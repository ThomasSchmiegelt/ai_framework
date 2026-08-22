"""Router: Jury-Dokumente (Werkbank, /api/jury-docs)

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


# ── Jury-Dokumente (Werkbank im Jury-Tab: anzeigen, bearbeiten, speichern) ──────

@router.get("/api/jury-docs")
async def list_jury_docs():
    docs = []
    for f in JURY_DOCS_DIR.glob("*.json"):
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
            docs.append({"id": d["id"], "name": d.get("name", ""), "updated_at": d.get("updated_at", 0)})
        except Exception:
            pass
    return sorted(docs, key=lambda x: x.get("updated_at", 0), reverse=True)


@router.get("/api/jury-docs/{doc_id}")
async def get_jury_doc(doc_id: str):
    fp = _jury_doc_path_by_id(doc_id)
    if not fp:
        raise HTTPException(404, "Dokument nicht gefunden")
    return json.loads(fp.read_text(encoding="utf-8"))


@router.post("/api/jury-docs")
async def save_jury_doc(req: Request):
    body = await req.json()
    name = str(body.get("name") or "Unbenannt").strip()
    text = str(body.get("text") or "")
    evaluation = body.get("evaluation")  # optional: zuletzt gespeicherte Bewertung
    doc_id = str(body.get("id") or "").strip()
    if not doc_id:
        doc_id = (_to_slug(name) or "doc") + "_" + uuid.uuid4().hex[:6]
    fp = _jury_doc_path_by_id(doc_id)
    if not fp:
        fp = JURY_DOCS_DIR / f"{_to_slug(name)}_{doc_id[-6:]}.json"
    data = {"id": doc_id, "name": name, "text": text, "evaluation": evaluation,
            "updated_at": time.time()}
    fp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return data


@router.delete("/api/jury-docs/{doc_id}")
async def delete_jury_doc(doc_id: str):
    fp = _jury_doc_path_by_id(doc_id)
    if not fp:
        raise HTTPException(404, "Dokument nicht gefunden")
    fp.unlink()
    return {"ok": True}
