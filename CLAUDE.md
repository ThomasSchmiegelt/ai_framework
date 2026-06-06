# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Constraints

**Cross-platform:** The app must run on both **Windows** and **Linux**. The Python/JS core is already platform-neutral. Keep it that way — no OS-specific paths, no platform-only dependencies. Platform-specific behavior (e.g. systemd on Linux) must always be guarded with a runtime check (`sys.platform`). Both `.bat`/`.ps1` (Windows) and `.sh` (Linux) scripts coexist in the repo; neither set should be removed.

**Open source / MIT:** This project is MIT-licensed. All added code and dependencies must be compatible with the MIT license. Do not introduce AGPL, GPL, or other copyleft dependencies.

## What This Project Is

AI_Framework_Thomas is a German-language, **general-purpose** AI chat interface that runs entirely locally. It wraps local Ollama LLMs in a FastAPI backend with a vanilla JS frontend, adding a tool-calling agentic loop, SQLite conversation persistence, and productivity tools (engineering calculators, material lookup, unit conversion, SymPy solver, matplotlib charting, PDF/DOCX/PPTX generation, planner/CPM, research).

It is the general variant derived from IG-11. **Seven modes** (`maschinenbau`, `ki`, `soziales`, `marketing`, `finanz` gray, `geschaeftsfuehrung` yellow, plus a user-configurable `custom` mode in violet) are chosen in the user profile and drive (a) the UI/slide color scheme via CSS variables (`html[data-mode=…]` in `app.css`), and (b) — when `mode_prompt` is on — a domain framing prepended to system prompts (`_mode_prefix()`, applied keyword-gated per question via `_MODE_KEYWORDS`). The **`custom`** mode is fully user-defined: its name, domain framing and (optional) keywords come from the profile fields `custom_mode_name` / `custom_mode_prompt` / `custom_mode_keywords` (read by `_mode_prompt_text()` / `_mode_keywords()`; with no keywords the framing applies to every question). The full automatic system-prompt vorspann is assembled by `_augment_prefix()` in `main.py`: base anti-hallucination guard + mode framing + persona/profile (`tone`) + a LaTeX-formula rule + a citation rule (norms/laws should be named so the frontend can linkify them). The profile flag **`pure_llm`** ("keine Modi / LLM pur") makes `_augment_prefix()` drop the guard/mode/persona/formula/citation prepend — only the language rule remains (an explicitly chosen agent and active RAG bases still apply). The profile field **`lang`** (`de`/`en`, default `de`) drives both the UI language (frontend `static/js/i18n.js` — a DE→EN dictionary that translates the static HTML shell on the fly, German is the source; deep JS-generated strings are still German) and the answer language: when `lang=en`, `_lang_rule()` appends `_LANG_RULE_EN` (an "answer in English" instruction) to the system-prompt vorspann, applied even under `pure_llm`. The toggle lives in the profile modal (`#profile-lang`, wired in `profile.js` → `I18n.setLang`). Branding (logo, template cover, template header) is uploaded by the user in the profile (`/api/profile/asset/{logo|cover|header}`, stored under `data/profile_assets/`, auto-resized via Pillow). `bilder/` contains default fallback assets shipped with the repo (`default_logo.png`, `default_cover.jpg`, `default_header.jpg`, `icon.png`/`icon.ico`) — served via `GET /api/assets/{name}`.

## Running the App

**Linux (this machine):**
```bash
source venv/bin/activate
uvicorn main:app --host 127.0.0.1 --port 8780 --reload   # dev
uvicorn main:app --host 0.0.0.0  --port 8780 --reload   # server mode
```

**Windows:**
```powershell
.\venv\Scripts\Activate.ps1
uvicorn main:app --host 127.0.0.1 --port 8780 --reload
```
Or use the provided scripts: `start.bat` (single-user) / `start_server.bat` (multi-user).

Ollama must be running separately on `http://localhost:11434` with at least one model from `config.json` pulled. Browser: <http://localhost:8780>.

## Key Configuration

`config.json` controls allowed models, default model, **`embed_model`** (RAG embeddings, default `nomic-embed-text`), Ollama URL, and server port. No environment variables are used — everything is read from this file at startup. For RAG, pull the embedding model once: `ollama pull nomic-embed-text`.

## Architecture

```
Frontend (Vanilla JS, 15 tabs) static/js/app.js, chat.js, canvas.js, ide.js, planner.js, medizin.js, mathe.js, dir_analysis.js, morph_box.js, jury.js, …
       ↓ SSE streaming
Backend (FastAPI)              main.py  (~5800 lines)
       ↓ tools/llm.py (unified)
Ollama (local)  ──or──  external OpenAI-compatible API (OpenRouter, OpenAI, Groq …)
       ↓
SQLite (aiosqlite)             data/ai_framework_thomas.db  — schema in db.py
```

The backend is async throughout (httpx, aiofiles, aiosqlite). **All LLM calls go
through `tools/llm.py`** (`_llm.chat` / `_llm.stream`), which transparently routes to
local Ollama or an external OpenAI-compatible provider depending on the model name.

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

### Medizin tab — 2-model consultation pipeline

