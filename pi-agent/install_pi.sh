#!/usr/bin/env bash
# install_pi.sh — Pi (https://pi.dev) installieren und an dieses Framework binden
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
# über `/login`. Siehe README.md.
#
# Aufruf:
#   ./install_pi.sh                                  # interaktiv
#   ./install_pi.sh --projekt ~/pfad --zugriff read
#   ./install_pi.sh --nur-skill                      # Pi nicht anfassen, nur verdrahten
set -euo pipefail

PI_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="$(dirname "$PI_DIR")"

PROJEKT=""
ZUGRIFF=""
NUR_SKILL=0
while [ $# -gt 0 ]; do
  case "$1" in
    --projekt) PROJEKT="${2:-}"; shift 2 ;;
    --zugriff) ZUGRIFF="${2:-}"; shift 2 ;;
    --nur-skill) NUR_SKILL=1; shift ;;
    -h|--help) sed -n '2,20p' "$0"; exit 0 ;;
    *) echo "Unbekannte Option: $1"; exit 2 ;;
  esac
done

step() { printf '\n>> %s\n' "$1"; }
ok()   { printf '   ok: %s\n' "$1"; }
warn() { printf '   ! %s\n' "$1"; }

echo "=== Pi-Anbindung für AI_Framework_Thomas ==="
echo "Framework: $APP_DIR"

# ── 1. Pi vorhanden? ──────────────────────────────────────────────────────────

step "Pi suchen..."
if command -v pi >/dev/null 2>&1; then
  ok "Pi gefunden: $(command -v pi)"
  pi --version 2>/dev/null | head -1 | sed 's/^/   Version: /' || true
elif [ "$NUR_SKILL" = "1" ]; then
  warn "Pi nicht gefunden - mit --nur-skill wird es nicht installiert."
else
  warn "Pi ist nicht installiert."
  echo ""
  echo "   Pi braucht Node.js 22.19 oder neuer. Das Framework selbst braucht KEIN"
  echo "   Node - es kommt nur für Pi dazu."
  if command -v node >/dev/null 2>&1; then
    nv="$(node --version | sed 's/^v//')"
    echo "   Node gefunden: v$nv"
    if [ "${nv%%.*}" -lt 22 ] 2>/dev/null; then warn "Node v$nv ist zu alt (>= 22.19 nötig)."; fi
  else
    echo "   Node nicht gefunden - der Pi-Installer bringt seine Laufzeit selbst mit."
  fi
  echo ""
  echo "   Folgender Befehl würde ausgeführt (offizieller Installer von pi.dev):"
  echo "     curl -fsSL https://pi.dev/install.sh | sh"
  echo ""
  if [ -t 0 ]; then
    read -r -p "   Jetzt ausführen? [j/N] " ans || ans=""
  else
    ans=""
    warn "Nicht interaktiv - Installation übersprungen."
  fi
  case "$ans" in
    [jJyY]*)
      step "Pi installieren..."
      curl -fsSL https://pi.dev/install.sh | sh
      if command -v pi >/dev/null 2>&1; then
        ok "Pi installiert: $(command -v pi)"
      else
        warn "Pi nach der Installation nicht im PATH - evtl. Shell neu öffnen."
      fi
      ;;
    *)
      echo "   Übersprungen. Alternativen:"
      echo "     npm install -g --ignore-scripts @earendil-works/pi-coding-agent"
      echo "     nix run github:earendil-works/pi/stable"
      ;;
  esac
fi

# ── 2. Skill-Beschreibung ins Pi-Projekt legen ────────────────────────────────

step "Skill-Beschreibung verdrahten..."
SKILL="$PI_DIR/skills/framework-cli.md"
if [ ! -f "$SKILL" ]; then
  warn "skills/framework-cli.md fehlt - Repo unvollständig?"
