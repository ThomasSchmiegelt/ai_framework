"""Router: Sprachausgabe TTS (/api/tts)

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


# ── Sprachausgabe (TTS) über ein API-Modell ───────────────────────────────────
# Standardmäßig läuft die Sprachausgabe rein clientseitig über die Web Speech API
# des Browsers (kostenlos, lokal, nichts wird gespeichert — siehe static/js/tts.js).
# Optional kann im Profil ein API-TTS-Modell (``anbieter::modell``, z. B.
# ``openai::tts-1``) gewählt werden; dann synthetisiert der Anbieter die Sprache
# (OpenAI-kompatibles ``/audio/speech``) und wir liefern das Audio an den Browser.
# Der Geheim-Modus erzwingt die Browser-Ausgabe (API wird ignoriert).

# Antwortstil-Persona (tone) → OpenAI-kompatible Stimme. Grobe Zuordnung nach
# Geschlecht/Alter/Klang; Anbieter ohne diese Stimmen fallen i. d. R. auf ihre
# Standardstimme zurück.
_TTS_VOICE_MAP = {
    "roboter":   "alloy",    # neutral/synthetisch
    "professor": "onyx",     # tiefer, älterer Mann
    "doktor":    "shimmer",  # ruhige, ältere Frau
    "felix":     "echo",     # jüngerer Mann
    "sandra":    "nova",     # jüngere Frau
    "hartman":   "onyx",     # zackiger Ausbilder (tiefer, markanter Mann)
    "":          "alloy",    # Standard
}


def _tts_model() -> str:
    """Im Profil gewähltes API-TTS-Modell (``anbieter::modell``) oder leer."""
    m = str(_load_profile().get("tts_model", "") or "").strip()
    return "" if m in _MODEL_PLACEHOLDERS else m


@router.get("/api/tts/config")
async def tts_config():
    """UI-Info: ist API-TTS aktiv, welche Optionen (aus den Anbietern) gibt es,
    Persona→Stimme-Zuordnung. Die Optionsliste baut sich aus den konfigurierten
    Anbietern (gängige TTS-Modellnamen als Vorschlag)."""
    m = _tts_model()
    options = [{"value": "", "label": "Browser (lokal, Web Speech API)"}]
    for p in _llm.load_providers():
        pid = p.get("id")
        if not pid:
            continue
        pname = p.get("name") or pid
        for tm in ("tts-1", "gpt-4o-mini-tts"):
            options.append({"value": f"{pid}::{tm}", "label": f"{pname} · {tm}"})
    # aktuelle Auswahl immer wählbar halten
    if m and not any(o["value"] == m for o in options):
        options.append({"value": m, "label": m})
    return {
        "tts_model": m,
        "api_active": bool(m) and _llm.is_remote(m) and not _secret_local(),
        "secret": _secret_local(),
        "enable_api": bool(_CONFIG.get("enable_api", True)),
        "options": options,
        "voices": _TTS_VOICE_MAP,
    }


@router.post("/api/tts")
async def tts_speak(req: Request):
    """Synthetisiert Text zu Sprache über das im Profil gewählte API-Modell.

    Antwort: Audio (``audio/mpeg``). Ist kein API-Modell gewählt, das Modell nicht
    remote oder der Geheim-Modus aktiv → **HTTP 409**, damit das Frontend auf die
    Browser-Sprachausgabe zurückfällt."""
    body = await req.json()
    text = str(body.get("text", "") or "").strip()
    tone = str(body.get("tone", "") or "").strip().lower()
    if not text:
        raise HTTPException(400, "Kein Text für die Sprachausgabe.")
    m = _tts_model()
    if _secret_local() or not m or not _llm.is_remote(m):
        raise HTTPException(409, "API-Sprachausgabe nicht aktiv – Browser-Ausgabe nutzen.")
    provider, real = _llm.resolve(m)
    if not provider:
        raise HTTPException(400, "Unbekannter API-Anbieter für die Sprachausgabe.")
    base = (provider.get("base_url") or "").rstrip("/")
    headers = {"Authorization": f"Bearer {provider.get('api_key', '')}",
               "Content-Type": "application/json"}
    voice = _TTS_VOICE_MAP.get(tone) or _TTS_VOICE_MAP[""]
    payload = {"model": real, "input": text[:4000], "voice": voice,
               "response_format": "mp3"}
    try:
        async with httpx.AsyncClient(timeout=120) as client:
            resp = await client.post(f"{base}/audio/speech", headers=headers, json=payload)
        if resp.status_code >= 400:
            raise HTTPException(502, f"API-Sprachausgabe fehlgeschlagen "
                                     f"(HTTP {resp.status_code}): {resp.text[:300]}")
        audio = resp.content
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(502, f"API-Sprachausgabe fehlgeschlagen: {e}")
    return Response(content=audio, media_type="audio/mpeg")






