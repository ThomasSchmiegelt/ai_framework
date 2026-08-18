#!/usr/bin/env bash
# Z-Image-Turbo Starter (Linux). Aufruf:
#   ./bild.sh "ein roter Sportwagen im Sonnenuntergang"
#   ./bild.sh "Portrait einer Katze" --seed 42 --out katze.png
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec "$SCRIPT_DIR/venv/bin/python" "$SCRIPT_DIR/generate.py" "$@"
