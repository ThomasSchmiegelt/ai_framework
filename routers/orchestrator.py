"""Router: Projekt-Orchestrator (/api/orchestrator/*)

Ein einziger Prompt beschreibt ein Vorhaben; der Orchestrator zerlegt es phasenweise
über MEHRERE Tab-Fähigkeiten und streamt jede Phase als Vorschau (SSE). Das Frontend
(``chat.js`` ``/projekt``) legt danach auf Knopfdruck EIN Projekt an und verknüpft alle
Artefakte (Plan/To-Do/Varianten/…) per ``project_id``.

Design wie der ``/plan``-Orchestrator (``routers/plans.py`` ``_plan_strategy_generator``):
alle generativen Phasen sind EIGENE LLM-Aufrufe hier — der Orchestrator importiert KEINE
anderen Feature-Router (Projektregel: kein Router↔Router-Zyklus). Deterministische Mathe
kommt aus ``tools/*`` (erlaubt): ``tools.decision`` für die AHP-Gewichte/Bewertung.

Stage 1 (dieses Modul): project → morph → decision (Paarvergleich) → plan → todo.
Stage 2 (folgt): patente → doku (Präsentation+Dokument) → angebot.
"""
from __future__ import annotations

import asyncio
import json
import re
import time
from typing import List, Optional

import httpx
from fastapi import Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from fastapi import APIRouter

import db as _db
from tools import llm as _llm
from tools import decision as _decision
from tools import dokumente as _dok

from core import *  # noqa: F401,F403  (geteilte Kernflaeche)
import core as _core  # noqa: F401

router = APIRouter()


# Phasen der Reihe nach. Stage 1 deckt die ersten vier ab; die restlichen sind für
# Stage 2 vorgesehen und werden vorerst als „geplant"-Hinweis gemeldet.
_STAGE1_PHASES = ["project", "flow", "morph", "decision", "plan", "todo"]
_STAGE2_PHASES = ["patente", "doku", "angebot"]
_ALL_PHASES = _STAGE1_PHASES + _STAGE2_PHASES


class OrchestratorRequest(BaseModel):
    brief: str = ""                      # Vorhaben-Beschreibung (Prompt)
    extra: str = ""                      # optionale Randbedingungen
    model: Optional[str] = None
    phases: Optional[List[str]] = None   # Teilmenge von _ALL_PHASES (Standard: alle)
    plan_tasks: Optional[int] = None     # Ziel-Vorgangszahl; None ⇒ automatisch aus Komplexität
    doc_format: str = "beides"           # praesentation | dokument | beides


# Komplexitätsstufe (1–5) → sinnvolle Vorgangszahl im Plan.
_COMPLEXITY_TASKS = {1: 6, 2: 10, 3: 16, 4: 24, 5: 34}
_COMPLEXITY_LABEL = {1: "sehr einfach", 2: "einfach", 3: "mittel", 4: "komplex", 5: "sehr komplex"}


async def _llm_json(model: str, system: str, user: str, tok: dict) -> dict:
    """Ein LLM-Aufruf mit erzwungenem JSON + robustem Parsing (kleine Modelle wrappen
    JSON in <think>/Fences). Liefert {} bei Fehlschlag."""
    try:
        async with _model_session(model), httpx.AsyncClient(timeout=300) as client:
            resp = await _llm.chat(client, {
                "model": model, "think": False, "stream": False, "format": "json",
                "messages": [{"role": "system", "content": system},
                             {"role": "user", "content": user}],
                "options": {"num_ctx": _profile_num_ctx()}, "keep_alive": KEEP_ALIVE,
            })
            resp.raise_for_status()
        j = resp.json()
        a, b = _llm_tok(j)
        tok["in"] += a; tok["out"] += b
        return _parse_llm_json(j.get("message", {}).get("content", "")) or {}
    except Exception:
        return {}


