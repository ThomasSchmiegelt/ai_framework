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

## Was das MSI bewusst NICHT tut

* Es installiert **kein** Ollama und keine Modelle.
* Es legt **keine** `config.json` an — die entsteht beim ersten Start bzw.
  stammt aus dem Installer. Ein Update überschreibt sie damit nie.
* Beim Deinstallieren bleiben `data\` und `config.json` erhalten, da sie nicht
  zum Paket gehören. Für eine restlose Entfernung muss der Ordner manuell oder
  per Skript gelöscht werden.
