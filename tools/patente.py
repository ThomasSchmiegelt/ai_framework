"""Patent-Recherche: Google-Patents-Scraping, Fallakten-Verwaltung, 7-Stufen-
Analyse-Pipeline und Wissensgraph-Datenaufbau für den „⚖️ Patente"-Tab.

Portiert aus dem eigenständigen Streamlit-Tool ~/ai-project/patente (app.py).
Reine Logik, keine FastAPI-/DB-Importe — main.py macht HTTP-Plumbing +
Persistenz, analog zum Aufbau von tools/mailstore.py.

Hinweis: Google Patents hat keine offizielle öffentliche API — das Scraping der
HTML-/XHR-Endpunkte birgt ein ToS-Risiko, das unverändert aus dem Original-Tool
übernommen wird (kein neues Risiko durch die Portierung).
"""
from __future__ import annotations

import json
import re
import shutil
import urllib.parse
from datetime import datetime
from pathlib import Path
from typing import Awaitable, Callable, Optional

import httpx
from bs4 import BeautifulSoup

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
    "Accept-Language": "de-DE,de;q=0.9,en;q=0.8",
}

ChatFn = Callable[[str, str], Awaitable[str]]


def _clean_number(n: str) -> str:
    return (n or "").strip().replace("-", "").replace("/", "")


# ── Scraping (Google Patents) ────────────────────────────────────────────────────

def extract_ipc_classes(soup: BeautifulSoup) -> list:
    ipc_klassen = []
    tags = soup.find_all("span", {"itemprop": "Code"}) or soup.find_all("meta", {"scheme": "classification-ipcr"})
    for tag in tags:
        text = (tag.get("content") or tag.text or "").strip()
        m = re.match(r"[A-H]\d{2}[A-Z]\s*\d+/\d+", text)
        if m:
            ipc_klassen.append(m.group(0).replace(" ", ""))
    if not ipc_klassen:
        muster = re.findall(r"\b[A-H]\d{2}[A-Z]\s*\d+/\d+\b", soup.get_text())
        ipc_klassen = list(set(muster[:10]))
    return list(set(ipc_klassen))


def extract_assignees(soup: BeautifulSoup) -> list:
    rechteinhaber = []
    for tag in soup.find_all("dd", itemprop="assigneeOriginal"):
        name = tag.text.strip()
        if name:
            rechteinhaber.append(name)
    if not rechteinhaber:
        meta = soup.find("meta", {"name": "DC.contributor"})
        if meta and meta.get("content"):
            rechteinhaber.append(meta["content"])
    return list(set(r for r in rechteinhaber if r))


async def fetch_patent_details(client: httpx.AsyncClient, patent_id: str) -> dict:
    """Ein Patent von Google Patents scrapen. Bei Fehler: `{"patent_id","error"}`."""
    pid = _clean_number(patent_id)
    url = f"https://patents.google.com/patent/{pid}/en"
    try:
        resp = await client.get(url, headers=HEADERS, timeout=15)
        soup = BeautifulSoup(resp.text, "html.parser")

        titel_tag = soup.find("meta", {"name": "DC.title"})
        titel = titel_tag["content"] if titel_tag else "Unbekannter Titel"

        abstract_sec = soup.find("section", itemprop="abstract")
        abstract = abstract_sec.text.strip() if abstract_sec else "Keine Zusammenfassung gefunden."

        claims_sec = soup.find("section", itemprop="claims")
        claims = claims_sec.text.strip() if claims_sec else "Keine Ansprüche gefunden."

        zitate = []
        for row in soup.find_all("tr", itemprop="backwardReferences"):
            pub = row.find(itemprop="publicationNumber")
            if pub and pub.text:
                zitate.append(_clean_number(pub.text))
        zitate = list(dict.fromkeys(zitate))

        return {
            "patent_id": pid,
            "title": titel,
            "abstract": abstract,
            "claims": claims,
            "zitate": zitate,
            "ipc_klassen": extract_ipc_classes(soup),
            "rechteinhaber": extract_assignees(soup),
            "url": url,
            "scraped_at": datetime.now().isoformat(),
        }
    except Exception as e:
        return {"patent_id": pid, "error": str(e)}


