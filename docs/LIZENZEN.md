# AI_Framework_Thomas — Verwendete Bibliotheken & Lizenzen

**Stand:** Mai 2026 · Grundlage für die Lizenzprüfung.

Die Python-Lizenzangaben stammen direkt aus den **Paket-Metadaten** des
installierten venv (`pip-licenses`). Bei Versionswechseln und für den
verbindlichen Lizenztext bitte die jeweilige `LICENSE`-Datei / PyPI-Seite prüfen.

---

## ⚠️ Zuerst prüfen — besondere Fälle

| Komponente | Lizenz | Hinweis |
|---|---|---|
| **LLM-Modelle** (Standard: `ministral-3:3b`, `nomic-embed-text`; weitere bei Bedarf vom Nutzer geladen) | modellspezifisch | **Keine Standard-OSS-Lizenzen.** Mistral-/Ministral-Modelle je nach Variante *Apache-2.0* oder *Mistral Research/Commercial License*. Für selbst nachgeladene Modelle gilt deren jeweilige Lizenz (z. B. *Gemma Terms of Use* von Google mit Nutzungsbeschränkungen). **Exaktes Modell + Lizenz auf der Ollama-Modellseite verifizieren** (Tags sind anpassbar). |
| **certifi** | **MPL-2.0** | Schwaches Copyleft auf Dateiebene — bei reiner Nutzung/Weitergabe i. d. R. unkritisch, aber zu beachten. |

Alle übrigen Python-Pakete stehen unter **permissiven** Lizenzen (MIT, BSD,
Apache-2.0, PSF) ohne Copyleft-Pflichten.

---

## 1. Python — direkte Abhängigkeiten (`requirements.txt`)

| Paket | Version | Lizenz | Zweck in AI_Framework_Thomas |
|---|---|---|---|
| fastapi | 0.136.3 | MIT | Web-Framework (Backend) |
| uvicorn[standard] | 0.48.0 | BSD-3-Clause | ASGI-Server |
| httpx | 0.28.1 | BSD | HTTP-Client (Ollama-Anbindung) |
| ddgs | 9.14.4 | MIT | DuckDuckGo-Websuche |
| pypdf | 4.0.0+ | BSD-3-Clause | PDF-Textextraktion |
| python-docx | 1.2.0 | MIT | DOCX lesen/schreiben |
| openpyxl | 3.1.5 | MIT | XLSX lesen/schreiben |
| python-pptx | 1.0.2 | MIT | PPTX-Export |
| Pillow | 12.2.0 | MIT-CMU (HPND) | Bildverarbeitung |
| python-multipart | 0.0.29 | Apache-2.0 | Datei-Uploads |
| aiofiles | 25.1.0 | Apache-2.0 | Async-Dateizugriff |
| aiosqlite | 0.22.1 | MIT | Async-SQLite |
| numpy | 2.4.6 | BSD-3-Clause (u. a.) | Berechnungen |
| scipy | 1.17.1 | BSD | Berechnungen (Optimierung/Algebra) |
| sympy | 1.14.0 | BSD | Symbolischer Gleichungslöser |
| Pint | 0.25.3 | BSD | Einheitenumrechnung |
| matplotlib | 3.10.9 | PSF (BSD-artig) | Diagramme **und PDF-Export** (Dokumente/Präsentationen, ohne LaTeX) |

---

## 2. Python — transitive Abhängigkeiten (mitinstalliert)

