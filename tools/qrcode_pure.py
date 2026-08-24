"""Reiner-Python-QR-Code-Encoder (MIT, nur Standardbibliothek).

Erzeugt QR-Codes **ohne jede Fremd-Abhängigkeit** – passend zur Projektphilosophie
(vgl. ``tools/pst_pure.py``, ``tools/music.py``). Unterstützt **Byte-Modus** (UTF-8),
Fehlerkorrektur-Level L/M, Versionen 1–10 (genug für WLAN-URLs). Ausgabe als **SVG**
(skaliert verlustfrei, im Browser sofort darstellbar) – kein Pillow nötig.

Referenz: ISO/IEC 18004. Die versionsabhängigen Tabellen (Kapazitäten, Blockstruktur,
Alignment-Positionen, Format-/Versionsinfo) sind fest hinterlegt, damit keine BCH-/
Kapazitätsrechnung fehlschlagen kann.

CLI:  python -m tools.qrcode_pure "http://192.168.0.10:8780" > code.svg
"""
from __future__ import annotations
from typing import List

# ── Galois-Feld GF(256) für Reed-Solomon ─────────────────────────────────────
_EXP = [0] * 512
_LOG = [0] * 256
_x = 1
for _i in range(255):
    _EXP[_i] = _x
    _LOG[_x] = _i
    _x <<= 1
    if _x & 0x100:
        _x ^= 0x11D
for _i in range(255, 512):
    _EXP[_i] = _EXP[_i - 255]


def _gf_mul(a: int, b: int) -> int:
    if a == 0 or b == 0:
        return 0
    return _EXP[_LOG[a] + _LOG[b]]


def _rs_generator(degree: int) -> List[int]:
    # Divisor-Polynom (Nayuki-Referenz): Produkt (x - α^i), i=0..degree-1.
    result = [0] * (degree - 1) + [1]
    root = 1
    for _ in range(degree):
        for j in range(degree):
            result[j] = _gf_mul(result[j], root)
            if j + 1 < degree:
                result[j] ^= result[j + 1]
        root = _gf_mul(root, 2)
    return result


def _rs_encode(data: List[int], ecc_len: int) -> List[int]:
    gen = _rs_generator(ecc_len)
    res = [0] * ecc_len
    for d in data:
        factor = d ^ res[0]
        res = res[1:] + [0]
        for i in range(ecc_len):
            res[i] ^= _gf_mul(gen[i], factor)
    return res


# ── Versionsabhängige Tabellen (Level L und M, Versionen 1–10) ───────────────
# Blockstruktur: (ecc_per_block, [(anz_bloecke, data_cw_je_block), ...])
_BLOCKS = {
    ("L", 1): (7,  [(1, 19)]),
    ("M", 1): (10, [(1, 16)]),
    ("L", 2): (10, [(1, 34)]),
    ("M", 2): (16, [(1, 28)]),
    ("L", 3): (15, [(1, 55)]),
    ("M", 3): (26, [(1, 44)]),
    ("L", 4): (20, [(1, 80)]),
    ("M", 4): (18, [(2, 32)]),
    ("L", 5): (26, [(1, 108)]),
    ("M", 5): (24, [(2, 43)]),
    ("L", 6): (18, [(2, 68)]),
    ("M", 6): (16, [(4, 27)]),
    ("L", 7): (20, [(2, 78)]),
    ("M", 7): (18, [(4, 31)]),
    ("L", 8): (24, [(2, 97)]),
    ("M", 8): (22, [(2, 38), (2, 39)]),
    ("L", 9): (30, [(2, 116)]),
    ("M", 9): (22, [(3, 36), (2, 37)]),
    ("L", 10): (18, [(2, 68), (2, 69)]),
    ("M", 10): (26, [(4, 43), (1, 44)]),
}

# Alignment-Pattern-Zentren je Version (leere Liste = keine, ab v2).
_ALIGN = {
    1: [], 2: [6, 18], 3: [6, 22], 4: [6, 26], 5: [6, 30],
    6: [6, 34], 7: [6, 22, 38], 8: [6, 24, 42], 9: [6, 26, 46], 10: [6, 28, 50],
}

