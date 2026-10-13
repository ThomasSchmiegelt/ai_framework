# -*- coding: utf-8 -*-
"""Maschinenbau-Rechenkerne — „Papier und Bleistift" in Code.

Reine Logik, nur Standardbibliothek (``math``), MIT. Importiert bewusst **kein**
FastAPI/DB und nichts aus ``core`` (Projektregel für ``tools/*``).

Grundsatz wie bei Rechnungen und AHP: **gerechnet wird hier, nie im LLM.** Jede
Funktion liefert einen formatierten Textblock mit Annahmen, Formel, Zahlenwerten
und Auslastung zurück (Muster ``tools/engineering.bolt_calculator``), damit das
Ergebnis im Chat nachprüfbar ist und nicht geraten wirkt.

**Einheiten durchgehend:** Kräfte N, Längen mm, Spannungen MPa (= N/mm²),
Momente Nm (Eingabe) bzw. Nmm (intern), Drehzahlen 1/min. Jede Ausgabe nennt
ihre Einheiten mit.

Schrauben sind absichtlich **nicht** hier: dafür gibt es die vollständige
VDI-2230-Rechnung in ``tools/engineering.bolt_calculator``.

Einstiegspunkt für den Werkzeug-Loop: ``calc(kind, params)`` + ``catalog()``.
CLI: ``python -m tools.mechanik <kind> schluessel=wert …``
"""

from __future__ import annotations

import difflib
import math
import re
from typing import Any

# Werkstoff-Grundwerte (Stahl, falls nichts anderes angegeben)
E_STAHL = 210000.0   # Elastizitätsmodul [MPa]
G_STAHL = 81000.0    # Schubmodul [MPa]


# ──────────────────────────────────────────────────────────────────────────────
# Formatierung
# ──────────────────────────────────────────────────────────────────────────────

def _n(x: float, digits: int = 2) -> str:
    """Zahl lesbar runden (ohne nachgestellte Nullen-Flut)."""
    if x is None:
        return "—"
    if isinstance(x, (int,)) or float(x).is_integer():
        return f"{int(x)}"
    a = abs(float(x))
    if a >= 10000 or (a < 0.01 and a > 0):
        return f"{float(x):.3g}"
    return f"{float(x):.{digits}f}".rstrip("0").rstrip(".")


def _block(titel: str, annahmen: list[str], formeln: list[str],
           ergebnis: list[str], bewertung: list[str] | None = None,
           hinweis: str | None = None) -> str:
    """Einheitlicher Ausgabeblock: Annahmen → Formel → Ergebnis → Bewertung."""
    out = [f"**{titel}**", ""]
    if annahmen:
        out.append("_Annahmen / Eingaben:_")
        out += [f"- {a}" for a in annahmen]
        out.append("")
    if formeln:
        out.append("_Rechenweg:_")
        out += [f"- {f}" for f in formeln]
        out.append("")
    out.append("_Ergebnis:_")
    out += [f"- **{e}**" for e in ergebnis]
    if bewertung:
        out.append("")
        out.append("_Bewertung:_")
        out += [f"- {b}" for b in bewertung]
    if hinweis:
        out.append("")
        out.append(f"⚠️ {hinweis}")
    return "\n".join(out)


def _auslastung(ist: float, zulaessig: float, groesse: str = "Spannung") -> list[str]:
    """Sicherheit + Auslastung + Ampel als Bewertungszeilen."""
    if not zulaessig or zulaessig <= 0:
        return [f"Kein Grenzwert angegeben — {groesse} nur informativ."]
    s = zulaessig / ist if ist else float("inf")
    pct = (ist / zulaessig) * 100 if zulaessig else 0.0
    ampel = "✅ tragfähig" if pct <= 100 else "❌ überschritten"
    if 90 < pct <= 100:
        ampel = "⚠️ knapp (>90 % Auslastung)"
    return [
        f"Auslastung {_n(pct, 1)} % der zulässigen {groesse}",
        f"Sicherheit S = {_n(s)} (bezogen auf den angegebenen Grenzwert)",
        ampel,
    ]


def _req(params: dict, *keys: str) -> list[float]:
    """Pflichtwerte holen; fehlende/ungültige sammeln und als Fehler melden."""
    fehlt, falsch, werte = [], [], []
    for k in keys:
        if k not in params or params[k] is None or params[k] == "":
            fehlt.append(k)
            werte.append(0.0)
            continue
        try:
            werte.append(float(params[k]))
        except (TypeError, ValueError):
            falsch.append(k)
            werte.append(0.0)
    if fehlt or falsch:
        teile = []
        if fehlt:
            teile.append("fehlende Werte: " + ", ".join(fehlt))
        if falsch:
            teile.append("keine Zahl: " + ", ".join(falsch))
        raise ValueError("; ".join(teile))
    return werte


def _opt(params: dict, key: str, default: float) -> float:
    """Optionalen Zahlenwert holen (leer/ungültig → Standardwert)."""
    v = params.get(key)
    if v is None or v == "":
        return float(default)
    try:
        return float(v)
    except (TypeError, ValueError):
        return float(default)


def _txt(params: dict, key: str, default: str) -> str:
    """Textwert holen. Gürtel und Hosenträger: kommt doch eine Zahl an (2.0),
    wird sie zu '2' gekürzt, damit Schlüssel wie der Eulerfall treffen."""
    v = params.get(key)
    if v in (None, ""):
        return default
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v).strip().lower()


# ──────────────────────────────────────────────────────────────────────────────
# A1 — Querschnittswerte (Grundlage für Biegung, Torsion, Knickung)
# ──────────────────────────────────────────────────────────────────────────────

#: Profilname → benötigte Maße (in mm)
PROFILE: dict[str, tuple[str, ...]] = {
    "rechteck":   ("b", "h"),                 # b = Breite, h = Höhe (Biegerichtung)
    "quadrat":    ("a",),
    "kreis":      ("d",),
    "rohr":       ("d", "d_i"),               # Außen-/Innendurchmesser
    "hohlkasten": ("b", "h", "t"),            # Wanddicke t, umlaufend
    "i_traeger":  ("h", "b", "t_s", "t_f"),   # Steg t_s, Flansch t_f
}


def match_profil(name: str) -> str:
    """Profilnamen tolerant zuordnen — kleine Modelle vertippen sich.

    Gemessen: granite4.2:3b schrieb „i_trafer" statt „i_traeger". Ein Tippfehler
    soll keine Berechnung verhindern, eine echte Falscheingabe aber schon.
    """
    n = str(name or "").strip().lower().replace("-", "_").replace(" ", "_")
    if n in PROFILE:
        return n
    # gaengige Schreibweisen
    direkt = {
        "i": "i_traeger", "i_profil": "i_traeger", "ipe": "i_traeger",
        "doppel_t": "i_traeger", "traeger": "i_traeger",
        "rund": "kreis", "vollkreis": "kreis", "kreisquerschnitt": "kreis",
        "voll": "kreis", "zylinder": "kreis",
        "hohlprofil": "rohr", "rohrquerschnitt": "rohr", "hohlzylinder": "rohr",
        "kasten": "hohlkasten", "hohlrechteck": "hohlkasten",
        "rechteckig": "rechteck", "flach": "rechteck",
        "quadratisch": "quadrat",
    }
    if n in direkt:
        return direkt[n]
    treffer = difflib.get_close_matches(n, list(PROFILE), n=1, cutoff=0.7)
    return treffer[0] if treffer else n


def section_properties(profil: str, dims: dict) -> dict:
    """Querschnittswerte eines Profils.

    Rückgabe (mm-basiert): ``a`` Fläche, ``iy``/``iz`` Flächenträgheitsmomente,
    ``wy``/``wz`` Widerstandsmomente, ``iy_r``/``iz_r`` Trägheitsradien,
    ``it``/``wt`` Torsionswerte (nur für Kreis/Rohr exakt).

    y ist die „starke" Achse: Biegung um y beansprucht die Höhe h.
    """
    p = match_profil(profil)
    if p not in PROFILE:
        raise ValueError(
            f"Profil '{profil}' unbekannt. Verfügbar: " + ", ".join(sorted(PROFILE))
        )
    need = PROFILE[p]
    vals = _req(dims, *need)
    v = dict(zip(need, vals))
    it = wt = None

    if p == "quadrat":
        a_ = v["a"]
        area = a_ * a_
        iy = iz = a_ ** 4 / 12.0
        wy = wz = a_ ** 3 / 6.0
    elif p == "rechteck":
        b, h = v["b"], v["h"]
        area = b * h
        iy, wy = b * h ** 3 / 12.0, b * h ** 2 / 6.0
        iz, wz = h * b ** 3 / 12.0, h * b ** 2 / 6.0
    elif p == "kreis":
        d = v["d"]
        area = math.pi / 4.0 * d ** 2
        iy = iz = math.pi * d ** 4 / 64.0
        wy = wz = math.pi * d ** 3 / 32.0
        it, wt = math.pi * d ** 4 / 32.0, math.pi * d ** 3 / 16.0
    elif p == "rohr":
        d, di = v["d"], v["d_i"]
        if di >= d:
            raise ValueError("d_i muss kleiner als d sein")
        area = math.pi / 4.0 * (d ** 2 - di ** 2)
        iy = iz = math.pi * (d ** 4 - di ** 4) / 64.0
        wy = wz = math.pi * (d ** 4 - di ** 4) / (32.0 * d)
        it, wt = math.pi * (d ** 4 - di ** 4) / 32.0, math.pi * (d ** 4 - di ** 4) / (16.0 * d)
    elif p == "hohlkasten":
        b, h, t = v["b"], v["h"], v["t"]
        if 2 * t >= min(b, h):
            raise ValueError("Wanddicke t zu groß für b/h")
        bi, hi = b - 2 * t, h - 2 * t
        area = b * h - bi * hi
        iy = (b * h ** 3 - bi * hi ** 3) / 12.0
        iz = (h * b ** 3 - hi * bi ** 3) / 12.0
        wy, wz = iy / (h / 2.0), iz / (b / 2.0)
    else:  # i_traeger
        h, b, ts, tf = v["h"], v["b"], v["t_s"], v["t_f"]
        if 2 * tf >= h:
            raise ValueError("2·t_f muss kleiner als h sein")
        hs = h - 2 * tf                     # Steghöhe
        area = 2 * b * tf + hs * ts
        iy = (b * h ** 3 - (b - ts) * hs ** 3) / 12.0
        iz = (2 * tf * b ** 3 + hs * ts ** 3) / 12.0
        wy, wz = iy / (h / 2.0), iz / (b / 2.0)

    return {
        "profil": p, "dims": v, "a": area,
        "iy": iy, "iz": iz, "wy": wy, "wz": wz,
        "iy_r": math.sqrt(iy / area), "iz_r": math.sqrt(iz / area),
        "it": it, "wt": wt,
    }


def _profil_text(sp: dict) -> str:
    dims = ", ".join(f"{k} = {_n(v)} mm" for k, v in sp["dims"].items())
    return f"Profil {sp['profil']} ({dims})"


def querschnitt(params: dict) -> str:
    """Querschnittswerte eines Profils ausgeben."""
    sp = section_properties(_txt(params, "profil", ""), params)
    erg = [
        f"Fläche A = {_n(sp['a'])} mm²",
        f"I_y = {_n(sp['iy'])} mm⁴ · W_y = {_n(sp['wy'])} mm³ · i_y = {_n(sp['iy_r'])} mm",
        f"I_z = {_n(sp['iz'])} mm⁴ · W_z = {_n(sp['wz'])} mm³ · i_z = {_n(sp['iz_r'])} mm",
    ]
    if sp["it"]:
        erg.append(f"I_t = {_n(sp['it'])} mm⁴ · W_t = {_n(sp['wt'])} mm³ (Torsion)")
    return _block(
        "Querschnittswerte", [_profil_text(sp)],
        ["y = starke Achse (Biegung beansprucht die Höhe h)"],
        erg,
        hinweis=None if sp["it"] else
        "Torsionswerte nur für Kreis/Rohr exakt — offene Profile tordieren grundsätzlich anders.",
    )


