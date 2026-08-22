"""Router: Recherche-Gruppe (research/deepresearch/search/clarify/deepdive)

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


class ResearchRequest(BaseModel):
    topic: str
    aspects: List[str]
    model: str = ""   # leer → Wissenschafts-Modell aus dem Profil


class DeepDiveRequest(BaseModel):
    """Deepdive: aus der letzten Antwort X Vertiefungsfragen ableiten und der Reihe
    nach abarbeiten (je Frage eine Websuche + optional RAG → eine Antwort)."""
    last_answer: str = ""           # letzte Assistenten-Antwort = Kontext / Vorwort
    topic: str = ""                 # letzte Nutzerfrage (Themenanker)
    count: int = 5                  # X — Anzahl Fragen/Kapitel
    model: str = ""                 # leer → aktuelles Chat-Modell (general)
    as_document: bool = False       # True → /ddd (Vorwort + Kapitel), False → /dd
    web_search: bool = True
    rag_collections: List[str] = []

@router.post("/api/research")
async def research(request: ResearchRequest):
    return StreamingResponse(
        _research_generator(request),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


async def _research_generator(request: ResearchRequest):
    import re
    from tools.search import search_with_sources

    aspects = [a.strip() for a in request.aspects if a.strip()]
    if not aspects:
        yield _sse({"type": "error", "message": "Keine Aspekte angegeben"})
        return

    # Recherche ist immer wissenschaftlich → Wissenschafts-Modell (sofern nicht
    # explizit ein gültiges Modell angefordert wurde).
    # Profil-Schalter „Web-Recherche lokal": externes Modell auf ein lokales umbiegen.
    _r_model, _r_err = await _research_model(request.model)
    if _r_err:
        yield _sse({"type": "error", "message": _r_err})
        return

    yield _sse({"type": "research_start", "topic": request.topic, "aspects": aspects})
    tasks = [search_with_sources(f"{request.topic} {aspect}", 5) for aspect in aspects]
    raw_results = await asyncio.gather(*tasks, return_exceptions=True)

    aspect_data = []
    all_sources = []
    for aspect, raw in zip(aspects, raw_results):
        if isinstance(raw, Exception):
            sources, text = [], f"Suchfehler: {raw}"
        else:
            sources, text = raw
        yield _sse({"type": "search_done", "aspect": aspect})
        aspect_data.append((aspect, text))
        all_sources.append({"aspect": aspect, "sources": sources})

    yield _sse({"type": "sources", "data": all_sources})

    yield _sse({"type": "synthesizing"})

    # Kontext-Budget: Textmenge je Aspekt an num_ctx anpassen, sonst wird bei vielen
    # Aspekten das Fenster gefüllt und der Bericht abgeschnitten (siehe _deepresearch).
    _rc = _profile_num_ctx()
    _r_per_aspect = max(400, min(2500, int((_rc * 0.55) * 3.3) // max(1, len(aspect_data))))
    synthesis_parts = [f"Thema: {request.topic}\n"]
    for aspect, result in aspect_data:
        synthesis_parts.append(f"### Suchergebnisse – {aspect}\n{result[:_r_per_aspect]}\n")

    synthesis_prompt = "\n".join(synthesis_parts) + (
        f"\n\nErstelle jetzt einen strukturierten, informativen Recherchebericht über **{request.topic}** "
        f"basierend auf den obigen Suchergebnissen. Gliederung:\n"
        f"1. Kurze Übersicht über {request.topic}\n"
        + "".join(f"{i+2}. Abschnitt: {a}\n" for i, (a, _) in enumerate(aspect_data))
        + f"{len(aspect_data)+2}. Fazit / Zusammenfassung\n\n"
        f"Schreibe auf Deutsch. Verwende Markdown (## Überschriften, **Fett**, Aufzählungen). "
        f"Sei informativ, präzise und stütze dich auf die Suchergebnisse."
    )

    try:
        _r_msgs: list = []
        _r_topic = request.topic + " " + " ".join(a for a, _ in aspect_data)
        _r_sys = "\n\n".join(p for p in (_SCIENCE_PROMPT, _augment_prefix(_r_topic)) if p)
        if _r_sys:
            _r_msgs.append({"role": "system", "content": _r_sys})
        _r_msgs.append({"role": "user", "content": synthesis_prompt})
        async with _model_session(_r_model), httpx.AsyncClient(timeout=300) as client:
            resp = await _llm.chat(client,{
                "model": _r_model,
                "think": False,
                "messages": _r_msgs,
                "stream": False,
                "options": {"num_ctx": _rc, "num_predict": max(600, int(_rc * 0.45))},
                "keep_alive": KEEP_ALIVE,
            })
            resp.raise_for_status()
            llm_result = resp.json()
    except Exception as e:
        yield _sse({"type": "error", "message": str(e)})
        return

    content = llm_result.get("message", {}).get("content", "")
    content = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL).strip()
    _r_ti, _r_to = _llm_tok(llm_result)

    words = content.split(" ")
    for i, word in enumerate(words):
        yield _sse({"type": "text", "content": word + (" " if i < len(words) - 1 else "")})
        await asyncio.sleep(0.004)

    yield _sse({"type": "done", "tokens": {"in": _r_ti, "out": _r_to}})


# ── Tiefe Recherche (Chat): Thema → automatische Teilaspekte → Websuche je Aspekt →
# quellen-gestützter Bericht mit steuerbarer Tiefe (Aspektzahl) und Länge (Wortzahl).
# Nutzt dieselben Bausteine wie /api/research (search_with_sources + Synthese), leitet
# die Aspekte aber selbst aus dem Thema ab. Web-Gate + „Web-Recherche lokal" gelten.

class DeepResearchRequest(BaseModel):
    topic: str
    depth: int = 6
    words: int = 1000
    focus: Optional[str] = None
    model: Optional[str] = None


@router.post("/api/deepresearch")
async def deep_research(request: DeepResearchRequest):
    return StreamingResponse(
        _deepresearch_generator(request),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


async def _deepresearch_generator(request: DeepResearchRequest):
    import re
    from tools.search import search_with_sources

    topic = (request.topic or "").strip()
    if not topic:
        yield _sse({"type": "error", "message": "Kein Thema angegeben."})
        return
    if not _web_search_allowed():
        yield _sse({"type": "error", "message": "Im aktuellen Modus ist die Websuche gesperrt "
                                                 "(z. B. Ausbildungs-/Hartman-Modus) — Tiefe "
                                                 "Recherche nicht möglich."})
        return
    depth = max(3, min(int(request.depth or 6), 12))
    target_words = max(200, min(int(request.words or 1000), 4000))
    focus = (request.focus or "").strip()

    _r_model, _r_err = await _research_model(request.model)
    if _r_err:
        yield _sse({"type": "error", "message": _r_err})
        return

    _tok = {"in": 0, "out": 0}

    # 1) Teilaspekte automatisch ableiten (robustes JSON)
    _focus_line = f"\nSchwerpunkt/Fokus: {focus}" if focus else ""
    _aspect_prompt = (
        f"Thema: \"{topic}\"{_focus_line}\n\n"
        f"Zerlege das Thema in genau {depth} prägnante, sich ergänzende Teilaspekte/Unterfragen "
        f"für eine gründliche Web-Recherche (je 2–6 Wörter, deutsch, ohne Nummerierung).\n"
        f'Antworte NUR mit JSON: {{"aspects":["…","…"]}}.'
    )
    aspects: list = []
    try:
        async with _model_session(_r_model), httpx.AsyncClient(timeout=120) as client:
            resp = await _llm.chat(client, {
                "model": _r_model, "think": False, "stream": False, "format": "json",
                "messages": [{"role": "user", "content": _aspect_prompt}],
                "options": {"num_ctx": _profile_num_ctx()}, "keep_alive": KEEP_ALIVE,
            })
            resp.raise_for_status()
        _aj = resp.json()
        _ti, _to = _llm_tok(_aj)
        _tok["in"] += _ti
        _tok["out"] += _to
        _d = _parse_llm_json(_aj.get("message", {}).get("content", "")) or {}
        aspects = [str(a).strip() for a in (_d.get("aspects") or []) if str(a).strip()][:depth]
    except httpx.ConnectError:
        yield _sse({"type": "error", "message": "Ollama nicht erreichbar – läuft der lokale Server?"})
        return
    except Exception:
        aspects = []
    if not aspects:
        aspects = ["Überblick", "technische Daten", "Geschichte / Hintergrund",
                   "Varianten / Modelle", "Preise / Markt", "Besonderheiten / Bewertung",
                   "Vor- und Nachteile", "Alternativen"][:depth]
    yield _sse({"type": "aspects", "aspects": aspects, "topic": topic})

    # 2) je Aspekt Websuche (parallel)
    tasks = [search_with_sources(f"{topic} {a}", 5) for a in aspects]
    raw_results = await asyncio.gather(*tasks, return_exceptions=True)
    aspect_data = []
    all_sources = []
    for a, r in zip(aspects, raw_results):
        if isinstance(r, Exception):
            sources, text = [], f"Suchfehler: {r}"
        else:
            sources, text = r
        yield _sse({"type": "search_done", "aspect": a})
        aspect_data.append((a, text))
        all_sources.append({"aspect": a, "sources": sources})
    yield _sse({"type": "sources", "data": all_sources})
    yield _sse({"type": "synthesizing"})

    # 3) quellen-gestützte Synthese mit Längenziel + Anti-Halluzinations-Auflage.
    # Kontext-Budget: 12 Aspekte × 2500 Zeichen füllen ein 8k-Fenster komplett und
    # schneiden die Antwort ab. Deshalb Textmenge JE ASPEKT und Ziellänge an num_ctx
    # anpassen (grobe Schätzung ~3.3 Zeichen/Token für Deutsch) und die Ausgabe-Tokens
    # (num_predict) begrenzen, damit der Bericht sauber endet statt mittendrin abzubrechen.
    _ctx = _profile_num_ctx()
    _out_reserve_tok = max(400, min(int(target_words * 1.7), int(_ctx * 0.5)))
    _in_budget_chars = max(2500, int((_ctx - _out_reserve_tok - 700) * 3.3))
    _per_aspect = max(400, min(2500, _in_budget_chars // max(1, len(aspect_data))))
    _eff_words = max(250, min(target_words, int(_out_reserve_tok / 1.7)))
    _shortened = _eff_words < int(target_words * 0.85)
    if _shortened:
        yield _sse({"type": "notice", "message":
                    f"Hinweis: Bericht auf ~{_eff_words} statt ~{target_words} Wörter begrenzt, "
                    f"damit er ins Kontextfenster ({_ctx} Tokens) passt. Für längere Berichte im "
                    f"Profil das Kontextfenster erhöhen oder weniger Aspekte (Tiefe) wählen."})

    _parts = [f"Thema: {topic}\n"]
    if focus:
        _parts.append(f"Schwerpunkt: {focus}\n")
    for a, t in aspect_data:
        _parts.append(f"### Suchergebnisse – {a}\n{t[:_per_aspect]}\n")
    _synth = "\n".join(_parts) + (
        f"\n\nSchreibe daraus einen AUSFÜHRLICHEN, gut strukturierten Recherchebericht über "
        f"**{topic}** von **ca. {_eff_words} Wörtern** auf Deutsch (Markdown: ## Überschriften, "
        f"**Fett**, Aufzählungen, bei Kennwerten gern eine Tabelle). Gliederung: kurze Übersicht, "
        f"je ein Abschnitt pro Aspekt, abschließend ein Fazit. Halte die Ziellänge ein und schließe "
        f"mit einem vollständigen Fazit ab (nicht mitten im Satz enden). WICHTIG: Stütze JEDE konkrete "
        f"Angabe (Zahlen, technische Daten, Baujahre, Preise, Eigennamen) AUSSCHLIESSLICH auf die "
        f"obigen Suchergebnisse. Ist etwas nicht belegt oder widersprüchlich, kennzeichne es "
        f"ausdrücklich als unsicher — erfinde nichts."
    )
    try:
        _sys = "\n\n".join(p for p in (_SCIENCE_PROMPT,
                                       _augment_prefix(topic + " " + " ".join(a for a, _ in aspect_data))) if p)
        _msgs = ([{"role": "system", "content": _sys}] if _sys else []) + \
                [{"role": "user", "content": _synth}]
        async with _model_session(_r_model), httpx.AsyncClient(timeout=600) as client:
            resp = await _llm.chat(client, {
                "model": _r_model, "think": False, "stream": False,
                "messages": _msgs,
                "options": {"num_ctx": _ctx, "num_predict": _out_reserve_tok + 200},
                "keep_alive": KEEP_ALIVE,
            })
            resp.raise_for_status()
        _j = resp.json()
    except httpx.ConnectError:
        yield _sse({"type": "error", "message": "Ollama nicht erreichbar – läuft der lokale Server?"})
        return
    except httpx.HTTPStatusError as e:
        _sc = getattr(e.response, "status_code", 0) or 0
        if _sc in (502, 503, 504):
            _m = (f"Der Anbieter hat nicht rechtzeitig geantwortet (HTTP {_sc}). "
                  f"Bei tiefer Recherche mit vielen Aspekten kann die Synthese lange dauern — "
                  f"bitte weniger Tiefe/Umfang wählen oder ein lokales Modell verwenden.")
        else:
            _m = f"Modell abgelehnt (num_ctx/VRAM?): HTTP {_sc}"
        yield _sse({"type": "error", "message": _m})
        return
    except Exception as e:
        yield _sse({"type": "error", "message": f"Synthese fehlgeschlagen: {e}"})
        return

    content = (_j.get("message", {}) or {}).get("content", "") or ""
    content = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL).strip()
    _ti, _to = _llm_tok(_j)
    _tok["in"] += _ti
    _tok["out"] += _to
    _words = content.split(" ")
    for _i, _w in enumerate(_words):
        yield _sse({"type": "text", "content": _w + (" " if _i < len(_words) - 1 else "")})
        await asyncio.sleep(0.003)
    yield _sse({"type": "done", "tokens": _tok})


# ── Erweiterte Suche („/such"): alternative Suchbegriffe + Websuche + Zusammenfassung ──
# Der Nutzer kennt oft den treffenden (Fach-)Begriff nicht. Diese Funktion lässt das
# LLM alternative Suchbegriffe für dasselbe Anliegen erzeugen (Synonyme, Fach-/
# Umgangssprache, engl. Entsprechungen), durchsucht damit das Web (DuckDuckGo) und
# fasst die Treffer mit Quellen zusammen. Reine Wiederverwendung vorhandener Bausteine.

class SearchExpandRequest(BaseModel):
    query: str
    model: Optional[str] = None
    count: int = 6
    search: bool = True


@router.post("/api/search/expand")
async def search_expand(request: SearchExpandRequest):
    return StreamingResponse(
        _search_expand_generator(request),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


async def _search_expand_generator(request: SearchExpandRequest):
    import re
    from tools.search import search_with_sources

    query = (request.query or "").strip()
    if not query:
        yield _sse({"type": "error", "message": "Kein Suchbegriff angegeben"})
        return

    # Persona »Hartman«: Websuche komplett gesperrt (alles rein lokal).
    if not _web_search_allowed():
        yield _sse({"type": "error", "message": "REKRUT, im Ausbildungsmodus läuft NICHTS "
                                                "nach draußen – KEINE Websuche. Alles rein lokal!"})
        return

    # Profil-Schalter „Web-Recherche lokal" beachten
    _model, _m_err = await _research_model(request.model)
    if _m_err:
        yield _sse({"type": "error", "message": _m_err})
        return
    n = max(3, min(int(request.count or 6), 12))
    _ti = _to = 0

    # 1) Alternative Suchbegriffe erzeugen (JSON, robust geparst)
    term_prompt = (
        f"Der Nutzer sucht Informationen, kennt aber evtl. nicht den treffenden Fachbegriff.\n"
        f"Suchanliegen: \"{query}\"\n\n"
        f"Erzeuge {n} alternative Suchbegriffe bzw. -phrasen, die DASSELBE beschreiben – "
        f"Synonyme, Fachbegriff gegenüber Umgangssprache, enger und weiter gefasste "
        f"Formulierungen sowie die wichtigsten englischen Entsprechungen. Jeweils kurz und "
        f"suchtauglich (2–5 Wörter), keine Dopplungen.\n"
        f"Antworte NUR mit JSON: {{\"terms\":[\"…\",\"…\"]}}"
    )
    # Bei einem API-Modell, das keine Recherche zulässt, fällt der Helfer auf ein
    # lokales Modell zurück (reiner Rückfall — bevorzugt bleibt das gewählte Modell).
    data, a, b, _used = await _research_llm_json(
        _model,
        "Du bist ein Recherche-Assistent für Suchbegriffe. Antworte ausschließlich mit gültigem JSON.",
        term_prompt)
    _ti += a; _to += b
    terms = [str(t).strip() for t in (data.get("terms") or []) if str(t).strip()]
    if _used != _model:
        yield _sse({"type": "notice",
                    "message": f"API-Modell lieferte keine Suchbegriffe – lokal wiederholt ({_used})."})

    # Original zuerst, dann Alternativen; doppelte (case-insensitiv) entfernen
    ordered, seen = [], set()
    for t in [query] + terms:
        k = t.lower()
        if k and k not in seen:
            seen.add(k); ordered.append(t)
    yield _sse({"type": "terms", "query": query, "terms": ordered})

    if not request.search:
        yield _sse({"type": "done", "tokens": {"in": _ti, "out": _to}})
        return

    # 2) Websuche über die ergiebigsten Begriffe (begrenzt, parallel)
    search_terms = ordered[:4]
    yield _sse({"type": "searching", "terms": search_terms})
    results = await asyncio.gather(
        *[search_with_sources(t, 5) for t in search_terms], return_exceptions=True)

    blocks, sources, seen_url = [], [], set()
    for term, raw in zip(search_terms, results):
        if isinstance(raw, Exception):
            srcs, text = [], f"Suchfehler: {raw}"
        else:
            srcs, text = raw
        blocks.append(f"### Treffer für „{term}“\n{text[:2500]}")
        for s in srcs:
            u = s.get("url", "")
            if u and u not in seen_url:
                seen_url.add(u); sources.append(s)
    yield _sse({"type": "sources", "data": sources})
    yield _sse({"type": "synthesizing"})

    # 3) Antwort synthetisieren (Wissenschaftsmodus + Modus-Vorspann)
    synth = (
        f"Suchanliegen des Nutzers: „{query}“\n"
        f"Verwendete alternative Suchbegriffe: {', '.join(search_terms)}\n\n"
        + "\n\n".join(blocks)
        + "\n\nFasse die Suchergebnisse zu einer klaren, strukturierten Antwort auf das "
        "Suchanliegen zusammen (Deutsch, Markdown). Nenne die wichtigsten Erkenntnisse, "
        "verweise wo sinnvoll auf Quellen, und schließe mit einem kurzen Hinweis, welche "
        "Suchbegriffe am ergiebigsten waren. Wenn die Treffer dürftig sind, sage das ehrlich."
    )
    try:
        _msgs = []
        _sys = "\n\n".join(p for p in (_SCIENCE_PROMPT, _augment_prefix(query)) if p)
        if _sys:
            _msgs.append({"role": "system", "content": _sys})
        _msgs.append({"role": "user", "content": synth})
        async with _model_session(_model), httpx.AsyncClient(timeout=300) as client:
            resp = await _llm.chat(client, {
                "model": _model, "think": False, "messages": _msgs,
                "stream": False, "options": {"num_ctx": _profile_num_ctx()},
                "keep_alive": KEEP_ALIVE,
            })
            resp.raise_for_status()
            _j2 = resp.json()
        a, b = _llm_tok(_j2); _ti += a; _to += b
    except Exception as e:
        yield _sse({"type": "error", "message": str(e)})
        return

    content = _j2.get("message", {}).get("content", "")
    content = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL).strip()
    words = content.split(" ")
    for i, word in enumerate(words):
        yield _sse({"type": "text", "content": word + (" " if i < len(words) - 1 else "")})
        await asyncio.sleep(0.004)

    yield _sse({"type": "done", "tokens": {"in": _ti, "out": _to}})


# ── Dynamische Rückfragen („/frag"): Eingabemaske mit Text-/Auswahlfeldern ──────
# Erzeugt zu einer Aufgabe gezielte Rückfragen, BEVOR sie beantwortet wird. Jede
# Frage hat einen Typ (text | single | multi) und ggf. Optionen, sodass das Frontend
# eine Eingabemaske – auch mit Multiple-Choice – rendern kann. Genutzt in Chat,
# Medizin und Mathe (Feld `domain` rahmt nur den Prompt, keine Modell-Sonderlogik).

class ClarifyRequest(BaseModel):
    prompt: str
    domain: Optional[str] = "chat"
    model: Optional[str] = None
    max_questions: int = 4


_CLARIFY_DOMAIN_HINT = {
    "chat": "allgemeine Anfrage",
    "medical": "medizinische Anfrage (Symptome, Vorgeschichte, Kontext) – KEINE Diagnose, nur Präzisierung",
    "math": "mathematische Aufgabe (Gegebenes, Gesuchtes, Randbedingungen, Genauigkeit)",
}


def _normalize_clarify_questions(raw_qs, n: int) -> list:
    """Validiert/normalisiert eine rohe LLM-Frageliste zu ``[{question,type,options}]``.
    Auswahltyp ohne ≥2 Optionen wird zu Freitext. Kappt auf ``n`` Fragen."""
    questions = []
    for q in (raw_qs or [])[:n]:
        if not isinstance(q, dict):
            continue
        text = str(q.get("question") or "").strip()
        if not text:
            continue
        qtype = str(q.get("type") or "text").strip().lower()
        if qtype not in ("text", "single", "multi"):
            qtype = "text"
        opts = [str(o).strip() for o in (q.get("options") or []) if str(o).strip()][:6]
        if qtype in ("single", "multi") and len(opts) < 2:
            qtype, opts = "text", []
        questions.append({"question": text, "type": qtype, "options": opts})
    return questions


@router.post("/api/clarify")
async def clarify(request: ClarifyRequest):
    """Liefert gezielte Rückfragen (Eingabemaske) zu einer Aufgabe – oder eine leere
    Liste, wenn keine Klärung nötig ist. Robust gegen LLM-Geplapper (JSON-Extraktion)."""
    prompt = (request.prompt or "").strip()
    if not prompt:
        raise HTTPException(400, "Keine Aufgabe angegeben")
    _model = _pick_model(request.model)
    n = max(1, min(int(request.max_questions or 4), 6))
    hint = _CLARIFY_DOMAIN_HINT.get((request.domain or "chat").lower(), _CLARIFY_DOMAIN_HINT["chat"])

    user = (
        f"Fachgebiet: {hint}.\n"
        f"Aufgabe/Anfrage des Nutzers: \"{prompt}\"\n\n"
        "Entscheide, ob dir wichtige Informationen fehlen, um eine wirklich gute Antwort zu geben.\n"
        "- Wenn die Anfrage klar genug ist: gib eine LEERE Frageliste zurück.\n"
        f"- Sonst stelle bis zu {n} kurze, gezielte Rückfragen. Bevorzuge Auswahlfragen, wo es passt.\n"
        "Jede Frage ist ein Objekt: {\"question\": \"…\", \"type\": \"text\"|\"single\"|\"multi\", \"options\": [\"…\"]}.\n"
        "Bei type \"single\"/\"multi\": 2–5 sinnvolle Optionen angeben. Bei \"text\": options leer lassen.\n"
        "Antworte NUR mit JSON in diesem Format: {\"questions\": [ {\"question\":\"…\",\"type\":\"single\",\"options\":[\"A\",\"B\"]} ]}"
    )
    payload = {
        "model": _model, "think": False, "format": "json", "stream": False,
        "keep_alive": KEEP_ALIVE,
        "messages": [
            {"role": "system", "content": "Du formulierst gezielte Rückfragen zur Präzisierung einer Aufgabe. Antworte ausschließlich mit gültigem JSON."},
            {"role": "user", "content": user},
        ],
    }
    _ti = _to = 0
    try:
        async with _model_session(_model), httpx.AsyncClient(timeout=120) as client:
            resp = await _llm.chat(client, payload)
            resp.raise_for_status()
            _j = resp.json()
        _ti, _to = _llm_tok(_j)
        data = _parse_llm_json(_j.get("message", {}).get("content", "")) or {}
    except Exception as e:
        raise HTTPException(502, f"Rückfragen konnten nicht erzeugt werden: {e}")

    raw_qs = data.get("questions") if isinstance(data, dict) else None
    questions = _normalize_clarify_questions(raw_qs, n)

    return {
        "type": "questions" if questions else "none",
        "questions": questions,
        "tokens": {"in": _ti, "out": _to},
    }


class ClarifyStructureRequest(BaseModel):
    questions_text: str            # die vom Modell im Chat gestellten Rückfragen (Freitext)
    task: Optional[str] = ""       # ursprüngliche Aufgabe (für Kontext/sinnvolle Optionen)
    domain: Optional[str] = "chat"
    model: Optional[str] = None
    max_questions: int = 8


@router.post("/api/clarify/structure")
async def clarify_structure(request: ClarifyStructureRequest):
    """Wandelt bereits im Chat gestellte Rückfragen (Freitext des Modells) in eine
    strukturierte Eingabemaske um: je Frage ``{question, type, options}`` mit
    Vorauswahl (single/multi) oder Freitext. Erfindet keine neuen Themen – bildet
    nur die vorhandenen Fragen ab. Robust gegen LLM-Geplapper (JSON-Extraktion)."""
    qtext = (request.questions_text or "").strip()
    if not qtext:
        raise HTTPException(400, "Keine Rückfragen übergeben")
    _model = _pick_model(request.model)
    n = max(1, min(int(request.max_questions or 8), 12))
    hint = _CLARIFY_DOMAIN_HINT.get((request.domain or "chat").lower(), _CLARIFY_DOMAIN_HINT["chat"])
    task = (request.task or "").strip()

    user = (
        f"Fachgebiet: {hint}.\n"
        + (f"Ursprüngliche Aufgabe des Nutzers: \"{task}\"\n" if task else "")
        + "Das KI-Modell hat dem Nutzer folgende Rückfragen gestellt (Freitext):\n"
        f"\"\"\"\n{qtext}\n\"\"\"\n\n"
        "Wandle GENAU diese Rückfragen in eine ausfüllbare Maske um – erfinde keine neuen "
        f"Themen, fasse eng Zusammengehöriges zu je einer Frage zusammen (max. {n}).\n"
        "Jede Frage ist ein Objekt: {\"question\": \"…\", \"type\": \"text\"|\"single\"|\"multi\", \"options\": [\"…\"]}.\n"
        "- Nenne die Frage im Text kurz und klar.\n"
        "- Wenn die Rückfrage konkrete Alternativen aufzählt (z. B. „LED, Glühlampe, OLED?“): "
        "type \"single\" (bzw. \"multi\", falls mehrere zugleich möglich) und diese Alternativen als options.\n"
        "- Ergänze bei Auswahlfragen sinnvolle, gängige Optionen, falls im Text nur Beispiele stehen.\n"
        "- Offene Fragen ohne Alternativen: type \"text\", options leer.\n"
        "Antworte NUR mit JSON: {\"questions\": [ {\"question\":\"…\",\"type\":\"single\",\"options\":[\"A\",\"B\"]} ]}"
    )
    payload = {
        "model": _model, "think": False, "format": "json", "stream": False,
        "keep_alive": KEEP_ALIVE,
        "messages": [
            {"role": "system", "content": "Du strukturierst gestellte Rückfragen in eine ausfüllbare Maske. Antworte ausschließlich mit gültigem JSON."},
            {"role": "user", "content": user},
        ],
    }
    _ti = _to = 0
    try:
        async with _model_session(_model), httpx.AsyncClient(timeout=120) as client:
            resp = await _llm.chat(client, payload)
            resp.raise_for_status()
            _j = resp.json()
        _ti, _to = _llm_tok(_j)
        data = _parse_llm_json(_j.get("message", {}).get("content", "")) or {}
    except Exception as e:
        raise HTTPException(502, f"Rückfragen konnten nicht strukturiert werden: {e}")

    raw_qs = data.get("questions") if isinstance(data, dict) else None
    questions = _normalize_clarify_questions(raw_qs, n)
    return {
        "type": "questions" if questions else "none",
        "questions": questions,
        "tokens": {"in": _ti, "out": _to},
    }


@router.post("/api/deepdive")
async def deepdive(request: DeepDiveRequest):
    return StreamingResponse(
        _deepdive_generator(request),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


async def _deepdive_questions(model: str, context: str, topic: str, count: int,
                              tok: Optional[dict] = None) -> list:
    """Leitet aus der letzten Antwort genau ``count`` Vertiefungsfragen ab.
    Gibt eine Liste von Fragestrings zurück (Fallback: generische Fragen).
    ``tok`` (optional): dict {"in","out"}, in das der Tokenverbrauch summiert wird."""
    sys = (
        "Du bist ein gründlicher Rechercheur. Aus dem gegebenen Text leitest du "
        f"genau {count} weiterführende, eigenständige Vertiefungsfragen ab, die das "
        "Thema systematisch vertiefen (verschiedene Aspekte, keine Dopplungen). "
        "Jede Frage muss für sich als Suchanfrage funktionieren. "
        'Antworte NUR als JSON: {"questions": ["…", "…"]}.'
    )
    usr = (f"Thema/Ausgangsfrage: {topic}\n\n" if topic else "") + (
        f"Ausgangstext (letzte Antwort):\n{context[:6000]}\n\n"
        f"Formuliere genau {count} Vertiefungsfragen auf Deutsch."
    )
    try:
        async with _model_session(model), httpx.AsyncClient(timeout=180) as client:
            resp = await _llm.chat(client, {
                "model": model,
                "think": False,
                "stream": False,
                "format": "json",
                "messages": [
                    {"role": "system", "content": sys},
                    {"role": "user", "content": usr},
                ],
                "options": {"num_ctx": _profile_num_ctx()},
                "keep_alive": KEEP_ALIVE,
            })
            resp.raise_for_status()
            _dd_j = resp.json()
            if tok is not None:
                _a, _b = _llm_tok(_dd_j)
                tok["in"] += _a
                tok["out"] += _b
            data = _parse_llm_json(_dd_j.get("message", {}).get("content", "")) or {}
    except Exception:
        data = {}
    qs = data.get("questions") if isinstance(data, dict) else None
    out = [str(q).strip() for q in qs if str(q).strip()] if isinstance(qs, list) else []
    # Auf gewünschte Anzahl bringen (kürzen bzw. generisch auffüllen).
    out = out[:count]
    base = (topic or "das Thema").strip()
    while len(out) < count:
        out.append(f"Welche weiteren wichtigen Aspekte zu {base} sind relevant? (Teil {len(out)+1})")
    return out


async def _deepdive_answer(model: str, question: str, web: bool, rag_collections: list,
                           tok: Optional[dict] = None) -> str:
    """Beantwortet EINE Deepdive-Frage: Websuche (+ optional RAG) als Beleg, dann
    ein LLM-Aufruf. Gibt den fertigen Markdown-Antworttext zurück.
    ``tok`` (optional): dict {"in","out"}, in das der Tokenverbrauch summiert wird."""
    from tools.search import search_with_sources
    blocks = []
    if web:
        try:
            _, text = await search_with_sources(question, 5)
            if text:
                blocks.append("### Websuche\n" + text[:3000])
        except Exception as e:
            blocks.append(f"### Websuche\n(Suche fehlgeschlagen: {e})")
    if rag_collections:
        try:
            from tools.rag import query_collections
            hits = await query_collections(rag_collections, question, top_k_cap=5)
            if hits:
                rag_txt = "\n\n".join(
                    f"[{h.get('collection_name','?')} · {h.get('filename','?')}]\n{h.get('text','')}"
                    for h in hits
                )
                blocks.append("### Wissensdatenbank\n" + rag_txt[:3000])
        except Exception:
            pass
    grounding = "\n\n".join(blocks)
    sys = "\n\n".join(p for p in (_augment_prefix(question), _SCIENCE_PROMPT) if p)
    usr = (
        (f"Belegmaterial:\n{grounding}\n\n" if grounding else "")
        + f"Beantworte ausführlich und strukturiert (Markdown) folgende Frage:\n\n{question}\n\n"
        + ("Stütze dich auf das Belegmaterial und nenne Quellen." if grounding
           else "Antworte aus deinem Fachwissen.")
    )
    try:
        async with _model_session(model), httpx.AsyncClient(timeout=300) as client:
            resp = await _llm.chat(client, {
                "model": model,
                "think": False,
                "stream": False,
                "messages": [
                    {"role": "system", "content": sys},
                    {"role": "user", "content": usr},
                ],
                "options": {"num_ctx": _profile_num_ctx()},
                "keep_alive": KEEP_ALIVE,
            })
            resp.raise_for_status()
            _dd_j = resp.json()
            if tok is not None:
                _a, _b = _llm_tok(_dd_j)
                tok["in"] += _a
                tok["out"] += _b
            content = _dd_j.get("message", {}).get("content", "")
    except Exception as e:
        return f"_(Antwort fehlgeschlagen: {e})_"
    return re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL).strip()


async def _deepdive_generator(request: DeepDiveRequest):
    # Mit Websuche zählt der Deepdive als web-gestützte Recherche → Profil-Schalter
    # „Web-Recherche lokal" beachten; ohne Websuche bleibt die normale Modellwahl.
    if request.web_search:
        model, _m_err = await _research_model(request.model, _model_for("general"))
        if _m_err:
            yield _sse({"type": "error", "message": _m_err})
            return
    else:
        model = _pick_model(request.model, _model_for("general"))
    count = max(1, min(int(request.count or 5), 20))   # Sicherheitsgrenze
    context = (request.last_answer or request.topic or "").strip()
    if not context:
        yield _sse({"type": "error", "message": "Keine vorherige Antwort für den Deepdive vorhanden."})
        return

    yield _sse({"type": "dd_meta", "count": count, "as_document": request.as_document})

    _dd_tok = {"in": 0, "out": 0}
    # 1) Vertiefungsfragen ableiten
    questions = await _deepdive_questions(model, context, request.topic, count, tok=_dd_tok)
    yield _sse({"type": "dd_questions", "questions": questions})

    # 2) Fragen der Reihe nach abarbeiten (je Frage Suche + Antwort)
    for idx, question in enumerate(questions):
        yield _sse({"type": "dd_chapter_start", "index": idx, "question": question})
        answer = await _deepdive_answer(model, question, request.web_search, request.rag_collections, tok=_dd_tok)
        yield _sse({"type": "dd_chapter_done", "index": idx, "question": question, "answer": answer})

    yield _sse({"type": "done", "tokens": _dd_tok})

