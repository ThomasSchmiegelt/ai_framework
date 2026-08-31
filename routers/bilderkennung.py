"""Router: Bilderkennung / Objekt-Grounding (/api/bilderkennung)

Bild hochladen → per Text sagen „such/markiere X" → das gefundene Objekt wird mit
**Koordinaten** (Bounding-Box) zurückgegeben, die das Frontend als Rahmen aufs Bild
zeichnet. Anders als ``/api/analyze-image`` (nur Beschreibung/Text) liefert dieser
Endpoint das *wo*.

**Braucht ein grounding-fähiges Vision-Modell** (z. B. ``qwen2.5vl`` — lokal via
Ollama, Apache-2.0). Reine Beschreib-Modelle (``llava``) liefern keine Koordinaten →
sauberer Rückfall: nur die Text-Antwort, ``found`` bleibt evtl. ``true`` ohne Boxen.

Nutzt die geteilte Vision-Plumbing (``_llm.chat`` + ``images:[b64]``, funktioniert
lokal **und** remote), ``downscale`` und ``_vision_model``. Token-Label „Bilderkennung".
"""
from __future__ import annotations

import base64
import io
import re
from typing import Any, Dict, List, Optional

import httpx
from fastapi import APIRouter, HTTPException, Request

from tools import llm as _llm

from core import *  # noqa: F401,F403  (geteilte Kernflaeche)
import core as _core  # noqa: F401

router = APIRouter()


_DETECT_SYSTEM = (
    "Du bist ein präzises Objekt-Lokalisierungs-System für Bilder. Der Nutzer nennt ein "
    "gesuchtes Objekt/Merkmal. Finde ALLE passenden Vorkommen im Bild und gib ihre Lage "
    "als Bounding-Box zurück.\n"
    "Antworte AUSSCHLIESSLICH mit JSON in genau diesem Format, ohne weiteren Text:\n"
    '{"found": true, "answer": "kurzer Satz auf Deutsch, was gefunden wurde und wo", '
    '"boxes": [{"label": "kurze Bezeichnung", "box": [x1, y1, x2, y2]}]}\n'
    "Koordinaten sind GANZZAHLEN im Bereich 0–1000, normiert auf die Bildbreite (x) bzw. "
    "Bildhöhe (y); Ursprung ist oben links, x1<x2 und y1<y2. Gib höchstens die "
    "angeforderte Anzahl Boxen zurück, die relevantesten zuerst. "
    'Ist nichts Passendes zu sehen: {"found": false, "answer": "…", "boxes": []}.'
)


def _small_dims(small_b64: str) -> tuple:
    """Maße (w,h) des heruntergerechneten Bildes — erlaubt dem Frontend, auch Modelle
    mit Pixel- statt 0–1000-Ausgabe korrekt zu skalieren. Best-effort (PIL)."""
    try:
        from PIL import Image  # lokal importiert wie in tools/imaging.py
        raw = base64.b64decode(small_b64)
        with Image.open(io.BytesIO(raw)) as im:
            return int(im.width), int(im.height)
    except Exception:
        return 0, 0


def _clean_boxes(raw_boxes: Any, limit: int) -> List[Dict[str, Any]]:
    """Boxen defensiv säubern: nur 4-Zahlen-Listen, auf 0–1000 clampen, x1<x2/y1<y2,
    Label gekürzt. Kleine Modelle liefern gern kaputte Strukturen → tolerant sein."""
    out: List[Dict[str, Any]] = []
    if not isinstance(raw_boxes, list):
        return out
    for item in raw_boxes:
        if not isinstance(item, dict):
            continue
        box = item.get("box")
        if not (isinstance(box, list) and len(box) == 4):
            continue
        try:
            nums = [float(v) for v in box]
        except (TypeError, ValueError):
            continue
        # 0–1000 clampen (falls Modell darüber liegt, wird per image_w/h im Frontend normiert)
        hi = max(nums)
        cap = 1000.0 if hi <= 1000.0 else hi
        x1, y1, x2, y2 = nums
        x1, x2 = sorted((x1, x2))
        y1, y2 = sorted((y1, y2))
        clamp = lambda v: max(0.0, min(cap, v))  # noqa: E731
        label = str(item.get("label") or "").strip()[:80]
        out.append({"label": label, "box": [round(clamp(x1)), round(clamp(y1)),
                                             round(clamp(x2)), round(clamp(y2))]})
        if len(out) >= limit:
            break
    return out


