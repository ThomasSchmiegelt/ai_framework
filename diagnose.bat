@echo off
chcp 65001 >nul
setlocal enableextensions
title AI_Framework_Thomas - Diagnose
cd /d "%~dp0"

:: ===========================================================================
:: AI_Framework_Thomas - Diagnose
:: Sammelt alle Infos die noetig sind um zu sehen warum der Start fehlschlaegt.
:: Einfach diese Datei in den Portable-Ordner legen (neben start.bat) und
:: per Doppelklick ausfuehren. Ergebnis: diagnose_report.txt
:: Diese Datei bitte komplett zuruecksenden.
:: ===========================================================================

set "ROOT=%~dp0"
set "LOG=%ROOT%diagnose_report.txt"

:: --- Layout erkennen (Bundle-Root vs. app-Ordner vs. Quellordner) ----------
if exist "%ROOT%app\main.py" (
    set "LAYOUT=Portable-Bundle-Root"
    set "APPDIR=%ROOT%app"
    set "PY=%ROOT%python\python.exe"
    set "OLLAMA=%ROOT%ollama\ollama.exe"
    set "MODELS=%ROOT%ollama\models"
) else (
    if exist "%ROOT%main.py" (
        set "LAYOUT=App-Ordner"
        set "APPDIR=%ROOT%."
        if exist "%ROOT%..\python\python.exe" ( set "PY=%ROOT%..\python\python.exe" ) else ( set "PY=python" )
        if exist "%ROOT%..\ollama\ollama.exe" ( set "OLLAMA=%ROOT%..\ollama\ollama.exe" ) else ( set "OLLAMA=ollama" )
        if exist "%ROOT%..\ollama\models" ( set "MODELS=%ROOT%..\ollama\models" ) else ( set "MODELS=%USERPROFILE%\.ollama\models" )
    ) else (
        set "LAYOUT=UNBEKANNT"
        set "APPDIR=%ROOT%."
        set "PY=python"
        set "OLLAMA=ollama"
        set "MODELS=%USERPROFILE%\.ollama\models"
    )
)

:: Bei venv-Variante (Standard-Install) bevorzugt venv-Python nehmen
if exist "%ROOT%venv\Scripts\python.exe" set "PY=%ROOT%venv\Scripts\python.exe"

:: ===========================================================================
echo Diagnose laeuft... bitte warten.
echo.

:: Log frisch anlegen
echo ===========================================================================> "%LOG%"
echo  AI_Framework_Thomas  -  Diagnose-Report>> "%LOG%"
echo ===========================================================================>> "%LOG%"
echo Zeitpunkt   : %DATE% %TIME%>> "%LOG%"
echo Rechner     : %COMPUTERNAME%>> "%LOG%"
echo Benutzer    : %USERNAME%>> "%LOG%"
echo Layout      : %LAYOUT%>> "%LOG%"
echo Ordner      : %ROOT%>> "%LOG%"
echo App-Ordner  : %APPDIR%>> "%LOG%"
echo Python      : %PY%>> "%LOG%"
echo Ollama      : %OLLAMA%>> "%LOG%"
echo Modelle     : %MODELS%>> "%LOG%"
echo.>> "%LOG%"

:: --- 1. System -------------------------------------------------------------
echo [*] System...
echo --- 1) SYSTEM -------------------------------------------------------------->> "%LOG%"
ver>> "%LOG%" 2>&1
echo Architektur : %PROCESSOR_ARCHITECTURE%>> "%LOG%"
reg query "HKLM\SOFTWARE\Microsoft\Windows NT\CurrentVersion" /v ProductName 2>nul | findstr /i "ProductName" >> "%LOG%" 2>&1
reg query "HKLM\SOFTWARE\Microsoft\Windows NT\CurrentVersion" /v DisplayVersion 2>nul | findstr /i "DisplayVersion" >> "%LOG%" 2>&1
echo.>> "%LOG%"

:: --- 2. Freier Speicher ----------------------------------------------------
echo --- 2) LAUFWERK ------------------------------------------------------------>> "%LOG%"
for %%D in ("%ROOT%") do set "DRV=%%~dD"
dir "%DRV%\" | findstr /i "frei bytes" >> "%LOG%" 2>&1
echo.>> "%LOG%"

