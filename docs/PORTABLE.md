# AI_Framework_Thomas — Portable (Variante 2)

## Übersicht

Die Portable-Variante enthält alles in einem einzigen Ordner:

```
AI_Framework_Thomas_Portable_YYYYMMDD\
├── app\              ← Anwendungscode
├── python\           ← Embedded Python 3.12 (kein Install nötig)
├── ollama\
│   ├── ollama.exe    ← Ollama Binary
│   └── models\       ← alle lokal gepullten LLM-Modelle (mind. ministral-3:3b)
├── start.bat         ← Starten
└── README.md
```

**Vorteil:** Auf jeden Windows-PC kopieren und direkt starten — keine Installation, kein Admin-Recht.

---

## Portable Bundle erstellen

### Voraussetzung
`install.bat` muss auf dem **Quellrechner** erfolgreich durchgelaufen sein.

### Bundle erstellen

```
Doppelklick auf: make_portable.bat
```

Das Skript:
1. Kopiert alle App-Dateien
2. Lädt Python 3.12 Embeddable Package herunter (~25 MB)
3. Installiert alle Python-Pakete ins Bundle
4. Kopiert `ollama.exe` aus der lokalen Installation
5. Kopiert die LLM-Modell-Dateien (~3–6 GB)
6. Erstellt `start.bat` für das Bundle

> **Dauer:** 5–30 Minuten je nach Datenmenge und Speichergeschwindigkeit

---

## Bundle verwenden

### Auf Zielrechner kopieren

Den kompletten Ordner `AI_Framework_Thomas_Portable_YYYYMMDD` kopieren:
- USB-Stick (USB 3.0 empfohlen wegen Dateigröße)
- Netzlaufwerk
- Externe SSD

### Starten

```
Doppelklick: AI_Framework_Thomas_Portable_YYYYMMDD\start.bat
```

Browser öffnet sich automatisch auf `http://localhost:8780`

---

## Technische Details

| Komponente | Methode |
|---|---|
| Python | Embedded Package (keine Systeminstallation) |
| Ollama | Einzelne `.exe` Datei |
| Modelle | Lokale Kopie im `ollama\models\` Unterordner |
| Daten | SQLite DB in `app\data\` |

### Umgebungsvariablen (gesetzt von start.bat)

```
OLLAMA_MODELS = <bundle>\ollama\models
OLLAMA_HOST   = 127.0.0.1:11434
```

---

## Bundle-Größe

| Komponente | Größe (ca.) |
|---|---|
| App + Python + Pakete | ~500 MB |
| ollama.exe | ~200 MB |
| ministral-3:3b | ~2 GB |
| nomic-embed-text | ~0,3 GB |
| je weiteres zugewiesenes Modell | +1–5 GB |
| **Gesamt (Basis)** | **~3 GB** |

> `make_portable.ps1` bündelt **alle** lokal gepullten Ollama-Modelle. Vor dem
> Bündeln nicht benötigte Modelle entfernen hält das Bundle schlank: `ollama rm <modell>`.

---

## Fehlerbehebung

| Problem | Lösung |
|---|---|
| `ollama.exe` startet nicht | Evtl. fehlen Visual C++ Redistributables — `winget install Microsoft.VCRedist.2015+.x64` |
| Modelle fehlen | `ollama\ollama.exe pull ministral-3:3b` im Bundle-Verzeichnis ausführen |
| Port belegt | In `app\config.json` Port ändern und in `start.bat` anpassen |
