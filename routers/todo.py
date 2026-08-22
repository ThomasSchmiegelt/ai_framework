"""Router: KI-To-Do-Listen mit Wissensgraph (/api/todo)

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
# KI-To-Do-Listen mit Wissensgraph
# ══════════════════════════════════════════════════════════════════════════════
# Persistenz je Liste in TODO_DIR/<name>/list.json (Items, Kanten, Graph-Positionen).
# Struktur-Logik in tools/todo.py; hier HTTP + LLM-Helfer (Extraktion/Verknüpfung).

def _todo_build_tree(projects: list) -> list:
    """Flache Projektliste (aus der DB) in einen verschachtelten Baum überführen."""
    by_parent: dict = {}
    for p in projects:
        by_parent.setdefault(p.get("parent_id") or None, []).append(p)

    def _kids(pid):
        out = []
        for p in by_parent.get(pid, []):
            node = dict(p)
            node["children"] = _kids(p["id"])
            out.append(node)
        return out
    return _kids(None)


@router.get("/api/todo/tree")
async def todo_tree():
    await _db.todo_root_ensure(_todo_root_name())
    projects = await _db.todo_projects_all()
    return {"tree": _todo_build_tree(projects), "flat": projects}


@router.post("/api/todo/projects")
async def todo_project_create(req: Request):
    body = await req.json()
    await _db.todo_root_ensure(_todo_root_name())
    pid = "tp_" + uuid.uuid4().hex[:12]
    parent = str(body.get("parent_id", "") or "").strip() or "root"
    name = str(body.get("name", "")).strip() or "Projekt"
    return await _db.todo_project_create({
        "id": pid, "name": name, "parent_id": parent,
        "type": body.get("type", "projekt"), "title": body.get("title", name),
        "participants": body.get("participants") or [],
    })


@router.get("/api/todo/projects/{pid}")
async def todo_project_get(pid: str):
    proj = await _db.todo_project_get(pid)
    if not proj:
        raise HTTPException(status_code=404, detail="Projekt nicht gefunden")
    return proj


@router.put("/api/todo/projects/{pid}")
async def todo_project_save(pid: str, req: Request):
    from tools import todo as _todo
    body = await req.json()
    clean = _todo.sanitize_list(body)   # Items/Kanten/Teilnehmer normalisieren
    header = {
        "type": clean.get("type", "projekt"), "title": clean.get("title", ""),
        "date": clean.get("date", ""), "participants": clean.get("participants") or [],
        "project_ref": clean.get("project_id", ""),
        "settings": {**(clean.get("settings") or {}), "positions": clean.get("positions") or {}},
    }
    saved = await _db.todo_save_project(pid, header, clean.get("items") or [], clean.get("edges") or [])
    if not saved:
        raise HTTPException(status_code=404, detail="Projekt nicht gefunden")
    return saved


@router.post("/api/todo/projects/{pid}/rename")
async def todo_project_rename(pid: str, req: Request):
    body = await req.json()
    await _db.todo_project_rename(pid, str(body.get("name", "")).strip() or "Projekt")
    return {"ok": True}


@router.post("/api/todo/projects/{pid}/move")
async def todo_project_move(pid: str, req: Request):
    body = await req.json()
    await _db.todo_project_move(pid, str(body.get("parent_id", "") or "").strip() or "root")
    return {"ok": True}


@router.delete("/api/todo/projects/{pid}")
async def todo_project_delete(pid: str, reparent: bool = False):
    await _db.todo_project_delete(pid, reparent=reparent)
    return {"ok": True}


_TODO_EXTRACT_SYSTEM = (
    "Du wandelst eine Besprechungsnotiz / einen Freitext in eine strukturierte "
    "AUFGABENLISTE (To-Dos) um. Erkenne einzelne konkrete Aufgaben, den/die "
    "Zuständigen (nur wenn genannt; nutze bevorzugt die vorgegebenen Teilnehmer), "
    "eine Fälligkeit (falls genannt, Format wie im Text) und Abhängigkeiten "
    "zwischen Aufgaben (welche Aufgabe muss vor einer anderen erledigt sein). "
    "Nummeriere die Aufgaben ab 0 und verweise in 'blockiert_von' auf diese Nummern. "
    'Antworte NUR mit JSON: {"items":[{"n":0,"text":"<Aufgabe>","assignees":["<Name>"],'
    '"due":"<Frist>","blockiert_von":[<n>]}]}.'
)
_TODO_LINKS_SYSTEM = (
    "Du findest inhaltliche VERKNÜPFUNGEN zwischen bestehenden Aufgaben (To-Dos). "
    "Du bekommst eine nummerierte Aufgabenliste. Nenne sinnvolle gerichtete "
    "Beziehungen (source→target) mit kurzem Label (z. B. 'blockiert', 'gehört zu', "
    "'folgt auf'). Nur echte Zusammenhänge, keine erzwungenen. "
    'Antworte NUR mit JSON: {"edges":[{"source":<n>,"target":<n>,"label":"<kurz>"}]}.'
)
_TODO_NEXT_SYSTEM = (
    "Du bist ein pragmatischer Projekt-Assistent. Aus der gegebenen Aufgabenliste "
    "(mit Status und Abhängigkeiten) benennst du kurz: (1) was als Nächstes "
    "sinnvoll angegangen wird, (2) welche Aufgaben blockiert sind und warum, "
    "(3) womöglich Vergessenes. Knapp, in Stichpunkten, kein Geschwätz."
)
_TODO_ASK_SYSTEM = (
    "Du bist ein Analyse-Assistent für eine To-Do-/Projektdatenbank. Du beantwortest "
    "Fragen AUSSCHLIESSLICH anhand der bereitgestellten Aufgaben-Daten (Projekte, "
    "Aufgaben, Zuständige, Status, Fristen, Notizen, Anhänge, Abhängigkeiten). "
    "Erfinde nichts — steht etwas nicht in den Daten, sag es offen. Antworte auf Deutsch, "
    "klar strukturiert (Überschriften/Stichpunkte, wo sinnvoll). "
    "Bei Fragen zu Personen/Kollegen bleibe sachlich und neutral: leite Aussagen "
    "nachvollziehbar aus den Daten ab (z. B. Arbeitsschwerpunkte, Themen, Zuverlässigkeit "
    "anhand von Status/Fristen), spekuliere nicht über sensible Merkmale und formuliere "
    "keine abwertenden Urteile. Nenne, worauf du dich stützt (Projekt/Aufgabe)."
)


@router.post("/api/todo/extract")
async def todo_extract(req: Request):
    from tools import todo as _todo
    body = await req.json()
    text = str(body.get("text", "")).strip()
    if not text:
        return {"items": [], "edges": [], "tokens": {"in": 0, "out": 0}}
    participants = [str(p).strip() for p in (body.get("participants") or []) if str(p).strip()]
    model = _pick_model(body.get("model"), _model_for("general"))
    # Besprechungsheader als Kontext voranstellen (Thema, Datum, Teilnehmer) — hilft
    # der KI beim Zuordnen von Zuständigen und beim Ableiten von Fristen.
    header = []
    topic = str(body.get("title") or body.get("topic") or "").strip()
    if topic:
        header.append(f"Thema der Besprechung: {topic}")
    date = str(body.get("date") or "").strip()
    if date:
        header.append(f"Datum: {date}")
    if participants:
        header.append(f"Teilnehmer: {', '.join(participants)}")
    prompt = ("\n".join(header) + "\n\nNotiz:\n" + text[:8000]) if header else text[:8000]
    data, ti, to, _ = await _research_llm_json(model, _TODO_EXTRACT_SYSTEM, prompt)
    raw_items = data.get("items") or []
    items, idx_to_id = [], {}
    for k, ri in enumerate(raw_items):
        if not isinstance(ri, dict):
            continue
        n = ri.get("n", k)
        it = _todo.new_item(ri.get("text", ""), assignees=ri.get("assignees"),
                            due=str(ri.get("due", "")).strip())
        if not it["text"]:
            continue
        try:
            idx_to_id[int(n)] = it["id"]
        except (TypeError, ValueError):
            idx_to_id[k] = it["id"]
        items.append((it, ri))
    # Abhängigkeiten → Kanten (blockiert_von: dep blockiert this)
    edges = []
    id_list = [it["id"] for it, _ in items]
    for it, ri in items:
        for dep in (ri.get("blockiert_von") or []):
            try:
                dep_id = idx_to_id.get(int(dep))
            except (TypeError, ValueError):
                dep_id = None
            if dep_id and dep_id != it["id"]:
                edges.append({"source": dep_id, "target": it["id"], "label": "blockiert"})
    return {"items": [it for it, _ in items], "edges": edges, "tokens": {"in": ti, "out": to}}


@router.post("/api/todo/suggest-links")
async def todo_suggest_links(req: Request):
    body = await req.json()
    items = [it for it in (body.get("items") or []) if isinstance(it, dict) and it.get("id")]
    if len(items) < 2:
        return {"edges": [], "tokens": {"in": 0, "out": 0}}
    model = _pick_model(body.get("model"), _model_for("general"))
    id_by_idx = {i: it["id"] for i, it in enumerate(items)}
    valid = set(id_by_idx.values())
    lst = "\n".join(f"{i}: {str(it.get('text','')).strip()[:200]}" for i, it in enumerate(items))
    data, ti, to, _ = await _research_llm_json(model, _TODO_LINKS_SYSTEM, "Aufgaben:\n" + lst)
    edges, seen = [], set()
    for e in (data.get("edges") or []):
        try:
            s = id_by_idx.get(int((e or {}).get("source")))
            t = id_by_idx.get(int((e or {}).get("target")))
        except (TypeError, ValueError):
            continue
        if s and t and s != t and (s, t) not in seen and s in valid and t in valid:
            seen.add((s, t))
            edges.append({"source": s, "target": t, "label": str((e or {}).get("label", "")).strip()[:60]})
    return {"edges": edges, "tokens": {"in": ti, "out": to}}


@router.post("/api/todo/next")
async def todo_next(req: Request):
    body = await req.json()
    pid = body.get("pid") or body.get("name")
    data = (await _db.todo_project_get(pid)) if pid else (body.get("data") or {})
    items = (data or {}).get("items") or []
    if not items:
        return {"text": "Die Liste ist leer.", "tokens": {"in": 0, "out": 0}}
    id2text = {it.get("id"): it.get("text", "") for it in items}
    lines = []
    for it in items:
        deps = "; ".join(f"←{id2text.get(l.get('target'), '')}" for l in (it.get("links") or []))
        lines.append(f"[{it.get('status','offen')}] {it.get('text','')}"
                     + (f" (Zuständig: {', '.join(it.get('assignees') or [])})" if it.get("assignees") else "")
                     + (f" {{{deps}}}" if deps else ""))
    model = _pick_model(body.get("model"), _model_for("general"))
    prompt = f"Liste: {data.get('title','')}\n\nAufgaben:\n" + "\n".join(lines)
    text, ti, to = "", 0, 0
    try:
        async with _model_session(model), httpx.AsyncClient(timeout=120) as client:
            resp = await _llm.chat(client, {
                "model": model, "think": False, "stream": False,
                "messages": [{"role": "system", "content": _TODO_NEXT_SYSTEM},
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


@router.post("/api/todo/ask")
async def todo_ask(req: Request):
    """„Über die Daten fragen": beantwortet eine freie Frage über den gesamten (oder den
    aktiven) To-Do-Datenbestand — inkl. Personen-/Kollegen-Auswertungen. LOKAL-BEVORZUGT
    (`_analysis_model` respektiert Geheim-Modus / vertrauliche Auswertungen). Bei großen
    Datenmengen Map-Reduce."""
    body = await req.json()
    question = str(body.get("question", "")).strip()
    if not question:
        raise HTTPException(status_code=400, detail="Keine Frage angegeben")
    root = str(body.get("root", "") or "").strip()
    if root and root != "root":
        ids = await _db.todo_descendants(root)
    else:
        ids = [p["id"] for p in await _db.todo_projects_all()]
    model = await _analysis_model(body.get("model"))
    if not model:
        raise HTTPException(status_code=503, detail="Kein lokales LLM verfügbar – die Auswertung läuft standardmäßig lokal (Profil-Schalter „API-Modelle für vertrauliche Auswertungen“ erlaubt API-Modelle).")
    data = await _db.todo_graph_data(ids)
    # Anhang-Text pro Punkt (md_text) für tiefere Auswertung nachladen.
    att_by_item: dict = {}
    try:
        for a in (await _db.todo_export()).get("attachments", []):
            txt = (a.get("md_text") or "").strip()
            if txt:
                att_by_item.setdefault(a.get("item_id"), []).append(txt)
    except Exception:
        att_by_item = {}
    # Pro Projekt einen Textblock: Aufgaben mit Status/Zuständigen/Frist/Notiz/Anhang + Kanten.
    id2text = {}
    for pr in data.get("projects", []):
        for it in pr["items"]:
            id2text[it["id"]] = it.get("text", "")
    blocks, n_items, persons = [], 0, set()
    for pr in data.get("projects", []):
        if not pr["items"] and not pr["edges"]:
            continue
        lines = [f"### Projekt: {pr['title']}"]
        for it in pr["items"]:
            n_items += 1
            asg = it.get("assignees") or []
            for a in asg:
                persons.add(a)
            parts = [f"- [{it.get('status', 'offen')}] {it.get('text', '')}"]
            if asg:
                parts.append(f"(Zuständig: {', '.join(asg)})")
            if (it.get("due") or "").strip():
                parts.append(f"(Fällig: {it['due']})")
            line = " ".join(parts)
            if (it.get("detail") or "").strip():
                line += f"\n    Notiz: {it['detail'].strip()[:600]}"
            for txt in att_by_item.get(it["id"], []):
                line += f"\n    Anhang: {txt[:600]}"
            lines.append(line)
        for e in pr["edges"]:
            s = id2text.get(e["source"], ""); t = id2text.get(e["target"], "")
            if s and t:
                lines.append(f"- Abhängigkeit: „{s}“ {e.get('label', 'blockiert') or 'blockiert'} → „{t}“")
        blocks.append("\n".join(lines))
    if not blocks:
        return {"answer": "Es sind keine Aufgaben im gewählten Bereich vorhanden.",
                "tokens": {"in": 0, "out": 0}, "scope": root or "root"}
    scope_hint = (f"{n_items} Aufgaben, {len(persons)} Personen"
                  + (f", Zuständige: {', '.join(sorted(persons))}" if persons else ""))
    num_ctx = _profile_num_ctx()
    budget = max(2000, int(num_ctx * 3.2))
    # Auf lokalen Modellen ist jede Generation langsam → Arbeitsmenge deckeln: höchstens
    # MAX_GROUPS Map-Läufe. Passt der Bestand nicht in MAX_GROUPS*budget Zeichen, werden
    # die überzähligen Bereiche weggelassen (mit Hinweis) statt dutzende langsame Aufrufe.
    MAX_GROUPS = 6
    truncated = False
    joined_all = "\n\n".join(blocks)
    cap = MAX_GROUPS * budget
    if len(joined_all) > cap:
        kept, acc = [], 0
        for blk in blocks:
            if acc + len(blk) > cap:
                break
            kept.append(blk); acc += len(blk)
        blocks = kept or [joined_all[:budget]]
        truncated = True
    tin = tout = 0
    try:
        async with _model_session(model), httpx.AsyncClient(timeout=300) as client:
            async def _run(system: str, user: str):
                r = await _llm.chat(client, {
                    "model": model, "think": False, "stream": False,
                    "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
                    "options": {"num_ctx": num_ctx}, "keep_alive": KEEP_ALIVE,
                })
                r.raise_for_status()
                jj = r.json()
                a, b = _llm_tok(jj)
                c = re.sub(r"<think>.*?</think>", "", jj.get("message", {}).get("content", ""), flags=re.DOTALL).strip()
                return c, a, b
            joined = "\n\n".join(blocks)
            if len(joined) <= budget:
                usr = (f"To-Do-Daten (Überblick: {scope_hint}):\n\n{joined}\n\nFrage: {question}")
                answer, a, b = await _run(_TODO_ASK_SYSTEM, usr)
                tin += a; tout += b
            else:
                # Map: Blöcke bis Budget bündeln, je Gruppe eine Teilantwort zur Frage.
                groups, cur, cur_len = [], [], 0
                for blk in blocks:
                    if cur and cur_len + len(blk) > budget:
                        groups.append("\n\n".join(cur)); cur, cur_len = [], 0
                    cur.append(blk); cur_len += len(blk)
                if cur:
                    groups.append("\n\n".join(cur))
                partials = []
                map_sys = (_TODO_ASK_SYSTEM + " Dies ist NUR EIN TEIL der Daten – sammle die für "
                           "die Frage relevanten Fakten aus diesem Teil (noch keine Endantwort).")
                for g in groups:
                    txt, a, b = await _run(map_sys, f"To-Do-Daten (Teil):\n\n{g}\n\nFrage: {question}")
                    tin += a; tout += b
                    if txt:
                        partials.append(txt)
                # Reduce: Teil-Befunde zur Endantwort zusammenführen.
                reduce_usr = (f"Frage: {question}\n\nTeil-Befunde aus dem gesamten To-Do-Bestand "
                              f"({scope_hint}):\n\n" + "\n\n---\n\n".join(partials)
                              + "\n\nFasse dies zu EINER fundierten Endantwort auf die Frage zusammen.")
                answer, a, b = await _run(_TODO_ASK_SYSTEM, reduce_usr)
                tin += a; tout += b
    except HTTPException:
        raise
    except (httpx.ConnectError, httpx.ConnectTimeout) as e:
        raise HTTPException(status_code=503, detail=f"Lokales LLM nicht erreichbar (läuft Ollama?): {e}") from e
    except httpx.HTTPStatusError as e:
        body = ""
        try: body = e.response.text[:300]
        except Exception: pass
        raise HTTPException(status_code=502, detail=f"Das Modell „{model}“ hat die Auswertung abgelehnt (evtl. num_ctx zu groß / zu wenig VRAM / Modell nicht geladen). {body}") from e
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Auswertung fehlgeschlagen: {type(e).__name__}: {e}") from e
    if truncated:
        answer += ("\n\n---\n*Hinweis: Der Bestand ist sehr groß – es wurde nur ein Teil "
                   "ausgewertet. Für vollständige Antworten ein einzelnes Projekt aktivieren (⚡) "
                   "oder gezielter fragen.*")
    return {"answer": answer, "tokens": {"in": tin, "out": tout}, "scope": root or "root"}


# ── To-Do: Anlagen (Dokument -> Markdown in DB), Verschieben, Suche, Graph ────

def _todo_safe_file(fn: str) -> str:
    base = Path(str(fn or "").replace("\\", "/")).name.strip()
    base = "".join(c for c in base if c.isalnum() or c in (" ", ".", "_", "-")).strip()
    return base or "datei"


@router.post("/api/todo/items/{item_id}/attach")
async def todo_item_attach(item_id: str, file: UploadFile = File(...)):
    """Datei an einen Punkt haengen: Original auf Platte, Text als Markdown in die DB
    (fuer Anzeige + Suche)."""
    from tools import files as _files
    d = TODO_ATT_DIR / _todo_safe_file(item_id)
    d.mkdir(parents=True, exist_ok=True)
    orig_path = d / _todo_safe_file(file.filename)
    orig_path.write_bytes(await file.read())
    try:
        text = _files.extract(orig_path)
    except Exception as e:
        text = f"[Konnte Datei nicht lesen: {e}]"
    md_text = f"# {file.filename}\n\n{text}"
    att_id = "ta_" + uuid.uuid4().hex[:12]
    try:
        await _db.todo_attach_add(att_id, item_id, file.filename, str(orig_path), md_text)
    except Exception:
        raise HTTPException(status_code=400, detail="Punkt nicht gefunden - bitte erst speichern.")
    return {"ok": True, "attachment": {"id": att_id, "name": file.filename}}


@router.get("/api/todo/attachment/{att_id}")
async def todo_attachment(att_id: str, orig: bool = False):
    att = await _db.todo_attach_get(att_id)
    if not att:
        raise HTTPException(status_code=404, detail="Anlage nicht gefunden")
    if orig and att.get("orig_path") and Path(att["orig_path"]).exists():
        return FileResponse(att["orig_path"], filename=att.get("name") or "anlage")
    from fastapi.responses import PlainTextResponse
    return PlainTextResponse(att.get("md_text", ""), media_type="text/markdown; charset=utf-8")


@router.delete("/api/todo/attachment/{att_id}")
async def todo_attachment_delete(att_id: str):
    att = await _db.todo_attach_delete(att_id)
    if att and att.get("orig_path"):
        try:
            Path(att["orig_path"]).unlink(missing_ok=True)
        except Exception:
            pass
    return {"ok": True}


@router.post("/api/todo/items/{item_id}/move")
async def todo_item_move(item_id: str, req: Request):
    body = await req.json()
    target = str(body.get("project_id", "")).strip()
    if not target:
        raise HTTPException(status_code=400, detail="Kein Zielprojekt")
    await _db.todo_item_move(item_id, target)
    return {"ok": True}


@router.post("/api/todo/items/{item_id}/reorder")
async def todo_item_reorder(item_id: str, req: Request):
    body = await req.json()
    direction = "up" if str(body.get("direction", "up")) == "up" else "down"
    await _db.todo_item_reorder(item_id, direction)
    return {"ok": True}


@router.get("/api/todo/search")
async def todo_search(q: str = Query(...), root: str = Query("")):
    """Projektuebergreifende Suche (Punkte + Anlagen-Markdown). Mit ?root=<id> auf den
    Teilbaum dieses Projekts beschraenkt; ohne (bzw. root) ueber ALLE Projekte."""
    scope = None
    if root and root != "root":
        scope = await _db.todo_descendants(root)
    results = await _db.todo_search(q, scope)
    return {"results": results, "query": q}


@router.get("/api/todo/graph")
async def todo_graph(root: str = Query("")):
    """Graph-Daten (Punkte+Kanten je Projekt) des Teilbaums <root> bzw. aller Projekte."""
    if root and root != "root":
        ids = await _db.todo_descendants(root)
    else:
        ids = [p["id"] for p in await _db.todo_projects_all()]
    return await _db.todo_graph_data(ids)


def _todo_parse_due(s: str):
    """Fälligkeit tolerant parsen: ISO (YYYY-MM-DD) oder DD.MM.(YY)YY → date, sonst None."""
    import datetime as _d
    s = (s or "").strip()
    if not s:
        return None
    for fmt in ("%Y-%m-%d", "%d.%m.%Y", "%d.%m.%y", "%Y/%m/%d"):
        try:
            return _d.datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


@router.get("/api/todo/agenda")
async def todo_agenda(root: str = Query(""), person: str = Query("")):
    """Deterministische Empfehlung „was als Nächstes?" für den aktiven Bereich.
    Priorisiert nach Fälligkeit (Zeit), Abhängigkeiten (blockiert/entblockt) und
    Status. Optional auf eine Person gefiltert. Kein LLM — nachvollziehbar."""
    import datetime as _d
    if root and root != "root":
        ids = await _db.todo_descendants(root)
    else:
        ids = [p["id"] for p in await _db.todo_projects_all()]
    data = await _db.todo_graph_data(ids)
    items, preds, succs, persons = {}, {}, {}, set()
    for pr in data.get("projects", []):
        for it in pr["items"]:
            items[it["id"]] = {"id": it["id"], "text": it["text"], "status": it["status"],
                               "assignees": it.get("assignees") or [], "due": it.get("due", ""),
                               "project": pr["name"], "project_title": pr["title"]}
            for a in (it.get("assignees") or []):
                persons.add(a)
        for e in pr["edges"]:
            preds.setdefault(e["target"], set()).add(e["source"])   # source = Vorgänger von target
            succs.setdefault(e["source"], set()).add(e["target"])
    today = _d.date.today()

    def _urgency(due):
        dd = _todo_parse_due(due)
        if not dd:
            return (0.0, None)
        delta = (dd - today).days
        if delta < 0:
            return (3.0, delta)
        if delta <= 3:
            return (2.0, delta)
        if delta <= 10:
            return (1.0, delta)
        return (0.5, delta)

    pfilter = (person or "").strip().lower()
    ready, blocked = [], []
    for iid, it in items.items():
        if it["status"] == "erledigt":
            continue
        if pfilter and not any(pfilter in a.lower() for a in it["assignees"]):
            continue
        blockers = [items[s]["text"] for s in preds.get(iid, set())
                    if s in items and items[s]["status"] != "erledigt"]
        unblocks = len([t for t in succs.get(iid, set())
                        if t in items and items[t]["status"] != "erledigt"])
        u, days = _urgency(it["due"])
        score = u * 10 + unblocks * 3 + (2 if it["status"] == "laeuft" else 0)
        row = {**it, "unblocks": unblocks, "urgency": u, "days": days,
               "blockers": blockers, "score": round(score, 2)}
        (blocked if blockers else ready).append(row)
    ready.sort(key=lambda r: (-r["score"], r["days"] if r["days"] is not None else 9999))
    blocked.sort(key=lambda r: -r["score"])
    jetzt = [r for r in ready if r["urgency"] >= 2 or r["unblocks"] >= 1 or r["status"] == "laeuft"]
    demn = [r for r in ready if r not in jetzt]
    return {"persons": sorted(persons), "jetzt": jetzt, "demnaechst": demn,
            "blocked": blocked, "scope_root": root or "root"}


@router.get("/api/todo/export")
async def todo_export():
    """Kompletten To-Do-Bestand (Projekte, Punkte, Kanten, Anlagen) als JSON —
    zum Sichern/Weitergeben. Frontend lädt es als Datei herunter."""
    return await _db.todo_export()


@router.post("/api/todo/import")
async def todo_import(req: Request):
    """To-Do-Bestand aus einer zuvor exportierten JSON-Datei einspielen. Vorhandene
    Projekte mit gleicher ID werden ersetzt (Punkte/Kanten sauber, kein Duplikat).
    Die Wurzel wird nicht überschrieben."""
    dump = await req.json()
    if not isinstance(dump, dict) or "projects" not in dump:
        raise HTTPException(status_code=400, detail="Ungültige Datei – erwartet wird ein To-Do-Export (Felder projects/items/edges/attachments).")
    await _db.todo_root_ensure(_todo_root_name())
    # Wurzel-Projektzeile aus dem Import entfernen (Name/Struktur der eigenen Wurzel bleibt
    # erhalten); Punkte, die direkt an der Wurzel hängen, werden weiterhin übernommen.
    dump = {
        "projects": [p for p in dump.get("projects", []) if p.get("id") != "root"],
        "items": dump.get("items", []),
        "edges": dump.get("edges", []),
        "attachments": dump.get("attachments", []),
    }
    # Vorhandene Projekte gleicher ID zuerst kaskadierend löschen → sauberer Re-Import
    # (Kanten haben keine stabile ID, sonst würden sie sich verdoppeln).
    for p in dump["projects"]:
        pid = p.get("id", "")
        if pid and pid != "root":
            await _db.todo_project_delete(pid)
    await _db.todo_import(dump)
    return {"projects": len(dump["projects"]), "items": len(dump["items"]),
            "edges": len(dump["edges"]), "attachments": len(dump["attachments"])}


@router.post("/api/todo/reset")
async def todo_reset():
    """Kompletten To-Do-Bestand leeren — mit AUTOMATISCHER Sicherung der alten Liste
    (Zeitstempel-JSON unter data/todo_backups/), damit nichts unwiederbringlich verloren
    geht. Danach ist nur noch die (leere) Wurzel übrig."""
    from datetime import datetime
    dump = await _db.todo_export()
    backup_dir = DATA_DIR / "todo_backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    fname = "todo_backup_" + datetime.now().strftime("%Y%m%d_%H%M%S") + ".json"
    (backup_dir / fname).write_text(json.dumps(dump, ensure_ascii=False, indent=2), encoding="utf-8")
    await _db.todo_wipe()
    await _db.todo_root_ensure(_todo_root_name())
    return {"backup": fname,
            "removed": {"projects": len(dump.get("projects", [])), "items": len(dump.get("items", []))}}
