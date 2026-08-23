"""RAG-Engine: Bereinigung, Chunking, Ollama-Embeddings, Cosine-Suche.

Vektorspeicher ist SQLite (Embeddings als float32-BLOB, siehe db.py); die
Ähnlichkeitssuche läuft als Brute-Force-Cosine über NumPy – für ein lokales
Einzelplatz-Tool mit überschaubaren Dokumentmengen völlig ausreichend und ohne
zusätzliche schwergewichtige Vektor-DB.

Die Embeddings werden über ein kleines Ollama-Modell (z. B. nomic-embed-text)
erzeugt. Auf kleinen Grafikkarten wird das Modell per ``num_gpu=0`` auf die CPU
gezwungen, damit es das Chat-Modell nicht aus dem VRAM verdrängt (siehe
VRAM-Schutz in main.py).
"""

import json
import re
import unicodedata
from pathlib import Path

import httpx
import numpy as np

import db as _db
from tools import llm as _llm

# Konfiguration aus config.json (gleiche Quelle wie main.py)
_CONFIG = {}
try:
    _CONFIG = json.loads(Path("config.json").read_text(encoding="utf-8"))
except Exception:
    pass

OLLAMA_BASE = _CONFIG.get("ollama_base", "http://localhost:11434")
EMBED_MODEL = _CONFIG.get("embed_model", "nomic-embed-text")

# ── VRAM-Stufen: steuern Chunk-Größe, Overlap, Trefferanzahl und Embed-Gerät ──
# Kleinere Karten → kleinere Chunks + weniger Treffer (geringerer Kontext fürs
# LLM, dessen Kontextfenster VRAM-gebunden ist) und Embeddings auf der CPU.
TIERS = {
    "none": {"label": "Kein VRAM / nur CPU", "chunk_size": 500,  "chunk_overlap": 60,  "top_k": 3, "embed_gpu": False, "char_limit": 1500},
    "4gb":  {"label": "4 GB VRAM",           "chunk_size": 600,  "chunk_overlap": 80,  "top_k": 3, "embed_gpu": False, "char_limit": 2000},
    "6gb":  {"label": "6 GB VRAM",           "chunk_size": 900,  "chunk_overlap": 120, "top_k": 4, "embed_gpu": False, "char_limit": 3500},
    "12gb": {"label": "12 GB+ VRAM",         "chunk_size": 1400, "chunk_overlap": 200, "top_k": 6, "embed_gpu": True,  "char_limit": 6000},
}
DEFAULT_TIER = "6gb"


def tier_config(tier: str) -> dict:
    return TIERS.get(tier, TIERS[DEFAULT_TIER])


# ── Bereinigung ───────────────────────────────────────────────────────────────

# Bereinigungsstufen einer Sammlung (Spalte ``clean_level``):
#   "standard" – verlustfreie Vereinheitlichung (Unicode, Typografie, unsichtbare
#                Zeichen, Silbentrennung, Seitenzahlen). Struktur bleibt erhalten.
#   "strikt"   – zusätzlich verlustbehaftet: Markdown-Zeichen, Links/URLs und
#                wiederkehrende Kopf-/Fußzeilen (Amtsblatt-Layout) fliegen raus.
#                Für Behörden-/Gesetzes-PDFs, die einfache Parser sonst stören.
CLEAN_LEVELS = ("standard", "strikt")
DEFAULT_CLEAN_LEVEL = "standard"

