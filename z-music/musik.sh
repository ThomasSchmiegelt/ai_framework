#!/usr/bin/env bash
# Musik-Generator (Linux/macOS). Braucht nur Python 3 (keine Installation).
# Aufruf:  ./musik.sh "fröhliche schnelle Abenteuermelodie"
#          ./musik.sh "8bit" --tempo 140 --seed 7
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec python3 "$SCRIPT_DIR/generate_music.py" "$@"
