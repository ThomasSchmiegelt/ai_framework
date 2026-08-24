"""Handy-Kopplung per QR-Code (WLAN).

Zeigt die im lokalen Netz erreichbaren Adressen des Rechners an und liefert dazu
QR-Codes (rein lokal erzeugt, ``tools/qrcode_pure.py`` – keine Fremd-Abhängigkeit,
nichts verlässt den Rechner). Das Handy scannt den Code und öffnet die Oberfläche
im selben WLAN. Zwei Ziele: **gesamtes Tool** oder **nur Chat (Assistent)**
(``?assistant=1`` – die Oberfläche blendet dann alle Tabs außer Chat aus).

Keine neue Server-Funktion nötig: der Zugang läuft über die ohnehin ausgelieferte
Web-Oberfläche. Diese Routen liefern nur Adressen + QR-Bild.
"""
import socket
import ipaddress

from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import Response

from core import _active_mode  # noqa: F401  (nur um „from core import *"-Konvention zu folgen)
from tools import qrcode_pure

router = APIRouter()


def _candidate_ips() -> list[str]:
    """Alle plausiblen IPv4-Adressen dieses Rechners im lokalen Netz (ohne Loopback)."""
    ips: set[str] = set()
    # 1) Primäre Route nach außen (funktioniert auch, wenn das Handy den Hotspot stellt).
    for probe in ("8.8.8.8", "192.168.1.1", "10.0.0.1"):
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.settimeout(0.3)
            s.connect((probe, 80))
            ips.add(s.getsockname()[0])
            s.close()
        except Exception:
            pass
    # 2) Alle Adressen des Hostnamens (mehrere Adapter, z. B. WLAN + Hotspot).
    try:
        host = socket.gethostname()
        for info in socket.getaddrinfo(host, None, socket.AF_INET):
            ips.add(info[4][0])
    except Exception:
        pass
    # Filter: nur private/link-lokale IPv4, kein Loopback.
    out = []
    for ip in ips:
        try:
            addr = ipaddress.ip_address(ip)
        except ValueError:
            continue
        if addr.is_loopback or not addr.is_private:
            continue
        out.append(ip)
    # Übliche Heimnetz-Bereiche (192.168.*) zuerst, dann Hotspot-Bereiche.
    out.sort(key=lambda x: (not x.startswith("192.168."), x))
    return out


def _base(request: Request) -> tuple[str, int]:
    """Schema (http/https) + Port, unter dem der Server gerade ausgeliefert wird."""
    scheme = request.url.scheme or "http"
    port = request.url.port or (443 if scheme == "https" else 80)
    return scheme, port


def _url_for(scheme: str, ip: str, port: int, assistant: bool) -> str:
    tail = "/?assistant=1" if assistant else "/"
    return f"{scheme}://{ip}:{port}{tail}"


@router.get("/api/pairing/info")
async def pairing_info(request: Request):
    scheme, port = _base(request)
    ips = _candidate_ips()
    entries = [
        {
            "ip": ip,
            "url_full": _url_for(scheme, ip, port, False),
            "url_assistant": _url_for(scheme, ip, port, True),
            "hotspot": not ip.startswith("192.168."),
        }
        for ip in ips
    ]
    return {
        "scheme": scheme,
        "port": port,
        "secure": scheme == "https",
        "entries": entries,
        "hint": (
            "Handy und Rechner müssen im selben WLAN sein. Stellt das Handy den Hotspot "
            "und der Rechner ist damit verbunden, funktioniert es ebenfalls."
        ),
    }


@router.get("/api/pairing/qr")
async def pairing_qr(request: Request, ip: str, assistant: int = 0):
    # Nur eigene, erkannte Adressen kodieren (kein beliebiger Text).
    if ip not in _candidate_ips():
        raise HTTPException(400, "Unbekannte Adresse")
    scheme, port = _base(request)
    url = _url_for(scheme, ip, port, bool(assistant))
    try:
        svg = qrcode_pure.to_svg(url, level="M", scale=8, border=4)
    except Exception as e:
        raise HTTPException(500, f"QR-Erzeugung fehlgeschlagen: {e}")
    return Response(content=svg, media_type="image/svg+xml",
                    headers={"Cache-Control": "no-store"})
