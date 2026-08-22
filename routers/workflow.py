"""Router: Arbeitsablauf im Chat (/api/workflow, /api/downloads)

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


# ── Arbeitsablauf im Chat (mehrstufig, Zwischenergebnisse) ────────────────────
# Der Nutzer gibt nummerierte Schritte ein („1. … 2. …"). Jeder Schritt wird als
# fokussierte Teilaufgabe ausgeführt; die bisherigen Ergebnisse fließen als
# Kontext in den nächsten Schritt. Am Ende führt ein Synthese-Schritt alles zu
# einem Gesamtergebnis zusammen. Rein LLM-basiert (robust auch für kleinere
# Modelle, kein Werkzeug-Loop). Modellrolle „general", Geheim/Hartman → lokal.
# Pro Schritt kann eine Tag-Angabe ``[lokal]`` / ``[api]`` / ``[web]`` (Kombis wie
# ``[lokal,web]``) das Modell und die Websuche steuern. Das Frontend parst die Tags und
# schickt Schritte als Objekte ``{text, mode, web}``; zur Robustheit akzeptieren wir hier
# auch nackte Strings (Tag im Text) und normalisieren beides.
_WF_TAG_RE = re.compile(r"^\s*\[([^\]]{1,40})\]\s*(.*)$", re.DOTALL)

# Ein Schritt kann statt Text auch ein Bild erzeugen ODER eine Sprachnachricht
# (Text wird vorgelesen). Erkennung über Tags ([bild]/[sprache]) ODER natürliche
# Sprache im Schritttext.
_WF_IMG_RE = re.compile(
    r"\b(erzeuge|erstelle|generiere|male|zeichne|entwirf|visualisiere|rendere|render|"
    r"generate|create|draw)\b[^.\n]{0,40}\b(bild|bilder|foto|photo|illustration|grafik|"
    r"grafiken|zeichnung|logo|poster|cover|image|picture)\b", re.I)
_WF_VOICE_RE = re.compile(
    r"\b(sprachnachricht|sprachausgabe|vertone|vorlesen|lies\b[^.\n]{0,30}\bvor|"
    r"als\s+(?:sprache|audio)|voice[- ]?message|read\s+aloud|text[- ]?to[- ]?speech|tts)\b", re.I)
# Imperativ-Vorspann eines Bild-Schritts entfernen -> übrig bleibt das Motiv als Prompt.
_WF_IMG_STRIP = re.compile(
    r"^\s*(?:bitte\s+)?(?:erzeuge|erstelle|generiere|male|zeichne|entwirf|visualisiere|"
    r"rendere|render|generate|create|draw)\b\s*(?:mir\s+)?(?:bitte\s+)?(?:ein(?:e|en)?\s+)?"
    # Nur generische „Bild"-Wörter als Vorspann entfernen. Logo/Poster/Cover sind
    # meist das Motiv selbst -> stehen lassen (nicht in dieser Liste).
    r"(?:bild|foto|photo|illustration|grafik|zeichnung|image|picture)s?\b"
    r"\s*(?:von|vom|of|mit|zu|:|,)?\s*", re.I)


def _wf_image_prompt(text: str) -> str:
    """Aus einem Bild-Schritt („generiere ein Bild von X") das Motiv X als Prompt."""
    p = _WF_IMG_STRIP.sub("", text or "").strip(" .:-–—")
    return p if len(p) >= 3 else (text or "").strip()


def _wf_normalize_step(s) -> dict:
    """Ein Schritt → ``{text, mode, web, kind}`` (``mode`` ∈ '' / 'local' / 'api';
    ``kind`` ∈ '' / 'image' / 'voice')."""
    if isinstance(s, dict):
        text = str(s.get("text", "") or "").strip()
        mode = str(s.get("mode", "") or "").strip().lower()
        web = bool(s.get("web", False))
        kind = str(s.get("kind", "") or "").strip().lower()
    else:
        text, mode, web, kind = str(s or "").strip(), "", False, ""
    m = _WF_TAG_RE.match(text)
    if m:  # Tag im Text (Fallback, falls das Frontend nicht geparst hat)
        toks = re.split(r"[,\s/+]+", m.group(1).lower())
        text = m.group(2).strip()
        for t in toks:
            if t in ("lokal", "local"):
                mode = "local"
            elif t in ("api", "remote", "cloud"):
                mode = "api"
            elif t in ("web", "recherche", "suche", "search", "internet"):
                web = True
            elif t in ("bild", "image", "img", "foto"):
                kind = "image"
            elif t in ("sprache", "stimme", "tts", "voice", "audio", "vorlesen"):
                kind = "voice"
    if kind not in ("image", "voice"):  # sonst aus dem Text ableiten
        if _WF_IMG_RE.search(text):
            kind = "image"
        elif _WF_VOICE_RE.search(text):
            kind = "voice"
        else:
            kind = ""
    if mode not in ("local", "api"):
        mode = ""
    return {"text": text, "mode": mode, "web": web, "kind": kind}


async def _workflow_generator(body: dict):
    steps = [_wf_normalize_step(s) for s in (body.get("steps") or [])]
    steps = [s for s in steps if s["text"]][:20]
    goal = str(body.get("goal", "") or "").strip()
    if not steps:
        yield _sse({"type": "error", "message": "Keine Schritte angegeben."})
        return
    base_model = _pick_model(body.get("model"), _model_for("general"))
    # API-Modell für ``[api]``-Schritte: nur ein echtes Remote-Modell und nur außerhalb
    # des Geheim-/Hartman-Modus (der alles lokal erzwingt).
    _api_raw = str(body.get("api_model", "") or "").strip()
    api_model = (_api_raw if (_api_raw and _api_raw not in _MODEL_PLACEHOLDERS
                              and _llm.is_remote(_api_raw) and not _secret_local()) else "")
    local_model = await _local_model(base_model)  # None, wenn kein lokales LLM da ist
    _ctx = _profile_num_ctx()
    _tok = {"in": 0, "out": 0}
    results = []  # [(step_text, result)]
    # Zeichenbudget für den mitgeführten Kontext (an num_ctx gekoppelt).
    _budget = max(2000, int((_ctx - 800) * 3.0))

    def _resolve_model(mode: str):
        """(Modell, Hinweis|None) für einen Schritt-Modus."""
        if mode == "local":
            if local_model:
                return local_model, None
            return base_model, "kein lokales Modell installiert – Standardmodell genutzt"
        if mode == "api":
            if api_model:
                return api_model, None
            if _secret_local():
                return (local_model or base_model), "Geheim-/Hartman-Modus: lokal statt API"
            return base_model, "kein API-Modell gewählt – Standardmodell genutzt"
        return base_model, None

    yield _sse({"type": "workflow_start", "count": len(steps)})

    async def _run(model: str, sys_prompt: str, user_prompt: str, num_predict: int):
        async with _model_session(model), httpx.AsyncClient(timeout=600) as client:
            resp = await _llm.chat(client, {
                "model": model, "think": False, "stream": False,
                "messages": [{"role": "system", "content": sys_prompt},
                             {"role": "user", "content": user_prompt}],
                "options": {"num_ctx": _ctx, "num_predict": num_predict},
                "keep_alive": KEEP_ALIVE,
            })
            resp.raise_for_status()
        _j = resp.json()
        _c = (_j.get("message", {}) or {}).get("content", "") or ""
        _c = re.sub(r"<think>.*?</think>", "", _c, flags=re.DOTALL).strip()
        _ti, _to = _llm_tok(_j)
        _tok["in"] += _ti
        _tok["out"] += _to
        return _c

    try:
        for i, step in enumerate(steps):
            _txt = step["text"]
            _kind = step.get("kind") or ""
            _model, _note = _resolve_model(step["mode"])
            yield _sse({"type": "step_start", "index": i, "total": len(steps),
                        "step": _txt, "model": _model, "kind": _kind,
                        "remote": _llm.is_remote(_model), "web": step["web"]})
            if _note:
                yield _sse({"type": "notice", "index": i, "message": _note})

            # ── Bild-Schritt: das eingestellte Bildmodell erzeugen lassen ────────
            # Nutzt den GEMEINSAMEN Kern _generate_image_core (wie Chat-🎨). Fehler
            # (kein Modell / Server aus) beenden den Ablauf NICHT, sondern werden als
            # Hinweis gemeldet; der Ablauf läuft weiter.
            if _kind == "image":
                _iprompt = _wf_image_prompt(_txt)
                yield _sse({"type": "generating_image", "index": i, "prompt": _iprompt})
                try:
                    _img = await _generate_image_core(_iprompt, preset="square")
                    yield _sse({"type": "image", "index": i,
                                "prompt": _iprompt, "image": _img.get("image", "")})
                    _rnote = f"🖼 Bild erzeugt: {_iprompt}"
                except HTTPException as _he:
                    _rnote = f"⚠ Bild nicht erzeugt: {_he.detail}"
                    yield _sse({"type": "notice", "index": i, "message": _rnote})
                except Exception as _e:
                    _rnote = f"⚠ Bild nicht erzeugt: {_e}"
                    yield _sse({"type": "notice", "index": i, "message": _rnote})
                results.append((_txt, _rnote))
                yield _sse({"type": "step_done", "index": i, "step": _txt, "result": _rnote})
                continue

            # Optionale Websuche für diesen Schritt (typisch: lokales Recherche-Modell
            # holt Quellen, die dann als Zwischenergebnis an ein API-Modell weitergehen).
            _web_ctx = ""
            if step["web"]:
                if _web_search_allowed():
                    yield _sse({"type": "searching", "index": i, "query": _txt[:80]})
                    try:
                        from tools.search import search_with_sources
                        _srcs, _stext = await search_with_sources(_txt[:200], 5)
                    except Exception as _e:
                        _srcs, _stext = [], f"Suchfehler: {_e}"
                    if _stext:
                        _web_ctx = _stext[:min(_budget, 6000)]
                    yield _sse({"type": "search_done", "index": i,
                                "count": len(_srcs or [])})
                else:
                    yield _sse({"type": "notice", "index": i,
                                "message": "Websuche im Hartman-Modus gesperrt – ohne Quellen"})

            prior = ""
            if results:
                _parts = [f"### Ergebnis Schritt {si + 1} ({s}):\n{r}" for si, (s, r) in enumerate(results)]
                prior = "\n\n".join(_parts)
                if len(prior) > _budget:
                    prior = "…\n" + prior[-_budget:]
            _sys = ("Du arbeitest einen mehrstufigen Arbeitsablauf ab. Löse NUR den "
                    "AKTUELLEN Schritt präzise und vollständig und baue dabei auf den "
                    "bisherigen Ergebnissen auf. Antworte fokussiert auf Deutsch in Markdown, "
                    "ohne den Schritt bloß zu wiederholen.")
            if _web_ctx:
                _sys += ("\n\nDir liegen Web-Suchergebnisse vor. Stütze konkrete Angaben "
                         "(Zahlen, Daten, Namen, Preise) NUR auf diese Quellen; ist etwas "
                         "nicht belegt, kennzeichne es als unsicher und erfinde nichts.")
            if goal:
                _sys += f"\n\nÜbergeordnetes Ziel des Ablaufs: {goal}"
            _user = ((f"Bisherige Ergebnisse:\n{prior}\n\n---\n" if prior else "")
                     + (f"Web-Suchergebnisse:\n{_web_ctx}\n\n---\n" if _web_ctx else "")
                     + f"AKTUELLER SCHRITT {i + 1}/{len(steps)}: {_txt}")
            _res = await _run(_model, _sys, _user, max(300, min(int(_ctx * 0.35), 1500)))
            results.append((_txt, _res))
            yield _sse({"type": "step_done", "index": i, "step": _txt, "result": _res})
            # Sprach-Schritt: den erzeugten Text im Browser vorlesen lassen (TTS,
            # GPU-frei). Das eigentliche Sprechen macht das Frontend.
            if _kind == "voice":
                yield _sse({"type": "speak", "index": i, "text": _res})

        # Abschluss-Synthese: bevorzugt das API-Modell (größeres Kontextfenster für die
        # gesammelten Teilergebnisse), sonst das Basismodell.
        _synth_model = api_model or base_model
        yield _sse({"type": "synthesizing", "model": _synth_model,
                    "remote": _llm.is_remote(_synth_model)})
        _all = "\n\n".join(f"### Schritt {i + 1}: {s}\n{r}" for i, (s, r) in enumerate(results))
        if len(_all) > _budget:
            _all = "…\n" + _all[-_budget:]
        _ssys = ("Du fasst die Ergebnisse eines mehrstufigen Arbeitsablaufs zu EINEM "
                 "zusammenhängenden, gut strukturierten Gesamtergebnis zusammen (Markdown: "
                 "## Überschriften, **Fett**, Aufzählungen/Tabellen wo sinnvoll). Führe die "
                 "Teilergebnisse logisch zusammen, wiederhole nicht stumpf, sondern liefere ein "
                 "kohärentes Endprodukt und schließe mit einem klaren Fazit ab.")
        if goal:
            _ssys += f"\n\nZiel des Ablaufs: {goal}"
        _suser = f"Schritt-Ergebnisse:\n{_all}\n\n---\nErstelle das zusammenhängende Gesamtergebnis."
        _final = await _run(_synth_model, _ssys, _suser, max(500, min(int(_ctx * 0.5), 2200)))
    except httpx.ConnectError:
        yield _sse({"type": "error", "message": "Ollama nicht erreichbar – läuft der lokale Server?"})
        return
    except httpx.HTTPStatusError as e:
        _sc = getattr(e.response, "status_code", 0) or 0
        if _sc in (502, 503, 504):
            _m = f"Der Anbieter hat nicht rechtzeitig geantwortet (HTTP {_sc}). Bitte weniger/kürzere Schritte oder ein lokales Modell."
        else:
            _m = f"Modell abgelehnt (num_ctx/VRAM?): HTTP {_sc}"
        yield _sse({"type": "error", "message": _m})
        return
    except Exception as e:
        yield _sse({"type": "error", "message": f"Arbeitsablauf fehlgeschlagen: {e}"})
        return

    for _i, _w in enumerate(_final.split(" ")):
        yield _sse({"type": "text", "content": _w + (" " if _i < len(_final.split(' ')) - 1 else "")})
        await asyncio.sleep(0.003)
    yield _sse({"type": "done", "tokens": _tok,
                "results": [{"step": s, "result": r} for s, r in results]})


@router.post("/api/workflow")
async def workflow(req: Request):
    """Führt einen mehrstufigen Arbeitsablauf aus (SSE). Body: ``{steps:[…], goal?,
    model?, api_model?}``; ``steps`` sind Strings ODER Objekte ``{text, mode, web, kind}``
    (``mode`` '' / 'local' / 'api', ``web`` = Websuche für den Schritt, ``kind`` ''
    /'image'/'voice'). Pro Schritt wählbares Modell (lokal recherchiert/zwischenspeichert →
    API-Modell verarbeitet weiter), die Synthese läuft bevorzugt auf dem API-Modell.
    **Bild-Schritt** (``kind='image'``, Tag ``[bild]`` oder „generiere ein Bild …") ruft
    den gemeinsamen Kern ``_generate_image_core`` → ``image``-Frame; **Sprach-Schritt**
    (``kind='voice'``, Tag ``[sprache]`` oder „… als Sprachnachricht/vorlesen") lässt den
    erzeugten Text vom Frontend vorlesen → ``speak``-Frame. Streamt ``workflow_start``/
    ``step_start``/``searching``/``search_done``/``notice``/``generating_image``/``image``/
    ``step_done``/``speak``/``synthesizing``/``text``/``done``/``error``. Token-Label
    „Arbeitsablauf" (Bild/Sprache erzeugen keine Chat-Tokens)."""
    body = await req.json()
    return StreamingResponse(_workflow_generator(body), media_type="text/event-stream")


@router.get("/api/downloads/{filename}")
async def download_report(filename: str):
    # only alphanumeric + dot + dash to prevent path traversal
    import re
    if not re.match(r'^[a-zA-Z0-9._-]+$', filename):
        raise HTTPException(400, "Ungültiger Dateiname")
    fp = REPORTS_DIR / filename
    if not fp.exists():
        raise HTTPException(404, "Datei nicht gefunden")
    media_types = {
        ".pdf": "application/pdf",
        ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    }
    mt = media_types.get(fp.suffix.lower(), "application/octet-stream")
    return FileResponse(fp, filename=filename, media_type=mt)







