@echo off
title AI_Framework_Thomas - Portable Bundle (System-Ollama) erstellen
cd /d "%~dp0"
echo.
echo  AI_Framework_Thomas - Portable Bundle (System-Ollama)
echo.
echo  Diese Variante buendelt WEDER Ollama NOCH die Modelle und nutzt das
echo  bereits auf dem Zielrechner installierte Ollama (Port 11434).
echo  -^> kleineres Bundle, setzt aber ein installiertes Ollama voraus.
echo.
echo  Voraussetzung: install.bat (fuer venv/Pakete) wurde ausgefuehrt.
echo  Optional: Ausgabe-Verzeichnis, z.B.
echo    make_portable_systemollama.bat -OutDir D:\Portable
echo.
pause
powershell -ExecutionPolicy Bypass -NoProfile -File "%~dp0make_portable.ps1" -UseSystemOllama %*
pause
