"""Router: Setup-Endpunkte (Erststart-Konfiguration, /api/setup)

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


# ── Setup-Endpunkte (Erststart-Konfiguration) ─────────────────────────────────

@router.get("/api/setup/embed-check")
async def setup_embed_check():
    """Prüft ob das konfigurierte Embedding-Modell in Ollama vorhanden ist."""
    embed_model = _CONFIG.get("embed_model", "nomic-embed-text")
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(f"{OLLAMA_BASE}/api/tags")
            names = {m["name"] for m in resp.json().get("models", [])}
            return {"ok": embed_model in names, "embed_model": embed_model}
    except Exception:
        return {"ok": False, "embed_model": embed_model}


@router.get("/api/platform")
async def get_platform():
    """Liefert die Betriebssystem-Plattform für die Onboarding-Maske."""
    import sys
    return {"platform": sys.platform}  # "linux", "win32", "darwin"


@router.post("/api/refine-document")
async def refine_document(req: Request):
    """Multi-Agenten-Verfeinerungsschleife: verbessert ein Dokument iterativ
    durch mehrere Agenten bis die Änderungsrate unter den Schwellwert fällt."""
    import difflib
    body = await req.json()
    text = (body.get("text") or "").strip()
    agents = body.get("agents") or []   # [{id, system_prompt, name}]
    threshold = float(body.get("threshold") or 2.0)   # % Änderungen für Stop
    max_iter = min(int(body.get("max_iterations") or 10), 50)
    model = _pick_model(body.get("model"))
    if not text:
        raise HTTPException(400, "Kein Dokumenttext")
    if not agents:
        raise HTTPException(400, "Mindestens ein Agent erforderlich")

    def _word_change_pct(old: str, new: str) -> float:
        a = old.split(); b = new.split()
        if not a and not b:
            return 0.0
        ratio = difflib.SequenceMatcher(None, a, b).ratio()
        return round((1.0 - ratio) * 100, 1)

    _tok = {"in": 0, "out": 0}

    async def _refine_once(current: str, agent: dict) -> str:
        sys_prompt = agent.get("system_prompt") or (
            "Verbessere den folgenden Text: korrigiere Fehler, verbessere Klarheit und Struktur. "
            "Gib NUR den verbesserten Text zurück, ohne Kommentare."
        )
        # Große Dokumente: in ~2000-Zeichen-Abschnitte aufteilen
        MAX = 4000
        if len(current) <= MAX:
            chunks = [current]
        else:
            paragraphs = current.split("\n\n")
            chunks, buf = [], ""
            for p in paragraphs:
                if len(buf) + len(p) > MAX and buf:
                    chunks.append(buf.strip())
                    buf = p
                else:
                    buf += ("\n\n" if buf else "") + p
            if buf:
                chunks.append(buf.strip())

        refined_parts = []
        for chunk in chunks:
            try:
                async with httpx.AsyncClient(timeout=180) as client:
                    resp = await _llm.chat(client,{
                        "model": model, "think": False, "stream": False,
                        "messages": [{"role": "system", "content": sys_prompt},
                                     {"role": "user", "content": chunk}],
                    })
                    resp.raise_for_status()
                    _rf_j = resp.json()
                    _a, _b = _llm_tok(_rf_j)
                    _tok["in"] += _a
                    _tok["out"] += _b
                    content = _rf_j.get("message", {}).get("content", "").strip()
                    content = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL).strip()
                    refined_parts.append(content if content else chunk)
            except Exception:
                refined_parts.append(chunk)
        return "\n\n".join(refined_parts)

    async def _stream():
        current = text
        async with _model_session(model):
            for iteration in range(1, max_iter + 1):
                agent = agents[(iteration - 1) % len(agents)]
                yield _sse({"type": "iteration_start", "n": iteration,
                            "agent": agent.get("name", f"Agent {iteration}")})
                try:
                    new_text = await _refine_once(current, agent)
                except Exception as e:
                    yield _sse({"type": "error", "message": str(e)})
                    return
                change_pct = _word_change_pct(current, new_text)
                current = new_text
                yield _sse({"type": "iteration_done", "n": iteration,
                            "agent": agent.get("name", f"Agent {iteration}"),
                            "change_pct": change_pct, "text": current})
                if change_pct < threshold:
                    yield _sse({"type": "converged", "n": iteration, "change_pct": change_pct,
                                "message": f"Konvergiert nach {iteration} Iterationen ({change_pct} % < {threshold} % Schwelle)"})
                    break
            yield _sse({"type": "done", "text": current, "tokens": _tok})

    return StreamingResponse(
        _stream(), media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/api/setup/config")
async def get_setup_config():
    """Gibt die aktuell aktiven Konfigurationswerte zurück (für Onboarding/Einstellungen)."""
    return {
        "default_model": _CONFIG.get("default_model", DEFAULT_MODEL),
        "data_dir": _CONFIG.get("data_dir", "data"),
    }


@router.put("/api/rag/collections/{cid}/server-path")
async def rag_set_server_path_endpoint(cid: str, req: Request):
    """Setzt oder löscht den Serverpfad einer RAG-Sammlung."""
    body = await req.json()
    sp = (body.get("server_path") or "").strip() or None
    coll = await _db.rag_get_collection(cid)
    if not coll:
        raise HTTPException(404, "Sammlung nicht gefunden")
    await _db.rag_set_server_path(cid, sp)
    return {"ok": True, "server_path": sp}


@router.post("/api/rag/collections/{cid}/publish")
async def rag_publish_collection(cid: str):
    """Exportiert eine RAG-Sammlung als .ragpack.json in den gespeicherten Serverpfad."""
    import base64
    coll = await _db.rag_get_collection(cid)
    if not coll:
        raise HTTPException(404, "Sammlung nicht gefunden")
    sp = (coll.get("server_path") or "").strip()
    if not sp:
        raise HTTPException(400, "Kein Serverpfad für diese Sammlung gesetzt")
    server_dir = Path(sp)
    if not server_dir.exists():
        raise HTTPException(400, f"Verzeichnis existiert nicht: {sp}")
    if not server_dir.is_dir():
        raise HTTPException(400, f"Pfad ist kein Verzeichnis: {sp}")
    data = await _db.rag_export_collection(cid)
    if not data:
        raise HTTPException(500, "Export fehlgeschlagen")
    # Embeddings base64-kodieren für JSON-Serialisierung
    for d in data["documents"]:
        for ch in d.get("chunks", []):
            ch["embedding"] = base64.b64encode(ch["embedding"]).decode()
    slug = re.sub(r"[^\w\-]+", "_", coll["name"])[:40] or cid[:8]
    out_file = server_dir / f"{slug}.ragpack.json"
    out_file.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"ok": True, "file": str(out_file), "n_chunks": sum(len(d.get("chunks", [])) for d in data["documents"])}


@router.get("/api/rag/server-packs")
async def rag_server_packs(dir: str = ""):
    """Listet alle .ragpack.json-Dateien in einem Verzeichnis auf."""
    p = Path(dir.strip())
    if not p.exists() or not p.is_dir():
        raise HTTPException(400, f"Verzeichnis nicht gefunden: {dir}")
    packs = []
    for f in sorted(p.glob("*.ragpack.json")):
        try:
            meta = json.loads(f.read_text(encoding="utf-8"))
            c = meta.get("collection", {})
            packs.append({
                "file": str(f),
                "name": c.get("name", f.stem),
                "n_docs": len(meta.get("documents", [])),
                "n_chunks": sum(len(d.get("chunks", [])) for d in meta.get("documents", [])),
                "created_at": c.get("created_at"),
            })
        except Exception:
            pass
    return packs


@router.post("/api/rag/collections/clone")
async def rag_clone_collection(req: Request):
    """Klont eine .ragpack.json-Datei in die lokale Datenbank (neue ID, kein server_path)."""
    import base64
    body = await req.json()
    file_path = (body.get("file_path") or "").strip()
    if not file_path:
        raise HTTPException(400, "file_path fehlt")
    p = Path(file_path)
    if not p.exists() or not p.is_file():
        raise HTTPException(400, f"Datei nicht gefunden: {file_path}")
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:
        raise HTTPException(400, f"Datei konnte nicht gelesen werden: {e}")
    coll = data.get("collection", {})
    docs = data.get("documents", [])
    # Neue IDs generieren um Kollisionen zu vermeiden
    new_cid = uuid.uuid4().hex
    coll = dict(coll)
    coll["id"] = new_cid
    coll.pop("server_path", None)  # Klon hat keinen Serverpfad
    id_map = {}
    new_docs = []
    for d in docs:
        new_did = f"doc_{uuid.uuid4().hex[:12]}"
        id_map[d["id"]] = new_did
        nd = dict(d)
        nd["id"] = new_did
        for ch in nd.get("chunks", []):
            if isinstance(ch.get("embedding"), str):
                ch["embedding"] = base64.b64decode(ch["embedding"])
        new_docs.append(nd)
    await _db.rag_import_collection(coll, new_docs)
    return {"ok": True, "id": new_cid, "name": coll.get("name"), "n_docs": len(new_docs)}


@router.post("/api/setup/config")
async def setup_config(req: Request):
    """Schreibt das gewählte Standard-Modell und optionalen Datenpfad in config.json."""
    global _CONFIG, DEFAULT_MODEL
    body = await req.json()
    model = _pick_model(body.get("default_model", ""))
    _CONFIG["default_model"] = model
    DEFAULT_MODEL = model
    if "data_dir" in body:
        raw = (body["data_dir"] or "").strip() or "data"
        _CONFIG["data_dir"] = raw
    try:
        _CONFIG_FILE.write_text(
            json.dumps({k: v for k, v in _CONFIG.items()}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception as e:
        raise HTTPException(500, f"config.json konnte nicht gespeichert werden: {e}")
    return {"ok": True, "default_model": model, "data_dir": _CONFIG.get("data_dir", "data")}


@router.post("/api/setup/systemd")
async def setup_systemd():
    """Richtet einen User-Systemd-Service ein (nur Linux, kein sudo nötig)."""
    import sys
    import subprocess
    if sys.platform != "linux":
        raise HTTPException(400, "Systemd ist nur unter Linux verfügbar.")

    app_dir = APP_DIR   # Repo-/Programm-Wurzel (aus core; NICHT Path(__file__) im routers/-Unterordner)
    uvicorn_bin = app_dir / "venv" / "bin" / "uvicorn"
    host = _CONFIG.get("host", "127.0.0.1")
    port = _CONFIG.get("port", 8780)

    service = f"""[Unit]
Description=AI Framework Thomas
After=network.target

[Service]
Type=simple
WorkingDirectory={app_dir}
ExecStart={uvicorn_bin} main:app --host {host} --port {port}
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
"""
    systemd_dir = Path.home() / ".config" / "systemd" / "user"
    systemd_dir.mkdir(parents=True, exist_ok=True)
    service_file = systemd_dir / "ai_framework_thomas.service"
    service_file.write_text(service, encoding="utf-8")

    errors = []
    for cmd in [
        ["systemctl", "--user", "daemon-reload"],
        ["systemctl", "--user", "enable", "--now", "ai_framework_thomas"],
    ]:
        try:
            subprocess.run(cmd, check=True, capture_output=True, timeout=10)
        except Exception as e:
            errors.append(str(e))

    if errors:
        return {
            "ok": False,
            "errors": errors,
            "service_file": str(service_file),
            "hint": "systemctl --user enable --now ai_framework_thomas",
        }
    return {"ok": True, "service_file": str(service_file)}
