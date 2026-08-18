"""
AI_Framework_Thomas — ChatGPT-ähnliches Interface für Ollama
FastAPI-Backend mit agentic Tool-Loop und SSE-Streaming
"""

import asyncio
import base64
import io
import json
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
DEFAULT_MODEL: str = _CONFIG.get("default_model", "ministral-3:3b")
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
    if m and _secret_local() and _llm.is_remote(m):
        m = ""  # Remote-Wahl im Geheim-Modus fallenlassen → lokaler Fallback
    if m and m not in _MODEL_PLACEHOLDERS:
        return m
    return fallback or DEFAULT_MODEL

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
TODO_DIR = DATA_DIR / "todo"   # KI-To-Do-Listen mit Wissensgraph (ein Ordner je Liste, list.json)
CAPACITY_FILE = DATA_DIR / "capacity.json"   # globale Kapazitätsliste (tab-übergreifend)
BILDER_DIR = Path(__file__).parent / "bilder"
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

for _d in [UPLOADS_DIR, CONVERSATIONS_DIR, AGENTS_DIR, REPORTS_DIR, PLANS_DIR, DOSSIERS_DIR, CODE_DIR, JURIES_DIR, JURY_DOCS_DIR, RFQ_DIR, PST_DIR, RECHNUNGEN_DIR, ANGEBOTE_DIR, ZEUGNISSE_DIR, VARIANTEN_DIR, TODO_DIR, PROFILE_ASSETS_DIR, TRANSCRIPTS_DIR]:
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
VALID_MODES = {"maschinenbau", "ki", "soziales", "marketing", "finanz", "geschaeftsfuehrung", "custom"}
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


# Drei Modell-Rollen im Profil: Allgemein / Programmieren / Wissenschaftlich.
# Jede Rolle kann ein eigenes (bei Bedarf nachgeladenes) LLM zugewiesen bekommen;
# leer → Standardmodell (ministral-3:3b).
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
    if val and _secret_local() and _llm.is_remote(val):
        val = ""
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
    installed = await _installed_local_models()
    if not installed:
        return None
    def _ok(m: Optional[str]) -> bool:
        return bool(m) and not _llm.is_remote(m) and m in installed
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


# ── Automatische Mathe-Weiche ────────────────────────────────────────────────
# Wunsch: Solange im Chat nur das schwache Standardmodell (ministral-3:3b) aktiv
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
    if _research_local_only() and _llm.is_remote(m):
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


@app.on_event("startup")
async def _startup():
    await _db.init()
    await _db.migrate_json(CONVERSATIONS_DIR)
    # To-Do-Projekte einmalig aus den alten JSON-Dateien in die DB übernehmen
    # (Wurzelprojekt = Benutzername); Alt-JSON bleibt liegen, bis der DB-Betrieb steht.
    await _db.migrate_todo_json(TODO_DIR, _todo_root_name())
    await _seed_todo_demo()

# ── Tool-Definitionen für Ollama ──────────────────────────────────────────────

TOOL_DEFS = [
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": (
                "Sucht im Internet nach aktuellen Informationen, Fakten, News oder technischen Inhalten. "
                "Verwende dieses Tool immer, wenn aktuelle oder unbekannte Informationen benötigt werden."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Die Suchanfrage"},
                    "num_results": {"type": "integer", "default": 6, "description": "Anzahl Ergebnisse"},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "calculate",
            "description": (
                "Führt Python-Code für Berechnungen aus. Gibt numerische Ergebnisse, Statistiken "
                "oder Tabellen aus. Nutze print() für Ausgaben. math und numpy sind verfügbar."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "code": {"type": "string", "description": "Auszuführender Python-Code"}
                },
                "required": ["code"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_presentation",
            "description": (
                "Erstellt eine Präsentation mit mehreren Folien. "
                "Die Folien werden auf dem HTML5-Canvas gerendert und können als PPTX exportiert werden."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "theme": {
                        "type": "string",
                        "enum": ["dark", "blue", "light", "green"],
                        "default": "dark",
                    },
                    "slides": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "layout": {
                                    "type": "string",
                                    "enum": ["title", "bullets", "two-column", "section", "blank"],
                                },
                                "title": {"type": "string"},
                                "content": {"type": "string"},
                                "bullets": {"type": "array", "items": {"type": "string"}},
                                "left": {"type": "string"},
                                "right": {"type": "string"},
                            },
                        },
                    },
                },
                "required": ["title", "slides"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "unit_convert",
            "description": (
                "Rechnet physikalische Einheiten um. Unterstützt Länge, Masse, Kraft, Druck, "
                "Energie, Temperatur, Drehmoment, Leistung, Fläche, Volumen und mehr."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "value": {"type": "number", "description": "Zahlenwert"},
                    "from_unit": {"type": "string", "description": "Quelleinheit (z.B. 'MPa', 'kN', 'inch', 'lbf')"},
                    "to_unit": {"type": "string", "description": "Zieleinheit (z.B. 'Pa', 'N', 'mm', 'N')"},
                },
                "required": ["value", "from_unit", "to_unit"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "solve_equation",
            "description": (
                "Löst algebraische Gleichungen oder Gleichungssysteme symbolisch. "
                "Gibt exakte und numerische Lösungen zurück. Beispiel: '2*x**2 + 3*x - 5 = 0'"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "expression": {
                        "type": "string",
                        "description": "Gleichung als String, z.B. 'x**2 - 4 = 0' oder 'sin(x) - 0.5'"
                    },
                    "variable": {
                        "type": "string",
                        "default": "x",
                        "description": "Variable nach der aufgelöst wird"
                    },
                },
                "required": ["expression"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "plot_chart",
            "description": (
                "Erstellt ein 2D-Diagramm aus DISKRETEN Wertepaaren (Mess-/Datenpunkte) — Linien-, "
                "Balken- oder Streudiagramm — und zeigt es direkt an. Ideal für tabellarische Messdaten, "
                "Kennlinien aus Datenpunkten etc. NICHT für mathematische Funktionen verwenden "
                "(f(x)=…, sin(x), x^2, sqrt(x)) — dafür ist plot_function da; sonst entsteht aus wenigen "
                "Stützpunkten ein grober Zickzack-Linienzug statt einer glatten Kurve."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "x_data": {"type": "array", "items": {"type": "number"}, "description": "X-Werte"},
                    "y_data": {"type": "array", "items": {"type": "number"}, "description": "Y-Werte (Hauptreihe)"},
                    "title": {"type": "string", "description": "Diagrammtitel"},
                    "x_label": {"type": "string", "description": "Bezeichnung X-Achse"},
                    "y_label": {"type": "string", "description": "Bezeichnung Y-Achse"},
                    "chart_type": {
                        "type": "string",
                        "enum": ["line", "bar", "scatter"],
                        "default": "line",
                    },
                    "series_label": {"type": "string", "description": "Legende Hauptreihe"},
                    "y2_data": {"type": "array", "items": {"type": "number"}, "description": "Optional: zweite Y-Reihe (gestrichelt)"},
                    "y2_label": {"type": "string", "description": "Legende zweite Reihe"},
                },
                "required": ["x_data", "y_data"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "plot_function",
            "description": (
                "Zeichnet den Graphen einer mathematischen Funktion und zeigt ihn direkt an. "
                "IMMER verwenden, wenn der Nutzer eine Funktion nennt (z. B. f(x)=x^2, sin(x), "
                "sqrt(x), 2x+1) oder einen Graphen/Plot/Verlauf/eine Kennlinie wünscht. "
                "Versteht ^ als Potenz, implizite Multiplikation (2x) und einen 'f(x)='/'y='-Vorsatz; "
                "mehrere Funktionen mit ';' trennen, um sie zu vergleichen."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "expression": {"type": "string", "description": "Funktionsterm, z. B. 'x^2', 'sin(x)', 'sqrt(x)'. Mehrere mit ';' getrennt."},
                    "var": {"type": "string", "default": "x", "description": "Variable (Standard: x)"},
                    "x_min": {"type": "number", "default": -10, "description": "Untere Bereichsgrenze"},
                    "x_max": {"type": "number", "default": 10, "description": "Obere Bereichsgrenze"},
                    "title": {"type": "string", "description": "Diagrammtitel (optional)"},
                    "x_label": {"type": "string", "description": "Bezeichnung X-Achse (optional)"},
                    "y_label": {"type": "string", "description": "Bezeichnung Y-Achse (optional)"},
                },
                "required": ["expression"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "material_lookup",
            "description": (
                "Sucht Werkstoffeigenschaften in der integrierten Datenbank: E-Modul, Streckgrenze, "
                "Zugfestigkeit, Dichte, Wärmeausdehnung etc. Unterstützt Stähle, Alu, Titan, "
                "Gusseisen, Kunststoffe, NE-Metalle."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Werkstoffbezeichnung, z.B. 'S355', '42CrMo4', '1.4301', 'AlMg3', 'PEEK'"
                    },
                    "prop": {
                        "type": "string",
                        "description": "Optionale spezifische Eigenschaft, z.B. 'E_GPa', 'Rm_MPa', 'density'"
                    },
                },
                "required": ["name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "bolt_calculator",
            "description": (
                "Schraubenauslegung nach VDI 2230 (vereinfacht): berechnet Spannungsquerschnitt, "
                "Zugspannung, Vergleichsspannung, Anzugsmoment und Auslastung."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "d_nom": {"type": "number", "description": "Nenndurchmesser [mm], z.B. 12 für M12"},
                    "pitch": {"type": "number", "description": "Gewindesteigung [mm], z.B. 1.75 für M12"},
                    "f_axial": {"type": "number", "description": "Axialkraft / Betriebskraft [kN]"},
                    "mu": {"type": "number", "default": 0.15, "description": "Reibungszahl (Standard: 0,15)"},
                    "material_class": {
                        "type": "string",
                        "enum": ["4.6", "5.6", "6.8", "8.8", "10.9", "12.9"],
                        "default": "8.8",
                        "description": "Festigkeitsklasse der Schraube",
                    },
                    "f_transverse": {"type": "number", "default": 0, "description": "Querkraft [kN] (optional)"},
                },
                "required": ["d_nom", "pitch", "f_axial"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "generate_report",
            "description": (
                "Erstellt einen formatierten Ingenieurbericht als PDF (LaTeX) oder DOCX. "
                "Enthält Titelseite, Inhaltsverzeichnis, Abschnitte mit Text, Gleichungen und Tabellen. "
                "Gibt einen Download-Link zurück."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "Berichtstitel"},
                    "author": {"type": "string", "description": "Autor (optional)"},
                    "sections": {
                        "type": "array",
                        "description": "Abschnitte des Berichts",
                        "items": {
                            "type": "object",
                            "properties": {
                                "heading": {"type": "string"},
                                "content": {"type": "string", "description": "Fließtext (Absätze mit Leerzeile trennen)"},
                                "equations": {
                                    "type": "array",
                                    "items": {"type": "string"},
                                    "description": "LaTeX-Gleichungen (ohne $), z.B. ['\\\\sigma = F/A']"
                                },
                                "table": {
                                    "type": "object",
                                    "properties": {
                                        "headers": {"type": "array", "items": {"type": "string"}},
                                        "rows": {"type": "array", "items": {"type": "array"}},
                                    },
                                },
                                "subsections": {
                                    "type": "array",
                                    "items": {
                                        "type": "object",
                                        "properties": {
                                            "heading": {"type": "string"},
                                            "content": {"type": "string"},
                                        },
                                    },
                                },
                            },
                        },
                    },
                },
                "required": ["title", "sections"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_spreadsheet",
            "description": (
                "Erstellt eine Tabelle/Spreadsheet mit Spaltenüberschriften und Datenzeilen. "
                "Wird im Canvas gerendert und kann als XLSX exportiert werden."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "headers": {"type": "array", "items": {"type": "string"}},
                    "rows": {"type": "array", "items": {"type": "array"}},
                },
                "required": ["headers", "rows"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "route_planner",
            "description": (
                "Berechnet eine Route von einem Ort A zu einem Ort B über OpenStreetMap "
                "(Geocoding via Nominatim, Routing via OSRM) und zeigt sie als interaktive "
                "Karte an. Verwende dieses Tool immer, wenn nach dem Weg, der Strecke, "
                "der Fahrzeit oder der Route zwischen zwei Orten gefragt wird."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "origin": {"type": "string", "description": "Startort, z.B. 'Stuttgart' oder 'Hauptbahnhof München'"},
                    "destination": {"type": "string", "description": "Zielort, z.B. 'Berlin' oder 'Marienplatz München'"},
                    "profile": {
                        "type": "string",
                        "enum": ["driving", "walking", "cycling"],
                        "default": "driving",
                        "description": "Fortbewegungsart (Auto, zu Fuß, Fahrrad)",
                    },
                },
                "required": ["origin", "destination"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_diagram",
            "description": (
                "Erstellt ein Datenfluss-, Ablauf-, Sequenz-, Klassen- oder "
                "Zustandsdiagramm mit Mermaid-Syntax. Verwende dieses Tool immer, "
                "wenn du einen Prozess, Datenfluss, eine Systemarchitektur oder "
                "Abhängigkeiten zwischen Komponenten grafisch darstellen möchtest."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "diagram_type": {
                        "type": "string",
                        "enum": ["flowchart", "sequenceDiagram", "classDiagram",
                                 "stateDiagram-v2", "erDiagram", "gantt", "pie"],
                        "description": "Mermaid-Diagrammtyp",
                    },
                    "definition": {
                        "type": "string",
                        "description": "Vollständige Mermaid-Diagrammdefinition",
                    },
                    "title": {
                        "type": "string",
                        "description": "Optionaler Titel des Diagramms",
                    },
                },
                "required": ["diagram_type", "definition"],
            },
        },
    },
]

ALL_TOOL_NAMES = {t["function"]["name"] for t in TOOL_DEFS}

# Code-Interpreter-Tool für den Chat — bewusst NICHT in TOOL_DEFS, damit es nur dann
# angeboten wird, wenn das Profil-Häkchen »Erweiterte Chat-Werkzeuge« gesetzt ist
# (und die serverseitige Ausführung erlaubt ist). Führt Python in derselben Sandbox
# wie der Code-Tab aus (stdout/stderr + matplotlib-Bilder).
_RUN_PYTHON_TOOL_DEF = {
    "type": "function",
    "function": {
        "name": "run_python",
        "description": (
            "Führt Python-Code in einer sicheren Sandbox aus, um eine Aufgabe rechnerisch zu "
            "LÖSEN oder zu PRÜFEN (Berechnungen, Datenanalyse, Simulationen, Diagramme mit "
            "matplotlib). Liefert stdout/stderr; erzeugte Plots werden dem Nutzer angezeigt. "
            "Nutze es bei komplexen/zahlenlastigen Fragen statt selbst zu rechnen. Kein Datei- "
            "oder Netzzugriff."
        ),
        "parameters": {
            "type": "object",
            "properties": {"code": {"type": "string", "description": "Auszuführender Python-Code"}},
            "required": ["code"],
        },
    },
}

# Bild-Werkzeug für den Chat — nur im Assistent-Modus angeboten (und nur wenn ein
# Bildmodell konfiguriert ist). Erzeugt ein Bild direkt aus der Unterhaltung.
_GENERATE_IMAGE_TOOL_DEF = {
    "type": "function",
    "function": {
        "name": "generate_image",
        "description": (
            "Erzeugt ein BILD aus einer Text-Beschreibung (Foto, Illustration, Konzept, Logo …). "
            "Verwende es, wenn der Nutzer ein Bild/eine Grafik/ein Motiv erzeugt haben möchte. "
            "Formuliere eine anschauliche englische oder deutsche Prompt-Beschreibung."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "prompt": {"type": "string", "description": "Bildbeschreibung"},
                "size": {"type": "string", "enum": ["square", "landscape", "portrait"],
                         "description": "Seitenverhältnis (Standard square)"},
            },
            "required": ["prompt"],
        },
    },
}


# ── Pydantic-Modelle ──────────────────────────────────────────────────────────


class ChatMessage(BaseModel):
    role: str
    content: str
    files: Optional[List[str]] = None


class ChatRequest(BaseModel):
    messages: List[ChatMessage]
    model: str = DEFAULT_MODEL
    agent_id: Optional[str] = None
    use_tools: bool = True
    web_search: bool = False   # Websuche-Tool nur anbieten, wenn der Schalter aktiv ist
    conversation_id: Optional[str] = None
    rag_collections: List[str] = []
    science: bool = False   # Wissenschaftsmodus (z. B. Matrix-Recherche)
    show_thinking: bool = False   # Denkprozess des Modells als eigene SSE-Frames mitsenden


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


class ResearchRequest(BaseModel):
    topic: str
    aspects: List[str]
    model: str = ""   # leer → Wissenschafts-Modell aus dem Profil


class DeepDiveRequest(BaseModel):
    """Deepdive: aus der letzten Antwort X Vertiefungsfragen ableiten und der Reihe
    nach abarbeiten (je Frage eine Websuche + optional RAG → eine Antwort)."""
    last_answer: str = ""           # letzte Assistenten-Antwort = Kontext / Vorwort
    topic: str = ""                 # letzte Nutzerfrage (Themenanker)
    count: int = 5                  # X — Anzahl Fragen/Kapitel
    model: str = ""                 # leer → aktuelles Chat-Modell (general)
    as_document: bool = False       # True → /ddd (Vorwort + Kapitel), False → /dd
    web_search: bool = True
    rag_collections: List[str] = []


# ── Routen ────────────────────────────────────────────────────────────────────


@app.get("/api/models")
async def get_models():
    # Lokale Ollama-Modelle + konfigurierte Remote-Modelle (externe API-Anbieter)
    # in EINER Liste, damit die Profil-Rollen-Selects beide anbieten. Remote-Modelle
    # tragen das Präfix "<provider_id>::<model>" und sind mit remote:True markiert.
    result = {"models": []}
    async with httpx.AsyncClient(timeout=10) as client:
        try:
            resp = await client.get(f"{OLLAMA_BASE}/api/tags")
            data = resp.json()
            if ALLOWED_MODELS:
                order = {n: i for i, n in enumerate(ALLOWED_MODELS)}
                data["models"] = sorted(
                    data.get("models", []),
                    key=lambda m: order.get(m["name"], 999),
                )
            result["models"] = data.get("models", [])
        except Exception as e:
            result["error"] = str(e)
    try:
        remote = await _llm.list_remote_models()
        result["models"] = list(result["models"]) + remote
    except Exception:
        pass
    return result


@app.post("/api/model/activate")
async def activate_model(req: Request):
    """Lädt proaktiv das Modell einer Funktion/Rolle in den VRAM (Vorwärmen beim
    Funktionswechsel). Bei Modellwechsel entlädt _model_session das vorherige Modell
    automatisch – so ist beim ersten Senden im neuen Tab schon das richtige LLM
    geladen. Für Remote-Modelle ein No-op. Idempotent: bereits geladenes Modell wird
    nicht erneut geladen."""
    body = await req.json()
    role = str(body.get("role", "") or "").strip().lower()
    if role in _MODEL_ROLES:
        model = _model_for(role)
    else:
        model = _pick_model(body.get("model"), DEFAULT_MODEL)
    if _llm.is_remote(model):
        return {"model": model, "remote": True, "switched": False}
    if _loaded_model == model:
        return {"model": model, "remote": False, "switched": False}
    try:
        async with _model_session(model), httpx.AsyncClient(timeout=120) as client:
            # Leerer Prompt an /api/generate lädt das Modell, ohne zu generieren.
            await client.post(f"{OLLAMA_BASE}/api/generate", json={
                "model": model, "prompt": "", "stream": False,
                "keep_alive": KEEP_ALIVE, "options": {"num_ctx": _profile_num_ctx()},
            })
    except Exception:
        pass
    return {"model": model, "remote": False, "switched": True}


# ── Externe KI-Anbieter (OpenAI-kompatibel) ─────────────────────────────────────
# Konfiguration in data/api_providers.json (enthält API-Keys → gitignored, NICHT im
# Backup). Modelle dieser Anbieter erscheinen präfigiert in /api/models und damit in
# den Profil-Rollen-Selects; die LLM-Abstraktion (tools/llm.py) routet Aufrufe an
# „<id>::<model>" automatisch an den jeweiligen Anbieter.

def _load_api_providers() -> list:
    if not API_PROVIDERS_FILE.exists():
        return []
    try:
        data = json.loads(API_PROVIDERS_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _save_api_providers(items: list) -> None:
    API_PROVIDERS_FILE.write_text(json.dumps(items, ensure_ascii=False, indent=2),
                                  encoding="utf-8")


def _provider_public(p: dict) -> dict:
    """Anbieter ohne API-Key (für die Anzeige im Frontend)."""
    return {"id": p.get("id"), "name": p.get("name"), "base_url": p.get("base_url"),
            "models": p.get("models", []), "has_key": bool(p.get("api_key"))}


@app.get("/api/providers")
async def list_providers():
    return [_provider_public(p) for p in _load_api_providers()]


@app.post("/api/providers")
async def save_provider(req: Request):
    """Anbieter anlegen oder aktualisieren. Body: {id?, name, base_url, api_key?,
    models?}. Ist keine Modell-Liste angegeben, wird sie (best effort) vom Anbieter
    geholt. Ein leeres api_key bei vorhandenem Anbieter behält den alten Key."""
    body = await req.json()
    name = (body.get("name") or "").strip()
    base_url = (body.get("base_url") or "").strip().rstrip("/")
    if not name or not base_url:
        raise HTTPException(status_code=400, detail="Name und Base-URL erforderlich")
    items = _load_api_providers()
    pid = (body.get("id") or "").strip()
    existing = next((p for p in items if p.get("id") == pid), None) if pid else None

    api_key = body.get("api_key")
    if not api_key and existing:
        api_key = existing.get("api_key", "")
    api_key = (api_key or "").strip()

    if not pid:
        pid = _to_slug(name)[:20] or "provider"
        # Kollision vermeiden
        base_pid, i = pid, 2
        while any(p.get("id") == pid for p in items):
            pid = f"{base_pid}{i}"; i += 1

    models = body.get("models") or []
    prov = {"id": pid, "name": name, "base_url": base_url, "api_key": api_key,
            "models": [str(m) for m in models]}
    if not prov["models"]:
        try:
            prov["models"] = await _llm.fetch_provider_models(prov)
        except Exception:
            prov["models"] = []

    if existing:
        existing.update(prov)
    else:
        items.append(prov)
    _save_api_providers(items)
    return _provider_public(prov)


@app.delete("/api/providers/{pid}")
async def delete_provider(pid: str):
    items = [p for p in _load_api_providers() if p.get("id") != pid]
    _save_api_providers(items)
    return {"ok": True}


@app.post("/api/providers/test")
async def test_provider(req: Request):
    """Verbindung testen / Modell-Liste holen. Body: {base_url, api_key} ODER {id}."""
    body = await req.json()
    pid = (body.get("id") or "").strip()
    if pid:
        prov = next((p for p in _load_api_providers() if p.get("id") == pid), None)
        if not prov:
            raise HTTPException(status_code=404, detail="Anbieter nicht gefunden")
    else:
        prov = {"base_url": (body.get("base_url") or "").strip().rstrip("/"),
                "api_key": (body.get("api_key") or "").strip()}
        if not prov["base_url"]:
            raise HTTPException(status_code=400, detail="Base-URL erforderlich")
    try:
        models = await _llm.fetch_provider_models(prov)
        return {"ok": True, "models": models}
    except Exception as e:
        return {"ok": False, "error": str(e)}


@app.post("/api/research")
async def research(request: ResearchRequest):
    return StreamingResponse(
        _research_generator(request),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


async def _research_generator(request: ResearchRequest):
    import re
    from tools.search import search_with_sources

    aspects = [a.strip() for a in request.aspects if a.strip()]
    if not aspects:
        yield _sse({"type": "error", "message": "Keine Aspekte angegeben"})
        return

    # Recherche ist immer wissenschaftlich → Wissenschafts-Modell (sofern nicht
    # explizit ein gültiges Modell angefordert wurde).
    # Profil-Schalter „Web-Recherche lokal": externes Modell auf ein lokales umbiegen.
    _r_model, _r_err = await _research_model(request.model)
    if _r_err:
        yield _sse({"type": "error", "message": _r_err})
        return

    yield _sse({"type": "research_start", "topic": request.topic, "aspects": aspects})
    tasks = [search_with_sources(f"{request.topic} {aspect}", 5) for aspect in aspects]
    raw_results = await asyncio.gather(*tasks, return_exceptions=True)

    aspect_data = []
    all_sources = []
    for aspect, raw in zip(aspects, raw_results):
        if isinstance(raw, Exception):
            sources, text = [], f"Suchfehler: {raw}"
        else:
            sources, text = raw
        yield _sse({"type": "search_done", "aspect": aspect})
        aspect_data.append((aspect, text))
        all_sources.append({"aspect": aspect, "sources": sources})

    yield _sse({"type": "sources", "data": all_sources})

    yield _sse({"type": "synthesizing"})

    # Kontext-Budget: Textmenge je Aspekt an num_ctx anpassen, sonst wird bei vielen
    # Aspekten das Fenster gefüllt und der Bericht abgeschnitten (siehe _deepresearch).
    _rc = _profile_num_ctx()
    _r_per_aspect = max(400, min(2500, int((_rc * 0.55) * 3.3) // max(1, len(aspect_data))))
    synthesis_parts = [f"Thema: {request.topic}\n"]
    for aspect, result in aspect_data:
        synthesis_parts.append(f"### Suchergebnisse – {aspect}\n{result[:_r_per_aspect]}\n")

    synthesis_prompt = "\n".join(synthesis_parts) + (
        f"\n\nErstelle jetzt einen strukturierten, informativen Recherchebericht über **{request.topic}** "
        f"basierend auf den obigen Suchergebnissen. Gliederung:\n"
        f"1. Kurze Übersicht über {request.topic}\n"
        + "".join(f"{i+2}. Abschnitt: {a}\n" for i, (a, _) in enumerate(aspect_data))
        + f"{len(aspect_data)+2}. Fazit / Zusammenfassung\n\n"
        f"Schreibe auf Deutsch. Verwende Markdown (## Überschriften, **Fett**, Aufzählungen). "
        f"Sei informativ, präzise und stütze dich auf die Suchergebnisse."
    )

    try:
        _r_msgs: list = []
        _r_topic = request.topic + " " + " ".join(a for a, _ in aspect_data)
        _r_sys = "\n\n".join(p for p in (_SCIENCE_PROMPT, _augment_prefix(_r_topic)) if p)
        if _r_sys:
            _r_msgs.append({"role": "system", "content": _r_sys})
        _r_msgs.append({"role": "user", "content": synthesis_prompt})
        async with _model_session(_r_model), httpx.AsyncClient(timeout=300) as client:
            resp = await _llm.chat(client,{
                "model": _r_model,
                "think": False,
                "messages": _r_msgs,
                "stream": False,
                "options": {"num_ctx": _rc, "num_predict": max(600, int(_rc * 0.45))},
                "keep_alive": KEEP_ALIVE,
            })
            resp.raise_for_status()
            llm_result = resp.json()
    except Exception as e:
        yield _sse({"type": "error", "message": str(e)})
        return

    content = llm_result.get("message", {}).get("content", "")
    content = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL).strip()
    _r_ti, _r_to = _llm_tok(llm_result)

    words = content.split(" ")
    for i, word in enumerate(words):
        yield _sse({"type": "text", "content": word + (" " if i < len(words) - 1 else "")})
        await asyncio.sleep(0.004)

    yield _sse({"type": "done", "tokens": {"in": _r_ti, "out": _r_to}})


# ── Tiefe Recherche (Chat): Thema → automatische Teilaspekte → Websuche je Aspekt →
# quellen-gestützter Bericht mit steuerbarer Tiefe (Aspektzahl) und Länge (Wortzahl).
# Nutzt dieselben Bausteine wie /api/research (search_with_sources + Synthese), leitet
# die Aspekte aber selbst aus dem Thema ab. Web-Gate + „Web-Recherche lokal" gelten.

class DeepResearchRequest(BaseModel):
    topic: str
    depth: int = 6
    words: int = 1000
    focus: Optional[str] = None
    model: Optional[str] = None


@app.post("/api/deepresearch")
async def deep_research(request: DeepResearchRequest):
    return StreamingResponse(
        _deepresearch_generator(request),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


async def _deepresearch_generator(request: DeepResearchRequest):
    import re
    from tools.search import search_with_sources

    topic = (request.topic or "").strip()
    if not topic:
        yield _sse({"type": "error", "message": "Kein Thema angegeben."})
        return
    if not _web_search_allowed():
        yield _sse({"type": "error", "message": "Im aktuellen Modus ist die Websuche gesperrt "
                                                 "(z. B. Ausbildungs-/Hartman-Modus) — Tiefe "
                                                 "Recherche nicht möglich."})
        return
    depth = max(3, min(int(request.depth or 6), 12))
    target_words = max(200, min(int(request.words or 1000), 4000))
    focus = (request.focus or "").strip()

    _r_model, _r_err = await _research_model(request.model)
    if _r_err:
        yield _sse({"type": "error", "message": _r_err})
        return

    _tok = {"in": 0, "out": 0}

    # 1) Teilaspekte automatisch ableiten (robustes JSON)
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
        _tok["in"] += _ti
        _tok["out"] += _to
        _d = _parse_llm_json(_aj.get("message", {}).get("content", "")) or {}
        aspects = [str(a).strip() for a in (_d.get("aspects") or []) if str(a).strip()][:depth]
    except httpx.ConnectError:
        yield _sse({"type": "error", "message": "Ollama nicht erreichbar – läuft der lokale Server?"})
        return
    except Exception:
        aspects = []
    if not aspects:
        aspects = ["Überblick", "technische Daten", "Geschichte / Hintergrund",
                   "Varianten / Modelle", "Preise / Markt", "Besonderheiten / Bewertung",
                   "Vor- und Nachteile", "Alternativen"][:depth]
    yield _sse({"type": "aspects", "aspects": aspects, "topic": topic})

    # 2) je Aspekt Websuche (parallel)
    tasks = [search_with_sources(f"{topic} {a}", 5) for a in aspects]
    raw_results = await asyncio.gather(*tasks, return_exceptions=True)
    aspect_data = []
    all_sources = []
    for a, r in zip(aspects, raw_results):
        if isinstance(r, Exception):
            sources, text = [], f"Suchfehler: {r}"
        else:
            sources, text = r
        yield _sse({"type": "search_done", "aspect": a})
        aspect_data.append((a, text))
        all_sources.append({"aspect": a, "sources": sources})
    yield _sse({"type": "sources", "data": all_sources})
    yield _sse({"type": "synthesizing"})

    # 3) quellen-gestützte Synthese mit Längenziel + Anti-Halluzinations-Auflage.
    # Kontext-Budget: 12 Aspekte × 2500 Zeichen füllen ein 8k-Fenster komplett und
    # schneiden die Antwort ab. Deshalb Textmenge JE ASPEKT und Ziellänge an num_ctx
    # anpassen (grobe Schätzung ~3.3 Zeichen/Token für Deutsch) und die Ausgabe-Tokens
    # (num_predict) begrenzen, damit der Bericht sauber endet statt mittendrin abzubrechen.
    _ctx = _profile_num_ctx()
    _out_reserve_tok = max(400, min(int(target_words * 1.7), int(_ctx * 0.5)))
    _in_budget_chars = max(2500, int((_ctx - _out_reserve_tok - 700) * 3.3))
    _per_aspect = max(400, min(2500, _in_budget_chars // max(1, len(aspect_data))))
    _eff_words = max(250, min(target_words, int(_out_reserve_tok / 1.7)))
    _shortened = _eff_words < int(target_words * 0.85)
    if _shortened:
        yield _sse({"type": "notice", "message":
                    f"Hinweis: Bericht auf ~{_eff_words} statt ~{target_words} Wörter begrenzt, "
                    f"damit er ins Kontextfenster ({_ctx} Tokens) passt. Für längere Berichte im "
                    f"Profil das Kontextfenster erhöhen oder weniger Aspekte (Tiefe) wählen."})

    _parts = [f"Thema: {topic}\n"]
    if focus:
        _parts.append(f"Schwerpunkt: {focus}\n")
    for a, t in aspect_data:
        _parts.append(f"### Suchergebnisse – {a}\n{t[:_per_aspect]}\n")
    _synth = "\n".join(_parts) + (
        f"\n\nSchreibe daraus einen AUSFÜHRLICHEN, gut strukturierten Recherchebericht über "
        f"**{topic}** von **ca. {_eff_words} Wörtern** auf Deutsch (Markdown: ## Überschriften, "
        f"**Fett**, Aufzählungen, bei Kennwerten gern eine Tabelle). Gliederung: kurze Übersicht, "
        f"je ein Abschnitt pro Aspekt, abschließend ein Fazit. Halte die Ziellänge ein und schließe "
        f"mit einem vollständigen Fazit ab (nicht mitten im Satz enden). WICHTIG: Stütze JEDE konkrete "
        f"Angabe (Zahlen, technische Daten, Baujahre, Preise, Eigennamen) AUSSCHLIESSLICH auf die "
        f"obigen Suchergebnisse. Ist etwas nicht belegt oder widersprüchlich, kennzeichne es "
        f"ausdrücklich als unsicher — erfinde nichts."
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
        yield _sse({"type": "error", "message": "Ollama nicht erreichbar – läuft der lokale Server?"})
        return
    except httpx.HTTPStatusError as e:
        _sc = getattr(e.response, "status_code", 0) or 0
        if _sc in (502, 503, 504):
            _m = (f"Der Anbieter hat nicht rechtzeitig geantwortet (HTTP {_sc}). "
                  f"Bei tiefer Recherche mit vielen Aspekten kann die Synthese lange dauern — "
                  f"bitte weniger Tiefe/Umfang wählen oder ein lokales Modell verwenden.")
        else:
            _m = f"Modell abgelehnt (num_ctx/VRAM?): HTTP {_sc}"
        yield _sse({"type": "error", "message": _m})
        return
    except Exception as e:
        yield _sse({"type": "error", "message": f"Synthese fehlgeschlagen: {e}"})
        return

    content = (_j.get("message", {}) or {}).get("content", "") or ""
    content = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL).strip()
    _ti, _to = _llm_tok(_j)
    _tok["in"] += _ti
    _tok["out"] += _to
    _words = content.split(" ")
    for _i, _w in enumerate(_words):
        yield _sse({"type": "text", "content": _w + (" " if _i < len(_words) - 1 else "")})
        await asyncio.sleep(0.003)
    yield _sse({"type": "done", "tokens": _tok})


# ── Erweiterte Suche („/such"): alternative Suchbegriffe + Websuche + Zusammenfassung ──
# Der Nutzer kennt oft den treffenden (Fach-)Begriff nicht. Diese Funktion lässt das
# LLM alternative Suchbegriffe für dasselbe Anliegen erzeugen (Synonyme, Fach-/
# Umgangssprache, engl. Entsprechungen), durchsucht damit das Web (DuckDuckGo) und
# fasst die Treffer mit Quellen zusammen. Reine Wiederverwendung vorhandener Bausteine.

class SearchExpandRequest(BaseModel):
    query: str
    model: Optional[str] = None
    count: int = 6
    search: bool = True


@app.post("/api/search/expand")
async def search_expand(request: SearchExpandRequest):
    return StreamingResponse(
        _search_expand_generator(request),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


async def _search_expand_generator(request: SearchExpandRequest):
    import re
    from tools.search import search_with_sources

    query = (request.query or "").strip()
    if not query:
        yield _sse({"type": "error", "message": "Kein Suchbegriff angegeben"})
        return

    # Persona »Hartman«: Websuche komplett gesperrt (alles rein lokal).
    if not _web_search_allowed():
        yield _sse({"type": "error", "message": "REKRUT, im Ausbildungsmodus läuft NICHTS "
                                                "nach draußen – KEINE Websuche. Alles rein lokal!"})
        return

    # Profil-Schalter „Web-Recherche lokal" beachten
    _model, _m_err = await _research_model(request.model)
    if _m_err:
        yield _sse({"type": "error", "message": _m_err})
        return
    n = max(3, min(int(request.count or 6), 12))
    _ti = _to = 0

    # 1) Alternative Suchbegriffe erzeugen (JSON, robust geparst)
    term_prompt = (
        f"Der Nutzer sucht Informationen, kennt aber evtl. nicht den treffenden Fachbegriff.\n"
        f"Suchanliegen: \"{query}\"\n\n"
        f"Erzeuge {n} alternative Suchbegriffe bzw. -phrasen, die DASSELBE beschreiben – "
        f"Synonyme, Fachbegriff gegenüber Umgangssprache, enger und weiter gefasste "
        f"Formulierungen sowie die wichtigsten englischen Entsprechungen. Jeweils kurz und "
        f"suchtauglich (2–5 Wörter), keine Dopplungen.\n"
        f"Antworte NUR mit JSON: {{\"terms\":[\"…\",\"…\"]}}"
    )
    # Bei einem API-Modell, das keine Recherche zulässt, fällt der Helfer auf ein
    # lokales Modell zurück (reiner Rückfall — bevorzugt bleibt das gewählte Modell).
    data, a, b, _used = await _research_llm_json(
        _model,
        "Du bist ein Recherche-Assistent für Suchbegriffe. Antworte ausschließlich mit gültigem JSON.",
        term_prompt)
    _ti += a; _to += b
    terms = [str(t).strip() for t in (data.get("terms") or []) if str(t).strip()]
    if _used != _model:
        yield _sse({"type": "notice",
                    "message": f"API-Modell lieferte keine Suchbegriffe – lokal wiederholt ({_used})."})

    # Original zuerst, dann Alternativen; doppelte (case-insensitiv) entfernen
    ordered, seen = [], set()
    for t in [query] + terms:
        k = t.lower()
        if k and k not in seen:
            seen.add(k); ordered.append(t)
    yield _sse({"type": "terms", "query": query, "terms": ordered})

    if not request.search:
        yield _sse({"type": "done", "tokens": {"in": _ti, "out": _to}})
        return

    # 2) Websuche über die ergiebigsten Begriffe (begrenzt, parallel)
    search_terms = ordered[:4]
    yield _sse({"type": "searching", "terms": search_terms})
    results = await asyncio.gather(
        *[search_with_sources(t, 5) for t in search_terms], return_exceptions=True)

    blocks, sources, seen_url = [], [], set()
    for term, raw in zip(search_terms, results):
        if isinstance(raw, Exception):
            srcs, text = [], f"Suchfehler: {raw}"
        else:
            srcs, text = raw
        blocks.append(f"### Treffer für „{term}“\n{text[:2500]}")
        for s in srcs:
            u = s.get("url", "")
            if u and u not in seen_url:
                seen_url.add(u); sources.append(s)
    yield _sse({"type": "sources", "data": sources})
    yield _sse({"type": "synthesizing"})

    # 3) Antwort synthetisieren (Wissenschaftsmodus + Modus-Vorspann)
    synth = (
        f"Suchanliegen des Nutzers: „{query}“\n"
        f"Verwendete alternative Suchbegriffe: {', '.join(search_terms)}\n\n"
        + "\n\n".join(blocks)
        + "\n\nFasse die Suchergebnisse zu einer klaren, strukturierten Antwort auf das "
        "Suchanliegen zusammen (Deutsch, Markdown). Nenne die wichtigsten Erkenntnisse, "
        "verweise wo sinnvoll auf Quellen, und schließe mit einem kurzen Hinweis, welche "
        "Suchbegriffe am ergiebigsten waren. Wenn die Treffer dürftig sind, sage das ehrlich."
    )
    try:
        _msgs = []
        _sys = "\n\n".join(p for p in (_SCIENCE_PROMPT, _augment_prefix(query)) if p)
        if _sys:
            _msgs.append({"role": "system", "content": _sys})
        _msgs.append({"role": "user", "content": synth})
        async with _model_session(_model), httpx.AsyncClient(timeout=300) as client:
            resp = await _llm.chat(client, {
                "model": _model, "think": False, "messages": _msgs,
                "stream": False, "options": {"num_ctx": _profile_num_ctx()},
                "keep_alive": KEEP_ALIVE,
            })
            resp.raise_for_status()
            _j2 = resp.json()
        a, b = _llm_tok(_j2); _ti += a; _to += b
    except Exception as e:
        yield _sse({"type": "error", "message": str(e)})
        return

    content = _j2.get("message", {}).get("content", "")
    content = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL).strip()
    words = content.split(" ")
    for i, word in enumerate(words):
        yield _sse({"type": "text", "content": word + (" " if i < len(words) - 1 else "")})
        await asyncio.sleep(0.004)

    yield _sse({"type": "done", "tokens": {"in": _ti, "out": _to}})


# ── Dynamische Rückfragen („/frag"): Eingabemaske mit Text-/Auswahlfeldern ──────
# Erzeugt zu einer Aufgabe gezielte Rückfragen, BEVOR sie beantwortet wird. Jede
# Frage hat einen Typ (text | single | multi) und ggf. Optionen, sodass das Frontend
# eine Eingabemaske – auch mit Multiple-Choice – rendern kann. Genutzt in Chat,
# Medizin und Mathe (Feld `domain` rahmt nur den Prompt, keine Modell-Sonderlogik).

class ClarifyRequest(BaseModel):
    prompt: str
    domain: Optional[str] = "chat"
    model: Optional[str] = None
    max_questions: int = 4


_CLARIFY_DOMAIN_HINT = {
    "chat": "allgemeine Anfrage",
    "medical": "medizinische Anfrage (Symptome, Vorgeschichte, Kontext) – KEINE Diagnose, nur Präzisierung",
    "math": "mathematische Aufgabe (Gegebenes, Gesuchtes, Randbedingungen, Genauigkeit)",
}


def _normalize_clarify_questions(raw_qs, n: int) -> list:
    """Validiert/normalisiert eine rohe LLM-Frageliste zu ``[{question,type,options}]``.
    Auswahltyp ohne ≥2 Optionen wird zu Freitext. Kappt auf ``n`` Fragen."""
    questions = []
    for q in (raw_qs or [])[:n]:
        if not isinstance(q, dict):
            continue
        text = str(q.get("question") or "").strip()
        if not text:
            continue
        qtype = str(q.get("type") or "text").strip().lower()
        if qtype not in ("text", "single", "multi"):
            qtype = "text"
        opts = [str(o).strip() for o in (q.get("options") or []) if str(o).strip()][:6]
        if qtype in ("single", "multi") and len(opts) < 2:
            qtype, opts = "text", []
        questions.append({"question": text, "type": qtype, "options": opts})
    return questions


@app.post("/api/clarify")
async def clarify(request: ClarifyRequest):
    """Liefert gezielte Rückfragen (Eingabemaske) zu einer Aufgabe – oder eine leere
    Liste, wenn keine Klärung nötig ist. Robust gegen LLM-Geplapper (JSON-Extraktion)."""
    prompt = (request.prompt or "").strip()
    if not prompt:
        raise HTTPException(400, "Keine Aufgabe angegeben")
    _model = _pick_model(request.model)
    n = max(1, min(int(request.max_questions or 4), 6))
    hint = _CLARIFY_DOMAIN_HINT.get((request.domain or "chat").lower(), _CLARIFY_DOMAIN_HINT["chat"])

    user = (
        f"Fachgebiet: {hint}.\n"
        f"Aufgabe/Anfrage des Nutzers: \"{prompt}\"\n\n"
        "Entscheide, ob dir wichtige Informationen fehlen, um eine wirklich gute Antwort zu geben.\n"
        "- Wenn die Anfrage klar genug ist: gib eine LEERE Frageliste zurück.\n"
        f"- Sonst stelle bis zu {n} kurze, gezielte Rückfragen. Bevorzuge Auswahlfragen, wo es passt.\n"
        "Jede Frage ist ein Objekt: {\"question\": \"…\", \"type\": \"text\"|\"single\"|\"multi\", \"options\": [\"…\"]}.\n"
        "Bei type \"single\"/\"multi\": 2–5 sinnvolle Optionen angeben. Bei \"text\": options leer lassen.\n"
        "Antworte NUR mit JSON in diesem Format: {\"questions\": [ {\"question\":\"…\",\"type\":\"single\",\"options\":[\"A\",\"B\"]} ]}"
    )
    payload = {
        "model": _model, "think": False, "format": "json", "stream": False,
        "keep_alive": KEEP_ALIVE,
        "messages": [
            {"role": "system", "content": "Du formulierst gezielte Rückfragen zur Präzisierung einer Aufgabe. Antworte ausschließlich mit gültigem JSON."},
            {"role": "user", "content": user},
        ],
    }
    _ti = _to = 0
    try:
        async with _model_session(_model), httpx.AsyncClient(timeout=120) as client:
            resp = await _llm.chat(client, payload)
            resp.raise_for_status()
            _j = resp.json()
        _ti, _to = _llm_tok(_j)
        data = _parse_llm_json(_j.get("message", {}).get("content", "")) or {}
    except Exception as e:
        raise HTTPException(502, f"Rückfragen konnten nicht erzeugt werden: {e}")

    raw_qs = data.get("questions") if isinstance(data, dict) else None
    questions = _normalize_clarify_questions(raw_qs, n)

    return {
        "type": "questions" if questions else "none",
        "questions": questions,
        "tokens": {"in": _ti, "out": _to},
    }


class ClarifyStructureRequest(BaseModel):
    questions_text: str            # die vom Modell im Chat gestellten Rückfragen (Freitext)
    task: Optional[str] = ""       # ursprüngliche Aufgabe (für Kontext/sinnvolle Optionen)
    domain: Optional[str] = "chat"
    model: Optional[str] = None
    max_questions: int = 8


@app.post("/api/clarify/structure")
async def clarify_structure(request: ClarifyStructureRequest):
    """Wandelt bereits im Chat gestellte Rückfragen (Freitext des Modells) in eine
    strukturierte Eingabemaske um: je Frage ``{question, type, options}`` mit
    Vorauswahl (single/multi) oder Freitext. Erfindet keine neuen Themen – bildet
    nur die vorhandenen Fragen ab. Robust gegen LLM-Geplapper (JSON-Extraktion)."""
    qtext = (request.questions_text or "").strip()
    if not qtext:
        raise HTTPException(400, "Keine Rückfragen übergeben")
    _model = _pick_model(request.model)
    n = max(1, min(int(request.max_questions or 8), 12))
    hint = _CLARIFY_DOMAIN_HINT.get((request.domain or "chat").lower(), _CLARIFY_DOMAIN_HINT["chat"])
    task = (request.task or "").strip()

    user = (
        f"Fachgebiet: {hint}.\n"
        + (f"Ursprüngliche Aufgabe des Nutzers: \"{task}\"\n" if task else "")
        + "Das KI-Modell hat dem Nutzer folgende Rückfragen gestellt (Freitext):\n"
        f"\"\"\"\n{qtext}\n\"\"\"\n\n"
        "Wandle GENAU diese Rückfragen in eine ausfüllbare Maske um – erfinde keine neuen "
        f"Themen, fasse eng Zusammengehöriges zu je einer Frage zusammen (max. {n}).\n"
        "Jede Frage ist ein Objekt: {\"question\": \"…\", \"type\": \"text\"|\"single\"|\"multi\", \"options\": [\"…\"]}.\n"
        "- Nenne die Frage im Text kurz und klar.\n"
        "- Wenn die Rückfrage konkrete Alternativen aufzählt (z. B. „LED, Glühlampe, OLED?“): "
        "type \"single\" (bzw. \"multi\", falls mehrere zugleich möglich) und diese Alternativen als options.\n"
        "- Ergänze bei Auswahlfragen sinnvolle, gängige Optionen, falls im Text nur Beispiele stehen.\n"
        "- Offene Fragen ohne Alternativen: type \"text\", options leer.\n"
        "Antworte NUR mit JSON: {\"questions\": [ {\"question\":\"…\",\"type\":\"single\",\"options\":[\"A\",\"B\"]} ]}"
    )
    payload = {
        "model": _model, "think": False, "format": "json", "stream": False,
        "keep_alive": KEEP_ALIVE,
        "messages": [
            {"role": "system", "content": "Du strukturierst gestellte Rückfragen in eine ausfüllbare Maske. Antworte ausschließlich mit gültigem JSON."},
            {"role": "user", "content": user},
        ],
    }
    _ti = _to = 0
    try:
        async with _model_session(_model), httpx.AsyncClient(timeout=120) as client:
            resp = await _llm.chat(client, payload)
            resp.raise_for_status()
            _j = resp.json()
        _ti, _to = _llm_tok(_j)
        data = _parse_llm_json(_j.get("message", {}).get("content", "")) or {}
    except Exception as e:
        raise HTTPException(502, f"Rückfragen konnten nicht strukturiert werden: {e}")

    raw_qs = data.get("questions") if isinstance(data, dict) else None
    questions = _normalize_clarify_questions(raw_qs, n)
    return {
        "type": "questions" if questions else "none",
        "questions": questions,
        "tokens": {"in": _ti, "out": _to},
    }


@app.post("/api/deepdive")
async def deepdive(request: DeepDiveRequest):
    return StreamingResponse(
        _deepdive_generator(request),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


async def _deepdive_questions(model: str, context: str, topic: str, count: int,
                              tok: Optional[dict] = None) -> list:
    """Leitet aus der letzten Antwort genau ``count`` Vertiefungsfragen ab.
    Gibt eine Liste von Fragestrings zurück (Fallback: generische Fragen).
    ``tok`` (optional): dict {"in","out"}, in das der Tokenverbrauch summiert wird."""
    sys = (
        "Du bist ein gründlicher Rechercheur. Aus dem gegebenen Text leitest du "
        f"genau {count} weiterführende, eigenständige Vertiefungsfragen ab, die das "
        "Thema systematisch vertiefen (verschiedene Aspekte, keine Dopplungen). "
        "Jede Frage muss für sich als Suchanfrage funktionieren. "
        'Antworte NUR als JSON: {"questions": ["…", "…"]}.'
    )
    usr = (f"Thema/Ausgangsfrage: {topic}\n\n" if topic else "") + (
        f"Ausgangstext (letzte Antwort):\n{context[:6000]}\n\n"
        f"Formuliere genau {count} Vertiefungsfragen auf Deutsch."
    )
    try:
        async with _model_session(model), httpx.AsyncClient(timeout=180) as client:
            resp = await _llm.chat(client, {
                "model": model,
                "think": False,
                "stream": False,
                "format": "json",
                "messages": [
                    {"role": "system", "content": sys},
                    {"role": "user", "content": usr},
                ],
                "options": {"num_ctx": _profile_num_ctx()},
                "keep_alive": KEEP_ALIVE,
            })
            resp.raise_for_status()
            _dd_j = resp.json()
            if tok is not None:
                _a, _b = _llm_tok(_dd_j)
                tok["in"] += _a
                tok["out"] += _b
            data = _parse_llm_json(_dd_j.get("message", {}).get("content", "")) or {}
    except Exception:
        data = {}
    qs = data.get("questions") if isinstance(data, dict) else None
    out = [str(q).strip() for q in qs if str(q).strip()] if isinstance(qs, list) else []
    # Auf gewünschte Anzahl bringen (kürzen bzw. generisch auffüllen).
    out = out[:count]
    base = (topic or "das Thema").strip()
    while len(out) < count:
        out.append(f"Welche weiteren wichtigen Aspekte zu {base} sind relevant? (Teil {len(out)+1})")
    return out


async def _deepdive_answer(model: str, question: str, web: bool, rag_collections: list,
                           tok: Optional[dict] = None) -> str:
    """Beantwortet EINE Deepdive-Frage: Websuche (+ optional RAG) als Beleg, dann
    ein LLM-Aufruf. Gibt den fertigen Markdown-Antworttext zurück.
    ``tok`` (optional): dict {"in","out"}, in das der Tokenverbrauch summiert wird."""
    from tools.search import search_with_sources
    blocks = []
    if web:
        try:
            _, text = await search_with_sources(question, 5)
            if text:
                blocks.append("### Websuche\n" + text[:3000])
        except Exception as e:
            blocks.append(f"### Websuche\n(Suche fehlgeschlagen: {e})")
    if rag_collections:
        try:
            from tools.rag import query_collections
            hits = await query_collections(rag_collections, question, top_k_cap=5)
            if hits:
                rag_txt = "\n\n".join(
                    f"[{h.get('collection_name','?')} · {h.get('filename','?')}]\n{h.get('text','')}"
                    for h in hits
                )
                blocks.append("### Wissensdatenbank\n" + rag_txt[:3000])
        except Exception:
            pass
    grounding = "\n\n".join(blocks)
    sys = "\n\n".join(p for p in (_augment_prefix(question), _SCIENCE_PROMPT) if p)
    usr = (
        (f"Belegmaterial:\n{grounding}\n\n" if grounding else "")
        + f"Beantworte ausführlich und strukturiert (Markdown) folgende Frage:\n\n{question}\n\n"
        + ("Stütze dich auf das Belegmaterial und nenne Quellen." if grounding
           else "Antworte aus deinem Fachwissen.")
    )
    try:
        async with _model_session(model), httpx.AsyncClient(timeout=300) as client:
            resp = await _llm.chat(client, {
                "model": model,
                "think": False,
                "stream": False,
                "messages": [
                    {"role": "system", "content": sys},
                    {"role": "user", "content": usr},
                ],
                "options": {"num_ctx": _profile_num_ctx()},
                "keep_alive": KEEP_ALIVE,
            })
            resp.raise_for_status()
            _dd_j = resp.json()
            if tok is not None:
                _a, _b = _llm_tok(_dd_j)
                tok["in"] += _a
                tok["out"] += _b
            content = _dd_j.get("message", {}).get("content", "")
    except Exception as e:
        return f"_(Antwort fehlgeschlagen: {e})_"
    return re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL).strip()


async def _deepdive_generator(request: DeepDiveRequest):
    # Mit Websuche zählt der Deepdive als web-gestützte Recherche → Profil-Schalter
    # „Web-Recherche lokal" beachten; ohne Websuche bleibt die normale Modellwahl.
    if request.web_search:
        model, _m_err = await _research_model(request.model, _model_for("general"))
        if _m_err:
            yield _sse({"type": "error", "message": _m_err})
            return
    else:
        model = _pick_model(request.model, _model_for("general"))
    count = max(1, min(int(request.count or 5), 20))   # Sicherheitsgrenze
    context = (request.last_answer or request.topic or "").strip()
    if not context:
        yield _sse({"type": "error", "message": "Keine vorherige Antwort für den Deepdive vorhanden."})
        return

    yield _sse({"type": "dd_meta", "count": count, "as_document": request.as_document})

    _dd_tok = {"in": 0, "out": 0}
    # 1) Vertiefungsfragen ableiten
    questions = await _deepdive_questions(model, context, request.topic, count, tok=_dd_tok)
    yield _sse({"type": "dd_questions", "questions": questions})

    # 2) Fragen der Reihe nach abarbeiten (je Frage Suche + Antwort)
    for idx, question in enumerate(questions):
        yield _sse({"type": "dd_chapter_start", "index": idx, "question": question})
        answer = await _deepdive_answer(model, question, request.web_search, request.rag_collections, tok=_dd_tok)
        yield _sse({"type": "dd_chapter_done", "index": idx, "question": question, "answer": answer})

    yield _sse({"type": "done", "tokens": _dd_tok})


@app.post("/api/chat")
async def chat(request: ChatRequest):
    return StreamingResponse(
        _chat_generator(request),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ── RAG-API (Wissenssammlungen) ───────────────────────────────────────────────

EMBED_MODEL: str = _CONFIG.get("embed_model", "nomic-embed-text")


class RagCollectionCreate(BaseModel):
    name: str
    tier: str = "6gb"          # frei wählbares Label (z. B. Regler-Stufe)
    chunk_size: Optional[int] = None
    chunk_overlap: Optional[int] = None
    top_k: Optional[int] = None
    char_limit: Optional[int] = None
    strictness: str = "ausgewogen"   # kreativ | ausgewogen | korrekt
    clean: bool = True
    clean_level: str = "standard"    # standard | strikt
    embed_model: Optional[str] = None  # None = lokales Standardmodell (config.json)


@app.get("/api/rag/tiers")
async def rag_tiers():
    from tools.rag import TIERS, DEFAULT_TIER
    return {"tiers": TIERS, "default": DEFAULT_TIER, "embed_model": EMBED_MODEL}


@app.get("/api/rag/embed-models")
async def rag_embed_models():
    """Wählbare Embeddingmodelle für neue Sammlungen: lokal installierte
    Ollama-Modelle plus alle konfigurierten API-Modelle.

    Die Zuordnung gilt dauerhaft pro Sammlung — Vektoren unterschiedlicher
    Modelle sind nicht vergleichbar (andere Dimension/Semantik). Ein Wechsel
    erfordert Neuindizierung; das Frontend weist darauf hin."""
    local = [{"name": m, "remote": False, "provider": "Ollama (lokal)"}
             for m in await _installed_local_models()]
    try:
        remote = await _llm.list_remote_models()
    except Exception:
        remote = []
    return {"default": EMBED_MODEL, "local": local, "remote": remote}


@app.get("/api/rag/collections")
async def rag_collections():
    return await _db.rag_list_collections()


@app.post("/api/rag/collections")
async def rag_create_collection(body: RagCollectionCreate):
    from tools.rag import tier_config
    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Name fehlt")
    tc = tier_config(body.tier)
    strictness = body.strictness if body.strictness in ("kreativ", "ausgewogen", "korrekt") else "ausgewogen"
    clean_level = body.clean_level if body.clean_level in ("standard", "strikt") else "standard"
    # Embeddingmodell: lokal (Ollama) oder extern („anbieter::modell"). Die Wahl gilt
    # dauerhaft für diese Sammlung — Vektoren verschiedener Modelle sind nicht
    # vergleichbar, ein Wechsel erfordert Neuindizierung.
    embed_model = (body.embed_model or "").strip() or EMBED_MODEL
    if _llm.is_remote(embed_model):
        provider, _real = _llm.resolve(embed_model)
        if provider is None:
            raise HTTPException(status_code=400,
                                detail=f"Unbekannter API-Anbieter für Embeddingmodell '{embed_model}'.")
    coll = {
        "id": f"rag_{uuid.uuid4().hex[:12]}",
        "name": name,
        "embed_model": embed_model,
        "tier": (body.tier or "regler").strip()[:24],   # freies Anzeige-Label (Regler-Stufe)
        "chunk_size": int(body.chunk_size or tc["chunk_size"]),
        "chunk_overlap": int(body.chunk_overlap if body.chunk_overlap is not None else tc["chunk_overlap"]),
        "top_k": int(body.top_k or tc["top_k"]),
        "embed_gpu": False,   # auf kleinen Karten immer CPU (verdrängt das Chat-Modell nicht)
        "clean": bool(body.clean),
        "clean_level": clean_level,
        "char_limit": int(body.char_limit or tc["char_limit"]),
        "strictness": strictness,
        "created_at": time.time(),
    }
    await _db.rag_create_collection(coll)
    return coll


@app.delete("/api/rag/collections/{cid}")
async def rag_delete_collection(cid: str):
    await _db.rag_delete_collection(cid)
    return {"ok": True}


@app.get("/api/rag/collections/{cid}/documents")
async def rag_documents(cid: str):
    return await _db.rag_list_documents(cid)


@app.post("/api/rag/collections/{cid}/documents")
async def rag_add_document(cid: str, file: UploadFile = File(...)):
    from tools.rag import ingest_file
    coll = await _db.rag_get_collection(cid)
    if not coll:
        raise HTTPException(status_code=404, detail="Sammlung nicht gefunden")
    # Upload temporär ablegen, Text extrahieren
    tmp = UPLOADS_DIR / f"rag_{uuid.uuid4().hex}_{file.filename}"
    async with aiofiles.open(tmp, "wb") as fh:
        await fh.write(await file.read())
    try:
        text = _extract_text(tmp)
    finally:
        try:
            tmp.unlink()
        except Exception:
            pass
    if not text or text.startswith("[Lesefehler"):
        raise HTTPException(status_code=400, detail=f"Text konnte nicht extrahiert werden: {text}")
    try:
        n = await ingest_file(coll, text, file.filename, f"doc_{uuid.uuid4().hex[:12]}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    return {"ok": True, "filename": file.filename, "n_chunks": n}


async def _optimize_chunk_for_rag(chunk: str, model: str, tok: Optional[dict] = None) -> str:
    """Ruft das LLM auf, um einen Textabschnitt RAG-konform aufzubereiten.
    ``tok`` (optional): dict {"in","out"}, in das der Tokenverbrauch summiert wird."""
    try:
        async with httpx.AsyncClient(timeout=120) as client:
            resp = await _llm.chat(client,{
                "model": model,
                "think": False,
                "stream": False,
                "messages": [
                    {"role": "system", "content": _RAG_OPTIMIZE_SYSTEM},
                    {"role": "user", "content": chunk},
                ],
            })
            resp.raise_for_status()
            _ro_j = resp.json()
            if tok is not None:
                _a, _b = _llm_tok(_ro_j)
                tok["in"] += _a
                tok["out"] += _b
            content = _ro_j.get("message", {}).get("content", "").strip()
            # Thinking-Tags und Code-Fences entfernen
            content = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL).strip()
            content = re.sub(r"^```[^\n]*\n?|```$", "", content, flags=re.MULTILINE).strip()
            return content if content else chunk
    except Exception:
        return chunk  # Fallback: Originaltext


@app.post("/api/rag/collections/{cid}/documents/optimized")
async def rag_add_document_optimized(cid: str, file: UploadFile = File(...)):
    """Lädt ein Dokument hoch, optimiert es per LLM für RAG und ingestiert es.
    Gibt SSE-Fortschrittsereignisse zurück."""
    from tools.rag import ingest_file

    async def _stream():
        coll = await _db.rag_get_collection(cid)
        if not coll:
            yield _sse({"type": "error", "message": "Sammlung nicht gefunden"})
            return

        # Datei temporär speichern und Text extrahieren
        tmp = UPLOADS_DIR / f"rag_opt_{uuid.uuid4().hex}_{file.filename}"
        async with aiofiles.open(tmp, "wb") as fh:
            await fh.write(await file.read())
        try:
            raw_text = _extract_text(tmp)
        finally:
            try:
                tmp.unlink()
            except Exception:
                pass

        if not raw_text or raw_text.startswith("[Lesefehler"):
            yield _sse({"type": "error", "message": f"Text konnte nicht extrahiert werden: {raw_text}"})
            return

        # Text in ~2 500-Zeichen-Abschnitte aufteilen (für LLM-Verarbeitung)
        CHUNK = 2500
        STEP  = 2300
        chunks = [raw_text[i:i + CHUNK] for i in range(0, len(raw_text), STEP)]
        total = len(chunks)
        yield _sse({"type": "progress", "step": f"Extrahiert: {len(raw_text):,} Zeichen, {total} Abschnitte", "pct": 5})

        model = _model_for("general")
        optimized_parts: list[str] = []
        _tok = {"in": 0, "out": 0}
        async with _model_session(model):
            for idx, chunk in enumerate(chunks):
                pct = 5 + int((idx / total) * 85)
                yield _sse({"type": "progress",
                             "step": f"Abschnitt {idx + 1}/{total} wird optimiert…",
                             "pct": pct})
                opt = await _optimize_chunk_for_rag(chunk, model, tok=_tok)
                optimized_parts.append(opt)

        optimized_text = "\n\n".join(optimized_parts)
        yield _sse({"type": "progress", "step": "Einbetten und speichern…", "pct": 92})

        try:
            n = await ingest_file(coll, optimized_text, file.filename, f"doc_{uuid.uuid4().hex[:12]}")
        except Exception as e:
            yield _sse({"type": "error", "message": str(e)})
            return

        yield _sse({"type": "done", "filename": file.filename, "n_chunks": n, "tokens": _tok})

    return StreamingResponse(
        _stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.delete("/api/rag/documents/{did}")
async def rag_delete_document(did: str):
    await _db.rag_delete_document(did)
    return {"ok": True}


def _stitch_chunks(chunks: list) -> str:
    """Setzt überlappende Chunks wieder zu einem Fließtext zusammen, indem die
    Überlappung (gemeinsamer Übergang) zwischen aufeinanderfolgenden Chunks
    entfernt wird."""
    if not chunks:
        return ""
    out = chunks[0]
    for nxt in chunks[1:]:
        k = min(len(out), len(nxt), 1200)
        ov = 0
        for j in range(k, 20, -1):           # größte Überlappung ≥ 20 Zeichen
            if out[-j:] == nxt[:j]:
                ov = j
                break
        out += ("" if ov else "\n\n") + nxt[ov:]
    return out


@app.get("/api/rag/documents/{did}/export")
async def rag_export_document(did: str, format: str = "md"):
    """Exportiert den (aus den Chunks rekonstruierten) Inhalt eines Dokuments
    als Markdown oder TXT zum Download."""
    doc = await _db.rag_document_chunks(did)
    if not doc:
        raise HTTPException(status_code=404, detail="Dokument nicht gefunden")
    text = _stitch_chunks(doc["chunks"])
    ext = "md" if format == "md" else "txt"
    media = "text/markdown" if ext == "md" else "text/plain"
    base = re.sub(r"[^\w.\-]+", "_", Path(doc["filename"]).stem) or "dokument"
    headers = {"Content-Disposition": f'attachment; filename="{base}.{ext}"'}
    return Response(content=text, media_type=f"{media}; charset=utf-8", headers=headers)


@app.post("/api/rag/collections/{cid}/from-conversation")
async def rag_from_conversation(cid: str, req: Request):
    """Übernimmt ein gespeichertes Gespräch als Dokument in eine RAG-Sammlung
    (optional wird das Original-Gespräch danach gelöscht = „verschieben")."""
    from tools.rag import ingest_file
    body = await req.json()
    conv_id = body.get("conversation_id")
    delete_after = bool(body.get("delete_after"))
    coll = await _db.rag_get_collection(cid)
    if not coll:
        raise HTTPException(status_code=404, detail="Sammlung nicht gefunden")
    conv = await _db.get_conversation(conv_id)
    if not conv:
        raise HTTPException(status_code=404, detail="Gespräch nicht gefunden")
    lines = []
    for m in conv["messages"]:
        if m["role"] == "system":
            continue
        label = "Benutzer" if m["role"] == "user" else "Assistent"
        lines.append(f"{label}: {str(m.get('content', '')).strip()}")
    text = "\n\n".join(l for l in lines if l.strip())
    if not text:
        raise HTTPException(status_code=400, detail="Gespräch enthält keinen Text")
    title = conv.get("title") or conv_id
    try:
        n = await ingest_file(coll, text, f"Chat: {title}", f"doc_{uuid.uuid4().hex[:12]}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    if delete_after:
        await _db.delete_conversation(conv_id)
    return {"ok": True, "n_chunks": n, "deleted": delete_after}


@app.post("/api/rag/collections/{cid}/from-text")
async def rag_from_text(cid: str, req: Request):
    """Übernimmt beliebigen Text (z. B. Recherchebericht, Matrix-Ergebnis) als
    Dokument in eine Wissensdatenbank."""
    from tools.rag import ingest_file
    body = await req.json()
    text = str(body.get("text", "")).strip()
    title = (str(body.get("title", "")).strip() or "Notiz")[:120]
    coll = await _db.rag_get_collection(cid)
    if not coll:
        raise HTTPException(status_code=404, detail="Wissensdatenbank nicht gefunden")
    if not text:
        raise HTTPException(status_code=400, detail="Kein Text übergeben")
    try:
        n = await ingest_file(coll, text, title, f"doc_{uuid.uuid4().hex[:12]}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    return {"ok": True, "n_chunks": n}


@app.post("/api/matrix/export-md-zip")
async def matrix_export_md_zip(req: Request):
    """Packt übergebene Markdown-Dokumente (Matrix-Recherche: eine Zelle je Datei,
    benannt thema_prompt.md) in ein ZIP-Archiv zum Download. Dateinamen werden
    serverseitig auf einen sicheren Basisnamen reduziert (kein Pfad-Traversal)."""
    import io, zipfile, re as _re
    body = await req.json()
    files = body.get("files") or []
    if not isinstance(files, list) or not files:
        raise HTTPException(status_code=400, detail="Keine Dateien übergeben")
    zipname = _re.sub(r"[^\w\-]+", "_", str(body.get("zipname", "")).strip()) or "markdown"
    buf = io.BytesIO()
    seen: set[str] = set()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for i, f in enumerate(files):
            name = _re.sub(r"[\\/]+", "_", str((f or {}).get("name", "")).strip()).lstrip(".")
            content = str((f or {}).get("content", ""))
            if not name:
                name = f"doc_{i + 1}.md"
            if not name.lower().endswith(".md"):
                name += ".md"
            base = name
            n = 2
            while name in seen:
                name = f"{base[:-3]}_{n}.md"
                n += 1
            seen.add(name)
            zf.writestr(name, content)
    buf.seek(0)
    from fastapi.responses import StreamingResponse as SR
    return SR(
        buf,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{zipname}.zip"'},
    )


_MATRIX_GRAPH_SYSTEM = (
    "Du bist ein Analyst für Wissensgraphen. Du bekommst eine Liste von Knoten — jeder "
    "Knoten ist ein Thema bzw. eine Firma aus einer Recherche-Tabelle samt der dazu "
    "recherchierten Informationen. Finde inhaltlich belegbare, gerichtete Beziehungen "
    "ZWISCHEN diesen Knoten (z. B. „liefert an\", „Tochter von\", „Wettbewerber von\", "
    "„kooperiert mit\", „Kunde von\"). Nutze AUSSCHLIESSLICH die vorgegebenen Knoten-IDs "
    "in eckigen Klammern. Erfinde nichts, was nicht aus den Texten hervorgeht; im Zweifel "
    "lieber keine Kante. Halte die Beziehungsbezeichnung kurz (1–3 Wörter). "
    'Antworte NUR mit JSON: {"edges":[{"source":"<id>","target":"<id>","label":"Beziehung"}]}.'
)


@app.post("/api/matrix/graph")
async def matrix_graph(req: Request):
    """KI-Vorschlag für die Verknüpfungen eines Wissensgraphen über die Matrix-Zeilen.
    Knoten = Zeilen (Thema + recherchierte Zellinhalte). Liefert gerichtete Kanten
    zwischen den übergebenen Knoten-IDs; der Nutzer korrigiert sie danach im
    Graph-Editor (Hybrid: KI schlägt vor, Mensch entscheidet)."""
    body = await req.json()
    nodes = body.get("nodes") or []
    if not isinstance(nodes, list) or len(nodes) < 2:
        return {"edges": [], "tokens": {"in": 0, "out": 0}}
    model = _pick_model(body.get("model"), DEFAULT_MODEL)
    hint = str(body.get("hint", "")).strip()

    valid_ids = {str(n.get("id")) for n in nodes if n.get("id")}
    # Zeichenbudget je Knoten am Kontextfenster ausrichten (viele Knoten → knapper).
    per_node = max(400, int(_profile_num_ctx() * 3.5 * 0.6 / max(1, len(nodes))))
    lines = []
    for n in nodes:
        nid = str(n.get("id", "")).strip()
        if not nid:
            continue
        label = str(n.get("label", "")).strip()
        text = " ".join(str(n.get("text", "")).split())[:per_node]
        lines.append(f"[{nid}] {label}" + (f"\n{text}" if text else ""))
    usr = "Knoten:\n\n" + "\n\n".join(lines)
    if hint:
        usr += f"\n\nFokus/Hinweis für die Beziehungssuche: {hint}"

    edges, tin, tout = [], 0, 0
    try:
        async with _model_session(model), httpx.AsyncClient(timeout=180) as client:
            resp = await _llm.chat(client, {
                "model": model, "think": False, "stream": False, "format": "json",
                "messages": [{"role": "system", "content": _MATRIX_GRAPH_SYSTEM},
                             {"role": "user", "content": usr}],
                "options": {"num_ctx": _profile_num_ctx()}, "keep_alive": KEEP_ALIVE,
            })
            resp.raise_for_status()
        j = resp.json()
        tin, tout = _llm_tok(j)
        data = _parse_llm_json(j.get("message", {}).get("content", "")) or {}
        seen = set()
        for e in (data.get("edges") or []):
            s = str((e or {}).get("source", "")).strip()
            t = str((e or {}).get("target", "")).strip()
            lbl = str((e or {}).get("label", "")).strip()[:60]
            if s in valid_ids and t in valid_ids and s != t and (s, t) not in seen:
                seen.add((s, t))
                edges.append({"source": s, "target": t, "label": lbl})
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Graph-Analyse fehlgeschlagen: {e}")
    return {"edges": edges, "tokens": {"in": tin, "out": tout}}


_MATRIX_EXTRACT_SYSTEM = (
    "Du extrahierst aus den Recherche-Informationen zu EINEM Eintrag (Thema/Firma) "
    "typisierte Merkmale, die ihn charakterisieren. Du bekommst eine Liste von "
    "Kategorien. Ordne dem Eintrag pro Kategorie null, ein oder mehrere KONKRETE "
    "Werte zu, die im Text tatsächlich vorkommen (kurze Substantive/Eigennamen, "
    "max. 4 Wörter). Mehrere Werte derselben Kategorie als EINZELNE Einträge. "
    "Schreibe Werte einheitlich (z. B. Orte ohne Zusätze: „Berlin\", nicht "
    "„Sitz in Berlin\"). Erfinde nichts; gibt der Text zu einer Kategorie nichts "
    "her, lass sie weg. Nutze AUSSCHLIESSLICH die vorgegebenen Kategorienamen. "
    'Antworte NUR mit JSON: {"attributes":[{"category":"<Kategorie>","value":"<Wert>"}]}.'
)


@app.post("/api/matrix/extract")
async def matrix_extract(req: Request):
    """Extrahiert für EINEN Matrix-Eintrag (Thema + recherchierte Zellinhalte)
    typisierte Merkmale je vorgegebener Kategorie (z. B. Ort, Tool, Tätigkeit).
    Der Frontend baut daraus „Merkmal-Knoten" (Hubs): Zeilen, die denselben Wert
    teilen, hängen am selben Hub und sind so verbunden. Pro Zeile ein Aufruf."""
    body = await req.json()
    label = str(body.get("label", "")).strip()
    text = " ".join(str(body.get("text", "")).split())
    cats = [str(c).strip() for c in (body.get("categories") or []) if str(c).strip()]
    if not cats:
        cats = ["Tätigkeit", "Ort", "Tool", "Aufgabenbereich", "Name"]
    if not label and not text:
        return {"attributes": [], "tokens": {"in": 0, "out": 0}}
    model = _pick_model(body.get("model"), DEFAULT_MODEL)
    valid_cats = {c.lower(): c for c in cats}

    budget = max(800, int(_profile_num_ctx() * 3.5 * 0.7))
    usr = (
        f"Eintrag: {label}\n\nInformationen:\n{text[:budget]}\n\n"
        f"Kategorien: {', '.join(cats)}"
    )

    attrs, tin, tout = [], 0, 0
    try:
        async with _model_session(model), httpx.AsyncClient(timeout=180) as client:
            resp = await _llm.chat(client, {
                "model": model, "think": False, "stream": False, "format": "json",
                "messages": [{"role": "system", "content": _MATRIX_EXTRACT_SYSTEM},
                             {"role": "user", "content": usr}],
                "options": {"num_ctx": _profile_num_ctx()}, "keep_alive": KEEP_ALIVE,
            })
            resp.raise_for_status()
        j = resp.json()
        tin, tout = _llm_tok(j)
        data = _parse_llm_json(j.get("message", {}).get("content", "")) or {}
        seen = set()
        for a in (data.get("attributes") or []):
            cat = str((a or {}).get("category", "")).strip()
            val = str((a or {}).get("value", "")).strip()[:60]
            canon = valid_cats.get(cat.lower())
            if not canon or not val:
                continue
            key = (canon.lower(), val.lower())
            if key in seen:
                continue
            seen.add(key)
            attrs.append({"category": canon, "value": val})
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Merkmal-Analyse fehlgeschlagen: {e}")
    return {"attributes": attrs, "tokens": {"in": tin, "out": tout}}


# In den RAG geeignete Dateiendungen (Textextraktion via tools/files.py).
_RAG_FOLDER_EXTS = {
    ".pdf", ".docx", ".doc", ".xlsx", ".xls", ".csv", ".txt", ".md", ".rtf",
    ".py", ".js", ".json", ".yaml", ".yml", ".html", ".htm", ".css",
}


@app.post("/api/rag/collections/{cid}/folder")
async def rag_add_folder(cid: str, req: Request):
    """Baut aus allen Textdateien eines Server-seitigen Ordners eine Wissensdatenbank
    auf: Datei für Datei extrahieren → (in ingest_file) bereinigen, chunken, einbetten.
    Streamt SSE-Fortschritt. Hinweis: liest serverseitige Pfade — im Mehrbenutzer-/
    Server-Modus bewusst sparsam einsetzen."""
    coll = await _db.rag_get_collection(cid)
    if not coll:
        raise HTTPException(status_code=404, detail="Wissensdatenbank nicht gefunden")
    body = await req.json()
    raw_path = str(body.get("path", "")).strip()
    recursive = bool(body.get("recursive", True))
    if not raw_path:
        raise HTTPException(status_code=400, detail="Kein Ordnerpfad angegeben")
    folder = Path(raw_path).expanduser()
    if not folder.is_dir():
        raise HTTPException(status_code=400, detail=f"Ordner nicht gefunden: {folder}")

    it = folder.rglob("*") if recursive else folder.glob("*")
    files = sorted(p for p in it if p.is_file() and p.suffix.lower() in _RAG_FOLDER_EXTS)

    async def gen():
        from tools.rag import ingest_file
        total = len(files)
        yield _sse({"type": "folder_start", "total": total, "folder": str(folder)})
        n_chunks = 0
        n_ok = 0
        errors: list = []
        for i, fp in enumerate(files):
            yield _sse({"type": "progress", "step": fp.name, "index": i,
                        "total": total, "pct": int(i / total * 100) if total else 100})
            try:
                text = await asyncio.to_thread(_extract_text, fp)
                if not text or text.startswith("[Lesefehler") or text.startswith("[Kann Datei"):
                    errors.append(f"{fp.name}: kein Text")
                    continue
                c = await ingest_file(coll, text, fp.name, f"doc_{uuid.uuid4().hex[:12]}")
                n_chunks += c
                n_ok += 1
            except Exception as e:
                errors.append(f"{fp.name}: {e}")
        yield _sse({"type": "done", "n_files": n_ok, "n_chunks": n_chunks, "errors": errors[:20]})

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


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


# Plot-Absicht + Funktion(en) deterministisch aus dem Nutzertext ziehen, damit ein
# Funktionsgraph auch dann erscheint, wenn das kleine Modell plot_function NICHT von
# selbst aufruft. Wird als Fallback genutzt (nur wenn das Modell nicht schon geplottet hat).
_PLOT_INTENT = re.compile(
    r"(?i)\b(plotte?|plotten|zeichne|graph|graf|grafik|verlauf|kennlinie|skizziere|plot)\b")
_PLOT_RANGE = re.compile(
    r"(?i)(?:von|from)\s*(-?\d+(?:[.,]\d+)?)\s*(?:bis|to|–|—|\.\.+|-)\s*(-?\d+(?:[.,]\d+)?)")


def _extract_plot_request(text: str):
    """Gibt (expression, x_min, x_max) zurück, wenn der Text einen Funktionsplot
    verlangt, sonst None. expression kann mehrere Terme mit ``;`` enthalten."""
    if not text or not _PLOT_INTENT.search(text):
        return None
    x_min, x_max = -10.0, 10.0
    work = text
    m = _PLOT_RANGE.search(text)
    if m:
        try:
            x_min = float(m.group(1).replace(",", "."))
            x_max = float(m.group(2).replace(",", "."))
        except ValueError:
            pass
        work = text[:m.start()] + " ; " + text[m.end():]
    # Plot-Verben + typische Füllwörter entfernen, damit nur die Funktion übrig bleibt
    work = _PLOT_INTENT.sub(" ", work)
    work = re.sub(r"(?i)\b(den|der|die|das|von|vom|im|bereich|funktion|term|kurve|"
                  r"mir|bitte|einmal|mal|als|graphen?)\b", " ", work)
    # Verbindungen → Trenner, damit „x^2 und cos(x)" zwei Funktionen werden
    work = re.sub(r"(?i)\s+(und|and|sowie|,)\s+", " ; ", work)
    exprs = []
    # 1) explizite f(x)=… / y=… Definitionen
    for mm in re.finditer(r"(?:[A-Za-z]\w*\s*\([^)]*\)|y)\s*=\s*([^;]+)", work):
        exprs.append(mm.group(1))
    # 2) sonst: math-artige Tokens, die die Variable x enthalten
    if not exprs:
        for tok in re.split(r"[;]+", work):
            tok = tok.strip()
            if "x" in tok and re.search(r"(?i)[\^*/+]|x\d|\dx|sin|cos|tan|sqrt|exp|log|abs|x\^|x\b", tok):
                cand = re.search(r"[0-9A-Za-z_.^*/+()\-\s]*x[0-9A-Za-z_.^*/+()\-\s]*", tok)
                if cand:
                    exprs.append(cand.group(0))
    # säubern: Rand-Whitespace/Satzzeichen weg, muss die Variable x enthalten
    cleaned = []
    for e in exprs:
        e = e.strip(" .:;,").strip()
        if e and "x" in e and re.search(r"[0-9x)]\s*$", e):
            cleaned.append(e)
    if not cleaned:
        return None
    return ";".join(cleaned[:4]), x_min, x_max


async def _force_answer(messages: list, model: str, num_ctx: int) -> tuple:
    """Rettungsaufruf, wenn der Werkzeug-Loop endet, ohne sichtbaren Text zu liefern
    (Reasoning-Modell steckt alles ins »Denken«, oder Max-Iterationen bei tiefer
    Web-Recherche erreicht). Erzwingt EINE finale Antwort ohne Werkzeuge/Denken aus dem
    bereits gesammelten Kontext (inkl. der Tool-Ergebnisse). Gibt (text, tok_in, tok_out)."""
    msgs = list(messages) + [{
        "role": "user",
        "content": ("Beantworte jetzt die ursprüngliche Frage VOLLSTÄNDIG und direkt auf "
                    "Deutsch – nutze die bereits gesammelten Informationen/Suchergebnisse. "
                    "KEINE weiteren Werkzeugaufrufe, KEIN internes Nachdenken (<think>), "
                    "sondern unmittelbar die ausformulierte Antwort."),
    }]
    try:
        async with _model_session(model), httpx.AsyncClient(timeout=180) as client:
            resp = await _llm.chat(client, {
                "model": model, "think": False, "stream": False,
                "messages": msgs, "options": {"num_ctx": num_ctx}, "keep_alive": KEEP_ALIVE,
            })
            resp.raise_for_status()
        j = resp.json()
        ti, to = _llm_tok(j)
        txt = ((j.get("message", {}) or {}).get("content", "") or "")
        txt = re.sub(r"<think>.*?</think>", "", txt, flags=re.DOTALL)
        txt = re.sub(r"</?think>", "", txt).strip()
        return txt, ti, to
    except Exception:
        return "", 0, 0


async def _chat_generator(request: ChatRequest):
    system_prompt: Optional[str] = None
    active_tools = TOOL_DEFS
    model = request.model
    _agent_fixed_model = False   # Agent gibt explizit ein Modell vor
    code_capable = False   # Programmier-Agent → Code aus der Antwort in die IDE übernehmen
    _presenter_dedicated = False   # echter Präsentations-Agent (Canvas-Fallback erlaubt)
    _log_t0 = time.time()
    _tools_called: list = []
    _last_user = next((m.content for m in reversed(request.messages) if m.role == "user"), "")
    # Kontextfenster früh bestimmen, damit ALLE Aufrufe dieses Ablaufs (inkl. der
    # adaptiven Prompt-Ableitung) dasselbe num_ctx nutzen → kein Neuladen des Modells.
    _num_ctx = _profile_num_ctx()

    # Adaptiver Agent: erst die Frage analysieren, dann einen fragespezifischen
    # Experten-System-Prompt ableiten, der anschließend die Antwort erzeugt.
    _ad_tok = {"in": 0, "out": 0}   # Tokenverbrauch der adaptiven Ableitung (→ Gesamtzähler)
    if request.agent_id == "__adaptive__":
        role, derived = await _derive_adaptive_prompt(_last_user, model, _num_ctx, tok=_ad_tok)
        if derived:
            system_prompt = derived
            yield _sse({"type": "adaptive", "role": role})
    # Agenten-Konfiguration laden (sucht nach ID unabhängig vom Dateinamen)
    elif request.agent_id:
        agent_file = _agent_path_by_id(request.agent_id)
        if agent_file and agent_file.exists():
            agent = json.loads(agent_file.read_text(encoding="utf-8"))
            system_prompt = agent.get("system_prompt") or None
            if agent.get("model"):
                model = agent["model"]
                _agent_fixed_model = True
            # Fest an den Agenten gebundene Wissensdatenbank(en) automatisch aktivieren
            # (z. B. Gesetzes-/Regel-Agent mit hinterlegtem Normtext). Doppelte vermeiden.
            _agent_rag = agent.get("rag_collections") or []
            if _agent_rag:
                request.rag_collections = list(dict.fromkeys(list(request.rag_collections) + _agent_rag))
            allowed = set(agent.get("tools", list(ALL_TOOL_NAMES)))
            active_tools = [t for t in TOOL_DEFS if t["function"]["name"] in allowed]
            # Marker-„Tool" code_ide kennzeichnet den Programmier-Agenten (kein echtes
            # Ollama-Tool, daher nicht in active_tools — nur Fähigkeits-Flag).
            code_capable = "code_ide" in allowed
            # „Echter" Präsentations-Agent: nur für diesen (oder bei klarer Nutzer-
            # Absicht) darf der Canvas-Fallback aus Fließtext eine Präsentation bauen.
            # Verhindert, dass allgemeine Antwort-Modi (z. B. Felix/Sandra), die das
            # create_presentation-Tool nur „dabei" haben, zu schnell ins Canvas springen.
            _presenter_dedicated = (
                request.agent_id == "presenter"
                or agent.get("category") == "Präsentation"
            )

    # Websuche ist standardmäßig AUS und nur über den Schalter (oder Wissenschafts-
    # modus) verfügbar. Alle übrigen Werkzeuge (Plot, Rechner, Einheiten …) bleiben
    # davon unberührt – sonst „malt" das Modell mangels plot_function selbst Linien.
    if _hartman() or not (request.web_search or request.science):
        # Persona »Hartman« sperrt die Websuche komplett (alles rein lokal).
        active_tools = [t for t in active_tools
                        if t["function"]["name"] != "web_search"]

    # Ob plot_function dem Modell als Tool angeboten wird, entscheidet sich weiter unten
    # NACH der endgültigen Modellwahl (nur fähige externe Modelle bekommen es, siehe
    # Kommentar dort). Kleine lokale Modelle erzeugen dabei ungültige LaTeX-Escapes
    # (\( … \)), an denen Ollama mit HTTP 500 scheitert.

    # Rollen-Modell wählen, sofern der Agent keines fest vorgibt:
    #  • Programmier-Agent (code_ide) → Programmier-Modell
    #  • Wissenschaftsmodus → Wissenschafts-Modell (außer der Nutzer wählte gezielt
    #    ein anderes als das Allgemein-/Standardmodell)
    #  • sonst → angefordertes/Allgemein-Modell
    if not _agent_fixed_model:
        _req = _pick_model(request.model)
        if code_capable:
            model = _model_for("coding")
        elif request.science and _req in (DEFAULT_MODEL, _model_for("general")):
            model = _model_for("science")
        else:
            model = _req
        # Mathe-Weiche: Läuft nur das schwache Standardmodell und sieht die Nachricht
        # nach einer Matheaufgabe aus, an das (stärkere) Mathe-Modell (Rolle
        # „Programmieren / Mathe") weiterreichen. Greift nur, wenn dort tatsächlich ein
        # anderes Modell hinterlegt ist – sonst bliebe es ein wirkungsloser Umweg.
        if (model == DEFAULT_MODEL and _math_autoroute_enabled()
                and _looks_like_math(_last_user)):
            _math_model = _model_for("coding")
            if _math_model and _math_model != DEFAULT_MODEL:
                model = _math_model

    # Profil-Schalter „Recherche lokal": Wissenschafts-/Recherchekontext (Matrix-Zellen
    # laufen mit science=true) zwingend auf ein lokales Modell umbiegen, auch wenn die
    # Rolle ein externes API-Modell ist. Ist kein lokales LLM da → Fehlerframe.
    if request.science and _research_local_only() and _llm.is_remote(model):
        _loc = await _local_model(model)
        if not _loc:
            yield _sse({"type": "error", "message": "Kein lokales LLM verfügbar – „Web-Recherche lokal“ ist im Profil aktiv."})
            return
        model = _loc

    # plot_function nur FÄHIGEN externen Modellen (OpenRouter/OpenAI/… — Namensschema
    # „provider::modell") anbieten: sie erzeugen glatte Funktionsgraphen (400 Stützstellen)
    # und haben den LaTeX-Escape-Bug kleiner lokaler Modelle nicht. Für lokale Modelle
    # bleibt es ausgeblendet — dort greift der deterministische Fallback
    # (_extract_plot_request → plot_function nach der Antwort). So werden Funktionen nicht
    # mehr als grober Polygonzug über plot_chart „gemalt".
    _is_remote_model = "::" in (model or "")
    if not _is_remote_model:
        active_tools = [t for t in active_tools
                        if t["function"]["name"] != "plot_function"]

    # Erweiterte Chat-Werkzeuge (Profil-Häkchen): Code-Interpreter (run_python) + autonome
    # Web-Recherche, damit das Modell komplexe Aufgaben rechnend/recherchierend löst.
    # Standard aus — kleine Modelle sind mit dem Werkzeug-Loop oft überfordert.
    _agent_tools_on = _chat_agent_tools()
    if _agent_tools_on:
        _names = {t["function"]["name"] for t in active_tools}
        # Code-Interpreter nur, wenn serverseitige Python-Ausführung erlaubt ist
        if ALLOW_PYTHON_EXEC and "run_python" not in _names:
            active_tools = active_tools + [_RUN_PYTHON_TOOL_DEF]
        # Web-Recherche autonom anbieten (unabhängig vom 🔍-Schalter), sofern erlaubt
        if _web_search_allowed() and "web_search" not in _names:
            _web_def = next((t for t in TOOL_DEFS if t["function"]["name"] == "web_search"), None)
            if _web_def:
                active_tools = active_tools + [_web_def]

    # Assistent-Modus: Bild-Werkzeug freischalten, wenn ein Bildmodell konfiguriert ist,
    # damit das Modell auf Wunsch selbst ein Bild erzeugen kann.
    _assist_on = _assistant_mode()
    if _assist_on and _image_model() and not any(t["function"]["name"] == "generate_image" for t in active_tools):
        active_tools = active_tools + [_GENERATE_IMAGE_TOOL_DEF]

    # Nachrichten aufbauen – Modus-Brille (falls aktiv) dem System-Prompt voranstellen
    messages: list = []
    _sci = _SCIENCE_PROMPT if request.science else ""
    # Assistent-Modus: Intent-Router als Systemhinweis – das Modell entscheidet selbst,
    # welche Fähigkeit es nutzt.
    _router_hint = ""
    if _assist_on:
        _caps = []
        if any(t["function"]["name"] == "run_python" for t in active_tools):
            _caps.append("Rechnen/Code/Datenanalyse → run_python")
        if any(t["function"]["name"] == "web_search" for t in active_tools):
            _caps.append("aktuelle Fakten/Recherche → web_search")
        if any(t["function"]["name"] == "generate_image" for t in active_tools):
            _caps.append("Bild/Grafik/Motiv erzeugen → generate_image")
        if any(t["function"]["name"] == "create_diagram" for t in active_tools):
            _caps.append("Ablauf/Architektur/Beziehungen → create_diagram (Mermaid)")
        if any(t["function"]["name"] == "create_presentation" for t in active_tools):
            _caps.append("Folien/Präsentation → create_presentation")
        if any(t["function"]["name"] == "create_spreadsheet" for t in active_tools):
            _caps.append("Tabelle/Kalkulation → create_spreadsheet")
        if any(t["function"]["name"] == "route_planner" for t in active_tools):
            _caps.append("Route/Fahrtzeit → route_planner")
        _router_hint = ("ASSISTENT-MODUS: Du bist ein universeller Assistent und entscheidest "
                        "SELBST, welches Werkzeug eine Aufgabe am besten löst — rufe es dann "
                        "eigenständig auf, statt nur zu beschreiben. Verfügbare Fähigkeiten: "
                        + "; ".join(_caps) + ". Für einfache Wissens-/Gesprächsfragen antworte "
                        "direkt ohne Werkzeug.")
    _agent_hint = ""
    if _agent_tools_on and ALLOW_PYTHON_EXEC:
        _agent_hint = ("Du hast einen Code-Interpreter: Für rechen-/datenlastige oder komplexe "
                       "Aufgaben schreibe und führe Python über das Werkzeug run_python aus, "
                       "statt selbst zu rechnen; nutze das Ergebnis für deine Antwort.")

    # Ist die Websuche verfügbar (per 🔍-Schalter, Wissenschaftsmodus oder erweiterte
    # Werkzeuge), konkrete/überprüfbare Angaben AKTIV recherchieren statt aus dem
    # Gedächtnis zu raten (Detaildaten sind dort oft falsch).
    _web_hint = ""
    if any(t["function"]["name"] == "web_search" for t in active_tools):
        _web_hint = ("Für KONKRETE, überprüfbare Angaben (technische Daten wie Leistung/PS, "
                     "Baujahre, Maße, Preise, Eigennamen, aktuelle Fakten) nutze web_search und "
                     "stütze dich auf die gefundenen Quellen — verlasse dich NICHT auf dein "
                     "Gedächtnis, das bei solchen Detaildaten häufig falsch liegt.")

    # Mathematische/rechnerische Fragen VORZUGSWEISE per Code lösen (vermeidet Rechenfehler
    # kleiner Modelle): run_python, falls der Code-Interpreter aktiv ist, sonst das
    # immer verfügbare calculate-Werkzeug.
    _math_hint = ""
    if _looks_like_math(_last_user):
        _tnames = {t["function"]["name"] for t in active_tools}
        _mtool = "run_python" if "run_python" in _tnames else ("calculate" if "calculate" in _tnames else "")
        if _mtool:
            _math_hint = (
                f"Diese Frage ist mathematisch/rechnerisch: Löse sie VORZUGSWEISE mit dem "
                f"Code-Werkzeug »{_mtool}« — führe die Rechnung als Code aus und stütze deine "
                f"Antwort auf das berechnete Ergebnis, statt im Kopf zu rechnen. Nenne dem "
                f"Nutzer das Ergebnis klar und knapp erklärt."
            )
    _sys = "\n\n".join(p for p in (_sci, _augment_prefix(_last_user), system_prompt, _router_hint, _agent_hint, _web_hint, _math_hint) if p)
    if _sys:
        messages.append({"role": "system", "content": _sys})

    # RAG: relevante Passagen aus den gewählten Sammlungen vorab einblenden
    if request.rag_collections:
        try:
            from tools.rag import query_collections
            hits = await query_collections(request.rag_collections, _last_user)
        except Exception as e:
            hits = []
            yield _sse({"type": "error", "message": f"RAG-Suche fehlgeschlagen: {e}"})
        if hits:
            ctx = "\n\n".join(
                f"[Quelle {i + 1}: {h['filename']}]\n{h['text']}" for i, h in enumerate(hits)
            )
            # Strengste Vorgabe unter den gewählten Sammlungen anwenden (Regler „kreativ↔korrekt")
            _rank = {"kreativ": 0, "ausgewogen": 1, "korrekt": 2}
            _strict = "ausgewogen"
            for _cid in request.rag_collections:
                _c = await _db.rag_get_collection(_cid)
                if _c and _rank.get(_c.get("strictness", "ausgewogen"), 1) > _rank.get(_strict, 1):
                    _strict = _c["strictness"]
            _rag_instr = {
                "korrekt": (
                    "Beantworte die Frage AUSSCHLIESSLICH anhand der folgenden Auszüge aus den "
                    "Wissensdatenbanken des Nutzers und nenne die Quelle (Dateiname). Steht die "
                    "Antwort nicht in den Auszügen, sage das klar und rate nicht."),
                "ausgewogen": (
                    "Beantworte die Frage vorrangig anhand der folgenden Auszüge aus den "
                    "Wissensdatenbanken des Nutzers und nenne die Quelle (Dateiname); ergänze nur "
                    "bei Bedarf mit gesichertem Wissen."),
                "kreativ": (
                    "Nutze die folgenden Auszüge aus den Wissensdatenbanken des Nutzers als "
                    "Grundlage und ergänze sie bei Bedarf mit eigenem Wissen. Nenne die Quelle "
                    "(Dateiname), wenn du dich darauf stützt."),
            }[_strict]
            messages.append({"role": "system", "content": _rag_instr + "\n\n" + ctx})
            yield _sse({"type": "rag", "sources": [
                {"filename": h["filename"], "collection": h["collection_name"], "score": h["score"]}
                for h in hits
            ]})

    for msg in request.messages:
        content = msg.content
        images: list = []

        if msg.files:
            for fid in msg.files:
                fp = UPLOADS_DIR / fid
                if not fp.exists():
                    continue
                if _is_image(fp):
                    images.append(base64.b64encode(fp.read_bytes()).decode())
                else:
                    extracted = _extract_text(fp)
                    content += f"\n\n[Datei: {fp.name}]\n{extracted}"

        entry: dict = {"role": msg.role, "content": content}
        if images:
            entry["images"] = images
        messages.append(entry)

    # Ist der aktive Agent präsentationsfähig? (für den Canvas-Fallback)
    presentation_capable = any(
        t.get("function", {}).get("name") == "create_presentation" for t in active_tools
    )
    canvas_emitted = False   # über alle Loop-Iterationen: wurde schon ein Canvas gesendet?
    image_emitted = False    # wurde schon ein Funktionsgraph/Diagramm-Bild gesendet?

    # Niedrige Temperatur reduziert Halluzinationen kleiner Modelle deutlich und
    # macht das Tool-Calling zuverlässiger. Für Wissenschaft/Recherche noch strenger.
    _temp = 0.1 if request.science else 0.3
    # _num_ctx wurde bereits oben (vor der adaptiven Ableitung) bestimmt.
    # Denkprozess anfordern? Wird abgeschaltet, falls das Modell 'think' nicht unterstützt.
    _think_on = bool(request.show_thinking)
    _tok_in = _ad_tok["in"]    # summierte Prompt-Tokens über alle Loop-Iterationen
    _tok_out = _ad_tok["out"]  # summierte Antwort-Tokens (inkl. adaptiver Ableitung)
    # Agentic Loop
    for _iter in range(8):
        payload = {
            "model": model,
            "messages": messages,
            "stream": False,
            "options": {"temperature": _temp, "num_ctx": _num_ctx},
            "keep_alive": KEEP_ALIVE,
            "tools": active_tools if request.use_tools else [],
        }
        if _think_on:
            payload["think"] = True

        try:
            async with _model_session(model), httpx.AsyncClient(timeout=180) as client:
                resp = await _llm.chat(client,payload)
                # Modelle ohne Reasoning lehnen 'think' mit 400 ab → ohne erneut versuchen
                if resp.status_code == 400 and _think_on:
                    _think_on = False
                    payload.pop("think", None)
                    resp = await _llm.chat(client,payload)
                resp.raise_for_status()
                result = resp.json()
                _tok_in += int(result.get("prompt_eval_count") or 0)
                _tok_out += int(result.get("eval_count") or 0)
        except Exception as e:
            # Bekannte Ollama-Fragilität: kleine Modelle erzeugen beim Tool-Calling
            # gelegentlich ungültige Escapes (\( … ) → HTTP 500. Wollte der Nutzer einen
            # Funktionsplot, liefern wir ihn deterministisch, statt nur einen Fehler zu zeigen.
            if not image_emitted:
                _pr = _extract_plot_request(_last_user)
                if _pr:
                    try:
                        from tools.engineering import plot_function
                        _pe, _pmn, _pmx = _pr
                        _ppi = json.loads(plot_function(_pe, x_min=_pmn, x_max=_pmx))
                        if _ppi.get("type") == "image":
                            yield _sse({"type": "image", "data": _ppi["data"]})
                            yield _sse({"type": "text", "content": "Hier ist der Graph der Funktion."})
                            yield _sse({"type": "done"})
                            return
                    except Exception:
                        pass
            yield _sse({"type": "error", "message": str(e)})
            return

        msg_obj = result.get("message", {})
        tool_calls = msg_obj.get("tool_calls") or []

        # Auch <call_tool> Inline-Format parsen (manche Modelle nutzen dies)
        content_raw = msg_obj.get("content", "")
        if not tool_calls:
            inline_calls = _extract_inline_tool_calls(content_raw)
            if inline_calls:
                tool_calls = inline_calls
                content_raw = _strip_inline_tool_calls(content_raw)
                msg_obj["content"] = content_raw

        # Denkprozess einsammeln: aus dem nativen 'thinking'-Feld (Reasoning-Modelle)
        # UND aus inline <think>…</think>-Tags. In jedem Fall aus dem sichtbaren Text
        # entfernen, damit er nie in die Antwort leckt.
        _think_parts = []
        _native_think = (msg_obj.get("thinking") or "").strip()
        if _native_think:
            _think_parts.append(_native_think)
        if content_raw and "<think" in content_raw.lower():
            _think_parts += [m.strip() for m in
                             re.findall(r"<think>(.*?)</think>", content_raw, flags=re.DOTALL)]
            content_raw = re.sub(r"<think>.*?</think>", "", content_raw, flags=re.DOTALL)
            # unvollständig (kein schließendes Tag): Rest ab <think> als Denken werten
            _unclosed = re.search(r"<think>(.*)$", content_raw, flags=re.DOTALL)
            if _unclosed:
                _think_parts.append(_unclosed.group(1).strip())
                content_raw = content_raw[:_unclosed.start()]
            content_raw = re.sub(r"</?think>", "", content_raw).strip()
            msg_obj["content"] = content_raw
        _think_text = "\n\n".join(p for p in _think_parts if p).strip()
        if request.show_thinking and _think_text:
            yield _sse({"type": "thinking", "content": _think_text})

        # Diagnose: Roh-Antwort des Modells protokollieren (hilft bei „keine Antwort")
        _write_log({
            "type": "llm_response", "model": model, "iter": _iter,
            "content_len": len(content_raw or ""),
            "tool_calls": [tc.get("function", {}).get("name") for tc in tool_calls],
            "done_reason": result.get("done_reason"),
        })

        if not tool_calls:
            content = content_raw

            # Leere sichtbare Antwort (häufig: Reasoning-Modell steckt alles ins »Denken«,
            # oder kleines Modell liefert nur Tool-Calls). Vor der Fehlermeldung EINEN
            # Rettungsaufruf ohne Werkzeuge/Denken versuchen → aus dem gesammelten Kontext
            # (inkl. Web-Ergebnissen) doch noch eine Antwort formulieren.
            if not (content or "").strip():
                _fa, _fti, _fto = await _force_answer(messages, model, _num_ctx)
                _tok_in += _fti
                _tok_out += _fto
                if _fa.strip():
                    content = _fa   # weiter unten normal streamen/speichern
                else:
                    _hinweis = (
                        f"Das Modell '{model}' hat eine leere Antwort geliefert"
                        + (" (nach Tool-Aufrufen)." if _tools_called else ".")
                        + " Bitte erneut senden oder ein anderes Modell wählen."
                    )
                    yield _sse({"type": "error", "message": _hinweis})
                    _write_log({"type": "empty_response", "model": model,
                                "tools_called": _tools_called})
                    yield _sse({"type": "done"})
                    return

            # Canvas-Daten extrahieren falls vorhanden
            canvas_data = _extract_canvas_json(content)
            if canvas_data:
                yield _sse({"type": "canvas", "data": canvas_data})
                canvas_emitted = True
                content = _strip_canvas_json(content)

            # Text wortweise streamen
            words = content.split(" ")
            for i, word in enumerate(words):
                yield _sse({"type": "text", "content": word + (" " if i < len(words) - 1 else "")})
                await asyncio.sleep(0.004)

            # Fallback: präsentationsfähiger Agent lieferte nur Fließtext (kein Tool-Aufruf)
            # → Text per zweitem Aufruf in Folien umwandeln, damit dennoch eine
            #   Canvas-Präsentation entsteht. NUR wenn der Nutzer eine Präsentation
            #   wollte ODER es der dedizierte Präsentations-Agent ist — damit allgemeine
            #   Antwort-Modi nicht bei jedem „Agenda"/„Gliederung" ins Canvas springen.
            _wants_pres = bool(re.search(
                r"(?i)präsentation|präsentier|foliensatz|folien|\bslides?\b|slide-?deck|"
                r"powerpoint|pptx|vortrag",
                _last_user))
            if (not canvas_emitted and presentation_capable and len(content) > 300
                    and (_wants_pres or _presenter_dedicated)
                    and re.search(r"(?i)folie|slide|präsentation|agenda|gliederung|inhaltsverzeichnis", content)):
                _pf_tok = {"in": 0, "out": 0}
                conv = await _text_to_presentation(content, model, tok=_pf_tok)
                _tok_in += _pf_tok["in"]
                _tok_out += _pf_tok["out"]
                if conv:
                    canvas_data = conv
                    yield _sse({"type": "canvas", "data": canvas_data})
                    canvas_emitted = True

            # Deterministischer Plot-Fallback: hat das Modell trotz Plot-Wunsch nicht
            # selbst geplottet, ziehen wir die Funktion aus dem Nutzertext und zeichnen
            # sie serverseitig (kleine Modelle rufen plot_function oft nicht zuverlässig auf).
            if not image_emitted:
                _plot_req = _extract_plot_request(_last_user)
                if _plot_req:
                    try:
                        from tools.engineering import plot_function
                        _expr, _xmin, _xmax = _plot_req
                        _pres = plot_function(_expr, x_min=_xmin, x_max=_xmax)
                        _pimg = json.loads(_pres)
                        if _pimg.get("type") == "image":
                            yield _sse({"type": "image", "data": _pimg["data"]})
                            image_emitted = True
                    except Exception:
                        pass

            # Programmier-Agent: Code aus der Antwort als Basis in die Code-IDE übernehmen
            if code_capable:
                code_block = _extract_code_block(content)
                if code_block:
                    _cname = re.sub(r"\s+", " ", _last_user).strip()[:40] or "Chat-Programm"
                    yield _sse({"type": "code", "code": code_block, "name": _cname})

            # Konversation in DB speichern (inkl. Canvas-JSON)
            if request.conversation_id:
                messages.append({"role": "assistant", "content": content})
                await _db.save_conversation(
                    request.conversation_id,
                    messages,
                    model=model,
                    agent_id=request.agent_id,
                    canvas_json=json.dumps(canvas_data, ensure_ascii=False) if canvas_data else None,
                )

            _write_log({
                "type": "chat", "model": model,
                "msg_count": len(request.messages),
                "resp_len": len(content),
                "tools_called": _tools_called,
                "ms": int((time.time() - _log_t0) * 1000),
                "tok_in": _tok_in, "tok_out": _tok_out,
            })
            yield _sse({"type": "done", "tokens": {"in": _tok_in, "out": _tok_out}})
            return

        # Tool-Calls ausführen
        messages.append({
            "role": "assistant",
            "content": msg_obj.get("content", ""),
            "tool_calls": tool_calls,
        })

        for tc in tool_calls:
            fn = tc["function"]["name"]
            args = tc["function"]["arguments"]
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except Exception:
                    args = {}

            yield _sse({"type": "tool_start", "tool": fn, "args": args})
            _tool_t0 = time.time()
            tool_result = await _execute_tool(fn, args)
            _tool_ms = int((time.time() - _tool_t0) * 1000)
            _tools_called.append(fn)
            _write_log({"type": "tool", "name": fn, "ms": _tool_ms, "result_len": len(tool_result)})
            yield _sse({"type": "tool_done", "tool": fn, "preview": tool_result[:300]})

            # Canvas sofort streamen wenn es ein Präsentations-/Tabellen-Tool ist
            if fn in ("create_presentation", "create_spreadsheet"):
                try:
                    canvas_data = json.loads(tool_result)
                    yield _sse({"type": "canvas", "data": canvas_data})
                    canvas_emitted = True
                    # Canvas in DB speichern (auch wenn das Modell danach noch Text schreibt)
                    if request.conversation_id:
                        await _db.update_canvas(
                            request.conversation_id,
                            json.dumps(canvas_data, ensure_ascii=False),
                        )
                except Exception:
                    pass

            # Diagramm-Bild sofort streamen
            if fn in ("plot_chart", "plot_function"):
                try:
                    img_data = json.loads(tool_result)
                    if img_data.get("type") == "image":
                        yield _sse({"type": "image", "data": img_data["data"]})
                        image_emitted = True
                        tool_result = "Diagramm wurde erstellt und wird angezeigt."
                except Exception:
                    pass

            # Mermaid-Diagramm sofort streamen
            if fn == "create_diagram":
                try:
                    diag = json.loads(tool_result)
                    yield _sse({"type": "diagram", "data": diag})
                    tool_result = (
                        f"Diagramm '{diag.get('title', diag.get('diagram_type', ''))}' "
                        f"wird dem Nutzer bereits angezeigt. "
                        f"Beschreibe es kurz in 1–2 Sätzen."
                    )
                except Exception:
                    pass

            # Route sofort als interaktive Karte streamen
            if fn == "route_planner":
                try:
                    map_data = json.loads(tool_result)
                    if map_data.get("type") == "map":
                        yield _sse({"type": "map", "data": map_data})
                        # Modell erhält nur die Kennzahlen, nicht die ganze Geometrie
                        tool_result = (
                            f"Route von {map_data['start']['name']} nach "
                            f"{map_data['end']['name']} wird dem Nutzer bereits als "
                            f"interaktive Karte angezeigt. "
                            f"Strecke: {map_data['distance_km']} km, "
                            f"Fahrzeit: {map_data['duration_text']} "
                            f"(Profil: {map_data['profile']}). "
                            f"Fasse dem Nutzer NUR diese Eckdaten knapp zusammen und "
                            f"verweise auf die Karte. Erfinde KEINE Wegbeschreibung, "
                            f"keine Straßennamen, Ausfahrten, Brücken, Normen, "
                            f"Tempolimits oder technischen Analysen – diese Angaben "
                            f"liegen dir nicht vor."
                        )
                except Exception:
                    pass

            # Code-Interpreter: erzeugte Diagramme sofort anzeigen, dem Modell nur den Text geben
            if fn == "run_python":
                try:
                    _pyd = json.loads(tool_result)
                    for _img in (_pyd.get("images") or []):
                        yield _sse({"type": "image", "data": _img})
                        image_emitted = True
                    tool_result = _pyd.get("text", "") or "(keine Ausgabe)"
                    if _pyd.get("images"):
                        tool_result += "\n(Das/die Diagramm(e) werden dem Nutzer bereits angezeigt.)"
                except Exception:
                    pass

            # Assistent-Modus: erzeugtes Bild sofort anzeigen, dem Modell nur eine Notiz geben
            if fn == "generate_image":
                try:
                    _imd = json.loads(tool_result)
                    if _imd.get("ok") and _imd.get("image"):
                        yield _sse({"type": "image", "data": _imd["image"]})
                        image_emitted = True
                        tool_result = ("Das Bild wurde erzeugt und wird dem Nutzer bereits "
                                       "angezeigt. Beschreibe es kurz in 1–2 Sätzen.")
                    else:
                        tool_result = "Bildgenerierung fehlgeschlagen: " + str(_imd.get("error", "unbekannt"))
                except Exception:
                    pass

            messages.append({"role": "tool", "content": tool_result})

    # Werkzeug-Loop erschöpft (z. B. tiefe Web-Recherche mit vielen Suchschritten): statt
    # aufzugeben eine finale Antwort ohne Werkzeuge aus dem gesammelten Kontext erzwingen.
    _fa, _fti, _fto = await _force_answer(messages, model, _num_ctx)
    _tok_in += _fti
    _tok_out += _fto
    if _fa.strip():
        _words = _fa.split(" ")
        for _i, _w in enumerate(_words):
            yield _sse({"type": "text", "content": _w + (" " if _i < len(_words) - 1 else "")})
            await asyncio.sleep(0.004)
        if request.conversation_id:
            messages.append({"role": "assistant", "content": _fa})
            await _db.save_conversation(request.conversation_id, messages,
                                        model=model, agent_id=request.agent_id)
        yield _sse({"type": "done", "tokens": {"in": _tok_in, "out": _tok_out}})
        return

    yield _sse({"type": "error", "message":
                "Die Recherche brauchte zu viele Schritte und lieferte keine finale Antwort. "
                "Bitte die Frage etwas eingrenzen oder ein stärkeres Modell wählen."})
    yield _sse({"type": "done", "tokens": {"in": _tok_in, "out": _tok_out}})


def _sse(data: dict) -> str:
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


# ── Tool-Ausführung ───────────────────────────────────────────────────────────


async def _execute_tool(name: str, args: dict) -> str:
    if name == "web_search":
        from tools.search import search
        return await search(args.get("query", ""), int(args.get("num_results", 6)))

    if name == "calculate":
        return _safe_exec(args.get("code", ""))

    if name == "run_python":
        # Code-Interpreter (Chat, per Profil-Häkchen): dieselbe Sandbox wie der Code-Tab.
        if not ALLOW_PYTHON_EXEC:
            return "Python-Ausführung ist in dieser Installation deaktiviert."
        out = await asyncio.to_thread(_run_python_code, str(args.get("code", "") or ""), 15.0)
        txt = ""
        if out.get("output"):
            txt += "STDOUT:\n" + out["output"]
        if out.get("error"):
            txt += "\nSTDERR:\n" + out["error"]
        # JSON-Umschlag: der Loop trennt Bilder (→ anzeigen) vom Text (→ ans Modell)
        return json.dumps({"text": (txt.strip() or "(keine Ausgabe)")[:6000],
                           "images": out.get("images") or []}, ensure_ascii=False)

    if name == "generate_image":
        # Bild-Werkzeug (Assistent-Modus): erzeugt ein Bild; der Loop streamt es als image-Frame.
        try:
            r = await _generate_image_core(str(args.get("prompt", "") or ""), "",
                                           str(args.get("size", "square") or "square"))
            return json.dumps({"ok": True, "image": r.get("image", ""),
                               "prompt": r.get("prompt", "")}, ensure_ascii=False)
        except HTTPException as e:
            return json.dumps({"ok": False, "error": str(e.detail)}, ensure_ascii=False)
        except Exception as e:
            return json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False)

    if name in ("create_presentation", "create_spreadsheet"):
        canvas_type = name.replace("create_", "")
        data = {"type": canvas_type, **args}
        if canvas_type == "presentation":
            data = _normalize_presentation(data)
        return json.dumps(data, ensure_ascii=False)

    if name == "unit_convert":
        from tools.engineering import unit_convert
        return unit_convert(
            float(args.get("value", 0)),
            str(args.get("from_unit", "")),
            str(args.get("to_unit", "")),
        )

    if name == "solve_equation":
        from tools.engineering import solve_equation
        return solve_equation(
            str(args.get("expression", "")),
            str(args.get("variable", "x")),
        )

    if name == "plot_chart":
        from tools.engineering import plot_chart
        return plot_chart(
            x_data=args.get("x_data", []),
            y_data=args.get("y_data", []),
            title=args.get("title", ""),
            x_label=args.get("x_label", ""),
            y_label=args.get("y_label", ""),
            chart_type=args.get("chart_type", "line"),
            series_label=args.get("series_label", ""),
            y2_data=args.get("y2_data"),
            y2_label=args.get("y2_label", ""),
        )

    if name == "plot_function":
        from tools.engineering import plot_function
        return plot_function(
            expression=str(args.get("expression", "")),
            var=str(args.get("var", "x") or "x"),
            x_min=float(args.get("x_min", -10)),
            x_max=float(args.get("x_max", 10)),
            title=str(args.get("title", "")),
            x_label=str(args.get("x_label", "")),
            y_label=str(args.get("y_label", "")),
        )

    if name == "material_lookup":
        from tools.materials import material_lookup
        return material_lookup(
            str(args.get("name", "")),
            str(args.get("prop", "")),
        )

    if name == "bolt_calculator":
        from tools.engineering import bolt_calculator
        return bolt_calculator(
            d_nom=float(args.get("d_nom", 0)),
            pitch=float(args.get("pitch", 0)),
            f_axial=float(args.get("f_axial", 0)),
            mu=float(args.get("mu", 0.15)),
            material_class=str(args.get("material_class", "8.8")),
            f_transverse=float(args.get("f_transverse", 0)),
        )

    if name == "generate_report":
        from tools.report import generate_report
        return generate_report(
            title=str(args.get("title", "Bericht")),
            author=str(args.get("author", "")),
            sections=args.get("sections", []),
        )

    if name == "route_planner":
        from tools.routing import plan_route
        return await plan_route(
            origin=str(args.get("origin", "")),
            destination=str(args.get("destination", "")),
            profile=str(args.get("profile", "driving")),
        )

    if name == "create_diagram":
        return json.dumps({
            "type": "diagram",
            "diagram_type": str(args.get("diagram_type", "flowchart")),
            "definition": str(args.get("definition", "")),
            "title": str(args.get("title", "")),
        }, ensure_ascii=False)

    return f"Unbekanntes Tool: {name}"


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


# ── Hilfsfunktionen ───────────────────────────────────────────────────────────


def _is_image(fp: Path) -> bool:
    return fp.suffix.lower() in {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"}


def _extract_text(fp: Path) -> str:
    try:
        from tools.files import extract
        return extract(fp)
    except Exception as e:
        return f"[Lesefehler: {e}]"


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


# ── Konversations-API ─────────────────────────────────────────────────────────


@app.get("/api/conversations")
async def list_conversations(project_id: Optional[str] = None):
    return await _db.list_conversations(project_id=project_id)


@app.get("/api/conversations/{cid}")
async def get_conversation(cid: str):
    data = await _db.get_conversation(cid)
    if not data:
        raise HTTPException(404, "Nicht gefunden")
    return data


@app.delete("/api/conversations/{cid}")
async def delete_conversation(cid: str):
    await _db.delete_conversation(cid)
    return {"ok": True}


@app.post("/api/conversations/{cid}/compress")
async def compress_conversation(cid: str):
    import re
    data = await _db.get_conversation(cid)
    if not data:
        raise HTTPException(404, "Nicht gefunden")

    msgs = data["messages"]
    model = data.get("model") or DEFAULT_MODEL

    full_text = ""
    for m in msgs:
        if m["role"] == "system":
            continue
        label = "Benutzer" if m["role"] == "user" else "Assistent"
        full_text += f"{label}: {str(m.get('content', ''))[:600]}\n\n"

    prompt = (
        "Fasse das folgende Gespräch zu einer kompakten Zusammenfassung zusammen. "
        "Die Zusammenfassung soll alle wichtigen Informationen, Erkenntnisse, Ergebnisse und "
        "offenen Punkte enthalten, sodass das Gespräch nahtlos fortgesetzt werden kann. "
        "Antworte NUR mit der Zusammenfassung, keine Einleitung.\n\n"
        f"--- GESPRÄCH ---\n{full_text[:10000]}"
    )

    async with _model_session(model), httpx.AsyncClient(timeout=120) as client:
        resp = await _llm.chat(client,{
            "model": model,
            "think": False,
            "messages": [{"role": "user", "content": prompt}],
            "stream": False,
        })
        resp.raise_for_status()
        result = resp.json()
        _cc_ti, _cc_to = _llm_tok(result)

    summary = result.get("message", {}).get("content", "").strip()
    summary = re.sub(r"<think>.*?</think>", "", summary, flags=re.DOTALL).strip()

    # System-Nachricht mit Zusammenfassung + letzte 2 Austausche behalten
    compressed: list = [
        {"role": "system", "content": f"[ZUSAMMENFASSUNG DES BISHERIGEN GESPRÄCHS]\n\n{summary}"}
    ]
    exchanges = []
    i = 0
    while i < len(msgs):
        if msgs[i]["role"] == "user" and i + 1 < len(msgs) and msgs[i + 1]["role"] == "assistant":
            exchanges.append((msgs[i], msgs[i + 1]))
            i += 2
        else:
            i += 1
    for u, a in exchanges[-2:]:
        compressed.append(u)
        compressed.append(a)

    await _db.save_conversation(cid, compressed, model=model, agent_id=data.get("agent_id"))
    return {"ok": True, "summary": summary, "messages": compressed,
            "tokens": {"in": _cc_ti, "out": _cc_to}}


@app.post("/api/conversations/{cid}/to-skill")
async def conversation_to_skill(cid: str):
    import re
    data = await _db.get_conversation(cid)
    if not data:
        raise HTTPException(404, "Nicht gefunden")

    msgs = data["messages"]
    model = data.get("model") or DEFAULT_MODEL

    full_text = ""
    for m in msgs:
        if m["role"] == "system":
            continue
        label = "Benutzer" if m["role"] == "user" else "Assistent"
        full_text += f"{label}: {str(m.get('content', ''))[:600]}\n\n"

    prompt = (
        "Analysiere das folgende Gespräch und erstelle daraus einen spezialisierten KI-Agenten.\n\n"
        "Antworte NUR mit einem JSON-Objekt, ohne Markdown-Blöcke, ohne Erklärung:\n"
        "{\n"
        '  "name": "Kurzname des Agenten (max 30 Zeichen)",\n'
        '  "icon": "Ein passendes Emoji",\n'
        '  "description": "Was der Agent kann (max 100 Zeichen)",\n'
        '  "category": "Genau eine aus: Fertigung, Qualität, Dokumentation, Kommunikation, Analyse, Recherche, Technik, Sonstige",\n'
        '  "system_prompt": "Detaillierter System-Prompt der das Wissen aus dem Gespräch einbettet. Beginnt mit Du bist ein... Enthält wichtige Erkenntnisse, Methoden und Fachkontext. Maximal 300 Wörter. Auf Deutsch.",\n'
        '  "tools": ["Sinnvolle Tools aus: web_search, calculate, create_presentation, create_spreadsheet"]\n'
        "}\n\n"
        f"--- GESPRÄCH ---\n{full_text[:10000]}"
    )

    async with _model_session(model), httpx.AsyncClient(timeout=120) as client:
        resp = await _llm.chat(client,{
            "model": model,
            "think": False,
            "messages": [{"role": "user", "content": prompt}],
            "stream": False,
        })
        resp.raise_for_status()
        result = resp.json()
        _ts_ti, _ts_to = _llm_tok(result)

    content = result.get("message", {}).get("content", "").strip()
    content = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL).strip()
    content = re.sub(r"```(?:json)?", "", content).replace("```", "").strip()

    try:
        m = re.search(r'\{.*\}', content, re.DOTALL)
        skill_data = json.loads(m.group() if m else content)
    except Exception:
        skill_data = {
            "name": data["title"][:30],
            "icon": "🧠",
            "description": "Aus Chat generierter Agent",
            "category": "Sonstige",
            "system_prompt": f"Du bist ein spezialisierter Assistent basierend auf folgendem Gespräch.\n\n{full_text[:500]}",
            "tools": [],
        }

    skill_data["tokens"] = {"in": _ts_ti, "out": _ts_to}
    return skill_data


@app.patch("/api/conversations/{cid}/rename")
async def rename_conversation(cid: str, req: Request):
    body = await req.json()
    new_title = str(body.get("title", "")).strip()
    if not new_title:
        raise HTTPException(400, "Kein Titel angegeben")
    await _db.rename_conversation(cid, new_title)
    return {"ok": True, "title": new_title}


@app.get("/api/conversations/{cid}/export")
async def export_conversation(cid: str):
    data = await _db.get_conversation(cid)
    if not data:
        raise HTTPException(404, "Nicht gefunden")
    slug = _to_slug(data.get("title", cid))
    filename = f"{slug}.json"
    content = json.dumps(data, ensure_ascii=False, indent=2)
    from fastapi.responses import Response
    return Response(
        content=content.encode("utf-8"),
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.post("/api/conversations/import")
async def import_conversation(req: Request):
    body = await req.json()
    conv_id = body.get("id") or f"conv_{int(time.time() * 1000)}"
    # Neue ID vergeben um Kollisionen zu vermeiden
    conv_id = f"import_{uuid.uuid4().hex[:10]}"
    messages = body.get("messages", [])
    model = body.get("model")
    agent_id = body.get("agent_id")
    canvas_json = body.get("canvas_json")
    await _db.save_conversation(conv_id, messages, model=model, agent_id=agent_id, canvas_json=canvas_json)
    # Projekt-ID setzen falls vorhanden
    if body.get("project_id"):
        await _db.set_project(conv_id, body["project_id"])
    return {"ok": True, "id": conv_id}


@app.post("/api/conversations/export-all")
async def export_all_conversations():
    """Exportiert alle Gespräche als ZIP-Archiv."""
    import io, zipfile
    convs = await _db.list_conversations(limit=9999)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for c in convs:
            data = await _db.get_conversation(c["id"])
            if data:
                slug = _to_slug(data.get("title", c["id"]))
                filename = f"{slug}_{c['id'][:8]}.json"
                zf.writestr(filename, json.dumps(data, ensure_ascii=False, indent=2))
    buf.seek(0)
    from fastapi.responses import StreamingResponse as SR
    return SR(
        buf,
        media_type="application/zip",
        headers={"Content-Disposition": 'attachment; filename="ai_framework_thomas_gespraeche.zip"'},
    )


@app.get("/api/search")
async def search_conversations(q: str = Query(..., min_length=2)):
    return await _db.search(q)


# ── Upload-API ────────────────────────────────────────────────────────────────


@app.post("/api/upload")
async def upload_file(file: UploadFile = File(...)):
    fid = f"{uuid.uuid4().hex[:8]}_{file.filename}"
    fp = UPLOADS_DIR / fid
    content = await file.read()
    fp.write_bytes(content)
    return {
        "id": fid,
        "filename": file.filename,
        "type": file.content_type,
        "is_image": bool(file.content_type and file.content_type.startswith("image/")),
        "size": len(content),
    }


@app.get("/api/uploads/{fid}")
async def get_upload(fid: str):
    fp = UPLOADS_DIR / fid
    if not fp.exists():
        raise HTTPException(404)
    return FileResponse(fp)


# ── Transkription (Spracherkennung, Audio → Text) ─────────────────────────────
# Quelle: Mikrofon (inkl. USB) oder Datei. Engine wahlweise LOKAL (faster-whisper,
# CPU-schonend) oder API (OpenAI/Groq-kompatibles /audio/transcriptions). Der
# Geheim-/Lokal-Modus erzwingt die lokale Engine. Audio ist kein Token-Strom →
# keine TokenMeter-Meldung.

@app.get("/api/transcribe/engines")
async def transcribe_engines():
    """Verfügbare Transkriptions-Engines/Modelle für die UI.

    Meldet, ob die lokale Engine (faster-whisper) installiert ist, welche
    lokalen Modellgrößen es gibt, welche API-Anbieter konfiguriert sind, sowie
    ob der Geheim-Modus die API-Wahl gerade sperrt."""
    local_ok = _transcribe.local_available()
    providers = [{"id": p.get("id"), "name": p.get("name") or p.get("id")}
                 for p in _llm.load_providers() if p.get("id")]
    return {
        "local_available": local_ok,
        "local_models": _transcribe.list_local_models() if local_ok else [],
        "local_default": STT_MODEL,
        "providers": providers,
        "local_only": _secret_local(),
        "api_enabled": bool(_CONFIG.get("enable_api", True)),
    }


def _remote_audio_target(model: str) -> tuple:
    """(base_url, headers, real_model) für einen Remote-STT-Aufruf oder ``(None,…)``.

    Nutzt die Anbieter-Konfiguration aus ``tools/llm.py`` (``provider::modell``)."""
    provider, real = _llm.resolve(model)
    if not provider:
        return None, None, real
    base = (provider.get("base_url") or "").rstrip("/")
    headers = {"Authorization": f"Bearer {provider.get('api_key', '')}"}
    return base, headers, real


@app.post("/api/transcribe")
async def transcribe_audio(
    audio: UploadFile = File(...),
    engine: str = Form("local"),
    model: str = Form(""),
    language: str = Form(""),
    task: str = Form("transcribe"),
):
    """Transkribiert eine hochgeladene/aufgenommene Audiodatei.

    ``engine`` = ``local`` (faster-whisper) oder ``api`` (Anbieter-Modell
    ``provider::modell``). Im Geheim-Modus wird ``api`` auf ``local`` gezwungen."""
    data = await audio.read()
    if not data:
        raise HTTPException(400, "Leere Audiodatei.")
    safe_name = re.sub(r"[^A-Za-z0-9._-]", "_", audio.filename or "audio")[-80:] or "audio"
    fid = f"{uuid.uuid4().hex[:8]}_{safe_name}"
    fp = TRANSCRIPTS_DIR / fid
    fp.write_bytes(data)

    use_engine = (engine or "local").strip().lower()
    # Geheim-/Lokal-Modus: API-Transkription unterbinden → immer lokal.
    forced_local = False
    if _secret_local() and use_engine != "local":
        use_engine = "local"
        forced_local = True

    if use_engine == "api":
        mdl = (model or "").strip()
        if not mdl or not _llm.is_remote(mdl):
            raise HTTPException(400, "Für die API-Transkription ein Anbieter-Modell "
                                     "(anbieter::modell) wählen.")
        base, headers, real = _remote_audio_target(mdl)
        if not base:
            raise HTTPException(400, "Unbekannter API-Anbieter für die Transkription.")
        want_task = "translations" if str(task).strip().lower() == "translate" else "transcriptions"
        url = f"{base}/audio/{want_task}"
        files = {"file": (safe_name, data, audio.content_type or "application/octet-stream")}
        form = {"model": real, "response_format": "verbose_json"}
        lang = (language or "").strip().lower()
        if lang and lang != "auto":
            form["language"] = lang
        try:
            async with httpx.AsyncClient(timeout=300) as client:
                resp = await client.post(url, headers=headers, data=form, files=files)
            if resp.status_code >= 400:
                raise HTTPException(502, f"API-Transkription fehlgeschlagen "
                                         f"(HTTP {resp.status_code}): {resp.text[:300]}")
            j = resp.json()
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(502, f"API-Transkription fehlgeschlagen: {e}")
        segs = [{"start": round(float(s.get("start", 0) or 0), 2),
                 "end": round(float(s.get("end", 0) or 0), 2),
                 "text": (s.get("text") or "").strip()}
                for s in (j.get("segments") or [])]
        return {
            "text": (j.get("text") or "").strip(),
            "segments": segs,
            "language": j.get("language") or lang or "",
            "engine": "api", "model": mdl, "audio_id": fid,
        }

    # ── Lokale Engine (faster-whisper) ──────────────────────────────────────
    if not _transcribe.local_available():
        raise HTTPException(503, "Lokale Transkription nicht verfügbar — faster-whisper "
                                 "ist nicht installiert.")
    mdl = (model or "").strip() or STT_MODEL
    if _llm.is_remote(mdl):  # versehentlich Remote-Name im lokalen Pfad → Default
        mdl = STT_MODEL
    try:
        result = await asyncio.to_thread(
            _transcribe.transcribe_local, str(fp), mdl, language, task,
            STT_DEVICE, STT_COMPUTE, str(STT_DOWNLOAD_ROOT),
        )
    except Exception as e:
        raise HTTPException(500, f"Lokale Transkription fehlgeschlagen: {e}")
    result.update({"engine": "local", "model": mdl, "audio_id": fid,
                   "forced_local": forced_local})
    return result


# ── Sprachausgabe (TTS) über ein API-Modell ───────────────────────────────────
# Standardmäßig läuft die Sprachausgabe rein clientseitig über die Web Speech API
# des Browsers (kostenlos, lokal, nichts wird gespeichert — siehe static/js/tts.js).
# Optional kann im Profil ein API-TTS-Modell (``anbieter::modell``, z. B.
# ``openai::tts-1``) gewählt werden; dann synthetisiert der Anbieter die Sprache
# (OpenAI-kompatibles ``/audio/speech``) und wir liefern das Audio an den Browser.
# Der Geheim-Modus erzwingt die Browser-Ausgabe (API wird ignoriert).

# Antwortstil-Persona (tone) → OpenAI-kompatible Stimme. Grobe Zuordnung nach
# Geschlecht/Alter/Klang; Anbieter ohne diese Stimmen fallen i. d. R. auf ihre
# Standardstimme zurück.
_TTS_VOICE_MAP = {
    "roboter":   "alloy",    # neutral/synthetisch
    "professor": "onyx",     # tiefer, älterer Mann
    "doktor":    "shimmer",  # ruhige, ältere Frau
    "felix":     "echo",     # jüngerer Mann
    "sandra":    "nova",     # jüngere Frau
    "hartman":   "onyx",     # zackiger Ausbilder (tiefer, markanter Mann)
    "":          "alloy",    # Standard
}


def _tts_model() -> str:
    """Im Profil gewähltes API-TTS-Modell (``anbieter::modell``) oder leer."""
    m = str(_load_profile().get("tts_model", "") or "").strip()
    return "" if m in _MODEL_PLACEHOLDERS else m


@app.get("/api/tts/config")
async def tts_config():
    """UI-Info: ist API-TTS aktiv, welche Optionen (aus den Anbietern) gibt es,
    Persona→Stimme-Zuordnung. Die Optionsliste baut sich aus den konfigurierten
    Anbietern (gängige TTS-Modellnamen als Vorschlag)."""
    m = _tts_model()
    options = [{"value": "", "label": "Browser (lokal, Web Speech API)"}]
    for p in _llm.load_providers():
        pid = p.get("id")
        if not pid:
            continue
        pname = p.get("name") or pid
        for tm in ("tts-1", "gpt-4o-mini-tts"):
            options.append({"value": f"{pid}::{tm}", "label": f"{pname} · {tm}"})
    # aktuelle Auswahl immer wählbar halten
    if m and not any(o["value"] == m for o in options):
        options.append({"value": m, "label": m})
    return {
        "tts_model": m,
        "api_active": bool(m) and _llm.is_remote(m) and not _secret_local(),
        "secret": _secret_local(),
        "enable_api": bool(_CONFIG.get("enable_api", True)),
        "options": options,
        "voices": _TTS_VOICE_MAP,
    }


@app.post("/api/tts")
async def tts_speak(req: Request):
    """Synthetisiert Text zu Sprache über das im Profil gewählte API-Modell.

    Antwort: Audio (``audio/mpeg``). Ist kein API-Modell gewählt, das Modell nicht
    remote oder der Geheim-Modus aktiv → **HTTP 409**, damit das Frontend auf die
    Browser-Sprachausgabe zurückfällt."""
    body = await req.json()
    text = str(body.get("text", "") or "").strip()
    tone = str(body.get("tone", "") or "").strip().lower()
    if not text:
        raise HTTPException(400, "Kein Text für die Sprachausgabe.")
    m = _tts_model()
    if _secret_local() or not m or not _llm.is_remote(m):
        raise HTTPException(409, "API-Sprachausgabe nicht aktiv – Browser-Ausgabe nutzen.")
    provider, real = _llm.resolve(m)
    if not provider:
        raise HTTPException(400, "Unbekannter API-Anbieter für die Sprachausgabe.")
    base = (provider.get("base_url") or "").rstrip("/")
    headers = {"Authorization": f"Bearer {provider.get('api_key', '')}",
               "Content-Type": "application/json"}
    voice = _TTS_VOICE_MAP.get(tone) or _TTS_VOICE_MAP[""]
    payload = {"model": real, "input": text[:4000], "voice": voice,
               "response_format": "mp3"}
    try:
        async with httpx.AsyncClient(timeout=120) as client:
            resp = await client.post(f"{base}/audio/speech", headers=headers, json=payload)
        if resp.status_code >= 400:
            raise HTTPException(502, f"API-Sprachausgabe fehlgeschlagen "
                                     f"(HTTP {resp.status_code}): {resp.text[:300]}")
        audio = resp.content
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(502, f"API-Sprachausgabe fehlgeschlagen: {e}")
    return Response(content=audio, media_type="audio/mpeg")


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


@app.get("/api/image/config")
async def image_config():
    """UI-Info analog ``/api/tts/config``: aktives Bildmodell, SD-URL, Presets und
    die wählbaren Optionen (aus = leer, lokal SD, je Anbieter DALL·E/gpt-image)."""
    m = _image_model()
    options = [
        {"value": "", "label": "Aus (keine Bildgenerierung)"},
        {"value": "local::sd", "label": "Lokal · Stable Diffusion WebUI"},
    ]
    if not _secret_local():
        # Einheitlich wie die Rollen-Modelle: die unter „☁ KI-Anbieter" konfigurierten
        # Anbieter samt ihrer Modell-Liste (z. B. z-image-turbo). Der Anbieter (URL +
        # Schlüssel) wird dort eingetragen; hier wird nur das Modell gewählt.
        for p in _llm.load_providers():
            pid = p.get("id")
            if not pid:
                continue
            pname = p.get("name") or pid
            seen = set()
            for mdl in (p.get("models") or []):
                val = f"{pid}::{mdl}"
                seen.add(val)
                options.append({"value": val, "label": f"{pname} · {mdl}"})
            # Gängige Bildmodelle zusätzlich anbieten, falls der Anbieter sie nicht listet.
            for im in ("dall-e-3", "gpt-image-1"):
                val = f"{pid}::{im}"
                if val not in seen:
                    options.append({"value": val, "label": f"{pname} · {im}"})
    # aktuelle Auswahl immer wählbar halten
    if m and not any(o["value"] == m for o in options):
        options.append({"value": m, "label": m})
    return {
        "image_model": m,
        "sd_url": _sd_url(),
        "secret": _secret_local(),
        "enable_api": bool(_CONFIG.get("enable_api", True)),
        "sizes": [{"value": k, "label": v["label"]} for k, v in _IMAGE_SIZES.items()],
        "options": options,
    }


@app.post("/api/image/generate")
async def image_generate(req: Request):
    """Erzeugt ein Bild aus einem Prompt (HTTP-Endpoint, siehe _generate_image_core)."""
    body = await req.json()
    return await _generate_image_core(body.get("prompt", ""), body.get("negative_prompt", ""),
                                      body.get("size", "square"), body.get("model", ""))


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


@app.get("/api/capacity")
async def get_capacity():
    return {"items": _load_capacity()}


@app.put("/api/capacity")
async def put_capacity(req: Request):
    body = await req.json()
    items = body.get("items") if isinstance(body, dict) else body
    if not isinstance(items, list):
        raise HTTPException(status_code=400, detail="items-Liste erwartet")
    return {"items": _save_capacity(items)}


# ── Verwaltung mehrerer benannter Ressourcenlisten ──────────────────────────────
@app.get("/api/capacity/lists")
async def get_capacity_lists():
    """Übersicht aller Listen + aktuelle Auswahl (ohne die vollen Items)."""
    data = _load_cap_lists()
    return {
        "lists": [{"id": l["id"], "name": l["name"], "n_items": len(l["items"]),
                   "updated_at": l["updated_at"]} for l in data["lists"]],
        "selected": data["selected"],
    }


@app.get("/api/capacity/lists/{list_id}")
async def get_capacity_list(list_id: str):
    data = _load_cap_lists()
    for l in data["lists"]:
        if l["id"] == list_id:
            return l
    raise HTTPException(status_code=404, detail="Liste nicht gefunden")


@app.post("/api/capacity/lists")
async def create_capacity_list(req: Request):
    body = await req.json()
    name = str(body.get("name", "")).strip()[:80] or "Neue Liste"
    data = _load_cap_lists()
    new = {"id": uuid.uuid4().hex[:12], "name": name, "items": [], "updated_at": time.time()}
    data["lists"].append(new)
    data["selected"] = list(data["selected"]) + [new["id"]]
    _save_cap_lists(data)
    return {"id": new["id"], "name": new["name"]}


@app.put("/api/capacity/lists/{list_id}")
async def update_capacity_list(list_id: str, req: Request):
    body = await req.json()
    data = _load_cap_lists()
    found = None
    for l in data["lists"]:
        if l["id"] == list_id:
            found = l
            break
    if found is None:
        # Upsert: unbekannte ID neu anlegen (erlaubt Anlegen über PUT)
        found = {"id": list_id, "name": "", "items": [], "updated_at": time.time()}
        data["lists"].append(found)
        if list_id not in data["selected"]:
            data["selected"].append(list_id)
    if "name" in body:
        found["name"] = str(body.get("name", "")).strip()[:80] or found.get("name") or "Liste"
    if "items" in body and isinstance(body["items"], list):
        found["items"] = body["items"]
    found["updated_at"] = time.time()
    saved = _save_cap_lists(data)
    out = next((l for l in saved["lists"] if l["id"] == found["id"]), found)
    return out


@app.delete("/api/capacity/lists/{list_id}")
async def delete_capacity_list(list_id: str):
    data = _load_cap_lists()
    data["lists"] = [l for l in data["lists"] if l["id"] != list_id]
    data["selected"] = [s for s in data["selected"] if s != list_id]
    _save_cap_lists(data)
    return {"ok": True}


@app.put("/api/capacity/selection")
async def set_capacity_selection(req: Request):
    """Setzt, welche Listen (per Häkchen) für Auswertung & Planer aktiv sind."""
    body = await req.json()
    sel = body.get("selected") if isinstance(body, dict) else body
    if not isinstance(sel, list):
        raise HTTPException(status_code=400, detail="selected-Liste erwartet")
    data = _load_cap_lists()
    ids = {l["id"] for l in data["lists"]}
    data["selected"] = [s for s in sel if s in ids]
    _save_cap_lists(data)
    return {"selected": data["selected"]}


# ── Anfrage-Auswertung (RFQ) ──────────────────────────────────────────────────
# Große XLS-Anfragen mit vielen Arbeitspaketen: je Paket ein Dispatcher-/Master-
# Aufruf, der die zuständige Fachrolle bestimmt und interessant/Partner/Best-Cost-
# Country bewertet. Robust mit Zwischenspeicherung (data/rfq/{job}.json) + Resume.

_RFQ_SYSTEM = (
    "Du bist Angebots- und Vergabemanager. Du bekommst EIN Arbeitspaket aus einer "
    "großen Anfrage. Bestimme die zuständige Fachrolle/Disziplin (bevorzugt eine aus "
    "der bereitgestellten Kapazitätsliste) und bewerte das Paket nüchtern. "
    "Nutze die Kapazitätsliste (Rollen, Skills, Land) für die Zuordnung und für die "
    "Best-Cost-Country-Einschätzung. Erfinde nichts; bei Unklarheit 'prüfen'. "
    "Antworte NUR mit JSON in genau diesem Format: "
    '{"responsible":"Fachrolle/Disziplin",'
    '"interesting":{"verdict":"ja|nein|pruefen","reason":"kurz"},'
    '"partner":{"needed":true,"type":"Art des Partners oder leer"},'
    '"bcc":{"suitable":true,"region":"Land/Region oder leer","reason":"kurz"}}'
)


def _rfq_job_path(job_id: str) -> Path:
    safe = re.sub(r"[^A-Za-z0-9_-]", "", job_id or "")[:40] or uuid.uuid4().hex[:12]
    return RFQ_DIR / f"{safe}.json"


_RFQ_CUSTOM_SYSTEM = (
    "Du bewertest EIN Arbeitspaket aus einer großen Anfrage anhand einer konkreten "
    "Vorgabe/Frage. Antworte knapp und sachlich, erfinde nichts. "
    'Antworte NUR mit JSON: {"value":"kurze Antwort/Einordnung (wenige Worte)",'
    '"note":"optionale 1-Satz-Begründung"}.'
)


def _rfq_agent_prompt(agent_id: str) -> str:
    """System-Prompt eines Agenten anhand seiner ID (für agentenbasierte Spalten)."""
    if not agent_id:
        return ""
    fp = _agent_path_by_id(agent_id)
    if not fp:
        return ""
    try:
        return str((json.loads(fp.read_text(encoding="utf-8")) or {}).get("system_prompt", "")).strip()
    except Exception:
        return ""


def _sanitize_rfq_columns(cols) -> list:
    """Eigene Bewertungsspalten säubern: max. 6, je {key,name,prompt?,agent_id?}."""
    out = []
    for c in (cols or []):
        if not isinstance(c, dict):
            continue
        name = str(c.get("name", "")).strip()[:40]
        raw_key = str(c.get("key", "")).strip() or name
        key = re.sub(r"[^a-z0-9_]", "", raw_key.lower())[:24]
        if not key or not name:
            continue
        # Spalte braucht eine Vorgabe: freier Prompt ODER ein Agent
        if not str(c.get("prompt", "")).strip() and not str(c.get("agent_id", "")).strip():
            continue
        # Doppelte Keys eindeutig machen
        if any(o["key"] == key for o in out):
            key = (key + "_" + uuid.uuid4().hex[:3])[:24]
        out.append({
            "key": key, "name": name,
            "prompt": str(c.get("prompt", "")).strip()[:1000],
            "agent_id": str(c.get("agent_id", "")).strip()[:64],
        })
        if len(out) >= 6:
            break
    return out


def _llm_tok(j: dict) -> tuple:
    """(prompt_tokens, completion_tokens) aus einer Ollama-förmigen Antwort."""
    return int((j or {}).get("prompt_eval_count") or 0), int((j or {}).get("eval_count") or 0)


def _rfq_task_max() -> int:
    """Max. Zeichen je Arbeitspaket-Text — skaliert mit dem Kontextfenster (statt fest 4000).
    So wird ein langes Paket nicht künstlich beschnitten, wenn das Fenster groß genug ist."""
    return max(4000, int(_profile_num_ctx() * 3.5 * 0.4))


def _rfq_cap_max() -> int:
    """Max. Zeichen der Kapazitätsliste je Auswertung, damit eine sehr große (vereinigte)
    Ressourcenliste nicht jeden einzelnen Aufruf dominiert/das Fenster sprengt."""
    return max(2000, int(_profile_num_ctx() * 3.5 * 0.25))


async def _rfq_eval_custom(client, model: str, task: str, columns: list):
    """Wertet je eigener Spalte EIN Arbeitspaket aus → ({key: {value, note}}, tok_in, tok_out).
    Pro Spalte ein LLM-Aufruf (Agent-Persona oder freier Prompt als Vorgabe)."""
    out = {}
    tin = tout = 0
    for col in columns:
        key = col.get("key")
        if not key:
            continue
        instr = (col.get("prompt") or "").strip()
        persona = _rfq_agent_prompt(col.get("agent_id") or "")
        _tmax = _rfq_task_max()
        if persona:
            sys = persona + '\n\nAntworte NUR mit JSON: {"value":"kurze Antwort","note":"1-Satz-Begründung"}.'
            usr = (f"Arbeitspaket:\n{task[:_tmax]}"
                   + (f"\n\nZusätzliche Vorgabe: {instr}" if instr else ""))
        else:
            sys = _RFQ_CUSTOM_SYSTEM
            usr = (f"Vorgabe/Frage für die Spalte „{col.get('name', '')}\":\n"
                   f"{instr or col.get('name', '')}\n\nArbeitspaket:\n{task[:_tmax]}")
        try:
            async with _model_session(model):
                resp = await _llm.chat(client, {
                    "model": model, "think": False, "stream": False, "format": "json",
                    "messages": [{"role": "system", "content": sys},
                                 {"role": "user", "content": usr}],
                    "options": {"num_ctx": _profile_num_ctx()}, "keep_alive": KEEP_ALIVE,
                })
                resp.raise_for_status()
            _j = resp.json()
            _ti, _to = _llm_tok(_j)
            tin += _ti; tout += _to
            d = _parse_llm_json(_j.get("message", {}).get("content", "")) or {}
            out[key] = {"value": str(d.get("value", "")).strip()[:200],
                        "note": str(d.get("note", "")).strip()[:300]}
        except Exception as e:
            out[key] = {"value": "(Fehler)", "note": str(e)[:120]}
    return out, tin, tout


async def _rfq_eval_one(client, model: str, task: str, capacity_ctx: str,
                        web: bool, rag_collections: list,
                        custom_columns: list = None) -> dict:
    """Wertet EIN Arbeitspaket aus → strukturiertes Ergebnis-dict."""
    grounding = []
    if web and task.strip():
        try:
            from tools.search import search_with_sources
            _, txt = await search_with_sources(task[:200], 4)
            if txt:
                grounding.append("Websuche:\n" + txt[:1800])
        except Exception:
            pass
    if rag_collections:
        try:
            from tools.rag import query_collections
            hits = await query_collections(rag_collections, task[:500], top_k_cap=4)
            if hits:
                grounding.append("Wissensdatenbank:\n" + "\n".join(
                    h.get("text", "") for h in hits)[:1800])
        except Exception:
            pass
    user = ""
    if capacity_ctx:
        user += f"Kapazitätsliste (verfügbare Rollen/Partner):\n{capacity_ctx[:_rfq_cap_max()]}\n\n"
    if grounding:
        user += "\n\n".join(grounding) + "\n\n"
    user += f"Arbeitspaket:\n{task[:_rfq_task_max()]}"
    # _model_session serialisiert lokale Generierungen (VRAM-Lock) und ist für
    # Remote-Modelle ein No-op → mehrere Remote-Aufrufe laufen echt parallel.
    async with _model_session(model):
        resp = await _llm.chat(client, {
            "model": model,
            "think": False,
            "stream": False,
            "format": "json",
            "messages": [
                {"role": "system", "content": _RFQ_SYSTEM},
                {"role": "user", "content": user},
            ],
            "options": {"num_ctx": _profile_num_ctx()},
            "keep_alive": KEEP_ALIVE,
        })
        resp.raise_for_status()
    _j = resp.json()
    _ti, _to = _llm_tok(_j)
    data = _parse_llm_json(_j.get("message", {}).get("content", "")) or {}
    inter = data.get("interesting") or {}
    partner = data.get("partner") or {}
    bcc = data.get("bcc") or {}
    result = {
        "responsible": str(data.get("responsible", "")).strip(),
        "interesting": str(inter.get("verdict", "")).strip().lower(),
        "interesting_reason": str(inter.get("reason", "")).strip(),
        "partner_needed": bool(partner.get("needed")),
        "partner_type": str(partner.get("type", "")).strip(),
        "bcc_suitable": bool(bcc.get("suitable")),
        "bcc_region": str(bcc.get("region", "")).strip(),
        "bcc_reason": str(bcc.get("reason", "")).strip(),
    }
    if custom_columns:
        cres, cti, cto = await _rfq_eval_custom(client, model, task, custom_columns)
        result["custom"] = cres
        _ti += cti; _to += cto
    # Token-Verbrauch für den Sitzungszähler (wird in gen() summiert, vor dem Streamen entfernt)
    result["__tok"] = {"in": _ti, "out": _to}
    return result


@app.post("/api/rfq/ask")
async def rfq_ask(req: Request):
    """Freie Rückfrage zur ausgewerteten Anfrage (Chat-Zeile im Anfrage-Tab). Ein
    LLM-Aufruf mit einer kompakten Zusammenfassung der Auswertung als Kontext."""
    body = await req.json()
    question = str(body.get("question", "")).strip()
    if not question:
        raise HTTPException(status_code=400, detail="Keine Frage angegeben")
    context = str(body.get("context", "")).strip()[:max(9000, int(_profile_num_ctx() * 3.5 * 0.6))]
    model = _pick_model(body.get("model"), _model_for("general"))
    sys = (
        "Du bist Angebots- und Vergabeassistent. Beantworte die Frage des Nutzers zur "
        "ausgewerteten Anfrage knapp, konkret und auf Deutsch — möglichst nur auf Basis "
        "der bereitgestellten Auswertung. Fehlt eine Information, sage das."
    )
    usr = (f"Auswertung (Auszug):\n{context}\n\n" if context else "") + f"Frage: {question}"
    try:
        async with _model_session(model), httpx.AsyncClient(timeout=180) as client:
            resp = await _llm.chat(client, {
                "model": model, "think": False, "stream": False,
                "messages": [{"role": "system", "content": sys},
                             {"role": "user", "content": usr}],
                "options": {"num_ctx": _profile_num_ctx()}, "keep_alive": KEEP_ALIVE,
            })
            resp.raise_for_status()
        _j = resp.json()
        answer = str(_j.get("message", {}).get("content", "")).strip()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Anfrage-Chat fehlgeschlagen: {e}")
    _ti, _to = _llm_tok(_j)
    return {"answer": answer or "(keine Antwort)", "tokens": {"in": _ti, "out": _to}}


@app.post("/api/rfq/preview")
async def rfq_preview(file: UploadFile = File(...), sheet: str = Form(""),
                      header_row: int = Form(0)):
    """Lädt die Anfrage-Datei hoch und liefert Blätter/Spalten/Beispielzeilen zur
    Spaltenzuordnung zurück."""
    from tools.files import read_table
    fid = f"rfq_{uuid.uuid4().hex[:8]}_{file.filename}"
    fp = UPLOADS_DIR / fid
    fp.write_bytes(await file.read())
    try:
        tbl = await asyncio.to_thread(read_table, fp, sheet or None, int(header_row), 5)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Tabelle nicht lesbar: {e}")
    if tbl.get("error"):
        raise HTTPException(status_code=400, detail=tbl["error"])
    # Gesamtzeilenzahl (ohne 5er-Limit) separat bestimmen
    full = await asyncio.to_thread(read_table, fp, tbl.get("sheet") or None, int(header_row), None)
    return {
        "file_id": fid,
        "sheets": tbl.get("sheets", []),
        "sheet": tbl.get("sheet", ""),
        "headers": tbl.get("headers", []),
        "sample_rows": tbl.get("rows", []),
        "n_rows": len(full.get("rows", [])),
    }


@app.post("/api/rfq/evaluate")
async def rfq_evaluate(req: Request):
    body = await req.json()
    file_id = str(body.get("file_id", "")).strip()
    fp = UPLOADS_DIR / file_id
    if not file_id or not fp.exists():
        raise HTTPException(status_code=404, detail="Datei nicht gefunden — bitte erneut hochladen")
    sheet = str(body.get("sheet", "")).strip() or None
    header_row = int(body.get("header_row", 0) or 0)
    task_col = int(body.get("task_col", -1))
    id_col = body.get("id_col")
    title_col = body.get("title_col")
    model = _pick_model(body.get("model"), _model_for("general"))
    web = bool(body.get("web_search"))
    rag_collections = body.get("rag_collections") or []
    custom_columns = _sanitize_rfq_columns(body.get("custom_columns"))
    limit = body.get("limit")
    job_id = str(body.get("job_id", "")).strip() or uuid.uuid4().hex[:12]
    resume = bool(body.get("resume"))
    # Parallelität nur für Remote-Modelle (externe API) — lokal bremst der VRAM-Lock
    # ohnehin auf 1. Client darf override liefern; sonst Default 6 (remote) / 1 (lokal).
    _remote = _llm.is_remote(model)
    try:
        _req_conc = int(body.get("concurrency", 0))
    except (TypeError, ValueError):
        _req_conc = 0
    concurrency = max(1, min(_req_conc or 6, 12)) if _remote else 1

    async def gen():
        from tools.files import read_table
        tbl = await asyncio.to_thread(read_table, fp, sheet, header_row, None)
        rows = tbl.get("rows", [])
        headers = tbl.get("headers", [])
        if task_col < 0 or task_col >= len(headers):
            yield _sse({"type": "error", "message": "Keine gültige Aufgaben-Spalte gewählt"})
            return
        if isinstance(limit, int) and limit > 0:
            rows = rows[:limit]
        capacity_ctx = _capacity_context()

        # Job-Datei laden (Resume) oder neu anlegen
        jpath = _rfq_job_path(job_id)
        done: dict = {}
        if resume and jpath.exists():
            try:
                done = (json.loads(jpath.read_text(encoding="utf-8")) or {}).get("results", {})
            except Exception:
                done = {}

        def _cell(row, idx):
            try:
                return row[int(idx)] if idx is not None and int(idx) >= 0 else ""
            except (TypeError, ValueError, IndexError):
                return ""

        _EMPTY = {"responsible": "", "interesting": "", "interesting_reason": "(leer)",
                  "partner_needed": False, "partner_type": "", "bcc_suitable": False,
                  "bcc_region": "", "bcc_reason": ""}

        async def _one(i, row, client):
            """Liefert (i, rid, title, task, cells, result, is_new)."""
            task = str(_cell(row, task_col)).strip()
            rid = str(_cell(row, id_col)).strip() if id_col is not None else ""
            title = str(_cell(row, title_col)).strip() if title_col is not None else ""
            key = str(i)
            if key in done:
                return i, rid, title, task, list(row), done[key], False
            if not task:
                return i, rid, title, task, list(row), dict(_EMPTY), False
            try:
                result = await _rfq_eval_one(client, model, task, capacity_ctx, web,
                                             rag_collections, custom_columns)
            except Exception as e:
                result = {"responsible": "", "interesting": "fehler",
                          "interesting_reason": str(e)[:200], "partner_needed": False,
                          "partner_type": "", "bcc_suitable": False, "bcc_region": "",
                          "bcc_reason": ""}
            return i, rid, title, task, list(row), result, True

        total = len(rows)
        yield _sse({"type": "start", "job_id": job_id, "total": total,
                    "headers": headers, "concurrency": concurrency, "remote": _remote,
                    "custom_columns": custom_columns})
        counts = {"interesting": 0, "partner": 0, "bcc": 0}
        tok_total = {"in": 0, "out": 0}
        indexed = list(enumerate(rows))
        async with httpx.AsyncClient(timeout=300) as client:
            # In Blöcken der Größe `concurrency` abarbeiten (remote echt parallel,
            # lokal = 1). Reihenfolge der Ausgabe bleibt erhalten; pro Block persistieren.
            for bs in range(0, total, concurrency):
                batch = indexed[bs:bs + concurrency]
                done_batch = await asyncio.gather(*(_one(i, row, client) for i, row in batch))
                dirty = False
                for i, rid, title, task, cells, result, is_new in done_batch:
                    # Token-Verbrauch herausziehen (nicht streamen/persistieren)
                    _tk = result.pop("__tok", None) if isinstance(result, dict) else None
                    if _tk:
                        tok_total["in"] += int(_tk.get("in") or 0)
                        tok_total["out"] += int(_tk.get("out") or 0)
                    if is_new:
                        done[str(i)] = result
                        dirty = True
                    if result.get("interesting") == "ja":
                        counts["interesting"] += 1
                    if result.get("partner_needed"):
                        counts["partner"] += 1
                    if result.get("bcc_suitable"):
                        counts["bcc"] += 1
                    yield _sse({"type": "row", "index": i, "id": rid, "title": title,
                                "task": task, "result": result, "cells": cells,
                                "pct": int((i + 1) / total * 100) if total else 100})
                if dirty:
                    try:
                        jpath.write_text(json.dumps({"job_id": job_id, "results": done},
                                                    ensure_ascii=False), encoding="utf-8")
                    except Exception:
                        pass
        yield _sse({"type": "done", "job_id": job_id, "summary": {"n": total, **counts},
                    "tokens": tok_total})

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ── RFQ → Planer-Übergabe ─────────────────────────────────────────────────────
# Ausgewählte (interessante) Tickets gesamthaft in EINEN Plan überführen. Da RFQ
# keine Stunden liefert, schätzt das LLM bei der Übergabe Aufwand + Dauer je Ticket
# (nur für die Teilmenge). Die „Zuständige Rolle" wird zur Ressource; Kosten/Auslastung
# rechnet der Planer anschließend gegen die globale Kapazitätsliste.

_RFQ_ESTIMATE_SYSTEM = (
    "Du bist Projektkalkulator. Schätze für EIN Arbeitspaket realistisch den Aufwand in "
    "Personenstunden (hours) und die Dauer in Arbeitstagen (duration_days) für die genannte "
    "Rolle. Sei nüchtern; bei Unklarheit konservativ. "
    'Antworte NUR mit JSON: {"hours":Zahl,"duration_days":Zahl}.'
)


async def _rfq_estimate_one(client, model: str, task: str, role: str,
                            tok: Optional[dict] = None) -> dict:
    """Schätzt Aufwand (h) und Dauer (Tage) für ein Ticket. Fallback 8 h / 1 Tag.
    ``tok`` (optional): dict {"in","out"}, in das der Tokenverbrauch summiert wird."""
    usr = f"Rolle: {role or 'unbestimmt'}\n\nArbeitspaket:\n{task[:_rfq_task_max()]}"
    try:
        async with _model_session(model):
            resp = await _llm.chat(client, {
                "model": model, "think": False, "stream": False, "format": "json",
                "messages": [
                    {"role": "system", "content": _RFQ_ESTIMATE_SYSTEM},
                    {"role": "user", "content": usr},
                ],
                "options": {"num_ctx": _profile_num_ctx()}, "keep_alive": KEEP_ALIVE,
            })
            resp.raise_for_status()
        _re_j = resp.json()
        if tok is not None:
            _a, _b = _llm_tok(_re_j)
            tok["in"] += _a
            tok["out"] += _b
        data = _parse_llm_json(_re_j.get("message", {}).get("content", "")) or {}
    except Exception:
        data = {}

    def _num(v, default):
        try:
            n = float(v)
            return n if n > 0 else default
        except (TypeError, ValueError):
            return default
    return {"hours": _num(data.get("hours"), 8.0), "duration_days": _num(data.get("duration_days"), 1.0)}


def _rfq_task_note(rid: str, res: dict) -> str:
    bits = []
    if rid:
        bits.append(f"ID {rid}")
    if res.get("interesting"):
        bits.append(f"interessant: {res['interesting']}")
    if res.get("partner_needed"):
        bits.append(f"Partner: {res.get('partner_type') or 'ja'}")
    if res.get("bcc_suitable"):
        bits.append(f"BCC: {res.get('bcc_region') or 'ja'}")
    return " · ".join(bits)


@app.post("/api/rfq/to-plan")
async def rfq_to_plan(req: Request):
    body = await req.json()
    file_id = str(body.get("file_id", "")).strip()
    fp = UPLOADS_DIR / file_id
    if not file_id or not fp.exists():
        raise HTTPException(status_code=404, detail="Datei nicht gefunden — bitte erneut hochladen")
    job_id = str(body.get("job_id", "")).strip()
    jpath = _rfq_job_path(job_id) if job_id else None
    if not jpath or not jpath.exists():
        raise HTTPException(status_code=400, detail="Keine Auswertung gefunden — bitte zuerst auswerten")
    sheet = str(body.get("sheet", "")).strip() or None
    header_row = int(body.get("header_row", 0) or 0)
    task_col = int(body.get("task_col", -1))
    id_col = body.get("id_col")
    title_col = body.get("title_col")
    model = _pick_model(body.get("model"), _model_for("general"))
    plan_name = str(body.get("plan_name", "")).strip() or "Anfrage-Auswertung"
    selection = body.get("selection", "interesting")
    _remote = _llm.is_remote(model)
    try:
        _req_conc = int(body.get("concurrency", 0))
    except (TypeError, ValueError):
        _req_conc = 0
    concurrency = max(1, min(_req_conc or 6, 12)) if _remote else 1

    async def gen():
        from tools.files import read_table
        try:
            results = (json.loads(jpath.read_text(encoding="utf-8")) or {}).get("results", {})
        except Exception:
            results = {}
        tbl = await asyncio.to_thread(read_table, fp, sheet, header_row, None)
        rows = tbl.get("rows", [])
        headers = tbl.get("headers", [])
        if task_col < 0 or task_col >= len(headers):
            yield _sse({"type": "error", "message": "Keine gültige Aufgaben-Spalte gewählt"})
            return

        def _cell(row, idx):
            try:
                return row[int(idx)] if idx is not None and int(idx) >= 0 else ""
            except (TypeError, ValueError, IndexError):
                return ""

        # Zu übernehmende Zeilen bestimmen
        if isinstance(selection, list):
            sel = [int(i) for i in selection if isinstance(i, int) or str(i).isdigit()]
        else:
            sel = []
            for i in range(len(rows)):
                r = results.get(str(i)) or {}
                if selection == "all":
                    if r:
                        sel.append(i)
                elif (r.get("interesting") or "") == "ja":
                    sel.append(i)
        sel = [i for i in sel if 0 <= i < len(rows)]
        total = len(sel)
        yield _sse({"type": "start", "total": total, "concurrency": concurrency, "remote": _remote})
        if not total:
            yield _sse({"type": "error", "message": "Keine passenden Tickets für die Auswahl"})
            return

        cap_items = _load_capacity()
        _tok = {"in": 0, "out": 0}

        async def _one(n, i, client):
            row = rows[i]
            res = results.get(str(i)) or {}
            task = str(_cell(row, task_col)).strip()
            rid = str(_cell(row, id_col)).strip() if id_col is not None else ""
            title = str(_cell(row, title_col)).strip() if title_col is not None else ""
            role = str(res.get("responsible", "")).strip()
            est = await _rfq_estimate_one(client, model, task, role, tok=_tok)
            cap = _match_catalog(role, cap_items) if role else None
            rate = float((cap or {}).get("rate", 0) or 0)
            name = (title or task[:80] or f"Paket {i + 1}").strip()
            return {
                "id": f"T{n + 1}", "name": name, "duration": round(est["duration_days"], 1),
                "predecessors": [], "successors": [], "resources": "",
                "resource_list": [{"kind": "human", "name": role or "unbestimmt", "qty": 1,
                                   "hours": round(est["hours"], 1), "rate": rate, "lead": 0}],
                "notes": _rfq_task_note(rid, res), "area": role or "Sonstige",
                "is_start": False, "is_end": False,
                "rfq": {"interesting": res.get("interesting", ""),
                        "partner_needed": bool(res.get("partner_needed")),
                        "partner_type": res.get("partner_type", ""),
                        "bcc_suitable": bool(res.get("bcc_suitable")),
                        "bcc_region": res.get("bcc_region", "")},
            }

        tasks = [None] * total
        async with httpx.AsyncClient(timeout=300) as client:
            enum_sel = list(enumerate(sel))
            for bs in range(0, total, concurrency):
                batch = enum_sel[bs:bs + concurrency]
                computed = await asyncio.gather(*(_one(n, i, client) for n, i in batch))
                for t in computed:
                    tasks[int(t["id"][1:]) - 1] = t
                yield _sse({"type": "progress", "done": min(bs + concurrency, total), "total": total})

        catalog = [{"kind": c.get("kind", "human"), "name": c.get("name", ""), "rate": c.get("rate", 0)}
                   for c in cap_items if c.get("name")]
        plan_id = uuid.uuid4().hex[:12]
        plan = {
            "id": plan_id, "name": plan_name,
            "created_at": time.time(), "updated_at": time.time(),
            "tasks": tasks,
            "description": f"Aus Anfrage-Auswertung übernommen ({total} Tickets).",
            "system_prompt": "",
            "resource_catalog": catalog, "resource_mode": "extend",
            "start_date": time.strftime("%Y-%m-%d"), "end_date": "", "workdays": True,
        }
        _plan_path(plan_id, plan_name).write_text(
            json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
        yield _sse({"type": "done", "plan_id": plan_id, "plan_name": plan_name, "n": total,
                    "tokens": _tok})

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.get("/api/dossiers")
async def list_dossiers():
    """Listet die automatisch erzeugten Planer-Recherche-Dossiers (.md) auf –
    als wählbares Quellmaterial im Dokumentengenerator."""
    items = []
    if DOSSIERS_DIR.exists():
        for fp in sorted(DOSSIERS_DIR.rglob("*.md")):
            items.append({
                "id": fp.relative_to(DOSSIERS_DIR).as_posix(),
                "name": fp.stem.replace("_", " "),
                "plan": fp.parent.name.replace("_", " "),
            })
    return items


@app.get("/api/dossiers/load")
async def load_dossier(id: str = Query(...)):
    """Inhalt eines Dossiers (mit Pfad-Traversal-Schutz)."""
    target = (DOSSIERS_DIR / id).resolve()
    try:
        target.relative_to(DOSSIERS_DIR.resolve())
    except ValueError:
        raise HTTPException(400, "Ungültiger Pfad")
    if not target.exists() or target.suffix != ".md":
        raise HTTPException(404)
    return {"name": target.stem.replace("_", " "),
            "content": target.read_text(encoding="utf-8")}


# ── Mail (IMAP read-only) → Wissensdatenbank ────────────────────────────────
# Reine stdlib (tools/mail.py). Server/Port/Benutzer liegen in data/mail.json
# (NICHT im Backup, NICHT in git – siehe .gitignore). Das **Passwort wird NICHT
# gespeichert**: es lebt nur im Arbeitsspeicher dieses Prozesses (_MAIL_SESSION_PW)
# und muss pro Sitzung neu eingegeben werden.
MAIL_CONFIG_FILE = DATA_DIR / "mail.json"

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


@app.get("/api/mail/config")
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


@app.post("/api/mail/config")
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


@app.post("/api/mail/list")
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


@app.post("/api/mail/message")
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


@app.post("/api/mail/to-rag")
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


# ── Mail-Aktionen & Regeln (regelbasierte Verarbeitung, Versand stets manuell) ──
MAIL_RULES_FILE = DATA_DIR / "mail_rules.json"


def _load_mail_rules() -> list:
    if MAIL_RULES_FILE.exists():
        try:
            return json.loads(MAIL_RULES_FILE.read_text(encoding="utf-8")) or []
        except Exception:
            return []
    return []


@app.get("/api/mail/rules")
async def mail_rules_list():
    return {"rules": _load_mail_rules()}


@app.post("/api/mail/rules")
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


@app.delete("/api/mail/rules/{rid}")
async def mail_rules_delete(rid: str):
    rules = [r for r in _load_mail_rules() if r.get("id") != rid]
    MAIL_RULES_FILE.write_text(json.dumps(rules, ensure_ascii=False, indent=2),
                               encoding="utf-8")
    return {"ok": True}


@app.post("/api/mail/action/rag")
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


@app.post("/api/mail/action/agent")
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


@app.post("/api/presentation/from-text")
async def presentation_from_text(req: Request):
    """Wandelt fertigen Text (z. B. aus dem Dokumentengenerator) in eine
    Canvas-Präsentation um — ohne Konversation. Gibt das Canvas-JSON zurück,
    das der Frontend-Renderer direkt anzeigen kann."""
    body = await req.json()
    text = str(body.get("text", "")).strip()
    if not text:
        raise HTTPException(status_code=400, detail="Kein Text übergeben")
    model = _pick_model(body.get("model"))
    _tok = {"in": 0, "out": 0}
    data = await _text_to_presentation(text, model, tok=_tok)
    if not data:
        raise HTTPException(status_code=422,
                            detail="Konnte aus dem Text keine Folien ableiten")
    data["tokens"] = _tok
    return data


async def _slide_image_prompt(title: str, bullets: list, content: str,
                              model: str, style: str = "") -> tuple:
    """Leitet aus dem Folientext EINEN kompakten, visuellen Bild-Prompt ab
    (ein kurzer LLM-Aufruf; robuster deterministischer Rückfall). Der Prompt
    beschreibt eine *Szene/Illustration* zum Folienthema – KEIN Text-im-Bild,
    keine Aufzählung. Token-sparsam. Gibt ``(prompt, tok_in, tok_out)`` zurück.

    Geheim-/Hartman-Modus: ``model`` ist bereits lokal gecoerct (Aufrufer nutzt
    ``_model_for('general')``), daher rein lokal."""
    _txt = " · ".join([str(title or "")] +
                      [str(b) for b in (bullets or [])] +
                      ([str(content)] if content else [])).strip(" ·")
    _style = str(style or "").strip()
    # Deterministischer Rückfall (falls LLM scheitert / kein Modell): Thema + Stil.
    _fallback = (f"{title or _txt[:80]}, professionelle Illustration"
                 + (f", {_style}" if _style else ", moderner Business-Stil")
                 + ", hochwertig, keine Schrift, kein Text").strip()
    if not model or not _txt:
        return _fallback, 0, 0
    _sys = ("Du bist Prompt-Designer für ein Text-zu-Bild-Modell. Formuliere aus "
            "dem Folieninhalt EINEN einzigen, bildhaften englischen ODER deutschen "
            "Prompt (max. ~40 Wörter) für EIN illustratives Bild zur Folie: eine "
            "konkrete Szene/Metapher/Illustration, KEINE Aufzählung, KEIN Text im "
            "Bild. Nur den Prompt ausgeben, ohne Anführungszeichen."
            + (f" Stil: {_style}." if _style else ""))
    try:
        async with _model_session(model), httpx.AsyncClient(timeout=120) as client:
            resp = await _llm.chat(client, {
                "model": model, "think": False, "stream": False,
                "messages": [{"role": "system", "content": _sys},
                             {"role": "user", "content": _txt[:1200]}],
                "options": {"num_ctx": _profile_num_ctx(), "num_predict": 120},
                "keep_alive": KEEP_ALIVE,
            })
            resp.raise_for_status()
        _j = resp.json()
        _p = (_j.get("message", {}) or {}).get("content", "") or ""
        _p = re.sub(r"<think>.*?</think>", "", _p, flags=re.DOTALL).strip().strip('"').strip()
        _ti, _to = _llm_tok(_j)
        if _p and len(_p) > 8:
            if _style and _style.lower() not in _p.lower():
                _p = f"{_p}, {_style}"
            return _p[:600], _ti, _to
        return _fallback, _ti, _to
    except Exception:
        return _fallback, 0, 0


@app.post("/api/presentation/slide-image")
async def presentation_slide_image(req: Request):
    """Erzeugt EIN KI-Bild für eine Präsentationsfolie: leitet aus dem Folientext
    einen Bild-Prompt ab und ruft die konfigurierte Bildgenerierung (lokal SD-WebUI
    oder API-Bildmodell). Antwort: ``{image, prompt}`` (data-URI). Frontend setzt es
    als ``image_right`` + Layout ``two-column``. Geheim-/Hartman-Modus: nur lokal
    (siehe ``_generate_image_core``, sonst 409). Bild ≠ Token-Strom, aber die
    Prompt-Ableitung meldet Tokens im Feld ``tokens`` (Label „Präsentationsbild")."""
    body = await req.json()
    title = str(body.get("title", "") or "")
    bullets = body.get("bullets") or []
    if isinstance(bullets, str):
        bullets = [b for b in bullets.split("\n") if b.strip()]
    content = str(body.get("content", "") or "")
    preset = str(body.get("preset", "square") or "square")
    style = str(body.get("style", "") or "")
    given = str(body.get("prompt", "") or "").strip()

    _tok = {"in": 0, "out": 0}
    if given:
        prompt = given
    else:
        # Prompt-Ableitung mit lokal-gecoerctem Textmodell (Geheim/Hartman-fest).
        _pm = _pick_model(_model_for("general"))
        prompt, _ti, _to = await _slide_image_prompt(title, bullets, content, _pm, style)
        _tok["in"] += _ti
        _tok["out"] += _to

    result = await _generate_image_core(prompt, "", preset, str(body.get("model", "") or ""))
    result["prompt"] = prompt
    result["tokens"] = _tok
    return result


# ── Arbeitsablauf im Chat (mehrstufig, Zwischenergebnisse) ────────────────────
# Der Nutzer gibt nummerierte Schritte ein („1. … 2. …"). Jeder Schritt wird als
# fokussierte Teilaufgabe ausgeführt; die bisherigen Ergebnisse fließen als
# Kontext in den nächsten Schritt. Am Ende führt ein Synthese-Schritt alles zu
# einem Gesamtergebnis zusammen. Rein LLM-basiert (robust auch für kleinere
# Modelle, kein Werkzeug-Loop). Modellrolle „general", Geheim/Hartman → lokal.
# Pro Schritt kann eine Tag-Angabe ``[lokal]`` / ``[api]`` / ``[web]`` (Kombis wie
# ``[lokal,web]``) das Modell und die Websuche steuern. Das Frontend parst die Tags und
# schickt Schritte als Objekte ``{text, mode, web}``; zur Robustheit akzeptieren wir hier
# auch nackte Strings (Tag im Text) und normalisieren beides.
_WF_TAG_RE = re.compile(r"^\s*\[([^\]]{1,40})\]\s*(.*)$", re.DOTALL)


def _wf_normalize_step(s) -> dict:
    """Ein Schritt → ``{text, mode, web}`` (``mode`` ∈ '' / 'local' / 'api')."""
    if isinstance(s, dict):
        text = str(s.get("text", "") or "").strip()
        mode = str(s.get("mode", "") or "").strip().lower()
        web = bool(s.get("web", False))
    else:
        text, mode, web = str(s or "").strip(), "", False
    m = _WF_TAG_RE.match(text)
    if m:  # Tag im Text (Fallback, falls das Frontend nicht geparst hat)
        toks = re.split(r"[,\s/+]+", m.group(1).lower())
        text = m.group(2).strip()
        for t in toks:
            if t in ("lokal", "local"):
                mode = "local"
            elif t in ("api", "remote", "cloud"):
                mode = "api"
            elif t in ("web", "recherche", "suche", "search", "internet"):
                web = True
    if mode not in ("local", "api"):
        mode = ""
    return {"text": text, "mode": mode, "web": web}


async def _workflow_generator(body: dict):
    steps = [_wf_normalize_step(s) for s in (body.get("steps") or [])]
    steps = [s for s in steps if s["text"]][:20]
    goal = str(body.get("goal", "") or "").strip()
    if not steps:
        yield _sse({"type": "error", "message": "Keine Schritte angegeben."})
        return
    base_model = _pick_model(body.get("model"), _model_for("general"))
    # API-Modell für ``[api]``-Schritte: nur ein echtes Remote-Modell und nur außerhalb
    # des Geheim-/Hartman-Modus (der alles lokal erzwingt).
    _api_raw = str(body.get("api_model", "") or "").strip()
    api_model = (_api_raw if (_api_raw and _api_raw not in _MODEL_PLACEHOLDERS
                              and _llm.is_remote(_api_raw) and not _secret_local()) else "")
    local_model = await _local_model(base_model)  # None, wenn kein lokales LLM da ist
    _ctx = _profile_num_ctx()
    _tok = {"in": 0, "out": 0}
    results = []  # [(step_text, result)]
    # Zeichenbudget für den mitgeführten Kontext (an num_ctx gekoppelt).
    _budget = max(2000, int((_ctx - 800) * 3.0))

    def _resolve_model(mode: str):
        """(Modell, Hinweis|None) für einen Schritt-Modus."""
        if mode == "local":
            if local_model:
                return local_model, None
            return base_model, "kein lokales Modell installiert – Standardmodell genutzt"
        if mode == "api":
            if api_model:
                return api_model, None
            if _secret_local():
                return (local_model or base_model), "Geheim-/Hartman-Modus: lokal statt API"
            return base_model, "kein API-Modell gewählt – Standardmodell genutzt"
        return base_model, None

    yield _sse({"type": "workflow_start", "count": len(steps)})

    async def _run(model: str, sys_prompt: str, user_prompt: str, num_predict: int):
        async with _model_session(model), httpx.AsyncClient(timeout=600) as client:
            resp = await _llm.chat(client, {
                "model": model, "think": False, "stream": False,
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
        _tok["in"] += _ti
        _tok["out"] += _to
        return _c

    try:
        for i, step in enumerate(steps):
            _txt = step["text"]
            _model, _note = _resolve_model(step["mode"])
            yield _sse({"type": "step_start", "index": i, "total": len(steps),
                        "step": _txt, "model": _model,
                        "remote": _llm.is_remote(_model), "web": step["web"]})
            if _note:
                yield _sse({"type": "notice", "index": i, "message": _note})

            # Optionale Websuche für diesen Schritt (typisch: lokales Recherche-Modell
            # holt Quellen, die dann als Zwischenergebnis an ein API-Modell weitergehen).
            _web_ctx = ""
            if step["web"]:
                if _web_search_allowed():
                    yield _sse({"type": "searching", "index": i, "query": _txt[:80]})
                    try:
                        _srcs, _stext = await search_with_sources(_txt[:200], 5)
                    except Exception as _e:
                        _srcs, _stext = [], f"Suchfehler: {_e}"
                    if _stext:
                        _web_ctx = _stext[:min(_budget, 6000)]
                    yield _sse({"type": "search_done", "index": i,
                                "count": len(_srcs or [])})
                else:
                    yield _sse({"type": "notice", "index": i,
                                "message": "Websuche im Hartman-Modus gesperrt – ohne Quellen"})

            prior = ""
            if results:
                _parts = [f"### Ergebnis Schritt {si + 1} ({s}):\n{r}" for si, (s, r) in enumerate(results)]
                prior = "\n\n".join(_parts)
                if len(prior) > _budget:
                    prior = "…\n" + prior[-_budget:]
            _sys = ("Du arbeitest einen mehrstufigen Arbeitsablauf ab. Löse NUR den "
                    "AKTUELLEN Schritt präzise und vollständig und baue dabei auf den "
                    "bisherigen Ergebnissen auf. Antworte fokussiert auf Deutsch in Markdown, "
                    "ohne den Schritt bloß zu wiederholen.")
            if _web_ctx:
                _sys += ("\n\nDir liegen Web-Suchergebnisse vor. Stütze konkrete Angaben "
                         "(Zahlen, Daten, Namen, Preise) NUR auf diese Quellen; ist etwas "
                         "nicht belegt, kennzeichne es als unsicher und erfinde nichts.")
            if goal:
                _sys += f"\n\nÜbergeordnetes Ziel des Ablaufs: {goal}"
            _user = ((f"Bisherige Ergebnisse:\n{prior}\n\n---\n" if prior else "")
                     + (f"Web-Suchergebnisse:\n{_web_ctx}\n\n---\n" if _web_ctx else "")
                     + f"AKTUELLER SCHRITT {i + 1}/{len(steps)}: {_txt}")
            _res = await _run(_model, _sys, _user, max(300, min(int(_ctx * 0.35), 1500)))
            results.append((_txt, _res))
            yield _sse({"type": "step_done", "index": i, "step": _txt, "result": _res})

        # Abschluss-Synthese: bevorzugt das API-Modell (größeres Kontextfenster für die
        # gesammelten Teilergebnisse), sonst das Basismodell.
        _synth_model = api_model or base_model
        yield _sse({"type": "synthesizing", "model": _synth_model,
                    "remote": _llm.is_remote(_synth_model)})
        _all = "\n\n".join(f"### Schritt {i + 1}: {s}\n{r}" for i, (s, r) in enumerate(results))
        if len(_all) > _budget:
            _all = "…\n" + _all[-_budget:]
        _ssys = ("Du fasst die Ergebnisse eines mehrstufigen Arbeitsablaufs zu EINEM "
                 "zusammenhängenden, gut strukturierten Gesamtergebnis zusammen (Markdown: "
                 "## Überschriften, **Fett**, Aufzählungen/Tabellen wo sinnvoll). Führe die "
                 "Teilergebnisse logisch zusammen, wiederhole nicht stumpf, sondern liefere ein "
                 "kohärentes Endprodukt und schließe mit einem klaren Fazit ab.")
        if goal:
            _ssys += f"\n\nZiel des Ablaufs: {goal}"
        _suser = f"Schritt-Ergebnisse:\n{_all}\n\n---\nErstelle das zusammenhängende Gesamtergebnis."
        _final = await _run(_synth_model, _ssys, _suser, max(500, min(int(_ctx * 0.5), 2200)))
    except httpx.ConnectError:
        yield _sse({"type": "error", "message": "Ollama nicht erreichbar – läuft der lokale Server?"})
        return
    except httpx.HTTPStatusError as e:
        _sc = getattr(e.response, "status_code", 0) or 0
        if _sc in (502, 503, 504):
            _m = f"Der Anbieter hat nicht rechtzeitig geantwortet (HTTP {_sc}). Bitte weniger/kürzere Schritte oder ein lokales Modell."
        else:
            _m = f"Modell abgelehnt (num_ctx/VRAM?): HTTP {_sc}"
        yield _sse({"type": "error", "message": _m})
        return
    except Exception as e:
        yield _sse({"type": "error", "message": f"Arbeitsablauf fehlgeschlagen: {e}"})
        return

    for _i, _w in enumerate(_final.split(" ")):
        yield _sse({"type": "text", "content": _w + (" " if _i < len(_final.split(' ')) - 1 else "")})
        await asyncio.sleep(0.003)
    yield _sse({"type": "done", "tokens": _tok,
                "results": [{"step": s, "result": r} for s, r in results]})


@app.post("/api/workflow")
async def workflow(req: Request):
    """Führt einen mehrstufigen Arbeitsablauf aus (SSE). Body: ``{steps:[…], goal?,
    model?, api_model?}``; ``steps`` sind Strings ODER Objekte ``{text, mode, web}``
    (``mode`` '' / 'local' / 'api', ``web`` = Websuche für den Schritt). Pro Schritt
    wählbares Modell (lokal recherchiert/zwischenspeichert → API-Modell verarbeitet weiter),
    die Synthese läuft bevorzugt auf dem API-Modell. Streamt ``workflow_start``/
    ``step_start``/``searching``/``search_done``/``notice``/``step_done``/
    ``synthesizing``/``text``/``done``/``error``. Token-Label „Arbeitsablauf"."""
    body = await req.json()
    return StreamingResponse(_workflow_generator(body), media_type="text/event-stream")


@app.get("/api/downloads/{filename}")
async def download_report(filename: str):
    # only alphanumeric + dot + dash to prevent path traversal
    import re
    if not re.match(r'^[a-zA-Z0-9._-]+$', filename):
        raise HTTPException(400, "Ungültiger Dateiname")
    fp = REPORTS_DIR / filename
    if not fp.exists():
        raise HTTPException(404, "Datei nicht gefunden")
    media_types = {
        ".pdf": "application/pdf",
        ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    }
    mt = media_types.get(fp.suffix.lower(), "application/octet-stream")
    return FileResponse(fp, filename=filename, media_type=mt)


# ── Agenten-API ───────────────────────────────────────────────────────────────


@app.get("/api/agents")
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


@app.post("/api/agents/generate-prompt")
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


@app.post("/api/derive-persona")
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


def _slide_fields_from_partial(raw: str):
    """Bergungs-Parser für (evtl. ABGESCHNITTENES) Slide-JSON: zieht title, bullets
    und caption per Regex heraus, auch wenn das JSON nie geschlossen wurde. So landet
    bei trunkierter Modellantwort kein roher ``{"title":…``-Text auf der Folie."""
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
            seg = seg[:end]            # nur bis zum schließenden ] (falls vorhanden)
        # Vollständig in Anführungszeichen stehende Strings — ein abgeschnittener
        # (nicht geschlossener) letzter Stichpunkt wird so automatisch übersprungen.
        bullets = re.findall(r'"((?:[^"\\]|\\.)*)"', seg)
    caption = ""
    mc = re.search(r'"caption"\s*:\s*"((?:[^"\\]|\\.)*)"', raw)
    if mc:
        caption = mc.group(1)
    return title, bullets, caption


@app.post("/api/analyze-image")
async def analyze_image(req: Request):
    """Analysiert ein einzelnes Bild mit einem Vision-Modell und liefert
    strukturierten Folieninhalt (Titel, Stichpunkte, Bildunterschrift)."""
    import re
    from tools.imaging import downscale, is_descriptive_filename

    body = await req.json()
    image_b64 = body.get("image") or ""
    if not image_b64:
        raise HTTPException(400, "Kein Bild übergeben")

    system_prompt = (body.get("system_prompt") or "").strip() or (
        "Du bist ein technischer Fach-Experte und beschreibst das gezeigte Bild "
        "knapp und sachlich auf Deutsch."
    )
    filename = (body.get("filename") or "").strip()
    topic = (body.get("topic") or "").strip()
    _model = _pick_model(body.get("model"))

    descriptive, label = is_descriptive_filename(filename) if filename else (False, "")
    small = downscale(image_b64)

    name_hint = ""
    if filename:
        if descriptive:
            name_hint = (
                f"\nDer Dateiname '{label}' ist beschreibend – nutze ihn als Hinweis "
                f"auf den Bildinhalt und möglichst als Folientitel."
            )
        else:
            name_hint = (
                f"\nDer Dateiname ('{filename}') ist nicht aussagekräftig – ignoriere ihn "
                f"und stütze dich allein auf das, was im Bild zu sehen ist."
            )

    user_text = (
        (f"Kontext der Präsentation: {topic}\n" if topic else "")
        + "Beschreibe dieses Bild für eine Präsentationsfolie."
        + name_hint
        + "\n\nAntworte NUR mit JSON in genau diesem Format, ohne weiteren Text:\n"
        '{"title":"Kurzer Folientitel","bullets":["Stichpunkt 1","Stichpunkt 2","Stichpunkt 3"],'
        '"caption":"Eine kurze Bildunterschrift (max. ein Satz)"}\n'
        "Maximal 3 kurze Stichpunkte (je höchstens ein knapper Satz). "
        "Kein Markdown, keine Sternchen, keine Aufzählungszeichen im Text."
    )

    async with _model_session(_model), httpx.AsyncClient(timeout=180) as client:
        resp = await _llm.chat(client,{
            "model": _model,
            "think": False,
            # format:"json" + ausreichendes Kontextfenster verhindern Vorgeplapper und
            # abgeschnittene Antworten (sonst landet roher JSON-Text auf der Folie).
            "format": "json",
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_text, "images": [small]},
            ],
            "options": {"num_ctx": _profile_num_ctx()},
            "stream": False,
        })
        resp.raise_for_status()
        _ai_j = resp.json()
        _ai_ti, _ai_to = _llm_tok(_ai_j)
        raw = _ai_j.get("message", {}).get("content", "")

    raw = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL).strip()

    def _strip_md(s: str) -> str:
        # Markdown-Reste entfernen, die das Canvas sonst literal zeichnet
        s = re.sub(r"[*_`#>]+", "", s)
        s = re.sub(r"^\s*[-•]\s*", "", s)
        return re.sub(r"\s+", " ", s).strip()

    title, bullets, caption = (label if descriptive else "Abbildung"), [], ""
    data = _parse_llm_json(raw)
    if isinstance(data, dict):
        title = _strip_md(str(data.get("title") or "")) or title
        b = data.get("bullets") or []
        bullets = [_strip_md(str(x)) for x in b if str(x).strip()][:3]
        caption = _strip_md(str(data.get("caption") or ""))
    if not bullets and not caption:
        # Bergung aus (evtl. abgeschnittenem) JSON — kein roher JSON-Müll auf der Folie.
        st, sb, sc = _slide_fields_from_partial(raw)
        if st:
            title = _strip_md(st) or title
        bullets = [_strip_md(str(x)) for x in sb if str(x).strip()][:3]
        caption = _strip_md(sc)
    if not bullets and not caption:
        # Letzter Fallback: nur echten Fließtext zeigen, niemals JSON-Fragmente.
        plain = raw.strip()
        looks_json = plain.startswith("{") or '"bullets"' in plain or '"title"' in plain
        caption = "" if looks_json else _strip_md(plain)[:200]

    return {
        "title": title,
        "bullets": bullets,
        "caption": caption,
        "descriptive_filename": descriptive,
        "tokens": {"in": _ai_ti, "out": _ai_to},
    }


@app.post("/api/illus/intro")
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


@app.post("/api/agents")
async def create_agent(agent: AgentDef):
    if not agent.id:
        agent.id = _to_slug(agent.name or "agent") + "_" + uuid.uuid4().hex[:4]
    fp = _unique_agent_path(agent.name or agent.id, exclude_id=agent.id)
    fp.write_text(json.dumps(agent.model_dump(), ensure_ascii=False, indent=2), encoding="utf-8")
    return agent


@app.put("/api/agents/{aid}")
async def update_agent(aid: str, agent: AgentDef):
    agent.id = aid
    # Alte Datei finden und ggf. umbenennen
    old_fp = _agent_path_by_id(aid)
    new_fp = _unique_agent_path(agent.name or aid, exclude_id=aid)
    if old_fp and old_fp != new_fp and old_fp.exists():
        old_fp.unlink(missing_ok=True)
    new_fp.write_text(json.dumps(agent.model_dump(), ensure_ascii=False, indent=2), encoding="utf-8")
    return agent


@app.delete("/api/agents/{aid}")
async def delete_agent(aid: str):
    fp = _agent_path_by_id(aid)
    if fp:
        fp.unlink(missing_ok=True)
    return {"ok": True}


class AgentMergeReq(BaseModel):
    ids: List[str]
    model: Optional[str] = None
    name: Optional[str] = None


@app.post("/api/agents/merge")
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


@app.post("/api/agents/from-legal")
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


# ── Jury (gespeicherte Bewertungs-Gremien aus Agenten) ──────────────────────────
# Eine Jury bündelt mehrere Agenten (z. B. ⚖️ Gesetz-Agenten). Sie bewertet einen
# beliebigen Text — auch KI-generierten — mit je einem Votum pro Mitglied plus einem
# synthetisierten Gesamturteil. Dateibasiert wie Agenten/Pläne (data/juries/).

def _jury_path_by_id(jid: str) -> Optional[Path]:
    for fp in JURIES_DIR.glob("*.json"):
        try:
            if json.loads(fp.read_text(encoding="utf-8")).get("id") == jid:
                return fp
        except Exception:
            pass
    return None


def _load_agent_dict(aid: str) -> Optional[dict]:
    fp = _agent_path_by_id(aid)
    if fp and fp.exists():
        try:
            return json.loads(fp.read_text(encoding="utf-8"))
        except Exception:
            return None
    return None


@app.get("/api/juries")
async def list_juries():
    out = []
    for fp in sorted(JURIES_DIR.glob("*.json")):
        try:
            out.append(json.loads(fp.read_text(encoding="utf-8")))
        except Exception:
            pass
    return out


@app.post("/api/juries")
async def create_jury(req: Request):
    body = await req.json()
    name = (body.get("name") or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="Name fehlt")
    members = [str(m) for m in (body.get("member_agent_ids") or [])]
    jury = {
        "id": _to_slug(name) + "_" + uuid.uuid4().hex[:6],
        "name": name,
        "description": (body.get("description") or "").strip(),
        "member_agent_ids": members,
        "project_id": (body.get("project_id") or "").strip(),
        "created_at": time.time(),
    }
    fp = JURIES_DIR / f"{_to_slug(name)}_{jury['id'][-6:]}.json"
    fp.write_text(json.dumps(jury, ensure_ascii=False, indent=2), encoding="utf-8")
    return jury


@app.put("/api/juries/{jid}")
async def update_jury(jid: str, req: Request):
    fp = _jury_path_by_id(jid)
    if not fp:
        raise HTTPException(status_code=404, detail="Jury nicht gefunden")
    jury = json.loads(fp.read_text(encoding="utf-8"))
    body = await req.json()
    if "name" in body:
        jury["name"] = (body.get("name") or jury["name"]).strip()
    if "description" in body:
        jury["description"] = (body.get("description") or "").strip()
    if "member_agent_ids" in body:
        jury["member_agent_ids"] = [str(m) for m in (body.get("member_agent_ids") or [])]
    fp.write_text(json.dumps(jury, ensure_ascii=False, indent=2), encoding="utf-8")
    return jury


@app.delete("/api/juries/{jid}")
async def delete_jury(jid: str):
    fp = _jury_path_by_id(jid)
    if fp:
        fp.unlink()
    return {"ok": True}


_JURY_MEMBER_SYSTEM = (
    "Du bewertest einen vorgelegten Text aus deiner Fachperspektive (siehe deine Rolle). "
    "Sei konkret und belege Kritik. Wenn dir Fachgrundlagen (Auszüge aus Gesetzen/Normen/"
    "Wissensdatenbank) eingeblendet sind, prüfe ausschließlich anhand dieser und nenne die "
    "Fundstelle (§/Artikel/Quelle). Erfinde nichts. Antworte NUR mit JSON in genau diesem "
    'Format: {"score":0-100,"befund":"kurzer Gesamtbefund","risiken":["Verstoß/Risiko mit '
    'Fundstelle", "..."],"empfehlung":"konkrete Empfehlung"}'
)

_JURY_SYNTH_SYSTEM = (
    "Du fasst die Einzelvoten einer Bewertungs-Jury zu einem Gesamturteil zusammen. "
    "Gewichte fachlich, hebe Konsens und Streitpunkte hervor. Antworte NUR mit JSON: "
    '{"gesamturteil":"…","score":0-100,"konsens":"…","hauptkritik":["…"],'
    '"empfehlungen":["…"]}'
)

# Map-Schritt fuer sehr lange Dokumente: pro Abschnitt eine kurze Vorab-Analyse.
_JURY_CHUNK_SYSTEM = (
    "Du erhältst EINEN Abschnitt eines längeren Dokuments. Notiere aus deiner "
    "Fachperspektive die wichtigsten Befunde, Risiken/Verstöße (mit Fundstelle, falls "
    "Fachgrundlagen eingeblendet sind) und auffälligen Punkte NUR für diesen Abschnitt. "
    "Maximal 100 Wörter, Stichpunkte. Kein JSON, keine Gesamtwertung."
)


def _chunk_for_ctx(text: str, num_ctx: int, max_chunks: int = 40) -> list:
    """Teilt Text in Abschnitte, die mit Prompt + Ausgabe ins Kontextfenster passen.
    ~3,5 Zeichen/Token (DE), ~50 % des Fensters für den Textabschnitt. Begrenzt die
    Abschnittszahl (notfalls größere Abschnitte), um die Kosten zu deckeln."""
    per = max(4000, int(num_ctx * 3.5 * 0.5))
    need = (len(text) + per - 1) // per
    if need > max_chunks:
        per = (len(text) + max_chunks - 1) // max_chunks
    chunks, i, n = [], 0, len(text)
    while i < n:
        end = min(i + per, n)
        if end < n:  # möglichst an Absatz-/Satzgrenze trennen
            br = text.rfind("\n", i + per // 2, end)
            if br <= i:
                br = text.rfind(". ", i + per // 2, end)
                if br > i:
                    br += 1
            if br > i:
                end = br
        seg = text[i:end].strip()
        if seg:
            chunks.append(seg)
        i = end
    return chunks


@app.post("/api/jury/evaluate")
async def jury_evaluate(req: Request):
    """Bewertet einen Text mit allen Mitgliedern einer Jury (SSE-Stream).
    Body: {jury_id | member_agent_ids[], text, context?, criteria?}.
    Frames: member (pro Votum), summary (Gesamturteil), error, done."""
    body = await req.json()
    text = (body.get("text") or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="Kein Text zum Bewerten")
    context = (body.get("context") or "").strip()
    criteria = (body.get("criteria") or "").strip()

    member_ids = body.get("member_agent_ids") or []
    if not member_ids and body.get("jury_id"):
        jfp = _jury_path_by_id(body["jury_id"])
        if jfp:
            try:
                member_ids = json.loads(jfp.read_text(encoding="utf-8")).get("member_agent_ids", [])
            except Exception:
                member_ids = []
    member_ids = [str(m) for m in member_ids]
    if not member_ids:
        raise HTTPException(status_code=400, detail="Jury hat keine Mitglieder")

    async def _stream():
        verdicts = []
        tok_total = {"in": 0, "out": 0}
        for aid in member_ids:
            agent = _load_agent_dict(aid)
            if not agent:
                continue
            aname = agent.get("name", aid)
            aicon = agent.get("icon", "⚖️")
            yield _sse({"type": "member", "status": "start", "agent": aname, "icon": aicon})

            # Fachgrundlagen aus gebundenen Wissensdatenbanken (z. B. Gesetzestext) ziehen
            rag_ctx = ""
            rag_ids = agent.get("rag_collections") or []
            if rag_ids:
                try:
                    from tools.rag import query_collections
                    hits = await query_collections(rag_ids, text[:2000])
                    if hits:
                        rag_ctx = "\n\n".join(
                            f"[Quelle {i+1}: {h.get('filename','')}]\n{h.get('text','')}"
                            for i, h in enumerate(hits[:6]))
                except Exception:
                    rag_ctx = ""

            sys_prompt = (agent.get("system_prompt") or "").strip()
            member_sys = (sys_prompt + "\n\n" + _JURY_MEMBER_SYSTEM) if sys_prompt else _JURY_MEMBER_SYSTEM
            base_parts = []
            if context:
                base_parts.append(f"Kontext:\n{context}")
            if criteria:
                base_parts.append(f"Bewertungskriterien:\n{criteria}")
            if rag_ctx:
                base_parts.append(f"Eingeblendete Fachgrundlagen:\n{rag_ctx[:6000]}")

            mdl = _pick_model(agent.get("model"), _model_for("science"))
            num_ctx = _profile_num_ctx()
            # Passt das Dokument in einen Direktdurchlauf? Sonst Map-Reduce über Abschnitte.
            single_max = max(4000, int(num_ctx * 3.5 * 0.5))

            async def _member_call(client, sysmsg, usermsg, as_json):
                payload = {"model": mdl, "think": False, "stream": False,
                           "messages": [{"role": "system", "content": sysmsg},
                                        {"role": "user", "content": usermsg}],
                           "options": {"num_ctx": num_ctx}, "keep_alive": KEEP_ALIVE}
                if as_json:
                    payload["format"] = "json"
                resp = await _llm.chat(client, payload)
                resp.raise_for_status()
                j = resp.json()
                ti, to = _llm_tok(j)
                return j.get("message", {}).get("content", ""), ti, to

            data = None
            notes = None   # Abschnitts-Befunde (nur im Map-Reduce-Pfad gesetzt)
            try:
                async with _model_session(mdl), httpx.AsyncClient(timeout=300) as client:
                    if len(text) <= single_max:
                        up = "\n\n".join(base_parts + [f"Zu bewertender Text:\n{text}"])
                        content, ti, to = await _member_call(client, member_sys, up, True)
                        tok_total["in"] += ti; tok_total["out"] += to
                        data = _parse_llm_json(content)
                    else:
                        # Map: jeden Abschnitt vorab analysieren
                        chunks = _chunk_for_ctx(text, num_ctx)
                        chunk_sys = (sys_prompt + "\n\n" + _JURY_CHUNK_SYSTEM) if sys_prompt else _JURY_CHUNK_SYSTEM
                        notes = []
                        for ci, ch in enumerate(chunks):
                            yield _sse({"type": "member", "status": "progress", "agent": aname,
                                        "icon": aicon, "chunk": ci + 1, "chunks": len(chunks)})
                            up = "\n\n".join(base_parts + [f"Dokument-Abschnitt {ci+1}/{len(chunks)}:\n{ch}"])
                            try:
                                content, ti, to = await _member_call(client, chunk_sys, up, False)
                                tok_total["in"] += ti; tok_total["out"] += to
                                if content.strip():
                                    notes.append(f"[Abschnitt {ci+1}] {content.strip()}")
                            except Exception:
                                pass
                        # Reduce: Gesamtvotum aus den Abschnitts-Befunden
                        joined = "\n\n".join(notes)[:int(num_ctx * 3.0)]
                        up = "\n\n".join(base_parts + [
                            f"Das Dokument ist sehr lang und wurde abschnittsweise vorab-analysiert "
                            f"({len(chunks)} Abschnitte). Abschnitts-Befunde:\n{joined}\n\n"
                            "Erstelle daraus dein abschließendes Gesamtvotum zum gesamten Dokument."])
                        content, ti, to = await _member_call(client, member_sys, up, True)
                        tok_total["in"] += ti; tok_total["out"] += to
                        data = _parse_llm_json(content)
            except Exception as e:
                yield _sse({"type": "member", "status": "error", "agent": aname,
                            "icon": aicon, "message": str(e)})
                continue

            verdict = {
                "agent": aname, "icon": aicon,
                "score": (data or {}).get("score"),
                "befund": ((data or {}).get("befund") or "").strip(),
                "risiken": [str(r) for r in ((data or {}).get("risiken") or [])],
                "empfehlung": ((data or {}).get("empfehlung") or "").strip(),
            }
            # Fallback: lieferte die (Reduce-)Wertung kein verwertbares JSON, aber es gibt
            # Abschnitts-Befunde, dann diese als Befund/Risiken zeigen (statt leerer Karte).
            if not verdict["befund"] and not verdict["risiken"] and notes:
                verdict["befund"] = ("Automatische Gesamtwertung war unsicher — "
                                     "abschnittsweise Befunde des großen Dokuments:")
                verdict["risiken"] = notes[:12]
            verdicts.append(verdict)
            yield _sse({"type": "member", "status": "done", **verdict})

        if not verdicts:
            yield _sse({"type": "error", "message": "Kein Mitglied lieferte ein Votum."})
            yield _sse({"type": "done"})
            return

        # Synthese / Gesamturteil
        votes_txt = "\n\n".join(
            f"## {v['agent']} (Score {v['score']})\nBefund: {v['befund']}\n"
            f"Risiken: {'; '.join(v['risiken'])}\nEmpfehlung: {v['empfehlung']}"
            for v in verdicts)
        gmodel = _model_for("general")
        try:
            async with _model_session(gmodel), httpx.AsyncClient(timeout=180) as client:
                resp = await _llm.chat(client, {
                    "model": gmodel, "think": False, "stream": False, "format": "json",
                    "options": {"num_ctx": _profile_num_ctx()}, "keep_alive": KEEP_ALIVE,
                    "messages": [
                        {"role": "system", "content": _JURY_SYNTH_SYSTEM},
                        {"role": "user", "content": f"Einzelvoten der Jury:\n\n{votes_txt}"},
                    ],
                })
                resp.raise_for_status()
                _sj = resp.json()
                _sti, _sto = _llm_tok(_sj)
                tok_total["in"] += _sti; tok_total["out"] += _sto
                synth = _parse_llm_json(_sj.get("message", {}).get("content", "")) or {}
        except Exception:
            synth = {}
        # Fallback-Gesamtscore: Mittelwert der Einzel-Scores
        scores = [v["score"] for v in verdicts if isinstance(v["score"], (int, float))]
        avg = round(sum(scores) / len(scores)) if scores else None
        yield _sse({"type": "summary",
                    "gesamturteil": (synth.get("gesamturteil") or "").strip(),
                    "score": synth.get("score", avg),
                    "konsens": (synth.get("konsens") or "").strip(),
                    "hauptkritik": [str(x) for x in (synth.get("hauptkritik") or [])],
                    "empfehlungen": [str(x) for x in (synth.get("empfehlungen") or [])]})
        yield _sse({"type": "done", "tokens": tok_total})

    return StreamingResponse(_stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ── Verzeichnis-Analyse ─────────────────────────────────────────────────────────
# Liest einen lokalen Ordner (Server-Pfad), gibt einen Überblick + interessante
# Dateien zurück, analysiert einzelne Dateien vertieft und schreibt eine
# Index-/„init"-Datei (_KI_INDEX.md) zurück in den Ordner. Personenbezogene Daten
# in den DATEIINHALTEN werden anonymisiert (Datei-/Ordnernamen bleiben sichtbar).
# Da beliebige Server-Pfade gelesen/geschrieben werden, ist der Tab optional und
# per Default ausgeblendet — im Mehrnutzer-/Servermodus nicht freischalten.

_DIR_MAX_FILES = 2000           # Obergrenze gescannter Dateien
_DIR_MAX_DEPTH = 8              # maximale Verschachtelungstiefe
_DIR_SNIPPET_FILES = 40         # so viele Dateien liefern einen Inhalts-Snippet
_DIR_SNIPPET_CHARS = 800        # Snippet-Länge je Datei (vor Anonymisierung)
_DIR_FILE_MAX_BYTES = 5_000_000 # Dateien größer als das werden nicht eingelesen
_DIR_SKIP_DIRS = {".git", "node_modules", "__pycache__", "venv", ".venv",
                  ".idea", ".vscode", "dist", "build", ".cache"}
_DIR_TEXT_EXT = {".pdf", ".docx", ".doc", ".xlsx", ".xls", ".csv", ".txt",
                 ".md", ".py", ".js", ".json", ".yaml", ".yml", ".html", ".css"}


def _dir_resolve_base(path: str) -> Path:
    """Validiert einen Server-Pfad und gibt das aufgelöste Verzeichnis zurück."""
    raw = (path or "").strip()
    if not raw:
        raise HTTPException(status_code=400, detail="Pfad fehlt")
    try:
        base = Path(raw).expanduser().resolve()
    except Exception:
        raise HTTPException(status_code=400, detail="Ungültiger Pfad")
    if not base.exists():
        raise HTTPException(status_code=404, detail="Verzeichnis nicht gefunden")
    if not base.is_dir():
        raise HTTPException(status_code=400, detail="Pfad ist kein Verzeichnis")
    return base


def _dir_safe_child(base: Path, rel: str) -> Path:
    """Pfad-Traversal-Schutz: stellt sicher, dass rel innerhalb von base liegt
    (Muster wie /api/dossiers/load)."""
    target = (base / rel).resolve()
    try:
        target.relative_to(base)
    except ValueError:
        raise HTTPException(status_code=400, detail="Ungültiger Pfad")
    if not target.is_file():
        raise HTTPException(status_code=404, detail="Datei nicht gefunden")
    return target


def _dir_walk(base: Path):
    """Bounded rekursiver Walk. Liefert eine Liste von
    {rel, name, size, ext, is_dir} und überspringt versteckte/ausgeschlossene
    Verzeichnisse. Fängt PermissionError ab."""
    files: List[dict] = []
    count = 0

    def _recurse(d: Path, depth: int):
        nonlocal count
        if depth > _DIR_MAX_DEPTH or count >= _DIR_MAX_FILES:
            return
        try:
            entries = sorted(d.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
        except (PermissionError, OSError):
            return
        for p in entries:
            if count >= _DIR_MAX_FILES:
                return
            name = p.name
            if name.startswith("."):
                continue
            try:
                rel = str(p.relative_to(base))
            except ValueError:
                continue
            if p.is_dir():
                if name in _DIR_SKIP_DIRS:
                    continue
                files.append({"rel": rel, "name": name, "size": 0,
                              "ext": "", "is_dir": True})
                count += 1
                _recurse(p, depth + 1)
            elif p.is_file():
                try:
                    size = p.stat().st_size
                except OSError:
                    size = 0
                files.append({"rel": rel, "name": name, "size": size,
                              "ext": p.suffix.lower(), "is_dir": False})
                count += 1

    _recurse(base, 0)
    return files


async def _llm_ner_names(text: str, model: str, tok: Optional[dict] = None) -> List[str]:
    """Optionaler LLM-NER-Pass: liefert eine Liste zu schwärzender Personennamen.
    Best effort — bei jedem Fehler leere Liste.
    ``tok`` (optional): dict {"in","out"}, in das der Tokenverbrauch summiert wird."""
    snippet = (text or "")[:4000]
    if not snippet.strip():
        return []
    try:
        async with _model_session(model), httpx.AsyncClient(timeout=120) as client:
            resp = await _llm.chat(client,{
                "model": model,
                "think": False,
                "stream": False,
                "format": "json",
                "messages": [
                    {"role": "system", "content": (
                        "Extrahiere ALLE vorkommenden Personennamen (Vor- und/oder "
                        "Nachnamen echter Menschen) aus dem Text. Keine Firmen, Orte, "
                        "Produkte. Antworte NUR mit JSON: {\"namen\":[\"…\"]}")},
                    {"role": "user", "content": snippet},
                ],
            })
            resp.raise_for_status()
            _ner_j = resp.json()
            if tok is not None:
                _a, _b = _llm_tok(_ner_j)
                tok["in"] += _a
                tok["out"] += _b
            raw = _ner_j.get("message", {}).get("content", "")
    except Exception:
        return []
    raw = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL)
    m = re.search(r"\{[\s\S]*\}", raw)
    if not m:
        return []
    try:
        data = json.loads(m.group(0))
        names = data.get("namen") or data.get("names") or []
        return [str(n) for n in names if isinstance(n, (str,)) and n.strip()]
    except Exception:
        return []


async def _anonymize(text: str, mapping: dict, model: str, use_llm: bool,
                     tok: Optional[dict] = None):
    """Anonymisiert Text deterministisch (regex) und optional per LLM-NER."""
    from tools.anonymize import redact_pii, redact_names
    clean, mapping = redact_pii(text, mapping)
    if use_llm:
        names = await _llm_ner_names(text, model, tok=tok)
        if names:
            clean, mapping = redact_names(clean, names, mapping)
    return clean, mapping


@app.post("/api/dir/scan")
async def dir_scan(req: Request):
    """Erster Scan: Struktur + Inhalts-Snippets (anonymisiert) → KI-Überblick
    mit Markierung interessanter Dateien."""
    body = await req.json()
    base = _dir_resolve_base(body.get("path", ""))
    anonymize = True   # Anonymisierung von Personendaten ist PFLICHT (nicht abschaltbar)
    use_llm_ner = bool(body.get("llm_ner", False))   # zusätzlicher NER-Pass (langsamer)
    # Verzeichnis-Analyse läuft standardmäßig AUSSCHLIESSLICH lokal (Datenschutz).
    # Nur mit Profil-Schalter „API-Modelle für vertrauliche Auswertungen“ UND
    # explizit gewähltem Remote-Modell geht sie an einen externen Anbieter.
    model = await _analysis_model(body.get("model"))
    if not model:
        raise HTTPException(status_code=503, detail="Kein lokales LLM verfügbar – die Verzeichnis-Analyse benötigt ein lokales Modell (Ollama). Alternativ im Profil „API-Modelle für vertrauliche Auswertungen“ aktivieren und ein API-Modell wählen.")

    files = _dir_walk(base)
    text_files = [f for f in files
                  if not f["is_dir"] and f["ext"] in _DIR_TEXT_EXT
                  and 0 < f["size"] <= _DIR_FILE_MAX_BYTES]
    text_files.sort(key=lambda f: f["size"])

    _tok = {"in": 0, "out": 0}
    mapping: dict = {}
    snippets = []
    for f in text_files[:_DIR_SNIPPET_FILES]:
        try:
            txt = _extract_text(base / f["rel"])
        except Exception:
            continue
        if not txt or txt.startswith("[Lesefehler"):
            continue
        snip = txt[:_DIR_SNIPPET_CHARS]
        if anonymize:
            snip, mapping = await _anonymize(snip, mapping, model, use_llm_ner, tok=_tok)
        snippets.append({"file": f["rel"], "snippet": snip})

    # Kompakte Auflistung für das LLM
    n_dirs = sum(1 for f in files if f["is_dir"])
    n_files = sum(1 for f in files if not f["is_dir"])
    listing = "\n".join(
        f"- {f['rel']}" + ("/" if f["is_dir"] else f" ({f['size']} B)")
        for f in files[:300])
    snip_block = "\n\n".join(
        f"### {s['file']}\n{s['snippet']}" for s in snippets)

    summary, interesting = "", []
    try:
        async with _model_session(model), httpx.AsyncClient(timeout=180) as client:
            resp = await _llm.chat(client,{
                "model": model,
                "think": False,
                "stream": False,
                "format": "json",
                "messages": [
                    {"role": "system", "content": (
                        "Du analysierst ein Dateiverzeichnis. Gib einen knappen "
                        "Überblick (worum geht es, welche Arten von Dateien) und "
                        "markiere die interessantesten Dateien, die eine genauere "
                        "Analyse lohnen. Antworte NUR mit JSON: {\"summary\":\"…\","
                        "\"interesting\":[{\"file\":\"relativer/pfad\",\"reason\":\"…\"}]}")},
                    {"role": "user", "content": (
                        f"Verzeichnis: {base.name}\n"
                        f"{n_dirs} Unterordner, {n_files} Dateien.\n\n"
                        f"Struktur:\n{listing}\n\n"
                        f"Inhalts-Auszüge:\n{snip_block}")},
                ],
            })
            resp.raise_for_status()
            _ds_j = resp.json()
            _a, _b = _llm_tok(_ds_j)
            _tok["in"] += _a
            _tok["out"] += _b
            raw = _ds_j.get("message", {}).get("content", "")
        raw = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL)
        m = re.search(r"\{[\s\S]*\}", raw)
        if m:
            data = json.loads(m.group(0))
            summary = (data.get("summary") or "").strip()
            for it in (data.get("interesting") or []):
                if isinstance(it, dict) and it.get("file"):
                    interesting.append({"file": str(it["file"]),
                                        "reason": str(it.get("reason", ""))})
    except Exception as e:
        summary = f"(KI-Überblick nicht verfügbar: {e})"

    return {
        "base": str(base),
        "name": base.name,
        "n_dirs": n_dirs,
        "n_files": n_files,
        "truncated": len(files) >= _DIR_MAX_FILES,
        "tree": files,
        "summary": summary,
        "interesting": interesting,
        "redacted": len(mapping),
        "tokens": _tok,
    }


@app.post("/api/dir/analyze-file")
async def dir_analyze_file(req: Request):
    """Vertiefte Analyse einer einzelnen Datei (Volltext, anonymisiert) → Markdown."""
    body = await req.json()
    base = _dir_resolve_base(body.get("path", ""))
    file_rel = (body.get("file_rel") or "").strip()
    if not file_rel:
        raise HTTPException(status_code=400, detail="file_rel fehlt")
    target = _dir_safe_child(base, file_rel)
    use_llm_ner = bool(body.get("llm_ner", False))
    # Standard: lokal (Datenschutz); API-Modell nur über Profil-Schalter + explizite Wahl.
    model = await _analysis_model(body.get("model"))
    if not model:
        raise HTTPException(status_code=503, detail="Kein lokales LLM verfügbar – die Verzeichnis-Analyse benötigt ein lokales Modell (Ollama). Alternativ im Profil „API-Modelle für vertrauliche Auswertungen“ aktivieren und ein API-Modell wählen.")

    try:
        if target.stat().st_size > 25_000_000:
            raise HTTPException(status_code=400, detail="Datei zu groß für die Detailanalyse (> 25 MB)")
    except OSError:
        pass
    txt = _extract_text(target)
    if not txt or txt.startswith("[Lesefehler"):
        raise HTTPException(status_code=400, detail=f"Text nicht lesbar: {txt}")
    # Anonymisierung von Personendaten ist PFLICHT (nicht abschaltbar)
    _tok = {"in": 0, "out": 0}
    mapping: dict = {}
    txt, mapping = await _anonymize(txt[:16000], mapping, model, use_llm_ner, tok=_tok)

    analysis = ""
    try:
        async with _model_session(model), httpx.AsyncClient(timeout=240) as client:
            resp = await _llm.chat(client,{
                "model": model,
                "think": False,
                "stream": False,
                "messages": [
                    {"role": "system", "content": (
                        "Analysiere die folgende Datei und fasse präzise auf Deutsch "
                        "zusammen: Worum geht es, wichtigste Inhalte/Aussagen, "
                        "Auffälligkeiten. Antworte in Markdown. Erfinde nichts.")},
                    {"role": "user", "content": f"Datei: {file_rel}\n\n{txt}"},
                ],
            })
            resp.raise_for_status()
            _da_j = resp.json()
            _a, _b = _llm_tok(_da_j)
            _tok["in"] += _a
            _tok["out"] += _b
            analysis = _da_j.get("message", {}).get("content", "").strip()
            analysis = re.sub(r"<think>.*?</think>", "", analysis, flags=re.DOTALL).strip()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Analyse fehlgeschlagen: {e}")

    return {"file": file_rel, "analysis": analysis, "redacted": len(mapping), "tokens": _tok}


@app.post("/api/dir/finalize")
async def dir_finalize(req: Request):
    """Schreibt die Index-/„init"-Datei (_KI_INDEX.md) in den Ordner und legt
    optional eine Wissensdatenbank ('Verzeichnis: …') aus dem Inhalt an."""
    body = await req.json()
    base = _dir_resolve_base(body.get("path", ""))
    content = (body.get("content") or "").strip()
    if not content:
        raise HTTPException(status_code=400, detail="Inhalt fehlt")
    to_rag = bool(body.get("to_rag", False))
    filename = (body.get("filename") or "_KI_INDEX.md").strip() or "_KI_INDEX.md"
    if "/" in filename or "\\" in filename or filename.startswith("."):
        filename = "_KI_INDEX.md"

    header = (f"# KI-Verzeichnisanalyse: {base.name}\n\n"
              f"▶ Von KI generiert · {time.strftime('%Y-%m-%d %H:%M')} · "
              f"Personendaten in Inhalten anonymisiert.\n\n")
    full = header + content
    target = base / filename
    try:
        target.write_text(full, encoding="utf-8")
    except (PermissionError, OSError) as e:
        raise HTTPException(status_code=500, detail=f"Schreiben fehlgeschlagen: {e}")

    rag_id = None
    if to_rag:
        from tools.rag import ingest_file
        coll = {
            "id": f"rag_{uuid.uuid4().hex[:12]}",
            "name": f"Verzeichnis: {base.name}",
            "embed_model": EMBED_MODEL,
            "tier": "ausgewogen",
            "chunk_size": 1000, "chunk_overlap": 150, "top_k": 6,
            "embed_gpu": False, "clean": True, "char_limit": 6000,
            "strictness": "ausgewogen", "created_at": time.time(),
        }
        await _db.rag_create_collection(coll)
        try:
            await ingest_file(coll, full, f"{base.name}.md", f"doc_{uuid.uuid4().hex[:12]}")
        except Exception as e:
            await _db.rag_delete_collection(coll["id"])
            raise HTTPException(
                status_code=500,
                detail=f"Einbetten fehlgeschlagen — ist '{EMBED_MODEL}' gepullt? ({e})")
        rag_id = coll["id"]

    return {"ok": True, "path": str(target), "rag_collection_id": rag_id}


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
    if not fp.is_file():
        raise HTTPException(status_code=400, detail=f"Datei nicht gefunden: {fp}")
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


@app.get("/api/pst/formats")
async def pst_formats():
    """Welche Eingabeformate auf diesem System nutzbar sind (für die UI-Hinweise)."""
    from tools import mailstore
    return {"formats": mailstore.available_formats(), "local_llm": await _local_llm_available()}


@app.get("/api/pst/stores")
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


@app.get("/api/pst/{store_id}")
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


@app.post("/api/pst/{store_id}/settings")
async def pst_save_settings(store_id: str, req: Request):
    """Ansicht + Konnektoren zu einem Postfach speichern (in store.json)."""
    d, store = _pst_load(store_id)
    body = await req.json()
    store["settings"] = body.get("settings") or {}
    (d / "store.json").write_text(json.dumps(store, ensure_ascii=False), encoding="utf-8")
    return {"ok": True}


@app.post("/api/pst/open")
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
    store = {
        "id": store_id, "source": str(fp), "source_format": fp.suffix.lower(),
        "opened_at": time.time(), "count": len(mails), "mails": mails,
    }
    base.mkdir(parents=True, exist_ok=True)
    (base / "store.json").write_text(json.dumps(store, ensure_ascii=False), encoding="utf-8")
    return {"store_id": store_id, "count": len(mails), "source_format": fp.suffix.lower(),
            "password": pw_status, "mails": _pst_list_view(mails)}


@app.get("/api/pst/{store_id}/mail/{mid}")
async def pst_mail(store_id: str, mid: str):
    """Vollständige E-Mail (Header + kompletter Body + Anhang-Infos) für die Klick-Ansicht."""
    _, store = _pst_load(store_id)
    for m in store.get("mails", []):
        if m.get("mid") == mid:
            return m
    raise HTTPException(status_code=404, detail="Mail nicht gefunden")


@app.delete("/api/pst/{store_id}")
async def pst_delete(store_id: str):
    """Geparstes Postfach (inkl. Anhängen) verwerfen."""
    d = _pst_store_dir(store_id)
    import shutil
    shutil.rmtree(d, ignore_errors=True)
    return {"ok": True}


@app.post("/api/pst/analyze")
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


@app.post("/api/pst/similarity")
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


@app.post("/api/pst/to-rag")
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


@app.post("/api/pst/ask")
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


@app.post("/api/pst/summarize")
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


@app.post("/api/pst/command")
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


@app.get("/api/patente/ops-config")
async def patente_ops_config_get():
    """Status der EPO-OPS-Anbindung (Key nie im Klartext zurückgeben)."""
    creds = _pat_ops_creds()
    key = (creds or {}).get("consumer_key", "")
    return {"configured": bool(creds),
            "key_masked": (key[:4] + "…" + key[-4:]) if len(key) > 8 else ("•" * len(key))}


@app.post("/api/patente/ops-config")
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


@app.get("/api/patente/projects")
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


@app.post("/api/patente/projects")
async def patente_project_create(body: PatProjectCreate):
    safe = _pat_safe_name(body.name)
    d = PATENTE_DIR / safe
    d.mkdir(parents=True, exist_ok=True)
    if not (d / "patente.json").exists():
        (d / "patente.json").write_text("[]", encoding="utf-8")
    (d / "analysen").mkdir(exist_ok=True)
    return {"name": safe}


@app.delete("/api/patente/projects/{name}")
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


@app.get("/api/patente/projects/{name}")
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


@app.post("/api/patente/projects/{name}/import/lookup")
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


@app.post("/api/patente/search")
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


@app.post("/api/patente/preview")
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


@app.post("/api/patente/projects/{name}/import/csv")
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


@app.post("/api/patente/projects/{name}/import/json")
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


@app.post("/api/patente/projects/{name}/import/citations")
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


@app.get("/api/patente/projects/{name}/export.json")
async def patente_export_json(name: str):
    items = _pat_load(name)
    data = json.dumps(items, indent=2, ensure_ascii=False)
    return Response(content=data, media_type="application/json",
                     headers={"Content-Disposition": f'attachment; filename="{_pat_safe_name(name)}_akte.json"'})


@app.get("/api/patente/projects/{name}/export.csv")
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


@app.post("/api/patente/projects/{name}/analyze")
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
    if _research_local_only() and _llm.is_remote(neben_model):
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


@app.post("/api/patente/projects/{name}/fto")
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
    if _research_local_only() and _llm.is_remote(neben_model):
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


@app.get("/api/patente/projects/{name}/analyses")
async def patente_analyses(name: str):
    from tools import patente as _patente
    d = _pat_project_dir(name)
    return {"analysen": _patente.load_analyses(d / "analysen")}


@app.get("/api/patente/projects/{name}/analyses/{file_name}")
async def patente_analysis_get(name: str, file_name: str):
    d = _pat_project_dir(name)
    p = d / "analysen" / Path(file_name).name
    if not p.exists():
        raise HTTPException(status_code=404, detail="Analyse nicht gefunden")
    return json.loads(p.read_text(encoding="utf-8"))


@app.get("/api/patente/projects/{name}/analyses/{file_name}/markdown")
async def patente_analysis_markdown(name: str, file_name: str):
    d = _pat_project_dir(name)
    safe = Path(file_name).name
    p = (d / "analysen" / safe).with_suffix(".md")
    if not p.exists():
        raise HTTPException(status_code=404, detail="Markdown nicht gefunden")
    return Response(content=p.read_text(encoding="utf-8"), media_type="text/markdown",
                     headers={"Content-Disposition": f'attachment; filename="{p.name}"'})


@app.delete("/api/patente/projects/{name}/analyses/{file_name}")
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


@app.post("/api/patente/projects/{name}/ask")
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


@app.post("/api/patente/projects/{name}/graph")
async def patente_graph(name: str, body: PatGraph):
    from tools import patente as _patente
    items = _pat_load(name)
    nodes, edges = _patente.build_graph_data(
        items, body.show_ipc, body.show_assignee, body.show_citations, body.focus_assignee)
    return {"nodes": nodes, "edges": edges}


class PatMigrate(BaseModel):
    source_dir: str


@app.post("/api/patente/migrate")
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


# ── Morphologischer Kasten (Zwicky-Box) ─────────────────────────────────────────
# KI-gestütztes Ideenfindungs-Raster: Parameter (Zeilen) × Ausprägungen (Werte).
# Eine Lösung = je Parameter eine Ausprägung. Die KI generiert Parameter/
# Ausprägungen, bewertet gewählte Kombinationen (+ schlägt interessante vor) und
# verfeinert einzelne Zellen. Export läuft über bestehende Wege (DOCX/Doku/RAG)
# im Frontend — kein eigener Endpunkt.


def _morph_value_str(v) -> str:
    """Normalisiert eine Ausprägung auf einen lesbaren String. Kleine Modelle
    liefern statt eines Strings manchmal ein verschachteltes Objekt/Listen —
    das wird kompakt als „Schlüssel: Wert · …" geglättet."""
    if isinstance(v, str):
        return v.strip()
    if isinstance(v, dict):
        parts = []
        for k, val in v.items():
            if isinstance(val, (list, tuple)):
                val = ", ".join(str(x) for x in val)
            elif isinstance(val, dict):
                val = "; ".join(f"{a}: {b}" for a, b in val.items())
            parts.append(f"{k}: {val}")
        return " · ".join(parts).strip()
    if isinstance(v, (list, tuple)):
        return ", ".join(_morph_value_str(x) for x in v).strip()
    return str(v).strip()


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


async def _morph_llm(model: str, system: str, user: str,
                     tok: Optional[dict] = None) -> Optional[dict]:
    """``tok`` (optional): dict {"in","out"}, in das der Tokenverbrauch summiert wird."""
    try:
        async with _model_session(model), httpx.AsyncClient(timeout=180) as client:
            resp = await _llm.chat(client,{
                "model": model,
                "think": False,
                "stream": False,
                "format": "json",
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
            return _parse_llm_json(j.get("message", {}).get("content", ""))
    except Exception:
        return None


async def _morph_sources_context(problem: str, web: bool, rag_collections: list) -> str:
    """Optionaler Inspirationskontext aus Websuche und/oder Wissensdatenbanken für
    die Morph-Generierung. Gibt einen an den User-Prompt anzuhängenden Block zurück
    (oder "" wenn nichts gewählt/gefunden). Die Embeddings der RAG-Suche laufen auf
    CPU (siehe rag.py), daher kein eigenes _model_session nötig — wie im Chat-RAG."""
    parts = []
    problem = (problem or "").strip()
    if web and problem:
        try:
            from tools.search import search_with_sources
            _, txt = await search_with_sources(problem, 5)
            if txt:
                parts.append("Recherche-Ergebnisse (Web):\n" + txt[:2500])
        except Exception:
            pass
    if rag_collections and problem:
        try:
            from tools.rag import query_collections
            hits = await query_collections(rag_collections, problem, top_k_cap=8)
            if hits:
                ctx = "\n\n".join(f"[{h['filename']}]\n{h['text']}" for h in hits)
                parts.append("Auszüge aus den Wissensdatenbanken:\n" + ctx)
        except Exception:
            pass
    if not parts:
        return ""
    return ("\n\nNutze die folgenden Quellen als Inspiration und fachliche Grundlage; "
            "erfinde nichts dazu, was ihnen klar widerspricht:\n\n" + "\n\n".join(parts))


@app.post("/api/morph/generate")
async def morph_generate(req: Request):
    """Erzeugt Parameter (Zeilen) und je Parameter mehrere Ausprägungen (Werte)
    für ein Problem."""
    body = await req.json()
    problem = (body.get("problem") or "").strip()
    if not problem:
        raise HTTPException(status_code=400, detail="Problem fehlt")
    model = _pick_model(body.get("model"), _model_for("general"))
    _ctx = await _morph_sources_context(
        problem, bool(body.get("web")), body.get("rag_collections") or [])
    _tok = {"in": 0, "out": 0}
    data = await _morph_llm(
        model,
        ("Du erstellst einen morphologischen Kasten (Zwicky-Box) für eine "
         "Aufgabenstellung. Bestimme 4–7 unabhängige Parameter (Merkmale, die eine "
         "Lösung beschreiben) und je Parameter 3–5 konkrete Ausprägungen. Jede "
         "Ausprägung ist ein KURZER Text (Stichwort, max. ~6 Wörter) — KEIN Objekt, "
         "keine verschachtelten Felder. Antworte NUR mit JSON: "
         "{\"parameters\":[{\"name\":\"Parameter\",\"values\":"
         "[\"Ausprägung 1\",\"Ausprägung 2\"]}]}"),
        f"Aufgabenstellung:\n{problem}{_ctx}", tok=_tok)
    params = []
    if data:
        for p in (data.get("parameters") or []):
            if isinstance(p, dict) and p.get("name"):
                vals = [s for s in (_morph_value_str(v) for v in (p.get("values") or [])) if s]
                if vals:
                    params.append({"name": str(p["name"]).strip(), "values": vals})
    if not params:
        raise HTTPException(status_code=502, detail="KI lieferte keine verwertbaren Parameter")
    return {"parameters": params, "tokens": _tok}


@app.post("/api/morph/evaluate")
async def morph_evaluate(req: Request):
    """Bewertet eine gewählte Kombination und schlägt interessante Alternativen vor.
    selection = Liste von {parameter, value}."""
    body = await req.json()
    problem = (body.get("problem") or "").strip()
    selection = body.get("selection") or []
    if not problem or not selection:
        raise HTTPException(status_code=400, detail="Problem oder Auswahl fehlt")
    model = _pick_model(body.get("model"), _model_for("general"))
    sel_txt = "\n".join(
        f"- {s.get('parameter','?')}: {s.get('value','?')}"
        for s in selection if isinstance(s, dict))
    params_txt = ""
    if body.get("parameters"):
        params_txt = "\n\nVerfügbare Parameter/Ausprägungen:\n" + "\n".join(
            f"- {p.get('name','?')}: {', '.join(p.get('values', []))}"
            for p in body["parameters"] if isinstance(p, dict))
    _tok = {"in": 0, "out": 0}
    data = await _morph_llm(
        model,
        ("Du bewertest eine Lösungskombination aus einem morphologischen Kasten. "
         "Gib eine Gesamtbewertung (score 0–100), Einschätzungen zu Machbarkeit und "
         "Innovationsgrad (jeweils 0–100), eine kurze Begründung und Risiken. "
         "Schlage außerdem bis zu drei interessante alternative Kombinationen vor. "
         "Antworte NUR mit JSON: {\"score\":0,\"machbarkeit\":0,\"innovation\":0,"
         "\"begruendung\":\"…\",\"risiken\":[\"…\"],\"vorschlaege\":[{\"picks\":"
         "[{\"parameter\":\"…\",\"value\":\"…\"}],\"score\":0,\"begruendung\":\"…\"}]}"),
        f"Aufgabenstellung:\n{problem}\n\nGewählte Kombination:\n{sel_txt}{params_txt}", tok=_tok)
    if not data:
        raise HTTPException(status_code=502, detail="KI-Bewertung fehlgeschlagen")
    return {
        "score": data.get("score"),
        "machbarkeit": data.get("machbarkeit"),
        "innovation": data.get("innovation"),
        "begruendung": (data.get("begruendung") or "").strip(),
        "risiken": [str(r) for r in (data.get("risiken") or [])],
        "vorschlaege": data.get("vorschlaege") or [],
        "tokens": _tok,
    }


@app.post("/api/morph/refine-cell")
async def morph_refine_cell(req: Request):
    """Verfeinert eine einzelne Zelle: ausformulieren (expand) oder
    Alternativen/Kritik (critique)."""
    body = await req.json()
    problem = (body.get("problem") or "").strip()
    parameter = (body.get("parameter") or "").strip()
    value = (body.get("value") or "").strip()
    action = (body.get("action") or "expand").strip().lower()
    if not value:
        raise HTTPException(status_code=400, detail="Ausprägung fehlt")
    model = _pick_model(body.get("model"), _model_for("general"))
    _ctx = await _morph_sources_context(
        problem, bool(body.get("web")), body.get("rag_collections") or [])
    if action == "critique":
        system = ("Du kritisierst eine Ausprägung in einem morphologischen Kasten und "
                  "schlägst bessere/zusätzliche Alternativen vor. Antworte NUR mit JSON: "
                  "{\"text\":\"kurze Kritik\",\"alternativen\":[\"…\"]}")
    else:
        system = ("Du formulierst eine Ausprägung in einem morphologischen Kasten "
                  "konkreter und anschaulicher aus (1–3 Sätze). Antworte NUR mit JSON: "
                  "{\"text\":\"…\",\"alternativen\":[]}")
    _tok = {"in": 0, "out": 0}
    data = await _morph_llm(
        model, system,
        f"Aufgabenstellung:\n{problem}\n\nParameter: {parameter}\nAusprägung: {value}{_ctx}",
        tok=_tok)
    if not data:
        raise HTTPException(status_code=502, detail="KI-Verfeinerung fehlgeschlagen")
    return {"text": (data.get("text") or "").strip(),
            "alternativen": [str(a) for a in (data.get("alternativen") or [])],
            "tokens": _tok}


@app.post("/api/morph/ideas")
async def morph_ideas(req: Request):
    """Erzeugt mehrere KREATIVE Konzept-Ideen (je eine Ausprägung pro Parameter)
    zum Durchwischen. Optional über Web/Wissensdatenbanken inspiriert."""
    body = await req.json()
    problem = (body.get("problem") or "").strip()
    if not problem:
        raise HTTPException(status_code=400, detail="Problem fehlt")
    model = _pick_model(body.get("model"), _model_for("general"))
    n = max(1, min(8, int(body.get("n") or 5)))
    params = body.get("parameters") or []
    params_txt = ""
    if params:
        params_txt = "\n\nParameter und mögliche Ausprägungen:\n" + "\n".join(
            f"- {p.get('name','?')}: {', '.join(_morph_value_str(v) for v in (p.get('values') or []))}"
            for p in params if isinstance(p, dict))
    _ctx = await _morph_sources_context(
        problem, bool(body.get("web")), body.get("rag_collections") or [])
    _tok = {"in": 0, "out": 0}
    data = await _morph_llm(
        model,
        (f"Du erzeugst {n} KREATIVE, deutlich unterschiedliche Lösungsideen für eine "
         "Aufgabenstellung auf Basis eines morphologischen Kastens. Jede Idee wählt je "
         "Parameter genau EINE Ausprägung (nutze die vorgegebenen, wenn vorhanden, sonst "
         "passende eigene) und bekommt einen kurzen, prägnanten Konzepttitel/-satz. Wage "
         "auch ungewöhnliche, originelle Kombinationen. Antworte NUR mit JSON: "
         "{\"ideen\":[{\"concept\":\"kurzer Konzepttext\",\"picks\":"
         "[{\"parameter\":\"…\",\"value\":\"…\"}]}]}"),
        f"Aufgabenstellung:\n{problem}{params_txt}{_ctx}", tok=_tok)
    ideen = []
    if data:
        for it in (data.get("ideen") or []):
            if not isinstance(it, dict):
                continue
            picks = []
            for pk in (it.get("picks") or []):
                if isinstance(pk, dict) and pk.get("parameter"):
                    picks.append({"parameter": str(pk["parameter"]).strip(),
                                  "value": _morph_value_str(pk.get("value"))})
            concept = _morph_value_str(it.get("concept"))
            if picks or concept:
                ideen.append({"concept": concept, "picks": picks})
    if not ideen:
        raise HTTPException(status_code=502, detail="KI lieferte keine Ideen")
    return {"ideen": ideen, "tokens": _tok}


# ── Morph-Trainingsfile (Backend, automatisch generiert) ──────────────────────
# Gute/schlechte Ideen sammeln sich fortlaufend je Thema unter
# data/morph_training/<slug>.jsonl. Quellen: Wischtechnik, gelöschte ausformulierte
# Karten (= „schlecht"), gemerkte Lösungen (= „gut"). Pro Zeile sowohl strukturiert
# als auch im Chat-Format (messages) zum Finetunen.
MORPH_TRAIN_DIR = DATA_DIR / "morph_training"


def _morph_train_path(problem: str) -> Path:
    slug = _to_slug((problem or "").strip()) or "allgemein"
    return MORPH_TRAIN_DIR / f"{slug}.jsonl"


@app.post("/api/morph/training/add")
async def morph_training_add(req: Request):
    body = await req.json()
    problem = (body.get("problem") or "").strip()
    label = (body.get("label") or "").strip().lower()
    if label not in ("good", "bad"):
        raise HTTPException(status_code=400, detail="label muss 'good' oder 'bad' sein")
    idea = body.get("idea") or {}
    picks = [p for p in (idea.get("picks") or []) if isinstance(p, dict)]
    concept = str(idea.get("concept") or "").strip()
    reason = str(body.get("reason") or "").strip()
    source = str(body.get("source") or "swipe").strip()
    evaluation = body.get("evaluation") or None
    combo = "\n".join(f"- {p.get('parameter','?')}: {p.get('value','?')}" for p in picks)
    user_txt = f"Aufgabe: {problem or '—'}"
    if concept:
        user_txt += f"\nIdee: {concept}"
    if combo:
        user_txt += f"\nKombination:\n{combo}"
    urteil = "GUT — geeignete Idee." if label == "good" else "SCHLECHT — ungeeignete Idee."
    assistant_txt = urteil + (f"\nBegründung: {reason}" if reason else "")
    rec = {
        "problem": problem, "label": label, "reason": reason, "source": source,
        "idea": {"concept": concept, "picks": picks}, "evaluation": evaluation,
        "ts": time.time(),
        "messages": [
            {"role": "user", "content": user_txt},
            {"role": "assistant", "content": assistant_txt},
        ],
    }
    MORPH_TRAIN_DIR.mkdir(parents=True, exist_ok=True)
    path = _morph_train_path(problem)
    async with aiofiles.open(path, "a", encoding="utf-8") as f:
        await f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    n = 0
    async with aiofiles.open(path, "r", encoding="utf-8") as f:
        async for _line in f:
            if _line.strip():
                n += 1
    return {"ok": True, "count": n, "file": path.name}


@app.get("/api/morph/training")
async def morph_training_get(problem: str = "", format: str = "jsonl"):
    path = _morph_train_path(problem)
    recs = []
    if path.exists():
        async with aiofiles.open(path, "r", encoding="utf-8") as f:
            async for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    recs.append(json.loads(line))
                except Exception:
                    pass
    if format == "md":
        def _fmt(r):
            idea = r.get("idea") or {}
            picks = [p for p in (idea.get("picks") or []) if isinstance(p, dict)]
            combo = ", ".join(f"{p.get('parameter','?')}: {p.get('value','?')}" for p in picks)
            head = (idea.get("concept") or combo or "—").strip()
            line = f"- **{head}**"
            if combo and idea.get("concept"):
                line += f" ({combo})"
            if r.get("reason"):
                line += f" — {r['reason']}"
            return line
        good = [r for r in recs if r.get("label") == "good"]
        bad = [r for r in recs if r.get("label") == "bad"]
        md = f"# Trainingsdaten Morphologischer Kasten\n\n**Aufgabe:** {problem or '—'}\n\n"
        md += f"## Gute Ideen ({len(good)})\n\n" + ("\n".join(_fmt(r) for r in good) or "_keine_") + "\n\n"
        md += f"## Schlechte Ideen ({len(bad)})\n\n" + ("\n".join(_fmt(r) for r in bad) or "_keine_") + "\n"
        return Response(md, media_type="text/markdown; charset=utf-8")
    raw = ("\n".join(json.dumps(r, ensure_ascii=False) for r in recs) + "\n") if recs else ""
    return Response(raw, media_type="application/jsonl; charset=utf-8")


@app.delete("/api/morph/training")
async def morph_training_delete(problem: str = ""):
    path = _morph_train_path(problem)
    if path.exists():
        path.unlink()
    return {"ok": True}


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


@app.get("/api/firmenprofil")
async def get_firmenprofil():
    return _load_firmenprofil()


@app.post("/api/firmenprofil")
async def save_firmenprofil(req: Request):
    data = await req.json()
    if not isinstance(data, dict):
        raise HTTPException(status_code=400, detail="Ungültiges Profil.")
    _save_firmenprofil(data)
    return {"ok": True}


@app.get("/api/rechnung/next-number")
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


@app.post("/api/rechnung/parse")
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


@app.post("/api/rechnung/breakdown")
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


@app.post("/api/rechnung/create")
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


@app.get("/api/rechnung/list")
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


@app.delete("/api/rechnung/{nr}")
async def rechnung_delete(nr: str):
    fp = _rechnung_path(nr)
    if fp.exists():
        fp.unlink()
    return {"ok": True}


@app.get("/api/rechnung/{nr}/pdf")
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


@app.get("/api/rechnung/{nr}/docx")
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

@app.get("/api/angebot/next-number")
async def angebot_next_number():
    return {"nummer": _peek_angebotsnummer()}


@app.post("/api/angebot/from-plan")
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


@app.post("/api/angebot/create")
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


@app.get("/api/angebot/list")
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


@app.get("/api/angebot/{nr}")
async def angebot_get(nr: str):
    return _load_angebot(nr)


@app.delete("/api/angebot/{nr}")
async def angebot_delete(nr: str):
    fp = _angebot_path(nr)
    if fp.exists():
        fp.unlink()
    return {"ok": True}


@app.get("/api/angebot/{nr}/pdf")
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


@app.get("/api/angebot/{nr}/docx")
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


@app.post("/api/zeugnis/generate")
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


@app.post("/api/zeugnis/{zid}/save")
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


@app.get("/api/zeugnis/list")
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


@app.get("/api/zeugnis/{zid}")
async def zeugnis_get(zid: str):
    if not re.match(r"^[A-Za-z0-9._\-]{1,80}$", zid):
        raise HTTPException(status_code=400, detail="Ungültige ID.")
    fp = ZEUGNISSE_DIR / f"{zid}.json"
    if not fp.exists():
        raise HTTPException(status_code=404, detail="Zeugnis nicht gefunden.")
    return json.loads(fp.read_text(encoding="utf-8"))


@app.delete("/api/zeugnis/{zid}")
async def zeugnis_delete(zid: str):
    if not re.match(r"^[A-Za-z0-9._\-]{1,80}$", zid):
        raise HTTPException(status_code=400, detail="Ungültige ID.")
    fp = ZEUGNISSE_DIR / f"{zid}.json"
    if fp.exists():
        fp.unlink()
    return {"ok": True}


# ── Export-API ────────────────────────────────────────────────────────────────


@app.post("/api/export/docx")
async def export_docx(req: Request):
    from tools.export import to_docx
    body = await req.json()
    body["_profile"] = _load_profile()
    fp = to_docx(body)
    return FileResponse(
        fp,
        filename="ai_framework_thomas_dokument.docx",
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )


@app.post("/api/export/xlsx")
async def export_xlsx(req: Request):
    from tools.export import to_xlsx
    body = await req.json()
    body["_profile"] = _load_profile()
    fp = to_xlsx(body)
    return FileResponse(
        fp,
        filename="ai_framework_thomas_tabelle.xlsx",
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


@app.post("/api/export/pptx")
async def export_pptx(req: Request):
    from tools.export import to_pptx
    body = await req.json()
    body["_profile"] = _load_profile()
    fp = to_pptx(body)
    return FileResponse(
        fp,
        filename="ai_framework_thomas_praesentation.pptx",
        media_type="application/vnd.openxmlformats-officedocument.presentationml.presentation",
    )


@app.post("/api/export/pdf")
async def export_pdf(req: Request):
    """PDF aus Dokument ({title,content}) oder Präsentation ({type:'presentation'})
    — über matplotlib, ohne TeX-Installation."""
    from tools.export import to_pdf
    body = await req.json()
    body["_profile"] = _load_profile()
    try:
        fp = await asyncio.to_thread(to_pdf, body)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"PDF-Export fehlgeschlagen: {e}")
    name = ("ai_framework_thomas_praesentation.pdf"
            if body.get("type") == "presentation" else "ai_framework_thomas_dokument.pdf")
    return FileResponse(fp, filename=name, media_type="application/pdf")


@app.post("/api/export/latex")
async def export_latex(req: Request):
    """Reine LaTeX-Quelle (.tex): Dokument → article, Präsentation → beamer.
    Formeln bleiben echtes LaTeX-Math; keine TeX-Installation nötig."""
    from tools.export import to_latex
    body = await req.json()
    body["_profile"] = _load_profile()
    try:
        fp = await asyncio.to_thread(to_latex, body)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"LaTeX-Export fehlgeschlagen: {e}")
    name = ("ai_framework_thomas_praesentation.tex"
            if body.get("type") == "presentation" else "ai_framework_thomas_dokument.tex")
    return FileResponse(fp, filename=name, media_type="application/x-tex")


# ── Profil-API ────────────────────────────────────────────────────────────────


@app.get("/api/profile")
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


@app.put("/api/profile")
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


# ── Projekt-API ───────────────────────────────────────────────────────────────


@app.get("/api/projects")
async def list_projects():
    # Liste (rückwärtskompatibel) — pro Projekt Status + Label ergänzt.
    projects = _load_projects()
    for p in projects:
        st = p.get("status") or "planung"
        p["status"] = st
        p["status_label"] = _PROJECT_STATUS_LABELS.get(st, st)
    return projects


@app.get("/api/project-status-labels")
async def project_status_labels():
    return _PROJECT_STATUS_LABELS


@app.post("/api/projects")
async def create_project(req: Request):
    body = await req.json()
    projects = _load_projects()
    project = {
        "id": uuid.uuid4().hex[:8],
        "name": str(body.get("name", "Neues Projekt")).strip(),
        "number": str(body.get("number", "")).strip(),
        "description": str(body.get("description", "")).strip(),
        "status": "planung",
        "plan_id": str(body.get("plan_id", "")).strip(),
        "created_at": time.time(),
    }
    projects.append(project)
    _save_projects(projects)
    return project


@app.put("/api/projects/{pid}")
async def update_project(pid: str, req: Request):
    body = await req.json()
    projects = _load_projects()
    hit = None
    for p in projects:
        if p["id"] == pid:
            # Stammdaten nur überschreiben, wenn im Body enthalten
            if "name" in body:
                p["name"] = str(body.get("name") or p.get("name", "")).strip()
            if "number" in body:
                p["number"] = str(body.get("number") or "").strip()
            if "description" in body:
                p["description"] = str(body.get("description") or "").strip()
            # Workflow-Felder (nur bei Vorhandensein)
            for k in ("status", "plan_id", "angebot_nr", "rechnung_nr"):
                if k in body:
                    p[k] = str(body.get(k) or "").strip()
            if body.get("status") and body["status"] not in _PROJECT_STATUS_LABELS:
                raise HTTPException(status_code=400, detail="Unbekannter Projektstatus.")
            p["updated_at"] = time.time()
            hit = p
            break
    if hit is None:
        raise HTTPException(status_code=404, detail="Projekt nicht gefunden.")
    _save_projects(projects)
    hit["status_label"] = _PROJECT_STATUS_LABELS.get(hit.get("status") or "planung", "")
    return {"ok": True, "project": hit}


@app.delete("/api/projects/{pid}")
async def delete_project(pid: str):
    projects = [p for p in _load_projects() if p["id"] != pid]
    PROJECTS_FILE.write_text(json.dumps(projects, ensure_ascii=False, indent=2), encoding="utf-8")
    # Projekt-gebundene Skill-Agenten mitlöschen (sie sind ausschließlich diesem
    # Projekt zugeordnet und sonst nirgends sichtbar → keine Karteileichen hinterlassen).
    removed = 0
    for f in list(AGENTS_DIR.glob("*.json")):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        if (data.get("project_id") or "") == pid:
            f.unlink(missing_ok=True)
            removed += 1
    return {"ok": True, "agents_removed": removed}


@app.put("/api/conversations/{cid}/project")
async def set_conversation_project(cid: str, req: Request):
    body = await req.json()
    project_id = body.get("project_id")
    await _db.set_project(cid, project_id)
    return {"ok": True}


# ── Plan-API ──────────────────────────────────────────────────────────────────


@app.get("/api/plans")
async def list_plans():
    plans = []
    for f in sorted(PLANS_DIR.glob("*.json"), key=lambda x: x.stat().st_mtime, reverse=True):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            plans.append({"id": data["id"], "name": data.get("name", "Plan"), "updated_at": data.get("updated_at", 0)})
        except Exception:
            pass
    return plans


def _plan_path(plan_id: str, plan_name: str = "") -> Path:
    """Gibt den Dateipfad für einen Plan zurück (sprechender Name + ID-Suffix)."""
    if plan_name:
        slug = _to_slug(plan_name)
        return PLANS_DIR / f"{slug}_{plan_id[:8]}.json"
    return PLANS_DIR / f"{plan_id}.json"


def _plan_path_by_id(plan_id: str) -> Optional[Path]:
    """Findet Plan-Datei anhand der ID."""
    for fp in PLANS_DIR.glob("*.json"):
        try:
            data = json.loads(fp.read_text(encoding="utf-8"))
            if data.get("id") == plan_id:
                return fp
        except Exception:
            pass
    return None


@app.post("/api/plans")
async def create_plan(req: Request):
    body = await req.json()
    name = str(body.get("name", "Neuer Plan")).strip()
    plan = {
        "id": uuid.uuid4().hex[:12],
        "name": name,
        "created_at": time.time(),
        "updated_at": time.time(),
        "tasks": body.get("tasks", []),
        "description": str(body.get("description", "")).strip(),
        "system_prompt": str(body.get("system_prompt", "")).strip(),
        "resource_catalog": body.get("resource_catalog", []),
        "resource_mode": str(body.get("resource_mode", "free")).strip(),
        "start_date": str(body.get("start_date", "")).strip(),
        "workdays": bool(body.get("workdays", False)),
        "project_id": str(body.get("project_id", "")).strip(),
    }
    _plan_path(plan["id"], name).write_text(
        json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return plan


@app.get("/api/plans/{pid}")
async def get_plan(pid: str):
    fp = _plan_path_by_id(pid)
    if not fp or not fp.exists():
        raise HTTPException(404, "Plan nicht gefunden")
    return json.loads(fp.read_text(encoding="utf-8"))


@app.put("/api/plans/{pid}")
async def save_plan(pid: str, req: Request):
    body = await req.json()
    old_fp = _plan_path_by_id(pid)
    existing = json.loads(old_fp.read_text(encoding="utf-8")) if old_fp and old_fp.exists() else {}
    existing.update(body)
    existing["id"] = pid
    existing["updated_at"] = time.time()
    new_fp = _plan_path(pid, existing.get("name", ""))
    if old_fp and old_fp != new_fp and old_fp.exists():
        old_fp.unlink(missing_ok=True)
    new_fp.write_text(json.dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8")
    return existing


@app.delete("/api/plans/{pid}")
async def delete_plan(pid: str):
    fp = _plan_path_by_id(pid)
    if fp:
        fp.unlink(missing_ok=True)
    return {"ok": True}


@app.post("/api/plans/{pid}/ai")
async def plan_ai(pid: str, req: Request):
    body = await req.json()
    fp = PLANS_DIR / f"{pid}.json"
    plan = json.loads(fp.read_text(encoding="utf-8")) if fp.exists() else {}
    # Inline-Tasks aus dem Body übernehmen falls kein gespeicherter Plan
    tasks = plan.get("tasks") or body.get("tasks", [])
    model = _pick_model(body.get("model"))
    user_message = body.get("message", "")
    use_web = body.get("use_web", False)
    use_rag = body.get("use_rag", False)
    tasks_summary = json.dumps(tasks, ensure_ascii=False)

    system_prompt = (
        "Du bist ein erfahrener Projektmanager und hilfst beim Erstellen und Verfeinern von Projektplänen. "
        "Du kennst Methoden wie CPM, Netzplanung, kritischen Pfad und Ressourcenplanung. "
        "Antworte auf Deutsch, präzise und konstruktiv. "
        "Wenn du Aufgaben vorschlägst, nenne sie als JSON-Liste mit Feldern: id, name, duration, predecessors, successors."
    )

    context_parts = [f"Aktueller Plan '{plan.get('name', 'Plan')}':\n\nAufgaben:\n{tasks_summary}"]

    if use_web:
        from tools.search import search_with_sources
        try:
            _, search_text = await search_with_sources(user_message, 4)
            if search_text:
                context_parts.append(f"Websuche-Ergebnisse:\n{search_text[:3000]}")
        except Exception:
            pass

    # Wissensdatenbanken zur Informationsbeschaffung: die plan-eigene Basis
    # (bei aktivem 📚-Schalter) UND optional im Planer ausgewählte Basen.
    rag_ids = list(body.get("rag_collections") or [])
    if use_rag and plan.get("rag_collection_id"):
        rag_ids.append(plan["rag_collection_id"])
    if rag_ids:
        from tools.rag import query_collections
        colls, seen = [], set()
        for cid in rag_ids:
            if not cid or cid in seen:
                continue
            seen.add(cid)
            c = await _db.rag_get_collection(cid)
            if c:
                colls.append(c)
        if colls:
            try:
                hits = await query_collections(colls, user_message)
                if hits:
                    rag_text = "\n\n".join(h.get("text", "") for h in hits[:6])
                    context_parts.append(f"Aus Wissensdatenbank:\n{rag_text[:3000]}")
            except Exception:
                pass

    user_content = "\n\n".join(context_parts) + f"\n\nFrage: {user_message}"
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_content},
    ]

    async def _stream():
        async with _model_session(model), httpx.AsyncClient(timeout=120) as client:
            async for chunk in _llm.stream(client, {
                "model": model,
                "think": False,
                "messages": messages,
                "stream": True,
                # Großes Kontextfenster: der komplette Aufgaben-JSON-Block kann das
                # Ollama-Default (2048) sprengen → sonst fällt die eigentliche Frage
                # aus dem Kontext und das Modell antwortet mit Bruchstücken/Einwörtern.
                "options": {"num_ctx": 8192, "temperature": 0.4},
            }):
                try:
                    token = chunk.get("message", {}).get("content", "")
                    if token:
                        yield f"data: {json.dumps({'type': 'text', 'content': token})}\n\n"
                    if chunk.get("done"):
                        _a, _b = _llm_tok(chunk)
                        yield f"data: {json.dumps({'type': 'done', 'tokens': {'in': _a, 'out': _b}})}\n\n"
                except Exception:
                    pass

    return StreamingResponse(_stream(), media_type="text/event-stream")


@app.post("/api/plans/{pid}/check-feasibility")
async def plan_check_feasibility(pid: str, req: Request):
    """Prüft den Plan strukturiert auf Durchführbarkeit: erkennt deterministisch
    Zyklen, lose Enden und mehrfache Wurzeln und lässt das LLM fehlende Aufgaben,
    Lücken, Risiken und Empfehlungen ergänzen. Liefert strukturiertes JSON."""
    body = await req.json()
    fp = PLANS_DIR / f"{pid}.json"
    plan = json.loads(fp.read_text(encoding="utf-8")) if fp.exists() else {}
    tasks = plan.get("tasks") or body.get("tasks", [])
    if not tasks:
        raise HTTPException(400, "Kein Plan mit Aufgaben vorhanden")
    description = (body.get("description") or plan.get("description") or "").strip()
    system_prompt = (body.get("system_prompt") or plan.get("system_prompt") or "").strip()
    model = _pick_model(body.get("model"))

    # ── Deterministische Strukturprüfung ──────────────────────────────────────
    by_id = {str(t.get("id")): t for t in tasks if t.get("id")}
    ids = list(by_id.keys())
    idset = set(ids)
    preds = {i: [p for p in (by_id[i].get("predecessors") or []) if p in idset] for i in ids}
    has_succ = set()
    for i in ids:
        for p in preds[i]:
            has_succ.add(p)
    no_pred = [i for i in ids if not preds[i] and not by_id[i].get("is_start")]
    no_succ = [i for i in ids if i not in has_succ and not by_id[i].get("is_end")]
    # Zyklen via Kahn
    indeg = {i: len(preds[i]) for i in ids}
    succ = {i: [] for i in ids}
    for i in ids:
        for p in preds[i]:
            succ[p].append(i)
    queue = [i for i in ids if indeg[i] == 0]
    seen_n = 0
    while queue:
        n = queue.pop()
        seen_n += 1
        for m in succ[n]:
            indeg[m] -= 1
            if indeg[m] == 0:
                queue.append(m)
    cycle = seen_n < len(ids)

    struct_hints = []
    if cycle:
        struct_hints.append("Mindestens ein Zyklus in den Abhängigkeiten – CPM nicht berechenbar.")
    if len(no_pred) > 1:
        struct_hints.append(f"Mehrere Aufgaben ohne Vorgänger (mögliche fehlende Verknüpfung): {', '.join(no_pred[:8])}.")
    if no_succ:
        struct_hints.append(f"Lose Enden ohne Nachfolger: {', '.join(no_succ[:8])}.")

    # ── LLM-Bewertung ─────────────────────────────────────────────────────────
    tasks_summary = json.dumps(
        [{"id": t.get("id"), "name": t.get("name"), "duration": t.get("duration"),
          "predecessors": t.get("predecessors")} for t in tasks],
        ensure_ascii=False)
    sys = (system_prompt + "\n\n" if system_prompt else "") + (
        "Du bist ein erfahrener Projektmanager und prüfst Projektpläne kritisch auf "
        "Durchführbarkeit und Vollständigkeit. Antworte ausschließlich mit gültigem JSON."
    )
    user = (
        (f"Projektbeschreibung & Ziel:\n{description}\n\n" if description else "") +
        f"Aktuelle Aufgaben:\n{tasks_summary}\n\n" +
        (("Automatisch erkannte Strukturprobleme:\n- " + "\n- ".join(struct_hints) + "\n\n") if struct_hints else "") +
        "Prüfe den Plan auf Durchführbarkeit und welche Aufgaben FEHLEN. Antworte NUR mit JSON:\n"
        '{"durchfuehrbar": true, "bewertung": "kurzes Gesamturteil", '
        '"fehlende_aufgaben": [{"id":"N1","name":"…","duration":2,"predecessors":["T3"]}], '
        '"luecken": ["…"], "risiken": ["…"], "empfehlungen": ["…"]}\n'
        "fehlende_aufgaben: konkrete Vorschläge mit nicht kollidierender id, name, duration, predecessors "
        "(verweise nur auf existierende oder neue ids)."
    )

    async with _model_session(model), httpx.AsyncClient(timeout=180) as client:
        resp = await _llm.chat(client, {
            "model": model, "think": False, "format": "json",
            "messages": [{"role": "system", "content": sys},
                         {"role": "user", "content": user}],
            "stream": False, "options": {"num_ctx": 8192},
        })
        resp.raise_for_status()
        _cf_j = resp.json()
        _cf_ti, _cf_to = _llm_tok(_cf_j)
        raw = _cf_j.get("message", {}).get("content", "")

    data = _parse_llm_json(raw) or {}
    if not isinstance(data, dict):
        data = {}
    # Kleine Modelle liefern oft Umlaut-Schlüssel statt der ASCII-Variante aus dem
    # Schema-Beispiel → auf die erwarteten Keys normalisieren.
    for k_uml, k_ascii in (("durchführbar", "durchfuehrbar"), ("lücken", "luecken")):
        if k_uml in data and not data.get(k_ascii):
            data[k_ascii] = data.pop(k_uml)
    data.setdefault("durchfuehrbar", not cycle)
    data.setdefault("luecken", [])
    data.setdefault("fehlende_aufgaben", [])
    # Deterministische Befunde ergänzen (verlässlich, unabhängig vom Modell)
    if struct_hints:
        data["struktur"] = struct_hints
    data["cycle"] = cycle
    data["no_predecessor"] = no_pred
    data["loose_ends"] = no_succ
    data["tokens"] = {"in": _cf_ti, "out": _cf_to}
    return data


@app.post("/api/plans/evaluate")
async def plan_evaluate(req: Request):
    """Vergleicht bis zu 3 Pläne KI-gestützt und erzeugt einen verbesserten gemeinsamen Plan."""
    body = await req.json()
    plan_ids = (body.get("plan_ids") or [])[:3]
    model = _pick_model(body.get("model"))
    if not plan_ids:
        raise HTTPException(400, "Mindestens eine plan_id erforderlich")

    plans = []
    for pid in plan_ids:
        fp = _plan_path_by_id(pid)
        if fp and fp.exists():
            try:
                plans.append(json.loads(fp.read_text(encoding="utf-8")))
            except Exception:
                pass
    if not plans:
        raise HTTPException(404, "Keine Pläne geladen")

    def _plan_summary(p):
        tasks = p.get("tasks", [])
        tlines = "\n".join(f"  - {t.get('name','')} (Dauer {t.get('duration',0)}d, Vorgänger: {t.get('predecessors',[])})" for t in tasks[:40])
        return (f"Plan: {p.get('name','')}\nBeschreibung: {p.get('description','')}\n"
                f"Aufgaben ({len(tasks)}):\n{tlines}")

    plan_texts = "\n\n---\n\n".join(f"## Plan {i+1}\n{_plan_summary(p)}" for i, p in enumerate(plans))

    prompt = (
        f"Du erhältst {len(plans)} Projektplan{'e' if len(plans)>1 else ''} zum gleichen Projektvorhaben. "
        f"Jeder Plan ist ca. 80 % korrekt und vollständig. Analysiere jeden Plan, identifiziere:\n"
        f"1. Stärken (gut durchdacht, vollständig)\n"
        f"2. Lücken (fehlende Aufgaben, falsche Abhängigkeiten, unrealistische Dauern)\n"
        f"Erstelle dann einen **verbesserten gemeinsamen Plan** der alle Stärken vereint (Ziel: ~99 % Korrektheit).\n\n"
        f"Antworte auf Deutsch. Gib am Ende den verbesserten Plan als JSON in folgendem Format aus:\n"
        f"```json\n{{\"name\":\"...\",\"description\":\"...\",\"tasks\":[{{\"id\":\"T1\",\"name\":\"...\","
        f"\"duration\":1,\"predecessors\":[],\"successors\":[\"T2\"]}},...]}}\n```\n\n"
        f"Hier die Pläne:\n\n{plan_texts}"
    )

    async def _stream():
        text_buf = ""
        _tok = {"in": 0, "out": 0}
        async with _model_session(model), httpx.AsyncClient(timeout=300) as client:
            async for chunk in _llm.stream(client, {
                "model": model, "think": False,
                "messages": [{"role": "system", "content": _SCIENCE_PROMPT},
                             {"role": "user", "content": prompt}],
                "stream": True,
            }):
                try:
                    token = chunk.get("message", {}).get("content", "")
                    if token:
                        text_buf += token
                        yield f"data: {json.dumps({'type': 'text', 'content': token})}\n\n"
                    if chunk.get("done"):
                        _a, _b = _llm_tok(chunk)
                        _tok["in"] += _a
                        _tok["out"] += _b
                        break
                except Exception:
                    pass

        # Verbesserten Plan aus JSON-Block extrahieren
        text_clean = re.sub(r"<think>.*?</think>", "", text_buf, flags=re.DOTALL).strip()
        m = re.search(r"```json\s*(\{.*?\})\s*```", text_clean, re.DOTALL)
        if not m:
            m = re.search(r"(\{\"name\".*\})", text_clean, re.DOTALL)
        if m:
            try:
                improved = json.loads(m.group(1))
                improved["name"] = improved.get("name", "Verbesserter Plan") + " (KI-Synthese)"
                yield f"data: {json.dumps({'type': 'plan', 'plan': improved})}\n\n"
            except Exception:
                pass
        yield f"data: {json.dumps({'type': 'done', 'tokens': _tok})}\n\n"

    return StreamingResponse(
        _stream(), media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


async def _ensure_plan_rag(plan: dict) -> dict:
    """Stellt sicher, dass der Plan eine eigene Wissensdatenbank besitzt
    (wird bei der ersten Tätigkeits-Recherche automatisch angelegt)."""
    cid = plan.get("rag_collection_id")
    coll = await _db.rag_get_collection(cid) if cid else None
    if coll:
        return coll
    from tools.rag import tier_config
    tc = tier_config("6gb")
    coll = {
        "id": f"rag_{uuid.uuid4().hex[:12]}",
        "name": (f"Plan: {plan.get('name', 'Plan')}")[:60],
        "embed_model": EMBED_MODEL,
        "tier": "plan",
        "chunk_size": tc["chunk_size"], "chunk_overlap": tc["chunk_overlap"],
        "top_k": tc["top_k"], "embed_gpu": False, "clean": True,
        "char_limit": tc["char_limit"], "strictness": "korrekt",
        "created_at": time.time(),
    }
    await _db.rag_create_collection(coll)
    plan["rag_collection_id"] = coll["id"]
    return coll


@app.post("/api/plans/{pid}/research-task")
async def plan_research_task(pid: str, req: Request):
    """Recherchiert eine einzelne Tätigkeit wissenschaftlich: adaptiver Agent →
    Web-Recherche → Markdown-Dossier → Einbettung ins plan-spezifische RAG →
    Verlinkung mit der Tätigkeit. Macht den Plan interaktiv (RAG je Plan)."""
    import re as _re
    from tools.search import search_with_sources
    from tools.rag import ingest_file

    body = await req.json()
    task_id = body.get("task_id")
    model = _pick_model(body.get("model"))
    fp = _plan_path_by_id(pid)
    if not fp or not fp.exists():
        raise HTTPException(status_code=404, detail="Plan nicht gefunden")
    plan = json.loads(fp.read_text(encoding="utf-8"))
    task = next((t for t in plan.get("tasks", []) if t.get("id") == task_id), None)
    if not task:
        raise HTTPException(status_code=404, detail="Tätigkeit nicht gefunden")

    coll = await _ensure_plan_rag(plan)
    tname = task.get("name", task_id)
    context = (plan.get("name", "") + " — " + (plan.get("description", "") or "")).strip(" —")
    query = f"{tname} {context}".strip()

    # 1. Adaptiver Agent aus der Tätigkeit ableiten
    _tok = {"in": 0, "out": 0}
    role, persona = await _derive_adaptive_prompt(
        f"Projektaufgabe: {tname}. Projektkontext: {context}", model, tok=_tok)

    # 2. Web-Recherche
    try:
        sources, search_text = await search_with_sources(query, 5)
    except Exception as e:
        sources, search_text = [], f"(Websuche fehlgeschlagen: {e})"

    # 3. Wissenschaftliche Synthese als Markdown
    _sys = "\n\n".join(p for p in (_SCIENCE_PROMPT, persona) if p)
    prompt = (
        f"Erstelle ein strukturiertes, wissenschaftlich sorgfältiges Kurzdossier in **Markdown** "
        f"zur Projekttätigkeit {tname} (Kontext: {context}). Stütze dich auf die folgenden "
        f"Suchergebnisse und zitiere Quellen mit Link. Erfinde nichts.\n\n"
        f"Suchergebnisse:\n{search_text[:6000]}\n\n"
        f"Gliederung:\n## {tname}\n### Überblick\n### Vorgehen / Methodik\n"
        f"### Wichtige Punkte & Belege\n### Risiken / Offene Fragen\n### Quellen"
    )
    try:
        async with _model_session(model), httpx.AsyncClient(timeout=300) as client:
            resp = await _llm.chat(client,{
                "model": model, "think": False, "stream": False,
                "messages": [{"role": "system", "content": _sys},
                             {"role": "user", "content": prompt}],
            })
            resp.raise_for_status()
            _rt_j = resp.json()
            _a, _b = _llm_tok(_rt_j)
            _tok["in"] += _a
            _tok["out"] += _b
            md = _rt_j.get("message", {}).get("content", "")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Synthese fehlgeschlagen: {e}")
    md = _re.sub(r"<think>.*?</think>", "", md, flags=_re.DOTALL).strip()
    # gesicherte Quellenliste anhängen (nicht vom Modell erfunden)
    if sources:
        md += "\n\n### Quellen\n" + "\n".join(
            f"- [{s.get('title', 'Quelle')}]({s.get('url', '')})" for s in sources if s.get("url"))

    # 4. Ins plan-spezifische RAG einbetten
    try:
        await ingest_file(coll, md, f"{task_id} – {tname}", f"doc_{uuid.uuid4().hex[:12]}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"RAG-Einbettung fehlgeschlagen: {e}")

    # 4b. Dossier automatisch als Markdown-Datei exportieren
    #     data/dossiers/<plan-slug>_<planid>/<task-slug>_<taskid>.md
    plan_folder = DOSSIERS_DIR / f"{_to_slug(plan.get('name', 'plan'))}_{pid[:8]}"
    plan_folder.mkdir(parents=True, exist_ok=True)
    md_path = plan_folder / f"{_to_slug(tname)}_{_to_slug(str(task_id))}.md"
    frontmatter = (
        "---\n"
        f"plan: {plan.get('name', '')}\n"
        f"task_id: {task_id}\n"
        f"task: {tname}\n"
        f"role: {role}\n"
        f"exported: {_dt.now().isoformat(timespec='seconds')}\n"
        f"sources: {len(sources)}\n"
        "---\n\n"
    )
    try:
        md_path.write_text(frontmatter + md, encoding="utf-8")
    except Exception as e:
        _write_log({"type": "error", "where": "dossier_export",
                    "file": md_path.name, "error": str(e)})
        md_path = None

    # 5. Dossier an die Tätigkeit hängen und Plan speichern
    task["doc"] = md
    task["doc_role"] = role
    task["researched"] = True
    if md_path:
        task["doc_file"] = str(md_path.relative_to(DATA_DIR)).replace("\\", "/")
    plan["updated_at"] = time.time()
    fp.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")

    return {"ok": True, "task_id": task_id, "role": role, "md": md,
            "collection_id": coll["id"], "collection_name": coll["name"],
            "n_sources": len(sources),
            "doc_file": task.get("doc_file"),
            "tokens": _tok}


@app.post("/api/plans/derive-agent")
async def plan_derive_agent(req: Request):
    """Leitet aus Projektbeschreibung + Ziel einen Projektplaner-Agenten (System-Prompt) ab.
    Dieser steuert anschließend die Aufgaben-/Ressourcenvorschläge."""
    import re
    body = await req.json()
    description = (body.get("description") or "").strip()
    if not description:
        raise HTTPException(400, "Keine Projektbeschreibung angegeben")
    _model = _pick_model(body.get("model"))

    async with _model_session(_model), httpx.AsyncClient(timeout=120) as client:
        resp = await _llm.chat(client,{
            "model": _model,
            "think": False,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "Du erstellst System-Prompts für KI-Projektplaner. "
                        "Antworte NUR mit dem fertigen System-Prompt als Fließtext, ohne Einleitung, "
                        "ohne Erklärung, ohne JSON, ohne Markdown."
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"Projektbeschreibung und Ziel:\n{description}\n\n"
                        "Erstelle den System-Prompt für einen fachkundigen Projektplaner-Agenten zu diesem Projekt.\n"
                        "Regeln:\n"
                        "- Beginne mit 'Du bist ...'\n"
                        "- Er soll passende Aufgaben, Abhängigkeiten, Dauern und Ressourcen "
                        "(Mensch/Hardware/Software) mit Zeiten und Kosten vorschlagen\n"
                        "- Antworte auf Deutsch, maximal 120 Wörter, nur Fließtext"
                    ),
                },
            ],
            "stream": False,
        })
        resp.raise_for_status()
        _da_j = resp.json()
        _da_ti, _da_to = _llm_tok(_da_j)
        raw = _da_j.get("message", {}).get("content", "")

    system_prompt = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL).strip()
    system_prompt = re.sub(r"^```[a-zA-Z]*\s*", "", system_prompt).strip()
    system_prompt = re.sub(r"\s*```$", "", system_prompt).strip()
    # Falls das Modell doch JSON lieferte, den system_prompt-Wert herausziehen
    if system_prompt.startswith("{"):
        ms = re.search(r'"system_prompt"\s*:\s*"([\s\S]+?)"\s*[},]', system_prompt)
        if ms:
            system_prompt = ms.group(1).strip()
    if not system_prompt or system_prompt.startswith("{"):
        system_prompt = (
            "Du bist ein erfahrener Projektplaner und schlägst zu diesem Projekt passende Aufgaben, "
            "Abhängigkeiten, Dauern und Ressourcen (Mensch/Hardware/Software) mit Zeiten und Kosten vor."
        )
    # Kurzname heuristisch aus den ersten sinnvollen Wörtern der Beschreibung
    words = re.findall(r"[A-Za-zÄÖÜäöüß0-9\-]{3,}", description)
    agent_name = " ".join(words[:3]) + "-Planer" if words else "Projektplaner"
    return {"agent_name": agent_name[:40], "system_prompt": system_prompt,
            "tokens": {"in": _da_ti, "out": _da_to}}


# Schlüsselwörter zur Typ-Erkennung. Mensch wird ZUERST geprüft, damit Rollen wie
# "Softwareentwickler" nicht fälschlich als Software klassifiziert werden.
_HUMAN_KW = (
    "ingenieur", "techniker", "entwickler", "leiter", "mitarbeiter", "monteur", "planer",
    "analyst", "experte", "expertin", "redakteur", "prüfer", "pruefer", "admin", "manager",
    "berater", "konstrukteur", "mechaniker", "mechatroniker", "elektroniker", "elektrotechnik",
    "elektromechanik", "programmierer", "designer", "architekt", "tester", "trainer",
    "einkauf", "einkäufer", "einkaeufer", "controller", "controlling", "justiziar",
    "geschäftsführung", "geschaeftsfuehrung", "scientist", "fachkraft", "personal",
    "pilot", "operator", "bediener", "wissenschaftler", "sachbearbeiter", "assistenz",
    "praktikant", "werkstudent", "schulungs", "kraft", "team", "rolle",
)
_HARDWARE_KW = (
    "sensor", "server", "gpu", "grafikkarte", "rechner", "workstation", "laptop", " pc",
    "maschine", "gerät", "geraet", "drucker", "kabel", "rack", "motor", "welle", "encoder",
    "nas", "switch", "usv", "messgerät", "messgeraet", "messstreifen", "dehnungsmess",
    "kamera", "roboter", "antrieb", "netzteil", "batterie", "akku", "scheibe", "prüfstand",
    "pruefstand", "prüfling", "hubwagen", "werkzeug", "anlage", "platine", "bauteil",
    "komponente", "hardware", "speicher", "storage", "festplatte", "ssd", "router",
)
_SOFTWARE_KW = (
    "lizenz", "software", "labview", "cad", "solidworks", "autocad", "fusion", "revit",
    "python", "matlab", "simulink", "simulation", "tool", "programm", " app", "datenbank",
    "suite", "runtime", "betriebssystem", "plugin", "framework", "office", "abonnement",
    "saas", "api", "ollama", "grafana", "docker", "vektor-db", "backup-software",
)


def _classify_resource_kind(name: str):
    """Errät den Ressourcentyp anhand von Schlüsselwörtern im Namen.
    Gibt 'human'/'hardware'/'software' zurück oder None, wenn unsicher."""
    n = " " + (name or "").lower() + " "
    if any(k in n for k in _HUMAN_KW):
        return "human"
    if any(k in n for k in _HARDWARE_KW):
        return "hardware"
    if any(k in n for k in _SOFTWARE_KW):
        return "software"
    return None


def _coerce_resource(r: dict) -> dict:
    """Normalisiert eine Ressourcen-Angabe der KI auf das interne Schema.
    Korrigiert den Typ per Heuristik, wenn der Name eindeutig ist."""
    name = str(r.get("name", "")).strip()[:80]
    kind = str(r.get("kind", "human")).lower().strip()
    if kind not in ("human", "hardware", "software"):
        kind = "human"
    guessed = _classify_resource_kind(name)
    if guessed:
        kind = guessed
    def _num(v):
        try:
            return max(0, float(v))
        except Exception:
            return 0
    return {
        "kind": kind,
        "name": name,
        "qty": _num(r.get("qty", 1)) or 1,
        "hours": _num(r.get("hours", 0)),
        "rate": _num(r.get("rate", 0)),
    }


def _norm_name(s: str) -> str:
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


def _normalize_catalog(catalog) -> list:
    """Bringt einen Ressourcen-Katalog auf {kind, name, rate}."""
    out = []
    for c in (catalog or []):
        if not isinstance(c, dict):
            continue
        name = str(c.get("name", "")).strip()[:80]
        if not name:
            continue
        kind = str(c.get("kind", "")).lower().strip()
        if kind not in ("human", "hardware", "software"):
            kind = _classify_resource_kind(name) or "human"
        try:
            rate = max(0, float(c.get("rate", 0)))
        except Exception:
            rate = 0
        out.append({"kind": kind, "name": name, "rate": rate})
    return out


def _apply_catalog_to_resources(res_list: list, catalog: list, mode: str) -> list:
    """Gleicht Ressourcen mit dem Katalog ab.
    - 'strict': nur Katalog-Ressourcen behalten (Typ+Satz aus Katalog).
    - 'extend': Treffer an Katalog angleichen, neue (Zukauf) behalten.
    - sonst: unverändert."""
    if not catalog or mode not in ("strict", "extend"):
        return res_list
    out = []
    for r in res_list:
        match = _match_catalog(r.get("name", ""), catalog)
        if match:
            r = {**r, "kind": match["kind"], "rate": match["rate"], "name": match["name"]}
            out.append(r)
        elif mode == "extend":
            r = {**r, "from_catalog": False}  # Zukauf / Ergänzung
            out.append(r)
        # strict + kein Treffer → verwerfen
    return out


def _catalog_prompt(catalog: list, mode: str) -> str:
    """Erzeugt den Katalog-Hinweis für das LLM."""
    if not catalog:
        return ""
    lines = []
    for c in catalog[:60]:
        unit = "€/h" if c["kind"] == "human" else "€/Einheit"
        lines.append(f"- [{c['kind']}] {c['name']} ({c['rate']:.0f} {unit})")
    cat = "Verfügbarer Ressourcen-Katalog:\n" + "\n".join(lines) + "\n\n"
    if mode == "strict":
        cat += ("Verwende AUSSCHLIESSLICH Ressourcen aus diesem Katalog mit den angegebenen "
                "Kostensätzen. Erfinde keine neuen Ressourcen.\n\n")
    elif mode == "extend":
        cat += ("Nutze bevorzugt Ressourcen aus diesem Katalog (mit den angegebenen Sätzen). "
                "Nur wenn nötig, darfst du zusätzliche Ressourcen ergänzen (Zukauf).\n\n")
    return cat


def _coerce_candidate(c: dict) -> dict:
    try:
        dur = max(0, float(c.get("duration", 1)))
    except Exception:
        dur = 1
    res = [_coerce_resource(r) for r in (c.get("resources") or []) if isinstance(r, dict)][:6]
    return {"name": str(c.get("name", "")).strip()[:120], "duration": dur, "resources": res}


@app.post("/api/plans/suggest-tasks")
async def suggest_tasks(req: Request):
    """Schlägt zu einer Aufgabe mehrere mögliche Vorgänger und Nachfolger vor
    (mit Dauer und Ressourcen) – zur Auswahl, nicht automatisch übernommen."""
    import re
    body = await req.json()
    _model = _pick_model(body.get("model"))
    system_prompt = (body.get("system_prompt") or "").strip() or (
        "Du bist ein erfahrener Projektplaner."
    )
    description = (body.get("description") or "").strip()
    tasks = body.get("tasks", [])
    anchor = body.get("anchor") or {}
    catalog = _normalize_catalog(body.get("resource_catalog"))
    res_mode = str(body.get("resource_mode", "free")).lower().strip()
    no_pred = bool(anchor.get("is_start"))   # Projektstart → keine Vorgänger
    no_succ = bool(anchor.get("is_end"))     # Projektende → keine Nachfolger
    tasks_summary = json.dumps(
        [{"id": t.get("id"), "name": t.get("name"), "duration": t.get("duration")} for t in tasks],
        ensure_ascii=False,
    )

    if no_pred and no_succ:
        return {"predecessors": [], "successors": []}

    if no_pred:
        scope = ("Diese Aufgabe ist als PROJEKTSTART markiert. Schlage KEINE Vorgänger vor "
                 "(predecessors=[]), nur bis zu 3 direkte NACHFOLGER.")
    elif no_succ:
        scope = ("Diese Aufgabe ist als PROJEKTENDE markiert. Schlage KEINE Nachfolger vor "
                 "(successors=[]), nur bis zu 3 direkte VORGÄNGER.")
    else:
        scope = "Schlage bis zu 3 sinnvolle direkte VORGÄNGER und bis zu 3 direkte NACHFOLGER dieser Aufgabe vor."

    user = (
        (f"Projektkontext: {description}\n\n" if description else "")
        + f"Bereits vorhandene Aufgaben: {tasks_summary}\n\n"
        + f"Betrachtete Aufgabe: \"{anchor.get('name', '')}\" (Dauer {anchor.get('duration', '?')} Tage)\n\n"
        + scope + " "
        + "Gib für jeden Vorschlag einen kurzen Namen, die Dauer in Tagen und die nötigen Ressourcen an "
        + "(Mensch/Hardware/Software) mit Menge, grober Zeit in Stunden und Kostensatz in Euro pro Stunde "
        + "(bei Hardware/Software pro Einheit, hours=0).\n\n"
        + _catalog_prompt(catalog, res_mode)
        + "Antworte NUR mit JSON in genau diesem Format, ohne Markdown, ohne Erklärung:\n"
        + '{"predecessors":[{"name":"Teile bestellen","duration":5,'
        + '"resources":[{"kind":"human","name":"Einkäufer","qty":1,"hours":4,"rate":55}]}],'
        + '"successors":[{"name":"Inbetriebnahme","duration":3,"resources":[]}]}\n'
        + "kind ist genau einer von: human, hardware, software."
    )

    async with _model_session(_model), httpx.AsyncClient(timeout=180) as client:
        resp = await _llm.chat(client,{
            "model": _model,
            "think": False,
            "messages": [
                {"role": "system", "content": system_prompt + " Antworte ausschließlich mit gültigem JSON."},
                {"role": "user", "content": user},
            ],
            "stream": False,
        })
        resp.raise_for_status()
        _st_j = resp.json()
        _st_ti, _st_to = _llm_tok(_st_j)
        raw = _st_j.get("message", {}).get("content", "")

    raw = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL).strip()
    raw = re.sub(r"^```[a-zA-Z]*\s*", "", raw).strip()
    raw = re.sub(r"\s*```$", "", raw).strip()
    preds, succs = [], []
    m = re.search(r"\{[\s\S]*\}", raw)
    if m:
        try:
            data = json.loads(m.group(0))
            preds = [_coerce_candidate(c) for c in (data.get("predecessors") or []) if isinstance(c, dict)][:3]
            succs = [_coerce_candidate(c) for c in (data.get("successors") or []) if isinstance(c, dict)][:3]
        except Exception:
            pass
    if no_pred:
        preds = []
    if no_succ:
        succs = []
    for c in preds + succs:
        c["resources"] = _apply_catalog_to_resources(c.get("resources", []), catalog, res_mode)
    return {"predecessors": preds, "successors": succs,
            "tokens": {"in": _st_ti, "out": _st_to}}


@app.post("/api/plans/detail-task")
async def detail_task(req: Request):
    """Detailliert eine ausgewählte Aufgabe per LLM: verfeinert Bezeichnung,
    Dauer, Beschreibung und Ressourcen und schlägt zusätzlich Vorgänger und
    Nachfolger vor. Alle Werte sind im Frontend wähl- und editierbar."""
    body = await req.json()
    _model = _pick_model(body.get("model"))
    system_prompt = (body.get("system_prompt") or "").strip() or "Du bist ein erfahrener Projektplaner."
    description = (body.get("description") or "").strip()
    tasks = body.get("tasks", [])
    task = body.get("task") or {}
    catalog = _normalize_catalog(body.get("resource_catalog"))
    res_mode = str(body.get("resource_mode", "free")).lower().strip()
    no_pred = bool(task.get("is_start"))
    no_succ = bool(task.get("is_end"))

    cur_res = ", ".join(
        f"{r.get('kind','')}:{r.get('name','')}" for r in (task.get("resource_list") or [])
    ) or "—"
    tasks_summary = json.dumps(
        [{"id": t.get("id"), "name": t.get("name")} for t in tasks if t.get("id") != task.get("id")],
        ensure_ascii=False,
    )

    if no_pred and no_succ:
        scope = "Schlage KEINE Vorgänger und KEINE Nachfolger vor (predecessors=[], successors=[])."
    elif no_pred:
        scope = "Diese Aufgabe ist PROJEKTSTART: predecessors=[], aber bis zu 3 Nachfolger."
    elif no_succ:
        scope = "Diese Aufgabe ist PROJEKTENDE: successors=[], aber bis zu 3 Vorgänger."
    else:
        scope = "Schlage zusätzlich bis zu 3 sinnvolle direkte Vorgänger und bis zu 3 Nachfolger vor."

    user = (
        (f"Projektkontext: {description}\n\n" if description else "")
        + f"Andere Aufgaben: {tasks_summary}\n\n"
        + f"Zu detaillierende Aufgabe: \"{task.get('name', '')}\" "
        + f"(aktuelle Dauer {task.get('duration', '?')} Tage, aktuelle Ressourcen: {cur_res}).\n\n"
        + "Detailliere DIESE Aufgabe: eine präzisere Bezeichnung (name), eine realistische "
        + "Dauer in Tagen (duration), eine kurze Detailbeschreibung in 1–2 Sätzen (notes) und die "
        + "nötigen Ressourcen (Mensch/Hardware/Software mit Menge, Stunden, Kostensatz €). "
        + scope + "\n\n"
        + _catalog_prompt(catalog, res_mode)
        + "Antworte NUR mit JSON in genau diesem Format, ohne Markdown, ohne Erklärung:\n"
        + '{"detail":{"name":"...","duration":5,"notes":"...","resources":[{"kind":"human","name":"...","qty":1,"hours":8,"rate":80}]},'
        + '"predecessors":[{"name":"...","duration":3,"resources":[]}],'
        + '"successors":[{"name":"...","duration":2,"resources":[]}]}\n'
        + "kind ist genau einer von: human, hardware, software."
    )

    async with _model_session(_model), httpx.AsyncClient(timeout=180) as client:
        resp = await _llm.chat(client,{
            "model": _model,
            "think": False,
            "messages": [
                {"role": "system", "content": system_prompt + " Antworte ausschließlich mit gültigem JSON."},
                {"role": "user", "content": user},
            ],
            "stream": False,
        })
        resp.raise_for_status()
        _dt_j = resp.json()
        _dt_ti, _dt_to = _llm_tok(_dt_j)
        raw = _dt_j.get("message", {}).get("content", "")

    raw = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL).strip()
    raw = re.sub(r"^```[a-zA-Z]*\s*", "", raw).strip()
    raw = re.sub(r"\s*```$", "", raw).strip()

    # Detail mit aktuellen Werten als Fallback
    try:
        cur_dur = float(task.get("duration", 1))
    except Exception:
        cur_dur = 1
    detail = {
        "name": str(task.get("name", "")),
        "duration": cur_dur,
        "notes": str(task.get("notes", "")),
        "resources": [],
    }
    preds, succs = [], []
    m = re.search(r"\{[\s\S]*\}", raw)
    if m:
        try:
            data = json.loads(m.group(0))
            d = data.get("detail") or {}
            if d.get("name"):
                detail["name"] = str(d["name"]).strip()[:120]
            try:
                detail["duration"] = max(0, float(d.get("duration", cur_dur)))
            except Exception:
                pass
            if d.get("notes"):
                detail["notes"] = str(d["notes"]).strip()[:400]
            detail["resources"] = [_coerce_resource(r) for r in (d.get("resources") or []) if isinstance(r, dict)][:6]
            preds = [_coerce_candidate(c) for c in (data.get("predecessors") or []) if isinstance(c, dict)][:3]
            succs = [_coerce_candidate(c) for c in (data.get("successors") or []) if isinstance(c, dict)][:3]
        except Exception:
            pass

    if no_pred:
        preds = []
    if no_succ:
        succs = []
    detail["resources"] = _apply_catalog_to_resources(detail["resources"], catalog, res_mode)
    for c in preds + succs:
        c["resources"] = _apply_catalog_to_resources(c.get("resources", []), catalog, res_mode)
    return {"detail": detail, "predecessors": preds, "successors": succs,
            "tokens": {"in": _dt_ti, "out": _dt_to}}


@app.post("/api/plans/insert-between")
async def insert_between(req: Request):
    """Schlägt per LLM 1–3 sinnvolle Zwischenvorgänge vor, die zwischen zwei
    Aufgaben A und B passen. Die KI liest Bezeichnung/Notizen beider Aufgaben
    und überlegt, welche Tätigkeit die Lücke schließt. Auswahl/Editieren im
    Frontend; das Frontend verdrahtet anschließend A→neu→B."""
    body = await req.json()
    _model = _pick_model(body.get("model"))
    system_prompt = (body.get("system_prompt") or "").strip() or "Du bist ein erfahrener Projektplaner."
    description = (body.get("description") or "").strip()
    a = body.get("task_a") or {}
    b = body.get("task_b") or {}
    catalog = _normalize_catalog(body.get("resource_catalog"))
    res_mode = str(body.get("resource_mode", "free")).lower().strip()

    def _desc(t):
        parts = [str(t.get("name", "")).strip()]
        if t.get("notes"):
            parts.append(f"(Notiz: {str(t['notes']).strip()})")
        return " ".join(p for p in parts if p) or "?"

    user = (
        (f"Projektkontext: {description}\n\n" if description else "")
        + f"Aufgabe A (Vorgänger): \"{_desc(a)}\", Dauer {a.get('duration', '?')} Tage.\n"
        + f"Aufgabe B (Nachfolger): \"{_desc(b)}\", Dauer {b.get('duration', '?')} Tage.\n\n"
        + "Überlege, welche 1–3 Tätigkeiten logisch ZWISCHEN A und B liegen müssen, "
        + "damit der Ablauf von A nach B vollständig und konsistent ist. Jede Tätigkeit "
        + "hat name, duration (Tage) und resources (Mensch/Hardware/Software mit Menge, "
        + "Stunden, Kostensatz €).\n\n"
        + _catalog_prompt(catalog, res_mode)
        + "Antworte NUR mit JSON in genau diesem Format, ohne Markdown, ohne Erklärung:\n"
        + '{"tasks":[{"name":"...","duration":3,"notes":"...","resources":[{"kind":"human","name":"...","qty":1,"hours":8,"rate":80}]}]}\n'
        + "kind ist genau einer von: human, hardware, software."
    )

    async with _model_session(_model), httpx.AsyncClient(timeout=180) as client:
        resp = await _llm.chat(client,{
            "model": _model,
            "think": False,
            "messages": [
                {"role": "system", "content": system_prompt + " Antworte ausschließlich mit gültigem JSON."},
                {"role": "user", "content": user},
            ],
            "stream": False,
        })
        resp.raise_for_status()
        _ib_j = resp.json()
        _ib_ti, _ib_to = _llm_tok(_ib_j)
        raw = _ib_j.get("message", {}).get("content", "")

    raw = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL).strip()
    raw = re.sub(r"^```[a-zA-Z]*\s*", "", raw).strip()
    raw = re.sub(r"\s*```$", "", raw).strip()

    tasks = []
    m = re.search(r"\{[\s\S]*\}", raw)
    if m:
        try:
            data = json.loads(m.group(0))
            for c in (data.get("tasks") or [])[:3]:
                if not isinstance(c, dict):
                    continue
                cand = _coerce_candidate(c)
                cand["notes"] = str(c.get("notes", "")).strip()[:400]
                cand["resources"] = _apply_catalog_to_resources(cand.get("resources", []), catalog, res_mode)
                tasks.append(cand)
        except Exception:
            pass

    return {"tasks": tasks, "tokens": {"in": _ib_ti, "out": _ib_to}}


@app.post("/api/plans/from-list")
async def plan_from_list(req: Request):
    """Konvertiert eine flache Aufgabenliste (aus Anfrage/Ausschreibung) in einen
    strukturierten Projektplan mit Abhängigkeiten, Dauern und Bereichen.
    Streaming-SSE-Endpunkt."""
    import re

    body = await req.json()
    task_list = (body.get("task_list") or "").strip()
    if not task_list:
        raise HTTPException(400, "Keine Aufgabenliste angegeben")
    name = (body.get("name") or "Projekt aus Liste").strip()
    _model = _pick_model(body.get("model"))

    async def _gen():
        yield _sse({"type": "status", "message": "Analysiere Aufgabenliste…"})
        system = (
            "Du bist ein erfahrener Projektmanager. Du erhältst eine einfache, unstrukturierte "
            "Aufgabenliste (z. B. aus einer Anfrage oder Ausschreibung). Deine Aufgabe: "
            "Erstelle daraus einen vollständigen, logisch geordneten Projektplan mit realistischen "
            "Dauern und Abhängigkeiten. Fasse verwandte Tätigkeiten in Bereiche (area) zusammen."
        )
        user = (
            f"Aufgabenliste:\n{task_list}\n\n"
            "Erzeuge einen strukturierten Projektplan. Verwende fortlaufende IDs T1, T2, … "
            "Weise jeder Aufgabe einen 'area' (Bereich/Phase, z. B. 'Planung', 'Konstruktion', "
            "'Test', 'Abnahme') zu. Schätze realistische Dauern in Arbeitstagen. "
            "Setze 'predecessors' als Liste direkter Vorgänger-IDs.\n\n"
            "Antworte NUR mit JSON ohne Markdown, ohne Erklärung:\n"
            '{"tasks":[{"id":"T1","name":"Aufgabe","duration":3,"predecessors":[],"area":"Planung"},'
            '{"id":"T2","name":"Aufgabe","duration":2,"predecessors":["T1"],"area":"Planung"}]}'
        )
        payload = {
            "model": _model,
            "think": False,
            "format": "json",
            "messages": [
                {"role": "system", "content": system + " Antworte ausschließlich mit gültigem JSON."},
                {"role": "user",   "content": user},
            ],
            "stream": False,
            "options": {"num_ctx": 8192},
        }
        try:
            async with _model_session(_model), httpx.AsyncClient(timeout=300) as client:
                resp = await _llm.chat(client,payload)
                resp.raise_for_status()
                _fl_j = resp.json()
                _fl_ti, _fl_to = _llm_tok(_fl_j)
                raw = _fl_j.get("message", {}).get("content", "")
        except Exception as e:
            yield _sse({"type": "error", "message": str(e)})
            return

        yield _sse({"type": "status", "message": "Verarbeite Antwort…"})

        raw = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL).strip()
        raw = re.sub(r"^```[a-zA-Z]*\s*", "", raw).strip()
        raw = re.sub(r"\s*```$", "", raw).strip()

        rawtasks = []
        m = re.search(r"\{[\s\S]*\}", raw)
        if m:
            try:
                rawtasks = json.loads(m.group(0)).get("tasks") or []
            except Exception:
                rawtasks = []

        if not rawtasks:
            yield _sse({"type": "error", "message":
                "Das Modell lieferte kein verwertbares JSON. Bitte ein größeres Modell verwenden."})
            return

        tasks, seen = [], set()
        for i, t in enumerate(rawtasks, start=1):
            if not isinstance(t, dict):
                continue
            tid = str(t.get("id") or f"T{i}").strip() or f"T{i}"
            while tid in seen:
                tid = f"{tid}_{i}"
            seen.add(tid)
            try:
                dur = max(1, float(t.get("duration", 1)))
            except Exception:
                dur = 1
            preds = [str(p).strip() for p in (t.get("predecessors") or []) if str(p).strip()]
            tasks.append({
                "id": tid,
                "name": str(t.get("name", tid)).strip()[:120],
                "duration": dur,
                "predecessors": preds,
                "successors": [],
                "resources": "",
                "resource_list": [],
                "notes": "",
                "area": str(t.get("area") or "").strip()[:40],
                "is_start": False,
                "is_end": False,
            })

        ids = {t["id"] for t in tasks}
        by_id = {t["id"]: t for t in tasks}
        for t in tasks:
            t["predecessors"] = [p for p in t["predecessors"] if p in ids and p != t["id"]]
        for t in tasks:
            for p in t["predecessors"]:
                by_id[p]["successors"].append(t["id"])
        for t in tasks:
            if not t["predecessors"]:
                t["is_start"] = True
            if not t["successors"]:
                t["is_end"] = True

        plan = {"name": name, "description": "", "tasks": tasks}
        yield _sse({"type": "plan", "plan": plan, "tokens": {"in": _fl_ti, "out": _fl_to}})

    return StreamingResponse(_gen(), media_type="text/event-stream")


# ── Intelligente Verknüpfung / Auto-Strukturierung ────────────────────────────

def _reachable(start: str, target: str, preds: dict) -> bool:
    """Ist ``target`` ein (transitiver) Vorgänger von ``start`` (folgt preds)?"""
    stack = list(preds.get(start, ()))
    seen = set()
    while stack:
        n = stack.pop()
        if n == target:
            return True
        if n in seen:
            continue
        seen.add(n)
        stack.extend(preds.get(n, ()))
    return False


def _add_edge(p: str, t: str, preds: dict) -> bool:
    """Setzt ``p`` als Vorgänger von ``t``, falls das weder Zyklus noch Redundanz
    erzeugt. Gibt True zurück, wenn eine Kante hinzugefügt wurde."""
    if p == t or p in preds[t]:
        return False
    if _reachable(p, t, preds):   # t ist schon Vorgänger von p → würde Zyklus bilden
        return False
    if _reachable(t, p, preds):   # p ist bereits (transitiv) Vorgänger von t → redundant
        return False
    preds[t].add(p)
    return True


def _topo_order(ids: list, preds: dict) -> list:
    from collections import deque
    indeg = {i: 0 for i in ids}
    succ = {i: [] for i in ids}
    for t in ids:
        for p in preds.get(t, ()):
            if p in indeg:
                succ[p].append(t)
                indeg[t] += 1
    q = deque([i for i in ids if indeg[i] == 0])
    order = []
    while q:
        n = q.popleft()
        order.append(n)
        for s in succ[n]:
            indeg[s] -= 1
            if indeg[s] == 0:
                q.append(s)
    order += [i for i in ids if i not in order]   # evtl. Restzyklen hinten anhängen
    return order


@app.post("/api/plans/auto-structure")
async def auto_structure_plan(req: Request):
    """Schlägt für BESTEHENDE Aufgaben automatisch Phasen + Abhängigkeiten vor
    (fachlich via LLM), optional mit Ressourcen-Entzerrung (gleiche Rolle nicht
    parallel) und ohne künstliche Verkettung unabhängiger Stränge. Liefert
    {links:[{id,predecessors,area}], stats:{…}} — der Plan wird NICHT gespeichert;
    das Frontend zeigt eine Vorschau und wendet sie auf Bestätigung an."""
    body = await req.json()
    tasks_in = body.get("tasks") or []
    if not tasks_in:
        raise HTTPException(status_code=400, detail="Keine Aufgaben übergeben")
    opts = body.get("options") or {}
    want_deps = opts.get("dependencies", True)
    want_phases = opts.get("phases", True)
    want_leveling = bool(opts.get("resource_leveling"))
    model = _pick_model(body.get("model"), _model_for("general"))
    description = (body.get("description") or "").strip()

    ids, name_by, roles_by, area_by = [], {}, {}, {}
    for i, t in enumerate(tasks_in, 1):
        tid = str(t.get("id") or f"T{i}").strip() or f"T{i}"
        if tid in name_by:
            continue
        ids.append(tid)
        name_by[tid] = str(t.get("name", tid))[:120]
        roles_by[tid] = [str(r).strip() for r in (t.get("roles") or []) if str(r).strip()]
        area_by[tid] = str(t.get("area") or "").strip()[:40]
    idset = set(ids)
    preds = {i: set() for i in ids}
    area_out = dict(area_by)
    _as_ti, _as_to = 0, 0

    # 1) Fachliche Abhängigkeiten + Phasen via LLM
    if want_deps or want_phases:
        listing = "\n".join(
            f"- {tid}: {name_by[tid]}" + (f"  [Bereich: {area_by[tid]}]" if area_by[tid] else "")
            for tid in ids)
        sys = (
            "Du bist erfahrener Projektmanager. Du erhältst eine Liste bestehender "
            "Projektaufgaben mit IDs. Bestimme die LOGISCHE Struktur: welche Aufgabe muss "
            "vor welcher fertig sein (direkte Vorgänger), und ordne jede Aufgabe einer "
            "Projektphase (area) zu. Verkette NICHT künstlich — fachlich unabhängige Aufgaben "
            "dürfen parallel bleiben. Verwende AUSSCHLIESSLICH die vorgegebenen IDs. "
            'Antworte NUR mit JSON: {"links":[{"id":"T1","predecessors":["T2"],"area":"Konstruktion"}]}.'
        )
        usr = (f"Projektkontext: {description}\n\n" if description else "") + f"Aufgaben:\n{listing}"
        try:
            async with _model_session(model), httpx.AsyncClient(timeout=300) as client:
                resp = await _llm.chat(client, {
                    "model": model, "think": False, "stream": False, "format": "json",
                    "messages": [{"role": "system", "content": sys}, {"role": "user", "content": usr}],
                    "options": {"num_ctx": _profile_num_ctx()}, "keep_alive": KEEP_ALIVE,
                })
                resp.raise_for_status()
            _as_j = resp.json()
            _as_ti, _as_to = _llm_tok(_as_j)
            data = _parse_llm_json(_as_j.get("message", {}).get("content", "")) or {}
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Strukturierung fehlgeschlagen: {e}")
        for link in (data.get("links") or []):
            if not isinstance(link, dict):
                continue
            tid = str(link.get("id", "")).strip()
            if tid not in idset:
                continue
            if want_phases:
                a = str(link.get("area") or "").strip()[:40]
                if a:
                    area_out[tid] = a
            if want_deps:
                for p in (link.get("predecessors") or []):
                    p = str(p).strip()
                    if p in idset:
                        _add_edge(p, tid, preds)

    dep_links = sum(len(v) for v in preds.values())

    # 2) Ressourcen-Entzerrung: Aufgaben gleicher Rolle nacheinander legen
    leveled = 0
    if want_leveling:
        order = _topo_order(ids, preds)
        rank = {tid: n for n, tid in enumerate(order)}
        role_tasks = {}
        for tid in ids:
            for role in roles_by[tid]:
                role_tasks.setdefault(role.lower(), []).append(tid)
        for tlist in role_tasks.values():
            if len(tlist) < 2:
                continue
            tlist = sorted(tlist, key=lambda x: rank.get(x, 0))
            for a, b in zip(tlist, tlist[1:]):
                if _add_edge(a, b, preds):
                    leveled += 1

    links = [{"id": tid, "predecessors": sorted(preds[tid]), "area": area_out.get(tid, "")}
             for tid in ids]
    n_phases = len({a for a in area_out.values() if a})
    return {"links": links, "stats": {"tasks": len(ids), "dep_links": dep_links,
                                      "leveled_links": leveled, "phases": n_phases},
            "tokens": {"in": _as_ti, "out": _as_to}}


async def _generate_plan_core(_model, description, max_tasks, system_prompt="",
                              catalog=None, res_mode="free", rag_context="", num_ctx=None):
    """Kern der Plan-Generierung (Aufgaben mit Dauer, Abhängigkeiten, Ressourcen) per
    lokalem LLM. Gemeinsam genutzt von /api/plans/generate und /api/plans/from-document.
    ``num_ctx`` erzwingt ein größeres Kontextfenster (z. B. großes Dokument auf starkem
    Rechner). Nachfolger werden aus den Vorgängern abgeleitet."""
    import re
    # Keine harte 20er-Grenze – nur ein großzügiges Sicherheitsnetz gegen Ausreißer.
    max_tasks = max(5, min(int(max_tasks or 12), 300))
    big_request = max_tasks > 30
    system_prompt = (system_prompt or "").strip() or (
        "Du bist ein erfahrener Projektplaner und zerlegst Projekte in sinnvolle, "
        "chronologisch abhängige Arbeitspakete."
    )
    catalog = catalog or []
    res_mode = str(res_mode or "free").lower().strip()

    user = (
        (f"Verfügbares Hintergrundwissen (als Grundlage nutzen, nicht erfinden):\n{rag_context}\n\n" if rag_context else "") +
        f"Projektbeschreibung und Ziel:\n{description}\n\n"
        f"Erstelle einen vollständigen Projektplan mit {max_tasks} Aufgaben in sinnvoller Reihenfolge. "
        "Vergib fortlaufende IDs T1, T2, …. Jede Aufgabe hat: id, name, duration (Tage), "
        "predecessors (Liste der IDs direkter Vorgänger; die erste Aufgabe hat []), "
        "und resources (Mensch/Hardware/Software) mit Menge, Zeit in Stunden und Kostensatz in Euro "
        "(bei Hardware/Software pro Einheit, hours=0).\n\n"
        + _catalog_prompt(catalog, res_mode) +
        "Antworte NUR mit JSON in genau diesem Format, ohne Markdown, ohne Erklärung:\n"
        '{"tasks":[{"id":"T1","name":"Anforderungen klären","duration":3,"predecessors":[],'
        '"resources":[{"kind":"human","name":"Projektleiter","qty":1,"hours":16,"rate":90}]},'
        '{"id":"T2","name":"Konzept erstellen","duration":5,"predecessors":["T1"],"resources":[]}]}\n'
        "kind ist genau einer von: human, hardware, software.\n\n"
        f"WICHTIG: Der Plan MUSS GENAU {max_tasks} Aufgaben enthalten (IDs T1 bis "
        f"T{max_tasks}) – nicht weniger und nicht mehr. Zerlege das Vorhaben fein genug, "
        f"um auf {max_tasks} sinnvolle Arbeitspakete zu kommen."
    )

    payload = {
        "model": _model,
        "think": False,
        "format": "json",   # erzwingt valides JSON → robuster gegen Geplapper
        "messages": [
            {"role": "system", "content": system_prompt + " Antworte ausschließlich mit gültigem JSON."},
            {"role": "user", "content": user},
        ],
        "stream": False,
    }
    if num_ctx:
        # explizit gewünschtes Kontextfenster (z. B. großes Dokument auf starkem Rechner)
        payload["options"] = {"num_ctx": int(num_ctx)}
    elif big_request:
        # größeres Kontextfenster, damit lange Pläne nicht abgeschnitten werden
        payload["options"] = {"num_ctx": 8192}

    async with _model_session(_model), httpx.AsyncClient(timeout=600 if big_request else 300) as client:
        resp = await _llm.chat(client,payload)
        resp.raise_for_status()
        _j = resp.json()
        raw = _j.get("message", {}).get("content", "")
    _plan_ti, _plan_to = _llm_tok(_j)

    raw = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL).strip()
    raw = re.sub(r"^```[a-zA-Z]*\s*", "", raw).strip()
    raw = re.sub(r"\s*```$", "", raw).strip()

    # Balancierte {…}-Objekte aus einem (evtl. abgeschnittenen) String ziehen.
    def _extract_objects(s: str) -> list:
        objs, depth, start = [], 0, -1
        for i, ch in enumerate(s):
            if ch == "{":
                if depth == 0:
                    start = i
                depth += 1
            elif ch == "}" and depth > 0:
                depth -= 1
                if depth == 0 and start >= 0:
                    objs.append(s[start:i + 1]); start = -1
        return objs

    rawtasks = []
    m = re.search(r"\{[\s\S]*\}", raw)
    if m:
        try:
            rawtasks = json.loads(m.group(0)).get("tasks") or []
        except Exception:
            rawtasks = []

    # Rettungs-Parser: bei abgeschnittenem/teilweisem JSON einzelne Aufgaben bergen
    if not rawtasks:
        bracket = raw.find("[", raw.find('"tasks"')) if '"tasks"' in raw else raw.find("[")
        segment = raw[bracket:] if bracket >= 0 else raw
        for objstr in _extract_objects(segment):
            try:
                obj = json.loads(objstr)
                if isinstance(obj, dict) and obj.get("name"):
                    rawtasks.append(obj)
            except Exception:
                pass

    if not rawtasks:
        raise HTTPException(502,
            "Das Modell lieferte keinen verwertbaren Plan – bei dieser Aufgabenzahl ist ein "
            "größeres/leistungsfähigeres Modell erforderlich. Alternativ weniger Aufgaben "
            "anfordern oder den Plan in Phasen generieren.")

    # Normalisieren: IDs eindeutig machen, Aufgaben säubern
    tasks, seen = [], set()
    for i, t in enumerate(rawtasks[:max_tasks], start=1):
        if not isinstance(t, dict):
            continue
        tid = str(t.get("id") or f"T{i}").strip() or f"T{i}"
        while tid in seen:
            tid = f"{tid}_{i}"
        seen.add(tid)
        try:
            dur = max(0, float(t.get("duration", 1)))
        except Exception:
            dur = 1
        res = [_coerce_resource(r) for r in (t.get("resources") or []) if isinstance(r, dict)][:6]
        res = _apply_catalog_to_resources(res, catalog, res_mode)
        preds = [str(p).strip() for p in (t.get("predecessors") or []) if str(p).strip()]
        tasks.append({
            "id": tid, "name": str(t.get("name", tid)).strip()[:120], "duration": dur,
            "predecessors": preds, "successors": [], "resources": "",
            "resource_list": res, "notes": "", "is_start": False, "is_end": False,
        })

    # Ungültige Vorgänger-Verweise entfernen, Nachfolger ableiten
    ids = {t["id"] for t in tasks}
    by_id = {t["id"]: t for t in tasks}
    for t in tasks:
        t["predecessors"] = [p for p in t["predecessors"] if p in ids and p != t["id"]]
    for t in tasks:
        for p in t["predecessors"]:
            by_id[p]["successors"].append(t["id"])
    # Start/Ende markieren
    for t in tasks:
        if not t["predecessors"]:
            t["is_start"] = True
        if not t["successors"]:
            t["is_end"] = True

    # Warnung: kleine lokale Modelle liefern bei großen Plänen oft unvollständig
    warning = ""
    if big_request:
        warning = (
            f"Großer Plan angefordert ({max_tasks} Aufgaben). Kleine lokale Modelle "
            "(z. B. ministral-3:3b) liefern dann oft unvollständige oder inkonsistente "
            "Pläne – für viele Aufgaben ein größeres/leistungsfähigeres Modell verwenden "
            "oder den Plan in Phasen generieren."
        )
    if len(tasks) < max_tasks * 0.8:
        warning = (warning + " " if warning else "") + (
            f"Das Modell lieferte nur {len(tasks)} von {max_tasks} angeforderten Aufgaben."
        )

    return {"name": "", "description": description, "tasks": tasks,
            "requested": max_tasks, "warning": warning.strip(),
            "tokens": {"in": _plan_ti, "out": _plan_to}}


async def _plan_rag_context(rag_collections, query: str) -> str:
    """Löst RAG-Collection-IDs (Liste oder kommagetrennt) auf und zieht Grounding-Kontext."""
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


@app.post("/api/plans/generate")
async def generate_plan(req: Request):
    """Generiert aus einer Projektbeschreibung einen vollständigen Projektplan
    (Aufgaben mit Dauer, Abhängigkeiten und Ressourcen) per lokalem LLM."""
    body = await req.json()
    _model = _pick_model(body.get("model"))
    description = (body.get("description") or "").strip()
    if not description:
        raise HTTPException(400, "Keine Projektbeschreibung angegeben")
    catalog = _normalize_catalog(body.get("resource_catalog"))
    res_mode = str(body.get("resource_mode", "free")).lower().strip()
    rag_context = await _plan_rag_context(body.get("rag_collections"), description)
    return await _generate_plan_core(
        _model, description, body.get("max_tasks", 12),
        body.get("system_prompt"), catalog, res_mode, rag_context)


@app.post("/api/plans/from-document")
async def plan_from_document(
    file: UploadFile = File(...),
    max_tasks: int = Form(12),
    model: str = Form(""),
    resource_mode: str = Form("free"),
    rag_collections: str = Form(""),
):
    """Importiert ein Dokument (z. B. Strategiepapier), leitet daraus die nötigen
    Ressourcen ab und erzeugt einen vollständigen Projektplan mit der gewünschten
    Aufgabenzahl. Auf einem leistungsfähigen Rechner sind auch große Pläne möglich."""
    import os
    _model = _pick_model(model or None)
    tmp = UPLOADS_DIR / f"plandoc_{uuid.uuid4().hex}_{file.filename}"
    async with aiofiles.open(tmp, "wb") as fh:
        await fh.write(await file.read())
    try:
        text = await asyncio.to_thread(_extract_text, tmp)
    finally:
        tmp.unlink(missing_ok=True)
    text = (text or "").strip()
    if not text or text.startswith("[Lesefehler"):
        raise HTTPException(400, f"Dokument „{file.filename}“ konnte nicht gelesen werden.")
    try:
        max_tasks = max(5, min(int(max_tasks), 300))
    except Exception:
        max_tasks = 12
    # Eingabebudget an das Kontextfenster koppeln (großer Rechner → großes num_ctx).
    num_ctx = max(8192, _profile_num_ctx())
    doc = text[: num_ctx * 2]
    rag_context = await _plan_rag_context(rag_collections, doc[:500])
    system_prompt = (
        "Du bist ein erfahrener Projektplaner. Lies das beigefügte Dokument (z. B. ein "
        "Strategiepapier), leite die nötigen Arbeitspakete und Ressourcen ab und zerlege "
        f"das Vorhaben in GENAU {max_tasks} sinnvolle, chronologisch abhängige Aufgaben. "
        f"Zerlege fein genug bzw. fasse passend zusammen, damit es exakt {max_tasks} "
        "Arbeitspakete werden."
    )
    description = (
        f"Aus folgendem Dokument „{file.filename}“ einen Projektplan ableiten. Stütze "
        "Aufgaben und Ressourcen ausschließlich auf den Inhalt, erfinde nichts hinzu.\n\n"
        f"--- DOKUMENT ---\n{doc}"
    )
    result = await _generate_plan_core(
        _model, description, max_tasks, system_prompt, [], resource_mode,
        rag_context, num_ctx=num_ctx)
    result["name"] = (os.path.splitext(file.filename or "")[0] or "Importierter Plan")[:120]
    result["description"] = (
        f"Automatisch aus „{file.filename}“ abgeleiteter Plan (Ziel: {max_tasks} Vorgänge)."
    )
    result["source_document"] = file.filename
    return result


# ── Nutzer-Feedback aus dem Chat („/-" Fehler, „/+" Idee) ─────────────────────
# „/- <Text>" protokolliert ein Problem/eine Fehlermeldung, „/+ <Text>" eine Idee
# bzw. einen Verbesserungsvorschlag. Alles landet als Markdown in FEEDBACK_FILE,
# gruppiert nach Art, mit Zeitstempel und (optional) der Unterhaltungs-ID.

_FEEDBACK_KINDS = {
    "problem": ("🔴 Fehler & Probleme", "🔴"),
    "idea":    ("🟢 Ideen & Verbesserungen", "🟢"),
}


def _read_feedback_md() -> str:
    try:
        return FEEDBACK_FILE.read_text(encoding="utf-8")
    except FileNotFoundError:
        return ""


def _append_feedback(kind: str, text: str, conversation_id: str = "") -> int:
    """Hängt einen Feedback-Eintrag an das Markdown-Protokoll an. Gibt die
    Gesamtzahl der Einträge zurück. Robust gegen fehlende Datei."""
    from datetime import datetime
    kind = kind if kind in _FEEDBACK_KINDS else "idea"
    _, icon = _FEEDBACK_KINDS[kind]
    text = (text or "").strip()
    ts = datetime.now().strftime("%Y-%m-%d %H:%M")
    existing = _read_feedback_md()
    if not existing:
        existing = (
            "# Nutzer-Feedback\n\n"
            "Im Chat erfasst: `/- …` meldet ein **Problem/einen Fehler**, "
            "`/+ …` notiert eine **Idee/einen Verbesserungsvorschlag**.\n"
        )
    conv = f" · _{conversation_id}_" if conversation_id else ""
    entry = f"\n- {icon} **[{ts}]**{conv} {text}\n"
    FEEDBACK_FILE.write_text(existing.rstrip() + "\n" + entry, encoding="utf-8")
    # Einträge zählen (Listenzeilen mit einem der Icons)
    body = _read_feedback_md()
    return sum(1 for ln in body.splitlines()
               if ln.lstrip().startswith(("- 🔴", "- 🟢")))


@app.post("/api/feedback")
async def add_feedback(req: Request):
    """Speichert Nutzer-Feedback aus dem Chat als Markdown-Protokoll.
    Body: ``{"kind": "problem"|"idea", "text": "…", "conversation_id": "…"}``."""
    body = await req.json()
    text = (body.get("text") or "").strip()
    if not text:
        raise HTTPException(400, "Kein Feedback-Text angegeben")
    kind = "problem" if body.get("kind") == "problem" else "idea"
    count = await asyncio.to_thread(
        _append_feedback, kind, text[:4000], str(body.get("conversation_id") or "")[:80])
    return {"ok": True, "kind": kind, "count": count, "file": FEEDBACK_FILE.name}


@app.get("/api/feedback")
async def get_feedback():
    """Liefert das gesammelte Feedback-Protokoll (Markdown) zurück."""
    content = await asyncio.to_thread(_read_feedback_md)
    return {"markdown": content, "file": FEEDBACK_FILE.name}


# ── /plan — Chat-getriebener Strategie- & Einsatzplan-Orchestrator ────────────
# Aus dem Chat-Verlauf (Briefing) baut der Befehl `/plan` in einem Zug: eine
# Strategie (Markdown), die nötigen Beratungs-Agenten (Vorschlag), einen Einsatz-/
# Ressourcenplan (Planer-Schema, Vorschlag) und eine Bewertungs-Jury (Vorschlag).
# Es wird NICHTS gespeichert — das Frontend zeigt eine Vorschau und legt auf
# Bestätigung über die vorhandenen Endpoints (/api/agents, /api/plans, /api/juries) an.


class PlanStrategyRequest(BaseModel):
    brief: str = ""                 # Chat-Verlauf als Briefing (frei diskutiert)
    extra: str = ""                 # optionale Randbedingungen nach „/plan"
    model: str = ""                 # leer → general-Modell aus dem Profil
    web_search: bool = False
    rag_collections: List[str] = []
    count: int = 12                 # Zielanzahl Aufgaben im Einsatzplan (4–60)
    # Feste Agenten: per „/plan … /dsgvo /tisax" angepinnte, bereits vorhandene
    # Agenten, die in jedem Fall als Berater + Jury-Mitglied verwendet werden.
    # Jeder Eintrag: {id, name, description, system_prompt, icon, category, tools}.
    pinned_agents: List[dict] = []


async def _plan_ground(query: str, web: bool, rag_collections: list,
                       top_k: int = 5, char_budget: int = 3000) -> str:
    """Sammelt optionales Belegmaterial (Websuche + RAG) wie beim Deepdive.
    ``top_k``/``char_budget`` skalieren den RAG-Abruf — für die Planerzeugung wird
    bewusst mehr aus der hinterlegten Datei gezogen, damit das ganze Dokument abgedeckt ist."""
    blocks = []
    if web and query:
        try:
            from tools.search import search_with_sources
            _, text = await search_with_sources(query, 5)
            if text:
                blocks.append("### Websuche\n" + text[:3000])
        except Exception:
            pass
    if rag_collections:
        try:
            from tools.rag import query_collections
            hits = await query_collections(rag_collections, query or "Strategie", top_k_cap=top_k)
            if hits:
                blocks.append("### Wissensdatenbank (hinterlegte Datei)\n" + "\n\n".join(
                    f"[{h.get('collection_name','?')} · {h.get('filename','?')}]\n{h.get('text','')}"
                    for h in hits)[:char_budget])
        except Exception:
            pass
    return "\n\n".join(blocks)


async def _plan_llm_json(model: str, sys: str, usr: str):
    """Ein LLM-Aufruf mit erzwungenem JSON → (data, tok_in, tok_out)."""
    try:
        async with _model_session(model), httpx.AsyncClient(timeout=300) as client:
            resp = await _llm.chat(client, {
                "model": model, "think": False, "stream": False, "format": "json",
                "messages": [{"role": "system", "content": sys},
                             {"role": "user", "content": usr}],
                "options": {"num_ctx": _profile_num_ctx()}, "keep_alive": KEEP_ALIVE,
            })
            resp.raise_for_status()
            j = resp.json()
        data = _parse_llm_json(j.get("message", {}).get("content", "")) or {}
        ti, to = _llm_tok(j)
        return data, ti, to
    except Exception:
        return {}, 0, 0


def _plan_clean_agents(data) -> list:
    """Holt aus einer (evtl. eigenwillig geformten) LLM-Antwort eine Agentenliste.
    Toleriert {agents:[…]}, eine nackte Liste oder ein einzelnes Agent-Objekt."""
    if isinstance(data, dict):
        raw = data.get("agents")
        if not isinstance(raw, list):
            raw = [data] if data.get("name") and data.get("system_prompt") else []
    elif isinstance(data, list):
        raw = data
    else:
        raw = []
    out = []
    for ag in raw[:6]:
        if not isinstance(ag, dict):
            continue
        nm = str(ag.get("name") or "").strip()[:60]
        sp = str(ag.get("system_prompt") or ag.get("prompt") or "").strip()
        if not nm or not sp:
            continue
        tools = [str(t).strip() for t in (ag.get("tools") or []) if str(t).strip()][:6] \
            or ["web_search", "calculate"]
        out.append({
            "name": nm,
            "description": str(ag.get("description") or "").strip()[:300],
            "system_prompt": sp,
            "icon": (str(ag.get("icon") or "🤖").strip() or "🤖")[:4],
            "category": str(ag.get("category") or "Beratung").strip()[:40] or "Beratung",
            "tools": tools,
        })
    return out


def _plan_pinned_agents(pinned) -> list:
    """Bringt die per „/plan … /agent" angepinnten, BEREITS vorhandenen Agenten auf
    die Vorschlags-Form und markiert sie (``pinned``=True, vorhandene ``id`` behalten)."""
    out = []
    for ag in (pinned or []):
        if not isinstance(ag, dict):
            continue
        nm = str(ag.get("name") or "").strip()[:60]
        sp = str(ag.get("system_prompt") or "").strip()
        if not nm:
            continue
        out.append({
            "id": str(ag.get("id") or "").strip() or None,
            "name": nm,
            "description": str(ag.get("description") or "").strip()[:300],
            "system_prompt": sp,
            "icon": (str(ag.get("icon") or "🤖").strip() or "🤖")[:4],
            "category": str(ag.get("category") or "Beratung").strip()[:40] or "Beratung",
            "tools": [str(t).strip() for t in (ag.get("tools") or []) if str(t).strip()][:6]
                     or ["web_search", "calculate"],
            "pinned": True,
        })
    return out


async def _plan_agents(model: str, task_text: str, strategy_md: str, pinned: list = None):
    """Schlägt die Beratungs-Agenten vor → (agents, tok_in, tok_out). Angepinnte
    feste Agenten stehen IMMER an erster Stelle; das LLM ergänzt nur fehlende Rollen
    (kein Duplikat). Mit einem Retry über einen schlankeren Prompt, falls ein kleines
    Modell zunächst leer/eigenwillig antwortet (3B-Modelle kollabieren sonst auf 0)."""
    fixed = _plan_pinned_agents(pinned)
    have = {a["name"].lower() for a in fixed}
    fixed_note = ""
    if fixed:
        fixed_note = ("\n\nDiese Experten sind bereits FEST gesetzt (NICHT erneut vorschlagen, "
                      "nicht duplizieren): " + ", ".join(a["name"] for a in fixed)
                      + ". Schlage nur ERGÄNZENDE, noch fehlende Experten vor.")
    sys_b = (
        "Du leitest aus Strategie und Briefing die nötigen FACH-Beratungs-Agenten ab "
        "(z. B. Kosten-, Datenschutz-/Compliance-, Zeitplan-, Hardware-Experte). Jeder "
        "Agent erhält einen prägnanten Namen, eine kurze Beschreibung und einen system_prompt, "
        "der seine Fachperspektive, Prüfkriterien und den deutschen Antwortstil festlegt. "
        "Antworte NUR mit JSON in genau diesem Format:\n"
        '{"agents":[{"name":"Kosten-Experte","description":"…","system_prompt":"Du bist …",'
        '"icon":"💶","category":"Beratung","tools":["web_search","calculate"]}]}\n'
        "Lege für JEDES genannte Bewertungskriterium einen eigenen Experten an. "
        "Maximal 6 Agenten, mindestens 2. icon ist ein passendes Emoji." + fixed_note
    )
    # Briefing zuerst (enthält die Kriterien), Strategie nur als kurzer Auszug —
    # ein langer Strategie-Block lässt kleine Modelle auf einen Agenten kollabieren.
    usr_b = task_text + (f"\n\nStrategie-Auszug:\n{strategy_md[:1200]}" if strategy_md else "")
    data, ti, to = await _plan_llm_json(model, sys_b, usr_b)
    extra_agents = _plan_clean_agents(data)
    if not extra_agents and not fixed:
        # Retry: minimaler, sehr direktiver Prompt nur auf Basis des Briefings.
        sys_r = (
            "Lies das Briefing und nenne die Fach-Experten, die das Vorhaben bewerten sollten "
            "(je Bewertungskriterium einen). Antworte NUR mit JSON: "
            '{"agents":[{"name":"…","description":"…","system_prompt":"Du bist …","icon":"🤖",'
            '"category":"Beratung","tools":["web_search","calculate"]}]}. Mindestens 2 Experten.'
        )
        data2, ti2, to2 = await _plan_llm_json(model, sys_r, task_text)
        extra_agents = _plan_clean_agents(data2); ti += ti2; to += to2
    # Zusammenführen: feste zuerst, dann neue ohne Namens-Duplikate, gesamt ≤ 6.
    merged = list(fixed)
    for a in extra_agents:
        if a["name"].lower() in have:
            continue
        have.add(a["name"].lower())
        merged.append(a)
        if len(merged) >= 6:
            break
    return merged, ti, to


def _plan_norm_tasks(rawtasks: list, max_tasks: int = 40) -> list:
    """Bringt KI-Aufgaben aufs Planer-Schema (wie generate_plan): IDs eindeutig,
    Ressourcen normalisiert, Vorgänger gesäubert, Nachfolger + Start/Ende abgeleitet."""
    tasks, seen = [], set()
    for i, t in enumerate((rawtasks or [])[:max_tasks], start=1):
        if not isinstance(t, dict):
            continue
        tid = str(t.get("id") or f"T{i}").strip() or f"T{i}"
        while tid in seen:
            tid = f"{tid}_{i}"
        seen.add(tid)
        try:
            dur = max(0, float(t.get("duration", 1)))
        except Exception:
            dur = 1
        res = [_coerce_resource(r) for r in (t.get("resource_list") or t.get("resources") or [])
               if isinstance(r, dict)][:6]
        preds = [str(p).strip() for p in (t.get("predecessors") or []) if str(p).strip()]
        roles = [str(r).strip() for r in (t.get("roles") or []) if str(r).strip()][:6]
        tasks.append({
            "id": tid, "name": str(t.get("name", tid)).strip()[:120], "duration": dur,
            "predecessors": preds, "successors": [], "resources": "",
            "resource_list": res, "roles": roles, "area": str(t.get("area") or "").strip()[:40],
            "notes": str(t.get("notes") or "").strip()[:300], "is_start": False, "is_end": False,
        })
    ids = {t["id"] for t in tasks}
    by_id = {t["id"]: t for t in tasks}
    for t in tasks:
        t["predecessors"] = [p for p in t["predecessors"] if p in ids and p != t["id"]]
    for t in tasks:
        for p in t["predecessors"]:
            by_id[p]["successors"].append(t["id"])
    for t in tasks:
        if not t["predecessors"]:
            t["is_start"] = True
        if not t["successors"]:
            t["is_end"] = True
    return tasks


async def _plan_strategy_generator(req: PlanStrategyRequest):
    model = _pick_model(req.model, _model_for("general"))
    brief = (req.brief or "").strip()
    extra = (req.extra or "").strip()
    if not brief and not extra:
        yield _sse({"type": "error", "message": "Kein Briefing — bitte erst im Chat diskutieren, dann /plan."})
        return
    task_text = "\n\n".join(p for p in (
        f"Diskussion / Briefing:\n{brief[:6000]}" if brief else "",
        f"Zusätzliche Randbedingungen:\n{extra}" if extra else "",
    ) if p)
    query = (extra or brief).split("\n", 1)[0][:200]
    count = max(4, min(int(req.count or 12), 60))
    tin = tout = 0

    # ── Phase A — Strategie (Markdown, optional geerdet) ──────────────────────
    yield _sse({"type": "phase", "label": "Strategie wird entwickelt…"})
    # Für die Planung mehr aus der hinterlegten Datei ziehen (skaliert mit der
    # gewünschten Aufgabenzahl), damit das ganze Dokument abgedeckt ist.
    grounding = await _plan_ground(
        query, req.web_search, req.rag_collections,
        top_k=max(8, min(count, 40)), char_budget=min(12000, 3000 + count * 150))
    sys_a = (
        "Du bist ein erfahrener Strategie- und Projektberater. Aus der Diskussion "
        "entwickelst du eine klare, umsetzbare Strategie. Gliedere als Markdown mit "
        "diesen Abschnitten: '## Ziel', '## Optionen', '## Bewertungskriterien', "
        "'## Vorgehen', '## Risiken', '## Meilensteine'. Sei konkret und entscheidungs"
        "orientiert. Wenn dir Belegmaterial eingeblendet ist, stütze dich darauf und "
        "nenne Quellen; erfinde keine Preise oder Rechtsstände."
    )
    usr_a = (f"Belegmaterial:\n{grounding}\n\n" if grounding else "") + task_text
    strategy_md = ""
    try:
        async with _model_session(model), httpx.AsyncClient(timeout=300) as client:
            resp = await _llm.chat(client, {
                "model": model, "think": False, "stream": False,
                "messages": [{"role": "system", "content": sys_a},
                             {"role": "user", "content": usr_a}],
                "options": {"num_ctx": _profile_num_ctx()}, "keep_alive": KEEP_ALIVE,
            })
            resp.raise_for_status()
            j = resp.json()
        strategy_md = re.sub(r"<think>.*?</think>", "", j.get("message", {}).get("content", ""),
                             flags=re.DOTALL).strip()
        a, b = _llm_tok(j); tin += a; tout += b
    except Exception as e:
        strategy_md = f"_(Strategie konnte nicht erstellt werden: {e})_"
    yield _sse({"type": "strategy", "markdown": strategy_md})

    # ── Phase B — Beratungs-Agenten (Vorschlag) ───────────────────────────────
    yield _sse({"type": "phase", "label": "Beratungs-Agenten werden abgeleitet…"})
    agents, a, b = await _plan_agents(model, task_text, strategy_md, req.pinned_agents)
    tin += a; tout += b
    yield _sse({"type": "agents", "agents": agents})

    # ── Phase C — Einsatz-/Ressourcenplan (Vorschlag) ─────────────────────────
    yield _sse({"type": "phase", "label": "Einsatz- & Ressourcenplan wird erstellt…"})
    roles_hint = ", ".join(a["name"] for a in agents) or "die nötigen Fachrollen"
    sys_c = (
        "Du bist ein erfahrener Projektplaner. Erstelle aus Strategie und Briefing einen "
        "Einsatz- und Ressourcenplan in sinnvollen Phasen. Vergib fortlaufende IDs T1, T2, …. "
        "Jede Aufgabe hat: id, name, duration (Tage), predecessors (Liste direkter Vorgänger-IDs; "
        "die erste hat []), area (Projektphase), roles (zuständige Rollen — nutze wo passend die "
        f"Beratungsrollen: {roles_hint}) und resource_list (Mensch/Hardware/Software) mit "
        "kind (human|hardware|software), name, qty, hours (bei Hardware/Software 0) und rate (€). "
        "Füge einen resource_catalog mit den verwendeten Rollen/Ressourcen und Kostensätzen an. "
        "Antworte NUR mit JSON in genau diesem Format, ohne Markdown:\n"
        '{"name":"Projektname","description":"…","tasks":[{"id":"T1","name":"Anforderungen klären",'
        '"duration":3,"predecessors":[],"area":"Vorbereitung","roles":["Projektleiter"],'
        '"resource_list":[{"kind":"human","name":"Projektleiter","qty":1,"hours":16,"rate":90}]}],'
        '"resource_catalog":[{"kind":"human","name":"Projektleiter","rate":90}]}\n'
        f"Erzeuge möglichst genau {count} Aufgaben mit echten Abhängigkeiten — lieber feinere "
        "Granularität als zu wenige. Stütze die Aufgaben, wo Belegmaterial vorliegt, auf dessen Inhalt."
    )
    usr_c = (
        (f"Belegmaterial (hinterlegte Datei):\n{grounding}\n\n" if grounding else "")
        + (f"Strategie:\n{strategy_md[:4000]}\n\n" if strategy_md else "")
        + task_text
    )
    data_c, a, b = await _plan_llm_json(model, sys_c, usr_c); tin += a; tout += b
    plan_tasks = _plan_norm_tasks(data_c.get("tasks") or [], max_tasks=count)
    plan = {
        "name": str(data_c.get("name") or (query[:60] or "Einsatzplan")).strip()[:120],
        "description": str(data_c.get("description") or "").strip()[:1000],
        "tasks": plan_tasks,
        "resource_catalog": _normalize_catalog(data_c.get("resource_catalog")),
        "resource_mode": "free",
    }
    yield _sse({"type": "plan", "plan": plan})

    # ── Phase D — Bewertungs-Jury (Vorschlag) ─────────────────────────────────
    yield _sse({"type": "phase", "label": "Bewertungs-Jury wird zusammengestellt…"})
    jury = {
        "name": (f"Bewertung: {query[:48]}" if query else "Bewertungs-Jury").strip(),
        "description": "Bewertet das Vorhaben aus den Fachperspektiven der Beratungs-Agenten.",
        "member_agent_names": [a["name"] for a in agents],
    }
    yield _sse({"type": "jury", "jury": jury})

    yield _sse({"type": "done", "tokens": {"in": tin, "out": tout}})


@app.post("/api/plan/strategy")
async def plan_strategy(req: PlanStrategyRequest):
    return StreamingResponse(
        _plan_strategy_generator(req),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


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
        (VARIANTEN_DIR, "varianten"), (TODO_DIR, "todo"),
        (TODO_ATT_DIR, "todo_att"),   # To-Do-Anlagen (Original-Dateien; MD-Text + Baum sind in der DB)
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


@app.get("/api/backup")
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


@app.post("/api/restore")
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


# ── Asset-Serving (bilder/) ───────────────────────────────────────────────────

_ALLOWED_IMAGE_EXT = {".jpg", ".jpeg", ".png", ".gif", ".svg", ".webp"}


@app.get("/api/assets/{name}")
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


@app.get("/api/profile/assets")
async def list_profile_assets():
    """Welche Profil-Assets sind gesetzt? (für die UI)"""
    return {
        kind: {
            "present": (PROFILE_ASSETS_DIR / cfg["file"]).exists(),
            "recommended": cfg["size"],
        }
        for kind, cfg in _PROFILE_ASSETS.items()
    }


@app.post("/api/profile/asset/{kind}")
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


@app.get("/api/profile/asset/{kind}")
async def get_profile_asset(kind: str):
    if kind not in _PROFILE_ASSETS:
        raise HTTPException(400, "Unbekannter Asset-Typ")
    fp = PROFILE_ASSETS_DIR / _PROFILE_ASSETS[kind]["file"]
    if not fp.exists():
        raise HTTPException(404, "Kein Asset hinterlegt")
    return FileResponse(fp)


@app.delete("/api/profile/asset/{kind}")
async def delete_profile_asset(kind: str):
    if kind not in _PROFILE_ASSETS:
        raise HTTPException(400, "Unbekannter Asset-Typ")
    fp = PROFILE_ASSETS_DIR / _PROFILE_ASSETS[kind]["file"]
    if fp.exists():
        fp.unlink()
    return {"ok": True}


# ── Code-IDE ─────────────────────────────────────────────────────────────────


def _code_path_by_id(prog_id: str) -> Optional[Path]:
    for f in CODE_DIR.glob("*.json"):
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
            if d.get("id") == prog_id:
                return f
        except Exception:
            pass
    return None


def _agent_def_by_id(agent_id: str) -> dict:
    if not agent_id:
        return {}
    fp = _agent_path_by_id(agent_id)
    if not fp:
        return {}
    try:
        return json.loads(fp.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}


def _profile_code_context() -> str:
    """Kurzer Profil-Kontext für den adaptiven Code-Agenten."""
    p = _load_profile()
    bits = []
    for k, label in (("position", "Position"), ("department", "Abteilung"),
                     ("company", "Firma")):
        v = str(p.get(k, "")).strip()
        if v:
            bits.append(f"{label}: {v}")
    mode = str(p.get("mode", "")).strip()
    if mode:
        bits.append(f"Fachmodus: {mode}")
    return "; ".join(bits)


_CODE_BASE_SYS = (
    "Du bist ein erfahrener Software-Entwickler. Schreibe sauberen, lauffähigen, "
    "sinnvoll kommentierten Code. Halte Erklärungen kurz — der Code steht im Vordergrund."
)


@app.post("/api/code/assist")
async def code_assist(req: Request):
    """Code-Assistent für den Code-Tab. Stellt — sofern nötig — zuerst Rückfragen
    (Phase 1), erzeugt dann Code (Phase 2). Optional mit wählbarem Coding-Agenten
    (inkl. hinterlegtem Beispielcode) und adaptiver Rollen-/Profil-Analyse."""
    body = await req.json()
    prompt = str(body.get("prompt", "")).strip()
    if not prompt:
        raise HTTPException(status_code=400, detail="Keine Aufgabe angegeben")
    answers = str(body.get("answers", "")).strip()
    language = str(body.get("language", "") or "").strip()[:30]
    agent_id = str(body.get("agent_id", "")).strip()
    adaptive = bool(body.get("adaptive"))
    force_code = bool(body.get("force_code"))
    current_code = str(body.get("current_code", "") or "")[:8000]
    model = _pick_model(body.get("model"), _model_for("coding"))
    num_ctx = _profile_num_ctx()

    agent = _agent_def_by_id(agent_id)
    persona = str(agent.get("system_prompt", "")).strip()
    example_code = str(agent.get("example_code", "")).strip()

    # Adaptiver Agent: Experten-Rolle aus Aufgabe ableiten (nur ohne expliziten Agenten)
    adaptive_note = ""
    if adaptive and not persona:
        role, sysp = await _derive_adaptive_prompt(prompt, model, num_ctx)
        if sysp:
            persona, adaptive_note = sysp, role

    sys_parts = [persona or _CODE_BASE_SYS]
    pctx = _profile_code_context()
    if adaptive and pctx:
        sys_parts.append(f"Kontext zum Nutzer (nutze, wenn hilfreich): {pctx}.")
    if example_code:
        sys_parts.append("Orientiere dich an Stil und Struktur dieses Beispielcodes:\n"
                         "```\n" + example_code[:4000] + "\n```")
    if language:
        sys_parts.append(f"Bevorzugte Programmiersprache: {language}.")
    system = "\n\n".join(sys_parts)

    # ── Phase 1: Rückfragen ─────────────────────────────────────────────────
    if not force_code and not answers:
        clarify_sys = system + (
            "\n\nPrüfe, ob WESENTLICHE Informationen fehlen, um die Aufgabe korrekt zu "
            "lösen (Eingaben/Ausgaben, Sprache, Rahmenbedingungen). Wenn ja: stelle bis zu "
            "4 kurze, konkrete Rückfragen. Wenn alles hinreichend klar ist: leere Liste. "
            'Antworte NUR mit JSON: {"questions":["…"]}.')
        try:
            async with _model_session(model), httpx.AsyncClient(timeout=120) as client:
                resp = await _llm.chat(client, {
                    "model": model, "think": False, "stream": False, "format": "json",
                    "messages": [{"role": "system", "content": clarify_sys},
                                 {"role": "user", "content": f"Aufgabe:\n{prompt}"}],
                    "options": {"num_ctx": num_ctx}, "keep_alive": KEEP_ALIVE,
                })
                resp.raise_for_status()
            _jc = resp.json()
            _cti, _cto = _llm_tok(_jc)
            d = _parse_llm_json(_jc.get("message", {}).get("content", "")) or {}
            qs = [str(q).strip() for q in (d.get("questions") or []) if str(q).strip()][:4]
        except Exception:
            qs, _cti, _cto = [], 0, 0
        if qs:
            return {"type": "questions", "questions": qs, "adaptive_role": adaptive_note,
                    "tokens": {"in": _cti, "out": _cto}}

    # ── Phase 2: Code erzeugen ──────────────────────────────────────────────
    usr = f"Aufgabe:\n{prompt}"
    if answers:
        usr += f"\n\nZusätzliche Antworten/Vorgaben:\n{answers}"
    if current_code:
        usr += f"\n\nBestehender Code (anpassen/erweitern, falls passend):\n```\n{current_code}\n```"
    usr += "\n\nGib eine vollständige, lauffähige Lösung — Code in EINEM ```-Codeblock."
    code_sys = system + "\n\nAntworte mit einer kurzen Erklärung und dem Code in genau EINEM ```-Codeblock."
    try:
        async with _model_session(model), httpx.AsyncClient(timeout=240) as client:
            resp = await _llm.chat(client, {
                "model": model, "think": False, "stream": False,
                "messages": [{"role": "system", "content": code_sys},
                             {"role": "user", "content": usr}],
                "options": {"num_ctx": num_ctx, "temperature": 0.2}, "keep_alive": KEEP_ALIVE,
            })
            resp.raise_for_status()
        _jc = resp.json()
        _cti, _cto = _llm_tok(_jc)
        content = str(_jc.get("message", {}).get("content", ""))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Code-Erzeugung fehlgeschlagen: {e}")
    code = _extract_code_block(content) or content.strip()
    note = re.sub(r"```[a-zA-Z0-9_+-]*\n[\s\S]*?```", "", content).strip()[:600]
    return {"type": "code", "code": code, "note": note,
            "adaptive_role": adaptive_note, "language": language,
            "tokens": {"in": _cti, "out": _cto}}


@app.get("/api/code")
async def list_code():
    programs = []
    for f in CODE_DIR.glob("*.json"):
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
            programs.append({"id": d["id"], "name": d.get("name", ""), "updated_at": d.get("updated_at", 0)})
        except Exception:
            pass
    return sorted(programs, key=lambda x: x.get("updated_at", 0), reverse=True)


@app.get("/api/code/{prog_id}")
async def get_code_program(prog_id: str):
    fp = _code_path_by_id(prog_id)
    if not fp:
        raise HTTPException(404, "Programm nicht gefunden")
    return json.loads(fp.read_text(encoding="utf-8"))


@app.post("/api/code")
async def save_code_program(req: Request):
    body = await req.json()
    name = str(body.get("name") or "Unbenannt").strip()
    code = str(body.get("code") or "")
    prog_id = str(body.get("id") or "").strip()
    if not prog_id:
        prog_id = (_to_slug(name) or "prog") + "_" + uuid.uuid4().hex[:6]
    fp = _code_path_by_id(prog_id)
    if not fp:
        fp = CODE_DIR / f"{_to_slug(name)}_{prog_id[-6:]}.json"
    data = {"id": prog_id, "name": name, "code": code, "updated_at": time.time()}
    fp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return data


@app.delete("/api/code/{prog_id}")
async def delete_code_program(prog_id: str):
    fp = _code_path_by_id(prog_id)
    if not fp:
        raise HTTPException(404, "Programm nicht gefunden")
    fp.unlink()
    return {"ok": True}


def _safe_relpath(p: str) -> str:
    """Relativen, sicheren Dateipfad erzwingen (kein Pfad-Traversal, kein absoluter Pfad,
    Backslashes → Slash). Leere/auflösbare Segmente (., ..) werden verworfen."""
    p = str(p or "").replace("\\", "/").strip().lstrip("/")
    parts = [seg.strip() for seg in p.split("/") if seg.strip() and seg.strip() not in (".", "..")]
    return "/".join(parts)[:200]


_CODE_PROJECT_SYSTEM = (
    "Du bist ein erfahrener Software-Architekt. Erzeuge zu einer Aufgabe eine sinnvolle, "
    "kohärente MEHRDATEI-Projektstruktur. Wähle eine übliche Aufteilung (Einstiegspunkt, "
    "Module/Pakete, ggf. Tests, README, ggf. Konfig/Abhängigkeiten). Gib JEDE Datei mit "
    "RELATIVEM Pfad (Schrägstriche als Trenner, KEIN führender Slash, kein „..“, keine "
    "absoluten Pfade) und vollständigem, lauffähigem Inhalt aus. Halte das Projekt fokussiert: "
    "höchstens {maxfiles} Dateien, jede Datei kompakt. Schreibe echten Code — KEINE Auslassungs-"
    "Platzhalter wie „…“ oder „TODO Rest“. Kommentare/Texte auf Deutsch. "
    'Antworte NUR mit JSON: {"files":[{"path":"ordner/datei.ext","content":"<voller Inhalt>"}],'
    '"note":"1–2 Sätze, was die Struktur enthält"}.'
)


@app.post("/api/code/project")
async def code_project(req: Request):
    """Erzeugt zu einer Aufgabe eine Mehrdatei-Projektstruktur (Dateibaum + Inhalte)
    als JSON. Optional mit Coding-Agent (Persona/`example_code`) und Sprache/Stack.
    Nicht direkt ausführbar — Anzeige als Baum im Code-Tab, Download als ZIP."""
    body = await req.json()
    prompt = str(body.get("prompt", "")).strip()
    if not prompt:
        raise HTTPException(status_code=400, detail="Keine Aufgabe angegeben")
    language = str(body.get("language", "") or "").strip()[:60]
    agent_id = str(body.get("agent_id", "")).strip()
    try:
        max_files = int(body.get("max_files", 10))
    except Exception:
        max_files = 10
    max_files = max(2, min(16, max_files))
    model = _pick_model(body.get("model"), _model_for("coding"))
    num_ctx = _profile_num_ctx()

    agent = _agent_def_by_id(agent_id)
    persona = str(agent.get("system_prompt", "")).strip()
    example_code = str(agent.get("example_code", "")).strip()

    sys_parts = [_CODE_PROJECT_SYSTEM.format(maxfiles=max_files)]
    if persona:
        sys_parts.append("Rolle/Vorgaben des gewählten Agenten:\n" + persona)
    if example_code:
        sys_parts.append("Orientiere dich an Stil/Struktur dieses Beispielcodes:\n```\n"
                         + example_code[:3000] + "\n```")
    if language:
        sys_parts.append(f"Sprache/Stack: {language}.")
    system = "\n\n".join(sys_parts)

    usr = f"Aufgabe:\n{prompt}\n\nLiefere höchstens {max_files} Dateien."

    try:
        async with _model_session(model), httpx.AsyncClient(timeout=300) as client:
            resp = await _llm.chat(client, {
                "model": model, "think": False, "stream": False, "format": "json",
                "messages": [{"role": "system", "content": system},
                             {"role": "user", "content": usr}],
                "options": {"num_ctx": num_ctx, "temperature": 0.2}, "keep_alive": KEEP_ALIVE,
            })
            resp.raise_for_status()
        _jc = resp.json()
        tin, tout = _llm_tok(_jc)
        data = _parse_llm_json(_jc.get("message", {}).get("content", "")) or {}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Projekt-Erzeugung fehlgeschlagen: {e}")

    files, seen = [], set()
    for f in (data.get("files") or []):
        path = _safe_relpath((f or {}).get("path", ""))
        content = (f or {}).get("content", "")
        if not isinstance(content, str):
            content = json.dumps(content, ensure_ascii=False, indent=2)
        if not path or path in seen:
            continue
        seen.add(path)
        files.append({"path": path, "content": content})
        if len(files) >= max_files:
            break
    note = str(data.get("note", "")).strip()[:600]
    return {"files": files, "note": note, "tokens": {"in": tin, "out": tout}}


@app.post("/api/code/project-zip")
async def code_project_zip(req: Request):
    """Packt eine (im Code-Tab erzeugte/bearbeitete) Projektstruktur in ein ZIP zum
    Download. Pfade werden serverseitig auf sichere relative Pfade reduziert."""
    import io, zipfile, re as _re
    body = await req.json()
    files = body.get("files") or []
    if not isinstance(files, list) or not files:
        raise HTTPException(status_code=400, detail="Keine Dateien übergeben")
    zipname = _re.sub(r"[^\w\-]+", "_", str(body.get("zipname", "")).strip()) or "projekt"
    buf = io.BytesIO()
    seen: set[str] = set()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for i, f in enumerate(files):
            path = _safe_relpath((f or {}).get("path", "")) or f"datei_{i + 1}.txt"
            base, n = path, 2
            while path in seen:
                if "." in base.rsplit("/", 1)[-1]:
                    stem, ext = base.rsplit(".", 1)
                    path = f"{stem}_{n}.{ext}"
                else:
                    path = f"{base}_{n}"
                n += 1
            seen.add(path)
            zf.writestr(path, str((f or {}).get("content", "")))
    buf.seek(0)
    return StreamingResponse(
        buf, media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{zipname}.zip"'},
    )


@app.post("/api/code/run-python")
async def run_python_code(req: Request):
    """Führt Python-Code aus dem Code-Tab serverseitig aus (stdout/stderr,
    matplotlib-Plots, Zeitlimit). Im Mehrbenutzer-/Servermodus über
    config.json `allow_python_exec: false` abschaltbar."""
    if not ALLOW_PYTHON_EXEC:
        raise HTTPException(403, "Python-Ausführung ist in dieser Installation deaktiviert.")
    body = await req.json()
    code = str(body.get("code") or "")
    if not code.strip():
        return {"output": "", "error": "", "images": []}
    try:
        t = float(body.get("timeout") or 15.0)
    except Exception:
        t = 15.0
    t = max(1.0, min(t, 60.0))
    # Ausführung (blockierend mit Zeitlimit) im Threadpool, damit der Event-Loop frei bleibt
    return await asyncio.to_thread(_run_python_code, code, t)


# ── Autonomer Coding-Agent (Agent-Harness im Code-Tab) ────────────────────────
# Aider-/Claude-Code-artiger Loop: eine Aufgabe → das Modell nutzt Werkzeuge
# (Dateien auflisten/lesen/schreiben, Python im Sandkasten prüfen), iteriert selbst
# bis fertig. Die Dateien liegen im Client (Workspace) und werden mitgeschickt; der
# Agent arbeitet auf einer In-Memory-Kopie und liefert am Ende den neuen Stand.
# HTML/JS-Ergebnisse werden im Client-Canvas gerendert (nicht hier ausgeführt).

_CODE_AGENT_SYSTEM = (
    "Du bist ein autonomer Coding-Agent in einer Entwickler-Werkbank. Löse die Aufgabe des "
    "Nutzers eigenständig in kleinen, überprüfbaren Schritten mit den bereitgestellten "
    "Werkzeugen:\n"
    "- list_files(): vorhandene Dateien auflisten\n"
    "- read_file(path): eine Datei lesen\n"
    "- write_file(path, content): eine Datei anlegen oder KOMPLETT überschreiben — immer den "
    "VOLLSTÄNDIGEN Dateiinhalt angeben (keine Auslassungen, kein „…“)\n"
    "- run_python(code): Python im Sandkasten ausführen, um Python-Ergebnisse zu PRÜFEN "
    "(liefert stdout/stderr; kein Datei- oder Netzzugriff)\n"
    "Arbeite iterativ: schreibe/ändere Dateien, prüfe, korrigiere Fehler. Für WEB-/CANVAS-"
    "Aufgaben schreibe selbstständig lauffähiges HTML/JS (z. B. eine index.html mit allem "
    "inline, ohne Server) — die Anzeige erfolgt im Browser-Canvas; nutze dafür NICHT "
    "run_python. Werden dir Konsolenfehler gemeldet, behebe sie. "
    "Wenn die Aufgabe erledigt ist, antworte mit einer KURZEN Zusammenfassung (1–3 Sätze) und "
    "OHNE weiteren Werkzeugaufruf."
)


def _code_agent_tools(allow_py: bool) -> list:
    tools = [
        {"type": "function", "function": {
            "name": "list_files",
            "description": "Listet die vorhandenen Dateien (Pfade) im Arbeitsbereich.",
            "parameters": {"type": "object", "properties": {}}}},
        {"type": "function", "function": {
            "name": "read_file",
            "description": "Liest den vollständigen Inhalt einer Datei.",
            "parameters": {"type": "object", "properties": {
                "path": {"type": "string", "description": "Relativer Dateipfad"}},
                "required": ["path"]}}},
        {"type": "function", "function": {
            "name": "write_file",
            "description": "Legt eine Datei an oder überschreibt sie KOMPLETT. Immer den "
                           "vollständigen Dateiinhalt angeben.",
            "parameters": {"type": "object", "properties": {
                "path": {"type": "string"}, "content": {"type": "string"}},
                "required": ["path", "content"]}}},
    ]
    if allow_py:
        tools.append({"type": "function", "function": {
            "name": "run_python",
            "description": "Führt Python-Code im Sandkasten aus und liefert stdout/stderr. "
                           "Zum Prüfen von Python-Ergebnissen — NICHT für HTML/JS.",
            "parameters": {"type": "object", "properties": {
                "code": {"type": "string"}}, "required": ["code"]}}})
    return tools


def _extract_file_blocks(text: str) -> list:
    """Best-effort-Fallback (wenn ein Modell keine Tool-Aufrufe macht): gefencte
    Codeblöcke mit Datei-Hinweis aus Fließtext ziehen. Rückgabe [(path, content)]."""
    out = []
    for m in re.finditer(r"```([^\n`]*)\n(.*?)```", text, flags=re.DOTALL):
        info = (m.group(1) or "").strip()
        code = m.group(2)
        path = ""
        mm = re.search(r"([\w./-]+\.\w{1,5})", info)
        if mm:
            path = mm.group(1)
        else:
            pre = text[:m.start()].rstrip().split("\n")[-1] if m.start() else ""
            m2 = re.search(r"([\w./-]+\.\w{1,5})", pre)
            if m2:
                path = m2.group(1)
            else:
                path = {"html": "index.html", "js": "main.js", "javascript": "main.js",
                        "python": "main.py", "py": "main.py"}.get(info.lower(), "")
        p = _safe_relpath(path)
        if p and code.strip():
            out.append((p, code.rstrip("\n")))
    return out


async def _code_agent_generator(body: dict):
    task = str(body.get("task", "") or "").strip()
    if not task:
        yield _sse({"type": "error", "message": "Keine Aufgabe angegeben."})
        return

    files: dict = {}
    for f in (body.get("files") or []):
        p = _safe_relpath((f or {}).get("path", ""))
        if p:
            files[p] = str((f or {}).get("content", "") or "")

    model = _pick_model(body.get("model"), _model_for("coding"))
    num_ctx = _profile_num_ctx()
    try:
        max_steps = int(body.get("max_steps") or 12)
    except Exception:
        max_steps = 12
    max_steps = max(1, min(max_steps, 20))
    allow_py = bool(ALLOW_PYTHON_EXEC)
    changed: set = set()

    def _apply_write(path, content):
        p = _safe_relpath(path)
        if not p:
            return None
        files[p] = str(content or "")
        changed.add(p)
        return p

    filelist = "\n".join(f"- {p}" for p in files) or "(leer)"
    system = _CODE_AGENT_SYSTEM + ("" if allow_py else
             "\n\nHINWEIS: Python-Ausführung ist in dieser Installation deaktiviert — "
             "run_python steht NICHT zur Verfügung.")
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": f"Aufgabe:\n{task}\n\nVorhandene Dateien:\n{filelist}"},
    ]
    tools = _code_agent_tools(allow_py)
    tok = {"in": 0, "out": 0}

    try:
        for _step in range(max_steps):
            async with _model_session(model), httpx.AsyncClient(timeout=180) as client:
                resp = await _llm.chat(client, {
                    "model": model, "think": False, "stream": False,
                    "messages": messages, "tools": tools,
                    "options": {"num_ctx": num_ctx}, "keep_alive": KEEP_ALIVE,
                })
                resp.raise_for_status()
            result = resp.json()
            _ti, _to = _llm_tok(result)
            tok["in"] += _ti
            tok["out"] += _to
            msg = result.get("message", {}) or {}
            content_raw = msg.get("content", "") or ""
            tool_calls = msg.get("tool_calls") or []
            if not tool_calls:
                inline = _extract_inline_tool_calls(content_raw)
                if inline:
                    tool_calls = inline
                    content_raw = _strip_inline_tool_calls(content_raw)
            content_raw = re.sub(r"<think>.*?</think>", "", content_raw, flags=re.DOTALL).strip()

            if not tool_calls:
                # Fertig — oder weiches Fallback: Datei-Blöcke aus dem Text übernehmen
                for p, c in _extract_file_blocks(content_raw):
                    if _apply_write(p, c):
                        yield _sse({"type": "step", "tool": "write_file", "arg": p,
                                    "result": "aus Text übernommen"})
                yield _sse({"type": "text", "content": content_raw or "Fertig."})
                break

            messages.append({"role": "assistant", "content": content_raw, "tool_calls": tool_calls})
            for tc in tool_calls:
                fn = (tc.get("function") or {}).get("name", "")
                args = (tc.get("function") or {}).get("arguments")
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except Exception:
                        args = {}
                args = args or {}

                if fn == "list_files":
                    res = "\n".join(files.keys()) or "(keine Dateien)"
                    yield _sse({"type": "step", "tool": "list_files", "arg": "",
                                "result": f"{len(files)} Datei(en)"})
                elif fn == "read_file":
                    p = _safe_relpath(args.get("path", ""))
                    if p in files:
                        res = files[p]
                        yield _sse({"type": "step", "tool": "read_file", "arg": p,
                                    "result": f"{len(res)} Zeichen"})
                    else:
                        res = f"FEHLER: Datei '{p}' existiert nicht."
                        yield _sse({"type": "step", "tool": "read_file", "arg": p,
                                    "result": "nicht gefunden"})
                elif fn == "write_file":
                    p = _apply_write(args.get("path", ""), args.get("content", ""))
                    if p:
                        res = f"Datei '{p}' geschrieben ({len(files[p])} Zeichen)."
                        yield _sse({"type": "step", "tool": "write_file", "arg": p,
                                    "result": "geschrieben"})
                    else:
                        res = "FEHLER: Ungültiger Pfad."
                        yield _sse({"type": "step", "tool": "write_file",
                                    "arg": str(args.get("path", "")), "result": "Fehler"})
                elif fn == "run_python" and allow_py:
                    code = str(args.get("code", "") or "")
                    out = await asyncio.to_thread(_run_python_code, code, 15.0)
                    res = ""
                    if out.get("output"):
                        res += "STDOUT:\n" + out["output"]
                    if out.get("error"):
                        res += "\nSTDERR:\n" + out["error"]
                    res = (res.strip() or "(keine Ausgabe)")[:4000]
                    yield _sse({"type": "step", "tool": "run_python", "arg": code[:60],
                                "result": "Fehler" if out.get("error") else "ok"})
                else:
                    res = f"Werkzeug '{fn}' ist nicht verfügbar."
                    yield _sse({"type": "step", "tool": fn or "?", "arg": "", "result": "n/a"})

                messages.append({"role": "tool", "content": res[:6000]})
        else:
            yield _sse({"type": "text",
                        "content": f"Maximale Schrittzahl ({max_steps}) erreicht — "
                                   f"Zwischenstand wird übernommen."})
    except httpx.ConnectError:
        yield _sse({"type": "error", "message": "Ollama nicht erreichbar — läuft der lokale Server?"})
    except httpx.HTTPStatusError as e:
        yield _sse({"type": "error",
                    "message": f"Modell abgelehnt (num_ctx/VRAM?): HTTP {e.response.status_code}"})
    except Exception as e:
        yield _sse({"type": "error", "message": f"Agent-Fehler: {type(e).__name__}: {e}"})

    yield _sse({"type": "files",
                "files": [{"path": p, "content": c} for p, c in files.items()],
                "changed": sorted(changed)})
    yield _sse({"type": "done", "tokens": tok})


@app.post("/api/code/agent")
async def code_agent(req: Request):
    """Autonomer Coding-Agent (SSE): löst eine Aufgabe eigenständig über einen
    Werkzeug-Loop (Dateien lesen/schreiben, Python-Sandkasten). Liefert Schritt-Frames
    (`step`), finalen Text, den neuen Dateistand (`files`) und `done` mit Tokens."""
    body = await req.json()
    return StreamingResponse(
        _code_agent_generator(body),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ── Jury-Dokumente (Werkbank im Jury-Tab: anzeigen, bearbeiten, speichern) ──────

def _jury_doc_path_by_id(doc_id: str) -> Optional[Path]:
    for f in JURY_DOCS_DIR.glob("*.json"):
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
            if d.get("id") == doc_id:
                return f
        except Exception:
            pass
    return None


@app.get("/api/jury-docs")
async def list_jury_docs():
    docs = []
    for f in JURY_DOCS_DIR.glob("*.json"):
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
            docs.append({"id": d["id"], "name": d.get("name", ""), "updated_at": d.get("updated_at", 0)})
        except Exception:
            pass
    return sorted(docs, key=lambda x: x.get("updated_at", 0), reverse=True)


@app.get("/api/jury-docs/{doc_id}")
async def get_jury_doc(doc_id: str):
    fp = _jury_doc_path_by_id(doc_id)
    if not fp:
        raise HTTPException(404, "Dokument nicht gefunden")
    return json.loads(fp.read_text(encoding="utf-8"))


@app.post("/api/jury-docs")
async def save_jury_doc(req: Request):
    body = await req.json()
    name = str(body.get("name") or "Unbenannt").strip()
    text = str(body.get("text") or "")
    evaluation = body.get("evaluation")  # optional: zuletzt gespeicherte Bewertung
    doc_id = str(body.get("id") or "").strip()
    if not doc_id:
        doc_id = (_to_slug(name) or "doc") + "_" + uuid.uuid4().hex[:6]
    fp = _jury_doc_path_by_id(doc_id)
    if not fp:
        fp = JURY_DOCS_DIR / f"{_to_slug(name)}_{doc_id[-6:]}.json"
    data = {"id": doc_id, "name": name, "text": text, "evaluation": evaluation,
            "updated_at": time.time()}
    fp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return data


@app.delete("/api/jury-docs/{doc_id}")
async def delete_jury_doc(doc_id: str):
    fp = _jury_doc_path_by_id(doc_id)
    if not fp:
        raise HTTPException(404, "Dokument nicht gefunden")
    fp.unlink()
    return {"ok": True}


# ── Hilfe-Wissensdatenbank + Hilfe-Agent (aus der mitgelieferten Doku) ──────────

_HELP_DOC_FILES = [
    "README.md", "BEDIENUNGSANLEITUNG.md",
    "docs/ENTWICKLUNG.md", "docs/SERVER.md", "docs/PERSISTENZ.md",
    "docs/TECHNISCHE_BESCHREIBUNG.md", "docs/PORTABLE.md",
    "docs/INSTALL.md", "docs/GITHUB.md", "docs/LIZENZEN.md",
    "docs/HANDY_ZUGRIFF.md",
]
_HELP_COLLECTION_NAME = "Hilfe: LOCAL AI"
_HELP_AGENT_ID = "hilfe_agent"


def _help_agent_path() -> Optional[Path]:
    for f in AGENTS_DIR.glob("*.json"):
        try:
            if json.loads(f.read_text(encoding="utf-8")).get("id") == _HELP_AGENT_ID:
                return f
        except Exception:
            pass
    return None


@app.post("/api/help/build")
async def help_build(req: Request):
    """Liest die mitgelieferte Tool-Doku in eine RAG-Wissensdatenbank ein und legt
    (oder aktualisiert) einen Hilfe-Agenten an, der ausschließlich daraus antwortet.
    Idempotent: eine vorhandene Hilfe-Sammlung wird ersetzt (frische Doku)."""
    from tools.rag import ingest_file
    root = Path(__file__).parent

    # vorhandene Hilfe-Sammlung(en) entfernen → frisch aufbauen
    try:
        for c in await _db.rag_list_collections():
            if c.get("name") == _HELP_COLLECTION_NAME:
                await _db.rag_delete_collection(c["id"])
    except Exception:
        pass

    coll = {
        "id": f"rag_{uuid.uuid4().hex[:12]}",
        "name": _HELP_COLLECTION_NAME,
        "embed_model": EMBED_MODEL,
        "tier": "ausgewogen",
        "chunk_size": 1000, "chunk_overlap": 150, "top_k": 6,
        "embed_gpu": False, "clean": True, "char_limit": 6000,
        "strictness": "korrekt", "created_at": time.time(),
    }
    await _db.rag_create_collection(coll)

    ingested = 0
    try:
        for rel in _HELP_DOC_FILES:
            fp = root / rel
            if not fp.is_file():
                continue
            try:
                text = fp.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue
            if not text.strip():
                continue
            await ingest_file(coll, text, rel, f"doc_{uuid.uuid4().hex[:12]}")
            ingested += 1
    except Exception as e:
        await _db.rag_delete_collection(coll["id"])
        raise HTTPException(
            status_code=500,
            detail=f"Einbetten fehlgeschlagen — ist das Embedding-Modell '{EMBED_MODEL}' gepullt? ({e})")

    if not ingested:
        await _db.rag_delete_collection(coll["id"])
        raise HTTPException(status_code=404, detail="Keine Doku-Dateien gefunden.")

    system_prompt = (
        "Du bist der Hilfe-Assistent für die Anwendung „LOCAL AI“. Dir ist die komplette "
        "Bedienungs- und Entwicklerdokumentation als Wissensdatenbank hinterlegt. Beantworte "
        "Fragen zur Bedienung, zu Tabs/Funktionen und zur Einrichtung AUSSCHLIESSLICH anhand "
        "der eingeblendeten Doku-Auszüge und nenne den jeweiligen Abschnitt/die Datei. Steht "
        "etwas nicht in der Doku, sage das klar und rate nicht. Antworte präzise auf Deutsch."
    )
    existing = _help_agent_path()
    if existing:
        try:
            data = json.loads(existing.read_text(encoding="utf-8"))
        except Exception:
            data = {}
        data.update({
            "id": _HELP_AGENT_ID, "name": data.get("name") or "Hilfe-Assistent",
            "description": "Beantwortet Fragen zur Bedienung des Tools aus der mitgelieferten Doku.",
            "system_prompt": system_prompt, "icon": "🆘", "category": "Hilfe",
            "favorite": True, "rag_collections": [coll["id"]],
        })
        data.setdefault("tools", [])
        existing.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        agent_id = _HELP_AGENT_ID
    else:
        agent = AgentDef(
            id=_HELP_AGENT_ID,
            name="Hilfe-Assistent",
            description="Beantwortet Fragen zur Bedienung des Tools aus der mitgelieferten Doku.",
            system_prompt=system_prompt,
            tools=[],
            icon="🆘",
            category="Hilfe",
            favorite=True,
            rag_collections=[coll["id"]],
        )
        fp = _unique_agent_path(agent.name, exclude_id=agent.id)
        fp.write_text(json.dumps(agent.model_dump(), ensure_ascii=False, indent=2), encoding="utf-8")
        agent_id = agent.id

    return {"ok": True, "rag_collection_id": coll["id"], "agent_id": agent_id,
            "docs": ingested, "agent_name": "Hilfe-Assistent"}


@app.get("/api/help/guide")
async def help_guide():
    """Liefert die Handy-/FritzBox-Anleitung (docs/HANDY_ZUGRIFF.md) als Markdown
    für das Anleitungs-Fenster im Nutzerprofil."""
    fp = Path(__file__).parent / "docs" / "HANDY_ZUGRIFF.md"
    if not fp.is_file():
        raise HTTPException(status_code=404, detail="Anleitung nicht gefunden")
    return {"markdown": fp.read_text(encoding="utf-8", errors="ignore")}


# ── Diagnose-Logging ──────────────────────────────────────────────────────────


@app.get("/api/logs")
async def get_logs():
    if not LOG_FILE.exists():
        return []
    entries = []
    for line in LOG_FILE.read_text(encoding="utf-8").splitlines():
        if line.strip():
            try:
                entries.append(json.loads(line))
            except Exception:
                pass
    return entries


@app.delete("/api/logs")
async def clear_logs():
    if LOG_FILE.exists():
        LOG_FILE.unlink()
    return {"ok": True}


@app.put("/api/logs/config")
async def configure_logs(req: Request):
    global _log_active
    body = await req.json()
    _log_active = bool(body.get("active", False))
    return {"active": _log_active}


@app.get("/api/logs/active")
async def get_logs_active():
    return {"active": _log_active}


@app.post("/api/logs/entry")
async def add_log_entry(req: Request):
    body = await req.json()
    _write_log({k: v for k, v in body.items() if k != "ts"})
    return {"ok": True}


@app.get("/api/logs/download")
async def download_logs():
    if not LOG_FILE.exists():
        from fastapi.responses import Response as _Resp
        return _Resp("", media_type="text/plain")
    return FileResponse(
        LOG_FILE,
        media_type="application/octet-stream",
        filename=f"ai_framework_thomas_{time.strftime('%Y-%m-%d_%H-%M')}.log",
    )


# ── Setup-Endpunkte (Erststart-Konfiguration) ─────────────────────────────────

@app.get("/api/setup/embed-check")
async def setup_embed_check():
    """Prüft ob das konfigurierte Embedding-Modell in Ollama vorhanden ist."""
    embed_model = _CONFIG.get("embed_model", "nomic-embed-text")
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(f"{OLLAMA_BASE}/api/tags")
            names = {m["name"] for m in resp.json().get("models", [])}
            return {"ok": embed_model in names, "embed_model": embed_model}
    except Exception:
        return {"ok": False, "embed_model": embed_model}


@app.get("/api/platform")
async def get_platform():
    """Liefert die Betriebssystem-Plattform für die Onboarding-Maske."""
    import sys
    return {"platform": sys.platform}  # "linux", "win32", "darwin"


@app.post("/api/refine-document")
async def refine_document(req: Request):
    """Multi-Agenten-Verfeinerungsschleife: verbessert ein Dokument iterativ
    durch mehrere Agenten bis die Änderungsrate unter den Schwellwert fällt."""
    import difflib
    body = await req.json()
    text = (body.get("text") or "").strip()
    agents = body.get("agents") or []   # [{id, system_prompt, name}]
    threshold = float(body.get("threshold") or 2.0)   # % Änderungen für Stop
    max_iter = min(int(body.get("max_iterations") or 10), 50)
    model = _pick_model(body.get("model"))
    if not text:
        raise HTTPException(400, "Kein Dokumenttext")
    if not agents:
        raise HTTPException(400, "Mindestens ein Agent erforderlich")

    def _word_change_pct(old: str, new: str) -> float:
        a = old.split(); b = new.split()
        if not a and not b:
            return 0.0
        ratio = difflib.SequenceMatcher(None, a, b).ratio()
        return round((1.0 - ratio) * 100, 1)

    _tok = {"in": 0, "out": 0}

    async def _refine_once(current: str, agent: dict) -> str:
        sys_prompt = agent.get("system_prompt") or (
            "Verbessere den folgenden Text: korrigiere Fehler, verbessere Klarheit und Struktur. "
            "Gib NUR den verbesserten Text zurück, ohne Kommentare."
        )
        # Große Dokumente: in ~2000-Zeichen-Abschnitte aufteilen
        MAX = 4000
        if len(current) <= MAX:
            chunks = [current]
        else:
            paragraphs = current.split("\n\n")
            chunks, buf = [], ""
            for p in paragraphs:
                if len(buf) + len(p) > MAX and buf:
                    chunks.append(buf.strip())
                    buf = p
                else:
                    buf += ("\n\n" if buf else "") + p
            if buf:
                chunks.append(buf.strip())

        refined_parts = []
        for chunk in chunks:
            try:
                async with httpx.AsyncClient(timeout=180) as client:
                    resp = await _llm.chat(client,{
                        "model": model, "think": False, "stream": False,
                        "messages": [{"role": "system", "content": sys_prompt},
                                     {"role": "user", "content": chunk}],
                    })
                    resp.raise_for_status()
                    _rf_j = resp.json()
                    _a, _b = _llm_tok(_rf_j)
                    _tok["in"] += _a
                    _tok["out"] += _b
                    content = _rf_j.get("message", {}).get("content", "").strip()
                    content = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL).strip()
                    refined_parts.append(content if content else chunk)
            except Exception:
                refined_parts.append(chunk)
        return "\n\n".join(refined_parts)

    async def _stream():
        current = text
        async with _model_session(model):
            for iteration in range(1, max_iter + 1):
                agent = agents[(iteration - 1) % len(agents)]
                yield _sse({"type": "iteration_start", "n": iteration,
                            "agent": agent.get("name", f"Agent {iteration}")})
                try:
                    new_text = await _refine_once(current, agent)
                except Exception as e:
                    yield _sse({"type": "error", "message": str(e)})
                    return
                change_pct = _word_change_pct(current, new_text)
                current = new_text
                yield _sse({"type": "iteration_done", "n": iteration,
                            "agent": agent.get("name", f"Agent {iteration}"),
                            "change_pct": change_pct, "text": current})
                if change_pct < threshold:
                    yield _sse({"type": "converged", "n": iteration, "change_pct": change_pct,
                                "message": f"Konvergiert nach {iteration} Iterationen ({change_pct} % < {threshold} % Schwelle)"})
                    break
            yield _sse({"type": "done", "text": current, "tokens": _tok})

    return StreamingResponse(
        _stream(), media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/api/setup/config")
async def get_setup_config():
    """Gibt die aktuell aktiven Konfigurationswerte zurück (für Onboarding/Einstellungen)."""
    return {
        "default_model": _CONFIG.get("default_model", DEFAULT_MODEL),
        "data_dir": _CONFIG.get("data_dir", "data"),
    }


@app.put("/api/rag/collections/{cid}/server-path")
async def rag_set_server_path_endpoint(cid: str, req: Request):
    """Setzt oder löscht den Serverpfad einer RAG-Sammlung."""
    body = await req.json()
    sp = (body.get("server_path") or "").strip() or None
    coll = await _db.rag_get_collection(cid)
    if not coll:
        raise HTTPException(404, "Sammlung nicht gefunden")
    await _db.rag_set_server_path(cid, sp)
    return {"ok": True, "server_path": sp}


@app.post("/api/rag/collections/{cid}/publish")
async def rag_publish_collection(cid: str):
    """Exportiert eine RAG-Sammlung als .ragpack.json in den gespeicherten Serverpfad."""
    import base64
    coll = await _db.rag_get_collection(cid)
    if not coll:
        raise HTTPException(404, "Sammlung nicht gefunden")
    sp = (coll.get("server_path") or "").strip()
    if not sp:
        raise HTTPException(400, "Kein Serverpfad für diese Sammlung gesetzt")
    server_dir = Path(sp)
    if not server_dir.exists():
        raise HTTPException(400, f"Verzeichnis existiert nicht: {sp}")
    if not server_dir.is_dir():
        raise HTTPException(400, f"Pfad ist kein Verzeichnis: {sp}")
    data = await _db.rag_export_collection(cid)
    if not data:
        raise HTTPException(500, "Export fehlgeschlagen")
    # Embeddings base64-kodieren für JSON-Serialisierung
    for d in data["documents"]:
        for ch in d.get("chunks", []):
            ch["embedding"] = base64.b64encode(ch["embedding"]).decode()
    slug = re.sub(r"[^\w\-]+", "_", coll["name"])[:40] or cid[:8]
    out_file = server_dir / f"{slug}.ragpack.json"
    out_file.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"ok": True, "file": str(out_file), "n_chunks": sum(len(d.get("chunks", [])) for d in data["documents"])}


@app.get("/api/rag/server-packs")
async def rag_server_packs(dir: str = ""):
    """Listet alle .ragpack.json-Dateien in einem Verzeichnis auf."""
    p = Path(dir.strip())
    if not p.exists() or not p.is_dir():
        raise HTTPException(400, f"Verzeichnis nicht gefunden: {dir}")
    packs = []
    for f in sorted(p.glob("*.ragpack.json")):
        try:
            meta = json.loads(f.read_text(encoding="utf-8"))
            c = meta.get("collection", {})
            packs.append({
                "file": str(f),
                "name": c.get("name", f.stem),
                "n_docs": len(meta.get("documents", [])),
                "n_chunks": sum(len(d.get("chunks", [])) for d in meta.get("documents", [])),
                "created_at": c.get("created_at"),
            })
        except Exception:
            pass
    return packs


@app.post("/api/rag/collections/clone")
async def rag_clone_collection(req: Request):
    """Klont eine .ragpack.json-Datei in die lokale Datenbank (neue ID, kein server_path)."""
    import base64
    body = await req.json()
    file_path = (body.get("file_path") or "").strip()
    if not file_path:
        raise HTTPException(400, "file_path fehlt")
    p = Path(file_path)
    if not p.exists() or not p.is_file():
        raise HTTPException(400, f"Datei nicht gefunden: {file_path}")
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:
        raise HTTPException(400, f"Datei konnte nicht gelesen werden: {e}")
    coll = data.get("collection", {})
    docs = data.get("documents", [])
    # Neue IDs generieren um Kollisionen zu vermeiden
    new_cid = uuid.uuid4().hex
    coll = dict(coll)
    coll["id"] = new_cid
    coll.pop("server_path", None)  # Klon hat keinen Serverpfad
    id_map = {}
    new_docs = []
    for d in docs:
        new_did = f"doc_{uuid.uuid4().hex[:12]}"
        id_map[d["id"]] = new_did
        nd = dict(d)
        nd["id"] = new_did
        for ch in nd.get("chunks", []):
            if isinstance(ch.get("embedding"), str):
                ch["embedding"] = base64.b64decode(ch["embedding"])
        new_docs.append(nd)
    await _db.rag_import_collection(coll, new_docs)
    return {"ok": True, "id": new_cid, "name": coll.get("name"), "n_docs": len(new_docs)}


@app.post("/api/setup/config")
async def setup_config(req: Request):
    """Schreibt das gewählte Standard-Modell und optionalen Datenpfad in config.json."""
    global _CONFIG, DEFAULT_MODEL
    body = await req.json()
    model = _pick_model(body.get("default_model", ""))
    _CONFIG["default_model"] = model
    DEFAULT_MODEL = model
    if "data_dir" in body:
        raw = (body["data_dir"] or "").strip() or "data"
        _CONFIG["data_dir"] = raw
    try:
        _CONFIG_FILE.write_text(
            json.dumps({k: v for k, v in _CONFIG.items()}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception as e:
        raise HTTPException(500, f"config.json konnte nicht gespeichert werden: {e}")
    return {"ok": True, "default_model": model, "data_dir": _CONFIG.get("data_dir", "data")}


@app.post("/api/setup/systemd")
async def setup_systemd():
    """Richtet einen User-Systemd-Service ein (nur Linux, kein sudo nötig)."""
    import sys
    import subprocess
    if sys.platform != "linux":
        raise HTTPException(400, "Systemd ist nur unter Linux verfügbar.")

    app_dir = Path(__file__).parent.resolve()
    uvicorn_bin = app_dir / "venv" / "bin" / "uvicorn"
    host = _CONFIG.get("host", "127.0.0.1")
    port = _CONFIG.get("port", 8780)

    service = f"""[Unit]
Description=AI Framework Thomas
After=network.target

[Service]
Type=simple
WorkingDirectory={app_dir}
ExecStart={uvicorn_bin} main:app --host {host} --port {port}
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
"""
    systemd_dir = Path.home() / ".config" / "systemd" / "user"
    systemd_dir.mkdir(parents=True, exist_ok=True)
    service_file = systemd_dir / "ai_framework_thomas.service"
    service_file.write_text(service, encoding="utf-8")

    errors = []
    for cmd in [
        ["systemctl", "--user", "daemon-reload"],
        ["systemctl", "--user", "enable", "--now", "ai_framework_thomas"],
    ]:
        try:
            subprocess.run(cmd, check=True, capture_output=True, timeout=10)
        except Exception as e:
            errors.append(str(e))

    if errors:
        return {
            "ok": False,
            "errors": errors,
            "service_file": str(service_file),
            "hint": "systemctl --user enable --now ai_framework_thomas",
        }
    return {"ok": True, "service_file": str(service_file)}


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


def _med_transcript(messages: list) -> str:
    """Formt den bisherigen Verlauf in einen lesbaren Gesprächstext."""
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


@app.post("/api/medizin/extract")
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


@app.post("/api/medizin/consult")
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


@app.post("/api/medizin/translate")
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


# ── Mathe-Tutor: deterministische SymPy-Grundwahrheit ────────────────────────
#
# Kleine lokale Modelle rufen Verifikations-Werkzeuge mitten im Tutor-Dialog NICHT
# zuverlässig selbst auf (getestet: ministral-3:3b und qwen2.5-coder:7b ignorieren
# sie bzw. bestätigen falsche Schritte). Damit „werkzeuggeprüft" trotzdem echt ist,
# rechnet der Server hier die korrekte Lösung deterministisch mit SymPy und gibt sie
# dem Tutor-Agenten als verifizierte Fakten mit – statt es dem Modell zu überlassen.


def _mathe_sympy_facts(kind: str, sympy_str: str, goal: str) -> str:
    """Berechnet aus einem (vom LLM extrahierten) Ausdruck deterministisch die
    Grundwahrheit mit SymPy. Gibt eine kurze Faktenliste oder "" zurück."""
    if not sympy_str:
        return ""
    # Einfacher Zeichensatz-Schutz: nur mathematische Ausdrücke zulassen
    if not re.fullmatch(r"[0-9A-Za-z_+\-*/^().,=<>\[\] ]+", sympy_str):
        return ""
    expr = sympy_str.replace("^", "**").strip()
    expr = expr.replace("==", "=")  # Modell nutzt oft Python-Gleichheit „=="
    # „f(x) = …"-Präfix entfernen (häufige Schreibweise), reine rechte Seite behalten
    expr = re.sub(r"^[a-zA-Z]\s*\([a-zA-Z]\)\s*=\s*", "", expr)
    try:
        import sympy as sp
        from sympy.parsing.sympy_parser import (
            parse_expr, standard_transformations, implicit_multiplication_application)
        _tf = standard_transformations + (implicit_multiplication_application,)
        def _p(s):  # robust: versteht implizite Multiplikation (z. B. „2x" → 2*x)
            return parse_expr(s, transformations=_tf)
        facts: list[str] = []
        # Fall A: Modell lieferte bereits einen SymPy-Funktionsaufruf (diff(...), integrate(...), …)
        if re.match(r"^(diff|integrate|factor|expand|simplify|solve|limit|series|nsimplify)\s*\(", expr):
            res = sp.sympify(expr)
            facts.append(f"Ergebnis [SymPy]: {res}")
            return "\n".join(facts)
        # Fall B: Gleichung (enthält genau ein '=')
        if "=" in expr and "==" not in expr:
            lhs_s, rhs_s = expr.split("=", 1)
            lhs, rhs = _p(lhs_s), _p(rhs_s)
            eq = sp.Eq(lhs, rhs)
            syms = sorted(eq.free_symbols, key=lambda s: s.name)
            facts.append(f"Gleichung: {expr}")
            if syms:
                sols = sp.solve(eq, *syms)
                if sols:
                    facts.append(f"Lösung(en) [SymPy]: {sols}")
            poly = sp.expand(lhs - rhs)
            fac = sp.factor(poly)
            if str(fac) != str(poly):
                facts.append(f"Faktorisierung von ({poly}): {fac}")
            return "\n".join(facts)
        # Fall C: reiner Ausdruck – je nach Ziel ableiten/integrieren/faktorisieren
        e = _p(expr)
        facts.append(f"Ausdruck: {expr}")
        if goal == "diff":
            facts.append(f"Ableitung [SymPy]: {sp.diff(e)}")
        elif goal == "integrate":
            facts.append(f"Stammfunktion [SymPy]: {sp.integrate(e)} (+ C)")
        elif goal == "factor":
            facts.append(f"Faktorisiert [SymPy]: {sp.factor(e)}")
        elif goal == "solve":
            syms = sorted(e.free_symbols, key=lambda s: s.name)
            if syms:
                facts.append(f"Nullstellen [SymPy]: {sp.solve(e, *syms)}")
        else:
            simp = sp.simplify(e)
            facts.append(f"Vereinfacht [SymPy]: {simp}")
            fac = sp.factor(e)
            if str(fac) != str(e) and str(fac) != str(simp):
                facts.append(f"Faktorisiert [SymPy]: {fac}")
        return "\n".join(facts)
    except Exception:
        return ""


async def _mathe_ground_facts(client, model, messages, tok: Optional[dict] = None) -> str:
    """Extrahiert die zentrale Aufgabe aus dem Gespräch und liefert die
    SymPy-verifizierte Grundwahrheit als Fakten-String (oder "" wenn nichts
    deterministisch prüfbar ist). Erwartet einen offenen httpx-Client, dessen
    Modell bereits unter ``_model_session`` geladen wurde.
    ``tok`` (optional): dict {"in","out"}, in das der Tokenverbrauch summiert wird."""
    transcript = _med_transcript(messages)  # gleiche Formatierung wie Medizin
    if not transcript:
        return ""
    try:
        resp = await _llm.chat(client, {
            "model": model, "think": False, "stream": False,
            "messages": [
                {"role": "system", "content": (
                    "Extrahiere die zentrale mathematische Aufgabe aus dem Gespräch. "
                    "Antworte NUR mit JSON in genau diesem Format, ohne weiteren Text: "
                    '{"kind":"equation|expression|none","sympy":"<SymPy-auswertbarer Ausdruck, '
                    'Gleichungen mit = , Potenz mit ** , keine Worte>","goal":"solve|factor|diff|'
                    'integrate|simplify|none"}. Bei reinen Theorie-/Wortaufgaben ohne klaren '
                    'Ausdruck: kind=none.')},
                {"role": "user", "content": f"Gespräch:\n{transcript[:2000]}"},
            ],
        })
        resp.raise_for_status()
        _mg_j = resp.json()
        if tok is not None:
            _a, _b = _llm_tok(_mg_j)
            tok["in"] += _a
            tok["out"] += _b
        raw = _mg_j.get("message", {}).get("content", "") or ""
    except Exception:
        return ""

    raw = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL)
    m = re.search(r"\{[\s\S]*\}", raw)
    if not m:
        return ""
    try:
        data = json.loads(m.group(0))
    except Exception:
        return ""

    kind = str(data.get("kind", "none")).strip().lower()
    sympy_str = str(data.get("sympy", "")).strip()
    goal = str(data.get("goal", "none")).strip().lower()
    if not sympy_str or sympy_str.lower() in ("none", "null"):
        return ""
    return _mathe_sympy_facts(kind, sympy_str, goal)


def _mathe_check_tokens(facts: str) -> list[str]:
    """Zieht aus den SymPy-Fakten die numerischen Ergebnis-Tokens, gegen die eine
    Modell-Lösung strikt geprüft werden kann (z. B. Gleichungslösungen)."""
    toks: list[str] = []
    for line in facts.splitlines():
        if "[SymPy]" not in line or ":" not in line:
            continue
        val = line.split(":", 1)[1]
        for piece in re.split(r"[\[\]\{\}(),=\s]+", val):
            piece = piece.strip()
            if re.fullmatch(r"-?\d+(?:\.\d+)?", piece):
                toks.append(piece)
    return list(dict.fromkeys(toks))


def _mathe_solution_ok(text: str, tokens: list[str]) -> bool:
    """True, wenn alle erwarteten Ergebnis-Tokens in der Modell-Lösung vorkommen."""
    if not tokens:
        return False
    norm = re.sub(r"\s+", "", text or "")
    return all(t in norm for t in tokens)


@app.post("/api/mathe/ground")
async def mathe_ground(req: Request):
    """Extrahiert die zentrale Aufgabe aus dem Tutor-Gespräch und liefert die
    SymPy-verifizierte Grundwahrheit als Fakten zurück (für den Tutor-Modus).
    Gibt {facts: ""} zurück, wenn nichts deterministisch prüfbar ist."""
    body = await req.json()
    messages = body.get("messages") or []
    model = _pick_model(body.get("model"), _model_for("general"))
    _tok = {"in": 0, "out": 0}
    async with _model_session(model), httpx.AsyncClient(timeout=90) as client:
        facts = await _mathe_ground_facts(client, model, messages, tok=_tok)
    return {"facts": facts, "tokens": _tok}


_MATHE_VERIFY_ROUNDS = 2  # zusätzliche Korrekturrunden nach dem ersten Lösungsversuch


@app.post("/api/mathe/solve-verified")
async def mathe_solve_verified(req: Request):
    """Agentischer Verifikationsloop (freie Adaption des „Agentic-AI + Simulink"-
    Konzepts): das Modell löst die Aufgabe, die Lösung wird deterministisch mit
    SymPy geprüft; weicht sie ab, fließt die SymPy-Wahrheit als Korrektur zurück
    und das Modell rechnet erneut (max. _MATHE_VERIFY_ROUNDS Korrekturrunden).
    Streamt SSE-Frames: stage (solve|verify|fix), text (Endlösung), done, error."""
    body = await req.json()
    messages = body.get("messages") or []
    model = _pick_model(body.get("model"), _model_for("coding"))

    async def gen():
        _tok = {"in": 0, "out": 0}
        try:
            async with _model_session(model), httpx.AsyncClient(timeout=180) as client:
                # 1) Deterministische Grundwahrheit
                yield _sse({"type": "stage", "stage": "verify", "status": "start",
                            "label": "SymPy-Grundwahrheit"})
                facts = await _mathe_ground_facts(client, model, messages, tok=_tok)
                tokens = _mathe_check_tokens(facts) if facts else []
                yield _sse({"type": "stage", "stage": "verify", "status": "done",
                            "label": "SymPy-Grundwahrheit",
                            "content": facts or "Keine deterministisch prüfbaren Fakten – nur Plausibilität."})

                task = _med_transcript(messages)
                solution = ""
                verified = False
                rounds = 0
                for rnd in range(_MATHE_VERIFY_ROUNDS + 1):
                    rounds = rnd + 1
                    is_fix = rnd > 0
                    label = "Korrektur" if is_fix else "Lösung"
                    stage = "fix" if is_fix else "solve"
                    yield _sse({"type": "stage", "stage": stage, "status": "start", "label": label})

                    sys = ("Du bist ein sorgfältiger Mathe-Experte. Löse die Aufgabe Schritt für "
                           "Schritt und gib am Ende das Endergebnis klar an. Formeln in LaTeX ($…$).")
                    user = f"Aufgabe:\n{task}"
                    if is_fix and facts:
                        user += (f"\n\nDeine bisherige Lösung stimmt NICHT mit der deterministischen "
                                 f"SymPy-Berechnung überein.\nSymPy-Fakten:\n{facts}\n\nBisherige Lösung:\n"
                                 f"{solution}\n\nKorrigiere und gib die vollständige, korrekte Lösung mit "
                                 f"dem richtigen Endergebnis an.")
                    try:
                        resp = await _llm.chat(client, {
                            "model": model, "think": False, "stream": False,
                            "messages": [{"role": "system", "content": sys},
                                         {"role": "user", "content": user}],
                        })
                        resp.raise_for_status()
                        _sv_j = resp.json()
                        _a, _b = _llm_tok(_sv_j)
                        _tok["in"] += _a
                        _tok["out"] += _b
                        solution = _sv_j.get("message", {}).get("content", "") or ""
                        solution = re.sub(r"<think>.*?</think>", "", solution, flags=re.DOTALL).strip()
                    except Exception as e:
                        yield _sse({"type": "error", "content": f"Modellfehler: {e}"})
                        return
                    yield _sse({"type": "stage", "stage": stage, "status": "done",
                                "label": label, "content": solution})

                    if not tokens:
                        break  # nicht strikt prüfbar → erste Lösung steht
                    if _mathe_solution_ok(solution, tokens):
                        verified = True
                        break

                yield _sse({"type": "text", "content": solution})
                yield _sse({"type": "done", "verified": verified, "checkable": bool(tokens),
                            "rounds": rounds, "facts": facts, "tokens": _tok})
        except Exception as e:
            yield _sse({"type": "error", "content": str(e)})

    return StreamingResponse(gen(), media_type="text/event-stream")


# ══════════════════════════════════════════════════════════════════════════════
# Variantenvergleich (gewichtete Entscheidung, AHP-Hybrid)
# ══════════════════════════════════════════════════════════════════════════════
# Persistenz je Vergleich in VARIANTEN_DIR/<name>/decision.json. Die Gewichte
# (Paarvergleich) und das Ranking werden **deterministisch** in tools/decision.py
# gerechnet — nie vom LLM. Das LLM schlägt nur Kriterien/Varianten/Urteile vor.

def _var_safe_name(name: str) -> str:
    safe = "".join(c for c in str(name or "") if c.isalnum() or c in (" ", "_", "-")).strip()
    if not safe or safe.startswith("_"):
        raise HTTPException(status_code=400, detail="Ungültiger Name")
    return safe


def _var_dir(name: str, create: bool = False) -> Path:
    d = VARIANTEN_DIR / _var_safe_name(name)
    if create:
        d.mkdir(parents=True, exist_ok=True)
    elif not d.exists():
        raise HTTPException(status_code=404, detail="Vergleich nicht gefunden")
    return d


def _var_compute(data: dict) -> dict:
    """Deterministische Kennzahlen (Gewichte + Ranking) aus den Rohdaten rechnen."""
    from tools import decision as _dec
    criteria = data.get("criteria") or []
    variants = data.get("variants") or []
    directions = [(c.get("direction") if c.get("direction") in ("benefit", "cost") else "benefit")
                  for c in criteria]
    pairwise = data.get("pairwise") or []
    if pairwise and len(pairwise) == len(criteria) and len(criteria) >= 2:
        pw = _dec.pairwise_weights(pairwise)
        weights = pw["weights"]
    else:
        weights = _dec.equal_weights(len(criteria))
        pw = {"weights": weights, "cr": 0.0, "consistent": True, "lambda_max": float(len(criteria)), "n": len(criteria)}
    ratings = data.get("ratings") or []
    sc = _dec.score_variants(weights, ratings, directions)
    best = sc.get("best")
    result = {
        "weights": weights,
        "cr": pw.get("cr", 0.0),
        "consistent": pw.get("consistent", True),
        "lambda_max": pw.get("lambda_max", 0.0),
        "scores": sc.get("scores", []),
        "ranking": sc.get("ranking", []),
        "best": best,
        "best_name": (variants[best].get("name") if (best is not None and best < len(variants)) else ""),
    }
    return result


def _var_load(name: str) -> dict:
    p = _var_dir(name) / "decision.json"
    if not p.exists():
        return {"title": name, "description": "", "criteria": [], "variants": [],
                "pairwise": [], "ratings": [], "result": {}}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        data = {}
    data.setdefault("title", name)
    data["result"] = _var_compute(data)
    return data


def _var_save(name: str, body: dict) -> dict:
    d = _var_dir(name, create=True)
    criteria = [{"name": str(c.get("name", "")).strip()[:120],
                 "direction": (c.get("direction") if c.get("direction") in ("benefit", "cost") else "benefit")}
                for c in (body.get("criteria") or []) if str(c.get("name", "")).strip()]
    variants = [{"name": str(v.get("name", "")).strip()[:120],
                 "description": str(v.get("description", "")).strip()[:2000]}
                for v in (body.get("variants") or []) if str(v.get("name", "")).strip()]
    data = {
        "title": str(body.get("title", name)).strip()[:200] or name,
        "description": str(body.get("description", "")).strip()[:4000],
        "criteria": criteria,
        "variants": variants,
        "pairwise": body.get("pairwise") or [],
        "ratings": body.get("ratings") or [],
        "updated_at": time.time(),
    }
    data["result"] = _var_compute(data)
    (d / "decision.json").write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return data


@app.get("/api/varianten/projects")
async def varianten_list():
    out = []
    if VARIANTEN_DIR.exists():
        for d in sorted(VARIANTEN_DIR.iterdir()):
            if not d.is_dir() or d.name.startswith("_"):
                continue
            p = d / "decision.json"
            meta = {}
            if p.exists():
                try:
                    meta = json.loads(p.read_text(encoding="utf-8"))
                except Exception:
                    meta = {}
            res = _var_compute(meta) if meta else {}
            out.append({
                "name": d.name,
                "title": meta.get("title") or d.name,
                "n_criteria": len(meta.get("criteria") or []),
                "n_variants": len(meta.get("variants") or []),
                "best_name": res.get("best_name", ""),
                "updated_at": meta.get("updated_at", 0),
            })
    out.sort(key=lambda x: x.get("updated_at", 0), reverse=True)
    return out


@app.post("/api/varianten/projects")
async def varianten_create(req: Request):
    body = await req.json()
    name = _var_safe_name(body.get("name", ""))
    d = VARIANTEN_DIR / name
    if d.exists():
        raise HTTPException(status_code=409, detail="Vergleich existiert bereits")
    return _var_save(name, {"title": body.get("title", name)})


@app.get("/api/varianten/projects/{name}")
async def varianten_get(name: str):
    return _var_load(name)


@app.put("/api/varianten/projects/{name}")
async def varianten_put(name: str, req: Request):
    body = await req.json()
    return _var_save(name, body)


@app.delete("/api/varianten/projects/{name}")
async def varianten_delete(name: str):
    import shutil
    d = _var_dir(name)
    shutil.rmtree(d, ignore_errors=True)
    return {"ok": True}


_VAR_CRITERIA_SYSTEM = (
    "Du hilfst bei einer Entscheidung (Variantenvergleich). Nenne die wichtigsten "
    "ENTSCHEIDUNGSKRITERIEN, nach denen die Varianten bewertet werden sollten — "
    "konkret, überschneidungsfrei, 4–8 Stück. Gib pro Kriterium an, ob ein hoher "
    "Wert gut ('benefit', z. B. Qualität) oder schlecht ('cost', z. B. Preis) ist. "
    'Antworte NUR mit JSON: {"criteria":[{"name":"<Kriterium>","direction":"benefit|cost"}]}.'
)
_VAR_VARIANTS_SYSTEM = (
    "Du hilfst bei einer Entscheidung. Schlage sinnvolle, klar unterscheidbare "
    "VARIANTEN/Alternativen vor (3–6), jeweils mit kurzer Beschreibung. "
    'Antworte NUR mit JSON: {"variants":[{"name":"<Variante>","description":"<kurz>"}]}.'
)
_VAR_PAIRWISE_SYSTEM = (
    "Du schätzt die relative Wichtigkeit von Entscheidungskriterien im PAARVERGLEICH "
    "(Saaty-Skala 1–9: 1=gleich wichtig, 3=etwas wichtiger, 5=deutlich, 7=sehr, "
    "9=extrem wichtiger; Zwischenwerte erlaubt). Du bekommst eine nummerierte "
    "Kriterienliste. Gib für jedes Paar (i<j) an, um welchen Faktor Kriterium i "
    "wichtiger ist als j (Wert <1, wenn j wichtiger ist). "
    'Antworte NUR mit JSON: {"pairs":[{"i":0,"j":1,"value":3,"grund":"<kurz>"}]}.'
)
_VAR_RATINGS_SYSTEM = (
    "Du bewertest VARIANTEN je KRITERIUM auf einer Skala von 1 (sehr schlecht) bis "
    "10 (sehr gut) — immer so, dass 10 = am besten ist (auch bei Kosten: 10 = "
    "günstigste Variante). Stütze dich auf die gegebenen Variantenbeschreibungen "
    "und den Kontext; erfinde keine Fakten, im Zweifel neutral (5). "
    'Antworte NUR mit JSON: {"ratings":[{"variant":0,"scores":[{"criterion":0,"value":7}]}]}.'
)
_VAR_EXPLAIN_SYSTEM = (
    "Du erklärst das Ergebnis eines gewichteten Variantenvergleichs (Nutzwertanalyse). "
    "Du bekommst die deterministisch berechneten Gewichte, das Ranking und eine "
    "Sensitivitätsangabe (bei welchem Kriterium der Sieger wechselt). Fasse in "
    "2–4 Sätzen zusammen: Wer gewinnt und warum, wie knapp/robust das ist, worauf "
    "man achten sollte. Rechne KEINE Zahlen neu; nutze die gegebenen Werte."
)


@app.post("/api/varianten/suggest-criteria")
async def varianten_suggest_criteria(req: Request):
    body = await req.json()
    model = _pick_model(body.get("model"), _model_for("general"))
    prompt = (f"Entscheidung: {str(body.get('title','')).strip()}\n"
              f"Beschreibung: {str(body.get('description','')).strip()[:2000]}")
    data, ti, to, _ = await _research_llm_json(model, _VAR_CRITERIA_SYSTEM, prompt)
    crits = []
    for c in (data.get("criteria") or []):
        nm = str((c or {}).get("name", "")).strip()[:120]
        if nm:
            d = (c or {}).get("direction")
            crits.append({"name": nm, "direction": d if d in ("benefit", "cost") else "benefit"})
    return {"criteria": crits, "tokens": {"in": ti, "out": to}}


@app.post("/api/varianten/suggest-variants")
async def varianten_suggest_variants(req: Request):
    body = await req.json()
    model = _pick_model(body.get("model"), _model_for("general"))
    crit_names = ", ".join(str(c.get("name", "")).strip() for c in (body.get("criteria") or []) if c.get("name"))
    prompt = (f"Entscheidung: {str(body.get('title','')).strip()}\n"
              f"Beschreibung: {str(body.get('description','')).strip()[:2000]}\n"
              f"Kriterien: {crit_names}")
    data, ti, to, _ = await _research_llm_json(model, _VAR_VARIANTS_SYSTEM, prompt)
    variants = []
    for v in (data.get("variants") or []):
        nm = str((v or {}).get("name", "")).strip()[:120]
        if nm:
            variants.append({"name": nm, "description": str((v or {}).get("description", "")).strip()[:2000]})
    return {"variants": variants, "tokens": {"in": ti, "out": to}}


@app.post("/api/varianten/suggest-pairwise")
async def varianten_suggest_pairwise(req: Request):
    body = await req.json()
    criteria = [str(c.get("name", "")).strip() for c in (body.get("criteria") or []) if c.get("name")]
    n = len(criteria)
    if n < 2:
        return {"pairwise": [], "rationale": [], "tokens": {"in": 0, "out": 0}}
    model = _pick_model(body.get("model"), _model_for("general"))
    lst = "\n".join(f"{i}: {c}" for i, c in enumerate(criteria))
    prompt = (f"Entscheidung: {str(body.get('title','')).strip()}\n\nKriterien:\n{lst}")
    data, ti, to, _ = await _research_llm_json(model, _VAR_PAIRWISE_SYSTEM, prompt)
    # Vollständige Matrix aufbauen (Diagonale 1, Reziprozität); Rest 1 (Gleichstand)
    matrix = [[1.0] * n for _ in range(n)]
    rationale = []
    for pr in (data.get("pairs") or []):
        try:
            i, j = int(pr.get("i")), int(pr.get("j"))
            val = float(pr.get("value"))
        except (TypeError, ValueError):
            continue
        if 0 <= i < n and 0 <= j < n and i != j and val > 0:
            val = max(1.0 / 9.0, min(9.0, val))
            matrix[i][j] = val
            matrix[j][i] = 1.0 / val
            g = str(pr.get("grund", "")).strip()[:200]
            if g:
                rationale.append({"i": i, "j": j, "grund": g})
    return {"pairwise": matrix, "rationale": rationale, "tokens": {"in": ti, "out": to}}


@app.post("/api/varianten/suggest-ratings")
async def varianten_suggest_ratings(req: Request):
    body = await req.json()
    criteria = [str(c.get("name", "")).strip() for c in (body.get("criteria") or []) if c.get("name")]
    variants = [{"name": str(v.get("name", "")).strip(), "description": str(v.get("description", "")).strip()}
                for v in (body.get("variants") or []) if v.get("name")]
    nc, nv = len(criteria), len(variants)
    if nc == 0 or nv == 0:
        return {"ratings": [], "tokens": {"in": 0, "out": 0}}
    # Recherche-Modell (respektiert „Web-Recherche lokal"); optional RAG-Kontext
    model, err = await _research_model(body.get("model"))
    if err:
        raise HTTPException(status_code=503, detail=err)
    rag_ctx = await _plan_rag_context(body.get("collection_ids"),
                                      str(body.get("title", "")) + " " + " ".join(criteria))
    clist = "\n".join(f"{i}: {c}" for i, c in enumerate(criteria))
    vlist = "\n".join(f"{i}: {v['name']} — {v['description'][:400]}" for i, v in enumerate(variants))
    prompt = (f"Entscheidung: {str(body.get('title','')).strip()}\n\nKriterien:\n{clist}\n\n"
              f"Varianten:\n{vlist}")
    if rag_ctx:
        prompt += f"\n\nBelegkontext (aus Quellen):\n{rag_ctx[:3000]}"
    data, ti, to, _ = await _research_llm_json(model, _VAR_RATINGS_SYSTEM, prompt)
    ratings = [[5.0] * nc for _ in range(nv)]
    for rv in (data.get("ratings") or []):
        try:
            vi = int(rv.get("variant"))
        except (TypeError, ValueError):
            continue
        if not (0 <= vi < nv):
            continue
        for sc in (rv.get("scores") or []):
            try:
                ci, val = int(sc.get("criterion")), float(sc.get("value"))
            except (TypeError, ValueError):
                continue
            if 0 <= ci < nc:
                ratings[vi][ci] = max(1.0, min(10.0, val))
    return {"ratings": ratings, "tokens": {"in": ti, "out": to}}


@app.post("/api/varianten/explain")
async def varianten_explain(req: Request):
    body = await req.json()
    name = body.get("name")
    data = _var_load(name) if name else body.get("data") or {}
    res = data.get("result") or _var_compute(data)
    criteria = data.get("criteria") or []
    variants = data.get("variants") or []
    if not res.get("ranking"):
        return {"text": "Noch keine vollständige Bewertung vorhanden.", "tokens": {"in": 0, "out": 0}}
    from tools import decision as _dec
    directions = [(c.get("direction") if c.get("direction") in ("benefit", "cost") else "benefit") for c in criteria]
    sens = _dec.sensitivity(res.get("weights") or [], data.get("ratings") or [], directions)
    wtxt = "\n".join(f"- {criteria[i].get('name','?')}: Gewicht {res['weights'][i]:.2f}"
                     for i in range(min(len(criteria), len(res.get("weights") or []))))
    rtxt = "\n".join(f"{r['index']+1}. {variants[r['index']].get('name','?')} — Nutzwert {r['score']:.2f} ({r['percent']}%)"
                     for r in res["ranking"] if r["index"] < len(variants))
    flip = [criteria[s["criterion"]].get("name", "?") for s in sens
            if s.get("flips") and s["criterion"] < len(criteria)]
    stxt = ("Sieger wechselt bei stärkerer Gewichtung von: " + ", ".join(flip)) if flip else \
           "Der Sieger bleibt auch bei moderat veränderten Gewichten stabil."
    model = _pick_model(body.get("model"), _model_for("general"))
    prompt = (f"Entscheidung: {data.get('title','')}\n\nGewichte:\n{wtxt}\n\n"
              f"Konsistenz CR={res.get('cr',0):.2f} ({'ok' if res.get('consistent') else 'zu inkonsistent'}).\n\n"
              f"Ranking:\n{rtxt}\n\nSensitivität: {stxt}")
    text, ti, to = "", 0, 0
    try:
        async with _model_session(model), httpx.AsyncClient(timeout=120) as client:
            resp = await _llm.chat(client, {
                "model": model, "think": False, "stream": False,
                "messages": [{"role": "system", "content": _VAR_EXPLAIN_SYSTEM},
                             {"role": "user", "content": prompt}],
                "options": {"num_ctx": _profile_num_ctx()}, "keep_alive": KEEP_ALIVE,
            })
            resp.raise_for_status()
        j = resp.json()
        ti, to = _llm_tok(j)
        text = re.sub(r"<think>.*?</think>", "", j.get("message", {}).get("content", ""), flags=re.DOTALL).strip()
    except Exception as e:
        text = f"(Analyse nicht möglich: {e})"
    return {"text": text, "tokens": {"in": ti, "out": to}}


@app.post("/api/varianten/auto-fill")
async def varianten_auto_fill(req: Request):
    """Aus einer Problembeschreibung die KOMPLETTE Bewertungstabelle erzeugen.

    Orchestriert die vorhandenen Einzelschritte (Kriterien → Paarvergleich →
    Varianten → Bewertungen) in einem Durchlauf, optional mit Web-Grounding.
    Reine Vorschläge; Gewichte/Ranking rechnet weiterhin der PUT (``_var_compute``)
    deterministisch. Lokal-bevorzugt über ``_research_model`` (respektiert
    „Web-Recherche lokal"/Geheim-Modus)."""
    body = await req.json()
    title = str(body.get("title", "")).strip()[:200]
    description = str(body.get("description", "")).strip()[:4000]
    if not (title or description):
        raise HTTPException(status_code=400, detail="Bitte das Problem beschreiben.")
    model, err = await _research_model(body.get("model"))
    if err:
        raise HTTPException(status_code=503, detail=err)

    tok = {"in": 0, "out": 0}

    def _add(a, b):
        tok["in"] += a
        tok["out"] += b

    base = (f"Entscheidung: {title}\n" if title else "") + \
           (f"Beschreibung: {description}\n" if description else "")

    # 1) Optionale Web-Recherche als Grounding (nur der Web-Query ist extern; das LLM
    #    bleibt bei Geheim-Modus lokal). Fehler dürfen die Generierung nicht stoppen.
    ground = ""
    sources: list = []
    if body.get("web"):
        try:
            from tools.search import search_with_sources
            q = (title + " " + description).strip()[:200]
            src, text = await search_with_sources(q, 5)
            sources = src or []
            if text:
                ground = f"\n\nBelegkontext aus Web-Recherche:\n{text[:2800]}"
        except Exception:
            pass   # ohne Grounding weiter

    # 2) Kriterien
    data, ti, to, _ = await _research_llm_json(model, _VAR_CRITERIA_SYSTEM, base + ground)
    _add(ti, to)
    criteria = []
    for c in (data.get("criteria") or []):
        nm = str((c or {}).get("name", "")).strip()[:120]
        if nm:
            d = (c or {}).get("direction")
            criteria.append({"name": nm, "direction": d if d in ("benefit", "cost") else "benefit"})
    nc = len(criteria)

    # 3) Paarvergleich (vollständige nc×nc-Matrix mit Reziprozität)
    pairwise = [[1.0] * nc for _ in range(nc)]
    if nc >= 2:
        clist = "\n".join(f"{i}: {c['name']}" for i, c in enumerate(criteria))
        pdata, ti, to, _ = await _research_llm_json(
            model, _VAR_PAIRWISE_SYSTEM, base + "\nKriterien:\n" + clist)
        _add(ti, to)
        for pr in (pdata.get("pairs") or []):
            try:
                i, j = int(pr.get("i")), int(pr.get("j"))
                val = float(pr.get("value"))
            except (TypeError, ValueError):
                continue
            if 0 <= i < nc and 0 <= j < nc and i != j and val > 0:
                val = max(1.0 / 9.0, min(9.0, val))
                pairwise[i][j] = val
                pairwise[j][i] = 1.0 / val

    # 4) Varianten — aus der Problembeschreibung (NICHT an die evtl. lange, verbose
    #    Kriterienliste gekoppelt: das blähte den Prompt auf und ließ kleine Modelle
    #    das JSON verwerfen). Nur kompakte Kriterien-Kurznamen als Kontext.
    crit_hint = ", ".join(c["name"].split("(")[0].strip()[:40] for c in criteria[:8])
    def _parse_variants(vd):
        out = []
        for v in (vd.get("variants") or []):
            nm = str((v or {}).get("name", "")).strip()[:120]
            if nm:
                out.append({"name": nm, "description": str((v or {}).get("description", "")).strip()[:2000]})
        return out
    vprompt = base + (f"\nKriterien (nur Kontext): {crit_hint}" if crit_hint else "") + ground
    vdata, ti, to, _ = await _research_llm_json(model, _VAR_VARIANTS_SYSTEM, vprompt)
    _add(ti, to)
    variants = _parse_variants(vdata)
    if not variants:   # Rückfall: minimaler Prompt (nur Problem), einmalig
        vdata, ti, to, _ = await _research_llm_json(model, _VAR_VARIANTS_SYSTEM, base)
        _add(ti, to)
        variants = _parse_variants(vdata)
    nv = len(variants)

    # 5) Bewertungen (nv×nc, 1–10; Standard 5)
    ratings = [[5.0] * nc for _ in range(nv)]
    if nc and nv:
        clist = "\n".join(f"{i}: {c['name']}" for i, c in enumerate(criteria))
        vlist = "\n".join(f"{i}: {v['name']} — {v['description'][:400]}" for i, v in enumerate(variants))
        rdata, ti, to, _ = await _research_llm_json(
            model, _VAR_RATINGS_SYSTEM,
            base + f"\nKriterien:\n{clist}\n\nVarianten:\n{vlist}" + ground)
        _add(ti, to)
        for rv in (rdata.get("ratings") or []):
            try:
                vi = int(rv.get("variant"))
            except (TypeError, ValueError):
                continue
            if not (0 <= vi < nv):
                continue
            for sc in (rv.get("scores") or []):
                try:
                    ci, val = int(sc.get("criterion")), float(sc.get("value"))
                except (TypeError, ValueError):
                    continue
                if 0 <= ci < nc:
                    ratings[vi][ci] = max(1.0, min(10.0, val))

    return {"criteria": criteria, "variants": variants, "pairwise": pairwise,
            "ratings": ratings, "sources": sources, "tokens": tok}


# ══════════════════════════════════════════════════════════════════════════════
# KI-To-Do-Listen mit Wissensgraph
# ══════════════════════════════════════════════════════════════════════════════
# Persistenz je Liste in TODO_DIR/<name>/list.json (Items, Kanten, Graph-Positionen).
# Struktur-Logik in tools/todo.py; hier HTTP + LLM-Helfer (Extraktion/Verknüpfung).

TODO_ATT_DIR = DATA_DIR / "todo_att"   # Original-Anlagen je Punkt (MD-Text liegt in der DB)


def _todo_root_name() -> str:
    """Name des Wurzelprojekts = Benutzername aus dem Profil (Rückfall: default_project)."""
    p = _load_profile()
    nm = " ".join(x for x in (
        str(p.get("first_name", "")).strip(), str(p.get("last_name", "")).strip()) if x).strip()
    return nm or str(p.get("default_project", "")).strip() or "Meine To-Dos"


def _todo_build_tree(projects: list) -> list:
    """Flache Projektliste (aus der DB) in einen verschachtelten Baum überführen."""
    by_parent: dict = {}
    for p in projects:
        by_parent.setdefault(p.get("parent_id") or None, []).append(p)

    def _kids(pid):
        out = []
        for p in by_parent.get(pid, []):
            node = dict(p)
            node["children"] = _kids(p["id"])
            out.append(node)
        return out
    return _kids(None)


@app.get("/api/todo/tree")
async def todo_tree():
    await _db.todo_root_ensure(_todo_root_name())
    projects = await _db.todo_projects_all()
    return {"tree": _todo_build_tree(projects), "flat": projects}


@app.post("/api/todo/projects")
async def todo_project_create(req: Request):
    body = await req.json()
    await _db.todo_root_ensure(_todo_root_name())
    pid = "tp_" + uuid.uuid4().hex[:12]
    parent = str(body.get("parent_id", "") or "").strip() or "root"
    name = str(body.get("name", "")).strip() or "Projekt"
    return await _db.todo_project_create({
        "id": pid, "name": name, "parent_id": parent,
        "type": body.get("type", "projekt"), "title": body.get("title", name),
        "participants": body.get("participants") or [],
    })


@app.get("/api/todo/projects/{pid}")
async def todo_project_get(pid: str):
    proj = await _db.todo_project_get(pid)
    if not proj:
        raise HTTPException(status_code=404, detail="Projekt nicht gefunden")
    return proj


@app.put("/api/todo/projects/{pid}")
async def todo_project_save(pid: str, req: Request):
    from tools import todo as _todo
    body = await req.json()
    clean = _todo.sanitize_list(body)   # Items/Kanten/Teilnehmer normalisieren
    header = {
        "type": clean.get("type", "projekt"), "title": clean.get("title", ""),
        "date": clean.get("date", ""), "participants": clean.get("participants") or [],
        "project_ref": clean.get("project_id", ""),
        "settings": {**(clean.get("settings") or {}), "positions": clean.get("positions") or {}},
    }
    saved = await _db.todo_save_project(pid, header, clean.get("items") or [], clean.get("edges") or [])
    if not saved:
        raise HTTPException(status_code=404, detail="Projekt nicht gefunden")
    return saved


@app.post("/api/todo/projects/{pid}/rename")
async def todo_project_rename(pid: str, req: Request):
    body = await req.json()
    await _db.todo_project_rename(pid, str(body.get("name", "")).strip() or "Projekt")
    return {"ok": True}


@app.post("/api/todo/projects/{pid}/move")
async def todo_project_move(pid: str, req: Request):
    body = await req.json()
    await _db.todo_project_move(pid, str(body.get("parent_id", "") or "").strip() or "root")
    return {"ok": True}


@app.delete("/api/todo/projects/{pid}")
async def todo_project_delete(pid: str, reparent: bool = False):
    await _db.todo_project_delete(pid, reparent=reparent)
    return {"ok": True}


_TODO_EXTRACT_SYSTEM = (
    "Du wandelst eine Besprechungsnotiz / einen Freitext in eine strukturierte "
    "AUFGABENLISTE (To-Dos) um. Erkenne einzelne konkrete Aufgaben, den/die "
    "Zuständigen (nur wenn genannt; nutze bevorzugt die vorgegebenen Teilnehmer), "
    "eine Fälligkeit (falls genannt, Format wie im Text) und Abhängigkeiten "
    "zwischen Aufgaben (welche Aufgabe muss vor einer anderen erledigt sein). "
    "Nummeriere die Aufgaben ab 0 und verweise in 'blockiert_von' auf diese Nummern. "
    'Antworte NUR mit JSON: {"items":[{"n":0,"text":"<Aufgabe>","assignees":["<Name>"],'
    '"due":"<Frist>","blockiert_von":[<n>]}]}.'
)
_TODO_LINKS_SYSTEM = (
    "Du findest inhaltliche VERKNÜPFUNGEN zwischen bestehenden Aufgaben (To-Dos). "
    "Du bekommst eine nummerierte Aufgabenliste. Nenne sinnvolle gerichtete "
    "Beziehungen (source→target) mit kurzem Label (z. B. 'blockiert', 'gehört zu', "
    "'folgt auf'). Nur echte Zusammenhänge, keine erzwungenen. "
    'Antworte NUR mit JSON: {"edges":[{"source":<n>,"target":<n>,"label":"<kurz>"}]}.'
)
_TODO_NEXT_SYSTEM = (
    "Du bist ein pragmatischer Projekt-Assistent. Aus der gegebenen Aufgabenliste "
    "(mit Status und Abhängigkeiten) benennst du kurz: (1) was als Nächstes "
    "sinnvoll angegangen wird, (2) welche Aufgaben blockiert sind und warum, "
    "(3) womöglich Vergessenes. Knapp, in Stichpunkten, kein Geschwätz."
)
_TODO_ASK_SYSTEM = (
    "Du bist ein Analyse-Assistent für eine To-Do-/Projektdatenbank. Du beantwortest "
    "Fragen AUSSCHLIESSLICH anhand der bereitgestellten Aufgaben-Daten (Projekte, "
    "Aufgaben, Zuständige, Status, Fristen, Notizen, Anhänge, Abhängigkeiten). "
    "Erfinde nichts — steht etwas nicht in den Daten, sag es offen. Antworte auf Deutsch, "
    "klar strukturiert (Überschriften/Stichpunkte, wo sinnvoll). "
    "Bei Fragen zu Personen/Kollegen bleibe sachlich und neutral: leite Aussagen "
    "nachvollziehbar aus den Daten ab (z. B. Arbeitsschwerpunkte, Themen, Zuverlässigkeit "
    "anhand von Status/Fristen), spekuliere nicht über sensible Merkmale und formuliere "
    "keine abwertenden Urteile. Nenne, worauf du dich stützt (Projekt/Aufgabe)."
)


@app.post("/api/todo/extract")
async def todo_extract(req: Request):
    from tools import todo as _todo
    body = await req.json()
    text = str(body.get("text", "")).strip()
    if not text:
        return {"items": [], "edges": [], "tokens": {"in": 0, "out": 0}}
    participants = [str(p).strip() for p in (body.get("participants") or []) if str(p).strip()]
    model = _pick_model(body.get("model"), _model_for("general"))
    # Besprechungsheader als Kontext voranstellen (Thema, Datum, Teilnehmer) — hilft
    # der KI beim Zuordnen von Zuständigen und beim Ableiten von Fristen.
    header = []
    topic = str(body.get("title") or body.get("topic") or "").strip()
    if topic:
        header.append(f"Thema der Besprechung: {topic}")
    date = str(body.get("date") or "").strip()
    if date:
        header.append(f"Datum: {date}")
    if participants:
        header.append(f"Teilnehmer: {', '.join(participants)}")
    prompt = ("\n".join(header) + "\n\nNotiz:\n" + text[:8000]) if header else text[:8000]
    data, ti, to, _ = await _research_llm_json(model, _TODO_EXTRACT_SYSTEM, prompt)
    raw_items = data.get("items") or []
    items, idx_to_id = [], {}
    for k, ri in enumerate(raw_items):
        if not isinstance(ri, dict):
            continue
        n = ri.get("n", k)
        it = _todo.new_item(ri.get("text", ""), assignees=ri.get("assignees"),
                            due=str(ri.get("due", "")).strip())
        if not it["text"]:
            continue
        try:
            idx_to_id[int(n)] = it["id"]
        except (TypeError, ValueError):
            idx_to_id[k] = it["id"]
        items.append((it, ri))
    # Abhängigkeiten → Kanten (blockiert_von: dep blockiert this)
    edges = []
    id_list = [it["id"] for it, _ in items]
    for it, ri in items:
        for dep in (ri.get("blockiert_von") or []):
            try:
                dep_id = idx_to_id.get(int(dep))
            except (TypeError, ValueError):
                dep_id = None
            if dep_id and dep_id != it["id"]:
                edges.append({"source": dep_id, "target": it["id"], "label": "blockiert"})
    return {"items": [it for it, _ in items], "edges": edges, "tokens": {"in": ti, "out": to}}


@app.post("/api/todo/suggest-links")
async def todo_suggest_links(req: Request):
    body = await req.json()
    items = [it for it in (body.get("items") or []) if isinstance(it, dict) and it.get("id")]
    if len(items) < 2:
        return {"edges": [], "tokens": {"in": 0, "out": 0}}
    model = _pick_model(body.get("model"), _model_for("general"))
    id_by_idx = {i: it["id"] for i, it in enumerate(items)}
    valid = set(id_by_idx.values())
    lst = "\n".join(f"{i}: {str(it.get('text','')).strip()[:200]}" for i, it in enumerate(items))
    data, ti, to, _ = await _research_llm_json(model, _TODO_LINKS_SYSTEM, "Aufgaben:\n" + lst)
    edges, seen = [], set()
    for e in (data.get("edges") or []):
        try:
            s = id_by_idx.get(int((e or {}).get("source")))
            t = id_by_idx.get(int((e or {}).get("target")))
        except (TypeError, ValueError):
            continue
        if s and t and s != t and (s, t) not in seen and s in valid and t in valid:
            seen.add((s, t))
            edges.append({"source": s, "target": t, "label": str((e or {}).get("label", "")).strip()[:60]})
    return {"edges": edges, "tokens": {"in": ti, "out": to}}


@app.post("/api/todo/next")
async def todo_next(req: Request):
    body = await req.json()
    pid = body.get("pid") or body.get("name")
    data = (await _db.todo_project_get(pid)) if pid else (body.get("data") or {})
    items = (data or {}).get("items") or []
    if not items:
        return {"text": "Die Liste ist leer.", "tokens": {"in": 0, "out": 0}}
    id2text = {it.get("id"): it.get("text", "") for it in items}
    lines = []
    for it in items:
        deps = "; ".join(f"←{id2text.get(l.get('target'), '')}" for l in (it.get("links") or []))
        lines.append(f"[{it.get('status','offen')}] {it.get('text','')}"
                     + (f" (Zuständig: {', '.join(it.get('assignees') or [])})" if it.get("assignees") else "")
                     + (f" {{{deps}}}" if deps else ""))
    model = _pick_model(body.get("model"), _model_for("general"))
    prompt = f"Liste: {data.get('title','')}\n\nAufgaben:\n" + "\n".join(lines)
    text, ti, to = "", 0, 0
    try:
        async with _model_session(model), httpx.AsyncClient(timeout=120) as client:
            resp = await _llm.chat(client, {
                "model": model, "think": False, "stream": False,
                "messages": [{"role": "system", "content": _TODO_NEXT_SYSTEM},
                             {"role": "user", "content": prompt}],
                "options": {"num_ctx": _profile_num_ctx()}, "keep_alive": KEEP_ALIVE,
            })
            resp.raise_for_status()
        j = resp.json()
        ti, to = _llm_tok(j)
        text = re.sub(r"<think>.*?</think>", "", j.get("message", {}).get("content", ""), flags=re.DOTALL).strip()
    except Exception as e:
        text = f"(Analyse nicht möglich: {e})"
    return {"text": text, "tokens": {"in": ti, "out": to}}


@app.post("/api/todo/ask")
async def todo_ask(req: Request):
    """„Über die Daten fragen": beantwortet eine freie Frage über den gesamten (oder den
    aktiven) To-Do-Datenbestand — inkl. Personen-/Kollegen-Auswertungen. LOKAL-BEVORZUGT
    (`_analysis_model` respektiert Geheim-Modus / vertrauliche Auswertungen). Bei großen
    Datenmengen Map-Reduce."""
    body = await req.json()
    question = str(body.get("question", "")).strip()
    if not question:
        raise HTTPException(status_code=400, detail="Keine Frage angegeben")
    root = str(body.get("root", "") or "").strip()
    if root and root != "root":
        ids = await _db.todo_descendants(root)
    else:
        ids = [p["id"] for p in await _db.todo_projects_all()]
    model = await _analysis_model(body.get("model"))
    if not model:
        raise HTTPException(status_code=503, detail="Kein lokales LLM verfügbar – die Auswertung läuft standardmäßig lokal (Profil-Schalter „API-Modelle für vertrauliche Auswertungen“ erlaubt API-Modelle).")
    data = await _db.todo_graph_data(ids)
    # Anhang-Text pro Punkt (md_text) für tiefere Auswertung nachladen.
    att_by_item: dict = {}
    try:
        for a in (await _db.todo_export()).get("attachments", []):
            txt = (a.get("md_text") or "").strip()
            if txt:
                att_by_item.setdefault(a.get("item_id"), []).append(txt)
    except Exception:
        att_by_item = {}
    # Pro Projekt einen Textblock: Aufgaben mit Status/Zuständigen/Frist/Notiz/Anhang + Kanten.
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
        return {"answer": "Es sind keine Aufgaben im gewählten Bereich vorhanden.",
                "tokens": {"in": 0, "out": 0}, "scope": root or "root"}
    scope_hint = (f"{n_items} Aufgaben, {len(persons)} Personen"
                  + (f", Zuständige: {', '.join(sorted(persons))}" if persons else ""))
    num_ctx = _profile_num_ctx()
    budget = max(2000, int(num_ctx * 3.2))
    # Auf lokalen Modellen ist jede Generation langsam → Arbeitsmenge deckeln: höchstens
    # MAX_GROUPS Map-Läufe. Passt der Bestand nicht in MAX_GROUPS*budget Zeichen, werden
    # die überzähligen Bereiche weggelassen (mit Hinweis) statt dutzende langsame Aufrufe.
    MAX_GROUPS = 6
    truncated = False
    joined_all = "\n\n".join(blocks)
    cap = MAX_GROUPS * budget
    if len(joined_all) > cap:
        kept, acc = [], 0
        for blk in blocks:
            if acc + len(blk) > cap:
                break
            kept.append(blk); acc += len(blk)
        blocks = kept or [joined_all[:budget]]
        truncated = True
    tin = tout = 0
    try:
        async with _model_session(model), httpx.AsyncClient(timeout=300) as client:
            async def _run(system: str, user: str):
                r = await _llm.chat(client, {
                    "model": model, "think": False, "stream": False,
                    "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
                    "options": {"num_ctx": num_ctx}, "keep_alive": KEEP_ALIVE,
                })
                r.raise_for_status()
                jj = r.json()
                a, b = _llm_tok(jj)
                c = re.sub(r"<think>.*?</think>", "", jj.get("message", {}).get("content", ""), flags=re.DOTALL).strip()
                return c, a, b
            joined = "\n\n".join(blocks)
            if len(joined) <= budget:
                usr = (f"To-Do-Daten (Überblick: {scope_hint}):\n\n{joined}\n\nFrage: {question}")
                answer, a, b = await _run(_TODO_ASK_SYSTEM, usr)
                tin += a; tout += b
            else:
                # Map: Blöcke bis Budget bündeln, je Gruppe eine Teilantwort zur Frage.
                groups, cur, cur_len = [], [], 0
                for blk in blocks:
                    if cur and cur_len + len(blk) > budget:
                        groups.append("\n\n".join(cur)); cur, cur_len = [], 0
                    cur.append(blk); cur_len += len(blk)
                if cur:
                    groups.append("\n\n".join(cur))
                partials = []
                map_sys = (_TODO_ASK_SYSTEM + " Dies ist NUR EIN TEIL der Daten – sammle die für "
                           "die Frage relevanten Fakten aus diesem Teil (noch keine Endantwort).")
                for g in groups:
                    txt, a, b = await _run(map_sys, f"To-Do-Daten (Teil):\n\n{g}\n\nFrage: {question}")
                    tin += a; tout += b
                    if txt:
                        partials.append(txt)
                # Reduce: Teil-Befunde zur Endantwort zusammenführen.
                reduce_usr = (f"Frage: {question}\n\nTeil-Befunde aus dem gesamten To-Do-Bestand "
                              f"({scope_hint}):\n\n" + "\n\n---\n\n".join(partials)
                              + "\n\nFasse dies zu EINER fundierten Endantwort auf die Frage zusammen.")
                answer, a, b = await _run(_TODO_ASK_SYSTEM, reduce_usr)
                tin += a; tout += b
    except HTTPException:
        raise
    except (httpx.ConnectError, httpx.ConnectTimeout) as e:
        raise HTTPException(status_code=503, detail=f"Lokales LLM nicht erreichbar (läuft Ollama?): {e}") from e
    except httpx.HTTPStatusError as e:
        body = ""
        try: body = e.response.text[:300]
        except Exception: pass
        raise HTTPException(status_code=502, detail=f"Das Modell „{model}“ hat die Auswertung abgelehnt (evtl. num_ctx zu groß / zu wenig VRAM / Modell nicht geladen). {body}") from e
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Auswertung fehlgeschlagen: {type(e).__name__}: {e}") from e
    if truncated:
        answer += ("\n\n---\n*Hinweis: Der Bestand ist sehr groß – es wurde nur ein Teil "
                   "ausgewertet. Für vollständige Antworten ein einzelnes Projekt aktivieren (⚡) "
                   "oder gezielter fragen.*")
    return {"answer": answer, "tokens": {"in": tin, "out": tout}, "scope": root or "root"}


# ── To-Do: Anlagen (Dokument -> Markdown in DB), Verschieben, Suche, Graph ────

def _todo_safe_file(fn: str) -> str:
    base = Path(str(fn or "").replace("\\", "/")).name.strip()
    base = "".join(c for c in base if c.isalnum() or c in (" ", ".", "_", "-")).strip()
    return base or "datei"


@app.post("/api/todo/items/{item_id}/attach")
async def todo_item_attach(item_id: str, file: UploadFile = File(...)):
    """Datei an einen Punkt haengen: Original auf Platte, Text als Markdown in die DB
    (fuer Anzeige + Suche)."""
    from tools import files as _files
    d = TODO_ATT_DIR / _todo_safe_file(item_id)
    d.mkdir(parents=True, exist_ok=True)
    orig_path = d / _todo_safe_file(file.filename)
    orig_path.write_bytes(await file.read())
    try:
        text = _files.extract(orig_path)
    except Exception as e:
        text = f"[Konnte Datei nicht lesen: {e}]"
    md_text = f"# {file.filename}\n\n{text}"
    att_id = "ta_" + uuid.uuid4().hex[:12]
    try:
        await _db.todo_attach_add(att_id, item_id, file.filename, str(orig_path), md_text)
    except Exception:
        raise HTTPException(status_code=400, detail="Punkt nicht gefunden - bitte erst speichern.")
    return {"ok": True, "attachment": {"id": att_id, "name": file.filename}}


@app.get("/api/todo/attachment/{att_id}")
async def todo_attachment(att_id: str, orig: bool = False):
    att = await _db.todo_attach_get(att_id)
    if not att:
        raise HTTPException(status_code=404, detail="Anlage nicht gefunden")
    if orig and att.get("orig_path") and Path(att["orig_path"]).exists():
        return FileResponse(att["orig_path"], filename=att.get("name") or "anlage")
    from fastapi.responses import PlainTextResponse
    return PlainTextResponse(att.get("md_text", ""), media_type="text/markdown; charset=utf-8")


@app.delete("/api/todo/attachment/{att_id}")
async def todo_attachment_delete(att_id: str):
    att = await _db.todo_attach_delete(att_id)
    if att and att.get("orig_path"):
        try:
            Path(att["orig_path"]).unlink(missing_ok=True)
        except Exception:
            pass
    return {"ok": True}


@app.post("/api/todo/items/{item_id}/move")
async def todo_item_move(item_id: str, req: Request):
    body = await req.json()
    target = str(body.get("project_id", "")).strip()
    if not target:
        raise HTTPException(status_code=400, detail="Kein Zielprojekt")
    await _db.todo_item_move(item_id, target)
    return {"ok": True}


@app.post("/api/todo/items/{item_id}/reorder")
async def todo_item_reorder(item_id: str, req: Request):
    body = await req.json()
    direction = "up" if str(body.get("direction", "up")) == "up" else "down"
    await _db.todo_item_reorder(item_id, direction)
    return {"ok": True}


@app.get("/api/todo/search")
async def todo_search(q: str = Query(...), root: str = Query("")):
    """Projektuebergreifende Suche (Punkte + Anlagen-Markdown). Mit ?root=<id> auf den
    Teilbaum dieses Projekts beschraenkt; ohne (bzw. root) ueber ALLE Projekte."""
    scope = None
    if root and root != "root":
        scope = await _db.todo_descendants(root)
    results = await _db.todo_search(q, scope)
    return {"results": results, "query": q}


@app.get("/api/todo/graph")
async def todo_graph(root: str = Query("")):
    """Graph-Daten (Punkte+Kanten je Projekt) des Teilbaums <root> bzw. aller Projekte."""
    if root and root != "root":
        ids = await _db.todo_descendants(root)
    else:
        ids = [p["id"] for p in await _db.todo_projects_all()]
    return await _db.todo_graph_data(ids)


def _todo_parse_due(s: str):
    """Fälligkeit tolerant parsen: ISO (YYYY-MM-DD) oder DD.MM.(YY)YY → date, sonst None."""
    import datetime as _d
    s = (s or "").strip()
    if not s:
        return None
    for fmt in ("%Y-%m-%d", "%d.%m.%Y", "%d.%m.%y", "%Y/%m/%d"):
        try:
            return _d.datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


@app.get("/api/todo/agenda")
async def todo_agenda(root: str = Query(""), person: str = Query("")):
    """Deterministische Empfehlung „was als Nächstes?" für den aktiven Bereich.
    Priorisiert nach Fälligkeit (Zeit), Abhängigkeiten (blockiert/entblockt) und
    Status. Optional auf eine Person gefiltert. Kein LLM — nachvollziehbar."""
    import datetime as _d
    if root and root != "root":
        ids = await _db.todo_descendants(root)
    else:
        ids = [p["id"] for p in await _db.todo_projects_all()]
    data = await _db.todo_graph_data(ids)
    items, preds, succs, persons = {}, {}, {}, set()
    for pr in data.get("projects", []):
        for it in pr["items"]:
            items[it["id"]] = {"id": it["id"], "text": it["text"], "status": it["status"],
                               "assignees": it.get("assignees") or [], "due": it.get("due", ""),
                               "project": pr["name"], "project_title": pr["title"]}
            for a in (it.get("assignees") or []):
                persons.add(a)
        for e in pr["edges"]:
            preds.setdefault(e["target"], set()).add(e["source"])   # source = Vorgänger von target
            succs.setdefault(e["source"], set()).add(e["target"])
    today = _d.date.today()

    def _urgency(due):
        dd = _todo_parse_due(due)
        if not dd:
            return (0.0, None)
        delta = (dd - today).days
        if delta < 0:
            return (3.0, delta)
        if delta <= 3:
            return (2.0, delta)
        if delta <= 10:
            return (1.0, delta)
        return (0.5, delta)

    pfilter = (person or "").strip().lower()
    ready, blocked = [], []
    for iid, it in items.items():
        if it["status"] == "erledigt":
            continue
        if pfilter and not any(pfilter in a.lower() for a in it["assignees"]):
            continue
        blockers = [items[s]["text"] for s in preds.get(iid, set())
                    if s in items and items[s]["status"] != "erledigt"]
        unblocks = len([t for t in succs.get(iid, set())
                        if t in items and items[t]["status"] != "erledigt"])
        u, days = _urgency(it["due"])
        score = u * 10 + unblocks * 3 + (2 if it["status"] == "laeuft" else 0)
        row = {**it, "unblocks": unblocks, "urgency": u, "days": days,
               "blockers": blockers, "score": round(score, 2)}
        (blocked if blockers else ready).append(row)
    ready.sort(key=lambda r: (-r["score"], r["days"] if r["days"] is not None else 9999))
    blocked.sort(key=lambda r: -r["score"])
    jetzt = [r for r in ready if r["urgency"] >= 2 or r["unblocks"] >= 1 or r["status"] == "laeuft"]
    demn = [r for r in ready if r not in jetzt]
    return {"persons": sorted(persons), "jetzt": jetzt, "demnaechst": demn,
            "blocked": blocked, "scope_root": root or "root"}


@app.get("/api/todo/export")
async def todo_export():
    """Kompletten To-Do-Bestand (Projekte, Punkte, Kanten, Anlagen) als JSON —
    zum Sichern/Weitergeben. Frontend lädt es als Datei herunter."""
    return await _db.todo_export()


@app.post("/api/todo/import")
async def todo_import(req: Request):
    """To-Do-Bestand aus einer zuvor exportierten JSON-Datei einspielen. Vorhandene
    Projekte mit gleicher ID werden ersetzt (Punkte/Kanten sauber, kein Duplikat).
    Die Wurzel wird nicht überschrieben."""
    dump = await req.json()
    if not isinstance(dump, dict) or "projects" not in dump:
        raise HTTPException(status_code=400, detail="Ungültige Datei – erwartet wird ein To-Do-Export (Felder projects/items/edges/attachments).")
    await _db.todo_root_ensure(_todo_root_name())
    # Wurzel-Projektzeile aus dem Import entfernen (Name/Struktur der eigenen Wurzel bleibt
    # erhalten); Punkte, die direkt an der Wurzel hängen, werden weiterhin übernommen.
    dump = {
        "projects": [p for p in dump.get("projects", []) if p.get("id") != "root"],
        "items": dump.get("items", []),
        "edges": dump.get("edges", []),
        "attachments": dump.get("attachments", []),
    }
    # Vorhandene Projekte gleicher ID zuerst kaskadierend löschen → sauberer Re-Import
    # (Kanten haben keine stabile ID, sonst würden sie sich verdoppeln).
    for p in dump["projects"]:
        pid = p.get("id", "")
        if pid and pid != "root":
            await _db.todo_project_delete(pid)
    await _db.todo_import(dump)
    return {"projects": len(dump["projects"]), "items": len(dump["items"]),
            "edges": len(dump["edges"]), "attachments": len(dump["attachments"])}


@app.post("/api/todo/reset")
async def todo_reset():
    """Kompletten To-Do-Bestand leeren — mit AUTOMATISCHER Sicherung der alten Liste
    (Zeitstempel-JSON unter data/todo_backups/), damit nichts unwiederbringlich verloren
    geht. Danach ist nur noch die (leere) Wurzel übrig."""
    from datetime import datetime
    dump = await _db.todo_export()
    backup_dir = DATA_DIR / "todo_backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    fname = "todo_backup_" + datetime.now().strftime("%Y%m%d_%H%M%S") + ".json"
    (backup_dir / fname).write_text(json.dumps(dump, ensure_ascii=False, indent=2), encoding="utf-8")
    await _db.todo_wipe()
    await _db.todo_root_ensure(_todo_root_name())
    return {"backup": fname,
            "removed": {"projects": len(dump.get("projects", [])), "items": len(dump.get("items", []))}}


# ── Static Files (muss zuletzt kommen) ───────────────────────────────────────

app.mount("/", StaticFiles(directory="static", html=True), name="static")
