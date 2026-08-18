# Sicherheitsrichtlinie

## Unterstützte Versionen

Sicherheitskorrekturen fließen in die jeweils **aktuelle Version auf `main`** ein
(siehe Datei `VERSION`). Ältere Stände werden nicht separat gepflegt – bitte auf den
neuesten `main`-Stand aktualisieren.

## Eine Schwachstelle melden

**Bitte melde Sicherheitslücken NICHT über öffentliche GitHub-Issues.**

Nutze stattdessen die **private Sicherheitsmeldung** von GitHub:
*Repository → Reiter „Security" → „Report a vulnerability"*
(GitHub „Private Vulnerability Reporting"). Alternativ eine vertrauliche
Kontaktaufnahme mit dem Maintainer **@ThomasSchmiegelt** über GitHub.

Bitte gib nach Möglichkeit an:

- betroffene Komponente/Datei und Version (`VERSION`),
- eine Beschreibung und – falls möglich – Schritte zur Reproduktion,
- die mögliche Auswirkung.

Wir bestätigen den Eingang zeitnah, halten dich über den Fortschritt auf dem Laufenden
und stimmen die Offenlegung mit dir ab.

## Hinweise zum Sicherheitsmodell

Dieses Projekt ist für den **lokalen Betrieb** gedacht. Einige Funktionen sind bewusst
mächtig und sollten nur in vertrauenswürdigen Umgebungen aktiviert werden:

- **Server-seitige Python-Ausführung** (`allow_python_exec` in `config.json`, Code-Tab
  `POST /api/code/run-python`) führt Code in einer *eingeschränkten* Sandbox aus, ist
  aber standardmäßig im Server-Build (`make_server`) **deaktiviert**. Auf Mehrbenutzer-/
  öffentlichen Installationen aus lassen, sofern nicht ausdrücklich gewünscht.
- **API-Schlüssel/Geheimnisse** (externe LLM-Anbieter, EPO-OPS, TLS-Schlüssel) gehören
  nach `data/…` bzw. `certs/` und sind per `.gitignore` ausgeschlossen – **niemals
  committen**.
- **Netzwerk:** Für den Server-Modus (`0.0.0.0`) selbst für Zugriffsschutz/TLS sorgen
  (siehe `docs/SERVER.md`). Standardmäßig lauscht die App nur lokal (`127.0.0.1`).
- **PST/Postfach, Rechnungen, Patente, To-Do** können echte personenbezogene/vertrauliche
  Daten enthalten – diese liegen unter `data/` und werden nicht versioniert.

Vielen Dank, dass du zur Sicherheit des Projekts beiträgst.
