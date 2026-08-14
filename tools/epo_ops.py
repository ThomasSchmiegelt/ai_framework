"""EPO Open Patent Services (OPS) — offizieller, kostenloser Zugang zu amtlichen
Patentdaten (130+ Mio. Dokumente, INPADOC-Rechtsstand, Patentfamilien).

Primäre Datenquelle des Patente-Tabs; das Google-Patents-Scraping in
tools/patente.py bleibt Fallback/Ergänzung. Reine Logik ohne FastAPI-/DB-Importe
(main.py macht HTTP-Plumbing + Persistenz, analog tools/patente.py).

Zugang: kostenloser Developer-Account unter https://developers.epo.org →
Consumer Key + Secret (OAuth2 Client-Credentials). Die Zugangsdaten liegen in
data/epo_ops.json (gitignored; Backup nur mit dem ``secrets``-Schalter).

Fair-Use: OPS drosselt zu dichte Anfragen (HTTP 403 mit X-Rebound-Quota).
Deshalb serialisiert ``_ops_get`` alle Aufrufe über ein globales Lock, hält eine
Mindestpause ein und wiederholt bei Drossel-Fehlern mit wachsender Wartezeit —
dasselbe Muster wie ``_ddgs_call`` in tools/search.py.

Die OPS-JSON-Antworten sind tief verschachtelt und namespaced
(``ops:world-patent-data`` …). Statt fixer Pfade sucht ``_find`` rekursiv nach
Schlüsseln ohne Namespace-Präfix — robust gegen Strukturvarianten.
"""
from __future__ import annotations

import asyncio
import base64
import time
from typing import Optional

import httpx

BASE = "https://ops.epo.org/3.2"

_LOCK = asyncio.Lock()
_MIN_INTERVAL = 1.2   # Sekunden zwischen zwei OPS-Aufrufen (Fair-Use)
_MAX_RETRIES = 3
_last_call_at = 0.0

# Token-Cache je Consumer-Key: {key: (token, gültig_bis_monotonic)}
_tokens: dict = {}


class OpsError(Exception):
    """Fehler der OPS-Schnittstelle (Auth, Quota, Parsing)."""


def creds_ok(creds: Optional[dict]) -> bool:
    return bool(creds and creds.get("consumer_key") and creds.get("consumer_secret"))


async def get_token(client: httpx.AsyncClient, creds: dict) -> str:
    """OAuth2-Token holen (Client-Credentials); ~20 min gültig, gecacht."""
    key = creds.get("consumer_key", "")
    secret = creds.get("consumer_secret", "")
    if not key or not secret:
        raise OpsError("Kein EPO-OPS-Key hinterlegt")
    tok = _tokens.get(key)
    if tok and tok[1] > time.monotonic():
        return tok[0]
    basic = base64.b64encode(f"{key}:{secret}".encode()).decode()
    resp = await client.post(
        f"{BASE}/auth/accesstoken",
        headers={"Authorization": f"Basic {basic}",
                 "Content-Type": "application/x-www-form-urlencoded"},
        content="grant_type=client_credentials", timeout=20)
    if resp.status_code != 200:
        raise OpsError(f"OPS-Anmeldung fehlgeschlagen (HTTP {resp.status_code}) — Key/Secret prüfen")
    data = resp.json()
    token = data.get("access_token", "")
    if not token:
        raise OpsError("OPS-Anmeldung lieferte kein Token")
    ttl = int(data.get("expires_in") or 1200)
    _tokens[key] = (token, time.monotonic() + max(60, ttl - 60))
    return token


def _is_throttle(status: int) -> bool:
    return status in (403, 429, 503)


