# Anwendungsfälle — was kann ich mit AI_Framework_Thomas konkret tun?

Diese Liste beschreibt **echte, sofort ausführbare Aufgaben** — nach Ziel gruppiert, jeweils mit dem
passenden Tab bzw. Chat-Befehl **und dem konkreten Vorteil, es mit KI zu machen**. Alles läuft
**lokal und privat** (Ollama); externe API-Anbieter sind optional. Schritt-für-Schritt-Details in der
[Bedienungsanleitung](BEDIENUNGSANLEITUNG.md); Gesamtübersicht der Chat-Befehle mit `/hilfe`.

> **Lesehilfe:** _Ich möchte …_ → **Tab/Befehl** — _Vorteil:_ warum das mit KI schneller/besser geht.
> „🔒 lokal" = verlässt den Rechner nicht (Geheim-Modus erzwingt das global). „🌐 Web" = nutzt eine
> Websuche (abschaltbar).

## Warum überhaupt mit KI? — die wiederkehrenden Vorteile

- **Tempo:** aus Idee/Rohdaten in Minuten ein fertiges Ergebnis, statt Stunden Handarbeit.
- **Kein Spezialwissen nötig:** Fachsprache (Zeugnis, Patentprüfung, §14-UStG, Zeugniscodes) und
  Formatierung (PPTX, PDF, LaTeX) übernimmt das Tool.
- **Menge:** tausende Zeilen, hunderte Mails, ganze Ordner werden auf einmal durchsucht und
  zusammengefasst — das liest kein Mensch durch.
- **Zusammenhänge:** Wissensgraphen und Recherche verbinden Informationen, die verstreut vorliegen.
- **Struktur & Nachvollziehbarkeit:** subjektive Entscheidungen werden transparent (Gewichte,
  Konsistenz, Quellen).
- **Verlässlichkeit, wo sie zählt:** Zahlen (Rechnungen, Gewichte, Diffs) rechnet **deterministisch der
  Code**, nicht das Sprachmodell — die KI liefert Text, die Mathematik bleibt korrekt.
- **Privatsphäre:** vertrauliche Inhalte (Mails, Verträge, Firmendaten) bleiben im Geheim-Modus **auf
  dem eigenen Rechner** — der entscheidende Vorteil gegenüber Cloud-KI.

---

## 1. Chatten, recherchieren, Wissen nutzen

- **Privat mit einer KI chatten** — **Chat** (🔒 lokal). _Vorteil:_ die Bequemlichkeit von ChatGPT,
  aber Daten verlassen den Rechner nicht.
- **Die KI selbst das passende Werkzeug wählen lassen** (rechnen, suchen, Bild, Dokumentensuche) —
  **Assistent-Modus**. _Vorteil:_ eine einzige Eingabefläche statt 25 Tabs zu kennen — man beschreibt
  das Ziel, die KI findet den Weg.
- **Eigene Dokumente/PDFs/Notizen befragen** — Dateien in **RAG** laden, dann fragen. _Vorteil:_
  Antworten **mit Quellenangabe** aus dem eigenen Material, ohne alles selbst zu lesen oder zu suchen.
- **Ein Thema gründlich recherchieren** — **`/recherche <Thema>`** (🌐 Web). _Vorteil:_ die KI zerlegt
  das Thema in Teilaspekte, durchsucht mehrere Quellen parallel und fasst **belegt** zusammen —
  Stunden Recherche in Minuten.
- **Bessere Suchbegriffe finden** (Synonyme, Fachsprache, Englisch) — **`/such <Begriff>`** (🌐 Web).
  _Vorteil:_ findet auch, was man mit den eigenen Worten nicht getroffen hätte.
- **Fehlende Angaben per Formular ergänzen** — **`/frag <Aufgabe>`**. _Vorteil:_ die KI fragt gezielt
  nach, statt auf einer vagen Eingabe schlecht zu raten → bessere Ergebnisse.
- **Eine Antwort vertiefen** — **`/dd10`** / als Dokument **`/ddd10`**. _Vorteil:_ automatisches
  Nachbohren erzeugt Tiefe, die eine einzelne Frage nicht liefert.
- **Antworten vorlesen lassen** — 🔊 (TTS). _Vorteil:_ freihändig zuhören, z. B. beim Prüfen längerer
  Texte.

## 2. Dokumente & Präsentationen erstellen

- **Aus einem Thema eine fertige Präsentation bauen** — **`/praesentation <Thema>`** → Canvas → PPTX.
  _Vorteil:_ Gliederung, Recherche, Texte **und Bilder** entstehen automatisch — vom Stichwort zum
  vorzeigbaren Foliensatz, ohne Design- oder Rechercheaufwand.
- **Aus vorhandenem Text Folien machen** — **Dokumente**-Tab. _Vorteil:_ Umwandeln in Minuten statt
  manuellem Copy-Paste in Folienvorlagen.
- **KI-Bilder in Folien einfügen** — Canvas „Bild erzeugen". _Vorteil:_ passende Illustrationen ohne
  Bildersuche, Lizenzfragen oder Grafikprogramm.
