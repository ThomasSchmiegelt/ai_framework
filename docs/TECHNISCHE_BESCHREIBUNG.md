# AI_Framework_Thomas — Technische Beschreibung

> Stand: Juni 2026 · Bezieht sich auf `main.py` (~5400 Zeilen), `db.py`, das `tools/`-Paket
> und das Vanilla-JS-Frontend unter `static/`.

## 1. Was AI_Framework_Thomas ist

AI_Framework_Thomas ist eine **vollständig lokal laufende, deutschsprachige KI-Chat-Oberfläche
mit Ingenieur-Schwerpunkt**. Es kapselt lokale Ollama-LLMs hinter einem
FastAPI-Backend mit Vanilla-JS-Frontend und ergänzt sie um:

- eine **agentische Tool-Calling-Schleife**,
- **SQLite-Persistenz** der Gespräche (inkl. Volltextsuche),
- **ingenieurspezifische Werkzeuge** (VDI-2230-Schraubenberechnung, Werkstoff-Datenbank,
  Einheitenumrechnung, SymPy-Gleichungslöser, Matplotlib-Diagramme, PDF/DOCX/PPTX-Erzeugung).

Es werden **keine Cloud-Dienste** benötigt: weder Cloud-LLMs noch externe Datenbanken.
Die einzige optionale Netzwerkfunktion ist die Web-Suche (DuckDuckGo).

## 2. Gesamtarchitektur

```
┌──────────────────────────────────────────────────────────────┐
│  Frontend  (Vanilla JS, 13 Tabs)                               │
│  app.js, chat.js, canvas.js, ide.js, planner.js, medizin.js,   │
│  mathe.js, …                                                   │
└───────────────┬──────────────────────────────────────────────┘
                │  HTTP + SSE (Server-Sent Events)
┌───────────────▼──────────────────────────────────────────────┐
│  Backend  (FastAPI, durchgängig async)                         │
│  main.py  ── REST-Endpunkte, agentische Schleife, Tool-Dispatch│
│  db.py    ── SQLite-Persistenzschicht                          │
│  tools/   ── Such-, Datei-, Export-, Engineering-Werkzeuge     │
└───────┬───────────────────────────────┬──────────────────────┘
        │ httpx                          │ aiosqlite / Dateisystem
┌───────▼─────────────┐      ┌───────────▼──────────────────────┐
│  Ollama (lokal)     │      │  SQLite  data/ai_framework_thomas.db             │
│  localhost:11434    │      │  + JSON-Dateien (Agenten, Pläne,  │
│  ministral-3:3b     │      │    Profile, Projekte, Code)       │
└─────────────────────┘      └───────────────────────────────────┘
```

Das Backend ist **durchgehend asynchron** (`httpx`, `aiofiles`, `aiosqlite`).
Es gibt **keinen Frontend-Build-Schritt** — HTML/CSS/JS werden direkt über FastAPIs
`StaticFiles` ausgeliefert.

## 3. Konfiguration

Sämtliche Einstellungen stehen in `config.json` (keine Umgebungsvariablen):

| Feld | Bedeutung |
|---|---|
| `allowed_models` | **Sortier-Reihenfolge** der Modelle (kein Filter mehr) — Auswahllisten zeigen alle installierten Ollama-Modelle |
| `default_model` | Standard-Chatmodell (`granite4.2:3b`; IBM, Apache-2.0, Tool-Use/JSON, 128K); Modell-Rollen je Einsatzzweck im Profil |
| `ollama_base` | Ollama-URL (`http://localhost:11434`) |
| `host` / `port` | Bind-Adresse (`127.0.0.1` Einzelplatz / `0.0.0.0` Server) und Port (`8780`) |

Ollama muss separat laufen; mindestens eines der gelisteten Modelle muss gepullt sein.

## 4. Der Kern: die agentische Schleife (`/api/chat`)

Der zentrale Endpunkt ist `POST /api/chat`. Er liefert die Antwort als **SSE-Stream**
und durchläuft eine Schleife von **maximal 8 Iterationen** (`_chat_generator`):

