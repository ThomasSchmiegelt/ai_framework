# CLAUDE.md

Guidance for Claude Code (claude.ai/code) when working in this repository.

> **Full detail lives in the docs — read them before deep work:**
> developer reference `docs/ENTWICKLUNG.md`, end-user manual `BEDIENUNGSANLEITUNG.md`.
> This file is the lean quick-reference: project rules, conventions, and a map of where
> each subsystem is documented. Keep it under the 40k-char limit — put new deep detail
> in `docs/ENTWICKLUNG.md`, not here.

## Project Constraints (must follow)

- **Cross-platform:** runs on **Windows and Linux**. The Python/JS core is platform-neutral — keep it that way: no OS-specific paths, no platform-only dependencies. Guard platform-specific behavior (e.g. systemd) with a runtime check (`sys.platform`). Both `.bat`/`.ps1` (Windows) and `.sh` (Linux) scripts coexist; never remove one set.
- **MIT / open source:** all added code and dependencies must be MIT-compatible. Do **not** introduce AGPL/GPL/copyleft dependencies.

## What This Project Is

AI_Framework_Thomas is a German-language, **general-purpose** AI chat interface that runs entirely locally: local Ollama LLMs wrapped in a **FastAPI** backend with a **vanilla-JS** frontend, plus a tool-calling agentic loop, SQLite conversation persistence, and productivity tools (engineering calculators, material lookup, unit conversion, SymPy solver, matplotlib charting, PDF/DOCX/PPTX/LaTeX generation, planner/CPM, research, RAG).

Derived from IG-11 as the general variant. The UI has **17 tabs** (Chat, Canvas, Agenten, Recherche, RAG, Dokumente, Medizin, Mathe, Mail 🚧, Planer, Matrix, Anfrage, Code, Verzeichnis-Analyse, Morph-Kasten, Jury, Logs) and **seven modes** (`maschinenbau`, `ki`, `soziales`, `marketing`, `finanz`, `geschaeftsfuehrung`, plus a user-configurable `custom`) that drive the color scheme and an optional domain framing. See `docs/ENTWICKLUNG.md` for the mode/system-prompt machinery (`_augment_prefix`, `pure_llm`, `lang`).

## Running the App

**Linux (this machine):**
```bash
source venv/bin/activate
uvicorn main:app --host 127.0.0.1 --port 8780 --reload   # dev
uvicorn main:app --host 0.0.0.0  --port 8780 --reload    # server mode
```

**Windows:**
```powershell
.\venv\Scripts\Activate.ps1
uvicorn main:app --host 127.0.0.1 --port 8780 --reload
```
Or use the scripts: `start.bat` / `start_server.bat` (Windows), `scripts/start.sh` (Linux).

Ollama must run separately on `http://localhost:11434` with at least one `config.json` model pulled. For RAG, pull the embedding model once: `ollama pull nomic-embed-text`. Browser: <http://localhost:8780>.

## Key Configuration

`config.json` controls allowed models, default model, `embed_model` (RAG embeddings, default `nomic-embed-text`), Ollama URL, port/host, and installer flags (`hidden_tabs_default`, `enable_api`, `allow_python_exec`). **No environment variables** — everything is read from this file at startup. `allow_python_exec` (default `true`) gates server-side Python execution in the Code tab (`POST /api/code/run-python`); `make_server` defaults it to `false` (runs arbitrary code on the host).

## Architecture (sketch)

```
Frontend (Vanilla JS, 16 tabs)   static/js/app.js, chat.js, canvas.js, …
       ↓ SSE streaming
Backend (FastAPI, async)         main.py  (~5800 lines)
       ↓ tools/llm.py (unified)
Ollama (local)  ──or──  external OpenAI-compatible API (OpenRouter, OpenAI, Groq …)
       ↓
SQLite (aiosqlite)               data/ai_framework_thomas.db  — schema in db.py
```

Async throughout (httpx, aiofiles, aiosqlite). **All LLM calls go through `tools/llm.py`** (`_llm.chat` / `_llm.stream`), which transparently routes to local Ollama or an external OpenAI-compatible provider based on the model name (`"<provider_id>::<model>"` prefix = remote). `/api/chat` runs an agentic loop (max 8 iterations) and streams SSE frames (`text`, `canvas`, `image`, `map`, `tool_start`, `tool_done`, `rag`, `adaptive`, `error`, `done`).