@router.get("/api/bilderkennung/vision-models")
async def bilderkennung_models():
    """Lokal installierte, vision-fähige Ollama-Modelle (für das Tab-Dropdown).
    Leere Liste ⇒ Frontend rät zu ``ollama pull qwen2.5vl``."""
    models: List[str] = []
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            resp = await client.get(f"{OLLAMA_BASE}/api/tags")
            resp.raise_for_status()
            for m in resp.json().get("models", []):
                if "vision" in (m.get("capabilities") or []) and m.get("name"):
                    models.append(m["name"])
    except Exception:
        pass
    return {"models": models, "recommended": "qwen2.5vl"}


@router.post("/api/bilderkennung")
async def bilderkennung_detect(req: Request):
    """Objekt-Grounding: Body ``{image (base64/Data-URI), query, model?, max_boxes?}``
    → ``{found, answer, boxes:[{label, box:[x1,y1,x2,y2]}], image_w, image_h, model,
    tokens}`` (Boxen normiert 0–1000). 503 ohne lokales Vision-Modell."""
    from tools.imaging import downscale
    body = await req.json()
    image_b64 = body.get("image") or ""
    if not image_b64:
        raise HTTPException(400, "Kein Bild übergeben")
    query = str(body.get("query", "") or "").strip()
    if not query:
        raise HTTPException(400, "Kein Suchbegriff übergeben")
    try:
        max_boxes = int(body.get("max_boxes") or 8)
    except (TypeError, ValueError):
        max_boxes = 8
    max_boxes = max(1, min(30, max_boxes))

    _model = await _vision_model(_pick_model(body.get("model")))
    if not _model:
        raise HTTPException(503, "Kein lokales Vision-Modell verfügbar. "
                                 "Bitte ein multimodales Modell installieren, z. B. "
                                 "'ollama pull qwen2.5vl'.")

    small = downscale(image_b64)
    sw, sh = _small_dims(small)

    user_text = (f"Gesucht: {query}\n"
                 f"Gib höchstens {max_boxes} Bounding-Box(en) für dieses Objekt/Merkmal zurück.")
    _tok = {"in": 0, "out": 0}
    async with _model_session(_model), httpx.AsyncClient(timeout=180) as client:
        resp = await _llm.chat(client, {
            "model": _model, "think": False, "format": "json", "stream": False,
            "messages": [{"role": "system", "content": _DETECT_SYSTEM},
                         {"role": "user", "content": user_text, "images": [small]}],
            "options": {"num_ctx": _profile_num_ctx()},
            "keep_alive": KEEP_ALIVE,
        })
        resp.raise_for_status()
        _j = resp.json()
    _ti, _to = _llm_tok(_j)
    _tok["in"] += _ti
    _tok["out"] += _to

    raw = (_j.get("message", {}) or {}).get("content", "") or ""
    raw = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL).strip()
    data = _parse_llm_json(raw)
    if not isinstance(data, dict):
        data = {}
    boxes = _clean_boxes(data.get("boxes"), max_boxes)
    answer = str(data.get("answer") or "").strip()[:600]
    found = bool(data.get("found")) or bool(boxes)
    if not answer:
        answer = (f'„{query}“ wurde markiert.' if boxes
                  else f'„{query}“ konnte nicht sicher lokalisiert werden.')

    return {"found": found, "answer": answer, "boxes": boxes,
            "image_w": sw, "image_h": sh, "model": _model, "tokens": _tok}
