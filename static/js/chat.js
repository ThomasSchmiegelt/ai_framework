/* ── AI_Framework_Thomas Chat ─────────────────────────────────────────────────────────── */

const Chat = (() => {
  let messages = [];      // { role, content, files }
  let isStreaming = false;
  let pendingFiles = [];  // { id, filename, is_image }
  let currentConvId = null;
  let abortController = null;   // bricht den laufenden /api/chat-Stream ab
  let showThinking = false;    // Denkprozess-Panel aktiv?
  let _presImages = [];        // /praesentation Bildmodus: [{name, data(dataURL)}], sortierbar
  let _presDoc = '';           // optionaler .md/.txt-Inhalt als Zusatzkontext

  // Anzeigenamen der Antwortstil-Personas (Profil → tone). Leer = Standard-Branding.
  const PERSONA_NAMES = {
    roboter:   'Roboter',
    professor: 'Herr Professor',
    doktor:    'Frau Doktor',
    felix:     'Felix',
    sandra:    'Sandra',
  };

  // Beschriftung der Assistenten-Antwort: gewählte Persona (z. B. „Roboter"),
  // sonst der Markenname. Übersetzt über I18n, falls verfügbar.
  function _assistantLabel() {
    let name = 'LOCAL AI';
    try {
      const tone = (typeof Profile !== 'undefined' && Profile.get) ? (Profile.get().tone || '') : '';
      if (tone && PERSONA_NAMES[tone]) {
        name = (typeof I18n !== 'undefined') ? I18n.t(PERSONA_NAMES[tone]) : PERSONA_NAMES[tone];
      }
    } catch (_) {}
    return '🤖 ' + name;
  }

  // ── Konversation laden ─────────────────────────────────────────────────────

  async function loadConversation(convId) {
    try {
      const resp = await fetch(`/api/conversations/${convId}`);
      if (!resp.ok) return;
      const data = await resp.json();
      messages = data.messages || [];
      currentConvId = convId;
      window._currentConvId = convId;

      // Projekt-Selector aktualisieren
      const chatProjSel = document.getElementById('chat-project-select');
      if (chatProjSel) chatProjSel.value = data.project_id || '';

      clearMessages();
      for (const msg of messages) {
        if (msg.role === 'system') continue;
        appendMessage(msg.role, msg.content);
      }
      scrollToBottom();

      // Canvas-Daten der Konversation wiederherstellen (nur rendern, kein Tab-Wechsel)
      if (data.canvas_json) {
        try {
          const canvasData = JSON.parse(data.canvas_json);
          CanvasRenderer.render(canvasData);
          const slideNav = document.getElementById('slide-nav');
          if (slideNav) slideNav.style.display = canvasData.type === 'presentation' ? 'flex' : 'none';
          document.getElementById('canvas-title').textContent = canvasData.title || '';
        } catch (_) {}
      }
    } catch (e) {
      console.error('Konversation laden fehlgeschlagen', e);
    }
  }

  function newConversation() {
    messages = [];
    pendingFiles = [];
    currentConvId = `conv_${Date.now()}`;
    window._currentConvId = currentConvId;
    clearMessages();
    showWelcome(true);
    document.querySelectorAll('.conv-item').forEach(el => el.classList.remove('active'));
  }

  // ── Nachrichten senden ─────────────────────────────────────────────────────

  // Senden-Button: während des Streamings = Abbruch (Sanduhr), sonst senden
  function sendOrAbort() {
    if (isStreaming) { abortStreaming(); return; }
    sendMessage();
  }

  async function sendMessage() {
    if (isStreaming) return;

    const input = document.getElementById('message-input');
    let text = input.value.trim();
    if (!text && pendingFiles.length === 0) return;
    hideSlashHints();

    // Befehlsübersicht: „/hilfe" (Aliase /help, /befehle, /?) zeigt alle Chat-Befehle
    // als Karte an (kein LLM-Aufruf).
    if (_parseHelp(text)) {
      input.value = '';
      autoResizeTextarea(input);
      runHelp();
      return;
    }

    // Nutzer-Feedback: „/- <Text>" (Fehler/Problem) bzw. „/+ <Text>" (Idee/
    // Verbesserung) wird ins Markdown-Protokoll geschrieben, nicht ans LLM gesendet.
    const fb = _parseFeedback(text);
    if (fb) {
      input.value = '';
      autoResizeTextarea(input);
      runFeedback(fb.kind, fb.text);
      return;
    }

    // Deepdive: „/dd10" / „/ddd10" / „/deepdive10" / „/deepdivedocument10" → eigener
    // Ablauf (X Fragen zur letzten Antwort, der Reihe nach gesucht & beantwortet).
    const dd = _parseDeepDive(text);
    if (dd) {
      input.value = '';
      autoResizeTextarea(input);
      runDeepDive(dd.count, dd.asDocument, dd.extra);
      return;
    }

    // Plan-Orchestrator: „/plan <Zusatz>" baut aus dem Chat-Verlauf eine Strategie,
    // Beratungs-Agenten, einen Einsatz-/Ressourcenplan und eine Bewertungs-Jury
    // (Vorschau → auf Bestätigung anlegen). Vor der Slash-Agent-Auflösung prüfen.
    const pl = _parsePlan(text);
    if (pl) {
      input.value = '';
      autoResizeTextarea(input);
      runPlan(pl.extra, pl.pinned, pl.unresolved, pl.count);
      return;
    }

    // Projekt-Orchestrator: „/projekt <Beschreibung>" (Aliase /vorhaben, /projektplan)
    // zerlegt ein Vorhaben phasenweise (Morph → Paarvergleich → Plan → To-Do → …) und
    // legt auf Bestätigung EIN Projekt mit allen Artefakten an. Vorschau-Muster wie /plan.
    const pj = _parseProjekt(text);
    if (pj !== null) {
      input.value = '';
      autoResizeTextarea(input);
      runProjektOrchestrator(pj);
      return;
    }

    // Vorgang laden: „/vorgang" (Aliase /vorgänge) listet gespeicherte /projekt-Vorgänge
    // (inkl. mitgeliefertem Beispiel) und baut den gewählten als Vorschau-Karten neu auf
    // → „✅ Alles anlegen" (Gegenstück zum Speichern des Orchestrators).
    const vg = _parseVorgang(text);
    if (vg !== null) {
      input.value = '';
      autoResizeTextarea(input);
      runVorgangLoader();
      return;
    }

    // Erweiterte Suche: „/such <Begriff>" (Aliase /suche, /finde, /search) lässt die KI
    // alternative Suchbegriffe erzeugen, durchsucht damit das Web und fasst zusammen.
    const se = _parseSearch(text);
    if (se) {
      input.value = '';
      autoResizeTextarea(input);
      runSearch(se.query);
      return;
    }

    // Bildgenerierung: „/bild <Beschreibung>" erzeugt direkt ein Bild, „/bildhelp"
    // (oder leeres „/bild") öffnet den geführten Dialog.
    const bi = _parseBild(text);
    if (bi) {
      input.value = '';
      autoResizeTextarea(input);
      if (bi.help) runBildHelp(bi.prompt);
      else runBild(bi.prompt);
      return;
    }

    // Musik: „/musik <Stil/Stimmung>" (Aliase /music, /song) erzeugt ein kurzes Stück
    // (algorithmisch, tools/music.py) und spielt es als Audio im Chat ab.
    const mu = _parseMusik(text);
    if (mu) {
      input.value = '';
      autoResizeTextarea(input);
      runMusik(mu.description);
      return;
    }

    // Excel-Vergleich: „/excelvergleich" (Aliase /xlsvergleich, /excel) öffnet ein
    // Overlay (zwei Dateien + Blatt/Schlüsselspalte) und rendert das Ergebnis im Chat.
    const xc = _parseExcelCompare(text);
    if (xc) {
      input.value = '';
      autoResizeTextarea(input);
      runExcelCompare();
      return;
    }

    // Paarvergleich: „/paarvergleich <Thema>" (Aliase /entscheidung, /ahp) startet den
    // schrittweisen Merkmal-für-Merkmal-Paarvergleich (Varianten-Overlay über dem Chat).
    const pv = _parsePairwise(text);
    if (pv) {
      input.value = '';
      autoResizeTextarea(input);
      runPairwise(pv.topic);
      return;
    }

    // Bild-Modus per Toolbar-Haken (🎨): die nächste normale Nachricht wird zum
    // Bild-Prompt. One-shot – der Haken wird danach zurückgesetzt.
    const imgToggle = document.getElementById('btn-image-toggle');
    if (imgToggle && imgToggle.classList.contains('active') && pendingFiles.length === 0) {
      imgToggle.classList.remove('active');
      input.value = '';
      autoResizeTextarea(input);
      runBild(text);
      return;
    }

    // Tiefe Recherche: „/recherche <Thema>" (Aliase /deep, /tief) öffnet die Rückfrage
    // (Tiefe + Umfang) und startet dann die mehrstufige Web-Recherche.
    const dr = _parseDeepResearch(text);
    if (dr) {
      input.value = '';
      autoResizeTextarea(input);
      if (dr.topic) _openResearchForm(dr.topic);
      else showToast('Bitte nach „/recherche" ein Thema angeben');
      return;
    }

    // Arbeitsablauf: „/workflow" (Aliase /ablauf, /flow) führt die nummerierten
    // Schritte nacheinander aus, speichert Zwischenergebnisse und präsentiert am
    // Ende ein Gesamtergebnis (mit Buttons → Präsentation / → Planer).
    const wf = _parseWorkflow(text);
    if (wf) {
      input.value = '';
      autoResizeTextarea(input);
      if (wf.steps.length) runWorkflow(wf.steps, wf.goal);
      else showToast('Bitte nummerierte Schritte angeben, z. B. /workflow 1. … 2. …');
      return;
    }

    // Ziel-Loop: „/ziel <Beschreibung>" (Aliase /goal, /zi).  Anders als /workflow gibt
    // man KEINE Schritte vor, sondern nur EIN Ziel — der Loop plant selbst, arbeitet in
    // Runden (Handeln → Bewerten) darauf hin und entscheidet selbst, wann es erreicht ist.
    const gl = _parseGoal(text);
    if (gl) {
      input.value = '';
      autoResizeTextarea(input);
      if (gl.goal) runGoalLoop(gl.goal, { rounds: gl.rounds, web: gl.web });
      else showToast('Bitte nach „/ziel" ein Ziel angeben, z. B. /ziel Vergleiche 3 Akku-Chemien für ein E-Bike');
      return;
    }

    // Geführter Präsentationsassistent: „/praesentation <Thema>" öffnet ein kurzes
    // Interview (Zielgruppe/Ziel/Umfang), dann läuft Gliederung → Webrecherche je
    // Punkt → Bilder (flächiges Deckblatt + Abschluss) automatisch durch.
    const pr = _parseIllustratedPres(text);
    if (pr) {
      input.value = '';
      autoResizeTextarea(input);
      // Thema optional: ohne Thema öffnet sich das Interview im Bildmodus (Bilder → Präsentation).
      _openPresInterview(pr.topic, pr.images);
      return;
    }

    // Bild → Prompt: „/bildprompt [Stil]" öffnet einen Bild-Picker und leitet daraus
    // einen Text-zu-Bild-Prompt ab (Vision-Modell).
    const ip = _parseBildPrompt(text);
    if (ip) {
      input.value = '';
      autoResizeTextarea(input);
      runBildPrompt(ip.style);
      return;
    }

    // Bildbearbeitung: „/bildedit [Anweisung]" — Bild wählen + sagen, wie es
    // verändert werden soll (img2img, lokal über Z-Image oder ein fähiges API-Modell).
    const be = _parseBildEdit(text);
    if (be) {
      input.value = '';
      autoResizeTextarea(input);
      runBildEdit(be.instruction);
      return;
    }

    // Hochskalieren: „/upscale" — Bild wählen und vergrößern (KI-Detail über Z-Image
    // oder schnell per Lanczos).
    const up = _parseUpscale(text);
    if (up) {
      input.value = '';
      autoResizeTextarea(input);
      runUpscale();
      return;
    }

    // Rückfragen: „/frag <Aufgabe>" erzeugt eine dynamische Eingabemaske (Text/
    // Auswahl), deren Antworten an die Aufgabe gehängt und normal gesendet werden.
    const fr = _parseFrag(text);
    if (fr) {
      input.value = '';
      autoResizeTextarea(input);
      runFrag(fr.task);
      return;
    }

    // Automatisches Angebot eines Tabellenvergleichs: sind ≥2 Excel-/CSV-Dateien
    // angehängt (und kein Slash-Befehl), den zellenweisen Vergleich anbieten. Läuft
    // auch im Assistent-Modus (reines Chat/Overlay).
    const _cmpTables = pendingFiles.filter(_looksLikeTableFile);
    if (!_bypassCompareOffer && _cmpTables.length >= 2 && !text.startsWith('/') && _compareOfferEnabled()) {
      _offerCompare(_cmpTables.slice(0, 2), text);
      return;
    }
    _bypassCompareOffer = false;

    // Automatisches Angebot einer tiefen Recherche bei breiten Fakten-/Recherchefragen
    // (Profil-Häkchen, Web erlaubt, kein Agent/keine Datei). Fragt Tiefe+Umfang ab.
    if (!_bypassResearchOffer && pendingFiles.length === 0 && _researchOfferEnabled()
        && _looksLikeResearch(text)) {
      input.value = '';
      autoResizeTextarea(input);
      _offerResearch(text);
      return;
    }
    _bypassResearchOffer = false;

    // Slash-Agent: führendes „/Name" wählt nur für DIESE Nachricht einen Agenten
    let slashAgent = null;
    const slash = _resolveSlashAgent(text);
    if (slash && slash.agent) {
      slashAgent = slash.agent;
      text = slash.rest.trim();
      if (!text && pendingFiles.length === 0) {
        showToast('Bitte nach /' + (slashAgent.name || '') + ' noch eine Frage eingeben');
        return;
      }
    } else if (slash && slash.notFound) {
      showToast('Kein Agent für „/' + slash.token + '" gefunden – Nachricht wird normal gesendet');
    }

    showWelcome(false);
    isStreaming = true;
    setBtnSendState(false);

    // Denkprozess-Panel für die neue Antwort leeren
    resetThinking();

    const model = (typeof Profile !== 'undefined' ? Profile.modelFor('general') : '') || undefined;
    // Slash-Agent hat Vorrang (nur für diese Nachricht), sonst der Selektor
    const agentId = slashAgent ? slashAgent.id : (document.getElementById('agent-select').value || null);
    const useSearch = document.getElementById('btn-search-toggle').classList.contains('active');

    // Datei-IDs sammeln
    const fileIds = pendingFiles.map(f => f.id);
    const msg = { role: 'user', content: text, files: fileIds.length ? fileIds : undefined };
    messages.push(msg);

    // User-Nachricht anzeigen
    const userBubble = appendMessage('user', text, pendingFiles);

    // Input zurücksetzen
    input.value = '';
    autoResizeTextarea(input);
    pendingFiles = [];
    renderFilePreview();

    if (!currentConvId) currentConvId = `conv_${Date.now()}`;

    // Assistant-Platzhalter
    const assistantRow = appendMessage('assistant', '', [], true);
    const bubbleContent = assistantRow.querySelector('.bubble-content');
    // Eigenes Textelement, damit eingebettete Medien (Karte, Diagramm) beim
    // Text-Streaming und finalen Markdown-Rendern nicht überschrieben werden.
    const textEl = document.createElement('div');
    textEl.className = 'bubble-text';
    bubbleContent.appendChild(textEl);
    if (slashAgent) insertAgentNote(bubbleContent, textEl, slashAgent);
    let fullText = '';

    // Sichtbare „arbeitet…"-Anzeige (Sanduhr), bis der erste Inhalt kommt
    const workingEl = makeWorking('arbeitet');
    bubbleContent.insertBefore(workingEl, textEl);
    let _workingCleared = false;
    const clearWorking = () => { if (!_workingCleared) { _workingCleared = true; workingEl.remove(); } };

    // Tool-Status-Element
    let toolStatusEl = null;

    // Tab-übergreifend: Wunsch „… in die To-Do-Liste / in den Planer" merken, um nach
    // der Antwort die Übernahme-Rückfrage anzubieten (die Antwort erzeugt das Modell normal).
    _tabHandoffPending = _planIntent(text) ? 'planner' : (_todoIntent(text) ? 'todo' : _tabIntent(text));

    // SSE-Stream starten (abbrechbar über AbortController)
    abortController = new AbortController();
    let wasAborted = false;
    try {
      const resp = await fetch('/api/chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        signal: abortController.signal,
        body: JSON.stringify({
          messages: messages.filter(m => m.role !== 'system'),
          model,
          agent_id: agentId || undefined,
          use_tools: true,
          web_search: useSearch,
          conversation_id: currentConvId,
          rag_collections: (typeof RAG !== 'undefined') ? RAG.selectedCollections() : [],
          show_thinking: showThinking,
        }),
      });

      const reader = resp.body.getReader();
      const decoder = new TextDecoder();
      let buffer = '';

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split('\n');
        buffer = lines.pop();

        for (const line of lines) {
          if (!line.startsWith('data: ')) continue;
          try {
            const event = JSON.parse(line.slice(6));
            handleStreamEvent(event, textEl, fullText, (t) => { fullText = t; });
            // Sobald echter Inhalt kommt, die „arbeitet…"-Anzeige entfernen
            if (['text', 'image', 'map', 'canvas', 'diagram', 'done', 'error', 'tool_progress'].includes(event.type)) clearWorking();
            if (event.type === 'tool_start') {
              toolStatusEl = showToolStatus(assistantRow, event.tool, event.args);
            } else if (event.type === 'tool_progress') {
              // Live-Fortschritt eines Tab-Agenten (tiefe Recherche/Arbeitsablauf/To-Do)
              if (!toolStatusEl) toolStatusEl = showToolStatus(assistantRow, event.tool, {});
              const _sp = toolStatusEl.querySelector('span');
              if (_sp) _sp.textContent = event.message || '';
              scrollToBottom();
            } else if (event.type === 'tool_done') {
              if (toolStatusEl) { toolStatusEl.remove(); toolStatusEl = null; }
            } else if (event.type === 'canvas') {
              showCanvasPanel(event.data);
            } else if (event.type === 'code') {
              if (typeof CodeIDE !== 'undefined') CodeIDE.loadFromChat(event.code, event.name);
            } else if (event.type === 'image') {
              insertImage(bubbleContent, event.data);
            } else if (event.type === 'map') {
              insertMap(bubbleContent, event.data);
            } else if (event.type === 'diagram') {
              insertDiagram(bubbleContent, event.data);
            } else if (event.type === 'rag') {
              insertRagSources(bubbleContent, textEl, event.sources);
            } else if (event.type === 'adaptive') {
              insertAdaptiveNote(bubbleContent, textEl, event.role);
            } else if (event.type === 'thinking') {
              appendThinking(event.content);
            } else if (event.type === 'done') {
              if (event.tokens && typeof TokenMeter !== 'undefined') TokenMeter.add(event.tokens, 'Chat');
            }
          } catch (_) {}
        }
      }
    } catch (e) {
      if (e.name === 'AbortError') {
        wasAborted = true;
      } else {
        textEl.innerHTML = `<em style="color:#ef4444">Fehler: ${e.message}</em>`;
      }
    }
    clearWorking();

    // Cursor entfernen, Markdown rendern (nur das Textelement, Medien bleiben erhalten)
    const cursor = textEl.querySelector('.cursor');
    if (cursor) cursor.remove();
    renderMarkdown(textEl, fullText);
    if (wasAborted) {
      const note = document.createElement('div');
      note.style.cssText = 'margin-top:6px;font-size:12px;color:var(--text-muted);font-style:italic';
      note.textContent = '⏹ Abgebrochen';
      textEl.appendChild(note);
    }

    messages.push({ role: 'assistant', content: fullText });

    // Stellt die Antwort selbst Rückfragen? → Angebot einer strukturierten Maske.
    _maybeOfferClarify(assistantRow, fullText);

    // Tab-übergreifend: Wollte der Nutzer das Ergebnis in einen anderen Tab? → Rückfrage
    // und dann wirklich übernehmen (To-Do: neu/ergänzen; Planer: als Projektplan).
    if (_tabHandoffPending && fullText.trim() && !wasAborted) {
      const _t = _tabHandoffPending; _tabHandoffPending = '';
      _handoffToTab(_t, fullText);
    }

    abortController = null;
    isStreaming = false;
    setBtnSendState(true);
    scrollToBottom();

    // Konversationsliste aktualisieren
    loadConversationList();

    // Auto-Komprimierung: Überlauf prüfen, Leerlauf-Timer neu starten
    resetIdleTimer();
    _autoCompress('overflow');
  }

  function handleStreamEvent(event, textEl, currentText, setText) {
    if (event.type === 'text') {
      const newText = currentText + event.content;
      setText(newText);
      // Rohtext + cursor anzeigen
      textEl.textContent = newText;
      if (!textEl.querySelector('.cursor')) {
        const cur = document.createElement('span');
        cur.className = 'cursor';
        textEl.appendChild(cur);
      }
      scrollToBottom();
    } else if (event.type === 'error') {
      textEl.innerHTML = `<em style="color:#ef4444">Fehler: ${escHtml(event.message)}</em>`;
    }
  }

  // Löst ein führendes „/Name" am Nachrichtenanfang in einen Agenten auf.
  // Rückgabe: {agent, rest} bei Treffer, {notFound, token, rest} sonst, null wenn kein „/".
  function _resolveSlashAgent(text) {
    const m = text.match(/^\/(\S+)\s*([\s\S]*)$/);
    if (!m) return null;
    const token = m[1].toLowerCase();
    const rest = m[2];
    let agents = [];
    try { agents = (typeof AgentManager !== 'undefined' && AgentManager.getAgents()) || []; } catch (_) {}
    const norm = s => String(s || '').toLowerCase();
    const slug = s => norm(s).replace(/[^a-z0-9]/g, '');
    // 1. exakte id / exakter Name
    let a = agents.find(x => norm(x.id) === token || norm(x.name) === token || slug(x.name) === slug(token));
    // 2. Präfix auf id, Name oder slug(Name)
    if (!a) a = agents.find(x => norm(x.id).startsWith(token) || norm(x.name).startsWith(token) || slug(x.name).startsWith(slug(token)));
    if (!a) return { notFound: true, token, rest };
    return { agent: a, rest };
  }
  // Auch für andere Tabs nutzbar machen (Medizin, Mathe, Dokumente): /Agent in jeder Chatzeile
  window.resolveSlashAgent = _resolveSlashAgent;

  // Löst EINEN Token (ohne führenden „/") in einen vorhandenen Agenten auf — für
  // feste Agenten in „/plan … /dsgvo /tisax". Gibt das Agent-Objekt oder null zurück.
  function _findAgentByToken(token) {
    let agents = [];
    try { agents = (typeof AgentManager !== 'undefined' && AgentManager.getAgents()) || []; } catch (_) {}
    const norm = s => String(s || '').toLowerCase();
    const slug = s => norm(s).replace(/[^a-z0-9]/g, '');
    const t = norm(token);
    let a = agents.find(x => norm(x.id) === t || norm(x.name) === t || slug(x.name) === slug(t));
    if (!a) a = agents.find(x => norm(x.id).startsWith(t) || norm(x.name).startsWith(t) || slug(x.name).startsWith(slug(t)));
    return a || null;
  }

  function insertAgentNote(container, beforeEl, agent) {
    if (!agent) return;
    const box = document.createElement('div');
    box.style.cssText = 'margin:0 0 10px;padding:6px 10px;border-left:3px solid var(--accent);background:var(--accent-dim);border-radius:6px;font-size:12px;color:var(--text-dim)';
    box.innerHTML = `➜ <strong>Agent:</strong> ${escHtml((agent.icon ? agent.icon + ' ' : '') + (agent.name || agent.id))} <span class="planner-muted">(nur diese Frage)</span>`;
    container.insertBefore(box, beforeEl);
    scrollToBottom();
  }

  function insertAdaptiveNote(container, beforeEl, role) {
    if (!role) return;
    const box = document.createElement('div');
    box.style.cssText = 'margin:0 0 10px;padding:6px 10px;border-left:3px solid var(--accent);background:var(--accent-dim);border-radius:6px;font-size:12px;color:var(--text-dim)';
    box.innerHTML = `🧠 <strong>Adaptiver Agent:</strong> ${escHtml(role)}`;
    container.insertBefore(box, beforeEl);
    scrollToBottom();
  }

  function insertRagSources(container, beforeEl, sources) {
    if (!sources || !sources.length) return;
    const box = document.createElement('div');
    box.style.cssText = 'margin:0 0 10px;padding:8px 10px;border-left:3px solid var(--accent);background:var(--accent-dim);border-radius:6px;font-size:12px;color:var(--text-dim)';
    const items = sources.map(s =>
      `<span title="Score ${s.score}">${s.image_url ? '🖼' : '📄'} ${escHtml(s.filename)} <span class="planner-muted">(${escHtml(s.collection)})</span></span>`
    ).join(' · ');
    box.innerHTML = `📚 <strong>Kontext aus Wissenssammlung:</strong> ${items}`;
    // Bild-aware RAG: Thumbnails der Bild-Treffer (dedupe je Bild-URL), Klick öffnet Vollbild.
    const seen = new Set();
    const thumbs = [];
    for (const s of sources) {
      if (s.image_url && !seen.has(s.image_url)) { seen.add(s.image_url); thumbs.push(s); }
    }
    if (thumbs.length) {
      const row = document.createElement('div');
      row.style.cssText = 'display:flex;flex-wrap:wrap;gap:6px;margin-top:8px';
      for (const s of thumbs) {
        const a = document.createElement('a');
        a.href = s.image_url; a.target = '_blank'; a.rel = 'noopener noreferrer';
        a.title = (s.filename || 'Bild') + ' — zum Vergrößern klicken';
        const img = document.createElement('img');
        img.src = s.image_url; img.alt = s.filename || 'Bild';
        img.style.cssText = 'height:64px;width:auto;max-width:120px;object-fit:cover;border-radius:6px;border:1px solid var(--border);cursor:pointer;display:block';
        a.appendChild(img);
        row.appendChild(a);
      }
      box.appendChild(row);
    }
    container.insertBefore(box, beforeEl);
    scrollToBottom();
  }

  function insertImage(container, src) {
    const wrapper = document.createElement('div');
    wrapper.style.cssText = 'margin: 10px 0';
    const img = document.createElement('img');
    img.src = src;
    img.style.cssText = 'max-width:100%;border-radius:8px;display:block;box-shadow:0 2px 12px #0006';
    img.alt = 'Bild';
    wrapper.appendChild(img);
    // 💾 Speichern-Knopf unter jedem Bild (Data-URI/URL herunterladen).
    const bar = document.createElement('div');
    bar.style.cssText = 'margin-top:4px';
    const dl = document.createElement('a');
    dl.href = src;
    dl.download = 'bild_' + Date.now() + '.png';
    dl.textContent = '💾 speichern';
    dl.title = 'Bild speichern';
    dl.style.cssText = 'font-size:12px;color:var(--accent,#3b76ba);text-decoration:none';
    bar.appendChild(dl);
    wrapper.appendChild(bar);
    container.appendChild(wrapper);
    scrollToBottom();
  }

  function insertMap(container, data) {
    if (typeof L === 'undefined') {
      const note = document.createElement('div');
      note.style.cssText = 'margin:10px 0;color:#ef4444';
      note.textContent = 'Karte konnte nicht geladen werden (Leaflet nicht verfügbar).';
      container.appendChild(note);
      return;
    }

    const wrapper = document.createElement('div');
    // Definite Breite: die Assistenten-Blase ist ein schrumpfendes Flex-Item
    // (align-items:flex-start) und das Leaflet-div hat keine intrinsische Breite –
    // ohne feste Breite kollabiert die Karte schmal. calc(100vw-…) bleibt responsiv.
    wrapper.style.cssText = 'margin:10px 0;width:min(680px,calc(100vw - 60px));max-width:100%';

    const mapEl = document.createElement('div');
    mapEl.style.cssText = 'height:360px;width:100%;border-radius:8px;overflow:hidden;box-shadow:0 2px 12px #0006';
    wrapper.appendChild(mapEl);

    const info = document.createElement('div');
    info.style.cssText = 'margin-top:6px;font-size:13px;color:#d4e8f8';
    info.innerHTML =
      `🗺️ <strong>${escHtml(data.start.name.split(',')[0])}</strong> → ` +
      `<strong>${escHtml(data.end.name.split(',')[0])}</strong> · ` +
      `${data.distance_km} km · ${escHtml(data.duration_text)}`;
    wrapper.appendChild(info);

    container.appendChild(wrapper);

    // Leaflet muss die Größe nach dem Einfügen ins DOM kennen
    const map = L.map(mapEl, { scrollWheelZoom: false });
    L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
      maxZoom: 19,
      attribution: '© OpenStreetMap',
    }).addTo(map);

    const line = L.polyline(data.coordinates, { color: '#3b76ba', weight: 5, opacity: 0.85 }).addTo(map);
    L.marker([data.start.lat, data.start.lon]).addTo(map).bindPopup('Start: ' + escHtml(data.start.name.split(',')[0]));
    L.marker([data.end.lat, data.end.lon]).addTo(map).bindPopup('Ziel: ' + escHtml(data.end.name.split(',')[0]));
    map.fitBounds(line.getBounds(), { padding: [30, 30] });

    // Nach dem Layout-Reflow Kachelgröße korrigieren
    setTimeout(() => map.invalidateSize(), 100);
    scrollToBottom();
  }

  let _mermaidReady = false;
  function _ensureMermaid() {
    if (_mermaidReady || typeof mermaid === 'undefined') return;
    mermaid.initialize({ startOnLoad: false, theme: 'dark',
      themeVariables: { background: '#1e1e2e', primaryColor: '#3b76ba',
                        primaryTextColor: '#d4e8f8', lineColor: '#a3c8eb' } });
    _mermaidReady = true;
  }

  async function _renderMermaid(el, definition) {
    _ensureMermaid();
    if (typeof mermaid === 'undefined') { el.textContent = definition; return; }
    const id = 'mm-' + Date.now() + '-' + Math.random().toString(36).slice(2, 7);
    try {
      const { svg } = await mermaid.render(id, definition);
      el.innerHTML = svg;
    } catch (_) {
      el.textContent = definition;
    }
  }

  function insertDiagram(container, data) {
    const card = document.createElement('div');
    card.className = 'diagram-card';
    if (data.title) {
      const t = document.createElement('div');
      t.className = 'diagram-card-title';
      t.textContent = data.title;
      card.appendChild(t);
    }
    const diagramEl = document.createElement('div');
    diagramEl.className = 'diagram-body';
    card.appendChild(diagramEl);
    container.appendChild(card);
    _renderMermaid(diagramEl, data.definition);
    scrollToBottom();
  }

  // Sichtbare „arbeitet…"-Anzeige (Sanduhr + Spinner + laufende Punkte) für Chat & Bilderstellung
  // „arbeitet…"-Anzeige mit LIVE-Sekundenzähler + indeterminatem Fortschrittsbalken.
  // Der Zähler beweist, dass der Vorgang lebt (wichtig bei langen Läufen wie der
  // Bilderzeugung: 30–60 s je Bild). `opts.hintAfter` (Sekunden) blendet nach einer
  // Weile `opts.hint` als beruhigenden Zusatztext ein. Der Timer räumt sich selbst
  // auf, sobald das Element aus dem DOM entfernt/ersetzt wird (`isConnected`).
  function makeWorking(label, opts) {
    opts = opts || {};
    const el = document.createElement('div');
    el.className = 'chat-working';
    el.innerHTML = `<span class="hourglass">⏳</span><span class="spinner"></span>`
      + `<span class="cw-label">${escHtml(label || 'arbeitet')}</span><span class="cw-dots"></span>`
      + `<span class="cw-elapsed"> · 0s</span>`
      + `<span class="cw-bar"><span class="cw-bar-fill"></span></span>`;
    const t0 = Date.now();
    const elapsedEl = el.querySelector('.cw-elapsed');
    const labelEl = el.querySelector('.cw-label');
    const hintAfter = Number(opts.hintAfter) || 0;
    const hint = opts.hint || '';
    let hintShown = false;
    const iv = setInterval(() => {
      if (!el.isConnected) { clearInterval(iv); return; }
      const s = Math.round((Date.now() - t0) / 1000);
      elapsedEl.textContent = ' · ' + s + 's';
      if (hint && !hintShown && hintAfter && s >= hintAfter) {
        hintShown = true;
        labelEl.textContent = hint;
      }
    }, 1000);
    return el;
  }

  function showToolStatus(row, toolName, args) {
    const TOOL_LABELS = {
      web_search:          `🔍 Suche: "${args?.query || ''}"`,
      calculate:           '🧮 Berechnung wird ausgeführt…',
      create_presentation: '📊 Präsentation wird erstellt…',
      create_spreadsheet:  '📋 Tabelle wird erstellt…',
      unit_convert:        `🔄 Einheitenumrechnung: ${args?.value ?? ''} ${args?.from_unit ?? ''} → ${args?.to_unit ?? ''}`,
      solve_equation:      `📐 Gleichung lösen: ${args?.expression ?? ''}`,
      plot_chart:          `📈 Diagramm erstellen: ${args?.title || args?.y_label || ''}`,
      material_lookup:     `🔩 Werkstoff: ${args?.name ?? ''}`,
      bolt_calculator:     `🔧 Schraubenauslegung M${args?.d_nom ?? ''}`,
      generate_report:     `📄 Bericht erstellen: ${args?.title ?? ''}`,
      route_planner:       `🗺️ Route: ${args?.origin ?? ''} → ${args?.destination ?? ''}`,
      create_diagram:      `📐 Diagramm: ${args?.title || args?.diagram_type || ''}`,
      solve_math:          `🧮 SymPy löst: ${args?.expression ?? ''}`,
      deep_research:       `🔎 Tiefe Recherche: „${args?.topic || ''}“…`,
      run_workflow:        `🧵 Arbeitsablauf (${(args?.steps || []).length || ''} Schritte)…`,
      ask_todo:            `✅ To-Do-Bestand: „${args?.question || ''}“…`,
    };
    const status = document.createElement('div');
    status.className = 'tool-status';
    status.innerHTML = `<div class="spinner"></div><span>${TOOL_LABELS[toolName] || toolName}</span>`;
    row.appendChild(status);
    scrollToBottom();
    return status;
  }

  // ── Canvas-Anzeige ─────────────────────────────────────────────────────────

  function showCanvasPanel(data) {
    CanvasRenderer.render(data);
    switchTab('canvas');

    // Slide-Nav anzeigen/verstecken
    const slideNav = document.getElementById('slide-nav');
    if (data.type === 'presentation') {
      slideNav.style.display = 'flex';
    } else {
      slideNav.style.display = 'none';
    }
    CanvasRenderer.render(data);
  }

  // ── Datei-Upload ──────────────────────────────────────────────────────────

  async function uploadFile(file) {
    const formData = new FormData();
    formData.append('file', file);

    const resp = await fetch('/api/upload', { method: 'POST', body: formData });
    const data = await resp.json();
    pendingFiles.push(data);
    renderFilePreview();
    showToast(`📎 ${data.filename} hochgeladen`);
  }

  function renderFilePreview() {
    const area = document.getElementById('file-preview-area');
    area.innerHTML = '';
    for (const f of pendingFiles) {
      const chip = document.createElement('div');
      chip.className = 'file-chip';
      const icon = f.is_image ? '🖼️' : f.filename.endsWith('.pdf') ? '📄' : '📎';
      chip.innerHTML = `${icon} ${escHtml(f.filename)} <span class="remove-file" data-id="${f.id}">✕</span>`;
      chip.querySelector('.remove-file').addEventListener('click', () => {
        pendingFiles = pendingFiles.filter(pf => pf.id !== f.id);
        renderFilePreview();
      });
      area.appendChild(chip);
    }
  }

  // ── Nachrichten anzeigen ──────────────────────────────────────────────────

  function clearMessages() {
    const container = document.getElementById('messages');
    container.innerHTML = '';
  }

  function appendMessage(role, content, files = [], streaming = false) {
    const container = document.getElementById('messages');

    // Welcome-Screen ausblenden
    const welcome = document.getElementById('welcome');
    if (welcome) welcome.style.display = 'none';

    const row = document.createElement('div');
    row.className = `message-row ${role}`;

    const roleLabel = document.createElement('div');
    roleLabel.className = 'msg-role';
    const userLabel = (typeof I18n !== 'undefined') ? I18n.t('Du') : 'Du';
    roleLabel.textContent = role === 'user' ? userLabel : _assistantLabel();

    const bubble = document.createElement('div');
    bubble.className = 'msg-bubble';

    const bubbleContent = document.createElement('div');
    bubbleContent.className = 'bubble-content';

    if (!streaming && content) {
      renderMarkdown(bubbleContent, content);
    }

    bubble.appendChild(bubbleContent);

    // Datei-Anhänge beim User anzeigen
    if (files && files.length > 0) {
      const fileRow = document.createElement('div');
      fileRow.style.display = 'flex';
      fileRow.style.flexWrap = 'wrap';
      fileRow.style.gap = '4px';
      fileRow.style.marginTop = '6px';
      for (const f of files) {
        const chip = document.createElement('div');
        chip.className = 'file-chip';
        chip.textContent = `📎 ${f.filename || f.id}`;
        fileRow.appendChild(chip);
      }
      bubble.appendChild(fileRow);
    }

    // Antwort direkt als Markdown-Datei speicherbar (Roh-Markdown aus _rawMd;
    // während des Streamings wird der jeweils aktuelle Stand gespeichert)
    if (role === 'assistant') {
      const saveBar = document.createElement('div');
      saveBar.className = 'msg-save-bar';
      const mdBtn = document.createElement('button');
      mdBtn.textContent = '⬇ .md';
      mdBtn.title = 'Diese Antwort als Markdown-Datei speichern';
      mdBtn.addEventListener('click', () => {
        const raw = (bubbleContent._rawMd || bubbleContent.textContent || '').trim();
        if (!raw) { if (typeof showToast === 'function') showToast('Nichts zu speichern'); return; }
        downloadTextFile(_saveName('antwort', 'md'), raw + '\n', 'text/markdown;charset=utf-8');
        if (typeof showToast === 'function') showToast('✓ Antwort als MD gespeichert');
      });
      saveBar.appendChild(mdBtn);
      // 🔊 Vorlesen in der Stimme der aktiven Persona (Profil → tone). Nur, wenn der
      // Browser Sprachausgabe unterstützt.
      if (typeof TTS !== 'undefined' && TTS.available()) {
        const ttsBtn = document.createElement('button');
        ttsBtn.textContent = '🔊';
        const tone = (typeof Profile !== 'undefined' && Profile.get) ? (Profile.get().tone || '') : '';
        ttsBtn.title = 'Antwort vorlesen (' + TTS.personaLabel(tone) + ')';
        ttsBtn.addEventListener('click', () => {
          const raw = (bubbleContent._rawMd || bubbleContent.textContent || '').trim();
          if (!raw) return;
          const t = (typeof Profile !== 'undefined' && Profile.get) ? (Profile.get().tone || '') : '';
          TTS.toggle(ttsBtn, raw, t);
        });
        saveBar.appendChild(ttsBtn);
      }
      // ↪ Diese Antwort an einen anderen Tab übergeben (To-Do, Planer, Code, Mathe,
      // Medizin, Varianten, Morph-Kasten, Patente, Anfrage, Rechnung, Zeugnis).
      const sendBtn = document.createElement('button');
      sendBtn.textContent = '↪ senden an…';
      sendBtn.title = 'Diese Antwort in einen anderen Tab übernehmen';
      sendBtn.addEventListener('click', () => {
        const raw = (bubbleContent._rawMd || bubbleContent.textContent || '').trim();
        if (!raw) { if (typeof showToast === 'function') showToast('Nichts zu übernehmen'); return; }
        _openHandoffMenu(raw, sendBtn);
      });
      saveBar.appendChild(sendBtn);
      bubble.appendChild(saveBar);
    }

    row.appendChild(roleLabel);
    row.appendChild(bubble);
    container.appendChild(row);
    scrollToBottom();

    row.querySelector('.bubble-content').style.display = 'block';
    return row;
  }

  // KaTeX als marked-Extension registrieren: Formeln werden beim Parsen gerendert,
  // damit marked die LaTeX-Syntax (_, \, *) nicht zerlegt. Einmalig, sobald KaTeX da ist.
  let _mathRegistered = false;
  function _ensureMathExtension() {
    if (_mathRegistered || typeof katex === 'undefined'
        || typeof marked === 'undefined' || typeof marked.use !== 'function') return;
    const render = (tex, display) => {
      try { return katex.renderToString(tex, { displayMode: display, throwOnError: false }); }
      catch (_) { return display ? `$$${tex}$$` : `$${tex}$`; }
    };
    const mk = (name, level, re, display, startToken) => ({
      name, level,
      start(src) { const i = src.indexOf(startToken); return i < 0 ? undefined : i; },
      tokenizer(src) { const m = re.exec(src); if (m) return { type: name, raw: m[0], text: m[1].trim() }; },
      renderer(t) { return render(t.text, display); },
    });
    marked.use({ extensions: [
      mk('mathBlockDollar', 'block', /^\$\$([\s\S]+?)\$\$/, true, '$$'),
      mk('mathBlockBracket', 'block', /^\\\[([\s\S]+?)\\\]/, true, '\\['),
      mk('mathInlineDollar', 'inline', /^\$([^\n$]+?)\$/, false, '$'),
      mk('mathInlineParen', 'inline', /^\\\(([^\n]+?)\\\)/, false, '\\('),
    ] });
    _mathRegistered = true;
  }
  // Auch für andere Module (Planer-Dossier, Recherche) verfügbar machen
  window._ensureKatexMarked = _ensureMathExtension;

  // Normen (DIN/EN/ISO/IEC/VDI/VDE/ASTM) und Gesetzes-Paragrafen erkennen
  const _CITE_RE = new RegExp(
    '((?:DIN(?:\\s+EN)?(?:\\s+ISO)?|EN(?:\\s+ISO)?|ISO|IEC|VDI|VDE|ASTM)\\s+\\d{1,6}(?:[-\\s]\\d{1,5})*(?::\\d{4})?)' +
    '|((?:§|Art\\.)\\s?\\d+[a-z]?(?:\\s?ff?\\.)?\\s+[A-ZÄÖÜ][A-Za-zÄÖÜäöü]{1,8})',
    'g'
  );

  function _citationHref(match) {
    const law = match.match(/^(§|Art\.)\s?(\d+[a-z]?)\s?(?:ff?\.)?\s+([A-ZÄÖÜ][A-Za-zÄÖÜäöü]{1,8})$/);
    if (law) {
      const file = law[1] === 'Art.' ? `art_${law[2].toLowerCase()}.html` : `__${law[2].toLowerCase()}.html`;
      return `https://www.gesetze-im-internet.de/${law[3].toLowerCase()}/${file}`;
    }
    return 'https://www.dinmedia.de/de/search?query=' + encodeURIComponent(match.trim());
  }

  // Erkannte Norm-/Gesetzesangaben in Links umwandeln (vorhandene Links/Code überspringen)
  function linkifyCitations(root) {
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, {
      acceptNode(node) {
        if (!node.nodeValue || !node.nodeValue.trim()) return NodeFilter.FILTER_REJECT;
        let p = node.parentElement;
        while (p && p !== root) {
          if (p.tagName === 'A' || p.tagName === 'CODE' || p.tagName === 'PRE') return NodeFilter.FILTER_REJECT;
          p = p.parentElement;
        }
        return NodeFilter.FILTER_ACCEPT;
      },
    });
    const targets = [];
    let n;
    while ((n = walker.nextNode())) {
      _CITE_RE.lastIndex = 0;
      if (_CITE_RE.test(n.nodeValue)) targets.push(n);
    }
    for (const node of targets) {
      const text = node.nodeValue;
      const frag = document.createDocumentFragment();
      let last = 0, m;
      _CITE_RE.lastIndex = 0;
      while ((m = _CITE_RE.exec(text))) {
        if (m.index > last) frag.appendChild(document.createTextNode(text.slice(last, m.index)));
        const a = document.createElement('a');
        a.href = _citationHref(m[0]);
        a.textContent = m[0];
        a.target = '_blank';
        a.rel = 'noopener noreferrer';
        frag.appendChild(a);
        last = m.index + m[0].length;
      }
      if (last < text.length) frag.appendChild(document.createTextNode(text.slice(last)));
      node.parentNode.replaceChild(frag, node);
    }
  }

  // ── Direkt-Download von Text-Dateien (MD/CSV) ──────────────────────────────
  function downloadTextFile(name, content, mime) {
    const blob = new Blob([content], { type: mime || 'text/plain;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url; a.download = name; a.click();
    URL.revokeObjectURL(url);
  }

  function _saveName(prefix, ext) {
    const d = new Date();
    const p = n => String(n).padStart(2, '0');
    return `${prefix}_${d.getFullYear()}${p(d.getMonth() + 1)}${p(d.getDate())}_${p(d.getHours())}${p(d.getMinutes())}.${ext}`;
  }

  // HTML-Tabelle → CSV (Excel-kompatibel: Semikolon-Trenner, Felder gequotet;
  // der Aufrufer stellt das UTF-8-BOM voran, damit Excel Umlaute erkennt)
  function tableToCsv(tbl) {
    const esc = v => {
      const s = (v || '').replace(/\s+/g, ' ').trim();
      return /[";\n]/.test(s) ? '"' + s.replace(/"/g, '""') + '"' : s;
    };
    return Array.from(tbl.rows)
      .map(r => Array.from(r.cells).map(c => esc(c.textContent)).join(';'))
      .join('\r\n');
  }

  function renderMarkdown(el, text) {
    el._rawMd = text;   // Roh-Markdown für „als .md speichern" aufheben
    if (typeof marked !== 'undefined') {
      _ensureMathExtension();
      el.innerHTML = marked.parse(text, { gfm: true, breaks: true });
      // Normen-/Gesetzesangaben automatisch verlinken (deterministisch)
      linkifyCitations(el);
      // Links grundsätzlich in neuem Fenster/Tab öffnen
      el.querySelectorAll('a[href]').forEach(a => {
        a.target = '_blank';
        a.rel = 'noopener noreferrer';
      });
      // Mermaid-Codeblöcke ersetzen bevor hljs sie verfärbt
      el.querySelectorAll('pre code.language-mermaid').forEach(block => {
        const def = block.textContent;
        const diagramEl = document.createElement('div');
        diagramEl.className = 'diagram-body';
        block.closest('pre').replaceWith(diagramEl);
        _renderMermaid(diagramEl, def);
      });
      // Code-Highlighting (alle anderen Sprachen)
      el.querySelectorAll('pre code').forEach(block => {
        if (typeof hljs !== 'undefined') hljs.highlightElement(block);
      });
      // Kopier-Button für Code-Blöcke
      el.querySelectorAll('pre').forEach(pre => {
        pre.style.position = 'relative';
        const codeEl = pre.querySelector('code');
        const codeText = () => codeEl?.textContent || pre.textContent;

        // „In Code-Tab" — Codeblock ins Code-Tab übernehmen und ausführen.
        // Sprache aus der highlight.js-Klasse (language-…) ableiten.
        if (codeEl && typeof CodeIDE !== 'undefined' && CodeIDE.loadFromChat) {
          const m = (codeEl.className || '').match(/language-([\w+-]+)/);
          const lang = m ? m[1].toLowerCase() : '';
          const ideBtn = document.createElement('button');
          ideBtn.textContent = '▶ Code-Tab';
          ideBtn.title = 'Code ins Code-Tab übernehmen und ausführen';
          ideBtn.style.cssText = 'position:absolute;top:8px;right:74px;padding:3px 8px;font-size:11px;background:#234;color:#9cf;border:1px solid #46a;border-radius:4px;cursor:pointer';
          ideBtn.addEventListener('click', () => CodeIDE.loadFromChat(codeText(), 'Chat-Code', lang));
          pre.appendChild(ideBtn);
        }

        const btn = document.createElement('button');
        btn.textContent = 'Kopieren';
        btn.style.cssText = 'position:absolute;top:8px;right:8px;padding:3px 8px;font-size:11px;background:#333;color:#aaa;border:1px solid #555;border-radius:4px;cursor:pointer';
        btn.addEventListener('click', () => {
          navigator.clipboard.writeText(codeText());
          btn.textContent = '✓';
          setTimeout(() => { btn.textContent = 'Kopieren'; }, 1500);
        });
        pre.appendChild(btn);
      });
      // Markdown-Tabellen: direkt als CSV speicherbar (Excel-kompatibel)
      el.querySelectorAll('table').forEach(tbl => {
        const bar = document.createElement('div');
        bar.className = 'table-csv-bar';
        const csvBtn = document.createElement('button');
        csvBtn.textContent = '⬇ CSV';
        csvBtn.title = 'Diese Tabelle als CSV-Datei speichern (Excel-kompatibel)';
        csvBtn.addEventListener('click', () => {
          downloadTextFile(_saveName('tabelle', 'csv'), '\uFEFF' + tableToCsv(tbl), 'text/csv;charset=utf-8');
          if (typeof showToast === 'function') showToast('✓ Tabelle als CSV gespeichert');
        });
        bar.appendChild(csvBtn);
        tbl.parentNode.insertBefore(bar, tbl);
      });
    } else {
      el.textContent = text;
    }
  }

  function scrollToBottom() {
    const container = document.getElementById('messages');
    container.scrollTop = container.scrollHeight;
  }

  function setBtnSendState(enabled) {
    const btn = document.getElementById('btn-send');
    // Während des Streamings bleibt der Button klickbar – ein Klick auf die
    // Sanduhr (⏳) bricht die laufende Antwort ab.
    btn.disabled = false;
    btn.textContent = enabled ? '↑' : '⏳';
    btn.title = enabled ? 'Senden (Enter)' : 'Antwort abbrechen';
    btn.classList.toggle('btn-send-busy', !enabled);
  }

  // ── Abbruch ────────────────────────────────────────────────────────────────
  function abortStreaming() {
    if (abortController) {
      try { abortController.abort(); } catch (_) {}
    }
  }

  // ── Denkprozess-Panel ───────────────────────────────────────────────────────
  function setThinkingPanelVisible(visible) {
    const panel = document.getElementById('thinking-panel');
    if (panel) panel.style.display = visible ? 'flex' : 'none';
    const split = document.getElementById('chat-splitter');
    if (split) split.style.display = visible ? 'block' : 'none';
  }

  // Ziehbarer Trenner zwischen Nachrichten und Denkprozess-Panel (Planer-Muster)
  const _CHAT_SPLIT_KEY = 'chat_thinking_w';
  function _initSplitter() {
    const splitter = document.getElementById('chat-splitter');
    const panel    = document.getElementById('thinking-panel');
    const main     = document.getElementById('chat-main');
    if (!splitter || !panel || !main) return;
    const saved = parseInt(localStorage.getItem(_CHAT_SPLIT_KEY) || '', 10);
    if (saved > 0) panel.style.width = saved + 'px';

    const _apply = (clientX) => {
      const rect = main.getBoundingClientRect();
      let w = rect.right - clientX;             // Abstand vom rechten Rand
      const max = rect.width - 320;             // Nachrichten mind. ~320px
      w = Math.max(220, Math.min(w, max));      // Panel mind. 220px
      panel.style.width = w + 'px';
    };
    const _onMove = (e) => _apply(e.clientX);
    const _onUp = () => {
      splitter.classList.remove('dragging');
      document.body.style.userSelect = '';
      document.removeEventListener('mousemove', _onMove);
      document.removeEventListener('mouseup', _onUp);
      localStorage.setItem(_CHAT_SPLIT_KEY, String(parseInt(panel.style.width, 10) || 0));
    };
    splitter.addEventListener('mousedown', (e) => {
      e.preventDefault();
      splitter.classList.add('dragging');
      document.body.style.userSelect = 'none';
      document.addEventListener('mousemove', _onMove);
      document.addEventListener('mouseup', _onUp);
    });
    splitter.addEventListener('dblclick', () => {
      panel.style.width = '';
      localStorage.removeItem(_CHAT_SPLIT_KEY);
    });
  }

  function resetThinking() {
    const el = document.getElementById('thinking-content');
    if (!el) return;
    el.textContent = '';
    el.classList.remove('thinking-empty');
    el.dataset.has = '';
  }

  function appendThinking(text) {
    if (!text) return;
    const el = document.getElementById('thinking-content');
    if (!el) return;
    el.classList.remove('thinking-empty');
    el.textContent += (el.dataset.has ? '\n\n' : '') + text;
    el.dataset.has = '1';
    el.scrollTop = el.scrollHeight;
    // Falls Antwort kommt, aber Panel zu war: Hinweis nicht nötig – Toggle steuert Sichtbarkeit
  }

  function toggleThinking() {
    showThinking = !showThinking;
    const btn = document.getElementById('btn-thinking-toggle');
    if (btn) btn.classList.toggle('active', showThinking);
    setThinkingPanelVisible(showThinking);
    try { localStorage.setItem('show_thinking', showThinking ? '1' : '0'); } catch (_) {}
  }

  function initThinking() {
    try { showThinking = localStorage.getItem('show_thinking') === '1'; } catch (_) {}
    const btn = document.getElementById('btn-thinking-toggle');
    if (btn) btn.classList.toggle('active', showThinking);
    _initSplitter();
    setThinkingPanelVisible(showThinking);
    const closeBtn = document.getElementById('btn-thinking-close');
    if (closeBtn) closeBtn.addEventListener('click', () => {
      // Schließen über ✕ deaktiviert auch den Toggle
      if (showThinking) toggleThinking();
    });
  }

  function showWelcome(show) {
    const welcome = document.getElementById('welcome');
    const messages = document.getElementById('messages');
    if (welcome) welcome.style.display = show ? 'flex' : 'none';
    if (messages && !show) {
      const existing = messages.querySelector('#welcome');
      if (existing) existing.style.display = 'none';
    }
  }

  // ── Konversationsliste ────────────────────────────────────────────────────

  async function loadConversationList() {
    try {
      const projectId = (typeof Projects !== 'undefined') ? Projects.getActive() : null;
      const url = projectId ? `/api/conversations?project_id=${encodeURIComponent(projectId)}` : '/api/conversations';
      const resp = await fetch(url);
      const convs = await resp.json();
      renderConversationList(convs);
      // Projekt-Selektor für aktuellen Chat aktualisieren
      _syncChatProjectSelect(convs);
    } catch (e) {}
  }

  function _syncChatProjectSelect(convs) {
    const sel = document.getElementById('chat-project-select');
    if (!sel || typeof Projects === 'undefined') return;
    const projects = Projects.getAll();
    const prev = sel.value;
    sel.innerHTML = '<option value="">— Kein Projekt —</option>';
    for (const p of projects) {
      const opt = document.createElement('option');
      opt.value = p.id;
      opt.textContent = p.number ? `[${p.number}] ${p.name}` : p.name;
      sel.appendChild(opt);
    }
    // Aktuellen Chat-Projektstatus herstellen
    if (currentConvId) {
      const conv = convs.find(c => c.id === currentConvId);
      sel.value = conv?.project_id || '';
    }
    if (prev && !sel.value) sel.value = prev;
  }

  function renderConversationList(convs) {
    const container = document.getElementById('conversations');
    container.innerHTML = '';

    if (convs.length === 0) {
      container.innerHTML = '<div style="padding:12px;color:var(--text-muted);font-size:13px">Noch kein Gespräch</div>';
      return;
    }

    // Datum-Gruppierung
    const today = new Date(); today.setHours(0,0,0,0);
    const yesterday = new Date(today); yesterday.setDate(yesterday.getDate()-1);
    const week = new Date(today); week.setDate(week.getDate()-7);

    let lastGroup = '';

    for (const conv of convs) {
      const d = new Date(conv.timestamp * 1000);
      let group = 'Älter';
      if (d >= today) group = 'Heute';
      else if (d >= yesterday) group = 'Gestern';
      else if (d >= week) group = 'Diese Woche';

      if (group !== lastGroup) {
        const label = document.createElement('div');
        label.className = 'conv-group-label';
        label.textContent = group;
        container.appendChild(label);
        lastGroup = group;
      }

      const item = document.createElement('div');
      item.className = 'conv-item' + (conv.id === currentConvId ? ' active' : '');
      item.dataset.convId = conv.id;
      item.innerHTML = `
        <span class="conv-title" title="Doppelklick zum Umbenennen">${escHtml(conv.title)}</span>
        <div class="conv-actions">
          <button class="btn-rename-conv" title="Umbenennen">✏️</button>
          <button class="btn-export-conv" title="Exportieren">⬇️</button>
          <button class="btn-compress-conv" title="Chat komprimieren">🗜</button>
          <button class="btn-toskill-conv" title="Als Skill speichern">⚡</button>
          <button class="btn-del-conv" title="Löschen">🗑</button>
        </div>
      `;

      item.addEventListener('click', async () => {
        document.querySelectorAll('.conv-item').forEach(el => el.classList.remove('active'));
        item.classList.add('active');
        await loadConversation(conv.id);
        switchTab('chat');
      });

      item.querySelector('.btn-del-conv').addEventListener('click', async (e) => {
        e.stopPropagation();
        await fetch(`/api/conversations/${conv.id}`, { method: 'DELETE' });
        if (conv.id === currentConvId) newConversation();
        loadConversationList();
      });

      item.querySelector('.btn-rename-conv').addEventListener('click', e => {
        e.stopPropagation();
        _inlineRename(item, conv.id, conv.title);
      });

      item.querySelector('.conv-title').addEventListener('dblclick', e => {
        e.stopPropagation();
        _inlineRename(item, conv.id, conv.title);
      });

      item.querySelector('.btn-export-conv').addEventListener('click', async e => {
        e.stopPropagation();
        const url = `/api/conversations/${conv.id}/export`;
        const a = document.createElement('a');
        a.href = url; a.download = ''; a.click();
      });

      item.querySelector('.btn-compress-conv').addEventListener('click', async (e) => {
        e.stopPropagation();
        await compressConversation(conv.id);
      });

      item.querySelector('.btn-toskill-conv').addEventListener('click', async (e) => {
        e.stopPropagation();
        await chatToSkill(conv.id);
      });

      container.appendChild(item);
    }
  }

  // ── Inline-Umbenennen ─────────────────────────────────────────────────────

  function _inlineRename(item, convId, currentTitle) {
    const titleSpan = item.querySelector('.conv-title');
    if (!titleSpan || titleSpan.querySelector('input')) return;
    const input = document.createElement('input');
    input.type = 'text';
    input.value = currentTitle;
    input.style.cssText = 'width:100%;background:var(--bg-input);border:1px solid var(--accent);border-radius:5px;color:var(--text);font-size:13px;padding:2px 6px;outline:none';
    titleSpan.innerHTML = '';
    titleSpan.appendChild(input);
    input.focus();
    input.select();

    const commit = async () => {
      const newTitle = input.value.trim();
      if (newTitle && newTitle !== currentTitle) {
        await fetch(`/api/conversations/${convId}/rename`, {
          method: 'PATCH',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ title: newTitle }),
        });
        showToast('Gespräch umbenannt');
      }
      loadConversationList();
    };

    input.addEventListener('keydown', e => {
      if (e.key === 'Enter')  { e.preventDefault(); commit(); }
      if (e.key === 'Escape') { loadConversationList(); }
    });
    input.addEventListener('blur', commit);
  }

  // ── Konversation importieren ────────────────────────────────────────────────

  async function importConversation(file) {
    try {
      const text = await file.text();
      const data = JSON.parse(text);
      const resp = await fetch('/api/conversations/import', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(data),
      });
      if (!resp.ok) throw new Error(await resp.text());
      await loadConversationList();
      showToast('Gespräch importiert');
    } catch (e) {
      showToast('Import fehlgeschlagen: ' + e.message);
    }
  }

  // ── Chat komprimieren ─────────────────────────────────────────────────────

  async function compressConversation(convId) {
    showToast('🗜 Komprimiere Chat…');
    try {
      const resp = await fetch(`/api/conversations/${convId}/compress`, { method: 'POST' });
      if (!resp.ok) throw new Error(await resp.text());
      const data = await resp.json();
      if (data.tokens && typeof TokenMeter !== 'undefined') TokenMeter.add(data.tokens, 'Chat');
      if (convId === currentConvId) {
        await loadConversation(convId);
      }
      showToast('Chat komprimiert');
    } catch (e) {
      showToast('Fehler beim Komprimieren');
      console.error(e);
    }
  }

  // ── Automatische Komprimierung (Überlauf-Sensor + Leerlauf-Timer) ──────────

  let _idleTimer = null;
  let _autoBusy = false;

  function _autoSettings() {
    const p = (typeof Profile !== 'undefined' && Profile.get) ? Profile.get() : {};
    return {
      on: !!p.auto_compress,
      overflow: p.compress_overflow_chars || 12000,
      idleMs: (p.compress_idle_min || 10) * 60000,
    };
  }

  function _convChars() {
    return messages.filter(m => m.role !== 'system')
      .reduce((n, m) => n + ((m.content || '').length), 0);
  }

  async function _autoCompress(reason) {
    const s = _autoSettings();
    if (!s.on || _autoBusy || isStreaming || !currentConvId) return;
    // Leerlauf löst schon etwas früher aus als der harte Überlauf
    const threshold = reason === 'idle' ? s.overflow * 0.6 : s.overflow;
    if (_convChars() < threshold) return;
    if (messages.filter(m => m.role !== 'system').length < 6) return;  // lohnt sich erst ab ein paar Austauschen
    _autoBusy = true;
    const before = messages.length;
    try {
      const resp = await fetch(`/api/conversations/${currentConvId}/compress`, { method: 'POST' });
      if (!resp.ok) throw new Error(await resp.text());
      const data = await resp.json();
      if (data.tokens && typeof TokenMeter !== 'undefined') TokenMeter.add(data.tokens, 'Chat');
      const after = (data.messages || []).length;
      await loadConversation(currentConvId);
      showToast(`💾 Verlauf automatisch komprimiert (${before} → ${after} Nachrichten)`);
    } catch (e) {
      console.error('Auto-Komprimierung fehlgeschlagen:', e);
    } finally {
      _autoBusy = false;
    }
  }

  function resetIdleTimer() {
    if (_idleTimer) clearTimeout(_idleTimer);
    if (!_autoSettings().on) return;
    _idleTimer = setTimeout(() => _autoCompress('idle'), _autoSettings().idleMs);
  }

  // Nutzeraktivität setzt den Leerlauf-Timer zurück
  ['keydown', 'mousedown', 'touchstart'].forEach(ev =>
    document.addEventListener(ev, resetIdleTimer, { passive: true }));

  // ── Chat zu Skill ──────────────────────────────────────────────────────────

  async function chatToSkill(convId) {
    showToast('⚡ Analysiere Chat…');
    try {
      const resp = await fetch(`/api/conversations/${convId}/to-skill`, { method: 'POST' });
      if (!resp.ok) throw new Error(await resp.text());
      const skillData = await resp.json();
      if (skillData.tokens && typeof TokenMeter !== 'undefined') TokenMeter.add(skillData.tokens, 'Chat');
      delete skillData.tokens;   // nicht in den Agenten-Datensatz übernehmen
      // Agent-Modal mit generierten Daten öffnen (id=null → neuer Agent)
      AgentManager.openModal({ ...skillData, id: null });
      switchTab('agents');
    } catch (e) {
      showToast('Fehler bei der Skill-Erstellung');
      console.error(e);
    }
  }

  // ── Deepdive (/dd, /ddd) ─────────────────────────────────────────────────────
  // Erkennt einen Deepdive-Befehl am Zeilenanfang. „/dd10" und „/deepdive10" =
  // Chat-Vertiefung, „/ddd10" und „/deepdivedocument10" = Dokument. X optional
  // (Default 5), Rest hinter dem Befehl gilt als Thema, falls keine Vorantwort da ist.
  function _parseDeepDive(text) {
    const m = text.match(/^\/(deepdivedocument|deepdive|ddd|dd)\s*(\d+)?\b\s*([\s\S]*)$/i);
    if (!m) return null;
    const tok = m[1].toLowerCase();
    const asDocument = (tok === 'deepdivedocument' || tok === 'ddd');
    const count = m[2] ? Math.max(1, Math.min(parseInt(m[2], 10), 20)) : 5;
    return { asDocument, count, extra: (m[3] || '').trim() };
  }

  function _handleDeepDiveEvent(ev, ctx) {
    const { asDocument, statusContent, chapterEls, docParts } = ctx;
    if (ev.type === 'dd_questions') {
      const qs = ev.questions || [];
      statusContent.innerHTML = '<strong>🔎 Vertiefungsfragen:</strong><ol style="margin:6px 0 0 18px">'
        + qs.map(q => `<li>${escHtml(q)}</li>`).join('') + '</ol>';
      scrollToBottom();
    } else if (ev.type === 'dd_chapter_start') {
      if (!asDocument) {
        const row = appendMessage('assistant', '', [], true);
        const content = row.querySelector('.bubble-content');
        content.innerHTML = `<div style="font-weight:600;margin-bottom:6px">${ev.index + 1}. ${escHtml(ev.question)}</div><em>⏳ recherchiert…</em>`;
        chapterEls[ev.index] = content;
      }
    } else if (ev.type === 'dd_chapter_done') {
      if (asDocument) {
        docParts.push(`\n## ${ev.index + 1}. ${ev.question}\n\n${ev.answer || ''}\n`);
        statusContent.innerHTML = `<strong>📕 Deepdive-Dokument…</strong> Kapitel ${ev.index + 1} fertig`;
      } else {
        const content = chapterEls[ev.index];
        if (content) {
          content.innerHTML = `<div style="font-weight:600;margin-bottom:6px">${ev.index + 1}. ${escHtml(ev.question)}</div>`;
          const ans = document.createElement('div');
          content.appendChild(ans);
          renderMarkdown(ans, ev.answer || '');
        }
        messages.push({ role: 'assistant', content: `**${ev.question}**\n\n${ev.answer || ''}` });
      }
      scrollToBottom();
    } else if (ev.type === 'done') {
      if (ev.tokens && typeof TokenMeter !== 'undefined') TokenMeter.add(ev.tokens, 'Deepdive');
    } else if (ev.type === 'error') {
      showToast('Deepdive: ' + (ev.message || 'Fehler'));
    }
  }

  async function runDeepDive(count, asDocument, extra) {
    if (isStreaming) return;
    const rev = [...messages].reverse();
    const lastAnswer = (rev.find(m => m.role === 'assistant') || {}).content || '';
    const lastUser = (rev.find(m => m.role === 'user') || {}).content || '';
    if (!lastAnswer && !extra) {
      showToast('Deepdive braucht eine vorherige Antwort (oder ein Thema nach dem Befehl).');
      return;
    }
    showWelcome(false);
    isStreaming = true;
    setBtnSendState(false);

    const model = (typeof Profile !== 'undefined' ? Profile.modelFor('general') : '') || undefined;
    const useSearch = document.getElementById('btn-search-toggle').classList.contains('active');
    const ragCollections = (typeof RAG !== 'undefined') ? RAG.selectedCollections() : [];

    appendMessage('user', asDocument
      ? `📕 /ddd${count} — Deepdive-Dokument: ${count} Kapitel zur letzten Antwort`
      : `🔎 /dd${count} — Deepdive: ${count} Fragen zur letzten Antwort`);

    const statusRow = appendMessage('assistant', '', [], true);
    const statusContent = statusRow.querySelector('.bubble-content');
    statusContent.innerHTML = '<em>⏳ Vertiefungsfragen werden erzeugt…</em>';

    const title = (lastUser || extra || 'Deepdive').split('\n')[0].slice(0, 80);
    const docParts = asDocument
      ? [`# ${title}\n\n## Vorwort\n\n${lastAnswer || extra}\n`]
      : null;
    const chapterEls = {};

    abortController = new AbortController();
    try {
      const resp = await fetch('/api/deepdive', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        signal: abortController.signal,
        body: JSON.stringify({
          last_answer: lastAnswer || extra,
          topic: lastUser || extra || '',
          count,
          model,
          as_document: asDocument,
          web_search: useSearch,
          rag_collections: ragCollections,
        }),
      });
      const reader = resp.body.getReader();
      const decoder = new TextDecoder();
      let buffer = '';
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split('\n');
        buffer = lines.pop();
        for (const line of lines) {
          if (!line.startsWith('data: ')) continue;
          let ev;
          try { ev = JSON.parse(line.slice(6)); } catch (_) { continue; }
          _handleDeepDiveEvent(ev, { asDocument, statusContent, chapterEls, docParts });
        }
      }
    } catch (e) {
      if (e.name !== 'AbortError') showToast('Deepdive-Fehler: ' + e.message);
    } finally {
      isStreaming = false;
      setBtnSendState(true);
    }

    if (asDocument && docParts && docParts.length > 1) {
      const md = docParts.join('\n');
      if (typeof DocGen !== 'undefined' && DocGen.showResult) {
        DocGen.showResult(md);
        switchTab('docgen');
        showToast('✓ Deepdive-Dokument im Dokumente-Tab — als DOCX/PDF exportierbar');
      }
    }
  }

  // ── /such — Erweiterte Suche mit alternativen Suchbegriffen ──────────────────
  // Erkennt „/such", „/suche", „/finde", „/search" am Anfang und liefert den
  // Restbegriff. Die KI erzeugt daraus Synonyme/alternative Begriffe und sucht.
  function _parseSearch(text) {
    const m = text.match(/^\/(such|suche|finde|search)\b\s*([\s\S]*)$/i);
    if (!m) return null;
    return { query: (m[2] || '').trim() };
  }

  function _renderSearchChips(container, terms) {
    container.innerHTML = '';
    if (!terms || !terms.length) return;
    const lbl = document.createElement('div');
    lbl.className = 'search-chips-label';
    lbl.textContent = 'Alternative Suchbegriffe (Klick = einzeln im Web suchen):';
    container.appendChild(lbl);
    for (const t of terms) {
      const chip = document.createElement('button');
      chip.className = 'search-chip';
      chip.type = 'button';
      chip.textContent = t;
      chip.title = `Mit „${t}" einzeln im Web suchen`;
      chip.addEventListener('click', () => _searchWithTerm(t));
      container.appendChild(chip);
    }
  }

  // Klick auf einen Chip: Begriff ins Eingabefeld, Websuche aktivieren, normal senden.
  function _searchWithTerm(term) {
    if (isStreaming) { showToast('Bitte warten, bis die laufende Antwort fertig ist'); return; }
    const input = document.getElementById('message-input');
    input.value = term;
    const stb = document.getElementById('btn-search-toggle');
    if (stb && !stb.classList.contains('active')) stb.classList.add('active');
    sendMessage();
  }

  function _renderSearchSources(container, sources) {
    if (!sources || !sources.length) return;
    let box = container.querySelector('.search-sources');
    if (!box) { box = document.createElement('div'); box.className = 'search-sources'; container.appendChild(box); }
    let html = '<div class="search-sources-title">📎 Quellen</div><ol>';
    for (const s of sources.slice(0, 12)) {
      const u = escHtml(s.url || '');
      const t = escHtml(s.title || s.url || '(ohne Titel)');
      html += `<li><a href="${u}" target="_blank" rel="noopener noreferrer">${t}</a></li>`;
    }
    html += '</ol>';
    box.innerHTML = html;
  }

  async function runSearch(query) {
    query = (query || '').trim();
    if (!query) { showToast('Bitte nach „/such" einen Suchbegriff eingeben'); return; }
    if (isStreaming) return;
    showWelcome(false);
    isStreaming = true;
    setBtnSendState(false);

    appendMessage('user', '🔎 /such ' + query);
    if (!currentConvId) currentConvId = `conv_${Date.now()}`;

    const row = appendMessage('assistant', '', [], true);
    const content = row.querySelector('.bubble-content');
    const chips = document.createElement('div');
    chips.className = 'search-chips';
    content.appendChild(chips);
    const textEl = document.createElement('div');
    textEl.className = 'bubble-text';
    textEl.innerHTML = '<em>⏳ alternative Suchbegriffe werden erzeugt…</em>';
    content.appendChild(textEl);

    const model = (typeof Profile !== 'undefined' ? Profile.modelFor('general') : '') || undefined;
    let answer = '';
    abortController = new AbortController();
    try {
      const resp = await fetch('/api/search/expand', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        signal: abortController.signal,
        body: JSON.stringify({ query, model }),
      });
      const reader = resp.body.getReader();
      const dec = new TextDecoder();
      let buf = '';
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buf += dec.decode(value, { stream: true });
        const lines = buf.split('\n');
        buf = lines.pop();
        for (const line of lines) {
          if (!line.startsWith('data: ')) continue;
          let ev;
          try { ev = JSON.parse(line.slice(6)); } catch (_) { continue; }
          if (ev.type === 'terms') {
            _renderSearchChips(chips, ev.terms || []);
          } else if (ev.type === 'searching') {
            textEl.innerHTML = '<em>🔎 Websuche läuft…</em>';
          } else if (ev.type === 'synthesizing') {
            textEl.innerHTML = '<em>⏳ Ergebnisse werden zusammengefasst…</em>';
            answer = '';
          } else if (ev.type === 'text') {
            answer += ev.content;
            textEl.textContent = answer;
            scrollToBottom();
          } else if (ev.type === 'sources') {
            _renderSearchSources(content, ev.data || []);
          } else if (ev.type === 'error') {
            textEl.innerHTML = `<em style="color:#ef4444">Fehler: ${escHtml(ev.message || '')}</em>`;
          } else if (ev.type === 'done') {
            if (ev.tokens && typeof TokenMeter !== 'undefined') TokenMeter.add(ev.tokens, 'Suche');
          }
        }
      }
      // Abschluss: Markdown rendern, ins Verlaufsprotokoll übernehmen
      if (answer && typeof marked !== 'undefined') {
        if (window._ensureKatexMarked) window._ensureKatexMarked();
        textEl.innerHTML = marked.parse(answer, { gfm: true, breaks: true });
        textEl.querySelectorAll('a[href]').forEach(a => { a.target = '_blank'; a.rel = 'noopener noreferrer'; });
      }
      if (answer) {
        messages.push({ role: 'user', content: '/such ' + query });
        messages.push({ role: 'assistant', content: answer });
        loadConversationList();
      }
    } catch (e) {
      if (e.name !== 'AbortError') textEl.innerHTML = `<em style="color:#ef4444">Suche fehlgeschlagen: ${escHtml(e.message)}</em>`;
    } finally {
      abortController = null;
      isStreaming = false;
      setBtnSendState(true);
    }
  }

  // ── Tiefe Recherche (gesteuert: Tiefe + Umfang, mit Auto-Angebot) ───────────
  let _bypassResearchOffer = false;

  function _parseDeepResearch(text) {
    const m = text.match(/^\/(recherche|deepresearch|deep|tief)\b\s*([\s\S]*)$/i);
    if (!m) return null;
    return { topic: (m[2] || '').trim() };
  }

  let _bypassCompareOffer = false;
  function _looksLikeTableFile(f) { return /\.(xlsx|xls|csv)$/i.test((f && f.filename) || ''); }
  function _compareOfferEnabled() {
    try {
      const p = (typeof Profile !== 'undefined' && Profile.get) ? (Profile.get() || {}) : {};
      return p.excel_compare_offer !== false;
    } catch (_) { return true; }
  }

  function _researchOfferEnabled() {
    try {
      const p = (typeof Profile !== 'undefined' && Profile.get) ? (Profile.get() || {}) : {};
      if (p.deep_research_offer === false) return false;
      // Im Hartman-/Ausbildungsmodus ist Web gesperrt → kein Angebot
      if (String(p.tone || '').toLowerCase() === 'hartman') return false;
      return true;
    } catch (_) { return false; }
  }

  // Heuristik: sieht die Nachricht nach einer breiten Recherche-/Fakten-/Kauffrage aus?
  const _RESEARCH_RE = /(alles über|recherchier|ausführlich|informationen (über|zu)|erzähl.*über|was wei(ß|ss)t du über|vergleich|kaufberatung|welche.*gibt es|worauf.*achten|suche (einen|eine|ein|nach)|finde (einen|eine|ein)|gebrauchte|gebrauchten|test(bericht)?|erfahrungen mit)/i;
  function _looksLikeResearch(text) {
    const t = (text || '').trim();
    if (t.length < 12 || t.startsWith('/')) return false;
    const words = t.split(/\s+/).length;
    if (words < 3) return false;
    return _RESEARCH_RE.test(t);
  }

  // Dezentes Angebot statt sofortiger Normalantwort
  function _offerResearch(topic) {
    showWelcome(false);
    const row = appendMessage('assistant', '', [], true);
    const c = row.querySelector('.bubble-content');
    const box = document.createElement('div');
    box.className = 'research-offer';
    box.innerHTML = '🔬 Möchtest du dazu eine <b>ausführliche Recherche</b> mit mehreren Quellen '
      + '(Tiefe &amp; Umfang wählbar)? '
      + '<button class="research-offer-yes">Tiefe &amp; Umfang wählen</button> '
      + '<button class="research-offer-no">Normal antworten</button>';
    c.appendChild(box);
    box.querySelector('.research-offer-yes').addEventListener('click', () => { row.remove(); _openResearchForm(topic); });
    box.querySelector('.research-offer-no').addEventListener('click', () => {
      row.remove();
      const inp = document.getElementById('message-input');
      inp.value = topic;
      _bypassResearchOffer = true;
      sendMessage();
    });
    scrollToBottom();
  }

  // Rückfrage-Formular: Tiefe (Aspekte) + Umfang (Wörter) + optionaler Fokus
  const _RES_DEPTH = [{ label: 'kurz', n: 4 }, { label: 'mittel', n: 8 }, { label: 'tief', n: 12 }];
  const _RES_WORDS = [{ label: 'kompakt (~400)', n: 400 }, { label: 'ausführlich (~1000)', n: 1000 }, { label: 'umfassend (~2500)', n: 2500 }];
  let _resFormWired = false;
  function _resChips(host, items, defIdx) {
    host.innerHTML = '';
    items.forEach((it, i) => {
      const b = document.createElement('button');
      b.type = 'button'; b.className = 'bild-chip'; b.textContent = it.label; b.dataset.n = it.n;
      if (i === defIdx) b.classList.add('active');
      b.addEventListener('click', () => { host.querySelectorAll('.bild-chip').forEach(x => x.classList.remove('active')); b.classList.add('active'); });
      host.appendChild(b);
    });
  }
  function _resSelected(host) { const a = host.querySelector('.bild-chip.active'); return a ? +a.dataset.n : 0; }

  function _openResearchForm(topic) {
    const ov = document.getElementById('research-ask');
    if (!ov) { runDeepResearch(topic, { depth: 8, words: 1000 }); return; }
    _resChips(document.getElementById('res-depth'), _RES_DEPTH, 1);
    _resChips(document.getElementById('res-words'), _RES_WORDS, 1);
    const topicEl = document.getElementById('res-topic');
    if (topicEl) topicEl.textContent = topic;
    document.getElementById('res-focus').value = '';
    if (!_resFormWired) {
      _resFormWired = true;
      const close = () => { ov.style.display = 'none'; };
      document.getElementById('res-close').addEventListener('click', close);
      document.getElementById('res-cancel').addEventListener('click', close);
      ov.addEventListener('click', e => { if (e.target === ov) close(); });
      document.addEventListener('keydown', e => { if (e.key === 'Escape' && ov.style.display !== 'none') close(); });
      document.getElementById('res-go').addEventListener('click', () => {
        const depth = _resSelected(document.getElementById('res-depth')) || 8;
        const words = _resSelected(document.getElementById('res-words')) || 1000;
        const focus = document.getElementById('res-focus').value.trim();
        const t = (document.getElementById('res-topic')?.textContent || '').trim();
        close();
        runDeepResearch(t, { depth, words, focus });
      });
    }
    ov.style.display = 'flex';
  }

  async function runDeepResearch(topic, opts) {
    topic = (topic || '').trim();
    opts = opts || {};
    if (!topic) { showToast('Kein Thema für die Recherche'); return; }
    if (isStreaming) { showToast('Bitte warten, bis die laufende Antwort fertig ist'); return; }
    showWelcome(false);
    isStreaming = true;
    setBtnSendState(false);

    appendMessage('user', '🔬 Recherche: ' + topic);
    if (!currentConvId) currentConvId = `conv_${Date.now()}`;

    const row = appendMessage('assistant', '', [], true);
    const content = row.querySelector('.bubble-content');
    const logEl = document.createElement('div'); logEl.className = 'research-log'; content.appendChild(logEl);
    const workingEl = makeWorking('recherchiert'); content.appendChild(workingEl);
    const textEl = document.createElement('div'); textEl.className = 'bubble-text'; content.appendChild(textEl);
    const _log = (t) => { const d = document.createElement('div'); d.className = 'research-log-line'; d.textContent = t; logEl.appendChild(d); scrollToBottom(); };

    const model = (typeof Profile !== 'undefined' ? Profile.modelFor('general') : '') || undefined;
    let answer = '', workingCleared = false;
    const clearWorking = () => { if (!workingCleared) { workingCleared = true; workingEl.remove(); } };
    abortController = new AbortController();
    try {
      const resp = await fetch('/api/deepresearch', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        signal: abortController.signal,
        body: JSON.stringify({ topic, depth: opts.depth || 8, words: opts.words || 1000, focus: opts.focus || '', model }),
      });
      const reader = resp.body.getReader(); const dec = new TextDecoder(); let buf = '';
      while (true) {
        const { done, value } = await reader.read(); if (done) break;
        buf += dec.decode(value, { stream: true });
        const lines = buf.split('\n'); buf = lines.pop();
        for (const line of lines) {
          if (!line.startsWith('data: ')) continue;
          let ev; try { ev = JSON.parse(line.slice(6)); } catch (_) { continue; }
          if (ev.type === 'aspects') {
            _log('🧭 ' + (ev.aspects || []).length + ' Aspekte: ' + (ev.aspects || []).join(' · '));
          } else if (ev.type === 'search_done') {
            _log('🔎 recherchiert: ' + ev.aspect);
          } else if (ev.type === 'notice') {
            _log('ℹ ' + (ev.message || ''));
          } else if (ev.type === 'synthesizing') {
            _log('📝 Bericht wird geschrieben…');
          } else if (ev.type === 'text') {
            clearWorking();
            answer += ev.content; textEl.textContent = answer; scrollToBottom();
          } else if (ev.type === 'sources') {
            const flat = [];
            for (const grp of (ev.data || [])) for (const s of (grp.sources || [])) flat.push(s);
            _renderSearchSources(content, flat);
          } else if (ev.type === 'done') {
            if (ev.tokens && typeof TokenMeter !== 'undefined') TokenMeter.add(ev.tokens, 'Tiefe Recherche');
          } else if (ev.type === 'error') {
            clearWorking();
            textEl.innerHTML = `<em style="color:#ef4444">Recherche fehlgeschlagen: ${escHtml(ev.message || '')}</em>`;
          }
        }
      }
      if (answer && typeof marked !== 'undefined') {
        if (window._ensureKatexMarked) window._ensureKatexMarked();
        textEl.innerHTML = marked.parse(answer, { gfm: true, breaks: true });
        textEl.querySelectorAll('a[href]').forEach(a => { a.target = '_blank'; a.rel = 'noopener noreferrer'; });
      }
      if (answer) {
        messages.push({ role: 'user', content: '🔬 Recherche: ' + topic });
        messages.push({ role: 'assistant', content: answer });
        loadConversationList();
      }
    } catch (e) {
      if (e.name !== 'AbortError') textEl.innerHTML = `<em style="color:#ef4444">Recherche fehlgeschlagen: ${escHtml(e.message)}</em>`;
    } finally {
      clearWorking();
      abortController = null;
      isStreaming = false;
      setBtnSendState(true);
    }
  }

  // ── /workflow — mehrstufiger Arbeitsablauf im Chat ──────────────────────────
  // „/workflow" (Aliase /ablauf, /flow) + nummerierte Schritte. Die Schritte
  // werden nacheinander ausgeführt, Zwischenergebnisse gespeichert und als
  // Kontext an den nächsten Schritt gegeben; am Ende ein Gesamtergebnis mit
  // Übergabe-Buttons (→ Präsentation / → Planer).
  // Ein Schritt darf mit einer Tag-Angabe beginnen: [lokal] / [api] / [web] bzw.
  // Kombis wie [lokal,web]. → { text, mode, web } (mode '' | 'local' | 'api').
  function _wfParseTags(s) {
    let mode = '', web = false, kind = '', text = String(s || '').trim();
    const m = text.match(/^\s*\[([^\]]{1,40})\]\s*([\s\S]*)$/);
    if (m) {
      text = (m[2] || '').trim();
      for (const t of m[1].toLowerCase().split(/[,\s/+]+/).filter(Boolean)) {
        if (t === 'lokal' || t === 'local') mode = 'local';
        else if (t === 'api' || t === 'remote' || t === 'cloud') mode = 'api';
        else if (['web', 'recherche', 'suche', 'search', 'internet'].includes(t)) web = true;
        else if (['bild', 'image', 'img', 'foto'].includes(t)) kind = 'image';
        else if (['sprache', 'stimme', 'tts', 'voice', 'audio', 'vorlesen'].includes(t)) kind = 'voice';
      }
    }
    // Ohne Tag: Bild-/Sprach-Absicht aus dem Schritttext ableiten.
    if (!kind) {
      if (/\b(erzeuge|erstelle|generiere|male|zeichne|entwirf|visualisiere|rendere|render|generate|create|draw)\b[^.\n]{0,40}\b(bild|bilder|foto|photo|illustration|grafik|grafiken|zeichnung|logo|poster|cover|image|picture)\b/i.test(text)) kind = 'image';
      else if (/\b(sprachnachricht|sprachausgabe|vertone|vorlesen|lies\b[^.\n]{0,30}\bvor|als\s+(sprache|audio)|voice[- ]?message|read\s+aloud|text[- ]?to[- ]?speech|tts)\b/i.test(text)) kind = 'voice';
    }
    return { text, mode, web, kind };
  }

  // Kurzes Label für die Anzeige eines Schritt-Tags.
  function _wfBadge(st) {
    const bits = [];
    if (st.kind === 'image') bits.push('🖼 Bild');
    else if (st.kind === 'voice') bits.push('🔊 Sprache');
    if (st.mode === 'local') bits.push('💻 lokal');
    else if (st.mode === 'api') bits.push('☁ API');
    if (st.web) bits.push('🌐 Web');
    return bits.length ? ' [' + bits.join(' · ') + ']' : '';
  }

  // Ein Remote-Modell für [api]-Schritte finden: bevorzugt aus den Profil-Rollen,
  // sonst das erste Remote-Modell der konfigurierten Anbieter.
  async function _wfApiModel() {
    const p = (typeof Profile !== 'undefined' && Profile.get) ? (Profile.get() || {}) : {};
    for (const k of ['model_science', 'model_general', 'model_coding', 'model_medical']) {
      const v = String(p[k] || '').trim();
      if (v.indexOf('::') > 0) return v;
    }
    try {
      const data = await (await fetch('/api/models')).json();
      const r = (data.models || []).find(m => m.remote);
      if (r) return r.name;
    } catch (_) {}
    return '';
  }

  function _parseWorkflow(text) {
    const m = text.match(/^\/(workflow|ablauf|flow)\b\s*([\s\S]*)$/i);
    if (!m) return null;
    const body = (m[2] || '').trim();
    const raw = [];
    let goal = '';
    // Nummerierte Marker „1." „2)" — inline ODER zeilenweise.
    const re = /(?:^|\n|\s)(\d{1,2})[.)]\s+/g;
    const marks = [];
    let mm;
    while ((mm = re.exec(body))) marks.push({ start: mm.index, contentStart: mm.index + mm[0].length });
    if (marks.length) {
      goal = body.slice(0, marks[0].start).trim();
      for (let i = 0; i < marks.length; i++) {
        const end = i + 1 < marks.length ? marks[i + 1].start : body.length;
        const s = body.slice(marks[i].contentStart, end).trim();
        if (s) raw.push(s);
      }
    } else {
      // Rückfall: jede nicht-leere Zeile = ein Schritt.
      body.split('\n').map(x => x.trim()).filter(Boolean).forEach(x => raw.push(x));
    }
    const steps = raw.slice(0, 20).map(_wfParseTags).filter(s => s.text);
    return { steps, goal };
  }

  async function runWorkflow(steps, goal) {
    // Schritte sind Objekte { text, mode, web }; nackte Strings tolerieren.
    steps = (steps || []).map(s => (typeof s === 'string' ? _wfParseTags(s) : s))
                         .filter(s => s && s.text);
    goal = (goal || '').trim();
    if (!steps.length) { showToast('Keine Schritte angegeben'); return; }
    if (isStreaming) { showToast('Bitte warten, bis die laufende Antwort fertig ist'); return; }
    showWelcome(false);
    isStreaming = true;
    setBtnSendState(false);

    const head = '🔧 Arbeitsablauf' + (goal ? ': ' + goal : '') + ` (${steps.length} Schritte)`;
    appendMessage('user', head + '\n' + steps.map((s, i) => `${i + 1}. ${s.text}${_wfBadge(s)}`).join('\n'));
    if (!currentConvId) currentConvId = `conv_${Date.now()}`;

    const row = appendMessage('assistant', '', [], true);
    const content = row.querySelector('.bubble-content');
    const logEl = document.createElement('div'); logEl.className = 'research-log'; content.appendChild(logEl);
    const workingEl = makeWorking('arbeitet Schritte ab'); content.appendChild(workingEl);
    const stepsEl = document.createElement('div'); content.appendChild(stepsEl);
    const textEl = document.createElement('div'); textEl.className = 'bubble-text'; content.appendChild(textEl);
    const _log = (t) => { const d = document.createElement('div'); d.className = 'research-log-line'; d.textContent = t; logEl.appendChild(d); scrollToBottom(); };

    const model = (typeof Profile !== 'undefined' ? Profile.modelFor('general') : '') || undefined;
    // API-Modell für [api]-Schritte (nur nötig, wenn ein Schritt es anfordert).
    const apiModel = steps.some(s => s.mode === 'api') ? await _wfApiModel() : '';
    const stepResults = [];
    const _stepModels = {};
    let answer = '', workingCleared = false;
    const clearWorking = () => { if (!workingCleared) { workingCleared = true; workingEl.remove(); } };
    abortController = new AbortController();
    try {
      const resp = await fetch('/api/workflow', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        signal: abortController.signal,
        body: JSON.stringify({ steps, goal, model, api_model: apiModel }),
      });
      const reader = resp.body.getReader(); const dec = new TextDecoder(); let buf = '';
      while (true) {
        const { done, value } = await reader.read(); if (done) break;
        buf += dec.decode(value, { stream: true });
        const lines = buf.split('\n'); buf = lines.pop();
        for (const line of lines) {
          if (!line.startsWith('data: ')) continue;
          let ev; try { ev = JSON.parse(line.slice(6)); } catch (_) { continue; }
          if (ev.type === 'workflow_start') {
            _log('🧭 ' + ev.count + ' Schritte');
          } else if (ev.type === 'step_start') {
            const _mdl = ev.remote ? '☁ ' + String(ev.model || '').split('::').slice(1).join('::')
                       : (ev.model ? '💻 ' + ev.model : '');
            const _tag = (_mdl ? ' · ' + _mdl : '') + (ev.web ? ' · 🌐 Web' : '');
            _log(`▶ Schritt ${ev.index + 1}/${ev.total}: ${(ev.step || '').slice(0, 70)}${_tag}`);
            _stepModels[ev.index] = { model: ev.model, remote: ev.remote, web: ev.web };
          } else if (ev.type === 'searching') {
            _log(`  🔎 Websuche: ${(ev.query || '').slice(0, 70)}…`);
          } else if (ev.type === 'search_done') {
            _log(`  ✓ ${ev.count || 0} Quellen gefunden`);
          } else if (ev.type === 'generating_image') {
            _log(`  🖼 Bild wird erzeugt: ${(ev.prompt || '').slice(0, 70)}…`);
          } else if (ev.type === 'image') {
            clearWorking();
            const wrap = document.createElement('div'); wrap.className = 'wf-image';
            wrap.style.cssText = 'margin:10px 0';
            if (ev.prompt) {
              const cap = document.createElement('div');
              cap.style.cssText = 'font-size:.85em;opacity:.7;margin-bottom:4px';
              cap.textContent = '🖼 ' + ev.prompt;
              wrap.appendChild(cap);
            }
            insertImage(wrap, ev.image);
            stepsEl.appendChild(wrap); scrollToBottom();
          } else if (ev.type === 'speak') {
            clearWorking();
            const tone = (typeof Profile !== 'undefined' && Profile.get) ? (Profile.get().tone || '') : '';
            if (typeof TTS !== 'undefined' && TTS.speak) TTS.speak(ev.text || '', tone);
            const b = document.createElement('button'); b.className = 'wf-action-btn';
            b.textContent = '🔊 nochmal vorlesen';
            b.onclick = () => { if (typeof TTS !== 'undefined' && TTS.speak) TTS.speak(ev.text || '', tone); };
            const bwrap = document.createElement('div'); bwrap.style.cssText = 'margin:6px 0';
            bwrap.appendChild(b); stepsEl.appendChild(bwrap); scrollToBottom();
          } else if (ev.type === 'notice') {
            const d = document.createElement('div'); d.className = 'research-log-line';
            d.style.opacity = '.7'; d.textContent = '  ℹ ' + (ev.message || ''); logEl.appendChild(d); scrollToBottom();
          } else if (ev.type === 'step_done') {
            stepResults.push({ step: ev.step, result: ev.result });
            const _sm = _stepModels[ev.index] || {};
            const _badge = _sm.remote ? '  ☁' : (_sm.web ? '  🌐' : '');
            const det = document.createElement('details'); det.className = 'wf-step';
            const sum = document.createElement('summary');
            sum.textContent = `✓ Schritt ${ev.index + 1}: ${(ev.step || '').slice(0, 70)}${_badge}`;
            det.appendChild(sum);
            const bd = document.createElement('div'); bd.className = 'wf-step-body';
            renderMarkdown(bd, ev.result || '');
            det.appendChild(bd); stepsEl.appendChild(det); scrollToBottom();
          } else if (ev.type === 'synthesizing') {
            const _mdl = ev.remote ? '☁ ' + String(ev.model || '').split('::').slice(1).join('::') : '';
            _log('📝 Gesamtergebnis wird zusammengeführt…' + (_mdl ? ' (' + _mdl + ')' : ''));
          } else if (ev.type === 'text') {
            clearWorking();
            answer += ev.content; textEl.textContent = answer; scrollToBottom();
          } else if (ev.type === 'done') {
            if (ev.tokens && typeof TokenMeter !== 'undefined') TokenMeter.add(ev.tokens, 'Arbeitsablauf');
          } else if (ev.type === 'error') {
            clearWorking();
            textEl.innerHTML = `<em style="color:#ef4444">Arbeitsablauf fehlgeschlagen: ${escHtml(ev.message || '')}</em>`;
          }
        }
      }
      if (answer && typeof marked !== 'undefined') {
        if (window._ensureKatexMarked) window._ensureKatexMarked();
        textEl.innerHTML = marked.parse(answer, { gfm: true, breaks: true });
        textEl.querySelectorAll('a[href]').forEach(a => { a.target = '_blank'; a.rel = 'noopener noreferrer'; });
      }
      // Gesamttext (Schritte + Zusammenführung) für die Übergabe.
      const combined = (goal ? `# ${goal}\n\n` : '')
        + stepResults.map((r, i) => `## Schritt ${i + 1}: ${r.step}\n\n${r.result}`).join('\n\n')
        + (answer ? `\n\n## Gesamtergebnis\n\n${answer}` : '');
      if (stepResults.length) {
        const bar = document.createElement('div'); bar.className = 'wf-actions';
        const bPres = document.createElement('button'); bPres.className = 'wf-action-btn';
        bPres.textContent = '🖥️ → Präsentation';
        bPres.onclick = () => _workflowToPresentation(combined);
        const bPlan = document.createElement('button'); bPlan.className = 'wf-action-btn';
        bPlan.textContent = '🗂️ → Planer';
        bPlan.onclick = () => { if (typeof Planner !== 'undefined' && Planner.openFromText) Planner.openFromText(combined, goal || 'Arbeitsablauf'); else showToast('Planer nicht verfügbar'); };
        bar.appendChild(bPres); bar.appendChild(bPlan); content.appendChild(bar);
      }
      if (answer || stepResults.length) {
        messages.push({ role: 'user', content: head });
        messages.push({ role: 'assistant', content: combined });
        loadConversationList();
      }
    } catch (e) {
      if (e.name !== 'AbortError') textEl.innerHTML = `<em style="color:#ef4444">Arbeitsablauf fehlgeschlagen: ${escHtml(e.message)}</em>`;
    } finally {
      clearWorking();
      abortController = null;
      isStreaming = false;
      setBtnSendState(true);
    }
  }

  async function _workflowToPresentation(text) {
    if (!text) { showToast('Kein Ergebnis'); return; }
    const model = (typeof Profile !== 'undefined' ? Profile.modelFor('general') : '') || undefined;
    showToast('🖥️ Präsentation wird erstellt…');
    try {
      const r = await fetch('/api/presentation/from-text', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text, model }),
      });
      if (!r.ok) throw new Error((await r.json()).detail || r.status);
      const data = await r.json();
      if (data.tokens && typeof TokenMeter !== 'undefined') TokenMeter.add(data.tokens, 'Arbeitsablauf');
      delete data.tokens;
      if (typeof CanvasRenderer !== 'undefined') CanvasRenderer.render(data);
      if (typeof switchTab === 'function') switchTab('canvas');
      showToast('✓ Präsentation im Canvas erstellt');
    } catch (e) { showToast('Präsentation fehlgeschlagen: ' + e.message); }
  }

  // ── /ziel — zielgerichteter Loop im Chat ────────────────────────────────────
  // „/ziel <Beschreibung>" (Aliase /goal, /zi): der Nutzer gibt NUR ein Ziel vor.
  // Der Loop plant Teilziele, arbeitet in Runden (Handeln → Bewerten) darauf hin
  // und entscheidet selbst, wann das Ziel erreicht ist (oder bricht am Runden-
  // Deckel ab). Optionaler Tag am Anfang: [web] (Runden web-erden), [r7] (Deckel).
  function _parseGoal(text) {
    const m = text.match(/^\/(ziel|goal|zi)\b\s*([\s\S]*)$/i);
    if (!m) return null;
    let body = (m[2] || '').trim();
    let web = false, rounds = 0;
    const tm = body.match(/^\s*\[([^\]]{1,40})\]\s*([\s\S]*)$/);
    if (tm) {
      for (const t of tm[1].toLowerCase().split(/[,\s/+]+/).filter(Boolean)) {
        if (['web', 'internet', 'recherche', 'suche', 'search'].includes(t)) web = true;
        else if (/^r?\d{1,2}$/.test(t)) rounds = parseInt(t.replace(/^r/, ''), 10);
      }
      body = (tm[2] || '').trim();
    }
    return { goal: body, web, rounds };
  }

  async function runGoalLoop(goal, opts) {
    goal = (goal || '').trim();
    opts = opts || {};
    if (!goal) { showToast('Kein Ziel angegeben'); return; }
    if (isStreaming) { showToast('Bitte warten, bis die laufende Antwort fertig ist'); return; }
    showWelcome(false);
    isStreaming = true;
    setBtnSendState(false);

    const head = '🎯 Ziel: ' + goal + (opts.web ? '  [🌐 Web]' : '');
    appendMessage('user', head);
    if (!currentConvId) currentConvId = `conv_${Date.now()}`;

    const row = appendMessage('assistant', '', [], true);
    const content = row.querySelector('.bubble-content');
    const logEl = document.createElement('div'); logEl.className = 'research-log'; content.appendChild(logEl);
    // Sichtbarer Fortschrittsbalken (aus den Bewerten-Runden gespeist).
    const prog = document.createElement('div'); prog.className = 'goal-progress';
    prog.style.cssText = 'display:none;height:8px;border-radius:6px;background:var(--border,#2a2f3a);overflow:hidden;margin:8px 0';
    const progFill = document.createElement('div');
    progFill.style.cssText = 'height:100%;width:0%;background:linear-gradient(90deg,#3b82f6,#22c55e);transition:width .4s ease';
    prog.appendChild(progFill); content.appendChild(prog);
    const workingEl = makeWorking('plant und arbeitet auf das Ziel hin', { hintAfter: 25, hint: 'arbeitet weiter — jede Runde plant, handelt und prüft (bei langen Läufen etwas Geduld)' });
    content.appendChild(workingEl);
    const stepsEl = document.createElement('div'); content.appendChild(stepsEl);
    const textEl = document.createElement('div'); textEl.className = 'bubble-text'; content.appendChild(textEl);
    const _log = (t) => { const d = document.createElement('div'); d.className = 'research-log-line'; d.textContent = t; logEl.appendChild(d); scrollToBottom(); };
    const setProg = (p) => { prog.style.display = 'block'; progFill.style.width = Math.max(0, Math.min(100, p)) + '%'; };

    const model = (typeof Profile !== 'undefined' ? Profile.modelFor('general') : '') || undefined;
    const roundResults = [];
    let answer = '', workingCleared = false, reached = false;
    const clearWorking = () => { if (!workingCleared) { workingCleared = true; workingEl.remove(); } };
    abortController = new AbortController();
    try {
      const payload = { goal, web: !!opts.web, model };
      if (opts.rounds) payload.max_rounds = opts.rounds;
      const resp = await fetch('/api/goal', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        signal: abortController.signal,
        body: JSON.stringify(payload),
      });
      const reader = resp.body.getReader(); const dec = new TextDecoder(); let buf = '';
      while (true) {
        const { done, value } = await reader.read(); if (done) break;
        buf += dec.decode(value, { stream: true });
        const lines = buf.split('\n'); buf = lines.pop();
        for (const line of lines) {
          if (!line.startsWith('data: ')) continue;
          let ev; try { ev = JSON.parse(line.slice(6)); } catch (_) { continue; }
          if (ev.type === 'goal_start') {
            _log(`🎯 Ziel-Loop gestartet (max. ${ev.max_rounds} Runden${ev.web ? ', 🌐 Web' : ''})`);
          } else if (ev.type === 'planning') {
            _log('🧭 Teilziele werden geplant…');
          } else if (ev.type === 'plan') {
            const ul = document.createElement('ul'); ul.style.cssText = 'margin:4px 0 8px 1.2em;opacity:.85';
            (ev.items || []).forEach(it => { const li = document.createElement('li'); li.textContent = it; ul.appendChild(li); });
            const cap = document.createElement('div'); cap.style.cssText = 'font-size:.85em;opacity:.7'; cap.textContent = 'Plan:';
            stepsEl.appendChild(cap); stepsEl.appendChild(ul); scrollToBottom();
          } else if (ev.type === 'round_start') {
            _log(`▶ Runde ${ev.index + 1}/${ev.total}: ${(ev.focus || '').slice(0, 70)}`);
          } else if (ev.type === 'searching') {
            _log(`  🔎 Websuche: ${(ev.query || '').slice(0, 70)}…`);
          } else if (ev.type === 'search_done') {
            _log(`  ✓ ${ev.count || 0} Quellen gefunden`);
          } else if (ev.type === 'round_work') {
            clearWorking();
            const det = document.createElement('details'); det.className = 'wf-step';
            const sum = document.createElement('summary');
            sum.textContent = `✓ Runde ${ev.index + 1}: ${(ev.focus || '').slice(0, 70)}`;
            det.appendChild(sum);
            const bd = document.createElement('div'); bd.className = 'wf-step-body';
            renderMarkdown(bd, ev.result || '');
            det.appendChild(bd); stepsEl.appendChild(det); scrollToBottom();
            roundResults.push({ focus: ev.focus, result: ev.result });
          } else if (ev.type === 'evaluating') {
            _log('  🧪 Bewertung: Ziel erreicht?');
          } else if (ev.type === 'evaluate') {
            setProg(ev.progress || 0);
            const mark = ev.reached ? '✅ Ziel erreicht' : `↻ ${ev.progress || 0}% — offen: ${(ev.gap || '—').slice(0, 80)}`;
            _log('  ' + mark);
          } else if (ev.type === 'synthesizing') {
            reached = !!ev.reached;
            _log('📝 Gesamtergebnis wird zusammengeführt…');
          } else if (ev.type === 'text') {
            clearWorking();
            answer += ev.content; textEl.textContent = answer; scrollToBottom();
          } else if (ev.type === 'done') {
            if (typeof ev.reached === 'boolean') reached = ev.reached;
            if (typeof ev.progress === 'number') setProg(ev.progress);
            if (ev.tokens && typeof TokenMeter !== 'undefined') TokenMeter.add(ev.tokens, 'Ziel-Loop');
          } else if (ev.type === 'error') {
            clearWorking();
            textEl.innerHTML = `<em style="color:#ef4444">Ziel-Loop fehlgeschlagen: ${escHtml(ev.message || '')}</em>`;
          }
        }
      }
      if (answer && typeof marked !== 'undefined') {
        if (window._ensureKatexMarked) window._ensureKatexMarked();
        textEl.innerHTML = marked.parse(answer, { gfm: true, breaks: true });
        textEl.querySelectorAll('a[href]').forEach(a => { a.target = '_blank'; a.rel = 'noopener noreferrer'; });
      }
      const combined = `# ${goal}\n\n`
        + roundResults.map((r, i) => `## Runde ${i + 1}: ${r.focus}\n\n${r.result}`).join('\n\n')
        + (answer ? `\n\n## Gesamtergebnis\n\n${answer}` : '');
      if (roundResults.length) {
        const status = document.createElement('div');
        status.style.cssText = 'font-size:.9em;margin:6px 0;opacity:.85';
        status.textContent = reached ? '✅ Ziel als erreicht bewertet' : '↻ Runden-Deckel erreicht — Stand oben, offene Punkte im Fazit';
        content.appendChild(status);
        const bar = document.createElement('div'); bar.className = 'wf-actions';
        const bPres = document.createElement('button'); bPres.className = 'wf-action-btn';
        bPres.textContent = '🖥️ → Präsentation';
        bPres.onclick = () => _workflowToPresentation(combined);
        const bPlan = document.createElement('button'); bPlan.className = 'wf-action-btn';
        bPlan.textContent = '🗂️ → Planer';
        bPlan.onclick = () => { if (typeof Planner !== 'undefined' && Planner.openFromText) Planner.openFromText(combined, goal.slice(0, 60) || 'Ziel'); else showToast('Planer nicht verfügbar'); };
        bar.appendChild(bPres); bar.appendChild(bPlan); content.appendChild(bar);
      }
      if (answer || roundResults.length) {
        messages.push({ role: 'user', content: head });
        messages.push({ role: 'assistant', content: combined });
        loadConversationList();
      }
    } catch (e) {
      if (e.name !== 'AbortError') textEl.innerHTML = `<em style="color:#ef4444">Ziel-Loop fehlgeschlagen: ${escHtml(e.message)}</em>`;
    } finally {
      clearWorking();
      abortController = null;
      isStreaming = false;
      setBtnSendState(true);
    }
  }

  // ── /praesentation — illustrierter Präsentationsassistent ───────────────────
  // „/praesentation [alle|keine|<Zahl>] <Thema>" (Aliase /präsentation, /vortrag,
  // /slides): Thema → Folien + KI-Bilder (Standard: nur Titel- + Abschnittsfolien).
  function _parseIllustratedPres(text) {
    const m = text.match(/^\/(praesentation|präsentation|presentation|vortrag|slides)\b\s*([\s\S]*)$/i);
    if (!m) return null;
    let body = (m[2] || '').trim();
    let images = 'sections';
    const d = body.match(/^(alle|all|keine|ohne|kein|none|\d{1,2})\b\s*([\s\S]*)$/i);
    if (d) {
      const tok = d[1].toLowerCase();
      if (/^\d+$/.test(tok)) images = parseInt(tok, 10);
      else if (tok === 'alle' || tok === 'all') images = 'all';
      else images = 'none';   // keine/ohne/kein/none
      body = (d[2] || '').trim();
    }
    return { topic: body, images };
  }

  async function runIllustratedPresentation(topic, images) {
    if (isStreaming) { showToast('Bitte warten, bis die laufende Antwort fertig ist'); return; }
    showWelcome(false); isStreaming = true; setBtnSendState(false);
    const label = images === 'all' ? 'alle Folien'
                : images === 'none' ? 'ohne Bilder'
                : (typeof images === 'number' ? images + ' Bilder' : 'Titel + Abschnitte');
    appendMessage('user', `🖼️ Illustrierte Präsentation: ${topic}  [${label}]`);
    if (!currentConvId) currentConvId = `conv_${Date.now()}`;
    const row = appendMessage('assistant', '', [], true);
    const content = row.querySelector('.bubble-content');
    const logEl = document.createElement('div'); logEl.className = 'research-log'; content.appendChild(logEl);
    const workingEl = makeWorking('Präsentation wird erstellt'); content.appendChild(workingEl);
    const textEl = document.createElement('div'); textEl.className = 'bubble-text'; content.appendChild(textEl);
    const _log = (t) => { const d = document.createElement('div'); d.className = 'research-log-line'; d.textContent = t; logEl.appendChild(d); scrollToBottom(); };
    const model = (typeof Profile !== 'undefined' ? Profile.modelFor('general') : '') || undefined;
    let presentation = null;
    abortController = new AbortController();
    try {
      const resp = await fetch('/api/presentation/illustrated', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        signal: abortController.signal,
        body: JSON.stringify({ topic, images, model }),
      });
      const reader = resp.body.getReader(); const dec = new TextDecoder(); let buf = '';
      while (true) {
        const { done, value } = await reader.read(); if (done) break;
        buf += dec.decode(value, { stream: true });
        const lines = buf.split('\n'); buf = lines.pop();
        for (const line of lines) {
          if (!line.startsWith('data: ')) continue;
          let ev; try { ev = JSON.parse(line.slice(6)); } catch (_) { continue; }
          if (ev.type === 'pres_start') _log('🧭 Entwurf wird erstellt…');
          else if (ev.type === 'structure') _log(`📑 ${ev.count} Folien${ev.title ? ': ' + ev.title : ''}`);
          else if (ev.type === 'slide_image_start') _log(`🖼 Bild ${ev.n}/${ev.total} — Folie „${(ev.title || '').slice(0, 50)}"…`);
          else if (ev.type === 'slide_image_done') _log(`  ✓ Bild ${ev.n} fertig`);
          else if (ev.type === 'notice') { const d = document.createElement('div'); d.className = 'research-log-line'; d.style.opacity = '.7'; d.textContent = '  ℹ ' + (ev.message || ''); logEl.appendChild(d); scrollToBottom(); }
          else if (ev.type === 'done') { presentation = ev.presentation; if (ev.tokens && typeof TokenMeter !== 'undefined') TokenMeter.add(ev.tokens, 'Präsentationsassistent'); }
          else if (ev.type === 'error') { textEl.innerHTML = `<em style="color:#ef4444">${escHtml(ev.message || 'Fehlgeschlagen')}</em>`; }
        }
      }
      workingEl.remove();
      if (presentation && typeof CanvasRenderer !== 'undefined') {
        CanvasRenderer.render(presentation);
        if (typeof switchTab === 'function') switchTab('canvas');
        textEl.textContent = `✓ Präsentation „${presentation.title || topic}" mit ${(presentation.slides || []).length} Folien im Canvas erstellt.`;
        const bar = document.createElement('div'); bar.className = 'wf-actions';
        const b = document.createElement('button'); b.className = 'wf-action-btn'; b.textContent = '🖥️ zum Canvas';
        b.onclick = () => switchTab('canvas'); bar.appendChild(b); content.appendChild(bar);
        messages.push({ role: 'user', content: `Illustrierte Präsentation: ${topic}` });
        messages.push({ role: 'assistant', content: `Präsentation „${presentation.title || topic}" im Canvas erstellt.` });
        loadConversationList();
      } else if (!textEl.innerHTML) {
        textEl.innerHTML = `<em style="color:#ef4444">Präsentation fehlgeschlagen.</em>`;
      }
    } catch (e) {
      workingEl.remove();
      if (e.name !== 'AbortError') textEl.innerHTML = `<em style="color:#ef4444">Fehlgeschlagen: ${escHtml(e.message)}</em>`;
    } finally {
      abortController = null; isStreaming = false; setBtnSendState(true);
    }
  }

  // ── Geführter Präsentationsassistent: Interview + Erstellung ────────────────
  // Zwei Modi im selben Overlay: OHNE Bilder = Themenweg (Gliederung/Webrecherche,
  // /api/presentation/guided); MIT hochgeladenen Bildern = Bildweg (Vision je Bild →
  // Folien, /api/presentation/from-images). Die Bild-Auswahl entscheidet den Modus.
  function _openPresInterview(topic, imagesDirective) {
    const old = document.getElementById('pres-interview'); if (old) old.remove();
    _presImages = []; _presDoc = '';
    const imgMode = imagesDirective === 'all' ? 'all' : imagesDirective === 'none' ? 'none' : 'smart';
    const ov = document.createElement('div');
    ov.id = 'pres-interview';
    ov.style.cssText = 'position:fixed;inset:0;z-index:9999;background:rgba(0,0,0,.55);display:flex;align-items:center;justify-content:center;padding:16px';
    const fld = 'width:100%;padding:7px 9px;border-radius:7px;border:1px solid var(--border,#334);background:var(--bg-input,#0e141b);color:var(--text,#e6edf3);box-sizing:border-box';
    ov.innerHTML = `
      <div style="background:var(--bg-panel,#1b2330);color:var(--text,#e6edf3);border:1px solid var(--border,#334);border-radius:12px;max-width:580px;width:100%;max-height:92vh;overflow:auto;padding:18px 20px;box-shadow:0 10px 40px #000a">
        <div style="font-weight:700;font-size:1.1em;margin-bottom:10px">🖼️ Präsentation erstellen</div>

        <label style="display:block;margin:2px 0 3px">Bilder (optional – mit Bildern: aus den Bildern; ohne: aus dem Thema)</label>
        <div style="display:flex;gap:8px;flex-wrap:wrap;margin-bottom:6px">
          <button id="pi-pick-images" type="button" class="wf-action-btn">＋ Bilder wählen</button>
          <button id="pi-pick-doc" type="button" class="wf-action-btn">＋ Text/Markdown</button>
          <span id="pi-doc-info" style="opacity:.7;align-self:center;font-size:.9em"></span>
        </div>
        <div id="pi-thumbs" style="display:flex;flex-wrap:wrap;gap:6px;margin-bottom:6px"></div>

        <label style="display:block;margin:8px 0 3px">Thema / was beschrieben werden soll</label>
        <input id="pi-topic" type="text" placeholder="z. B. Baustellenfortschritt KW 12" style="${fld}">
        <label style="display:block;margin:10px 0 3px">Zielgruppe</label>
        <input id="pi-audience" type="text" placeholder="z. B. Geschäftsführung, Studierende, Laien" style="${fld}">
        <label style="display:block;margin:10px 0 3px">Ziel / Zweck</label>
        <input id="pi-goal" type="text" placeholder="z. B. überzeugen, informieren, Entscheidung vorbereiten" style="${fld}">
        <label style="display:block;margin:10px 0 3px">Anrede</label>
        <div id="pi-address"></div>
        <label style="display:block;margin:10px 0 3px">Stil</label>
        <div id="pi-style"></div>
        <label style="display:block;margin:10px 0 3px">Start- & Abschlussfolie</label>
        <div id="pi-cover"></div>
        <label style="display:flex;align-items:center;gap:8px;margin:12px 0 4px;cursor:pointer"><input id="pi-mermaid" type="checkbox"> Mermaid-Diagramm(e) als Übersicht ergänzen</label>
        <label style="display:flex;align-items:center;gap:8px;margin:4px 0 4px;cursor:pointer"><input id="pi-notes" type="checkbox"> Sprechernotizen je Folie (PPTX)</label>

        <div id="pi-topic-only" style="border-top:1px solid var(--border,#334);margin-top:10px;padding-top:8px">
          <div style="opacity:.7;font-size:.85em;margin-bottom:4px">Nur ohne Bilder (Themenweg):</div>
          <label style="display:block;margin:4px 0 3px">Umfang</label>
          <div id="pi-count"></div>
          <label style="display:block;margin:8px 0 3px">Bilder generieren</label>
          <div id="pi-images"></div>
          <label style="display:flex;align-items:center;gap:8px;margin:10px 0 4px;cursor:pointer"><input id="pi-web" type="checkbox" checked> Webrecherche je Gliederungspunkt</label>
        </div>

        <label style="display:block;margin:10px 0 3px">Bildwünsche / Stil generierter Bilder (optional)</label>
        <input id="pi-wishes" type="text" placeholder="z. B. fotorealistisch, minimalistisch, Aquarell" style="${fld}">
        <div style="display:flex;gap:8px;justify-content:flex-end;margin-top:16px">
          <button id="pi-cancel" class="wf-action-btn">Abbrechen</button>
          <button id="pi-go" class="wf-action-btn" style="background:var(--accent,#2d6cdf);color:#fff">Erstellen</button>
        </div>
      </div>`;
    document.body.appendChild(ov);
    if (topic) ov.querySelector('#pi-topic').value = topic;

    const mkChips = (host, opts, initial) => {
      host.dataset.val = String(initial);
      opts.forEach(([val, lab]) => {
        const b = document.createElement('button');
        b.type = 'button'; b.textContent = lab; b.dataset.val = String(val);
        b.style.cssText = 'padding:5px 10px;margin:2px;border-radius:14px;border:1px solid var(--border,#334);background:transparent;color:inherit;cursor:pointer';
        if (String(val) === String(initial)) { b.style.background = 'var(--accent,#2d6cdf)'; b.style.color = '#fff'; }
        b.onclick = () => {
          host.dataset.val = String(val);
          [...host.children].forEach(c => { c.style.background = 'transparent'; c.style.color = 'inherit'; });
          b.style.background = 'var(--accent,#2d6cdf)'; b.style.color = '#fff';
        };
        host.appendChild(b);
      });
    };
    mkChips(ov.querySelector('#pi-address'), [['sie', 'Sie'], ['du', 'Du']], 'sie');
    mkChips(ov.querySelector('#pi-style'), [['technisch', 'technisch'], ['sozial', 'sozial'], ['wissenschaftlich', 'wissenschaftlich'], ['marketing', 'marketing'], ['schlicht', 'schlicht'], ['kreativ', 'kreativ']], 'technisch');
    mkChips(ov.querySelector('#pi-cover'), [['generate', 'generieren'], ['uploaded', 'hochgeladenes Bild'], ['text', 'nur Text']], 'generate');
    mkChips(ov.querySelector('#pi-count'), [[4, 'kurz (4)'], [6, 'mittel (6)'], [8, 'lang (8)']], 6);
    mkChips(ov.querySelector('#pi-images'), [['smart', 'KI entscheidet'], ['all', 'alle Folien'], ['none', 'keine']], imgMode);

    // Bild-Auswahl + Drag-Sortieren
    const thumbs = ov.querySelector('#pi-thumbs');
    const _renderThumbs = () => {
      thumbs.innerHTML = '';
      const topicOnly = ov.querySelector('#pi-topic-only');
      if (topicOnly) topicOnly.style.display = _presImages.length ? 'none' : '';
      _presImages.forEach((im, idx) => {
        const cell = document.createElement('div');
        cell.draggable = true; cell.dataset.idx = String(idx);
        cell.style.cssText = 'position:relative;width:64px;height:64px;border-radius:6px;overflow:hidden;border:1px solid var(--border,#334);cursor:grab';
        cell.title = im.name + ' — ziehen zum Sortieren';
        cell.innerHTML = `<img src="${im.data}" style="width:100%;height:100%;object-fit:cover;display:block"><button data-x="${idx}" style="position:absolute;top:0;right:0;border:0;background:#000a;color:#fff;cursor:pointer;font-size:12px;line-height:1;padding:1px 4px">×</button><span style="position:absolute;bottom:0;left:0;background:#000a;color:#fff;font-size:10px;padding:0 3px">${idx + 1}</span>`;
        cell.addEventListener('dragstart', e => { e.dataTransfer.setData('text/plain', String(idx)); });
        cell.addEventListener('dragover', e => e.preventDefault());
        cell.addEventListener('drop', e => {
          e.preventDefault();
          const from = parseInt(e.dataTransfer.getData('text/plain'), 10);
          const to = idx;
          if (isNaN(from) || from === to) return;
          const [m] = _presImages.splice(from, 1);
          _presImages.splice(to, 0, m);
          _renderThumbs();
        });
        cell.querySelector('button[data-x]').onclick = (e) => { e.stopPropagation(); _presImages.splice(idx, 1); _renderThumbs(); };
        thumbs.appendChild(cell);
      });
    };
    ov.querySelector('#pi-pick-images').onclick = () => {
      const inp = document.createElement('input');
      inp.type = 'file'; inp.accept = 'image/*'; inp.multiple = true; inp.style.display = 'none';
      inp.onchange = async () => {
        const files = [...(inp.files || [])].filter(f => /^image\//.test(f.type) || /\.(jpe?g|png|gif|webp|bmp)$/i.test(f.name));
        files.sort((a, b) => a.name.localeCompare(b.name, 'de', { numeric: true }));
        for (const f of files) {
          const data = await new Promise(res => { const r = new FileReader(); r.onload = () => res(String(r.result || '')); r.readAsDataURL(f); });
          _presImages.push({ name: f.name, data });
        }
        _renderThumbs();
      };
      inp.click();
    };
    ov.querySelector('#pi-pick-doc').onclick = () => {
      const inp = document.createElement('input');
      inp.type = 'file'; inp.accept = '.md,.txt,text/plain,text/markdown'; inp.style.display = 'none';
      inp.onchange = () => {
        const f = inp.files && inp.files[0]; if (!f) return;
        const r = new FileReader();
        r.onload = () => { _presDoc = String(r.result || ''); ov.querySelector('#pi-doc-info').textContent = `✓ ${f.name}`; };
        r.readAsText(f);
      };
      inp.click();
    };
    _renderThumbs();

    const close = () => ov.remove();
    ov.addEventListener('click', e => { if (e.target === ov) close(); });
    document.addEventListener('keydown', function esc(e) { if (e.key === 'Escape') { close(); document.removeEventListener('keydown', esc); } });
    ov.querySelector('#pi-cancel').onclick = close;
    ov.querySelector('#pi-go').onclick = () => {
      const topicVal = ov.querySelector('#pi-topic').value.trim();
      if (_presImages.length) {
        const params = {
          images: _presImages.slice(),
          doc_text: _presDoc || '',
          topic: topicVal,
          audience: ov.querySelector('#pi-audience').value.trim(),
          address: ov.querySelector('#pi-address').dataset.val || 'sie',
          style: ov.querySelector('#pi-style').dataset.val || '',
          cover_source: ov.querySelector('#pi-cover').dataset.val || 'generate',
          want_mermaid: ov.querySelector('#pi-mermaid').checked,
          mermaid_count: 2,
          want_notes: ov.querySelector('#pi-notes').checked,
          image_wishes: ov.querySelector('#pi-wishes').value.trim(),
        };
        close();
        runImagePresentation(params);
        return;
      }
      if (!topicVal) { showToast('Bitte ein Thema eingeben oder Bilder wählen'); return; }
      const params = {
        topic: topicVal,
        audience: ov.querySelector('#pi-audience').value.trim(),
        goal: ov.querySelector('#pi-goal').value.trim(),
        count: parseInt(ov.querySelector('#pi-count').dataset.val, 10) || 6,
        image_mode: ov.querySelector('#pi-images').dataset.val || 'smart',
        image_wishes: ov.querySelector('#pi-wishes').value.trim(),
        style: ov.querySelector('#pi-style').dataset.val || '',
        web: ov.querySelector('#pi-web').checked,
      };
      close();
      runGuidedPresentation(params);
    };
    setTimeout(() => { const a = ov.querySelector('#pi-topic'); if (a) a.focus(); }, 30);
  }

  // Mermaid-Definition → PNG-Data-URI (für export-feste Diagrammfolien). mermaid.js liegt
  // nur im Browser; das Backend liefert die Definition als Text, hier wird sie gerastert.
  // WICHTIG: mit htmlLabels:false rendern — HTML-Labels erzeugen <foreignObject>, das den
  // Canvas „tainted" und toDataURL blockiert. Danach die Chat-Standardkonfig wiederherstellen.
  async function _mermaidToPng(def) {
    try {
      _ensureMermaid();
      if (typeof mermaid === 'undefined') return '';
      mermaid.initialize({ startOnLoad: false, theme: 'dark', htmlLabels: false,
        flowchart: { htmlLabels: false },
        themeVariables: { background: '#1e1e2e', primaryColor: '#3b76ba',
                          primaryTextColor: '#d4e8f8', lineColor: '#a3c8eb' } });
      let png = '';
      try {
        const id = 'mmp-' + Date.now() + '-' + Math.random().toString(36).slice(2, 7);
        const { svg } = await mermaid.render(id, def);
        const dataUrl = 'data:image/svg+xml;base64,' + btoa(unescape(encodeURIComponent(svg)));
        const img = await new Promise((res, rej) => { const i = new Image(); i.onload = () => res(i); i.onerror = rej; i.src = dataUrl; });
        const scale = 2;
        const w = (img.naturalWidth || 640) * scale, h = (img.naturalHeight || 400) * scale;
        const cv = document.createElement('canvas'); cv.width = w; cv.height = h;
        const ctx = cv.getContext('2d');
        ctx.fillStyle = '#1e1e2e'; ctx.fillRect(0, 0, w, h);
        ctx.drawImage(img, 0, 0, w, h);
        png = cv.toDataURL('image/png');
      } finally {
        // Chat-Standard (mit HTML-Labels) wiederherstellen, damit Chat-Diagramme unverändert bleiben.
        mermaid.initialize({ startOnLoad: false, theme: 'dark',
          themeVariables: { background: '#1e1e2e', primaryColor: '#3b76ba',
                            primaryTextColor: '#d4e8f8', lineColor: '#a3c8eb' } });
      }
      return png;
    } catch (_) { return ''; }
  }

  async function runImagePresentation(params) {
    if (isStreaming) { showToast('Bitte warten, bis die laufende Antwort fertig ist'); return; }
    showWelcome(false); isStreaming = true; setBtnSendState(false);
    const head = `🖼️ Präsentation aus ${params.images.length} Bild(ern)${params.topic ? ': ' + params.topic : ''}`;
    appendMessage('user', head);
    if (!currentConvId) currentConvId = `conv_${Date.now()}`;
    const row = appendMessage('assistant', '', [], true);
    const content = row.querySelector('.bubble-content');
    const logEl = document.createElement('div'); logEl.className = 'research-log'; content.appendChild(logEl);
    const workingEl = makeWorking('Präsentation wird erstellt'); content.appendChild(workingEl);
    const textEl = document.createElement('div'); textEl.className = 'bubble-text'; content.appendChild(textEl);
    const _log = (t) => { const d = document.createElement('div'); d.className = 'research-log-line'; d.textContent = t; logEl.appendChild(d); scrollToBottom(); };
    const model = (typeof Profile !== 'undefined' ? Profile.modelFor('general') : '') || undefined;
    const image_model = (typeof Profile !== 'undefined' && Profile.imageModel) ? Profile.imageModel() : undefined;
    let presentation = null;
    abortController = new AbortController();
    try {
      const resp = await fetch('/api/presentation/from-images', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        signal: abortController.signal,
        body: JSON.stringify(Object.assign({ model, image_model }, params)),
      });
      const reader = resp.body.getReader(); const dec = new TextDecoder(); let buf = '';
      while (true) {
        const { done, value } = await reader.read(); if (done) break;
        buf += dec.decode(value, { stream: true });
        const lines = buf.split('\n'); buf = lines.pop();
        for (const line of lines) {
          if (!line.startsWith('data: ')) continue;
          let ev; try { ev = JSON.parse(line.slice(6)); } catch (_) { continue; }
          if (ev.type === 'pres_start') _log('🧭 Bilder werden ausgewertet…');
          else if (ev.type === 'analyzing') _log(`  🔍 Bild ${ev.n}/${ev.total}${ev.name ? ' — ' + ev.name : ''}…`);
          else if (ev.type === 'image_done') _log(`  ✓ „${(ev.title || '').slice(0, 50)}"`);
          else if (ev.type === 'slide_image_start') _log(`🖼 Bild ${ev.n}/${ev.total}${ev.kind === 'cover' ? ' (Deckblatt)' : ev.kind === 'closing' ? ' (Abschluss)' : ''}…`);
          else if (ev.type === 'slide_image_done') _log(`  ✓ Bild ${ev.n} fertig`);
          else if (ev.type === 'structure') _log(`📑 ${ev.count} Folien`);
          else if (ev.type === 'notice') { const d = document.createElement('div'); d.className = 'research-log-line'; d.style.opacity = '.7'; d.textContent = '  ℹ ' + (ev.message || ''); logEl.appendChild(d); scrollToBottom(); }
          else if (ev.type === 'done') { presentation = ev.presentation; if (ev.tokens && typeof TokenMeter !== 'undefined') TokenMeter.add(ev.tokens, 'Präsentationsassistent'); }
          else if (ev.type === 'error') { textEl.innerHTML = `<em style="color:#ef4444">${escHtml(ev.message || 'Fehlgeschlagen')}</em>`; }
        }
      }
      workingEl.remove();
      if (presentation && typeof CanvasRenderer !== 'undefined') {
        // Mermaid-Folien zu Bildern rastern (export-fest), sonst als Text-Folie belassen.
        for (const sl of (presentation.slides || [])) {
          if (sl.mermaid) {
            const png = await _mermaidToPng(sl.mermaid);
            if (png) { sl.image_right = png; sl.layout = 'two-column'; }
            delete sl.mermaid;
          }
        }
        CanvasRenderer.render(presentation);
        if (typeof switchTab === 'function') switchTab('canvas');
        textEl.textContent = `✓ Präsentation „${presentation.title || ''}" mit ${(presentation.slides || []).length} Folien im Canvas erstellt.`;
        _presConfirmBar(content, presentation, params);
        messages.push({ role: 'user', content: head });
        messages.push({ role: 'assistant', content: `Präsentation „${presentation.title || ''}" im Canvas erstellt.` });
        loadConversationList();
      } else if (!textEl.innerHTML) {
        textEl.innerHTML = `<em style="color:#ef4444">Präsentation fehlgeschlagen.</em>`;
      }
    } catch (e) {
      workingEl.remove();
      if (e.name !== 'AbortError') textEl.innerHTML = `<em style="color:#ef4444">Fehlgeschlagen: ${escHtml(e.message)}</em>`;
    } finally {
      abortController = null; isStreaming = false; setBtnSendState(true);
    }
  }

  // Nachfrage nach dem Bauen: so verwenden ODER Start-/Abschlussfolie per Bildgenerator neu.
  function _presConfirmBar(content, presentation, params) {
    const bar = document.createElement('div'); bar.className = 'wf-actions';
    const bCanvas = document.createElement('button'); bCanvas.className = 'wf-action-btn'; bCanvas.textContent = '🖥️ zum Canvas';
    bCanvas.onclick = () => switchTab('canvas');
    const bUse = document.createElement('button'); bUse.className = 'wf-action-btn'; bUse.textContent = '✅ so verwenden';
    bUse.onclick = () => { bar.remove(); showToast('Präsentation übernommen'); };
    const bRegen = document.createElement('button'); bRegen.className = 'wf-action-btn'; bRegen.textContent = '🎨 Start-/Abschlussfolie neu generieren';
    bRegen.onclick = () => _regenCoverClosing(presentation, params, bRegen);
    bar.appendChild(bCanvas); bar.appendChild(bUse); bar.appendChild(bRegen);
    content.appendChild(bar);
  }

  async function _regenCoverClosing(presentation, params, btn) {
    const slides = presentation.slides || [];
    if (!slides.length) return;
    btn.disabled = true; const orig = btn.textContent; btn.textContent = '🎨 wird generiert…';
    const image_model = (typeof Profile !== 'undefined' && Profile.imageModel) ? Profile.imageModel() : undefined;
    const style = params.style || '';
    const wishes = params.image_wishes || '';
    const _gen = async (slide, basis) => {
      try {
        const r = await fetch('/api/presentation/slide-image', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ title: slide.title || '', content: basis, preset: 'landscape',
                                 style: [style, wishes].filter(Boolean).join(' · '), model: image_model }),
        });
        if (!r.ok) throw new Error('HTTP ' + r.status);
        const d = await r.json();
        if (d.tokens && typeof TokenMeter !== 'undefined') TokenMeter.add(d.tokens, 'Präsentationsbild');
        if (d.image) { slide.image = d.image; slide.layout = 'title'; }
      } catch (e) { showToast('Bild fehlgeschlagen: ' + e.message); }
    };
    await _gen(slides[0], presentation.title || '');
    await _gen(slides[slides.length - 1], presentation.title || '');
    try { CanvasRenderer.render(presentation); switchTab('canvas'); } catch (_) {}
    btn.disabled = false; btn.textContent = orig;
    showToast('Start-/Abschlussfolie neu generiert');
  }

  async function runGuidedPresentation(params) {
    if (isStreaming) { showToast('Bitte warten, bis die laufende Antwort fertig ist'); return; }
    showWelcome(false); isStreaming = true; setBtnSendState(false);
    const head = `🖼️ Präsentation: ${params.topic}`;
    appendMessage('user', head);
    if (!currentConvId) currentConvId = `conv_${Date.now()}`;
    const row = appendMessage('assistant', '', [], true);
    const content = row.querySelector('.bubble-content');
    const logEl = document.createElement('div'); logEl.className = 'research-log'; content.appendChild(logEl);
    const workingEl = makeWorking('Präsentation wird erstellt'); content.appendChild(workingEl);
    const textEl = document.createElement('div'); textEl.className = 'bubble-text'; content.appendChild(textEl);
    const _log = (t) => { const d = document.createElement('div'); d.className = 'research-log-line'; d.textContent = t; logEl.appendChild(d); scrollToBottom(); };
    const model = (typeof Profile !== 'undefined' ? Profile.modelFor('general') : '') || undefined;
    let presentation = null;
    abortController = new AbortController();
    try {
      const resp = await fetch('/api/presentation/guided', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        signal: abortController.signal,
        body: JSON.stringify(Object.assign({ model }, params)),
      });
      const reader = resp.body.getReader(); const dec = new TextDecoder(); let buf = '';
      while (true) {
        const { done, value } = await reader.read(); if (done) break;
        buf += dec.decode(value, { stream: true });
        const lines = buf.split('\n'); buf = lines.pop();
        for (const line of lines) {
          if (!line.startsWith('data: ')) continue;
          let ev; try { ev = JSON.parse(line.slice(6)); } catch (_) { continue; }
          if (ev.type === 'pres_start') _log('🧭 Gliederung wird erstellt…');
          else if (ev.type === 'structure') _log(`📑 Inhaltsverzeichnis (${(ev.toc || []).length}): ${(ev.toc || []).join(' · ').slice(0, 120)}`);
          else if (ev.type === 'researching') _log(`  🔎 Recherche: ${(ev.query || '').slice(0, 60)}…`);
          else if (ev.type === 'research_done') _log(`  ✓ ${ev.count || 0} Quellen`);
          else if (ev.type === 'section_done') _log(`  📝 „${(ev.title || '').slice(0, 50)}" zusammengefasst${ev.image ? ' (+ Bild)' : ''}`);
          else if (ev.type === 'slide_image_start') _log(`🖼 Bild ${ev.n}/${ev.total}${ev.kind === 'cover' ? ' (Deckblatt)' : ev.kind === 'closing' ? ' (Abschluss)' : ''} — „${(ev.title || '').slice(0, 40)}"…`);
          else if (ev.type === 'slide_image_done') _log(`  ✓ Bild ${ev.n} fertig`);
          else if (ev.type === 'notice') { const d = document.createElement('div'); d.className = 'research-log-line'; d.style.opacity = '.7'; d.textContent = '  ℹ ' + (ev.message || ''); logEl.appendChild(d); scrollToBottom(); }
          else if (ev.type === 'done') { presentation = ev.presentation; if (ev.tokens && typeof TokenMeter !== 'undefined') TokenMeter.add(ev.tokens, 'Präsentationsassistent'); }
          else if (ev.type === 'error') { textEl.innerHTML = `<em style="color:#ef4444">${escHtml(ev.message || 'Fehlgeschlagen')}</em>`; }
        }
      }
      workingEl.remove();
      if (presentation && typeof CanvasRenderer !== 'undefined') {
        CanvasRenderer.render(presentation);
        if (typeof switchTab === 'function') switchTab('canvas');
        textEl.textContent = `✓ Präsentation „${presentation.title || params.topic}" mit ${(presentation.slides || []).length} Folien im Canvas erstellt.`;
        const bar = document.createElement('div'); bar.className = 'wf-actions';
        const b = document.createElement('button'); b.className = 'wf-action-btn'; b.textContent = '🖥️ zum Canvas';
        b.onclick = () => switchTab('canvas'); bar.appendChild(b); content.appendChild(bar);
        messages.push({ role: 'user', content: head });
        messages.push({ role: 'assistant', content: `Präsentation „${presentation.title || params.topic}" im Canvas erstellt.` });
        loadConversationList();
      } else if (!textEl.innerHTML) {
        textEl.innerHTML = `<em style="color:#ef4444">Präsentation fehlgeschlagen.</em>`;
      }
    } catch (e) {
      workingEl.remove();
      if (e.name !== 'AbortError') textEl.innerHTML = `<em style="color:#ef4444">Fehlgeschlagen: ${escHtml(e.message)}</em>`;
    } finally {
      abortController = null; isStreaming = false; setBtnSendState(true);
    }
  }

  // ── /bildprompt — Bild → Text-zu-Bild-Prompt (Vision) ───────────────────────
  // „/bildprompt [Stil]" öffnet einen Bild-Picker; das Vision-Modell macht daraus
  // einen Prompt, den man direkt an /bild weiterreichen kann.
  function _parseBildPrompt(text) {
    const m = text.match(/^\/(bildprompt|img2prompt|bild2prompt|imageprompt)\b\s*([\s\S]*)$/i);
    if (!m) return null;
    return { style: (m[2] || '').trim() };
  }

  function runBildPrompt(style) {
    const inp = document.createElement('input');
    inp.type = 'file'; inp.accept = 'image/*'; inp.style.display = 'none';
    document.body.appendChild(inp);
    inp.addEventListener('change', () => {
      const file = inp.files && inp.files[0];
      inp.remove();
      if (!file) return;
      const reader = new FileReader();
      reader.onload = () => _doBildPrompt(String(reader.result || ''), file.name, style);
      reader.readAsDataURL(file);
    });
    inp.click();
  }

  async function _doBildPrompt(dataUrl, filename, style) {
    if (!dataUrl) return;
    showWelcome(false);
    appendMessage('user', `🔍 Bild → Prompt${style ? ' (' + style + ')' : ''}: ${filename || 'Bild'}`);
    const row = appendMessage('assistant', '', [], true);
    const content = row.querySelector('.bubble-content');
    insertImage(content, dataUrl);
    const workingEl = makeWorking('Prompt wird aus dem Bild abgeleitet'); content.appendChild(workingEl);
    const textEl = document.createElement('div'); textEl.className = 'bubble-text'; content.appendChild(textEl);
    const model = (typeof Profile !== 'undefined' ? Profile.modelFor('general') : '') || undefined;
    try {
      const resp = await fetch('/api/image-to-prompt', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ image: dataUrl, style, model }),
      });
      workingEl.remove();
      if (!resp.ok) {
        let m = 'HTTP ' + resp.status; try { m = (await resp.json()).detail || m; } catch (_) {}
        textEl.innerHTML = `<em style="color:#ef4444">${escHtml(m)}</em>`; return;
      }
      const data = await resp.json();
      if (data.tokens && typeof TokenMeter !== 'undefined') TokenMeter.add(data.tokens, 'Bild→Prompt');
      const prompt = data.prompt || '';
      const box = document.createElement('div');
      box.style.cssText = 'white-space:pre-wrap;background:#0003;padding:8px 10px;border-radius:8px;margin:6px 0';
      box.textContent = prompt;
      content.appendChild(box);
      const bar = document.createElement('div'); bar.className = 'wf-actions';
      const bGen = document.createElement('button'); bGen.className = 'wf-action-btn'; bGen.textContent = '🎨 Bild daraus erzeugen';
      bGen.onclick = () => runBild(prompt);
      const bCopy = document.createElement('button'); bCopy.className = 'wf-action-btn'; bCopy.textContent = '📋 kopieren';
      bCopy.onclick = () => { if (navigator.clipboard) navigator.clipboard.writeText(prompt); showToast('Prompt kopiert'); };
      bar.appendChild(bGen); bar.appendChild(bCopy); content.appendChild(bar);
      messages.push({ role: 'user', content: 'Bild → Prompt' });
      messages.push({ role: 'assistant', content: prompt });
    } catch (e) {
      workingEl.remove();
      textEl.innerHTML = `<em style="color:#ef4444">Fehlgeschlagen: ${escHtml(e.message)}</em>`;
    }
  }

  // ── /bildedit — Bildbearbeitung (img2img): Bild + Anweisung → verändertes Bild ─
  function _parseBildEdit(text) {
    const m = text.match(/^\/(bildedit|bildbearbeiten|imgedit|imageedit|edit)\b\s*([\s\S]*)$/i);
    if (!m) return null;
    return { instruction: (m[2] || '').trim() };
  }

  function runBildEdit(instruction) {
    const inp = document.createElement('input');
    inp.type = 'file'; inp.accept = 'image/*'; inp.style.display = 'none';
    document.body.appendChild(inp);
    inp.addEventListener('change', () => {
      const file = inp.files && inp.files[0];
      inp.remove();
      if (!file) return;
      const reader = new FileReader();
      reader.onload = () => _openBildEditForm(String(reader.result || ''), file.name, instruction);
      reader.readAsDataURL(file);
    });
    inp.click();
  }

  function _openBildEditForm(dataUrl, filename, instruction) {
    const old = document.getElementById('bildedit-form'); if (old) old.remove();
    const ov = document.createElement('div');
    ov.id = 'bildedit-form';
    ov.style.cssText = 'position:fixed;inset:0;z-index:9999;background:rgba(0,0,0,.55);display:flex;align-items:center;justify-content:center;padding:16px';
    const fld = 'width:100%;padding:7px 9px;border-radius:7px;border:1px solid var(--border,#334);background:var(--bg-input,#0e141b);color:var(--text,#e6edf3);box-sizing:border-box';
    ov.innerHTML = `
      <div style="background:var(--bg-panel,#1b2330);color:var(--text,#e6edf3);border:1px solid var(--border,#334);border-radius:12px;max-width:520px;width:100%;max-height:92vh;overflow:auto;padding:18px 20px;box-shadow:0 10px 40px #000a">
        <div style="font-weight:700;font-size:1.1em;margin-bottom:10px">✏️ Bild verändern</div>
        <div id="be-stage" style="position:relative;max-width:100%;margin:0 auto 8px;line-height:0"></div>
        <div id="be-masktools" style="display:none;align-items:center;gap:10px;margin:0 0 8px;font-size:.9em">
          <span>Pinsel</span><input id="be-brush" type="range" min="8" max="80" value="34" style="flex:1">
          <button id="be-clear" class="wf-action-btn" type="button">🗑 löschen</button>
        </div>
        <button id="be-maskbtn" class="wf-action-btn" type="button" style="margin-bottom:10px">🖌 Bereich markieren (nur diesen ändern)</button>
        <label style="display:block;margin:6px 0 3px">Was soll verändert werden?</label>
        <textarea id="be-instruction" rows="2" style="${fld}" placeholder="z. B. Himmel bei Sonnenuntergang · im Aquarellstil · das Auto entfernen"></textarea>
        <label style="display:block;margin:10px 0 3px">Stärke der Veränderung</label>
        <div id="be-strength"></div>
        <div style="display:flex;gap:8px;justify-content:flex-end;margin-top:16px">
          <button id="be-cancel" class="wf-action-btn">Abbrechen</button>
          <button id="be-go" class="wf-action-btn" style="background:var(--accent,#2d6cdf);color:#fff">Verändern</button>
        </div>
      </div>`;
    document.body.appendChild(ov);
    ov.querySelector('#be-instruction').value = instruction || '';

    // Bild + optionaler Masken-Canvas (Inpainting, reines Vanilla-Canvas).
    const stage = ov.querySelector('#be-stage');
    let maskCanvas = null, overlay = null, painting = false, hasMask = false, brush = 34;
    const _imgEl = new Image();
    _imgEl.onload = () => {
      const dw = Math.min(460, _imgEl.naturalWidth || 460);
      const dh = Math.round(dw * (_imgEl.naturalHeight / _imgEl.naturalWidth));
      const base = document.createElement('canvas'); base.width = dw; base.height = dh;
      base.style.cssText = 'max-width:100%;border-radius:8px;display:block';
      base.getContext('2d').drawImage(_imgEl, 0, 0, dw, dh);
      overlay = document.createElement('canvas'); overlay.width = dw; overlay.height = dh;
      overlay.style.cssText = 'position:absolute;left:0;top:0;max-width:100%;border-radius:8px;touch-action:none;cursor:crosshair;display:none';
      maskCanvas = document.createElement('canvas');
      maskCanvas.width = _imgEl.naturalWidth; maskCanvas.height = _imgEl.naturalHeight;
      const mc = maskCanvas.getContext('2d'); mc.fillStyle = '#000'; mc.fillRect(0, 0, maskCanvas.width, maskCanvas.height);
      stage.appendChild(base); stage.appendChild(overlay);
      const oc = overlay.getContext('2d');
      const sx = maskCanvas.width / dw, sy = maskCanvas.height / dh;
      const paint = (e) => {
        const r = overlay.getBoundingClientRect();
        const x = (e.clientX - r.left) * (overlay.width / r.width);
        const y = (e.clientY - r.top) * (overlay.height / r.height);
        oc.fillStyle = 'rgba(230,60,60,0.55)';
        oc.beginPath(); oc.arc(x, y, brush / 2, 0, 7); oc.fill();
        mc.fillStyle = '#fff';
        mc.beginPath(); mc.arc(x * sx, y * sy, (brush / 2) * sx, 0, 7); mc.fill();
        hasMask = true;
      };
      overlay.addEventListener('pointerdown', e => { painting = true; try { overlay.setPointerCapture(e.pointerId); } catch (_) {} paint(e); });
      overlay.addEventListener('pointermove', e => { if (painting) paint(e); });
      overlay.addEventListener('pointerup', () => { painting = false; });
      overlay.addEventListener('pointerleave', () => { painting = false; });
    };
    _imgEl.src = dataUrl;

    ov.querySelector('#be-maskbtn').onclick = (ev) => {
      const on = overlay && overlay.style.display === 'none';
      if (overlay) overlay.style.display = on ? 'block' : 'none';
      ov.querySelector('#be-masktools').style.display = on ? 'flex' : 'none';
      ev.target.textContent = on ? '🖌 Markierung aus (ganzes Bild)' : '🖌 Bereich markieren (nur diesen ändern)';
      ev.target.style.background = on ? 'var(--accent,#2d6cdf)' : '';
      ev.target.style.color = on ? '#fff' : '';
    };
    ov.querySelector('#be-brush').oninput = (e) => { brush = parseInt(e.target.value, 10) || 34; };
    ov.querySelector('#be-clear').onclick = () => {
      if (overlay) overlay.getContext('2d').clearRect(0, 0, overlay.width, overlay.height);
      if (maskCanvas) { const mc = maskCanvas.getContext('2d'); mc.fillStyle = '#000'; mc.fillRect(0, 0, maskCanvas.width, maskCanvas.height); }
      hasMask = false;
    };

    const host = ov.querySelector('#be-strength');
    host.dataset.val = '0.55';
    [['0.35', 'leicht'], ['0.55', 'mittel'], ['0.75', 'stark']].forEach(([val, lab]) => {
      const b = document.createElement('button');
      b.type = 'button'; b.textContent = lab; b.dataset.val = val;
      b.style.cssText = 'padding:5px 12px;margin:2px;border-radius:14px;border:1px solid var(--border,#334);background:transparent;color:inherit;cursor:pointer';
      if (val === '0.55') { b.style.background = 'var(--accent,#2d6cdf)'; b.style.color = '#fff'; }
      b.onclick = () => {
        host.dataset.val = val;
        [...host.children].forEach(c => { c.style.background = 'transparent'; c.style.color = 'inherit'; });
        b.style.background = 'var(--accent,#2d6cdf)'; b.style.color = '#fff';
      };
      host.appendChild(b);
    });
    const close = () => ov.remove();
    ov.addEventListener('click', e => { if (e.target === ov) close(); });
    document.addEventListener('keydown', function esc(e) { if (e.key === 'Escape') { close(); document.removeEventListener('keydown', esc); } });
    ov.querySelector('#be-cancel').onclick = close;
    ov.querySelector('#be-go').onclick = () => {
      const instr = ov.querySelector('#be-instruction').value.trim();
      if (!instr) { showToast('Bitte eine Änderungsanweisung angeben'); return; }
      const strength = parseFloat(host.dataset.val) || 0.55;
      const maskOn = overlay && overlay.style.display !== 'none' && hasMask;
      const mask = maskOn ? maskCanvas.toDataURL('image/png') : null;
      close();
      _doBildEdit(dataUrl, instr, strength, mask);
    };
    setTimeout(() => { const t = ov.querySelector('#be-instruction'); if (t) t.focus(); }, 30);
  }

  async function _doBildEdit(dataUrl, instruction, strength, mask) {
    showWelcome(false);
    appendMessage('user', `✏️ Bild verändern${mask ? ' (markierter Bereich)' : ''}: ${instruction}`);
    const row = appendMessage('assistant', '', [], true);
    const content = row.querySelector('.bubble-content');
    insertImage(content, dataUrl);   // Original
    const workingEl = makeWorking(mask ? 'markierter Bereich wird verändert' : 'Bild wird verändert',
      { hintAfter: 20, hint: 'Bild wird verändert — das lokale Modell rechnet, das dauert ~30–60 s' }); content.appendChild(workingEl);
    const textEl = document.createElement('div'); textEl.className = 'bubble-text'; content.appendChild(textEl);
    try {
      const _body = { image: dataUrl, prompt: instruction, strength };
      if (mask) _body.mask = mask;
      const resp = await fetch('/api/image/edit', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(_body),
      });
      workingEl.remove();
      if (!resp.ok) {
        let m = 'HTTP ' + resp.status; try { m = (await resp.json()).detail || m; } catch (_) {}
        textEl.innerHTML = `<em style="color:#ef4444">${escHtml(m)}</em>`; return;
      }
      const data = await resp.json();
      textEl.textContent = '→ Ergebnis:';
      insertImage(content, data.image);
      const bar = document.createElement('div'); bar.className = 'wf-actions';
      const bAgain = document.createElement('button'); bAgain.className = 'wf-action-btn'; bAgain.textContent = '✏️ weiter bearbeiten';
      bAgain.onclick = () => _openBildEditForm(data.image, 'ergebnis.png', '');
      const bUp = document.createElement('button'); bUp.className = 'wf-action-btn'; bUp.textContent = '🔍 hochskalieren';
      bUp.onclick = () => _openUpscaleForm(data.image, 'ergebnis.png');
      bar.appendChild(bAgain); bar.appendChild(bUp); content.appendChild(bar);
      messages.push({ role: 'user', content: `Bild verändern: ${instruction}` });
      messages.push({ role: 'assistant', content: '(verändertes Bild)' });
    } catch (e) {
      workingEl.remove();
      textEl.innerHTML = `<em style="color:#ef4444">Fehlgeschlagen: ${escHtml(e.message)}</em>`;
    }
  }

  // ── /upscale — Bild hochskalieren (KI-Detail über Z-Image oder schnell/Lanczos) ─
  function _parseUpscale(text) {
    const m = text.match(/^\/(upscale|hochskalieren|vergroessern|vergrößern)\b\s*([\s\S]*)$/i);
    return m ? {} : null;
  }

  function runUpscale() {
    const inp = document.createElement('input');
    inp.type = 'file'; inp.accept = 'image/*'; inp.style.display = 'none';
    document.body.appendChild(inp);
    inp.addEventListener('change', () => {
      const file = inp.files && inp.files[0];
      inp.remove();
      if (!file) return;
      const reader = new FileReader();
      reader.onload = () => _openUpscaleForm(String(reader.result || ''), file.name);
      reader.readAsDataURL(file);
    });
    inp.click();
  }

  function _openUpscaleForm(dataUrl, filename) {
    const old = document.getElementById('upscale-form'); if (old) old.remove();
    const ov = document.createElement('div');
    ov.id = 'upscale-form';
    ov.style.cssText = 'position:fixed;inset:0;z-index:9999;background:rgba(0,0,0,.55);display:flex;align-items:center;justify-content:center;padding:16px';
    ov.innerHTML = `
      <div style="background:var(--bg-panel,#1b2330);color:var(--text,#e6edf3);border:1px solid var(--border,#334);border-radius:12px;max-width:480px;width:100%;max-height:92vh;overflow:auto;padding:18px 20px;box-shadow:0 10px 40px #000a">
        <div style="font-weight:700;font-size:1.1em;margin-bottom:10px">🔍 Bild hochskalieren</div>
        <img src="${dataUrl}" style="max-width:100%;max-height:40vh;border-radius:8px;display:block;margin:0 auto 12px">
        <label style="display:block;margin:6px 0 3px">Methode</label>
        <div id="up-mode"></div>
        <div style="opacity:.7;font-size:.85em;margin-top:8px">2×, lange Seite max. 2048 px. „KI-Detail" ergänzt echte Schärfe (lokal über Z-Image, ~30–50 s); „Schnell" vergrößert nur (sofort).</div>
        <div style="display:flex;gap:8px;justify-content:flex-end;margin-top:16px">
          <button id="up-cancel" class="wf-action-btn">Abbrechen</button>
          <button id="up-go" class="wf-action-btn" style="background:var(--accent,#2d6cdf);color:#fff">Hochskalieren</button>
        </div>
      </div>`;
    document.body.appendChild(ov);
    const host = ov.querySelector('#up-mode');
    host.dataset.val = 'ai';
    [['ai', '✨ KI-Detail'], ['fast', '⚡ Schnell (Lanczos)']].forEach(([val, lab]) => {
      const b = document.createElement('button');
      b.type = 'button'; b.textContent = lab; b.dataset.val = val;
      b.style.cssText = 'padding:6px 12px;margin:2px;border-radius:14px;border:1px solid var(--border,#334);background:transparent;color:inherit;cursor:pointer';
      if (val === 'ai') { b.style.background = 'var(--accent,#2d6cdf)'; b.style.color = '#fff'; }
      b.onclick = () => {
        host.dataset.val = val;
        [...host.children].forEach(c => { c.style.background = 'transparent'; c.style.color = 'inherit'; });
        b.style.background = 'var(--accent,#2d6cdf)'; b.style.color = '#fff';
      };
      host.appendChild(b);
    });
    const close = () => ov.remove();
    ov.addEventListener('click', e => { if (e.target === ov) close(); });
    document.addEventListener('keydown', function esc(e) { if (e.key === 'Escape') { close(); document.removeEventListener('keydown', esc); } });
    ov.querySelector('#up-cancel').onclick = close;
    ov.querySelector('#up-go').onclick = () => { const mode = host.dataset.val || 'ai'; close(); _doUpscale(dataUrl, mode); };
  }

  async function _doUpscale(dataUrl, mode) {
    showWelcome(false);
    appendMessage('user', `🔍 Bild hochskalieren (${mode === 'fast' ? 'schnell' : 'KI-Detail'})`);
    const row = appendMessage('assistant', '', [], true);
    const content = row.querySelector('.bubble-content');
    const workingEl = makeWorking('Bild wird hochskaliert',
      { hintAfter: 20, hint: 'Bild wird hochskaliert — die KI-Variante rechnet lokal, das dauert ~30–60 s' }); content.appendChild(workingEl);
    const textEl = document.createElement('div'); textEl.className = 'bubble-text'; content.appendChild(textEl);
    try {
      const resp = await fetch('/api/image/upscale', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ image: dataUrl, factor: 2, mode }),
      });
      workingEl.remove();
      if (!resp.ok) {
        let m = 'HTTP ' + resp.status; try { m = (await resp.json()).detail || m; } catch (_) {}
        textEl.innerHTML = `<em style="color:#ef4444">${escHtml(m)}</em>`; return;
      }
      const data = await resp.json();
      textEl.textContent = `→ ${data.width}×${data.height} px (${data.mode === 'ai' ? 'KI-Detail' : 'schnell'})`
        + (data.note ? ' · ' + data.note : '');
      insertImage(content, data.image);
      const bar = document.createElement('div'); bar.className = 'wf-actions';
      const bMore = document.createElement('button'); bMore.className = 'wf-action-btn'; bMore.textContent = '🔍 nochmal';
      bMore.onclick = () => _openUpscaleForm(data.image, 'upscaled.png');
      bar.appendChild(bMore); content.appendChild(bar);
    } catch (e) {
      workingEl.remove();
      textEl.innerHTML = `<em style="color:#ef4444">Fehlgeschlagen: ${escHtml(e.message)}</em>`;
    }
  }

  // ── Tab-übergreifend: Chat-Ergebnis in einen anderen Tab übernehmen ─────────
  // Erkennt den Wunsch „… in die To-Do-Liste" bzw. „… in den Planer" (natürliche
  // Sprache) und bietet nach der Antwort eine Rückfrage an (wohin übernehmen?).
  let _tabHandoffPending = '';   // '' | 'todo' | 'planner'

  function _planIntent(text) {
    const t = String(text || '');
    if (!/\bplaner\b/i.test(t) && !/\bnetzplan\b/i.test(t)) return false;
    return /\bplaner[-\s]?tab\b/i.test(t)
      || /\b(in|im)\s+(den|dem)?\s*planer\b/i.test(t)
      || /\b(nutze|verwende|benutze|nutzen|verwenden|benutzen|pack[e]?|stell[e]?|leg[e]?)\b[^.\n]{0,22}\bplaner\b/i.test(t)
      || /\bplaner\b[^.\n]{0,22}\b(nutzen|verwenden|benutzen|erstellen|anlegen|nehmen)\b/i.test(t)
      || /\bnetzplan\b/i.test(t);
  }

  function _todoIntent(text) {
    const t = String(text || '');
    return /\b(in|auf|zur?|als)\b[^.\n]{0,25}\b(to-?do|to-?do-?liste|aufgaben-?liste|aufgabenliste)\b/i.test(t)
      || /\b(to-?do|aufgabenliste)\b[^.\n]{0,22}\b(hinzuf[üu]gen|eintragen|aufnehmen|erg[äa]nzen|schreiben|stellen|packen|setzen)\b/i.test(t)
      || /\b(pack|stell|setz|schreib|f[üu]g|leg)[a-z]*\b[^.\n]{0,25}\b(to-?do|aufgabenliste)\b/i.test(t);
  }

  // Listenpunkte aus einer Markdown-Antwort ziehen (Aufzählungen, Tabellen, sonst Zeilen).
  function _parseListItems(md) {
    const lines = String(md || '').split('\n');
    const clean = (s) => String(s || '').replace(/\*\*|__|`/g, '').replace(/\[([^\]]+)\]\([^)]*\)/g, '$1').trim();
    const items = [];
    for (const ln of lines) {
      const t = ln.trim();
      if (!t) continue;
      if (/^\|.*\|$/.test(t)) {                        // Markdown-Tabellenzeile
        if (/^\|[\s:|\-]+\|$/.test(t)) continue;       // Trennzeile ---
        const cells = t.split('|').slice(1, -1).map(clean);
        const first = cells[0] || '';
        if (!first || /^(artikel|item|aufgabe|aufgaben|name|bezeichnung|nr\.?|#|pos\.?|menge|anzahl|✓|erledigt)$/i.test(first)) continue;
        const extra = cells.slice(1).filter(Boolean).join(' · ');
        items.push(extra ? `${first} — ${extra}` : first);
        continue;
      }
      const m = t.match(/^(?:[-*•–▪]|\d+[.)])\s+(.*)$/);  // Aufzählung / nummeriert
      if (m) { const v = clean(m[1]); if (v) items.push(v); }
    }
    if (!items.length) {                                // Rückfall: nicht-leere Zeilen
      for (const ln of lines) {
        const t = ln.trim();
        if (t && !/^#/.test(t) && !/^\|/.test(t)) { const v = clean(t); if (v && v.length < 200) items.push(v); }
      }
    }
    return [...new Set(items)].slice(0, 200);
  }

  async function _openTodoHandoff(md) {
    const parsed = _parseListItems(md);
    if (!parsed.length) { showToast('Keine Listenpunkte in der Antwort erkannt'); return; }
    let projects = [];
    try { const d = await (await fetch('/api/todo/tree')).json(); projects = (d.flat || []).filter(p => p.id && p.id !== 'root'); } catch (_) {}
    const guess = (() => { const h = String(md || '').match(/^#{1,3}\s+(.+)$/m); return ((h ? h[1] : 'Einkaufsliste').replace(/[*`#]/g, '').trim().slice(0, 60)) || 'Einkaufsliste'; })();
    const old = document.getElementById('todo-handoff'); if (old) old.remove();
    const ov = document.createElement('div'); ov.id = 'todo-handoff';
    ov.style.cssText = 'position:fixed;inset:0;z-index:9999;background:rgba(0,0,0,.55);display:flex;align-items:center;justify-content:center;padding:16px';
    const fld = 'width:100%;padding:7px 9px;border-radius:7px;border:1px solid var(--border,#334);background:var(--bg-input,#0e141b);color:var(--text,#e6edf3);box-sizing:border-box';
    const opts = projects.map(p => `<option value="${escHtml(p.id)}">${escHtml(p.name || p.id)}</option>`).join('');
    ov.innerHTML = `
      <div style="background:var(--bg-panel,#1b2330);color:var(--text,#e6edf3);border:1px solid var(--border,#334);border-radius:12px;max-width:560px;width:100%;max-height:92vh;overflow:auto;padding:18px 20px;box-shadow:0 10px 40px #000a">
        <div style="font-weight:700;font-size:1.1em;margin-bottom:4px">✅ In die To-Do-Liste übernehmen</div>
        <div style="opacity:.7;margin-bottom:12px">${parsed.length} Punkt(e) erkannt – wohin damit?</div>
        <label style="display:flex;align-items:center;gap:8px;margin:4px 0;cursor:pointer"><input type="radio" name="th-target" value="new" checked> Neuen Punkt/Projekt anlegen</label>
        <input id="th-name" type="text" style="${fld}" value="${escHtml(guess)}">
        <label style="display:flex;align-items:center;gap:8px;margin:12px 0 4px;cursor:pointer"><input type="radio" name="th-target" value="exist" ${projects.length ? '' : 'disabled'}> Zu bestehendem Projekt ergänzen</label>
        <select id="th-exist" style="${fld}" ${projects.length ? '' : 'disabled'}>${opts || '<option>(keine Projekte)</option>'}</select>
        <label style="display:block;margin:12px 0 3px">Punkte (eine Zeile = ein Punkt, editierbar)</label>
        <textarea id="th-items" rows="8" style="${fld};font-family:inherit"></textarea>
        <div style="display:flex;gap:8px;justify-content:flex-end;margin-top:16px">
          <button id="th-cancel" class="wf-action-btn">Abbrechen</button>
          <button id="th-go" class="wf-action-btn" style="background:var(--accent,#2d6cdf);color:#fff">Übernehmen</button>
        </div>
      </div>`;
    document.body.appendChild(ov);
    ov.querySelector('#th-items').value = parsed.join('\n');
    const close = () => ov.remove();
    ov.addEventListener('click', e => { if (e.target === ov) close(); });
    document.addEventListener('keydown', function esc(e) { if (e.key === 'Escape') { close(); document.removeEventListener('keydown', esc); } });
    ov.querySelector('#th-cancel').onclick = close;
    ov.querySelector('#th-go').onclick = async () => {
      const items = ov.querySelector('#th-items').value.split('\n').map(s => s.trim()).filter(Boolean).map(t => ({ text: t }));
      if (!items.length) { showToast('Keine Punkte angegeben'); return; }
      const target = ov.querySelector('input[name="th-target"]:checked').value;
      const goBtn = ov.querySelector('#th-go'); goBtn.disabled = true; goBtn.textContent = 'Speichere…';
      try {
        let pname;
        if (target === 'new') {
          const name = ov.querySelector('#th-name').value.trim() || 'Liste';
          const created = await (await fetch('/api/todo/projects', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ name }) })).json();
          await fetch(`/api/todo/projects/${created.id}`, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ type: 'frei', title: name, items }) });
          pname = name;
        } else {
          const pid = ov.querySelector('#th-exist').value;
          const proj = await (await fetch(`/api/todo/projects/${pid}`)).json();
          pname = proj.title || proj.name || 'Projekt';
          const merged = (proj.items || []).concat(items);
          // Vorhandenen Header/Graph erhalten: Titel/Typ/Beteiligte/Positionen mitschicken.
          const body = Object.assign({}, proj, {
            items: merged,
            project_id: proj.project_id || proj.project_ref || '',
            positions: proj.positions || (proj.settings && proj.settings.positions) || {},
          });
          await fetch(`/api/todo/projects/${pid}`, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
        }
        close();
        if (typeof Todo !== 'undefined' && Todo.refresh) Todo.refresh();
        showToast(`✓ ${items.length} Punkt(e) in „${pname}" übernommen`);
        const row = appendMessage('assistant', '', [], false);
        const c = row.querySelector('.bubble-content');
        c.textContent = `✅ ${items.length} Punkt(e) in die To-Do-Liste „${pname}" übernommen.`;
        const bar = document.createElement('div'); bar.className = 'wf-actions';
        const b = document.createElement('button'); b.className = 'wf-action-btn'; b.textContent = '✅ To-Do öffnen';
        b.onclick = () => { if (typeof switchTab === 'function') switchTab('todo'); };
        bar.appendChild(b); c.appendChild(bar);
      } catch (e) {
        goBtn.disabled = false; goBtn.textContent = 'Übernehmen';
        showToast('Fehlgeschlagen: ' + (e && e.message || e));
      }
    };
  }

  // Chat-Ergebnis als Projektplan in den Planer übernehmen (nutzt Planner.openFromText
  // = Dokument→Plan, leitet Vorgänge/Abhängigkeiten/kritischen Pfad ab).
  function _openPlanHandoff(md) {
    const text = String(md || '').trim();
    if (!text) { showToast('Kein Inhalt für den Planer'); return; }
    const guess = (() => { const h = text.match(/^#{1,3}\s+(.+)$/m); return ((h ? h[1] : 'Plan').replace(/[*`#]/g, '').trim().slice(0, 60)) || 'Plan'; })();
    const old = document.getElementById('plan-handoff'); if (old) old.remove();
    const ov = document.createElement('div'); ov.id = 'plan-handoff';
    ov.style.cssText = 'position:fixed;inset:0;z-index:9999;background:rgba(0,0,0,.55);display:flex;align-items:center;justify-content:center;padding:16px';
    const fld = 'width:100%;padding:7px 9px;border-radius:7px;border:1px solid var(--border,#334);background:var(--bg-input,#0e141b);color:var(--text,#e6edf3);box-sizing:border-box';
    ov.innerHTML = `
      <div style="background:var(--bg-panel,#1b2330);color:var(--text,#e6edf3);border:1px solid var(--border,#334);border-radius:12px;max-width:520px;width:100%;max-height:92vh;overflow:auto;padding:18px 20px;box-shadow:0 10px 40px #000a">
        <div style="font-weight:700;font-size:1.1em;margin-bottom:4px">🗂️ In den Planer übernehmen</div>
        <div style="opacity:.7;margin-bottom:12px">Aus dem Ergebnis wird ein <b>Projektplan</b> abgeleitet (Vorgänge, Abhängigkeiten, kritischer Pfad) und im Planer-Tab geöffnet.</div>
        <label style="display:block;margin:6px 0 3px">Plan-Name</label>
        <input id="ph-name" type="text" style="${fld}" value="${escHtml(guess)}">
        <div style="display:flex;gap:8px;justify-content:flex-end;margin-top:16px">
          <button id="ph-cancel" class="wf-action-btn">Abbrechen</button>
          <button id="ph-go" class="wf-action-btn" style="background:var(--accent,#2d6cdf);color:#fff">In den Planer</button>
        </div>
      </div>`;
    document.body.appendChild(ov);
    const close = () => ov.remove();
    ov.addEventListener('click', e => { if (e.target === ov) close(); });
    document.addEventListener('keydown', function esc(e) { if (e.key === 'Escape') { close(); document.removeEventListener('keydown', esc); } });
    ov.querySelector('#ph-cancel').onclick = close;
    ov.querySelector('#ph-go').onclick = () => {
      const name = ov.querySelector('#ph-name').value.trim() || 'Plan';
      close();
      if (typeof Planner !== 'undefined' && Planner.openFromText) {
        showToast('🗂️ Plan wird im Planer erstellt…');
        Planner.openFromText(text, name);
      } else showToast('Planer nicht verfügbar');
    };
    setTimeout(() => { const t = ov.querySelector('#ph-name'); if (t) { t.focus(); t.select(); } }, 30);
  }

  // Ziel-Tabs für die Übernahme. To-Do/Planer haben eigene Rückfragen; Code lädt in die
  // Code-Werkstatt; die übrigen füllen ihr Haupteingabefeld vor (bzw. legen den Text in die
  // Zwischenablage, wenn der Tab formularbasiert ist) und wechseln dorthin. Keys = `data-tab`.
  const _HANDOFF_TARGETS = [
    { key: 'todo', label: '✅ To-Do' },
    { key: 'planner', label: '🗂 Planer' },
    { key: 'ide', label: '💻 Code' },
    { key: 'mathe', label: '🧮 Mathe', input: 'mathe-input' },
    { key: 'medizin', label: '🩺 Medizin', input: 'medizin-input' },
    { key: 'varianten', label: '⚖ Varianten', input: 'var-problem' },
    { key: 'pairwise', label: '⚖ Paarvergleich starten' },
    { key: 'compare', label: '📊 Excel-Vergleich' },
    { key: 'morph', label: '🧩 Morph-Kasten', input: 'morph-problem' },
    { key: 'patente', label: '📜 Patente', input: 'pat-search-term' },
    { key: 'rfq', label: '📩 Anfrage', input: 'rfq-chat-input' },
    { key: 'rechnung', label: '🧾 Rechnung', copy: true },
    { key: 'zeugnis', label: '📄 Zeugnis', copy: true },
  ];

  // Ziel-spezifische Aktionen für die „was soll dort passieren?"-Rückfrage. Nur Tabs
  // mit sinnvoller Aufbereitung (die Ziele mit eigenem Dialog – todo/planner/pairwise/
  // ide/compare – laufen direkt weiter).
  const _HANDOFF_ACTIONS = {
    patente: ['🔎 Recherche-Suchbegriff', '📝 als Patent-Entwurf', '🆕 Neuheitsanalyse'],
    mathe: ['🧮 Aufgabe lösen', '📐 Schritt für Schritt'],
    medizin: ['🩺 Auswerten', '📋 Zusammenfassen'],
    varianten: ['⚖ Entscheidung bewerten'],
    morph: ['🧩 Problem zerlegen'],
    rfq: ['📩 Als Anfrage aufbereiten'],
    rechnung: ['🧾 Positionen extrahieren'],
    zeugnis: ['📄 Zeugnis-Angaben'],
  };

  function _handoffToTab(key, md) {
    const text = String(md || '').trim();
    if (!text) { showToast('Nichts zu übernehmen'); return; }
    if (key === 'todo') return _openTodoHandoff(text);
    if (key === 'planner') return _openPlanHandoff(text);
    if (key === 'pairwise') {
      if (typeof Varianten !== 'undefined' && Varianten.openStepwise) {
        const nm = text.slice(0, 60).replace(/[^\wäöüÄÖÜß \-]/g, '').trim() || 'Entscheidung';
        Varianten.openStepwise({ name: nm, title: text.slice(0, 120) });
        showToast('⚖ Schritt-für-Schritt Paarvergleich gestartet');
      } else showToast('Varianten-Modul nicht geladen');
      return;
    }
    if (key === 'ide') {
      if (typeof CodeIDE !== 'undefined' && CodeIDE.loadFromChat) CodeIDE.loadFromChat(text, 'chat');
      else if (typeof CodeWorkspace !== 'undefined' && CodeWorkspace.loadFromChat) CodeWorkspace.loadFromChat(text, 'chat');
      if (typeof switchTab === 'function') switchTab('ide');
      showToast('✓ in den Code-Tab übernommen'); return;
    }
    // Tabs mit Aufbereitung: erst „was soll passieren?" fragen und den Inhalt zielgerecht
    // umformen (z. B. Patente → Recherche-Suchbegriff), dann übernehmen.
    if (_HANDOFF_ACTIONS[key]) return _openHandoffPrepare(key, text);
    _handoffRoute(key, text);
  }

  // Platziert den (ggf. aufbereiteten) Text im Ziel-Tab (Feld füllen / Zwischenablage).
  function _handoffRoute(key, text, action) {
    const t = _HANDOFF_TARGETS.find(x => x.key === key);
    const name = t ? t.label.replace(/^\S+\s/, '') : key;
    if (typeof switchTab === 'function') switchTab(key);
    if (t && t.copy) {
      if (navigator.clipboard) { try { navigator.clipboard.writeText(text); } catch (_) {} }
      showToast(`${name}-Tab geöffnet – aufbereiteter Text in der Zwischenablage (einfügen)`);
      return;
    }
    const el = t && t.input ? document.getElementById(t.input) : null;
    if (el) {
      el.value = text;
      el.dispatchEvent(new Event('input', { bubbles: true }));
      try { el.focus(); } catch (_) {}
      // Patente-Recherche: wenn gewünscht, die Suche gleich auslösen.
      if (key === 'patente' && /recherche|suchbegriff/i.test(action || '')) {
        const go = document.getElementById('pat-search-btn') || document.getElementById('btn-pat-search');
        if (go) { try { go.click(); } catch (_) {} }
      }
      showToast(`✓ aufbereitet in den ${name}-Tab übernommen`);
    } else {
      showToast(`${name}-Tab geöffnet`);
    }
  }

  function _openHandoffPrepare(key, md) {
    const t = _HANDOFF_TARGETS.find(x => x.key === key);
    const name = t ? t.label : key;
    const actions = _HANDOFF_ACTIONS[key] || [];
    const old = document.getElementById('handoff-prep'); if (old) old.remove();
    const ov = document.createElement('div'); ov.id = 'handoff-prep';
    ov.style.cssText = 'position:fixed;inset:0;z-index:10001;background:rgba(0,0,0,.55);display:flex;align-items:center;justify-content:center;padding:16px';
    const fld = 'width:100%;padding:7px 9px;border-radius:7px;border:1px solid var(--border,#334);background:var(--bg-input,#0e141b);color:var(--text,#e6edf3);box-sizing:border-box';
    ov.innerHTML = `
      <div style="background:var(--bg-panel,#1b2330);color:var(--text,#e6edf3);border:1px solid var(--border,#334);border-radius:12px;max-width:520px;width:100%;padding:18px 20px;box-shadow:0 10px 40px #000a">
        <div style="font-weight:700;font-size:1.08em;margin-bottom:2px">→ ${escHtml(name)}: Was soll dort passieren?</div>
        <div style="opacity:.7;margin-bottom:12px;font-size:.9em">Der Inhalt wird für den Ziel-Tab passend aufbereitet.</div>
        <div id="hp-actions" style="display:flex;flex-wrap:wrap;gap:6px;margin-bottom:10px">
          ${actions.map((a, i) => `<button type="button" class="hp-act${i === 0 ? ' active' : ''}" data-a="${escHtml(a)}" style="padding:5px 10px;border-radius:14px;border:1px solid var(--border,#334);background:${i === 0 ? 'var(--accent,#2d6cdf)' : 'transparent'};color:${i === 0 ? '#fff' : 'inherit'};cursor:pointer;font-size:.9em">${escHtml(a)}</button>`).join('')}
        </div>
        <label style="display:block;margin:2px 0 3px;font-size:.9em">Zusatzwunsch (optional)</label>
        <input id="hp-extra" type="text" style="${fld}" placeholder="z. B. Fokus, Sprache, Umfang …">
        <div style="display:flex;gap:8px;justify-content:flex-end;margin-top:16px">
          <button id="hp-cancel" class="wf-action-btn">Abbrechen</button>
          <button id="hp-go" class="wf-action-btn" style="background:var(--accent,#2d6cdf);color:#fff">Aufbereiten &amp; übernehmen</button>
        </div>
        <div id="hp-status" style="margin-top:8px;font-size:.88em;color:var(--text-muted,#8b98a5)"></div>
      </div>`;
    document.body.appendChild(ov);
    let _act = actions[0] || '';
    ov.querySelectorAll('.hp-act').forEach(b => b.addEventListener('click', () => {
      _act = b.dataset.a;
      ov.querySelectorAll('.hp-act').forEach(x => { x.classList.remove('active'); x.style.background = 'transparent'; x.style.color = 'inherit'; });
      b.classList.add('active'); b.style.background = 'var(--accent,#2d6cdf)'; b.style.color = '#fff';
    }));
    const close = () => ov.remove();
    ov.addEventListener('click', e => { if (e.target === ov) close(); });
    ov.querySelector('#hp-cancel').onclick = close;
    ov.querySelector('#hp-go').onclick = async () => {
      const extra = ov.querySelector('#hp-extra').value.trim();
      const action = (_act + (extra ? ' — ' + extra : '')).trim();
      const go = ov.querySelector('#hp-go'); go.disabled = true;
      ov.querySelector('#hp-status').textContent = '⏳ Inhalt wird aufbereitet …';
      let prepared = md;
      try {
        const r = await fetch('/api/handoff/prepare', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ target: key, content: md, action }),
        });
        if (r.ok) {
          const s = await r.json();
          prepared = s.prepared || md;
          if (s.tokens && typeof TokenMeter !== 'undefined') TokenMeter.add(s.tokens, 'Handoff-Aufbereitung');
        }
      } catch (_) {}
      close();
      _handoffRoute(key, prepared, action);
    };
  }

  function _openHandoffMenu(md, anchor) {
    const old = document.getElementById('handoff-menu'); if (old) old.remove();
    const menu = document.createElement('div'); menu.id = 'handoff-menu';
    const r = anchor.getBoundingClientRect();
    menu.style.cssText = `position:fixed;z-index:10000;left:${Math.round(r.left)}px;top:${Math.round(r.bottom + 4)}px;background:var(--bg-panel,#1b2330);color:var(--text,#e6edf3);border:1px solid var(--border,#334);border-radius:10px;box-shadow:0 8px 30px #000a;padding:6px;max-height:60vh;overflow:auto;min-width:190px`;
    _HANDOFF_TARGETS.forEach(t => {
      const b = document.createElement('button');
      b.textContent = t.label; b.type = 'button';
      b.style.cssText = 'display:block;width:100%;text-align:left;padding:7px 10px;border:none;background:transparent;color:inherit;cursor:pointer;border-radius:6px;font-size:.95em';
      b.onmouseenter = () => { b.style.background = 'var(--accent,#2d6cdf)'; b.style.color = '#fff'; };
      b.onmouseleave = () => { b.style.background = 'transparent'; b.style.color = 'inherit'; };
      b.onclick = () => { menu.remove(); _handoffToTab(t.key, md); };
      menu.appendChild(b);
    });
    document.body.appendChild(menu);
    // Im Viewport halten: der „senden an…"-Button steht meist am unteren Chat-Rand,
    // deshalb je nach Platz nach OBEN oder UNTEN klappen und die Höhe auf den
    // sichtbaren Bereich begrenzen (scrollbar) — sonst sind die unteren Einträge
    // unter dem Fensterrand nicht erreichbar.
    const margin = 8;
    const vh = window.innerHeight, vw = window.innerWidth;
    const spaceBelow = vh - r.bottom - margin;
    const spaceAbove = r.top - margin;
    const openUp = menu.offsetHeight > spaceBelow && spaceAbove > spaceBelow;
    const avail = openUp ? spaceAbove : spaceBelow;
    menu.style.maxHeight = Math.max(140, Math.min(menu.offsetHeight, avail)) + 'px';
    const mh = menu.offsetHeight;   // nach der Höhenbegrenzung neu messen
    let top = openUp ? (r.top - mh - 4) : (r.bottom + 4);
    top = Math.max(margin, Math.min(top, vh - mh - margin));
    let left = Math.round(r.left);
    const mw = menu.offsetWidth;
    if (left + mw > vw - margin) left = Math.max(margin, vw - mw - margin);
    menu.style.top = Math.round(top) + 'px';
    menu.style.left = left + 'px';
    setTimeout(() => {
      const close = (e) => { if (!menu.contains(e.target)) { menu.remove(); document.removeEventListener('click', close); } };
      document.addEventListener('click', close);
    }, 10);
  }

  // Generischer Tab-Intent: „<Tab>-Tab" (z. B. „mathe-tab", „code tab") → Ziel-Key.
  function _tabIntent(text) {
    const m = String(text || '').match(/\b(code|mathe|medizin|varianten|morph\w*|patent\w*|anfrage|rechnung|angebot|zeugnis\w*)[-\s]?tab\b/i);
    if (!m) return '';
    const w = m[1].toLowerCase();
    if (w.startsWith('code')) return 'ide';
    if (w.startsWith('patent')) return 'patente';
    if (w.startsWith('morph')) return 'morph';
    if (w === 'anfrage') return 'rfq';
    if (w === 'angebot' || w === 'rechnung') return 'rechnung';
    if (w.startsWith('zeugnis')) return 'zeugnis';
    return w;   // mathe, medizin, varianten
  }

  // ── /bild — Bildgenerierung (lokal SD-WebUI oder API) ───────────────────────
  // „/bildhelp" (Aliase /imagehelp, /imghelp) öffnet den geführten Dialog; „/bild
  // <Beschreibung>" erzeugt direkt. Ein leeres „/bild" öffnet ebenfalls den Dialog.
  function _parseBild(text) {
    const help = text.match(/^\/(bild|image|img)help\b\s*([\s\S]*)$/i);
    if (help) return { help: true, prompt: (help[2] || '').trim() };
    const m = text.match(/^\/(bild|image|img)\b\s*([\s\S]*)$/i);
    if (!m) return null;
    const p = (m[2] || '').trim();
    return p ? { help: false, prompt: p } : { help: true, prompt: '' };
  }

  // Erzeugt ein Bild und zeigt es im Verlauf an (außerhalb der LLM-Historie, wie
  // runSearch). opts = { size, negative }.
  // Aktuelle Unterhaltung serverseitig speichern (für Abläufe, die nicht über
  // /api/chat laufen, z. B. Bildgenerierung) – sonst fehlen sie in der Liste.
  async function _persistConversation() {
    if (!currentConvId || !messages.length) return;
    try {
      await fetch(`/api/conversations/${currentConvId}/save`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ messages: messages.filter(m => m.role !== 'system') }),
      });
    } catch (_) {}
  }

  // ── /excelvergleich — Excel-Vergleich aus dem Chat (Overlay #compare-help) ──
  // Zwei Tabellen laden, Blatt + Schlüsselspalte wählen; Diff + KI-Bewertung
  // rendern direkt in einer Chat-Blase. Nutzt die geteilten Compare-Helfer, damit
  // es auch im Assistent-Modus (Tabs ausgeblendet) funktioniert.
  function _parseExcelCompare(text) {
    const m = text.match(/^\/(excelvergleich|xlsvergleich|excel|tabellenvergleich)\b\s*([\s\S]*)$/i);
    return m ? {} : null;
  }

  let _cmpHelpWired = false;
  const _cmpSideA = {}, _cmpSideB = {};

  function _cmpFillSide(pfx, side, data) {
    document.getElementById('cmphelp-' + pfx + '-meta').textContent =
      `${data.filename} · ${data.n_rows} Zeilen · Blatt „${data.sheet}"`;
    const ssel = document.getElementById('cmphelp-sheet-' + pfx);
    ssel.innerHTML = (data.sheets || []).map(s => `<option ${s === data.sheet ? 'selected' : ''}>${escHtml(s)}</option>`).join('');
    const ksel = document.getElementById('cmphelp-key-' + pfx);
    ksel.innerHTML = (data.headers || []).map((h, i) => `<option value="${i}">${escHtml(h || ('Spalte ' + (i + 1)))}</option>`).join('');
    side.file_id = data.file_id; side.filename = data.filename; side.sheet = data.sheet; side.key = 0;
  }

  async function _cmpRead(pfx, side, useSelectedSheet) {
    if (typeof Compare === 'undefined') { showToast('Vergleichsmodul nicht geladen'); return; }
    const fileEl = document.getElementById('cmphelp-file-' + pfx);
    const f = (fileEl.files && fileEl.files[0]) || side.file;
    if (!f) { showToast('Bitte Datei ' + pfx.toUpperCase() + ' wählen'); return; }
    side.file = f;
    const sheet = useSelectedSheet ? (document.getElementById('cmphelp-sheet-' + pfx).value || '') : '';
    document.getElementById('cmphelp-' + pfx + '-meta').textContent = 'lese…';
    try {
      const data = await Compare.preview(f, sheet, 0);
      _cmpFillSide(pfx, side, data);
    } catch (e) { document.getElementById('cmphelp-' + pfx + '-meta').textContent = 'Fehler: ' + e.message; }
  }

  function runExcelCompare() {
    const ov = document.getElementById('compare-help');
    if (!ov) return;
    if (!_cmpHelpWired) {
      _cmpHelpWired = true;
      const close = () => { ov.style.display = 'none'; };
      document.getElementById('cmphelp-close').addEventListener('click', close);
      document.getElementById('cmphelp-cancel').addEventListener('click', close);
      ov.addEventListener('click', e => { if (e.target === ov) close(); });
      document.addEventListener('keydown', e => { if (e.key === 'Escape' && ov.style.display !== 'none') close(); });
      document.getElementById('cmphelp-read-a').addEventListener('click', () => _cmpRead('a', _cmpSideA, false));
      document.getElementById('cmphelp-read-b').addEventListener('click', () => _cmpRead('b', _cmpSideB, false));
      document.getElementById('cmphelp-sheet-a').addEventListener('change', () => _cmpRead('a', _cmpSideA, true));
      document.getElementById('cmphelp-sheet-b').addEventListener('change', () => _cmpRead('b', _cmpSideB, true));
      document.getElementById('cmphelp-go').addEventListener('click', () => {
        if (!_cmpSideA.file_id || !_cmpSideB.file_id) { showToast('Bitte beide Dateien einlesen'); return; }
        const params = {
          file_id_a: _cmpSideA.file_id, sheet_a: document.getElementById('cmphelp-sheet-a').value || _cmpSideA.sheet,
          header_row_a: 0, key_a: parseInt(document.getElementById('cmphelp-key-a').value || '0', 10) || 0,
          file_id_b: _cmpSideB.file_id, sheet_b: document.getElementById('cmphelp-sheet-b').value || _cmpSideB.sheet,
          header_row_b: 0, key_b: parseInt(document.getElementById('cmphelp-key-b').value || '0', 10) || 0,
          model: (typeof Profile !== 'undefined' ? Profile.modelFor('general') : '') || undefined,
        };
        const labelA = _cmpSideA.filename || 'A', labelB = _cmpSideB.filename || 'B';
        close();
        _runExcelCompareStream(params, labelA, labelB);
      });
    }
    ['a', 'b'].forEach(p => {
      document.getElementById('cmphelp-sheet-' + p).innerHTML = '';
      document.getElementById('cmphelp-key-' + p).innerHTML = '';
      document.getElementById('cmphelp-' + p + '-meta').textContent = '';
    });
    _cmpSideA.file_id = ''; _cmpSideA.file = null; _cmpSideB.file_id = ''; _cmpSideB.file = null;
    ov.style.display = 'flex';
  }

  async function _runExcelCompareStream(params, labelA, labelB) {
    if (typeof Compare === 'undefined') return;
    if (isStreaming) { showToast('Bitte warten, bis die laufende Antwort fertig ist'); return; }
    showWelcome(false); isStreaming = true; setBtnSendState(false);
    appendMessage('user', `📊 Excel-Vergleich: ${labelA} ↔ ${labelB}`);
    if (!currentConvId) currentConvId = `conv_${Date.now()}`;
    const row = appendMessage('assistant', '', [], true);
    const content = row.querySelector('.bubble-content');
    const diffEl = document.createElement('div');
    const evalEl = document.createElement('div'); evalEl.className = 'bubble-text';
    evalEl.appendChild(makeWorking('🔍 vergleicht'));
    content.appendChild(diffEl); content.appendChild(evalEl); scrollToBottom();
    let evalText = '';
    await Compare.runStream(params, {
      onDiff: (diff) => { diffEl.innerHTML = Compare.renderDiffHtml(diff); evalEl.innerHTML = ''; evalEl.appendChild(makeWorking('🧠 KI bewertet')); scrollToBottom(); },
      onText: (chunk) => { evalText += chunk; evalEl.innerHTML = (typeof marked !== 'undefined') ? marked.parse(evalText) : escHtml(evalText); scrollToBottom(); },
      onDone: (evaluation, tokens) => {
        evalEl.innerHTML = (typeof marked !== 'undefined') ? marked.parse(evaluation || evalText) : escHtml(evaluation || evalText);
        if (tokens && typeof TokenMeter !== 'undefined') TokenMeter.add(tokens, 'Excel-Vergleich');
        messages.push({ role: 'user', content: `📊 Excel-Vergleich: ${labelA} ↔ ${labelB}` });
        messages.push({ role: 'assistant', content: '📊 Excel-Vergleich:\n\n' + (evaluation || evalText) });
        _persistConversation(); loadConversationList();
        isStreaming = false; setBtnSendState(true);
      },
      onError: (msg) => { evalEl.innerHTML = `<em style="color:#ef4444">Vergleich fehlgeschlagen: ${escHtml(msg)}</em>`; isStreaming = false; setBtnSendState(true); },
    });
  }

  // ── Assistent-Modus: zwei Tabellen erkannt → zellenweisen Vergleich anbieten ──
  // Dezentes Angebot (wie _offerResearch); auf Zusage folgt ein kompaktes Inline-
  // Formular (Schlüsselspalten + KI-Schalter) und der zellenweise Vergleich direkt
  // in der Chat-Blase. Nutzt die geteilten Compare-Helfer (auch im Assistent-Modus).
  function _offerCompare(files, origText) {
    showWelcome(false);
    const row = appendMessage('assistant', '', [], true);
    const c = row.querySelector('.bubble-content');
    const box = document.createElement('div');
    box.className = 'research-offer';
    box.innerHTML = `📊 Du hast zwei Tabellen angehängt (<b>${escHtml(files[0].filename)}</b> und `
      + `<b>${escHtml(files[1].filename)}</b>). Sie <b>zellenweise vergleichen</b>? `
      + '<button class="research-offer-yes">Vergleichen</button> '
      + '<button class="research-offer-no">Normal senden</button>';
    c.appendChild(box);
    box.querySelector('.research-offer-yes').addEventListener('click', () => { row.remove(); _startInlineCompare(files); });
    box.querySelector('.research-offer-no').addEventListener('click', () => {
      row.remove(); _bypassCompareOffer = true; sendMessage();
    });
    scrollToBottom();
  }

  async function _startInlineCompare(files) {
    if (typeof Compare === 'undefined') { showToast('Vergleichsmodul nicht geladen'); return; }
    if (isStreaming) { showToast('Bitte warten, bis die laufende Antwort fertig ist'); return; }
    showWelcome(false);
    const row = appendMessage('assistant', '', [], true);
    const c = row.querySelector('.bubble-content');
    c.appendChild(makeWorking('lese Tabellen'));
    let da, db;
    try {
      da = await Compare.preview(files[0].id, '', 0);
      db = await Compare.preview(files[1].id, '', 0);
    } catch (e) { c.innerHTML = `<em style="color:#ef4444">Konnte Tabellen nicht lesen: ${escHtml(e.message)}</em>`; return; }
    const opt = (arr) => (arr || []).map((h, i) => `<option value="${i}">${escHtml(h || ('Spalte ' + (i + 1)))}</option>`).join('');
    c.innerHTML =
      `<div style="font-size:13px;margin-bottom:6px">📊 <b>${escHtml(da.filename)}</b> ↔ <b>${escHtml(db.filename)}</b></div>`
      + '<div style="display:flex;gap:10px;flex-wrap:wrap;align-items:flex-end;margin-bottom:8px">'
      + `<label style="font-size:12px">Schlüssel A<br><select id="cmpinl-key-a">${opt(da.headers)}</select></label>`
      + `<label style="font-size:12px">Schlüssel B<br><select id="cmpinl-key-b">${opt(db.headers)}</select></label>`
      + '<label style="font-size:12px;display:flex;align-items:center;gap:4px"><input type="checkbox" id="cmpinl-ki" checked> KI-Bewertung je geänderte Zelle</label>'
      + '<button id="cmpinl-go" class="export-btn var-ai" style="font-size:12px">🔍 Vergleichen</button>'
      + '</div><div id="cmpinl-out"></div>';
    c.querySelector('#cmpinl-go').addEventListener('click', () => {
      const ka = parseInt(c.querySelector('#cmpinl-key-a').value || '0', 10) || 0;
      const kb = parseInt(c.querySelector('#cmpinl-key-b').value || '0', 10) || 0;
      const kiOn = c.querySelector('#cmpinl-ki').checked;
      const keyNameA = (da.headers[ka] || '').trim();
      const nameSet = {}; (da.headers || []).forEach(h => nameSet[String(h || '').trim()] = true);
      const common = (db.headers || []).map(h => String(h || '').trim()).filter(n => n && nameSet[n] && n !== keyNameA);
      const columns = common.map(n => ({ name: n, mode: kiOn ? 'logic_llm' : 'logic', metric: 'nospace' }));
      const params = {
        file_id_a: files[0].id, sheet_a: da.sheet, header_row_a: 0, key_a: ka,
        file_id_b: files[1].id, sheet_b: db.sheet, header_row_b: 0, key_b: kb,
        columns, model: (typeof Profile !== 'undefined' ? Profile.modelFor('general') : '') || undefined,
      };
      _runInlineCompare(params, c.querySelector('#cmpinl-out'), da.filename, db.filename);
    });
    scrollToBottom();
  }

  async function _runInlineCompare(params, out, labelA, labelB) {
    isStreaming = true; setBtnSendState(false);
    if (!currentConvId) currentConvId = `conv_${Date.now()}`;
    out.innerHTML = ''; out.appendChild(makeWorking('🔍 vergleicht zellenweise'));
    let meta = null; const cells = []; const cmap = {}; let tmr = 0;
    const render = () => { out.innerHTML = Compare.renderCellsHtml(meta, cells, { onlyChanges: true }); };
    const sched = () => { if (tmr) return; tmr = setTimeout(() => { tmr = 0; render(); }, 300); };
    await Compare.runCellStream(params, {
      onMeta: (ev) => { meta = ev; render(); },
      onCell: (ev) => { const k = ev.key + ' ' + ev.column; if (!(k in cmap)) { cmap[k] = cells.length; cells.push(ev); } else cells[cmap[k]] = ev; sched(); },
      onDone: (ev) => {
        render();
        if (ev.tokens && typeof TokenMeter !== 'undefined') TokenMeter.add(ev.tokens, 'Excel-Vergleich');
        const changed = cells.filter(x => x.verdict === 'changed').length;
        messages.push({ role: 'user', content: `📊 Excel-Vergleich: ${labelA} ↔ ${labelB}` });
        messages.push({ role: 'assistant', content: `📊 Zellenweiser Vergleich abgeschlossen: ${changed} geänderte Zellen von ${cells.length} verglichenen.` });
        _persistConversation(); loadConversationList();
        isStreaming = false; setBtnSendState(true);
      },
      onError: (msg) => { out.innerHTML = `<em style="color:#ef4444">Vergleich fehlgeschlagen: ${escHtml(msg)}</em>`; isStreaming = false; setBtnSendState(true); },
    });
  }

  // ── /paarvergleich — schrittweiser Paarvergleich (Varianten-Overlay) ──────────
  function _parsePairwise(text) {
    const m = text.match(/^\/(paarvergleich|entscheidung|ahp)\b\s*([\s\S]*)$/i);
    if (!m) return null;
    return { topic: (m[2] || '').trim() };
  }

  function runPairwise(topic) {
    if (typeof Varianten === 'undefined' || !Varianten.openStepwise) { showToast('Varianten-Modul nicht geladen'); return; }
    const name = (topic || '').trim() || ('Entscheidung ' + new Date().toLocaleDateString('de-DE'));
    const safe = name.replace(/[^\wäöüÄÖÜß \-]/g, '').slice(0, 60).trim() || 'Entscheidung';
    Varianten.openStepwise({
      name: safe, title: name,
      onDone: (data) => {
        const r = (data && data.result) || {};
        const weights = r.weights || [];
        const crit = data.criteria || [];
        showWelcome(false);
        appendMessage('user', '⚖ Paarvergleich: ' + name);
        if (!currentConvId) currentConvId = `conv_${Date.now()}`;
        const row = appendMessage('assistant', '', [], true);
        const content = row.querySelector('.bubble-content');
        const el = document.createElement('div'); el.className = 'bubble-text';
        let md = `**Gewichtung „${name}"**\n\n`;
        if (weights.length && crit.length) {
          const order = crit.map((c, i) => ({ name: c.name || '?', w: weights[i] || 0 })).sort((a, b) => b.w - a.w);
          order.forEach((o, i) => { md += `${i + 1}. ${o.name} — ${(o.w * 100).toFixed(0)}%\n`; });
        } else { md += '_(noch keine Gewichte — Merkmale hinzufügen und bewerten)_\n'; }
        if (r.cr != null && crit.length >= 3) md += `\nKonsistenz CR = ${r.cr.toFixed(2)} ${r.consistent !== false ? '✓' : '⚠ zu inkonsistent'}`;
        md += `\n\n_Weiter im Varianten-Tab: Varianten hinzufügen und bewerten._`;
        el.innerHTML = (typeof marked !== 'undefined') ? marked.parse(md) : escHtml(md).replace(/\n/g, '<br>');
        content.appendChild(el);
        messages.push({ role: 'user', content: '⚖ Paarvergleich: ' + name });
        messages.push({ role: 'assistant', content: md });
        _persistConversation(); loadConversationList();
      },
    });
  }

  // ── /musik — algorithmischer Musik-Generator (kein LLM/GPU, tools/music.py) ──
  function _parseMusik(text) {
    const m = text.match(/^\/(musik|music|song)\b\s*([\s\S]*)$/i);
    if (!m) return null;
    return { description: (m[2] || '').trim() };
  }

  async function runMusik(description) {
    description = (description || '').trim();
    if (isStreaming) { showToast('Bitte warten, bis die laufende Antwort fertig ist'); return; }
    showWelcome(false); isStreaming = true; setBtnSendState(false);
    appendMessage('user', '🎵 ' + (description || 'Musik'));
    if (!currentConvId) currentConvId = `conv_${Date.now()}`;
    const row = appendMessage('assistant', '', [], true);
    const content = row.querySelector('.bubble-content');
    const textEl = document.createElement('div'); textEl.className = 'bubble-text';
    textEl.appendChild(makeWorking('🎵 Musik wird erzeugt'));
    content.appendChild(textEl); scrollToBottom();
    try {
      const resp = await fetch('/api/music/generate', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ description }),
      });
      if (!resp.ok) {
        let d = 'HTTP ' + resp.status; try { d = (await resp.json()).detail || d; } catch (_) {}
        textEl.innerHTML = `<em style="color:#ef4444">Musik fehlgeschlagen: ${escHtml(d)}</em>`; return;
      }
      const data = await resp.json();
      textEl.textContent = `🎵 ${data.style} · ${data.key} · ${data.tempo} BPM · ${data.seconds}s`;
      const wrap = document.createElement('div'); wrap.style.cssText = 'margin:8px 0';
      const audio = document.createElement('audio'); audio.controls = true; audio.src = data.audio;
      audio.style.cssText = 'width:min(420px,100%);display:block';
      wrap.appendChild(audio);
      const dl = document.createElement('a'); dl.href = data.audio; dl.download = 'musik_' + Date.now() + '.wav';
      dl.textContent = '💾 speichern';
      dl.style.cssText = 'font-size:12px;color:var(--accent,#3b76ba);text-decoration:none;display:inline-block;margin-top:4px';
      wrap.appendChild(dl);
      content.appendChild(wrap); scrollToBottom();
      // In den Verlauf + persistieren (leichter Marker, nicht die Audiodaten).
      messages.push({ role: 'user', content: '🎵 ' + (description || 'Musik') });
      messages.push({ role: 'assistant', content: `🎵 Musik erzeugt (${data.style}, ${data.key}, ${data.tempo} BPM, ${data.seconds}s)` });
      _persistConversation(); loadConversationList();
    } catch (e) {
      textEl.innerHTML = `<em style="color:#ef4444">Musik fehlgeschlagen: ${escHtml(e.message)}</em>`;
    } finally {
      isStreaming = false; setBtnSendState(true);
    }
  }

  async function runBild(prompt, opts) {
    prompt = (prompt || '').trim();
    opts = opts || {};
    if (!prompt) { showToast('Bitte eine Bildbeschreibung angeben'); return; }
    if (isStreaming) { showToast('Bitte warten, bis die laufende Antwort fertig ist'); return; }
    showWelcome(false);
    isStreaming = true;
    setBtnSendState(false);

    appendMessage('user', '🎨 ' + prompt);
    if (!currentConvId) currentConvId = `conv_${Date.now()}`;

    const row = appendMessage('assistant', '', [], true);
    const content = row.querySelector('.bubble-content');
    const textEl = document.createElement('div');
    textEl.className = 'bubble-text';
    textEl.appendChild(makeWorking('🎨 Bild wird erzeugt (das kann etwas dauern)', {
      hintAfter: 20,
      hint: '🎨 Bild wird erzeugt — beim ersten Mal lädt das lokale Modell (Z-Image), das dauert ~30–60 s',
    }));
    content.appendChild(textEl);
    scrollToBottom();

    const model = (typeof Profile !== 'undefined' ? Profile.imageModel?.() : '') || undefined;
    try {
      const resp = await fetch('/api/image/generate', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          prompt,
          size: opts.size || 'square',
          negative_prompt: opts.negative || '',
          model,
        }),
      });
      if (!resp.ok) {
        let detail = 'HTTP ' + resp.status;
        try { detail = (await resp.json()).detail || detail; } catch (_) {}
        textEl.innerHTML = `<em style="color:#ef4444">Bildgenerierung fehlgeschlagen: ${escHtml(detail)}</em>`;
        return;
      }
      const data = await resp.json();
      textEl.remove();
      insertImage(content, data.image);   // enthält den 💾-Speichern-Knopf
      // Bildunterschrift (Prompt)
      const cap = document.createElement('div');
      cap.style.cssText = 'font-size:12px;color:var(--text-muted);margin-top:2px';
      cap.textContent = '🎨 ' + prompt;
      content.appendChild(cap);
      scrollToBottom();
      // In den Verlauf aufnehmen UND persistieren, damit der Bild-Chat zuverlässig
      // in der Unterhaltungsliste gespeichert bleibt (läuft nicht über /api/chat).
      messages.push({ role: 'user', content: '🎨 ' + prompt });
      messages.push({ role: 'assistant', content: '🎨 Bild erzeugt: ' + prompt });
      _persistConversation();
      loadConversationList();
    } catch (e) {
      textEl.innerHTML = `<em style="color:#ef4444">Bildgenerierung fehlgeschlagen: ${escHtml(e.message)}</em>`;
    } finally {
      isStreaming = false;
      setBtnSendState(true);
    }
  }

  // Geführter Bild-Dialog (/bildhelp): festes Formular, komponiert den Prompt
  // deterministisch (kein LLM → auch im Geheim-Modus nutzbar).
  const _BILD_STIL = ['Fotorealistisch', 'Illustration', '3D-Render', 'Aquarell',
    'Ölgemälde', 'Anime', 'Technische Zeichnung'];
  const _BILD_PERSP = ['Nahaufnahme / Makro', 'Halbtotale', 'Totale / Weitwinkel',
    'Vogelperspektive', 'Froschperspektive', 'Isometrisch'];
  const _BILD_LICHT = ['Tageslicht', 'Goldene Stunde', 'Studiolicht', 'Neon',
    'Kerzenlicht', 'Dramatisch / Chiaroscuro'];
  const _BILD_SIZE = [
    { value: 'square', label: 'Quadrat (1:1)' },
    { value: 'landscape', label: 'Quer (16:9)' },
    { value: 'portrait', label: 'Hoch (9:16)' },
  ];

  function _bildChips(host, items, single) {
    host.innerHTML = '';
    items.forEach((it, i) => {
      const val = typeof it === 'string' ? it : it.value;
      const lab = typeof it === 'string' ? it : it.label;
      const b = document.createElement('button');
      b.type = 'button';
      b.className = 'bild-chip';
      b.textContent = lab;
      b.dataset.val = val;
      if (single && i === 0) b.classList.add('active');
      b.addEventListener('click', () => {
        if (single) host.querySelectorAll('.bild-chip').forEach(c => c.classList.remove('active'));
        b.classList.toggle('active');
      });
      host.appendChild(b);
    });
  }

  function _bildSelected(host) {
    const a = host.querySelector('.bild-chip.active');
    return a ? a.dataset.val : '';
  }

  let _bildWired = false;
  function runBildHelp(prefill) {
    const ov = document.getElementById('bild-help');
    if (!ov) return;
    _bildChips(document.getElementById('bild-stil'), _BILD_STIL, true);
    _bildChips(document.getElementById('bild-perspektive'), _BILD_PERSP, true);
    _bildChips(document.getElementById('bild-licht'), _BILD_LICHT, true);
    _bildChips(document.getElementById('bild-size'), _BILD_SIZE, true);
    const motiv = document.getElementById('bild-motiv');
    motiv.value = prefill || '';
    document.getElementById('bild-negativ').value = '';

    if (!_bildWired) {
      _bildWired = true;
      const close = () => { ov.style.display = 'none'; };
      document.getElementById('bild-close').addEventListener('click', close);
      document.getElementById('bild-cancel').addEventListener('click', close);
      ov.addEventListener('click', (e) => { if (e.target === ov) close(); });
      document.addEventListener('keydown', (e) => {
        if (e.key === 'Escape' && ov.style.display !== 'none') close();
      });
      document.getElementById('bild-go').addEventListener('click', () => {
        const m = document.getElementById('bild-motiv').value.trim();
        if (!m) { showToast('Bitte ein Motiv beschreiben'); return; }
        const parts = [m,
          _bildSelected(document.getElementById('bild-stil')),
          _bildSelected(document.getElementById('bild-perspektive')),
          _bildSelected(document.getElementById('bild-licht'))].filter(Boolean);
        const size = _bildSelected(document.getElementById('bild-size')) || 'square';
        const negative = document.getElementById('bild-negativ').value.trim();
        close();
        runBild(parts.join(', '), { size, negative });
      });
    }
    ov.style.display = 'flex';
    setTimeout(() => motiv.focus(), 50);
  }

  // ── /frag — Dynamische Rückfragen (Eingabemaske) ────────────────────────────
  function _parseFrag(text) {
    const m = text.match(/^\/frag\b\s*([\s\S]*)$/i);
    if (!m) return null;
    return { task: (m[1] || '').trim() };
  }

  async function runFrag(task) {
    task = (task || '').trim();
    if (!task) { showToast('Bitte nach „/frag" eine Aufgabe eingeben'); return; }
    if (isStreaming) return;
    if (typeof Clarify === 'undefined') { showToast('Rückfrage-Modul nicht geladen'); return; }
    showWelcome(false);
    appendMessage('user', '❓ /frag ' + task);
    const row = appendMessage('assistant', '', [], true);
    const mount = row.querySelector('.bubble-content');
    const model = (typeof Profile !== 'undefined' ? Profile.modelFor('general') : '') || undefined;
    const res = await Clarify.ask({ task, domain: 'chat', model, mount });
    if (!res) return;
    if (res.tokens && typeof TokenMeter !== 'undefined') TokenMeter.add(res.tokens, 'Rückfragen');
    // Augmentierte Aufgabe über den normalen Weg senden (Antwort streamt darunter)
    const input = document.getElementById('message-input');
    input.value = res.augmentedTask;
    autoResizeTextarea(input);
    sendMessage();
  }

  // ── Spontane Rückfragen des Modells → strukturierte Maske anbieten ────────────
  // Stellt die Antwort selbst Rückfragen (statt die Aufgabe zu lösen), bieten wir
  // einen Knopf an, der die Fragen in eine ausfüllbare Maske (Vorauswahl + Freitext)
  // umwandelt; nach dem Beantworten wird die Aufgabe automatisch vervollständigt.
  const _CLARIFY_CUE_RE = /r[üu]ckfrage|pr[äa]zisier|gezielte fragen|einige fragen|folgende (fragen|angaben|informationen|punkte)|ben[öo]tige (ich )?noch|br[äa]uchte (ich )?noch/i;

  function _countQuestionLines(text) {
    // „?“-Zeilen zählen, Code-Blöcke ausklammern (dort sind ? kein Signal).
    const stripped = String(text || '').replace(/```[\s\S]*?```/g, '').replace(/`[^`]*`/g, '');
    let count = 0;
    for (const line of stripped.split('\n')) {
      const t = line.trim().replace(/[)\]"'*_>\s]+$/, '');
      if (t.endsWith('?') && t.length > 6) count++;
    }
    return count;
  }

  function _looksLikeClarifyingQuestions(text) {
    if (!text || text.length < 20) return false;
    const qLines = _countQuestionLines(text);
    return qLines >= 2 || (_CLARIFY_CUE_RE.test(text) && qLines >= 1);
  }

  function _lastUserTask() {
    for (let i = messages.length - 1; i >= 0; i--) {
      if (messages[i].role === 'user') return (messages[i].content || '').trim();
    }
    return '';
  }

  // Fügt der Antwort einen „📋 Strukturiert beantworten“-Knopf hinzu, wenn sie
  // wie eine Rückfragen-Liste aussieht.
  function _maybeOfferClarify(row, fullText) {
    if (!row || typeof Clarify === 'undefined' || !Clarify.askFromText) return;
    if (!_looksLikeClarifyingQuestions(fullText)) return;
    const saveBar = row.querySelector('.msg-save-bar');
    if (!saveBar || saveBar.querySelector('.clarify-offer-btn')) return;

    const btn = document.createElement('button');
    btn.className = 'clarify-offer-btn';
    btn.textContent = '📋 Strukturiert beantworten';
    btn.title = 'Diese Rückfragen als Auswahl-/Eingabemaske ausfüllen und die Aufgabe abschließen';
    btn.addEventListener('click', () => _runClarifyOffer(row, fullText, btn));
    saveBar.appendChild(btn);
    saveBar.classList.add('clarify-present');   // Bar nicht abdunkeln (Call-to-Action)
  }

  async function _runClarifyOffer(row, questionsText, btn) {
    if (isStreaming) { showToast('Bitte warten, bis die Antwort fertig ist'); return; }
    btn.disabled = true;
    // Maske unterhalb der Antwort einhängen
    let mount = row.querySelector('.clarify-mount');
    if (!mount) {
      mount = document.createElement('div');
      mount.className = 'clarify-mount';
      (row.querySelector('.msg-bubble') || row).appendChild(mount);
    }
    const task = _lastUserTask();
    const model = (typeof Profile !== 'undefined' ? Profile.modelFor('general') : '') || undefined;
    let res;
    try {
      res = await Clarify.askFromText({ questionsText, task, domain: 'chat', model, mount });
    } catch (_) { res = null; }
    if (res && res.tokens && typeof TokenMeter !== 'undefined') TokenMeter.add(res.tokens, 'Rückfragen');
    if (!res || res.noQuestions) { btn.disabled = false; return; }
    btn.remove();                                   // Knopf verbraucht
    if (!res.answered || !res.augmentedTask) return; // „Ohne Rückfragen“ → nichts senden
    // Beantwortete Aufgabe über den normalen Weg senden (Antwort streamt darunter)
    const input = document.getElementById('message-input');
    input.value = res.augmentedTask;
    autoResizeTextarea(input);
    sendMessage();
  }

  // ── /- und /+ — Nutzer-Feedback ins Markdown-Protokoll ───────────────────────
  // „/- <Text>" meldet ein Problem/eine Fehlermeldung, „/+ <Text>" notiert eine
  // Idee/einen Verbesserungsvorschlag. Beides wird serverseitig als Markdown
  // gesammelt (data/feedback.md) und NICHT an das LLM geschickt.
  function _parseFeedback(text) {
    const m = text.match(/^\/([+\-])\s+([\s\S]+)$/);
    if (!m) return null;
    return { kind: m[1] === '-' ? 'problem' : 'idea', text: m[2].trim() };
  }

  async function runFeedback(kind, text) {
    if (!text) {
      showToast(kind === 'problem'
        ? 'Bitte nach „/-" die Fehlermeldung eintragen'
        : 'Bitte nach „/+" den Verbesserungsvorschlag eintragen');
      return;
    }
    const icon = kind === 'problem' ? '🔴' : '🟢';
    const label = kind === 'problem' ? 'Fehler/Problem notiert' : 'Idee/Verbesserung notiert';
    showWelcome(false);
    try {
      const r = await fetch('/api/feedback', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ kind, text, conversation_id: currentConvId || undefined }),
      });
      if (!r.ok) {
        let msg = 'HTTP ' + r.status;
        try { msg = (await r.json()).detail || msg; } catch (_) {}
        throw new Error(msg);
      }
      const d = await r.json();
      appendMessage('assistant', `${icon} **${label}** (gespeichert in \`${d.file || 'feedback.md'}\`, ${d.count || 1} Einträge):\n\n> ${text}`);
      showToast(`${icon} ${label}`);
    } catch (e) {
      showToast('Feedback konnte nicht gespeichert werden: ' + e.message);
    }
  }
  window.runFeedback = runFeedback;

  // ── /hilfe — Übersicht aller Chat-Befehle (kein LLM) ─────────────────────────
  // Erkennt „/hilfe", „/help", „/befehle", „/?". Rendert die Befehlsliste aus
  // SLASH_COMMANDS (einzige Quelle → bleibt automatisch aktuell) als Chat-Karte.
  function _parseHelp(text) {
    return /^\/(hilfe|help|befehle|\?)\s*$/i.test((text || '').trim());
  }
  function runHelp() {
    showWelcome(false);
    const lines = SLASH_COMMANDS
      .filter(c => !c.info)   // der reine „/<Agent>"-Platzhalter wird separat erklärt
      .map(c => `- **${c.cmd}** — ${c.desc}`);
    const md = '📖 **Chat-Befehle** — tippe `/` im Eingabefeld für Autovervollständigung '
      + '(↑↓ wählen · Tab übernehmen), oder nutze direkt:\n\n'
      + lines.join('\n')
      + '\n- **/<Agentenkürzel>** — einen gespeicherten Agenten nur für die nächste Nachricht verwenden '
      + '(z. B. `/datenschutz_berater`)\n\n'
      + '_Tipp: Über das Menü „↪ senden an…" unter jeder Antwort lässt sich das Ergebnis an andere '
      + 'Tabs (To-Do, Planer, Code, Mathe …) übergeben._';
    appendMessage('assistant', md);
  }
  window.runHelp = runHelp;

  // ── /plan — Strategie- & Einsatzplan-Orchestrator ────────────────────────────
  // „/plan" (optional mit Zusatz) baut aus dem bisherigen Chat-Verlauf in einem Zug
  // Strategie + Beratungs-Agenten + Einsatz-/Ressourcenplan + Bewertungs-Jury als
  // VORSCHAU. Gespeichert wird nichts; erst „✅ Alles anlegen" legt über die
  // vorhandenen Endpoints (/api/agents, /api/plans, /api/juries) an.
  function _parsePlan(text) {
    // „/plan" oder „/plan50" (Zielanzahl Aufgaben, optional). Default 12.
    const m = text.match(/^\/plan(\d+)?\b\s*([\s\S]*)$/i);
    if (!m) return null;
    const count = m[1] ? Math.max(4, Math.min(parseInt(m[1], 10), 60)) : 12;
    let rest = (m[2] || '').trim();
    const pinned = [], unresolved = [];
    // „/token"-Referenzen aus dem Rest ziehen = feste, bereits vorhandene Agenten.
    rest = rest.replace(/\/(\S+)/g, (full, tok) => {
      const a = _findAgentByToken(tok);
      if (a) { if (!pinned.some(p => p.id === a.id)) pinned.push(a); return ''; }
      unresolved.push(tok); return full;   // unaufgelöst → im Briefing belassen
    }).replace(/\s{2,}/g, ' ').trim();
    return { extra: rest, pinned, unresolved, count };
  }

  // Briefing = die letzten Gesprächsbeiträge (User + Assistent), als Text.
  function _planBrief(maxTurns = 8) {
    const turns = messages.filter(m => m.role === 'user' || m.role === 'assistant');
    return turns.slice(-maxTurns)
      .map(m => `${m.role === 'user' ? 'Nutzer' : 'Assistent'}: ${m.content || ''}`)
      .join('\n\n').slice(0, 8000);
  }

  function _planSection(title) {
    const sec = document.createElement('div');
    sec.style.margin = '10px 0';
    const h = document.createElement('div');
    h.style.cssText = 'font-weight:600;margin-bottom:4px';
    h.textContent = title;
    const body = document.createElement('div');
    sec.appendChild(h); sec.appendChild(body);
    return { sec, body };
  }

  // Button-Fabrik: immer type="button" (sonst könnte ein Klick als Formular-Submit
  // gewertet werden und der eigentliche Handler scheinbar „nicht reagieren").
  function _planBtn(cls, label, onClick) {
    const b = document.createElement('button');
    b.type = 'button';
    b.className = cls;
    b.textContent = label;
    b.addEventListener('click', onClick);
    return b;
  }

  function _handlePlanEvent(ev, ctx) {
    const { statusEl, card, proposal } = ctx;
    if (ev.type === 'phase') {
      statusEl.innerHTML = `<em>⏳ ${escHtml(ev.label || '')}</em>`;
    } else if (ev.type === 'strategy') {
      proposal.strategy = ev.markdown || '';
      const { sec, body } = _planSection('🧭 Strategie');
      renderMarkdown(body, proposal.strategy);
      card.appendChild(sec);
    } else if (ev.type === 'agents') {
      proposal.agents = ev.agents || [];
      const { sec, body } = _planSection(`🤖 Beratungs-Agenten (${proposal.agents.length})`);
      body.innerHTML = proposal.agents.map(a =>
        `<div style="margin:3px 0">${escHtml(a.icon || '🤖')} <strong>${escHtml(a.name)}</strong>`
        + (a.pinned ? ' <span title="Fester Agent" style="opacity:.8">📌 fest</span>' : '')
        + (a.description ? ` — <span style="opacity:.8">${escHtml(a.description)}</span>` : '') + '</div>'
      ).join('') || '<em>keine</em>';
      card.appendChild(sec);
    } else if (ev.type === 'plan') {
      proposal.plan = ev.plan || null;
      const t = (proposal.plan && proposal.plan.tasks) || [];
      const { sec, body } = _planSection(`📅 Einsatz- & Ressourcenplan (${t.length} Aufgaben)`);
      body.innerHTML = `<div style="margin-bottom:4px"><strong>${escHtml(proposal.plan?.name || 'Plan')}</strong></div>`
        + '<ol style="margin:0 0 0 18px">' + t.map(x =>
          `<li>${escHtml(x.name)}${x.area ? ` <span style="opacity:.7">[${escHtml(x.area)}]</span>` : ''}`
          + `${x.duration ? ` · ${x.duration} T` : ''}</li>`).join('') + '</ol>';
      card.appendChild(sec);
    } else if (ev.type === 'jury') {
      proposal.jury = ev.jury || null;
      const m = (proposal.jury && proposal.jury.member_agent_names) || [];
      const { sec, body } = _planSection('⚖️ Bewertungs-Jury');
      body.innerHTML = `<div><strong>${escHtml(proposal.jury?.name || 'Jury')}</strong></div>`
        + `<div style="opacity:.8">Mitglieder: ${m.map(escHtml).join(', ') || '—'}</div>`;
      card.appendChild(sec);
    } else if (ev.type === 'done') {
      if (ev.tokens && typeof TokenMeter !== 'undefined' && TokenMeter.add) {
        TokenMeter.add({ in: ev.tokens.in || 0, out: ev.tokens.out || 0 }, 'Plan-Orchestrator');
      }
    } else if (ev.type === 'error') {
      showToast('Plan: ' + (ev.message || 'Fehler'));
    }
  }

  function _finishPlanCard(ctx) {
    const { statusEl, card, proposal } = ctx;
    const note = document.createElement('div');
    note.style.cssText = 'margin:8px 0;font-size:.85em;opacity:.75';
    note.textContent = 'Hinweis: Kosten-/Rechtsangaben sind ein Entscheidungs­hilfe-Entwurf. '
      + 'Für DSGVO/EU AI Act/Preise echte Quellen prüfen (Recherche/Web-Toggle) – kein Rechtsrat.';
    card.appendChild(note);

    const bar = document.createElement('div');
    bar.style.cssText = 'display:flex;gap:8px;flex-wrap:wrap;margin-top:6px';
    bar.appendChild(_planBtn('btn-save', '✅ Alles anlegen', () => _applyPlan(proposal, bar, statusEl)));
    bar.appendChild(_planBtn('btn-cancel', '📄 In Dokumente', () => {
      if (!proposal.strategy) { showToast('Keine Strategie vorhanden'); return; }
      if (typeof DocGen !== 'undefined' && DocGen.showResult) {
        DocGen.showResult(proposal.strategy); switchTab('docgen');
        showToast('✓ Strategie im Dokumente-Tab');
      } else { showToast('Dokumente-Tab nicht verfügbar'); }
    }));
    bar.appendChild(_planBtn('btn-cancel', '📚 In Wissensdatenbank', () => {
      if (!proposal.strategy) { showToast('Keine Strategie vorhanden'); return; }
      if (typeof RAG !== 'undefined' && RAG.ingestText) {
        RAG.ingestText((proposal.plan?.name || 'Strategie'), proposal.strategy);
      } else { showToast('Wissensdatenbank nicht verfügbar'); }
    }));
    card.appendChild(bar);
    statusEl.innerHTML = '<strong>✅ Vorschau fertig</strong> — prüfen und anlegen.';
    scrollToBottom();
  }

  async function _applyPlan(proposal, bar, statusEl) {
    bar.querySelectorAll('button').forEach(b => b.disabled = true);
    statusEl.innerHTML = '<em>⏳ Projekt, Agenten, Plan und Jury werden angelegt…</em>';
    const agentIds = [];
    let created = 0, reused = 0;
    try {
      // 0) Projekt anlegen — Plan, Agenten und Jury werden damit verknüpft.
      let projectId = null, projectName = '';
      {
        projectName = (proposal.plan && proposal.plan.name) || 'Strategie-Projekt';
        const r = await fetch('/api/projects', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            name: projectName,
            description: (proposal.plan && proposal.plan.description) || (proposal.strategy || '').slice(0, 300),
          }),
        });
        if (r.ok) { const pj = await r.json(); projectId = pj.id; }
      }

      for (const a of (proposal.agents || [])) {
        // Fester, bereits vorhandener Agent → nicht erneut anlegen, vorhandene id nutzen.
        if (a.id) { agentIds.push(a.id); reused++; continue; }
        const r = await fetch('/api/agents', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            name: a.name, description: a.description || '', system_prompt: a.system_prompt,
            tools: a.tools || ['web_search', 'calculate'], icon: a.icon || '🤖',
            category: a.category || 'Beratung', favorite: false, project_id: projectId,
          }),
        });
        if (r.ok) { const saved = await r.json(); if (saved.id) { agentIds.push(saved.id); created++; } }
      }

      let planId = null;
      if (proposal.plan) {
        const r = await fetch('/api/plans', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            name: proposal.plan.name, description: proposal.plan.description || '',
            tasks: proposal.plan.tasks || [],
            resource_catalog: proposal.plan.resource_catalog || [],
            resource_mode: proposal.plan.resource_mode || 'free', project_id: projectId,
          }),
        });
        if (r.ok) { const saved = await r.json(); planId = saved.id; }
      }

      let juryId = null;
      if (proposal.jury) {
        const r = await fetch('/api/juries', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            name: proposal.jury.name, description: proposal.jury.description || '',
            member_agent_ids: agentIds, project_id: projectId,
          }),
        });
        if (r.ok) { const saved = await r.json(); juryId = saved.id; }
      }

      if (typeof AgentManager !== 'undefined' && AgentManager.load) { try { await AgentManager.load(); } catch (_) {} }
      // Projekt-Auswahl aktualisieren, aktuelle Unterhaltung dem Projekt zuordnen.
      if (projectId && typeof Projects !== 'undefined') {
        try { await Projects.load(); } catch (_) {}
        if (window._currentConvId && Projects.assignCurrentChat) {
          try { await Projects.assignCurrentChat(projectId); } catch (_) {}
        }
      }

      statusEl.innerHTML = `<strong>✅ Angelegt:</strong> `
        + (projectId ? `Projekt „${escHtml(projectName)}", ` : '')
        + `${created} neue Agenten`
        + (reused ? ` (+ ${reused} feste übernommen)` : '')
        + (planId ? ', 1 Plan' : '') + (juryId ? ', 1 Jury' : '')
        + (projectId ? ' — alles mit dem Projekt verknüpft.' : '.');
      const done = document.createElement('div');
      done.style.cssText = 'display:flex;gap:8px;flex-wrap:wrap;margin-top:8px';
      if (projectId && typeof Projects !== 'undefined' && Projects.openModal) {
        done.appendChild(_planBtn('btn-cancel', '📁 Projekt', () => Projects.openModal()));
      }
      if (planId) {
        done.appendChild(_planBtn('btn-cancel', '📅 Plan öffnen', () => {
          if (typeof Planner !== 'undefined' && Planner.openPlan) Planner.openPlan(planId);
          else switchTab('planner');
        }));
      }
      done.appendChild(_planBtn('btn-cancel', '🤖 Agenten', () => switchTab('agents')));
      if (juryId) {
        done.appendChild(_planBtn('btn-cancel', '⚖️ Jury starten', () => {
          switchTab('jury');
          if (typeof Jury !== 'undefined' && Jury.openManager) Jury.openManager();
        }));
      }
      bar.parentElement.appendChild(done);
      showToast('✓ Strategie umgesetzt — Agenten, Plan und Jury angelegt');
    } catch (e) {
      statusEl.innerHTML = '<strong>⚠️ Fehler beim Anlegen:</strong> ' + escHtml(e.message || '');
      bar.querySelectorAll('button').forEach(b => b.disabled = false);
    }
  }

  async function runPlan(extra, pinned, unresolved, count) {
    if (isStreaming) return;
    pinned = pinned || []; unresolved = unresolved || [];
    count = Math.max(4, Math.min(parseInt(count, 10) || 12, 60));
    const brief = _planBrief();
    if (!brief && !extra && !pinned.length) {
      showToast('„/plan" braucht eine vorherige Diskussion im Chat (oder Zusatz nach dem Befehl).');
      return;
    }
    showWelcome(false);
    isStreaming = true;
    setBtnSendState(false);

    const model = (typeof Profile !== 'undefined' ? Profile.modelFor('general') : '') || undefined;
    const useSearch = document.getElementById('btn-search-toggle').classList.contains('active');
    const ragCollections = (typeof RAG !== 'undefined') ? RAG.selectedCollections() : [];

    // Feste Agenten auf die Backend-Form bringen (id behalten → nicht neu anlegen).
    const pinnedPayload = pinned.map(a => ({
      id: a.id, name: a.name, description: a.description || '',
      system_prompt: a.system_prompt || '', icon: a.icon || '🤖',
      category: a.category || 'Beratung', tools: a.tools || ['web_search', 'calculate'],
    }));

    let echo = `🧭 /plan — Strategie & Einsatzplan aus der Diskussion (~${count} Aufgaben)`;
    if (ragCollections.length) echo += '\n\n📂 Quelle: hinterlegte Datei(en) — ' + ragCollections.join(', ');
    if (pinned.length) echo += '\n\n📌 Feste Agenten: ' + pinned.map(a => (a.icon ? a.icon + ' ' : '') + a.name).join(', ');
    if (unresolved.length) echo += '\n\n⚠️ Nicht gefunden (als Text behandelt): ' + unresolved.map(t => '/' + t).join(' ');
    if (extra) echo += `\n\nZusatz: ${extra}`;
    appendMessage('user', echo);

    const row = appendMessage('assistant', '', [], true);
    const content = row.querySelector('.bubble-content');
    const statusEl = document.createElement('div');
    statusEl.innerHTML = '<em>⏳ Strategie wird vorbereitet…</em>';
    const card = document.createElement('div');
    content.appendChild(statusEl);
    content.appendChild(card);

    const ctx = { statusEl, card, proposal: { strategy: '', agents: [], plan: null, jury: null } };

    abortController = new AbortController();
    try {
      const resp = await fetch('/api/plan/strategy', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        signal: abortController.signal,
        body: JSON.stringify({
          brief, extra, model, web_search: useSearch, rag_collections: ragCollections,
          count, pinned_agents: pinnedPayload,
        }),
      });
      const reader = resp.body.getReader();
      const decoder = new TextDecoder();
      let buffer = '';
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split('\n');
        buffer = lines.pop();
        for (const line of lines) {
          if (!line.startsWith('data: ')) continue;
          let ev;
          try { ev = JSON.parse(line.slice(6)); } catch (_) { continue; }
          _handlePlanEvent(ev, ctx);
          scrollToBottom();
        }
      }
      _finishPlanCard(ctx);
    } catch (e) {
      if (e.name !== 'AbortError') showToast('Plan-Fehler: ' + e.message);
    } finally {
      isStreaming = false;
      setBtnSendState(true);
    }
  }

  // ── Projekt-Orchestrator (/projekt) ─────────────────────────────────────────
  // Ein Prompt beschreibt ein Vorhaben; der Orchestrator zerlegt es phasenweise
  // (Morph → Paarvergleich → Plan → To-Do → …) und legt auf Bestätigung EIN Projekt
  // mit allen Artefakten an. Vorschau-/Anlege-Muster wie /plan.
  function _parseProjekt(text) {
    const m = String(text || '').match(/^\/(projekt|vorhaben|projektplan)\b\s*([\s\S]*)$/i);
    if (!m) return null;
    return (m[2] || '').trim();   // Beschreibung (kann leer sein)
  }

  function _parseVorgang(text) {
    const m = String(text || '').match(/^\/(vorgang|vorgaenge|vorgänge|vorgangladen)\b\s*([\s\S]*)$/i);
    if (!m) return null;
    return (m[2] || '').trim();   // (Argument derzeit ignoriert — es wird die Auswahl gezeigt)
  }

  function _projCard(title) {
    const d = document.createElement('details');
    d.open = true;
    d.style.cssText = 'margin:8px 0;border:1px solid var(--border,#334);border-radius:8px;padding:6px 10px;background:var(--bg-input,#0e141b)';
    const s = document.createElement('summary');
    s.style.cssText = 'font-weight:600;cursor:pointer';
    s.textContent = title;
    d.appendChild(s);
    const body = document.createElement('div');
    body.style.cssText = 'margin-top:6px;font-size:.94em';
    d.appendChild(body);
    d._body = body;
    return d;
  }

  // Baut die Assistenten-Blase + den Vorschau-Kontext (statusEl/cardsWrap/cards/proposal),
  // den sowohl der Live-Orchestrator (Stream) als auch der Lader (/vorgang) befüllen.
  function _newProjektCtx(initialStatusHtml) {
    const row = appendMessage('assistant', '', [], true);
    const content = row.querySelector('.bubble-content');
    const statusEl = document.createElement('div');
    statusEl.innerHTML = initialStatusHtml || '';
    content.appendChild(statusEl);
    const cardsWrap = document.createElement('div');
    content.appendChild(cardsWrap);
    return { statusEl, cardsWrap, cards: {}, proposal: null };
  }

  async function runProjektOrchestrator(brief) {
    if (isStreaming) return;
    brief = (brief || '').trim();
    if (!brief) { showToast('„/projekt" braucht eine Beschreibung, z. B. „/projekt Entwicklung einer …"'); return; }
    showWelcome(false);
    isStreaming = true;
    setBtnSendState(false);

    const model = (typeof Profile !== 'undefined' ? Profile.modelFor('general') : '') || undefined;
    appendMessage('user', '🗂 /projekt — ' + brief);

    const ctx = _newProjektCtx('<em>⏳ Vorhaben wird zerlegt…</em>');

    abortController = new AbortController();
    try {
      const resp = await fetch('/api/orchestrator/plan', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        signal: abortController.signal,
        body: JSON.stringify({ brief, model }),
      });
      const reader = resp.body.getReader();
      const decoder = new TextDecoder();
      let buffer = '';
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split('\n');
        buffer = lines.pop();
        for (const line of lines) {
          if (!line.startsWith('data: ')) continue;
          let ev;
          try { ev = JSON.parse(line.slice(6)); } catch (_) { continue; }
          _handleProjektEvent(ev, ctx);
          scrollToBottom();
        }
      }
      _finishProjektCard(ctx);
    } catch (e) {
      if (e.name !== 'AbortError') { statusEl.innerHTML = '<em style="color:#ef4444">Fehler: ' + escHtml(e.message || '') + '</em>'; }
    } finally {
      isStreaming = false;
      setBtnSendState(true);
    }
  }

  function _handleProjektEvent(ev, ctx) {
    if (ev.type === 'phase') {
      ctx.statusEl.innerHTML = '<em>' + escHtml(ev.label || '…') + '</em>';
    } else if (ev.type === 'notice') {
      const n = document.createElement('div');
      n.style.cssText = 'margin:4px 0;font-size:.88em;color:var(--text-muted,#8b98a5)';
      n.textContent = 'ⓘ ' + (ev.message || '');
      ctx.cardsWrap.appendChild(n);
    } else if (ev.type === 'project') {
      const c = _projCard('🗂 Projekt: ' + (ev.project.name || ''));
      c._body.innerHTML = escHtml(ev.project.description || '');
      ctx.cardsWrap.appendChild(c); ctx.cards.project = c;
    } else if (ev.type === 'complexity') {
      const n = document.createElement('div');
      n.style.cssText = 'margin:2px 0 4px;font-size:.9em;color:var(--text-muted,#8b98a5)';
      n.textContent = '📊 Komplexität: ' + (ev.label || ev.complexity) + ' → '
        + ev.plan_tasks + ' Planvorgänge' + (ev.auto ? ' (automatisch)' : '');
      ctx.cardsWrap.appendChild(n);
    } else if (ev.type === 'flow') {
      const c = _projCard('🗺 Ablaufdiagramm');
      const f = ev.flow || {};
      if (f.mermaid) {
        const holder = document.createElement('div');
        holder.style.cssText = 'overflow-x:auto';
        c._body.appendChild(holder);
        try { _renderMermaid(holder, f.mermaid); } catch (_) { holder.textContent = f.mermaid; }
      } else if ((f.steps || []).length) {
        c._body.innerHTML = '<ol style="margin:0;padding-left:20px">' + f.steps.map(s => '<li>' + escHtml(s) + '</li>').join('') + '</ol>';
      } else {
        c._body.innerHTML = '<em>kein Ablauf</em>';
      }
      ctx.cardsWrap.appendChild(c); ctx.cards.flow = c;
    } else if (ev.type === 'morph') {
      const c = _projCard('🧩 Zerlegung — morphologischer Kasten');
      const params = (ev.morph.parameters || []);
      c._body.innerHTML = params.length
        ? '<ul style="margin:0;padding-left:18px">' + params.map(p =>
            '<li><strong>' + escHtml(p.name) + ':</strong> ' + p.values.map(escHtml).join(' · ') + '</li>').join('') + '</ul>'
        : '<em>keine Parameter</em>';
      ctx.cardsWrap.appendChild(c); ctx.cards.morph = c;
    } else if (ev.type === 'decision') {
      const c = _projCard('⚖ Paarvergleich-Bewertung');
      const d = ev.decision, res = d.result || {};
      let html = '';
      if ((d.criteria || []).length) {
        html += '<div><strong>Kriterien (Gewicht):</strong> ' + d.criteria.map((cr, i) => {
          const w = (res.weights && res.weights[i] != null) ? ' (' + Math.round(res.weights[i] * 100) + '%)' : '';
          return escHtml(cr.name) + w;
        }).join(', ') + '</div>';
      }
      if ((res.ranking || []).length && (d.variants || []).length) {
        html += '<div style="margin-top:6px"><strong>Ranking:</strong><ol style="margin:4px 0;padding-left:20px">'
          + res.ranking.map(r => {
              const v = d.variants[r.index];
              return '<li>' + escHtml(v ? v.name : '?') + ' — ' + (r.percent != null ? r.percent + '%' : '') + '</li>';
            }).join('') + '</ol></div>';
      }
      if (res.cr != null) html += '<div style="font-size:.85em;opacity:.75">Konsistenz CR = ' + (Math.round(res.cr * 1000) / 1000)
        + (res.consistent ? ' ✓' : ' ⚠ (prüfen)') + '</div>';
      c._body.innerHTML = html || '<em>keine Bewertung</em>';
      ctx.cardsWrap.appendChild(c); ctx.cards.decision = c;
    } else if (ev.type === 'plan') {
      const c = _projCard('📅 Projektplan (' + (ev.plan.tasks || []).length + ' Vorgänge)');
      const t = ev.plan.tasks || [];
      c._body.innerHTML = t.length
        ? '<ol style="margin:0;padding-left:20px">' + t.slice(0, 12).map(x =>
            '<li>' + escHtml(x.name) + ' <span style="opacity:.6">(' + x.duration + ' T'
            + (x.area ? ', ' + escHtml(x.area) : '') + ')</span></li>').join('')
          + (t.length > 12 ? '<li style="opacity:.6">… und ' + (t.length - 12) + ' weitere</li>' : '') + '</ol>'
        : '<em>keine Vorgänge</em>';
      ctx.cardsWrap.appendChild(c); ctx.cards.plan = c;
    } else if (ev.type === 'todo') {
      const c = _projCard('✅ To-Do-Liste (' + (ev.todo.items || []).length + ' Punkte)');
      const it = ev.todo.items || [];
      c._body.innerHTML = it.length
        ? '<ul style="margin:0;padding-left:18px">' + it.slice(0, 15).map(x => '<li>' + escHtml(x) + '</li>').join('')
          + (it.length > 15 ? '<li style="opacity:.6">… und ' + (it.length - 15) + ' weitere</li>' : '') + '</ul>'
        : '<em>keine Punkte</em>';
      ctx.cardsWrap.appendChild(c); ctx.cards.todo = c;
    } else if (ev.type === 'patente') {
      const p = ev.patente || {};
      const c = _projCard('⚖ Patent-Entwurf (kein Einreichen)');
      let html = '';
      if (p.title) html += '<div><strong>' + escHtml(p.title) + '</strong></div>';
      if (p.claim1) html += '<div style="margin-top:4px"><em>Anspruch 1:</em> ' + escHtml(p.claim1) + '</div>';
      if (p.abstract) html += '<div style="margin-top:4px"><em>Abstract:</em> ' + escHtml(p.abstract) + '</div>';
      if ((p.ipc || []).length) html += '<div style="margin-top:4px;font-size:.85em;opacity:.75">IPC/CPC: ' + p.ipc.map(escHtml).join(', ') + '</div>';
      if ((p.search_terms || []).length) html += '<div style="font-size:.85em;opacity:.75">Recherche: ' + p.search_terms.map(escHtml).join(', ') + '</div>';
      c._body.innerHTML = html || '<em>kein Entwurf</em>';
      // Skizzen: Schema-Figuren (Mermaid) + optional KI-Konzeptbild + Bezugszeichenliste
      const figs = p.figures || [];
      if (figs.length) {
        const fh = document.createElement('div');
        fh.style.cssText = 'margin-top:8px';
        fh.innerHTML = '<div style="font-weight:600;margin-bottom:4px">🖼 Skizzen (Entwurf – kein Einreichen)</div>';
        figs.forEach(f => {
          const cap = document.createElement('div');
          cap.style.cssText = 'font-size:.9em;margin:6px 0 2px';
          cap.textContent = f.caption || '';
          fh.appendChild(cap);
          if (f.mermaid) {
            const holder = document.createElement('div'); holder.style.cssText = 'overflow-x:auto';
            fh.appendChild(holder);
            try { _renderMermaid(holder, f.mermaid); } catch (_) { holder.textContent = f.mermaid; }
          }
          if (f.image) {
            const img = document.createElement('img');
            img.src = f.image; img.alt = 'Konzeptskizze';
            img.style.cssText = 'max-width:100%;border-radius:6px;margin-top:4px';
            fh.appendChild(img);
          }
        });
        if ((p.bezugszeichen || []).length) {
          const bz = document.createElement('div');
          bz.style.cssText = 'font-size:.85em;opacity:.85;margin-top:6px';
          bz.innerHTML = '<strong>Bezugszeichen:</strong> ' + p.bezugszeichen.map(z => escHtml((z.n || '') + ' ' + (z.label || ''))).join(' · ');
          fh.appendChild(bz);
        }
        if (p.figures_description) {
          const fd = document.createElement('div');
          fd.style.cssText = 'font-size:.85em;opacity:.8;margin-top:4px';
          fd.textContent = p.figures_description;
          fh.appendChild(fd);
        }
        c._body.appendChild(fh);
      }
      ctx.cardsWrap.appendChild(c); ctx.cards.patente = c;
    } else if (ev.type === 'doku') {
      const d = ev.doku || {};
      const c = _projCard('📄 Dokumentation' + (d.n_slides ? ' + Präsentation (' + d.n_slides + ' Folien)' : ''));
      c._body.innerHTML = (d.has_markdown ? '✓ Dokument (Markdown)' : '– kein Dokument')
        + (d.n_slides ? '<br>✓ Foliensatz' + (d.has_cover ? ' inkl. Titelbild' : '') : '')
        + '<div style="font-size:.85em;opacity:.7;margin-top:4px">Format: ' + escHtml(d.format || 'beides') + ' — öffnen/herunterladen nach dem Anlegen</div>';
      ctx.cardsWrap.appendChild(c); ctx.cards.doku = c;
    } else if (ev.type === 'angebot') {
      const a = ev.angebot || {};
      const c = _projCard('🧾 Angebot (erstellen, nicht senden)');
      if ((a.positionen || []).length) {
        c._body.innerHTML = '<ul style="margin:0;padding-left:18px">'
          + a.positionen.slice(0, 10).map(p => '<li>' + escHtml(p.beschreibung) + ' — '
              + (p.einzelpreis != null ? p.einzelpreis.toLocaleString('de-DE') + ' €' : '') + '</li>').join('')
          + '</ul><div style="margin-top:6px"><strong>Netto:</strong> ' + escHtml(a.summe_netto || '')
          + ' · <strong>Brutto:</strong> ' + escHtml(a.summe_brutto || '') + ' (inkl. ' + (a.ust_satz || 19) + '% USt)</div>';
      } else {
        c._body.innerHTML = '<em>kein Angebot (Plan ohne bepreiste Vorgänge)</em>';
      }
      ctx.cardsWrap.appendChild(c); ctx.cards.angebot = c;
    } else if (ev.type === 'proposal') {
      ctx.proposal = ev.proposal;
    } else if (ev.type === 'done') {
      if (ev.tokens && typeof TokenMeter !== 'undefined') TokenMeter.add(ev.tokens, 'Projekt-Orchestrator');
    } else if (ev.type === 'error') {
      ctx.statusEl.innerHTML = '<em style="color:#ef4444">Fehler: ' + escHtml(ev.message || '') + '</em>';
    }
  }

  function _finishProjektCard(ctx) {
    if (!ctx.proposal) { if (!ctx.statusEl.textContent) ctx.statusEl.textContent = ''; return; }
    ctx.statusEl.innerHTML = '<strong>Vorschau fertig.</strong> Alles als Projekt anlegen und verknüpfen?';
    const bar = document.createElement('div');
    bar.style.cssText = 'display:flex;gap:8px;flex-wrap:wrap;margin-top:10px';
    const doneStatus = document.createElement('div');
    doneStatus.style.cssText = 'margin-top:8px;font-size:.94em';
    const go = _planBtn('btn-cancel', '✅ Alles anlegen', () => _applyProjekt(ctx.proposal, bar, doneStatus));
    go.style.cssText = 'background:var(--accent,#2d6cdf);color:#fff';
    const cancel = _planBtn('btn-cancel', 'Verwerfen', () => { bar.remove(); doneStatus.textContent = 'Verworfen.'; });
    bar.appendChild(go); bar.appendChild(cancel);
    ctx.cardsWrap.appendChild(bar);
    ctx.cardsWrap.appendChild(doneStatus);
  }

  async function _applyProjekt(proposal, bar, statusEl) {
    bar.querySelectorAll('button').forEach(b => b.disabled = true);
    statusEl.innerHTML = '<em>⏳ Projekt und Artefakte werden angelegt…</em>';
    const made = [];
    try {
      // 1) Projekt
      const pmeta = proposal.project || {};
      const pr = await fetch('/api/projects', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name: pmeta.name || 'Neues Projekt', description: pmeta.description || '' }),
      });
      const project = pr.ok ? await pr.json() : null;
      const projectId = project ? project.id : null;
      if (projectId) made.push('Projekt „' + (pmeta.name || '') + '"');

      // 2) Plan (mit project_id verknüpft)
      let planId = null;
      if (proposal.plan && (proposal.plan.tasks || []).length) {
        const r = await fetch('/api/plans', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            name: proposal.plan.name, description: proposal.plan.description || '',
            tasks: proposal.plan.tasks, resource_catalog: proposal.plan.resource_catalog || [],
            resource_mode: 'free', project_id: projectId,
          }),
        });
        if (r.ok) { const s = await r.json(); planId = s.id; made.push('Plan (' + proposal.plan.tasks.length + ' Vorgänge)'); }
        // Plan dem Projekt als plan_id eintragen
        if (planId && projectId) {
          try { await fetch('/api/projects/' + projectId, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ plan_id: planId }) }); } catch (_) {}
        }
      }

      // 3) Varianten-Vergleich (Paarvergleich) — POST anlegen, dann PUT befüllen
      let varName = null;
      const dec = proposal.decision;
      if (dec && (dec.criteria || []).length && (dec.variants || []).length) {
        const nm = (pmeta.name || 'Vergleich').replace(/[^\w \-]/g, '').trim().slice(0, 50) || 'Vergleich';
        try { await fetch('/api/varianten/projects', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ name: nm }) }); } catch (_) {}
        const r = await fetch('/api/varianten/projects/' + encodeURIComponent(nm), {
          method: 'PUT', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            title: pmeta.name || nm, description: proposal.brief || '',
            criteria: dec.criteria, variants: dec.variants,
            pairwise: dec.pairwise_matrix || [], ratings: dec.ratings || [],
            project_id: projectId || '',
          }),
        });
        if (r.ok) { varName = nm; made.push('Variantenvergleich'); }
      }

      // 4) To-Do-Liste
      let todoPid = null;
      if (proposal.todo && (proposal.todo.items || []).length) {
        const name = proposal.todo.title || (pmeta.name || 'Aufgaben');
        const cr = await fetch('/api/todo/projects', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ name }) });
        if (cr.ok) {
          const created = await cr.json();
          todoPid = created.id;
          const items = proposal.todo.items.map(t => ({ text: t }));
          await fetch('/api/todo/projects/' + created.id, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ type: 'projekt', title: name, items }) });
          made.push('To-Do (' + items.length + ' Punkte)');
        }
      }

      // 5) Patent-Projekt (Workspace für die echte Recherche; Entwurf steckt in der Doku)
      let patName = null;
      const pat = proposal.patente;
      if (pat && (pat.title || pat.claim1)) {
        const nm = (pmeta.name || 'Patente').replace(/[^\w \-]/g, '').trim().slice(0, 50) || 'Patente';
        try {
          const r = await fetch('/api/patente/projects', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ name: nm, project_id: projectId || '' }) });
          if (r.ok) { const s = await r.json(); patName = s.name || nm; made.push('Patent-Projekt'); }
        } catch (_) {}
      }

      // 6) Angebot (erstellen, NICHT versenden) — deterministisch aus dem Plan
      let angNr = null;
      const ang = proposal.angebot;
      if (ang && (ang.positionen || []).length) {
        try {
          const r = await fetch('/api/angebot/create', {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ positionen: ang.positionen, ust_satz: ang.ust_satz || 19,
              project_id: projectId, plan_id: planId || '' }),
          });
          if (r.ok) { const s = await r.json(); angNr = s.nummer; made.push('Angebot ' + (s.nummer || '')); }
        } catch (_) {}
      }

      // Verknüpfte Artefakte zentral am Projekt hinterlegen → Projekt-Modal zeigt alles
      if (projectId) {
        try {
          await fetch('/api/projects/' + projectId, {
            method: 'PUT', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ links: {
              plan_id: planId || '', varianten: varName || '', patente: patName || '',
              todo_pid: todoPid || '', angebot_nr: angNr || '',
              has_doku: !!((proposal.doku || {}).markdown || ((proposal.doku || {}).presentation)),
              has_flow: !!((proposal.flow || {}).mermaid), run: true,
            } }),
          });
        } catch (_) {}
      }

      // 7) Vorgang als JSON unter dem Projekt speichern (reproduzierbar/nachvollziehbar)
      try {
        await fetch('/api/orchestrator/save', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            project_id: projectId || '', proposal,
            created: { plan_id: planId, var_name: varName, todo_pid: todoPid, pat_name: patName, angebot_nr: angNr },
          }),
        });
        made.push('Vorgang gespeichert');
      } catch (_) {}

      // Projekt-Auswahl aktualisieren, aktuelle Unterhaltung zuordnen
      if (projectId && typeof Projects !== 'undefined') {
        try { await Projects.load(); } catch (_) {}
        if (window._currentConvId && Projects.assignCurrentChat) { try { await Projects.assignCurrentChat(projectId); } catch (_) {} }
      }
      if (typeof Todo !== 'undefined' && Todo.refresh) { try { Todo.refresh(); } catch (_) {} }

      statusEl.innerHTML = '<strong>✅ Angelegt:</strong> ' + made.map(escHtml).join(', ') + ' — alles mit dem Projekt verknüpft.';
      const done = document.createElement('div');
      done.style.cssText = 'display:flex;gap:8px;flex-wrap:wrap;margin-top:8px';
      if (projectId && typeof Projects !== 'undefined' && Projects.openModal) done.appendChild(_planBtn('btn-cancel', '📁 Projekt', () => Projects.openModal()));
      if (planId) done.appendChild(_planBtn('btn-cancel', '📅 Plan', () => { if (typeof Planner !== 'undefined' && Planner.openPlan) Planner.openPlan(planId); else switchTab('planner'); }));
      if (varName) done.appendChild(_planBtn('btn-cancel', '🧮 Varianten', () => switchTab('varianten')));
      if (todoPid) done.appendChild(_planBtn('btn-cancel', '✅ To-Do', () => switchTab('todo')));
      if (patName) done.appendChild(_planBtn('btn-cancel', '⚖ Patente', () => switchTab('patente')));
      if (angNr) done.appendChild(_planBtn('btn-cancel', '🧾 Angebot', () => switchTab('rechnung')));
      // Doku: Präsentation in Canvas öffnen + Dokument (.md) herunterladen
      const doku = proposal.doku || {};
      if (doku.presentation && (doku.presentation.slides || []).length) {
        done.appendChild(_planBtn('btn-cancel', '📊 Präsentation', () => {
          if (typeof CanvasRenderer !== 'undefined') { CanvasRenderer.render(doku.presentation); switchTab('canvas'); }
        }));
      }
      if (doku.markdown) {
        done.appendChild(_planBtn('btn-cancel', '📄 Doku (.md)', () => {
          const blob = new Blob([doku.markdown], { type: 'text/markdown;charset=utf-8' });
          const url = URL.createObjectURL(blob);
          const a = document.createElement('a');
          a.href = url; a.download = ((pmeta.name || 'Dokumentation').replace(/[^\w \-]/g, '').trim() || 'Dokumentation') + '.md';
          document.body.appendChild(a); a.click(); a.remove();
          setTimeout(() => URL.revokeObjectURL(url), 1000);
        }));
      }
      // Ablauf als wiederverwendbare Vorlage speichern
      const tplBtn = _planBtn('btn-cancel', '⭐ Als Vorlage', async () => {
        tplBtn.disabled = true;
        try {
          const r = await fetch('/api/orchestrator/save', {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ project_id: projectId || '', proposal,
              created: { plan_id: planId, var_name: varName, todo_pid: todoPid, pat_name: patName, angebot_nr: angNr },
              save_as_template: true, template_name: pmeta.name || '' }),
          });
          const s = r.ok ? await r.json() : {};
          tplBtn.textContent = s.template ? '⭐ Vorlage „' + s.template + '"' : '⭐ gespeichert';
          showToast('✓ Als Vorlage gespeichert');
        } catch (_) { tplBtn.disabled = false; showToast('Vorlage fehlgeschlagen'); }
      });
      done.appendChild(tplBtn);
      statusEl.appendChild(done);
      showToast('✓ Projekt angelegt: ' + made.length + ' Artefakte');
    } catch (e) {
      statusEl.innerHTML = '<strong>⚠️ Fehler beim Anlegen:</strong> ' + escHtml(e.message || '');
      bar.querySelectorAll('button').forEach(b => b.disabled = false);
    }
  }

  // ── /vorgang: gespeicherten Orchestrator-Lauf laden und als Vorschau neu aufbauen ──
  // Gegenstück zum Speichern (`/api/orchestrator/save`): listet alle Vorgänge und baut den
  // gewählten über dieselben Karten (`_handleProjektEvent`) + „✅ Alles anlegen"
  // (`_finishProjektCard`/`_applyProjekt`) wieder auf. Rein synthetische Frames.
  async function runVorgangLoader() {
    if (isStreaming) return;
    showWelcome(false);
    appendMessage('user', '📂 /vorgang');
    let runs = [];
    try {
      const r = await fetch('/api/orchestrator/runs');
      runs = r.ok ? await r.json() : [];
    } catch (_) {}
    const row = appendMessage('assistant', '', [], true);
    const content = row.querySelector('.bubble-content');
    if (!Array.isArray(runs) || !runs.length) {
      content.innerHTML = '<em>Keine gespeicherten Vorgänge gefunden. Lege mit „/projekt &lt;Beschreibung&gt;" einen an.</em>';
      scrollToBottom();
      return;
    }
    const intro = document.createElement('div');
    intro.innerHTML = '<strong>Gespeicherte Vorgänge</strong> — zum Laden anklicken:';
    content.appendChild(intro);
    const list = document.createElement('div');
    list.style.cssText = 'display:flex;flex-direction:column;gap:6px;margin-top:8px';
    runs.forEach(run => {
      const b = document.createElement('button');
      b.className = 'btn-cancel';
      b.style.cssText = 'text-align:left;padding:8px 10px;line-height:1.35';
      const dt = run.saved_at ? new Date(run.saved_at * 1000).toLocaleDateString('de-DE') : '';
      const nPh = (run.phases || []).length;
      b.innerHTML = '📂 <strong>' + escHtml(run.name || run.file || '') + '</strong>'
        + (run.brief ? '<div style="font-size:.85em;opacity:.7;margin-top:2px">' + escHtml(String(run.brief).slice(0, 130)) + '</div>' : '')
        + '<div style="font-size:.8em;opacity:.6;margin-top:2px">' + nPh + ' Phasen'
        + (dt ? ' · ' + dt : '') + '</div>';
      b.onclick = () => { b.disabled = true; _openVorgang(run.file || run.project_id); };
      list.appendChild(b);
    });
    content.appendChild(list);
    scrollToBottom();
  }

  async function _openVorgang(fileId) {
    let rec = null;
    try {
      const r = await fetch('/api/orchestrator/runs/' + encodeURIComponent(fileId));
      if (!r.ok) throw new Error('HTTP ' + r.status);
      rec = await r.json();
    } catch (e) { showToast('Vorgang konnte nicht geladen werden: ' + (e.message || '')); return; }
    const proposal = rec.proposal || {};
    const ctx = _newProjektCtx('<em>📂 Vorgang „' + escHtml((proposal.project || {}).name || '') + '" wird geladen…</em>');
    const emit = (f) => { try { _handleProjektEvent(f, ctx); } catch (_) {} };
    // Karten in Phasenreihenfolge neu aufbauen (synthetische Frames wie im Stream).
    if (proposal.project) emit({ type: 'project', project: proposal.project });
    if (proposal.complexity != null) emit({ type: 'complexity', complexity: proposal.complexity,
      label: proposal.complexity, plan_tasks: ((proposal.plan || {}).tasks || []).length, auto: true });
    if (proposal.flow) emit({ type: 'flow', flow: proposal.flow });
    if (proposal.morph) emit({ type: 'morph', morph: proposal.morph });
    if (proposal.decision) emit({ type: 'decision', decision: proposal.decision });
    if (proposal.plan) emit({ type: 'plan', plan: proposal.plan });
    if (proposal.todo) emit({ type: 'todo', todo: proposal.todo });
    if (proposal.patente) emit({ type: 'patente', patente: proposal.patente });
    if (proposal.doku) {
      const d = proposal.doku || {};
      emit({ type: 'doku', doku: {
        format: d.format, has_markdown: !!d.markdown,
        n_slides: ((d.presentation || {}).slides || []).length,
        has_cover: !!d.has_cover, presentation: d.presentation, markdown: d.markdown } });
    }
    if (proposal.angebot) emit({ type: 'angebot', angebot: proposal.angebot });
    ctx.proposal = proposal;
    _finishProjektCard(ctx);   // zeigt „✅ Alles anlegen"
    scrollToBottom();
  }

  // ── Befehls-Autocomplete in der Chatbox („/") ───────────────────────────────
  // Beim Tippen eines führenden „/" erscheint über der Eingabe eine graue Liste der
  // verfügbaren Slash-Befehle. Auswahl per Klick, Tab oder ↑/↓+Tab; Esc schließt.
  const SLASH_COMMANDS = [
    { key: '/hilfe', ins: '/hilfe', cmd: '/hilfe', desc: 'Übersicht aller Chat-Befehle anzeigen (auch /help, /befehle, /?)' },
    { key: '/such', ins: '/such ', cmd: '/such …', desc: 'Alternative Suchbegriffe finden + Web durchsuchen (auch /suche, /finde)' },
    { key: '/recherche', ins: '/recherche ', cmd: '/recherche …', desc: 'Tiefe Recherche: mehrere Aspekte im Web, steuerbare Tiefe & Länge (auch /deep, /tief)' },
    { key: '/frag', ins: '/frag ', cmd: '/frag …', desc: 'Rückfragen-Maske: fehlende Infos per Formular ergänzen, dann antworten' },
    { key: '/bild', ins: '/bild ', cmd: '/bild …', desc: 'Bild aus Beschreibung erzeugen (lokal SD-WebUI oder API)' },
    { key: '/bildhelp', ins: '/bildhelp', cmd: '/bildhelp', desc: 'Geführter Bild-Dialog: Motiv, Stil, Perspektive, Beleuchtung, Format' },
    { key: '/bildprompt', ins: '/bildprompt ', cmd: '/bildprompt [Stil]', desc: 'Bild → Prompt: Bild auswählen, Vision-Modell leitet einen Text-zu-Bild-Prompt ab (→ „🎨 Bild daraus erzeugen")' },
    { key: '/bildedit', ins: '/bildedit ', cmd: '/bildedit [Anweisung]', desc: 'Bildbearbeitung: Bild hochladen + sagen, wie es verändert werden soll (img2img). Optional „🖌 Bereich markieren" = nur den gemalten Bereich ändern (Inpainting). Lokal über Z-Image oder ein fähiges API-Modell; Stärke wählbar' },
    { key: '/upscale', ins: '/upscale', cmd: '/upscale', desc: 'Bild hochskalieren (2×, max 2048): „KI-Detail" ergänzt echte Schärfe lokal über Z-Image, „Schnell" vergrößert sofort per Lanczos' },
    { key: '/musik', ins: '/musik ', cmd: '/musik <Stil/Stimmung>', desc: 'Musik erzeugen (algorithmisch, ohne GPU): z. B. „/musik fröhliche 8bit Abenteuermelodie", „/musik traurig langsam" – spielt das Stück als Audio ab (💾 speichern)' },
    { key: '/excelvergleich', ins: '/excelvergleich', cmd: '/excelvergleich', desc: 'Excel-Vergleich: zwei Tabellen laden, je Blatt + Schlüsselspalte wählen → neue/entfernte/geänderte Zeilen + KI-Bewertung im Chat (auch /xlsvergleich, /excel)' },
    { key: '/paarvergleich', ins: '/paarvergleich ', cmd: '/paarvergleich [Thema]', desc: 'Schrittweiser Paarvergleich: Merkmal für Merkmal eingeben, Wichtigkeit gegen die vorherigen bewerten (Gewichte + Konsistenzcheck). Auch /entscheidung, /ahp' },
    { key: '/praesentation', ins: '/praesentation ', cmd: '/praesentation …', desc: 'Präsentationsassistent (Interview): OHNE Bilder aus dem Thema (Gliederung + Webrecherche); MIT hochgeladenen Bildern eine Präsentation AUS den Bildern (Vision je Bild, Dateiname-Hinweis, sortierbar) + optional .md/.txt, Mermaid-Diagramme, Sprechernotizen, Start/Abschluss generiert → Canvas' },
    { key: '/dd',   ins: '/dd',    cmd: '/dd<N>',  desc: 'Deepdive: N Vertiefungsfragen zur letzten Antwort (z. B. /dd10)' },
    { key: '/ddd',  ins: '/ddd',   cmd: '/ddd<N>', desc: 'Deepdive-Dokument: N Kapitel zur letzten Antwort' },
    { key: '/plan', ins: '/plan ', cmd: '/plan …', desc: 'Strategie → Agenten → Plan → Jury aus dem Verlauf (/planN für Aufgabenzahl)' },
    { key: '/projekt', ins: '/projekt ', cmd: '/projekt <Beschreibung>', desc: 'Projekt-Orchestrator: zerlegt ein Vorhaben phasenweise als Vorschau (Ablaufdiagramm → morphologischer Kasten → Paarvergleich → Plan → To-Do → Patent-Entwurf & Skizze → Doku/Präsentation → Angebot) und legt auf Bestätigung EIN Projekt mit allen verknüpften Artefakten an (auch /vorhaben). Nichts wird eingereicht/versendet.' },
    { key: '/vorgang', ins: '/vorgang', cmd: '/vorgang', desc: 'Gespeicherten Projekt-Vorgang laden: listet die per /projekt gespeicherten Durchläufe (inkl. mitgeliefertem Beispiel), baut den gewählten als Vorschau-Karten neu auf und bietet „✅ Alles anlegen" (auch /vorgänge)' },
    { key: '/workflow', ins: '/workflow ', cmd: '/workflow 1. … 2. …', desc: 'Arbeitsablauf: nummerierte Schritte nacheinander. Pro Schritt Tags [lokal] [api] [web] [bild] [sprache] (z. B. „1. [lokal,web] recherchiere … 2. [api] verarbeite … 3. [bild] erzeuge ein Bild von … 4. [sprache] fasse es als Sprachnachricht"). Ergebnis → Chat/Präsentation/Planer' },
    { key: '/ziel', ins: '/ziel ', cmd: '/ziel <Beschreibung>', desc: 'Ziel-Loop: nur EIN Ziel vorgeben — der Loop plant Teilziele, arbeitet in Runden (Handeln → Bewerten) darauf hin und entscheidet selbst, wann es erreicht ist (Fortschrittsbalken). Optional [web] (Runden web-erden) und [r7] (Runden-Deckel), z. B. „/ziel [web,r6] Vergleiche 3 Akku-Chemien für ein E-Bike". Ergebnis → Chat/Präsentation/Planer (auch /goal)' },
    { key: '/+',    ins: '/+ ',    cmd: '/+ …',    desc: 'Verbesserungsidee ins Feedback-Protokoll (nicht ans LLM)' },
    { key: '/-',    ins: '/- ',    cmd: '/- …',    desc: 'Fehler/Problem ins Feedback-Protokoll (nicht ans LLM)' },
    { key: '/',     ins: '/',      cmd: '/<Agent>', desc: 'Agent nur für diese Nachricht (z. B. /datenschutz_berater)', info: true },
  ];
  let _slashMatches = [], _slashActive = 0;

  function _slashBox() { return document.getElementById('slash-hints'); }

  function hideSlashHints() {
    const box = _slashBox();
    if (box) box.style.display = 'none';
    _slashMatches = []; _slashActive = 0;
  }

  function updateSlashHints(value) {
    const box = _slashBox();
    if (!box) return;
    const m = (value || '').match(/^\/(\S*)$/);   // „/" + Befehlsname, noch kein Leerzeichen
    if (!m) { hideSlashHints(); return; }
    const token = ('/' + m[1]).toLowerCase();
    _slashMatches = SLASH_COMMANDS.filter(c =>
      c.info || c.key.toLowerCase().startsWith(token) || token.startsWith(c.key.toLowerCase()));
    if (!_slashMatches.length) { hideSlashHints(); return; }
    _slashActive = 0;
    _renderSlashHints();
    box.style.display = '';
  }

  function _renderSlashHints() {
    const box = _slashBox();
    if (!box) return;
    box.innerHTML = _slashMatches.map((c, i) =>
      `<div class="slash-hint${i === _slashActive ? ' active' : ''}" data-i="${i}">`
      + `<span class="slash-hint-cmd">${escHtml(c.cmd)}</span>`
      + `<span class="slash-hint-desc" title="${escHtml(c.desc)}">${escHtml(c.desc)}</span></div>`).join('')
      + '<div class="slash-hint-foot">↑↓ wählen · Tab/Klick übernehmen · Esc schließen</div>';
    box.querySelectorAll('.slash-hint').forEach(el => {
      // mousedown statt click: verhindert, dass das Eingabefeld vorher den Fokus verliert
      el.addEventListener('mousedown', e => { e.preventDefault(); _acceptSlash(+el.dataset.i); });
    });
  }

  function _acceptSlash(i) {
    const c = _slashMatches[i];
    if (!c) return;
    const input = document.getElementById('message-input');
    if (!input) return;
    input.value = c.ins;
    input.focus();
    try { input.setSelectionRange(c.ins.length, c.ins.length); } catch (_) {}
    if (typeof autoResizeTextarea === 'function') autoResizeTextarea(input);
    if (c.info) updateSlashHints(input.value);   // „/" stehen lassen → Agentenname weiter tippen
    else hideSlashHints();
  }

  // Rückgabe true = Taste verbraucht (app.js sendet dann NICHT bzw. unterdrückt Default).
  function onSlashHintKeydown(e) {
    const box = _slashBox();
    if (!box || box.style.display === 'none' || !_slashMatches.length) return false;
    if (e.key === 'ArrowDown') { _slashActive = (_slashActive + 1) % _slashMatches.length; _renderSlashHints(); return true; }
    if (e.key === 'ArrowUp')   { _slashActive = (_slashActive - 1 + _slashMatches.length) % _slashMatches.length; _renderSlashHints(); return true; }
    if (e.key === 'Tab')       { _acceptSlash(_slashActive); return true; }
    if (e.key === 'Escape')    { hideSlashHints(); return true; }
    return false;   // Enter → normal weiter (Senden); Liste wird dort geschlossen
  }

  function initSlashHints() {
    const input = document.getElementById('message-input');
    if (input) input.addEventListener('blur', () => setTimeout(hideSlashHints, 120));
  }

  return {
    sendMessage,
    sendOrAbort,
    abortStreaming,
    toggleThinking,
    initThinking,
    uploadFile,
    loadConversation,
    newConversation,
    loadConversationList,
    importConversation,
    updateSlashHints,
    onSlashHintKeydown,
    hideSlashHints,
    initSlashHints,
    renderMarkdown,   // wiederverwendbar (Dokumentengenerator, Recherche): identische
                      // Formel-/Normen-/Code-Aufbereitung wie im Chat
  };
})();
