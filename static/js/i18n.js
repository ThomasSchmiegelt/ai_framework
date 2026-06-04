/* AI_Framework_Thomas — Sprachumschaltung (Deutsch ↔ Englisch)
 *
 * Ansatz: Das HTML wird auf Deutsch ausgeliefert (= einzige Quelle der
 * deutschen Texte). Dieses Modul übersetzt beim Umschalten auf Englisch die
 * sichtbaren Oberflächentexte anhand eines DE→EN-Wörterbuchs. Beim Zurück-
 * schalten auf Deutsch werden die zwischengespeicherten Originaltexte
 * wiederhergestellt.
 *
 * Übersetzt werden: direkte Textknoten von Elementen sowie die Attribute
 * `placeholder` und `title`. Vergleichsschlüssel = getrimmter Text mit auf ein
 * Leerzeichen reduzierten Whitespace-Folgen. Dynamische Container (Chatverlauf,
 * Listen, Tabellen-Bodies …) werden ausgespart, damit Nutzerinhalte nicht
 * angetastet werden.
 *
 * Umfang: statisches UI-Grundgerüst (Tabs, Buttons, Überschriften, Labels,
 * Platzhalter, Modale). Tief in JS erzeugte Texte sind bewusst noch deutsch.
 */
const I18n = (() => {
  const STORAGE_KEY = 'aift_lang';
  let _lang = 'de';

  // IDs von Containern mit dynamischem Inhalt – deren Teilbäume nicht anfassen.
  const SKIP_IDS = new Set([
    'conversations', 'messages', 'agents-grid', 'category-filter',
    'research-progress-list', 'research-report', 'rag-collections-list',
    'docgen-output', 'task-tbody', 'matrix-body', 'matrix-header-row',
    'planner-ai-messages', 'ide-chat-history', 'ide-console',
    'ide-file-list-inline', 'log-entries', 'plan-doc-body',
    'suggest-modal-body', 'detail-modal-body', 'insert-modal-body',
    'schedule-modal-body', 'resource-tbody', 'pres-table-container',
    'template-chips', 'aspect-tags', 'sidebar-wordmark', 'json-gutter',
  ]);
  const SKIP_TAGS = new Set(['SCRIPT', 'STYLE', 'CANVAS', 'IFRAME', 'NOSCRIPT']);

  // DE → EN. Schlüssel = getrimmter, whitespace-normalisierter deutscher Text.
  const EN = {
    // ── Sidebar ──
    '＋ Neues Gespräch': '＋ New chat',
    '🔍 Gespräche durchsuchen…': '🔍 Search chats…',
    '— Alle Projekte —': '— All projects —',
    'Modell': 'Model',
    'Lade…': 'Loading…',
    'Agent': 'Agent',
    '— Kein Agent —': '— No agent —',
    'Aktuellen Chat Projekt zuordnen': 'Assign current chat to project',
    '— Kein Projekt —': '— No project —',
    '📁 Projekte': '📁 Projects',
    '🤖 Agenten': '🤖 Agents',
    'Gespräch importieren (JSON)': 'Import conversation (JSON)',
    '⬆ Gespräch': '⬆ Conversation',
    'Alle Gespräche exportieren (ZIP)': 'Export all conversations (ZIP)',
    '⬇ Gespräche': '⬇ Conversations',
    'Alle Nutzerdaten exportieren (ZIP-Backup)': 'Export all user data (ZIP backup)',
    'Nutzerdaten aus ZIP-Backup wiederherstellen': 'Restore user data from ZIP backup',
    '👤 Profil bearbeiten': '👤 Edit profile',

    // ── Tabs ──
    '💬 Chat': '💬 Chat',
    '🔬 Recherche': '🔬 Research',
    '📄 Dokumente': '📄 Documents',
    '🗂️ Planer': '🗂️ Planner',
    '📊 Matrix': '📊 Matrix',

    // ── Chat / Welcome ──
    'Du': 'You',
    'Herr Professor': 'Professor',
    'Frau Doktor': 'Doctor',
    'Roboter': 'Robot',
    'Dein lokaler KI-Assistent auf Basis von Ollama.': 'Your local AI assistant powered by Ollama.',
    'Stelle eine Frage, lade eine Datei hoch oder aktiviere die Websuche.': 'Ask a question, upload a file or enable web search.',
    '🔍 Was ist der aktuelle Stand der E-Maschinen-Forschung?': '🔍 What is the current state of electric machine research?',
    '📊 Erstelle eine Präsentation über FEM-Analyse': '📊 Create a presentation about FEM analysis',
    '🧮 Berechne die Leistung: P = U × I mit U=400V, I=25A': '🧮 Calculate the power: P = U × I with U=400V, I=25A',
    '📋 Erstelle eine Tabelle mit Motorkennwerten': '📋 Create a table of motor characteristics',
    '📄 Analysiere ein hochgeladenes Dokument': '📄 Analyze an uploaded document',
    '⚙️ Erkläre den Unterschied zwischen FEM und CFD': '⚙️ Explain the difference between FEM and CFD',
    'Nachricht senden… (Enter = senden, Shift+Enter = neue Zeile)': 'Send a message… (Enter = send, Shift+Enter = new line)',
    '🔍 Websuche': '🔍 Web search',
    'Websuche aktivieren/deaktivieren': 'Enable/disable web search',
    '📊 Präsentation': '📊 Presentation',
    'Präsentations-Agent aktivieren': 'Activate presentation agent',
    '💻 Programmieren': '💻 Coding',
    'Programmier-Agent aktivieren': 'Activate coding agent',
    '📎 Datei': '📎 File',
    'Datei anhängen (PDF, Bild, DOCX, XLSX…)': 'Attach file (PDF, image, DOCX, XLSX…)',
    'Wissenssammlung (RAG) für die Antwort nutzen': 'Use knowledge base (RAG) for the answer',
    'Sammlung(en) wählen': 'Select collection(s)',
    'Chat als Word exportieren': 'Export chat as Word',
    'Senden (Enter)': 'Send (Enter)',
    'LOCAL AI · Lokal & privat · Ctrl+K = Neues Gespräch': 'LOCAL AI · Local & private · Ctrl+K = New chat',

    // ── Canvas ──
    'Kein Inhalt': 'No content',
    '🪄 Assistent': '🪄 Assistant',
    '🖼️ Bild-Präsentation': '🖼️ Image presentation',
    '🪄 Präsentations-Assistent': '🪄 Presentation assistant',
    'Präsentationstitel…': 'Presentation title…',
    '🖼️ Bebilderte Präsentation': '🖼️ Illustrated presentation',
    'Beschreibung der Präsentation': 'Presentation description',
    'Worum geht es? (z.B. Aufbau elektrischer Maschinen – Rotor, Stator, Wicklungen). Daraus wird der Analyse-Experte abgeleitet.':
      'What is it about? (e.g. structure of electrical machines – rotor, stator, windings). The analysis expert is derived from this.',
    '📁 Bilderordner wählen': '📁 Choose image folder',
    'Kein Ordner gewählt': 'No folder selected',
    '🧠 Analyse-Experte ableiten': '🧠 Derive analysis expert',
    'System-Prompt des Analyse-Experten (wird abgeleitet, hier editierbar)…':
      'System prompt of the analysis expert (derived, editable here)…',
    '▶ Präsentation erstellen': '▶ Create presentation',
    '＋ Folie': '＋ Slide',
    '▶ Erstellen': '▶ Create',

    // ── Agenten ──
    '＋ Neuer Agent': '＋ New agent',
    'Spezialisierte Assistenten mit eigenem System-Prompt, Modell und Tool-Auswahl. In der Sidebar auswählen, um einen Agenten im Chat zu aktivieren.':
      'Specialized assistants with their own system prompt, model and tool selection. Select one in the sidebar to activate an agent in the chat.',
    '🔍 Agenten durchsuchen…': '🔍 Search agents…',

    // ── Recherche ──
    '🔬 Agentische Recherche': '🔬 Agentic research',
    'Gib ein Thema und Aspekte ein. Für jeden Aspekt wird eine parallele Websuche gestartet – die KI fasst alle Ergebnisse in einem strukturierten Bericht zusammen.':
      'Enter a topic and aspects. A parallel web search runs for each aspect – the AI summarizes all results in a structured report.',
    'Thema': 'Topic',
    'z.B. Elektromotor, Wasserstoffantrieb, FEM-Analyse…': 'e.g. electric motor, hydrogen drive, FEM analysis…',
    'Aspekte': 'Aspects',
    '— Enter oder ＋ zum Hinzufügen': '— Enter or ＋ to add',
    'z.B. Physik, Kosten, Markt, KI …': 'e.g. physics, cost, market, AI …',
    '🔬 Recherche starten': '🔬 Start research',
    '↺ Neue Recherche': '↺ New research',
    '📄 Als Dokument': '📄 As document',
    '📚 In Wissensdatenbank': '📚 To knowledge base',

    // ── RAG ──
    '📚 Wissensdatenbanken (RAG)': '📚 Knowledge bases (RAG)',
    'Lege Wissensdatenbanken an und lade Dokumente hoch. Im Chat aktivierst du den 📚-Umschalter und wählst eine Datenbank – passende Auszüge werden der Antwort als Kontext vorangestellt. Embeddings:':
      'Create knowledge bases and upload documents. In the chat, enable the 📚 toggle and pick a base – matching excerpts are prepended to the answer as context. Embeddings:',
    'Name der Wissensdatenbank': 'Knowledge base name',
    'z.B. Handbuch, Normen, Projektakte…': 'e.g. manual, standards, project file…',
    'Suche:': 'Search:',
    'ausgewogen': 'balanced',
    'schnell': 'fast',
    'gründlich': 'thorough',
    'Antwort:': 'Answer:',
    'kreativ': 'creative',
    'korrekt': 'accurate',
    'Dokumente bereinigen': 'Clean up documents',
    '📚 Wissensdatenbank anlegen': '📚 Create knowledge base',
    '💬 → 📚 Gespräch in Wissensdatenbank übernehmen': '💬 → 📚 Add conversation to knowledge base',
    'Gespräch': 'Conversation',
    'Ziel-Wissensdatenbank': 'Target knowledge base',
    'Original danach löschen': 'Delete original afterwards',
    'übernehmen': 'add',

    // ── Dokumentengenerator ──
    '📄 Dokumentengenerator': '📄 Document generator',
    'Wähle einen': 'Choose a',
    'Dokument-Agenten': 'document agent',
    '(im Tab 🤖 Agenten anlegen, Kategorie „Dokumentation"), optional eine oder mehrere Wissensdatenbanken als Quelle, und beschreibe das gewünschte Dokument (z. B. einen Antrag für ein Förderprogramm).':
      '(create one in the 🤖 Agents tab, category "Documentation"), optionally one or more knowledge bases as source, and describe the desired document (e.g. an application for a funding program).',
    'Dokument-Agent': 'Document agent',
    'Wissensdatenbank(en) als Quelle': 'Knowledge base(s) as source',
    '— optional, Strg/Shift = mehrere': '— optional, Ctrl/Shift = multiple',
    'Auftrag — welches Dokument soll erstellt werden?': 'Task — which document should be created?',
    'z. B. Erstelle einen Förderantrag für das Programm … auf Basis des Projekts. Gliederung: Zusammenfassung, Ausgangslage, Ziele, Arbeitspakete, Zeit- und Kostenplan, Wirkung.':
      'e.g. Create a funding application for the program … based on the project. Structure: summary, background, goals, work packages, schedule and budget, impact.',
    '📄 Dokument erzeugen': '📄 Generate document',
    '📝 Als DOCX': '📝 As DOCX',
    'quellengebunden (wissenschaftlich)': 'source-bound (scientific)',

    // ── Planer ──
    '— Plan laden —': '— Load plan —',
    '＋ Neuer Plan': '＋ New plan',
    'Planname…': 'Plan name…',
    '💾 Speichern': '💾 Save',
    'Projektbeschreibung & Ziel … daraus wird ein Projekt-Agent abgeleitet, der die KI-Vorschläge steuert':
      'Project description & goal … a project agent is derived from this to steer the AI suggestions',
    '🧠 Projekt-Agent ableiten': '🧠 Derive project agent',
    '🪄 KI-Projekt generieren': '🪄 Generate AI project',
    'Aufgaben': 'Tasks',
    'Ressourcen: frei': 'Resources: free',
    'Katalog: erweitern (Zukauf)': 'Catalog: extend (purchase)',
    'Katalog: strikt': 'Catalog: strict',
    '📥 Katalog': '📥 Catalog',
    '📤 Ressourcen': '📤 Resources',
    '🗓 Start': '🗓 Start',
    'Arbeitstage': 'Workdays',
    'Aufgabenliste': 'Task list',
    '＋ Aufgabe': '＋ Task',
    '✨ Mach schön': '✨ Beautify',
    '➕ Dazwischen': '➕ In between',
    '📅 Bestellplan': '📅 Order plan',
    '🔬 Alle recherchieren': '🔬 Research all',
    '⏹ Stop': '⏹ Stop',
    'Dauer (d)': 'Duration (d)',
    'Vorgänger': 'Predecessors',
    'Nachfolger': 'Successors',
    'Ressourcen / Kosten': 'Resources / cost',
    'Puffer': 'Slack',
    'Netzplan': 'Network diagram',
    'Einpassen': 'Fit',
    "KI-Frage: z.B. 'Welche Aufgaben fehlen?', 'Prüfe den Plan auf Konsistenz', 'Detailliere Aufgabe T2'…":
      "AI question: e.g. 'Which tasks are missing?', 'Check the plan for consistency', 'Detail task T2'…",

    // ── Matrix ──
    '📊 Tabellen-Recherche': '📊 Table research',
    '＋ Zeile': '＋ Row',
    '＋ Spalte': '＋ Column',
    '▶ Alle ausführen': '▶ Run all',
    '⬆ CSV Import': '⬆ CSV import',
    '✕ Leeren': '✕ Clear',
    'Spalte 1 = Themen/Informationen · Kopfzeile ab Spalte 2 = Suchprompts · Zellen werden durch KI-Websuche befüllt':
      'Column 1 = topics/information · header from column 2 = search prompts · cells are filled by AI web search',
    '💾 Gespeichert': '💾 Saved',

    // ── Code / IDE ──
    '🧩 JSON-Editor': '🧩 JSON editor',
    '＋ Neu': '＋ New',
    'Programmname…': 'Program name…',
    '▶ Ausführen': '▶ Run',
    'Strg+Enter = Ausführen · Strg+S = Speichern': 'Ctrl+Enter = Run · Ctrl+S = Save',
    'Beispiele:': 'Examples:',
    '📐 Toleranzanalyse': '📐 Tolerance analysis',
    '📈 Federkennlinie': '📈 Spring curve',
    '📄 Leere Vorlage': '📄 Empty template',
    '🤖 KI-Assistent — Was soll das Programm zeigen?': '🤖 AI assistant — what should the program show?',
    'Code erstellen': 'Create code',
    'Code-Editor': 'Code editor',
    'Der KI-generierte Code erscheint hier automatisch und kann manuell angepasst werden…':
      'The AI-generated code appears here automatically and can be edited manually…',
    'Vorschau': 'Preview',
    'Meldungen': 'Messages',
    '🔧 Fehler automatisch beheben': '🔧 Fix errors automatically',
    '📂 Datei öffnen': '📂 Open file',
    'Keine Datei': 'No file',
    '✨ Formatieren': '✨ Format',
    '✓ Prüfen': '✓ Validate',
    '💾 Herunterladen': '💾 Download',
    'JSON-Dateien öffnen, prüfen und reparieren. Fehler werden mit Zeile/Spalte angezeigt. Die korrigierte Datei lädst du wieder herunter.':
      'Open, validate and repair JSON files. Errors are shown with line/column. Download the corrected file again.',

    // ── Logs ──
    '▶ Logging aktivieren': '▶ Enable logging',
    '⏺ Inaktiv': '⏺ Inactive',
    '↺ Aktualisieren': '↺ Refresh',
    'Alle Typen': 'All types',
    'Fehler': 'Errors',
    'Protokolliert Chat-Anfragen, Tool-Aufrufe, Exports und Frontend-Ereignisse für Diagnose & Verbesserung von LOCAL AI.':
      'Logs chat requests, tool calls, exports and frontend events for diagnostics & improvement of LOCAL AI.',

    // ── Agent-Modal ──
    'Neuer Agent': 'New agent',
    'Aus Vorlage starten': 'Start from template',
    '— optional, Felder werden befüllt': '— optional, fields are filled in',
    'z.B. Ingenieur-Assistent': 'e.g. engineering assistant',
    'Beschreibung': 'Description',
    'Kurze Beschreibung des Agenten': 'Short description of the agent',
    'Kategorie': 'Category',
    'Sonstige': 'Other',
    'Fertigung': 'Manufacturing',
    'Qualität': 'Quality',
    'Dokumentation': 'Documentation',
    'Kommunikation': 'Communication',
    'Analyse': 'Analysis',
    'Recherche': 'Research',
    'Technik': 'Engineering',
    'KI-Assistent: Was soll der Agent tun?': 'AI assistant: what should the agent do?',
    '✨ System-Prompt automatisch generieren': '✨ Generate system prompt automatically',
    '(automatisch befüllt oder manuell eingeben)': '(filled automatically or entered manually)',
    '(leer = aktuell ausgewähltes)': '(empty = currently selected)',
    'z.B. qwen3.6:27b oder leer lassen': 'e.g. qwen3.6:27b or leave empty',
    'Verfügbare Tools': 'Available tools',
    '(zum Teilen per Copy-Paste)': '(to share via copy-paste)',
    '📋 JSON kopieren': '📋 Copy JSON',
    '▶ JSON anzeigen': '▶ Show JSON',
    '🗑 Löschen': '🗑 Delete',
    'Abbrechen': 'Cancel',
    'Speichern': 'Save',

    // ── Profil-Modal ──
    '👤 Nutzerprofil': '👤 User profile',
    '🌐 Sprache / Language': '🌐 Language / Sprache',
    'Oberflächensprache': 'Interface language',
    'Deutsch': 'German',
    'Englisch': 'English',
    'Profil gespeichert': 'Profile saved',
    'Vorname': 'First name',
    'Nachname': 'Last name',
    'Firma': 'Company',
    'Abteilung': 'Department',
    'Standard-Projekt': 'Default project',
    'E-Mail': 'Email',
    'Telefon': 'Phone',
    '🎨 Modus & Branding': '🎨 Mode & branding',
    'Modus (Fachausrichtung & Farben)': 'Mode (focus & colors)',
    '🔧 Maschinenbau (Blau)': '🔧 Mechanical engineering (blue)',
    '🤖 KI (Grün)': '🤖 AI (green)',
    '🤝 Soziales (Braun)': '🤝 Social (brown)',
    '📣 Marketing (Rot)': '📣 Marketing (red)',
    '💰 Finanz (Grau)': '💰 Finance (gray)',
    '📈 Geschäftsführung (Gelb)': '📈 Management (yellow)',
    'Modus prägt die KI-Antworten': 'Mode shapes the AI answers',
    'Keine Modi verwenden (LLM pur)': "Don't use modes (pure LLM)",
    'Antwortstil (Persona)': 'Response style (persona)',
    'Neutral (Standard)': 'Neutral (default)',
    '🤖 Roboter (extrem sachlich)': '🤖 Robot (extremely factual)',
    '🎓 Herr Professor (Sie, formell-distanziert)': '🎓 Professor (formal, distant)',
    '🩺 Frau Doktor (Sie, korrekt-distanziert)': '🩺 Doctor (correct, distant)',
    '😎 Felix (Du, kumpelhaft)': '😎 Felix (casual, buddy)',
    '😊 Sandra (Du, sehr korrekt & kumpelhaft)': '😊 Sandra (very correct & friendly)',
    '🧠 Modelle': '🧠 Models',
    'Standardmäßig läuft nur': 'By default only',
    '. Hier kannst du je Einsatzzweck ein anderes installiertes Modell zuweisen – es wird bei Bedarf nachgeladen (jeweils nur eines aktiv).':
      ' runs. Here you can assign a different installed model per use case – it is loaded on demand (only one active at a time).',
    'Allgemein': 'General',
    'Programmieren': 'Coding',
    'Wissenschaftlich': 'Scientific',
    '🗜 Automatische Komprimierung': '🗜 Automatic compression',
    'Aktiv': 'Active',
    'Überlauf ab': 'Overflow from',
    'Zeichen': 'characters',
    'Leerlauf nach': 'Idle after',
    'Minuten': 'minutes',
    'Lange Verläufe werden bei Überschreitung der Zeichenzahl bzw. nach Inaktivität automatisch zusammengefasst (mit Hinweis). Komprimiert wird das gerade geöffnete Gespräch.':
      'Long conversations are summarized automatically (with a notice) once the character count is exceeded or after inactivity. The currently open conversation is compressed.',
    'Logo & Vorlagenbilder (werden in Oberfläche, Folien und Dokumenten verwendet):':
      'Logo & template images (used in the interface, slides and documents):',
    'Vorlagen-Deckblatt': 'Template cover',
    'Vorlagen-Kopfzeile': 'Template header',
    'entfernen': 'remove',

    // ── Projekt-Modal ──
    '📁 Projekte verwalten': '📁 Manage projects',
    'Nr. (optional)': 'No. (optional)',
    'Projektname *': 'Project name *',
    '＋ Projekt anlegen': '＋ Create project',
    'Schließen': 'Close',

    // ── Ressourcen-Modal ──
    '🧰 Ressourcen': '🧰 Resources',
    'Typ, Name, Menge, Zeit (h), Kostensatz (€) und Lieferzeit (Tage Vorlauf für Bestellung). Bei Hardware/Software ohne Zeit gilt der Satz pro Einheit.':
      'Type, name, quantity, time (h), cost rate (€) and lead time (days of advance for ordering). For hardware/software without time, the rate applies per unit.',
    'Typ': 'Type',
    'Menge': 'Qty',
    'Zeit (h)': 'Time (h)',
    'Satz (€)': 'Rate (€)',
    'Lieferz. (d)': 'Lead (d)',
    'Summe (€)': 'Total (€)',
    '＋ Ressource': '＋ Resource',
    '💾 Übernehmen': '💾 Apply',

    // ── RAG-Auswahl-Modal ──
    '📚 In Wissensdatenbank übernehmen': '📚 Add to knowledge base',
    'Zieldatenbank': 'Target base',
    'Übernehmen': 'Apply',

    // ── Dossier / Vorschläge / Detail / Einfügen / Ersetzen / Bestellplan ──
    '📄 Dossier': '📄 Dossier',
    '✨ Vorschläge': '✨ Suggestions',
    'Auswahl übernehmen': 'Apply selection',
    '📝 Aufgabe detaillieren': '📝 Detail task',
    'KI-Vorschläge — wähle per Häkchen und bearbeite die Felder vor dem Übernehmen.':
      'AI suggestions — tick the ones you want and edit the fields before applying.',
    '➕ Vorgang zwischen zwei Aufgaben einfügen': '➕ Insert task between two tasks',
    'Wähle Vorgänger (A) und Nachfolger (B). Die KI liest beide und schlägt passende Zwischenvorgänge vor. Beim Übernehmen wird A → neu → B verdrahtet und eine direkte Kante A→B aufgelöst.':
      'Choose predecessor (A) and successor (B). The AI reads both and suggests suitable intermediate tasks. On apply, A → new → B is wired and a direct edge A→B is dissolved.',
    'A (Vorgänger)': 'A (predecessor)',
    'B (Nachfolger)': 'B (successor)',
    '✨ KI-Vorschläge holen': '✨ Get AI suggestions',
    '🔁 Aufgabe ersetzen': '🔁 Replace task',
    'Durch bestehende Aufgabe ersetzen (z. B. T4 durch T10)': 'Replace with an existing task (e.g. T4 with T10)',
    'Die Verknüpfungen der ersetzten Aufgabe gehen auf die gewählte über; die ersetzte Aufgabe wird entfernt.':
      'The links of the replaced task transfer to the chosen one; the replaced task is removed.',
    'oder durch neue Aufgabe ersetzen (Verknüpfungen bleiben)': 'or replace with a new task (links remain)',
    'Dauer (Tage)': 'Duration (days)',
    '📅 Ressourcen- & Bestellplan': '📅 Resource & order plan',
    'Wann wird welche Ressource gebraucht (frühester Start der Aufgabe) und – bei hinterlegter Lieferzeit – bis wann sie bestellt sein muss.':
      'When each resource is needed (earliest start of the task) and – if a lead time is set – by when it must be ordered.',
    '⬇ Bestellplan CSV': '⬇ Order plan CSV',
  };

  const _collapse = (s) => s.trim().replace(/\s+/g, ' ');

  function _inSkip(node) {
    let el = node.parentElement;
    while (el) {
      if (SKIP_TAGS.has(el.tagName)) return true;
      if (el.id && SKIP_IDS.has(el.id)) return true;
      el = el.parentElement;
    }
    return false;
  }

  // Einen Textknoten übersetzen (en) bzw. wiederherstellen (de).
  function _applyTextNode(node, toEnglish) {
    if (toEnglish) {
      const raw = node.nodeValue;
      const key = _collapse(raw);
      if (!key) return;
      const en = EN[key];
      if (en === undefined) return;
      if (node.__i18n_de === undefined) node.__i18n_de = raw;
      const lead = raw.match(/^\s*/)[0];
      const trail = raw.match(/\s*$/)[0];
      node.nodeValue = lead + en + trail;
    } else if (node.__i18n_de !== undefined) {
      node.nodeValue = node.__i18n_de;
    }
  }

  // Ein Attribut (placeholder/title) übersetzen bzw. wiederherstellen.
  function _applyAttr(el, attr, cacheKey, toEnglish) {
    if (toEnglish) {
      const raw = el.getAttribute(attr);
      if (!raw) return;
      const en = EN[_collapse(raw)];
      if (en === undefined) return;
      if (el[cacheKey] === undefined) el[cacheKey] = raw;
      el.setAttribute(attr, en);
    } else if (el[cacheKey] !== undefined) {
      el.setAttribute(attr, el[cacheKey]);
    }
  }

  function apply() {
    const toEnglish = _lang === 'en';

    // Textknoten
    const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
    const nodes = [];
    let n;
    while ((n = walker.nextNode())) {
      if (n.nodeValue && n.nodeValue.trim() && !_inSkip(n)) nodes.push(n);
    }
    nodes.forEach((node) => _applyTextNode(node, toEnglish));

    // Attribute
    document.querySelectorAll('[placeholder]').forEach((el) => {
      if (!_inSkip(el)) _applyAttr(el, 'placeholder', '__i18n_de_ph', toEnglish);
    });
    document.querySelectorAll('[title]').forEach((el) => {
      if (!_inSkip(el)) _applyAttr(el, 'title', '__i18n_de_title', toEnglish);
    });

    document.documentElement.lang = _lang;
  }

  // Übersetzung eines einzelnen Strings (für JS-Code nutzbar).
  function t(deText) {
    if (_lang !== 'en') return deText;
    return EN[_collapse(deText)] || deText;
  }

  function getLang() { return _lang; }

  function setLang(lang) {
    const next = lang === 'en' ? 'en' : 'de';
    if (next === _lang) { apply(); return; }
    _lang = next;
    try { localStorage.setItem(STORAGE_KEY, next); } catch (_) {}
    apply();
  }

  // Beim Laden gespeicherte Sprache anwenden (vermeidet Aufblitzen).
  try {
    const saved = localStorage.getItem(STORAGE_KEY);
    if (saved === 'en') _lang = 'en';
  } catch (_) {}

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', apply);
  } else {
    apply();
  }

  return { t, apply, setLang, getLang };
})();
