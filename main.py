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
from pathlib import Path
from typing import List, Optional

import aiofiles
import httpx
from fastapi import FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response, StreamingResponse

import db as _db
from tools import llm as _llm
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


# Platzhalter-Werte aus den Frontend-Selektoren (kein echtes Modell)
_MODEL_PLACEHOLDERS = {
    "Lade…", "Lade...", "Ollama nicht erreichbar", "Fehler beim Laden",
}


def _pick_model(m, fallback: Optional[str] = None) -> str:
    """Wählt ein gültiges Modell: das angeforderte (jedes installierte Ollama-Modell
    ist erlaubt), sonst den Fallback bzw. das Standardmodell. Verhindert 500er durch
    Platzhalternamen (z.B. 'Lade…') aus dem Frontend-Selektor."""
    m = (m or "").strip()
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
BILDER_DIR = Path(__file__).parent / "bilder"
PROFILE_FILE = DATA_DIR / "user_profile.json"
PROFILE_ASSETS_DIR = DATA_DIR / "profile_assets"
PROJECTS_FILE = DATA_DIR / "projects.json"
# Externe OpenAI-kompatible KI-Anbieter (enthält API-Keys → gitignored, NICHT im Backup)
API_PROVIDERS_FILE = DATA_DIR / "api_providers.json"
LOG_FILE = DATA_DIR / "ai_framework_thomas.log"

for _d in [UPLOADS_DIR, CONVERSATIONS_DIR, AGENTS_DIR, REPORTS_DIR, PLANS_DIR, DOSSIERS_DIR, CODE_DIR, JURIES_DIR, JURY_DOCS_DIR, PROFILE_ASSETS_DIR]:
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
VALID_TONES = {"roboter", "professor", "doktor", "felix", "sandra"}
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
_OPTIONAL_TABS = {"rag", "ide", "mail", "logs", "medizin", "mathe", "diranalyse", "morph", "jury"}
# Auf erstem Start verborgene optionale Tabs. Der Installer kann die Vorbelegung über
# config.json ("hidden_tabs_default") setzen (P8); ungültige/unbekannte Tabs werden
# herausgefiltert, Fallback ist „alle optionalen Tabs verbergen".
_cfg_hidden = _CONFIG.get("hidden_tabs_default")
if isinstance(_cfg_hidden, list):
    _DEFAULT_HIDDEN_TABS = [t for t in _cfg_hidden if t in _OPTIONAL_TABS]
else:
    _DEFAULT_HIDDEN_TABS = ["rag", "ide", "mail", "logs", "medizin", "mathe", "diranalyse", "morph", "jury"]


def _model_for(role: str) -> str:
    """Das im Profil der Rolle zugewiesene Modell, sonst das Standardmodell."""
    key = _MODEL_ROLES.get(role)
    val = str(_load_profile().get(key, "") or "").strip() if key else ""
    return val or DEFAULT_MODEL


# Immer aktive Grundregel gegen Halluzinationen (unabhängig vom Modus)
_BASE_GUARD = (
    "Erfinde niemals Fakten, Zahlen, Normen (z. B. DIN/VDI), Quellen oder technische "
    "Daten. Stütze dich ausschließlich auf gesichertes Wissen und auf die Ergebnisse "
    "aufgerufener Tools. Fehlt dir eine Information, sage das offen – rate nicht."
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

app = FastAPI(title="AI_Framework_Thomas")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
async def _startup():
    await _db.init()
    await _db.migrate_json(CONVERSATIONS_DIR)

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
                "Erstellt ein 2D-Diagramm (Linien-, Balken- oder Streudiagramm) und zeigt es direkt an. "
                "Ideal für Kraft-Weg-Kurven, Spannungs-Dehnungs-Diagramme, Kennlinien etc."
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


class ResearchRequest(BaseModel):
    topic: str
    aspects: List[str]
    model: str = ""   # leer → Wissenschafts-Modell aus dem Profil


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
    _r_model = _pick_model(request.model, _model_for("science"))

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

    synthesis_parts = [f"Thema: {request.topic}\n"]
    for aspect, result in aspect_data:
        synthesis_parts.append(f"### Suchergebnisse – {aspect}\n{result[:2500]}\n")

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
            })
            resp.raise_for_status()
            llm_result = resp.json()
    except Exception as e:
        yield _sse({"type": "error", "message": str(e)})
        return

    content = llm_result.get("message", {}).get("content", "")
    content = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL).strip()

    words = content.split(" ")
    for i, word in enumerate(words):
        yield _sse({"type": "text", "content": word + (" " if i < len(words) - 1 else "")})
        await asyncio.sleep(0.004)

    yield _sse({"type": "done"})


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


