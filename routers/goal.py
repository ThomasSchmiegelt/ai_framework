"""Router: Ziel-Loop im Chat (/api/goal)

Der Nutzer gibt EIN Ziel in Worten vor („/ziel …"); das Backend arbeitet darauf
autonom in Runden hin und **entscheidet selbst, wann Schluss ist**:

    Ziel → Plan (Teilziele) → Schleife[ Handeln → Bewerten("erreicht?") ] → Synthese

Jede Runde besteht aus einem *Handeln*-Schritt (fokussierter LLM-Fortschritt, optional
web-gestützt) und einem *Bewerten*-Schritt (kurzer LLM-Call → JSON, ob das Ziel erreicht
ist und was noch fehlt). Erreicht das Modell das Ziel oder ist der Runden-Deckel
erschöpft, führt eine Synthese alle Zwischenergebnisse zu einem Gesamtergebnis zusammen.

Bewusst **rein LLM-basiert** (wie /workflow, kein Werkzeug-Loop) → robust auch für kleine
lokale Modelle. Modellrolle „general"; Geheim-/Hartman-Modus erzwingt lokal (über
``_pick_model``/``_web_search_allowed``). Die einzige „echte" Fähigkeit ist die optionale
Websuche zur Erdung (``search_with_sources``), damit konkrete Angaben belegt sind.

Architektur: nutzt ausschließlich geteilte Kernhelfer aus ``core`` (kein Import eines
anderen Feature-Routers → kein Router↔Router-Zyklus).
"""
from __future__ import annotations

import asyncio
import json
import re

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from tools import llm as _llm

from core import *  # noqa: F401,F403  (geteilte Kernflaeche)
import core as _core  # noqa: F401

router = APIRouter()


# Wortweises Streamen des Endtextes in die Chat-Blase (wie /workflow).
async def _stream_words(text: str):
    parts = (text or "").split(" ")
    for i, w in enumerate(parts):
        yield _sse({"type": "text", "content": w + (" " if i < len(parts) - 1 else "")})
        await asyncio.sleep(0.003)


