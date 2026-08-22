"""Router: Jury (Multi-Agent-Bewertung, /api/juries, /api/jury/evaluate)

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


# ── Jury (gespeicherte Bewertungs-Gremien aus Agenten) ──────────────────────────
# Eine Jury bündelt mehrere Agenten (z. B. ⚖️ Gesetz-Agenten). Sie bewertet einen
# beliebigen Text — auch KI-generierten — mit je einem Votum pro Mitglied plus einem
# synthetisierten Gesamturteil. Dateibasiert wie Agenten/Pläne (data/juries/).

@router.get("/api/juries")
async def list_juries():
    out = []
    for fp in sorted(JURIES_DIR.glob("*.json")):
        try:
            out.append(json.loads(fp.read_text(encoding="utf-8")))
        except Exception:
            pass
    return out


@router.post("/api/juries")
async def create_jury(req: Request):
    body = await req.json()
    name = (body.get("name") or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="Name fehlt")
    members = [str(m) for m in (body.get("member_agent_ids") or [])]
    jury = {
        "id": _to_slug(name) + "_" + uuid.uuid4().hex[:6],
        "name": name,
        "description": (body.get("description") or "").strip(),
        "member_agent_ids": members,
        "project_id": (body.get("project_id") or "").strip(),
        "created_at": time.time(),
    }
    fp = JURIES_DIR / f"{_to_slug(name)}_{jury['id'][-6:]}.json"
    fp.write_text(json.dumps(jury, ensure_ascii=False, indent=2), encoding="utf-8")
    return jury


@router.put("/api/juries/{jid}")
async def update_jury(jid: str, req: Request):
    fp = _jury_path_by_id(jid)
    if not fp:
        raise HTTPException(status_code=404, detail="Jury nicht gefunden")
    jury = json.loads(fp.read_text(encoding="utf-8"))
    body = await req.json()
    if "name" in body:
        jury["name"] = (body.get("name") or jury["name"]).strip()
    if "description" in body:
        jury["description"] = (body.get("description") or "").strip()
    if "member_agent_ids" in body:
        jury["member_agent_ids"] = [str(m) for m in (body.get("member_agent_ids") or [])]
    fp.write_text(json.dumps(jury, ensure_ascii=False, indent=2), encoding="utf-8")
    return jury


@router.delete("/api/juries/{jid}")
async def delete_jury(jid: str):
    fp = _jury_path_by_id(jid)
    if fp:
        fp.unlink()
    return {"ok": True}


_JURY_MEMBER_SYSTEM = (
    "Du bewertest einen vorgelegten Text aus deiner Fachperspektive (siehe deine Rolle). "
    "Sei konkret und belege Kritik. Wenn dir Fachgrundlagen (Auszüge aus Gesetzen/Normen/"
    "Wissensdatenbank) eingeblendet sind, prüfe ausschließlich anhand dieser und nenne die "
    "Fundstelle (§/Artikel/Quelle). Erfinde nichts. Antworte NUR mit JSON in genau diesem "
    'Format: {"score":0-100,"befund":"kurzer Gesamtbefund","risiken":["Verstoß/Risiko mit '
    'Fundstelle", "..."],"empfehlung":"konkrete Empfehlung"}'
)

_JURY_SYNTH_SYSTEM = (
    "Du fasst die Einzelvoten einer Bewertungs-Jury zu einem Gesamturteil zusammen. "
    "Gewichte fachlich, hebe Konsens und Streitpunkte hervor. Antworte NUR mit JSON: "
    '{"gesamturteil":"…","score":0-100,"konsens":"…","hauptkritik":["…"],'
    '"empfehlungen":["…"]}'
)

# Map-Schritt fuer sehr lange Dokumente: pro Abschnitt eine kurze Vorab-Analyse.
_JURY_CHUNK_SYSTEM = (
    "Du erhältst EINEN Abschnitt eines längeren Dokuments. Notiere aus deiner "
    "Fachperspektive die wichtigsten Befunde, Risiken/Verstöße (mit Fundstelle, falls "
    "Fachgrundlagen eingeblendet sind) und auffälligen Punkte NUR für diesen Abschnitt. "
    "Maximal 100 Wörter, Stichpunkte. Kein JSON, keine Gesamtwertung."
)


def _chunk_for_ctx(text: str, num_ctx: int, max_chunks: int = 40) -> list:
    """Teilt Text in Abschnitte, die mit Prompt + Ausgabe ins Kontextfenster passen.
    ~3,5 Zeichen/Token (DE), ~50 % des Fensters für den Textabschnitt. Begrenzt die
    Abschnittszahl (notfalls größere Abschnitte), um die Kosten zu deckeln."""
    per = max(4000, int(num_ctx * 3.5 * 0.5))
    need = (len(text) + per - 1) // per
    if need > max_chunks:
        per = (len(text) + max_chunks - 1) // max_chunks
    chunks, i, n = [], 0, len(text)
    while i < n:
        end = min(i + per, n)
        if end < n:  # möglichst an Absatz-/Satzgrenze trennen
            br = text.rfind("\n", i + per // 2, end)
            if br <= i:
                br = text.rfind(". ", i + per // 2, end)
                if br > i:
                    br += 1
            if br > i:
                end = br
        seg = text[i:end].strip()
        if seg:
            chunks.append(seg)
        i = end
    return chunks


@router.post("/api/jury/evaluate")
async def jury_evaluate(req: Request):
    """Bewertet einen Text mit allen Mitgliedern einer Jury (SSE-Stream).
    Body: {jury_id | member_agent_ids[], text, context?, criteria?}.
    Frames: member (pro Votum), summary (Gesamturteil), error, done."""
    body = await req.json()
    text = (body.get("text") or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="Kein Text zum Bewerten")
    context = (body.get("context") or "").strip()
    criteria = (body.get("criteria") or "").strip()

    member_ids = body.get("member_agent_ids") or []
    if not member_ids and body.get("jury_id"):
        jfp = _jury_path_by_id(body["jury_id"])
        if jfp:
            try:
                member_ids = json.loads(jfp.read_text(encoding="utf-8")).get("member_agent_ids", [])
            except Exception:
                member_ids = []
    member_ids = [str(m) for m in member_ids]
    if not member_ids:
        raise HTTPException(status_code=400, detail="Jury hat keine Mitglieder")

    async def _stream():
        verdicts = []
        tok_total = {"in": 0, "out": 0}
        for aid in member_ids:
            agent = _load_agent_dict(aid)
            if not agent:
                continue
            aname = agent.get("name", aid)
            aicon = agent.get("icon", "⚖️")
            yield _sse({"type": "member", "status": "start", "agent": aname, "icon": aicon})

            # Fachgrundlagen aus gebundenen Wissensdatenbanken (z. B. Gesetzestext) ziehen
            rag_ctx = ""
            rag_ids = agent.get("rag_collections") or []
            if rag_ids:
                try:
                    from tools.rag import query_collections
                    hits = await query_collections(rag_ids, text[:2000])
                    if hits:
                        rag_ctx = "\n\n".join(
                            f"[Quelle {i+1}: {h.get('filename','')}]\n{h.get('text','')}"
                            for i, h in enumerate(hits[:6]))
                except Exception:
                    rag_ctx = ""

            sys_prompt = (agent.get("system_prompt") or "").strip()
            member_sys = (sys_prompt + "\n\n" + _JURY_MEMBER_SYSTEM) if sys_prompt else _JURY_MEMBER_SYSTEM
            base_parts = []
            if context:
                base_parts.append(f"Kontext:\n{context}")
            if criteria:
                base_parts.append(f"Bewertungskriterien:\n{criteria}")
            if rag_ctx:
                base_parts.append(f"Eingeblendete Fachgrundlagen:\n{rag_ctx[:6000]}")

            mdl = _pick_model(agent.get("model"), _model_for("science"))
            num_ctx = _profile_num_ctx()
            # Passt das Dokument in einen Direktdurchlauf? Sonst Map-Reduce über Abschnitte.
            single_max = max(4000, int(num_ctx * 3.5 * 0.5))

            async def _member_call(client, sysmsg, usermsg, as_json):
                payload = {"model": mdl, "think": False, "stream": False,
                           "messages": [{"role": "system", "content": sysmsg},
                                        {"role": "user", "content": usermsg}],
                           "options": {"num_ctx": num_ctx}, "keep_alive": KEEP_ALIVE}
                if as_json:
                    payload["format"] = "json"
                resp = await _llm.chat(client, payload)
                resp.raise_for_status()
                j = resp.json()
                ti, to = _llm_tok(j)
                return j.get("message", {}).get("content", ""), ti, to

            data = None
            notes = None   # Abschnitts-Befunde (nur im Map-Reduce-Pfad gesetzt)
            try:
                async with _model_session(mdl), httpx.AsyncClient(timeout=300) as client:
                    if len(text) <= single_max:
                        up = "\n\n".join(base_parts + [f"Zu bewertender Text:\n{text}"])
                        content, ti, to = await _member_call(client, member_sys, up, True)
                        tok_total["in"] += ti; tok_total["out"] += to
                        data = _parse_llm_json(content)
                    else:
                        # Map: jeden Abschnitt vorab analysieren
                        chunks = _chunk_for_ctx(text, num_ctx)
                        chunk_sys = (sys_prompt + "\n\n" + _JURY_CHUNK_SYSTEM) if sys_prompt else _JURY_CHUNK_SYSTEM
                        notes = []
                        for ci, ch in enumerate(chunks):
                            yield _sse({"type": "member", "status": "progress", "agent": aname,
                                        "icon": aicon, "chunk": ci + 1, "chunks": len(chunks)})
                            up = "\n\n".join(base_parts + [f"Dokument-Abschnitt {ci+1}/{len(chunks)}:\n{ch}"])
                            try:
                                content, ti, to = await _member_call(client, chunk_sys, up, False)
                                tok_total["in"] += ti; tok_total["out"] += to
                                if content.strip():
                                    notes.append(f"[Abschnitt {ci+1}] {content.strip()}")
                            except Exception:
                                pass
                        # Reduce: Gesamtvotum aus den Abschnitts-Befunden
                        joined = "\n\n".join(notes)[:int(num_ctx * 3.0)]
                        up = "\n\n".join(base_parts + [
                            f"Das Dokument ist sehr lang und wurde abschnittsweise vorab-analysiert "
                            f"({len(chunks)} Abschnitte). Abschnitts-Befunde:\n{joined}\n\n"
                            "Erstelle daraus dein abschließendes Gesamtvotum zum gesamten Dokument."])
                        content, ti, to = await _member_call(client, member_sys, up, True)
                        tok_total["in"] += ti; tok_total["out"] += to
                        data = _parse_llm_json(content)
            except Exception as e:
                yield _sse({"type": "member", "status": "error", "agent": aname,
                            "icon": aicon, "message": str(e)})
                continue

            verdict = {
                "agent": aname, "icon": aicon,
                "score": (data or {}).get("score"),
                "befund": ((data or {}).get("befund") or "").strip(),
                "risiken": [str(r) for r in ((data or {}).get("risiken") or [])],
                "empfehlung": ((data or {}).get("empfehlung") or "").strip(),
            }
            # Fallback: lieferte die (Reduce-)Wertung kein verwertbares JSON, aber es gibt
            # Abschnitts-Befunde, dann diese als Befund/Risiken zeigen (statt leerer Karte).
            if not verdict["befund"] and not verdict["risiken"] and notes:
                verdict["befund"] = ("Automatische Gesamtwertung war unsicher — "
                                     "abschnittsweise Befunde des großen Dokuments:")
                verdict["risiken"] = notes[:12]
            verdicts.append(verdict)
            yield _sse({"type": "member", "status": "done", **verdict})

        if not verdicts:
            yield _sse({"type": "error", "message": "Kein Mitglied lieferte ein Votum."})
            yield _sse({"type": "done"})
            return

        # Synthese / Gesamturteil
        votes_txt = "\n\n".join(
            f"## {v['agent']} (Score {v['score']})\nBefund: {v['befund']}\n"
            f"Risiken: {'; '.join(v['risiken'])}\nEmpfehlung: {v['empfehlung']}"
            for v in verdicts)
        gmodel = _model_for("general")
        try:
            async with _model_session(gmodel), httpx.AsyncClient(timeout=180) as client:
                resp = await _llm.chat(client, {
                    "model": gmodel, "think": False, "stream": False, "format": "json",
                    "options": {"num_ctx": _profile_num_ctx()}, "keep_alive": KEEP_ALIVE,
                    "messages": [
                        {"role": "system", "content": _JURY_SYNTH_SYSTEM},
                        {"role": "user", "content": f"Einzelvoten der Jury:\n\n{votes_txt}"},
                    ],
                })
                resp.raise_for_status()
                _sj = resp.json()
                _sti, _sto = _llm_tok(_sj)
                tok_total["in"] += _sti; tok_total["out"] += _sto
                synth = _parse_llm_json(_sj.get("message", {}).get("content", "")) or {}
        except Exception:
            synth = {}
        # Fallback-Gesamtscore: Mittelwert der Einzel-Scores
        scores = [v["score"] for v in verdicts if isinstance(v["score"], (int, float))]
        avg = round(sum(scores) / len(scores)) if scores else None
        yield _sse({"type": "summary",
                    "gesamturteil": (synth.get("gesamturteil") or "").strip(),
                    "score": synth.get("score", avg),
                    "konsens": (synth.get("konsens") or "").strip(),
                    "hauptkritik": [str(x) for x in (synth.get("hauptkritik") or [])],
                    "empfehlungen": [str(x) for x in (synth.get("empfehlungen") or [])]})
        yield _sse({"type": "done", "tokens": tok_total})

    return StreamingResponse(_stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