1. **Kontext zusammenbauen**
   - Ist eine `agent_id` gesetzt, wird die Agentendatei geladen: deren `system_prompt`
     wird vorangestellt, ein optionales Modell überschreibt das Standardmodell, und die
     verfügbaren Tools werden auf die in der Agentendefinition erlaubten reduziert.
   - Hochgeladene Dateien werden eingebunden: **Bilder** als Base64 (für Vision-Modelle),
     **Dokumente** als extrahierter Text, der an die Nutzernachricht angehängt wird.

2. **Anfrage an Ollama** (`POST /api/chat`, `stream:false`) mit den aktiven Tool-Definitionen.

3. **Antwort parsen** — zwei Formate werden unterstützt:
   - natives `tool_calls`-JSON (strukturiertes Tool-Calling), **oder**
   - inline `<call_tool>` / `<tool_call>`-XML-Tags für Modelle ohne strukturiertes
     Tool-Calling (`_extract_inline_tool_calls`).

4. **Verzweigung:**
   - **Keine Tool-Calls** → der Text wird wortweise an den Client gestreamt. Eventuell
     eingebettetes Canvas-JSON wird erkannt (`_extract_canvas_json`) und separat als
     `canvas`-Frame gesendet. Anschließend wird das Gespräch in die DB gespeichert,
     ein Log-Eintrag geschrieben und ein `done`-Frame gesendet.
   - **Tool-Calls vorhanden** → jeder Aufruf wird ausgeführt (`_execute_tool`),
     das Ergebnis als `tool`-Nachricht angehängt, und die Schleife läuft erneut.
     Präsentationen/Tabellen werden sofort als `canvas`-Frame, Diagramme als
     `image`-Frame gestreamt.

5. Werden 8 Iterationen ohne finale Textantwort erreicht, wird ein `error`-Frame gesendet.

### SSE-Frame-Typen

Jedes Frame ist eine JSON-Zeile `data: {...}\n\n` mit `type`-Feld:

| `type` | Bedeutung |
|---|---|
| `text` | Ausgabe-Token (wortweise) |
| `canvas` | Präsentations-/Tabellen-Daten für den HTML5-Canvas |
| `image` | Base64-Diagramm (Matplotlib) |
| `map` | Route (Leaflet) für `route_planner` |
| `tool_start` / `tool_done` | Tool-Ausführung beginnt/endet (mit Vorschau) |
| `error` | Fehlermeldung |
| `done` | Antwort abgeschlossen |

> Die **Medizin-Pipeline** (`/api/medizin/consult`) ergänzt die Frames `stage`
> (aufklappbare Zwischenschritte) und `question` (Rückfrage an den Nutzer).

## 5. VRAM-Schutz — nur EIN Modell gleichzeitig im Speicher

Die Zielhardware hat begrenzten VRAM (~6 GB). Es darf **niemals mehr als ein** Modell
gleichzeitig geladen sein. Standardmodell ist `granite4.2:3b` (der Installer lädt zusätzlich `ministral-3:3b`); je
Profil-Rolle (Allgemein / Programmieren·Mathe / Wissenschaftlich / Medizin) kann ein anderes
Modell zugewiesen werden, das bei Bedarf nachgeladen wird (`model_coding` deckt Code-IDE
**und** Mathe-Tab ab). Dafür gibt es in `main.py`:

- `_model_lock` (`asyncio.Lock`) — serialisiert **alle** Generierungen,
- `_loaded_model` — merkt sich das aktuell geladene Modell,
- den Async-Context-Manager `_model_session(model)` — entlädt beim Modellwechsel
  zuerst das alte Modell (`Ollama keep_alive=0` via `_unload_model`), bevor das neue lädt.

> **Regel:** Jede Ollama-Aufrufstelle muss in
> `async with _model_session(model), httpx.AsyncClient(...) as client:` gekapselt sein.

Die **Medizin-Pipeline** wechselt bewusst mehrfach zwischen Ministral und MedGemma —
jede Stufe in einem eigenen `_model_session`-Block (nie verschachtelt), sodass der
Lock die Wechsel serialisiert. Das ist auf ~6 GB VRAM korrekt, aber spürbar langsamer
(Lade-/Entlade-Vorgänge je Stufe); die aufklappbaren Statusschritte zeigen den Fortschritt.