The 🩺 Medizin tab (`static/js/medizin.js`) has an **expert pipeline** toggle
(`🔬 Experten-Pipeline`, default on) that orchestrates two models with a
human-in-the-loop loop, plus a plain single-model chat fallback (`_sendSimple`).
The pipeline endpoint `POST /api/medizin/consult` streams SSE and runs, **sequentially
under the VRAM lock** (each stage in its own `_model_session` block so model switches
serialize — never nested):
1. **refine** — Ministral (`_model_for("general")`) turns the dialogue into a structured medical case
2. **analyze** — MedGemma (`_model_for("medical")`) checks for missing info (RAG patient file injected here)
3a. if incomplete and `round < _MED_MAX_ROUNDS` (=2): Ministral **formulates a follow-up question** → `question` frame, waits for the user's answer
3b. if complete or rounds exhausted: MedGemma streams the **final assessment** (`think:False` — MedGemma returns empty content with Ollama think mode)

SSE frames: `stage` (`{stage:refine|analyze|formulate|final, status:start|done, label?, content?}` → collapsible blocks in the UI), `question` (`{content, round}` → shown as assistant bubble, frontend tracks `round`), `text`, `done` (`{needs_followup, round}`), `error`. `POST /api/medizin/translate` streams a lay-language translation of an assessment via the general model (the `🗣 In einfaches Deutsch übersetzen` button). Patient files are RAG collections whose name starts with `Patient:` (filtered in `medizin.js`). The Medizin model selector is **restricted to MedGemma** (`_isMedModel` → `/^medgemma:/i`, e.g. `medgemma:4b`/`medgemma:27b`); no general chat models are offered there (an empty list shows an `ollama pull medgemma:4b` hint). Disclaimer text in `_MED_DISCLAIMER`.

### Mathe tab — tutor mode with deterministic SymPy grounding