# ──────────────────────────────────────────────────────────────────────────────
# A2 — Zug/Druck, Schub, Flächenpressung
# ──────────────────────────────────────────────────────────────────────────────

def zug_druck(params: dict) -> str:
    """Normalspannung aus Zug- oder Druckkraft: σ = F/A."""
    (f,) = _req(params, "f")
    a = _opt(params, "a", 0.0)
    annahmen = [f"Kraft F = {_n(f)} N (Zug positiv, Druck negativ)"]
    if a <= 0:
        sp = section_properties(_txt(params, "profil", ""), params)
        a = sp["a"]
        annahmen.append(_profil_text(sp))
    annahmen.append(f"Querschnitt A = {_n(a)} mm²")
    rp = _opt(params, "rp", 0.0)
    sigma = f / a
    bew = []
    if rp > 0:
        s_soll = _opt(params, "s", 1.5)
        zul = rp / s_soll
        annahmen.append(f"Streckgrenze R_p = {_n(rp)} MPa, geforderte Sicherheit S = {_n(s_soll)}")
        bew = _auslastung(abs(sigma), zul) + [
            f"zulässige Spannung σ_zul = R_p/S = {_n(zul)} MPa"
        ]
    return _block(
        "Zug-/Druckspannung", annahmen, ["σ = F / A"],
        [f"σ = {_n(sigma)} MPa ({'Zug' if sigma >= 0 else 'Druck'})"],
        bew,
        hinweis="Bei Druck zusätzlich auf Knickung prüfen (kind='knickung') — "
                "schlanke Stäbe versagen vor Erreichen der Streckgrenze.",
    )


def schub(params: dict) -> str:
    """Schubspannung aus Querkraft: τ_m = F/A (+ Formfaktor für τ_max)."""
    f, a = _req(params, "f", "a")
    rp = _opt(params, "rp", 0.0)
    form = _txt(params, "profil", "")
    tau_m = f / a
    faktor = {"rechteck": 1.5, "quadrat": 1.5, "kreis": 4.0 / 3.0}.get(form)
    annahmen = [f"Querkraft F = {_n(f)} N", f"Querschnitt A = {_n(a)} mm²"]
    erg = [f"mittlere Schubspannung τ_m = {_n(tau_m)} MPa"]
    formeln = ["τ_m = F / A"]
    tau_ist = tau_m
    if faktor:
        tau_max = faktor * tau_m
        tau_ist = tau_max
        annahmen.append(f"Querschnittsform {form} → Formfaktor {_n(faktor, 3)}")
        formeln.append(f"τ_max = {_n(faktor, 3)} · τ_m (parabolische Verteilung)")
        erg.append(f"maximale Schubspannung τ_max = {_n(tau_max)} MPa")
    bew = []
    if rp > 0:
        s_soll = _opt(params, "s", 1.5)
        tau_zul = 0.577 * rp / s_soll   # von Mises: τ_F = R_p/√3
        annahmen.append(f"R_p = {_n(rp)} MPa, S = {_n(s_soll)}")
        formeln.append("τ_zul = R_p / (√3 · S)  (von Mises)")
        bew = _auslastung(tau_ist, tau_zul, "Schubspannung") + [
            f"τ_zul = {_n(tau_zul)} MPa"
        ]
    return _block("Schubspannung", annahmen, formeln, erg, bew)


def flaechenpressung(params: dict) -> str:
    """Flächenpressung p = F/A; für Bolzen/Lager A = d·l (projiziert)."""
    (f,) = _req(params, "f")
    a = _opt(params, "a", 0.0)
    annahmen = [f"Kraft F = {_n(f)} N"]
    formeln = []
    if a <= 0:
        d, l = _req(params, "d", "l")
        a = d * l
        annahmen += [f"Bolzendurchmesser d = {_n(d)} mm", f"tragende Länge l = {_n(l)} mm"]
        formeln.append("A = d · l  (projizierte Fläche, Bolzen/Auge)")
    else:
        annahmen.append(f"Berührfläche A = {_n(a)} mm²")
    formeln.append("p = F / A")
    p_zul = _opt(params, "p_zul", 0.0)
    p = f / a
    bew = _auslastung(p, p_zul, "Pressung") if p_zul > 0 else [
        "Kein p_zul angegeben — Richtwerte: Stahl/Stahl ruhend 70–150 MPa, "
        "schwellend 40–80 MPa, Gleitlager Bronze 5–15 MPa."
    ]
    return _block("Flächenpressung", annahmen, formeln,
                  [f"p = {_n(p)} MPa", f"tragende Fläche A = {_n(a)} mm²"], bew)


# ──────────────────────────────────────────────────────────────────────────────
# A3 — Biegung (Standardlastfälle)
# ──────────────────────────────────────────────────────────────────────────────

#: Lastfall → (Beschreibung, M_max-Formel, f_max-Formel, Lastart)
#: f(F|q, L, E, I) — Momente in Nmm, Längen mm, E in MPa
BIEGEFAELLE: dict[str, dict] = {
    "traeger_mitte": {
        "text": "Träger auf 2 Stützen, Einzellast F in der Mitte",
        "m": lambda F, L: F * L / 4.0,
        "f": lambda F, L, E, I: F * L ** 3 / (48.0 * E * I),
        "m_formel": "M_max = F·L/4 (Feldmitte)",
        "f_formel": "f_max = F·L³/(48·E·I)",
        "last": "f",
    },
    "traeger_gleichlast": {
        "text": "Träger auf 2 Stützen, Streckenlast q",
        "m": lambda q, L: q * L ** 2 / 8.0,
        "f": lambda q, L, E, I: 5.0 * q * L ** 4 / (384.0 * E * I),
        "m_formel": "M_max = q·L²/8 (Feldmitte)",
        "f_formel": "f_max = 5·q·L⁴/(384·E·I)",
        "last": "q",
    },
    "kragarm_ende": {
        "text": "Kragarm (einseitig eingespannt), Einzellast F am freien Ende",
        "m": lambda F, L: F * L,
        "f": lambda F, L, E, I: F * L ** 3 / (3.0 * E * I),
        "m_formel": "M_max = F·L (an der Einspannung)",
        "f_formel": "f_max = F·L³/(3·E·I)",
        "last": "f",
    },
    "kragarm_gleichlast": {
        "text": "Kragarm, Streckenlast q",
        "m": lambda q, L: q * L ** 2 / 2.0,
        "f": lambda q, L, E, I: q * L ** 4 / (8.0 * E * I),
        "m_formel": "M_max = q·L²/2 (an der Einspannung)",
        "f_formel": "f_max = q·L⁴/(8·E·I)",
        "last": "q",
    },
    "traeger_eingespannt_mitte": {
        "text": "beidseitig eingespannter Träger, Einzellast F in der Mitte",
        "m": lambda F, L: F * L / 8.0,
        "f": lambda F, L, E, I: F * L ** 3 / (192.0 * E * I),
        "m_formel": "M_max = F·L/8 (Einspannung und Feldmitte)",
        "f_formel": "f_max = F·L³/(192·E·I)",
        "last": "f",
    },
    "traeger_eingespannt_gleichlast": {
        "text": "beidseitig eingespannter Träger, Streckenlast q",
        "m": lambda q, L: q * L ** 2 / 12.0,
        "f": lambda q, L, E, I: q * L ** 4 / (384.0 * E * I),
        "m_formel": "M_max = q·L²/12 (Einspannung)",
        "f_formel": "f_max = q·L⁴/(384·E·I)",
        "last": "q",
    },
}


def biegung(params: dict) -> str:
    """Biegespannung und Durchbiegung für einen Standardlastfall."""
    fall = _txt(params, "fall", "")
    if not fall:
        # Bewusst KEIN Standardwert: zwischen Kragarm und Träger auf zwei Stützen
        # liegt beim gleichen F und L der Faktor 4 im Biegemoment. Ein stiller
        # Standard liefert hier selbstbewusst falsche Zahlen (gemessen mit 3B-Modell).
        raise ValueError(
            "Lastfall fehlt — bitte 'fall' angeben oder die Rechenart mit Lastfall "
            "wählen (z. B. kind='biegung_kragarm_ende'). Verfügbar: "
            + ", ".join(BIEGEFAELLE)
        )
    if fall not in BIEGEFAELLE:
        raise ValueError(
            f"Lastfall '{fall}' unbekannt. Verfügbar: " + ", ".join(BIEGEFAELLE)
        )
    cfg = BIEGEFAELLE[fall]
    (l,) = _req(params, "l")
    lastart = cfg["last"]
    # Lastart und Lastfall MÜSSEN zusammenpassen. Früher wurde ein übergebenes F
    # bei einem Streckenlastfall still zu q = F/L umgerechnet — das verdeckte eine
    # falsche Lastfallwahl und lieferte selbstbewusst einen um Faktor 2 falschen
    # Wert (gemessen: granite4.2:3b nahm „_gleichlast" für eine mittige Einzellast).
    # Jetzt lieber eine klare Rückfrage als eine stille Fehlrechnung.
    if lastart == "f":
        if "f" not in params and _opt(params, "q", 0.0) > 0:
            raise ValueError(
                f"Lastfall '{fall}' erwartet eine Einzellast 'f', es kam aber eine "
                "Streckenlast 'q'. Für eine Streckenlast die Variante mit "
                "'_gleichlast' wählen."
            )
        (last,) = _req(params, "f")
        last_txt = f"Einzellast F = {_n(last)} N"
    else:
        if _opt(params, "q", 0.0) <= 0:
            hinweis_f = (" Es kam eine Einzellast 'f' — für eine mittig oder am Ende "
                         "angreifende Einzellast die Variante OHNE '_gleichlast' "
                         "wählen (z. B. biegung_traeger_mitte, biegung_kragarm_ende)."
                         ) if "f" in params else ""
            raise ValueError(
                f"Lastfall '{fall}' erwartet eine Streckenlast 'q' in N/mm." + hinweis_f
            )
        last = _opt(params, "q", 0.0)
        last_txt = f"Streckenlast q = {_n(last, 4)} N/mm"

    e_mod = _opt(params, "e", E_STAHL)
    w = _opt(params, "w", 0.0)
    i_fl = _opt(params, "i", 0.0)
    annahmen = [cfg["text"], f"Stützweite/Länge L = {_n(l)} mm", last_txt,
                f"E-Modul = {_n(e_mod)} MPa"]
    if w <= 0 or i_fl <= 0:
        sp = section_properties(_txt(params, "profil", ""), params)
        w = w if w > 0 else sp["wy"]
        i_fl = i_fl if i_fl > 0 else sp["iy"]
        annahmen.append(_profil_text(sp))
    annahmen.append(f"W_y = {_n(w)} mm³, I_y = {_n(i_fl)} mm⁴")

    m_nmm = cfg["m"](last, l)
    sigma_b = m_nmm / w
    f_max = cfg["f"](last, l, e_mod, i_fl)
    erg = [
        f"Biegemoment M_max = {_n(m_nmm)} Nmm = {_n(m_nmm / 1000.0)} Nm",
        f"Biegespannung σ_b = M/W = {_n(sigma_b)} MPa",
        f"Durchbiegung f_max = {_n(f_max, 3)} mm",
    ]
    if l:
        erg.append(f"relative Durchbiegung f/L = 1/{_n(l / f_max, 0) if f_max else '∞'}")
    rp = _opt(params, "rp", 0.0)
    bew = []
    if rp > 0:
        s_soll = _opt(params, "s", 1.5)
        zul = rp / s_soll
        annahmen.append(f"R_p = {_n(rp)} MPa, S = {_n(s_soll)}")
        bew = _auslastung(abs(sigma_b), zul, "Biegespannung") + [
            f"σ_b,zul = R_p/S = {_n(zul)} MPa"
        ]
    return _block("Biegung", annahmen,
                  [cfg["m_formel"], "σ_b = M_max / W_y", cfg["f_formel"]],
                  erg, bew,
                  hinweis="Reine Biegung ohne Querkraft-/Normalkraftanteil. Bei kurzen, "
                          "dicken Trägern und bei zusätzlicher Torsion mit "
                          "kind='vergleichsspannung' nachrechnen.")


