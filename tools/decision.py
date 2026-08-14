"""Entscheidungslogik für den Tab „Variantenvergleich" (gewichtete Bewertung).

Reine Logik ohne FastAPI/DB-Bezug (analog ``tools/dokumente.py``). Setzt den
**Analytic-Hierarchy-Process (AHP)** in der Hybrid-Variante um:

- **Kriteriengewichte** aus einem **Paarvergleich** (Saaty-Skala 1–9): Gewichte
  per geometrischem Mittel der Zeilen, dazu der **Konsistenzindex** (CR) als
  Qualitätsmaß der Urteile.
- **Varianten** werden je Kriterium **direkt** auf einer Skala (Standard 1–10)
  bewertet; die gewichtete Nutzwertsumme ergibt das Ranking.

Sämtliche Zahlen werden **hier deterministisch** gerechnet — **nie vom LLM**,
damit das Ergebnis nachvollziehbar und reproduzierbar ist. Das LLM schlägt nur
Kriterien/Varianten/Urteile vor (in ``main.py``), die der Nutzer übernimmt oder
korrigiert.
"""

from __future__ import annotations

import math
from typing import Optional

# ── Saaty Random-Index (mittlerer Konsistenzindex zufälliger Matrizen) ─────────
# Quelle: Saaty, „The Analytic Hierarchy Process". Index n → RI. Für n>10
# extrapoliert (RI wächst asymptotisch gegen ~1,6); relevant sind kleine n.
_RANDOM_INDEX = {
    1: 0.0, 2: 0.0, 3: 0.58, 4: 0.90, 5: 1.12, 6: 1.24, 7: 1.32,
    8: 1.41, 9: 1.45, 10: 1.49, 11: 1.51, 12: 1.48, 13: 1.56,
    14: 1.57, 15: 1.59,
}


def _ri(n: int) -> float:
    if n in _RANDOM_INDEX:
        return _RANDOM_INDEX[n]
    return 1.59 if n > 15 else 0.0


def _clamp_saaty(v) -> float:
    """Ein Paarvergleichswert auf einen sinnvollen Bereich bringen.

    Saaty-Skala: 1..9 (Vorzug der Zeile) bzw. 1/9..1 (Vorzug der Spalte).
    Ungültige/leere Werte → 1 (Gleichstand), damit die Matrix robust bleibt."""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return 1.0
    if f <= 0 or math.isnan(f) or math.isinf(f):
        return 1.0
    # auf [1/9, 9] begrenzen
    return max(1.0 / 9.0, min(9.0, f))


def _normalize_pairwise(matrix: list) -> list:
    """Eine (evtl. unvollständige) Paarvergleichsmatrix säubern:
    quadratisch machen, Diagonale = 1, Reziprozität a_ji = 1/a_ij erzwingen.

    Es zählt die **obere Dreiecksmatrix** (die vom Nutzer gesetzten Werte); die
    untere wird daraus als Kehrwert abgeleitet, damit Widersprüche zwischen
    a_ij und a_ji gar nicht erst entstehen."""
    n = len(matrix or [])
    if n == 0:
        return []
    a = [[1.0] * n for _ in range(n)]
    for i in range(n):
        row = matrix[i] if i < len(matrix) else []
        for j in range(i + 1, n):
            v = _clamp_saaty(row[j] if j < len(row) else 1.0)
            a[i][j] = v
            a[j][i] = 1.0 / v
    return a


def pairwise_weights(matrix: list) -> dict:
    """Kriteriengewichte + Konsistenz aus einer Paarvergleichsmatrix.

    Rückgabe:
      weights      – Liste der normierten Gewichte (Summe 1), Reihenfolge wie Eingabe
      lambda_max   – Haupteigenwert (Näherung)
      ci           – Konsistenzindex (λmax−n)/(n−1)
      cr           – Konsistenzverhältnis CI/RI (n<3 ⇒ 0, per Definition konsistent)
      consistent   – True, wenn cr ≤ 0,10 (Saaty-Faustregel)
      n            – Dimension

    Methode: geometrisches Mittel je Zeile, dann normiert (robuste, verbreitete
    Näherung des Eigenvektors)."""
    a = _normalize_pairwise(matrix)
    n = len(a)
    if n == 0:
        return {"weights": [], "lambda_max": 0.0, "ci": 0.0, "cr": 0.0,
                "consistent": True, "n": 0}
    # geometrisches Mittel je Zeile
    gm = []
    for i in range(n):
        prod = 1.0
        for j in range(n):
            prod *= a[i][j]
        gm.append(prod ** (1.0 / n))
    total = sum(gm) or 1.0
    weights = [g / total for g in gm]

    # λmax über A·w / w (mittelt die Komponenten)
    if n < 3:
        lam = float(n)
        cr = 0.0
        ci = 0.0
    else:
        aw = [sum(a[i][j] * weights[j] for j in range(n)) for i in range(n)]
        ratios = [aw[i] / weights[i] for i in range(n) if weights[i] > 0]
        lam = sum(ratios) / len(ratios) if ratios else float(n)
        ci = (lam - n) / (n - 1)
        ri = _ri(n)
        cr = (ci / ri) if ri > 0 else 0.0

    return {
        "weights": [round(w, 6) for w in weights],
        "lambda_max": round(lam, 6),
        "ci": round(ci, 6),
        "cr": round(max(cr, 0.0), 6),
        "consistent": cr <= 0.10,
        "n": n,
    }


