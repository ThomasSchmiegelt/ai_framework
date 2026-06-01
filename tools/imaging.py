"""Bild-Hilfsfunktionen für die bebilderte Präsentation.

- Heuristik, ob ein Dateiname beschreibend ist (vs. Kamera-/Screenshot-Default).
- Verkleinern von Bildern vor der Vision-Analyse (schneller, VRAM-schonend,
  kleinerer Kontext).
"""

import base64
import io
import re

# Typische nicht-beschreibende Dateinamen (Kameras, Screenshots, Scanner …)
_CAMERA_PATTERNS = [
    r"^img[_\- ]?\d+$",
    r"^dsc[nf]?[_\- ]?\d+$",
    r"^p\d{6,}$",
    r"^dcim\d*$",
    r"^pxl[_\- ]?\d+",
    r"^gopr\d+$",
    r"^mvimg[_\- ]?\d+",
    r"^capture[_\- ]?\d*$",
    r"^screenshot.*$",
    r"^bildschirmfoto.*$",
    r"^scan[_\- ]?\d*$",
    r"^image[_\- ]?\d+$",
    r"^foto[_\- ]?\d*$",
    r"^bild[_\- ]?\d*$",
    r"^untitled[_\- ]?\d*$",
    r"^unbenannt[_\- ]?\d*$",
    r"^\d+$",            # reine Zahl
    r"^[0-9a-f]{8,}$",  # Hash-artig
]

_CAMERA_RE = [re.compile(p) for p in _CAMERA_PATTERNS]


def is_descriptive_filename(filename: str) -> tuple[bool, str]:
    """Prüft, ob ein Dateiname beschreibend ist.

    Rückgabe: (ist_beschreibend, lesbares_label). Das Label ist der bereinigte
    Name (Trennzeichen → Leerzeichen), unabhängig vom Ergebnis nutzbar als Hinweis.
    """
    stem = re.sub(r"\.[A-Za-z0-9]+$", "", filename).strip()
    norm = stem.lower()

    # Lesbares Label: Trenner zu Leerzeichen, Mehrfachleerzeichen zusammenfassen
    label = re.sub(r"[_\-]+", " ", stem)
    label = re.sub(r"\s+", " ", label).strip()

    if len(norm) < 4:
        return False, label
    if any(rx.match(norm) for rx in _CAMERA_RE):
        return False, label
    # Mindestens ein "echtes" Wort mit >= 3 Buchstaben
    if not re.search(r"[A-Za-zÄÖÜäöüß]{3,}", stem):
        return False, label

    return True, label


def downscale(image_b64: str, max_side: int = 1024) -> str:
    """Verkleinert ein Base64-Bild auf max_side (längste Kante) und gibt
    Base64-JPEG (ohne data-URL-Präfix) zurück. Bei Fehler wird die Eingabe
    (ohne Präfix) unverändert zurückgegeben."""
    # data-URL-Präfix entfernen, falls vorhanden
    if image_b64.startswith("data:"):
        image_b64 = image_b64.split(",", 1)[-1]

    try:
        from PIL import Image  # type: ignore

        raw = base64.b64decode(image_b64)
        img = Image.open(io.BytesIO(raw))
        if img.mode not in ("RGB", "L"):
            img = img.convert("RGB")
        img.thumbnail((max_side, max_side))
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=85)
        return base64.b64encode(buf.getvalue()).decode()
    except Exception:
        return image_b64
