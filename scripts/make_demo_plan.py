# -*- coding: utf-8 -*-
"""Erzeugt ein Beispielprojekt (100 Aufgaben) zum Thema „Einsatz lokaler KI in
einem Unternehmen". Die Netzplan-Struktur (viele parallele Arbeitspakete je Phase,
zusammengeführt über Meilenstein-Gates) wird deterministisch gebaut – so wird der
Nutzen der Netzplantechnik (kritischer Pfad, Puffer) deutlich. Die Aufgaben-NAMEN
liefert das lokale LLM ministral-3:3b. Es fällt zusätzlich eine importierbare
Beispiel-Ressourcenliste (CSV) ab.
"""
import json, re, random, sys
import httpx

OLLAMA = "http://localhost:11434"
APP    = "http://127.0.0.1:8780"
MODEL  = "ministral-3:3b"
random.seed(42)

CONTEXT = ("Einsatz lokaler, datenschutzkonformer KI (lokale LLMs auf eigener "
           "Hardware) in einem mittelständischen Unternehmen")

# (Phasenname, Anzahl paralleler Arbeitspakete)
PHASES = [
    ("Initiierung & Strategie", 5),
    ("Anforderungserhebung in den Fachbereichen", 8),
    ("Infrastruktur, Hardware & Beschaffung", 8),
    ("Datenmanagement & Datenaufbereitung", 8),
    ("Modell- und Werkzeugauswahl (lokale LLMs)", 7),
    ("Pilot-Use-Cases in den Abteilungen", 10),
    ("Integration & Schnittstellen zu Bestandssystemen", 8),
    ("Sicherheit, Datenschutz & Compliance", 7),
    ("Schulung & Change-Management", 6),
    ("Test & Qualitätssicherung", 6),
    ("Rollout an Standorten/Abteilungen", 7),
    ("Betrieb, Monitoring & Optimierung", 6),
]

# Ressourcen-Katalog: (kind, name, rate€, lead_tage)  lead nur bei HW/SW relevant
HUMANS = [
    ("Projektleiter", 95), ("KI-Architekt", 110), ("Data Engineer", 85),
    ("ML Engineer", 95), ("Data Scientist", 100), ("DevOps Engineer", 90),
    ("IT-Security-Experte", 105), ("Datenschutzbeauftragter", 100),
    ("Fachbereichsexperte", 75), ("Change Manager", 85), ("Trainer", 70),
    ("Systemadministrator", 75), ("Netzwerk-Ingenieur", 85),
    ("Qualitätsmanager", 80), ("Business Analyst", 80),
]
HARDWARE = [
    ("GPU-Server (2x H100)", 45000, 30), ("KI-Workstation", 6000, 14),
    ("Netzwerk-Upgrade (10GbE)", 18000, 21), ("Storage-Array (NAS)", 22000, 28),
    ("Edge-KI-Gerät", 3500, 20), ("USV & Server-Rack", 8000, 25),
]
SOFTWARE = [
    ("Vektordatenbank (Lizenz)", 5000, 7), ("MLOps-Plattform", 12000, 10),
    ("Monitoring-Suite", 4000, 5), ("Open-Source-LLM-Stack", 0, 0),
    ("Sicherheits-/DLP-Software", 9000, 14),
]
HRATE = {n: r for n, r in HUMANS}
HWMAP = {n: (r, l) for n, r, l in HARDWARE}
SWMAP = {n: (r, l) for n, r, l in SOFTWARE}


