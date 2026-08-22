"""Router: Morphologischer Kasten / Zwicky-Box (/api/morph)

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


# ── Morphologischer Kasten (Zwicky-Box) ─────────────────────────────────────────
# KI-gestütztes Ideenfindungs-Raster: Parameter (Zeilen) × Ausprägungen (Werte).
# Eine Lösung = je Parameter eine Ausprägung. Die KI generiert Parameter/
# Ausprägungen, bewertet gewählte Kombinationen (+ schlägt interessante vor) und
# verfeinert einzelne Zellen. Export läuft über bestehende Wege (DOCX/Doku/RAG)
# im Frontend — kein eigener Endpunkt.


def _morph_value_str(v) -> str:
    """Normalisiert eine Ausprägung auf einen lesbaren String. Kleine Modelle
    liefern statt eines Strings manchmal ein verschachteltes Objekt/Listen —
    das wird kompakt als „Schlüssel: Wert · …" geglättet."""
    if isinstance(v, str):
        return v.strip()
    if isinstance(v, dict):
        parts = []
        for k, val in v.items():
            if isinstance(val, (list, tuple)):
                val = ", ".join(str(x) for x in val)
            elif isinstance(val, dict):
                val = "; ".join(f"{a}: {b}" for a, b in val.items())
            parts.append(f"{k}: {val}")
        return " · ".join(parts).strip()
    if isinstance(v, (list, tuple)):
        return ", ".join(_morph_value_str(x) for x in v).strip()
    return str(v).strip()



async def _morph_llm(model: str, system: str, user: str,
                     tok: Optional[dict] = None) -> Optional[dict]:
    """``tok`` (optional): dict {"in","out"}, in das der Tokenverbrauch summiert wird."""
    try:
        async with _model_session(model), httpx.AsyncClient(timeout=180) as client:
            resp = await _llm.chat(client,{
                "model": model,
                "think": False,
                "stream": False,
                "format": "json",
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
            })
            resp.raise_for_status()
            j = resp.json()
            if tok is not None:
                _a, _b = _llm_tok(j)
                tok["in"] += _a
                tok["out"] += _b
            return _parse_llm_json(j.get("message", {}).get("content", ""))
    except Exception:
        return None


async def _morph_sources_context(problem: str, web: bool, rag_collections: list) -> str:
    """Optionaler Inspirationskontext aus Websuche und/oder Wissensdatenbanken für
    die Morph-Generierung. Gibt einen an den User-Prompt anzuhängenden Block zurück
    (oder "" wenn nichts gewählt/gefunden). Die Embeddings der RAG-Suche laufen auf
    CPU (siehe rag.py), daher kein eigenes _model_session nötig — wie im Chat-RAG."""
    parts = []
    problem = (problem or "").strip()
    if web and problem:
        try:
            from tools.search import search_with_sources
            _, txt = await search_with_sources(problem, 5)
            if txt:
                parts.append("Recherche-Ergebnisse (Web):\n" + txt[:2500])
        except Exception:
            pass
    if rag_collections and problem:
        try:
            from tools.rag import query_collections
            hits = await query_collections(rag_collections, problem, top_k_cap=8)
            if hits:
                ctx = "\n\n".join(f"[{h['filename']}]\n{h['text']}" for h in hits)
                parts.append("Auszüge aus den Wissensdatenbanken:\n" + ctx)
        except Exception:
            pass
    if not parts:
        return ""
    return ("\n\nNutze die folgenden Quellen als Inspiration und fachliche Grundlage; "
            "erfinde nichts dazu, was ihnen klar widerspricht:\n\n" + "\n\n".join(parts))


