# AI_Framework_Thomas — Portable (Variante 2)

## Übersicht

Die Portable-Variante enthält alles in einem einzigen Ordner:

```
AI_Framework_Thomas_Portable_YYYYMMDD\
├── app\              ← Anwendungscode
├── python\           ← Embedded Python 3.12 (kein Install nötig)
├── ollama\
│   ├── ollama.exe    ← Ollama Binary
│   └── models\       ← nur die Whitelist-Modelle (ministral-3:3b, qwen3.5:4b, medgemma:4b, nomic-embed-text)
├── start.bat         ← Starten
└── README.md
```

**Vorteil:** Auf jeden Windows-PC kopieren und direkt starten — keine Installation, kein Admin-Recht.

---

## Zwei Bundle-Varianten

| Variante | Skript | Bündelt Ollama + Modelle? | Größe | Voraussetzung am Ziel |
|---|---|---|---|---|
| **A — Voll-Bundle** (Standard) | `make_portable.bat` | **Ja** (eigene `ollama.exe` + Modelle, eigener Port 11500) | ~5 GB | nichts |
| **B — System-Ollama** | `make_portable_systemollama.bat` | **Nein** (nutzt installiertes Ollama, Port 11434) | ~0,5 GB | Ollama installiert + Modelle gezogen |

**Variante B** ist gedacht für Rechner, auf denen Ollama **bereits installiert** ist:
das Bundle enthält dann nur App + Embedded-Python + Pakete (kein `ollama\`-Ordner,
keine Modell-Blobs) und spricht das vorhandene System-Ollama auf dem Standard-Port
**11434** an. `start.bat` startet das installierte Ollama bei Bedarf über den PATH.
Vor dem ersten Start die Modelle ziehen:

```
ollama pull ministral-3:3b
ollama pull nomic-embed-text
```

Intern ist Variante B derselbe Skriptlauf mit dem Schalter `-UseSystemOllama`
(`make_portable.ps1 -UseSystemOllama`); die Schritte „Ollama-Binary kopieren",
„Modelle kopieren" und „config.json-Port umschreiben" entfallen.

---

## Portable Bundle erstellen

### Voraussetzung
`install.bat` muss auf dem **Quellrechner** erfolgreich durchgelaufen sein.

### Bundle erstellen

```
Doppelklick auf: make_portable.bat
```

**Ausgabe-Verzeichnis wählen (optional):** Standardmäßig wird das Bundle im
übergeordneten Ordner des Projekts angelegt. Mit `-OutDir` lässt sich das Ziel
frei wählen:

```
make_portable.bat -OutDir D:\Portable
```

Der **Start**-Pfad des fertigen Bundles ist ohnehin frei: `start.bat` arbeitet
durchgängig mit `%~dp0` (eigener Ordner), das Bundle läuft also aus jedem
Verzeichnis / von jedem Laufwerk.

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

## Bundle aktualisieren (`update.bat`)

`update.bat` tauscht **nur die Systemdateien** (Programmcode) eines bestehenden
Bundles aus und lässt **alle Nutzerdaten und die Konfiguration unberührt**.

**Unverändert bleiben:** `app\data\` (Konversationen, Agenten, Pläne, RAG-DB, Profil,
Branding, `mail.json`, `mail_rules.json`), `app\config.json` (eigener Port 11500),
`python\`, `ollama\` inkl. Modelle.

**Ersetzt werden:** `main.py`, `db.py`, `requirements.txt`, `static\`, `tools\`,
`docs\`, `scripts\`, `samples\`, `bilder\`, `*.md`, `*.ps1`, `*.bat`, `LICENSE`.

### Ablauf
1. Die **neue Version** entpacken/bereitlegen (der Ordner, in dem `update.bat` liegt
   = Quelle).
2. `update.bat` mit dem Pfad der bestehenden Installation als Ziel aufrufen:

   ```
   update.bat "D:\AI_Framework_Thomas_Portable_YYYYMMDD"
   ```

   Ohne Argument wird der Zielpfad abgefragt. Das `app\`-Unterverzeichnis wird auf
   beiden Seiten automatisch erkannt (Portable-Bundle **und** flache Entwickler-Installation).
3. Vor dem Überschreiben legt das Skript eine Sicherung des alten Codes unter
   `app\_update_backup\` an (Rollback möglich).
4. Danach bietet es optional an, die `requirements.txt`-Pakete in das gebündelte
   `python\` (bzw. `venv\`) zu installieren — wichtig, falls die neue Version neue
   Abhängigkeiten mitbringt.

> Nach dem Update die App neu starten (`start.bat`) und den Browser mit **Strg+F5**
> neu laden, damit das aktualisierte Frontend (CSS/JS) geladen wird.

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
OLLAMA_HOST   = 127.0.0.1:11500
```

> **Eigener Ollama-Port 11500:** Das Bundle startet sein Ollama bewusst auf Port
> **11500** (nicht dem Standard 11434) und setzt `config.json` → `ollama_base`
> entsprechend. So kollidiert es nie mit einem bereits **system-installierten**
> Ollama auf 11434 und nutzt garantiert seine **eigenen** gebündelten Modelle
> (inkl. `nomic-embed-text` für RAG). `start.bat` startet das Bundle-Ollama nur,
> wenn der Port 11500 noch nicht antwortet, und wartet dann auf dessen
> Bereitschaft, bevor die App hochfährt.

---

## Bundle-Größe

| Komponente | Größe (ca.) |
|---|---|
| App + Python + Pakete | ~500 MB |
| ollama.exe | ~200 MB |
| ministral-3:3b | ~2 GB |
| qwen3.5:4b | ~2,5 GB |
| medgemma:4b (🩺 Medizin) | ~2,5 GB |
| nomic-embed-text | ~0,3 GB |
| **Gesamt (Basis)** | **~7,5 GB** |

> `make_portable.ps1` bündelt **gezielt nur** die Whitelist `$BUNDLE_MODELS`
> (`ministral-3:3b`, `qwen3.5:4b`, `medgemma:4b`, `nomic-embed-text:latest`) — nicht mehr das
> komplette lokale Modellverzeichnis. Fehlt eines lokal, wird es vor dem Kopieren
> automatisch nachgezogen. Weitere Modelle in `$BUNDLE_MODELS` ergänzen, falls nötig.

---

## Fehlerbehebung

| Problem | Lösung |
|---|---|
| `ollama.exe` startet nicht | Evtl. fehlen Visual C++ Redistributables — `winget install Microsoft.VCRedist.2015+.x64` |
| RAG funktioniert nicht / „Modell nicht gefunden" | `nomic-embed-text` fehlt im Bundle. Im Bundle-Ordner: `set OLLAMA_HOST=127.0.0.1:11500` & `set OLLAMA_MODELS=%CD%\ollama\models`, dann `ollama\ollama.exe pull nomic-embed-text`. Beim Neu-Erstellen zieht `make_portable.ps1` das Modell jetzt automatisch nach. |
| Modelle fehlen | Wie oben, aber `ollama\ollama.exe pull ministral-3:3b` |
| App-Port 8780 belegt | In `app\config.json` `port` ändern und in `start.bat` anpassen |
| Ollama-Port 11500 belegt | In `app\config.json` `ollama_base` und in `start.bat` (`OLLAMA_HOST`) gemeinsam anpassen |
