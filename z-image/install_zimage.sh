#!/usr/bin/env bash
# install_zimage.sh — Z-Image-Turbo · lokale Installation (Linux)
#
# Legt eine eigene venv im Ordner z-image an und installiert PyTorch (CUDA 12.4)
# sowie diffusers (Quellcode) mit den nötigen Bibliotheken. Die ~20 GB
# Modellgewichte werden NICHT hier, sondern beim ersten Bildlauf automatisch von
# Hugging Face geladen.
#
# Aufruf:  ./install_zimage.sh          (GPU/CUDA)
#          ./install_zimage.sh --cpu    (ohne CUDA, nur CPU – sehr langsam)
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"
echo "=== Z-Image-Turbo — lokale Installation (Linux) ==="

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

if [ "${1:-}" = "--cpu" ]; then
  echo "Installiere PyTorch (CPU-Variante)..."
  venv/bin/pip install torch torchvision
else
  echo "Installiere PyTorch (CUDA 12.4)..."
  venv/bin/pip install torch torchvision --index-url https://download.pytorch.org/whl/cu124
fi

echo "Installiere diffusers (Quellcode) + Bibliotheken..."
venv/bin/pip install -r requirements.txt

venv/bin/python -c "import torch; from diffusers import ZImagePipeline; print('OK  torch', torch.__version__, '| CUDA', torch.cuda.is_available())"

echo ""
echo "=== Fertig ==="
echo "Erster Testlauf (lädt beim ersten Mal ~20 GB Modellgewichte nach ~/.cache/huggingface):"
echo "  ./bild.sh \"ein roter Sportwagen im Sonnenuntergang, Fotorealismus\""
