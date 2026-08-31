@echo off
chcp 65001 >nul
title AI_Framework_Thomas
cd /d "%~dp0"

:: Ollama starten falls nicht aktiv
tasklist /fi "IMAGENAME eq ollama.exe" 2>nul | find /i "ollama.exe" >nul
if errorlevel 1 (
    echo [*] Starte Ollama...
    start /min "" ollama serve
    timeout /t 3 /nobreak >nul
)

:: UTF-8 als Standardencoding fuer Python setzen (verhindert Umlaute/Emoji-Probleme)
set PYTHONUTF8=1

:: Virtuelle Umgebung aktivieren
if exist "venv\Scripts\activate.bat" (
    call venv\Scripts\activate.bat
) else (
    echo [!] venv nicht gefunden. Bitte zuerst install.bat ausfuehren.
    pause
    exit /b 1
)

:: Browser nach kurzer Verzoegerung oeffnen
start /min "" cmd /c "timeout /t 2 >nul && start http://localhost:8780"

echo.
echo  AI_Framework_Thomas gestartet -^> http://localhost:8780
echo  Fenster schliessen um zu beenden.
echo.

:: Server ueber den Python-Interpreter starten (python -m uvicorn) statt ueber die
:: uvicorn.exe-Shim: manche Firmen-Sicherheitsrichtlinien (Device Guard / WDAC)
:: blockieren unsignierte .exe im Nutzerprofil — python.exe ist erlaubt.
python -m uvicorn main:app --host 127.0.0.1 --port 8780 --reload
