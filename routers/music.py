"""Router: Musik-Generator (/api/music, algorithmisch)

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


@router.post("/api/music/generate")
async def music_generate(req: Request):
    """Algorithmischer Musik-Generator (Chat-Befehl ``/musik``). Body ``{description,
    style?, key?, tempo?, bars?, seed?}`` → ``{audio: data:audio/wav;base64…, style, key,
    tempo, bars, seconds}``. Reine Musiktheorie/Synthese (``tools/music.py``, nur
    Standardbibliothek, MIT) – kein LLM, kein GPU, kein TokenMeter. CPU-gebunden, daher
    in einem Thread erzeugt, um den Event-Loop nicht zu blockieren."""
    import base64
    from tools import music as _music
    body = await req.json()
    _tempo = body.get("tempo")
    try:
        _tempo = int(_tempo) if _tempo not in (None, "") else None
    except Exception:
        _tempo = None
    try:
        _bars = int(body.get("bars") or 16)
    except Exception:
        _bars = 16
    _seed = body.get("seed")
    try:
        _seed = int(_seed) if _seed not in (None, "") else None
    except Exception:
        _seed = None
    try:
        res = await asyncio.to_thread(
            _music.generate,
            str(body.get("description", "") or ""),
            (str(body.get("style", "")).strip() or None),
            (str(body.get("key", "")).strip() or None),
            _tempo, _bars, _seed,
        )
    except Exception as e:
        raise HTTPException(500, f"Musik-Erzeugung fehlgeschlagen: {e}")
    b64 = base64.b64encode(res["wav"]).decode("ascii")
    return {"audio": f"data:audio/wav;base64,{b64}", "style": res["style"],
            "key": res["key"], "tempo": res["tempo"], "bars": res["bars"],
            "seconds": res["seconds"]}

