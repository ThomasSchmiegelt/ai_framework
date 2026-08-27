"""
AI_Framework_Thomas — ChatGPT-ähnliches Interface für Ollama
FastAPI-Backend mit agentic Tool-Loop und SSE-Streaming
"""

import asyncio
import base64
import io
import json
import os
import re
import sys
import time
import uuid
from datetime import date, datetime
from pathlib import Path
from typing import List, Optional

import aiofiles
import httpx
from fastapi import FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response, StreamingResponse

import db as _db
from tools import llm as _llm
from tools import transcribe as _transcribe
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from core import *  # geteilte Kernfläche (Konfig/Pfade/Modelle/Profil/SSE/LLM)
import core as _core  # noqa: F401  (Zugriff auf geteilte Namen falls nötig)

# ── Feature-Router (ausgelagerte Endpunkte) ──────────────────────────────────
# Jedes Modul in routers/ trägt einen APIRouter; hier importiert, unten via
# app.include_router(...) VOR dem StaticFiles-Mount eingehängt.
# ROUTER-IMPORTS  (Marker – nicht entfernen)
from routers import chat as _r_chat
from routers import presentation as _r_presentation
from routers import workflow as _r_workflow
from routers import tts as _r_tts
from routers import music as _r_music
from routers import image as _r_image
from routers import upload as _r_upload
from routers import research as _r_research
from routers import providers as _r_providers
from routers import rfq as _r_rfq
from routers import agents as _r_agents
from routers import matrix as _r_matrix
from routers import rag as _r_rag
from routers import jury as _r_jury
from routers import pst as _r_pst
from routers import patente as _r_patente
from routers import plans as _r_plans
from routers import projects as _r_projects
from routers import profile as _r_profile
from routers import backup as _r_backup
from routers import help as _r_help
from routers import code as _r_code
from routers import medizin as _r_medizin
from routers import mathe as _r_mathe
from routers import todo as _r_todo
from routers import conversations as _r_conversations
from routers import mail as _r_mail
from routers import dir_analysis as _r_dir_analysis
from routers import morph as _r_morph
from routers import setup as _r_setup
from routers import jury_docs as _r_jury_docs
from routers import transcribe as _r_transcribe
from routers import capacity as _r_capacity
from routers import feedback as _r_feedback
from routers import logs as _r_logs
from routers import export as _r_export
from routers import dokumente as _r_dokumente
from routers import varianten as _r_varianten
from routers import compare as _r_compare
from routers import pairing as _r_pairing
from routers import orchestrator as _r_orchestrator
from routers import goal as _r_goal


app = FastAPI(title="AI_Framework_Thomas")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


async def _seed_todo_demo() -> None:
    """Großen Demo-To-Do-Baum einspielen (Vorführung „vernetzte Informationen").
    Quelle: ``defaults/todo_demo.json`` (Form von ``db.todo_export`` + optional ``version``).
    Nur wenn Config-Flag ``seed_todo_demo`` gesetzt ist. Der Marker ``data/todo/.demo_seeded``
    speichert die zuletzt eingespielte **Version** — ändert sich diese (neue Demo), wird
    automatisch neu eingespielt. Dabei werden ALLE ``tp_demo_*``-Projekte zuerst gelöscht
    (saubere Ersetzung, Kanten haben keine stabile ID). Eigene Projekte bleiben unberührt.
    Erneut laden erzwingen: Marker löschen."""
    if not bool(_CONFIG.get("seed_todo_demo", False)):
        return
    marker = TODO_DIR / ".demo_seeded"
    fp = DEFAULTS_DIR / "todo_demo.json"
    if not fp.exists():
        return
    try:
        dump = json.loads(fp.read_text(encoding="utf-8"))
        version = str(dump.get("version", "1"))
        if marker.exists() and marker.read_text(encoding="utf-8").strip() == version:
            return   # bereits in dieser Version eingespielt
        await _db.todo_root_ensure(_todo_root_name())
        # ALLE vorhandenen Demo-Projekte entfernen (Kaskade) — auch solche mit alten IDs,
        # damit die neue Version sauber ersetzt und keine Kanten doppelt entstehen.
        for p in await _db.todo_projects_all():
            pid = p.get("id", "")
            if pid.startswith("tp_demo_"):
                await _db.todo_project_delete(pid)
        await _db.todo_import(dump)
        TODO_DIR.mkdir(parents=True, exist_ok=True)
        marker.write_text(version, encoding="utf-8")
        n = len(dump.get("items", []))
        print(f"[DB] Demo-To-Do-Projekt eingespielt (v{version}, {n} Punkte) -> {fp.name}")
    except Exception as e:
        print("[DB] Demo-To-Do-Seed übersprungen: " + str(e))


