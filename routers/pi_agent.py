# -*- coding: utf-8 -*-
"""Router: Pi-Agent-Tab — ein externer Agenten-Harness INNERHALB des Frameworks.

[Pi](https://pi.dev) (Earendil, MIT) läuft als **Unterprozess** und bekommt das
Framework als Werkzeug: ``pi-agent/framework_cli.py`` plus die Skill-Datei, die
Pi mit ``--skill`` direkt lädt. Pi plant und entscheidet, das Framework rechnet
und verwaltet.

**Warum Unterprozess und nicht eingebettet:** Pi ist Node.js, das Framework
Python. Ein Unterprozess je Lauf ist dasselbe crash-sichere Muster wie die
`z-image`/`z-video`-Brücken — stirbt Pi, stirbt nur der Unterprozess. Kein
``_model_session``/VRAM-Lock: Pi spricht Ollama selbst über seinen eigenen
Provider an.

**Arbeitsordner ist eine Sandbox** (``data/pi/workspace``), nicht der
Programmordner. Pi hat eingebaute ``read``/``write``/``edit``/``bash``-Werkzeuge;
mit dem Programmordner als cwd könnte es den Framework-Code ändern.

**Ehrliche Grenze:** Pi braucht ``bash``, um die CLI aufzurufen — damit kann es
grundsätzlich beliebige Befehle unter deinem Benutzerkonto ausführen. Die Stufe
``pi_agent_access`` steuert, was die **CLI** herausgibt; sie ist eine
Leitplanke, keine Sandbox. Pi selbst hat ausdrücklich kein Rechtesystem.
"""

import asyncio
import json
import os
import re
import shutil
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from core import *  # noqa: F401,F403
import core as _core  # noqa: F401

router = APIRouter()

#: Programmordner (routers/ liegt eine Ebene darunter)
_APP_DIR = Path(__file__).resolve().parent.parent
#: Ordner der Anbindung (CLI + Skill-Beschreibung)
PI_AGENT_DIR = _APP_DIR / "pi-agent"
#: Arbeitsordner für Pi — bewusst NICHT der Programmordner (s. Modul-Docstring)
PI_WORKSPACE = DATA_DIR / "pi" / "workspace"
#: Sitzungen (Gesprächsverläufe). Pi legt sie sonst unter dem Benutzerprofil ab;
#: hier bleiben sie beim Framework.
PI_SESSIONS = DATA_DIR / "pi" / "sessions"

#: Obergrenze je Lauf (Sekunden); Profil `pi_timeout` überschreibt
_PI_TIMEOUT_DEFAULT = 900


def _pi_binary() -> str:
    """Pfad zur `pi`-Programmdatei oder leer, wenn nicht installiert."""
    p = str(_load_profile().get("pi_binary", "") or "").strip()
    if p and Path(p).exists():
        return p
    return shutil.which("pi") or ""


def _pi_command() -> list:
    """Argv-Vorspann, um Pi zu starten — unter Windows OHNE ``cmd.exe``.

    ``shutil.which("pi")`` liefert dort ``pi.CMD``, einen Batch-Wrapper, der
    seine Argumente mit ``%*`` an ``node.exe … cli.js`` weitergibt. Damit läuft
    **jedes Argument durch cmd.exe** — und ein ``<``, ``>``, ``&``, ``|`` oder
    ``%`` darin wird als Umleitung bzw. Verkettung gedeutet. Gemessen: der
    Arbeitsauftrag enthielt ``<verb>``, cmd.exe schnitt die Befehlszeile ab und
    Pi beendete sich direkt nach dem ``session``-Frame, ohne Fehlermeldung.

    Darum wird der Wrapper umgangen: ``node.exe`` direkt mit dem gebündelten
    ``cli.js``. Python quotet die Argumente dann korrekt für ``CreateProcess``,
    und auch Nutzereingaben mit Sonderzeichen sind unproblematisch. Fällt die
    Auflösung aus, wird der Wrapper benutzt (dann gilt die Einschränkung).
    """
    binary = _pi_binary()
    if not binary:
        return []
    p = Path(binary)
    if p.suffix.lower() not in (".cmd", ".bat"):
        return [binary]          # Linux/macOS: direkt ausführbar
    cli = (p.parent / "node_modules" / "@earendil-works" / "pi-coding-agent"
           / "dist" / "bundle" / "cli.js")
    node = p.parent / "node.exe"
    node_exe = str(node) if node.exists() else (shutil.which("node") or "")
    if cli.exists() and node_exe:
        return [node_exe, str(cli)]
    return [binary]


