#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Wan als lokaler Video-Server ("Bruecke") fuer das AI-Framework - CRASH-SICHER.

Damit die 🎬-Videofunktion im Framework (Tab „Videoerzeugung" + Chat /video,
Profil-Weg `local::wan`) Videos ueber das lokale Wan erzeugt. Der Server spricht
genau die eine Schnittstelle, die das Framework aufruft:

    POST /generate  {mode, prompt, first_b64?, last_b64?, frames, fps, steps, size, seed}
                 ->  {"video_b64": "<base64-mp4>"}
    GET  /health ->  {"status":"ok", ...}

Im Framework-Profil unter „🎬 Videoerzeugung" **Lokal · Wan** waehlen und als
Adresse die URL dieses Servers eintragen (Standard http://127.0.0.1:7870).

Start (Windows):  video_server.bat
Start (Linux):    ./video_server.sh

── Warum das crash-sicher ist ────────────────────────────────────────────────
Der Server-Prozess importiert selbst KEIN torch/CUDA. Jede Videoerzeugung laeuft
in einem FRISCHEN Unterprozess (generate_video.py), der genau EIN Video erzeugt
und sich dann beendet - der bewiesen stabile Weg (identisch zur Bild-Bruecke
sd_server.py). Folgen:
  • Kein VRAM/Fragmentierungs-Aufbau ueber mehrere Laeufe.
  • Ein CUDA-Fehler toetet nur den Unterprozess; der Server meldet ihn und lebt weiter.
  • generate_video.py macht selbst die VRAM-Sicherung (Ollama entladen, freien VRAM
    messen, bei Knappheit CPU-Offload) und gibt nach dem Video den VRAM wieder frei.
Preis: jedes Video laedt das Modell neu aus dem Cache. Dafuer stabil.
Nur Python-Standardbibliothek -> keine neue Abhaengigkeit.
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

# stdout/stderr auf UTF-8 zwingen (siehe sd_server.py: sonst cp1252-Crash bei
# umgeleitetem stdout / Auto-Start durch das Backend).
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

_HERE = os.path.dirname(os.path.abspath(__file__))
_GEN = os.path.join(_HERE, "generate_video.py")
_lock = threading.Lock()   # eine Erzeugung zur Zeit (eine GPU)
_cfg = None                # argparse-Namespace


def _decode(b):
    if "," in b and b.strip().startswith("data:"):
        b = b.split(",", 1)[1]
    return base64.b64decode(b)


def _run_generate(mode, prompt, negative, first_b64, last_b64,
                  frames, fps, steps, size, seed, timeout=None, memory_saver=False):
    """generate_video.py in einem frischen Unterprozess ausfuehren -> base64-mp4.
    Wirft RuntimeError bei Fehler/Timeout (der Server bleibt am Leben).
    ``timeout`` (Sekunden, aus dem Request) hat Vorrang vor dem Server-Flag; 0/None =
    KEIN Limit. ``memory_saver`` = sequenzieller CPU-Offload (minimaler VRAM, langsamer)."""
    with _lock:
        with tempfile.TemporaryDirectory() as td:
            out = os.path.join(td, "clip.mp4")
            cmd = [sys.executable, _GEN, "--mode", mode,
                   "--frames", str(int(frames)), "--fps", str(int(fps)),
                   "--steps", str(int(steps)), "--size", str(size),
                   "--out", "clip.mp4", "--outdir", td,
                   "--min-free", str(_cfg.min_free)]
            if prompt:
                cmd += ["--prompt", prompt]
            if negative:
                cmd += ["--negative", negative]
            if seed is not None:
                cmd += ["--seed", str(int(seed))]
            if _cfg.model:
                cmd += ["--model", _cfg.model]
            if first_b64:
                fp = os.path.join(td, "first.png")
                with open(fp, "wb") as _f:
                    _f.write(_decode(first_b64))
                cmd += ["--first", fp]
            if last_b64:
                lp = os.path.join(td, "last.png")
                with open(lp, "wb") as _f:
                    _f.write(_decode(last_b64))
                cmd += ["--last", lp]
            if memory_saver:
                cmd.append("--offload-seq")       # minimaler VRAM (langsamer) - "Speicher sparen"
            elif not _cfg.full_gpu:
                cmd.append("--offload")          # niedriger Spitzen-VRAM (Standard: sicher)
            if _cfg.keep_ollama:
                cmd.append("--keep-ollama")
            t0 = time.time()
            # timeout 0 / negativ = KEIN Limit (erster Lauf laedt mehrere GB Gewichte).
            # Request-Timeout (aus dem Framework-Profil) hat Vorrang vor dem Server-Flag.
            if timeout and timeout > 0:
                to = timeout
            else:
                to = _cfg.timeout if (_cfg.timeout and _cfg.timeout > 0) else None
            try:
                p = subprocess.run(cmd, capture_output=True, text=True,
                                   timeout=to, cwd=_HERE)
            except subprocess.TimeoutExpired:
                raise RuntimeError(f"Zeitueberschreitung nach {to}s "
                                   f"(Modell laedt/erzeugt zu lange)")
            if p.returncode != 0 or not os.path.exists(out):
                tail = (p.stderr or p.stdout or "").strip().splitlines()
                msg = " | ".join(t.strip() for t in tail[-4:]) or f"Exit {p.returncode}"
                raise RuntimeError("Erzeugung fehlgeschlagen: " + msg)
            with open(out, "rb") as f:
                data = f.read()
            print(f"  Video erzeugt in {time.time() - t0:.1f}s "
                  f"({mode}, {size}, {frames} Frames, Unterprozess)")
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
        if self.path.rstrip("/") == "/health":
            self._send_json(200, {"status": "ok", "engine": "wan",
                                  "mode": "subprocess (crash-sicher)"})
        else:
            self._send_json(404, {"error": "not found"})

    def do_POST(self):
        if self.path.rstrip("/") != "/generate":
            self._send_json(404, {"error": "not found"})
            return
        try:
            n = int(self.headers.get("Content-Length", 0))
            req = json.loads(self.rfile.read(n) or b"{}")
        except Exception as e:
            self._send_json(400, {"error": f"ungueltiger Request: {e}"})
            return
        mode = str(req.get("mode", "t2v") or "t2v").strip().lower()
        if mode not in ("t2v", "i2v", "flf2v"):
            mode = "t2v"
        prompt = str(req.get("prompt", "") or "").strip()
        negative = str(req.get("negative_prompt", "") or req.get("negative", "") or "")
        first_b64 = req.get("first_b64") or req.get("first") or None
        last_b64 = req.get("last_b64") or req.get("last") or None
        if mode in ("i2v", "flf2v") and not first_b64:
            self._send_json(400, {"error": f"{mode} ohne Startbild (first)"})
            return
        if mode == "flf2v" and not last_b64:
            self._send_json(400, {"error": "flf2v ohne Endbild (last)"})
            return
        if mode == "t2v" and not prompt:
            self._send_json(400, {"error": "t2v ohne prompt"})
            return
        try:
            frames = max(9, min(200, int(req.get("frames") or 81)))
            fps = max(1, min(60, int(req.get("fps") or 16)))
            steps = max(1, min(80, int(req.get("steps") or 30)))
        except Exception:
            frames, fps, steps = 81, 16, 30
        size = str(req.get("size", "720p") or "720p")
        seed = req.get("seed")
        try:
            seed = int(seed) if seed not in (None, "") else None
        except Exception:
            seed = None
        try:
            req_to = int(req.get("timeout") or 0)
        except Exception:
            req_to = 0
        mem_saver = bool(req.get("memory_saver"))
        print(f"generate: mode={mode} \"{prompt[:60]}\" size={size} frames={frames}"
              + (" [Speicher sparen]" if mem_saver else ""))
        try:
            b64 = _run_generate(mode, prompt, negative, first_b64, last_b64,
                                frames, fps, steps, size, seed,
                                timeout=req_to if req_to > 0 else None,
                                memory_saver=mem_saver)
        except Exception as e:
            print(f"  FEHLER: {e}")
            self._send_json(500, {"error": str(e)})
            return
        self._send_json(200, {"video_b64": b64, "mode": mode})


def main():
    global _cfg
    ap = argparse.ArgumentParser(description="Wan als Video-Server (crash-sicher)")
    ap.add_argument("--host", default="127.0.0.1", help="Adresse (Standard: 127.0.0.1)")
    ap.add_argument("--port", type=int, default=7870, help="Port (Standard: 7870)")
    ap.add_argument("--timeout", type=int, default=0, help="max. Sekunden je Video (0 = KEIN Limit; erster Lauf laedt mehrere GB)")
    ap.add_argument("--model", default=None, help="Wan-Modell-Repo an generate_video.py durchreichen")
    ap.add_argument("--min-free", dest="min_free", type=float, default=40.0,
                    help="an generate_video.py: ab wie viel freiem VRAM ohne Offload geladen wird")
    ap.add_argument("--full-gpu", dest="full_gpu", action="store_true",
                    help="KEIN erzwungener Offload (schneller, hoher Spitzen-VRAM); Standard ist Offload = sicher")
    ap.add_argument("--keep-ollama", dest="keep_ollama", action="store_true",
                    help="geladene Ollama-Modelle NICHT vor jeder Erzeugung entladen")
    _cfg = ap.parse_args()

    if not os.path.exists(_GEN):
        print(f"FEHLER: generate_video.py nicht gefunden neben diesem Server ({_GEN}).")
        sys.exit(1)

    srv = ThreadingHTTPServer((_cfg.host, _cfg.port), _Handler)
    mode = "full-gpu (schnell, hoher VRAM)" if _cfg.full_gpu else "Offload (sicher, niedriger VRAM)"
    print("=== Wan Video-Server (crash-sicher, Unterprozess je Video) ===")
    print(f"Modus: {mode}. Jedes Video laedt das Modell neu, dafuer stabil.")
    print(f"Bereit. Hoere auf http://{_cfg.host}:{_cfg.port}")
    print("Im Framework-Profil unter „🎬 Videoerzeugung“ diese Adresse eintragen.")
    print("Beenden mit Strg+C.")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nServer beendet.")


if __name__ == "__main__":
    main()
