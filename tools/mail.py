"""Mail-Lesezugriff (read-only) für die „Mail → Wissensdatenbank"-Funktion.

Unterstützt **IMAP** und **POP3** — gewählt über `cfg["protocol"]` (`"imap"` /
`"pop3"`, Standard `"imap"`). Nur Python-Standardbibliothek (`imaplib`, `poplib`,
`email`) → bleibt MIT/permissiv, keine neuen Abhängigkeiten. Die Aufrufe sind
blockierend und werden in `main.py` über `asyncio.to_thread(...)` aus dem
Event-Loop ausgelagert. Die „uid" ist bei IMAP die echte IMAP-UID, bei POP3 die
UIDL-Kennung — in beiden Fällen über das Auflisten/Übernehmen hinweg stabil.
"""
from __future__ import annotations

import email
import imaplib
import poplib
import re
from email.header import decode_header, make_header

_MAX_BODY = 100_000   # sehr lange Threads kappen (Speicher/Embedding-Schutz)


def domain_of(addr: str) -> str:
    """Extrahiert die Domäne aus einem From-/To-Header (z. B.
    ``"Max Muster <max@firma.de>"`` → ``firma.de``). Leer, wenn keine erkennbar."""
    m = re.search(r"[\w.+-]+@([\w-]+(?:\.[\w-]+)+)", str(addr or ""))
    return m.group(1).lower() if m else ""


# Signaturen/Disclaimer/Zitat-Verläufe, die vor der RAG-Aufnahme stören
_SIG_MARKERS = (
    "-- ", "mit freundlichen grüßen", "freundliche grüße", "beste grüße",
    "viele grüße", "best regards", "kind regards", "regards,", "gesendet von",
    "sent from my", "von meinem", "diese e-mail", "this e-mail", "this email",
    "vertraulich", "confidential", "haftungsausschluss", "disclaimer",
)
_QUOTE_INTRO = re.compile(
    r"^\s*(>|am\s.+\sschrieb|on\s.+\swrote|-{2,}\s*(original|ursprüngliche)|"
    r"von:\s|from:\s|gesendet:\s|sent:\s)", re.IGNORECASE)


def clean_mail_text(text: str) -> str:
    """Bereinigt einen Mail-Body vor der RAG-Aufnahme: schneidet den zitierten
    Vorgänger-Verlauf ab, entfernt Signatur/Disclaimer ab dem ersten Marker und
    reduziert Leerzeilen. Konservativ — der eigentliche Inhalt bleibt erhalten."""
    if not text:
        return ""
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    out: list[str] = []
    for ln in lines:
        low = ln.strip().lower()
        # Beginn eines zitierten Verlaufs → ab hier abschneiden
        if _QUOTE_INTRO.match(ln):
            break
        # Signatur-/Disclaimer-Marker → ab hier abschneiden
        if low and any(low.startswith(mk) or low == mk.strip() for mk in _SIG_MARKERS):
            break
        out.append(ln)
    cleaned = "\n".join(out)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
    return cleaned or text.strip()   # nie komplett leeren


def _decode(value: str) -> str:
    """MIME-kodierte Header (=?UTF-8?…?=) in lesbaren Text wandeln."""
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value)))
    except Exception:
        return value


def _protocol(cfg: dict) -> str:
    return "pop3" if str(cfg.get("protocol", "imap")).lower() == "pop3" else "imap"


def _imap_connect(cfg: dict) -> imaplib.IMAP4:
    host = (cfg.get("host") or "").strip()
    user = (cfg.get("user") or "").strip()
    if not host or not user:
        raise ValueError("Mail-Zugang nicht konfiguriert (Host/Benutzer fehlt).")
    use_ssl = cfg.get("ssl", True)
    port = int(cfg.get("port") or (993 if use_ssl else 143))
    conn = imaplib.IMAP4_SSL(host, port) if use_ssl else imaplib.IMAP4(host, port)
    conn.login(user, cfg.get("password", ""))
    return conn


def _pop3_connect(cfg: dict) -> poplib.POP3:
    host = (cfg.get("host") or "").strip()
    user = (cfg.get("user") or "").strip()
    if not host or not user:
        raise ValueError("Mail-Zugang nicht konfiguriert (Host/Benutzer fehlt).")
    use_ssl = cfg.get("ssl", True)
    port = int(cfg.get("port") or (995 if use_ssl else 110))
    conn = poplib.POP3_SSL(host, port) if use_ssl else poplib.POP3(host, port)
    conn.user(user)
    conn.pass_(cfg.get("password", ""))
    return conn


def _pop3_uidl_map(conn: poplib.POP3) -> list[tuple[int, str]]:
    """Liefert [(Nachrichtennummer, UIDL), …] sortiert (älteste→neueste)."""
    out: list[tuple[int, str]] = []
    for line in conn.uidl()[1]:
        parts = line.decode("ascii", "replace").split()
        if len(parts) >= 2:
            out.append((int(parts[0]), parts[1]))
    return out


def _body_text(msg: email.message.Message) -> str:
    """Plain-Text-Körper extrahieren (bevorzugt text/plain, sonst HTML grob entstrippt)."""
    def _dec(part) -> str:
        try:
            return part.get_payload(decode=True).decode(
                part.get_content_charset() or "utf-8", "replace")
        except Exception:
            return ""

    if msg.is_multipart():
        for part in msg.walk():
            disp = str(part.get("Content-Disposition", ""))
            if part.get_content_type() == "text/plain" and "attachment" not in disp:
                t = _dec(part)
                if t.strip():
                    return t
        for part in msg.walk():
            if part.get_content_type() == "text/html":
                html = _dec(part)
                if html.strip():
                    return re.sub(r"<[^>]+>", " ", html)
        return ""
    return _dec(msg) or str(msg.get_payload())


