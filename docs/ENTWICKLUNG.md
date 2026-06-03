# AI_Framework_Thomas — Entwicklerdokumentation

**Stand:** Mai 2026 · Für Entwickler, die AI_Framework_Thomas erweitern oder warten.
Bedienung aus Nutzersicht: siehe [BEDIENUNGSANLEITUNG.md](../BEDIENUNGSANLEITUNG.md).

---

## 1. Architektur im Überblick

```
Browser (Vanilla JS, SPA)                 static/index.html + static/js/*.js
        │  fetch + SSE (Server-Sent Events)
        ▼
FastAPI / Uvicorn (async)                 main.py  (~1900 Zeilen)
        │  httpx                          tools/*.py   (Tool-Implementierungen)
        ▼                                 db.py        (SQLite via aiosqlite)
Ollama (lokales LLM)                      http://localhost:11434
        │
        ▼
SQLite (data/ai_framework_thomas.db)
```

- **Komplett asynchron**: `httpx`, `aiofiles`, `aiosqlite`.
- **Kein Frontend-Build**: HTML/CSS/JS werden direkt über `StaticFiles` ausgeliefert.
- **Single-User-fokussiert**, läuft aber auch im Server-Modus (`0.0.0.0`).

---

## 2. Backend (`main.py`)

### 2.1 Konfiguration
`config.json` wird beim Start eingelesen — keine Umgebungsvariablen:
```json
{ "allowed_models": [...], "default_model": "...", "ollama_base": "...",
  "port": 8780, "host": "127.0.0.1" }
```

### 2.2 Agentic Loop (`_chat_generator`)
Der `/api/chat`-Endpunkt durchläuft maximal **8 Iterationen**:
1. Request an Ollama mit allen aktiven Tool-Definitionen.
2. Antwort parsen — native `tool_calls` **oder** Inline-Tags
   (`<call_tool>` / `<tool_call>`) für Modelle ohne strukturiertes Tool-Calling.
3. Tool ausführen (`_execute_tool`), Ergebnis anhängen, wiederholen.
4. Sobald keine Tool-Calls mehr kommen: Antwort als SSE streamen.

**SSE-Frames** (`data: {...}`), unterschieden über `type`:
`text`, `canvas`, `image`, `tool_start`, `tool_done`, `error`, `done`.

### 2.3 VRAM-Schutz — nur EIN Modell gleichzeitig
Bei begrenztem VRAM (z. B. 6 GB) darf nie mehr als ein Modell gleichzeitig
geladen sein (Standard: nur `ministral-3:3b`; weitere Rollen-Modelle werden bei
Bedarf nachgeladen, siehe 2.4). Der Schutz sitzt zentral in `main.py`:

```python
_model_lock   = asyncio.Lock()      # serialisiert ALLE Ollama-Generierungen
_loaded_model = None                # zuletzt geladenes Modell

async def _model_session(model):    # asynccontextmanager
    async with _model_lock:
        if _loaded_model and _loaded_model != model:
            await _unload_model(_loaded_model)   # Ollama keep_alive=0
        _loaded_model = model
        yield
```

Jede Ollama-Aufrufstelle ist mit `async with _model_session(model), httpx...:`
umschlossen. Dadurch:
- Beim **Modellwechsel** wird das alte Modell zuerst aus dem VRAM entladen.
- Der Lock verhindert, dass **parallele** Anfragen zwei Modelle gleichzeitig laden.

Betroffene Stellen: Haupt-Chat, Recherche-Synthese, Gespräch-Komprimierung,
Chat→Skill, Agent-Prompt-Generierung, Planer-KI (Streaming).

