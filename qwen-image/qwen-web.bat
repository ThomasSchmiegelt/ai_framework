@echo off
rem Qwen-Image · schlankes Web-Tool - Start (Windows)
setlocal
cd /d "%~dp0"
set PY=python
if exist "venv\Scripts\python.exe" set PY="venv\Scripts\python.exe"
start "" "http://127.0.0.1:7871"
%PY% qwen-web.py %*
pause
