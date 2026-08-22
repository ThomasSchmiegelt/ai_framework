"""Router: Excel-Vergleich (Tab + Chat /excelvergleich)

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


# ── Excel-Vergleich (Tab „Excel-Vergleich" + Chat /excelvergleich) ────────────
# Zwei Tabellenblätter über eine Schlüsselspalte deterministisch diffen
# (tools/tablediff.py) und zusätzlich per LLM inhaltlich bewerten. Benannt
# gespeichert unter data/compare/<name>/comparison.json. Modellwahl über
# _pick_model → Geheim-/Hartman-Modus wird automatisch beachtet.

def _cmp_safe_name(name: str) -> str:
    safe = "".join(c for c in str(name or "") if c.isalnum() or c in (" ", "_", "-")).strip()
    if not safe or safe.startswith("_"):
        raise HTTPException(status_code=400, detail="Ungültiger Name")
    return safe


def _cmp_dir(name: str, create: bool = False) -> Path:
    d = COMPARE_DIR / _cmp_safe_name(name)
    if create:
        d.mkdir(parents=True, exist_ok=True)
    elif not d.exists():
        raise HTTPException(status_code=404, detail="Vergleich nicht gefunden")
    return d


def _cmp_side(s) -> dict:
    s = s or {}
    return {
        "file_id": str(s.get("file_id", ""))[:200],
        "filename": str(s.get("filename", ""))[:200],
        "sheet": str(s.get("sheet", ""))[:200],
        "header_row": int(s.get("header_row", 0) or 0),
        "key": s.get("key", 0),
    }


def _cmp_save(name: str, body: dict) -> dict:
    d = _cmp_dir(name, create=True)
    data = {
        "title": str(body.get("title", name)).strip()[:200] or name,
        "side_a": _cmp_side(body.get("side_a")),
        "side_b": _cmp_side(body.get("side_b")),
        "diff": body.get("diff") or {},
        "evaluation": str(body.get("evaluation", ""))[:200000],
        "updated_at": time.time(),
    }
    (d / "comparison.json").write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return data


def _cmp_load(name: str) -> dict:
    p = _cmp_dir(name) / "comparison.json"
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {"title": name, "side_a": {}, "side_b": {}, "diff": {}, "evaluation": ""}


@router.post("/api/compare/preview")
async def compare_preview(file: UploadFile = File(...), sheet: str = Form(""),
                          header_row: int = Form(0)):
    """Lädt eine Excel-/CSV-Datei hoch und liefert Blätter/Kopfzeilen/Beispielzeilen."""
    from tools.files import read_table
    fid = f"compare_{uuid.uuid4().hex[:8]}_{file.filename}"
    fp = UPLOADS_DIR / fid
    fp.write_bytes(await file.read())
    try:
        tbl = await asyncio.to_thread(read_table, fp, sheet or None, int(header_row), 5)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Tabelle nicht lesbar: {e}")
    if tbl.get("error"):
        raise HTTPException(status_code=400, detail=tbl["error"])
    full = await asyncio.to_thread(read_table, fp, tbl.get("sheet") or None, int(header_row), None)
    return {
        "file_id": fid,
        "filename": file.filename,
        "sheets": tbl.get("sheets", []),
        "sheet": tbl.get("sheet", ""),
        "headers": tbl.get("headers", []),
        "sample_rows": tbl.get("rows", []),
        "n_rows": len(full.get("rows", [])),
    }


@router.post("/api/compare/run")
async def compare_run(req: Request):
    body = await req.json()

    async def gen():
        from tools.files import read_table
        from tools import tablediff as _td
        fa = str(body.get("file_id_a", "")).strip()
        fb = str(body.get("file_id_b", "")).strip()
        pa, pb = UPLOADS_DIR / fa, UPLOADS_DIR / fb
        if not fa or not fb or not pa.exists() or not pb.exists():
            yield _sse({"type": "error", "message": "Datei(en) nicht gefunden — bitte erneut einlesen."})
            return
        sa = str(body.get("sheet_a", "")).strip() or None
        sb = str(body.get("sheet_b", "")).strip() or None
        ha = int(body.get("header_row_a", 0) or 0)
        hb = int(body.get("header_row_b", 0) or 0)
        ka = body.get("key_a", 0)
        kb = body.get("key_b", 0)
        try:
            ta = await asyncio.to_thread(read_table, pa, sa, ha, None)
            tb = await asyncio.to_thread(read_table, pb, sb, hb, None)
        except Exception as e:
            yield _sse({"type": "error", "message": f"Tabelle nicht lesbar: {e}"})
            return
        if ta.get("error") or tb.get("error"):
            yield _sse({"type": "error", "message": ta.get("error") or tb.get("error")})
            return
        diff = _td.diff_tables(ta.get("headers", []), ta.get("rows", []), ka,
                               tb.get("headers", []), tb.get("rows", []), kb)
        yield _sse({"type": "diff", "diff": diff})

        model = _pick_model(body.get("model"), _model_for("general"))
        _nc = _profile_num_ctx()
        _budget = max(2000, int(_nc * 3.2))
        summary = _td.diff_summary_text(diff, max_lines=300)[:_budget]
        sysmsg = (
            "Du bist ein sorgfältiger Analyst. Vergleiche zwei Tabellenblätter anhand des "
            "bereitgestellten strukturierten Diffs (nur-in-A, nur-in-B, geänderte Zellen). "
            "Fasse die wichtigsten inhaltlichen Unterschiede auf Deutsch strukturiert zusammen "
            "(Markdown: ## Überschriften, **fett**, Aufzählungen). Gruppiere nach Art der Änderung "
            "und hebe auffällige/relevante Abweichungen hervor. Stütze JEDE Aussage AUSSCHLIESSLICH "
            "auf die Diff-Daten — erfinde nichts und spekuliere nicht über Ursachen. Fehlt eine "
            "Information, sage das."
        )
        usr = (
            f"Blatt A: {ta.get('sheet','')} · Blatt B: {tb.get('sheet','')}\n"
            f"Schlüsselspalte A: {diff.get('key_col_a','')} · B: {diff.get('key_col_b','')}\n\n"
            f"Strukturierter Diff:\n{summary}\n\n"
            "Erstelle die Auswertung."
        )
        _j = {}
        try:
            async with _model_session(model), httpx.AsyncClient(timeout=300) as client:
                resp = await _llm.chat(client, {
                    "model": model, "think": False, "stream": False,
                    "messages": [{"role": "system", "content": sysmsg},
                                 {"role": "user", "content": usr}],
                    "options": {"num_ctx": _nc, "num_predict": max(500, int(_nc * 0.4))},
                    "keep_alive": KEEP_ALIVE,
                })
                resp.raise_for_status()
                _j = resp.json()
        except httpx.ConnectError:
            yield _sse({"type": "error", "message": "Ollama nicht erreichbar — läuft der lokale Server?"})
            return
        except Exception as e:
            yield _sse({"type": "error", "message": f"KI-Bewertung fehlgeschlagen: {e}"})
            return
        content = str(_j.get("message", {}).get("content", ""))
        content = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL).strip()
        _ti, _to = _llm_tok(_j)
        words = content.split(" ")
        for i, w in enumerate(words):
            yield _sse({"type": "text", "content": w + (" " if i < len(words) - 1 else "")})
            await asyncio.sleep(0.003)
        yield _sse({"type": "done", "evaluation": content, "tokens": {"in": _ti, "out": _to}})

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.get("/api/compare/projects")
async def compare_list():
    out = []
    if COMPARE_DIR.exists():
        for d in sorted(COMPARE_DIR.iterdir()):
            if not d.is_dir() or d.name.startswith("_"):
                continue
            p = d / "comparison.json"
            meta = {}
            if p.exists():
                try:
                    meta = json.loads(p.read_text(encoding="utf-8"))
                except Exception:
                    meta = {}
            cnt = (meta.get("diff") or {}).get("counts") or {}
            out.append({
                "name": d.name,
                "title": meta.get("title") or d.name,
                "changed": cnt.get("changed", 0),
                "only_a": cnt.get("only_a", 0),
                "only_b": cnt.get("only_b", 0),
                "updated_at": meta.get("updated_at", 0),
            })
    out.sort(key=lambda x: x.get("updated_at", 0), reverse=True)
    return out


@router.post("/api/compare/projects")
async def compare_create(req: Request):
    body = await req.json()
    name = _cmp_safe_name(body.get("name", ""))
    if (COMPARE_DIR / name).exists() and not body.get("overwrite"):
        raise HTTPException(status_code=409, detail="Vergleich existiert bereits")
    return _cmp_save(name, body)


@router.get("/api/compare/projects/{name}")
async def compare_get(name: str):
    return _cmp_load(name)


@router.put("/api/compare/projects/{name}")
async def compare_put(name: str, req: Request):
    body = await req.json()
    return _cmp_save(name, body)


@router.delete("/api/compare/projects/{name}")
async def compare_delete(name: str):
    import shutil
    d = _cmp_dir(name)
    shutil.rmtree(d, ignore_errors=True)
    return {"ok": True}
