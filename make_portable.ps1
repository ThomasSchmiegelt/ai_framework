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
    [switch]$UseSystemOllama,

    # Kopiert die KOMPLETTE Ollama-Laufzeit inkl. ROCm (natives AMD-Backend,
    # ~1,2 GB extra). Ohne diesen Schalter wird ROCm weggelassen — AMD-/Intel-
    # GPUs laufen dann über das mitkopierte Vulkan-Backend, NVIDIA über CUDA.
    [switch]$FullRuntime,

    # Bündelt Ollama-Binary + Laufzeit, aber KEINE Modelle (~2,5 GB statt ~9 GB).
    # Die start.bat des Bundles lädt fehlende Modelle beim ERSTSTART automatisch
    # von ollama.com nach (einmalig Internet auf dem Zielrechner nötig).
    [switch]$NoModels
)

$ErrorActionPreference = "Stop"

$APP_DIR       = $PSScriptRoot
$DATE_STAMP    = Get-Date -Format "yyyyMMdd"
$BUNDLE_NAME   = if ($UseSystemOllama) { "AI_Framework_Thomas_Portable_SystemOllama_$DATE_STAMP" }
                 elseif ($NoModels)    { "AI_Framework_Thomas_Portable_NoModels_$DATE_STAMP" }
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

if ($UseSystemOllama -and $NoModels) {
    Write-Fail "-UseSystemOllama und -NoModels schließen sich aus: die System-Ollama-Variante bündelt ohnehin keine Modelle."
}

Clear-Host
Write-Host ""
Write-Host "  ╔════════════════════════════════════════════╗" -ForegroundColor DarkCyan
Write-Host "  ║    🤖  AI_Framework_Thomas  —  Portable Bundle Creator  ║" -ForegroundColor DarkCyan
Write-Host "  ╚════════════════════════════════════════════╝" -ForegroundColor DarkCyan
Write-Host ""
Write-Host "  Ausgabe: $BUNDLE_DIR" -ForegroundColor Gray
Write-Host ""

# Windows-Pfadlängen-Grenze (260 Zeichen): pip legt im Bundle sehr tiefe Pfade an
# (z. B. python\Lib\site-packages\pip\_vendor\urllib3\contrib\emscripten\...).
# Ist das Zielverzeichnis zu tief, bricht die Paket-Installation mit
# "No such file or directory" ab. Früh warnen statt spät scheitern.
if ($BUNDLE_DIR.Length -gt 100) {
    Write-Warn "Zielpfad ist sehr lang ($($BUNDLE_DIR.Length) Zeichen) — pip kann an der Windows-260-Zeichen-Grenze scheitern."
    Write-Warn "Empfehlung: kurzes -OutDir wählen (z. B. C:\Portable) oder Long Paths aktivieren."
}

# ── 1. App-Dateien kopieren ────────────────────────────────────────────────────

Write-Step "App-Dateien kopieren..."

if (Test-Path $BUNDLE_DIR) {
    # Ein zuvor gebautes Bundle im selben Zielordner koennte noch LAUFEN: dessen
    # start.bat startet die gebuendelte ollama.exe, und der Smoke-Test weiter unten
    # ebenfalls. Eine laufende ollama.exe sperrt ihre Datei -> Remove-Item braeche mit
    # "Zugriff verweigert" ab. Darum gezielt jede ollama.exe stoppen, deren
    # Programmdatei INNERHALB dieses Bundles liegt (das System-Ollama bleibt unberuehrt).
    Get-CimInstance Win32_Process -Filter "Name='ollama.exe'" -ErrorAction SilentlyContinue |
        Where-Object { $_.ExecutablePath -and $_.ExecutablePath.StartsWith($BUNDLE_DIR, [System.StringComparison]::OrdinalIgnoreCase) } |
        ForEach-Object {
            Write-Warn "Stoppe laufende Ollama-Instanz aus dem alten Bundle (PID $($_.ProcessId))..."
            Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
        }
    Start-Sleep -Milliseconds 500
    try {
        Remove-Item $BUNDLE_DIR -Recurse -Force -ErrorAction Stop
    } catch {
        Write-Fail ("Bestehendes Bundle konnte nicht geloescht werden:`n    $BUNDLE_DIR`n" +
                    "    Ursache: $($_.Exception.Message)`n" +
                    "    Meist laeuft noch eine ollama.exe aus einem frueheren Bundle und sperrt die Datei.`n" +
                    "    -> Alle Bundle-Fenster schliessen bzw. die ollama.exe beenden (Task-Manager)`n" +
                    "       und erneut ausfuehren - oder mit -OutDir einen anderen Zielordner waehlen.")
    }
}
New-Item -ItemType Directory -Path $BUNDLE_DIR | Out-Null