#### Modell-Rollen (Profil)
Es gibt kein fest verdrahtetes Modell außer `DEFAULT_MODEL` (`ministral-3:3b`).
Das Profil hält drei optionale Zuweisungen: `model_general`, `model_coding`,
`model_science` (UI: **Allgemein / Programmieren / Wissenschaftlich**). `_model_for(role)`
liefert das zugewiesene Modell oder `DEFAULT_MODEL`. `_pick_model(m, fallback)`
akzeptiert jedes installierte Modell und weist Platzhalter (`Lade…`, das veraltete
`qwen3.6-16k:latest`) ab. `/api/models` filtert **nicht** mehr nach `allowed_models`
(liefert alle installierten Modelle; `allowed_models` ist nur noch Sortier-Reihenfolge).
Wiring: Sidebar-Default = `model_general`; Code-IDE + `code_ide`-fähige Agenten →
`model_coding`; `/api/research` + Wissenschaftspfad → `model_science`.

### 2.4 Berechnungs-Sandbox (`_safe_exec`)
Das Tool `calculate` führt Python in einem eingeschränkten `exec()` aus:
kein Datei-/Netzwerkzugriff, nur Whitelist (`math`, `numpy`, `scipy`, `sympy`).

### 2.5 Diagnose-Logging
`_write_log(entry)` schreibt JSON-Zeilen nach `data/ai_framework_thomas.log`, **nur wenn**
`_log_active` (per `/api/logs/config` umschaltbar). Rotation bei > 5 MB.
Protokolliert: `chat` (Modell, Dauer, Tools), `tool`, Frontend-Events.

---

## 3. API-Referenz

| Methode & Pfad | Zweck |
|---|---|
| `GET /api/models` | Erlaubte, in Ollama vorhandene Modelle |
| `POST /api/chat` | Haupt-Chat (SSE, Agentic Loop) |
| `POST /api/research` | Aspekt-basierte Recherche (SSE) |
| `GET/DELETE /api/conversations[/{id}]` | Gespräche auflisten / laden / löschen |
| `POST /api/conversations/{id}/compress` | Gespräch zusammenfassen |
| `POST /api/conversations/{id}/to-skill` | Gespräch → Agent ableiten |
| `PATCH /api/conversations/{id}/rename` | Gespräch umbenennen |
| `GET /api/conversations/{id}/export` | Einzelgespräch als JSON |
| `POST /api/conversations/import` | Gespräch aus JSON importieren |
| `POST /api/conversations/export-all` | Alle Gespräche als ZIP |
| `GET /api/search?q=` | FTS5-Volltextsuche |
| `POST /api/upload` · `GET /api/uploads/{id}` | Datei-Upload/-Abruf |
| `GET/POST/PUT/DELETE /api/agents[/{id}]` | Agenten-CRUD |
| `POST /api/agents/generate-prompt` | System-Prompt per KI erzeugen |
| `POST /api/export/{docx\|xlsx\|pptx\|pdf\|latex}` | Dokument-/Präsentations-Export. **pdf** rendert LaTeX-Formeln via matplotlib-mathtext (keine TeX-Installation nötig); **latex** liefert eine reine `.tex` (Dokument → `article`, Präsentation → `beamer`) |
| `GET/PUT /api/profile` | Nutzerprofil |
| `GET/POST/PUT/DELETE /api/projects[/{id}]` | Projekt-CRUD |
| `PUT /api/conversations/{id}/project` | Gespräch einem Projekt zuordnen |
| `GET/POST/PUT/DELETE /api/plans[/{id}]` | Netzpläne-CRUD (inkl. `description`, `system_prompt`, `resource_catalog`, `resource_mode`) |
| `POST /api/plans/{id}/ai` | Planer-KI-Assistent (SSE, nur Text) |
| `POST /api/plans/derive-agent` | Projekt-Agent (System-Prompt) aus Beschreibung ableiten |
| `POST /api/plans/suggest-tasks` | Vorgänger/Nachfolger-Vorschläge zu einer Aufgabe (JSON, respektiert `is_start`/`is_end` + Katalog-Modus) |
| `POST /api/plans/detail-task` | Aufgabe detaillieren: verfeinert Name/Dauer/Notiz/Ressourcen **und** schlägt Vorgänger/Nachfolger vor → im Frontend wähl- & editierbar, „Übernehmen" |
| `POST /api/plans/insert-between` | KI liest Bezeichnung/Notiz zweier Aufgaben A und B → schlägt 1–3 passende **Zwischenvorgänge** vor; Frontend verdrahtet A→neu→B und löst die direkte Kante A→B |
| `POST /api/plans/generate` | Kompletten Projektplan per LLM generieren (`max_tasks` frei bis 200, **keine 20er-Grenze** mehr; `format:"json"` + `num_ctx 8192` bei großen Plänen; Rettungs-Parser für abgeschnittenes JSON; gibt `warning` zurück, wenn viel angefordert wird / das Modell zu wenig liefert; IDs/Vorgänger validiert, Nachfolger abgeleitet) |
| `POST /api/derive-persona` | Bild-Analyse-Persona aus Präsentationsbeschreibung ableiten |
| `POST /api/analyze-image` | Einzelbild per Vision-Modell beschreiben → `{title, bullets, caption}` |
| `GET/POST/DELETE /api/code[/{id}]` | IDE-Programme-CRUD |
| `GET/DELETE /api/logs` · `PUT /api/logs/config` · `GET /api/logs/active` · `POST /api/logs/entry` · `GET /api/logs/download` | Diagnose-Logger |
| `GET /api/backup` · `POST /api/restore` | Komplett-Backup/-Restore (ZIP) |
| `GET /api/assets/{name}` | Corporate-Bilder aus `bilder/` |
| `GET/POST /api/mail/config` | Postfach-Zugang (IMAP/POP3) lesen/speichern — *🚧 in Entwicklung* |
| `POST /api/mail/list` · `POST /api/mail/message` | Mails auflisten / eine Mail volltext laden (read-only) |
| `POST /api/mail/to-rag` | Ausgewählte Mails (optional bereinigt) in eine Wissensdatenbank |
| `GET/POST /api/mail/rules` · `DELETE /api/mail/rules/{id}` | Mail-Regeln (Filter + bis zu 4 Aktionen) — Persistenz `data/mail_rules.json` |
| `POST /api/mail/action/rag` | Einzelne Mail bereinigt (`tools.mail.clean_mail_text`) in eine Wissensdatenbank |
| `POST /api/mail/action/agent` | Agent erledigt eine Aufgabe an einer Mail (z. B. Antwort entwerfen) → **nur Text zurück, kein Versand** |

