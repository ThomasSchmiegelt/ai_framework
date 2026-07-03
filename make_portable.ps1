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
    [string]$OutDir,

    # Bündelt WEDER die Ollama-Binary NOCH die Modelle und nutzt das bereits auf
    # dem Zielrechner installierte System-Ollama (Standard-Port 11434). Ergebnis:
    # ein deutlich kleineres Bundle, das ein vorhandenes Ollama voraussetzt.
    [switch]$UseSystemOllama
)

$ErrorActionPreference = "Stop"

$APP_DIR       = $PSScriptRoot
$DATE_STAMP    = Get-Date -Format "yyyyMMdd"
$BUNDLE_NAME   = if ($UseSystemOllama) { "AI_Framework_Thomas_Portable_SystemOllama_$DATE_STAMP" }
                 else                  { "AI_Framework_Thomas_Portable_$DATE_STAMP" }
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
# Bei -UseSystemOllama wird stattdessen der Standard-Port 11434 des bereits
# installierten Ollama verwendet (keine eigene Binary/Modelle im Bundle).
$OLLAMA_PORT   = if ($UseSystemOllama) { 11434 } else { 11500 }
$PYTHON_VER    = "3.12.7"
$PYTHON_ZIP    = "python-$PYTHON_VER-embed-amd64.zip"
$PYTHON_URL    = "https://www.python.org/ftp/python/$PYTHON_VER/$PYTHON_ZIP"
$GETPIP_URL    = "https://bootstrap.pypa.io/get-pip.py"
# Es werden GEZIELT nur die unten gelisteten Modelle ins Bundle kopiert (siehe
# Abschnitt 4) – nicht mehr das komplette lokale Modellverzeichnis. So landen z. B.
# nur die freigegebenen Modelle im Bundle und keine versehentlich lokal gepullten.
# Fehlt eines lokal, wird es vor dem Kopieren automatisch nachgezogen.
$BUNDLE_MODELS = @("ministral-3:3b", "qwen3.5:4b", "medgemma:4b", "nomic-embed-text:latest")  # ins Bundle
$MODELS        = @("ministral-3:3b", "qwen3.5:4b", "medgemma:4b")   # Modelle (fürs README): Chat + 🩺 Medizin
$EMBED_MODEL   = "nomic-embed-text:latest"           # RAG-Embeddings

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
    "/XF", "*.pyc", "server.log", "mail.json", "api_providers.json", "/NFL", "/NDL", "/NJH", "/NJS"
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
# get-pip.py bringt NUR pip mit — nicht setuptools/wheel. Manche (reine-Python-)
# Pakete (z. B. Abhängigkeiten von extract-msg) haben kein fertiges Wheel und werden
# aus einer Source-Distribution gebaut; dafür braucht pip das Build-Backend
# setuptools.build_meta. Fehlt es, bricht die Installation mit
# "Cannot import 'setuptools.build_meta'" ab. Daher hier bereitstellen.
& "$pyDir\python.exe" -m pip install --quiet --no-warn-script-location setuptools wheel
if ($LASTEXITCODE -ne 0) { Write-Fail "setuptools/wheel konnten nicht installiert werden" }
Write-OK "pip + setuptools/wheel installiert"