# ──────────────────────────────────────────────────────────────────────────────
# B — Torsion, Wellen, Vergleichsspannung, Knickung
# ──────────────────────────────────────────────────────────────────────────────

def torsion(params: dict) -> str:
    """Torsionsspannung und Verdrehwinkel einer Welle."""
    (t_nm,) = _req(params, "t")
    t_nmm = t_nm * 1000.0
    d = _opt(params, "d", 0.0)
    di = _opt(params, "d_i", 0.0)
    wt = _opt(params, "wt", 0.0)
    it = _opt(params, "it", 0.0)
    annahmen = [f"Torsionsmoment T = {_n(t_nm)} Nm"]
    if wt <= 0 or it <= 0:
        if d <= 0:
            raise ValueError("fehlende Werte: d (Wellendurchmesser) oder wt/it")
        sp = section_properties("rohr" if di > 0 else "kreis",
                                {"d": d, "d_i": di} if di > 0 else {"d": d})
        wt, it = sp["wt"], sp["it"]
        annahmen.append(f"{'Hohlwelle' if di > 0 else 'Vollwelle'} d = {_n(d)} mm"
                        + (f", d_i = {_n(di)} mm" if di > 0 else ""))
    annahmen.append(f"W_t = {_n(wt)} mm³, I_t = {_n(it)} mm⁴")
    tau_t = t_nmm / wt
    erg = [f"Torsionsspannung τ_t = T/W_t = {_n(tau_t)} MPa"]
    formeln = ["τ_t = T / W_t",
               "W_t = π·d³/16 (Vollwelle) bzw. π·(d⁴−d_i⁴)/(16·d) (Hohlwelle)"]
    l = _opt(params, "l", 0.0)
    g_mod = _opt(params, "g", G_STAHL)
    if l > 0:
        phi = t_nmm * l / (g_mod * it)
        annahmen.append(f"Wellenlänge L = {_n(l)} mm, G = {_n(g_mod)} MPa")
        formeln.append("φ = T·L/(G·I_t)")
        erg.append(f"Verdrehwinkel φ = {_n(math.degrees(phi), 3)}° "
                   f"({_n(phi * 1000, 3)} mrad), bezogen {_n(math.degrees(phi) / (l / 1000.0), 3)}°/m")
    rp = _opt(params, "rp", 0.0)
    bew = []
    if rp > 0:
        s_soll = _opt(params, "s", 1.5)
        tau_zul = 0.577 * rp / s_soll
        annahmen.append(f"R_p = {_n(rp)} MPa, S = {_n(s_soll)}")
        bew = _auslastung(tau_t, tau_zul, "Torsionsspannung") + [
            f"τ_t,zul = R_p/(√3·S) = {_n(tau_zul)} MPa"
        ]
    return _block("Torsion / Welle", annahmen, formeln, erg, bew)


def wellendurchmesser(params: dict) -> str:
    """Erforderlicher Wellendurchmesser aus Torsionsmoment."""
    t_nm, tau_zul = _req(params, "t", "tau_zul")
    t_nmm = t_nm * 1000.0
    d = (16.0 * t_nmm / (math.pi * tau_zul)) ** (1.0 / 3.0)
    # auf nächstes gängiges Normmaß aufrunden
    norm = [6, 8, 10, 12, 14, 16, 18, 20, 22, 25, 28, 30, 32, 35, 38, 40, 45, 50,
            55, 60, 65, 70, 75, 80, 85, 90, 95, 100, 110, 120, 130, 140, 150]
    gewaehlt = next((x for x in norm if x >= d), math.ceil(d))
    tau_ist = t_nmm / (math.pi * gewaehlt ** 3 / 16.0)
    return _block(
        "Erforderlicher Wellendurchmesser",
        [f"Torsionsmoment T = {_n(t_nm)} Nm",
         f"zulässige Torsionsspannung τ_zul = {_n(tau_zul)} MPa",
         "Vollwelle, reine Torsion"],
        ["τ = T/W_t mit W_t = π·d³/16  →  d = ∛(16·T/(π·τ_zul))"],
        [f"rechnerisch d ≥ {_n(d)} mm",
         f"gewählt d = {gewaehlt} mm (nächstes gängiges Maß) → τ_t = {_n(tau_ist)} MPa"],
        _auslastung(tau_ist, tau_zul, "Torsionsspannung"),
        hinweis="Nur Torsion. Biegung aus Riemen-/Zahnkräften und Kerbwirkung "
                "(Passfedernut, Absatz) erhöhen den Bedarf deutlich — mit "
                "kind='vergleichsspannung' und kind='dauerfestigkeit' nachweisen.",
    )


def vergleichsspannung(params: dict) -> str:
    """Zusammengesetzte Beanspruchung → Vergleichsspannung (von Mises/Tresca)."""
    sigma = _opt(params, "sigma", 0.0)
    tau = _opt(params, "tau", 0.0)
    if sigma == 0.0 and tau == 0.0:
        raise ValueError("fehlende Werte: sigma und/oder tau")
    hypo = _txt(params, "hypothese", "mises")
    alpha0 = _opt(params, "alpha_0", 1.0)
    if hypo.startswith("tre") or hypo.startswith("sh"):
        sv = math.sqrt(sigma ** 2 + 4.0 * (alpha0 * tau) ** 2)
        formel = "σ_v = √(σ² + 4·(α₀·τ)²)  (Schubspannungshypothese / Tresca)"
        name = "Tresca"
    else:
        sv = math.sqrt(sigma ** 2 + 3.0 * (alpha0 * tau) ** 2)
        formel = "σ_v = √(σ² + 3·(α₀·τ)²)  (Gestaltänderungsenergie / von Mises)"
        name = "von Mises"
    annahmen = [f"Normalspannung σ = {_n(sigma)} MPa",
                f"Schub-/Torsionsspannung τ = {_n(tau)} MPa",
                f"Hypothese: {name}"]
    if alpha0 != 1.0:
        annahmen.append(f"Anstrengungsverhältnis α₀ = {_n(alpha0)}")
    rp = _opt(params, "rp", 0.0)
    bew = []
    if rp > 0:
        s_soll = _opt(params, "s", 1.5)
        zul = rp / s_soll
        annahmen.append(f"R_p = {_n(rp)} MPa, S = {_n(s_soll)}")
        bew = _auslastung(sv, zul, "Vergleichsspannung") + [f"σ_zul = {_n(zul)} MPa"]
    return _block("Vergleichsspannung", annahmen, [formel],
                  [f"σ_v = {_n(sv)} MPa"], bew,
                  hinweis="α₀ berücksichtigt unterschiedliche Lastfälle von σ und τ "
                          "(z. B. Biegung wechselnd, Torsion ruhend → α₀ ≈ 0,7).")


#: Eulerfall → (freie Knicklänge als Faktor von L, Beschreibung)
EULERFAELLE: dict[str, tuple[float, str]] = {
    "1": (2.0,   "ein Ende eingespannt, anderes frei"),
    "2": (1.0,   "beide Enden gelenkig gelagert"),
    "3": (0.699, "ein Ende eingespannt, anderes gelenkig"),
    "4": (0.5,   "beide Enden eingespannt"),
}

#: Werkstoff → (σ_dP Proportionalitätsgrenze, Tetmajer-Funktion σ_K(λ))
_TETMAJER: dict[str, tuple[float, Any, str]] = {
    "s235": (190.0, lambda lam: 310.0 - 1.14 * lam, "σ_K = 310 − 1,14·λ"),
    "s355": (260.0, lambda lam: 335.0 - 0.62 * lam, "σ_K = 335 − 0,62·λ"),
    "guss": (0.0,   lambda lam: 776.0 - 12.0 * lam + 0.053 * lam ** 2,
             "σ_K = 776 − 12·λ + 0,053·λ²"),
}


def knickung(params: dict) -> str:
    """Knicknachweis nach Euler, bei kleiner Schlankheit nach Tetmajer."""
    (f,) = _req(params, "f")
    l = _opt(params, "l", 0.0)
    if l <= 0:
        raise ValueError("fehlende Werte: l (Stablänge)")
    fall = _txt(params, "fall", "2")
    if fall not in EULERFAELLE:
        raise ValueError("fall muss 1, 2, 3 oder 4 sein (Eulerfall)")
    beta, falltext = EULERFAELLE[fall]
    e_mod = _opt(params, "e", E_STAHL)
    werkstoff = _txt(params, "werkstoff", "s235")
    i_fl = _opt(params, "i", 0.0)
    a = _opt(params, "a", 0.0)
    annahmen = [f"Druckkraft F = {_n(f)} N", f"Stablänge L = {_n(l)} mm",
                f"Eulerfall {fall} ({falltext}) → l_k = {_n(beta, 3)}·L",
                f"E = {_n(e_mod)} MPa"]
    if i_fl <= 0 or a <= 0:
        sp = section_properties(_txt(params, "profil", ""), params)
        # Knicken tritt um die SCHWACHE Achse auf
        i_fl = i_fl if i_fl > 0 else min(sp["iy"], sp["iz"])
        a = a if a > 0 else sp["a"]
        annahmen.append(_profil_text(sp) + " — maßgebend ist die kleinere Achse")
    annahmen.append(f"I_min = {_n(i_fl)} mm⁴, A = {_n(a)} mm²")

    lk = beta * l
    i_r = math.sqrt(i_fl / a)
    lam = lk / i_r
    f_krit = math.pi ** 2 * e_mod * i_fl / lk ** 2
    sigma_d = f / a

    sdp, tet_fn, tet_formel = _TETMAJER.get(werkstoff, _TETMAJER["s235"])
    lam_g = math.pi * math.sqrt(e_mod / sdp) if sdp > 0 else 80.0
    elastisch = lam >= lam_g

    formeln = [f"l_k = {_n(beta, 3)}·L = {_n(lk)} mm",
               "i = √(I_min/A), λ = l_k/i",
               f"Grenzschlankheit λ_g = π·√(E/σ_dP) = {_n(lam_g, 1)} ({werkstoff})"]
    erg = [f"Trägheitsradius i = {_n(i_r)} mm",
           f"Schlankheitsgrad λ = {_n(lam, 1)}",
           f"vorhandene Druckspannung σ_d = F/A = {_n(sigma_d)} MPa"]
    if elastisch:
        formeln.append("λ ≥ λ_g → elastisches Knicken, Euler gilt: F_krit = π²·E·I/l_k²")
        sigma_k = math.pi ** 2 * e_mod / lam ** 2
        erg += [f"kritische Last F_krit = {_n(f_krit)} N = {_n(f_krit / 1000.0)} kN",
                f"Knickspannung σ_K = π²·E/λ² = {_n(sigma_k)} MPa"]
        s_vorh = f_krit / f if f else float("inf")
        s_soll = _opt(params, "s", 3.0)
        bew = [f"vorhandene Knicksicherheit S = F_krit/F = {_n(s_vorh)}",
               f"gefordert wurde S = {_n(s_soll)} (üblich 3–6 bei Euler)",
               "✅ ausreichend" if s_vorh >= s_soll else "❌ nicht ausreichend"]
    else:
        formeln.append(f"λ < λ_g → unelastisches Knicken, Tetmajer: {tet_formel}")
        sigma_k = max(tet_fn(lam), 0.0)
        f_k = sigma_k * a
        erg += [f"Knickspannung σ_K (Tetmajer) = {_n(sigma_k)} MPa",
                f"zugehörige Knicklast F_K = σ_K·A = {_n(f_k)} N = {_n(f_k / 1000.0)} kN"]
        s_vorh = sigma_k / sigma_d if sigma_d else float("inf")
        s_soll = _opt(params, "s", 2.0)
        bew = [f"vorhandene Sicherheit S = σ_K/σ_d = {_n(s_vorh)}",
               f"gefordert wurde S = {_n(s_soll)} (üblich 1,5–3 im Tetmajer-Bereich)",
               "✅ ausreichend" if s_vorh >= s_soll else "❌ nicht ausreichend"]
        if lam < 20:
            bew.append("⚠️ λ < 20: kein Knickproblem mehr — reiner Druckspannungsnachweis genügt.")
    return _block("Knicknachweis", annahmen, formeln, erg, bew,
                  hinweis="Euler setzt einen ideal geraden, mittig belasteten Stab voraus. "
                          "Vorkrümmung und Lastexzentrizität senken die Traglast — "
                          "im Stahlbau stattdessen das Ersatzstabverfahren nach EC3 verwenden.")


