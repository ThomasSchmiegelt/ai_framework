#!/usr/bin/env bash
# Wan-Modell VORAB laden (Linux, resumable). Beispiele:
#   ./download_model.sh --mode flf2v
#   ./download_model.sh --model Wan-AI/Wan2.1-T2V-1.3B-Diffusers
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec "$SCRIPT_DIR/venv/bin/python" "$SCRIPT_DIR/download_model.py" "$@"