---

## 4. Datenbank (`db.py`)

- `conversations`: `id, title, model, agent_id, canvas_json, project_id, timestamps`
- `messages`: `conv_id, seq, role, content, images_json`
- `messages_fts`: FTS5-Virtualtabelle mit Auto-Trigger für Volltextsuche.
- Beim Start werden Alt-JSON-Dateien aus `data/conversations/` automatisch
  nach SQLite migriert.

---

## 5. Tools (`tools/`)

| Modul | Inhalt |
|---|---|
| `search.py` | DuckDuckGo-Suche (async), liefert Quellenliste |
| `files.py` | Extraktion: PDF (pypdf), DOCX, XLSX/CSV, Text |
| `export.py` | DOCX/XLSX/PPTX/**PDF**/**LaTeX**-Erzeugung mit Corporate-Design & KI-Kennzeichnung. **PDF** (`to_pdf`) rendert LaTeX-Formeln via matplotlib-mathtext (vorvalidiert, crash-sicher); **LaTeX** (`to_latex`) erzeugt `article`/`beamer`-Quelltext (Markdown→LaTeX, Formeln bleiben Math) |
| `engineering.py` | Einheiten (Pint), Solver (SymPy), Diagramme aus Wertereihen (`plot_chart`), **Funktionsgraph aus Term** (`plot_function`, SymPy-Lambdify, `^`/implizite Mult./`f(x)=`-Vorsatz, mehrere Funktionen mit `;`), VDI-2230-Schraube |
| `materials.py` | ~40 Werkstoffe (Stähle, Alu, Titan, Edelstahl, Kunststoffe) |
| `report.py` | PDF/DOCX-Reports mit LaTeX-Formelsatz |
| `routing.py` | Routenplanung: Geocoding (Nominatim) + Routing (OSRM), liefert `{type:"map", …}` für das `route_planner`-Chat-Tool (Internet nötig) |
| `imaging.py` | Bild-Hilfen für die bebilderte Präsentation: Dateiname-Heuristik + Pillow-Downscale |
| `mail.py` | *🚧 in Entwicklung.* Read-only IMAP/POP3 (nur stdlib: `imaplib`/`poplib`/`email`); `domain_of()` (Domäne aus From-Header), `clean_mail_text()` (Zitat-Verlauf/Signatur/Disclaimer entfernen vor RAG) |
| `rag.py` | RAG-Engine: Cleanup, Chunking, Ollama-Embeddings (CPU), NumPy-Kosinus-Suche; VRAM-Tiers, Per-Basis `strictness`/`char_limit` |

> **Chat-Tool `route_planner`** (in `TOOL_DEFS`): bei Fragen nach dem Weg/Strecke von Ort A nach B; das Backend streamt einen `map`-Frame, den `chat.js` als interaktive Leaflet-Karte rendert (Leaflet vom CDN in `index.html`).

---

## 6. Frontend (`static/js/`)

| Datei | Verantwortung |
|---|---|
| `app.js` | Globaler State, Modell-Laden, Tab-Wechsel, Backup/Restore, Init aller Module |
| `chat.js` | SSE-Consumer, Nachrichten-Rendering, Upload, Gespräch-Rename/Import |
| `canvas.js` | HTML5-Canvas-Renderer für Folien & Tabellen, Corporate-Bilder, Themes; registriert **Edit-Regionen** für den WYSIWYG-Editor |
| `canvas_editor.js` | **WYSIWYG-Folieneditor**: Klick auf Folientext → Overlay-Eingabe; Bild tauschen; Folien-Toolbar (verschieben/löschen/„Text neu generieren") |
| `agents.js` | Agenten-CRUD-Oberfläche |
| `research.js` | Recherche-Modus + Dokument-Export mit Kopfzeile |
| `planner.js` | Netzplan (CPM), Tabelle mit Reihenfolge-Spalte (#) + stabilen IDs (Kaskaden-Rename), Zoom/Pan, CSV-Im/Export; **KI**: Agent ableiten, Plan generieren (freie Aufgabenzahl + Warnung), Aufgabe detaillieren, Vorgänger/Nachfolger vorschlagen, **Vorgang dazwischen einfügen**, **ersetzen**, Löschen mit Re-Bridge, **„Mach schön"** (Konsistenz + Sortierung), Zyklus-/Konfliktwarnungen; **Ressourcen** (Modal, Kosten, Lieferzeit, Rollup, Katalog-Im/Export, **Bestellplan** mit Arbeitstagen) |
| `matrix_research.js` | Recherche-Matrix, **Agent je Spalte** (nur Favoriten; `_cols[c].agent` → `agent_id` an `/api/chat`), **Live-Save** in `localStorage`, CSV-Im/Export, Zellen als Markdown+LaTeX |
| `presentation_assistant.js` | Tabellenbasierter Präsentations-Assistent (Folie-für-Folie) |
| `illustrated_presentation.js` | **Bebilderte Präsentation**: Ordner-Picker, Analyse-Persona ableiten, Bilder per Vision-Modell beschreiben → Zweispalter-Folien |
| `doc_generator.js` | **Dokumentengenerator**: Dokument/Präsentation per Agent + RAG + Quellmaterial erzeugen; Export DOCX/PDF/**LaTeX**; **Besprechungsnotizen** im Einfügefeld (Autospeichern in `localStorage`, Auto-Leeren nach Export) |
| `mail.js` | *🚧 in Entwicklung.* Mail-Tab: Abruf, Live-Filter (Absender/Betreff/Domäne), **Aktions-Set (max. 4)** (RAG/Agent/Doku/Notiz), **Regeln** (`/api/mail/rules`), Ergebnis-Karten rechts; Agent-Entwurf mit Kopieren/mailto/→Doku — **Versand stets manuell** |
| `json_editor.js` | **JSON-Editor**: Datei öffnen, Live-Validierung (Zeile/Spalte), formatieren, herunterladen — Untertab des **💻 Code**-Tabs (kein eigener Tab mehr) |
| `ide.js` | Code-IDE (Untertab): Editor, Canvas-Vorschau, KI-Assistent (Modell = Profil-Rolle „Programmieren"), Auto-Repair |
| `logger.js` | Diagnose-Logger-Oberfläche |
| `profile.js` / `projects.js` | Nutzerprofil bzw. Projektverwaltung |

### 6.1 IDE-Canvas-Framework (`ide.js`)
Der generierte/eingegebene Code läuft in einem **sandboxed iframe**, das ein
kleines Framework bereitstellt:

```javascript
// canvas + ctx sind vordefiniert, Canvas füllt das Vorschaufenster (responsiv)
const c = ai_framework_thomas_input("c", "Federrate [N/mm]", 25, {min:1, max:200, step:1});
function draw() { /* zeichnet mit ctx, nutzt ai_framework_thomas_input-Werte */ }
ai_framework_thomas_run(draw);   // PFLICHT am Ende — registriert + zeichnet, reagiert auf Eingaben
```

- `ai_framework_thomas_input(id, label, default, opts)` erzeugt automatisch ein Eingabefeld
  unter dem Canvas und gibt den aktuellen Wert zurück; bei Änderung wird neu gezeichnet.
- `ai_framework_thomas_run(fn)` registriert die Zeichenfunktion und ruft sie auf (auch bei Resize).
- **KI-Assistent**: nutzt das Profil-Modell der Rolle **Programmieren**
  (`Profile.modelFor('coding')`, Fallback `ministral-3:3b`). System-Prompt
  erzwingt reines Vanilla-JS (kein `require`/`import`/Chart.js).
- **Auto-Repair**: bei Laufzeitfehlern in der Konsole erscheint ein Button, der
  Code + Fehlermeldung erneut an die KI schickt.
- `console.*` und `window.onerror` werden per `postMessage` an die Eltern-Seite
  weitergeleitet und in der Konsole angezeigt.

---

## 7. Datenformate (`data/`)

| Pfad | Format |
|---|---|
| `data/ai_framework_thomas.db` | SQLite (Gespräche, Nachrichten, FTS) |
| `data/agents/<slug>.json` | `{id, name, description, system_prompt, tools[], model?, icon, category}` |
| `data/plans/<slug>_<id8>.json` | `{id, name, description, system_prompt, resource_mode, resource_catalog[{kind,name,rate}], start_date, workdays, tasks[…], timestamps}` — Task: `{id, name, duration, predecessors[], successors[], resource_list[{kind,name,qty,hours,rate,lead}], is_start, is_end, notes}` |
| `data/code/<slug>_<id6>.json` | `{id, name, code, updated_at}` |
| `data/user_profile.json` | `{first_name, last_name, company, department, position, email, phone, default_project}` |
| `data/projects.json` | `[{id, name, number, description, created_at}]` |
| `data/uploads/` | temporäre Uploads |
| `data/mail.json` | Postfach-Zugang `{protocol, host, port, user, ssl, password}` — **nicht** im Backup/git (Passwort im Klartext) |
| `data/mail_rules.json` | Mail-Regeln `[{id, name, filter:{from,subject,domain}, actions:[…≤4]}]` — *🚧 in Entwicklung*, nicht in git |
| `data/ai_framework_thomas.log` | Diagnose-Log (JSON-Lines, nur bei aktivem Logging) |

Dateinamen werden über `_to_slug()` aus dem Anzeigenamen gebildet
(ä→ae, ö→oe, ü→ue, ß→ss; Sonderzeichen → `_`).

---

## 8. Corporate Design (`bilder/`)

| Datei | Verwendung |
|---|---|
| `Logo.jpg` | **nur** Sidebar-Kopf (bewusst NICHT in Folien/Dokumenten — dort steckt das Logo bereits in der Kopfzeile) |
| `Design_Praesentation_Deckblatt.jpg` | PPTX-Titelfolie (Vollbild-Hintergrund) |
| `Design_Praesentation_Kopfzeile.jpg` | PPTX-Inhaltsfolien (Kopfzeilen-Strip) |
| `Design_Praesentation_Dokumente.jpg` | DOCX-Kopfzeile (nur bei `_include_header_image`) |

Ausgeliefert über `GET /api/assets/{name}` (Whitelist auf Bildendungen).
Farbpalette (CSS-Variablen in `app.css`): Primär `#3b76ba`, Dunkelblau
`#11314f`/`#003a74`, helle Texte `#d4e8f8`/`#a3c8eb`, Grau `#6c6f76`.

---

## 9. Lokale Entwicklung

```powershell
.\venv\Scripts\Activate.ps1
uvicorn main:app --host 127.0.0.1 --port 8780 --reload
```

Voraussetzung: Ollama läuft auf `localhost:11434` mit den Modellen aus
`config.json`. Browser: <http://localhost:8780>.

**Konventionen:**
- Neue API-Endpunkte vor dem `app.mount("/", StaticFiles(...))` am Dateiende einfügen
  (der Static-Mount muss zuletzt registriert werden).
- Jeder neue Ollama-Aufruf MUSS durch `_model_session(model)` laufen (VRAM-Schutz).
- Modellnamen aus dem Request mit `_pick_model(body.get("model"))` absichern
  (validiert gegen `ALLOWED_MODELS`, fällt sonst auf `DEFAULT_MODEL` zurück — verhindert
  500er durch ungültige Selektorwerte).
- JSON-Antworten kleiner LLMs robust parsen: `<think>`-Tags und ```-Codezäune
  entfernen, JSON per Regex extrahieren, immer einen Fallback vorsehen.
- Neue Frontend-Module in `index.html` als `<script>` einbinden und in
  `app.js` (`DOMContentLoaded`) initialisieren.

---

## 10. Verzeichnis & Release

Nicht zum Quellbaum gehörend (in `.gitignore`): `venv/`, `__pycache__/`,
`.claude/` (Assistenten-Memory), `server.log`, sowie die Laufzeitdaten unter
`data/` (DB, Uploads, Reports, Gespräche, Nutzer-Pläne, Profil).

Zusatzordner:
- `samples/` — Beispiel-Ressourcenliste (CSV, importierbar über *📥 Katalog*).
- `scripts/` — Hilfsskripte:
  - `make_demo_plan.py` / `verify_demo.py` — erzeugen/prüfen das
    100-Aufgaben-Beispielprojekt (Aufgabennamen vom LLM, Struktur deterministisch).
    Aufruf mit `PYTHONIOENCODING=utf-8`.
  - `build_release.ps1` — schnürt eine saubere **`ai_framework_thomas.zip`** (Quellbaum ohne
    venv/Caches/`.claude`/Laufzeitdaten, **mit** Standard-Agenten, leerer
    Datenstruktur und dem 100-Aufgaben-Beispielprojekt) auf den Desktop.

Installationsvarianten: `install` (Standard, Python+Ollama+venv),
`make_portable` (Embedded-Python-Bundle), `make_server` (Mehrbenutzer/NSSM-Dienst).
Die `make_*`-Skripte schließen `venv`, `__pycache__`, `.git`, `.claude` und
`server.log` beim Kopieren aus.
