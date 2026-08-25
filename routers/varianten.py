"""Router: Variantenvergleich (AHP-Hybrid, Tab + Chat /paarvergleich)

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



# ══════════════════════════════════════════════════════════════════════════════
# Variantenvergleich (gewichtete Entscheidung, AHP-Hybrid)
# ══════════════════════════════════════════════════════════════════════════════
# Persistenz je Vergleich in VARIANTEN_DIR/<name>/decision.json. Die Gewichte
# (Paarvergleich) und das Ranking werden **deterministisch** in tools/decision.py
# gerechnet — nie vom LLM. Das LLM schlägt nur Kriterien/Varianten/Urteile vor.

def _var_safe_name(name: str) -> str:
    safe = "".join(c for c in str(name or "") if c.isalnum() or c in (" ", "_", "-")).strip()
    if not safe or safe.startswith("_"):
        raise HTTPException(status_code=400, detail="Ungültiger Name")
    return safe


def _var_dir(name: str, create: bool = False) -> Path:
    d = VARIANTEN_DIR / _var_safe_name(name)
    if create:
        d.mkdir(parents=True, exist_ok=True)
    elif not d.exists():
        raise HTTPException(status_code=404, detail="Vergleich nicht gefunden")
    return d


def _var_compute(data: dict) -> dict:
    """Deterministische Kennzahlen (Gewichte + Ranking) aus den Rohdaten rechnen."""
    from tools import decision as _dec
    criteria = data.get("criteria") or []
    variants = data.get("variants") or []
    directions = [(c.get("direction") if c.get("direction") in ("benefit", "cost") else "benefit")
                  for c in criteria]
    pairwise = data.get("pairwise") or []
    if pairwise and len(pairwise) == len(criteria) and len(criteria) >= 2:
        pw = _dec.pairwise_weights(pairwise)
        weights = pw["weights"]
    else:
        weights = _dec.equal_weights(len(criteria))
        pw = {"weights": weights, "cr": 0.0, "consistent": True, "lambda_max": float(len(criteria)), "n": len(criteria)}
    ratings = data.get("ratings") or []
    sc = _dec.score_variants(weights, ratings, directions)
    best = sc.get("best")
    result = {
        "weights": weights,
        "cr": pw.get("cr", 0.0),
        "consistent": pw.get("consistent", True),
        "lambda_max": pw.get("lambda_max", 0.0),
        "worst_pair": pw.get("worst_pair"),   # strittigstes Urteil (für „erneut bewerten")
        "scores": sc.get("scores", []),
        "ranking": sc.get("ranking", []),
        "best": best,
        "best_name": (variants[best].get("name") if (best is not None and best < len(variants)) else ""),
    }
    return result


def _var_load(name: str) -> dict:
    p = _var_dir(name) / "decision.json"
    if not p.exists():
        return {"title": name, "description": "", "criteria": [], "variants": [],
                "pairwise": [], "ratings": [], "result": {}}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        data = {}
    data.setdefault("title", name)
    data["result"] = _var_compute(data)
    return data


def _var_save(name: str, body: dict) -> dict:
    d = _var_dir(name, create=True)
    criteria = [{"name": str(c.get("name", "")).strip()[:120],
                 "direction": (c.get("direction") if c.get("direction") in ("benefit", "cost") else "benefit")}
                for c in (body.get("criteria") or []) if str(c.get("name", "")).strip()]
    variants = [{"name": str(v.get("name", "")).strip()[:120],
                 "description": str(v.get("description", "")).strip()[:2000]}
                for v in (body.get("variants") or []) if str(v.get("name", "")).strip()]
    data = {
        "title": str(body.get("title", name)).strip()[:200] or name,
        "description": str(body.get("description", "")).strip()[:4000],
        "criteria": criteria,
        "variants": variants,
        "pairwise": body.get("pairwise") or [],
        "ratings": body.get("ratings") or [],
        "project_id": str(body.get("project_id", "")).strip(),   # optionale Dach-Projekt-Verknüpfung
        "updated_at": time.time(),
    }
    data["result"] = _var_compute(data)
    (d / "decision.json").write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return data


@router.get("/api/varianten/projects")
async def varianten_list():
    out = []
    if VARIANTEN_DIR.exists():
        for d in sorted(VARIANTEN_DIR.iterdir()):
            if not d.is_dir() or d.name.startswith("_"):
                continue
            p = d / "decision.json"
            meta = {}
            if p.exists():
                try:
                    meta = json.loads(p.read_text(encoding="utf-8"))
                except Exception:
                    meta = {}
            res = _var_compute(meta) if meta else {}
            out.append({
                "name": d.name,
                "title": meta.get("title") or d.name,
                "n_criteria": len(meta.get("criteria") or []),
                "n_variants": len(meta.get("variants") or []),
                "best_name": res.get("best_name", ""),
                "updated_at": meta.get("updated_at", 0),
            })
    out.sort(key=lambda x: x.get("updated_at", 0), reverse=True)
    return out


@router.post("/api/varianten/projects")
async def varianten_create(req: Request):
    body = await req.json()
    name = _var_safe_name(body.get("name", ""))
    d = VARIANTEN_DIR / name
    if d.exists():
        raise HTTPException(status_code=409, detail="Vergleich existiert bereits")
    return _var_save(name, {"title": body.get("title", name)})


@router.get("/api/varianten/projects/{name}")
async def varianten_get(name: str):
    return _var_load(name)


@router.put("/api/varianten/projects/{name}")
async def varianten_put(name: str, req: Request):
    body = await req.json()
    return _var_save(name, body)


@router.delete("/api/varianten/projects/{name}")
async def varianten_delete(name: str):
    import shutil
    d = _var_dir(name)
    shutil.rmtree(d, ignore_errors=True)
    return {"ok": True}




_VAR_CRITERIA_SYSTEM = (
    "Du hilfst bei einer Entscheidung (Variantenvergleich). Nenne die wichtigsten "
    "ENTSCHEIDUNGSKRITERIEN, nach denen die Varianten bewertet werden sollten — "
    "konkret, überschneidungsfrei, 4–8 Stück. Gib pro Kriterium an, ob ein hoher "
    "Wert gut ('benefit', z. B. Qualität) oder schlecht ('cost', z. B. Preis) ist. "
    'Antworte NUR mit JSON: {"criteria":[{"name":"<Kriterium>","direction":"benefit|cost"}]}.'
)
_VAR_VARIANTS_SYSTEM = (
    "Du hilfst bei einer Entscheidung. Schlage sinnvolle, klar unterscheidbare "
    "VARIANTEN/Alternativen vor (3–6), jeweils mit kurzer Beschreibung. "
    'Antworte NUR mit JSON: {"variants":[{"name":"<Variante>","description":"<kurz>"}]}.'
)
_VAR_PAIRWISE_SYSTEM = (
    "Du schätzt die relative Wichtigkeit von Entscheidungskriterien im PAARVERGLEICH "
    "(Saaty-Skala 1–9: 1=gleich wichtig, 3=etwas wichtiger, 5=deutlich, 7=sehr, "
    "9=extrem wichtiger; Zwischenwerte erlaubt). Du bekommst eine nummerierte "
    "Kriterienliste. Gib für jedes Paar (i<j) an, um welchen Faktor Kriterium i "
    "wichtiger ist als j (Wert <1, wenn j wichtiger ist). "
    'Antworte NUR mit JSON: {"pairs":[{"i":0,"j":1,"value":3,"grund":"<kurz>"}]}.'
)
_VAR_RATINGS_SYSTEM = (
    "Du bewertest VARIANTEN je KRITERIUM auf einer Skala von 1 (sehr schlecht) bis "
    "10 (sehr gut) — immer so, dass 10 = am besten ist (auch bei Kosten: 10 = "
    "günstigste Variante). Stütze dich auf die gegebenen Variantenbeschreibungen "
    "und den Kontext; erfinde keine Fakten, im Zweifel neutral (5). "
    'Antworte NUR mit JSON: {"ratings":[{"variant":0,"scores":[{"criterion":0,"value":7}]}]}.'
)
_VAR_EXPLAIN_SYSTEM = (
    "Du erklärst das Ergebnis eines gewichteten Variantenvergleichs (Nutzwertanalyse). "
    "Du bekommst die deterministisch berechneten Gewichte, das Ranking und eine "
    "Sensitivitätsangabe (bei welchem Kriterium der Sieger wechselt). Fasse in "
    "2–4 Sätzen zusammen: Wer gewinnt und warum, wie knapp/robust das ist, worauf "
    "man achten sollte. Rechne KEINE Zahlen neu; nutze die gegebenen Werte."
)


@router.post("/api/varianten/suggest-criteria")
async def varianten_suggest_criteria(req: Request):
    body = await req.json()
    model = _pick_model(body.get("model"), _model_for("general"))
    prompt = (f"Entscheidung: {str(body.get('title','')).strip()}\n"
              f"Beschreibung: {str(body.get('description','')).strip()[:2000]}")
    data, ti, to, _ = await _research_llm_json(model, _VAR_CRITERIA_SYSTEM, prompt)
    crits = []
    for c in (data.get("criteria") or []):
        nm = str((c or {}).get("name", "")).strip()[:120]
        if nm:
            d = (c or {}).get("direction")
            crits.append({"name": nm, "direction": d if d in ("benefit", "cost") else "benefit"})
    return {"criteria": crits, "tokens": {"in": ti, "out": to}}


@router.post("/api/varianten/suggest-variants")
async def varianten_suggest_variants(req: Request):
    body = await req.json()
    model = _pick_model(body.get("model"), _model_for("general"))
    crit_names = ", ".join(str(c.get("name", "")).strip() for c in (body.get("criteria") or []) if c.get("name"))
    prompt = (f"Entscheidung: {str(body.get('title','')).strip()}\n"
              f"Beschreibung: {str(body.get('description','')).strip()[:2000]}\n"
              f"Kriterien: {crit_names}")
    data, ti, to, _ = await _research_llm_json(model, _VAR_VARIANTS_SYSTEM, prompt)
    variants = []
    for v in (data.get("variants") or []):
        nm = str((v or {}).get("name", "")).strip()[:120]
        if nm:
            variants.append({"name": nm, "description": str((v or {}).get("description", "")).strip()[:2000]})
    return {"variants": variants, "tokens": {"in": ti, "out": to}}


@router.post("/api/varianten/suggest-pairwise")
async def varianten_suggest_pairwise(req: Request):
    body = await req.json()
    criteria = [str(c.get("name", "")).strip() for c in (body.get("criteria") or []) if c.get("name")]
    n = len(criteria)
    if n < 2:
        return {"pairwise": [], "rationale": [], "tokens": {"in": 0, "out": 0}}
    model = _pick_model(body.get("model"), _model_for("general"))
    lst = "\n".join(f"{i}: {c}" for i, c in enumerate(criteria))
    prompt = (f"Entscheidung: {str(body.get('title','')).strip()}\n\nKriterien:\n{lst}")
    data, ti, to, _ = await _research_llm_json(model, _VAR_PAIRWISE_SYSTEM, prompt)
    # Vollständige Matrix aufbauen (Diagonale 1, Reziprozität); Rest 1 (Gleichstand)
    matrix = [[1.0] * n for _ in range(n)]
    rationale = []
    for pr in (data.get("pairs") or []):
        try:
            i, j = int(pr.get("i")), int(pr.get("j"))
            val = float(pr.get("value"))
        except (TypeError, ValueError):
            continue
        if 0 <= i < n and 0 <= j < n and i != j and val > 0:
            val = max(1.0 / 9.0, min(9.0, val))
            matrix[i][j] = val
            matrix[j][i] = 1.0 / val
            g = str(pr.get("grund", "")).strip()[:200]
            if g:
                rationale.append({"i": i, "j": j, "grund": g})
    return {"pairwise": matrix, "rationale": rationale, "tokens": {"in": ti, "out": to}}


@router.post("/api/varianten/suggest-ratings")
async def varianten_suggest_ratings(req: Request):
    body = await req.json()
    criteria = [str(c.get("name", "")).strip() for c in (body.get("criteria") or []) if c.get("name")]
    variants = [{"name": str(v.get("name", "")).strip(), "description": str(v.get("description", "")).strip()}
                for v in (body.get("variants") or []) if v.get("name")]
    nc, nv = len(criteria), len(variants)
    if nc == 0 or nv == 0:
        return {"ratings": [], "tokens": {"in": 0, "out": 0}}
    # Recherche-Modell (respektiert „Web-Recherche lokal"); optional RAG-Kontext
    model, err = await _research_model(body.get("model"))
    if err:
        raise HTTPException(status_code=503, detail=err)
    rag_ctx = await _plan_rag_context(body.get("collection_ids"),
                                      str(body.get("title", "")) + " " + " ".join(criteria))
    clist = "\n".join(f"{i}: {c}" for i, c in enumerate(criteria))
    vlist = "\n".join(f"{i}: {v['name']} — {v['description'][:400]}" for i, v in enumerate(variants))
    prompt = (f"Entscheidung: {str(body.get('title','')).strip()}\n\nKriterien:\n{clist}\n\n"
              f"Varianten:\n{vlist}")
    if rag_ctx:
        prompt += f"\n\nBelegkontext (aus Quellen):\n{rag_ctx[:3000]}"
    data, ti, to, _ = await _research_llm_json(model, _VAR_RATINGS_SYSTEM, prompt)
    ratings = [[5.0] * nc for _ in range(nv)]
    for rv in (data.get("ratings") or []):
        try:
            vi = int(rv.get("variant"))
        except (TypeError, ValueError):
            continue
        if not (0 <= vi < nv):
            continue
        for sc in (rv.get("scores") or []):
            try:
                ci, val = int(sc.get("criterion")), float(sc.get("value"))
            except (TypeError, ValueError):
                continue
            if 0 <= ci < nc:
                ratings[vi][ci] = max(1.0, min(10.0, val))
    return {"ratings": ratings, "tokens": {"in": ti, "out": to}}


@router.post("/api/varianten/explain")
async def varianten_explain(req: Request):
    body = await req.json()
    name = body.get("name")
    data = _var_load(name) if name else body.get("data") or {}
    res = data.get("result") or _var_compute(data)
    criteria = data.get("criteria") or []
    variants = data.get("variants") or []
    if not res.get("ranking"):
        return {"text": "Noch keine vollständige Bewertung vorhanden.", "tokens": {"in": 0, "out": 0}}
    from tools import decision as _dec
    directions = [(c.get("direction") if c.get("direction") in ("benefit", "cost") else "benefit") for c in criteria]
    sens = _dec.sensitivity(res.get("weights") or [], data.get("ratings") or [], directions)
    wtxt = "\n".join(f"- {criteria[i].get('name','?')}: Gewicht {res['weights'][i]:.2f}"
                     for i in range(min(len(criteria), len(res.get("weights") or []))))
    rtxt = "\n".join(f"{r['index']+1}. {variants[r['index']].get('name','?')} — Nutzwert {r['score']:.2f} ({r['percent']}%)"
                     for r in res["ranking"] if r["index"] < len(variants))
    flip = [criteria[s["criterion"]].get("name", "?") for s in sens
            if s.get("flips") and s["criterion"] < len(criteria)]
    stxt = ("Sieger wechselt bei stärkerer Gewichtung von: " + ", ".join(flip)) if flip else \
           "Der Sieger bleibt auch bei moderat veränderten Gewichten stabil."
    model = _pick_model(body.get("model"), _model_for("general"))
    prompt = (f"Entscheidung: {data.get('title','')}\n\nGewichte:\n{wtxt}\n\n"
              f"Konsistenz CR={res.get('cr',0):.2f} ({'ok' if res.get('consistent') else 'zu inkonsistent'}).\n\n"
              f"Ranking:\n{rtxt}\n\nSensitivität: {stxt}")
    text, ti, to = "", 0, 0
    try:
        async with _model_session(model), httpx.AsyncClient(timeout=120) as client:
            resp = await _llm.chat(client, {
                "model": model, "think": False, "stream": False,
                "messages": [{"role": "system", "content": _VAR_EXPLAIN_SYSTEM},
                             {"role": "user", "content": prompt}],
                "options": {"num_ctx": _profile_num_ctx()}, "keep_alive": KEEP_ALIVE,
            })
            resp.raise_for_status()
        j = resp.json()
        ti, to = _llm_tok(j)
        text = re.sub(r"<think>.*?</think>", "", j.get("message", {}).get("content", ""), flags=re.DOTALL).strip()
    except Exception as e:
        text = f"(Analyse nicht möglich: {e})"
    return {"text": text, "tokens": {"in": ti, "out": to}}


@router.post("/api/varianten/auto-fill")
async def varianten_auto_fill(req: Request):
    """Aus einer Problembeschreibung die KOMPLETTE Bewertungstabelle erzeugen.

    Orchestriert die vorhandenen Einzelschritte (Kriterien → Paarvergleich →
    Varianten → Bewertungen) in einem Durchlauf, optional mit Web-Grounding.
    Reine Vorschläge; Gewichte/Ranking rechnet weiterhin der PUT (``_var_compute``)
    deterministisch. Lokal-bevorzugt über ``_research_model`` (respektiert
    „Web-Recherche lokal"/Geheim-Modus)."""
    body = await req.json()
    title = str(body.get("title", "")).strip()[:200]
    description = str(body.get("description", "")).strip()[:4000]
    if not (title or description):
        raise HTTPException(status_code=400, detail="Bitte das Problem beschreiben.")
    model, err = await _research_model(body.get("model"))
    if err:
        raise HTTPException(status_code=503, detail=err)

    tok = {"in": 0, "out": 0}

    def _add(a, b):
        tok["in"] += a
        tok["out"] += b

    base = (f"Entscheidung: {title}\n" if title else "") + \
           (f"Beschreibung: {description}\n" if description else "")

    # 1) Optionale Web-Recherche als Grounding (nur der Web-Query ist extern; das LLM
    #    bleibt bei Geheim-Modus lokal). Fehler dürfen die Generierung nicht stoppen.
    ground = ""
    sources: list = []
    if body.get("web"):
        try:
            from tools.search import search_with_sources
            q = (title + " " + description).strip()[:200]
            src, text = await search_with_sources(q, 5)
            sources = src or []
            if text:
                ground = f"\n\nBelegkontext aus Web-Recherche:\n{text[:2800]}"
        except Exception:
            pass   # ohne Grounding weiter

    # 2) Kriterien
    data, ti, to, _ = await _research_llm_json(model, _VAR_CRITERIA_SYSTEM, base + ground)
    _add(ti, to)
    criteria = []
    for c in (data.get("criteria") or []):
        nm = str((c or {}).get("name", "")).strip()[:120]
        if nm:
            d = (c or {}).get("direction")
            criteria.append({"name": nm, "direction": d if d in ("benefit", "cost") else "benefit"})
    nc = len(criteria)

    # 3) Paarvergleich (vollständige nc×nc-Matrix mit Reziprozität)
    pairwise = [[1.0] * nc for _ in range(nc)]
    if nc >= 2:
        clist = "\n".join(f"{i}: {c['name']}" for i, c in enumerate(criteria))
        pdata, ti, to, _ = await _research_llm_json(
            model, _VAR_PAIRWISE_SYSTEM, base + "\nKriterien:\n" + clist)
        _add(ti, to)
        for pr in (pdata.get("pairs") or []):
            try:
                i, j = int(pr.get("i")), int(pr.get("j"))
                val = float(pr.get("value"))
            except (TypeError, ValueError):
                continue
            if 0 <= i < nc and 0 <= j < nc and i != j and val > 0:
                val = max(1.0 / 9.0, min(9.0, val))
                pairwise[i][j] = val
                pairwise[j][i] = 1.0 / val

    # 4) Varianten — aus der Problembeschreibung (NICHT an die evtl. lange, verbose
    #    Kriterienliste gekoppelt: das blähte den Prompt auf und ließ kleine Modelle
    #    das JSON verwerfen). Nur kompakte Kriterien-Kurznamen als Kontext.
    crit_hint = ", ".join(c["name"].split("(")[0].strip()[:40] for c in criteria[:8])
    def _parse_variants(vd):
        out = []
        for v in (vd.get("variants") or []):
            nm = str((v or {}).get("name", "")).strip()[:120]
            if nm:
                out.append({"name": nm, "description": str((v or {}).get("description", "")).strip()[:2000]})
        return out
    vprompt = base + (f"\nKriterien (nur Kontext): {crit_hint}" if crit_hint else "") + ground
    vdata, ti, to, _ = await _research_llm_json(model, _VAR_VARIANTS_SYSTEM, vprompt)
    _add(ti, to)
    variants = _parse_variants(vdata)
    if not variants:   # Rückfall: minimaler Prompt (nur Problem), einmalig
        vdata, ti, to, _ = await _research_llm_json(model, _VAR_VARIANTS_SYSTEM, base)
        _add(ti, to)
        variants = _parse_variants(vdata)
    nv = len(variants)

    # 5) Bewertungen (nv×nc, 1–10; Standard 5)
    ratings = [[5.0] * nc for _ in range(nv)]
    if nc and nv:
        clist = "\n".join(f"{i}: {c['name']}" for i, c in enumerate(criteria))
        vlist = "\n".join(f"{i}: {v['name']} — {v['description'][:400]}" for i, v in enumerate(variants))
        rdata, ti, to, _ = await _research_llm_json(
            model, _VAR_RATINGS_SYSTEM,
            base + f"\nKriterien:\n{clist}\n\nVarianten:\n{vlist}" + ground)
        _add(ti, to)
        for rv in (rdata.get("ratings") or []):
            try:
                vi = int(rv.get("variant"))
            except (TypeError, ValueError):
                continue
            if not (0 <= vi < nv):
                continue
            for sc in (rv.get("scores") or []):
                try:
                    ci, val = int(sc.get("criterion")), float(sc.get("value"))
                except (TypeError, ValueError):
                    continue
                if 0 <= ci < nc:
                    ratings[vi][ci] = max(1.0, min(10.0, val))

    return {"criteria": criteria, "variants": variants, "pairwise": pairwise,
            "ratings": ratings, "sources": sources, "tokens": tok}

