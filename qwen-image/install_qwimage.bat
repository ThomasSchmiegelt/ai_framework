@echo off
REM Installiert Qwen-Image-2.1 (diffusers) in eine eigene venv in diesem Ordner.
REM PyTorch (CUDA 12.4) + diffusers (QwenImagePipeline) + Abhaengigkeiten.
REM Modellgewichte (~15-20 GB) werden beim ersten bild.bat-Lauf automatisch geladen.
REM
REM Aufruf:
REM   install_qwimage.bat                 (mit CUDA-GPU, empfohlen)
REM   install_qwimage.bat CPU             (fur Rechner ohne CUDA-GPU)
REM
REM OPTIONALE HF-TOKEN (schnellere Downloads):
REM   - Umgebungsvariable  HF_TOKEN=hf_xxx,  ODER
REM   - diesen Ordner\hf_token.txt  (eine Zeile),  ODER
REM   - beim Aufruf:  set HF_TOKEN=hf_xxx^& call install_qwimage.bat
REM Der Token ist ein Geheimnis (per .gitignore ausgeschlossen), nie ausgegeben
REM und reine Zugangs-Konfiguration (keine neue Abhaengigkeit, MIT-konform).

if /i "%~1"=="CPU" set T=torch torchvision
if /i "%~1"=="CPU" (
  python -m venv venv || exit /b 1
  venv\Scripts\python.exe -m pip install --upgrade pip
  venv\Scripts\python.exe -m pip install torch torchvision
  venv\Scripts\python.exe -m pip install "diffusers>=0.34.0" transformers accelerate safetensors "numpy<2.3" Pillow huggingface_hub
  goto :end
)

REM --- GPU / CUDA (Default) ---
python -m venv venv || exit /b 1
venv\Scripts\python.exe -m pip install --upgrade pip
venv\Scripts\python.exe -m pip install torch torchvision --index-url https://download.pytorch.org/whl/cu124
if errorlevel 1 (
  echo   CUDA-Paket nicht erhalten - versuche CPU-Variante ...
  venv\Scripts\python.exe -m pip install torch torchvision
)
venv\Scripts\python.exe -m pip install "diffusers>=0.34.0" transformers accelerate safetensors "numpy<2.3" Pillow huggingface_hub

:end
echo.
echo === Fertig ===
echo Test:   bild.bat "ein blauer Van auf einem Parkplatz, Fotorealistisch"
echo Server: sd_server.bat   (A1111-kompatibel, fuer das AI-Framework, Port 7861)
echo Der erste Lauf laedt ~15-20 GB Modellgewichte herunter (einmalig).
