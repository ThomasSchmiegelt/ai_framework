"""Router: Projekt-API (/api/projects)

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


# ── Projekt-API ───────────────────────────────────────────────────────────────


@router.get("/api/projects")
async def list_projects():
    # Liste (rückwärtskompatibel) — pro Projekt Status + Label ergänzt.
    projects = _load_projects()
    for p in projects:
        st = p.get("status") or "planung"
        p["status"] = st
        p["status_label"] = _PROJECT_STATUS_LABELS.get(st, st)
    return projects


@router.get("/api/project-status-labels")
async def project_status_labels():
    return _PROJECT_STATUS_LABELS


@router.post("/api/projects")
async def create_project(req: Request):
    body = await req.json()
    projects = _load_projects()
    project = {
        "id": uuid.uuid4().hex[:8],
        "name": str(body.get("name", "Neues Projekt")).strip(),
        "number": str(body.get("number", "")).strip(),
        "description": str(body.get("description", "")).strip(),
        "status": "planung",
        "plan_id": str(body.get("plan_id", "")).strip(),
        "created_at": time.time(),
    }
    projects.append(project)
    _save_projects(projects)
    return project


@router.put("/api/projects/{pid}")
async def update_project(pid: str, req: Request):
    body = await req.json()
    projects = _load_projects()
    hit = None
    for p in projects:
        if p["id"] == pid:
            # Stammdaten nur überschreiben, wenn im Body enthalten
            if "name" in body:
                p["name"] = str(body.get("name") or p.get("name", "")).strip()
            if "number" in body:
                p["number"] = str(body.get("number") or "").strip()
            if "description" in body:
                p["description"] = str(body.get("description") or "").strip()
            # Workflow-Felder (nur bei Vorhandensein)
            for k in ("status", "plan_id", "angebot_nr", "rechnung_nr"):
                if k in body:
                    p[k] = str(body.get(k) or "").strip()
            if body.get("status") and body["status"] not in _PROJECT_STATUS_LABELS:
                raise HTTPException(status_code=400, detail="Unbekannter Projektstatus.")
            p["updated_at"] = time.time()
            hit = p
            break
    if hit is None:
        raise HTTPException(status_code=404, detail="Projekt nicht gefunden.")
    _save_projects(projects)
    hit["status_label"] = _PROJECT_STATUS_LABELS.get(hit.get("status") or "planung", "")
    return {"ok": True, "project": hit}


@router.delete("/api/projects/{pid}")
async def delete_project(pid: str):
    projects = [p for p in _load_projects() if p["id"] != pid]
    PROJECTS_FILE.write_text(json.dumps(projects, ensure_ascii=False, indent=2), encoding="utf-8")
    # Projekt-gebundene Skill-Agenten mitlöschen (sie sind ausschließlich diesem
    # Projekt zugeordnet und sonst nirgends sichtbar → keine Karteileichen hinterlassen).
    removed = 0
    for f in list(AGENTS_DIR.glob("*.json")):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        if (data.get("project_id") or "") == pid:
            f.unlink(missing_ok=True)
            removed += 1
    return {"ok": True, "agents_removed": removed}


@router.put("/api/conversations/{cid}/project")
async def set_conversation_project(cid: str, req: Request):
    body = await req.json()
    project_id = body.get("project_id")
    await _db.set_project(cid, project_id)
    return {"ok": True}