def equal_weights(n: int) -> list:
    """Gleichverteilte Gewichte (Rückfall, wenn kein Paarvergleich vorliegt)."""
    if n <= 0:
        return []
    return [round(1.0 / n, 6)] * n


def _num(v, default=0.0) -> float:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return default
    if math.isnan(f) or math.isinf(f):
        return default
    return f


def score_variants(weights: list, ratings: list, directions: Optional[list] = None,
                   scale_max: float = 10.0) -> dict:
    """Gewichtete Nutzwertsumme je Variante → Ranking.

    weights     – Kriteriengewichte (Länge = Anzahl Kriterien)
    ratings     – Matrix [Variante][Kriterium] mit Bewertungen (Standard 1..scale_max)
    directions  – optional je Kriterium 'benefit' (höher besser, Standard) oder
                  'cost' (niedriger besser → wird gespiegelt)
    scale_max   – oberes Skalenende (für die Kosten-Spiegelung und die Prozentanzeige)

    Rückgabe: scores (je Variante), ranking (Indizes + Score + Prozent, absteigend),
    best (Index der Siegervariante) oder None."""
    m = len(ratings or [])
    k = len(weights or [])
    if m == 0 or k == 0:
        return {"scores": [], "ranking": [], "best": None}

    dirs = directions or ["benefit"] * k
    scores = []
    for vi in range(m):
        row = ratings[vi] if vi < len(ratings) else []
        s = 0.0
        for ci in range(k):
            r = _num(row[ci] if ci < len(row) else 0.0)
            if (dirs[ci] if ci < len(dirs) else "benefit") == "cost":
                # Kosten spiegeln: hoher Rohwert = schlecht
                r = scale_max - r
            s += _num(weights[ci]) * r
        scores.append(round(s, 6))

    order = sorted(range(m), key=lambda i: scores[i], reverse=True)
    smax = max(scores) if scores else 0.0
    ranking = [{
        "index": i,
        "score": scores[i],
        "percent": round((scores[i] / smax * 100.0) if smax > 0 else 0.0, 1),
    } for i in order]
    return {"scores": scores, "ranking": ranking, "best": (order[0] if order else None)}


def sensitivity(weights: list, ratings: list, directions: Optional[list] = None,
                scale_max: float = 10.0, delta: float = 0.10) -> list:
    """Einfache Ein-Kriterium-Sensitivität: erhöht je Kriterium das Gewicht um
    ``delta`` (Rest proportional verkleinert) und meldet, ob die Siegervariante
    wechselt. Rein deterministisch — Grundlage für den KI-Kommentar, keine
    Bewertung.

    Rückgabe: Liste je Kriterium {criterion, flips, new_best} (new_best = neuer
    Sieger-Index nach der Auslenkung)."""
    k = len(weights or [])
    base = score_variants(weights, ratings, directions, scale_max)
    base_best = base.get("best")
    out = []
    for ci in range(k):
        w = list(weights)
        rest = sum(w) - w[ci]
        add = delta
        if rest <= 0:
            new_w = w
        else:
            w[ci] = w[ci] + add
            factor = (sum(weights) - (w[ci])) / rest if rest > 0 else 1.0
            new_w = [(w[j] if j == ci else weights[j] * factor) for j in range(k)]
        res = score_variants(new_w, ratings, directions, scale_max)
        out.append({
            "criterion": ci,
            "flips": res.get("best") != base_best,
            "new_best": res.get("best"),
        })
    return out