# Robocopy: App-Dateien ohne venv, temporäre Ordner UND ohne die Laufzeit-Daten.
# WICHTIG: Das komplette "data\" des Baurechners wird ausgeschlossen — es enthaelt
# PRIVATE Nutzerdaten (SQLite-DB mit Gespraechen/RAG, user_profile.json, sowie
# conversations\, pst\, angebote\, rechnungen\, zeugnisse\, uploads\ ...). Ein
# portables Bundle darf davon NICHTS mitliefern. Die leere Verzeichnisstruktur und
# die mitgelieferten Default-Agenten baut Abschnitt 5 danach frisch auf.
$robocopyArgs = @(
    $APP_DIR, "$BUNDLE_DIR\app",
    "/E", "/XD", "venv", "__pycache__", ".git", ".claude", "$APP_DIR\data", "AI_Framework_Thomas_Portable*", "AI_Framework_Thomas_Server*",
    "/XF", "*.pyc", "server.log", "/NFL", "/NDL", "/NJH", "/NJS"
)
robocopy @robocopyArgs | Out-Null
Write-OK "App kopiert nach $BUNDLE_DIR\app (ohne Laufzeit-data\)"

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
# --no-cache-dir: der globale pip-Cache (%LOCALAPPDATA%\pip\cache) kann Wheels aus
# einem frueheren ELEVATED Lauf enthalten, die fuer den nicht-elevierten Bundle-Bau
# unlesbar sind (OSError: Permission denied). Das Embedded-Python braucht den
# globalen Cache ohnehin nicht.
& "$pyDir\python.exe" $getPipPath --quiet --no-cache-dir
# get-pip.py bringt NUR pip mit — nicht setuptools/wheel. Manche (reine-Python-)
# Pakete (z. B. Abhängigkeiten von extract-msg) haben kein fertiges Wheel und werden
# aus einer Source-Distribution gebaut; dafür braucht pip das Build-Backend
# setuptools.build_meta. Fehlt es, bricht die Installation mit
# "Cannot import 'setuptools.build_meta'" ab. Daher hier bereitstellen.
& "$pyDir\python.exe" -m pip install --quiet --no-warn-script-location --no-cache-dir setuptools wheel
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
    --quiet --no-warn-script-location --prefer-binary --no-build-isolation --no-cache-dir
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

$ollamaSrcDir = Split-Path $ollamaSrc -Parent

# ÄLTERE Ollama-Versionen: Laufzeit-DLLs liegen direkt neben der Exe.
Get-ChildItem $ollamaSrcDir -Filter "*.dll" -ErrorAction SilentlyContinue |
    Copy-Item -Destination $ollamaDir -ErrorAction SilentlyContinue