async def _goal_loop_generator(body: dict):
    goal = str(body.get("goal", "") or "").strip()
    if not goal:
        yield _sse({"type": "error", "message": "Kein Ziel angegeben."})
        return

    # Runden-Deckel: kleine Modelle „drehen" sonst; hart begrenzen (2–10, Standard 5).
    try:
        max_rounds = int(body.get("max_rounds") or 5)
    except (TypeError, ValueError):
        max_rounds = 5
    max_rounds = max(2, min(max_rounds, 10))

    want_web = bool(body.get("web", False))
    web_ok = want_web and _web_search_allowed()

    base_model = _pick_model(body.get("model"), _model_for("general"))
    local_model = await _local_model(base_model)  # None, wenn kein lokales LLM da ist
    model = base_model
    _ctx = _profile_num_ctx()
    _tok = {"in": 0, "out": 0}
    # Zeichenbudget für den mitgeführten Kontext (an num_ctx gekoppelt, wie /workflow).
    _budget = max(2000, int((_ctx - 800) * 3.0))

    async def _run(sys_prompt: str, user_prompt: str, num_predict: int) -> str:
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

    yield _sse({"type": "goal_start", "goal": goal, "max_rounds": max_rounds, "web": web_ok})

    work: list[tuple[str, str]] = []  # [(fokus, ergebnis)] je Runde
    reached = False
    last_gap = ""
    last_progress = 0

    try:
        # ── Plan: Ziel in überprüfbare Teilziele zerlegen (nur Orientierung) ─────
        yield _sse({"type": "planning"})
        _plan_sys = ("Du zerlegst ein Ziel in 3–6 kurze, ÜBERPRÜFBARE Teilziele, die zusammen "
                     "das Ziel erfüllen. Antworte NUR als JSON: {\"plan\":[\"…\",\"…\"]}. Kein weiterer Text.")
        _plan_raw = await _run(_plan_sys, f"Ziel: {goal}", max(200, min(int(_ctx * 0.2), 700)))
        _pj = _parse_llm_json(_plan_raw) or {}
        plan = [str(x).strip() for x in (_pj.get("plan") or []) if str(x).strip()][:6]
        if not plan:
            plan = [goal]
        yield _sse({"type": "plan", "items": plan})
        plan_txt = "\n".join(f"{i + 1}. {p}" for i, p in enumerate(plan))

        # ── Schleife: Handeln → Bewerten, bis erreicht oder Deckel ───────────────
        for rnd in range(max_rounds):
            focus = last_gap or (plan[rnd] if rnd < len(plan) else goal)
            yield _sse({"type": "round_start", "index": rnd, "total": max_rounds,
                        "focus": focus[:120]})

            # Optionale Web-Erdung für den aktuellen Fokus.
            _web_ctx = ""
            if web_ok:
                yield _sse({"type": "searching", "index": rnd, "query": focus[:80]})
                try:
                    from tools.search import search_with_sources
                    _srcs, _stext = await search_with_sources(focus[:200], 5)
                except Exception as _e:
                    _srcs, _stext = [], f"Suchfehler: {_e}"
                if _stext:
                    _web_ctx = _stext[:min(_budget, 6000)]
                yield _sse({"type": "search_done", "index": rnd, "count": len(_srcs or [])})

            # Bisher Erarbeitetes als Kontext (gekürzt aufs Budget).
            prior = ""
            if work:
                _parts = [f"### Runde {wi + 1} ({f}):\n{r}" for wi, (f, r) in enumerate(work)]
                prior = "\n\n".join(_parts)
                if len(prior) > _budget:
                    prior = "…\n" + prior[-_budget:]

            _act_sys = ("Du arbeitest iterativ auf ein ZIEL hin. Erarbeite den NÄCHSTEN "
                        "sinnvollen Fortschritt in Richtung Ziel — konkret, vollständig und "
                        "aufbauend auf dem bisher Erarbeiteten, ohne dich zu wiederholen. "
                        "Konzentriere dich auf die genannte offene Lücke. Antworte fokussiert "
                        "auf Deutsch in Markdown.")
            if _web_ctx:
                _act_sys += ("\n\nDir liegen Web-Suchergebnisse vor. Stütze konkrete Angaben "
                             "(Zahlen, Daten, Namen, Preise) NUR auf diese Quellen; ist etwas "
                             "nicht belegt, kennzeichne es als unsicher und erfinde nichts.")
            _act_user = (f"ZIEL: {goal}\n\nTeilziele:\n{plan_txt}\n\n"
                         + (f"Web-Suchergebnisse:\n{_web_ctx}\n\n---\n" if _web_ctx else "")
                         + (f"Bisher erarbeitet:\n{prior}\n\n---\n" if prior else "")
                         + f"Aktueller Fokus / offene Lücke: {focus}\n\n"
                         "Erarbeite JETZT den nächsten Fortschritt für genau diesen Fokus.")
            _res = await _run(_act_sys, _act_user, max(300, min(int(_ctx * 0.4), 1600)))
            work.append((focus, _res))
            yield _sse({"type": "round_work", "index": rnd, "focus": focus, "result": _res})

            # ── Bewerten: Ziel erreicht? Was fehlt noch? (JSON) ──────────────────
            yield _sse({"type": "evaluating", "index": rnd})
            _all_work = "\n\n".join(f"### Runde {wi + 1}: {f}\n{r}" for wi, (f, r) in enumerate(work))
            if len(_all_work) > _budget:
                _all_work = "…\n" + _all_work[-_budget:]
            _ev_sys = ("Du bewertest STRENG, ob ein Ziel mit dem bisher Erarbeiteten VOLLSTÄNDIG "
                       "erreicht ist. Sei ehrlich: nur wirklich vollständig = erreicht. Antworte "
                       "NUR als JSON: {\"erreicht\": true|false, \"fortschritt\": 0-100, "
                       "\"luecke\": \"was konkret noch fehlt (leer, wenn erreicht)\"}. Kein weiterer Text.")
            _ev_user = f"ZIEL: {goal}\n\nBisher erarbeitet:\n{_all_work}"
            _ev_raw = await _run(_ev_sys, _ev_user, 400)
            _ej = _parse_llm_json(_ev_raw) or {}
            reached = bool(_ej.get("erreicht", False))
            try:
                prog = int(float(_ej.get("fortschritt", 0)))
            except (TypeError, ValueError):
                prog = last_progress
            prog = max(0, min(prog, 100))
            last_gap = str(_ej.get("luecke", "") or "").strip()
            if reached:
                prog = 100
            last_progress = prog
            yield _sse({"type": "evaluate", "index": rnd, "reached": reached,
                        "progress": prog, "gap": last_gap[:200]})
            if reached:
                break

        # ── Synthese: alle Runden zu EINEM Gesamtergebnis zusammenführen ─────────
        yield _sse({"type": "synthesizing", "reached": reached})
        _all = "\n\n".join(f"### Runde {i + 1}: {f}\n{r}" for i, (f, r) in enumerate(work))
        if len(_all) > _budget:
            _all = "…\n" + _all[-_budget:]
        _syn_sys = ("Du fasst die Zwischenergebnisse eines zielgerichteten Arbeitslaufs zu EINEM "
                    "zusammenhängenden, gut strukturierten Gesamtergebnis zusammen (Markdown: "
                    "## Überschriften, **Fett**, Aufzählungen/Tabellen wo sinnvoll). Liefere ein "
                    "kohärentes Endprodukt bezogen auf das Ziel, wiederhole nicht stumpf und "
                    "schließe mit einem klaren Fazit ab.")
        _syn_user = (f"ZIEL: {goal}\n\nZwischenergebnisse:\n{_all}\n\n---\n"
                     + ("Das Ziel gilt als erreicht — stelle das Endergebnis dar."
                        if reached else
                        "Der Runden-Deckel wurde erreicht — fasse den erreichten Stand zusammen "
                        f"und benenne offen, was noch fehlt: {last_gap or 'siehe Bewertung'}.")
                     + "\n\nErstelle das zusammenhängende Gesamtergebnis.")
        _final = await _run(_syn_sys, _syn_user, max(500, min(int(_ctx * 0.5), 2400)))
    except httpx.ConnectError:
        yield _sse({"type": "error", "message": "Ollama nicht erreichbar – läuft der lokale Server?"})
        return
    except httpx.HTTPStatusError as e:
        _sc = getattr(e.response, "status_code", 0) or 0
        if _sc in (502, 503, 504):
            _m = (f"Der Anbieter hat nicht rechtzeitig geantwortet (HTTP {_sc}). "
                  "Bitte weniger Runden oder ein lokales Modell.")
        else:
            _m = f"Modell abgelehnt (num_ctx/VRAM?): HTTP {_sc}"
        yield _sse({"type": "error", "message": _m})
        return
    except Exception as e:
        yield _sse({"type": "error", "message": f"Ziel-Loop fehlgeschlagen: {e}"})
        return

    async for _f in _stream_words(_final):
        yield _f
    yield _sse({"type": "done", "tokens": _tok, "reached": reached,
                "rounds": len(work), "progress": last_progress,
                "results": [{"focus": f, "result": r} for f, r in work]})


@router.post("/api/goal")
async def goal_loop(req: Request):
    """Zielgerichteter Loop (SSE). Body: ``{goal, max_rounds?, web?, model?}``.

    Arbeitet autonom in Runden (Handeln → Bewerten) auf das Ziel hin und entscheidet
    selbst, wann es erreicht ist (bzw. bricht am Runden-Deckel ab). Rein LLM-basiert
    (robust für kleine Modelle); ``web=true`` erdet jede Runde über die Websuche
    (nur außerhalb des Hartman-Modus). Modellrolle „general", Geheim/Hartman → lokal.
    Streamt ``goal_start``/``planning``/``plan``/``round_start``/``searching``/
    ``search_done``/``round_work``/``evaluating``/``evaluate``/``synthesizing``/``text``/
    ``done``/``error``. Token-Label „Ziel-Loop"."""
    body = await req.json()
    return StreamingResponse(_goal_loop_generator(body), media_type="text/event-stream")
