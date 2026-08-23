"""Router: Präsentationsassistenten + Dossiers + Bild→Prompt (/api/presentation)

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


# ── Illustrierter Präsentationsassistent (Thema → Folien + KI-Bilder) ─────────
# Chat-Befehl /praesentation: erzeugt aus einem Thema eine komplette Präsentation
# UND je nach Umfang KI-Bilder (über den im Profil gewählten Bildgenerator, z. B.
# die lokale Z-Image-Brücke). Baut auf vorhandenen Bausteinen auf: _text_to_presentation,
# _slide_image_prompt, _generate_image_core. Bild-Umfang standardmäßig „nur Titel- und
# Abschnittsfolien", per Anweisung „alle"/„keine"/Zahl steuerbar.


async def _presentation_draft(topic: str, model: str) -> tuple:
    """Aus einem Thema einen Markdown-Präsentationsentwurf ableiten (ein LLM-Aufruf).
    Gibt ``(text, tok_in, tok_out)`` zurück. Der deterministische Parser
    ``_parse_prose_presentation`` macht daraus später Folien (Abschnitts-Trennfolien
    = bloße ``##``-Überschriften ohne Stichpunkte)."""
    _sys = "Du erstellst prägnante Präsentationsentwürfe auf Deutsch als reines Markdown."
    _user = (
        f"Erstelle einen Präsentationsentwurf über: {topic}\n\n"
        "Format als Markdown, NUR die Gliederung, ohne Erklärungen/Codeblöcke:\n"
        "# <Gesamttitel>\n"
        "Danach 6–9 Folien. Gliedere in 2–3 Abschnitte: jeder Abschnitt beginnt mit einer "
        "eigenen Trennfolie als bloße Überschrift OHNE Stichpunkte (## <Abschnittstitel>). "
        "Inhaltsfolien: ## <Folientitel> gefolgt von 3–5 kurzen Stichpunkten (- …)."
    )
    async with _model_session(model), httpx.AsyncClient(timeout=180) as client:
        resp = await _llm.chat(client, {
            "model": model, "think": False, "stream": False,
            "messages": [{"role": "system", "content": _sys},
                         {"role": "user", "content": _user}],
            "options": {"num_ctx": _profile_num_ctx(), "num_predict": 1200},
            "keep_alive": KEEP_ALIVE,
        })
        resp.raise_for_status()
    _j = resp.json()
    _c = (_j.get("message", {}) or {}).get("content", "") or ""
    _c = re.sub(r"<think>.*?</think>", "", _c, flags=re.DOTALL).strip()
    _ti, _to = _llm_tok(_j)
    return _c, _ti, _to


def _select_image_slides(slides: list, images) -> list:
    """Indizes der Folien, die ein Bild bekommen. ``images``: 'sections' (Standard =
    Titel+Abschnitte), 'all'/'alle', 'none'/'keine', oder eine Zahl N (wichtigste
    zuerst: Titel/Abschnitte, dann Inhaltsfolien)."""
    n = len(slides)
    if not n:
        return []
    secs = [i for i, s in enumerate(slides)
            if str(s.get("layout", "")).lower() in ("title", "section")]
    others = [i for i in range(n) if i not in secs]
    val = images
    if isinstance(val, str):
        v = val.strip().lower()
        if v.isdigit():
            val = int(v)
        elif v in ("all", "alle"):
            return list(range(n))
        elif v in ("none", "keine", "ohne", "kein"):
            return []
        else:
            return secs if secs else [0]
    if isinstance(val, bool):
        return secs if secs else [0]
    if isinstance(val, (int, float)):
        k = max(0, min(int(val), n))
        return sorted((secs + others)[:k])
    return secs if secs else [0]


async def _illustrated_presentation_generator(body: dict):
    topic = str(body.get("topic", "") or "").strip()
    if not topic:
        yield _sse({"type": "error", "message": "Kein Thema angegeben."})
        return
    images = body.get("images", "sections")
    style = str(body.get("style", "") or "").strip()
    model = _pick_model(body.get("model"), _model_for("general"))
    image_model = str(body.get("image_model", "") or "")
    tok = {"in": 0, "out": 0}
    yield _sse({"type": "pres_start", "topic": topic})
    # 1. Entwurf zum Thema
    try:
        draft, _ti, _to = await _presentation_draft(topic, model)
        tok["in"] += _ti
        tok["out"] += _to
    except httpx.ConnectError:
        yield _sse({"type": "error", "message": "Ollama nicht erreichbar – läuft der lokale Server?"})
        return
    except httpx.HTTPStatusError as e:
        yield _sse({"type": "error", "message": f"Modell abgelehnt (num_ctx/VRAM?): HTTP {getattr(e.response,'status_code',0)}"})
        return
    except Exception as e:
        yield _sse({"type": "error", "message": f"Entwurf fehlgeschlagen: {e}"})
        return
    data = await _text_to_presentation(draft, model, tok=tok)
    if not data or not data.get("slides"):
        yield _sse({"type": "error", "message": "Konnte aus dem Thema keine Folien ableiten."})
        return
    slides = data["slides"]
    yield _sse({"type": "structure", "count": len(slides), "title": data.get("title", "")})

    # 2. Bilder erzeugen (sequenziell – die lokale Brücke arbeitet ohnehin seriell).
    sel = _select_image_slides(slides, images)
    if sel:
        _pm = _pick_model(_model_for("general"))
        for _n, idx in enumerate(sel):
            slide = slides[idx]
            yield _sse({"type": "slide_image_start", "index": idx, "n": _n + 1,
                        "total": len(sel), "title": slide.get("title", "")})
            try:
                _bul = slide.get("bullets") or (
                    [l for l in str(slide.get("left", "")).split("\n") if l.strip()]
                    if slide.get("left") else [])
                _cnt = slide.get("content") or slide.get("subtitle") or ""
                _ip, _pi, _po = await _slide_image_prompt(slide.get("title", ""), _bul, _cnt, _pm, style)
                tok["in"] += _pi
                tok["out"] += _po
                _img = await _generate_image_core(_ip, "", "square", image_model)
                _im = _img.get("image", "")
                if not slide.get("left"):
                    slide["left"] = "\n".join(_bul) if _bul else _cnt
                slide["image_right"] = _im
                slide["image"] = _im
                slide["layout"] = "two-column"
                yield _sse({"type": "slide_image_done", "index": idx, "n": _n + 1})
            except HTTPException as he:
                yield _sse({"type": "notice", "message": f"Folie {idx + 1}: Bild nicht erzeugt – {he.detail}"})
            except Exception as e:
                yield _sse({"type": "notice", "message": f"Folie {idx + 1}: Bild nicht erzeugt – {e}"})

    data["tokens"] = tok
    yield _sse({"type": "done", "presentation": data, "tokens": tok, "images_done": len(sel)})


@router.post("/api/presentation/illustrated")
async def presentation_illustrated(req: Request):
    """Illustrierter Präsentationsassistent (SSE). Body ``{topic, images?, style?,
    model?, image_model?}``; ``images`` = 'sections' (Standard = Titel+Abschnitte) /
    'all' / 'none' / Zahl N. Streamt ``pres_start``/``structure``/``slide_image_start``/
    ``slide_image_done``/``notice``/``done`` (``done.presentation`` = fertiges Canvas-JSON
    mit ``image_right`` je Bildfolie). Bilder über ``_generate_image_core`` (Profil-Bildmodell,
    Geheim/Hartman → lokal). Token-Label „Präsentationsassistent"."""
    body = await req.json()
    return StreamingResponse(_illustrated_presentation_generator(body), media_type="text/event-stream")


@router.post("/api/image-to-prompt")
async def image_to_prompt(req: Request):
    """Bild → Text-zu-Bild-Prompt (Vision-Modell). Body ``{image (base64/Data-URI),
    model?, style?}`` → ``{prompt, tokens}``. Nutzt dieselbe Vision-Plumbing wie
    ``/api/analyze-image``; lokal-fähig (Geheim/Hartman: ``_pick_model`` coerct lokal)."""
    from tools.imaging import downscale
    body = await req.json()
    image_b64 = body.get("image") or ""
    if not image_b64:
        raise HTTPException(400, "Kein Bild übergeben")
    style = str(body.get("style", "") or "").strip()
    _model = _pick_model(body.get("model"))
    small = downscale(image_b64)
    _sys = ("Du bist Prompt-Designer für Text-zu-Bild-Modelle. Beschreibe das gezeigte Bild "
            "als EINEN kompakten, bildhaften Prompt (Motiv, Stil/Medium, Komposition/Perspektive, "
            "Licht, Farben, Stimmung, Detailgrad), mit dem ein Text-zu-Bild-Modell ein ähnliches "
            "Bild erzeugen könnte. KEINE Aufzählung, kein Vorspann, nur den Prompt (max. ~60 Wörter).")
    _user = "Erzeuge den Bild-Prompt für dieses Bild." + (f" Zielstil: {style}." if style else "")
    _tok = {"in": 0, "out": 0}
    async with _model_session(_model), httpx.AsyncClient(timeout=180) as client:
        resp = await _llm.chat(client, {
            "model": _model, "think": False, "stream": False,
            "messages": [{"role": "system", "content": _sys},
                         {"role": "user", "content": _user, "images": [small]}],
            "options": {"num_ctx": _profile_num_ctx(), "num_predict": 220},
            "keep_alive": KEEP_ALIVE,
        })
        resp.raise_for_status()
    _j = resp.json()
    _p = (_j.get("message", {}) or {}).get("content", "") or ""
    _p = re.sub(r"<think>.*?</think>", "", _p, flags=re.DOTALL).strip().strip('"').strip()
    _ti, _to = _llm_tok(_j)
    _tok["in"] += _ti
    _tok["out"] += _to
    if not _p:
        raise HTTPException(502, "Kein Prompt erzeugt (Vision-Modell lieferte nichts).")
    return {"prompt": _p[:800], "tokens": _tok}


# ── Geführter, recherche-gestützter Präsentationsassistent ────────────────────
# Interview (Zielgruppe/Ziel/Umfang) → schlüssige Gliederung + Inhaltsverzeichnis →
# je Gliederungspunkt eine Webrecherche, zusammengefasst als Folieninhalt → Bilder:
# flächiges Deckblatt + Abschlussfolie mit Bild; Inhaltsfolien bekommen ein Bild,
# wenn die KI es für sinnvoll hält (mit Zufallskomponente) → dann zweispaltig.


async def _pres_structure(topic: str, audience: str, goal: str, count: int,
                          model: str, tok: dict) -> dict:
    """LLM → schlüssige Gliederung als JSON: ``{title, subtitle, sections:[{title,
    illustrate}], closing:{title, subtitle}}``."""
    _sys = "Du planst schlüssige, logisch aufgebaute Präsentationen. Antworte NUR mit JSON."
    _user = (
        f"Plane eine Präsentation.\nThema: {topic}\n"
        + (f"Zielgruppe: {audience}\n" if audience else "")
        + (f"Ziel/Zweck: {goal}\n" if goal else "")
        + f"Erzeuge eine schlüssige Gliederung mit genau {count} Inhaltsabschnitten, die "
        "logisch aufeinander aufbauen. Antworte NUR mit JSON in diesem Format:\n"
        '{"title":"Haupttitel","subtitle":"knapper Untertitel",'
        '"sections":[{"title":"Abschnittstitel","illustrate":true}],'
        '"closing":{"title":"Abschlusstitel","subtitle":"Kernbotschaft/Ausblick"}}\n'
        'Setze illustrate=true nur, wenn ein illustratives Bild den Punkt sinnvoll '
        'unterstützt (anschauliche/konkrete Themen) – bei reinen Zahlen/Definitionen false. '
        "Sprache: Deutsch."
    )
    async with _model_session(model), httpx.AsyncClient(timeout=180) as client:
        resp = await _llm.chat(client, {
            "model": model, "think": False, "format": "json", "stream": False,
            "messages": [{"role": "system", "content": _sys},
                         {"role": "user", "content": _user}],
            "options": {"num_ctx": _profile_num_ctx()},
            "keep_alive": KEEP_ALIVE,
        })
        resp.raise_for_status()
    _j = resp.json()
    _ti, _to = _llm_tok(_j)
    tok["in"] += _ti
    tok["out"] += _to
    raw = (_j.get("message", {}) or {}).get("content", "") or ""
    raw = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL)
    return _parse_llm_json(raw) or {}