# NEUERE Ollama-Versionen: die komplette Laufzeit (ggml-Backends, CUDA/ROCm/
# Vulkan-Runner) liegt unter lib\ollama\... — OHNE dieses Verzeichnis startet
# die gebündelte ollama.exe nicht bzw. kann kein Modell laden! ollama.exe sucht
# lib\ relativ zur eigenen Exe, daher 1:1 als Unterverzeichnis mitkopieren.
$libSrc = Join-Path $ollamaSrcDir "lib"
if (Test-Path $libSrc) {
    # ROCm (natives AMD-Backend) ist mit Abstand am größten (~1,2 GB) und wird
    # ohne -FullRuntime weggelassen — AMD-/Intel-GPUs nutzt Ollama dann über das
    # mitkopierte Vulkan-Backend, NVIDIA nativ über CUDA, sonst CPU.
    $rcArgs = @($libSrc, "$ollamaDir\lib", "/E", "/NFL", "/NDL", "/NJH", "/NJS")
    if (-not $FullRuntime) { $rcArgs += @("/XD", "rocm*") }
    robocopy @rcArgs | Out-Null
    if ($LASTEXITCODE -ge 8) { Write-Fail "Ollama-Laufzeit (lib\) konnte nicht kopiert werden" }
    $libMB = [math]::Round(((Get-ChildItem "$ollamaDir\lib" -Recurse -File | Measure-Object Length -Sum).Sum / 1MB))
    Write-OK "Ollama-Laufzeit kopiert: $ollamaDir\lib ($libMB MB$(if (-not $FullRuntime) { ', ohne ROCm' }))"
}

# Plausibilitätsprüfung: ohne ggml-Backend kann 'ollama serve' keine Modelle
# laden — dann lieber hier hart abbrechen als ein kaputtes Bundle ausliefern.
$hasNewRuntime = Test-Path "$ollamaDir\lib\ollama\ggml.dll"
$hasOldRuntime = [bool](Get-ChildItem $ollamaDir -Filter "*.dll" -ErrorAction SilentlyContinue)
if (-not ($hasNewRuntime -or $hasOldRuntime)) {
    Write-Fail "Ollama-Laufzeit nicht gefunden (weder DLLs neben der Exe noch lib\ollama\ggml.dll in '$ollamaSrcDir'). Unbekanntes Ollama-Layout — bitte Ollama aktualisieren/neu installieren."
}
Write-OK "Ollama kopiert: $ollamaDir\ollama.exe"

# ── 4. LLM-Modelle kopieren (nur die Whitelist) ────────────────────────────────
# (Bei -NoModels übersprungen: die start.bat des Bundles lädt fehlende Modelle
#  beim Erststart automatisch von ollama.com nach.)

$modelsDest = "$ollamaDir\models"

if ($NoModels) {

New-Item -ItemType Directory -Path $modelsDest -Force | Out-Null
Write-Step "NoModels-Modus: Modelle werden NICHT gebündelt."
Write-OK "start.bat lädt beim Erststart nach: $($BUNDLE_MODELS -join ', ')"

} else {

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

}  # Ende: Modelle nur OHNE -NoModels kopieren

# ── 4b. Smoke-Test: startet die GEBÜNDELTE ollama.exe wirklich? ────────────────
# Fängt kaputte Bundles direkt beim Bauen ab (z. B. fehlende lib\-Laufzeit),
# statt erst auf dem Zielrechner. Läuft auf einem eigenen Testport.

