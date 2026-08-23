"""
Postfach-/Mailstore-Leser für AI_Framework_Thomas (Tab „📮 Postfach").

Liest Mail-Container ein und liefert je Nachricht ein einheitliches dict:
    {mid, folder, sender, recipients, cc, subject, date, body, attachments:[{name, ext, size, rel}]}

Formate:
- ``.pst``  → drei Wege, in dieser Reihenfolge: (1) installiertes **Outlook per COM**
              (Windows, ``pywin32``, permissiv), (2) optional ``pypff`` (Paket
              ``libpff-python``, LGPL — dokumentierte Ausnahme zur „kein-Copyleft"-Regel, nur
              für PST-Import), (3) der **eingebaute Reiner-Python-Leser** ``tools.pst_pure``
              (MIT, ohne Fremdbibliothek, Unicode-PST). Damit ist ``.pst`` immer lesbar.
              Hinweis: klassische PST-„Passwörter" sind nicht kryptografisch (nur eine
              CRC-Prüfsumme) — der Inhalt ist auch ohne Passwort lesbar; die Eingabe wird
              gegen die gespeicherte CRC nur **verifiziert** (``pst_password_status``).
- ``.mbox`` → stdlib ``mailbox``
- ``.eml``  → stdlib ``email``
- ``.msg``  → ``extract-msg`` (BSD)

Alle Funktionen sind synchron und defensiv (fehlerhafte Einzel-Mails werden übersprungen,
nicht die ganze Datei). Anhänge werden — falls ``att_dir`` übergeben — als Dateien abgelegt;
ihr Inhalt wird erst in Analysestufe 2 gelesen (tools.files.extract bzw. Bild ans Vision-LLM).
"""
from __future__ import annotations

import re
from email import message_from_binary_file, message_from_string
from email.message import Message
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Optional


# ── Verfügbarkeit der optionalen Reader ──────────────────────────────────────
def _has_pypff() -> bool:
    try:
        import pypff  # noqa: F401
        return True
    except Exception:
        return False


def _has_extract_msg() -> bool:
    try:
        import extract_msg  # noqa: F401
        return True
    except Exception:
        return False


def _has_outlook() -> bool:
    """Windows + installiertes Outlook (COM) — der native, permissiv lizenzierte Weg
    für .pst auf Windows (kein libpff/kein C-Build nötig)."""
    import sys
    if sys.platform != "win32":
        return False
    try:
        import win32com.client  # noqa: F401
        import pythoncom  # noqa: F401
    except Exception:
        return False
    try:
        import pythoncom
        pythoncom.CoInitialize()
        try:
            import win32com.client
            win32com.client.Dispatch("Outlook.Application")
            return True
        finally:
            pythoncom.CoUninitialize()
    except Exception:
        return False


def _pst_reader_name() -> str:
    """Welcher .pst-Reader auf diesem System greift (für UI-Hinweise): 'outlook',
    'libpff' oder 'pure' (eingebauter reiner-Python-Leser, immer verfügbar für Unicode-PST)."""
    if _has_outlook():
        return "outlook"
    if _has_pypff():
        return "libpff"
    return "pure"


def available_formats() -> dict:
    """Welche Eingabeformate auf diesem System nutzbar sind (für die UI-Hinweise)."""
    reader = _pst_reader_name()
    return {
        "pst": True,            # dank eingebautem Reiner-Python-Leser immer verfügbar
        "pst_reader": reader,   # 'outlook' | 'libpff' | 'pure'
        "mbox": True,
        "eml": True,
        "msg": _has_extract_msg(),
    }


# ── Hilfen ───────────────────────────────────────────────────────────────────
_SAFE = re.compile(r"[^\w.\-]+")


def _safe_name(name: str, fallback: str) -> str:
    name = _SAFE.sub("_", (name or "").strip()).strip("._") or fallback
    return name[:120]


def _norm_date(val) -> str:
    if not val:
        return ""
    try:
        if hasattr(val, "isoformat"):
            return val.isoformat()
        return str(parsedate_to_datetime(str(val)).isoformat())
    except Exception:
        return str(val)


def _clean(s) -> str:
    return " ".join(str(s or "").split())


