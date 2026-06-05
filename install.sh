#!/usr/bin/env bash
# install.sh — AI Framework Thomas · Linux-Installation
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "=== AI Framework Thomas — Installation ==="

# Python 3 prüfen
if ! command -v python3 &>/dev/null; then
  echo "FEHLER: python3 nicht gefunden. Bitte installieren: sudo apt install python3 python3-venv python3-pip"
  exit 1
fi
PYTHON=$(command -v python3)
echo "Python: $($PYTHON --version)"

# Ollama prüfen
if ! curl -sf http://localhost:11434/api/tags >/dev/null 2>&1; then
  echo "WARNUNG: Ollama ist nicht erreichbar (http://localhost:11434)."
  echo "         Bitte Ollama starten: ollama serve"
  echo "         Installation wird trotzdem fortgesetzt."
fi

# Virtuelle Umgebung anlegen
if [ ! -d "venv" ]; then
  echo "Erstelle virtuelle Umgebung..."
  $PYTHON -m venv venv
fi

# Abhängigkeiten installieren
echo "Installiere Python-Abhängigkeiten..."
venv/bin/pip install --upgrade pip --quiet
venv/bin/pip install -r requirements.txt --quiet

# Datenverzeichnisse anlegen (falls nicht vorhanden)
for dir in data/uploads data/reports data/code data/plans data/dossiers data/profile_assets; do
  mkdir -p "$dir"
  touch "$dir/.gitkeep" 2>/dev/null || true
done

echo ""
echo "=== Installation abgeschlossen ==="
echo "Starten mit:  ./start.sh"
echo "Oder direkt:  source venv/bin/activate && uvicorn main:app --host 127.0.0.1 --port 8780 --reload"