@router.post("/api/morph/generate")
async def morph_generate(req: Request):
    """Erzeugt Parameter (Zeilen) und je Parameter mehrere Ausprägungen (Werte)
    für ein Problem."""
    body = await req.json()
    problem = (body.get("problem") or "").strip()
    if not problem:
        raise HTTPException(status_code=400, detail="Problem fehlt")
    model = _pick_model(body.get("model"), _model_for("general"))
    _ctx = await _morph_sources_context(
        problem, bool(body.get("web")), body.get("rag_collections") or [])
    _tok = {"in": 0, "out": 0}
    data = await _morph_llm(
        model,
        ("Du erstellst einen morphologischen Kasten (Zwicky-Box) für eine "
         "Aufgabenstellung. Bestimme 4–7 unabhängige Parameter (Merkmale, die eine "
         "Lösung beschreiben) und je Parameter 3–5 konkrete Ausprägungen. Jede "
         "Ausprägung ist ein KURZER Text (Stichwort, max. ~6 Wörter) — KEIN Objekt, "
         "keine verschachtelten Felder. Antworte NUR mit JSON: "
         "{\"parameters\":[{\"name\":\"Parameter\",\"values\":"
         "[\"Ausprägung 1\",\"Ausprägung 2\"]}]}"),
        f"Aufgabenstellung:\n{problem}{_ctx}", tok=_tok)
    params = []
    if data:
        for p in (data.get("parameters") or []):
            if isinstance(p, dict) and p.get("name"):
                vals = [s for s in (_morph_value_str(v) for v in (p.get("values") or [])) if s]
                if vals:
                    params.append({"name": str(p["name"]).strip(), "values": vals})
    if not params:
        raise HTTPException(status_code=502, detail="KI lieferte keine verwertbaren Parameter")
    return {"parameters": params, "tokens": _tok}


@router.post("/api/morph/evaluate")
async def morph_evaluate(req: Request):
    """Bewertet eine gewählte Kombination und schlägt interessante Alternativen vor.
    selection = Liste von {parameter, value}."""
    body = await req.json()
    problem = (body.get("problem") or "").strip()
    selection = body.get("selection") or []
    if not problem or not selection:
        raise HTTPException(status_code=400, detail="Problem oder Auswahl fehlt")
    model = _pick_model(body.get("model"), _model_for("general"))
    sel_txt = "\n".join(
        f"- {s.get('parameter','?')}: {s.get('value','?')}"
        for s in selection if isinstance(s, dict))
    params_txt = ""
    if body.get("parameters"):
        params_txt = "\n\nVerfügbare Parameter/Ausprägungen:\n" + "\n".join(
            f"- {p.get('name','?')}: {', '.join(p.get('values', []))}"
            for p in body["parameters"] if isinstance(p, dict))
    _tok = {"in": 0, "out": 0}
    data = await _morph_llm(
        model,
        ("Du bewertest eine Lösungskombination aus einem morphologischen Kasten. "
         "Gib eine Gesamtbewertung (score 0–100), Einschätzungen zu Machbarkeit und "
         "Innovationsgrad (jeweils 0–100), eine kurze Begründung und Risiken. "
         "Schlage außerdem bis zu drei interessante alternative Kombinationen vor. "
         "Antworte NUR mit JSON: {\"score\":0,\"machbarkeit\":0,\"innovation\":0,"
         "\"begruendung\":\"…\",\"risiken\":[\"…\"],\"vorschlaege\":[{\"picks\":"
         "[{\"parameter\":\"…\",\"value\":\"…\"}],\"score\":0,\"begruendung\":\"…\"}]}"),
        f"Aufgabenstellung:\n{problem}\n\nGewählte Kombination:\n{sel_txt}{params_txt}", tok=_tok)
    if not data:
        raise HTTPException(status_code=502, detail="KI-Bewertung fehlgeschlagen")
    return {
        "score": data.get("score"),
        "machbarkeit": data.get("machbarkeit"),
        "innovation": data.get("innovation"),
        "begruendung": (data.get("begruendung") or "").strip(),
        "risiken": [str(r) for r in (data.get("risiken") or [])],
        "vorschlaege": data.get("vorschlaege") or [],
        "tokens": _tok,
    }