def list_messages(cfg: dict, limit: int = 25, search: str = "") -> list[dict]:
    """Listet die neuesten Mails (nur Header). Dispatcht nach Protokoll."""
    if _protocol(cfg) == "pop3":
        return _pop3_list(cfg, limit, search)
    return _imap_list(cfg, limit, search)


def fetch_messages(cfg: dict, uids: list[str]) -> list[dict]:
    """Holt die vollständigen Mails (Header + Plain-Text-Körper). Dispatcht nach Protokoll."""
    if _protocol(cfg) == "pop3":
        return _pop3_fetch(cfg, uids)
    return _imap_fetch(cfg, uids)


def _imap_list(cfg: dict, limit: int = 25, search: str = "") -> list[dict]:
    """Listet die neuesten Mails aus INBOX (nur Header). Optionaler Suchbegriff
    filtert clientseitig über Absender/Betreff (umlaut-/charset-sicher)."""
    conn = _imap_connect(cfg)
    try:
        conn.select("INBOX", readonly=True)
        typ, data = conn.uid("search", None, "ALL")
        if typ != "OK":
            return []
        uids = data[0].split()
        # Wenn gesucht wird, mehr Kandidaten scannen und in Python filtern.
        scan = max(limit, 150) if search else limit
        uids = uids[-scan:][::-1]   # neueste zuerst
        s = search.lower()
        out: list[dict] = []
        for uid in uids:
            typ, mdata = conn.uid(
                "fetch", uid, "(BODY.PEEK[HEADER.FIELDS (FROM SUBJECT DATE)])")
            if typ != "OK":
                continue
            raw = next((p[1] for p in mdata if isinstance(p, tuple)), b"")
            hdr = email.message_from_bytes(raw)
            frm = _decode(hdr.get("From", ""))
            subj = _decode(hdr.get("Subject", "(kein Betreff)"))
            if s and s not in frm.lower() and s not in subj.lower():
                continue
            out.append({
                "uid": uid.decode(),
                "from": frm,
                "subject": subj,
                "date": _decode(hdr.get("Date", "")),
            })
            if len(out) >= limit:
                break
        return out
    finally:
        try:
            conn.logout()
        except Exception:
            pass


def _imap_fetch(cfg: dict, uids: list[str]) -> list[dict]:
    """Holt die vollständigen Mails (Header + Plain-Text-Körper) zu den IMAP-UIDs."""
    conn = _imap_connect(cfg)
    try:
        conn.select("INBOX", readonly=True)
        out: list[dict] = []
        for uid in uids:
            u = uid.encode() if isinstance(uid, str) else uid
            typ, mdata = conn.uid("fetch", u, "(BODY.PEEK[])")
            if typ != "OK":
                continue
            raw = next((p[1] for p in mdata if isinstance(p, tuple)), b"")
            if not raw:
                continue
            msg = email.message_from_bytes(raw)
            out.append(_msg_to_dict(msg, str(uid)))
        return out
    finally:
        try:
            conn.logout()
        except Exception:
            pass


def _msg_to_dict(msg: email.message.Message, uid: str) -> dict:
    return {
        "uid": uid,
        "from": _decode(msg.get("From", "")),
        "to": _decode(msg.get("To", "")),
        "subject": _decode(msg.get("Subject", "(kein Betreff)")),
        "date": _decode(msg.get("Date", "")),
        "text": _body_text(msg).strip()[:_MAX_BODY],
    }


def _pop3_list(cfg: dict, limit: int = 25, search: str = "") -> list[dict]:
    """Listet die neuesten POP3-Mails (nur Header via TOP). Stabile UIDL-Kennung.
    Suchbegriff filtert clientseitig über Absender/Betreff."""
    conn = _pop3_connect(cfg)
    try:
        uidls = _pop3_uidl_map(conn)        # älteste→neueste
        uidls = uidls[::-1]                 # neueste zuerst
        scan = max(limit, 150) if search else limit
        s = search.lower()
        out: list[dict] = []
        for num, uid in uidls[:scan]:
            try:
                # TOP <num> 0 → nur Header (keine Körperzeilen)
                lines = conn.top(num, 0)[1]
            except Exception:
                continue
            hdr = email.message_from_bytes(b"\n".join(lines))
            frm = _decode(hdr.get("From", ""))
            subj = _decode(hdr.get("Subject", "(kein Betreff)"))
            if s and s not in frm.lower() and s not in subj.lower():
                continue
            out.append({
                "uid": uid,
                "from": frm,
                "subject": subj,
                "date": _decode(hdr.get("Date", "")),
            })
            if len(out) >= limit:
                break
        return out
    finally:
        try:
            conn.quit()
        except Exception:
            pass


def _pop3_fetch(cfg: dict, uids: list[str]) -> list[dict]:
    """Holt die vollständigen POP3-Mails zu den UIDL-Kennungen."""
    conn = _pop3_connect(cfg)
    try:
        wanted = set(uids)
        num_by_uid = {uid: num for num, uid in _pop3_uidl_map(conn) if uid in wanted}
        out: list[dict] = []
        for uid in uids:
            num = num_by_uid.get(uid)
            if not num:
                continue
            try:
                raw = b"\n".join(conn.retr(num)[1])
            except Exception:
                continue
            if not raw:
                continue
            out.append(_msg_to_dict(email.message_from_bytes(raw), uid))
        return out
    finally:
        try:
            conn.quit()
        except Exception:
            pass