:: --- 3. Pfad-Pruefung (Umlaute / Leerzeichen koennen stoeren) ---------------
echo --- 3) PFAD-PRUEFUNG ------------------------------------------------------->> "%LOG%"
echo Voller Pfad : %ROOT%>> "%LOG%"
echo Hinweis: Umlaute (a/o/u) oder Sonderzeichen im Pfad koennen Probleme machen.>> "%LOG%"
echo.>> "%LOG%"

:: --- 4. Verzeichnisstruktur ------------------------------------------------
echo --- 4) VERZEICHNIS-INHALT (Bundle-Root) ----------------------------------->> "%LOG%"
dir /b "%ROOT%" >> "%LOG%" 2>&1
echo.>> "%LOG%"
echo Pruefung wichtiger Dateien/Ordner:>> "%LOG%"
if exist "%APPDIR%\main.py"         (echo   [OK] main.py>> "%LOG%") else (echo   [FEHLT] main.py>> "%LOG%")
if exist "%APPDIR%\config.json"     (echo   [OK] config.json>> "%LOG%") else (echo   [FEHLT] config.json>> "%LOG%")
if exist "%APPDIR%\requirements.txt"(echo   [OK] requirements.txt>> "%LOG%") else (echo   [FEHLT] requirements.txt>> "%LOG%")
if exist "%APPDIR%\static"          (echo   [OK] static\>> "%LOG%") else (echo   [FEHLT] static\>> "%LOG%")
if exist "%PY%"                     (echo   [OK] python.exe>> "%LOG%") else (echo   [FEHLT] python.exe (%PY%)>> "%LOG%")
if exist "%OLLAMA%"                 (echo   [OK] ollama.exe>> "%LOG%") else (echo   [FEHLT] ollama.exe (%OLLAMA%)>> "%LOG%")
if exist "%MODELS%"                 (echo   [OK] models-Ordner>> "%LOG%") else (echo   [FEHLT] models-Ordner (%MODELS%)>> "%LOG%")
echo.>> "%LOG%"

:: --- 5. config.json --------------------------------------------------------
echo --- 5) CONFIG.JSON --------------------------------------------------------->> "%LOG%"
if exist "%APPDIR%\config.json" (
    type "%APPDIR%\config.json" >> "%LOG%" 2>&1
) else (
    echo   config.json nicht gefunden!>> "%LOG%"
)
echo.>> "%LOG%"
echo.>> "%LOG%"

:: --- 6. Python -------------------------------------------------------------
echo [*] Python...
echo --- 6) PYTHON -------------------------------------------------------------->> "%LOG%"
if exist "%PY%" (
    "%PY%" --version >> "%LOG%" 2>&1
    "%PY%" -c "import sys;print('Executable:',sys.executable);print('Version  :',sys.version)" >> "%LOG%" 2>&1
) else (
    echo   python.exe nicht gefunden unter: %PY%>> "%LOG%"
)
echo.>> "%LOG%"

:: --- 7. Python-Pakete (das ist der haeufigste Startfehler) -----------------
echo [*] Python-Pakete...
echo --- 7) PYTHON-PAKETE (Import-Test) ----------------------------------------->> "%LOG%"
if exist "%PY%" (
    call :pytest fastapi
    call :pytest uvicorn
    call :pytest httpx
    call :pytest aiosqlite
    call :pytest aiofiles
    call :pytest numpy
    call :pytest PIL
    call :pytest pydantic
    call :pytest multipart
    call :pytest sympy
    call :pytest scipy
    call :pytest pint
    call :pytest matplotlib
    call :pytest pypdf
    call :pytest docx
    call :pytest openpyxl
    call :pytest pptx
    call :pytest ddgs
    echo.>> "%LOG%"
    echo pip list:>> "%LOG%"
    "%PY%" -m pip list >> "%LOG%" 2>&1
) else (
    echo   uebersprungen - kein Python>> "%LOG%"
)
echo.>> "%LOG%"

:: --- 8. App-Import-Test (laedt main.py wie beim echten Start) ---------------
echo [*] App-Import-Test...
echo --- 8) APP-IMPORT-TEST (import main) --------------------------------------->> "%LOG%"
if exist "%PY%" if exist "%APPDIR%\main.py" (
    pushd "%APPDIR%"
    "%PY%" -c "import sys; sys.path.insert(0, '.'); import main; print('[OK] main.py importiert - App laedt grundsaetzlich')" >> "%LOG%" 2>&1
    popd
) else (
    echo   uebersprungen - kein Python oder kein main.py>> "%LOG%"
)
echo.>> "%LOG%"