else
  if [ -z "$PROJEKT" ] && [ -t 0 ]; then
    echo "   In welchem Pi-Projekt soll der Agent arbeiten?"
    echo "   (Ordner, in dem du 'pi' startest. Leer = überspringen.)"
    read -r -p "   Projektordner: " PROJEKT || PROJEKT=""
  fi
  if [ -z "$PROJEKT" ]; then
    warn "Übersprungen. Später manuell verlinken:"
    echo "     ln -s \"$SKILL\" <projekt>/.agents/skills/"
  elif [ ! -d "$PROJEKT" ]; then
    warn "Ordner '$PROJEKT' existiert nicht - nichts kopiert."
  else
    ZIEL="$PROJEKT/.agents/skills"
    mkdir -p "$ZIEL"
    # Symlink, damit Änderungen am Repo automatisch ankommen; Fallback Kopie.
    ln -sf "$SKILL" "$ZIEL/framework-cli.md" 2>/dev/null || cp -f "$SKILL" "$ZIEL/"
    ok "framework-cli.md -> $ZIEL"
    echo "   Pi ruft die CLI so auf:"
    echo "     python3 \"$APP_DIR/pi-agent/framework_cli.py\" status"
  fi
fi

# ── 3. Zugriffsstufe setzen ───────────────────────────────────────────────────

step "Zugriffsstufe setzen..."
if [ -z "$ZUGRIFF" ]; then
  if [ -t 0 ]; then
    echo "   Was darf der Agent?"
    echo "     0 = aus   1 = nur lesen und rechnen   2 = lesen, anlegen und ändern"
    echo "   Löschen und Versenden gibt es in KEINER Stufe."
    read -r -p "   Stufe [0/1/2]: " a || a=""
    case "$a" in 1*) ZUGRIFF=read ;; 2*) ZUGRIFF=write ;; *) ZUGRIFF=off ;; esac
  else
    ZUGRIFF=off
  fi
fi
# Python finden. Reihenfolge: venv des Frameworks (Linux- UND Windows-Layout, denn
# dieses Skript laeuft auch in Git Bash), dann python3, dann python. Unter Windows
# ist "python3" oft nur der Store-Platzhalter, der mit Fehler abbricht.
PYTHON=""
for cand in "$APP_DIR/venv/bin/python" "$APP_DIR/venv/Scripts/python.exe"; do
  [ -x "$cand" ] && PYTHON="$cand" && break
done
if [ -z "$PYTHON" ]; then
  for cand in python3 python; do
    if command -v "$cand" >/dev/null 2>&1 && "$cand" -c "import sys" >/dev/null 2>&1; then
      PYTHON="$cand"; break
    fi
  done
fi
if [ -z "$PYTHON" ]; then
  warn "Kein funktionierendes Python gefunden - Zugriffsstufe nicht gesetzt."
  echo "     Trage sie manuell in $APP_DIR/config.json ein:"
  echo "       \"pi_agent_access\": \"$ZUGRIFF\""
  exit 1
fi
PI_ACCESS="$ZUGRIFF" APP_DIR_ENV="$APP_DIR" "$PYTHON" - <<'PYEOF'
import json, os, pathlib
p = pathlib.Path(os.environ["APP_DIR_ENV"]) / "config.json"
cfg = {}
if p.exists():
    try: cfg = json.loads(p.read_text(encoding="utf-8-sig"))
    except Exception: cfg = {}
cfg["pi_agent_access"] = os.environ["PI_ACCESS"]
p.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
print("   ok: pi_agent_access = %s" % cfg["pi_agent_access"])
PYEOF

# ── 4. Probe ──────────────────────────────────────────────────────────────────

step "Probe..."
"$PYTHON" "$APP_DIR/pi-agent/framework_cli.py" status || true
echo ""
echo "Fertig. Nächste Schritte:"
echo "  1. Framework starten (./start.sh), falls noch nicht laufend."
echo "  2. In Pi einen Anbieter verbinden: 'pi' starten, dann '/login'."
echo "     Soll alles lokal bleiben, dort den lokalen Server eintragen -"
echo "     der Geheim-Modus des Frameworks gilt NICHT für Pi."
echo "  3. Verben ansehen: $PYTHON \"$APP_DIR/pi-agent/framework_cli.py\" --hilfe"
