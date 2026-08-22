"""Router: Diagnose-Logging (/api/logs)

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


# ── Diagnose-Logging ──────────────────────────────────────────────────────────


@router.get("/api/logs")
async def get_logs():
    if not LOG_FILE.exists():
        return []
    entries = []
    for line in LOG_FILE.read_text(encoding="utf-8").splitlines():
        if line.strip():
            try:
                entries.append(json.loads(line))
            except Exception:
                pass
    return entries


@router.delete("/api/logs")
async def clear_logs():
    if LOG_FILE.exists():
        LOG_FILE.unlink()
    return {"ok": True}


@router.put("/api/logs/config")
async def configure_logs(req: Request):
    global _log_active
    body = await req.json()
    _log_active = bool(body.get("active", False))
    return {"active": _log_active}


@router.get("/api/logs/active")
async def get_logs_active():
    return {"active": _log_active}


@router.post("/api/logs/entry")
async def add_log_entry(req: Request):
    body = await req.json()
    _write_log({k: v for k, v in body.items() if k != "ts"})
    return {"ok": True}


@router.get("/api/logs/download")
async def download_logs():
    if not LOG_FILE.exists():
        from fastapi.responses import Response as _Resp
        return _Resp("", media_type="text/plain")
    return FileResponse(
        LOG_FILE,
        media_type="application/octet-stream",
        filename=f"ai_framework_thomas_{time.strftime('%Y-%m-%d_%H-%M')}.log",
    )
