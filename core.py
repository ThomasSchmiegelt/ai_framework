"""Geteilte Kernfläche des Backends (aus main.py herausgezogen).

Enthält Konfiguration, Pfade (+ Import-Seiteneffekte: DB-Pfad, mkdir, LLM-Config,
Default-Agenten), Modellwahl, Profil-Flags, Prompt-Bau, LLM-Plumbing, SSE.
Wird von main.py und den routers/-Modulen per ``from core import *`` genutzt;
``__all__`` listet daher auch die _unterstrich-Helfer explizit auf.
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


_CONFIG_FILE = Path("config.json")
_CONFIG: dict = {}
if _CONFIG_FILE.exists():
    try:
        _CONFIG = json.loads(_CONFIG_FILE.read_text(encoding="utf-8"))
    except Exception:
        pass

OLLAMA_BASE: str = _CONFIG.get("ollama_base", "http://localhost:11434")
ALLOWED_MODELS: list[str] = _CONFIG.get("allowed_models", [])
DEFAULT_MODEL: str = _CONFIG.get("default_model", "granite4.2:3b")
# Kontextfenster (Tokens) für den Haupt-Chat/Dokumentengenerator. Ollama-Default ist
# nur 4096 – das reicht bei Recherche-/RAG-Berichten nicht, sodass Antworten mitten im
# Satz abbrechen. Höher kostet KV-Cache-VRAM (config.json: "chat_num_ctx").
CHAT_NUM_CTX: int = int(_CONFIG.get("chat_num_ctx", 8192))
# Im Profil wählbare Kontextfenster-Stufen (Tokens). Mehr = mehr KV-Cache-VRAM.
_ALLOWED_NUM_CTX: tuple[int, ...] = (4096, 8192, 16384, 32768, 65536, 131072)
# Wie lange ein geladenes Modell ohne neue Anfrage im VRAM bleibt (Ollama keep_alive).
# Lang genug, damit es zwischen Schritten nicht ständig neu lädt; endlich, damit der
# VRAM im Leerlauf wieder frei wird. Bei Modellwechsel entlädt _model_session sofort.
KEEP_ALIVE: str = str(_CONFIG.get("model_keep_alive", "30m"))
# Python-Ausführung im Code-Tab (serverseitig). Lokal sinnvoll; im Mehrbenutzer-/
# Servermodus ggf. abschalten (config.json: "allow_python_exec": false).
ALLOW_PYTHON_EXEC: bool = bool(_CONFIG.get("allow_python_exec", True))
# Versionsnummer des Frameworks. Quelle ist die Datei VERSION im Programmordner:
# sie gehört zum Programmcode und wird vom Updater mitgetauscht, während
# config.json bewusst unberührt bleibt (Nutzerkonfiguration). Stünde die Version
# nur in config.json, bliebe sie nach jedem Update auf dem alten Stand — und die
# Software-Verteilung (ACMP) könnte die installierte Version nicht erkennen.
# Reihenfolge: VERSION-Datei → config.json → fest verdrahteter Wert.
def _read_version() -> str:
    try:
        v = (Path(__file__).parent / "VERSION").read_text(encoding="utf-8").strip()
        if v:
            return v.splitlines()[0].strip()
    except Exception:
        pass
    return str(_CONFIG.get("version") or "1.4.0")


APP_VERSION: str = _read_version()


# Platzhalter-Werte aus den Frontend-Selektoren (kein echtes Modell)
_MODEL_PLACEHOLDERS = {
    "Lade…", "Lade...", "Ollama nicht erreichbar", "Fehler beim Laden",
}


def _pick_model(m, fallback: Optional[str] = None) -> str:
    """Wählt ein gültiges Modell: das angeforderte (jedes installierte Ollama-Modell
    ist erlaubt), sonst den Fallback bzw. das Standardmodell. Verhindert 500er durch
    Platzhalternamen (z.B. 'Lade…') aus dem Frontend-Selektor.

    Im **Geheim-/Lokal-Modus** (Profil ``local_only_mode``) wird ein angefordertes
    **Remote-Modell verworfen**, damit der lokale Fallback greift — so bleibt alles
    lokal, ohne jeden Endpoint einzeln anzufassen (zentraler Chokepoint)."""
    m = (m or "").strip()
    if m and _secret_local() and _llm.is_remote(m) and not _llm.is_local(m):
        m = ""  # Remote-Wahl im Geheim-Modus fallenlassen → lokaler Fallback
        # (ein als lokal markierter llama.cpp/LM-Studio-Anbieter bleibt erlaubt)
    if m and m not in _MODEL_PLACEHOLDERS:
        return m
    return fallback or DEFAULT_MODEL

APP_DIR = Path(__file__).parent.resolve()   # Programm-/Repo-Wurzel (core.py liegt dort)
_raw_data_dir = _CONFIG.get("data_dir", "data")
DATA_DIR = Path(_raw_data_dir) if Path(_raw_data_dir).is_absolute() else Path(__file__).parent / _raw_data_dir
_db.set_db_path(DATA_DIR / "ai_framework_thomas.db")
UPLOADS_DIR = DATA_DIR / "uploads"
CONVERSATIONS_DIR = DATA_DIR / "conversations"
AGENTS_DIR = DATA_DIR / "agents"
REPORTS_DIR = DATA_DIR / "reports"
PLANS_DIR = DATA_DIR / "plans"
DOSSIERS_DIR = DATA_DIR / "dossiers"   # automatisch exportierte Planer-Recherche-Dossiers (.md)
CODE_DIR = DATA_DIR / "code"
JURIES_DIR = DATA_DIR / "juries"   # gespeicherte Bewertungs-Jurys (Gruppen von Agenten)
JURY_DOCS_DIR = DATA_DIR / "jury_docs"   # im Jury-Tab erstellte/geprüfte Dokumente
RFQ_DIR = DATA_DIR / "rfq"   # Anfrage-Auswertung: Job-Zwischenstände (resume-fähig)
PST_DIR = DATA_DIR / "pst"   # Postfach-Auswertung: geparste Mailstores (+ Anhänge, lokal)
PATENTE_DIR = DATA_DIR / "patente"   # Patent-Recherche: Fallakten je Projekt (+ Analysen)
PAT_CACHE_DIR = PATENTE_DIR / "_cache"   # Scrape-/OPS-Cache je Patentnummer (kein Projekt)
EPO_OPS_FILE = DATA_DIR / "epo_ops.json"   # EPO-OPS-Zugangsdaten (gitignored, Backup nur mit secrets)
FIRMENPROFIL_FILE = DATA_DIR / "firmenprofil.json"   # Absender-/Steuerdaten für Rechnungen
RECHNUNGEN_DIR = DATA_DIR / "rechnungen"   # erstellte Rechnungen (JSON-Datensatz je Nummer)
ANGEBOTE_DIR = DATA_DIR / "angebote"   # erstellte Angebote (JSON-Datensatz je Nummer)
ZEUGNISSE_DIR = DATA_DIR / "zeugnisse"   # erzeugte Arbeitszeugnisse (JSON-Datensatz)
VARIANTEN_DIR = DATA_DIR / "varianten"   # Variantenvergleich: ein Ordner je Vergleich (decision.json)
COMPARE_DIR = DATA_DIR / "compare"   # Excel-Vergleich: ein Ordner je Vergleich (comparison.json)
TODO_DIR = DATA_DIR / "todo"   # KI-To-Do-Listen mit Wissensgraph (ein Ordner je Liste, list.json)
TODO_ATT_DIR = DATA_DIR / "todo_att"   # Original-Anlagen je Punkt (MD-Text in DB); Backup + Todo-Router nutzen es
ORCHESTRATOR_DIR = DATA_DIR / "orchestrator"   # gespeicherte /projekt-Vorgänge (JSON) + Vorlagen (_templates/)
MAIL_CONFIG_FILE = DATA_DIR / "mail.json"        # IMAP-Zugang (Mail-Router; Backup listet es)
MAIL_RULES_FILE = DATA_DIR / "mail_rules.json"   # Mail-Automatikregeln (Mail-Router; Backup)
MORPH_TRAIN_DIR = DATA_DIR / "morph_training"    # Morph-Kasten Trainingsbeispiele (Backup)
CAPACITY_FILE = DATA_DIR / "capacity.json"   # globale Kapazitätsliste (tab-übergreifend)
BILDER_DIR = Path(__file__).parent / "bilder"
# Firmeneigene Branding-Vorlagen je Modus (gitignored, lokal befüllt). Aktuell nur
# „modern_blau" → APP_DIR/"weitere Vorlagen"/modern_blau/. Siehe README dort.
MODE_TEMPLATES_DIR = APP_DIR / "weitere Vorlagen"
PROFILE_FILE = DATA_DIR / "user_profile.json"
PROFILE_ASSETS_DIR = DATA_DIR / "profile_assets"
PROJECTS_FILE = DATA_DIR / "projects.json"
# Nutzer-Feedback aus dem Chat: „/-" (Fehler/Problem) und „/+" (Idee/Verbesserung)
# werden als Markdown-Protokoll gesammelt (für spätere Auswertung).
FEEDBACK_FILE = DATA_DIR / "feedback.md"
TRANSCRIPTS_DIR = DATA_DIR / "transcripts"   # hochgeladene/aufgenommene Audiodateien (Transkription)
# Externe OpenAI-kompatible KI-Anbieter (enthält API-Keys → gitignored, NICHT im Backup)
API_PROVIDERS_FILE = DATA_DIR / "api_providers.json"
LOG_FILE = DATA_DIR / "ai_framework_thomas.log"
RAG_IMAGES_DIR = DATA_DIR / "rag_images"   # Bild-aware RAG: Originalbilder je Sammlung/Dokument (Backup-Ordner)
# RAG-Embedding-Modell (Default). Cross-Feature (RAG, Verzeichnis-Analyse, Chat-Upload,
# To-Do, Planer) → in core, damit alle Router es über ``from core import *`` erhalten.
EMBED_MODEL: str = _CONFIG.get("embed_model", "nomic-embed-text")

# ── Transkription (Spracherkennung, Audio → Text) ────────────────────────────
# Lokale faster-whisper-Defaults aus config.json. Modell-Downloads landen in
# STT_DOWNLOAD_ROOT (Installer cached vor / Portable-Bundle liefert offline mit).
STT_MODEL = str(_CONFIG.get("stt_model", "base") or "base")
STT_DEVICE = str(_CONFIG.get("stt_device", "cpu") or "cpu")
STT_COMPUTE = str(_CONFIG.get("stt_compute", "int8") or "int8")
_stt_root = str(_CONFIG.get("stt_download_root", "") or "").strip()
STT_DOWNLOAD_ROOT = (
    (Path(_stt_root) if Path(_stt_root).is_absolute() else Path(__file__).parent / _stt_root)
    if _stt_root else (Path(__file__).parent / "models" / "whisper")
)

for _d in [UPLOADS_DIR, CONVERSATIONS_DIR, AGENTS_DIR, REPORTS_DIR, PLANS_DIR, DOSSIERS_DIR, CODE_DIR, JURIES_DIR, JURY_DOCS_DIR, RFQ_DIR, PST_DIR, RECHNUNGEN_DIR, ANGEBOTE_DIR, ZEUGNISSE_DIR, VARIANTEN_DIR, COMPARE_DIR, TODO_DIR, PROFILE_ASSETS_DIR, TRANSCRIPTS_DIR, RAG_IMAGES_DIR, ORCHESTRATOR_DIR]:
    _d.mkdir(parents=True, exist_ok=True)

# Mitgelieferte Standard-Agenten (Referenz-Quelle, getrennt von DATA_DIR, damit sie
# auch bei einem eigenen Datenpfad verfügbar sind).
DEFAULTS_DIR = Path(__file__).parent / "defaults"


def _seed_defaults() -> None:
    """Beim ersten Start einen leeren Agenten-Ordner mit den mitgelieferten
    Standard-Agenten (``defaults/agents/``) befüllen. Wichtig, wenn ein eigener
    Datenpfad (``config.json`` ``data_dir``) gewählt wurde – dort wird ``agents/``
    sonst leer angelegt und es erscheinen keine Agenten. Einmalig per Marker, damit
    bewusst gelöschte Agenten nicht beim nächsten Start wiederkehren."""
    marker = AGENTS_DIR / ".seeded"
    if marker.exists():
        return
    src_dir = DEFAULTS_DIR / "agents"
    has_agents = any(AGENTS_DIR.glob("*.json"))
    if src_dir.is_dir() and not has_agents:
        for src in src_dir.glob("*.json"):
            try:
                (AGENTS_DIR / src.name).write_text(
                    src.read_text(encoding="utf-8"), encoding="utf-8")
            except Exception:
                pass
    try:
        marker.write_text("seeded", encoding="utf-8")
    except Exception:
        pass


_seed_defaults()

# LLM-Abstraktion konfigurieren (lokal Ollama + ggf. externe API-Anbieter)
_llm.set_config(OLLAMA_BASE, API_PROVIDERS_FILE)

# ── Modi (fachliche Ausrichtung) ──────────────────────────────────────────────
# AI_Framework_Thomas kennt vier Modi; jeder prägt Farben (Frontend) und – wenn aktiv – die
# fachliche Brille der KI (System-Prompt-Präfix).
VALID_MODES = {"maschinenbau", "ki", "soziales", "marketing", "finanz", "geschaeftsfuehrung", "modern_blau", "custom"}
DEFAULT_MODE = "maschinenbau"
_MODE_PROMPTS = {
    "maschinenbau": (
        "Fachlicher Kontext: Maschinenbau/Ingenieurwesen. Antworte technisch präzise "
        "und normbewusst (z. B. VDI/DIN), mit Bezug zu Konstruktion, Werkstoffen, "
        "Berechnung und Fertigung."
    ),
    "ki": (
        "Fachlicher Kontext: Künstliche Intelligenz und Daten. Antworte mit Bezug zu "
        "Machine Learning, Datenanalyse, lokalen LLMs, MLOps und praktischer Umsetzung."
    ),
    "soziales": (
        "Fachlicher Kontext: Soziales/Gemeinwohl (Soziale Arbeit, Bildung, "
        "gemeinnützige Organisationen). Antworte klar, empathisch und "
        "adressatengerecht, mit Blick auf Wirkung und Teilhabe."
    ),
    "marketing": (
        "Fachlicher Kontext: Marketing und Kommunikation. Antworte zielgruppen- und "
        "wirkungsorientiert, mit Bezug zu Botschaft, Kanälen, Markenbild und Conversion."
    ),
    "finanz": (
        "Fachlicher Kontext: Finanzen und Controlling. Antworte mit Bezug zu "
        "Kennzahlen, Budgetierung, Wirtschaftlichkeit, Liquidität und Risiko. "
        "Sei nüchtern, zahlenorientiert und nachvollziehbar."
    ),
    "geschaeftsfuehrung": (
        "Fachlicher Kontext: Geschäftsführung und Management. Antworte strategisch, "
        "entscheidungsorientiert und prägnant, mit Blick auf Ziele, Chancen/Risiken "
        "und unternehmerische Wirkung."
    ),
    "modern_blau": (
        "Fachlicher Kontext: Professionelle Unternehmenskommunikation im Corporate-"
        "Design »Modern Blau«. Antworte klar, sachlich und präsentationsreif, mit "
        "gut strukturierten, übernehmbaren Aussagen (Stichpunkte, prägnante Titel)."
    ),
}

# ── Antwortstil-Personas (per Profil wählbar) ────────────────────────────────
VALID_TONES = {"roboter", "professor", "doktor", "felix", "sandra", "hartman"}
_TONE_PROMPTS = {
    "roboter": (
        "Du selbst antwortest im Stil »Roboter«: extrem sachlich und nüchtern. Keine "
        "Höflichkeitsfloskeln, keine persönliche Anrede, keine Emotionen. Nur Fakten – "
        "knapp, präzise und neutral."
    ),
    "professor": (
        "Du selbst bist »Herr Professor« – antworte in der Rolle eines souveränen "
        "Hochschulprofessors. WICHTIG: »Herr Professor« bist DU, nicht der Nutzer; rede "
        "den Nutzer niemals mit »Herr Professor« oder einem Titel an, sondern schlicht "
        "höflich mit »Sie«. Korrekt, formell und sachlich, in gewählter, präziser Fachsprache."
    ),
    "doktor": (
        "Du selbst bist »Frau Doktor« – antworte in dieser Rolle. WICHTIG: »Frau Doktor« "
        "bist DU, nicht der Nutzer; rede den Nutzer niemals mit »Frau Doktor« oder einem "
        "Titel an, sondern schlicht höflich mit »Sie«. Korrekt, höflich und sachlich-"
        "distanziert, professionell und klar verständlich."
    ),
    "felix": (
        "Du selbst heißt »Felix« – antworte in dieser Rolle. »Felix« ist DEIN Name, nicht "
        "der des Nutzers; den Nutzer nicht »Felix« nennen. Sprich den Nutzer durchgehend "
        "mit »Du« an. Locker und kumpelhaft, aber fachlich korrekt. Freundlich, direkt und "
        "unkompliziert."
    ),
    "sandra": (
        "Du selbst heißt »Sandra« – antworte in dieser Rolle. »Sandra« ist DEIN Name, nicht "
        "der des Nutzers; den Nutzer nicht »Sandra« nennen. Sprich den Nutzer durchgehend "
        "mit »Du« an. Herzlich und kumpelhaft, dabei sehr korrekt und sorgfältig. Nahbar, "
        "freundlich und genau."
    ),
    "hartman": (
        "Du selbst bist »Gunnery Sergeant Hartman«, ein Ausbilder mit militärisch-zackigem "
        "Kommandoton. Antworte KNAPP, laut und befehlsartig: kurze, schneidige Sätze, klare "
        "Anweisungen, kein Geschwafel und keine Höflichkeitsfloskeln. Nutze militärische "
        "Ansprache (»REKRUT«, »Sie«), gelegentlich GROSSBUCHSTABEN zur Betonung und ein "
        "forsches »Verstanden?« oder »Ausführung!« am Ende. WICHTIG: Der Drill ist nur der "
        "STIL – der Inhalt bleibt fachlich absolut KORREKT und tatsächlich hilfreich. KEINE "
        "Beleidigungen, keine Schimpfwörter, keine Herabwürdigung von Personen: Disziplin, "
        "Präzision und Tempo statt Beschimpfung."
    ),
}

_log_active: bool = False
_LOG_MAX_BYTES = 5 * 1024 * 1024  # 5 MB Rotation


def _write_log(entry: dict) -> None:
    if not _log_active:
        return
    try:
        if LOG_FILE.exists() and LOG_FILE.stat().st_size > _LOG_MAX_BYTES:
            LOG_FILE.rename(LOG_FILE.with_suffix(".1.log"))
        with open(LOG_FILE, "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"ts": time.time(), **entry}, ensure_ascii=False) + "\n")
    except Exception:
        pass


# ── VRAM-Schutz: nur EIN Modell gleichzeitig im Speicher (6-GB-Grenze) ────────
# Wechselt der Nutzer zwischen Modellen (z.B. Allgemein- vs. Programmier-Rolle aus
# dem Profil), wird das vorherige Modell zuerst aus dem VRAM entladen, bevor das neue lädt.
# Der Lock serialisiert zugleich alle Ollama-Generierungen, sodass nie zwei
# Modelle parallel geladen werden.
import contextlib as _contextlib

_model_lock = asyncio.Lock()
_loaded_model: Optional[str] = None


async def _unload_model(name: str) -> None:
    """Entlädt ein Modell sofort aus dem VRAM (Ollama keep_alive=0)."""
    if not name:
        return
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            await client.post(
                f"{OLLAMA_BASE}/api/generate",
                json={"model": name, "keep_alive": 0},
            )
    except Exception:
        pass


@_contextlib.asynccontextmanager
async def _model_session(model: str):
    """Hält den Modell-Lock für die Dauer einer Ollama-Generierung und stellt
    sicher, dass nur ein Modell im VRAM liegt. Beim Modellwechsel wird das
    zuvor geladene Modell zuerst entladen.

    Für **Remote-Modelle** (externe API-Anbieter) ist dies ein No-op: sie belegen
    kein lokales VRAM, brauchen also weder Lock noch Entladen — so blockiert ein
    Remote-Aufruf auch nicht die lokale Generierung."""
    global _loaded_model
    if _llm.is_remote(model):
        yield
        return
    async with _model_lock:
        if _loaded_model and _loaded_model != model:
            await _unload_model(_loaded_model)
        _loaded_model = model
        yield


def _to_slug(name: str, max_len: int = 40) -> str:
    """Erstellt einen sicheren, lesbaren Dateinamen aus einem beliebigen Namen."""
    import re as _re
    s = name.lower().strip()
    s = s.translate(str.maketrans({'ä': 'ae', 'ö': 'oe', 'ü': 'ue', 'ß': 'ss', 'Ä': 'ae', 'Ö': 'oe', 'Ü': 'ue'}))
    s = _re.sub(r'[^a-z0-9]+', '_', s)
    s = s.strip('_')[:max_len]
    return s or 'datei'


def _unique_agent_path(name: str, exclude_id: str = "") -> Path:
    """Gibt einen eindeutigen Dateipfad für einen Agenten zurück."""
    slug = _to_slug(name)
    candidate = AGENTS_DIR / f"{slug}.json"
    if not candidate.exists():
        return candidate
    # Prüfen ob die existierende Datei vom selben Agenten stammt
    if exclude_id:
        try:
            existing = json.loads(candidate.read_text(encoding="utf-8"))
            if existing.get("id") == exclude_id:
                return candidate
        except Exception:
            pass
    # Kollision → Nummer anhängen
    for i in range(2, 999):
        candidate = AGENTS_DIR / f"{slug}_{i}.json"
        if not candidate.exists():
            return candidate
    return AGENTS_DIR / f"{slug}_{uuid.uuid4().hex[:4]}.json"


def _agent_path_by_id(aid: str) -> Optional[Path]:
    """Findet die Datei eines Agenten anhand seiner ID (unabhängig vom Dateinamen)."""
    for fp in AGENTS_DIR.glob("*.json"):
        try:
            data = json.loads(fp.read_text(encoding="utf-8"))
            if data.get("id") == aid:
                return fp
        except Exception:
            pass
    # Fallback: alte ID-basierte Benennung
    legacy = AGENTS_DIR / f"{aid}.json"
    return legacy if legacy.exists() else None


def _load_profile() -> dict:
    if PROFILE_FILE.exists():
        try:
            return json.loads(PROFILE_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def _active_mode() -> str:
    """Aktiver Modus aus dem Profil (Fallback DEFAULT_MODE)."""
    m = str(_load_profile().get("mode", "") or "").lower().strip()
    return m if m in VALID_MODES else DEFAULT_MODE


# Modus-spezifische Branding-Vorlagen. Pro Asset-Zweck („kind") werden mehrere
# Dateinamen (case-insensitiv, PNG/JPG) akzeptiert – so kann der Nutzer die Original-
# dateien aus dem Styleguide 1:1 hineinkopieren.
_MODE_TEMPLATE_NAMES = {
    "cover":   ["Deckblatt", "cover", "deckblatt"],
    "header":  ["inhaltsfolie", "Inhaltsfolie", "header", "kopfzeile"],
    "closing": ["Abschlussfolie", "abschlussfolie", "closing", "abschluss"],
    "logo":    ["logo", "Logo"],
}
_MODE_TEMPLATE_EXTS = [".png", ".jpg", ".jpeg", ".webp"]


def _mode_template_asset(kind: str, mode: str | None = None):
    """Pfad zur firmeneigenen Vorlage für ``kind`` im aktuellen Modus, falls vorhanden.

    Nur „modern_blau" hat aktuell eigene Vorlagen (``weitere Vorlagen/modern_blau/``).
    In anderen Modi ⇒ ``None`` (dann greift das normale Profil-Branding). Auch ``None``,
    wenn keine passende Datei existiert – es entsteht nie ein Fehler."""
    m = (mode or _active_mode())
    if m != "modern_blau":
        return None
    base = MODE_TEMPLATES_DIR / "modern_blau"
    if not base.is_dir():
        return None
    for stem in _MODE_TEMPLATE_NAMES.get(kind, []):
        for ext in _MODE_TEMPLATE_EXTS:
            fp = base / f"{stem}{ext}"
            if fp.exists() and fp.is_file():
                return fp
    return None


# Drei Modell-Rollen im Profil: Allgemein / Programmieren / Wissenschaftlich.
# Jede Rolle kann ein eigenes (bei Bedarf nachgeladenes) LLM zugewiesen bekommen;
# leer → Standardmodell (DEFAULT_MODEL, Standard granite4.2:3b).
_MODEL_ROLES = {
    "general": "model_general",
    "coding":  "model_coding",
    "science": "model_science",
    "medical": "model_medical",
}

# Optionale Tabs, die im Profil ein-/ausgeblendet werden können. Beim ERSTAUFRUF
# (noch kein user_profile.json) sind sie alle ausgeblendet – der Nutzer schaltet
# Gewünschtes im Profil frei.
_OPTIONAL_TABS = {"rag", "ide", "mail", "logs", "medizin", "mathe", "diranalyse", "postfach", "patente", "rechnung", "zeugnis", "morph", "jury"}
# Auf erstem Start verborgene optionale Tabs. Der Installer kann die Vorbelegung über
# config.json ("hidden_tabs_default") setzen (P8); ungültige/unbekannte Tabs werden
# herausgefiltert, Fallback ist „alle optionalen Tabs verbergen".
_cfg_hidden = _CONFIG.get("hidden_tabs_default")
if isinstance(_cfg_hidden, list):
    _DEFAULT_HIDDEN_TABS = [t for t in _cfg_hidden if t in _OPTIONAL_TABS]
else:
    _DEFAULT_HIDDEN_TABS = list(_OPTIONAL_TABS)


def _model_for(role: str) -> str:
    """Das im Profil der Rolle zugewiesene Modell, sonst das Standardmodell.

    Im **Geheim-/Lokal-Modus** wird eine **Remote-Zuweisung ignoriert** (→ lokales
    Standardmodell); eine bereits lokale Rollen-Zuweisung bleibt erhalten."""
    key = _MODEL_ROLES.get(role)
    val = str(_load_profile().get(key, "") or "").strip() if key else ""
    if val and _secret_local() and _llm.is_remote(val) and not _llm.is_local(val):
        val = ""  # lokaler llama.cpp-Anbieter bleibt auch im Geheim-Modus zulässig
    return val or DEFAULT_MODEL


async def _installed_local_models() -> list[str]:
    """Namen der aktuell in Ollama installierten (lokalen) Modelle. Leere Liste,
    wenn Ollama nicht erreichbar ist."""
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(f"{OLLAMA_BASE}/api/tags")
            return [m["name"] for m in resp.json().get("models", [])]
    except Exception:
        return []


async def _local_llm_available() -> bool:
    """Ist mindestens ein lokales LLM installiert und Ollama erreichbar?"""
    return bool(await _installed_local_models())


async def _local_model(preferred: Optional[str] = None) -> Optional[str]:
    """Erzwingt ein LOKALES (nicht-remote) Modell für Funktionen, die ausschließlich
    lokal laufen sollen (Verzeichnis-Analyse, PST-Auswertung, „Recherche lokal").
    Reihenfolge: ``preferred`` (falls lokal & installiert) → general-Rolle (falls lokal)
    → DEFAULT_MODEL → erstes installiertes Modell. ``None``, wenn kein lokales LLM da ist."""
    # Ein als lokal markierter llama.cpp/LM-Studio-Anbieter zählt wie Ollama — und
    # wird sogar akzeptiert, wenn gar kein Ollama läuft (eigener lokaler Server).
    if _llm.is_local(preferred):
        return preferred
    installed = await _installed_local_models()
    if not installed:
        gen = _model_for("general")
        return gen if _llm.is_local(gen) else None
    def _ok(m: Optional[str]) -> bool:
        return bool(m) and ((not _llm.is_remote(m) and m in installed) or _llm.is_local(m))
    if _ok(preferred):
        return preferred
    gen = _model_for("general")
    if _ok(gen):
        return gen
    if DEFAULT_MODEL in installed:
        return DEFAULT_MODEL
    return installed[0]


def _hartman() -> bool:
    """Persona »Gunnery Sergeant Hartman« aktiv? Dieser Antwortstil erzwingt zusätzlich
    einen **kompletten Lokal-Riegel** (nur lokale Modelle, keine Websuche) – unabhängig
    vom Geheim-Button, der dabei unangetastet bleibt."""
    return str(_load_profile().get("tone", "") or "").lower().strip() == "hartman"


def _secret_local() -> bool:
    """Globaler **Geheim-/Lokal-Modus**: sämtliche Modell-Rollen laufen auf lokalen
    Standardmodellen, Remote-Wahlen werden überall verworfen. Übersteuert auch
    ``confidential_allow_api``. Aktiv, wenn der Geheim-Button (Profil ``local_only_mode``)
    an ist **oder** die Persona »Hartman« gewählt ist (die den Lokal-Riegel impliziert,
    ohne den Geheim-Button umzuschalten)."""
    return bool(_load_profile().get("local_only_mode", False)) or _hartman()


def _web_search_allowed() -> bool:
    """Darf überhaupt eine Websuche laufen? In der Persona »Hartman« ist jede Websuche
    gesperrt (alles rein lokal); sonst erlaubt (der Geheim-Modus hält nur das Modell
    lokal, lässt die reine Web-Anfrage aber zu)."""
    return not _hartman()


def _assistant_mode() -> bool:
    """Profil-Flag »🧭 Assistent-Modus«: eine einzige Gesprächsfläche (nur Chat-Tab), in der
    das Modell **selbst entscheidet**, welches Werkzeug es zieht (Code, Web-/Tiefe-Recherche,
    Bild, Diagramm, Präsentation …). Impliziert die erweiterten Chat-Werkzeuge und schaltet das
    Bild-Werkzeug frei. Standard aus — braucht ein fähiges Modell (kleine Modelle → Std-Modus)."""
    return bool(_load_profile().get("assistant_mode", False))


def _chat_agent_tools() -> bool:
    """Profil-Häkchen »Erweiterte Chat-Werkzeuge«: bietet dem Chat-Modell zusätzlich einen
    **Code-Interpreter** (``run_python``, serverseitige Sandbox) und **autonome Web-Recherche**
    an, damit es komplexe Aufgaben rechnend/recherchierend löst. Standard aus — kleine Modelle
    sind mit dem Werkzeug-Loop oft überfordert. Der **Assistent-Modus** impliziert dies."""
    return bool(_load_profile().get("chat_code_interpreter", False)) or _assistant_mode()


def _confidential_api_allowed() -> bool:
    """Profil-Schalter: dürfen die vertraulichen Auswertungen (Verzeichnis-Analyse,
    Postfach) auch API-Modelle nutzen? Standard: aus — Inhalte bleiben lokal.
    Im Geheim-Modus stets aus (alles lokal)."""
    if _secret_local():
        return False
    return bool(_load_profile().get("confidential_allow_api", False))


async def _analysis_model(preferred: Optional[str] = None) -> Optional[str]:
    """Modellwahl für vertrauliche Auswertungen (Verzeichnis-Analyse, Postfach).

    Standard: zwingend lokal (wie :func:`_local_model`). Ist im Profil
    ``confidential_allow_api`` gesetzt UND hat der Nutzer explizit ein
    API-Modell (``provider::modell``) gewählt, wird dieses genutzt — die
    Inhalte gehen dann an den externen Anbieter. Ohne explizite Remote-Wahl
    bleibt es beim Lokal-Zwang; so schaltet der Haken allein noch nichts um."""
    if _confidential_api_allowed():
        m = (preferred or "").strip()
        if m and m not in _MODEL_PLACEHOLDERS and _llm.is_remote(m):
            return m
    return await _local_model(preferred)


async def _vision_model(preferred: Optional[str] = None) -> Optional[str]:
    """Lokales Modell für Bild-/Vision-Aufgaben (Bildanalyse, Präsentation-aus-Bildern,
    Bild-aware RAG). Bevorzugt das vertrauliche Analyse-Modell (``_analysis_model`` →
    Geheim-/Hartman-tauglich, lokal); ist dieses nicht multimodal, wird ein installiertes
    **vision-fähiges** Ollama-Modell bevorzugt (Fähigkeit aus ``/api/tags`` ``capabilities``).
    Rückfall = das Analyse-Modell. ``None`` ⇒ kein lokales LLM (Aufrufer meldet 503/Fehler)."""
    base = await _analysis_model(preferred)
    if not base or _llm.is_remote(base):
        return base   # None → 503; lokaler Server/llama.cpp / erlaubtes API-Modell → so belassen
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            resp = await client.get(f"{OLLAMA_BASE}/api/tags")
            resp.raise_for_status()
            models = resp.json().get("models", [])
        vision = [m.get("name") for m in models
                  if "vision" in (m.get("capabilities") or []) and m.get("name")]
        if base in vision:
            return base
        if vision:
            return vision[0]
    except Exception:
        pass
    return base   # Fallback: Analyse-Modell (evtl. nur Text → schwächere Beschreibung)


def _slide_fields_from_partial(raw: str):
    """Bergungs-Parser für (evtl. ABGESCHNITTENES) Slide-JSON: zieht title, bullets
    und caption per Regex heraus, auch wenn das JSON nie geschlossen wurde. So landet
    bei trunkierter Modellantwort kein roher ``{"title":…``-Text auf der Folie.
    Geteilt (Bildanalyse in Agenten + Präsentation-aus-Bildern) → core."""
    title = ""
    mt = re.search(r'"title"\s*:\s*"((?:[^"\\]|\\.)*)"', raw)
    if mt:
        title = mt.group(1)
    bullets = []
    mb = re.search(r'"bullets"\s*:\s*\[(.*)', raw, re.DOTALL)
    if mb:
        seg = mb.group(1)
        end = seg.find("]")
        if end >= 0:
            seg = seg[:end]
        bullets = re.findall(r'"((?:[^"\\]|\\.)*)"', seg)
    caption = ""
    mc = re.search(r'"caption"\s*:\s*"((?:[^"\\]|\\.)*)"', raw)
    if mc:
        caption = mc.group(1)
    return title, bullets, caption


async def _analyze_image_core(image_b64: str, system_prompt: str = "", filename: str = "",
                              topic: str = "", model: Optional[str] = None,
                              want_notes: bool = False) -> dict:
    """Analysiert EIN Bild mit einem Vision-Modell → strukturierter Folieninhalt
    ``{title, bullets, caption, notes?, descriptive_filename, tokens}``. Geteilter Kern für
    ``/api/analyze-image`` (Agenten) und den Präsentation-aus-Bildern-Generator. Nutzt den
    **Dateinamen als Hinweis** (``is_descriptive_filename``); ``want_notes`` verlangt zusätzlich
    eine kurze Sprechernotiz. ``format:json`` + Kontextfenster verhindern Vorgeplapper."""
    from tools.imaging import downscale, is_descriptive_filename
    system_prompt = (system_prompt or "").strip() or (
        "Du bist ein technischer Fach-Experte und beschreibst das gezeigte Bild "
        "knapp und sachlich auf Deutsch.")
    filename = (filename or "").strip()
    topic = (topic or "").strip()
    _model = _pick_model(model)
    descriptive, label = is_descriptive_filename(filename) if filename else (False, "")
    small = downscale(image_b64)

    name_hint = ""
    if filename:
        if descriptive:
            name_hint = (f"\nDer Dateiname '{label}' ist beschreibend – nutze ihn als Hinweis "
                         f"auf den Bildinhalt und möglichst als Folientitel.")
        else:
            name_hint = (f"\nDer Dateiname ('{filename}') ist nicht aussagekräftig – ignoriere ihn "
                         f"und stütze dich allein auf das, was im Bild zu sehen ist.")

    _notes_field = (',"notes":"eine kurze Sprechernotiz (1-2 Sätze) für den Vortragenden"'
                    if want_notes else "")
    user_text = (
        (f"Kontext der Präsentation: {topic}\n" if topic else "")
        + "Beschreibe dieses Bild für eine Präsentationsfolie."
        + name_hint
        + "\n\nAntworte NUR mit JSON in genau diesem Format, ohne weiteren Text:\n"
        '{"title":"Kurzer Folientitel","bullets":["Stichpunkt 1","Stichpunkt 2","Stichpunkt 3"],'
        '"caption":"Eine kurze Bildunterschrift (max. ein Satz)"' + _notes_field + '}\n'
        "Maximal 3 kurze Stichpunkte (je höchstens ein knapper Satz). "
        "Kein Markdown, keine Sternchen, keine Aufzählungszeichen im Text.")

    async with _model_session(_model), httpx.AsyncClient(timeout=180) as client:
        resp = await _llm.chat(client, {
            "model": _model, "think": False, "format": "json",
            "messages": [{"role": "system", "content": system_prompt},
                         {"role": "user", "content": user_text, "images": [small]}],
            "options": {"num_ctx": _profile_num_ctx()}, "stream": False,
        })
        resp.raise_for_status()
        _ai_j = resp.json()
    _ai_ti, _ai_to = _llm_tok(_ai_j)
    raw = _ai_j.get("message", {}).get("content", "")
    raw = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL).strip()

    def _strip_md(s: str) -> str:
        s = re.sub(r"[*_`#>]+", "", s)
        s = re.sub(r"^\s*[-•]\s*", "", s)
        return re.sub(r"\s+", " ", s).strip()

    title, bullets, caption, notes = (label if descriptive else "Abbildung"), [], "", ""
    data = _parse_llm_json(raw)
    if isinstance(data, dict):
        title = _strip_md(str(data.get("title") or "")) or title
        b = data.get("bullets") or []
        bullets = [_strip_md(str(x)) for x in b if str(x).strip()][:3]
        caption = _strip_md(str(data.get("caption") or ""))
        notes = _strip_md(str(data.get("notes") or ""))
    if not bullets and not caption:
        st, sb, sc = _slide_fields_from_partial(raw)
        if st:
            title = _strip_md(st) or title
        bullets = [_strip_md(str(x)) for x in sb if str(x).strip()][:3]
        caption = _strip_md(sc)
    if not bullets and not caption:
        plain = raw.strip()
        looks_json = plain.startswith("{") or '"bullets"' in plain or '"title"' in plain
        caption = "" if looks_json else _strip_md(plain)[:200]

    out = {"title": title, "bullets": bullets, "caption": caption,
           "descriptive_filename": descriptive, "tokens": {"in": _ai_ti, "out": _ai_to}}
    if want_notes:
        out["notes"] = notes
    return out


# ── Automatische Mathe-Weiche ────────────────────────────────────────────────
# Wunsch: Solange im Chat nur das kleine Standardmodell (DEFAULT_MODEL) aktiv
# ist, sollen erkannte Matheaufgaben automatisch an das (stärkere) Mathe-Modell
# der Rolle „Programmieren / Mathe" durchgereicht werden. Wählt der Nutzer ein
# stärkeres Allgemein-Modell, entfällt die Umleitung (model != DEFAULT_MODEL).
# Steuerbar über das Profil-Häkchen `math_autoroute` (Standard: an).

# Schlüsselwörter, die typisch für eine konkrete Rechen-/Matheaufgabe sind.
_MATH_KEYWORDS = re.compile(
    r"\b(rechne|berechne|errechne|l[öo]se(?:n)?|gleichung(?:ssystem)?|ungleichung|"
    r"integr(?:al|ier)|ableitung|ableiten|differenzier|grenzwert|limes|"
    r"determinante|matrize?n?|vektor(?:en)?|wurzel|logarithm|exponent(?:ial)?|"
    r"nullstelle|extrem(?:um|a|wert)|kurvendiskussion|polynom|faktorisier|"
    r"umstellen|aufl[öo]sen|bruchrechnung|primzahl|fakult[äa]t|"
    r"wahrscheinlichkeit|kombinatorik|binomial|sinus|cosinus|tangens|"
    r"vereinfache|quadratische|terme?n?)\b",
    re.IGNORECASE)

# Starke Symbol-/Operator-Signale (Formel statt nur erwähnter Zahl).
_MATH_SYMBOLS = re.compile(
    r"[=≤≥≠±√∫∑∏∂πθ]"                  # Gleichheits-/Mathe-Sonderzeichen
    r"|\b\d+(?:[.,]\d+)?\s*[\+\-*/^]\s*\d"  # 2+3, 4 * 5, 6^2 …
    r"|\bx\s*\^?\s*\d"                       # x^2, x 2
    r"|\bsqrt\b|\bd/dx\b|\b\d+\s*!"           # sqrt, d/dx, 5!
)


def _looks_like_math(text: str) -> bool:
    """Heuristik: Wirkt die Nachricht wie eine zu rechnende Matheaufgabe?

    Bewusst eher konservativ (Modellwechsel kostet bei ~6 GB VRAM ein Umladen):
    ein klares Symbol-/Operator-Signal ODER ein Mathe-Schlüsselwort genügt.
    Falschtreffer sind unkritisch — sie landen nur auf einem anderen, ebenfalls
    fähigen Modell."""
    t = text or ""
    if not t.strip():
        return False
    return bool(_MATH_SYMBOLS.search(t) or _MATH_KEYWORDS.search(t))


def _math_autoroute_enabled() -> bool:
    return bool(_load_profile().get("math_autoroute", True))


def _research_local_only() -> bool:
    """Profil-Schalter: web-gestützte Recherche zwingend lokal, auch wenn die
    zugewiesene Rolle ein externes API-Modell ist (Datenschutz / API-Beschränkungen).

    Gilt für Recherche-Tab, Matrix-Recherche, erweiterte Suche (``/such``),
    Deepdive und die Patent-Analyse. Der Geheim-Modus impliziert dies."""
    return _secret_local() or bool(_load_profile().get("research_local_only", False))


async def _research_model(preferred: Optional[str] = None,
                          fallback: Optional[str] = None) -> tuple:
    """Modellwahl für web-gestützte Recherche.

    Ist der Profil-Schalter „Web-Recherche lokal" gesetzt und das gewählte Modell
    extern, wird auf ein lokales Modell umgebogen. Rückgabe ``(modell, fehler)`` —
    ``fehler`` ist gesetzt, wenn umgebogen werden müsste, aber kein lokales LLM
    installiert ist."""
    m = _pick_model(preferred, fallback or _model_for("science"))
    if _research_local_only() and _llm.is_remote(m) and not _llm.is_local(m):
        loc = await _local_model(m)
        if not loc:
            return None, ('Kein lokales LLM verfügbar – „Web-Recherche lokal" ist '
                          'im Profil aktiv.')
        return loc, None
    return m, None


async def _research_fallback_model(model: str) -> Optional[str]:
    """Ersatzmodell, wenn ein API-Modell die Web-Recherche verweigert oder scheitert.

    Manche Anbieter unterbinden Web-/Tool-Nutzung oder liefern dabei Fehler. In dem
    Fall wird die Recherche einmalig lokal wiederholt. Für bereits lokale Modelle
    gibt es nichts zu tun (``None``) — ein zweiter Versuch brächte dasselbe
    Ergebnis."""
    if not model or not _llm.is_remote(model):
        return None
    return await _local_model(None)


async def _research_llm_json(model: str, system: str, prompt: str,
                             timeout: float = 120.0) -> tuple:
    """Ein JSON-Aufruf für Recherchezwecke — mit automatischem Rückfall auf ein
    lokales Modell, falls das API-Modell scheitert oder unbrauchbar antwortet
    (manche Anbieter unterbinden web-/toolgestützte Recherche).

    Rückgabe ``(daten, tokens_in, tokens_out, benutztes_modell)``; ``daten`` ist
    ``{}``, wenn auch der lokale Versuch nichts Verwertbares liefert."""
    attempts = [model]
    fb = await _research_fallback_model(model)
    if fb and fb != model:
        attempts.append(fb)
    for m in attempts:
        try:
            async with _model_session(m), httpx.AsyncClient(timeout=timeout) as client:
                resp = await _llm.chat(client, {
                    "model": m, "think": False, "format": "json",
                    "messages": [{"role": "system", "content": system},
                                 {"role": "user", "content": prompt}],
                    "stream": False, "keep_alive": KEEP_ALIVE,
                })
                resp.raise_for_status()
                j = resp.json()
            ti, to = _llm_tok(j)
            data = _parse_llm_json(j.get("message", {}).get("content", "")) or {}
            if data:
                return data, ti, to, m
        except Exception:
            pass   # nächster Versuch (lokaler Rückfall)
    return {}, 0, 0, model


def _profile_num_ctx() -> int:
    """Im Profil gewähltes Kontextfenster (Tokens), validiert gegen die erlaubten
    Stufen. Wird auf ALLE lokalen Modellaufrufe eines Ablaufs angewandt, damit das
    gewählte Fenster wirklich greift und das Modell nicht zwischen Schritten mit
    unterschiedlichem num_ctx neu geladen wird."""
    try:
        n = int(_load_profile().get("chat_num_ctx", CHAT_NUM_CTX))
    except (TypeError, ValueError):
        n = CHAT_NUM_CTX
    return n if n in _ALLOWED_NUM_CTX else CHAT_NUM_CTX


# Immer aktive Grundregel gegen Halluzinationen (unabhängig vom Modus)
_BASE_GUARD = (
    "Erfinde niemals Fakten, Zahlen, Normen (z. B. DIN/VDI), Quellen oder technische "
    "Daten. Stütze dich ausschließlich auf gesichertes Wissen und auf die Ergebnisse "
    "aufgerufener Tools. Fehlt dir eine Information, sage das offen – rate nicht. "
    "WICHTIG: Konkrete Detailangaben (genaue PS-/kW-Werte, Baujahre, Maße, Gewichte, "
    "Preise, Datumsangaben, Eigennamen) aus dem Gedächtnis sind oft unzuverlässig. Wenn "
    "ein Recherche-Werkzeug (web_search) verfügbar ist, belege solche Angaben damit; ist "
    "keines verfügbar, kennzeichne sie ausdrücklich als ungefähr/ohne Gewähr, statt sie "
    "als gesicherte Fakten zu behaupten."
)

# Formeln/Gleichungen als LaTeX mit Formelzeichen ausgeben (im Frontend gerendert)
_FORMULA_RULE = (
    "Gib Berechnungen und Gleichungen als LaTeX-Formeln mit den üblichen Formelzeichen "
    "aus – inline mit $…$ und abgesetzt mit $$…$$ (z. B. $\\sigma = \\dfrac{F}{A}$). "
    "Benenne die verwendeten Formelzeichen kurz mit Einheit und setze konkrete Werte "
    "ein, statt nur Zahlen ohne Symbole zu schreiben."
)

# Funktionen/Kennlinien zusätzlich als Graph zeichnen (tone-/modusunabhängig)
_PLOT_RULE = (
    "Wenn der Nutzer eine mathematische Funktion zum Zeichnen nennt (etwa plotte "
    "f(x)=x^2 oder zeichne sin(x)), zeichnet die App den Graphen automatisch selbst – "
    "du musst dafür KEIN Werkzeug aufrufen und keine ASCII-Kurve malen. Beschreibe die "
    "Funktion höchstens kurz; der Graph wird ohnehin angezeigt."
)

# Ablauf-/Datenfluss-/Systemdiagramme grafisch darstellen (tone-/modusunabhängig)
_DIAGRAM_RULE = (
    "Wenn du einen Prozessablauf, Datenfluss, eine Systemarchitektur, Zustandsmaschine "
    "oder Beziehungen zwischen Komponenten zeigst, nutze das Werkzeug create_diagram "
    "mit Mermaid-Syntax statt einer reinen Textdarstellung – die App rendert das "
    "Diagramm direkt als Grafik."
)

# Zitate von Normen/Gesetzen/Quellen als Link (Detail-URLs ergänzt die App selbst)
_CITATION_RULE = (
    "Wenn du eine Norm (DIN/EN/ISO/IEC/VDI/VDE), einen Gesetzes- oder Paragrafenverweis "
    "(z. B. § 433 BGB) oder die Quelle einer Formel nennst, benenne sie eindeutig "
    "(Bezeichnung samt Nummer). Erfinde keine URLs – die Anwendung verlinkt erkannte "
    "Norm- und Gesetzesangaben automatisch auf maßgebliche Quellen."
)


# Wissenschaftsmodus – fest für Recherche/Matrix/Planer-Recherche (Korrektheit zuerst)
_SCIENCE_PROMPT = (
    "WISSENSCHAFTSMODUS – höchste Sorgfalt und Korrektheit. Stütze JEDE Aussage auf die "
    "vorliegenden Quellen bzw. Suchergebnisse und kennzeichne Unsicherheiten ausdrücklich. "
    "Unterscheide klar zwischen gesicherten Fakten, herrschender Meinung und Annahmen. "
    "Erfinde nichts und gib keine Behauptung ohne Beleg wieder. Nenne Quellen mit Titel und "
    "Link. Bei Normen/Gesetzen die genaue Bezeichnung angeben. Strukturiere sachlich "
    "(Überblick, Befunde mit Belegen, offene Fragen). "
    "Sprich den Nutzer NICHT persönlich an: keine Anrede (kein 'Sehr geehrte', kein 'Hallo', "
    "keine Namensnennung) – formuliere durchgehend neutral und sachlich."
)


_RAG_OPTIMIZE_SYSTEM = (
    "Du bist ein Spezialist für die Aufbereitung von Texten für semantische Suche (RAG). "
    "Überarbeite den gegebenen Textabschnitt so, dass er bei einer Vektorsuche optimal "
    "gefunden werden kann: Löse Abkürzungen auf, wandle Tabellen und Listen in klaren "
    "Fließtext um, ergänze fehlenden Kontext (z.B. Einheit, Bauteil, Produktname) direkt "
    "im Satz, entferne Kopf-/Fußzeilen und Seitenzahlen, korrigiere OCR-Fehler. "
    "Erhalte ALLE fachlichen Informationen vollständig – kürze nichts und erfinde nichts. "
    "Antworte NUR mit dem überarbeiteten Text, ohne Erklärungen oder Kommentare."
)

_LANG_RULE_EN = (
    "Always respond in English, regardless of the language of the question, "
    "unless the user explicitly asks for a reply in another language."
)


def _lang_rule() -> str:
    """Sprachanweisung für die Antwort, abhängig von der Profil-Sprache.
    Leer für Deutsch (Standardverhalten der Modelle)."""
    if str(_load_profile().get("lang", "de")).lower().strip() == "en":
        return _LANG_RULE_EN
    return ""


def _augment_prefix(user_text: str = "") -> str:
    """Baut den automatischen System-Prompt-Vorspann (Grundregel, Modus-Brille,
    Persona/Profil, Formel- und Zitatregeln). Bei „LLM pur" (keine Modi) entfällt
    alles bis auf die Sprachregel – die gewählte Antwortsprache gilt immer."""
    if _load_profile().get("pure_llm"):
        return _lang_rule()
    parts = [_BASE_GUARD, _mode_prefix(user_text), _persona_prefix(),
             _FORMULA_RULE, _PLOT_RULE, _DIAGRAM_RULE, _CITATION_RULE, _lang_rule()]
    return "\n\n".join(p for p in parts if p)

# Stichwörter je Modus: Die Fachbrille wird nur angewandt, wenn die aktuelle
# Frage thematisch dazu passt (siehe _mode_prefix). So bekommt z. B. eine
# Routenfrage im Maschinenbau-Modus keine erzwungenen VDI/DIN-Bezüge.
_MODE_KEYWORDS = {
    "maschinenbau": [
        "schraube", "schrauben", "welle", "lager", "getriebe", "konstruktion",
        "fertigung", "werkstoff", "stahl", "aluminium", "titan", "festigkeit",
        "spannung", "biegung", "torsion", "drehmoment", "belastung", "toleranz",
        "passung", "bauteil", "maschine", "antrieb", "zahnrad", "feder", "niet",
        "schweißen", "fräsen", "drehen", "bohren", "statik", "dynamik",
        "kinematik", "ingenieur", "vdi", "din", "iso", "cad", "kn", "mpa", "nm",
        "funktion", "graph", "plot", "plotten", "diagramm", "kennlinie", "verlauf", "kurve",
    ],
    "ki": [
        "ki", "künstliche intelligenz", "machine learning", "ml", "deep learning",
        "neuronales netz", "neuronale", "llm", "training", "trainieren",
        "datensatz", "klassifikation", "regression", "feature", "embedding",
        "transformer", "datenanalyse", "mlops", "algorithmus", "overfitting",
        "tensor", "gradient", "modell",
    ],
    "soziales": [
        "sozial", "soziale arbeit", "betreuung", "klient", "jugend", "senioren",
        "pflege", "bildung", "teilhabe", "inklusion", "gemeinwohl", "ehrenamt",
        "beratung", "förderung", "integration", "gemeinnützig", "träger", "kita",
    ],
    "marketing": [
        "marketing", "kampagne", "zielgruppe", "marke", "branding", "conversion",
        "social media", "content", "seo", "werbung", "anzeige", "positionierung",
        "botschaft", "reichweite", "funnel", "newsletter", "slogan", "kanal",
    ],
    "finanz": [
        "budget", "kosten", "umsatz", "gewinn", "marge", "liquidität", "cashflow",
        "bilanz", "controlling", "kennzahl", "roi", "rendite", "investition",
        "finanzierung", "kredit", "zins", "abschreibung", "kalkulation", "ebit",
        "rentabilität", "steuer", "buchhaltung", "euro",
    ],
    "geschaeftsfuehrung": [
        "strategie", "geschäftsmodell", "vision", "kpi", "management", "führung",
        "entscheidung", "wettbewerb", "wachstum", "stakeholder", "roadmap", "swot",
        "geschäftsführung", "unternehmen", "expansion", "marktanteil",
    ],
}


def _mode_keywords(mode: str) -> list:
    """Stichwörter eines Modus. Für den frei konfigurierbaren Modus „custom"
    aus dem Profilfeld ``custom_mode_keywords`` (Komma-/Zeilen-getrennt)."""
    if mode == "custom":
        raw = _load_profile().get("custom_mode_keywords", "") or ""
        return [w.strip().lower() for w in re.split(r"[,\n;]+", str(raw)) if w.strip()]
    return _MODE_KEYWORDS.get(mode, [])


def _mode_prompt_text(mode: str) -> str:
    """Fachkontext-Text eines Modus. Für „custom" aus dem Profilfeld
    ``custom_mode_prompt`` (vom Nutzer frei formuliert)."""
    if mode == "custom":
        return str(_load_profile().get("custom_mode_prompt", "") or "").strip()
    return _MODE_PROMPTS.get(mode, "")


def _mode_matches(text: str, mode: str) -> bool:
    """True, wenn der Text ein Stichwort des Modus als ganzes Wort enthält."""
    kws = _mode_keywords(mode)
    t = (text or "").lower()
    return any(re.search(r"\b" + re.escape(kw) + r"\b", t) for kw in kws)


def _mode_prefix(user_text: str = "") -> str:
    """System-Prompt-Präfix für den aktiven Modus.

    Leer, wenn der Modus abgeschaltet ist oder – sofern ein ``user_text``
    übergeben wird – die Frage thematisch nicht zum Modus passt (frageabhängige
    Fachbrille). Ohne ``user_text`` wird der Modus immer angewandt.
    Beim frei konfigurierbaren Modus „custom" ohne hinterlegte Stichwörter
    greift die Fachbrille immer (keine Stichwort-Gatterung)."""
    prof = _load_profile()
    if prof.get("mode_prompt") is False:   # ausdrücklich abgeschaltet
        return ""
    mode = _active_mode()
    mp = _mode_prompt_text(mode)
    if not mp:
        return ""
    if user_text and _mode_keywords(mode) and not _mode_matches(user_text, mode):
        return ""
    return mp


def _tone_prompt() -> str:
    """Antwortstil-Präfix für die im Profil gewählte Persona (leer = neutral)."""
    t = str(_load_profile().get("tone", "") or "").lower().strip()
    return _TONE_PROMPTS.get(t, "")


def _profile_context() -> str:
    """Beschreibt den Nutzer für passende Anrede und Kontext (Name, Position,
    Abteilung, Firma) – u. a. für Recherche und Präsentationen. Leer, wenn das
    Profil keine verwertbaren Angaben enthält."""
    p = _load_profile()
    name = " ".join(x for x in (
        str(p.get("first_name", "")).strip(), str(p.get("last_name", "")).strip()) if x)
    role = ", ".join(x for x in (
        str(p.get("position", "")).strip(), str(p.get("department", "")).strip()) if x)
    company = str(p.get("company", "")).strip()
    facts = []
    if name:
        facts.append(f"Name: {name}")
    if role:
        facts.append(f"Funktion: {role}")
    if company:
        facts.append(f"Organisation: {company}")
    if not facts:
        return ""
    return (
        "Angaben zum Nutzer (für persönliche Anrede sowie als Kontext bei Recherche "
        "und Präsentationen) – " + "; ".join(facts) + "."
    )


def _persona_prefix() -> str:
    """Kombiniert Profil-Kontext und Antwortstil zu einem System-Prompt-Präfix."""
    return "\n\n".join(p for p in (_profile_context(), _tone_prompt()) if p)


def _load_projects() -> list:
    if PROJECTS_FILE.exists():
        try:
            return json.loads(PROJECTS_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return []


def _save_projects(projects: list) -> None:
    PROJECTS_FILE.write_text(
        json.dumps(projects, ensure_ascii=False, indent=2), encoding="utf-8")


# Projekt-Status im Angebots-/Rechnungs-Workflow (Planer ↔ Angebot/Rechnung).
_PROJECT_STATUS_LABELS = {
    "planung": "In Planung",
    "angebot_frei": "Für Angebot freigegeben",
    "angebot": "Angebot erstellt / in Bearbeitung",
    "rechnung_frei": "Für Rechnung freigegeben",
    "abgerechnet": "Abgerechnet",
}


def _update_project_fields(pid: str, **fields) -> Optional[dict]:
    """Setzt Felder eines Projekts (nur nicht-``None``-Werte) und speichert.
    Gibt das aktualisierte Projekt zurück (oder ``None``, wenn unbekannt)."""
    if not pid:
        return None
    projects = _load_projects()
    hit = None
    for p in projects:
        if p.get("id") == pid:
            for k, v in fields.items():
                if v is not None:
                    p[k] = v
            p["updated_at"] = time.time()
            hit = p
            break
    if hit is not None:
        _save_projects(projects)
    return hit


def _sse(data: dict) -> str:
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"

def _llm_tok(j: dict) -> tuple:
    """(prompt_tokens, completion_tokens) aus einer Ollama-förmigen Antwort."""
    return int((j or {}).get("prompt_eval_count") or 0), int((j or {}).get("eval_count") or 0)


def _parse_llm_json(raw: str) -> Optional[dict]:
    """Strippt <think>/Code-Fences und extrahiert das erste JSON-Objekt."""
    raw = re.sub(r"<think>.*?</think>", "", raw or "", flags=re.DOTALL)
    m = re.search(r"\{[\s\S]*\}", raw)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except Exception:
        return None


# ── Globale Kapazitätsliste ───────────────────────────────────────────────────
# Tab-übergreifende Liste von Ressourcen/Partnern auf Basis des Planer-Schemas
# ({kind,name,rate}), erweitert um Land/Region, freie Kapazität (h) und Skills.
# Wird vom Planer (Katalog-Import) und der Anfrage-Auswertung (RFQ) genutzt.

def _coerce_capacity(item: dict) -> dict:
    """Bringt einen Kapazitätseintrag auf das interne Schema und säubert Typen."""
    name = str(item.get("name", "")).strip()[:120]
    kind = str(item.get("kind", "human")).lower().strip()
    if kind not in ("human", "hardware", "software"):
        kind = _classify_resource_kind(name) or "human"
    def _num(v):
        try:
            return max(0, float(v))
        except (TypeError, ValueError):
            return 0
    return {
        "kind": kind,
        "name": name,
        "rate": _num(item.get("rate", 0)),
        "country": str(item.get("country", "")).strip()[:60],
        "capacity_h": _num(item.get("capacity_h", 0)),
        "skills": str(item.get("skills", "")).strip()[:300],
    }


# ── Mehrere benannte Ressourcen-/Kapazitätslisten ──────────────────────────────
# Datenmodell: data/capacity_lists.json = {lists:[{id,name,items,updated_at}],
# selected:[ids]}. Mehrere Listen sind per Häkchen aktivierbar; _load_capacity()
# liefert die VEREINIGUNG der aktiven Listen (dedupliziert nach Name). Migration
# aus der alten Einzelliste capacity.json erfolgt transparent beim ersten Zugriff.
CAP_LISTS_FILE = DATA_DIR / "capacity_lists.json"


def _load_capacity_file() -> list:
    """Liest die alte Einzelliste (capacity.json) — nur noch für die Migration."""
    if not CAPACITY_FILE.exists():
        return []
    try:
        data = json.loads(CAPACITY_FILE.read_text(encoding="utf-8"))
    except Exception:
        return []
    items = data.get("items") if isinstance(data, dict) else data
    return [_coerce_capacity(i) for i in items if isinstance(i, dict) and str(i.get("name", "")).strip()] if isinstance(items, list) else []


def _coerce_cap_list(lst: dict) -> dict:
    items = lst.get("items") if isinstance(lst, dict) else None
    items = items if isinstance(items, list) else []
    return {
        "id": str(lst.get("id") or uuid.uuid4().hex[:12]),
        "name": str(lst.get("name", "")).strip()[:80] or "Liste",
        "items": [_coerce_capacity(i) for i in items
                  if isinstance(i, dict) and str(i.get("name", "")).strip()],
        "updated_at": lst.get("updated_at") or time.time(),
    }


def _save_cap_lists(data: dict) -> dict:
    lists = [_coerce_cap_list(l) for l in (data.get("lists") or []) if isinstance(l, dict)]
    ids = {l["id"] for l in lists}
    selected = [s for s in (data.get("selected") or []) if s in ids]
    out = {"lists": lists, "selected": selected, "updated_at": time.time()}
    CAP_LISTS_FILE.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    return out


def _load_cap_lists() -> dict:
    """Liefert {lists, selected} mit transparenter Migration aus capacity.json."""
    if CAP_LISTS_FILE.exists():
        try:
            data = json.loads(CAP_LISTS_FILE.read_text(encoding="utf-8"))
        except Exception:
            data = {}
        lists = [_coerce_cap_list(l) for l in (data.get("lists") or []) if isinstance(l, dict)]
        ids = {l["id"] for l in lists}
        selected = [s for s in (data.get("selected") or []) if s in ids]
        if not selected and lists:
            selected = [lists[0]["id"]]
        return {"lists": lists, "selected": selected}
    # Migration: alte Einzelliste → eine Liste „Standard"
    legacy = _load_capacity_file()
    migrated = {"lists": [{"id": "standard", "name": "Standard",
                           "items": legacy, "updated_at": time.time()}],
                "selected": ["standard"]}
    return _save_cap_lists(migrated)


def _load_capacity() -> list:
    """Vereinigung der aktiven (angehakten) Listen, dedupliziert nach Name."""
    data = _load_cap_lists()
    sel = set(data["selected"])
    seen, out = set(), []
    for l in data["lists"]:
        if l["id"] not in sel:
            continue
        for it in l["items"]:
            key = it["name"].strip().lower()
            if not key or key in seen:
                continue
            seen.add(key)
            out.append(it)
    return out


def _save_capacity(items: list) -> list:
    """Rückwärtskompatibel: speichert in die erste aktive Liste (legt „Standard" an,
    falls noch keine existiert). Wird vom alten Einzel-Editor genutzt."""
    clean = [_coerce_capacity(i) for i in items
             if isinstance(i, dict) and str(i.get("name", "")).strip()]
    data = _load_cap_lists()
    target = data["selected"][0] if data["selected"] else (data["lists"][0]["id"] if data["lists"] else None)
    if target is None:
        data["lists"].append({"id": "standard", "name": "Standard", "items": clean, "updated_at": time.time()})
        data["selected"] = ["standard"]
    else:
        for l in data["lists"]:
            if l["id"] == target:
                l["items"] = clean
                l["updated_at"] = time.time()
    _save_cap_lists(data)
    return clean


def _capacity_context(items: list = None) -> str:
    """Kompakter Listentext der Kapazitäten für den Auswertungs-Prompt."""
    items = _load_capacity() if items is None else items
    if not items:
        return ""
    lines = []
    for it in items:
        parts = [it["name"]]
        if it.get("skills"):
            parts.append(f"Skills: {it['skills']}")
        if it.get("country"):
            parts.append(f"Land: {it['country']}")
        if it.get("capacity_h"):
            parts.append(f"frei: {it['capacity_h']:g} h")
        lines.append("- " + " · ".join(parts))
    return "\n".join(lines)


async def _derive_adaptive_prompt(user_text: str, model: str, num_ctx: int = CHAT_NUM_CTX,
                                  tok: Optional[dict] = None):
    """Leitet aus der Nutzerfrage einen fragespezifischen Experten-System-Prompt ab.
    Rückgabe: (rolle, system_prompt) – bei Fehler ("", "").
    ``tok`` (optional): dict {"in","out"}, in das der Tokenverbrauch summiert wird.

    Nutzt dasselbe num_ctx wie die anschließende Antwort, damit Ollama das Modell
    nicht erst mit kleinem Fenster lädt und danach für die Antwort neu laden muss."""
    if not (user_text or "").strip():
        return "", ""
    try:
        async with _model_session(model), httpx.AsyncClient(timeout=120) as client:
            resp = await _llm.chat(client,{
                "model": model,
                "think": False,
                "stream": False,
                "options": {"num_ctx": num_ctx},
                "keep_alive": KEEP_ALIVE,
                "messages": [
                    {"role": "system", "content": (
                        "Bestimme den am besten geeigneten Fach-Experten, um die Frage des Nutzers "
                        "zu beantworten. Antworte NUR mit JSON in genau diesem Format, ohne weiteren "
                        'Text: {"rolle":"Kurzbezeichnung des Experten","system_prompt":"Du bist ein '
                        '... und antwortest ... auf Deutsch."}'
                    )},
                    {"role": "user", "content": f"Frage des Nutzers:\n{(user_text or '')[:1500]}"},
                ],
            })
            resp.raise_for_status()
            _ad_j = resp.json()
            if tok is not None:
                _a, _b = _llm_tok(_ad_j)
                tok["in"] += _a
                tok["out"] += _b
            raw = _ad_j.get("message", {}).get("content", "")
    except Exception:
        return "", ""
    raw = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL)
    m = re.search(r"\{[\s\S]*\}", raw)
    if not m:
        return "", ""
    try:
        data = json.loads(m.group(0))
    except Exception:
        return "", ""
    return (data.get("rolle") or "Experte").strip(), (data.get("system_prompt") or "").strip()


def _safe_exec(code: str) -> str:
    import math

    safe_builtins = {
        "print": print, "range": range, "len": len, "abs": abs, "round": round,
        "min": min, "max": max, "sum": sum, "int": int, "float": float,
        "str": str, "bool": bool, "list": list, "dict": dict, "tuple": tuple,
        "set": set, "enumerate": enumerate, "zip": zip, "sorted": sorted,
        "reversed": reversed, "map": map, "filter": filter, "type": type,
        "__import__": __import__,
    }

    safe_globals: dict = {"__builtins__": safe_builtins, "math": math}

    try:
        import numpy as np  # type: ignore
        safe_globals["np"] = np
        safe_globals["numpy"] = np
    except ImportError:
        pass

    try:
        import scipy  # type: ignore
        safe_globals["scipy"] = scipy
        import scipy.optimize as _opt  # type: ignore
        import scipy.linalg as _linalg  # type: ignore
        safe_globals["scipy_optimize"] = _opt
        safe_globals["scipy_linalg"] = _linalg
    except ImportError:
        pass

    try:
        import sympy as _sym  # type: ignore
        safe_globals["sympy"] = _sym
        safe_globals["sp"] = _sym
    except ImportError:
        pass

    buf = io.StringIO()
    old_stdout = sys.stdout
    sys.stdout = buf
    try:
        exec(code, safe_globals)  # noqa: S102
    except Exception as exc:
        sys.stdout = old_stdout
        return f"Fehler: {exc}"
    sys.stdout = old_stdout
    return buf.getvalue() or "OK (kein Output)"


def _run_python_code(code: str, timeout: float = 15.0) -> dict:
    """Führt Python-Code für den Code-Tab aus und liefert stdout/stderr, etwaige
    matplotlib-Figuren (als PNG-Data-URIs) und Fehler strukturiert zurück.

    Wie ``_safe_exec`` läuft der Code mit eingeschränkten, kuratierten Modulen
    (kein Datei-/Netzwerkzugriff vorgesehen) und einem Zeitlimit. Die Ausführung
    erfolgt in einem Worker-Thread, damit das Zeitlimit greift; bei Überschreitung
    kehrt der Aufruf zurück (der Thread läuft im Hintergrund aus)."""
    import contextlib
    import traceback as _tb
    from concurrent.futures import ThreadPoolExecutor, TimeoutError as _FTimeout

    result = {"output": "", "error": "", "images": []}

    def _work() -> tuple[str, list[str]]:
        import math, statistics, random, datetime, json as _json, re as _re
        import itertools, functools, collections
        g: dict = {
            "__name__": "__main__", "math": math, "statistics": statistics,
            "random": random, "datetime": datetime, "json": _json, "re": _re,
            "itertools": itertools, "functools": functools, "collections": collections,
        }
        # Wissenschafts-Stack optional vorladen (wie in _safe_exec)
        for _mod, _alias in (("numpy", "np"), ("scipy", None), ("sympy", "sp"), ("pandas", "pd")):
            try:
                m = __import__(_mod)
                g[_mod] = m
                if _alias:
                    g[_alias] = m
            except Exception:
                pass
        plt = None
        try:
            import matplotlib
            matplotlib.use("Agg")          # headless – keine GUI nötig
            import matplotlib.pyplot as plt  # type: ignore
            g["matplotlib"] = matplotlib
            g["plt"] = plt
        except Exception:
            plt = None

        out_buf = io.StringIO()
        with contextlib.redirect_stdout(out_buf), contextlib.redirect_stderr(out_buf):
            exec(code, g)  # noqa: S102

        imgs: list[str] = []
        if plt is not None:
            try:
                for num in plt.get_fignums():
                    fig = plt.figure(num)
                    b = io.BytesIO()
                    fig.savefig(b, format="png", dpi=110, bbox_inches="tight")
                    imgs.append("data:image/png;base64," + base64.b64encode(b.getvalue()).decode())
                plt.close("all")
            except Exception:
                pass
        return out_buf.getvalue(), imgs

    ex = ThreadPoolExecutor(max_workers=1)
    try:
        fut = ex.submit(_work)
        out, imgs = fut.result(timeout=timeout)
        result["output"] = out
        result["images"] = imgs
    except _FTimeout:
        result["error"] = f"Zeitlimit ({int(timeout)} s) überschritten – Ausführung abgebrochen."
    except Exception:
        # Nutzer-Tracebacks ohne internen Rahmen zeigen
        result["error"] = _tb.format_exc(limit=2)
    finally:
        ex.shutdown(wait=False)   # bei Timeout nicht auf den Thread warten
    return result


def _extract_inline_tool_calls(content: str) -> list:
    """Parst <call_tool>{...}</call_tool> und ähnliche Inline-Formate."""
    import re
    calls = []

    # Format: <call_tool>{"name": "...", "arguments": {...}}</call_tool>
    for m in re.finditer(r'<call_tool>\s*(\{.*?\})\s*</call_tool>', content, re.DOTALL):
        try:
            data = json.loads(m.group(1))
            name = data.get("name") or data.get("function")
            args = data.get("arguments") or data.get("parameters") or {}
            if name:
                calls.append({"function": {"name": name, "arguments": args}})
        except Exception:
            pass

    # Format: <tool_call>{"name": "...", "arguments": {...}}</tool_call>
    for m in re.finditer(r'<tool_call>\s*(\{.*?\})\s*</tool_call>', content, re.DOTALL):
        try:
            data = json.loads(m.group(1))
            name = data.get("name") or data.get("function")
            args = data.get("arguments") or data.get("parameters") or {}
            if name:
                calls.append({"function": {"name": name, "arguments": args}})
        except Exception:
            pass

    return calls


def _strip_inline_tool_calls(content: str) -> str:
    import re
    content = re.sub(r'<call_tool>.*?</call_tool>', '', content, flags=re.DOTALL)
    content = re.sub(r'<tool_call>.*?</tool_call>', '', content, flags=re.DOTALL)
    return content.strip()


def _extract_code_block(text: str) -> Optional[str]:
    """Extrahiert den größten ```-Codeblock aus einer Antwort — für den Programmier-
    Agenten, dessen Code in die Code-IDE übernommen wird. Gibt None zurück, wenn
    kein nennenswerter Codeblock enthalten ist."""
    blocks = re.findall(r"```[ \t]*[a-zA-Z0-9_+-]*[ \t]*\n?([\s\S]*?)```", text)
    blocks = [b.strip() for b in blocks if b.strip()]
    if not blocks:
        return None
    code = max(blocks, key=len)
    return code if len(code) > 10 else None


def _med_transcript(messages: list) -> str:
    """Formt den bisherigen Verlauf in einen lesbaren Gesprächstext.
    Von Medizin- und Mathe-Tutor genutzt (cross-feature) → core."""
    lines = []
    for m in messages:
        role = m.get("role")
        content = str(m.get("content", "")).strip()
        if not content:
            continue
        if role == "user":
            lines.append(f"Patient: {content}")
        elif role == "assistant":
            lines.append(f"Assistent: {content}")
    return "\n".join(lines)


def _todo_root_name() -> str:
    """Name des Wurzelprojekts = Benutzername aus dem Profil (Rückfall: default_project).
    In core, weil der @startup-Handler in main.py ihn braucht (todo-Router ebenso)."""
    p = _load_profile()
    nm = " ".join(x for x in (
        str(p.get("first_name", "")).strip(), str(p.get("last_name", "")).strip()) if x).strip()
    return nm or str(p.get("default_project", "")).strip() or "Meine To-Dos"


class AgentDef(BaseModel):
    id: Optional[str] = None
    name: str
    description: str
    system_prompt: str
    tools: List[str] = ["web_search", "calculate"]
    model: Optional[str] = None
    icon: str = "🤖"
    category: str = "Sonstige"
    favorite: bool = False   # nur Favoriten erscheinen im Sidebar-Agentenselektor
    # Fest an den Agenten gebundene Wissensdatenbanken (z. B. ein Gesetzes-/Regel-
    # Agent mit hinterlegtem Normtext); werden im Chat automatisch aktiviert.
    rag_collections: List[str] = []
    # Optionaler Beispielcode (für Coding-Agenten): wird dem Code-Assistenten als
    # Stil-/Struktur-Vorlage mitgegeben.
    example_code: Optional[str] = ""
    # Optionale Projekt-Zuordnung (z. B. von „/plan" gemeinsam mit Plan & Jury angelegt).
    project_id: Optional[str] = None


def _is_image(fp: Path) -> bool:
    """Bild-Endung? Cross-Feature (Chat-Upload, Medizin, Postfach) → core."""
    return fp.suffix.lower() in {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"}


def _extract_text(fp: Path) -> str:
    """Dokument→Text (tools.files.extract). Cross-Feature (RAG, Verzeichnis, Chat-Upload,
    Postfach, To-Do, Planer) → in core."""
    try:
        from tools.files import extract
        return extract(fp)
    except Exception as e:
        return f"[Lesefehler: {e}]"


def _norm_name(s: str) -> str:
    """Namen normalisieren (cross-feature: Planer + RFQ Katalog-Matching)."""
    return re.sub(r"\s+", " ", str(s or "").lower().strip())


def _match_catalog(name: str, catalog: list):
    """Findet eine Katalog-Ressource zum Namen (exakt oder als Teilstring)."""
    n = _norm_name(name)
    if not n:
        return None
    for c in catalog:
        if _norm_name(c.get("name")) == n:
            return c
    for c in catalog:
        cn = _norm_name(c.get("name"))
        if cn and (cn in n or n in cn):
            return c
    return None


# ── Bildgenerierung (lokal SD-WebUI und/oder API) ─────────────────────────────
# Analog zur TTS oben: im Profil wird ein Bildmodell gewählt.
#   • ``local::sd``  → lokaler Stable-Diffusion-WebUI-Server (AUTOMATIC1111/Forge),
#     Endpoint ``/sdapi/v1/txt2img``, URL im Profil (``sd_webui_url``). Vom Nutzer
#     betrieben – keine neue Abhängigkeit, kein Ollama-VRAM-Guard nötig (separater
#     Server). Immer erlaubt, auch im Geheim-Modus.
#   • ``<anbieter>::<modell>`` → OpenAI-kompatibles ``/images/generations``
#     (z. B. ``openai::dall-e-3`` / ``openai::gpt-image-1``). Im Geheim-Modus gesperrt.
# Bild ≠ Token-Strom → kein TokenMeter (wie Audio).

# Größen-Voreinstellungen: (Breite, Höhe) für lokal; API-Größenstring je Modellfamilie.
_IMAGE_SIZES = {
    "square":    {"label": "Quadrat (1:1)",  "wh": (1024, 1024)},
    "landscape": {"label": "Quer (16:9)",    "wh": (1792, 1024)},
    "portrait":  {"label": "Hoch (9:16)",    "wh": (1024, 1792)},
}


def _image_model() -> str:
    """Im Profil gewähltes Bildmodell (``local::sd`` / ``anbieter::modell``) oder leer."""
    m = str(_load_profile().get("image_model", "") or "").strip()
    return "" if m in _MODEL_PLACEHOLDERS else m


def _sd_url() -> str:
    """URL des lokalen Stable-Diffusion-WebUI-Servers (Profil)."""
    return str(_load_profile().get("sd_webui_url", "") or "").strip().rstrip("/")


async def _sd_reachable() -> bool:
    """Ist der lokale Bild-Server (SD-WebUI / Z-Image-Brücke) erreichbar? Für eine
    Vorab-Prüfung, damit z. B. der Präsentationsassistent nicht je Folie einzeln an
    einem fehlenden Server scheitert. Jede HTTP-Antwort (auch 404) = erreichbar; nur
    Verbindungsfehler/Timeout = nicht erreichbar."""
    base = _sd_url()
    if not base:
        return False
    try:
        async with httpx.AsyncClient(timeout=3) as client:
            await client.get(f"{base}/health")
        return True
    except Exception:
        return False


def _sd_server_python(d: str) -> Optional[str]:
    """venv-Python der Z-Image-Brücke im Ordner ``d`` (Windows/Linux)."""
    for rel in ("venv/Scripts/python.exe", "venv/bin/python"):
        p = os.path.join(d, *rel.split("/"))
        if os.path.exists(p):
            return p
    return None


def _sd_server_dir() -> Optional[str]:
    """Ordner mit ``sd_server.py`` + eigener venv der Z-Image-Brücke. Reihenfolge:
    Profil ``sd_server_dir`` → Repo-Ordner ``z-image`` → ``~/z-image`` (Standalone)."""
    cands = []
    cfg = str(_load_profile().get("sd_server_dir", "") or "").strip()
    if cfg:
        cands.append(cfg)
    cands.append(os.path.join(str(APP_DIR), "z-image"))
    cands.append(os.path.join(os.path.expanduser("~"), "z-image"))
    for c in cands:
        try:
            if c and os.path.exists(os.path.join(c, "sd_server.py")) and _sd_server_python(c):
                return c
        except Exception:
            pass
    return None


def _url_port(url: str) -> Optional[int]:
    m = re.search(r":(\d{2,5})(?:/|$)", url or "")
    return int(m.group(1)) if m else None


def _launch_detached(cmd: list, cwd: Optional[str] = None) -> None:
    """Prozess losgelöst starten (überlebt den Request; keine Konsole/Handles)."""
    import subprocess
    kwargs = dict(cwd=cwd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                  stdin=subprocess.DEVNULL)
    if sys.platform == "win32":
        kwargs["creationflags"] = 0x00000008 | 0x00000200  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    subprocess.Popen(cmd, **kwargs)


_sd_launch_lock = asyncio.Lock()


async def _ensure_sd_server() -> bool:
    """Stellt sicher, dass der lokale Bild-Server läuft – **startet ihn bei Bedarf
    selbst** (crash-sichere Z-Image-Brücke, torch-frei, ~1 s Start; Modell lädt erst
    pro Bild). Nur wenn eine lokale SD-URL gesetzt ist und Profil ``sd_autostart``
    (Standard an) nicht abgeschaltet wurde. Rückgabe True, wenn (jetzt) erreichbar."""
    if await _sd_reachable():
        return True
    if not _sd_url():
        return False
    if not bool(_load_profile().get("sd_autostart", True)):
        return False
    d = _sd_server_dir()
    if not d:
        return False
    py = _sd_server_python(d)
    script = os.path.join(d, "sd_server.py")
    if not (py and os.path.exists(script)):
        return False
    port = _url_port(_sd_url()) or 7860
    async with _sd_launch_lock:
        if await _sd_reachable():        # während des Wartens von anderer Stelle gestartet
            return True
        try:
            _launch_detached([py, script, "--port", str(port)], cwd=d)
        except Exception:
            return False
        for _ in range(30):              # torch-frei -> i. d. R. < 2 s
            await asyncio.sleep(0.5)
            if await _sd_reachable():
                return True
    return False


def _api_image_size(real_model: str, preset: str) -> str:
    """Größen-String für die OpenAI-kompatible Bild-API je Modellfamilie."""
    if preset not in _IMAGE_SIZES:
        preset = "square"
    if "gpt-image" in (real_model or "").lower():
        # gpt-image-1 kennt nur 1024x1024 / 1536x1024 / 1024x1536
        return {"square": "1024x1024", "landscape": "1536x1024",
                "portrait": "1024x1536"}[preset]
    # DALL·E 3 (und die meisten Kompatiblen): 1024er / 1792er
    w, h = _IMAGE_SIZES[preset]["wh"]
    return f"{w}x{h}"




async def _generate_image_core(prompt: str, negative: str = "", preset: str = "square",
                               model: Optional[str] = None) -> dict:
    """Kern der Bildgenerierung – lokal (SD-WebUI) oder OpenAI-kompatible Bild-API.
    Genutzt vom Endpoint ``/api/image/generate`` UND dem Chat-Werkzeug ``generate_image``
    (Assistent-Modus). Antwort: ``{image: data-URI/URL, model, prompt}``; wirft
    HTTPException bei Fehlern. Geheim-/Hartman-Modus: Remote-Modell → lokaler SD-Server;
    ist keiner eingerichtet → **HTTP 409** (keine Cloud-Anfrage)."""
    prompt = str(prompt or "").strip()
    if not prompt:
        raise HTTPException(400, "Kein Bild-Prompt angegeben.")
    negative = str(negative or "").strip()
    preset = str(preset or "square").strip().lower()
    if preset not in _IMAGE_SIZES:
        preset = "square"

    model = str(model or "").strip() or _image_model()
    if not model or model in _MODEL_PLACEHOLDERS:
        raise HTTPException(400, "Keine Bildgenerierung konfiguriert – im Profil ein "
                                 "Bildmodell (lokal SD-WebUI oder API) wählen.")

    # Geheim-Modus: Remote-API nicht zulassen → auf lokal umleiten.
    secret = _secret_local()
    is_local = model.startswith("local::") or not _llm.is_remote(model)
    if secret and not is_local:
        if _sd_url():
            model, is_local = "local::sd", True
        else:
            raise HTTPException(409, "Im Geheim-Modus ist nur lokale Bildgenerierung "
                                     "erlaubt – bitte SD-WebUI-URL im Profil eintragen.")

    w, h = _IMAGE_SIZES[preset]["wh"]

    # ── Lokal: Stable Diffusion WebUI (AUTOMATIC1111/Forge) ──────────────────
    if is_local:
        base = _sd_url()
        if not base:
            raise HTTPException(400, "Keine SD-WebUI-URL im Profil hinterlegt.")
        await _ensure_sd_server()   # Bild-Server bei Bedarf selbst starten (Z-Image-Brücke)
        payload = {
            "prompt": prompt,
            "negative_prompt": negative,
            "width": w, "height": h,
            "steps": 28, "cfg_scale": 6.5,
            "sampler_name": "DPM++ 2M",
        }
        try:
            async with httpx.AsyncClient(timeout=300) as client:
                resp = await client.post(f"{base}/sdapi/v1/txt2img", json=payload)
            if resp.status_code >= 400:
                raise HTTPException(502, f"SD-WebUI-Fehler (HTTP {resp.status_code}): "
                                         f"{resp.text[:300]}")
            data = resp.json()
        except HTTPException:
            raise
        except httpx.ConnectError:
            raise HTTPException(503, f"SD-WebUI nicht erreichbar unter {base} – läuft der "
                                     f"Server (mit --api)?")
        except Exception as e:
            raise HTTPException(502, f"Lokale Bildgenerierung fehlgeschlagen: {e}")
        imgs = data.get("images") or []
        if not imgs:
            raise HTTPException(502, "SD-WebUI lieferte kein Bild zurück.")
        b64 = imgs[0]
        if "," in b64 and b64.strip().startswith("data:"):
            b64 = b64.split(",", 1)[1]
        return {"image": f"data:image/png;base64,{b64}", "model": model, "prompt": prompt}

    # ── API: OpenAI-kompatibles /images/generations ──────────────────────────
    provider, real = _llm.resolve(model)
    if not provider:
        raise HTTPException(400, "Unbekannter API-Anbieter für die Bildgenerierung.")
    base = (provider.get("base_url") or "").rstrip("/")
    headers = {"Authorization": f"Bearer {provider.get('api_key', '')}",
               "Content-Type": "application/json"}
    payload = {"model": real, "prompt": prompt, "n": 1,
               "size": _api_image_size(real, preset), "response_format": "b64_json"}
    try:
        async with httpx.AsyncClient(timeout=300) as client:
            resp = await client.post(f"{base}/images/generations", headers=headers,
                                     json=payload)
        if resp.status_code >= 400:
            raise HTTPException(502, f"Bild-API fehlgeschlagen (HTTP {resp.status_code}): "
                                     f"{resp.text[:300]}")
        data = resp.json()
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(502, f"Bild-API fehlgeschlagen: {e}")
    items = data.get("data") or []
    if not items:
        raise HTTPException(502, "Bild-API lieferte kein Bild zurück.")
    first = items[0] or {}
    b64 = first.get("b64_json")
    if b64:
        src = f"data:image/png;base64,{b64}"
    elif first.get("url"):
        src = first["url"]   # manche Anbieter liefern nur eine URL
    else:
        raise HTTPException(502, "Bild-API-Antwort ohne Bilddaten.")
    return {"image": src, "model": model, "prompt": prompt}


async def _edit_image_core(prompt: str, image_b64: str, strength: float = 0.55,
                           preset: str = "square", model: Optional[str] = None,
                           mask_b64: Optional[str] = None) -> dict:
    """Bildbearbeitung (img2img): vorhandenes Bild + Anweisung → verändertes Bild.
    Mit ``mask_b64`` = **Inpainting** (nur der weiße Maskenbereich wird geändert).
    Lokal über SD-WebUI/Z-Image-Brücke (``/sdapi/v1/img2img``, Feld ``mask``) oder
    OpenAI-kompatible Bild-Edits-API (``/images/edits``; **nur Modelle, die das
    können** – sonst klare Fehlermeldung). Antwort wie ``_generate_image_core``:
    ``{image, model, prompt}``. ``strength`` = wie stark verändert wird. Geheim/Hartman:
    nur lokal (Remote → lokaler SD-Server; ohne SD-URL 409)."""
    prompt = str(prompt or "").strip()
    if not prompt:
        raise HTTPException(400, "Keine Änderungsanweisung angegeben.")
    if not image_b64:
        raise HTTPException(400, "Kein Bild übergeben.")
    try:
        strength = float(strength)
    except Exception:
        strength = 0.55
    strength = max(0.1, min(strength, 0.95))
    preset = str(preset or "square").strip().lower()
    if preset not in _IMAGE_SIZES:
        preset = "square"
    model = str(model or "").strip() or _image_model()
    if not model or model in _MODEL_PLACEHOLDERS:
        raise HTTPException(400, "Keine Bildgenerierung konfiguriert – im Profil ein "
                                 "Bildmodell (lokal SD-WebUI oder API) wählen.")

    # Rohe base64 (ohne data:-Präfix) bereitstellen.
    def _strip_data(b: str) -> str:
        b = str(b or "")
        if "," in b and b.strip().startswith("data:"):
            b = b.split(",", 1)[1]
        return b
    raw_b64 = _strip_data(image_b64)
    raw_mask = _strip_data(mask_b64) if mask_b64 else ""

    secret = _secret_local()
    is_local = model.startswith("local::") or not _llm.is_remote(model)
    if secret and not is_local:
        if _sd_url():
            model, is_local = "local::sd", True
        else:
            raise HTTPException(409, "Im Geheim-Modus ist nur lokale Bildbearbeitung "
                                     "erlaubt – bitte SD-WebUI-URL im Profil eintragen.")

    w, h = _IMAGE_SIZES[preset]["wh"]

    # ── Lokal: SD-WebUI / Z-Image-Brücke img2img ────────────────────────────
    if is_local:
        base = _sd_url()
        if not base:
            raise HTTPException(400, "Keine SD-WebUI-URL im Profil hinterlegt.")
        await _ensure_sd_server()   # Bild-Server bei Bedarf selbst starten
        payload = {
            "init_images": [raw_b64], "prompt": prompt,
            "denoising_strength": strength, "width": w, "height": h,
            "steps": 28, "cfg_scale": 6.5, "sampler_name": "DPM++ 2M",
        }
        if raw_mask:
            payload["mask"] = raw_mask   # Inpainting: weiß = Bereich ändern
        try:
            async with httpx.AsyncClient(timeout=300) as client:
                resp = await client.post(f"{base}/sdapi/v1/img2img", json=payload)
            if resp.status_code >= 400:
                raise HTTPException(502, f"SD-WebUI-Fehler (HTTP {resp.status_code}): "
                                         f"{resp.text[:300]}")
            data = resp.json()
        except HTTPException:
            raise
        except httpx.ConnectError:
            raise HTTPException(503, f"SD-WebUI nicht erreichbar unter {base} – läuft der "
                                     f"Server (mit --api)?")
        except Exception as e:
            raise HTTPException(502, f"Lokale Bildbearbeitung fehlgeschlagen: {e}")
        imgs = data.get("images") or []
        if not imgs:
            raise HTTPException(502, "SD-WebUI lieferte kein Bild zurück.")
        b64 = imgs[0]
        if "," in b64 and b64.strip().startswith("data:"):
            b64 = b64.split(",", 1)[1]
        return {"image": f"data:image/png;base64,{b64}", "model": model, "prompt": prompt}

    # ── API: OpenAI-kompatibles /images/edits (multipart) ───────────────────
    provider, real = _llm.resolve(model)
    if not provider:
        raise HTTPException(400, "Unbekannter API-Anbieter für die Bildbearbeitung.")
    base = (provider.get("base_url") or "").rstrip("/")
    headers = {"Authorization": f"Bearer {provider.get('api_key', '')}"}
    try:
        img_bytes = base64.b64decode(raw_b64)
    except Exception:
        raise HTTPException(400, "Bild konnte nicht dekodiert werden.")
    files = {"image": ("image.png", img_bytes, "image/png")}
    form = {"model": real, "prompt": prompt, "n": "1",
            "size": _api_image_size(real, preset)}
    if raw_mask:
        # OpenAI-Konvention: TRANSPARENTE Bereiche der Maske werden bearbeitet.
        # Unsere Maske ist weiß = ändern → dort Alpha 0 (transparent) setzen.
        try:
            import io as _io
            from PIL import Image as _Img
            _m = _Img.open(_io.BytesIO(base64.b64decode(raw_mask))).convert("L")
            _im = _Img.open(_io.BytesIO(img_bytes))
            _m = _m.resize(_im.size)
            _alpha = _m.point(lambda p: 0 if p > 128 else 255)
            _rgba = _Img.new("RGBA", _m.size, (0, 0, 0, 255))
            _rgba.putalpha(_alpha)
            _buf = _io.BytesIO(); _rgba.save(_buf, "PNG")
            files["mask"] = ("mask.png", _buf.getvalue(), "image/png")
        except Exception:
            pass   # Maske optional – zur Not ohne (ganzes Bild)
    try:
        async with httpx.AsyncClient(timeout=300) as client:
            resp = await client.post(f"{base}/images/edits", headers=headers,
                                     data=form, files=files)
        if resp.status_code >= 400:
            raise HTTPException(502, f"Bild-Edit-API fehlgeschlagen (HTTP {resp.status_code}) – "
                                     f"unterstützt „{real}“ Bildbearbeitung? {resp.text[:250]}")
        data = resp.json()
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(502, f"Bild-Edit-API fehlgeschlagen: {e}")
    items = data.get("data") or []
    if not items:
        raise HTTPException(502, "Bild-Edit-API lieferte kein Bild zurück.")
    first = items[0] or {}
    b64 = first.get("b64_json")
    if b64:
        src = f"data:image/png;base64,{b64}"
    elif first.get("url"):
        src = first["url"]
    else:
        raise HTTPException(502, "Bild-Edit-API-Antwort ohne Bilddaten.")
    return {"image": src, "model": model, "prompt": prompt}




async def _upscale_image_core(image_b64: str, factor: float = 2.0,
                              mode: str = "ai", model: Optional[str] = None) -> dict:
    """Bild hochskalieren. ``mode='fast'`` = Lanczos (Pillow, kein VRAM, keine neuen
    Details); ``mode='ai'`` = Detail-Upscale via Z-Image-img2img (niedrige Stärke)
    über die lokale Brücke → ergänzt echte Details. **Kein Absturz**: fehlt der lokale
    Server / ist das Modell remote / scheitert die Brücke, wird auf Lanczos
    zurückgefallen (Feld ``note``). Deckel lange Seite 2048 (ai) / 4096 (fast).
    Antwort ``{image, mode, width, height, note?}``. Alles MIT (diffusers/Pillow)."""
    if not image_b64:
        raise HTTPException(400, "Kein Bild übergeben.")
    try:
        factor = float(factor)
    except Exception:
        factor = 2.0
    factor = max(1.1, min(factor, 4.0))
    mode = str(mode or "ai").lower().strip()
    raw = image_b64
    if "," in raw and raw.strip().startswith("data:"):
        raw = raw.split(",", 1)[1]
    import io as _io
    from PIL import Image as _Img, ImageFilter as _F
    try:
        _src = _Img.open(_io.BytesIO(base64.b64decode(raw))).convert("RGB")
    except Exception:
        raise HTTPException(400, "Bild konnte nicht gelesen werden.")
    ow, oh = _src.size
    cap = _UPSCALE_MAX_FAST if mode == "fast" else _UPSCALE_MAX_AI
    tw, th = int(ow * factor), int(oh * factor)
    _long = max(tw, th)
    if _long > cap:
        _s = cap / _long
        tw, th = int(tw * _s), int(th * _s)
    tw -= tw % 16
    th -= th % 16
    tw, th = max(256, tw), max(256, th)

    def _lanczos(note: str = "") -> dict:
        _rs = getattr(_Img, "Resampling", _Img)
        _up = _src.resize((tw, th), getattr(_rs, "LANCZOS", 1))
        try:
            _up = _up.filter(_F.UnsharpMask(radius=2, percent=80, threshold=2))
        except Exception:
            pass
        _b = _io.BytesIO(); _up.save(_b, "PNG")
        out = {"image": "data:image/png;base64," + base64.b64encode(_b.getvalue()).decode(),
               "mode": "fast", "width": tw, "height": th}
        if note:
            out["note"] = note
        return out

    if mode == "fast":
        return _lanczos()

    # KI-Detail-Upscale nur lokal über die Z-Image-Brücke (sonst Lanczos-Rückfall).
    _m = str(model or "").strip() or _image_model()
    is_local = _m.startswith("local::") or (bool(_m) and not _llm.is_remote(_m)) or _secret_local()
    sd = _sd_url()
    if not (is_local and sd):
        return _lanczos("KI-Upscale nur lokal über Z-Image (SD-URL im Profil nötig) – "
                        "schnelle Vergrößerung genutzt.")
    payload = {"init_images": [raw], "prompt": _UPSCALE_PROMPT,
               "denoising_strength": 0.30, "width": tw, "height": th,
               "steps": 28, "cfg_scale": 6.5, "sampler_name": "DPM++ 2M"}
    try:
        async with httpx.AsyncClient(timeout=360) as client:
            resp = await client.post(f"{sd}/sdapi/v1/img2img", json=payload)
        if resp.status_code >= 400:
            raise RuntimeError(f"HTTP {resp.status_code}: {resp.text[:150]}")
        imgs = (resp.json().get("images") or [])
        if not imgs:
            raise RuntimeError("kein Bild")
        b64 = imgs[0]
        if "," in b64 and b64.strip().startswith("data:"):
            b64 = b64.split(",", 1)[1]
        return {"image": f"data:image/png;base64,{b64}", "mode": "ai",
                "width": tw, "height": th}
    except Exception as e:
        return _lanczos(f"KI-Upscale nicht möglich ({e}) – schnelle Vergrößerung genutzt.")




def _extract_canvas_json(content: str) -> Optional[dict]:
    import re

    for m in re.finditer(r'```(?:json)?\s*(\{.*?\})\s*```', content, re.DOTALL):
        try:
            data = json.loads(m.group(1))
        except Exception:
            continue

        # Wrapper-Formate auflösen:
        # {"tool": "create_presentation", "parameters": {...}}
        # {"function": "...", "arguments": {...}}
        tool_name = data.get("tool") or data.get("function") or data.get("name")
        if tool_name in ("create_presentation", "create_spreadsheet"):
            inner = data.get("parameters") or data.get("arguments") or data.get("input") or {}
            canvas_type = tool_name.replace("create_", "")
            data = {"type": canvas_type, **inner}

        # Expliziter type-Header
        if data.get("type") in ("presentation", "spreadsheet"):
            if data["type"] == "presentation":
                return _normalize_presentation(data)
            return data

        # Präsentation anhand von "slides"-Feld erkennen
        if "slides" in data and isinstance(data["slides"], list):
            data["type"] = "presentation"
            return _normalize_presentation(data)

        # Spreadsheet anhand von "headers"+"rows" erkennen
        if "headers" in data and "rows" in data:
            data["type"] = "spreadsheet"
            return data

    return None


def _strip_canvas_json(content: str) -> str:
    import re

    return re.sub(
        r'```(?:json)?\s*\{.*?\}\s*```',
        "",
        content,
        flags=re.DOTALL,
    ).strip()








def _normalize_presentation(data: dict) -> dict:
    """Normalisiert unterschiedliche Slide-Strukturen auf unser einheitliches Format."""
    for slide in data.get("slides", []):
        # subtitle → content (title-Layout)
        if "subtitle" in slide and not slide.get("content"):
            slide["content"] = slide.pop("subtitle")

        # content als Dict → Felder hochziehen
        c = slide.get("content")
        if isinstance(c, dict):
            if "title" in c and not slide.get("title"):
                slide["title"] = c["title"]
            if "bullets" in c and not slide.get("bullets"):
                slide["bullets"] = c["bullets"]
            if "left" in c:
                slide["left"] = c["left"]
            if "right" in c:
                slide["right"] = c["right"]
            slide["content"] = c.get("subtitle") or c.get("content") or c.get("text") or ""
        elif isinstance(c, list):
            # Liste wird zu bullets, wenn kein bullets-Feld vorhanden
            if not slide.get("bullets"):
                slide["bullets"] = [str(x) for x in c]
            slide["content"] = ""

        # bullets normalisieren
        bullets = slide.get("bullets")
        if isinstance(bullets, list):
            normalized = []
            for x in bullets:
                if isinstance(x, str):
                    normalized.append(x)
                elif isinstance(x, dict):
                    normalized.append(x.get("text") or x.get("content") or str(x))
                else:
                    normalized.append(str(x))
            slide["bullets"] = normalized

        # theme lowercase
    if "theme" in data:
        data["theme"] = data["theme"].lower()

    return data


def _parse_prose_presentation(text: str) -> Optional[dict]:
    """Wandelt einen vom Modell als Markdown/Fließtext gelieferten Präsentations-
    entwurf DETERMINISTISCH in Folien um — ohne zweiten LLM-Aufruf, damit der
    Inhalt (Überschriften, Aufzählungen, Texte) 1:1 erhalten bleibt. Erkennt
    Folien-Marker (Markdown-Überschriften #/##, »Folie/Slide/Seite N«, **Fettzeilen**)
    sowie Aufzählungen (-, *, •, –, 1.). Gibt None zurück, wenn zu wenig Inhalt
    erkennbar ist (dann übernimmt der LLM-Fallback)."""
    lines = text.replace("\r\n", "\n").split("\n")

    heading_re = re.compile(r"^\s{0,3}(#{1,4})\s+(.*\S)\s*$")
    marker_re = re.compile(
        r"^\s*\*{0,2}_{0,2}\s*(?:Folie|Slide|Seite|Chart)\s*[:#]?\s*\d+\s*[:.\)\-–]?\s*(.*?)\s*\*{0,2}\s*$",
        re.IGNORECASE)
    bold_title_re = re.compile(r"^\s*\*\*(.+?)\*\*\s*:?\s*$")
    bullet_re = re.compile(r"^\s*(?:[-*•–·▪]|\d+[\.\)])\s+(.*\S)\s*$")

    slides: list = []
    cur: Optional[dict] = None

    def _clean(s: str) -> str:
        # Markdown-Hervorhebung/Restzeichen aus Zeileninhalt entfernen
        return re.sub(r"(\*\*|__|[*_`])", "", s).strip()

    def _new(title: str) -> None:
        nonlocal cur
        t = (title or "").strip()
        # Folien-Marker (»Folie 1:« usw.) am Anfang entfernen
        t = re.sub(
            r"^\**\s*(?:Folie|Slide|Seite|Chart)\s*[:#]?\s*\d+\s*[:.\)\-–]?\s*",
            "", t, flags=re.IGNORECASE)
        # Markdown-Hervorhebung (**…**, __…__) und Restzeichen abstreifen
        t = re.sub(r"[*_`]+", "", t).strip().strip(":").strip()
        cur = {"title": t, "bullets": [], "_text": []}
        slides.append(cur)

    for raw in lines:
        line = raw.rstrip()
        if not line.strip():
            continue
        mm = marker_re.match(line)
        mh = heading_re.match(line)
        mb = bullet_re.match(line)
        mbold = bold_title_re.match(line)
        if mm:
            _new(mm.group(1))
        elif mh:
            _new(mh.group(2))
        elif cur is None:
            # Inhalt vor der ersten Überschrift → erste (Titel-)Folie
            _new(re.sub(r"^[*_#\s]+", "", line).strip())
        elif mb:
            cur["bullets"].append(_clean(mb.group(1)))
        elif mbold and not cur["bullets"] and not cur["_text"]:
            # Fettzeile direkt nach einer Überschrift = Untertitel
            cur["_text"].append(_clean(mbold.group(1)))
        else:
            cur["_text"].append(_clean(line.strip()))

    if len(slides) < 2:
        return None

    out: list = []
    for i, s in enumerate(slides):
        title = s["title"]
        bullets = [b for b in s["bullets"] if b]
        body = " ".join(s["_text"]).strip()
        if i == 0 and not bullets:
            out.append({"layout": "title", "title": title or "Präsentation",
                        "content": body})
        elif bullets:
            sl = {"layout": "bullets", "title": title, "bullets": bullets}
            if body:
                sl["content"] = body
            out.append(sl)
        elif body:
            # kein Aufzählungszeichen, aber Textzeilen → als Bullets übernehmen
            out.append({"layout": "bullets", "title": title,
                        "bullets": [t for t in s["_text"] if t]})
        else:
            out.append({"layout": "section", "title": title})

    # Mindestens eine Folie muss echten Inhalt tragen, sonst lohnt der Parser nicht
    if not any(sl.get("bullets") or sl.get("content") for sl in out):
        return None

    data = {"type": "presentation", "title": slides[0]["title"] or "Präsentation",
            "theme": "dark", "slides": out}
    return _normalize_presentation(data)