## 6. Werkzeuge (Tools)

Die Tool-Definitionen (`TOOL_DEFS` in `main.py`) werden Ollama als Funktions-Schemata
übergeben; die Ausführung erfolgt zentral in `_execute_tool(name, args)`:

| Tool | Funktion | Implementierung |
|---|---|---|
| `web_search` | DuckDuckGo-Suche, gibt Quellenliste zurück | `tools/search.py` |
| `calculate` | Python-Code für Berechnungen ausführen | `_safe_exec()` (Sandbox) |
| `unit_convert` | Physikalische Einheiten umrechnen (Pint) | `tools/engineering.py` |
| `solve_equation` | Gleichungen/Systeme symbolisch lösen (SymPy) | `tools/engineering.py` |
| `plot_chart` | 2D-Diagramm (Linie/Balken/Streu) aus Wertereihen als Bild | `tools/engineering.py` |
| `plot_function` | Funktionsgraph aus einem Term (`f(x)=x^2`, `sin(x)`; mehrere mit `;`) als Bild — **nur serverseitig** aufgerufen (deterministischer Fallback in `_chat_generator`, nicht als Modell-Tool, da kleine Modelle dabei ungültiges JSON erzeugen → Ollama 500) | `tools/engineering.py` |
| `material_lookup` | ~40 Werkstoffe (E-Modul, Rₚ, Rₘ, Dichte …) | `tools/materials.py` |
| `bolt_calculator` | Schraubenauslegung nach VDI 2230 (vereinfacht) | `tools/engineering.py` |
| `generate_report` | Ingenieurbericht als PDF/DOCX (LaTeX-Formeln) | `tools/report.py` |
| `create_presentation` | Foliensatz fürs Canvas (PPTX-exportierbar) | direkt in `main.py` |
| `create_spreadsheet` | Tabelle fürs Canvas (XLSX-exportierbar) | direkt in `main.py` |

Datei-Extraktion (PDF via pypdf, DOCX, XLSX, Bilder) liegt in `tools/files.py`,
die Export-Erzeugung (DOCX/XLSX/PPTX/**PDF**/**LaTeX** aus Chat-/Dokumentinhalten) in
`tools/export.py` — **PDF** rendert Formeln via matplotlib-mathtext (ohne TeX-Installation),
**LaTeX** liefert `article`/`beamer`-Quelltext.

### Code-Sandbox (`_safe_exec`)

`calculate` läuft in einem eingeschränkten `exec()`-Sandbox **ohne Datei- oder
Netzwerkzugriff**. Nur eine Whitelist an Builtins plus `math`, `numpy`, `scipy`
(inkl. `optimize`/`linalg`) und `sympy` sind verfügbar. `stdout` wird in einen Puffer
umgeleitet und als Ergebnis zurückgegeben; Ausnahmen werden als `Fehler: …` abgefangen.

## 7. Persistenz (`db.py`)

> Ausführliche Vertiefung: [`docs/PERSISTENZ.md`](PERSISTENZ.md) — Schema, FTS5-Trigger,
> jede DB-Funktion einzeln, Backup/Restore und Designentscheidungen im Detail.

SQLite über `aiosqlite`, Datei `data/ai_framework_thomas.db`, im **WAL-Modus** mit Foreign Keys:

- **`conversations`** — `id`, `title`, `created_at`, `updated_at`, `model`, `agent_id`,
  `canvas_json`, `project_id`. Der Titel wird automatisch aus der ersten Nutzernachricht
  abgeleitet (max. 80 Zeichen).
- **`messages`** — `rowid`, `conv_id` (CASCADE-Delete), `seq`, `role`, `content`,
  `images_json`, `created_at`. Index auf `(conv_id, seq)`.
- **`messages_fts`** — FTS5-Virtual-Table (`unicode61`-Tokenizer) für Volltextsuche,
  per AFTER-INSERT/DELETE-Trigger automatisch synchron gehalten.
  Die Suche (`/api/search`) dedupliziert auf ein Ergebnis pro Gespräch und liefert
  hervorgehobene Snippets.

