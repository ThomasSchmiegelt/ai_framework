"""
Direkter Test des /api/chat-Endpunkts (am Frontend vorbei).
Zeigt die ROHE SSE-Antwort des Backends inkl. echter Fehlermeldung.
Aufruf ueber test_chat.bat (nutzt das Bundle-Python).
"""
import json
import sys
import time
import urllib.request

# Windows-Konsolen laufen oft mit cp1252 statt UTF-8 - Modellantworten enthalten
# haeufig Emojis, die sonst mit UnicodeEncodeError abbrechen.
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

APPDIR = sys.argv[1] if len(sys.argv) > 1 else "."

# config.json einlesen (Port + Standardmodell + Ollama-URL)
cfg = {}
try:
    with open(APPDIR + "/config.json", encoding="utf-8") as fh:
        cfg = json.load(fh)
    print("[OK] config.json gelesen")
except Exception as e:
    print("[FEHLER] config.json nicht lesbar:", e)

port = cfg.get("port", 8780)
model = cfg.get("default_model", "ministral-3:3b")
ollama = cfg.get("ollama_base", "http://localhost:11434")
url = "http://localhost:%s/api/chat" % port

print("App-URL :", url)
print("Modell  :", model)
print("Ollama  :", ollama)
print("=" * 70)


def run(use_tools):
    body = {
        "messages": [{"role": "user", "content": "Antworte mit genau einem kurzen Satz: Sag Hallo."}],
        "model": model,
        "use_tools": use_tools,
    }
    print("\n>>> TEST: use_tools = %s" % use_tools)
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    t0 = time.time()
    got_text = False
    try:
        resp = urllib.request.urlopen(req, timeout=240)
        print("HTTP-Status:", resp.status)
        for raw in resp:
            line = raw.decode("utf-8", "replace").strip()
            if not line or not line.startswith("data:"):
                continue
            payload = line[5:].strip()
            try:
                frame = json.loads(payload)
            except Exception:
                print("  [roh]", payload[:200])
                continue
            ftype = frame.get("type")
            if ftype == "text":
                got_text = True
                sys.stdout.write(frame.get("content", ""))
                sys.stdout.flush()
            elif ftype == "error":
                print("\n  [ERROR-FRAME]:", frame.get("message"))
            elif ftype == "done":
                print("\n  [done] nach %.1fs" % (time.time() - t0))
            else:
                print("\n  [%s] %s" % (ftype, json.dumps(frame, ensure_ascii=False)[:200]))
    except Exception as e:
        print("\n[HTTP-FEHLER] %s: %s" % (type(e).__name__, e))
    if not got_text:
        print("  -> KEIN Text empfangen!")
    print("-" * 70)


run(False)   # zuerst OHNE Tools (einfachster Fall)
run(True)    # dann MIT Tools (Standard im Framework)
print("\nFertig.")