async def _text_to_presentation(text: str, model: str,
                                tok: Optional[dict] = None) -> Optional[dict]:
    """Wandelt einen vom Modell als Fließtext gelieferten Präsentationsentwurf in
    eine Canvas-Präsentation um. Versucht ZUERST den deterministischen Parser
    (`_parse_prose_presentation`, bewahrt den Inhalt vollständig); nur wenn der
    keine brauchbaren Folien liefert, wird per zweitem LLM-Aufruf umgewandelt.
    Gibt None zurück, wenn keine gültigen Folien entstehen."""
    # 1. Deterministischer Parser — verliert keinen Inhalt
    parsed = _parse_prose_presentation(text)
    if parsed and sum(
            1 for s in parsed["slides"] if s.get("bullets") or s.get("content")) >= 2:
        return parsed

    # 2. LLM-Fallback (für unstrukturierten Fließtext)
    user = (
        "Wandle den folgenden Präsentationsentwurf in strukturierte Folien um. "
        "Antworte NUR mit JSON in genau diesem Format, ohne Markdown, ohne Erklärung:\n"
        '{"title":"Titel der Präsentation","theme":"dark","slides":['
        '{"layout":"title","title":"Titel","content":"Untertitel"},'
        '{"layout":"bullets","title":"Abschnitt","bullets":["Punkt 1","Punkt 2","Punkt 3"]}]}\n'
        "layout ist eines von: title, section, bullets, two-column. Erzeuge 5–10 Folien, "
        "erste Folie layout=title. Behalte Sprache und Inhalt des Entwurfs bei.\n\n"
        "Entwurf:\n" + text[:6000]
    )
    try:
        async with _model_session(model), httpx.AsyncClient(timeout=180) as client:
            resp = await _llm.chat(client,{
                "model": model,
                "think": False,
                "format": "json",
                "messages": [
                    {"role": "system", "content": "Du formatierst Präsentationsentwürfe als gültiges JSON."},
                    {"role": "user", "content": user},
                ],
                "stream": False,
            })
            resp.raise_for_status()
            _tp_j = resp.json()
            if tok is not None:
                _a, _b = _llm_tok(_tp_j)
                tok["in"] += _a
                tok["out"] += _b
            raw = _tp_j.get("message", {}).get("content", "")
    except Exception:
        return parsed

    raw = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL).strip()
    m = re.search(r"\{[\s\S]*\}", raw)
    if not m:
        return parsed
    try:
        d = json.loads(m.group(0))
    except Exception:
        return parsed
    slides = d.get("slides")
    if not isinstance(slides, list) or len(slides) < 2:
        return parsed
    data = {"type": "presentation", "title": str(d.get("title", "Präsentation")),
            "theme": str(d.get("theme", "dark")), "slides": slides}
    return _normalize_presentation(data)


