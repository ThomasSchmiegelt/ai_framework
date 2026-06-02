# 🤖 AI_Framework_Thomas — Bedienungsanleitung

**Version:** 3.0 · **Stand:** Mai 2026

---

## Inhaltsverzeichnis

1. [Was ist AI_Framework_Thomas?](#1-was-ist-ai_framework_thomas)
2. [Anwendung starten](#2-anwendung-starten)
3. [Benutzeroberfläche im Überblick](#3-benutzeroberfläche-im-überblick)
4. [Chatten](#4-chatten)
5. [Dateien hochladen](#5-dateien-hochladen)
6. [Tools und KI-Funktionen](#6-tools-und-ki-funktionen)
7. [Canvas — Präsentationen & Tabellen](#7-canvas--präsentationen--tabellen)
8. [Präsentations-Assistent](#8-präsentations-assistent)
9. [Recherche](#9-recherche)
- [RAG — Eigene Wissenssammlungen](#rag--eigene-wissenssammlungen)
- [Dokumentengenerator](#dokumentengenerator)
10. [Planer (Netzplan / CPM)](#10-planer-netzplan--cpm)
11. [Matrix-Recherche](#11-matrix-recherche)
12. [Code-Tab (IDE + JSON-Editor)](#12-code-tab-ide--json-editor)
- [JSON-Editor](#json-editor)
13. [Diagnose-Logger](#13-diagnose-logger)
14. [Agenten](#14-agenten)
15. [Gespräche verwalten](#15-gespräche-verwalten)
16. [Nutzerprofil & Projekte](#16-nutzerprofil--projekte)
17. [Exportieren](#17-exportieren)
18. [Backup & Wiederherstellung](#18-backup--wiederherstellung)
19. [Modelle & VRAM](#19-modelle--vram)
20. [Tastenkürzel](#20-tastenkürzel)
21. [Technische Hinweise](#21-technische-hinweise)
22. [Deinstallation](#22-deinstallation)

---

## 1. Was ist AI_Framework_Thomas?

AI_Framework_Thomas ist ein lokales, datenschutzfreundliches KI-Interface — vollständig auf dem
eigenen Rechner. Alle Daten bleiben lokal; nach außen geht nur die Websuche
(DuckDuckGo), und das auch nur, wenn sie aktiviert ist.

**Kernfunktionen:** Chat mit lokalen Modellen · Websuche · Berechnungen ·
Präsentationen & Tabellen · Recherche mit Quellen · Netzplanung · Code-IDE ·
spezialisierte Agenten · Dokument-Export mit Firmen-Design.

---

## 2. Anwendung starten

**Windows:** `start.bat` doppelklicken (Einzelplatz) bzw. `start_server.bat`
(Server). Danach im Browser **http://localhost:8780** öffnen.

**Manuell / Linux:**
```bash
source venv/bin/activate        # Windows: .\venv\Scripts\Activate.ps1
uvicorn main:app --host 127.0.0.1 --port 8780 --reload
```

Voraussetzung: **Ollama läuft** (`ollama list` zum Prüfen) und die Modelle aus
`config.json` sind gezogen.

---

## 3. Benutzeroberfläche im Überblick

Oben verläuft die **Tab-Leiste** mit acht Bereichen:

| Tab | Funktion |
|-----|----------|
| 💬 Chat | Haupt-Chat-Ansicht |
| 🖼️ Canvas | Präsentationen & Tabellen anzeigen |
| 🤖 Agenten | Agenten erstellen und bearbeiten |
| 🔬 Recherche | Aspektbasierte Recherche mit Quellen |
| 📚 RAG | Eigene Wissenssammlungen für die KI |
| 🗂️ Planer | Netzplan / Kritischer Pfad |
| 📊 Matrix | Recherche-Tabelle |
| 💻 IDE | Code-Programme erstellen & ausführen |
| 📋 Logs | Diagnose-Protokoll |

### Sidebar (links)

- **Logo** (oben; im Profil hochladbar, sonst Schriftzug „AI_Framework_Thomas")
- **＋ Neues Gespräch**
- **🔍 Suche** über alle gespeicherten Gespräche
- **Gesprächsliste** (Klick lädt, Doppelklick benennt um)
- **Projekt-Filter** (oben) und unten: **Modell**, **Agent**,
  **Chat→Projekt-Zuordnung**
- Buttons: **📁 Projekte**, **🤖 Agenten**, Gespräch-Im/Export,
  **💾 Backup**, **📥 Restore**, **👤 Profil bearbeiten**

---

## 4. Chatten

1. Text eingeben, **Enter** sendet (**Shift+Enter** = neue Zeile).
2. Die KI antwortet per Streaming (wortweise in Echtzeit).

**🔍 Websuche** (Toolbar) ist standardmäßig aktiv — die KI sucht selbstständig,
wenn sie aktuelle Informationen braucht. Inaktiv = nur Modellwissen.

Während der Antwort werden Tool-Aufrufe angezeigt (Suche, Berechnung,
Präsentation …).

**Formeln & Quellen:** Berechnungen werden als **mathematische Formeln mit
Formelzeichen** dargestellt (z. B. σ = F/A, sauber gesetzt). Erkennt die Antwort
**Normen** (DIN/EN/ISO/VDI …) oder **Gesetzes-/Paragrafenangaben** (z. B. § 433 BGB),
werden diese automatisch als **Link** zu einer maßgeblichen Quelle gesetzt
(Gesetze → gesetze-im-internet.de, Normen → DIN-Suche). Links öffnen in einem neuen Tab.

---

## 5. Dateien hochladen

**📎 Datei** klicken oder Dateien ins Eingabefeld ziehen.

| Format | Verarbeitung |
|--------|-------------|
| PDF | Textextraktion (pypdf) |
| DOCX / DOC | Textextraktion |
| XLSX / XLS / CSV | Tabelleninhalt als Text |
| TXT, MD, PY, JS, JSON | Direkte Übergabe |
| PNG, JPG, GIF, WEBP | Bildanalyse (multimodales Modell nötig) |

---

## 6. Tools und KI-Funktionen

Die KI ruft diese Tools bei Bedarf selbstständig auf:

- **🔍 Websuche** (`web_search`) — DuckDuckGo, kein API-Key.
- **🧮 Berechnung** (`calculate`) — Python-Sandbox (`math`, `numpy`, `scipy`, `sympy`).
- **📊 Präsentation** (`create_presentation`) — mehrseitige Folien im Canvas.
- **📋 Tabelle** (`create_spreadsheet`) — strukturierte Tabelle im Canvas.
- **🗺️ Routenplaner** (`route_planner`) — bei einer Frage nach dem Weg von Ort A nach
  Ort B wird eine **interaktive OpenStreetMap-Karte** mit der Route direkt im Chat
  angezeigt (Strecke und Fahrzeit inklusive). *Benötigt Internet.*
- Zusätzlich: Einheitenumrechnung, Gleichungslöser, Diagramme, Werkstoff-Lookup,
  VDI-2230-Schraubenberechnung.

**Beispiel-Prompts:**
- *„Berechne die Leistung: P = U × I mit U = 400 V, I = 25 A"*
- *„Erstelle eine Präsentation über FEM-Analyse mit 6 Folien"*
- *„Vergleichstabelle gängiger FEM-Softwarepakete"*
- *„Wie komme ich von Stuttgart nach München?"* → zeigt die Route auf der Karte

---

## 7. Canvas — Präsentationen & Tabellen

Der Tab **🖼️ Canvas** zeigt erzeugte Präsentationen und Tabellen.

- Navigation: **‹** / **›** zwischen Folien, Folienzähler zeigt die Position.
- Export: **📊 PPTX** bzw. **📋 XLSX** (sofortiger Download).
- Präsentationen erscheinen im **Design des gewählten Modus** (Farben) mit den
  **im Profil hinterlegten Vorlagen**: Deckblatt-Hintergrund und Kopfzeilen-Banner.
  Sind keine Vorlagen hochgeladen, werden die Folien schlicht ohne Bild erzeugt.
- Beim Laden eines alten Gesprächs wird dessen Canvas automatisch wiederhergestellt.

### Folien direkt bearbeiten (WYSIWYG)

Erzeugte Folien lassen sich direkt auf dem Canvas anpassen:

- **Auf einen Text klicken** → ein Eingabefeld erscheint an Ort und Stelle.
  *Enter* übernimmt, *Esc* bricht ab (bei mehrzeiligem Text: *Strg+Enter*).
- **Auf ein Bild klicken** → Bild austauschen.
- **Folien-Werkzeugleiste** (oben rechts): Folie nach vorne/hinten verschieben,
  Bild tauschen, **✨ Text neu generieren** (bei Bildfolien), Folie löschen.
- **Zwischen Folien wechseln** mit **‹** / **›** oben (Folienzähler zeigt die
  Position) — gilt auch für die *bebilderte Präsentation*, sodass sich **jede**
  Folie bearbeiten lässt, nicht nur das Deckblatt.

---

## 8. Präsentations-Assistent

Im Canvas-Tab auf **🪄 Assistent** klicken — es öffnet sich ein **tabellarischer**
Assistent (kein Frage-Antwort-Wizard mehr).

### Ablauf

1. **Titel** der Präsentation und **Design-Thema** oben eintragen.
2. In der **Tabelle** je Zeile eine Folie definieren:
   - **Typ**: Text · Text + Bild · Nur Bild · Abschnitt
   - **Titel** der Folie
   - **Inhalt / Thema für KI** (Stichworte genügen)
   - **📎 Bild** bei Bedarf hochladen
3. **▶ Erstellen** klicken.

### Was automatisch entsteht

- **Deckblatt** — vollautomatisch mit Präsentationstitel, Ersteller und
  Projektnummer (aus dem Nutzerprofil).
- **Inhaltsfolien** — die KI formuliert pro Tabellenzeile **Folie für Folie**
  saubere Texte (schonend für kleine Rechner). Bilder werden rechts platziert
  und automatisch skaliert, Text links.
- **Abschlussfolie** — vollautomatisch.

Das Ergebnis öffnet sich im Canvas und kann als PPTX exportiert werden.

### Bebilderte Präsentation (Bilderordner automatisch beschreiben)

Im Canvas-Tab auf **🖼️ Bild-Präsentation** klicken. Hier erzeugt die KI aus einem
**ganzen Ordner voller Bilder** eine Präsentation, bei der jedes Bild fachkundig
beschrieben wird:

1. **Titel** und **Beschreibung & Ziel** der Präsentation eingeben.
2. **🧠 Analyse-Experte ableiten** — aus der Beschreibung wird eine fachliche
   Persona erzeugt (z. B. ein „Elektrotechnik-Experte" bei E-Maschinen-Themen).
   Den Text kannst du vor dem Start noch anpassen.
3. **📁 Bilderordner wählen** — alle Bilder des Ordners werden geladen.
4. **▶ Präsentation erstellen** — pro Bild prüft die KI den Dateinamen und
   **analysiert das Bild** (lokales Vision-Modell). Es entsteht je Bild eine Folie
   mit dem Bild auf der einen und einem kurzen Text auf der anderen Hälfte.

Aufbau: Deckblatt → kurze Beschreibung → Bildfolien → Abschluss. Anschließend mit
dem WYSIWYG-Editor (siehe Abschnitt 7) feinjustierbar.

---

## 9. Recherche

Tab **🔬 Recherche**: Thema eingeben, mehrere **Aspekte** hinzufügen
(z. B. „Physik", „Kosten", „Normen"). Für jeden Aspekt läuft eine Websuche; die
KI fasst alles zu einem strukturierten Bericht **mit Quellenangaben** zusammen.

- **📄 Als Dokument** exportiert den Bericht als DOCX **mit Firmen-Kopfzeile** und
  dem Hinweis „Dieser Bericht wurde von KI (AI_Framework_Thomas) generiert".
- **📚 In Wissensdatenbank** übernimmt den Bericht direkt in eine gewählte
  Wissensdatenbank (RAG), sodass die KI später darauf zugreifen kann.

> **Wissenschaftsmodus:** Recherche läuft immer im Wissenschaftsmodus — die KI
> arbeitet quellengebunden, kennzeichnet Unsicherheiten und erfindet nichts.
> Korrektheit hat Vorrang.

---

## RAG — Eigene Wissenssammlungen

Tab **📚 RAG**: Hier legst du **Wissenssammlungen** aus eigenen Dokumenten an. Im
Chat kann die KI dann auf diese Dokumente zugreifen und ihre Antworten **darauf
stützen** (RAG = „Retrieval-Augmented Generation"). Alles bleibt lokal — die
Dokumente verlassen den Rechner nicht.

> **Einmalige Voraussetzung:** Das Embedding-Modell muss in Ollama vorhanden sein.
> Einmal ausführen: `ollama pull nomic-embed-text`. (Der Installer `install.ps1`
> erledigt das bereits automatisch.)

### So funktioniert es kurz erklärt

1. Hochgeladene Dokumente werden in kleine Textstücke („**Chunks**") zerlegt.
2. Jeder Chunk wird in einen Zahlenvektor („**Embedding**") umgewandelt und in der
   lokalen Datenbank gespeichert.
3. Stellst du im Chat eine Frage, werden die **passendsten Chunks** herausgesucht
   und der KI als Kontext mitgegeben — samt Quellenangabe (Dateiname).

### Wissensdatenbank anlegen

1. Tab **📚 RAG** öffnen.
2. **Name** vergeben (z. B. „Handbuch", „Normen", „Projektakte").
3. Zwei **Regler** einstellen (statt technischer Zahlen):
   - **Suche: schnell ↔ gründlich** — wie viel und wie große Textstücke gesucht
     werden. *schnell* = kleine Abschnitte, wenig Kontext (sparsam, ideal für kleine
     Grafikkarten); *gründlich* = mehr und größere Treffer (mehr Kontext, langsamer).
   - **Antwort: kreativ ↔ korrekt** — wie streng die KI bei den Quellen bleibt.
     *kreativ* = darf mit eigenem Wissen ergänzen; *korrekt* = antwortet ausschließlich
     aus den gefundenen Auszügen.
   Unter jedem Regler steht, was die aktuelle Stellung konkret bedeutet.
4. **Dokumente bereinigen** an-/ausschalten (empfohlen: an, siehe unten).
5. **📚 Wissensdatenbank anlegen** klicken.

> **Hinweis (kleine Grafikkarte):** Die Embeddings laufen immer **auf der CPU**, damit
> sie das Chat-Modell nicht aus den 6 GB VRAM verdrängen. Der Regler *gründlich* erhöht
> nur Chunk-Größe und Trefferzahl — den VRAM-Bedarf steuerst du indirekt über den
> Kontext, der dem Chat-Modell vorgelegt wird (kleiner = sparsamer).

### Dokumente hinzufügen

Bei einer Sammlung auf **＋ Dokument(e) hinzufügen** klicken und eine oder mehrere
Dateien wählen (PDF, DOCX, XLSX, CSV, TXT, MD). Jede Datei wird extrahiert,
optional bereinigt, in Chunks zerlegt und eingebettet. Danach steht je Dokument die
Anzahl der erzeugten Chunks. Einzelne Dokumente oder die ganze Sammlung lassen sich
wieder **entfernen**.

> Bei großen PDFs kann das Einbetten einen Moment dauern — eine Meldung bestätigt
> den Abschluss („✓ Datei: N Chunks").

### Gespräch in eine Sammlung übernehmen

Im Block **💬 → 📚 Gespräch in Sammlung übernehmen** ein gespeichertes Gespräch und
eine Zielsammlung wählen, dann **übernehmen**. Das Gespräch wird als Dokument in die
Sammlung eingebettet und steht künftig als Wissen zur Verfügung. Mit der Option
**„Original danach löschen"** wird das Gespräch dabei aus der Liste entfernt
(„verschieben"); ohne Haken bleibt es erhalten (Kopie).

### Dokumentbereinigung

Mit aktivierter Option **Bereinigen** wird der extrahierte Text vor dem Chunking
geglättet: Silbentrennung am Zeilenende wird aufgehoben (`Maschi-/nenbau` →
`Maschinenbau`), umbrochene Zeilen werden zu Absätzen zusammengefügt, reine
Seitenzahl-Zeilen und Steuerzeichen entfernt, Mehrfach-Leerzeichen reduziert. Das
verbessert die Trefferqualität spürbar.

> **Vorab prüfen:** Mit `python scripts/clean_documents.py <Datei-oder-Ordner>` kannst
> du das Bereinigungsergebnis als `*.clean.txt` ansehen, bevor du hochlädst
> (vorher `set PYTHONIOENCODING=utf-8`).

### Im Chat nutzen

1. In der Chat-Toolbar den Umschalter **📚 RAG** aktivieren — daneben erscheint eine
   **Sammlungsauswahl**.
2. Eine (oder mit Strg/Shift mehrere) Sammlung(en) markieren.
3. Frage stellen. Über der Antwort zeigt eine Leiste **„📚 Kontext aus
   Wissenssammlung"** an, welche Dokumente herangezogen wurden. Die KI nennt die
   Quelle und weist darauf hin, falls die Antwort nicht in den Dokumenten steht.

---

## Dokumentengenerator

Tab **📄 Dokumente**: erzeugt vollständige Dokumente (z. B. einen **Antrag für ein
Förderprogramm**, einen Projektbericht oder ein Pflichtenheft) mit Hilfe eines
**Dokument-Agenten** und – optional – auf Basis deiner Wissensdatenbanken.

1. **Dokument-Agent wählen.** Die Agenten legst du im Tab **🤖 Agenten** an
   (Kategorie **„Dokumentation"** → sie erscheinen hier zuoberst). Der System-Prompt
   des Agenten bestimmt Aufbau und Stil des Dokuments (z. B. „Du erstellst formale
   Förderanträge nach Gliederung …"). So kannst du **verschiedene Dokumenttypen** als
   eigene Agenten definieren.
2. **Wissensdatenbank(en) als Quelle** wählen (optional, Mehrfachauswahl) — z. B. das
   **Plan-RAG** aus der Tätigkeits-Recherche oder eine Recherche-Datenbank. Die
   passenden Auszüge fließen als Kontext ein.
3. **Auftrag** beschreiben (welches Dokument, welche Gliederung, welches Programm …).
4. **📄 Dokument erzeugen.** Das Ergebnis erscheint formatiert (inkl. Formeln/Links).
5. **📝 Als DOCX** exportieren oder **📚 In Wissensdatenbank** zurücklegen.

Mit der Option **„quellengebunden (wissenschaftlich)"** arbeitet die KI streng auf
Basis der Quellen (für belegpflichtige Dokumente).

### Quellmaterial mitgeben

Im Abschnitt **📎 Quellmaterial** kannst du der Aufgabe konkrete Vorlagen beilegen:

- **Externes Dokument laden** — PDF/DOCX/TXT/XLSX hochladen; der Text wird automatisch
  extrahiert und als Grundlage genutzt (z. B. alte Protokolle, um eine **Besprechung
  vorzubereiten**).
- **Dossier wählen** — eines der in der Tätigkeits-Recherche erzeugten Dossiers als
  Quelle einbinden.
- **📋 Bestehenden Text einfügen** — Text per Copy & Paste übernehmen. „**⬇ Text direkt
  als Dokument übernehmen**" lädt ihn unverändert als Dokument (ohne KI); ohne
  Übernehmen dient er der Aufgabe als Quellmaterial.

Aus dem **Chat** bringt der Knopf **„→ 📄 Doku"** (in der Eingabeleiste) das laufende
Gespräch komprimiert hierher — praktisch, um z. B. aus einer Unterhaltung eine
Besprechung zu planen.

---

## 10. Planer (Netzplan / CPM)

Tab **🗂️ Planer** — Projektplanung nach der Methode des **Kritischen Pfades**.

> **Zum Ausprobieren** ist ein **100-Aufgaben-Beispielprojekt** („Lokale KI im
> Unternehmen", stark parallelisiert) enthalten — einfach oben über das Dropdown
> **„— Plan laden —"** auswählen. Eine passende Beispiel-Ressourcenliste liegt unter
> `samples/Beispiel_Ressourcenliste.csv` (über **📥 Katalog** importierbar).

Jede Aufgabe hat: **ES**/**EF** (frühester Start/Ende), **LS**/**LF** (spätester
Start/Ende), **Puffer** = LF − EF. Aufgaben mit Puffer 0 bilden den **kritischen
Pfad** (rot).

> **Tipp:** Den **Trenner** zwischen Aufgabenliste und Netzplan kannst du mit der Maus
> **ziehen**, um die Breite der Bereiche anzupassen (Doppelklick setzt zurück). Im
> **Code**-Tab gibt es denselben Trenner zwischen Editor und Vorschau.

1. **＋ Aufgabe** anlegen, ID, Name, Dauer, Vorgänger/Nachfolger (kommasepariert) eintragen.
2. **⚙️ CPM berechnen** zeichnet den Netzplan.
3. Navigation: Mausrad = Zoom, Ziehen = Verschieben.
4. **💾 Speichern** / Laden über das Dropdown; **CSV-Import/-Export** möglich.

**KI-Assistent** (unten): *„Welche Aufgaben fehlen?"*, *„Prüfe auf Konsistenz"*,
*„Detailliere Aufgabe T3"*.

### KI-Funktionen für die Planung

Über der Tabelle eine **Projektbeschreibung & Ziel** eingeben, dann:

- **🧠 Projekt-Agent ableiten** — erzeugt aus der Beschreibung einen fachkundigen
  Planer-Experten, der alle weiteren KI-Vorschläge steuert.
- **🪄 KI-Projekt generieren** — das lokale LLM erstellt einen **kompletten Plan**
  (Aufgaben, Abhängigkeiten, Dauern, Ressourcen). IDs und Verknüpfungen werden
  automatisch geprüft, Start-/Endaufgaben markiert. Über das Feld **Aufgaben**
  bestimmst du die gewünschte Anzahl (keine feste Obergrenze mehr).
  > **Hinweis:** Bei vielen Aufgaben (> 30) stoßen kleine lokale Modelle an ihre
  > Grenzen – es kommt eine Rückfrage, und das Ergebnis kann unvollständig sein.
  > Für große Pläne ein **größeres/leistungsfähigeres Modell** verwenden oder den
  > Plan **in Phasen** generieren. Liefert das Modell zu wenig, erscheint eine
  > Warnung.
- **✨** an einer Aufgabe — schlägt **Vorgänger und Nachfolger** vor (mit Dauer und
  Ressourcen). Du wählst per Häkchen, was übernommen wird — nichts wird automatisch
  eingefügt. Übernommene Aufgaben werden direkt verknüpft.
- **📝 Detaillieren** — verfeinert die **gewählte Aufgabe** selbst (präzisere
  Bezeichnung, realistische Dauer, kurze Detailbeschreibung, passende Ressourcen)
  **und** schlägt Vorgänger/Nachfolger vor. Im Fenster kannst du alle Vorschläge
  **anhaken und editieren** (Namen, Dauern); **Übernehmen** schreibt die Details in
  die Aufgabe und legt die ausgewählten Vorgänger/Nachfolger verknüpft an.
- **🔁 Ersetzen** — tauscht eine Aufgabe aus: entweder **durch eine bestehende**
  (z. B. T4 durch T10 — deren Verknüpfungen werden übertragen, das Original entfernt)
  oder **durch eine neue** (Name/Dauer eingeben — ID und Verknüpfungen bleiben).
- **🗑 Löschen** — entfernt die Aufgabe und **verbindet ihre Vorgänger direkt mit
  ihren Nachfolgern**, damit die Ablaufkette nicht reißt.
- **🏁 / 🛑** markiert eine Aufgabe als **Projektanfang** bzw. **-ende**; die KI
  schlägt dann dort keine Vorgänger bzw. Nachfolger mehr vor.

### Struktur bearbeiten, sortieren, einfügen

- Die Spalte **#** zeigt die **Ablaufreihenfolge** (berechnet) — unabhängig von der
  **ID**, die als stabiler Bezug in den Verknüpfungen dient. Eine ID kannst du
  umbenennen; alle Verweise werden automatisch mitgeführt.
- **✨ Mach schön** prüft alle Verknüpfungen auf Konsistenz (macht sie symmetrisch,
  entfernt ungültige), **sortiert die Tabelle nach Ablaufreihenfolge** und zeichnet
  den Netzplan neu.
- **➕ Dazwischen** fügt einen **neuen Vorgang zwischen zwei Aufgaben** ein: A und B
  wählen → die KI liest beide und schlägt passende Zwischenvorgänge vor. Nach Auswahl
  wird `A → neu → B` verdrahtet und eine direkte Kante A→B aufgelöst.

### Ressourcen & Kosten

- Spalte **Ressourcen / Kosten**: auf den Button klicken → Detailfenster mit
  **Typ (Mensch/Hardware/Software), Name, Menge, Zeit (h), Kostensatz**. Summen je
  Ressource und je Aufgabe werden automatisch berechnet.
- Über der Tabelle zeigt der **Rollup** Gesamtkosten, Personenstunden und die
  Kosten auf dem kritischen Pfad.
- **📥 Katalog** importiert einen Ressourcen-Katalog (CSV: `Typ;Name;Satz`). Die
  Auswahl **„frei / Katalog: erweitern / Katalog: strikt"** steuert, ob die KI
  frei Ressourcen wählt, den Katalog bevorzugt aber zukaufen darf, oder sich
  **strikt** an den Katalog (inkl. Kostensätze) hält.
- **📤 Ressourcen** exportiert die im Plan verwendeten Ressourcen als CSV
  (Typ, Name, Satz, Stunden, Kosten — mit Summenzeile).
- Im Ressourcen-Fenster kannst du je Ressource eine **Lieferzeit (Tage)** angeben.

### 📅 Bestellplan — wann wird welche Ressource gebraucht?

- Setze oben das **Projektstart-Datum** (🗓 Start), damit aus den Tagen echte
  Kalenderdaten werden (sonst „Tag X").
- Mit der Checkbox **Arbeitstage** werden die Tage als Arbeitstage gerechnet
  (Wochenenden werden übersprungen).
- **📅 Bestellplan** zeigt pro Ressource: **benötigt ab** (frühester Start der
  nutzenden Aufgabe) und **bestellen bis** (Bedarf − Lieferzeit). Liegt der
  Bestelltermin vor Projektstart, wird er **⚠ rot** markiert (früh bestellen).
  Über **⬇ Bestellplan CSV** lässt sich die Liste exportieren.
- Werden dieselbe Person/Hardware in **überlappenden Zeitfenstern** gebraucht,
  listet das Fenster diese **Ressourcenkonflikte (Doppelbelegung)** auf.

> **Warnzeile:** Über der Tabelle erscheint rot eine Warnung, wenn die
> Verknüpfungen einen **Zyklus** bilden (dann ist die CPM-Rechnung unzuverlässig –
> „✨ Mach schön" oder Verknüpfung auflösen) oder wenn **Ressourcenkonflikte**
> bestehen.

### 🔬 Tätigkeits-Recherche → interaktiver Plan

Jede Tätigkeit lässt sich **wissenschaftlich recherchieren**. Ablauf je Tätigkeit:
Analyse → **adaptiver Experten-Agent** → **Web-Recherche** → **Markdown-Dossier** →
Einbettung in ein **plan-spezifisches RAG** (wird automatisch als „Plan: …" angelegt)
→ Verlinkung mit der Tätigkeit.

- **🔬** in der Aufgabenzeile recherchiert die einzelne Tätigkeit. Danach erscheint
  **📄** zum Ansehen des Dossiers; der 🔬-Button wird markiert (erledigt).
- **🔬 Alle recherchieren** (Toolbar) recherchiert alle noch offenen Tätigkeiten
  nacheinander, mit Fortschrittsanzeige und **⏹ Stop** (abbrechbar; bereits
  recherchierte werden beim nächsten Lauf übersprungen → fortsetzbar).
- So entsteht **eine Plan-Wissensdatenbank** mit je einem Dossier pro Tätigkeit
  (100 Tätigkeiten = 100 Dossiers in einem RAG). Diese Datenbank kannst du im Chat
  über den **📚-Umschalter** auswählen — der Plan wird dadurch „interaktiv".

> **Hinweis (Dauer):** Jede Tätigkeit bedeutet eine Websuche + eine KI-Synthese.
> Bei vielen Tätigkeiten dauert „Alle recherchieren" entsprechend lange (lokal auf
> kleiner Grafikkarte ggf. sehr lange) — der Stapellauf ist daher abbrech- und
> fortsetzbar. Die Recherche erfolgt im **Wissenschaftsmodus** (quellengebunden).

---

## 11. Matrix-Recherche

Tab **📊 Matrix** — Recherche als Tabelle:

- **Spalte 1** = Themen, **Kopfzeile ab Spalte 2** = Suchprompts.
- **Agent je Spalte:** Unter jedem Suchprompt lässt sich ein **eigener Agent**
  wählen (nur als **Favorit** markierte Agenten erscheinen hier). So kann jede
  Spalte anders arbeiten — z. B. ein **Firmenagent** zur Messebesuch-Vorbereitung,
  der **Rechercheur & Bewerter** (sucht *und* bewertet Relevanz/Schlüssigkeit) oder
  der **Halluzinationsprüfer** (prüft Angaben auf Plausibilität). *„Kein Agent"* =
  einfache Websuche.
- Zelle anklicken = einzeln ausführen; **▶ Alle ausführen** = der Reihe nach.
- Bei vielen Spalten lässt sich die Tabelle **horizontal scrollen**.
- **Live-Speicherung:** Ergebnisse (samt Agent-Zuordnung je Spalte) werden nach
  jeder Zelle automatisch im Browser gesichert — bei einem Absturz geht nichts
  verloren. Eine kurze Anzeige *„💾 Gespeichert"* bestätigt dies.
- Zellinhalte werden als **Markdown inkl. LaTeX-Formeln** dargestellt.
- **CSV-Import/-Export** und **📋 XLSX**-Export verfügbar.
- **📚 In Wissensdatenbank** übernimmt die gesamte Matrix (Themen × Fragen samt
  Antworten) als Dokument in eine gewählte Wissensdatenbank.

> **Wissenschaftsmodus:** Auch die Matrix-Recherche läuft immer im
> Wissenschaftsmodus (quellengebunden, keine erfundenen Inhalte).

---

## 12. Code-Tab (IDE + JSON-Editor)

Tab **💻 Code** — oben zwei Untertabs: **IDE** und **JSON-Editor**.

### Untertab „IDE"

Erzeugt und führt **HTML5-Canvas-Programme** aus, z. B. für Toleranzanalysen oder
Diagramme. Für Techniker gedacht, **ohne Programmierkenntnisse** nutzbar.

### KI-Assistent (Haupteinstieg)

Im blau hinterlegten Feld beschreiben, was das Programm zeigen soll, dann
**▶ Code erstellen** klicken. Die KI schreibt den Code, übernimmt ihn in den Editor
und führt ihn sofort aus. Welches Modell der IDE-Assistent nutzt, legst du im
**Profil → 🧠 Modelle → Programmieren** fest (leer = `ministral-3:3b`).

> *„Zeige ein Balkendiagramm der Zugfestigkeit für Stahl, Alu und Titan"*
> *„Erstelle eine Toleranzanalyse für drei Bauteile"*

### Interaktive Eingabefelder

Die generierten Programme zeigen unter dem Canvas **Eingabefelder**. Werte ändern
→ die Darstellung wird sofort neu berechnet (z. B. Federrate, Toleranzen).

### Vorschau & Konsole

Das Programm läuft im rechten Vorschaufenster (Canvas füllt den Bereich
vollständig). Meldungen und Fehler erscheinen in der **Konsole** darunter.

### Automatische Fehlerreparatur

Tritt ein Fehler auf, erscheint **🔧 Fehler automatisch beheben** — ein Klick
schickt Code und Fehler an die KI, die eine korrigierte Version liefert.

### Speichern & Beispiele

- **💾 Speichern** legt das Programm ab (erscheint als Chip in der Editor-Leiste).
- Beispiel-Vorlagen: **📐 Toleranzanalyse**, **📈 Federkennlinie**, **📄 Leere Vorlage**.

---

## JSON-Editor

Untertab **🧩 JSON-Editor** (im Tab **💻 Code**) — ein einfacher Editor, um
JSON-Dateien zu öffnen, zu prüfen und zu **reparieren**, auch ohne
Programmierkenntnisse (z. B. eine beschädigte Export- oder Konfigurationsdatei).

- **📂 Datei öffnen** lädt eine `.json`-Datei in den Editor (mit Zeilennummern).
- **Live-Prüfung:** Während des Tippens zeigt die Statusanzeige *„✓ Gültiges JSON"*
  oder den **Fehler mit Zeile und Spalte** an.
- **✨ Formatieren** rückt das JSON sauber ein; **✓ Prüfen** prüft auf Knopfdruck.
- **💾 Herunterladen** speichert die korrigierte Datei (der Browser kann die
  Originaldatei nicht direkt überschreiben — du lädst die reparierte Version herunter
  und ersetzt damit das Original).

---

## 13. Diagnose-Logger

Tab **📋 Logs** — ein zuschaltbares Protokoll zur Verbesserung und Fehlersuche.

- **▶ Logging aktivieren** schaltet die Aufzeichnung ein (Status: 🔴 Aktiv).
- Protokolliert: Chat-Anfragen (Modell, Dauer, Tools), Tool-Aufrufe, Exporte,
  Tab-Wechsel und Frontend-Fehler.
- **Filter** nach Typ, **↺ Aktualisieren**, **⬇ Download** (als `.log`),
  **✕ Leeren**.
- Standardmäßig **aus** — nur bei Bedarf aktivieren.

---

## 14. Agenten

Agenten sind Profile mit eigenem System-Prompt, Tool-Set und optionalem Modell.

- **Nur Favoriten sind wählbar:** Überall, wo ein Agent ausgewählt wird (Sidebar,
  **Matrix** je Spalte, **Dokumentengenerator**), erscheinen **nur als Favorit
  markierte** Agenten. Im Tab **🤖 Agenten** schaltest du den **⭐-Stern** auf einer
  Agenten-Karte um — favorisierte Agenten landen sofort in allen Auswahllisten.
- **Aktivieren:** Dropdown **Agent** in der Sidebar (Favoriten). *„— Kein Agent —"* = Standard.
- **Schnellauswahl im Chat:** In der Chatbox setzen die Buttons **📊 Präsentation**
  und **💻 Programmieren** direkt den Präsentations- bzw. Programmier-Agenten
  (erneutes Klicken hebt die Auswahl wieder auf).
- **🧠 Adaptiver Agent** (im Dropdown): analysiert zuerst die gestellte Frage,
  leitet daraus automatisch einen **fragespezifischen Experten** ab und lässt diesen
  die Antwort erzeugen. Über der Antwort wird die gewählte Experten-Rolle angezeigt.
  Kostet einen kurzen Vorab-Durchlauf des Modells (etwas mehr Latenz), liefert dafür
  ohne manuelle Agentenwahl eine passend zugeschnittene Antwort.
- **Erstellen:** Tab **🤖 Agenten** → **＋ Neuer Agent**, Formular ausfüllen
  (Name, Icon, Beschreibung, System-Prompt, Modell, Tools). Der Button
  **System-Prompt generieren** erzeugt einen Vorschlag per KI.
- Agenten werden unter sprechenden Dateinamen gespeichert (`data/agents/`).

Standard-Agenten: Recherche-, Ingenieur-, Analyse-, Präsentations-,
Werkstoff-, Requirements-Agent, Kanban-Coach u. a.

**Dokument-Agenten:** Agenten der Kategorie **„Dokumentation"** dienen als Vorlagen
im **Dokumentengenerator** (Tab 📄 Dokumente) — lege z. B. einen „Förderantrag"-Agenten
mit passendem System-Prompt an, um daraus später Anträge zu erzeugen.

---

## 15. Gespräche verwalten

- **Neu:** **＋ Neues Gespräch** oder **Ctrl + K**.
- **Laden:** Eintrag in der Sidebar anklicken.
- **Umbenennen:** Doppelklick auf den Titel oder **✏️**.
- **Löschen:** **🗑** beim Überfahren des Eintrags.
- **Suchen:** Suchfeld oben (Volltext, ab 2 Zeichen).
- **Exportieren/Importieren:** einzelnes Gespräch als JSON, oder **alle als ZIP**.
- **Komprimieren** (🗜): langes Gespräch von der KI zusammenfassen lassen — manuell
  pro Gespräch, oder **automatisch** (siehe Profil → Automatische Komprimierung).

---

## 16. Nutzerprofil & Projekte

### Profil (**👤 Profil bearbeiten**)
Vorname, Nachname, Firma, Abteilung, Position, Kontakt, Standard-Projekt.
Diese Daten erscheinen in der **Fußzeile** aller Exporte und auf dem
Präsentations-Deckblatt.

### 🎨 Modus & Branding (im Profil)
- **Modus** wählen: **Maschinenbau** (Blau), **KI** (Grün), **Soziales** (Braun),
  **Marketing** (Rot), **Finanz** (Grau), **Geschäftsführung** (Gelb) oder ein
  **eigener Modus** (Violett). Der Modus ändert sofort das **Farbschema** der
  gesamten Oberfläche und der Folien.
- **Eigener Modus (Violett):** frei konfigurierbar — du vergibst einen **Namen**,
  eine **Fachbrille** (frei formulierter Kontext-Text, der den KI-Antworten
  vorangestellt wird) und optionale **Stichwörter**. Ohne Stichwörter greift die
  Fachbrille bei jeder Frage; mit Stichwörtern nur bei thematisch passenden Fragen.
- **„Modus prägt die KI-Antworten"**: ist der Haken gesetzt, bekommt die KI eine
  fachliche Brille passend zum Modus (abschaltbar, falls rein farbliche Umschaltung
  gewünscht ist).
- **„Keine Modi verwenden (LLM pur)"**: schaltet **sämtliche** automatischen
  Vorgaben ab — Modus-Brille, Persona, Anti-Halluzinations-Grundregel sowie Formel-
  und Zitatregeln. Das Modell antwortet dann ohne jede Voreinstellung („pur"). Ein
  ausdrücklich gewählter Agent und aktive Wissensdatenbanken bleiben davon unberührt.
- **Logo & Vorlagenbilder hochladen** (werden automatisch auf die Sollgröße skaliert):
  - **Logo** – 512×512 px, PNG mit Transparenz (Sidebar + Dokumente)
  - **Vorlagen-Deckblatt** – 1920×1080 px, JPG (Präsentations-Titelfolie)
  - **Vorlagen-Kopfzeile** – 1920×240 px, PNG/JPG (Banner über Folien/Dokumenten)
  Ohne Upload bleibt die Oberfläche schlicht (Schriftzug „AI_Framework_Thomas") und Folien
  werden ohne Branding-Bild erzeugt.

### 🗜 Automatische Komprimierung (im Profil)
Lange Chatverläufe können automatisch zusammengefasst werden, damit das
Kontextfenster (und damit der VRAM-Bedarf) nicht überläuft:
- **Aktiv** ein-/ausschalten.
- **Überlauf ab** (Zeichen): Überschreitet der aktuelle Verlauf diese Größe, wird er
  nach der nächsten Antwort komprimiert.
- **Leerlauf nach** (Minuten): Bei längerer Inaktivität wird ein langer Verlauf
  ebenfalls komprimiert.

Komprimiert wird stets das **gerade geöffnete** Gespräch; ein Hinweis-Toast zeigt
das Ergebnis (z. B. „💾 Verlauf automatisch komprimiert (12 → 4 Nachrichten)"). Die
Zusammenfassung ersetzt die älteren Nachrichten; die letzten Austausche bleiben erhalten.

### Projekte (**📁 Projekte**)
Projekte mit Nummer, Name und Beschreibung anlegen. Den aktiven Chat über das
Dropdown **„Aktuellen Chat Projekt zuordnen"** zuweisen. Über den Projekt-Filter
oben in der Sidebar die Gesprächsliste nach Projekt einschränken.

---

## 17. Exportieren

| Quelle | Button | Ergebnis |
|--------|--------|----------|
| Chat | **📝 DOCX** (Toolbar) | Gespräch als Word-Dokument |
| Canvas | **📊 PPTX** | Präsentation im Corporate-Design |
| Canvas | **📋 XLSX** | Tabelle als Excel |
| Recherche | **📄 Als Dokument** | Bericht als DOCX mit Kopfzeile |

- Alle Exporte tragen in der **Fußzeile**: *Name · Firma · KI generierter Inhalt*.
- KI-erzeugte Antworten werden im Dokument zusätzlich mit **„▶ Von KI generiert"**
  gekennzeichnet.
- **Dokument-Kopfzeile als Bild** nur bei Recherche-Berichten; reine
  Chat-Exporte bleiben ohne Bild-Kopfzeile.

---

## 18. Backup & Wiederherstellung

- **💾 Backup** (Sidebar) lädt **alle Nutzerdaten** als ZIP herunter: Profil
  (inkl. Modell-Rollen), Projekte, Gespräche, Pläne, Agenten (inkl. Favoriten),
  gespeicherte IDE-Programme, die **Branding-Bilder** (Logo/Deckblatt/Kopfzeile)
  sowie die **RAG-Wissensdatenbanken** (inkl. Dokumente und Embeddings).
- **📥 Restore** stellt aus einem Backup-ZIP wieder her: Profil und Branding-Bilder
  werden überschrieben; bereits vorhandene Pläne, Agenten und Wissensdatenbanken
  werden übersprungen (keine Duplikate); Gespräche erhalten neue IDs. Der Bestätigungs-
  hinweis zeigt, wie viel je Kategorie wiederhergestellt wurde.

---

## 19. Modelle & VRAM

Das aktive Modell wird unten in der Sidebar gewählt. Standardmäßig installiert ist
**nur**:

| Modell | Rolle |
|--------|-------|
| `ministral-3:3b` | Standardmodell für alles (auch Vision) |
| `gemma4:e2b` | Alternatives kompaktes Chat-Modell |
| `nomic-embed-text` | RAG-Embeddings (klein; läuft auf kleinen Karten bewusst auf der CPU) |

### Drei Modell-Rollen im Profil

Unter **👤 Profil → 🧠 Modelle** weist du je Einsatzzweck ein Modell zu:

| Rolle | Verwendet für |
|-------|---------------|
| **Allgemein** | normaler Chat (Standard der Sidebar-Auswahl) |
| **Programmieren** | KI-Assistent im Code-Tab (IDE) und der Programmier-Agent |
| **Wissenschaftlich** | Recherche und der wissenschaftliche/quellengebundene Modus |

Leer = `ministral-3:3b`. **Andere Modelle vorher laden** (sie werden bei Bedarf
nachgeladen): `ollama pull <modell>` — danach erscheinen sie in den Auswahllisten.

**Nur ein Modell gleichzeitig im Speicher:** AI_Framework_Thomas entlädt beim Modellwechsel
automatisch das zuvor genutzte Modell, bevor das neue lädt. Dadurch genügen
**6 GB VRAM**, auch wenn die Rollen unterschiedliche Modelle verwenden.

In den Auswahllisten erscheinen **alle** in Ollama installierten Modelle;
`allowed_models` in `config.json` legt nur noch die Reihenfolge fest.

---

## 20. Tastenkürzel

| Kürzel | Aktion |
|--------|--------|
| **Enter** | Nachricht senden |
| **Shift + Enter** | Neue Zeile |
| **Ctrl + K** | Neues Gespräch |
| **Strg + Enter** (IDE) | Code ausführen |
| **Strg + S** (IDE) | Programm speichern |

---

## 21. Technische Hinweise

- **Lokal:** Gespräche in `data/ai_framework_thomas.db` (SQLite + Volltextindex), Agenten/Pläne/
  Programme als JSON in `data/`, Uploads temporär in `data/uploads/`.
- **Agentic Loop:** Bis zu 8 Iterationen — die KI kann mehrfach suchen, rechnen
  und Ergebnisse kombinieren, bevor sie antwortet.
- **Tool-Kompatibilität:** Native `tool_calls` und Inline-Formate
  (`<call_tool>`, `<tool_call>`) werden erkannt — breite Modellunterstützung.
- **Berechnungs-Sandbox:** eingeschränktes `exec()` ohne Datei-/Netzwerkzugriff.
- **VRAM-Schutz:** zentrale Serialisierung aller Modell-Aufrufe (siehe Abschnitt 19).

Für tiefergehende Architektur siehe **docs/ENTWICKLUNG.md**.

---

## 22. Deinstallation

**Automatisch:** `uninstall.bat` doppelklicken und den Anweisungen folgen.
Entfernt die virtuelle Umgebung (`venv/`), optional die Daten (`data/`) und
optional Ollama. Der Programmordner bleibt und kann manuell gelöscht werden.

**Manuell:** laufende `python.exe` beenden → `venv\` löschen → optional `data\`
löschen (enthält alle Gespräche, Pläne und Programme!) → Ordner entfernen →
optional Ollama deinstallieren.

---

*AI_Framework_Thomas — Lokal & privat · Powered by Ollama*
