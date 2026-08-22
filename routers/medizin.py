"""Router: Medizin-Pipeline (2-Modell-Konsultation, /api/medizin)

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


# ── Medizin-Pipeline (2-Modell-Konsultation: Ministral ↔ MedGemma) ───────────
#
# Ablauf je Nutzer-Eingabe (human-in-the-loop, max. 2 Rückfrage-Runden):
#   1. Ministral (Allgemein) strukturiert die Eingabe medizinisch sauber  (Stage „refine")
#   2. MedGemma (Medizin) prüft auf fehlende Angaben                       (Stage „analyze")
#   3a. Fehlt etwas & Runde < 2 → Ministral formuliert Rückfrage → warten  (Frame „question")
#   3b. Vollständig (oder Runde erschöpft) → MedGemma erstellt die finale
#       Einschätzung, gestreamt                                            (Stage „final" + text)
# Optional danach: /api/medizin/translate übersetzt das Ergebnis per
# Ministral in laienverständliches Deutsch.
#
# Jeder Schritt läuft sequenziell in einem eigenen _model_session-Block, damit
# der VRAM-Guard die Modellwechsel serialisiert (nie zwei Modelle gleichzeitig).

_MED_MAX_ROUNDS = 2  # Höchstzahl an Rückfrage-Runden, dann zwingend Ergebnis

_MED_DISCLAIMER = (
    "Wichtig: Du bist ein medizinisches Assistenzsystem, KEIN Arzt. Stelle keine "
    "endgültige Diagnose und ersetze keine ärztliche Untersuchung. Weise am Ende kurz "
    "darauf hin, dass die Einschätzung ärztlich geprüft werden muss, und nenne ggf. "
    "Warnsignale, bei denen sofort ärztliche Hilfe nötig ist."
)


async def _med_call(client, model: str, system: str, user: str, *, think: bool = False,
                    tok: Optional[dict] = None) -> str:
    """Ein nicht-streamender Ollama-Chat-Aufruf, gibt den reinen Text zurück.
    ``tok`` (optional): dict {"in","out"}, in das der Tokenverbrauch summiert wird."""
    resp = await _llm.chat(client,{
        "model": model,
        "think": think,
        "stream": False,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    })
    resp.raise_for_status()
    j = resp.json()
    if tok is not None:
        _a, _b = _llm_tok(j)
        tok["in"] += _a
        tok["out"] += _b
    raw = j.get("message", {}).get("content", "") or ""
    return re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL).strip()


async def _med_rag_context(rag_collections: list, query: str) -> str:
    """Sucht passende Passagen aus den (Patienten-)Wissensdatenbanken."""
    if not rag_collections:
        return ""
    try:
        from tools.rag import query_collections
        hits = await query_collections(rag_collections, query)
        if hits:
            joined = "\n\n".join(h.get("text", "") for h in hits[:6])
            return joined[:3000]
    except Exception:
        pass
    return ""




# Falldokumente (Gutachten, Überweisung, Attest, Befunde) fließen mit ihrem
# VOLLTEXT in den Analyseprompt — anders als der (kürzere) RAG-Auszug. Pro
# Dokument gedeckelt, damit der Kontext nicht überläuft.
_MED_DOC_CHARS = 8000

# Strukturierte Patientenstammdaten (Formular im Medizin-Tab). Reihenfolge =
# Anzeige im Kontextblock.
_MED_PATIENT_FIELDS = [
    ("name", "Name"),
    ("birthdate", "Geburtsdatum/Alter"),
    ("sex", "Geschlecht"),
    ("concern", "Anliegen/Verdachtsdiagnose"),
    ("history", "Vorerkrankungen"),
    ("medication", "Dauermedikation"),
    ("allergies", "Allergien"),
]


def _med_patient_block(patient: dict) -> str:
    if not isinstance(patient, dict):
        return ""
    lines = []
    for key, label in _MED_PATIENT_FIELDS:
        val = str(patient.get(key, "") or "").strip()
        if val:
            lines.append(f"- {label}: {val}")
    return "Patientenstammdaten (vom Nutzer angegeben):\n" + "\n".join(lines) if lines else ""


def _med_documents_block(documents: list) -> str:
    if not documents:
        return ""
    parts = []
    for d in documents:
        if not isinstance(d, dict):
            continue
        txt = str(d.get("text", "") or "").strip()
        if not txt or txt.startswith("[Lesefehler") or txt.startswith("[Kann Datei"):
            continue
        name = str(d.get("filename", "") or "Dokument").strip()
        parts.append(f"Dokument „{name}“:\n{txt[:_MED_DOC_CHARS]}")
    return "\n\n".join(parts)


def _med_case_context(patient: dict, documents: list) -> str:
    """Baut den zusätzlichen Fallkontext aus Patientenstammdaten + Falldokumenten."""
    blocks = [b for b in (_med_patient_block(patient), _med_documents_block(documents)) if b]
    return "\n\n".join(blocks)


@router.post("/api/medizin/extract")
async def medizin_extract(file: UploadFile = File(...)):
    """Extrahiert den Volltext eines hochgeladenen Falldokuments (Gutachten,
    Überweisung, Attest, Befund) für den Analyseprompt der Medizin-Pipeline.
    Rein lokale Textextraktion (tools/files.extract), kein LLM-Aufruf. Bilder
    werden nicht per OCR gelesen — sie gehören in den Chat-Anhang (Vision-Modell)."""
    data = await file.read()
    is_image = _is_image(Path(file.filename or ""))
    tmp = UPLOADS_DIR / f"medext_{uuid.uuid4().hex[:8]}_{file.filename}"
    tmp.write_bytes(data)
    try:
        text = await asyncio.to_thread(_extract_text, tmp)
    finally:
        try:
            tmp.unlink()
        except OSError:
            pass
    return {"filename": file.filename, "text": text, "is_image": is_image, "chars": len(text or "")}


@router.post("/api/medizin/consult")
async def medizin_consult(req: Request):
    """Eine Stufe der Medizin-Konsultation (siehe Beschreibung oben). Streamt
    SSE-Frames: stage / question / text / done / error."""
    body = await req.json()
    messages = body.get("messages") or []
    rag_collections = body.get("rag_collections") or []
    documents = body.get("documents") or []
    patient = body.get("patient") or {}
    try:
        rnd = int(body.get("round", 0))
    except Exception:
        rnd = 0

    model_general = _pick_model(body.get("model_general"), _model_for("general"))
    model_medical = _pick_model(body.get("model_medical"), _model_for("medical"))

    transcript = _med_transcript(messages)
    case_ctx = _med_case_context(patient, documents)
    latest = ""
    for m in reversed(messages):
        if m.get("role") == "user":
            latest = str(m.get("content", "")).strip()
            break

    async def _stream():
        if not transcript and not case_ctx:
            yield _sse({"type": "error", "content": "Keine Eingabe erhalten."})
            return

        _tok = {"in": 0, "out": 0}   # Tokenverbrauch über alle Stufen (→ done-Frame)
        # ── Stage 1: Ministral strukturiert die Anfrage medizinisch ──────────
        yield _sse({"type": "stage", "stage": "refine", "status": "start",
                     "label": f"{model_general} strukturiert die Anfrage…"})
        refine_user = f"Gesprächsverlauf:\n{transcript}" if transcript else ""
        if case_ctx:
            refine_user = (f"{case_ctx}\n\n{refine_user}" if refine_user else case_ctx)
        try:
            async with _model_session(model_general), httpx.AsyncClient(timeout=120) as client:
                refined = await _med_call(
                    client, model_general,
                    ("Du bereitest Patientenanfragen für einen medizinischen Fachkollegen auf. "
                     "Formuliere aus dem Gesprächsverlauf UND den beigefügten Patientenstammdaten/"
                     "Falldokumenten (z. B. Überweisung, Gutachten, Attest, Befund) eine sachliche, "
                     "strukturierte medizinische Falldarstellung in Stichpunkten (Anliegen, bekannte "
                     "Angaben wie Alter/Geschlecht/Symptome/Dauer/Vorerkrankungen/Medikamente, soweit "
                     "genannt; nenne relevante Inhalte der Dokumente). Erfinde nichts, ergänze keine "
                     "nicht genannten Fakten. Nur die Falldarstellung, kein Vorwort."),
                    refine_user, tok=_tok,
                )
        except Exception as e:
            yield _sse({"type": "error", "content": f"Aufbereitung fehlgeschlagen: {e}"})
            return
        if not refined:
            refined = latest
        yield _sse({"type": "stage", "stage": "refine", "status": "done", "content": refined})

        # ── Stage 2: MedGemma prüft auf fehlende Angaben ─────────────────────
        rag_ctx = await _med_rag_context(rag_collections, refined or latest)
        forced_final = rnd >= _MED_MAX_ROUNDS
        if not forced_final:
            yield _sse({"type": "stage", "stage": "analyze", "status": "start",
                         "label": f"{model_medical} prüft auf fehlende Angaben…"})
            analyze_user = f"Strukturierte Falldarstellung:\n{refined}"
            if rag_ctx:
                analyze_user += f"\n\nPatientenakte (Auszug):\n{rag_ctx}"
            try:
                async with _model_session(model_medical), httpx.AsyncClient(timeout=180) as client:
                    analysis = await _med_call(
                        client, model_medical,
                        ("Du bist ein erfahrener Mediziner. Prüfe, ob für eine fundierte erste "
                         "Einschätzung wesentliche Angaben fehlen — sowohl Patientendaten (Alter, "
                         "Geschlecht, Dauer/Verlauf, Schweregrad, Begleitsymptome, Vorerkrankungen, "
                         "Medikamente, Allergien) ALS AUCH relevante Unterlagen (z. B. Überweisung, "
                         "ärztliches Attest, Befunde, Laborwerte), falls sie für die Beurteilung nötig "
                         "wären. Wenn alles Wesentliche vorhanden ist, antworte mit GENAU dem Wort "
                         "VOLLSTAENDIG. Andernfalls beginne mit FEHLT: und liste danach in kurzen "
                         "Stichpunkten (max. 4) nur die wirklich fehlenden Angaben bzw. Unterlagen."),
                        analyze_user, tok=_tok,
                    )
            except Exception as e:
                yield _sse({"type": "error", "content": f"Analyse fehlgeschlagen: {e}"})
                return
            yield _sse({"type": "stage", "stage": "analyze", "status": "done", "content": analysis})

            complete = "vollstaendig" in analysis.lower()[:60] or "vollständig" in analysis.lower()[:60]
            if not complete and analysis.strip():
                # ── Stage 3a: Ministral formuliert eine Rückfrage ────────────
                yield _sse({"type": "stage", "stage": "formulate", "status": "start",
                             "label": f"{model_general} formuliert die Rückfrage…"})
                try:
                    async with _model_session(model_general), httpx.AsyncClient(timeout=120) as client:
                        question = await _med_call(
                            client, model_general,
                            ("Du sprichst freundlich und verständlich mit einem Patienten (kein "
                             "Fachjargon). Formuliere eine kurze, klare Rückfrage auf Deutsch, die "
                             "den Patienten genau um die unten genannten fehlenden Angaben bittet. "
                             "Bündele sie in 1–3 einfachen Fragen. Wenn Unterlagen fehlen (z. B. "
                             "Überweisung, ärztliches Attest, Befund, Laborwerte), weise am Ende in "
                             "einem kurzen Satz freundlich darauf hin, dass diese auch direkt als "
                             "Dokument angehängt werden können. Nur die Rückfrage, kein Vorwort."),
                            f"Ursprüngliches Anliegen:\n{latest}\n\nFehlende Angaben:\n{analysis}",
                            tok=_tok,
                        )
                except Exception as e:
                    yield _sse({"type": "error", "content": f"Rückfrage fehlgeschlagen: {e}"})
                    return
                if not question:
                    question = "Können Sie bitte noch ein paar Angaben ergänzen (Alter, Dauer, Begleitsymptome)?"
                yield _sse({"type": "stage", "stage": "formulate", "status": "done"})
                yield _sse({"type": "question", "content": question, "round": rnd + 1})
                yield _sse({"type": "done", "needs_followup": True, "round": rnd + 1, "tokens": _tok})
                return

        # ── Stage 3b: MedGemma erstellt die finale Einschätzung (gestreamt) ──
        yield _sse({"type": "stage", "stage": "final", "status": "start",
                     "label": f"{model_medical} erstellt die Einschätzung…"})
        final_user = f"Strukturierte Falldarstellung:\n{refined}\n\nVollständiger Verlauf:\n{transcript}"
        if case_ctx:
            final_user += f"\n\nOriginal-Fallunterlagen (Volltext):\n{case_ctx}"
        if rag_ctx:
            final_user += f"\n\nPatientenakte (Auszug):\n{rag_ctx}"
        try:
            async with _model_session(model_medical), httpx.AsyncClient(timeout=300) as client:
                async for chunk in _llm.stream(client, {
                    "model": model_medical,
                    "think": False,   # MedGemma unterstützt Ollamas Think-Modus nicht
                                       # (liefert dann leeren content) – immer aus.
                    "stream": True,
                    "messages": [
                        {"role": "system", "content": (
                            "Du bist ein erfahrener Mediziner und gibst eine fundierte erste "
                            "fachliche Einschätzung auf Deutsch: mögliche Ursachen / "
                            "Differentialdiagnosen, sinnvolle nächste Schritte und Untersuchungen, "
                            "Dringlichkeit. Strukturiere klar mit Überschriften. " + _MED_DISCLAIMER
                        )},
                        {"role": "user", "content": final_user},
                    ],
                }):
                    token = chunk.get("message", {}).get("content", "")
                    if token:
                        yield _sse({"type": "text", "content": token})
                    if chunk.get("done"):
                        _a, _b = _llm_tok(chunk)
                        _tok["in"] += _a
                        _tok["out"] += _b
        except Exception as e:
            yield _sse({"type": "error", "content": f"Einschätzung fehlgeschlagen: {e}"})
            return
        yield _sse({"type": "done", "needs_followup": False, "round": rnd, "tokens": _tok})

    return StreamingResponse(_stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.post("/api/medizin/translate")
async def medizin_translate(req: Request):
    """Übersetzt eine medizinische Einschätzung per Allgemein-Modell in
    laienverständliches Deutsch (gestreamt)."""
    body = await req.json()
    text = str(body.get("text", "")).strip()
    model_general = _pick_model(body.get("model_general"), _model_for("general"))
    if not text:
        raise HTTPException(status_code=400, detail="Kein Text übergeben")

    async def _stream():
        _tok = {"in": 0, "out": 0}
        try:
            async with _model_session(model_general), httpx.AsyncClient(timeout=180) as client:
                async for chunk in _llm.stream(client, {
                    "model": model_general,
                    "think": False,
                    "stream": True,
                    "messages": [
                        {"role": "system", "content": (
                            "Übersetze den folgenden medizinischen Text in einfaches, "
                            "laienverständliches Deutsch ohne Fachjargon. Behalte ALLE wichtigen "
                            "Aussagen, Empfehlungen und Warnhinweise bei, erkläre Fachbegriffe kurz. "
                            "Erfinde nichts hinzu.")},
                        {"role": "user", "content": text[:6000]},
                    ],
                }):
                    token = chunk.get("message", {}).get("content", "")
                    if token:
                        yield _sse({"type": "text", "content": token})
                    if chunk.get("done"):
                        _a, _b = _llm_tok(chunk)
                        _tok["in"] += _a
                        _tok["out"] += _b
        except Exception as e:
            yield _sse({"type": "error", "content": f"Übersetzung fehlgeschlagen: {e}"})
            return
        yield _sse({"type": "done", "tokens": _tok})

    return StreamingResponse(_stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