def _load_agent_dict(aid: str) -> Optional[dict]:
    """Agent-Definition per ID laden. Cross-Feature (Agenten, Jury, Plan) → core."""
    fp = _agent_path_by_id(aid)
    if fp and fp.exists():
        try:
            return json.loads(fp.read_text(encoding="utf-8"))
        except Exception:
            return None
    return None


def _safe_relpath(p: str) -> str:
    """Relativen, sicheren Dateipfad erzwingen (kein Pfad-Traversal, kein absoluter Pfad,
    Backslashes → Slash). Leere/auflösbare Segmente (., ..) werden verworfen.
    Cross-Feature (Code-Router, Backup/Restore, PST) → core."""
    p = str(p or "").replace("\\", "/").strip().lstrip("/")
    parts = [seg.strip() for seg in p.split("/") if seg.strip() and seg.strip() not in (".", "..")]
    return "/".join(parts)[:200]


def _plan_path(plan_id: str, plan_name: str = "") -> Path:
    """Gibt den Dateipfad für einen Plan zurück (sprechender Name + ID-Suffix).
    Cross-Feature (Planer, Backup/Restore) → core."""
    if plan_name:
        slug = _to_slug(plan_name)
        return PLANS_DIR / f"{slug}_{plan_id[:8]}.json"
    return PLANS_DIR / f"{plan_id}.json"