@app.get("/api/rag/tiers")
async def rag_tiers():
    from tools.rag import TIERS, DEFAULT_TIER
    return {"tiers": TIERS, "default": DEFAULT_TIER, "embed_model": EMBED_MODEL}


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
    coll = {
        "id": f"rag_{uuid.uuid4().hex[:12]}",
        "name": name,
        "embed_model": EMBED_MODEL,
        "tier": (body.tier or "regler").strip()[:24],   # freies Anzeige-Label (Regler-Stufe)
        "chunk_size": int(body.chunk_size or tc["chunk_size"]),
        "chunk_overlap": int(body.chunk_overlap if body.chunk_overlap is not None else tc["chunk_overlap"]),
        "top_k": int(body.top_k or tc["top_k"]),
        "embed_gpu": False,   # auf kleinen Karten immer CPU (verdrängt das Chat-Modell nicht)
        "clean": bool(body.clean),
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


async def _optimize_chunk_for_rag(chunk: str, model: str) -> str:
    """Ruft das LLM auf, um einen Textabschnitt RAG-konform aufzubereiten."""
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
            content = resp.json().get("message", {}).get("content", "").strip()
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
        async with _model_session(model):
            for idx, chunk in enumerate(chunks):
                pct = 5 + int((idx / total) * 85)
                yield _sse({"type": "progress",
                             "step": f"Abschnitt {idx + 1}/{total} wird optimiert…",
                             "pct": pct})
                opt = await _optimize_chunk_for_rag(chunk, model)
                optimized_parts.append(opt)

        optimized_text = "\n\n".join(optimized_parts)
        yield _sse({"type": "progress", "step": "Einbetten und speichern…", "pct": 92})

        try:
            n = await ingest_file(coll, optimized_text, file.filename, f"doc_{uuid.uuid4().hex[:12]}")
        except Exception as e:
            yield _sse({"type": "error", "message": str(e)})
            return

        yield _sse({"type": "done", "filename": file.filename, "n_chunks": n})

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


async def _derive_adaptive_prompt(user_text: str, model: str):
    """Leitet aus der Nutzerfrage einen fragespezifischen Experten-System-Prompt ab.
    Rückgabe: (rolle, system_prompt) – bei Fehler ("", "")."""
    if not (user_text or "").strip():
        return "", ""
    try:
        async with _model_session(model), httpx.AsyncClient(timeout=120) as client:
            resp = await _llm.chat(client,{
                "model": model,
                "think": False,
                "stream": False,
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
            raw = resp.json().get("message", {}).get("content", "")
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

    # Adaptiver Agent: erst die Frage analysieren, dann einen fragespezifischen
    # Experten-System-Prompt ableiten, der anschließend die Antwort erzeugt.
    if request.agent_id == "__adaptive__":
        role, derived = await _derive_adaptive_prompt(_last_user, model)
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
    if not (request.web_search or request.science):
        active_tools = [t for t in active_tools
                        if t["function"]["name"] != "web_search"]

    # plot_function dem Modell NICHT als Ollama-Tool anbieten: kleine Modelle (z. B.
    # ministral-3:3b) erzeugen dabei häufig ungültige LaTeX-Escapes (\( … \)) in den
    # Argumenten, an denen Ollama beim Parsen mit HTTP 500 scheitert. Funktionsgraphen
    # werden stattdessen deterministisch serverseitig erzeugt (_extract_plot_request →
    # plot_function als Fallback nach der Antwort). plot_chart bleibt verfügbar.
    active_tools = [t for t in active_tools
                    if t["function"]["name"] != "plot_function"]

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

    # Nachrichten aufbauen – Modus-Brille (falls aktiv) dem System-Prompt voranstellen
    messages: list = []
    _sci = _SCIENCE_PROMPT if request.science else ""
    _sys = "\n\n".join(p for p in (_sci, _augment_prefix(_last_user), system_prompt) if p)
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
    # Denkprozess anfordern? Wird abgeschaltet, falls das Modell 'think' nicht unterstützt.
    _think_on = bool(request.show_thinking)
    # Agentic Loop
    for _iter in range(8):
        payload = {
            "model": model,
            "messages": messages,
            "stream": False,
            "options": {"temperature": _temp},
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

            # Leere Antwort des Modells: nicht stumm bleiben, sondern erklären.
            # (Häufig bei kleinen Modellen, die nichts oder nur einen Tool-Call ohne
            #  Text liefern – sonst sähe der Nutzer „keine Antwort" ohne Hinweis.)
            if not (content or "").strip():
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
                conv = await _text_to_presentation(content, model)
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
            })
            yield _sse({"type": "done"})
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

            messages.append({"role": "tool", "content": tool_result})

    yield _sse({"type": "error", "message": "Maximale Iterationen erreicht"})


def _sse(data: dict) -> str:
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


# ── Tool-Ausführung ───────────────────────────────────────────────────────────


async def _execute_tool(name: str, args: dict) -> str:
    if name == "web_search":
        from tools.search import search
        return await search(args.get("query", ""), int(args.get("num_results", 6)))

    if name == "calculate":
        return _safe_exec(args.get("code", ""))

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


async def _text_to_presentation(text: str, model: str) -> Optional[dict]:
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
            raw = resp.json().get("message", {}).get("content", "")
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
    return {"ok": True, "summary": summary, "messages": compressed}


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
            out = resp.json().get("message", {}).get("content", "").strip()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Agent-Fehler: {e}")
    out = re.sub(r"<think>.*?</think>", "", out, flags=re.DOTALL).strip()
    return {"ok": True, "text": out, "model": model,
            "subject": m.get("subject", ""), "from": m.get("from", "")}


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
    data = await _text_to_presentation(text, model)
    if not data:
        raise HTTPException(status_code=422,
                            detail="Konnte aus dem Text keine Folien ableiten")
    return data


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
async def list_agents():
    agents = []
    for f in AGENTS_DIR.glob("*.json"):
        try:
            agents.append(json.loads(f.read_text(encoding="utf-8")))
        except Exception:
            pass
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
        generated = result.get("message", {}).get("content", "").strip()

    generated = re.sub(r"<think>.*?</think>", "", generated, flags=re.DOTALL).strip()
    return {"prompt": generated}


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
        raw = resp.json().get("message", {}).get("content", "")

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
    return {"persona_name": persona_name, "system_prompt": system_prompt}


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
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_text, "images": [small]},
            ],
            "stream": False,
        })
        resp.raise_for_status()
        raw = resp.json().get("message", {}).get("content", "")

    raw = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL).strip()

    def _strip_md(s: str) -> str:
        # Markdown-Reste entfernen, die das Canvas sonst literal zeichnet
        s = re.sub(r"[*_`#>]+", "", s)
        s = re.sub(r"^\s*[-•]\s*", "", s)
        return re.sub(r"\s+", " ", s).strip()

    title, bullets, caption = (label if descriptive else "Abbildung"), [], ""
    m = re.search(r"\{[\s\S]*\}", raw)
    if m:
        try:
            data = json.loads(m.group(0))
            title = _strip_md(data.get("title") or title) or title
            b = data.get("bullets") or []
            bullets = [_strip_md(str(x)) for x in b if str(x).strip()][:3]
            caption = _strip_md(data.get("caption") or "")
        except Exception:
            pass
    if not bullets and not caption:
        # Fallback: roher Text als Bildunterschrift
        caption = _strip_md(raw)[:200]

    return {
        "title": title,
        "bullets": bullets,
        "caption": caption,
        "descriptive_filename": descriptive,
    }


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
):
    """Erzeugt aus einem hochgeladenen Gesetzestext / einer Norm einen spezialisierten
    Gesetzes-/Regel-Agenten. Der Text wird beim Hochladen nach Markdown konvertiert;
    bei kurzem Text direkt in den system_prompt eingebettet, bei langem Text in eine
    eigene Wissensdatenbank ('Gesetz: …') ausgelagert und fest an den Agenten gebunden
    (rag_collections) — die Entscheidung fällt automatisch nach Länge."""
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

    name = (title or "").strip() or Path(file.filename or "Gesetz").stem
    md = _legal_to_md(raw, name)
    tools_list = ["web_search"] if web_search else []

    if len(md) <= _LEGAL_PROMPT_LIMIT:
        mode, rag_ids = "prompt", []
        system_prompt = (
            f"Du bist ein juristischer Fachassistent für „{name}“. Beantworte Fragen "
            f"AUSSCHLIESSLICH auf Basis des folgenden Regel-/Gesetzestextes und nenne immer "
            f"die einschlägige Fundstelle (§ bzw. Artikel). Steht die Antwort nicht im Text, "
            f"sage das klar und rate nicht. Antworte präzise und auf Deutsch.\n\n"
            f"--- {name} ---\n\n{md}"
        )
    else:
        mode = "rag"
        coll = {
            "id": f"rag_{uuid.uuid4().hex[:12]}",
            "name": f"Gesetz: {name}",
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
            f"Du bist ein juristischer Fachassistent für „{name}“. Dir ist der vollständige "
            f"Regel-/Gesetzestext als Wissensdatenbank hinterlegt. Beantworte Fragen "
            f"AUSSCHLIESSLICH anhand der eingeblendeten Auszüge und nenne immer die "
            f"einschlägige Fundstelle (§ bzw. Artikel). Steht die Antwort nicht in den "
            f"Auszügen, sage das klar und rate nicht. Antworte präzise und auf Deutsch."
        )

    agent = AgentDef(
        id=_to_slug(name) + "_" + uuid.uuid4().hex[:4],
        name=name,
        description=f"Gesetzes-/Regel-Agent zu „{name}“ (automatisch aus hochgeladenem Text erstellt).",
        system_prompt=system_prompt,
        tools=tools_list,
        icon="⚖️",
        category="Recht",
        favorite=True,
        rag_collections=rag_ids,
    )
    fp = _unique_agent_path(agent.name or agent.id, exclude_id=agent.id)
    fp.write_text(json.dumps(agent.model_dump(), ensure_ascii=False, indent=2), encoding="utf-8")
    return {
        "agent_id": agent.id, "name": name, "mode": mode, "chars": len(md),
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
            member_sys = sys_prompt + "\n\n" + _JURY_MEMBER_SYSTEM if sys_prompt else _JURY_MEMBER_SYSTEM
            user_parts = []
            if context:
                user_parts.append(f"Kontext:\n{context}")
            if criteria:
                user_parts.append(f"Bewertungskriterien:\n{criteria}")
            if rag_ctx:
                user_parts.append(f"Eingeblendete Fachgrundlagen:\n{rag_ctx[:6000]}")
            user_parts.append(f"Zu bewertender Text:\n{text[:8000]}")
            user_content = "\n\n".join(user_parts)

            mdl = _pick_model(agent.get("model"), _model_for("science"))
            data = None
            try:
                async with _model_session(mdl), httpx.AsyncClient(timeout=180) as client:
                    resp = await _llm.chat(client, {
                        "model": mdl, "think": False, "stream": False,
                        "format": "json",
                        "messages": [
                            {"role": "system", "content": member_sys},
                            {"role": "user", "content": user_content},
                        ],
                    })
                    resp.raise_for_status()
                    data = _parse_llm_json(resp.json().get("message", {}).get("content", ""))
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
                    "messages": [
                        {"role": "system", "content": _JURY_SYNTH_SYSTEM},
                        {"role": "user", "content": f"Einzelvoten der Jury:\n\n{votes_txt}"},
                    ],
                })
                resp.raise_for_status()
                synth = _parse_llm_json(resp.json().get("message", {}).get("content", "")) or {}
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
        yield _sse({"type": "done"})

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