# Typografische Sonderzeichen -> einfache ASCII-Entsprechung. NFKC allein deckt das
# nicht ab (Gedankenstriche und typografische Anfuehrungszeichen bleiben dort
# unveraendert), darum die explizite Tabelle. Bewusst als \u-Escapes notiert, damit
# diese Quelldatei selbst frei von unsichtbaren/mehrdeutigen Zeichen bleibt.
_CHAR_MAP = {
    # Leerzeichen-Varianten -> normales Leerzeichen
    "\u00a0": " ",  # geschuetztes Leerzeichen (NBSP)
    "\u202f": " ",  # schmales geschuetztes Leerzeichen
    "\u2007": " ",  # Ziffernleerzeichen
    "\u2002": " ",  # En-Space
    "\u2003": " ",  # Em-Space
    "\u2008": " ",  # Interpunktions-Leerzeichen
    "\u2009": " ",  # schmales Leerzeichen
    "\u200a": " ",  # Haarspatium
    "\u3000": " ",  # ideographisches Leerzeichen
    # Strich-Varianten -> einfacher Bindestrich
    "\u2010": "-", "\u2011": "-", "\u2012": "-", "\u2013": "-", "\u2014": "-", "\u2015": "-", "\u2212": "-",
    # Apostroph-Varianten -> gerades Apostroph
    "\u2018": "'", "\u2019": "'", "\u201a": "'", "\u201b": "'", "\u2032": "'",
    # Anfuehrungszeichen-Varianten -> gerades Anfuehrungszeichen
    "\u201c": '"', "\u201d": '"', "\u201e": '"', "\u201f": '"', "\u2033": '"', "\u00ab": '"', "\u00bb": '"',
    # Auslassungszeichen
    "\u2026": "...",
}
_CHAR_TABLE = str.maketrans(_CHAR_MAP)

# Seitenmarke der Form "12/144" bzw. "12 / 144" (EU-Amtsblatt und aehnliche Layouts)
_RE_PAGE_OF = re.compile(r"^\s*\d{1,4}\s*/\s*\d{1,4}\s*$")
# Reine Seitenzahl-Zeile (ggf. mit Strichen drumherum; Gedankenstriche sind zu
# diesem Zeitpunkt bereits auf "-" vereinheitlicht)
_RE_PAGE_NUM = re.compile(r"^[-\s]*\d{1,4}[-\s]*$")

# Markdown-Bestandteile (nur Stufe "strikt")
_RE_MD_IMAGE = re.compile(r"!\[([^\]]*)\]\([^)]*\)")
_RE_MD_LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")
_RE_MD_FENCE = re.compile(r"^\s*(?:```|~~~)")
_RE_MD_HEAD = re.compile(r"^\s{0,3}#{1,6}\s*")
_RE_MD_BULLET = re.compile(r"^\s{0,4}[-*+]\s+")
_RE_MD_BOLD = re.compile(r"(\*\*|__)(?=\S)(.+?)(?<=\S)\1", re.DOTALL)
_RE_MD_CODE = re.compile(r"`+([^`]+)`+")
_RE_URL = re.compile(r"https?://\S+|www\.\S+")


def _strip_markdown(text: str) -> str:
    """Entfernt Markdown-Auszeichnung und Links, behaelt den sichtbaren Text."""
    text = _RE_MD_IMAGE.sub(r"\1", text)
    text = _RE_MD_LINK.sub(r"\1", text)
    text = _RE_MD_BOLD.sub(r"\2", text)
    text = _RE_MD_CODE.sub(r"\1", text)
    out = []
    for line in text.split("\n"):
        if _RE_MD_FENCE.match(line):
            continue
        line = _RE_MD_HEAD.sub("", line)
        line = _RE_MD_BULLET.sub("", line)
        out.append(line)
    return _RE_URL.sub("", "\n".join(out))


