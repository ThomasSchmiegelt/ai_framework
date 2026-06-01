"""
Datei-Extraktion für AI_Framework_Thomas — wandelt hochgeladene Dokumente in Text um.

Unterstützt PDF (pypdf), DOCX (python-docx), XLSX/CSV (openpyxl) sowie
Klartextformate. Bilder werden nicht hier verarbeitet, sondern im Backend
Base64-kodiert direkt an multimodale Modelle übergeben.

Einstiegspunkt: :func:`extract` — wählt anhand der Dateiendung den passenden
Parser und gibt den extrahierten Text zurück.
"""
from pathlib import Path


def extract(fp: Path) -> str:
    suffix = fp.suffix.lower()

    if suffix == ".pdf":
        return _pdf(fp)
    elif suffix in (".docx", ".doc"):
        return _docx(fp)
    elif suffix in (".xlsx", ".xls", ".csv"):
        return _spreadsheet(fp)
    elif suffix in (".txt", ".md", ".py", ".js", ".json", ".yaml", ".yml", ".html", ".css"):
        return fp.read_text(errors="replace")
    elif suffix in (".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"):
        return f"[Bild: {fp.name} — wird direkt an Bildmodell gesendet]"
    else:
        try:
            return fp.read_text(errors="replace")
        except Exception as e:
            return f"[Kann Datei nicht lesen: {e}]"


def _pdf(fp: Path) -> str:
    try:
        from pypdf import PdfReader
        reader = PdfReader(str(fp))
        pages = []
        for i, page in enumerate(reader.pages):
            text = page.extract_text() or ""
            if text.strip():
                pages.append(f"--- Seite {i+1} ---\n{text}")
        return "\n\n".join(pages) if pages else "[PDF ohne lesbaren Text (möglicherweise gescannt)]"
    except ImportError:
        return "[pypdf nicht installiert]"
    except Exception as e:
        return f"[PDF-Fehler: {e}]"


def _docx(fp: Path) -> str:
    try:
        from docx import Document
        doc = Document(str(fp))
        paragraphs = [p.text for p in doc.paragraphs if p.text.strip()]
        tables = []
        for table in doc.tables:
            rows = []
            for row in table.rows:
                rows.append(" | ".join(cell.text for cell in row.cells))
            tables.append("\n".join(rows))
        content = "\n\n".join(paragraphs)
        if tables:
            content += "\n\n[Tabellen]\n" + "\n\n".join(tables)
        return content or "[Leeres Dokument]"
    except ImportError:
        return "[python-docx nicht installiert]"
    except Exception as e:
        return f"[DOCX-Fehler: {e}]"


def _spreadsheet(fp: Path) -> str:
    suffix = fp.suffix.lower()
    try:
        if suffix == ".csv":
            return fp.read_text(errors="replace")

        import openpyxl
        wb = openpyxl.load_workbook(str(fp), read_only=True, data_only=True)
        parts = []
        for sheet_name in wb.sheetnames:
            ws = wb[sheet_name]
            rows = []
            for row in ws.iter_rows(values_only=True, max_row=200):
                row_str = " | ".join(str(c) if c is not None else "" for c in row)
                if row_str.strip(" |"):
                    rows.append(row_str)
            if rows:
                parts.append(f"[Blatt: {sheet_name}]\n" + "\n".join(rows))
        return "\n\n".join(parts) or "[Leere Tabelle]"
    except ImportError:
        return "[openpyxl nicht installiert]"
    except Exception as e:
        return f"[Tabellen-Fehler: {e}]"
