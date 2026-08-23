"""Router: Mail (IMAP read-only, /api/mail)

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


# ── Mail (IMAP read-only) → Wissensdatenbank ────────────────────────────────
# Reine stdlib (tools/mail.py). Server/Port/Benutzer liegen in data/mail.json
# (NICHT im Backup, NICHT in git – siehe .gitignore). Das **Passwort wird NICHT
# gespeichert**: es lebt nur im Arbeitsspeicher dieses Prozesses (_MAIL_SESSION_PW)
# und muss pro Sitzung neu eingegeben werden.
# MAIL_CONFIG_FILE → core (Backup nutzt es)

# Mail-Passwort nur im Speicher halten – nie auf Platte schreiben.
_MAIL_SESSION_PW: Optional[str] = None


def _load_mail_cfg() -> dict:
    """Server/Port/Benutzer aus der Datei – ohne Passwort.

    Räumt ein evtl. früher im Klartext gespeichertes Passwort einmalig aus der
    Datei (Altbestand), damit nichts auf der Platte verbleibt.
    """
    if not MAIL_CONFIG_FILE.exists():
        return {}
    try:
        cfg = json.loads(MAIL_CONFIG_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if not isinstance(cfg, dict):
        return {}
    if "password" in cfg:
        cfg.pop("password", None)
        try:
            MAIL_CONFIG_FILE.write_text(json.dumps(cfg, ensure_ascii=False, indent=2),
                                        encoding="utf-8")
        except Exception:
            pass
    return cfg


def _mail_runtime_cfg() -> dict:
    """Verbindungs-Konfiguration inkl. Session-Passwort (nur im Speicher)."""
    cfg = _load_mail_cfg()
    cfg["password"] = _MAIL_SESSION_PW or ""
    return cfg


def _mail_cfg_or_401() -> dict:
    """Wie _mail_runtime_cfg, aber mit klarem Hinweis, falls das Passwort fehlt."""
    if not _MAIL_SESSION_PW:
        raise HTTPException(
            status_code=401,
            detail="Mail-Passwort für diese Sitzung nicht gesetzt – bitte in den Einstellungen eingeben.",
        )
    return _mail_runtime_cfg()


@router.get("/api/mail/config")
async def mail_get_config():
    cfg = _load_mail_cfg()
    return {
        "protocol": cfg.get("protocol", "imap"),
        "host": cfg.get("host", ""),
        "port": cfg.get("port", 993),
        "user": cfg.get("user", ""),
        "ssl": cfg.get("ssl", True),
        # „has_password" = Passwort ist für DIESE Sitzung eingegeben (nur im Speicher)
        "has_password": bool(_MAIL_SESSION_PW),
        "password_session_only": True,
    }


@router.post("/api/mail/config")
async def mail_set_config(req: Request):
    global _MAIL_SESSION_PW
    body = await req.json()
    cfg = _load_mail_cfg()
    cfg["protocol"] = "pop3" if str(body.get("protocol", "imap")).lower() == "pop3" else "imap"
    cfg["host"] = str(body.get("host", "")).strip()
    cfg["port"] = int(body.get("port") or (995 if cfg["protocol"] == "pop3" else 993))
    cfg["user"] = str(body.get("user", "")).strip()
    cfg["ssl"] = bool(body.get("ssl", True))
    cfg.pop("password", None)   # Passwort niemals in die Datei schreiben
    # Server/Port/Benutzer dauerhaft speichern …
    MAIL_CONFIG_FILE.write_text(json.dumps(cfg, ensure_ascii=False, indent=2),
                                encoding="utf-8")
    # … das Passwort nur im Speicher dieser Sitzung halten.
    pw = body.get("password")
    if pw:
        _MAIL_SESSION_PW = pw
    return {"ok": True}


@router.post("/api/mail/list")
async def mail_list(req: Request):
    from tools import mail as _mail
    body = await req.json()
    cfg = _mail_cfg_or_401()
    limit = int(body.get("limit") or 25)
    search = str(body.get("search", "")).strip()
    try:
        items = await asyncio.to_thread(_mail.list_messages, cfg, limit, search)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"IMAP-Fehler: {e}")
    return {"messages": items}


@router.post("/api/mail/message")
async def mail_message(req: Request):
    """Holt eine einzelne Mail vollständig (Vorschau im rechten Mail-Bereich)."""
    from tools import mail as _mail
    body = await req.json()
    uid = body.get("uid")
    if not uid:
        raise HTTPException(status_code=400, detail="Keine Mail gewählt")
    cfg = _mail_cfg_or_401()
    try:
        msgs = await asyncio.to_thread(_mail.fetch_messages, cfg, [uid])
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Mail-Fehler: {e}")
    if not msgs:
        raise HTTPException(status_code=404, detail="Mail nicht gefunden")
    return msgs[0]


@router.post("/api/mail/to-rag")
async def mail_to_rag(req: Request):
    from tools import mail as _mail
    from tools.rag import ingest_file
    body = await req.json()
    cid = body.get("collection_id")
    uids = body.get("uids") or []
    coll = await _db.rag_get_collection(cid)
    if not coll:
        raise HTTPException(status_code=404, detail="Wissensdatenbank nicht gefunden")
    if not uids:
        raise HTTPException(status_code=400, detail="Keine Mails ausgewählt")
    cfg = _mail_cfg_or_401()
    try:
        msgs = await asyncio.to_thread(_mail.fetch_messages, cfg, uids)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"IMAP-Fehler: {e}")
    do_clean = body.get("clean", True)
    ingested, chunks = 0, 0
    for m in msgs:
        if not m["text"].strip():
            continue
        bodytext = _mail.clean_mail_text(m["text"]) if do_clean else m["text"]
        text = (
            f"Von: {m['from']}\nAn: {m.get('to', '')}\nDatum: {m['date']}\n"
            f"Betreff: {m['subject']}\n\n{bodytext}"
        ).strip()
        title = f"Mail: {m['subject']}"[:120]
        try:
            n = await ingest_file(coll, text, title, f"mail_{uuid.uuid4().hex[:12]}")
            ingested += 1
            chunks += n
        except Exception:
            continue
    return {"ok": True, "ingested": ingested, "chunks": chunks}


@router.post("/api/mail/to-postfach")
async def mail_to_postfach(req: Request):
    """Brücke Mail-Tab → Postfach: holt die neuesten Mails über die im Mail-Tab
    konfigurierte IMAP-/POP3-Verbindung (Session-Passwort), konvertiert sie über den
    Postfach-Parser (inkl. Anhänge) und legt einen Postfach-Store unter data/pst/<id>/ an.
    Danach im Postfach über die normale Wieder-Öffnen-Route nutzbar. Bleibt lokal; die
    Auswertung (Stufe 2/Tags) läuft weiter über ``_analysis_model``."""
    import email as _email
    from tools import mail as _mail
    from tools import mailstore
    body = await req.json()
    try:
        limit = int(body.get("limit") or 500)
    except Exception:
        limit = 500
    limit = max(1, min(limit, 5000))
    cfg = _mail_cfg_or_401()
    try:
        raws = await asyncio.to_thread(_mail.fetch_recent_raw, cfg, limit)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Mail-Abruf fehlgeschlagen: {e}")

    store_id = uuid.uuid4().hex[:12]
    base = PST_DIR / store_id
    att_dir = base / "att"
    att_dir.mkdir(parents=True, exist_ok=True)
    mails = []
    for i, raw in enumerate(raws):
        try:
            em = _email.message_from_bytes(raw)
            m = mailstore._msg_from_email(em, f"m{i}", "imap", att_dir)
            m["stage"] = 1
            mails.append(m)
        except Exception:
            continue
    store = {
        "id": store_id, "source": f"Mail-Konto: {cfg.get('user', '')}", "source_format": "imap",
        "opened_at": time.time(), "count": len(mails), "mails": mails,
    }
    (base / "store.json").write_text(json.dumps(store, ensure_ascii=False), encoding="utf-8")
    return {"store_id": store_id, "count": len(mails)}


# ── Mail-Aktionen & Regeln (regelbasierte Verarbeitung, Versand stets manuell) ──
# MAIL_RULES_FILE → core (Backup nutzt es)


def _load_mail_rules() -> list:
    if MAIL_RULES_FILE.exists():
        try:
            return json.loads(MAIL_RULES_FILE.read_text(encoding="utf-8")) or []
        except Exception:
            return []
    return []


@router.get("/api/mail/rules")
async def mail_rules_list():
    return {"rules": _load_mail_rules()}


@router.post("/api/mail/rules")
async def mail_rules_save(req: Request):
    """Speichert/aktualisiert eine Mail-Regel (Filter → bis zu 4 Aktionen).
    Eine Regel: {id, name, filter:{from,subject,domain}, actions:[…]}."""
    body = await req.json()
    rules = _load_mail_rules()
    rid = str(body.get("id") or uuid.uuid4().hex[:10])
    rule = {
        "id": rid,
        "name": str(body.get("name", "")).strip() or "Regel",
        "filter": body.get("filter") or {},
        "actions": (body.get("actions") or [])[:4],
    }
    rules = [r for r in rules if r.get("id") != rid] + [rule]
    MAIL_RULES_FILE.write_text(json.dumps(rules, ensure_ascii=False, indent=2),
                               encoding="utf-8")
    return {"ok": True, "rule": rule}


@router.delete("/api/mail/rules/{rid}")
async def mail_rules_delete(rid: str):
    rules = [r for r in _load_mail_rules() if r.get("id") != rid]
    MAIL_RULES_FILE.write_text(json.dumps(rules, ensure_ascii=False, indent=2),
                               encoding="utf-8")
    return {"ok": True}


@router.post("/api/mail/action/rag")
async def mail_action_rag(req: Request):
    """Eine einzelne Mail (optional bereinigt) in eine Wissensdatenbank übernehmen."""
    from tools import mail as _mail
    from tools.rag import ingest_file
    body = await req.json()
    uid = body.get("uid")
    cid = body.get("collection_id")
    if not uid:
        raise HTTPException(status_code=400, detail="Keine Mail gewählt")
    coll = await _db.rag_get_collection(cid)
    if not coll:
        raise HTTPException(status_code=404, detail="Wissensdatenbank nicht gefunden")
    cfg = _mail_cfg_or_401()
    try:
        msgs = await asyncio.to_thread(_mail.fetch_messages, cfg, [uid])
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Mail-Fehler: {e}")
    if not msgs:
        raise HTTPException(status_code=404, detail="Mail nicht gefunden")
    m = msgs[0]
    bodytext = _mail.clean_mail_text(m["text"]) if body.get("clean", True) else m["text"]
    if not bodytext.strip():
        raise HTTPException(status_code=400, detail="Mail hat keinen Text")
    text = (
        f"Von: {m['from']}\nAn: {m.get('to', '')}\nDatum: {m['date']}\n"
        f"Betreff: {m['subject']}\n\n{bodytext}"
    ).strip()
    title = f"Mail: {m['subject']}"[:120]
    n = await ingest_file(coll, text, title, f"mail_{uuid.uuid4().hex[:12]}")
    return {"ok": True, "chunks": n}


@router.post("/api/mail/action/agent")
async def mail_action_agent(req: Request):
    """Lässt einen Agenten eine Aufgabe an einer Mail erledigen (z. B. Antwort
    entwerfen, zusammenfassen). Gibt NUR den erzeugten Text zurück — nichts wird
    gesendet. Versand erfolgt stets manuell im Frontend."""
    body = await req.json()
    uid = body.get("uid")
    instruction = str(body.get("instruction", "")).strip()
    if not uid:
        raise HTTPException(status_code=400, detail="Keine Mail gewählt")
    if not instruction:
        raise HTTPException(status_code=400, detail="Kein Auftrag angegeben")

    from tools import mail as _mail
    cfg = _mail_cfg_or_401()
    try:
        msgs = await asyncio.to_thread(_mail.fetch_messages, cfg, [uid])
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Mail-Fehler: {e}")
    if not msgs:
        raise HTTPException(status_code=404, detail="Mail nicht gefunden")
    m = msgs[0]

    # Agent-System-Prompt laden (optional); Modell wählen
    sys_prompt = (
        "Du bist ein sorgfältiger Assistent für die Mailbearbeitung. Antworte auf "
        "Deutsch, sachlich und nur auf Basis der vorliegenden Mail. Erfinde nichts."
    )
    model = _model_for("general")
    aid = body.get("agent_id")
    if aid:
        af = _agent_path_by_id(aid)
        if af and af.exists():
            try:
                agent = json.loads(af.read_text(encoding="utf-8"))
                sys_prompt = agent.get("system_prompt") or sys_prompt
                if agent.get("model"):
                    model = agent["model"]
            except Exception:
                pass
    model = _pick_model(body.get("model"), model)

    bodytext = _mail.clean_mail_text(m["text"]) or m["text"]
    user_msg = (
        f"AUFGABE: {instruction}\n\n"
        f"--- E-MAIL ---\nVon: {m['from']}\nAn: {m.get('to', '')}\n"
        f"Datum: {m['date']}\nBetreff: {m['subject']}\n\n{bodytext}\n--- ENDE ---\n\n"
        f"Erledige die Aufgabe. Gib NUR das Ergebnis aus (keine Vorrede). "
        f"Falls eine Antwort-Mail verlangt ist, formuliere sie versandfertig."
    )
    try:
        async with _model_session(model), httpx.AsyncClient(timeout=180) as client:
            resp = await _llm.chat(client,{
                "model": model,
                "think": False,
                "messages": [
                    {"role": "system", "content": sys_prompt},
                    {"role": "user", "content": user_msg},
                ],
                "options": {"temperature": 0.3},
                "stream": False,
            })
            resp.raise_for_status()
            _ma_j = resp.json()
            _ma_ti, _ma_to = _llm_tok(_ma_j)
            out = _ma_j.get("message", {}).get("content", "").strip()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Agent-Fehler: {e}")
    out = re.sub(r"<think>.*?</think>", "", out, flags=re.DOTALL).strip()
    return {"ok": True, "text": out, "model": model,
            "subject": m.get("subject", ""), "from": m.get("from", ""),
            "tokens": {"in": _ma_ti, "out": _ma_to}}