@router.post("/api/morph/refine-cell")
async def morph_refine_cell(req: Request):
    """Verfeinert eine einzelne Zelle: ausformulieren (expand) oder
    Alternativen/Kritik (critique)."""
    body = await req.json()
    problem = (body.get("problem") or "").strip()
    parameter = (body.get("parameter") or "").strip()
    value = (body.get("value") or "").strip()
    action = (body.get("action") or "expand").strip().lower()
    if not value:
        raise HTTPException(status_code=400, detail="Ausprägung fehlt")
    model = _pick_model(body.get("model"), _model_for("general"))
    _ctx = await _morph_sources_context(
        problem, bool(body.get("web")), body.get("rag_collections") or [])
    if action == "critique":
        system = ("Du kritisierst eine Ausprägung in einem morphologischen Kasten und "
                  "schlägst bessere/zusätzliche Alternativen vor. Antworte NUR mit JSON: "
                  "{\"text\":\"kurze Kritik\",\"alternativen\":[\"…\"]}")
    else:
        system = ("Du formulierst eine Ausprägung in einem morphologischen Kasten "
                  "konkreter und anschaulicher aus (1–3 Sätze). Antworte NUR mit JSON: "
                  "{\"text\":\"…\",\"alternativen\":[]}")
    _tok = {"in": 0, "out": 0}
    data = await _morph_llm(
        model, system,
        f"Aufgabenstellung:\n{problem}\n\nParameter: {parameter}\nAusprägung: {value}{_ctx}",
        tok=_tok)
    if not data:
        raise HTTPException(status_code=502, detail="KI-Verfeinerung fehlgeschlagen")
    return {"text": (data.get("text") or "").strip(),
            "alternativen": [str(a) for a in (data.get("alternativen") or [])],
            "tokens": _tok}


@router.post("/api/morph/ideas")
async def morph_ideas(req: Request):
    """Erzeugt mehrere KREATIVE Konzept-Ideen (je eine Ausprägung pro Parameter)
    zum Durchwischen. Optional über Web/Wissensdatenbanken inspiriert."""
    body = await req.json()
    problem = (body.get("problem") or "").strip()
    if not problem:
        raise HTTPException(status_code=400, detail="Problem fehlt")
    model = _pick_model(body.get("model"), _model_for("general"))
    n = max(1, min(8, int(body.get("n") or 5)))
    params = body.get("parameters") or []
    params_txt = ""
    if params:
        params_txt = "\n\nParameter und mögliche Ausprägungen:\n" + "\n".join(
            f"- {p.get('name','?')}: {', '.join(_morph_value_str(v) for v in (p.get('values') or []))}"
            for p in params if isinstance(p, dict))
    _ctx = await _morph_sources_context(
        problem, bool(body.get("web")), body.get("rag_collections") or [])
    _tok = {"in": 0, "out": 0}
    data = await _morph_llm(
        model,
        (f"Du erzeugst {n} KREATIVE, deutlich unterschiedliche Lösungsideen für eine "
         "Aufgabenstellung auf Basis eines morphologischen Kastens. Jede Idee wählt je "
         "Parameter genau EINE Ausprägung (nutze die vorgegebenen, wenn vorhanden, sonst "
         "passende eigene) und bekommt einen kurzen, prägnanten Konzepttitel/-satz. Wage "
         "auch ungewöhnliche, originelle Kombinationen. Antworte NUR mit JSON: "
         "{\"ideen\":[{\"concept\":\"kurzer Konzepttext\",\"picks\":"
         "[{\"parameter\":\"…\",\"value\":\"…\"}]}]}"),
        f"Aufgabenstellung:\n{problem}{params_txt}{_ctx}", tok=_tok)
    ideen = []
    if data:
        for it in (data.get("ideen") or []):
            if not isinstance(it, dict):
                continue
            picks = []
            for pk in (it.get("picks") or []):
                if isinstance(pk, dict) and pk.get("parameter"):
                    picks.append({"parameter": str(pk["parameter"]).strip(),
                                  "value": _morph_value_str(pk.get("value"))})
            concept = _morph_value_str(it.get("concept"))
            if picks or concept:
                ideen.append({"concept": concept, "picks": picks})
    if not ideen:
        raise HTTPException(status_code=502, detail="KI lieferte keine Ideen")
    return {"ideen": ideen, "tokens": _tok}


# ── Morph-Trainingsfile (Backend, automatisch generiert) ──────────────────────
# Gute/schlechte Ideen sammeln sich fortlaufend je Thema unter
# data/morph_training/<slug>.jsonl. Quellen: Wischtechnik, gelöschte ausformulierte
# Karten (= „schlecht"), gemerkte Lösungen (= „gut"). Pro Zeile sowohl strukturiert
# als auch im Chat-Format (messages) zum Finetunen.
# MORPH_TRAIN_DIR → core (Backup nutzt es)