def _jury_path_by_id(jid: str) -> Optional[Path]:
    """Jury-Datei per ID. Cross-Feature (Jury-Router, Backup/Restore) → core."""
    for fp in JURIES_DIR.glob("*.json"):
        try:
            if json.loads(fp.read_text(encoding="utf-8")).get("id") == jid:
                return fp
        except Exception:
            pass
    return None


def _code_path_by_id(prog_id: str) -> Optional[Path]:
    """Code-Datei per ID. Cross-Feature (Code-Router, Backup/Restore) → core."""
    for f in CODE_DIR.glob("*.json"):
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
            if d.get("id") == prog_id:
                return f
        except Exception:
            pass
    return None


def _jury_doc_path_by_id(doc_id: str) -> Optional[Path]:
    """Jury-Dokument per ID. Cross-Feature (Jury-Docs-Router, Backup/Restore) → core."""
    for f in JURY_DOCS_DIR.glob("*.json"):
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
            if d.get("id") == doc_id:
                return f
        except Exception:
            pass
    return None


def _plan_path_by_id(plan_id: str) -> Optional[Path]:
    """Findet Plan-Datei anhand der ID. Cross-Feature (Planer, Angebot/from-plan) → core."""
    for fp in PLANS_DIR.glob("*.json"):
        try:
            data = json.loads(fp.read_text(encoding="utf-8"))
            if data.get("id") == plan_id:
                return fp
        except Exception:
            pass
    return None