async def _llm_ner_names(text: str, model: str) -> List[str]:
    """Optionaler LLM-NER-Pass: liefert eine Liste zu schwärzender Personennamen.
    Best effort — bei jedem Fehler leere Liste."""
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
            raw = resp.json().get("message", {}).get("content", "")
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


async def _anonymize(text: str, mapping: dict, model: str, use_llm: bool):
    """Anonymisiert Text deterministisch (regex) und optional per LLM-NER."""
    from tools.anonymize import redact_pii, redact_names
    clean, mapping = redact_pii(text, mapping)
    if use_llm:
        names = await _llm_ner_names(text, model)
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
    model = _pick_model(body.get("model"), _model_for("general"))

    files = _dir_walk(base)
    text_files = [f for f in files
                  if not f["is_dir"] and f["ext"] in _DIR_TEXT_EXT
                  and 0 < f["size"] <= _DIR_FILE_MAX_BYTES]
    text_files.sort(key=lambda f: f["size"])

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
            snip, mapping = await _anonymize(snip, mapping, model, use_llm_ner)
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
            raw = resp.json().get("message", {}).get("content", "")
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
    model = _pick_model(body.get("model"), _model_for("general"))

    try:
        if target.stat().st_size > 25_000_000:
            raise HTTPException(status_code=400, detail="Datei zu groß für die Detailanalyse (> 25 MB)")
    except OSError:
        pass
    txt = _extract_text(target)
    if not txt or txt.startswith("[Lesefehler"):
        raise HTTPException(status_code=400, detail=f"Text nicht lesbar: {txt}")
    # Anonymisierung von Personendaten ist PFLICHT (nicht abschaltbar)
    mapping: dict = {}
    txt, mapping = await _anonymize(txt[:16000], mapping, model, use_llm_ner)

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
            analysis = resp.json().get("message", {}).get("content", "").strip()
            analysis = re.sub(r"<think>.*?</think>", "", analysis, flags=re.DOTALL).strip()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Analyse fehlgeschlagen: {e}")

    return {"file": file_rel, "analysis": analysis, "redacted": len(mapping)}


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


