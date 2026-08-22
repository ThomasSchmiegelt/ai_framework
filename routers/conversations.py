"""Router: Konversations-API (/api/conversations, /api/search)

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


# ── Konversations-API ─────────────────────────────────────────────────────────


@router.get("/api/conversations")
async def list_conversations(project_id: Optional[str] = None):
    return await _db.list_conversations(project_id=project_id)


@router.get("/api/conversations/{cid}")
async def get_conversation(cid: str):
    data = await _db.get_conversation(cid)
    if not data:
        raise HTTPException(404, "Nicht gefunden")
    return data


@router.delete("/api/conversations/{cid}")
async def delete_conversation(cid: str):
    await _db.delete_conversation(cid)
    return {"ok": True}


@router.post("/api/conversations/{cid}/compress")
async def compress_conversation(cid: str):
    import re
    data = await _db.get_conversation(cid)
    if not data:
        raise HTTPException(404, "Nicht gefunden")

    msgs = data["messages"]
    model = data.get("model") or DEFAULT_MODEL

    full_text = ""
    for m in msgs:
        if m["role"] == "system":
            continue
        label = "Benutzer" if m["role"] == "user" else "Assistent"
        full_text += f"{label}: {str(m.get('content', ''))[:600]}\n\n"

    prompt = (
        "Fasse das folgende Gespräch zu einer kompakten Zusammenfassung zusammen. "
        "Die Zusammenfassung soll alle wichtigen Informationen, Erkenntnisse, Ergebnisse und "
        "offenen Punkte enthalten, sodass das Gespräch nahtlos fortgesetzt werden kann. "
        "Antworte NUR mit der Zusammenfassung, keine Einleitung.\n\n"
        f"--- GESPRÄCH ---\n{full_text[:10000]}"
    )

    async with _model_session(model), httpx.AsyncClient(timeout=120) as client:
        resp = await _llm.chat(client,{
            "model": model,
            "think": False,
            "messages": [{"role": "user", "content": prompt}],
            "stream": False,
        })
        resp.raise_for_status()
        result = resp.json()
        _cc_ti, _cc_to = _llm_tok(result)

    summary = result.get("message", {}).get("content", "").strip()
    summary = re.sub(r"<think>.*?</think>", "", summary, flags=re.DOTALL).strip()

    # System-Nachricht mit Zusammenfassung + letzte 2 Austausche behalten
    compressed: list = [
        {"role": "system", "content": f"[ZUSAMMENFASSUNG DES BISHERIGEN GESPRÄCHS]\n\n{summary}"}
    ]
    exchanges = []
    i = 0
    while i < len(msgs):
        if msgs[i]["role"] == "user" and i + 1 < len(msgs) and msgs[i + 1]["role"] == "assistant":
            exchanges.append((msgs[i], msgs[i + 1]))
            i += 2
        else:
            i += 1
    for u, a in exchanges[-2:]:
        compressed.append(u)
        compressed.append(a)

    await _db.save_conversation(cid, compressed, model=model, agent_id=data.get("agent_id"))
    return {"ok": True, "summary": summary, "messages": compressed,
            "tokens": {"in": _cc_ti, "out": _cc_to}}


@router.post("/api/conversations/{cid}/to-skill")
async def conversation_to_skill(cid: str):
    import re
    data = await _db.get_conversation(cid)
    if not data:
        raise HTTPException(404, "Nicht gefunden")

    msgs = data["messages"]
    model = data.get("model") or DEFAULT_MODEL

    full_text = ""
    for m in msgs:
        if m["role"] == "system":
            continue
        label = "Benutzer" if m["role"] == "user" else "Assistent"
        full_text += f"{label}: {str(m.get('content', ''))[:600]}\n\n"

    prompt = (
        "Analysiere das folgende Gespräch und erstelle daraus einen spezialisierten KI-Agenten.\n\n"
        "Antworte NUR mit einem JSON-Objekt, ohne Markdown-Blöcke, ohne Erklärung:\n"
        "{\n"
        '  "name": "Kurzname des Agenten (max 30 Zeichen)",\n'
        '  "icon": "Ein passendes Emoji",\n'
        '  "description": "Was der Agent kann (max 100 Zeichen)",\n'
        '  "category": "Genau eine aus: Fertigung, Qualität, Dokumentation, Kommunikation, Analyse, Recherche, Technik, Sonstige",\n'
        '  "system_prompt": "Detaillierter System-Prompt der das Wissen aus dem Gespräch einbettet. Beginnt mit Du bist ein... Enthält wichtige Erkenntnisse, Methoden und Fachkontext. Maximal 300 Wörter. Auf Deutsch.",\n'
        '  "tools": ["Sinnvolle Tools aus: web_search, calculate, create_presentation, create_spreadsheet"]\n'
        "}\n\n"
        f"--- GESPRÄCH ---\n{full_text[:10000]}"
    )

    async with _model_session(model), httpx.AsyncClient(timeout=120) as client:
        resp = await _llm.chat(client,{
            "model": model,
            "think": False,
            "messages": [{"role": "user", "content": prompt}],
            "stream": False,
        })
        resp.raise_for_status()
        result = resp.json()
        _ts_ti, _ts_to = _llm_tok(result)

    content = result.get("message", {}).get("content", "").strip()
    content = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL).strip()
    content = re.sub(r"```(?:json)?", "", content).replace("```", "").strip()

    try:
        m = re.search(r'\{.*\}', content, re.DOTALL)
        skill_data = json.loads(m.group() if m else content)
    except Exception:
        skill_data = {
            "name": data["title"][:30],
            "icon": "🧠",
            "description": "Aus Chat generierter Agent",
            "category": "Sonstige",
            "system_prompt": f"Du bist ein spezialisierter Assistent basierend auf folgendem Gespräch.\n\n{full_text[:500]}",
            "tools": [],
        }

    skill_data["tokens"] = {"in": _ts_ti, "out": _ts_to}
    return skill_data


@router.patch("/api/conversations/{cid}/rename")
async def rename_conversation(cid: str, req: Request):
    body = await req.json()
    new_title = str(body.get("title", "")).strip()
    if not new_title:
        raise HTTPException(400, "Kein Titel angegeben")
    await _db.rename_conversation(cid, new_title)
    return {"ok": True, "title": new_title}


@router.get("/api/conversations/{cid}/export")
async def export_conversation(cid: str):
    data = await _db.get_conversation(cid)
    if not data:
        raise HTTPException(404, "Nicht gefunden")
    slug = _to_slug(data.get("title", cid))
    filename = f"{slug}.json"
    content = json.dumps(data, ensure_ascii=False, indent=2)
    from fastapi.responses import Response
    return Response(
        content=content.encode("utf-8"),
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/api/conversations/import")
async def import_conversation(req: Request):
    body = await req.json()
    conv_id = body.get("id") or f"conv_{int(time.time() * 1000)}"
    # Neue ID vergeben um Kollisionen zu vermeiden
    conv_id = f"import_{uuid.uuid4().hex[:10]}"
    messages = body.get("messages", [])
    model = body.get("model")
    agent_id = body.get("agent_id")
    canvas_json = body.get("canvas_json")
    await _db.save_conversation(conv_id, messages, model=model, agent_id=agent_id, canvas_json=canvas_json)
    # Projekt-ID setzen falls vorhanden
    if body.get("project_id"):
        await _db.set_project(conv_id, body["project_id"])
    return {"ok": True, "id": conv_id}


@router.post("/api/conversations/{cid}/save")
async def save_conversation_messages(cid: str, req: Request):
    """Nachrichten einer Unterhaltung persistieren – für Nicht-Chat-Abläufe (z. B.
    Bildgenerierung), die NICHT über /api/chat laufen und sonst nicht in der
    Unterhaltungsliste gespeichert würden."""
    body = await req.json()
    messages = body.get("messages", [])
    if not isinstance(messages, list) or not messages:
        raise HTTPException(400, "Keine Nachrichten")
    await _db.save_conversation(
        cid, messages,
        model=body.get("model"),
        agent_id=body.get("agent_id"),
        canvas_json=body.get("canvas_json"),
    )
    if body.get("project_id"):
        await _db.set_project(cid, body["project_id"])
    return {"ok": True, "id": cid}


@router.post("/api/conversations/export-all")
async def export_all_conversations():
    """Exportiert alle Gespräche als ZIP-Archiv."""
    import io, zipfile
    convs = await _db.list_conversations(limit=9999)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for c in convs:
            data = await _db.get_conversation(c["id"])
            if data:
                slug = _to_slug(data.get("title", c["id"]))
                filename = f"{slug}_{c['id'][:8]}.json"
                zf.writestr(filename, json.dumps(data, ensure_ascii=False, indent=2))
    buf.seek(0)
    from fastapi.responses import StreamingResponse as SR
    return SR(
        buf,
        media_type="application/zip",
        headers={"Content-Disposition": 'attachment; filename="ai_framework_thomas_gespraeche.zip"'},
    )


@router.get("/api/search")
async def search_conversations(q: str = Query(..., min_length=2)):
    return await _db.search(q)
