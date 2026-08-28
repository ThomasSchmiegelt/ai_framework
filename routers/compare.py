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
async def compare_preview(file: UploadFile = File(None), sheet: str = Form(""),
                          header_row: int = Form(0), file_id: str = Form("")):
    """Lädt eine Excel-/CSV-Datei hoch und liefert Blätter/Kopfzeilen/Beispielzeilen.

    Ist ``file_id`` gesetzt und liegt die Datei bereits in ``UPLOADS_DIR`` (z. B. eine
    im Chat per ``/api/upload`` angehängte Datei), wird sie **ohne erneuten Upload**
    wiederverwendet — das nutzt der Assistent-Modus-Vorschlag für die zwei Tabellen."""
    from tools.files import read_table
    reuse = str(file_id or "").strip()
    if reuse:
        fp = UPLOADS_DIR / reuse
        # Kein Pfad-Traversal: Datei muss direkt in UPLOADS_DIR liegen.
        if fp.parent != UPLOADS_DIR or not fp.exists():
            raise HTTPException(status_code=404, detail="Datei nicht gefunden — bitte erneut anhängen.")
        fid = reuse
        fname = fp.name
    else:
        if file is None:
            raise HTTPException(status_code=400, detail="Keine Datei übergeben.")
        fid = f"compare_{uuid.uuid4().hex[:8]}_{file.filename}"
        fp = UPLOADS_DIR / fid
        fp.write_bytes(await file.read())
        fname = file.filename
    try:
        tbl = await asyncio.to_thread(read_table, fp, sheet or None, int(header_row), 5)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Tabelle nicht lesbar: {e}")
    if tbl.get("error"):
        raise HTTPException(status_code=400, detail=tbl["error"])
    full = await asyncio.to_thread(read_table, fp, tbl.get("sheet") or None, int(header_row), None)
    return {
        "file_id": fid,
        "filename": fname,
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


_CELL_SYSTEM = (
    "Du vergleichst den Inhalt EINER Tabellenzelle in zwei Versionen (A und B). "
    "Beschreibe auf Deutsch in EINEM knappen Satz, WAS sich inhaltlich geändert hat "
    "(z. B. andere Zahl/Einheit, ergänzter/entfernter Text, Umformulierung, Tippfehler). "
    "Stütze dich AUSSCHLIESSLICH auf die beiden Texte — erfinde nichts, spekuliere nicht "
    "über Ursachen. Keine Einleitung, nur der Unterschied."
)


def _cmp_results_path(name: str) -> Optional[Path]:
    """Pfad zur inkrementellen Ergebnisdatei (nur wenn ein Name gesetzt ist)."""
    safe = "".join(c for c in str(name or "") if c.isalnum() or c in (" ", "_", "-")).strip()
    if not safe or safe.startswith("_"):
        return None
    d = COMPARE_DIR / safe
    d.mkdir(parents=True, exist_ok=True)
    return d / "results.json"


def _cmp_load_results(name: str) -> dict:
    p = _cmp_results_path(name)
    if p and p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


@router.post("/api/compare/run-cells")
async def compare_run_cells(req: Request):
    """Zellenweiser, mehrstufiger Vergleich (SSE). Pro Zelle zuerst ein billiger
    Logikvergleich (tools.tablediff.logic_verdict); nur bei Unterschied UND Spalten-Modus
    ``logic_llm`` eine LLM-Bewertung mit **frischem Kontext je Zelle** (2-Nachrichten-Prompt).
    Ergebnis wird — bei gesetztem ``name`` — laufend nach results.json geschrieben; ein
    erneuter Lauf mit ``resume:true`` überspringt bereits bewertete (Schlüssel,Spalte)-Paare."""
    body = await req.json()

    async def gen():
        from tools.files import read_table
        from tools import tablediff as _td
        fa = str(body.get("file_id_a", "")).strip()
        fb = str(body.get("file_id_b", "")).strip()
        pa, pb = UPLOADS_DIR / fa, UPLOADS_DIR / fb
        if not fa or not fb or pa.parent != UPLOADS_DIR or pb.parent != UPLOADS_DIR \
                or not pa.exists() or not pb.exists():
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

        # Spalten-Konfig: name → (mode, metric). Unkonfigurierte gemeinsame Spalten = logic.
        cfg = {}
        for c in (body.get("columns") or []):
            nm = str((c or {}).get("name", "")).strip()
            if not nm:
                continue
            cfg[nm] = (str((c or {}).get("mode", "logic")).strip() or "logic",
                       str((c or {}).get("metric", "nospace")).strip() or "nospace")
        used_cols = [n for n, (m, _) in cfg.items() if m != "ignore"] or None

        prep = _td.iter_cell_diffs(ta.get("headers", []), ta.get("rows", []), ka,
                                   tb.get("headers", []), tb.get("rows", []), kb,
                                   columns=used_cols)

        name = str(body.get("name", "")).strip()
        resume = bool(body.get("resume"))
        rpath = _cmp_results_path(name) if name else None
        prev_cells = {}
        if resume and name:
            for cell in (_cmp_load_results(name).get("cells") or []):
                prev_cells[(cell.get("key"), cell.get("column"))] = cell

        model = None
        want_llm = any(m == "logic_llm" for (m, _) in cfg.values())
        if want_llm:
            picked = str(body.get("model", "")).strip()
            model = _pick_model(picked, _model_for("general")) if picked else await _local_model(_model_for("general"))

        yield _sse({"type": "meta",
                    "key_col_a": prep.get("key_col_a", ""), "key_col_b": prep.get("key_col_b", ""),
                    "compared_columns": prep.get("compared_columns", []),
                    "columns_only_a": prep.get("columns_only_a", []),
                    "columns_only_b": prep.get("columns_only_b", []),
                    "only_in_a": prep.get("only_in_a", []), "only_in_b": prep.get("only_in_b", []),
                    "counts": prep.get("counts", {})})

        total = prep.get("counts", {}).get("cells", 0)
        cells_out, done, tok = [], 0, {"in": 0, "out": 0}
        _nc = _profile_num_ctx()

        def _persist(complete: bool):
            if not rpath:
                return
            payload = {
                "version": 1, "name": name,
                "config": {"columns": body.get("columns") or [],
                           "sheet_a": ta.get("sheet", ""), "sheet_b": tb.get("sheet", "")},
                "counts": prep.get("counts", {}),
                "compared_columns": prep.get("compared_columns", []),
                "columns_only_a": prep.get("columns_only_a", []),
                "columns_only_b": prep.get("columns_only_b", []),
                "only_in_a": prep.get("only_in_a", []), "only_in_b": prep.get("only_in_b", []),
                "cells": cells_out, "updated_at": time.time(), "complete": complete,
                "tokens": tok,
            }
            try:
                rpath.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            except Exception:
                pass

        for row in prep.get("keys", []):
            k = row.get("key")
            for cell in row.get("cells", []):
                col = cell.get("column")
                va, vb = cell.get("a", ""), cell.get("b", "")
                done += 1
                # Fortsetzen: bereits vorhandenes (Schlüssel,Spalte)-Ergebnis wiederverwenden.
                cached = prev_cells.get((k, col))
                if cached is not None:
                    cells_out.append(cached)
                    yield _sse({"type": "cell", **cached, "cached": True})
                    continue
                mode, metric = cfg.get(col, ("logic", "nospace"))
                verdict, detail = _td.logic_verdict(va, vb, metric)
                summary = ""
                if verdict == "changed" and mode == "logic_llm" and model:
                    usr = (f"Spalte: {col}\nSchlüssel: {k}\n\n"
                           f"A:\n{str(va)[:4000]}\n\nB:\n{str(vb)[:4000]}\n\n"
                           "Nenne den inhaltlichen Unterschied in einem Satz.")
                    try:
                        async with _model_session(model), httpx.AsyncClient(timeout=180) as client:
                            resp = await _llm.chat(client, {
                                "model": model, "think": False, "stream": False,
                                "messages": [{"role": "system", "content": _CELL_SYSTEM},
                                             {"role": "user", "content": usr}],
                                "options": {"num_ctx": _nc, "num_predict": 160},
                                "keep_alive": KEEP_ALIVE,
                            })
                            resp.raise_for_status()
                            j = resp.json()
                        summary = re.sub(r"<think>.*?</think>", "", str(j.get("message", {}).get("content", "")),
                                         flags=re.DOTALL).strip()
                        ti, to = _llm_tok(j)
                        tok["in"] += ti; tok["out"] += to
                    except httpx.ConnectError:
                        yield _sse({"type": "error", "message": "Ollama nicht erreichbar — läuft der lokale Server?"})
                        _persist(False)
                        return
                    except Exception as e:
                        summary = f"(KI-Bewertung fehlgeschlagen: {e})"
                rec = {"key": k, "column": col, "a": va, "b": vb,
                       "verdict": verdict, "detail": detail, "summary": summary}
                cells_out.append(rec)
                yield _sse({"type": "cell", **rec})
                if done % 20 == 0:
                    _persist(False)
                    yield _sse({"type": "progress", "done": done, "total": total})
        _persist(True)
        yield _sse({"type": "progress", "done": done, "total": total})
        yield _sse({"type": "done", "counts": prep.get("counts", {}), "tokens": tok, "complete": True})

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


@router.get("/api/compare/projects/{name}/results")
async def compare_results(name: str):
    """Zellenweise Ergebnisdatei (results.json) fürs Wieder-Öffnen/JSON-Export."""
    _cmp_dir(name)  # 404, falls es den Vergleich nicht gibt
    return _cmp_load_results(name) or {"cells": [], "complete": False}


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
