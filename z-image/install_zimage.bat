@echo off
REM install_zimage.bat - startet die PowerShell-Installation von Z-Image-Turbo.
REM (Kein Administrator noetig: Installation erfolgt in eine lokale venv.)
cd /d "%~dp0"
powershell -ExecutionPolicy Bypass -NoProfile -File "%~dp0install_zimage.ps1" %*
pause
