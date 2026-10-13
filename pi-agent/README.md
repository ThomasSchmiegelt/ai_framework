# pi-agent — AI_Framework_Thomas als Werkzeug für einen Agenten-Harness

Bindet [Pi](https://pi.dev) (Earendil, MIT, Node.js ≥ 22.19) als externen
Agenten an dieses Framework. Pi plant und entscheidet, das Framework rechnet und
verwaltet.

**Opt-in und ohne Abhängigkeiten.** Dieser Ordner hängt nicht am Backend — wie
`z-image/`, `z-video/` und `z-music/`. `framework_cli.py` braucht **nur die
Python-Standardbibliothek** (`urllib`), keine venv, keine Installation. Pi selbst
installierst du separat; das Framework funktioniert ohne Pi unverändert weiter.

## Warum eine CLI und nicht 200 Tool-Schemas

Das Framework hat ~39 Router mit weit über hundert Endpunkten. Die passen nicht
als Tool-Schemas in den Kontext eines lokalen Modells. Darum wird das Framework
als **ein** Werkzeug gebunden: die CLI plus `skills/framework-cli.md` als
Beschreibung, die Pi als Datei liest.

## Zugriffsstufe

Steht in der `config.json` des Frameworks unter `pi_agent_access`:

| Wert | Bedeutung |
|---|---|
| `"off"` | Die CLI verweigert jeden Aufruf. **Standard, wenn der Schlüssel fehlt.** |
| `"read"` | Nur lesende und rechnende Verben. |
| `"write"` | Zusätzlich anlegen und ändern. |

Der Installer fragt das ab (`install.ps1` / `install.sh`). Nachträglich ändern:
den Wert in `config.json` setzen — es braucht keinen Neustart, die CLI liest ihn
bei jedem Aufruf.

**Nicht vorhanden, in keiner Stufe:** Löschen, Mails versenden, Angebote oder
Rechnungen versenden, Patente einreichen, Profil/Einstellungen ändern, Python
ausführen, Aufgaben zurücksetzen oder importieren.

### Ehrliche Einordnung dieser Grenze

`pi_agent_access` ist eine **Leitplanke, keine Sicherheitsgrenze.** Die CLI hält
sich daran. Sie verhindert nicht, dass irgendein Prozess auf diesem Rechner die
lokale HTTP-API direkt anspricht — das konnte er vorher auch schon. Pi hat
ausdrücklich **kein** Rechtesystem und empfiehlt für harte Grenzen einen
Container. Wer eine echte Grenze braucht, setzt das Framework hinter
Authentifizierung oder Pi in einen Container.

## Einrichten

1. **Framework starten** (`start.bat` bzw. `start.sh`) — die CLI spricht die
   laufende Anwendung über die in `config.json` eingetragene Adresse an.

2. **Zugriffsstufe setzen**, falls nicht beim Installieren gewählt:

   ```json
   { "pi_agent_access": "read" }
   ```

3. **Prüfen:**

   ```
   python pi-agent/framework_cli.py status
   python pi-agent/framework_cli.py --hilfe
   ```

4. **Pi die Beschreibung geben.** Kopiere oder verlinke
   `pi-agent/skills/framework-cli.md` in den Skills-Ordner deines Pi-Projekts
   (bei Pi üblicherweise `.agents/skills/`):

   ```powershell
   # Windows
   Copy-Item pi-agent\skills\framework-cli.md <pi-projekt>\.agents\skills\
   ```

   ```bash
   # Linux
   ln -s "$(pwd)/pi-agent/skills/framework-cli.md" <pi-projekt>/.agents/skills/
   ```

## Verben

`framework_cli.py --hilfe` listet alle und markiert mit `x`, was die
eingestellte Stufe sperrt. Kurzform:

**Lesen** — `status`, `modelle`, `agenten`, `projekte`, `plaene`,
`rag-sammlungen`, `rag-dokumente`, `todo-baum`, `todo-suche`, `todo-agenda`,
`todo-frage`, `gespraeche`, `gespraech-suche`, `patente-projekte`, `varianten`,
`recherche`, `rechne`, `werkstoff`, `einheit`

**Anlegen/Ändern (nur `write`)** — `projekt-neu`, `todo-neu`, `todo-setzen`,
`rag-neu`, `rag-notiz`

`--json` liefert rohes JSON statt einer Liste.

### Beispiele

```
python pi-agent/framework_cli.py status
python pi-agent/framework_cli.py projekte
python pi-agent/framework_cli.py plaene 35dd095fb326
python pi-agent/framework_cli.py todo-agenda root
python pi-agent/framework_cli.py todo-frage "Was ist bei Kollege Meier offen?"
python pi-agent/framework_cli.py recherche "Normen für E-Bike-Ladesäulen" --tiefe 5
python pi-agent/framework_cli.py rechne                # alle Rechenarten
python pi-agent/framework_cli.py rechne torsion "t=250 Nm d=30"
python pi-agent/framework_cli.py werkstoff S235JR
```

`rechne`, `werkstoff` und `einheit` laufen **lokal ohne Framework** — sie
importieren `tools/mechanik.py`, `tools/materials.py` und `tools/engineering.py`
direkt. Alles andere braucht die laufende Anwendung.

## Geheim-Modus

Die CLI umgeht ihn nicht: alles Modellgestützte läuft über die HTTP-Endpunkte,
dort greifen `_secret_local()` und `_web_search_allowed()` wie immer. Lokal
gerechnet wird nur Deterministisches, ohne Modell und ohne Netz.

**Aber:** welches Modell **Pi selbst** benutzt, liegt außerhalb des Frameworks.
Soll wirklich alles lokal bleiben, muss Pis Anbieter auf deinen lokalen
OpenAI-kompatiblen Server zeigen (llama.cpp / LM Studio) — und die CLI der
einzige Weg sein, auf dem Pi an die Daten kommt.

## Lizenz

Dieser Ordner: MIT, wie das Framework. Pi selbst ist MIT (Earendil Inc.), wird
aber **nicht** mitgeliefert und nicht von den Installern des Frameworks
installiert — es bringt eine Node.js-Laufzeit mit, die das Framework sonst nicht
braucht.
