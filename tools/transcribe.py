"""Spracherkennung / Transkription (Audio → Text) — reine Logik, ohne FastAPI/DB.

Lokale Engine: **faster-whisper** (MIT). Läuft standardmäßig auf der **CPU**
(``compute_type="int8"``), damit die ~6 GB VRAM für Ollama frei bleiben; GPU ist per
Config (`stt_device="cuda"`) optional. Modelle werden von faster-whisper bei Bedarf von
HuggingFace nach ``download_root`` geladen (der Installer cached sie vor / das Portable-
Bundle liefert sie offline mit).

Der Import ist **weich** (``try/except``) — fehlt das Paket, meldet :func:`local_available`
``False`` und das Backend bietet nur den API-Weg an. Der API-Weg
(OpenAI/Groq-kompatibles ``/audio/transcriptions``) liegt in ``main.py`` (nutzt die
Provider-Konfiguration aus ``tools/llm.py``).

Lizenz-Hinweis: faster-whisper bringt **PyAV** als Audio-Decoder mit; PyAV bündelt
ffmpeg-Bibliotheken (LGPL, schwaches Copyleft, dynamisch gelinkt, unverändert) — analog
zur bereits dokumentierten ``libpff``-Ausnahme. Nötig, um komprimierte Uploads
(mp3/m4a/webm-opus) zu dekodieren.
"""

from __future__ import annotations

from typing import Optional

try:  # weiche Abhängigkeit
    from faster_whisper import WhisperModel  # type: ignore
    _HAVE_FW = True
except Exception:  # pragma: no cover - nur wenn Paket fehlt
    WhisperModel = None  # type: ignore
    _HAVE_FW = False

# Übliche faster-whisper-Modellgrößen (klein → groß). Alle laufen auf CPU; auf einer
# 6-GB-Karte sind bis „small" bequem möglich, „medium" mit int8 knapp.
KNOWN_MODELS = ["tiny", "base", "small", "medium", "large-v3"]

# Geladene Modelle zwischenspeichern (Laden dauert; ein Reload je Aufruf wäre teuer).
_CACHE: dict = {}


def local_available() -> bool:
    """Ist die lokale Engine (faster-whisper) importierbar?"""
    return _HAVE_FW


def list_local_models() -> list:
    """Bekannte lokale Modellgrößen (Reihenfolge klein→groß)."""
    return list(KNOWN_MODELS)


def _get_model(model: str, device: str, compute: str, download_root: Optional[str]):
    key = (model, device, compute, download_root or "")
    inst = _CACHE.get(key)
    if inst is None:
        inst = WhisperModel(model, device=device, compute_type=compute,
                            download_root=download_root)
        _CACHE[key] = inst
    return inst


def transcribe_local(path: str, model: str = "base", language: Optional[str] = None,
                     task: str = "transcribe", device: str = "cpu",
                     compute: str = "int8",
                     download_root: Optional[str] = None) -> dict:
    """Transkribiert eine Audiodatei lokal mit faster-whisper.

    Args:
        path:          Pfad zur Audiodatei (wav/mp3/m4a/webm … via PyAV dekodiert).
        model:         Modellgröße (siehe :data:`KNOWN_MODELS`).
        language:      ISO-Sprachcode (z. B. ``"de"``) oder ``None``/``"auto"`` → Erkennung.
        task:          ``"transcribe"`` (Originalsprache) oder ``"translate"`` (→ Englisch).
        device:        ``"cpu"`` (Standard) / ``"cuda"`` / ``"auto"``.
        compute:       ``"int8"`` (Standard, CPU-schonend) / ``"float16"`` (GPU) …
        download_root: Zielordner für Modell-Downloads (offline-fähig).

    Returns:
        ``{"text", "segments":[{"start","end","text"}], "language", "duration"}``

    Raises:
        RuntimeError, wenn faster-whisper nicht installiert ist.
    """
    if not _HAVE_FW:
        raise RuntimeError("faster-whisper ist nicht installiert (lokale Transkription "
                           "nicht verfügbar).")
    lang = (language or "").strip().lower()
    if lang in ("", "auto"):
        lang = None
    tsk = "translate" if str(task).strip().lower() == "translate" else "transcribe"
    inst = _get_model(model, device, compute, download_root)
    segments, info = inst.transcribe(path, language=lang, task=tsk, vad_filter=True)
    seg_list = []
    parts = []
    for s in segments:  # Generator → hier wird tatsächlich dekodiert/erkannt
        txt = (s.text or "").strip()
        seg_list.append({"start": round(float(s.start), 2),
                         "end": round(float(s.end), 2), "text": txt})
        if txt:
            parts.append(txt)
    return {
        "text": " ".join(parts).strip(),
        "segments": seg_list,
        "language": getattr(info, "language", lang) or "",
        "duration": round(float(getattr(info, "duration", 0.0) or 0.0), 2),
    }