async def _morph_llm(model: str, system: str, user: str) -> Optional[dict]:
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
            return _parse_llm_json(resp.json().get("message", {}).get("content", ""))
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
    data = await _morph_llm(
        model,
        ("Du erstellst einen morphologischen Kasten (Zwicky-Box) für eine "
         "Aufgabenstellung. Bestimme 4–7 unabhängige Parameter (Merkmale, die eine "
         "Lösung beschreiben) und je Parameter 3–5 konkrete Ausprägungen. Jede "
         "Ausprägung ist ein KURZER Text (Stichwort, max. ~6 Wörter) — KEIN Objekt, "
         "keine verschachtelten Felder. Antworte NUR mit JSON: "
         "{\"parameters\":[{\"name\":\"Parameter\",\"values\":"
         "[\"Ausprägung 1\",\"Ausprägung 2\"]}]}"),
        f"Aufgabenstellung:\n{problem}{_ctx}")
    params = []
    if data:
        for p in (data.get("parameters") or []):
            if isinstance(p, dict) and p.get("name"):
                vals = [s for s in (_morph_value_str(v) for v in (p.get("values") or [])) if s]
                if vals:
                    params.append({"name": str(p["name"]).strip(), "values": vals})
    if not params:
        raise HTTPException(status_code=502, detail="KI lieferte keine verwertbaren Parameter")
    return {"parameters": params}


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
    data = await _morph_llm(
        model,
        ("Du bewertest eine Lösungskombination aus einem morphologischen Kasten. "
         "Gib eine Gesamtbewertung (score 0–100), Einschätzungen zu Machbarkeit und "
         "Innovationsgrad (jeweils 0–100), eine kurze Begründung und Risiken. "
         "Schlage außerdem bis zu drei interessante alternative Kombinationen vor. "
         "Antworte NUR mit JSON: {\"score\":0,\"machbarkeit\":0,\"innovation\":0,"
         "\"begruendung\":\"…\",\"risiken\":[\"…\"],\"vorschlaege\":[{\"picks\":"
         "[{\"parameter\":\"…\",\"value\":\"…\"}],\"score\":0,\"begruendung\":\"…\"}]}"),
        f"Aufgabenstellung:\n{problem}\n\nGewählte Kombination:\n{sel_txt}{params_txt}")
    if not data:
        raise HTTPException(status_code=502, detail="KI-Bewertung fehlgeschlagen")
    return {
        "score": data.get("score"),
        "machbarkeit": data.get("machbarkeit"),
        "innovation": data.get("innovation"),
        "begruendung": (data.get("begruendung") or "").strip(),
        "risiken": [str(r) for r in (data.get("risiken") or [])],
        "vorschlaege": data.get("vorschlaege") or [],
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
    data = await _morph_llm(
        model, system,
        f"Aufgabenstellung:\n{problem}\n\nParameter: {parameter}\nAusprägung: {value}{_ctx}")
    if not data:
        raise HTTPException(status_code=502, detail="KI-Verfeinerung fehlgeschlagen")
    return {"text": (data.get("text") or "").strip(),
            "alternativen": [str(a) for a in (data.get("alternativen") or [])]}


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
    data = await _morph_llm(
        model,
        (f"Du erzeugst {n} KREATIVE, deutlich unterschiedliche Lösungsideen für eine "
         "Aufgabenstellung auf Basis eines morphologischen Kastens. Jede Idee wählt je "
         "Parameter genau EINE Ausprägung (nutze die vorgegebenen, wenn vorhanden, sonst "
         "passende eigene) und bekommt einen kurzen, prägnanten Konzepttitel/-satz. Wage "
         "auch ungewöhnliche, originelle Kombinationen. Antworte NUR mit JSON: "
         "{\"ideen\":[{\"concept\":\"kurzer Konzepttext\",\"picks\":"
         "[{\"parameter\":\"…\",\"value\":\"…\"}]}]}"),
        f"Aufgabenstellung:\n{problem}{params_txt}{_ctx}")
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
    return {"ideen": ideen}


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
    return _load_projects()


@app.post("/api/projects")
async def create_project(req: Request):
    body = await req.json()
    projects = _load_projects()
    project = {
        "id": uuid.uuid4().hex[:8],
        "name": str(body.get("name", "Neues Projekt")).strip(),
        "number": str(body.get("number", "")).strip(),
        "description": str(body.get("description", "")).strip(),
        "created_at": time.time(),
    }
    projects.append(project)
    PROJECTS_FILE.write_text(json.dumps(projects, ensure_ascii=False, indent=2), encoding="utf-8")
    return project


@app.put("/api/projects/{pid}")
async def update_project(pid: str, req: Request):
    body = await req.json()
    projects = _load_projects()
    for p in projects:
        if p["id"] == pid:
            p["name"] = str(body.get("name", p["name"])).strip()
            p["number"] = str(body.get("number", p.get("number", ""))).strip()
            p["description"] = str(body.get("description", p.get("description", ""))).strip()
            break
    PROJECTS_FILE.write_text(json.dumps(projects, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"ok": True}


@app.delete("/api/projects/{pid}")
async def delete_project(pid: str):
    projects = [p for p in _load_projects() if p["id"] != pid]
    PROJECTS_FILE.write_text(json.dumps(projects, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"ok": True}


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

    if use_rag:
        cid = plan.get("rag_collection_id")
        if cid:
            coll = await _db.rag_get_collection(cid)
            if coll:
                from tools.rag import query_collections
                try:
                    hits = await query_collections([coll], user_message)
                    if hits:
                        rag_text = "\n\n".join(h.get("text", "") for h in hits[:6])
                        context_parts.append(f"Aus Plan-Wissensdatenbank:\n{rag_text[:3000]}")
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
            }):
                try:
                    token = chunk.get("message", {}).get("content", "")
                    if token:
                        yield f"data: {json.dumps({'type': 'text', 'content': token})}\n\n"
                    if chunk.get("done"):
                        yield f"data: {json.dumps({'type': 'done'})}\n\n"
                except Exception:
                    pass

    return StreamingResponse(_stream(), media_type="text/event-stream")


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
        yield f"data: {json.dumps({'type': 'done'})}\n\n"

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
    role, persona = await _derive_adaptive_prompt(
        f"Projektaufgabe: {tname}. Projektkontext: {context}", model)

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
            md = resp.json().get("message", {}).get("content", "")
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
            "doc_file": task.get("doc_file")}


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
        raw = resp.json().get("message", {}).get("content", "")

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
    return {"agent_name": agent_name[:40], "system_prompt": system_prompt}


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
        raw = resp.json().get("message", {}).get("content", "")

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
    return {"predecessors": preds, "successors": succs}


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
        raw = resp.json().get("message", {}).get("content", "")

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
    return {"detail": detail, "predecessors": preds, "successors": succs}


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
        raw = resp.json().get("message", {}).get("content", "")

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

    return {"tasks": tasks}


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
                raw = resp.json().get("message", {}).get("content", "")
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
        yield _sse({"type": "plan", "plan": plan})

    return StreamingResponse(_gen(), media_type="text/event-stream")


