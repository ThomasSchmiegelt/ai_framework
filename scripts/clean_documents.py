"""Bereinigungsskript für RAG-Dokumente (Vorab-Prüfung).

Extrahiert Text aus einer Datei oder einem Ordner (PDF/DOCX/XLSX/TXT/MD/CSV)
und wendet dieselbe Bereinigung an, die die RAG-Ingestion bei aktivierter Option
verwendet (tools.rag.clean_text). Schreibt je Quelle eine ``<name>.clean.txt``,
damit man das Ergebnis vor dem Hochladen begutachten kann.

Aufruf (aus dem Projektstamm, mit aktivierter venv):
    set PYTHONIOENCODING=utf-8
    python scripts/clean_documents.py <pfad-zu-datei-oder-ordner> [-o ausgabeordner]
"""

import argparse
import sys
from pathlib import Path

# Projektstamm importierbar machen (Skript liegt in scripts/)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.files import extract       # noqa: E402
from tools.rag import clean_text      # noqa: E402

_EXTS = {".pdf", ".docx", ".doc", ".xlsx", ".xls", ".csv", ".txt", ".md"}


def _process(fp: Path, out_dir: Path) -> None:
    try:
        raw = extract(fp)
    except Exception as e:
        print(f"  ✗ {fp.name}: Extraktion fehlgeschlagen ({e})")
        return
    cleaned = clean_text(raw)
    out = out_dir / (fp.stem + ".clean.txt")
    out.write_text(cleaned, encoding="utf-8")
    print(f"  ✓ {fp.name} → {out.name}  ({len(raw)} → {len(cleaned)} Zeichen)")


def main() -> None:
    ap = argparse.ArgumentParser(description="RAG-Dokumente bereinigen (Vorschau).")
    ap.add_argument("path", help="Datei oder Ordner")
    ap.add_argument("-o", "--out", help="Ausgabeordner (Standard: neben der Quelle)")
    args = ap.parse_args()

    src = Path(args.path)
    if not src.exists():
        print(f"Pfad nicht gefunden: {src}")
        sys.exit(1)

    files = [src] if src.is_file() else [p for p in sorted(src.rglob("*")) if p.suffix.lower() in _EXTS]
    if not files:
        print("Keine unterstützten Dokumente gefunden.")
        sys.exit(1)

    out_dir = Path(args.out) if args.out else (src.parent if src.is_file() else src)
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"Bereinige {len(files)} Datei(en) → {out_dir}")
    for fp in files:
        _process(fp, out_dir)
    print("Fertig.")


if __name__ == "__main__":
    main()
