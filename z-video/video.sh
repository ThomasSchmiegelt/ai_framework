#!/usr/bin/env bash
# Wan Video-Generator (Linux, Kommandozeile). Beispiele:
#   ./video.sh --mode t2v --prompt "ein roter Sportwagen faehrt durch die Wueste" --out clip.mp4
#   ./video.sh --mode flf2v --first a.png --last b.png --prompt "sanfte Kamerafahrt" --out clip.mp4
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec "$SCRIPT_DIR/venv/bin/python" "$SCRIPT_DIR/generate_video.py" "$@"