Beim Start (`_startup`) wird das Schema angelegt; die `project_id`-Spalte wird per
`ALTER TABLE` nachgerüstet (idempotent). Bestehende Alt-JSON-Dateien in
`data/conversations/` werden einmalig nach SQLite migriert (`migrate_json`).

Andere Objekte liegen als **JSON-Dateien** im Dateisystem: Agenten (`data/agents/`),
Pläne (`data/plans/`), Profil, Projekte und gespeicherte Code-Programme (`data/code/`).

## 8. REST-Endpunkte (Auswahl)

| Bereich | Endpunkte |
|---|---|
| Chat & Modelle | `GET /api/models`, `POST /api/chat`, `POST /api/research` |
| Medizin (🩺) | `POST /api/medizin/consult` (2-Modell-Pipeline, SSE), `POST /api/medizin/translate` |
| Mathe (🔢) | `POST /api/mathe/ground` (SymPy-Grundwahrheit für den Tutor-Modus) |
| Gespräche | `GET/DELETE /api/conversations[/{id}]`, `…/compress`, `…/to-skill`, `…/export`, `…/import`, `…/export-all`, `…/rename`, `…/project` |
| Suche | `GET /api/search?q=` |
| Dateien | `POST /api/upload`, `GET /api/uploads/{id}`, `GET /api/downloads/{file}` |
| Agenten | `GET/POST/PUT/DELETE /api/agents[/{id}]`, `POST /api/agents/generate-prompt` |
| Export | `POST /api/export/{docx\|xlsx\|pptx\|pdf\|latex}` |
| Mail *(🚧 in Entwicklung)* | `GET/POST /api/mail/config`, `POST /api/mail/{list\|message\|to-rag}`, `GET/POST /api/mail/rules` + `DELETE /api/mail/rules/{id}`, `POST /api/mail/action/{rag\|agent}` |
| Profil/Projekte | `GET/PUT /api/profile`, `GET/POST/PUT/DELETE /api/projects[/{id}]` |
| Pläne (Netzplan) | `GET/POST/PUT/DELETE /api/plans[/{id}]`, `POST /api/plans/{id}/ai` |
| Code-IDE | `GET/POST/DELETE /api/code[/{id}]` |
| Backup/Restore | `GET /api/backup`, `POST /api/restore` |
| Assets | `GET /api/assets/{name}` (Whitelist Corporate-Bilder) |
| Logging | `GET/DELETE /api/logs`, `PUT /api/logs/config`, `GET /api/logs/download` |

## 9. Frontend-Module (Tabs)

Reines HTML/CSS/JS, ein Modul pro Funktionsbereich unter `static/js/`:

