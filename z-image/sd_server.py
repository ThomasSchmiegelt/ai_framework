#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Z-Image-Turbo als lokaler, A1111-kompatibler Bild-Server ("Brücke").

Damit die 🎨-Bildfunktion im Chat des AI-Frameworks (Profil-Weg `local::sd`) Bilder
über das lokale Z-Image-Turbo erzeugt, ohne dass ein echter Stable-Diffusion-WebUI
läuft. Der Server spricht genau die eine Schnittstelle, die das Framework aufruft:

    POST /sdapi/v1/txt2img   ->   {"images": ["<base64-png>"]}

Im Framework-Profil dann unter „🎨 Bildgenerierung" **Lokal · Stable Diffusion
WebUI** wählen und als Adresse die URL dieses Servers eintragen
(Standard: http://127.0.0.1:7860).

Start (Windows):  sd_server.bat
Start (Linux):    ./sd_server.sh

Nur Python-Standardbibliothek für den Server (kein FastAPI/uvicorn) -> keine neue
Abhängigkeit. Das Modell wird EINMAL beim Start geladen und bleibt warm.

VRAM: Standardmäßig läuft die Pipeline im **CPU-Offload** (Gewichte im RAM, nur die
aktive Komponente auf der GPU) und entlädt vor jeder Erzeugung geladene
Ollama-Modelle. So teilen sich Chat (Ollama) und Bildgenerierung die eine GPU, ohne
dass der VRAM überläuft. Mit --full-gpu belegt das Modell dauerhaft ~16 GB VRAM
(schneller, aber dann bleibt für Ollama weniger übrig).
"""
import argparse
import base64
import io
import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# WICHTIG vor dem ersten torch-Import: expandable_segments verringert die
# VRAM-Fragmentierung bei wiederholten Generierungen. Ohne das kippt der
# Allokator ab dem 2. Bild beim VAE-Decode über die 24-GB-Grenze -> harter
# CUDA-Absturz ohne Python-Traceback.
os.environ.setdefault("PYTORCH_CUDA_ALLOC_CONF", "expandable_segments:True")

# Helfer aus generate.py wiederverwenden (gleicher Ordner): HF-Token + Ollama-VRAM.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import generate as _gen   # noqa: E402

MODEL_ID = _gen.MODEL_ID

_pipe = None                 # geladene Pipeline (einmalig)
_device = "cpu"              # Ziel-Gerät für die Erzeugung ("cuda"/"cpu")
_lock = threading.Lock()     # eine Erzeugung zur Zeit (eine GPU)
_cfg = None                  # argparse-Namespace


def _load_pipeline(args):
    """Z-Image-Turbo einmal laden und warm halten.

    Zwei Modi:
      • Offload (Standard): nur die aktive Komponente liegt auf der GPU, der Rest
        im RAM -> niedriger Spitzen-VRAM (~12 GB), robust gegen Wiederhol-OOM,
        Koexistenz mit Ollama.
      • --full-gpu: Modell bleibt dauerhaft im VRAM (schneller, belegt ~20 GB)."""
    global _pipe, _device
    if _gen._load_hf_token() == "file":
        print("Hugging-Face-Token aus hf_token.txt geladen.")
    import torch
    from diffusers import ZImagePipeline

    if not torch.cuda.is_available():
        print("WARNUNG: keine CUDA-GPU – Erzeugung läuft langsam auf der CPU.")
        _device, dtype = "cpu", torch.float32
    else:
        _device, dtype = "cuda", torch.bfloat16
        print(f"GPU: {torch.cuda.get_device_name(0)}")

    print(f"Lade Modell {MODEL_ID} … (aus dem Cache; erster Start lädt ~20 GB)")
    t0 = time.time()
    pipe = ZImagePipeline.from_pretrained(MODEL_ID, dtype=dtype)
    if _device == "cpu":
        pipe.to("cpu")
    elif args.full_gpu:
        pipe.to("cuda")
        print("Modus: full-gpu (Modell dauerhaft im VRAM)")
    else:
        pipe.enable_model_cpu_offload()   # nur aktive Komponente auf der GPU
        print("Modus: Offload (niedriger Spitzen-VRAM, Koexistenz mit Ollama)")
    # VAE speicherschonend dekodieren -> senkt die VRAM-Spitze beim letzten Schritt.
    for _m in ("enable_vae_slicing", "enable_vae_tiling"):
        try:
            getattr(pipe, _m)()
        except Exception:
            pass
    _pipe = pipe
    print(f"Bereit in {time.time() - t0:.1f} s.  Höre auf http://{args.host}:{args.port}")


def _generate(prompt, negative, width, height):
    """Ein Bild erzeugen und als base64-PNG (ohne data:-Präfix) zurückgeben."""
    import torch
    with _lock:
        # Vor der Erzeugung geladene Ollama-Modelle entladen (VRAM frei machen).
        if not _cfg.keep_ollama:
            stopped = _gen._free_ollama_vram()
            if stopped:
                print("  Ollama entladen (VRAM frei): " + ", ".join(stopped))
                time.sleep(2.0)   # dem Treiber Zeit geben, den VRAM wirklich freizugeben
        if _device == "cuda":
            torch.cuda.empty_cache()   # sauber in die Erzeugung starten
        g = None
        if _cfg.seed is not None and _cfg.seed >= 0:
            g = torch.Generator(_device).manual_seed(_cfg.seed)
        t0 = time.time()
        try:
            image = _pipe(
                prompt,
                negative_prompt=negative or None,
                height=height, width=width,
                num_inference_steps=_cfg.steps,   # Turbo: wenige Schritte (Framework-Wert wird ignoriert)
                guidance_scale=0.0,               # Turbo: immer 0
                generator=g,
            ).images[0]
        finally:
            if _device == "cuda":
                torch.cuda.empty_cache()   # VRAM nach jedem Bild wieder freigeben
        buf = io.BytesIO()
        image.save(buf, format="PNG")
        print(f"  Bild erzeugt in {time.time() - t0:.1f}s ({width}x{height}, {_cfg.steps} Schritte)")
        return base64.b64encode(buf.getvalue()).decode("ascii")


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):    # eigene, knappe Logs
        pass

    def _send_json(self, code, obj):
        body = json.dumps(obj).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        # Kleiner Gesundheits-Endpunkt (praktisch zum Testen).
        if self.path.rstrip("/") in ("/health", "/sdapi/v1/health"):
            self._send_json(200, {"status": "ok", "model": MODEL_ID})
        else:
            self._send_json(404, {"error": "not found"})

    def do_POST(self):
        if self.path.rstrip("/") != "/sdapi/v1/txt2img":
            self._send_json(404, {"error": "not found"})
            return
        try:
            n = int(self.headers.get("Content-Length", 0))
            req = json.loads(self.rfile.read(n) or b"{}")
        except Exception as e:
            self._send_json(400, {"error": f"ungültiger Request: {e}"})
            return
        prompt = str(req.get("prompt", "") or "").strip()
        if not prompt:
            self._send_json(400, {"error": "kein prompt"})
            return
        negative = str(req.get("negative_prompt", "") or "")
        width = int(req.get("width") or 1024)
        height = int(req.get("height") or 1024)
        print(f"txt2img: \"{prompt[:70]}\" {width}x{height}")
        try:
            b64 = _generate(prompt, negative, width, height)
        except Exception as e:
            print(f"  FEHLER: {e}")
            self._send_json(500, {"error": str(e)})
            return
        # A1111-kompatible Antwort: das Framework liest images[0].
        self._send_json(200, {"images": [b64], "parameters": {}, "info": "z-image-turbo"})


def main():
    global _cfg
    ap = argparse.ArgumentParser(description="Z-Image-Turbo als A1111-kompatibler Bild-Server")
    ap.add_argument("--host", default="127.0.0.1", help="Adresse (Standard: 127.0.0.1)")
    ap.add_argument("--port", type=int, default=7860, help="Port (Standard: 7860)")
    ap.add_argument("--steps", type=int, default=8, help="Denoising-Schritte (Turbo: 8)")
    ap.add_argument("--seed", type=int, default=None, help="fester Seed (sonst zufällig)")
    ap.add_argument("--full-gpu", dest="full_gpu", action="store_true",
                    help="Modell dauerhaft im VRAM halten (schneller, belegt ~16 GB); sonst CPU-Offload")
    ap.add_argument("--keep-ollama", dest="keep_ollama", action="store_true",
                    help="geladene Ollama-Modelle NICHT vor jeder Erzeugung entladen")
    _cfg = ap.parse_args()

    _load_pipeline(_cfg)
    srv = ThreadingHTTPServer((_cfg.host, _cfg.port), _Handler)
    print("Im Framework-Profil unter „🎨 Bildgenerierung“ die Adresse eintragen:")
    print(f"    http://{_cfg.host}:{_cfg.port}")
    print("Beenden mit Strg+C.")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nServer beendet.")


if __name__ == "__main__":
    main()
