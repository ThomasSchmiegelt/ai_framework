<#
    AI_Framework_Thomas - MSI bauen (WiX Toolset v3)
    ================================================

    Muss auf einem WINDOWS-Rechner mit installiertem WiX Toolset v3 laufen
    (candle.exe / light.exe / heat.exe im PATH oder unter -WixBin angeben).

    Ablauf:
      1. make_acmp.ps1 erzeugt den Paketordner (nur Programmcode)
      2. heat.exe erntet daraus die Dateiliste  -> AppFiles.wxs
      3. candle.exe kompiliert Product.wxs + AppFiles.wxs
      4. light.exe linkt das MSI

    Aufruf:
        .\build.ps1                       # Version aus ..\..\VERSION
        .\build.ps1 -Version 1.5.0
        .\build.ps1 -WixBin "C:\Program Files (x86)\WiX Toolset v3.14\bin"
#>

[CmdletBinding()]
param(
    [string]$Version,
    [string]$WixBin,
    [string]$OutDir
)

$ErrorActionPreference = 'Stop'
$here     = $PSScriptRoot
$repo     = (Resolve-Path (Join-Path $here '..\..')).Path
if (-not $OutDir) { $OutDir = Join-Path $here 'out' }

# --- Version bestimmen -------------------------------------------------------
if (-not $Version) {
    $vf = Join-Path $repo 'VERSION'
    if (-not (Test-Path $vf)) { throw "VERSION nicht gefunden: $vf" }
    $Version = (Get-Content $vf -TotalCount 1).Trim()
}
# MSI verlangt ein numerisches x.y.z - Suffixe wie "1.5.0-beta" abschneiden
if ($Version -notmatch '^\d+(\.\d+){0,3}$') {
    $clean = ($Version -split '[^0-9.]')[0].TrimEnd('.')
    Write-Warning "Version '$Version' ist fuer MSI ungueltig - verwende '$clean'."
    $Version = $clean
}
Write-Host "MSI-Version: $Version"

# --- WiX-Werkzeuge finden ----------------------------------------------------
function Find-WixTool([string]$name) {
    if ($WixBin) {
        $p = Join-Path $WixBin $name
        if (Test-Path $p) { return $p }
    }
    $cmd = Get-Command $name -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    foreach ($base in @(
        "${env:ProgramFiles(x86)}\WiX Toolset v3.14\bin",
        "${env:ProgramFiles(x86)}\WiX Toolset v3.11\bin")) {
        $p = Join-Path $base $name
        if (Test-Path $p) { return $p }
    }
    throw "$name nicht gefunden. WiX Toolset v3 installieren oder -WixBin angeben."
}
$heat   = Find-WixTool 'heat.exe'
$candle = Find-WixTool 'candle.exe'
$light  = Find-WixTool 'light.exe'

# --- 1) Paketordner erzeugen (nur Programmcode) ------------------------------
$payload = Join-Path $here 'payload'
if (Test-Path $payload) { Remove-Item $payload -Recurse -Force }
& (Join-Path $repo 'make_acmp.ps1') -Out $payload -Version $Version
if ($LASTEXITCODE -ne 0 -and $null -ne $LASTEXITCODE) { throw "make_acmp.ps1 fehlgeschlagen." }

# update.bat gehoert nicht ins MSI - Updates laufen dort ueber das MSI selbst
$u = Join-Path $payload 'update.bat'
if (Test-Path $u) { Remove-Item $u -Force }

New-Item -ItemType Directory -Path $OutDir -Force | Out-Null

# --- 2) Dateiliste ernten ----------------------------------------------------
$appFiles = Join-Path $here 'AppFiles.wxs'
& $heat dir $payload -cg AppFilesGroup -dr INSTALLFOLDER -gg -g1 -sfrag -srd -sreg `
        -var var.PayloadDir -out $appFiles
if ($LASTEXITCODE -ne 0) { throw "heat.exe fehlgeschlagen." }

# --- 3) kompilieren ----------------------------------------------------------
& $candle -nologo -arch x64 `
          -dProductVersion="$Version" -dPayloadDir="$payload" `
          -out "$OutDir\" (Join-Path $here 'Product.wxs') $appFiles
if ($LASTEXITCODE -ne 0) { throw "candle.exe fehlgeschlagen." }

# --- 4) linken ---------------------------------------------------------------
$msi = Join-Path $OutDir ("AI_Framework_Thomas_$Version.msi")
& $light -nologo -sice:ICE60 -out $msi "$OutDir\Product.wixobj" "$OutDir\AppFiles.wixobj"
if ($LASTEXITCODE -ne 0) { throw "light.exe fehlgeschlagen." }

Write-Host ""
Write-Host "MSI erstellt: $msi"
Write-Host ""
Write-Host "Silent-Installation fuer ACMP:"
Write-Host "  msiexec /i `"$([IO.Path]::GetFileName($msi))`" /qn /norestart /l*v `"%TEMP%\aifw_msi.log`""
Write-Host "Deinstallation:"
Write-Host "  msiexec /x `"$([IO.Path]::GetFileName($msi))`" /qn /norestart"
