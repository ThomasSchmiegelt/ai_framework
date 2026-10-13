# install_pi.ps1 — Pi (https://pi.dev) installieren und an dieses Framework binden
#
# Was dieses Skript tut:
#   1. Prüft, ob Pi schon installiert ist (Befehl `pi`).
#   2. Wenn nicht: ruft den OFFIZIELLEN Pi-Installer auf — erst nach Rückfrage und
#      mit sichtbarem Befehl. Es wird nichts im Hintergrund nachgeladen.
#   3. Legt die Skill-Beschreibung (skills/framework-cli.md) im gewählten
#      Pi-Projekt unter .agents/skills/ ab.
#   4. Setzt die Zugriffsstufe pi_agent_access in der config.json des Frameworks.
#
# Was es NICHT tut: Pi konfigurieren (Modell/Anbieter) — das macht `pi` selbst
# über `/login` bzw. seine eigene Konfiguration. Siehe README.md.
#
# Aufruf:
#   .\install_pi.ps1                               # interaktiv
#   .\install_pi.ps1 -Projekt C:\pfad\zum\projekt -Zugriff read
#   .\install_pi.ps1 -NurSkill                     # Pi nicht anfassen, nur verdrahten

param(
    [string]$Projekt = "",
    [ValidateSet("off", "read", "write", "")]
    [string]$Zugriff = "",
    [switch]$NurSkill
)

$ErrorActionPreference = "Stop"
$PI_DIR = $PSScriptRoot
$APP_DIR = Split-Path -Parent $PI_DIR

function Write-Step($t) { Write-Host "`n>> $t" -ForegroundColor Cyan }
function Write-OK($t)   { Write-Host "   ok: $t" -ForegroundColor Green }
function Write-Warn($t) { Write-Host "   ! $t" -ForegroundColor Yellow }

Write-Host "=== Pi-Anbindung für AI_Framework_Thomas ===" -ForegroundColor White
Write-Host "Framework: $APP_DIR" -ForegroundColor DarkGray

# ── 1. Pi vorhanden? ──────────────────────────────────────────────────────────

Write-Step "Pi suchen..."
$piCmd = Get-Command pi -ErrorAction SilentlyContinue
if ($piCmd) {
    Write-OK "Pi gefunden: $($piCmd.Source)"
    try { $v = & pi --version 2>$null; if ($v) { Write-Host "   Version: $v" -ForegroundColor DarkGray } } catch {}
} elseif ($NurSkill) {
    Write-Warn "Pi nicht gefunden - mit -NurSkill wird es nicht installiert."
} else {
    Write-Warn "Pi ist nicht installiert."
    Write-Host ""
    Write-Host "   Pi braucht Node.js 22.19 oder neuer. Das Framework selbst braucht KEIN" -ForegroundColor DarkGray
    Write-Host "   Node - es kommt nur fuer Pi dazu." -ForegroundColor DarkGray
    $node = Get-Command node -ErrorAction SilentlyContinue
    if ($node) {
        $nv = (& node --version) -replace '^v', ''
        Write-Host "   Node gefunden: v$nv" -ForegroundColor DarkGray
        $major = [int]($nv -split '\.')[0]
        if ($major -lt 22) { Write-Warn "Node v$nv ist zu alt (>= 22.19 noetig)." }
    } else {
        Write-Host "   Node nicht gefunden - der Pi-Installer bringt seine Laufzeit selbst mit." -ForegroundColor DarkGray
    }
    Write-Host ""
    Write-Host "   Folgender Befehl wuerde ausgefuehrt (offizieller Installer von pi.dev):" -ForegroundColor White
    Write-Host '     powershell -c "irm https://pi.dev/install.ps1 | iex"' -ForegroundColor Yellow
    Write-Host ""
    $ans = Read-Host "   Jetzt ausfuehren? [J/N]"
    if ($ans -match '^[JjYy]') {
        Write-Step "Pi installieren..."
        powershell -NoProfile -Command "irm https://pi.dev/install.ps1 | iex"
        $piCmd = Get-Command pi -ErrorAction SilentlyContinue
        if ($piCmd) {
            Write-OK "Pi installiert: $($piCmd.Source)"
        } else {
            Write-Warn "Pi nach der Installation nicht im PATH - evtl. Terminal neu oeffnen."
        }
    } else {
        Write-Host "   Uebersprungen. Alternativen:" -ForegroundColor DarkGray
        Write-Host "     npm install -g --ignore-scripts @earendil-works/pi-coding-agent" -ForegroundColor DarkGray
        Write-Host "     nix run github:earendil-works/pi/stable" -ForegroundColor DarkGray
    }
}

