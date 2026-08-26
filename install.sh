#!/usr/bin/env bash
# install.sh — AI Framework Thomas · Linux-Installation
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "=== AI Framework Thomas — Installation ==="

# Python 3 prüfen
if ! command -v python3 &>/dev/null; then
  echo "FEHLER: python3 nicht gefunden. Bitte installieren: sudo apt install python3 python3-venv python3-pip"
  exit 1
fi
PYTHON=$(command -v python3)
echo "Python: $($PYTHON --version)"

# Ollama prüfen
if ! curl -sf http://localhost:11434/api/tags >/dev/null 2>&1; then
  echo "WARNUNG: Ollama ist nicht erreichbar (http://localhost:11434)."
  echo "         Bitte Ollama starten: ollama serve"
  echo "         Installation wird trotzdem fortgesetzt."
fi

# Virtuelle Umgebung anlegen
if [ ! -d "venv" ]; then
  echo "Erstelle virtuelle Umgebung..."
  $PYTHON -m venv venv
fi

# Abhängigkeiten installieren
echo "Installiere Python-Abhängigkeiten..."
venv/bin/pip install --upgrade pip setuptools wheel --quiet
venv/bin/pip install -r requirements.txt --quiet --prefer-binary

# Spracherkennungs-Modell (faster-whisper) vor-cachen — laedt von HuggingFace nach
# models/whisper (config.json: stt_download_root), damit die erste Transkription
# offline laeuft. ~150 MB, laeuft auf CPU. Fehlschlag ist unkritisch (Nachladen zur Laufzeit).
echo "Lade Spracherkennungs-Modell 'base' (faster-whisper, ~150 MB, CPU)…"
venv/bin/python -c "from faster_whisper import WhisperModel; WhisperModel('base', device='cpu', compute_type='int8', download_root='models/whisper')" \
  && echo "  ok: STT-Modell bereit" || echo "  WARNUNG: STT-Modell nicht vorab geladen — wird beim ersten Transkribieren nachgeladen"

# Datenverzeichnisse anlegen (falls nicht vorhanden)
for dir in data/uploads data/reports data/code data/plans data/dossiers data/profile_assets data/jury_docs; do
  mkdir -p "$dir"
  touch "$dir/.gitkeep" 2>/dev/null || true
done

# ── Funktionsauswahl (optionale Tabs) + lokal/API ────────────────────────────
# Schreibt die Vorbelegung nach config.json: hidden_tabs_default (NICHT gewählte
# optionale Tabs) und enable_api. Nicht-interaktiv (kein TTY) → Standard belassen.
if [ -t 0 ]; then
  echo ""
  echo "=== Funktionsauswahl (optionale Tabs) ==="
  echo "Jeweils [j/N]. Diese Tabs sind beim Erststart sonst ausgeblendet."
  OPT_TABS="rag:Wissensdatenbanken-(RAG) ide:Code-IDE mathe:Mathe medizin:Medizin mail:Mail logs:Logs diranalyse:Verzeichnis-Analyse postfach:Postfach-(PST/Mail,-nur-lokal) patente:Patente-(Patent-Recherche) rechnung:Angebote/Rechnungen zeugnis:Arbeitszeugnisse varianten:Variantenvergleich todo:To-Do-mit-Wissensgraph morph:Morphologischer-Kasten jury:Jury"
  HIDDEN=""
  for entry in $OPT_TABS; do
    tab="${entry%%:*}"; label="${entry#*:}"
    read -r -p "  ${label//-/ } aktivieren? [j/N] " ans || ans=""
    case "$ans" in
      [jJyY]*) : ;;                      # aktiv → nicht verbergen
      *) HIDDEN="$HIDDEN${HIDDEN:+,}\"$tab\"" ;;
    esac
  done
  echo ""
  read -r -p "Externe KI-Anbieter (API, z. B. OpenRouter) zusätzlich zu lokal nutzen? [j/N] " api_ans || api_ans=""
  case "$api_ans" in [jJyY]*) ENABLE_API=true ;; *) ENABLE_API=false ;; esac
  # Python-Ausführung im Code-Tab (serverseitig). Lokal sinnvoll; im Mehrbenutzer-
  # Server eher abschalten. Leere Eingabe = Ja (Standard).
  read -r -p "Python-Code im Code-Tab serverseitig ausführen? (lokal empfohlen) [J/n] " py_ans || py_ans=""
  case "$py_ans" in [nN]*) ALLOW_PY=false ;; *) ALLOW_PY=true ;; esac

  HIDDEN_JSON="[$HIDDEN]"
  echo "Schreibe Auswahl nach config.json …"
  HIDDEN_JSON="$HIDDEN_JSON" ENABLE_API="$ENABLE_API" ALLOW_PY="$ALLOW_PY" "$PYTHON" - <<'PYEOF'
import json, os, pathlib
p = pathlib.Path("config.json")
cfg = {}
if p.exists():
    try: cfg = json.loads(p.read_text(encoding="utf-8"))
    except Exception: cfg = {}
cfg["hidden_tabs_default"] = json.loads(os.environ["HIDDEN_JSON"])
cfg["enable_api"] = os.environ["ENABLE_API"] == "true"
cfg["allow_python_exec"] = os.environ["ALLOW_PY"] == "true"
p.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
print("  ok: hidden_tabs_default=%s enable_api=%s allow_python_exec=%s" % (cfg["hidden_tabs_default"], cfg["enable_api"], cfg["allow_python_exec"]))
PYEOF
fi

echo ""
echo "=== Empfohlene Modelle (per 'ollama pull' laden) ==="
echo "  ollama pull ministral-3:3b     # Standardmodell (klein, schnell)"
echo "  ollama pull granite4.2:3b      # IBM, Apache-2.0, ~2,2 GB, 128K Kontext — sehr gut fuer"
echo "                                 #   Tool-Use/JSON + RAG, deutschsprachig (lokaler Einsatz)"
echo "  ollama pull nomic-embed-text   # RAG-Embeddings"
echo "  (grosse Rechner: zusaetzlich granite4.2:8b bzw. :30b)"
echo ""
echo "=== Installation abgeschlossen ==="
echo "Starten mit:  ./start.sh"
echo "Oder direkt:  source venv/bin/activate && uvicorn main:app --host 127.0.0.1 --port 8780 --reload"
