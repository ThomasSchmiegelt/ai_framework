@echo off
title AI_Framework_Thomas - Portable Bundle (Modelle beim Erststart) erstellen
cd /d "%~dp0"
echo.
echo  AI_Framework_Thomas - Portable Bundle (Modelle beim Erststart)
echo.
echo  Diese Variante buendelt Ollama KOMPLETT (Binary + Laufzeit, eigener
echo  Port 11500), aber KEINE Modelle -^> Bundle nur ~2,5 GB statt ~9 GB.
echo  Die start.bat des Bundles laedt die Modelle beim ERSTEN Start
echo  automatisch von ollama.com nach (einmalig Internet am Zielrechner).
echo.
echo  Voraussetzung: install.bat wurde ausgefuehrt (Ollama installiert).
echo  Optional: Ausgabe-Verzeichnis, z.B.
echo    make_portable_nomodels.bat -OutDir D:\Portable
echo.
pause
powershell -ExecutionPolicy Bypass -NoProfile -File "%~dp0make_portable.ps1" -NoModels %*
pause
