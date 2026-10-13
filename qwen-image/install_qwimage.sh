#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# Installiert Qwen-Image-2.1 (diffusers) in eine eigene venv in diesem Ordner.
# PyTorch (CUDA 12.4) + diffusers (QwenImagePipeline) + Abhaengigkeiten.
# Modellgewichte (~15-20 GB) werden beim ersten bild.sh-Lauf automatisch geladen.
#
# Aufruf:
#   ./install_qwimage.sh                 # mit CUDA-GPU (empfohlen)
#   ./install_qwimage.sh --cpu           # fuer Rechner ohne CUDA-GPU
#
# OPTIONALE HF-TOKEN (schnellere Downloads):
#   env HF_TOKEN=hf_xxx ./install_qwimage.sh        ODER
#   einen Token in ./hf_token.txt legen (eine Zeile)
# Der Token ist ein Geheimnis (per .gitignore ausgeschlossen), nie ausgegeben
# und reine Zugangs-Konfiguration (keine neue Abhaengigkeit, MIT-konform).
# ---------------------------------------------------------------------------
set -euo pipefail
cd "$(dirname "$0")"

FORCE_CPU=0
[[ "${1:-}" == "--cpu" ]] && FORCE_CPU=1

PY="$(command -v python3 || command -v python)"
echo "=== Qwen-Image-2.1 Installation (diffusers) ==="
echo "Python: $PY ($($PY --version 2>&1))"

if [ ! -d venv ]; then
  "$PY" -m venv venv
fi
python=venv/bin/python

if [[ -n "${HF_TOKEN:-}" ]]; then
  echo "Nutze HF-Token aus Umgebung (HF_TOKEN)."
elif [[ -f hf_token.txt ]]; then
  echo "Nutze HF-Token aus hf_token.txt."
else
  echo "Kein HF-Token - Modell-Download beim ersten Lauf eventuell gedrosselt."
fi

"$python" -m pip install --upgrade pip -q

if [[ "$FORCE_CPU" -eq 1 ]]; then
  echo "Installiere PyTorch (CPU) ..."
  "$python" -m pip install torch torchvision
else
  echo "Installiere PyTorch (CUDA 12.4) ..."
  "$python" -m pip install torch torchvision --index-url https://download.pytorch.org/whl/cu124 \
    || { echo "  CPU-Variante ..."; "$python" -m pip install torch torchvision; }
  if ! "$python" -c "import torch, sys; sys.exit(0 if torch.cuda.is_available() else 1)" 2>/dev/null; then
    echo "  WARNUNG: PyTorch ohne CUDA-Device - CPU-Betrieb (langsamer)."
  fi
fi

echo "Installiere diffusers & Co ..."
"$python" -m pip install "diffusers>=0.34.0" transformers accelerate safetensors "numpy<2.3" Pillow huggingface_hub

echo
echo "=== Fertig ==="
echo 'Test:   ./bild.sh "ein blauer Van auf einem Parkplatz, Fotorealistisch"'
echo 'Server: ./sd_server.sh   (A1111-kompatibel, fuer das AI-Framework, Port 7861)'
echo "Der erste Lauf laedt ~15-20 GB Modellgewichte herunter (einmalig)."
echo 'Pruefe: venv/bin/python -c "from diffusers import QwenImagePipeline; print(ok)"'
