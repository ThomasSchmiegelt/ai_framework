#!/usr/bin/env bash
# install_zvideo.sh — Wan Videogenerierung · lokale Installation (Linux)
#
# Legt eine eigene venv im Ordner z-video an und installiert PyTorch (CUDA 12.4)
# sowie diffusers (Quellcode) mit den noetigen Bibliotheken. Die Modellgewichte
# (mehrere GB; das 14B-Modell ~30-70 GB) werden NICHT hier, sondern beim ersten
# Videolauf automatisch von Hugging Face geladen.
#
# Aufruf:  ./install_zvideo.sh                       (GPU/CUDA)
#          ./install_zvideo.sh --cpu                 (ohne CUDA, nur CPU - sehr langsam)
#          ./install_zvideo.sh --hf-token hf_xxx     (optionaler Hugging-Face-Token)
#          HF_TOKEN=hf_xxx ./install_zvideo.sh       (Token auch via Umgebungsvariable)
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"
echo "=== Wan Videogenerierung — lokale Installation (Linux) ==="

CPU=0
HF_TOKEN="${HF_TOKEN:-}"
while [ "$#" -gt 0 ]; do
  case "$1" in
    --cpu) CPU=1 ;;
    --hf-token) shift; HF_TOKEN="${1:-}" ;;
    *) echo "Unbekanntes Argument: $1" ;;
  esac
  shift
done

if ! command -v python3 &>/dev/null; then
  echo "FEHLER: python3 nicht gefunden. Bitte installieren: sudo apt install python3 python3-venv python3-pip"
  exit 1
fi
echo "Python: $(python3 --version)"

if [ ! -d venv ]; then
  echo "Erstelle virtuelle Umgebung (venv)..."
  python3 -m venv venv
fi

echo "Aktualisiere pip/setuptools/wheel..."
venv/bin/pip install --upgrade pip setuptools wheel

if [ "$CPU" = "1" ]; then
  echo "Installiere PyTorch (CPU-Variante)..."
  venv/bin/pip install torch torchvision
else
  echo "Installiere PyTorch (CUDA 12.4)..."
  venv/bin/pip install torch torchvision --index-url https://download.pytorch.org/whl/cu124
fi

echo "Installiere diffusers (Quellcode) + Bibliotheken..."
venv/bin/pip install -r requirements.txt

# --- optionaler Hugging-Face-Token ------------------------------------------
if [ -z "$HF_TOKEN" ] && [ -t 0 ] && [ ! -f hf_token.txt ]; then
  echo ""
  echo "Optional: Hugging-Face-Token fuer schnellere Downloads (https://huggingface.co/settings/tokens)."
  read -r -p "HF-Token (leer lassen zum Ueberspringen): " HF_TOKEN || HF_TOKEN=""
fi
if [ -n "$HF_TOKEN" ]; then
  printf '%s' "$HF_TOKEN" > hf_token.txt
  echo "HF-Token gespeichert in hf_token.txt (lokal, nicht im Repo)."
fi

venv/bin/python -c "import torch; print('OK  torch', torch.__version__, '| CUDA', torch.cuda.is_available())"

echo ""
echo "=== Fertig ==="
echo "Server starten (das Framework kann ihn auch selbst starten):"
echo "  ./video_server.sh"
echo "Danach im Framework-Profil unter '🎬 Videoerzeugung' Lokal - Wan + URL http://127.0.0.1:7870 waehlen."
