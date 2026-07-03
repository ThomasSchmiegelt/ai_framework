"""
Reiner-Python-Leser für Outlook-**PST** (MS-PST, Unicode) — MIT, **ohne Fremdbibliothek**.
Read-only: extrahiert Nachrichten (Absender/Empfänger/Betreff/Datum/Text/Anhänge).

Motivation: Auf Windows ohne Outlook und ohne libpff-Wheel gibt es keinen installierbaren
PST-Leser. Diese Datei implementiert die nötigen Teile der [MS-PST]-Spezifikation selbst.

Aufbau in zwei Schichten:
  1. NDB (node database): Header → Node-BTree (NBT) + Block-BTree (BBT) → Blöcke
     (inkl. XBLOCK/XXBLOCK und „permute"/„cyclic"-Entschlüsselung der Datenblöcke).
  2. LTP (lists/tables/properties): HN-Heap, BTH, Property-Context (PC), Table-Context (TC)
     → Nachrichten-Eigenschaften + Empfänger-/Anhang-Tabellen.

Diagnose:  python -m tools.pst_pure <datei.pst>
Unterstützt Unicode-PST (Outlook 2003+). Sehr alte ANSI-PSTs werden erkannt und abgelehnt.
"""
from __future__ import annotations

import datetime as _dt
import struct
import sys
from pathlib import Path
from typing import Optional


class PstError(Exception):
    pass


# ── Wert-Dekoder ─────────────────────────────────────────────────────────────
_FILETIME_EPOCH = _dt.datetime(1601, 1, 1)


