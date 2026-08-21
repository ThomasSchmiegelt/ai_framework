"""Vergleich zweier Tabellenblätter (Excel/CSV) — reine Logik ohne FastAPI/DB.

Analog zu ``tools/decision.py``: nur Standardbibliothek, MIT. Nimmt zwei bereits
mit ``tools.files.read_table`` gelesene Blätter (Kopfzeilen + Zeilen) und einen
**Schlüsselspalten-Index** je Seite und baut einen deterministischen Zeilen-Diff:

- Zeilen, deren Schlüssel **nur in A** bzw. **nur in B** vorkommt,
- **geänderte** Zeilen (gleicher Schlüssel, aber abweichende Zellen), spaltenweise
  über die **Header-Namen** gepaart,
- Spalten, die es nur auf einer Seite gibt.

Die eigentliche inhaltliche Bewertung („viel Text") macht danach das LLM in
``main.py``; hier entstehen nur die strukturierten Fakten.
"""

from __future__ import annotations

from typing import Optional


def _cell(v) -> str:
    """Zelle robust in einen getrimmten String wandeln (None → '')."""
    if v is None:
        return ""
    if isinstance(v, float) and v.is_integer():
        # openpyxl liefert Zahlen oft als float — 3.0 soll wie 3 aussehen
        return str(int(v))
    return str(v).strip()


def _key_index(headers: list, key, default: int = 0) -> int:
    """Schlüsselspalte auflösen: akzeptiert einen Index oder einen Header-Namen."""
    n = len(headers or [])
    if n == 0:
        return 0
    if isinstance(key, int):
        return key if 0 <= key < n else (default if 0 <= default < n else 0)
    # String: erst als Header-Name, sonst als Zahl interpretieren
    name = str(key).strip()
    for i, h in enumerate(headers):
        if _cell(h) == name:
            return i
    try:
        idx = int(name)
        if 0 <= idx < n:
            return idx
    except (TypeError, ValueError):
        pass
    return default if 0 <= default < n else 0


def _row_map(headers: list, rows: list, key_idx: int) -> dict:
    """Schlüssel → letzte Zeile mit diesem Schlüssel (dict Header→Wert)."""
    out: dict = {}
    for row in rows or []:
        key = _cell(row[key_idx]) if key_idx < len(row) else ""
        if not key:
            continue
        d = {}
        for ci, h in enumerate(headers or []):
            d[_cell(h) or f"Spalte {ci + 1}"] = _cell(row[ci]) if ci < len(row) else ""
        out[key] = d
    return out


def diff_tables(headers_a: list, rows_a: list, key_a,
                headers_b: list, rows_b: list, key_b,
                max_items: int = 2000) -> dict:
    """Deterministischer Diff zweier Blätter über eine Schlüsselspalte.

    headers_a/b  – Kopfzeilen (Liste von Namen)
    rows_a/b     – Datenzeilen (Liste von Zell-Listen)
    key_a/b      – Schlüsselspalte je Seite (Index oder Header-Name)
    max_items    – Kappung je Ergebnisliste (Zähler bleiben vollständig)

    Rückgabe siehe ``counts`` + Listen ``only_in_a``/``only_in_b``/``changed``.
    """
    ki_a = _key_index(headers_a, key_a)
    ki_b = _key_index(headers_b, key_b)

    names_a = [_cell(h) or f"Spalte {i + 1}" for i, h in enumerate(headers_a or [])]
    names_b = [_cell(h) or f"Spalte {i + 1}" for i, h in enumerate(headers_b or [])]
    set_a, set_b = set(names_a), set(names_b)
    columns_only_a = [n for n in names_a if n not in set_b]
    columns_only_b = [n for n in names_b if n not in set_a]
    common_cols = [n for n in names_a if n in set_b]

    map_a = _row_map(headers_a, rows_a, ki_a)
    map_b = _row_map(headers_b, rows_b, ki_b)
    keys_a, keys_b = set(map_a), set(map_b)

    only_a_keys = [k for k in map_a if k not in keys_b]
    only_b_keys = [k for k in map_b if k not in keys_a]
    common_keys = [k for k in map_a if k in keys_b]

    only_in_a = [{"key": k, "row": map_a[k]} for k in only_a_keys[:max_items]]
    only_in_b = [{"key": k, "row": map_b[k]} for k in only_b_keys[:max_items]]

    changed = []
    changed_count = 0
    for k in common_keys:
        ra, rb = map_a[k], map_b[k]
        diffs = []
        for col in common_cols:
            va, vb = ra.get(col, ""), rb.get(col, "")
            if va != vb:
                diffs.append({"column": col, "a": va, "b": vb})
        if diffs:
            changed_count += 1
            if len(changed) < max_items:
                changed.append({"key": k, "changes": diffs})

    return {
        "key_col_a": names_a[ki_a] if ki_a < len(names_a) else "",
        "key_col_b": names_b[ki_b] if ki_b < len(names_b) else "",
        "columns_only_a": columns_only_a,
        "columns_only_b": columns_only_b,
        "common_columns": common_cols,
        "only_in_a": only_in_a,
        "only_in_b": only_in_b,
        "changed": changed,
        "counts": {
            "rows_a": len(map_a),
            "rows_b": len(map_b),
            "only_a": len(only_a_keys),
            "only_b": len(only_b_keys),
            "common": len(common_keys),
            "changed": changed_count,
        },
    }


def diff_summary_text(diff: dict, max_lines: int = 120) -> str:
    """Kompakte Text-Zusammenfassung des Diffs als LLM-Kontext (gekürzt)."""
    c = diff.get("counts", {})
    lines = [
        f"Zeilen A: {c.get('rows_a', 0)}, Zeilen B: {c.get('rows_b', 0)}",
        f"Nur in A: {c.get('only_a', 0)}, Nur in B: {c.get('only_b', 0)}, "
        f"Gemeinsam: {c.get('common', 0)}, Geändert: {c.get('changed', 0)}",
    ]
    if diff.get("columns_only_a"):
        lines.append("Spalten nur in A: " + ", ".join(diff["columns_only_a"]))
    if diff.get("columns_only_b"):
        lines.append("Spalten nur in B: " + ", ".join(diff["columns_only_b"]))
    lines.append("")
    for item in diff.get("changed", []):
        if len(lines) >= max_lines:
            lines.append("… (weitere Änderungen gekürzt)")
            break
        parts = "; ".join(
            f"{ch['column']}: '{ch['a']}' → '{ch['b']}'" for ch in item["changes"][:12]
        )
        lines.append(f"[{item['key']}] {parts}")
    for k in ("only_in_a", "only_in_b"):
        for item in diff.get(k, []):
            if len(lines) >= max_lines:
                break
            tag = "NUR A" if k == "only_in_a" else "NUR B"
            vals = "; ".join(f"{kk}={vv}" for kk, vv in list(item["row"].items())[:8])
            lines.append(f"[{tag} {item['key']}] {vals}")
    return "\n".join(lines)
