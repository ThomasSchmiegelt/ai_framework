# -*- coding: utf-8 -*-
"""Prüft das Beispielprojekt: CPM, Parallelität, kritischer Pfad/Puffer,
Ressourcenkonflikte (Überlast) und Lieferzeiten – wie das Frontend rechnet."""
import sys, httpx

PID = sys.argv[1] if len(sys.argv) > 1 else "f9e83970b76a"
plan = httpx.get(f"http://127.0.0.1:8780/api/plans/{PID}", timeout=30).json()
tasks = plan["tasks"]
by = {t["id"]: t for t in tasks}

# Kahn-Topo
indeg = {t["id"]: 0 for t in tasks}
for t in tasks:
    for p in t.get("predecessors", []):
        if p in by:
            indeg[t["id"]] += 1
queue = [t["id"] for t in tasks if indeg[t["id"]] == 0]
order, q = [], list(queue)
while q:
    i = q.pop(0); order.append(i)
    for s in by[i].get("successors", []):
        if s in by:
            indeg[s] -= 1
            if indeg[s] == 0:
                q.append(s)
cycle = [t["id"] for t in tasks if t["id"] not in order]

ES = {t["id"]: 0 for t in tasks}; EF = dict(ES)
for i in order:
    d = float(by[i].get("duration", 0) or 0)
    es = max([EF[p] for p in by[i].get("predecessors", []) if p in EF], default=0)
    ES[i] = es; EF[i] = es + d
projEnd = max(EF.values())
LF = {t["id"]: projEnd for t in tasks}; LS = dict(EF)
for i in reversed(order):
    d = float(by[i].get("duration", 0) or 0)
    succ = [s for s in by[i].get("successors", []) if s in LS]
    lf = min([LS[s] for s in succ], default=projEnd)
    LF[i] = lf; LS[i] = lf - d
flo = {i: LF[i] - EF[i] for i in by}
crit = [i for i in by if abs(flo[i]) < 1e-9]

# Parallelität: maximale Anzahl gleichzeitig laufender Aufgaben (dur>0)
events = []
for t in tasks:
    if float(t.get("duration", 0) or 0) > 0:
        events.append((ES[t["id"]], 1)); events.append((EF[t["id"]], -1))
events.sort(key=lambda e: (e[0], e[1]))
cur = mx = 0
for _, delta in events:
    cur += delta; mx = max(mx, cur)

# Ressourcenkonflikte (Mensch/Hardware, überlappende Fenster)
usage = {}
for t in tasks:
    for r in t.get("resource_list", []):
        if r.get("kind") == "software":
            continue
        usage.setdefault((r["kind"], r["name"].lower()), []).append((ES[t["id"]], EF[t["id"]], t["id"]))
conf = 0
for lst in usage.values():
    lst.sort()
    for a in range(len(lst)):
        for b in range(a + 1, len(lst)):
            if max(lst[a][0], lst[b][0]) < min(lst[a][1], lst[b][1]):
                conf += 1

lead_res = sum(1 for t in tasks for r in t.get("resource_list", []) if (r.get("lead") or 0) > 0)
total_cost = 0
for t in tasks:
    for r in t.get("resource_list", []):
        qty = r.get("qty", 0) or 0; h = r.get("hours", 0) or 0; rate = r.get("rate", 0) or 0
        total_cost += qty * h * rate if h > 0 else qty * rate

print(f"Aufgaben gesamt:        {len(tasks)}")
print(f"Zyklen:                 {len(cycle)}  (0 = gut)")
print(f"Projektdauer (ES-Tage): {projEnd:.0f}")
print(f"Kritischer Pfad:        {len(crit)} Aufgaben   (Rest hat Puffer)")
print(f"Aufgaben mit Puffer>0:  {sum(1 for i in by if flo[i] > 1e-9)}")
print(f"Max. Parallelität:      {mx} Aufgaben gleichzeitig")
print(f"Ressourcenkonflikte:    {conf}  (Doppelbelegung Mensch/HW)")
print(f"Ressourcen mit Lieferzeit: {lead_res}")
print(f"Geschätzte Gesamtkosten:   {total_cost:,.0f} EUR".replace(",", "."))
print("\nKritischer Pfad (Auszug):")
for i in [x for x in order if x in crit][:14]:
    print(f"  {i}: {by[i]['name'][:48]}  (Dauer {by[i].get('duration')})")
