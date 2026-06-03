/* ── AI_Framework_Thomas Chat ─────────────────────────────────────────────────────────── */

const Chat = (() => {
  let messages = [];      // { role, content, files }
  let isStreaming = false;
  let pendingFiles = [];  // { id, filename, is_image }
  let currentConvId = null;

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
    let name = 'AI_Framework_Thomas';
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

  async function sendMessage() {
    if (isStreaming) return;

    const input = document.getElementById('message-input');
    const text = input.value.trim();
    if (!text && pendingFiles.length === 0) return;

    showWelcome(false);
    isStreaming = true;
    setBtnSendState(false);

    const model = document.getElementById('model-select').value;
    const agentId = document.getElementById('agent-select').value || null;
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
    let fullText = '';

    // Tool-Status-Element
    let toolStatusEl = null;

    // SSE-Stream starten
    try {
      const resp = await fetch('/api/chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          messages: messages.filter(m => m.role !== 'system'),
          model,
          agent_id: agentId || undefined,
          use_tools: useSearch,
          conversation_id: currentConvId,
          rag_collections: (typeof RAG !== 'undefined') ? RAG.selectedCollections() : [],
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
            } else if (event.type === 'rag') {
              insertRagSources(bubbleContent, textEl, event.sources);
            } else if (event.type === 'adaptive') {
              insertAdaptiveNote(bubbleContent, textEl, event.role);
            }
          } catch (_) {}
        }
      }
    } catch (e) {
      textEl.innerHTML = `<em style="color:#ef4444">Fehler: ${e.message}</em>`;
    }

    // Cursor entfernen, Markdown rendern (nur das Textelement, Medien bleiben erhalten)
    const cursor = textEl.querySelector('.cursor');
    if (cursor) cursor.remove();
    renderMarkdown(textEl, fullText);

    messages.push({ role: 'assistant', content: fullText });

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
      // Code-Highlighting
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
    btn.disabled = !enabled;
    btn.textContent = enabled ? '↑' : '⏳';
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

  return {
    sendMessage,
    uploadFile,
    loadConversation,
    newConversation,
    loadConversationList,
    importConversation,
    renderMarkdown,   // wiederverwendbar (Dokumentengenerator, Recherche): identische
                      // Formel-/Normen-/Code-Aufbereitung wie im Chat
  };
})();