def ask_names(phase: str, k: int) -> list:
    """k realistische, unterschiedliche deutsche Aufgabennamen vom LLM."""
    prompt = (
        f"Projektkontext: {CONTEXT}.\n"
        f"Projektphase: \"{phase}\".\n"
        f"Nenne genau {k} konkrete, voneinander unabhängige Arbeitspakete dieser "
        f"Phase, die parallel bearbeitet werden können. Kurze Substantiv-Titel "
        f"(3–7 Wörter), keine Nummerierung.\n"
        f"Antworte NUR mit einem JSON-Array aus {k} Strings."
    )
    try:
        r = httpx.post(f"{OLLAMA}/api/chat", timeout=120, json={
            "model": MODEL, "think": False, "stream": False,
            "messages": [
                {"role": "system", "content": "Antworte ausschließlich mit einem gültigen JSON-Array von Strings."},
                {"role": "user", "content": prompt},
            ],
        })
        raw = r.json().get("message", {}).get("content", "")
        raw = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL)
        m = re.search(r"\[[\s\S]*\]", raw)
        names = json.loads(m.group(0)) if m else []
        names = [re.sub(r"^\s*\d+[.)]\s*", "", str(n)).strip().strip('"').strip()
                 for n in names if str(n).strip()]
    except Exception as e:
        print(f"   ! LLM-Fehler in Phase '{phase}': {e}")
        names = []
    # auf genau k bringen
    seen, out = set(), []
    for n in names:
        key = n.lower()
        if n and key not in seen:
            seen.add(key); out.append(n[:90])
        if len(out) == k:
            break
    i = 1
    while len(out) < k:
        out.append(f"{phase} – Arbeitspaket {i}"); i += 1
    return out


def human(name, dur, qty=1):
    return {"kind": "human", "name": name, "qty": qty, "hours": dur * 6,
            "rate": HRATE.get(name, 80), "lead": 0}

def hardware(name, qty=1):
    rate, lead = HWMAP.get(name, (1000, 14))
    return {"kind": "hardware", "name": name, "qty": qty, "hours": 0, "rate": rate, "lead": lead}

def software(name, qty=1):
    rate, lead = SWMAP.get(name, (1000, 7))
    return {"kind": "software", "name": name, "qty": qty, "hours": 0, "rate": rate, "lead": lead}


