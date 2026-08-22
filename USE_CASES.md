# Anwendungsfälle — was kann ich mit AI_Framework_Thomas konkret tun?

Diese Liste beschreibt **echte, sofort ausführbare Aufgaben** — nach Ziel gruppiert, jeweils mit dem
passenden Tab bzw. Chat-Befehl. Alles läuft **lokal und privat** (Ollama); externe API-Anbieter sind
optional. Für Schritt-für-Schritt-Details siehe die [Bedienungsanleitung](BEDIENUNGSANLEITUNG.md);
eine Gesamtübersicht der Chat-Befehle liefert `/hilfe` direkt im Chat.

> **Lesehilfe:** _Ich möchte …_ → **Tab/Befehl**. „🔒 lokal" = verlässt den Rechner nicht (Geheim-Modus
> erzwingt das global). „🌐 Web" = nutzt eine Websuche (abschaltbar).

---

## 1. Chatten, recherchieren, Wissen nutzen

- **Privat mit einer KI chatten**, ohne dass Daten den Rechner verlassen — **Chat** (🔒 lokal). Der
  **Geheim-Modus** (Sidebar-Schalter) erzwingt lokale Modelle für *alle* Funktionen.
- **Eine KI selbst entscheiden lassen, welches Werkzeug sie zieht** (rechnen, im Web suchen, Bild
  erzeugen, eigene Dokumente durchsuchen …) — **Assistent-Modus** (Profil-Schalter): nur der Chat
  bleibt sichtbar, das Modell wählt das passende Werkzeug selbst.
