@echo off
title AI_Framework_Thomas - Server Bundle erstellen
cd /d "%~dp0"
echo.
echo  AI_Framework_Thomas - Server Bundle erstellen
echo  Die Server-Variante wird fuer staerkere Hardware konfiguriert.
echo  Voraussetzung: install.bat muss vorher ausgefuehrt worden sein.
echo.
pause
powershell -ExecutionPolicy Bypass -NoProfile -File "%~dp0make_server.ps1"
pause
