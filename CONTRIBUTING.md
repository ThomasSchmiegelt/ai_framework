# Mitwirken an AI_Framework_Thomas

Danke für dein Interesse! Dieses Projekt ist ein **deutschsprachiges, lokal laufendes
KI-Framework** (FastAPI-Backend + Vanilla-JS-Frontend, Ollama-LLMs, Tool-Calling, RAG,
lokale Bildgenerierung u. v. m.). Diese Anleitung fasst zusammen, wie du beiträgst.

## Erste Schritte

**Linux**
```bash
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
uvicorn main:app --host 127.0.0.1 --port 8780 --reload
```

**Windows**
```powershell
py -3.12 -m venv venv; .\venv\Scripts\Activate.ps1
pip install -r requirements.txt
uvicorn main:app --host 127.0.0.1 --port 8780 --reload
```

Ollama muss separat laufen (`http://localhost:11434`) mit mindestens einem in
`config.json` erlaubten Modell. Für RAG einmalig `ollama pull nomic-embed-text`.
Ausführliche Doku: `docs/ENTWICKLUNG.md` (Entwickler) und `BEDIENUNGSANLEITUNG.md`
(Endnutzer). Der schnelle Überblick steht in `CLAUDE.md`.

## Projekt-Grundsätze (bitte einhalten)

- **Plattformübergreifend:** läuft auf **Windows und Linux**. Der Python/JS-Kern bleibt
  plattformneutral – keine OS-spezifischen Pfade, keine plattform-exklusiven
  Abhängigkeiten. Plattformspezifisches mit `sys.platform` absichern. `.bat`/`.ps1`
  **und** `.sh` koexistieren – niemals eine Seite entfernen.
- **MIT / Open Source:** neuer Code und neue Abhängigkeiten müssen **MIT-kompatibel**
  sein. Keine AGPL/GPL/Copyleft-Abhängigkeiten (die wenigen dokumentierten Ausnahmen
  siehe `CLAUDE.md` – nicht erweitern).
- **Kein Build-Schritt im Frontend:** reines HTML/CSS/JS, von FastAPI `StaticFiles`
  ausgeliefert.

## Wichtige Konventionen

- **Routen vor dem Static-Mount:** alle `@app.<method>`-Routen **vor** dem
  `app.mount("/", StaticFiles(...))` am Ende von `main.py` registrieren (der Mount ist
  ein Catch-all).
- **VRAM-Guard:** jeder Ollama-Aufruf gehört in
  `async with _model_session(model), httpx.AsyncClient() as client:` (nutzt intern
  `_llm.chat`/`_llm.stream`). Für Remote-Modelle ein No-op.
- **Modellwahl** immer über `_pick_model(...)`; LLM-JSON robust parsen (siehe
  `_parse_llm_json`).
- **Neue Frontend-Module:** `<script src="/static/js/…">` in `static/index.html`
  eintragen **und** im `DOMContentLoaded`-Handler in `app.js` initialisieren.
- Beim Ändern von JS/CSS die `?v=`-Cache-Marke in `index.html` und ggf. `CACHE` in
  `static/sw.js` anheben.

## Tests / Prüfen

Es gibt **keine automatisierte Test-Suite und keinen Linter**. Änderungen bitte durch
Ausführen der App und Bedienen des betroffenen Tabs prüfen. Zwei Backend-Smoke-Tests:
- `python3 test_chat.py [appdir]` – ruft `/api/chat` direkt auf.
- `diagnose.bat` (Windows) – sammelt Umgebungs-/Ollama-Status.

## Branch- & Commit-Workflow

- Für Änderungen einen **Feature-Branch** von `main` anlegen, danach per
  `git merge --no-ff` nach `main` zusammenführen.
- **Aussagekräftige Commit-Nachrichten** (gerne im Format `bereich: kurzbeschreibung`).
- **Keine privaten/Laufzeitdaten** committen (siehe `.gitignore`: `data/…`, `venv/`,
  Modelle, Zertifikate, PST/Rechnungen/Patente usw.).
- Vor dem Push: App startet, betroffener Bereich funktioniert, Doku (`CLAUDE.md` /
  `docs/ENTWICKLUNG.md` / `BEDIENUNGSANLEITUNG.md`) bei neuen Funktionen aktualisiert.

## Pull Requests

Bitte die PR-Vorlage ausfüllen (Was/Warum, betroffene Bereiche, Testschritte,
Plattform-/MIT-Konformität). Kleine, fokussierte PRs sind willkommen.

## Fehler & Ideen

Nutze die **Issue-Vorlagen** (Bug bzw. Feature). Sicherheitslücken **nicht** öffentlich
melden – siehe [`SECURITY.md`](SECURITY.md).