# ──────────────────────────────────────────────────────────────────────────────
# C — Verbindungen und Maschinenelemente
# ──────────────────────────────────────────────────────────────────────────────

def schweissnaht(params: dict) -> str:
    """Kehl- oder Stumpfnaht: Nahtspannung und Auslastung."""
    (f,) = _req(params, "f")
    art = _txt(params, "art", "kehlnaht")
    a_naht = _opt(params, "a", 0.0)   # Kehlnahtdicke bzw. Blechdicke
    l = _opt(params, "l", 0.0)
    if a_naht <= 0 or l <= 0:
        raise ValueError("fehlende Werte: a (Nahtdicke) und l (Nahtlänge)")
    anzahl = max(1, int(_opt(params, "n", 1)))
    a_w = a_naht * l * anzahl
    sigma_w = f / a_w
    rp = _opt(params, "rp", 0.0)
    v = _opt(params, "v", 0.8 if art.startswith("kehl") else 1.0)
    annahmen = [f"Kraft F = {_n(f)} N",
                f"Nahtart: {art}",
                f"{'Nahtdicke a' if art.startswith('kehl') else 'Blechdicke'} = {_n(a_naht)} mm",
                f"Nahtlänge l = {_n(l)} mm" + (f" × {anzahl} Nähte" if anzahl > 1 else ""),
                f"Nahtgütebeiwert v = {_n(v)}"]
    formeln = [f"A_w = a · l{' · n' if anzahl > 1 else ''} = {_n(a_w)} mm²",
               "σ_w = F / A_w"]
    erg = [f"Nahtspannung σ_w = {_n(sigma_w)} MPa", f"Nahtfläche A_w = {_n(a_w)} mm²"]
    bew = []
    if rp > 0:
        s_soll = _opt(params, "s", 1.5)
        zul = v * rp / s_soll
        annahmen.append(f"R_p (Grundwerkstoff) = {_n(rp)} MPa, S = {_n(s_soll)}")
        formeln.append("σ_w,zul = v · R_p / S")
        bew = _auslastung(sigma_w, zul, "Nahtspannung") + [f"σ_w,zul = {_n(zul)} MPa"]
    return _block("Schweißnaht", annahmen, formeln, erg, bew,
                  hinweis="Vereinfachter Nachweis über die Nahtfläche. Für den "
                          "Betriebsfestigkeitsnachweis Kerbfallkataloge (EC3-1-9 "
                          "bzw. FKM) verwenden; Endkrater und Anfahrstellen abziehen.")


def passfeder(params: dict) -> str:
    """Passfeder nach DIN 6885: Flächenpressung an Nut und Feder."""
    t_nm, d, h, l = _req(params, "t", "d", "h", "l")
    # Verwechslungsschutz: eine Passfeder ist immer deutlich niedriger als die
    # Welle dick ist. Weil d und h beide in mm angegeben werden, kann die
    # Einheitenprüfung einen Tausch nicht sehen — die Geometrie schon.
    # (Gemessen: granite4.2:3b lieferte d=8 / h=12 für Welle 40 / Feder 8.)
    if h >= d:
        raise ValueError(
            f"Federhöhe h = {_n(h)} mm ist nicht kleiner als der Wellendurchmesser "
            f"d = {_n(d)} mm — die Werte sind wahrscheinlich vertauscht. "
            "d = Wellendurchmesser, b = Federbreite, h = Federhöhe, l = Federlänge"
        )
    t_nmm = t_nm * 1000.0
    anzahl = max(1, int(_opt(params, "n", 1)))
    form = _txt(params, "form", "b")
    b = _opt(params, "b", 0.0)
    # tragende Länge: Form A (Rundstirn) verliert die Breite b
    l_tr = (l - b) if (form == "a" and b > 0) else l
    if l_tr <= 0:
        raise ValueError("tragende Länge l_tr ≤ 0 — l größer als b wählen")
    h_tr = _opt(params, "h_tr", 0.45 * h)
    f_u = 2.0 * t_nmm / d
    # Mitnahmefaktor: mehrere Federn tragen nicht voll (üblich 0,75 bei n=2)
    phi = 1.0 if anzahl == 1 else 0.75
    p = f_u / (h_tr * l_tr * anzahl * phi)
    p_zul = _opt(params, "p_zul", 0.0)
    annahmen = [f"Drehmoment T = {_n(t_nm)} Nm", f"Wellendurchmesser d = {_n(d)} mm",
                f"Federhöhe h = {_n(h)} mm → tragende Höhe h' = {_n(h_tr)} mm (≈0,45·h)",
                f"Federlänge l = {_n(l)} mm → tragende Länge l_tr = {_n(l_tr)} mm "
                f"(Form {form.upper()})",
                f"Anzahl Federn n = {anzahl}" + (f", Mitnahmefaktor φ = {_n(phi)}" if anzahl > 1 else "")]
    formeln = ["Umfangskraft F_u = 2·T/d",
               "p = F_u / (h' · l_tr · n · φ)"]
    erg = [f"Umfangskraft F_u = {_n(f_u)} N", f"Flächenpressung p = {_n(p)} MPa"]
    bew = _auslastung(p, p_zul, "Pressung") if p_zul > 0 else [
        "Kein p_zul angegeben — Richtwerte Nabe aus Stahl: ruhend 150 MPa, "
        "schwellend 110 MPa, wechselnd 90 MPa; Gusseisen etwa halb so viel."
    ]
    return _block("Passfederverbindung", annahmen, formeln, erg, bew,
                  hinweis="Die Passfeder überträgt das Moment über Flächenpressung, "
                          "nicht über Scherung — die Nut schwächt zusätzlich die Welle "
                          "(Kerbwirkung, siehe kind='dauerfestigkeit').")


def pressverband(params: dict) -> str:
    """Pressverband: übertragbares Moment bzw. erforderlicher Fugendruck."""
    d_f, l_f = _req(params, "d", "l")
    mu = _opt(params, "mu", 0.12)
    p = _opt(params, "p", 0.0)
    t_soll = _opt(params, "t", 0.0)
    annahmen = [f"Fugendurchmesser d_F = {_n(d_f)} mm",
                f"Fugenlänge l_F = {_n(l_f)} mm",
                f"Reibbeiwert μ = {_n(mu, 3)} (Stahl/Stahl trocken 0,10–0,15)"]
    formeln = ["übertragbares Moment  T = (π/2)·d_F²·l_F·p·μ",
               "übertragbare Axialkraft F_ax = π·d_F·l_F·p·μ"]
    erg: list[str] = []
    bew: list[str] = []
    if p > 0:
        annahmen.append(f"Fugendruck p = {_n(p)} MPa")
        t_moegl = math.pi / 2.0 * d_f ** 2 * l_f * p * mu / 1000.0   # Nm
        f_ax = math.pi * d_f * l_f * p * mu
        erg += [f"übertragbares Moment T = {_n(t_moegl)} Nm",
                f"übertragbare Axialkraft F_ax = {_n(f_ax)} N"]
        if t_soll > 0:
            s = t_moegl / t_soll if t_soll else float("inf")
            bew = [f"gefordertes Moment T_soll = {_n(t_soll)} Nm",
                   f"Rutschsicherheit S = {_n(s)}",
                   "✅ rutschfest" if s >= 1.5 else
                   ("⚠️ Sicherheit < 1,5" if s >= 1.0 else "❌ rutscht durch")]
    elif t_soll > 0:
        p_erf = t_soll * 1000.0 * 2.0 / (math.pi * d_f ** 2 * l_f * mu)
        annahmen.append(f"gefordertes Moment T = {_n(t_soll)} Nm")
        formeln.append("umgestellt: p_erf = 2·T/(π·d_F²·l_F·μ)")
        erg.append(f"erforderlicher Fugendruck p_erf = {_n(p_erf)} MPa")
        bew = ["Nächster Schritt: Übermaß aus p_erf über die Lamé-Gleichungen "
               "bestimmen und eine Passung nach DIN 7157 wählen (p steigt mit dem "
               "Übermaß, begrenzt durch die Nabenfestigkeit)."]
    else:
        raise ValueError("fehlende Werte: p (Fugendruck) oder t (gefordertes Moment)")
    return _block("Pressverband (Längspressverband)", annahmen, formeln, erg, bew,
                  hinweis="Setzt eine elastische Nabe und gleichmäßigen Fugendruck "
                          "voraus. Fliehkraft bei hohen Drehzahlen und "
                          "Temperaturdehnung bauen den Fugendruck ab.")


def druckfeder(params: dict) -> str:
    """Zylindrische Schraubendruckfeder (DIN EN 13906-1)."""
    d, d_m, n_w = _req(params, "d", "d_m", "n")
    g_mod = _opt(params, "g", 81500.0)
    f = _opt(params, "f", 0.0)
    s = _opt(params, "s", 0.0)
    w = d_m / d
    if w <= 1.0:
        raise ValueError("d_m muss deutlich größer als d sein (Wickelverhältnis w > 1)")
    rate = g_mod * d ** 4 / (8.0 * d_m ** 3 * n_w)     # N/mm
    k = (w + 0.5) / (w - 0.75)                          # Bergsträsser-Faktor
    annahmen = [f"Drahtdurchmesser d = {_n(d)} mm",
                f"mittlerer Windungsdurchmesser D_m = {_n(d_m)} mm",
                f"federnde Windungen n = {_n(n_w)}",
                f"Schubmodul G = {_n(g_mod)} MPa (Federstahl)",
                f"Wickelverhältnis w = D_m/d = {_n(w)}"]
    formeln = ["Federrate R = G·d⁴/(8·D_m³·n)",
               "τ = 8·F·D_m/(π·d³),  korrigiert τ_k = k·τ  mit k = (w+0,5)/(w−0,75)"]
    erg = [f"Federrate R = {_n(rate, 3)} N/mm",
           f"Spannungskorrekturfaktor k = {_n(k, 3)}"]
    if f <= 0 and s > 0:
        f = rate * s
        annahmen.append(f"Federweg s = {_n(s)} mm → F = R·s")
    if f > 0:
        s_weg = f / rate
        tau = 8.0 * f * d_m / (math.pi * d ** 3)
        tau_k = k * tau
        erg += [f"Federkraft F = {_n(f)} N bei Federweg s = {_n(s_weg)} mm",
                f"Schubspannung τ = {_n(tau)} MPa, korrigiert τ_k = {_n(tau_k)} MPa"]
        tau_zul = _opt(params, "tau_zul", 0.0)
        if tau_zul > 0:
            return _block("Schraubendruckfeder", annahmen, formeln, erg,
                          _auslastung(tau_k, tau_zul, "Schubspannung")
                          + [f"τ_zul = {_n(tau_zul)} MPa"],
                          hinweis="τ_zul für Federstahl EN 10270-1 etwa 0,45·R_m "
                                  "(statisch); dynamisch über das Goodman-Diagramm.")
    if w < 4 or w > 12:
        erg.append(f"⚠️ w = {_n(w)} liegt außerhalb des günstigen Bereichs 4–12")
    return _block("Schraubendruckfeder", annahmen, formeln, erg, None,
                  hinweis="Knickgefahr prüfen, wenn L_0/D_m > 3 (schlanke Feder) — "
                          "dann führen oder auf Hülse setzen.")