@app.post("/api/plans/generate")
async def generate_plan(req: Request):
    """Generiert aus einer Projektbeschreibung einen vollständigen Projektplan
    (Aufgaben mit Dauer, Abhängigkeiten und Ressourcen) per lokalem LLM.
    Nachfolger werden serverseitig aus den Vorgängern abgeleitet."""
    import re
    body = await req.json()
    _model = _pick_model(body.get("model"))
    description = (body.get("description") or "").strip()
    if not description:
        raise HTTPException(400, "Keine Projektbeschreibung angegeben")
    try:
        max_tasks = int(body.get("max_tasks", 12))
    except Exception:
        max_tasks = 12
    # Keine harte 20er-Grenze mehr – nur ein großzügiges Sicherheitsnetz gegen Ausreißer.
    max_tasks = max(5, min(max_tasks, 200))
    big_request = max_tasks > 30
    system_prompt = (body.get("system_prompt") or "").strip() or (
        "Du bist ein erfahrener Projektplaner und zerlegst Projekte in sinnvolle, "
        "chronologisch abhängige Arbeitspakete."
    )
    catalog = _normalize_catalog(body.get("resource_catalog"))
    res_mode = str(body.get("resource_mode", "free")).lower().strip()

    user = (
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
        "kind ist genau einer von: human, hardware, software."
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
    if big_request:
        # größeres Kontextfenster, damit lange Pläne nicht abgeschnitten werden
        payload["options"] = {"num_ctx": 8192}

    async with _model_session(_model), httpx.AsyncClient(timeout=600 if big_request else 300) as client:
        resp = await _llm.chat(client,payload)
        resp.raise_for_status()
        raw = resp.json().get("message", {}).get("content", "")

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
            "requested": max_tasks, "warning": warning.strip()}


