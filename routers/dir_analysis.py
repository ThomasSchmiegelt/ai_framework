"""Router: Verzeichnis-Analyse (/api/dir, nur lokal)

Aus ``main.py`` ausgelagert (reines Backend-Refactoring, kein Verhaltenswechsel).
Geteilte Namen kommen ueber ``from core import *``.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import io
import json
import math
import os
import random
import re
import shutil
import sys
import tempfile
import time
import uuid
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import aiofiles
import httpx
from fastapi import (APIRouter, Body, Depends, File, Form, HTTPException, Query,
                     Request, UploadFile)
from fastapi.responses import (FileResponse, HTMLResponse, JSONResponse,
                               PlainTextResponse, RedirectResponse, Response,
                               StreamingResponse)
from pydantic import BaseModel

import db as _db
from tools import llm as _llm
from tools import transcribe as _transcribe

from core import *  # noqa: F401,F403  (geteilte Kernflaeche)
import core as _core  # noqa: F401

router = APIRouter()


# ── Verzeichnis-Analyse ─────────────────────────────────────────────────────────
# Liest einen lokalen Ordner (Server-Pfad), gibt einen Überblick + interessante
# Dateien zurück, analysiert einzelne Dateien vertieft und schreibt eine
# Index-/„init"-Datei (_KI_INDEX.md) zurück in den Ordner. Personenbezogene Daten
# in den DATEIINHALTEN werden anonymisiert (Datei-/Ordnernamen bleiben sichtbar).
# Da beliebige Server-Pfade gelesen/geschrieben werden, ist der Tab optional und
# per Default ausgeblendet — im Mehrnutzer-/Servermodus nicht freischalten.

_DIR_MAX_FILES = 2000           # Obergrenze gescannter Dateien
_DIR_MAX_DEPTH = 8              # maximale Verschachtelungstiefe
_DIR_SNIPPET_FILES = 40         # so viele Dateien liefern einen Inhalts-Snippet
_DIR_SNIPPET_CHARS = 800        # Snippet-Länge je Datei (vor Anonymisierung)
_DIR_FILE_MAX_BYTES = 5_000_000 # Dateien größer als das werden nicht eingelesen
_DIR_SKIP_DIRS = {".git", "node_modules", "__pycache__", "venv", ".venv",
                  ".idea", ".vscode", "dist", "build", ".cache"}
_DIR_TEXT_EXT = {".pdf", ".docx", ".doc", ".xlsx", ".xls", ".csv", ".txt",
                 ".md", ".py", ".js", ".json", ".yaml", ".yml", ".html", ".css"}


def _dir_resolve_base(path: str) -> Path:
    """Validiert einen Server-Pfad und gibt das aufgelöste Verzeichnis zurück."""
    raw = (path or "").strip()
    if not raw:
        raise HTTPException(status_code=400, detail="Pfad fehlt")
    try:
        base = Path(raw).expanduser().resolve()
    except Exception:
        raise HTTPException(status_code=400, detail="Ungültiger Pfad")
    if not base.exists():
        raise HTTPException(status_code=404, detail="Verzeichnis nicht gefunden")
    if not base.is_dir():
        raise HTTPException(status_code=400, detail="Pfad ist kein Verzeichnis")
    return base


def _dir_safe_child(base: Path, rel: str) -> Path:
    """Pfad-Traversal-Schutz: stellt sicher, dass rel innerhalb von base liegt
    (Muster wie /api/dossiers/load)."""
    target = (base / rel).resolve()
    try:
        target.relative_to(base)
    except ValueError:
        raise HTTPException(status_code=400, detail="Ungültiger Pfad")
    if not target.is_file():
        raise HTTPException(status_code=404, detail="Datei nicht gefunden")
    return target


def _dir_walk(base: Path):
    """Bounded rekursiver Walk. Liefert eine Liste von
    {rel, name, size, ext, is_dir} und überspringt versteckte/ausgeschlossene
    Verzeichnisse. Fängt PermissionError ab."""
    files: List[dict] = []
    count = 0

    def _recurse(d: Path, depth: int):
        nonlocal count
        if depth > _DIR_MAX_DEPTH or count >= _DIR_MAX_FILES:
            return
        try:
            entries = sorted(d.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
        except (PermissionError, OSError):
            return
        for p in entries:
            if count >= _DIR_MAX_FILES:
                return
            name = p.name
            if name.startswith("."):
                continue
            try:
                rel = str(p.relative_to(base))
            except ValueError:
                continue
            if p.is_dir():
                if name in _DIR_SKIP_DIRS:
                    continue
                files.append({"rel": rel, "name": name, "size": 0,
                              "ext": "", "is_dir": True})
                count += 1
                _recurse(p, depth + 1)
            elif p.is_file():
                try:
                    size = p.stat().st_size
                except OSError:
                    size = 0
                files.append({"rel": rel, "name": name, "size": size,
                              "ext": p.suffix.lower(), "is_dir": False})
                count += 1

    _recurse(base, 0)
    return files


async def _llm_ner_names(text: str, model: str, tok: Optional[dict] = None) -> List[str]:
    """Optionaler LLM-NER-Pass: liefert eine Liste zu schwärzender Personennamen.
    Best effort — bei jedem Fehler leere Liste.
    ``tok`` (optional): dict {"in","out"}, in das der Tokenverbrauch summiert wird."""
    snippet = (text or "")[:4000]
    if not snippet.strip():
        return []
    try:
        async with _model_session(model), httpx.AsyncClient(timeout=120) as client:
            resp = await _llm.chat(client,{
                "model": model,
                "think": False,
                "stream": False,
                "format": "json",
                "messages": [
                    {"role": "system", "content": (
                        "Extrahiere ALLE vorkommenden Personennamen (Vor- und/oder "
                        "Nachnamen echter Menschen) aus dem Text. Keine Firmen, Orte, "
                        "Produkte. Antworte NUR mit JSON: {\"namen\":[\"…\"]}")},
                    {"role": "user", "content": snippet},
                ],
            })
            resp.raise_for_status()
            _ner_j = resp.json()
            if tok is not None:
                _a, _b = _llm_tok(_ner_j)
                tok["in"] += _a
                tok["out"] += _b
            raw = _ner_j.get("message", {}).get("content", "")
    except Exception:
        return []
    raw = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL)
    m = re.search(r"\{[\s\S]*\}", raw)
    if not m:
        return []
    try:
        data = json.loads(m.group(0))
        names = data.get("namen") or data.get("names") or []
        return [str(n) for n in names if isinstance(n, (str,)) and n.strip()]
    except Exception:
        return []


async def _anonymize(text: str, mapping: dict, model: str, use_llm: bool,
                     tok: Optional[dict] = None):
    """Anonymisiert Text deterministisch (regex) und optional per LLM-NER."""
    from tools.anonymize import redact_pii, redact_names
    clean, mapping = redact_pii(text, mapping)
    if use_llm:
        names = await _llm_ner_names(text, model, tok=tok)
        if names:
            clean, mapping = redact_names(clean, names, mapping)
    return clean, mapping


@router.post("/api/dir/scan")
async def dir_scan(req: Request):
    """Erster Scan: Struktur + Inhalts-Snippets (anonymisiert) → KI-Überblick
    mit Markierung interessanter Dateien."""
    body = await req.json()
    base = _dir_resolve_base(body.get("path", ""))
    anonymize = True   # Anonymisierung von Personendaten ist PFLICHT (nicht abschaltbar)
    use_llm_ner = bool(body.get("llm_ner", False))   # zusätzlicher NER-Pass (langsamer)
    # Verzeichnis-Analyse läuft standardmäßig AUSSCHLIESSLICH lokal (Datenschutz).
    # Nur mit Profil-Schalter „API-Modelle für vertrauliche Auswertungen“ UND
    # explizit gewähltem Remote-Modell geht sie an einen externen Anbieter.
    model = await _analysis_model(body.get("model"))
    if not model:
        raise HTTPException(status_code=503, detail="Kein lokales LLM verfügbar – die Verzeichnis-Analyse benötigt ein lokales Modell (Ollama). Alternativ im Profil „API-Modelle für vertrauliche Auswertungen“ aktivieren und ein API-Modell wählen.")

    files = _dir_walk(base)
    text_files = [f for f in files
                  if not f["is_dir"] and f["ext"] in _DIR_TEXT_EXT
                  and 0 < f["size"] <= _DIR_FILE_MAX_BYTES]
    text_files.sort(key=lambda f: f["size"])

    _tok = {"in": 0, "out": 0}
    mapping: dict = {}
    snippets = []
    for f in text_files[:_DIR_SNIPPET_FILES]:
        try:
            txt = _extract_text(base / f["rel"])
        except Exception:
            continue
        if not txt or txt.startswith("[Lesefehler"):
            continue
        snip = txt[:_DIR_SNIPPET_CHARS]
        if anonymize:
            snip, mapping = await _anonymize(snip, mapping, model, use_llm_ner, tok=_tok)
        snippets.append({"file": f["rel"], "snippet": snip})

    # Kompakte Auflistung für das LLM
    n_dirs = sum(1 for f in files if f["is_dir"])
    n_files = sum(1 for f in files if not f["is_dir"])
    listing = "\n".join(
        f"- {f['rel']}" + ("/" if f["is_dir"] else f" ({f['size']} B)")
        for f in files[:300])
    snip_block = "\n\n".join(
        f"### {s['file']}\n{s['snippet']}" for s in snippets)

    summary, interesting = "", []
    try:
        async with _model_session(model), httpx.AsyncClient(timeout=180) as client:
            resp = await _llm.chat(client,{
                "model": model,
                "think": False,
                "stream": False,
                "format": "json",
                "messages": [
                    {"role": "system", "content": (
                        "Du analysierst ein Dateiverzeichnis. Gib einen knappen "
                        "Überblick (worum geht es, welche Arten von Dateien) und "
                        "markiere die interessantesten Dateien, die eine genauere "
                        "Analyse lohnen. Antworte NUR mit JSON: {\"summary\":\"…\","
                        "\"interesting\":[{\"file\":\"relativer/pfad\",\"reason\":\"…\"}]}")},
                    {"role": "user", "content": (
                        f"Verzeichnis: {base.name}\n"
                        f"{n_dirs} Unterordner, {n_files} Dateien.\n\n"
                        f"Struktur:\n{listing}\n\n"
                        f"Inhalts-Auszüge:\n{snip_block}")},
                ],
            })
            resp.raise_for_status()
            _ds_j = resp.json()
            _a, _b = _llm_tok(_ds_j)
            _tok["in"] += _a
            _tok["out"] += _b
            raw = _ds_j.get("message", {}).get("content", "")
        raw = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL)
        m = re.search(r"\{[\s\S]*\}", raw)
        if m:
            data = json.loads(m.group(0))
            summary = (data.get("summary") or "").strip()
            for it in (data.get("interesting") or []):
                if isinstance(it, dict) and it.get("file"):
                    interesting.append({"file": str(it["file"]),
                                        "reason": str(it.get("reason", ""))})
    except Exception as e:
        summary = f"(KI-Überblick nicht verfügbar: {e})"

    return {
        "base": str(base),
        "name": base.name,
        "n_dirs": n_dirs,
        "n_files": n_files,
        "truncated": len(files) >= _DIR_MAX_FILES,
        "tree": files,
        "summary": summary,
        "interesting": interesting,
        "redacted": len(mapping),
        "tokens": _tok,
    }


@router.post("/api/dir/analyze-file")
async def dir_analyze_file(req: Request):
    """Vertiefte Analyse einer einzelnen Datei (Volltext, anonymisiert) → Markdown."""
    body = await req.json()
    base = _dir_resolve_base(body.get("path", ""))
    file_rel = (body.get("file_rel") or "").strip()
    if not file_rel:
        raise HTTPException(status_code=400, detail="file_rel fehlt")
    target = _dir_safe_child(base, file_rel)
    use_llm_ner = bool(body.get("llm_ner", False))
    # Standard: lokal (Datenschutz); API-Modell nur über Profil-Schalter + explizite Wahl.
    model = await _analysis_model(body.get("model"))
    if not model:
        raise HTTPException(status_code=503, detail="Kein lokales LLM verfügbar – die Verzeichnis-Analyse benötigt ein lokales Modell (Ollama). Alternativ im Profil „API-Modelle für vertrauliche Auswertungen“ aktivieren und ein API-Modell wählen.")

    try:
        if target.stat().st_size > 25_000_000:
            raise HTTPException(status_code=400, detail="Datei zu groß für die Detailanalyse (> 25 MB)")
    except OSError:
        pass
    txt = _extract_text(target)
    if not txt or txt.startswith("[Lesefehler"):
        raise HTTPException(status_code=400, detail=f"Text nicht lesbar: {txt}")
    # Anonymisierung von Personendaten ist PFLICHT (nicht abschaltbar)
    _tok = {"in": 0, "out": 0}
    mapping: dict = {}
    txt, mapping = await _anonymize(txt[:16000], mapping, model, use_llm_ner, tok=_tok)

    analysis = ""
    try:
        async with _model_session(model), httpx.AsyncClient(timeout=240) as client:
            resp = await _llm.chat(client,{
                "model": model,
                "think": False,
                "stream": False,
                "messages": [
                    {"role": "system", "content": (
                        "Analysiere die folgende Datei und fasse präzise auf Deutsch "
                        "zusammen: Worum geht es, wichtigste Inhalte/Aussagen, "
                        "Auffälligkeiten. Antworte in Markdown. Erfinde nichts.")},
                    {"role": "user", "content": f"Datei: {file_rel}\n\n{txt}"},
                ],
            })
            resp.raise_for_status()
            _da_j = resp.json()
            _a, _b = _llm_tok(_da_j)
            _tok["in"] += _a
            _tok["out"] += _b
            analysis = _da_j.get("message", {}).get("content", "").strip()
            analysis = re.sub(r"<think>.*?</think>", "", analysis, flags=re.DOTALL).strip()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Analyse fehlgeschlagen: {e}")

    return {"file": file_rel, "analysis": analysis, "redacted": len(mapping), "tokens": _tok}


@router.post("/api/dir/finalize")
async def dir_finalize(req: Request):
    """Schreibt die Index-/„init"-Datei (_KI_INDEX.md) in den Ordner und legt
    optional eine Wissensdatenbank ('Verzeichnis: …') aus dem Inhalt an."""
    body = await req.json()
    base = _dir_resolve_base(body.get("path", ""))
    content = (body.get("content") or "").strip()
    if not content:
        raise HTTPException(status_code=400, detail="Inhalt fehlt")
    to_rag = bool(body.get("to_rag", False))
    filename = (body.get("filename") or "_KI_INDEX.md").strip() or "_KI_INDEX.md"
    if "/" in filename or "\\" in filename or filename.startswith("."):
        filename = "_KI_INDEX.md"

    header = (f"# KI-Verzeichnisanalyse: {base.name}\n\n"
              f"▶ Von KI generiert · {time.strftime('%Y-%m-%d %H:%M')} · "
              f"Personendaten in Inhalten anonymisiert.\n\n")
    full = header + content
    target = base / filename
    try:
        target.write_text(full, encoding="utf-8")
    except (PermissionError, OSError) as e:
        raise HTTPException(status_code=500, detail=f"Schreiben fehlgeschlagen: {e}")

    rag_id = None
    if to_rag:
        from tools.rag import ingest_file
        coll = {
            "id": f"rag_{uuid.uuid4().hex[:12]}",
            "name": f"Verzeichnis: {base.name}",
            "embed_model": EMBED_MODEL,
            "tier": "ausgewogen",
            "chunk_size": 1000, "chunk_overlap": 150, "top_k": 6,
            "embed_gpu": False, "clean": True, "char_limit": 6000,
            "strictness": "ausgewogen", "created_at": time.time(),
        }
        await _db.rag_create_collection(coll)
        try:
            await ingest_file(coll, full, f"{base.name}.md", f"doc_{uuid.uuid4().hex[:12]}")
        except Exception as e:
            await _db.rag_delete_collection(coll["id"])
            raise HTTPException(
                status_code=500,
                detail=f"Einbetten fehlgeschlagen — ist '{EMBED_MODEL}' gepullt? ({e})")
        rag_id = coll["id"]

    return {"ok": True, "path": str(target), "rag_collection_id": rag_id}

