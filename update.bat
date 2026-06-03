@echo off
setlocal EnableExtensions EnableDelayedExpansion
title AI_Framework_Thomas - Update (nur Systemdateien)

:: ===========================================================================
::  AI_Framework_Thomas  -  Update
::  Tauscht NUR den Programmcode (Systemdateien) aus und laesst alle
::  Nutzerdaten + Konfiguration unberuehrt.
::
::  UNVERAENDERT bleiben:
::    data\         Konversationen, Agenten, Plaene, RAG-DB, Profil,
::                  Branding, mail.json, mail_rules.json ...
::    config.json   z.B. eigener Ollama-Port 11500 im Portable-Bundle
::    venv\ python\ ollama\   inkl. gebuendelter Modelle
::    _update_backup\  Sicherung der vorherigen Version
::
::  ERSETZT werden:
::    main.py, db.py, requirements.txt, test_chat.py,
::    static\, tools\, docs\, scripts\, samples\, bilder\,
::    *.md, *.ps1, *.bat Startskripte, LICENSE
::
::  Aufruf:   update.bat  "Pfad\zur\bestehenden\Installation"
::            Ohne Argument wird der Zielpfad abgefragt.
::  Quelle  = der Ordner, in dem DIESE update.bat liegt (die neue Version).
:: ===========================================================================

echo.
echo  ============================================================
echo    AI_Framework_Thomas  -  Update (nur Systemdateien)
echo  ============================================================
echo.

:: --- Quelle = Ordner dieser update.bat (neue Version) ---
set "SRC=%~dp0"

:: --- Ziel = Argument 1, sonst abfragen ---
set "DST=%~1"
if not "%DST%"=="" goto havedst
echo  Ziel = die bestehende Installation, die aktualisiert werden soll.
set /p "DST=Pfad zur Installation [leer = aktueller Ordner]: "
:havedst
if "%DST%"=="" set "DST=%CD%"
set "DST=%DST:"=%"

:: --- Code-Wurzel in Quelle bestimmen (flach oder \app) ---
set "SRCCODE="
if exist "%SRC%main.py"     set "SRCCODE=%SRC%"
if exist "%SRC%app\main.py" set "SRCCODE=%SRC%app\"

:: --- Code-Wurzel im Ziel bestimmen (flach oder \app) ---
set "DSTCODE="
if exist "%DST%\main.py"     set "DSTCODE=%DST%"
if exist "%DST%\app\main.py" set "DSTCODE=%DST%\app"

if defined SRCCODE goto srcok
echo  [X] In der Quelle wurde keine main.py gefunden:
echo      %SRC%
echo      update.bat muss im Ordner der neuen Version liegen.
goto fail
:srcok

if defined DSTCODE goto dstok
echo  [X] Im Ziel wurde keine Installation gefunden (keine main.py):
echo      %DST%
goto fail
:dstok

if not "%SRCCODE:~-1%"=="\" set "SRCCODE=%SRCCODE%\"

echo  Quelle (neu) : %SRCCODE%
echo  Ziel (alt)   : %DSTCODE%
echo.

:: Schutz: nicht ueber sich selbst kopieren
if /I "%SRCCODE%"=="%DSTCODE%\" goto same

set /p "OK=Jetzt aktualisieren? J/N "
if /I not "%OK%"=="J" goto aborted

:: --- 1) Sicherung der bisherigen Version (nur Code, ohne data/venv) ---
echo.
echo  [1/3] Sichere bisherige Version nach _update_backup ...
set "BKP=%DSTCODE%\_update_backup"
if exist "%BKP%" rmdir /s /q "%BKP%"
robocopy "%DSTCODE%" "%BKP%" main.py db.py requirements.txt test_chat.py *.md *.ps1 *.bat LICENSE /XF config.json /NJH /NJS /NFL /NDL /NP >nul
robocopy "%DSTCODE%\static" "%BKP%\static" /E /XD __pycache__ /XF *.pyc /NJH /NJS /NFL /NDL /NP >nul
robocopy "%DSTCODE%\tools"  "%BKP%\tools"  /E /XD __pycache__ /XF *.pyc /NJH /NJS /NFL /NDL /NP >nul
robocopy "%DSTCODE%\docs"   "%BKP%\docs"   /E /NJH /NJS /NFL /NDL /NP >nul
echo        gesichert: %BKP%

