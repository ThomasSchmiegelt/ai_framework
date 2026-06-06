#Requires -Version 5.1
<#
.SYNOPSIS
    Erstellt die AI_Framework_Thomas Server-Variante für Mehrbenutzerbetrieb.
.DESCRIPTION
    Richtet AI_Framework_Thomas als Windows-Dienst ein (via NSSM).
    Unterstützt mehrere gleichzeitige Nutzer, konfigurierbare Worker-Anzahl
    und optional Basic-Auth sowie zusätzliche LLM-Modelle.
#>

$ErrorActionPreference = "Stop"

$APP_DIR     = $PSScriptRoot
$SERVER_DIR  = Join-Path (Split-Path $APP_DIR -Parent) "AI_Framework_Thomas_Server"
$DATE_STAMP  = Get-Date -Format "yyyyMMdd"
$BASE_MODELS = @("ministral-3:3b", "gemma4:e2b")   # Standardmodelle; weitere unten abfragbar
$EMBED_MODEL = "nomic-embed-text"   # RAG-Embeddings
$NSSM_URL    = "https://nssm.cc/release/nssm-2.24.zip"

function Write-Step  { param($t) Write-Host "`n[►] $t" -ForegroundColor Cyan }
function Write-OK    { param($t) Write-Host "    [✓] $t" -ForegroundColor Green }
function Write-Warn  { param($t) Write-Host "    [!] $t" -ForegroundColor Yellow }
function Write-Fail  { param($t) Write-Host "`n[✗] $t" -ForegroundColor Red; Read-Host "Enter"; exit 1 }
# Schreibt Text als UTF-8 OHNE BOM. Windows PowerShell 5.1 setzt bei
# Out-File/Set-Content -Encoding UTF8 ein BOM voran — das verfaelscht config.json
# (json.loads scheitert am fuehrenden ﻿) und bricht .bat-Dateien (cmd.exe
# vertraegt kein BOM vor '@echo off'). Daher hier durchgaengig ohne BOM schreiben.
function Write-Utf8NoBom { param($Path, $Text)
    [System.IO.File]::WriteAllText($Path, $Text, (New-Object System.Text.UTF8Encoding($false)))
}

Clear-Host
Write-Host ""
Write-Host "  ╔═══════════════════════════════════════════════╗" -ForegroundColor DarkMagenta
Write-Host "  ║    🤖  AI_Framework_Thomas  —  Server Bundle Creator     ║" -ForegroundColor DarkMagenta
Write-Host "  ╚═══════════════════════════════════════════════╝" -ForegroundColor DarkMagenta
Write-Host ""
Write-Host "  Ausgabe: $SERVER_DIR" -ForegroundColor Gray
Write-Host ""

# ── Konfiguration abfragen ─────────────────────────────────────────────────────

$PORT    = Read-Host "Port [8780]"; if (-not $PORT) { $PORT = "8780" }
Write-Host "  Hinweis: Der VRAM-Schutz (nur EIN Modell gleichzeitig) wirkt pro Prozess." -ForegroundColor DarkYellow
Write-Host "  Auf ~6 GB VRAM: Worker = 1 lassen (siehe docs/SERVER.md)." -ForegroundColor DarkYellow
$WORKERS = Read-Host "Anzahl Worker-Prozesse [1]"; if (-not $WORKERS) { $WORKERS = "1" }
$HOST_IP = Read-Host "Bind-Adresse [0.0.0.0 = alle Interfaces]"; if (-not $HOST_IP) { $HOST_IP = "0.0.0.0" }

Write-Host ""
Write-Host "  Zusätzliche Modelle laden?" -ForegroundColor Yellow
Write-Host "  (Standard: $($BASE_MODELS -join ', '))" -ForegroundColor Gray
$extraModels = Read-Host "  Weitere Modelle (kommagetrennt, leer = nur Standard)"
$allModels = $BASE_MODELS
if ($extraModels) {
    $extras = $extraModels -split "," | ForEach-Object { $_.Trim() } | Where-Object { $_ }
    $allModels = $BASE_MODELS + $extras
}

$useAuth = Read-Host "`nBasic-Auth aktivieren? [J/N]"
$authUser = ""
$authPass = ""
if ($useAuth -match "^[JjYy]") {
    $authUser = Read-Host "Benutzername"
    $authPass = Read-Host "Passwort" -AsSecureString
    $authPass = [Runtime.InteropServices.Marshal]::PtrToStringAuto(
        [Runtime.InteropServices.Marshal]::SecureStringToBSTR($authPass))
}

# ── App-Dateien kopieren ────────────────────────────────────────────────────────

Write-Step "Server-Verzeichnis einrichten..."
if (Test-Path $SERVER_DIR) {
    $overwrite = Read-Host "  $SERVER_DIR existiert bereits. Überschreiben? [J/N]"
    if ($overwrite -notmatch "^[JjYy]") { exit 0 }
    Remove-Item $SERVER_DIR -Recurse -Force
}