# ── 2. Skill-Beschreibung ins Pi-Projekt legen ────────────────────────────────

Write-Step "Skill-Beschreibung verdrahten..."
$skill = Join-Path $PI_DIR "skills\framework-cli.md"
if (-not (Test-Path $skill)) {
    Write-Warn "skills\framework-cli.md fehlt - Repo unvollstaendig?"
} else {
    if (-not $Projekt) {
        Write-Host "   In welchem Pi-Projekt soll der Agent arbeiten?" -ForegroundColor DarkGray
        Write-Host "   (Ordner, in dem du 'pi' startest. Leer = ueberspringen.)" -ForegroundColor DarkGray
        $Projekt = Read-Host "   Projektordner"
    }
    if (-not $Projekt) {
        Write-Warn "Uebersprungen. Spaeter manuell kopieren:"
        Write-Host "     Copy-Item `"$skill`" <projekt>\.agents\skills\" -ForegroundColor DarkGray
    } elseif (-not (Test-Path $Projekt)) {
        Write-Warn "Ordner '$Projekt' existiert nicht - nichts kopiert."
    } else {
        $ziel = Join-Path $Projekt ".agents\skills"
        New-Item -ItemType Directory -Force -Path $ziel | Out-Null
        Copy-Item $skill $ziel -Force
        Write-OK "framework-cli.md -> $ziel"
        # Hinweis auf den absoluten CLI-Pfad, damit Pi ihn aus jedem Ordner findet.
        Write-Host "   Pi ruft die CLI so auf:" -ForegroundColor DarkGray
        Write-Host "     python `"$APP_DIR\pi-agent\framework_cli.py`" status" -ForegroundColor DarkGray
    }
}

# ── 3. Zugriffsstufe setzen ───────────────────────────────────────────────────

Write-Step "Zugriffsstufe setzen..."
if (-not $Zugriff) {
    Write-Host "   Was darf der Agent?" -ForegroundColor DarkGray
    Write-Host "     0 = aus   1 = nur lesen und rechnen   2 = lesen, anlegen und aendern" -ForegroundColor DarkGray
    Write-Host "   Loeschen und Versenden gibt es in KEINER Stufe." -ForegroundColor DarkGray
    $a = Read-Host "   Stufe [0/1/2]"
    $Zugriff = switch -Regex ($a) { '^1' { "read" } '^2' { "write" } default { "off" } }
}
$pyCmd = if (Test-Path "$APP_DIR\venv\Scripts\python.exe") { "$APP_DIR\venv\Scripts\python.exe" } else { "python" }
$env:PI_ACCESS = $Zugriff
$env:APP_DIR_ENV = $APP_DIR
$merge = @"
import json, os, pathlib
p = pathlib.Path(os.environ['APP_DIR_ENV']) / 'config.json'
cfg = {}
if p.exists():
    try: cfg = json.loads(p.read_text(encoding='utf-8-sig'))
    except Exception: cfg = {}
cfg['pi_agent_access'] = os.environ['PI_ACCESS']
p.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding='utf-8')
print('   ok: pi_agent_access = %s' % cfg['pi_agent_access'])
"@
$merge | & $pyCmd -

# ── 4. Probe ──────────────────────────────────────────────────────────────────

Write-Step "Probe..."
& $pyCmd "$APP_DIR\pi-agent\framework_cli.py" status
Write-Host ""
Write-Host "Fertig. Naechste Schritte:" -ForegroundColor White
Write-Host "  1. Framework starten (start.bat), falls noch nicht laufend." -ForegroundColor DarkGray
Write-Host "  2. In Pi einen Anbieter verbinden: 'pi' starten, dann '/login'." -ForegroundColor DarkGray
Write-Host "     Soll alles lokal bleiben, dort den lokalen Server eintragen -" -ForegroundColor DarkGray
Write-Host "     der Geheim-Modus des Frameworks gilt NICHT fuer Pi." -ForegroundColor DarkGray
Write-Host "  3. Verben ansehen: python `"$APP_DIR\pi-agent\framework_cli.py`" --hilfe" -ForegroundColor DarkGray
