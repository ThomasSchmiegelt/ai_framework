#!/usr/bin/env bash
# gen_cert.sh — selbstsigniertes TLS-Zertifikat für den HTTPS-/PWA-Betrieb im
# Heimnetz (Handy ↔ Ubuntu-Rechner über die FritzBox).
#
# Warum HTTPS? Ein Service Worker (= „App installieren") läuft nur in einem
# „secure context": HTTPS oder localhost. Über http://<LAN-IP>:8780 registriert
# Android-Chrome keinen SW → die Oberfläche funktioniert zwar im Browser, ist aber
# nicht installierbar. Mit diesem selbstsignierten Zertifikat geht beides.
#
# Das Zertifikat enthält als SubjectAltName: localhost, 127.0.0.1, die LAN-IP(s)
# dieses Rechners und – sofern ermittelbar – <hostname>.fritz.box / <hostname>.local,
# damit das Handy https://<ip-oder-name>:8780 nach einmaliger Bestätigung akzeptiert.
#
# Aufruf:
#   ./scripts/gen_cert.sh                       # automatische SANs
#   ./scripts/gen_cert.sh 192.168.178.50 pc.fritz.box   # zusätzliche IP/Namen
#   CERT_DAYS=365 ./scripts/gen_cert.sh         # Gültigkeit anpassen (Default 825)
set -euo pipefail

cd "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"   # Projektwurzel

if ! command -v openssl >/dev/null 2>&1; then
  echo "FEHLER: openssl nicht gefunden. Bitte installieren: sudo apt install openssl" >&2
  exit 1
fi

CERT_DIR="certs"
DAYS="${CERT_DAYS:-825}"        # 825 Tage = Maximum, das iOS/Safari für eigene Certs akzeptieren
mkdir -p "$CERT_DIR"

# ── SubjectAltName-Liste aufbauen ───────────────────────────────────────────
SANS=("DNS:localhost" "IP:127.0.0.1")
HN="$(hostname -s 2>/dev/null || hostname 2>/dev/null || echo localhost)"
if [ -n "$HN" ] && [ "$HN" != "localhost" ]; then
  SANS+=("DNS:${HN}" "DNS:${HN}.fritz.box" "DNS:${HN}.local")
fi

LAN_IPS=()
if hostname -I >/dev/null 2>&1; then
  for ip in $(hostname -I); do
    case "$ip" in
      127.*|169.254.*|::1|fe80:*) ;;          # Loopback/Link-local überspringen
      *) SANS+=("IP:${ip}"); LAN_IPS+=("$ip") ;;
    esac
  done
fi

# Zusätzliche IPs/Namen als Argumente
for extra in "$@"; do
  if [[ "$extra" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
    SANS+=("IP:${extra}"); LAN_IPS+=("$extra")
  else
    SANS+=("DNS:${extra}")
  fi
done

SAN_STR="$(IFS=,; echo "${SANS[*]}")"
echo "SubjectAltName: $SAN_STR"
echo

openssl req -x509 -newkey rsa:2048 -nodes \
  -keyout "$CERT_DIR/key.pem" -out "$CERT_DIR/cert.pem" \
  -days "$DAYS" -subj "/CN=${HN}" \
  -addext "subjectAltName=${SAN_STR}" 2>/dev/null

chmod 600 "$CERT_DIR/key.pem"

echo "✓ Zertifikat erstellt:"
echo "    $CERT_DIR/cert.pem   (gültig $DAYS Tage)"
echo "    $CERT_DIR/key.pem    (privater Schlüssel — NICHT weitergeben)"
echo
echo "── Server mit HTTPS starten ───────────────────────────────────────────"
echo "  AI_HOST=0.0.0.0 AI_SSL_CERT=$CERT_DIR/cert.pem AI_SSL_KEY=$CERT_DIR/key.pem ./start.sh"
echo "(oder direkt:)"
echo "  uvicorn main:app --host 0.0.0.0 --port ${AI_PORT:-8780} \\"
echo "    --ssl-certfile $CERT_DIR/cert.pem --ssl-keyfile $CERT_DIR/key.pem"
echo
echo "── Am Handy öffnen (gleiches WLAN/FritzBox) ───────────────────────────"
if [ "${#LAN_IPS[@]}" -gt 0 ]; then
  for ip in "${LAN_IPS[@]}"; do echo "  https://${ip}:${AI_PORT:-8780}"; done
fi
[ "$HN" != "localhost" ] && echo "  https://${HN}.fritz.box:${AI_PORT:-8780}"
echo
echo "Beim ersten Aufruf die Zertifikatswarnung bestätigen"
echo "(Android-Chrome: „Erweitert\" → „Weiter zu … (unsicher)\")."
echo "Ggf. die Firewall öffnen:  sudo ufw allow ${AI_PORT:-8780}/tcp"