robocopy $APP_DIR $SERVER_DIR /E `
    /XD "venv" "__pycache__" ".git" ".claude" "AI_Framework_Thomas_Portable*" "AI_Framework_Thomas_Server" `
    /XF "*.pyc" "server.log" "mail.json" "api_providers.json" /NFL /NDL /NJH /NJS | Out-Null

Write-OK "Dateien kopiert"

# ── venv in Server-Verzeichnis ────────────────────────────────────────────────

Write-Step "Python venv für Server erstellen..."
$pyCmdObj = Get-Command "python" -ErrorAction SilentlyContinue
$pyCmd = if ($pyCmdObj) { $pyCmdObj.Source } else { "python" }
& $pyCmd -m venv "$SERVER_DIR\venv"
& "$SERVER_DIR\venv\Scripts\pip.exe" install --upgrade pip --quiet
& "$SERVER_DIR\venv\Scripts\pip.exe" install -r "$SERVER_DIR\requirements.txt" --quiet
Write-OK "venv eingerichtet"

# ── Datenverzeichnisse ─────────────────────────────────────────────────────────

foreach ($d in @("data\conversations","data\uploads","data\agents","data\reports","data\code","data\plans","data\dossiers","data\profile_assets")) {
    New-Item -ItemType Directory -Path "$SERVER_DIR\$d" -Force | Out-Null
}
if (Test-Path "$APP_DIR\data\agents") {
    Copy-Item "$APP_DIR\data\agents\*" "$SERVER_DIR\data\agents\" -Force -ErrorAction SilentlyContinue
}

# ── config.json für Server ─────────────────────────────────────────────────────

Write-Step "Server-Konfiguration schreiben..."
$serverConfig = [ordered]@{
    allowed_models = $allModels
    default_model  = $BASE_MODELS[0]
    embed_model    = $EMBED_MODEL
    ollama_base    = "http://localhost:11434"
    port           = [int]$PORT
    host           = $HOST_IP
    workers        = [int]$WORKERS
}
if ($authUser) {
    $serverConfig["auth"] = @{ user = $authUser; pass = $authPass }
}
Write-Utf8NoBom "$SERVER_DIR\config.json" ($serverConfig | ConvertTo-Json -Depth 3)
Write-OK "config.json gespeichert"

# ── Basic-Auth Middleware (bei Bedarf) ────────────────────────────────────────

if ($authUser) {
    Write-Step "Basic-Auth in main.py aktivieren..."
    $authMiddleware = @"

# Basic-Auth (automatisch von make_server.ps1 generiert)
import base64, json as _json
from fastapi import Request, Response
from pathlib import Path as _Path

_auth_config = _json.loads(_Path("config.json").read_text()).get("auth", {})
_AUTH_USER = _auth_config.get("user", "")
_AUTH_PASS = _auth_config.get("pass", "")

@app.middleware("http")
async def basic_auth(request: Request, call_next):
    if not _AUTH_USER:
        return await call_next(request)
    auth = request.headers.get("authorization", "")
    if auth.startswith("Basic "):
        try:
            decoded = base64.b64decode(auth[6:]).decode()
            u, p = decoded.split(":", 1)
            if u == _AUTH_USER and p == _AUTH_PASS:
                return await call_next(request)
        except Exception:
            pass
    return Response("Unauthorized", status_code=401,
                    headers={"WWW-Authenticate": 'Basic realm="AI_Framework_Thomas"'})
"@

    $mainPy = Get-Content "$SERVER_DIR\main.py" -Raw
    $mainPy = $mainPy -replace "(app = FastAPI\(title=.*?\))", "`$1`n$authMiddleware"
    Write-Utf8NoBom "$SERVER_DIR\main.py" $mainPy
    Write-OK "Basic-Auth Middleware eingefügt"
}

# ── Modelle laden ─────────────────────────────────────────────────────────────

Write-Step "Alle Modelle laden..."
$ollamaRunning = Get-Process -Name "ollama" -ErrorAction SilentlyContinue
if (-not $ollamaRunning) {
    Start-Process -FilePath "ollama" -ArgumentList "serve" -WindowStyle Hidden
    Start-Sleep 3
}
foreach ($m in ($allModels + $EMBED_MODEL)) {
    Write-Host "  ➜ $m" -ForegroundColor Gray
    & ollama pull $m
    if ($LASTEXITCODE -eq 0) { Write-OK "$m" }
    else { Write-Warn "$m fehlgeschlagen — manuell: ollama pull $m" }
}

# ── Start-Skript ──────────────────────────────────────────────────────────────

Write-Step "Start-Skripte erstellen..."

$startServer = @"
@echo off
title AI_Framework_Thomas Server
cd /d "%~dp0"

set PORT=$PORT
set HOST=$HOST_IP
set WORKERS=$WORKERS

tasklist /fi "IMAGENAME eq ollama.exe" 2>nul | find /i "ollama.exe" >nul
if errorlevel 1 (
    echo [*] Starte Ollama...
    start /min "" ollama serve
    timeout /t 4 /nobreak >nul
)

