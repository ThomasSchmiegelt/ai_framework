@echo off
REM Z-Image-Turbo Bild-Server (A1111-kompatibel) fuer das AI-Framework-Chat-Bild.
REM Start:  sd_server.bat            (CPU-Offload, teilt GPU mit Ollama)
REM         sd_server.bat --full-gpu (Modell dauerhaft im VRAM, schneller)
setlocal
cd /d "%~dp0"
"%~dp0venv\Scripts\python.exe" "%~dp0sd_server.py" %*
endlocal