def waelzlager(params: dict) -> str:
    """Wälzlager-Lebensdauer L10 / L10h nach ISO 281."""
    c, p_last = _req(params, "c", "p")
    n = _opt(params, "n", 0.0)
    art = _txt(params, "art", "kugel")
    expo = 3.0 if art.startswith("kugel") else 10.0 / 3.0
    if p_last <= 0:
        raise ValueError("p (äquivalente Lagerlast) muss > 0 sein")
    if p_last >= c:
        raise ValueError(
            f"Lagerlast P = {_n(p_last)} N ist größer/gleich der Tragzahl C = {_n(c)} N — "
            "das ergibt keine sinnvolle Lebensdauer. Prüfe, ob C und P verwechselt "
            "wurden oder die Drehzahl versehentlich als P angekommen ist "
            "(Drehzahl gehört auf 'n')"
        )
    l10 = (c / p_last) ** expo          # in 10⁶ Umdrehungen
    annahmen = [f"dynamische Tragzahl C = {_n(c)} N",
                f"äquivalente Lagerlast P = {_n(p_last)} N",
                f"Lagerart: {art} → Exponent p = {_n(expo, 3)}"]
    formeln = ["L10 = (C/P)^p  [in 10⁶ Umdrehungen]"]
    erg = [f"L10 = {_n(l10)} · 10⁶ Umdrehungen"]
    bew = []
    if n > 0:
        l10h = l10 * 1e6 / (60.0 * n)
        annahmen.append(f"Drehzahl n = {_n(n)} 1/min")
        formeln.append("L10h = L10 · 10⁶ / (60·n)  [Stunden]")
        erg.append(f"L10h = {_n(l10h, 0)} h ≈ {_n(l10h / 8760.0)} Jahre Dauerbetrieb")
        richt = _opt(params, "l_soll", 0.0)
        if richt > 0:
            bew = [f"gefordert L10h,soll = {_n(richt, 0)} h",
                   "✅ erreicht" if l10h >= richt else "❌ nicht erreicht"]
    if c / p_last < 5:
        bew.append("⚠️ C/P < 5: hoch belastet — Lager eine Baugröße größer prüfen.")
    return _block("Wälzlager-Lebensdauer", annahmen, formeln, erg, bew,
                  hinweis="L10 = 90 % Überlebenswahrscheinlichkeit, ohne Zuschläge. "
                          "Für die erweiterte Lebensdauer L_nm kommen Schmierung, "
                          "Verunreinigung und Ermüdungsgrenzlast hinzu (a_ISO).")


def zahnrad(params: dict) -> str:
    """Zahnrad-Geometrie (Evolventen-Normverzahnung) + Umfangskraft, Lewis-Abschätzung."""
    m, z1 = _req(params, "m", "z1")
    z1 = int(z1)
    z2 = _opt(params, "z2", 0.0)
    b = _opt(params, "b", 0.0)
    t_nm = _opt(params, "t", 0.0)
    d1 = m * z1
    annahmen = [f"Modul m = {_n(m)} mm", f"Zähnezahl z1 = {z1}",
                "Normverzahnung α = 20°, h_a = 1·m, h_f = 1,25·m"]
    formeln = ["d = m·z,  d_a = m·(z+2),  d_f = m·(z−2,5),  p = π·m"]
    erg = [f"Rad 1: Teilkreis d1 = {_n(d1)} mm, Kopfkreis d_a1 = {_n(m * (z1 + 2))} mm, "
           f"Fußkreis d_f1 = {_n(m * (z1 - 2.5))} mm",
           f"Teilung p = {_n(math.pi * m)} mm"]
    if z2 > 0:
        z2 = int(z2)
        d2 = m * z2
        a_achs = m * (z1 + z2) / 2.0
        i = z2 / z1
        annahmen.append(f"Zähnezahl z2 = {z2}")
        formeln.append("Achsabstand a = m·(z1+z2)/2,  Übersetzung i = z2/z1")
        erg += [f"Rad 2: d2 = {_n(d2)} mm, d_a2 = {_n(m * (z2 + 2))} mm, "
                f"d_f2 = {_n(m * (z2 - 2.5))} mm",
                f"Achsabstand a = {_n(a_achs)} mm",
                f"Übersetzung i = {_n(i, 3)}"]
    bew = []
    if t_nm > 0:
        f_t = 2.0 * t_nm * 1000.0 / d1
        f_r = f_t * math.tan(math.radians(20.0))
        formeln.append("Umfangskraft F_t = 2·T/d1,  Radialkraft F_r = F_t·tan20°")
        erg += [f"Umfangskraft F_t = {_n(f_t)} N (bei T = {_n(t_nm)} Nm am Rad 1)",
                f"Radialkraft F_r = {_n(f_r)} N"]
        if b > 0:
            sigma_f = f_t / (b * m) * 2.8    # Lewis mit Y_Fa ≈ 2,8 (Normverzahnung)
            annahmen.append(f"Zahnbreite b = {_n(b)} mm")
            formeln.append("Lewis-Abschätzung σ_F ≈ F_t/(b·m)·Y_Fa mit Y_Fa ≈ 2,8")
            erg.append(f"Zahnfußspannung σ_F ≈ {_n(sigma_f)} MPa (grobe Abschätzung)")
            sig_zul = _opt(params, "sigma_zul", 0.0)
            if sig_zul > 0:
                bew = _auslastung(sigma_f, sig_zul, "Zahnfußspannung")
    if z1 < 17:
        bew.append(f"⚠️ z1 = {z1} < 17: Unterschnitt bei Normverzahnung — "
                   "Profilverschiebung nötig.")
    return _block("Zahnrad (Geradverzahnung)", annahmen, formeln, erg, bew,
                  hinweis="Die Zahnfußspannung ist eine Lewis-Abschätzung für die "
                          "Vorauswahl. Der Tragfähigkeitsnachweis (Fuß und Flanke) "
                          "läuft nach DIN 3990/ISO 6336 mit K_A, K_V, K_Hβ, Z_H …")


# ──────────────────────────────────────────────────────────────────────────────
# D — Dauerfestigkeit und Antrieb
# ──────────────────────────────────────────────────────────────────────────────

#: Beanspruchungsart → (Faktor auf R_m für die Wechselfestigkeit, Bezeichnung)
_WECHSELFEST: dict[str, tuple[float, str]] = {
    "biegung":   (0.49, "σ_bW ≈ 0,49·R_m"),
    "zugdruck":  (0.45, "σ_zdW ≈ 0,45·R_m"),
    "torsion":   (0.29, "τ_tW ≈ 0,29·R_m"),
}


def dauerfestigkeit(params: dict) -> str:
    """Dauerfestigkeitsnachweis (FKM-nah vereinfacht): Gestaltfestigkeit und Sicherheit."""
    sigma_a, rm = _req(params, "sigma_a", "rm")
    sigma_m = _opt(params, "sigma_m", 0.0)
    art = _txt(params, "art", "biegung")
    if art not in _WECHSELFEST:
        raise ValueError("art muss biegung, zugdruck oder torsion sein")
    fak, fak_txt = _WECHSELFEST[art]
    kt = _opt(params, "kt", 1.0)            # Kerbwirkungszahl β_k bzw. Formzahl
    k_oberfl = _opt(params, "k_o", 1.0)     # Oberflächenbeiwert (geschliffen 1,0; gedreht ~0,9)
    k_groesse = _opt(params, "k_g", 1.0)    # Größeneinflussfaktor
    s_soll = _opt(params, "s", 1.5)

    sigma_w = fak * rm
    # Mittelspannungseinfluss nach Goodman (konservativ, linear bis R_m)
    if sigma_m > 0:
        sigma_w_m = sigma_w * max(0.0, 1.0 - sigma_m / rm)
    else:
        sigma_w_m = sigma_w
    sigma_ga = sigma_w_m * k_oberfl * k_groesse / kt
    s_vorh = sigma_ga / sigma_a if sigma_a else float("inf")

    annahmen = [f"Spannungsamplitude σ_a = {_n(sigma_a)} MPa",
                f"Mittelspannung σ_m = {_n(sigma_m)} MPa",
                f"Zugfestigkeit R_m = {_n(rm)} MPa",
                f"Beanspruchungsart: {art}",
                f"Kerbwirkungszahl β_k = {_n(kt)}",
                f"Oberflächenbeiwert K_O = {_n(k_oberfl)}, Größenbeiwert K_G = {_n(k_groesse)}",
                f"geforderte Sicherheit S = {_n(s_soll)}"]
    formeln = [f"Wechselfestigkeit: {fak_txt} = {_n(sigma_w)} MPa",
               "Mittelspannung (Goodman): σ_W,m = σ_W·(1 − σ_m/R_m)",
               "Gestaltfestigkeit: σ_GA = σ_W,m · K_O · K_G / β_k",
               "Sicherheit: S = σ_GA / σ_a"]
    erg = [f"Wechselfestigkeit σ_W = {_n(sigma_w)} MPa",
           f"mittelspannungskorrigiert σ_W,m = {_n(sigma_w_m)} MPa",
           f"Gestaltfestigkeit σ_GA = {_n(sigma_ga)} MPa",
           f"vorhandene Sicherheit S = {_n(s_vorh)}"]
    bew = [f"Auslastung {_n(sigma_a / sigma_ga * 100 if sigma_ga else 0, 1)} % der Gestaltfestigkeit",
           "✅ dauerfest" if s_vorh >= s_soll else
           ("⚠️ Sicherheit unter Soll, aber > 1" if s_vorh >= 1.0 else "❌ nicht dauerfest")]
    if sigma_m + sigma_a > rm:
        bew.append("⚠️ σ_m + σ_a übersteigt R_m — statischer Bruch vor Ermüdung, "
                   "zuerst den Festigkeitsnachweis führen.")
    return _block("Dauerfestigkeitsnachweis", annahmen, formeln, erg, bew,
                  hinweis="Vereinfachter Nachweis mit Schätzwerten für σ_W aus R_m. "
                          "Der belastbare Nachweis läuft nach FKM-Richtlinie mit "
                          "Werkstoff-Kennwerten, Konstruktionsfaktor und "
                          "Auslastungsgrad — dies ist eine Vorauswahl.")


def antrieb(params: dict) -> str:
    """Leistung, Drehmoment, Drehzahl: den fehlenden dritten Wert berechnen."""
    p_kw = _opt(params, "p", 0.0)
    t_nm = _opt(params, "t", 0.0)
    n = _opt(params, "n", 0.0)
    eta = _opt(params, "eta", 1.0)
    gegeben = [x > 0 for x in (p_kw, t_nm, n)]
    if sum(gegeben) < 2:
        raise ValueError("mindestens zwei von p (kW), t (Nm), n (1/min) angeben")
    formeln = ["P = M·ω = M·2π·n/60", "Kurzform: M [Nm] = 9550 · P [kW] / n [1/min]"]
    annahmen = []
    if p_kw > 0 and n > 0:
        t_calc = 9549.3 * p_kw / n
        annahmen += [f"Leistung P = {_n(p_kw)} kW", f"Drehzahl n = {_n(n)} 1/min"]
        erg = [f"Drehmoment M = {_n(t_calc)} Nm"]
        if eta != 1.0:
            annahmen.append(f"Wirkungsgrad η = {_n(eta, 3)}")
            erg.append(f"abtriebsseitig mit η: M = {_n(t_calc * eta)} Nm")
    elif t_nm > 0 and n > 0:
        p_calc = t_nm * n / 9549.3
        annahmen += [f"Drehmoment M = {_n(t_nm)} Nm", f"Drehzahl n = {_n(n)} 1/min"]
        erg = [f"Leistung P = {_n(p_calc)} kW ({_n(p_calc * 1000)} W)"]
        if eta != 1.0:
            annahmen.append(f"Wirkungsgrad η = {_n(eta, 3)}")
            erg.append(f"Antriebsleistung bei η: P = {_n(p_calc / eta)} kW")
    else:
        n_calc = 9549.3 * p_kw / t_nm
        annahmen += [f"Leistung P = {_n(p_kw)} kW", f"Drehmoment M = {_n(t_nm)} Nm"]
        erg = [f"Drehzahl n = {_n(n_calc)} 1/min",
               f"Winkelgeschwindigkeit ω = {_n(n_calc * 2 * math.pi / 60.0)} 1/s"]
    return _block("Antrieb: Leistung / Drehmoment / Drehzahl",
                  annahmen, formeln, erg)