def _python_binary() -> str:
    """Python, mit dem die CLI laufen soll — bevorzugt die venv des Frameworks."""
    for cand in (_APP_DIR / "venv" / "Scripts" / "python.exe",
                 _APP_DIR / "venv" / "bin" / "python"):
        if cand.exists():
            return str(cand)
    import sys
    return sys.executable or "python"


def _pi_timeout() -> int:
    try:
        v = int(_load_profile().get("pi_timeout", 0) or 0)
    except Exception:
        v = 0
    return v if 60 <= v <= 7200 else _PI_TIMEOUT_DEFAULT


# ── Arbeitsauftrag an Pi (das „AGENTS.md"-Äquivalent) ────────────────────────
# Wird über --append-system-prompt übergeben. Enthält den ABSOLUTEN CLI-Aufruf:
# Pis cwd ist die Sandbox, ein relativer Pfad würde dort nicht aufgehen.

def _context_prompt() -> str:
    cli = PI_AGENT_DIR / "framework_cli.py"
    befehl = f'"{_python_binary()}" "{cli}"'
    stufe = _pi_agent_access()
    schreibt = ("Du darfst auch anlegen und ändern (Projekte, Aufgabenlisten, "
                "Wissensdatenbanken)." if stufe == "write" else
                "Du darfst NUR lesen und rechnen — nichts anlegen oder ändern. "
                "Fragt der Nutzer nach einer Änderung, sage ihm, dass dafür die "
                "Zugriffsstufe \"write\" nötig wäre.")
    return (
        "# Arbeitsumgebung\n\n"
        "Du arbeitest INNERHALB von AI_Framework_Thomas, einer lokalen "
        "KI-Arbeitsumgebung. Du bist nicht allein: das Framework bringt fertige, "
        "geprüfte Fähigkeiten mit, die du benutzen sollst, statt sie selbst "
        "nachzubauen oder im Kopf zu rechnen.\n\n"
        "## Deine Werkzeuge heißen `fw_…`\n\n"
        "Alle Fähigkeiten des Frameworks stehen dir als **eigene Werkzeuge** zur "
        "Verfügung, erkennbar am Präfix `fw_` — zum Beispiel `fw_bolt_calculator` "
        "(Schrauben nach VDI 2230), `fw_mechanik_calc` (Festigkeit, Biegung, "
        "Wellen, Knickung, Lager, Zahnräder, Antrieb), `fw_web_search` "
        "(Internetsuche), `fw_material_lookup`, `fw_unit_convert`, "
        "`fw_solve_equation`, `fw_plot_chart`, `fw_create_diagram`, "
        "`fw_route_planner`, `fw_generate_report`, `fw_search_patents`. "
        "Dazu **`fw_cli`** für den Bestand des Nutzers und mehrstufige Recherche.\n\n"
        "**SCHREIBE KEINEN EIGENEN RECHENCODE.** Du hast für technische Größen "
        "fertige, geprüfte Werkzeuge. Ein selbst geschriebenes Skript liefert hier "
        "falsche Zahlen — die `fw_`-Werkzeuge rechnen nach Norm und zeigen ihre "
        "Annahmen mit. Dasselbe gilt für Fakten: belege sie mit `fw_web_search`, "
        "statt sie zu erinnern.\n\n"
        "Wenn du nicht weiterweißt, rufe `fw_cli` mit dem Verb `werkzeuge` auf — "
        "das listet alles mit Beschreibung.\n\n"
        f"Die Kommandozeile gibt es auch direkt: `{befehl} <verb> [argumente]`.\n\n"
        "## Wofür du die Werkzeuge benutzt — nicht dein eigenes Wissen\n\n"
        "- **Im Internet suchen:** `suche <begriff>` (oder "
        "`werkzeug web_search '{\"query\": \"...\"}'`). Konkrete Zahlen, Preise, "
        "Normen, Namen belegst du über die Suche, statt sie zu erinnern.\n"
        "- **Schrauben auslegen (VDI 2230):** `schraube <d> <steigung> <kraft_kN> [klasse]`\n"
        "- **Festigkeit, Biegung, Wellen, Knickung, Lager, Zahnräder, Antrieb:** "
        "`rechne` (ohne Argumente listet es alle Rechenarten)\n"
        "- **Jedes weitere Werkzeug:** `werkzeuge` listet sie mit Beschreibung, "
        "`werkzeuge <name>` zeigt die Parameter, "
        "`werkzeug <name> '<json>'` führt es aus. Darunter Werkstoffdaten, "
        "Einheitenumrechnung, Gleichungen, Diagramme, Mermaid-Diagramme, "
        "Routenplanung, Berichte (PDF/DOCX), Tabellen, Präsentationen, "
        "Patentsuche und die SymPy-Grundwahrheit für Mathe.\n"
        "- **Bestand des Nutzers lesen:** `projekte`, `plaene`, `todo-baum`, "
        "`todo-agenda`, `todo-frage <frage>`, `rag-sammlungen`, `gespraeche`, "
        "`patente-projekte`, `varianten`\n"
        "- **Mehrstufige Recherche:** `recherche <thema>` (zerlegt in Teilaspekte, "
        "sucht je Aspekt, fasst quellengestützt zusammen)\n\n"
        "**Rechne technische Größen niemals selbst** und erfinde keine Zahlen. "
        "Fehlen dir Angaben, frage den Nutzer. Meldet ein Werkzeug fehlende "
        "Parameter oder eine unpassende Einheit, hast du etwas falsch zugeordnet "
        "— korrigiere die Zuordnung.\n\n"
        f"## Was du darfst\n\n{schreibt}\n\n"
        "Es gibt in KEINER Stufe: löschen, Mails/Angebote/Rechnungen versenden, "
        "Patente einreichen, Einstellungen ändern. Suche dafür keine Umwege; "
        "sage dem Nutzer, dass er das selbst in der Oberfläche tun muss.\n\n"
        "## Dein Arbeitsordner\n\n"
        f"`{PI_WORKSPACE}` — hier darfst du Dateien anlegen (Notizen, Entwürfe). "
        "Der Programmordner des Frameworks ist nicht dein Arbeitsbereich; ändere "
        "dort nichts.\n\n"
        "## Antwortsprache\n\nAntworte auf Deutsch, fachlich und knapp."
    )