- **Rechtssichere Rechnung/Angebot erstellen** — **Rechnungen**-Tab, PDF/DOCX. _Vorteil:_ Pflichtangaben
  und Formulierung von der KI, **Beträge rechnet der Code deterministisch** — schnell *und*
  rechnerisch korrekt.
- **Arbeitszeugnis in korrekter Zeugnissprache** — **Zeugnisse**-Tab. _Vorteil:_ die codierte
  Zeugnissprache beherrscht die KI — man muss die Geheimsprache nicht selbst kennen.
- **PDF/DOCX/PPTX/LaTeX mit Formeln/Tabellen/Diagrammen** — **Dokumente**-Tab. _Vorteil:_ professionelle
  Formate ohne die jeweilige Software oder LaTeX-Kenntnisse.

## 3. Analysieren & entscheiden

- **Zwei Excel-/CSV-Tabellen vergleichen** — **Excel-Vergleich** / **`/excelvergleich`**. _Vorteil:_
  findet neue/entfernte Zeilen und geänderte Zellen über tausende Datensätze in Sekunden **und erklärt,
  was inhaltlich relevant ist** — von Hand fehleranfällig und langsam.
- **Gewichtete Entscheidung zwischen Varianten** (AHP) — **Varianten** / **`/paarvergleich`**.
  _Vorteil:_ macht ein Bauchgefühl zu einer **nachvollziehbaren, gewichteten Bewertung** mit
  Konsistenzcheck — überzeugend gegenüber Dritten.
- **Ein Problem in eine komplette Entscheidungstabelle verwandeln** — Varianten „Problem → Tabelle".
  _Vorteil:_ Kriterien, Gewichte, Varianten und Bewertungen in einem Schritt — der leere-Blatt-Start
  entfällt.
- **Angebote/Ausschreibungen (RFQ) stapelweise auswerten** — **Anfrage**-Tab. _Vorteil:_ viele
  Angebote nach eigenen Kriterien einheitlich bewertet, statt jedes einzeln zu lesen.
- **Einen Text von mehreren KI-Rollen bewerten lassen** — **Jury**-Tab. _Vorteil:_ mehrere Perspektiven
  (kritisch, fachlich, formal) auf einmal — wie ein Gremium, sofort verfügbar.
- **Lösungsraum systematisch aufspannen** — **Morph-Kasten**. _Vorteil:_ die KI schlägt Ausprägungen
  vor, an die man selbst nicht gedacht hätte.
- **Mehrere Aspekte in einer Recherche-Matrix vergleichen** + **Wissensgraph** — **Matrix** (🌐 Web).
  _Vorteil:_ strukturierter Vergleich vieler Kandidaten/Themen inkl. sichtbar gemachter Verbindungen.

## 4. E-Mails, Verzeichnisse & vertrauliche Daten (alles lokal)

- **Ein Postfach auswerten** (`.pst`/`.mbox`/`.eml`/`.msg`) — **Postfach**-Tab (🔒 lokal). _Vorteil:_
  Jahre an E-Mails werden durchsuchbar, thematisch getaggt und als **Wissensgraph** verbunden —
  komplett offline, ideal für sensible Korrespondenz.
- **Das Postfach in Alltagssprache befragen** — Postfach-Chat (🔒 lokal). _Vorteil:_ „zeig mir alles zu
  Vertrag X" statt mühsamer Stichwortsuche über mehrere Ordner.
- **Einen Ordner voller Dateien analysieren** (mit PII-Schwärzung) — **Verzeichnis-Analyse** (🔒 lokal).
  _Vorteil:_ Überblick über unbekannte Dateibestände, ohne jede Datei zu öffnen — und ohne dass etwas
  nach außen geht.
- **E-Mails/Ordner in eine durchsuchbare Wissensdatenbank überführen** — „In RAG übernehmen".
  _Vorteil:_ danach lassen sich Fragen **quellenbelegt** über den ganzen Bestand beantworten.

## 5. Projekte & Planung

- **Projekte/Aufgaben als Baum mit Wissensgraph** — **To-Do**-Tab. _Vorteil:_ sieht Zusammenhänge
  (Zuständige, Blockaden, Themen) über viele Projekte hinweg, die eine flache Liste verdeckt.
- **Den eigenen Aufgabenbestand befragen** („Was ist als Nächstes dran?", „Woran arbeitet Kollege Y?")
  — To-Do-Datenchat / „🎯 Empfehlung" (🔒 lokal-bevorzugt). _Vorteil:_ Auswertungen über den ganzen
  Bestand ohne manuelles Filtern.
- **Aus einem Dokument einen Projektplan erzeugen** (bis 300 Vorgänge) — **Planer**, „Dokument → Plan".
  _Vorteil:_ ein Konzeptpapier wird zum strukturierten Plan mit Ressourcen — das manuelle Aufsetzen
  entfällt.
- **Kapazität, Zukauf und Terminketten planen** (CPM, Auto-Struktur) — **Planer**. _Vorteil:_ die KI
  entzerrt Ressourcen und schlägt Abhängigkeiten vor, die man leicht übersieht.
