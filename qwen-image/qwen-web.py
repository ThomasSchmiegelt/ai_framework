#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Qwen-Image (2.1) – kleines, eigenständiges Web-Tool

Steuert das lokale Qwen-Image-Modell **komplett unabhängig vom AI-Framework**
an: eine dünne HTML-Oberfläche (Prompt → Bild) plus optional Bildbearbeitung
(img2img). Keine Abhängigkeiten außer der **Standardbibliothek** – der Server
importiert bewusst KEIN torch/diffusers, sondern startet für jede Erzeugung
einen **frischen Unterprozess** `generate.py` in der lokalen `venv/` (crash-
sicher: stürzt eine Erzeugung ab, stirbt nur der Unterprozess, der Server
lebt weiter und gibt den VRAM vollständig frei).

Start (Windows):   .\\qwen-web.bat
Start (Linux):     ./qwen-web.sh
Manuell:           python qwen-web.py            (Port standard 7871)
                   python qwen-web.py --port 9000
Anschließend im Browser öffnen:   http://127.0.0.1:7871

Port-Aufteilung (alle lokal, keine Kollision):
    8780  AI-Framework      7860  Z-Image-Brücke      7861  Qwen-Brücke  7871  dieses Tool

Lizenz: MIT.
"""
import argparse
import base64
import json
import os
import shutil
import subprocess
import tempfile
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
GEN = os.path.join(HERE, "generate.py")


def _venv_python() -> tuple:
    """Erzeugungs-Python (mit torch+diffusers+CUDA) finden.

    Reihenfolge:
      1. `QWEN_PYTHON`-Umgebungsvariable (explizites Python-Exe, z. B. anderer venv)
      2. lokale `qwen-image/venv`
      3. verwandte `z-video/venv` (läuft, wenn die eigene qwen-venv blockiert ist –
         z. B. durch Windows Application Control; z-video hat das identische
         diffusers-Paket und CUDA-fähiges torch)
      4. `z-image/venv` (gleiche diffusers-Familie)
      5. aktueller Interpreter (Letzter Ausweg)

    Liefert (Pfad, Hinweis); Hinweis ist "" bei eigener venv, sonst eine
    Erklärung an den Nutzer.
    """
    ov = os.environ.get("QWEN_PYTHON", "").strip().strip('"')
    if ov and os.path.exists(ov):
        return (ov, f"QWEN_PYTHON-Umgebungsvar: {ov}")
    for rel in ("venv/Scripts/python.exe", "venv/bin/python"):
        p = os.path.join(HERE, rel)
        if os.path.exists(p):
            return (p, "lokale qwen-venv")
    # verwandte venv als Fallback (identische diffusers-Pipelines, CUDA-torch)
    for parent in ("z-video", "z-image"):
        for rel in ("Scripts/python.exe", "bin/python"):
            p = os.path.normpath(os.path.join(HERE, "..", parent, "venv", rel))
            if os.path.exists(p):
                return (p, f"Fallback-venv: {parent}/venv")
    import sys
    return (sys.executable, "aktueller Python-Interpreter")


def _read_pngs(d: str) -> list:
    """Alle PNGs im Ordner, sortiert nach Dateiname (Zeile 1..)."""
    try:
        files = sorted(f for f in os.listdir(d) if f.lower().endswith(".png"))
    except OSError:
        return []
    out = []
    for f in files:
        try:
            with open(os.path.join(d, f), "rb") as fh:
                out.append(fh.read())
        except OSError:
            pass
    return out


DEFAULT_HTML = r"""<!doctype html>
<html lang="de"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Qwen-Image · Bildgenerierung</title>
<style>
  :root{--bg:#0e1116;--panel:#161b22;--panel2:#1d242e;--text:#e6edf3;--dim:#8b949e;
        --acc:#3fb950;--acc2:#2ea043;--border:#2d333b;--input:#0b0d10}
  *{box-sizing:border-box}
  body{margin:0;font:15px/1.45 system-ui,Segoe UI,Arial,sans-serif;background:var(--bg);
       color:var(--text);display:flex;justify-content:center;padding:28px 16px}
  .wrap{width:100%;max-width:860px}
  h1{font-size:20px;margin:0 0 4px;font-weight:600}
  .sub{color:var(--dim);font-size:13px;margin:0 0 20px}
  .card{background:var(--panel);border:1px solid var(--border);border-radius:12px;
        padding:16px;margin-bottom:16px}
  textarea{width:100%;min-height:96px;resize:vertical;background:var(--input);color:var(--text);
           border:1px solid var(--border);border-radius:8px;padding:10px 12px;font:inherit}
  .row{display:flex;flex-wrap:wrap;gap:12px;margin-top:12px}
  .ctl{display:flex;flex-direction:column;gap:4px}
  .ctl label{font-size:12px;color:var(--dim)}
  input,select{background:var(--input);color:var(--text);border:1px solid var(--border);
               border-radius:7px;padding:7px 9px;font:inherit}
  input[type=number]{width:88px}
  .chk{flex-direction:row;align-items:center;gap:8px;margin-top:12px}
  .chk label{font-size:14px;color:var(--text);display:flex;gap:8px;align-items:center;cursor:pointer}
  .file{display:flex;flex-direction:column;gap:4px;margin-top:12px}
  .btn{display:inline-flex;align-items:center;gap:8px;background:var(--acc);color:#04140a;
       border:0;border-radius:9px;padding:11px 20px;font:600 15px inherit;cursor:pointer}
  .btn:hover{background:var(--acc2)}
  .btn:disabled{opacity:.5;cursor:not-allowed}
  .bar{display:none;align-items:center;gap:10px;margin-top:14px;color:var(--dim);font-size:13px}
  .bar.on{display:flex}
  .spin{width:15px;height:15px;border:2px solid var(--border);border-top-color:var(--acc);
        border-radius:50%;animation:sp .7s linear infinite}
  @keyframes sp{to{transform:rotate(360deg)}}
  .result{display:none}
  .result.on{display:block}
  .result img{width:100%;border-radius:10px;border:1px solid var(--border);display:block}
  .rmeta{display:flex;flex-wrap:wrap;gap:10px;align-items:center;margin-top:10px;color:var(--dim);font-size:13px}
  .dl{color:var(--acc);text-decoration:none;font-weight:600}
  .err{color:#ff7b72;display:none;margin-top:12px;white-space:pre-wrap;font-size:13px}
  .err.on{display:block}
  a.top{color:var(--dim);text-decoration:none;font-size:13px}
  .hint{font-size:12px;color:var(--dim);margin-top:6px}
</style>
</head><body>
<div class="wrap">
  <h1>Qwen-Image 2.1 <span class="sub" style="margin:0">· lokal · GPU</span></h1>
  <p class="sub">Eigenständiges, schlankes Werkzeug – läuft ohne AI-Framework.</p>

  <div class="card">
    <textarea id="prompt" placeholder="Beschreibe das Bild, z.&nbsp;B. &#8222;ein roter Sportwagen im Sonnenuntergang, kinematischer Stil&#8221;…"></textarea>
    <div class="row">
      <div class="ctl"><label>Breite</label>
        <select id="w"><option>1024</option><option>1328</option><option>768</option><option>1536</option></select></div>
      <div class="ctl"><label>Höhe</label>
        <select id="h"><option>1024</option><option>1328</option><option>768</option><option>1536</option></select></div>
      <div class="ctl"><label>Schritte</label>
        <input type="number" id="steps" value="50" min="1" max="150"></div>
      <div class="ctl"><label>Seed (leer = zufällig)</label>
        <input type="number" id="seed" placeholder=""></div>
      <div class="ctl"><label>Anzahl</label>
        <input type="number" id="batch" value="1" min="1" max="8"></div>
    </div>
    <div class="chk">
      <input type="checkbox" id="offload">
      <label for="offload">CPU-Offload erzwingen <span class="hint">(spart VRAM, langsamer – bei begrenztem Grafikkartenspeicher)</span></label>
    </div>
    <div class="file">
      <label style="font-size:12px;color:var(--dim)">Bildbearbeitung (optional) – vorhandenes Bild als Anfang (img2img)</label>
      <div style="display:flex;gap:12px;flex-wrap:wrap">
        <input type="file" id="img" accept="image/*">
        <div class="ctl"><label>Stärke (0.1–0.95)</label>
          <input type="number" id="strength" value="0.6" step="0.05" min="0.1" max="0.95" style="width:90px"></div>
      </div>
    </div>
    <div style="margin-top:16px;display:flex;align-items:center;gap:14px">
      <button class="btn" id="go">▶ Erzeugen</button>
      <div class="bar" id="bar"><span class="spin"></span><span id="btxt">Lade Modell &nbsp;·&nbsp; 0 s</span></div>
    </div>
    <div class="err" id="err"></div>
  </div>

  <div class="card result" id="res">
    <img id="out" alt="Ergebnis">
    <div class="rmeta"><span id="meta"></span></div>
    <div class="rmeta">
      <a class="dl" id="dl" href="#" download>⬇ PNG speichern</a>
      <span id="count" class="hint" style="margin:0 0 0 auto"></span>
    </div>
  </div>
</div>

<script>
const $=id=>document.getElementById(id);
let busy=false, t0=0, timer=null;
function startBar(){ t0=Date.now(); $("bar").classList.add("on");
  timer=setInterval(()=>{$("btxt").textContent = "Lade Modell & erzeugt · " + ((Date.now()-t0)/1000|0) + " s";},1000); }
function stopBar(){ clearInterval(timer); timer=null; }
function fileToB64(file){ return new Promise((res,rej)=>{ const r=new FileReader();
  r.onload=()=>{ const i=String(r.result).split("base64,")[1]||""; res(i); };
  r.onerror=rej; r.readAsDataURL(file); }); }

$("go").onclick=async()=>{
  const prompt=$("prompt").value.trim();
  if(!prompt){ $("err").textContent="Bitte einen Prompt eingeben."; $("err").classList.add("on"); return; }
  const body={ prompt, width:+$("w").value, height:+$("h").value,
               steps:+$("steps").value, batch:Math.max(1,+$("batch").value||1),
               offload:$("offload").checked };
  const sd=$("seed").value.trim(); if(sd!=="") body.seed=+sd;
  const f=$("img").files[0];
  if(f){ body.image_b64=await fileToB64(f); body.strength=+$("strength").value; }
  $("err").classList.remove("on"); $("res").classList.remove("on");
  busy=true; $("go").disabled=true; startBar();
  try{
    const r=await fetch("/api/generate",{method:"POST",
      headers:{"Content-Type":"application/json"}, body:JSON.stringify(body)});
    const d=await r.json();
    if(!r.ok) throw new Error(d.error||("Fehler HTTP " + r.status));
    const im = d.images[0] || "";
    if(!im) throw new Error("Kein Bild erhalten: " + (d.note||""));
    $("out").src="data:image/png;base64,"+im;
    $("dl").href="data:image/png;base64,"+im;
    $("meta").textContent=(d.seconds!=null? d.seconds.toFixed(1)+" s · ":"")+ (d.note||"");
    $("count").textContent = d.images.length>1 ? (d.images.length+" Bilder erzeugt") : "";
    $("res").classList.add("on");
  }catch(e){ $("err").textContent=String(e.message||e); $("err").classList.add("on"); }
  finally{ busy=false; $("go").disabled=false; stopBar(); }
};
$("prompt").addEventListener("keydown",e=>{ if((e.ctrlKey||e.metaKey)&&e.key==="Enter") $("go").click(); });
</script>
</body></html>
"""


class Handler(BaseHTTPRequestHandler):
    server_version = "QwenWeb/1.0"

    def _send(self, code, body, ctype):
        data = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _json(self, code, obj):
        self._send(code, json.dumps(obj, ensure_ascii=False), "application/json; charset=utf-8")

    def log_message(self, *a):
        pass  # schlank: kein Request-Log im Terminal

    def do_GET(self):
        p = self.path.split("?", 1)[0].rstrip("/")
        if p in ("", "/index.html"):      # "/" → "" (rstrip) → Startseite
            return self._send(200, DEFAULT_HTML, "text/html; charset=utf-8")
        if p == "/health":
            return self._json(200, {"ok": True})
        return self._json(404, {"error": "Nicht gefunden."})

    def do_POST(self):
        if self.path.rstrip("/") != "/api/generate":
            return self._json(404, {"error": "Nicht gefunden."})
        try:
            n = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(n) if n else b"{}"
            b = json.loads(raw.decode("utf-8") or "{}")
        except Exception as e:
            return self._json(400, {"error": f"Ungültige Anfrage: {e}"})

        prompt = (b.get("prompt") or "").strip()
        if not prompt:
            return self._json(400, {"error": "Kein Prompt angegeben."})

        work = tempfile.mkdtemp(prefix="qwenweb_")
        try:
            cmd = [_venv_python()[0], GEN, prompt,
                   "--width", str(int(b.get("width") or 1024)),
                   "--height", str(int(b.get("height") or 1024)),
                   "--steps", str(int(b.get("steps") or 50)),
                   "--outdir", work, "--out", "result"]

            seed = b.get("seed")
            if seed not in (None, ""):
                cmd += ["--seed", str(int(seed))]

            batch = int(b.get("batch") or 1)
            if batch > 1:
                cmd += ["--batch", str(batch)]

            if b.get("offload"):
                cmd.append("--offload")

            # Bildbearbeitung (img2img)
            img_b64 = (b.get("image_b64") or "").strip()
            if img_b64:
                try:
                    init_path = os.path.join(work, "in.png")
                    with open(init_path, "wb") as fh:
                        fh.write(base64.b64decode(img_b64))
                    cmd += ["--init", init_path,
                            "--strength", str(round(float(b.get("strength") or 0.6), 3))]
                except Exception as e:
                    return self._json(400, {"error": f"Upload nicht lesbar: {e}"})

            t0 = time.time()
            proc = subprocess.run(cmd, capture_output=True, text=True,
                                  timeout=3600, cwd=HERE)
            pngs = _read_pngs(work)

            if proc.returncode != 0 or not pngs:
                tail = (proc.stderr or proc.stdout or "").strip()
                err = tail[-1500:]
                hint = ""
                low = (err or "").lower()
                if "no module named 'diffusers'" in low or "no module named 'torch'" in low:
                    hint = "  → installieren:  .\\install_qwimage.ps1  (bzw.  ./install_qwimage.sh)"
                elif "qwenimagepipeline" in low and "importerror" in low:
                    hint = "  → diffusers zu alt:  python -m pip install -U diffusers"
                return self._json(500, {"error": "Erzeugung fehlgeschlagen.\n" + (err or "unbekannter Fehler") + hint})

            images = [base64.b64encode(x).decode("ascii") for x in pngs]
            secs = round(time.time() - t0, 1)
            note = proc.stdout.strip()[-200:]
            return self._json(200, {"images": images, "seconds": secs, "note": note})
        except subprocess.TimeoutExpired:
            return self._json(504, {"error": "Zeitüberschreitung – Erzeugung dauert zu lange (vielleicht CPU-Modus ohne Offload-Schalter)."})
        except FileNotFoundError as e:
            return self._json(500, {"error": f"Python/generate.py nicht gefunden: {e}\n  → .\\install_qwimage.ps1  (bzw.  ./install_qwimage.sh)"})
        except Exception as e:
            return self._json(500, {"error": f"{type(e).__name__}: {e}"})
        finally:
            shutil.rmtree(work, ignore_errors=True)


def main():
    ap = argparse.ArgumentParser(description="Qwen-Image – schlankes Web-Tool")
    ap.add_argument("--port", type=int, default=7871, help="Port (Default: 7871)")
    ap.add_argument("--host", default="127.0.0.1", help="Bind-Adresse (Default: 127.0.0.1)")
    args = ap.parse_args()

    if not os.path.exists(GEN):
        print(f"FEHLER: generate.py nicht gefunden in: {HERE}")
        raise SystemExit(1)

    py, _why = _venv_python()
    srv = ThreadingHTTPServer((args.host, args.port), Handler)
    url = f"http://{'127.0.0.1' if args.host in ('0.0.0.0','' ) else args.host}:{args.port}"
    print("=" * 60)
    print("  Qwen-Image · Web-Tool (eigständig, ohne AI-Framework)")
    print(f"  Oberfläche:   {url}")
    print(f"  Server-Python:{py}")
    print(f"  Erzeugung:    frischer Unterprozess je Bild (crash-sicher)")
    print("  Beenden:      Strg+C")
    print("=" * 60)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nBeendet.")


if __name__ == "__main__":
    main()