## Development Conventions (must follow)

These prevent the most common backend mistakes:

- **API endpoint ordering:** register all `@app.<method>` routes **before** the `app.mount("/", StaticFiles(...))` call at the bottom of `main.py`. The static mount is a catch-all — routes after it are unreachable.
- **VRAM guard (~6 GB, one local model at a time):** every Ollama call **must** be wrapped `async with _model_session(model), httpx.AsyncClient() as client:` (using `_llm.chat`/`_llm.stream` inside). `_model_session` unloads the previous model on switch and serializes generations via `_model_lock`. It is a **no-op for remote models**. Never call Ollama outside it.
- **Model validation:** always pass user-supplied model names through `_pick_model(body.get("model"), fallback)`. Rejects placeholders (`Lade…`, `qwen3.6-16k:latest`), falls back to `fallback or DEFAULT_MODEL` (`ministral-3:3b`).
- **Parsing LLM JSON:** small models wrap JSON in `<think>` tags and code fences. Strip them, extract JSON via regex, always provide a fallback — see `_parse_llm_json` / `_derive_adaptive_prompt` / planner endpoints for the pattern.
- **New frontend modules:** add `<script src="/static/js/mymodule.js">` to `static/index.html` **and** call `MyModule.init()` in the `DOMContentLoaded` handler in `app.js`.

## Subsystem Map

Each subsystem's deep documentation is in `docs/ENTWICKLUNG.md` (section numbers below). The lines here are orientation only.

| Subsystem | Key files | Detail |
|---|---|---|
| Agentic loop & SSE | `main.py` `_chat_generator` | ENTWICKLUNG §2.2 |
| VRAM guard & model roles | `main.py` `_model_session`, `_model_for` | §2.3 |
| Modes / system-prompt vorspann | `main.py` `_augment_prefix` | §11 |
| LLM abstraction & API providers | `tools/llm.py`, profile modal | §12 |
| RAG engine | `tools/rag.py`, `rag.js`, `db.py` | §13 |
| Agents (favorites, slash, adaptive, legal) | `data/agents/`, `agents.js`, `main.py` | §14 |
| Jury (multi-agent evaluation) | `data/juries/`, `jury.js`, `/api/jury/evaluate`; große Dokumente per Map-Reduce (`_chunk_for_ctx`, `_JURY_CHUNK_SYSTEM`), alle Calls mit `num_ctx`, Tokens im `done`-Frame | §15 |
| Anfrage-Auswertung (RFQ, XLS-Stapel) | `rfq.js`, `/api/rfq/*` (eval, to-plan, ask), eigene Bewertungsspalten (Agent/Prompt, `_sanitize_rfq_columns`), `tools/files.read_table` | — |
| Mehrere Ressourcenlisten | `/api/capacity/lists*` + `/api/capacity/selection`, `data/capacity_lists.json` (Migration aus `capacity.json`); `_load_capacity()` = Vereinigung der aktiven Listen | — |
| Token-Zähler | `static/js/tokens.js` (`TokenMeter`), Profil `price_per_1k_in/out`+`currency`; `tokens:{in,out}` aus Chat-`done`, RFQ-`done`, `/api/code/assist`, `/api/rfq/ask`; `_llm_tok()` + `tools/llm.py` usage-Mapping | — |
| Code-Assistent (Code-Tab) | `ide.js` `_assist`/`_renderClarify`, `/api/code/assist` (Rückfragen→Code, Coding-Agent + `example_code`, adaptiv via `_derive_adaptive_prompt`) | — |
| Dokument-Experte (verallgemeinert) | `/api/agents/from-legal` mit `domain` (Fachgebiet/Rolle), `agents.js` `createLegalAgent` | §14 |
| Kapazität & Zukauf (Planer) | `planner.js` `_openSchedule`/`_capacityAnalysisHtml`, `Planner.openPlan` | — |
| Auto-Strukturieren (Planer) | `planner.js` `_openAutoStructure`/`_applyAutoStructure`, `/api/plans/auto-structure` (LLM-Abhängigkeiten+Phasen, Ressourcen-Entzerrung, zyklensicher) | — |
| `/plan`-Orchestrator (Chat) | `chat.js` `_parsePlan`/`runPlan`/`_handlePlanEvent`/`_applyPlan`, SSE `/api/plan/strategy` (`_plan_strategy_generator`): Strategie→Agenten→Plan→Jury als **Vorschau**, speichert nichts; Anlegen via vorhandene `/api/agents`+`/api/plans`+`/api/juries`. Feste Agenten via `/plan … /kürzel` (`_findAgentByToken` → `pinned_agents`, `_plan_pinned_agents`): pinned zuerst, LLM ergänzt nur, beim Anlegen per vorhandener `id` wiederverwendet (kein Duplikat). „Alles anlegen" erstellt zuerst ein **Projekt** und verknüpft Plan/Agenten/Jury (Feld `project_id` in `create_plan`/`create_jury`/`AgentDef`) + ordnet die Unterhaltung zu | — |
| Verzeichnis-Analyse & Morph-Kasten | `dir_analysis.js`/`morph_box.js`, `tools/anonymize.py` | §16 |
| Mathe (tutor, auto-verify, plotting) | `mathe.js`, `main.py`, `tools/engineering.py` | §2.3, §17 |
| Medizin 2-model pipeline | `medizin.js`, `/api/medizin/*` | §2.3 |
| Backup/restore & branding | `/api/backup`, `/api/restore`, `data/profile_assets/` | §18 |
| PWA / phone-as-frontend | `static/manifest.json`, `static/sw.js`, `scripts/` | §19 |
| Deployment variants & installers | `install.*`, `make_portable.*`, `make_server.*` | §20 |
| Database schema | `db.py` | §4 |
| Tools package | `tools/*.py` | §5 |
| Frontend modules | `static/js/*.js` | §6 |
| Data file formats | `data/` | §7 |

