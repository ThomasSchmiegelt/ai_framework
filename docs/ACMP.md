# Softwareverteilung mit Aagon ACMP

Anleitung, um AI_Framework_Thomas über die **Aagon Client Management Platform**
zu installieren und aktuell zu halten — ohne bei jedem Update Ollama und die
Modelle mit auszurollen.

---

## 1. Grundidee: drei getrennte Pakete

Das Programm ist klein und ändert sich oft, Ollama und die Modelle sind groß und
ändern sich selten. Beides in einem Paket auszuliefern würde bei jedem
Programm-Update mehrere Gigabyte über das Netz schieben. Deshalb:

| Paket | Inhalt | Größe | Aktualisierung |
|---|---|---|---|
| **A — Laufzeit** | Python + venv-Abhängigkeiten | ~300 MB | selten (nur bei neuen Abhängigkeiten) |
| **B — Ollama + Modelle** | Ollama-Binary, `.gguf`-Modelle | mehrere GB | sehr selten |
| **C — Programm** | Code aus `make_acmp.ps1` | wenige MB | häufig |

Nur **Paket C** läuft regelmäßig. `update.bat` fasst `ollama\`, `python\`,
`venv\`, `data\` und `config.json` **nie** an — die Trennung ist im Updater
erzwungen, nicht nur Konvention.

---

## 2. Paket C bauen

```powershell
.\make_acmp.ps1 -Zip                 # Version aus der Datei VERSION
.\make_acmp.ps1 -Version 1.5.0 -Zip  # Version setzen und bauen
```

Das Skript arbeitet mit einer **Positivliste**: nur ausdrücklich genannte
Dateien und Ordner kommen ins Paket. Anschließend prüft es, dass kein
`ollama`, `venv`, `python`, `data`, `certs` oder `models` enthalten ist, und
bricht sonst mit Fehler ab. `config.json` wird bewusst entfernt — sie gehört
der Installation, nicht dem Paket.

Ergebnis: `_acmp\AI_Framework_<Version>\` (+ `.zip`).

---

## 3. ACMP-Befehlszeilen

### Installation / Update (Paket C)

```
update.bat "C:\Programme\AI_Framework" /S /LOG:"%TEMP%\aifw_update.log"
```

| Schalter | Wirkung |
|---|---|
| `/S` | Silent — keine Rückfragen, kein `pause`. **Für ACMP zwingend.** |
| `/LOG:<datei>` | Logdatei (Standard: `<Ziel>\_update.log`) |
| `/NOPIP` | Python-Pakete nicht aktualisieren |
| `/PIP` | Python-Pakete aktualisieren (bei `/S` ohnehin Standard) |

Ohne `/S` bleibt das Skript vollständig interaktiv wie bisher — die manuelle
Nutzung ändert sich nicht.

### Exit-Codes (Erfolgsprüfung)

| Code | Bedeutung |
|---|---|
| `0` | Update erfolgreich |
| `1` | allgemeiner Fehler / Abbruch |
| `2` | Quelle ungültig (keine `main.py` im Paketordner) |
| `3` | Ziel ungültig (dort keine Installation) |
| `4` | Fehler beim Kopieren der Systemdateien |
| `5` | Quelle und Ziel identisch |

In ACMP als Erfolg **nur `0`** werten. Ein fehlgeschlagenes `pip install`
gilt bewusst **nicht** als Fehler (Code bleibt `0`), da die Anwendung mit den
vorhandenen Paketen weiterläuft; der Hinweis steht im Log.

---

## 4. Versionserkennung

Nach jedem Update schreibt `update.bat`:

* **Datei** `<Installation>\VERSION` — eine Zeile, z. B. `1.5.0`
* **Registry** `HKLM\SOFTWARE\AI_Framework_Thomas`
  * `Version` (REG_SZ) — installierte Version
  * `InstallPath` (REG_SZ) — Installationsordner
  * `LastUpdate` (REG_SZ) — Datum des letzten Updates

Als ACMP-Erkennungsregel eignet sich der Registry-Wert `Version`; die
`VERSION`-Datei ist der Rückfall, wenn das Update ohne Adminrechte lief.

> **Wichtig — behobener Fehler:** Früher stand die Versionsnummer nur in
> `config.json`. Da der Updater `config.json` bewusst **nicht** austauscht
> (sie enthält Nutzerkonfiguration wie den Ollama-Port), blieb die gemeldete
> Version nach einem Update für immer auf dem alten Stand. Seit dieser Fassung
> ist die Datei `VERSION` die Quelle (`main.py` liest sie, `config.json` dient
> nur noch als Rückfall) — und sie wird beim Update mitgetauscht.

---

## 5. Ollama getrennt ausrollen (Paket B)

Ollama wird **nicht** vom Programmpaket berührt. Empfohlenes Vorgehen:

1. Ollama einmalig per eigenem ACMP-Paket installieren (MSI von ollama.com
   oder die portable Variante nach `<Installation>\ollama\`).
2. Modelle einmalig verteilen — entweder per `ollama pull` in einem
   Nachinstallations-Skript oder durch Kopieren des Modellordners
   (`%USERPROFILE%\.ollama\models` bzw. `<Installation>\ollama\models`).
3. Erkennungsregel für dieses Paket: Existenz von `ollama.exe` bzw. die
   Ollama-Version.

Beim Programm-Update bleibt beides unangetastet — es wird weder kopiert noch
gelöscht noch überschrieben.

---

## 6. Empfohlener Ablauf im ACMP-Client-Command

```
1. Prozess beenden:   taskkill /F /IM python.exe   (nur falls die App läuft)
2. Paket C entpacken nach %TEMP%\aifw_pkg
3. %TEMP%\aifw_pkg\update.bat "C:\Programme\AI_Framework" /S /LOG:"%TEMP%\aifw_update.log"
4. Exit-Code prüfen (0 = ok)
5. Log einsammeln: %TEMP%\aifw_update.log
6. Dienst/Verknüpfung wieder starten
```

Ein Rollback ist ohne Neupaketierung möglich: `update.bat` legt die vorherige
Fassung unter `<Installation>\_update_backup\` ab.

---

## 7. MSI-Variante

Ein WiX-Projekt für ein echtes MSI liegt unter `packaging/wix/`. Es paketiert
**nur den Programmcode** (dieselbe Trennung wie oben) und muss auf einem
Windows-Rechner mit dem WiX Toolset gebaut werden — siehe
`packaging/wix/README.md`.
