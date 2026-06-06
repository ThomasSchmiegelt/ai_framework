"""
Anonymisierung von Personendaten in Dateiinhalten — für den Verzeichnis-Analyse-Tab.

Reine Python-Stdlib (regex), keine zusätzliche Abhängigkeit, MIT-kompatibel.
Schwärzt **Inhalte** (nicht Datei-/Ordnernamen): E-Mail-Adressen, Telefonnummern,
IBAN, URLs und — heuristisch — Personennamen. Ersetzungen erfolgen durch stabile
Platzhalter (``[EMAIL_1]``, ``[TEL_1]`` …) mit einem **konsistenten Mapping je
Scan-Sitzung**: derselbe Originalwert bekommt immer denselben Platzhalter.

Die heuristische Namenserkennung (Anrede + Titel) ist bewusst konservativ
(deterministisch, wenig Fehlalarme). Schwer fassbare Namen kann ein optionaler
LLM-NER-Pass im Backend ergänzen (:func:`redact_names`) — das ist „best effort",
kleine lokale Modelle erkennen nicht jeden Namen.

Das Mapping ist die Klartext→Platzhalter-Tabelle. Es bleibt **lokal** und wird
NICHT in die geschriebene Index-/„init"-Datei aufgenommen.
"""
from __future__ import annotations

import re
from typing import Dict, List, Optional, Tuple

# E-Mail-Adressen
_EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b")

# IBAN (DE u.a.): 2 Buchstaben + 2 Ziffern + bis 30 alphanumerische Zeichen,
# optional in 4er-Gruppen mit Leerzeichen.
_IBAN_RE = re.compile(r"\b[A-Z]{2}\d{2}(?:[ ]?[A-Z0-9]{4}){2,8}[ ]?[A-Z0-9]{0,4}\b")

# URLs (http/https/www)
_URL_RE = re.compile(r"\b(?:https?://|www\.)[^\s<>()\"']+", re.IGNORECASE)

# Telefonnummern: optional +/00, Klammern, Trenner; mindestens 6 Ziffern gesamt.
_PHONE_RE = re.compile(
    r"(?<![\w.])(?:\+|00)?[\(\)\d][\d\s/().\-]{5,}\d(?![\w])")

# Anrede/Titel + 1–3 großgeschriebene Namens-Tokens (deterministisch, konservativ).
_TITLE = (r"(?:Herr|Frau|Hr\.?|Fr\.?|Dr\.(?:\s*med\.|\s*rer\.\s*nat\.|\s*-Ing\.)?"
          r"|Prof\.(?:\s*Dr\.)?|Dipl\.-?Ing\.|Mag\.|B\.\s*Sc\.|M\.\s*Sc\.)")
_NAME_TOKEN = r"[A-ZÄÖÜ][a-zäöüß]+(?:-[A-ZÄÖÜ][a-zäöüß]+)?"
_TITLED_NAME_RE = re.compile(
    rf"\b{_TITLE}\s+(?:{_NAME_TOKEN}\s+){{0,2}}{_NAME_TOKEN}")


def _placeholder(category: str, value: str, mapping: Dict[str, str]) -> str:
    """Liefert (und merkt sich) einen stabilen Platzhalter für ``value``."""
    if value in mapping:
        return mapping[value]
    n = 1 + sum(1 for v in mapping.values() if v.startswith(f"[{category}_"))
    ph = f"[{category}_{n}]"
    mapping[value] = ph
    return ph


def _sub(pattern: re.Pattern, category: str, text: str,
         mapping: Dict[str, str]) -> str:
    def repl(m: re.Match) -> str:
        return _placeholder(category, m.group(0).strip(), mapping)
    return pattern.sub(repl, text)


def redact_pii(text: str,
               mapping: Optional[Dict[str, str]] = None,
               names: bool = True) -> Tuple[str, Dict[str, str]]:
    """Schwärzt Personendaten in ``text`` und gibt ``(clean_text, mapping)`` zurück.

    Reihenfolge ist wichtig: URLs vor E-Mail/Telefon (URLs können Ziffern
    enthalten), IBAN vor Telefon. ``mapping`` kann von einem vorherigen Aufruf
    übergeben werden, damit Platzhalter über mehrere Dateien hinweg konsistent
    bleiben.
    """
    if mapping is None:
        mapping = {}
    if not text:
        return text or "", mapping
    out = text
    out = _sub(_URL_RE, "URL", out, mapping)
    out = _sub(_EMAIL_RE, "EMAIL", out, mapping)
    out = _sub(_IBAN_RE, "IBAN", out, mapping)
    out = _sub(_PHONE_RE, "TEL", out, mapping)
    if names:
        out = _sub(_TITLED_NAME_RE, "PERSON", out, mapping)
    return out, mapping


def redact_names(text: str, found_names: List[str],
                 mapping: Optional[Dict[str, str]] = None) -> Tuple[str, Dict[str, str]]:
    """Schwärzt eine Liste konkreter Namen (z. B. aus einem LLM-NER-Pass).

    Wendet jeden Namen als Wortgrenzen-Treffer an; längere Namen zuerst, damit
    Vollnamen vor Teilnamen ersetzt werden. ``best effort``.
    """
    if mapping is None:
        mapping = {}
    if not text or not found_names:
        return text or "", mapping
    out = text
    for name in sorted({n.strip() for n in found_names if n and n.strip()},
                       key=len, reverse=True):
        if len(name) < 3:
            continue
        ph = _placeholder("PERSON", name, mapping)
        out = re.sub(rf"\b{re.escape(name)}\b", ph, out)
    return out, mapping
