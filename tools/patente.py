"""Patent-Recherche: Datenbeschaffung (EPO OPS primär, Google-Patents-Scraping
als Fallback), Fallakten-Verwaltung, Analyse-Pipeline (7 Stufen + Merkmals-
analyse) und Wissensgraph-Datenaufbau für den „⚖️ Patente"-Tab.

Portiert aus dem eigenständigen Streamlit-Tool ~/ai-project/patente (app.py).
Reine Logik, keine FastAPI-/DB-Importe — main.py macht HTTP-Plumbing +
Persistenz, analog zum Aufbau von tools/mailstore.py.

Hinweis: Google Patents hat keine offizielle öffentliche API — das Scraping der
HTML-/XHR-Endpunkte birgt ein ToS-Risiko, das unverändert aus dem Original-Tool
übernommen wird. Mit hinterlegtem EPO-OPS-Key (tools/epo_ops.py) ist Google nur
noch Fallback/Ergänzung; alle Google-Aufrufe laufen gedrosselt (Mindestpause +
Backoff, Muster tools/search.py) und über einen Datei-Cache, damit wiederholte
Abrufe keine neuen Anfragen erzeugen.
"""
from __future__ import annotations

import asyncio
import json
import re
import shutil
import time
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

# Drosselung der Google-Aufrufe: globales Lock + Mindestpause + Backoff bei
# 429/5xx — dasselbe Muster wie _ddgs_call in tools/search.py. Verhindert, dass
# Stapel-Importe (bis 500 Nummern) Google-Ratelimits auslösen.
_GP_LOCK = asyncio.Lock()
_GP_MIN_INTERVAL = 2.0
_GP_MAX_RETRIES = 3
_gp_last_call_at = 0.0

# Datei-Cache für gescrapte Seiten-Datensätze: <cache_dir>/<patent_id>.json.
# Standard-Gültigkeit 30 Tage — amtliche Felder ändern sich selten; `force`
# erzwingt einen frischen Abruf.
CACHE_MAX_AGE_DAYS = 30


def _clean_number(n: str) -> str:
    return (n or "").strip().replace("-", "").replace("/", "").replace(" ", "")


async def _gp_get(client: httpx.AsyncClient, url: str) -> httpx.Response:
    """GET auf Google Patents — gedrosselt, mit Backoff bei Ratelimit/5xx."""
    global _gp_last_call_at
    async with _GP_LOCK:
        last: Optional[Exception] = None
        for attempt in range(_GP_MAX_RETRIES):
            wait = _GP_MIN_INTERVAL - (time.monotonic() - _gp_last_call_at)
            if wait > 0:
                await asyncio.sleep(wait)
            try:
                resp = await client.get(url, headers=HEADERS, timeout=15)
                _gp_last_call_at = time.monotonic()
                if resp.status_code in (429, 500, 502, 503) and attempt < _GP_MAX_RETRIES - 1:
                    await asyncio.sleep(_GP_MIN_INTERVAL * (attempt + 2) * 2)  # 8 s, 12 s
                    continue
                return resp
            except Exception as e:
                last = e
                _gp_last_call_at = time.monotonic()
                if attempt < _GP_MAX_RETRIES - 1:
                    await asyncio.sleep(_GP_MIN_INTERVAL * (attempt + 2))
                    continue
                raise
        raise last if last else RuntimeError("Google-Patents-Abruf fehlgeschlagen")


def _cache_read(cache_dir: Optional[Path], pid: str, max_age_days: int) -> Optional[dict]:
    if not cache_dir:
        return None
    fp = cache_dir / f"{pid}.json"
    if not fp.exists():
        return None
    try:
        data = json.loads(fp.read_text(encoding="utf-8"))
        ts = datetime.fromisoformat(data.get("scraped_at", "1970-01-01"))
        if (datetime.now() - ts).days <= max_age_days:
            return data
    except Exception:
        pass
    return None