# ── Konfiguration ────────────────────────────────────────────────────────────

@router.get("/api/pi/config")
async def pi_config():
    """Ist Pi installiert, welche Modelle hat es, welche Zugriffsstufe gilt?"""
    binary = _pi_binary()
    info = {
        "installed": bool(binary),
        "binary": binary,
        "version": "",
        "models": [],
        "zugriff": _pi_agent_access(),
        "workspace": str(PI_WORKSPACE),
        "skill": str(PI_AGENT_DIR / "skills" / "framework-cli.md"),
        "provider": str(_load_profile().get("pi_provider", "") or ""),
        "model": str(_load_profile().get("pi_model", "") or ""),
        "timeout": _pi_timeout(),
    }
    if not binary:
        info["hinweis"] = (
            "Pi ist nicht installiert. Der Installer liegt bei: "
            "pi-agent/install_pi.ps1 (Windows) bzw. pi-agent/install_pi.sh (Linux)."
        )
        return info

    cmd = _pi_command()

    async def _run(*args, timeout=40):
        try:
            pr = await asyncio.create_subprocess_exec(
                *cmd, *args, stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL, cwd=str(_APP_DIR))
            out, _ = await asyncio.wait_for(pr.communicate(), timeout=timeout)
            return out.decode("utf-8", "replace")
        except Exception:
            return ""

    info["version"] = (await _run("--version", timeout=25)).strip().splitlines()[:1]
    info["version"] = info["version"][0] if info["version"] else ""

    # „provider  model  context …" — Kopfzeile überspringen, erste zwei Spalten nehmen
    bekannt = set()
    for zeile in (await _run("--list-models")).splitlines():
        teile = zeile.split()
        if len(teile) < 2 or teile[0] == "provider":
            continue
        bekannt.add(teile[1])
        info["models"].append({"provider": teile[0], "model": teile[1], "katalog": True})

    # Zusätzlich ALLE lokalen Ollama-Modelle anbieten. Pi nimmt Modelle, die
    # nicht in seinem Katalog stehen, mit dem Hinweis „Using custom model id"
    # trotzdem an (gemessen). Das ist hier entscheidend: Pis Katalog kennt auf
    # dieser Maschine nur zwei 27B-Modelle, die für den Agenten-Loop zu langsam
    # sind (ein Lauf lief 15 Minuten ohne eine einzige Ausgabezeile), während
    # die kleinen Modelle des Frameworks zügig durchlaufen.
    try:
        import httpx as _httpx
        async with _httpx.AsyncClient(timeout=8) as client:
            resp = await client.get(f"{OLLAMA_BASE}/api/tags")
            for m in (resp.json().get("models") or []):
                name = str(m.get("name") or "")
                # Embedding-Modelle können keinen Agenten fahren
                if not name or "embed" in name.lower() or name in bekannt:
                    continue
                info["models"].append({"provider": "ollama", "model": name,
                                       "katalog": False})
    except Exception:
        pass

    # Empfehlung markieren: das Standardmodell des Frameworks bzw. die
    # general-Rolle. Ohne das stand zufaellig ein Vision-Modell vorne, das fuer
    # einen Werkzeug-Loop die schlechtere Wahl ist.
    _bevorzugt = [str(DEFAULT_MODEL or ""), str(_model_for("general") or "")]
    _bevorzugt = [b for b in _bevorzugt if b]
    for m in info["models"]:
        m["empfohlen"] = m["model"] in _bevorzugt
    info["models"].sort(key=lambda m: (not m.get("empfohlen"), m.get("katalog"),
                                       m.get("model", "")))
    return info


