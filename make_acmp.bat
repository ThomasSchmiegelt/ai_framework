@echo off
setlocal
:: ===========================================================================
::  AI_Framework_Thomas  -  ACMP-Updatepaket bauen (Wrapper um make_acmp.ps1)
::
::  Erzeugt ein Paket, das NUR den Programmcode enthaelt - ohne Ollama,
::  Modelle, venv/python und Nutzerdaten. Ollama gehoert in ein eigenes,
::  selten aktualisiertes ACMP-Paket.
::
::  Aufruf:  make_acmp.bat [Version]
::           make_acmp.bat 1.5.0
:: ===========================================================================

set "PSARGS=-Zip"
if not "%~1"=="" set "PSARGS=%PSARGS% -Version %~1"

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0make_acmp.ps1" %PSARGS%
set "RC=%ERRORLEVEL%"

if not "%RC%"=="0" (
  echo.
  echo  [X] Paketbau fehlgeschlagen. Exit-Code: %RC%
)
echo.
pause
endlocal & exit /b %RC%
