# 🎵 Musik-Generator (algorithmisch)

Erzeugt aus einer **Stil-/Stimmungsbeschreibung** ein kleines Musikstück als
**WAV-Datei** – Melodie, Akkorde, Bass und (je nach Stil) Schlagzeug, aus Musiktheorie
zusammengebaut. Kinderfreundlich zum Rumprobieren.

- **Keine Installation, keine Abhängigkeit, kein GPU** – nur **Python 3** (reine
  Standardbibliothek).
- **MIT-Lizenz** – alles selbst geschrieben, keine fremden Modelle/Gewichte.
- Läuft auf **Windows und Linux/macOS**.

> Kein neuronales „Text-zu-Audio"-Modell (das gäbe es mit MusicGen o. Ä., dessen
> Modellgewichte aber *nicht* MIT-konform sind). Dieser Generator ist dafür sofort,
> offline, transparent und beliebig anpassbar.

## Starten

**Windows**
```bat
cd z-music
musik.bat "fröhliche schnelle Abenteuermelodie"
```

**Linux/macOS**
```bash
cd z-music
chmod +x musik.sh
./musik.sh "fröhliche schnelle Abenteuermelodie"
```

Die fertige Datei liegt danach in **`z-music/outputs/`** – einfach doppelklicken/abspielen.

## Was man schreiben kann

Der Generator erkennt Stimmungs-Wörter in der Beschreibung:

| Wörter (Beispiele) | Ergebnis |
|---|---|
| fröhlich, happy, lustig, abenteuer, sonnig | Dur, gut gelaunt, mit Beat |
| traurig, melancholisch, trauer | Moll, langsam, sanft |
| spannend, episch, action, kampf, boss | Moll, treibend, mit Beat |
| chill, entspannt, lofi, gemütlich | ruhig, weich |
| gruselig, spooky, halloween, unheimlich | düster |
| 8bit, chiptune, retro, gameboy, spiel | Chiptune (Standard) |

Zusätzlich: **schnell/langsam** verschiebt das Tempo.

Beispiele:
```
musik.bat "traurige langsame Melodie in a-moll"
musik.bat "8bit"
musik.bat "spannende Bossmusik" --seed 42
```

## Optionen

| Option | Bedeutung | Standard |
|---|---|---|
| `--style` | Stil direkt: `happy`/`sad`/`epic`/`chill`/`spooky`/`chip` | aus Text |
| `--key` | Grundton: `C D E F G A H/B` | aus Text / `C` |
| `--tempo` | Tempo in BPM | je Stil |
| `--bars` | Länge in Takten (4–64) | `16` |
| `--seed` | fester Zufalls-Seed (gleiche Zahl = gleiches Stück) | zufällig |
| `--out` | Ausgabedateiname (.wav) | Zeitstempel |
| `--outdir` | Ausgabeordner | `outputs` |

## Ideen zum Weiterbauen

- Weitere Stile/Tonleitern (`SCALES`, `PROGRESSIONS`, `STYLE_PRESETS` in
  `generate_music.py`).
- Zusätzliche Instrumente/Klangfarben (`_wave_sample`, ADSR-Werte).
- Export als **MIDI** (für Musikprogramme) – ließe sich ebenfalls rein mit der
  Standardbibliothek ergänzen.
