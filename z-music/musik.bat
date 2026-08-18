@echo off
REM Musik-Generator (Windows). Braucht nur Python (keine Installation).
REM Aufruf:  musik.bat "fröhliche schnelle Abenteuermelodie"
REM          musik.bat "8bit" --tempo 140 --seed 7
setlocal
cd /d "%~dp0"
py -3 --version >nul 2>&1 && (py -3 "%~dp0generate_music.py" %*) || (python "%~dp0generate_music.py" %*)
endlocal
