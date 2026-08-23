"""Router: Agenten-API + Bildanalyse (/api/agents, /api/analyze-image)

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


# ── Agenten-API ───────────────────────────────────────────────────────────────


@router.get("/api/agents")
async def list_agents(project_id: Optional[str] = None):
    """Listet Agenten. Ohne ``project_id`` werden ALLE zurückgegeben (Kompatibilität);
    mit ``project_id`` nur die diesem Projekt fest zugeordneten Skill-Agenten."""
    agents = []
    for f in AGENTS_DIR.glob("*.json"):
        try:
            agents.append(json.loads(f.read_text(encoding="utf-8")))
        except Exception:
            pass
    if project_id is not None:
        agents = [a for a in agents if (a.get("project_id") or "") == project_id]
    return agents


@router.post("/api/agents/generate-prompt")
async def generate_agent_prompt(req: Request):
    import re
    body = await req.json()
    description = body.get("description", "").strip()
    if not description:
        raise HTTPException(400, "Keine Beschreibung angegeben")

    _gp_model = _pick_model(body.get("model"))

    async with _model_session(_gp_model), httpx.AsyncClient(timeout=120) as client:
        resp = await _llm.chat(client,{
            "model": _gp_model,
            "think": False,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "Du erstellst präzise System-Prompts für KI-Agenten. "
                        "Antworte NUR mit dem fertigen System-Prompt, ohne Einleitung, Erklärung oder Markdown."
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"Erstelle einen System-Prompt für einen KI-Agenten mit folgender Aufgabe:\n\n"
                        f"{description}\n\n"
                        f"Regeln:\n"
                        f"- Beginne mit 'Du bist ein...'\n"
                        f"- Beschreibe klar die Kernaufgabe und das Verhalten\n"
                        f"- Weise an, auf Deutsch zu antworten\n"
                        f"- Maximal 120 Wörter\n"
                        f"- Kein Markdown, nur Fließtext"
                    ),
                },
            ],
            "stream": False,
        })
        resp.raise_for_status()
        result = resp.json()
        _gp_ti, _gp_to = _llm_tok(result)
        generated = result.get("message", {}).get("content", "").strip()

    generated = re.sub(r"<think>.*?</think>", "", generated, flags=re.DOTALL).strip()
    return {"prompt": generated, "tokens": {"in": _gp_ti, "out": _gp_to}}


@router.post("/api/derive-persona")
async def derive_persona(req: Request):
    """Leitet aus der Präsentationsbeschreibung eine Analyse-Persona ab.

    Die Persona wird als System-Prompt verwendet, um die Bilder fachlich passend
    zu beschreiben (z.B. ein E-Maschinen-Experte für eine E-Maschinen-Präsentation).
    """
    import re
    body = await req.json()
    description = (body.get("description") or "").strip()
    if not description:
        raise HTTPException(400, "Keine Beschreibung angegeben")
    _model = _pick_model(body.get("model"))

    async with _model_session(_model), httpx.AsyncClient(timeout=120) as client:
        resp = await _llm.chat(client,{
            "model": _model,
            "think": False,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "Du bestimmst aus der Beschreibung einer Präsentation einen passenden "
                        "Fach-Experten, der die Bilder der Präsentation beschreiben soll. "
                        "Antworte NUR mit JSON in genau diesem Format, ohne weiteren Text: "
                        '{"persona_name":"Kurzname des Experten","system_prompt":"Du bist ein ... '
                        'der Bilder fachkundig auf Deutsch beschreibt."}'
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"Beschreibung der Präsentation:\n{description}\n\n"
                        "Der system_prompt muss anweisen, das gezeigte Bild knapp, fachlich korrekt "
                        "und auf Deutsch zu beschreiben (max. 3 Stichpunkte plus eine kurze Bildunterschrift)."
                    ),
                },
            ],
            "stream": False,
        })
        resp.raise_for_status()
        _dp_j = resp.json()
        _dp_ti, _dp_to = _llm_tok(_dp_j)
        raw = _dp_j.get("message", {}).get("content", "")

    raw = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL).strip()
    # Markdown-Codezaun entfernen (```json … ```)
    raw = re.sub(r"^```[a-zA-Z]*\s*", "", raw).strip()
    raw = re.sub(r"\s*```$", "", raw).strip()

    persona_name, system_prompt = "Fach-Experte", ""
    m = re.search(r"\{[\s\S]*\}", raw)
    if m:
        try:
            data = json.loads(m.group(0))
            persona_name = (data.get("persona_name") or persona_name).strip()
            system_prompt = (data.get("system_prompt") or "").strip()
        except Exception:
            pass
    if not system_prompt:
        # JSON kaputt (z.B. echte Zeilenumbrüche im String) → Felder per Regex ziehen
        mn = re.search(r'"persona_name"\s*:\s*"([^"]+)"', raw)
        if mn:
            persona_name = mn.group(1).strip()
        ms = re.search(r'"system_prompt"\s*:\s*"([\s\S]+?)"\s*[},]', raw)
        if ms:
            system_prompt = ms.group(1).strip()
    if not system_prompt:
        # Letzter Fallback: roher Text ohne JSON-Gerüst
        system_prompt = re.sub(r'^[\s{]*"?[\w]*"?\s*:?\s*', "", raw).strip() or (
            "Du bist ein technischer Fach-Experte und beschreibst das gezeigte Bild "
            "knapp und sachlich auf Deutsch (max. 3 Stichpunkte plus eine kurze Bildunterschrift)."
        )
    return {"persona_name": persona_name, "system_prompt": system_prompt,
            "tokens": {"in": _dp_ti, "out": _dp_to}}


@router.post("/api/analyze-image")
async def analyze_image(req: Request):
    """Analysiert ein einzelnes Bild mit einem Vision-Modell und liefert
    strukturierten Folieninhalt (Titel, Stichpunkte, Bildunterschrift). Dünner Wrapper
    um den geteilten Kern ``_analyze_image_core`` (core.py)."""
    body = await req.json()
    image_b64 = body.get("image") or ""
    if not image_b64:
        raise HTTPException(400, "Kein Bild übergeben")
    return await _analyze_image_core(
        image_b64,
        system_prompt=body.get("system_prompt") or "",
        filename=body.get("filename") or "",
        topic=body.get("topic") or "",
        model=body.get("model"),
        want_notes=bool(body.get("want_notes")),
    )


@router.post("/api/illus/intro")
async def illus_intro(req: Request):
    """Schreibt die Beschreibung der bebilderten Präsentation als Einleitungsfolie
    NEU — aus Sicht des gewählten/abgeleiteten Experten (Persona). Liefert kurze
    Stichpunkte für die Folie ``Über diese Präsentation``."""
    import re
    body = await req.json()
    description = (body.get("description") or "").strip()
    title = (body.get("title") or "").strip()
    persona = (body.get("system_prompt") or "").strip() or (
        "Du bist ein fachkundiger Experte und formulierst auf Deutsch knapp und sachlich."
    )
    if not description:
        return {"bullets": []}
    _model = _pick_model(body.get("model"))

    sysmsg = (
        persona
        + "\n\nDu formulierst die Einleitungsfolie einer Präsentation. Schreibe die "
        "vorgegebene Beschreibung in eigenen Worten zu einer knappen, professionellen "
        "Einleitung um (nicht wörtlich kopieren). Antworte NUR mit JSON in genau diesem "
        'Format, ohne weiteren Text:\n{"bullets":["Stichpunkt 1","Stichpunkt 2","Stichpunkt 3"]}\n'
        "Maximal 5 kurze Stichpunkte (je höchstens ein knapper Satz). "
        "Kein Markdown, keine Sternchen, keine Aufzählungszeichen im Text."
    )
    usermsg = (f"Titel der Präsentation: {title}\n" if title else "") + \
        f"Beschreibung:\n{description}"

    async with _model_session(_model), httpx.AsyncClient(timeout=120) as client:
        resp = await _llm.chat(client, {
            "model": _model,
            "think": False,
            "format": "json",   # erzwingt valides, vollständiges JSON (kein Vorgeplapper)
            "messages": [
                {"role": "system", "content": sysmsg},
                {"role": "user", "content": usermsg},
            ],
            "stream": False,
            "options": {"num_ctx": _profile_num_ctx()},
        })
        resp.raise_for_status()
        _ii_j = resp.json()
        _ii_ti, _ii_to = _llm_tok(_ii_j)
        raw = _ii_j.get("message", {}).get("content", "")

    def _strip_md(s: str) -> str:
        s = re.sub(r"[*_`#>]+", "", s)
        s = re.sub(r"^\s*[-•]\s*", "", s)
        return re.sub(r"\s+", " ", s).strip()

    raw = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL).strip()
    bullets: list[str] = []
    data = _parse_llm_json(raw)
    if isinstance(data, dict):
        b = data.get("bullets") or []
        bullets = [_strip_md(str(x)) for x in b if str(x).strip()][:5]
    if not bullets:
        # Bergung aus (evtl. abgeschnittenem) JSON
        _, sb, _c = _slide_fields_from_partial(raw)
        bullets = [_strip_md(str(x)) for x in sb if str(x).strip()][:5]
    if not bullets:
        # Letzter Fallback: die ORIGINAL-Beschreibung in Sätze zerlegen (nie JSON-Text).
        plain = _strip_md(description)
        bullets = [s.strip() for s in re.split(r"(?<=[.!?])\s+", plain) if s.strip()][:5]
    return {"bullets": bullets, "tokens": {"in": _ii_ti, "out": _ii_to}}


@router.post("/api/agents")
async def create_agent(agent: AgentDef):
    if not agent.id:
        agent.id = _to_slug(agent.name or "agent") + "_" + uuid.uuid4().hex[:4]
    fp = _unique_agent_path(agent.name or agent.id, exclude_id=agent.id)
    fp.write_text(json.dumps(agent.model_dump(), ensure_ascii=False, indent=2), encoding="utf-8")
    return agent


@router.put("/api/agents/{aid}")
async def update_agent(aid: str, agent: AgentDef):
    agent.id = aid
    # Alte Datei finden und ggf. umbenennen
    old_fp = _agent_path_by_id(aid)
    new_fp = _unique_agent_path(agent.name or aid, exclude_id=aid)
    if old_fp and old_fp != new_fp and old_fp.exists():
        old_fp.unlink(missing_ok=True)
    new_fp.write_text(json.dumps(agent.model_dump(), ensure_ascii=False, indent=2), encoding="utf-8")
    return agent


@router.delete("/api/agents/{aid}")
async def delete_agent(aid: str):
    fp = _agent_path_by_id(aid)
    if fp:
        fp.unlink(missing_ok=True)
    return {"ok": True}


class AgentMergeReq(BaseModel):
    ids: List[str]
    model: Optional[str] = None
    name: Optional[str] = None


@router.post("/api/agents/merge")
async def merge_agents(req: AgentMergeReq):
    """Verschmilzt mehrere vorhandene Agenten zu EINEM neuen Agenten: System-Prompts
    werden per LLM zu einer widerspruchsfreien Experten-Persona zusammengeführt,
    Tools und gebundene Wissensdatenbanken als Vereinigung übernommen. Der neue Agent
    wird gespeichert und zurückgegeben; die Quell-Agenten bleiben erhalten."""
    sources: list[dict] = []
    for aid in req.ids:
        d = _load_agent_dict(aid)
        if d:
            sources.append(d)
    if len(sources) < 2:
        raise HTTPException(400, "Mindestens zwei Agenten zum Verschmelzen wählen")

    # Vereinigung von Tools, Wissensdatenbanken, Beispielcode (Reihenfolge erhalten)
    tools: list[str] = []
    rag: list[str] = []
    example_blocks: list[str] = []
    for s in sources:
        for t in (s.get("tools") or []):
            if t not in tools:
                tools.append(t)
        for c in (s.get("rag_collections") or []):
            if c not in rag:
                rag.append(c)
        ex = (s.get("example_code") or "").strip()
        if ex:
            example_blocks.append(f"# {s.get('name', '')}\n{ex}")

    names = [s.get("name", "") for s in sources if s.get("name")]
    prompts_block = "\n\n".join(
        f"### Agent: {s.get('name', '')}\n{(s.get('system_prompt') or '').strip()}"
        for s in sources
    )

    model = _pick_model(req.model)
    merged_name = (req.name or "").strip()
    merged_desc = ""
    merged_prompt = ""
    _mg_ti, _mg_to = 0, 0
    try:
        async with _model_session(model), httpx.AsyncClient(timeout=180) as client:
            resp = await _llm.chat(client, {
                "model": model,
                "think": False,
                "messages": [
                    {
                        "role": "system",
                        "content": (
                            "Du verschmilzt mehrere KI-Agenten zu EINEM kohärenten Experten, "
                            "der die Fachgebiete und Fähigkeiten aller vereint. Antworte NUR mit "
                            'JSON in genau diesem Format, ohne weiteren Text: '
                            '{"name":"Kurzname","description":"ein Satz","system_prompt":"Du bist ..."}. '
                            "Der system_prompt beginnt mit 'Du bist', vereint alle Rollen "
                            "widerspruchsfrei, nennt die Kernaufgaben und weist an, auf Deutsch zu antworten."
                        ),
                    },
                    {
                        "role": "user",
                        "content": f"Verschmilz diese Agenten zu einem einzigen Experten:\n\n{prompts_block}",
                    },
                ],
                "stream": False,
            })
            resp.raise_for_status()
            _mg_j = resp.json()
            _mg_ti, _mg_to = _llm_tok(_mg_j)
            data = _parse_llm_json(_mg_j.get("message", {}).get("content", "")) or {}
        merged_prompt = (data.get("system_prompt") or "").strip()
        merged_name = merged_name or (data.get("name") or "").strip()
        merged_desc = (data.get("description") or "").strip()
    except Exception:
        pass

    # Deterministische Fallbacks, falls das LLM nichts Brauchbares liefert
    if not merged_name:
        merged_name = (" + ".join(names))[:80] or "Verschmolzener Agent"
    if not merged_prompt:
        merged_prompt = "\n\n".join(
            f"# {s.get('name', '')}\n{(s.get('system_prompt') or '').strip()}" for s in sources
        )
    if not merged_desc:
        merged_desc = "Verschmolzen aus: " + ", ".join(names)

    agent = AgentDef(
        name=merged_name,
        description=merged_desc,
        system_prompt=merged_prompt,
        tools=tools or ["web_search", "calculate"],
        rag_collections=rag,
        example_code="\n\n".join(example_blocks),
        icon=sources[0].get("icon", "🤖"),
        category=sources[0].get("category", "Sonstige"),
    )
    agent.id = _to_slug(agent.name or "agent") + "_" + uuid.uuid4().hex[:4]
    fp = _unique_agent_path(agent.name or agent.id, exclude_id=agent.id)
    fp.write_text(json.dumps(agent.model_dump(), ensure_ascii=False, indent=2), encoding="utf-8")
    out = agent.model_dump()
    out["tokens"] = {"in": _mg_ti, "out": _mg_to}
    return out


# Schwelle (Zeichen): bis hierher Text direkt in den system_prompt, darüber RAG-Basis.
_LEGAL_PROMPT_LIMIT = 8000

# Zeilenanfänge wie „§ 433", „§§ 305 ff.", „Artikel 5", „Art. 12a" → Markdown-Überschrift.
_LEGAL_HEAD_RE = re.compile(
    r"^\s*(§{1,2}\s*\d+\s*[a-z]?|Art(?:ikel|\.)\s*\d+\s*[a-z]?)\b.*$", re.IGNORECASE)


def _legal_to_md(text: str, title: str = "") -> str:
    """Wandelt einen extrahierten Gesetzes-/Normtext deterministisch nach Markdown:
    Paragraphen/Artikel werden zu Überschriften, überflüssige Leerzeilen entfernt.
    Bewusst ohne LLM (schnell, robust, keine VRAM-Last)."""
    lines = (text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")
    out = [f"# {title}".rstrip()] if title else []
    for ln in lines:
        s = ln.strip()
        if not s:
            out.append("")
        elif _LEGAL_HEAD_RE.match(s):
            out.append("")
            out.append(f"### {s}")
        else:
            out.append(s)
    md = "\n".join(out)
    return re.sub(r"\n{3,}", "\n\n", md).strip()


@router.post("/api/agents/from-legal")
async def create_legal_agent(
    file: UploadFile = File(...),
    title: str = Form(""),
    web_search: bool = Form(False),
    domain: str = Form(""),
):
    """Erzeugt aus einem hochgeladenen Fachdokument einen spezialisierten Dokument-Experten.
    Über `domain` (Fachgebiet/Rolle, z. B. „Recht", „Physik", „Medizin") wird die Persona
    und der Zitierstil angepasst — leer ⇒ juristischer Modus (rückwärtskompatibel). Der
    Text wird nach Markdown konvertiert; bei kurzem Text direkt in den system_prompt
    eingebettet, bei langem Text in eine eigene Wissensdatenbank ausgelagert und fest an
    den Agenten gebunden (rag_collections) — die Entscheidung fällt automatisch nach Länge."""
    from tools.rag import ingest_file
    tmp = UPLOADS_DIR / f"legal_{uuid.uuid4().hex}_{file.filename}"
    async with aiofiles.open(tmp, "wb") as fh:
        await fh.write(await file.read())
    try:
        raw = _extract_text(tmp)
    finally:
        try:
            tmp.unlink()
        except Exception:
            pass
    if not raw or raw.startswith("[Lesefehler"):
        raise HTTPException(status_code=400, detail=f"Text konnte nicht extrahiert werden: {raw}")

    name = (title or "").strip() or Path(file.filename or "Dokument").stem
    md = _legal_to_md(raw, name)
    tools_list = ["web_search"] if web_search else []

    # Fachgebiet/Rolle bestimmt Persona, Zitierstil, Kategorie & Icon.
    domain = (domain or "").strip()[:40]
    _is_legal = (not domain) or domain.lower() in (
        "recht", "gesetz", "gesetze", "jura", "legal", "norm", "juristisch")
    if _is_legal:
        persona = f"ein juristischer Fachassistent für „{name}“"
        cite = "die einschlägige Fundstelle (§ bzw. Artikel)"
        coll_prefix, category, icon = "Gesetz", "Recht", "⚖️"
    else:
        persona = f"ein Fachassistent für {domain} zum Thema „{name}“"
        cite = "die Fundstelle (z. B. Abschnitt, Kapitel, Gleichung oder Seite)"
        coll_prefix, category, icon = domain, domain, "📚"

    if len(md) <= _LEGAL_PROMPT_LIMIT:
        mode, rag_ids = "prompt", []
        system_prompt = (
            f"Du bist {persona}. Beantworte Fragen AUSSCHLIESSLICH auf Basis des folgenden "
            f"Dokuments und nenne immer {cite}. Steht die Antwort nicht im Text, "
            f"sage das klar und rate nicht. Antworte präzise und auf Deutsch.\n\n"
            f"--- {name} ---\n\n{md}"
        )
    else:
        mode = "rag"
        coll = {
            "id": f"rag_{uuid.uuid4().hex[:12]}",
            "name": f"{coll_prefix}: {name}",
            "embed_model": EMBED_MODEL,
            "tier": "korrekt",
            "chunk_size": 1200, "chunk_overlap": 200, "top_k": 6,
            "embed_gpu": False, "clean": True, "char_limit": 6000,
            "strictness": "korrekt", "created_at": time.time(),
        }
        await _db.rag_create_collection(coll)
        try:
            await ingest_file(coll, md, f"{name}.md", f"doc_{uuid.uuid4().hex[:12]}")
        except Exception as e:
            await _db.rag_delete_collection(coll["id"])
            raise HTTPException(
                status_code=500,
                detail=f"Einbetten fehlgeschlagen — ist das Embedding-Modell '{EMBED_MODEL}' gepullt? ({e})")
        rag_ids = [coll["id"]]
        system_prompt = (
            f"Du bist {persona}. Dir ist das vollständige Dokument als Wissensdatenbank "
            f"hinterlegt. Beantworte Fragen AUSSCHLIESSLICH anhand der eingeblendeten Auszüge "
            f"und nenne immer {cite}. Steht die Antwort nicht in den Auszügen, sage das klar "
            f"und rate nicht. Antworte präzise und auf Deutsch."
        )

    agent = AgentDef(
        id=_to_slug(name) + "_" + uuid.uuid4().hex[:4],
        name=name,
        description=f"Dokument-Experte ({category}) zu „{name}“ (automatisch aus hochgeladenem Text erstellt).",
        system_prompt=system_prompt,
        tools=tools_list,
        icon=icon,
        category=category,
        favorite=True,
        rag_collections=rag_ids,
    )
    fp = _unique_agent_path(agent.name or agent.id, exclude_id=agent.id)
    fp.write_text(json.dumps(agent.model_dump(), ensure_ascii=False, indent=2), encoding="utf-8")
    return {
        "agent_id": agent.id, "name": name, "mode": mode, "chars": len(md),
        "category": category, "coll_prefix": coll_prefix,
        "rag_collection_id": (rag_ids[0] if rag_ids else None),
    }