| Paket | Version | Lizenz |
|---|---|---|
| starlette | 1.2.0 | BSD-3-Clause |
| pydantic / pydantic_core | 2.13.4 / 2.46.4 | MIT |
| annotated-types / annotated-doc | 0.7.0 / 0.0.4 | MIT |
| typing_extensions | 4.15.0 | PSF-2.0 |
| typing-inspection | 0.4.2 | MIT |
| anyio | 4.13.0 | MIT |
| h11 | 0.16.0 | MIT |
| h2 / hpack / hyperframe | 4.3.0 / 4.1.0 / 6.1.0 | MIT |
| httpcore | 1.0.9 | BSD-3-Clause |
| httptools | 0.8.0 | MIT |
| websockets | 16.0 | BSD-3-Clause |
| watchfiles | 1.2.0 | MIT |
| **certifi** | 2026.5.20 | **MPL-2.0** |
| idna | 3.17 | BSD-3-Clause |
| click | 8.4.1 | BSD-3-Clause |
| colorama | 0.4.6 | BSD |
| python-dotenv | 1.2.2 | BSD-3-Clause |
| PyYAML | 6.0.3 | MIT |
| brotli | 1.2.0 | MIT |
| socksio | 1.0.0 | MIT |
| primp | 1.3.1 | MIT | *(von ddgs)* |
| fake-useragent | 2.2.0 | Apache-2.0 | *(von ddgs)* |
| lxml | 6.1.1 | BSD-3-Clause | *(von docx/pptx)* |
| et_xmlfile | 2.0.0 | MIT | *(von openpyxl)* |
| xlsxwriter | 3.2.9 | BSD-2-Clause |
| mpmath | 1.3.0 | BSD | *(von sympy)* |
| flexcache / flexparser | 0.3 / 0.4 | BSD | *(von Pint)* |
| platformdirs | 4.10.0 | MIT | *(von Pint)* |
| contourpy | 1.3.3 | BSD | *(von matplotlib)* |
| cycler | 0.12.1 | BSD | *(von matplotlib)* |
| fonttools | 4.63.0 | MIT | *(von matplotlib)* |
| kiwisolver | 1.5.0 | BSD | *(von matplotlib)* |
| pyparsing | 3.3.2 | MIT | *(von matplotlib)* |
| packaging | 26.2 | Apache-2.0 / BSD-2-Clause |
| python-dateutil | 2.9.0 | Apache-2.0 / BSD |
| six | 1.17.0 | MIT |
| pip | 25.0.1 | MIT |

> `pip-licenses`, `prettytable`, `wcwidth` waren nur temporäres Prüfwerkzeug und
> gehören **nicht** zum Auslieferungsumfang (nicht in `requirements.txt`).

---

## 3. Frontend (per CDN geladen, `static/index.html`)

| Bibliothek | Version | Lizenz | Zweck |
|---|---|---|---|
| marked.js | latest (jsDelivr) | MIT | Markdown-Rendering im Chat |
| highlight.js | 11.9.0 | BSD-3-Clause | Code-Syntax-Hervorhebung |
| KaTeX | 0.16.9 | MIT | Mathematische Formeln (Chat, Dokumente, Canvas-Folien) |

> Beide werden zur Laufzeit von `cdn.jsdelivr.net` geladen. Für einen
> vollständig **offline**-fähigen bzw. lizenzrechtlich gebündelten Betrieb
> können sie lokal unter `static/js/` abgelegt und die CDN-Verweise ersetzt werden.

Eigener Frontend-Code (`static/js/*.js`, `static/css/app.css`) gehört zu AI_Framework_Thomas
selbst und unterliegt keiner Drittlizenz.

---

## 4. Externe Tools & Laufzeit

| Komponente | Lizenz | Verwendung |
|---|---|---|
| **Ollama** | MIT | LLM-Laufzeit (separat installiert, `localhost:11434`) |
| **LLM-Modelle** | modellspezifisch ⚠️ | siehe Warnhinweis oben — auf Ollama-Seite verifizieren |
| Python (CPython) | PSF License | Laufzeit; bei `make_portable` als *Embeddable Package* gebündelt |
| pdflatex / LaTeX | LPPL | **Optional**, nur falls installiert — `tools/report.py` nutzt es für PDF-Reports; ohne LaTeX greift der DOCX-Fallback |
| NSSM | Public Domain | Nur `make_server` — Windows-Dienst-Wrapper (zur Laufzeit heruntergeladen) |
| winget | MIT | Nur `install.ps1` — installiert Python & Ollama |

---

## 5. Zusammenfassung für die Prüfung

- **Unkritisch (permissiv):** der weitaus größte Teil — MIT, BSD, Apache-2.0, PSF.
  Üblicherweise nur Copyright-/Lizenzhinweis bei Weitergabe beizulegen.
- **Genau prüfen:**
  1. **LLM-Modelle** — Nutzungsbedingungen von Gemma bzw. Mistral/Ministral
     gegen den geplanten (kommerziellen?) Einsatz abgleichen.
  2. **certifi (MPL-2.0)** — geringes Risiko, der Vollständigkeit halber gelistet.
- **CDN-Abhängigkeit:** marked.js, highlight.js & KaTeX für Offline-Auslieferung lokal bündeln.

*Diese Übersicht dient der Orientierung und ersetzt keine Rechtsberatung. Maßgeblich
ist jeweils der Originallizenztext der konkret eingesetzten Version.*