# Format-Info (15 Bit) je (Level, Maske), fertig maskiert (ISO 18004, Tabelle C.1).
_FORMAT_BITS = {
    ("L", 0): 0x77C4, ("L", 1): 0x72F3, ("L", 2): 0x7DAA, ("L", 3): 0x789D,
    ("L", 4): 0x662F, ("L", 5): 0x6318, ("L", 6): 0x6C41, ("L", 7): 0x6976,
    ("M", 0): 0x5412, ("M", 1): 0x5125, ("M", 2): 0x5E7C, ("M", 3): 0x5B4B,
    ("M", 4): 0x45F9, ("M", 5): 0x40CE, ("M", 6): 0x4F97, ("M", 7): 0x4AA0,
}

# Versions-Info (18 Bit) für v7–v10 (ISO 18004, Tabelle D.1).
_VERSION_BITS = {7: 0x07C94, 8: 0x085BC, 9: 0x09A99, 10: 0x0A4D3}

# Zeichen-Zählindikator-Länge (Byte-Modus): 8 Bit für v1–9, 16 Bit ab v10.


def _total_data_codewords(level: str, version: int) -> int:
    ecc, groups = _BLOCKS[(level, version)]
    return sum(n * d for n, d in groups)


def _capacity_bits(level: str, version: int) -> int:
    return _total_data_codewords(level, version) * 8


def _pick_version(payload: bytes, level: str) -> int:
    for v in range(1, 11):
        cc_bits = 8 if v <= 9 else 16
        needed = 4 + cc_bits + len(payload) * 8
        if needed <= _capacity_bits(level, v):
            return v
    raise ValueError("Daten zu lang für QR v1–10 (max. ~270 Byte).")


# ── Bit-Puffer ───────────────────────────────────────────────────────────────
class _Bits:
    def __init__(self):
        self.data: List[int] = []

    def put(self, value: int, length: int):
        for i in range(length - 1, -1, -1):
            self.data.append((value >> i) & 1)

    def __len__(self):
        return len(self.data)


def _encode_data(payload: bytes, level: str, version: int) -> List[int]:
    cap_bits = _capacity_bits(level, version)
    cc_bits = 8 if version <= 9 else 16
    b = _Bits()
    b.put(0b0100, 4)                 # Byte-Modus
    b.put(len(payload), cc_bits)     # Zeichenzahl
    for byte in payload:
        b.put(byte, 8)
    # Terminator + Auffüllen auf Codewort-Grenze
    rem = cap_bits - len(b)
    b.put(0, min(4, max(0, rem)))
    while len(b) % 8 != 0:
        b.data.append(0)
    codewords = [int("".join(map(str, b.data[i:i + 8])), 2) for i in range(0, len(b), 8)]
    total = _total_data_codewords(level, version)
    pad = [0xEC, 0x11]
    i = 0
    while len(codewords) < total:
        codewords.append(pad[i % 2]); i += 1
    return codewords


def _interleave(codewords: List[int], level: str, version: int) -> List[int]:
    ecc_len, groups = _BLOCKS[(level, version)]
    blocks = []          # (data_cw, ecc_cw)
    pos = 0
    for n, dcw in groups:
        for _ in range(n):
            data = codewords[pos:pos + dcw]; pos += dcw
            blocks.append((data, _rs_encode(data, ecc_len)))
    result: List[int] = []
    max_data = max(len(d) for d, _ in blocks)
    for i in range(max_data):
        for d, _ in blocks:
            if i < len(d):
                result.append(d[i])
    for i in range(ecc_len):
        for _, e in blocks:
            result.append(e[i])
    return result


# ── Modul-Matrix ─────────────────────────────────────────────────────────────
class _Matrix:
    def __init__(self, size: int):
        self.size = size
        self.mods = [[None] * size for _ in range(size)]   # None = leer
        self.reserved = [[False] * size for _ in range(size)]

    def set(self, r, c, val, reserve=True):
        self.mods[r][c] = 1 if val else 0
        if reserve:
            self.reserved[r][c] = True


def _place_finder(m: _Matrix, r0: int, c0: int):
    for dr in range(-1, 8):
        for dc in range(-1, 8):
            r, c = r0 + dr, c0 + dc
            if not (0 <= r < m.size and 0 <= c < m.size):
                continue
            inring = (0 <= dr <= 6 and 0 <= dc <= 6)
            if inring:
                on = (dr in (0, 6) or dc in (0, 6) or (2 <= dr <= 4 and 2 <= dc <= 4))
            else:
                on = False   # Separator
            m.set(r, c, on)