async def _pres_section_content(topic: str, sec_title: str, audience: str,
                                srctext: str, model: str, tok: dict) -> list:
    """Rechercheergebnisse (oder Allgemeinwissen) → 3–5 Folien-Stichpunkte."""
    _sys = ("Du fasst Inhalt für EINE Präsentationsfolie zusammen. Antworte NUR mit JSON "
            '{"bullets":["…","…","…"]}: 3–5 knappe, sachliche Stichpunkte auf Deutsch, '
            "je höchstens ein kurzer Satz, kein Meta-Text. Stütze konkrete Angaben (Zahlen, "
            "Daten, Namen) nur auf die gegebenen Quellen; liegen keine vor, nutze "
            "Allgemeinwissen.")
    _user = (f"Thema: {topic}\nFolie/Abschnitt: {sec_title}\n"
             + (f"Zielgruppe: {audience}\n" if audience else "")
             + ("\nQuellen:\n" + srctext[:6000] if srctext else ""))
    async with _model_session(model), httpx.AsyncClient(timeout=180) as client:
        resp = await _llm.chat(client, {
            "model": model, "think": False, "format": "json", "stream": False,
            "messages": [{"role": "system", "content": _sys},
                         {"role": "user", "content": _user}],
            "options": {"num_ctx": _profile_num_ctx(), "num_predict": 500},
            "keep_alive": KEEP_ALIVE,
        })
        resp.raise_for_status()
    _j = resp.json()
    _ti, _to = _llm_tok(_j)
    tok["in"] += _ti
    tok["out"] += _to
    raw = (_j.get("message", {}) or {}).get("content", "") or ""
    raw = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL)
    d = _parse_llm_json(raw)
    bullets = []
    if isinstance(d, dict):
        b = d.get("bullets") or []
        bullets = [re.sub(r"^\s*[-•*]\s*", "", str(x)).strip() for x in b if str(x).strip()]
    return bullets[:6]


