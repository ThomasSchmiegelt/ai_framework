@echo off
REM install_zvideo.bat - startet die PowerShell-Installation der Wan-Videogenerierung.
REM (Kein Administrator noetig: Installation erfolgt in eine lokale venv.)
cd /d "%~dp0"
powershell -ExecutionPolicy Bypass -NoProfile -File "%~dp0install_zvideo.ps1" %*
pause