def getriebestufe(params: dict) -> str:
    """Getriebestufe: Abtriebsdrehzahl und -moment aus Übersetzung."""
    i = _opt(params, "i", 0.0)
    z1 = _opt(params, "z1", 0.0)
    z2 = _opt(params, "z2", 0.0)
    if i <= 0:
        if z1 > 0 and z2 > 0:
            i = z2 / z1
        else:
            raise ValueError("fehlende Werte: i (Übersetzung) oder z1 und z2")
    n1 = _opt(params, "n", 0.0)
    t1 = _opt(params, "t", 0.0)
    eta = _opt(params, "eta", 0.97)
    annahmen = [f"Übersetzung i = {_n(i, 3)}"
                + (f" (aus z2/z1 = {_n(z2)}/{_n(z1)})" if z1 > 0 and z2 > 0 else ""),
                f"Wirkungsgrad η = {_n(eta, 3)}"]
    formeln = ["n2 = n1 / i", "M2 = M1 · i · η"]
    erg = [f"{'Untersetzung' if i > 1 else 'Übersetzung ins Schnelle'} i = {_n(i, 3)}"]
    if n1 > 0:
        annahmen.append(f"Antriebsdrehzahl n1 = {_n(n1)} 1/min")
        erg.append(f"Abtriebsdrehzahl n2 = {_n(n1 / i)} 1/min")
    if t1 > 0:
        annahmen.append(f"Antriebsmoment M1 = {_n(t1)} Nm")
        erg.append(f"Abtriebsmoment M2 = {_n(t1 * i * eta)} Nm")
    if n1 > 0 and t1 > 0:
        p1 = t1 * n1 / 9549.3
        erg.append(f"Leistung P1 = {_n(p1)} kW → P2 = {_n(p1 * eta)} kW "
                   f"(Verlust {_n(p1 * (1 - eta))} kW)")
    return _block("Getriebestufe", annahmen, formeln, erg)


#: Körper → (J-Formel, benötigte Maße, Formeltext)
_TRAEGHEIT: dict[str, tuple[Any, tuple[str, ...], str]] = {
    "vollzylinder": (lambda m, r, ri, l: 0.5 * m * r ** 2, ("m", "r"),
                     "J = ½·m·r²  (Rotation um die Zylinderachse)"),
    "hohlzylinder": (lambda m, r, ri, l: 0.5 * m * (r ** 2 + ri ** 2), ("m", "r", "r_i"),
                     "J = ½·m·(r_a² + r_i²)"),
    "stab_mitte":   (lambda m, r, ri, l: m * l ** 2 / 12.0, ("m", "l"),
                     "J = m·l²/12  (Stab um die Mitte, quer zur Achse)"),
    "stab_ende":    (lambda m, r, ri, l: m * l ** 2 / 3.0, ("m", "l"),
                     "J = m·l²/3  (Stab um ein Ende)"),
    "punktmasse":   (lambda m, r, ri, l: m * r ** 2, ("m", "r"),
                     "J = m·r²"),
}


def traegheitsmoment(params: dict) -> str:
    """Massenträgheitsmoment und (optional) Hochlaufzeit."""
    koerper = _txt(params, "koerper", "vollzylinder")
    if koerper not in _TRAEGHEIT:
        raise ValueError("koerper muss sein: " + ", ".join(_TRAEGHEIT))
    fn, need, formel = _TRAEGHEIT[koerper]
    _req(params, *need)                      # Pflichtmaße prüfen
    m = _opt(params, "m", 0.0)               # kg
    r = _opt(params, "r", 0.0) / 1000.0      # mm → m
    ri = _opt(params, "r_i", 0.0) / 1000.0
    l = _opt(params, "l", 0.0) / 1000.0
    j = fn(m, r, ri, l)
    annahmen = [f"Körper: {koerper}", f"Masse m = {_n(m)} kg"]
    if "r" in need:
        annahmen.append(f"Radius r = {_n(r * 1000)} mm")
    if "r_i" in need:
        annahmen.append(f"Innenradius r_i = {_n(ri * 1000)} mm")
    if "l" in need:
        annahmen.append(f"Länge l = {_n(l * 1000)} mm")
    formeln = [formel]
    erg = [f"Massenträgheitsmoment J = {_n(j, 4)} kg·m²"]
    n_ziel = _opt(params, "n", 0.0)
    t_nm = _opt(params, "t", 0.0)
    if n_ziel > 0 and t_nm > 0:
        omega = n_ziel * 2.0 * math.pi / 60.0
        t_hoch = j * omega / t_nm
        formeln.append("Hochlaufzeit t = J·ω/M mit ω = 2π·n/60")
        annahmen += [f"Zieldrehzahl n = {_n(n_ziel)} 1/min",
                     f"Beschleunigungsmoment M = {_n(t_nm)} Nm"]
        erg += [f"Winkelgeschwindigkeit ω = {_n(omega)} 1/s",
                f"Hochlaufzeit t = {_n(t_hoch, 3)} s",
                f"Rotationsenergie E = ½·J·ω² = {_n(0.5 * j * omega ** 2)} J"]
    return _block("Massenträgheitsmoment", annahmen, formeln, erg)


# ──────────────────────────────────────────────────────────────────────────────
# Registry + Einstiegspunkt für den Werkzeug-Loop
# ──────────────────────────────────────────────────────────────────────────────

#: kind → (Funktion, Kurzbeschreibung, Pflichtparameter, optionale Parameter)
CALCS: dict[str, tuple[Any, str, str, str]] = {
    # A — Festigkeit & Biegung
    "querschnitt": (querschnitt,
                    "Querschnittswerte A, I, W, i eines Profils",
                    "profil + Maße", "—"),
    "zug_druck": (zug_druck,
                  "Zug-/Druckspannung σ = F/A",
                  "f, a (oder profil + Maße)", "rp, s"),
    "schub": (schub,
              "Schubspannung τ = F/A inkl. Formfaktor",
              "f, a", "profil, rp, s"),
    "flaechenpressung": (flaechenpressung,
                         "Flächenpressung p = F/A (Bolzen/Auge: A = d·l)",
                         "f, a oder f, d, l", "p_zul"),
    "biegung": (biegung,
                "Biegespannung und Durchbiegung für Standardlastfälle",
                "fall, l, f oder q, profil + Maße (oder w, i)", "e, rp, s"),
    # B — Wellen, Torsion, Knickung
    "torsion": (torsion,
                "Torsionsspannung und Verdrehwinkel einer Welle",
                "t, d (oder wt, it)", "d_i, l, g, rp, s"),
    "wellendurchmesser": (wellendurchmesser,
                          "erforderlicher Wellendurchmesser aus Torsionsmoment",
                          "t, tau_zul", "—"),
    "vergleichsspannung": (vergleichsspannung,
                           "zusammengesetzte Beanspruchung (von Mises / Tresca)",
                           "sigma und/oder tau", "hypothese, alpha_0, rp, s"),
    "knickung": (knickung,
                 "Knicknachweis Euler (λ ≥ λ_g) bzw. Tetmajer",
                 "f, l, profil + Maße (oder i, a)", "fall, e, werkstoff, s"),
    # C — Verbindungen & Maschinenelemente
    "schweissnaht": (schweissnaht,
                     "Kehl-/Stumpfnaht: Nahtspannung und Auslastung",
                     "f, a, l", "art, n, v, rp, s"),
    "passfeder": (passfeder,
                  "Passfeder DIN 6885: Flächenpressung",
                  "t, d, h, l", "b, form, n, h_tr, p_zul"),
    "pressverband": (pressverband,
                     "Pressverband: übertragbares Moment bzw. nötiger Fugendruck",
                     "d, l + (p oder t)", "mu"),
    "druckfeder": (druckfeder,
                   "Schraubendruckfeder: Federrate und Schubspannung",
                   "d, d_m, n", "f oder s, g, tau_zul"),
    "waelzlager": (waelzlager,
                   "Wälzlager-Lebensdauer L10 / L10h (ISO 281)",
                   "c, p", "n, art, l_soll"),
    "zahnrad": (zahnrad,
                "Zahnrad-Geometrie, Umfangskraft, Lewis-Abschätzung",
                "m, z1", "z2, b, t, sigma_zul"),
    # D — Dauerfestigkeit & Antrieb
    "dauerfestigkeit": (dauerfestigkeit,
                        "Dauerfestigkeitsnachweis (Gestaltfestigkeit, Sicherheit)",
                        "sigma_a, rm", "sigma_m, art, kt, k_o, k_g, s"),
    "antrieb": (antrieb,
                "Leistung / Drehmoment / Drehzahl — den dritten Wert berechnen",
                "zwei von p, t, n", "eta"),
    "getriebestufe": (getriebestufe,
                      "Getriebestufe: n2 und M2 aus Übersetzung",
                      "i (oder z1, z2)", "n, t, eta"),
    "traegheitsmoment": (traegheitsmoment,
                         "Massenträgheitsmoment und Hochlaufzeit",
                         "koerper + Maße (m, r/l)", "r_i, n, t"),
}


# Lastfall-/Eulerfall-Varianten als eigene Rechenarten.
#
# Warum: gemessen mit granite4.2:3b ließ das Modell bei einer Kragarm-Frage den
# Schlüssel `fall` einfach weg — mit stillem Standardwert käme ein um den Faktor 4
# falsches Biegemoment heraus. Eine Auswahl aus einer Enum-Liste treffen kleine
# Modelle dagegen zuverlässig. Darum bekommt jeder Lastfall seinen eigenen
# `kind`-Wert; `fall` bleibt für CLI und große Modelle weiter nutzbar.
#: kind-Variante → (Basis-Rechenart, Feld, Wert)
_KIND_VORGABE: dict[str, tuple[str, str, str]] = {}
for _f in BIEGEFAELLE:
    _KIND_VORGABE[f"biegung_{_f}"] = ("biegung", "fall", _f)
for _nr in EULERFAELLE:
    _KIND_VORGABE[f"knickung_fall{_nr}"] = ("knickung", "fall", _nr)
# sprechende Zweitnamen für die Eulerfälle (so nennt sie der Nutzer)
for _alias, _nr in (("knickung_eingespannt_frei", "1"),
                    ("knickung_gelenkig", "2"),
                    ("knickung_eingespannt_gelenkig", "3"),
                    ("knickung_eingespannt_beidseitig", "4")):
    _KIND_VORGABE[_alias] = ("knickung", "fall", _nr)

#: alle gültigen kind-Werte (Basis + Varianten) — Quelle für das Tool-Enum
ALLE_KINDS: tuple[str, ...] = tuple(CALCS) + tuple(_KIND_VORGABE)


def catalog() -> str:
    """Alle Rechenarten als Markdown-Tabelle (für Tool-Beschreibung und Hilfe)."""
    zeilen = ["| kind | rechnet | Pflichtparameter | optional |",
              "|---|---|---|---|"]
    for k, (_, desc, pflicht, opt) in CALCS.items():
        zeilen.append(f"| `{k}` | {desc} | {pflicht} | {opt} |")
    zeilen.append("")
    zeilen.append("Profile für `profil`: " + ", ".join(sorted(PROFILE))
                  + " (Maße: " + "; ".join(f"{p}: {', '.join(d)}" for p, d in PROFILE.items()) + ")")
    zeilen.append("Lastfälle für `fall` (Biegung): " + ", ".join(BIEGEFAELLE))
    zeilen.append("Eulerfälle für `fall` (Knickung): "
                  + ", ".join(f"{k} = {v[1]}" for k, v in EULERFAELLE.items()))
    return "\n".join(zeilen)


KINDS = tuple(CALCS)


# ──────────────────────────────────────────────────────────────────────────────
# Eingabe-Aufbereitung — damit auch kleine Modelle (3–4B) treffen
# ──────────────────────────────────────────────────────────────────────────────
# Kleine lokale Modelle schreiben „kraft=2 kN" statt {"f": 2000} und verwechseln
# Einheiten. Deshalb wird jede Eingabe hier robust normalisiert, statt auf
# wohlgeformtes JSON zu hoffen. Gleiche Haltung wie der deterministische
# plot_function-Rückfall (siehe CLAUDE.md): das Werkzeug biegt es gerade.

