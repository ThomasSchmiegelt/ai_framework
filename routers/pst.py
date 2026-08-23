"""Router: Postfach-Auswertung PST/mbox/eml (/api/pst, nur lokal)

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


# ── Postfach-Auswertung (PST/mbox/eml/msg) ──────────────────────────────────────
# Liest ein Mail-Postfach ein (Stufe 1: Absender/Empfänger/Betreff/Datum/Inhalt),
# legt es lokal unter data/pst/<id>/ ab und wertet auf Wunsch Anhänge aus (Stufe 2:
# Dokument-Text via tools.files.extract + Bilder direkt am lokalen Vision-Modell,
# kein OCR). Die Analyse (Stufe 2) läuft AUSSCHLIESSLICH lokal. Wissensgraph +
# Konnektoren werden im Frontend gebildet.

_PST_LIST_BODY_CHARS = 6000     # Body-Vorschau in der Listen-/Graph-Antwort (Volltext via mail-Endpoint)
_PST_MAX_MAILS = 5000
_PST_TAG_SYSTEM = (
    "Du wertest EINE E-Mail (inkl. evtl. beigefügter Dokument-/Bildinhalte) aus. Vergib "
    "kurze, treffende Schlagworte/Themen (Firmen, Produkte, Vorgänge, Fachbegriffe) und eine "
    "knappe Zusammenfassung der Anhänge. Erfinde nichts. "
    'Antworte NUR mit JSON: {"tags":["…"],"attachments_summary":"…"}.'
)


def _pst_resolve_file(p: str) -> Path:
    fp = Path(str(p or "").strip()).expanduser()
    # Datei (PST/mbox/eml/msg/endungslose Thunderbird-Mbox) ODER Verzeichnis (Maildir).
    if not fp.exists():
        raise HTTPException(status_code=400, detail=f"Nicht gefunden: {fp}")
    if not (fp.is_file() or fp.is_dir()):
        raise HTTPException(status_code=400, detail=f"Kein lesbares Postfach: {fp}")
    return fp


def _pst_store_dir(store_id: str) -> Path:
    sid = re.sub(r"[^A-Za-z0-9]+", "", str(store_id or ""))
    d = PST_DIR / sid
    if not sid or not (d / "store.json").exists():
        raise HTTPException(status_code=404, detail="Postfach nicht gefunden")
    return d


def _pst_load(store_id: str) -> tuple[Path, dict]:
    d = _pst_store_dir(store_id)
    return d, json.loads((d / "store.json").read_text(encoding="utf-8"))


def _pst_list_view(mails: list) -> list:
    """Kompakte Mail-Liste für Frontend (Graph/Konnektoren) — Body gekürzt."""
    out = []
    for m in mails:
        out.append({
            "mid": m.get("mid"), "folder": m.get("folder", ""),
            "sender": m.get("sender", ""), "recipients": m.get("recipients", ""),
            "cc": m.get("cc", ""), "subject": m.get("subject", ""),
            "date": m.get("date", ""),
            "body": (m.get("body", "") or "")[:_PST_LIST_BODY_CHARS],
            "attachments": [{"name": a.get("name"), "ext": a.get("ext"), "size": a.get("size")}
                            for a in (m.get("attachments") or [])],
            "tags": m.get("tags") or [],
            "attachments_summary": m.get("attachments_summary", ""),
            "stage": m.get("stage", 1),
        })
    return out


@router.get("/api/pst/formats")
async def pst_formats():
    """Welche Eingabeformate auf diesem System nutzbar sind (für die UI-Hinweise)."""
    from tools import mailstore
    return {"formats": mailstore.available_formats(), "local_llm": await _local_llm_available()}


@router.get("/api/pst/stores")
async def pst_stores():
    """Bereits eingelesene (persistierte) Postfächer auflisten — zum Wieder-Öffnen ohne
    erneutes Parsen der .pst. Muss VOR der {store_id}-Route stehen."""
    out = []
    if PST_DIR.exists():
        for d in PST_DIR.iterdir():
            sj = d / "store.json"
            if not sj.is_file():
                continue
            try:
                s = json.loads(sj.read_text(encoding="utf-8"))
            except Exception:
                continue
            mails = s.get("mails", [])
            out.append({
                "store_id": s.get("id", d.name),
                "source": s.get("source", ""),
                "name": Path(s.get("source", "")).name or d.name,
                "count": s.get("count", len(mails)),
                "opened_at": s.get("opened_at", 0),
                "stage2": sum(1 for m in mails if m.get("stage") == 2),
                "has_similarity": (d / "similarity.json").is_file(),
                "has_settings": bool(s.get("settings")),
            })
    out.sort(key=lambda x: x.get("opened_at", 0), reverse=True)
    return {"stores": out}


@router.get("/api/pst/{store_id}")
async def pst_reopen(store_id: str):
    """Ein persistiertes Postfach wieder öffnen (kein erneutes Parsen). Liefert die
    Mailliste, die gecachten Ähnlichkeits-Kanten und die gespeicherten Einstellungen."""
    d, store = _pst_load(store_id)
    sim = []
    sp = d / "similarity.json"
    if sp.is_file():
        try:
            sim = json.loads(sp.read_text(encoding="utf-8")).get("edges", [])
        except Exception:
            sim = []
    return {
        "store_id": store.get("id", store_id),
        "count": store.get("count", 0),
        "source": store.get("source", ""),
        "source_format": store.get("source_format", ""),
        "mails": _pst_list_view(store.get("mails", [])),
        "similarity": sim,
        "settings": store.get("settings") or None,
    }


@router.post("/api/pst/{store_id}/settings")
async def pst_save_settings(store_id: str, req: Request):
    """Ansicht + Konnektoren zu einem Postfach speichern (in store.json)."""
    d, store = _pst_load(store_id)
    body = await req.json()
    store["settings"] = body.get("settings") or {}
    (d / "store.json").write_text(json.dumps(store, ensure_ascii=False), encoding="utf-8")
    return {"ok": True}


@router.post("/api/pst/open")
async def pst_open(req: Request):
    """Postfach einlesen (Stufe 1). Reine Extraktion (kein LLM nötig). Legt den geparsten
    Store + Anhänge lokal unter data/pst/<id>/ ab."""
    from tools import mailstore
    body = await req.json()
    fp = _pst_resolve_file(body.get("path", ""))
    password = str(body.get("password", "") or "") or None

    # PST-Passwort (nur CRC-Prüfung, verschlüsselt nichts) → Hinweis für die UI.
    pw_status = {"protected": False, "verified": False, "checked": False}
    if fp.suffix.lower() == ".pst":
        pw_status = await asyncio.to_thread(mailstore.pst_password_status, fp, password)

    store_id = uuid.uuid4().hex[:12]
    base = PST_DIR / store_id
    att_dir = base / "att"
    try:
        mails = await asyncio.to_thread(mailstore.read_store, fp, password, att_dir, _PST_MAX_MAILS)
    except mailstore.MailFormatUnavailable as e:
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Postfach konnte nicht gelesen werden: {e}")

    for m in mails:
        m["stage"] = 1
    # Anzeige-Label: endungslose Thunderbird-Mbox → „mbox", Verzeichnis → „maildir".
    _fmt = fp.suffix.lower() or ("maildir" if fp.is_dir() else "mbox")
    store = {
        "id": store_id, "source": str(fp), "source_format": _fmt,
        "opened_at": time.time(), "count": len(mails), "mails": mails,
    }
    base.mkdir(parents=True, exist_ok=True)
    (base / "store.json").write_text(json.dumps(store, ensure_ascii=False), encoding="utf-8")
    return {"store_id": store_id, "count": len(mails), "source_format": _fmt,
            "password": pw_status, "mails": _pst_list_view(mails)}


@router.get("/api/pst/{store_id}/mail/{mid}")
async def pst_mail(store_id: str, mid: str):
    """Vollständige E-Mail (Header + kompletter Body + Anhang-Infos) für die Klick-Ansicht."""
    _, store = _pst_load(store_id)
    for m in store.get("mails", []):
        if m.get("mid") == mid:
            return m
    raise HTTPException(status_code=404, detail="Mail nicht gefunden")


@router.delete("/api/pst/{store_id}")
async def pst_delete(store_id: str):
    """Geparstes Postfach (inkl. Anhängen) verwerfen."""
    d = _pst_store_dir(store_id)
    import shutil
    shutil.rmtree(d, ignore_errors=True)
    return {"ok": True}


@router.post("/api/pst/analyze")
async def pst_analyze(req: Request):
    """Stufe 2: Anhänge lesen (Dokument-Text via tools.files.extract + Bilder am lokalen
    Vision-Modell) und je Mail Themen-Schlagworte vergeben. AUSSCHLIESSLICH lokal."""
    from tools import files as _files
    body = await req.json()
    d, store = _pst_load(str(body.get("store_id", "")))
    att_base = d / "att"
    mids = body.get("mids")
    want_tags = bool(body.get("tags", True))

    model = await _analysis_model(body.get("model"))
    if not model:
        raise HTTPException(status_code=503, detail="Kein lokales LLM verfügbar – die Postfach-Analyse benötigt ein lokales Modell (Ollama). Alternativ im Profil „API-Modelle für vertrauliche Auswertungen“ aktivieren und ein API-Modell wählen.")

    targets = [m for m in store.get("mails", []) if (not mids or m.get("mid") in set(mids))]
    budget = max(1200, int(_profile_num_ctx() * 3.5 * 0.6))
    tin = tout = 0
    analyzed = 0

    async with _model_session(model), httpx.AsyncClient(timeout=240) as client:
        for m in targets:
            doc_texts, images = [], []
            for a in (m.get("attachments") or []):
                rel = a.get("rel") or ""
                if not rel:
                    continue
                ap = (att_base / rel).resolve()
                try:
                    if att_base.resolve() not in ap.parents:
                        continue
                except Exception:
                    continue
                if not ap.is_file():
                    continue
                ext = (a.get("ext") or "").lower()
                if ext in ("jpg", "jpeg", "png", "gif", "webp", "bmp"):
                    try:
                        if len(images) < 3:
                            images.append(base64.b64encode(ap.read_bytes()).decode())
                    except Exception:
                        pass
                else:
                    try:
                        txt = _files.extract(ap)
                        if txt and not txt.startswith("["):
                            doc_texts.append(f"[{a.get('name')}]\n{txt[:4000]}")
                    except Exception:
                        pass

            if not want_tags and not images and not doc_texts:
                continue

            usr = (f"Betreff: {m.get('subject','')}\nAbsender: {m.get('sender','')}\n\n"
                   f"Inhalt:\n{(m.get('body','') or '')[:budget]}")
            if doc_texts:
                usr += "\n\nDokument-Anhänge:\n" + "\n\n".join(doc_texts)
            if images:
                usr += "\n\n(Es sind Bild-Anhänge beigefügt — beschreibe/verwerte deren Inhalt.)"

            msg = {"role": "user", "content": usr}
            if images:
                msg["images"] = images
            try:
                resp = await _llm.chat(client, {
                    "model": model, "think": False, "stream": False, "format": "json",
                    "messages": [{"role": "system", "content": _PST_TAG_SYSTEM}, msg],
                    "options": {"num_ctx": _profile_num_ctx()}, "keep_alive": KEEP_ALIVE,
                })
                resp.raise_for_status()
                j = resp.json()
                a_in, a_out = _llm_tok(j); tin += a_in; tout += a_out
                data = _parse_llm_json(j.get("message", {}).get("content", "")) or {}
                tags = [str(t).strip() for t in (data.get("tags") or []) if str(t).strip()][:12]
                m["tags"] = tags
                m["attachments_summary"] = str(data.get("attachments_summary", "")).strip()[:1200]
            except Exception:
                m.setdefault("tags", [])
            if doc_texts:
                m["attachment_text"] = "\n\n".join(doc_texts)[:20000]
            m["stage"] = 2
            analyzed += 1

    (d / "store.json").write_text(json.dumps(store, ensure_ascii=False), encoding="utf-8")
    return {"analyzed": analyzed, "mails": _pst_list_view(store.get("mails", [])),
            "tokens": {"in": tin, "out": tout}}


def _pst_mail_text(m: dict) -> str:
    """Textbasis einer Mail für Embeddings/RAG (Betreff + Body + Anhang-Zusammenfassung)."""
    parts = [m.get("subject", ""), (m.get("body", "") or "")[:2000]]
    if m.get("attachments_summary"):
        parts.append(str(m["attachments_summary"]))
    txt = "\n".join(p for p in parts if p).strip()
    return txt or (m.get("subject") or m.get("sender") or "—")


@router.post("/api/pst/similarity")
async def pst_similarity(req: Request):
    """Verwandtschaftsgrad = semantische Ähnlichkeit. Bettet jede Mail LOKAL ein (Ollama,
    CPU) und liefert Mail-Paare mit Cosine-Score. Rein Vektor-Mathematik, kein Chat-LLM."""
    from tools import rag as _rag
    import numpy as np
    body = await req.json()
    d, store = _pst_load(str(body.get("store_id", "")))
    if not await _local_llm_available():
        raise HTTPException(status_code=503, detail="Kein lokales LLM/Ollama verfügbar – die Ähnlichkeitsanalyse braucht das lokale Embeddingmodell.")
    mids = body.get("mids")
    sel = set(mids) if mids else None
    mails = [m for m in store.get("mails", []) if (sel is None or m.get("mid") in sel)]
    if len(mails) < 2:
        return {"edges": [], "count": len(mails)}
    texts = [_pst_mail_text(m) for m in mails]
    try:
        vecs = await _rag.embed(texts, EMBED_MODEL, gpu=False)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Embeddings fehlgeschlagen (Modell {EMBED_MODEL} vorhanden?): {e}")
    if len(vecs) != len(mails):
        raise HTTPException(status_code=502, detail=f"Embedding-Anzahl passt nicht – ist '{EMBED_MODEL}' in Ollama vorhanden? (ollama pull {EMBED_MODEL})")
    mat = np.asarray(vecs, dtype=np.float32)
    norms = np.linalg.norm(mat, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    mat = mat / norms
    sims = mat @ mat.T
    ids = [m.get("mid") for m in mails]
    thr = float(body.get("min_score", 0.5))
    n = len(ids)
    edges = []
    for i in range(n):
        row = sims[i]
        for j in range(i + 1, n):
            s = float(row[j])
            if s >= thr:
                edges.append({"a": ids[i], "b": ids[j], "score": round(s, 4)})
    edges.sort(key=lambda e: e["score"], reverse=True)
    edges = edges[:6000]   # Graph lesbar / Datei klein halten
    (d / "similarity.json").write_text(json.dumps({"edges": edges}, ensure_ascii=False), encoding="utf-8")
    return {"edges": edges, "count": n}


@router.post("/api/pst/to-rag")
async def pst_to_rag(req: Request):
    """Ausgewählte Mails in eine (neue oder bestehende) lokale Wissensdatenbank übernehmen.
    Embeddings laufen lokal (Ollama, CPU) → 503, wenn kein lokales LLM vorhanden ist."""
    from tools.rag import ingest_file, tier_config
    body = await req.json()
    d, store = _pst_load(str(body.get("store_id", "")))
    if not await _local_llm_available():
        raise HTTPException(status_code=503, detail="Kein lokales LLM/Ollama verfügbar – RAG-Embeddings brauchen ein lokales Modell.")
    mids = body.get("mids")
    sel = set(mids) if mids else None
    include_att = bool(body.get("include_attachments", True))
    new_name = str(body.get("new_collection_name", "") or "").strip()

    if new_name:
        tc = tier_config("regler")
        coll = {
            "id": f"rag_{uuid.uuid4().hex[:12]}", "name": new_name[:120],
            "embed_model": EMBED_MODEL, "tier": "regler",
            "chunk_size": tc["chunk_size"], "chunk_overlap": tc["chunk_overlap"],
            "top_k": tc["top_k"], "embed_gpu": False, "clean": True,
            "char_limit": tc["char_limit"], "strictness": "ausgewogen",
            "created_at": time.time(),
        }
        await _db.rag_create_collection(coll)
    else:
        coll = await _db.rag_get_collection(body.get("collection_id"))
        if not coll:
            raise HTTPException(status_code=404, detail="Wissensdatenbank nicht gefunden")

    targets = [m for m in store.get("mails", []) if (sel is None or m.get("mid") in sel)]
    ingested = chunks = 0
    for m in targets:
        body_txt = (m.get("body", "") or "").strip()
        if include_att and m.get("attachments_summary"):
            body_txt += "\n\nAnhänge (Zusammenfassung): " + str(m["attachments_summary"])
        if include_att and m.get("attachment_text"):
            body_txt += "\n\n" + str(m["attachment_text"])
        if not body_txt.strip() and not m.get("subject"):
            continue
        text = (f"Von: {m.get('sender','')}\nAn: {m.get('recipients','')}\n"
                f"Datum: {m.get('date','')}\nBetreff: {m.get('subject','')}\n\n{body_txt}").strip()
        title = f"Mail: {m.get('subject','') or m.get('sender','')}"[:120]
        try:
            n = await ingest_file(coll, text, title, f"mail_{uuid.uuid4().hex[:12]}")
            ingested += 1
            chunks += n
        except Exception:
            continue
    return {"ok": True, "ingested": ingested, "chunks": chunks,
            "collection_id": coll["id"], "collection_name": coll["name"]}


@router.post("/api/pst/ask")
async def pst_ask(req: Request):
    """„Postfach fragen": Frage gegen eine lokale Wissensdatenbank (RAG) beantworten.
    AUSSCHLIESSLICH lokal (Embedding-Suche + lokales Chat-Modell)."""
    from tools.rag import query_collections
    body = await req.json()
    _pst_load(str(body.get("store_id", "")))   # Existenzprüfung
    question = str(body.get("question", "")).strip()
    cid = body.get("collection_id")
    if not question:
        raise HTTPException(status_code=400, detail="Keine Frage angegeben")
    if not cid:
        raise HTTPException(status_code=400, detail="Keine Wissensdatenbank gewählt – Mails erst per RAG-Übernahme einlesen.")
    model = await _analysis_model(body.get("model"))
    if not model:
        raise HTTPException(status_code=503, detail="Kein lokales LLM verfügbar – Postfach-Fragen laufen standardmäßig lokal (Profil-Schalter „API-Modelle für vertrauliche Auswertungen“ erlaubt API-Modelle).")
    hits = await query_collections([cid], question, top_k_cap=8)
    context = "\n\n---\n\n".join(f"[{h.get('filename','')}]\n{h.get('text','')}" for h in hits)
    sys_p = ("Beantworte die Frage NUR anhand des bereitgestellten E-Mail-Kontexts. Wenn die "
             "Antwort dort nicht steht, sage das offen. Antworte knapp auf Deutsch und nenne "
             "relevante Betreffzeilen/Absender als Beleg.")
    usr = f"Kontext (E-Mails):\n{context or '(keine Treffer)'}\n\nFrage: {question}"
    tin = tout = 0
    async with _model_session(model), httpx.AsyncClient(timeout=240) as client:
        resp = await _llm.chat(client, {
            "model": model, "think": False, "stream": False,
            "messages": [{"role": "system", "content": sys_p}, {"role": "user", "content": usr}],
            "options": {"num_ctx": _profile_num_ctx()}, "keep_alive": KEEP_ALIVE,
        })
        resp.raise_for_status()
        j = resp.json()
        tin, tout = _llm_tok(j)
        answer = j.get("message", {}).get("content", "").strip()
    sources = [{"filename": h.get("filename", ""), "score": round(float(h.get("score", 0)), 3),
                "collection": h.get("collection_name", "")} for h in hits]
    return {"answer": answer, "sources": sources, "tokens": {"in": tin, "out": tout}}


@router.post("/api/pst/summarize")
async def pst_summarize(req: Request):
    """Zusammenfassung einer Mail-Auswahl — LOKAL, Map-Reduce bei vielen Mails."""
    body = await req.json()
    d, store = _pst_load(str(body.get("store_id", "")))
    mids = body.get("mids")
    sel = set(mids) if mids else None
    model = await _analysis_model(body.get("model"))
    if not model:
        raise HTTPException(status_code=503, detail="Kein lokales LLM verfügbar – die Zusammenfassung läuft standardmäßig lokal (Profil-Schalter „API-Modelle für vertrauliche Auswertungen“ erlaubt API-Modelle).")
    mails = [m for m in store.get("mails", []) if (sel is None or m.get("mid") in sel)]
    if not mails:
        raise HTTPException(status_code=400, detail="Keine Mails ausgewählt")
    blocks = [(f"Von: {m.get('sender','')} | Datum: {(m.get('date','') or '')[:10]}\n"
               f"Betreff: {m.get('subject','')}\n{(m.get('body','') or '')[:1500]}") for m in mails]
    sys_p = ("Fasse die folgenden E-Mails sachlich auf Deutsch zusammen: zentrale Themen, "
             "Beteiligte, offene Punkte/To-dos. Erfinde nichts, nutze kurze Stichpunkte.")
    num_ctx = _profile_num_ctx()
    budget = max(2000, int(num_ctx * 3.2))
    tin = tout = 0
    async with _model_session(model), httpx.AsyncClient(timeout=300) as client:
        async def _run(text: str):
            r = await _llm.chat(client, {
                "model": model, "think": False, "stream": False,
                "messages": [{"role": "system", "content": sys_p}, {"role": "user", "content": text}],
                "options": {"num_ctx": num_ctx}, "keep_alive": KEEP_ALIVE,
            })
            r.raise_for_status()
            jj = r.json()
            a, b = _llm_tok(jj)
            return jj.get("message", {}).get("content", "").strip(), a, b
        # Map: Blöcke bis Budget bündeln
        groups, cur, cur_len = [], [], 0
        for blk in blocks:
            if cur and cur_len + len(blk) > budget:
                groups.append("\n\n===\n\n".join(cur)); cur, cur_len = [], 0
            cur.append(blk); cur_len += len(blk)
        if cur:
            groups.append("\n\n===\n\n".join(cur))
        partials = []
        for g in groups:
            txt, a, b = await _run(g); tin += a; tout += b; partials.append(txt)
        # Reduce
        if len(partials) <= 1:
            summary = partials[0] if partials else ""
        else:
            txt, a, b = await _run("Fasse diese Teil-Zusammenfassungen zu EINER prägnanten "
                                   "Gesamtzusammenfassung zusammen:\n\n" + "\n\n---\n\n".join(partials))
            tin += a; tout += b; summary = txt
    return {"summary": summary, "count": len(mails), "tokens": {"in": tin, "out": tout}}


_PST_COMMAND_SYSTEM = (
    "Du steuerst die Anzeige eines E-Mail-Wissensgraphen. Gib EINE JSON-Direktive zurück und setze "
    "NUR Felder, die die Anweisung wirklich nennt. Trigger → Feld: "
    "'Netz'/'wer mit wem'/'Kommunikation' → mode='net'; 'Themen-Nähe'/'verwandt'/'ähnlich' → mode='sim'; "
    "'Konnektor'/'nach Konnektoren' → mode='conn'; ein genannter Konnektorname aus der Liste → connector "
    "(EXAKT so schreiben); 'nur …'/'verbunden'/'isolierte ausblenden' → only_connected=true; "
    "'mit Anhang' → has_attachment=true; Monats-/Zeitangaben → date_from und date_to (YYYY-MM-DD, "
    "nutze den unten genannten Postfach-Zeitraum fuer das Jahr); "
    "'zeige X'/'zentriere auf X'/'X mit Eltern/Kindern/Nachbarn' → focus='X' und hops (1 = direkte "
    "Eltern/Kinder, 2-3 = weiter); ein reiner Suchbegriff → query. explain = EIN kurzer deutscher Satz. "
    "Moegliche Felder: mode, connector, sender, query, date_from, date_to, has_attachment, "
    "only_connected, focus, hops (1-3), explain. Beispiele: "
    "'Synera mit Eltern und Kindern' => {'focus':'Synera','hops':1,'explain':'…'}; "
    "'nur Konnektor Lebensversicherung als Netz' => {'mode':'net','connector':'Lebensversicherung','only_connected':true,'explain':'…'}; "
    "'Mails im Dezember mit Anhang' => {'date_from':'2025-12-01','date_to':'2025-12-31','has_attachment':true,'explain':'…'}. "
    "Antworte NUR mit JSON."
)


@router.post("/api/pst/command")
async def pst_command(req: Request):
    """Natürlichsprachiger Befehl → Anzeige-Direktive für den Postfach-Graphen.
    AUSSCHLIESSLICH lokal (LLM über _local_model, sonst 503)."""
    body = await req.json()
    _, store = _pst_load(str(body.get("store_id", "")))
    text = str(body.get("text", "")).strip()
    if not text:
        raise HTTPException(status_code=400, detail="Kein Befehl angegeben")
    conns = [str(c).strip() for c in (body.get("connectors") or []) if str(c).strip()][:40]
    model = await _analysis_model(body.get("model"))
    if not model:
        raise HTTPException(status_code=503, detail="Kein lokales LLM verfügbar – der Graph-Befehl läuft standardmäßig lokal (Profil-Schalter „API-Modelle für vertrauliche Auswertungen“ erlaubt API-Modelle).")
    # Zeitraum des Postfachs mitgeben (hilft dem Modell bei „Dezember" & Co.)
    dates = sorted(d[:10] for d in (m.get("date", "") for m in store.get("mails", [])) if d[:10])
    span = f"{dates[0]} bis {dates[-1]}" if dates else "unbekannt"
    usr = (f"Postfach-Zeitraum: {span}.\nVerfügbare Konnektoren: {', '.join(conns) or '(keine)'}.\n\n"
           f"Anweisung: {text}")
    tin = tout = 0
    async with _model_session(model), httpx.AsyncClient(timeout=120) as client:
        resp = await _llm.chat(client, {
            "model": model, "think": False, "stream": False, "format": "json",
            "messages": [{"role": "system", "content": _PST_COMMAND_SYSTEM},
                         {"role": "user", "content": usr}],
            "options": {"num_ctx": _profile_num_ctx()}, "keep_alive": KEEP_ALIVE,
        })
        resp.raise_for_status()
        j = resp.json()
        tin, tout = _llm_tok(j)
        raw = _parse_llm_json(j.get("message", {}).get("content", "")) or {}
    # Direktive säubern (nur bekannte, plausible Felder)
    out: dict = {}
    if raw.get("mode") in ("conn", "sim", "net"):
        out["mode"] = raw["mode"]
    for k in ("connector", "sender", "query", "date_from", "date_to", "focus", "explain"):
        v = raw.get(k)
        if isinstance(v, str) and v.strip():
            out[k] = v.strip()[:160]
    if raw.get("only_connected") in (True, False):
        out["only_connected"] = raw["only_connected"]
    if raw.get("has_attachment") is True:
        out["has_attachment"] = True
    try:
        out["hops"] = max(1, min(3, int(raw.get("hops") or 1)))
    except Exception:
        out["hops"] = 1
    return {"directive": out, "tokens": {"in": tin, "out": tout}}