# ── Backup / Restore ─────────────────────────────────────────────────────────

from datetime import datetime as _dt


@app.get("/api/backup")
async def create_backup():
    """Exportiert alle Nutzerdaten als ZIP-Archiv."""
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

    buf.seek(0)
    filename = f"ai_framework_thomas_backup_{today}.zip"
    from fastapi.responses import StreamingResponse as _SR
    return _SR(
        buf,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.post("/api/restore")
async def restore_backup(file: UploadFile = File(...)):
    """Importiert alle Nutzerdaten aus einem ZIP-Backup."""
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
                    content = resp.json().get("message", {}).get("content", "").strip()
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
            yield _sse({"type": "done", "text": current})

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


async def _med_call(client, model: str, system: str, user: str, *, think: bool = False) -> str:
    """Ein nicht-streamender Ollama-Chat-Aufruf, gibt den reinen Text zurück."""
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
    raw = resp.json().get("message", {}).get("content", "") or ""
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


@app.post("/api/medizin/consult")
async def medizin_consult(req: Request):
    """Eine Stufe der Medizin-Konsultation (siehe Beschreibung oben). Streamt
    SSE-Frames: stage / question / text / done / error."""
    body = await req.json()
    messages = body.get("messages") or []
    rag_collections = body.get("rag_collections") or []
    try:
        rnd = int(body.get("round", 0))
    except Exception:
        rnd = 0

    model_general = _pick_model(body.get("model_general"), _model_for("general"))
    model_medical = _pick_model(body.get("model_medical"), _model_for("medical"))

    transcript = _med_transcript(messages)
    latest = ""
    for m in reversed(messages):
        if m.get("role") == "user":
            latest = str(m.get("content", "")).strip()
            break

    async def _stream():
        if not transcript:
            yield _sse({"type": "error", "content": "Keine Eingabe erhalten."})
            return

        # ── Stage 1: Ministral strukturiert die Anfrage medizinisch ──────────
        yield _sse({"type": "stage", "stage": "refine", "status": "start",
                     "label": f"{model_general} strukturiert die Anfrage…"})
        try:
            async with _model_session(model_general), httpx.AsyncClient(timeout=120) as client:
                refined = await _med_call(
                    client, model_general,
                    ("Du bereitest Patientenanfragen für einen medizinischen Fachkollegen auf. "
                     "Formuliere aus dem Gesprächsverlauf eine sachliche, strukturierte medizinische "
                     "Falldarstellung in Stichpunkten (Anliegen, bekannte Angaben wie Alter/Geschlecht/"
                     "Symptome/Dauer/Vorerkrankungen/Medikamente, soweit genannt). Erfinde nichts, "
                     "ergänze keine nicht genannten Fakten. Nur die Falldarstellung, kein Vorwort."),
                    f"Gesprächsverlauf:\n{transcript}",
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
                         "Einschätzung wesentliche Angaben fehlen (z. B. Alter, Geschlecht, Dauer/"
                         "Verlauf, Schweregrad, Begleitsymptome, Vorerkrankungen, Medikamente, "
                         "Allergien). Wenn alles Wesentliche vorhanden ist, antworte mit GENAU dem "
                         "Wort VOLLSTAENDIG. Andernfalls beginne mit FEHLT: und liste danach in "
                         "kurzen Stichpunkten (max. 4) nur die wirklich fehlenden Angaben."),
                        analyze_user,
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
                             "Bündele sie in 1–3 einfachen Fragen. Nur die Rückfrage, kein Vorwort."),
                            f"Ursprüngliches Anliegen:\n{latest}\n\nFehlende Angaben:\n{analysis}",
                        )
                except Exception as e:
                    yield _sse({"type": "error", "content": f"Rückfrage fehlgeschlagen: {e}"})
                    return
                if not question:
                    question = "Können Sie bitte noch ein paar Angaben ergänzen (Alter, Dauer, Begleitsymptome)?"
                yield _sse({"type": "stage", "stage": "formulate", "status": "done"})
                yield _sse({"type": "question", "content": question, "round": rnd + 1})
                yield _sse({"type": "done", "needs_followup": True, "round": rnd + 1})
                return

        # ── Stage 3b: MedGemma erstellt die finale Einschätzung (gestreamt) ──
        yield _sse({"type": "stage", "stage": "final", "status": "start",
                     "label": f"{model_medical} erstellt die Einschätzung…"})
        final_user = f"Strukturierte Falldarstellung:\n{refined}\n\nVollständiger Verlauf:\n{transcript}"
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
        except Exception as e:
            yield _sse({"type": "error", "content": f"Einschätzung fehlgeschlagen: {e}"})
            return
        yield _sse({"type": "done", "needs_followup": False, "round": rnd})

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
        except Exception as e:
            yield _sse({"type": "error", "content": f"Übersetzung fehlgeschlagen: {e}"})
            return
        yield _sse({"type": "done"})

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