def _drop_boilerplate(lines: list, min_count: int = 4, max_len: int = 60,
                      max_cv: float = 0.35) -> list:
    """Entfernt wiederkehrende Kopf-/Fusszeilen (Amtsblatt-Layout, ELI-Adressen ...).

    Statistisch statt fest verdrahtet, damit es auch bei anderen Aemtern und
    Sprachen greift. Haeufigkeit allein waere aber zu grob - ein mehrfach
    vorkommender Inhaltssatz wuerde mitgeloescht. Zusaetzliches Kriterium ist
    darum die *Regelmaessigkeit*: echte Seitenkoepfe/-fuesse wiederholen sich in
    nahezu konstantem Zeilenabstand (eine Seite = konstant viele Zeilen), waehrend
    inhaltliche Wiederholungen unregelmaessig verteilt sind. Verworfen wird nur,
    was oft *und* regelmaessig auftritt (Variationskoeffizient der Abstaende
    <= ``max_cv``) *und* kurz ist (``max_len``) - Seitenkoepfe sind typischerweise
    deutlich kuerzer als ein Inhaltssatz, was ganze Saetze zusaetzlich schuetzt.
    """
    positions = {}
    for i, line in enumerate(lines):
        s = line.strip()
        if s and len(s) <= max_len:
            positions.setdefault(s, []).append(i)

    drop = set()
    for s, pos in positions.items():
        if len(pos) < min_count:
            continue
        gaps = [b - a for a, b in zip(pos, pos[1:])]
        mean = sum(gaps) / len(gaps)
        if mean <= 0:
            continue
        var = sum((g - mean) ** 2 for g in gaps) / len(gaps)
        if (var ** 0.5) / mean <= max_cv:   # gleichmaessige Abstaende -> Kopf-/Fusszeile
            drop.add(s)

    if not drop:
        return lines
    return ["" if line.strip() in drop else line for line in lines]


def clean_text(text: str, level: str = DEFAULT_CLEAN_LEVEL) -> str:
    """Bereinigt extrahierten Dokumenttext vor dem Chunking.

    Stufe ``standard`` (verlustfrei):
    - Unicode-Normalisierung NFKC (vereinheitlicht u. a. PDF-Ligaturen)
    - typografische Sonderzeichen -> ASCII (Gedankenstriche, Anfuehrungszeichen, Ellipse)
    - entfernt unsichtbare Steuer-/Formatzeichen (weiche Trennzeichen, Zero-Width,
      Richtungssteuerung) - ausser ``\\n`` und ``\\t``
    - hebt Silbentrennung am Zeilenende auf (``Maschi-\\nnenbau`` -> ``Maschinenbau``)
    - entfernt reine Seitenzahl-Zeilen und Seitenmarken der Form ``12/144``
    - fuegt innerhalb von Absaetzen umgebrochene Zeilen zusammen, reduziert Leerzeilen

    Stufe ``strikt`` zusaetzlich (verlustbehaftet):
    - entfernt Markdown-Auszeichnung (``#``, ``**``, ``__``, Backticks, Aufzaehlungszeichen)
    - reduziert Links ``[Text](URL)`` auf ``Text`` und entfernt nackte URLs
    - entfernt wiederkehrende Kopf-/Fusszeilen (siehe :func:`_drop_boilerplate`)
    """
    if not text:
        return ""
    strict = str(level or "").lower() == "strikt"

    # 1) Unicode vereinheitlichen, typografische Zeichen ersetzen
    text = unicodedata.normalize("NFKC", text)
    text = text.translate(_CHAR_TABLE)
    # 2) unsichtbare Steuer-/Formatzeichen raus (ausser \n und \t)
    text = "".join(ch for ch in text if ch in "\n\t" or unicodedata.category(ch)[0] != "C")
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\t", " ")

    # 3) Markdown/Links (nur strikt) - zeilenbasiert, daher vor dem Zusammenzug
    if strict:
        text = _strip_markdown(text)

    # 4) Silbentrennung am Zeilenende aufheben
    text = re.sub(r"(\w)-\n(\w)", r"\1\2", text)

    lines = text.split("\n")
    if strict:
        lines = _drop_boilerplate(lines)

    out_lines = []
    for line in lines:
        s = line.strip()
        if _RE_PAGE_NUM.match(s) or _RE_PAGE_OF.match(s):  # Seitenzahl / "12/144"
            out_lines.append("")
            continue
        out_lines.append(re.sub(r" {2,}", " ", s))
    text = "\n".join(out_lines)

    # 5) Innerhalb von Absaetzen umgebrochene Zeilen verbinden (Einzelumbruch ->
    # Leerzeichen, Doppelumbruch = Absatzgrenze bleibt erhalten)
    text = re.sub(r"(?<!\n)\n(?!\n)", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r" {2,}", " ", text)
    return text.strip()


