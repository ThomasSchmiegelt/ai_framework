"""Router: Upload-API (/api/upload, /api/uploads)

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


# ── Upload-API ────────────────────────────────────────────────────────────────


@router.post("/api/upload")
async def upload_file(file: UploadFile = File(...)):
    fid = f"{uuid.uuid4().hex[:8]}_{file.filename}"
    fp = UPLOADS_DIR / fid
    content = await file.read()
    fp.write_bytes(content)
    return {
        "id": fid,
        "filename": file.filename,
        "type": file.content_type,
        "is_image": bool(file.content_type and file.content_type.startswith("image/")),
        "size": len(content),
    }


@router.get("/api/uploads/{fid}")
async def get_upload(fid: str):
    fp = UPLOADS_DIR / fid
    if not fp.exists():
        raise HTTPException(404)
    return FileResponse(fp)