#: Eindeutige Synonyme → interner Schlüssel (gelten für alle Rechenarten)
_SYN_GLOBAL: dict[str, str] = {
    "kraft": "f", "force": "f", "f_axial": "f", "fa": "f", "last": "f",
    "streckenlast": "q", "q_last": "q",
    "laenge": "l", "länge": "l", "length": "l", "lf": "l", "l_f": "l",
    "durchmesser": "d", "diameter": "d", "d_a": "d", "da": "d",
    "aussendurchmesser": "d", "außendurchmesser": "d", "d_f": "d",
    "innendurchmesser": "d_i", "di": "d_i", "d_innen": "d_i",
    "breite": "b", "width": "b",
    "hoehe": "h", "höhe": "h", "height": "h",
    "flaeche": "a", "fläche": "a", "area": "a", "querschnitt": "a",
    "streckgrenze": "rp", "re": "rp", "r_e": "rp", "rp02": "rp",
    "rp0_2": "rp", "sigma_zul": "rp",
    "zugfestigkeit": "rm", "r_m": "rm",
    "sicherheit": "s", "safety": "s", "s_soll": "s",
    "emodul": "e", "e_modul": "e", "e_mod": "e", "elastizitaetsmodul": "e",
    "schubmodul": "g", "g_modul": "g",
    "reibwert": "mu", "reibbeiwert": "mu", "reibungszahl": "mu", "my": "mu",
    "wirkungsgrad": "eta",
    "profiltyp": "profil", "querschnittsform": "profil", "form_profil": "profil",
    "steg": "t_s", "stegdicke": "t_s", "stegbreite": "t_s",
    "flansch": "t_f", "flanschdicke": "t_f", "gurt": "t_f", "gurtdicke": "t_f",
    "lastfall": "fall", "eulerfall": "fall",
    "tragzahl": "c", "c_dyn": "c",
    "uebersetzung": "i", "übersetzung": "i",
    "amplitude": "sigma_a", "mittelspannung": "sigma_m",
    "kerbwirkungszahl": "kt", "formzahl": "kt", "beta_k": "kt",
}

#: Mehrdeutige Begriffe — nur für die Rechenarten, bei denen sie eindeutig sind
_SYN_KIND: dict[str, dict[str, str]] = {
    "zahnrad": {"modul": "m", "zaehnezahl": "z1", "zähnezahl": "z1",
                "z": "z1", "drehmoment": "t", "moment": "t", "torque": "t",
                "zahnbreite": "b"},
    "traegheitsmoment": {"masse": "m", "gewicht": "m", "radius": "r",
                         "innenradius": "r_i", "drehzahl": "n",
                         "drehmoment": "t", "moment": "t"},
    "druckfeder": {"windungen": "n", "windungszahl": "n",
                   "drahtdurchmesser": "d", "windungsdurchmesser": "d_m",
                   "federweg": "s", "mittlerer_durchmesser": "d_m"},
    "waelzlager": {"drehzahl": "n", "lagerlast": "p", "last_p": "p",
                   "aequivalente_last": "p"},
    "antrieb": {"drehzahl": "n", "leistung": "p", "drehmoment": "t",
                "moment": "t", "torque": "t"},
    "getriebestufe": {"drehzahl": "n", "drehmoment": "t", "moment": "t"},
    "torsion": {"drehmoment": "t", "moment": "t", "torque": "t",
                "torsionsmoment": "t", "wellenlaenge": "l"},
    "wellendurchmesser": {"drehmoment": "t", "moment": "t", "torque": "t",
                          "torsionsmoment": "t", "zul": "tau_zul",
                          "p_zul": "tau_zul", "sigma_zul": "tau_zul",
                          "rp": "tau_zul", "tau": "tau_zul"},
    "passfeder": {"drehmoment": "t", "moment": "t", "torque": "t",
                  "federhoehe": "h", "federhöhe": "h", "federlaenge": "l",
                  "federbreite": "b", "anzahl": "n"},
    "pressverband": {"drehmoment": "t", "moment": "t", "fugendruck": "p",
                     "fugenlaenge": "l", "fugendurchmesser": "d"},
    "schweissnaht": {"nahtdicke": "a", "nahtlaenge": "l", "anzahl": "n",
                     "nahtart": "art", "guetebeiwert": "v"},
    "hohlkasten": {"wanddicke": "t", "dicke": "t"},
    "querschnitt": {"wanddicke": "t", "dicke": "t", "stegdicke": "t_s",
                    "flanschdicke": "t_f"},
    "biegung": {"wanddicke": "t", "dicke": "t", "stegdicke": "t_s",
                "flanschdicke": "t_f", "stuetzweite": "l", "spannweite": "l"},
    "knickung": {"wanddicke": "t", "dicke": "t", "stegdicke": "t_s",
                 "flanschdicke": "t_f", "stablaenge": "l", "knicklaenge": "l"},
    "dauerfestigkeit": {"oberflaechenbeiwert": "k_o", "groessenbeiwert": "k_g"},
}

#: Einheiten-Suffix → Faktor auf die interne Einheit (N, mm, MPa, Nm)
_EINHEITEN: list[tuple[str, float]] = [
    # Kräfte → N
    ("kn", 1000.0), ("mn", 1e6), ("n", 1.0),
    # Leistungen → kW (interne Einheit von 'p' bei antrieb/getriebestufe)
    ("kw", 1.0), ("w", 0.001), ("mw", 1000.0), ("ps", 0.7355),
    # Längen → mm
    ("mm", 1.0), ("cm", 10.0), ("dm", 100.0), ("m", 1000.0),
    # Spannungen → MPa
    ("n/mm2", 1.0), ("n/mm²", 1.0), ("mpa", 1.0), ("gpa", 1000.0),
    ("kpa", 0.001), ("bar", 0.1),
    # Momente → Nm
    ("nm", 1.0), ("knm", 1000.0), ("ncm", 0.01), ("nmm", 0.001),
]

#: Schlüssel, die eine Länge sind (für die m→mm-Umrechnung)
_IST_LAENGE = {"l", "d", "d_i", "b", "h", "t", "t_s", "t_f", "a", "d_m", "r", "r_i"}
#: Schlüssel, die eine Kraft sind
_IST_KRAFT = {"f", "c", "p"}
#: Schlüssel, die ein Moment in Nm sind
_IST_MOMENT = {"t"}
#: Schlüssel, die Zahlen ohne Einheit sind (Zählwerte, Faktoren)
_OHNE_EINHEIT = {"n", "z1", "z2", "m", "i", "eta", "mu", "s", "kt", "k_o", "k_g", "v",
                 "alpha_0"}
#: Schlüssel, die TEXT bleiben müssen — eine Umwandlung in float würde den
#: Schlüssel zerstören („fall=2" → 2.0 → "2.0" trifft den Eulerfall nicht mehr)
_IST_TEXT = {"fall", "profil", "art", "werkstoff", "hypothese", "koerper", "form"}


#: Einheiten, die eindeutig eine Drehzahl bezeichnen
_DREHZAHL_EINHEITEN = ("rpm", "1/min", "u/min", "umin", "min-1", "min^-1", "1/s", "upm")
#: Einheiten, die eindeutig ein Moment bezeichnen
_MOMENT_EINHEITEN = ("nm", "knm", "ncm", "nmm")
#: Einheiten, die eindeutig eine Leistung bezeichnen
_LEISTUNG_EINHEITEN = ("kw", "w", "ps", "mw")
#: Rechenarten, in denen 't' ein Drehmoment ist (NICHT Wanddicke!)
_T_IST_MOMENT = frozenset({
    "torsion", "wellendurchmesser", "passfeder", "pressverband",
    "zahnrad", "antrieb", "getriebestufe", "traegheitsmoment",
})
#: Rechenarten, in denen 'p' eine Leistung in kW ist
_P_IST_LEISTUNG = frozenset({"antrieb", "getriebestufe"})


#: Einheit → physikalische Dimension (für die Plausibilitätsprüfung)
_DIM_VON_EINHEIT: dict[str, str] = {
    "n": "kraft", "kn": "kraft", "mn": "kraft",
    "mm": "laenge", "cm": "laenge", "dm": "laenge", "m": "laenge",
    "mpa": "spannung", "gpa": "spannung", "kpa": "spannung", "bar": "spannung",
    "n/mm2": "spannung", "n/mm²": "spannung",
    "nm": "moment", "knm": "moment", "ncm": "moment", "nmm": "moment",
    "kw": "leistung", "w": "leistung", "mw": "leistung", "ps": "leistung",
}

#: Parameter → erwartete Dimension(en). Leere Menge = bewusst dimensionslos
#: (Zählwert/Faktor), fehlender Eintrag = keine Prüfung.
_DIM_ERWARTET: dict[str, set[str]] = {
    "f": {"kraft"}, "c": {"kraft"},
    "l": {"laenge"}, "d": {"laenge"}, "d_i": {"laenge"}, "b": {"laenge"},
    "h": {"laenge"}, "t_s": {"laenge"}, "t_f": {"laenge"}, "d_m": {"laenge"},
    "r": {"laenge"}, "r_i": {"laenge"}, "h_tr": {"laenge"},
    "sigma": {"spannung"}, "tau": {"spannung"}, "rp": {"spannung"},
    "rm": {"spannung"}, "tau_zul": {"spannung"}, "p_zul": {"spannung"},
    "sigma_a": {"spannung"}, "sigma_m": {"spannung"}, "sigma_zul": {"spannung"},
    "e": {"spannung"}, "g": {"spannung"},
    "z1": set(), "z2": set(), "i": set(), "eta": set(), "mu": set(),
    "s": set(), "kt": set(), "k_o": set(), "k_g": set(), "v": set(),
}

#: Rechenart-spezifische Abweichungen (dieselbe Taste, andere Größe)
_DIM_ERWARTET_KIND: dict[str, dict[str, set[str]]] = {
    "waelzlager":       {"p": {"kraft"}, "n": {"drehzahl"}},
    "pressverband":     {"p": {"spannung"}, "t": {"moment"}},
    "antrieb":          {"p": {"leistung"}, "t": {"moment"}, "n": {"drehzahl"}},
    "getriebestufe":    {"p": {"leistung"}, "t": {"moment"}, "n": {"drehzahl"}},
    "torsion":          {"t": {"moment"}},
    "wellendurchmesser": {"t": {"moment"}},
    "passfeder":        {"t": {"moment"}, "n": set()},
    "zahnrad":          {"t": {"moment"}, "m": set()},
    "traegheitsmoment": {"t": {"moment"}, "n": {"drehzahl"}, "m": set()},
    "querschnitt":      {"t": {"laenge"}},
    "biegung":          {"t": {"laenge"}},
    "knickung":         {"t": {"laenge"}},
    "druckfeder":       {"n": set()},
    "schweissnaht":     {"n": set(), "a": {"laenge"}},
    "flaechenpressung": {"p_zul": {"spannung"}},
}

#: Dimension → wie sie dem Nutzer/Modell genannt wird
_DIM_NAME = {"kraft": "Kraft (N, kN)", "laenge": "Länge (mm, cm, m)",
             "spannung": "Spannung (MPa, N/mm²)", "moment": "Moment (Nm, kNm)",
             "leistung": "Leistung (kW)", "drehzahl": "Drehzahl (1/min)"}


def _dim_erwartet(kind: str, key: str) -> set[str] | None:
    """Erwartete Dimension eines Parameters; None = keine Prüfung."""
    k = str(kind or "").lower()
    if k in _DIM_ERWARTET_KIND and key in _DIM_ERWARTET_KIND[k]:
        return _DIM_ERWARTET_KIND[k][key]
    return _DIM_ERWARTET.get(key)


def _ist_drehzahl_einheit(txt: str) -> bool:
    """True, wenn der Einheitenteil eine Drehzahl ist (dann gehoert der Wert zu n)."""
    e = str(txt).strip().lower().replace(" ", "")
    return e in _DREHZAHL_EINHEITEN


