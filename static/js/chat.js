/* ── AI_Framework_Thomas Chat ─────────────────────────────────────────────────────────── */

const Chat = (() => {
  let messages = [];      // { role, content, files }
  let isStreaming = false;
  let pendingFiles = [];  // { id, filename, is_image }
  let currentConvId = null;
  let abortController = null;   // bricht den laufenden /api/chat-Stream ab
  let showThinking = false;    // Denkprozess-Panel aktiv?

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

    // Geführter Präsentationsassistent: „/praesentation <Thema>" öffnet ein kurzes
    // Interview (Zielgruppe/Ziel/Umfang), dann läuft Gliederung → Webrecherche je
    // Punkt → Bilder (flächiges Deckblatt + Abschluss) automatisch durch.
    const pr = _parseIllustratedPres(text);
    if (pr) {
      input.value = '';
      autoResizeTextarea(input);
      if (pr.topic) _openPresInterview(pr.topic, pr.images);
      else showToast('Bitte nach „/praesentation" ein Thema angeben');
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

    // Rückfragen: „/frag <Aufgabe>" erzeugt eine dynamische Eingabemaske (Text/
    // Auswahl), deren Antworten an die Aufgabe gehängt und normal gesendet werden.
    const fr = _parseFrag(text);
    if (fr) {
      input.value = '';
      autoResizeTextarea(input);
      runFrag(fr.task);
      return;
    }

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
            if (['text', 'image', 'map', 'canvas', 'diagram', 'done', 'error'].includes(event.type)) clearWorking();
            if (event.type === 'tool_start') {
              toolStatusEl = showToolStatus(assistantRow, event.tool, event.args);
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
      `<span title="Score ${s.score}">📄 ${escHtml(s.filename)} <span class="planner-muted">(${escHtml(s.collection)})</span></span>`
    ).join(' · ');
    box.innerHTML = `📚 <strong>Kontext aus Wissenssammlung:</strong> ${items}`;
    container.insertBefore(box, beforeEl);
    scrollToBottom();
  }

  function insertImage(container, src) {
    const wrapper = document.createElement('div');
    wrapper.style.cssText = 'margin: 10px 0';
    const img = document.createElement('img');
    img.src = src;
    img.style.cssText = 'max-width:100%;border-radius:8px;display:block;box-shadow:0 2px 12px #0006';
    img.alt = 'Diagramm';
    wrapper.appendChild(img);
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
  function makeWorking(label) {
    const el = document.createElement('div');
    el.className = 'chat-working';
    el.innerHTML = `<span class="hourglass">⏳</span><span class="spinner"></span>`
      + `<span>${escHtml(label || 'arbeitet')}</span><span class="cw-dots"></span>`;
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
  function _openPresInterview(topic, imagesDirective) {
    const old = document.getElementById('pres-interview'); if (old) old.remove();
    const imgMode = imagesDirective === 'all' ? 'all' : imagesDirective === 'none' ? 'none' : 'smart';
    const ov = document.createElement('div');
    ov.id = 'pres-interview';
    ov.style.cssText = 'position:fixed;inset:0;z-index:9999;background:rgba(0,0,0,.55);display:flex;align-items:center;justify-content:center;padding:16px';
    const fld = 'width:100%;padding:7px 9px;border-radius:7px;border:1px solid var(--border,#334);background:var(--bg-input,#0e141b);color:var(--text,#e6edf3)';
    ov.innerHTML = `
      <div style="background:var(--bg-panel,#1b2330);color:var(--text,#e6edf3);border:1px solid var(--border,#334);border-radius:12px;max-width:560px;width:100%;max-height:90vh;overflow:auto;padding:18px 20px;box-shadow:0 10px 40px #000a">
        <div style="font-weight:700;font-size:1.1em;margin-bottom:4px">🖼️ Präsentation erstellen</div>
        <div style="opacity:.7;margin-bottom:14px">Thema: <b>${escHtml(topic)}</b></div>
        <label style="display:block;margin:8px 0 3px">Zielgruppe</label>
        <input id="pi-audience" type="text" placeholder="z. B. Geschäftsführung, Studierende, Laien" style="${fld}">
        <label style="display:block;margin:10px 0 3px">Ziel / Zweck</label>
        <input id="pi-goal" type="text" placeholder="z. B. überzeugen, informieren, Entscheidung vorbereiten" style="${fld}">
        <label style="display:block;margin:10px 0 3px">Umfang</label>
        <div id="pi-count"></div>
        <label style="display:block;margin:10px 0 3px">Bilder</label>
        <div id="pi-images"></div>
        <label style="display:block;margin:10px 0 3px">Bildwünsche / Stil (optional)</label>
        <input id="pi-wishes" type="text" placeholder="z. B. fotorealistisch, minimalistisch, Aquarell" style="${fld}">
        <label style="display:flex;align-items:center;gap:8px;margin:12px 0 4px;cursor:pointer"><input id="pi-web" type="checkbox" checked> Webrecherche je Gliederungspunkt</label>
        <div style="display:flex;gap:8px;justify-content:flex-end;margin-top:16px">
          <button id="pi-cancel" class="wf-action-btn">Abbrechen</button>
          <button id="pi-go" class="wf-action-btn" style="background:var(--accent,#2d6cdf);color:#fff">Erstellen</button>
        </div>
      </div>`;
    document.body.appendChild(ov);
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
    mkChips(ov.querySelector('#pi-count'), [[4, 'kurz (4)'], [6, 'mittel (6)'], [8, 'lang (8)']], 6);
    mkChips(ov.querySelector('#pi-images'), [['smart', 'KI entscheidet'], ['all', 'alle Folien'], ['none', 'keine']], imgMode);
    const close = () => ov.remove();
    ov.addEventListener('click', e => { if (e.target === ov) close(); });
    document.addEventListener('keydown', function esc(e) { if (e.key === 'Escape') { close(); document.removeEventListener('keydown', esc); } });
    ov.querySelector('#pi-cancel').onclick = close;
    ov.querySelector('#pi-go').onclick = () => {
      const params = {
        topic,
        audience: ov.querySelector('#pi-audience').value.trim(),
        goal: ov.querySelector('#pi-goal').value.trim(),
        count: parseInt(ov.querySelector('#pi-count').dataset.val, 10) || 6,
        image_mode: ov.querySelector('#pi-images').dataset.val || 'smart',
        image_wishes: ov.querySelector('#pi-wishes').value.trim(),
        web: ov.querySelector('#pi-web').checked,
      };
      close();
      runGuidedPresentation(params);
    };
    setTimeout(() => { const a = ov.querySelector('#pi-audience'); if (a) a.focus(); }, 30);
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
        <img src="${dataUrl}" style="max-width:100%;max-height:36vh;border-radius:8px;display:block;margin:0 auto 12px">
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
      close();
      _doBildEdit(dataUrl, instr, strength);
    };
    setTimeout(() => { const t = ov.querySelector('#be-instruction'); if (t) t.focus(); }, 30);
  }

  async function _doBildEdit(dataUrl, instruction, strength) {
    showWelcome(false);
    appendMessage('user', `✏️ Bild verändern: ${instruction}`);
    const row = appendMessage('assistant', '', [], true);
    const content = row.querySelector('.bubble-content');
    insertImage(content, dataUrl);   // Original
    const workingEl = makeWorking('Bild wird verändert'); content.appendChild(workingEl);
    const textEl = document.createElement('div'); textEl.className = 'bubble-text'; content.appendChild(textEl);
    try {
      const resp = await fetch('/api/image/edit', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ image: dataUrl, prompt: instruction, strength }),
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
      bar.appendChild(bAgain); content.appendChild(bar);
      messages.push({ role: 'user', content: `Bild verändern: ${instruction}` });
      messages.push({ role: 'assistant', content: '(verändertes Bild)' });
    } catch (e) {
      workingEl.remove();
      textEl.innerHTML = `<em style="color:#ef4444">Fehlgeschlagen: ${escHtml(e.message)}</em>`;
    }
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
    textEl.appendChild(makeWorking('🎨 Bild wird erzeugt (das kann etwas dauern)'));
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
      insertImage(content, data.image);
      // Bildunterschrift + Speichern-Link
      const cap = document.createElement('div');
      cap.style.cssText = 'font-size:12px;color:var(--text-muted);margin-top:2px';
      const dl = document.createElement('a');
      dl.href = data.image;
      dl.download = 'bild_' + Date.now() + '.png';
      dl.textContent = '⬇ Speichern';
      dl.style.cssText = 'color:var(--accent, #3b76ba);text-decoration:none;margin-left:8px';
      cap.appendChild(document.createTextNode('🎨 ' + prompt));
      cap.appendChild(dl);
      content.appendChild(cap);
      scrollToBottom();
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

  // ── Befehls-Autocomplete in der Chatbox („/") ───────────────────────────────
  // Beim Tippen eines führenden „/" erscheint über der Eingabe eine graue Liste der
  // verfügbaren Slash-Befehle. Auswahl per Klick, Tab oder ↑/↓+Tab; Esc schließt.
  const SLASH_COMMANDS = [
    { key: '/such', ins: '/such ', cmd: '/such …', desc: 'Alternative Suchbegriffe finden + Web durchsuchen (auch /suche, /finde)' },
    { key: '/recherche', ins: '/recherche ', cmd: '/recherche …', desc: 'Tiefe Recherche: mehrere Aspekte im Web, steuerbare Tiefe & Länge (auch /deep, /tief)' },
    { key: '/frag', ins: '/frag ', cmd: '/frag …', desc: 'Rückfragen-Maske: fehlende Infos per Formular ergänzen, dann antworten' },
    { key: '/bild', ins: '/bild ', cmd: '/bild …', desc: 'Bild aus Beschreibung erzeugen (lokal SD-WebUI oder API)' },
    { key: '/bildhelp', ins: '/bildhelp', cmd: '/bildhelp', desc: 'Geführter Bild-Dialog: Motiv, Stil, Perspektive, Beleuchtung, Format' },
    { key: '/bildprompt', ins: '/bildprompt ', cmd: '/bildprompt [Stil]', desc: 'Bild → Prompt: Bild auswählen, Vision-Modell leitet einen Text-zu-Bild-Prompt ab (→ „🎨 Bild daraus erzeugen")' },
    { key: '/bildedit', ins: '/bildedit ', cmd: '/bildedit [Anweisung]', desc: 'Bildbearbeitung: Bild hochladen + sagen, wie es verändert werden soll (img2img, lokal über Z-Image oder ein fähiges API-Modell); Stärke wählbar' },
    { key: '/praesentation', ins: '/praesentation ', cmd: '/praesentation …', desc: 'Geführter Präsentationsassistent: kurzes Interview (Zielgruppe/Ziel/Umfang/Bilder) → schlüssige Gliederung + Inhaltsverzeichnis → Webrecherche je Punkt → flächiges Deckblatt & Abschlussbild, Inhaltsfolien zweispaltig → Canvas' },
    { key: '/dd',   ins: '/dd',    cmd: '/dd<N>',  desc: 'Deepdive: N Vertiefungsfragen zur letzten Antwort (z. B. /dd10)' },
    { key: '/ddd',  ins: '/ddd',   cmd: '/ddd<N>', desc: 'Deepdive-Dokument: N Kapitel zur letzten Antwort' },
    { key: '/plan', ins: '/plan ', cmd: '/plan …', desc: 'Strategie → Agenten → Plan → Jury aus dem Verlauf (/planN für Aufgabenzahl)' },
    { key: '/workflow', ins: '/workflow ', cmd: '/workflow 1. … 2. …', desc: 'Arbeitsablauf: nummerierte Schritte nacheinander. Pro Schritt Tags [lokal] [api] [web] [bild] [sprache] (z. B. „1. [lokal,web] recherchiere … 2. [api] verarbeite … 3. [bild] erzeuge ein Bild von … 4. [sprache] fasse es als Sprachnachricht"). Ergebnis → Chat/Präsentation/Planer' },
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
      + `<span class="slash-hint-desc">${escHtml(c.desc)}</span></div>`).join('')
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
