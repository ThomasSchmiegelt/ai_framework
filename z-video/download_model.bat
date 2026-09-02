@echo off
REM Wan-Modell VORAB laden (Windows, resumable). Beispiele:
REM   download_model.bat --mode flf2v
REM   download_model.bat --model Wan-AI/Wan2.1-T2V-1.3B-Diffusers
setlocal
cd /d "%~dp0"
"%~dp0venv\Scripts\python.exe" "%~dp0download_model.py" %*
endlocal
