#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Wan – lokale Videogenerierung (Kommandozeile).

Drei Modi:
    --mode flf2v   Erst- + Letztbild -> Video (das Modell interpoliert dazwischen)
    --mode i2v     Ein Startbild -> Video (Einzelbild wird animiert)
    --mode t2v     Nur Text -> Video

Beispiele:
    python generate_video.py --mode flf2v --first a.png --last b.png \
        --prompt "sanfte Kamerafahrt" --out clip.mp4
    python generate_video.py --mode i2v --first foto.png --prompt "leichter Wind" --out clip.mp4
    python generate_video.py --mode t2v --prompt "ein roter Sportwagen faehrt durch die Wueste" --out clip.mp4

Modell: Wan 2.1 (Alibaba, Apache-2.0). Beim ersten Lauf werden die Gewichte
(mehrere GB, das 14B-Modell ~30-70 GB) von Hugging Face in den lokalen Cache
geladen; danach startet die Generierung ohne erneuten Download.

VRAM: Vor dem Laden werden geladene Ollama-Modelle automatisch entladen
('ollama stop') und der freie VRAM geprueft; bei Knappheit schaltet das Skript
selbsttaetig auf CPU-Offload, statt in einen Out-of-Memory-Absturz zu laufen.

WICHTIG: Die genaue Pipeline-Klasse und die Modell-Tags haengen von der
installierten diffusers-Version ab (Wan-Unterstuetzung kommt aus dem Quellcode).
Die Standard-Modell-IDs unten sind die gaengigen Diffusers-Repos; ueber --model
laesst sich jederzeit ein anderes/kleineres Wan-Repo waehlen (z. B. das 1.3B-T2V
fuer schwaechere GPUs).
"""
import argparse
import os
import subprocess
import sys
import time
from datetime import datetime

# stdout/stderr auf UTF-8 (falls von einem Elternprozess umgeleitet - sonst cp1252 ->
# UnicodeEncodeError bei Umlauten/Sonderzeichen in den Fortschrittsmeldungen).
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# Hugging-Face Xet-Backend abschalten (MUSS vor dem Import von huggingface_hub/diffusers
# gesetzt sein): der neue Xet-Transfer wirft beim Laden grosser Repos gelegentlich
# "RuntimeError: File reconstruction error" ab. Der klassische, resumable HTTPS-Download
# ist zuverlaessiger. Ueberschreibbar, falls der Nutzer die Variable selbst setzt.
os.environ.setdefault("HF_HUB_DISABLE_XET", "1")
os.environ.setdefault("HF_HUB_ENABLE_HF_TRANSFER", "0")

# Standard-Modelle je Modus (Diffusers-Format). Ueber --model ueberschreibbar.
DEFAULT_MODELS = {
    "t2v":   "Wan-AI/Wan2.1-T2V-14B-Diffusers",
    "i2v":   "Wan-AI/Wan2.1-I2V-14B-720P-Diffusers",
    "flf2v": "Wan-AI/Wan2.1-FLF2V-14B-720P-Diffusers",
}

# Aufloesungs-Presets (Breite x Hoehe). Wan erwartet durch die VAE teilbare Masse.
SIZES = {
    "480p": (832, 480),
    "720p": (1280, 720),
    "square": (624, 624),
}


def _load_hf_token():
    """Optionalen Hugging-Face-Token bereitstellen (beschleunigt Downloads, hebt
    Rate-Limits an). Vorrang hat eine gesetzte Umgebungsvariable
    (HF_TOKEN / HUGGING_FACE_HUB_TOKEN); sonst wird eine lokale Datei
    'hf_token.txt' NEBEN diesem Skript gelesen (per .gitignore nicht im Repo).
    Der Token ist ein Geheimnis und wird NICHT ausgegeben. Rueckgabe: 'env',
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
    nicht laeuft oder nichts geladen ist)."""
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
    """Alle geladenen Ollama-Modelle entladen ('ollama stop'), um VRAM freizugeben."""
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
    ap = argparse.ArgumentParser(description="Wan - lokale Videogenerierung")
    ap.add_argument("--mode", choices=["t2v", "i2v", "flf2v"], default="t2v",
                    help="t2v = Text->Video, i2v = Startbild->Video, flf2v = Erst-+Letztbild->Video")
    ap.add_argument("--prompt", default="", help="Textbeschreibung (Prompt)")
    ap.add_argument("--negative", default="", help="Negativ-Prompt (was NICHT erscheinen soll)")
    ap.add_argument("--first", default=None, help="Startbild (i2v/flf2v)")
    ap.add_argument("--last", default=None, help="Endbild (nur flf2v)")
    ap.add_argument("--frames", type=int, default=81, help="Anzahl Frames (Wan: 81 ~ 5 s bei 16 fps)")
    ap.add_argument("--fps", type=int, default=16, help="Bilder pro Sekunde des Ausgabe-mp4")
    ap.add_argument("--steps", type=int, default=30, help="Denoising-Schritte (mehr = besser/langsamer)")
    ap.add_argument("--guidance", type=float, default=5.0, help="Guidance-Scale")
    ap.add_argument("--size", default="720p", help="Aufloesung: 480p / 720p / square oder BxH (z. B. 1280x720)")
    ap.add_argument("--seed", type=int, default=None, help="Zufalls-Seed (gleiche Zahl = gleiches Video)")
    ap.add_argument("--model", default=None, help="Wan-Modell-Repo (Standard je nach --mode)")
    ap.add_argument("--out", default=None, help="Ausgabedatei (.mp4)")
    ap.add_argument("--outdir", default="outputs", help="Ausgabeordner (Standard: outputs/)")
    ap.add_argument("--offload", action="store_true",
                    help="CPU-Offload erzwingen: spart VRAM (Komponenten nur bei Bedarf auf der GPU)")
    ap.add_argument("--offload-seq", dest="offload_seq", action="store_true",
                    help="Sequenzieller CPU-Offload: minimaler VRAM, am langsamsten")
    ap.add_argument("--min-free", dest="min_free", type=float, default=40.0,
                    help="Ab wie viel freiem VRAM (GB) OHNE Offload geladen wird; sonst automatisch Offload")
    ap.add_argument("--keep-ollama", dest="keep_ollama", action="store_true",
                    help="Geladene Ollama-Modelle NICHT automatisch entladen")
    args = ap.parse_args()

    prompt = (args.prompt or "").strip()
    if args.mode == "t2v" and not prompt:
        print("Fuer --mode t2v wird ein --prompt benoetigt.")
        sys.exit(1)
    if args.mode in ("i2v", "flf2v") and not args.first:
        print(f"Fuer --mode {args.mode} wird --first (Startbild) benoetigt.")
        sys.exit(1)
    if args.mode == "flf2v" and not args.last:
        print("Fuer --mode flf2v wird --last (Endbild) benoetigt.")
        sys.exit(1)

    # Groesse aufloesen (Preset oder BxH).
    if args.size in SIZES:
        width, height = SIZES[args.size]
    else:
        try:
            width, height = (int(x) for x in args.size.lower().split("x", 1))
        except Exception:
            width, height = SIZES["720p"]

    model_id = args.model or DEFAULT_MODELS[args.mode]

    if _load_hf_token() == "file":
        print("Hugging-Face-Token aus hf_token.txt geladen (schnellere Downloads).")

    # Import erst hier, damit --help ohne geladene Bibliotheken funktioniert.
    import torch
    from diffusers.utils import export_to_video

    if not torch.cuda.is_available():
        print("WARNUNG: Keine CUDA-GPU gefunden - die Generierung laeuft dann sehr langsam auf der CPU.")
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
            print(f"  ! Weniger als {args.min_free:.0f} GB frei - schalte CPU-Offload ein (langsamer),")
            print("    damit der VRAM nicht ueberlaeuft. Fuer volle Geschwindigkeit vorher VRAM freigeben.")
            offload = True

    # Bild(er) laden (i2v/flf2v). WICHTIG: seitenverhaeltnis-erhaltend zuschneiden
    # (Cover-Crop), NICHT verzerren. Das Framework schickt ohnehin bereits passend
    # zugeschnittene Bilder; dieser Cover-Crop ist die Absicherung fuer die CLI und
    # abweichende Seitenverhaeltnisse.
    first_img = last_img = None
    if args.mode in ("i2v", "flf2v"):
        from PIL import Image as _PILImage

        def _fit_cover(img, w, h):
            iw, ih = img.size
            if iw == w and ih == h:
                return img
            scale = max(w / iw, h / ih)
            nw, nh = max(1, round(iw * scale)), max(1, round(ih * scale))
            img = img.resize((nw, nh), _PILImage.LANCZOS)
            left, top = (nw - w) // 2, (nh - h) // 2
            return img.crop((left, top, left + w, top + h))

        first_img = _fit_cover(_PILImage.open(args.first).convert("RGB"), width, height)
        if args.mode == "flf2v":
            last_img = _fit_cover(_PILImage.open(args.last).convert("RGB"), width, height)

    # Pipeline je Modus. Wan laedt die VAE bewusst in float32 (Stabilitaet).
    # Die konkrete Klasse haengt von der installierten diffusers-Version ab.
    from diffusers import AutoencoderKLWan
    if args.mode == "t2v":
        from diffusers import WanPipeline as _Pipe
    else:
        # i2v UND flf2v laufen ueber die Image-to-Video-Pipeline; flf2v gibt
        # zusaetzlich ein last_image mit (First-Last-Frame-Konditionierung).
        from diffusers import WanImageToVideoPipeline as _Pipe

    _mode_txt = {"t2v": "Text->Video", "i2v": "Bild->Video",
                 "flf2v": "Erst-+Letztbild->Video"}[args.mode]
    print(f"Lade Modell {model_id} ... ({_mode_txt}; erster Start laedt die Gewichte herunter)")
    t0 = time.time()
    vae = AutoencoderKLWan.from_pretrained(model_id, subfolder="vae", torch_dtype=torch.float32)
    pipe = _Pipe.from_pretrained(model_id, vae=vae, torch_dtype=dtype)

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
    # Speicher-Spitzen senken (jeweils best-effort, je nach Pipeline-Version vorhanden):
    # VAE-Slicing/-Tiling zerstueckelt den Decode, Attention-Slicing die Attention-Matrix.
    for _m in ("enable_vae_slicing", "enable_vae_tiling", "enable_attention_slicing"):
        try:
            getattr(pipe, _m)()
        except Exception:
            pass
    print(f"Modell geladen in {time.time() - t0:.1f} s.")

    gen = None
    if args.seed is not None:
        gen = torch.Generator(device if device == "cuda" else "cpu").manual_seed(args.seed)

    kw = dict(
        prompt=prompt or None,
        negative_prompt=args.negative or None,
        height=height, width=width,
        num_frames=max(9, int(args.frames)),
        num_inference_steps=max(1, int(args.steps)),
        guidance_scale=args.guidance,
        generator=gen,
    )
    if args.mode in ("i2v", "flf2v"):
        kw["image"] = first_img
    if args.mode == "flf2v":
        kw["last_image"] = last_img   # First-Last-Frame-Konditionierung

    print("Erzeuge Video ...")
    t1 = time.time()
    result = pipe(**kw)
    frames = result.frames[0]
    dt = time.time() - t1

    os.makedirs(args.outdir, exist_ok=True)
    if args.out:
        name = os.path.basename(args.out)
        if not name.lower().endswith(".mp4"):
            name += ".mp4"
    else:
        name = f"wan_{datetime.now().strftime('%Y%m%d_%H%M%S')}.mp4"
    path = os.path.join(args.outdir, name)
    export_to_video(frames, path, fps=max(1, int(args.fps)))
    print(f"[{args.mode}] {dt:.1f}s  ->  {path}  ({width}x{height}, {args.frames} Frames @ {args.fps} fps)")
    print("Fertig.")


if __name__ == "__main__":
    main()
