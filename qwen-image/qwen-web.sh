#!/usr/bin/env bash
# Qwen-Image · schlankes Web-Tool — Start (Linux)
set -euo pipefail
cd "$(dirname "$0")"

PY="python3"
[ -x "./venv/bin/python" ] && PY="./venv/bin/python"

# Browser im Hintergrund öffnen (bestehende Plattform-Befehle)
( sleep 1
  if command -v xdg-open >/dev/null 2>&1; then xdg-open "http://127.0.0.1:7871" >/dev/null 2>&1 || true
  elif command -v open >/dev/null 2>&1; then open "http://127.0.0.1:7871" >/dev/null 2>&1 || true
  fi ) &

exec "$PY" qwen-web.py "$@"