def _save_attachment(att_dir: Optional[Path], mid: str, idx: int, name: str, data: bytes) -> dict:
    """Anhang-Metadaten (+ optional als Datei ablegen). ``rel`` = relativer Pfad unter att_dir."""
    ext = (Path(name).suffix or "").lower().lstrip(".")
    meta = {"name": name or f"anhang_{idx}", "ext": ext, "size": len(data or b""), "rel": ""}
    if att_dir is not None and data:
        sub = att_dir / mid
        sub.mkdir(parents=True, exist_ok=True)
        fname = f"{idx:02d}_{_safe_name(name, f'anhang_{idx}')}"
        (sub / fname).write_bytes(data)
        meta["rel"] = f"{mid}/{fname}"
    return meta


# ── E-Mail (email.message.Message) → dict ────────────────────────────────────
def _body_from_email(em: Message) -> str:
    """Bevorzugt text/plain; sonst HTML grob enttaggt."""
    plain, html = "", ""
    if em.is_multipart():
        for part in em.walk():
            ctype = part.get_content_type()
            disp = str(part.get("Content-Disposition") or "")
            if "attachment" in disp.lower():
                continue
            try:
                payload = part.get_payload(decode=True)
                if payload is None:
                    continue
                charset = part.get_content_charset() or "utf-8"
                text = payload.decode(charset, errors="replace")
            except Exception:
                continue
            if ctype == "text/plain" and not plain:
                plain = text
            elif ctype == "text/html" and not html:
                html = text
    else:
        try:
            payload = em.get_payload(decode=True)
            charset = em.get_content_charset() or "utf-8"
            plain = payload.decode(charset, errors="replace") if payload else str(em.get_payload())
        except Exception:
            plain = str(em.get_payload())
    if plain.strip():
        return plain
    if html:
        return re.sub(r"<[^>]+>", " ", html)
    return ""


def _atts_from_email(em: Message, att_dir, mid) -> list:
    out = []
    if not em.is_multipart():
        return out
    idx = 0
    for part in em.walk():
        disp = str(part.get("Content-Disposition") or "")
        fname = part.get_filename()
        if "attachment" not in disp.lower() and not fname:
            continue
        try:
            data = part.get_payload(decode=True) or b""
        except Exception:
            data = b""
        out.append(_save_attachment(att_dir, mid, idx, fname or f"anhang_{idx}", data))
        idx += 1
    return out


def _msg_from_email(em: Message, mid: str, folder: str, att_dir) -> dict:
    return {
        "mid": mid,
        "folder": folder,
        "sender": _clean(em.get("From")),
        "recipients": _clean(em.get("To")),
        "cc": _clean(em.get("Cc")),
        "subject": _clean(em.get("Subject")),
        "date": _norm_date(em.get("Date")),
        "body": _body_from_email(em).strip(),
        "attachments": _atts_from_email(em, att_dir, mid),
    }


# ── PST (pypff) ──────────────────────────────────────────────────────────────
def _pst_attachments(message, att_dir, mid) -> list:
    out = []
    try:
        n = message.number_of_attachments
    except Exception:
        return out
    for i in range(n):
        try:
            att = message.get_attachment(i)
            size = getattr(att, "size", 0) or 0
            data = att.read_buffer(size) if size else b""
            name = ""
            for getter in ("get_name", "get_long_filename", "get_filename"):
                try:
                    name = getattr(att, getter)() or ""
                    if name:
                        break
                except Exception:
                    pass
            out.append(_save_attachment(att_dir, mid, i, name or f"anhang_{i}", data or b""))
        except Exception:
            continue
    return out


def _pst_message(message, mid: str, folder: str, att_dir) -> dict:
    # Kopfzeilen (falls vorhanden) für From/To/Cc parsen, sonst Einzel-Getter.
    sender = recipients = cc = subject = date = ""
    body = ""
    try:
        headers = message.get_transport_headers()
    except Exception:
        headers = None
    if headers:
        try:
            em = message_from_string(headers)
            sender = _clean(em.get("From"))
            recipients = _clean(em.get("To"))
            cc = _clean(em.get("Cc"))
            subject = _clean(em.get("Subject"))
            date = _norm_date(em.get("Date"))
        except Exception:
            pass
    for attr, setter in (("get_subject", "subject"), ("get_sender_name", "sender")):
        try:
            val = getattr(message, attr)()
            if val and not locals().get(setter):
                if setter == "subject":
                    subject = _clean(val)
                else:
                    sender = _clean(val)
        except Exception:
            pass
    if not date:
        for attr in ("get_client_submit_time", "get_delivery_time"):
            try:
                date = _norm_date(getattr(message, attr)())
                if date:
                    break
            except Exception:
                pass
    for attr in ("get_plain_text_body", "get_html_body", "get_rtf_body"):
        try:
            raw = getattr(message, attr)()
            if raw:
                text = raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else str(raw)
                if attr == "get_html_body":
                    text = re.sub(r"<[^>]+>", " ", text)
                body = text.strip()
                if body:
                    break
        except Exception:
            continue
    return {
        "mid": mid, "folder": folder, "sender": sender, "recipients": recipients,
        "cc": cc, "subject": subject, "date": date, "body": body,
        "attachments": _pst_attachments(message, att_dir, mid),
    }


