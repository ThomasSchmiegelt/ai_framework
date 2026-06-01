@echo off
chcp 65001 >nul
title AI_Framework_Thomas - Server
cd /d "%~dp0"

:: UTF-8 als Standardencoding fuer Python setzen
set PYTHONUTF8=1

:: Anzahl Worker — Standard 1 wegen VRAM-Schutz: der "nur ein Modell
:: gleichzeitig"-Guard arbeitet pro Prozess. Mehr Worker nur erhoehen, wenn
:: genug VRAM fuer parallel geladene Modelle vorhanden ist (siehe docs/SERVER.md).
set WORKERS=1
set PORT=8780
set HOST=0.0.0.0

:: Ollama starten falls nicht aktiv
tasklist /fi "IMAGENAME eq ollama.exe" 2>nul | find /i "ollama.exe" >nul
if errorlevel 1 (
    echo [*] Starte Ollama...
    start /min "" ollama serve
    timeout /t 3 /nobreak >nul
)

if exist "venv\Scripts\activate.bat" (
    call venv\Scripts\activate.bat
) else (
    echo [!] venv nicht gefunden. Bitte zuerst install.bat ausfuehren.
    pause
    exit /b 1
)

echo.
echo  AI_Framework_Thomas SERVER
echo  Host:    %HOST%:%PORT%
echo  Workers: %WORKERS%
echo  Zugang:  http://<diese-IP>:%PORT%
echo.

uvicorn main:app --host %HOST% --port %PORT% --workers %WORKERS%