# ── Chunking ──────────────────────────────────────────────────────────────────

def chunk_text(text: str, size: int, overlap: int) -> list:
    """Teilt Text in überlappende Chunks (~``size`` Zeichen, ``overlap`` Überlappung).

    Schneidet bevorzugt an einer Wortgrenze, um Wörter nicht zu zerreißen."""
    text = (text or "").strip()
    if not text:
        return []
    size = max(100, int(size))
    overlap = max(0, min(int(overlap), size - 50))
    chunks = []
    start = 0
    n = len(text)
    while start < n:
        end = min(start + size, n)
        if end < n:
            # bis zur nächsten Wortgrenze rückwärts suchen
            ws = text.rfind(" ", start + size - overlap, end)
            if ws > start:
                end = ws
        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)
        if end >= n:
            break
        start = max(end - overlap, start + 1)
    return chunks


# ── Embeddings (Ollama) ─────────────────────────────────────────────────────

async def embed(texts: list, model: str = None, gpu: bool = False) -> list:
    """Erzeugt Embeddings für ``texts``.

    Lokale Modelle laufen über Ollama; ``gpu=False`` erzwingt dort CPU
    (``num_gpu=0``), damit das Embeddingmodell das Chat-Modell nicht aus dem VRAM
    verdrängt. Präfigierte Modellnamen (``<anbieter>::<modell>``) werden an den
    externen OpenAI-kompatiblen Anbieter weitergereicht — ``gpu`` ist dort ohne
    Bedeutung. So kann eine Sammlung wahlweise lokal oder per API embedden."""
    if not texts:
        return []
    model = model or EMBED_MODEL
    if _llm.is_remote(model):
        return await _llm.embed(texts, model)
    payload = {"model": model, "input": texts}
    if not gpu:
        payload["options"] = {"num_gpu": 0}
    async with httpx.AsyncClient(timeout=600) as client:
        resp = await client.post(f"{OLLAMA_BASE}/api/embed", json=payload)
        resp.raise_for_status()
        data = resp.json()
    embs = data.get("embeddings") or []
    return embs


def _to_blob(vec) -> bytes:
    return np.asarray(vec, dtype=np.float32).tobytes()


def _from_blob(blob: bytes) -> np.ndarray:
    return np.frombuffer(blob, dtype=np.float32)


# ── Ingestion ─────────────────────────────────────────────────────────────────

async def ingest_file(collection: dict, text: str, filename: str, doc_id: str,
                      image_rel: str = None) -> int:
    """Bereinigt (falls aktiviert), chunkt, embeddet und speichert ein Dokument.
    Gibt die Anzahl gespeicherter Chunks zurück.

    ``image_rel`` (optional): relativer Pfad des Originalbildes unter RAG_IMAGES_DIR
    für Bild-Dokumente – ``text`` ist dann die per Vision-Modell erzeugte Beschreibung,
    die wie normaler Text bereinigt/gechunkt/embeddet wird; die Verknüpfung zur Bilddatei
    wird am Dokument gespeichert (macht das Bild in Treffern anzeigbar)."""
    if collection.get("clean"):
        text = clean_text(text, collection.get("clean_level") or DEFAULT_CLEAN_LEVEL)
    else:
        text = (text or "").strip()
    chunks = chunk_text(text, collection["chunk_size"], collection["chunk_overlap"])
    if not chunks:
        return 0
    vecs = await embed(chunks, collection["embed_model"], bool(collection.get("embed_gpu")))
    if len(vecs) != len(chunks):
        _m = collection["embed_model"]
        _hint = ("Liefert der API-Anbieter für dieses Modell wirklich Embeddings?"
                 if _llm.is_remote(_m) else f"Ist das Modell '{_m}' in Ollama vorhanden?")
        raise RuntimeError(
            f"Embedding-Anzahl ({len(vecs)}) passt nicht zu Chunks ({len(chunks)}). {_hint}"
        )
    blobs = [_to_blob(v) for v in vecs]
    await _db.rag_add_document(doc_id, collection["id"], filename, chunks, blobs, image_rel=image_rel)
    return len(chunks)


