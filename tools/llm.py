"""
LLM-Abstraktion — vereinheitlicht lokale (Ollama) und entfernte (OpenAI-kompatible)
Chat-Aufrufe für AI_Framework_Thomas.

Hintergrund: Bisher ging jeder LLM-Aufruf direkt an Ollama
(``{OLLAMA_BASE}/api/chat``). Damit zusätzlich externe, OpenAI-kompatible Anbieter
(OpenRouter, OpenAI, Groq, Together …) nutzbar werden, kapselt dieses Modul den
Aufruf. **Die Rückgabeform bleibt immer Ollama-förmig** (``{"message": {"content",
"tool_calls", "thinking"}}`` bzw. Stream-Chunks ``{"message": {"content"}, "done"}``),
sodass die ~30 bestehenden Aufrufstellen in ``main.py`` nahezu unverändert bleiben:
nur ``client.post(.../api/chat, json=payload)`` → ``await chat(client, payload)`` und
``client.stream(...)`` → ``async for chunk in stream(client, payload)``.

Remote-Modelle werden am Präfix ``"<provider_id>::<model>"`` erkannt (lokale
Ollama-Namen enthalten nie ``::``). Anbieter liegen in ``data/api_providers.json``
(API-Keys; gitignored, nicht im Backup). Nur ``httpx`` als Abhängigkeit (vorhanden),
MIT-kompatibel.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import AsyncIterator, Optional

import httpx

# Von main.py beim Start gesetzt (set_config). Defaults nur als Fallback.
OLLAMA_BASE: str = "http://localhost:11434"
PROVIDERS_FILE: Optional[Path] = None

_REMOTE_SEP = "::"


def set_config(ollama_base: str, providers_file: Path) -> None:
    """Wird einmal beim App-Start aus main.py aufgerufen."""
    global OLLAMA_BASE, PROVIDERS_FILE
    OLLAMA_BASE = ollama_base
    PROVIDERS_FILE = providers_file


def load_providers() -> list:
    """Liest die Anbieter-Liste (jeder Aufruf frisch → erlaubt Live-Änderungen)."""
    if not PROVIDERS_FILE or not PROVIDERS_FILE.exists():
        return []
    try:
        data = json.loads(PROVIDERS_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception:
        return []


def is_remote(model: str) -> bool:
    return bool(model) and _REMOTE_SEP in model


def resolve(model: str):
    """Gibt ``(provider_cfg, real_model)`` zurück; ``(None, model)`` für lokal."""
    if not is_remote(model):
        return None, model
    pid, _, real = model.partition(_REMOTE_SEP)
    for p in load_providers():
        if p.get("id") == pid:
            return p, real
    # Präfix vorhanden, aber Anbieter unbekannt → als lokal behandeln (Fallback)
    return None, real


# ── Übersetzung Ollama-Payload → OpenAI-Request ─────────────────────────────────

def _to_openai_payload(payload: dict, real_model: str, stream: bool) -> dict:
    out = {"model": real_model, "messages": _to_openai_messages(payload.get("messages", [])),
           "stream": stream}
    opts = payload.get("options") or {}
    if "temperature" in opts:
        out["temperature"] = opts["temperature"]
    # Ollama format:"json" → OpenAI response_format
    if payload.get("format") == "json":
        out["response_format"] = {"type": "json_object"}
    tools = payload.get("tools")
    if tools:
        out["tools"] = tools   # Ollama-Tooldefinitionen sind OpenAI-kompatibel
    # 'think' gibt es bei OpenAI nicht → weglassen
    return out


def _to_openai_messages(messages: list) -> list:
    """Ollama-Nachrichten → OpenAI. Bilder (Ollama ``images``: [b64]) werden in den
    OpenAI-Multimodal-Content (``image_url`` mit data-URI) übersetzt."""
    out = []
    for m in messages:
        imgs = m.get("images")
        if imgs and m.get("role") == "user":
            content = []
            if m.get("content"):
                content.append({"type": "text", "text": m["content"]})
            for b64 in imgs:
                uri = b64 if str(b64).startswith("data:") else f"data:image/png;base64,{b64}"
                content.append({"type": "image_url", "image_url": {"url": uri}})
            out.append({"role": m.get("role", "user"), "content": content})
        else:
            msg = {"role": m.get("role", "user"), "content": m.get("content", "")}
            # Tool-Rollen/-Antworten unverändert durchreichen, falls vorhanden
            for k in ("tool_calls", "tool_call_id", "name"):
                if k in m:
                    msg[k] = m[k]
            out.append(msg)
    return out


# ── Übersetzung OpenAI-Antwort → Ollama-Form ────────────────────────────────────

def _openai_msg_to_ollama(choice_msg: dict) -> dict:
    msg = {"content": choice_msg.get("content") or ""}
    tcs = choice_msg.get("tool_calls")
    if tcs:
        norm = []
        for tc in tcs:
            fn = (tc or {}).get("function") or {}
            args = fn.get("arguments")
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except Exception:
                    args = {}
            norm.append({"function": {"name": fn.get("name", ""), "arguments": args or {}}})
        msg["tool_calls"] = norm
    # OpenAI-Reasoning-Feld (falls vorhanden) auf Ollamas 'thinking' mappen
    if choice_msg.get("reasoning"):
        msg["thinking"] = choice_msg["reasoning"]
    return msg


def _remote_headers(provider: dict) -> dict:
    h = {"Authorization": f"Bearer {provider.get('api_key', '')}",
         "Content-Type": "application/json"}
    # OpenRouter empfiehlt diese Header (optional, schaden anderen Anbietern nicht)
    h["HTTP-Referer"] = "https://localhost"
    h["X-Title"] = "LOCAL AI"
    return h


def _base(provider: dict) -> str:
    return (provider.get("base_url") or "").rstrip("/")


def _safe_json(resp: httpx.Response) -> dict:
    try:
        return resp.json()
    except Exception:
        return {"error": resp.text[:500]}


class LLMResponse(dict):
    """Ollama-förmige Antwort, die sich wie ein ``httpx.Response`` für die wenigen
    von den Aufrufstellen genutzten Methoden verhält (``json()``,
    ``raise_for_status()``, ``status_code``). So bleiben die ~30 Aufrufstellen nahezu
    unverändert: ``resp = await client.post(.../api/chat, json=…)`` →
    ``resp = await _llm.chat(client, …)`` — der nachfolgende Code (``resp.json()`` /
    ``resp.raise_for_status()`` / ``resp.get("message")``) funktioniert weiter."""

    def __init__(self, data: dict, status_code: int = 200):
        super().__init__(data or {})
        self._status = status_code

    @property
    def status_code(self) -> int:
        return self._status

    def json(self) -> dict:
        return dict(self)

    def raise_for_status(self):
        if self._status >= 400:
            # Ein echtes ``httpx.Response`` mitgeben, damit Aufrufstellen wie
            # ``except httpx.HTTPStatusError as e: … e.response.status_code`` NICHT
            # an ``NoneType`` scheitern (z. B. Remote-Anbieter liefert 504 → früher
            # AttributeError im Fehler-Handler statt sauberer Fehlermeldung).
            _req = httpx.Request("POST", "http://llm/local")
            _resp = httpx.Response(self._status, request=_req,
                                   text=str(self.get("error", "") or ""))
            raise httpx.HTTPStatusError(
                f"LLM HTTP {self._status}: {self.get('error', '')}",
                request=_req, response=_resp)
        return None


# ── Öffentliche API ─────────────────────────────────────────────────────────────

async def chat(client: httpx.AsyncClient, payload: dict) -> "LLMResponse":
    """Nicht-streamender Chat. Nimmt einen **Ollama-Payload** entgegen und liefert
    eine **Ollama-förmige** :class:`LLMResponse`. Routet je nach Modell an Ollama
    oder einen Remote-Anbieter (OpenAI-kompatibel). Der ``status_code`` bleibt
    erhalten, sodass die Aufrufstellen ihr bisheriges Fehler-/Retry-Verhalten
    (z. B. die ``think``-400-Wiederholung der Chat-Schleife) behalten."""
    model = payload.get("model", "")
    provider, real = resolve(model)

    if provider is None:
        # Lokal (Ollama)
        body = dict(payload)
        body["model"] = real
        body["stream"] = False
        resp = await client.post(f"{OLLAMA_BASE}/api/chat", json=body)
        return LLMResponse(_safe_json(resp), resp.status_code)

    # Remote (OpenAI-kompatibel)
    oai = _to_openai_payload(payload, real, stream=False)
    resp = await client.post(f"{_base(provider)}/chat/completions",
                             json=oai, headers=_remote_headers(provider))
    if resp.status_code >= 400:
        return LLMResponse({"message": {"content": ""}, "error": resp.text[:500]},
                           resp.status_code)
    data = _safe_json(resp)
    choices = data.get("choices") or [{}]
    out = {"message": _openai_msg_to_ollama(choices[0].get("message") or {})}
    # Token-Verbrauch (OpenAI ``usage``) auf Ollama-Felder abbilden, damit der
    # Token-Zähler im Backend Anbieter-unabhängig funktioniert.
    usage = data.get("usage") or {}
    if usage:
        out["prompt_eval_count"] = int(usage.get("prompt_tokens") or 0)
        out["eval_count"] = int(usage.get("completion_tokens") or 0)
    return LLMResponse(out, 200)


async def stream(client: httpx.AsyncClient, payload: dict) -> AsyncIterator[dict]:
    """Streamender Chat. Liefert **Ollama-förmige** Chunk-Dicts
    (``{"message": {"content": …}, "done": bool}``)."""
    model = payload.get("model", "")
    provider, real = resolve(model)

    if provider is None:
        body = dict(payload)
        body["model"] = real
        body["stream"] = True
        async with client.stream("POST", f"{OLLAMA_BASE}/api/chat", json=body) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if not line:
                    continue
                try:
                    yield json.loads(line)
                except Exception:
                    continue
        return

    # Remote
    oai = _to_openai_payload(payload, real, stream=True)
    async with client.stream("POST", f"{_base(provider)}/chat/completions",
                             json=oai, headers=_remote_headers(provider)) as resp:
        resp.raise_for_status()
        async for line in resp.aiter_lines():
            if not line or not line.startswith("data:"):
                continue
            chunk = line[len("data:"):].strip()
            if chunk == "[DONE]":
                yield {"done": True}
                return
            try:
                data = json.loads(chunk)
            except Exception:
                continue
            delta = ((data.get("choices") or [{}])[0].get("delta") or {})
            token = delta.get("content") or ""
            done = bool((data.get("choices") or [{}])[0].get("finish_reason"))
            yield {"message": {"content": token}, "done": done}
    # Sicherheitshalber ein finales done senden
    yield {"done": True}


async def list_remote_models() -> list:
    """Alle konfigurierten Remote-Modelle als präfigierte Einträge
    ``{name:"<id>::<model>", remote:True, provider:<name>}`` (für /api/models)."""
    out = []
    for p in load_providers():
        pid = p.get("id", "")
        pname = p.get("name", pid)
        for mdl in (p.get("models") or []):
            out.append({"name": f"{pid}{_REMOTE_SEP}{mdl}", "remote": True,
                        "provider": pname})
    return out


async def embed(texts: list, model: str, timeout: float = 600.0) -> list:
    """Erzeugt Embeddings über einen externen OpenAI-kompatiblen Anbieter.

    ``model`` ist ein präfigierter Remote-Name (``<provider_id>::<modell>``).
    Rückgabe: Liste von Vektoren in der Reihenfolge der Eingabetexte — also
    dieselbe Form wie ``tools.rag.embed`` sie von Ollama erhält.
    """
    provider, real_model = resolve(model)
    if provider is None:
        raise RuntimeError(f"Kein externer Anbieter für Embedding-Modell '{model}' gefunden.")
    if not texts:
        return []
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(
            f"{_base(provider)}/embeddings",
            headers=_remote_headers(provider),
            json={"model": real_model, "input": texts},
        )
    if resp.status_code >= 400:
        data = _safe_json(resp)
        msg = (data.get("error") or {}).get("message") if isinstance(data.get("error"), dict) else data.get("error")
        raise RuntimeError(
            f"Embedding-Anbieter '{provider.get('name', provider.get('id'))}' meldet "
            f"HTTP {resp.status_code}: {msg or str(data)[:200]}")
    data = resp.json()
    items = data.get("data") or []
    # Reihenfolge über 'index' absichern (manche Anbieter liefern unsortiert)
    try:
        items = sorted(items, key=lambda d: int(d.get("index", 0)))
    except Exception:
        pass
    return [it.get("embedding") for it in items if it.get("embedding") is not None]


async def fetch_provider_models(provider: dict, timeout: float = 15.0) -> list:
    """Holt die Modell-Liste eines Anbieters (GET {base}/models). Für „Verbindung
    testen" / automatisches Befüllen. Gibt eine Liste von Modellnamen zurück."""
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.get(f"{_base(provider)}/models",
                                headers=_remote_headers(provider))
        resp.raise_for_status()
        data = resp.json()
    items = data.get("data") if isinstance(data, dict) else data
    names = []
    for it in (items or []):
        if isinstance(it, dict) and it.get("id"):
            names.append(it["id"])
        elif isinstance(it, str):
            names.append(it)
    return names
