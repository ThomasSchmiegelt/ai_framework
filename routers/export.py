"""Router: Export-API (DOCX/XLSX/PPTX/PDF/LaTeX)

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


# ── Export-API ────────────────────────────────────────────────────────────────


@router.post("/api/export/docx")
async def export_docx(req: Request):
    from tools.export import to_docx
    body = await req.json()
    body["_profile"] = _load_profile()
    fp = to_docx(body)
    return FileResponse(
        fp,
        filename="ai_framework_thomas_dokument.docx",
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )


@router.post("/api/export/xlsx")
async def export_xlsx(req: Request):
    from tools.export import to_xlsx
    body = await req.json()
    body["_profile"] = _load_profile()
    fp = to_xlsx(body)
    return FileResponse(
        fp,
        filename="ai_framework_thomas_tabelle.xlsx",
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


@router.post("/api/export/pptx")
async def export_pptx(req: Request):
    from tools.export import to_pptx
    body = await req.json()
    body["_profile"] = _load_profile()
    fp = to_pptx(body)
    return FileResponse(
        fp,
        filename="ai_framework_thomas_praesentation.pptx",
        media_type="application/vnd.openxmlformats-officedocument.presentationml.presentation",
    )


@router.post("/api/export/pdf")
async def export_pdf(req: Request):
    """PDF aus Dokument ({title,content}) oder Präsentation ({type:'presentation'})
    — über matplotlib, ohne TeX-Installation."""
    from tools.export import to_pdf
    body = await req.json()
    body["_profile"] = _load_profile()
    try:
        fp = await asyncio.to_thread(to_pdf, body)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"PDF-Export fehlgeschlagen: {e}")
    name = ("ai_framework_thomas_praesentation.pdf"
            if body.get("type") == "presentation" else "ai_framework_thomas_dokument.pdf")
    return FileResponse(fp, filename=name, media_type="application/pdf")


@router.post("/api/export/latex")
async def export_latex(req: Request):
    """Reine LaTeX-Quelle (.tex): Dokument → article, Präsentation → beamer.
    Formeln bleiben echtes LaTeX-Math; keine TeX-Installation nötig."""
    from tools.export import to_latex
    body = await req.json()
    body["_profile"] = _load_profile()
    try:
        fp = await asyncio.to_thread(to_latex, body)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"LaTeX-Export fehlgeschlagen: {e}")
    name = ("ai_framework_thomas_praesentation.tex"
            if body.get("type") == "presentation" else "ai_framework_thomas_dokument.tex")
    return FileResponse(fp, filename=name, media_type="application/x-tex")