The 🔢 Mathe tab (`static/js/mathe.js`) routes the normal solver to the `mathe_experte`
agent. A **`🎓 Tutor-Modus`** toggle instead routes to the `mathe_tutor` agent (adaptive
Socratic: guides step-by-step, doesn't dump the answer; `💡 Lösung zeigen` is the escape
hatch). Because small local models do **not** reliably self-invoke verification tools
mid-dialogue (tested: ministral-3:3b/qwen2.5-coder both skip them and may confirm wrong
steps), tutor mode first calls `POST /api/mathe/ground`: the backend extracts the task as a
SymPy expression (one LLM call), computes the **ground truth deterministically with SymPy**
(`_mathe_sympy_facts` — handles `^`→`**`, `==`→`=`, `f(x)=` prefix, implicit multiplication
via `parse_expr` transformations, and direct calls like `diff(...)`), and returns verified
facts that `mathe.js` injects into the chat request so the tutor judges against truth instead
of guessing. No grounding for pure theory/word problems → graceful fallback to plain Socratic.
**Function plotting is deterministic, not model-driven:** `plot_function` is **not** offered
to the model (small models emit invalid LaTeX escapes `\(…\)` in tool args → Ollama HTTP 500;
filtered out of `active_tools` in `_chat_generator`). Instead `_chat_generator` detects a plot
request in the user text (`_extract_plot_request` → expression + range, handles `f(x)=`/`y=`,
`^`, implicit mult., multiple terms via „und"/`;`) and renders the graph **server-side** via
`plot_function` as a post-answer fallback — and again in the Ollama-error `except` branch, so a
plot still appears even if the LLM call fails. This covers **both** the Chat and Mathe tabs
(same `/api/chat`). `plot_chart` (explicit data series) stays model-callable. The `image` SSE
frame carries the full `data:` URI (don't double the prefix). The Mathe tab shares the
`model_coding` role with Code (no own model selector), LaTeX output is always on, and the
Plot toggle + Tutor button sit next to the chat input row.

### Verzeichnis-Analyse & Morphologischer Kasten — backend

Two optional tabs (frontend modules `dir_analysis.js` / `morph_box.js`, see Frontend
Modules). All endpoints are registered before the static mount and pass user model
names through `_pick_model(body.get("model"), _model_for("general"))`; every Ollama
call is wrapped in `async with _model_session(model), httpx…`.

**Verzeichnis-Analyse** (`/api/dir/scan`, `/api/dir/analyze-file`, `/api/dir/finalize`):
reads a **server-side path** (`_dir_resolve_base` validates `is_dir`). `_dir_walk` is a
bounded recursive walk (`_DIR_MAX_FILES`/`_DIR_MAX_DEPTH`, skips hidden + `_DIR_SKIP_DIRS`
like `.git`/`node_modules`/`venv`, catches `PermissionError`). Snippets/full text come from
the existing `_extract_text`. **PII is anonymized before anything reaches the LLM or the
client** via `tools/anonymize.py` (`_anonymize()` wraps `redact_pii` + an optional LLM-NER
pass `_llm_ner_names`); a per-request `mapping` keeps placeholders consistent. `analyze-file`
guards traversal with `(base / file_rel).resolve().relative_to(base)` (the `/api/dossiers/load`
pattern). `finalize` writes `_KI_INDEX.md` (UTF-8, tagged „▶ Von KI generiert") back into the
folder and optionally creates a `Verzeichnis: <name>` RAG base (`rag_create_collection` +
`ingest_file`). The scan/overview/per-file analyses all use `format:"json"` / Markdown with the
standard `<think>`-strip + regex-extract + fallback. **Server-mode caveat:** arbitrary paths are
read AND written — keep this tab hidden in multi-user/server deployments (it is optional + hidden
by default).

**Morphologischer Kasten** (`/api/morph/generate`, `/api/morph/evaluate`, `/api/morph/refine-cell`):
all use the shared `_morph_llm(model, system, user)` helper (`format:"json"`, `_parse_llm_json`
strips `<think>`/fences + extracts the first JSON object). `generate` returns parameters with short
string values — `_morph_value_str` flattens the nested objects small models sometimes emit into a
readable string. `evaluate` scores a chosen combination (score/Machbarkeit/Innovation 0–100 +
Begründung/Risiken) and proposes alternative combinations. `refine-cell` expands or critiques a
single value. Export is frontend-only (DOCX/DocGen/RAG) — no dedicated endpoint.

### LLM abstraction & external API providers (`tools/llm.py`)

All LLM calls go through **`tools/llm.py`** (imported as `_llm` in `main.py`), so the
app can use **local Ollama or an external OpenAI-compatible provider** (OpenRouter,
OpenAI, Groq, Together …) interchangeably. **The return shape is always Ollama-shaped**
(`{"message": {"content", "tool_calls", "thinking"}}` / stream chunks
`{"message": {"content"}, "done"}`), so the ~30 call sites stay almost unchanged:
`resp = await client.post(.../api/chat, json=PAYLOAD)` → `resp = await _llm.chat(client, PAYLOAD)`
and `client.stream(...)` → `async for chunk in _llm.stream(client, PAYLOAD)`. `_llm.chat`
returns an `LLMResponse(dict)` that also exposes `.json()` / `.raise_for_status()` /
`.status_code`, so downstream parsing (incl. the agentic loop's `think`-400 retry) works
unchanged.

- **Routing:** a model name with the prefix `"<provider_id>::<model>"` (local Ollama
  names never contain `::`) is remote; `_llm.resolve()` looks up the provider in
  `data/api_providers.json`. Remote requests translate Ollama→OpenAI: `options.temperature`
  →`temperature`, `format:"json"`→`response_format:{type:"json_object"}`, `think` dropped,
  images→`image_url` content parts; the OpenAI response/stream is translated back to the
  Ollama shape (tool-call `arguments` JSON-string → dict).
- **Providers:** stored in `data/api_providers.json` (**contains API keys → gitignored,
  excluded from `/api/backup` and the `make_*` bundles**; mirrors `data/mail.json`). CRUD:
  `GET/POST/DELETE /api/providers` + `POST /api/providers/test` (fetches `{base}/models`).
  `_provider_public()` strips the key for the frontend. `/api/models` merges local Ollama
  tags with the providers' models (prefixed, `remote:true`); the profile role selects list
  both (`profile.js` `_fillModelSelects`, labelled `☁ model (Provider)`).
- **Config wiring:** `_llm.set_config(OLLAMA_BASE, API_PROVIDERS_FILE)` is called once at
  startup. Provider management lives in the **profile modal** („☁ KI-Anbieter (API)").

### VRAM guard — only ONE model resident at a time

Target hardware has limited VRAM (~6 GB), so only one **local** model may be resident at a
time. By default only `ministral-3:3b` is installed/loaded; any other model is
pulled on demand and assigned per role in the profile (see **Model roles** below).
`main.py` defines `_model_lock` (asyncio.Lock), `_loaded_model`,
and the `_model_session(model)` async context manager: on model switch it unloads
the previous model (`Ollama keep_alive=0`) before the new one loads, and the lock
serializes all generations so concurrent requests can't load two models at once.
**Every LLM call site must be wrapped in `async with _model_session(model), httpx…:`**
(using `_llm.chat`/`_llm.stream` inside). For **remote** models `_model_session` is a
**no-op** (they use no local VRAM, so no lock/unload — a remote call also never blocks a
local generation).

### Model roles (profile)

There is no hardcoded model beyond `DEFAULT_MODEL` (`ministral-3:3b`). The profile
holds **four** optional role assignments — `model_general`, `model_coding`,
`model_science`, `model_medical` — surfaced in the profile modal as **Allgemein /
Programmieren / Mathe / Wissenschaftlich / Medizin** (`model_coding` drives both the
Code-IDE and the Mathe tab; selects populated from all installed
Ollama models via `/api/models`, which no longer filters by `allowed_models`).
**Models are chosen ONLY in the profile** — there are no per-tab model dropdowns
anymore. The former selectors in the **sidebar** (`#model-select`), **Planer**
(`#planner-model-select`), **Medizin** (`#medizin-model-select`) and **Matrix**
(`#matrix-model-select`) were removed; every consumer reads `Profile.modelFor(role)`
instead (Chat/DocGen/Presentation → `general`, Research/Matrix → `science`,
Medizin → `medical`, Code/Mathe → `coding`). The first-run onboarding still offers
one model picker (`#ob-model`) which writes `model_general` into the new profile.
`_MODEL_ROLES` maps role→profile key; `_model_for(role)` in `main.py` returns the
assigned model or `DEFAULT_MODEL`. Wiring:
- **Allgemein** → Chat (`chat.js` → `Profile.modelFor('general')`), DocGen, presentation
  builders, Planer (`planner.js` `_model()`) and the refine loop.
- **Programmieren / Mathe** → the Code-IDE assistant (`ide.js` uses `Profile.modelFor('coding')`),
  the **Mathe tab** (`mathe.js` `_model()` → `Profile.modelFor('coding')`; the two tabs share one
  model, neither has its own selector), and in `_chat_generator` any `code_ide`-capable agent
  (e.g. the `coder` agent, whose own `model` is now `null`).
- **Wissenschaftlich** → `/api/research` (`_pick_model(request.model, _model_for("science"))`)
  and the science path in `_chat_generator` (when no specific non-general model was chosen);
  the Matrix research grid (`matrix_research.js` `_getModel()` → `science`).
- **Medizin** → the 🩺 Medizin tab; the consultation pipeline uses it as the MedGemma role
  (`_model_for("medical")`); `medizin.js` resolves `Profile.modelFor('medical')` directly.
  Recommended: `alibayram/medgemma:latest` (a MedGemma-4B port; `ollama pull alibayram/medgemma`).
`Profile.modelFor(role)` (`profile.js`) exposes the same resolution to the frontend
(fallback `ministral-3:3b`; no longer reads any selector).
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
| `engineering.py` | Unit conversion (Pint), equation solver (SymPy), charting from value series (`plot_chart`), **function-graph from a term** (`plot_function`: SymPy lambdify, `^`/implicit mult./`f(x)=` prefix, multiple functions with `;`), bolt calculator (VDI 2230). Both stream an `image` frame. **Note:** `plot_function` is called **server-side only** (deterministic fallback in `_chat_generator`, see the Mathe tab section) — it is *not* exposed to the model as an Ollama tool (small models corrupt the tool-call JSON → 500). `plot_chart` remains model-callable. `_PLOT_RULE` now tells the model the app plots automatically (no tool call needed). |
| `materials.py` | ~40-material properties database (steels, aluminium, titanium, stainless, plastics) |
| `report.py` | PDF/DOCX report generation with LaTeX equation support |
| `routing.py` | Route planning for the `route_planner` chat tool: geocoding (Nominatim) + routing (OSRM); returns a `map` frame rendered as an interactive Leaflet map in `chat.js` (needs internet) |
| `imaging.py` | Image helpers for the illustrated-presentation feature: descriptive-filename heuristic + Pillow downscale |
| `anonymize.py` | **PII redaction for the Verzeichnis-Analyse tab** (stdlib regex only, no new dependency). `redact_pii(text, mapping)` replaces e-mails, phone numbers, IBAN, URLs and — heuristically (Anrede/title + name tokens) — person names with stable placeholders (`[EMAIL_1]`, `[TEL_1]`, `[PERSON_1]`…), keeping a **consistent mapping per scan session**. `redact_names(text, found_names, mapping)` applies a concrete name list (e.g. from an optional LLM-NER pass). Redacts **contents**, not file/folder names; the mapping stays local and is **not** written into the index file. |
| `llm.py` | **Unified LLM access (local Ollama ⇄ external OpenAI-compatible API).** `chat(client, payload)` / `stream(client, payload)` take an Ollama-style payload and return an Ollama-shaped `LLMResponse`/chunk dicts; `is_remote`/`resolve` route by the `"<provider_id>::<model>"` prefix; `list_remote_models`/`fetch_provider_models` for `/api/models` and provider testing. Translates request/response/stream both ways. Only `httpx`. See the **LLM abstraction** section. |
| `mail.py` | **🚧 in development.** Read-only IMAP/POP3 mailbox access (stdlib only: `imaplib`/`poplib`/`email`). `domain_of()` parses the sender domain; `clean_mail_text()` strips quoted history/signature/disclaimer before RAG ingest. Drives the Mail tab (filter by sender/subject/domain → up to 4 actions per mail: RAG/agent-task/→docgen/note; reusable rules in `data/mail_rules.json`; endpoints `/api/mail/{config,list,message,to-rag,rules,action/rag,action/agent}`). **Sending is always manual** (clipboard/mailto) — no SMTP/auto-send. |
| `rag.py` | RAG engine: document cleanup (`clean_text`), char-based overlapping `chunk_text`, Ollama embeddings (`embed`, forced to CPU via `num_gpu=0` on small-VRAM tiers so they don't evict the chat model), and NumPy brute-force cosine search (`query_collections`). VRAM tiers (`none`/`4gb`/`6gb`/`12gb`) preset chunk size, overlap, top-k, embed device **and `char_limit`** (max chars of injected context — enforced per query in `query_collections`, using the largest limit among the selected collections). Per-base `strictness` (`kreativ`/`ausgewogen`/`korrekt`) selects the RAG injection wording (strictest among selected bases wins). Embeddings stored as float32 BLOBs in SQLite (`rag_collections`/`rag_documents`/`rag_chunks` in `db.py`; `char_limit` and `strictness` columns added with migrations). Ingest sources: a document upload, a conversation (`POST /api/rag/collections/{id}/from-conversation`, optional `delete_after` = move), or arbitrary text (`POST /api/rag/collections/{id}/from-text` — used by the "📚 In Wissensdatenbank" buttons in research/matrix). `rag.js` exposes reusable `pickCollection()`/`ingestText()` (collection-picker modal). Embedding model from `config.json` `embed_model` (default `nomic-embed-text`, **must be pulled in Ollama**). |

Calculations run inside `_safe_exec()` in `main.py` — a restricted `exec()` sandbox with no file I/O or network, only whitelisted math/numpy/scipy/sympy.

### Database (`db.py`)

- `conversations` table: id, title, model, agent_id, canvas_json, timestamps
- `messages` table: rowid, conv_id, seq, role, content, images_json
- `messages_fts` FTS5 virtual table for full-text search with auto-maintenance triggers
- On startup, legacy JSON files in `data/conversations/` are migrated into SQLite automatically

### Frontend Modules (15 tabs: Chat, Canvas, Agenten, Recherche, RAG, Dokumente, Medizin, Mathe, Mail [🚧 in development], Planer, Matrix, Code, Verzeichnis-Analyse, Morph-Kasten, Logs)

**Optional tabs / first-run defaults:** RAG, Code (`ide`), Mathe, Medizin, Mail, Logs, **Verzeichnis-Analyse (`diranalyse`)** and **Morphologischer Kasten (`morph`)** are *optional* and toggled in the profile (`#profile-tab-vis`, applied by `Profile.applyTabVisibility` → hides the `.tab-btn`; `switchTab` also guards). **On first run (no `user_profile.json`) all eight are hidden** — `GET /api/profile` and the `PUT` handler default `hidden_tabs` to `_DEFAULT_HIDDEN_TABS` when the field is absent (onboarding doesn't send it); the profile modal always sends an explicit `hidden_tabs`. In the profile each optional tab is its own checkbox (Code and Mathe are **separate**, `data-tabs="ide"` / `data-tabs="mathe"`); `profile.js` expands `data-tabs` (comma-split, deduped — a single box may still carry several tabs) on save and checks a box only if all its tabs are visible.

- `app.js` — Global state, model loading, tab switching, backup/restore, module init
- `chat.js` — SSE streaming consumer, message rendering, file uploads, conversation rename/import. Renders `rag`/`adaptive` SSE frames as info bars above the answer; markdown links get `target="_blank"`. **Math:** `renderMarkdown` registers a KaTeX **marked extension** (`_ensureMathExtension`, lazy once `katex` is loaded; also on `window._ensureKatexMarked` for other modules) so formulas (`$…$`/`$$…$$`/`\(…\)`/`\[…\]`) are rendered *during* markdown parsing — marked never sees/mangles the LaTeX. (The earlier post-parse auto-render approach broke on `_`/`\`.) **Citation linkifier:** `linkifyCitations` walks text nodes (skipping `a`/`code`/`pre`) and turns recognized norms (DIN/EN/ISO/IEC/VDI/VDE/ASTM) and German law refs (`§/Art. … <ABK>`) into links — laws → deterministic `gesetze-im-internet.de/<abk>/__<n>.html`, norms → DIN-Media search. **Auto-compression:** when enabled in the profile (`auto_compress`, `compress_overflow_chars`, `compress_idle_min`), it calls the existing `/api/conversations/{id}/compress` on overflow (after a response) or on idle-timer, on the currently open conversation, with a notification toast
- `canvas.js` — HTML5 Canvas renderer for slides & spreadsheets; loads branding (deckblatt/kopfzeile/logo) from the profile via `/api/profile/asset/...` (`reloadBranding()`); slide colors follow the mode palette via `_pal()`; records edit-regions for the WYSIWYG editor. `render()` itself toggles `#slide-nav` visibility for presentations (so the illustrated-presentation path gets ‹/› navigation, not just chat); two-column image draw is contain-fit (no distortion). `moveSlide` gives toast feedback.
- `canvas_editor.js` — WYSIWYG slide editor: click slide text → in-place overlay input; swap image; per-slide toolbar (move/delete/regenerate text)
- `agents.js` — Agent CRUD (stored as JSON files in `data/agents/`)
- `research.js` — Aspect-based search with sources + DOCX export with header image
- `doc_generator.js` — Dokumentengenerator tab (`docgen`): generate documents (e.g. a funding application) with a **document agent** (any agent; those with category `Dokumentation` are listed first) optionally grounded in one or more RAG bases. Posts to `/api/chat` (`agent_id` + `rag_collections` + optional `science`), renders the streamed Markdown (with KaTeX/links), and exports via `/api/export/docx` or back into a RAG base (`RAG.ingestText`). Document agents are created in the normal 🤖 Agenten tab — no separate agent store. **Quellmaterial (source material):** the task can be grounded on (a) **uploaded external documents** (`/api/upload` → attached as `files[]` on the chat message, server extracts the text), (b) a **dossier** picked from the Planer-Recherche dossiers (`GET /api/dossiers` list + `GET /api/dossiers/load?id=` content, path-traversal-guarded), and (c) **pasted text** — all appended as „Quellmaterial" context to the brief. „Text übernehmen" (`_usePasted`) instead loads pasted text *directly* as the finished document. The chat toolbar's **→ Doku** button (`chatToDocGen` in `app.js`) hands the current conversation as compressed source material to `DocGen.loadFromChat()` and switches to this tab (e.g. to plan a meeting from a chat). `DocGen.showResult(text)` renders an externally produced document into the output pane and reveals the export buttons (used by the refine loop below).
- `refine.js` — **Verfeinerungsschleife** (multi-agent), visually integrated at the bottom of the Dokumente tab (`#refine-section`, CSS-classed to match the generator, no white textarea). Its input is a **source toggle** (no own text field): „Erzeugtes Dokument" (`DocGen.getText()`) or „Bestehender Text" (`#docgen-paste`). The agent dropdowns list **favorites only** (like the document-agent selector). Iterates a document through the selected agents via `POST /api/refine-document` until the change rate falls below the threshold; the result is pushed back into the generated-document pane via `DocGen.showResult()` (export handled there). Model = `Profile.modelFor('general')`.
- `rag.js` — RAG tab ("Wissensdatenbanken"): create/delete knowledge bases via two **sliders** — "schnell ↔ gründlich" (maps to chunk size/overlap/top-k/char_limit presets in `rag.js`) and "kreativ ↔ korrekt" (`strictness`) — plus a cleanup toggle and a prominent "📚 Wissensdatenbank anlegen" button. Upload documents and move/copy a conversation into a base (`/api/rag/collections/{id}/from-conversation`). In chat, the `📚 RAG` toggle + multi-select inject retrieved passages; the RAG system instruction wording follows the strictest selected base's `strictness`; a `rag` SSE frame shows the used sources (rendered by `chat.js`). Each base's document list is **collapsible** (native `<details>`, open by default when ≤4 docs)
- `planner.js` — Network diagram / CPM, zoom/pan, CSV im/export; AI (derive project agent, generate full plan, suggest predecessors/successors, detail a task, **insert task between two** via `/api/plans/insert-between`); structured resources with cost/rollup + lead time, resource catalog (import/export, free/extend/strict). Structure tools: stable task IDs with cascade-rename + separate computed **execution-order column (#)**, link-consistency normalizer (symmetric, dedup), delete-with-rebridge, **🔁 replace** task (by existing → inherits links, or new), **✨ Mach schön** (normalize + sort by order + redraw), **📅 Bestellplan** (resource schedule: needed-on = task ES, order-by = ES − lead, against project start date, optional **workdays** mode skipping weekends). Warnings line (`#planner-warn`) flags **cycles** (Kahn-unreached tasks) and **resource conflicts** (same human/hardware in overlapping ES…EF windows; also shown in the Bestellplan modal). The task table ↔ network-diagram split is a **draggable splitter** (`#planner-splitter`, `_initSplitter`; width persisted in `localStorage`, double-click resets) — the existing `ResizeObserver` on the canvas redraws automatically.
- `matrix_research.js` — Research matrix with live `localStorage` save + CSV im/export
- `presentation_assistant.js` — Table-based presentation builder (slide-by-slide generation)
- `illustrated_presentation.js` — Illustrated presentation: folder picker, derive analysis persona, describe each image via a vision model → two-column slides
- `json_editor.js` — JSON-Editor: open file, live validation (line/column), format, download (for repairing broken JSON files). **Now a sub-view** of the merged **💻 Code** tab (no standalone tab); the IDE-panel has a sub-tab bar (`#code-subtabs`, switched in `app.js`) toggling `#code-view-ide` ↔ `#code-view-json`. IDs unchanged, so `JsonEditor.init()` still binds normally.
- `ide.js` — Code-IDE: editor + sandboxed-iframe Canvas preview, AI assistant (uses the profile **Programmieren / Mathe** model via `Profile.modelFor('coding')` — shared with the Mathe tab, fallback `ministral-3:3b`), auto-repair. Provides the `ai_framework_thomas_input()` / `ai_framework_thomas_run()` iframe framework so generated programs get interactive input fields and a responsive canvas. The editor ↔ preview columns have a **draggable splitter** (`#ide-splitter`, `_initSplitter`; left width via the `--ide-left-w` CSS var on `#ide-body`, persisted in `localStorage`, double-click resets).
- `medizin.js` — 🩺 Medizin tab: 2-model consultation **pipeline** (`_sendPipeline` → `/api/medizin/consult`) with collapsible stage blocks (`refine/analyze/formulate/final`), follow-up `question` handling (`_round`, max 2), `🗣 In einfaches Deutsch übersetzen` (`/api/medizin/translate`), plus a plain single-model fallback (`_sendSimple`) toggled by `🔬 Experten-Pipeline`. Patient files = RAG collections named `Patient:…` (create/upload inline in the topbar).
- `mathe.js` — 🔢 Mathe tab: solver via the `mathe_experte` agent, or `🎓 Tutor-Modus` via the `mathe_tutor` agent with **deterministic SymPy grounding** (`/api/mathe/ground` facts injected into the chat request) + `💡 Lösung zeigen`. Model = `Profile.modelFor('coding')` (shared with Code, no own selector); LaTeX always on; the Plot toggle + Tutor/Lösung buttons sit in a toolbar at the chat input row. Renders `image` plot frames inline (full `data:` URI as-is), offers LaTeX/PDF export when the answer contains `$`.
- `dir_analysis.js` — 📁 Verzeichnis-Analyse tab: enter a **server-side folder path** → `POST /api/dir/scan` (structure tree + KI overview + flagged „interesting" files), click a file → `POST /api/dir/analyze-file` (deep Markdown analysis), then „📥 Index speichern" / „📚 In Wissensdatenbank" → `POST /api/dir/finalize` (writes `_KI_INDEX.md` back into the folder, optionally a `Verzeichnis: <name>` RAG base). **Personal data in file contents is ALWAYS anonymized — mandatory, not switchable** (the backend forces `anonymize=True` and ignores any client flag); an optional `+ KI-Namenssuche` checkbox adds a slower LLM-NER pass that only finds *additional* names. **File analyses run through a client-side serial queue** (`_queue`/`_processQueue`, one at a time) — the VRAM lock serializes them server-side anyway, and firing them in parallel made idle connections abort as „Failed to fetch". Failed analyses show a `↻ Erneut` retry button. Model = `Profile.modelFor('general')`; last path in `localStorage`. Optional tab, hidden by default — do not enable in multi-user/server mode (arbitrary paths are read/written).
- `morph_box.js` — 🧩 Morphologischer Kasten (Zwicky box) tab: ideation grid of parameters (rows) × values (chips). KI: `🤖 Parameter generieren` (`/api/morph/generate`), `📊 Kombination bewerten` (`/api/morph/evaluate` → score/Machbarkeit/Innovation bars + suggested combinations to apply), per-chip `✨ ausformulieren` / `💬 Alternativen` (`/api/morph/refine-cell`). A selection = one chip per parameter (click to (de)select, double-click to edit). State (`_problem`/`_params`/`_selection`) persists in `localStorage`; CSV im/export; export the chosen solution via DOCX (`/api/export/docx`), → Doku (`DocGen.showResult`) or Wissensdatenbank (`RAG.ingestText`). Model = `Profile.modelFor('general')`.
- `onboarding.js` — First-run wizard: shown when `user_profile.json` is absent (or profile flag set). Slide 1 is an interactive form (name, custom mode definition); slides 2–14 are info screens with screenshots (incl. Medizin and Mathe). On finish, saves the profile and launches in custom mode.
- `logger.js` — Diagnostic logger UI (toggle, filter, download)
- `profile.js` / `projects.js` — User profile (incl. the four model roles `Profile.modelFor`, and tab visibility via `data-tabs` checkboxes) and project management

### Backup & Restore

`GET /api/backup` builds a ZIP of **all** user data: `profile.json`, `projects.json`,
conversations (from SQLite), `plans/`, `agents/` (incl. `favorite`), `juries/`, `code/`,
**`profile_assets/`** (logo/cover/header), and **`rag/collections.json`** — a full
dump of the RAG knowledge bases incl. documents, chunks and float32 embeddings
(base64-encoded). **`api_providers.json` is deliberately excluded** (contains API keys).
`POST /api/restore` re-imports everything: profile/assets overwrite;
projects merge; plans skip by name; agents/juries/code skip by existing id; **RAG collections
skip by existing id**; conversations are always added as new (re-restoring duplicates
them). DB helpers `rag_export()` / `rag_collection_exists()` / `rag_import_collection()`
live in `db.py`. `app.js` shows per-category counts in the restore toast and reloads
profile/branding, agents and RAG afterwards.

### Branding & Modes (no fixed corporate design)

Branding is per-user, uploaded in the profile (defaults in `bilder/` are used only as repo-shipped fallbacks):
- `logo` → sidebar (`#sidebar-logo`) + can appear in slides/documents
- `cover` → presentation title-slide background (`Vorlagen-Deckblatt`)
- `header` → slide/document header banner (`Vorlagen-Kopfzeile`)

Endpoints: `POST/GET/DELETE /api/profile/asset/{kind}` (kind ∈ logo|cover|header), files under `data/profile_assets/`, auto-resized via Pillow (logo 512 PNG, cover/header 1920 JPG). `GET /api/profile/assets` reports which are set + recommended sizes. `canvas.js` loads them via `/api/profile/asset/...` and `export.py` reads them from `data/profile_assets/`; if absent, slides/documents are produced without the image.

**Colors follow the mode** via CSS variables: `:root` = Maschinenbau (blue), `html[data-mode="ki"]` (green), `[data-mode="soziales"]` (brown), `[data-mode="marketing"]` (red) in `app.css`. `profile.js` sets `document.documentElement.dataset.mode`. Canvas slides read the active palette through `_pal()` (computed from `--bg-input`/`--bg-hover`/`--accent`/`--text`). Exports still tag AI text with "▶ Von KI generiert".

### Agent System

Agents are JSON files in `data/agents/` with fields: `id`, `name`, `description`, `system_prompt`, `tools` (array), `model` (optional override), `icon` (emoji), `category`, `favorite`, **`rag_collections`** (optional list of knowledge-base ids bound to the agent — auto-activated in `_chat_generator` and merged into the request's RAG selection). The system prompt is prepended to the user message at request time. Domain agents shipped include `latex_experte`, `mathe_experte`, `mathe_tutor` (tutor mode), `medizin_assistent`, alongside the engineering/research/presentation defaults.

**Gesetz-/Regel-Agent aus Datei:** the 🤖 Agenten tab has a **⚖️ Gesetz-/Regel-Agent** button (`agents.js` `createLegalAgent` → `POST /api/agents/from-legal`, multipart `file`+`title`). The backend extracts the text (`_extract_text`), converts it to Markdown **deterministically** (`_legal_to_md` — regex turns `§ …`/`Art. …` line-starts into `###` headings, no LLM), then **decides by length** (`_LEGAL_PROMPT_LIMIT`, 8000 chars): short → the Markdown goes straight into the agent's `system_prompt`; long → it is embedded into a dedicated RAG base named `Gesetz: <title>` (`strictness:"korrekt"`) and bound via the agent's `rag_collections`. The agent is created with `icon:"⚖️"`, `category:"Recht"`, `favorite:true`. Returns `{agent_id, name, mode:"prompt"|"rag", chars, rag_collection_id}`.

**Adaptive agent:** the special `agent_id == "__adaptive__"` (a fixed entry in the sidebar agent selector) triggers `_derive_adaptive_prompt()` in `main.py` — a preliminary non-streaming LLM call that analyzes the latest user message and returns a question-specific `system_prompt` (JSON `{rolle, system_prompt}`), which is then used for the actual answer. Emits an `adaptive` SSE frame with the derived role (rendered above the answer by `chat.js`).

### Jury — multi-agent evaluation of a text

A **jury** bundles several agents (typically ⚖️ legal agents) into a reusable panel that
evaluates any text — including AI-generated content (a document, a system prompt, a
planner-derived project agent). Stored as JSON files in `data/juries/`
(`{id, name, description, member_agent_ids[], created_at}` — backed up like agents,
restored by id; gitignored). CRUD: `GET/POST/PUT/DELETE /api/juries`.

**Evaluation engine** `POST /api/jury/evaluate` (SSE), body `{jury_id | member_agent_ids[],
text, context?, criteria?}`: for each member it loads the agent's `system_prompt` + bound
`rag_collections` (legal text passages injected via `query_collections`), runs a verdict
call (`format:"json"`, model = `agent.model` or `_model_for("science")`) **sequentially
under `_model_session`**, and streams a `member` frame `{agent, icon, score, befund,
risiken[], empfehlung}`. After all members a **synthesis** call (general model) streams a
`summary` frame `{gesamturteil, score, konsens, hauptkritik[], empfehlungen[]}` (fallback
score = mean of member scores), then `done`. Reuses `_parse_llm_json` + `_sse`.

**Frontend (`jury.js`, no new tab):** management modal opened from the 🤖 Agenten tab
(`#btn-juries`) to create/edit juries (member checkboxes, legal/favourite agents first);
a reusable overlay **`Jury.evaluate(text, {title, context})`** streams the verdicts/summary
as cards. Wired into workflows via buttons: **Dokumente** (`#btn-docgen-jury` → checks the
generated document), the **agent edit modal** (`#btn-agent-prompt-jury` → checks the
system-prompt field), and the **Planer** (`#btn-planner-agent-jury` → checks the derived
project-agent prompt with the project description as context).

### Data File Formats (`data/`)

| Path | Schema |
|---|---|
| `data/ai_framework_thomas.db` | SQLite: conversations, messages, messages_fts, RAG tables |
| `data/agents/<slug>.json` | `{id, name, description, system_prompt, tools[], model?, icon, category, favorite, rag_collections[]}` |
| `data/plans/<slug>_<id8>.json` | `{id, name, description, system_prompt, resource_mode, resource_catalog[], start_date, workdays, tasks[{id, name, duration, predecessors[], successors[], resource_list[], is_start, is_end, notes}]}` |
| `data/code/<slug>_<id6>.json` | `{id, name, code, updated_at}` |
| `data/juries/<slug>_<id6>.json` | `{id, name, description, member_agent_ids[], created_at}` — in backup, gitignored |
| `data/user_profile.json` | user name/company/position + mode, lang, tone, **model roles** (`model_general/coding/science/medical`), **`hidden_tabs`** (optional tabs hidden; defaults to all eight on first run), etc. |
| `data/api_providers.json` | `[{id, name, base_url, api_key, models[]}]` external OpenAI-compatible providers — **contains API keys → not in backup/git/bundles** |
| `data/mail.json` | `{protocol, host, port, user, ssl, password}` — **not** in backup/git |
| `data/mail_rules.json` | `[{id, name, filter:{from,subject,domain}, actions:[…≤4]}]` — not in git |

Filenames use `_to_slug()` (ä→ae, ö→oe, ü→ue, ß→ss; special chars → `_`).

## Development Conventions

These rules prevent the most common backend mistakes:

- **API endpoint ordering:** Register all new `@app.<method>` routes **before** the `app.mount("/", StaticFiles(...))` call at the bottom of `main.py`. The static mount is a catch-all — any route defined after it is unreachable.
- **VRAM guard:** Every Ollama call **must** be wrapped: `async with _model_session(model), httpx.AsyncClient() as client:`. Never call Ollama outside `_model_session` — this is what prevents two models from loading simultaneously on ~6 GB VRAM.
- **Model validation:** Always pass user-supplied model names through `_pick_model(body.get("model"))`. This rejects frontend placeholder strings (`Lade…`, `qwen3.6-16k:latest`) and prevents 500 errors.
- **Parsing LLM JSON:** Small models wrap JSON in `<think>` tags and code fences. Strip these, extract JSON via regex, and always provide a fallback — see existing non-streaming endpoints (`_derive_adaptive_prompt`, planner endpoints) for the pattern.
- **New frontend modules:** Add a `<script src="/static/js/mymodule.js">` to `static/index.html` AND call `MyModule.init()` inside the `DOMContentLoaded` handler in `app.js`.

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
