# AI_Framework_Thomas auf GitHub veröffentlichen

Das Repository ist veröffentlichungsbereit vorbereitet:

- `LICENSE` (MIT) — bei Bedarf den Namen anpassen.
- `.gitignore` — schließt `venv/`, Caches, `.claude/`, `server.log` und alle
  **Laufzeit-/Nutzerdaten** unter `data/` aus (Standard-Agenten und die leere
  Verzeichnisstruktur bleiben erhalten; Profil-Bilder und Profil/Projekte werden
  **nicht** mitgeführt).
- `README.md` — Projektüberblick inkl. der vier Modi.

> **Hinweis:** Auf diesem Rechner ist `git` aktuell **nicht installiert**.
> Entweder Git installieren (`winget install Git.Git`) oder **GitHub Desktop**
> verwenden. Danach die folgenden Schritte ausführen.

## Variante A — Git-Kommandozeile

```powershell
cd C:\Users\user\ai_framework_thomas

git init
git add .
git commit -m "AI_Framework_Thomas: erste öffentliche Version (Maschinenbau/KI/Soziales/Marketing)"

# Leeres Repo auf github.com anlegen (ohne README/License), dann:
git branch -M main
git remote add origin https://github.com/<DEIN-KONTO>/ai_framework_thomas.git
git push -u origin main
```

## Variante B — GitHub CLI (falls `gh` installiert/angemeldet)

```powershell
cd C:\Users\user\ai_framework_thomas
git init && git add . && git commit -m "AI_Framework_Thomas: erste öffentliche Version"
gh repo create ai_framework_thomas --public --source . --remote origin --push
```

## Variante C — GitHub Desktop

1. *File → Add local repository* → `C:\Users\user\ai_framework_thomas`.
2. Git-Repo initialisieren lassen, ersten Commit erstellen.
3. *Publish repository* (öffentlich/privat wählen).

## Vor dem ersten Push kurz prüfen

- `git status` zeigt **kein** `venv/`, kein `data/ai_framework_thomas.db`, kein
  `data/profile_assets/<bild>` und kein `.claude/`.
- In `LICENSE` steht der gewünschte Rechteinhaber.
- README/Screenshots nach Wunsch ergänzen.

## Nach dem Klonen (für andere Nutzer)

```powershell
# Windows: install.bat ausführen (Python + Ollama + venv + Modelle)
install.bat
# danach starten:
start.bat   ->  http://localhost:8780
```
Logo, Vorlagen-Deckblatt und -Kopfzeile werden anschließend im **Profil**
hochgeladen; der **Modus** legt Farben und fachliche Ausrichtung fest.
