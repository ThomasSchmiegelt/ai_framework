@echo off
chcp 65001 >nul
echo.
echo AI_Framework_Thomas Deinstallation wird gestartet...
echo.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0uninstall.ps1"
