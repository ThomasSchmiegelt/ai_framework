# AI_Framework_Thomas — Server (Variante 3)

## Übersicht

Die Server-Variante ermöglicht **gleichzeitigen Zugriff mehrerer Nutzer** im Netzwerk.

**Typischer Einsatz:**
- Firmen-Intranet
- Schulungsumgebungen
- Shared Workstation im Büro

---

## Server-Bundle erstellen

### Voraussetzung
`install.bat` muss vorher auf dem Server-Rechner ausgeführt worden sein.

### Bundle erstellen

```
Doppelklick auf: make_server.bat
```

Das Skript fragt interaktiv:

| Eingabe | Standard | Beschreibung |
|---|---|---|
| Port | 8780 | TCP-Port für die Webanwendung |
| Worker | 1 | Anzahl paralleler Prozesse. Standard 1 wegen VRAM-Schutz (pro Prozess); nur bei ≥ 12 GB VRAM erhöhen |
| Bind-Adresse | 0.0.0.0 | Alle Netzwerk-Interfaces (oder spezifische IP) |
| Weitere Modelle | — | Kommagetrennt, z.B. `llama3:8b,phi3:mini` |
| Basic-Auth | N | Optionaler Passwortschutz |

---

## Starten

### Manuell

```
Doppelklick: AI_Framework_Thomas_Server\start_server.bat
```

### Als Windows-Dienst (automatischer Start)

```
Rechtsklick → Als Administrator ausführen:
AI_Framework_Thomas_Server\install_service.bat
```

Dienst verwalten:
```batch
sc start  AI_Framework_Thomas_Server
sc stop   AI_Framework_Thomas_Server
sc delete AI_Framework_Thomas_Server
```

---

## Firewall konfigurieren

Damit Nutzer im Netzwerk zugreifen können:

```
Rechtsklick → Als Administrator ausführen:
AI_Framework_Thomas_Server\open_firewall.bat
```

Oder manuell in Windows Defender Firewall:
- Eingehende Regel → Neu → Port → TCP → Port 8780 → Zulassen

---

## Nutzer-Zugriff

Nutzer öffnen im Browser:
```
http://<Server-IP-Adresse>:8780
```

Server-IP ermitteln:
```batch
ipconfig | find "IPv4"
```

---

## Kapazität und Worker

Jeder **Worker-Prozess** kann mehrere gleichzeitige HTTP-Verbindungen per asyncio verarbeiten.  
Die eigentliche Kapazitätsbeschränkung liegt beim **LLM-Inference** (Ollama):

| GPU VRAM | Empfohlene Worker | Gleichzeitige LLM-Anfragen |
|---|---|---|
| 6 GB | 1 | 1 (sequenziell) |
| 12 GB | 4 | 1–2 |
| 24 GB | 4–8 | 2–4 |
| Nur CPU | 2 | 1 |

> Ollama verarbeitet LLM-Anfragen standardmäßig sequenziell. Für parallele Verarbeitung:
> ```
> set OLLAMA_NUM_PARALLEL=2
> ```

> **VRAM-Hinweis bei mehreren Workern:** Der „nur ein Modell gleichzeitig"-Schutz
> wirkt **pro Prozess**. Bei knappem VRAM (≈ 6 GB) und mehreren Workern können sich
> die Worker beim Modellwechsel gegenseitig in die Quere kommen (zwei Modelle
> gleichzeitig geladen). Dann **`workers = 1`** setzen oder ausreichend VRAM
> vorsehen (≥ 12 GB).
>
> Der **🩺 Medizin-Tab** wechselt pro Anfrage mehrfach zwischen zwei Modellen
> (Standard- ↔ Medizin-Modell). Das ist auf 6 GB mit `workers = 1` korrekt, kostet
> aber je Stufe einen Lade-/Entlade-Vorgang — im Mehrbenutzerbetrieb entsprechend
> mehr Wartezeit. Mehr VRAM (≥ 12 GB) glättet das spürbar.

---

## Weitere Modelle hinzufügen

Server-Variante ist nicht auf die Standard-Modelle begrenzt.

1. Modell laden: `ollama pull <modellname>` — es erscheint danach automatisch in den
   Modell-Auswahllisten (Profil), da `/api/models` alle installierten Modelle liefert.
2. Optional `config.json` bearbeiten (nur Sortier-Reihenfolge / Standardmodell):
```json
{
  "allowed_models": ["granite4.2:3b", "llama3:8b"],
  "default_model": "granite4.2:3b"
}
```
3. Server neu starten

---

## Konfigurationsdatei

`AI_Framework_Thomas_Server\config.json`:

```json
{
  "allowed_models": [],
  "default_model":  "granite4.2:3b",
  "ollama_base":    "http://localhost:11434",
  "port":           8780,
  "host":           "0.0.0.0",
  "workers":        1,
  "auth": {
    "user": "admin",
    "pass": "geheim"
  }
}
```

> `auth` nur vorhanden wenn Basic-Auth beim Erstellen aktiviert wurde.

---

## Logs

| Datei | Inhalt |
|---|---|
| `logs\service.log` | Normale Ausgabe des Dienstes |
| `logs\error.log` | Fehler und Warnungen |

---

## Hardware-Empfehlungen (Server)

| Komponente | Minimum | Empfehlung |
|---|---|---|
| CPU | 4 Kerne | 8+ Kerne |
| RAM | 16 GB | 32 GB |
| GPU | optional | NVIDIA RTX 4060+ (8 GB VRAM) |
| Speicher | 50 GB SSD | 100 GB NVMe |
| Netzwerk | 100 Mbit | Gigabit |

---

## Fehlerbehebung

| Problem | Lösung |
|---|---|
| Nutzer kommen nicht rein | Firewall-Regel prüfen (`open_firewall.bat`) |
| Dienst startet nicht | `logs\error.log` prüfen; NSSM-Dienst per `sc query AI_Framework_Thomas_Server` |
| `workers` > 1 funktioniert nicht | Nur mit `uvicorn >= 0.20.0`; alternativ auf 1 Worker setzen |
| Ollama antwortet zu langsam | `OLLAMA_NUM_PARALLEL=1` in Dienst-Umgebungsvariablen prüfen |