async def _mathe_ground_facts(client, model, messages) -> str:
    """Extrahiert die zentrale Aufgabe aus dem Gespräch und liefert die
    SymPy-verifizierte Grundwahrheit als Fakten-String (oder "" wenn nichts
    deterministisch prüfbar ist). Erwartet einen offenen httpx-Client, dessen
    Modell bereits unter ``_model_session`` geladen wurde."""
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
        raw = resp.json().get("message", {}).get("content", "") or ""
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
    async with _model_session(model), httpx.AsyncClient(timeout=90) as client:
        facts = await _mathe_ground_facts(client, model, messages)
    return {"facts": facts}


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
        try:
            async with _model_session(model), httpx.AsyncClient(timeout=180) as client:
                # 1) Deterministische Grundwahrheit
                yield _sse({"type": "stage", "stage": "verify", "status": "start",
                            "label": "SymPy-Grundwahrheit"})
                facts = await _mathe_ground_facts(client, model, messages)
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
                        solution = resp.json().get("message", {}).get("content", "") or ""
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
                            "rounds": rounds, "facts": facts})
        except Exception as e:
            yield _sse({"type": "error", "content": str(e)})

    return StreamingResponse(gen(), media_type="text/event-stream")


# ── Static Files (muss zuletzt kommen) ───────────────────────────────────────

app.mount("/", StaticFiles(directory="static", html=True), name="static")