async def search_patents(client: httpx.AsyncClient, term: str = "", assignee: str = "",
                          country: str = "", max_results: int = 20) -> list:
    """Stichwortsuche über die Google-Patents-XHR-Suche (keine offizielle API).

    Die XHR-Suche liefert ~10 Treffer je Seite; für höhere Trefferzahlen wird über
    den ``page``-Parameter geblättert, bis genug eindeutige Nummern zusammenkommen."""
    parts = []
    if term:
        parts.append(term)
    if assignee:
        parts.append(f"assignee:({assignee})")
    if country:
        parts.append(f"country:{country}")
    query = " ".join(parts)
    if not query:
        return []
    ziel = max(1, int(max_results))
    nummern: list[str] = []
    seen: set[str] = set()
    try:
        for page in range(0, 6):  # bis zu 6 Seiten (~60 Treffer) durchblättern
            if len(nummern) >= ziel:
                break
            inner = f"q={query}&num=10" + (f"&page={page}" if page else "")
            encoded = urllib.parse.quote(inner)
            xhr_url = f"https://patents.google.com/xhr/query?url={encoded}"
            resp = await client.get(xhr_url, headers=HEADERS, timeout=15)
            if resp.status_code != 200:
                break
            daten = resp.json()
            cluster = (daten.get("results") or {}).get("cluster") or []
            treffer = (cluster[0].get("result") if cluster else None) or []
            if not treffer:
                break
            neu = 0
            for eintrag in treffer:
                pid = (eintrag.get("patent") or {}).get("publication_number")
                if not pid:
                    continue
                cn = _clean_number(pid)
                if cn and cn not in seen:
                    seen.add(cn)
                    nummern.append(cn)
                    neu += 1
            if neu == 0:  # keine neuen Treffer mehr → Ende
                break
    except Exception:
        pass
    nummern = nummern[:ziel]
    out = []
    for n in nummern:
        try:
            details = await fetch_patent_details(client, n)
        except Exception:
            continue
        if "error" not in details:
            out.append(details)
    return out


# ── Fallakten-JSON (CRUD) ────────────────────────────────────────────────────────

def load_project(path: Path) -> list:
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return []
    return []


def save_project(path: Path, new_items: list) -> list:
    """Merged neue Datensätze in die bestehende Fallakte, dedupliziert nach patent_id."""
    bestand = load_project(path)
    bestand.extend(new_items)
    eindeutig = {doc["patent_id"]: doc for doc in bestand if doc.get("patent_id")}.values()
    result = list(eindeutig)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    return result


# ── Analysen (JSON + Markdown-Wiki) ──────────────────────────────────────────────

def save_analysis(analysen_dir: Path, analyse_typ: str, patent_ids: list, ergebnisse: dict) -> str:
    """Speichert JSON + formatiertes Markdown (Wissens-Wiki), gibt den Dateibasis-
    Namen (ohne Endung) zurück."""
    analysen_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    base = f"{analyse_typ}_{ts}"
    erstellt_am = datetime.now().isoformat()
    obj = {"analyse_typ": analyse_typ, "patent_ids": list(patent_ids),
           "erstellt_am": erstellt_am, "ergebnisse": ergebnisse}
    (analysen_dir / f"{base}.json").write_text(
        json.dumps(obj, indent=2, ensure_ascii=False), encoding="utf-8")

    links = ", ".join(f"[[{pid}]]" for pid in patent_ids)
    md = [f"# Analyse: {analyse_typ.replace('_', ' ').title()}",
          f"**Datum:** {erstellt_am[:10]}",
          f"**Behandelte Dokumente:** {links}", ""]
    for bereich, text in ergebnisse.items():
        if bereich in ("pruefung_bestanden", "pruefung_details"):
            continue
        md.append(f"## {str(bereich).capitalize()}\n{text}\n")
    (analysen_dir / f"{base}.md").write_text("\n".join(md), encoding="utf-8")
    return base


def load_analyses(analysen_dir: Path) -> list:
    if not analysen_dir.exists():
        return []
    out = []
    for f in sorted(analysen_dir.iterdir(), reverse=True):
        if f.suffix == ".json":
            try:
                obj = json.loads(f.read_text(encoding="utf-8"))
            except Exception:
                continue
            obj["datei_name"] = f.name
            out.append(obj)
    return out


def delete_analysis(analysen_dir: Path, datei_name: str) -> bool:
    safe = Path(datei_name).name  # keine Verzeichnis-Traversal
    jpath = analysen_dir / safe
    mpath = jpath.with_suffix(".md")
    if jpath.exists():
        jpath.unlink()
        if mpath.exists():
            mpath.unlink()
        return True
    return False


# ── 7-Stufen-Experten-Pipeline ────────────────────────────────────────────────────
# Prompts wörtlich aus dem Original übernommen (fachlich abgestimmte deutsche
# Rechts-/Technik-Formulierungen). `chat_haupt`/`chat_neben` sind vom Aufrufer
# injizierte async Closures `(system, user) -> antwort`, die intern
# _model_session/_llm.chat kapseln — main.py bleibt VRAM-Guard-only, dieses Modul
# weiß nichts von Ollama/HTTP.

