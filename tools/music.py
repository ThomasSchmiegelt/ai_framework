"""Algorithmischer Musik-Generator (Engine) – reine Python-Standardbibliothek, MIT.

Baut aus einer Stil-/Stimmungsbeschreibung ein kleines Musikstück (Melodie, Akkorde,
Bass, Schlagzeug) aus Musiktheorie zusammen und rendert es als WAV (Bytes). Ohne
Fremdabhängigkeit, ohne GPU. Wird sowohl vom Chat-Backend (`/api/music/generate`,
Befehl ``/musik``) als auch vom eigenständigen Werkzeug ``z-music/generate_music.py``
genutzt – daher hier ohne CLI/print, nur reine Logik (analog ``tools/dokumente.py``).
"""
from __future__ import annotations

import array
import io
import math
import random
import wave
from typing import Optional

SR = 44100  # Abtastrate

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


def note_freq(midi: float) -> float:
    return 440.0 * (2.0 ** ((midi - 69) / 12.0))


def scale_notes(root_midi: int, scale: str, octaves: int = 2) -> list:
    steps = SCALES[scale]
    out = []
    for o in range(octaves + 1):
        for s in steps:
            out.append(root_midi + 12 * o + s)
    return out


def triad(root_midi: int, scale: str, degree: int) -> list:
    steps = SCALES[scale]
    n = len(steps)
    idx = (degree - 1) % n
    octs = (degree - 1) // n

    def deg(i):
        j = idx + i
        return root_midi + 12 * (octs + j // n) + steps[j % n]

    return [deg(0), deg(2), deg(4)]


def _wave_sample(phase: float, kind: str) -> float:
    if kind == "sine":
        return math.sin(2 * math.pi * phase)
    if kind == "square":
        return 1.0 if phase < 0.5 else -1.0
    if kind == "pulse":
        return 1.0 if phase < 0.25 else -1.0
    if kind == "saw":
        return 2.0 * phase - 1.0
    if kind == "triangle":
        return 4.0 * abs(phase - 0.5) - 1.0
    return math.sin(2 * math.pi * phase)


def synth_note(buf, start, freq, dur, amp, kind, sr=SR, a=0.01, d=0.06, s=0.7, r=0.08):
    n = int(dur * sr)
    if n <= 0 or freq <= 0:
        return
    rel = int(r * sr)
    total = n + rel
    atk = max(1, int(a * sr))
    dec = max(1, int(d * sr))
    phase = 0.0
    inc = freq / sr
    ln = len(buf)
    for i in range(total):
        if i < atk:
            env = i / atk
        elif i < atk + dec:
            env = 1.0 - (1.0 - s) * ((i - atk) / dec)
        elif i < n:
            env = s
        else:
            env = s * (1.0 - (i - n) / rel)
        idx = start + i
        if 0 <= idx < ln:
            buf[idx] += _wave_sample(phase, kind) * env * amp
        phase += inc
        if phase >= 1.0:
            phase -= 1.0


def synth_kick(buf, start, amp, sr=SR):
    n = int(0.18 * sr)
    ln = len(buf)
    for i in range(n):
        t = i / sr
        freq = 120.0 * math.exp(-t * 30) + 45.0
        env = math.exp(-t * 22)
        idx = start + i
        if 0 <= idx < ln:
            buf[idx] += math.sin(2 * math.pi * freq * t) * env * amp


def synth_snare(buf, start, amp, rnd, sr=SR):
    n = int(0.16 * sr)
    ln = len(buf)
    for i in range(n):
        t = i / sr
        env = math.exp(-t * 26)
        idx = start + i
        if 0 <= idx < ln:
            buf[idx] += (rnd.uniform(-1, 1) * 0.8
                         + math.sin(2 * math.pi * 180 * t) * 0.2) * env * amp


def synth_hat(buf, start, amp, rnd, sr=SR, dur=0.05):
    n = int(dur * sr)
    ln = len(buf)
    for i in range(n):
        env = math.exp(-(i / sr) * 60)
        idx = start + i
        if 0 <= idx < ln:
            buf[idx] += rnd.uniform(-1, 1) * env * amp


def build_song(style: dict, root_midi: int, tempo: int, bars: int, seed=None) -> list:
    rnd = random.Random(seed)
    sc = style["scale"]
    prog = rnd.choice(PROGRESSIONS[style["prog"]])
    beat = 60.0 / tempo
    bar_len = 4 * beat
    total = int((bars * bar_len + 0.5) * SR) + SR
    buf = [0.0] * total

    lead = scale_notes(root_midi + 12, sc, 1)
    wave_lead = style["lead"]
    drums = style["drums"]

    prev_i = len(lead) // 2
    for b in range(bars):
        bar_start = b * bar_len
        degree = prog[b % len(prog)]
        ch = triad(root_midi, sc, degree)

        for k in range(2):
            synth_note(buf, int((bar_start + k * 2 * beat) * SR),
                       note_freq(ch[0] - 12), 2 * beat * 0.95, 0.32, "triangle")
        for c in ch:
            synth_note(buf, int(bar_start * SR), note_freq(c), bar_len * 0.98,
                       0.12, style["pad"], a=0.02, d=0.1, s=0.8, r=0.15)

        chord_pcs = {c % 12 for c in ch}
        for e in range(8):
            t = bar_start + e * (beat / 2)
            if rnd.random() < 0.12:
                continue
            step = rnd.choice([-2, -1, -1, 1, 1, 2, 0])
            prev_i = max(0, min(len(lead) - 1, prev_i + step))
            if e % 2 == 0 and rnd.random() < 0.6:
                cands = [i for i, m in enumerate(lead) if m % 12 in chord_pcs]
                if cands:
                    prev_i = min(cands, key=lambda i: abs(i - prev_i))
            dur = (beat / 2) * (1.6 if rnd.random() < 0.25 else 0.9)
            synth_note(buf, int(t * SR), note_freq(lead[prev_i]), dur, 0.30, wave_lead)

        if drums:
            for k in range(4):
                tb = bar_start + k * beat
                if k in (0, 2):
                    synth_kick(buf, int(tb * SR), 0.9)
                if k in (1, 3):
                    synth_snare(buf, int(tb * SR), 0.5, rnd)
                synth_hat(buf, int(tb * SR), 0.18, rnd)
                synth_hat(buf, int((tb + beat / 2) * SR), 0.14, rnd)
    return buf


def render_wav_bytes(buf: list) -> bytes:
    peak = max(1e-6, max(abs(x) for x in buf))
    scale = 0.89 / peak
    data = array.array("h", bytes(2 * len(buf)))
    for i, x in enumerate(buf):
        v = math.tanh(x * scale) * 32767.0
        data[i] = int(max(-32768, min(32767, v)))
    bio = io.BytesIO()
    with wave.open(bio, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(data.tobytes())
    return bio.getvalue()


def style_from_text(text: str):
    t = (text or "").lower()
    chosen = None
    for words, key in KEYWORDS:
        if any(w in t for w in words):
            chosen = key
            break
    if not chosen:
        chosen = "chip"
    preset = dict(STYLE_PRESETS[chosen])
    if any(w in t for w in ("schnell", "fast", "schneller", "hektisch")):
        preset["tempo"] += 22
    if any(w in t for w in ("langsam", "slow", "gemächlich", "ruhig")):
        preset["tempo"] = max(60, preset["tempo"] - 24)
    return chosen, preset


def parse_key(text: str, default: str = "C") -> str:
    for tok in (text or "").replace(",", " ").split():
        k = tok.strip().upper().rstrip("-DUR").rstrip("-MOLL")
        if k in NOTE_BASE:
            return k
    return default


def generate(description: str = "", style: Optional[str] = None, key: Optional[str] = None,
             tempo: Optional[int] = None, bars: int = 16, seed: Optional[int] = None) -> dict:
    """Ein Stück erzeugen. Rückgabe: ``{wav: bytes, style, key, tempo, bars, seconds}``."""
    name, preset = style_from_text(description)
    if style and style in STYLE_PRESETS:
        name = style
        preset = dict(STYLE_PRESETS[style])
        if any(w in (description or "").lower() for w in ("schnell", "fast")):
            preset["tempo"] += 22
    key = (key or parse_key(description)).upper()
    if key not in NOTE_BASE:
        key = "C"
    if tempo:
        preset["tempo"] = max(50, min(220, int(tempo)))
    bars = max(4, min(64, int(bars)))
    root_midi = 48 + NOTE_BASE[key]
    buf = build_song(preset, root_midi, preset["tempo"], bars, seed=seed)
    wav = render_wav_bytes(buf)
    return {"wav": wav, "style": name, "key": key, "tempo": preset["tempo"],
            "bars": bars, "seconds": round(len(buf) / SR, 1)}
