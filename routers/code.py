"""Router: Code-Workspace + Coding-Agent (/api/code)

Aus ``main.py`` ausgelagert (reines Backend-Refactoring, kein Verhaltenswechsel).
Geteilte Namen kommen ueber ``from core import *``.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import io
import json
import math
import os
import random
import re
import shutil
import sys
import tempfile
import time
import uuid
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import aiofiles
import httpx
from fastapi import (APIRouter, Body, Depends, File, Form, HTTPException, Query,
                     Request, UploadFile)
from fastapi.responses import (FileResponse, HTMLResponse, JSONResponse,
                               PlainTextResponse, RedirectResponse, Response,
                               StreamingResponse)
from pydantic import BaseModel

import db as _db
from tools import llm as _llm
from tools import transcribe as _transcribe

from core import *  # noqa: F401,F403  (geteilte Kernflaeche)
import core as _core  # noqa: F401

router = APIRouter()


# ── Code-IDE ─────────────────────────────────────────────────────────────────


def _agent_def_by_id(agent_id: str) -> dict:
    if not agent_id:
        return {}
    fp = _agent_path_by_id(agent_id)
    if not fp:
        return {}
    try:
        return json.loads(fp.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}


def _profile_code_context() -> str:
    """Kurzer Profil-Kontext für den adaptiven Code-Agenten."""
    p = _load_profile()
    bits = []
    for k, label in (("position", "Position"), ("department", "Abteilung"),
                     ("company", "Firma")):
        v = str(p.get(k, "")).strip()
        if v:
            bits.append(f"{label}: {v}")
    mode = str(p.get("mode", "")).strip()
    if mode:
        bits.append(f"Fachmodus: {mode}")
    return "; ".join(bits)


_CODE_BASE_SYS = (
    "Du bist ein erfahrener Software-Entwickler. Schreibe sauberen, lauffähigen, "
    "sinnvoll kommentierten Code. Halte Erklärungen kurz — der Code steht im Vordergrund."
)


@router.post("/api/code/assist")
async def code_assist(req: Request):
    """Code-Assistent für den Code-Tab. Stellt — sofern nötig — zuerst Rückfragen
    (Phase 1), erzeugt dann Code (Phase 2). Optional mit wählbarem Coding-Agenten
    (inkl. hinterlegtem Beispielcode) und adaptiver Rollen-/Profil-Analyse."""
    body = await req.json()
    prompt = str(body.get("prompt", "")).strip()
    if not prompt:
        raise HTTPException(status_code=400, detail="Keine Aufgabe angegeben")
    answers = str(body.get("answers", "")).strip()
    language = str(body.get("language", "") or "").strip()[:30]
    agent_id = str(body.get("agent_id", "")).strip()
    adaptive = bool(body.get("adaptive"))
    force_code = bool(body.get("force_code"))
    current_code = str(body.get("current_code", "") or "")[:8000]
    model = _pick_model(body.get("model"), _model_for("coding"))
    num_ctx = _profile_num_ctx()

    agent = _agent_def_by_id(agent_id)
    persona = str(agent.get("system_prompt", "")).strip()
    example_code = str(agent.get("example_code", "")).strip()

    # Adaptiver Agent: Experten-Rolle aus Aufgabe ableiten (nur ohne expliziten Agenten)
    adaptive_note = ""
    if adaptive and not persona:
        role, sysp = await _derive_adaptive_prompt(prompt, model, num_ctx)
        if sysp:
            persona, adaptive_note = sysp, role

    sys_parts = [persona or _CODE_BASE_SYS]
    pctx = _profile_code_context()
    if adaptive and pctx:
        sys_parts.append(f"Kontext zum Nutzer (nutze, wenn hilfreich): {pctx}.")
    if example_code:
        sys_parts.append("Orientiere dich an Stil und Struktur dieses Beispielcodes:\n"
                         "```\n" + example_code[:4000] + "\n```")
    if language:
        sys_parts.append(f"Bevorzugte Programmiersprache: {language}.")
    system = "\n\n".join(sys_parts)

    # ── Phase 1: Rückfragen ─────────────────────────────────────────────────
    if not force_code and not answers:
        clarify_sys = system + (
            "\n\nPrüfe, ob WESENTLICHE Informationen fehlen, um die Aufgabe korrekt zu "
            "lösen (Eingaben/Ausgaben, Sprache, Rahmenbedingungen). Wenn ja: stelle bis zu "
            "4 kurze, konkrete Rückfragen. Wenn alles hinreichend klar ist: leere Liste. "
            'Antworte NUR mit JSON: {"questions":["…"]}.')
        try:
            async with _model_session(model), httpx.AsyncClient(timeout=120) as client:
                resp = await _llm.chat(client, {
                    "model": model, "think": False, "stream": False, "format": "json",
                    "messages": [{"role": "system", "content": clarify_sys},
                                 {"role": "user", "content": f"Aufgabe:\n{prompt}"}],
                    "options": {"num_ctx": num_ctx}, "keep_alive": KEEP_ALIVE,
                })
                resp.raise_for_status()
            _jc = resp.json()
            _cti, _cto = _llm_tok(_jc)
            d = _parse_llm_json(_jc.get("message", {}).get("content", "")) or {}
            qs = [str(q).strip() for q in (d.get("questions") or []) if str(q).strip()][:4]
        except Exception:
            qs, _cti, _cto = [], 0, 0
        if qs:
            return {"type": "questions", "questions": qs, "adaptive_role": adaptive_note,
                    "tokens": {"in": _cti, "out": _cto}}

    # ── Phase 2: Code erzeugen ──────────────────────────────────────────────
    usr = f"Aufgabe:\n{prompt}"
    if answers:
        usr += f"\n\nZusätzliche Antworten/Vorgaben:\n{answers}"
    if current_code:
        usr += f"\n\nBestehender Code (anpassen/erweitern, falls passend):\n```\n{current_code}\n```"
    usr += "\n\nGib eine vollständige, lauffähige Lösung — Code in EINEM ```-Codeblock."
    code_sys = system + "\n\nAntworte mit einer kurzen Erklärung und dem Code in genau EINEM ```-Codeblock."
    try:
        async with _model_session(model), httpx.AsyncClient(timeout=240) as client:
            resp = await _llm.chat(client, {
                "model": model, "think": False, "stream": False,
                "messages": [{"role": "system", "content": code_sys},
                             {"role": "user", "content": usr}],
                "options": {"num_ctx": num_ctx, "temperature": 0.2}, "keep_alive": KEEP_ALIVE,
            })
            resp.raise_for_status()
        _jc = resp.json()
        _cti, _cto = _llm_tok(_jc)
        content = str(_jc.get("message", {}).get("content", ""))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Code-Erzeugung fehlgeschlagen: {e}")
    code = _extract_code_block(content) or content.strip()
    note = re.sub(r"```[a-zA-Z0-9_+-]*\n[\s\S]*?```", "", content).strip()[:600]
    return {"type": "code", "code": code, "note": note,
            "adaptive_role": adaptive_note, "language": language,
            "tokens": {"in": _cti, "out": _cto}}


@router.get("/api/code")
async def list_code():
    programs = []
    for f in CODE_DIR.glob("*.json"):
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
            programs.append({"id": d["id"], "name": d.get("name", ""), "updated_at": d.get("updated_at", 0)})
        except Exception:
            pass
    return sorted(programs, key=lambda x: x.get("updated_at", 0), reverse=True)


@router.get("/api/code/{prog_id}")
async def get_code_program(prog_id: str):
    fp = _code_path_by_id(prog_id)
    if not fp:
        raise HTTPException(404, "Programm nicht gefunden")
    return json.loads(fp.read_text(encoding="utf-8"))


@router.post("/api/code")
async def save_code_program(req: Request):
    body = await req.json()
    name = str(body.get("name") or "Unbenannt").strip()
    code = str(body.get("code") or "")
    prog_id = str(body.get("id") or "").strip()
    if not prog_id:
        prog_id = (_to_slug(name) or "prog") + "_" + uuid.uuid4().hex[:6]
    fp = _code_path_by_id(prog_id)
    if not fp:
        fp = CODE_DIR / f"{_to_slug(name)}_{prog_id[-6:]}.json"
    data = {"id": prog_id, "name": name, "code": code, "updated_at": time.time()}
    fp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return data


@router.delete("/api/code/{prog_id}")
async def delete_code_program(prog_id: str):
    fp = _code_path_by_id(prog_id)
    if not fp:
        raise HTTPException(404, "Programm nicht gefunden")
    fp.unlink()
    return {"ok": True}


_CODE_PROJECT_SYSTEM = (
    "Du bist ein erfahrener Software-Architekt. Erzeuge zu einer Aufgabe eine sinnvolle, "
    "kohärente MEHRDATEI-Projektstruktur. Wähle eine übliche Aufteilung (Einstiegspunkt, "
    "Module/Pakete, ggf. Tests, README, ggf. Konfig/Abhängigkeiten). Gib JEDE Datei mit "
    "RELATIVEM Pfad (Schrägstriche als Trenner, KEIN führender Slash, kein „..“, keine "
    "absoluten Pfade) und vollständigem, lauffähigem Inhalt aus. Halte das Projekt fokussiert: "
    "höchstens {maxfiles} Dateien, jede Datei kompakt. Schreibe echten Code — KEINE Auslassungs-"
    "Platzhalter wie „…“ oder „TODO Rest“. Kommentare/Texte auf Deutsch. "
    'Antworte NUR mit JSON: {"files":[{"path":"ordner/datei.ext","content":"<voller Inhalt>"}],'
    '"note":"1–2 Sätze, was die Struktur enthält"}.'
)


@router.post("/api/code/project")
async def code_project(req: Request):
    """Erzeugt zu einer Aufgabe eine Mehrdatei-Projektstruktur (Dateibaum + Inhalte)
    als JSON. Optional mit Coding-Agent (Persona/`example_code`) und Sprache/Stack.
    Nicht direkt ausführbar — Anzeige als Baum im Code-Tab, Download als ZIP."""
    body = await req.json()
    prompt = str(body.get("prompt", "")).strip()
    if not prompt:
        raise HTTPException(status_code=400, detail="Keine Aufgabe angegeben")
    language = str(body.get("language", "") or "").strip()[:60]
    agent_id = str(body.get("agent_id", "")).strip()
    try:
        max_files = int(body.get("max_files", 10))
    except Exception:
        max_files = 10
    max_files = max(2, min(16, max_files))
    model = _pick_model(body.get("model"), _model_for("coding"))
    num_ctx = _profile_num_ctx()

    agent = _agent_def_by_id(agent_id)
    persona = str(agent.get("system_prompt", "")).strip()
    example_code = str(agent.get("example_code", "")).strip()

    sys_parts = [_CODE_PROJECT_SYSTEM.format(maxfiles=max_files)]
    if persona:
        sys_parts.append("Rolle/Vorgaben des gewählten Agenten:\n" + persona)
    if example_code:
        sys_parts.append("Orientiere dich an Stil/Struktur dieses Beispielcodes:\n```\n"
                         + example_code[:3000] + "\n```")
    if language:
        sys_parts.append(f"Sprache/Stack: {language}.")
    system = "\n\n".join(sys_parts)

    usr = f"Aufgabe:\n{prompt}\n\nLiefere höchstens {max_files} Dateien."

    try:
        async with _model_session(model), httpx.AsyncClient(timeout=300) as client:
            resp = await _llm.chat(client, {
                "model": model, "think": False, "stream": False, "format": "json",
                "messages": [{"role": "system", "content": system},
                             {"role": "user", "content": usr}],
                "options": {"num_ctx": num_ctx, "temperature": 0.2}, "keep_alive": KEEP_ALIVE,
            })
            resp.raise_for_status()
        _jc = resp.json()
        tin, tout = _llm_tok(_jc)
        data = _parse_llm_json(_jc.get("message", {}).get("content", "")) or {}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Projekt-Erzeugung fehlgeschlagen: {e}")

    files, seen = [], set()
    for f in (data.get("files") or []):
        path = _safe_relpath((f or {}).get("path", ""))
        content = (f or {}).get("content", "")
        if not isinstance(content, str):
            content = json.dumps(content, ensure_ascii=False, indent=2)
        if not path or path in seen:
            continue
        seen.add(path)
        files.append({"path": path, "content": content})
        if len(files) >= max_files:
            break
    note = str(data.get("note", "")).strip()[:600]
    return {"files": files, "note": note, "tokens": {"in": tin, "out": tout}}


@router.post("/api/code/project-zip")
async def code_project_zip(req: Request):
    """Packt eine (im Code-Tab erzeugte/bearbeitete) Projektstruktur in ein ZIP zum
    Download. Pfade werden serverseitig auf sichere relative Pfade reduziert."""
    import io, zipfile, re as _re
    body = await req.json()
    files = body.get("files") or []
    if not isinstance(files, list) or not files:
        raise HTTPException(status_code=400, detail="Keine Dateien übergeben")
    zipname = _re.sub(r"[^\w\-]+", "_", str(body.get("zipname", "")).strip()) or "projekt"
    buf = io.BytesIO()
    seen: set[str] = set()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for i, f in enumerate(files):
            path = _safe_relpath((f or {}).get("path", "")) or f"datei_{i + 1}.txt"
            base, n = path, 2
            while path in seen:
                if "." in base.rsplit("/", 1)[-1]:
                    stem, ext = base.rsplit(".", 1)
                    path = f"{stem}_{n}.{ext}"
                else:
                    path = f"{base}_{n}"
                n += 1
            seen.add(path)
            zf.writestr(path, str((f or {}).get("content", "")))
    buf.seek(0)
    return StreamingResponse(
        buf, media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{zipname}.zip"'},
    )


@router.post("/api/code/run-python")
async def run_python_code(req: Request):
    """Führt Python-Code aus dem Code-Tab serverseitig aus (stdout/stderr,
    matplotlib-Plots, Zeitlimit). Im Mehrbenutzer-/Servermodus über
    config.json `allow_python_exec: false` abschaltbar."""
    if not ALLOW_PYTHON_EXEC:
        raise HTTPException(403, "Python-Ausführung ist in dieser Installation deaktiviert.")
    body = await req.json()
    code = str(body.get("code") or "")
    if not code.strip():
        return {"output": "", "error": "", "images": []}
    try:
        t = float(body.get("timeout") or 15.0)
    except Exception:
        t = 15.0
    t = max(1.0, min(t, 60.0))
    # Ausführung (blockierend mit Zeitlimit) im Threadpool, damit der Event-Loop frei bleibt
    return await asyncio.to_thread(_run_python_code, code, t)


# ── Autonomer Coding-Agent (Agent-Harness im Code-Tab) ────────────────────────
# Aider-/Claude-Code-artiger Loop: eine Aufgabe → das Modell nutzt Werkzeuge
# (Dateien auflisten/lesen/schreiben, Python im Sandkasten prüfen), iteriert selbst
# bis fertig. Die Dateien liegen im Client (Workspace) und werden mitgeschickt; der
# Agent arbeitet auf einer In-Memory-Kopie und liefert am Ende den neuen Stand.
# HTML/JS-Ergebnisse werden im Client-Canvas gerendert (nicht hier ausgeführt).

_CODE_AGENT_SYSTEM = (
    "Du bist ein autonomer Coding-Agent in einer Entwickler-Werkbank. Löse die Aufgabe des "
    "Nutzers eigenständig in kleinen, überprüfbaren Schritten mit den bereitgestellten "
    "Werkzeugen:\n"
    "- list_files(): vorhandene Dateien auflisten\n"
    "- read_file(path): eine Datei lesen\n"
    "- write_file(path, content): eine Datei anlegen oder KOMPLETT überschreiben — immer den "
    "VOLLSTÄNDIGEN Dateiinhalt angeben (keine Auslassungen, kein „…“)\n"
    "- run_python(code): Python im Sandkasten ausführen, um Python-Ergebnisse zu PRÜFEN "
    "(liefert stdout/stderr; kein Datei- oder Netzzugriff)\n"
    "Arbeite iterativ: schreibe/ändere Dateien, prüfe, korrigiere Fehler. Für WEB-/CANVAS-"
    "Aufgaben schreibe selbstständig lauffähiges HTML/JS (z. B. eine index.html mit allem "
    "inline, ohne Server) — die Anzeige erfolgt im Browser-Canvas; nutze dafür NICHT "
    "run_python. Werden dir Konsolenfehler gemeldet, behebe sie. "
    "Wenn die Aufgabe erledigt ist, antworte mit einer KURZEN Zusammenfassung (1–3 Sätze) und "
    "OHNE weiteren Werkzeugaufruf."
)


def _code_agent_tools(allow_py: bool) -> list:
    tools = [
        {"type": "function", "function": {
            "name": "list_files",
            "description": "Listet die vorhandenen Dateien (Pfade) im Arbeitsbereich.",
            "parameters": {"type": "object", "properties": {}}}},
        {"type": "function", "function": {
            "name": "read_file",
            "description": "Liest den vollständigen Inhalt einer Datei.",
            "parameters": {"type": "object", "properties": {
                "path": {"type": "string", "description": "Relativer Dateipfad"}},
                "required": ["path"]}}},
        {"type": "function", "function": {
            "name": "write_file",
            "description": "Legt eine Datei an oder überschreibt sie KOMPLETT. Immer den "
                           "vollständigen Dateiinhalt angeben.",
            "parameters": {"type": "object", "properties": {
                "path": {"type": "string"}, "content": {"type": "string"}},
                "required": ["path", "content"]}}},
    ]
    if allow_py:
        tools.append({"type": "function", "function": {
            "name": "run_python",
            "description": "Führt Python-Code im Sandkasten aus und liefert stdout/stderr. "
                           "Zum Prüfen von Python-Ergebnissen — NICHT für HTML/JS.",
            "parameters": {"type": "object", "properties": {
                "code": {"type": "string"}}, "required": ["code"]}}})
    return tools


def _extract_file_blocks(text: str) -> list:
    """Best-effort-Fallback (wenn ein Modell keine Tool-Aufrufe macht): gefencte
    Codeblöcke mit Datei-Hinweis aus Fließtext ziehen. Rückgabe [(path, content)]."""
    out = []
    for m in re.finditer(r"```([^\n`]*)\n(.*?)```", text, flags=re.DOTALL):
        info = (m.group(1) or "").strip()
        code = m.group(2)
        path = ""
        mm = re.search(r"([\w./-]+\.\w{1,5})", info)
        if mm:
            path = mm.group(1)
        else:
            pre = text[:m.start()].rstrip().split("\n")[-1] if m.start() else ""
            m2 = re.search(r"([\w./-]+\.\w{1,5})", pre)
            if m2:
                path = m2.group(1)
            else:
                path = {"html": "index.html", "js": "main.js", "javascript": "main.js",
                        "python": "main.py", "py": "main.py"}.get(info.lower(), "")
        p = _safe_relpath(path)
        if p and code.strip():
            out.append((p, code.rstrip("\n")))
    return out


async def _code_agent_generator(body: dict):
    task = str(body.get("task", "") or "").strip()
    if not task:
        yield _sse({"type": "error", "message": "Keine Aufgabe angegeben."})
        return

    files: dict = {}
    for f in (body.get("files") or []):
        p = _safe_relpath((f or {}).get("path", ""))
        if p:
            files[p] = str((f or {}).get("content", "") or "")

    model = _pick_model(body.get("model"), _model_for("coding"))
    num_ctx = _profile_num_ctx()
    try:
        max_steps = int(body.get("max_steps") or 12)
    except Exception:
        max_steps = 12
    max_steps = max(1, min(max_steps, 20))
    allow_py = bool(ALLOW_PYTHON_EXEC)
    changed: set = set()

    def _apply_write(path, content):
        p = _safe_relpath(path)
        if not p:
            return None
        files[p] = str(content or "")
        changed.add(p)
        return p

    filelist = "\n".join(f"- {p}" for p in files) or "(leer)"
    system = _CODE_AGENT_SYSTEM + ("" if allow_py else
             "\n\nHINWEIS: Python-Ausführung ist in dieser Installation deaktiviert — "
             "run_python steht NICHT zur Verfügung.")
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": f"Aufgabe:\n{task}\n\nVorhandene Dateien:\n{filelist}"},
    ]
    tools = _code_agent_tools(allow_py)
    tok = {"in": 0, "out": 0}

    try:
        for _step in range(max_steps):
            async with _model_session(model), httpx.AsyncClient(timeout=180) as client:
                resp = await _llm.chat(client, {
                    "model": model, "think": False, "stream": False,
                    "messages": messages, "tools": tools,
                    "options": {"num_ctx": num_ctx}, "keep_alive": KEEP_ALIVE,
                })
                resp.raise_for_status()
            result = resp.json()
            _ti, _to = _llm_tok(result)
            tok["in"] += _ti
            tok["out"] += _to
            msg = result.get("message", {}) or {}
            content_raw = msg.get("content", "") or ""
            tool_calls = msg.get("tool_calls") or []
            if not tool_calls:
                inline = _extract_inline_tool_calls(content_raw)
                if inline:
                    tool_calls = inline
                    content_raw = _strip_inline_tool_calls(content_raw)
            content_raw = re.sub(r"<think>.*?</think>", "", content_raw, flags=re.DOTALL).strip()

            if not tool_calls:
                # Fertig — oder weiches Fallback: Datei-Blöcke aus dem Text übernehmen
                for p, c in _extract_file_blocks(content_raw):
                    if _apply_write(p, c):
                        yield _sse({"type": "step", "tool": "write_file", "arg": p,
                                    "result": "aus Text übernommen"})
                yield _sse({"type": "text", "content": content_raw or "Fertig."})
                break

            messages.append({"role": "assistant", "content": content_raw, "tool_calls": tool_calls})
            for tc in tool_calls:
                fn = (tc.get("function") or {}).get("name", "")
                args = (tc.get("function") or {}).get("arguments")
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except Exception:
                        args = {}
                args = args or {}

                if fn == "list_files":
                    res = "\n".join(files.keys()) or "(keine Dateien)"
                    yield _sse({"type": "step", "tool": "list_files", "arg": "",
                                "result": f"{len(files)} Datei(en)"})
                elif fn == "read_file":
                    p = _safe_relpath(args.get("path", ""))
                    if p in files:
                        res = files[p]
                        yield _sse({"type": "step", "tool": "read_file", "arg": p,
                                    "result": f"{len(res)} Zeichen"})
                    else:
                        res = f"FEHLER: Datei '{p}' existiert nicht."
                        yield _sse({"type": "step", "tool": "read_file", "arg": p,
                                    "result": "nicht gefunden"})
                elif fn == "write_file":
                    p = _apply_write(args.get("path", ""), args.get("content", ""))
                    if p:
                        res = f"Datei '{p}' geschrieben ({len(files[p])} Zeichen)."
                        yield _sse({"type": "step", "tool": "write_file", "arg": p,
                                    "result": "geschrieben"})
                    else:
                        res = "FEHLER: Ungültiger Pfad."
                        yield _sse({"type": "step", "tool": "write_file",
                                    "arg": str(args.get("path", "")), "result": "Fehler"})
                elif fn == "run_python" and allow_py:
                    code = str(args.get("code", "") or "")
                    out = await asyncio.to_thread(_run_python_code, code, 15.0)
                    res = ""
                    if out.get("output"):
                        res += "STDOUT:\n" + out["output"]
                    if out.get("error"):
                        res += "\nSTDERR:\n" + out["error"]
                    res = (res.strip() or "(keine Ausgabe)")[:4000]
                    yield _sse({"type": "step", "tool": "run_python", "arg": code[:60],
                                "result": "Fehler" if out.get("error") else "ok"})
                else:
                    res = f"Werkzeug '{fn}' ist nicht verfügbar."
                    yield _sse({"type": "step", "tool": fn or "?", "arg": "", "result": "n/a"})

                messages.append({"role": "tool", "content": res[:6000]})
        else:
            yield _sse({"type": "text",
                        "content": f"Maximale Schrittzahl ({max_steps}) erreicht — "
                                   f"Zwischenstand wird übernommen."})
    except httpx.ConnectError:
        yield _sse({"type": "error", "message": "Ollama nicht erreichbar — läuft der lokale Server?"})
    except httpx.HTTPStatusError as e:
        yield _sse({"type": "error",
                    "message": f"Modell abgelehnt (num_ctx/VRAM?): HTTP {e.response.status_code}"})
    except Exception as e:
        yield _sse({"type": "error", "message": f"Agent-Fehler: {type(e).__name__}: {e}"})

    yield _sse({"type": "files",
                "files": [{"path": p, "content": c} for p, c in files.items()],
                "changed": sorted(changed)})
    yield _sse({"type": "done", "tokens": tok})


@router.post("/api/code/agent")
async def code_agent(req: Request):
    """Autonomer Coding-Agent (SSE): löst eine Aufgabe eigenständig über einen
    Werkzeug-Loop (Dateien lesen/schreiben, Python-Sandkasten). Liefert Schritt-Frames
    (`step`), finalen Text, den neuen Dateistand (`files`) und `done` mit Tokens."""
    body = await req.json()
    return StreamingResponse(
        _code_agent_generator(body),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
