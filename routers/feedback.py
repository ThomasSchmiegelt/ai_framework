"""Router: Nutzer-Feedback (/api/feedback, Chat /- /+)

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


# ── Nutzer-Feedback aus dem Chat („/-" Fehler, „/+" Idee) ─────────────────────
# „/- <Text>" protokolliert ein Problem/eine Fehlermeldung, „/+ <Text>" eine Idee
# bzw. einen Verbesserungsvorschlag. Alles landet als Markdown in FEEDBACK_FILE,
# gruppiert nach Art, mit Zeitstempel und (optional) der Unterhaltungs-ID.

_FEEDBACK_KINDS = {
    "problem": ("🔴 Fehler & Probleme", "🔴"),
    "idea":    ("🟢 Ideen & Verbesserungen", "🟢"),
}


def _read_feedback_md() -> str:
    try:
        return FEEDBACK_FILE.read_text(encoding="utf-8")
    except FileNotFoundError:
        return ""


def _append_feedback(kind: str, text: str, conversation_id: str = "") -> int:
    """Hängt einen Feedback-Eintrag an das Markdown-Protokoll an. Gibt die
    Gesamtzahl der Einträge zurück. Robust gegen fehlende Datei."""
    from datetime import datetime
    kind = kind if kind in _FEEDBACK_KINDS else "idea"
    _, icon = _FEEDBACK_KINDS[kind]
    text = (text or "").strip()
    ts = datetime.now().strftime("%Y-%m-%d %H:%M")
    existing = _read_feedback_md()
    if not existing:
        existing = (
            "# Nutzer-Feedback\n\n"
            "Im Chat erfasst: `/- …` meldet ein **Problem/einen Fehler**, "
            "`/+ …` notiert eine **Idee/einen Verbesserungsvorschlag**.\n"
        )
    conv = f" · _{conversation_id}_" if conversation_id else ""
    entry = f"\n- {icon} **[{ts}]**{conv} {text}\n"
    FEEDBACK_FILE.write_text(existing.rstrip() + "\n" + entry, encoding="utf-8")
    # Einträge zählen (Listenzeilen mit einem der Icons)
    body = _read_feedback_md()
    return sum(1 for ln in body.splitlines()
               if ln.lstrip().startswith(("- 🔴", "- 🟢")))


@router.post("/api/feedback")
async def add_feedback(req: Request):
    """Speichert Nutzer-Feedback aus dem Chat als Markdown-Protokoll.
    Body: ``{"kind": "problem"|"idea", "text": "…", "conversation_id": "…"}``."""
    body = await req.json()
    text = (body.get("text") or "").strip()
    if not text:
        raise HTTPException(400, "Kein Feedback-Text angegeben")
    kind = "problem" if body.get("kind") == "problem" else "idea"
    count = await asyncio.to_thread(
        _append_feedback, kind, text[:4000], str(body.get("conversation_id") or "")[:80])
    return {"ok": True, "kind": kind, "count": count, "file": FEEDBACK_FILE.name}


@router.get("/api/feedback")
async def get_feedback():
    """Liefert das gesammelte Feedback-Protokoll (Markdown) zurück."""
    content = await asyncio.to_thread(_read_feedback_md)
    return {"markdown": content, "file": FEEDBACK_FILE.name}