async def _ops_get(client: httpx.AsyncClient, creds: dict, path: str,
                   params: Optional[dict] = None) -> dict:
    """GET auf einen rest-services-Pfad, gedrosselt + Backoff, JSON zurück."""
    global _last_call_at
    async with _LOCK:
        last_err: Optional[Exception] = None
        for attempt in range(_MAX_RETRIES):
            wait = _MIN_INTERVAL - (time.monotonic() - _last_call_at)
            if wait > 0:
                await asyncio.sleep(wait)
            try:
                token = await get_token(client, creds)
                resp = await client.get(
                    f"{BASE}/rest-services/{path}", params=params or {},
                    headers={"Authorization": f"Bearer {token}",
                             "Accept": "application/json"},
                    timeout=30)
                _last_call_at = time.monotonic()
                if resp.status_code == 404:
                    raise OpsError("nicht gefunden (404)")
                if _is_throttle(resp.status_code) and attempt < _MAX_RETRIES - 1:
                    # Quota/Drossel → wachsende Pause, Token verwerfen (403 kann
                    # auch ein abgelaufenes/ungültiges Token bedeuten)
                    _tokens.pop(creds.get("consumer_key", ""), None)
                    await asyncio.sleep(_MIN_INTERVAL * (attempt + 2) * 3)
                    continue
                if resp.status_code != 200:
                    raise OpsError(f"HTTP {resp.status_code}")
                return resp.json()
            except OpsError as e:
                last_err = e
                if "404" in str(e):
                    raise
            except Exception as e:  # Netz/JSON
                last_err = e
                _last_call_at = time.monotonic()
        raise OpsError(str(last_err) if last_err else "OPS-Aufruf fehlgeschlagen")


# ── Tolerante JSON-Navigation ────────────────────────────────────────────────

def _strip_ns(key: str) -> str:
    return key.split(":", 1)[-1] if ":" in key else key


def _find(obj, name: str) -> list:
    """Alle Werte, deren Schlüssel (ohne Namespace) ``name`` heißt — rekursiv."""
    out = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            if _strip_ns(k) == name:
                out.append(v)
            out.extend(_find(v, name))
    elif isinstance(obj, list):
        for it in obj:
            out.extend(_find(it, name))
    return out


def _lst(x) -> list:
    if x is None:
        return []
    return x if isinstance(x, list) else [x]


def _txt(node) -> str:
    """Textinhalt eines OPS-Knotens ({"$": "…"} / str / Liste / verschachtelt)."""
    if node is None:
        return ""
    if isinstance(node, str):
        return node.strip()
    if isinstance(node, (int, float)):
        return str(node)
    if isinstance(node, dict):
        if "$" in node:
            return str(node["$"]).strip()
        return " ".join(t for t in (_txt(v) for k, v in node.items() if not k.startswith("@")) if t)
    if isinstance(node, list):
        return "\n".join(t for t in (_txt(it) for it in node) if t)
    return ""


def _docid_str(docid: dict) -> str:
    """docdb/epodoc-document-id → "US1234567B2" (Länder+Nummer+Kind, ohne Punkte)."""
    if not isinstance(docid, dict):
        return ""
    dn = _txt(docid.get("doc-number"))
    if not dn:
        return ""
    cc = _txt(docid.get("country"))
    kind = _txt(docid.get("kind"))
    if cc and dn.upper().startswith(cc.upper()):
        cc = ""   # epodoc-Nummern enthalten das Land bereits
    return f"{cc}{dn}{kind}"


def _docid_date(docid: dict) -> str:
    d = _txt(docid.get("date")) if isinstance(docid, dict) else ""
    if len(d) == 8 and d.isdigit():
        return f"{d[:4]}-{d[4:6]}-{d[6:8]}"
    return d


def _pick_lang(nodes: list, langs=("de", "en")) -> str:
    """Aus sprachvarianten Knoten (@lang) bevorzugt de/en wählen."""
    flat = []
    for n in nodes:
        flat.extend(_lst(n))
    for lang in langs:
        for n in flat:
            if isinstance(n, dict) and str(n.get("@lang", "")).lower() == lang:
                return _txt(n)
    return _txt(flat[0]) if flat else ""


# ── Nummern-Normalisierung ───────────────────────────────────────────────────

def to_epodoc(number: str) -> str:
    """Best-effort-Normalisierung "US 10,000,000 B2" → "US10000000B2" (epodoc)."""
    return "".join(c for c in str(number or "") if c.isalnum()).upper()


def _without_kind(number: str) -> str:
    """Kind-Code am Ende entfernen (z. B. "…B2" → "…"), falls vorhanden."""
    import re
    return re.sub(r"[A-Z]\d?$", "", number)


# ── Fachliche Abfragen ───────────────────────────────────────────────────────

