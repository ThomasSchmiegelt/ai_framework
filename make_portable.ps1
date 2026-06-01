#Requires -Version 5.1
<#
.SYNOPSIS
    Erstellt ein portables AI_Framework_Thomas Bundle — kein Install nötig auf Zielrechner.
.DESCRIPTION
    Bündelt App, Embedded-Python, Ollama-Binary und LLM-Modelle in ein Verzeichnis.
    Voraussetzung: install.bat wurde bereits erfolgreich ausgeführt.
#>

param(
    # Zielverzeichnis, in dem der Bundle-Ordner angelegt wird.
    # Standard: das übergeordnete Verzeichnis dieses Skripts.
    [string]$OutDir
)

$ErrorActionPreference = "Stop"

$APP_DIR       = $PSScriptRoot
$DATE_STAMP    = Get-Date -Format "yyyyMMdd"
$BUNDLE_NAME   = "AI_Framework_Thomas_Portable_$DATE_STAMP"
# Basisverzeichnis: -OutDir falls angegeben, sonst Eltern-Ordner des Skripts.
if ($OutDir) {
    if (-not (Test-Path $OutDir)) { New-Item -ItemType Directory -Path $OutDir -Force | Out-Null }
    $BASE_DIR = (Resolve-Path $OutDir).Path
} else {
    $BASE_DIR = Split-Path $APP_DIR -Parent
}
$BUNDLE_DIR    = Join-Path $BASE_DIR $BUNDLE_NAME
# Eigener Ollama-Port fürs Bundle — kollidiert nicht mit einem evtl. bereits
# laufenden System-Ollama auf dem Standard-Port 11434. Garantiert, dass das
# Bundle sein eigenes Modellverzeichnis (inkl. nomic-embed-text für RAG) nutzt.
$OLLAMA_PORT   = 11500
$PYTHON_VER    = "3.12.7"
$PYTHON_ZIP    = "python-$PYTHON_VER-embed-amd64.zip"
$PYTHON_URL    = "https://www.python.org/ftp/python/$PYTHON_VER/$PYTHON_ZIP"
$GETPIP_URL    = "https://bootstrap.pypa.io/get-pip.py"
# Es werden ALLE lokal vorhandenen Ollama-Modelle mitgebündelt (siehe Abschnitt 4),
# damit jedes im Profil zugewiesene Modell (Allgemein/Programmieren/Wissenschaftlich)
# auf dem Zielrechner verfügbar ist. Zum Schlankhalten vor dem Bündeln nicht
# benötigte Modelle entfernen: ollama rm <modell>.
$MODELS        = @("ministral-3:3b")         # Basismodell (nur fürs README)
$EMBED_MODEL   = "nomic-embed-text:latest"   # RAG-Embeddings

function Write-Step  { param($t) Write-Host "`n[►] $t" -ForegroundColor Cyan }
function Write-OK    { param($t) Write-Host "    [✓] $t" -ForegroundColor Green }
function Write-Warn  { param($t) Write-Host "    [!] $t" -ForegroundColor Yellow }
function Write-Fail  { param($t) Write-Host "`n[✗] $t" -ForegroundColor Red; Read-Host "Enter"; exit 1 }

Clear-Host
Write-Host ""
Write-Host "  ╔════════════════════════════════════════════╗" -ForegroundColor DarkCyan
Write-Host "  ║    🤖  AI_Framework_Thomas  —  Portable Bundle Creator  ║" -ForegroundColor DarkCyan
Write-Host "  ╚════════════════════════════════════════════╝" -ForegroundColor DarkCyan
Write-Host ""
Write-Host "  Ausgabe: $BUNDLE_DIR" -ForegroundColor Gray
Write-Host ""

# ── 1. App-Dateien kopieren ────────────────────────────────────────────────────

Write-Step "App-Dateien kopieren..."
$excludes = @("venv", "__pycache__", ".git", "*.pyc", "data\*.db",
              "AI_Framework_Thomas_Portable*", "AI_Framework_Thomas_Server*", $BUNDLE_NAME)

if (Test-Path $BUNDLE_DIR) {
    Remove-Item $BUNDLE_DIR -Recurse -Force
}
New-Item -ItemType Directory -Path $BUNDLE_DIR | Out-Null

