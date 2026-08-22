"""Router: Modelle + externe KI-Anbieter (/api/models, /api/providers)

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


# ── Routen ────────────────────────────────────────────────────────────────────


@router.get("/api/models")
async def get_models():
    # Lokale Ollama-Modelle + konfigurierte Remote-Modelle (externe API-Anbieter)
    # in EINER Liste, damit die Profil-Rollen-Selects beide anbieten. Remote-Modelle
    # tragen das Präfix "<provider_id>::<model>" und sind mit remote:True markiert.
    result = {"models": []}
    async with httpx.AsyncClient(timeout=10) as client:
        try:
            resp = await client.get(f"{OLLAMA_BASE}/api/tags")
            data = resp.json()
            if ALLOWED_MODELS:
                order = {n: i for i, n in enumerate(ALLOWED_MODELS)}
                data["models"] = sorted(
                    data.get("models", []),
                    key=lambda m: order.get(m["name"], 999),
                )
            result["models"] = data.get("models", [])
        except Exception as e:
            result["error"] = str(e)
    try:
        remote = await _llm.list_remote_models()
        result["models"] = list(result["models"]) + remote
    except Exception:
        pass
    return result


@router.post("/api/model/activate")
async def activate_model(req: Request):
    """Lädt proaktiv das Modell einer Funktion/Rolle in den VRAM (Vorwärmen beim
    Funktionswechsel). Bei Modellwechsel entlädt _model_session das vorherige Modell
    automatisch – so ist beim ersten Senden im neuen Tab schon das richtige LLM
    geladen. Für Remote-Modelle ein No-op. Idempotent: bereits geladenes Modell wird
    nicht erneut geladen."""
    body = await req.json()
    role = str(body.get("role", "") or "").strip().lower()
    if role in _MODEL_ROLES:
        model = _model_for(role)
    else:
        model = _pick_model(body.get("model"), DEFAULT_MODEL)
    if _llm.is_remote(model):
        return {"model": model, "remote": True, "switched": False}
    if _loaded_model == model:
        return {"model": model, "remote": False, "switched": False}
    try:
        async with _model_session(model), httpx.AsyncClient(timeout=120) as client:
            # Leerer Prompt an /api/generate lädt das Modell, ohne zu generieren.
            await client.post(f"{OLLAMA_BASE}/api/generate", json={
                "model": model, "prompt": "", "stream": False,
                "keep_alive": KEEP_ALIVE, "options": {"num_ctx": _profile_num_ctx()},
            })
    except Exception:
        pass
    return {"model": model, "remote": False, "switched": True}


# ── Externe KI-Anbieter (OpenAI-kompatibel) ─────────────────────────────────────
# Konfiguration in data/api_providers.json (enthält API-Keys → gitignored, NICHT im
# Backup). Modelle dieser Anbieter erscheinen präfigiert in /api/models und damit in
# den Profil-Rollen-Selects; die LLM-Abstraktion (tools/llm.py) routet Aufrufe an
# „<id>::<model>" automatisch an den jeweiligen Anbieter.

def _load_api_providers() -> list:
    if not API_PROVIDERS_FILE.exists():
        return []
    try:
        data = json.loads(API_PROVIDERS_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _save_api_providers(items: list) -> None:
    API_PROVIDERS_FILE.write_text(json.dumps(items, ensure_ascii=False, indent=2),
                                  encoding="utf-8")


def _provider_public(p: dict) -> dict:
    """Anbieter ohne API-Key (für die Anzeige im Frontend)."""
    return {"id": p.get("id"), "name": p.get("name"), "base_url": p.get("base_url"),
            "models": p.get("models", []), "has_key": bool(p.get("api_key"))}


@router.get("/api/providers")
async def list_providers():
    return [_provider_public(p) for p in _load_api_providers()]


@router.post("/api/providers")
async def save_provider(req: Request):
    """Anbieter anlegen oder aktualisieren. Body: {id?, name, base_url, api_key?,
    models?}. Ist keine Modell-Liste angegeben, wird sie (best effort) vom Anbieter
    geholt. Ein leeres api_key bei vorhandenem Anbieter behält den alten Key."""
    body = await req.json()
    name = (body.get("name") or "").strip()
    base_url = (body.get("base_url") or "").strip().rstrip("/")
    if not name or not base_url:
        raise HTTPException(status_code=400, detail="Name und Base-URL erforderlich")
    items = _load_api_providers()
    pid = (body.get("id") or "").strip()
    existing = next((p for p in items if p.get("id") == pid), None) if pid else None

    api_key = body.get("api_key")
    if not api_key and existing:
        api_key = existing.get("api_key", "")
    api_key = (api_key or "").strip()

    if not pid:
        pid = _to_slug(name)[:20] or "provider"
        # Kollision vermeiden
        base_pid, i = pid, 2
        while any(p.get("id") == pid for p in items):
            pid = f"{base_pid}{i}"; i += 1

    models = body.get("models") or []
    prov = {"id": pid, "name": name, "base_url": base_url, "api_key": api_key,
            "models": [str(m) for m in models]}
    if not prov["models"]:
        try:
            prov["models"] = await _llm.fetch_provider_models(prov)
        except Exception:
            prov["models"] = []

    if existing:
        existing.update(prov)
    else:
        items.append(prov)
    _save_api_providers(items)
    return _provider_public(prov)


@router.delete("/api/providers/{pid}")
async def delete_provider(pid: str):
    items = [p for p in _load_api_providers() if p.get("id") != pid]
    _save_api_providers(items)
    return {"ok": True}


@router.post("/api/providers/test")
async def test_provider(req: Request):
    """Verbindung testen / Modell-Liste holen. Body: {base_url, api_key} ODER {id}."""
    body = await req.json()
    pid = (body.get("id") or "").strip()
    if pid:
        prov = next((p for p in _load_api_providers() if p.get("id") == pid), None)
        if not prov:
            raise HTTPException(status_code=404, detail="Anbieter nicht gefunden")
    else:
        prov = {"base_url": (body.get("base_url") or "").strip().rstrip("/"),
                "api_key": (body.get("api_key") or "").strip()}
        if not prov["base_url"]:
            raise HTTPException(status_code=400, detail="Base-URL erforderlich")
    try:
        models = await _llm.fetch_provider_models(prov)
        return {"ok": True, "models": models}
    except Exception as e:
        return {"ok": False, "error": str(e)}