async def _pub_data(client, creds, number: str, endpoint: str) -> dict:
    """published-data-Abruf mit Kind-Code-Fallback (manche Nummern nur ohne Kind)."""
    nr = to_epodoc(number)
    try:
        return await _ops_get(client, creds, f"published-data/publication/epodoc/{nr}/{endpoint}")
    except OpsError as e:
        stripped = _without_kind(nr)
        if stripped and stripped != nr and "404" in str(e):
            return await _ops_get(client, creds, f"published-data/publication/epodoc/{stripped}/{endpoint}")
        raise


def _parse_biblio(data: dict) -> dict:
    out: dict = {}
    docs = []
    for ed in _find(data, "exchange-document"):
        docs.extend(_lst(ed))
    if not docs:
        return out
    doc = docs[0]
    biblio = (_find(doc, "bibliographic-data") or [{}])[0]

    out["title"] = _pick_lang(_find(biblio, "invention-title"))
    out["abstract"] = _pick_lang(_find(doc, "abstract"))

    # Daten: Publikation / Anmeldung / Priorität
    def _dates_in(node) -> list:
        out_d = []
        for did in _find(node, "document-id"):
            for it in _lst(did):
                d = _docid_date(it)
                if d:
                    out_d.append(d)
        return out_d

    for ref_name, field in (("publication-reference", "publication_date"),
                            ("application-reference", "filing_date")):
        dates = []
        for ref in _find(biblio, ref_name):
            dates.extend(_dates_in(ref))
        if dates:
            out[field] = dates[0]
    prio_dates = []
    for pc in _find(biblio, "priority-claim"):
        prio_dates.extend(_dates_in(pc))
    if prio_dates:
        out["priority_date"] = min(prio_dates)

    # IPC (classifications-ipcr) + CPC (patent-classification mit Komponenten)
    ipc = []
    for node in _find(biblio, "classification-ipcr"):
        for it in _lst(node):
            t = _txt(it.get("text") if isinstance(it, dict) else it).replace(" ", "")
            if t:
                ipc.append(t)
    out["ipc_klassen"] = list(dict.fromkeys(ipc))
    cpc = []
    for node in _find(biblio, "patent-classification"):
        for it in _lst(node):
            if not isinstance(it, dict):
                continue
            sec = _txt(it.get("section"))
            cls = _txt(it.get("class"))
            sub = _txt(it.get("subclass"))
            grp = _txt(it.get("main-group"))
            sg = _txt(it.get("subgroup"))
            if sec and cls:
                code = f"{sec}{cls}{sub}{grp}{('/' + sg) if sg else ''}"
                cpc.append(code)
    out["cpc_klassen"] = list(dict.fromkeys(cpc))

    # Parteien: Anmelder/Rechteinhaber + Erfinder (bevorzugt "original"-Format)
    def _names(kind: str) -> list:
        names, orig = [], []
        for node in _find(biblio, kind):
            for it in _lst(node):
                if not isinstance(it, dict):
                    continue
                nm = _txt(_find(it, "name")[:1] or "")
                if not nm:
                    continue
                (orig if str(it.get("@data-format", "")) == "original" else names).append(nm)
        return list(dict.fromkeys(orig or names))
    out["rechteinhaber"] = _names("applicant")
    out["inventors"] = _names("inventor")

    # Rückwärtszitate (patcit)
    cites = []
    for node in _find(biblio, "patcit"):
        for it in _lst(node):
            for did in _find(it, "document-id"):
                found = ""
                for d in _lst(did):
                    s = _docid_str(d)
                    if s:
                        found = s
                        break
                if found:
                    cites.append(found)
                    break
    out["zitate"] = list(dict.fromkeys(cites))
    return out


