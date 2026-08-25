"""Router: Patent-Recherche (EPO OPS + Google, /api/patente)

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


# ── Patent-Recherche (Kanzlei Patent-Werkzeug) ───────────────────────────────────
# Portiert aus dem eigenständigen Streamlit-Tool ~/ai-project/patente: Google-
# Patents-Scraping (keine offizielle API, ToS-Risiko wie im Original) in
# projektbezogene Fallakten (data/patente/<projekt>/patente.json), semantische
# Suche über die Framework-eigene RAG-Engine (tools/rag.py, kein ChromaDB), eine
# 7-stufige Analyse-Pipeline (Technik/Recht/Umgehung/Innovation/Entwurf/Kritik/
# Moderator, tools/patente.run_pipeline) und ein Wissensgraph (Cytoscape.js im
# Frontend, kein pyvis/Backend-HTML). Modellwahl frei (wie Chat-Tab, _pick_model)
# — bewusst KEIN _analysis_model-Zwang; nur die RAG-Embeddings selbst laufen wie
# überall im Framework lokal (_local_llm_available-Gate).


def _pat_ops_creds() -> Optional[dict]:
    """EPO-OPS-Zugangsdaten (consumer_key/secret) aus data/epo_ops.json — oder
    None, wenn nicht konfiguriert (→ Google-Scraping-Fallback)."""
    if not EPO_OPS_FILE.exists():
        return None
    try:
        d = json.loads(EPO_OPS_FILE.read_text(encoding="utf-8"))
        if d.get("consumer_key") and d.get("consumer_secret"):
            return d
    except Exception:
        pass
    return None


def _pat_safe_name(name: str) -> str:
    safe = "".join(c for c in str(name or "") if c.isalnum() or c in (" ", "_", "-")).strip()
    if not safe or safe.startswith("_"):   # "_cache" u. Ä. sind reserviert
        raise HTTPException(status_code=400, detail="Ungültiger Projektname")
    return safe


def _pat_project_dir(name: str) -> Path:
    d = PATENTE_DIR / _pat_safe_name(name)
    if not d.exists():
        raise HTTPException(status_code=404, detail="Projekt nicht gefunden")
    return d


def _pat_load(name: str) -> list:
    from tools import patente as _patente
    return _patente.load_project(_pat_project_dir(name) / "patente.json")


def _pat_meta(d: Path) -> dict:
    p = d / "meta.json"
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def _pat_save_meta(d: Path, meta: dict):
    (d / "meta.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")


async def _pat_rag_collection_for(name: str) -> dict:
    """Get-or-create die projektgebundene RAG-Collection (analog pst_to_rag)."""
    from tools.rag import tier_config
    d = _pat_project_dir(name)
    meta = _pat_meta(d)
    cid = meta.get("rag_collection_id")
    if cid:
        coll = await _db.rag_get_collection(cid)
        if coll:
            return coll
    tc = tier_config("regler")
    coll = {
        "id": f"rag_{uuid.uuid4().hex[:12]}", "name": f"Patente: {name}",
        "embed_model": EMBED_MODEL, "tier": "regler",
        "chunk_size": tc["chunk_size"], "chunk_overlap": tc["chunk_overlap"],
        "top_k": tc["top_k"], "embed_gpu": False, "clean": True,
        "char_limit": tc["char_limit"], "strictness": "ausgewogen",
        "created_at": time.time(),
    }
    await _db.rag_create_collection(coll)
    meta["rag_collection_id"] = coll["id"]
    _pat_save_meta(d, meta)
    return coll


async def _pat_index_patent(coll: dict, patent: dict):
    from tools.rag import ingest_file
    pid = patent.get("patent_id")
    if not pid:
        return
    _legal = "; ".join(f"{e.get('date','')} {e.get('desc') or e.get('code','')}".strip()
                       for e in (patent.get("legal_status") or [])[:8])
    text = (f"Titel: {patent.get('title','')}\n"
            f"Zusammenfassung: {patent.get('abstract','')}\n"
            f"Ansprüche: {(patent.get('claims') or '')[:8000]}\n"
            f"IPC-Klassen: {', '.join(patent.get('ipc_klassen') or [])}\n"
            f"CPC-Klassen: {', '.join(patent.get('cpc_klassen') or [])}\n"
            f"Rechteinhaber: {', '.join(patent.get('rechteinhaber') or [])}\n"
            f"Erfinder: {', '.join(patent.get('inventors') or [])}\n"
            f"Anmeldedatum: {patent.get('filing_date','')} · Priorität: {patent.get('priority_date','')} · "
            f"Publikation: {patent.get('publication_date','')}\n"
            + (f"Rechtsstand: {_legal}\n" if _legal else "")
            + (f"Patentfamilie: {', '.join((patent.get('family') or [])[:20])}" if patent.get("family") else ""))
    await _db.rag_delete_document(pid)
    try:
        await ingest_file(coll, text, patent.get("title") or pid, pid)
    except Exception:
        pass


async def _pat_index_analysis(coll: dict, doc_id: str, md_text: str, title: str):
    from tools.rag import ingest_file
    await _db.rag_delete_document(doc_id)
    try:
        await ingest_file(coll, md_text, title, doc_id)
    except Exception:
        pass


class PatOpsConfig(BaseModel):
    consumer_key: str = ""
    consumer_secret: str = ""


@router.get("/api/patente/ops-config")
async def patente_ops_config_get():
    """Status der EPO-OPS-Anbindung (Key nie im Klartext zurückgeben)."""
    creds = _pat_ops_creds()
    key = (creds or {}).get("consumer_key", "")
    return {"configured": bool(creds),
            "key_masked": (key[:4] + "…" + key[-4:]) if len(key) > 8 else ("•" * len(key))}


@router.post("/api/patente/ops-config")
async def patente_ops_config_set(body: PatOpsConfig):
    """Speichert die EPO-OPS-Zugangsdaten und prüft sie mit einem Test-Login.
    Leere Felder löschen die Konfiguration (→ zurück auf Google-Fallback)."""
    from tools import epo_ops
    key = (body.consumer_key or "").strip()
    secret = (body.consumer_secret or "").strip()
    if not key and not secret:
        EPO_OPS_FILE.unlink(missing_ok=True)
        return {"ok": True, "configured": False, "message": "EPO-OPS-Zugang entfernt — Google-Fallback aktiv."}
    if not key or not secret:
        raise HTTPException(status_code=400, detail="Consumer Key UND Secret angeben")
    creds = {"consumer_key": key, "consumer_secret": secret}
    try:
        async with httpx.AsyncClient() as client:
            await epo_ops.get_token(client, creds)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Test-Anmeldung fehlgeschlagen: {e}")
    EPO_OPS_FILE.write_text(json.dumps(creds, indent=2), encoding="utf-8")
    return {"ok": True, "configured": True, "message": "✓ EPO OPS verbunden — amtliche Daten aktiv."}


@router.get("/api/patente/projects")
async def patente_projects():
    if not PATENTE_DIR.exists():
        return {"projects": []}
    out = []
    for d in sorted(PATENTE_DIR.iterdir()):
        if not d.is_dir() or d.name.startswith("_"):   # _cache u. Ä. überspringen
            continue
        items = _pat_load(d.name) if (d / "patente.json").exists() else []
        meta = _pat_meta(d)
        out.append({"name": d.name, "count": len(items), "has_rag": bool(meta.get("rag_collection_id"))})
    return {"projects": out}


class PatProjectCreate(BaseModel):
    name: str
    project_id: str = ""   # optionale Verknüpfung mit einem Dach-Projekt (Orchestrator)


@router.post("/api/patente/projects")
async def patente_project_create(body: PatProjectCreate):
    safe = _pat_safe_name(body.name)
    d = PATENTE_DIR / safe
    d.mkdir(parents=True, exist_ok=True)
    if not (d / "patente.json").exists():
        (d / "patente.json").write_text("[]", encoding="utf-8")
    (d / "analysen").mkdir(exist_ok=True)
    if (body.project_id or "").strip():
        meta = _pat_meta(d)
        meta["project_id"] = body.project_id.strip()
        _pat_save_meta(d, meta)
    return {"name": safe}


@router.delete("/api/patente/projects/{name}")
async def patente_project_delete(name: str):
    import shutil
    d = _pat_project_dir(name)
    meta = _pat_meta(d)
    cid = meta.get("rag_collection_id")
    if cid:
        try:
            await _db.rag_delete_collection(cid)
        except Exception:
            pass
    shutil.rmtree(d, ignore_errors=True)
    return {"ok": True}


@router.get("/api/patente/projects/{name}")
async def patente_project_get(name: str):
    from tools import patente as _patente
    items = _pat_load(name)
    # Stärke-Kennzahlen zur Laufzeit anreichern (deterministisch, kein LLM,
    # nicht persistiert — rechnet sich bei jedem Laden frisch aus den Feldern)
    for p in items:
        try:
            p["kennzahlen"] = _patente.patent_kennzahlen(p)
        except Exception:
            p["kennzahlen"] = {}
    return {"patente": items}


class PatLookup(BaseModel):
    patent_id: str


@router.post("/api/patente/projects/{name}/import/lookup")
async def patente_import_lookup(name: str, body: PatLookup):
    from tools import patente as _patente
    d = _pat_project_dir(name)
    pid = str(body.patent_id or "").strip()
    if not pid:
        raise HTTPException(status_code=400, detail="Keine Patentnummer angegeben")
    async with httpx.AsyncClient() as client:
        details = await _patente.fetch_patent(client, pid, ops_creds=_pat_ops_creds(),
                                              cache_dir=PAT_CACHE_DIR)
    if "error" in details:
        raise HTTPException(status_code=502, detail=f"Abruf fehlgeschlagen: {details['error']}")
    items = _patente.save_project(d / "patente.json", [details])
    if await _local_llm_available():
        coll = await _pat_rag_collection_for(name)
        await _pat_index_patent(coll, details)
    return {"patent": details, "count": len(items)}


class PatSearch(BaseModel):
    term: str = ""
    assignee: str = ""
    country: str = ""
    max_results: int = 20
    ipc: str = ""          # IPC-/CPC-Klasse, z. B. "B60L" oder "H01M10/052"
    date_from: str = ""    # Publikationsdatum von (YYYY-MM-DD)
    date_to: str = ""      # Publikationsdatum bis (YYYY-MM-DD)


@router.post("/api/patente/search")
async def patente_search(body: PatSearch):
    from tools import patente as _patente
    async with httpx.AsyncClient() as client:
        results, fehler, quelle = await _patente.search_patents(
            client, body.term, body.assignee, body.country,
            max(1, min(int(body.max_results or 20), 50)),
            ipc=body.ipc, date_from=body.date_from, date_to=body.date_to,
            ops_creds=_pat_ops_creds(), cache_dir=PAT_CACHE_DIR)
    return {"results": results, "error": fehler, "source": quelle}


class PatPreview(BaseModel):
    patent_id: str


@router.post("/api/patente/preview")
async def patente_preview(body: PatPreview):
    """Volltext eines Patents (Abstract/Ansprüche/IPC/Zitate) scrapen, ohne es in
    eine Fallakte zu speichern — zum Lesen vor der Stapelverarbeitung."""
    from tools import patente as _patente
    pid = (body.patent_id or "").strip()
    if not pid:
        raise HTTPException(status_code=400, detail="Keine Patentnummer angegeben.")
    async with httpx.AsyncClient() as client:
        details = await _patente.fetch_patent(client, pid, ops_creds=_pat_ops_creds(),
                                              cache_dir=PAT_CACHE_DIR)
    if "error" in details:
        raise HTTPException(status_code=502, detail=f"Abruf fehlgeschlagen: {details['error']}")
    return details


class PatImportCsv(BaseModel):
    numbers: list[str]


@router.post("/api/patente/projects/{name}/import/csv")
async def patente_import_csv(name: str, body: PatImportCsv):
    from tools import patente as _patente
    d = _pat_project_dir(name)
    coll = await _pat_rag_collection_for(name) if await _local_llm_available() else None
    imported, failed = [], []
    _creds = _pat_ops_creds()
    async with httpx.AsyncClient() as client:
        for raw in body.numbers[:500]:
            n = str(raw).strip()
            if not n:
                continue
            details = await _patente.fetch_patent(client, n, ops_creds=_creds,
                                                  cache_dir=PAT_CACHE_DIR)
            if "error" in details:
                failed.append(n)
                continue
            _patente.save_project(d / "patente.json", [details])
            if coll:
                await _pat_index_patent(coll, details)
            imported.append(details["patent_id"])
    return {"imported": imported, "failed": failed}


class PatImportJson(BaseModel):
    items: list


@router.post("/api/patente/projects/{name}/import/json")
async def patente_import_json(name: str, body: PatImportJson):
    from tools import patente as _patente
    d = _pat_project_dir(name)
    valid = [it for it in body.items if isinstance(it, dict) and it.get("patent_id")]
    if not valid:
        raise HTTPException(status_code=400, detail="Keine gültigen Patent-Datensätze im Import")
    items = _patente.save_project(d / "patente.json", valid)
    if await _local_llm_available():
        coll = await _pat_rag_collection_for(name)
        for it in valid:
            await _pat_index_patent(coll, it)
    return {"imported": len(valid), "count": len(items)}


class PatImportCitations(BaseModel):
    patent_id: str


@router.post("/api/patente/projects/{name}/import/citations")
async def patente_import_citations(name: str, body: PatImportCitations):
    from tools import patente as _patente
    d = _pat_project_dir(name)
    bestand = _pat_load(name)
    quelle = next((p for p in bestand if p.get("patent_id") == body.patent_id), None)
    if not quelle:
        raise HTTPException(status_code=404, detail="Patent nicht in der Akte gefunden")
    vorhandene = {p.get("patent_id") for p in bestand}
    zu_laden = [z for z in (quelle.get("zitate") or []) if z not in vorhandene]
    coll = await _pat_rag_collection_for(name) if await _local_llm_available() else None
    neu, failed = [], []
    _creds = _pat_ops_creds()
    async with httpx.AsyncClient() as client:
        for n in zu_laden[:200]:
            details = await _patente.fetch_patent(client, n, ops_creds=_creds,
                                                  cache_dir=PAT_CACHE_DIR)
            if "error" in details:
                failed.append(n)
                continue
            _patente.save_project(d / "patente.json", [details])
            if coll:
                await _pat_index_patent(coll, details)
            neu.append(details)
    return {"imported": neu, "failed": failed}


@router.get("/api/patente/projects/{name}/export.json")
async def patente_export_json(name: str):
    items = _pat_load(name)
    data = json.dumps(items, indent=2, ensure_ascii=False)
    return Response(content=data, media_type="application/json",
                     headers={"Content-Disposition": f'attachment; filename="{_pat_safe_name(name)}_akte.json"'})


@router.get("/api/patente/projects/{name}/export.csv")
async def patente_export_csv(name: str):
    import csv
    import io
    items = _pat_load(name)
    buf = io.StringIO()
    fields = ["patent_id", "title", "ipc_klassen", "cpc_klassen", "rechteinhaber",
              "inventors", "filing_date", "priority_date", "publication_date",
              "family", "zitate", "zitiert_von", "source", "url", "scraped_at"]
    w = csv.DictWriter(buf, fieldnames=fields, extrasaction="ignore")
    w.writeheader()
    for it in items:
        row = dict(it)
        for k in ("ipc_klassen", "cpc_klassen", "rechteinhaber", "inventors", "family",
                  "zitate", "zitiert_von"):
            if isinstance(row.get(k), list):
                row[k] = ", ".join(row[k])
        w.writerow(row)
    return Response(content=buf.getvalue(), media_type="text/csv",
                     headers={"Content-Disposition": f'attachment; filename="{_pat_safe_name(name)}_akte.csv"'})


class PatAnalyze(BaseModel):
    patent_ids: list[str]
    model: Optional[str] = None
    neben_model: Optional[str] = None


@router.post("/api/patente/projects/{name}/analyze")
async def patente_analyze(name: str, body: PatAnalyze):
    return StreamingResponse(
        _patente_analyze_generator(name, body),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


async def _patente_analyze_generator(name: str, body: PatAnalyze):
    from tools import patente as _patente
    d = _pat_project_dir(name)
    bestand = _pat_load(name)
    ids = [i for i in (body.patent_ids or []) if i]
    idset = set(ids)
    gewaehlt = [p for p in bestand if p.get("patent_id") in idset]
    if not gewaehlt:
        yield _sse({"type": "error", "message": "Keine gültigen Patente ausgewählt"})
        return

    # Anspruchs-Volltexte für die Merkmalsanalyse (Anspruch 1 ungekürzt) — die
    # ersten beiden gewählten Dokumente (Einzel- oder Vergleichs-Claim-Chart).
    claims_texts = [(p["patent_id"], p.get("claims") or "") for p in gewaehlt[:2]
                    if (p.get("claims") or "").strip()]

    # Kontextbudget aus dem Profil-Kontextfenster ableiten (~3,5 Zeichen/Token,
    # 60 % für das Patentmaterial) — statt fixer 500-Zeichen-Kürzung.
    _budget = max(8000, int(_profile_num_ctx() * 3.5 * 0.6))

    if len(gewaehlt) == 1:
        p = gewaehlt[0]
        analyse_text = (f"Patent {p['patent_id']}: {p.get('title','')}\n"
                        f"{p.get('abstract','')}\n{p.get('claims','')}")[:_budget]
        analyse_typ = "Einzelnes_Dokument"
    elif len(gewaehlt) == 2:
        d1, d2 = gewaehlt
        _each = _budget // 2
        analyse_text = (
            (f"DOKUMENT 1 ({d1['patent_id']}): {d1.get('abstract','')}\nAnsprüche: {d1.get('claims','')}")[:_each]
            + "\n\n"
            + (f"DOKUMENT 2 ({d2['patent_id']}): {d2.get('abstract','')}\nAnsprüche: {d2.get('claims','')}")[:_each])
        analyse_typ = "Vergleich_zweier_Dokumente"
    else:
        # Mehrfachauswahl: Anspruch 1 VOLLSTÄNDIG (deterministisch extrahiert) statt
        # der früheren 500-Zeichen-Kürzung; Gesamtbudget proportional verteilt.
        _each = max(1500, _budget // len(gewaehlt))
        analyse_text = "\n\n".join(
            (f"Patent {p['patent_id']}: {p.get('abstract','')}\n"
             f"Anspruch 1: {_patente.extract_claim1(p.get('claims') or '')}")[:_each]
            for p in gewaehlt)
        analyse_typ = "Mehrfachauswahl"

    # Nächstliegender Stand der Technik aus der Projekt-Akte (RAG-Treffer) für
    # den Aufgabe-Lösungs-Ansatz — eigene (analysierte) Dokumente per Titel
    # ausgefiltert; leer ohne lokales LLM/Embeddings.
    sdt_kontext = ""
    try:
        if claims_texts and await _local_llm_available():
            cid = _pat_meta(d).get("rag_collection_id")
            if cid:
                from tools.rag import query_collections
                own_titles = {(p.get("title") or "").strip() for p in gewaehlt}
                q = _patente.extract_claim1(claims_texts[0][1])[:2000]
                hits = await query_collections([cid], q, top_k_cap=6)
                hits = [h for h in hits if (h.get("filename") or "").strip() not in own_titles][:3]
                if hits:
                    sdt_kontext = "\n\n".join(f"[{h['filename']}]\n{h['text']}" for h in hits)
    except Exception:
        sdt_kontext = ""

    # Deterministische Kennzahlen-Tabelle für den Moderator (Triage-Score)
    kennzahlen_text = _patente.kennzahlen_markdown(gewaehlt)

    # Patentrecherche zählt zur web-gestützten Recherche: Rolle „Wissenschaftlich
    # (Recherche)" — dort zugewiesene API-Modelle werden genutzt; nur der Profil-
    # Schalter „Web-Recherche lokal" biegt ein API-Modell auf ein lokales um.
    model, _m_err = await _research_model(body.model, _model_for("science"))
    if _m_err:
        # Läuft bereits als SSE-Stream: Fehler als Frame melden, nicht als
        # HTTPException (die käme hier nie beim Client an).
        yield _sse({"type": "error", "message": _m_err})
        return
    neben_model = _pick_model(body.neben_model, model) if body.neben_model else model
    if _research_local_only() and _llm.is_remote(neben_model) and not _llm.is_local(neben_model):
        neben_model = model
    tok = {"in": 0, "out": 0}
    queue: asyncio.Queue = asyncio.Queue()
    SENTINEL = object()

    async def _call(mdl: str, system: str, user: str) -> str:
        async with _model_session(mdl), httpx.AsyncClient(timeout=400) as client:
            resp = await _llm.chat(client, {
                "model": mdl, "think": False, "stream": False,
                "messages": [{"role": "system", "content": system},
                             {"role": "user", "content": user}],
                "options": {"num_ctx": _profile_num_ctx()}, "keep_alive": KEEP_ALIVE,
            })
            resp.raise_for_status()
            j = resp.json()
            a, b = _llm_tok(j)
            tok["in"] += a
            tok["out"] += b
            return j.get("message", {}).get("content", "") or ""

    async def chat_haupt(system: str, user: str) -> str:
        return await _call(model, system, user)

    async def chat_neben(system: str, user: str) -> str:
        return await _call(neben_model, system, user)

    def on_progress(msg: str):
        queue.put_nowait(msg)

    async def _run():
        try:
            erg = await _patente.run_pipeline(chat_haupt, chat_neben, analyse_text,
                                              on_progress=on_progress, claims_texts=claims_texts,
                                              sdt_kontext=sdt_kontext,
                                              kennzahlen_text=kennzahlen_text)
            queue.put_nowait(("__result__", erg))
        except Exception as e:
            queue.put_nowait(("__error__", str(e)))
        finally:
            queue.put_nowait(SENTINEL)

    task = asyncio.create_task(_run())
    ergebnisse = None
    error = None
    while True:
        item = await queue.get()
        if item is SENTINEL:
            break
        if isinstance(item, tuple) and item[0] == "__result__":
            ergebnisse = item[1]
        elif isinstance(item, tuple) and item[0] == "__error__":
            error = item[1]
        else:
            yield _sse({"type": "progress", "message": item})
    await task

    if error or ergebnisse is None:
        yield _sse({"type": "error", "message": f"Pipeline-Fehler: {error or 'unbekannt'}"})
        return

    base = _patente.save_analysis(d / "analysen", analyse_typ, ids, ergebnisse)
    if await _local_llm_available():
        coll = await _pat_rag_collection_for(name)
        md_path = d / "analysen" / f"{base}.md"
        md_text = md_path.read_text(encoding="utf-8") if md_path.exists() else ""
        await _pat_index_analysis(coll, f"analyse_{base}", md_text, f"Analyse {base}")

    yield _sse({"type": "done", "ergebnisse": ergebnisse, "datei_name": f"{base}.json", "tokens": tok})


class PatFto(BaseModel):
    patent_ids: list[str]
    produkt: str
    model: Optional[str] = None
    neben_model: Optional[str] = None


@router.post("/api/patente/projects/{name}/fto")
async def patente_fto(name: str, body: PatFto):
    return StreamingResponse(
        _patente_fto_generator(name, body),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


async def _patente_fto_generator(name: str, body: PatFto):
    """FTO-Produkt-Check: Claim-Chart Anspruch 1 ↔ Produktbeschreibung je
    gewähltem Patent (All-Elements-Rule, Prüfschleife), Ergebnis als gespeicherte
    Analyse vom Typ FTO_Check. Keine Rechtsberatung (Hinweis im Fazit)."""
    from tools import patente as _patente
    d = _pat_project_dir(name)
    bestand = _pat_load(name)
    ids = [i for i in (body.patent_ids or []) if i]
    idset = set(ids)
    gewaehlt = [p for p in bestand if p.get("patent_id") in idset]
    produkt = (body.produkt or "").strip()
    if not gewaehlt:
        yield _sse({"type": "error", "message": "Keine gültigen Patente ausgewählt"})
        return
    if len(produkt) < 30:
        yield _sse({"type": "error", "message": "Bitte das eigene Produkt/die Idee ausführlicher beschreiben (mind. ein paar Sätze)."})
        return

    patents = [(p["patent_id"], p.get("claims") or "") for p in gewaehlt
               if (p.get("claims") or "").strip()]
    if not patents:
        yield _sse({"type": "error", "message": "Für die Auswahl liegen keine Anspruchstexte vor."})
        return

    # Produktbeschreibung ans Kontextbudget anpassen (wie analyze)
    produkt = produkt[:max(4000, int(_profile_num_ctx() * 3.5 * 0.3))]

    model, _m_err = await _research_model(body.model, _model_for("science"))
    if _m_err:
        yield _sse({"type": "error", "message": _m_err})
        return
    neben_model = _pick_model(body.neben_model, model) if body.neben_model else model
    if _research_local_only() and _llm.is_remote(neben_model) and not _llm.is_local(neben_model):
        neben_model = model
    tok = {"in": 0, "out": 0}
    queue: asyncio.Queue = asyncio.Queue()
    SENTINEL = object()

    async def _call(mdl: str, system: str, user: str) -> str:
        async with _model_session(mdl), httpx.AsyncClient(timeout=400) as client:
            resp = await _llm.chat(client, {
                "model": mdl, "think": False, "stream": False,
                "messages": [{"role": "system", "content": system},
                             {"role": "user", "content": user}],
                "options": {"num_ctx": _profile_num_ctx()}, "keep_alive": KEEP_ALIVE,
            })
            resp.raise_for_status()
            j = resp.json()
            a, b = _llm_tok(j)
            tok["in"] += a
            tok["out"] += b
            return j.get("message", {}).get("content", "") or ""

    async def chat_haupt(system: str, user: str) -> str:
        return await _call(model, system, user)

    async def chat_neben(system: str, user: str) -> str:
        return await _call(neben_model, system, user)

    def on_progress(msg: str):
        queue.put_nowait(msg)

    async def _run():
        try:
            erg = await _patente.run_fto_check(chat_haupt, chat_neben, produkt,
                                               patents, on_progress=on_progress)
            queue.put_nowait(("__result__", erg))
        except Exception as e:
            queue.put_nowait(("__error__", str(e)))
        finally:
            queue.put_nowait(SENTINEL)

    task = asyncio.create_task(_run())
    ergebnisse = None
    error = None
    while True:
        item = await queue.get()
        if item is SENTINEL:
            break
        if isinstance(item, tuple) and item[0] == "__result__":
            ergebnisse = item[1]
        elif isinstance(item, tuple) and item[0] == "__error__":
            error = item[1]
        else:
            yield _sse({"type": "progress", "message": item})
    await task

    if error or not ergebnisse:
        yield _sse({"type": "error", "message": f"FTO-Check fehlgeschlagen: {error or 'kein Ergebnis'}"})
        return

    # Produktbeschreibung mit ins Ergebnis (Nachvollziehbarkeit im „Gespeichert"-Tab)
    ergebnisse["produktbeschreibung"] = produkt
    base = _patente.save_analysis(d / "analysen", "FTO_Check", ids, ergebnisse)
    if await _local_llm_available():
        coll = await _pat_rag_collection_for(name)
        md_path = d / "analysen" / f"{base}.md"
        md_text = md_path.read_text(encoding="utf-8") if md_path.exists() else ""
        await _pat_index_analysis(coll, f"analyse_{base}", md_text, f"FTO-Check {base}")

    yield _sse({"type": "done", "ergebnisse": ergebnisse, "datei_name": f"{base}.json", "tokens": tok})


@router.get("/api/patente/projects/{name}/analyses")
async def patente_analyses(name: str):
    from tools import patente as _patente
    d = _pat_project_dir(name)
    return {"analysen": _patente.load_analyses(d / "analysen")}


@router.get("/api/patente/projects/{name}/analyses/{file_name}")
async def patente_analysis_get(name: str, file_name: str):
    d = _pat_project_dir(name)
    p = d / "analysen" / Path(file_name).name
    if not p.exists():
        raise HTTPException(status_code=404, detail="Analyse nicht gefunden")
    return json.loads(p.read_text(encoding="utf-8"))


@router.get("/api/patente/projects/{name}/analyses/{file_name}/markdown")
async def patente_analysis_markdown(name: str, file_name: str):
    d = _pat_project_dir(name)
    safe = Path(file_name).name
    p = (d / "analysen" / safe).with_suffix(".md")
    if not p.exists():
        raise HTTPException(status_code=404, detail="Markdown nicht gefunden")
    return Response(content=p.read_text(encoding="utf-8"), media_type="text/markdown",
                     headers={"Content-Disposition": f'attachment; filename="{p.name}"'})


@router.delete("/api/patente/projects/{name}/analyses/{file_name}")
async def patente_analysis_delete(name: str, file_name: str):
    from tools import patente as _patente
    d = _pat_project_dir(name)
    ok = _patente.delete_analysis(d / "analysen", file_name)
    if not ok:
        raise HTTPException(status_code=404, detail="Analyse nicht gefunden")
    return {"ok": True}


class PatAsk(BaseModel):
    question: str
    model: Optional[str] = None


@router.post("/api/patente/projects/{name}/ask")
async def patente_ask(name: str, body: PatAsk):
    from tools.rag import query_collections
    d = _pat_project_dir(name)
    meta = _pat_meta(d)
    cid = meta.get("rag_collection_id")
    question = (body.question or "").strip()
    if not question:
        raise HTTPException(status_code=400, detail="Keine Frage angegeben")
    if not cid:
        raise HTTPException(status_code=400, detail="Noch keine Dokumente indiziert – zuerst Patente importieren")
    hits = await query_collections([cid], question, top_k_cap=8)
    if not hits:
        return {"answer": "Keine relevanten Textstellen in der Akte gefunden.", "sources": []}
    context = "\n\n---\n\n".join(f"[{h.get('filename','')}]\n{h.get('text','')}" for h in hits)
    model = _pick_model(body.model, _model_for("science"))
    async with _model_session(model), httpx.AsyncClient(timeout=180) as client:
        resp = await _llm.chat(client, {
            "model": model, "think": False, "stream": False,
            "messages": [
                {"role": "system", "content": "Beantworte die Frage sachlich auf Basis des Kontextes. Verweise auf Dokumentnummern/-titel."},
                {"role": "user", "content": f"KONTEXT:\n{context}\n\nFRAGE: {question}"},
            ],
            "options": {"num_ctx": _profile_num_ctx()}, "keep_alive": KEEP_ALIVE,
        })
        resp.raise_for_status()
        j = resp.json()
        tin, tout = _llm_tok(j)
        answer = j.get("message", {}).get("content", "") or ""
    return {"answer": answer,
            "sources": [{"filename": h.get("filename", ""), "score": h.get("score")} for h in hits],
            "tokens": {"in": tin, "out": tout}}


class PatGraph(BaseModel):
    show_ipc: bool = True
    show_assignee: bool = True
    show_citations: bool = True
    focus_assignee: Optional[str] = None


@router.post("/api/patente/projects/{name}/graph")
async def patente_graph(name: str, body: PatGraph):
    from tools import patente as _patente
    items = _pat_load(name)
    nodes, edges = _patente.build_graph_data(
        items, body.show_ipc, body.show_assignee, body.show_citations, body.focus_assignee)
    return {"nodes": nodes, "edges": edges}


class PatMigrate(BaseModel):
    source_dir: str


@router.post("/api/patente/migrate")
async def patente_migrate(body: PatMigrate):
    return StreamingResponse(
        _patente_migrate_generator(body),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


async def _patente_migrate_generator(body: PatMigrate):
    from tools import patente as _patente
    src = Path(str(body.source_dir or "").strip()).expanduser()
    if not src.exists():
        yield _sse({"type": "error", "message": f"Quellverzeichnis nicht gefunden: {src}"})
        return
    PATENTE_DIR.mkdir(parents=True, exist_ok=True)
    migrated, skipped = _patente.migrate_legacy_projects(src, PATENTE_DIR)
    yield _sse({"type": "copied", "migrated": migrated, "skipped": skipped})

    local_ok = await _local_llm_available()
    for proj in migrated:
        items = _pat_load(proj)
        yield _sse({"type": "project_start", "project": proj, "count": len(items)})
        if not local_ok:
            continue
        try:
            coll = await _pat_rag_collection_for(proj)
            for i, p in enumerate(items):
                await _pat_index_patent(coll, p)
                if i % 25 == 0:
                    yield _sse({"type": "progress", "project": proj, "indexed": i + 1, "total": len(items)})
            for a in _patente.load_analyses(PATENTE_DIR / proj / "analysen"):
                fname = a.get("datei_name", "")
                base = fname[:-5] if fname.endswith(".json") else fname
                md_path = PATENTE_DIR / proj / "analysen" / f"{base}.md"
                if md_path.exists():
                    await _pat_index_analysis(
                        coll, f"analyse_{base}", md_path.read_text(encoding="utf-8"), f"Analyse {base}")
            yield _sse({"type": "project_done", "project": proj})
        except Exception as e:
            yield _sse({"type": "project_error", "project": proj, "message": str(e)})

    yield _sse({"type": "done", "migrated": migrated, "skipped": skipped})








