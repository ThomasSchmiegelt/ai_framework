#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Z-Image-Turbo – lokale Bildgenerierung (Kommandozeile).

Beispiele:
    python generate.py "ein roter Sportwagen im Sonnenuntergang, Fotorealismus"
    python generate.py "Portrait einer Katze" --steps 8 --seed 42 --out katze.png
    python generate.py "Landschaft" --width 1280 --height 768 --batch 4

Turbo-Standard: 8 Schritte, guidance 0.0 (laut Modell-Empfehlung). Das erste Mal
werden ~20 GB Modellgewichte von Hugging Face in den lokalen Cache geladen; danach
startet die Generierung ohne erneuten Download.

VRAM: Vor dem Laden werden geladene Ollama-Modelle automatisch entladen
('ollama stop') und der freie VRAM geprüft; bei Knappheit schaltet das Skript
selbsttätig auf CPU-Offload, statt in einen Out-of-Memory-Absturz zu laufen.
"""
import argparse
import os
import subprocess
import sys
import time
from datetime import datetime

MODEL_ID = "Tongyi-MAI/Z-Image-Turbo"


def _load_hf_token():
    """Optionalen Hugging-Face-Token bereitstellen (beschleunigt Downloads, hebt
    Rate-Limits an). Vorrang hat eine bereits gesetzte Umgebungsvariable
    (HF_TOKEN / HUGGING_FACE_HUB_TOKEN); sonst wird eine lokale Datei
    'hf_token.txt' NEBEN diesem Skript gelesen (per .gitignore nicht im Repo).
    Der Token ist ein Geheimnis und wird NICHT ausgegeben. Rückgabe: 'env',
    'file' oder None."""
    if os.environ.get("HF_TOKEN") or os.environ.get("HUGGING_FACE_HUB_TOKEN"):
        return "env"
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "hf_token.txt")
    try:
        with open(path, "r", encoding="utf-8") as f:
            tok = f.readline().strip()
    except Exception:
        return None
    if tok:
        os.environ["HF_TOKEN"] = tok
        return "file"
    return None


def _ollama_loaded():
    """Namen der aktuell in den VRAM geladenen Ollama-Modelle (leer, wenn Ollama
    nicht läuft oder nichts geladen ist)."""
    try:
        out = subprocess.run(["ollama", "ps"], capture_output=True, text=True, timeout=5)
    except Exception:
        return []   # Ollama nicht installiert / nicht auf dem PATH / nicht erreichbar
    names = []
    for line in out.stdout.strip().splitlines()[1:]:   # Kopfzeile überspringen
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


def main():
    ap = argparse.ArgumentParser(description="Z-Image-Turbo – lokale Bildgenerierung")
    ap.add_argument("prompt", nargs="?", help="Bildbeschreibung (Prompt)")
    ap.add_argument("--prompt", dest="prompt_opt", help="Alternative zum positionalen Prompt")
    ap.add_argument("--negative", default="", help="Negativ-Prompt (was NICHT erscheinen soll)")
    ap.add_argument("--steps", type=int, default=8, help="Anzahl Schritte (Turbo: 8, mehr = langsamer)")
    ap.add_argument("--guidance", type=float, default=0.0, help="Guidance-Scale (Turbo: 0.0)")
    ap.add_argument("--width", type=int, default=1024, help="Breite in Pixeln")
    ap.add_argument("--height", type=int, default=1024, help="Höhe in Pixeln")
    ap.add_argument("--seed", type=int, default=None, help="Zufalls-Seed (gleiche Zahl = gleiches Bild)")
    ap.add_argument("--batch", type=int, default=1, help="Wie viele Bilder erzeugen")
    ap.add_argument("--out", default=None, help="Ausgabedatei (bei --batch>1 wird eine Nummer angehängt)")
    ap.add_argument("--outdir", default="outputs", help="Ausgabeordner (Standard: outputs/)")
    ap.add_argument("--offload", action="store_true",
                    help="CPU-Offload erzwingen: spart VRAM (Komponenten nur bei Bedarf auf der GPU), etwas langsamer")
    ap.add_argument("--offload-seq", dest="offload_seq", action="store_true",
                    help="Sequenzieller CPU-Offload: minimaler VRAM (~2-3 GB), am langsamsten")
    ap.add_argument("--min-free", dest="min_free", type=float, default=18.0,
                    help="Ab wie viel freiem VRAM (GB) OHNE Offload geladen wird; sonst automatisch Offload")
    ap.add_argument("--keep-ollama", dest="keep_ollama", action="store_true",
                    help="Geladene Ollama-Modelle NICHT automatisch entladen (Standard: entladen, um VRAM freizugeben)")
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
    from diffusers import ZImagePipeline

    if not torch.cuda.is_available():
        print("WARNUNG: Keine CUDA-GPU gefunden – die Generierung läuft dann sehr langsam auf der CPU.")
        device, dtype = "cpu", torch.float32
    else:
        device, dtype = "cuda", torch.bfloat16
        print(f"GPU: {torch.cuda.get_device_name(0)}")

    # --- VRAM-Schutz -------------------------------------------------------
    # Z-Image-Turbo (bf16) belegt ~16 GB. Ist parallel z.B. ein Ollama-Modell
    # geladen, kann der 24-GB-VRAM überlaufen. Vor dem Laden freien Speicher
    # prüfen und bei Knappheit automatisch auf CPU-Offload ausweichen (langsamer,
    # aber kein Absturz). Voll nutzbar bleibt die GPU, wenn genug frei ist.
    offload = args.offload
    if device == "cuda":
        # Zuerst automatisch geladene Ollama-Modelle entladen, damit sie keinen
        # VRAM blockieren (außer der Nutzer will sie ausdrücklich behalten).
        if not args.keep_ollama:
            stopped = _free_ollama_vram()
            if stopped:
                print("Ollama-Modelle entladen (VRAM freigegeben): " + ", ".join(stopped))
                time.sleep(1.5)   # kurz warten, bis der Treiber den Speicher freigibt

        free_b, total_b = torch.cuda.mem_get_info()
        free_gb, total_gb = free_b / 1e9, total_b / 1e9
        print(f"VRAM frei: {free_gb:.1f} GB von {total_gb:.1f} GB")
        if not offload and not args.offload_seq and free_gb < args.min_free:
            print(f"  ! Weniger als {args.min_free:.0f} GB frei – vermutlich ist noch ein anderes")
            print("    Modell (z.B. Ollama) geladen. Schalte CPU-Offload ein, damit der VRAM")
            print("    nicht überläuft (langsamer). Für volle Geschwindigkeit vorher mit")
            print("    'ollama stop <modell>' VRAM freigeben und erneut starten.")
            offload = True

    print(f"Lade Modell {MODEL_ID} … (erster Start lädt ~20 GB herunter, danach schnell)")
    t0 = time.time()
    pipe = ZImagePipeline.from_pretrained(MODEL_ID, dtype=dtype)
    if device == "cpu":
        pipe.to("cpu")
    elif args.offload_seq:
        pipe.enable_sequential_cpu_offload()   # minimaler VRAM, am langsamsten
        print("CPU-Offload: sequenziell (minimaler VRAM)")
    elif offload:
        pipe.enable_model_cpu_offload()        # Komponenten nur bei Bedarf auf der GPU
        print("CPU-Offload: modellweise")
    else:
        pipe.to(device)
    # Speicherspar-Optionen des VAE (falls verfügbar) senken die Spitzenlast.
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

    for i in range(args.batch):
        # Bei mehreren Bildern mit gesetztem Seed jedes Bild um i verschieben.
        g = gen
        if args.seed is not None and args.batch > 1:
            g = torch.Generator(device).manual_seed(args.seed + i)

        t1 = time.time()
        image = pipe(
            prompt,
            negative_prompt=args.negative or None,
            height=args.height,
            width=args.width,
            num_inference_steps=args.steps,
            guidance_scale=args.guidance,
            generator=g,
        ).images[0]
        dt = time.time() - t1

        if args.out:
            base, ext = os.path.splitext(args.out)
            ext = ext or ".png"
            name = f"{base}{ext}" if args.batch == 1 else f"{base}_{i + 1}{ext}"
        else:
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            name = f"zimage_{ts}_{i + 1}.png"
        path = os.path.join(args.outdir, os.path.basename(name))
        image.save(path)
        print(f"[{i + 1}/{args.batch}] {dt:.1f}s  ->  {path}")

    print("Fertig.")


if __name__ == "__main__":
    main()
