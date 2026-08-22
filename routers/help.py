"""Router: Hilfe-Wissensdatenbank + Hilfe-Agent (/api/help)

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


# ── Hilfe-Wissensdatenbank + Hilfe-Agent (aus der mitgelieferten Doku) ──────────

_HELP_DOC_FILES = [
    "README.md", "BEDIENUNGSANLEITUNG.md",
    "docs/ENTWICKLUNG.md", "docs/SERVER.md", "docs/PERSISTENZ.md",
    "docs/TECHNISCHE_BESCHREIBUNG.md", "docs/PORTABLE.md",
    "docs/INSTALL.md", "docs/GITHUB.md", "docs/LIZENZEN.md",
    "docs/HANDY_ZUGRIFF.md",
]
_HELP_COLLECTION_NAME = "Hilfe: LOCAL AI"
_HELP_AGENT_ID = "hilfe_agent"


def _help_agent_path() -> Optional[Path]:
    for f in AGENTS_DIR.glob("*.json"):
        try:
            if json.loads(f.read_text(encoding="utf-8")).get("id") == _HELP_AGENT_ID:
                return f
        except Exception:
            pass
    return None


@router.post("/api/help/build")
async def help_build(req: Request):
    """Liest die mitgelieferte Tool-Doku in eine RAG-Wissensdatenbank ein und legt
    (oder aktualisiert) einen Hilfe-Agenten an, der ausschließlich daraus antwortet.
    Idempotent: eine vorhandene Hilfe-Sammlung wird ersetzt (frische Doku)."""
    from tools.rag import ingest_file
    root = APP_DIR

    # vorhandene Hilfe-Sammlung(en) entfernen → frisch aufbauen
    try:
        for c in await _db.rag_list_collections():
            if c.get("name") == _HELP_COLLECTION_NAME:
                await _db.rag_delete_collection(c["id"])
    except Exception:
        pass

    coll = {
        "id": f"rag_{uuid.uuid4().hex[:12]}",
        "name": _HELP_COLLECTION_NAME,
        "embed_model": EMBED_MODEL,
        "tier": "ausgewogen",
        "chunk_size": 1000, "chunk_overlap": 150, "top_k": 6,
        "embed_gpu": False, "clean": True, "char_limit": 6000,
        "strictness": "korrekt", "created_at": time.time(),
    }
    await _db.rag_create_collection(coll)

    ingested = 0
    try:
        for rel in _HELP_DOC_FILES:
            fp = root / rel
            if not fp.is_file():
                continue
            try:
                text = fp.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue
            if not text.strip():
                continue
            await ingest_file(coll, text, rel, f"doc_{uuid.uuid4().hex[:12]}")
            ingested += 1
    except Exception as e:
        await _db.rag_delete_collection(coll["id"])
        raise HTTPException(
            status_code=500,
            detail=f"Einbetten fehlgeschlagen — ist das Embedding-Modell '{EMBED_MODEL}' gepullt? ({e})")

    if not ingested:
        await _db.rag_delete_collection(coll["id"])
        raise HTTPException(status_code=404, detail="Keine Doku-Dateien gefunden.")

    system_prompt = (
        "Du bist der Hilfe-Assistent für die Anwendung „LOCAL AI“. Dir ist die komplette "
        "Bedienungs- und Entwicklerdokumentation als Wissensdatenbank hinterlegt. Beantworte "
        "Fragen zur Bedienung, zu Tabs/Funktionen und zur Einrichtung AUSSCHLIESSLICH anhand "
        "der eingeblendeten Doku-Auszüge und nenne den jeweiligen Abschnitt/die Datei. Steht "
        "etwas nicht in der Doku, sage das klar und rate nicht. Antworte präzise auf Deutsch."
    )
    existing = _help_agent_path()
    if existing:
        try:
            data = json.loads(existing.read_text(encoding="utf-8"))
        except Exception:
            data = {}
        data.update({
            "id": _HELP_AGENT_ID, "name": data.get("name") or "Hilfe-Assistent",
            "description": "Beantwortet Fragen zur Bedienung des Tools aus der mitgelieferten Doku.",
            "system_prompt": system_prompt, "icon": "🆘", "category": "Hilfe",
            "favorite": True, "rag_collections": [coll["id"]],
        })
        data.setdefault("tools", [])
        existing.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        agent_id = _HELP_AGENT_ID
    else:
        agent = AgentDef(
            id=_HELP_AGENT_ID,
            name="Hilfe-Assistent",
            description="Beantwortet Fragen zur Bedienung des Tools aus der mitgelieferten Doku.",
            system_prompt=system_prompt,
            tools=[],
            icon="🆘",
            category="Hilfe",
            favorite=True,
            rag_collections=[coll["id"]],
        )
        fp = _unique_agent_path(agent.name, exclude_id=agent.id)
        fp.write_text(json.dumps(agent.model_dump(), ensure_ascii=False, indent=2), encoding="utf-8")
        agent_id = agent.id

    return {"ok": True, "rag_collection_id": coll["id"], "agent_id": agent_id,
            "docs": ingested, "agent_name": "Hilfe-Assistent"}


@router.get("/api/help/guide")
async def help_guide():
    """Liefert die Handy-/FritzBox-Anleitung (docs/HANDY_ZUGRIFF.md) als Markdown
    für das Anleitungs-Fenster im Nutzerprofil."""
    fp = APP_DIR / "docs" / "HANDY_ZUGRIFF.md"
    if not fp.is_file():
        raise HTTPException(status_code=404, detail="Anleitung nicht gefunden")
    return {"markdown": fp.read_text(encoding="utf-8", errors="ignore")}