- **Aus einem Chat-Verlauf Strategie, Agenten, Plan und Jury ableiten** — **`/plan`**. _Vorteil:_ von
  der losen Diskussion zum umsetzbaren Projektgerüst in einem Schritt.

## 6. Technik, Code & Schutzrechte

- **Mathe-/Rechenaufgaben lösen und plotten** (mit Auto-Verifikation) — **Mathe**-Tab. _Vorteil:_ die KI
  löst **per Code** und prüft nach — zuverlässiger als „im Kopf" gerechnete LLM-Antworten.
- **Code schreiben, ausführen, iterieren** (Sandbox, autonomer Agent, Vorschau) — **Code**-Tab.
  _Vorteil:_ vom Prompt zum lauffähigen, getesteten Code, inkl. selbsttätiger Fehlerkorrektur.
- **Patente recherchieren und prüfen** (Merkmalsanalyse, Neuheit, FTO-Check, Stärke-Score) — **Patente**
  -Tab. _Vorteil:_ eine **prüfer-ähnliche Analyse**, die sonst Fachwissen (oder teure Beratung)
  erfordert — als erste Einordnung.
- **Medizinische Fragen mit 2-Modell-Pipeline** — **Medizin**-Tab (🔒 lokal). _Vorteil:_ strukturierte
  Aufbereitung, vertraulich auf dem eigenen Rechner.

## 7. Medien erzeugen

- **Ein Bild aus einer Beschreibung** — **`/bild`**, geführt **`/bildhelp`**. _Vorteil:_ eigene
  Illustrationen/Konzepte ohne Grafikkenntnisse — lokal, ohne Cloud-Dienst.
- **Ein Bild bearbeiten** (img2img, Inpainting) — **`/bildedit`**. _Vorteil:_ gezielte Änderungen ohne
  Bildbearbeitungsprogramm.
- **Ein Bild hochskalieren** — **`/upscale`**. _Vorteil:_ mehr Auflösung/Schärfe ohne Zusatzsoftware.
- **Aus einem Bild einen Prompt ableiten** — **`/bildprompt`**. _Vorteil:_ einen vorhandenen Stil
  reproduzierbar machen.
- **Audio/Sprache in Text** (mit Zeitmarken) — **Transkription**-Tab (🔒 lokal). _Vorteil:_ Meetings/
  Diktate automatisch verschriftlicht — offline, ohne Upload zu einem Dienst.
- **Kurze Musik/Jingles erzeugen** (ohne GPU) — **`/musik`**. _Vorteil:_ sofort ein passendes Stück,
  ohne Musikkenntnisse oder Lizenzsuche.

## 8. Arbeitsabläufe & Automatisierung

- **Mehrere Schritte nacheinander ausführen** — **`/workflow`** (Schritt-Tags `[lokal]`/`[api]`/`[web]`/
  `[bild]`/`[sprache]`). _Vorteil:_ verkettet Teilaufgaben zu einem Ablauf — ein kleines lokales Modell
  recherchiert, ein großes API-Modell verarbeitet, am Ende steht eine Synthese; das spart Aufwand *und*
  API-Kosten.
- **Ergebnisse an andere Tabs übergeben** — Menü **„↪ senden an…"**. _Vorteil:_ ein Ergebnis fließt
  direkt in To-Do/Planer/Code weiter, ohne Copy-Paste.

## 9. Betrieb, Datenschutz & Modelle

- **Alles streng lokal halten** — **Geheim-Modus** (Persona „Hartman" sperrt zusätzlich die Websuche).
  _Vorteil:_ KI-Nutzung auch für Inhalte, die eine Cloud niemals sehen dürfte.
- **Ein großes Kontextfenster lokal nutzen** — **llama.cpp**/**LM Studio** als „🖥 Lokaler Server"
  eintragen. _Vorteil:_ lange Dokumente/Verläufe verarbeiten, ohne Daten aus der Hand zu geben.
- **Externe Modelle anbinden** (OpenRouter/OpenAI/Groq) — Profil „KI-Anbieter (API)". _Vorteil:_ bei
  Bedarf mehr Leistung; der Schlüssel bleibt lokal (nicht im Backup/Git).
- **Alle Nutzerdaten sichern/wiederherstellen** (wählbarer Umfang) — **Profil → „Alle Daten sichern"**.
  _Vorteil:_ Umzug/Backup in einer Datei, inkl. Merge-Modus.
- **Token-Verbrauch & Kosten im Blick** — Token-Zähler (Sidebar). _Vorteil:_ Kostenkontrolle bei
  API-Nutzung; lokale Nutzung ist ohnehin kostenlos.

---

_Diese Liste ist bewusst pragmatisch gehalten. Detaillierte Anleitungen und alle Optionen stehen in
der [Bedienungsanleitung](BEDIENUNGSANLEITUNG.md); Entwickler-Details in
[docs/ENTWICKLUNG.md](docs/ENTWICKLUNG.md)._