def _place_alignment(m: _Matrix, version: int):
    centers = _ALIGN.get(version, [])
    for r in centers:
        for c in centers:
            # Nicht über Finder-Patterns
            if (r < 8 and c < 8) or (r < 8 and c > m.size - 9) or (r > m.size - 9 and c < 8):
                continue
            for dr in range(-2, 3):
                for dc in range(-2, 3):
                    on = max(abs(dr), abs(dc)) != 1
                    m.set(r + dr, c + dc, on)


def _place_timing(m: _Matrix):
    for i in range(8, m.size - 8):
        val = (i % 2 == 0)
        if m.mods[6][i] is None:
            m.set(6, i, val)
        if m.mods[i][6] is None:
            m.set(i, 6, val)


def _reserve_format(m: _Matrix, version: int):
    n = m.size
    for i in range(9):
        if i != 6:
            m.reserved[8][i] = True
            m.reserved[i][8] = True
    for i in range(8):
        m.reserved[8][n - 1 - i] = True
        m.reserved[n - 1 - i][8] = True
    m.set(n - 8, 8, 1)   # Dark module
    if version >= 7:
        for i in range(6):
            for j in range(3):
                m.reserved[n - 11 + j][i] = True
                m.reserved[i][n - 11 + j] = True


def _place_data(m: _Matrix, bits: List[int]):
    n = m.size
    idx = 0
    col = n - 1
    upward = True
    while col > 0:
        if col == 6:
            col -= 1
        rng = range(n - 1, -1, -1) if upward else range(n)
        for r in rng:
            for c in (col, col - 1):
                if m.mods[r][c] is None and not m.reserved[r][c]:
                    bit = bits[idx] if idx < len(bits) else 0
                    m.mods[r][c] = bit
                    idx += 1
        upward = not upward
        col -= 2