def resources_for(phase_idx, j, dur):
    """Ressourcen je paralleler Aufgabe. Der knappe KI-Architekt wird bewusst auf
    mehrere parallele Aufgaben gelegt → demonstriert die Überlast-Erkennung."""
    res = []
    rot = HUMANS[(phase_idx * 3 + j) % len(HUMANS)][0]
    res.append(human(rot, dur))
    # Engpass-Rolle teilen (erste 3 Parallelaufgaben mehrerer Phasen)
    if j < 3 and phase_idx in (1, 3, 5, 6):
        res.append(human("KI-Architekt", max(2, dur // 2)))
    # phasenspezifische HW/SW
    pname = PHASES[phase_idx][0]
    if "Infrastruktur" in pname:
        res.append(hardware(HARDWARE[j % len(HARDWARE)][0]))
    elif "Modell-" in pname or "Integration" in pname:
        res.append(software(SOFTWARE[j % len(SOFTWARE)][0]))
    elif "Pilot" in pname and j % 2 == 0:
        res.append(software("Open-Source-LLM-Stack"))
    elif "Sicherheit" in pname and j % 2 == 0:
        res.append(software("Sicherheits-/DLP-Software"))
    elif "Betrieb" in pname and j == 0:
        res.append(software("Monitoring-Suite"))
    return res


def build():
    tasks = []
    cid = 1
    def nid():
        nonlocal cid
        t = f"T{cid}"; cid += 1; return t

    # Start
    start_id = nid()
    tasks.append({"id": start_id, "name": "Projekt-Kickoff & KI-Zielbild", "duration": 3,
                  "predecessors": [], "successors": [], "resources": "",
                  "resource_list": [human("Projektleiter", 3), human("KI-Architekt", 3)],
                  "notes": "Projektstart – Zielbild, Scope, Stakeholder.",
                  "is_start": True, "is_end": False})

    prev_gate = start_id
    for pi, (phase, k) in enumerate(PHASES):
        print(f" » Phase {pi+1}/{len(PHASES)}: {phase} ({k} parallele Aufgaben) – LLM…")
        names = ask_names(phase, k)
        par_ids = []
        for j in range(k):
            tid = nid()
            # eine bewusst lange Aufgabe je Phase (Index 0) → klarer kritischer Pfad
            dur = random.choice([14, 16, 18]) if j == 0 else random.choice([3, 4, 5, 6, 8, 10])
            tasks.append({
                "id": tid, "name": names[j], "duration": dur,
                "predecessors": [prev_gate], "successors": [], "resources": "",
                "resource_list": resources_for(pi, j, dur),
                "notes": "", "is_start": False, "is_end": False,
            })
            par_ids.append(tid)
        # Meilenstein-Gate führt die Parallelstränge zusammen
        gid = nid()
        tasks.append({
            "id": gid, "name": f"Meilenstein: {phase} abgeschlossen", "duration": 0,
            "predecessors": par_ids, "successors": [], "resources": "",
            "resource_list": [], "notes": "Phasen-Quality-Gate (Meilenstein).",
            "is_start": False, "is_end": False,
        })
        prev_gate = gid

    # Ende
    end_id = nid()
    tasks.append({"id": end_id, "name": "Projektabschluss & Lessons Learned", "duration": 4,
                  "predecessors": [prev_gate], "successors": [], "resources": "",
                  "resource_list": [human("Projektleiter", 4), human("Qualitätsmanager", 4)],
                  "notes": "Abnahme, Doku, Lessons Learned.",
                  "is_start": False, "is_end": True})

    # Nachfolger aus Vorgängern ableiten
    by_id = {t["id"]: t for t in tasks}
    for t in tasks:
        for p in t["predecessors"]:
            if p in by_id and t["id"] not in by_id[p]["successors"]:
                by_id[p]["successors"].append(t["id"])
    return tasks


def main():
    tasks = build()
    assert len(tasks) == 100, f"Erwartet 100 Aufgaben, gebaut: {len(tasks)}"

    catalog = ([{"kind": "human", "name": n, "rate": r} for n, r in HUMANS]
               + [{"kind": "hardware", "name": n, "rate": r} for n, r, _ in HARDWARE]
               + [{"kind": "software", "name": n, "rate": r} for n, r, _ in SOFTWARE])

    plan = {
        "name": "Beispiel: Lokale KI im Unternehmen (100 Aufgaben, parallel)",
        "description": ("Beispielprojekt zur Einführung lokaler, datenschutzkonformer KI "
                        "(lokale LLMs auf eigener Hardware) in einem mittelständischen "
                        "Unternehmen. Bewusst stark parallelisiert über 12 Phasen mit "
                        "Meilenstein-Gates, damit kritischer Pfad und Puffer sichtbar werden."),
        "system_prompt": ("Du bist ein erfahrener Projektleiter für KI-Einführungsprojekte "
                          "in Unternehmen mit Fokus auf lokale, datenschutzkonforme LLMs."),
        "tasks": tasks,
        "resource_catalog": catalog,
        "resource_mode": "extend",
        "start_date": "2026-06-01",
        "workdays": True,
    }

    r = httpx.post(f"{APP}/api/plans", json=plan, timeout=60)
    r.raise_for_status()
    pid = r.json().get("id")
    print(f"\n✓ Plan gespeichert (id={pid}), {len(tasks)} Aufgaben.")

    # Beispiel-Ressourcenliste (importierbar: Typ;Name;Satz)
    out = "C:/Users/user/AI_Framework_Thomas/Beispiel_Ressourcenliste.csv"
    with open(out, "w", encoding="utf-8-sig", newline="") as f:
        f.write("Typ;Name;Satz\r\n")
        de = {"human": "Mensch", "hardware": "Hardware", "software": "Software"}
        for c in catalog:
            f.write(f"{de[c['kind']]};{c['name']};{c['rate']}\r\n")
    print(f"✓ Beispiel-Ressourcenliste: {out} ({len(catalog)} Ressourcen)")

    # Kurze Kennzahlen
    par = sum(1 for t in tasks if len(t["predecessors"]) <= 1 and not t["is_start"])
    print(f"  Phasen: {len(PHASES)} | Gates: {len(PHASES)} | parallele Arbeitspakete: 86")
    return pid


if __name__ == "__main__":
    main()
