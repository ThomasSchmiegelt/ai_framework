"""Router: Bildgenerierung/-bearbeitung (/api/image, Cores in core)

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


@router.get("/api/image/config")
async def image_config():
    """UI-Info analog ``/api/tts/config``: aktives Bildmodell, SD-URL, Presets und
    die wählbaren Optionen (aus = leer, lokal SD, je Anbieter DALL·E/gpt-image)."""
    m = _image_model()
    options = [
        {"value": "", "label": "Aus (keine Bildgenerierung)"},
        {"value": "local::sd", "label": "Lokal · Stable Diffusion WebUI"},
    ]
    if not _secret_local():
        # Einheitlich wie die Rollen-Modelle: die unter „☁ KI-Anbieter" konfigurierten
        # Anbieter samt ihrer Modell-Liste (z. B. z-image-turbo). Der Anbieter (URL +
        # Schlüssel) wird dort eingetragen; hier wird nur das Modell gewählt.
        for p in _llm.load_providers():
            pid = p.get("id")
            if not pid:
                continue
            pname = p.get("name") or pid
            seen = set()
            for mdl in (p.get("models") or []):
                val = f"{pid}::{mdl}"
                seen.add(val)
                options.append({"value": val, "label": f"{pname} · {mdl}"})
            # Gängige Bildmodelle zusätzlich anbieten, falls der Anbieter sie nicht listet.
            for im in ("dall-e-3", "gpt-image-1"):
                val = f"{pid}::{im}"
                if val not in seen:
                    options.append({"value": val, "label": f"{pname} · {im}"})
    # aktuelle Auswahl immer wählbar halten
    if m and not any(o["value"] == m for o in options):
        options.append({"value": m, "label": m})
    return {
        "image_model": m,
        "sd_url": _sd_url(),
        "secret": _secret_local(),
        "enable_api": bool(_CONFIG.get("enable_api", True)),
        "sizes": [{"value": k, "label": v["label"]} for k, v in _IMAGE_SIZES.items()],
        "options": options,
    }


@router.post("/api/image/generate")
async def image_generate(req: Request):
    """Erzeugt ein Bild aus einem Prompt (HTTP-Endpoint, siehe _generate_image_core)."""
    body = await req.json()
    return await _generate_image_core(body.get("prompt", ""), body.get("negative_prompt", ""),
                                      body.get("size", "square"), body.get("model", ""))


@router.post("/api/image/edit")
async def image_edit(req: Request):
    """Bildbearbeitung (img2img). Body ``{image (base64/Data-URI), prompt (Anweisung),
    strength?, preset?, model?}`` → ``{image, model, prompt}``. Lokal (SD-WebUI /
    Z-Image-Brücke) oder API-Edits (nur fähige Modelle). Bild ≠ Token-Strom → kein
    TokenMeter (wie Bildgenerierung)."""
    body = await req.json()
    return await _edit_image_core(
        str(body.get("prompt", "") or ""),
        str(body.get("image", "") or ""),
        body.get("strength", 0.55),
        str(body.get("preset", "square") or "square"),
        str(body.get("model", "") or ""),
        mask_b64=str(body.get("mask", "") or "") or None,
    )


_UPSCALE_PROMPT = ("hochauflösend, gestochen scharf, feine Details, klare Konturen, "
                   "hohe Bildqualität")
_UPSCALE_MAX_AI = 2048      # Deckel lange Seite beim KI-Weg (VRAM-Schutz)
_UPSCALE_MAX_FAST = 4096    # Deckel lange Seite beim Lanczos-Weg


@router.post("/api/image/upscale")
async def image_upscale(req: Request):
    """Bild hochskalieren. Body ``{image (base64/Data-URI), factor?, mode?, model?}``
    → ``{image, mode, width, height, note?}``. ``mode`` 'ai' (Z-Image-Detail-Upscale,
    lokal; Rückfall Lanczos) / 'fast' (Lanczos). Bild ≠ Token-Strom → kein TokenMeter."""
    body = await req.json()
    return await _upscale_image_core(
        str(body.get("image", "") or ""),
        body.get("factor", 2.0),
        str(body.get("mode", "ai") or "ai"),
        str(body.get("model", "") or ""),
    )