async def fetch_details(client: httpx.AsyncClient, creds: dict, number: str,
                        with_fulltext: bool = True, with_family: bool = True,
                        with_legal: bool = True) -> dict:
    """Amtliche Patentdaten via OPS. Liefert das Fallakten-Schema des Patente-Tabs;
    bei Fehler `{"patent_id","error"}` (Aufrufer fällt auf Google-Scraping zurück)."""
    pid = to_epodoc(number)
    try:
        biblio_raw = await _pub_data(client, creds, pid, "biblio")
    except Exception as e:
        return {"patent_id": pid, "error": f"OPS: {e}"}

    out = {"patent_id": pid, "source": "epo_ops",
           "url": f"https://patents.google.com/patent/{pid}/en"}
    out.update(_parse_biblio(biblio_raw))

    if with_fulltext:
        # Ansprüche/Beschreibung: nicht für alle Ämter verfügbar → best effort
        try:
            claims_raw = await _pub_data(client, creds, pid, "claims")
            claims = "\n".join(t for t in (_txt(c) for c in _find(claims_raw, "claim")) if t)
            if claims:
                out["claims"] = claims
        except Exception:
            pass
        try:
            desc_raw = await _pub_data(client, creds, pid, "description")
            desc = _txt((_find(desc_raw, "description") or [None])[0])
            if desc:
                out["description"] = desc[:20000]
        except Exception:
            pass

    if with_family:
        try:
            fam_raw = await _ops_get(client, creds, f"family/publication/epodoc/{pid}")
            fam = []
            for member in _find(fam_raw, "family-member"):
                for it in _lst(member):
                    for pref in _find(it, "publication-reference"):
                        found = ""
                        for did in _find(pref, "document-id"):
                            for d in _lst(did):
                                s = _docid_str(d)
                                if s:
                                    found = s
                                    break
                            if found:
                                break
                        if found:
                            fam.append(found)
                            break
            out["family"] = list(dict.fromkeys(fam))[:60]
        except Exception:
            pass

    if with_legal:
        try:
            legal_raw = await _ops_get(client, creds, f"legal/publication/epodoc/{pid}")
            events = []
            for node in _find(legal_raw, "legal"):
                for it in _lst(node):
                    if not isinstance(it, dict):
                        continue
                    code = _txt(it.get("@code") or it.get("code"))
                    desc = _txt(it.get("@desc") or it.get("desc"))
                    date = _txt(it.get("@date") or it.get("date") or it.get("dateMigr"))
                    if len(date) == 8 and date.isdigit():
                        date = f"{date[:4]}-{date[4:6]}-{date[6:8]}"
                    if code or desc:
                        events.append({"date": date, "code": code, "desc": desc[:120]})
            events.sort(key=lambda e: e.get("date") or "", reverse=True)
            out["legal_status"] = events[:40]
        except Exception:
            pass

    return out


# ── Suche (CQL) ──────────────────────────────────────────────────────────────

def build_cql(term: str = "", assignee: str = "", country: str = "",
              ipc: str = "", date_from: str = "", date_to: str = "") -> str:
    """Baut eine OPS-CQL-Query aus den Suchfeldern. Freitext mit AND/OR/NOT wird
    unverändert in die Titel/Abstract-Suche übernommen."""
    parts = []
    t = (term or "").strip()
    if t:
        if any(op in t for op in (" AND ", " OR ", " NOT ", "=")):
            parts.append(f"ta=({t})" if "=" not in t else t)
        else:
            parts.append(f'ta="{t}"')
    if assignee.strip():
        parts.append(f'pa="{assignee.strip()}"')
    if country.strip():
        parts.append(f"pn={country.strip().upper()}")
    if ipc.strip():
        parts.append(f'ic="{ipc.strip().replace(" ", "")}"')
    df = (date_from or "").replace("-", "").strip()
    dt = (date_to or "").replace("-", "").strip()
    if df or dt:
        parts.append(f'pd within "{df or "19000101"} {dt or "20991231"}"')
    return " and ".join(parts)


async def search(client: httpx.AsyncClient, creds: dict, cql: str,
                 max_results: int = 20) -> tuple:
    """CQL-Suche → (Liste Publikationsnummern, Gesamttrefferzahl)."""
    n = max(1, min(int(max_results or 20), 100))
    data = await _ops_get(client, creds, "published-data/search",
                          params={"q": cql, "Range": f"1-{n}"})
    total = 0
    for sr in _find(data, "biblio-search"):
        for it in _lst(sr):
            if isinstance(it, dict) and it.get("@total-result-count"):
                try:
                    total = int(it["@total-result-count"])
                except Exception:
                    pass
    nums = []
    for pref in _find(data, "publication-reference"):
        found = ""
        for did in _find(pref, "document-id"):
            for d in _lst(did):
                s = _docid_str(d)
                if s:
                    found = s
                    break
            if found:
                break
        if found and found not in nums:
            nums.append(found)
    return nums[:n], total
