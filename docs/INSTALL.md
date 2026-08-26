# AI_Framework_Thomas — Master-Installation (Variante 1)

## Voraussetzungen

| Anforderung | Mindest | Empfohlen |
|---|---|---|
| Betriebssystem | Windows 10 (64-bit) | Windows 11 |
| RAM | 8 GB | 16 GB |
| Speicherplatz | 10 GB frei | 20 GB frei |
| GPU | — | NVIDIA mit 6+ GB VRAM |
| Internetverbindung | Ja (für Installation) | — |

---

## Installation

### Schritt 1: Download

Das AI_Framework_Thomas Verzeichnis auf den Zielrechner kopieren (z.B. nach `C:\AI_Framework_Thomas`).

### Schritt 2: Installer starten

```
Doppelklick auf: install.bat
```

> Der Installer fragt nach Administrator-Rechten — das ist für die Ollama-Installation notwendig.

### Was wird installiert?

1. **Python 3.12** (falls nicht vorhanden, via `winget`)
2. **Ollama** für Windows (falls nicht vorhanden)
3. Python-Abhängigkeiten in einer virtuellen Umgebung (`venv\`)
4. **KI-Modelle** (Download mehrere GB):
   - `granite4.2:3b` (Standardmodell; IBM, Apache-2.0, gutes Tool-Use/JSON, 128K Kontext)
   - `ministral-3:3b` (kompaktes Allround-/Vision-Modell)
   - `qwen3.5:4b` (stärkeres kompaktes Chat-Modell)
   - `nomic-embed-text` (RAG-Embeddings)

   Weitere Modelle bei Bedarf laden (`ollama pull <modell>`) und im
   **Profil → 🧠 Modelle** den Rollen Allgemein / Programmieren·Mathe / Wissenschaftlich /
   **Medizin** zuweisen (die Rolle „Programmieren / Mathe" gilt für Code-IDE und Mathe-Tab). Für den 🩺 Medizin-Tab empfiehlt sich ein medizinisches Modell:
   `ollama pull medgemma:4b` (MedGemma-4B, ~2,5 GB; optional — in der Portable-Variante bereits mitgebündelt).
5. Desktop-Verknüpfung `AI_Framework_Thomas`

---

## Starten

**Methode 1:** Doppelklick auf `start.bat`  
**Methode 2:** Desktop-Verknüpfung `AI_Framework_Thomas`  
**Methode 3:** Im Browser: `http://localhost:8780`

---

## Modelle anpassen

`config.json` legt nur Standardmodell und Sortier-Reihenfolge fest — die
Auswahllisten im Profil zeigen **alle** in Ollama installierten Modelle:

```json
{
  "allowed_models": [],
  "default_model": "granite4.2:3b"
}
```

Weitere Modelle finden: [ollama.com/library](https://ollama.com/library)  
Manuell laden: `ollama pull <modellname>` — danach im **Profil → 🧠 Modelle** wählbar.

---

## Deinstallation

1. `venv\` Ordner löschen
2. Ollama deinstallieren: `winget uninstall Ollama.Ollama`
3. Modelle entfernen: `%USERPROFILE%\.ollama\models\` löschen
4. Gesamten AI_Framework_Thomas Ordner löschen

---

## Fehlerbehebung

| Problem | Lösung |
|---|---|
| `ollama: command not found` | Neues Terminal öffnen oder Neustart |
| Modell-Download schlägt fehl | `ollama pull ministral-3:3b` manuell in CMD |
| Port 8780 belegt | `config.json` → `"port": 8766` und `start.bat` anpassen |
| Seite lädt nicht | Prüfen: läuft `ollama.exe` im Task-Manager? |
| Falscher Modell-Tag | Auf [ollama.com/library](https://ollama.com/library) korrekte Tags prüfen |
