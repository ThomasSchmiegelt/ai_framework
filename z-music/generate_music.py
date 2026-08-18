#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Musik-Generator (algorithmisch) – erzeugt aus einer Stil-/Stimmungsbeschreibung ein
kleines Musikstück als WAV-Datei. **Reine Python-Standardbibliothek** – keine
Installation, keine Abhängigkeit, kein GPU. MIT-Lizenz.

Beispiele:
    python generate_music.py "fröhliche schnelle Abenteuermelodie"
    python generate_music.py "traurig langsam" --out lied.wav
    python generate_music.py "8bit chiptune" --key C --tempo 140 --bars 16 --seed 7

Das Stück wird aus Musiktheorie zusammengebaut: Tonleiter + Akkordfolge passend zur
Stimmung, darüber eine Melodie, ein Bass und (je nach Stil) ein Schlagzeug. Jeder Lauf
klingt anders (mit --seed reproduzierbar).
"""
import argparse
import array
import math
import os
import random
import struct
import sys
import wave
from datetime import datetime

try:
    for _s in (sys.stdout, sys.stderr):
        _s.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

SR = 44100  # Abtastrate

# ── Musiktheorie ──────────────────────────────────────────────────────────────
NOTE_BASE = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11, "H": 11}

SCALES = {
    "major":      [0, 2, 4, 5, 7, 9, 11],
    "minor":      [0, 2, 3, 5, 7, 8, 10],
    "pent_major": [0, 2, 4, 7, 9],
    "pent_minor": [0, 3, 5, 7, 10],
    "blues":      [0, 3, 5, 6, 7, 10],
    "dorian":     [0, 2, 3, 5, 7, 9, 10],
    "phrygian":   [0, 1, 3, 5, 7, 8, 10],
}

# Akkordfolgen als Tonleiter-Stufen (1-basiert), je Stimmung.
PROGRESSIONS = {
    "happy":   [[1, 5, 6, 4], [1, 4, 5, 5], [1, 6, 4, 5]],
    "sad":     [[1, 6, 3, 7], [1, 4, 1, 5], [1, 7, 6, 7]],
    "epic":    [[1, 7, 6, 7], [1, 6, 3, 7], [1, 5, 6, 4]],
    "chill":   [[2, 5, 1, 1], [1, 4, 2, 5], [1, 6, 2, 5]],
    "spooky":  [[1, 2, 1, 5], [1, 6, 2, 1], [1, 7, 1, 2]],
    "chip":    [[1, 5, 6, 4], [1, 4, 5, 6], [6, 4, 1, 5]],
}


def note_freq(midi):
    return 440.0 * (2.0 ** ((midi - 69) / 12.0))


def scale_notes(root_midi, scale, octaves=2):
    """Liste von MIDI-Noten der Tonleiter über mehrere Oktaven."""
    steps = SCALES[scale]
    out = []
    for o in range(octaves + 1):
        for s in steps:
            out.append(root_midi + 12 * o + s)
    return out


def triad(root_midi, scale, degree):
    """Dreiklang auf einer Tonleiterstufe (Stufe 1-basiert)."""
    steps = SCALES[scale]
    n = len(steps)
    idx = (degree - 1) % n
    octs = (degree - 1) // n
    def deg(i):
        j = idx + i
        return root_midi + 12 * (octs + j // n) + steps[j % n]
    return [deg(0), deg(2), deg(4)]


# ── Klangerzeugung ────────────────────────────────────────────────────────────
def _wave_sample(phase, kind):
    # phase in [0,1)
    if kind == "sine":
        return math.sin(2 * math.pi * phase)
    if kind == "square":
        return 1.0 if phase < 0.5 else -1.0
    if kind == "pulse":                     # schmaler Puls = chiptune
        return 1.0 if phase < 0.25 else -1.0
    if kind == "saw":
        return 2.0 * phase - 1.0
    if kind == "triangle":
        return 4.0 * abs(phase - 0.5) - 1.0
    return math.sin(2 * math.pi * phase)


def synth_note(buf, start, freq, dur, amp, kind, sr=SR,
               a=0.01, d=0.06, s=0.7, r=0.08):
    """Eine Note mit ADSR-Hüllkurve in den Master-Puffer ``buf`` (Liste) mischen."""
    n = int(dur * sr)
    if n <= 0 or freq <= 0:
        return
    rel = int(r * sr)
    total = n + rel
    atk = max(1, int(a * sr))
    dec = max(1, int(d * sr))
    phase = 0.0
    inc = freq / sr
    for i in range(total):
        # Hüllkurve
        if i < atk:
            env = i / atk
        elif i < atk + dec:
            env = 1.0 - (1.0 - s) * ((i - atk) / dec)
        elif i < n:
            env = s
        else:
            env = s * (1.0 - (i - n) / rel)
        val = _wave_sample(phase, kind) * env * amp
        idx = start + i
        if 0 <= idx < len(buf):
            buf[idx] += val
        phase += inc
        if phase >= 1.0:
            phase -= 1.0


def synth_kick(buf, start, amp, sr=SR):
    n = int(0.18 * sr)
    for i in range(n):
        t = i / sr
        freq = 120.0 * math.exp(-t * 30) + 45.0     # Tonhöhen-Abfall
        env = math.exp(-t * 22)
        idx = start + i
        if 0 <= idx < len(buf):
            buf[idx] += math.sin(2 * math.pi * freq * t) * env * amp


def synth_snare(buf, start, amp, sr=SR):
    n = int(0.16 * sr)
    for i in range(n):
        t = i / sr
        env = math.exp(-t * 26)
        idx = start + i
        if 0 <= idx < len(buf):
            buf[idx] += (random.uniform(-1, 1) * 0.8
                         + math.sin(2 * math.pi * 180 * t) * 0.2) * env * amp


def synth_hat(buf, start, amp, sr=SR, dur=0.05):
    n = int(dur * sr)
    for i in range(n):
        env = math.exp(-(i / sr) * 60)
        idx = start + i
        if 0 <= idx < len(buf):
            buf[idx] += random.uniform(-1, 1) * env * amp


# ── Stück zusammenbauen ───────────────────────────────────────────────────────
def build_song(style, root_midi, tempo, bars, seed=None):
    rnd = random.Random(seed)
    sc = style["scale"]
    prog = rnd.choice(PROGRESSIONS[style["prog"]])
    beat = 60.0 / tempo                 # Sekunden je Viertel
    bar_len = 4 * beat
    total = int((bars * bar_len + 0.5) * SR) + SR
    buf = [0.0] * total

    lead = scale_notes(root_midi + 12, sc, 1)   # Melodie eine Oktave höher
    wave_lead = style["lead"]
    drums = style["drums"]

    prev_i = len(lead) // 2
    for b in range(bars):
        bar_start = b * bar_len
        degree = prog[b % len(prog)]
        ch = triad(root_midi, sc, degree)

        # Bass: Grundton, halbe Noten
        for k in range(2):
            synth_note(buf, int((bar_start + k * 2 * beat) * SR),
                       note_freq(ch[0] - 12), 2 * beat * 0.95, 0.32, "triangle")
        # Akkord-Fläche: ganztaktig, leise
        for c in ch:
            synth_note(buf, int(bar_start * SR), note_freq(c), bar_len * 0.98,
                       0.12, style["pad"], a=0.02, d=0.1, s=0.8, r=0.15)

        # Melodie: acht Achtel je Takt, bevorzugt Akkordtöne, schrittweise Bewegung
        chord_pitchclasses = {c % 12 for c in ch}
        for e in range(8):
            t = bar_start + e * (beat / 2)
            if rnd.random() < 0.12:            # kleine Pause
                continue
            step = rnd.choice([-2, -1, -1, 1, 1, 2, 0])
            prev_i = max(0, min(len(lead) - 1, prev_i + step))
            # auf Zählzeit gern einen Akkordton treffen
            if e % 2 == 0 and rnd.random() < 0.6:
                cands = [i for i, m in enumerate(lead) if m % 12 in chord_pitchclasses]
                if cands:
                    prev_i = min(cands, key=lambda i: abs(i - prev_i))
            dur = (beat / 2) * (1.6 if rnd.random() < 0.25 else 0.9)
            synth_note(buf, int(t * SR), note_freq(lead[prev_i]), dur, 0.30, wave_lead)

        # Schlagzeug
        if drums:
            for k in range(4):
                tb = bar_start + k * beat
                if k in (0, 2):
                    synth_kick(buf, int(tb * SR), 0.9)
                if k in (1, 3):
                    synth_snare(buf, int(tb * SR), 0.5)
                synth_hat(buf, int(tb * SR), 0.18)
                synth_hat(buf, int((tb + beat / 2) * SR), 0.14)
    return buf


def write_wav(buf, path):
    # Normalisieren auf 16-bit, weiche Begrenzung
    peak = max(1e-6, max(abs(x) for x in buf))
    scale = 0.89 / peak
    data = array.array("h", (0 for _ in range(len(buf))))
    for i, x in enumerate(buf):
        v = math.tanh(x * scale) * 32767.0      # sanfter Limiter
        data[i] = int(max(-32768, min(32767, v)))
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(data.tobytes())


# ── Stil/Stimmung aus Text ableiten ───────────────────────────────────────────
STYLE_PRESETS = {
    "happy":  {"scale": "major",      "prog": "happy",  "tempo": 128, "lead": "square",   "pad": "triangle", "drums": True},
    "sad":    {"scale": "minor",      "prog": "sad",    "tempo": 82,  "lead": "triangle", "pad": "sine",     "drums": False},
    "epic":   {"scale": "minor",      "prog": "epic",   "tempo": 140, "lead": "saw",      "pad": "saw",      "drums": True},
    "chill":  {"scale": "dorian",     "prog": "chill",  "tempo": 84,  "lead": "sine",     "pad": "triangle", "drums": True},
    "spooky": {"scale": "phrygian",   "prog": "spooky", "tempo": 92,  "lead": "square",   "pad": "saw",      "drums": False},
    "chip":   {"scale": "pent_major", "prog": "chip",   "tempo": 138, "lead": "pulse",    "pad": "square",   "drums": True},
}

KEYWORDS = [
    (("fröhlich", "froehlich", "happy", "gut gelaunt", "lustig", "abenteuer", "sonnig"), "happy"),
    (("traurig", "sad", "melancholisch", "weinen", "trauer"), "sad"),
    (("spannend", "episch", "epic", "action", "kampf", "held", "boss", "drama"), "epic"),
    (("chill", "entspannt", "ruhig", "lofi", "lo-fi", "relax", "gemütlich"), "chill"),
    (("gruselig", "spooky", "halloween", "unheimlich", "horror", "geist"), "spooky"),
    (("8bit", "8-bit", "chiptune", "chip", "retro", "gameboy", "arcade", "spiel", "game", "pixel"), "chip"),
]


def style_from_text(text):
    t = (text or "").lower()
    chosen = None
    for words, key in KEYWORDS:
        if any(w in t for w in words):
            chosen = key
            break
    if not chosen:
        chosen = "chip"   # kinderfreundlicher Standard
    preset = dict(STYLE_PRESETS[chosen])
    # Tempo-Feinjustierung
    if any(w in t for w in ("schnell", "fast", "schneller", "hektisch")):
        preset["tempo"] += 22
    if any(w in t for w in ("langsam", "slow", "gemächlich", "ruhig")):
        preset["tempo"] = max(60, preset["tempo"] - 24)
    return chosen, preset


def parse_key(text, default="C"):
    for tok in (text or "").replace(",", " ").split():
        k = tok.strip().upper().rstrip("-DUR").rstrip("-MOLL")
        if k in NOTE_BASE:
            return k
    return default


def main():
    ap = argparse.ArgumentParser(description="Algorithmischer Musik-Generator → WAV (reine Standardbibliothek)")
    ap.add_argument("beschreibung", nargs="?", default="",
                    help="Stil/Stimmung, z. B. \"fröhliche schnelle Abenteuermelodie\" oder \"8bit\"")
    ap.add_argument("--style", choices=list(STYLE_PRESETS), help="Stil direkt wählen (überschreibt die Erkennung)")
    ap.add_argument("--key", default=None, help="Grundton (C, D, E, F, G, A, H/B); Standard: aus Text oder C")
    ap.add_argument("--tempo", type=int, default=None, help="Tempo (BPM)")
    ap.add_argument("--bars", type=int, default=16, help="Anzahl Takte (Länge)")
    ap.add_argument("--seed", type=int, default=None, help="Zufalls-Seed (gleiche Zahl = gleiches Stück)")
    ap.add_argument("--out", default=None, help="Ausgabedatei (.wav)")
    ap.add_argument("--outdir", default="outputs", help="Ausgabeordner")
    args = ap.parse_args()

    name, preset = style_from_text(args.beschreibung)
    if args.style:
        name = args.style
        preset = dict(STYLE_PRESETS[args.style])
        if any(w in (args.beschreibung or "").lower() for w in ("schnell", "fast")):
            preset["tempo"] += 22
    key = (args.key or parse_key(args.beschreibung)).upper()
    if key not in NOTE_BASE:
        key = "C"
    if args.tempo:
        preset["tempo"] = max(50, min(220, args.tempo))
    bars = max(4, min(64, args.bars))
    root_midi = 48 + NOTE_BASE[key]     # um C3 herum

    print(f"🎵 Stil: {name} · Tonart: {key} · Tempo: {preset['tempo']} BPM · Takte: {bars}"
          + (f" · Seed: {args.seed}" if args.seed is not None else ""))
    print("   erzeuge Töne …")
    buf = build_song(preset, root_midi, preset["tempo"], bars, seed=args.seed)

    os.makedirs(args.outdir, exist_ok=True)
    if args.out:
        base, ext = os.path.splitext(args.out)
        path = os.path.join(args.outdir, os.path.basename(base) + (ext or ".wav"))
    else:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = os.path.join(args.outdir, f"musik_{name}_{ts}.wav")
    write_wav(buf, path)
    print(f"✓ fertig → {path}  ({len(buf)/SR:.1f}s)")


if __name__ == "__main__":
    main()
