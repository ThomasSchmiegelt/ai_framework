@echo off
title AI_Framework_Thomas - Portable Bundle erstellen
cd /d "%~dp0"
echo.
echo  AI_Framework_Thomas - Portable Bundle
echo  Voraussetzung: install.bat muss vorher ausgefuehrt worden sein
echo  (Ollama und Modelle muessen installiert sein)
echo.
echo  Optional: Ausgabe-Verzeichnis waehlen, z.B.
echo    make_portable.bat -OutDir D:\Portable
echo.
pause
powershell -ExecutionPolicy Bypass -NoProfile -File "%~dp0make_portable.ps1" %*
pause
