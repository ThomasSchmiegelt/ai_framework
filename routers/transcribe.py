"""Router: Transkription (Audio -> Text, /api/transcribe)

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


# ── Transkription (Spracherkennung, Audio → Text) ─────────────────────────────
# Quelle: Mikrofon (inkl. USB) oder Datei. Engine wahlweise LOKAL (faster-whisper,
# CPU-schonend) oder API (OpenAI/Groq-kompatibles /audio/transcriptions). Der
# Geheim-/Lokal-Modus erzwingt die lokale Engine. Audio ist kein Token-Strom →
# keine TokenMeter-Meldung.

@router.get("/api/transcribe/engines")
async def transcribe_engines():
    """Verfügbare Transkriptions-Engines/Modelle für die UI.

    Meldet, ob die lokale Engine (faster-whisper) installiert ist, welche
    lokalen Modellgrößen es gibt, welche API-Anbieter konfiguriert sind, sowie
    ob der Geheim-Modus die API-Wahl gerade sperrt."""
    local_ok = _transcribe.local_available()
    providers = [{"id": p.get("id"), "name": p.get("name") or p.get("id")}
                 for p in _llm.load_providers() if p.get("id")]
    return {
        "local_available": local_ok,
        "local_models": _transcribe.list_local_models() if local_ok else [],
        "local_default": STT_MODEL,
        "providers": providers,
        "local_only": _secret_local(),
        "api_enabled": bool(_CONFIG.get("enable_api", True)),
    }


def _remote_audio_target(model: str) -> tuple:
    """(base_url, headers, real_model) für einen Remote-STT-Aufruf oder ``(None,…)``.

    Nutzt die Anbieter-Konfiguration aus ``tools/llm.py`` (``provider::modell``)."""
    provider, real = _llm.resolve(model)
    if not provider:
        return None, None, real
    base = (provider.get("base_url") or "").rstrip("/")
    headers = {"Authorization": f"Bearer {provider.get('api_key', '')}"}
    return base, headers, real


@router.post("/api/transcribe")
async def transcribe_audio(
    audio: UploadFile = File(...),
    engine: str = Form("local"),
    model: str = Form(""),
    language: str = Form(""),
    task: str = Form("transcribe"),
):
    """Transkribiert eine hochgeladene/aufgenommene Audiodatei.

    ``engine`` = ``local`` (faster-whisper) oder ``api`` (Anbieter-Modell
    ``provider::modell``). Im Geheim-Modus wird ``api`` auf ``local`` gezwungen."""
    data = await audio.read()
    if not data:
        raise HTTPException(400, "Leere Audiodatei.")
    safe_name = re.sub(r"[^A-Za-z0-9._-]", "_", audio.filename or "audio")[-80:] or "audio"
    fid = f"{uuid.uuid4().hex[:8]}_{safe_name}"
    fp = TRANSCRIPTS_DIR / fid
    fp.write_bytes(data)

    use_engine = (engine or "local").strip().lower()
    # Geheim-/Lokal-Modus: API-Transkription unterbinden → immer lokal.
    forced_local = False
    if _secret_local() and use_engine != "local":
        use_engine = "local"
        forced_local = True

    if use_engine == "api":
        mdl = (model or "").strip()
        if not mdl or not _llm.is_remote(mdl):
            raise HTTPException(400, "Für die API-Transkription ein Anbieter-Modell "
                                     "(anbieter::modell) wählen.")
        base, headers, real = _remote_audio_target(mdl)
        if not base:
            raise HTTPException(400, "Unbekannter API-Anbieter für die Transkription.")
        want_task = "translations" if str(task).strip().lower() == "translate" else "transcriptions"
        url = f"{base}/audio/{want_task}"
        files = {"file": (safe_name, data, audio.content_type or "application/octet-stream")}
        form = {"model": real, "response_format": "verbose_json"}
        lang = (language or "").strip().lower()
        if lang and lang != "auto":
            form["language"] = lang
        try:
            async with httpx.AsyncClient(timeout=300) as client:
                resp = await client.post(url, headers=headers, data=form, files=files)
            if resp.status_code >= 400:
                raise HTTPException(502, f"API-Transkription fehlgeschlagen "
                                         f"(HTTP {resp.status_code}): {resp.text[:300]}")
            j = resp.json()
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(502, f"API-Transkription fehlgeschlagen: {e}")
        segs = [{"start": round(float(s.get("start", 0) or 0), 2),
                 "end": round(float(s.get("end", 0) or 0), 2),
                 "text": (s.get("text") or "").strip()}
                for s in (j.get("segments") or [])]
        return {
            "text": (j.get("text") or "").strip(),
            "segments": segs,
            "language": j.get("language") or lang or "",
            "engine": "api", "model": mdl, "audio_id": fid,
        }

    # ── Lokale Engine (faster-whisper) ──────────────────────────────────────
    if not _transcribe.local_available():
        raise HTTPException(503, "Lokale Transkription nicht verfügbar — faster-whisper "
                                 "ist nicht installiert.")
    mdl = (model or "").strip() or STT_MODEL
    if _llm.is_remote(mdl):  # versehentlich Remote-Name im lokalen Pfad → Default
        mdl = STT_MODEL
    try:
        result = await asyncio.to_thread(
            _transcribe.transcribe_local, str(fp), mdl, language, task,
            STT_DEVICE, STT_COMPUTE, str(STT_DOWNLOAD_ROOT),
        )
    except Exception as e:
        raise HTTPException(500, f"Lokale Transkription fehlgeschlagen: {e}")
    result.update({"engine": "local", "model": mdl, "audio_id": fid,
                   "forced_local": forced_local})
    return result