def _read_pst(path: Path, att_dir, limit: int) -> list:
    import pypff
    pff = pypff.file()
    pff.open(str(path))
    out: list = []
    counter = [0]

    def _walk(folder, folder_name):
        if len(out) >= limit:
            return
        try:
            nmsg = folder.number_of_sub_messages
        except Exception:
            nmsg = 0
        for i in range(nmsg):
            if len(out) >= limit:
                return
            try:
                message = folder.get_sub_message(i)
                mid = f"m{counter[0]}"
                counter[0] += 1
                out.append(_pst_message(message, mid, folder_name, att_dir))
            except Exception:
                continue
        try:
            nsub = folder.number_of_sub_folders
        except Exception:
            nsub = 0
        for j in range(nsub):
            try:
                sub = folder.get_sub_folder(j)
                sname = ""
                try:
                    sname = sub.get_name() or ""
                except Exception:
                    pass
                _walk(sub, f"{folder_name}/{sname}" if folder_name else sname)
            except Exception:
                continue

    try:
        _walk(pff.get_root_folder(), "")
    finally:
        try:
            pff.close()
        except Exception:
            pass
    return out


# ── PST über eingebauten Reiner-Python-Leser (tools.pst_pure, MIT, keine Deps) ─
def _read_pst_pure(path: Path, att_dir, limit: int) -> list:
    from . import pst_pure
    out: list = []
    with pst_pure.PST(str(path)) as pst:
        for i, m in enumerate(pst.read_all_messages(limit=limit)):
            mid = f"m{i}"
            atts = []
            for j, a in enumerate(m.get("attachments") or []):
                atts.append(_save_attachment(att_dir, mid, j, a.get("name") or f"anhang_{j}",
                                              a.get("data") or b""))
            sender = _clean(m.get("sender"))
            email = _clean(m.get("sender_email"))
            if email and email.lower() != sender.lower() and "@" in email:
                sender = f"{sender} <{email}>" if sender else email
            out.append({
                "mid": mid,
                "folder": "",
                "sender": sender,
                "recipients": _clean(m.get("to")),
                "cc": _clean(m.get("cc")),
                "subject": _clean(m.get("subject")),
                "date": _norm_date(m.get("date")),
                "body": (m.get("body") or "").strip(),
                "attachments": atts,
            })
    return out


def pst_password_status(path, password: Optional[str] = None) -> dict:
    """Passwort-Info für die UI: hat die .pst ein Passwort und passt die Eingabe?
    Nutzt den eingebauten Leser (funktioniert auch, wenn Outlook/libpff aktiv sind).
    Der Inhalt ist unabhängig davon lesbar (PST-Passwörter verschlüsseln nicht)."""
    try:
        from . import pst_pure
        with pst_pure.PST(str(path)) as pst:
            ok = pst.verify_password(password)
        return {"protected": ok is not None, "verified": bool(ok), "checked": ok is not None}
    except Exception:
        return {"protected": False, "verified": False, "checked": False}


# ── PST über Outlook-COM (Windows, pywin32) ─────────────────────────────────
def _outlook_date(val) -> str:
    try:
        if hasattr(val, "isoformat"):
            return val.isoformat()
    except Exception:
        pass
    try:
        return str(val)
    except Exception:
        return ""