def _cache_write(cache_dir: Optional[Path], data: dict):
    if not cache_dir or not data.get("patent_id") or "error" in data:
        return
    try:
        cache_dir.mkdir(parents=True, exist_ok=True)
        (cache_dir / f"{data['patent_id']}.json").write_text(
            json.dumps(data, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


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


def _extract_dates(soup: BeautifulSoup) -> dict:
    """Anmelde-/Prioritäts-/Publikationsdatum aus den time-/meta-Tags der Seite."""
    out = {}
    for itemprop, field in (("filingDate", "filing_date"),
                            ("priorityDate", "priority_date"),
                            ("publicationDate", "publication_date")):
        tag = soup.find("time", itemprop=itemprop) or soup.find("meta", itemprop=itemprop)
        val = (tag.get("datetime") or tag.get("content") or tag.text or "").strip() if tag else ""
        if val:
            out[field] = val[:10]
    return out


def _extract_inventors(soup: BeautifulSoup) -> list:
    out = []
    for tag in soup.find_all("dd", itemprop="inventor"):
        name = tag.text.strip()
        if name:
            out.append(name)
    return list(dict.fromkeys(out))


async def fetch_patent_details(client: httpx.AsyncClient, patent_id: str,
                               cache_dir: Optional[Path] = None,
                               force: bool = False) -> dict:
    """Ein Patent von Google Patents scrapen (gedrosselt + Datei-Cache).
    Bei Fehler: `{"patent_id","error"}`."""
    pid = _clean_number(patent_id)
    if not force:
        cached = _cache_read(cache_dir, pid, CACHE_MAX_AGE_DAYS)
        if cached:
            return cached
    url = f"https://patents.google.com/patent/{pid}/en"
    try:
        resp = await _gp_get(client, url)
        if resp.status_code != 200:
            return {"patent_id": pid, "error": f"HTTP {resp.status_code} von Google Patents"}
        soup = BeautifulSoup(resp.text, "html.parser")

        titel_tag = soup.find("meta", {"name": "DC.title"})
        titel = " ".join((titel_tag["content"] if titel_tag else "Unbekannter Titel").split())

        abstract_sec = soup.find("section", itemprop="abstract")
        abstract = abstract_sec.text.strip() if abstract_sec else "Keine Zusammenfassung gefunden."

        claims_sec = soup.find("section", itemprop="claims")
        claims = claims_sec.text.strip() if claims_sec else "Keine Ansprüche gefunden."

        desc_sec = soup.find("section", itemprop="description")
        description = desc_sec.text.strip()[:20000] if desc_sec else ""

        zitate = []
        for row in soup.find_all("tr", itemprop="backwardReferences"):
            pub = row.find(itemprop="publicationNumber")
            if pub and pub.text:
                zitate.append(_clean_number(pub.text))
        zitate = list(dict.fromkeys(zitate))

        # Vorwärtszitate („Cited By") — wer zitiert DIESES Patent
        zitiert_von = []
        for row in soup.find_all("tr", itemprop=re.compile(r"^forwardReferences")):
            pub = row.find(itemprop="publicationNumber")
            if pub and pub.text:
                zitiert_von.append(_clean_number(pub.text))
        zitiert_von = list(dict.fromkeys(zitiert_von))

        data = {
            "patent_id": pid,
            "title": titel,
            "abstract": abstract,
            "claims": claims,
            "description": description,
            "zitate": zitate,
            "zitiert_von": zitiert_von,
            "ipc_klassen": extract_ipc_classes(soup),
            "rechteinhaber": extract_assignees(soup),
            "inventors": _extract_inventors(soup),
            "source": "google",
            "url": url,
            "scraped_at": datetime.now().isoformat(),
        }
        data.update(_extract_dates(soup))
        _cache_write(cache_dir, data)
        return data
    except Exception as e:
        return {"patent_id": pid, "error": str(e)}


# Felder, die die amtliche OPS-Antwort gewinnen soll, wenn beide Quellen liefern.
_OPS_PREFERRED = ("filing_date", "priority_date", "publication_date",
                  "cpc_klassen", "inventors", "family", "legal_status")


async def fetch_patent(client: httpx.AsyncClient, patent_id: str,
                       ops_creds: Optional[dict] = None,
                       cache_dir: Optional[Path] = None,
                       force: bool = False) -> dict:
    """Vereinheitlichter Patent-Abruf: EPO OPS (amtlich) zuerst, Google-Scraping
    als Fallback bzw. Ergänzung fehlender Felder (v. a. Ansprüche/Beschreibung,
    die OPS nicht für alle Ämter liefert). ``source`` nennt die Herkunft."""
    from tools import epo_ops

    ops_data: dict = {}
    if epo_ops.creds_ok(ops_creds):
        ops_data = await epo_ops.fetch_details(client, ops_creds, patent_id)
        if "error" in ops_data:
            ops_data = {}

    need_google = not ops_data or not (ops_data.get("claims") and ops_data.get("abstract"))
    g_data: dict = {}
    if need_google:
        g_data = await fetch_patent_details(client, patent_id, cache_dir=cache_dir, force=force)

    if not ops_data and (not g_data or "error" in g_data):
        return g_data or {"patent_id": _clean_number(patent_id), "error": "Kein Abruf möglich"}

    if not ops_data:
        return g_data

    # Merge: OPS-Basis, Google füllt Lücken; amtliche Felder gewinnen.
    merged = dict(g_data) if g_data and "error" not in g_data else {}
    for k, v in ops_data.items():
        if k in _OPS_PREFERRED or not merged.get(k) or (isinstance(v, str) and len(v) > len(str(merged.get(k) or ""))):
            if v:
                merged[k] = v
    merged["patent_id"] = ops_data.get("patent_id") or merged.get("patent_id")
    merged["source"] = "epo_ops+google" if (g_data and "error" not in g_data) else "epo_ops"
    merged.setdefault("scraped_at", datetime.now().isoformat())
    return merged


def build_google_query(term: str = "", assignee: str = "", country: str = "",
                       ipc: str = "", date_from: str = "", date_to: str = "") -> str:
    """Google-Patents-Query-String aus den Suchfeldern (Fallback ohne OPS-Key).
    Boolean-Ausdrücke (AND/OR/NOT) im Suchbegriff bleiben erhalten; IPC/CPC-Codes
    werden als Klassifikations-Token angehängt, Daten als after:/before:-Filter."""
    parts = []
    if term:
        parts.append(term)
    if ipc:
        parts.append(f"({ipc.strip().replace(' ', '')})")
    if assignee:
        parts.append(f"assignee:({assignee})")
    if country:
        parts.append(f"country:{country}")
    if date_from:
        parts.append(f"after:publication:{date_from.replace('-', '')}")
    if date_to:
        parts.append(f"before:publication:{date_to.replace('-', '')}")
    return " ".join(parts)


async def search_patent_numbers(client: httpx.AsyncClient, query: str,
                                max_results: int = 20) -> tuple:
    """Nummernsuche über die Google-Patents-XHR-Suche (keine offizielle API).
    Gibt ``(nummern, fehler)`` zurück — ``fehler`` ist ein Text statt stillem
    Verschlucken, damit das Frontend den Grund anzeigen kann.

    Die XHR-Suche liefert ~10 Treffer je Seite; für höhere Trefferzahlen wird über
    den ``page``-Parameter geblättert, bis genug eindeutige Nummern zusammenkommen."""
    if not query:
        return [], ""
    ziel = max(1, int(max_results))
    nummern: list[str] = []
    seen: set[str] = set()
    fehler = ""
    try:
        for page in range(0, 6):  # bis zu 6 Seiten (~60 Treffer) durchblättern
            if len(nummern) >= ziel:
                break
            inner = f"q={query}&num=10" + (f"&page={page}" if page else "")
            encoded = urllib.parse.quote(inner)
            xhr_url = f"https://patents.google.com/xhr/query?url={encoded}"
            resp = await _gp_get(client, xhr_url)
            if resp.status_code != 200:
                if not nummern:
                    fehler = f"Google-Suche antwortete mit HTTP {resp.status_code}"
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
    except Exception as e:
        if not nummern:
            fehler = f"Google-Suche fehlgeschlagen: {e}"
    return nummern[:ziel], fehler


async def search_patents(client: httpx.AsyncClient, term: str = "", assignee: str = "",
                          country: str = "", max_results: int = 20,
                          ipc: str = "", date_from: str = "", date_to: str = "",
                          ops_creds: Optional[dict] = None,
                          cache_dir: Optional[Path] = None) -> tuple:
    """Suche mit Detail-Abruf: bei OPS-Key amtliche CQL-Suche, sonst Google-XHR.
    Gibt ``(ergebnisse, fehler, quelle)`` zurück."""
    from tools import epo_ops

    nummern: list[str] = []
    fehler = ""
    quelle = "google"
    if epo_ops.creds_ok(ops_creds):
        cql = epo_ops.build_cql(term, assignee, country, ipc, date_from, date_to)
        if cql:
            try:
                nummern, _total = await epo_ops.search(client, ops_creds, cql, max_results)
                quelle = "epo_ops"
            except Exception as e:
                fehler = f"OPS-Suche fehlgeschlagen ({e}) — Google-Fallback."
    if not nummern and quelle != "epo_ops":
        query = build_google_query(term, assignee, country, ipc, date_from, date_to)
        nummern, g_err = await search_patent_numbers(client, query, max_results)
        if g_err:
            fehler = (fehler + " " + g_err).strip()

    out = []
    for n in nummern:
        try:
            details = await fetch_patent(client, n, ops_creds=ops_creds, cache_dir=cache_dir)
        except Exception:
            continue
        if "error" not in details:
            out.append(details)
    return out, fehler, quelle


# ── Stärke-Kennzahlen (deterministisch, KEIN LLM) ────────────────────────────────
# Triage-Heuristik nach der Patent-Scoring-Literatur (Vorwärtszitate, Familien-
# größe, Restlaufzeit, Anspruchsbreite/-zahl): sagt, WELCHE Patente einen genauen
# Blick lohnen — sie ist bewusst KEINE Bewertung des Patentwerts. Halluzinations-
# frei, weil rein aus den amtlichen/gescrapten Feldern gerechnet.

_LEGAL_DEAD_KW = ("lapse", "expir", "withdraw", "revo", "ceas", "erlosch",
                  "verzicht", "zurückweisung", "no longer in force", "reject")
_LEGAL_ALIVE_KW = ("grant", "in force", "erteilt", "renewal", "fee payment",
                   "annual fee", "examination")


def _claim_count(claims: str) -> int:
    return len(re.findall(r"(?m)^\s*\d{1,3}[\.\)]\s", claims or ""))


def patent_kennzahlen(p: dict) -> dict:
    """Deterministische Stärke-Kennzahlen + Triage-Score 0–100 für einen
    Fallakten-Datensatz. Fehlende Felder ergeben None/0 statt Fehler."""
    out: dict = {}

    # Restlaufzeit: Anmeldetag + 20 Jahre (Regel-Laufzeit) − heute
    rest = None
    fd = (p.get("filing_date") or "")[:10]
    if re.match(r"^\d{4}-\d{2}-\d{2}$", fd):
        try:
            ablauf = datetime(int(fd[:4]) + 20, int(fd[5:7]), min(int(fd[8:10]), 28))
            rest = max(0.0, round((ablauf - datetime.now()).days / 365.25, 1))
        except Exception:
            rest = None
    out["restlaufzeit_jahre"] = rest

    out["vorwaertszitate"] = len(p.get("zitiert_von") or [])
    out["familie"] = len(p.get("family") or [])
    out["anspruchszahl"] = _claim_count(p.get("claims") or "")
    c1 = extract_claim1(p.get("claims") or "")
    out["anspruch1_worte"] = len(c1.split()) if c1 else 0

    # Rechtsstand-HINWEIS (Heuristik über die letzten Ereignisse — nie als Fakt
    # ausgeben; maßgeblich ist immer das amtliche Register)
    events = p.get("legal_status") or []
    hint = "unbekannt"
    for e in events[:6]:   # events sind absteigend sortiert (neueste zuerst)
        blob = f"{e.get('code','')} {e.get('desc','')}".lower()
        if any(k in blob for k in _LEGAL_DEAD_KW):
            hint = "vermutlich erloschen"
            break
        if any(k in blob for k in _LEGAL_ALIVE_KW):
            hint = "vermutlich in Kraft"
            break
    out["rechtsstand_hinweis"] = hint if events else "unbekannt"

    # Score 0–100 (Gewichte: Zitate 35, Familie 20, Restlaufzeit 20,
    # Anspruchsbreite 15, Anspruchszahl 10). Log-Skalierung, damit einzelne
    # Ausreißer (z. B. 500 Zitate) die Skala nicht sprengen.
    import math
    s_cit = min(1.0, math.log1p(out["vorwaertszitate"]) / math.log1p(50))
    s_fam = min(1.0, math.log1p(out["familie"]) / math.log1p(20))
    s_rest = min(1.0, (rest or 0) / 20.0)
    # Breite: kürzerer Anspruch 1 = breiter; 40 Wörter ≈ sehr breit, 300+ ≈ eng
    w = out["anspruch1_worte"]
    s_breit = 0.0 if not w else max(0.0, min(1.0, (300 - w) / 260))
    s_anz = min(1.0, out["anspruchszahl"] / 20.0)
    out["score"] = round(100 * (0.35 * s_cit + 0.20 * s_fam + 0.20 * s_rest
                                + 0.15 * s_breit + 0.10 * s_anz))
    return out


def kennzahlen_markdown(patente: list) -> str:
    """Kennzahlen-Tabelle (Markdown) für den Moderator-Prompt — deterministische
    Grundwahrheit, die das LLM zitieren, aber nicht erfinden kann."""
    rows = []
    for p in patente:
        k = patent_kennzahlen(p)
        rows.append(f"| {p.get('patent_id','?')} | {k['score']} | "
                    f"{k['restlaufzeit_jahre'] if k['restlaufzeit_jahre'] is not None else '?'} | "
                    f"{k['vorwaertszitate']} | {k['familie']} | {k['anspruchszahl']} | "
                    f"{k['anspruch1_worte']} | {k['rechtsstand_hinweis']} |")
    if not rows:
        return ""
    return ("| Patent | Score | Restlaufzeit (J.) | Vorwärtszitate | Familie | "
            "Ansprüche | Anspruch-1-Wörter | Rechtsstand-Hinweis |\n"
            "|---|---|---|---|---|---|---|---|\n" + "\n".join(rows))


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
        if bereich in ("pruefung_bestanden", "pruefung_details", "merkmale_geprueft",
                       "neuheit_geprueft", "fto_geprueft"):
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


def extract_claim1(claims_text: str) -> str:
    """Zieht Anspruch 1 deterministisch aus dem Anspruchs-Volltext: vom ersten
    „1." bis zum Beginn von „2." (Zeilenanfang). Fallback: die ersten 3000 Zeichen."""
    t = (claims_text or "").strip()
    if not t:
        return ""
    m = re.search(r"(?ms)^\s*1[\.\)]\s*(.+?)(?=^\s*2[\.\)]\s)", t)
    if m:
        return m.group(1).strip()
    m = re.search(r"(?ms)\b1[\.\)]\s*(.+?)(?=\b2[\.\)]\s)", t)
    if m and len(m.group(1)) > 80:
        return m.group(1).strip()
    return t[:3000]


async def run_merkmalsanalyse(chat_haupt: ChatFn, chat_neben: ChatFn,
                              claims_texts: list,
                              on_progress: Optional[Callable[[str], None]] = None) -> tuple:
    """Element-weise Merkmalsanalyse (Claim-Chart-Kern): zerlegt Anspruch 1 in
    Merkmale M1…Mn und erzeugt eine Markdown-Tabelle; bei zwei Dokumenten eine
    Gegenüberstellung mit Bewertung identisch/ähnlich/fehlt je Merkmal.
    ``claims_texts`` = Liste ``(patent_id, anspruchs_volltext)`` (1–2 Einträge).
    Gibt ``(markdown, geprueft)`` zurück — geprüft über dieselbe FREIGABE-Schleife
    wie die Technik-Stufe."""
    def progress(msg: str):
        if on_progress:
            on_progress(msg)

    pairs = [(pid, extract_claim1(txt)) for pid, txt in claims_texts if (txt or "").strip()][:2]
    if not pairs:
        return "", False

    if len(pairs) == 1:
        pid, c1 = pairs[0]
        system = (
            "Du bist Patentprüfer und erstellst eine Merkmalsanalyse. Zerlege Anspruch 1 "
            "in seine einzelnen technischen Merkmale (M1, M2, …) — jedes Merkmal ein in "
            "sich geschlossener Teilsatz des Anspruchs, NICHTS weglassen, NICHTS erfinden. "
            "Antworte NUR mit einer Markdown-Tabelle in genau diesem Format:\n"
            "| Nr. | Merkmal (Wortlaut) | Bedeutung/Funktion |\n|---|---|---|\n"
            "| M1 | … | … |")
        user = f"Anspruch 1 von [[{pid}]] (vollständiger Wortlaut):\n{c1}"
        original = f"ANSPRUCH 1 ({pid}):\n{c1}"
    else:
        (pid_a, c1a), (pid_b, c1b) = pairs
        system = (
            "Du bist Patentprüfer und erstellst eine vergleichende Merkmalsanalyse "
            "(Claim-Chart). Zerlege Anspruch 1 von Dokument A in seine technischen "
            "Merkmale (M1, M2, …) und prüfe je Merkmal, ob es in Anspruch 1 von "
            "Dokument B wörtlich oder sinngemäß enthalten ist. Bewertung je Merkmal: "
            "identisch / ähnlich / fehlt — mit kurzem Beleg (Zitat aus B) oder '—'. "
            "NICHTS erfinden. Antworte NUR mit einer Markdown-Tabelle in genau diesem "
            "Format:\n"
            "| Nr. | Merkmal aus A (Wortlaut) | Fundstelle in B | Bewertung |\n|---|---|---|---|\n"
            "| M1 | … | … | identisch |")
        user = (f"DOKUMENT A — Anspruch 1 von [[{pid_a}]]:\n{c1a}\n\n"
                f"DOKUMENT B — Anspruch 1 von [[{pid_b}]]:\n{c1b}")
        original = f"ANSPRUCH 1 A ({pid_a}):\n{c1a}\n\nANSPRUCH 1 B ({pid_b}):\n{c1b}"

    progress("📐 Erstelle Merkmalsanalyse (Anspruch 1)…")
    tabelle = await chat_haupt(system + _BRACKET_RULE, user)

    # Prüfschleife: Neben-Modell validiert die Tabelle gegen den Original-Anspruch
    geprueft = False
    for runde in range(2):
        pruef = await chat_neben(
            "Du bist ein Prüf-Agent. Vergleiche die Merkmalsanalyse-Tabelle mit dem "
            "Original-Anspruchswortlaut. Prüfe: (1) Sind ALLE Merkmale des Anspruchs "
            "erfasst? (2) Ist jeder Tabelleneintrag durch den Wortlaut gedeckt (nichts "
            "erfunden)? Wenn beides stimmt, antworte NUR mit dem exakten Wort 'FREIGABE'.",
            f"ORIGINAL:\n{original}\n\nZU PRÜFENDE TABELLE:\n{tabelle}")
        if "FREIGABE" in pruef.upper():
            geprueft = True
            break
        progress(f"⚠️ Merkmalsanalyse unvollständig — Korrekturrunde {runde + 1}…")
        tabelle = await chat_haupt(
            system + _BRACKET_RULE +
            f"\n\nDeine vorherige Tabelle hatte Mängel laut Prüfer:\n{pruef}\n"
            "Erstelle die Tabelle korrigiert und vollständig neu.",
            user)
    return tabelle, geprueft


async def run_pipeline(chat_haupt: ChatFn, chat_neben: ChatFn, patent_texte: str,
                        on_progress: Optional[Callable[[str], None]] = None,
                        claims_texts: Optional[list] = None,
                        sdt_kontext: str = "",
                        kennzahlen_text: str = "") -> dict:
    """Analyse-Pipeline nach Prüfer-Methodik: Technik (Prüfschleife) →
    Merkmalsanalyse (Claim-Chart) → Neuheit & erfinderische Tätigkeit
    (EPA-Aufgabe-Lösungs-Ansatz, eigene Prüfschleife) → Recht (je Merkmal) →
    Umgehung → Innovation → Entwurf → Kritik → Moderator (mit deterministischen
    Kennzahlen + Handlungsempfehlung).

    ``claims_texts``: Liste ``(patent_id, claims)`` für die Merkmalsanalyse.
    ``sdt_kontext``: nächstliegender Stand der Technik aus der Fallakte
    (RAG-Treffer, vom Aufrufer geliefert; leer = Stufe läuft nur mit dem Material).
    ``kennzahlen_text``: deterministische Score-Tabelle (``kennzahlen_markdown``)
    für den Moderator — zitierbar, nicht erfindbar."""
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

    # 1b) Merkmalsanalyse (Claim-Chart): Anspruch 1 element-weise zerlegen —
    #     strukturierte Grundlage für Recht + Umgehung statt reinem Freitext.
    merkmale = ""
    if claims_texts:
        merkmale, mk_ok = await run_merkmalsanalyse(chat_haupt, chat_neben,
                                                     claims_texts, on_progress)
        if merkmale:
            ergebnisse["merkmale"] = merkmale
            ergebnisse["merkmale_geprueft"] = mk_ok
    merk_ctx = f"\nMerkmalsanalyse (Anspruch 1, element-weise):\n{merkmale}\n" if merkmale else ""

    # 1c) Neuheit & erfinderische Tätigkeit — EPA-Aufgabe-Lösungs-Ansatz.
    #     Struktur erzwingt Prüfer-Methodik statt freiem Meinungs-Text; jede
    #     Aussage braucht eine Fundstelle. Eigene FREIGABE-Prüfschleife (1 Runde).
    progress("🧪 Prüfe Neuheit & erfinderische Tätigkeit (Aufgabe-Lösungs-Ansatz)...")
    sdt_block = (f"\n\nNÄCHSTLIEGENDER STAND DER TECHNIK AUS DER FALLAKTE "
                 f"(RAG-Treffer):\n{sdt_kontext}" if sdt_kontext else "")
    neuheit_sys = (
        "Du bist Patentprüfer und wendest strikt den Aufgabe-Lösungs-Ansatz des EPA an. "
        "Gliedere deine Antwort GENAU so:\n"
        "## 1. Nächstliegender Stand der Technik\n(benennen + begründen; nur aus dem "
        "vorliegenden Material, nichts erfinden)\n"
        "## 2. Unterschiedsmerkmale\n(welche Merkmale M1…Mn der Merkmalsanalyse sind "
        "im nächstliegenden Stand der Technik NICHT offenbart — mit Fundstelle)\n"
        "## 3. Objektive technische Aufgabe\n(abgeleitet aus der technischen Wirkung "
        "der Unterschiedsmerkmale)\n"
        "## 4. Naheliegen (Could-Would)\n(hätte der Fachmann die Lösung nicht nur "
        "finden KÖNNEN, sondern konkret Anlass gehabt, sie zu WÄHLEN? Begründung "
        "mit Beleg)\n"
        "## 5. Ergebnis\n(neuheitsschädlich getroffen / erfinderisch fraglich / "
        "vermutlich erfinderisch — je mit einem Satz Begründung)\n"
        "Wenn kein Stand der Technik im Material vorliegt, sage das ausdrücklich und "
        "beschränke dich auf eine Einschätzung der Anspruchsbreite.")
    neuheit_user = (f"Merkmalsanalyse:\n{merkmale}\n\nMaterial:\n{patent_texte}{sdt_block}"
                    if merkmale else f"Material:\n{patent_texte}{sdt_block}")
    neuheit = await haupt(neuheit_sys, neuheit_user)
    neuheit_ok = False
    for runde in range(2):
        pruef = await neben(
            "Du bist ein Prüf-Agent. Kontrolliere die Neuheits-/Erfindungshöhen-Analyse: "
            "(1) Ist jede Behauptung durch das Originalmaterial gedeckt (nichts erfunden)? "
            "(2) Folgt sie dem Aufgabe-Lösungs-Ansatz (nächstliegender SdT → Unterschieds"
            "merkmale → objektive Aufgabe → Could-Would)? Wenn beides stimmt, antworte "
            "NUR mit dem exakten Wort 'FREIGABE'.",
            f"MATERIAL:\n{patent_texte}{sdt_block}\n\nZU PRÜFENDE ANALYSE:\n{neuheit}")
        if "FREIGABE" in pruef.upper():
            neuheit_ok = True
            break
        progress(f"⚠️ Neuheitsanalyse beanstandet — Korrekturrunde {runde + 1}…")
        neuheit = await haupt(
            neuheit_sys + f"\n\nDeine vorherige Analyse hatte laut Prüfer Mängel:\n{pruef}\n"
            "Korrigiere sie vollständig.",
            neuheit_user)
    ergebnisse["neuheit"] = neuheit
    ergebnisse["neuheit_geprueft"] = neuheit_ok

    # 2) Recht — strukturiert je Merkmal (Auslegung/Breite/Schwachstelle)
    progress("⚖️ Bewerte rechtlichen Schutzumfang...")
    ergebnisse["recht"] = await haupt(
        f"Du bist Patentanwalt. Bewerte den juristischen Schutzumfang. Beginne mit "
        f"einer Tabelle über die Merkmale des Anspruchs 1 in GENAU diesem Format:\n"
        f"| Nr. | Auslegung (was fällt darunter) | Breite (breit/mittel/eng) | Schwachstelle |\n"
        f"|---|---|---|---|\n"
        f"Danach ein kurzes Fazit zu Stärken und Schwächen des Schutzbereichs. "
        f"Stütze dich auf die Merkmalsanalyse und die Neuheitsprüfung; erfinde nichts.\n\n"
        f"Technik-Analyse: {technik}\n{merk_ctx}\nNeuheitsprüfung: {ergebnisse['neuheit'][:3000]}",
        patent_texte)

    # 3) Umgehung
    progress("🚧 Suche Design-around-Optionen...")
    ergebnisse["umgehung"] = await haupt(
        f"Du bist Spezialist für Patentumgehungen. Identifiziere je Merkmal (M1, M2, …), "
        f"ob man es weglassen oder ändern kann, um den Schutzbereich zu verlassen.\n\n"
        f"Technik: {technik}\nRecht: {ergebnisse['recht']}\n{merk_ctx}",
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

    # 7) Moderator — Management-Summary mit deterministischen Kennzahlen und
    #    expliziter Handlungsempfehlung (Triage-Entscheidung statt Prosa).
    progress("📊 Erstelle Abschlussbericht...")
    kz_block = (f"\n\nDeterministische Kennzahlen (verifiziert berechnet — zitieren, "
                f"nicht verändern):\n{kennzahlen_text}" if kennzahlen_text else "")
    ergebnisse["moderator"] = await haupt(
        f"Fasse die Ergebnisse als Management-Summary zusammen. Gliedere:\n"
        f"## Kernaussage\n(3 Sätze)\n"
        f"## Kennzahlen\n(die mitgelieferte Tabelle unverändert übernehmen, dann 2 Sätze Einordnung)\n"
        f"## Handlungsempfehlung\nGenau EINE der Optionen wählen und begründen: "
        f"NEUANMELDUNG (Entwurf weiterverfolgen) / UMGEHEN (Design-around umsetzen) / "
        f"LIZENZ PRÜFEN / BEOBACHTEN / IGNORIEREN.\n\n"
        f"Neuheitsprüfung: {ergebnisse['neuheit'][:2000]}\n"
        f"Rechtliche Lage: {ergebnisse['recht']}\n"
        f"Neuer Entwurf: {ergebnisse['entwurf']}\nKritik: {ergebnisse['kritik']}{kz_block}",
        "")

    return ergebnisse


# ── FTO-Produkt-Check (Claim-Chart Patent ↔ Produktbeschreibung) ─────────────────

async def run_fto_check(chat_haupt: ChatFn, chat_neben: ChatFn, produkt_text: str,
                        patents: list,
                        on_progress: Optional[Callable[[str], None]] = None) -> dict:
    """Freedom-to-Operate-Check: prüft je Patent element-weise, ob die Merkmale
    von Anspruch 1 in der eigenen Produkt-/Ideenbeschreibung verwirklicht sind
    (All-Elements-Rule: Verletzungsrisiko nur, wenn ALLE Merkmale verwirklicht
    sind). ``patents`` = Liste ``(patent_id, claims_volltext)`` (Cap 5).
    Ergebnis: {"fto_<pid>": tabelle_md, …, "fto_fazit": text, "fto_geprueft": bool}.
    KEINE Rechtsberatung — der Fazit-Prompt weist explizit darauf hin."""
    def progress(msg: str):
        if on_progress:
            on_progress(msg)

    async def haupt(system: str, user: str) -> str:
        return await chat_haupt(system + _BRACKET_RULE, user)

    async def neben(system: str, user: str) -> str:
        return await chat_neben(system + _BRACKET_RULE, user)

    ergebnisse: dict = {}
    alle_geprueft = True
    kurzfazit: list = []

    pairs = [(pid, extract_claim1(txt)) for pid, txt in patents if (txt or "").strip()][:5]
    for idx, (pid, c1) in enumerate(pairs):
        progress(f"🛡 FTO-Check {idx + 1}/{len(pairs)}: [[{pid}]] …")
        system = (
            "Du bist Patentanwalt und prüfst Freedom-to-Operate nach der All-Elements-Rule: "
            "Ein Verletzungsrisiko besteht NUR, wenn ALLE Merkmale von Anspruch 1 im Produkt "
            "verwirklicht sind. Zerlege Anspruch 1 in seine Merkmale (M1, M2, …) und prüfe "
            "jedes gegen die Produktbeschreibung. Bewertung je Merkmal: verwirklicht / "
            "nicht verwirklicht / unklar — mit wörtlichem Beleg aus der Produktbeschreibung "
            "oder '—'. NICHTS erfinden; was die Beschreibung nicht hergibt, ist 'unklar'. "
            "Antworte NUR mit einer Markdown-Tabelle in genau diesem Format und danach "
            "EINEM Ergebnis-Satz:\n"
            "| Nr. | Merkmal aus Anspruch 1 | Fundstelle im Produkt | Bewertung |\n"
            "|---|---|---|---|\n| M1 | … | … | verwirklicht |\n\n"
            "Ergebnis: <Verletzungsrisiko / kein Verletzungsrisiko erkennbar / unklar> — <1 Satz>")
        user = (f"Anspruch 1 von [[{pid}]]:\n{c1}\n\n"
                f"PRODUKT-/IDEENBESCHREIBUNG:\n{produkt_text}")
        tabelle = await haupt(system, user)

        geprueft = False
        for runde in range(2):
            pruef = await neben(
                "Du bist ein Prüf-Agent. Kontrolliere die FTO-Tabelle: (1) Sind ALLE "
                "Merkmale des Anspruchs erfasst? (2) Ist jede 'verwirklicht'-Bewertung "
                "durch ein wörtliches Zitat aus der Produktbeschreibung belegt? "
                "(3) Folgt das Ergebnis der All-Elements-Rule? Wenn alles stimmt, "
                "antworte NUR mit dem exakten Wort 'FREIGABE'.",
                f"ANSPRUCH 1 ({pid}):\n{c1}\n\nPRODUKT:\n{produkt_text}\n\n"
                f"ZU PRÜFENDE TABELLE:\n{tabelle}")
            if "FREIGABE" in pruef.upper():
                geprueft = True
                break
            progress(f"⚠️ FTO-Tabelle für [[{pid}]] beanstandet — Korrekturrunde {runde + 1}…")
            tabelle = await haupt(
                system + f"\n\nDeine vorherige Tabelle hatte laut Prüfer Mängel:\n{pruef}\n"
                "Erstelle sie korrigiert neu.",
                user)
        ergebnisse[f"fto_{pid}"] = tabelle
        alle_geprueft = alle_geprueft and geprueft
        kurzfazit.append(f"[[{pid}]]:\n{tabelle[-600:]}")

    if not ergebnisse:
        return {}

    progress("📊 Erstelle FTO-Gesamtfazit…")
    ergebnisse["fto_fazit"] = await haupt(
        "Fasse die FTO-Einzelprüfungen zu einem Gesamtfazit zusammen: je Patent eine "
        "Zeile (Risiko-Ampel 🔴 Verletzungsrisiko / 🟡 unklar / 🟢 kein Risiko erkennbar "
        "+ ausschlaggebendes Merkmal), danach die wichtigsten offenen Punkte, die der "
        "Nutzer klären sollte. Schließe wörtlich mit: 'Hinweis: Diese automatisierte "
        "Auswertung ist keine Rechtsberatung — für belastbare FTO-Aussagen einen "
        "Patentanwalt einbeziehen.'",
        "\n\n---\n\n".join(kurzfazit))
    ergebnisse["fto_geprueft"] = alle_geprueft
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
