#Requires -Version 5.1
<#
.SYNOPSIS
    Schnürt eine saubere Installations-ZIP (ai_framework_thomas.zip) der AI_Framework_Thomas-Quellen.
.DESCRIPTION
    Kopiert den App-Quellbaum in ein Staging-Verzeichnis OHNE venv, Caches,
    .claude (Claude-Memory), .git, Laufzeitdaten und server.log. Nimmt die
    Standard-Agenten, eine leere Datenstruktur (.gitkeep), das 100-Aufgaben-
    Beispielprojekt und den samples-Ordner mit. Ergebnis: <Desktop>\ai_framework_thomas.zip
#>
$ErrorActionPreference = "Stop"

$APP_DIR  = Split-Path $PSScriptRoot -Parent
$STAGEROOT = Join-Path $env:TEMP "ai_framework_thomas_stage"
$STAGE    = Join-Path $STAGEROOT "AI_Framework_Thomas"
$DESKTOP  = [Environment]::GetFolderPath("Desktop")
$ZIP      = Join-Path $DESKTOP "ai_framework_thomas.zip"
$DEMO_PLAN = "beispiel_lokale_ki_im_unternehmen_100_au_f9e83970.json"

Write-Host "[*] Staging vorbereiten: $STAGE" -ForegroundColor Cyan
if (Test-Path $STAGEROOT) { Remove-Item $STAGEROOT -Recurse -Force }
New-Item -ItemType Directory -Path $STAGE -Force | Out-Null

# App-Baum kopieren – ohne venv, Caches, .claude, .git, KOMPLETTES data, Bundles
robocopy $APP_DIR $STAGE /E `
    /XD "venv" "__pycache__" ".claude" ".git" "data" "AI_Framework_Thomas_Portable" "AI_Framework_Thomas_Server" `
    /XF "server.log" "*.pyc" "*.bak" "*.tmp" /NFL /NDL /NJH /NJS | Out-Null

# Datenstruktur frisch aufbauen (nur Defaults + Beispiel, keine Nutzerdaten)
foreach ($d in @("conversations","uploads","reports","code","plans","dossiers","agents","profile_assets")) {
    New-Item -ItemType Directory -Path "$STAGE\data\$d" -Force | Out-Null
    Set-Content -Path "$STAGE\data\$d\.gitkeep" -Value "" -NoNewline -Encoding ascii
}
# Standard-Agenten mitnehmen
if (Test-Path "$APP_DIR\data\agents") {
    Copy-Item "$APP_DIR\data\agents\*" "$STAGE\data\agents\" -Force -ErrorAction SilentlyContinue
}
# 100-Aufgaben-Beispielprojekt mitnehmen (zum sofortigen Testen im Planer)
if (Test-Path "$APP_DIR\data\plans\$DEMO_PLAN") {
    Copy-Item "$APP_DIR\data\plans\$DEMO_PLAN" "$STAGE\data\plans\$DEMO_PLAN" -Force
    Write-Host "[*] Beispielprojekt eingepackt: $DEMO_PLAN" -ForegroundColor Gray
}

# Frische, neutrale config.json sicherstellen (ohne evtl. lokale Anpassungen)
$cfg = [ordered]@{
    allowed_models = @("ministral-3:3b")
    default_model  = "ministral-3:3b"
    embed_model    = "nomic-embed-text"
    ollama_base    = "http://localhost:11434"
    port           = 8780
    host           = "127.0.0.1"
}
[System.IO.File]::WriteAllText("$STAGE\config.json",
    ($cfg | ConvertTo-Json), (New-Object System.Text.UTF8Encoding($false)))

# ZIP schreiben
Write-Host "[*] Komprimiere nach $ZIP" -ForegroundColor Cyan
if (Test-Path $ZIP) { Remove-Item $ZIP -Force }
Compress-Archive -Path $STAGE -DestinationPath $ZIP -CompressionLevel Optimal

$size = (Get-Item $ZIP).Length / 1MB
$files = (Get-ChildItem $STAGE -Recurse -File).Count
Write-Host ("[OK] ai_framework_thomas.zip erstellt: {0:N1} MB, {1} Dateien -> {2}" -f $size, $files, $ZIP) -ForegroundColor Green

# Aufräumen
Remove-Item $STAGEROOT -Recurse -Force
