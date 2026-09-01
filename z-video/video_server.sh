#!/usr/bin/env bash
# Wan Video-Server (Bruecke) fuer das AI-Framework (Tab Videoerzeugung / Chat /video).
# Start:  ./video_server.sh            (CPU-Offload, teilt GPU mit Ollama)
#         ./video_server.sh --full-gpu (Modell dauerhaft im VRAM, schneller)
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec "$SCRIPT_DIR/venv/bin/python" "$SCRIPT_DIR/video_server.py" "$@"
