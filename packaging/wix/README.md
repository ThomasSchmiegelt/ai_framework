# MSI-Paket (WiX Toolset v3)

Erzeugt ein echtes MSI für die ACMP-Verteilung. Es paketiert — wie das
`update.bat`-Verfahren — **ausschließlich den Programmcode**. Ollama, Modelle,
Python/venv und die Nutzerdaten unter `data\` bleiben außen vor und werden über
eigene Pakete verteilt (siehe [`../../docs/ACMP.md`](../../docs/ACMP.md)).

> ⚠️ **Noch nicht auf einem Windows-Rechner gebaut.** Diese Dateien sind auf
> einem Linux-System entstanden; WiX läuft ausschließlich unter Windows. Der
> erste Build muss dort erfolgen und wird erfahrungsgemäß ein bis zwei
> Nachbesserungen brauchen (typisch: ICE-Validierungswarnungen, Pfad- oder
> Verknüpfungsdetails). Das `update.bat`-Verfahren ist der bereits nutzbare Weg.

## Voraussetzungen

* Windows
* [WiX Toolset v3.11 oder v3.14](https://wixtoolset.org/) (`candle.exe`,
  `light.exe`, `heat.exe`)
* PowerShell

## Bauen

```powershell
cd packaging\wix
.\build.ps1                       # Version aus ..\..\VERSION
.\build.ps1 -Version 1.5.0
.\build.ps1 -WixBin "C:\Program Files (x86)\WiX Toolset v3.14\bin"
```

Ablauf des Skripts:

1. `make_acmp.ps1` erzeugt den Paketordner `payload\` (nur Programmcode,
   mit eingebauter Prüfung gegen Ollama/venv/data).
2. `heat.exe` erntet daraus die Dateiliste → `AppFiles.wxs`
   (wird bei jedem Build neu erzeugt, nicht von Hand pflegen).
3. `candle.exe` kompiliert `Product.wxs` + `AppFiles.wxs`.
4. `light.exe` linkt `out\AI_Framework_Thomas_<Version>.msi`.

## Verteilung über ACMP

```
Installation:    msiexec /i AI_Framework_Thomas_1.5.0.msi /qn /norestart /l*v "%TEMP%\aifw_msi.log"
Zielordner:      msiexec /i ... INSTALLFOLDER="D:\Apps\AI_Framework" /qn
Deinstallation:  msiexec /x AI_Framework_Thomas_1.5.0.msi /qn /norestart
```

Erfolg = Exit-Code `0` (bzw. `3010` = Neustart erwünscht).

**Erkennungsregel:** `HKLM\SOFTWARE\AI_Framework_Thomas\Version` — derselbe
Schlüssel, den auch `update.bat` schreibt. Dadurch funktioniert die
Inventarisierung unabhängig davon, über welchen der beiden Wege verteilt wurde.

## Erstbau auf Windows — Schritt für Schritt

### 1. WiX installieren

WiX **v3** (nicht v4/v5 — die Syntax dieses Projekts ist v3):

```powershell
winget install WiXToolset.WiXToolset
# oder Download: https://github.com/wixtoolset/wix3/releases
```

Prüfen, dass die Werkzeuge gefunden werden:

```powershell
where.exe candle.exe light.exe heat.exe
```

Werden sie nicht gefunden, beim Bauen den Pfad mitgeben:

```powershell
.\build.ps1 -WixBin "C:\Program Files (x86)\WiX Toolset v3.14\bin"
```

### 2. Bauen

```powershell
cd packaging\wix
.\build.ps1 -Version 1.4.1
```

Erfolg: `out\AI_Framework_Thomas_1.4.1.msi`.

### 3. Testinstallation (in einer VM oder mit Snapshot!)

```powershell
msiexec /i out\AI_Framework_Thomas_1.4.1.msi /qn /norestart /l*v "%TEMP%\aifw_msi.log"
echo $LASTEXITCODE          # 0 = ok, 3010 = Neustart erwuenscht
```

Prüfliste:

| Prüfpunkt | Erwartung |
|---|---|
| Zielordner | `C:\Program Files\AI_Framework` gefüllt |
| `ollama`, `venv`, `python`, `data` | **nicht vorhanden** |
| Registry | `HKLM\SOFTWARE\AI_Framework_Thomas\Version` = `1.4.1` |
| Startmenü | Verknüpfung „AI Framework starten" |
| Deinstallation | `msiexec /x … /qn` entfernt die Programmdateien |

Alternativer Zielordner:

```powershell
msiexec /i AI_Framework_Thomas_1.4.1.msi INSTALLFOLDER="D:\Apps\AI_Framework" /qn
```

### 4. Zu erwartende Stolperstellen

Diese Punkte sind **nicht** verifiziert — hier ist am ehesten mit Nacharbeit zu rechnen:

| Meldung / Symptom | Ursache und Abhilfe |
|---|---|
| `ICE60: The file ... is not a Font, and its version is not a companion file reference` | Harmlos bei versionslosen Dateien. Ist in `build.ps1` bereits per `-sice:ICE60` unterdrückt; treten weitere ICE-Warnungen auf, analog ergänzen. |
| `ICE38/ICE64: Component installs to user profile` | Betrifft die Startmenü-Verknüpfung. Falls störend: die Komponente `StartMenuShortcut` in `Product.wxs` entfernen — sie ist nicht funktionswesentlich. |
| `light.exe : error LGHT0204` (ICE-Validierung) | Bei reinen Warnungen `-sval` ergänzen, um die Validierung zu überspringen. |
| `heat.exe` erzeugt leere Dateiliste | `payload\` war leer → `make_acmp.ps1` ist vorher gescheitert. Dessen Ausgabe prüfen. |
| `candle.exe : error CNDL0150 unresolved reference to variable PayloadDir` | `-dPayloadDir` fehlt. Ist in `build.ps1` gesetzt; bei manuellem Aufruf mitgeben. |
| Version wird abgelehnt (`Invalid product version`) | MSI verlangt rein numerisch `x.y.z`. `build.ps1` schneidet Suffixe wie `-beta` automatisch ab — Meldung im Log beachten. |
| Upgrade installiert nebeneinander statt zu ersetzen | Die `UpgradeCode` in `Product.wxs` darf sich **nie** ändern. Sie ist fest auf `DB8422D3-0898-452A-ADEE-29E3BED5EFEA` gesetzt — nicht anfassen. |
| Dateien werden bei Upgrade nicht ersetzt | Windows-Installer ersetzt versionslose Dateien nur bei neuerem Datum. Notfalls in `Product.wxs` `<RemoveFile>`/`REINSTALLMODE=amus` verwenden. |

### 5. MSI oder update.bat?

Beide Wege schreiben **denselben** Registry-Schlüssel, die Erkennungsregel in
ACMP funktioniert also unabhängig davon.

| | `update.bat` | MSI |
|---|---|---|
| Status | einsatzbereit | Erstbau steht aus |
| Erstinstallation | nein (setzt Installation voraus) | ja |
| Deinstallation / Repair | nein | ja |
| ACMP-Inventarisierung | über Registry/Datei | zusätzlich über Programme-Liste |
| Nutzerdaten | bleiben garantiert unberührt | bleiben unberührt (nicht im Paket) |

Empfehlung: MSI für die **Erstverteilung**, `update.bat` für die **laufenden
Programm-Updates** — letzteres ist schneller, kleiner und legt automatisch ein
Rollback-Backup an.

## Was das MSI bewusst NICHT tut

* Es installiert **kein** Ollama und keine Modelle.
* Es legt **keine** `config.json` an — die entsteht beim ersten Start bzw.
  stammt aus dem Installer. Ein Update überschreibt sie damit nie.
* Beim Deinstallieren bleiben `data\` und `config.json` erhalten, da sie nicht
  zum Paket gehören. Für eine restlose Entfernung muss der Ordner manuell oder
  per Skript gelöscht werden.