_BRACKET_RULE = (
    "\n\nRegel für die Textausgabe: Setze jede erwähnte Patentnummer und jeden "
    "Unternehmensnamen in doppelte eckige Klammern, zum Beispiel [[US1234567]] "
    "oder [[Firma GmbH]]."
)


async def run_pipeline(chat_haupt: ChatFn, chat_neben: ChatFn, patent_texte: str,
                        on_progress: Optional[Callable[[str], None]] = None) -> dict:
    """Portiert `patent_analyse_pipeline` (7 Stufen inkl. Prüfschleife)."""
    def progress(msg: str):
        if on_progress:
            on_progress(msg)

    async def haupt(system: str, user: str) -> str:
        return await chat_haupt(system + _BRACKET_RULE, user)

    async def neben(system: str, user: str) -> str:
        return await chat_neben(system + _BRACKET_RULE, user)

    ergebnisse: dict = {}

    # 1) Technik + Prüfschleife (kleines Modell prüft, großes korrigiert)
    technik = await haupt(
        "Du bist ein technischer Gutachter. Analysiere das Datenmaterial ausführlich. "
        "Benenne Kernkomponenten, Funktionsweise und technische Besonderheiten.",
        patent_texte)

    progress("🔄 Starte Prüfschleife für technische Analyse...")
    bestanden = False
    pruefung_details = ""
    for runde in range(2):
        pruef = await neben(
            "Du bist ein Prüf-Agent. Vergleiche die technische Analyse mit dem Originalpatent. "
            "Suche nach Faktenfehlern oder Dingen, die die Analyse erfunden hat. Wenn die "
            "Analyse absolut korrekt und logisch ist, antworte NUR mit dem exakten Wort "
            "'FREIGABE'.",
            f"ORIGINALDATEN:\n{patent_texte}\n\nZU PRÜFENDE ANALYSE:\n{technik}")
        if "FREIGABE" in pruef.upper():
            bestanden = True
            pruefung_details = f"In Runde {runde + 1} fehlerfrei validiert."
            break
        progress(f"⚠️ Fehler gefunden. Starte Korrekturrunde {runde + 1}...")
        technik = await haupt(
            f"Deine vorherige technische Analyse enthielt Fehler. Korrigiere sie basierend "
            f"auf dieser Kritik des Prüfers:\nKritik: {pruef}\n\nBehebe alle genannten "
            f"Fehler und liefere eine saubere Analyse.",
            patent_texte)
    ergebnisse["technik"] = technik
    ergebnisse["pruefung_bestanden"] = bestanden
    ergebnisse["pruefung_details"] = pruefung_details

    # 2) Recht
    progress("⚖️ Bewerte rechtlichen Schutzumfang...")
    ergebnisse["recht"] = await haupt(
        f"Du bist Patentanwalt. Bewerte den juristischen Schutzumfang der Ansprüche "
        f"detailliert. Analysiere Stärken und Schwächen des Schutzbereichs.\n\n"
        f"Technik-Analyse: {technik}",
        patent_texte)

    # 3) Umgehung
    progress("🚧 Suche Design-around-Optionen...")
    ergebnisse["umgehung"] = await haupt(
        f"Du bist Spezialist für Patentumgehungen. Identifiziere Merkmale, die man "
        f"weglassen oder ändern kann, um den Schutzbereich zu verlassen.\n\n"
        f"Technik: {technik}\nRecht: {ergebnisse['recht']}",
        patent_texte)

    # 4) Innovation
    progress("💡 Entwickle Verbesserungsideen...")
    ergebnisse["innovation"] = await haupt(
        f"Du bist ein technischer Entwickler. Basierend auf der technischen Analyse, "
        f"entwickle 3 konkrete Ideen, wie man die beschriebene Technologie funktionell "
        f"weiterentwickeln und verbessern kann.\n\nTechnik: {technik}",
        patent_texte)

    # 5) Entwurf
    progress("📝 Formuliere Entwurf für Neuanmeldung...")
    ergebnisse["entwurf"] = await haupt(
        f"Du bist ein Formulierer für Schutzschriften. Erstelle einen Entwurf für eine neue "
        f"Anmeldung (Anspruch 1 und Beschreibung), die die Umgehungsvorschläge nutzt. Das "
        f"Ziel ist maximaler Schutz, ohne das Original zu verletzen.\n"
        f"Umgehungs-Strategie: {ergebnisse['umgehung']}\nTechnische Basis: {technik}",
        "")

    # 6) Kritik (kleines Modell — einfache Schwachstellenprüfung)
    progress("🛡️ Prüfe Entwurf auf Schwachstellen...")
    ergebnisse["kritik"] = await neben(
        f"Prüfe den neuen Entwurf auf technische Schwachstellen und das Risiko einer "
        f"Nachahmung.\n\nNeuer Entwurf: {ergebnisse['entwurf']}",
        "")

    # 7) Moderator
    progress("📊 Erstelle Abschlussbericht...")
    ergebnisse["moderator"] = await haupt(
        f"Fasse die Ergebnisse zusammen. Lohnt sich eine Neuanmeldung auf Basis des "
        f"Entwurfs?\nRechtliche Lage: {ergebnisse['recht']}\n"
        f"Neuer Entwurf: {ergebnisse['entwurf']}\nKritik: {ergebnisse['kritik']}",
        "")

    return ergebnisse