async def _guided_presentation_generator(body: dict):
    import random
    topic = str(body.get("topic", "") or "").strip()
    if not topic:
        yield _sse({"type": "error", "message": "Kein Thema angegeben."})
        return
    audience = str(body.get("audience", "") or "").strip()
    goal = str(body.get("goal", "") or "").strip()
    try:
        count = int(body.get("count") or 5)
    except Exception:
        count = 5
    count = max(3, min(count, 10))
    want_web = bool(body.get("web", True))
    image_mode = str(body.get("image_mode", "smart") or "smart").lower()   # smart|all|none
    image_wishes = str(body.get("image_wishes", "") or "").strip()
    style = str(body.get("style", "") or "").strip()
    image_model = str(body.get("image_model", "") or "")
    tok = {"in": 0, "out": 0}

    _rmodel, _rerr = await _research_model(body.get("model"))
    if _rerr or not _rmodel:
        yield _sse({"type": "error", "message": _rerr or "Kein Modell verfügbar."})
        return
    web_ok = want_web and _web_search_allowed()
    yield _sse({"type": "pres_start", "topic": topic})
    if want_web and not _web_search_allowed():
        yield _sse({"type": "notice", "message": "Websuche gesperrt (Hartman-Modus) – Inhalte ohne Recherche."})

    # Vorab-Prüfung: Bildgenerierung lokal (local::sd)? Server bei Bedarf selbst starten
    # (Z-Image-Brücke). Klappt das nicht, EINMAL klar melden und Bilder überspringen
    # (statt je Folie zu scheitern).
    if image_mode != "none":
        _im = image_model or _image_model()
        _is_local_img = _im == "local::sd" or (bool(_im) and not _llm.is_remote(_im))
        if _is_local_img:
            yield _sse({"type": "notice", "message": "Prüfe/­starte den lokalen Bild-Server…"})
            if not await _ensure_sd_server():
                yield _sse({"type": "notice", "message":
                            "Bild-Server nicht erreichbar und Auto-Start nicht möglich – bitte "
                            "z-image/sd_server.bat starten (Profil-Adresse: " + (_sd_url() or "—")
                            + "). Die Folien werden ohne Bilder erstellt."})
                image_mode = "none"

    # 1. Struktur / Inhaltsverzeichnis
    try:
        struct = await _pres_structure(topic, audience, goal, count, _rmodel, tok)
    except httpx.ConnectError:
        yield _sse({"type": "error", "message": "Ollama nicht erreichbar – läuft der lokale Server?"})
        return
    except Exception as e:
        yield _sse({"type": "error", "message": f"Gliederung fehlgeschlagen: {e}"})
        return
    title = str(struct.get("title") or topic).strip()
    subtitle = str(struct.get("subtitle") or "").strip()
    sections = [s for s in (struct.get("sections") or [])
                if isinstance(s, dict) and str(s.get("title", "")).strip()][:count]
    if not sections:
        sections = [{"title": topic, "illustrate": True}]
    closing = struct.get("closing") or {}
    yield _sse({"type": "structure", "title": title,
                "toc": [str(s["title"]).strip() for s in sections]})

    # 2. Folien zusammenbauen: Deckblatt, Inhaltsverzeichnis, Inhaltsfolien (+ Recherche), Abschluss
    cover = {"layout": "title", "title": title, "content": subtitle}
    toc_slide = {"layout": "bullets", "title": "Inhalt",
                 "bullets": [str(s["title"]).strip() for s in sections]}
    slides = [cover, toc_slide]
    content_slides = []
    for si, sec in enumerate(sections):
        sec_title = str(sec["title"]).strip()
        srcs, srctext = [], ""
        if web_ok:
            yield _sse({"type": "researching", "index": si, "query": sec_title})
            try:
                from tools.search import search_with_sources
                srcs, srctext = await search_with_sources(f"{topic} {sec_title}", 5)
            except Exception:
                srcs, srctext = [], ""
            yield _sse({"type": "research_done", "index": si, "count": len(srcs or [])})
        try:
            bullets = await _pres_section_content(topic, sec_title, audience, srctext, _rmodel, tok)
        except Exception:
            bullets = []
        if not bullets:
            bullets = [f"(Kein Inhalt zu „{sec_title}“ ermittelt)"]
        sl = {"layout": "bullets", "title": sec_title, "bullets": bullets}
        if srcs:
            sl["sources"] = [{"title": x.get("title", ""), "url": x.get("url", "")} for x in srcs[:4]]
        # Bild-Entscheidung: KI-Flag + Zufall (bzw. alle/keine).
        if image_mode == "all":
            want_img = True
        elif image_mode == "none":
            want_img = False
        else:
            want_img = bool(sec.get("illustrate", True)) and (random.random() < 0.6)
        sl["_want_image"] = want_img
        slides.append(sl)
        content_slides.append(sl)
        yield _sse({"type": "section_done", "index": si, "title": sec_title,
                    "bullets": bullets, "image": want_img})

    close_title = str(closing.get("title") or "Fazit").strip()
    close_sub = str(closing.get("subtitle") or "").strip()
    closing_slide = {"layout": "title", "title": close_title, "content": close_sub}
    slides.append(closing_slide)

    data = {"type": "presentation", "title": title, "theme": "dark", "slides": slides}

    # 3. Bilder: Deckblatt (flächig, landscape), gewählte Inhaltsfolien (square, zweispaltig),
    #    Abschluss (flächig). Sequenziell – die lokale Brücke arbeitet ohnehin seriell.
    _pm = _pick_model(_model_for("general"))
    _wish = (style + (" · " + image_wishes if image_wishes else "")).strip(" ·")
    targets = []
    if image_mode != "none":
        targets.append(("cover", cover))
    targets += [("content", sl) for sl in content_slides if sl.get("_want_image")]
    if image_mode != "none":
        targets.append(("closing", closing_slide))
    total = len(targets)
    for _n, (kind, sl) in enumerate(targets):
        yield _sse({"type": "slide_image_start", "n": _n + 1, "total": total,
                    "kind": kind, "title": sl.get("title", "")})
        try:
            if kind in ("cover", "closing"):
                _basis = subtitle if kind == "cover" else close_sub
                _ip, _pi, _po = await _slide_image_prompt(sl.get("title", ""), [], _basis, _pm, _wish)
                tok["in"] += _pi
                tok["out"] += _po
                _img = await _generate_image_core(_ip, "", "landscape", image_model)
                sl["image"] = _img.get("image", "")     # flächig über das title-Layout
            else:
                _bul = sl.get("bullets") or []
                _ip, _pi, _po = await _slide_image_prompt(sl.get("title", ""), _bul, "", _pm, _wish)
                tok["in"] += _pi
                tok["out"] += _po
                _img = await _generate_image_core(_ip, "", "square", image_model)
                _im = _img.get("image", "")
                if not sl.get("left"):
                    sl["left"] = "\n".join(_bul)
                sl["image_right"] = _im
                sl["image"] = _im
                sl["layout"] = "two-column"
            yield _sse({"type": "slide_image_done", "n": _n + 1})
        except HTTPException as he:
            yield _sse({"type": "notice", "message": f"Bild „{sl.get('title','')}“ nicht erzeugt – {he.detail}"})
        except Exception as e:
            yield _sse({"type": "notice", "message": f"Bild „{sl.get('title','')}“ nicht erzeugt – {e}"})

    for sl in slides:
        sl.pop("_want_image", None)
    data["tokens"] = tok
    yield _sse({"type": "done", "presentation": data, "tokens": tok, "images_done": total})