async def _llm_md(model: str, system: str, user: str, tok: dict) -> str:
    """Ein LLM-Aufruf für Markdown-Text (ohne JSON-Zwang)."""
    try:
        async with _model_session(model), httpx.AsyncClient(timeout=300) as client:
            resp = await _llm.chat(client, {
                "model": model, "think": False, "stream": False,
                "messages": [{"role": "system", "content": system},
                             {"role": "user", "content": user}],
                "options": {"num_ctx": _profile_num_ctx()}, "keep_alive": KEEP_ALIVE,
            })
            resp.raise_for_status()
        j = resp.json()
        a, b = _llm_tok(j)
        tok["in"] += a; tok["out"] += b
        return re.sub(r"<think>.*?</think>", "", j.get("message", {}).get("content", ""),
                      flags=re.DOTALL).strip()
    except Exception:
        return ""


def _build_pairwise_matrix(names: list, judgments: list) -> list:
    """Aus Urteilen {a,b,importance} (Saaty 1–9: „a wichtiger als b") eine reziproke
    n×n-Matrix bauen. Fehlende Paare = 1 (gleich). Robuster als eine rohe Matrix vom LLM."""
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


async def _orchestrator_generator(req: OrchestratorRequest):
    brief = (req.brief or "").strip()
    extra = (req.extra or "").strip()
    if not brief:
        yield _sse({"type": "error", "message": "Keine Vorhaben-Beschreibung angegeben."})
        return
    model = _pick_model(req.model, _model_for("general"))
    phases = [p for p in (req.phases or _ALL_PHASES) if p in _ALL_PHASES] or _ALL_PHASES
    # Explizite Vorgangszahl übersteuert die automatische Ableitung aus der Komplexität.
    explicit_count = int(req.plan_tasks) if (req.plan_tasks and int(req.plan_tasks) > 0) else None
    count = max(4, min(explicit_count, 60)) if explicit_count else 16   # vorläufig; ggf. aus Komplexität
    task_text = brief + (f"\n\nRandbedingungen:\n{extra}" if extra else "")
    tok = {"in": 0, "out": 0}
    proposal: dict = {"brief": brief, "doc_format": req.doc_format}

    yield _sse({"type": "orchestrator_start", "phases": phases})

    # ── Phase: Projekt-Rahmen (Name/Beschreibung) + Komplexität → Vorgangszahl ─
    if "project" in phases:
        yield _sse({"type": "phase", "key": "project", "label": "🗂 Projektrahmen & Komplexität…"})
        d = await _llm_json(
            model,
            ("Du benennst ein Projekt aus einer Vorhaben-Beschreibung UND schätzt seine Komplexität "
             "(1=sehr einfach … 5=sehr komplex) für die spätere Detailtiefe des Projektplans. "
             'Antworte NUR mit JSON: {"name":"kurzer, prägnanter Projektname",'
             '"description":"1–2 Sätze Zusammenfassung","complexity":3}.'),
            task_text, tok)
        proposal["project"] = {
            "name": (str(d.get("name", "")).strip() or brief.split("\n", 1)[0][:60] or "Neues Projekt")[:120],
            "description": str(d.get("description", "")).strip()[:1000],
        }
        # Komplexität → Vorgangszahl (sofern der Aufrufer keine feste Zahl vorgab).
        try:
            complexity = int(d.get("complexity", 3) or 3)
        except Exception:
            complexity = 3
        complexity = max(1, min(complexity, 5))
        proposal["complexity"] = complexity
        if explicit_count is None:
            count = _COMPLEXITY_TASKS.get(complexity, 16)
        yield _sse({"type": "project", "project": proposal["project"]})
        yield _sse({"type": "complexity", "complexity": complexity,
                    "label": _COMPLEXITY_LABEL.get(complexity, ""), "plan_tasks": count,
                    "auto": explicit_count is None})

    # ── Phase: Ablaufdiagramm (Mermaid) — „was ist alles zu tun" ──────────────
    if "flow" in phases:
        yield _sse({"type": "phase", "key": "flow", "label": "🗺 Ablaufdiagramm wird erstellt…"})
        _pname = (proposal.get("project", {}) or {}).get("name", "") or brief[:60]
        d = await _llm_json(
            model,
            ("Du entwirfst den ABLAUF zur Umsetzung eines Vorhabens als Mermaid-Flussdiagramm. "
             "Liefere 5–9 aufeinander aufbauende Schritte (von der Analyse bis zum Ergebnis/Angebot). "
             "Gib das Diagramm als gültigen Mermaid-Code 'flowchart TD' mit Knoten A,B,C… und Pfeilen "
             "(A[\"Schritt\"] --> B[\"Schritt\"]) zurück, deutsche Kurzlabels ohne Sonderzeichen/Klammern "
             "im Label. Antworte NUR mit JSON: {\"steps\":[\"Schritt 1\",\"Schritt 2\"],\"mermaid\":\"flowchart TD\\n  A[...] --> B[...]\"}"),
            f"Vorhaben: {_pname}\n{task_text}", tok)
        steps = [str(s).strip() for s in (d.get("steps") or []) if str(s).strip()][:12]
        mermaid = str(d.get("mermaid", "") or "").strip()
        # Validierung/Rückfall: gültiges Mermaid muss mit flowchart/graph beginnen.
        if not re.match(r"^\s*(flowchart|graph)\b", mermaid):
            if steps:
                _ids = [chr(65 + i) for i in range(len(steps))]
                _lines = ["flowchart TD"]
                for i, s in enumerate(steps):
                    _lbl = re.sub(r'["\[\]()]', "", s)[:40]
                    _lines.append(f'  {_ids[i]}["{_lbl}"]')
                for i in range(len(steps) - 1):
                    _lines.append(f"  {_ids[i]} --> {_ids[i + 1]}")
                mermaid = "\n".join(_lines)
            else:
                mermaid = ""
        proposal["flow"] = {"steps": steps, "mermaid": mermaid}
        yield _sse({"type": "flow", "flow": proposal["flow"]})

    # ── Phase: Morphologischer Kasten (Zerlegung) ─────────────────────────────
    morph_params: list = []
    if "morph" in phases:
        yield _sse({"type": "phase", "key": "morph", "label": "🧩 Problem wird zerlegt (morphologischer Kasten)…"})
        d = await _llm_json(
            model,
            ("Du erstellst einen morphologischen Kasten (Zwicky-Box) für eine Aufgabenstellung. "
             "Bestimme 4–7 unabhängige Parameter (Merkmale einer Lösung) und je Parameter 3–5 "
             "konkrete Ausprägungen (kurze Stichworte, max. ~6 Wörter). Antworte NUR mit JSON: "
             '{"parameters":[{"name":"Parameter","values":["Ausprägung 1","Ausprägung 2"]}]}'),
            f"Aufgabenstellung:\n{task_text}", tok)
        for p in (d.get("parameters") or []):
            if isinstance(p, dict) and str(p.get("name", "")).strip():
                vals = [str(v).strip() for v in (p.get("values") or []) if str(v).strip()][:5]
                if vals:
                    morph_params.append({"name": str(p["name"]).strip()[:80], "values": vals})
        proposal["morph"] = {"parameters": morph_params}
        if not morph_params:
            yield _sse({"type": "notice", "message": "Zerlegung lieferte keine Parameter — Phase übersprungen."})
        yield _sse({"type": "morph", "morph": proposal["morph"]})

    # ── Phase: Paarvergleich-Bewertung (AHP, deterministische Gewichte) ───────
    if "decision" in phases:
        yield _sse({"type": "phase", "key": "decision", "label": "⚖ Kriterien & Paarvergleich werden bewertet…"})
        # Kriterien aus den Morph-Parametern vorschlagen (falls vorhanden), sonst frei.
        seed = ("Nutze bevorzugt diese Merkmale als Kriterien: "
                + ", ".join(p["name"] for p in morph_params) + ".\n") if morph_params else ""
        d = await _llm_json(
            model,
            ("Du bereitest eine gewichtete Entscheidung (AHP) für ein Vorhaben vor. Liefere "
             "4–6 Bewertungskriterien, 3–5 sinnvolle Lösungsvarianten und Paarvergleichs-Urteile. "
             "Ein Urteil sagt, wie viel WICHTIGER Kriterium a gegenüber b ist (Saaty 1=gleich, "
             "3=etwas, 5=deutlich, 7=stark, 9=extrem). Bewerte außerdem jede Variante je Kriterium "
             "als GÜTE 1–10 (10=am besten; bei Kosten: günstigste=10). Antworte NUR mit JSON: "
             '{"criteria":[{"name":"…","direction":"benefit|cost"}],'
             '"variants":[{"name":"…","description":"…"}],'
             '"pairwise":[{"a":"KriteriumA","b":"KriteriumB","importance":3}],'
             '"ratings":[{"variant":"…","scores":[{"criterion":"…","value":8}]}]}'),
            seed + f"Vorhaben:\n{task_text}", tok)

        criteria = [{"name": str(c.get("name", "")).strip()[:80],
                     "direction": (c.get("direction") if c.get("direction") in ("benefit", "cost") else "benefit")}
                    for c in (d.get("criteria") or []) if str(c.get("name", "")).strip()][:8]
        variants = [{"name": str(v.get("name", "")).strip()[:80],
                     "description": str(v.get("description", "")).strip()[:500]}
                    for v in (d.get("variants") or []) if str(v.get("name", "")).strip()][:8]
        cnames = [c["name"] for c in criteria]
        # AHP-Gewichte deterministisch aus den Urteilen.
        result = {}
        pairwise_matrix = []
        if len(cnames) >= 2:
            pairwise_matrix = _build_pairwise_matrix(cnames, d.get("pairwise") or [])
            w = _decision.pairwise_weights(pairwise_matrix)
            # Bewertungsmatrix [Variante][Kriterium] aus den benannten Scores.
            cidx = {c.lower(): i for i, c in enumerate(cnames)}
            ratings = []
            for v in variants:
                row = [5.0] * len(cnames)
                for r in (d.get("ratings") or []):
                    if str(r.get("variant", "")).strip().lower() == v["name"].lower():
                        for s in (r.get("scores") or []):
                            k = cidx.get(str(s.get("criterion", "")).strip().lower())
                            if k is not None:
                                try:
                                    row[k] = max(1.0, min(float(s.get("value", 5) or 5), 10.0))
                                except Exception:
                                    pass
                ratings.append(row)
            sv = _decision.score_variants(w.get("weights") or [], ratings,
                                          [c["direction"] for c in criteria]) if ratings else {}
            result = {"weights": w.get("weights") or [], "cr": w.get("cr", 0.0),
                      "consistent": w.get("consistent", True),
                      "ranking": sv.get("ranking") or [], "best": sv.get("best"),
                      "best_name": (variants[sv["best"]]["name"] if sv.get("best") is not None
                                    and sv["best"] < len(variants) else "")}
            proposal["decision"] = {"criteria": criteria, "variants": variants,
                                    "pairwise_matrix": pairwise_matrix, "ratings": ratings,
                                    "result": result}
        else:
            proposal["decision"] = {"criteria": criteria, "variants": variants,
                                    "pairwise_matrix": [], "ratings": [], "result": {}}
            yield _sse({"type": "notice", "message": "Zu wenige Kriterien für einen Paarvergleich."})
        yield _sse({"type": "decision", "decision": proposal["decision"]})

    # ── Phase: Projektplan (CPM-fähige Vorgänge) ──────────────────────────────
    if "plan" in phases:
        yield _sse({"type": "phase", "key": "plan", "label": "📅 Projektplan wird erstellt…"})
        best_hint = ""
        if proposal.get("decision", {}).get("result", {}).get("best_name"):
            best_hint = ("\nBevorzugte Lösungsvariante aus der Bewertung: "
                         + proposal["decision"]["result"]["best_name"] + ".")
        sys_p = (
            "Du bist ein erfahrener Projektplaner. Erstelle einen Einsatz- und Ressourcenplan in "
            "sinnvollen Phasen. Vergib fortlaufende IDs T1, T2, …. Jede Aufgabe hat: id, name, "
            "duration (Tage), predecessors (Liste direkter Vorgänger-IDs; die erste hat []), area "
            "(Projektphase), roles (zuständige Rollen) und resource_list (kind human|hardware|software, "
            "name, qty, hours (bei Hardware/Software 0), rate €). Füge resource_catalog (Rollen/Ressourcen "
            "+ Kostensätze) an. Antworte NUR mit JSON:\n"
            '{"name":"Projektname","description":"…","tasks":[{"id":"T1","name":"…","duration":3,'
            '"predecessors":[],"area":"Vorbereitung","roles":["Projektleiter"],'
            '"resource_list":[{"kind":"human","name":"Projektleiter","qty":1,"hours":16,"rate":90}]}],'
            '"resource_catalog":[{"kind":"human","name":"Projektleiter","rate":90}]}\n'
            f"Erzeuge möglichst genau {count} Aufgaben mit echten Abhängigkeiten."
        )
        d = await _llm_json(model, sys_p, task_text + best_hint, tok)
        tasks = d.get("tasks") or []
        # Vorgänger auf existierende IDs beschränken; leere Namen raus.
        ids = {str(t.get("id", "")).strip() for t in tasks if isinstance(t, dict)}
        norm_tasks = []
        for t in tasks:
            if not isinstance(t, dict) or not str(t.get("name", "")).strip():
                continue
            preds = [p for p in (t.get("predecessors") or []) if str(p).strip() in ids]
            norm_tasks.append({
                "id": str(t.get("id", "")).strip() or f"T{len(norm_tasks) + 1}",
                "name": str(t.get("name", "")).strip()[:200],
                "duration": max(1, int(t.get("duration", 1) or 1)),
                "predecessors": preds,
                "area": str(t.get("area", "")).strip()[:80],
                "roles": [str(r).strip() for r in (t.get("roles") or []) if str(r).strip()],
                "resource_list": t.get("resource_list") or [],
            })
        norm_tasks = norm_tasks[:count]
        proposal["plan"] = {
            "name": (str(d.get("name", "")).strip() or proposal.get("project", {}).get("name", "") or "Projektplan")[:120],
            "description": str(d.get("description", "")).strip()[:1000],
            "tasks": norm_tasks,
            "resource_catalog": d.get("resource_catalog") or [],
            "resource_mode": "free",
        }
        if not norm_tasks:
            yield _sse({"type": "notice", "message": "Plan lieferte keine Vorgänge."})
        yield _sse({"type": "plan", "plan": proposal["plan"]})

    # ── Phase: To-Do-Liste ────────────────────────────────────────────────────
    if "todo" in phases:
        yield _sse({"type": "phase", "key": "todo", "label": "✅ To-Do-Liste wird abgeleitet…"})
        plan_hint = ""
        if proposal.get("plan", {}).get("tasks"):
            plan_hint = ("\nOrientiere dich an diesen Planaufgaben:\n"
                         + "\n".join(f"- {t['name']}" for t in proposal["plan"]["tasks"][:30]))
        d = await _llm_json(
            model,
            ("Du erstellst eine konkrete, umsetzbare To-Do-Liste für ein Vorhaben. Liefere 6–20 "
             "kurze, klar formulierte Aufgabenpunkte (Verb + Objekt). Antworte NUR mit JSON: "
             '{"items":["Aufgabe 1","Aufgabe 2"]}'),
            f"Vorhaben:\n{task_text}{plan_hint}", tok)
        items = [str(x).strip()[:200] for x in (d.get("items") or []) if str(x).strip()][:40]
        proposal["todo"] = {"title": proposal.get("project", {}).get("name", "") or "Aufgaben",
                            "items": items}
        yield _sse({"type": "todo", "todo": proposal["todo"]})

    best_name = (proposal.get("decision", {}).get("result", {}) or {}).get("best_name", "")
    best_line = (f"\nBevorzugte Lösungsvariante aus der Bewertung: {best_name}." if best_name else "")

    # ── Phase: Patent-Entwurf & Neuheitsanalyse (KEIN Einreichen!) ────────────
    if "patente" in phases:
        yield _sse({"type": "phase", "key": "patente", "label": "⚖ Patent-Entwurf & Neuheitsanalyse…"})
        try:
            d = await _llm_json(
                model,
                ("Du bist Patentingenieur und erstellst einen ENTWURF (keine amtliche Einreichung) "
                 "für eine mögliche Patentanmeldung zu einem Vorhaben. Liefere: einen Titel, einen "
                 "Hauptanspruch (Anspruch 1, ein Satz, merkmalsgegliedert), eine Kurzzusammenfassung "
                 "(Abstract), eine kurze Neuheits-/erfinderische-Tätigkeit-Argumentation und "
                 "vorgeschlagene Recherche-Stichworte sowie IPC/CPC-Klassen. Antworte NUR mit JSON: "
                 '{"title":"…","claim1":"…","abstract":"…","novelty":"…",'
                 '"search_terms":["…"],"ipc":["B60L"]}'),
                f"Vorhaben:\n{task_text}{best_line}", tok)
            proposal["patente"] = {
                "title": str(d.get("title", "")).strip()[:200],
                "claim1": str(d.get("claim1", "")).strip()[:2000],
                "abstract": str(d.get("abstract", "")).strip()[:2000],
                "novelty": str(d.get("novelty", "")).strip()[:2000],
                "search_terms": [str(x).strip() for x in (d.get("search_terms") or []) if str(x).strip()][:10],
                "ipc": [str(x).strip() for x in (d.get("ipc") or []) if str(x).strip()][:8],
                "note": "Entwurf/Recherche — keine amtliche Einreichung.",
            }
        except Exception as e:
            proposal["patente"] = {}
            yield _sse({"type": "notice", "message": f"Patent-Entwurf übersprungen: {e}"})
        # Skizzen ergänzen: Schema-Figuren mit Bezugszeichen (+ optional KI-Konzeptbild).
        if proposal.get("patente"):
            try:
                yield _sse({"type": "phase", "key": "patente", "label": "🖼 Patent-Skizzen (Figuren + Bezugszeichen)…"})
                figs = await _patent_figures_core(
                    task_text, claim1=proposal["patente"].get("claim1", ""),
                    model=model, want_image=True, n=2)
                proposal["patente"]["figures"] = figs.get("figures") or []
                proposal["patente"]["bezugszeichen"] = figs.get("bezugszeichen") or []
                proposal["patente"]["figures_description"] = figs.get("description") or ""
                _ft = figs.get("tokens") or {}
                tok["in"] += int(_ft.get("in", 0) or 0); tok["out"] += int(_ft.get("out", 0) or 0)
            except Exception as e:
                yield _sse({"type": "notice", "message": f"Patent-Skizzen übersprungen: {e}"})
        yield _sse({"type": "patente", "patente": proposal.get("patente") or {}})

    # ── Phase: Dokumentation (Markdown) + Präsentation (+ optional Titelbild) ──
    if "doku" in phases:
        yield _sse({"type": "phase", "key": "doku", "label": "📄 Bebilderte Dokumentation wird erstellt…"})
        doc_md = ""
        pres = None
        try:
            doc_md = await _llm_md(
                model,
                ("Du schreibst eine strukturierte Projekt-Dokumentation als Markdown (## Überschriften, "
                 "**Fett**, Aufzählungen/Tabellen wo sinnvoll). Gliederung: Ziel & Kontext, Lösungsansatz, "
                 "Bewertete Varianten & Entscheidung, Umsetzung/Vorgehen, Ausblick. Konkret und sachlich."),
                (f"Vorhaben:\n{task_text}{best_line}\n\n"
                 + (("Bewertete Varianten: " + ", ".join(v["name"] for v in proposal.get("decision", {}).get("variants", []))) if proposal.get("decision") else "")),
                tok)
            if doc_md:
                pres = await _text_to_presentation(doc_md, model, tok=tok)
        except Exception as e:
            yield _sse({"type": "notice", "message": f"Dokumentation teilweise fehlgeschlagen: {e}"})
        # Optionales Titelbild (best-effort; scheitert lautlos, z. B. ohne Bildmodell/SD-Server).
        cover = ""
        if pres and _image_model():
            try:
                yield _sse({"type": "phase", "key": "doku", "label": "🖼 Titelbild wird erzeugt…"})
                _pname = (proposal.get("project", {}) or {}).get("name", "") or brief
                _ip = (f"Professionelles, sauberes Titelbild für eine Projekt-Dokumentation zum Thema: "
                       f"{_pname}. Moderne, sachliche Bildsprache, ohne Text.")
                _img = await _generate_image_core(_ip, preset="landscape")
                cover = _img.get("image", "") if isinstance(_img, dict) else ""
                if cover and pres.get("slides"):
                    pres["slides"][0]["image"] = cover
            except Exception:
                cover = ""
        proposal["doku"] = {"format": req.doc_format, "markdown": doc_md,
                            "presentation": pres or None, "has_cover": bool(cover)}
        yield _sse({"type": "doku", "doku": {
            "format": req.doc_format,
            "has_markdown": bool(doc_md),
            "n_slides": len((pres or {}).get("slides") or []) if pres else 0,
            "has_cover": bool(cover),
            "presentation": pres or None,
            "markdown": doc_md,
        }})

    # ── Phase: Angebot (deterministisch aus dem Plan; erstellen, NICHT senden) ─
    if "angebot" in phases:
        yield _sse({"type": "phase", "key": "angebot", "label": "🧾 Angebot wird kalkuliert…"})
        plan = proposal.get("plan") or {}
        if plan.get("tasks"):
            try:
                positionen = _dok.plan_to_positions(plan)
                comp = _dok.compute_invoice({"positionen": positionen, "ust_satz": 19,
                                             "kleinunternehmer": False})
                proposal["angebot"] = {
                    "positionen": positionen, "ust_satz": 19,
                    "summe_netto": _dok.fmt_eur(comp["summe_netto"]),
                    "ust_betrag": _dok.fmt_eur(comp["ust_betrag"]),
                    "summe_brutto": _dok.fmt_eur(comp["summe_brutto"]),
                }
            except Exception as e:
                proposal["angebot"] = {}
                yield _sse({"type": "notice", "message": f"Angebot nicht kalkulierbar: {e}"})
        else:
            proposal["angebot"] = {}
            yield _sse({"type": "notice", "message": "Kein Plan mit bepreisten Vorgängen → kein Angebot."})
        yield _sse({"type": "angebot", "angebot": proposal.get("angebot") or {}})

    yield _sse({"type": "proposal", "proposal": proposal})
    yield _sse({"type": "done", "tokens": tok})