def _mask_fn(mask: int, r: int, c: int) -> bool:
    if mask == 0: return (r + c) % 2 == 0
    if mask == 1: return r % 2 == 0
    if mask == 2: return c % 3 == 0
    if mask == 3: return (r + c) % 3 == 0
    if mask == 4: return (r // 2 + c // 3) % 2 == 0
    if mask == 5: return (r * c) % 2 + (r * c) % 3 == 0
    if mask == 6: return ((r * c) % 2 + (r * c) % 3) % 2 == 0
    return ((r + c) % 2 + (r * c) % 3) % 2 == 0


def _apply_mask(m: _Matrix, mask: int) -> _Matrix:
    out = _Matrix(m.size)
    out.mods = [row[:] for row in m.mods]
    out.reserved = [row[:] for row in m.reserved]
    for r in range(m.size):
        for c in range(m.size):
            if not m.reserved[r][c] and m.mods[r][c] is not None:
                if _mask_fn(mask, r, c):
                    out.mods[r][c] ^= 1
    return out


def _place_format(m: _Matrix, level: str, mask: int):
    bits = _FORMAT_BITS[(level, mask)]
    n = m.size
    seq = [(bits >> i) & 1 for i in range(14, -1, -1)]   # MSB..LSB (15 Bit)
    # Um das obere-linke Finder
    coords1 = [(8, 0), (8, 1), (8, 2), (8, 3), (8, 4), (8, 5), (8, 7), (8, 8),
               (7, 8), (5, 8), (4, 8), (3, 8), (2, 8), (1, 8), (0, 8)]
    for bit, (r, c) in zip(seq, coords1):
        m.mods[r][c] = bit; m.reserved[r][c] = True
    # Gespiegelt an den anderen beiden Findern
    coords2 = [(n - 1, 8), (n - 2, 8), (n - 3, 8), (n - 4, 8), (n - 5, 8), (n - 6, 8), (n - 7, 8),
               (8, n - 8), (8, n - 7), (8, n - 6), (8, n - 5), (8, n - 4), (8, n - 3), (8, n - 2), (8, n - 1)]
    for bit, (r, c) in zip(seq, coords2):
        m.mods[r][c] = bit; m.reserved[r][c] = True


def _place_version(m: _Matrix, version: int):
    if version < 7:
        return
    bits = _VERSION_BITS[version]
    n = m.size
    seq = [(bits >> i) & 1 for i in range(17, -1, -1)]
    k = 0
    for i in range(6):
        for j in range(3):
            bit = seq[17 - (i * 3 + j)]
            m.mods[n - 11 + j][i] = bit; m.reserved[n - 11 + j][i] = True
            m.mods[i][n - 11 + j] = bit; m.reserved[i][n - 11 + j] = True
            k += 1


def _penalty(m: _Matrix) -> int:
    n = m.size
    g = m.mods
    score = 0
    # Regel 1: ≥5 gleiche in Reihe/Spalte
    for line in (g, list(zip(*g))):
        for row in line:
            run = 1
            for i in range(1, n):
                if row[i] == row[i - 1]:
                    run += 1
                else:
                    if run >= 5: score += 3 + (run - 5)
                    run = 1
            if run >= 5: score += 3 + (run - 5)
    # Regel 2: 2x2-Blöcke
    for r in range(n - 1):
        for c in range(n - 1):
            if g[r][c] == g[r][c + 1] == g[r + 1][c] == g[r + 1][c + 1]:
                score += 3
    # Regel 3: Finder-ähnliches Muster 1:1:3:1:1
    pat1 = [1, 0, 1, 1, 1, 0, 1, 0, 0, 0, 0]
    pat2 = [0, 0, 0, 0, 1, 0, 1, 1, 1, 0, 1]
    for line in (g, list(zip(*g))):
        for row in line:
            row = list(row)
            for i in range(n - 11 + 1):
                seg = row[i:i + 11]
                if seg == pat1 or seg == pat2:
                    score += 40
    # Regel 4: Anteil dunkler Module
    dark = sum(sum(1 for v in row if v) for row in g)
    ratio = dark * 100 // (n * n)
    score += (abs(ratio - 50) // 5) * 10
    return score


def make_matrix(text: str, level: str = "M") -> List[List[int]]:
    """QR-Modul-Matrix (Liste von 0/1-Zeilen) für ``text`` (UTF-8, Byte-Modus)."""
    level = level.upper()
    if level not in ("L", "M"):
        level = "M"
    payload = text.encode("utf-8")
    version = _pick_version(payload, level)
    codewords = _encode_data(payload, level, version)
    final_bits_cw = _interleave(codewords, level, version)
    bits: List[int] = []
    for cw in final_bits_cw:
        for i in range(7, -1, -1):
            bits.append((cw >> i) & 1)

    size = 17 + 4 * version
    base = _Matrix(size)
    _place_finder(base, 0, 0)
    _place_finder(base, 0, size - 7)
    _place_finder(base, size - 7, 0)
    _place_alignment(base, version)
    _place_timing(base)
    _reserve_format(base, version)
    _place_version(base, version)
    _place_data(base, bits)

    best, best_score = None, None
    for mask in range(8):
        cand = _apply_mask(base, mask)
        _place_format(cand, level, mask)
        s = _penalty(cand)
        if best_score is None or s < best_score:
            best, best_score = cand, s
    return [[1 if best.mods[r][c] else 0 for c in range(size)] for r in range(size)]


def to_svg(text: str, level: str = "M", scale: int = 8, border: int = 4,
           dark: str = "#0b243f", light: str = "#ffffff") -> str:
    """QR-Code als eigenständiges SVG (String)."""
    mat = make_matrix(text, level)
    n = len(mat)
    dim = (n + 2 * border) * scale
    rects = []
    for r in range(n):
        for c in range(n):
            if mat[r][c]:
                x = (c + border) * scale
                y = (r + border) * scale
                rects.append(f'<rect x="{x}" y="{y}" width="{scale}" height="{scale}"/>')
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{dim}" height="{dim}" '
        f'viewBox="0 0 {dim} {dim}" shape-rendering="crispEdges">'
        f'<rect width="{dim}" height="{dim}" fill="{light}"/>'
        f'<g fill="{dark}">{"".join(rects)}</g></svg>'
    )


if __name__ == "__main__":
    import sys
    txt = sys.argv[1] if len(sys.argv) > 1 else "https://example.com"
    sys.stdout.write(to_svg(txt))
