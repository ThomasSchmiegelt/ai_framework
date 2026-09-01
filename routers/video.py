"""Router: Videogenerierung (/api/video) — lokal über die z-video/Wan-Brücke.

Spiegelt das Bild-Muster (routers/image.py): dünnes HTTP-Plumbing, der Kern
(``_generate_video_core``) und alle Helfer liegen in ``core`` (Chat-Loop-tauglich,
kein Router↔Router-Zyklus). Geteilte Namen kommen über ``from core import *``.
"""
from __future__ import annotations

import asyncio
import re
import time

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse

from tools import llm as _llm
from core import *  # noqa: F401,F403  (geteilte Kernfläche)

router = APIRouter()

_ENHANCE_SYSTEM = (
    "Du bist ein Assistent für Text-zu-Video-Prompts. Formuliere die Eingabe des Nutzers "
    "zu EINEM kompakten, anschaulichen Video-Prompt aus: konkrete Szene, Kamerabewegung/-"
    "perspektive, Licht/Atmosphäre, Stil, Tempo. Behalte die Kernaussage bei, erfinde keine "
    "widersprüchlichen Motive. Antworte NUR mit dem Prompt selbst – keine Erklärung, keine "
    "Anführungszeichen, keine Aufzählung, höchstens ca. 60 Wörter. Sprache wie die Eingabe."
)


@router.get("/api/video/config")
async def video_config():
    """UI-Info analog ``/api/image/config``: aktives Videomodell, Server-URL, Presets,
    Modi und die wählbaren Optionen (aus = leer, lokal Wan)."""
    m = _video_model()
    options = [
        {"value": "", "label": "Aus (keine Videogenerierung)"},
        {"value": "local::wan", "label": "Lokal · Wan (z-video)"},
    ]
    if m and not any(o["value"] == m for o in options):
        options.append({"value": m, "label": m})
    return {
        "video_model": m,
        "video_url": _video_url(),
        "autostart": bool(_load_profile().get("video_autostart", True)),
        "model_cached": _video_model_cached(),
        "sizes": [{"value": k, "label": v["label"], "w": v["wh"][0], "h": v["wh"][1]}
                  for k, v in _VIDEO_SIZES.items()],
        "modes": [{"value": k, "label": v} for k, v in _VIDEO_MODES.items()],
        "options": options,
    }


@router.post("/api/video/enhance-prompt")
async def video_enhance_prompt(req: Request):
    """Optionale Prompt-Erweiterung für die Videoerzeugung (Wan empfiehlt das für bessere
    Ergebnisse). Formuliert die kurze Eingabe per LLM zu einem anschaulichen Video-Prompt
    aus. Modellrolle ``general`` (Geheim/Hartman → lokal). Deterministischer Rückfall: bei
    fehlendem Modell/Fehler wird der Originaltext zurückgegeben. Meldet ``tokens``."""
    body = await req.json()
    prompt = str(body.get("prompt", "") or "").strip()
    if not prompt:
        return {"prompt": "", "tokens": {"in": 0, "out": 0}}
    model = _model_for("general")   # Geheim/Hartman erzwingt lokal
    if not model:
        return {"prompt": prompt, "tokens": {"in": 0, "out": 0}}
    try:
        async with _model_session(model), httpx.AsyncClient(timeout=90) as client:
            resp = await _llm.chat(client, {
                "model": model, "think": False, "stream": False,
                "options": {"num_ctx": _profile_num_ctx(), "temperature": 0.7},
                "keep_alive": KEEP_ALIVE,
                "messages": [{"role": "system", "content": _ENHANCE_SYSTEM},
                             {"role": "user", "content": prompt}],
            })
            resp.raise_for_status()
            j = resp.json()
        raw = (j.get("message") or {}).get("content", "") or ""
        raw = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL).strip()
        raw = raw.strip().strip('"').strip("„").strip("“").strip()
        ti, to = _llm_tok(j)
    except Exception:
        return {"prompt": prompt, "tokens": {"in": 0, "out": 0}}
    return {"prompt": raw or prompt, "tokens": {"in": ti, "out": to}}


async def _video_generate_generator(body: dict):
    """SSE-Lauf: ``start`` → periodische ``progress`` (Sekunden-Heartbeat, während der
    Brücken-Aufruf als Task läuft) → ``video`` (mit ``video_url``) → ``done``/``error``."""
    mode = str(body.get("mode", "t2v") or "t2v").strip().lower()
    prompt = str(body.get("prompt", "") or "").strip()
    first_b64 = str(body.get("first", "") or body.get("first_b64", "") or "")
    last_b64 = str(body.get("last", "") or body.get("last_b64", "") or "")
    opts = {k: body.get(k) for k in ("negative", "size", "frames", "fps", "steps", "seed")}

    yield _sse({"type": "start", "mode": mode})
    task = asyncio.create_task(
        _generate_video_core(mode, prompt, first_b64, last_b64, opts))
    t0 = time.monotonic()
    try:
        while not task.done():
            await asyncio.sleep(2.0)
            yield _sse({"type": "progress", "elapsed": int(time.monotonic() - t0)})
    except asyncio.CancelledError:
        task.cancel()
        raise
    try:
        result = task.result()
    except HTTPException as e:
        yield _sse({"type": "error", "message": str(e.detail)})
        return
    except Exception as e:  # noqa: BLE001
        yield _sse({"type": "error", "message": str(e)})
        return
    yield _sse({"type": "video", "video_url": result.get("video_url", ""),
                "mode": result.get("mode", mode), "prompt": prompt,
                "elapsed": int(time.monotonic() - t0)})
    yield _sse({"type": "done"})


@router.post("/api/video/generate")
async def video_generate(req: Request):
    """Erzeugt ein Video (SSE). Body ``{mode, prompt, first, last, size, frames, fps,
    steps, seed}``. Video ≠ Token-Strom → kein TokenMeter (wie Bild/Audio)."""
    body = await req.json()
    return StreamingResponse(_video_generate_generator(body),
                             media_type="text/event-stream")


@router.get("/api/video/file/{vid}")
async def video_file(vid: str):
    """Liefert ein erzeugtes Video als mp4 (FileResponse). ``vid`` ist ein reiner
    Hex-Name (uuid4) → strikte Prüfung statt ``_safe_relpath`` genügt."""
    if not re.fullmatch(r"[0-9a-f]{8,64}", vid or ""):
        raise HTTPException(400, "Ungültige Video-ID.")
    fp = VIDEOS_DIR / f"{vid}.mp4"
    if not fp.exists() or not fp.is_file():
        raise HTTPException(404, "Video nicht gefunden.")
    return FileResponse(fp, media_type="video/mp4", filename=f"{vid}.mp4")
