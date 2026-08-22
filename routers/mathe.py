"""Router: Mathe-Tutor (SymPy-verifiziert, /api/mathe)

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


# ── Mathe-Tutor: deterministische SymPy-Grundwahrheit ────────────────────────
#
# Kleine lokale Modelle rufen Verifikations-Werkzeuge mitten im Tutor-Dialog NICHT
# zuverlässig selbst auf (getestet: ministral-3:3b und qwen2.5-coder:7b ignorieren
# sie bzw. bestätigen falsche Schritte). Damit „werkzeuggeprüft" trotzdem echt ist,
# rechnet der Server hier die korrekte Lösung deterministisch mit SymPy und gibt sie
# dem Tutor-Agenten als verifizierte Fakten mit – statt es dem Modell zu überlassen.


def _mathe_sympy_facts(kind: str, sympy_str: str, goal: str) -> str:
    """Berechnet aus einem (vom LLM extrahierten) Ausdruck deterministisch die
    Grundwahrheit mit SymPy. Gibt eine kurze Faktenliste oder "" zurück."""
    if not sympy_str:
        return ""
    # Einfacher Zeichensatz-Schutz: nur mathematische Ausdrücke zulassen
    if not re.fullmatch(r"[0-9A-Za-z_+\-*/^().,=<>\[\] ]+", sympy_str):
        return ""
    expr = sympy_str.replace("^", "**").strip()
    expr = expr.replace("==", "=")  # Modell nutzt oft Python-Gleichheit „=="
    # „f(x) = …"-Präfix entfernen (häufige Schreibweise), reine rechte Seite behalten
    expr = re.sub(r"^[a-zA-Z]\s*\([a-zA-Z]\)\s*=\s*", "", expr)
    try:
        import sympy as sp
        from sympy.parsing.sympy_parser import (
            parse_expr, standard_transformations, implicit_multiplication_application)
        _tf = standard_transformations + (implicit_multiplication_application,)
        def _p(s):  # robust: versteht implizite Multiplikation (z. B. „2x" → 2*x)
            return parse_expr(s, transformations=_tf)
        facts: list[str] = []
        # Fall A: Modell lieferte bereits einen SymPy-Funktionsaufruf (diff(...), integrate(...), …)
        if re.match(r"^(diff|integrate|factor|expand|simplify|solve|limit|series|nsimplify)\s*\(", expr):
            res = sp.sympify(expr)
            facts.append(f"Ergebnis [SymPy]: {res}")
            return "\n".join(facts)
        # Fall B: Gleichung (enthält genau ein '=')
        if "=" in expr and "==" not in expr:
            lhs_s, rhs_s = expr.split("=", 1)
            lhs, rhs = _p(lhs_s), _p(rhs_s)
            eq = sp.Eq(lhs, rhs)
            syms = sorted(eq.free_symbols, key=lambda s: s.name)
            facts.append(f"Gleichung: {expr}")
            if syms:
                sols = sp.solve(eq, *syms)
                if sols:
                    facts.append(f"Lösung(en) [SymPy]: {sols}")
            poly = sp.expand(lhs - rhs)
            fac = sp.factor(poly)
            if str(fac) != str(poly):
                facts.append(f"Faktorisierung von ({poly}): {fac}")
            return "\n".join(facts)
        # Fall C: reiner Ausdruck – je nach Ziel ableiten/integrieren/faktorisieren
        e = _p(expr)
        facts.append(f"Ausdruck: {expr}")
        if goal == "diff":
            facts.append(f"Ableitung [SymPy]: {sp.diff(e)}")
        elif goal == "integrate":
            facts.append(f"Stammfunktion [SymPy]: {sp.integrate(e)} (+ C)")
        elif goal == "factor":
            facts.append(f"Faktorisiert [SymPy]: {sp.factor(e)}")
        elif goal == "solve":
            syms = sorted(e.free_symbols, key=lambda s: s.name)
            if syms:
                facts.append(f"Nullstellen [SymPy]: {sp.solve(e, *syms)}")
        else:
            simp = sp.simplify(e)
            facts.append(f"Vereinfacht [SymPy]: {simp}")
            fac = sp.factor(e)
            if str(fac) != str(e) and str(fac) != str(simp):
                facts.append(f"Faktorisiert [SymPy]: {fac}")
        return "\n".join(facts)
    except Exception:
        return ""


async def _mathe_ground_facts(client, model, messages, tok: Optional[dict] = None) -> str:
    """Extrahiert die zentrale Aufgabe aus dem Gespräch und liefert die
    SymPy-verifizierte Grundwahrheit als Fakten-String (oder "" wenn nichts
    deterministisch prüfbar ist). Erwartet einen offenen httpx-Client, dessen
    Modell bereits unter ``_model_session`` geladen wurde.
    ``tok`` (optional): dict {"in","out"}, in das der Tokenverbrauch summiert wird."""
    transcript = _med_transcript(messages)  # gleiche Formatierung wie Medizin
    if not transcript:
        return ""
    try:
        resp = await _llm.chat(client, {
            "model": model, "think": False, "stream": False,
            "messages": [
                {"role": "system", "content": (
                    "Extrahiere die zentrale mathematische Aufgabe aus dem Gespräch. "
                    "Antworte NUR mit JSON in genau diesem Format, ohne weiteren Text: "
                    '{"kind":"equation|expression|none","sympy":"<SymPy-auswertbarer Ausdruck, '
                    'Gleichungen mit = , Potenz mit ** , keine Worte>","goal":"solve|factor|diff|'
                    'integrate|simplify|none"}. Bei reinen Theorie-/Wortaufgaben ohne klaren '
                    'Ausdruck: kind=none.')},
                {"role": "user", "content": f"Gespräch:\n{transcript[:2000]}"},
            ],
        })
        resp.raise_for_status()
        _mg_j = resp.json()
        if tok is not None:
            _a, _b = _llm_tok(_mg_j)
            tok["in"] += _a
            tok["out"] += _b
        raw = _mg_j.get("message", {}).get("content", "") or ""
    except Exception:
        return ""

    raw = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL)
    m = re.search(r"\{[\s\S]*\}", raw)
    if not m:
        return ""
    try:
        data = json.loads(m.group(0))
    except Exception:
        return ""

    kind = str(data.get("kind", "none")).strip().lower()
    sympy_str = str(data.get("sympy", "")).strip()
    goal = str(data.get("goal", "none")).strip().lower()
    if not sympy_str or sympy_str.lower() in ("none", "null"):
        return ""
    return _mathe_sympy_facts(kind, sympy_str, goal)


def _mathe_check_tokens(facts: str) -> list[str]:
    """Zieht aus den SymPy-Fakten die numerischen Ergebnis-Tokens, gegen die eine
    Modell-Lösung strikt geprüft werden kann (z. B. Gleichungslösungen)."""
    toks: list[str] = []
    for line in facts.splitlines():
        if "[SymPy]" not in line or ":" not in line:
            continue
        val = line.split(":", 1)[1]
        for piece in re.split(r"[\[\]\{\}(),=\s]+", val):
            piece = piece.strip()
            if re.fullmatch(r"-?\d+(?:\.\d+)?", piece):
                toks.append(piece)
    return list(dict.fromkeys(toks))


def _mathe_solution_ok(text: str, tokens: list[str]) -> bool:
    """True, wenn alle erwarteten Ergebnis-Tokens in der Modell-Lösung vorkommen."""
    if not tokens:
        return False
    norm = re.sub(r"\s+", "", text or "")
    return all(t in norm for t in tokens)


@router.post("/api/mathe/ground")
async def mathe_ground(req: Request):
    """Extrahiert die zentrale Aufgabe aus dem Tutor-Gespräch und liefert die
    SymPy-verifizierte Grundwahrheit als Fakten zurück (für den Tutor-Modus).
    Gibt {facts: ""} zurück, wenn nichts deterministisch prüfbar ist."""
    body = await req.json()
    messages = body.get("messages") or []
    model = _pick_model(body.get("model"), _model_for("general"))
    _tok = {"in": 0, "out": 0}
    async with _model_session(model), httpx.AsyncClient(timeout=90) as client:
        facts = await _mathe_ground_facts(client, model, messages, tok=_tok)
    return {"facts": facts, "tokens": _tok}


_MATHE_VERIFY_ROUNDS = 2  # zusätzliche Korrekturrunden nach dem ersten Lösungsversuch


@router.post("/api/mathe/solve-verified")
async def mathe_solve_verified(req: Request):
    """Agentischer Verifikationsloop (freie Adaption des „Agentic-AI + Simulink"-
    Konzepts): das Modell löst die Aufgabe, die Lösung wird deterministisch mit
    SymPy geprüft; weicht sie ab, fließt die SymPy-Wahrheit als Korrektur zurück
    und das Modell rechnet erneut (max. _MATHE_VERIFY_ROUNDS Korrekturrunden).
    Streamt SSE-Frames: stage (solve|verify|fix), text (Endlösung), done, error."""
    body = await req.json()
    messages = body.get("messages") or []
    model = _pick_model(body.get("model"), _model_for("coding"))

    async def gen():
        _tok = {"in": 0, "out": 0}
        try:
            async with _model_session(model), httpx.AsyncClient(timeout=180) as client:
                # 1) Deterministische Grundwahrheit
                yield _sse({"type": "stage", "stage": "verify", "status": "start",
                            "label": "SymPy-Grundwahrheit"})
                facts = await _mathe_ground_facts(client, model, messages, tok=_tok)
                tokens = _mathe_check_tokens(facts) if facts else []
                yield _sse({"type": "stage", "stage": "verify", "status": "done",
                            "label": "SymPy-Grundwahrheit",
                            "content": facts or "Keine deterministisch prüfbaren Fakten – nur Plausibilität."})

                task = _med_transcript(messages)
                solution = ""
                verified = False
                rounds = 0
                for rnd in range(_MATHE_VERIFY_ROUNDS + 1):
                    rounds = rnd + 1
                    is_fix = rnd > 0
                    label = "Korrektur" if is_fix else "Lösung"
                    stage = "fix" if is_fix else "solve"
                    yield _sse({"type": "stage", "stage": stage, "status": "start", "label": label})

                    sys = ("Du bist ein sorgfältiger Mathe-Experte. Löse die Aufgabe Schritt für "
                           "Schritt und gib am Ende das Endergebnis klar an. Formeln in LaTeX ($…$).")
                    user = f"Aufgabe:\n{task}"
                    if is_fix and facts:
                        user += (f"\n\nDeine bisherige Lösung stimmt NICHT mit der deterministischen "
                                 f"SymPy-Berechnung überein.\nSymPy-Fakten:\n{facts}\n\nBisherige Lösung:\n"
                                 f"{solution}\n\nKorrigiere und gib die vollständige, korrekte Lösung mit "
                                 f"dem richtigen Endergebnis an.")
                    try:
                        resp = await _llm.chat(client, {
                            "model": model, "think": False, "stream": False,
                            "messages": [{"role": "system", "content": sys},
                                         {"role": "user", "content": user}],
                        })
                        resp.raise_for_status()
                        _sv_j = resp.json()
                        _a, _b = _llm_tok(_sv_j)
                        _tok["in"] += _a
                        _tok["out"] += _b
                        solution = _sv_j.get("message", {}).get("content", "") or ""
                        solution = re.sub(r"<think>.*?</think>", "", solution, flags=re.DOTALL).strip()
                    except Exception as e:
                        yield _sse({"type": "error", "content": f"Modellfehler: {e}"})
                        return
                    yield _sse({"type": "stage", "stage": stage, "status": "done",
                                "label": label, "content": solution})

                    if not tokens:
                        break  # nicht strikt prüfbar → erste Lösung steht
                    if _mathe_solution_ok(solution, tokens):
                        verified = True
                        break

                yield _sse({"type": "text", "content": solution})
                yield _sse({"type": "done", "verified": verified, "checkable": bool(tokens),
                            "rounds": rounds, "facts": facts, "tokens": _tok})
        except Exception as e:
            yield _sse({"type": "error", "content": str(e)})

    return StreamingResponse(gen(), media_type="text/event-stream")

