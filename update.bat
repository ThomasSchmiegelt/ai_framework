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
::    main.py, db.py, requirements.txt, test_chat.py, VERSION,
::    static\, tools\, docs\, scripts\, samples\, bilder\, defaults\,
::    *.md, *.ps1, *.bat Startskripte, LICENSE
::
::  AUFRUF
::    update.bat "Pfad\zur\Installation" [/S] [/LOG:datei] [/NOPIP] [/PIP]
::
::      /S        Silent: keine Rueckfragen, kein "pause" - fuer die
::                Softwareverteilung (ACMP). Ohne /S bleibt der Ablauf
::                interaktiv wie bisher.
::      /LOG:x    Logdatei (Standard: <Ziel>\_update.log)
::      /NOPIP    Python-Pakete NICHT aktualisieren
::      /PIP      Python-Pakete aktualisieren (Standard bei /S)
::
::  EXIT-CODES (fuer ACMP-Erfolgspruefung)
::      0  Update erfolgreich
::      1  allgemeiner Fehler / Abbruch durch Benutzer
::      2  Quelle ungueltig (keine main.py im Skriptordner)
::      3  Ziel ungueltig (dort keine Installation gefunden)
::      4  Fehler beim Kopieren der Systemdateien
::      5  Quelle und Ziel identisch
::
::  Quelle = der Ordner, in dem DIESE update.bat liegt (die neue Version).
:: ===========================================================================

set "SILENT=0"
set "DOPIP=ASK"
set "LOGFILE="
set "DST="

:: --- Quelle = Ordner dieser update.bat (neue Version) ---
:: WICHTIG: VOR der Argument-Schleife sichern. Die Schleife nutzt "shift" (ohne
:: /1), das auch %0 mitverschiebt - danach zeigt %~dp0 nicht mehr auf update.bat,
:: sondern auf ein Argument. Das SRC hier festhalten, bevor geshiftet wird.
set "SRC=%~dp0"

:: --- Argumente einlesen (Reihenfolge egal) ---
:parseargs
if "%~1"=="" goto argsdone
set "A=%~1"
if /I "%A%"=="/S"     ( set "SILENT=1" & shift & goto parseargs )
if /I "%A%"=="/NOPIP" ( set "DOPIP=NO"  & shift & goto parseargs )
if /I "%A%"=="/PIP"   ( set "DOPIP=YES" & shift & goto parseargs )
if /I "%A:~0,5%"=="/LOG:" ( set "LOGFILE=%A:~5%" & shift & goto parseargs )
if not defined DST set "DST=%A%"
shift
goto parseargs
:argsdone

:: Im Silent-Modus sind Rueckfragen unmoeglich -> Pakete standardmaessig mit
:: aktualisieren, damit neue Abhaengigkeiten nicht fehlen.
if "%SILENT%"=="1" if "%DOPIP%"=="ASK" set "DOPIP=YES"

:: --- Ziel abfragen, wenn nicht uebergeben (nur interaktiv moeglich) ---
if not "%DST%"=="" goto havedst
if "%SILENT%"=="1" (
  set "DST=%CD%"
) else (
  echo  Ziel = die bestehende Installation, die aktualisiert werden soll.
  set /p "DST=Pfad zur Installation [leer = aktueller Ordner]: "
)
:havedst
if "%DST%"=="" set "DST=%CD%"
set "DST=%DST:"=%"

:: --- Logdatei festlegen und eroeffnen ---
if not defined LOGFILE set "LOGFILE=%DST%\_update.log"
echo ============================================================ > "%LOGFILE%" 2>nul
if errorlevel 1 set "LOGFILE=%TEMP%\ai_framework_update.log" & echo ============================================================ > "%LOGFILE%"
call :log "AI_Framework_Thomas Update  %DATE% %TIME%"
call :log "Quelle : %SRC%"
call :log "Ziel   : %DST%"
call :log "Silent : %SILENT%   Pakete: %DOPIP%"