def _einheit_von(roh: str) -> str:
    """Einheitenteil eines Wertes ('1500 rpm' -> 'rpm')."""
    m = re.match(r"^\s*[+-]?\d+(?:[.,]\d+)?(?:[eE][+-]?\d+)?\s*(.*)$", str(roh))
    return (m.group(1).strip().lower().replace(" ", "") if m else "")


def _zahl_mit_einheit(key: str, roh: str) -> Any:
    """'2 kN' → 2000.0, '1,5 m' → 1500.0, '50x100' → unverändert (Textfall)."""
    if key in _IST_TEXT:
        return str(roh).strip().lower()
    txt = str(roh).strip().lower().replace("·", " ")
    if not txt:
        return roh
    # Dezimalkomma → Punkt (deutsche Schreibweise), Tausenderpunkt nicht anfassen
    txt = re.sub(r"(\d),(\d)", r"\1.\2", txt)
    m = re.match(r"^([+-]?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)\s*(.*)$", txt)
    if not m:
        return roh
    wert = float(m.group(1))
    einheit = m.group(2).strip().replace(" ", "")
    if not einheit or key in _OHNE_EINHEIT:
        return wert
    # passende Einheit suchen (längstes Suffix zuerst, damit 'knm' vor 'n' greift)
    for name, faktor in sorted(_EINHEITEN, key=lambda x: -len(x[0])):
        if einheit == name:
            if key in _IST_MOMENT and name in ("nm", "knm", "ncm"):
                return wert * faktor
            if key in _IST_LAENGE and name in ("mm", "cm", "dm", "m"):
                return wert * faktor
            if key in _IST_KRAFT and name in ("n", "kn", "mn"):
                return wert * faktor
            if name in ("mpa", "gpa", "n/mm2", "n/mm²", "kpa", "bar"):
                return wert * faktor
            return wert * faktor
    return wert


def _parse_pairs(text: str) -> list[tuple[str, str]]:
    """``"f=2 kN, l=1 m"`` → ``[("f", "2 kN"), ("l", "1 m")]``.

    Liefert bewusst eine **Liste von Paaren** und kein Dict: ein doppelt
    vergebener Schlüssel ginge beim Zusammenlegen sonst still verloren, bevor
    ``normalize`` ihn prüfen kann (gemessen: ``P=4 kN … p=1500 rpm`` löschte die
    Lagerlast). Trennt an Komma, Semikolon und Zeilenumbruch; Leerzeichen
    innerhalb eines Wertes bleiben erhalten, damit „2 kN" zusammenhält.
    """
    if not text:
        return []
    # WICHTIG zuerst: deutsches Dezimalkomma → Punkt. Sonst zerlegt der Split
    # unten „0,25" in „0" und „25" (das Komma ist auch Trennzeichen).
    txt = re.sub(r"(\d),(\d)", r"\1.\2", str(text))
    # an Komma/Semikolon/Newline trennen; zusätzlich an Leerzeichen VOR einem
    # „schluessel=" (so bleibt „f=2 kN" beisammen, „f=2 l=3" wird getrennt)
    roh = re.split(r"[,;\n]+|\s+(?=[A-Za-zÄÖÜäöü_][\w_äöüÄÖÜ]*\s*=)", txt)
    paare: list[tuple[str, str]] = []
    for teil in roh:
        teil = teil.strip()
        if not teil or "=" not in teil:
            continue
        k, v = teil.split("=", 1)
        k = k.strip().lower().replace(" ", "_")
        if k:
            paare.append((k, v.strip()))
    return paare


def parse_werte(text: str) -> dict:
    """``"f=2 kN, l=1 m"`` → Parameter-Dict (bequemer Zugang zu ``_parse_pairs``)."""
    return {k: v for k, v in _parse_pairs(text)}


def normalize(kind: str, params: Any) -> dict:
    """Synonyme auflösen, Einheiten umrechnen, Strings zu Zahlen machen."""
    if isinstance(params, str):
        paare: list[tuple[str, Any]] = list(_parse_pairs(params))
    elif isinstance(params, dict):
        paare = list(params.items())
    else:
        return {}
    syn = dict(_SYN_GLOBAL)
    syn.update(_SYN_KIND.get(str(kind or "").lower(), {}))
    out: dict[str, Any] = {}
    kollision: list[str] = []
    dimfehler: list[str] = []
    for k, v in paare:
        key = str(k).strip().lower().replace(" ", "_").replace("-", "_")
        key = syn.get(key, key)
        # Einheiten-Inferenz: die Einheit verrät die physikalische Größe
        # zuverlässiger als der Schlüsselname, den ein 3B-Modell vergibt.
        # Gemessene Fehlgriffe: „p=1500 rpm" überschrieb die Lagerlast P,
        # „l=250 Nm" legte das Drehmoment auf die Länge.
        if isinstance(v, str):
            _e = _einheit_von(v)
            if _ist_drehzahl_einheit(_e):
                key = "n"
            elif _e in _MOMENT_EINHEITEN and str(kind or "").lower() in _T_IST_MOMENT:
                key = "t"
            elif _e in _LEISTUNG_EINHEITEN and str(kind or "").lower() in _P_IST_LEISTUNG:
                key = "p"
        # Dimensionsprüfung: passt die angegebene Einheit überhaupt zu dem,
        # was dieser Parameter bedeutet? Ein Moment als Spannung einzusetzen
        # liefert sonst eine plausibel aussehende, falsche Zahl.
        if isinstance(v, str):
            _e2 = _einheit_von(v)
            _dim = ("drehzahl" if _ist_drehzahl_einheit(_e2)
                    else _DIM_VON_EINHEIT.get(_e2))
            _soll = _dim_erwartet(kind, key)
            if _dim and _soll is not None and _soll and _dim not in _soll:
                dimfehler.append(
                    f"'{key}' erwartet {' oder '.join(_DIM_NAME.get(x, x) for x in sorted(_soll))}"
                    f", bekam aber {_DIM_NAME.get(_dim, _dim)} ({v})"
                )
        if isinstance(v, str):
            v = _zahl_mit_einheit(key, v)
        if key in out and out[key] != v:
            # NICHT still ueberschreiben: zwei verschiedene Werte fuer denselben
            # Schluessel sind fast immer ein Modellfehler.
            kollision.append(f"{key} = {out[key]} und {v}")
        out[key] = v
    if kollision:
        out["__kollision__"] = "; ".join(kollision)
    if dimfehler:
        out["__dimfehler__"] = "; ".join(dimfehler)
    return out


def calc(kind: str, params: Any = None) -> str:
    """Einstiegspunkt: eine Rechenart ausführen.

    ``params`` darf ein Dict ODER ein String ``"f=1000 l=2 m"`` sein — kleine
    Modelle liefern zuverlässiger einen String als verschachteltes JSON.
    Synonyme und Einheiten werden vorher normalisiert (siehe ``normalize``).

    Gibt immer einen Text zurück — auch im Fehlerfall, dann mit der Angabe,
    welche Parameter fehlen. Das Modell im Werkzeug-Loop kann daraus direkt
    den korrigierten Aufruf bilden.
    """
    k = str(kind or "").strip().lower().replace("-", "_").replace(" ", "_")
    if k not in CALCS and k not in _KIND_VORGABE:
        # häufige Verwechslungen kleiner Modelle abfangen
        alias = {"flaechentraegheitsmoment": "querschnitt", "profil": "querschnitt",
                 "spannung": "zug_druck", "zug": "zug_druck", "druck": "zug_druck",
                 "welle": "torsion", "schraube": None, "feder": "druckfeder",
                 "lager": "waelzlager", "naht": "schweissnaht",
                 "biegespannung": "biegung", "durchbiegung": "biegung",
                 "knicken": "knickung", "euler": "knickung",
                 "leistung": "antrieb", "drehmoment": "antrieb",
                 "getriebe": "getriebestufe", "ermuedung": "dauerfestigkeit"}
        ziel = alias.get(k, "missing")
        if ziel is None:
            return ("**Schrauben laufen über ein anderes Werkzeug.** Für die "
                    "Schraubenauslegung nach VDI 2230 `bolt_calculator` benutzen "
                    "(Nenndurchmesser, Steigung, Axialkraft, Festigkeitsklasse).")
        if ziel != "missing":
            k = ziel
        else:
            # Tippfehler tolerieren (gemessen: 'biegung_trafer_mitte')
            treffer = difflib.get_close_matches(k, list(ALLE_KINDS), n=1, cutoff=0.75)
            if treffer:
                k = treffer[0]
            else:
                return ("**Unbekannte Rechenart** `" + str(kind) + "`\n\nVerfügbar:\n\n"
                        + catalog())
    # Lastfall-/Eulerfall-Variante zuerst auf die Basis-Rechenart abbilden,
    # damit der CALCS-Zugriff unten immer trifft.
    vorgabe = _KIND_VORGABE.get(k)
    feld_wert = None
    if vorgabe:
        k, feld, wert = vorgabe[0], vorgabe[1], vorgabe[2]
        feld_wert = (feld, wert)
    fn, desc, pflicht, opt = CALCS[k]
    params = normalize(k, params)
    if feld_wert:
        params[feld_wert[0]] = feld_wert[1]
    _dimf = params.pop("__dimfehler__", None)
    if _dimf:
        return ("**Einheit passt nicht zur Größe** — hier wurde eine physikalische "
                "Größe mit einer anderen verwechselt. Es wird bewusst nicht "
                "gerechnet, weil das Ergebnis sonst plausibel aussähe und falsch wäre.\n\n"
                f"- {_dimf}\n"
                f"- Pflichtparameter für `{k}`: {pflicht}\n"
                "- Prüfe, ob die richtige Rechenart gewählt ist (z. B. ein "
                "Torsionsmoment gehört zu `torsion`, nicht zu `vergleichsspannung`).")
    if params.pop("__kollision__", None):
        return ("**Widersprüchliche Eingaben** — derselbe Wert wurde zweimal "
                "unterschiedlich angegeben. Bitte eindeutig wiederholen.\n\n"
                f"- Pflichtparameter für `{k}`: {pflicht}\n"
                "- Einheiten: Kräfte N, Längen mm, Spannungen MPa, Momente Nm, "
                "Drehzahl 1/min (Drehzahl immer auf 'n').")
    try:
        return fn(params)
    except ValueError as e:
        return (f"**Eingaben unvollständig für `{k}`** ({desc})\n\n"
                f"- Problem: {e}\n"
                f"- Pflichtparameter: {pflicht}\n"
                f"- optional: {opt}\n\n"
                "Einheiten: Kräfte N, Längen mm, Spannungen MPa, Momente Nm, Drehzahl 1/min.")
    except ZeroDivisionError:
        return (f"**Division durch Null bei `{k}`** — ein Maß ist 0 "
                "(Fläche, Durchmesser oder Widerstandsmoment). Eingaben prüfen.")
    except Exception as e:  # defensiv: der Werkzeug-Loop darf nicht abbrechen
        return f"**Berechnung `{k}` fehlgeschlagen:** {type(e).__name__}: {e}"


# ──────────────────────────────────────────────────────────────────────────────
# CLI:  python -m tools.mechanik <kind> schluessel=wert …
# ──────────────────────────────────────────────────────────────────────────────

def _main(argv: list[str]) -> int:
    # stdout auf UTF-8 zwingen — sonst stirbt die CLI unter Windows-cp1252 am
    # ersten σ/λ/Umlaut (gleiche Falle wie z-image/sd_server.py, siehe CLAUDE.md).
    try:
        import sys as _s
        _s.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    if not argv or argv[0] in ("-h", "--help", "help"):
        print("Maschinenbau-Rechenkerne\n")
        print("Aufruf: python -m tools.mechanik <kind> schluessel=wert …\n")
        print(catalog())
        return 0
    kind, rest = argv[0], argv[1:]
    # genau derselbe Parser, den auch das Werkzeug benutzt
    print(calc(kind, " ".join(rest)))
    return 0


if __name__ == "__main__":
    import sys
    raise SystemExit(_main(sys.argv[1:]))
