# 🤖 AI_Framework_Thomas — Bedienungsanleitung

**Version:** 3.3 · **Stand:** Juli 2026

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
- [Mail-Bearbeitung (Beta)](#mail-bearbeitung)
10. [Medizin-Tab](#10-medizin-tab)
11. [Mathe-Tab](#11-mathe-tab)
- [Verzeichnis-Analyse-Tab](#11a-verzeichnis-analyse-tab)
- [Morphologischer-Kasten-Tab](#11b-morphologischer-kasten-tab)
- [Postfach-Tab (PST-/Mail-Auswertung)](#11c-postfach--pst-mail-auswertung)
- [Patente-Tab (Patent-Recherche)](#11d-patente--patent-recherche)
- [Angebot & Rechnung](#11e-angebot--rechnung)
- [Arbeitszeugnisse](#11f-arbeitszeugnisse)
- [Variantenvergleich (🧮 Varianten)](#11g-variantenvergleich--varianten)
- [To-Do mit Wissensgraph (✅ To-Do)](#11h-to-do-mit-wissensgraph--to-do)
- [Transkription — Sprache zu Text (🎙 Transkription)](#11i-transkription--sprache-zu-text--transkription)
- [Geheim-Modus — alles lokal](#11j-geheim-modus--alles-lokal)
- [Sprachausgabe — Antworten vorlesen (🔊)](#11k-sprachausgabe--antworten-vorlesen-)
- [Bildgenerierung — Bilder aus Text (🎨)](#11l-bildgenerierung--bilder-aus-text-)
12. [Planer (Netzplan / CPM)](#12-planer-netzplan--cpm)
13. [Matrix-Recherche](#13-matrix-recherche)
14. [Code-Tab (IDE + JSON-Editor)](#14-code-tab-ide--json-editor)
- [JSON-Editor](#json-editor)
15. [Diagnose-Logger](#15-diagnose-logger)
16. [Agenten](#16-agenten)
- [Bewertungs-Jurys](#16a-bewertungs-jurys-)
17. [Gespräche verwalten](#17-gespräche-verwalten)
18. [Nutzerprofil & Projekte](#18-nutzerprofil--projekte)
19. [Exportieren](#19-exportieren)
20. [Backup & Wiederherstellung](#20-backup--wiederherstellung)
21. [Modelle & VRAM](#21-modelle--vram)
22. [Tastenkürzel](#22-tastenkürzel)
23. [Technische Hinweise](#23-technische-hinweise)
24. [Aktualisieren (Update)](#24-aktualisieren-update)
25. [Deinstallation](#25-deinstallation)

---

## 1. Was ist AI_Framework_Thomas?

AI_Framework_Thomas ist ein lokales, datenschutzfreundliches KI-Interface — vollständig auf dem
eigenen Rechner. Alle Daten bleiben lokal; nach außen geht nur die Websuche
(DuckDuckGo), und das auch nur, wenn sie aktiviert ist.

**Kernfunktionen:** Chat mit lokalen Modellen · Websuche · Berechnungen ·
Präsentationen & Tabellen · Recherche mit Quellen · Netzplanung · Code-IDE ·
spezialisierte Agenten · Dokument-Export mit Firmen-Design · Medizin-Assistent
mit Patienten-Akten · Mathematik-Workspace mit Plots und LaTeX-Export ·
Postfach-Auswertung (PST/Mail) als Wissensgraph · Patent-Recherche · Angebote &
Rechnungen (§14 UStG) · qualifizierte Arbeitszeugnisse.

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

Oben verläuft die **Tab-Leiste** (umbruchfähig) mit folgenden Bereichen:

| Tab | Funktion |
|-----|----------|
| 💬 Chat | Haupt-Chat-Ansicht |
| 🖼️ Canvas | Präsentationen & Tabellen anzeigen (Export PPTX/PDF/XLSX) |
| 📄 Dokumente | Dokumentengenerator — Dokument oder Präsentation erzeugen |
| 🔬 Recherche | Aspektbasierte Recherche mit Quellen |
| 📊 Matrix | Recherche-Tabelle |
| 🗂️ Planer | Netzplan / Kritischer Pfad |
| 💻 Code | Code-IDE + JSON-Editor |
| 🤖 Agenten | Agenten erstellen und bearbeiten |
| 📚 RAG | Eigene Wissenssammlungen für die KI |
| 🩺 Medizin | Medizin-Assistent mit Patienten-RAG *(optional, im Profil konfigurierbar)* |
| 🔢 Mathe | Mathematik-Workspace: Plots, SymPy, LaTeX/PDF-Export *(optional)* |
| 📁 Verzeichnis | Ordner analysieren, Index schreiben, Personendaten anonymisieren *(optional)* |
| 🧩 Morph-Kasten | Morphologischer Kasten (Zwicky-Box) mit KI *(optional)* |
| ⚖️ Jury | Dokumente schreiben/bearbeiten und von einer Jury prüfen lassen *(optional)* |
| 📧 Mail | Postfach (IMAP/POP3) read-only: filtern → bis zu 4 Aktionen (Beta) *(optional)* |
| 📮 Postfach | PST-/Mail-Dateien einlesen & als Wissensgraph auswerten — nur lokal *(optional)* |
| ⚖️ Patente | Patent-Recherche, Fallakten & KI-Analyse *(optional)* |
| 🧾 Angebot / Rechnung | Angebote & Rechnungen mit exakter Betragsrechnung (§14 UStG) *(optional)* |
| 📜 Zeugnisse | Qualifizierte Arbeitszeugnisse in Zeugnissprache *(optional)* |
| 🧮 Varianten | Gewichteter Variantenvergleich (Paarvergleich/AHP) mit KI-Hilfe, Schnellvergleich (Wischtechnik) & Auto-Tabelle aus Problembeschreibung *(optional)* |
| ✅ To-Do | KI-Aufgabenliste (Besprechung/Projekt) mit Wissensgraph (2D & rotierende 3D-Kugel) *(optional)* |
| 📋 Logs | Diagnose-Protokoll *(optional)* |

> **Optionale Tabs:** Die Tabs **RAG**, **Code**, **Medizin**, **Mathe**, **Verzeichnis**,
> **Postfach**, **Patente**, **Angebot/Rechnung**, **Zeugnisse**, **Varianten**, **To-Do**,
> **Morph-Kasten**, **Jury**, **Mail** und **Logs** können im Profil ausgeblendet werden
> (→ [Abschnitt 18 – Tab-Sichtbarkeit](#18-nutzerprofil--projekte)).
> Sie bleiben jederzeit wieder einschaltbar.

### Sidebar (links)

- **Logo** (oben; im Profil hochladbar, sonst Schriftzug „AI_Framework_Thomas")
- **＋ Neues Gespräch**
- **🔍 Suche** über alle gespeicherten Gespräche
- **Gesprächsliste** (Klick lädt, Doppelklick benennt um)
- **Projekt-Filter** (oben) und unten: **Agent**,
  **Chat→Projekt-Zuordnung**
  > Es gibt **keinen Modell-Auswahlkasten** mehr in der Seitenleiste (auch nicht im
  > Planer, in Medizin oder Matrix). Welches Modell verwendet wird, stellst du
  > zentral unter **👤 Profil bearbeiten** pro Rolle ein (Allgemein / Programmieren /
  > Wissenschaftlich / Medizin).
- Buttons: **📁 Projekte**, **🤖 Agenten**, Gespräch-Im/Export,
  **💾 Backup**, **📥 Restore**, **👤 Profil bearbeiten**

---

## 4. Chatten

1. Text eingeben, **Enter** sendet (**Shift+Enter** = neue Zeile).
2. Die KI antwortet per Streaming (wortweise in Echtzeit).

**🔍 Websuche** (Toolbar) ist standardmäßig **aus** — die KI antwortet dann nur aus
Modellwissen (alle Daten bleiben lokal). Einmal anklicken aktiviert die Suche, dann
recherchiert die KI bei Bedarf selbstständig. Der Schalter bleibt nur für die laufende
Sitzung aktiv und startet bei jedem Neuladen wieder ausgeschaltet.

Während der Antwort werden Tool-Aufrufe angezeigt (Suche, Berechnung,
Präsentation …).

**⚡ Schnell-Agent mit `/`:** Beginnst du eine Nachricht mit einem Schrägstrich und dem
Agentennamen (z. B. `/Mathe Löse x²−4=0` oder `/Hilfe Wie lege ich eine Jury an?`), läuft
**nur diese eine Frage** über den passenden Agenten — der Agenten-Auswahlschalter in der
Seitenleiste bleibt unverändert. Über der Antwort erscheint ein Hinweis „➜ Agent: … (nur
diese Frage)". Wird kein passender Agent gefunden, kommt ein kurzer Hinweis und die
Nachricht wird normal gesendet.

**🔎 Deepdive mit `/dd` und `/ddd`:** Vertieft die **letzte Antwort** automatisch. Tippe
`/dd10` (oder `/deepdive10`): die KI leitet **10 Vertiefungsfragen** ab und arbeitet sie
**der Reihe nach** ab — jede Frage wird als eigene Suchanfrage genutzt und einzeln
beantwortet (Web­suche je nach 🔍-Schalter, plus die im Chat aktiven Wissensdatenbanken).
Die Zahl ist frei wählbar (z. B. `/dd5`, `/dd20`; ohne Zahl = 5).
`/ddd10` (oder `/deepdivedocument10`) macht dasselbe **als Dokument**: die letzte Antwort
wird zum **Vorwort**, jede Frage zu einem **Kapitel** mit der Antwort als Inhalt. Das
fertige Dokument öffnet sich im **Dokumente-Tab** und ist dort als **DOCX/PDF**
exportierbar.

**🧭 Strategie & Einsatzplan mit `/plan`:** Diskutiere dein Vorhaben zuerst frei im
Chat (z. B. „Vergleich von drei KI-Tools nach Kosten, Datenschutz, Zeitplan und
Hardware"), dann tippe **`/plan`**. Aus dem **bisherigen Gesprächsverlauf** baut die KI
in einem Zug eine **Vorschau** mit vier Bausteinen: eine **Strategie** (Ziel, Optionen,
Bewertungskriterien, Vorgehen, Risiken, Meilensteine), die nötigen **Beratungs-Agenten**
(z. B. Kosten-, Datenschutz-, Zeitplan-, Hardware-Experte), einen **Einsatz- und
Ressourcenplan** (Phasen, Aufgaben, Dauern, Rollen, Kosten) und eine **Bewertungs-Jury**.
Optional kannst du Randbedingungen anhängen: `/plan Budget 10k €, Start im Q3`. Mit
einer **Zahl direkt am Befehl** legst du die gewünschte Aufgabenzahl im Einsatzplan
fest, z. B. **`/plan50`** (4–60; ohne Angabe 12). Der 🔍-Schalter und die aktiven
Wissensdatenbanken erden **Strategie *und* Plan** mit echten Quellen — lege ein
Strategiepapier in eine **Wissensdatenbank** und wähle sie aus, dann werden die Aufgaben
auf dessen Inhalt gestützt.
**Feste Agenten erzwingen:** Hängst du vorhandene Agenten als `/Kürzel` an, werden sie
**auf jeden Fall** als Berater und Jury-Mitglied verwendet (die KI ergänzt nur fehlende
Rollen) — z. B. `/plan Einsatz Copilot /dsgvo /tisax` nutzt zwingend deinen DSGVO- und
deinen TISAX-Agenten. Diese festen Agenten sind in der Vorschau mit **📌** markiert und
werden beim Anlegen **nicht doppelt** erzeugt, sondern direkt der Jury zugeordnet. Ein
nicht gefundenes Kürzel wird als normaler Text behandelt (Hinweis in der Vorschau).
**Es wird zunächst nichts gespeichert** — erst mit **„✅ Alles anlegen"** wird ein
**Projekt** (benannt nach dem Plan) angelegt und damit **alles verknüpft**: die
Agenten, der Plan (im **Planer**) und die Jury (im **Jury-Tab**) erhalten die Projekt­-
Zuordnung, und die aktuelle Unterhaltung wird dem Projekt zugewiesen. Die neu erzeugten
Beratungs-Agenten gehören **ausschließlich diesem Projekt** (projekt-eigene „Skills"):
Sie erscheinen **nicht** im globalen Agenten-Verzeichnis, sondern nur im **Projekt-Dialog**,
und werden beim Löschen des Projekts mitentfernt. Danach führen
Knöpfe direkt dorthin (u. a. **📁 Projekt** zur Projektverwaltung). Die Strategie lässt sich zusätzlich **in die Dokumente** oder **in die
Wissensdatenbank** übernehmen. *Hinweis:* Kosten- und Rechtsangaben sind eine
Entscheidungs­hilfe — für DSGVO/EU AI Act/Preise echte Quellen prüfen, kein Rechtsrat.

**📝 Feedback mit `/-` und `/+`:** Während der Arbeit kannst du direkt im Chat
festhalten, was **nicht funktioniert** oder **besser werden** sollte – ohne den
Gedankengang zu unterbrechen.

- **`/- <Text>`** meldet ein **Problem / einen Fehler** (etwas ist schlecht oder
  funktioniert nicht), z. B. `/- Der Plan-Import ignoriert die Aufgabenzahl`.
- **`/+ <Text>`** notiert eine **Idee / einen Verbesserungsvorschlag**, z. B.
  `/+ Export der Matrix-Zellen auch als CSV`.

Diese Einträge werden **nicht an die KI gesendet**, sondern als Markdown-Protokoll in
`data/feedback.md` gesammelt (mit Zeitstempel, Art-Symbol 🔴/🟢 und – falls vorhanden –
der Unterhaltungs-ID). Im Chat erscheint eine kurze Bestätigung mit der Gesamtzahl der
Einträge. So entsteht über die Zeit eine gepflegte **Fehler- und Ideenliste**, die du
später auswerten oder ins Entwicklungs-Backlog übernehmen kannst.

### Chat-Befehle (Slash-Befehle) im Überblick

Alle Befehle stehen **am Zeilenanfang** einer Chat-Nachricht.

> **Tipp – Befehls-Vorschau:** Sobald du in der Chatbox ein **„/"** tippst, erscheint
> direkt darüber eine **graue Liste der verfügbaren Befehle** mit Kurzbeschreibung.
> Mit **↑/↓** wählen, mit **Tab** oder **Klick** übernehmen, **Esc** schließt sie wieder.

| Befehl | Wirkung |
|--------|---------|
| `/<Agentname> <Frage>` | **Schnell-Agent:** nur diese eine Frage läuft über den genannten Agenten (z. B. `/Mathe Löse x²−4=0`). Der Selektor bleibt unverändert. |
| `/such <Begriff>` · `/suche` · `/finde` · `/search` | **Erweiterte Suche:** die KI erzeugt **alternative Suchbegriffe** (Synonyme, Fach-/Umgangssprache, engl. Begriffe) als anklickbare Chips, durchsucht damit das Web und fasst die Treffer **mit Quellen** zusammen. Hilfreich, wenn man den treffenden Fachbegriff nicht kennt. |
| `/frag <Aufgabe>` | **Rückfragen-Maske:** die KI prüft, ob ihr Infos fehlen, und zeigt bei Bedarf eine **dynamische Eingabemaske** (Text-, Einfach- und Mehrfachauswahl). Deine Antworten werden an die Aufgabe gehängt und dann normal beantwortet. Auch in **Medizin** und **Mathe** verfügbar. |
| `/bild <Beschreibung>` · `/image` | **Bildgenerierung:** erzeugt aus deiner Beschreibung ein Bild (lokaler Stable-Diffusion-Server oder ein API-Modell — siehe unten). Alternativ der **🎨 Bild**-Haken in der Toolbar: die nächste Nachricht wird dann als Bild-Prompt behandelt. |
| `/bildhelp` · `/imagehelp` | **Geführter Bild-Dialog:** ein Formular fragt **Motiv, Stil, Kameraperspektive, Beleuchtung, Seitenverhältnis** und einen optionalen **Negativ-Prompt** ab und baut daraus den Prompt. |
| `/dd[N] [Zusatz]` · `/deepdive[N]` | **Deepdive:** vertieft die letzte Antwort mit *N* Folgefragen (ohne Zahl = 5), nacheinander recherchiert & beantwortet. |
| `/ddd[N] [Zusatz]` · `/deepdivedocument[N]` | Wie Deepdive, aber **als Dokument** (Vorwort + Kapitel) im Dokumente-Tab. |
| `/plan[N] [Zusatz] [/Kürzel …]` | **Strategie & Einsatzplan-Orchestrator:** baut aus dem Gesprächsverlauf eine Vorschau (Strategie + Agenten + Plan + Jury). `N` = Aufgabenzahl (4–60, Standard 12). `/Kürzel` erzwingt vorhandene Agenten. |
| `/- <Text>` | **Feedback – Problem/Fehler** ins Protokoll `data/feedback.md` (nicht an die KI). |
| `/+ <Text>` | **Feedback – Idee/Verbesserung** ins Protokoll `data/feedback.md` (nicht an die KI). |

**Kontextfenster:** Im Profil wählbar von **4k bis 128k** Tokens (Chat &
Dokumentengenerator). Größer verhindert abgeschnittene Antworten, kostet aber mehr
GPU-Speicher — bei viel VRAM ruhig hoch wählen.

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
- **📈 Funktion plotten** (`plot_function`) — nennst du eine **mathematische Funktion**
  (z. B. `f(x)=x^2`, `sin(x)`, `sqrt(x)`) oder bittest um einen Graphen/Verlauf/eine
  Kennlinie, zeichnet die KI den **Graphen direkt im Chat**. Versteht `^` als Potenz,
  implizite Multiplikation (`2x`) und mehrere Funktionen mit `;` zum Vergleich. Diese
  Regel ist **immer aktiv** (unabhängig vom Antwortstil/Modus), im **Maschinenbau-Modus**
  besonders betont.
- **📊 Diagramm** (`plot_chart`) — Linien-/Balken-/Streudiagramm aus Wertereihen
  (z. B. Kraft-Weg-Kurven, Spannungs-Dehnungs-Diagramme).
- Zusätzlich: Einheitenumrechnung, Gleichungslöser, Werkstoff-Lookup,
  VDI-2230-Schraubenberechnung.

**Beispiel-Prompts:**
- *„Berechne die Leistung: P = U × I mit U = 400 V, I = 25 A"*
- *„Plotte f(x)=x^2 und sin(x) von -5 bis 5"* → zeichnet den Graphen im Chat
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
2. **Experten wählen** — entweder einen **festen Agenten** aus der Liste
   (sein System-Prompt wird übernommen) **oder** mit **🧠 Analyse-Experte
   ableiten** aus der Beschreibung eine fachliche Persona erzeugen (z. B. ein
   „Elektrotechnik-Experte" bei E-Maschinen-Themen). Den Text kannst du vor dem
   Start noch anpassen.
3. **📁 Bilderordner wählen** — alle Bilder des Ordners werden geladen.
4. **▶ Präsentation erstellen** — pro Bild prüft die KI den Dateinamen und
   **analysiert das Bild** (lokales Vision-Modell). Es entsteht je Bild eine Folie
   mit dem Bild auf der einen und einem kurzen Text auf der anderen Hälfte.

Aufbau: **Deckblatt (nur der Titel)** → **Einleitungsfolie** (der gewählte Experte
formuliert die Beschreibung neu) → Bildfolien → Abschluss. Anschließend mit dem
WYSIWYG-Editor (siehe Abschnitt 7) feinjustierbar. Der **PDF-Export** enthält die
Bilder ebenso wie der PPTX-Export.

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
4. **Bereinigungsstufe** wählen — *Standard* oder *Strikt* (siehe
   [Dokumentbereinigung](#dokumentbereinigung)). Im Zweifel *Standard*.
5. **Embedding-Modell** wählen — Standard ist das lokale `nomic-embed-text`
   (läuft auf der CPU, nichts verlässt den Rechner). Alternativ ein API-Modell,
   falls Anbieter hinterlegt sind (siehe [Embedding-Modell wählen](#embedding-modell-wählen)).
6. **Dokumente bereinigen** an-/ausschalten (empfohlen: an, siehe unten).
7. **📚 Wissensdatenbank anlegen** klicken.

> **Hinweis (kleine Grafikkarte):** Die Embeddings laufen immer **auf der CPU**, damit
> sie das Chat-Modell nicht aus den 6 GB VRAM verdrängen. Der Regler *gründlich* erhöht
> nur Chunk-Größe und Trefferzahl — den VRAM-Bedarf steuerst du indirekt über den
> Kontext, der dem Chat-Modell vorgelegt wird (kleiner = sparsamer).

> **🆘 Hilfe zum Tool:** Der Knopf **„Hilfe-Wissensdatenbank erstellen/aktualisieren"**
> (oben im RAG-Tab) liest die mitgelieferte Bedienungs- und Entwicklerdoku in eine
> Wissensdatenbank „Hilfe: LOCAL AI" ein und legt einen **Hilfe-Assistenten** an. Danach
> kannst du im Chat per **`/Hilfe …`** Fragen zur Bedienung stellen, die direkt aus der
> Doku beantwortet werden. Ein erneuter Klick aktualisiert die Datenbank.

### Dokumente hinzufügen

Bei einer Sammlung auf **＋ Dokument(e) hinzufügen** klicken und eine oder mehrere
Dateien wählen (PDF, DOCX, XLSX, CSV, TXT, MD). Jede Datei wird extrahiert,
optional bereinigt, in Chunks zerlegt und eingebettet. Danach steht je Dokument die
Anzahl der erzeugten Chunks. Einzelne Dokumente oder die ganze Sammlung lassen sich
wieder **entfernen**.

> Bei großen PDFs kann das Einbetten einen Moment dauern — eine Meldung bestätigt
> den Abschluss („✓ Datei: N Chunks").

### Ganzen Ordner einlesen

Statt einzelner Dateien lässt sich ein **kompletter Ordner** in eine Wissensdatenbank
überführen — alle enthaltenen Textdateien (PDF, DOCX, XLSX, CSV, TXT, MD, gängige
Code-/Markup-Dateien) werden nacheinander extrahiert, bereinigt, gechunkt und eingebettet:

- **📁 Server-Ordner:** einen Pfad auf dem Rechner eingeben (z. B. `C:\Dokumente\Projekt`
  oder `/mnt/share/rag`). Auf Nachfrage werden auch **Unterordner** rekursiv einbezogen.
  Ideal für große Bestände, da nichts hochgeladen werden muss.
- **📂 Browser-Ordner:** einen Ordner im Datei-Dialog wählen; alle Dateien werden
  hochgeladen und eingelesen. Funktioniert auch im Server-/Mehrbenutzerbetrieb.

Eine Fortschrittskarte zeigt Datei für Datei den Stand; am Ende erscheint die Gesamtzahl
eingelesener Dateien und Chunks (übersprungene Dateien werden gemeldet).

### Gespräch in eine Sammlung übernehmen

Im Block **💬 → 📚 Gespräch in Sammlung übernehmen** ein gespeichertes Gespräch und
eine Zielsammlung wählen, dann **übernehmen**. Das Gespräch wird als Dokument in die
Sammlung eingebettet und steht künftig als Wissen zur Verfügung. Mit der Option
**„Original danach löschen"** wird das Gespräch dabei aus der Liste entfernt
(„verschieben"); ohne Haken bleibt es erhalten (Kopie).

### Dokumentbereinigung

Mit aktivierter Option **Bereinigen** wird der extrahierte Text vor dem Chunking
geglättet. Es gibt **zwei Stufen**, wählbar beim Anlegen der Sammlung.

#### Stufe „Standard" (Voreinstellung)

Vereinheitlicht den Text, **ohne Inhalt zu verlieren**:

- **Unicode-Normalisierung (NFKC)** — vereinheitlicht optisch gleiche, technisch
  verschiedene Zeichen; löst u. a. PDF-Ligaturen auf.
- **Typografische Sonderzeichen → einfache Zeichen:** alle Gedankenstrich-Varianten
  (`‐ ‑ ‒ – — ― −`) → `-`, typografische Anführungszeichen (`„ " " ‚ ' »`) → `"`
  bzw. `'`, Auslassungszeichen `…` → `...`.
- **Leerzeichen-Varianten → normales Leerzeichen:** geschütztes, schmales,
  Ziffern-, ideographisches Leerzeichen usw.
- **Unsichtbare Steuerzeichen entfernt:** weiche Trennzeichen, Zero-Width-Zeichen,
  Wortverbinder, Richtungssteuerung. *Gerade weiche Trennzeichen aus PDFs sind
  tückisch — im Editor unsichtbar, zerlegen sie aber Wörter für die Suche.*
- **Silbentrennung aufgehoben:** `Maschi-/nenbau` → `Maschinenbau`.
- **Seitenzahlen entfernt** — reine Zahlenzeilen und Marken der Form `12/144`.
- Umbrochene Zeilen zu Absätzen zusammengefügt, Mehrfach-Leerzeichen reduziert.

#### Stufe „Strikt"

Alles aus *Standard*, **zusätzlich** (und dabei bewusst verlustbehaftet):

- **Markdown-Zeichen entfernt:** `#`/`##`/`###`, `**fett**`, `__…__`, Backticks,
  Aufzählungszeichen am Zeilenanfang. Aus `## Artikel 1 Gegenstand` wird
  `Artikel 1 Gegenstand`.
- **Links reduziert:** `[Kommission](https://…)` → `Kommission`; nackte Adressen
  (`http://…`, `https://…`, `www.…`) werden entfernt.
- **Wiederkehrende Kopf- und Fußzeilen entfernt** — z. B. „Amtsblatt der
  Europäischen Union", „DE ABl. L vom 12.7.2024", „ELI: http://…".

> **Wann „Strikt"?** Für **Behörden-, Gesetzes- und Normendokumente**, deren
> PDF-Layout auf jeder Seite Kopf-/Fußzeilen, Seitenmarken und Amtsblatt-Bausteine
> wiederholt. Diese Wiederholungen verwässern sonst die Suche, weil sie in fast
> jedem Chunk auftauchen. Für normale Handbücher, Angebote oder bewusst in
> Markdown gepflegte Texte ist *Standard* die bessere Wahl — dort ist die
> Struktur (Überschriften, Links) ja Information.

**Wie die Kopfzeilen-Erkennung arbeitet:** Es sind **keine** festen Textbausteine
hinterlegt — das würde nur bei der EU funktionieren. Stattdessen gilt eine Zeile
als Kopf-/Fußzeile, wenn sie *drei Bedingungen zugleich* erfüllt: sie kommt
**mindestens 4×** vor, in **gleichmäßigen Abständen** (Seitenköpfe wiederholen
sich alle n Zeilen) und sie ist **kurz (≤ 60 Zeichen)**. Ein inhaltlicher Satz,
der zufällig mehrfach vorkommt, wird dadurch **nicht** gelöscht. Das Verfahren
greift so auch bei anderen Ämtern, Sprachen und Dokumentarten.

> **Vorab prüfen:** Mit `python scripts/clean_documents.py <Datei-oder-Ordner>` kannst
> du das Bereinigungsergebnis als `*.clean.txt` ansehen, bevor du hochlädst
> (vorher `set PYTHONIOENCODING=utf-8`).

> **Wenn ein Dokument gar nicht verarbeitet wird:** Zuerst Stufe *Strikt*
> versuchen. Hilft das nicht, das PDF vorab als reine `.txt` speichern (UTF-8,
> ohne BOM) und diese hochladen — die Bereinigung greift auch dort.

### Embedding-Modell wählen

Das **Embedding-Modell** wandelt Text in Zahlenvektoren um; darüber findet die
Suche passende Stellen. Beim Anlegen einer Sammlung wählbar:

- **Lokal (Voreinstellung, `nomic-embed-text`)** — läuft über Ollama auf der
  **CPU**, damit es das Chat-Modell nicht aus dem VRAM verdrängt. Die
  Dokumentinhalte **verlassen den Rechner nicht**.
- **API-Modell** — falls unter *Profil → Externe KI-Anbieter* ein Anbieter
  hinterlegt ist. Oft schneller und treffsicherer bei großen Sammlungen.
  ⚠ **Die Dokumenttexte werden dabei an den Anbieter gesendet.** Für vertrauliche
  Unterlagen ungeeignet.

> **Wichtig:** Das Modell gehört **dauerhaft zur Sammlung**. Vektoren
> verschiedener Modelle sind nicht miteinander vergleichbar. Ein Wechsel bedeutet
> deshalb: neue Sammlung anlegen und die Dokumente erneut einlesen. Umgekehrt
> unproblematisch: Du kannst beliebig viele Sammlungen mit **unterschiedlichen**
> Modellen parallel betreiben und gemeinsam abfragen — jede wird korrekt mit
> ihrem eigenen Modell durchsucht.

In der Sammlungsliste steht das verwendete Modell hinter 🖥 (lokal) bzw.
🌐 (API), daneben die Bereinigungsstufe.

### Im Chat nutzen

1. In der Chat-Toolbar den Umschalter **📚 RAG** aktivieren — daneben erscheint eine
   **Sammlungsauswahl** als dunkelthemenfähiges Dropdown mit Checkboxen.
2. Eine oder mehrere Sammlung(en) anhaken.
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
4. **📄 Dokument erzeugen** — das Ergebnis erscheint **rechts** formatiert (inkl. Formeln
   via KaTeX/Links). Oder **🖥️ Präsentation erzeugen** — derselbe Inhalt landet als
   Präsentation im **Canvas** (Querformat).
5. **✏️ Bearbeiten** — das erzeugte Dokument **direkt im Text** ändern (WYSIWYG): Klick auf
   **✏️ Bearbeiten** macht die rechte Ansicht editierbar (farbiger Rahmen). Tippe Korrekturen
   direkt hinein, dann **✓ Übernehmen** (oder **✕ Abbrechen**). Die Änderungen gelten für
   **alle** weiteren Exporte (DOCX/PDF/LaTeX/RAG/Jury). *Hinweis:* Während der Bearbeitung
   erscheinen **Mermaid-Diagramme als Quelltext** (damit sie erhalten bleiben) und werden
   nach „Übernehmen" wieder als Diagramm gezeichnet; Formeln bleiben erhalten.
6. Export als **📝 DOCX**, **📑 PDF**, **𝐓 LaTeX** (reine `.tex`-Datei), als
   **🖥️ Präsentation** (Canvas) oder zurück **📚 In Wissensdatenbank**.

> **Layout:** Der Dokumenten-Tab ist zweispaltig — links die Steuerung, rechts das
> erzeugte Dokument. Der **Trenner** dazwischen ist mit der Maus ziehbar
> (Doppelklick setzt zurück). Denselben ziehbaren Trenner gibt es in **Recherche**,
> **RAG** und **Mail**.

Mit der Option **„quellengebunden (wissenschaftlich)"** arbeitet die KI streng auf
Basis der Quellen (für belegpflichtige Dokumente).

> **Hinweis zu Formeln im Export:**
> - **PDF** setzt LaTeX-Formeln jetzt als echten **Formelsatz** (über matplotlib-mathtext,
>   ohne LaTeX-Installation) — `$…$`, `$$…$$`, `\(…\)` und `\[…\]` werden gerendert.
> - **𝐓 LaTeX-Export** erzeugt eine reine `.tex`-Datei (Dokument → `article`,
>   Präsentation → `beamer`); Formeln bleiben echtes LaTeX-Math, Markdown wird in
>   LaTeX-Befehle übersetzt. Mit einer LaTeX-Installation (z. B. MiKTeX/TeX Live)
>   selbst zu PDF kompilierbar.
> - **DOCX** zeigt Formeln weiterhin als `$…$`-Text (kein Word-Formelsatz).
> - **Formelsatz am Bildschirm** (Dokument-Ansicht, Canvas-Folien) via KaTeX.

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
  - **Besprechungsnotizen:** Das Feld wird **automatisch gespeichert** (übersteht ein
    Neuladen) — ideal, um während einer Besprechung mitzuschreiben. „**💾 Notiz
    speichern**" sichert manuell, „**🗑 Notiz leeren**" löscht. Nach dem **erfolgreichen
    Export** des erzeugten Dokuments (DOCX/PDF/LaTeX) wird das Notizfeld **automatisch
    geleert**.

Aus dem **Chat** bringt der Knopf **„→ 📄 Doku"** (in der Eingabeleiste) das laufende
Gespräch komprimiert hierher — praktisch, um z. B. aus einer Unterhaltung eine
Besprechung zu planen.

---

## Anfrage-Auswertung (📋 Anfrage)

Für umfangreiche Anfragen/Ausschreibungen, die als **XLS/CSV mit vielen Arbeitspaketen**
(bis ~500 Zeilen) kommen. Jedes Paket wird automatisch ausgewertet: ein „Masteragent"
bestimmt die **zuständige Fachrolle** und bewertet **interessant?**, **Partner nötig?**
und **Best-Cost-Country?** — abgeglichen mit einer **globalen Kapazitätsliste**.

**Ablauf:**
1. **Datei einlesen** — XLS/CSV wählen, dann **Blatt**, **Kopfzeile** und die
   **Aufgaben-Spalte** zuordnen (ID-/Titel-Spalte optional). Eine Vorschau zeigt die
   ersten Zeilen.
2. **Lauf-Optionen** — Modell wählen, optional **Websuche je Paket** und
   **Wissensdatenbanken** einbeziehen, **Umfang** festlegen (Testlauf „erste 25" … „alle").
3. **▶ Auswerten starten** — das Ergebnisraster füllt sich Paket für Paket (Zuständig,
   Interessant, Partner, Best-Cost-Country) mit Live-Zählern. Mit **✕ Abbrechen** stoppen
   und mit **⏩ Fortsetzen** später nahtlos weitermachen (Zwischenstand wird serverseitig
   gesichert).
4. **📋 XLSX exportieren** — Originalspalten + Auswertungsspalten (inkl. eigener Spalten)
   als Excel-Datei. Mit **„nur interessante"** das Raster filtern.

**🧩 Eigene Spalten:** zusätzlich zu den festen Spalten lassen sich **bis zu 6 eigene
Bewertungsspalten** definieren. Je Spalte wählst du entweder einen **Agenten** (bewertet
mit seiner Fachrolle) oder gibst einen **freien Prompt** (Frage/Vorgabe) an, z. B. „Wie
hoch ist das technische Risiko (niedrig/mittel/hoch)?". Jede Spalte ist ein zusätzlicher
LLM-Aufruf pro Paket und erscheint im Raster und im Export.

**💬 Chat-Zeile:** unter dem Raster eine freie Rückfrage zur ausgewerteten Anfrage stellen
(z. B. „Fasse die interessanten Pakete zusammen", „Welche brauchen einen Partner?"). Die KI
antwortet auf Basis der aktuellen Auswertung.

**📚 In RAG übernehmen:** Die fertige Auswertung als Dokument in eine **Wissensdatenbank**
übernehmen (du wählst die Sammlung). So lässt sich später im Chat oder in weiteren Anfragen
darauf zugreifen.

> **Große Pakete & Kontextfenster:** Die Anfrage wird **paketweise** ausgewertet — die
> Gesamtdatei muss also nie komplett ins Kontextfenster passen, egal wie viele Pakete sie
> hat. Wie viel Text **eines einzelnen Pakets** (plus Kapazitätsliste) berücksichtigt wird,
> richtet sich automatisch nach dem **Kontextfenster** (Profil): ein größeres Fenster lässt
> längere Paketbeschreibungen ungekürzt zu. Hat ein **einzelnes** Paket mehr Text als das
> Fenster fasst, wird es gekürzt — dann das Kontextfenster erhöhen.

**👥 Kapazitätslisten (mehrere):** Rollen/Partner mit **Land**, **freier Kapazität (h)**,
**Kostensatz** und **Skills**. Du kannst **mehrere benannte Listen** anlegen (Button
**👥 Kapazitätslisten**) und **direkt im Anfrage-Tab per Häkchen** (Feld **„Ressourcenlisten
(aktiv)"** neben den Wissensdatenbanken) festlegen, welche **aktiv** sind — die aktiven Listen
werden vereinigt und gelten für Auswertung **und** Planer. CSV-Import möglich
(`Typ;Name;Satz;Land;Kapazität;Skills`).

**➜ In Planer übernehmen:** Die ausgewählten Tickets (nur interessante oder alle) werden
**gesamthaft in einen Plan** überführt. Da die Anfrage keine Stunden enthält, **schätzt die
KI bei der Übergabe** je Ticket **Aufwand (h) und Dauer (Tage)**; die zuständige Rolle wird
zur Ressource. Anschließend öffnet sich der **Planer** und im Dialog **„📅 Kapazität &
Zukauf"** siehst du je Rolle **Bedarf vs. freie Kapazität (Auslastung %)**, **Fehlstunden
(Make-or-Buy)**, die **Kostenschätzung** sowie die aggregierten **Partner-** und
**Best-Cost-Country-Listen** — und darunter den **Bestellplan** für Hardware/Software mit
Lieferzeiten. So wird sichtbar, **wo Ressourcen überbucht sind** und **was zugekauft werden
muss**. (Tickets ohne gepflegte Abhängigkeiten starten rechnerisch alle am Projektstart —
deshalb ist hier die kapazitätsbasierte Auslastung die maßgebliche Sicht, nicht die
Termin-Überlappung.)

> Tipp: Bei sehr großen Anfragen zuerst einen **Testlauf (erste 25)** machen, Spaltenzuordnung
> und Ergebnisqualität prüfen, dann auf **„alle"** stellen.

---

## Mail-Bearbeitung

> 🚧 **In Entwicklung (Beta).** Der Mail-Tab wird aktiv weiterentwickelt; einzelne
> Schritte können sich noch ändern und nicht jedes Postfach ist erprobt. Der Zugriff
> ist und bleibt **read-only** — es werden **keine Mails gelöscht oder automatisch
> versendet**.

Tab **📧 Mail**: ruft ein Postfach **read-only** ab, **filtert** die Mails und führt
pro Mail **bis zu vier Aktionen** aus (z. B. in eine Wissensdatenbank übernehmen oder
einen Agenten eine Antwort entwerfen lassen). Zweispaltig: **links** Zugang, Filter,
Liste und Aktions-Set; **rechts** die **Ergebnisse**. **Versand erfolgt immer manuell.**

1. **⚙️ Postfach-Zugang** öffnen und **Protokoll** wählen — **IMAP** (empfohlen, lässt
   die Mails auf dem Server) oder **POP3**. Server, Port (Standard wird je Protokoll/SSL
   vorgeschlagen), Benutzer und Passwort eintragen, **💾 Zugang speichern**. Der Block
   ist **einklappbar** (minimierbar) und bleibt nach dem Speichern zu.
   - Bei **Gmail** mit 2-Faktor ein **App-Passwort** verwenden, nicht das Hauptpasswort.
   - Der Zugang wird lokal in `data/mail.json` gespeichert (nicht im Backup, nicht in git).
2. Optional **Suche** (serverseitig) und **Anzahl** setzen, dann **📥 Abrufen**.
3. **🔎 Filter** (clientseitig, live): nach **Absender**, **Betreff** und/oder **Domäne**
   eingrenzen. Über der Liste steht „Treffer/Gesamt".
4. **🎬 Aktions-Set (max. 4):** Pro Slot einen Typ wählen und konfigurieren:
   - **📚 In RAG (bereinigt)** — Mail mailspezifisch **bereinigen** (Zitat-Verlauf,
     Signatur, Disclaimer werden entfernt) und in eine Wissensdatenbank übernehmen.
   - **🤖 Agent-Aufgabe** — einen **Favoriten-Agenten** (⭐) wählen und einen
     **Freitext-Auftrag** geben (z. B. „höfliche Antwort entwerfen", „zusammenfassen",
     „Termine/Aufgaben extrahieren"). Das Ergebnis erscheint rechts als **editierbarer
     Entwurf**.
   - **📄 → Dokumentengenerator** — Mail als Quellmaterial übergeben.
   - **🏷 Markieren / Notiz** — lokale Markierung an die Mail (Badge in der Liste).
5. **▶ Aktionen anwenden** — auf die **angehakten** Mails (oder „**auf alle gefilterten**").
   Läuft sequenziell, mit Fortschritt und **⏹ Abbrechen**. (Agent-Aktionen nutzen das
   lokale Modell und können dauern — ab >5 Mails wird rückgefragt.)
6. **Ergebnisse rechts:** Jede Aktion bekommt eine Karte. Ein Agent-Entwurf hat
   **📋 Kopieren**, **✉ Im Mailprogramm öffnen** (mailto, vorbefüllt — du sendest selbst)
   und **📄 → Doku**.

**Regeln:** Filter + Aktions-Set lassen sich als **Regel** speichern (`💾 Regel`) und
später wieder laden (`— Regel laden —`), z. B. „Rechnungen von firma.de → ins RAG +
Antwort entwerfen". Regeln liegen lokal in `data/mail_rules.json`. Die Ausführung
bleibt immer manuell per **▶**.

> Es werden keine Mails gelöscht oder versendet — der Zugriff ist rein lesend, der
> Versand passiert ausschließlich von Hand in deinem Mailprogramm/der Zwischenablage.

---

## 10. Medizin-Tab

> **Hinweis:** Dieser Tab dient ausschließlich als **Demonstrations- und Assistenz-Werkzeug**
> und ersetzt keine ärztliche Diagnose oder Fachberatung. Sämtliche KI-Ausgaben müssen von
> medizinisch ausgebildetem Personal geprüft werden.

Tab **🩺 Medizin** — ein eigenständiges Chat-Interface für medizinische Fragestellungen,
mit Patienten-RAG und Datei-Upload für Befunde und Bilder. Der Tab bleibt beim Senden
einer Nachricht sichtbar — es wird nicht zum allgemeinen Chat-Tab gewechselt.

*(Tab muss ggf. im Profil erst eingeblendet werden → Abschnitt 18, Tab-Sichtbarkeit.)*

### Patienten-Akte (RAG)

Der Medizin-Tab kennt das Konzept einer **Patienten-Akte**: eine dedizierte
Wissensdatenbank, deren Name mit `Patient:` beginnt (z. B. `Patient: Max Mustermann`).

- **Neue Akte anlegen:** Im Topbar den Bereich **➕ Neue Akte** öffnen — das Eingabefeld
  erscheint direkt in der Toolbar. Namen eingeben und **✓** drücken. Die neue Akte wird
  als RAG-Sammlung mit dem Präfix `Patient:` angelegt.
- **Akte wählen:** Dropdown oben links — zeigt alle vorhandenen Patienten-Akten. Ist eine
  Akte gewählt, fließt ihr Inhalt automatisch als Kontext in alle Fragen ein.
- **Dokumente zur Akte hinzufügen:** **📎 In Akte laden** in der Toolbar — Bilder (PNG, JPG)
  und Berichte (PDF, DOCX) werden direkt in die gewählte Akte eingebettet. So bauen sich
  patientenspezifische Wissensdatenbanken auf, die die KI bei der Beantwortung nutzt.

### 🔬 Experten-Pipeline (zwei Modelle, mit Rückfragen)

Standardmäßig ist die **Experten-Pipeline** aktiv (Umschalter **🔬 Experten-Pipeline**
in der Toolbar). Statt einer einfachen Antwort arbeiten **zwei Modelle** zusammen und
stellen bei Bedarf Rückfragen:

1. Das **Standardmodell** (z. B. Ministral) bereitet deine Frage medizinisch auf.
2. Das **Medizin-Modell** (z. B. MedGemma) prüft, ob für eine Einschätzung **wichtige
   Angaben fehlen** (Alter, Dauer, Begleitsymptome …).
3. Fehlt etwas, formuliert das Standardmodell eine **freundliche Rückfrage** — du
   ergänzt die Angaben (bis zu **zwei Rückfrage-Runden**).
4. Dann erstellt das Medizin-Modell die **fachliche Einschätzung** (mögliche Ursachen,
   nächste Schritte, Dringlichkeit) mit Warnhinweisen.

Die einzelnen Schritte erscheinen als **aufklappbare Blöcke** über der Antwort
(Aufbereitung / Analyse / Rückfrage) — so ist transparent, was gerade passiert. Unter
einer fertigen Einschätzung bietet **🗣 In einfaches Deutsch übersetzen** eine
laienverständliche Fassung an.

> **Tempo:** Die Pipeline wechselt mehrfach zwischen den Modellen. Auf kleinen
> Grafikkarten (~6 GB) wird bei jedem Wechsel ein Modell ent- und das andere geladen –
> das dauert spürbar länger als ein normaler Chat. Die Statusschritte zeigen den Fortschritt.

Über den Umschalter lässt sich auf den **einfachen Direkt-Chat** (nur ein Modell, ohne
Rückfragen) zurückschalten.

### Chat & Akten-Werkzeuge

- Freitext eingeben und **Enter** senden (oder den Sende-Button).
- **📎 Datei anhängen** — lädt eine Datei zur aktuellen Frage hoch (Vision-Modell nötig).
  Für **dauerhafte** Befunde/Bilder besser **📎 In Akte laden** nutzen (Patienten-RAG;
  die Pipeline nutzt die Akte automatisch als Kontext).
- **🗑 Verlauf löschen** — Chat-Reset (die Patienten-Akte bleibt erhalten).
- Das **Medizin-Modell** stellst du unter **👤 Profil bearbeiten** als Rolle **Medizin**
  ein (empfohlen ein MedGemma-Modell wie `medgemma:4b`). Es gibt kein eigenes Modell-Dropdown
  mehr im Medizin-Tab. Ist kein medizinisches Modell hinterlegt, läuft die Pipeline gegen das
  Standardmodell (`ollama pull medgemma:4b` für die beste Qualität).

### Schnell-Prompts

In der Toolbar gibt es vier vordefinierte Schnelleinstieg-Buttons:
- **Anamnese** — Gesprächsleitfaden für die Anamnese
- **DD** — Differentialdiagnosen erstellen
- **Labor** — Laborwerte interpretieren
- **Medikament** — Medikamenten-Informationen

### Modell-Tipp

Für die MedGemma-Rolle empfiehlt sich ein medizinisch trainiertes Modell:
`medgemma:4b` (MedGemma-4B, ~2,5 GB). In der **Portable-Variante ist es bereits
mitgebündelt**; sonst einmalig installieren und im Profil als **Medizin-Modell** hinterlegen:
```bash
ollama pull medgemma:4b
```
Ohne medizinisches Modell läuft die Pipeline gegen das Standardmodell — funktioniert,
ist aber fachlich schwächer.

---

## 11. Mathe-Tab

Tab **🔢 Mathe** — ein eigenständiger Mathematik-Workspace mit Chat-Interface, der
Graphen plottet, Gleichungssysteme löst und Berichte als LaTeX/PDF exportiert.
Der Agent **Mathe-Experte** ist voreingestellt. **LaTeX-Formelsatz ist hier Standard**
(keine Auswahl nötig).

*(Tab muss ggf. im Profil erst eingeblendet werden → Abschnitt 18, Tab-Sichtbarkeit.)*

> **Modell:** Der Mathe-Tab teilt sich das Modell mit dem **Code-Tab** — eingestellt unter
> **👤 Profil → 🧠 Modelle → „Programmieren / Mathe"** (leer = `ministral-3:3b`). Ein eigenes
> Modell-Auswahlfeld gibt es im Mathe-Tab daher nicht.

### Chat & Plots

- Frage eingeben und **Enter** senden. Enthält die Anfrage eine **mathematische Funktion**
  (z. B. `f(x) = x^2`, `sin(x)`, mehrere mit „und"; Bereich „von … bis …"), wird der
  **Graph direkt im Chat-Verlauf** gezeichnet.
- Der Schalter **📈 Plot** direkt an der Chatzeile steuert das automatische Zeichnen.
  Die Graphen entstehen **zuverlässig serverseitig** — unabhängig davon, ob das kleine
  Modell ein Zeichenwerkzeug aufruft.
- **📎 Datei anhängen** — lädt eine Aufgabe, ein Bild oder ein Dokument hoch.
- **🗑 Verlauf löschen** — Chat-Reset.

### 🎓 Tutor-Modus (Schritt für Schritt lernen)

Der Umschalter **🎓 Tutor-Modus** macht aus dem Löser einen **Lern-Tutor**: Er löst die
Aufgabe **nicht sofort**, sondern führt dich **Schritt für Schritt** selbst zur Lösung.

- **Adaptiv-sokratisch:** Der Tutor gibt einen Ansatz und eine Leitfrage und **wartet auf
  deine Antwort**. Wenn du hängst oder dich irrst, gibt er schrittweise mehr preis.
- **Werkzeuggeprüft:** Deine Zwischenschritte werden **serverseitig mit SymPy verifiziert** —
  der Tutor erkennt Rechenfehler zuverlässig und zeigt dir, warum etwas nicht stimmt
  (statt eine falsche Antwort durchgehen zu lassen).
- **Niveau automatisch:** Schul-, Oberstufen- oder Studienniveau wird aus der Aufgabe erkannt.
- **💡 Lösung zeigen** — fordert jederzeit die vollständige, ausführliche Lösung an.

> Funktioniert am besten bei Gleichungen, Ableitungen, Integralen und Faktorisierungen
> (dort greift die SymPy-Prüfung). Bei reinen Theoriefragen führt der Tutor rein erklärend.

### 🔁 Auto-Verifizieren (selbstprüfende Lösung)

Der Umschalter **🔁 Auto-Verifizieren** löst die Aufgabe und **prüft das Ergebnis
automatisch mit SymPy** — eine freie, vollständig lokale Umsetzung der Idee „die KI rechnet,
ein deterministisches Werkzeug verifiziert". Stimmt das Ergebnis nicht mit der SymPy-Berechnung
überein, fließt die Grundwahrheit als Korrekturhinweis zurück und das Modell rechnet erneut
(bis zu zwei Korrekturrunden).

- Die Zwischenschritte erscheinen als **aufklappbare Blöcke** (🔍 SymPy-Grundwahrheit,
  🧮 Lösungsversuch, 🔧 Korrektur).
- Am Ende zeigt ein **Abzeichen** den Status: **✓ verifiziert**, **⚠ nicht abschließend
  verifiziert** (mit sichtbarer SymPy-Grundwahrheit) oder **ℹ mit SymPy-Fakten gestützt**
  (bei nicht eindeutig prüfbaren Ergebnissen wie Ableitungen).
- Der Modus schließt den Tutor-Modus aus (nur einer von beiden gleichzeitig).

### Schnell-Prompts

Vier vordefinierte Einsteig-Aufgaben in der Toolbar:
- **∫ Integral** — Integrationsbeispiel
- **Σ Summen** — Summenformel mit Herleitung
- **Vektor** — Vektor-/Matrizenrechnung
- **Statistik** — Statistische Auswertung

### LaTeX/PDF-Export

Enthält die Antwort mathematische Formeln (erkennbar am `$`-Zeichen), erscheint
automatisch eine **Export-Leiste** unter der Antwort:

- **𝐓 LaTeX** — erzeugt eine `.tex`-Datei (article-Klasse) mit echten LaTeX-Formeln,
  direkt kompilierbar mit MiKTeX/TeX Live.
- **📑 PDF** — erzeugt ein PDF mit Formelsatz über matplotlib-mathtext
  (kein TeX-Install nötig).

### Was der Mathe-Experte kann

Der voreingestellte Agent **Mathe-Experte** verwendet folgende Tools:
- `calculate` — Python-Sandbox mit numpy, scipy, sympy
- `solve_equation` — algebraische und symbolische Gleichungslöser (SymPy)
- `plot_function` — Funktionsgraphen (ein oder mehrere Funktionen mit `;`)
- `plot_chart` — Wertereihen-Diagramme (Linien, Balken, Streuung)
- `unit_convert` — Einheitenumrechnung (Pint)
- `web_search` — Nachschlagen von Formeln und Definitionen

**Beispiel-Prompts:**
- *„Löse das Gleichungssystem: 2x + y = 7 und x − y = 1"*
- *„Plotte f(x) = x^3 − 3x und g(x) = x von −3 bis 3"*
- *„Berechne das bestimmte Integral von x^2 von 0 bis 3"*
- *„Eigenwerte der Matrix [[2,1],[1,3]]"*

---

## 11a. Verzeichnis-Analyse-Tab

Tab **📁 Verzeichnis** — liest einen lokalen Ordner ein, verschafft dir per KI einen
Überblick, hebt interessante Dateien hervor, analysiert einzelne Dateien auf Wunsch
vertieft und schreibt am Ende eine Index-Datei (`_KI_INDEX.md`) in den Ordner zurück.

*(Tab muss ggf. im Profil erst eingeblendet werden → Abschnitt 18, Tab-Sichtbarkeit.)*

> **🔒 Datenschutz:** Personenbezogene Daten in den **Dateiinhalten** (E-Mail-Adressen,
> Telefonnummern, IBAN, URLs und erkannte Namen) werden **anonymisiert**, bevor sie an
> die KI oder in die Anzeige gehen — ersetzt durch Platzhalter wie `[EMAIL_1]`,
> `[PERSON_1]`. Datei- und Ordnernamen bleiben sichtbar. Die Zuordnungstabelle bleibt
> lokal und wird **nicht** in die Index-Datei geschrieben.

### Ablauf

1. **Server-Pfad eingeben** (z. B. `/home/thomas/projekt`) und **🔍 Scannen**.
   Es erscheinen: ein KI-Überblick, die Verzeichnisstruktur und eine Liste
   **⭐ interessanter Dateien** mit Begründung.
2. **Datei anklicken** → vertiefte Analyse als Markdown rechts im Detailbereich.
3. **📥 Index in Ordner speichern** schreibt `_KI_INDEX.md` mit Überblick und allen
   Detailanalysen in den Ordner. **📚 In Wissensdatenbank** legt zusätzlich eine
   RAG-Sammlung „Verzeichnis: …" an, die du anschließend im Chat befragen kannst.

> **Anonymisierung ist Pflicht** und lässt sich **nicht abschalten** — Personendaten
> werden bei jedem Scan und jeder Analyse geschwärzt. Optional kannst du zusätzlich
> **`+ KI-Namenssuche`** aktivieren (ein langsamerer KI-Pass, der weitere Namen findet);
> diese Option kann die Anonymisierung nur **verstärken**, nie reduzieren.

> **Tipp:** Klicke ruhig mehrere Dateien an — die Analysen werden **nacheinander**
> abgearbeitet (Warteschlange). Schlägt eine fehl, erscheint ein **↻ Erneut**-Knopf.

> **Hinweis (Servermodus):** Da beliebige Server-Pfade gelesen **und** beschrieben
> werden, ist dieser Tab für den Mehrnutzer-/Servereinsatz nicht gedacht — er bleibt
> dort am besten ausgeblendet.

---

## 11b. Morphologischer-Kasten-Tab

Tab **🧩 Morph-Kasten** — ein KI-gestützter morphologischer Kasten (Zwicky-Box) für
die systematische Ideenfindung: Zeilen sind **Parameter** (Merkmale einer Lösung),
die **Chips** je Zeile sind die möglichen **Ausprägungen**. Eine Lösung entsteht,
indem du je Parameter eine Ausprägung wählst.

*(Tab muss ggf. im Profil erst eingeblendet werden → Abschnitt 18, Tab-Sichtbarkeit.)*

### Ablauf

1. **Aufgabenstellung** eingeben (z. B. „Konzept für ein modulares Lastenfahrrad")
   und **🤖 Parameter generieren** — die KI füllt das Raster mit Parametern und
   Ausprägungen.
2. **Ausprägungen wählen:** Chip **einfach anklicken** = für die Lösung auswählen (erneut
   klicken = abwählen). **Doppelklick** = Text bearbeiten (Enter = übernehmen, Esc =
   abbrechen). Die aktuelle Lösung wird oben angezeigt.
3. **Verfeinern:** pro Chip **✨** (ausformulieren) oder **💬** (Kritik & Alternativen).
   Die **Alternativen erscheinen jetzt in einem Feld, das offen bleibt**, bis du eine per
   **＋ übernehmen** ins Raster holst oder es mit **✕** schließt (kein flüchtiger Hinweis mehr).
   Eigene Parameter/Ausprägungen über **＋ Parameter** bzw. **＋** in der Zeile.
4. **📊 KI: Kombination bewerten** — bewertet die gewählte Lösung (Gesamt-/Machbarkeits-/
   Innovations-Score, Begründung, Risiken) und schlägt interessante Kombinationen vor,
   die du per **Übernehmen** ins Raster setzt.
5. **💾 Lösung + Bewertung merken** — legt die aktuelle Kombination (mit der zuletzt
   erhaltenen Bewertung) in einer **Lösungsliste** ab. So sammelst du mehrere Varianten
   mit ihren Scores zum Vergleich (laden/löschen je Eintrag).
6. **Exportieren:** **DOCX**, **→ Doku** (in den Dokumentengenerator), **Wissensdatenbank**,
   **CSV-Im-/Export**, sowie für die gemerkten Lösungen:
   - **🧠 Trainingsfile (JSONL)** — je Lösung eine Zeile im Chat-Format (Frage = Aufgabe +
     Parameterraum, Antwort = gewählte Kombination + Begründung), direkt zum **Finetunen
     eines LLM** verwendbar.
   - **📊 Auswertung (CSV)** — Tabelle Lösung × {Score, Machbarkeit, Innovation, gewählte
     Ausprägungen} zur Weiterauswertung (z. B. in Excel).

   Der Stand wird automatisch im Browser gespeichert (übersteht einen Reload).

### 🃏 Ideen wischen (kreative Ideenfindung)

Mit **🃏 Ideen wischen** denkt sich die KI laufend **ganze Konzept-Ideen** aus (je eine
Ausprägung pro Parameter + ein kurzer Konzepttitel) und legt sie als Kartenstapel vor:

- **Nach links wischen = gut**, **nach rechts wischen = schlecht** (am PC: Karte mit der
  Maus ziehen oder die Knöpfe **👍 Gut** / **👎 Schlecht**).
- Nach jedem Wisch kannst du **kurz begründen**, *warum* die Idee gut bzw. schlecht ist
  (überspringbar – einfach leer lassen + OK).
- Gute Ideen werden gleich **in den Kasten übernommen**. Geht der Stapel zur Neige, lädt die
  KI im Hintergrund nach (oder **🔄 Mehr Ideen**).

### 🧠 Automatisches Trainingsfile

Während du arbeitest, sammelt die App **gute und schlechte Beispiele automatisch** in einer
Datei auf dem Rechner (`data/morph_training/<thema>.jsonl`):

- **Wischen** (gut/schlecht + deine Begründung),
- eine **ausformulierte Karte gelöscht** → wird als **„schlecht"** vermerkt,
- eine **Lösung gemerkt** → wird als **„gut"** vermerkt.

Über **⬇ Auto-Trainingsfile** lädst du diese Sammlung herunter (zum Finetunen eines LLM),
über **📚 Training → Wissensdatenbank** schiebst du sie als lesbare Gut/Schlecht-Liste in eine
**Wissensdatenbank**.

### 🌐 Web / 📚 Wissensdatenbank als Quelle

Über der Tabelle wählst du **Informationsquellen** für die KI: **🌐 Web** (DuckDuckGo) und/oder
eine oder mehrere **📚 Wissensdatenbank(en)**. Sie fließen als Inspiration in **Parameter
generieren**, **Ideen wischen** und **💬 Alternativen** ein (**↻** aktualisiert die Liste).

> **Am Handy nutzen (PWA):** Die Oberfläche lässt sich aufs Handy „installieren" (Frontend am
> Handy, Backend bleibt am Rechner). Dafür den Server im **Servermodus** (`0.0.0.0`) starten und
> am Handy die Adresse `http(s)://<Rechner-IP>:8780` öffnen → Browser-Menü **„Zum Startbildschirm
> hinzufügen"**. Die **Wischen**-Ansicht ist auf Touch ausgelegt. Hinweis: Die echte Installation
> verlangt **HTTPS** (oder `localhost`); über einfaches `http://…` läuft alles im Handy-Browser,
> ist aber nicht installierbar (Details siehe Entwickler-Doku, Stichwort selbstsigniertes Zertifikat).

---

## 11c. Postfach — PST-/Mail-Auswertung

Tab **📮 Postfach** — liest **E-Mail-Postfächer** ein und wertet sie als **Wissensgraph** aus.
Arbeitet **ausschließlich lokal**: alle KI-Schritte (Anhang-Analyse, Ähnlichkeit, Fragen,
Zusammenfassung, Graph-Befehl) laufen über ein **lokales** Modell (Ollama). Ohne lokales LLM
sind diese Funktionen deaktiviert (klare 503-Meldung) — deine Mails verlassen den Rechner nie.

**Formate:** `.pst` (Outlook), `.mbox`, `.eml`, `.msg`. Für `.pst` ist **kein Outlook nötig** —
ein eingebauter, reiner-Python-Leser liest Unicode-PST direkt. PST-„Passwörter" sind technisch
nur eine Prüfsumme (keine Verschlüsselung): der Inhalt ist auch ohne Passwort lesbar; die Eingabe
im Passwortfeld wird nur **verifiziert** (Anzeige „🔓 korrekt" / „⚠ falsch").

### Einlesen
1. **Server-Pfad** zur Datei eingeben (z. B. `C:\Users\...\backup.pst`), bei Bedarf **Passwort**.
2. **📥 Einlesen** → **Stufe 1**: Absender, Empfänger, Betreff, Datum, Inhalt aller Mails.
3. Optional **🏷 Stufe 2: Anhänge & Tags** → liest **Anhänge** (Dokumente per Textauszug,
   **Bilder direkt am lokalen Vision-Modell**, kein OCR) und vergibt **Themen-Schlagworte**.
   Läuft über die aktuelle Auswahl/Filterung (markiert → gefiltert → alle).

### Wissensgraph & Konnektoren
Der Graph zeigt Mails als Blasen. **Klick auf eine Blase → ganze E-Mail**. Geordnet wird nach
selbst definierten **Konnektoren** (**🔗 Konnektoren**) — benannte Wortgruppen, z. B. Konnektor
*Lebensversicherung* = „Allianz, Continentale, CMI". Eine Mail hängt am Konnektor-Knoten, wenn
eines der Wörter in Absender/Betreff/Inhalt (nach Stufe 2 auch im Anhang) vorkommt.

**Drei Ansichten** (Modus-Umschalter in der Steuerzeile):
- **🔗 Konnektoren** — nach deinen Wortgruppen (+ optional Absender-Domains/Themen-Tags als
  Auto-Konnektoren).
- **🧬 Themen-Nähe** — **Verwandtschaftsgrad**: inhaltlich ähnliche Mails werden per lokaler
  Embeddings verbunden und farblich zu **Clustern** gruppiert; Schwellwert-Regler steuert die Dichte.
- **👥 Netz** — **Kommunikationsnetz**: wer mit wem (Absender/Empfänger), Kantenstärke = Häufigkeit;
  Klick auf eine Person filtert die Liste.

### ▶ Graph anzeigen (Play) & Filter
Damit die Bedienung auch bei großen Postfächern flüssig bleibt, wird der Graph **nicht live**,
sondern nur per **▶ Graph anzeigen** neu aufgebaut. Änderst du Einstellungen, pulsiert der Knopf
(„veraltet"). Liste, Zeitleiste und Suche bleiben sofort aktiv.

- **Konnektor-Schnellfilter** (Dropdown „Konnektor: …") — nur Mails eines Konnektors.
- **nur verbundene** — blendet isolierte Blasen (ohne Konnektor/Verbindung) aus.
- **⚙ Filter** klappt die Detailfilter auf: Datum von/bis, Absender/Domain, Anhang-Typ (PDF/Bild/
  Office), Themen-Tag, Ordner, „nur mit Anhang", **🔁 Duplikate** (Near-Duplikate). **Zeitleiste**
  zum Eingrenzen nach Monat, **🔎 Suche** über alles.

### 💬 Chat — fragen & steuern
**💬 Chat** klappt zwei Eingaben auf (alles lokal):
- **💬 Postfach fragen** — übernimm Mails per **📚 In RAG** in eine lokale Wissensdatenbank und
  stelle dann Fragen dazu; die Antwort nennt **Quellen**.
- **🗣 Graph-Befehl** — sag in Worten, was gezeigt werden soll, z. B. *„zeige Synera mit Eltern
  und Kindern"* (zentriert auf passende Knoten samt Nachbarschaft), *„nur Konnektor
  Lebensversicherung als Netz"* oder *„Mails im Dezember mit Anhang"*. Ein lokales Modell setzt
  daraus Modus, Filter, Fokus und Nachbarschaftstiefe.

### Auswerten, speichern, exportieren
- **🧾 Zusammenfassen** — fasst die Auswahl lokal zusammen. **📊 Statistik** — Top-Absender,
  Volumen pro Monat, Anhangsquote. **⬇ CSV / ⬇ JSON** — exportiert die gefilterten Mails.
- **💾 Speichern** — sichert die aktuelle **Ansicht + Konnektoren** zum Postfach.
- **📂 Gespeicherte** — bereits eingelesene Postfächer **wieder öffnen** — ohne die `.pst` erneut
  zu parsen; die berechnete Themen-Nähe und deine gespeicherte Ansicht kommen mit zurück.

> **Ein Arbeits-Spinner** oben rechts zeigt an, wenn eine Aktion (Einlesen, Analyse, Ähnlichkeit,
> Frage, Graph-Befehl) läuft.

---

## 11d. Patente — Patent-Recherche

Tab **⚖️ Patente** — recherchiert **Patente**, sammelt sie in **Fallakten** und wertet sie mit
einer mehrstufigen **KI-Analyse** aus.

> **Datenquellen:** Mit hinterlegtem **EPO-OPS-Key** (siehe unten) kommen die Daten **amtlich**
> von der Europäischen Patentorganisation — inklusive **Rechtsstand**, **Patentfamilie**,
> Anmelde-/Prioritäts-/Publikationsdaten, Erfindern und CPC-Klassen. Ohne Key (oder als
> Ergänzung) wird auf das **Scraping** öffentlicher Google-Patents-Seiten zurückgegriffen
> (keine offizielle API — bei Massenabrufen die Nutzungsbedingungen beachten; Abrufe laufen
> gedrosselt und über einen lokalen Cache). Die KI-Analyse läuft je nach Profil lokal oder –
> wenn „Web-Recherche lokal" aus ist – über dein API-Modell (Rolle „Wissenschaftlich").

0. **🔑 EPO OPS einrichten (empfohlen, einmalig)** — im Import-Bereich den Abschnitt
   „EPO OPS — amtliche Datenquelle" aufklappen. Kostenlosen Developer-Account unter
   **developers.epo.org** anlegen, dort *Consumer Key* + *Secret* erzeugen und im Tab
   speichern (**💾 Verbinden** prüft den Zugang sofort). Der Status zeigt „✓ verbunden";
   Suche und Abrufe tragen dann das Badge **🏛 EPO**.
1. **Projekt (Fallakte) wählen oder anlegen** — oben über das Dropdown bzw. `Neues Projekt… ＋ Anlegen`.
2. **🔍 Import** — Patente hinzufügen auf drei Wegen:
   - **Exakte Nummer** (z. B. `US10000000`) → **📥 Abrufen**.
   - **Suche** nach *Stichwort* (Boolean AND/OR/NOT erlaubt), *Rechteinhaber*, *Land*,
     **IPC/CPC-Klasse** und **Publikationszeitraum** (von/bis). Das Badge zeigt die Quelle
     (🏛 EPO amtlich / 🌐 Google-Fallback); Fehler werden angezeigt statt verschluckt.
   - **Massenverarbeitung**: mehrere Patentnummern (je Zeile oder komma-getrennt) → **Patente lesen**
     bzw. **⚙ Stapelverarbeitung starten**. Bestehende Akten lassen sich als **JSON** wieder einlesen.
3. **📁 Akte** — die gesammelten Patente mit Titel, Zusammenfassung, Ansprüchen, IPC/CPC,
   Rechteinhabern, Erfindern, Daten sowie (mit OPS) **Rechtsstand** und **Patentfamilie**.
4. **⚗ Analyse** — startet die **KI-Analyse-Pipeline** nach Prüfer-Methodik: Technik (mit
   Prüfschleife) → **📐 Merkmalsanalyse** (Anspruch 1 element-weise als Tabelle; bei zwei
   Dokumenten als Claim-Chart-Gegenüberstellung identisch/ähnlich/fehlt) → **🧪 Neuheit &
   erfinderische Tätigkeit** (EPA-Aufgabe-Lösungs-Ansatz: nächstliegender Stand der Technik
   aus der Akte → Unterschiedsmerkmale → objektive Aufgabe → Could-Would-Test, mit eigener
   Prüfschleife) → Recht (Schutzbereich je Merkmal) → Umgehung → Innovation → Entwurf →
   Kritik → **Moderator** (Management-Summary mit deterministischer Kennzahlen-Tabelle und
   klarer Handlungsempfehlung). Ergebnisse werden als Markdown gespeichert und lassen sich
   in eine **Wissensdatenbank** übernehmen.
5. **🛡 FTO-Check** (im Analyse-Bereich) — beschreibe **dein eigenes Produkt / deine Idee**
   im Textfeld und starte den Check gegen die ausgewählten Patente (max. 5): je Patent eine
   **Ampel-Tabelle je Anspruchsmerkmal** (verwirklicht / nicht verwirklicht / unklar, mit
   wörtlicher Fundstelle) nach der All-Elements-Rule plus Gesamtfazit.
   > ⚠️ Die automatisierte Auswertung ist **keine Rechtsberatung** — für belastbare
   > FTO-Aussagen immer einen Patentanwalt einbeziehen.
6. **📈 Stärke-Score** — jede Akte-Zeile erhält einen deterministisch berechneten
   Triage-Score 0–100 (Vorwärtszitate, Familiengröße, Restlaufzeit, Anspruchsbreite/-zahl;
   Spalte per Klick sortierbar; Details als Badges in der Patentansicht). Der Score
   priorisiert („welches Patent lohnt den Blick"), er bewertet nicht den Patentwert.
7. **📊 Statistik** — deterministisches Dashboard der Akte: Top-Anmelder, Anmeldungen pro
   Jahr, IPC-Hauptklassen, Score-Verteilung und eine **White-Space-Matrix** (IPC × Anmelder;
   leere Zellen = unbesetzte Felder).
8. **💬 Chat** — stelle Fragen zur gesamten Akte (belegt aus den enthaltenen Patenten).
9. **🕸 Graph** — Wissensgraph der Zusammenhänge (Rechteinhaber, IPC-Klassen, Themen).
10. **Export** — die Akte als **JSON** oder **CSV**.

---

## 11e. Angebot & Rechnung

Tab **🧾 Angebot / Rechnung** — erstellt **Angebote** und **Rechnungen** im Design deines
Firmenprofils, mit **rechnerisch korrekten** Beträgen.

> **Wichtig:** Die **Beträge berechnet der Server exakt** (Dezimalarithmetik) — **nie das
> KI-Modell**. Netto, Umsatzsteuer und Brutto folgen den **§14-UStG-Pflichtangaben**; die Option
> **Kleinunternehmer §19** weist keine USt aus. Das KI-Modell hilft nur beim **Zerlegen** einer
> Beschreibung in Positionen, nicht beim Rechnen.

1. Oben **Angebot** oder **Rechnung** wählen.
2. **Positionen erzeugen** – wahlweise:
   - **📄 Aus Angebot übernehmen** — ein gespeichertes Angebot als Basis laden (Abweichungen
     Ist ≠ vereinbart je Position „gesondert ausweisen" oder „verstecken").
   - **🔧 Vorgang zerlegen** — den Auftrag beschreiben; die KI zerlegt ihn nach wählbaren
     **Leistungskategorien** (Recherche, Planung, Konstruktion, Beschaffung, Fremdleistungen,
     Fertigung/Montage, Inbetriebnahme, Dokumentation, Projektmanagement; eigene ergänzbar) mit
     Stundensatz.
   - **✨ Freitext zerlegen** — die Rechnung frei beschreiben (z. B. „3 Tage à 800 €, 1,5 Std à
     95,50 €, Fahrtkosten 120 €"); die KI legt die Positionen an, der Server rechnet.
3. **Kunde** und **Rechnungsdaten** (Nummer wird automatisch vorgeschlagen, Datum, Leistungszeitraum,
   USt-Satz, Zahlungsziel, optionaler Einleitungssatz) ergänzen.
4. **Export** als **PDF** oder **DOCX**. Gespeicherte Vorgänge stehen im Verlauf; die Absenderdaten
   kommen aus dem **Firmenprofil**.

---

## 11f. Arbeitszeugnisse

Tab **📜 Zeugnisse** — formuliert **qualifizierte Arbeitszeugnisse** in üblicher, **codierter
Zeugnissprache** passend zur gewählten Gesamtnote.

> **Tipp:** Für saubere, juristisch übliche Formulierungen empfiehlt sich ein **API-Modell**
> (kleine lokale Modelle treffen die Zeugnis-Codes oft nicht zuverlässig).

1. **＋ Neues Zeugnis** — **Angaben** ausfüllen: Arbeitgeber (leer = Firmenprofil), Name, Geschlecht,
   Position/Funktion, optional Abteilung, **Gesamtnote** (1–5), Ein-/Austritt, Beendigungsgrund,
   Führungsverantwortung, **Aufgaben/Verantwortungsbereiche** (eine je Zeile) und besondere Stärken.
2. **Ausstellung** — Ort, Datum, Unterzeichner.
3. **📜 Zeugnis erzeugen** — die KI schreibt einen zur Note passenden Text. Er lässt sich
   **nachbearbeiten** und **speichern**.
4. **📁 Verlauf** — frühere Zeugnisse wieder öffnen; Export als **PDF/DOCX**.

---

## 11g. Variantenvergleich (🧮 Varianten)

Tab **🧮 Varianten** — hilft, **systematisch und nachvollziehbar** zwischen mehreren Alternativen
zu entscheiden (gewichtete Nutzwertanalyse nach dem **Paarvergleichs-Verfahren / AHP**). Die
**Rechnung ist deterministisch** — Gewichte und Ranking kommen vom Server, **nie vom KI-Modell**.
Die KI unterstützt nur beim Ausfüllen.

**🪄 Ganze Tabelle automatisch erzeugen:** Ganz oben im Vergleich kannst du im Feld
**„Problem beschreiben"** einfach dein Entscheidungsproblem in eigenen Worten eintippen und auf
**🪄 Tabelle automatisch erzeugen** klicken — die KI füllt dann **Kriterien, Paarvergleich, Varianten
und Bewertungen** in einem Durchlauf. Optional stellt sie vorab ein paar **Rückfragen (Interview)** und
nutzt eine **Webrecherche** (Häkchen setzen). Danach nur noch prüfen und anpassen. (Die Zahlen bleiben
Vorschläge; Gewichte und Ranking rechnet weiterhin der Server. Im Geheim-Modus bleibt das Modell lokal.)

**Ablauf (fünf Schritte, oben nach unten):**

1. **Vergleich anlegen** — oben einen Namen eingeben und **➕ Anlegen**. Titel und Beschreibung der
   Entscheidung eintragen. (Oder direkt den 🪄-Weg oben nutzen.)
2. **Kriterien** — die Entscheidungskriterien auflisten (z. B. Kosten, Qualität, Lieferzeit). Je
   Kriterium wählst du, ob ein **hoher Wert gut** („höher = besser") oder **schlecht** ist
   („höher = schlechter", z. B. Preis). **🤖 vorschlagen** lässt die KI passende Kriterien finden.
3. **Paarvergleich** — für je zwei Kriterien gibst du an, **wie viel wichtiger** das eine gegenüber
   dem anderen ist (Skala 1 = gleich wichtig … 9 = extrem wichtiger; Kehrwerte für die Gegenrichtung).
   Daraus errechnet der Server die **Gewichte** und die **Konsistenz (CR)** — eine grüne Anzeige
   (CR ≤ 0,10) bedeutet, dass deine Urteile widerspruchsarm sind; wird sie gelb, solltest du einzelne
   Vergleiche überdenken. **🤖 Vorschlag** füllt den Paarvergleich vor. **🎚 Schnellvergleich** blendet
   die Paare nacheinander als große Karten ein: mit **Pfeil ←** ist die linke, mit **Pfeil →** die rechte
   Seite wichtiger, mit **Pfeil ↑** sind beide gleich wichtig (auch per Klick/Wischen; **Esc** schließt).
   So klickst du dich in Sekunden durch alle Paare; die Feinjustierung ist danach in der Matrix möglich.
4. **Varianten & Bewertung** — die Alternativen eintragen (**🤖 vorschlagen** möglich) und in der
   Bewertungsmatrix je Kriterium mit **1–10** benoten (10 = am besten, auch bei Kosten). **🤖 bewerten**
   schätzt die Werte aus den Variantenbeschreibungen (und optional aus hinterlegten Quellen).
5. **Ergebnis** — ein **Ranking mit Balken** und markiertem Sieger. **🤖 Analyse** erklärt das Ergebnis
   inkl. Hinweis, wie stabil es gegenüber veränderten Gewichten ist. **⬇ CSV** exportiert die Rangliste.

Jeder Vergleich wird automatisch gespeichert (`data/varianten/`) und ist über die Auswahl oben wieder
abrufbar.

---

## 11h. To-Do mit Wissensgraph (✅ To-Do)

Tab **✅ To-Do** — eine **interaktive, KI-gestützte Aufgabenliste** als **Projektbaum**. Du schreibst
eine **Besprechungsnotiz**, lässt die KI daraus einzelne **Punkte** ableiten, hakst sie ab, verschiebst
sie zwischen Projekten, hängst **Dokumente** an (→ Markdown, mitgesucht) und siehst alles im
**Wissensgraph**. Alles wird in der **Datenbank** gespeichert (im Backup als `todo/todos.json`; die
Original-Anlagen unter `data/todo_att/`).

**🌳 Projektbaum (linke Spalte):** Die **Wurzel = dein Name** (aus dem Profil) ist deine persönliche
Liste. Darunter legst du **Unterprojekte** an (Feld *Name* + **➕ Unterprojekt** unter dem gewählten
Knoten) — beliebig tief. Je Knoten: **Klick** öffnet ihn, **✎** umbenennen, **✕** löschen (mit
Rückfrage: Unterprojekte mitlöschen oder hochziehen).

**⚡ Aktivieren (Scope):** Ein Klick auf **⚡** aktiviert ein Projekt — **Suche und Wissensgraph zeigen
dann nur diesen Teilbaum** (Projekt + Unterprojekte). Ist die **Wurzel** aktiv, sind **alle** Projekte
im Graphen verbunden. Der aktive Bereich steht oben (`⚡ aktiv: …`).

**↪ Verschieben & Sortieren:** Jeder Punkt hat **▲▼** (Reihenfolge) und ein **„↪ verschieben…"**-Menü,
das ihn in ein anderes Projekt des Baums verschiebt.

**Der Kern-Ablauf (rechte Spalte):**

1. **👥 Besprechungsheader** ausfüllen: **Thema / Worum geht es**, **Teilnehmer** und **Datum** (optional
   Verknüpfung mit einem Eintrag aus dem **Projekte-Tab**). Diese Angaben geben der KI Kontext und
   **fließen beim Ableiten in den Prompt ein** (Thema, Datum für Fristen, Teilnehmer als mögliche
   Zuständige).
2. **📝 Besprechungsnotiz** ins große Feld schreiben (oder einfügen).
3. **🪄 To-Do-Liste ableiten** — die KI macht daraus einzelne Punkte samt Zuständigen, Fristen und
   **Abhängigkeiten**. („ersetzen" anhaken, um vorhandene Punkte zu überschreiben statt zu ergänzen.)
4. **Punkte bearbeiten:** Text/Zuständige/Frist anpassen, mit dem **Haken** links auf **erledigt**
   setzen (durchgestrichen). Einzelne Punkte fügst du auch direkt per **➕ Punkt** hinzu.

**📎 Dokumente anhängen:** Über den **📎-Knopf** eines Punkts eine Datei anhängen (PDF, DOCX, XLSX,
Bilder, Text …). Der Inhalt wird automatisch in eine **Markdown-Datei** umgewandelt und beim Punkt als
anklickbarer Chip abgelegt. Diese Markdown-Anlagen werden von der Suche **mitdurchsucht**.

**🔍 Suche (oben rechts):** durchsucht den **aktiven Bereich** — Aufgabentexte, Zuständige **und den
Inhalt der angehängten Dokumente**. Ist die Wurzel aktiv, geht die Suche über **alle** Projekte. Ein
Klick auf einen Treffer öffnet das passende Projekt und springt zum Punkt.

**🎯 Empfehlung – was als Nächstes?** Ein eigener Untertab **priorisiert deterministisch** (kein
KI-Raten), was im **aktiven Bereich** als Nächstes drankommt — nach **Fälligkeit** (überfällig / in X
Tagen), **Abhängigkeiten** (was ist blockiert, was *entblockt* andere Punkte) und **Status** (läuft vor
offen). Über **„Für: <Person>"** filterst du auf eine Zuständige — dann siehst du **deren** nächste
Schritte. Die Liste ist dreigeteilt: **🔥 Jetzt dran** (nummeriert, mit Badges wie *überfällig*,
*entblockt N*, *läuft*), **🕒 Demnächst** und **⛔ Blockiert** (mit „wartet auf: …"). Klick auf einen
Eintrag springt zum Punkt.

**🕸 Wissensgraph:** Über **▶ Graph aufbauen** entsteht der Graph des **aktiven Bereichs** —
**Knoten = Punkte**, farbige **Hubs = Zuständige und Status**, **Pfeile = Verknüpfungen**. Mit
**🔗 Verbinden** ziehst du selbst Verknüpfungen, **✨ Verknüpfungen** lässt die KI Beziehungen
vorschlagen, **🤖 Nächste Schritte** nennt das Nächstliegende und Blockaden. Umfasst der Bereich mehrere
Projekte (Wurzel aktiv oder Umschalter **🌐 Alle Projekte**), erscheinen sie in **einem** Graphen —
je Projekt eine eigene Rahmenfarbe, verbunden über gemeinsame Zuständige. **Bei sehr vielen Punkten**
(z. B. der ganze Bestand des großen Demos) warnt der flache 2D-Graph und baut erst nach Bestätigung auf,
damit der Browser nicht einfriert — dann besser ein **Einzelprojekt aktivieren (⚡)**, den
**👤 Personenfilter** nutzen oder die **🔮 3D-Kugel** wählen.

**🔮 2D / 3D-Kugel:** Mit dem Umschalter **🕸 2D / 🔮 3D-Kugel** wechselst du zwischen dem flachen Graphen
und einer **rotierenden 3D-Kugel**. In der Kugel liegen die Punkte auf einer Kugeloberfläche, die
Zuständigen-Hubs im Inneren. **Ziehen dreht** die Kugel, das **Mausrad zoomt**, ein **Klick** zeigt die
Punktdetails, ein **Doppelklick** schaltet die automatische Drehung an/aus. Die 3D-Ansicht läuft flüssig
und **friert auch bei sehr vielen Punkten nicht ein** — ideal, um den ganzen Bestand auf einmal zu sehen.

**🧩 Ansicht anpassen (Splitter, Ein-/Ausklappen, Filter):** Die **Trennlinie** zwischen Projektbaum
und Inhalt lässt sich **ziehen** (Breite bleibt gespeichert). Im Untertab **Liste** sind die drei
Bereiche **👥 Besprechungsheader**, **📝 Besprechungsnotiz** und **✅ Punkte** über das **▾** in der
Kopfzeile **einklappbar**, und die **waagerechten Trennlinien** dazwischen lassen sich **ziehen**, um
den Bereichen mehr oder weniger Höhe zu geben (alles wird gespeichert). Direkt in der **Punkte-Kopfzeile**
(ohne extra Platz) sitzen **Filter**: **„nur offene"** (erledigte ausblenden), ein **Fälligkeits-Zeitraum**
(von–bis) und ein **Zuständigkeits-Filter** (👤). Der Zähler zeigt dann „… · X/Y sichtbar"; **✕** setzt
die Filter zurück. Im **Wissensgraph** gibt es zusätzlich einen **Personenfilter** (👤), der den Graphen
auf die Punkte einer Person eingrenzt.

**📤 Export / 📥 Import / 🗑 Reset:** In der oberen Leiste kannst du die **gesamte Projektliste als
JSON-Datei exportieren** (Sicherung/Weitergabe) und eine solche Datei wieder **importieren** (Projekte
mit gleicher Kennung werden ersetzt, andere ergänzt; deine Wurzel bleibt). **🗑 Reset** leert die
**komplette** Liste – vorher wird **automatisch eine Sicherung** der alten Liste angelegt
(`data/todo_backups/todo_backup_<Zeitstempel>.json`), sodass nichts verloren geht.

**🤖 Über die Daten fragen (Daten-Chat):** Oben rechts in der To-Do-Leiste gibt es eine **Chat-Zeile**.
Damit **sprichst du mit einem Sprachmodell über deine To-Do-Daten** — es liest Aufgaben, Zuständige,
Status, Fristen, Notizen und Anhänge des **aktiven Bereichs** (Wurzel aktiv = alles). Beispiele:
*„Wer arbeitet an den meisten Projekten?"*, *„Welche Aufgaben sind überfällig und blockieren andere?"*,
*„Erstelle ein Persönlichkeitsprofil von Anna Berger anhand ihrer Aufgaben und Notizen."* Die Antwort
erscheint in einem **einklappbaren Feld** über der Liste. So wird sichtbar, wie sich aus einer über
Jahre gepflegten Liste **vernetzte Informationen** über Kollegen ergeben.
>
> **Datenschutz:** Der Daten-Chat rechnet **lokal-bevorzugt** — bei sensiblen Personen-/Kollegen-Fragen
> bleibt er **auf diesem Rechner** (nutzt das lokale Sprachmodell), außer du erlaubst im Profil
> ausdrücklich API-Modelle für vertrauliche Auswertungen. Im **🔒 Geheim-Modus** bleibt es immer lokal.
> Personenauswertungen sind bewusst **sachlich-neutral** aus den Daten abgeleitet (keine erfundenen
> oder abwertenden Aussagen). *Hinweis: Werte Daten über andere Menschen nur mit deren Wissen und im
> erlaubten Rahmen aus.*

**Demonstrations-Projekt:** Zum Ausprobieren bringt die App einen großen **fiktiven Beispiel-Baum**
mit (**~25 Projekte, ~1000 Aufgaben**: operative Projekte, ein **🎉 Firmenfest** und ein bewusst
deplatziertes **„Persönliches"**-Projekt) und **14 erfundenen Kolleg:innen**, die **projektübergreifend**
auftauchen. Er zeigt eindrücklich,
was der Daten-Chat und der Wissensgraph aus vernetzten Informationen herausholen. Die Namen sind frei
**umbenennbar**, und du kannst die Demo-Projekte jederzeit löschen.

> **Speichern:** **💾 Speichern** legt Punkte, Verknüpfungen und Anlagen in der Datenbank ab. Verschieben,
> Umsortieren und Aktivieren wirken sofort. Ein **Backup** (Profil-Modal) sichert den ganzen Baum
> (`todo/todos.json`) samt Original-Anlagen.

---

## 11i. Transkription — Sprache zu Text (🎙 Transkription)

Tab **🎙 Transkription** — wandelt **gesprochene Sprache in Text** um. Als Quelle dient das
**Mikrofon** (auch ein **USB-Mikrofon**) oder eine **Audiodatei** (mp3, wav, m4a, ogg, webm …).

**Engine wählen:**
- **Lokal (faster-whisper)** — läuft **auf diesem Rechner**, die Audiodaten verlassen ihn nicht.
  Standardmäßig auf der **CPU** (hält die Grafikkarte für die Sprachmodelle frei; läuft auch auf
  einer 6-GB-Karte). Das Modell (Standard **base**) wird bei der Installation vorab geladen.
- **API-Modell** — schickt die Aufnahme an einen externen Anbieter (z. B. OpenAI/Groq mit einem
  `whisper`-Modell). Nur sinnvoll, wenn oben im Profil ein KI-Anbieter hinterlegt ist. ⚠ Die
  Audiodaten gehen dann an den Anbieter. **Im Geheim-Modus ist diese Option gesperrt.**

**So geht's:**
1. **Engine**, **Modell**, **Sprache** (oder *automatisch erkennen*) und **Aufgabe**
   (*Transkribieren* = Originalsprache · *Ins Englische übersetzen*) wählen.
2. **🎤 Mikrofon:** Gerät im Dropdown wählen (USB-Mikrofone erscheinen nach der ersten
   Freigabe mit Namen), **● Aufnahme starten**, sprechen, **■ Aufnahme stoppen** — der Text
   erscheint automatisch. **Oder 📄 Datei:** Audiodatei wählen und **Transkribieren**.
3. **📝 Ergebnis:** Der Text steht editierbar im Feld, darunter **Zeitmarken** je Abschnitt.
   **🔊 Vorlesen** (Sprachausgabe), **📋 Kopieren**, **⬇ .txt** speichern, **→ Chat** (Text in den
   Chat übernehmen) oder **→ To-Do** (als Notiz ins To-Do, dort „To-Do-Liste ableiten").

> **Wo werden Sprach-Ein- und -Ausgabe gespeichert?** Die **Spracheingabe** (deine Aufnahme bzw.
> hochgeladene Audiodatei) wird serverseitig unter `data/transcripts/` abgelegt und ist im **Backup**
> enthalten (Schalter „Uploads/Medien"). Die **Sprachausgabe (Vorlesen / 🔊)** entsteht **live im
> Browser** über die System-Stimmen und wird **nicht als Datei gespeichert**.

**🎙 Diktat im Chat:** In der Chat-Eingabeleiste gibt es zusätzlich einen **🎙 Diktat**-Knopf —
einmal klicken nimmt auf, erneut klicken stoppt und schreibt den erkannten Text direkt ins
Eingabefeld. Es gelten die Engine-/Modell-/Spracheinstellungen des Transkriptions-Tabs.

---

## 11j. Geheim-Modus — alles lokal

In der **Sidebar** (unter „Profil bearbeiten") gibt es den Umschalter **🔒 Geheim-Modus**. Ist er
**an**, laufen **sämtliche Modell-Rollen zwingend auf den lokalen Standardmodellen** — jede zuvor
gewählte API-/Remote-Zuweisung wird **überall ignoriert** (Chat, Recherche, vertrauliche
Auswertungen, Transkription). So bleibt garantiert **alles auf diesem Rechner**. Der Schalter geht
**in beide Richtungen**: schaltest du ihn wieder **aus**, gelten deine gespeicherten (ggf.
Remote-)Modelle sofort wieder. Bei aktivem Modus zeigt die Marke oben **🔒 lokal**. Denselben
Schalter findest du auch im **Profil-Modal** („🔒 Geheim-Modus — alles lokal").

---

## 11k. Sprachausgabe — Antworten vorlesen (🔊)

Antworten lassen sich **vorlesen**. Über jeder Assistenten-Antwort im Chat erscheint ein
**🔊-Knopf** (neben „⬇ .md") — Klick liest die Antwort vor, ein weiterer Klick stoppt. Auch im
**Transkriptions-Tab** gibt es **🔊 Vorlesen**.

Die Stimme richtet sich nach der gewählten **Antwortstil-Persona** (Profil → *Antwortstil*):

| Persona | Stimme |
|---|---|
| 🤖 **Roboter** | synthetisch/monoton |
| 🎓 **Herr Professor** | älterer Mann |
| 🩺 **Frau Doktor** | ältere Frau |
| 😎 **Felix** | junger Mann |
| 😊 **Sandra** | junge Frau |
| 🎖 **Gunnery Sergeant Hartman** | zackig, laut (tief & schnell) |

> **🎖 Gunnery Sergeant Hartman** ist ein **Sondermodus**: Die Antworten werden **militärisch zackig**
> (kurze Befehlssätze, forscher Ton) — der Drill ist aber nur der *Stil*, der Inhalt bleibt fachlich
> korrekt und hilfreich (keine Beleidigungen). Zusätzlich schaltet dieser Modus einen
> **Ausbildungs-/Lokal-Riegel** ein: es sind **nur lokale Modelle** nutzbar und **jede Websuche ist
> gesperrt** — alles bleibt rein lokal. Der **🔒 Geheim-Button** bleibt davon unberührt (unabhängig
> schaltbar).

Im **Profil-Modal** kannst du die Stimme mit **🔊 Stimme testen** direkt anhören. Die Sprachausgabe
nutzt standardmäßig die **im Betriebssystem/Browser installierten Stimmen** (Windows/Linux), läuft
**komplett lokal im Browser** und wird **nicht gespeichert**. Stehen für ein Geschlecht mehrere
Stimmen bereit, werden Alter/Klang zusätzlich über **Tonhöhe** unterschieden (tiefer = älter).
Unterstützt ein Browser keine Sprachausgabe, wird der 🔊-Knopf ausgeblendet.

**API-Stimme (optional):** Im Profil unter **🧠 Modelle → 🔊 Sprachausgabe (TTS)** kannst du statt
„Browser (lokal)" ein **API-Modell** eines konfigurierten Anbieters wählen (z. B. `openai::tts-1`) —
dann werden die Antworten mit den **hochwertigeren Anbieter-Stimmen** vorgelesen (das Audio entsteht
beim Anbieter). Die Persona bestimmt weiterhin die Stimme. ⚠ Der Text geht dabei an den Anbieter.
Im **Geheim-Modus** wird immer die **Browser-Ausgabe** genutzt; scheitert die API, wird automatisch
auf den Browser zurückgefallen.

---

## 11l. Bildgenerierung — Bilder aus Text (🎨)

Der Chat kann aus einer Beschreibung **Bilder erzeugen** — wahlweise **lokal** oder über ein **API-Modell**.
Standardmäßig ist die Funktion **aus**; du wählst sie einmalig im **Profil** unter **🧠 Modelle → 🎨 Bildgenerierung**:

- **Lokal · Stable Diffusion WebUI** — du betreibst einen eigenen Bild-Server
  (**AUTOMATIC1111**, **Forge** o. Ä.), gestartet **mit `--api`**, und trägst dessen **Adresse**
  ins Feld darunter ein (Standard `http://127.0.0.1:7860`). Nichts verlässt deinen Rechner.
- **API** (`dall-e-3` / `gpt-image-1`) eines konfigurierten Anbieters — das Bild entsteht beim Anbieter.
  ⚠ Deine Beschreibung geht dann nach außen. Im **Geheim-Modus** ist nur der lokale Weg erlaubt.

**Bild erzeugen** — drei Wege im Chat:

1. **🎨 Bild**-Haken in der Toolbar setzen → die **nächste Nachricht** wird als Bild-Prompt behandelt (danach automatisch wieder aus).
2. Befehl **`/bild <Beschreibung>`** — z. B. `/bild ein roter Sportwagen bei Sonnenuntergang, Fotorealistisch`.
3. **`/bildhelp`** öffnet einen **geführten Dialog**, der **Motiv, Stil, Kameraperspektive, Beleuchtung,
   Seitenverhältnis** und optional einen **Negativ-Prompt** (was *nicht* im Bild sein soll) abfragt und
   daraus den Prompt baut.

Das fertige Bild erscheint in der Antwort mit einem **⬇ Speichern**-Link. Ist noch kein Bildmodell
eingerichtet, weist ein Hinweis darauf hin.

---

## 12. Planer (Netzplan / CPM)

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
  bestimmst du die gewünschte Anzahl (bis 300).
  > **Hinweis:** Bei vielen Aufgaben (> 30) stoßen kleine lokale Modelle an ihre
  > Grenzen – es kommt eine Rückfrage, und das Ergebnis kann unvollständig sein.
  > Für große Pläne ein **größeres/leistungsfähigeres Modell** verwenden oder den
  > Plan **in Phasen** generieren. Liefert das Modell zu wenig, erscheint eine
  > Warnung.
- **📄 Dokument → Plan** — importiere ein **Dokument** (PDF/DOCX/**MD**/TXT/XLSX/CSV,
  z. B. ein Strategiepapier). Die KI **liest es, leitet die nötigen Ressourcen ab** und
  erstellt einen kompletten Plan mit der bei **Aufgaben** gewählten Ziel-Vorgangszahl.
  Eine zusätzlich gewählte **📚 Wissensdatenbank** fließt als Beleg mit ein. Eingabe-
  und Kontextfenster sind gekoppelt (`num_ctx = max(8192, Profil-Kontext)`) — auf einem
  **leistungsfähigen Rechner mit großem Modell** sind so auch 100+ Vorgänge in einem
  Zug möglich.
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

### 🔗 Auto-Strukturieren — Projekt automatisch verknüpfen

Hast du eine **flache Aufgabenliste ohne Abhängigkeiten** (z. B. aus einer Anfrage
über **➜ In Planer übernehmen** oder **📋 Aus Liste**), strukturiert **🔗 Auto-Strukturieren**
sie automatisch. Die KI berücksichtigt dabei vier Aspekte:

- **Fachliche Abhängigkeiten** — leitet ab, welche Aufgabe vor welcher fertig sein muss.
- **Phasen / Bereiche** — ordnet jede Aufgabe einer Projektphase zu (Konzept,
  Konstruktion, Test …).
- **Ressourcen-Entzerrung** — legt Aufgaben **derselben Rolle nacheinander** statt
  parallel, damit eine Person/Maschine nicht doppelt belegt wird.
- **Parallelstränge** bleiben erhalten — fachlich unabhängige Aufgaben werden **nicht**
  künstlich verkettet.

Du wählst per Häkchen, welche der drei Verknüpfungs-Arten genutzt werden, und klickst
**▶ Vorschau berechnen**. Die **Vorschau** zeigt je Phase, welche Aufgabe von welcher
abhängt, plus eine Zusammenfassung (Anzahl Abhängigkeiten, Entzerrungen, Phasen). Erst
**✓ Anwenden** ersetzt die bestehenden Verknüpfungen — alle Zyklen werden dabei
verhindert. Mit **↩ Rückgängig** nimmst du die Strukturierung wieder zurück.

> Danach im **📅-Dialog** die **Kapazität & Zukauf**-Analyse öffnen: durch die
> Entzerrung verteilen sich die Aufgaben über die Zeit, und der Bestellplan/​Konflikt­
> abschnitt wird aussagekräftig.

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

## 13. Matrix-Recherche

Tab **📊 Matrix** — Recherche als Tabelle:

- **Spalte 1** = Themen, **Kopfzeile ab Spalte 2** = Suchprompts.
- **Agent je Spalte:** Unter jedem Suchprompt lässt sich ein **eigener Agent**
  wählen (nur als **Favorit** markierte Agenten erscheinen hier). So kann jede
  Spalte anders arbeiten — z. B. ein **Firmenagent** zur Messebesuch-Vorbereitung,
  der **Rechercheur & Bewerter** (sucht *und* bewertet Relevanz/Schlüssigkeit) oder
  der **Halluzinationsprüfer** (prüft Angaben auf Plausibilität). *„Kein Agent"* =
  einfache Websuche.
- Zelle anklicken = einzeln ausführen; **▶ Alle ausführen** = der Reihe nach.
- **Pro Spalte schaltbar:** Häkchen **„ausführen"** (wird bei *Alle ausführen*
  berücksichtigt), Häkchen **„Kontext"** (gibt die fertigen Ergebnisse der vorherigen
  Spalten derselben Zeile als Grundlage mit) und **▶ Spalte** (führt nur diese eine
  Spalte für alle Zeilen aus). So lässt sich eine mehrstufige Auswertung **Schritt
  für Schritt** abarbeiten.
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

### 🤝 Partner-Auswertung (Vertriebs-/Akquise-Recherche)

Der Button **🤝 Partner-Auswertung** richtet die Matrix als mehrstufige
Partner-/Lead-Recherche ein:

1. **📋 Firmenliste** öffnet ein Eingabefenster — füge eine **beliebig lange Liste**
   ein (eine Firma pro Zeile). Jede Zeile wird zu einer Tabellenzeile.
2. **🤝 Partner-Auswertung** legt die vordefinierten Spalten an und erstellt – falls
   noch nicht vorhanden – die nötigen **Agenten** (vorhandene werden **nicht**
   überschrieben):
   - **Interesse & Profil** — prüft per Websuche, ob die Firma als Partner/Kunde
     interessant ist, erstellt ein Profil und nennt mögliche Ansprechpartner.
   - **Kontaktdaten** — Name, Position, Telefon, E-Mail der Ansprechpartner
     (nur öffentlich belegbar).
   - **LinkedIn / X / Instagram / Facebook / GitHub** — je eine Spalte, sucht das
     öffentliche Profil der Firma bzw. der Personen.
   - **Kaltakquise-Mail** — formuliert eine personalisierte Erstkontakt-E-Mail aus
     den gefundenen Infos.
3. **Stufenweise arbeiten:** Nur die erste Spalte ist anfangs aktiv. Mit **▶ Spalte**
   führst du die nächste Stufe aus; die Spalten mit **„Kontext"** nutzen dabei die
   Ergebnisse der vorherigen Stufen derselben Zeile.

Die beiden zentralen Agenten (**Partner-Rechercheur** und **Akquise-Texter**) sind
**ganz normale Agenten** und im **🤖 Agenten-Tab** frei an deine Firma, Tonalität und
Zielgruppe **anpassbar**.

> **Hinweis:** Die Recherche stützt sich auf die öffentliche DuckDuckGo-Websuche;
> je nach Plattform sind die Treffer unterschiedlich vollständig. Die Agenten sind
> **DSGVO-bewusst** formuliert und nutzen nur öffentlich auffindbare Angaben.

---

## 14. Code-Tab (IDE + JSON-Editor)

Tab **💻 Code** — oben zwei Untertabs: **IDE** und **JSON-Editor**.

### Untertab „IDE"

Erzeugt und führt **HTML5-Canvas-Programme** aus, z. B. für Toleranzanalysen oder
Diagramme. Für Techniker gedacht, **ohne Programmierkenntnisse** nutzbar.

### KI-Assistent (Haupteinstieg)

Im blau hinterlegten Feld beschreiben, was das Programm zeigen soll, dann
**▶ Code erstellen** klicken. Die KI schreibt den Code, übernimmt ihn in den Editor
und führt ihn sofort aus. Welches Modell der IDE-Assistent nutzt, legst du im
**Profil → 🧠 Modelle → „Programmieren / Mathe"** fest (gemeinsam mit dem Mathe-Tab;
leer = `ministral-3:3b`).

> *„Zeige ein Balkendiagramm der Zugfestigkeit für Stahl, Alu und Titan"*
> *„Erstelle eine Toleranzanalyse für drei Bauteile"*

**Rückfragen vor dem Coden:** Ist die Option **„Rückfragen"** aktiv (Standard), stellt der
Assistent zuerst kurze Rückfragen, wenn wesentliche Informationen fehlen. Du beantwortest
sie im Feld und klickst **„↑ Antworten & Code erstellen"** — oder **„⏭ Trotzdem coden"**,
wenn es direkt losgehen soll.

**Coding-Agent:** Über das Auswahlfeld **Coding-Agent** wählst du einen Agenten, dessen
Fachrolle (System-Prompt) und optionaler **Beispielcode** den Stil/Struktur der Lösung
vorgeben. Beispielcode hinterlegst du beim Agenten (Tab **🤖 Agenten** → Feld
**Beispielcode**). Agenten der Kategorie *Programmieren* bzw. mit Beispielcode (📎) stehen
oben in der Liste.

**🤖 Autonomer Agent (Agent-Harness):** Der Knopf **🤖 Agent** öffnet ein Aufgabenfeld für
einen **selbstständig arbeitenden** Coding-Agenten (wie Aider/Claude-Code). Du beschreibst nur
das Ziel — der Agent **plant, legt/ändert mehrere Dateien selbst an, prüft sein Ergebnis**
(Python im Sandkasten bzw. HTML/JS im **Canvas**) **und behebt Fehler** in mehreren Schritten,
bis es läuft. Das **Schritt-Protokoll** läuft live mit; mit **⏹ Stopp** brichst du ab, mit
**↩ Rückgängig** nimmst du alle Änderungen des Laufs zurück. Für **Web-/Canvas-Aufgaben**
(z. B. *„Baue ein Snake-Spiel auf `<canvas>`"*) rendert er das Ergebnis direkt und repariert
erkannte Konsolenfehler automatisch (bis zu 2 Runden).

> **Wichtig:** Der Agent braucht **zuverlässiges Werkzeug-Verhalten** — stelle unter
> **Profil → 🧠 Modelle → „Programmieren / Mathe"** ein **fähiges** Modell ein (ein starkes
> lokales oder ein API-Modell). Das kleine Standardmodell `ministral-3:3b` schafft einfache
> Aufgaben, größere nur eingeschränkt. Ausgeführt wird ausschließlich in der **Python-Sandbox**
> bzw. im Browser-Canvas — **keine** echten Shell-/Systembefehle.

**Adaptiv:** Mit dem Häkchen **„adaptiv"** analysiert die KI die Begrifflichkeiten der
Aufgabe und wertet dein **Profil** (Position, Abteilung, Firma, Fachmodus) aus, um eine
passende Experten-Rolle abzuleiten — auch ohne expliziten Agenten.

### Komfort-Editor

Der Code-Editor bietet **Syntax-Highlighting, Zeilennummern, automatische Klammern,
Klammern-Hervorhebung und Autovervollständigung** (mit **Strg+Leertaste**). Tastenkürzel:
**Strg+Enter** = ausführen, **Strg+S** = speichern, **Tab** = 2 Leerzeichen einrücken.

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

## 15. Diagnose-Logger

Tab **📋 Logs** — ein zuschaltbares Protokoll zur Verbesserung und Fehlersuche.

- **▶ Logging aktivieren** schaltet die Aufzeichnung ein (Status: 🔴 Aktiv).
- Protokolliert: Chat-Anfragen (Modell, Dauer, Tools), Tool-Aufrufe, Exporte,
  Tab-Wechsel und Frontend-Fehler.
- **Filter** nach Typ, **↺ Aktualisieren**, **⬇ Download** (als `.log`),
  **✕ Leeren**.
- Standardmäßig **aus** — nur bei Bedarf aktivieren.

---

## 16. Agenten

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
- **📚 Dokument-Experte aus Datei:** Lade ein **Fachdokument** (Gesetz, Norm, Skript,
  Handbuch — PDF/DOCX/TXT) hoch, vergib einen Titel und ein **Fachgebiet/Rolle**
  (z. B. „Recht", „Physik", „Medizin"; leer = Recht) — daraus entsteht **automatisch**
  ein spezialisierter Experte. Das Fachgebiet passt **Persona und Zitierstil** an: ein
  Physik-Skript ergibt also einen Physik-Experten (Fundstelle = Abschnitt/Kapitel/
  Gleichung), ein Gesetzestext einen juristischen Assistenten (§/Artikel). Kurze Texte
  landen direkt im System-Prompt; lange Texte werden in eine eigene Wissensdatenbank
  ausgelagert und fest an den Agenten gebunden. Der Experte antwortet dann ausschließlich
  auf Basis des Dokuments und nennt die Fundstelle.
  > Für lange Dokumente muss das Embedding-Modell installiert sein (`ollama pull nomic-embed-text`).
- **⚖️ Jurys:** Über den Button **⚖️ Jurys** mehrere Agenten zu einem
  **Bewertungs-Gremium** bündeln (siehe Abschnitt 16a). Eine Jury bewertet einen Text
  — z. B. ein erzeugtes Dokument, einen System-Prompt oder den im Profil/Planer
  abgeleiteten Projekt-Agenten.
- **⚖️ Von Jury prüfen** im Agenten-Bearbeiten-Dialog lässt den aktuellen
  System-Prompt von einer Jury bewerten.
- **Projekt-gebundene Skill-Agenten:** Über **`/plan`** im Chat erzeugte Berater werden
  ihrem Projekt fest zugeordnet und erscheinen **nicht** im globalen Verzeichnis, sondern
  nur unter ihrem Projekt (siehe Abschnitt 18). So bleibt der Agenten-Tab übersichtlich.
- Agenten werden unter sprechenden Dateinamen gespeichert (`data/agents/`).

### 16a. Bewertungs-Jurys (⚖️)

Eine **Jury** ist ein gespeichertes Gremium aus mehreren Agenten (besonders sinnvoll
mit ⚖️ Gesetz-Agenten). Sie bewertet einen vorgelegten Text — auch einen
KI-generierten — und liefert **pro Mitglied ein Votum** (Score 0–100, Befund,
Risiken/Verstöße mit Fundstelle, Empfehlung) plus ein **Gesamturteil**.

**Jury anlegen:** im Agenten-Tab auf **⚖️ Jurys** → Name vergeben, Mitglieder
(Agenten) ankreuzen, speichern. Jurys lassen sich später bearbeiten und löschen. Die
Mitglieder-Auswahl ist **nach Projekt gruppiert**: oben die allgemeinen Agenten, darunter
je Projekt dessen Skill-Agenten — so findest du die projekt-eigenen Berater gezielt wieder.

**Bewerten lassen** kannst du an mehreren Stellen — überall öffnet sich dasselbe
Bewertungs-Fenster (Jury wählen → ▶ Bewerten):
- **Dokumente-Tab:** Button **⚖️ Von Jury prüfen** neben den Export-Knöpfen prüft das
  erzeugte Dokument.
- **Agenten-Tab:** im Bearbeiten-Dialog **⚖️ Von Jury prüfen** prüft den System-Prompt.
- **Planer-Tab:** nach **🧠 Projekt-Agent ableiten** prüft **⚖️ Agent prüfen** den
  abgeleiteten Agenten (mit der Projektbeschreibung als Kontext).

> Tipp: Binde an die Jury-Mitglieder die einschlägigen Gesetz-Agenten (mit hinterlegtem
> Normtext), dann werden konkrete Fundstellen (§/Artikel) in den Befunden genannt.

**Große Dokumente (mehr als das Kontextfenster):** Wichtig sind **zwei** verschiedene Dinge:
- **Die Fachgrundlage des Agenten** (z. B. ein Gesetzestext mit 100 000 Wörtern) gehört
  **nicht** in jede Anfrage, sondern in eine **Wissensdatenbank**. Lege den Agenten als
  **📚 Dokument-Experte** an (Agenten-Tab) — lange Texte landen automatisch in einer
  RAG-Basis, fest an den Agenten gebunden. Die Jury zieht daraus pro Votum nur die
  **relevanten Passagen**. So spielt die Textlänge keine Rolle.
- **Das zu bewertende Dokument** kann beliebig groß sein: passt es nicht in einen Durchgang,
  bewertet die Jury es **automatisch abschnittsweise** (Map-Reduce) und fasst die
  Abschnitts-Befunde je Mitglied zu einem Gesamtvotum zusammen (Fortschritt „prüft Abschnitt
  k/N…"). Die Abschnittsgröße richtet sich nach dem **Kontextfenster** (Profil) — ein
  größeres Fenster = weniger Abschnitte und schnellere Läufe, aber mehr VRAM. Hinweis:
  sehr lange Dokumente bedeuten **viele Modellaufrufe** (im Token-Zähler sichtbar).

**⚖️ Jury-Tab (Dokument-Werkbank):** Zusätzlich zum Bewertungs-Fenster gibt es einen
eigenen Tab **⚖️ Jury** *(im Profil einblendbar → Abschnitt 18)*. Dort kannst du
**Dokumente schreiben, einfügen, bearbeiten und speichern** und sie direkt von einer Jury
prüfen lassen:
- Links: Liste deiner Jurys und der **gespeicherten Dokumente**.
- Rechts: Editor (mit **👁 Vorschau**), Jury-Auswahl, **⚖️ Mit Jury prüfen** (zeigt die
  Voten direkt darunter), **💾 Speichern** sowie Export als **DOCX**, **→ Doku** oder in
  eine **Wissensdatenbank**.
- Gespeicherte Dokumente landen in der Datensicherung (Backup) und lassen sich später
  wieder laden und weiterbearbeiten.

### Standard-Agenten

| Agent | Kategorie | Beschreibung |
|-------|-----------|-------------|
| 🔬 Recherche-Agent | Wissenschaft | Quellengebundene Web-Recherche |
| ⚙️ Ingenieur-Assistent | Maschinenbau | Technische Berechnungen & Normen |
| 📊 Analyse-Agent | Wissenschaft | Daten- und Systemanalyse |
| 🖥️ Präsentations-Agent | Präsentation | Folien-Erstellung im Canvas |
| 💻 Programmier-Agent | Entwicklung | Code-Erstellung & IDE-Assistent |
| 🏗️ Werkstoff-Experte | Maschinenbau | Werkstoff-Lookup und -Vergleich |
| 📋 Requirements-Analyst | Dokumentation | Anforderungsanalyse |
| 📐 LaTeX-Experte | Dokumentation | LaTeX-Dokumente, Gleichungen, Berichte |
| 🔢 Mathe-Experte | Wissenschaft | Plots, SymPy, Gleichungssysteme, Statistik |
| 🩺 Medizin-Assistent | Medizin | Medizinische Fragestellungen & Befundanalyse |
| 🃏 Kanban-Coach | Prozesse | Agile Methoden und Kanban-Optimierung |

**Dokument-Agenten:** Agenten der Kategorie **„Dokumentation"** dienen als Vorlagen
im **Dokumentengenerator** (Tab 📄 Dokumente) — lege z. B. einen „Förderantrag"-Agenten
mit passendem System-Prompt an, um daraus später Anträge zu erzeugen.

---

## 17. Gespräche verwalten

- **Neu:** **＋ Neues Gespräch** oder **Ctrl + K**.
- **Laden:** Eintrag in der Sidebar anklicken.
- **Umbenennen:** Doppelklick auf den Titel oder **✏️**.
- **Löschen:** **🗑** beim Überfahren des Eintrags.
- **Suchen:** Suchfeld oben (Volltext, ab 2 Zeichen).
- **Exportieren/Importieren:** einzelnes Gespräch als JSON, oder **alle als ZIP**.
- **Komprimieren** (🗜): langes Gespräch von der KI zusammenfassen lassen — manuell
  pro Gespräch, oder **automatisch** (siehe Profil → Automatische Komprimierung).

---

## 18. Nutzerprofil & Projekte

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
  Vorgaben ab — Modus-Brille, Persona, Anti-Halluzinations-Grundregel sowie Formel-,
  Graph- und Zitatregeln. Das Modell antwortet dann ohne jede Voreinstellung („pur"). Ein
  ausdrücklich gewählter Agent und aktive Wissensdatenbanken bleiben davon unberührt.
- **Logo & Vorlagenbilder hochladen** (werden automatisch auf die Sollgröße skaliert):
  - **Logo** – 512×512 px, PNG mit Transparenz (Sidebar + Dokumente)
  - **Vorlagen-Deckblatt** – 1920×1080 px, JPG (Präsentations-Titelfolie)
  - **Vorlagen-Kopfzeile** – 1920×240 px, PNG/JPG (Banner über Folien/Dokumenten)
  Ohne Upload bleibt die Oberfläche schlicht (Schriftzug „AI_Framework_Thomas") und Folien
  werden ohne Branding-Bild erzeugt.

### 🧠 Modelle (im Profil)

Unter **👤 Profil → 🧠 Modelle** weist du je Einsatzzweck ein Modell zu
(leer = Standardmodell `ministral-3:3b`):

| Rolle | Verwendet für |
|-------|---------------|
| **Allgemein** | normaler Chat (Standard der Sidebar-Auswahl) |
| **Programmieren / Mathe** | KI-Assistent im Code-Tab (IDE), der Mathe-Tab und der Programmier-Agent (gemeinsames Modell) |
| **Wissenschaftlich** | Recherche, Patente-Tab und der wissenschaftliche/quellengebundene Modus |
| **Medizin** | Medizin-Tab (🩺) — voreingestelltes Modell für medizinische Anfragen |

Die Auswahllisten enthalten **alle** in Ollama installierten Modelle; neue Modelle
erscheinen nach `ollama pull <name>` automatisch — sowie zusätzlich die Modelle
konfigurierter externer Anbieter (mit ☁ markiert, siehe nächster Abschnitt).

### ☁ KI-Anbieter (externe API, im Profil)

Optional kannst du zusätzlich zu lokalem Ollama einen **OpenAI-kompatiblen
API-Anbieter** einbinden (z. B. **OpenRouter**, OpenAI, Groq, Together):

1. **👤 Profil → ☁ KI-Anbieter (API)**: Name, **Base-URL** (z. B.
   `https://openrouter.ai/api/v1`) und **API-Schlüssel** eingeben → **＋ Hinzufügen**.
   Die App lädt die verfügbaren Modelle des Anbieters.
2. Die Anbieter-Modelle erscheinen danach **oben in den Modell-Rollen** (mit ☁
   gekennzeichnet) und können jeder Rolle zugewiesen werden — z. B. ein starkes
   Cloud-Modell für die Jury-Bewertung oder Recherche.

> **Wichtig:**
> - Der **API-Schlüssel bleibt lokal** auf diesem Rechner und wird **nicht** ins
>   Backup und **nicht** ins Git/in Pakete übernommen.
> - **Remote-Aufrufe verlassen deinen Rechner** (Daten gehen an den Anbieter) und
>   belegen **kein** lokales VRAM — die Ein-Modell-Beschränkung gilt nur für lokale
>   Ollama-Modelle.

### 🌐 Web-Recherche immer lokal (im Profil)

Manche API-Anbieter **unterbinden web- bzw. werkzeuggestützte Recherche** oder
liefern dabei Fehler. Für diesen Fall gibt es zwei Mechanismen — einen, den du
einschaltest, und einen, der immer greift.

**1. Der Schalter „Web-Recherche immer lokal ausführen"**

Ist er aktiv, laufen **alle** web-gestützten Aufgaben zwingend auf einem lokalen
Ollama-Modell, auch wenn den Rollen ein API-Modell zugewiesen ist:

- Recherche-Tab (🔎)
- Matrix-Recherche
- erweiterte Suche im Chat (`/such`)
- Deepdive **mit** Websuche
- Patent-Analyse

Sinnvoll, wenn deine API keine Websuche zulässt — oder aus Datenschutzgründen,
weil Suchanfragen und Fundstellen dann den Rechner nicht verlassen.

> Ohne installiertes lokales Modell ist die Recherche bei aktivem Schalter
> **nicht verfügbar** (Fehlermeldung statt stiller Umleitung).

**2. Der automatische Rückfall (immer aktiv, unabhängig vom Schalter)**

Scheitert eine Recherche am API-Modell — Anbieter nicht erreichbar, Web-/Tool-Nutzung
gesperrt, keine verwertbare Antwort — wird sie **einmalig automatisch lokal
wiederholt**. Du siehst dann einen Hinweis wie:

> *API-Modell lieferte keine Suchbegriffe – lokal wiederholt (gemma4:26b).*

Das ist ein **reiner Rückfall**: Bevorzugt bleibt immer das gewählte Modell, lokal
wird nur nachgesetzt, wenn es sonst gar kein Ergebnis gäbe.

### 🔢 Token-Zähler & Preis (im Profil)

Unten links in der Seitenleiste zeigt ein **Token-Zähler** den Verbrauch der laufenden
Sitzung (↓ Eingabe / ↑ Ausgabe) und – sofern ein Preis hinterlegt ist – die **geschätzten
Kosten**. Darunter steht der **letzte Vorgang**.

**Aufschlüsselung pro Vorgang:** Ein **Klick** auf den Zähler öffnet eine Übersicht, die
den Aufwand **nach Vorgangsart** gruppiert (mit Anzahl und – falls Preis hinterlegt –
Kosten) und die **letzten Vorgänge** chronologisch auflistet. So siehst du z. B., wie
viele Tokens eine **Matrix-Recherche**, eine **Partner-Auswertung**, eine **Plan-Erstellung**
oder eine **Suche** (`/such`) gekostet hat. Erfasst werden u. a.: **Chat**, **Deepdive**,
**Suche**, **Rückfragen** (`/frag`), **Matrix-Recherche**, **Partner-Auswertung**, **Plan**,
**Jury**, **Anfrage-Auswertung** und **Code-Assistent**.

Den Preis setzt du im **Profil**: *Preis je 1.000 Eingabe-Tokens*, *je 1.000
Ausgabe-Tokens* und die *Währung*. Lokale Ollama-Modelle sind kostenlos (0 lassen); für
externe API-Anbieter den jeweiligen Tarif eintragen. Der Zähler überlebt einen Reload;
in der Aufschlüsselung gibt es einen Knopf **Zähler zurücksetzen**.

Die **Versionsnummer** des Frameworks steht im **👤 Profil** (neben „Nutzerprofil") und
klein in der Seitenleiste; sie lässt sich in der `config.json` (`"version"`) setzen.

### 👁 Tab-Sichtbarkeit (im Profil)

Unter **Sichtbare Tabs** lässt sich die Tab-Leiste bereinigen — optionale Tabs
können ein-/ausgeblendet werden:

| Häkchen | steuert |
|---------|---------|
| 📚 RAG | RAG-Tab |
| 💻 Code | Code-Tab |
| 🔢 Mathe | Mathe-Tab |
| 🩺 Medizin | Medizin-Tab |
| 📁 Verzeichnis | Verzeichnis-Analyse-Tab |
| 🧩 Morph-Kasten | Morphologischer-Kasten-Tab |
| 📧 Mail | Mail-Tab |
| 📋 Logs | Logs-Tab |

> **Beim Erstaufruf** (frische Installation, noch kein Profil) sind **alle** diese Tabs
> **ausgeblendet** — die Oberfläche startet aufgeräumt mit den Kern-Tabs. Hake hier an,
> was du nutzen möchtest, und speichere.

Ausgeblendete Tabs sind **nicht gelöscht** — sie werden sofort wieder sichtbar,
sobald der Haken gesetzt und das Profil gespeichert wird.

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
oben in der Sidebar die Gesprächsliste nach Projekt einschränken. Ein Projekt kann
**eigene Skill-Agenten** besitzen (z. B. die von **`/plan`** erzeugten Berater); sie
werden im Projekt-Dialog als **🧩 Skills** aufgelistet. Beim **Löschen** eines Projekts
werden diese projekt-eigenen Agenten **mitgelöscht** (Nachfrage nennt die Anzahl).

---

## 19. Exportieren

| Quelle | Button | Ergebnis |
|--------|--------|----------|
| Chat | **📝 DOCX** (Toolbar) | Gespräch als Word-Dokument |
| Canvas | **📊 PPTX** | Präsentation im Corporate-Design |
| Canvas | **📋 XLSX** | Tabelle als Excel |
| Recherche | **📄 Als Dokument** | Bericht als DOCX mit Kopfzeile |
| Mathe | **𝐓 LaTeX** / **📑 PDF** | Mathematischer Bericht mit Formelsatz |

- Alle Exporte tragen in der **Fußzeile**: *Name · Firma · KI generierter Inhalt*.
- KI-erzeugte Antworten werden im Dokument zusätzlich mit **„▶ Von KI generiert"**
  gekennzeichnet.
- **Dokument-Kopfzeile als Bild** nur bei Recherche-Berichten; reine
  Chat-Exporte bleiben ohne Bild-Kopfzeile.

---

## 20. Backup & Wiederherstellung

Zu finden im **Profil-Modal** unter **💾 Alle Daten sichern & wiederherstellen**
(die Knöpfe **💾 Backup** / **📥 Restore** in der Seitenleiste nutzen dieselbe
Funktion mit Standardumfang).

### Was immer gesichert wird

Alles in **einer ZIP-Datei**:

| Bereich | Inhalt |
|---|---|
| Profil & Branding | Profil inkl. Modell-Rollen und Tab-Sichtbarkeit, Logo, Deckblatt, Kopfzeile |
| Arbeit | Gespräche, Projekte, Pläne, Agenten (inkl. Favoriten), Jurys, Jury-Dokumente, Code-Programme |
| Wissen | RAG-Wissensdatenbanken **inkl. Dokumente und Embeddings** |
| Geschäft | Angebote, Rechnungen, Zeugnisse, Patente, Anfragen (RFQ), Morph-Kasten, Firmenprofil |
| Sonstiges | Ressourcen-/Kapazitätslisten, Mail-Konfiguration, Feedback |

### Was du zuschalten kannst

Drei Dinge sind **standardmäßig aus**, weil sie groß oder vertraulich sind:

- **Hochgeladene Dateien, Berichte, Dossiers** — macht die Sicherung vollständig,
  kann aber einige hundert MB groß werden.
- **Postfach-Archive** — die eingelesenen Mails samt Anhängen. ⚠ Kann mehrere GB
  groß werden und enthält vollständige private Korrespondenz.
- **API-Zugangsdaten** — ⚠ deine API-Schlüssel liegen dann **im Klartext** in der
  ZIP-Datei. Nur für den Umzug auf einen anderen Rechner sinnvoll; die Datei
  danach nicht weitergeben. Vor dem Export kommt eine Rückfrage.

Im Archiv liegt eine `backup_info.json`, die festhält, was enthalten ist — so
siehst du später, ob eine Sicherung vollständig war.

### Wiederherstellen

Zwei Betriebsarten:

- **Zusammenführen** (Standard, empfohlen) — Vorhandenes bleibt unangetastet, nur
  Fehlendes wird ergänzt. Bereits vorhandene Pläne, Agenten und Wissensdatenbanken
  werden übersprungen, es entstehen keine Duplikate. Damit kannst du ein Backup
  gefahrlos in eine bereits genutzte Installation einspielen.
- **Vorhandenes ersetzen** — ⚠ gleichnamige Dateien werden mit dem Stand aus dem
  Archiv überschrieben. Das lässt sich nicht rückgängig machen; es kommt eine
  Rückfrage.

Gespräche erhalten beim Import neue IDs. Nach dem Import steht direkt im Profil,
was je Bereich übernommen wurde.

> **Umzug auf einen neuen Rechner:** Alle drei Häkchen setzen, exportieren, auf
> dem Zielrechner „Vorhandenes ersetzen" wählen. Danach die ZIP-Datei löschen —
> sie enthält dann deine API-Schlüssel.

---

## 21. Modelle & VRAM

Das aktive Modell wird unten in der Sidebar gewählt. Standardmäßig installiert ist
**nur**:

| Modell | Rolle |
|--------|-------|
| `ministral-3:3b` | Standardmodell für alles (auch Vision) |
| `qwen3.5:4b` | Stärkeres kompaktes Chat-Modell |
| `nomic-embed-text` | RAG-Embeddings (klein; läuft auf kleinen Karten bewusst auf der CPU) |

Empfohlen für den Medizin-Tab: `medgemma:4b` (MedGemma-4B, ~2,5 GB). In der
**Portable-Variante mitgebündelt**, sonst separat laden: `ollama pull medgemma:4b`.

### Vier Modell-Rollen im Profil

Unter **👤 Profil → 🧠 Modelle** weist du je Einsatzzweck ein Modell zu:

| Rolle | Verwendet für |
|-------|---------------|
| **Allgemein** | normaler Chat (Standard der Sidebar-Auswahl) |
| **Programmieren / Mathe** | KI-Assistent im Code-Tab (IDE), der Mathe-Tab und der Programmier-Agent (gemeinsames Modell) |
| **Wissenschaftlich** | Recherche, Patente-Tab und der wissenschaftliche/quellengebundene Modus |
| **Medizin** | Medizin-Tab — voreingestelltes Modell für medizinische Anfragen |

Leer = `ministral-3:3b`. **Andere Modelle vorher laden** (sie werden bei Bedarf
nachgeladen): `ollama pull <modell>` — danach erscheinen sie in den Auswahllisten.

**Nur ein Modell gleichzeitig im Speicher:** AI_Framework_Thomas entlädt beim Modellwechsel
automatisch das zuvor genutzte Modell, bevor das neue lädt. Dadurch genügen
**6 GB VRAM**, auch wenn die Rollen unterschiedliche Modelle verwenden.

In den Auswahllisten erscheinen **alle** in Ollama installierten Modelle;
`allowed_models` in `config.json` legt nur noch die Reihenfolge fest.

---

## 22. Tastenkürzel

| Kürzel | Aktion |
|--------|--------|
| **Enter** | Nachricht senden |
| **Shift + Enter** | Neue Zeile |
| **Ctrl + K** | Neues Gespräch |
| **Strg + Enter** (IDE) | Code ausführen |
| **Strg + S** (IDE) | Programm speichern |

---

## 23. Technische Hinweise

- **Lokal:** Gespräche in `data/ai_framework_thomas.db` (SQLite + Volltextindex), Agenten/Pläne/
  Programme als JSON in `data/`, Uploads temporär in `data/uploads/`.
- **Agentic Loop:** Bis zu 8 Iterationen — die KI kann mehrfach suchen, rechnen
  und Ergebnisse kombinieren, bevor sie antwortet.
- **Tool-Kompatibilität:** Native `tool_calls` und Inline-Formate
  (`<call_tool>`, `<tool_call>`) werden erkannt — breite Modellunterstützung.
- **Berechnungs-Sandbox:** eingeschränktes `exec()` ohne Datei-/Netzwerkzugriff.
- **VRAM-Schutz:** zentrale Serialisierung aller Modell-Aufrufe (siehe Abschnitt 21).

Für tiefergehende Architektur siehe **docs/ENTWICKLUNG.md**.

---

## 24. Aktualisieren (Update)

Mit **`update.bat`** wird **nur der Programmcode** (Systemdateien) ausgetauscht — alle
**Nutzerdaten und Einstellungen bleiben erhalten**.

- **Unberührt:** `data\` (Gespräche, Agenten, Pläne, Wissensdatenbanken, Profil,
  Branding, Mail-Zugang & -Regeln), `config.json`, sowie `venv\` / `python\` / `ollama\`
  inkl. Modelle.
- **Ersetzt:** Programmdateien (`main.py`, `static\`, `tools\`, Skripte, Doku …).

**So geht's:**
1. Neue Version bereitlegen (der Ordner mit der neuen `update.bat` = Quelle).
2. `update.bat` starten und den Pfad der bestehenden Installation angeben:
   ```
   update.bat "D:\AI_Framework_Thomas_Portable_YYYYMMDD"
   ```
   Ohne Angabe wird der Pfad abgefragt. Ein Portable-`app\`-Unterordner wird automatisch erkannt.
3. Vor dem Überschreiben legt das Skript eine Sicherung unter `app\_update_backup\` an.
4. Auf Wunsch werden anschließend neue Python-Pakete aus `requirements.txt` installiert.

> Danach **App neu starten** (`start.bat`) und im Browser mit **Strg+F5** neu laden.
> Eine vollständige Datensicherung vorab (siehe Abschnitt **20. Backup**) ist trotzdem empfehlenswert.

### Zentrale Verwaltung (ACMP)

In verwalteten Umgebungen wird AI_Framework_Thomas über die **Aagon Client Management
Platform (ACMP)** installiert und aktualisiert — dann **musst du selbst nichts tun**:
Die IT rollt neue Programmversionen automatisch aus. Dabei gilt dieselbe Trennung wie
oben: Nur der **Programmcode** wird getauscht, deine **Daten, Einstellungen, Ollama und
die Modelle bleiben unangetastet**. Technisch übernimmt das dasselbe `update.bat`, nur
lautlos (Schalter `/S`). Details für Administratoren stehen in **`docs/ACMP.md`**.

---

## 25. Deinstallation

**Automatisch:** `uninstall.bat` doppelklicken und den Anweisungen folgen.
Entfernt die virtuelle Umgebung (`venv/`), optional die Daten (`data/`) und
optional Ollama. Der Programmordner bleibt und kann manuell gelöscht werden.

**Manuell:** laufende `python.exe` beenden → `venv\` löschen → optional `data\`
löschen (enthält alle Gespräche, Pläne und Programme!) → Ordner entfernen →
optional Ollama deinstallieren.

---

*AI_Framework_Thomas — Lokal & privat · Powered by Ollama*