def _seed_orchestrator_example() -> None:
    """Mitgelieferten Beispiel-Vorgang einspielen (kompletter /projekt-Durchlauf).
    Quelle: ``defaults/orchestrator_beispiel.json`` (Saved-Run-Schema). Wird nach
    ``data/orchestrator/beispiel-vorgang.json`` kopiert, **wenn dort nicht vorhanden**
    (idempotent, kein Config-Flag). Danach im Chat via ``/vorgang`` ladbar. Neu einspielen
    (z. B. nach einer Aktualisierung der Datei): die geseedete Kopie löschen."""
    try:
        src = DEFAULTS_DIR / "orchestrator_beispiel.json"
        if not src.exists():
            return
        ORCHESTRATOR_DIR.mkdir(parents=True, exist_ok=True)
        dst = ORCHESTRATOR_DIR / "beispiel-vorgang.json"
        if dst.exists():
            return
        dst.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
        print(f"[DB] Beispiel-Vorgang eingespielt -> {dst.name}")
    except Exception as e:
        print("[DB] Beispiel-Vorgang-Seed übersprungen: " + str(e))


@app.on_event("startup")
async def _startup():
    await _db.init()
    await _db.migrate_json(CONVERSATIONS_DIR)
    # To-Do-Projekte einmalig aus den alten JSON-Dateien in die DB übernehmen
    # (Wurzelprojekt = Benutzername); Alt-JSON bleibt liegen, bis der DB-Betrieb steht.
    await _db.migrate_todo_json(TODO_DIR, _todo_root_name())
    await _seed_todo_demo()
    _seed_orchestrator_example()


# ── Feature-Router einhängen (VOR dem Static-Mount) ──────────────────────────
# INCLUDE-ROUTER  (Marker – nicht entfernen)
app.include_router(_r_chat.router)
app.include_router(_r_presentation.router)
app.include_router(_r_workflow.router)
app.include_router(_r_tts.router)
app.include_router(_r_music.router)
app.include_router(_r_image.router)
app.include_router(_r_upload.router)
app.include_router(_r_research.router)
app.include_router(_r_providers.router)
app.include_router(_r_rfq.router)
app.include_router(_r_agents.router)
app.include_router(_r_matrix.router)
app.include_router(_r_rag.router)
app.include_router(_r_jury.router)
app.include_router(_r_pst.router)
app.include_router(_r_patente.router)
app.include_router(_r_plans.router)
app.include_router(_r_projects.router)
app.include_router(_r_profile.router)
app.include_router(_r_backup.router)
app.include_router(_r_help.router)
app.include_router(_r_code.router)
app.include_router(_r_medizin.router)
app.include_router(_r_mathe.router)
app.include_router(_r_todo.router)
app.include_router(_r_conversations.router)
app.include_router(_r_mail.router)
app.include_router(_r_dir_analysis.router)
app.include_router(_r_morph.router)
app.include_router(_r_setup.router)
app.include_router(_r_jury_docs.router)
app.include_router(_r_transcribe.router)
app.include_router(_r_capacity.router)
app.include_router(_r_feedback.router)
app.include_router(_r_logs.router)
app.include_router(_r_export.router)
app.include_router(_r_dokumente.router)
app.include_router(_r_varianten.router)
app.include_router(_r_compare.router)
app.include_router(_r_pairing.router)
app.include_router(_r_orchestrator.router)
app.include_router(_r_goal.router)


# ── Static Files (muss zuletzt kommen) ───────────────────────────────────────

app.mount("/", StaticFiles(directory="static", html=True), name="static")
