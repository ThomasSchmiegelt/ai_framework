"""
Websuche für AI_Framework_Thomas via DuckDuckGo (Bibliothek ``ddgs``, kein API-Key nötig).

- :func:`search` — Volltext-Suche, liefert formatierten Text fürs LLM
- :func:`search_with_sources` — zusätzlich Quellenliste (Titel, URL, Snippet)
  für die Recherche-Ansicht
- :func:`search_news` — Nachrichten-Suche mit Datum

Alle Funktionen sind async und geben bei Fehlern einen Fehlertext statt einer
Exception zurück, damit der Agentic-Loop robust weiterläuft.

**Drosselung (wichtig bei externen APIs):** DuckDuckGo drosselt zu dichte
Anfragen. Bei lokalen Modellen entzerrt der VRAM-Guard die Aufrufe von selbst;
mit einer schnellen externen API (Matrix-Recherche, Partner-Auswertung) feuern
die Suchen dagegen kurz hintereinander und werden geblockt. Deshalb serialisiert
:func:`_ddgs_call` alle Suchen über ein globales Lock, hält eine Mindestpause ein
und wiederholt bei Ratelimit-Fehlern mit wachsender Wartezeit. Die (synchrone)
ddgs-Bibliothek läuft in einem Thread, um den Event-Loop nicht zu blockieren.
"""
import asyncio
import time

from ddgs import DDGS

# Serialisiert alle ddgs-Aufrufe prozessweit + Mindestabstand (Sekunden) zwischen
# zwei Suchen. Verhindert das „zu schnell an ddgs"-Problem bei externen APIs.
# 5 s statt 3 s: schnelle API-Modelle (Recherche/Matrix) feuern Suchen sonst so
# dicht, dass DuckDuckGo trotz Backoff dauerhaft blockt.
_DDGS_LOCK = asyncio.Lock()
_DDGS_MIN_INTERVAL = 5.0
_DDGS_MAX_RETRIES = 4
_last_call_at = 0.0


def _is_ratelimit(err: Exception) -> bool:
    s = str(err).lower()
    return any(k in s for k in ("ratelimit", "rate limit", "202", "429", "too many"))


async def _ddgs_call(kind: str, query: str, num_results: int) -> list[dict]:
    """Führt eine ddgs-Suche gedrosselt + mit Backoff aus. ``kind`` = 'text'|'news'.
    Gibt die rohe Ergebnisliste zurück; wirft nur, wenn alle Versuche scheitern."""
    global _last_call_at

    def _run() -> list[dict]:
        with DDGS() as ddgs:
            gen = ddgs.news if kind == "news" else ddgs.text
            return list(gen(query, max_results=num_results))

    async with _DDGS_LOCK:
        last_err: Exception | None = None
        for attempt in range(_DDGS_MAX_RETRIES):
            # Mindestpause seit dem letzten Aufruf einhalten
            wait = _DDGS_MIN_INTERVAL - (time.monotonic() - _last_call_at)
            if wait > 0:
                await asyncio.sleep(wait)
            try:
                results = await asyncio.to_thread(_run)
                _last_call_at = time.monotonic()
                return results
            except Exception as e:  # noqa: BLE001 — Backoff nur bei Ratelimit
                last_err = e
                _last_call_at = time.monotonic()
                if _is_ratelimit(e) and attempt < _DDGS_MAX_RETRIES - 1:
                    await asyncio.sleep(_DDGS_MIN_INTERVAL * (attempt + 2))  # 10 s, 15 s, 20 s
                    continue
                raise
        if last_err:
            raise last_err
        return []


async def search(query: str, num_results: int = 5) -> str:
    _, text = await search_with_sources(query, num_results)
    return text


async def search_with_sources(query: str, num_results: int = 5) -> tuple[list[dict], str]:
    """Returns (sources_list, formatted_text). sources_list: [{title, url, body}]"""
    try:
        results = await _ddgs_call("text", query, num_results)

        if not results:
            return [], f"Keine Ergebnisse für: {query}"

        sources = [
            {"title": r.get("title", ""), "url": r.get("href", ""), "body": r.get("body", "")}
            for r in results
        ]

        lines = [f"Suchergebnisse für '{query}':\n"]
        for i, r in enumerate(results, 1):
            lines.append(f"{i}. **{r.get('title', '')}**")
            lines.append(f"   {r.get('href', '')}")
            lines.append(f"   {r.get('body', '')}\n")

        return sources, "\n".join(lines)

    except Exception as e:
        return [], f"Suchfehler: {e}"


async def search_news(query: str, num_results: int = 5) -> str:
    try:
        results = await _ddgs_call("news", query, num_results)

        if not results:
            return f"Keine News für: {query}"

        lines = [f"News für '{query}':\n"]
        for i, r in enumerate(results, 1):
            lines.append(f"{i}. **{r.get('title', '')}** ({r.get('date', '')})")
            lines.append(f"   {r.get('url', '')}")
            lines.append(f"   {r.get('body', '')}\n")

        return "\n".join(lines)

    except Exception as e:
        return f"News-Fehler: {e}"