def _outlook_attachments(item, att_dir, mid) -> list:
    out = []
    try:
        atts = item.Attachments
        n = atts.Count
    except Exception:
        return out
    for i in range(1, n + 1):  # COM-Collections sind 1-basiert
        try:
            att = atts.Item(i)
            name = ""
            for prop in ("FileName", "DisplayName"):
                try:
                    name = getattr(att, prop) or ""
                    if name:
                        break
                except Exception:
                    pass
            name = name or f"anhang_{i}"
            meta = {"name": name, "ext": (Path(name).suffix or "").lower().lstrip("."), "size": 0, "rel": ""}
            if att_dir is not None:
                sub = att_dir / mid
                sub.mkdir(parents=True, exist_ok=True)
                fname = f"{i:02d}_{_safe_name(name, f'anhang_{i}')}"
                try:
                    att.SaveAsFile(str(sub / fname))
                    meta["size"] = (sub / fname).stat().st_size
                    meta["rel"] = f"{mid}/{fname}"
                except Exception:
                    pass
            out.append(meta)
        except Exception:
            continue
    return out


def _read_pst_outlook(path: Path, att_dir, limit: int) -> list:
    """Liest .pst über das installierte Outlook (COM). Fügt den Store temporär hinzu,
    liest alle Mail-Elemente und entfernt den Store wieder. Läuft im Worker-Thread →
    COM muss dort initialisiert werden."""
    import pythoncom
    import win32com.client

    pythoncom.CoInitialize()
    out: list = []
    counter = [0]
    ns = None
    store_root = None
    try:
        outlook = win32com.client.Dispatch("Outlook.Application")
        ns = outlook.GetNamespace("MAPI")
        target = str(Path(path).resolve())
        ns.AddStore(target)
        # Den gerade hinzugefügten Store finden (Abgleich über den Dateipfad).
        for i in range(1, ns.Folders.Count + 1):
            try:
                root = ns.Folders.Item(i)
                store = root.Store
                fp = getattr(store, "FilePath", "") or ""
                if fp and Path(fp).resolve() == Path(target).resolve():
                    store_root = root
                    break
            except Exception:
                continue
        if store_root is None:   # Fallback: zuletzt hinzugefügter Top-Ordner
            store_root = ns.Folders.Item(ns.Folders.Count)

        def _walk(folder, folder_name):
            if len(out) >= limit:
                return
            try:
                items = folder.Items
                cnt = items.Count
            except Exception:
                cnt = 0
            for k in range(1, cnt + 1):
                if len(out) >= limit:
                    return
                try:
                    item = items.Item(k)
                    if getattr(item, "Class", 0) != 43:   # 43 = olMail
                        continue
                    mid = f"m{counter[0]}"; counter[0] += 1
                    try:
                        sender = getattr(item, "SenderName", "") or getattr(item, "SenderEmailAddress", "") or ""
                    except Exception:
                        sender = ""
                    out.append({
                        "mid": mid, "folder": folder_name,
                        "sender": _clean(sender),
                        "recipients": _clean(getattr(item, "To", "")),
                        "cc": _clean(getattr(item, "CC", "")),
                        "subject": _clean(getattr(item, "Subject", "")),
                        "date": _outlook_date(getattr(item, "ReceivedTime", None) or getattr(item, "SentOn", None)),
                        "body": str(getattr(item, "Body", "") or "").strip(),
                        "attachments": _outlook_attachments(item, att_dir, mid),
                    })
                except Exception:
                    continue
            try:
                subs = folder.Folders
                scnt = subs.Count
            except Exception:
                scnt = 0
            for s in range(1, scnt + 1):
                try:
                    sub = subs.Item(s)
                    sname = getattr(sub, "Name", "") or ""
                    _walk(sub, f"{folder_name}/{sname}" if folder_name else sname)
                except Exception:
                    continue

        _walk(store_root, getattr(store_root, "Name", "") or "")
    finally:
        try:
            if ns is not None and store_root is not None:
                ns.RemoveStore(store_root)
        except Exception:
            pass
        try:
            pythoncom.CoUninitialize()
        except Exception:
            pass
    return out


# ── Öffentliche API ──────────────────────────────────────────────────────────
class MailFormatUnavailable(Exception):
    pass


def _looks_like_mbox(path) -> bool:
    """Heuristik für endungslose Mbox-Dateien (z. B. Thunderbird „Local Folders": die
    Ordner heißen ``Inbox``/``Sent`` OHNE Endung). Eine Mbox beginnt mit der Trennzeile
    ``From `` (mit Leerzeichen) — im Gegensatz zum ``From:``-Header einer einzelnen .eml."""
    try:
        with open(path, "rb") as fh:
            head = fh.read(5)
        return head == b"From "
    except Exception:
        return False


