"""Router: Rechnungen, Angebote, Firmenprofil, Arbeitszeugnisse

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


# ── Rechnungen & Arbeitszeugnisse ─────────────────────────────────────────────
# Zwei Dokument-Tabs. Rechnungsbeträge werden deterministisch berechnet
# (tools/dokumente.py, Decimal) — nie vom LLM. Das LLM (frei wählbar, ideal ein
# DSGVO-konformes API-Modell) hilft nur beim Strukturieren von Freitext-Rechnungen
# und beim Formulieren der Zeugnistexte. Ausgabe als PDF (to_pdf) und DOCX.

def _load_firmenprofil() -> dict:
    try:
        if FIRMENPROFIL_FILE.exists():
            return json.loads(FIRMENPROFIL_FILE.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}


def _save_firmenprofil(data: dict) -> None:
    FIRMENPROFIL_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                                 encoding="utf-8")


_RECHNUNG_NR_RE = re.compile(r"^[A-Za-z0-9._\-]{1,40}$")


# ── Beleg-Nummernkreise (Rechnung & Angebot teilen dieselbe Zähler-Logik) ─────

def _doc_peek_number(counter_path: Path, pref: str, start: int) -> str:
    """Nächste Belegnummer für einen Zähler, ohne ihn zu erhöhen."""
    last = 0
    try:
        c = json.loads(counter_path.read_text(encoding="utf-8"))
        if c.get("prefix") == pref:
            last = int(c.get("last") or 0)
    except Exception:
        last = 0
    seq = max(last + 1, int(start or 1))
    return f"{pref}{seq:04d}"


def _doc_commit_number(counter_path: Path, pref: str, nr: str) -> None:
    """Zähler auf die Sequenz von ``nr`` hochsetzen (nach dem Speichern)."""
    m = re.search(r"(\d+)\s*$", nr)
    seq = int(m.group(1)) if m else 0
    counter_path.write_text(
        json.dumps({"prefix": pref, "last": seq}, ensure_ascii=False),
        encoding="utf-8")


def _rechnung_counter_path() -> Path:
    return RECHNUNGEN_DIR / "_counter.json"


def _rechnung_prefix() -> str:
    """Präfix für die Rechnungsnummer (Profil ‚rechnung_prefix' oder ‚JAHR-')."""
    pref = str(_load_firmenprofil().get("rechnung_prefix") or "").strip()
    return pref if pref else f"{date.today().year}-"


def _peek_rechnungsnummer() -> str:
    start = int(_load_firmenprofil().get("rechnung_start") or 1)
    return _doc_peek_number(_rechnung_counter_path(), _rechnung_prefix(), start)


def _commit_rechnungsnummer(nr: str) -> None:
    _doc_commit_number(_rechnung_counter_path(), _rechnung_prefix(), nr)


def _rechnung_path(nr: str) -> Path:
    if not _RECHNUNG_NR_RE.match(nr or ""):
        raise HTTPException(status_code=400, detail="Ungültige Rechnungsnummer.")
    return RECHNUNGEN_DIR / f"{nr}.json"


def _load_rechnung(nr: str) -> dict:
    fp = _rechnung_path(nr)
    if not fp.exists():
        raise HTTPException(status_code=404, detail="Rechnung nicht gefunden.")
    return json.loads(fp.read_text(encoding="utf-8"))


def _angebot_counter_path() -> Path:
    return ANGEBOTE_DIR / "_counter.json"


def _angebot_prefix() -> str:
    """Präfix für die Angebotsnummer (Profil ‚angebot_prefix' oder ‚AN-JAHR-')."""
    pref = str(_load_firmenprofil().get("angebot_prefix") or "").strip()
    return pref if pref else f"AN-{date.today().year}-"


def _peek_angebotsnummer() -> str:
    start = int(_load_firmenprofil().get("angebot_start") or 1)
    return _doc_peek_number(_angebot_counter_path(), _angebot_prefix(), start)


def _commit_angebotsnummer(nr: str) -> None:
    _doc_commit_number(_angebot_counter_path(), _angebot_prefix(), nr)


def _angebot_path(nr: str) -> Path:
    if not _RECHNUNG_NR_RE.match(nr or ""):
        raise HTTPException(status_code=400, detail="Ungültige Angebotsnummer.")
    return ANGEBOTE_DIR / f"{nr}.json"


def _load_angebot(nr: str) -> dict:
    fp = _angebot_path(nr)
    if not fp.exists():
        raise HTTPException(status_code=404, detail="Angebot nicht gefunden.")
    return json.loads(fp.read_text(encoding="utf-8"))


@router.get("/api/firmenprofil")
async def get_firmenprofil():
    return _load_firmenprofil()


@router.post("/api/firmenprofil")
async def save_firmenprofil(req: Request):
    data = await req.json()
    if not isinstance(data, dict):
        raise HTTPException(status_code=400, detail="Ungültiges Profil.")
    _save_firmenprofil(data)
    return {"ok": True}


@router.get("/api/rechnung/next-number")
async def rechnung_next_number():
    return {"nummer": _peek_rechnungsnummer()}


_RECHNUNG_PARSE_SYSTEM = (
    "Du extrahierst aus einer freien Rechnungsbeschreibung strukturierte "
    "Rechnungspositionen. Antworte NUR mit JSON dieser Form:\n"
    '{"positionen":[{"menge":<Zahl>,"einheit":"<Std|Tag|Stk|pauschal|…>",'
    '"beschreibung":"<Text>","einzelpreis":<Netto-Einzelpreis als Zahl>}],'
    '"leistungsdatum":"<optional>","einleitung":"<optionaler Einleitungssatz>"}\n'
    "Einzelpreise sind Nettopreise. Rechne nichts aus (keine Summen). Wenn eine "
    "Menge fehlt, nimm 1. Keine Erklärungen, kein Fließtext."
)


@router.post("/api/rechnung/parse")
async def rechnung_parse(req: Request):
    """Freitext („3 Tage Beratung à 800 €, Fahrtkosten 120 €") → Positionen (JSON)."""
    body = await req.json()
    text = str(body.get("text", "")).strip()
    if not text:
        raise HTTPException(status_code=400, detail="Kein Text übergeben.")
    model = _pick_model(body.get("model"), _model_for("general"))
    tok_in = tok_out = 0
    data = None
    try:
        async with _model_session(model), httpx.AsyncClient(timeout=180) as client:
            resp = await _llm.chat(client, {
                "model": model, "stream": False, "format": "json", "think": False,
                "messages": [
                    {"role": "system", "content": _RECHNUNG_PARSE_SYSTEM},
                    {"role": "user", "content": text},
                ],
                "options": {"temperature": 0.1},
            })
            resp.raise_for_status()
            j = resp.json()
            ti, to = _llm_tok(j)
            tok_in += ti; tok_out += to
            data = _parse_llm_json(j.get("message", {}).get("content", ""))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Analyse fehlgeschlagen: {e}")
    if not data or not isinstance(data.get("positionen"), list):
        raise HTTPException(status_code=422, detail="Keine Positionen erkannt.")
    return {"positionen": data.get("positionen", []),
            "leistungsdatum": data.get("leistungsdatum", ""),
            "einleitung": data.get("einleitung", ""),
            "tokens": {"in": tok_in, "out": tok_out}}


_RECHNUNG_BREAKDOWN_SYSTEM = (
    "Du zerlegst einen beschriebenen Vorgang/Auftrag in einzelne Rechnungspositionen "
    "nach Leistungskategorien (z. B. Beschaffung, Planung, Konstruktion, Recherche, "
    "Fremdleistungen, Fertigung/Montage, Inbetriebnahme, Dokumentation, "
    "Projektmanagement). Nutze bevorzugt die vorgegebenen Kategorien und nur die, die "
    "wirklich zum Vorgang passen. Antworte NUR mit JSON:\n"
    '{"positionen":[{"menge":<Zahl>,"einheit":"<Std|Tag|Stk|pauschal>",'
    '"beschreibung":"<Kategorie>: <konkrete Tätigkeit im Vorgang>",'
    '"einzelpreis":<Netto-Einzelpreis als Zahl>}]}\n'
    "Regeln: menge = geschätzter Aufwand (Stunden, außer bei pauschal/Fremdleistungen). "
    "einzelpreis ist netto; bei zeitbasierten Positionen der genannte Stundensatz. "
    "Beschreibung immer mit der Kategorie beginnen. Rechne KEINE Summen. Keine Erklärungen."
)


@router.post("/api/rechnung/breakdown")
async def rechnung_breakdown(req: Request):
    """Zerlegt einen Vorgang in Einzelpositionen nach Leistungskategorien."""
    from tools import dokumente as _dok
    body = await req.json()
    vorgang = str(body.get("vorgang", "")).strip()
    if not vorgang:
        raise HTTPException(status_code=400, detail="Kein Vorgang beschrieben.")
    kategorien = body.get("kategorien") or _dok.RECHNUNG_KATEGORIEN
    kategorien = [str(k).strip() for k in kategorien if str(k).strip()]
    try:
        stundensatz = float(body.get("stundensatz") or 0) or 0.0
    except (TypeError, ValueError):
        stundensatz = 0.0
    model = _pick_model(body.get("model"), _model_for("general"))

    user = (f"Vorgang: {vorgang}\n"
            f"Zu verwendende Kategorien: {', '.join(kategorien)}\n"
            + (f"Stundensatz (netto, €/Std): {stundensatz:g}\n" if stundensatz else ""))
    tok_in = tok_out = 0
    data = None
    try:
        async with _model_session(model), httpx.AsyncClient(timeout=240) as client:
            resp = await _llm.chat(client, {
                "model": model, "stream": False, "format": "json", "think": False,
                "messages": [
                    {"role": "system", "content": _RECHNUNG_BREAKDOWN_SYSTEM},
                    {"role": "user", "content": user},
                ],
                "options": {"temperature": 0.3},
            })
            resp.raise_for_status()
            j = resp.json()
            ti, to = _llm_tok(j)
            tok_in += ti; tok_out += to
            data = _parse_llm_json(j.get("message", {}).get("content", ""))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Zerlegung fehlgeschlagen: {e}")
    if not data or not isinstance(data.get("positionen"), list):
        raise HTTPException(status_code=422, detail="Keine Positionen erzeugt.")
    positionen = data["positionen"]
    # Stundensatz deterministisch auf zeitbasierte Positionen anwenden
    if stundensatz:
        for p in positionen:
            einheit = str(p.get("einheit", "")).strip().lower()
            if einheit.startswith(("std", "stunde", "h")):
                p["einzelpreis"] = stundensatz
    return {"positionen": positionen, "tokens": {"in": tok_in, "out": tok_out}}


@router.post("/api/rechnung/create")
async def rechnung_create(req: Request):
    """Rechnung berechnen, Nummer vergeben und als Datensatz speichern."""
    from tools import dokumente as _dok
    body = await req.json()
    positionen = body.get("positionen") or []
    if not positionen:
        raise HTTPException(status_code=400, detail="Mindestens eine Position nötig.")
    profile = _load_firmenprofil()
    nr = str(body.get("nummer") or "").strip() or _peek_rechnungsnummer()
    if not _RECHNUNG_NR_RE.match(nr):
        raise HTTPException(status_code=400, detail="Ungültige Rechnungsnummer.")

    inv = {
        "nummer": nr,
        "datum": body.get("datum") or date.today().isoformat(),
        "leistungsdatum": body.get("leistungsdatum", ""),
        "kunde": body.get("kunde") or {},
        "positionen": positionen,
        "ust_satz": body.get("ust_satz", profile.get("ust_satz", 19)),
        "kleinunternehmer": bool(body.get("kleinunternehmer",
                                          profile.get("kleinunternehmer", False))),
        "zahlungsziel_tage": body.get("zahlungsziel_tage", 14),
        "einleitung": body.get("einleitung", ""),
        "hinweis": body.get("hinweis", ""),
        # Workflow-Verknüpfung (optional)
        "project_id": str(body.get("project_id") or "").strip(),
        "angebot_nr": str(body.get("angebot_nr") or "").strip(),
        "abweichungen": body.get("abweichungen") or [],
        "erstellt_am": datetime.now().isoformat(),
    }
    computed = _dok.compute_invoice(inv)
    # Datensatz (Decimal → String für JSON)
    record = json.loads(json.dumps(inv, default=str))
    record["summe_netto"] = str(computed["summe_netto"])
    record["ust_betrag"] = str(computed["ust_betrag"])
    record["summe_brutto"] = str(computed["summe_brutto"])
    _rechnung_path(nr).write_text(
        json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    if not body.get("nummer"):
        _commit_rechnungsnummer(nr)
    # Projekt auf „abgerechnet" setzen + Rechnungsnummer hinterlegen
    if inv["project_id"]:
        _update_project_fields(inv["project_id"], status="abgerechnet", rechnung_nr=nr)
    return {
        "nummer": nr,
        "summe_netto": _dok.fmt_eur(computed["summe_netto"]),
        "ust_betrag": _dok.fmt_eur(computed["ust_betrag"]),
        "summe_brutto": _dok.fmt_eur(computed["summe_brutto"]),
    }


@router.get("/api/rechnung/list")
async def rechnung_list():
    from tools import dokumente as _dok
    out = []
    for fp in sorted(RECHNUNGEN_DIR.glob("*.json"), reverse=True):
        if fp.name.startswith("_"):
            continue
        try:
            r = json.loads(fp.read_text(encoding="utf-8"))
        except Exception:
            continue
        out.append({
            "nummer": r.get("nummer", fp.stem),
            "datum": r.get("datum", ""),
            "kunde": (r.get("kunde") or {}).get("name", ""),
            "brutto": _dok.fmt_eur(_dok._money(r.get("summe_brutto", 0))),
        })
    return {"rechnungen": out}


@router.delete("/api/rechnung/{nr}")
async def rechnung_delete(nr: str):
    fp = _rechnung_path(nr)
    if fp.exists():
        fp.unlink()
    return {"ok": True}


@router.get("/api/rechnung/{nr}/pdf")
async def rechnung_pdf(nr: str):
    from tools import dokumente as _dok
    from tools.export import to_pdf
    r = _load_rechnung(nr)
    md = _dok.invoice_markdown(r, _load_firmenprofil())
    data = {"title": f"Rechnung {r.get('nummer', '')}".strip(), "content": md,
            "_profile": _load_profile()}
    try:
        fp = await asyncio.to_thread(to_pdf, data)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"PDF-Export fehlgeschlagen: {e}")
    return FileResponse(fp, filename=f"Rechnung_{nr}.pdf", media_type="application/pdf")


@router.get("/api/rechnung/{nr}/docx")
async def rechnung_docx(nr: str):
    from tools import dokumente as _dok
    r = _load_rechnung(nr)
    try:
        fp = await asyncio.to_thread(_dok.invoice_docx, r, _load_firmenprofil())
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"DOCX-Export fehlgeschlagen: {e}")
    return FileResponse(
        fp, filename=f"Rechnung_{nr}.docx",
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document")


# ── Angebote (teilt Renderer/Berechnung mit Rechnungen, eigener Nummernkreis) ──

@router.get("/api/angebot/next-number")
async def angebot_next_number():
    return {"nummer": _peek_angebotsnummer()}


@router.post("/api/angebot/from-plan")
async def angebot_from_plan(req: Request):
    """Erzeugt Angebotspositionen aus einem Plan (nach Bereich gruppiert).
    Speichert nichts, ruft kein LLM auf — reine Kostenaggregation."""
    from tools import dokumente as _dok
    body = await req.json()
    plan_id = str(body.get("plan_id") or "").strip()
    project_id = str(body.get("project_id") or "").strip()
    # Plan über plan_id oder über die Projektverknüpfung finden
    plan = None
    if plan_id:
        fp = _plan_path_by_id(plan_id)
        if fp and fp.exists():
            plan = json.loads(fp.read_text(encoding="utf-8"))
    if plan is None and project_id:
        for f in PLANS_DIR.glob("*.json"):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
            except Exception:
                continue
            if (data.get("project_id") or "") == project_id:
                plan = data
                break
    if plan is None:
        raise HTTPException(status_code=404, detail="Kein Plan gefunden.")
    positionen = _dok.plan_to_positions(plan)
    if not positionen:
        raise HTTPException(status_code=422,
                            detail="Plan enthält keine bepreisten Vorgänge.")
    # Projektdaten für die Vorbelegung
    projekt = None
    pid = project_id or str(plan.get("project_id") or "").strip()
    if pid:
        for p in _load_projects():
            if p.get("id") == pid:
                projekt = p
                break
    return {
        "positionen": positionen,
        "plan_id": plan.get("id", ""),
        "plan_name": plan.get("name", ""),
        "project_id": pid,
        "projekt": projekt,
    }


@router.post("/api/angebot/create")
async def angebot_create(req: Request):
    """Angebot berechnen, Nummer vergeben und als Datensatz speichern."""
    from tools import dokumente as _dok
    body = await req.json()
    positionen = body.get("positionen") or []
    if not positionen:
        raise HTTPException(status_code=400, detail="Mindestens eine Position nötig.")
    profile = _load_firmenprofil()
    nr = str(body.get("nummer") or "").strip() or _peek_angebotsnummer()
    if not _RECHNUNG_NR_RE.match(nr):
        raise HTTPException(status_code=400, detail="Ungültige Angebotsnummer.")

    ang = {
        "nummer": nr,
        "datum": body.get("datum") or date.today().isoformat(),
        "leistungsdatum": body.get("leistungsdatum", ""),
        "gueltig_bis": body.get("gueltig_bis", ""),
        "gueltig_tage": body.get("gueltig_tage", 30),
        "kunde": body.get("kunde") or {},
        "positionen": positionen,
        "ust_satz": body.get("ust_satz", profile.get("ust_satz", 19)),
        "kleinunternehmer": bool(body.get("kleinunternehmer",
                                          profile.get("kleinunternehmer", False))),
        "einleitung": body.get("einleitung", ""),
        "hinweis": body.get("hinweis", ""),
        "project_id": str(body.get("project_id") or "").strip(),
        "plan_id": str(body.get("plan_id") or "").strip(),
        "erstellt_am": datetime.now().isoformat(),
    }
    computed = _dok.compute_invoice(ang)
    record = json.loads(json.dumps(ang, default=str))
    record["summe_netto"] = str(computed["summe_netto"])
    record["ust_betrag"] = str(computed["ust_betrag"])
    record["summe_brutto"] = str(computed["summe_brutto"])
    _angebot_path(nr).write_text(
        json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    if not body.get("nummer"):
        _commit_angebotsnummer(nr)
    # Projekt auf „angebot" setzen + Angebotsnummer hinterlegen
    if ang["project_id"]:
        _update_project_fields(ang["project_id"], status="angebot", angebot_nr=nr,
                               plan_id=ang["plan_id"] or None)
    return {
        "nummer": nr,
        "summe_netto": _dok.fmt_eur(computed["summe_netto"]),
        "ust_betrag": _dok.fmt_eur(computed["ust_betrag"]),
        "summe_brutto": _dok.fmt_eur(computed["summe_brutto"]),
    }


@router.get("/api/angebot/list")
async def angebot_list():
    from tools import dokumente as _dok
    out = []
    for fp in sorted(ANGEBOTE_DIR.glob("*.json"), reverse=True):
        if fp.name.startswith("_"):
            continue
        try:
            a = json.loads(fp.read_text(encoding="utf-8"))
        except Exception:
            continue
        out.append({
            "nummer": a.get("nummer", fp.stem),
            "datum": a.get("datum", ""),
            "kunde": (a.get("kunde") or {}).get("name", ""),
            "brutto": _dok.fmt_eur(_dok._money(a.get("summe_brutto", 0))),
            "project_id": a.get("project_id", ""),
        })
    return {"angebote": out}


@router.get("/api/angebot/{nr}")
async def angebot_get(nr: str):
    return _load_angebot(nr)


@router.delete("/api/angebot/{nr}")
async def angebot_delete(nr: str):
    fp = _angebot_path(nr)
    if fp.exists():
        fp.unlink()
    return {"ok": True}


@router.get("/api/angebot/{nr}/pdf")
async def angebot_pdf(nr: str):
    from tools import dokumente as _dok
    from tools.export import to_pdf
    a = _load_angebot(nr)
    md = _dok.invoice_markdown(a, _load_firmenprofil(), typ="angebot")
    data = {"title": f"Angebot {a.get('nummer', '')}".strip(), "content": md,
            "_profile": _load_profile()}
    try:
        fp = await asyncio.to_thread(to_pdf, data)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"PDF-Export fehlgeschlagen: {e}")
    return FileResponse(fp, filename=f"Angebot_{nr}.pdf", media_type="application/pdf")


@router.get("/api/angebot/{nr}/docx")
async def angebot_docx(nr: str):
    from tools import dokumente as _dok
    a = _load_angebot(nr)
    try:
        fp = await asyncio.to_thread(_dok.invoice_docx, a, _load_firmenprofil(), "angebot")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"DOCX-Export fehlgeschlagen: {e}")
    return FileResponse(
        fp, filename=f"Angebot_{nr}.docx",
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document")


@router.post("/api/zeugnis/generate")
async def zeugnis_generate(req: Request):
    """Qualifiziertes Arbeitszeugnis (codierte Zeugnissprache) via LLM erzeugen."""
    from tools import dokumente as _dok
    body = await req.json()
    meta = body.get("meta") or body
    if not (meta.get("name") and meta.get("position")):
        raise HTTPException(status_code=400, detail="Name und Position sind nötig.")
    # Arbeitgeber aus Firmenprofil vorbelegen, falls nicht angegeben
    if not meta.get("arbeitgeber"):
        prof = _load_firmenprofil()
        meta["arbeitgeber"] = prof.get("firma") or prof.get("inhaber") or ""
    model = _pick_model(body.get("model"), _model_for("general"))
    system = _dok.zeugnis_system_prompt()
    user = _dok.zeugnis_user_prompt(meta)
    tok_in = tok_out = 0
    text = ""
    try:
        async with _model_session(model), httpx.AsyncClient(timeout=300) as client:
            resp = await _llm.chat(client, {
                "model": model, "stream": False, "think": False,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                "options": {"temperature": 0.4},
            })
            resp.raise_for_status()
            j = resp.json()
            ti, to = _llm_tok(j)
            tok_in += ti; tok_out += to
            text = j.get("message", {}).get("content", "").strip()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erzeugung fehlgeschlagen: {e}")
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()
    if not text:
        raise HTTPException(status_code=422, detail="Kein Zeugnistext erzeugt.")
    # Datensatz speichern
    zid = f"{datetime.now():%Y%m%d_%H%M%S}_{_to_slug(meta.get('name', 'zeugnis'))[:24]}"
    (ZEUGNISSE_DIR / f"{zid}.json").write_text(
        json.dumps({"id": zid, "meta": meta, "text": text,
                    "erstellt_am": datetime.now().isoformat()},
                   ensure_ascii=False, indent=2), encoding="utf-8")
    return {"id": zid, "text": text, "note": _dok.NOTEN[_dok.note_key(meta.get("note"))]["label"],
            "tokens": {"in": tok_in, "out": tok_out}}


@router.post("/api/zeugnis/{zid}/save")
async def zeugnis_save(zid: str, req: Request):
    """Bearbeiteten Zeugnistext zurückspeichern."""
    if not re.match(r"^[A-Za-z0-9._\-]{1,80}$", zid):
        raise HTTPException(status_code=400, detail="Ungültige ID.")
    fp = ZEUGNISSE_DIR / f"{zid}.json"
    if not fp.exists():
        raise HTTPException(status_code=404, detail="Zeugnis nicht gefunden.")
    body = await req.json()
    rec = json.loads(fp.read_text(encoding="utf-8"))
    rec["text"] = str(body.get("text", rec.get("text", "")))
    rec["bearbeitet_am"] = datetime.now().isoformat()
    fp.write_text(json.dumps(rec, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"ok": True}


@router.get("/api/zeugnis/list")
async def zeugnis_list():
    out = []
    for fp in sorted(ZEUGNISSE_DIR.glob("*.json"), reverse=True):
        try:
            r = json.loads(fp.read_text(encoding="utf-8"))
        except Exception:
            continue
        meta = r.get("meta") or {}
        out.append({"id": r.get("id", fp.stem), "name": meta.get("name", ""),
                    "position": meta.get("position", ""),
                    "erstellt_am": r.get("erstellt_am", "")})
    return {"zeugnisse": out}


@router.get("/api/zeugnis/{zid}")
async def zeugnis_get(zid: str):
    if not re.match(r"^[A-Za-z0-9._\-]{1,80}$", zid):
        raise HTTPException(status_code=400, detail="Ungültige ID.")
    fp = ZEUGNISSE_DIR / f"{zid}.json"
    if not fp.exists():
        raise HTTPException(status_code=404, detail="Zeugnis nicht gefunden.")
    return json.loads(fp.read_text(encoding="utf-8"))


@router.delete("/api/zeugnis/{zid}")
async def zeugnis_delete(zid: str):
    if not re.match(r"^[A-Za-z0-9._\-]{1,80}$", zid):
        raise HTTPException(status_code=400, detail="Ungültige ID.")
    fp = ZEUGNISSE_DIR / f"{zid}.json"
    if fp.exists():
        fp.unlink()
    return {"ok": True}
