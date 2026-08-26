# -*- coding: utf-8 -*-
"""Erzeugt die mitgelieferte Beispiel-Vorgangsdatei ``defaults/orchestrator_beispiel.json``.

Ein **kompletter /projekt-Orchestrator-Lauf** (Idee → Ablauf → morphologischer Kasten →
Paarvergleich → Plan → To-Do → Patent-Entwurf/Skizze → Dokumentation/Präsentation → Angebot)
im exakten Saved-Run-Schema von ``routers/orchestrator.py`` (``POST /api/orchestrator/save``):

    {version, saved_at, project_id, proposal{…alle Phasen…}, created}

Der **kreative Inhalt** (Texte, Morph-Parameter, Kriterien/Varianten/Urteile, Plan-Vorgänge,
To-Do, Patent, Folien) steht hier hart im Skript. Die **abgeleiteten** Felder werden mit den
ECHTEN deterministischen Funktionen berechnet, damit sie 1:1 dem entsprechen, was der
Live-Orchestrator erzeugt:

  * ``decision.result`` (AHP-Gewichte/CR/Ranking) via ``tools.decision`` — Aufbau der
    Paarvergleichsmatrix mit derselben ``_build_pairwise_matrix``-Logik wie im Router.
  * ``angebot`` (Positionen + Summen) via ``tools.dokumente`` (``plan_to_positions`` +
    ``compute_invoice`` + ``fmt_eur``).

Aufruf:  python scripts/build_orchestrator_example.py
Ergebnis: defaults/orchestrator_beispiel.json  (danach beim App-Start nach
          data/orchestrator/beispiel-vorgang.json ge-seedet → im Chat via /vorgang ladbar).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

from tools import decision as _decision   # noqa: E402
from tools import dokumente as _dok        # noqa: E402


# ── Kopie der Router-Logik (routers/orchestrator.py:_build_pairwise_matrix) ─────
# Bewusst dupliziert, damit dieser Build-Helfer nicht den ganzen Router (und damit
# core/db) importieren muss. Muss bei Änderungen im Router mitgezogen werden.
def _build_pairwise_matrix(names: list, judgments: list) -> list:
    n = len(names)
    idx = {nm.strip().lower(): i for i, nm in enumerate(names)}
    mat = [[1.0] * n for _ in range(n)]
    for j in (judgments or []):
        try:
            ia = idx.get(str(j.get("a", "")).strip().lower())
            ib = idx.get(str(j.get("b", "")).strip().lower())
            v = float(j.get("importance", 1) or 1)
        except Exception:
            continue
        if ia is None or ib is None or ia == ib:
            continue
        v = max(1.0 / 9.0, min(v, 9.0))
        mat[ia][ib] = v
        mat[ib][ia] = 1.0 / v
    return mat


# ── Fixe Metadaten (stabil, damit die committete Datei nicht bei jedem Lauf churnt) ─
SAVED_AT = 1787702400.0          # fixer Zeitstempel (~2026)
PROJECT_ID = "beispiel-vorgang"   # → _orch_safe(...) == "beispiel-vorgang" → Dateiname

PROJECT_NAME = "Solar-Ladestation SunTrack für E-Bikes"
BRIEF = (
    "Ich möchte eine autarke, wetterfeste Ladestation für E-Bikes entwickeln, die ihren Strom "
    "vollständig aus Sonnenenergie bezieht. Der Clou: das Solarpanel soll der Sonne PASSIV "
    "nachgeführt werden – ohne Motor, ohne Sensorik, ohne Elektronik –, indem ein temperatur"
    "abhängiger Bimetall-Aktor den Schwenkrahmen allein durch die Sonneneinstrahlung bewegt. "
    "Ein wechselbarer Akku-Puffer speichert die Energie für sonnenarme Zeiten. Bitte spiele das "
    "als kompletten Vorgang durch: Idee, Recherche, Skizze, Plan, To-Do, morphologischer Kasten "
    "und Paarvergleich, Patentrecherche, Angebot und eine Präsentation."
)


def build_proposal() -> dict:
    proposal: dict = {"brief": BRIEF, "doc_format": "beides"}

    # ── project + complexity ──────────────────────────────────────────────────
    proposal["project"] = {
        "name": PROJECT_NAME,
        "description": (
            "Autarke, wetterfeste Solar-Ladestation für E-Bikes mit passivem, elektronik- und "
            "motorlosem Sonnen-Nachführmodul (temperaturabhängiger Bimetall-Aktor) und "
            "wechselbarem Akku-Puffer. Die passive Nachführung ist das patentierbare Kernmerkmal."
        ),
    }
    proposal["complexity"] = 4   # komplex

    # ── flow (Ablaufdiagramm) ─────────────────────────────────────────────────
    steps = [
        "Bedarf und Standort analysieren",
        "Anforderungen und Lastprofil festlegen",
        "Konzeptvarianten im morphologischen Kasten sammeln",
        "Varianten im Paarvergleich bewerten",
        "Vorzugskonzept detailliert konstruieren",
        "Prototyp der passiven Nachfuehrung bauen",
        "Feldtest und Ertragsmessung durchfuehren",
        "Patententwurf und Recherche erstellen",
        "Dokumentation, Praesentation und Angebot erstellen",
    ]
    _ids = [chr(65 + i) for i in range(len(steps))]
    _mlines = ["flowchart TD"]
    for i, s in enumerate(steps):
        _mlines.append(f'  {_ids[i]}["{s}"]')
    for i in range(len(steps) - 1):
        _mlines.append(f"  {_ids[i]} --> {_ids[i + 1]}")
    proposal["flow"] = {"steps": steps, "mermaid": "\n".join(_mlines)}

    # ── morph (morphologischer Kasten) ────────────────────────────────────────
    proposal["morph"] = {"parameters": [
        {"name": "Energiequelle", "values": [
            "Monokristallines Panel", "Polykristallines Panel", "Duennschicht-Panel",
            "Panel + Kleinwindturbine"]},
        {"name": "Sonnen-Nachfuehrung", "values": [
            "Fest (keine Nachfuehrung)", "Passiv thermisch (Bimetall)",
            "Passiv Formgedaechtnis (SMA)", "Motorisch elektronisch"]},
        {"name": "Energiespeicher", "values": [
            "Fester LiFePO4-Puffer", "Wechselakku-Kassette", "Supercap-Puffer",
            "Kein Speicher (Direktladung)"]},
        {"name": "Ladeschnittstelle", "values": [
            "Universal-Steckdose 230V", "USB-C Power Delivery",
            "Herstellerspezifischer Stecker", "Induktive Ladeflaeche"]},
        {"name": "Gehaeuse/Schutz", "values": [
            "Stahl verzinkt", "Aluminium eloxiert", "GFK-Kunststoff", "Beton-Sockel + Alu"]},
        {"name": "Abrechnung", "values": [
            "Kostenlos/offen", "Muenz/Token", "App/QR-Bezahlung", "RFID-Karte"]},
    ]}

    # ── decision (Paarvergleich, AHP — deterministisch berechnet) ─────────────
    criteria = [
        {"name": "Investitionskosten", "direction": "cost"},
        {"name": "Energieertrag", "direction": "benefit"},
        {"name": "Wartungsarmut", "direction": "benefit"},
        {"name": "Robustheit/Wetterfestigkeit", "direction": "benefit"},
        {"name": "Patentierbarkeit/Neuheit", "direction": "benefit"},
    ]
    variants = [
        {"name": "Fest ohne Nachfuehrung",
         "description": "Starr montiertes Panel mit LiFePO4-Puffer — einfachste und guenstigste Loesung."},
        {"name": "Passive thermische Nachfuehrung",
         "description": "Ein Bimetall-Aktor schwenkt das Panel ohne Motor/Elektronik der Sonne nach — Vorzugskonzept."},
        {"name": "Motorische Nachfuehrung",
         "description": "Elektromotor mit Sensorik fuehrt aktiv nach — hoechster Ertrag, aber teuer und wartungsintensiv."},
    ]
    # Paarvergleichs-Urteile (Saaty: „a wichtiger als b"). Aus einem konsistenten
    # Zielprofil abgeleitet (Wartungsarmut/Ertrag/Patentierbarkeit hoch, Kosten niedrig).
    judgments = [
        {"a": "Energieertrag", "b": "Investitionskosten", "importance": 2},
        {"a": "Wartungsarmut", "b": "Investitionskosten", "importance": 2},
        {"a": "Patentierbarkeit/Neuheit", "b": "Investitionskosten", "importance": 2},
        {"a": "Energieertrag", "b": "Robustheit/Wetterfestigkeit", "importance": 2},
        {"a": "Wartungsarmut", "b": "Robustheit/Wetterfestigkeit", "importance": 2},
        {"a": "Patentierbarkeit/Neuheit", "b": "Robustheit/Wetterfestigkeit", "importance": 2},
    ]
    # Guete-Bewertungen [Variante][Kriterium] (1..10, 10 = am besten; bei Kosten: guenstigste=10).
    ratings = [
        [9, 5, 9, 8, 3],   # Fest ohne Nachfuehrung
        [6, 8, 8, 7, 9],   # Passive thermische Nachfuehrung
        [3, 9, 4, 6, 5],   # Motorische Nachfuehrung
    ]
    cnames = [c["name"] for c in criteria]
    pairwise_matrix = _build_pairwise_matrix(cnames, judgments)
    w = _decision.pairwise_weights(pairwise_matrix)
    sv = _decision.score_variants(w.get("weights") or [], ratings,
                                  [c["direction"] for c in criteria])
    result = {
        "weights": w.get("weights") or [],
        "cr": w.get("cr", 0.0),
        "consistent": w.get("consistent", True),
        "ranking": sv.get("ranking") or [],
        "best": sv.get("best"),
        "best_name": (variants[sv["best"]]["name"]
                      if sv.get("best") is not None and sv["best"] < len(variants) else ""),
    }
    proposal["decision"] = {
        "criteria": criteria, "variants": variants,
        "pairwise_matrix": pairwise_matrix,
        "ratings": [[float(x) for x in row] for row in ratings],
        "result": result,
    }

    # ── plan (CPM-faehige Vorgaenge mit Bereich + Ressourcen) ──────────────────
    tasks = [
        {"id": "T1", "name": "Standort- und Bedarfsanalyse", "duration": 3, "predecessors": [],
         "area": "Recherche", "roles": ["Projektingenieur"],
         "resource_list": [{"kind": "human", "name": "Projektingenieur", "qty": 1, "hours": 16, "rate": 85}]},
        {"id": "T2", "name": "Lastprofil und Anforderungen festlegen", "duration": 2, "predecessors": ["T1"],
         "area": "Planung", "roles": ["Projektingenieur"],
         "resource_list": [{"kind": "human", "name": "Projektingenieur", "qty": 1, "hours": 12, "rate": 85}]},
        {"id": "T3", "name": "Konzeptvarianten im morphologischen Kasten", "duration": 3, "predecessors": ["T2"],
         "area": "Planung", "roles": ["Konstrukteur"],
         "resource_list": [{"kind": "human", "name": "Konstrukteur", "qty": 1, "hours": 20, "rate": 80}]},
        {"id": "T4", "name": "Variantenbewertung (Paarvergleich)", "duration": 1, "predecessors": ["T3"],
         "area": "Planung", "roles": ["Projektleiter"],
         "resource_list": [{"kind": "human", "name": "Projektleiter", "qty": 1, "hours": 8, "rate": 95}]},
        {"id": "T5", "name": "Detailkonstruktion passives Nachfuehrmodul", "duration": 8, "predecessors": ["T4"],
         "area": "Konstruktion", "roles": ["Konstrukteur"],
         "resource_list": [{"kind": "human", "name": "Konstrukteur", "qty": 1, "hours": 60, "rate": 80}]},
        {"id": "T6", "name": "Materialbeschaffung (Panel, Bimetall, Akku)", "duration": 5, "predecessors": ["T5"],
         "area": "Beschaffung", "roles": ["Einkauf"],
         "resource_list": [
             {"kind": "human", "name": "Einkauf", "qty": 1, "hours": 10, "rate": 60},
             {"kind": "hardware", "name": "Panel/Bimetall/Wechselakku (Material)", "qty": 1, "hours": 0, "rate": 2400}]},
        {"id": "T7", "name": "Prototypfertigung und Montage", "duration": 6, "predecessors": ["T6"],
         "area": "Fertigung/Montage", "roles": ["Techniker"],
         "resource_list": [{"kind": "human", "name": "Techniker", "qty": 2, "hours": 80, "rate": 65}]},
        {"id": "T8", "name": "Feldtest und Ertragsmessung", "duration": 10, "predecessors": ["T7"],
         "area": "Pruefung/Qualitaetssicherung", "roles": ["Projektingenieur"],
         "resource_list": [{"kind": "human", "name": "Projektingenieur", "qty": 1, "hours": 40, "rate": 85}]},
        {"id": "T9", "name": "Patententwurf und Neuheitsrecherche", "duration": 4, "predecessors": ["T5"],
         "area": "Recherche", "roles": ["Patentingenieur"],
         "resource_list": [{"kind": "human", "name": "Patentingenieur", "qty": 1, "hours": 24, "rate": 110}]},
        {"id": "T10", "name": "Optimierung nach Feldtest", "duration": 4, "predecessors": ["T8"],
         "area": "Konstruktion", "roles": ["Konstrukteur"],
         "resource_list": [{"kind": "human", "name": "Konstrukteur", "qty": 1, "hours": 24, "rate": 80}]},
        {"id": "T11", "name": "Dokumentation und Praesentation", "duration": 3, "predecessors": ["T8", "T9"],
         "area": "Dokumentation", "roles": ["Technischer Redakteur"],
         "resource_list": [{"kind": "human", "name": "Technischer Redakteur", "qty": 1, "hours": 20, "rate": 70}]},
        {"id": "T12", "name": "Projektabschluss und Angebotserstellung", "duration": 2, "predecessors": ["T10", "T11"],
         "area": "Projektmanagement", "roles": ["Projektleiter"],
         "resource_list": [{"kind": "human", "name": "Projektleiter", "qty": 1, "hours": 12, "rate": 95}]},
    ]
    proposal["plan"] = {
        "name": "Solar-Ladestation SunTrack — Umsetzung",
        "description": "Einsatz- und Ressourcenplan von der Analyse bis zum Angebot.",
        "tasks": tasks,
        "resource_catalog": [
            {"kind": "human", "name": "Projektleiter", "rate": 95},
            {"kind": "human", "name": "Projektingenieur", "rate": 85},
            {"kind": "human", "name": "Konstrukteur", "rate": 80},
            {"kind": "human", "name": "Patentingenieur", "rate": 110},
            {"kind": "human", "name": "Techniker", "rate": 65},
            {"kind": "human", "name": "Einkauf", "rate": 60},
            {"kind": "human", "name": "Technischer Redakteur", "rate": 70},
            {"kind": "hardware", "name": "Panel/Bimetall/Wechselakku (Material)", "rate": 2400},
        ],
        "resource_mode": "free",
    }

    # ── todo ──────────────────────────────────────────────────────────────────
    proposal["todo"] = {
        "title": PROJECT_NAME,
        "items": [
            "Sonnenstands- und Verschattungsdaten fuer den Standort erfassen",
            "Taegliches Lastprofil (Anzahl Ladungen, Wh je Ladung) festlegen",
            "Morphologischen Kasten mit dem Team abstimmen",
            "Paarvergleich der Kriterien final bewerten und Vorzugsvariante bestaetigen",
            "Bimetall-Aktor auslegen (Auslenkung ueber Temperaturbereich)",
            "Schwenkrahmen und Lagerung konstruieren",
            "Wechselakku-Kassette und Laderegler auswaehlen",
            "Material bestellen und Prototyp aufbauen",
            "Feldtest planen (Messzeitraum, Ertragslogger)",
            "Patent-Stichworte und IPC-Klassen fuer die Recherche zusammenstellen",
            "Freedom-to-Operate grob pruefen (keine Einreichung)",
            "Dokumentation, Praesentation und Angebot erstellen",
        ],
    }

    # ── patente (Entwurf + Skizze; KEINE Einreichung) ─────────────────────────
    proposal["patente"] = {
        "title": "Autarke E-Bike-Ladestation mit passiver thermischer Sonnennachfuehrung",
        "claim1": (
            "Autarke Ladestation fuer Elektrofahrraeder, umfassend ein Solarpanel (1), einen das "
            "Solarpanel (1) tragenden Schwenkrahmen (2), einen Energiespeicher (5) und eine "
            "Ladeschnittstelle (6), dadurch gekennzeichnet, dass der Schwenkrahmen (2) mit einem "
            "temperaturabhaengigen, motor- und elektroniklosen Bimetall-Aktor (3) gekoppelt ist, "
            "der das Solarpanel (1) allein aufgrund der einstrahlungsbedingten Erwaermung selbsttaetig "
            "der Sonne nachfuehrt."
        ),
        "abstract": (
            "Die Erfindung betrifft eine solarbetriebene Ladestation fuer E-Bikes, deren Solarpanel "
            "ohne Motor, Sensorik oder Steuerelektronik der Sonne nachgefuehrt wird. Ein Bimetall-Aktor "
            "wandelt die einstrahlungsbedingte Erwaermung unmittelbar in eine Schwenkbewegung des "
            "Panelrahmens um. Ein wechselbarer Akku-Puffer speichert die Energie fuer sonnenarme Zeiten."
        ),
        "novelty": (
            "Bekannte Nachfuehrsysteme arbeiten motorisch mit Lichtsensoren und Steuerelektronik. Neu "
            "ist die rein passive, energieautarke Nachfuehrung ueber einen thermischen Aktor, die keinen "
            "Hilfsstrom, keine beweglichen Verschleissteile eines Antriebs und keine Elektronik benoetigt "
            "und damit besonders wartungsarm und ausfallsicher ist."
        ),
        "search_terms": [
            "solar tracking passive bimetal", "E-Bike Ladestation solar", "passive Nachfuehrung Solarpanel",
            "thermischer Aktor Solarmodul", "Formgedaechtnis Nachfuehrung", "self-tracking solar panel",
        ],
        "ipc": ["F24S30/00", "F24S50/20", "H02S20/32", "B60L53/52"],
        "note": "Entwurf/Recherche — keine amtliche Einreichung.",
        "figures": [
            {"caption": "Fig. 1: Schematischer Aufbau der Ladestation mit passivem Nachfuehrmodul",
             "mermaid": (
                 "flowchart LR\n"
                 '  1["Solarpanel (1)"] --> 2["Schwenkrahmen (2)"]\n'
                 '  3["Bimetall-Aktor (3)"] --> 2\n'
                 '  2 --> 4["Laderegler (4)"]\n'
                 '  4 --> 5["Wechselakku-Puffer (5)"]\n'
                 '  5 --> 6["Ladeschnittstelle (6)"]\n'
                 '  7["Wetterfestes Gehaeuse (7)"] --> 1'
             )},
        ],
        "bezugszeichen": [
            {"n": 1, "label": "Solarpanel"},
            {"n": 2, "label": "Schwenkrahmen"},
            {"n": 3, "label": "Bimetall-Aktor (passive Nachfuehrung)"},
            {"n": 4, "label": "Laderegler"},
            {"n": 5, "label": "Wechselakku-Puffer"},
            {"n": 6, "label": "Ladeschnittstelle"},
            {"n": 7, "label": "Wetterfestes Gehaeuse"},
        ],
        "figures_description": (
            "Fig. 1 zeigt den schematischen Aufbau: Das Solarpanel (1) ist auf einem Schwenkrahmen (2) "
            "gelagert, der von einem temperaturabhaengigen Bimetall-Aktor (3) ohne Motor oder Elektronik "
            "der Sonne nachgefuehrt wird. Der Laderegler (4) speist den wechselbaren Akku-Puffer (5), aus "
            "dem ueber die Ladeschnittstelle (6) E-Bikes geladen werden. Alle Komponenten sind im "
            "wetterfesten Gehaeuse (7) untergebracht."
        ),
    }

    # ── doku (Markdown + Praesentation; keine Base64-Bilder) ──────────────────
    markdown = (
        "# Solar-Ladestation SunTrack für E-Bikes\n\n"
        "## Ziel & Kontext\n"
        "Autarke, wetterfeste Ladestation für E-Bikes, die ihren Strom vollständig solar erzeugt. "
        "Kernidee ist eine **passive Sonnennachführung** ohne Motor, Sensorik oder Elektronik.\n\n"
        "## Lösungsansatz\n"
        "Ein temperaturabhängiger **Bimetall-Aktor** schwenkt den Panelrahmen allein durch die "
        "Sonneneinstrahlung. Ein **wechselbarer Akku-Puffer** überbrückt sonnenarme Zeiten.\n\n"
        "## Bewertete Varianten & Entscheidung\n"
        "- Fest ohne Nachführung — günstig, aber geringer Ertrag und wenig Neuheit\n"
        "- **Passive thermische Nachführung — Vorzugskonzept** (bester Nutzwert)\n"
        "- Motorische Nachführung — höchster Ertrag, aber teuer und wartungsintensiv\n\n"
        "Die gewichtete Bewertung (AHP-Paarvergleich) weist die passive thermische Nachführung "
        "als beste Variante aus.\n\n"
        "## Umsetzung / Vorgehen\n"
        "Analyse → Konzept (morphologischer Kasten) → Bewertung → Detailkonstruktion → Prototyp → "
        "Feldtest → Patententwurf → Dokumentation & Angebot.\n\n"
        "## Ausblick\n"
        "Skalierung zu einer Kleinserie, Feldvalidierung des Jahresertrags und Freedom-to-Operate-"
        "Prüfung vor einer möglichen Anmeldung.\n"
    )
    presentation = {
        "type": "presentation",
        "title": "Solar-Ladestation SunTrack",
        "theme": "dark",
        "slides": [
            {"layout": "title", "title": "Solar-Ladestation SunTrack",
             "content": "Autarke E-Bike-Ladestation mit passiver Sonnen-Nachführung"},
            {"layout": "bullets", "title": "Ziel & Kontext", "bullets": [
                "Autarke, wetterfeste Ladestation für E-Bikes",
                "Strom vollständig aus Sonnenenergie",
                "Passive Nachführung ohne Motor/Elektronik",
                "Wechselbarer Akku-Puffer für sonnenarme Zeiten"]},
            {"layout": "bullets", "title": "Lösungsansatz", "bullets": [
                "Bimetall-Aktor schwenkt das Panel thermisch der Sonne nach",
                "Kein Hilfsstrom, keine Sensorik, keine Steuerung",
                "Wartungsarm und ausfallsicher",
                "Laderegler + Wechselakku + Universal-Ladeschnittstelle"]},
            {"layout": "two-column", "title": "Varianten & Entscheidung",
             "left": ("- Fest ohne Nachführung\n- Passive thermische Nachführung (Vorzug)\n"
                      "- Motorische Nachführung"),
             "right": ("Gewichtete AHP-Bewertung: die passive thermische Nachführung erreicht den "
                       "besten Nutzwert — gutes Verhältnis aus Ertrag, Wartungsarmut und Neuheit.")},
            {"layout": "bullets", "title": "Umsetzung", "bullets": [
                "Detailkonstruktion des Nachführmoduls",
                "Prototypbau und Feldtest mit Ertragsmessung",
                "Patententwurf und Neuheitsrecherche",
                "Dokumentation, Präsentation und Angebot"]},
            {"layout": "section", "title": "Fazit"},
        ],
    }
    proposal["doku"] = {
        "format": "beides",
        "markdown": markdown,
        "presentation": presentation,
        "has_cover": False,
    }

    # ── angebot (deterministisch aus dem Plan — genau wie der Router) ─────────
    positionen = _dok.plan_to_positions(proposal["plan"])
    comp = _dok.compute_invoice({"positionen": positionen, "ust_satz": 19, "kleinunternehmer": False})
    proposal["angebot"] = {
        "positionen": positionen,
        "ust_satz": 19,
        "summe_netto": _dok.fmt_eur(comp["summe_netto"]),
        "ust_betrag": _dok.fmt_eur(comp["ust_betrag"]),
        "summe_brutto": _dok.fmt_eur(comp["summe_brutto"]),
    }

    return proposal


def main() -> int:
    record = {
        "version": 1,
        "saved_at": SAVED_AT,
        "project_id": PROJECT_ID,
        "proposal": build_proposal(),
        "created": {"plan_id": None, "var_name": None, "todo_pid": None,
                    "pat_name": None, "angebot_nr": None},
    }
    out = _ROOT / "defaults" / "orchestrator_beispiel.json"
    out.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    d = record["proposal"]["decision"]["result"]
    a = record["proposal"]["angebot"]
    print(f"[OK] geschrieben: {out.relative_to(_ROOT)}")
    print(f"     Kriterien-Gewichte: {d['weights']}")
    print(f"     CR = {d['cr']}  (konsistent: {d['consistent']})")
    print(f"     Sieger: {d['best_name']}  Ranking: "
          + ", ".join(f"{r['index']}:{r['percent']}%" for r in d["ranking"]))
    print(f"     Angebot: {len(a['positionen'])} Positionen, "
          f"netto {a['summe_netto']}, brutto {a['summe_brutto']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
