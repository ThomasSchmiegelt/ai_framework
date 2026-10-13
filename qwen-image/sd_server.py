#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Qwen-Image-2.1 als lokaler, A1111-kompatibler Bild-Server ("Brücke") — CRASH-SICHER.

Damit die 🎨-Bildfunktion im AI-Framework (Profil-Weg `local::qwen`) Bilder über
das lokale Qwen-Image-2.1 erzeugt, ohne echten Stable-Diffusion-WebUI. Der Server
spricht genau die eine Schnittstelle, die das Framework aufruft:

    POST /sdapi/v1/txt2img   ->   {"images": ["<base64-png>"]}
    POST /sdapi/v1/img2img   ->   {"images": ["<base64-png>"]}   (Bildbearbeitung)

Im Framework-Profil unter „🎨 Bildgenerierung" **Lokal · Qwen-Image 2.1** wählen
und als Adresse die URL dieses Servers eintragen (Standard http://127.0.0.1:7861).

Start (Windows):  sd_server.bat
Start (Linux):    ./sd_server.sh

── Warum das crash-sicher ist ────────────────────────────────────────────────
Der Server-Prozess importiert selbst KEIN torch/CUDA. Jede Bilderzeugung läuft in
einem FRISCHEN Unterprozess (generate.py), der genau EIN Bild erzeugt und sich dann
beendet — der bewiesen stabile Weg (identisch zu bild.bat, das nie abgestürzt ist).
Folgen:
  • Kein VRAM/Fragmentierungs-Aufbau über mehrere Läufe
    (genau das ließ die frühere In-Prozess-Variante beim 2. Bild hart abstürzen).
  • Ein CUDA-Fehler tötet nur den Unterprozess; der Server meldet ihn und lebt weiter.
  • generate.py macht selbst die VRAM-Sicherung (Ollama entladen, freien VRAM messen,
    bei Knappheit CPU-Offload) und gibt nach dem Bild den gesamten VRAM wieder frei.
Preis: jedes Bild lädt das Modell neu aus dem Cache (~40–60 s). Dafür stabil.

Qwen-Image-Spezifik: 50 Schritte, guidance 0.0, **kein negatives Prompt** (rectified
flow / kein CFG) — die vom Framework gesandten SD-Felder (steps=28/cfg=6.5/negative)
werden ignoriert und durch die Qwen-Defaults ersetzt (wie bei Z-Image).
Nur Python-Standardbibliothek -> keine neue Abhängigkeit.
"""
import argparse
import base64
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# stdout/stderr auf UTF-8 zwingen (Auto-Start mit umgeleitetem stdout → sonst cp1252
# und ein Emoji/Umlaut in einer print()-Zeile lässt den Prozess abstürzen).
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

_HERE = os.path.dirname(os.path.abspath(__file__))
_GEN = os.path.join(_HERE, "generate.py")
_lock = threading.Lock()   # eine Erzeugung zur Zeit (eine GPU)
_cfg = None                # argparse-Namespace


def _run_generate(prompt, width, height, init_b64=None, strength=None, mask_b64=None):
    """generate.py in einem frischen Unterprozess ausführen -> base64-PNG. Mit
    ``init_b64`` = Bildbearbeitung (img2img); zusätzlich ``mask_b64`` = Inpainting.
    Wirft RuntimeError bei Fehler/Timeout (der Server bleibt am Leben)."""
    def _decode(b):
        if "," in b and b.strip().startswith("data:"):
            b = b.split(",", 1)[1]
        return base64.b64decode(b)
    with _lock:
        with tempfile.TemporaryDirectory() as td:
            out = os.path.join(td, "img.png")
            cmd = [sys.executable, _GEN, prompt,
                   "--width", str(int(width)), "--height", str(int(height)),
                   "--steps", str(_cfg.steps),
                   "--out", "img.png", "--outdir", td,
                   "--min-free", str(_cfg.min_free)]
            if init_b64:
                init_path = os.path.join(td, "init.png")
                with open(init_path, "wb") as _f:
                    _f.write(_decode(init_b64))
                cmd += ["--init", init_path, "--strength",
                        str(strength if strength is not None else 0.6)]
                if mask_b64:
                    mask_path = os.path.join(td, "mask.png")
                    with open(mask_path, "wb") as _f:
                        _f.write(_decode(mask_b64))
                    cmd += ["--mask", mask_path]
            if not _cfg.full_gpu:
                cmd.append("--offload")          # niedriger Spitzen-VRAM (Standard: sicher)
            if _cfg.keep_ollama:
                cmd.append("--keep-ollama")
            t0 = time.time()
            try:
                p = subprocess.run(cmd, capture_output=True, text=True,
                                   timeout=_cfg.timeout, cwd=_HERE)
            except subprocess.TimeoutExpired:
                raise RuntimeError(f"Zeitüberschreitung nach {_cfg.timeout}s "
                                   f"(Modell lädt/erzeugt zu lange)")
            if p.returncode != 0 or not os.path.exists(out):
                tail = (p.stderr or p.stdout or "").strip().splitlines()
                msg = " | ".join(t.strip() for t in tail[-5:]) or f"Exit {p.returncode}"
                # Klarer Hinweis auf das häufigste Setup-Problem.
                if ("QwenImagePipeline" in msg or "ImportError" in msg or
                        "cannot import name" in msg):
                    msg += ("  → Bitte diffusers aktualisieren: "
                            f"\"venv\\Scripts\\python.exe\" -m pip install -U diffusers  "
                            "(bzw. venv/bin/pip -U diffusers)")
                raise RuntimeError("Erzeugung fehlgeschlagen: " + msg)
            with open(out, "rb") as f:
                data = f.read()
            print(f"  Bild erzeugt in {time.time() - t0:.1f}s ({width}x{height}, "
                  f"{_cfg.steps} Schritte, Unterprozess)")
            return base64.b64encode(data).decode("ascii")


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send_json(self, code, obj):
        body = json.dumps(obj).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.rstrip("/") in ("/health", "/sdapi/v1/health"):
            self._send_json(200, {"status": "ok",
                                  "model": "Qwen/Qwen-Image-2.1",
                                  "mode": "subprocess (crash-sicher)"})
        else:
            self._send_json(404, {"error": "not found"})

    def do_POST(self):
        _path = self.path.rstrip("/")
        if _path not in ("/sdapi/v1/txt2img", "/sdapi/v1/img2img"):
            self._send_json(404, {"error": "not found"})
            return
        is_edit = _path.endswith("img2img")
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
        # negative_prompt wird bei Qwen-Image ignoriert (kein CFG/rectified flow).
        try:
            width = max(256, min(2048, int(req.get("width") or 1024)))
            height = max(256, min(2048, int(req.get("height") or 1024)))
        except Exception:
            width = height = 1024
        init_b64, strength, mask_b64 = None, None, None
        if is_edit:
            inits = req.get("init_images") or []
            if not inits:
                self._send_json(400, {"error": "img2img ohne init_images"})
                return
            init_b64 = inits[0]
            mask_b64 = req.get("mask") or None
            try:
                strength = float(req.get("denoising_strength", 0.6))
            except Exception:
                strength = 0.6
            print(f"{'inpaint' if mask_b64 else 'img2img'}: \"{prompt[:70]}\" "
                  f"{width}x{height} strength={strength}")
        else:
            print(f"txt2img: \"{prompt[:70]}\" {width}x{height}")
        try:
            b64 = _run_generate(prompt, width, height,
                                init_b64=init_b64, strength=strength, mask_b64=mask_b64)
        except Exception as e:
            print(f"  FEHLER: {e}")
            self._send_json(500, {"error": str(e)})
            return
        # A1111-kompatible Antwort: das Framework liest images[0].
        self._send_json(200, {"images": [b64], "parameters": {}, "info": "qwen-image-2.1"})


def main():
    global _cfg
    ap = argparse.ArgumentParser(description="Qwen-Image-2.1 als A1111-kompatibler Bild-Server (crash-sicher)")
    ap.add_argument("--host", default="127.0.0.1", help="Adresse (Standard: 127.0.0.1)")
    ap.add_argument("--port", type=int, default=7861, help="Port (Standard: 7861 — Z-Image nutzt 7860)")
    ap.add_argument("--steps", type=int, default=50, help="Denoising-Schritte (Qwen-Image: 50)")
    ap.add_argument("--timeout", type=int, default=900, help="max. Sekunden je Bild (inkl. Modell-Laden)")
    ap.add_argument("--min-free", dest="min_free", type=float, default=18.0,
                    help="an generate.py: ab wie viel freiem VRAM ohne Offload geladen wird")
    ap.add_argument("--full-gpu", dest="full_gpu", action="store_true",
                    help="KEIN erzwungener Offload (schneller, hoher Spitzen-VRAM); Standard ist Offload = sicher")
    ap.add_argument("--keep-ollama", dest="keep_ollama", action="store_true",
                    help="geladene Ollama-Modelle NICHT vor jeder Erzeugung entladen")
    _cfg = ap.parse_args()

    if not os.path.exists(_GEN):
        print(f"FEHLER: generate.py nicht gefunden neben diesem Server ({_GEN}).")
        sys.exit(1)

    srv = ThreadingHTTPServer((_cfg.host, _cfg.port), _Handler)
    mode = "full-gpu (schnell, hoher VRAM)" if _cfg.full_gpu else "Offload (sicher, niedriger VRAM)"
    print("=== Qwen-Image-2.1 Bild-Server (crash-sicher, Unterprozess je Bild) ===")
    print(f"Modus: {mode}. Jedes Bild lädt das Modell neu (~40–60 s), dafür stabil.")
    print(f"Bereit. Höre auf http://{_cfg.host}:{_cfg.port}")
    print("Im Framework-Profil unter „🎨 Bildgenerierung“ diese Adresse eintragen.")
    print("Beenden mit Strg+C.")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nServer beendet.")


if __name__ == "__main__":
    main()
