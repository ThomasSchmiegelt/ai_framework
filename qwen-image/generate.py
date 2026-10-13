#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Qwen-Image (2.1) – lokale Bildgenerierung

Erzeugt Bilder komplett LOKAL auf der GPU aus einem Text-Prompt, ohne
Cloud und ohne API-Schlüssel. Nutzt die [diffusers]-Bibliothek und das
offizielle Qwen-Image-Modell von Hugging Face (Qwen/Qwen-Image-2.1).

Läuft in einer EIGENEN venv (neben diesem Skript, „venv/"), damit der
GPU-VRAM nicht mit dem Ollama/LM-Backend kollidiert.

Aufruf (Windows):   .\\bild.bat \"ein roter Sportwagen im Sonnenuntergang\"
Aufruf (Linux):     ./bild.sh \"ein roter Sportwagen im Sonnenuntergang\"
                   (bzw.  venv/bin/python generate.py \"…\"  , Optionen unten)

Installation:  .\\install_qwimage.ps1   (bzw.  ./install_qwimage.sh)

VRAM-Schutz (wichtig bei paralleler Ollama-Nutzung):
  Qwen-Image (bf16) belegt ~15–20 GB VRAM. Damit der Speicher nicht überläuft,
  entlädt dieses Skript vor dem Laden automatisch die geladenen Ollama-Modelle
  ('ollama stop') und misst danach den freien VRAM; sind weniger als --min-free
  GB frei, schaltet es selbsttätig auf CPU-Offload aus (langsamer, aber kein
  Absturz). Mit --keep-ollama Ollama-Werte behalten. Mit --offload/--offload-seq
  Offload erzwingen. Nach dem Bild wird der VRAM wieder freigeben.

Lizenz: MIT (Skript). Modell Qwen-Image-2.1: Qwen-Lizenz (Hugging Face).
[diffusers] https://github.com/huggingface/diffusers
"""
import argparse
import os
import subprocess
import sys
import time
from datetime import datetime

# Modell auf Hugging Face. Qwen-Image-2.1 = Zielversion; bei Bedarf hier (oder
# mit --modell) auf eine andere Qwen-Image-Variante umbenbar, z. B. "Qwen/Qwen-Image".
MODEL_ID = "Qwen/Qwen-Image-2.1"
# Qwen-Image-Defaults: 50 Schritte, kein CFG (guidance 0.0), native ~1328².
DEFAULT_STEPS = 50
DEFAULT_GUIDANCE = 0.0


def _load_hf_token() -> str:
    """Optionalen Hugging-Face-Token laden: erst HF_TOKEN (Env), sonst die Datei
    hf_token.txt in diesem Ordner. Rein optional – beschleunigt den Modell-
    Download und hebt Rate-Limits. Wird nirgends ausgegeben.
    Rückgabe: 'env', 'file' oder '' (leer)."""
    tk = os.environ.get("HF_TOKEN", "").strip()
    if tk:
        return "env"
    hf_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "hf_token.txt")
    if os.path.exists(hf_file):
        with open(hf_file, "r", encoding="utf-8") as f:
            if f.read().strip():
                return "file"
    return ""


def _ollama_loaded() -> list:
    """Liste der aktuell geladenen Ollama-Modelle (leer bei Fehler/das Programm
    nicht läuft oder nichts geladen ist)."""
    try:
        out = subprocess.run(["ollama", "ps"], capture_output=True, text=True, timeout=5)
    except Exception:
        return []
    names = []
    for line in out.stdout.strip().splitlines()[1:]:
        parts = line.split()
        if parts:
            names.append(parts[0])
    return names


def _free_ollama_vram():
    """Alle geladenen Ollama-Modelle entladen ('ollama stop'), um VRAM freizugeben.
    Gibt die Liste der gestoppten Modelle zurück."""
    names = _ollama_loaded()
    stopped = []
    for n in names:
        try:
            subprocess.run(["ollama", "stop", n], capture_output=True, text=True, timeout=20)
            stopped.append(n)
        except Exception:
            pass
    return stopped


# diffusers ist lazy-loaded: fehlende Klassen werfen AttributeError, nicht ImportError.
# Deshalb hasattr()-Prüfung statt try/except.
def _pick(_kandidaten):
    import diffusers  # type: ignore
    for _n in _kandidaten:
        if hasattr(diffusers, _n):
            return getattr(diffusers, _n)
    raise ImportError(
        "Keine passende QwenImage-Klasse im installierten diffusers gefunden. "
        "Für Qwen-Image-2.1: pip install git+https://github.com/huggingface/diffusers")


def _import_gen_pipeline():
    # Qwen-Image-2.1 nutzt QwenImage21Pipeline; Qwen-Image-2.0 nutzt QwenImagePipeline.
    return _pick(["QwenImage21Pipeline", "QwenImagePipeline"])


def _import_edit_pipeline():
    return _pick(["QwenImage21Img2ImgPipeline", "QwenImage21Pipeline",
                  "QwenImageImg2ImgPipeline", "QwenImagePipeline"])


def _import_inpaint_pipeline():
    return _pick(["QwenImage21InpaintPipeline", "QwenImage21Pipeline",
                  "QwenImageInpaintPipeline", "QwenImagePipeline"])


def main():
    ap = argparse.ArgumentParser(description="Qwen-Image – lokale Bildgenerierung")
    ap.add_argument("prompt", nargs="?", help="Bildbeschreibung (Prompt)")
    ap.add_argument("--prompt", dest="prompt_opt", help="Alternative zum positionalen Prompt")
    ap.add_argument("--modell", default=MODEL_ID, help="Modell auf Hugging Face (Default: %s)" % MODEL_ID)
    ap.add_argument("--negative", default="", help="Negativ-Prompt (Qwen-Image nutzt i.d.R. keinen; wird ignoriert)")
    ap.add_argument("--steps", type=int, default=DEFAULT_STEPS, help="Anzahl Schritte (Qwen: ~50)")
    ap.add_argument("--guidance", type=float, default=DEFAULT_GUIDANCE, help="Guidance-Scale (Qwen: 0.0)")
    ap.add_argument("--width", type=int, default=1024, help="Breite in Pixeln (gerades Vielfaches von 32, z. B. 1024)")
    ap.add_argument("--height", type=int, default=1024, help="Höhe in Pixeln (gerades Vielfaches von 32)")
    ap.add_argument("--seed", type=int, default=None, help="Zufalls-Seed (gleiche Zahl = gleiches Bild)")
    ap.add_argument("--batch", type=int, default=1, help="Wie viele Bilder erzeugen")
    ap.add_argument("--out", default=None, help="Ausgabedatei (bei --batch>1 wird eine Nummer angehängt)")
    ap.add_argument("--outdir", default="outputs", help="Ausgabeordner (Standard: outputs/)")
    ap.add_argument("--offload", action="store_true",
                    help="CPU-Offload erzwingen: spart VRAM (Komponenten nur bei Bedarf auf der GPU)")
    ap.add_argument("--offload-seq", dest="offload_seq", action="store_true",
                    help="Sequenzieller CPU-Offload: minimaler VRAM, am langsamsten")
    ap.add_argument("--min-free", dest="min_free", type=float, default=18.0,
                    help="Ab wie viel freiem VRAM (GB) OHNE Offload geladen wird; sonst automatisch Offload")
    ap.add_argument("--keep-ollama", dest="keep_ollama", action="store_true",
                    help="Geladene Ollama-Modelle NICHT automatisch entladen")
    ap.add_argument("--init", default=None,
                    help="Eingabebild für Bildbearbeitung (img2img); ohne --init = Text→Bild")
    ap.add_argument("--strength", type=float, default=0.6,
                    help="img2img: wie stark verändert wird (0.1 wenig … 0.95 stark)")
    ap.add_argument("--mask", default=None,
                    help="Maske fürs Inpainting (WEISS = Bereich ändern); nur mit --init")
    args = ap.parse_args()

    prompt = args.prompt_opt or args.prompt
    if not prompt:
        print("Kein Prompt angegeben. Beispiel:\n  python generate.py \"ein roter Sportwagen im Sonnenuntergang\"")
        sys.exit(1)

    # Optionalen HF-Token laden (vor dem ersten Modell-Download wirksam).
    if _load_hf_token() == "file":
        print("Hugging-Face-Token aus hf_token.txt geladen (schnellere Downloads).")

    # Import erst hier, damit --help ohne geladene Bibliotheken funktioniert.
    import torch

    edit_mode = bool(args.init)
    inpaint_mode = bool(args.mask) and edit_mode
    init_img = mask_img = None
    if inpaint_mode:
        _Pipe = _import_inpaint_pipeline()
        from PIL import Image as _PILImage
        init_img = _PILImage.open(args.init).convert("RGB").resize((args.width, args.height))
        mask_img = _PILImage.open(args.mask).convert("L").resize((args.width, args.height))
    elif edit_mode:
        _Pipe = _import_edit_pipeline()
        from PIL import Image as _PILImage
        init_img = _PILImage.open(args.init).convert("RGB").resize((args.width, args.height))
    else:
        _Pipe = _import_gen_pipeline()

    if not torch.cuda.is_available():
        print("WARNUNG: Keine CUDA-GPU gefunden – die Generierung läuft dann sehr langsam auf der CPU.")
        device, dtype = "cpu", torch.float32
    else:
        device, dtype = "cuda", torch.bfloat16
        print(f"GPU: {torch.cuda.get_device_name(0)}")

    # --- VRAM-Schutz -------------------------------------------------------
    offload = args.offload
    if device == "cuda":
        if not args.keep_ollama:
            stopped = _free_ollama_vram()
            if stopped:
                print("Ollama-Modelle entladen (VRAM freigegeben): " + ", ".join(stopped))
                time.sleep(1.5)
        free_b, total_b = torch.cuda.mem_get_info()
        free_gb, total_gb = free_b / 1e9, total_b / 1e9
        print(f"VRAM frei: {free_gb:.1f} GB von {total_gb:.1f} GB")
        if not offload and not args.offload_seq and free_gb < args.min_free:
            print(f"  ! Weniger als {args.min_free:.0f} GB frei – vermutlich ist noch ein anderes")
            print("    Modell (z.B. Ollama) geladen. Schalte CPU-Offload ein, damit der VRAM nicht")
            print("    überläuft (langsamer). Für volle Geschwindigkeit vorher mit 'ollama stop <modell>'.")
            offload = True

    _mode_txt = ' (Inpainting)' if inpaint_mode else (' (Bildbearbeitung/img2img)' if edit_mode else '')
    print(f"Lade Modell {args.modell} …{_mode_txt}"
          " (erster Start lädt ~15–20 GB herunter, danach schnell)")
    t0 = time.time()
    pipe = _Pipe.from_pretrained(args.modell, torch_dtype=dtype)
    if device == "cpu":
        pipe.to("cpu")
    elif args.offload_seq:
        pipe.enable_sequential_cpu_offload()
        print("CPU-Offload: sequenziell (minimaler VRAM)")
    elif offload:
        pipe.enable_model_cpu_offload()
        print("CPU-Offload: modellweise")
    else:
        pipe.to(device)
    for _m in ("enable_vae_slicing", "enable_vae_tiling"):
        try:
            getattr(pipe, _m)()
        except Exception:
            pass
    print(f"Modell geladen in {time.time() - t0:.1f} s.")

    os.makedirs(args.outdir, exist_ok=True)
    gen = None
    if args.seed is not None:
        gen = torch.Generator(device).manual_seed(args.seed)

    # --- Pipeline-Parameter (2.0 & 2.1 signature-aware) -------------------
    import inspect as _inspect
    try:
        _params = set(_inspect.signature(type(pipe).__call__).parameters.keys())
    except Exception:
        _params = None  # Signatur unbekannt -> alle Felder versuchen

    for i in range(args.batch):
        g = gen
        if args.seed is not None and args.batch > 1:
            g = torch.Generator(device).manual_seed(args.seed + i)

        t1 = time.time()

        _kw = dict(
            height=args.height, width=args.width,
            num_inference_steps=max(1, args.steps),
        )
        # Guidance: 2.0=guidance_scale, 2.1=true_cfg_scale (1.0 = kein CFG ~ guidance 0.0)
        _t = 1.0 if args.guidance in (0.0, 0, None) else args.guidance
        if _params is None:
            _kw["guidance_scale"] = args.guidance
            _kw["true_cfg_scale"] = _t
        elif "guidance_scale" in _params:
            _kw["guidance_scale"] = args.guidance
        elif "true_cfg_scale" in _params:
            _kw["true_cfg_scale"] = _t
        if _params is None or "generator" in _params:
            _kw["generator"] = g
        if edit_mode and (_params is None or "image" in _params):
            _kw["image"] = init_img
            if _params is None or "strength" in _params:
                _kw["strength"] = max(0.1, min(args.strength, 0.95))
        if inpaint_mode and (_params is None or "mask_image" in _params):
            _kw["mask_image"] = mask_img

        if _params is not None:
            _kw = {k: v for k, v in _kw.items() if k in _params}

        try:
            image = pipe(prompt, **_kw).images[0]
        except TypeError:
            minimal = {k: v for k, v in _kw.items()
                       if k in ("height", "width", "num_inference_steps", "generator")}
            if _params is not None:
                minimal = {k: v for k, v in minimal.items() if k in _params}
            image = pipe(prompt, **minimal).images[0]
        dt = time.time() - t1

        if args.out:
            base, ext = os.path.splitext(args.out)
            ext = ext or ".png"
            name = f"{base}{ext}" if args.batch == 1 else f"{base}_{i + 1}{ext}"
        else:
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            name = f"qwenimage_{ts}_{i + 1}.png"
        path = os.path.join(args.outdir, os.path.basename(name))
        image.save(path)
        print(f"[{i + 1}/{args.batch}] {dt:.1f}s  ->  {path}")

    print("Fertig.")


if __name__ == "__main__":
    main()
