# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Project Is

AI_Framework_Thomas is a German-language, **general-purpose** AI chat interface that runs entirely locally. It wraps local Ollama LLMs in a FastAPI backend with a vanilla JS frontend, adding a tool-calling agentic loop, SQLite conversation persistence, and productivity tools (engineering calculators, material lookup, unit conversion, SymPy solver, matplotlib charting, PDF/DOCX/PPTX generation, planner/CPM, research).

It is the general variant derived from IG-11. **Seven modes** (`maschinenbau`, `ki`, `soziales`, `marketing`, `finanz` gray, `geschaeftsfuehrung` yellow, plus a user-configurable `custom` mode in violet) are chosen in the user profile and drive (a) the UI/slide color scheme via CSS variables (`html[data-mode=…]` in `app.css`), and (b) — when `mode_prompt` is on — a domain framing prepended to system prompts (`_mode_prefix()`, applied keyword-gated per question via `_MODE_KEYWORDS`). The **`custom`** mode is fully user-defined: its name, domain framing and (optional) keywords come from the profile fields `custom_mode_name` / `custom_mode_prompt` / `custom_mode_keywords` (read by `_mode_prompt_text()` / `_mode_keywords()`; with no keywords the framing applies to every question). The full automatic system-prompt vorspann is assembled by `_augment_prefix()` in `main.py`: base anti-hallucination guard + mode framing + persona/profile (`tone`) + a LaTeX-formula rule + a citation rule (norms/laws should be named so the frontend can linkify them). The profile flag **`pure_llm`** ("keine Modi / LLM pur") makes `_augment_prefix()` drop the guard/mode/persona/formula/citation prepend — only the language rule remains (an explicitly chosen agent and active RAG bases still apply). The profile field **`lang`** (`de`/`en`, default `de`) drives both the UI language (frontend `static/js/i18n.js` — a DE→EN dictionary that translates the static HTML shell on the fly, German is the source; deep JS-generated strings are still German) and the answer language: when `lang=en`, `_lang_rule()` appends `_LANG_RULE_EN` (an "answer in English" instruction) to the system-prompt vorspann, applied even under `pure_llm`. The toggle lives in the profile modal (`#profile-lang`, wired in `profile.js` → `I18n.setLang`). Branding (logo, template cover, template header) is **not** shipped as files; the user uploads it in the profile (`/api/profile/asset/{logo|cover|header}`, stored under `data/profile_assets/`, auto-resized via Pillow). `bilder/` is empty.

## Running the App

```bash
# Activate the virtualenv first (created by install.ps1)
.\venv\Scripts\Activate.ps1

# Development (localhost only)
uvicorn main:app --host 127.0.0.1 --port 8780 --reload

# Server mode (all interfaces)
uvicorn main:app --host 0.0.0.0 --port 8780 --reload
```

Or use the provided scripts: `start.bat` (single-user) / `start_server.bat` (multi-user).

Ollama must be running separately on `http://localhost:11434` with at least one model from `config.json` pulled.

## Key Configuration

`config.json` controls allowed models, default model, **`embed_model`** (RAG embeddings, default `nomic-embed-text`), Ollama URL, and server port. No environment variables are used — everything is read from this file at startup. For RAG, pull the embedding model once: `ollama pull nomic-embed-text`.

## Architecture

```
Frontend (Vanilla JS, 10 tabs) static/js/app.js, chat.js, canvas.js, ide.js, planner.js, …
       ↓ SSE streaming
Backend (FastAPI)              main.py  (~4300 lines)
       ↓ httpx
Ollama (local LLM)             localhost:11434
       ↓
SQLite (aiosqlite)             data/ai_framework_thomas.db  — schema in db.py
```

The backend is async throughout (httpx, aiofiles, aiosqlite).

Full developer documentation: `docs/ENTWICKLUNG.md`. End-user manual: `BEDIENUNGSANLEITUNG.md`.

### Agentic Loop (`main.py`)

