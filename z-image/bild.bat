@echo off
REM Z-Image-Turbo Starter (Windows). Aufruf:
REM   bild.bat "ein roter Sportwagen im Sonnenuntergang"
REM   bild.bat "Portrait einer Katze" --seed 42 --out katze.png
setlocal
cd /d "%~dp0"
"%~dp0venv\Scripts\python.exe" "%~dp0generate.py" %*
endlocal