async def _plan_rag_context(rag_collections, query: str) -> str:
    """Löst RAG-Collection-IDs (Liste oder kommagetrennt) auf und zieht Grounding-Kontext.

    Cross-Feature-Helfer (Planer, Varianten-Auto-Fill, …) → in core, damit mehrere
    Router ihn über ``from core import *`` erhalten."""
    if isinstance(rag_collections, str):
        rag_ids = [c.strip() for c in rag_collections.split(",") if c.strip()]
    else:
        rag_ids = [c for c in (rag_collections or []) if c]
    if not rag_ids:
        return ""
    from tools.rag import query_collections
    colls = []
    for cid in rag_ids:
        c = await _db.rag_get_collection(cid) if cid else None
        if c:
            colls.append(c)
    if not colls:
        return ""
    try:
        hits = await query_collections(colls, (query or "Plan")[:500])
        if hits:
            return "\n\n".join(h.get("text", "") for h in hits[:8])[:4000]
    except Exception:
        pass
    return ""


# ══════════════════════════════════════════════════════════════════════════════
# Assistent-Modus: Tabs als aufrufbare Agenten (streamende Kern-Generatoren)
# ══════════════════════════════════════════════════════════════════════════════
#
# Diese drei Async-Generatoren kapseln je eine Tab-Fähigkeit als „Werkzeug", das der
# Chat-Tool-Loop im Assistent-Modus eigenständig aufrufen kann. Sie liegen in core.py,
# weil der Chat-Loop sie aufruft (Projektregel: Capability-Cores des Tool-Loops gehören
# nach core, nicht in einen Feature-Router → sonst Router↔Router-Zyklus). Sie sind
# BEWUSST eigenständig gehalten (kein Import der Tab-Endpoints in routers/research.py,
# routers/workflow.py, routers/todo.py) — so bleiben die bestehenden Tabs unangetastet.
#
# Vertrag: jeder Generator yieldet dicts:
#   {"type":"progress","message":str}  – Live-Statuszeile (Fortschritt)
#   {"type":"notice","message":str}    – weicher Hinweis
#   {"type":"text","content":str}      – Wort-für-Wort-Stream der Endantwort
#   {"type":"image","data":str}        – optionales Bild (Workflow)
#   {"type":"error","message":str}     – harter Fehler (Loop meldet ihn, beendet)
#   {"type":"result","summary":str,"tok":{"in":int,"out":int}}  – Abschluss
# Der Chat-Loop übersetzt progress/notice → tool_progress-Frames, reicht text/image
# durch und übernimmt `summary` als Assistenten-Antwort (Terminal-Werkzeug).


