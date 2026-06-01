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

def clean_text(text: str) -> str:
    """Bereinigt extrahierten Dokumenttext vor dem Chunking.

    - normalisiert Unicode/Whitespace, entfernt Steuerzeichen
    - hebt Silbentrennung am Zeilenende auf (z. B. ``Maschi-\\nnenbau`` → ``Maschinenbau``)
    - fügt innerhalb von Absätzen umgebrochene Zeilen zusammen
    - entfernt Zeilen, die nur aus einer Seitenzahl bestehen
    - reduziert mehrfache Leerzeilen auf eine
    """
    if not text:
        return ""
    text = unicodedata.normalize("NFC", text)
    # Steuerzeichen raus (außer \n und \t)
    text = "".join(ch for ch in text if ch == "\n" or ch == "\t" or unicodedata.category(ch)[0] != "C")
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\t", " ")
    # Silbentrennung am Zeilenende aufheben
    text = re.sub(r"(\w)-\n(\w)", r"\1\2", text)

    out_lines = []
    for line in text.split("\n"):
        s = line.strip()
        if re.fullmatch(r"[-–—\s]*\d{1,4}[-–—\s]*", s):  # reine Seitenzahl-Zeile
            out_lines.append("")
            continue
        s = re.sub(r"[  ]{2,}", " ", s)  # Mehrfach-Leerzeichen
        out_lines.append(s)
    text = "\n".join(out_lines)

    # Innerhalb von Absätzen umgebrochene Zeilen verbinden (Einzelumbruch → Leerzeichen,
    # Doppelumbruch = Absatzgrenze bleibt erhalten)
    text = re.sub(r"(?<!\n)\n(?!\n)", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"[ ]{2,}", " ", text)
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
    """Erzeugt Embeddings über Ollama. ``gpu=False`` erzwingt CPU (num_gpu=0),
    damit das Embeddingmodell das Chat-Modell nicht aus dem VRAM verdrängt."""
    if not texts:
        return []
    model = model or EMBED_MODEL
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

async def ingest_file(collection: dict, text: str, filename: str, doc_id: str) -> int:
    """Bereinigt (falls aktiviert), chunkt, embeddet und speichert ein Dokument.
    Gibt die Anzahl gespeicherter Chunks zurück."""
    if collection.get("clean"):
        text = clean_text(text)
    else:
        text = (text or "").strip()
    chunks = chunk_text(text, collection["chunk_size"], collection["chunk_overlap"])
    if not chunks:
        return 0
    vecs = await embed(chunks, collection["embed_model"], bool(collection.get("embed_gpu")))
    if len(vecs) != len(chunks):
        raise RuntimeError(
            f"Embedding-Anzahl ({len(vecs)}) passt nicht zu Chunks ({len(chunks)}). "
            f"Ist das Modell '{collection['embed_model']}' in Ollama vorhanden?"
        )
    blobs = [_to_blob(v) for v in vecs]
    await _db.rag_add_document(doc_id, collection["id"], filename, chunks, blobs)
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
        hits.append({
            "text": text,
            "filename": r["filename"],
            "collection_name": r["collection_name"],
            "score": round(score, 4),
        })
    return hits
