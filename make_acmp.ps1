<#
    AI_Framework_Thomas  -  ACMP-Updatepaket bauen
    ==============================================

    Erzeugt ein Paket, das AUSSCHLIESSLICH den Programmcode enthaelt -
    kein Ollama, keine Modelle, kein venv/python, keine Nutzerdaten.
    Genau das ist der Sinn: Ollama und die Modelle sind gross und aendern
    sich selten, der Programmcode oft. Beides gehoert deshalb in getrennte
    ACMP-Pakete; dieses hier ist das haeufig ausgerollte "Programm"-Paket.

    Aufruf:
        .\make_acmp.ps1 [-Out <Zielordner>] [-Zip] [-Version <x.y.z>]

        -Out      Zielordner (Standard: .\_acmp\AI_Framework_<Version>)
        -Zip      zusaetzlich als .zip packen
        -Version  ueberschreibt die Versionsnummer und schreibt sie in VERSION

    Das erzeugte Paket wird in ACMP so ausgerollt:
        update.bat "C:\Programme\AI_Framework" /S /LOG:"%TEMP%\aifw.log"
    Erfolgspruefung ueber den Exit-Code (0 = ok), Versionserkennung ueber
    HKLM\SOFTWARE\AI_Framework_Thomas\Version bzw. die Datei VERSION.
    Details: docs\ACMP.md
#>

[CmdletBinding()]
param(
    [string]$Out,
    [switch]$Zip,
    [string]$Version
)

$ErrorActionPreference = 'Stop'
$src = $PSScriptRoot

# --- Version bestimmen / setzen ---------------------------------------------
$verFile = Join-Path $src 'VERSION'
if ($Version) {
    Set-Content -Path $verFile -Value $Version -Encoding ASCII -NoNewline
    Add-Content -Path $verFile -Value "`n" -NoNewline
    $ver = $Version
} elseif (Test-Path $verFile) {
    $ver = (Get-Content $verFile -TotalCount 1).Trim()
} else {
    throw "Keine VERSION-Datei gefunden und keine -Version angegeben."
}
Write-Host "Version: $ver"

if (-not $Out) { $Out = Join-Path $src ("_acmp\AI_Framework_" + $ver) }

# --- Zielordner frisch anlegen ----------------------------------------------
if (Test-Path $Out) { Remove-Item $Out -Recurse -Force }
New-Item -ItemType Directory -Path $Out -Force | Out-Null

# --- Was ins Paket gehoert ---------------------------------------------------
# Bewusst eine Positivliste: alles Nicht-Aufgefuehrte bleibt draussen. So kann
# kein Ollama-Ordner, kein venv und keine Nutzerdatei versehentlich mitwandern.
$rootFiles = @(
    'main.py', 'db.py', 'requirements.txt', 'test_chat.py', 'VERSION',
    'LICENSE', 'update.bat',
    'start.bat', 'start_server.bat', 'install.bat', 'install.ps1', 'install.sh',
    'start.sh', 'uninstall.bat', 'uninstall.ps1', 'diagnose.bat', 'test_chat.bat'
)
$rootGlobs = @('*.md')
$dirs      = @('static', 'tools', 'docs', 'scripts', 'samples', 'bilder', 'defaults')

foreach ($f in $rootFiles) {
    $p = Join-Path $src $f
    if (Test-Path $p) { Copy-Item $p -Destination $Out }
}
foreach ($g in $rootGlobs) {
    Get-ChildItem -Path $src -Filter $g -File | ForEach-Object {
        Copy-Item $_.FullName -Destination $Out
    }
}
foreach ($d in $dirs) {
    $p = Join-Path $src $d
    if (Test-Path $p) {
        Copy-Item $p -Destination $Out -Recurse
        # Laufzeitmuell aus der Kopie entfernen
        Get-ChildItem -Path (Join-Path $Out $d) -Recurse -Force -Include '__pycache__' -Directory -ErrorAction SilentlyContinue |
            Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
        Get-ChildItem -Path (Join-Path $Out $d) -Recurse -Force -Include '*.pyc' -File -ErrorAction SilentlyContinue |
            Remove-Item -Force -ErrorAction SilentlyContinue
    }
}

# --- Sicherheitsnetz: darf definitiv NICHT im Paket sein ---------------------
$verboten = @('ollama', 'venv', '.venv', 'python', 'data', 'certs', 'models')
$gefunden = @()
foreach ($v in $verboten) {
    if (Test-Path (Join-Path $Out $v)) { $gefunden += $v }
}
if ($gefunden.Count -gt 0) {
    throw ("Paket enthaelt unerlaubte Bestandteile: " + ($gefunden -join ', '))
}
# config.json gehoert der Installation, nicht dem Paket
if (Test-Path (Join-Path $Out 'config.json')) {
    Remove-Item (Join-Path $Out 'config.json') -Force
}

$size = (Get-ChildItem $Out -Recurse -File | Measure-Object -Property Length -Sum).Sum
Write-Host ("Paket erstellt: {0}" -f $Out)
Write-Host ("Groesse       : {0:N1} MB" -f ($size / 1MB))
Write-Host "Enthaelt KEIN Ollama, KEINE Modelle, KEIN venv, KEINE Nutzerdaten."

if ($Zip) {
    $zipPath = "$Out.zip"
    if (Test-Path $zipPath) { Remove-Item $zipPath -Force }
    Compress-Archive -Path (Join-Path $Out '*') -DestinationPath $zipPath
    Write-Host "ZIP           : $zipPath"
}

Write-Host ""
Write-Host "ACMP-Befehlszeile fuer dieses Paket:"
Write-Host "  update.bat `"C:\Programme\AI_Framework`" /S /LOG:`"%TEMP%\aifw_update.log`""
Write-Host "Erfolg = Exit-Code 0.  Siehe docs\ACMP.md"