def read_store(path, password: Optional[str] = None, att_dir=None, limit: int = 5000) -> list:
    """Liest einen Mail-Container und liefert die Nachrichten-dicts (siehe Modul-Docstring).
    ``att_dir`` (Path) legt Anhänge als Dateien ab. ``password`` wird — falls das Format es
    unterstützt — durchgereicht (bei klassischem PST i. d. R. nicht nötig).

    Neben den Endungen ``.pst``/``.mbox``/``.eml``/``.msg`` werden erkannt:
    - **endungslose Mbox-Dateien** (Thunderbird „Local Folders") per Inhalts-Heuristik,
    - **Maildir-Verzeichnisse** (Ordner mit ``cur``/``new``)."""
    path = Path(path)
    if att_dir is not None:
        att_dir = Path(att_dir)
        att_dir.mkdir(parents=True, exist_ok=True)

    # Maildir: ein Verzeichnis (mit cur/new). z. B. Dovecot; manche Thunderbird-Setups.
    if path.is_dir():
        import mailbox
        try:
            box = mailbox.Maildir(str(path), factory=None)
        except Exception as e:
            raise MailFormatUnavailable(f"Verzeichnis ist kein lesbares Maildir: {e}")
        out = []
        for i, em in enumerate(box):
            if i >= limit:
                break
            try:
                out.append(_msg_from_email(em, f"m{i}", "maildir", att_dir))
            except Exception:
                continue
        return out

    ext = path.suffix.lower()

    # Mbox: über die Endung ODER (endungslos/unbekannt) per Inhalts-Heuristik (Thunderbird).
    if ext == ".mbox" or (ext not in (".pst", ".eml", ".msg") and _looks_like_mbox(path)):
        import mailbox
        out = []
        box = mailbox.mbox(str(path))
        for i, em in enumerate(box):
            if i >= limit:
                break
            try:
                out.append(_msg_from_email(em, f"m{i}", "mbox", att_dir))
            except Exception:
                continue
        return out

    if ext == ".pst":
        # Bevorzugt Outlook-COM (Windows, nativ, permissiv) → sonst libpff (LGPL) →
        # sonst der eingebaute Reiner-Python-Leser (MIT, ohne Fremdbibliothek).
        if _has_outlook():
            try:
                return _read_pst_outlook(path, att_dir, limit)
            except Exception:
                pass  # Outlook-COM fehlgeschlagen → auf eingebauten Leser zurückfallen
        if _has_pypff():
            try:
                return _read_pst(path, att_dir, limit)
            except Exception:
                pass
        return _read_pst_pure(path, att_dir, limit)

    # (.mbox / endungslose Mbox wird bereits oben behandelt.)

    if ext == ".eml":
        with open(path, "rb") as fh:
            em = message_from_binary_file(fh)
        return [_msg_from_email(em, "m0", "eml", att_dir)]

    if ext == ".msg":
        if not _has_extract_msg():
            raise MailFormatUnavailable(".msg-Unterstützung fehlt (Paket extract-msg nicht installiert).")
        import extract_msg
        m = extract_msg.Message(str(path))
        atts = []
        for i, a in enumerate(getattr(m, "attachments", []) or []):
            try:
                data = a.data if isinstance(getattr(a, "data", None), (bytes, bytearray)) else b""
                name = getattr(a, "longFilename", None) or getattr(a, "shortFilename", None) or f"anhang_{i}"
                atts.append(_save_attachment(att_dir, "m0", i, name, data))
            except Exception:
                continue
        return [{
            "mid": "m0", "folder": "msg",
            "sender": _clean(getattr(m, "sender", "")),
            "recipients": _clean(getattr(m, "to", "")),
            "cc": _clean(getattr(m, "cc", "")),
            "subject": _clean(getattr(m, "subject", "")),
            "date": _norm_date(getattr(m, "date", "")),
            "body": str(getattr(m, "body", "") or "").strip(),
            "attachments": atts,
        }]

    raise MailFormatUnavailable(f"Nicht unterstütztes Format: {ext or '(ohne Endung)'}")
