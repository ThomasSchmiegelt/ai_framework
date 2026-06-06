#Requires -Version 5.1
<#
.SYNOPSIS
    AI_Framework_Thomas — Master-Installer für Windows 11
.DESCRIPTION
    Installiert Python 3.12, Ollama und richtet AI_Framework_Thomas vollständig ein.
    Lädt das Standardmodell ministral-3:3b und das Embedding-Modell herunter.
    Weitere Modelle (z. B. für Programmieren/Wissenschaft) bei Bedarf nachladen:
    ollama pull <modell> — danach im Profil unter „Modelle" zuweisen.
#>

$ErrorActionPreference = "Stop"

$APP_DIR       = $PSScriptRoot
$MODELS        = @("ministral-3:3b", "gemma4:e2b")   # Standardmodelle; weitere bei Bedarf via 'ollama pull'
$EMBED_MODEL   = "nomic-embed-text"   # RAG-Embeddings (klein, CPU-tauglich)
$PYTHON_MIN    = [Version]"3.11.0"
$VENV_DIR      = Join-Path $APP_DIR "venv"
$CONFIG_FILE   = Join-Path $APP_DIR "config.json"

# ── Hilfsfunktionen ────────────────────────────────────────────────────────────

function Write-Step  { param($t) Write-Host "`n[►] $t" -ForegroundColor Cyan }
function Write-OK    { param($t) Write-Host "    [✓] $t" -ForegroundColor Green }
function Write-Warn  { param($t) Write-Host "    [!] $t" -ForegroundColor Yellow }
function Write-Fail  { param($t) Write-Host "`n[✗] $t" -ForegroundColor Red; Read-Host "Enter drücken zum Beenden"; exit 1 }
# Schreibt Text als UTF-8 OHNE BOM. Windows PowerShell 5.1 setzt bei
# Out-File -Encoding UTF8 ein BOM voran — das fuehrt in config.json zu einem
# fuehrenden ﻿, an dem json.loads(read_text("utf-8")) in main.py scheitert.
function Write-Utf8NoBom { param($Path, $Text)
    [System.IO.File]::WriteAllText($Path, $Text, (New-Object System.Text.UTF8Encoding($false)))
}

function Wait-OllamaReady {
    for ($i = 0; $i -lt 15; $i++) {
        try {
            $r = Invoke-RestMethod -Uri "http://localhost:11434/api/tags" -TimeoutSec 2
            return $true
        } catch { Start-Sleep 1 }
    }
    return $false
}

# ── Banner ─────────────────────────────────────────────────────────────────────

Clear-Host
Write-Host ""
Write-Host "  ╔══════════════════════════════════════════╗" -ForegroundColor DarkCyan
Write-Host "  ║      🤖  AI_Framework_Thomas  —  Windows Installer      ║" -ForegroundColor DarkCyan
Write-Host "  ╚══════════════════════════════════════════╝" -ForegroundColor DarkCyan
Write-Host ""
Write-Host "  Installationsverzeichnis: $APP_DIR" -ForegroundColor Gray
Write-Host "  Modelle: $($MODELS -join ', ')" -ForegroundColor Gray
Write-Host ""

# ── 1. Python ──────────────────────────────────────────────────────────────────

Write-Step "Python prüfen..."
$pythonCmd = $null

foreach ($cmd in @("python", "py", "python3")) {
    try {
        $out = & $cmd --version 2>&1
        if ($out -match "Python (\d+\.\d+\.\d+)") {
            $v = [Version]$Matches[1]
            if ($v -ge $PYTHON_MIN) { $pythonCmd = $cmd; Write-OK "Python $v ($cmd)"; break }
        }
    } catch {}
}

if (-not $pythonCmd) {
    Write-Warn "Python $PYTHON_MIN+ nicht gefunden — installiere via winget..."
    try {
        winget install --id Python.Python.3.12 -e --source winget --accept-source-agreements --accept-package-agreements --silent
        $env:PATH = [Environment]::GetEnvironmentVariable("PATH","Machine") + ";" + [Environment]::GetEnvironmentVariable("PATH","User")
        # Konkreten python.exe-Pfad suchen falls PATH noch nicht aktualisiert
        $pyFound = Get-ChildItem "$env:LOCALAPPDATA\Programs\Python" -Filter "python.exe" -Recurse -ErrorAction SilentlyContinue | Select-Object -First 1
        if (-not $pyFound) { $pyFound = Get-ChildItem "C:\Python3*" -Filter "python.exe" -Recurse -ErrorAction SilentlyContinue | Select-Object -First 1 }
        $pythonCmd = if ($pyFound) { $pyFound.FullName } else { "python" }
        Write-OK "Python installiert"
    } catch {
        Write-Fail "Python konnte nicht automatisch installiert werden.`nManuelle Installation: https://www.python.org/downloads/ (Python 3.12+)"
    }
}