| Modul | Aufgabe |
|---|---|
| `app.js` | Globaler State, Modell-Laden, Tab-Wechsel, Backup/Restore, Modul-Init |
| `chat.js` | SSE-Stream-Konsument, Nachrichtenrendering, Datei-Upload, Umbenennen/Import |
| `canvas.js` | HTML5-Canvas-Renderer für Folien & Tabellen, lädt IGEL-Corporate-Bilder |
| `agents.js` | Agenten-CRUD (JSON-Dateien) |
| `research.js` | Aspektbasierte Recherche mit Quellen + DOCX-Export |
| `doc_generator.js` | Dokumentengenerator (Agent + RAG + Quellmaterial → DOCX/PDF/LaTeX); Besprechungsnotizen mit Autospeichern |
| `mail.js` | *🚧 in Entwicklung.* Mail-Tab: Abruf, Filter (Absender/Betreff/Domäne), Aktions-Set (max. 4: RAG/Agent/Doku/Notiz), speicherbare Regeln; Versand stets manuell |
| `planner.js` | Netzplan / Critical-Path-Method (CPM), Zoom/Pan, CSV-Im/Export, KI-Assistent |
| `matrix_research.js` | Recherche-Matrix mit Agent je Spalte (nur Favoriten), `localStorage`-Speicherung + CSV-Im/Export |
| `presentation_assistant.js` | Tabellenbasierter Präsentationsbauer (Folie für Folie) |
| `ide.js` | Code-IDE (Untertab des Code-Tabs): Editor + Sandbox-iframe-Vorschau, KI-Assistent (Modell = Profil-Rolle „Programmieren · Mathe"), Auto-Reparatur |
| `json_editor.js` | JSON-Editor (zweiter Untertab des Code-Tabs): öffnen, prüfen, formatieren, reparieren |
| `medizin.js` | 🩺 Medizin-Tab: 2-Modell-Pipeline mit Rückfragen + Laien-Übersetzung; Patienten-Akten (RAG `Patient:…`); Umschalter Experten-Pipeline / Direkt-Chat |
| `mathe.js` | 🔢 Mathe-Tab: Löser (`mathe_experte`) bzw. **Tutor-Modus** (`mathe_tutor`) mit SymPy-Grundwahrheit; Plots inline, LaTeX/PDF-Export |
| `profile.js` / `projects.js` | Nutzerprofil (vier Modell-Rollen, Tab-Sichtbarkeit) und Projektverwaltung |
| `logger.js` | Diagnose-Logger-UI (Filter, Download) |

## 10. Agentensystem

Agenten sind JSON-Dateien in `data/agents/` mit den Feldern: `id`, `name`,
`description`, `system_prompt`, `tools` (Array erlaubter Tool-Namen), `model` (optional),
`icon` (Emoji), `category`, `favorite`. Der `system_prompt` wird zur Laufzeit der Nutzernachricht
vorangestellt; das `tools`-Array beschränkt die in der Schleife angebotenen Werkzeuge.
Dateien werden anhand der **ID** gefunden (`_agent_path_by_id`), unabhängig vom
Dateinamen; Namen werden zu sicheren Slugs umgesetzt (`_to_slug`, inkl. Umlaut-Ersatz).

## 11. Corporate Design

Bilder in `bilder/` werden über `GET /api/assets/{name}` (Endungs-Whitelist) ausgeliefert:
`Logo.jpg` (Sidebar/Canvas), `Design_Praesentation_Deckblatt.jpg` (PPTX-Titel),
`Design_Praesentation_Kopfzeile.jpg` (PPTX-Kopf), `Design_Praesentation_Dokumente.jpg`
(DOCX-Kopf). Exporte kennzeichnen KI-Text mit „▶ Von KI generiert“.
Farbpalette in `app.css`: Primär `#3b76ba`, Dunkelblau `#11314f`/`#003a74`,
Hell `#d4e8f8`.

## 12. Betrieb & Deployment

Starten (nach `install.ps1`):

```powershell
.\venv\Scripts\Activate.ps1
# Entwicklung (nur localhost)
uvicorn main:app --host 127.0.0.1 --port 8780 --reload
# Server (alle Interfaces)
uvicorn main:app --host 0.0.0.0 --port 8780 --reload
```

Oder per Skript: `start.bat` (Einzelplatz) / `start_server.bat` (Mehrbenutzer).

Drei Installationsvarianten (je `.bat`- und `.ps1`-Paar):
- **`install`** — Standard: Python 3.12 via winget + Ollama + venv
- **`make_portable`** — eigenständiges Bundle, keine Systemabhängigkeiten
- **`make_server`** — Mehrbenutzer-Servermodus mit `0.0.0.0`-Bindung
- **`uninstall`** — entfernt venv, optional Daten/Ollama/Desktop-Verknüpfung

> Hinweis: Alle PowerShell-Skripte sind als **UTF-8 mit BOM** gespeichert, damit
> Windows PowerShell 5.1 die deutschen Sonderzeichen korrekt liest; die `.bat`-Dateien
> sind reines ASCII (cmd.exe verträgt kein BOM).

## 13. Abhängigkeiten

**Python:** FastAPI, Uvicorn, httpx, ddgs, pypdf, python-docx, openpyxl, python-pptx,
Pillow, python-multipart, aiofiles, aiosqlite, SymPy, NumPy, SciPy, Pint, matplotlib
(gepinnte Versionen in `requirements.txt`).

**Frontend:** keine — reines HTML/CSS/JS ohne Build-Schritt.
