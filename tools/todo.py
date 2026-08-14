"""Datenmodell-Helfer für den Tab „To-Do" (KI-gestützte Aufgabenliste mit Wissensgraph).

Reine Logik ohne FastAPI/DB-Bezug (analog ``tools/dokumente.py``). Eine **Liste**
ist ein Container vom Typ Besprechung, Projekt oder frei; sie enthält
**Aufgaben** (items) mit Beteiligten, Status, Fälligkeit und Verknüpfungen
untereinander sowie die Kanten/Positionen des Wissensgraphen.

Persistiert wird das Ganze in ``main.py`` als ``list.json`` je Liste in der
Verzeichnisstruktur ``data/todo/<name>/`` — hier nur die reine Struktur-Logik
(Anlegen, Verknüpfen, Aufräumen). Kein LLM, keine Datei-/DB-Zugriffe.
"""

from __future__ import annotations

import time
import uuid
from typing import Optional

# Erlaubte Werte (Frontend nutzt dieselben Schlüssel)
STATUS = ("offen", "laeuft", "erledigt")
TYPES = ("besprechung", "projekt", "frei")
LINK_KINDS = ("verknuepft", "blockiert", "gehoert_zu", "folgt_auf")


def _clean_str(v, limit: int = 4000) -> str:
    return str(v if v is not None else "").strip()[:limit]


def _clean_list(v) -> list:
    if not isinstance(v, list):
        return []
    return [_clean_str(x, 200) for x in v if _clean_str(x, 200)]


def new_id() -> str:
    return "i" + uuid.uuid4().hex[:10]


def new_item(text: str, detail: str = "", assignees: Optional[list] = None,
             status: str = "offen", due: str = "", item_id: str = "") -> dict:
    """Ein Aufgaben-Item bauen (mit stabiler ID)."""
    st = status if status in STATUS else "offen"
    return {
        "id": item_id or new_id(),
        "text": _clean_str(text, 500),
        "detail": _clean_str(detail),
        "assignees": _clean_list(assignees),
        "status": st,
        "due": _clean_str(due, 40),
        "links": [],            # [{target: item_id, kind}]
        "created_at": time.time(),
    }


def link_items(items: list, source_id: str, target_id: str, kind: str = "verknuepft") -> bool:
    """Eine gerichtete Verknüpfung source→target eintragen (idempotent).

    Gibt True zurück, wenn eine neue Kante entstand. Selbstbezug und Verweise auf
    nicht existierende Items werden ignoriert."""
    if source_id == target_id:
        return False
    ids = {it.get("id") for it in items}
    if source_id not in ids or target_id not in ids:
        return False
    k = kind if kind in LINK_KINDS else "verknuepft"
    for it in items:
        if it.get("id") == source_id:
            links = it.setdefault("links", [])
            if any(l.get("target") == target_id for l in links):
                return False
            links.append({"target": target_id, "kind": k})
            return True
    return False


def prune_links(items: list) -> list:
    """Verwaiste Verknüpfungen (Ziel existiert nicht mehr) entfernen.

    Gibt die bereinigte Item-Liste zurück (in place bearbeitet)."""
    ids = {it.get("id") for it in items if it.get("id")}
    for it in items:
        links = it.get("links") or []
        it["links"] = [l for l in links
                       if l.get("target") in ids and l.get("target") != it.get("id")]
    return items


def sanitize_list(data: dict) -> dict:
    """Eine (aus JSON/Client geladene) Liste in eine konsistente Struktur bringen.

    Erzwingt Typ/Status-Whitelist, vergibt fehlende IDs, dedupliziert Item-IDs und
    entfernt verwaiste Verknüpfungen sowie Kanten auf gelöschte Items."""
    ltype = data.get("type")
    if ltype not in TYPES:
        ltype = "frei"

    raw_items = data.get("items") if isinstance(data.get("items"), list) else []
    items = []
    seen = set()
    for raw in raw_items:
        if not isinstance(raw, dict):
            continue
        it = new_item(
            raw.get("text", ""), raw.get("detail", ""), raw.get("assignees"),
            raw.get("status", "offen"), raw.get("due", ""),
            item_id=_clean_str(raw.get("id"), 40),
        )
        if it["id"] in seen:
            it["id"] = new_id()
        seen.add(it["id"])
        # Links später bereinigen (nach vollständigem ID-Satz)
        it["links"] = [
            {"target": _clean_str(l.get("target"), 40),
             "kind": (l.get("kind") if l.get("kind") in LINK_KINDS else "verknuepft")}
            for l in (raw.get("links") or []) if isinstance(l, dict)
        ]
        if raw.get("created_at"):
            it["created_at"] = raw["created_at"]
        items.append(it)
    prune_links(items)

    ids = {it["id"] for it in items}
    edges = []
    for e in (data.get("edges") if isinstance(data.get("edges"), list) else []):
        if not isinstance(e, dict):
            continue
        s, t = _clean_str(e.get("source"), 40), _clean_str(e.get("target"), 40)
        if s in ids and t in ids and s != t:
            edges.append({"source": s, "target": t, "label": _clean_str(e.get("label"), 120)})

    positions = {}
    for k, v in (data.get("positions") or {}).items():
        if k in ids and isinstance(v, dict) and "x" in v and "y" in v:
            try:
                positions[k] = {"x": float(v["x"]), "y": float(v["y"])}
            except (TypeError, ValueError):
                pass

    return {
        "type": ltype,
        "title": _clean_str(data.get("title"), 200),
        "date": _clean_str(data.get("date"), 40),
        "participants": _clean_list(data.get("participants")),
        "project_id": _clean_str(data.get("project_id"), 40),
        "items": items,
        "edges": edges,
        "positions": positions,
        "settings": data.get("settings") if isinstance(data.get("settings"), dict) else {},
        "updated_at": time.time(),
    }


def stats(data: dict) -> dict:
    """Kurzstatistik für die Listenübersicht."""
    items = data.get("items") or []
    by_status = {s: 0 for s in STATUS}
    for it in items:
        st = it.get("status") if it.get("status") in STATUS else "offen"
        by_status[st] += 1
    return {
        "total": len(items),
        "offen": by_status["offen"],
        "laeuft": by_status["laeuft"],
        "erledigt": by_status["erledigt"],
        "participants": len(data.get("participants") or []),
    }
