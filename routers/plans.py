"""Router: Plan-API + /plan-Orchestrator (/api/plans, /api/plan/strategy)

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


# ── Plan-API ──────────────────────────────────────────────────────────────────


@router.get("/api/plans")
async def list_plans():
    plans = []
    for f in sorted(PLANS_DIR.glob("*.json"), key=lambda x: x.stat().st_mtime, reverse=True):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            plans.append({"id": data["id"], "name": data.get("name", "Plan"), "updated_at": data.get("updated_at", 0)})
        except Exception:
            pass
    return plans


@router.post("/api/plans")
async def create_plan(req: Request):
    body = await req.json()
    name = str(body.get("name", "Neuer Plan")).strip()
    plan = {
        "id": uuid.uuid4().hex[:12],
        "name": name,
        "created_at": time.time(),
        "updated_at": time.time(),
        "tasks": body.get("tasks", []),
        "description": str(body.get("description", "")).strip(),
        "system_prompt": str(body.get("system_prompt", "")).strip(),
        "resource_catalog": body.get("resource_catalog", []),
        "resource_mode": str(body.get("resource_mode", "free")).strip(),
        "start_date": str(body.get("start_date", "")).strip(),
        "workdays": bool(body.get("workdays", False)),
        "project_id": str(body.get("project_id", "")).strip(),
    }
    _plan_path(plan["id"], name).write_text(
        json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return plan


@router.get("/api/plans/{pid}")
async def get_plan(pid: str):
    fp = _plan_path_by_id(pid)
    if not fp or not fp.exists():
        raise HTTPException(404, "Plan nicht gefunden")
    return json.loads(fp.read_text(encoding="utf-8"))


@router.put("/api/plans/{pid}")
async def save_plan(pid: str, req: Request):
    body = await req.json()
    old_fp = _plan_path_by_id(pid)
    existing = json.loads(old_fp.read_text(encoding="utf-8")) if old_fp and old_fp.exists() else {}
    existing.update(body)
    existing["id"] = pid
    existing["updated_at"] = time.time()
    new_fp = _plan_path(pid, existing.get("name", ""))
    if old_fp and old_fp != new_fp and old_fp.exists():
        old_fp.unlink(missing_ok=True)
    new_fp.write_text(json.dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8")
    return existing


@router.delete("/api/plans/{pid}")
async def delete_plan(pid: str):
    fp = _plan_path_by_id(pid)
    if fp:
        fp.unlink(missing_ok=True)
    return {"ok": True}


@router.post("/api/plans/{pid}/ai")
async def plan_ai(pid: str, req: Request):
    body = await req.json()
    fp = PLANS_DIR / f"{pid}.json"
    plan = json.loads(fp.read_text(encoding="utf-8")) if fp.exists() else {}
    # Inline-Tasks aus dem Body übernehmen falls kein gespeicherter Plan
    tasks = plan.get("tasks") or body.get("tasks", [])
    model = _pick_model(body.get("model"))
    user_message = body.get("message", "")
    use_web = body.get("use_web", False)
    use_rag = body.get("use_rag", False)
    tasks_summary = json.dumps(tasks, ensure_ascii=False)

    system_prompt = (
        "Du bist ein erfahrener Projektmanager und hilfst beim Erstellen und Verfeinern von Projektplänen. "
        "Du kennst Methoden wie CPM, Netzplanung, kritischen Pfad und Ressourcenplanung. "
        "Antworte auf Deutsch, präzise und konstruktiv. "
        "Wenn du Aufgaben vorschlägst, nenne sie als JSON-Liste mit Feldern: id, name, duration, predecessors, successors."
    )

    context_parts = [f"Aktueller Plan '{plan.get('name', 'Plan')}':\n\nAufgaben:\n{tasks_summary}"]

    if use_web:
        from tools.search import search_with_sources
        try:
            _, search_text = await search_with_sources(user_message, 4)
            if search_text:
                context_parts.append(f"Websuche-Ergebnisse:\n{search_text[:3000]}")
        except Exception:
            pass

    # Wissensdatenbanken zur Informationsbeschaffung: die plan-eigene Basis
    # (bei aktivem 📚-Schalter) UND optional im Planer ausgewählte Basen.
    rag_ids = list(body.get("rag_collections") or [])
    if use_rag and plan.get("rag_collection_id"):
        rag_ids.append(plan["rag_collection_id"])
    if rag_ids:
        from tools.rag import query_collections
        colls, seen = [], set()
        for cid in rag_ids:
            if not cid or cid in seen:
                continue
            seen.add(cid)
            c = await _db.rag_get_collection(cid)
            if c:
                colls.append(c)
        if colls:
            try:
                hits = await query_collections(colls, user_message)
                if hits:
                    rag_text = "\n\n".join(h.get("text", "") for h in hits[:6])
                    context_parts.append(f"Aus Wissensdatenbank:\n{rag_text[:3000]}")
            except Exception:
                pass

    user_content = "\n\n".join(context_parts) + f"\n\nFrage: {user_message}"
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_content},
    ]

    async def _stream():
        async with _model_session(model), httpx.AsyncClient(timeout=120) as client:
            async for chunk in _llm.stream(client, {
                "model": model,
                "think": False,
                "messages": messages,
                "stream": True,
                # Großes Kontextfenster: der komplette Aufgaben-JSON-Block kann das
                # Ollama-Default (2048) sprengen → sonst fällt die eigentliche Frage
                # aus dem Kontext und das Modell antwortet mit Bruchstücken/Einwörtern.
                "options": {"num_ctx": 8192, "temperature": 0.4},
            }):
                try:
                    token = chunk.get("message", {}).get("content", "")
                    if token:
                        yield f"data: {json.dumps({'type': 'text', 'content': token})}\n\n"
                    if chunk.get("done"):
                        _a, _b = _llm_tok(chunk)
                        yield f"data: {json.dumps({'type': 'done', 'tokens': {'in': _a, 'out': _b}})}\n\n"
                except Exception:
                    pass

    return StreamingResponse(_stream(), media_type="text/event-stream")


@router.post("/api/plans/{pid}/check-feasibility")
async def plan_check_feasibility(pid: str, req: Request):
    """Prüft den Plan strukturiert auf Durchführbarkeit: erkennt deterministisch
    Zyklen, lose Enden und mehrfache Wurzeln und lässt das LLM fehlende Aufgaben,
    Lücken, Risiken und Empfehlungen ergänzen. Liefert strukturiertes JSON."""
    body = await req.json()
    fp = PLANS_DIR / f"{pid}.json"
    plan = json.loads(fp.read_text(encoding="utf-8")) if fp.exists() else {}
    tasks = plan.get("tasks") or body.get("tasks", [])
    if not tasks:
        raise HTTPException(400, "Kein Plan mit Aufgaben vorhanden")
    description = (body.get("description") or plan.get("description") or "").strip()
    system_prompt = (body.get("system_prompt") or plan.get("system_prompt") or "").strip()
    model = _pick_model(body.get("model"))

    # ── Deterministische Strukturprüfung ──────────────────────────────────────
    by_id = {str(t.get("id")): t for t in tasks if t.get("id")}
    ids = list(by_id.keys())
    idset = set(ids)
    preds = {i: [p for p in (by_id[i].get("predecessors") or []) if p in idset] for i in ids}
    has_succ = set()
    for i in ids:
        for p in preds[i]:
            has_succ.add(p)
    no_pred = [i for i in ids if not preds[i] and not by_id[i].get("is_start")]
    no_succ = [i for i in ids if i not in has_succ and not by_id[i].get("is_end")]
    # Zyklen via Kahn
    indeg = {i: len(preds[i]) for i in ids}
    succ = {i: [] for i in ids}
    for i in ids:
        for p in preds[i]:
            succ[p].append(i)
    queue = [i for i in ids if indeg[i] == 0]
    seen_n = 0
    while queue:
        n = queue.pop()
        seen_n += 1
        for m in succ[n]:
            indeg[m] -= 1
            if indeg[m] == 0:
                queue.append(m)
    cycle = seen_n < len(ids)

    struct_hints = []
    if cycle:
        struct_hints.append("Mindestens ein Zyklus in den Abhängigkeiten – CPM nicht berechenbar.")
    if len(no_pred) > 1:
        struct_hints.append(f"Mehrere Aufgaben ohne Vorgänger (mögliche fehlende Verknüpfung): {', '.join(no_pred[:8])}.")
    if no_succ:
        struct_hints.append(f"Lose Enden ohne Nachfolger: {', '.join(no_succ[:8])}.")

    # ── LLM-Bewertung ─────────────────────────────────────────────────────────
    tasks_summary = json.dumps(
        [{"id": t.get("id"), "name": t.get("name"), "duration": t.get("duration"),
          "predecessors": t.get("predecessors")} for t in tasks],
        ensure_ascii=False)
    sys = (system_prompt + "\n\n" if system_prompt else "") + (
        "Du bist ein erfahrener Projektmanager und prüfst Projektpläne kritisch auf "
        "Durchführbarkeit und Vollständigkeit. Antworte ausschließlich mit gültigem JSON."
    )
    user = (
        (f"Projektbeschreibung & Ziel:\n{description}\n\n" if description else "") +
        f"Aktuelle Aufgaben:\n{tasks_summary}\n\n" +
        (("Automatisch erkannte Strukturprobleme:\n- " + "\n- ".join(struct_hints) + "\n\n") if struct_hints else "") +
        "Prüfe den Plan auf Durchführbarkeit und welche Aufgaben FEHLEN. Antworte NUR mit JSON:\n"
        '{"durchfuehrbar": true, "bewertung": "kurzes Gesamturteil", '
        '"fehlende_aufgaben": [{"id":"N1","name":"…","duration":2,"predecessors":["T3"]}], '
        '"luecken": ["…"], "risiken": ["…"], "empfehlungen": ["…"]}\n'
        "fehlende_aufgaben: konkrete Vorschläge mit nicht kollidierender id, name, duration, predecessors "
        "(verweise nur auf existierende oder neue ids)."
    )

    async with _model_session(model), httpx.AsyncClient(timeout=180) as client:
        resp = await _llm.chat(client, {
            "model": model, "think": False, "format": "json",
            "messages": [{"role": "system", "content": sys},
                         {"role": "user", "content": user}],
            "stream": False, "options": {"num_ctx": 8192},
        })
        resp.raise_for_status()
        _cf_j = resp.json()
        _cf_ti, _cf_to = _llm_tok(_cf_j)
        raw = _cf_j.get("message", {}).get("content", "")

    data = _parse_llm_json(raw) or {}
    if not isinstance(data, dict):
        data = {}
    # Kleine Modelle liefern oft Umlaut-Schlüssel statt der ASCII-Variante aus dem
    # Schema-Beispiel → auf die erwarteten Keys normalisieren.
    for k_uml, k_ascii in (("durchführbar", "durchfuehrbar"), ("lücken", "luecken")):
        if k_uml in data and not data.get(k_ascii):
            data[k_ascii] = data.pop(k_uml)
    data.setdefault("durchfuehrbar", not cycle)
    data.setdefault("luecken", [])
    data.setdefault("fehlende_aufgaben", [])
    # Deterministische Befunde ergänzen (verlässlich, unabhängig vom Modell)
    if struct_hints:
        data["struktur"] = struct_hints
    data["cycle"] = cycle
    data["no_predecessor"] = no_pred
    data["loose_ends"] = no_succ
    data["tokens"] = {"in": _cf_ti, "out": _cf_to}
    return data


@router.post("/api/plans/evaluate")
async def plan_evaluate(req: Request):
    """Vergleicht bis zu 3 Pläne KI-gestützt und erzeugt einen verbesserten gemeinsamen Plan."""
    body = await req.json()
    plan_ids = (body.get("plan_ids") or [])[:3]
    model = _pick_model(body.get("model"))
    if not plan_ids:
        raise HTTPException(400, "Mindestens eine plan_id erforderlich")

    plans = []
    for pid in plan_ids:
        fp = _plan_path_by_id(pid)
        if fp and fp.exists():
            try:
                plans.append(json.loads(fp.read_text(encoding="utf-8")))
            except Exception:
                pass
    if not plans:
        raise HTTPException(404, "Keine Pläne geladen")

    def _plan_summary(p):
        tasks = p.get("tasks", [])
        tlines = "\n".join(f"  - {t.get('name','')} (Dauer {t.get('duration',0)}d, Vorgänger: {t.get('predecessors',[])})" for t in tasks[:40])
        return (f"Plan: {p.get('name','')}\nBeschreibung: {p.get('description','')}\n"
                f"Aufgaben ({len(tasks)}):\n{tlines}")

    plan_texts = "\n\n---\n\n".join(f"## Plan {i+1}\n{_plan_summary(p)}" for i, p in enumerate(plans))

    prompt = (
        f"Du erhältst {len(plans)} Projektplan{'e' if len(plans)>1 else ''} zum gleichen Projektvorhaben. "
        f"Jeder Plan ist ca. 80 % korrekt und vollständig. Analysiere jeden Plan, identifiziere:\n"
        f"1. Stärken (gut durchdacht, vollständig)\n"
        f"2. Lücken (fehlende Aufgaben, falsche Abhängigkeiten, unrealistische Dauern)\n"
        f"Erstelle dann einen **verbesserten gemeinsamen Plan** der alle Stärken vereint (Ziel: ~99 % Korrektheit).\n\n"
        f"Antworte auf Deutsch. Gib am Ende den verbesserten Plan als JSON in folgendem Format aus:\n"
        f"```json\n{{\"name\":\"...\",\"description\":\"...\",\"tasks\":[{{\"id\":\"T1\",\"name\":\"...\","
        f"\"duration\":1,\"predecessors\":[],\"successors\":[\"T2\"]}},...]}}\n```\n\n"
        f"Hier die Pläne:\n\n{plan_texts}"
    )

    async def _stream():
        text_buf = ""
        _tok = {"in": 0, "out": 0}
        async with _model_session(model), httpx.AsyncClient(timeout=300) as client:
            async for chunk in _llm.stream(client, {
                "model": model, "think": False,
                "messages": [{"role": "system", "content": _SCIENCE_PROMPT},
                             {"role": "user", "content": prompt}],
                "stream": True,
            }):
                try:
                    token = chunk.get("message", {}).get("content", "")
                    if token:
                        text_buf += token
                        yield f"data: {json.dumps({'type': 'text', 'content': token})}\n\n"
                    if chunk.get("done"):
                        _a, _b = _llm_tok(chunk)
                        _tok["in"] += _a
                        _tok["out"] += _b
                        break
                except Exception:
                    pass

        # Verbesserten Plan aus JSON-Block extrahieren
        text_clean = re.sub(r"<think>.*?</think>", "", text_buf, flags=re.DOTALL).strip()
        m = re.search(r"```json\s*(\{.*?\})\s*```", text_clean, re.DOTALL)
        if not m:
            m = re.search(r"(\{\"name\".*\})", text_clean, re.DOTALL)
        if m:
            try:
                improved = json.loads(m.group(1))
                improved["name"] = improved.get("name", "Verbesserter Plan") + " (KI-Synthese)"
                yield f"data: {json.dumps({'type': 'plan', 'plan': improved})}\n\n"
            except Exception:
                pass
        yield f"data: {json.dumps({'type': 'done', 'tokens': _tok})}\n\n"

    return StreamingResponse(
        _stream(), media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


async def _ensure_plan_rag(plan: dict) -> dict:
    """Stellt sicher, dass der Plan eine eigene Wissensdatenbank besitzt
    (wird bei der ersten Tätigkeits-Recherche automatisch angelegt)."""
    cid = plan.get("rag_collection_id")
    coll = await _db.rag_get_collection(cid) if cid else None
    if coll:
        return coll
    from tools.rag import tier_config
    tc = tier_config("6gb")
    coll = {
        "id": f"rag_{uuid.uuid4().hex[:12]}",
        "name": (f"Plan: {plan.get('name', 'Plan')}")[:60],
        "embed_model": EMBED_MODEL,
        "tier": "plan",
        "chunk_size": tc["chunk_size"], "chunk_overlap": tc["chunk_overlap"],
        "top_k": tc["top_k"], "embed_gpu": False, "clean": True,
        "char_limit": tc["char_limit"], "strictness": "korrekt",
        "created_at": time.time(),
    }
    await _db.rag_create_collection(coll)
    plan["rag_collection_id"] = coll["id"]
    return coll


@router.post("/api/plans/{pid}/research-task")
async def plan_research_task(pid: str, req: Request):
    """Recherchiert eine einzelne Tätigkeit wissenschaftlich: adaptiver Agent →
    Web-Recherche → Markdown-Dossier → Einbettung ins plan-spezifische RAG →
    Verlinkung mit der Tätigkeit. Macht den Plan interaktiv (RAG je Plan)."""
    import re as _re
    from tools.search import search_with_sources
    from tools.rag import ingest_file

    body = await req.json()
    task_id = body.get("task_id")
    model = _pick_model(body.get("model"))
    fp = _plan_path_by_id(pid)
    if not fp or not fp.exists():
        raise HTTPException(status_code=404, detail="Plan nicht gefunden")
    plan = json.loads(fp.read_text(encoding="utf-8"))
    task = next((t for t in plan.get("tasks", []) if t.get("id") == task_id), None)
    if not task:
        raise HTTPException(status_code=404, detail="Tätigkeit nicht gefunden")

    coll = await _ensure_plan_rag(plan)
    tname = task.get("name", task_id)
    context = (plan.get("name", "") + " — " + (plan.get("description", "") or "")).strip(" —")
    query = f"{tname} {context}".strip()

    # 1. Adaptiver Agent aus der Tätigkeit ableiten
    _tok = {"in": 0, "out": 0}
    role, persona = await _derive_adaptive_prompt(
        f"Projektaufgabe: {tname}. Projektkontext: {context}", model, tok=_tok)

    # 2. Web-Recherche
    try:
        sources, search_text = await search_with_sources(query, 5)
    except Exception as e:
        sources, search_text = [], f"(Websuche fehlgeschlagen: {e})"

    # 3. Wissenschaftliche Synthese als Markdown
    _sys = "\n\n".join(p for p in (_SCIENCE_PROMPT, persona) if p)
    prompt = (
        f"Erstelle ein strukturiertes, wissenschaftlich sorgfältiges Kurzdossier in **Markdown** "
        f"zur Projekttätigkeit {tname} (Kontext: {context}). Stütze dich auf die folgenden "
        f"Suchergebnisse und zitiere Quellen mit Link. Erfinde nichts.\n\n"
        f"Suchergebnisse:\n{search_text[:6000]}\n\n"
        f"Gliederung:\n## {tname}\n### Überblick\n### Vorgehen / Methodik\n"
        f"### Wichtige Punkte & Belege\n### Risiken / Offene Fragen\n### Quellen"
    )
    try:
        async with _model_session(model), httpx.AsyncClient(timeout=300) as client:
            resp = await _llm.chat(client,{
                "model": model, "think": False, "stream": False,
                "messages": [{"role": "system", "content": _sys},
                             {"role": "user", "content": prompt}],
            })
            resp.raise_for_status()
            _rt_j = resp.json()
            _a, _b = _llm_tok(_rt_j)
            _tok["in"] += _a
            _tok["out"] += _b
            md = _rt_j.get("message", {}).get("content", "")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Synthese fehlgeschlagen: {e}")
    md = _re.sub(r"<think>.*?</think>", "", md, flags=_re.DOTALL).strip()
    # gesicherte Quellenliste anhängen (nicht vom Modell erfunden)
    if sources:
        md += "\n\n### Quellen\n" + "\n".join(
            f"- [{s.get('title', 'Quelle')}]({s.get('url', '')})" for s in sources if s.get("url"))

    # 4. Ins plan-spezifische RAG einbetten
    try:
        await ingest_file(coll, md, f"{task_id} – {tname}", f"doc_{uuid.uuid4().hex[:12]}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"RAG-Einbettung fehlgeschlagen: {e}")

    # 4b. Dossier automatisch als Markdown-Datei exportieren
    #     data/dossiers/<plan-slug>_<planid>/<task-slug>_<taskid>.md
    plan_folder = DOSSIERS_DIR / f"{_to_slug(plan.get('name', 'plan'))}_{pid[:8]}"
    plan_folder.mkdir(parents=True, exist_ok=True)
    md_path = plan_folder / f"{_to_slug(tname)}_{_to_slug(str(task_id))}.md"
    frontmatter = (
        "---\n"
        f"plan: {plan.get('name', '')}\n"
        f"task_id: {task_id}\n"
        f"task: {tname}\n"
        f"role: {role}\n"
        f"exported: {datetime.now().isoformat(timespec='seconds')}\n"
        f"sources: {len(sources)}\n"
        "---\n\n"
    )
    try:
        md_path.write_text(frontmatter + md, encoding="utf-8")
    except Exception as e:
        _write_log({"type": "error", "where": "dossier_export",
                    "file": md_path.name, "error": str(e)})
        md_path = None

    # 5. Dossier an die Tätigkeit hängen und Plan speichern
    task["doc"] = md
    task["doc_role"] = role
    task["researched"] = True
    if md_path:
        task["doc_file"] = str(md_path.relative_to(DATA_DIR)).replace("\\", "/")
    plan["updated_at"] = time.time()
    fp.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")

    return {"ok": True, "task_id": task_id, "role": role, "md": md,
            "collection_id": coll["id"], "collection_name": coll["name"],
            "n_sources": len(sources),
            "doc_file": task.get("doc_file"),
            "tokens": _tok}


@router.post("/api/plans/derive-agent")
async def plan_derive_agent(req: Request):
    """Leitet aus Projektbeschreibung + Ziel einen Projektplaner-Agenten (System-Prompt) ab.
    Dieser steuert anschließend die Aufgaben-/Ressourcenvorschläge."""
    import re
    body = await req.json()
    description = (body.get("description") or "").strip()
    if not description:
        raise HTTPException(400, "Keine Projektbeschreibung angegeben")
    _model = _pick_model(body.get("model"))

    async with _model_session(_model), httpx.AsyncClient(timeout=120) as client:
        resp = await _llm.chat(client,{
            "model": _model,
            "think": False,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "Du erstellst System-Prompts für KI-Projektplaner. "
                        "Antworte NUR mit dem fertigen System-Prompt als Fließtext, ohne Einleitung, "
                        "ohne Erklärung, ohne JSON, ohne Markdown."
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"Projektbeschreibung und Ziel:\n{description}\n\n"
                        "Erstelle den System-Prompt für einen fachkundigen Projektplaner-Agenten zu diesem Projekt.\n"
                        "Regeln:\n"
                        "- Beginne mit 'Du bist ...'\n"
                        "- Er soll passende Aufgaben, Abhängigkeiten, Dauern und Ressourcen "
                        "(Mensch/Hardware/Software) mit Zeiten und Kosten vorschlagen\n"
                        "- Antworte auf Deutsch, maximal 120 Wörter, nur Fließtext"
                    ),
                },
            ],
            "stream": False,
        })
        resp.raise_for_status()
        _da_j = resp.json()
        _da_ti, _da_to = _llm_tok(_da_j)
        raw = _da_j.get("message", {}).get("content", "")

    system_prompt = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL).strip()
    system_prompt = re.sub(r"^```[a-zA-Z]*\s*", "", system_prompt).strip()
    system_prompt = re.sub(r"\s*```$", "", system_prompt).strip()
    # Falls das Modell doch JSON lieferte, den system_prompt-Wert herausziehen
    if system_prompt.startswith("{"):
        ms = re.search(r'"system_prompt"\s*:\s*"([\s\S]+?)"\s*[},]', system_prompt)
        if ms:
            system_prompt = ms.group(1).strip()
    if not system_prompt or system_prompt.startswith("{"):
        system_prompt = (
            "Du bist ein erfahrener Projektplaner und schlägst zu diesem Projekt passende Aufgaben, "
            "Abhängigkeiten, Dauern und Ressourcen (Mensch/Hardware/Software) mit Zeiten und Kosten vor."
        )
    # Kurzname heuristisch aus den ersten sinnvollen Wörtern der Beschreibung
    words = re.findall(r"[A-Za-zÄÖÜäöüß0-9\-]{3,}", description)
    agent_name = " ".join(words[:3]) + "-Planer" if words else "Projektplaner"
    return {"agent_name": agent_name[:40], "system_prompt": system_prompt,
            "tokens": {"in": _da_ti, "out": _da_to}}


# Schlüsselwörter zur Typ-Erkennung. Mensch wird ZUERST geprüft, damit Rollen wie
# "Softwareentwickler" nicht fälschlich als Software klassifiziert werden.
_HUMAN_KW = (
    "ingenieur", "techniker", "entwickler", "leiter", "mitarbeiter", "monteur", "planer",
    "analyst", "experte", "expertin", "redakteur", "prüfer", "pruefer", "admin", "manager",
    "berater", "konstrukteur", "mechaniker", "mechatroniker", "elektroniker", "elektrotechnik",
    "elektromechanik", "programmierer", "designer", "architekt", "tester", "trainer",
    "einkauf", "einkäufer", "einkaeufer", "controller", "controlling", "justiziar",
    "geschäftsführung", "geschaeftsfuehrung", "scientist", "fachkraft", "personal",
    "pilot", "operator", "bediener", "wissenschaftler", "sachbearbeiter", "assistenz",
    "praktikant", "werkstudent", "schulungs", "kraft", "team", "rolle",
)
_HARDWARE_KW = (
    "sensor", "server", "gpu", "grafikkarte", "rechner", "workstation", "laptop", " pc",
    "maschine", "gerät", "geraet", "drucker", "kabel", "rack", "motor", "welle", "encoder",
    "nas", "switch", "usv", "messgerät", "messgeraet", "messstreifen", "dehnungsmess",
    "kamera", "roboter", "antrieb", "netzteil", "batterie", "akku", "scheibe", "prüfstand",
    "pruefstand", "prüfling", "hubwagen", "werkzeug", "anlage", "platine", "bauteil",
    "komponente", "hardware", "speicher", "storage", "festplatte", "ssd", "router",
)
_SOFTWARE_KW = (
    "lizenz", "software", "labview", "cad", "solidworks", "autocad", "fusion", "revit",
    "python", "matlab", "simulink", "simulation", "tool", "programm", " app", "datenbank",
    "suite", "runtime", "betriebssystem", "plugin", "framework", "office", "abonnement",
    "saas", "api", "ollama", "grafana", "docker", "vektor-db", "backup-software",
)


def _classify_resource_kind(name: str):
    """Errät den Ressourcentyp anhand von Schlüsselwörtern im Namen.
    Gibt 'human'/'hardware'/'software' zurück oder None, wenn unsicher."""
    n = " " + (name or "").lower() + " "
    if any(k in n for k in _HUMAN_KW):
        return "human"
    if any(k in n for k in _HARDWARE_KW):
        return "hardware"
    if any(k in n for k in _SOFTWARE_KW):
        return "software"
    return None


def _coerce_resource(r: dict) -> dict:
    """Normalisiert eine Ressourcen-Angabe der KI auf das interne Schema.
    Korrigiert den Typ per Heuristik, wenn der Name eindeutig ist."""
    name = str(r.get("name", "")).strip()[:80]
    kind = str(r.get("kind", "human")).lower().strip()
    if kind not in ("human", "hardware", "software"):
        kind = "human"
    guessed = _classify_resource_kind(name)
    if guessed:
        kind = guessed
    def _num(v):
        try:
            return max(0, float(v))
        except Exception:
            return 0
    return {
        "kind": kind,
        "name": name,
        "qty": _num(r.get("qty", 1)) or 1,
        "hours": _num(r.get("hours", 0)),
        "rate": _num(r.get("rate", 0)),
    }


def _normalize_catalog(catalog) -> list:
    """Bringt einen Ressourcen-Katalog auf {kind, name, rate}."""
    out = []
    for c in (catalog or []):
        if not isinstance(c, dict):
            continue
        name = str(c.get("name", "")).strip()[:80]
        if not name:
            continue
        kind = str(c.get("kind", "")).lower().strip()
        if kind not in ("human", "hardware", "software"):
            kind = _classify_resource_kind(name) or "human"
        try:
            rate = max(0, float(c.get("rate", 0)))
        except Exception:
            rate = 0
        out.append({"kind": kind, "name": name, "rate": rate})
    return out


def _apply_catalog_to_resources(res_list: list, catalog: list, mode: str) -> list:
    """Gleicht Ressourcen mit dem Katalog ab.
    - 'strict': nur Katalog-Ressourcen behalten (Typ+Satz aus Katalog).
    - 'extend': Treffer an Katalog angleichen, neue (Zukauf) behalten.
    - sonst: unverändert."""
    if not catalog or mode not in ("strict", "extend"):
        return res_list
    out = []
    for r in res_list:
        match = _match_catalog(r.get("name", ""), catalog)
        if match:
            r = {**r, "kind": match["kind"], "rate": match["rate"], "name": match["name"]}
            out.append(r)
        elif mode == "extend":
            r = {**r, "from_catalog": False}  # Zukauf / Ergänzung
            out.append(r)
        # strict + kein Treffer → verwerfen
    return out


def _catalog_prompt(catalog: list, mode: str) -> str:
    """Erzeugt den Katalog-Hinweis für das LLM."""
    if not catalog:
        return ""
    lines = []
    for c in catalog[:60]:
        unit = "€/h" if c["kind"] == "human" else "€/Einheit"
        lines.append(f"- [{c['kind']}] {c['name']} ({c['rate']:.0f} {unit})")
    cat = "Verfügbarer Ressourcen-Katalog:\n" + "\n".join(lines) + "\n\n"
    if mode == "strict":
        cat += ("Verwende AUSSCHLIESSLICH Ressourcen aus diesem Katalog mit den angegebenen "
                "Kostensätzen. Erfinde keine neuen Ressourcen.\n\n")
    elif mode == "extend":
        cat += ("Nutze bevorzugt Ressourcen aus diesem Katalog (mit den angegebenen Sätzen). "
                "Nur wenn nötig, darfst du zusätzliche Ressourcen ergänzen (Zukauf).\n\n")
    return cat


def _coerce_candidate(c: dict) -> dict:
    try:
        dur = max(0, float(c.get("duration", 1)))
    except Exception:
        dur = 1
    res = [_coerce_resource(r) for r in (c.get("resources") or []) if isinstance(r, dict)][:6]
    return {"name": str(c.get("name", "")).strip()[:120], "duration": dur, "resources": res}


@router.post("/api/plans/suggest-tasks")
async def suggest_tasks(req: Request):
    """Schlägt zu einer Aufgabe mehrere mögliche Vorgänger und Nachfolger vor
    (mit Dauer und Ressourcen) – zur Auswahl, nicht automatisch übernommen."""
    import re
    body = await req.json()
    _model = _pick_model(body.get("model"))
    system_prompt = (body.get("system_prompt") or "").strip() or (
        "Du bist ein erfahrener Projektplaner."
    )
    description = (body.get("description") or "").strip()
    tasks = body.get("tasks", [])
    anchor = body.get("anchor") or {}
    catalog = _normalize_catalog(body.get("resource_catalog"))
    res_mode = str(body.get("resource_mode", "free")).lower().strip()
    no_pred = bool(anchor.get("is_start"))   # Projektstart → keine Vorgänger
    no_succ = bool(anchor.get("is_end"))     # Projektende → keine Nachfolger
    tasks_summary = json.dumps(
        [{"id": t.get("id"), "name": t.get("name"), "duration": t.get("duration")} for t in tasks],
        ensure_ascii=False,
    )

    if no_pred and no_succ:
        return {"predecessors": [], "successors": []}

    if no_pred:
        scope = ("Diese Aufgabe ist als PROJEKTSTART markiert. Schlage KEINE Vorgänger vor "
                 "(predecessors=[]), nur bis zu 3 direkte NACHFOLGER.")
    elif no_succ:
        scope = ("Diese Aufgabe ist als PROJEKTENDE markiert. Schlage KEINE Nachfolger vor "
                 "(successors=[]), nur bis zu 3 direkte VORGÄNGER.")
    else:
        scope = "Schlage bis zu 3 sinnvolle direkte VORGÄNGER und bis zu 3 direkte NACHFOLGER dieser Aufgabe vor."

    user = (
        (f"Projektkontext: {description}\n\n" if description else "")
        + f"Bereits vorhandene Aufgaben: {tasks_summary}\n\n"
        + f"Betrachtete Aufgabe: \"{anchor.get('name', '')}\" (Dauer {anchor.get('duration', '?')} Tage)\n\n"
        + scope + " "
        + "Gib für jeden Vorschlag einen kurzen Namen, die Dauer in Tagen und die nötigen Ressourcen an "
        + "(Mensch/Hardware/Software) mit Menge, grober Zeit in Stunden und Kostensatz in Euro pro Stunde "
        + "(bei Hardware/Software pro Einheit, hours=0).\n\n"
        + _catalog_prompt(catalog, res_mode)
        + "Antworte NUR mit JSON in genau diesem Format, ohne Markdown, ohne Erklärung:\n"
        + '{"predecessors":[{"name":"Teile bestellen","duration":5,'
        + '"resources":[{"kind":"human","name":"Einkäufer","qty":1,"hours":4,"rate":55}]}],'
        + '"successors":[{"name":"Inbetriebnahme","duration":3,"resources":[]}]}\n'
        + "kind ist genau einer von: human, hardware, software."
    )

    async with _model_session(_model), httpx.AsyncClient(timeout=180) as client:
        resp = await _llm.chat(client,{
            "model": _model,
            "think": False,
            "messages": [
                {"role": "system", "content": system_prompt + " Antworte ausschließlich mit gültigem JSON."},
                {"role": "user", "content": user},
            ],
            "stream": False,
        })
        resp.raise_for_status()
        _st_j = resp.json()
        _st_ti, _st_to = _llm_tok(_st_j)
        raw = _st_j.get("message", {}).get("content", "")

    raw = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL).strip()
    raw = re.sub(r"^```[a-zA-Z]*\s*", "", raw).strip()
    raw = re.sub(r"\s*```$", "", raw).strip()
    preds, succs = [], []
    m = re.search(r"\{[\s\S]*\}", raw)
    if m:
        try:
            data = json.loads(m.group(0))
            preds = [_coerce_candidate(c) for c in (data.get("predecessors") or []) if isinstance(c, dict)][:3]
            succs = [_coerce_candidate(c) for c in (data.get("successors") or []) if isinstance(c, dict)][:3]
        except Exception:
            pass
    if no_pred:
        preds = []
    if no_succ:
        succs = []
    for c in preds + succs:
        c["resources"] = _apply_catalog_to_resources(c.get("resources", []), catalog, res_mode)
    return {"predecessors": preds, "successors": succs,
            "tokens": {"in": _st_ti, "out": _st_to}}


@router.post("/api/plans/detail-task")
async def detail_task(req: Request):
    """Detailliert eine ausgewählte Aufgabe per LLM: verfeinert Bezeichnung,
    Dauer, Beschreibung und Ressourcen und schlägt zusätzlich Vorgänger und
    Nachfolger vor. Alle Werte sind im Frontend wähl- und editierbar."""
    body = await req.json()
    _model = _pick_model(body.get("model"))
    system_prompt = (body.get("system_prompt") or "").strip() or "Du bist ein erfahrener Projektplaner."
    description = (body.get("description") or "").strip()
    tasks = body.get("tasks", [])
    task = body.get("task") or {}
    catalog = _normalize_catalog(body.get("resource_catalog"))
    res_mode = str(body.get("resource_mode", "free")).lower().strip()
    no_pred = bool(task.get("is_start"))
    no_succ = bool(task.get("is_end"))

    cur_res = ", ".join(
        f"{r.get('kind','')}:{r.get('name','')}" for r in (task.get("resource_list") or [])
    ) or "—"
    tasks_summary = json.dumps(
        [{"id": t.get("id"), "name": t.get("name")} for t in tasks if t.get("id") != task.get("id")],
        ensure_ascii=False,
    )

    if no_pred and no_succ:
        scope = "Schlage KEINE Vorgänger und KEINE Nachfolger vor (predecessors=[], successors=[])."
    elif no_pred:
        scope = "Diese Aufgabe ist PROJEKTSTART: predecessors=[], aber bis zu 3 Nachfolger."
    elif no_succ:
        scope = "Diese Aufgabe ist PROJEKTENDE: successors=[], aber bis zu 3 Vorgänger."
    else:
        scope = "Schlage zusätzlich bis zu 3 sinnvolle direkte Vorgänger und bis zu 3 Nachfolger vor."

    user = (
        (f"Projektkontext: {description}\n\n" if description else "")
        + f"Andere Aufgaben: {tasks_summary}\n\n"
        + f"Zu detaillierende Aufgabe: \"{task.get('name', '')}\" "
        + f"(aktuelle Dauer {task.get('duration', '?')} Tage, aktuelle Ressourcen: {cur_res}).\n\n"
        + "Detailliere DIESE Aufgabe: eine präzisere Bezeichnung (name), eine realistische "
        + "Dauer in Tagen (duration), eine kurze Detailbeschreibung in 1–2 Sätzen (notes) und die "
        + "nötigen Ressourcen (Mensch/Hardware/Software mit Menge, Stunden, Kostensatz €). "
        + scope + "\n\n"
        + _catalog_prompt(catalog, res_mode)
        + "Antworte NUR mit JSON in genau diesem Format, ohne Markdown, ohne Erklärung:\n"
        + '{"detail":{"name":"...","duration":5,"notes":"...","resources":[{"kind":"human","name":"...","qty":1,"hours":8,"rate":80}]},'
        + '"predecessors":[{"name":"...","duration":3,"resources":[]}],'
        + '"successors":[{"name":"...","duration":2,"resources":[]}]}\n'
        + "kind ist genau einer von: human, hardware, software."
    )

    async with _model_session(_model), httpx.AsyncClient(timeout=180) as client:
        resp = await _llm.chat(client,{
            "model": _model,
            "think": False,
            "messages": [
                {"role": "system", "content": system_prompt + " Antworte ausschließlich mit gültigem JSON."},
                {"role": "user", "content": user},
            ],
            "stream": False,
        })
        resp.raise_for_status()
        _dt_j = resp.json()
        _dt_ti, _dt_to = _llm_tok(_dt_j)
        raw = _dt_j.get("message", {}).get("content", "")

    raw = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL).strip()
    raw = re.sub(r"^```[a-zA-Z]*\s*", "", raw).strip()
    raw = re.sub(r"\s*```$", "", raw).strip()

    # Detail mit aktuellen Werten als Fallback
    try:
        cur_dur = float(task.get("duration", 1))
    except Exception:
        cur_dur = 1
    detail = {
        "name": str(task.get("name", "")),
        "duration": cur_dur,
        "notes": str(task.get("notes", "")),
        "resources": [],
    }
    preds, succs = [], []
    m = re.search(r"\{[\s\S]*\}", raw)
    if m:
        try:
            data = json.loads(m.group(0))
            d = data.get("detail") or {}
            if d.get("name"):
                detail["name"] = str(d["name"]).strip()[:120]
            try:
                detail["duration"] = max(0, float(d.get("duration", cur_dur)))
            except Exception:
                pass
            if d.get("notes"):
                detail["notes"] = str(d["notes"]).strip()[:400]
            detail["resources"] = [_coerce_resource(r) for r in (d.get("resources") or []) if isinstance(r, dict)][:6]
            preds = [_coerce_candidate(c) for c in (data.get("predecessors") or []) if isinstance(c, dict)][:3]
            succs = [_coerce_candidate(c) for c in (data.get("successors") or []) if isinstance(c, dict)][:3]
        except Exception:
            pass

    if no_pred:
        preds = []
    if no_succ:
        succs = []
    detail["resources"] = _apply_catalog_to_resources(detail["resources"], catalog, res_mode)
    for c in preds + succs:
        c["resources"] = _apply_catalog_to_resources(c.get("resources", []), catalog, res_mode)
    return {"detail": detail, "predecessors": preds, "successors": succs,
            "tokens": {"in": _dt_ti, "out": _dt_to}}


@router.post("/api/plans/insert-between")
async def insert_between(req: Request):
    """Schlägt per LLM 1–3 sinnvolle Zwischenvorgänge vor, die zwischen zwei
    Aufgaben A und B passen. Die KI liest Bezeichnung/Notizen beider Aufgaben
    und überlegt, welche Tätigkeit die Lücke schließt. Auswahl/Editieren im
    Frontend; das Frontend verdrahtet anschließend A→neu→B."""
    body = await req.json()
    _model = _pick_model(body.get("model"))
    system_prompt = (body.get("system_prompt") or "").strip() or "Du bist ein erfahrener Projektplaner."
    description = (body.get("description") or "").strip()
    a = body.get("task_a") or {}
    b = body.get("task_b") or {}
    catalog = _normalize_catalog(body.get("resource_catalog"))
    res_mode = str(body.get("resource_mode", "free")).lower().strip()

    def _desc(t):
        parts = [str(t.get("name", "")).strip()]
        if t.get("notes"):
            parts.append(f"(Notiz: {str(t['notes']).strip()})")
        return " ".join(p for p in parts if p) or "?"

    user = (
        (f"Projektkontext: {description}\n\n" if description else "")
        + f"Aufgabe A (Vorgänger): \"{_desc(a)}\", Dauer {a.get('duration', '?')} Tage.\n"
        + f"Aufgabe B (Nachfolger): \"{_desc(b)}\", Dauer {b.get('duration', '?')} Tage.\n\n"
        + "Überlege, welche 1–3 Tätigkeiten logisch ZWISCHEN A und B liegen müssen, "
        + "damit der Ablauf von A nach B vollständig und konsistent ist. Jede Tätigkeit "
        + "hat name, duration (Tage) und resources (Mensch/Hardware/Software mit Menge, "
        + "Stunden, Kostensatz €).\n\n"
        + _catalog_prompt(catalog, res_mode)
        + "Antworte NUR mit JSON in genau diesem Format, ohne Markdown, ohne Erklärung:\n"
        + '{"tasks":[{"name":"...","duration":3,"notes":"...","resources":[{"kind":"human","name":"...","qty":1,"hours":8,"rate":80}]}]}\n'
        + "kind ist genau einer von: human, hardware, software."
    )

    async with _model_session(_model), httpx.AsyncClient(timeout=180) as client:
        resp = await _llm.chat(client,{
            "model": _model,
            "think": False,
            "messages": [
                {"role": "system", "content": system_prompt + " Antworte ausschließlich mit gültigem JSON."},
                {"role": "user", "content": user},
            ],
            "stream": False,
        })
        resp.raise_for_status()
        _ib_j = resp.json()
        _ib_ti, _ib_to = _llm_tok(_ib_j)
        raw = _ib_j.get("message", {}).get("content", "")

    raw = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL).strip()
    raw = re.sub(r"^```[a-zA-Z]*\s*", "", raw).strip()
    raw = re.sub(r"\s*```$", "", raw).strip()

    tasks = []
    m = re.search(r"\{[\s\S]*\}", raw)
    if m:
        try:
            data = json.loads(m.group(0))
            for c in (data.get("tasks") or [])[:3]:
                if not isinstance(c, dict):
                    continue
                cand = _coerce_candidate(c)
                cand["notes"] = str(c.get("notes", "")).strip()[:400]
                cand["resources"] = _apply_catalog_to_resources(cand.get("resources", []), catalog, res_mode)
                tasks.append(cand)
        except Exception:
            pass

    return {"tasks": tasks, "tokens": {"in": _ib_ti, "out": _ib_to}}


@router.post("/api/plans/from-list")
async def plan_from_list(req: Request):
    """Konvertiert eine flache Aufgabenliste (aus Anfrage/Ausschreibung) in einen
    strukturierten Projektplan mit Abhängigkeiten, Dauern und Bereichen.
    Streaming-SSE-Endpunkt."""
    import re

    body = await req.json()
    task_list = (body.get("task_list") or "").strip()
    if not task_list:
        raise HTTPException(400, "Keine Aufgabenliste angegeben")
    name = (body.get("name") or "Projekt aus Liste").strip()
    _model = _pick_model(body.get("model"))

    async def _gen():
        yield _sse({"type": "status", "message": "Analysiere Aufgabenliste…"})
        system = (
            "Du bist ein erfahrener Projektmanager. Du erhältst eine einfache, unstrukturierte "
            "Aufgabenliste (z. B. aus einer Anfrage oder Ausschreibung). Deine Aufgabe: "
            "Erstelle daraus einen vollständigen, logisch geordneten Projektplan mit realistischen "
            "Dauern und Abhängigkeiten. Fasse verwandte Tätigkeiten in Bereiche (area) zusammen."
        )
        user = (
            f"Aufgabenliste:\n{task_list}\n\n"
            "Erzeuge einen strukturierten Projektplan. Verwende fortlaufende IDs T1, T2, … "
            "Weise jeder Aufgabe einen 'area' (Bereich/Phase, z. B. 'Planung', 'Konstruktion', "
            "'Test', 'Abnahme') zu. Schätze realistische Dauern in Arbeitstagen. "
            "Setze 'predecessors' als Liste direkter Vorgänger-IDs.\n\n"
            "Antworte NUR mit JSON ohne Markdown, ohne Erklärung:\n"
            '{"tasks":[{"id":"T1","name":"Aufgabe","duration":3,"predecessors":[],"area":"Planung"},'
            '{"id":"T2","name":"Aufgabe","duration":2,"predecessors":["T1"],"area":"Planung"}]}'
        )
        payload = {
            "model": _model,
            "think": False,
            "format": "json",
            "messages": [
                {"role": "system", "content": system + " Antworte ausschließlich mit gültigem JSON."},
                {"role": "user",   "content": user},
            ],
            "stream": False,
            "options": {"num_ctx": 8192},
        }
        try:
            async with _model_session(_model), httpx.AsyncClient(timeout=300) as client:
                resp = await _llm.chat(client,payload)
                resp.raise_for_status()
                _fl_j = resp.json()
                _fl_ti, _fl_to = _llm_tok(_fl_j)
                raw = _fl_j.get("message", {}).get("content", "")
        except Exception as e:
            yield _sse({"type": "error", "message": str(e)})
            return

        yield _sse({"type": "status", "message": "Verarbeite Antwort…"})

        raw = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL).strip()
        raw = re.sub(r"^```[a-zA-Z]*\s*", "", raw).strip()
        raw = re.sub(r"\s*```$", "", raw).strip()

        rawtasks = []
        m = re.search(r"\{[\s\S]*\}", raw)
        if m:
            try:
                rawtasks = json.loads(m.group(0)).get("tasks") or []
            except Exception:
                rawtasks = []

        if not rawtasks:
            yield _sse({"type": "error", "message":
                "Das Modell lieferte kein verwertbares JSON. Bitte ein größeres Modell verwenden."})
            return

        tasks, seen = [], set()
        for i, t in enumerate(rawtasks, start=1):
            if not isinstance(t, dict):
                continue
            tid = str(t.get("id") or f"T{i}").strip() or f"T{i}"
            while tid in seen:
                tid = f"{tid}_{i}"
            seen.add(tid)
            try:
                dur = max(1, float(t.get("duration", 1)))
            except Exception:
                dur = 1
            preds = [str(p).strip() for p in (t.get("predecessors") or []) if str(p).strip()]
            tasks.append({
                "id": tid,
                "name": str(t.get("name", tid)).strip()[:120],
                "duration": dur,
                "predecessors": preds,
                "successors": [],
                "resources": "",
                "resource_list": [],
                "notes": "",
                "area": str(t.get("area") or "").strip()[:40],
                "is_start": False,
                "is_end": False,
            })

        ids = {t["id"] for t in tasks}
        by_id = {t["id"]: t for t in tasks}
        for t in tasks:
            t["predecessors"] = [p for p in t["predecessors"] if p in ids and p != t["id"]]
        for t in tasks:
            for p in t["predecessors"]:
                by_id[p]["successors"].append(t["id"])
        for t in tasks:
            if not t["predecessors"]:
                t["is_start"] = True
            if not t["successors"]:
                t["is_end"] = True

        plan = {"name": name, "description": "", "tasks": tasks}
        yield _sse({"type": "plan", "plan": plan, "tokens": {"in": _fl_ti, "out": _fl_to}})

    return StreamingResponse(_gen(), media_type="text/event-stream")


# ── Intelligente Verknüpfung / Auto-Strukturierung ────────────────────────────

def _reachable(start: str, target: str, preds: dict) -> bool:
    """Ist ``target`` ein (transitiver) Vorgänger von ``start`` (folgt preds)?"""
    stack = list(preds.get(start, ()))
    seen = set()
    while stack:
        n = stack.pop()
        if n == target:
            return True
        if n in seen:
            continue
        seen.add(n)
        stack.extend(preds.get(n, ()))
    return False


def _add_edge(p: str, t: str, preds: dict) -> bool:
    """Setzt ``p`` als Vorgänger von ``t``, falls das weder Zyklus noch Redundanz
    erzeugt. Gibt True zurück, wenn eine Kante hinzugefügt wurde."""
    if p == t or p in preds[t]:
        return False
    if _reachable(p, t, preds):   # t ist schon Vorgänger von p → würde Zyklus bilden
        return False
    if _reachable(t, p, preds):   # p ist bereits (transitiv) Vorgänger von t → redundant
        return False
    preds[t].add(p)
    return True


def _topo_order(ids: list, preds: dict) -> list:
    from collections import deque
    indeg = {i: 0 for i in ids}
    succ = {i: [] for i in ids}
    for t in ids:
        for p in preds.get(t, ()):
            if p in indeg:
                succ[p].append(t)
                indeg[t] += 1
    q = deque([i for i in ids if indeg[i] == 0])
    order = []
    while q:
        n = q.popleft()
        order.append(n)
        for s in succ[n]:
            indeg[s] -= 1
            if indeg[s] == 0:
                q.append(s)
    order += [i for i in ids if i not in order]   # evtl. Restzyklen hinten anhängen
    return order


@router.post("/api/plans/auto-structure")
async def auto_structure_plan(req: Request):
    """Schlägt für BESTEHENDE Aufgaben automatisch Phasen + Abhängigkeiten vor
    (fachlich via LLM), optional mit Ressourcen-Entzerrung (gleiche Rolle nicht
    parallel) und ohne künstliche Verkettung unabhängiger Stränge. Liefert
    {links:[{id,predecessors,area}], stats:{…}} — der Plan wird NICHT gespeichert;
    das Frontend zeigt eine Vorschau und wendet sie auf Bestätigung an."""
    body = await req.json()
    tasks_in = body.get("tasks") or []
    if not tasks_in:
        raise HTTPException(status_code=400, detail="Keine Aufgaben übergeben")
    opts = body.get("options") or {}
    want_deps = opts.get("dependencies", True)
    want_phases = opts.get("phases", True)
    want_leveling = bool(opts.get("resource_leveling"))
    model = _pick_model(body.get("model"), _model_for("general"))
    description = (body.get("description") or "").strip()

    ids, name_by, roles_by, area_by = [], {}, {}, {}
    for i, t in enumerate(tasks_in, 1):
        tid = str(t.get("id") or f"T{i}").strip() or f"T{i}"
        if tid in name_by:
            continue
        ids.append(tid)
        name_by[tid] = str(t.get("name", tid))[:120]
        roles_by[tid] = [str(r).strip() for r in (t.get("roles") or []) if str(r).strip()]
        area_by[tid] = str(t.get("area") or "").strip()[:40]
    idset = set(ids)
    preds = {i: set() for i in ids}
    area_out = dict(area_by)
    _as_ti, _as_to = 0, 0

    # 1) Fachliche Abhängigkeiten + Phasen via LLM
    if want_deps or want_phases:
        listing = "\n".join(
            f"- {tid}: {name_by[tid]}" + (f"  [Bereich: {area_by[tid]}]" if area_by[tid] else "")
            for tid in ids)
        sys = (
            "Du bist erfahrener Projektmanager. Du erhältst eine Liste bestehender "
            "Projektaufgaben mit IDs. Bestimme die LOGISCHE Struktur: welche Aufgabe muss "
            "vor welcher fertig sein (direkte Vorgänger), und ordne jede Aufgabe einer "
            "Projektphase (area) zu. Verkette NICHT künstlich — fachlich unabhängige Aufgaben "
            "dürfen parallel bleiben. Verwende AUSSCHLIESSLICH die vorgegebenen IDs. "
            'Antworte NUR mit JSON: {"links":[{"id":"T1","predecessors":["T2"],"area":"Konstruktion"}]}.'
        )
        usr = (f"Projektkontext: {description}\n\n" if description else "") + f"Aufgaben:\n{listing}"
        try:
            async with _model_session(model), httpx.AsyncClient(timeout=300) as client:
                resp = await _llm.chat(client, {
                    "model": model, "think": False, "stream": False, "format": "json",
                    "messages": [{"role": "system", "content": sys}, {"role": "user", "content": usr}],
                    "options": {"num_ctx": _profile_num_ctx()}, "keep_alive": KEEP_ALIVE,
                })
                resp.raise_for_status()
            _as_j = resp.json()
            _as_ti, _as_to = _llm_tok(_as_j)
            data = _parse_llm_json(_as_j.get("message", {}).get("content", "")) or {}
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Strukturierung fehlgeschlagen: {e}")
        for link in (data.get("links") or []):
            if not isinstance(link, dict):
                continue
            tid = str(link.get("id", "")).strip()
            if tid not in idset:
                continue
            if want_phases:
                a = str(link.get("area") or "").strip()[:40]
                if a:
                    area_out[tid] = a
            if want_deps:
                for p in (link.get("predecessors") or []):
                    p = str(p).strip()
                    if p in idset:
                        _add_edge(p, tid, preds)

    dep_links = sum(len(v) for v in preds.values())

    # 2) Ressourcen-Entzerrung: Aufgaben gleicher Rolle nacheinander legen
    leveled = 0
    if want_leveling:
        order = _topo_order(ids, preds)
        rank = {tid: n for n, tid in enumerate(order)}
        role_tasks = {}
        for tid in ids:
            for role in roles_by[tid]:
                role_tasks.setdefault(role.lower(), []).append(tid)
        for tlist in role_tasks.values():
            if len(tlist) < 2:
                continue
            tlist = sorted(tlist, key=lambda x: rank.get(x, 0))
            for a, b in zip(tlist, tlist[1:]):
                if _add_edge(a, b, preds):
                    leveled += 1

    links = [{"id": tid, "predecessors": sorted(preds[tid]), "area": area_out.get(tid, "")}
             for tid in ids]
    n_phases = len({a for a in area_out.values() if a})
    return {"links": links, "stats": {"tasks": len(ids), "dep_links": dep_links,
                                      "leveled_links": leveled, "phases": n_phases},
            "tokens": {"in": _as_ti, "out": _as_to}}


async def _generate_plan_core(_model, description, max_tasks, system_prompt="",
                              catalog=None, res_mode="free", rag_context="", num_ctx=None):
    """Kern der Plan-Generierung (Aufgaben mit Dauer, Abhängigkeiten, Ressourcen) per
    lokalem LLM. Gemeinsam genutzt von /api/plans/generate und /api/plans/from-document.
    ``num_ctx`` erzwingt ein größeres Kontextfenster (z. B. großes Dokument auf starkem
    Rechner). Nachfolger werden aus den Vorgängern abgeleitet."""
    import re
    # Keine harte 20er-Grenze – nur ein großzügiges Sicherheitsnetz gegen Ausreißer.
    max_tasks = max(5, min(int(max_tasks or 12), 300))
    big_request = max_tasks > 30
    system_prompt = (system_prompt or "").strip() or (
        "Du bist ein erfahrener Projektplaner und zerlegst Projekte in sinnvolle, "
        "chronologisch abhängige Arbeitspakete."
    )
    catalog = catalog or []
    res_mode = str(res_mode or "free").lower().strip()

    user = (
        (f"Verfügbares Hintergrundwissen (als Grundlage nutzen, nicht erfinden):\n{rag_context}\n\n" if rag_context else "") +
        f"Projektbeschreibung und Ziel:\n{description}\n\n"
        f"Erstelle einen vollständigen Projektplan mit {max_tasks} Aufgaben in sinnvoller Reihenfolge. "
        "Vergib fortlaufende IDs T1, T2, …. Jede Aufgabe hat: id, name, duration (Tage), "
        "predecessors (Liste der IDs direkter Vorgänger; die erste Aufgabe hat []), "
        "und resources (Mensch/Hardware/Software) mit Menge, Zeit in Stunden und Kostensatz in Euro "
        "(bei Hardware/Software pro Einheit, hours=0).\n\n"
        + _catalog_prompt(catalog, res_mode) +
        "Antworte NUR mit JSON in genau diesem Format, ohne Markdown, ohne Erklärung:\n"
        '{"tasks":[{"id":"T1","name":"Anforderungen klären","duration":3,"predecessors":[],'
        '"resources":[{"kind":"human","name":"Projektleiter","qty":1,"hours":16,"rate":90}]},'
        '{"id":"T2","name":"Konzept erstellen","duration":5,"predecessors":["T1"],"resources":[]}]}\n'
        "kind ist genau einer von: human, hardware, software.\n\n"
        f"WICHTIG: Der Plan MUSS GENAU {max_tasks} Aufgaben enthalten (IDs T1 bis "
        f"T{max_tasks}) – nicht weniger und nicht mehr. Zerlege das Vorhaben fein genug, "
        f"um auf {max_tasks} sinnvolle Arbeitspakete zu kommen."
    )

    payload = {
        "model": _model,
        "think": False,
        "format": "json",   # erzwingt valides JSON → robuster gegen Geplapper
        "messages": [
            {"role": "system", "content": system_prompt + " Antworte ausschließlich mit gültigem JSON."},
            {"role": "user", "content": user},
        ],
        "stream": False,
    }
    if num_ctx:
        # explizit gewünschtes Kontextfenster (z. B. großes Dokument auf starkem Rechner)
        payload["options"] = {"num_ctx": int(num_ctx)}
    elif big_request:
        # größeres Kontextfenster, damit lange Pläne nicht abgeschnitten werden
        payload["options"] = {"num_ctx": 8192}

    async with _model_session(_model), httpx.AsyncClient(timeout=600 if big_request else 300) as client:
        resp = await _llm.chat(client,payload)
        resp.raise_for_status()
        _j = resp.json()
        raw = _j.get("message", {}).get("content", "")
    _plan_ti, _plan_to = _llm_tok(_j)

    raw = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL).strip()
    raw = re.sub(r"^```[a-zA-Z]*\s*", "", raw).strip()
    raw = re.sub(r"\s*```$", "", raw).strip()

    # Balancierte {…}-Objekte aus einem (evtl. abgeschnittenen) String ziehen.
    def _extract_objects(s: str) -> list:
        objs, depth, start = [], 0, -1
        for i, ch in enumerate(s):
            if ch == "{":
                if depth == 0:
                    start = i
                depth += 1
            elif ch == "}" and depth > 0:
                depth -= 1
                if depth == 0 and start >= 0:
                    objs.append(s[start:i + 1]); start = -1
        return objs

    rawtasks = []
    m = re.search(r"\{[\s\S]*\}", raw)
    if m:
        try:
            rawtasks = json.loads(m.group(0)).get("tasks") or []
        except Exception:
            rawtasks = []

    # Rettungs-Parser: bei abgeschnittenem/teilweisem JSON einzelne Aufgaben bergen
    if not rawtasks:
        bracket = raw.find("[", raw.find('"tasks"')) if '"tasks"' in raw else raw.find("[")
        segment = raw[bracket:] if bracket >= 0 else raw
        for objstr in _extract_objects(segment):
            try:
                obj = json.loads(objstr)
                if isinstance(obj, dict) and obj.get("name"):
                    rawtasks.append(obj)
            except Exception:
                pass

    if not rawtasks:
        raise HTTPException(502,
            "Das Modell lieferte keinen verwertbaren Plan – bei dieser Aufgabenzahl ist ein "
            "größeres/leistungsfähigeres Modell erforderlich. Alternativ weniger Aufgaben "
            "anfordern oder den Plan in Phasen generieren.")

    # Normalisieren: IDs eindeutig machen, Aufgaben säubern
    tasks, seen = [], set()
    for i, t in enumerate(rawtasks[:max_tasks], start=1):
        if not isinstance(t, dict):
            continue
        tid = str(t.get("id") or f"T{i}").strip() or f"T{i}"
        while tid in seen:
            tid = f"{tid}_{i}"
        seen.add(tid)
        try:
            dur = max(0, float(t.get("duration", 1)))
        except Exception:
            dur = 1
        res = [_coerce_resource(r) for r in (t.get("resources") or []) if isinstance(r, dict)][:6]
        res = _apply_catalog_to_resources(res, catalog, res_mode)
        preds = [str(p).strip() for p in (t.get("predecessors") or []) if str(p).strip()]
        tasks.append({
            "id": tid, "name": str(t.get("name", tid)).strip()[:120], "duration": dur,
            "predecessors": preds, "successors": [], "resources": "",
            "resource_list": res, "notes": "", "is_start": False, "is_end": False,
        })

    # Ungültige Vorgänger-Verweise entfernen, Nachfolger ableiten
    ids = {t["id"] for t in tasks}
    by_id = {t["id"]: t for t in tasks}
    for t in tasks:
        t["predecessors"] = [p for p in t["predecessors"] if p in ids and p != t["id"]]
    for t in tasks:
        for p in t["predecessors"]:
            by_id[p]["successors"].append(t["id"])
    # Start/Ende markieren
    for t in tasks:
        if not t["predecessors"]:
            t["is_start"] = True
        if not t["successors"]:
            t["is_end"] = True

    # Warnung: kleine lokale Modelle liefern bei großen Plänen oft unvollständig
    warning = ""
    if big_request:
        warning = (
            f"Großer Plan angefordert ({max_tasks} Aufgaben). Kleine lokale Modelle "
            "(z. B. ministral-3:3b) liefern dann oft unvollständige oder inkonsistente "
            "Pläne – für viele Aufgaben ein größeres/leistungsfähigeres Modell verwenden "
            "oder den Plan in Phasen generieren."
        )
    if len(tasks) < max_tasks * 0.8:
        warning = (warning + " " if warning else "") + (
            f"Das Modell lieferte nur {len(tasks)} von {max_tasks} angeforderten Aufgaben."
        )

    return {"name": "", "description": description, "tasks": tasks,
            "requested": max_tasks, "warning": warning.strip(),
            "tokens": {"in": _plan_ti, "out": _plan_to}}


@router.post("/api/plans/generate")
async def generate_plan(req: Request):
    """Generiert aus einer Projektbeschreibung einen vollständigen Projektplan
    (Aufgaben mit Dauer, Abhängigkeiten und Ressourcen) per lokalem LLM."""
    body = await req.json()
    _model = _pick_model(body.get("model"))
    description = (body.get("description") or "").strip()
    if not description:
        raise HTTPException(400, "Keine Projektbeschreibung angegeben")
    catalog = _normalize_catalog(body.get("resource_catalog"))
    res_mode = str(body.get("resource_mode", "free")).lower().strip()
    rag_context = await _plan_rag_context(body.get("rag_collections"), description)
    return await _generate_plan_core(
        _model, description, body.get("max_tasks", 12),
        body.get("system_prompt"), catalog, res_mode, rag_context)


@router.post("/api/plans/from-document")
async def plan_from_document(
    file: UploadFile = File(...),
    max_tasks: int = Form(12),
    model: str = Form(""),
    resource_mode: str = Form("free"),
    rag_collections: str = Form(""),
):
    """Importiert ein Dokument (z. B. Strategiepapier), leitet daraus die nötigen
    Ressourcen ab und erzeugt einen vollständigen Projektplan mit der gewünschten
    Aufgabenzahl. Auf einem leistungsfähigen Rechner sind auch große Pläne möglich."""
    import os
    _model = _pick_model(model or None)
    tmp = UPLOADS_DIR / f"plandoc_{uuid.uuid4().hex}_{file.filename}"
    async with aiofiles.open(tmp, "wb") as fh:
        await fh.write(await file.read())
    try:
        text = await asyncio.to_thread(_extract_text, tmp)
    finally:
        tmp.unlink(missing_ok=True)
    text = (text or "").strip()
    if not text or text.startswith("[Lesefehler"):
        raise HTTPException(400, f"Dokument „{file.filename}“ konnte nicht gelesen werden.")
    try:
        max_tasks = max(5, min(int(max_tasks), 300))
    except Exception:
        max_tasks = 12
    # Eingabebudget an das Kontextfenster koppeln (großer Rechner → großes num_ctx).
    num_ctx = max(8192, _profile_num_ctx())
    doc = text[: num_ctx * 2]
    rag_context = await _plan_rag_context(rag_collections, doc[:500])
    system_prompt = (
        "Du bist ein erfahrener Projektplaner. Lies das beigefügte Dokument (z. B. ein "
        "Strategiepapier), leite die nötigen Arbeitspakete und Ressourcen ab und zerlege "
        f"das Vorhaben in GENAU {max_tasks} sinnvolle, chronologisch abhängige Aufgaben. "
        f"Zerlege fein genug bzw. fasse passend zusammen, damit es exakt {max_tasks} "
        "Arbeitspakete werden."
    )
    description = (
        f"Aus folgendem Dokument „{file.filename}“ einen Projektplan ableiten. Stütze "
        "Aufgaben und Ressourcen ausschließlich auf den Inhalt, erfinde nichts hinzu.\n\n"
        f"--- DOKUMENT ---\n{doc}"
    )
    result = await _generate_plan_core(
        _model, description, max_tasks, system_prompt, [], resource_mode,
        rag_context, num_ctx=num_ctx)
    result["name"] = (os.path.splitext(file.filename or "")[0] or "Importierter Plan")[:120]
    result["description"] = (
        f"Automatisch aus „{file.filename}“ abgeleiteter Plan (Ziel: {max_tasks} Vorgänge)."
    )
    result["source_document"] = file.filename
    return result


# ── /plan — Chat-getriebener Strategie- & Einsatzplan-Orchestrator ────────────
# Aus dem Chat-Verlauf (Briefing) baut der Befehl `/plan` in einem Zug: eine
# Strategie (Markdown), die nötigen Beratungs-Agenten (Vorschlag), einen Einsatz-/
# Ressourcenplan (Planer-Schema, Vorschlag) und eine Bewertungs-Jury (Vorschlag).
# Es wird NICHTS gespeichert — das Frontend zeigt eine Vorschau und legt auf
# Bestätigung über die vorhandenen Endpoints (/api/agents, /api/plans, /api/juries) an.


class PlanStrategyRequest(BaseModel):
    brief: str = ""                 # Chat-Verlauf als Briefing (frei diskutiert)
    extra: str = ""                 # optionale Randbedingungen nach „/plan"
    model: str = ""                 # leer → general-Modell aus dem Profil
    web_search: bool = False
    rag_collections: List[str] = []
    count: int = 12                 # Zielanzahl Aufgaben im Einsatzplan (4–60)
    # Feste Agenten: per „/plan … /dsgvo /tisax" angepinnte, bereits vorhandene
    # Agenten, die in jedem Fall als Berater + Jury-Mitglied verwendet werden.
    # Jeder Eintrag: {id, name, description, system_prompt, icon, category, tools}.
    pinned_agents: List[dict] = []


async def _plan_ground(query: str, web: bool, rag_collections: list,
                       top_k: int = 5, char_budget: int = 3000) -> str:
    """Sammelt optionales Belegmaterial (Websuche + RAG) wie beim Deepdive.
    ``top_k``/``char_budget`` skalieren den RAG-Abruf — für die Planerzeugung wird
    bewusst mehr aus der hinterlegten Datei gezogen, damit das ganze Dokument abgedeckt ist."""
    blocks = []
    if web and query:
        try:
            from tools.search import search_with_sources
            _, text = await search_with_sources(query, 5)
            if text:
                blocks.append("### Websuche\n" + text[:3000])
        except Exception:
            pass
    if rag_collections:
        try:
            from tools.rag import query_collections
            hits = await query_collections(rag_collections, query or "Strategie", top_k_cap=top_k)
            if hits:
                blocks.append("### Wissensdatenbank (hinterlegte Datei)\n" + "\n\n".join(
                    f"[{h.get('collection_name','?')} · {h.get('filename','?')}]\n{h.get('text','')}"
                    for h in hits)[:char_budget])
        except Exception:
            pass
    return "\n\n".join(blocks)


async def _plan_llm_json(model: str, sys: str, usr: str):
    """Ein LLM-Aufruf mit erzwungenem JSON → (data, tok_in, tok_out)."""
    try:
        async with _model_session(model), httpx.AsyncClient(timeout=300) as client:
            resp = await _llm.chat(client, {
                "model": model, "think": False, "stream": False, "format": "json",
                "messages": [{"role": "system", "content": sys},
                             {"role": "user", "content": usr}],
                "options": {"num_ctx": _profile_num_ctx()}, "keep_alive": KEEP_ALIVE,
            })
            resp.raise_for_status()
            j = resp.json()
        data = _parse_llm_json(j.get("message", {}).get("content", "")) or {}
        ti, to = _llm_tok(j)
        return data, ti, to
    except Exception:
        return {}, 0, 0


def _plan_clean_agents(data) -> list:
    """Holt aus einer (evtl. eigenwillig geformten) LLM-Antwort eine Agentenliste.
    Toleriert {agents:[…]}, eine nackte Liste oder ein einzelnes Agent-Objekt."""
    if isinstance(data, dict):
        raw = data.get("agents")
        if not isinstance(raw, list):
            raw = [data] if data.get("name") and data.get("system_prompt") else []
    elif isinstance(data, list):
        raw = data
    else:
        raw = []
    out = []
    for ag in raw[:6]:
        if not isinstance(ag, dict):
            continue
        nm = str(ag.get("name") or "").strip()[:60]
        sp = str(ag.get("system_prompt") or ag.get("prompt") or "").strip()
        if not nm or not sp:
            continue
        tools = [str(t).strip() for t in (ag.get("tools") or []) if str(t).strip()][:6] \
            or ["web_search", "calculate"]
        out.append({
            "name": nm,
            "description": str(ag.get("description") or "").strip()[:300],
            "system_prompt": sp,
            "icon": (str(ag.get("icon") or "🤖").strip() or "🤖")[:4],
            "category": str(ag.get("category") or "Beratung").strip()[:40] or "Beratung",
            "tools": tools,
        })
    return out


def _plan_pinned_agents(pinned) -> list:
    """Bringt die per „/plan … /agent" angepinnten, BEREITS vorhandenen Agenten auf
    die Vorschlags-Form und markiert sie (``pinned``=True, vorhandene ``id`` behalten)."""
    out = []
    for ag in (pinned or []):
        if not isinstance(ag, dict):
            continue
        nm = str(ag.get("name") or "").strip()[:60]
        sp = str(ag.get("system_prompt") or "").strip()
        if not nm:
            continue
        out.append({
            "id": str(ag.get("id") or "").strip() or None,
            "name": nm,
            "description": str(ag.get("description") or "").strip()[:300],
            "system_prompt": sp,
            "icon": (str(ag.get("icon") or "🤖").strip() or "🤖")[:4],
            "category": str(ag.get("category") or "Beratung").strip()[:40] or "Beratung",
            "tools": [str(t).strip() for t in (ag.get("tools") or []) if str(t).strip()][:6]
                     or ["web_search", "calculate"],
            "pinned": True,
        })
    return out


async def _plan_agents(model: str, task_text: str, strategy_md: str, pinned: list = None):
    """Schlägt die Beratungs-Agenten vor → (agents, tok_in, tok_out). Angepinnte
    feste Agenten stehen IMMER an erster Stelle; das LLM ergänzt nur fehlende Rollen
    (kein Duplikat). Mit einem Retry über einen schlankeren Prompt, falls ein kleines
    Modell zunächst leer/eigenwillig antwortet (3B-Modelle kollabieren sonst auf 0)."""
    fixed = _plan_pinned_agents(pinned)
    have = {a["name"].lower() for a in fixed}
    fixed_note = ""
    if fixed:
        fixed_note = ("\n\nDiese Experten sind bereits FEST gesetzt (NICHT erneut vorschlagen, "
                      "nicht duplizieren): " + ", ".join(a["name"] for a in fixed)
                      + ". Schlage nur ERGÄNZENDE, noch fehlende Experten vor.")
    sys_b = (
        "Du leitest aus Strategie und Briefing die nötigen FACH-Beratungs-Agenten ab "
        "(z. B. Kosten-, Datenschutz-/Compliance-, Zeitplan-, Hardware-Experte). Jeder "
        "Agent erhält einen prägnanten Namen, eine kurze Beschreibung und einen system_prompt, "
        "der seine Fachperspektive, Prüfkriterien und den deutschen Antwortstil festlegt. "
        "Antworte NUR mit JSON in genau diesem Format:\n"
        '{"agents":[{"name":"Kosten-Experte","description":"…","system_prompt":"Du bist …",'
        '"icon":"💶","category":"Beratung","tools":["web_search","calculate"]}]}\n'
        "Lege für JEDES genannte Bewertungskriterium einen eigenen Experten an. "
        "Maximal 6 Agenten, mindestens 2. icon ist ein passendes Emoji." + fixed_note
    )
    # Briefing zuerst (enthält die Kriterien), Strategie nur als kurzer Auszug —
    # ein langer Strategie-Block lässt kleine Modelle auf einen Agenten kollabieren.
    usr_b = task_text + (f"\n\nStrategie-Auszug:\n{strategy_md[:1200]}" if strategy_md else "")
    data, ti, to = await _plan_llm_json(model, sys_b, usr_b)
    extra_agents = _plan_clean_agents(data)
    if not extra_agents and not fixed:
        # Retry: minimaler, sehr direktiver Prompt nur auf Basis des Briefings.
        sys_r = (
            "Lies das Briefing und nenne die Fach-Experten, die das Vorhaben bewerten sollten "
            "(je Bewertungskriterium einen). Antworte NUR mit JSON: "
            '{"agents":[{"name":"…","description":"…","system_prompt":"Du bist …","icon":"🤖",'
            '"category":"Beratung","tools":["web_search","calculate"]}]}. Mindestens 2 Experten.'
        )
        data2, ti2, to2 = await _plan_llm_json(model, sys_r, task_text)
        extra_agents = _plan_clean_agents(data2); ti += ti2; to += to2
    # Zusammenführen: feste zuerst, dann neue ohne Namens-Duplikate, gesamt ≤ 6.
    merged = list(fixed)
    for a in extra_agents:
        if a["name"].lower() in have:
            continue
        have.add(a["name"].lower())
        merged.append(a)
        if len(merged) >= 6:
            break
    return merged, ti, to


def _plan_norm_tasks(rawtasks: list, max_tasks: int = 40) -> list:
    """Bringt KI-Aufgaben aufs Planer-Schema (wie generate_plan): IDs eindeutig,
    Ressourcen normalisiert, Vorgänger gesäubert, Nachfolger + Start/Ende abgeleitet."""
    tasks, seen = [], set()
    for i, t in enumerate((rawtasks or [])[:max_tasks], start=1):
        if not isinstance(t, dict):
            continue
        tid = str(t.get("id") or f"T{i}").strip() or f"T{i}"
        while tid in seen:
            tid = f"{tid}_{i}"
        seen.add(tid)
        try:
            dur = max(0, float(t.get("duration", 1)))
        except Exception:
            dur = 1
        res = [_coerce_resource(r) for r in (t.get("resource_list") or t.get("resources") or [])
               if isinstance(r, dict)][:6]
        preds = [str(p).strip() for p in (t.get("predecessors") or []) if str(p).strip()]
        roles = [str(r).strip() for r in (t.get("roles") or []) if str(r).strip()][:6]
        tasks.append({
            "id": tid, "name": str(t.get("name", tid)).strip()[:120], "duration": dur,
            "predecessors": preds, "successors": [], "resources": "",
            "resource_list": res, "roles": roles, "area": str(t.get("area") or "").strip()[:40],
            "notes": str(t.get("notes") or "").strip()[:300], "is_start": False, "is_end": False,
        })
    ids = {t["id"] for t in tasks}
    by_id = {t["id"]: t for t in tasks}
    for t in tasks:
        t["predecessors"] = [p for p in t["predecessors"] if p in ids and p != t["id"]]
    for t in tasks:
        for p in t["predecessors"]:
            by_id[p]["successors"].append(t["id"])
    for t in tasks:
        if not t["predecessors"]:
            t["is_start"] = True
        if not t["successors"]:
            t["is_end"] = True
    return tasks


async def _plan_strategy_generator(req: PlanStrategyRequest):
    model = _pick_model(req.model, _model_for("general"))
    brief = (req.brief or "").strip()
    extra = (req.extra or "").strip()
    if not brief and not extra:
        yield _sse({"type": "error", "message": "Kein Briefing — bitte erst im Chat diskutieren, dann /plan."})
        return
    task_text = "\n\n".join(p for p in (
        f"Diskussion / Briefing:\n{brief[:6000]}" if brief else "",
        f"Zusätzliche Randbedingungen:\n{extra}" if extra else "",
    ) if p)
    query = (extra or brief).split("\n", 1)[0][:200]
    count = max(4, min(int(req.count or 12), 60))
    tin = tout = 0

    # ── Phase A — Strategie (Markdown, optional geerdet) ──────────────────────
    yield _sse({"type": "phase", "label": "Strategie wird entwickelt…"})
    # Für die Planung mehr aus der hinterlegten Datei ziehen (skaliert mit der
    # gewünschten Aufgabenzahl), damit das ganze Dokument abgedeckt ist.
    grounding = await _plan_ground(
        query, req.web_search, req.rag_collections,
        top_k=max(8, min(count, 40)), char_budget=min(12000, 3000 + count * 150))
    sys_a = (
        "Du bist ein erfahrener Strategie- und Projektberater. Aus der Diskussion "
        "entwickelst du eine klare, umsetzbare Strategie. Gliedere als Markdown mit "
        "diesen Abschnitten: '## Ziel', '## Optionen', '## Bewertungskriterien', "
        "'## Vorgehen', '## Risiken', '## Meilensteine'. Sei konkret und entscheidungs"
        "orientiert. Wenn dir Belegmaterial eingeblendet ist, stütze dich darauf und "
        "nenne Quellen; erfinde keine Preise oder Rechtsstände."
    )
    usr_a = (f"Belegmaterial:\n{grounding}\n\n" if grounding else "") + task_text
    strategy_md = ""
    try:
        async with _model_session(model), httpx.AsyncClient(timeout=300) as client:
            resp = await _llm.chat(client, {
                "model": model, "think": False, "stream": False,
                "messages": [{"role": "system", "content": sys_a},
                             {"role": "user", "content": usr_a}],
                "options": {"num_ctx": _profile_num_ctx()}, "keep_alive": KEEP_ALIVE,
            })
            resp.raise_for_status()
            j = resp.json()
        strategy_md = re.sub(r"<think>.*?</think>", "", j.get("message", {}).get("content", ""),
                             flags=re.DOTALL).strip()
        a, b = _llm_tok(j); tin += a; tout += b
    except Exception as e:
        strategy_md = f"_(Strategie konnte nicht erstellt werden: {e})_"
    yield _sse({"type": "strategy", "markdown": strategy_md})

    # ── Phase B — Beratungs-Agenten (Vorschlag) ───────────────────────────────
    yield _sse({"type": "phase", "label": "Beratungs-Agenten werden abgeleitet…"})
    agents, a, b = await _plan_agents(model, task_text, strategy_md, req.pinned_agents)
    tin += a; tout += b
    yield _sse({"type": "agents", "agents": agents})

    # ── Phase C — Einsatz-/Ressourcenplan (Vorschlag) ─────────────────────────
    yield _sse({"type": "phase", "label": "Einsatz- & Ressourcenplan wird erstellt…"})
    roles_hint = ", ".join(a["name"] for a in agents) or "die nötigen Fachrollen"
    sys_c = (
        "Du bist ein erfahrener Projektplaner. Erstelle aus Strategie und Briefing einen "
        "Einsatz- und Ressourcenplan in sinnvollen Phasen. Vergib fortlaufende IDs T1, T2, …. "
        "Jede Aufgabe hat: id, name, duration (Tage), predecessors (Liste direkter Vorgänger-IDs; "
        "die erste hat []), area (Projektphase), roles (zuständige Rollen — nutze wo passend die "
        f"Beratungsrollen: {roles_hint}) und resource_list (Mensch/Hardware/Software) mit "
        "kind (human|hardware|software), name, qty, hours (bei Hardware/Software 0) und rate (€). "
        "Füge einen resource_catalog mit den verwendeten Rollen/Ressourcen und Kostensätzen an. "
        "Antworte NUR mit JSON in genau diesem Format, ohne Markdown:\n"
        '{"name":"Projektname","description":"…","tasks":[{"id":"T1","name":"Anforderungen klären",'
        '"duration":3,"predecessors":[],"area":"Vorbereitung","roles":["Projektleiter"],'
        '"resource_list":[{"kind":"human","name":"Projektleiter","qty":1,"hours":16,"rate":90}]}],'
        '"resource_catalog":[{"kind":"human","name":"Projektleiter","rate":90}]}\n'
        f"Erzeuge möglichst genau {count} Aufgaben mit echten Abhängigkeiten — lieber feinere "
        "Granularität als zu wenige. Stütze die Aufgaben, wo Belegmaterial vorliegt, auf dessen Inhalt."
    )
    usr_c = (
        (f"Belegmaterial (hinterlegte Datei):\n{grounding}\n\n" if grounding else "")
        + (f"Strategie:\n{strategy_md[:4000]}\n\n" if strategy_md else "")
        + task_text
    )
    data_c, a, b = await _plan_llm_json(model, sys_c, usr_c); tin += a; tout += b
    plan_tasks = _plan_norm_tasks(data_c.get("tasks") or [], max_tasks=count)
    plan = {
        "name": str(data_c.get("name") or (query[:60] or "Einsatzplan")).strip()[:120],
        "description": str(data_c.get("description") or "").strip()[:1000],
        "tasks": plan_tasks,
        "resource_catalog": _normalize_catalog(data_c.get("resource_catalog")),
        "resource_mode": "free",
    }
    yield _sse({"type": "plan", "plan": plan})

    # ── Phase D — Bewertungs-Jury (Vorschlag) ─────────────────────────────────
    yield _sse({"type": "phase", "label": "Bewertungs-Jury wird zusammengestellt…"})
    jury = {
        "name": (f"Bewertung: {query[:48]}" if query else "Bewertungs-Jury").strip(),
        "description": "Bewertet das Vorhaben aus den Fachperspektiven der Beratungs-Agenten.",
        "member_agent_names": [a["name"] for a in agents],
    }
    yield _sse({"type": "jury", "jury": jury})

    yield _sse({"type": "done", "tokens": {"in": tin, "out": tout}})


@router.post("/api/plan/strategy")
async def plan_strategy(req: PlanStrategyRequest):
    return StreamingResponse(
        _plan_strategy_generator(req),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )












