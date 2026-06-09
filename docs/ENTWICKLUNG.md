# AI_Framework_Thomas — Entwicklerdokumentation

**Stand:** Juni 2026 · Für Entwickler, die AI_Framework_Thomas erweitern oder warten.
Bedienung aus Nutzersicht: siehe [BEDIENUNGSANLEITUNG.md](../BEDIENUNGSANLEITUNG.md).

---

## 1. Architektur im Überblick

```
Browser (Vanilla JS, SPA)                 static/index.html + static/js/*.js
        │  fetch + SSE (Server-Sent Events)
        ▼
FastAPI / Uvicorn (async)                 main.py  (~5400 Zeilen)
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
`text`, `canvas`, `image`, `map` (Route), `tool_start`, `tool_done`, `error`, `done`
(sowie `rag`/`adaptive` als Info-Leisten). Die Medizin-Pipeline ergänzt `stage` und
`question` (siehe 2.3).

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
Das Profil hält **vier** optionale Zuweisungen: `model_general`, `model_coding`,
`model_science`, `model_medical` (UI: **Allgemein / Programmieren · Mathe / Wissenschaftlich /
Medizin**). `_model_for(role)` liefert das zugewiesene Modell oder `DEFAULT_MODEL`.
`_pick_model(m, fallback)` akzeptiert jedes installierte Modell und weist Platzhalter
(`Lade…`, das veraltete `qwen3.6-16k:latest`) ab. `/api/models` filtert **nicht** mehr
nach `allowed_models` (liefert alle installierten Modelle; `allowed_models` ist nur noch
Sortier-Reihenfolge). Wiring: Sidebar-Default = `model_general`; Code-IDE + **Mathe-Tab**
(`mathe.js` `_model()`) + `code_ide`-fähige Agenten → `model_coding` (gemeinsames Modell,
keiner der beiden Tabs hat ein eigenes Auswahlfeld); `/api/research` + Wissenschaftspfad →
`model_science`; 🩺 Medizin-Pipeline (MedGemma-Rolle) → `model_medical`
(empfohlen `alibayram/medgemma`).

#### Funktionsgraphen: deterministisch, nicht modellgetrieben
`plot_function` wird dem Modell **nicht** als Ollama-Tool angeboten (in `_chat_generator`
aus `active_tools` herausgefiltert): kleine Modelle erzeugen beim Tool-Aufruf ungültige
LaTeX-Escapes `\( … \)` in den Argumenten → Ollama antwortet mit **HTTP 500**. Stattdessen
erkennt `_extract_plot_request(text)` einen Plot-Wunsch (Funktion[en], Bereich „von … bis …")
und zeichnet den Graphen **serverseitig** über `plot_function` — als Fallback nach der Antwort
**und** im `except`-Zweig des Ollama-Aufrufs, sodass der Graph auch bei einem 500 erscheint.
Gilt für Chat- und Mathe-Tab (beide `/api/chat`). `plot_chart` (explizite Wertereihen) bleibt
modell-aufrufbar. `_PLOT_RULE` sagt dem Modell, dass die App automatisch zeichnet.

#### Medizin-Pipeline & Mathe-Tutor (mehrstufige Endpunkte)
- **`POST /api/medizin/consult`** (SSE): 2-Modell-Konsultation mit Human-in-the-Loop.
  Stufen `refine` (Ministral strukturiert) → `analyze` (MedGemma prüft auf fehlende
  Angaben) → `formulate`/`question` (Rückfrage, max. `_MED_MAX_ROUNDS`=2 Runden) → `final`
  (MedGemma streamt die Einschätzung, `think:False`). Jede Stufe in eigenem
  `_model_session`-Block (Modellwechsel serialisiert). Frames: `stage`, `question`, `text`,
  `done {needs_followup, round}`, `error`. **`POST /api/medizin/translate`** übersetzt eine
  Einschätzung per Allgemein-Modell in Laiendeutsch (SSE).
- **`POST /api/mathe/ground`**: extrahiert die Aufgabe als SymPy-Ausdruck (ein LLM-Aufruf)
  und berechnet die **Grundwahrheit deterministisch** (`_mathe_sympy_facts`). Der Tutor-Modus
  (`mathe_tutor`-Agent) injiziert diese verifizierten Fakten in die `/api/chat`-Anfrage, weil
  kleine Modelle Verifikations-Tools im Dialog nicht zuverlässig selbst aufrufen. Theorie-/
  Wortaufgaben → leere Fakten → rein sokratisch.

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
| `POST /api/medizin/consult` | 🩺 2-Modell-Konsultation (SSE: `stage`/`question`/`text`/`done`/`error`); Rückfragen bis 2 Runden, dann Einschätzung |
| `POST /api/medizin/translate` | Medizinische Einschätzung in Laiendeutsch übersetzen (SSE, Allgemein-Modell) |
| `POST /api/mathe/ground` | 🎓 Tutor: Aufgabe extrahieren + SymPy-Grundwahrheit berechnen → `{facts}` (leer bei Theorie) |
| `GET/POST/DELETE /api/code[/{id}]` | IDE-Programme-CRUD |
| `POST /api/code/run-python` | Python aus dem Code-Tab serverseitig ausführen (stdout/stderr, matplotlib-PNG, Zeitlimit); 403 wenn `allow_python_exec:false` |
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
| `ide.js` | Code-IDE (Untertab): Editor, Canvas-Vorschau, KI-Assistent (Modell = Profil-Rolle „Programmieren · Mathe"), Auto-Repair |
| `medizin.js` | **🩺 Medizin-Tab**: 2-Modell-**Pipeline** (`/api/medizin/consult`) mit aufklappbaren Stufen, Rückfragen (max. 2 Runden), Laien-Übersetzung (`/api/medizin/translate`); Umschalter **🔬 Experten-Pipeline** (sonst einfacher Direkt-Chat). **Patienten-Akten** = RAG-Sammlungen `Patient:…` (inline anlegen/Dateien einlesen) |
| `mathe.js` | **🔢 Mathe-Tab**: Löser über `mathe_experte`, **🎓 Tutor-Modus** über `mathe_tutor` mit SymPy-Grundwahrheit (`/api/mathe/ground`) + **💡 Lösung zeigen**. Modell = `Profile.modelFor('coding')` (mit Code geteilt, kein eigenes Auswahlfeld), LaTeX immer an; Plot-Schalter + Tutor-Button an der Chatzeile; Plot-`image`-Frames inline, LaTeX/PDF-Export bei Formeln |
| `logger.js` | Diagnose-Logger-Oberfläche |
| `profile.js` / `projects.js` | Nutzerprofil (vier Modell-Rollen, Tab-Sichtbarkeit über `data-tabs`-Häkchen) bzw. Projektverwaltung |

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
- **KI-Assistent**: nutzt das Profil-Modell der Rolle **Programmieren / Mathe**
  (`Profile.modelFor('coding')`, mit dem Mathe-Tab geteilt, Fallback `ministral-3:3b`). System-Prompt
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
| `data/agents/<slug>.json` | `{id, name, description, system_prompt, tools[], model?, icon, category, favorite}` — Standard-Agenten u. a. `latex_experte`, `mathe_experte`, `mathe_tutor`, `medizin_assistent` |
| `data/plans/<slug>_<id8>.json` | `{id, name, description, system_prompt, resource_mode, resource_catalog[{kind,name,rate}], start_date, workdays, tasks[…], timestamps}` — Task: `{id, name, duration, predecessors[], successors[], resource_list[{kind,name,qty,hours,rate,lead}], is_start, is_end, notes}` |
| `data/code/<slug>_<id6>.json` | `{id, name, code, updated_at}` |
| `data/user_profile.json` | `{first_name, last_name, company, department, position, email, phone, default_project, lang, mode, tone, model_general, model_coding, model_science, model_medical, hidden_tabs[], …}` — `hidden_tabs` blendet optionale Tabs aus (Erstaufruf: alle sechs) |
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
`server.log` beim Kopieren aus. Vollständige Deployment-Details: siehe §20.

---

## 11. Modi & System-Prompt-Vorspann (`_augment_prefix`)

Es gibt **sieben Modi** (`maschinenbau`, `ki`, `soziales`, `marketing`, `finanz` (grau),
`geschaeftsfuehrung` (gelb) + einen nutzerdefinierten `custom` in Violett), gewählt im
Profil. Sie steuern (a) das UI-/Folien-Farbschema über CSS-Variablen (`html[data-mode=…]`
in `app.css`) und (b) — wenn `mode_prompt` an ist — eine **Domänen-Rahmung**, die den
System-Prompts vorangestellt wird (`_mode_prefix()`, keyword-gesteuert pro Frage über
`_MODE_KEYWORDS`).

Der **`custom`**-Modus ist voll nutzerdefiniert: Name, Domänen-Rahmung und (optionale)
Keywords kommen aus den Profilfeldern `custom_mode_name` / `custom_mode_prompt` /
`custom_mode_keywords` (gelesen von `_mode_prompt_text()` / `_mode_keywords()`; ohne
Keywords gilt die Rahmung für jede Frage).

Der automatische System-Prompt-Vorspann wird von **`_augment_prefix()`** in `main.py`
zusammengesetzt: Anti-Halluzinations-Guard + Modus-Rahmung + Persona/Profil (`tone`) +
LaTeX-Formelregel + Zitierregel (Normen/Gesetze sollen benannt werden, damit das Frontend
linkifizieren kann).

- **`pure_llm`** (Profil-Flag „keine Modi / LLM pur"): `_augment_prefix()` lässt
  Guard/Modus/Persona/Formel/Zitat weg — nur die Sprachregel bleibt. Ein explizit
  gewählter Agent und aktive RAG-Basen gelten weiterhin.
- **`lang`** (`de`/`en`, Default `de`): steuert UI-Sprache (Frontend `static/js/i18n.js` —
  DE→EN-Wörterbuch, übersetzt die statische HTML-Hülle on the fly; tief JS-generierte
  Strings bleiben deutsch) **und** Antwortsprache: bei `lang=en` hängt `_lang_rule()`
  `_LANG_RULE_EN` an (auch unter `pure_llm`). Toggle: `#profile-lang` → `I18n.setLang`.

**Branding** (Logo, Deckblatt, Kopfzeile) lädt der Nutzer im Profil hoch
(`POST/GET/DELETE /api/profile/asset/{logo|cover|header}`, Ablage `data/profile_assets/`,
Pillow-resize: Logo 512 PNG, cover/header 1920 JPG). `bilder/` enthält Repo-Fallbacks
(`default_logo.png`, `default_cover.jpg`, `default_header.jpg`, `icon.png`/`icon.ico`),
ausgeliefert über `GET /api/assets/{name}`. `canvas.js` lädt sie über
`/api/profile/asset/...`, `export.py` aus `data/profile_assets/`. Folien lesen die aktive
Modus-Palette über `_pal()`. Exporte kennzeichnen KI-Text mit „▶ Von KI generiert".

**Präsentations-Canvas-Fallback:** kleine Modelle rufen das `create_presentation`-Tool
oft nicht auf, sondern schreiben Prosa. Ist der aktive Agent präsentationsfähig
(`create_presentation` in seinen Tools → `presentation_capable`) und es kam kein
`canvas`-Frame, aber die Antwort sieht nach Präsentation aus, ruft `_chat_generator`
`_text_to_presentation(text, model)` (zweiter `format:"json"`-Aufruf) und emittiert
trotzdem einen `canvas`-Frame.