call venv\Scripts\activate.bat

echo.
echo  AI_Framework_Thomas SERVER
echo  Erreichbar unter: http://^<IP-Adresse^>:%PORT%
echo  Lokaler Test:     http://localhost:%PORT%
echo.

uvicorn main:app --host %HOST% --port %PORT% --workers %WORKERS%
"@
Write-Utf8NoBom "$SERVER_DIR\start_server.bat" $startServer

# Windows-Dienst Installer (NSSM)
Write-Step "NSSM Windows-Dienst Installer erstellen..."
$nssmTmp = "$env:TEMP\nssm.zip"
try {
    if (-not (Test-Path $nssmTmp)) {
        Invoke-WebRequest -Uri $NSSM_URL -OutFile $nssmTmp -UseBasicParsing
    }
    Expand-Archive -Path $nssmTmp -DestinationPath "$SERVER_DIR\_nssm_tmp" -Force
    $nssmExe = Get-ChildItem "$SERVER_DIR\_nssm_tmp" -Filter "nssm.exe" -Recurse |
        Where-Object { $_.FullName -match "win64" } | Select-Object -First 1
    if ($nssmExe) {
        New-Item -ItemType Directory -Path "$SERVER_DIR\tools" -Force | Out-Null
        Copy-Item $nssmExe.FullName "$SERVER_DIR\tools\nssm.exe"
        Remove-Item "$SERVER_DIR\_nssm_tmp" -Recurse -Force
        Write-OK "NSSM bereit: tools\nssm.exe"
    }
} catch {
    Write-Warn "NSSM konnte nicht heruntergeladen werden — Dienst-Installation manuell durchführen"
}

$installService = @"
@echo off
:: Als Administrator ausfuehren!
cd /d "%~dp0"
set SERVICE_NAME=AI_Framework_Thomas_Server
set APP_DIR=%~dp0

echo Installiere Windows-Dienst '%SERVICE_NAME%'...
tools\nssm.exe install %SERVICE_NAME% "%APP_DIR%venv\Scripts\uvicorn.exe"
tools\nssm.exe set %SERVICE_NAME% AppParameters "main:app --host $HOST_IP --port $PORT --workers $WORKERS"
tools\nssm.exe set %SERVICE_NAME% AppDirectory "%APP_DIR%"
tools\nssm.exe set %SERVICE_NAME% DisplayName "AI_Framework_Thomas Server"
tools\nssm.exe set %SERVICE_NAME% Description "AI_Framework_Thomas - Lokaler KI-Assistent Server"
tools\nssm.exe set %SERVICE_NAME% Start SERVICE_AUTO_START
tools\nssm.exe set %SERVICE_NAME% AppStdout "%APP_DIR%logs\service.log"
tools\nssm.exe set %SERVICE_NAME% AppStderr "%APP_DIR%logs\error.log"

mkdir "%APP_DIR%logs" 2>nul

net start %SERVICE_NAME%
echo.
echo Dienst '%SERVICE_NAME%' installiert und gestartet.
echo Verwalten: sc stop/start %SERVICE_NAME%
echo Entfernen: tools\nssm.exe remove %SERVICE_NAME% confirm
pause
"@
Write-Utf8NoBom "$SERVER_DIR\install_service.bat" $installService

# Firewall-Regel Skript
$firewallScript = @"
@echo off
:: Als Administrator ausfuehren!
echo Firewall-Regel fuer AI_Framework_Thomas Port $PORT hinzufuegen...
netsh advfirewall firewall add rule name="AI_Framework_Thomas Server" dir=in action=allow protocol=TCP localport=$PORT
echo Fertig. AI_Framework_Thomas ist jetzt im Netzwerk erreichbar.
pause
"@
Write-Utf8NoBom "$SERVER_DIR\open_firewall.bat" $firewallScript

Write-OK "Skripte erstellt"

# ── Fertig ────────────────────────────────────────────────────────────────────

Write-Host ""
Write-Host "  ╔══════════════════════════════════════════════╗" -ForegroundColor Green
Write-Host "  ║     Server-Bundle fertig!                    ║" -ForegroundColor Green
Write-Host "  ╚══════════════════════════════════════════════╝" -ForegroundColor Green
Write-Host ""
Write-Host "  Verzeichnis:    $SERVER_DIR" -ForegroundColor White
Write-Host "  Manuell Start:  start_server.bat" -ForegroundColor Cyan
Write-Host "  Als Dienst:     install_service.bat  (als Admin!)" -ForegroundColor Cyan
Write-Host "  Firewall:       open_firewall.bat     (als Admin!)" -ForegroundColor Cyan
Write-Host ""
Write-Host "  Konfiguration: $SERVER_DIR\config.json" -ForegroundColor Gray
Write-Host "  Modelle:       $($allModels -join ', ')" -ForegroundColor Gray
if ($authUser) {
    Write-Host "  Basic-Auth:    $authUser / ***" -ForegroundColor Gray
}
Write-Host ""
