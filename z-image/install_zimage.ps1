# install_zimage.ps1 — Z-Image-Turbo · lokale Installation (Windows)
#
# Legt eine eigene virtuelle Umgebung (venv) im Ordner z-image an und installiert
# PyTorch (CUDA 12.4, passend für RTX 3090 & neuer) sowie diffusers (Quellcode)
# mit den nötigen Bibliotheken. Die ~20 GB Modellgewichte werden NICHT hier,
# sondern beim ersten Bildlauf automatisch von Hugging Face geladen.
#
# Aufruf:  .\install_zimage.ps1                      (GPU/CUDA)
#          .\install_zimage.ps1 -Cpu                 (ohne CUDA, nur CPU – sehr langsam)
#          .\install_zimage.ps1 -HfToken "hf_xxx"    (optionaler Hugging-Face-Token,
#                                                     beschleunigt den Modell-Download)
param([switch]$Cpu, [string]$HfToken = "")
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root
Write-Host "=== Z-Image-Turbo — lokale Installation (Windows) ==="

# --- virtuelle Umgebung anlegen ---------------------------------------------
if (-not (Test-Path "$root\venv")) {
  Write-Host "Erstelle virtuelle Umgebung (venv)..."
  $ok = $false
  try { & py -3.12 -m venv "$root\venv"; if ($?) { $ok = $true } } catch {}
  if (-not $ok) { try { & py -3 -m venv "$root\venv"; if ($?) { $ok = $true } } catch {} }
  if (-not $ok) { try { & python -m venv "$root\venv"; if ($?) { $ok = $true } } catch {} }
  if (-not $ok) {
    Write-Error "Python 3 nicht gefunden. Bitte Python 3.10–3.12 installieren (https://www.python.org)."
    exit 1
  }
}
$pyexe = Join-Path $root "venv\Scripts\python.exe"

Write-Host "Aktualisiere pip/setuptools/wheel..."
& $pyexe -m pip install --upgrade pip setuptools wheel

# --- PyTorch ----------------------------------------------------------------
if ($Cpu) {
  Write-Host "Installiere PyTorch (CPU-Variante)..."
  & $pyexe -m pip install torch torchvision
} else {
  Write-Host "Installiere PyTorch (CUDA 12.4)..."
  & $pyexe -m pip install torch torchvision --index-url https://download.pytorch.org/whl/cu124
}
if (-not $?) { Write-Error "PyTorch-Installation fehlgeschlagen."; exit 1 }

# --- diffusers (Quellcode) + Bibliotheken -----------------------------------
Write-Host "Installiere diffusers (Quellcode) + Bibliotheken..."
& $pyexe -m pip install -r (Join-Path $root "requirements.txt")
if (-not $?) { Write-Error "Bibliotheks-Installation fehlgeschlagen."; exit 1 }

# --- optionaler Hugging-Face-Token ------------------------------------------
# Beschleunigt den einmaligen Modell-Download und hebt Rate-Limits an. Rein
# optional: ohne Token laedt das Modell trotzdem (nur langsamer/gedrosselt).
# Der Token ist ein Geheimnis -> lokal in hf_token.txt (per .gitignore NICHT im
# Repo), niemals ausgeben.
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

# --- Kurztest der Umgebung --------------------------------------------------
& $pyexe -c "import torch; from diffusers import ZImagePipeline; print('OK  torch', torch.__version__, '| CUDA', torch.cuda.is_available())"

Write-Host ""
Write-Host "=== Fertig ==="
Write-Host "Erster Testlauf (lädt beim ersten Mal ~20 GB Modellgewichte nach %USERPROFILE%\.cache\huggingface):"
Write-Host "  .\bild.bat `"ein roter Sportwagen im Sonnenuntergang, Fotorealismus`""