**Weitere Nicht-Streaming-KI-Endpunkte** (immer mit `<think>`-/Codezaun-Strip +
Regex-Extraktion + Fallback): `/api/derive-persona`, `/api/analyze-image` (Vision),
Planer `/api/plans/{derive-agent,suggest-tasks,generate}`. **Science-Modus:**
`/api/research` und Matrix-Recherche (postet an `/api/chat` mit `science:true`) stellen
immer `_SCIENCE_PROMPT` voran — unabhängig von `pure_llm`. Temperatur im Chat-Payload
niedrig (`0.3`, bei `science` `0.1`). Bei leerem Content ohne Tool-Calls emittiert
`_chat_generator` einen klaren `error`-Frame statt einer leeren Antwort.

**Per-Task-Planrecherche:** `POST /api/plans/{pid}/research-task` durchläuft
analyze → adaptive Persona (`_derive_adaptive_prompt`) → Websuche (`tools.search`) →
wissenschaftliches Markdown-Dossier → einbetten in eine auto-erzeugte plan-spezifische
RAG (`_ensure_plan_rag`, Name `Plan: <name>`, gespeichert als `plan["rag_collection_id"]`)
→ Dossier an die Aufgabe anhängen. `planner.js` treibt es pro Task (🔬) und als
abbrechbaren/fortsetzbaren Batch (überspringt bereits `researched`); 📄 öffnet das Dossier.

---

## 12. LLM-Abstraktion & externe API-Anbieter (`tools/llm.py`)

Alle LLM-Aufrufe laufen über **`tools/llm.py`** (in `main.py` als `_llm` importiert), damit
die App **lokales Ollama ODER einen externen OpenAI-kompatiblen Anbieter** (OpenRouter,
OpenAI, Groq, Together …) nutzen kann. **Die Rückgabe ist immer Ollama-förmig**
(`{"message": {"content", "tool_calls", "thinking"}}` / Stream-Chunks
`{"message": {"content"}, "done"}`), sodass die ~30 Aufrufstellen fast unverändert bleiben:
`client.post(.../api/chat, json=PAYLOAD)` → `await _llm.chat(client, PAYLOAD)`,
`client.stream(...)` → `async for chunk in _llm.stream(client, PAYLOAD)`. `_llm.chat`
liefert ein `LLMResponse(dict)`, das zusätzlich `.json()` / `.raise_for_status()` /
`.status_code` bietet (auch der `think`-400-Retry der Agentic-Loop funktioniert unverändert).

- **Routing:** ein Modellname mit Präfix `"<provider_id>::<model>"` (lokale Ollama-Namen
  enthalten nie `::`) ist remote; `_llm.resolve()` schlägt den Anbieter in
  `data/api_providers.json` nach. Remote-Requests übersetzen Ollama→OpenAI:
  `options.temperature`→`temperature`, `format:"json"`→`response_format:{type:"json_object"}`,
  `think` verworfen, Bilder→`image_url`-Content-Parts; Response/Stream werden zurück in die
  Ollama-Form übersetzt (Tool-Call-`arguments` JSON-String → dict).
