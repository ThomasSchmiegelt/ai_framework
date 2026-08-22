"""Router: Kapazitaets-/Ressourcenlisten (/api/capacity)

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


@router.get("/api/capacity")
async def get_capacity():
    return {"items": _load_capacity()}


@router.put("/api/capacity")
async def put_capacity(req: Request):
    body = await req.json()
    items = body.get("items") if isinstance(body, dict) else body
    if not isinstance(items, list):
        raise HTTPException(status_code=400, detail="items-Liste erwartet")
    return {"items": _save_capacity(items)}


# ── Verwaltung mehrerer benannter Ressourcenlisten ──────────────────────────────
@router.get("/api/capacity/lists")
async def get_capacity_lists():
    """Übersicht aller Listen + aktuelle Auswahl (ohne die vollen Items)."""
    data = _load_cap_lists()
    return {
        "lists": [{"id": l["id"], "name": l["name"], "n_items": len(l["items"]),
                   "updated_at": l["updated_at"]} for l in data["lists"]],
        "selected": data["selected"],
    }


@router.get("/api/capacity/lists/{list_id}")
async def get_capacity_list(list_id: str):
    data = _load_cap_lists()
    for l in data["lists"]:
        if l["id"] == list_id:
            return l
    raise HTTPException(status_code=404, detail="Liste nicht gefunden")


@router.post("/api/capacity/lists")
async def create_capacity_list(req: Request):
    body = await req.json()
    name = str(body.get("name", "")).strip()[:80] or "Neue Liste"
    data = _load_cap_lists()
    new = {"id": uuid.uuid4().hex[:12], "name": name, "items": [], "updated_at": time.time()}
    data["lists"].append(new)
    data["selected"] = list(data["selected"]) + [new["id"]]
    _save_cap_lists(data)
    return {"id": new["id"], "name": new["name"]}


@router.put("/api/capacity/lists/{list_id}")
async def update_capacity_list(list_id: str, req: Request):
    body = await req.json()
    data = _load_cap_lists()
    found = None
    for l in data["lists"]:
        if l["id"] == list_id:
            found = l
            break
    if found is None:
        # Upsert: unbekannte ID neu anlegen (erlaubt Anlegen über PUT)
        found = {"id": list_id, "name": "", "items": [], "updated_at": time.time()}
        data["lists"].append(found)
        if list_id not in data["selected"]:
            data["selected"].append(list_id)
    if "name" in body:
        found["name"] = str(body.get("name", "")).strip()[:80] or found.get("name") or "Liste"
    if "items" in body and isinstance(body["items"], list):
        found["items"] = body["items"]
    found["updated_at"] = time.time()
    saved = _save_cap_lists(data)
    out = next((l for l in saved["lists"] if l["id"] == found["id"]), found)
    return out


@router.delete("/api/capacity/lists/{list_id}")
async def delete_capacity_list(list_id: str):
    data = _load_cap_lists()
    data["lists"] = [l for l in data["lists"] if l["id"] != list_id]
    data["selected"] = [s for s in data["selected"] if s != list_id]
    _save_cap_lists(data)
    return {"ok": True}


@router.put("/api/capacity/selection")
async def set_capacity_selection(req: Request):
    """Setzt, welche Listen (per Häkchen) für Auswertung & Planer aktiv sind."""
    body = await req.json()
    sel = body.get("selected") if isinstance(body, dict) else body
    if not isinstance(sel, list):
        raise HTTPException(status_code=400, detail="selected-Liste erwartet")
    data = _load_cap_lists()
    ids = {l["id"] for l in data["lists"]}
    data["selected"] = [s for s in sel if s in ids]
    _save_cap_lists(data)
    return {"selected": data["selected"]}