:: --- 9. Laufende Prozesse --------------------------------------------------
echo --- 9) LAUFENDE PROZESSE --------------------------------------------------->> "%LOG%"
tasklist /fi "IMAGENAME eq ollama.exe" 2>nul | findstr /i "ollama" >> "%LOG%" 2>&1
tasklist /fi "IMAGENAME eq python.exe" 2>nul | findstr /i "python" >> "%LOG%" 2>&1
tasklist /fi "IMAGENAME eq uvicorn.exe" 2>nul | findstr /i "uvicorn" >> "%LOG%" 2>&1
echo (leer = keiner dieser Prozesse laeuft gerade)>> "%LOG%"
echo.>> "%LOG%"

:: --- 10. Belegte Ports -----------------------------------------------------
echo --- 10) PORTS (8780=App, 11500=Bundle-Ollama, 11434=System-Ollama) -------->> "%LOG%"
netstat -ano | findstr ":8780 :11500 :11434" >> "%LOG%" 2>&1
echo (leer = diese Ports sind frei / nichts lauscht darauf)>> "%LOG%"
echo.>> "%LOG%"

:: --- 11. Ollama ------------------------------------------------------------
echo [*] Ollama...
echo --- 11) OLLAMA ------------------------------------------------------------->> "%LOG%"
if exist "%OLLAMA%" (
    "%OLLAMA%" --version >> "%LOG%" 2>&1
) else (
    echo   ollama.exe nicht gefunden unter: %OLLAMA%>> "%LOG%"
)
echo.>> "%LOG%"
echo Gebuendelte Modell-Manifeste:>> "%LOG%"
if exist "%MODELS%\manifests" (
    dir /s /b "%MODELS%\manifests" >> "%LOG%" 2>&1
) else (
    echo   Kein manifests-Ordner unter %MODELS%>> "%LOG%"
)
echo.>> "%LOG%"

:: --- 12. Ollama-Erreichbarkeit (live testen) -------------------------------
echo [*] Ollama-Erreichbarkeit...
echo --- 12) OLLAMA ERREICHBARKEIT (live) -------------------------------------->> "%LOG%"
if not exist "%PY%" goto sec12skip
call :ollping 11500 Bundle
call :ollping 11434 System
goto sec12done
:sec12skip
echo   uebersprungen - kein Python>> "%LOG%"
:sec12done
echo Hinweis: Fehler hier ist normal solange Ollama noch nicht laeuft.>> "%LOG%"
echo.>> "%LOG%"

:: --- 13. Umgebungsvariablen ------------------------------------------------
echo --- 13) RELEVANTE UMGEBUNGSVARIABLEN -------------------------------------->> "%LOG%"
echo OLLAMA_HOST   = %OLLAMA_HOST%>> "%LOG%"
echo OLLAMA_MODELS = %OLLAMA_MODELS%>> "%LOG%"
echo PYTHONUTF8    = %PYTHONUTF8%>> "%LOG%"
echo PYTHONHOME    = %PYTHONHOME%>> "%LOG%"
echo PYTHONPATH    = %PYTHONPATH%>> "%LOG%"
echo.>> "%LOG%"

echo ===========================================================================>> "%LOG%"
echo  Ende des Reports>> "%LOG%"
echo ===========================================================================>> "%LOG%"

:: Report anzeigen
cls
type "%LOG%"
echo.
echo ===========================================================================
echo  Fertig. Report gespeichert als:
echo  %LOG%
echo  Bitte diese Datei (diagnose_report.txt) zuruecksenden.
echo ===========================================================================
echo.
pause
endlocal
exit /b 0

:: --- Subroutine: einzelner Import-Test (kann die Batch nicht abbrechen) -----
:pytest
"%PY%" -c "import %~1" >nul 2>&1
if errorlevel 1 (
    echo   [FEHLT] %~1>> "%LOG%"
) else (
    echo   [OK]  %~1>> "%LOG%"
)
goto :eof

:: --- Subroutine: Ollama-Port anpingen (Port=%1, Label=%2) -------------------
:ollping
echo Port %~1 [%~2]:>> "%LOG%"
"%PY%" -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:%~1/api/tags',timeout=3)" >nul 2>&1
if errorlevel 1 (
    echo   [NICHT erreichbar]>> "%LOG%"
) else (
    echo   [OK - antwortet]>> "%LOG%"
)
goto :eof