:: --- 2) Systemdateien austauschen (data\ und config.json bleiben unberuehrt) ---
echo.
echo  [2/3] Tausche Systemdateien aus ...
set "RCERR=0"

:: Dateien im Stammverzeichnis - config.json und *.db ausdruecklich ausgeschlossen
robocopy "%SRCCODE%." "%DSTCODE%." main.py db.py requirements.txt test_chat.py *.md *.ps1 *.bat LICENSE /XF config.json *.db server.log /NJH /NJS /NDL /NP
if errorlevel 8 set "RCERR=1"

:: Code-Verzeichnisse (additiv; data\ wird NIE beruehrt)
if exist "%SRCCODE%static"  robocopy "%SRCCODE%static"  "%DSTCODE%\static"  /E /XD __pycache__ /XF *.pyc /NJH /NJS /NDL /NP
if errorlevel 8 set "RCERR=1"
if exist "%SRCCODE%tools"   robocopy "%SRCCODE%tools"   "%DSTCODE%\tools"   /E /XD __pycache__ /XF *.pyc /NJH /NJS /NDL /NP
if errorlevel 8 set "RCERR=1"
if exist "%SRCCODE%docs"    robocopy "%SRCCODE%docs"    "%DSTCODE%\docs"    /E /NJH /NJS /NDL /NP
if errorlevel 8 set "RCERR=1"
if exist "%SRCCODE%scripts" robocopy "%SRCCODE%scripts" "%DSTCODE%\scripts" /E /XD __pycache__ /XF *.pyc /NJH /NJS /NDL /NP
if errorlevel 8 set "RCERR=1"
if exist "%SRCCODE%samples" robocopy "%SRCCODE%samples" "%DSTCODE%\samples" /E /NJH /NJS /NDL /NP
if errorlevel 8 set "RCERR=1"
if exist "%SRCCODE%bilder"  robocopy "%SRCCODE%bilder"  "%DSTCODE%\bilder"  /E /NJH /NJS /NDL /NP
if errorlevel 8 set "RCERR=1"

if "%RCERR%"=="1" goto copyerr
echo        Systemdateien aktualisiert.

:: --- 3) Optional: Python-Pakete aktualisieren (neue Abhaengigkeiten) ---
echo.
set "PYEXE="
if exist "%DST%\python\python.exe"           set "PYEXE=%DST%\python\python.exe"
if exist "%DSTCODE%\..\python\python.exe"    set "PYEXE=%DSTCODE%\..\python\python.exe"
if exist "%DSTCODE%\venv\Scripts\python.exe" set "PYEXE=%DSTCODE%\venv\Scripts\python.exe"

if not defined PYEXE goto nopip
echo  [3/3] Python gefunden: !PYEXE!
set "PUP="
set /p "PUP=requirements.txt-Pakete jetzt aktualisieren? J/N "
if /I not "!PUP!"=="J" goto skippip
echo        Installiere/aktualisiere Pakete ...
"!PYEXE!" -m pip install -r "%DSTCODE%\requirements.txt" --quiet --no-warn-script-location
if errorlevel 1 echo        Hinweis: Paket-Update meldete einen Fehler.
if not errorlevel 1 echo        Pakete aktuell.
goto pipdone
:skippip
echo        uebersprungen.
goto pipdone
:nopip
echo  [3/3] Kein gebuendeltes Python/venv gefunden - Paket-Update uebersprungen.
:pipdone

echo.
echo  ============================================================
echo    Update fertig. Nutzerdaten und config.json unveraendert.
echo    Rollback bei Bedarf aus:  %BKP%
echo  ============================================================
echo.
echo  App neu starten (start.bat) und im Browser mit Strg+F5 neu laden.
echo.
pause
exit /b 0

:: ---------------------------------------------------------------------------
:same
echo  [X] Quelle und Ziel sind identisch - nichts zu tun.
goto fail

:aborted
echo  Abgebrochen.
pause
exit /b 0

:copyerr
echo.
echo  [X] Beim Kopieren ist ein Fehler aufgetreten. Bitte Meldungen oben pruefen.
echo      Die vorherige Version liegt in: %BKP%
pause
exit /b 1

:fail
echo.
pause
exit /b 1