# ── 2. Ollama ──────────────────────────────────────────────────────────────────

Write-Step "Ollama prüfen..."
$ollamaFound = $false

try {
    $ov = & ollama --version 2>&1
    if ($ov -match "ollama") { $ollamaFound = $true; Write-OK "Ollama: $ov" }
} catch {}

if (-not $ollamaFound) {
    Write-Warn "Ollama nicht gefunden — installiere..."
    $installed = $false

    # Versuch 1: winget
    try {
        winget install --id Ollama.Ollama -e --source winget --accept-source-agreements --accept-package-agreements --silent
        $env:PATH = [Environment]::GetEnvironmentVariable("PATH","Machine") + ";" + [Environment]::GetEnvironmentVariable("PATH","User")
        $installed = $true
        Write-OK "Ollama via winget installiert"
    } catch {}

    # Versuch 2: direkter Download
    if (-not $installed) {
        Write-Warn "winget fehlgeschlagen — lade Installer herunter..."
        $tmp = "$env:TEMP\OllamaSetup.exe"
        Invoke-WebRequest -Uri "https://ollama.com/download/OllamaSetup.exe" -OutFile $tmp -UseBasicParsing
        Start-Process -FilePath $tmp -ArgumentList "/S" -Wait
        $env:PATH = [Environment]::GetEnvironmentVariable("PATH","Machine") + ";" + [Environment]::GetEnvironmentVariable("PATH","User")
        Write-OK "Ollama installiert"
    }
}

# Ollama-Server starten — Pfad aus bekannten Installationsorten ermitteln
$ollamaRunning = Get-Process -Name "ollama" -ErrorAction SilentlyContinue
if (-not $ollamaRunning) {
    Write-Step "Ollama-Server starten..."
    $ollamaExe = @(
        "$env:LOCALAPPDATA\Programs\Ollama\ollama.exe",
        "$env:ProgramFiles\Ollama\ollama.exe",
        (Get-Command "ollama" -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Source)
    ) | Where-Object { $_ -and (Test-Path $_) } | Select-Object -First 1
    if (-not $ollamaExe) { Write-Warn "ollama.exe nicht gefunden — bitte Ollama neu installieren" }
    else {
        Start-Process -FilePath $ollamaExe -ArgumentList "serve" -WindowStyle Hidden
        if (Wait-OllamaReady) { Write-OK "Ollama bereit" }
        else { Write-Warn "Ollama antwortet nicht — Modell-Download könnte fehlschlagen" }
    }
} else {
    Write-OK "Ollama läuft bereits"
}

# ── 3. Virtuelle Umgebung ──────────────────────────────────────────────────────

Write-Step "Python venv einrichten..."
if (-not (Test-Path "$VENV_DIR\Scripts\python.exe")) {
    & $pythonCmd -m venv $VENV_DIR
    Write-OK "venv erstellt: $VENV_DIR"
} else {
    Write-OK "venv vorhanden"
}

$pyVenv  = "$VENV_DIR\Scripts\python.exe"
$pipVenv = "$VENV_DIR\Scripts\pip.exe"

# ── 4. Pakete installieren ─────────────────────────────────────────────────────

Write-Step "Python-Pakete installieren..."
& $pipVenv install --upgrade pip --quiet
& $pipVenv install -r "$APP_DIR\requirements.txt" --quiet
if ($LASTEXITCODE -ne 0) { Write-Fail "Paket-Installation fehlgeschlagen" }
Write-OK "Alle Pakete installiert"

# ── 5. LLM-Modelle laden ──────────────────────────────────────────────────────

Write-Step "KI-Modelle laden (ca. 3–8 GB — bitte warten)..."
Write-Host "  Hinweis: Modell-Tags auf https://ollama.com/library verifizieren falls Fehler auftreten." -ForegroundColor DarkGray

foreach ($model in ($MODELS + $EMBED_MODEL)) {
    Write-Host "`n  ➜ Lade $model..." -ForegroundColor White
    & ollama pull $model
    if ($LASTEXITCODE -eq 0) { Write-OK "$model geladen" }
    else { Write-Warn "$model fehlgeschlagen — Tag prüfen oder später manuell: ollama pull $model" }
}

# ── 6. config.json ────────────────────────────────────────────────────────────