async def _deep_research_core(topic: str, depth: int = 6, words: int = 900,
                             focus: str = "", model: Optional[str] = None):
    """Tiefe Web-Recherche als Assistent-Werkzeug: Thema → Teilaspekte → je Aspekt
    Websuche → quellen-gestützte Synthese. Eigenständige, schlanke Variante des
    ``/api/deepresearch``-Generators (bewusst nicht importiert)."""
    from tools.search import search_with_sources

    topic = (topic or "").strip()
    if not topic:
        yield {"type": "error", "message": "Kein Thema für die Recherche angegeben."}
        return
    if not _web_search_allowed():
        yield {"type": "error", "message": "Websuche ist im aktuellen Modus gesperrt "
               "(z. B. Hartman-/Ausbildungsmodus) — tiefe Recherche nicht möglich."}
        return
    depth = max(3, min(int(depth or 6), 12))
    target_words = max(200, min(int(words or 900), 4000))
    focus = (focus or "").strip()

    _r_model, _r_err = await _research_model(model)
    if _r_err:
        yield {"type": "error", "message": _r_err}
        return

    _tok = {"in": 0, "out": 0}
    yield {"type": "progress", "message": f"🔎 Recherche „{topic}“ – zerlege in Teilaspekte…"}

    _focus_line = f"\nSchwerpunkt/Fokus: {focus}" if focus else ""
    _aspect_prompt = (
        f"Thema: \"{topic}\"{_focus_line}\n\n"
        f"Zerlege das Thema in genau {depth} prägnante, sich ergänzende Teilaspekte/Unterfragen "
        f"für eine gründliche Web-Recherche (je 2–6 Wörter, deutsch, ohne Nummerierung).\n"
        f'Antworte NUR mit JSON: {{"aspects":["…","…"]}}.'
    )
    aspects: list = []
    try:
        async with _model_session(_r_model), httpx.AsyncClient(timeout=120) as client:
            resp = await _llm.chat(client, {
                "model": _r_model, "think": False, "stream": False, "format": "json",
                "messages": [{"role": "user", "content": _aspect_prompt}],
                "options": {"num_ctx": _profile_num_ctx()}, "keep_alive": KEEP_ALIVE,
            })
            resp.raise_for_status()
        _aj = resp.json()
        _ti, _to = _llm_tok(_aj)
        _tok["in"] += _ti; _tok["out"] += _to
        _d = _parse_llm_json(_aj.get("message", {}).get("content", "")) or {}
        aspects = [str(a).strip() for a in (_d.get("aspects") or []) if str(a).strip()][:depth]
    except httpx.ConnectError:
        yield {"type": "error", "message": "Ollama nicht erreichbar – läuft der lokale Server?"}
        return
    except Exception:
        aspects = []
    if not aspects:
        aspects = ["Überblick", "technische Daten", "Geschichte / Hintergrund",
                   "Varianten / Modelle", "Preise / Markt", "Besonderheiten / Bewertung",
                   "Vor- und Nachteile", "Alternativen"][:depth]

    yield {"type": "progress", "message": "🌐 Durchsuche das Web zu " + str(len(aspects)) + " Aspekten…"}
    tasks = [search_with_sources(f"{topic} {a}", 5) for a in aspects]
    raw_results = await asyncio.gather(*tasks, return_exceptions=True)
    aspect_data = []
    for _n, (a, r) in enumerate(zip(aspects, raw_results), start=1):
        if isinstance(r, Exception):
            sources, text = [], f"Suchfehler: {r}"
        else:
            sources, text = r
        yield {"type": "progress", "message": f"✓ Aspekt {_n}/{len(aspects)}: {a}"}
        aspect_data.append((a, text))
    yield {"type": "progress", "message": "🧩 Fasse die Quellen zu einem Bericht zusammen…"}

    _ctx = _profile_num_ctx()
    _out_reserve_tok = max(400, min(int(target_words * 1.7), int(_ctx * 0.5)))
    _in_budget_chars = max(2500, int((_ctx - _out_reserve_tok - 700) * 3.3))
    _per_aspect = max(400, min(2500, _in_budget_chars // max(1, len(aspect_data))))
    _eff_words = max(250, min(target_words, int(_out_reserve_tok / 1.7)))
    if _eff_words < int(target_words * 0.85):
        yield {"type": "notice", "message":
               f"Bericht auf ~{_eff_words} statt ~{target_words} Wörter begrenzt (Kontextfenster {_ctx})."}

    _parts = [f"Thema: {topic}\n"]
    if focus:
        _parts.append(f"Schwerpunkt: {focus}\n")
    for a, t in aspect_data:
        _parts.append(f"### Suchergebnisse – {a}\n{t[:_per_aspect]}\n")
    _synth = "\n".join(_parts) + (
        f"\n\nSchreibe daraus einen AUSFÜHRLICHEN, gut strukturierten Recherchebericht über "
        f"**{topic}** von **ca. {_eff_words} Wörtern** auf Deutsch (Markdown: ## Überschriften, "
        f"**Fett**, Aufzählungen, bei Kennwerten gern eine Tabelle). Gliederung: kurze Übersicht, "
        f"je ein Abschnitt pro Aspekt, abschließend ein Fazit. WICHTIG: Stütze JEDE konkrete Angabe "
        f"(Zahlen, technische Daten, Baujahre, Preise, Eigennamen) AUSSCHLIESSLICH auf die obigen "
        f"Suchergebnisse. Ist etwas nicht belegt oder widersprüchlich, kennzeichne es ausdrücklich "
        f"als unsicher — erfinde nichts."
    )
    try:
        _sys = "\n\n".join(p for p in (_SCIENCE_PROMPT,
                                       _augment_prefix(topic + " " + " ".join(a for a, _ in aspect_data))) if p)
        _msgs = ([{"role": "system", "content": _sys}] if _sys else []) + \
                [{"role": "user", "content": _synth}]
        async with _model_session(_r_model), httpx.AsyncClient(timeout=600) as client:
            resp = await _llm.chat(client, {
                "model": _r_model, "think": False, "stream": False,
                "messages": _msgs,
                "options": {"num_ctx": _ctx, "num_predict": _out_reserve_tok + 200},
                "keep_alive": KEEP_ALIVE,
            })
            resp.raise_for_status()
        _j = resp.json()
    except httpx.ConnectError:
        yield {"type": "error", "message": "Ollama nicht erreichbar – läuft der lokale Server?"}
        return
    except httpx.HTTPStatusError as e:
        _sc = getattr(e.response, "status_code", 0) or 0
        yield {"type": "error", "message": f"Modell abgelehnt (HTTP {_sc}) – weniger Tiefe/Umfang oder lokales Modell wählen."}
        return
    except Exception as e:
        yield {"type": "error", "message": f"Synthese fehlgeschlagen: {e}"}
        return

    content = (_j.get("message", {}) or {}).get("content", "") or ""
    content = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL).strip()
    _ti, _to = _llm_tok(_j)
    _tok["in"] += _ti; _tok["out"] += _to
    if not content:
        yield {"type": "error", "message": "Die Recherche lieferte keinen Text."}
        return
    _words = content.split(" ")
    for _i, _w in enumerate(_words):
        yield {"type": "text", "content": _w + (" " if _i < len(_words) - 1 else "")}
        await asyncio.sleep(0.003)
    yield {"type": "result", "summary": content, "tok": _tok}


async def _workflow_core(steps: list, goal: str = "", model: Optional[str] = None,
                        web: bool = False):
    """Mehrstufiger Arbeitsablauf als Assistent-Werkzeug: nummerierte Teilaufgaben
    nacheinander (Zwischenergebnisse fließen als Kontext weiter) + Abschluss-Synthese.
    Schlanke, eigenständige Variante des ``/api/workflow``-Generators."""
    steps = [str(s).strip() for s in (steps or []) if str(s).strip()][:12]
    goal = (goal or "").strip()
    if not steps:
        yield {"type": "error", "message": "Keine Schritte für den Arbeitsablauf angegeben."}
        return
    base_model = _pick_model(model, _model_for("general"))
    _ctx = _profile_num_ctx()
    _tok = {"in": 0, "out": 0}
    _budget = max(2000, int((_ctx - 800) * 3.0))
    _web_on = bool(web) and _web_search_allowed()
    results: list = []  # [(step, result)]

    yield {"type": "progress", "message": f"🧵 Arbeitsablauf mit {len(steps)} Schritten…"}

    async def _run(sys_prompt: str, user_prompt: str, num_predict: int) -> str:
        async with _model_session(base_model), httpx.AsyncClient(timeout=600) as client:
            resp = await _llm.chat(client, {
                "model": base_model, "think": False, "stream": False,
                "messages": [{"role": "system", "content": sys_prompt},
                             {"role": "user", "content": user_prompt}],
                "options": {"num_ctx": _ctx, "num_predict": num_predict},
                "keep_alive": KEEP_ALIVE,
            })
            resp.raise_for_status()
        _j = resp.json()
        _c = (_j.get("message", {}) or {}).get("content", "") or ""
        _c = re.sub(r"<think>.*?</think>", "", _c, flags=re.DOTALL).strip()
        _ti, _to = _llm_tok(_j)
        _tok["in"] += _ti; _tok["out"] += _to
        return _c

    try:
        for i, step in enumerate(steps):
            yield {"type": "progress", "message": f"▶ Schritt {i + 1}/{len(steps)}: {step[:70]}"}
            _web_ctx = ""
            if _web_on:
                try:
                    from tools.search import search_with_sources
                    _srcs, _stext = await search_with_sources(step[:200], 5)
                    if _stext:
                        _web_ctx = _stext[:min(_budget, 6000)]
                    yield {"type": "progress", "message": f"  🌐 {len(_srcs or [])} Quellen zu Schritt {i + 1}"}
                except Exception as _e:
                    _web_ctx = ""
            prior = ""
            if results:
                _p = [f"### Ergebnis Schritt {si + 1} ({s}):\n{r}" for si, (s, r) in enumerate(results)]
                prior = "\n\n".join(_p)
                if len(prior) > _budget:
                    prior = "…\n" + prior[-_budget:]
            _sys = ("Du arbeitest einen mehrstufigen Arbeitsablauf ab. Löse NUR den AKTUELLEN "
                    "Schritt präzise und vollständig und baue dabei auf den bisherigen Ergebnissen "
                    "auf. Antworte fokussiert auf Deutsch in Markdown, ohne den Schritt bloß zu wiederholen.")
            if _web_ctx:
                _sys += ("\n\nDir liegen Web-Suchergebnisse vor. Stütze konkrete Angaben (Zahlen, "
                         "Daten, Namen, Preise) NUR auf diese Quellen; ist etwas nicht belegt, "
                         "kennzeichne es als unsicher und erfinde nichts.")
            if goal:
                _sys += f"\n\nÜbergeordnetes Ziel des Ablaufs: {goal}"
            _user = ((f"Bisherige Ergebnisse:\n{prior}\n\n---\n" if prior else "")
                     + (f"Web-Suchergebnisse:\n{_web_ctx}\n\n---\n" if _web_ctx else "")
                     + f"AKTUELLER SCHRITT {i + 1}/{len(steps)}: {step}")
            _res = await _run(_sys, _user, max(300, min(int(_ctx * 0.35), 1500)))
            results.append((step, _res))

        yield {"type": "progress", "message": "🧩 Fasse die Teilergebnisse zusammen…"}
        _all = "\n\n".join(f"### Schritt {i + 1}: {s}\n{r}" for i, (s, r) in enumerate(results))
        if len(_all) > _budget:
            _all = "…\n" + _all[-_budget:]
        _ssys = ("Du fasst die Ergebnisse eines mehrstufigen Arbeitsablaufs zu EINEM "
                 "zusammenhängenden, gut strukturierten Gesamtergebnis zusammen (Markdown: "
                 "## Überschriften, **Fett**, Aufzählungen/Tabellen wo sinnvoll). Führe die "
                 "Teilergebnisse logisch zusammen und schließe mit einem klaren Fazit ab.")
        if goal:
            _ssys += f"\n\nZiel des Ablaufs: {goal}"
        _final = await _run(_ssys, f"Schritt-Ergebnisse:\n{_all}\n\n---\nErstelle das zusammenhängende Gesamtergebnis.",
                            max(500, min(int(_ctx * 0.5), 2200)))
    except httpx.ConnectError:
        yield {"type": "error", "message": "Ollama nicht erreichbar – läuft der lokale Server?"}
        return
    except httpx.HTTPStatusError as e:
        _sc = getattr(e.response, "status_code", 0) or 0
        yield {"type": "error", "message": f"Modell abgelehnt (HTTP {_sc}) – weniger/kürzere Schritte oder lokales Modell."}
        return
    except Exception as e:
        yield {"type": "error", "message": f"Arbeitsablauf fehlgeschlagen: {e}"}
        return

    if not _final.strip():
        yield {"type": "error", "message": "Der Arbeitsablauf lieferte kein Gesamtergebnis."}
        return
    _w = _final.split(" ")
    for _i, _t in enumerate(_w):
        yield {"type": "text", "content": _t + (" " if _i < len(_w) - 1 else "")}
        await asyncio.sleep(0.003)
    yield {"type": "result", "summary": _final, "tok": _tok}


_TODO_ASK_CORE_SYSTEM = (
    "Du beantwortest Fragen über einen To-Do-/Projektbestand ausschließlich anhand der "
    "übergebenen Daten. Erfinde nichts. Bei Personen-/Kollegen-Auswertungen bleibe sachlich "
    "und neutral. Antworte knapp und strukturiert auf Deutsch (Markdown)."
)


async def _todo_ask_core(question: str, root: str = "", model: Optional[str] = None):
    """To-Do-Bestand befragen als Assistent-Werkzeug (lokal-bevorzugt). Schlanke Variante
    von ``/api/todo/ask`` (Einzelabfrage oder Map-Reduce), yieldet Fortschritt + Antwort."""
    question = (question or "").strip()
    if not question:
        yield {"type": "error", "message": "Keine Frage an den To-Do-Bestand angegeben."}
        return
    _model = await _analysis_model(model)
    if not _model:
        yield {"type": "error", "message": "Kein lokales LLM verfügbar – die To-Do-Auswertung läuft "
               "standardmäßig lokal (Profil-Schalter „API-Modelle für vertrauliche Auswertungen“)."}
        return
    yield {"type": "progress", "message": "✅ Durchsuche den To-Do-Bestand…"}

    root = (root or "").strip()
    if root and root != "root":
        ids = await _db.todo_descendants(root)
    else:
        ids = [p["id"] for p in await _db.todo_projects_all()]
    data = await _db.todo_graph_data(ids)
    att_by_item: dict = {}
    try:
        for a in (await _db.todo_export()).get("attachments", []):
            txt = (a.get("md_text") or "").strip()
            if txt:
                att_by_item.setdefault(a.get("item_id"), []).append(txt)
    except Exception:
        att_by_item = {}
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
        _msg = "Es sind keine Aufgaben im gewählten Bereich vorhanden."
        yield {"type": "text", "content": _msg}
        yield {"type": "result", "summary": _msg, "tok": {"in": 0, "out": 0}}
        return

    scope_hint = (f"{n_items} Aufgaben, {len(persons)} Personen"
                  + (f", Zuständige: {', '.join(sorted(persons))}" if persons else ""))
    num_ctx = _profile_num_ctx()
    budget = max(2000, int(num_ctx * 3.2))
    MAX_GROUPS = 6
    joined_all = "\n\n".join(blocks)
    cap = MAX_GROUPS * budget
    if len(joined_all) > cap:
        kept, acc = [], 0
        for blk in blocks:
            if acc + len(blk) > cap:
                break
            kept.append(blk); acc += len(blk)
        blocks = kept or [joined_all[:budget]]
        yield {"type": "notice", "message": "Großer Bestand – nur ein Teil ausgewertet (für Details ein Einzelprojekt aktivieren)."}
    _tok = {"in": 0, "out": 0}
    try:
        async with _model_session(_model), httpx.AsyncClient(timeout=300) as client:
            async def _run(system: str, user: str) -> str:
                r = await _llm.chat(client, {
                    "model": _model, "think": False, "stream": False,
                    "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
                    "options": {"num_ctx": num_ctx}, "keep_alive": KEEP_ALIVE,
                })
                r.raise_for_status()
                jj = r.json()
                a, b = _llm_tok(jj)
                _tok["in"] += a; _tok["out"] += b
                return re.sub(r"<think>.*?</think>", "", jj.get("message", {}).get("content", ""), flags=re.DOTALL).strip()
            joined = "\n\n".join(blocks)
            if len(joined) <= budget:
                answer = await _run(_TODO_ASK_CORE_SYSTEM,
                                    f"To-Do-Daten (Überblick: {scope_hint}):\n\n{joined}\n\nFrage: {question}")
            else:
                groups, cur, cur_len = [], [], 0
                for blk in blocks:
                    if cur and cur_len + len(blk) > budget:
                        groups.append("\n\n".join(cur)); cur, cur_len = [], 0
                    cur.append(blk); cur_len += len(blk)
                if cur:
                    groups.append("\n\n".join(cur))
                map_sys = (_TODO_ASK_CORE_SYSTEM + " Dies ist NUR EIN TEIL der Daten – sammle die für "
                           "die Frage relevanten Fakten aus diesem Teil (noch keine Endantwort).")
                partials = []
                for _gi, g in enumerate(groups, start=1):
                    yield {"type": "progress", "message": f"  Werte Bereich {_gi}/{len(groups)} aus…"}
                    txt = await _run(map_sys, f"To-Do-Daten (Teil):\n\n{g}\n\nFrage: {question}")
                    if txt:
                        partials.append(txt)
                reduce_usr = (f"Frage: {question}\n\nTeil-Befunde aus dem gesamten To-Do-Bestand "
                              f"({scope_hint}):\n\n" + "\n\n---\n\n".join(partials))
                answer = await _run(_TODO_ASK_CORE_SYSTEM, reduce_usr)
    except httpx.ConnectError:
        yield {"type": "error", "message": "Ollama nicht erreichbar – läuft der lokale Server?"}
        return
    except httpx.HTTPStatusError as e:
        _sc = getattr(e.response, "status_code", 0) or 0
        yield {"type": "error", "message": f"Modell abgelehnt (HTTP {_sc}, num_ctx/VRAM?)."}
        return
    except Exception as e:
        yield {"type": "error", "message": f"To-Do-Auswertung fehlgeschlagen: {e}"}
        return

    answer = (answer or "").strip() or "Keine Antwort aus den Daten ableitbar."
    _w = answer.split(" ")
    for _i, _t in enumerate(_w):
        yield {"type": "text", "content": _t + (" " if _i < len(_w) - 1 else "")}
        await asyncio.sleep(0.003)
    yield {"type": "result", "summary": answer, "tok": _tok}


async def _patent_figures_core(description: str, claim1: str = "", model: Optional[str] = None,
                              want_image: bool = False, n: int = 2):
    """Erzeugt Patent-SKIZZEN als Entwurf (kein einreichungsfertiges Blatt): 1–3 Figuren
    je als beschriftetes Schema (Mermaid-Flowchart mit NUMMERIERTEN Bezugszeichen im Label,
    z. B. '1["Solarpanel (1)"]'), dazu eine Bezugszeichenliste und eine Figurenbeschreibung.
    Optional zusätzlich eine grobe KI-Konzeptskizze (Bild) je Leitfigur — best-effort.

    Geteilter Kern für den /projekt-Orchestrator UND den Patente-Tab. Rückgabe:
    {figures:[{caption, mermaid, image?}], bezugszeichen:[{n,label}], description, tokens}."""
    description = (description or "").strip()
    n = max(1, min(int(n or 2), 3))
    model = _pick_model(model, _model_for("general"))
    tok = {"in": 0, "out": 0}
    sys = (
        "Du bist Patentzeichner und entwirfst SKIZZEN für eine Patentanmeldung (Entwurf, keine "
        "amtliche Einreichung). Erzeuge " + str(n) + " ergänzende Figuren zu der Erfindung. JEDE "
        "Figur ist ein beschriftetes SCHEMA als gültiger Mermaid-Code ('flowchart LR' oder 'TD'), "
        "wobei die Bauteile NUMMERIERTE BEZUGSZEICHEN im Knoten-Label tragen, z. B. "
        "1[\"Solarpanel (1)\"] --> 2[\"Laderegler (2)\"]. Verwende KEINE Sonderzeichen/Klammern im "
        "Label außer der Bezugszeichen-Klammer. Liefere zusätzlich die Bezugszeichenliste "
        "(Nummer -> Bauteil) und eine kurze Figurenbeschreibung (Patentstil: 'Fig. 1 zeigt …'). "
        "Antworte NUR mit JSON: {\"figures\":[{\"caption\":\"Fig. 1: …\",\"mermaid\":\"flowchart LR\\n  "
        "1[\\\"Bauteil (1)\\\"] --> 2[\\\"Bauteil (2)\\\"]\"}],\"bezugszeichen\":[{\"n\":1,\"label\":\"Bauteil\"}],"
        "\"description\":\"Fig. 1 zeigt …\"}"
    )
    usr = (f"Erfindung:\n{description}" + (f"\n\nHauptanspruch:\n{claim1}" if (claim1 or '').strip() else ""))[:6000]
    figures, bezug, fdesc = [], [], ""
    try:
        async with _model_session(model), httpx.AsyncClient(timeout=180) as client:
            resp = await _llm.chat(client, {
                "model": model, "think": False, "stream": False, "format": "json",
                "messages": [{"role": "system", "content": sys}, {"role": "user", "content": usr}],
                "options": {"num_ctx": _profile_num_ctx()}, "keep_alive": KEEP_ALIVE,
            })
            resp.raise_for_status()
        j = resp.json()
        a, b = _llm_tok(j); tok["in"] += a; tok["out"] += b
        d = _parse_llm_json(j.get("message", {}).get("content", "")) or {}
        for f in (d.get("figures") or [])[:n]:
            if not isinstance(f, dict):
                continue
            mm = str(f.get("mermaid", "") or "").strip()
            if not re.match(r"^\s*(flowchart|graph)\b", mm):
                continue
            figures.append({"caption": str(f.get("caption", "") or "").strip()[:160], "mermaid": mm})
        for z in (d.get("bezugszeichen") or [])[:40]:
            if isinstance(z, dict) and z.get("label"):
                try:
                    bezug.append({"n": int(z.get("n", 0) or 0), "label": str(z["label"]).strip()[:80]})
                except Exception:
                    pass
        fdesc = str(d.get("description", "") or "").strip()[:3000]
    except Exception:
        pass
    # Optionale KI-Konzeptskizze (grobe Anschauung) für die Leitfigur — best-effort.
    # WICHTIG: KEIN Text/keine Beschriftungen im Bild — Bildmodelle rendern Wörter/Zahlen
    # verstümmelt. Die (korrekte) Beschriftung liefert allein das Mermaid-Schema.
    if want_image and figures and _image_model():
        try:
            _p = ("Technische Konzeptskizze der Vorrichtung als reine Schwarz-Weiß-Strichzeichnung "
                  "im Patentzeichnungs-Stil, klare Umrisslinien, KEIN Text, KEINE Beschriftungen, "
                  "keine Wörter, keine Zahlen, keine Bezugszeichen — nur Form und Aufbau: "
                  + description[:300])
            _neg = ("text, words, letters, labels, captions, numbers, digits, typography, "
                    "handwriting, watermark, signature, annotations, logo")
            _img = await _generate_image_core(_p, negative=_neg, preset="landscape")
            if isinstance(_img, dict) and _img.get("image"):
                figures[0]["image"] = _img["image"]
        except Exception:
            pass
    return {"figures": figures, "bezugszeichen": bezug, "description": fdesc, "tokens": tok}


__all__ = [
    'APP_DIR', 'AgentDef',
    '_CONFIG_FILE', '_CONFIG', 'OLLAMA_BASE', 'ALLOWED_MODELS',
    'DEFAULT_MODEL', 'CHAT_NUM_CTX', '_ALLOWED_NUM_CTX', 'KEEP_ALIVE',
    'ALLOW_PYTHON_EXEC', '_read_version', 'APP_VERSION', '_MODEL_PLACEHOLDERS',
    '_pick_model', '_raw_data_dir', 'DATA_DIR', 'UPLOADS_DIR',
    'CONVERSATIONS_DIR', 'AGENTS_DIR', 'REPORTS_DIR', 'PLANS_DIR',
    'DOSSIERS_DIR', 'CODE_DIR', 'JURIES_DIR', 'JURY_DOCS_DIR',
    'RFQ_DIR', 'PST_DIR', 'PATENTE_DIR', 'PAT_CACHE_DIR',
    'EPO_OPS_FILE', 'FIRMENPROFIL_FILE', 'RECHNUNGEN_DIR', 'ANGEBOTE_DIR',
    'ZEUGNISSE_DIR', 'VARIANTEN_DIR', 'COMPARE_DIR', 'TODO_DIR', 'TODO_ATT_DIR', 'ORCHESTRATOR_DIR',
    'MAIL_CONFIG_FILE', 'MAIL_RULES_FILE', 'MORPH_TRAIN_DIR',
    '_safe_relpath', '_plan_path', '_jury_path_by_id', '_code_path_by_id', '_jury_doc_path_by_id',
    '_load_agent_dict', '_norm_name', '_match_catalog',
    '_extract_canvas_json', '_strip_canvas_json', '_normalize_presentation', '_parse_prose_presentation', '_text_to_presentation',
    '_IMAGE_SIZES', '_image_model', '_sd_url', '_sd_reachable', '_sd_server_python', '_sd_server_dir', '_url_port', '_launch_detached', '_ensure_sd_server', '_api_image_size', '_generate_image_core', '_edit_image_core', '_upscale_image_core',
    'CAPACITY_FILE', 'BILDER_DIR', 'PROFILE_FILE', 'PROFILE_ASSETS_DIR',
    'MODE_TEMPLATES_DIR', '_mode_template_asset',
    'PROJECTS_FILE', 'FEEDBACK_FILE', 'TRANSCRIPTS_DIR', 'API_PROVIDERS_FILE',
    'LOG_FILE', 'RAG_IMAGES_DIR', 'EMBED_MODEL', '_extract_text', '_is_image', '_todo_root_name', '_med_transcript',
    '_derive_adaptive_prompt', '_safe_exec', '_run_python_code', '_extract_inline_tool_calls', '_strip_inline_tool_calls', '_extract_code_block',
    'STT_MODEL', 'STT_DEVICE', 'STT_COMPUTE',
    '_stt_root', 'STT_DOWNLOAD_ROOT', 'DEFAULTS_DIR', '_seed_defaults',
    'VALID_MODES', 'DEFAULT_MODE', '_MODE_PROMPTS', 'VALID_TONES',
    '_TONE_PROMPTS', '_log_active', '_LOG_MAX_BYTES', '_write_log',
    '_model_lock', '_loaded_model', '_unload_model', '_model_session',
    '_to_slug', '_unique_agent_path', '_agent_path_by_id', '_load_profile',
    '_active_mode', '_MODEL_ROLES', '_OPTIONAL_TABS', '_cfg_hidden', '_DEFAULT_HIDDEN_TABS',
    '_model_for', '_installed_local_models', '_local_llm_available', '_local_model',
    '_hartman', '_secret_local', '_web_search_allowed', '_assistant_mode',
    '_chat_agent_tools', '_confidential_api_allowed', '_analysis_model',
    '_vision_model', '_slide_fields_from_partial', '_analyze_image_core', '_MATH_KEYWORDS',
    '_MATH_SYMBOLS', '_looks_like_math', '_math_autoroute_enabled', '_research_local_only',
    '_research_model', '_research_fallback_model', '_research_llm_json', '_profile_num_ctx',
    '_BASE_GUARD', '_FORMULA_RULE', '_PLOT_RULE', '_DIAGRAM_RULE',
    '_CITATION_RULE', '_SCIENCE_PROMPT', '_RAG_OPTIMIZE_SYSTEM', '_LANG_RULE_EN',
    '_lang_rule', '_augment_prefix', '_MODE_KEYWORDS', '_mode_keywords',
    '_mode_prompt_text', '_mode_matches', '_mode_prefix', '_tone_prompt',
    '_profile_context', '_persona_prefix', '_load_projects', '_save_projects',
    '_PROJECT_STATUS_LABELS', '_update_project_fields', '_sse', '_llm_tok',
    '_parse_llm_json', '_plan_rag_context', '_plan_path_by_id',
    'CAP_LISTS_FILE', '_load_capacity_file', '_coerce_cap_list', '_save_cap_lists', '_load_cap_lists', '_load_capacity', '_save_capacity', '_capacity_context', '_coerce_capacity',
    '_deep_research_core', '_workflow_core', '_todo_ask_core', '_TODO_ASK_CORE_SYSTEM',
    '_patent_figures_core',
]
