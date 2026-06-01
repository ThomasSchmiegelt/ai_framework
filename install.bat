@echo off
:: UAC-Elevation - Administrator-Rechte anfordern
net session >nul 2>&1
if %errorlevel% neq 0 (
    echo [*] Fordere Administrator-Rechte an...
    powershell -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
    exit /b
)

cd /d "%~dp0"
powershell -ExecutionPolicy Bypass -NoProfile -File "%~dp0install.ps1"
pause