Write-Step "Konfigurationsdatei anlegen..."
if (-not (Test-Path $CONFIG_FILE)) {
    $cfg = [ordered]@{
        allowed_models = $MODELS
        default_model  = $MODELS[0]
        embed_model    = $EMBED_MODEL
        ollama_base    = "http://localhost:11434"
        port           = 8780
        host           = "127.0.0.1"
    }
    Write-Utf8NoBom $CONFIG_FILE ($cfg | ConvertTo-Json)
    Write-OK "config.json erstellt"
} else {
    Write-OK "config.json bereits vorhanden"
}

# ── 6b. Funktionsauswahl (optionale Tabs) + lokal/API ──────────────────────────

Write-Step "Funktionsauswahl (optionale Tabs)..."
Write-Host "  Jeweils [J/N]. Diese Tabs sind beim Erststart sonst ausgeblendet." -ForegroundColor DarkGray
$optTabs = [ordered]@{
    rag='Wissensdatenbanken (RAG)'; ide='Code-IDE'; mathe='Mathe'; medizin='Medizin';
    mail='Mail'; logs='Logs'; diranalyse='Verzeichnis-Analyse';
    morph='Morphologischer Kasten'; jury='Jury'
}
$hidden = @()
foreach ($t in $optTabs.Keys) {
    $a = Read-Host ("  {0} aktivieren? [J/N]" -f $optTabs[$t])
    if ($a -notmatch '^[JjYy]') { $hidden += $t }
}
$apiAns = Read-Host "Externe KI-Anbieter (API, z. B. OpenRouter) zusaetzlich zu lokal nutzen? [J/N]"
$enableApi = if ($apiAns -match '^[JjYy]') { "true" } else { "false" }

# Robust per venv-Python in config.json schreiben (vermeidet PS-JSON-Eigenheiten)
$env:HIDDEN_JSON = '[' + (($hidden | ForEach-Object { '"' + $_ + '"' }) -join ',') + ']'
$env:ENABLE_API  = $enableApi
$pyMerge = @"
import json, os, pathlib
p = pathlib.Path('config.json')
cfg = {}
if p.exists():
    try: cfg = json.loads(p.read_text(encoding='utf-8'))
    except Exception: cfg = {}
cfg['hidden_tabs_default'] = json.loads(os.environ['HIDDEN_JSON'])
cfg['enable_api'] = os.environ['ENABLE_API'] == 'true'
p.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding='utf-8')
print('  ok: hidden_tabs_default=%s enable_api=%s' % (cfg['hidden_tabs_default'], cfg['enable_api']))
"@
if (Test-Path $pyVenv) { $pyMerge | & $pyVenv - } else { $pyMerge | & $pythonCmd - }
Write-OK "Funktionsauswahl gespeichert"

# ── 7. Desktop-Verknüpfung ────────────────────────────────────────────────────

Write-Step "Desktop-Verknüpfung erstellen..."
try {
    $desktop = [Environment]::GetFolderPath("Desktop")
    $wsh = New-Object -ComObject WScript.Shell
    $sc  = $wsh.CreateShortcut("$desktop\AI_Framework_Thomas.lnk")
    $sc.TargetPath       = "$APP_DIR\start.bat"
    $sc.WorkingDirectory = $APP_DIR
    $sc.Description      = "AI_Framework_Thomas — Lokaler KI-Assistent"
    $iconPath = Join-Path $APP_DIR "bilder\icon.ico"
    if (Test-Path $iconPath) { $sc.IconLocation = "$iconPath,0" } else { $sc.IconLocation = "shell32.dll,13" }
    $sc.Save()
    Write-OK "Verknüpfung auf Desktop"
} catch {
    Write-Warn "Desktop-Verknüpfung konnte nicht erstellt werden (kein kritischer Fehler)"
}

# ── Fertig ────────────────────────────────────────────────────────────────────

Write-Host ""
Write-Host "  ╔══════════════════════════════════════════╗" -ForegroundColor Green
Write-Host "  ║       Installation abgeschlossen!       ║" -ForegroundColor Green
Write-Host "  ╚══════════════════════════════════════════╝" -ForegroundColor Green
Write-Host ""
Write-Host "  Starten:  Doppelklick auf start.bat oder Desktop-Verknüpfung" -ForegroundColor White
Write-Host "  URL:      http://localhost:8780" -ForegroundColor Cyan
Write-Host "  Server:   start_server.bat  (mehrere Nutzer, Port offen)" -ForegroundColor Gray
Write-Host ""

$ans = Read-Host "AI_Framework_Thomas jetzt starten? [J/N]"
if ($ans -match "^[JjYy]") {
    Set-Location $APP_DIR
    & "$APP_DIR\start.bat"
}
