# install_zvideo.ps1 - Wan Videogenerierung - lokale Installation (Windows)
#
# Legt eine eigene virtuelle Umgebung (venv) im Ordner z-video an und installiert
# PyTorch (CUDA 12.4, passend fuer RTX 3090 und neuer) sowie diffusers (Quellcode)
# mit den noetigen Bibliotheken. Die Modellgewichte (mehrere GB; das 14B-Modell
# ~30-70 GB) werden NICHT hier, sondern beim ersten Videolauf automatisch von
# Hugging Face geladen.
#
# Aufruf:  .\install_zvideo.ps1                      (GPU/CUDA)
#          .\install_zvideo.ps1 -Cpu                 (ohne CUDA, nur CPU - sehr langsam)
#          .\install_zvideo.ps1 -HfToken "hf_xxx"    (optionaler Hugging-Face-Token)
param([switch]$Cpu, [string]$HfToken = "")
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root
Write-Host "=== Wan Videogenerierung - lokale Installation (Windows) ==="

if (-not (Test-Path "$root\venv")) {
  Write-Host "Erstelle virtuelle Umgebung (venv)..."
  $ok = $false
  try { & py -3.12 -m venv "$root\venv"; if ($?) { $ok = $true } } catch {}
  if (-not $ok) { try { & py -3 -m venv "$root\venv"; if ($?) { $ok = $true } } catch {} }
  if (-not $ok) { try { & python -m venv "$root\venv"; if ($?) { $ok = $true } } catch {} }
  if (-not $ok) {
    Write-Error "Python 3 nicht gefunden. Bitte Python 3.10-3.12 installieren (https://www.python.org)."
    exit 1
  }
}
$pyexe = Join-Path $root "venv\Scripts\python.exe"

Write-Host "Aktualisiere pip/setuptools/wheel..."
& $pyexe -m pip install --upgrade pip setuptools wheel

if ($Cpu) {
  Write-Host "Installiere PyTorch (CPU-Variante)..."
  & $pyexe -m pip install torch torchvision
} else {
  Write-Host "Installiere PyTorch (CUDA 12.4)..."
  & $pyexe -m pip install torch torchvision --index-url https://download.pytorch.org/whl/cu124
}
if (-not $?) { Write-Error "PyTorch-Installation fehlgeschlagen."; exit 1 }

Write-Host "Installiere diffusers (Quellcode) + Bibliotheken..."
& $pyexe -m pip install -r (Join-Path $root "requirements.txt")
if (-not $?) { Write-Error "Bibliotheks-Installation fehlgeschlagen."; exit 1 }

# --- optionaler Hugging-Face-Token ------------------------------------------
$tokenFile = Join-Path $root "hf_token.txt"
if ([string]::IsNullOrWhiteSpace($HfToken) -and [Environment]::UserInteractive -and -not (Test-Path $tokenFile)) {
  Write-Host ""
  Write-Host "Optional: Hugging-Face-Token fuer schnellere Downloads (https://huggingface.co/settings/tokens)."
  $HfToken = Read-Host "HF-Token (leer lassen zum Ueberspringen)"
}
if (-not [string]::IsNullOrWhiteSpace($HfToken)) {
  Set-Content -Path $tokenFile -Value $HfToken.Trim() -Encoding utf8 -NoNewline
  Write-Host "HF-Token gespeichert in hf_token.txt (lokal, nicht im Repo)."
}

& $pyexe -c "import torch; print('OK torch', torch.__version__, 'CUDA', torch.cuda.is_available())"

Write-Host ""
Write-Host "=== Fertig ==="
Write-Host "Server starten (das Framework kann ihn auch selbst starten):"
Write-Host "  .\video_server.bat"
Write-Host "Danach im Framework-Profil unter Videoerzeugung 'Lokal - Wan' + URL http://127.0.0.1:7870 waehlen."