## Tools (`tools/` package) — one-liners

| Module | Responsibility |
|---|---|
| `llm.py` | **Unified LLM access** (local Ollama ⇄ external OpenAI-compatible API); Ollama-shaped responses both ways |
| `search.py` | DuckDuckGo async search → sources list |
| `files.py` | Document extraction: PDF (pypdf), DOCX, XLSX, images |
| `export.py` | Generate DOCX/XLSX/PPTX/PDF/LaTeX (matplotlib mathtext for PDF formulas; beamer/article for LaTeX) |
| `engineering.py` | Pint units, SymPy solver, `plot_chart` (data series, model-callable) + `plot_function` (server-side only), VDI 2230 bolts |
| `materials.py` | ~40-material properties database |
| `report.py` | PDF/DOCX reports with LaTeX equations |
| `routing.py` | `route_planner` chat tool: Nominatim + OSRM → `map` frame |
| `imaging.py` | Image helpers for illustrated presentations |
| `anonymize.py` | PII redaction for Verzeichnis-Analyse (stdlib regex only) |
| `mail.py` | 🚧 read-only IMAP/POP3 (stdlib); sending is always manual |
| `rag.py` | RAG: cleanup, chunking, Ollama embeddings (CPU), NumPy cosine search; VRAM tiers, per-base `strictness`/`char_limit` |

Calculations run inside `_safe_exec()` in `main.py` — a restricted `exec()` sandbox (no file I/O or network; whitelisted math/numpy/scipy/sympy).

## Dependencies

Python: FastAPI, Uvicorn, httpx, ddgs, pypdf, python-docx, openpyxl, python-pptx, Pillow, python-multipart, aiofiles, aiosqlite, SymPy, NumPy, SciPy, Pint, matplotlib (pinned in `requirements.txt`). **No frontend build step** — plain HTML/CSS/JS served by FastAPI `StaticFiles`.

## Deployment (summary)

Three variants, each with a `.bat`/`.ps1` (and Linux `.sh` where applicable) pair: `install` (standard: Python 3.12 + Ollama + venv), `make_portable` (self-contained bundle, own Ollama port `11500`), `make_server` (multi-user `0.0.0.0`; use `workers = 1` on ~6 GB VRAM — the VRAM guard doesn't coordinate across worker processes). Installers interactively pick optional tabs (`hidden_tabs_default`), API on/off (`enable_api`), and Python execution on/off (`allow_python_exec`). Default agents seed from `defaults/agents/` on first run. Full detail: `docs/ENTWICKLUNG.md` §20, `docs/SERVER.md`.
