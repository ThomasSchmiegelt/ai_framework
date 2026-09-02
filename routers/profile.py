"""Router: Profil-API + Assets (/api/profile, /api/assets)

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


# ── Profil-API ────────────────────────────────────────────────────────────────


@router.get("/api/profile")
async def get_profile():
    p = _load_profile()
    # Erstaufruf (noch kein Profil gespeichert): optionale Tabs standardmäßig aus.
    if not PROFILE_FILE.exists():
        p.setdefault("hidden_tabs", list(_DEFAULT_HIDDEN_TABS))
    # Installer-Flag: ob externe KI-Anbieter (API) angeboten werden (read-only,
    # aus config.json). Steuert nur die Sichtbarkeit des Anbieter-Abschnitts.
    p["enable_api"] = bool(_CONFIG.get("enable_api", True))
    # Installer-Flag: ob Python im Code-Tab serverseitig ausgeführt werden darf
    # (read-only, aus config.json). Steuert die Sichtbarkeit der Python-Option.
    p["allow_python_exec"] = ALLOW_PYTHON_EXEC
    # Versionsnummer (read-only) fürs Profil-Modal / Branding.
    p["app_version"] = APP_VERSION
    # Kontextfenster-Default ans Frontend spiegeln, falls noch nichts gewählt wurde.
    p.setdefault("chat_num_ctx", CHAT_NUM_CTX)
    return p


@router.put("/api/profile")
async def save_profile(req: Request):
    body = await req.json()
    str_fields = {"first_name", "last_name", "company", "department", "position", "email", "phone", "default_project"}
    profile = {k: str(v).strip() for k, v in body.items() if k in str_fields}
    # Oberflächen- und Antwortsprache (de/en)
    lang = str(body.get("lang", "") or "").lower().strip()
    profile["lang"] = "en" if lang == "en" else "de"
    # Modus (fachliche Ausrichtung + Farbschema)
    mode = str(body.get("mode", "") or "").lower().strip()
    profile["mode"] = mode if mode in VALID_MODES else DEFAULT_MODE
    # Frei konfigurierbarer violetter Modus „custom": Name, Fachbrille (Prompt)
    # und optionale Stichwörter werden im Profil hinterlegt.
    profile["custom_mode_name"]     = str(body.get("custom_mode_name", "") or "").strip()[:40]
    profile["custom_mode_prompt"]   = str(body.get("custom_mode_prompt", "") or "").strip()[:2000]
    profile["custom_mode_keywords"] = str(body.get("custom_mode_keywords", "") or "").strip()[:1000]
    # Modus prägt die KI-Prompts? (Standard: ja)
    profile["mode_prompt"] = bool(body.get("mode_prompt", True))
    # „LLM pur": keine Modi/Persona/Grundregel/Formel-/Zitatregeln voranstellen
    profile["pure_llm"] = bool(body.get("pure_llm", False))
    # Antwortstil-Persona (leer = neutral)
    tone = str(body.get("tone", "") or "").lower().strip()
    profile["tone"] = tone if tone in VALID_TONES else ""
    # Modell-Rollen (Allgemein / Programmieren / Wissenschaftlich); leer → Standardmodell
    for _key in _MODEL_ROLES.values():
        val = str(body.get(_key, "") or "").strip()
        if val and val not in _MODEL_PLACEHOLDERS:
            profile[_key] = val
    # Mathe-Weiche: erkannte Matheaufgaben ans Mathe-Modell durchreichen, solange nur
    # das schwache Standardmodell aktiv ist (Standard: an).
    profile["math_autoroute"] = bool(body.get("math_autoroute", True))
    # Recherche (Matrix + Recherche-Tab) zwingend lokal ausführen (Standard: aus)
    profile["research_local_only"] = bool(body.get("research_local_only", False))
    # Erweiterte Chat-Werkzeuge: Code-Interpreter (run_python) + autonome Web-Recherche
    # im Chat-Werkzeug-Loop (Standard: aus — kleine Modelle sind damit überfordert)
    profile["chat_code_interpreter"] = bool(body.get("chat_code_interpreter", False))
    # Automatisches Angebot einer tiefen Recherche bei breiten Fakten-/Rechercheanfragen
    # (rein Frontend-Steuerung; Standard: an)
    profile["deep_research_offer"] = bool(body.get("deep_research_offer", True))
    # 🧭 Assistent-Modus: nur Chat-Tab, Modell wählt Werkzeuge selbst (Standard: aus)
    profile["assistant_mode"] = bool(body.get("assistant_mode", False))
    # Vertrauliche Auswertungen (Verzeichnis-Analyse, Postfach) dürfen API-Modelle
    # nutzen, wenn explizit eines gewählt ist (Standard: aus — alles bleibt lokal)
    profile["confidential_allow_api"] = bool(body.get("confidential_allow_api", False))
    # Globaler Geheim-/Lokal-Modus: alle Modell-Rollen zwingend lokal (Standard: aus)
    profile["local_only_mode"] = bool(body.get("local_only_mode", False))
    # Optionales API-TTS-Modell (anbieter::modell) für die Sprachausgabe; leer = Browser
    _ttsm = str(body.get("tts_model", "") or "").strip()
    profile["tts_model"] = "" if _ttsm in _MODEL_PLACEHOLDERS else _ttsm
    # Bildgenerierung: gewähltes Modell (local::sd / anbieter::modell) + lokale SD-URL
    _imgm = str(body.get("image_model", "") or "").strip()
    profile["image_model"] = "" if _imgm in _MODEL_PLACEHOLDERS else _imgm
    profile["sd_webui_url"] = str(body.get("sd_webui_url", "") or "").strip()
    if "sd_autostart" in body:
        profile["sd_autostart"] = bool(body.get("sd_autostart"))
    if "sd_server_dir" in body:
        profile["sd_server_dir"] = str(body.get("sd_server_dir", "") or "").strip()
    # Videogenerierung: gewähltes Modell (local::wan) + lokale Video-Server-URL
    _vidm = str(body.get("video_model", "") or "").strip()
    profile["video_model"] = "" if _vidm in _MODEL_PLACEHOLDERS else _vidm
    profile["video_server_url"] = str(body.get("video_server_url", "") or "").strip()
    if "video_autostart" in body:
        profile["video_autostart"] = bool(body.get("video_autostart"))
    if "video_server_dir" in body:
        profile["video_server_dir"] = str(body.get("video_server_dir", "") or "").strip()
    if "video_timeout" in body:
        try:
            profile["video_timeout"] = max(0, int(body.get("video_timeout") or 0))
        except (TypeError, ValueError):
            profile["video_timeout"] = 0
    # Automatische Komprimierung langer Verläufe (Überlauf + Leerlauf)
    profile["auto_compress"] = bool(body.get("auto_compress", False))
    try:
        profile["compress_overflow_chars"] = max(2000, int(body.get("compress_overflow_chars", 12000)))
    except (TypeError, ValueError):
        profile["compress_overflow_chars"] = 12000
    try:
        profile["compress_idle_min"] = max(1, int(body.get("compress_idle_min", 10)))
    except (TypeError, ValueError):
        profile["compress_idle_min"] = 10
    # Kontextfenster (Tokens) für Chat/Dokumentengenerator. Nur erlaubte Stufen
    # zulassen, damit ein Tippfehler nicht den VRAM sprengt. Leer/ungültig → Default.
    try:
        _nctx = int(body.get("chat_num_ctx", CHAT_NUM_CTX))
    except (TypeError, ValueError):
        _nctx = CHAT_NUM_CTX
    profile["chat_num_ctx"] = _nctx if _nctx in _ALLOWED_NUM_CTX else CHAT_NUM_CTX
    # Token-Preis (für den Kostenschätzer im Token-Zähler). Lokale Modelle = 0.
    for _pk in ("price_per_1k_in", "price_per_1k_out"):
        try:
            profile[_pk] = max(0.0, float(body.get(_pk, 0) or 0))
        except (TypeError, ValueError):
            profile[_pk] = 0.0
    profile["currency"] = (str(body.get("currency", "€") or "€").strip() or "€")[:4]
    # Erst-Start-Einleitung: einmal absolviert? + beim nächsten Start erneut zeigen?
    profile["onboarding_done"] = bool(body.get("onboarding_done", False))
    profile["replay_intro"] = bool(body.get("replay_intro", False))
    # Ausgeblendete optionale Tabs. Nur überschreiben, wenn das Feld explizit
    # mitgesendet wird (Profil-Modal). Fehlt es (z. B. Onboarding-Speicherung),
    # gilt die Erstaufruf-Voreinstellung: alle optionalen Tabs ausgeblendet.
    if "hidden_tabs" in body:
        raw_hidden = body.get("hidden_tabs") or []
        profile["hidden_tabs"] = [t for t in raw_hidden if t in _OPTIONAL_TABS]
    else:
        profile["hidden_tabs"] = list(_DEFAULT_HIDDEN_TABS)
    PROFILE_FILE.write_text(json.dumps(profile, ensure_ascii=False, indent=2), encoding="utf-8")
    return profile

# ── Asset-Serving (bilder/) ───────────────────────────────────────────────────

_ALLOWED_IMAGE_EXT = {".jpg", ".jpeg", ".png", ".gif", ".svg", ".webp"}


@router.get("/api/assets/{name}")
async def get_asset(name: str):
    fp = BILDER_DIR / name
    if fp.suffix.lower() not in _ALLOWED_IMAGE_EXT:
        raise HTTPException(400, "Nur Bilddateien erlaubt")
    if not fp.exists() or not fp.is_file():
        raise HTTPException(404, "Datei nicht gefunden")
    return FileResponse(fp)


# ── Profil-Assets (Logo, Vorlagen-Deckblatt, Vorlagen-Kopfzeile) ──────────────
# Diese ersetzen die früheren Corporate-Bilder unter bilder/. Der Nutzer lädt sie
# im Profil hoch; sie werden seitenverhältnis-erhaltend auf eine Sollgröße skaliert.
_PROFILE_ASSETS = {
    "logo":   {"file": "logo.png",   "max": 512,  "fmt": "PNG",  "default": "default_logo.png",   "size": "512×512 px, PNG mit Transparenz"},
    "cover":  {"file": "cover.jpg",  "max": 1920, "fmt": "JPEG", "default": "default_cover.jpg",   "size": "1920×1080 px, JPG"},
    "header": {"file": "header.jpg", "max": 1920, "fmt": "JPEG", "default": "default_header.jpg",  "size": "1920×240 px, PNG/JPG"},
    # „closing" (Abschlussfolie) hat KEIN Profil-Upload – es kommt ausschließlich aus den
    # modus-spezifischen Vorlagen (Modern Blau). Als Profil-Asset nur ein Platzhalter,
    # damit die GET-Route den Zweck kennt.
    "closing": {"file": "closing.jpg", "max": 1920, "fmt": "JPEG", "default": "",                 "size": "1920×1080 px, JPG (nur Modern Blau)"},
}


def _seed_default_profile_assets() -> None:
    """Beim ersten Start die mitgelieferten Standard-Branding-Bilder
    (``bilder/default_*``) nach ``data/profile_assets/`` übernehmen – nur, solange
    der Nutzer noch keine eigenen hochgeladen hat. Einmalig per Sentinel, damit ein
    bewusst entferntes Asset nicht beim nächsten Start wieder auftaucht."""
    sentinel = PROFILE_ASSETS_DIR / ".defaults_seeded"
    if sentinel.exists():
        return
    for cfg in _PROFILE_ASSETS.values():
        target = PROFILE_ASSETS_DIR / cfg["file"]
        src = BILDER_DIR / cfg.get("default", "")
        if not target.exists() and src.exists():
            try:
                target.write_bytes(src.read_bytes())
            except Exception:
                pass
    try:
        sentinel.write_text("seeded", encoding="utf-8")
    except Exception:
        pass


_seed_default_profile_assets()


@router.get("/api/profile/assets")
async def list_profile_assets():
    """Welche Profil-Assets sind gesetzt? (für die UI)"""
    return {
        kind: {
            "present": (PROFILE_ASSETS_DIR / cfg["file"]).exists(),
            "recommended": cfg["size"],
        }
        for kind, cfg in _PROFILE_ASSETS.items()
    }


@router.post("/api/profile/asset/{kind}")
async def upload_profile_asset(kind: str, file: UploadFile = File(...)):
    if kind not in _PROFILE_ASSETS:
        raise HTTPException(400, "Unbekannter Asset-Typ")
    cfg = _PROFILE_ASSETS[kind]
    raw = await file.read()
    try:
        from PIL import Image
        im = Image.open(io.BytesIO(raw))
        im.thumbnail((cfg["max"], cfg["max"]))   # seitenverhältnis-erhaltend
        out = PROFILE_ASSETS_DIR / cfg["file"]
        if cfg["fmt"] == "PNG":
            im.convert("RGBA").save(out, "PNG")
        else:
            bg = Image.new("RGB", im.size, (255, 255, 255))
            im2 = im.convert("RGBA")
            bg.paste(im2, mask=im2.split()[-1])
            bg.save(out, "JPEG", quality=88)
    except Exception as e:
        raise HTTPException(400, f"Bild konnte nicht verarbeitet werden: {e}")
    return {"ok": True, "kind": kind}


@router.get("/api/profile/asset/{kind}")
async def get_profile_asset(kind: str):
    if kind not in _PROFILE_ASSETS:
        raise HTTPException(400, "Unbekannter Asset-Typ")
    # 1) Firmeneigene Vorlage des aktiven Modus (nur „Modern Blau") hat Vorrang …
    tpl = _mode_template_asset(kind)
    if tpl is not None:
        return FileResponse(tpl)
    # 2) … sonst das normale Profil-Branding (kein „closing"-Profilupload → 404).
    fp = PROFILE_ASSETS_DIR / _PROFILE_ASSETS[kind]["file"]
    if not fp.exists():
        raise HTTPException(404, "Kein Asset hinterlegt")
    return FileResponse(fp)


@router.delete("/api/profile/asset/{kind}")
async def delete_profile_asset(kind: str):
    if kind not in _PROFILE_ASSETS:
        raise HTTPException(400, "Unbekannter Asset-Typ")
    fp = PROFILE_ASSETS_DIR / _PROFILE_ASSETS[kind]["file"]
    if fp.exists():
        fp.unlink()
    return {"ok": True}
