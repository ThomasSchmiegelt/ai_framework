#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Musik-Generator (Kommandozeile) – erzeugt aus einer Stil-/Stimmungsbeschreibung ein
kleines Musikstück als WAV. **Reine Python-Standardbibliothek** – keine Installation,
keine Abhängigkeit, kein GPU. MIT.

Die eigentliche Logik liegt in ``tools/music.py`` (dieselbe Engine nutzt der Chat-Befehl
``/musik``). Dieses Skript ist nur die Kommandozeile drumherum.

Beispiele:
    python generate_music.py "fröhliche schnelle Abenteuermelodie"
    python generate_music.py "traurig langsam" --out lied.wav
    python generate_music.py "8bit chiptune" --key C --tempo 140 --bars 16 --seed 7
"""
import argparse
import os
import sys
from datetime import datetime

# tools/music.py aus dem Repo-Wurzelordner (eine Ebene über z-music/) importierbar machen.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tools import music as _music   # noqa: E402

try:
    for _s in (sys.stdout, sys.stderr):
        _s.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


def main():
    ap = argparse.ArgumentParser(description="Algorithmischer Musik-Generator → WAV (reine Standardbibliothek)")
    ap.add_argument("beschreibung", nargs="?", default="",
                    help="Stil/Stimmung, z. B. \"fröhliche schnelle Abenteuermelodie\" oder \"8bit\"")
    ap.add_argument("--style", choices=list(_music.STYLE_PRESETS),
                    help="Stil direkt wählen (überschreibt die Erkennung)")
    ap.add_argument("--key", default=None, help="Grundton (C D E F G A H/B); Standard: aus Text oder C")
    ap.add_argument("--tempo", type=int, default=None, help="Tempo (BPM)")
    ap.add_argument("--bars", type=int, default=16, help="Anzahl Takte (Länge)")
    ap.add_argument("--seed", type=int, default=None, help="Zufalls-Seed (gleiche Zahl = gleiches Stück)")
    ap.add_argument("--out", default=None, help="Ausgabedatei (.wav)")
    ap.add_argument("--outdir", default="outputs", help="Ausgabeordner")
    args = ap.parse_args()

    res = _music.generate(args.beschreibung, style=args.style, key=args.key,
                          tempo=args.tempo, bars=args.bars, seed=args.seed)
    print(f"🎵 Stil: {res['style']} · Tonart: {res['key']} · Tempo: {res['tempo']} BPM · "
          f"Takte: {res['bars']}" + (f" · Seed: {args.seed}" if args.seed is not None else ""))

    os.makedirs(args.outdir, exist_ok=True)
    if args.out:
        base, ext = os.path.splitext(args.out)
        path = os.path.join(args.outdir, os.path.basename(base) + (ext or ".wav"))
    else:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = os.path.join(args.outdir, f"musik_{res['style']}_{ts}.wav")
    with open(path, "wb") as f:
        f.write(res["wav"])
    print(f"✓ fertig → {path}  ({res['seconds']}s)")


if __name__ == "__main__":
    main()
