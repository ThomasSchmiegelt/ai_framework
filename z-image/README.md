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

### Optional: Hugging-Face-Token (schnellere Downloads)

Ohne Token lädt das Modell trotzdem — nur **langsamer und gedrosselt**
(unauthentifizierte Anfragen sind rate-limitiert). Ein kostenloser **Read-Token**
(https://huggingface.co/settings/tokens) hebt das Limit an und beschleunigt den
einmaligen ~20-GB-Download deutlich. Drei Wege, alle optional:

- **Beim Installieren angeben:** `install_zimage.ps1 -HfToken "hf_xxx"` bzw.
  `./install_zimage.sh --hf-token hf_xxx` (oder `HF_TOKEN=hf_xxx ./install_zimage.sh`).
  Läuft der Installer interaktiv, fragt er den Token einmal ab (Überspringen mit Enter).
- **Umgebungsvariable:** `HF_TOKEN` setzen — wird automatisch erkannt und hat Vorrang.
- **Lokale Datei:** den Token in **`z-image/hf_token.txt`** legen (eine Zeile).

Der Token ist ein **Geheimnis**: Er wird in `hf_token.txt` gespeichert, ist per
`.gitignore` vom Repo ausgeschlossen und wird **nie** ausgegeben oder geloggt.

> **MIT-konform:** Der Token ist reine Zugangs-Konfiguration, **keine neue
> Abhängigkeit**. Gelesen wird er von `huggingface_hub` (Apache-2.0), das ohnehin
> installiert ist — es kommt kein Copyleft und kein zusätzliches Paket hinzu.

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
| `--init datei` | **Bildbearbeitung (img2img):** Eingabebild, das verändert wird | — |
| `--strength F` | img2img: wie stark verändert wird (0.1 wenig … 0.95 stark) | `0.55` |
| `--mask datei` | **Inpainting:** Maske (weiß = ändern, schwarz = behalten); nur mit `--init` | — |

**Bildbearbeitung (img2img):** mit `--init` wird statt Text→Bild ein vorhandenes Bild
anhand des Prompts verändert (`bild.bat "im Aquarellstil" --init foto.jpg --strength 0.6`).
Mit zusätzlicher `--mask` wird nur der markierte Bereich verändert (**Inpainting**,
`ZImageInpaintPipeline`; weiß = ändern). Der Bild-Server (`sd_server.py`) bietet beides
als A1111-Endpunkt `POST /sdapi/v1/img2img` an (`init_images`/`denoising_strength`,
optional `mask`) — so nutzt die Chat-Bildbearbeitung `/bildedit` (inkl. „🖌 Bereich
markieren") dieselbe crash-sichere Brücke.

## Im Chat des AI-Frameworks nutzen (🎨 lokal über Z-Image)

Die 🎨-Bildfunktion im Chat spricht die Schnittstelle eines **Stable-Diffusion-WebUI**
(`local::sd`) an. Damit sie **Z-Image** nutzt, startest du die mitgelieferte **Brücke**
`sd_server.py` — ein kleiner, A1111-kompatibler HTTP-Server (nur Python-Standardbibliothek,
keine neue Abhängigkeit), der genau den einen Endpunkt `POST /sdapi/v1/txt2img` bedient:

```bash
# Windows
sd_server.bat                 # crash-sicher (Offload, teilt die GPU mit Ollama)
sd_server.bat --full-gpu      # schneller, höherer Spitzen-VRAM
# Linux
./sd_server.sh
```

Dann **einmal im Framework-Profil** unter **🧠 Modelle → 🎨 Bildgenerierung**:

1. **Lokal · Stable Diffusion WebUI** auswählen,
2. als Adresse **`http://127.0.0.1:7860`** eintragen (bzw. `--host`/`--port` des Servers),
3. speichern.

> **Auto-Start:** Läuft der Server nicht, **startet das Framework ihn bei Bedarf selbst**
> (beim Erzeugen/Bearbeiten von Bildern und beim Präsentationsassistenten). Es sucht den
> Ordner in dieser Reihenfolge: Profil `sd_server_dir` → `z-image/` im Repo → `~/z-image`
> (Standalone) — jeweils mit eigener `venv`. Abschaltbar über das Profil-Flag
> `sd_autostart` (Standard an). Manuell starten (`sd_server.bat`) bleibt jederzeit möglich
> und hält den Server unabhängig offen.

Danach erzeugen `/bild …`, `/bildhelp`, der 🎨-Haken **und `[bild]`-Schritte im
`/workflow`** die Bilder **lokal über Z-Image**. Der Server ignoriert die vom Framework fest
gesendeten `steps=28`/`cfg=6.5` und nutzt die Turbo-Werte (8 Schritte, guidance 0).

### Warum das crash-sicher ist

Der Server-Prozess importiert selbst **kein** torch/CUDA. Jede Bilderzeugung läuft in
einem **frischen Unterprozess** (`generate.py`), der genau **ein** Bild erzeugt und sich
dann beendet — der bewiesen stabile Weg (identisch zu `bild.bat`). Dadurch:

- **kein VRAM-/Fragmentierungs-Aufbau** über mehrere Läufe (genau das ließ eine frühere
  In-Prozess-Variante beim 2. Bild hart abstürzen und riss den ganzen Rechner mit);
- ein CUDA-Fehler tötet **nur den Unterprozess** — der Server meldet ihn und lebt weiter;
- nach jedem Bild ist der **VRAM wieder komplett frei** (getestet: nach jeder Erzeugung
  ~0,4 GB belegt), Koexistenz mit Ollama.

**Preis:** jedes Bild lädt das Modell neu aus dem Cache (~30–50 s). Dafür stabil. Optionen:
`--full-gpu` (schneller, höherer VRAM), `--steps N`, `--timeout N`, `--min-free GB`,
`--keep-ollama`. Test ohne Framework:

```bash
curl -s http://127.0.0.1:7860/health     # {"status":"ok",…,"mode":"subprocess (crash-sicher)"}
```

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