@router.post("/api/presentation/guided")
async def presentation_guided(req: Request):
    """Geführter, recherche-gestützter Präsentationsassistent (SSE). Body
    ``{topic, audience?, goal?, count?, web?, image_mode?, image_wishes?, style?,
    model?, image_model?}``. ``image_mode`` = 'smart' (KI-Entscheidung + Zufall) /
    'all' / 'none'. Ablauf: Gliederung → Inhaltsverzeichnis → je Punkt Webrecherche
    (``search_with_sources``) + Zusammenfassung → Bilder (flächiges Deckblatt + Abschluss,
    Inhaltsfolien zweispaltig). Streamt ``pres_start``/``structure``/``researching``/
    ``research_done``/``section_done``/``slide_image_start``/``slide_image_done``/
    ``notice``/``done``. Token-Label „Präsentationsassistent"."""
    body = await req.json()
    return StreamingResponse(_guided_presentation_generator(body), media_type="text/event-stream")


# ── Präsentation AUS hochgeladenen Bildern (Chat: /praesentation im Bildmodus) ─
# Reihe von Bildern → je Bild Vision-Analyse (Dateiname-Hinweis) → Zweispalter-Folie
# (Bild | Text), optional Intro aus .md/.txt, optional Mermaid-Übersicht, Cover/Abschluss
# je Quelle (hochgeladen | generiert | Text). Nutzt den geteilten Vision-Kern und bleibt
# lokal (Geheim/Hartman). Mermaid wird im Frontend zu PNG gerastert (export-fest).

