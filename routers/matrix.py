"""Router: Matrix-Recherche + Wissensgraph (/api/matrix)

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


@router.post("/api/matrix/export-md-zip")
async def matrix_export_md_zip(req: Request):
    """Packt übergebene Markdown-Dokumente (Matrix-Recherche: eine Zelle je Datei,
    benannt thema_prompt.md) in ein ZIP-Archiv zum Download. Dateinamen werden
    serverseitig auf einen sicheren Basisnamen reduziert (kein Pfad-Traversal)."""
    import io, zipfile, re as _re
    body = await req.json()
    files = body.get("files") or []
    if not isinstance(files, list) or not files:
        raise HTTPException(status_code=400, detail="Keine Dateien übergeben")
    zipname = _re.sub(r"[^\w\-]+", "_", str(body.get("zipname", "")).strip()) or "markdown"
    buf = io.BytesIO()
    seen: set[str] = set()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for i, f in enumerate(files):
            name = _re.sub(r"[\\/]+", "_", str((f or {}).get("name", "")).strip()).lstrip(".")
            content = str((f or {}).get("content", ""))
            if not name:
                name = f"doc_{i + 1}.md"
            if not name.lower().endswith(".md"):
                name += ".md"
            base = name
            n = 2
            while name in seen:
                name = f"{base[:-3]}_{n}.md"
                n += 1
            seen.add(name)
            zf.writestr(name, content)
    buf.seek(0)
    from fastapi.responses import StreamingResponse as SR
    return SR(
        buf,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{zipname}.zip"'},
    )


_MATRIX_GRAPH_SYSTEM = (
    "Du bist ein Analyst für Wissensgraphen. Du bekommst eine Liste von Knoten — jeder "
    "Knoten ist ein Thema bzw. eine Firma aus einer Recherche-Tabelle samt der dazu "
    "recherchierten Informationen. Finde inhaltlich belegbare, gerichtete Beziehungen "
    "ZWISCHEN diesen Knoten (z. B. „liefert an\", „Tochter von\", „Wettbewerber von\", "
    "„kooperiert mit\", „Kunde von\"). Nutze AUSSCHLIESSLICH die vorgegebenen Knoten-IDs "
    "in eckigen Klammern. Erfinde nichts, was nicht aus den Texten hervorgeht; im Zweifel "
    "lieber keine Kante. Halte die Beziehungsbezeichnung kurz (1–3 Wörter). "
    'Antworte NUR mit JSON: {"edges":[{"source":"<id>","target":"<id>","label":"Beziehung"}]}.'
)


@router.post("/api/matrix/graph")
async def matrix_graph(req: Request):
    """KI-Vorschlag für die Verknüpfungen eines Wissensgraphen über die Matrix-Zeilen.
    Knoten = Zeilen (Thema + recherchierte Zellinhalte). Liefert gerichtete Kanten
    zwischen den übergebenen Knoten-IDs; der Nutzer korrigiert sie danach im
    Graph-Editor (Hybrid: KI schlägt vor, Mensch entscheidet)."""
    body = await req.json()
    nodes = body.get("nodes") or []
    if not isinstance(nodes, list) or len(nodes) < 2:
        return {"edges": [], "tokens": {"in": 0, "out": 0}}
    model = _pick_model(body.get("model"), DEFAULT_MODEL)
    hint = str(body.get("hint", "")).strip()

    valid_ids = {str(n.get("id")) for n in nodes if n.get("id")}
    # Zeichenbudget je Knoten am Kontextfenster ausrichten (viele Knoten → knapper).
    per_node = max(400, int(_profile_num_ctx() * 3.5 * 0.6 / max(1, len(nodes))))
    lines = []
    for n in nodes:
        nid = str(n.get("id", "")).strip()
        if not nid:
            continue
        label = str(n.get("label", "")).strip()
        text = " ".join(str(n.get("text", "")).split())[:per_node]
        lines.append(f"[{nid}] {label}" + (f"\n{text}" if text else ""))
    usr = "Knoten:\n\n" + "\n\n".join(lines)
    if hint:
        usr += f"\n\nFokus/Hinweis für die Beziehungssuche: {hint}"

    edges, tin, tout = [], 0, 0
    try:
        async with _model_session(model), httpx.AsyncClient(timeout=180) as client:
            resp = await _llm.chat(client, {
                "model": model, "think": False, "stream": False, "format": "json",
                "messages": [{"role": "system", "content": _MATRIX_GRAPH_SYSTEM},
                             {"role": "user", "content": usr}],
                "options": {"num_ctx": _profile_num_ctx()}, "keep_alive": KEEP_ALIVE,
            })
            resp.raise_for_status()
        j = resp.json()
        tin, tout = _llm_tok(j)
        data = _parse_llm_json(j.get("message", {}).get("content", "")) or {}
        seen = set()
        for e in (data.get("edges") or []):
            s = str((e or {}).get("source", "")).strip()
            t = str((e or {}).get("target", "")).strip()
            lbl = str((e or {}).get("label", "")).strip()[:60]
            if s in valid_ids and t in valid_ids and s != t and (s, t) not in seen:
                seen.add((s, t))
                edges.append({"source": s, "target": t, "label": lbl})
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Graph-Analyse fehlgeschlagen: {e}")
    return {"edges": edges, "tokens": {"in": tin, "out": tout}}


_MATRIX_EXTRACT_SYSTEM = (
    "Du extrahierst aus den Recherche-Informationen zu EINEM Eintrag (Thema/Firma) "
    "typisierte Merkmale, die ihn charakterisieren. Du bekommst eine Liste von "
    "Kategorien. Ordne dem Eintrag pro Kategorie null, ein oder mehrere KONKRETE "
    "Werte zu, die im Text tatsächlich vorkommen (kurze Substantive/Eigennamen, "
    "max. 4 Wörter). Mehrere Werte derselben Kategorie als EINZELNE Einträge. "
    "Schreibe Werte einheitlich (z. B. Orte ohne Zusätze: „Berlin\", nicht "
    "„Sitz in Berlin\"). Erfinde nichts; gibt der Text zu einer Kategorie nichts "
    "her, lass sie weg. Nutze AUSSCHLIESSLICH die vorgegebenen Kategorienamen. "
    'Antworte NUR mit JSON: {"attributes":[{"category":"<Kategorie>","value":"<Wert>"}]}.'
)


@router.post("/api/matrix/extract")
async def matrix_extract(req: Request):
    """Extrahiert für EINEN Matrix-Eintrag (Thema + recherchierte Zellinhalte)
    typisierte Merkmale je vorgegebener Kategorie (z. B. Ort, Tool, Tätigkeit).
    Der Frontend baut daraus „Merkmal-Knoten" (Hubs): Zeilen, die denselben Wert
    teilen, hängen am selben Hub und sind so verbunden. Pro Zeile ein Aufruf."""
    body = await req.json()
    label = str(body.get("label", "")).strip()
    text = " ".join(str(body.get("text", "")).split())
    cats = [str(c).strip() for c in (body.get("categories") or []) if str(c).strip()]
    if not cats:
        cats = ["Tätigkeit", "Ort", "Tool", "Aufgabenbereich", "Name"]
    if not label and not text:
        return {"attributes": [], "tokens": {"in": 0, "out": 0}}
    model = _pick_model(body.get("model"), DEFAULT_MODEL)
    valid_cats = {c.lower(): c for c in cats}

    budget = max(800, int(_profile_num_ctx() * 3.5 * 0.7))
    usr = (
        f"Eintrag: {label}\n\nInformationen:\n{text[:budget]}\n\n"
        f"Kategorien: {', '.join(cats)}"
    )

    attrs, tin, tout = [], 0, 0
    try:
        async with _model_session(model), httpx.AsyncClient(timeout=180) as client:
            resp = await _llm.chat(client, {
                "model": model, "think": False, "stream": False, "format": "json",
                "messages": [{"role": "system", "content": _MATRIX_EXTRACT_SYSTEM},
                             {"role": "user", "content": usr}],
                "options": {"num_ctx": _profile_num_ctx()}, "keep_alive": KEEP_ALIVE,
            })
            resp.raise_for_status()
        j = resp.json()
        tin, tout = _llm_tok(j)
        data = _parse_llm_json(j.get("message", {}).get("content", "")) or {}
        seen = set()
        for a in (data.get("attributes") or []):
            cat = str((a or {}).get("category", "")).strip()
            val = str((a or {}).get("value", "")).strip()[:60]
            canon = valid_cats.get(cat.lower())
            if not canon or not val:
                continue
            key = (canon.lower(), val.lower())
            if key in seen:
                continue
            seen.add(key)
            attrs.append({"category": canon, "value": val})
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Merkmal-Analyse fehlgeschlagen: {e}")
    return {"attributes": attrs, "tokens": {"in": tin, "out": tout}}
