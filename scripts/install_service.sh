#!/usr/bin/env bash
# install_service.sh — richtet AI Framework als systemd-Dienst ein:
# Autostart beim Hochfahren + automatischer Neustart bei Absturz, optional mit HTTPS.
#
# Aufruf (fragt das sudo-Passwort ab):
#   ./scripts/install_service.sh
#
# Überschreibbar per Umgebungsvariablen:
#   SERVICE_NAME (Default ai-framework)   AI_PORT (8780)   AI_HOST (0.0.0.0)
#   RUN_USER (aktueller Benutzer)
#   AI_SSL_CERT / AI_SSL_KEY (Default certs/cert.pem|key.pem im Projekt)
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SERVICE_NAME="${SERVICE_NAME:-ai-framework}"
RUN_USER="${RUN_USER:-$(id -un)}"
PORT="${AI_PORT:-8780}"
HOST="${AI_HOST:-0.0.0.0}"
CERT="${AI_SSL_CERT:-$APP_DIR/certs/cert.pem}"
KEY="${AI_SSL_KEY:-$APP_DIR/certs/key.pem}"

[ -x "$APP_DIR/venv/bin/uvicorn" ] || {
  echo "FEHLER: $APP_DIR/venv/bin/uvicorn fehlt. Bitte zuerst ./install.sh ausführen." >&2; exit 1; }

SSL_OPTS=""
SCHEME="http"
if [ -f "$CERT" ] && [ -f "$KEY" ]; then
  SSL_OPTS=" --ssl-certfile $CERT --ssl-keyfile $KEY"
  SCHEME="https"
  echo "→ HTTPS aktiv (Zertifikat: $CERT)"
else
  echo "→ HINWEIS: Kein Zertifikat gefunden ($CERT)."
  echo "  Der Dienst startet ohne HTTPS. Für die Handy-Installation erst:"
  echo "      ./scripts/gen_cert.sh   und das Skript danach erneut ausführen."
fi

UNIT="/etc/systemd/system/${SERVICE_NAME}.service"
echo "→ Schreibe $UNIT  (User=$RUN_USER, ${SCHEME}://${HOST}:${PORT})"

sudo tee "$UNIT" >/dev/null <<EOF
[Unit]
Description=AI Framework Thomas (lokales KI-Framework)
After=network-online.target ollama.service
Wants=network-online.target

[Service]
Type=simple
User=${RUN_USER}
WorkingDirectory=${APP_DIR}
Environment=PYTHONUTF8=1
ExecStart=${APP_DIR}/venv/bin/uvicorn main:app --host ${HOST} --port ${PORT}${SSL_OPTS}
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable "$SERVICE_NAME"
sudo systemctl restart "$SERVICE_NAME"

echo
echo "✓ Dienst „${SERVICE_NAME}\" eingerichtet und gestartet (Autostart aktiv)."
echo "  Status:   systemctl status ${SERVICE_NAME}"
echo "  Logs:     journalctl -u ${SERVICE_NAME} -f"
echo "  Stoppen:  sudo systemctl stop ${SERVICE_NAME}"
echo "  Entfernen: ./scripts/uninstall_service.sh"
echo
echo "Adresse am Handy (gleiches WLAN/FritzBox):  ${SCHEME}://<rechner-ip>:${PORT}"