_PRES_STYLE_FRAMING = {
    "technisch": "Formuliere sachlich-präzise und technisch fundiert.",
    "sozial": "Formuliere empathisch, verständlich und menschenzentriert.",
    "wissenschaftlich": "Formuliere nüchtern und präzise, mit Fachbegriffen.",
    "marketing": "Formuliere prägnant, nutzenorientiert und überzeugend.",
    "schlicht": "Formuliere schlicht, klar und knapp.",
    "kreativ": "Formuliere anschaulich, bildhaft und originell.",
}


def _pres_persona(style: str, address: str, audience: str) -> str:
    """Deterministischer System-Prompt für die Bild-Beschreibung aus Stil + Anrede + Zielgruppe."""
    parts = ["Du bist ein Fach-Experte und beschreibst Bilder für eine Präsentationsfolie."]
    s = (style or "").strip().lower()
    if s in _PRES_STYLE_FRAMING:
        parts.append(_PRES_STYLE_FRAMING[s])
    elif style:
        parts.append(f"Stil: {style}.")
    if audience:
        parts.append(f"Zielgruppe: {audience} – wähle Ansprache und Tiefe passend.")
    a = (address or "").strip().lower()
    if a.startswith("sie"):
        parts.append("Verwende die höfliche Anrede „Sie“.")
    elif a.startswith("du"):
        parts.append("Verwende die persönliche Anrede „Du“.")
    parts.append("Antworte knapp und sachlich auf Deutsch.")
    return " ".join(parts)


async def _pres_intro_bullets(topic: str, doc_text: str, persona: str,
                              model: str, tok: dict) -> list:
    """Einleitungsfolie aus Thema/Belegmaterial → 3–5 Stichpunkte (ein LLM-Aufruf)."""
    if not (topic or doc_text):
        return []
    _sys = (persona + " Du schreibst die Einleitungsfolie einer Präsentation. Antworte NUR mit JSON "
            '{"bullets":["…","…","…"]}: 3–5 knappe Stichpunkte, kein Markdown, kein Vorspann.')
    _user = ((f"Thema: {topic}\n" if topic else "")
             + (("Belegmaterial:\n" + doc_text[:4000]) if doc_text else ""))
    try:
        async with _model_session(model), httpx.AsyncClient(timeout=120) as client:
            resp = await _llm.chat(client, {
                "model": model, "think": False, "format": "json", "stream": False,
                "messages": [{"role": "system", "content": _sys},
                             {"role": "user", "content": _user}],
                "options": {"num_ctx": _profile_num_ctx(), "num_predict": 400},
                "keep_alive": KEEP_ALIVE,
            })
            resp.raise_for_status()
        _j = resp.json()
        _ti, _to = _llm_tok(_j)
        tok["in"] += _ti
        tok["out"] += _to
        raw = re.sub(r"<think>.*?</think>", "", (_j.get("message", {}) or {}).get("content", "") or "", flags=re.DOTALL)
        d = _parse_llm_json(raw)
        b = (d or {}).get("bullets") if isinstance(d, dict) else None
        return [re.sub(r"^\s*[-•*]\s*", "", str(x)).strip() for x in (b or []) if str(x).strip()][:5]
    except Exception:
        return []