def _morph_train_path(problem: str) -> Path:
    slug = _to_slug((problem or "").strip()) or "allgemein"
    return MORPH_TRAIN_DIR / f"{slug}.jsonl"


@router.post("/api/morph/training/add")
async def morph_training_add(req: Request):
    body = await req.json()
    problem = (body.get("problem") or "").strip()
    label = (body.get("label") or "").strip().lower()
    if label not in ("good", "bad"):
        raise HTTPException(status_code=400, detail="label muss 'good' oder 'bad' sein")
    idea = body.get("idea") or {}
    picks = [p for p in (idea.get("picks") or []) if isinstance(p, dict)]
    concept = str(idea.get("concept") or "").strip()
    reason = str(body.get("reason") or "").strip()
    source = str(body.get("source") or "swipe").strip()
    evaluation = body.get("evaluation") or None
    combo = "\n".join(f"- {p.get('parameter','?')}: {p.get('value','?')}" for p in picks)
    user_txt = f"Aufgabe: {problem or '—'}"
    if concept:
        user_txt += f"\nIdee: {concept}"
    if combo:
        user_txt += f"\nKombination:\n{combo}"
    urteil = "GUT — geeignete Idee." if label == "good" else "SCHLECHT — ungeeignete Idee."
    assistant_txt = urteil + (f"\nBegründung: {reason}" if reason else "")
    rec = {
        "problem": problem, "label": label, "reason": reason, "source": source,
        "idea": {"concept": concept, "picks": picks}, "evaluation": evaluation,
        "ts": time.time(),
        "messages": [
            {"role": "user", "content": user_txt},
            {"role": "assistant", "content": assistant_txt},
        ],
    }
    MORPH_TRAIN_DIR.mkdir(parents=True, exist_ok=True)
    path = _morph_train_path(problem)
    async with aiofiles.open(path, "a", encoding="utf-8") as f:
        await f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    n = 0
    async with aiofiles.open(path, "r", encoding="utf-8") as f:
        async for _line in f:
            if _line.strip():
                n += 1
    return {"ok": True, "count": n, "file": path.name}


@router.get("/api/morph/training")
async def morph_training_get(problem: str = "", format: str = "jsonl"):
    path = _morph_train_path(problem)
    recs = []
    if path.exists():
        async with aiofiles.open(path, "r", encoding="utf-8") as f:
            async for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    recs.append(json.loads(line))
                except Exception:
                    pass
    if format == "md":
        def _fmt(r):
            idea = r.get("idea") or {}
            picks = [p for p in (idea.get("picks") or []) if isinstance(p, dict)]
            combo = ", ".join(f"{p.get('parameter','?')}: {p.get('value','?')}" for p in picks)
            head = (idea.get("concept") or combo or "—").strip()
            line = f"- **{head}**"
            if combo and idea.get("concept"):
                line += f" ({combo})"
            if r.get("reason"):
                line += f" — {r['reason']}"
            return line
        good = [r for r in recs if r.get("label") == "good"]
        bad = [r for r in recs if r.get("label") == "bad"]
        md = f"# Trainingsdaten Morphologischer Kasten\n\n**Aufgabe:** {problem or '—'}\n\n"
        md += f"## Gute Ideen ({len(good)})\n\n" + ("\n".join(_fmt(r) for r in good) or "_keine_") + "\n\n"
        md += f"## Schlechte Ideen ({len(bad)})\n\n" + ("\n".join(_fmt(r) for r in bad) or "_keine_") + "\n"
        return Response(md, media_type="text/markdown; charset=utf-8")
    raw = ("\n".join(json.dumps(r, ensure_ascii=False) for r in recs) + "\n") if recs else ""
    return Response(raw, media_type="application/jsonl; charset=utf-8")


@router.delete("/api/morph/training")
async def morph_training_delete(problem: str = ""):
    path = _morph_train_path(problem)
    if path.exists():
        path.unlink()
    return {"ok": True}