Write-Step "Smoke-Test: gebündeltes Ollama starten..."
# Testport dynamisch waehlen (freien Port vom OS geben lassen) statt fest 11599.
# Ein fest verdrahteter Port kann durch eine ZURUECKGEBLIEBENE ollama.exe eines
# frueheren (evtl. abgebrochenen) Laufs belegt sein -> der neue Serve-Prozess
# scheitert dann mit "bind: address already in use" und der Smoke-Test meldet
# faelschlich ein kaputtes Bundle. Einen garantiert freien Port erfragen:
try {
    $lsnr = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback, 0)
    $lsnr.Start()
    $testPort = $lsnr.LocalEndpoint.Port
    $lsnr.Stop()
} catch {
    $testPort = 11599
}
$proc = $null
try {
    $env:OLLAMA_HOST   = "127.0.0.1:$testPort"
    $env:OLLAMA_MODELS = $modelsDest
    $smokeLog = Join-Path $env:TEMP "aif_ollama_smoke.log"
    $proc = Start-Process "$ollamaDir\ollama.exe" -ArgumentList "serve" -WindowStyle Hidden -PassThru -RedirectStandardError $smokeLog
    $ok = $false
    foreach ($i in 1..15) {
        Start-Sleep -Seconds 1
        if ($proc.HasExited) { break }
        try {
            $resp = Invoke-WebRequest -Uri "http://127.0.0.1:$testPort/api/tags" -UseBasicParsing -TimeoutSec 2
            if ($resp.StatusCode -eq 200) { $ok = $true; break }
        } catch {}
    }
    if ($ok) {
        $tags = ($resp.Content | ConvertFrom-Json).models
        Write-OK ("Gebündeltes Ollama läuft — sieht {0} Modell(e)" -f @($tags).Count)
        if (-not @($tags).Count) {
            if ($NoModels) { Write-OK "Keine Modelle im Bundle — wie vorgesehen (Erststart lädt nach)." }
            else { Write-Warn "Ollama startet, findet aber keine Modelle im Bundle-Verzeichnis!" }
        }
        # GPU-Erkennung aus dem Serve-Log melden (rein informativ — auf dem
        # ZIELrechner entscheidet dessen GPU/Treiber; s. ollama\server.log dort).
        try {
            $gpuLine = Select-String -Path $smokeLog -Pattern 'inference compute' -ErrorAction SilentlyContinue | Select-Object -First 1
            if ($gpuLine -and $gpuLine.Line -match 'library=(\S+).*?description="([^"]+)"') {
                Write-OK ("GPU auf DIESEM Rechner erkannt: {0} ({1})" -f $Matches[2], $Matches[1])
            } elseif (Select-String -Path $smokeLog -Pattern 'no compatible GPUs|library=cpu' -Quiet -ErrorAction SilentlyContinue) {
                Write-Warn "Auf diesem Rechner keine GPU erkannt — Bundle läuft hier auf CPU (Zielrechner kann abweichen)."
            }
        } catch {}
    } else {
        Write-Fail "Gebündeltes Ollama startet NICHT (Test auf 127.0.0.1:$testPort). Bundle wäre unbrauchbar — Abbruch."
    }
} finally {
    if ($proc -and -not $proc.HasExited) { Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue }
    Remove-Item Env:OLLAMA_HOST -ErrorAction SilentlyContinue
    Remove-Item Env:OLLAMA_MODELS -ErrorAction SilentlyContinue
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

:: GPU: NVIDIA laeuft ueber CUDA (aktueller NVIDIA-Treiber noetig), AMD/Intel-
:: Grafikkarten ueber Vulkan (aktueller Grafiktreiber). Reine iGPUs (integrierte
:: Intel-/AMD-Grafik) ignoriert Ollama standardmaessig -> zum Aktivieren die
:: naechste Zeile einkommentieren (Doppelpunkte entfernen):
:: set OLLAMA_IGPU_ENABLE=1
:: Was erkannt wurde, steht nach dem Start in ollama\server.log ("inference compute").

:: Antwortet unser Port schon? (z.B. start.bat zweimal gestartet) -> nicht neu starten
"%~dp0python\python.exe" -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:$OLLAMA_PORT/api/tags', timeout=2)" >nul 2>&1
if not errorlevel 1 goto ollamaready

echo [*] Starte Ollama (eigener Port $OLLAMA_PORT, Log: ollama\server.log)...
start /min "" cmd /c ""%~dp0ollama\ollama.exe" serve > "%~dp0ollama\server.log" 2>&1"

set /a _tries=0
:waitollama
timeout /t 2 /nobreak >nul
"%~dp0python\python.exe" -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:$OLLAMA_PORT/api/tags', timeout=2)" >nul 2>&1
if not errorlevel 1 goto ollamaready
set /a _tries+=1
if %_tries% lss 15 goto waitollama
echo [!] Ollama antwortet nicht - Chat/RAG koennten fehlschlagen. Details: ollama\server.log

:ollamaready

:: Fehlende Modelle automatisch von ollama.com nachladen (einmalig, Internet
:: noetig). Im Voll-Bundle sind alle Modelle enthalten -> nichts zu tun.
:: In der NoModels-Variante laedt dieser Block beim ERSTSTART alle Modelle.
for %%M in ($($BUNDLE_MODELS -join ' ')) do (
    "%~dp0ollama\ollama.exe" list 2>nul | findstr /i /l /c:"%%M" >nul || (
        echo [*] Lade Modell %%M von ollama.com herunter ^(einmalig^)...
        "%~dp0ollama\ollama.exe" pull %%M
        if errorlevel 1 echo [!] Konnte %%M nicht laden - Internetverbindung pruefen.
    )
)

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
} elseif ($NoModels) {
    $readmeContent = @"
# AI_Framework_Thomas Portable (Modelle beim Erststart)

Diese Variante bündelt **Ollama komplett** (Binary + Laufzeit, eigener Port $OLLAMA_PORT),
aber **keine Modelle** — dadurch ist das Bundle deutlich kleiner. ``start.bat`` lädt
die Modelle beim **ersten Start automatisch** von ollama.com nach.

## Verwendung
Doppelklick auf ``start.bat``

## Anforderungen
- Windows 10/11 (64-bit), keine Installation, keine Admin-Rechte
- **Beim ersten Start: Internetverbindung** (Modell-Download, einmalig ~7 GB):
$(($BUNDLE_MODELS | ForEach-Object { "  - $_" }) -join "`n")
- Danach läuft alles komplett offline

## Hinweise
- Der Erststart dauert je nach Internetgeschwindigkeit deutlich länger
  (Modell-Download); Fortschritt ist im Konsolenfenster sichtbar
- Die Modelle landen im Bundle-Ordner (``ollama\models``) — das Bundle bleibt
  portabel und kann danach mitsamt Modellen weiterkopiert werden
- Bricht der Download ab: ``start.bat`` einfach erneut starten (setzt fort)

## GPU-Unterstützung (läuft es nur auf CPU?)
- NVIDIA: nativ über CUDA — benötigt einen **aktuellen NVIDIA-Treiber**
- AMD/Intel-Grafikkarten: über Vulkan — aktueller Grafiktreiber nötig
  (natives AMD-ROCm nur, wenn das Bundle mit ``-FullRuntime`` erstellt wurde)
- **Integrierte GPUs (Intel/AMD iGPU) ignoriert Ollama standardmäßig** — auf
  solchen Rechnern läuft es bewusst auf CPU. Zum Ausprobieren in ``start.bat``
  die Zeile ``:: set OLLAMA_IGPU_ENABLE=1`` einkommentieren.
- Diagnose: ``ollama\server.log`` öffnen und nach ``inference compute`` suchen —
  dort steht, welche GPU erkannt wurde (``library=CUDA``/``Vulkan``) oder ob
  auf CPU gerechnet wird (``no compatible GPUs``).

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
- GPU-Unterstützung: NVIDIA nativ (CUDA, aktueller Treiber nötig), AMD/Intel
  über Vulkan, sonst CPU. Das native AMD-Backend (ROCm, ~1,2 GB) ist nur
  enthalten, wenn das Bundle mit ``-FullRuntime`` erstellt wurde.
  **Integrierte GPUs (iGPU) ignoriert Ollama standardmäßig** → in ``start.bat``
  die Zeile ``:: set OLLAMA_IGPU_ENABLE=1`` einkommentieren zum Ausprobieren.
  Diagnose: ``ollama\server.log`` nach ``inference compute`` durchsuchen.
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