:: --- Code-Wurzel in Quelle bestimmen (flach oder \app) ---
set "SRCCODE="
if exist "%SRC%main.py"     set "SRCCODE=%SRC%"
if exist "%SRC%app\main.py" set "SRCCODE=%SRC%app\"

:: --- Code-Wurzel im Ziel bestimmen (flach oder \app) ---
set "DSTCODE="
if exist "%DST%\main.py"     set "DSTCODE=%DST%"
if exist "%DST%\app\main.py" set "DSTCODE=%DST%\app"

if defined SRCCODE goto srcok
call :log "[X] In der Quelle wurde keine main.py gefunden: %SRC%"
call :log "    update.bat muss im Ordner der neuen Version liegen."
set "RC=2"
goto fail
:srcok

if defined DSTCODE goto dstok
call :log "[X] Im Ziel wurde keine Installation gefunden - keine main.py: %DST%"
set "RC=3"
goto fail
:dstok

if not "%SRCCODE:~-1%"=="\" set "SRCCODE=%SRCCODE%\"

:: Schutz: nicht ueber sich selbst kopieren
if /I "%SRCCODE%"=="%DSTCODE%\" (
  call :log "[X] Quelle und Ziel sind identisch - nichts zu tun."
  set "RC=5"
  goto fail
)

:: --- Version der neuen Fassung ermitteln (fuer Log + Registry) ---
set "NEWVER="
if exist "%SRCCODE%VERSION" for /f "usebackq delims=" %%v in ("%SRCCODE%VERSION") do if not defined NEWVER set "NEWVER=%%v"
if not defined NEWVER set "NEWVER=unbekannt"
call :log "Neue Version: %NEWVER%"

if "%SILENT%"=="1" goto doit
set /p "OK=Jetzt aktualisieren? J/N "
if /I not "%OK%"=="J" (
  call :log "Abgebrochen durch Benutzer."
  set "RC=1"
  goto fail
)
:doit

:: --- 1) Sicherung der bisherigen Version (nur Code, ohne data/venv) ---
call :log "[1/4] Sichere bisherige Version nach _update_backup ..."
set "BKP=%DSTCODE%\_update_backup"
if exist "%BKP%" rmdir /s /q "%BKP%"
robocopy "%DSTCODE%" "%BKP%" main.py db.py requirements.txt test_chat.py VERSION *.md *.ps1 *.bat LICENSE /XF config.json /NJH /NJS /NFL /NDL /NP >nul
robocopy "%DSTCODE%\static" "%BKP%\static" /E /XD __pycache__ /XF *.pyc /NJH /NJS /NFL /NDL /NP >nul
robocopy "%DSTCODE%\tools"  "%BKP%\tools"  /E /XD __pycache__ /XF *.pyc /NJH /NJS /NFL /NDL /NP >nul
robocopy "%DSTCODE%\docs"   "%BKP%\docs"   /E /NJH /NJS /NFL /NDL /NP >nul
robocopy "%DSTCODE%\defaults" "%BKP%\defaults" /E /NJH /NJS /NFL /NDL /NP >nul
call :log "      gesichert: %BKP%"

:: --- 2) Systemdateien austauschen (data\, config.json, ollama\ bleiben unberuehrt) ---
call :log "[2/4] Tausche Systemdateien aus ..."
set "RCERR=0"

:: Dateien im Stammverzeichnis - config.json und *.db ausdruecklich ausgeschlossen
robocopy "%SRCCODE%." "%DSTCODE%." main.py db.py requirements.txt test_chat.py VERSION *.md *.ps1 *.bat LICENSE /XF config.json *.db server.log /NJH /NJS /NDL /NP >> "%LOGFILE%" 2>&1
if errorlevel 8 set "RCERR=1"

:: Code-Verzeichnisse (additiv; data\, venv\, python\ und ollama\ werden NIE beruehrt)
call :copydir static
call :copydir tools
call :copydir docs
call :copydir scripts
call :copydir samples
call :copydir bilder
call :copydir defaults

if "%RCERR%"=="1" (
  call :log "[X] Beim Kopieren ist ein Fehler aufgetreten. Vorherige Version: %BKP%"
  set "RC=4"
  goto fail
)
call :log "      Systemdateien aktualisiert."

