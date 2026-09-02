# Wan – lokale Videogenerierung (z-video)

Eigenständiges, **optionales** Werkzeug für **lokale Videoerzeugung** auf einer
starken GPU – Gegenstück zu `z-image/` (Bild), nur für Video. Es hängt **nicht**
am FastAPI-Backend, sondern läuft in einer **eigenen venv** und stellt dem
Framework eine crash-sichere HTTP-Brücke bereit.

Modell: **Wan 2.1** (Alibaba, **Apache-2.0**). Drei Modi:

| Modus | Eingabe | Zweck |
|---|---|---|
| `flf2v` | **Start- und Endbild** (+ Text) | Erst-/Letztbild → Video (Interpolation) |
| `i2v`   | Startbild (+ Text) | Einzelbild wird animiert |
| `t2v`   | nur Text | Text → Video |

> **Achtung – große Modelle:** Die 14B-Wan-Gewichte sind **~30–70 GB** und
> brauchen eine leistungsfähige GPU (viel VRAM). Für schwächere Karten lässt sich
> über `--model` ein kleineres Wan-Repo wählen (z. B. `Wan-AI/Wan2.1-T2V-1.3B-Diffusers`).
> Die Gewichte landen im Hugging-Face-Cache, **nicht** im Repo.

## Installation

```powershell
# Windows (GPU/CUDA)
.\install_zvideo.ps1
```
```bash
# Linux (GPU/CUDA)
./install_zvideo.sh
```

Optionaler Hugging-Face-Token (schnellere Downloads): `-HfToken hf_xxx` /
`--hf-token hf_xxx` bzw. `HF_TOKEN=…`. Der Token wird lokal in `hf_token.txt`
gespeichert (gitignored, **niemals** ins Repo).

## Modell vorab laden (empfohlen bei großen Modellen)

Der **erste** Videolauf lädt mehrere zehn GB Gewichte von Hugging Face. Bricht diese
Übertragung mittendrin ab, scheitert der ganze Lauf. Lade die Gewichte deshalb **einmalig
vorab** – der Download ist **wiederaufsetzbar** (erneut starten überspringt Fertiges):

```powershell
.\download_model.bat --mode flf2v      # Erst-/Letztbild (720P)
.\download_model.bat --mode i2v        # Einzelbild → Video (720P)
.\download_model.bat --mode t2v        # Text → Video (14B)
```
```bash
./download_model.sh --mode flf2v
./download_model.sh --model Wan-AI/Wan2.1-T2V-1.3B-Diffusers   # kleiner
```

Bricht die Leitung ab, **einfach erneut starten** – bereits geladene Dateien bleiben im
Cache. Das Skript setzt zudem bei Netzfehlern automatisch mehrfach neu an und schaltet das
Xet-Backend ab (der klassische HTTPS-Download ist zuverlässiger). Danach startet die
eigentliche Erzeugung (Tab/Chat/CLI) **ohne** weiteren Download.

> **Zeitlimit:** Es gibt **standardmäßig kein Zeitlimit** je Video (damit der erste,
> lange Download nicht abgeschnitten wird). Im Framework-Profil unter **🎬 Videoerzeugung**
> lässt sich optional ein hartes Limit (Minuten) setzen – nur einschalten, wenn ein Lauf
> bewusst abgebrochen werden soll.

## Nutzung im Framework (empfohlen)

1. Server starten – oder das **Framework startet ihn bei Bedarf selbst**
   (Profil `video_autostart`, Standard an):
   ```
   video_server.bat        (Windows)
   ./video_server.sh       (Linux)
   ```
2. Im Framework-**Profil** unter **🎬 Videoerzeugung**: **Lokal · Wan** wählen und
   als Adresse `http://127.0.0.1:7870` eintragen.
3. Im Framework den Tab **🎬 Videoerzeugung** nutzen oder im Chat `/video`.

Die Brücke ist **crash-sicher**: Sie importiert selbst kein torch/CUDA und startet
je Video einen **frischen Unterprozess** (`generate_video.py`), der genau ein Video
erzeugt, den VRAM danach freigibt und sich beendet. Ein CUDA-Fehler killt nur den
Unterprozess – der Server lebt weiter. `generate_video.py` entlädt vor dem Lauf
automatisch geladene Ollama-Modelle und weicht bei VRAM-Knappheit auf CPU-Offload
aus.

## Direkte Kommandozeile

```bash
./video.sh --mode t2v   --prompt "ein roter Sportwagen faehrt durch die Wueste" --out clip.mp4
./video.sh --mode i2v   --first foto.png --prompt "leichter Wind" --out clip.mp4
./video.sh --mode flf2v --first a.png --last b.png --prompt "sanfte Kamerafahrt" --out clip.mp4
```

Wichtige Optionen: `--frames` (Länge, Wan-Standard 81 ≈ 5 s), `--fps`, `--steps`,
`--size` (`480p`/`720p`/`square` oder `BxH`), `--seed`, `--model`, `--offload` /
`--offload-seq` (VRAM sparen).

## Lizenz

Wan = Apache-2.0, diffusers/transformers/accelerate = Apache-2.0, torch = BSD,
imageio-ffmpeg = BSD. Für den mp4-Export zieht `imageio-ffmpeg` ffmpeg-Bibliotheken
(LGPL, dynamisch gelinkt, unverändert) – dieselbe dokumentierte weak-copyleft-
Ausnahme wie PyAV/faster-whisper im Hauptprojekt. Keine GPL-Komponenten.
