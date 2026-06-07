# gen_cert.ps1 — selbstsigniertes TLS-Zertifikat für den HTTPS-/PWA-Betrieb im
# Heimnetz (Handy <-> Rechner). Windows-Pendant zu gen_cert.sh.
#
# Warum HTTPS? Ein Service Worker (= "App installieren") laeuft nur in einem
# secure context: HTTPS oder localhost. Ueber http://<LAN-IP>:8780 registriert
# das Handy keinen SW -> die Oberflaeche laeuft im Browser, ist aber nicht
# installierbar. Mit diesem Zertifikat geht beides.
#
# Benoetigt openssl (z. B. aus "Git for Windows" oder der portablen Bundle-Umgebung).
#
# Aufruf:
#   .\scripts\gen_cert.ps1
#   .\scripts\gen_cert.ps1 192.168.178.50 pc.fritz.box
#   $env:CERT_DAYS=365; .\scripts\gen_cert.ps1
$ErrorActionPreference = 'Stop'
Set-Location (Join-Path $PSScriptRoot '..')   # Projektwurzel

if (-not (Get-Command openssl -ErrorAction SilentlyContinue)) {
  Write-Error "openssl nicht gefunden. Installiere 'Git for Windows' (enthaelt openssl) oder OpenSSL und fuehre das Skript erneut aus."
  exit 1
}

$CertDir = 'certs'
$Days = if ($env:CERT_DAYS) { [int]$env:CERT_DAYS } else { 825 }
$Port = if ($env:AI_PORT) { $env:AI_PORT } else { '8780' }
New-Item -ItemType Directory -Force -Path $CertDir | Out-Null

# ── SubjectAltName-Liste ────────────────────────────────────────────────────
$sans = [System.Collections.Generic.List[string]]::new()
$sans.Add('DNS:localhost'); $sans.Add('IP:127.0.0.1')
$hn = [System.Net.Dns]::GetHostName()
if ($hn) { $sans.Add("DNS:$hn"); $sans.Add("DNS:$hn.fritz.box"); $sans.Add("DNS:$hn.local") }

$lanIps = @()
try {
  Get-NetIPAddress -AddressFamily IPv4 -ErrorAction Stop |
    Where-Object { $_.IPAddress -notmatch '^(127\.|169\.254\.)' } |
    ForEach-Object { $sans.Add("IP:$($_.IPAddress)"); $lanIps += $_.IPAddress }
} catch {
  # Fallback ueber .NET, falls Get-NetIPAddress fehlt
  [System.Net.Dns]::GetHostAddresses($hn) |
    Where-Object { $_.AddressFamily -eq 'InterNetwork' -and $_.IPAddressToString -notmatch '^(127\.|169\.254\.)' } |
    ForEach-Object { $sans.Add("IP:$($_.IPAddressToString)"); $lanIps += $_.IPAddressToString }
}

foreach ($extra in $args) {
  if ($extra -match '^\d+\.\d+\.\d+\.\d+$') { $sans.Add("IP:$extra"); $lanIps += $extra }
  else { $sans.Add("DNS:$extra") }
}

$sanStr = ($sans -join ',')
Write-Host "SubjectAltName: $sanStr"
Write-Host ""

& openssl req -x509 -newkey rsa:2048 -nodes `
  -keyout "$CertDir/key.pem" -out "$CertDir/cert.pem" `
  -days $Days -subj "/CN=$hn" -addext "subjectAltName=$sanStr"

Write-Host ""
Write-Host "OK  Zertifikat erstellt:"
Write-Host "    $CertDir/cert.pem   (gueltig $Days Tage)"
Write-Host "    $CertDir/key.pem    (privater Schluessel - NICHT weitergeben)"
Write-Host ""
Write-Host "-- Server mit HTTPS starten --------------------------------------"
Write-Host "  uvicorn main:app --host 0.0.0.0 --port $Port ``"
Write-Host "    --ssl-certfile $CertDir/cert.pem --ssl-keyfile $CertDir/key.pem"
Write-Host ""
Write-Host "-- Am Handy oeffnen (gleiches WLAN/FritzBox) ---------------------"
foreach ($ip in $lanIps) { Write-Host "  https://${ip}:$Port" }
if ($hn) { Write-Host "  https://$hn.fritz.box:$Port" }
Write-Host ""
Write-Host "Beim ersten Aufruf die Zertifikatswarnung bestaetigen."