# Pakete installieren
Write-Step "Python-Pakete installieren (in Bundle)..."
#  --prefer-binary       : vorhandene Wheels bevorzugen (kein unnötiger sdist-Build).
#  --no-build-isolation  : nötige sdist-Builds (reine-Python-Pakete wie compressed-rtf,
#                          red-black-tree-mod) nutzen das oben installierte setuptools/
#                          wheel aus dem Haupt-Env. Im Embedded-Python funktioniert pips
#                          Build-Isolation nicht zuverlässig (kein venv), sonst
#                          "Cannot import 'setuptools.build_meta'".
& "$pyDir\python.exe" -m pip install -r "$BUNDLE_DIR\app\requirements.txt" `
    --quiet --no-warn-script-location --prefer-binary --no-build-isolation
if ($LASTEXITCODE -ne 0) { Write-Fail "Paket-Installation fehlgeschlagen" }
Write-OK "Pakete installiert"

# ── 3. Ollama Binary kopieren ──────────────────────────────────────────────────
# (Bei -UseSystemOllama übersprungen: das Bundle nutzt das installierte Ollama.)

if (-not $UseSystemOllama) {

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

# ── 4. LLM-Modelle kopieren (nur die Whitelist) ────────────────────────────────

Write-Step "LLM-Modelle kopieren (nur: $($BUNDLE_MODELS -join ', '))..."

# Modellquelle: bevorzugt die OLLAMA_MODELS-Umgebungsvariable (falls der Nutzer
# seine Modelle z. B. auf D:\OLLAMA_MODELS ausgelagert hat), sonst der Standard
# unter %USERPROFILE%\.ollama\models.
if ($env:OLLAMA_MODELS -and (Test-Path $env:OLLAMA_MODELS)) {
    $modelsSrc = $env:OLLAMA_MODELS
    Write-OK "Modellquelle aus OLLAMA_MODELS: $modelsSrc"
} else {
    $modelsSrc = "$env:USERPROFILE\.ollama\models"
}
$modelsDest = "$ollamaDir\models"

# Kopiert genau EIN Modell (manifest + die referenzierten Blobs) ins Bundle.
# Liefert $true bei Erfolg. Modellname im Format "name:tag" (tag-Default: latest).
function Copy-OllamaModel {
    param([string]$ModelRef, [string]$SrcRoot, [string]$DestRoot)

    $parts = $ModelRef -split ':', 2
    $name  = $parts[0]
    $tag   = if ($parts.Count -gt 1 -and $parts[1]) { $parts[1] } else { "latest" }

    # Manifest-Pfad: …/manifests/registry.ollama.ai/library/<name>/<tag>
    $manifestRel = "manifests\registry.ollama.ai\library\$name\$tag"
    $manifestSrc = Join-Path $SrcRoot $manifestRel
    if (-not (Test-Path $manifestSrc)) {
        Write-Warn "Manifest fehlt für $ModelRef ($manifestSrc) — übersprungen."
        return $false
    }

    # Manifest kopieren (Verzeichnisstruktur erhalten)
    $manifestDest = Join-Path $DestRoot $manifestRel
    New-Item -ItemType Directory -Path (Split-Path $manifestDest -Parent) -Force | Out-Null
    Copy-Item $manifestSrc $manifestDest -Force

    # Referenzierte Blobs (config + layers) aus dem Manifest lesen und kopieren
    try {
        $manifest = Get-Content $manifestSrc -Raw | ConvertFrom-Json
    } catch {
        Write-Warn "Manifest für $ModelRef nicht lesbar — übersprungen."
        return $false
    }
    $digests = @()
    if ($manifest.config -and $manifest.config.digest) { $digests += $manifest.config.digest }
    foreach ($layer in $manifest.layers) { if ($layer.digest) { $digests += $layer.digest } }

    $blobDestDir = Join-Path $DestRoot "blobs"
    New-Item -ItemType Directory -Path $blobDestDir -Force | Out-Null
    foreach ($d in ($digests | Select-Object -Unique)) {
        $blobFile = $d -replace ':', '-'                 # sha256:abc -> sha256-abc
        $blobSrc  = Join-Path $SrcRoot "blobs\$blobFile"
        $blobDest = Join-Path $blobDestDir $blobFile
        if (Test-Path $blobSrc) {
            if (-not (Test-Path $blobDest)) { Copy-Item $blobSrc $blobDest -Force }
        } else {
            Write-Warn "Blob fehlt: $blobFile (für $ModelRef)"
        }
    }
    return $true
}

if (-not (Test-Path $modelsSrc)) {
    Write-Warn "Ollama-Modellverzeichnis nicht gefunden: $modelsSrc"
    Write-Warn "Modelle werden beim ersten Start heruntergeladen."
} else {
    New-Item -ItemType Directory -Path $modelsDest -Force | Out-Null
    $bundled = @()
    foreach ($m in $BUNDLE_MODELS) {
        $base = ($m -split ':')[0]
        # Lokal vorhanden? Sonst nachziehen (gegen das System-Ollama auf 11434).
        $have = $false
        try { $have = ((& ollama list 2>$null) -match [regex]::Escape($base)) } catch {}
        if (-not $have) {
            Write-Warn "$m nicht lokal gefunden — wird nachgeladen..."
            & ollama pull $m
            if ($LASTEXITCODE -ne 0) { Write-Warn "Konnte $m nicht laden — wird übersprungen." }
        }
        if (Copy-OllamaModel -ModelRef $m -SrcRoot $modelsSrc -DestRoot $modelsDest) {
            $bundled += $m
            Write-OK "Gebündelt: $m"
        }
    }
    if ($bundled.Count) { Write-OK ("Modelle im Bundle: " + ($bundled -join ', ')) }
    else { Write-Warn "Keine Modelle gebündelt — Bundle braucht beim ersten Start Internet." }
}

}  # Ende: Ollama-Binary + Modelle nur OHNE -UseSystemOllama bündeln
else {
    Write-Step "System-Ollama-Modus: Ollama-Binary & Modelle werden NICHT gebündelt."
    Write-OK "Bundle nutzt das installierte Ollama auf 127.0.0.1:$OLLAMA_PORT"
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
# (Bei -UseSystemOllama nicht nötig — config bleibt auf dem Standard-Port 11434.)

if (-not $UseSystemOllama) {
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
}

# ── 6. Start-Skripte im Bundle ─────────────────────────────────────────────────

Write-Step "Portable Start-Skript erstellen..."

if ($UseSystemOllama) {
    # Variante „System-Ollama": nutzt das auf dem Rechner installierte Ollama
    # (Port 11434, Standard-Modellverzeichnis). Startet es bei Bedarf über PATH.
    $startContent = @"
@echo off
title AI_Framework_Thomas Portable (System-Ollama)
cd /d "%~dp0app"

:: Nutzt das INSTALLIERTE Ollama (Standard-Port 11434, dessen Modellverzeichnis).
:: Voraussetzung: Ollama ist installiert und die Modelle sind gezogen
::   ollama pull ministral-3:3b   &   ollama pull qwen3.5:4b   &   ollama pull medgemma:4b   &   ollama pull nomic-embed-text
"%~dp0python\python.exe" -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:$OLLAMA_PORT/api/tags', timeout=2)" >nul 2>&1
if not errorlevel 1 goto ollamaready

echo [*] Starte installiertes Ollama (PATH)...
start /min "" ollama serve

set /a _tries=0
:waitollama
timeout /t 2 /nobreak >nul
"%~dp0python\python.exe" -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:$OLLAMA_PORT/api/tags', timeout=2)" >nul 2>&1
if not errorlevel 1 goto ollamaready
set /a _tries+=1
if %_tries% lss 15 goto waitollama
echo [!] Ollama antwortet nicht. Ist Ollama installiert? (https://ollama.com)
echo [!] Chat/RAG koennten ohne laufendes Ollama fehlschlagen.

:ollamaready

start /min "" cmd /c "timeout /t 3 >nul && start http://localhost:8780"

echo.
echo  AI_Framework_Thomas Portable (System-Ollama) gestartet
echo  URL:    http://localhost:8780
echo  Ollama: http://127.0.0.1:$OLLAMA_PORT  (installiertes System-Ollama)
echo  Fenster schliessen um zu beenden.
echo.

"%~dp0python\python.exe" -m uvicorn main:app --host 127.0.0.1 --port 8780
"@
} else {
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
}

# start.bat OHNE BOM schreiben — cmd.exe darf kein BOM vor '@echo off' sehen
[System.IO.File]::WriteAllText("$BUNDLE_DIR\start.bat", $startContent, (New-Object System.Text.UTF8Encoding($false)))

Write-OK "start.bat erstellt"

# ── 7. README im Bundle ────────────────────────────────────────────────────────

if ($UseSystemOllama) {
    $readmeContent = @"
# AI_Framework_Thomas Portable (System-Ollama)

Diese Variante bündelt **weder Ollama noch die Modelle** und nutzt das bereits auf
dem Rechner installierte Ollama (Port $OLLAMA_PORT). Das Bundle ist dadurch deutlich kleiner.

## Verwendung
Doppelklick auf ``start.bat``

## Voraussetzungen
- Windows 10/11 (64-bit)
- **Ollama installiert** (https://ollama.com) und folgende Modelle gezogen:
$(($MODELS | ForEach-Object { "  - ``ollama pull $_``" }) -join "`n")
  - ``ollama pull $EMBED_MODEL``  (für RAG)

## Hinweise
- ``start.bat`` startet das installierte Ollama bei Bedarf automatisch (über PATH)
- Es wird das Standard-Modellverzeichnis des installierten Ollama genutzt
- Fehlt ein Modell: ``ollama pull <name>`` in einer normalen Konsole

## Bundle erstellt
$(Get-Date -Format "yyyy-MM-dd HH:mm")
"@
} else {
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
}

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