# Robocopy: App-Dateien ohne venv und temporäre Ordner
$robocopyArgs = @(
    $APP_DIR, "$BUNDLE_DIR\app",
    "/E", "/XD", "venv", "__pycache__", ".git", ".claude", "AI_Framework_Thomas_Portable*", "AI_Framework_Thomas_Server*",
    "/XF", "*.pyc", "server.log", "/NFL", "/NDL", "/NJH", "/NJS"
)
robocopy @robocopyArgs | Out-Null
Write-OK "App kopiert nach $BUNDLE_DIR\app"

# ── 2. Python Embeddable ───────────────────────────────────────────────────────

Write-Step "Python $PYTHON_VER Embeddable Package laden..."
$tmpZip = "$env:TEMP\$PYTHON_ZIP"

if (-not (Test-Path $tmpZip)) {
    Write-Host "    Lade herunter: $PYTHON_URL" -ForegroundColor Gray
    Invoke-WebRequest -Uri $PYTHON_URL -OutFile $tmpZip -UseBasicParsing
}

$pyDir = "$BUNDLE_DIR\python"
New-Item -ItemType Directory -Path $pyDir -Force | Out-Null
Expand-Archive -Path $tmpZip -DestinationPath $pyDir -Force
Write-OK "Python entpackt nach $pyDir"

# _pth-Datei anpassen: import site einschalten (für pip/site-packages)
$pthFile = Get-ChildItem $pyDir -Filter "python3*._pth" | Select-Object -First 1
if ($pthFile) {
    $content = Get-Content $pthFile.FullName -Raw
    $content = $content -replace "#import site", "import site"
    $content = $content + "`nLib`nLib\\site-packages`n"
    # WICHTIG: Embedded-Python ._pth darf KEIN BOM haben — sonst klebt das BOM an
    # der ersten Pfadzeile (pythonXYZ.zip) und der Interpreter findet 'encodings'
    # nicht ("Fatal Python error: init_fs_encoding"). Daher UTF-8 OHNE BOM schreiben.
    [System.IO.File]::WriteAllText($pthFile.FullName, $content, (New-Object System.Text.UTF8Encoding($false)))
    Write-OK "_pth konfiguriert: $($pthFile.Name)"
}

# pip installieren
Write-Step "pip in Embedded Python einrichten..."
$getPipPath = "$env:TEMP\get-pip.py"
Invoke-WebRequest -Uri $GETPIP_URL -OutFile $getPipPath -UseBasicParsing
& "$pyDir\python.exe" $getPipPath --quiet
Write-OK "pip installiert"

