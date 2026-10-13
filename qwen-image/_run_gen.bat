@echo off
REM Eingebautes Testbild - sicherer Windows-Quoting via .bat, detached gelaufen.
cd /d C:\Users\user\ai_framework-main\qwen-image
"C:\Users\user\ai_framework-main\z-video\venv\Scripts\python.exe" "C:\Users\user\ai_framework-main\qwen-image\generate.py" "a majestic cat sitting on a windowsill, soft golden evening light, detailed fur, cozy atmosphere" --offload --width 1024 --height 1024 --steps 50 --outdir "C:\Users\user\ai_framework-main\qwen-image\outputs" --out testbild.png > "C:\Users\user\ai_framework-main\qwen-image\gen.log" 2>&1
