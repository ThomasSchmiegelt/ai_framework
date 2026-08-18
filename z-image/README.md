# Z-Image-Turbo — lokale Bildgenerierung

Eigenständiges Kommandozeilen-Werkzeug, das das **Z-Image-Turbo**-Modell (Alibaba
Tongyi Lab, 6B Parameter, Apache-2.0) über die [🤗 diffusers]-Bibliothek **komplett
lokal auf der GPU** ausführt — Text → Bild in ~8 Schritten, ohne Cloud, ohne
API-Schlüssel. Läuft getrennt vom AI-Framework in einer eigenen venv.

> Dies ist bewusst **kein** ComfyUI und kein Webserver — nur ein schlankes Skript
> (`generate.py`), wie gewünscht. Es hängt **nicht** am Framework-Backend und
> belegt dessen venv nicht.

## Voraussetzungen

- **NVIDIA-GPU mit ≥16 GB VRAM** für die volle bf16-Variante (getestet: RTX 3090,
  24 GB). Weniger VRAM → CPU-Offload (siehe unten), langsamer aber lauffähig.
- **Python 3.10–3.12** und (für CUDA) ein aktueller NVIDIA-Treiber.
- ~30 GB freier Speicherplatz: ~6 GB für PyTorch/Bibliotheken + ~20 GB
  Modellgewichte (einmalig nach `~/.cache/huggingface` bzw.
  `%USERPROFILE%\.cache\huggingface`).

## Installation

**Windows**

```powershell
cd z-image
.\install_zimage.bat            # bzw.  .\install_zimage.ps1
```

**Linux**

```bash
cd z-image
chmod +x install_zimage.sh bild.sh
./install_zimage.sh
```

Der Installer legt `z-image/venv` an, installiert **PyTorch (CUDA 12.4)** und
**diffusers aus dem Quellcode** (die Z-Image-Unterstützung ist noch in keinem
Release enthalten) samt `transformers`, `accelerate`, `safetensors`,
`sentencepiece`, `protobuf`, `Pillow`. Für Rechner ohne CUDA:
`install_zimage.ps1 -Cpu` bzw. `./install_zimage.sh --cpu`.

Die **Modellgewichte** werden nicht beim Installieren, sondern beim **ersten
Bildlauf** automatisch geladen (~20 GB, danach im Cache).

## Benutzung

```bash
# Windows
bild.bat "ein roter Oldtimer-Sportwagen auf einer Bergstraße im Abendlicht, Fotorealismus"
# Linux
./bild.sh "ein roter Oldtimer-Sportwagen auf einer Bergstraße im Abendlicht, Fotorealismus"
```

Fertige Bilder landen in `z-image/outputs/`. Der erste Aufruf dauert länger
(Download + Modell in den VRAM laden); danach je Bild wenige Sekunden.

### Optionen (`generate.py`)

| Option | Bedeutung | Standard |
|---|---|---|
| `--steps N` | Anzahl Denoising-Schritte (Turbo braucht wenige) | `8` |
| `--guidance F` | Guidance-Scale (Turbo: 0) | `0.0` |
| `--width` / `--height` | Bildgröße in Pixeln | `1024` |
| `--seed N` | fester Zufalls-Seed (reproduzierbar) | zufällig |
| `--batch N` | mehrere Bilder auf einmal | `1` |
| `--negative "…"` | Negativ-Prompt | leer |
| `--out datei.png` | Ausgabedateiname | Zeitstempel |
| `--outdir ordner` | Ausgabeordner | `outputs` |
| `--offload` | CPU-Offload erzwingen (spart VRAM) | aus |
| `--offload-seq` | sequenzieller Offload (min. VRAM, am langsamsten) | aus |
| `--min-free GB` | ab wie viel freiem VRAM **ohne** Offload geladen wird | `18` |
| `--keep-ollama` | geladene Ollama-Modelle **nicht** automatisch entladen | aus |

## VRAM-Verwaltung (wichtig bei paralleler Ollama-Nutzung)

Auf einer einzelnen GPU teilen sich Z-Image (~16 GB) und die lokalen Ollama-Chat-
Modelle des Frameworks denselben Speicher. Damit der VRAM **nicht überläuft**,
macht `generate.py` vor jedem Lauf automatisch Folgendes:

1. **Ollama entladen:** geladene Ollama-Modelle werden per `ollama stop`
   entladen und ihr VRAM freigegeben (abschaltbar mit `--keep-ollama`). Läuft
   Ollama nicht oder ist nichts geladen, passiert nichts.
2. **Freien VRAM messen** (`torch.cuda.mem_get_info`, erfasst auch andere
   Prozesse).
3. **Automatischer Offload:** Sind danach weniger als `--min-free` GB (Standard
   18) frei, schaltet das Skript selbsttätig auf **CPU-Offload** statt in einen
   Out-of-Memory-Absturz zu laufen.

Faustregel auf der RTX 3090 (24 GB): Ist der VRAM frei, läuft Z-Image voll auf der
GPU (schnell). Willst du Framework-Chat und Z-Image **gleichzeitig** offen halten,
starte mit `--offload`.

## Lizenz

- **Skripte in diesem Ordner:** MIT (wie das Gesamtprojekt).
- **Modell Z-Image-Turbo:** Apache-2.0 (Tongyi-MAI/Z-Image-Turbo auf Hugging Face).
- **diffusers / transformers / PyTorch:** jeweils Apache-2.0 bzw. BSD.

[🤗 diffusers]: https://github.com/huggingface/diffusers