- **Anbieter:** in `data/api_providers.json` (**enthält API-Keys → gitignored, aus
  `/api/backup` und den `make_*`-Bundles ausgeschlossen**; analog `data/mail.json`). CRUD:
  `GET/POST/DELETE /api/providers` + `POST /api/providers/test` (holt `{base}/models`).
  `_provider_public()` entfernt den Key fürs Frontend. `/api/models` merged lokale
  Ollama-Tags mit Anbieter-Modellen (präfixiert, `remote:true`); die Profil-Rollen-Selects
  listen beide (`profile.js` `_fillModelSelects`, Label `☁ model (Provider)`).
- **Config:** `_llm.set_config(OLLAMA_BASE, API_PROVIDERS_FILE)` einmal beim Start. Verwaltung
  im **Profil-Modal** („☁ KI-Anbieter (API)"). Für remote ist `_model_session` ein No-op.

---

## 13. RAG-Engine (`tools/rag.py`, `rag.js`)

Dokument-Cleanup (`clean_text`), zeichenbasiertes überlappendes `chunk_text`,
Ollama-Embeddings (`embed`, auf kleinen VRAM-Tiers via `num_gpu=0` auf CPU gezwungen, damit
sie das Chat-Modell nicht verdrängen) und NumPy-Brute-Force-Kosinus-Suche
(`query_collections`). **VRAM-Tiers** (`none`/`4gb`/`6gb`/`12gb`) presetten Chunk-Größe,
Overlap, top-k, Embed-Device **und `char_limit`** (max. injizierte Kontextzeichen — pro
Query erzwungen, größtes Limit der gewählten Sammlungen). Per-Basis **`strictness`**
(`kreativ`/`ausgewogen`/`korrekt`) wählt die RAG-Injektions-Formulierung (strengste der
gewählten Basen gewinnt). Embeddings als float32-BLOBs in SQLite
(`rag_collections`/`rag_documents`/`rag_chunks` in `db.py`; `char_limit`/`strictness` via
Migration). Embed-Modell aus `config.json` `embed_model` (Default `nomic-embed-text`, **muss
in Ollama gepullt sein**).

**Ingest-Quellen:** Dokument-Upload, ein Gespräch
(`POST /api/rag/collections/{id}/from-conversation`, optional `delete_after` = verschieben)
oder beliebiger Text (`POST /api/rag/collections/{id}/from-text` — von den „📚 In
Wissensdatenbank"-Buttons in Recherche/Matrix). `rag.js` bietet wiederverwendbares
`pickCollection()`/`ingestText()` (Picker-Modal).

**RAG-Tab** („Wissensdatenbanken"): zwei Slider — „schnell ↔ gründlich" (Chunk-Presets) und
„kreativ ↔ korrekt" (`strictness`) — plus Cleanup-Toggle. Im Chat injiziert `📚 RAG`-Toggle +
Multiselect Passagen; ein `rag`-SSE-Frame zeigt die genutzten Quellen. Dokumentlisten sind
einklappbar (`<details>`). **Hilfe-Wissensdatenbank:** `🆘 …erstellen/aktualisieren`
(`_buildHelp` → `POST /api/help/build`) bettet die mitgelieferte Tool-Doku (`README.md`,
`BEDIENUNGSANLEITUNG.md`, `docs/*.md`, Liste in `_HELP_DOC_FILES`) in eine
`Hilfe: LOCAL AI`-Sammlung ein und legt/erneuert einen Favoriten-Agenten **`hilfe_agent`**
(🆘, an die Basis gebunden) an — idempotent, erreichbar via `/Hilfe …`.

---

## 14. Agenten — Favoriten, Slash, Adaptiv, Gesetz-Agent

Agenten sind JSON-Dateien in `data/agents/` mit Feldern: `id`, `name`, `description`,
`system_prompt`, `tools[]`, `model?` (Override), `icon`, `category`, `favorite`,
**`rag_collections[]`** (an den Agenten gebundene Wissensbasen — in `_chat_generator`
auto-aktiviert und in die RAG-Auswahl gemerged). Der Agent-Editor exponiert das über einen
Wissensbasis-Multiselect (`#field-agent-rag`, befüllt aus `/api/rag/collections`). Der
System-Prompt wird zur Laufzeit der Nutzernachricht vorangestellt. Standard-Agenten u. a.
`latex_experte`, `mathe_experte`, `mathe_tutor`, `medizin_assistent`, `coder`, `presenter`.

- **Sidebar = Favoriten:** der Sidebar-Agentenselektor listet **nur `favorite:true`**
  (+ „Kein Agent" + adaptiv). Jede Agentenkarte hat einen ⭐-Toggle
  (`AgentManager.toggleFavorite`). Die Chat-Toolbar hat zwei Schnellwähler — **📊
  Präsentation** und **💻 Programmieren** — die den Selektor auf `presenter`/`coder` setzen
  (fügen die Option bei Bedarf on the fly hinzu). `coder`/`presenter` sind Default-Favoriten.
- **Slash-Agent (One-Shot):** ein führendes `/<name>` in der Chateingabe lässt **nur diese
  Nachricht** durch den passenden Agenten laufen, ohne den Selektor zu ändern. `chat.js`
  `_resolveSlashAgent` matcht gegen `id`/`name` (exakt → Präfix → ent-slugifizierter Name;
  z. B. `/mathe` → `mathe_experte`, `/Hilfe` → `hilfe_agent`), strippt das Präfix, setzt
  `agent_id` nur für diesen Request und zeigt eine „➜ Agent: … (nur diese Frage)"-Notiz
  (`insertAgentNote`). Kein Match → Toast + normaler Versand.
- **Adaptiver Agent:** `agent_id == "__adaptive__"` triggert `_derive_adaptive_prompt()` —
  ein vorgelagerter Nicht-Streaming-Aufruf, der die letzte Nachricht analysiert und einen
  fragenspezifischen `system_prompt` (`{rolle, system_prompt}`) liefert; emittiert einen
  `adaptive`-Frame mit der Rolle.
- **Gesetz-/Regel-Agent aus Datei:** Button **⚖️ Gesetz-/Regel-Agent**
  (`agents.js` `createLegalAgent` → `POST /api/agents/from-legal`, multipart `file`+`title`).
  Backend extrahiert Text (`_extract_text`), wandelt **deterministisch** in Markdown
  (`_legal_to_md` — Regex macht `§ …`/`Art. …`-Zeilenanfänge zu `###`, kein LLM), entscheidet
  **nach Länge** (`_LEGAL_PROMPT_LIMIT`, 8000 Zeichen): kurz → Markdown direkt in
  `system_prompt`; lang → in eine dedizierte RAG `Gesetz: <title>` (`strictness:"korrekt"`)
  eingebettet und über `rag_collections` gebunden. Agent mit `icon:"⚖️"`, `category:"Recht"`,
  `favorite:true`. Rückgabe `{agent_id, name, mode:"prompt"|"rag", chars, rag_collection_id}`.

**Standard-Agent-Seeding (`_seed_defaults()`):** die mitgelieferten Agenten liegen in
**`defaults/agents/`** (getrennt von `DATA_DIR`). Beim Start, wenn `AGENTS_DIR` leer ist und
keinen `.seeded`-Marker hat, werden sie hineinkopiert — behebt „Agenten verschwinden bei
eigenem `data_dir`" und seedet Frischinstallationen. Der Marker verhindert, dass bewusst
gelöschte Agenten wiederkommen.

---

## 15. Jury — Mehr-Agenten-Bewertung eines Textes

Eine **Jury** bündelt mehrere Agenten (typisch ⚖️ Gesetz-Agenten) zu einem
wiederverwendbaren Gremium, das beliebigen Text bewertet (auch KI-erzeugten: ein Dokument,
ein System-Prompt, einen Planer-Projektagenten). Speicherung als JSON in `data/juries/`
(`{id, name, description, member_agent_ids[], created_at}` — im Backup, restore nach id,
gitignored). CRUD: `GET/POST/PUT/DELETE /api/juries`.

**Bewertungs-Engine** `POST /api/jury/evaluate` (SSE), Body `{jury_id | member_agent_ids[],
text, context?, criteria?}`: pro Mitglied lädt sie `system_prompt` + gebundene
`rag_collections` (Passagen via `query_collections`), führt einen Verdict-Aufruf
(`format:"json"`, Modell = `agent.model` oder `_model_for("science")`) **sequenziell unter
`_model_session`** aus und streamt einen `member`-Frame `{agent, icon, score, befund,
risiken[], empfehlung}`. Danach eine **Synthese** (Allgemein-Modell) → `summary`-Frame
`{gesamturteil, score, konsens, hauptkritik[], empfehlungen[]}` (Fallback-Score = Mittel der
Mitglieder), dann `done`. Nutzt `_parse_llm_json` + `_sse`.

**Frontend (`jury.js`):** Verwaltungs-Modal aus dem 🤖 Agenten-Tab (`#btn-juries`);
wiederverwendbares Overlay **`Jury.evaluate(text, {title, context})`** (Streamer
`_streamEval`). Eingebunden in Dokumente (`#btn-docgen-jury`) und Agent-Edit-Modal
(`#btn-agent-prompt-jury`). Der Planer prüft Projekt-Agenten **nicht mehr selbst**
(früher `#btn-planner-agent-jury`); stattdessen wird der abgeleitete Agent über
„💾 Als Agent speichern" (`_saveAsAgent` → `AgentManager.openModal`) in den Agenten-Tab
übernommen und dort über `#btn-agent-prompt-jury` geprüft/bearbeitet (siehe §14).

**Dedizierter ⚖️ Jury-Tab (Dokument-Werkbank):** optionaler `jury`-Tab (`#jury-panel`):
links Jury-Liste + gespeicherte Dokumente; rechts ein editierbares Dokument (Textarea + 👁
Markdown-Vorschau) mit „⚖️ Mit Jury prüfen", „💾 Speichern", Export DOCX / → Doku
(`DocGen.showResult`) / → Wissensdatenbank (`RAG.ingestText`). Persistenz in
**`data/jury_docs/`** (`{id, name, text, evaluation?, updated_at}`) via
`GET/POST/PUT/DELETE /api/jury-docs` (dateibasiert wie `data/code/`), im Backup
(skip-by-id), gitignored. `Jury.loadDocument(name, text)` reicht externen Text hinein.
Splitter `#jury-tab-splitter`.

---

## 16. Verzeichnis-Analyse & Morphologischer Kasten (Backend)

Zwei **optionale** Tabs (Frontend `dir_analysis.js` / `morph_box.js`). Alle Endpunkte vor
dem Static-Mount registriert, Modellnamen über
`_pick_model(body.get("model"), _model_for("general"))`, jeder Ollama-Aufruf in
`async with _model_session(model), httpx…`.

**Verzeichnis-Analyse** (`/api/dir/scan`, `/api/dir/analyze-file`, `/api/dir/finalize`):
liest einen **serverseitigen Pfad** (`_dir_resolve_base` prüft `is_dir`). `_dir_walk` ist
ein bounded rekursiver Walk (`_DIR_MAX_FILES`/`_DIR_MAX_DEPTH`, überspringt versteckte +
`_DIR_SKIP_DIRS` wie `.git`/`node_modules`/`venv`, fängt `PermissionError`).
Snippets/Volltext aus `_extract_text`. **PII wird anonymisiert, bevor irgendetwas das LLM
oder den Client erreicht** — `tools/anonymize.py` (`_anonymize()` umhüllt `redact_pii` + eine
optionale LLM-NER `_llm_ner_names`); ein per-Request-`mapping` hält Platzhalter konsistent.
Anonymisierung ist **verpflichtend, nicht abschaltbar** (Backend erzwingt `anonymize=True`,
ignoriert Client-Flags); ein optionales `+ KI-Namenssuche`-Häkchen ergänzt nur *zusätzliche*
Namen. `analyze-file` schützt Traversal mit
`(base / file_rel).resolve().relative_to(base)`. `finalize` schreibt `_KI_INDEX.md`
(UTF-8, „▶ Von KI generiert") zurück in den Ordner und legt optional eine
`Verzeichnis: <name>`-RAG an. **Server-Modus-Vorbehalt:** beliebige Pfade werden gelesen UND
geschrieben — diesen Tab in Mehrbenutzer-/Server-Deployments versteckt lassen (optional,
Default versteckt).

`tools/anonymize.py` (nur stdlib-Regex): `redact_pii(text, mapping)` ersetzt E-Mails,
Telefonnummern, IBAN, URLs und — heuristisch (Anrede/Titel + Namens-Tokens) — Personennamen
durch stabile Platzhalter (`[EMAIL_1]`, `[TEL_1]`, `[PERSON_1]`…). `redact_names(text,
found_names, mapping)` wendet eine konkrete Namensliste an. Redigiert **Inhalte**, nicht
Datei-/Ordnernamen; das Mapping bleibt lokal, kommt **nicht** in die Indexdatei.

**Morphologischer Kasten** (`/api/morph/{generate,evaluate,refine-cell,ideas}` +
`/api/morph/training…`): alle Generierung über `_morph_llm(model, system, user)`
(`format:"json"`, `_parse_llm_json`). `generate` liefert Parameter mit kurzen String-Werten
(`_morph_value_str` flacht verschachtelte Objekte ab). `evaluate` bewertet eine Kombination
(score/Machbarkeit/Innovation 0–100 + Begründung/Risiken) und schlägt Alternativen vor.
`refine-cell` weitet einen Einzelwert aus oder kritisiert ihn. **`ideas`** generiert N
kreative Gesamtkonzepte (ein Wert pro Parameter + Konzepttext) fürs **Swipe-Deck**.
**Source-Grounding:** `generate`/`refine-cell`/`ideas` akzeptieren `web:bool` +
`rag_collections:[]`; `_morph_sources_context()` stellt DuckDuckGo (`search_with_sources`)
und/oder RAG-Passagen (`query_collections`, CPU-Embed) als Inspiration voran.
**Auto-Trainingsfile:** `POST /api/morph/training/add` hängt Gut/Schlecht-Beispiele an
`data/morph_training/<slug>.jsonl` (`_to_slug(problem)`; gitignored) — Quellen:
Swipe-Entscheidungen, **gelöschte ausformulierte Chips** (= „schlecht"), gespeicherte
Lösungen (= „gut"). Jede Zeile ist strukturiert **und** Chat-Format (`messages`) fürs
Finetuning. `GET …/training?problem=&format=jsonl|md` liefert die Datei (md =
lesbare Listen, ingestbar via `RAG.ingestText`); `DELETE` leert sie.

---

## 17. Mathe-Tab — Tutor, Auto-Verifizieren, Plotting

(Modell-Rollen & deterministisches Plotting bereits in §2.3.)

Der 🔢 Mathe-Tab (`mathe.js`) routet den normalen Löser zum `mathe_experte`-Agenten. Der
**`🎓 Tutor-Modus`** routet stattdessen zum `mathe_tutor` (adaptiv-sokratisch; `💡 Lösung
zeigen` ist der Notausgang). Weil kleine Modelle Verifikations-Tools im Dialog nicht
zuverlässig selbst aufrufen, ruft der Tutor zuerst `POST /api/mathe/ground`: das Backend
extrahiert die Aufgabe als SymPy-Ausdruck (ein LLM-Aufruf), berechnet die **Grundwahrheit
deterministisch** (`_mathe_sympy_facts` — behandelt `^`→`**`, `==`→`=`, `f(x)=`-Vorsatz,
implizite Multiplikation via `parse_expr`-Transformationen, direkte Aufrufe wie `diff(...)`)
und gibt verifizierte Fakten zurück, die `mathe.js` in den Chat-Request injiziert.

**Auto-Verifizieren (agentic SymPy-Loop):** ein **`🔁 Auto-Verifizieren`**-Toggle
(exklusiv zum Tutor) routet zu `POST /api/mathe/solve-verified` (SSE) statt `/api/chat`:
(1) das Modell löst, (2) die Antwort wird deterministisch gegen die SymPy-Grundwahrheit
geprüft (`_mathe_ground_facts` → `_mathe_sympy_facts`; `_mathe_check_tokens` extrahiert
numerische Lösungs-Tokens, `_mathe_solution_ok` prüft Containment), (3) bei Abweichung
fließen die SymPy-Fakten als Korrektur-Prompt zurück und das Modell löst neu (max
`_MATHE_VERIFY_ROUNDS`). Frames: `stage` (`{stage:solve|verify|fix, status, content}` →
einklappbar), `text` (finale Antwort), `done` (`{verified, checkable, rounds, facts}` →
grünes „✓ verifiziert" / gelb / Info-Badge). Nicht-numerische Ergebnisse (diff/integrate)
sind nicht strikt prüfbar → erste Lösung gilt, Fakten als Leitplanke.

Der Mathe-Tab teilt die `model_coding`-Rolle mit Code (kein eigenes Auswahlfeld), LaTeX
immer an, rendert `image`-Plot-Frames inline (volle `data:`-URI as-is) und bietet
LaTeX/PDF-Export, wenn die Antwort `$` enthält.

---

## 18. Backup & Restore

`GET /api/backup` baut ein ZIP **aller** Nutzerdaten: `profile.json`, `projects.json`,
Gespräche (aus SQLite), `plans/`, `agents/` (inkl. `favorite`), `juries/`, `code/`,
`jury_docs/`, **`profile_assets/`** (Logo/Cover/Header) und **`rag/collections.json`** — ein
voller Dump der RAG-Basen inkl. Dokumenten, Chunks und float32-Embeddings (base64).
**`api_providers.json` ist bewusst ausgeschlossen** (API-Keys), ebenso `mail.json`.

`POST /api/restore` re-importiert alles: Profil/Assets überschreiben; Projekte mergen; Pläne
skip nach Name; Agenten/Juries/Code/Jury-Docs skip nach existierender id; **RAG-Sammlungen
skip nach existierender id**; Gespräche werden immer neu angelegt (Re-Restore dupliziert
sie). DB-Helfer `rag_export()` / `rag_collection_exists()` / `rag_import_collection()` in
`db.py`. `app.js` zeigt Kategorie-Zähler im Restore-Toast und lädt danach
Profil/Branding, Agenten und RAG neu.

---

## 19. PWA / Handy-als-Frontend

Die App ist eine installierbare **PWA**: `static/manifest.json` (Icons aus
`/api/assets/icon.png`) und `static/sw.js` (App-Shell-Cache; `/api/*` immer Netzwerk, nie
gecacht) werden vom Static-Mount am Root ausgeliefert; `index.html` linkt Manifest +
theme/apple-Metas; `app.js` registriert den SW **nur im Secure Context**
(`window.isSecureContext`). Handy nutzt das Frontend, der Desktop behält das Backend:
`0.0.0.0` binden (`start_server.*`, oder `config.json` `host`/`AI_HOST`) — CORS ist offen.

**Vorbehalt:** Service Worker / „Installieren" brauchen **HTTPS oder localhost**; über
`http://<lan-ip>:8780` registriert Android den SW nicht (UI funktioniert, nur nicht
installierbar). Für echte Installation uvicorn mit selbstsigniertem Cert
(`--ssl-keyfile/--ssl-certfile`) und `https://<lan-ip>:8780` öffnen. Mobile-CSS in `app.css`
`@media (max-width:600px)`; das Swipe-Deck ist touch-first.

**Helfer (Linux, `scripts/`):** `gen_cert.sh`/`.ps1` erzeugen ein selbstsigniertes Cert
(SANs: localhost, LAN-IPs, `<host>.fritz.box`) nach `certs/` (gitignored); `start.sh` liest
optional `AI_SSL_CERT`/`AI_SSL_KEY`; `install_service.sh`/`uninstall_service.sh` legen eine
systemd-Unit an (`ai-framework.service`, `User=<you>`, `After=ollama.service`, HTTPS falls
Cert vorhanden, Autostart + `Restart=on-failure`). Endnutzer-Walkthrough:
`docs/HANDY_ZUGRIFF.md` (in `_HELP_DOC_FILES` → in die Hilfe-RAG ingestet).

---

## 20. Deployment-Varianten & Installer

**Installer-Feature-Auswahl (`install.sh` / `install.bat` → `install.ps1`):** die Installer
fragen interaktiv, welche optionalen Tabs aktiviert werden, ob externe **API**-Anbieter
erlaubt sind (local-only vs. local+API) und ob **Python im Code-Tab serverseitig ausgeführt**
werden darf, und schreiben die Wahl in `config.json`: `hidden_tabs_default` (die NICHT
aktivierten optionalen Tabs; von `_DEFAULT_HIDDEN_TABS` beim Erstaufruf konsumiert),
`enable_api` (read-only Flag aus `GET /api/profile`; `profile.js` versteckt
`#provider-section` wenn false — Default `true`) und `allow_python_exec` (Default `true`;
ebenfalls über `GET /api/profile` gespiegelt — `profile.js` blendet die Python-Option im
Code-Tab aus und `CodeIDE.disablePython()` schaltet zurück auf JS, wenn false). Der
JSON-Merge erfolgt in beiden Skripten per Python (vermeidet PowerShells
Einzelelement-Array-Eigenart). Nicht-interaktive Läufe (kein TTY) lassen die Defaults
unverändert. **`make_server`** setzt `allow_python_exec = false` (Mehrbenutzer: kein
beliebiger Code auf dem Server).

**Optionale Tabs / Erstaufruf:** RAG, Code (`ide`), Mathe, Medizin, Mail, Logs,
Verzeichnis-Analyse (`diranalyse`), Morph-Kasten (`morph`) und Jury (`jury`) sind optional,
im Profil umschaltbar (`#profile-tab-vis` → `Profile.applyTabVisibility`; `switchTab`
guardet zusätzlich). **Beim Erstaufruf (kein `user_profile.json`) sind alle neun versteckt**
— `GET /api/profile` und der `PUT`-Handler defaulten `hidden_tabs` auf
`_DEFAULT_HIDDEN_TABS`, wenn das Feld fehlt (Onboarding sendet es nicht); das Profil-Modal
sendet immer ein explizites `hidden_tabs`. Im Profil hat jeder optionale Tab ein eigenes
Häkchen (Code und Mathe getrennt, `data-tabs="ide"` / `data-tabs="mathe"`); `profile.js`
expandiert `data-tabs` (Komma-gesplittet, dedupliziert) beim Speichern.

Drei Installationsvarianten (je `.bat`+`.ps1`-Paar):
- `install` — Standard: Python 3.12 via winget + Ollama + venv.
- `make_portable` — selbstständiges Bundle ohne Systemabhängigkeiten. Nutzt **eigenen
  Ollama-Port `11500`** (schreibt `config.json` `ollama_base` um), bundelt nur die
  Whitelist-Modelle. Modell-Blobs aus **`$env:OLLAMA_MODELS` falls gesetzt** (z. B.
  `D:\OLLAMA_MODELS`), sonst `%USERPROFILE%\.ollama\models`.
- `make_server` — Mehrbenutzer-Servermodus mit `0.0.0.0`-Binding.

Die `make_*`-Skripte schließen `venv`, `__pycache__`, `.git`, `.claude`, `server.log` beim
Kopieren aus. **Troubleshooting-Helfer** (Repo-Root): `diagnose.bat` schreibt
OS/Python/Packages/Ports/Ollama-Status nach `diagnose_report.txt`; `test_chat.bat` +
`test_chat.py` treffen `/api/chat` direkt. Beide erkennen Bundle- vs. Dev-Layout.
**VRAM-Guard-Vorbehalt:** in `make_server` mit `workers > 1` koordiniert der
Einzel-Modell-Guard nicht über Worker-Prozesse — auf ~6 GB VRAM `workers = 1`
(dokumentiert in `docs/SERVER.md`).