@router.post("/api/orchestrator/plan")
async def orchestrator_plan(req: OrchestratorRequest):
    return StreamingResponse(
        _orchestrator_generator(req),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ── Persistenz: Vorgang (proposal) als JSON speichern — unter dem Projekt und
# optional als wiederverwendbare Vorlage. Damit „weiß" das Tool, was zu tun war,
# und der komplette Durchlauf ist reproduzierbar/nachvollziehbar. ─────────────
_ORCH_TPL_DIR = ORCHESTRATOR_DIR / "_templates"


def _orch_safe(name: str) -> str:
    safe = "".join(c for c in str(name or "") if c.isalnum() or c in (" ", "_", "-")).strip()
    return (safe or "vorgang")[:80]


class OrchestratorSaveRequest(BaseModel):
    project_id: str = ""
    proposal: dict = {}
    created: dict = {}                    # was tatsächlich angelegt wurde (IDs)
    save_as_template: bool = False
    template_name: str = ""


@router.post("/api/orchestrator/save")
async def orchestrator_save(req: OrchestratorSaveRequest):
    """Speichert den kompletten Vorgang als JSON unter dem Projekt (und optional als
    Vorlage). Rein Datei-basiert, kein LLM."""
    if not (req.proposal or {}):
        raise HTTPException(status_code=400, detail="Kein Vorgang (proposal) übergeben.")
    ORCHESTRATOR_DIR.mkdir(parents=True, exist_ok=True)
    record = {
        "version": 1,
        "saved_at": time.time(),
        "project_id": (req.project_id or "").strip(),
        "proposal": req.proposal,
        "created": req.created or {},
    }
    pid = (req.project_id or "").strip() or _orch_safe(
        (req.proposal.get("project") or {}).get("name", "") or "vorgang")
    path = ORCHESTRATOR_DIR / f"{_orch_safe(pid)}.json"
    path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    tpl_name = ""
    if req.save_as_template:
        _ORCH_TPL_DIR.mkdir(parents=True, exist_ok=True)
        tpl_name = _orch_safe(req.template_name or (req.proposal.get("project") or {}).get("name", "") or "vorlage")
        # Vorlage = der Ablauf (flow) + Struktur, ohne die konkreten Ergebnisse aufzublähen.
        tpl = {
            "version": 1, "saved_at": time.time(), "name": tpl_name,
            "brief": req.proposal.get("brief", ""),
            "complexity": req.proposal.get("complexity"),
            "flow": req.proposal.get("flow") or {},
            "phases": [k for k in _ALL_PHASES if k in (req.proposal or {})],
        }
        (_ORCH_TPL_DIR / f"{tpl_name}.json").write_text(
            json.dumps(tpl, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"ok": True, "path": path.name, "template": tpl_name}


@router.get("/api/orchestrator/runs")
async def orchestrator_runs():
    """Alle gespeicherten Vorgänge auflisten (Gegenstück zu ``save``). Rein lesend.
    Muss VOR ``/runs/{project_id}`` stehen (Literal-vor-Parametrik-Regel)."""
    out = []
    if ORCHESTRATOR_DIR.exists():
        for f in sorted(ORCHESTRATOR_DIR.glob("*.json")):
            try:
                d = json.loads(f.read_text(encoding="utf-8"))
            except Exception:
                continue
            prop = d.get("proposal") or {}
            phases = [k for k in _ALL_PHASES if k in prop]
            out.append({
                "project_id": d.get("project_id") or f.stem,
                "file": f.stem,
                "name": (prop.get("project") or {}).get("name") or f.stem,
                "brief": (prop.get("brief") or "")[:200],
                "phases": phases,
                "saved_at": d.get("saved_at", 0),
            })
    out.sort(key=lambda x: x.get("saved_at", 0), reverse=True)
    return out


@router.get("/api/orchestrator/runs/{project_id}")
async def orchestrator_run_get(project_id: str):
    path = ORCHESTRATOR_DIR / f"{_orch_safe(project_id)}.json"
    if not path.exists():
        raise HTTPException(status_code=404, detail="Kein gespeicherter Vorgang für dieses Projekt.")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Konnte Vorgang nicht lesen: {e}")


@router.get("/api/orchestrator/templates")
async def orchestrator_templates():
    out = []
    if _ORCH_TPL_DIR.exists():
        for f in sorted(_ORCH_TPL_DIR.glob("*.json")):
            try:
                d = json.loads(f.read_text(encoding="utf-8"))
            except Exception:
                continue
            out.append({"name": d.get("name") or f.stem,
                        "brief": (d.get("brief") or "")[:200],
                        "complexity": d.get("complexity"),
                        "steps": (d.get("flow") or {}).get("steps") or [],
                        "saved_at": d.get("saved_at", 0)})
    out.sort(key=lambda x: x.get("saved_at", 0), reverse=True)
    return out