# Pakete installieren
Write-Step "Python-Pakete installieren (in Bundle)..."
& "$pyDir\python.exe" -m pip install -r "$BUNDLE_DIR\app\requirements.txt" `
    --quiet --no-warn-script-location
if ($LASTEXITCODE -ne 0) { Write-Fail "Paket-Installation fehlgeschlagen" }
Write-OK "Pakete installiert"

# ── 3. Ollama Binary kopieren ──────────────────────────────────────────────────

Write-Step "Ollama Binary suchen und kopieren..."
$ollamaLocations = @(
    "$env:LOCALAPPDATA\Programs\Ollama\ollama.exe",
    "$env:ProgramFiles\Ollama\ollama.exe",
    $(if ($c = Get-Command "ollama" -ErrorAction SilentlyContinue) { $c.Source })
)

$ollamaSrc = $ollamaLocations | Where-Object { $_ -and (Test-Path $_) } | Select-Object -First 1

if (-not $ollamaSrc) {
    Write-Fail "Ollama nicht gefunden. Bitte zuerst install.bat ausführen."
}

$ollamaDir = "$BUNDLE_DIR\ollama"
New-Item -ItemType Directory -Path $ollamaDir -Force | Out-Null
Copy-Item $ollamaSrc "$ollamaDir\ollama.exe"

# Weitere Ollama-DLLs im selben Verzeichnis mitkopieren
$ollamaSrcDir = Split-Path $ollamaSrc -Parent
Get-ChildItem $ollamaSrcDir -Filter "*.dll" -ErrorAction SilentlyContinue |
    Copy-Item -Destination $ollamaDir -ErrorAction SilentlyContinue
Write-OK "Ollama kopiert: $ollamaDir\ollama.exe"

# ── 4. LLM-Modelle kopieren ────────────────────────────────────────────────────

Write-Step "LLM-Modelle kopieren (alle lokal vorhandenen — kann mehrere GB sein)..."

# Embedding-Modell für RAG absichern: ist es lokal nicht vorhanden, fehlt es auch
# im Bundle → kein RAG. Daher vor dem Kopieren ggf. nachziehen.
$embedBase = ($EMBED_MODEL -split ':')[0]
$haveEmbed = $false
try { $haveEmbed = ((& ollama list 2>$null) -match [regex]::Escape($embedBase)) } catch {}
if (-not $haveEmbed) {
    Write-Warn "$EMBED_MODEL nicht lokal gefunden — wird für RAG nachgeladen..."
    & ollama pull $EMBED_MODEL
    if ($LASTEXITCODE -ne 0) {
        Write-Warn "Konnte $EMBED_MODEL nicht laden — RAG funktioniert im Bundle evtl. nicht."
    } else { Write-OK "$EMBED_MODEL geladen" }
} else {
    Write-OK "$EMBED_MODEL ist lokal vorhanden (wird gebündelt)"
}

$modelsSrc  = "$env:USERPROFILE\.ollama\models"
$modelsDest = "$ollamaDir\models"

if (-not (Test-Path $modelsSrc)) {
    Write-Warn "Ollama-Modellverzeichnis nicht gefunden: $modelsSrc"
    Write-Warn "Modelle werden beim ersten Start heruntergeladen."
} else {
    New-Item -ItemType Directory -Path $modelsDest -Force | Out-Null
    # Komplettes Modellverzeichnis (manifests + blobs) spiegeln → jedes lokal
    # gepullte Modell ist im Bundle verfügbar und im Profil zuweisbar.
    robocopy $modelsSrc $modelsDest /E /NFL /NDL /NJH /NJS | Out-Null
    $pulled = @()
    try { $pulled = (& ollama list 2>$null | Select-Object -Skip 1 | ForEach-Object { ($_ -split '\s+')[0] }) } catch {}
    if ($pulled) { Write-OK ("Modelle gebündelt: " + ($pulled -join ', ')) }
    else { Write-OK "Modellverzeichnis kopiert" }
}

# ── 5. Datenverzeichnis anlegen ────────────────────────────────────────────────

foreach ($d in @("conversations","uploads","agents","reports","code","plans","dossiers","profile_assets")) {
    New-Item -ItemType Directory -Path "$BUNDLE_DIR\app\data\$d" -Force | Out-Null
}

# Default-Agenten kopieren falls vorhanden
if (Test-Path "$APP_DIR\data\agents") {
    Copy-Item "$APP_DIR\data\agents\*" "$BUNDLE_DIR\app\data\agents\" -Force -ErrorAction SilentlyContinue
}

# ── 5b. config.json im Bundle auf den eigenen Ollama-Port umschreiben ──────────

Write-Step "Bundle-config.json auf Port $OLLAMA_PORT setzen..."
$cfgPath = "$BUNDLE_DIR\app\config.json"
if (Test-Path $cfgPath) {
    $cfg = Get-Content $cfgPath -Raw | ConvertFrom-Json
    $cfg | Add-Member -NotePropertyName ollama_base -NotePropertyValue "http://localhost:$OLLAMA_PORT" -Force
    [System.IO.File]::WriteAllText($cfgPath, ($cfg | ConvertTo-Json), (New-Object System.Text.UTF8Encoding($false)))
    Write-OK "ollama_base = http://localhost:$OLLAMA_PORT"
} else {
    Write-Warn "config.json im Bundle nicht gefunden — Port bleibt auf Standard"
}

# ── 6. Start-Skripte im Bundle ─────────────────────────────────────────────────

Write-Step "Portable Start-Skript erstellen..."

$startContent = @"
@echo off
title AI_Framework_Thomas Portable
cd /d "%~dp0app"

:: Eigenes Modellverzeichnis + eigener Ollama-Port (kollidiert NICHT mit einem
:: evtl. system-installierten Ollama auf 11434). So nutzt das Bundle garantiert
:: seine gebuendelten Modelle inkl. nomic-embed-text fuer RAG.
set OLLAMA_MODELS=%~dp0ollama\models
set OLLAMA_HOST=127.0.0.1:$OLLAMA_PORT

:: Antwortet unser Port schon? (z.B. start.bat zweimal gestartet) -> nicht neu starten
"%~dp0python\python.exe" -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:$OLLAMA_PORT/api/tags', timeout=2)" >nul 2>&1
if not errorlevel 1 goto ollamaready

echo [*] Starte Ollama (eigener Port $OLLAMA_PORT)...
start /min "" "%~dp0ollama\ollama.exe" serve

set /a _tries=0
:waitollama
timeout /t 2 /nobreak >nul
"%~dp0python\python.exe" -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:$OLLAMA_PORT/api/tags', timeout=2)" >nul 2>&1
if not errorlevel 1 goto ollamaready
set /a _tries+=1
if %_tries% lss 15 goto waitollama
echo [!] Ollama antwortet nicht - Chat/RAG koennten fehlschlagen.

:ollamaready

:: Browser nach kurzer Verzoegerung
start /min "" cmd /c "timeout /t 3 >nul && start http://localhost:8780"

echo.
echo  AI_Framework_Thomas Portable gestartet
echo  URL:    http://localhost:8780
echo  Ollama: http://127.0.0.1:$OLLAMA_PORT
echo  Fenster schliessen um zu beenden.
echo.

"%~dp0python\python.exe" -m uvicorn main:app --host 127.0.0.1 --port 8780
"@

# start.bat OHNE BOM schreiben — cmd.exe darf kein BOM vor '@echo off' sehen
[System.IO.File]::WriteAllText("$BUNDLE_DIR\start.bat", $startContent, (New-Object System.Text.UTF8Encoding($false)))

Write-OK "start.bat erstellt"

# ── 7. README im Bundle ────────────────────────────────────────────────────────

$readmeContent = @"
# AI_Framework_Thomas Portable

## Verwendung
Doppelklick auf ``start.bat``

## Anforderungen
- Windows 10/11 (64-bit)
- Keine Installation notwendig

## Modelle
$(($MODELS | ForEach-Object { "- $_" }) -join "`n")