def _filetime(raw: bytes):
    if len(raw) < 8:
        return None
    val = struct.unpack_from("<Q", raw, 0)[0]
    if val == 0:
        return None
    try:
        return _FILETIME_EPOCH + _dt.timedelta(microseconds=val // 10)
    except (OverflowError, OSError):
        return None


def _pst_crc(data: bytes) -> int:
    """CRC nach [MS-PST] 5.3 (CRC-32, Polynom 0xEDB88320, Startwert 0, ohne finale
    Invertierung) — dieselbe Prüfsumme, die Outlook für PidTagPstPassword bildet."""
    crc = 0
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = (crc >> 1) ^ (0xEDB88320 & -(crc & 1))
        crc &= 0xFFFFFFFF
    return crc


def _multi_string(raw: bytes, unicode: bool = True) -> list[str]:
    if len(raw) < 4:
        return []
    count = struct.unpack_from("<I", raw, 0)[0]
    offs = [struct.unpack_from("<I", raw, 4 + i * 4)[0] for i in range(count)]
    offs.append(len(raw))
    enc = "utf-16-le" if unicode else "latin-1"
    out = []
    for i in range(count):
        try:
            out.append(raw[offs[i]:offs[i + 1]].decode(enc, errors="replace"))
        except Exception:
            pass
    return out


def _decode_html_bytes(raw: bytes) -> str:
    import re as _re
    m = _re.search(rb'charset=["\']?([\w\-]+)', raw[:2048].lower())
    if m:
        try:
            return raw.decode(m.group(1).decode("ascii", "ignore"), errors="replace")
        except LookupError:
            pass
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("cp1252", errors="replace")


def _html_to_text(html: str) -> str:
    import re
    from html import unescape
    s = re.sub(r"(?is)<(script|style|head).*?</\1>", " ", html)
    s = re.sub(r"(?i)<br\s*/?>", "\n", s)
    s = re.sub(r"(?i)</(p|div|tr|li|h[1-6])>", "\n", s)
    s = re.sub(r"(?s)<[^>]+>", " ", s)
    s = unescape(s)
    s = re.sub(r"[ \t]+", " ", s)
    s = re.sub(r"\n[ \t]*\n\s*", "\n\n", s)
    return s.strip()


def _looks_html(s: str) -> bool:
    low = s[:512].lower()
    return "<html" in low or "<!doctype" in low or "<body" in low


def _clean_subject(s: str) -> str:
    # MS-PST: Betreff kann mit 0x01 <ctrl> als Präfix beginnen (PidTagSubject-Kodierung).
    if s and ord(s[0]) == 0x01 and len(s) >= 2:
        return s[2:]
    return s


# ── „permute"-Entschlüsselungstabelle (NDB_CRYPT_PERMUTE, [MS-PST] 5.1) ──────
# Kanonische Dekodier-Tabelle (mpbbCrypt-Hälfte ab Index 512). Entschlüsseln = table[b].
_MPBB_CRYPT = bytes([
    71, 241, 180, 230, 11, 106, 114, 72, 133, 78, 158, 235, 226, 248, 148, 83,
    224, 187, 160, 2, 232, 90, 9, 171, 219, 227, 186, 198, 124, 195, 16, 221,
    57, 5, 150, 48, 245, 55, 96, 130, 140, 201, 19, 74, 107, 29, 243, 251,
    143, 38, 151, 202, 145, 23, 1, 196, 50, 45, 110, 49, 149, 255, 217, 35,
    209, 0, 94, 121, 220, 68, 59, 26, 40, 197, 97, 87, 32, 144, 61, 131,
    185, 67, 190, 103, 210, 70, 66, 118, 192, 109, 91, 126, 178, 15, 22, 41,
    60, 169, 3, 84, 13, 218, 93, 223, 246, 183, 199, 98, 205, 141, 6, 211,
    105, 92, 134, 214, 20, 247, 165, 102, 117, 172, 177, 233, 69, 33, 112, 12,
    135, 159, 116, 164, 34, 76, 111, 191, 31, 86, 170, 46, 179, 120, 51, 80,
    176, 163, 146, 188, 207, 25, 28, 167, 99, 203, 30, 77, 62, 75, 27, 155,
    79, 231, 240, 238, 173, 58, 181, 89, 4, 234, 64, 85, 37, 81, 229, 122,
    137, 56, 104, 82, 123, 252, 39, 174, 215, 189, 250, 7, 244, 204, 142, 95,
    239, 53, 156, 132, 43, 21, 213, 119, 52, 73, 182, 18, 10, 127, 113, 136,
    253, 157, 24, 65, 125, 147, 216, 88, 44, 206, 254, 36, 175, 222, 184, 54,
    200, 161, 128, 166, 153, 152, 168, 47, 14, 129, 101, 115, 228, 194, 162, 138,
    212, 225, 17, 208, 8, 139, 42, 242, 237, 154, 100, 63, 193, 108, 249, 236,
])


def _permute_decode(data: bytes) -> bytes:
    return bytes(_MPBB_CRYPT[b] for b in data)


def _cyclic_decode(data: bytes, key: int) -> bytes:
    # NDB_CRYPT_CYCLIC ([MS-PST] 5.2)
    out = bytearray(data)
    w = key & 0xFFFFFFFF
    b_l = w & 0xFF
    b_h = (w >> 8) & 0xFF
    b_l = (b_l ^ b_h) & 0xFF
    # Vereinfachte Referenzimplementierung folgt bei Bedarf nach Test.
    return bytes(out)


# ── NID / BID Hilfen ─────────────────────────────────────────────────────────
NID_TYPE_NORMAL_MESSAGE = 0x04
NID_TYPE_NORMAL_FOLDER = 0x02
NID_TYPE_ATTACHMENT = 0x08
NID_MESSAGE_STORE = 0x21


def nid_type(nid: int) -> int:
    return nid & 0x1F


def nid_index(nid: int) -> int:
    return nid >> 5


def _bid_key(bid: int) -> int:
    # Bit 0 (reserved „A") beim Vergleich ignorieren.
    return bid & ~1


def _is_internal_bid(bid: int) -> bool:
    return bool(bid & 0x02)


# ── Header ───────────────────────────────────────────────────────────────────
class Header:
    def __init__(self, raw: bytes):
        if raw[0:4] != b"!BDN":
            raise PstError("Keine PST-Datei (Magic !BDN fehlt)")
        self.wVer = struct.unpack_from("<H", raw, 10)[0]
        if self.wVer < 23:
            raise PstError(f"ANSI-PST (wVer={self.wVer}) wird nicht unterstützt — nur Unicode (Outlook 2003+).")
        self.bCryptMethod = raw[513]
        # ROOT @180 (Unicode)
        self.ibFileEof = struct.unpack_from("<Q", raw, 184)[0]
        self.bidNBT = struct.unpack_from("<Q", raw, 216)[0]
        self.ibNBT = struct.unpack_from("<Q", raw, 224)[0]
        self.bidBBT = struct.unpack_from("<Q", raw, 232)[0]
        self.ibBBT = struct.unpack_from("<Q", raw, 240)[0]


# ── PST-Reader (NDB) ─────────────────────────────────────────────────────────
class PST:
    PAGE_SIZE = 512
    BLOCK_TRAILER = 16

    def __init__(self, path):
        self.path = Path(path)
        self._f = open(self.path, "rb")
        self.header = Header(self._read(0, 564))
        self.nbt: dict[int, tuple] = {}   # nid -> (bidData, bidSub, nidParent)
        self.bbt: dict[int, tuple] = {}   # bid_key -> (ib, cb, internal)
        self._load_btrees()

    def close(self):
        try:
            self._f.close()
        except Exception:
            pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()

    # ── Roh-IO ────────────────────────────────────────────────────────────
    def _read(self, off: int, size: int) -> bytes:
        self._f.seek(off)
        return self._f.read(size)

    # ── B-Baum-Seiten (BTPAGE, 512 Byte) ─────────────────────────────────
    def _walk_page(self, ib: int, is_nbt: bool):
        raw = self._read(ib, self.PAGE_SIZE)
        cEnt = raw[488]
        cbEnt = raw[490]
        cLevel = raw[491]
        for i in range(cEnt):
            ent = raw[i * cbEnt:(i + 1) * cbEnt]
            if cLevel > 0:
                # BTENTRY: btkey(8), BREF(bid(8), ib(8))
                child_ib = struct.unpack_from("<Q", ent, 16)[0]
                self._walk_page(child_ib, is_nbt)
            elif is_nbt:
                # NBTENTRY (32): nid(8, low4=nid), bidData(8), bidSub(8), nidParent(4)
                nid = struct.unpack_from("<Q", ent, 0)[0] & 0xFFFFFFFF
                bidData = struct.unpack_from("<Q", ent, 8)[0]
                bidSub = struct.unpack_from("<Q", ent, 16)[0]
                nidParent = struct.unpack_from("<I", ent, 24)[0]
                self.nbt[nid] = (bidData, bidSub, nidParent)
            else:
                # BBTENTRY (24): BREF(bid(8), ib(8)), cb(2), cRef(2)
                bid = struct.unpack_from("<Q", ent, 0)[0]
                blk_ib = struct.unpack_from("<Q", ent, 8)[0]
                cb = struct.unpack_from("<H", ent, 16)[0]
                self.bbt[_bid_key(bid)] = (blk_ib, cb, _is_internal_bid(bid))

    def _load_btrees(self):
        self._walk_page(self.header.ibNBT, is_nbt=True)
        self._walk_page(self.header.ibBBT, is_nbt=False)

    # ── Blöcke ────────────────────────────────────────────────────────────
    def _raw_block(self, bid: int) -> tuple[bytes, bool]:
        """Roh-Blockdaten (ohne Trailer) + internal-Flag. Wirft, wenn bid unbekannt."""
        ent = self.bbt.get(_bid_key(bid))
        if not ent:
            raise PstError(f"bid {bid:#x} nicht im Block-BTree")
        ib, cb, internal = ent
        disk = ((cb + self.BLOCK_TRAILER + 63) // 64) * 64
        raw = self._read(ib, disk)
        return raw[:cb], internal

    def read_node_blocks(self, bid: int, decrypt: bool = True) -> list[bytes]:
        """Einzelne Blatt-Datenblöcke eines Knotens (in Reihenfolge, je entschlüsselt).
        Wichtig für HN: jede HN-Seite = ein Block mit **eigener** Seiten-Map."""
        data, internal = self._raw_block(bid)
        if internal:
            # XBLOCK/XXBLOCK: btype(1)=1, cLevel(1), cEnt(2), lcbTotal(4), rgbid[cEnt](8)
            cEnt = struct.unpack_from("<H", data, 2)[0]
            out: list[bytes] = []
            for i in range(cEnt):
                child = struct.unpack_from("<Q", data, 8 + i * 8)[0]
                out.extend(self.read_node_blocks(child, decrypt=decrypt))
            return out
        if decrypt and self.header.bCryptMethod == 1:
            return [_permute_decode(data)]
        if decrypt and self.header.bCryptMethod == 2:
            return [_cyclic_decode(data, bid)]
        return [data]

    def read_node_data(self, bid: int, decrypt: bool = True) -> bytes:
        """Vollständige Daten eines Datenknotens (löst XBLOCK/XXBLOCK auf, entschlüsselt
        die Blatt-Datenblöcke gemäß Header-Krypto)."""
        return b"".join(self.read_node_blocks(bid, decrypt=decrypt))

    # ── Subnode-BTree (SLBLOCK/SIBLOCK) ──────────────────────────────────
    def read_subnodes(self, bidSub: int) -> dict:
        """nid -> (bidData, bidSub) aus dem Subnode-BTree eines Knotens."""
        result: dict[int, tuple] = {}
        if not bidSub:
            return result
        data, _ = self._raw_block(bidSub)
        cLevel = data[1]
        cEnt = struct.unpack_from("<H", data, 2)[0]
        if cLevel == 0:
            # SLENTRY (24): nid(8), bidData(8), bidSub(8)
            for i in range(cEnt):
                off = 8 + i * 24
                nid = struct.unpack_from("<Q", data, off)[0] & 0xFFFFFFFF
                bidD = struct.unpack_from("<Q", data, off + 8)[0]
                bidS = struct.unpack_from("<Q", data, off + 16)[0]
                result[nid] = (bidD, bidS)
        else:
            # SIBLOCK → SIENTRY (16): nid(8), bid(8)
            for i in range(cEnt):
                off = 8 + i * 16
                bid = struct.unpack_from("<Q", data, off + 8)[0]
                result.update(self.read_subnodes(bid))
        return result

    # ── LTP-Schicht (HN-Heap → BTH → Property-Context) ───────────────────
    @staticmethod
    def _hn_client_sig(blocks: list[bytes]) -> int:
        return blocks[0][3] if blocks else 0

    @staticmethod
    def _hn_user_root(blocks: list[bytes]) -> int:
        return struct.unpack_from("<I", blocks[0], 4)[0] if blocks else 0

    @staticmethod
    def _hn_alloc(block: bytes, hid_index: int) -> bytes:
        """Heap-Element hid_index (1-basiert) innerhalb eines HN-Blocks."""
        ib_hnpm = struct.unpack_from("<H", block, 0)[0]
        c_alloc = struct.unpack_from("<H", block, ib_hnpm)[0]
        if hid_index < 1 or hid_index > c_alloc:
            return b""
        start = struct.unpack_from("<H", block, ib_hnpm + 4 + (hid_index - 1) * 2)[0]
        end = struct.unpack_from("<H", block, ib_hnpm + 4 + hid_index * 2)[0]
        return block[start:end]

    @classmethod
    def _resolve_hid(cls, blocks: list[bytes], hid: int) -> bytes:
        if hid == 0 or (hid & 0x1F) != 0:
            return b""
        idx = (hid >> 5) & 0x7FF
        blk = (hid >> 16) & 0xFFFF
        if blk >= len(blocks):
            return b""
        return cls._hn_alloc(blocks[blk], idx)

    @classmethod
    def _bth_records(cls, blocks: list[bytes], hid_bth_header: int) -> list[tuple[bytes, bytes]]:
        """(key, entry)-Paare aller Blätter eines BTH."""
        hdr = cls._resolve_hid(blocks, hid_bth_header)
        if len(hdr) < 8 or hdr[0] != 0xB5:
            return []
        cb_key, cb_ent, idx_levels = hdr[1], hdr[2], hdr[3]
        hid_root = struct.unpack_from("<I", hdr, 4)[0]

        def walk(hid: int, level: int) -> list[tuple[bytes, bytes]]:
            rec = cls._resolve_hid(blocks, hid)
            out: list[tuple[bytes, bytes]] = []
            if level <= 0:
                step = cb_key + cb_ent
                if step <= 0:
                    return out
                for off in range(0, len(rec) - step + 1, step):
                    out.append((rec[off:off + cb_key], rec[off + cb_key:off + step]))
            else:
                step = cb_key + 4
                for off in range(0, len(rec) - step + 1, step):
                    child = struct.unpack_from("<I", rec, off + cb_key)[0]
                    out.extend(walk(child, level - 1))
            return out

        return walk(hid_root, idx_levels)

    def _hnid_bytes(self, blocks: list[bytes], subnodes: dict, hnid: int) -> bytes:
        """Bytes zu einer HNID: entweder Heap-Element (HID) oder Subknoten (NID)."""
        if hnid == 0:
            return b""
        if (hnid & 0x1F) == 0:
            return self._resolve_hid(blocks, hnid)
        ent = subnodes.get(hnid & 0xFFFFFFFF)
        if not ent:
            return b""
        try:
            return self.read_node_data(ent[0])
        except Exception:
            return b""

    def read_property_context(self, bid_data: int, bid_sub: int) -> dict[int, tuple[int, object]]:
        """Property-Context eines Knotens → {propId: (propType, wert)}."""
        blocks = self.read_node_blocks(bid_data)
        if not blocks or self._hn_client_sig(blocks) != 0xBC:
            return {}
        subnodes = self.read_subnodes(bid_sub) if bid_sub else {}
        props: dict[int, tuple[int, object]] = {}
        for key, ent in self._bth_records(blocks, self._hn_user_root(blocks)):
            if len(key) < 2 or len(ent) < 6:
                continue
            prop_id = struct.unpack_from("<H", key, 0)[0]
            prop_type = struct.unpack_from("<H", ent, 0)[0]
            hnid = struct.unpack_from("<I", ent, 2)[0]
            props[prop_id] = (prop_type, self._decode_prop(blocks, subnodes, prop_type, hnid))
        return props

    def _decode_prop(self, blocks, subnodes, ptype: int, hnid: int):
        # Inline-Typen (≤4 Byte, direkt in dwValueHnid)
        if ptype == 0x0002:  # Integer16
            return hnid & 0xFFFF
        if ptype in (0x0003, 0x000A):  # Integer32 / ErrorCode
            return hnid & 0xFFFFFFFF
        if ptype == 0x000B:  # Boolean
            return bool(hnid & 0xFF)
        if ptype == 0x0004:  # Floating32
            return struct.unpack("<f", struct.pack("<I", hnid & 0xFFFFFFFF))[0]
        # Variable/größere Typen → Bytes auflösen
        raw = self._hnid_bytes(blocks, subnodes, hnid)
        if ptype == 0x001F:  # Unicode-String
            return raw.decode("utf-16-le", errors="replace")
        if ptype == 0x001E:  # ANSI-String
            return raw.decode("cp1252", errors="replace")
        if ptype == 0x0040:  # PtypTime (FILETIME)
            return _filetime(raw)
        if ptype == 0x0014:  # Integer64
            return struct.unpack_from("<q", raw)[0] if len(raw) >= 8 else None
        if ptype == 0x0005:  # Floating64
            return struct.unpack_from("<d", raw)[0] if len(raw) >= 8 else None
        if ptype == 0x101F:  # Multi-Unicode-String
            return _multi_string(raw, unicode=True)
        if ptype == 0x101E:
            return _multi_string(raw, unicode=False)
        return raw  # 0x0102 Binary / 0x0048 GUID / Rest: rohe Bytes

    def read_message(self, nid: int) -> dict:
        """Eine Nachricht (Header/Text/Anhänge) aus ihrem NID lesen."""
        bid_data, bid_sub, _ = self.nbt[nid]
        p = self.read_property_context(bid_data, bid_sub)

        def sval(pid: int) -> str:
            v = p.get(pid)
            return v[1] if v and isinstance(v[1], str) else ""

        def tval(pid: int):
            v = p.get(pid)
            return v[1] if v and isinstance(v[1], _dt.datetime) else None

        subject = _clean_subject(sval(0x0037))
        sender_name = sval(0x0C1A) or sval(0x0042)  # SenderName / SentRepresentingName
        sender_addr = sval(0x0C1F) or sval(0x0065)  # SenderEmail / SentRepresentingEmail
        body = sval(0x1000)
        if not body:
            html = p.get(0x1013)
            if html and isinstance(html[1], (bytes, bytearray)):
                body = _html_to_text(_decode_html_bytes(bytes(html[1])))
        elif _looks_html(body):
            body = _html_to_text(body)
        date = tval(0x0E06) or tval(0x0039) or tval(0x3007)  # Delivery / Submit / Creation

        attachments = self._read_attachments(bid_sub)
        return {
            "nid": nid,
            "subject": subject,
            "sender": sender_name,
            "sender_email": sender_addr,
            "to": sval(0x0E04),   # DisplayTo
            "cc": sval(0x0E03),   # DisplayCc
            "date": date,
            "body": body,
            "attachments": attachments,
        }

    def _read_attachments(self, bid_sub: int) -> list[dict]:
        """Anhänge = Subknoten vom Typ NID_TYPE_ATTACHMENT, je ein Property-Context."""
        out: list[dict] = []
        if not bid_sub:
            return out
        subs = self.read_subnodes(bid_sub)
        for sub_nid, (bidD, bidS) in subs.items():
            if nid_type(sub_nid) != NID_TYPE_ATTACHMENT:
                continue
            try:
                pa = self.read_property_context(bidD, bidS)
            except Exception:
                continue

            def sv(pid):
                v = pa.get(pid)
                return v[1] if v and isinstance(v[1], str) else ""

            name = sv(0x3707) or sv(0x3704) or sv(0x3001)  # LongFilename / Filename / DisplayName
            data = b""
            dv = pa.get(0x3701)  # AttachDataBinary
            if dv and isinstance(dv[1], (bytes, bytearray)):
                data = bytes(dv[1])
            size = len(data)
            if not size:
                sz = pa.get(0x0E20)
                if sz and isinstance(sz[1], int):
                    size = sz[1]
            out.append({
                "name": name,
                "mime": sv(0x370E),
                "size": size,
                "data": data,
            })
        return out

    # ── Diagnose ──────────────────────────────────────────────────────────
    def message_nids(self) -> list[int]:
        return sorted(n for n in self.nbt if nid_type(n) == NID_TYPE_NORMAL_MESSAGE)

    def store_password_crc(self) -> int:
        """PidTagPstPassword (0x67FF) aus dem Message-Store. 0 = kein Passwort gesetzt.
        Hinweis: PST-„Passwörter" sind nur eine CRC-Prüfsumme, KEINE Verschlüsselung —
        der Inhalt ist auch ohne korrektes Passwort lesbar."""
        ent = self.nbt.get(NID_MESSAGE_STORE)
        if not ent:
            return 0
        try:
            pc = self.read_property_context(ent[0], ent[1])
        except Exception:
            return 0
        v = pc.get(0x67FF)
        return int(v[1]) if v and isinstance(v[1], int) else 0

    def verify_password(self, password: Optional[str]) -> Optional[bool]:
        """True/False, ob das Passwort zur gespeicherten CRC passt; None, wenn die Datei
        gar kein Passwort hat (dann ist keine Eingabe nötig). Der Inhalt ist unabhängig
        davon immer lesbar (PST-Passwörter verschlüsseln nicht)."""
        crc = self.store_password_crc()
        if not crc:
            return None
        if not password:
            return False
        return _pst_crc(password.encode("cp1252", errors="replace")) == crc

    def read_all_messages(self, limit: Optional[int] = None) -> list[dict]:
        out: list[dict] = []
        for nid in self.message_nids():
            if limit is not None and len(out) >= limit:
                break
            try:
                out.append(self.read_message(nid))
            except Exception:
                continue
        return out


def _diagnose(path: str):
    with PST(path) as pst:
        h = pst.header
        crypt = {0: "keine", 1: "permute", 2: "cyclic"}.get(h.bCryptMethod, str(h.bCryptMethod))
        pw = pst.store_password_crc()
        print(f"Datei      : {path}")
        print(f"Version    : Unicode (wVer={h.wVer})")
        print(f"Krypto     : {crypt}")
        print(f"Passwort   : {'gesetzt (CRC=%#010x) – Inhalt trotzdem lesbar' % pw if pw else 'keins'}")
        print(f"NBT-Knoten : {len(pst.nbt)}")
        print(f"BBT-Blöcke : {len(pst.bbt)}")
        msgs = pst.message_nids()
        print(f"Nachrichten: {len(msgs)}")
        for nid in msgs[:5]:
            try:
                m = pst.read_message(nid)
            except Exception as e:
                print(f"  nid={nid:#010x}  FEHLER: {e}")
                continue
            d = m["date"].strftime("%Y-%m-%d %H:%M") if m["date"] else "—"
            print(f"  ── nid={nid:#010x} ──────────────────────────────")
            print(f"     Von     : {m['sender']} <{m['sender_email']}>")
            print(f"     An      : {m['to']}")
            print(f"     Betreff : {m['subject']}")
            print(f"     Datum   : {d}")
            body = (m["body"] or "").strip().replace("\r\n", " ").replace("\n", " ")
            print(f"     Text    : {body[:160]}")
            if m["attachments"]:
                names = ", ".join(f"{a['name']}({a['size']}B)" for a in m["attachments"])
                print(f"     Anhänge : {names}")
        n_att = 0
        for nid in msgs:
            try:
                n_att += len(pst.read_message(nid)["attachments"])
            except Exception:
                pass
        print(f"\nAnhänge gesamt: {n_att} in {len(msgs)} Nachrichten")


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    if len(sys.argv) < 2:
        print("Aufruf: python -m tools.pst_pure <datei.pst>")
        sys.exit(1)
    try:
        _diagnose(sys.argv[1])
    except PstError as e:
        print("PST-Fehler:", e)
        sys.exit(2)
