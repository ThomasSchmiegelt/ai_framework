"""
Websuche für AI_Framework_Thomas via DuckDuckGo (Bibliothek ``ddgs``, kein API-Key nötig).

- :func:`search` — Volltext-Suche, liefert formatierten Text fürs LLM
- :func:`search_with_sources` — zusätzlich Quellenliste (Titel, URL, Snippet)
  für die Recherche-Ansicht
- :func:`search_news` — Nachrichten-Suche mit Datum

Alle Funktionen sind async und geben bei Fehlern einen Fehlertext statt einer
Exception zurück, damit der Agentic-Loop robust weiterläuft.
"""
from ddgs import DDGS


async def search(query: str, num_results: int = 5) -> str:
    _, text = await search_with_sources(query, num_results)
    return text


async def search_with_sources(query: str, num_results: int = 5) -> tuple[list[dict], str]:
    """Returns (sources_list, formatted_text). sources_list: [{title, url, body}]"""
    try:
        with DDGS() as ddgs:
            results = list(ddgs.text(query, max_results=num_results))

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
        with DDGS() as ddgs:
            results = list(ddgs.news(query, max_results=num_results))

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
