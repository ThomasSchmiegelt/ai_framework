# Qwen-Image-2.1 — lokale Bildgenerierung (optional, eigenständig)

Eigenständiges, **opt-in**-Werkzeug, das **Qwen-Image-2.1**
(https://huggingface.co/Qwen/Qwen-Image-2.1, Apache-2.0) **komplett lokal auf der
GPU** ausführt — über `diffusers` (`QwenImagePipeline`) in einer **eigenen venv**
im Ordner `venv/`. Die Modellgewichte (~15–20 GB) landen in der venv bzw. im
Hugging-Face-Cache, **nicht** im Repo.

> **Abgrenzung:** Das ist *nicht* die Chat-Bildgenerierung des AI_Framework
> (`local::sd` / API-Modelle). Dieses Werkzeug ist ein eigenständiger
> Kommandozeilen-Lauf (wie `z-image/`), der optional zusätzlich **als Brücke für
> das Chat-Bild** dienen kann (s. u.).

## Voraussetzungen
- **Windows oder Linux**
- **Python 3.10–3.12**
- **NVIDIA-GPU** mit **≥ 18 GB freiem VRAM** empfohlen (Modelldaten ~15–20 GB +
  Aktivierungspuffer; mit `--offload` läuft es auch auf kleineren Karten, dann langsamer)
- **~30 GB freier Speicher** (venv mit PyTorch + Modellgewichte)
- **Internet** für die einmaligen Downloads (Pakete + Modellgewichte)

## Installation
```powershell
# Windows (mit CUDA-GPU, empfohlen)
.\install_qwimage.ps1
# Windows ohne GPU (CPU, langsam)
.\install_qwimage.ps1 -Cpu
# Windows: .\install_qwimage.bat   (bzw. CPU: install_qwimage.bat CPU)
```
```bash
# Linux (mit CUDA-GPU, empfohlen)
./install_qwimage.sh
# Linux ohne GPU (CPU, langsam)
./install_qwimage.sh --cpu
```

## Nutzung (Befehlszeile)
```powershell
./bild.bat "ein blauer Van auf einem Parkplatz, Fotorealistisch"
./bild.bat "Portrait einer Katze, Studio-Licht" --seed 42 --out katze.png
./bild.bat "Berglandschaft" --width 1536 --height 1024 --offload
```
```bash
./bild.sh "ein blauer Van auf einem Parkplatz, Fotorealistisch"
./bild.sh "Portrait einer Katze, Studio-Licht" --seed 42 --out katze.png
```

### Wichtige Optionen
| Option | Standard | Beschreibung |
|---|---|---|
| `--width` / `--height` | 1024 | Bildgröße (Vielfaches von 32; Qwen nativ ~1328²) |
| `--seed` | zufällig | Zufallssaat (reproduzierbare Bilder) |
| `--batch` | 1 | Anzahl Bilder |
| `--out` / `--outdir` | `./outputs` | Dateiname bzw. Outputordner |
| `--offload` | **an** | Teile GPU mit Ollama (niedriger Spitzen-VRAM). `--full-gpu` = schnell, hoher VRAM |
| `--min-free` | 18.0 | unterhalb dieses freien-VRAM-Werts zwangsförmig CPU-Offload |
| `--keep-ollama` | aus | geladene Ollama-Modelle **nicht** automatisch entladen |
| `--modell` | `Qwen/Qwen-Image-2.1` | anderer Repo-/LoRA-Pfad |
| `--init` + `--strength` | — | **img2img** (Bildbearbeitung) |
| `--mask` | — | **Inpainting** (verwandele nur die markierte Region) |
| `--steps` | 50 | Denoising-Schritte (Qwen-Empfehlung 50) |

> **Negatives Prompt** wird *nicht* unterstützt — Qwen-Image verwendet keinen
> CFG-Guidance-Wert und kennt kein negatives Prompt (rectified flow). Der
> Parameter `--negative` wird akzeptiert und ignoriert.

## Als Chat-Bild-Brücke (A1111-kompatibel)
`sd_server.py` stellt **Qwen-Image** als lokalen, A1111-kompatiblen Bild-Server
dar, damit die 🎨-Bildfunktion im AI_Framework (Profil-Weg **`local::qwen`**)
lokale Bilder erzeugt:
```powershell
.\sd_server.bat          # CPU-Offload, teilt GPU mit Ollama (Port 7861)
.\sd_server.bat --full-gpu
```
```bash
./sd_server.sh
```
Im AI_Framework-Profil unter **🎨 Bildgenerierung** den Weg **Lokal · Qwen-Image 2.1**
wählen und als **SD-WebUI-Adresse** `http://127.0.0.1:7861` eintragen
(der AI_Framework kann die Brücke bei Bedarf auch selbst starten).
**Crash-sicher**: der Server-Prozess importiert *kein* torch; jede Erzeugung läuft in
einem frischen Unterprozess (`generate.py`), der ein Bild erzeugt und sich beendet.
Funktional: txt2img + img2img + Inpainting; die SD-spezifischen Felder
(`steps`/`cfg_scale`/`negative_prompt`) werden ignoriert und durch die
Qwen-Defaults ersetzt.

## Modelle laden
Beim ersten `bild.sh`/`bild.bat`-Aufruf werden die Qwen-Image-2.1-Gewichte
automatisch aus `Qwen/Qwen-Image-2.1` im Hugging-Face-Cache geladen. Danach
offline nutzbar.

**Vorteil lokal:** keine Cloud, keine API-Kosten, keine Daten außerhalb des
Rechners, läuft auch ohne Internet nach dem ersten Lauf → ideal auch im
**Geheim-/Hartman-Modus**.

## Lizenz
Alle Python-Abhängigkeiten sind MIT/Apache/BSD-kompatibel (diffusers,
transformers, accelerate, safetensors, Pillow, huggingface-hub, torch,
torchvision) und Qwen-Image-2.1 ist **Apache 2.0** — damit bleibt das Ganze unter
der MIT-Lizenz des Projekts. (Keine copyleft-Abhängigkeiten.)
