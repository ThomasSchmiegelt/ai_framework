@echo off
REM Wan Video-Server (Bruecke) fuer das AI-Framework (Tab Videoerzeugung / Chat /video).
REM Start:  video_server.bat            (CPU-Offload, teilt GPU mit Ollama)
REM         video_server.bat --full-gpu (Modell dauerhaft im VRAM, schneller)
setlocal
cd /d "%~dp0"
"%~dp0venv\Scripts\python.exe" "%~dp0video_server.py" %*
endlocal
