"""Router: Anfrage-Auswertung RFQ (/api/rfq)

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


# ── Anfrage-Auswertung (RFQ) ──────────────────────────────────────────────────
# Große XLS-Anfragen mit vielen Arbeitspaketen: je Paket ein Dispatcher-/Master-
# Aufruf, der die zuständige Fachrolle bestimmt und interessant/Partner/Best-Cost-
# Country bewertet. Robust mit Zwischenspeicherung (data/rfq/{job}.json) + Resume.

_RFQ_SYSTEM = (
    "Du bist Angebots- und Vergabemanager. Du bekommst EIN Arbeitspaket aus einer "
    "großen Anfrage. Bestimme die zuständige Fachrolle/Disziplin (bevorzugt eine aus "
    "der bereitgestellten Kapazitätsliste) und bewerte das Paket nüchtern. "
    "Nutze die Kapazitätsliste (Rollen, Skills, Land) für die Zuordnung und für die "
    "Best-Cost-Country-Einschätzung. Erfinde nichts; bei Unklarheit 'prüfen'. "
    "Antworte NUR mit JSON in genau diesem Format: "
    '{"responsible":"Fachrolle/Disziplin",'
    '"interesting":{"verdict":"ja|nein|pruefen","reason":"kurz"},'
    '"partner":{"needed":true,"type":"Art des Partners oder leer"},'
    '"bcc":{"suitable":true,"region":"Land/Region oder leer","reason":"kurz"}}'
)


def _rfq_job_path(job_id: str) -> Path:
    safe = re.sub(r"[^A-Za-z0-9_-]", "", job_id or "")[:40] or uuid.uuid4().hex[:12]
    return RFQ_DIR / f"{safe}.json"


_RFQ_CUSTOM_SYSTEM = (
    "Du bewertest EIN Arbeitspaket aus einer großen Anfrage anhand einer konkreten "
    "Vorgabe/Frage. Antworte knapp und sachlich, erfinde nichts. "
    'Antworte NUR mit JSON: {"value":"kurze Antwort/Einordnung (wenige Worte)",'
    '"note":"optionale 1-Satz-Begründung"}.'
)


def _rfq_agent_prompt(agent_id: str) -> str:
    """System-Prompt eines Agenten anhand seiner ID (für agentenbasierte Spalten)."""
    if not agent_id:
        return ""
    fp = _agent_path_by_id(agent_id)
    if not fp:
        return ""
    try:
        return str((json.loads(fp.read_text(encoding="utf-8")) or {}).get("system_prompt", "")).strip()
    except Exception:
        return ""


def _sanitize_rfq_columns(cols) -> list:
    """Eigene Bewertungsspalten säubern: max. 6, je {key,name,prompt?,agent_id?}."""
    out = []
    for c in (cols or []):
        if not isinstance(c, dict):
            continue
        name = str(c.get("name", "")).strip()[:40]
        raw_key = str(c.get("key", "")).strip() or name
        key = re.sub(r"[^a-z0-9_]", "", raw_key.lower())[:24]
        if not key or not name:
            continue
        # Spalte braucht eine Vorgabe: freier Prompt ODER ein Agent
        if not str(c.get("prompt", "")).strip() and not str(c.get("agent_id", "")).strip():
            continue
        # Doppelte Keys eindeutig machen
        if any(o["key"] == key for o in out):
            key = (key + "_" + uuid.uuid4().hex[:3])[:24]
        out.append({
            "key": key, "name": name,
            "prompt": str(c.get("prompt", "")).strip()[:1000],
            "agent_id": str(c.get("agent_id", "")).strip()[:64],
        })
        if len(out) >= 6:
            break
    return out




def _rfq_task_max() -> int:
    """Max. Zeichen je Arbeitspaket-Text — skaliert mit dem Kontextfenster (statt fest 4000).
    So wird ein langes Paket nicht künstlich beschnitten, wenn das Fenster groß genug ist."""
    return max(4000, int(_profile_num_ctx() * 3.5 * 0.4))


def _rfq_cap_max() -> int:
    """Max. Zeichen der Kapazitätsliste je Auswertung, damit eine sehr große (vereinigte)
    Ressourcenliste nicht jeden einzelnen Aufruf dominiert/das Fenster sprengt."""
    return max(2000, int(_profile_num_ctx() * 3.5 * 0.25))


async def _rfq_eval_custom(client, model: str, task: str, columns: list):
    """Wertet je eigener Spalte EIN Arbeitspaket aus → ({key: {value, note}}, tok_in, tok_out).
    Pro Spalte ein LLM-Aufruf (Agent-Persona oder freier Prompt als Vorgabe)."""
    out = {}
    tin = tout = 0
    for col in columns:
        key = col.get("key")
        if not key:
            continue
        instr = (col.get("prompt") or "").strip()
        persona = _rfq_agent_prompt(col.get("agent_id") or "")
        _tmax = _rfq_task_max()
        if persona:
            sys = persona + '\n\nAntworte NUR mit JSON: {"value":"kurze Antwort","note":"1-Satz-Begründung"}.'
            usr = (f"Arbeitspaket:\n{task[:_tmax]}"
                   + (f"\n\nZusätzliche Vorgabe: {instr}" if instr else ""))
        else:
            sys = _RFQ_CUSTOM_SYSTEM
            usr = (f"Vorgabe/Frage für die Spalte „{col.get('name', '')}\":\n"
                   f"{instr or col.get('name', '')}\n\nArbeitspaket:\n{task[:_tmax]}")
        try:
            async with _model_session(model):
                resp = await _llm.chat(client, {
                    "model": model, "think": False, "stream": False, "format": "json",
                    "messages": [{"role": "system", "content": sys},
                                 {"role": "user", "content": usr}],
                    "options": {"num_ctx": _profile_num_ctx()}, "keep_alive": KEEP_ALIVE,
                })
                resp.raise_for_status()
            _j = resp.json()
            _ti, _to = _llm_tok(_j)
            tin += _ti; tout += _to
            d = _parse_llm_json(_j.get("message", {}).get("content", "")) or {}
            out[key] = {"value": str(d.get("value", "")).strip()[:200],
                        "note": str(d.get("note", "")).strip()[:300]}
        except Exception as e:
            out[key] = {"value": "(Fehler)", "note": str(e)[:120]}
    return out, tin, tout


async def _rfq_eval_one(client, model: str, task: str, capacity_ctx: str,
                        web: bool, rag_collections: list,
                        custom_columns: list = None) -> dict:
    """Wertet EIN Arbeitspaket aus → strukturiertes Ergebnis-dict."""
    grounding = []
    if web and task.strip():
        try:
            from tools.search import search_with_sources
            _, txt = await search_with_sources(task[:200], 4)
            if txt:
                grounding.append("Websuche:\n" + txt[:1800])
        except Exception:
            pass
    if rag_collections:
        try:
            from tools.rag import query_collections
            hits = await query_collections(rag_collections, task[:500], top_k_cap=4)
            if hits:
                grounding.append("Wissensdatenbank:\n" + "\n".join(
                    h.get("text", "") for h in hits)[:1800])
        except Exception:
            pass
    user = ""
    if capacity_ctx:
        user += f"Kapazitätsliste (verfügbare Rollen/Partner):\n{capacity_ctx[:_rfq_cap_max()]}\n\n"
    if grounding:
        user += "\n\n".join(grounding) + "\n\n"
    user += f"Arbeitspaket:\n{task[:_rfq_task_max()]}"
    # _model_session serialisiert lokale Generierungen (VRAM-Lock) und ist für
    # Remote-Modelle ein No-op → mehrere Remote-Aufrufe laufen echt parallel.
    async with _model_session(model):
        resp = await _llm.chat(client, {
            "model": model,
            "think": False,
            "stream": False,
            "format": "json",
            "messages": [
                {"role": "system", "content": _RFQ_SYSTEM},
                {"role": "user", "content": user},
            ],
            "options": {"num_ctx": _profile_num_ctx()},
            "keep_alive": KEEP_ALIVE,
        })
        resp.raise_for_status()
    _j = resp.json()
    _ti, _to = _llm_tok(_j)
    data = _parse_llm_json(_j.get("message", {}).get("content", "")) or {}
    inter = data.get("interesting") or {}
    partner = data.get("partner") or {}
    bcc = data.get("bcc") or {}
    result = {
        "responsible": str(data.get("responsible", "")).strip(),
        "interesting": str(inter.get("verdict", "")).strip().lower(),
        "interesting_reason": str(inter.get("reason", "")).strip(),
        "partner_needed": bool(partner.get("needed")),
        "partner_type": str(partner.get("type", "")).strip(),
        "bcc_suitable": bool(bcc.get("suitable")),
        "bcc_region": str(bcc.get("region", "")).strip(),
        "bcc_reason": str(bcc.get("reason", "")).strip(),
    }
    if custom_columns:
        cres, cti, cto = await _rfq_eval_custom(client, model, task, custom_columns)
        result["custom"] = cres
        _ti += cti; _to += cto
    # Token-Verbrauch für den Sitzungszähler (wird in gen() summiert, vor dem Streamen entfernt)
    result["__tok"] = {"in": _ti, "out": _to}
    return result


@router.post("/api/rfq/ask")
async def rfq_ask(req: Request):
    """Freie Rückfrage zur ausgewerteten Anfrage (Chat-Zeile im Anfrage-Tab). Ein
    LLM-Aufruf mit einer kompakten Zusammenfassung der Auswertung als Kontext."""
    body = await req.json()
    question = str(body.get("question", "")).strip()
    if not question:
        raise HTTPException(status_code=400, detail="Keine Frage angegeben")
    context = str(body.get("context", "")).strip()[:max(9000, int(_profile_num_ctx() * 3.5 * 0.6))]
    model = _pick_model(body.get("model"), _model_for("general"))
    sys = (
        "Du bist Angebots- und Vergabeassistent. Beantworte die Frage des Nutzers zur "
        "ausgewerteten Anfrage knapp, konkret und auf Deutsch — möglichst nur auf Basis "
        "der bereitgestellten Auswertung. Fehlt eine Information, sage das."
    )
    usr = (f"Auswertung (Auszug):\n{context}\n\n" if context else "") + f"Frage: {question}"
    try:
        async with _model_session(model), httpx.AsyncClient(timeout=180) as client:
            resp = await _llm.chat(client, {
                "model": model, "think": False, "stream": False,
                "messages": [{"role": "system", "content": sys},
                             {"role": "user", "content": usr}],
                "options": {"num_ctx": _profile_num_ctx()}, "keep_alive": KEEP_ALIVE,
            })
            resp.raise_for_status()
        _j = resp.json()
        answer = str(_j.get("message", {}).get("content", "")).strip()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Anfrage-Chat fehlgeschlagen: {e}")
    _ti, _to = _llm_tok(_j)
    return {"answer": answer or "(keine Antwort)", "tokens": {"in": _ti, "out": _to}}


@router.post("/api/rfq/preview")
async def rfq_preview(file: UploadFile = File(...), sheet: str = Form(""),
                      header_row: int = Form(0)):
    """Lädt die Anfrage-Datei hoch und liefert Blätter/Spalten/Beispielzeilen zur
    Spaltenzuordnung zurück."""
    from tools.files import read_table
    fid = f"rfq_{uuid.uuid4().hex[:8]}_{file.filename}"
    fp = UPLOADS_DIR / fid
    fp.write_bytes(await file.read())
    try:
        tbl = await asyncio.to_thread(read_table, fp, sheet or None, int(header_row), 5)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Tabelle nicht lesbar: {e}")
    if tbl.get("error"):
        raise HTTPException(status_code=400, detail=tbl["error"])
    # Gesamtzeilenzahl (ohne 5er-Limit) separat bestimmen
    full = await asyncio.to_thread(read_table, fp, tbl.get("sheet") or None, int(header_row), None)
    return {
        "file_id": fid,
        "sheets": tbl.get("sheets", []),
        "sheet": tbl.get("sheet", ""),
        "headers": tbl.get("headers", []),
        "sample_rows": tbl.get("rows", []),
        "n_rows": len(full.get("rows", [])),
    }


@router.post("/api/rfq/evaluate")
async def rfq_evaluate(req: Request):
    body = await req.json()
    file_id = str(body.get("file_id", "")).strip()
    fp = UPLOADS_DIR / file_id
    if not file_id or not fp.exists():
        raise HTTPException(status_code=404, detail="Datei nicht gefunden — bitte erneut hochladen")
    sheet = str(body.get("sheet", "")).strip() or None
    header_row = int(body.get("header_row", 0) or 0)
    task_col = int(body.get("task_col", -1))
    id_col = body.get("id_col")
    title_col = body.get("title_col")
    model = _pick_model(body.get("model"), _model_for("general"))
    web = bool(body.get("web_search"))
    rag_collections = body.get("rag_collections") or []
    custom_columns = _sanitize_rfq_columns(body.get("custom_columns"))
    limit = body.get("limit")
    job_id = str(body.get("job_id", "")).strip() or uuid.uuid4().hex[:12]
    resume = bool(body.get("resume"))
    # Parallelität nur für Remote-Modelle (externe API) — lokal bremst der VRAM-Lock
    # ohnehin auf 1. Client darf override liefern; sonst Default 6 (remote) / 1 (lokal).
    _remote = _llm.is_remote(model)
    try:
        _req_conc = int(body.get("concurrency", 0))
    except (TypeError, ValueError):
        _req_conc = 0
    concurrency = max(1, min(_req_conc or 6, 12)) if _remote else 1

    async def gen():
        from tools.files import read_table
        tbl = await asyncio.to_thread(read_table, fp, sheet, header_row, None)
        rows = tbl.get("rows", [])
        headers = tbl.get("headers", [])
        if task_col < 0 or task_col >= len(headers):
            yield _sse({"type": "error", "message": "Keine gültige Aufgaben-Spalte gewählt"})
            return
        if isinstance(limit, int) and limit > 0:
            rows = rows[:limit]
        capacity_ctx = _capacity_context()

        # Job-Datei laden (Resume) oder neu anlegen
        jpath = _rfq_job_path(job_id)
        done: dict = {}
        if resume and jpath.exists():
            try:
                done = (json.loads(jpath.read_text(encoding="utf-8")) or {}).get("results", {})
            except Exception:
                done = {}

        def _cell(row, idx):
            try:
                return row[int(idx)] if idx is not None and int(idx) >= 0 else ""
            except (TypeError, ValueError, IndexError):
                return ""

        _EMPTY = {"responsible": "", "interesting": "", "interesting_reason": "(leer)",
                  "partner_needed": False, "partner_type": "", "bcc_suitable": False,
                  "bcc_region": "", "bcc_reason": ""}

        async def _one(i, row, client):
            """Liefert (i, rid, title, task, cells, result, is_new)."""
            task = str(_cell(row, task_col)).strip()
            rid = str(_cell(row, id_col)).strip() if id_col is not None else ""
            title = str(_cell(row, title_col)).strip() if title_col is not None else ""
            key = str(i)
            if key in done:
                return i, rid, title, task, list(row), done[key], False
            if not task:
                return i, rid, title, task, list(row), dict(_EMPTY), False
            try:
                result = await _rfq_eval_one(client, model, task, capacity_ctx, web,
                                             rag_collections, custom_columns)
            except Exception as e:
                result = {"responsible": "", "interesting": "fehler",
                          "interesting_reason": str(e)[:200], "partner_needed": False,
                          "partner_type": "", "bcc_suitable": False, "bcc_region": "",
                          "bcc_reason": ""}
            return i, rid, title, task, list(row), result, True

        total = len(rows)
        yield _sse({"type": "start", "job_id": job_id, "total": total,
                    "headers": headers, "concurrency": concurrency, "remote": _remote,
                    "custom_columns": custom_columns})
        counts = {"interesting": 0, "partner": 0, "bcc": 0}
        tok_total = {"in": 0, "out": 0}
        indexed = list(enumerate(rows))
        async with httpx.AsyncClient(timeout=300) as client:
            # In Blöcken der Größe `concurrency` abarbeiten (remote echt parallel,
            # lokal = 1). Reihenfolge der Ausgabe bleibt erhalten; pro Block persistieren.
            for bs in range(0, total, concurrency):
                batch = indexed[bs:bs + concurrency]
                done_batch = await asyncio.gather(*(_one(i, row, client) for i, row in batch))
                dirty = False
                for i, rid, title, task, cells, result, is_new in done_batch:
                    # Token-Verbrauch herausziehen (nicht streamen/persistieren)
                    _tk = result.pop("__tok", None) if isinstance(result, dict) else None
                    if _tk:
                        tok_total["in"] += int(_tk.get("in") or 0)
                        tok_total["out"] += int(_tk.get("out") or 0)
                    if is_new:
                        done[str(i)] = result
                        dirty = True
                    if result.get("interesting") == "ja":
                        counts["interesting"] += 1
                    if result.get("partner_needed"):
                        counts["partner"] += 1
                    if result.get("bcc_suitable"):
                        counts["bcc"] += 1
                    yield _sse({"type": "row", "index": i, "id": rid, "title": title,
                                "task": task, "result": result, "cells": cells,
                                "pct": int((i + 1) / total * 100) if total else 100})
                if dirty:
                    try:
                        jpath.write_text(json.dumps({"job_id": job_id, "results": done},
                                                    ensure_ascii=False), encoding="utf-8")
                    except Exception:
                        pass
        yield _sse({"type": "done", "job_id": job_id, "summary": {"n": total, **counts},
                    "tokens": tok_total})

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ── RFQ → Planer-Übergabe ─────────────────────────────────────────────────────
# Ausgewählte (interessante) Tickets gesamthaft in EINEN Plan überführen. Da RFQ
# keine Stunden liefert, schätzt das LLM bei der Übergabe Aufwand + Dauer je Ticket
# (nur für die Teilmenge). Die „Zuständige Rolle" wird zur Ressource; Kosten/Auslastung
# rechnet der Planer anschließend gegen die globale Kapazitätsliste.

_RFQ_ESTIMATE_SYSTEM = (
    "Du bist Projektkalkulator. Schätze für EIN Arbeitspaket realistisch den Aufwand in "
    "Personenstunden (hours) und die Dauer in Arbeitstagen (duration_days) für die genannte "
    "Rolle. Sei nüchtern; bei Unklarheit konservativ. "
    'Antworte NUR mit JSON: {"hours":Zahl,"duration_days":Zahl}.'
)


async def _rfq_estimate_one(client, model: str, task: str, role: str,
                            tok: Optional[dict] = None) -> dict:
    """Schätzt Aufwand (h) und Dauer (Tage) für ein Ticket. Fallback 8 h / 1 Tag.
    ``tok`` (optional): dict {"in","out"}, in das der Tokenverbrauch summiert wird."""
    usr = f"Rolle: {role or 'unbestimmt'}\n\nArbeitspaket:\n{task[:_rfq_task_max()]}"
    try:
        async with _model_session(model):
            resp = await _llm.chat(client, {
                "model": model, "think": False, "stream": False, "format": "json",
                "messages": [
                    {"role": "system", "content": _RFQ_ESTIMATE_SYSTEM},
                    {"role": "user", "content": usr},
                ],
                "options": {"num_ctx": _profile_num_ctx()}, "keep_alive": KEEP_ALIVE,
            })
            resp.raise_for_status()
        _re_j = resp.json()
        if tok is not None:
            _a, _b = _llm_tok(_re_j)
            tok["in"] += _a
            tok["out"] += _b
        data = _parse_llm_json(_re_j.get("message", {}).get("content", "")) or {}
    except Exception:
        data = {}

    def _num(v, default):
        try:
            n = float(v)
            return n if n > 0 else default
        except (TypeError, ValueError):
            return default
    return {"hours": _num(data.get("hours"), 8.0), "duration_days": _num(data.get("duration_days"), 1.0)}


def _rfq_task_note(rid: str, res: dict) -> str:
    bits = []
    if rid:
        bits.append(f"ID {rid}")
    if res.get("interesting"):
        bits.append(f"interessant: {res['interesting']}")
    if res.get("partner_needed"):
        bits.append(f"Partner: {res.get('partner_type') or 'ja'}")
    if res.get("bcc_suitable"):
        bits.append(f"BCC: {res.get('bcc_region') or 'ja'}")
    return " · ".join(bits)


@router.post("/api/rfq/to-plan")
async def rfq_to_plan(req: Request):
    body = await req.json()
    file_id = str(body.get("file_id", "")).strip()
    fp = UPLOADS_DIR / file_id
    if not file_id or not fp.exists():
        raise HTTPException(status_code=404, detail="Datei nicht gefunden — bitte erneut hochladen")
    job_id = str(body.get("job_id", "")).strip()
    jpath = _rfq_job_path(job_id) if job_id else None
    if not jpath or not jpath.exists():
        raise HTTPException(status_code=400, detail="Keine Auswertung gefunden — bitte zuerst auswerten")
    sheet = str(body.get("sheet", "")).strip() or None
    header_row = int(body.get("header_row", 0) or 0)
    task_col = int(body.get("task_col", -1))
    id_col = body.get("id_col")
    title_col = body.get("title_col")
    model = _pick_model(body.get("model"), _model_for("general"))
    plan_name = str(body.get("plan_name", "")).strip() or "Anfrage-Auswertung"
    selection = body.get("selection", "interesting")
    _remote = _llm.is_remote(model)
    try:
        _req_conc = int(body.get("concurrency", 0))
    except (TypeError, ValueError):
        _req_conc = 0
    concurrency = max(1, min(_req_conc or 6, 12)) if _remote else 1

    async def gen():
        from tools.files import read_table
        try:
            results = (json.loads(jpath.read_text(encoding="utf-8")) or {}).get("results", {})
        except Exception:
            results = {}
        tbl = await asyncio.to_thread(read_table, fp, sheet, header_row, None)
        rows = tbl.get("rows", [])
        headers = tbl.get("headers", [])
        if task_col < 0 or task_col >= len(headers):
            yield _sse({"type": "error", "message": "Keine gültige Aufgaben-Spalte gewählt"})
            return

        def _cell(row, idx):
            try:
                return row[int(idx)] if idx is not None and int(idx) >= 0 else ""
            except (TypeError, ValueError, IndexError):
                return ""

        # Zu übernehmende Zeilen bestimmen
        if isinstance(selection, list):
            sel = [int(i) for i in selection if isinstance(i, int) or str(i).isdigit()]
        else:
            sel = []
            for i in range(len(rows)):
                r = results.get(str(i)) or {}
                if selection == "all":
                    if r:
                        sel.append(i)
                elif (r.get("interesting") or "") == "ja":
                    sel.append(i)
        sel = [i for i in sel if 0 <= i < len(rows)]
        total = len(sel)
        yield _sse({"type": "start", "total": total, "concurrency": concurrency, "remote": _remote})
        if not total:
            yield _sse({"type": "error", "message": "Keine passenden Tickets für die Auswahl"})
            return

        cap_items = _load_capacity()
        _tok = {"in": 0, "out": 0}

        async def _one(n, i, client):
            row = rows[i]
            res = results.get(str(i)) or {}
            task = str(_cell(row, task_col)).strip()
            rid = str(_cell(row, id_col)).strip() if id_col is not None else ""
            title = str(_cell(row, title_col)).strip() if title_col is not None else ""
            role = str(res.get("responsible", "")).strip()
            est = await _rfq_estimate_one(client, model, task, role, tok=_tok)
            cap = _match_catalog(role, cap_items) if role else None
            rate = float((cap or {}).get("rate", 0) or 0)
            name = (title or task[:80] or f"Paket {i + 1}").strip()
            return {
                "id": f"T{n + 1}", "name": name, "duration": round(est["duration_days"], 1),
                "predecessors": [], "successors": [], "resources": "",
                "resource_list": [{"kind": "human", "name": role or "unbestimmt", "qty": 1,
                                   "hours": round(est["hours"], 1), "rate": rate, "lead": 0}],
                "notes": _rfq_task_note(rid, res), "area": role or "Sonstige",
                "is_start": False, "is_end": False,
                "rfq": {"interesting": res.get("interesting", ""),
                        "partner_needed": bool(res.get("partner_needed")),
                        "partner_type": res.get("partner_type", ""),
                        "bcc_suitable": bool(res.get("bcc_suitable")),
                        "bcc_region": res.get("bcc_region", "")},
            }

        tasks = [None] * total
        async with httpx.AsyncClient(timeout=300) as client:
            enum_sel = list(enumerate(sel))
            for bs in range(0, total, concurrency):
                batch = enum_sel[bs:bs + concurrency]
                computed = await asyncio.gather(*(_one(n, i, client) for n, i in batch))
                for t in computed:
                    tasks[int(t["id"][1:]) - 1] = t
                yield _sse({"type": "progress", "done": min(bs + concurrency, total), "total": total})

        catalog = [{"kind": c.get("kind", "human"), "name": c.get("name", ""), "rate": c.get("rate", 0)}
                   for c in cap_items if c.get("name")]
        plan_id = uuid.uuid4().hex[:12]
        plan = {
            "id": plan_id, "name": plan_name,
            "created_at": time.time(), "updated_at": time.time(),
            "tasks": tasks,
            "description": f"Aus Anfrage-Auswertung übernommen ({total} Tickets).",
            "system_prompt": "",
            "resource_catalog": catalog, "resource_mode": "extend",
            "start_date": time.strftime("%Y-%m-%d"), "end_date": "", "workdays": True,
        }
        _plan_path(plan_id, plan_name).write_text(
            json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
        yield _sse({"type": "done", "plan_id": plan_id, "plan_name": plan_name, "n": total,
                    "tokens": _tok})

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