async def _pres_mermaid_defs(context: str, count: int, model: str, tok: dict) -> list:
    """0–2 valide Mermaid-Definitionen als Übersicht (ein LLM-Aufruf). Rückgabe:
    ``[{title, mermaid}]``. Das Frontend rastert die Definition zu einem Bild."""
    count = max(1, min(int(count or 1), 2))
    _sys = ("Du erstellst valide Mermaid-Diagramme (flowchart TD ODER graph LR) als Übersicht "
            "zu einem Präsentationsinhalt. Antworte NUR mit JSON "
            '{"diagrams":[{"title":"kurzer Titel","mermaid":"flowchart TD\\n  A[Start]-->B[Ende]"}]}. '
            f"Höchstens {count} Diagramm(e). Verwende in Knotentexten NUR einfache ASCII-Wörter "
            "(keine Anführungszeichen, Klammern, Sonderzeichen); im Feld mermaid nur die reine "
            "Mermaid-Definition mit \\n als Zeilentrenner.")
    try:
        async with _model_session(model), httpx.AsyncClient(timeout=150) as client:
            resp = await _llm.chat(client, {
                "model": model, "think": False, "format": "json", "stream": False,
                "messages": [{"role": "system", "content": _sys},
                             {"role": "user", "content": f"Inhalt:\n{context[:4000]}"}],
                "options": {"num_ctx": _profile_num_ctx(), "num_predict": 500},
                "keep_alive": KEEP_ALIVE,
            })
            resp.raise_for_status()
        _j = resp.json()
        _ti, _to = _llm_tok(_j)
        tok["in"] += _ti
        tok["out"] += _to
        raw = re.sub(r"<think>.*?</think>", "", (_j.get("message", {}) or {}).get("content", "") or "", flags=re.DOTALL)
        d = _parse_llm_json(raw)
        out = []
        for it in ((d or {}).get("diagrams") or [])[:count]:
            if isinstance(it, dict) and str(it.get("mermaid", "")).strip():
                out.append({"title": str(it.get("title") or "Übersicht").strip(),
                            "mermaid": str(it["mermaid"]).strip()})
        return out
    except Exception:
        return []


