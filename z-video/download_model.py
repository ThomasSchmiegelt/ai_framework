#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Wan-Modellgewichte VORAB herunterladen (resumable) - z-video.

Warum getrennt vom Erzeugen? Der erste Videolauf laedt mehrere zehn GB Gewichte
von Hugging Face. Bricht diese Uebertragung mittendrin ab, scheitert der ganze
Lauf. Dieses Skript zieht die Gewichte EINMALIG in den lokalen HF-Cache -
robust und WIEDERAUFSETZBAR: einfach erneut starten, bereits geladene Dateien
werden uebersprungen, nur der Rest wird geholt. Danach startet die eigentliche
Erzeugung (Tab/Chat/CLI) ohne weiteren Download.

Beispiele:
    python download_model.py --mode flf2v      # Erst-/Letztbild-Modell (720P)
    python download_model.py --mode i2v        # Einzelbild -> Video (720P)
    python download_model.py --mode t2v        # Text -> Video (14B)
    python download_model.py --model Wan-AI/Wan2.1-T2V-1.3B-Diffusers   # kleiner

Windows:  download_model.bat --mode flf2v
Linux:    ./download_model.sh --mode flf2v
"""
import argparse
import os
import sys
import time

# stdout/stderr auf UTF-8 (Umlaute in Meldungen, auch bei umgeleitetem stdout).
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# Xet-Backend abschalten (MUSS vor huggingface_hub-Import stehen): der neue
# Xet-Transfer wirft beim Laden grosser Repos gelegentlich
# "RuntimeError: File reconstruction error". Der klassische, resumable
# HTTPS-Download ist zuverlaessiger.
os.environ.setdefault("HF_HUB_DISABLE_XET", "1")
os.environ.setdefault("HF_HUB_ENABLE_HF_TRANSFER", "0")

# Modelle je Modus (identisch zu generate_video.py).
DEFAULT_MODELS = {
    "t2v":   "Wan-AI/Wan2.1-T2V-14B-Diffusers",
    "i2v":   "Wan-AI/Wan2.1-I2V-14B-720P-Diffusers",
    "flf2v": "Wan-AI/Wan2.1-FLF2V-14B-720P-Diffusers",
}


def _load_hf_token():
    """Optionalen HF-Token bereitstellen (beschleunigt Downloads, hebt Rate-Limits).
    Env HF_TOKEN/HUGGING_FACE_HUB_TOKEN hat Vorrang, sonst 'hf_token.txt' daneben.
    Token ist ein Geheimnis und wird NICHT ausgegeben."""
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


def main():
    ap = argparse.ArgumentParser(description="Wan-Modell vorab herunterladen (resumable)")
    ap.add_argument("--mode", choices=["t2v", "i2v", "flf2v"], default="flf2v",
                    help="Modus -> Standard-Modell (Standard: flf2v = Erst-/Letztbild)")
    ap.add_argument("--model", default=None,
                    help="anderes/kleineres Wan-Repo (ueberschreibt --mode)")
    ap.add_argument("--retries", type=int, default=20,
                    help="Wie oft bei Netzabbruch automatisch neu ansetzen (Standard: 20)")
    args = ap.parse_args()

    repo = args.model or DEFAULT_MODELS[args.mode]
    tok = _load_hf_token()

    try:
        from huggingface_hub import snapshot_download
    except Exception:
        print("FEHLER: huggingface_hub fehlt. Erst 'install_zvideo' ausfuehren "
              "(oder: pip install huggingface_hub).")
        sys.exit(1)

    print("=== Wan-Modell vorab laden (z-video) ===")
    print(f"Repo:    {repo}")
    print(f"Token:   {'ja (' + tok + ')' if tok else 'nein (oeffentlich)'}")
    print("Xet:     aus (klassischer resumable HTTPS-Download)")
    print("Hinweis: mehrere zehn GB - laeuft je nach Leitung Stunden. Bei Abbruch")
    print("         einfach dieses Skript erneut starten; Fertiges wird uebersprungen.\n")

    def _snap():
        # huggingface_hub >= 1.0 hat 'resume_download' entfernt (Downloads sind dort
        # ohnehin standardmaessig fortsetzbar). Aeltere Versionen nehmen den Parameter.
        try:
            return snapshot_download(repo_id=repo, resume_download=True)
        except TypeError:
            return snapshot_download(repo_id=repo)

    attempt = 0
    while True:
        attempt += 1
        try:
            path = _snap()
            print(f"\nFERTIG. Modell liegt im Cache:\n  {path}")
            print("Die Videoerzeugung (Tab/Chat/CLI) startet jetzt ohne Download.")
            return
        except KeyboardInterrupt:
            print("\nAbgebrochen. Erneut starten setzt fort (Fertiges bleibt erhalten).")
            sys.exit(130)
        except Exception as e:
            if attempt >= max(1, args.retries):
                print(f"\nFEHLER nach {attempt} Versuchen: {e}")
                print("Erneut starten setzt den Download fort (bereits geladene Dateien "
                      "bleiben im Cache).")
                sys.exit(1)
            wait = min(30, 3 * attempt)
            print(f"\n  Uebertragung unterbrochen ({e}). Neuer Versuch "
                  f"{attempt + 1}/{args.retries} in {wait}s ...")
            time.sleep(wait)


if __name__ == "__main__":
    main()
