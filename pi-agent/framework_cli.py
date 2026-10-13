#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""framework_cli — eine Kommandozeile auf AI_Framework_Thomas, als Werkzeug für
einen externen Agenten-Harness (Pi, https://pi.dev).

WARUM EINE CLI UND KEINE 200 TOOL-SCHEMAS
-----------------------------------------
Das Framework hat ~39 Router mit weit über hundert Endpunkten. Ein lokales
Modell kann die nicht als Tool-Schemas im Kontext halten. Darum wird das
Framework als **ein** Werkzeug gebunden: diese CLI plus die Beschreibung in
``skills/framework-cli.md``. Pi liest die Beschreibung als Datei und ruft die
CLI auf. (Dasselbe Muster, das sich in ema-motor-analysis bewährt hat.)

ZUGRIFFSSTUFE — wird bei der Installation festgelegt
----------------------------------------------------
``config.json`` des Frameworks, Schlüssel ``pi_agent_access``:

    "off"    Die CLI verweigert jeden Aufruf.  (Standard, wenn nichts gesetzt ist)
    "read"   Nur lesende und rechnende Verben.
    "write"  Zusätzlich anlegen und ändern.

Fehlt der Schlüssel, gilt **"off"**: eine neue Fähigkeit schaltet sich nicht von
selbst ein.

NIE — in KEINER Stufe
---------------------
Löschen, Mails versenden, Angebote oder Rechnungen versenden, Patente
einreichen, Profil/Einstellungen ändern, Python ausführen, To-Do
zurücksetzen/importieren. Diese Verben gibt es hier nicht, auch nicht in "write".

EHRLICHE EINORDNUNG DER GRENZE
------------------------------
``pi_agent_access`` ist ein **Leitplanke, keine Sicherheitsgrenze.** Die CLI
hält sich daran; sie verhindert aber nicht, dass irgendein Prozess auf diesem
Rechner die lokale HTTP-API direkt anspricht — das konnte er vorher auch schon.
Wer eine echte Grenze braucht, muss das Framework hinter Authentifizierung
setzen oder Pi in einen Container stecken (Pi selbst hat kein
Rechtesystem und empfiehlt das ausdrücklich).

GEHEIM-/HARTMAN-MODUS
---------------------
Diese CLI umgeht ihn nicht: alles, was Modelle benutzt, läuft über die
HTTP-Endpunkte des Frameworks, und dort greifen ``_secret_local()`` und
``_web_search_allowed()`` wie immer. Lokal gerechnet wird nur Deterministisches
(``tools/mechanik.py``, Werkstoffe, Einheiten) — ohne Modell und ohne Netz.
Wichtig: welches Modell **Pi selbst** benutzt, liegt außerhalb des Frameworks.
Wenn alles lokal bleiben soll, muss auch Pis Anbieter lokal zeigen.

Aufruf:  python framework_cli.py <verb> [args]   ·   --hilfe zeigt alles
"""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

# stdout auf UTF-8 zwingen — sonst stirbt die CLI unter Windows-cp1252 am ersten
# Umlaut oder σ (gleiche Falle wie z-image/sd_server.py, siehe CLAUDE.md).
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

#: Ordner des Frameworks (eine Ebene über pi-agent/)
FRAMEWORK_DIR = Path(__file__).resolve().parent.parent

EXIT_OK, EXIT_USAGE, EXIT_ACCESS, EXIT_UNREACHABLE, EXIT_ERROR = 0, 2, 3, 4, 5


# ══════════════════════════════════════════════════════════════════════════════
# Konfiguration und Zugriffsstufe
# ══════════════════════════════════════════════════════════════════════════════

def _config() -> dict:
    p = FRAMEWORK_DIR / "config.json"
    try:
        return json.loads(p.read_text(encoding="utf-8-sig"))
    except Exception:
        return {}


def basis_url() -> str:
    """Basis-URL des laufenden Frameworks aus config.json."""
    cfg = _config()
    host = str(cfg.get("host", "127.0.0.1") or "127.0.0.1")
    # 0.0.0.0 ist eine Bind-Adresse, keine Ziel-Adresse
    if host in ("0.0.0.0", "::", ""):
        host = "127.0.0.1"
    port = int(cfg.get("port", 8780) or 8780)
    return f"http://{host}:{port}"


def zugriff() -> str:
    """``off`` | ``read`` | ``write`` — fehlender Schlüssel bedeutet ``off``."""
    v = str(_config().get("pi_agent_access", "off") or "off").strip().lower()
    return v if v in ("off", "read", "write") else "off"


# ══════════════════════════════════════════════════════════════════════════════
# HTTP (nur stdlib)
# ══════════════════════════════════════════════════════════════════════════════

def _hole(pfad: str, params: dict | None = None):
    url = basis_url() + pfad
    if params:
        url += "?" + urllib.parse.urlencode({k: v for k, v in params.items() if v not in (None, "")})
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=120) as r:
        roh = r.read().decode("utf-8", "replace")
    try:
        return json.loads(roh)
    except Exception:
        return roh


def _sende(pfad: str, daten: dict, methode: str = "POST"):
    url = basis_url() + pfad
    req = urllib.request.Request(
        url, data=json.dumps(daten).encode("utf-8"), method=methode,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=600) as r:
        roh = r.read().decode("utf-8", "replace")
    try:
        return json.loads(roh)
    except Exception:
        return roh


def _sse(pfad: str, daten: dict):
    """SSE-Strom lesen und die Frames als dicts liefern (für /api/deepresearch)."""
    url = basis_url() + pfad
    req = urllib.request.Request(
        url, data=json.dumps(daten).encode("utf-8"), method="POST",
        headers={"Content-Type": "application/json", "Accept": "text/event-stream"},
    )
    with urllib.request.urlopen(req, timeout=1800) as r:
        for zeile in r:
            s = zeile.decode("utf-8", "replace").strip()
            if not s.startswith("data: "):
                continue
            try:
                yield json.loads(s[6:])
            except Exception:
                continue


# ══════════════════════════════════════════════════════════════════════════════
# Ausgabe
# ══════════════════════════════════════════════════════════════════════════════

_ALS_JSON = False


def aus(wert, tabelle: list[str] | None = None) -> None:
    """Ergebnis ausgeben: --json liefert rohes JSON, sonst eine knappe Liste."""
    if _ALS_JSON:
        print(json.dumps(wert, ensure_ascii=False, indent=2))
        return
    if isinstance(wert, str):
        print(wert)
        return
    if isinstance(wert, list):
        if not wert:
            print("(keine Einträge)")
            return
        for e in wert:
            if isinstance(e, dict) and tabelle:
                print(" · ".join(f"{k}={e.get(k, '')}" for k in tabelle if e.get(k) not in (None, "")))
            elif isinstance(e, dict):
                print(json.dumps(e, ensure_ascii=False))
            else:
                print(f"- {e}")
        print(f"\n({len(wert)} Einträge)")
        return
    print(json.dumps(wert, ensure_ascii=False, indent=2))


# ══════════════════════════════════════════════════════════════════════════════
# LESENDE VERBEN
# ══════════════════════════════════════════════════════════════════════════════

def v_status(args):
    """Erreichbarkeit, Zugriffsstufe und aktive Fähigkeiten."""
    cfg = _config()
    info = {
        "framework": basis_url(),
        "zugriffsstufe": zugriff(),
        "erreichbar": False,
        "bildgenerierung": bool(cfg.get("enable_image_generation", True)),
        "videoerzeugung": bool(cfg.get("enable_video_generation", True)),
    }
    try:
        prof = _hole("/api/profile")
        info["erreichbar"] = True
        info["version"] = prof.get("app_version", "")
        info["modus"] = prof.get("mode", "")
        info["geheim_modus"] = bool(prof.get("local_only_mode"))
        info["persona"] = prof.get("tone", "")
        info["modellrollen"] = {k: prof.get(k, "") for k in
                                ("model_general", "model_coding", "model_science", "model_medical")
                                if prof.get(k)}
    except Exception as e:
        info["fehler"] = f"{type(e).__name__}: {e}"
    aus(info)
    return EXIT_OK if info["erreichbar"] else EXIT_UNREACHABLE


def v_modelle(args):
    """Verfügbare Modelle (lokal und, falls konfiguriert, remote)."""
    d = _hole("/api/models")
    aus(d.get("models", d) if isinstance(d, dict) else d, ["name", "remote"])
    return EXIT_OK


def v_agenten(args):
    """Gespeicherte Agenten."""
    d = _hole("/api/agents")
    aus(d.get("agents", d) if isinstance(d, dict) else d, ["id", "name", "category"])
    return EXIT_OK


def v_projekte(args):
    """Projekte."""
    d = _hole("/api/projects")
    aus(d.get("projects", d) if isinstance(d, dict) else d, ["id", "name", "status"])
    return EXIT_OK


def v_plaene(args):
    """Pläne; mit <id> den einzelnen Plan."""
    if args:
        aus(_hole(f"/api/plans/{urllib.parse.quote(args[0])}"))
    else:
        d = _hole("/api/plans")
        aus(d.get("plans", d) if isinstance(d, dict) else d, ["id", "name"])
    return EXIT_OK


def v_rag_sammlungen(args):
    """Wissensdatenbanken (RAG-Sammlungen)."""
    d = _hole("/api/rag/collections")
    aus(d.get("collections", d) if isinstance(d, dict) else d,
        ["id", "name", "doc_count", "embed_model"])
    return EXIT_OK


def v_rag_dokumente(args):
    """Dokumente einer Sammlung: rag-dokumente <sammlung-id>"""
    if not args:
        return _fehlt("rag-dokumente <sammlung-id>")
    d = _hole(f"/api/rag/collections/{urllib.parse.quote(args[0])}/documents")
    aus(d.get("documents", d) if isinstance(d, dict) else d, ["id", "title", "chunks"])
    return EXIT_OK


def v_todo_baum(args):
    """Projektbaum der To-Do-Verwaltung."""
    aus(_hole("/api/todo/tree"))
    return EXIT_OK


def v_todo_suche(args):
    """todo-suche <begriff> [wurzel] — Volltextsuche im To-Do-Bestand."""
    if not args:
        return _fehlt("todo-suche <begriff> [wurzel]")
    aus(_hole("/api/todo/search", {"q": " ".join(args[:1]), "root": args[1] if len(args) > 1 else ""}))
    return EXIT_OK


def v_todo_agenda(args):
    """todo-agenda [wurzel] [person] — was als Nächstes dran ist (deterministisch)."""
    aus(_hole("/api/todo/agenda", {"root": args[0] if args else "",
                                   "person": args[1] if len(args) > 1 else ""}))
    return EXIT_OK


def v_todo_frage(args):
    """todo-frage <frage> — KI-Auswertung des To-Do-Bestands (speichert nichts)."""
    if not args:
        return _fehlt("todo-frage <frage>")
    aus(_sende("/api/todo/ask", {"question": " ".join(args)}))
    return EXIT_OK


def v_gespraeche(args):
    """Gespräche auflisten; mit <id> eines laden."""
    if args:
        aus(_hole(f"/api/conversations/{urllib.parse.quote(args[0])}"))
    else:
        d = _hole("/api/conversations")
        aus(d.get("conversations", d) if isinstance(d, dict) else d, ["id", "title", "updated_at"])
    return EXIT_OK


def v_gespraech_suche(args):
    """gespraech-suche <begriff> — Volltextsuche über die Gespräche."""
    if not args:
        return _fehlt("gespraech-suche <begriff>")
    aus(_hole("/api/search", {"q": " ".join(args)}))
    return EXIT_OK


def v_patente_projekte(args):
    """Patent-Vorgänge; mit <name> den einzelnen Vorgang."""
    if args:
        aus(_hole(f"/api/patente/projects/{urllib.parse.quote(args[0])}"))
    else:
        aus(_hole("/api/patente/projects"))
    return EXIT_OK


def v_varianten(args):
    """Variantenvergleiche; mit <name> den einzelnen Vergleich."""
    if args:
        aus(_hole(f"/api/varianten/projects/{urllib.parse.quote(args[0])}"))
    else:
        aus(_hole("/api/varianten/projects"))
    return EXIT_OK


def v_recherche(args):
    """recherche <thema> [--tiefe N] [--worte N] — mehrstufige Web-Recherche.

    Rechnet und streamt, speichert nichts. Unterliegt den Web-Gates des
    Frameworks (Hartman-Persona und „Web-Recherche lokal" schalten sie ab).
    """
    if not args:
        return _fehlt("recherche <thema> [--tiefe N] [--worte N]")
    thema, tiefe, worte = [], 6, 1000
    i = 0
    while i < len(args):
        if args[i] == "--tiefe" and i + 1 < len(args):
            tiefe = max(3, min(12, int(args[i + 1]))); i += 2
        elif args[i] == "--worte" and i + 1 < len(args):
            worte = max(200, min(4000, int(args[i + 1]))); i += 2
        else:
            thema.append(args[i]); i += 1
    text = []
    for ev in _sse("/api/deepresearch", {"topic": " ".join(thema), "depth": tiefe, "words": worte}):
        t = ev.get("type")
        if t == "text":
            text.append(ev.get("content", ""))
        elif t == "error":
            print("FEHLER: " + str(ev.get("content") or ev.get("message", "")), file=sys.stderr)
            return EXIT_ERROR
        elif t in ("searching", "aspect", "notice") and not _ALS_JSON:
            print("… " + str(ev.get("query") or ev.get("message") or ev.get("title", "")), file=sys.stderr)
    aus("".join(text) or "(kein Ergebnis)")
    return EXIT_OK


# ── Deterministisch und lokal: kein Modell, kein Netz ────────────────────────

def _tools_pfad():
    if str(FRAMEWORK_DIR) not in sys.path:
        sys.path.insert(0, str(FRAMEWORK_DIR))


def v_rechne(args):
    """rechne <kind> <schluessel=wert …> — Maschinenbau-Rechenkerne.

    Läuft lokal über tools/mechanik.py: deterministisch, ohne Modell und ohne
    Netz. ``rechne`` ohne Argumente zeigt alle Rechenarten.
    """
    _tools_pfad()
    from tools.mechanik import calc, catalog
    if not args:
        print(catalog())
        return EXIT_OK
    print(calc(args[0], " ".join(args[1:])))
    return EXIT_OK


def v_werkstoff(args):
    """werkstoff <name> [eigenschaft] — Werkstoffkennwerte (lokal)."""
    if not args:
        return _fehlt("werkstoff <name> [eigenschaft]")
    _tools_pfad()
    from tools.materials import material_lookup
    print(material_lookup(args[0], args[1] if len(args) > 1 else ""))
    return EXIT_OK


def v_einheit(args):
    """einheit <wert> <von> <nach> — Einheitenumrechnung (lokal)."""
    if len(args) < 3:
        return _fehlt("einheit <wert> <von> <nach>")
    _tools_pfad()
    from tools.engineering import unit_convert
    print(unit_convert(float(args[0].replace(",", ".")), args[1], args[2]))
    return EXIT_OK


# ── Die komplette Werkzeug-Registry des Frameworks ──────────────────────────
# Hier hängt der Agent an DENSELBEN Werkzeugen, die auch der Chat benutzt
# (Websuche, Schraubenauslegung VDI 2230, Rechenkerne, Diagramme, Werkstoffe,
# Einheiten, Routen, Berichte, Tabellen, Präsentationen, Diagramme, Patente …).
# Kein Verb je Werkzeug: neue Chat-Werkzeuge stehen automatisch zur Verfügung.

def v_werkzeuge(args):
    """werkzeuge [name] — verfügbare Werkzeuge; mit <name> dessen Parameter."""
    d = _hole("/api/tools/list")
    tools = d.get("tools", [])
    if args:
        gesucht = args[0].strip().lower()
        for t in tools:
            if t["name"].lower() == gesucht:
                aus(t)
                return EXIT_OK
        nah = [t["name"] for t in tools if gesucht[:4] in t["name"]]
        print(f"Werkzeug '{args[0]}' nicht verfügbar."
              + (f" Gemeint: {', '.join(nah)}?" if nah else ""), file=sys.stderr)
        return EXIT_USAGE
    if _ALS_JSON:
        aus(tools)
        return EXIT_OK
    for t in tools:
        kurz = " ".join(str(t.get("description", "")).split())[:150]
        print(f"{t['name']:<22} {kurz}")
    print("")
    print(f"({len(tools)} Werkzeuge · 'werkzeuge <name>' zeigt die Parameter)")
    return EXIT_OK


def v_werkzeug(args):
    """werkzeug <name> <json-argumente> — ein Framework-Werkzeug ausführen.

    Beispiele:
      werkzeug web_search '{"query": "VDI 2230", "num_results": 3}'
      werkzeug bolt_calculator '{"d_nom": 12, "pitch": 1.75, "f_axial": 20}'
    """
    if not args:
        return _fehlt("werkzeug <name> <json-argumente>   ('werkzeuge' listet alle)")
    name = args[0]
    roh = " ".join(args[1:]).strip() or "{}"
    if roh == "-":
        roh = sys.stdin.read()
    try:
        argumente = json.loads(roh)
    except Exception as e:
        beispiel = '{"schluessel": "wert"}'
        print(f"Die Argumente sind kein gültiges JSON ({e}).", file=sys.stderr)
        print(f"Erwartet: werkzeug {name} '{beispiel}'", file=sys.stderr)
        print(f"Parameter dieses Werkzeugs: framework_cli.py werkzeuge {name}",
              file=sys.stderr)
        return EXIT_USAGE
    antwort = _sende("/api/tools/run", {"name": name, "arguments": argumente})
    if isinstance(antwort, dict):
        aus(antwort.get("result", antwort))
        for f in (antwort.get("dateien") or []):
            print(f"[Datei: {basis_url()}/api/tools/file/{f}]", file=sys.stderr)
    else:
        aus(antwort)
    return EXIT_OK


def v_suche(args):
    """suche <begriff> — Websuche (Kurzform für werkzeug web_search)."""
    if not args:
        return _fehlt("suche <begriff>")
    return v_werkzeug(["web_search", json.dumps({"query": " ".join(args), "num_results": 6})])


def v_schraube(args):
    """schraube <d> <steigung> <axialkraft_kN> [festigkeitsklasse] — VDI 2230.

    Beispiel: schraube 12 1.75 20 8.8   (M12, Steigung 1,75 mm, 20 kN)
    """
    if len(args) < 3:
        return _fehlt("schraube <nenndurchmesser_mm> <steigung_mm> <axialkraft_kN> [klasse]")
    a = {"d_nom": float(args[0].replace(",", ".")),
         "pitch": float(args[1].replace(",", ".")),
         "f_axial": float(args[2].replace(",", "."))}
    if len(args) > 3:
        a["material_class"] = args[3]
    return v_werkzeug(["bolt_calculator", json.dumps(a)])


# ══════════════════════════════════════════════════════════════════════════════
# SCHREIBENDE VERBEN — nur bei pi_agent_access = "write"
# Anlegen und Ändern. Kein Löschen, kein Versenden (siehe Kopf).
# ══════════════════════════════════════════════════════════════════════════════

def v_todo_neu(args):
    """todo-neu <name> [eltern-id] — neue To-Do-Liste/Unterprojekt anlegen."""
    if not args:
        return _fehlt("todo-neu <name> [eltern-id]")
    aus(_sende("/api/todo/projects",
               {"name": args[0], "parent_id": args[1] if len(args) > 1 else "root"}))
    return EXIT_OK


def v_todo_setzen(args):
    """todo-setzen <pid> <json-datei|-> — Punkte einer Liste ersetzen.

    Erwartet die Liste als JSON (Datei oder „-" für stdin). Ersetzt den Inhalt
    der Liste — vorher mit ``todo-baum``/``todo-suche`` den Stand lesen.
    """
    if len(args) < 2:
        return _fehlt("todo-setzen <pid> <json-datei|->")
    roh = sys.stdin.read() if args[1] == "-" else Path(args[1]).read_text(encoding="utf-8")
    aus(_sende(f"/api/todo/projects/{urllib.parse.quote(args[0])}", json.loads(roh), "PUT"))
    return EXIT_OK


def v_projekt_neu(args):
    """projekt-neu <name> [beschreibung] — Projekt anlegen."""
    if not args:
        return _fehlt("projekt-neu <name> [beschreibung]")
    aus(_sende("/api/projects",
               {"name": args[0], "description": " ".join(args[1:])}))
    return EXIT_OK


def v_rag_neu(args):
    """rag-neu <name> — neue Wissensdatenbank anlegen."""
    if not args:
        return _fehlt("rag-neu <name>")
    aus(_sende("/api/rag/collections", {"name": " ".join(args)}))
    return EXIT_OK


def v_rag_notiz(args):
    """rag-notiz <sammlung-id> <titel> <text|-> — Text in eine Sammlung einlesen."""
    if len(args) < 3:
        return _fehlt("rag-notiz <sammlung-id> <titel> <text|->")
    text = sys.stdin.read() if args[2] == "-" else " ".join(args[2:])
    aus(_sende(f"/api/rag/collections/{urllib.parse.quote(args[0])}/from-text",
               {"title": args[1], "text": text}))
    return EXIT_OK


# ══════════════════════════════════════════════════════════════════════════════
# Registry
# ══════════════════════════════════════════════════════════════════════════════

#: verb → (stufe, funktion, kurzbeschreibung)
VERBEN: dict[str, tuple[str, object, str]] = {
    # lesen
    "status":           ("read", v_status, "Erreichbarkeit, Zugriffsstufe, aktive Fähigkeiten"),
    "modelle":          ("read", v_modelle, "verfügbare Modelle"),
    "agenten":          ("read", v_agenten, "gespeicherte Agenten"),
    "projekte":         ("read", v_projekte, "Projekte"),
    "plaene":           ("read", v_plaene, "Pläne (optional <id>)"),
    "rag-sammlungen":   ("read", v_rag_sammlungen, "Wissensdatenbanken"),
    "rag-dokumente":    ("read", v_rag_dokumente, "Dokumente einer Sammlung"),
    "todo-baum":        ("read", v_todo_baum, "To-Do-Projektbaum"),
    "todo-suche":       ("read", v_todo_suche, "To-Do-Volltextsuche"),
    "todo-agenda":      ("read", v_todo_agenda, "was als Nächstes dran ist"),
    "todo-frage":       ("read", v_todo_frage, "KI-Auswertung des To-Do-Bestands"),
    "gespraeche":       ("read", v_gespraeche, "Gespräche (optional <id>)"),
    "gespraech-suche":  ("read", v_gespraech_suche, "Volltextsuche über Gespräche"),
    "patente-projekte": ("read", v_patente_projekte, "Patent-Vorgänge"),
    "varianten":        ("read", v_varianten, "Variantenvergleiche"),
    "recherche":        ("read", v_recherche, "mehrstufige Web-Recherche (speichert nichts)"),
    "rechne":           ("read", v_rechne, "Maschinenbau-Rechenkerne (lokal, deterministisch)"),
    "werkzeuge":        ("read", v_werkzeuge, "ALLE Framework-Werkzeuge auflisten"),
    "werkzeug":         ("read", v_werkzeug, "ein Framework-Werkzeug ausführen"),
    "suche":            ("read", v_suche, "Websuche (Kurzform von werkzeug web_search)"),
    "schraube":         ("read", v_schraube, "Schraubenauslegung VDI 2230 (Kurzform)"),
    "werkstoff":        ("read", v_werkstoff, "Werkstoffkennwerte (lokal)"),
    "einheit":          ("read", v_einheit, "Einheitenumrechnung (lokal)"),
    # schreiben
    "todo-neu":         ("write", v_todo_neu, "To-Do-Liste anlegen"),
    "todo-setzen":      ("write", v_todo_setzen, "Punkte einer To-Do-Liste ersetzen"),
    "projekt-neu":      ("write", v_projekt_neu, "Projekt anlegen"),
    "rag-neu":          ("write", v_rag_neu, "Wissensdatenbank anlegen"),
    "rag-notiz":        ("write", v_rag_notiz, "Text in eine Sammlung einlesen"),
}

_STUFEN_RANG = {"off": 0, "read": 1, "write": 2}


def _fehlt(form: str) -> int:
    print(f"Aufruf: framework_cli.py {form}", file=sys.stderr)
    return EXIT_USAGE


def hilfe() -> None:
    z = zugriff()
    print(__doc__.strip().split("Aufruf:")[0].rstrip())
    print()
    print(f"Zugriffsstufe laut config.json: {z}")
    print(f"Framework: {basis_url()}")
    print()
    for stufe, titel in (("read", "LESEN (Stufe read und write)"),
                         ("write", "ANLEGEN/ÄNDERN (nur Stufe write)")):
        print(titel)
        for name, (s, fn, kurz) in VERBEN.items():
            if s != stufe:
                continue
            marke = "  " if _STUFEN_RANG[z] >= _STUFEN_RANG[s] else "x "
            print(f" {marke}{name:<18} {kurz}")
        print()
    print("x = durch die eingestellte Zugriffsstufe gesperrt")
    print("--json  Ergebnis als JSON statt als Liste")
    print()
    print("Nicht vorhanden, in keiner Stufe: Löschen, Mail-/Angebots-/Rechnungsversand,")
    print("Patente einreichen, Profil ändern, Python ausführen, To-Do zurücksetzen.")


def main(argv: list[str]) -> int:
    global _ALS_JSON
    argv = list(argv)
    if "--json" in argv:
        _ALS_JSON = True
        argv.remove("--json")
    if not argv or argv[0] in ("--hilfe", "-h", "--help", "hilfe", "help"):
        hilfe()
        return EXIT_OK

    verb = argv[0].strip().lower()
    if verb not in VERBEN:
        nah = [v for v in VERBEN if v.startswith(verb[:4])]
        print(f"Unbekanntes Verb '{verb}'." + (f" Gemeint: {', '.join(nah)}?" if nah else ""),
              file=sys.stderr)
        print("'--hilfe' zeigt alle Verben.", file=sys.stderr)
        return EXIT_USAGE

    stufe, fn, _ = VERBEN[verb]
    z = zugriff()
    if z == "off":
        print("Der Pi-Zugriff ist abgeschaltet (config.json: pi_agent_access = \"off\").\n"
              "Zum Freischalten den Installer erneut laufen lassen oder den Wert auf\n"
              "\"read\" (nur lesen) bzw. \"write\" (anlegen und ändern) setzen.",
              file=sys.stderr)
        return EXIT_ACCESS
    if _STUFEN_RANG[z] < _STUFEN_RANG[stufe]:
        print(f"'{verb}' braucht die Zugriffsstufe \"write\", eingestellt ist \"{z}\".\n"
              "Lesende Verben stehen zur Verfügung — '--hilfe' zeigt welche.",
              file=sys.stderr)
        return EXIT_ACCESS

    try:
        return fn(argv[1:]) or EXIT_OK
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = json.loads(e.read().decode("utf-8", "replace")).get("detail", "")
        except Exception:
            pass
        print(f"HTTP {e.code} von {basis_url()}{': ' + str(detail) if detail else ''}",
              file=sys.stderr)
        return EXIT_ERROR
    except urllib.error.URLError as e:
        print(f"Framework unter {basis_url()} nicht erreichbar: {e.reason}\n"
              "Läuft die Anwendung? (start.bat / start.sh)", file=sys.stderr)
        return EXIT_UNREACHABLE
    except KeyboardInterrupt:
        return EXIT_ERROR
    except Exception as e:
        print(f"{type(e).__name__}: {e}", file=sys.stderr)
        return EXIT_ERROR


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