async def _images_presentation_generator(body: dict):
    images = body.get("images") or []
    images = [im for im in images if isinstance(im, dict) and im.get("data")]
    if not images:
        yield _sse({"type": "error", "message": "Keine Bilder übergeben."})
        return
    topic = str(body.get("topic", "") or "").strip()
    audience = str(body.get("audience", "") or "").strip()
    address = str(body.get("address", "") or "").strip()
    style = str(body.get("style", "") or "").strip()
    doc_text = str(body.get("doc_text", "") or "").strip()
    cover_source = str(body.get("cover_source", "generate") or "generate").strip().lower()
    want_mermaid = bool(body.get("want_mermaid"))
    mermaid_count = int(body.get("mermaid_count") or 2)
    want_notes = bool(body.get("want_notes"))
    image_wishes = str(body.get("image_wishes", "") or "").strip()
    image_model = str(body.get("image_model", "") or "")
    tok = {"in": 0, "out": 0}

    model = await _vision_model(body.get("model"))
    if not model:
        yield _sse({"type": "error", "message": "Kein lokales multimodales Modell verfügbar – die Bild-Präsentation braucht ein Vision-Modell (z. B. „qwen2.5-vl“/„llava“ in Ollama)."})
        return

    persona = _pres_persona(style, address, audience)
    yield _sse({"type": "pres_start", "topic": topic or "Bildpräsentation"})

    # 1. Je Bild analysieren → Zweispalter-Folie (Original-Bild rechts)
    content_slides = []
    captions = []
    first_title = ""
    total = len(images)
    for i, im in enumerate(images):
        name = str(im.get("name", "") or "")
        data_uri = str(im.get("data", "") or "")
        yield _sse({"type": "analyzing", "n": i + 1, "total": total, "name": name})
        try:
            a = await _analyze_image_core(data_uri, system_prompt=persona, filename=name,
                                          topic=topic, model=model, want_notes=want_notes)
            _t = a.get("tokens") or {}
            tok["in"] += _t.get("in", 0)
            tok["out"] += _t.get("out", 0)
        except Exception as e:
            yield _sse({"type": "notice", "message": f"Bild {i + 1} ({name}): Analyse fehlgeschlagen – {e}"})
            a = {"title": name or "Abbildung", "bullets": [], "caption": ""}
        lines = list(a.get("bullets") or [])
        if a.get("caption"):
            lines.append(a["caption"])
        sl = {"layout": "two-column", "title": a.get("title") or "Abbildung",
              "left": "\n".join(lines), "image_right": data_uri}
        if want_notes and a.get("notes"):
            sl["notes"] = a["notes"]
        content_slides.append(sl)
        if a.get("caption"):
            captions.append(a["caption"])
        if not first_title:
            first_title = a.get("title") or ""
        yield _sse({"type": "image_done", "n": i + 1, "title": sl["title"]})

    title = topic or first_title or "Präsentation"
    subtitle = audience and f"Für {audience}" or ""

    # 2. Optionale Intro-Folie aus Thema/Belegmaterial
    intro_slide = None
    if topic or doc_text:
        yield _sse({"type": "notice", "message": "Einleitung wird formuliert…"})
        ib = await _pres_intro_bullets(topic, doc_text, persona, model, tok)
        if ib:
            intro_slide = {"layout": "bullets", "title": "Über diese Präsentation", "bullets": ib}

    # 3. Optionale Mermaid-Übersicht (Frontend rastert → Bild)
    diagram_slides = []
    if want_mermaid and mermaid_count > 0:
        yield _sse({"type": "notice", "message": "Diagramm-Übersicht wird abgeleitet…"})
        ctx = (("Thema: " + topic + "\n") if topic else "") \
            + ("Belegmaterial: " + doc_text[:2000] + "\n" if doc_text else "") \
            + "Bildinhalte:\n" + "\n".join(f"- {c}" for c in captions[:20])
        for dg in await _pres_mermaid_defs(ctx, mermaid_count, model, tok):
            diagram_slides.append({"layout": "two-column", "title": dg["title"],
                                   "left": "Übersicht", "mermaid": dg["mermaid"]})

    # 4. Cover / Abschluss je Quelle
    async def _gen_slide_image(_title, _basis):
        _pm = _pick_model(_model_for("general"))
        _wish = (style + (" · " + image_wishes if image_wishes else "")).strip(" ·")
        _ip, _pi, _po = await _slide_image_prompt(_title, [], _basis, _pm, _wish)
        tok["in"] += _pi
        tok["out"] += _po
        _img = await _generate_image_core(_ip, "", "landscape", image_model)
        return _img.get("image", "")

    close_title = "Vielen Dank"
    if cover_source == "generate":
        # Lokalen Bild-Server bei Bedarf starten (Z-Image-Brücke), sonst Rückfall auf „uploaded"/Text.
        _im = image_model or _image_model()
        _is_local_img = _im == "local::sd" or (bool(_im) and not _llm.is_remote(_im))
        if _is_local_img and not await _ensure_sd_server():
            yield _sse({"type": "notice", "message": "Bild-Server nicht erreichbar – Start/Abschluss aus hochgeladenen Bildern."})
            cover_source = "uploaded"

    cover = {"layout": "title", "title": title}
    if subtitle:
        cover["content"] = subtitle
    closing = {"layout": "title", "title": close_title}
    if cover_source == "uploaded":
        cover["image"] = str(images[0].get("data", "") or "")
        closing["image"] = str(images[-1].get("data", "") or "")
    elif cover_source == "generate":
        yield _sse({"type": "slide_image_start", "n": 1, "total": 2, "kind": "cover", "title": title})
        try:
            cover["image"] = await _gen_slide_image(title, subtitle)
            yield _sse({"type": "slide_image_done", "n": 1})
        except Exception as e:
            yield _sse({"type": "notice", "message": f"Deckblatt-Bild nicht erzeugt – {e}"})
        yield _sse({"type": "slide_image_start", "n": 2, "total": 2, "kind": "closing", "title": close_title})
        try:
            closing["image"] = await _gen_slide_image(close_title, title)
            yield _sse({"type": "slide_image_done", "n": 2})
        except Exception as e:
            yield _sse({"type": "notice", "message": f"Abschluss-Bild nicht erzeugt – {e}"})

    # 5. Folien zusammenstellen: Cover → Intro → Bildfolien → Diagramme → Abschluss
    slides = [cover]
    if intro_slide:
        slides.append(intro_slide)
    slides += content_slides
    slides += diagram_slides
    slides.append(closing)

    data = {"type": "presentation", "title": title, "theme": "dark", "slides": slides,
            "tokens": tok}
    yield _sse({"type": "structure", "count": len(slides), "title": title})
    yield _sse({"type": "done", "presentation": data, "tokens": tok})


@router.post("/api/presentation/from-images")
async def presentation_from_images(req: Request):
    """Baut aus hochgeladenen Bildern eine Präsentation (SSE). Body ``{images:[{name,data}],
    doc_text?, topic?, audience?, address, style?, cover_source(uploaded|generate|text),
    want_mermaid, mermaid_count?, want_notes, image_wishes?, model?, image_model?}``. Je Bild
    Vision-Analyse (Dateiname-Hinweis) → Zweispalter-Folie; optional Intro/Mermaid; Cover/Abschluss
    je Quelle. Braucht ein lokales multimodales Modell (``_vision_model``). Mermaid-Folien tragen ein
    ``mermaid``-Feld, das das Frontend zu einem Bild rastert. Token-Label „Präsentationsassistent"."""
    body = await req.json()
    return StreamingResponse(_images_presentation_generator(body), media_type="text/event-stream")






# ── Dossiers + Präsentation-from-text/slide-image (später eigener Router) ────


@router.get("/api/dossiers")
async def list_dossiers():
    """Listet die automatisch erzeugten Planer-Recherche-Dossiers (.md) auf –
    als wählbares Quellmaterial im Dokumentengenerator."""
    items = []
    if DOSSIERS_DIR.exists():
        for fp in sorted(DOSSIERS_DIR.rglob("*.md")):
            items.append({
                "id": fp.relative_to(DOSSIERS_DIR).as_posix(),
                "name": fp.stem.replace("_", " "),
                "plan": fp.parent.name.replace("_", " "),
            })
    return items


@router.get("/api/dossiers/load")
async def load_dossier(id: str = Query(...)):
    """Inhalt eines Dossiers (mit Pfad-Traversal-Schutz)."""
    target = (DOSSIERS_DIR / id).resolve()
    try:
        target.relative_to(DOSSIERS_DIR.resolve())
    except ValueError:
        raise HTTPException(400, "Ungültiger Pfad")
    if not target.exists() or target.suffix != ".md":
        raise HTTPException(404)
    return {"name": target.stem.replace("_", " "),
            "content": target.read_text(encoding="utf-8")}




