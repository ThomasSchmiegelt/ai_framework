---
name: framework-cli
description: Zugriff auf AI_Framework_Thomas (lokale KI-Arbeitsumgebung) über eine Kommandozeile — Projekte, Pläne, To-Do, Wissensdatenbanken, Patent-Vorgänge, Variantenvergleiche lesen, Web-Recherche anstoßen und Maschinenbau-Größen deterministisch berechnen. Je nach eingestellter Zugriffsstufe auch Projekte/Listen/Sammlungen anlegen und ändern.
---

# AI_Framework_Thomas über `framework_cli.py`

Das Framework ist eine lokale KI-Arbeitsumgebung mit ~39 Feature-Bereichen. Du
sprichst sie **nicht** über einzelne HTTP-Routen an, sondern über **ein**
Werkzeug: diese CLI. Alles, was du brauchst, steht hier.

```
python pi-agent/framework_cli.py <verb> [argumente] [--json]
```

Rufe `framework_cli.py --hilfe` auf, wenn du unsicher bist: die Ausgabe nennt
die aktuelle Zugriffsstufe und markiert gesperrte Verben mit `x`.

## Zuerst: `status`

```
python pi-agent/framework_cli.py status
```

Sagt dir, ob das Framework läuft, welche Zugriffsstufe gilt (`read` oder
`write`), welche Version, welcher Modus, ob der Geheim-Modus an ist und welche
Modellrollen gesetzt sind. **Führe das als Erstes aus**, wenn ein Aufruf
unerwartet scheitert.

Exit-Codes: `0` ok · `2` Aufruffehler · `3` Zugriff gesperrt · `4` Framework
nicht erreichbar · `5` sonstiger Fehler.

## Lesende Verben

| Verb | Wofür |
|---|---|
| `status` | Erreichbarkeit, Zugriffsstufe, aktive Fähigkeiten |
| `modelle` | welche Modelle verfügbar sind |
| `agenten` | gespeicherte Agenten (Name, Kategorie) |
| `projekte` | Projekte mit Status |
| `plaene [id]` | Pläne; mit `id` der einzelne Plan samt Vorgängen |
| `rag-sammlungen` | Wissensdatenbanken |
| `rag-dokumente <id>` | Dokumente einer Sammlung |
| `todo-baum` | Projektbaum der Aufgabenverwaltung |
| `todo-suche <begriff> [wurzel]` | Volltextsuche im Aufgabenbestand |
| `todo-agenda [wurzel] [person]` | was als Nächstes dran ist (deterministisch, ohne Modell) |
| `todo-frage <frage>` | KI-Auswertung des Aufgabenbestands, auch über Personen |
| `gespraeche [id]` | Chatverläufe; mit `id` der einzelne Verlauf |
| `gespraech-suche <begriff>` | Volltextsuche über die Verläufe |
| `patente-projekte [name]` | Patent-Vorgänge |
| `varianten [name]` | gewichtete Variantenvergleiche (AHP) |
| `recherche <thema> [--tiefe N] [--worte N]` | mehrstufige Web-Recherche, quellengestützt |
| `rechne [kind] [werte]` | Maschinenbau-Rechenkerne |
| `werkstoff <name> [eigenschaft]` | Werkstoffkennwerte |
| `einheit <wert> <von> <nach>` | Einheitenumrechnung |

## Rechnen statt schätzen

**Rechne Festigkeits-, Wellen-, Verbindungs- und Antriebsgrößen niemals selbst.**
Benutze `rechne`. Das läuft lokal und deterministisch, ohne Modell und ohne Netz,
und gibt Annahmen, Rechenweg, Ergebnis und Auslastung zurück.

```
python pi-agent/framework_cli.py rechne
    → listet alle Rechenarten mit ihren Pflichtparametern

python pi-agent/framework_cli.py rechne biegung_kragarm_ende \
    "f=1 kN l=1 m profil=rechteck b=50 h=100 rp=235"
```

Einheiten darfst du mitschreiben (`kN`, `m`, `MPa`, `Nm`, `1/min`), deutsche
Namen auch (`kraft`, `laenge`, `durchmesser`, `drehmoment`, `streckgrenze`).

**Die Rechenart muss zur Lagerung passen** — zwischen `biegung_kragarm_ende` und
`biegung_traeger_mitte` liegt bei gleicher Kraft und Länge der Faktor 4 im
Biegemoment. Steht die Lagerung nicht in der Aufgabe, **frage nach**, statt zu
raten.

Meldet das Werkzeug fehlende Schlüssel, eine unpassende Einheit oder vertauschte
Werte, hast du eine Größe falsch zugeordnet. Korrigiere die Zuordnung — und
**erfinde keine Zahlen** für Lasten, Maße oder Werkstoffe. Frage sie nach.

Schrauben nach VDI 2230 gehören nicht hierher; dafür gibt es den
Schraubenrechner im Chat des Frameworks.

## Anlegen und Ändern — nur bei Zugriffsstufe `write`

| Verb | Wofür |
|---|---|
| `projekt-neu <name> [beschreibung]` | Projekt anlegen |
| `todo-neu <name> [eltern-id]` | Aufgabenliste oder Unterprojekt anlegen |
| `todo-setzen <pid> <json-datei\|->` | Punkte einer Liste **ersetzen** |
| `rag-neu <name>` | Wissensdatenbank anlegen |
| `rag-notiz <id> <titel> <text\|->` | Text in eine Sammlung einlesen |

`todo-setzen` **ersetzt** den Inhalt der Liste. Lies den Stand vorher mit
`todo-baum` oder `todo-suche`, führe deine Änderung ein und schicke das Ganze
zurück. Sonst gehen Punkte verloren.

Ist die Stufe `read`, antwortet die CLI mit Exit-Code `3` und sagt das. Erzwinge
es dann nicht über andere Wege — melde dem Nutzer, dass die Stufe `write`
nötig wäre.

## Was es nicht gibt — in keiner Stufe

Löschen, Mails versenden, Angebote oder Rechnungen versenden, Patente
einreichen, Profil oder Einstellungen ändern, Python ausführen, Aufgaben
zurücksetzen oder importieren. Diese Verben existieren nicht. Suche **keine**
Umwege dafür: wenn der Nutzer etwas davon braucht, sage ihm, dass er es in der
Oberfläche des Frameworks selbst tun muss.

## Grenzen, die du offen ansprechen sollst

- Die Rechenkerne sind nachvollziehbare Hand- und Lehrbuchformeln für
  **Auslegung und Vorauswahl**. Dauerfestigkeit, Schweißnähte und
  Zahnradtragfähigkeit sind darin vereinfacht; ein belastbarer Nachweis läuft
  nach FKM-Richtlinie, EC3-1-9 bzw. DIN 3990/ISO 6336 und braucht einen Prüfer.
- `recherche` hängt an den Web-Einstellungen des Frameworks. Ist die
  Hartman-Persona aktiv oder „Web-Recherche lokal" gesetzt, liefert sie nichts —
  das ist Absicht, kein Fehler.
- Der **Geheim-Modus des Frameworks gilt nicht für dich.** Er erzwingt lokale
  Modelle *innerhalb* des Frameworks. Welches Modell **du** benutzt, entscheidet
  deine eigene Konfiguration. Soll alles lokal bleiben, muss auch dein Anbieter
  lokal zeigen — sage dem Nutzer, wenn das nicht der Fall ist.
