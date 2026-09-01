@echo off
REM Wan Video-Generator (Windows, Kommandozeile). Beispiele:
REM   video.bat --mode t2v --prompt "ein roter Sportwagen faehrt durch die Wueste" --out clip.mp4
REM   video.bat --mode flf2v --first a.png --last b.png --prompt "sanfte Kamerafahrt" --out clip.mp4
setlocal
cd /d "%~dp0"
"%~dp0venv\Scripts\python.exe" "%~dp0generate_video.py" %*
endlocal