The `/api/chat` endpoint runs an agentic loop (max 8 iterations):
1. Send request to Ollama with the active tool definitions
2. Parse response — Ollama may return native `tool_calls` JSON **or** inline `<call_tool>`/`<tool_call>` XML tags (the latter for models that don't support structured tool-calling)
3. Execute the tool, append the result, repeat
4. Stream output tokens to the client via SSE as `data: {...}` JSON frames with `type` field: `text`, `canvas`, `image`, `map` (route), `tool_start`, `tool_done`, `error`, `done`

The Ollama payload sets a low **temperature** (`0.3`, or `0.1` when `request.science`) to curb hallucinations of the small default model and make tool-calling more reliable. If the model returns **empty content with no tool calls**, `_chat_generator` emits a clear `error` frame (instead of a silent blank reply) and logs an `empty_response`/per-iteration `llm_response` diagnostic via `_write_log` (only when the Logs toggle is active).

**Presentation canvas fallback:** small models often don't call the `create_presentation` tool and just write prose. If the active agent is presentation-capable (`create_presentation` in its tools → `presentation_capable`) and no `canvas` frame was emitted but the reply looks like a presentation, `_chat_generator` calls `_text_to_presentation(text, model)` (a second `format:"json"` call) to convert the prose into slides and emits a `canvas` frame anyway — so the Präsentations-Agent always yields a canvas presentation.

Beyond `/api/chat`, several **non-streaming AI endpoints** return structured JSON parsed from the local LLM (always with `<think>`/code-fence stripping + regex extraction + a fallback): `/api/derive-persona`, `/api/analyze-image` (vision), and planner endpoints `/api/plans/{derive-agent,suggest-tasks,generate}`. Use `_pick_model(body.get("model"))` to validate the requested model (falls back to `DEFAULT_MODEL`). **Science mode:** `/api/research` and matrix research (which posts to `/api/chat` with `science: true`) always prepend `_SCIENCE_PROMPT` (accuracy-first, source-bound, no fabrication) — independent of `pure_llm`. **Per-task plan research:** `POST /api/plans/{pid}/research-task` runs the pipeline analyze → adaptive persona (`_derive_adaptive_prompt`) → web search (`tools.search`) → scientific Markdown dossier → embed into an auto-created plan-specific RAG (`_ensure_plan_rag`, named `Plan: <name>`, stored as `plan["rag_collection_id"]`) → attach the dossier to the task (`task["doc"]`/`doc_role`/`researched`). `planner.js` drives it per-task (🔬) and as an abortable/resumable batch (skips already-`researched` tasks); 📄 opens the dossier. **Vision:** `/api/analyze-image` and the illustrated-presentation feature send images in the `images` field; `ministral-3:3b` (the default) is vision-capable and fast for image analysis. If a vision-only task needs a different model, assign one and ensure it is pulled.

### VRAM guard — only ONE model resident at a time

Target hardware has limited VRAM (~6 GB), so only one model may be resident at a
time. By default only `ministral-3:3b` is installed/loaded; any other model is
pulled on demand and assigned per role in the profile (see **Model roles** below).
`main.py` defines `_model_lock` (asyncio.Lock), `_loaded_model`,
and the `_model_session(model)` async context manager: on model switch it unloads
the previous model (`Ollama keep_alive=0`) before the new one loads, and the lock
serializes all generations so concurrent requests can't load two models at once.
**Every Ollama call site must be wrapped in `async with _model_session(model), httpx…:`.**

### Model roles (profile)

There is no hardcoded model beyond `DEFAULT_MODEL` (`ministral-3:3b`). The profile
holds three optional role assignments — `model_general`, `model_coding`,
`model_science` — surfaced in the profile modal as **Allgemein / Programmieren /
Wissenschaftlich** (selects populated from all installed Ollama models via
`/api/models`, which no longer filters by `allowed_models`). `_model_for(role)` in
`main.py` returns the assigned model or `DEFAULT_MODEL`. Wiring:
- **Allgemein** → the sidebar model selector defaults to it (`app.js loadModels`).
- **Programmieren** → the Code-IDE assistant (`ide.js` uses `Profile.modelFor('coding')`),
  and in `_chat_generator` any `code_ide`-capable agent (e.g. the `coder` agent, whose
  own `model` is now `null`) resolves to it.
- **Wissenschaftlich** → `/api/research` (`_pick_model(request.model, _model_for("science"))`)
  and the science path in `_chat_generator` (when no specific non-general model was chosen).
`Profile.modelFor(role)` (`profile.js`) exposes the same resolution to the frontend.
`_pick_model(m, fallback)` accepts any installed model name and rejects placeholders
(`Lade…`, the legacy `qwen3.6-16k:latest`), falling back to `fallback or DEFAULT_MODEL`.

### Sidebar agents = favorites; chat quick-select

The sidebar agent selector lists **only agents flagged `favorite: true`** (plus
„Kein Agent" + adaptive). Each agent card in the 🤖 Agenten tab has a ⭐ toggle
(`AgentManager.toggleFavorite` → PUT with flipped `favorite`; `AgentDef.favorite`
persists it). The chat input toolbar has two quick-select toggles — **📊 Präsentation**
and **💻 Programmieren** — that set the agent selector to `presenter` / `coder`
(toggling off → „Kein Agent"); they add the option on the fly if the agent isn't a
favorite. `coder` and `presenter` ship as favorites by default.

### Tools (`tools/` package)

| Module | Responsibilities |
|---|---|
| `search.py` | DuckDuckGo async search, returns sources list |
| `files.py` | Document extraction: PDF (pypdf), DOCX, XLSX, images |
| `export.py` | Generate DOCX, XLSX, PPTX, **PDF** and **LaTeX** from chat/document content (`_embed_b64_image` fits images aspect-preserving/contain into the box — no distortion). `to_pdf` is pure matplotlib and renders LaTeX formulas via mathtext (pre-validated/crash-safe, no TeX install needed); `to_latex` emits a `.tex` source (document → `article`, presentation → `beamer`; Markdown → LaTeX, formulas stay math). Endpoints `/api/export/{pdf,latex}` |
| `engineering.py` | Unit conversion (Pint), equation solver (SymPy), charting from value series (`plot_chart`), **function-graph from a term** (`plot_function`: SymPy lambdify, `^`/implicit mult./`f(x)=` prefix, multiple functions with `;`), bolt calculator (VDI 2230). Both plot tools stream an `image` frame; a tone-/mode-independent `_PLOT_RULE` in `_augment_prefix` nudges the model to call `plot_function` whenever a function is mentioned |
| `materials.py` | ~40-material properties database (steels, aluminium, titanium, stainless, plastics) |
| `report.py` | PDF/DOCX report generation with LaTeX equation support |
| `routing.py` | Route planning for the `route_planner` chat tool: geocoding (Nominatim) + routing (OSRM); returns a `map` frame rendered as an interactive Leaflet map in `chat.js` (needs internet) |
| `imaging.py` | Image helpers for the illustrated-presentation feature: descriptive-filename heuristic + Pillow downscale |
| `mail.py` | **🚧 in development.** Read-only IMAP/POP3 mailbox access (stdlib only: `imaplib`/`poplib`/`email`). `domain_of()` parses the sender domain; `clean_mail_text()` strips quoted history/signature/disclaimer before RAG ingest. Drives the Mail tab (filter by sender/subject/domain → up to 4 actions per mail: RAG/agent-task/→docgen/note; reusable rules in `data/mail_rules.json`; endpoints `/api/mail/{config,list,message,to-rag,rules,action/rag,action/agent}`). **Sending is always manual** (clipboard/mailto) — no SMTP/auto-send. |
| `rag.py` | RAG engine: document cleanup (`clean_text`), char-based overlapping `chunk_text`, Ollama embeddings (`embed`, forced to CPU via `num_gpu=0` on small-VRAM tiers so they don't evict the chat model), and NumPy brute-force cosine search (`query_collections`). VRAM tiers (`none`/`4gb`/`6gb`/`12gb`) preset chunk size, overlap, top-k, embed device **and `char_limit`** (max chars of injected context — enforced per query in `query_collections`, using the largest limit among the selected collections). Per-base `strictness` (`kreativ`/`ausgewogen`/`korrekt`) selects the RAG injection wording (strictest among selected bases wins). Embeddings stored as float32 BLOBs in SQLite (`rag_collections`/`rag_documents`/`rag_chunks` in `db.py`; `char_limit` and `strictness` columns added with migrations). Ingest sources: a document upload, a conversation (`POST /api/rag/collections/{id}/from-conversation`, optional `delete_after` = move), or arbitrary text (`POST /api/rag/collections/{id}/from-text` — used by the "📚 In Wissensdatenbank" buttons in research/matrix). `rag.js` exposes reusable `pickCollection()`/`ingestText()` (collection-picker modal). Embedding model from `config.json` `embed_model` (default `nomic-embed-text`, **must be pulled in Ollama**). |

Calculations run inside `_safe_exec()` in `main.py` — a restricted `exec()` sandbox with no file I/O or network, only whitelisted math/numpy/scipy/sympy.

### Database (`db.py`)

- `conversations` table: id, title, model, agent_id, canvas_json, timestamps
- `messages` table: rowid, conv_id, seq, role, content, images_json
- `messages_fts` FTS5 virtual table for full-text search with auto-maintenance triggers
- On startup, legacy JSON files in `data/conversations/` are migrated into SQLite automatically

### Frontend Modules (11 tabs: Chat, Canvas, Agenten, Recherche, RAG, Dokumente, Mail [🚧 in development], Planer, Matrix, Code, Logs)

- `app.js` — Global state, model loading, tab switching, backup/restore, module init
- `chat.js` — SSE streaming consumer, message rendering, file uploads, conversation rename/import. Renders `rag`/`adaptive` SSE frames as info bars above the answer; markdown links get `target="_blank"`. **Math:** `renderMarkdown` registers a KaTeX **marked extension** (`_ensureMathExtension`, lazy once `katex` is loaded; also on `window._ensureKatexMarked` for other modules) so formulas (`$…$`/`$$…$$`/`\(…\)`/`\[…\]`) are rendered *during* markdown parsing — marked never sees/mangles the LaTeX. (The earlier post-parse auto-render approach broke on `_`/`\`.) **Citation linkifier:** `linkifyCitations` walks text nodes (skipping `a`/`code`/`pre`) and turns recognized norms (DIN/EN/ISO/IEC/VDI/VDE/ASTM) and German law refs (`§/Art. … <ABK>`) into links — laws → deterministic `gesetze-im-internet.de/<abk>/__<n>.html`, norms → DIN-Media search. **Auto-compression:** when enabled in the profile (`auto_compress`, `compress_overflow_chars`, `compress_idle_min`), it calls the existing `/api/conversations/{id}/compress` on overflow (after a response) or on idle-timer, on the currently open conversation, with a notification toast
- `canvas.js` — HTML5 Canvas renderer for slides & spreadsheets; loads branding (deckblatt/kopfzeile/logo) from the profile via `/api/profile/asset/...` (`reloadBranding()`); slide colors follow the mode palette via `_pal()`; records edit-regions for the WYSIWYG editor. `render()` itself toggles `#slide-nav` visibility for presentations (so the illustrated-presentation path gets ‹/› navigation, not just chat); two-column image draw is contain-fit (no distortion). `moveSlide` gives toast feedback.
- `canvas_editor.js` — WYSIWYG slide editor: click slide text → in-place overlay input; swap image; per-slide toolbar (move/delete/regenerate text)
- `agents.js` — Agent CRUD (stored as JSON files in `data/agents/`)
- `research.js` — Aspect-based search with sources + DOCX export with header image
- `doc_generator.js` — Dokumentengenerator tab (`docgen`): generate documents (e.g. a funding application) with a **document agent** (any agent; those with category `Dokumentation` are listed first) optionally grounded in one or more RAG bases. Posts to `/api/chat` (`agent_id` + `rag_collections` + optional `science`), renders the streamed Markdown (with KaTeX/links), and exports via `/api/export/docx` or back into a RAG base (`RAG.ingestText`). Document agents are created in the normal 🤖 Agenten tab — no separate agent store. **Quellmaterial (source material):** the task can be grounded on (a) **uploaded external documents** (`/api/upload` → attached as `files[]` on the chat message, server extracts the text), (b) a **dossier** picked from the Planer-Recherche dossiers (`GET /api/dossiers` list + `GET /api/dossiers/load?id=` content, path-traversal-guarded), and (c) **pasted text** — all appended as „Quellmaterial" context to the brief. „Text übernehmen" (`_usePasted`) instead loads pasted text *directly* as the finished document. The chat toolbar's **→ Doku** button (`chatToDocGen` in `app.js`) hands the current conversation as compressed source material to `DocGen.loadFromChat()` and switches to this tab (e.g. to plan a meeting from a chat).
- `rag.js` — RAG tab ("Wissensdatenbanken"): create/delete knowledge bases via two **sliders** — "schnell ↔ gründlich" (maps to chunk size/overlap/top-k/char_limit presets in `rag.js`) and "kreativ ↔ korrekt" (`strictness`) — plus a cleanup toggle and a prominent "📚 Wissensdatenbank anlegen" button. Upload documents and move/copy a conversation into a base (`/api/rag/collections/{id}/from-conversation`). In chat, the `📚 RAG` toggle + multi-select inject retrieved passages; the RAG system instruction wording follows the strictest selected base's `strictness`; a `rag` SSE frame shows the used sources (rendered by `chat.js`). Each base's document list is **collapsible** (native `<details>`, open by default when ≤4 docs)
- `planner.js` — Network diagram / CPM, zoom/pan, CSV im/export; AI (derive project agent, generate full plan, suggest predecessors/successors, detail a task, **insert task between two** via `/api/plans/insert-between`); structured resources with cost/rollup + lead time, resource catalog (import/export, free/extend/strict). Structure tools: stable task IDs with cascade-rename + separate computed **execution-order column (#)**, link-consistency normalizer (symmetric, dedup), delete-with-rebridge, **🔁 replace** task (by existing → inherits links, or new), **✨ Mach schön** (normalize + sort by order + redraw), **📅 Bestellplan** (resource schedule: needed-on = task ES, order-by = ES − lead, against project start date, optional **workdays** mode skipping weekends). Warnings line (`#planner-warn`) flags **cycles** (Kahn-unreached tasks) and **resource conflicts** (same human/hardware in overlapping ES…EF windows; also shown in the Bestellplan modal). The task table ↔ network-diagram split is a **draggable splitter** (`#planner-splitter`, `_initSplitter`; width persisted in `localStorage`, double-click resets) — the existing `ResizeObserver` on the canvas redraws automatically.
- `matrix_research.js` — Research matrix with live `localStorage` save + CSV im/export
- `presentation_assistant.js` — Table-based presentation builder (slide-by-slide generation)
- `illustrated_presentation.js` — Illustrated presentation: folder picker, derive analysis persona, describe each image via a vision model → two-column slides
- `json_editor.js` — JSON-Editor: open file, live validation (line/column), format, download (for repairing broken JSON files). **Now a sub-view** of the merged **💻 Code** tab (no standalone tab); the IDE-panel has a sub-tab bar (`#code-subtabs`, switched in `app.js`) toggling `#code-view-ide` ↔ `#code-view-json`. IDs unchanged, so `JsonEditor.init()` still binds normally.
- `ide.js` — Code-IDE: editor + sandboxed-iframe Canvas preview, AI assistant (uses the profile **Programmieren** model via `Profile.modelFor('coding')`, fallback `ministral-3:3b`), auto-repair. Provides the `ai_framework_thomas_input()` / `ai_framework_thomas_run()` iframe framework so generated programs get interactive input fields and a responsive canvas. The editor ↔ preview columns have a **draggable splitter** (`#ide-splitter`, `_initSplitter`; left width via the `--ide-left-w` CSS var on `#ide-body`, persisted in `localStorage`, double-click resets).
- `logger.js` — Diagnostic logger UI (toggle, filter, download)
- `profile.js` / `projects.js` — User profile (incl. the three model roles, `Profile.modelFor`) and project management

### Backup & Restore

`GET /api/backup` builds a ZIP of **all** user data: `profile.json`, `projects.json`,
conversations (from SQLite), `plans/`, `agents/` (incl. `favorite`), `code/`,
**`profile_assets/`** (logo/cover/header), and **`rag/collections.json`** — a full
dump of the RAG knowledge bases incl. documents, chunks and float32 embeddings
(base64-encoded). `POST /api/restore` re-imports everything: profile/assets overwrite;
projects merge; plans skip by name; agents/code skip by existing id; **RAG collections
skip by existing id**; conversations are always added as new (re-restoring duplicates
them). DB helpers `rag_export()` / `rag_collection_exists()` / `rag_import_collection()`
live in `db.py`. `app.js` shows per-category counts in the restore toast and reloads
profile/branding, agents and RAG afterwards.

### Branding & Modes (no fixed corporate design)

Branding is per-user, uploaded in the profile (no images shipped in `bilder/`):
- `logo` → sidebar (`#sidebar-logo`) + can appear in slides/documents
- `cover` → presentation title-slide background (`Vorlagen-Deckblatt`)
- `header` → slide/document header banner (`Vorlagen-Kopfzeile`)

Endpoints: `POST/GET/DELETE /api/profile/asset/{kind}` (kind ∈ logo|cover|header), files under `data/profile_assets/`, auto-resized via Pillow (logo 512 PNG, cover/header 1920 JPG). `GET /api/profile/assets` reports which are set + recommended sizes. `canvas.js` loads them via `/api/profile/asset/...` and `export.py` reads them from `data/profile_assets/`; if absent, slides/documents are produced without the image.

**Colors follow the mode** via CSS variables: `:root` = Maschinenbau (blue), `html[data-mode="ki"]` (green), `[data-mode="soziales"]` (brown), `[data-mode="marketing"]` (red) in `app.css`. `profile.js` sets `document.documentElement.dataset.mode`. Canvas slides read the active palette through `_pal()` (computed from `--bg-input`/`--bg-hover`/`--accent`/`--text`). Exports still tag AI text with "▶ Von KI generiert".

### Agent System

Agents are JSON files in `data/agents/` with fields: `id`, `name`, `description`, `system_prompt`, `tools` (array), `model` (optional override), `icon` (emoji), `category`. The system prompt is prepended to the user message at request time.

**Adaptive agent:** the special `agent_id == "__adaptive__"` (a fixed entry in the sidebar agent selector) triggers `_derive_adaptive_prompt()` in `main.py` — a preliminary non-streaming LLM call that analyzes the latest user message and returns a question-specific `system_prompt` (JSON `{rolle, system_prompt}`), which is then used for the actual answer. Emits an `adaptive` SSE frame with the derived role (rendered above the answer by `chat.js`).

## Dependencies

Python: FastAPI, Uvicorn, httpx, ddgs, pypdf, python-docx, openpyxl, python-pptx, Pillow, python-multipart, aiofiles, aiosqlite, SymPy, NumPy, SciPy, Pint, matplotlib (see `requirements.txt` for pinned versions). Completeness verified by importing every dependency + project module and `pip check`.

No frontend build step — plain HTML/CSS/JS served directly by FastAPI's `StaticFiles`.

## Deployment Variants

Three installation variants exist (each has a `.bat` and `.ps1` script pair):
- `install` — Standard: Python 3.12 via winget + Ollama + venv
- `make_portable` — Self-contained bundle, no system dependencies. Uses its **own Ollama port `11500`** (rewrites the bundle's `config.json` `ollama_base`) so it never collides with a system Ollama on `11434`, and bundles only the whitelisted models. It sources the model blobs from **`$env:OLLAMA_MODELS` if set** (e.g. a `D:\OLLAMA_MODELS` override), otherwise `%USERPROFILE%\.ollama\models`.
- `make_server` — Multi-user server mode with `0.0.0.0` binding

The `make_*` scripts exclude `venv`, `__pycache__`, `.git`, `.claude`, `server.log` when copying. **Troubleshooting helpers** (repo root): `diagnose.bat` reports OS/Python/packages/ports/Ollama state into `diagnose_report.txt`; `test_chat.bat` + `test_chat.py` hit `/api/chat` directly to surface the raw answer/error. Both auto-detect the portable-bundle vs. dev layout. **Per-process VRAM guard caveat:** in `make_server` with `workers > 1`, the single-model guard does not coordinate across worker processes — on ~6 GB VRAM use `workers = 1` (documented in `docs/SERVER.md`).

`scripts/` (dev helpers, not required at runtime): `build_release.ps1` packages a clean `ai_framework_thomas.zip` to the Desktop (source tree minus venv/caches/`.claude`/runtime data, **plus** default agents, an empty data structure, and the 100-task demo plan); `make_demo_plan.py` / `verify_demo.py` regenerate/verify that demo plan (run with `PYTHONIOENCODING=utf-8`). `samples/` holds an importable example resource catalog CSV.