# ── Lauf ─────────────────────────────────────────────────────────────────────

def _kurz(wert, n: int = 1200) -> str:
    s = wert if isinstance(wert, str) else json.dumps(wert, ensure_ascii=False)
    return s if len(s) <= n else s[:n] + " …"


def _inhalt(content) -> str:
    """Pis Werkzeugergebnis ist eine Liste ``[{type:"text", text:…}]``.

    Ohne diese Auspackung stünde rohes JSON in der Oberfläche statt des
    lesbaren Rechenergebnisses.
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        teile = []
        for c in content:
            if isinstance(c, dict):
                teile.append(str(c.get("text") or c.get("content") or ""))
            else:
                teile.append(str(c))
        return "\n".join(t for t in teile if t)
    return "" if content is None else json.dumps(content, ensure_ascii=False)


async def _pi_run_generator(prompt: str, provider: str, model: str,
                            thinking: str, session_id: str):
    """Pi als Unterprozess fahren und seine JSON-Frames auf SSE übersetzen."""
    cmd = _pi_command()
    if not cmd:
        yield _sse({"type": "error", "content":
                    "Pi ist nicht installiert. Installer: pi-agent/install_pi.ps1 bzw. .sh"})
        return
    if _pi_agent_access() == "off":
        yield _sse({"type": "error", "content":
                    "Der Pi-Zugriff ist abgeschaltet (config.json: pi_agent_access). "
                    "Die aktuelle Stufe steht im Profil unter „Installation\"."})
        return

    PI_WORKSPACE.mkdir(parents=True, exist_ok=True)
    skill = PI_AGENT_DIR / "skills" / "framework-cli.md"

    args = [
        "-p", "--mode", "json",
        "--offline",                 # keine Update-/Katalog-Abfragen beim Start
        "--no-context-files",        # deterministisch: kein AGENTS.md/CLAUDE.md-Fund
        "--append-system-prompt", _context_prompt(),
    ]

    # ── Welche eingebauten Pi-Werkzeuge ausgeschlossen werden ────────────────
    #
    # web_search: Pis eigenes laeuft ueber einen gehosteten Ollama-Dienst, der
    #   hier nicht angemeldet ist ("Unauthorized. Run `ollama signin`").
    #   Gemessen: das Modell rief es zuerst auf, bekam den Fehler und ERFAND
    #   danach die Zahlen. Die Websuche des Frameworks (fw_web_search) ist der
    #   Weg -- mit Quellenangabe und denselben Gates wie im Chat.
    #
    # bash: Standardmaessig AUS, und das ist ein echter Gewinn. Die Erweiterung
    #   startet die CLI selbst, Pi braucht fuer die Framework-Faehigkeiten also
    #   keine Shell. Gemessen: MIT bash versuchte das Modell zuerst zweimal,
    #   die Schraube mit selbstgeschriebenem Python auszurechnen (scheiterte am
    #   Windows-Python-Platzhalter) und kam erst danach auf das richtige
    #   Werkzeug. Ohne bash fallen beide Probleme weg: der Umweg ueber
    #   Eigenrechnen UND der beliebige Shell-Zugriff unter dem Nutzerkonto.
    #   Einschaltbar per Profil `pi_allow_bash`, wenn Pi wirklich eine Shell
    #   braucht -- dann gilt wieder: Leitplanke, keine Sandbox.
    _aus = ["web_search"]
    if not bool(_load_profile().get("pi_allow_bash", False)):
        _aus.append("bash")
    args += ["--exclude-tools", ",".join(_aus)]
    # Die Erweiterung registriert ALLE Framework-Werkzeuge als echte Pi-Werkzeuge
    # (fw_*) plus fw_cli. Das ist der tragende Weg; der Skill-Text bleibt als
    # zusaetzliche Erklaerung dabei.
    ext = PI_AGENT_DIR / "extensions" / "framework-tools.ts"
    if ext.exists():
        args += ["--extension", str(ext)]
    if skill.exists():
        args += ["--skill", str(skill)]
    if provider:
        args += ["--provider", provider]
    if model:
        args += ["--model", model]
    if thinking in ("off", "minimal", "low", "medium", "high", "xhigh", "max"):
        args += ["--thinking", thinking]
    # Sitzung: feste ID je Gespräch im Tab. `--session-id` legt sie an, wenn sie
    # fehlt, und setzt sonst GENAU dort fort — damit kann der Nutzer eine
    # Rückfrage des Agenten beantworten und Pi hat den Verlauf.
    # (Das frühere `--no-session` beim ersten Lauf war ein Fehler: dann gab es
    # nichts, was ein späteres `--continue` hätte fortsetzen können.)
    if session_id:
        PI_SESSIONS.mkdir(parents=True, exist_ok=True)
        args += ["--session-dir", str(PI_SESSIONS), "--session-id", session_id]
    else:
        args += ["--no-session"]
    args += ["--", prompt]

    env = dict(os.environ)
    env["PI_OFFLINE"] = "1"
    env.setdefault("PYTHONIOENCODING", "utf-8")
    # Die Erweiterung braucht die Adresse des laufenden Frameworks und den Pfad
    # zur CLI — Pis cwd ist die Sandbox, relative Pfade gehen dort nicht auf.
    env["AIF_BASE_URL"] = f"http://127.0.0.1:{int(_CONFIG.get('port', 8780) or 8780)}"
    env["AIF_CLI"] = str(PI_AGENT_DIR / "framework_cli.py")
    env["AIF_PYTHON"] = _python_binary()

    proc = None
    tok = {"in": 0, "out": 0}
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd, *args,
            # stdin MUSS geschlossen sein. Mit aktivierten Werkzeugen wartet Pi
            # sonst endlos auf stdin und gibt KEINE einzige Zeile aus (gemessen:
            # mit --no-tools lief es sofort, mit Werkzeugen 0 Bytes bis zum
            # Zeitlimit; `< /dev/null` behob es). Ohne diese Zeile hängt der Tab.
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(PI_WORKSPACE), env=env,
        )
        yield _sse({"type": "pi_start", "provider": provider, "model": model,
                    "workspace": str(PI_WORKSPACE), "zugriff": _pi_agent_access()})

        grenze = _pi_timeout()
        loop = asyncio.get_event_loop()
        start = loop.time()
        offen_text = False

        while True:
            if loop.time() - start > grenze:
                yield _sse({"type": "notice", "message":
                            f"Zeitlimit von {grenze} s erreicht — Lauf abgebrochen."})
                break
            try:
                raw = await asyncio.wait_for(proc.stdout.readline(), timeout=20)
            except asyncio.TimeoutError:
                # Lebenszeichen, damit die Oberfläche nicht „toter Strom" zeigt
                yield _sse({"type": "progress",
                            "seconds": int(loop.time() - start)})
                continue
            if not raw:
                break
            zeile = raw.decode("utf-8", "replace").strip()
            if not zeile.startswith("{"):
                if zeile:
                    yield _sse({"type": "notice", "message": _kurz(zeile, 300)})
                continue
            try:
                d = json.loads(zeile)
            except Exception:
                continue

            t = d.get("type")
            if t == "session":
                yield _sse({"type": "pi_session", "id": d.get("id", ""),
                            "cwd": d.get("cwd", "")})
                continue

            # Token laufend mitnehmen (letzter Stand gewinnt)
            u = d.get("usage") or (d.get("message") or {}).get("usage") or {}
            if u:
                tok["in"] = int(u.get("input", tok["in"]) or 0)
                tok["out"] = int(u.get("output", tok["out"]) or 0)

            ev = d.get("assistantMessageEvent") or {}
            et = str(ev.get("type") or "")

            if et == "text_delta":
                offen_text = True
                yield _sse({"type": "text", "content": ev.get("delta", "")})
            elif et == "thinking_delta":
                yield _sse({"type": "thinking", "content": ev.get("delta", "")})
            elif et == "thinking_end":
                yield _sse({"type": "thinking_end"})
            elif t == "tool_execution_start":
                # Die verlässliche Quelle für Name UND Argumente. Die
                # assistantMessageEvent-Varianten `toolcall_start/_delta/_end`
                # werden bewusst ignoriert: dort steht der Name nur im ersten
                # Frame und die Argumente nur im zweiten — in der Oberfläche
                # entstünden daraus mehrere namenlose Blöcke.
                yield _sse({
                    "type": "pi_tool", "event": "start",
                    "id": str(d.get("toolCallId") or ""),
                    "name": str(d.get("toolName") or "Werkzeug"),
                    "input": _kurz(d.get("args") or ""),
                })
            elif t == "turn_end":
                for tr in (d.get("toolResults") or []):
                    yield _sse({
                        "type": "pi_tool", "event": "result",
                        "id": str(tr.get("toolCallId") or ""),
                        "name": str(tr.get("toolName") or "Werkzeug"),
                        "fehler": bool(tr.get("isError")),
                        "output": _kurz(_inhalt(tr.get("content"))),
                    })
            elif t == "error":
                yield _sse({"type": "error", "content": _kurz(d.get("message") or d, 400)})

        rc = await proc.wait() if proc.returncode is None else proc.returncode
        if rc not in (0, None):
            fehler = ""
            try:
                fehler = (await proc.stderr.read()).decode("utf-8", "replace")
            except Exception:
                pass
            if not offen_text:
                yield _sse({"type": "error", "content":
                            f"Pi endete mit Code {rc}. {_kurz(fehler, 400)}"})
            else:
                yield _sse({"type": "notice",
                            "message": f"Pi endete mit Code {rc}."})
        yield _sse({"type": "done", "tokens": tok})

    except asyncio.CancelledError:
        raise
    except Exception as e:
        yield _sse({"type": "error", "content": f"{type(e).__name__}: {e}"})
    finally:
        # Abbruch im Browser (Stopp-Knopf) => Unterprozess beenden, nicht
        # verwaist weiterlaufen lassen.
        if proc is not None and proc.returncode is None:
            try:
                proc.kill()
            except Exception:
                pass


@router.post("/api/pi/run")
async def pi_run(req: Request):
    """Einen Pi-Lauf starten und als SSE streamen."""
    body = await req.json()
    prompt = str(body.get("prompt", "") or "").strip()
    if not prompt:
        raise HTTPException(400, "Es fehlt die Aufgabe (prompt).")
    prof = _load_profile()
    provider = str(body.get("provider", "") or prof.get("pi_provider", "") or "").strip()
    model = str(body.get("model", "") or prof.get("pi_model", "") or "").strip()
    thinking = str(body.get("thinking", "") or "").strip().lower()
    # Gesprächs-ID aus dem Tab. Nur harmlose Zeichen zulassen — der Wert wird als
    # Dateiname und als Prozessargument benutzt.
    session_id = re.sub(r"[^A-Za-z0-9_-]", "", str(body.get("session_id", "") or ""))[:64]
    return StreamingResponse(
        _pi_run_generator(prompt, provider, model, thinking, session_id),
        media_type="text/event-stream",
    )