## Hinweise
- Beim ersten Start kann es 10-30 Sekunden dauern bis Ollama bereit ist
- Modelle werden aus dem ``ollama\models`` Unterverzeichnis geladen
- Das Bundle nutzt einen **eigenen Ollama-Port ($OLLAMA_PORT)**, damit es nicht
  mit einem evtl. bereits installierten Ollama (Port 11434) kollidiert
- Falls Modelle fehlen, Pull gegen den Bundle-Port (in der ``start.bat``-Konsole):
  ``set OLLAMA_HOST=127.0.0.1:$OLLAMA_PORT`` & ``set OLLAMA_MODELS=%CD%\ollama\models``
  dann ``ollama\ollama.exe pull nomic-embed-text``

## Bundle erstellt
$(Get-Date -Format "yyyy-MM-dd HH:mm")
"@

$readmeContent | Out-File -FilePath "$BUNDLE_DIR\README.md" -Encoding UTF8

# ── Fertig ────────────────────────────────────────────────────────────────────

$size = (Get-ChildItem $BUNDLE_DIR -Recurse | Measure-Object -Property Length -Sum).Sum / 1GB

Write-Host ""
Write-Host "  ╔══════════════════════════════════════════╗" -ForegroundColor Green
Write-Host "  ║      Portable Bundle fertig!             ║" -ForegroundColor Green
Write-Host "  ╚══════════════════════════════════════════╝" -ForegroundColor Green
Write-Host ""
Write-Host "  Verzeichnis: $BUNDLE_DIR" -ForegroundColor White
Write-Host "  Größe:       $([math]::Round($size, 2)) GB" -ForegroundColor White
Write-Host ""
Write-Host "  ➜ Gesamtes Verzeichnis '$BUNDLE_NAME' kopieren oder zippen" -ForegroundColor Cyan
Write-Host "  ➜ Starten: $BUNDLE_NAME\start.bat" -ForegroundColor Cyan
Write-Host ""