- **Eigene Dokumente/PDFs/Notizen befragen** („Was steht in meinen Unterlagen zu X?") — Dateien in
  **RAG** laden, dann im Chat fragen; im Assistent-Modus durchsucht die KI sie autonom.
- **Ein Thema gründlich recherchieren** (mehrere Teilaspekte, Quellen, steuerbare Tiefe & Länge) —
  Chat-Befehl **`/recherche <Thema>`** (🌐 Web).
- **Bessere Suchbegriffe finden und das Web durchsuchen** (Synonyme, Fachsprache, Englisch) —
  **`/such <Begriff>`** (🌐 Web).
- **Fehlende Angaben vor der Antwort per Formular ergänzen** (auch Multiple-Choice) —
  **`/frag <Aufgabe>`**.
- **Eine Antwort vertiefen** (N Nachfragen automatisch recherchieren) — **`/dd10`** bzw. als
  Dokument **`/ddd10`**.
- **Antworten vorlesen lassen** (verschiedene Sprecher-Personas) — 🔊-Knopf an jeder Antwort (TTS).

## 2. Dokumente & Präsentationen erstellen

- **Aus einem Thema eine fertige Präsentation bauen** (Interview → Gliederung → Recherche je Punkt →
  Deckblatt/Bilder → Folien) — **`/praesentation <Thema>`** → Canvas → PPTX-Export.
- **Aus vorhandenem Text Folien machen** — **Dokumente**-Tab bzw. „→ Präsentation" aus dem Workflow.
- **KI-Bilder in Folien einfügen** (Deckblatt flächig, Inhaltsfolien zweispaltig) — in der Canvas-
  Foliennavigation „Bild erzeugen".
- **Rechtssichere Rechnung oder Angebot erstellen** (Netto/USt/Brutto, §14-UStG-Pflichtangaben —
  Beträge werden **deterministisch mit Decimal** gerechnet, nie vom LLM) — **Rechnungen**-Tab, Export
  PDF/DOCX.
- **Ein Arbeitszeugnis in korrekter Zeugnissprache formulieren** — **Zeugnisse**-Tab, Export PDF/DOCX.
- **PDF/DOCX/PPTX/LaTeX mit Formeln, Tabellen, Diagrammen erzeugen** — **Dokumente**-Tab.

## 3. Analysieren & entscheiden

- **Zwei Excel-/CSV-Tabellen vergleichen** (neue/entfernte Zeilen, geänderte Zellen + KI-Bewertung) —
  **Excel-Vergleich**-Tab oder Chat **`/excelvergleich`**.
- **Eine gewichtete Entscheidung zwischen Varianten treffen** (Paarvergleich/AHP, Gewichte +
  Konsistenzcheck, Sensitivität) — **Varianten**-Tab oder Chat **`/paarvergleich <Thema>`**.
- **Ein Problem in eine komplette Entscheidungstabelle verwandeln** (Kriterien → Gewichte → Varianten
  → Bewertungen in einem Schritt, optional mit Interview & Webrecherche) — Varianten-Tab „Problem →
  Tabelle".
- **Angebote/Ausschreibungen (RFQ) stapelweise auswerten** (eigene Bewertungsspalten, XLS-Import) —
  **Anfrage**-Tab.
- **Einen Text von mehreren KI-Rollen bewerten lassen** (Gremium/Jury, auch große Dokumente per
  Map-Reduce) — **Jury**-Tab.
- **Lösungsraum systematisch aufspannen** (morphologischer Kasten) — **Morph-Kasten**-Tab.
- **Mehrere Aspekte in einer Recherche-Matrix vergleichen** und als **Wissensgraph** vernetzen —
  **Matrix**-Tab (🌐 Web).

## 4. E-Mails, Verzeichnisse & vertrauliche Daten (alles lokal)

- **Ein Postfach auswerten** (`.pst`/`.mbox`/`.eml`/`.msg`) — Absender/Betreff/Inhalt, Themen-Tags,
  **Wissensgraph**, Volltextsuche, Zeitleiste — **Postfach**-Tab (🔒 lokal). `.pst` ist dank
  eingebautem MIT-Reader **immer** lesbar.
- **Das Postfach in Alltagssprache befragen** und Steuerbefehle nutzen („zeig alles zu Thema X") —
  Postfach-Chat / Graph-Befehl (🔒 lokal).
- **Einen Ordner voller Dateien analysieren** (Inhalte zusammenfassen, PII schwärzen) —
  **Verzeichnis-Analyse**-Tab (🔒 lokal).
- **E-Mails/Ordnerinhalte in eine durchsuchbare Wissensdatenbank überführen** und dann per Chat
  befragen — „In RAG übernehmen" (Postfach) bzw. RAG-Upload.

## 5. Projekte & Planung

- **Projekte und Aufgaben als Baum mit Wissensgraph verwalten** (Zuständige, Status, Fristen,
  Verknüpfungen; 2D- und 3D-Kugel-Ansicht) — **To-Do**-Tab.
- **Den eigenen Aufgabenbestand befragen** („Was ist als Nächstes dran?", „Woran arbeitet Kollege
  Y?") — To-Do-Datenchat / „🎯 Empfehlung" (🔒 lokal-bevorzugt).
- **Einen Projektplan aus einem Dokument erzeugen** (Ressourcen + bis zu 300 Vorgänge) — **Planer**,
  „Dokument → Plan".
- **Kapazität, Zukauf und Terminketten planen** (CPM, Auto-Strukturierung, Entzerrung) — **Planer**.
- **Aus einem Chat-Verlauf Strategie, Berater-Agenten, Plan und Bewertungs-Jury ableiten** (Vorschau,
  dann als Projekt anlegen) — Chat **`/plan`** (bzw. `/planN` für die Aufgabenzahl).

## 6. Technik, Code & Schutzrechte

- **Mathe-/Rechenaufgaben lösen und plotten lassen** (mit Auto-Verifikation) — **Mathe**-Tab; im Chat
  löst die KI Rechnungen bevorzugt per Code.
- **Code schreiben, ausführen und iterieren** (ein Chat-Arbeitsbereich, mehrere Dateien, Sandbox-Run,
  autonomer Coding-Agent, Canvas-Vorschau) — **Code**-Tab.
- **Patente recherchieren und prüfen** (EPO-OPS amtlich oder Google-Fallback; Merkmalsanalyse,
  Neuheit/erfinderische Tätigkeit, FTO-Produkt-Check, Stärke-Score) — **Patente**-Tab; im
  Assistent-Modus auch autonom als Chat-Werkzeug.
- **Medizinische Fragen mit einer 2-Modell-Pipeline bearbeiten** — **Medizin**-Tab (🔒 lokal).

## 7. Medien erzeugen

- **Ein Bild aus einer Beschreibung erzeugen** (lokal über Stable-Diffusion/Z-Image oder API) —
  **`/bild <Beschreibung>`**, geführt **`/bildhelp`**.
- **Ein Bild bearbeiten** (img2img, Bereich markieren = Inpainting) — **`/bildedit`**.
- **Ein Bild hochskalieren** (KI-Detail oder schnell) — **`/upscale`**.
- **Aus einem Bild einen Text-zu-Bild-Prompt ableiten** — **`/bildprompt`**.
- **Audio/Sprache in Text umwandeln** (Mikrofon oder Datei, mit Zeitmarken; lokal per faster-whisper)
  — **Transkription**-Tab; Chat-Diktat per 🎙-Knopf (🔒 lokal im Geheim-Modus).
- **Kurze Musik/Jingles algorithmisch erzeugen** (ohne GPU, sofort) — **`/musik <Stimmung>`**.

## 8. Arbeitsabläufe & Automatisierung

- **Mehrere Schritte nacheinander ausführen lassen** (Zwischenergebnisse fließen weiter, am Ende eine
  Synthese) — **`/workflow`** mit nummerierten Schritten. Pro Schritt Tags: `[lokal]`/`[api]`/`[web]`
  sowie `[bild]`/`[sprache]` für Medien-Schritte. Ergebnis → Präsentation oder Planer übergeben.
- **Ergebnisse aus dem Chat an andere Tabs übergeben** (To-Do, Planer, Code, Mathe …) — Menü
  **„↪ senden an…"** unter jeder Antwort.

## 9. Betrieb, Datenschutz & Modelle

- **Alles streng lokal halten** (kein Cloud-Aufruf, kein VRAM-Konflikt) — **Geheim-Modus**; die
  Persona „Hartman" erzwingt zusätzlich einen kompletten Lokal-Riegel inkl. Websuche-Sperre.
- **Ein großes Kontextfenster lokal nutzen** — einen **llama.cpp**- oder **LM-Studio**-Server als
  Anbieter mit Häkchen „🖥 Lokaler Server" eintragen; er zählt dann wie Ollama (auch im Geheim-Modus
  und bei vertraulichen Auswertungen), Kontext über den `-c`-Startparameter.
- **Externe Modelle anbinden** (OpenRouter, OpenAI, Groq …) — im Profil unter „KI-Anbieter (API)";
  der Schlüssel bleibt lokal (nicht im Backup/Git).
- **Alle Nutzerdaten sichern und wiederherstellen** (wählbarer Umfang, Merge/Ersetzen) — **Profil →
  „Alle Daten sichern & wiederherstellen"**.
- **Token-Verbrauch und Kosten im Blick behalten** — Token-Zähler in der Sidebar (mit Aufschlüsselung
  je Vorgangsart).

---

_Diese Liste ist bewusst pragmatisch gehalten. Detaillierte Anleitungen und alle Optionen stehen in
der [Bedienungsanleitung](BEDIENUNGSANLEITUNG.md); Entwickler-Details in
[docs/ENTWICKLUNG.md](docs/ENTWICKLUNG.md)._
