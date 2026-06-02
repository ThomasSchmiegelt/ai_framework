@echo off
chcp 65001 >nul
setlocal enableextensions
title AI_Framework_Thomas - Chat-Test
cd /d "%~dp0"

:: ===========================================================================
:: Direkter Test des Chat-Backends (am Frontend vorbei).
:: test_chat.bat UND test_chat.py zusammen in den Portable-Ordner legen
:: (neben start.bat) und diese .bat per Doppelklick starten.
:: WICHTIG: Die App muss laufen (start.bat offen / Port 8780 aktiv).
:: ===========================================================================

set "ROOT=%~dp0"

:: --- Python + App-Ordner erkennen (wie in diagnose.bat) --------------------
if exist "%ROOT%app\main.py" (
    set "APPDIR=%ROOT%app"
    set "PY=%ROOT%python\python.exe"
) else (
    set "APPDIR=%ROOT%."
    if exist "%ROOT%..\python\python.exe" ( set "PY=%ROOT%..\python\python.exe" ) else ( set "PY=python" )
)
if exist "%ROOT%venv\Scripts\python.exe" set "PY=%ROOT%venv\Scripts\python.exe"

if not exist "%PY%" (
    echo [FEHLER] Python nicht gefunden unter: %PY%
    pause
    exit /b 1
)
if not exist "%ROOT%test_chat.py" (
    echo [FEHLER] test_chat.py liegt nicht neben dieser .bat
    pause
    exit /b 1
)

echo Chat-Test laeuft (App muss laufen)... bitte warten, kann 1-2 Min dauern.
echo.
"%PY%" "%ROOT%test_chat.py" "%APPDIR%"

echo.
echo ===========================================================================
echo  Bitte den gesamten Text oben markieren, kopieren und zuruecksenden.
echo ===========================================================================
echo.
pause
endlocal
