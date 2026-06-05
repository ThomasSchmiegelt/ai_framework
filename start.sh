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

echo "AI Framework Thomas startet auf http://${HOST}:${PORT}"
exec uvicorn main:app --host "$HOST" --port "$PORT"