# ── Suche ──────────────────────────────────────────────────────────────────────

async def query_collections(collection_ids: list, query: str, top_k_cap: int = 8) -> list:
    """Holt die ähnlichsten Chunks aus den gewählten Sammlungen.

    Embeddet die Anfrage pro vorkommendem Embeddingmodell genau einmal und
    bewertet per Cosine-Ähnlichkeit. Rückgabe: Liste von dicts mit
    ``text``, ``filename``, ``collection_name``, ``score`` (absteigend)."""
    query = (query or "").strip()
    if not query or not collection_ids:
        return []

    # Pro Sammlung Konfiguration laden (für Modell/Gerät/top_k)
    colls = {}
    for cid in collection_ids:
        c = await _db.rag_get_collection(cid)
        if c:
            colls[cid] = c
    if not colls:
        return []

    rows = await _db.rag_fetch_chunks(list(colls.keys()))
    if not rows:
        return []

    # Anfrage je (Modell, Gerät) einmal embedden
    q_cache = {}
    for c in colls.values():
        key = (c["embed_model"], bool(c["embed_gpu"]))
        if key not in q_cache:
            vec = await embed([query], c["embed_model"], bool(c["embed_gpu"]))
            q_cache[key] = np.asarray(vec[0], dtype=np.float32) if vec else None

    scored = []
    per_coll_topk = max((c["top_k"] for c in colls.values()), default=4)
    for r in rows:
        c = colls.get(r["collection_id"])
        if not c:
            continue
        qv = q_cache.get((c["embed_model"], bool(c["embed_gpu"])))
        if qv is None:
            continue
        dv = _from_blob(r["embedding"])
        if dv.shape != qv.shape:
            continue
        denom = (np.linalg.norm(qv) * np.linalg.norm(dv)) or 1.0
        score = float(np.dot(qv, dv) / denom)
        scored.append((score, r))

    scored.sort(key=lambda x: x[0], reverse=True)
    cap = min(top_k_cap, max(per_coll_topk, 1) * len(colls))

    # Zeichenbegrenzung: höchstes konfiguriertes Limit der gewählten Sammlungen.
    # Chunks werden nach Score aufgenommen, bis das Budget erschöpft ist; der
    # letzte Chunk wird ggf. gekürzt.
    char_budget = max((int(c.get("char_limit", 3000)) for c in colls.values()), default=3000)
    hits = []
    used = 0
    for score, r in scored[:cap]:
        if used >= char_budget:
            break
        text = r["text"]
        remaining = char_budget - used
        if len(text) > remaining:
            text = text[:remaining].rstrip() + " …"
        used += len(text)
        hit = {
            "text": text,
            "filename": r["filename"],
            "collection_name": r["collection_name"],
            "score": round(score, 4),
        }
        # Bild-aware RAG: stammt der Treffer aus einem Bild-Dokument, den Bildbezug
        # mitgeben, damit der Chat ein Thumbnail zeigen kann (Text = Vision-Beschreibung).
        if r.get("image_rel"):
            hit["document_id"] = r.get("document_id")
            hit["image_url"] = f"/api/rag/documents/{r.get('document_id')}/image"
        hits.append(hit)
    return hits
