# -*- coding: utf-8 -*-
# Einmaliger, robust losgeloster Launcher f黵 den Qwen-Image-2.1-Test.
# Warum Python?  List-Args -> Windows-Quoting wird von subprocess korrekt
# erledigt (kein Start-Process-Argumentarray-Zerlegen).  DETACHED_PROCESS
# laesst den Kindprozess ueberleben, selbst wenn die aufrufende Shell/der
# Python-Interpreter sofort beendet wird.
import subprocess, sys

ROOT   = r"C:\Users\user\ai_framework-main"
PY     = ROOT + r"\z-video\venv\Scripts\python.exe"
GEN    = ROOT + r"\qwen-image\generate.py"
PROMPT = ("a majestic cat sitting on a windowsill, soft golden evening light, "
          "detailed fur, cozy atmosphere")
OUT    = ROOT + r"\qwen-image\outputs"

args = [PY, GEN, PROMPT,
        "--offload", "--width", "1024", "--height", "1024", "--steps", "50",
        "--outdir", OUT, "--out", "testbild.png"]

log = open(ROOT + r"\qwen-image\gen.log", "w", encoding="utf-8")
err = open(ROOT + r"\qwen-image\gen.err.log", "w", encoding="utf-8")

DETACHED_PROCESS   = 0x00000008
CREATE_NO_WINDOW   = 0x08000000
p = subprocess.Popen(args, stdout=log, stderr=err,
                     creationflags=DETACHED_PROCESS | CREATE_NO_WINDOW)
print("GEN_PID=", p.pid)
# Dateien nicht hier schliessen - OS uebernimmt.  Launcher beendet sich sofort.
