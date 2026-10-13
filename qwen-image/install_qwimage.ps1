<#
.SYNOPSIS
Installiert Qwen-Image-2.1 (diffusers) in eine eigene venv im qwen-image-Ordner.
.BODY
Legt qwen-image/venv an und installiert PyTorch (CUDA 12.4) + diffusers (mit
QwenImagePipeline), transformers, accelerate, safetensors, numpy, Pillow,
huggingface_hub.

Die Modellgewichte (~15-20 GB) werden NICHT hier heruntergeladen, sondern beim
ersten bild.bat-Lauf automatisch in den Hugging-Face-Cache.

OPTIONAL - Hugging-Face-Token (schnellere Downloads):
  Ohne Token laedt der Modell-Download langsamer und gedrosselt (rate-limitiert).
  Ein kostenloser Read-Token (https://huggingface.co/settings/tokens) beschleunigt
  den einmaligen Download deutlich. Drei Wege, alle optional:
    1) -HfToken "hf_xxx"   (dieser Parameter)
    2) Umgebungsvariable   $env:HF_TOKEN = "hf_xxx"
    3) Datei               qwen-image\hf_token.txt   (eine Zeile)
  Der Token ist ein Geheimnis: per .gitignore ausgeschlossen, nie ausgegeben.
  Der Token ist reine Zugangs-Konfiguration, keine neue Abhaengigkeit (MIT-konform).

Aufruf:
  .\install_qwimage.ps1                 # mit CUDA-GPU (empfohlen)
  .\install_qwimage.ps1 -Cpu            # fuer Rechner ohne CUDA-GPU
  .\install_qwimage.ps1 -HfToken "hf_xxx"
#>
param(
    [switch]$Cpu,           # explizit CPU-Variante erzwingen (auch wenn eine GPU vorliegt)
    [string]$HfToken = ""   # optional: Hugging-Face-Token
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root

Write-Host "=== Qwen-Image-2.1 Installation (diffusers) ===" -ForegroundColor Cyan

# 1) Python 3.10-3.12
$py = $null
foreach ($cand in @("py -3.12", "py -3.11", "py -3.10", "py -3", "python", "python3")) {
    $parts = $cand -split ' '
    $exe = $parts[0]
    if (Get-Command $exe -ErrorAction SilentlyContinue) { $py = $cand; break }
}
if (-not $py) { throw "Kein Python gefunden (Python 3.10-3.12 erforderlich)." }

# 2) venv
if (-not (Test-Path ".\venv")) {
    Write-Host "Erstelle venv ..."
    & $py -m venv venv
}
$python = ".\venv\Scripts\python.exe"

# 3) Optionalen HF-Token setzen (Parameter > env).
if ($HfToken) { $env:HF_TOKEN = $HfToken.Trim(); Write-Host "Nutze HF-Token aus Parameter." }
elseif ($env:HF_TOKEN) { Write-Host "Nutze HF-Token aus Umgebung (HF_TOKEN)." }
elseif (Test-Path ".\hf_token.txt") { Write-Host "Nutze HF-Token aus hf_token.txt." }
else { Write-Host "Kein HF-Token — Modell-Download beim ersten Lauf eventuell gedrosselt." -ForegroundColor DarkYellow }

Write-Host "Aktualisiere pip ..."
& $python -m pip install --upgrade pip --quiet

Write-Host "Installiere PyTorch (CUDA 12.4) ..."
& $python -m pip install torch torchvision --index-url https://download.pytorch.org/whl/cu124
if ($LASTEXITCODE -ne 0) {
    Write-Host "  CUDA-Paket nicht erhaeltlich — versuche CPU-Variante ..."
    & $python -m pip install torch torchvision
}

if ($Cpu) { Write-Host "(-Cpu angegeben: CPU-Betrieb)" }
Write-Host "Installiere diffusers & Co ..."
& $python -m pip install "diffusers>=0.34.0" transformers accelerate safetensors "numpy<2.3" Pillow huggingface_hub

if ($LASTEXITCODE -ne 0) { throw "diffusers/Abhaengigkeiten konnten nicht installiert werden." }

Write-Host ""
Write-Host "=== Fertig ===" -ForegroundColor Green
Write-Host 'Test:   .\bild.bat "ein blauer Van auf einem Parkplatz, Fotorealistisch"'
Write-Host 'Server: .\sd_server.bat   (A1111-kompatibel, fuer das AI-Framework, Port 7861)'
Write-Host "Der erste Lauf laedt ~15-20 GB Modellgewichte herunter (einmalig)."
Write-Host 'Prueft: & $python -c "from diffusers import QwenImagePipeline; print(chr(9988))"'
