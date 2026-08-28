/* AI_Framework_Thomas — Tabellen-Recherche (Matrix) */

const MatrixResearch = (() => {

  /* ── Zustand ──────────────────────────────────────────────────────── */
  // rows: Array von { topic: string }
  // cols: Array von { prompt: string }
  // cells[r][c]: { status: 'empty'|'running'|'done', text: string }
  let _rows  = [{ topic: '' }, { topic: '' }, { topic: '' }];
  let _cols  = [{ prompt: '', agent: '', tool: '' }, { prompt: '', agent: '', tool: '' }];
  let _cells = [];  // _cells[r][c]
  let _running = false;
  let _favAgents = [];  // nur als Favorit markierte Agenten (auswählbar pro Spalte)
  let _partnerMode = false;  // Partner-Auswertung aktiv (Spalten-Kaskade + Token-Label)

  /* ── Wissensgraph ────────────────────────────────────────────────── */
  // Knoten = Zeilen (stabile gid je Zeile, siehe _rowGid). Kanten = vom Nutzer
  // bestätigte/erstellte Verknüpfungen [{source, target, label}] zwischen gids.
  let _graphEdges = [];        // [{source, target, label}]
  let _graphPos   = {};        // gid -> {x,y}  (gemerkte Knoten-Positionen)
  let _graphOpen  = false;     // Graph-Pane sichtbar?
  let _cy = null;              // Cytoscape-Instanz (lazy)
  let _connectMode = false;    // Verbindungsmodus: zwei Knoten → Kante
  let _connectFrom = null;     // gid des zuerst angeklickten Knotens
  let _autoGraph = true;       // nach „▶ Alle ausführen" automatisch Graphen erzeugen

  // Merkmal-Knoten (Hubs): je Zeile typisierte Merkmale; geteilte Werte werden zu
  // gemeinsamen Knoten (farbig je Kategorie), an denen mehrere Zeilen hängen.
  let _rowAttrs   = {};        // gid -> [{category, value}]
  let _attrCats   = ['Tätigkeit', 'Ort', 'Tool', 'Aufgabenbereich', 'Name'];
  let _showHubs   = true;      // Merkmal-Knoten anzeigen?
  let _sharedOnly = true;      // nur von ≥2 Zeilen geteilte Merkmale als Hub zeigen
  const _CAT_COLORS = ['#4f8cff', '#22c55e', '#f59e0b', '#ec4899', '#a855f7', '#14b8a6', '#ef4444', '#84cc16'];

  const STORAGE_KEY = 'ai_framework_thomas_matrix_v1';

  function _saveState() {
    try {
      // Laufende Zellen vor dem Speichern als 'empty' markieren
      const saveCells = _cells.map(row =>
        row.map(c => c.status === 'running' ? { status: 'empty', text: '' } : c)
      );
      localStorage.setItem(STORAGE_KEY, JSON.stringify({
        rows: _rows, cols: _cols, cells: saveCells, partnerMode: _partnerMode,
        graphEdges: _graphEdges, graphPos: _graphPos, graphOpen: _graphOpen,
        rowAttrs: _rowAttrs, attrCats: _attrCats, showHubs: _showHubs, sharedOnly: _sharedOnly,
        savedAt: Date.now(),
      }));
      _showSaveIndicator();
    } catch (_) {}
  }

  function _loadState() {
    try {
      const raw = localStorage.getItem(STORAGE_KEY);
      if (!raw) return false;
      const state = JSON.parse(raw);
      if (state.rows?.length && state.cols?.length && state.cells?.length) {
        _rows  = state.rows;
        _cols  = state.cols;
        _cells = state.cells;
        _partnerMode = !!state.partnerMode;
        _graphEdges = Array.isArray(state.graphEdges) ? state.graphEdges : [];
        _graphPos   = (state.graphPos && typeof state.graphPos === 'object') ? state.graphPos : {};
        _graphOpen  = !!state.graphOpen;
        _rowAttrs   = (state.rowAttrs && typeof state.rowAttrs === 'object') ? state.rowAttrs : {};
        if (Array.isArray(state.attrCats) && state.attrCats.length) _attrCats = state.attrCats;
        _showHubs   = state.showHubs !== false;
        _sharedOnly = state.sharedOnly !== false;
        return true;
      }
    } catch (_) {}
    return false;
  }

  let _saveIndicatorTimer = null;
  function _showSaveIndicator() {
    const el = document.getElementById('matrix-autosave-label');
    if (!el) return;
    el.style.display = '';
    clearTimeout(_saveIndicatorTimer);
    _saveIndicatorTimer = setTimeout(() => { el.style.display = 'none'; }, 2000);
  }

  function _initCells() {
    _cells = _rows.map(() => _cols.map(() => ({ status: 'empty', text: '' })));
  }

  /* ── Modell: zentral aus dem Profil (Matrix nutzt science:true) ───── */
  function _getModel() {
    return (typeof Profile !== 'undefined' ? Profile.modelFor('science') : '') || undefined;
  }

  /* ── Recherche-/Prüf-Agenten (pro Spalte wählbar) ─────────────────── */
  // Nur als Favorit markierte Agenten laden (Recherche-/Prüf-Agenten zuerst,
  // damit z. B. der Firmenagent für den Messebesuch schnell auffindbar ist).
  async function _loadAgents() {
    let agents = [];
    try {
      agents = await (await fetch('/api/agents')).json();
    } catch (_) { return; }
    const rank = a => (a.category === 'Recherche' || a.category === 'Prüfung') ? 0 : 1;
    _favAgents = agents
      .filter(a => a.favorite)
      .sort((a, b) => rank(a) - rank(b) || (a.name || '').localeCompare(b.name || ''));
    _render();
  }

  // Options-HTML für ein Spalten-Agenten-Dropdown (markiert den gewählten Agenten)
  function _agentOptionsHtml(selectedId) {
    let html = `<option value="">Kein Agent</option>`;
    for (const a of _favAgents) {
      const sel = a.id === selectedId ? ' selected' : '';
      html += `<option value="${escHtml(a.id)}"${sel}>${escHtml((a.icon || '🤖') + ' ' + a.name)}</option>`;
    }
    return html;
  }

  // Wählbares Werkzeug pro Spalte (leer = Automatisch = alle Werkzeuge; 'none' = ohne Werkzeug).
  // Namen entsprechen den Tool-Namen des Chat-Tool-Loops (ChatRequest.tools).
  const _TOOL_CHOICES = [
    ['', 'Werkzeug: automatisch'],
    ['web_search', '🔍 Websuche'],
    ['search_knowledge_base', '📚 Wissensdatenbank'],
    ['calculate', '🧮 Rechnen'],
    ['create_diagram', '🕸 Diagramm'],
    ['route_planner', '🗺 Route'],
    ['none', '🚫 Kein Werkzeug'],
  ];
  function _toolOptionsHtml(selected) {
    return _TOOL_CHOICES.map(([v, l]) =>
      `<option value="${v}"${v === (selected || '') ? ' selected' : ''}>${l}</option>`).join('');
  }

  /* ── Zelleninhalt rendern (Markdown + LaTeX bei fertigen Zellen) ──── */
  function _renderCell(div, cell) {
    if (cell.status === 'running') { div.textContent = '⟳ Suche läuft…'; return; }
    if (cell.status === 'done' && cell.text && typeof marked !== 'undefined') {
      if (window._ensureKatexMarked) window._ensureKatexMarked();
      div.innerHTML = marked.parse(cell.text, { gfm: true, breaks: true });
      div.querySelectorAll('a[href]').forEach(a => { a.target = '_blank'; a.rel = 'noopener noreferrer'; });
      _addCellExpand(div);
    } else {
      div.textContent = cell.text || '';
    }
  }

  // ⤢-Knopf in der Zelle: öffnet den vollen Inhalt (inkl. großer Tabellen) im Modal.
  // Bei Hover sichtbar; Klick darf nicht die Zelle (= erneut ausführen) auslösen.
  function _addCellExpand(div) {
    const r = +div.dataset.row, c = +div.dataset.col;
    const btn = document.createElement('button');
    btn.className = 'cell-expand';
    btn.textContent = '⤢';
    btn.title = 'Inhalt vergrößern';
    btn.addEventListener('click', e => { e.stopPropagation(); _openCellModal(r, c); });
    div.appendChild(btn);
  }

  // Vollansicht einer Zelle in einem Overlay (lazy erzeugt, wiederverwendet).
  function _openCellModal(r, c) {
    const cell = _cells[r]?.[c];
    if (!cell || !cell.text) return;
    let overlay = document.getElementById('matrix-cell-modal');
    if (!overlay) {
      overlay = document.createElement('div');
      overlay.id = 'matrix-cell-modal';
      overlay.innerHTML =
        '<div id="matrix-cell-modal-box">'
        + '<div id="matrix-cell-modal-head"><span id="matrix-cell-modal-title"></span>'
        + '<button id="matrix-cell-modal-close" title="Schließen (Esc)">✕</button></div>'
        + '<div id="matrix-cell-modal-body"></div></div>';
      document.body.appendChild(overlay);
      const close = () => { overlay.style.display = 'none'; };
      overlay.addEventListener('click', e => { if (e.target === overlay) close(); });
      overlay.querySelector('#matrix-cell-modal-close').addEventListener('click', close);
      document.addEventListener('keydown', e => {
        if (e.key === 'Escape' && overlay.style.display === 'flex') close();
      });
    }
    const topic = (_rows[r]?.topic || `Zeile ${r + 1}`).trim().slice(0, 70);
    const colLbl = (_cols[c]?.label || _cols[c]?.prompt || `Spalte ${c + 1}`).trim().slice(0, 70);
    overlay.querySelector('#matrix-cell-modal-title').textContent = `${topic}  ·  ${colLbl}`;
    const body = overlay.querySelector('#matrix-cell-modal-body');
    if (typeof marked !== 'undefined') {
      if (window._ensureKatexMarked) window._ensureKatexMarked();
      body.innerHTML = marked.parse(cell.text, { gfm: true, breaks: true });
      body.querySelectorAll('a[href]').forEach(a => { a.target = '_blank'; a.rel = 'noopener noreferrer'; });
    } else {
      body.textContent = cell.text;
    }
    body.scrollTop = 0;
    overlay.style.display = 'flex';
  }

  /* ── Tabelle rendern ─────────────────────────────────────────────── */
  function _render() {
    const headerRow = document.getElementById('matrix-header-row');
    const tbody     = document.getElementById('matrix-body');
    if (!headerRow || !tbody) return;

    // Header
    headerRow.innerHTML = '';

    // Linke obere Ecke
    const thEmpty = document.createElement('th');
    thEmpty.style.minWidth = '160px';
    thEmpty.innerHTML = '<div style="padding:6px 8px;color:var(--text-muted);font-size:11px">Thema / Prompt →</div>';
    headerRow.appendChild(thEmpty);

    // Spalten-Köpfe
    for (let c = 0; c < _cols.length; c++) {
      const col = _cols[c];
      const active = col.active !== false;
      const th = document.createElement('th');
      const labelHtml = col.label ? `<div class="matrix-col-label">${escHtml(col.label)}</div>` : '';
      th.innerHTML = `
        ${labelHtml}
        <div class="matrix-header-cell-wrap">
          <textarea class="matrix-cell-input" rows="2" placeholder="Suchprompt…" data-col="${c}">${escHtml(col.prompt)}</textarea>
          <button class="btn-del-matrix-col" data-col="${c}" title="Spalte löschen">✕</button>
        </div>
        <select class="matrix-col-agent" data-col="${c}" title="Agent für diese Spalte (nur Favoriten)">${_agentOptionsHtml(col.agent || '')}</select>
        <select class="matrix-col-tool" data-col="${c}" title="Werkzeug für diese Spalte (automatisch = alle)">${_toolOptionsHtml(col.tool || '')}</select>
        <div class="matrix-col-ctrls">
          <label title="Diese Spalte bei „Alle ausführen“ berücksichtigen"><input type="checkbox" class="matrix-col-active" data-col="${c}"${active ? ' checked' : ''}> ausführen</label>
          <label title="Ergebnisse der vorherigen Spalten dieser Zeile als Kontext mitgeben"><input type="checkbox" class="matrix-col-ctx" data-col="${c}"${col.ctx ? ' checked' : ''}> Kontext</label>
          <button class="export-btn matrix-run-col" data-col="${c}" title="Nur diese Spalte für alle Zeilen ausführen">▶ Spalte</button>
        </div>`;
      headerRow.appendChild(th);
    }

    // Body
    tbody.innerHTML = '';
    for (let r = 0; r < _rows.length; r++) {
      const tr = document.createElement('tr');

      // Erste Spalte: Thema
      const tdTopic = document.createElement('td');
      tdTopic.innerHTML = `
        <div style="display:flex;align-items:center;gap:4px;padding:4px 6px">
          <textarea class="matrix-cell-input" rows="2" placeholder="Thema / Information…" data-row="${r}" style="flex:1">${escHtml(_rows[r].topic)}</textarea>
          <button class="btn-del-matrix-col" data-row="${r}" title="Zeile löschen" style="font-size:11px">✕</button>
        </div>`;
      tr.appendChild(tdTopic);

      // Ergebniszellen
      for (let c = 0; c < _cols.length; c++) {
        const cell = _cells[r]?.[c] || { status: 'empty', text: '' };
        const td = document.createElement('td');
        const div = document.createElement('div');
        div.className = `matrix-cell-result ${cell.status}`;
        div.dataset.row = r;
        div.dataset.col = c;
        _renderCell(div, cell);
        div.title = cell.status === 'done' ? 'Klick zum erneuten Ausführen' : 'Klick zum Ausführen';
        div.style.cursor = 'pointer';
        div.addEventListener('click', () => _runCell(r, c));
        td.appendChild(div);
        tr.appendChild(td);
      }
      tbody.appendChild(tr);
    }

    // Events für Header-Prompts
    headerRow.querySelectorAll('textarea[data-col]').forEach(ta => {
      ta.addEventListener('input', e => {
        _cols[+ta.dataset.col].prompt = ta.value;
        _saveState();
      });
    });
    headerRow.querySelectorAll('.matrix-col-agent[data-col]').forEach(sel => {
      sel.addEventListener('change', () => {
        _cols[+sel.dataset.col].agent = sel.value;
        _saveState();
      });
    });
    headerRow.querySelectorAll('.matrix-col-tool[data-col]').forEach(sel => {
      sel.addEventListener('change', () => {
        _cols[+sel.dataset.col].tool = sel.value;
        _saveState();
      });
    });
    headerRow.querySelectorAll('.btn-del-matrix-col[data-col]').forEach(btn => {
      btn.addEventListener('click', () => {
        const c = +btn.dataset.col;
        const lbl = (_cols[c]?.label || _cols[c]?.prompt || `Spalte ${c + 1}`).trim().slice(0, 50);
        if (!confirm(`Spalte „${lbl || 'Spalte ' + (c + 1)}“ mit allen Ergebnissen löschen?`)) return;
        _cols.splice(c, 1);
        _cells.forEach(row => row.splice(c, 1));
        _saveState();
        _render();
      });
    });
    headerRow.querySelectorAll('.matrix-col-active').forEach(cb => {
      cb.addEventListener('change', () => { _cols[+cb.dataset.col].active = cb.checked; _saveState(); });
    });
    headerRow.querySelectorAll('.matrix-col-ctx').forEach(cb => {
      cb.addEventListener('change', () => { _cols[+cb.dataset.col].ctx = cb.checked; _saveState(); });
    });
    headerRow.querySelectorAll('.matrix-run-col').forEach(btn => {
      btn.addEventListener('click', () => _runColumn(+btn.dataset.col));
    });

    // Events für Zeilen-Themen
    tbody.querySelectorAll('textarea[data-row]').forEach(ta => {
      ta.addEventListener('input', () => {
        _rows[+ta.dataset.row].topic = ta.value;
        _saveState();
      });
    });
    tbody.querySelectorAll('.btn-del-matrix-col[data-row]').forEach(btn => {
      btn.addEventListener('click', () => {
        const r = +btn.dataset.row;
        const lbl = (_rows[r]?.topic || `Zeile ${r + 1}`).trim().slice(0, 50);
        if (!confirm(`Zeile „${lbl || 'Zeile ' + (r + 1)}“ mit allen Ergebnissen löschen?`)) return;
        // zugehörigen Graph-Zustand der Zeile mit aufräumen (gid-basiert)
        const gid = _rows[r]?._gid;
        if (gid) {
          delete _rowAttrs[gid];
          delete _graphPos[gid];
          _graphEdges = _graphEdges.filter(e => e.source !== gid && e.target !== gid);
        }
        _rows.splice(r, 1);
        _cells.splice(r, 1);
        _saveState();
        _render();
      });
    });

    // Graph mit dem aktuellen Knoten-Set abgleichen (nur wenn sichtbar).
    if (_graphOpen) _syncGraph();
  }

  /* ── Einzelne Zelle ausführen ────────────────────────────────────── */
  async function _runCell(r, c) {
    const topic  = _rows[r]?.topic?.trim();
    const prompt = _cols[c]?.prompt?.trim();
    if (!topic || !prompt) { showToast('Bitte Thema und Prompt eingeben'); return; }

    _cells[r][c] = { status: 'running', text: '' };
    _updateCellUI(r, c);

    const model = _getModel();
    const agentId = _cols[c]?.agent || '';
    const query = `${topic}: ${prompt}`;
    let userMsg = `Recherchiere folgendes: "${query}"\n\nGib eine kompakte, strukturierte Antwort, die direkt zum Thema "${topic}" und zur Frage "${prompt}" passt.`;

    // Spalten-Kaskade: Wenn „Kontext" aktiv ist, die bereits fertigen Ergebnisse der
    // vorherigen Spalten dieser Zeile mitgeben (z. B. damit die Mail-Spalte die zuvor
    // gefundenen Kontaktdaten kennt).
    if (_cols[c]?.ctx) {
      const prior = [];
      for (let pc = 0; pc < c; pc++) {
        const pcell = _cells[r]?.[pc];
        if (pcell?.status === 'done' && pcell.text?.trim()) {
          const lbl = _cols[pc].label || _cols[pc].prompt || `Spalte ${pc + 1}`;
          prior.push(`### ${lbl}\n${pcell.text.trim()}`);
        }
      }
      if (prior.length) {
        userMsg += `\n\nBereits ermittelte Informationen zu "${topic}" (als verlässliche Grundlage nutzen, nicht widersprechen):\n\n${prior.join('\n\n')}`;
      }
    }

    // Mit gewähltem Agent: dessen System-Prompt (z. B. Bewertung/Plausibilitätsprüfung)
    // steuert die Antwort. Ohne Agent: einfacher Recherche-Assistent.
    const body = { model, use_tools: true, science: true };
    // Spalten-Werkzeug: 'none' = ohne Werkzeug, '<name>' = nur dieses Werkzeug, '' = automatisch.
    const colTool = _cols[c]?.tool || '';
    if (colTool === 'none') body.use_tools = false;
    else if (colTool) body.tools = [colTool];
    if (agentId) {
      body.agent_id = agentId;
      body.messages = [{ role: 'user', content: userMsg }];
    } else {
      body.messages = [
        {
          role: 'system',
          content: 'Du bist ein Recherche-Assistent. Suche präzise Informationen und gib eine kompakte, strukturierte Antwort auf Deutsch. Maximal 3-4 Sätze oder eine kurze Liste.',
        },
        { role: 'user', content: userMsg },
      ];
    }

    try {
      const resp = await fetch('/api/chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });

      const reader = resp.body.getReader();
      const dec    = new TextDecoder();
      let buf = '', text = '';

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buf += dec.decode(value, { stream: true });
        const lines = buf.split('\n');
        buf = lines.pop();
        for (const line of lines) {
          if (!line.startsWith('data: ')) continue;
          try {
            const ev = JSON.parse(line.slice(6));
            if (ev.type === 'text') { text += ev.content; }
            if (ev.type === 'done') {
              if (ev.tokens && typeof TokenMeter !== 'undefined')
                TokenMeter.add(ev.tokens, _partnerMode ? 'Partner-Auswertung' : 'Matrix-Recherche');
              break;
            }
          } catch (_) {}
        }
      }
      _cells[r][c] = { status: 'done', text: text.trim() || '(kein Ergebnis)' };
    } catch (e) {
      _cells[r][c] = { status: 'done', text: `Fehler: ${e.message}` };
    }
    _updateCellUI(r, c);
    _saveState();
  }

  function _updateCellUI(r, c) {
    const cell = _cells[r]?.[c];
    if (!cell) return;
    const div = document.querySelector(`.matrix-cell-result[data-row="${r}"][data-col="${c}"]`);
    if (!div) return;
    div.className = `matrix-cell-result ${cell.status}`;
    _renderCell(div, cell);
  }

  /* ── Alle Zellen ausführen ───────────────────────────────────────── */
  async function _runAll() {
    if (_running) { showToast('Läuft bereits…'); return; }
    _running = true;
    document.getElementById('btn-matrix-run-all').disabled = true;
    let count = 0;
    for (let r = 0; r < _rows.length; r++) {
      for (let c = 0; c < _cols.length; c++) {
        if (!_rows[r].topic?.trim() || !_cols[c].prompt?.trim()) continue;
        if (_cols[c].active === false) continue;   // deaktivierte Spalte überspringen
        await _runCell(r, c);
        count++;
      }
    }
    _running = false;
    document.getElementById('btn-matrix-run-all').disabled = false;
    showToast(`${count} Zellen ausgeführt`);
    // Anschluss-Stufe: aus der frisch gefüllten Tabelle automatisch einen Wissensgraphen
    // erzeugen (Knoten = Zeilen, Verknüpfungen per KI). Nur wenn überhaupt etwas lief.
    if (count && _autoGraph) await _tableToGraph();
  }

  /* ── Nur eine Spalte (für alle Zeilen) ausführen – „stufenweise" ─────── */
  async function _runColumn(c) {
    if (_running) { showToast('Läuft bereits…'); return; }
    const col = _cols[c];
    if (!col || !col.prompt?.trim()) { showToast('Spalte hat keinen Prompt'); return; }
    _running = true;
    document.getElementById('btn-matrix-run-all').disabled = true;
    let count = 0;
    for (let r = 0; r < _rows.length; r++) {
      if (!_rows[r].topic?.trim()) continue;
      await _runCell(r, c);
      count++;
    }
    _running = false;
    document.getElementById('btn-matrix-run-all').disabled = false;
    showToast(`Spalte „${col.label || col.prompt}“ – ${count} Zellen ausgeführt`);
  }

  /* ── Als XLSX exportieren ────────────────────────────────────────── */
  async function _exportXlsx() {
    const headers = ['Thema', ..._cols.map(c => c.prompt || '(leer)')];
    const rows = _rows.map((row, r) => [
      row.topic,
      ..._cols.map((_, c) => _cells[r]?.[c]?.text || ''),
    ]);
    try {
      const resp = await fetch('/api/export/xlsx', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ title: 'Matrix-Recherche', headers, rows }),
      });
      const blob = await resp.blob();
      const url  = URL.createObjectURL(blob);
      const a    = document.createElement('a');
      a.href = url; a.download = 'matrix_recherche.xlsx'; a.click();
      URL.revokeObjectURL(url);
    } catch (e) {
      showToast('Export fehlgeschlagen');
    }
  }

  /* ── Eine Zelle = ein Markdown-Dokument (thema_prompt.md) ─────────── */
  // Bereinigt Text zu einem Dateinamen-tauglichen Slug.
  function _slug(s) {
    return (s || '').trim().toLowerCase()
      .replace(/[^\p{L}\p{N}]+/gu, '_')
      .replace(/^_+|_+$/g, '')
      .slice(0, 60) || 'leer';
  }

  // Baut für jede befüllte Zelle ein Dokument: Thema + Prompt als Überschrift
  // (Kontext für den Embedder!), dann der Zellinhalt. Name = thema_prompt.
  function _cellDocs() {
    const docs = [];
    const used = {};
    for (let r = 0; r < _rows.length; r++) {
      const topic = _rows[r]?.topic?.trim();
      if (!topic) continue;
      for (let c = 0; c < _cols.length; c++) {
        const prompt = _cols[c]?.prompt?.trim();
        const txt = _cells[r]?.[c]?.text?.trim();
        if (!prompt || !txt) continue;
        const base = `${_slug(topic)}_${_slug(prompt)}`;
        let name = base, i = 2;
        while (used[name]) name = `${base}_${i++}`;
        used[name] = true;
        docs.push({ name: `${name}.md`, title: name, content: `# ${topic}\n## ${prompt}\n\n${txt}\n` });
      }
    }
    return docs;
  }

  // Alle Zellen einzeln als .md-Dateien in einem ZIP herunterladen.
  async function _exportMdZip() {
    const docs = _cellDocs();
    if (!docs.length) { showToast('Keine befüllten Zellen'); return; }
    showToast('⏳ ZIP wird erstellt…');
    try {
      const resp = await fetch('/api/matrix/export-md-zip', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ zipname: 'matrix_markdown', files: docs.map(d => ({ name: d.name, content: d.content })) }),
      });
      if (!resp.ok) throw new Error(resp.status);
      const blob = await resp.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url; a.download = 'matrix_markdown.zip'; a.click();
      URL.revokeObjectURL(url);
      showToast(`✓ ${docs.length} Markdown-Dateien als ZIP`);
    } catch (e) {
      showToast('Export fehlgeschlagen: ' + e.message);
    }
  }

  // Jede Zelle als eigenes Dokument direkt in eine Wissensdatenbank übernehmen.
  async function _ingestPerCell() {
    const docs = _cellDocs();
    if (!docs.length) { showToast('Keine befüllten Zellen'); return; }
    if (typeof RAG === 'undefined' || !RAG.pickCollection) { showToast('RAG nicht verfügbar'); return; }
    const cid = await RAG.pickCollection(`${docs.length} Zellen einzeln übernehmen (thema_prompt.md)`);
    if (!cid) return;
    let ok = 0, fail = 0;
    for (const d of docs) {
      try {
        const r = await fetch(`/api/rag/collections/${cid}/from-text`, {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ title: d.title, text: d.content }),
        });
        if (!r.ok) throw new Error();
        ok++;
      } catch (_) { fail++; }
      showToast(`⏳ ${ok + fail}/${docs.length} eingebettet…`);
    }
    showToast(`✓ ${ok} Dokumente übernommen${fail ? `, ${fail} fehlgeschlagen` : ''}`);
    if (RAG.loadCollections) RAG.loadCollections();
  }

  /* ── In Wissensdatenbank übernehmen (alles in EIN Dokument) ───────── */
  function _exportRag() {
    const parts = [`# Matrix-Recherche`];
    for (let r = 0; r < _rows.length; r++) {
      if (!_rows[r].topic?.trim()) continue;
      parts.push(`\n## ${_rows[r].topic}`);
      for (let c = 0; c < _cols.length; c++) {
        const ans = _cells[r]?.[c]?.text;
        if (_cols[c].prompt?.trim() && ans) parts.push(`\n### ${_cols[c].prompt}\n${ans}`);
      }
    }
    const text = parts.join('\n');
    if (typeof RAG !== 'undefined') RAG.ingestText('Matrix-Recherche', text);
  }

  /* ── CSV-Export ─────────────────────────────────────────────────── */
  function _exportCsv() {
    const sep = ';';
    const quote = v => '"' + String(v).replace(/"/g, '""') + '"';
    const lines = [];
    // Kopfzeile: leer + Prompts
    lines.push([quote('Thema'), ..._cols.map(c => quote(c.prompt || ''))].join(sep));
    // Datenzeilen
    for (let r = 0; r < _rows.length; r++) {
      const row = [quote(_rows[r].topic || '')];
      for (let c = 0; c < _cols.length; c++) {
        row.push(quote(_cells[r]?.[c]?.text || ''));
      }
      lines.push(row.join(sep));
    }
    const blob = new Blob(['﻿' + lines.join('\r\n')], { type: 'text/csv;charset=utf-8' });
    const url  = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url; a.download = 'matrix_recherche.csv'; a.click();
    URL.revokeObjectURL(url);
  }

  /* ── CSV-Import ──────────────────────────────────────────────────── */
  function _importCsv(file) {
    const reader = new FileReader();
    reader.onload = e => {
      try {
        let text = e.target.result;
        // BOM entfernen
        if (text.charCodeAt(0) === 0xFEFF) text = text.slice(1);
        const lines = text.split(/\r?\n/).filter(l => l.trim());
        if (lines.length < 2) { showToast('CSV zu kurz'); return; }

        // Trennzeichen erkennen (Semikolon oder Komma)
        const sep = lines[0].includes(';') ? ';' : ',';

        const parseLine = line => {
          const result = [];
          let cur = '', inQ = false;
          for (let i = 0; i < line.length; i++) {
            const ch = line[i];
            if (ch === '"') {
              if (inQ && line[i + 1] === '"') { cur += '"'; i++; }
              else inQ = !inQ;
            } else if (ch === sep && !inQ) {
              result.push(cur); cur = '';
            } else cur += ch;
          }
          result.push(cur);
          return result;
        };

        const header = parseLine(lines[0]);
        const newCols = header.slice(1).map(p => ({ prompt: p.trim(), agent: '' }));
        const newRows = [], newCells = [];

        for (let i = 1; i < lines.length; i++) {
          const parts = parseLine(lines[i]);
          newRows.push({ topic: parts[0]?.trim() || '' });
          const cellRow = newCols.map((_, c) => ({
            status: parts[c + 1]?.trim() ? 'done' : 'empty',
            text: parts[c + 1]?.trim() || '',
          }));
          newCells.push(cellRow);
        }

        _cols  = newCols;
        _rows  = newRows;
        _cells = newCells;
        _saveState();
        _render();
        showToast(`CSV importiert: ${newRows.length} Zeilen, ${newCols.length} Spalten`);
      } catch (err) {
        showToast('Fehler beim CSV-Lesen: ' + err.message);
      }
    };
    reader.readAsText(file, 'utf-8');
  }

  /* ════════════════════════════════════════════════════════════════════
     Partner-Auswertung: Firmenliste → Recherche-Stufen → Kaltakquise-Mail.
     Baut auf der Matrix-Engine auf (Zeilen = Firmen, Spalten = Stufen).
     ════════════════════════════════════════════════════════════════════ */

  // Die nötigen Agenten. Stabile IDs → idempotent (kein Duplikat beim erneuten
  // Einrichten). „Partner-Rechercheur" und „Akquise-Texter" sind bewusst editierbar
  // (im Agenten-Tab anpassbar) – wie vom Nutzer gewünscht.
  const PARTNER_AGENTS = {
    partner_recherche: {
      id: 'partner_recherche', name: 'Partner-Rechercheur', icon: '🔎',
      category: 'Recherche', favorite: true, tools: ['web_search', 'calculate'],
      model: null, rag_collections: [], example_code: '',
      description: 'Prüft per Websuche, ob eine Firma als Partner/Kunde interessant ist, erstellt ein Profil und findet Ansprechpartner.',
      system_prompt: 'Du bist ein B2B-Partner-Rechercheur. Recherchiere mit der Websuche öffentlich verfügbare Informationen zur genannten Firma. '
        + 'Beurteile zuerst knapp, ob die Firma als Geschäftspartner/Kunde interessant ist (Ja/Nein + ein Satz Begründung). '
        + 'Falls interessant: erstelle ein kurzes Profil (Geschäftsfeld, ungefähre Größe, Standort, Website, Relevanz) und nenne mögliche '
        + 'Ansprechpartner (Name, Rolle/Abteilung), soweit öffentlich auffindbar. Antworte strukturiert auf Deutsch, nur belegbare Fakten, '
        + 'keine Erfindungen. Markiere Unsicheres ausdrücklich als „unbestätigt".',
    },
    partner_kontakt: {
      id: 'partner_kontakt', name: 'Kontaktdaten-Rechercheur', icon: '📇',
      category: 'Recherche', favorite: true, tools: ['web_search', 'calculate'],
      model: null, rag_collections: [], example_code: '',
      description: 'Ermittelt geschäftliche Kontaktdaten (Name, Position, Telefon, E-Mail) der Ansprechpartner aus öffentlichen Quellen.',
      system_prompt: 'Du bist ein Rechercheur für geschäftliche Kontaktdaten. Ermittle über die Websuche die geschäftlichen Kontaktdaten der '
        + 'wichtigsten Ansprechpartner der Firma: Name, Position, geschäftliche Telefonnummer und E-Mail-Adresse. Nutze ausschließlich öffentlich '
        + 'verfügbare, berufliche Quellen (Impressum, Firmen-Website, offizielle Unternehmensprofile). Gib pro Person eine kurze Karteizeile aus. '
        + 'Wenn etwas nicht sicher belegbar ist, schreibe „nicht öffentlich auffindbar" statt zu raten. Antworte auf Deutsch.',
    },
    partner_social: {
      id: 'partner_social', name: 'Social-Media-Rechercheur', icon: '🌐',
      category: 'Recherche', favorite: true, tools: ['web_search', 'calculate'],
      model: null, rag_collections: [], example_code: '',
      description: 'Findet öffentliche Unternehmens-/Personenprofile auf einer sozialen Plattform (LinkedIn, X, Instagram, Facebook, GitHub).',
      system_prompt: 'Du bist ein Rechercheur für öffentliche berufliche bzw. Unternehmens-Profile in sozialen Netzwerken. Finde mit der Websuche '
        + '(z. B. per site:-Suche auf der jeweiligen Plattform) das passende öffentliche Profil zur Firma bzw. zu den genannten Ansprechpartnern '
        + 'auf der in der Aufgabe genannten Plattform. Gib die Profil-URL und die wichtigsten öffentlich sichtbaren Infos (Rolle, Schwerpunkte, '
        + 'letzte relevante Aktivität) an. Nur öffentlich zugängliche Informationen, keine Vermutungen. Wenn kein Profil auffindbar ist, sage das klar. '
        + 'Antworte auf Deutsch.',
    },
    partner_mail: {
      id: 'partner_mail', name: 'Akquise-Texter', icon: '✉️',
      category: 'Recherche', favorite: true, tools: ['web_search', 'calculate'],
      model: null, rag_collections: [], example_code: '',
      description: 'Formuliert auf Basis der Recherche eine personalisierte, seriöse Kaltakquise-E-Mail. Vom Nutzer anpassbar.',
      system_prompt: 'Du bist ein erfahrener Vertriebstexter für seriöse B2B-Kaltakquise. Formuliere auf Basis der zuvor recherchierten Firmen- und '
        + 'Personeninfos eine personalisierte Erstkontakt-E-Mail (Cold Outreach). Struktur: Betreffzeile, persönliche Anrede mit Namen (falls bekannt), '
        + 'kurzer Aufhänger mit konkretem Bezug zur Firma, 2–3 prägnante Absätze zum Mehrwert, ein klarer aber unaufdringlicher Call-to-Action, Grußformel. '
        + 'Höflich, kein Spam, DSGVO-bewusst. Nutze die gefundenen Kontaktdaten in Anrede und Signatur. Antworte auf Deutsch. '
        + 'HINWEIS: Diesen Agenten im Agenten-Tab an die eigene Firma/Tonalität anpassen.',
    },
  };

  // Vordefinierte Spalten der Partner-Auswertung. Spätere Stufen sind anfangs
  // deaktiviert (active:false) und nutzen den Kontext der vorherigen Spalten,
  // damit man sich Stufe für Stufe „durchhangeln" kann.
  function _PARTNER_COLUMNS() {
    const social = (plat, host) => ({
      label: plat, agent: 'partner_social', active: false, ctx: true,
      prompt: `Finde das öffentliche ${plat}-Profil (site:${host}) der Firma bzw. der Ansprechpartner und gib URL + wichtigste öffentliche Infos an.`,
    });
    return [
      {
        label: 'Interesse & Profil', agent: 'partner_recherche', active: true, ctx: false,
        prompt: 'Ist diese Firma ein interessanter Partner/Kunde? Kurzes Urteil (Ja/Nein + Begründung), dann – falls interessant – Detailprofil (Geschäftsfeld, Größe, Standort, Website) und mögliche Ansprechpartner.',
      },
      {
        label: 'Kontaktdaten', agent: 'partner_kontakt', active: false, ctx: true,
        prompt: 'Ermittle die geschäftlichen Kontaktdaten der wichtigsten Ansprechpartner: Name, Position, Telefon, E-Mail (nur öffentlich belegbar).',
      },
      social('LinkedIn', 'linkedin.com'),
      social('X', 'x.com OR twitter.com'),
      social('Instagram', 'instagram.com'),
      social('Facebook', 'facebook.com'),
      social('GitHub', 'github.com'),
      {
        label: 'Kaltakquise-Mail', agent: 'partner_mail', active: false, ctx: true,
        prompt: 'Formuliere eine personalisierte Kaltakquise-E-Mail an den Ansprechpartner mit den gefundenen Kontaktdaten und Infos (Betreff, Anrede, 2–3 Absätze, Call-to-Action, Grußformel).',
      },
    ];
  }

  // Fehlende Partner-Agenten anlegen (vorhandene NICHT überschreiben, damit
  // Nutzer-Anpassungen erhalten bleiben). Gibt die Zahl neu angelegter Agenten zurück.
  async function _ensurePartnerAgents() {
    let existing = [];
    try { existing = await (await fetch('/api/agents')).json(); } catch (_) {}
    const have = new Set((existing || []).map(a => a.id));
    let created = 0;
    for (const def of Object.values(PARTNER_AGENTS)) {
      if (have.has(def.id)) continue;
      try {
        const r = await fetch('/api/agents', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(def),
        });
        if (r.ok) created++;
      } catch (_) {}
    }
    await _loadAgents();
    return created;
  }

  // Schlankes Textfenster (z. B. für lange Firmenlisten) – ohne zusätzliches HTML.
  function _showTextModal(title, placeholder, onOk) {
    const ov = document.createElement('div');
    ov.style.cssText = 'position:fixed;inset:0;z-index:3000;background:rgba(0,0,0,.55);display:flex;align-items:center;justify-content:center';
    const box = document.createElement('div');
    box.style.cssText = 'background:var(--bg-input);border:1px solid var(--border);border-radius:10px;padding:16px;width:min(620px,92vw);box-shadow:0 12px 44px rgba(0,0,0,.55)';
    box.innerHTML = `<h3 style="margin:0 0 8px;font-size:15px">${escHtml(title)}</h3>
      <textarea class="_pl_ta" style="width:100%;height:260px;resize:vertical;font-size:13px;box-sizing:border-box" placeholder="${escHtml(placeholder)}"></textarea>
      <div style="display:flex;gap:8px;justify-content:flex-end;margin-top:10px">
        <button class="export-btn _pl_cancel">Abbrechen</button>
        <button class="export-btn _pl_ok">Übernehmen</button></div>`;
    ov.appendChild(box); document.body.appendChild(ov);
    const close = () => ov.remove();
    box.querySelector('._pl_cancel').addEventListener('click', close);
    ov.addEventListener('click', e => { if (e.target === ov) close(); });
    box.querySelector('._pl_ok').addEventListener('click', () => { const v = box.querySelector('._pl_ta').value; close(); onOk(v); });
    box.querySelector('._pl_ta').focus();
  }

  // Firmenliste einlesen: jede nicht-leere Zeile wird zu einer Tabellenzeile (Firma).
  function _importCompanyList() {
    _showTextModal(
      'Firmenliste einlesen — eine Firma pro Zeile (beliebig lang)',
      'Muster GmbH, Musterstraße 1, 12345 Musterstadt\nBeispiel AG\n…',
      (text) => {
        const lines = (text || '').split(/\r?\n/).map(l => l.trim()).filter(Boolean);
        if (!lines.length) { showToast('Keine Firmen erkannt'); return; }
        if (_rows.some(r => r.topic?.trim()) &&
            !confirm(`${lines.length} Firmen einlesen und bestehende Zeilen ersetzen?`)) return;
        _rows = lines.map(l => ({ topic: l }));
        _initCells();
        _saveState(); _render();
        showToast(`✓ ${lines.length} Firmen eingelesen`);
      });
  }

  // Excel-/CSV-Datei einlesen: als Firmenliste (erste Spalte) ODER als vollständige
  // Tabelle (Kopfzeile → Spalten, Zeilen → Zellen). Server: /api/matrix/import-table.
  async function _importExcel(file) {
    if (!file) return;
    const asTable = confirm(
      'Als vollständige Tabelle importieren?\n\n' +
      'OK = ganze Tabelle (Kopfzeile → Spalten, Zeilen → Zellen)\n' +
      'Abbrechen = nur die erste Spalte als Firmen-/Themenliste');
    const fd = new FormData();
    fd.append('file', file);
    fd.append('mode', asTable ? 'table' : 'companies');
    showToast('⏳ Datei wird gelesen…');
    let data;
    try {
      const r = await fetch('/api/matrix/import-table', { method: 'POST', body: fd });
      if (!r.ok) { let m = 'HTTP ' + r.status; try { m = (await r.json()).detail || m; } catch (_) {} throw new Error(m); }
      data = await r.json();
    } catch (e) { showToast('Import fehlgeschlagen: ' + e.message); return; }
    const headers = data.headers || [], rows = data.rows || [];
    if (!rows.length) { showToast('Keine Zeilen gefunden'); return; }
    if (_rows.some(r => r.topic && r.topic.trim()) &&
        !confirm(`${rows.length} Zeilen importieren und bestehende ersetzen?`)) return;
    if (asTable) {
      _cols = headers.slice(1).map(h => ({ prompt: String(h || '').trim(), agent: '', tool: '' }));
      if (!_cols.length) _cols = [{ prompt: '', agent: '', tool: '' }];
      _rows = rows.map(r => ({ topic: String((r || [])[0] == null ? '' : r[0]).trim() }));
      _cells = rows.map(r => _cols.map((_, c) => {
        const v = String((r || [])[c + 1] == null ? '' : r[c + 1]).trim();
        return { status: v ? 'done' : 'empty', text: v };
      }));
      showToast(`✓ Tabelle importiert: ${_rows.length} Zeilen, ${_cols.length} Spalten`);
    } else {
      _rows = rows.map(r => ({ topic: String((r || [])[0] == null ? '' : r[0]).trim() })).filter(r => r.topic);
      _initCells();
      showToast(`✓ ${_rows.length} Firmen eingelesen`);
    }
    _saveState();
    _render();
  }

  // Matrix-Zustand als JSON exportieren / importieren (Spalten, Zeilen, Zellen).
  function _exportJson() {
    const payload = { version: 1, cols: _cols, rows: _rows, cells: _cells, exported_at: new Date().toISOString() };
    const a = document.createElement('a');
    a.href = URL.createObjectURL(new Blob([JSON.stringify(payload, null, 2)], { type: 'application/json' }));
    a.download = 'matrix_recherche.json'; a.click();
    URL.revokeObjectURL(a.href);
  }

  function _importJson(file) {
    const reader = new FileReader();
    reader.onload = e => {
      try {
        const d = JSON.parse(e.target.result);
        if (!Array.isArray(d.cols) || !Array.isArray(d.rows) || !Array.isArray(d.cells))
          throw new Error('Erwarte cols/rows/cells');
        if (_rows.some(r => r.topic && r.topic.trim()) && !confirm('Bestehende Matrix ersetzen?')) return;
        _cols = d.cols; _rows = d.rows; _cells = d.cells;
        _saveState(); _render();
        showToast(`✓ JSON importiert: ${_rows.length} Zeilen, ${_cols.length} Spalten`);
      } catch (err) { showToast('JSON ungültig: ' + err.message); }
    };
    reader.readAsText(file, 'utf-8');
  }

  // Partner-Vorlage einrichten: Agenten sicherstellen + vordefinierte Spalten setzen.
  async function _applyPartnerTemplate() {
    if (!confirm('Partner-Auswertung einrichten?\n\nDie aktuellen Spalten werden durch die Partner-Stufen ersetzt '
      + '(Firmen-Zeilen bleiben erhalten). Fehlende Agenten werden angelegt; vorhandene Anpassungen bleiben erhalten.')) return;
    showToast('⏳ Partner-Agenten werden vorbereitet…');
    const created = await _ensurePartnerAgents();
    _cols = _PARTNER_COLUMNS();
    _initCells();
    _partnerMode = true;
    _saveState(); _render();
    showToast(`✓ Partner-Auswertung eingerichtet${created ? ` · ${created} Agent(en) angelegt` : ''}. `
      + 'Firmenliste einlesen, dann Spalte für Spalte ausführen (▶ Spalte).');
  }

  /* ════════════════════════════════════════════════════════════════════
     Wissensgraph: Zeilen = Knoten, Verknüpfungen per KI-Vorschlag + manuell.
     Cytoscape.js rendert in #matrix-graph; Kanten/Positionen liegen im State.
     ════════════════════════════════════════════════════════════════════ */

  // Stabile ID je Zeile (überlebt Umsortieren/Löschen). Wird in _rows persistiert;
  // Kanten referenzieren diese gid, nicht den Zeilenindex.
  function _rowGid(row) {
    if (!row._gid) row._gid = 'n' + Math.random().toString(36).slice(2, 9);
    return row._gid;
  }

  // Alle Zelltexte einer Zeile zu einem Knoten-Begleittext zusammenfassen (für die KI).
  function _rowText(r) {
    const parts = [];
    for (let c = 0; c < _cols.length; c++) {
      const txt = _cells[r]?.[c]?.text?.trim();
      if (!txt) continue;
      const lbl = _cols[c].label || _cols[c].prompt || `Spalte ${c + 1}`;
      parts.push(`${lbl}: ${txt}`);
    }
    return parts.join(' · ');
  }

  // Farbe je Merkmal-Kategorie (deterministisch: erst nach Position in _attrCats,
  // sonst per Hash – so bleibt die Farbe stabil, auch bei freien Kategorien).
  function _catColor(cat) {
    const i = _attrCats.findIndex(c => c.toLowerCase() === (cat || '').toLowerCase());
    if (i >= 0) return _CAT_COLORS[i % _CAT_COLORS.length];
    let h = 0; for (const ch of (cat || '')) h = (h * 31 + ch.charCodeAt(0)) >>> 0;
    return _CAT_COLORS[h % _CAT_COLORS.length];
  }

  const _hubId = (cat, val) => 'attr::' + (cat || '').toLowerCase() + '::' + (val || '').trim().toLowerCase();

  // Cytoscape-Elemente aus dem aktuellen Tabellenzustand: Zeilen-Knoten (mit Thema)
  // + Merkmal-Knoten (Hubs) aus _rowAttrs. Zeilen, die einen Merkmalswert teilen,
  // hängen am selben Hub und sind dadurch verbunden.
  function _graphElements() {
    const nodes = [], valid = new Set();
    const rowGids = [];
    _rows.forEach(row => {
      const topic = (row.topic || '').trim();
      if (!topic) return;
      const id = _rowGid(row);
      valid.add(id); rowGids.push(id);
      const data = { id, kind: 'row', label: topic.length > 42 ? topic.slice(0, 40) + '…' : topic };
      const node = { data };
      if (_graphPos[id]) node.position = { x: _graphPos[id].x, y: _graphPos[id].y };
      nodes.push(node);
    });

    // Merkmal-Hubs einsammeln: hubId -> {label, category, rows:Set}
    const hubs = new Map();
    if (_showHubs) {
      rowGids.forEach(id => {
        (_rowAttrs[id] || []).forEach(a => {
          const cat = (a.category || '').trim(), val = (a.value || '').trim();
          if (!cat || !val) return;
          const hid = _hubId(cat, val);
          if (!hubs.has(hid)) hubs.set(hid, { label: val, category: cat, rows: new Set() });
          hubs.get(hid).rows.add(id);
        });
      });
    }

    const hubEdges = [];
    hubs.forEach((meta, hid) => {
      if (_sharedOnly && meta.rows.size < 2) return;   // nur geteilte Merkmale zeigen
      valid.add(hid);
      const data = { id: hid, kind: 'hub', label: meta.label, category: meta.category, catColor: _catColor(meta.category) };
      const node = { data };
      if (_graphPos[hid]) node.position = { x: _graphPos[hid].x, y: _graphPos[hid].y };
      nodes.push(node);
      meta.rows.forEach(rid => hubEdges.push({ source: rid, target: hid, catColor: _catColor(meta.category) }));
    });

    // dangling manuelle Kanten (auf gelöschte Zeilen) automatisch verwerfen
    _graphEdges = _graphEdges.filter(e => valid.has(e.source) && valid.has(e.target));
    const edges = _graphEdges.map(e => ({
      data: { id: `${e.source}__${e.target}`, source: e.source, target: e.target, label: e.label || '', kind: 'manual' },
    }));
    hubEdges.forEach(he => edges.push({
      data: { id: `h_${he.source}__${he.target}`, source: he.source, target: he.target, kind: 'hub', catColor: he.catColor },
    }));
    return { nodes, edges, allHavePos: nodes.every(n => n.position) };
  }

  function _graphStyle() {
    const css = getComputedStyle(document.documentElement);
    const accent = (css.getPropertyValue('--accent') || '#4f8cff').trim();
    const text   = (css.getPropertyValue('--text') || '#e8e8e8').trim();
    const border = (css.getPropertyValue('--border') || '#3a3a3a').trim();
    const bg     = (css.getPropertyValue('--bg-hover') || '#2a2a2a').trim();
    return [
      { selector: 'node', style: {
        'background-color': bg, 'border-color': accent, 'border-width': 2,
        'label': 'data(label)', 'color': text, 'font-size': 11,
        'text-wrap': 'wrap', 'text-max-width': 120, 'text-valign': 'center',
        'text-halign': 'center', 'width': 'label', 'height': 'label',
        'padding': 8, 'shape': 'round-rectangle', 'text-outline-width': 0,
      }},
      // Merkmal-Knoten (Hub): farbig je Kategorie, ovale Form, dunkle Schrift
      { selector: 'node[kind="hub"]', style: {
        'background-color': 'data(catColor)', 'border-color': 'data(catColor)',
        'border-width': 0, 'shape': 'round-tag', 'color': '#0b0b0b',
        'font-size': 10, 'font-weight': 'bold', 'padding': 6,
        'text-max-width': 110, 'text-outline-width': 1.5, 'text-outline-color': '#ffffff',
      }},
      { selector: 'node.sel', style: { 'border-color': '#22c55e', 'border-width': 4 }},
      { selector: 'edge', style: {
        'width': 2, 'line-color': border, 'target-arrow-color': border,
        'target-arrow-shape': 'triangle', 'curve-style': 'bezier',
        'label': 'data(label)', 'font-size': 9, 'color': text,
        'text-background-color': bg, 'text-background-opacity': 0.85,
        'text-background-padding': 2,
      }},
      // Hub-Kanten (Zeile → Merkmal): dünn, farbig, ohne Pfeil/Label
      { selector: 'edge[kind="hub"]', style: {
        'width': 1.5, 'line-color': 'data(catColor)', 'line-opacity': 0.6,
        'target-arrow-shape': 'none', 'curve-style': 'haystack', 'label': '',
      }},
      { selector: 'edge:selected', style: { 'line-color': accent, 'target-arrow-color': accent, 'width': 3 }},
    ];
  }

  function _rememberPositions() {
    if (!_cy) return;
    _cy.nodes().forEach(n => { const p = n.position(); _graphPos[n.id()] = { x: p.x, y: p.y }; });
  }

  function _runLayout(force) {
    if (!_cy) return;
    const els = _cy.nodes();
    if (!els.length) return;
    _cy.layout({ name: 'cose', animate: false, padding: 30, nodeRepulsion: 8000, idealEdgeLength: 120 }).run();
    _rememberPositions();
    _saveState();
  }

  // Graph (neu) aufbauen bzw. mit dem aktuellen Tabellenzustand abgleichen.
  function _syncGraph() {
    const host = document.getElementById('matrix-graph');
    if (!host || typeof cytoscape === 'undefined') return;
    if (_cy) _rememberPositions();
    const { nodes, edges, allHavePos } = _graphElements();

    if (!_cy) {
      _cy = cytoscape({
        container: host, elements: [...nodes, ...edges], style: _graphStyle(),
        wheelSensitivity: 0.2, minZoom: 0.2, maxZoom: 3,
        layout: allHavePos ? { name: 'preset' } : { name: 'cose', animate: false, padding: 30 },
      });
      _cy.on('dragfree', 'node', () => { _rememberPositions(); _saveState(); });
      _cy.on('tap', 'node', evt => _onNodeTap(evt.target));
      _cy.on('tap', 'edge', evt => _onEdgeTap(evt.target));
      if (!allHavePos) { _rememberPositions(); _saveState(); }
    } else {
      _cy.json({ elements: [...nodes, ...edges] });
      if (!allHavePos) _runLayout();
    }
    _updateGraphHint();
  }

  function _updateGraphHint() {
    const el = document.getElementById('matrix-graph-hint');
    if (!el) return;
    if (_connectMode) {
      el.textContent = _connectFrom ? 'Zielknoten anklicken…' : 'Startknoten anklicken…';
      return;
    }
    const rows = _cy ? _cy.nodes('[kind="row"]').length : 0;
    const hubs = _cy ? _cy.nodes('[kind="hub"]').length : 0;
    el.textContent = `${rows} Zeilen · ${hubs} Merkmale · ${_graphEdges.length} man. Kanten`;
    // Farb-Legende der Kategorien aktualisieren
    const leg = document.getElementById('matrix-graph-legend');
    if (leg) {
      leg.innerHTML = _attrCats.map(c =>
        `<span style="display:inline-flex;align-items:center;gap:3px;margin-right:8px">`
        + `<span style="width:10px;height:10px;border-radius:50%;background:${_catColor(c)};display:inline-block"></span>`
        + `${escHtml(c)}</span>`
      ).join('');
    }
  }

  // Knoten angeklickt: im Verbindungsmodus Kante ziehen, sonst Zelltext anzeigen.
  function _onNodeTap(node) {
    if (node.data('kind') === 'hub') {
      // Merkmal-Knoten: zeigt, welche Zeilen ihn teilen (kein Verbindungsmodus).
      if (_cy) {
        const conn = _cy.getElementById(node.id()).connectedNodes('[kind="row"]').map(n => n.data('label'));
        showToast(`${node.data('category')}: ${node.data('label')} — ${conn.length} Zeile(n): ${conn.join(', ')}`);
      }
      return;
    }
    if (_connectMode) {
      if (!_connectFrom) {
        _connectFrom = node.id();
        node.addClass('sel');
      } else if (_connectFrom === node.id()) {
        _cy.getElementById(_connectFrom).removeClass('sel');
        _connectFrom = null;
      } else {
        _addEdge(_connectFrom, node.id());
        _cy.getElementById(_connectFrom).removeClass('sel');
        _connectFrom = null;
      }
      _updateGraphHint();
    }
  }

  // Kante angeklickt: Beschriftung ändern (leer = Kante löschen).
  function _onEdgeTap(edge) {
    if (_connectMode) return;
    const s = edge.data('source'), t = edge.data('target');
    const cur = edge.data('label') || '';
    const next = prompt('Beziehung bearbeiten (leeres Feld = Verknüpfung löschen):', cur);
    if (next === null) return;
    const lbl = next.trim();
    if (!lbl) {
      _graphEdges = _graphEdges.filter(e => !(e.source === s && e.target === t));
    } else {
      const e = _graphEdges.find(e => e.source === s && e.target === t);
      if (e) e.label = lbl;
    }
    _saveState();
    _syncGraph();
  }

  function _addEdge(source, target, label) {
    if (_graphEdges.some(e => e.source === source && e.target === target)) {
      showToast('Verknüpfung besteht bereits');
      return;
    }
    _graphEdges.push({ source, target, label: label || '' });
    _saveState();
    _syncGraph();
  }

  function _toggleConnect() {
    _connectMode = !_connectMode;
    if (!_connectMode && _connectFrom && _cy) {
      _cy.getElementById(_connectFrom).removeClass('sel');
      _connectFrom = null;
    }
    document.getElementById('btn-graph-connect')?.classList.toggle('active', _connectMode);
    _updateGraphHint();
  }

  function _toggleGraph(force) {
    _graphOpen = (force === undefined) ? !_graphOpen : !!force;
    const pane     = document.getElementById('matrix-graph-pane');
    const splitter = document.getElementById('matrix-splitter');
    if (!pane || !splitter) return;
    pane.style.display     = _graphOpen ? 'flex' : 'none';
    splitter.style.display = _graphOpen ? 'block' : 'none';
    document.getElementById('btn-matrix-graph')?.classList.toggle('active', _graphOpen);
    _saveState();
    if (_graphOpen) {
      _syncGraph();
      // Layout-Maße greifen erst, wenn der Container sichtbar ist.
      setTimeout(() => { if (_cy) { _cy.resize(); _cy.fit(undefined, 30); } }, 60);
    }
  }

  // KI-Vorschlag für die Verknüpfungen einholen (Hybrid: Vorschlag, dann manuell korrigieren).
  async function _suggestEdges() {
    const aiNodes = [];
    _rows.forEach((row, r) => {
      const topic = (row.topic || '').trim();
      if (!topic) return;
      aiNodes.push({ id: _rowGid(row), label: topic, text: _rowText(r) });
    });
    if (aiNodes.length < 2) { showToast('Mindestens zwei Zeilen mit Thema nötig'); return; }
    const btn = document.getElementById('btn-graph-suggest');
    if (btn) btn.disabled = true;
    showToast('⏳ KI sucht Verknüpfungen…');
    try {
      const resp = await fetch('/api/matrix/graph', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ model: _getModel(), nodes: aiNodes }),
      });
      if (!resp.ok) throw new Error((await resp.json().catch(() => ({}))).detail || resp.status);
      const data = await resp.json();
      if (data.tokens && typeof TokenMeter !== 'undefined') TokenMeter.add(data.tokens, 'Wissensgraph');
      let added = 0;
      for (const e of (data.edges || [])) {
        if (e.source === e.target) continue;
        if (_graphEdges.some(x => x.source === e.source && x.target === e.target)) continue;
        _graphEdges.push({ source: e.source, target: e.target, label: (e.label || '').trim() });
        added++;
      }
      _saveState();
      _syncGraph();
      showToast(added ? `✓ ${added} Verknüpfung(en) vorgeschlagen — per Klick auf eine Kante anpassen/löschen` : 'Keine belegbaren Verknüpfungen gefunden');
    } catch (e) {
      showToast('Graph-Analyse fehlgeschlagen: ' + e.message);
    } finally {
      if (btn) btn.disabled = false;
    }
  }

  function _clearEdges() {
    if (!_graphEdges.length) return;
    if (!confirm('Alle manuellen Verknüpfungen löschen? (Knoten/Merkmale bleiben erhalten)')) return;
    _graphEdges = [];
    _saveState();
    _syncGraph();
  }

  // Merkmal-Knoten (Hubs): je Zeile die typisierten Merkmale per KI extrahieren.
  // Zeilen, die einen Wert teilen, hängen anschließend am selben Hub-Knoten und
  // sind dadurch verbunden — statt die Zeilen direkt „alle mit allen" zu verdrahten.
  async function _extractAttributes() {
    const rows = _rows.filter(r => (r.topic || '').trim());
    if (rows.length < 1) { showToast('Keine Zeilen mit Thema'); return; }
    const btn = document.getElementById('btn-graph-extract');
    if (btn) btn.disabled = true;
    let done = 0;
    try {
      for (const row of rows) {
        const id = _rowGid(row);
        const r = _rows.indexOf(row);
        showToast(`⏳ Merkmale erkennen ${done + 1}/${rows.length}…`);
        try {
          const resp = await fetch('/api/matrix/extract', {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ model: _getModel(), label: row.topic, text: _rowText(r), categories: _attrCats }),
          });
          if (resp.ok) {
            const data = await resp.json();
            if (data.tokens && typeof TokenMeter !== 'undefined') TokenMeter.add(data.tokens, 'Wissensgraph');
            _rowAttrs[id] = (data.attributes || []).filter(a => a && a.category && a.value);
          }
        } catch (_) { /* einzelne Zeile darf scheitern */ }
        done++;
        _saveState();
      }
      _syncGraph();
      if (_cy) { _runLayout(true); setTimeout(() => { _cy.resize(); _cy.fit(undefined, 30); }, 80); }
      showToast(`✓ Merkmale erkannt (${done} Zeilen) — geteilte Merkmale verbinden die Knoten`);
    } finally {
      if (btn) btn.disabled = false;
    }
  }

  // Merkmals-Kategorien bearbeiten (Standard: Tätigkeit, Ort, Tool, Aufgabenbereich, Name).
  function _editCategories() {
    const next = prompt('Merkmals-Kategorien (mit Komma getrennt) — je Kategorie eine eigene Farbe:', _attrCats.join(', '));
    if (next === null) return;
    const list = [...new Set(next.split(',').map(s => s.trim()).filter(Boolean))];
    if (!list.length) { showToast('Mindestens eine Kategorie nötig'); return; }
    _attrCats = list;
    _saveState();
    _updateGraphHint();
    showToast('✓ Kategorien aktualisiert — „🏷 Merkmale" neu ausführen, um sie anzuwenden');
  }

  // Merkmal-Knoten ein-/ausblenden bzw. nur geteilte zeigen.
  function _toggleHubs(on) {
    _showHubs = on;
    _saveState();
    _syncGraph();
  }
  function _toggleSharedOnly(on) {
    _sharedOnly = on;
    _saveState();
    _syncGraph();
  }

  // „Zweite Stufe": eine komplette (bereits gefüllte) Tabelle in einen Wissensgraphen
  // überführen — Graph-Pane öffnen und die typisierten Merkmale je Zeile extrahieren.
  // Zeilen, die ein Merkmal teilen (Ort, Tool, …), verbinden sich über den Hub-Knoten.
  // Gleiche Routine, die nach „▶ Alle ausführen" als Anschluss-Stufe läuft.
  async function _tableToGraph() {
    if (_rows.filter(r => r.topic?.trim()).length < 2) {
      showToast('Mindestens zwei Zeilen mit Thema nötig');
      return;
    }
    if (!_graphOpen) _toggleGraph(true);   // Pane öffnen + Knoten aufbauen
    else _syncGraph();
    await _extractAttributes();            // Merkmale je Zeile → Hub-Knoten verbinden
  }

  // Splitter: Breite des Graph-Panes per Maus ziehen.
  function _initSplitter() {
    const splitter = document.getElementById('matrix-splitter');
    const split    = document.getElementById('matrix-split');
    const pane     = document.getElementById('matrix-graph-pane');
    if (!splitter || !split || !pane) return;
    let dragging = false;
    const onMove = e => {
      if (!dragging) return;
      const rect = split.getBoundingClientRect();
      let w = rect.right - e.clientX;
      w = Math.max(240, Math.min(rect.width - 200, w));
      pane.style.flexBasis = w + 'px';
      if (_cy) _cy.resize();
    };
    splitter.addEventListener('mousedown', e => {
      dragging = true; e.preventDefault();
      document.body.style.userSelect = 'none';
    });
    window.addEventListener('mousemove', onMove);
    window.addEventListener('mouseup', () => {
      if (!dragging) return;
      dragging = false; document.body.style.userSelect = '';
      if (_cy) _cy.fit(undefined, 30);
    });
  }

  // Button-Leiste ein-/ausblenden, um mehr Fläche für Tabelle/Graph zu gewinnen.
  const TOOLBAR_KEY = 'ai_framework_thomas_matrix_toolbar';
  let _toolbarCollapsed = false;
  function _toggleToolbar(force) {
    _toolbarCollapsed = (force === undefined) ? !_toolbarCollapsed : !!force;
    const tb  = document.getElementById('matrix-toolbar');
    const btn = document.getElementById('btn-matrix-toolbar-toggle');
    if (tb)  tb.style.display = _toolbarCollapsed ? 'none' : '';
    if (btn) { btn.textContent = _toolbarCollapsed ? '▾ Leiste' : '▴ Leiste'; btn.classList.toggle('active', _toolbarCollapsed); }
    try { localStorage.setItem(TOOLBAR_KEY, _toolbarCollapsed ? '1' : '0'); } catch (_) {}
    // Graph nach Größenänderung neu vermessen.
    if (_graphOpen && _cy) setTimeout(() => { _cy.resize(); _cy.fit(undefined, 30); }, 60);
  }

  /* ── Reset ───────────────────────────────────────────────────────── */
  function _clear() {
    if (!confirm('Tabelle leeren?')) return;
    _rows  = [{ topic: '' }, { topic: '' }, { topic: '' }];
    _cols  = [{ prompt: '', agent: '' }, { prompt: '', agent: '' }];
    _partnerMode = false;
    _graphEdges = [];
    _graphPos = {};
    _rowAttrs = {};
    _initCells();
    _saveState();
    _render();
    if (_graphOpen) _syncGraph();
  }

  /* ── init ────────────────────────────────────────────────────────── */
  function init() {
    if (!_loadState()) _initCells();

    _loadAgents();

    document.getElementById('btn-matrix-add-row')?.addEventListener('click', () => {
      _rows.push({ topic: '' });
      _cells.push(_cols.map(() => ({ status: 'empty', text: '' })));
      _saveState();
      _render();
    });

    document.getElementById('btn-matrix-add-col')?.addEventListener('click', () => {
      _cols.push({ prompt: '', agent: '' });
      _cells.forEach(row => row.push({ status: 'empty', text: '' }));
      _saveState();
      _render();
    });

    document.getElementById('btn-matrix-run-all')?.addEventListener('click', _runAll);
    document.getElementById('btn-matrix-partner')?.addEventListener('click', _applyPartnerTemplate);
    document.getElementById('btn-matrix-companies')?.addEventListener('click', _importCompanyList);
    document.getElementById('btn-matrix-export')?.addEventListener('click', _exportXlsx);
    document.getElementById('btn-matrix-export-csv')?.addEventListener('click', _exportCsv);
    document.getElementById('btn-matrix-rag')?.addEventListener('click', _exportRag);
    document.getElementById('btn-matrix-rag-cells')?.addEventListener('click', _ingestPerCell);
    document.getElementById('btn-matrix-md-zip')?.addEventListener('click', _exportMdZip);
    document.getElementById('btn-matrix-clear')?.addEventListener('click', _clear);

    // Wissensgraph
    document.getElementById('btn-matrix-graph')?.addEventListener('click', () => _toggleGraph());
    document.getElementById('btn-graph-extract')?.addEventListener('click', _extractAttributes);
    document.getElementById('btn-graph-cats')?.addEventListener('click', _editCategories);
    document.getElementById('btn-graph-suggest')?.addEventListener('click', _suggestEdges);
    document.getElementById('btn-graph-connect')?.addEventListener('click', _toggleConnect);
    const hubsCb = document.getElementById('matrix-show-hubs');
    if (hubsCb) { hubsCb.checked = _showHubs; hubsCb.addEventListener('change', () => _toggleHubs(hubsCb.checked)); }
    const sharedCb = document.getElementById('matrix-shared-only');
    if (sharedCb) { sharedCb.checked = _sharedOnly; sharedCb.addEventListener('change', () => _toggleSharedOnly(sharedCb.checked)); }
    document.getElementById('btn-graph-relayout')?.addEventListener('click', () => { _runLayout(true); if (_cy) _cy.fit(undefined, 30); });
    document.getElementById('btn-graph-fit')?.addEventListener('click', () => { if (_cy) _cy.fit(undefined, 30); });
    document.getElementById('btn-graph-clear-edges')?.addEventListener('click', _clearEdges);
    document.getElementById('btn-matrix-table-graph')?.addEventListener('click', _tableToGraph);
    document.getElementById('btn-matrix-toolbar-toggle')?.addEventListener('click', () => _toggleToolbar());
    _initSplitter();
    try { if (localStorage.getItem(TOOLBAR_KEY) === '1') _toggleToolbar(true); } catch (_) {}

    // Auto-Graph-Schalter (nach „Alle ausführen") – Zustand merken.
    const autoCb = document.getElementById('matrix-auto-graph');
    if (autoCb) {
      try { _autoGraph = localStorage.getItem('ai_framework_thomas_matrix_autograph') !== '0'; } catch (_) {}
      autoCb.checked = _autoGraph;
      autoCb.addEventListener('change', () => {
        _autoGraph = autoCb.checked;
        try { localStorage.setItem('ai_framework_thomas_matrix_autograph', _autoGraph ? '1' : '0'); } catch (_) {}
      });
    }

    const csvInput = document.getElementById('matrix-csv-input');
    document.getElementById('btn-matrix-import-csv')?.addEventListener('click', () => csvInput?.click());
    csvInput?.addEventListener('change', e => {
      if (e.target.files[0]) _importCsv(e.target.files[0]);
      e.target.value = '';
    });

    const excelInput = document.getElementById('matrix-excel-input');
    document.getElementById('btn-matrix-import-excel')?.addEventListener('click', () => excelInput?.click());
    excelInput?.addEventListener('change', e => { if (e.target.files[0]) _importExcel(e.target.files[0]); e.target.value = ''; });
    const jsonInput = document.getElementById('matrix-json-input');
    document.getElementById('btn-matrix-export-json')?.addEventListener('click', _exportJson);
    document.getElementById('btn-matrix-import-json')?.addEventListener('click', () => jsonInput?.click());
    jsonInput?.addEventListener('change', e => { if (e.target.files[0]) _importJson(e.target.files[0]); e.target.value = ''; });

    document.querySelector('[data-tab="matrix"]')?.addEventListener('click', () => {
      _loadAgents();
      _render();
      // Cytoscape muss nach dem Sichtbarwerden neu vermessen werden.
      if (_graphOpen) setTimeout(() => { if (_cy) { _cy.resize(); _cy.fit(undefined, 30); } }, 60);
    });

    _render();
    // Gespeicherten Graph-Zustand wiederherstellen (Pane + Cytoscape aufbauen).
    if (_graphOpen) { _graphOpen = false; _toggleGraph(true); }
  }

  return { init };

})();