:: --- 3) Version in der Registry hinterlegen (Erkennung durch ACMP) ---
call :log "[3/4] Registriere Version %NEWVER% ..."
reg add "HKLM\SOFTWARE\AI_Framework_Thomas" /v Version     /t REG_SZ /d "%NEWVER%" /f >nul 2>&1
reg add "HKLM\SOFTWARE\AI_Framework_Thomas" /v InstallPath /t REG_SZ /d "%DST%"    /f >nul 2>&1
reg add "HKLM\SOFTWARE\AI_Framework_Thomas" /v LastUpdate  /t REG_SZ /d "%DATE%"   /f >nul 2>&1
if errorlevel 1 (
  call :log "      Hinweis: Registry-Eintrag nicht moeglich - keine Adminrechte?"
  call :log "      Die Datei %DSTCODE%\VERSION bleibt als Erkennungsquelle nutzbar."
) else (
  call :log "      HKLM\SOFTWARE\AI_Framework_Thomas\Version = %NEWVER%"
)

:: --- 4) Optional: Python-Pakete aktualisieren (neue Abhaengigkeiten) ---
set "PYEXE="
if exist "%DST%\python\python.exe"           set "PYEXE=%DST%\python\python.exe"
if exist "%DSTCODE%\..\python\python.exe"    set "PYEXE=%DSTCODE%\..\python\python.exe"
if exist "%DSTCODE%\venv\Scripts\python.exe" set "PYEXE=%DSTCODE%\venv\Scripts\python.exe"

if not defined PYEXE (
  call :log "[4/4] Kein gebuendeltes Python/venv gefunden - Paket-Update uebersprungen."
  goto pipdone
)
call :log "[4/4] Python gefunden: !PYEXE!"
if "%DOPIP%"=="ASK" (
  set "PUP="
  set /p "PUP=requirements.txt-Pakete jetzt aktualisieren? J/N "
  if /I "!PUP!"=="J" ( set "DOPIP=YES" ) else ( set "DOPIP=NO" )
)
if not "%DOPIP%"=="YES" (
  call :log "      uebersprungen."
  goto pipdone
)
call :log "      Installiere/aktualisiere Pakete ..."
"!PYEXE!" -m pip install -r "%DSTCODE%\requirements.txt" --quiet --no-warn-script-location >> "%LOGFILE%" 2>&1
if errorlevel 1 (
  call :log "      Hinweis: Paket-Update meldete einen Fehler - Update gilt trotzdem als erfolgreich."
) else (
  call :log "      Pakete aktuell."
)
:pipdone

call :log "============================================================"
call :log "  Update auf %NEWVER% fertig. Nutzerdaten und config.json unveraendert."
call :log "  Ollama und Modelle wurden nicht angefasst."
call :log "  Rollback bei Bedarf aus: %BKP%"
call :log "============================================================"
if "%SILENT%"=="0" (
  echo.
  echo  App neu starten ^(start.bat^) und im Browser mit Strg+F5 neu laden.
  echo.
  pause
)
endlocal
exit /b 0

:: ---------------------------------------------------------------------------
:: Verzeichnis kopieren, Fehler in RCERR sammeln
:copydir
if not exist "%SRCCODE%%~1" goto :eof
robocopy "%SRCCODE%%~1" "%DSTCODE%\%~1" /E /XD __pycache__ /XF *.pyc /NJH /NJS /NDL /NP >> "%LOGFILE%" 2>&1
if errorlevel 8 set "RCERR=1"
goto :eof

:: Meldung auf Konsole UND in die Logdatei.
:: Die Umleitung steht bewusst VOR dem echo: endet der Text auf einer Ziffer,
:: wuerde "echo text1>> datei" die 1 als Handle-Nummer lesen.
:log
echo %~1
>>"%LOGFILE%" echo %~1
goto :eof

:fail
call :log "Update fehlgeschlagen - Exit-Code %RC%"
if "%SILENT%"=="0" pause
endlocal & exit /b %RC%
