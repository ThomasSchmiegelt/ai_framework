# AI_Framework_Thomas Deinstallationsscript
# Entfernt die virtuelle Umgebung und optionale Daten

$ErrorActionPreference = 'Stop'
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Definition

Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  AI_Framework_Thomas Deinstallation" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

$confirm = Read-Host "Soll AI_Framework_Thomas deinstalliert werden? (j/n)"
if ($confirm -ne 'j' -and $confirm -ne 'J') {
    Write-Host "Abgebrochen." -ForegroundColor Yellow
    exit 0
}

# ── Laufende Prozesse beenden ──────────────────────────────────────────────
Write-Host ""
Write-Host "► Beende laufende AI_Framework_Thomas-Prozesse…" -ForegroundColor Yellow
try {
    Get-Process -Name "python" -ErrorAction SilentlyContinue |
        Where-Object { $_.MainWindowTitle -match 'uvicorn|ai_framework_thomas' -or $_.CommandLine -match 'uvicorn|ai_framework_thomas' } |
        Stop-Process -Force -ErrorAction SilentlyContinue
} catch {}
# Fallback: alle Python-Prozesse mit uvicorn
try {
    $procs = Get-WmiObject Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue
    foreach ($p in $procs) {
        if ($p.CommandLine -match 'uvicorn') {
            Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue
            Write-Host "  Prozess PID $($p.ProcessId) beendet"
        }
    }
} catch {}

# ── Virtuelle Umgebung löschen ─────────────────────────────────────────────
$venvPath = Join-Path $scriptDir "venv"
if (Test-Path $venvPath) {
    Write-Host "► Lösche virtuelle Python-Umgebung (venv)…" -ForegroundColor Yellow
    Remove-Item -Recurse -Force $venvPath
    Write-Host "  ✓ venv gelöscht" -ForegroundColor Green
} else {
    Write-Host "  venv nicht gefunden (bereits entfernt?)" -ForegroundColor Gray
}

# ── Daten löschen (optional) ───────────────────────────────────────────────
$dataPath = Join-Path $scriptDir "data"
if (Test-Path $dataPath) {
    Write-Host ""
    $delData = Read-Host "Sollen die gespeicherten Daten (Gespräche, Pläne, Uploads) auch gelöscht werden? (j/n)"
    if ($delData -eq 'j' -or $delData -eq 'J') {
        Remove-Item -Recurse -Force $dataPath
        Write-Host "  ✓ Datenverzeichnis gelöscht" -ForegroundColor Green
    } else {
        Write-Host "  Daten bleiben erhalten." -ForegroundColor Gray
    }
}

# ── Desktop-Verknüpfung entfernen ──────────────────────────────────────────
try {
    $lnk = Join-Path ([Environment]::GetFolderPath("Desktop")) "AI_Framework_Thomas.lnk"
    if (Test-Path $lnk) {
        Remove-Item $lnk -Force
        Write-Host "  ✓ Desktop-Verknüpfung entfernt" -ForegroundColor Green
    }
} catch {}

# ── Windows-Dienst entfernen (nur Server-Variante) ─────────────────────────
try {
    $svc = Get-Service -Name "AI_Framework_Thomas_Server" -ErrorAction SilentlyContinue
    if ($svc) {
        Write-Host "► Entferne Windows-Dienst 'AI_Framework_Thomas_Server'…" -ForegroundColor Yellow
        $nssm = Join-Path $scriptDir "tools\nssm.exe"
        try { Stop-Service -Name "AI_Framework_Thomas_Server" -Force -ErrorAction SilentlyContinue } catch {}
        if (Test-Path $nssm) { & $nssm remove "AI_Framework_Thomas_Server" confirm | Out-Null }
        else { sc.exe delete "AI_Framework_Thomas_Server" | Out-Null }
        Write-Host "  ✓ Dienst entfernt" -ForegroundColor Green
    }
} catch {}

# ── Ollama deinstallieren (optional) ──────────────────────────────────────
Write-Host ""
$delOllama = Read-Host "Soll Ollama ebenfalls deinstalliert werden? (j/n)"
if ($delOllama -eq 'j' -or $delOllama -eq 'J') {
    Write-Host "► Deinstalliere Ollama über winget…" -ForegroundColor Yellow
    try {
        winget uninstall --id Ollama.Ollama --silent 2>&1 | Out-Null
        Write-Host "  ✓ Ollama deinstalliert" -ForegroundColor Green
    } catch {
        Write-Host "  Ollama konnte nicht automatisch deinstalliert werden." -ForegroundColor Red
        Write-Host "  Bitte manuell über Systemsteuerung → Programme entfernen." -ForegroundColor Gray
    }
}

# ── Abschluss ──────────────────────────────────────────────────────────────
Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  AI_Framework_Thomas wurde deinstalliert." -ForegroundColor Green
Write-Host "  Der Programmordner kann jetzt manuell" -ForegroundColor Gray
Write-Host "  gelöscht werden:" -ForegroundColor Gray
Write-Host "  $scriptDir" -ForegroundColor Gray
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""
Read-Host "Drücke Enter zum Beenden"
