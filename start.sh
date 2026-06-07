#!/usr/bin/env bash
# start.sh — AI Framework Thomas · Linux-Start
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

if [ ! -f "venv/bin/activate" ]; then
  echo "FEHLER: Virtuelle Umgebung fehlt. Bitte zuerst ./install.sh ausführen."
  exit 1
fi

source venv/bin/activate

export PYTHONUTF8=1

HOST="${AI_HOST:-127.0.0.1}"
PORT="${AI_PORT:-8780}"

# Optionales HTTPS (für die PWA-Installation am Handy): beide Variablen setzen.
# Zertifikat z. B. mit ./scripts/gen_cert.sh erzeugen.
SSL_ARGS=()
SCHEME="http"
if [ -n "${AI_SSL_CERT:-}" ] && [ -n "${AI_SSL_KEY:-}" ]; then
  SSL_ARGS=(--ssl-certfile "$AI_SSL_CERT" --ssl-keyfile "$AI_SSL_KEY")
  SCHEME="https"
fi

echo "AI Framework Thomas startet auf ${SCHEME}://${HOST}:${PORT}"
exec uvicorn main:app --host "$HOST" --port "$PORT" "${SSL_ARGS[@]}"
