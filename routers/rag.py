"""Router: RAG-API (Wissenssammlungen, /api/rag)

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


# ── Bild-aware RAG (Vision-Beschreibung beim Ingest) ──────────────────────────
# Bilder werden gespeichert + per lokalem Vision-Modell beschrieben; die Beschreibung
# wird wie Text indiziert (auffindbar), das Original bleibt über die Dokument-
# Verknüpfung (rag_documents.image_rel) anzeigbar. Muster: routers/pst.py Stufe 2.
_IMG_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"}

_RAG_IMAGE_DESCRIBE_SYSTEM = (
    "Du beschreibst ein Bild für eine durchsuchbare Wissensdatenbank. Erfasse den Inhalt "
    "faktisch und möglichst vollständig, damit das Bild später über eine Textsuche gefunden "
    "wird: sichtbare Objekte, Personen (nur sachlich), Handlungen, Szene/Umgebung, Farben nur "
    "wenn bedeutsam. Gib ALLEN sichtbaren Text/Beschriftungen wortgetreu wieder (Schilder, "
    "Diagramm-Achsen, Tabellen, Logos, Zahlen). Bei Diagrammen/Charts/Screenshots: Aussage, "
    "Achsen, Werte und Trends benennen. Antworte in sachlicher deutscher Prosa, ohne Vorspann, "
    "ohne Anrede, ohne Markdown."
)


async def _describe_image(path: Path, model: str, caption: str = "") -> tuple[str, dict]:
    """Beschreibt ein Bild per lokalem Vision-Modell für die RAG-Indizierung.

    Muster wie ``routers/pst.py`` (Stufe 2): base64-Bild an ein multimodales Modell.
    ``caption`` (optionaler Nutzertext) wird dem durchsuchbaren Text vorangestellt, damit
    eigenes Fachvokabular mit-embeddet wird. Rückgabe: (Beschreibungstext, {"in","out"})."""
    tok = {"in": 0, "out": 0}
    b64 = base64.b64encode(path.read_bytes()).decode()
    cap = (caption or "").strip()
    usr = "Beschreibe dieses Bild vollständig für die Wissensdatenbank."
    if cap:
        usr += f"\n\nZusätzlicher Kontext vom Nutzer (mit einbeziehen): {cap}"
    msg = {"role": "user", "content": usr, "images": [b64]}
    async with _model_session(model), httpx.AsyncClient(timeout=240) as client:
        resp = await _llm.chat(client, {
            "model": model, "think": False, "stream": False,
            "messages": [{"role": "system", "content": _RAG_IMAGE_DESCRIBE_SYSTEM}, msg],
            "options": {"num_ctx": _profile_num_ctx()}, "keep_alive": KEEP_ALIVE,
        })
        resp.raise_for_status()
        j = resp.json()
    a, b = _llm_tok(j)
    tok["in"] += a
    tok["out"] += b
    desc = (j.get("message", {}).get("content", "") or "").strip()
    desc = re.sub(r"<think>.*?</think>", "", desc, flags=re.DOTALL).strip()
    if cap:
        desc = f"{cap}\n\n{desc}" if desc else cap
    return desc, tok


# Vision-Modellwahl für die Bildbeschreibung = geteilter Kern ``_vision_model`` (core.py).
async def _pick_vision_model() -> Optional[str]:
    return await _vision_model(None)


# ── RAG-API (Wissenssammlungen) ───────────────────────────────────────────────

class RagCollectionCreate(BaseModel):
    name: str
    tier: str = "6gb"          # frei wählbares Label (z. B. Regler-Stufe)
    chunk_size: Optional[int] = None
    chunk_overlap: Optional[int] = None
    top_k: Optional[int] = None
    char_limit: Optional[int] = None
    strictness: str = "ausgewogen"   # kreativ | ausgewogen | korrekt
    clean: bool = True
    clean_level: str = "standard"    # standard | strikt
    embed_model: Optional[str] = None  # None = lokales Standardmodell (config.json)


@router.get("/api/rag/tiers")
async def rag_tiers():
    from tools.rag import TIERS, DEFAULT_TIER
    return {"tiers": TIERS, "default": DEFAULT_TIER, "embed_model": EMBED_MODEL}


@router.get("/api/rag/embed-models")
async def rag_embed_models():
    """Wählbare Embeddingmodelle für neue Sammlungen: lokal installierte
    Ollama-Modelle plus alle konfigurierten API-Modelle.

    Die Zuordnung gilt dauerhaft pro Sammlung — Vektoren unterschiedlicher
    Modelle sind nicht vergleichbar (andere Dimension/Semantik). Ein Wechsel
    erfordert Neuindizierung; das Frontend weist darauf hin."""
    local = [{"name": m, "remote": False, "provider": "Ollama (lokal)"}
             for m in await _installed_local_models()]
    try:
        remote = await _llm.list_remote_models()
    except Exception:
        remote = []
    return {"default": EMBED_MODEL, "local": local, "remote": remote}


@router.get("/api/rag/collections")
async def rag_collections():
    return await _db.rag_list_collections()


@router.post("/api/rag/collections")
async def rag_create_collection(body: RagCollectionCreate):
    from tools.rag import tier_config
    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Name fehlt")
    tc = tier_config(body.tier)
    strictness = body.strictness if body.strictness in ("kreativ", "ausgewogen", "korrekt") else "ausgewogen"
    clean_level = body.clean_level if body.clean_level in ("standard", "strikt") else "standard"
    # Embeddingmodell: lokal (Ollama) oder extern („anbieter::modell"). Die Wahl gilt
    # dauerhaft für diese Sammlung — Vektoren verschiedener Modelle sind nicht
    # vergleichbar, ein Wechsel erfordert Neuindizierung.
    embed_model = (body.embed_model or "").strip() or EMBED_MODEL
    if _llm.is_remote(embed_model):
        provider, _real = _llm.resolve(embed_model)
        if provider is None:
            raise HTTPException(status_code=400,
                                detail=f"Unbekannter API-Anbieter für Embeddingmodell '{embed_model}'.")
    coll = {
        "id": f"rag_{uuid.uuid4().hex[:12]}",
        "name": name,
        "embed_model": embed_model,
        "tier": (body.tier or "regler").strip()[:24],   # freies Anzeige-Label (Regler-Stufe)
        "chunk_size": int(body.chunk_size or tc["chunk_size"]),
        "chunk_overlap": int(body.chunk_overlap if body.chunk_overlap is not None else tc["chunk_overlap"]),
        "top_k": int(body.top_k or tc["top_k"]),
        "embed_gpu": False,   # auf kleinen Karten immer CPU (verdrängt das Chat-Modell nicht)
        "clean": bool(body.clean),
        "clean_level": clean_level,
        "char_limit": int(body.char_limit or tc["char_limit"]),
        "strictness": strictness,
        "created_at": time.time(),
    }
    await _db.rag_create_collection(coll)
    return coll


@router.delete("/api/rag/collections/{cid}")
async def rag_delete_collection(cid: str):
    await _db.rag_delete_collection(cid)
    # Bild-aware RAG: den Bildordner der Sammlung mit entfernen (DB-Zeilen kaskadieren,
    # die Dateien auf der Platte nicht).
    try:
        img_dir = (RAG_IMAGES_DIR / _safe_relpath(cid)).resolve()
        if RAG_IMAGES_DIR.resolve() in img_dir.parents and img_dir.is_dir():
            shutil.rmtree(img_dir, ignore_errors=True)
    except Exception:
        pass
    return {"ok": True}


@router.get("/api/rag/collections/{cid}/documents")
async def rag_documents(cid: str):
    return await _db.rag_list_documents(cid)


@router.post("/api/rag/collections/{cid}/documents")
async def rag_add_document(cid: str, file: UploadFile = File(...), caption: str = Form("")):
    from tools.rag import ingest_file
    coll = await _db.rag_get_collection(cid)
    if not coll:
        raise HTTPException(status_code=404, detail="Sammlung nicht gefunden")
    doc_id = f"doc_{uuid.uuid4().hex[:12]}"
    raw = await file.read()
    ext = Path(file.filename or "").suffix.lower()

    # Bild-aware RAG: Bild speichern + lokal per Vision-Modell beschreiben; die
    # Beschreibung wird indiziert (auffindbar), das Original bleibt anzeigbar.
    if ext in _IMG_EXTS:
        model = await _pick_vision_model()
        if not model:
            raise HTTPException(status_code=503, detail="Bild-Import benötigt ein lokales multimodales Modell (z. B. „llava“ oder „qwen2.5-vl“ in Ollama). Bitte ein solches Modell installieren/laden.")
        rel = f"{_safe_relpath(cid)}/{doc_id}{ext}"
        dest = RAG_IMAGES_DIR / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        async with aiofiles.open(dest, "wb") as fh:
            await fh.write(raw)
        try:
            desc, _tok = await _describe_image(dest, model, caption)
        except Exception as e:
            try:
                dest.unlink()
            except Exception:
                pass
            raise HTTPException(status_code=500, detail=f"Bildbeschreibung fehlgeschlagen: {e}")
        if not desc:
            desc = f"[Bild: {file.filename}]"
        try:
            n = await ingest_file(coll, desc, file.filename, doc_id, image_rel=rel)
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))
        return {"ok": True, "filename": file.filename, "n_chunks": n, "kind": "image", "tokens": _tok}

    # Textdokument: temporär ablegen, Text extrahieren
    tmp = UPLOADS_DIR / f"rag_{uuid.uuid4().hex}_{file.filename}"
    async with aiofiles.open(tmp, "wb") as fh:
        await fh.write(raw)
    try:
        text = _extract_text(tmp)
    finally:
        try:
            tmp.unlink()
        except Exception:
            pass
    if not text or text.startswith("[Lesefehler"):
        raise HTTPException(status_code=400, detail=f"Text konnte nicht extrahiert werden: {text}")
    try:
        n = await ingest_file(coll, text, file.filename, doc_id)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    return {"ok": True, "filename": file.filename, "n_chunks": n}


async def _optimize_chunk_for_rag(chunk: str, model: str, tok: Optional[dict] = None) -> str:
    """Ruft das LLM auf, um einen Textabschnitt RAG-konform aufzubereiten.
    ``tok`` (optional): dict {"in","out"}, in das der Tokenverbrauch summiert wird."""
    try:
        async with httpx.AsyncClient(timeout=120) as client:
            resp = await _llm.chat(client,{
                "model": model,
                "think": False,
                "stream": False,
                "messages": [
                    {"role": "system", "content": _RAG_OPTIMIZE_SYSTEM},
                    {"role": "user", "content": chunk},
                ],
            })
            resp.raise_for_status()
            _ro_j = resp.json()
            if tok is not None:
                _a, _b = _llm_tok(_ro_j)
                tok["in"] += _a
                tok["out"] += _b
            content = _ro_j.get("message", {}).get("content", "").strip()
            # Thinking-Tags und Code-Fences entfernen
            content = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL).strip()
            content = re.sub(r"^```[^\n]*\n?|```$", "", content, flags=re.MULTILINE).strip()
            return content if content else chunk
    except Exception:
        return chunk  # Fallback: Originaltext


@router.post("/api/rag/collections/{cid}/documents/optimized")
async def rag_add_document_optimized(cid: str, file: UploadFile = File(...)):
    """Lädt ein Dokument hoch, optimiert es per LLM für RAG und ingestiert es.
    Gibt SSE-Fortschrittsereignisse zurück."""
    from tools.rag import ingest_file

    async def _stream():
        coll = await _db.rag_get_collection(cid)
        if not coll:
            yield _sse({"type": "error", "message": "Sammlung nicht gefunden"})
            return

        # Datei temporär speichern und Text extrahieren
        tmp = UPLOADS_DIR / f"rag_opt_{uuid.uuid4().hex}_{file.filename}"
        async with aiofiles.open(tmp, "wb") as fh:
            await fh.write(await file.read())
        try:
            raw_text = _extract_text(tmp)
        finally:
            try:
                tmp.unlink()
            except Exception:
                pass

        if not raw_text or raw_text.startswith("[Lesefehler"):
            yield _sse({"type": "error", "message": f"Text konnte nicht extrahiert werden: {raw_text}"})
            return

        # Text in ~2 500-Zeichen-Abschnitte aufteilen (für LLM-Verarbeitung)
        CHUNK = 2500
        STEP  = 2300
        chunks = [raw_text[i:i + CHUNK] for i in range(0, len(raw_text), STEP)]
        total = len(chunks)
        yield _sse({"type": "progress", "step": f"Extrahiert: {len(raw_text):,} Zeichen, {total} Abschnitte", "pct": 5})

        model = _model_for("general")
        optimized_parts: list[str] = []
        _tok = {"in": 0, "out": 0}
        async with _model_session(model):
            for idx, chunk in enumerate(chunks):
                pct = 5 + int((idx / total) * 85)
                yield _sse({"type": "progress",
                             "step": f"Abschnitt {idx + 1}/{total} wird optimiert…",
                             "pct": pct})
                opt = await _optimize_chunk_for_rag(chunk, model, tok=_tok)
                optimized_parts.append(opt)

        optimized_text = "\n\n".join(optimized_parts)
        yield _sse({"type": "progress", "step": "Einbetten und speichern…", "pct": 92})

        try:
            n = await ingest_file(coll, optimized_text, file.filename, f"doc_{uuid.uuid4().hex[:12]}")
        except Exception as e:
            yield _sse({"type": "error", "message": str(e)})
            return

        yield _sse({"type": "done", "filename": file.filename, "n_chunks": n, "tokens": _tok})

    return StreamingResponse(
        _stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.delete("/api/rag/documents/{did}")
async def rag_delete_document(did: str):
    # Bild-aware RAG: Originalbild (falls vorhanden) vor dem DB-Löschen mit entfernen.
    info = await _db.rag_document_image(did)
    await _db.rag_delete_document(did)
    if info and info.get("image_rel"):
        try:
            fp = (RAG_IMAGES_DIR / _safe_relpath(info["image_rel"])).resolve()
            if RAG_IMAGES_DIR.resolve() in fp.parents and fp.is_file():
                fp.unlink()
        except Exception:
            pass
    return {"ok": True}


def _stitch_chunks(chunks: list) -> str:
    """Setzt überlappende Chunks wieder zu einem Fließtext zusammen, indem die
    Überlappung (gemeinsamer Übergang) zwischen aufeinanderfolgenden Chunks
    entfernt wird."""
    if not chunks:
        return ""
    out = chunks[0]
    for nxt in chunks[1:]:
        k = min(len(out), len(nxt), 1200)
        ov = 0
        for j in range(k, 20, -1):           # größte Überlappung ≥ 20 Zeichen
            if out[-j:] == nxt[:j]:
                ov = j
                break
        out += ("" if ov else "\n\n") + nxt[ov:]
    return out


@router.get("/api/rag/documents/{did}/export")
async def rag_export_document(did: str, format: str = "md"):
    """Exportiert den (aus den Chunks rekonstruierten) Inhalt eines Dokuments
    als Markdown oder TXT zum Download."""
    doc = await _db.rag_document_chunks(did)
    if not doc:
        raise HTTPException(status_code=404, detail="Dokument nicht gefunden")
    text = _stitch_chunks(doc["chunks"])
    ext = "md" if format == "md" else "txt"
    media = "text/markdown" if ext == "md" else "text/plain"
    base = re.sub(r"[^\w.\-]+", "_", Path(doc["filename"]).stem) or "dokument"
    headers = {"Content-Disposition": f'attachment; filename="{base}.{ext}"'}
    return Response(content=text, media_type=f"{media}; charset=utf-8", headers=headers)


@router.get("/api/rag/documents/{did}/image")
async def rag_document_image(did: str):
    """Bild-aware RAG: liefert das Originalbild eines Bild-Dokuments (für Thumbnails im
    Chat-Quellen-Panel). 404, wenn das Dokument kein Bild trägt oder die Datei fehlt."""
    info = await _db.rag_document_image(did)
    if not info:
        raise HTTPException(status_code=404, detail="Kein Bild zu diesem Dokument")
    rel = _safe_relpath(info["image_rel"])
    fp = (RAG_IMAGES_DIR / rel).resolve()
    try:
        if RAG_IMAGES_DIR.resolve() not in fp.parents or not fp.is_file():
            raise HTTPException(status_code=404, detail="Bilddatei fehlt")
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=404, detail="Bilddatei fehlt")
    return FileResponse(str(fp), filename=info["filename"])


@router.post("/api/rag/collections/{cid}/from-conversation")
async def rag_from_conversation(cid: str, req: Request):
    """Übernimmt ein gespeichertes Gespräch als Dokument in eine RAG-Sammlung
    (optional wird das Original-Gespräch danach gelöscht = „verschieben")."""
    from tools.rag import ingest_file
    body = await req.json()
    conv_id = body.get("conversation_id")
    delete_after = bool(body.get("delete_after"))
    coll = await _db.rag_get_collection(cid)
    if not coll:
        raise HTTPException(status_code=404, detail="Sammlung nicht gefunden")
    conv = await _db.get_conversation(conv_id)
    if not conv:
        raise HTTPException(status_code=404, detail="Gespräch nicht gefunden")
    lines = []
    for m in conv["messages"]:
        if m["role"] == "system":
            continue
        label = "Benutzer" if m["role"] == "user" else "Assistent"
        lines.append(f"{label}: {str(m.get('content', '')).strip()}")
    text = "\n\n".join(l for l in lines if l.strip())
    if not text:
        raise HTTPException(status_code=400, detail="Gespräch enthält keinen Text")
    title = conv.get("title") or conv_id
    try:
        n = await ingest_file(coll, text, f"Chat: {title}", f"doc_{uuid.uuid4().hex[:12]}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    if delete_after:
        await _db.delete_conversation(conv_id)
    return {"ok": True, "n_chunks": n, "deleted": delete_after}


@router.post("/api/rag/collections/{cid}/from-text")
async def rag_from_text(cid: str, req: Request):
    """Übernimmt beliebigen Text (z. B. Recherchebericht, Matrix-Ergebnis) als
    Dokument in eine Wissensdatenbank."""
    from tools.rag import ingest_file
    body = await req.json()
    text = str(body.get("text", "")).strip()
    title = (str(body.get("title", "")).strip() or "Notiz")[:120]
    coll = await _db.rag_get_collection(cid)
    if not coll:
        raise HTTPException(status_code=404, detail="Wissensdatenbank nicht gefunden")
    if not text:
        raise HTTPException(status_code=400, detail="Kein Text übergeben")
    try:
        n = await ingest_file(coll, text, title, f"doc_{uuid.uuid4().hex[:12]}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    return {"ok": True, "n_chunks": n}


# In den RAG geeignete Dateiendungen (Textextraktion via tools/files.py).
# Bild-Endungen (_IMG_EXTS) werden zusätzlich aufgenommen und – falls ein lokales
# Vision-Modell verfügbar ist – per Beschreibung indiziert (Bild-aware RAG).
_RAG_FOLDER_EXTS = {
    ".pdf", ".docx", ".doc", ".xlsx", ".xls", ".csv", ".txt", ".md", ".rtf",
    ".py", ".js", ".json", ".yaml", ".yml", ".html", ".htm", ".css",
} | _IMG_EXTS


@router.post("/api/rag/collections/{cid}/folder")
async def rag_add_folder(cid: str, req: Request):
    """Baut aus allen Textdateien eines Server-seitigen Ordners eine Wissensdatenbank
    auf: Datei für Datei extrahieren → (in ingest_file) bereinigen, chunken, einbetten.
    Streamt SSE-Fortschritt. Hinweis: liest serverseitige Pfade — im Mehrbenutzer-/
    Server-Modus bewusst sparsam einsetzen."""
    coll = await _db.rag_get_collection(cid)
    if not coll:
        raise HTTPException(status_code=404, detail="Wissensdatenbank nicht gefunden")
    body = await req.json()
    raw_path = str(body.get("path", "")).strip()
    recursive = bool(body.get("recursive", True))
    if not raw_path:
        raise HTTPException(status_code=400, detail="Kein Ordnerpfad angegeben")
    folder = Path(raw_path).expanduser()
    if not folder.is_dir():
        raise HTTPException(status_code=400, detail=f"Ordner nicht gefunden: {folder}")

    it = folder.rglob("*") if recursive else folder.glob("*")
    files = sorted(p for p in it if p.is_file() and p.suffix.lower() in _RAG_FOLDER_EXTS)

    async def gen():
        from tools.rag import ingest_file
        total = len(files)
        yield _sse({"type": "folder_start", "total": total, "folder": str(folder)})
        n_chunks = 0
        n_ok = 0
        errors: list = []
        # Vision-Modell für Bilder einmal auflösen (lokal, Geheim-Modus-tauglich, bevorzugt
        # multimodal); ohne ein solches Modell werden Bilder übersprungen (Text läuft normal).
        img_model = await _pick_vision_model()
        for i, fp in enumerate(files):
            yield _sse({"type": "progress", "step": fp.name, "index": i,
                        "total": total, "pct": int(i / total * 100) if total else 100})
            try:
                ext = fp.suffix.lower()
                if ext in _IMG_EXTS:
                    if not img_model:
                        errors.append(f"{fp.name}: kein lokales Vision-Modell")
                        continue
                    doc_id = f"doc_{uuid.uuid4().hex[:12]}"
                    rel = f"{_safe_relpath(coll['id'])}/{doc_id}{ext}"
                    dest = RAG_IMAGES_DIR / rel
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(fp, dest)
                    desc, _ = await _describe_image(dest, img_model, "")
                    if not desc:
                        desc = f"[Bild: {fp.name}]"
                    c = await ingest_file(coll, desc, fp.name, doc_id, image_rel=rel)
                    n_chunks += c
                    n_ok += 1
                    continue
                text = await asyncio.to_thread(_extract_text, fp)
                if not text or text.startswith("[Lesefehler") or text.startswith("[Kann Datei"):
                    errors.append(f"{fp.name}: kein Text")
                    continue
                c = await ingest_file(coll, text, fp.name, f"doc_{uuid.uuid4().hex[:12]}")
                n_chunks += c
                n_ok += 1
            except Exception as e:
                errors.append(f"{fp.name}: {e}")
        yield _sse({"type": "done", "n_files": n_ok, "n_chunks": n_chunks, "errors": errors[:20]})

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})