@router.post("/api/presentation/from-text")
async def presentation_from_text(req: Request):
    """Wandelt fertigen Text (z. B. aus dem Dokumentengenerator) in eine
    Canvas-Präsentation um — ohne Konversation. Gibt das Canvas-JSON zurück,
    das der Frontend-Renderer direkt anzeigen kann."""
    body = await req.json()
    text = str(body.get("text", "")).strip()
    if not text:
        raise HTTPException(status_code=400, detail="Kein Text übergeben")
    model = _pick_model(body.get("model"))
    _tok = {"in": 0, "out": 0}
    data = await _text_to_presentation(text, model, tok=_tok)
    if not data:
        raise HTTPException(status_code=422,
                            detail="Konnte aus dem Text keine Folien ableiten")
    data["tokens"] = _tok
    return data


async def _slide_image_prompt(title: str, bullets: list, content: str,
                              model: str, style: str = "") -> tuple:
    """Leitet aus dem Folientext EINEN kompakten, visuellen Bild-Prompt ab
    (ein kurzer LLM-Aufruf; robuster deterministischer Rückfall). Der Prompt
    beschreibt eine *Szene/Illustration* zum Folienthema – KEIN Text-im-Bild,
    keine Aufzählung. Token-sparsam. Gibt ``(prompt, tok_in, tok_out)`` zurück.

    Geheim-/Hartman-Modus: ``model`` ist bereits lokal gecoerct (Aufrufer nutzt
    ``_model_for('general')``), daher rein lokal."""
    _txt = " · ".join([str(title or "")] +
                      [str(b) for b in (bullets or [])] +
                      ([str(content)] if content else [])).strip(" ·")
    _style = str(style or "").strip()
    # Deterministischer Rückfall (falls LLM scheitert / kein Modell): Thema + Stil.
    _fallback = (f"{title or _txt[:80]}, professionelle Illustration"
                 + (f", {_style}" if _style else ", moderner Business-Stil")
                 + ", hochwertig, keine Schrift, kein Text").strip()
    if not model or not _txt:
        return _fallback, 0, 0
    _sys = ("Du bist Prompt-Designer für ein Text-zu-Bild-Modell. Formuliere aus "
            "dem Folieninhalt EINEN einzigen, bildhaften englischen ODER deutschen "
            "Prompt (max. ~40 Wörter) für EIN illustratives Bild zur Folie: eine "
            "konkrete Szene/Metapher/Illustration, KEINE Aufzählung, KEIN Text im "
            "Bild. Nur den Prompt ausgeben, ohne Anführungszeichen."
            + (f" Stil: {_style}." if _style else ""))
    try:
        async with _model_session(model), httpx.AsyncClient(timeout=120) as client:
            resp = await _llm.chat(client, {
                "model": model, "think": False, "stream": False,
                "messages": [{"role": "system", "content": _sys},
                             {"role": "user", "content": _txt[:1200]}],
                "options": {"num_ctx": _profile_num_ctx(), "num_predict": 120},
                "keep_alive": KEEP_ALIVE,
            })
            resp.raise_for_status()
        _j = resp.json()
        _p = (_j.get("message", {}) or {}).get("content", "") or ""
        _p = re.sub(r"<think>.*?</think>", "", _p, flags=re.DOTALL).strip().strip('"').strip()
        _ti, _to = _llm_tok(_j)
        if _p and len(_p) > 8:
            if _style and _style.lower() not in _p.lower():
                _p = f"{_p}, {_style}"
            return _p[:600], _ti, _to
        return _fallback, _ti, _to
    except Exception:
        return _fallback, 0, 0


@router.post("/api/presentation/slide-image")
async def presentation_slide_image(req: Request):
    """Erzeugt EIN KI-Bild für eine Präsentationsfolie: leitet aus dem Folientext
    einen Bild-Prompt ab und ruft die konfigurierte Bildgenerierung (lokal SD-WebUI
    oder API-Bildmodell). Antwort: ``{image, prompt}`` (data-URI). Frontend setzt es
    als ``image_right`` + Layout ``two-column``. Geheim-/Hartman-Modus: nur lokal
    (siehe ``_generate_image_core``, sonst 409). Bild ≠ Token-Strom, aber die
    Prompt-Ableitung meldet Tokens im Feld ``tokens`` (Label „Präsentationsbild")."""
    body = await req.json()
    title = str(body.get("title", "") or "")
    bullets = body.get("bullets") or []
    if isinstance(bullets, str):
        bullets = [b for b in bullets.split("\n") if b.strip()]
    content = str(body.get("content", "") or "")
    preset = str(body.get("preset", "square") or "square")
    style = str(body.get("style", "") or "")
    given = str(body.get("prompt", "") or "").strip()

    _tok = {"in": 0, "out": 0}
    if given:
        prompt = given
    else:
        # Prompt-Ableitung mit lokal-gecoerctem Textmodell (Geheim/Hartman-fest).
        _pm = _pick_model(_model_for("general"))
        prompt, _ti, _to = await _slide_image_prompt(title, bullets, content, _pm, style)
        _tok["in"] += _ti
        _tok["out"] += _to

    result = await _generate_image_core(prompt, "", preset, str(body.get("model", "") or ""))
    result["prompt"] = prompt
    result["tokens"] = _tok
    return result

