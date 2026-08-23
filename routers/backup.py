"""Router: Backup / Restore (/api/backup, /api/restore)

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


# ── Backup / Restore ─────────────────────────────────────────────────────────

from datetime import datetime as _dt


# ── Sicherung: welche Daten gehören in ein Backup ───────────────────────────
# Bewusst als Listen statt verstreuter Einzelzeilen: Beim Hinzufügen eines neuen
# Tabs muss genau hier ein Eintrag ergänzt werden, sonst fehlt er im Backup.
# (Genau das war der Grund, warum das Backup zwischenzeitlich unvollständig war.)

# Immer enthalten – klein und textbasiert:
def _backup_dirs_always() -> list:
    return [
        (ANGEBOTE_DIR, "angebote"), (RECHNUNGEN_DIR, "rechnungen"),
        (ZEUGNISSE_DIR, "zeugnisse"), (PATENTE_DIR, "patente"),
        (RFQ_DIR, "rfq"), (MORPH_TRAIN_DIR, "morph_training"),
        (VARIANTEN_DIR, "varianten"), (COMPARE_DIR, "compare"), (TODO_DIR, "todo"),
        (TODO_ATT_DIR, "todo_att"),   # To-Do-Anlagen (Original-Dateien; MD-Text + Baum sind in der DB)
        (RAG_IMAGES_DIR, "rag_images"),   # Bild-aware RAG: Originalbilder (Beschreibung + Vektoren sind in der DB)
    ]


def _backup_files_always() -> list:
    return [
        (FIRMENPROFIL_FILE, "firmenprofil.json"),
        (MAIL_CONFIG_FILE, "mail.json"),
        (MAIL_RULES_FILE, "mail_rules.json"),
        (FEEDBACK_FILE, "feedback.md"),
    ]


# Optional – können groß werden:
def _backup_dirs_bulk() -> list:
    return [(UPLOADS_DIR, "uploads"), (REPORTS_DIR, "reports"),
            (DOSSIERS_DIR, "dossiers"),
            (TRANSCRIPTS_DIR, "transcripts"),   # Audiodateien der Transkription (Sprach-EINGABE)
            (DATA_DIR / "todo_backups", "todo_backups")]   # Reset-Sicherungen der To-Do-Liste


def _zip_tree(zf, base: Path, prefix: str) -> int:
    """Legt einen kompletten Verzeichnisbaum ins ZIP (rekursiv, relative Pfade).

    Rekursiv, weil z. B. ``patente/`` und ``pst/`` je Projekt bzw. Postfach
    Unterordner anlegen. Gibt die Anzahl geschriebener Dateien zurück."""
    if not base.exists():
        return 0
    n = 0
    for fp in sorted(base.rglob("*")):
        if fp.is_file():
            zf.write(fp, f"{prefix}/{fp.relative_to(base).as_posix()}")
            n += 1
    return n


@router.get("/api/backup")
async def create_backup(uploads: bool = False, pst: bool = False,
                        secrets: bool = False):
    """Exportiert die Nutzerdaten als ZIP-Archiv.

    Immer enthalten: Profil, Projekte, Gespräche, Pläne, Agenten, Jurys, Code,
    RAG (inkl. Embeddings), Ressourcenlisten, Branding sowie die Geschäftsdaten
    (Angebote, Rechnungen, Zeugnisse, Patente, Anfragen, Morph-Kasten,
    Firmenprofil, Mail-Konfiguration, Feedback).

    Zuschaltbar, weil groß bzw. vertraulich:
    - ``uploads``  hochgeladene Dateien, Berichte, Dossiers
    - ``pst``      eingelesene Postfächer samt Anhängen (kann sehr groß werden)
    - ``secrets``  API-Zugangsdaten im Klartext (nur für einen Rechnerumzug)
    """
    import io, zipfile

    buf = io.BytesIO()
    today = _dt.now().strftime("%Y-%m-%d")

    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:

        # Profil
        if PROFILE_FILE.exists():
            zf.write(PROFILE_FILE, "profile.json")

        # Projekte
        if PROJECTS_FILE.exists():
            zf.write(PROJECTS_FILE, "projects.json")

        # Ressourcen-/Kapazitätslisten
        if CAP_LISTS_FILE.exists():
            zf.write(CAP_LISTS_FILE, "capacity_lists.json")
        elif CAPACITY_FILE.exists():
            zf.write(CAPACITY_FILE, "capacity.json")

        # Gespräche
        convs = await _db.list_conversations(limit=9999)
        for c in convs:
            data = await _db.get_conversation(c["id"])
            if data:
                slug = _to_slug(data.get("title", c["id"]))
                fname = f"conversations/{slug}_{c['id'][:8]}.json"
                zf.writestr(fname, json.dumps(data, ensure_ascii=False, indent=2))

        # Pläne
        for fp in sorted(PLANS_DIR.glob("*.json")):
            zf.write(fp, f"plans/{fp.name}")

        # Agenten
        for fp in sorted(AGENTS_DIR.glob("*.json")):
            zf.write(fp, f"agents/{fp.name}")

        # Jurys (Bewertungs-Gremien)
        for fp in sorted(JURIES_DIR.glob("*.json")):
            zf.write(fp, f"juries/{fp.name}")

        # Code-Programme (IDE)
        for fp in sorted(CODE_DIR.glob("*.json")):
            zf.write(fp, f"code/{fp.name}")

        # Jury-Dokumente (Werkbank im Jury-Tab)
        for fp in sorted(JURY_DOCS_DIR.glob("*.json")):
            zf.write(fp, f"jury_docs/{fp.name}")

        # Branding-Assets (Logo, Vorlagen-Deckblatt, Vorlagen-Kopfzeile)
        for fp in sorted(PROFILE_ASSETS_DIR.glob("*")):
            if fp.is_file():
                zf.write(fp, f"profile_assets/{fp.name}")

        # RAG-Wissensdatenbanken inkl. Embeddings (base64-kodiert)
        try:
            import base64 as _b64
            rag_dump = await _db.rag_export()
            for entry in rag_dump:
                for d in entry["documents"]:
                    for ch in d["chunks"]:
                        ch["embedding"] = _b64.b64encode(ch["embedding"]).decode("ascii")
            if rag_dump:
                zf.writestr("rag/collections.json", json.dumps(rag_dump, ensure_ascii=False))
        except Exception:
            pass

        # To-Do-Projektbaum inkl. Punkte/Kanten/Anlagen-Markdown (aus der DB)
        try:
            todo_dump = await _db.todo_export()
            if todo_dump.get("projects"):
                zf.writestr("todo/todos.json", json.dumps(todo_dump, ensure_ascii=False))
        except Exception:
            pass

        # Geschäftsdaten und übrige Einzeldateien (immer)
        for _dir, _prefix in _backup_dirs_always():
            _zip_tree(zf, _dir, _prefix)
        for _fp, _name in _backup_files_always():
            if _fp.exists():
                zf.write(_fp, _name)

        # Zuschaltbar: Uploads/Berichte/Dossiers
        if uploads:
            for _dir, _prefix in _backup_dirs_bulk():
                _zip_tree(zf, _dir, _prefix)

        # Zuschaltbar: Postfach-Archive (private Korrespondenz, oft sehr groß)
        if pst:
            _zip_tree(zf, PST_DIR, "pst")

        # Zuschaltbar: API-Zugangsdaten. Standardmäßig NICHT enthalten, da die
        # Schlüssel sonst im Klartext in einer weitergebbaren Datei landen.
        if secrets and API_PROVIDERS_FILE.exists():
            zf.write(API_PROVIDERS_FILE, "api_providers.json")
        if secrets and EPO_OPS_FILE.exists():
            zf.write(EPO_OPS_FILE, "epo_ops.json")

        # Kennzeichnung des Archivinhalts – der Restore und der Nutzer sehen so,
        # was drin ist (und was bewusst fehlt).
        zf.writestr("backup_info.json", json.dumps({
            "created": _dt.now().isoformat(timespec="seconds"),
            "app_version": APP_VERSION,
            "includes_uploads": bool(uploads),
            "includes_pst": bool(pst),
            "includes_secrets": bool(secrets),
        }, ensure_ascii=False, indent=2))

    buf.seek(0)
    filename = f"ai_framework_thomas_backup_{today}.zip"
    from fastapi.responses import StreamingResponse as _SR
    return _SR(
        buf,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/api/restore")
async def restore_backup(file: UploadFile = File(...), replace: bool = False):
    """Importiert alle Nutzerdaten aus einem ZIP-Backup.

    ``replace=False`` (Standard) führt zusammen: Vorhandenes bleibt unangetastet,
    nur Fehlendes wird ergänzt. ``replace=True`` überschreibt gleichnamige
    Dateien mit dem Stand aus dem Archiv."""
    import io, zipfile

    content = await file.read()
    stats = {"conversations": 0, "plans": 0, "agents": 0, "juries": 0,
             "profile": False, "projects": False,
             "profile_assets": 0, "rag_collections": 0, "errors": []}

    try:
        with zipfile.ZipFile(io.BytesIO(content)) as zf:
            names = zf.namelist()

            # Profil
            if "profile.json" in names:
                try:
                    data = json.loads(zf.read("profile.json").decode("utf-8"))
                    PROFILE_FILE.write_text(
                        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
                    )
                    stats["profile"] = True
                except Exception as e:
                    stats["errors"].append(f"profile.json: {e}")

            # Projekte
            if "projects.json" in names:
                try:
                    data = json.loads(zf.read("projects.json").decode("utf-8"))
                    # Bestehende Projekte mit importierten zusammenführen
                    existing = _load_projects()
                    existing_ids = {p["id"] for p in existing}
                    for p in data:
                        if p.get("id") not in existing_ids:
                            existing.append(p)
                    PROJECTS_FILE.write_text(
                        json.dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8"
                    )
                    stats["projects"] = True
                except Exception as e:
                    stats["errors"].append(f"projects.json: {e}")

            # Ressourcen-/Kapazitätslisten
            if "capacity_lists.json" in names:
                try:
                    CAP_LISTS_FILE.write_bytes(zf.read("capacity_lists.json"))
                    stats["capacity_lists"] = True
                except Exception as e:
                    stats["errors"].append(f"capacity_lists.json: {e}")
            elif "capacity.json" in names:
                try:
                    CAPACITY_FILE.write_bytes(zf.read("capacity.json"))
                    stats["capacity"] = True
                except Exception as e:
                    stats["errors"].append(f"capacity.json: {e}")

            # Gespräche
            for name in names:
                if not name.startswith("conversations/") or not name.endswith(".json"):
                    continue
                try:
                    data = json.loads(zf.read(name).decode("utf-8"))
                    conv_id = f"restore_{uuid.uuid4().hex[:10]}"
                    msgs = data.get("messages", [])
                    await _db.save_conversation(
                        conv_id, msgs,
                        model=data.get("model"),
                        agent_id=data.get("agent_id"),
                        canvas_json=data.get("canvas_json"),
                    )
                    if data.get("project_id"):
                        await _db.set_project(conv_id, data["project_id"])
                    stats["conversations"] += 1
                except Exception as e:
                    stats["errors"].append(f"{name}: {e}")

            # Pläne (überspringt wenn Name+Größe bereits identisch)
            existing_plan_names = {
                json.loads(fp.read_text(encoding="utf-8")).get("name", "")
                for fp in PLANS_DIR.glob("*.json")
                if fp.exists()
            }
            for name in names:
                if not name.startswith("plans/") or not name.endswith(".json"):
                    continue
                try:
                    data = json.loads(zf.read(name).decode("utf-8"))
                    plan_name = data.get("name", "")
                    if plan_name and plan_name in existing_plan_names:
                        continue  # bereits vorhanden → überspringen
                    new_id = uuid.uuid4().hex[:12]
                    data["id"] = new_id
                    data["updated_at"] = time.time()
                    dest = _plan_path(new_id, plan_name)
                    dest.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
                    stats["plans"] += 1
                    existing_plan_names.add(plan_name)
                except Exception as e:
                    stats["errors"].append(f"{name}: {e}")

            # Agenten (überspringt wenn ID bereits existiert)
            for name in names:
                if not name.startswith("agents/") or not name.endswith(".json"):
                    continue
                try:
                    data = json.loads(zf.read(name).decode("utf-8"))
                    agent_name = data.get("name", "")
                    agent_id = data.get("id") or _to_slug(agent_name) + "_" + uuid.uuid4().hex[:4]
                    data["id"] = agent_id
                    existing_fp = _agent_path_by_id(agent_id)
                    if existing_fp and existing_fp.exists():
                        continue
                    dest = _unique_agent_path(agent_name or agent_id, exclude_id=agent_id)
                    dest.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
                    stats["agents"] += 1
                except Exception as e:
                    stats["errors"].append(f"{name}: {e}")

            # Jurys (überspringt wenn ID bereits existiert)
            for name in names:
                if not name.startswith("juries/") or not name.endswith(".json"):
                    continue
                try:
                    data = json.loads(zf.read(name).decode("utf-8"))
                    jid = data.get("id") or _to_slug(data.get("name", "jury")) + "_" + uuid.uuid4().hex[:6]
                    data["id"] = jid
                    if _jury_path_by_id(jid):
                        continue  # bereits vorhanden
                    dest = JURIES_DIR / f"{_to_slug(data.get('name', 'jury'))}_{jid[-6:]}.json"
                    dest.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
                    stats["juries"] += 1
                except Exception as e:
                    stats["errors"].append(f"{name}: {e}")

            # Code-Programme (überspringt wenn ID bereits existiert)
            for name in names:
                if not name.startswith("code/") or not name.endswith(".json"):
                    continue
                try:
                    data = json.loads(zf.read(name).decode("utf-8"))
                    prog_id = data.get("id", "")
                    if prog_id and _code_path_by_id(prog_id):
                        continue  # bereits vorhanden
                    if not prog_id:
                        prog_id = _to_slug(data.get("name", "prog")) + "_" + uuid.uuid4().hex[:6]
                        data["id"] = prog_id
                    fp = CODE_DIR / f"{_to_slug(data.get('name','prog'))}_{prog_id[-6:]}.json"
                    fp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
                    stats.setdefault("code", 0)
                    stats["code"] += 1
                except Exception as e:
                    stats["errors"].append(f"{name}: {e}")

            # Jury-Dokumente (überspringt bereits vorhandene per id)
            for name in names:
                if not name.startswith("jury_docs/") or not name.endswith(".json"):
                    continue
                try:
                    data = json.loads(zf.read(name).decode("utf-8"))
                    doc_id = data.get("id", "")
                    if doc_id and _jury_doc_path_by_id(doc_id):
                        continue
                    if not doc_id:
                        doc_id = _to_slug(data.get("name", "doc")) + "_" + uuid.uuid4().hex[:6]
                        data["id"] = doc_id
                    fp = JURY_DOCS_DIR / f"{_to_slug(data.get('name','doc'))}_{doc_id[-6:]}.json"
                    fp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
                    stats.setdefault("jury_docs", 0)
                    stats["jury_docs"] += 1
                except Exception as e:
                    stats["errors"].append(f"{name}: {e}")

            # Branding-Assets (Logo, Deckblatt, Kopfzeile) – überschreiben das Vorhandene
            for name in names:
                if not name.startswith("profile_assets/") or name.endswith("/"):
                    continue
                try:
                    asset_name = Path(name).name
                    if not asset_name:
                        continue
                    (PROFILE_ASSETS_DIR / asset_name).write_bytes(zf.read(name))
                    stats["profile_assets"] += 1
                except Exception as e:
                    stats["errors"].append(f"{name}: {e}")

            # RAG-Wissensdatenbanken (überspringt bereits vorhandene Sammlungen)
            if "rag/collections.json" in names:
                try:
                    import base64 as _b64
                    rag_dump = json.loads(zf.read("rag/collections.json").decode("utf-8"))
                    for entry in rag_dump:
                        coll = entry.get("collection", {})
                        cid = coll.get("id")
                        if not cid or await _db.rag_collection_exists(cid):
                            continue
                        for d in entry.get("documents", []):
                            for ch in d.get("chunks", []):
                                ch["embedding"] = _b64.b64decode(ch["embedding"])
                        await _db.rag_import_collection(coll, entry.get("documents", []))
                        stats["rag_collections"] += 1
                except Exception as e:
                    stats["errors"].append(f"rag/collections.json: {e}")

            # To-Do-Projektbaum (aus der DB exportiert)
            if "todo/todos.json" in names:
                try:
                    todo_dump = json.loads(zf.read("todo/todos.json").decode("utf-8"))
                    await _db.todo_import(todo_dump)
                    stats["todo_projects"] = len(todo_dump.get("projects", []))
                except Exception as e:
                    stats["errors"].append(f"todo/todos.json: {e}")

            # ── Geschäftsdaten, Uploads, Postfach, Zugangsdaten ──────────────
            # Dateibasierte Bereiche generisch zurückspielen. ``replace=False``
            # (Standard) lässt vorhandene Dateien unangetastet und ergänzt nur
            # Fehlendes — so kann ein Backup gefahrlos in eine bereits genutzte
            # Installation eingespielt werden.
            _targets = (_backup_dirs_always() + _backup_dirs_bulk()
                        + [(PST_DIR, "pst")])
            for _dir, _prefix in _targets:
                cnt = 0
                for name in names:
                    if not name.startswith(f"{_prefix}/") or name.endswith("/"):
                        continue
                    # _safe_relpath verwirft ".." und führende Slashes → kein
                    # Ausbrechen aus dem Zielordner durch manipulierte Archive.
                    rel = _safe_relpath(name[len(_prefix) + 1:])
                    if not rel:
                        continue
                    dest = _dir / rel
                    try:
                        if dest.exists() and not replace:
                            continue
                        dest.parent.mkdir(parents=True, exist_ok=True)
                        dest.write_bytes(zf.read(name))
                        cnt += 1
                    except Exception as e:
                        stats["errors"].append(f"{name}: {e}")
                if cnt:
                    stats[_prefix] = cnt

            # Einzeldateien (Firmenprofil, Mail-Konfiguration, Feedback)
            for _fp, _name in _backup_files_always():
                if _name in names and (replace or not _fp.exists()):
                    try:
                        _fp.parent.mkdir(parents=True, exist_ok=True)
                        _fp.write_bytes(zf.read(_name))
                        stats[_name] = True
                    except Exception as e:
                        stats["errors"].append(f"{_name}: {e}")

            # API-Zugangsdaten nur, wenn im Archiv vorhanden. Bewusst immer
            # überschreibend: wer sie mitsichert, will sie beim Umzug auch haben.
            if "api_providers.json" in names:
                try:
                    API_PROVIDERS_FILE.write_bytes(zf.read("api_providers.json"))
                    stats["api_providers"] = True
                except Exception as e:
                    stats["errors"].append(f"api_providers.json: {e}")
            if "epo_ops.json" in names:
                try:
                    EPO_OPS_FILE.write_bytes(zf.read("epo_ops.json"))
                    stats["epo_ops"] = True
                except Exception as e:
                    stats["errors"].append(f"epo_ops.json: {e}")

    except zipfile.BadZipFile:
        raise HTTPException(400, "Ungültige ZIP-Datei")

    return stats


