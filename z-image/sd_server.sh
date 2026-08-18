#!/usr/bin/env bash
# Z-Image-Turbo Bild-Server (A1111-kompatibel) fuer das AI-Framework-Chat-Bild.
# Start:  ./sd_server.sh            (CPU-Offload, teilt GPU mit Ollama)
#         ./sd_server.sh --full-gpu (Modell dauerhaft im VRAM, schneller)
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec "$SCRIPT_DIR/venv/bin/python" "$SCRIPT_DIR/sd_server.py" "$@"