# ── Wissensgraph-Daten ────────────────────────────────────────────────────────

def build_graph_data(patente: list, show_ipc: bool = True, show_assignee: bool = True,
                      show_citations: bool = True, focus_assignee: Optional[str] = None) -> tuple:
    """Portiert `erstelle_graph_daten` — liefert (nodes, edges) für die Cytoscape-
    Darstellung im Frontend (kein Backend-HTML/pyvis)."""
    knoten: dict = {}
    kanten: list = []
    _seen_kanten: set = set()  # (von, zu) — verhindert parallele Doppelkanten

    def _add_kante(von: str, zu: str, typ: str) -> None:
        key = (von, zu)
        if key in _seen_kanten:
            return
        _seen_kanten.add(key)
        kanten.append({"von": von, "zu": zu, "typ": typ})

    for p in patente:
        pid = p.get("patent_id")
        if not pid:
            continue
        titel = (p.get("title") or pid)[:50]
        ipc_liste = p.get("ipc_klassen") or []
        rh_liste = p.get("rechteinhaber") or []
        zitate = p.get("zitate") or []

        if focus_assignee and focus_assignee != "Alle" and focus_assignee not in rh_liste:
            continue

        knoten[pid] = {"id": pid, "label": f"{pid}: {titel}", "typ": "patent"}

        if show_ipc:
            for ipc in ipc_liste:
                hauptklasse = ipc[:3]
                ipc_id = f"IPC_{hauptklasse}"
                knoten.setdefault(ipc_id, {"id": ipc_id, "label": hauptklasse, "typ": "ipc"})
                _add_kante(pid, ipc_id, "klassifiziert_als")

        if show_assignee:
            for rh in rh_liste:
                rh_id = f"RH_{rh[:30]}"
                knoten.setdefault(rh_id, {"id": rh_id, "label": rh[:30], "typ": "rechteinhaber"})
                _add_kante(pid, rh_id, "gehoert")

        if show_citations:
            for zitat in zitate:
                zitat_id = f"ZITAT_{zitat}"
                knoten.setdefault(zitat_id, {"id": zitat_id, "label": zitat, "typ": "zitat"})
                _add_kante(pid, zitat_id, "zitiert")

    return list(knoten.values()), kanten


# ── Migration bestehender Fallakten (Streamlit-Tool) ─────────────────────────────

def migrate_legacy_projects(source_dir: Path, dest_dir: Path) -> tuple:
    """Kopiert `patente.json` + `analysen/` je Unterordner unverändert nach
    `dest_dir`. Überspringt ChromaDB-interne Verzeichnisse. Gibt (migrated,
    skipped) zurück — ChromaDB selbst wird NICHT migriert, die Neu-Indizierung
    erfolgt über die RAG-Engine des Frameworks (Aufrufer-Verantwortung)."""
    migrated, skipped = [], []
    if not source_dir.exists():
        return migrated, skipped
    for entry in sorted(source_dir.iterdir()):
        if not entry.is_dir():
            continue
        if entry.name in ("chroma_db", "chroma_datenbank"):
            continue
        src_json = entry / "patente.json"
        if not src_json.exists():
            skipped.append(entry.name)
            continue
        dst_dir = dest_dir / entry.name
        dst_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src_json, dst_dir / "patente.json")
        src_analysen = entry / "analysen"
        if src_analysen.exists():
            dst_analysen = dst_dir / "analysen"
            dst_analysen.mkdir(parents=True, exist_ok=True)
            for f in src_analysen.iterdir():
                if f.is_file():
                    shutil.copy2(f, dst_analysen / f.name)
        migrated.append(entry.name)
    return migrated, skipped
