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
              if (event.tokens && typeof TokenMeter !== 'undefined') TokenMeter.add(event.tokens);
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
    wrapper.style.cssText = 'margin:10px 0';

    const mapEl = document.createElement('div');
    mapEl.style.cssText = 'height:360px;border-radius:8px;overflow:hidden;box-shadow:0 2px 12px #0006';
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

  function renderMarkdown(el, text) {
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
        const btn = document.createElement('button');
        btn.textContent = 'Kopieren';
        btn.style.cssText = 'position:absolute;top:8px;right:8px;padding:3px 8px;font-size:11px;background:#333;color:#aaa;border:1px solid #555;border-radius:4px;cursor:pointer';
        pre.style.position = 'relative';
        btn.addEventListener('click', () => {
          navigator.clipboard.writeText(pre.querySelector('code')?.textContent || pre.textContent);
          btn.textContent = '✓';
          setTimeout(() => { btn.textContent = 'Kopieren'; }, 1500);
        });
        pre.appendChild(btn);
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
        TokenMeter.add({ in: ev.tokens.in || 0, out: ev.tokens.out || 0 });
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
    renderMarkdown,   // wiederverwendbar (Dokumentengenerator, Recherche): identische
                      // Formel-/Normen-/Code-Aufbereitung wie im Chat
  };
})();
