/* AI_Framework_Thomas — Anfrage-Auswertung (RFQ)
   Lädt eine große XLS-/CSV-Anfrage, ordnet die Aufgaben-Spalte zu und wertet jedes
   Arbeitspaket per LLM aus (zuständige Fachrolle, interessant?, Partner?, Best-Cost-
   Country). Robust mit Abbruch, Zwischenspeicherung (Server) und Fortsetzen (Resume).
   Ergebnis als Raster + XLSX-Export. Enthält den Editor der globalen Kapazitätsliste. */

const RFQ = (() => {
  let _file = null;          // gewählte Datei (für Reload mit anderem Blatt/Kopfzeile)
  let _fileId = '';          // serverseitige ID nach Upload
  let _headers = [];         // Spaltenüberschriften der Quelle
  let _results = [];         // [{index, id, title, task, cells, result}]
  let _rowEls = {};          // index → <tr> im Ergebnisraster
  let _jobId = '';           // aktueller/letzter Job (für Resume)
  let _abort = null;         // AbortController des laufenden Laufs
  let _running = false;
  let _cap = [];             // Kapazitätsliste (Items)
  let _customCols = [];      // eigene Bewertungsspalten [{key,name,prompt,agent_id}]
  let _agentsCache = null;   // Agentenliste für die Spalten-Auswahl

  const EVAL_HEADERS = ['Zuständig', 'Interessant', 'Begründung', 'Partner nötig',
    'Partnerart', 'Best-Cost-Country', 'BCC-Region', 'BCC-Begründung'];

  // ── Eigene Bewertungsspalten ────────────────────────────────────────────────
  const COLS_KEY = 'rfq_custom_columns';
  function _loadCustomCols() {
    try { _customCols = JSON.parse(localStorage.getItem(COLS_KEY) || '[]') || []; }
    catch (_) { _customCols = []; }
    if (!Array.isArray(_customCols)) _customCols = [];
  }
  function _persistCustomCols() {
    try { localStorage.setItem(COLS_KEY, JSON.stringify(_customCols)); } catch (_) {}
  }
  function _slugKey(s) {
    return (s || '').toLowerCase().replace(/[^a-z0-9_]/g, '').slice(0, 24) || ('c' + Math.random().toString(36).slice(2, 6));
  }

  async function _loadAgentsForCols() {
    if (_agentsCache) return _agentsCache;
    try { _agentsCache = await (await fetch('/api/agents')).json(); }
    catch (_) { _agentsCache = []; }
    if (!Array.isArray(_agentsCache)) _agentsCache = (_agentsCache && _agentsCache.agents) || [];
    return _agentsCache;
  }

  async function _openCols() {
    await _loadAgentsForCols();
    _renderColsList();
    document.getElementById('rfq-cols-overlay').classList.add('active');
  }

  function _renderColsList() {
    const box = document.getElementById('rfq-cols-list');
    if (!box) return;
    const agentOpts = (sel) => '<option value="">— Agent wählen —</option>' +
      (_agentsCache || []).map(a => `<option value="${escHtml(a.id)}"${a.id === sel ? ' selected' : ''}>${escHtml(a.name || a.id)}</option>`).join('');
    box.innerHTML = _customCols.map((c, i) => `
      <div class="rfq-col-row" data-i="${i}" style="border:1px solid var(--border);border-radius:6px;padding:8px;display:flex;flex-direction:column;gap:6px">
        <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap">
          <input class="sidebar-select col-name" placeholder="Spaltenname (z.B. Risiko)" value="${escHtml(c.name || '')}" style="font-size:12px;flex:1;min-width:140px" />
          <label style="font-size:12px;display:flex;gap:4px;align-items:center"><input type="radio" name="colmode${i}" class="col-mode" value="agent"${c.agent_id ? ' checked' : ''}/> Agent</label>
          <label style="font-size:12px;display:flex;gap:4px;align-items:center"><input type="radio" name="colmode${i}" class="col-mode" value="prompt"${!c.agent_id ? ' checked' : ''}/> Prompt</label>
          <button class="export-btn col-del" data-i="${i}" style="font-size:11px">🗑</button>
        </div>
        <select class="sidebar-select col-agent" style="font-size:12px;${c.agent_id ? '' : 'display:none'}">${agentOpts(c.agent_id || '')}</select>
        <textarea class="col-prompt" placeholder="Frage/Vorgabe für diese Spalte, z.B. 'Wie hoch ist das technische Risiko (niedrig/mittel/hoch)?'" style="font-size:12px;min-height:46px;resize:vertical;background:var(--bg-input);color:var(--text);border:1px solid var(--border);border-radius:4px;padding:5px;${c.agent_id ? 'display:none' : ''}">${escHtml(c.prompt || '')}</textarea>
      </div>`).join('') || '<span class="planner-muted" style="font-size:12px">Noch keine eigene Spalte. „＋ Spalte" hinzufügen.</span>';
    // Mode-Umschaltung (Agent ⇄ Prompt)
    box.querySelectorAll('.rfq-col-row').forEach(row => {
      row.querySelectorAll('.col-mode').forEach(r => r.addEventListener('change', () => {
        const isAgent = row.querySelector('.col-mode[value="agent"]').checked;
        row.querySelector('.col-agent').style.display = isAgent ? '' : 'none';
        row.querySelector('.col-prompt').style.display = isAgent ? 'none' : '';
      }));
      row.querySelector('.col-del').addEventListener('click', () => {
        _readColsList(); _customCols.splice(parseInt(row.dataset.i, 10), 1); _renderColsList();
      });
    });
  }

  function _readColsList() {
    const rows = document.querySelectorAll('#rfq-cols-list .rfq-col-row');
    _customCols = Array.from(rows).map(row => {
      const name = row.querySelector('.col-name').value.trim();
      const isAgent = row.querySelector('.col-mode[value="agent"]').checked;
      return {
        key: _slugKey(name),
        name,
        agent_id: isAgent ? (row.querySelector('.col-agent').value || '') : '',
        prompt: isAgent ? '' : (row.querySelector('.col-prompt').value.trim()),
      };
    }).filter(c => c.name && (c.agent_id || c.prompt));
  }

  function _colsAdd() {
    _readColsList();
    if (_customCols.length >= 6) { showToast('Maximal 6 eigene Spalten'); return; }
    _customCols.push({ key: '', name: '', prompt: '', agent_id: '' });
    _renderColsList();
  }

  function _colsSave() {
    _readColsList();
    _persistCustomCols();
    showToast(`✓ ${_customCols.length} eigene Spalte(n) übernommen`);
    document.getElementById('rfq-cols-overlay').classList.remove('active');
    // Falls bereits Ergebnisse vorhanden sind: Kopf neu zeichnen (Spalten erst beim nächsten Lauf gefüllt)
    if (_results.filter(Boolean).length) { _resetResultsTable(); for (const it of _results) if (it) _renderResultRow(it); }
  }

  // ── Schritt 1: Vorschau / Spaltenzuordnung ──────────────────────────────────
  async function _preview(useExisting) {
    const inp = document.getElementById('rfq-file');
    if (!useExisting) _file = (inp.files && inp.files[0]) || _file;
    if (!_file) { showToast('Bitte zuerst eine Datei wählen'); return; }
    const fd = new FormData();
    fd.append('file', _file);
    fd.append('sheet', document.getElementById('rfq-sheet').value || '');
    fd.append('header_row', document.getElementById('rfq-header-row').value || '0');
    document.getElementById('rfq-file-info').textContent = '⏳ wird eingelesen…';
    try {
      const r = await fetch('/api/rfq/preview', { method: 'POST', body: fd });
      const data = await r.json();
      if (!r.ok) throw new Error(data.detail || r.status);
      _fileId = data.file_id;
      _headers = data.headers || [];
      _fillSheetSelect(data.sheets, data.sheet);
      _fillColSelects(_headers);
      _renderSample(_headers, data.sample_rows || []);
      document.getElementById('rfq-file-info').textContent =
        `✓ ${_file.name} · ${data.n_rows} Zeilen · ${_headers.length} Spalten`;
      document.getElementById('rfq-mapping').style.display = 'flex';
      document.getElementById('rfq-sample-wrap').style.display = 'block';
      document.getElementById('rfq-run-options').style.display = 'flex';
    } catch (e) {
      document.getElementById('rfq-file-info').textContent = '';
      showToast('Fehler: ' + e.message);
    }
  }

  function _fillSheetSelect(sheets, current) {
    const sel = document.getElementById('rfq-sheet');
    sel.innerHTML = '';
    for (const s of (sheets || [])) {
      const o = document.createElement('option');
      o.value = s; o.textContent = s;
      if (s === current) o.selected = true;
      sel.appendChild(o);
    }
  }

  function _fillColSelects(headers) {
    const optsRequired = headers.map((h, i) => `<option value="${i}">${escHtml(h || ('Spalte ' + (i + 1)))}</option>`).join('');
    const optsOptional = '<option value="-1">— keine —</option>' + optsRequired;
    document.getElementById('rfq-task-col').innerHTML = optsRequired;
    document.getElementById('rfq-id-col').innerHTML = optsOptional;
    document.getElementById('rfq-title-col').innerHTML = optsOptional;
    // Heuristik: längste/sprechende Spalte als Aufgabe vorwählen
    const guess = headers.findIndex(h => /aufgab|beschreib|task|leistung|paket|text|titel/i.test(h || ''));
    if (guess >= 0) document.getElementById('rfq-task-col').value = String(guess);
    document.getElementById('rfq-id-col').value = '-1';
    document.getElementById('rfq-title-col').value = '-1';
  }

  function _renderSample(headers, rows) {
    const tbl = document.getElementById('rfq-sample');
    const th = '<thead><tr>' + headers.map(h => `<th style="text-align:left;padding:3px 6px;border-bottom:1px solid var(--border);position:sticky;top:0;background:var(--bg-input)">${escHtml(h)}</th>`).join('') + '</tr></thead>';
    const tb = '<tbody>' + rows.map(r => '<tr>' + headers.map((_, c) =>
      `<td style="padding:3px 6px;border-bottom:1px solid var(--border);max-width:280px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${escHtml(String(r[c] ?? ''))}</td>`).join('') + '</tr>').join('') + '</tbody>';
    tbl.innerHTML = th + tb;
  }

  async function _loadModels() {
    const sel = document.getElementById('rfq-model');
    let def = '';
    try { def = (typeof Profile !== 'undefined' ? Profile.modelFor('general') : '') || ''; } catch (_) {}
    try {
      const data = await (await fetch('/api/models')).json();
      sel.innerHTML = '';
      for (const m of (data.models || [])) {
        const o = document.createElement('option');
        o.value = m.name; o.textContent = m.name;
        if (m.name === def) o.selected = true;
        sel.appendChild(o);
      }
      if (!sel.options.length && def) sel.innerHTML = `<option value="${escHtml(def)}" selected>${escHtml(def)}</option>`;
    } catch (_) {
      if (def) sel.innerHTML = `<option value="${escHtml(def)}" selected>${escHtml(def)}</option>`;
    }
  }

  async function _loadRag() {
    const box = document.getElementById('rfq-rag');
    if (!box) return;
    const prev = new Set(_selectedRag());
    try {
      const colls = await (await fetch('/api/rag/collections')).json();
      if (!colls.length) {
        box.innerHTML = '<span class="planner-muted" style="font-size:11px">— keine vorhanden —</span>';
        return;
      }
      box.innerHTML = colls.map(c => {
        const chk = prev.has(c.id) ? ' checked' : '';
        return `<label style="display:flex;align-items:center;gap:6px;padding:1px 0;cursor:pointer">
          <input type="checkbox" class="rfq-rag-chk" value="${escHtml(c.id)}"${chk} />
          <span>${escHtml(c.name)} <span class="planner-muted">(${c.n_chunks})</span></span></label>`;
      }).join('');
    } catch (_) {}
  }

  // Aktive Ressourcenlisten direkt im Anfrage-Tab per Häkchen wählen.
  async function _loadCapListsForRun() {
    const box = document.getElementById('rfq-caplists');
    if (!box) return;
    try {
      const d = await (await fetch('/api/capacity/lists')).json();
      const lists = d.lists || [];
      const sel = new Set(d.selected || []);
      if (!lists.length) {
        box.innerHTML = '<span class="planner-muted" style="font-size:11px">— keine vorhanden —</span>';
        return;
      }
      box.innerHTML = lists.map(l => `<label style="display:flex;align-items:center;gap:6px;padding:1px 0;cursor:pointer">
        <input type="checkbox" class="rfq-caplist-chk" value="${escHtml(l.id)}"${sel.has(l.id) ? ' checked' : ''} />
        <span>${escHtml(l.name)} <span class="planner-muted">(${l.n_items})</span></span></label>`).join('');
      box.querySelectorAll('.rfq-caplist-chk').forEach(cb => cb.addEventListener('change', _saveCapListSelectionFromRun));
    } catch (_) {}
  }

  async function _saveCapListSelectionFromRun() {
    const selected = Array.from(document.querySelectorAll('#rfq-caplists .rfq-caplist-chk:checked')).map(o => o.value);
    try {
      await fetch('/api/capacity/selection', {
        method: 'PUT', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ selected }),
      });
      showToast('✓ Aktive Ressourcenlisten gespeichert');
    } catch (_) {}
  }

  // ── Schritt 2: Auswertungslauf (SSE) ────────────────────────────────────────
  function _selectedRag() {
    return Array.from(document.querySelectorAll('#rfq-rag .rfq-rag-chk:checked')).map(o => o.value);
  }

  async function _run(resume) {
    if (_running) return;
    if (!_fileId) { showToast('Bitte zuerst eine Datei einlesen'); return; }
    const taskCol = parseInt(document.getElementById('rfq-task-col').value, 10);
    if (isNaN(taskCol) || taskCol < 0) { showToast('Bitte die Aufgaben-Spalte wählen'); return; }
    const idColV = parseInt(document.getElementById('rfq-id-col').value, 10);
    const titleColV = parseInt(document.getElementById('rfq-title-col').value, 10);
    const body = {
      file_id: _fileId,
      sheet: document.getElementById('rfq-sheet').value || '',
      header_row: parseInt(document.getElementById('rfq-header-row').value, 10) || 0,
      task_col: taskCol,
      id_col: idColV >= 0 ? idColV : null,
      title_col: titleColV >= 0 ? titleColV : null,
      model: document.getElementById('rfq-model').value || undefined,
      web_search: document.getElementById('rfq-web').checked,
      rag_collections: _selectedRag(),
      custom_columns: _customCols,
      limit: parseInt(document.getElementById('rfq-limit').value, 10) || 0,
    };
    if (resume && _jobId) { body.job_id = _jobId; body.resume = true; }

    if (!resume) { _results = []; _rowEls = {}; _resetResultsTable(); }
    _running = true;
    _abort = new AbortController();
    document.getElementById('btn-rfq-run').disabled = true;
    document.getElementById('btn-rfq-resume').disabled = true;
    document.getElementById('btn-rfq-cancel').style.display = '';
    document.getElementById('btn-rfq-export').style.display = 'none';
    document.getElementById('rfq-counters').style.display = 'flex';
    const status = document.getElementById('rfq-status');
    status.textContent = '⏳ Auswertung läuft…';

    try {
      const resp = await fetch('/api/rfq/evaluate', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        signal: _abort.signal, body: JSON.stringify(body),
      });
      if (!resp.ok || !resp.body) {
        let msg = 'HTTP ' + resp.status;
        try { msg = (await resp.json()).detail || msg; } catch (_) {}
        throw new Error(msg);
      }
      const reader = resp.body.getReader();
      const dec = new TextDecoder();
      let buf = '';
      let done = false;
      while (!done) {
        const { value, done: d } = await reader.read();
        done = d;
        if (value) buf += dec.decode(value, { stream: true });
        const lines = buf.split('\n');
        buf = lines.pop();
        for (const line of lines) {
          if (!line.startsWith('data:')) continue;
          let ev;
          try { ev = JSON.parse(line.slice(5).trim()); } catch (_) { continue; }
          _handleEvent(ev);
        }
      }
    } catch (e) {
      if (e.name === 'AbortError') status.textContent = '⏹ Abgebrochen — „Fortsetzen" setzt fort.';
      else { status.textContent = ''; showToast('Fehler: ' + e.message); }
    } finally {
      _running = false;
      document.getElementById('btn-rfq-run').disabled = false;
      document.getElementById('btn-rfq-resume').disabled = false;
      document.getElementById('btn-rfq-cancel').style.display = 'none';
      if (_results.filter(Boolean).length) {
        document.getElementById('btn-rfq-export').style.display = '';
        document.getElementById('btn-rfq-to-plan').style.display = '';
        document.getElementById('btn-rfq-to-rag').style.display = '';
      }
    }
  }

  // ── Übergabe in den Planer ──────────────────────────────────────────────────
  function _openPlanDialog() {
    if (!_results.filter(Boolean).length) { showToast('Erst auswerten'); return; }
    if (!_jobId) { showToast('Keine Auswertung gefunden — bitte neu auswerten'); return; }
    document.getElementById('rfq-plan-status').textContent = '';
    document.getElementById('rfq-plan-overlay').classList.add('active');
  }

  async function _toPlan() {
    const taskCol = parseInt(document.getElementById('rfq-task-col').value, 10);
    const idColV = parseInt(document.getElementById('rfq-id-col').value, 10);
    const titleColV = parseInt(document.getElementById('rfq-title-col').value, 10);
    const body = {
      file_id: _fileId,
      sheet: document.getElementById('rfq-sheet').value || '',
      header_row: parseInt(document.getElementById('rfq-header-row').value, 10) || 0,
      task_col: taskCol,
      id_col: idColV >= 0 ? idColV : null,
      title_col: titleColV >= 0 ? titleColV : null,
      job_id: _jobId,
      model: document.getElementById('rfq-model').value || undefined,
      selection: document.getElementById('rfq-plan-selection').value || 'interesting',
      plan_name: document.getElementById('rfq-plan-name').value.trim() || 'Anfrage-Auswertung',
    };
    const status = document.getElementById('rfq-plan-status');
    const goBtn = document.getElementById('btn-rfq-plan-go');
    goBtn.disabled = true;
    status.textContent = '⏳ Aufwand wird geschätzt…';
    try {
      const resp = await fetch('/api/rfq/to-plan', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      if (!resp.ok || !resp.body) {
        let msg = 'HTTP ' + resp.status;
        try { msg = (await resp.json()).detail || msg; } catch (_) {}
        throw new Error(msg);
      }
      const reader = resp.body.getReader();
      const dec = new TextDecoder();
      let buf = '';
      let done = false;
      let planId = '';
      while (!done) {
        const { value, done: d } = await reader.read();
        done = d;
        if (value) buf += dec.decode(value, { stream: true });
        const lines = buf.split('\n');
        buf = lines.pop();
        for (const line of lines) {
          if (!line.startsWith('data:')) continue;
          let ev;
          try { ev = JSON.parse(line.slice(5).trim()); } catch (_) { continue; }
          if (ev.type === 'start') {
            status.textContent = `⏳ Aufwand wird geschätzt … 0/${ev.total}`
              + (ev.remote && ev.concurrency > 1 ? ` (${ev.concurrency} parallel)` : '');
          } else if (ev.type === 'progress') {
            status.textContent = `⏳ Aufwand wird geschätzt … ${ev.done}/${ev.total}`;
          } else if (ev.type === 'done') {
            planId = ev.plan_id;
            status.textContent = `✓ Plan „${ev.plan_name}" mit ${ev.n} Aufgaben erstellt`;
          } else if (ev.type === 'error') {
            throw new Error(ev.message || 'Fehler');
          }
        }
      }
      if (planId) {
        document.getElementById('rfq-plan-overlay').classList.remove('active');
        showToast('✓ Plan erstellt — Kapazität & Zukauf im 📅-Dialog des Planers');
        if (typeof Planner !== 'undefined' && Planner.openPlan) Planner.openPlan(planId);
        else if (typeof switchTab === 'function') switchTab('planner');
      }
    } catch (e) {
      status.textContent = '✕ ' + e.message;
      showToast('Übergabe fehlgeschlagen: ' + e.message);
    } finally {
      goBtn.disabled = false;
    }
  }

  function _handleEvent(ev) {
    if (ev.type === 'start') {
      _jobId = ev.job_id || _jobId;
      // Server-bereinigte Spaltendefinition übernehmen (Keys/Reihenfolge maßgeblich)
      if (Array.isArray(ev.custom_columns)) { _customCols = ev.custom_columns; _resetResultsTable(); }
      document.getElementById('rfq-c-total').textContent = ev.total;
      if (ev.remote && ev.concurrency > 1) {
        document.getElementById('rfq-status').textContent =
          `⏳ Auswertung läuft (Remote-Modell · ${ev.concurrency} parallel)…`;
      }
    } else if (ev.type === 'row') {
      _results[ev.index] = { index: ev.index, id: ev.id, title: ev.title, task: ev.task, cells: ev.cells || [], result: ev.result || {} };
      _renderResultRow(_results[ev.index]);
      _updateCounters();
      document.getElementById('rfq-status').textContent = `⏳ ${ev.pct}% — Paket ${ev.index + 1}`;
    } else if (ev.type === 'done') {
      const s = ev.summary || {};
      document.getElementById('rfq-status').textContent =
        `✓ Fertig: ${s.n} Pakete · ${s.interesting} interessant · ${s.partner} Partner · ${s.bcc} BCC`;
      if (ev.tokens && typeof TokenMeter !== 'undefined') TokenMeter.add(ev.tokens, 'Anfrage');
    } else if (ev.type === 'error') {
      showToast('Fehler: ' + (ev.message || ''));
    }
  }

  function _resetResultsTable() {
    const base = ['#', 'ID', 'Aufgabe', 'Zuständig', 'Interessant', 'Partner', 'BCC'];
    const cols = base.concat(_customCols.map(c => c.name));
    document.getElementById('rfq-results-head').innerHTML =
      cols.map(h => `<th style="text-align:left;padding:5px 7px;border-bottom:1px solid var(--border);position:sticky;top:0;background:var(--bg-input)">${escHtml(h)}</th>`).join('');
    document.getElementById('rfq-results-body').innerHTML = '';
    document.getElementById('rfq-c-int').textContent = '0';
    document.getElementById('rfq-c-partner').textContent = '0';
    document.getElementById('rfq-c-bcc').textContent = '0';
  }

  function _badge(text, color) {
    return `<span style="display:inline-block;padding:1px 7px;border-radius:10px;font-size:11px;background:${color};color:#fff">${escHtml(text)}</span>`;
  }

  function _renderResultRow(item) {
    const res = item.result || {};
    const td = (html) => `<td style="padding:5px 7px;border-bottom:1px solid var(--border);vertical-align:top">${html}</td>`;
    const interest = (res.interesting || '').toLowerCase();
    const interestBadge = interest === 'ja' ? _badge('ja', '#16a34a')
      : interest === 'nein' ? _badge('nein', '#6b7280')
      : interest === 'fehler' ? _badge('Fehler', '#dc2626')
      : _badge(interest || '?', '#d97706');
    const partner = res.partner_needed ? _badge('ja' + (res.partner_type ? ': ' + res.partner_type : ''), '#7c3aed') : '–';
    const bcc = res.bcc_suitable ? _badge('ja' + (res.bcc_region ? ': ' + res.bcc_region : ''), '#0891b2') : '–';
    const taskShort = (item.task || '').slice(0, 140);
    const html = td(item.index + 1)
      + td(escHtml(item.id || ''))
      + td(`<div title="${escHtml(item.task || '')}" style="max-width:360px">${escHtml(taskShort)}${item.task && item.task.length > 140 ? '…' : ''}</div>`)
      + td(escHtml(res.responsible || ''))
      + td(interestBadge + (res.interesting_reason ? `<div class="planner-muted" style="font-size:11px">${escHtml(res.interesting_reason)}</div>` : ''))
      + td(partner)
      + td(bcc)
      + _customCols.map(c => {
          const cv = (res.custom && res.custom[c.key]) || {};
          return td(escHtml(cv.value || '–') + (cv.note ? `<div class="planner-muted" style="font-size:11px">${escHtml(cv.note)}</div>` : ''));
        }).join('');
    let tr = _rowEls[item.index];
    if (!tr) {
      tr = document.createElement('tr');
      _rowEls[item.index] = tr;
      document.getElementById('rfq-results-body').appendChild(tr);
    }
    tr.innerHTML = html;
    tr.dataset.interesting = interest;
    _applyFilter(tr);
  }

  function _updateCounters() {
    let int = 0, partner = 0, bcc = 0;
    for (const it of _results) {
      if (!it) continue;
      if ((it.result.interesting || '') === 'ja') int++;
      if (it.result.partner_needed) partner++;
      if (it.result.bcc_suitable) bcc++;
    }
    document.getElementById('rfq-c-int').textContent = int;
    document.getElementById('rfq-c-partner').textContent = partner;
    document.getElementById('rfq-c-bcc').textContent = bcc;
  }

  function _applyFilter(tr) {
    const only = document.getElementById('rfq-only-interesting').checked;
    tr.style.display = (only && tr.dataset.interesting !== 'ja') ? 'none' : '';
  }

  function _onFilterChange() {
    Object.values(_rowEls).forEach(_applyFilter);
  }

  // ── Chat-Zeile: Rückfrage zur ausgewerteten Anfrage ─────────────────────────
  function _buildAskContext() {
    const filled = _results.filter(Boolean);
    if (!filled.length) return '';
    const n = filled.length;
    const intl = filled.filter(it => (it.result.interesting || '') === 'ja');
    const partner = filled.filter(it => it.result.partner_needed);
    const bcc = filled.filter(it => it.result.bcc_suitable);
    const line = (it) => {
      const r = it.result || {};
      const t = (it.title || it.task || '').slice(0, 80);
      let s = `#${it.index + 1} ${it.id ? '[' + it.id + '] ' : ''}${t} — Rolle: ${r.responsible || '?'}, interessant: ${r.interesting || '?'}`;
      if (r.partner_needed) s += `, Partner: ${r.partner_type || 'ja'}`;
      if (r.bcc_suitable) s += `, BCC: ${r.bcc_region || 'ja'}`;
      for (const c of _customCols) {
        const cv = (r.custom && r.custom[c.key]) || {};
        if (cv.value) s += `, ${c.name}: ${cv.value}`;
      }
      return s;
    };
    let ctx = `Anfrage mit ${n} ausgewerteten Paketen — ${intl.length} interessant, ${partner.length} mit Partnerbedarf, ${bcc.length} Best-Cost-Country-tauglich.\n\n`;
    // bevorzugt interessante Pakete listen, sonst alle (gedeckelt)
    const list = (intl.length ? intl : filled).slice(0, 60);
    ctx += list.map(line).join('\n');
    return ctx.slice(0, 9000);
  }

  // ── Auswertung in eine Wissensdatenbank (RAG) übernehmen ────────────────────
  async function _toRag() {
    const filled = _results.filter(Boolean);
    if (!filled.length) { showToast('Keine Ergebnisse zum Übernehmen'); return; }
    if (typeof RAG === 'undefined' || !RAG.ingestText) { showToast('RAG-Modul nicht verfügbar'); return; }
    const fname = (_file && _file.name) ? _file.name.replace(/\.[^.]+$/, '') : 'Anfrage';
    const lines = [`# Anfrage-Auswertung: ${fname}`, '', `${filled.length} Arbeitspakete ausgewertet.`, ''];
    for (const it of filled) {
      const r = it.result || {};
      lines.push(`## ${it.id ? '[' + it.id + '] ' : ''}${it.title || it.task || ('Paket ' + (it.index + 1))}`);
      if (it.task && it.task !== it.title) lines.push(it.task);
      lines.push(`- Zuständig: ${r.responsible || '—'}`);
      lines.push(`- Interessant: ${r.interesting || '—'}${r.interesting_reason ? ' (' + r.interesting_reason + ')' : ''}`);
      if (r.partner_needed) lines.push(`- Partner nötig: ${r.partner_type || 'ja'}`);
      if (r.bcc_suitable) lines.push(`- Best-Cost-Country: ${r.bcc_region || 'ja'}${r.bcc_reason ? ' — ' + r.bcc_reason : ''}`);
      for (const c of _customCols) {
        const cv = (r.custom && r.custom[c.key]) || {};
        if (cv.value) lines.push(`- ${c.name}: ${cv.value}${cv.note ? ' (' + cv.note + ')' : ''}`);
      }
      lines.push('');
    }
    // RAG.ingestText kümmert sich um Sammlungs-Auswahl, Einbettung und Rückmeldung
    await RAG.ingestText('Anfrage-Auswertung: ' + fname, lines.join('\n'));
  }

  async function _askChat() {
    const inp = document.getElementById('rfq-chat-input');
    const q = (inp.value || '').trim();
    if (!q) return;
    const ansEl = document.getElementById('rfq-chat-answer');
    const btn = document.getElementById('btn-rfq-chat-send');
    ansEl.style.display = 'block';
    ansEl.textContent = '⏳ …';
    btn.disabled = true; inp.disabled = true;
    let model;
    try { model = (typeof Profile !== 'undefined' ? Profile.modelFor('general') : '') || undefined; } catch (_) {}
    try {
      const r = await fetch('/api/rfq/ask', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ question: q, context: _buildAskContext(), model }),
      });
      const d = await r.json();
      if (!r.ok) throw new Error(d.detail || r.status);
      ansEl.textContent = d.answer || '(keine Antwort)';
      if (d.tokens && typeof TokenMeter !== 'undefined') TokenMeter.add(d.tokens, 'Anfrage-Frage');
      inp.value = '';
    } catch (e) {
      ansEl.textContent = '❌ ' + e.message;
    } finally {
      btn.disabled = false; inp.disabled = false; inp.focus();
    }
  }

  // ── XLSX-Export (Originalspalten + Auswertungsspalten) ──────────────────────
  async function _export() {
    const filled = _results.filter(Boolean);
    if (!filled.length) { showToast('Keine Ergebnisse zum Exportieren'); return; }
    const headers = [..._headers, ...EVAL_HEADERS, ..._customCols.map(c => c.name)];
    const rows = filled.map(it => {
      const r = it.result || {};
      const custom = _customCols.map(c => {
        const cv = (r.custom && r.custom[c.key]) || {};
        return [cv.value || '', cv.note || ''].filter(Boolean).join(' — ');
      });
      return [...(it.cells || []),
        r.responsible || '',
        r.interesting || '',
        r.interesting_reason || '',
        r.partner_needed ? 'ja' : 'nein',
        r.partner_type || '',
        r.bcc_suitable ? 'ja' : 'nein',
        r.bcc_region || '',
        r.bcc_reason || '',
        ...custom];
    });
    try {
      const resp = await fetch('/api/export/xlsx', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ title: 'Anfrage-Auswertung', headers, rows }),
      });
      if (!resp.ok) throw new Error('HTTP ' + resp.status);
      const blob = await resp.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url; a.download = 'anfrage_auswertung.xlsx'; a.click();
      URL.revokeObjectURL(url);
      showToast('✓ XLSX exportiert');
    } catch (e) { showToast('Export fehlgeschlagen: ' + e.message); }
  }

  // ── Ressourcen-/Kapazitätslisten (mehrere, benannt) ─────────────────────────
  let _capLists = [];      // [{id,name,n_items,updated_at}]
  let _capSelected = [];   // aktive Listen-IDs
  let _capEditId = null;   // gerade bearbeitete Liste

  function _capStatus(msg) {
    const el = document.getElementById('rfq-cap-status');
    if (el) el.textContent = msg || '';
  }

  async function _openCap() {
    await _loadCapLists();
    // erste Liste zum Bearbeiten wählen
    _capEditId = (_capLists.find(l => _capSelected.includes(l.id)) || _capLists[0] || {}).id || null;
    await _loadCapList(_capEditId);
    document.getElementById('rfq-cap-overlay').classList.add('active');
  }

  async function _loadCapLists() {
    try {
      const d = await (await fetch('/api/capacity/lists')).json();
      _capLists = d.lists || [];
      _capSelected = d.selected || [];
    } catch (_) { _capLists = []; _capSelected = []; }
    _renderCapLists();
  }

  function _renderCapLists() {
    // aktive Listen als Häkchen
    const act = document.getElementById('rfq-cap-active');
    if (act) {
      act.innerHTML = _capLists.length
        ? _capLists.map(l => `<label style="display:flex;align-items:center;gap:5px;cursor:pointer">
            <input type="checkbox" class="rfq-cap-active-chk" value="${escHtml(l.id)}"${_capSelected.includes(l.id) ? ' checked' : ''} />
            <span>${escHtml(l.name)} <span class="planner-muted">(${l.n_items})</span></span></label>`).join('')
        : '<span class="planner-muted" style="font-size:11px">— noch keine Liste —</span>';
      act.querySelectorAll('.rfq-cap-active-chk').forEach(cb =>
        cb.addEventListener('change', _saveCapSelection));
    }
    // Bearbeiten-Dropdown
    const sel = document.getElementById('rfq-cap-listsel');
    if (sel) {
      sel.innerHTML = _capLists.map(l =>
        `<option value="${escHtml(l.id)}"${l.id === _capEditId ? ' selected' : ''}>${escHtml(l.name)}</option>`).join('');
    }
  }

  async function _saveCapSelection() {
    _capSelected = Array.from(document.querySelectorAll('#rfq-cap-active .rfq-cap-active-chk:checked')).map(o => o.value);
    try {
      await fetch('/api/capacity/selection', {
        method: 'PUT', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ selected: _capSelected }),
      });
      _capStatus('✓ Auswahl gespeichert');
    } catch (_) { _capStatus('Auswahl konnte nicht gespeichert werden'); }
  }

  async function _loadCapList(id) {
    _capEditId = id;
    if (!id) { _cap = []; _renderCapTable(); return; }
    try { _cap = (await (await fetch('/api/capacity/lists/' + encodeURIComponent(id))).json()).items || []; }
    catch (_) { _cap = []; }
    _renderCapTable();
  }

  async function _capNewList() {
    const name = (prompt('Name der neuen Liste:', 'Neue Liste') || '').trim();
    if (!name) return;
    try {
      const r = await (await fetch('/api/capacity/lists', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name }),
      })).json();
      await _loadCapLists();
      await _loadCapList(r.id);
      _renderCapLists();
      _capStatus('✓ Liste angelegt (und aktiviert)');
    } catch (e) { _capStatus('Fehler: ' + e.message); }
  }

  async function _capRenameList() {
    if (!_capEditId) return;
    const cur = (_capLists.find(l => l.id === _capEditId) || {}).name || '';
    const name = (prompt('Liste umbenennen:', cur) || '').trim();
    if (!name || name === cur) return;
    try {
      await fetch('/api/capacity/lists/' + encodeURIComponent(_capEditId), {
        method: 'PUT', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name }),
      });
      await _loadCapLists();
      _capStatus('✓ Umbenannt');
    } catch (e) { _capStatus('Fehler: ' + e.message); }
  }

  async function _capDeleteList() {
    if (!_capEditId) return;
    const cur = (_capLists.find(l => l.id === _capEditId) || {}).name || 'Liste';
    if (!confirm(`Liste „${cur}" wirklich löschen?`)) return;
    try {
      await fetch('/api/capacity/lists/' + encodeURIComponent(_capEditId), { method: 'DELETE' });
      await _loadCapLists();
      _capEditId = (_capLists[0] || {}).id || null;
      await _loadCapList(_capEditId);
      _renderCapLists();
      _capStatus('✓ Liste gelöscht');
    } catch (e) { _capStatus('Fehler: ' + e.message); }
  }

  function _renderCapTable() {
    const tbl = document.getElementById('rfq-cap-table');
    const head = ['Typ', 'Name', 'Satz (€)', 'Land', 'Kapazität (h)', 'Skills', ''];
    const th = '<thead><tr>' + head.map(h => `<th style="text-align:left;padding:4px 6px;border-bottom:1px solid var(--border)">${h}</th>`).join('') + '</tr></thead>';
    const kindOpts = (k) => ['human', 'hardware', 'software'].map(v =>
      `<option value="${v}"${v === k ? ' selected' : ''}>${v === 'human' ? 'Mensch' : v === 'hardware' ? 'Hardware' : 'Software'}</option>`).join('');
    const tb = '<tbody>' + _cap.map((it, i) => `<tr data-i="${i}">
      <td style="padding:3px 5px"><select class="sidebar-select cap-kind" style="font-size:11px">${kindOpts(it.kind)}</select></td>
      <td style="padding:3px 5px"><input class="sidebar-select cap-name" style="font-size:11px" value="${escHtml(it.name || '')}" /></td>
      <td style="padding:3px 5px"><input class="sidebar-select cap-rate" type="number" style="font-size:11px;width:80px" value="${it.rate || 0}" /></td>
      <td style="padding:3px 5px"><input class="sidebar-select cap-country" style="font-size:11px;width:110px" value="${escHtml(it.country || '')}" /></td>
      <td style="padding:3px 5px"><input class="sidebar-select cap-cap" type="number" style="font-size:11px;width:80px" value="${it.capacity_h || 0}" /></td>
      <td style="padding:3px 5px"><input class="sidebar-select cap-skills" style="font-size:11px" value="${escHtml(it.skills || '')}" /></td>
      <td style="padding:3px 5px"><button class="export-btn cap-del" data-i="${i}" style="font-size:11px">🗑</button></td>
    </tr>`).join('') + '</tbody>';
    tbl.innerHTML = th + tb;
    tbl.querySelectorAll('.cap-del').forEach(b =>
      b.addEventListener('click', () => { _readCapTable(); _cap.splice(parseInt(b.dataset.i, 10), 1); _renderCapTable(); }));
  }

  function _readCapTable() {
    const rows = document.querySelectorAll('#rfq-cap-table tbody tr');
    _cap = Array.from(rows).map(tr => ({
      kind: tr.querySelector('.cap-kind').value,
      name: tr.querySelector('.cap-name').value.trim(),
      rate: parseFloat(tr.querySelector('.cap-rate').value) || 0,
      country: tr.querySelector('.cap-country').value.trim(),
      capacity_h: parseFloat(tr.querySelector('.cap-cap').value) || 0,
      skills: tr.querySelector('.cap-skills').value.trim(),
    })).filter(it => it.name);
  }

  function _capAdd() {
    _readCapTable();
    _cap.push({ kind: 'human', name: '', rate: 0, country: '', capacity_h: 0, skills: '' });
    _renderCapTable();
  }

  async function _capSave() {
    _readCapTable();
    if (!_capEditId) { await _capNewList(); if (!_capEditId) return; }
    try {
      const r = await fetch('/api/capacity/lists/' + encodeURIComponent(_capEditId), {
        method: 'PUT', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ items: _cap }),
      });
      if (!r.ok) throw new Error((await r.json()).detail || r.status);
      _cap = (await r.json()).items || _cap;
      await _loadCapLists();          // n_items aktualisieren
      showToast('✓ Liste gespeichert');
      _capStatus('✓ Gespeichert');
    } catch (e) { showToast('Fehler: ' + e.message); }
  }

  // CSV: Typ;Name;Satz;Land;Kapazität;Skills (Trennzeichen ; oder ,)
  function _capImportCsv(file) {
    const reader = new FileReader();
    reader.onload = () => {
      _readCapTable();
      const text = String(reader.result || '');
      const delim = (text.split('\n')[0] || '').includes(';') ? ';' : ',';
      const lines = text.split(/\r?\n/).filter(l => l.trim());
      const deKind = (s) => { s = (s || '').toLowerCase(); return s.startsWith('mensch') || s.startsWith('human') ? 'human' : s.startsWith('hard') ? 'hardware' : s.startsWith('soft') ? 'software' : 'human'; };
      let start = 0;
      if (lines.length && /typ|name|satz|kind/i.test(lines[0])) start = 1;
      for (let i = start; i < lines.length; i++) {
        const c = lines[i].split(delim);
        if (!c[1] && !c[0]) continue;
        _cap.push({
          kind: deKind(c[0]), name: (c[1] || c[0] || '').trim(),
          rate: parseFloat(c[2]) || 0, country: (c[3] || '').trim(),
          capacity_h: parseFloat(c[4]) || 0, skills: (c[5] || '').trim(),
        });
      }
      _renderCapTable();
      showToast('✓ CSV importiert — noch speichern');
    };
    reader.readAsText(file);
  }

  function init() {
    document.getElementById('btn-rfq-preview')?.addEventListener('click', () => _preview(false));
    document.getElementById('btn-rfq-reload')?.addEventListener('click', () => _preview(true));
    document.getElementById('btn-rfq-run')?.addEventListener('click', () => _run(false));
    document.getElementById('btn-rfq-resume')?.addEventListener('click', () => _run(true));
    document.getElementById('btn-rfq-cancel')?.addEventListener('click', () => { if (_abort) _abort.abort(); });
    document.getElementById('btn-rfq-export')?.addEventListener('click', _export);
    document.getElementById('btn-rfq-to-rag')?.addEventListener('click', _toRag);
    document.getElementById('btn-rfq-to-plan')?.addEventListener('click', _openPlanDialog);
    document.getElementById('btn-rfq-plan-close')?.addEventListener('click', () =>
      document.getElementById('rfq-plan-overlay').classList.remove('active'));
    document.getElementById('btn-rfq-plan-go')?.addEventListener('click', _toPlan);
    document.getElementById('rfq-only-interesting')?.addEventListener('change', _onFilterChange);
    document.getElementById('btn-rfq-capacity')?.addEventListener('click', _openCap);
    document.getElementById('btn-rfq-cap-close')?.addEventListener('click', () =>
      document.getElementById('rfq-cap-overlay').classList.remove('active'));
    document.getElementById('btn-rfq-cap-add')?.addEventListener('click', _capAdd);
    document.getElementById('btn-rfq-cap-save')?.addEventListener('click', _capSave);
    document.getElementById('btn-rfq-cap-new')?.addEventListener('click', _capNewList);
    document.getElementById('btn-rfq-cap-rename')?.addEventListener('click', _capRenameList);
    document.getElementById('btn-rfq-cap-delete')?.addEventListener('click', _capDeleteList);
    document.getElementById('rfq-cap-listsel')?.addEventListener('change', (e) => {
      _readCapTable(); _loadCapList(e.target.value);
    });
    // Eigene Bewertungsspalten
    _loadCustomCols();
    document.getElementById('btn-rfq-columns')?.addEventListener('click', _openCols);
    document.getElementById('btn-rfq-cols-close')?.addEventListener('click', () =>
      document.getElementById('rfq-cols-overlay').classList.remove('active'));
    document.getElementById('btn-rfq-cols-add')?.addEventListener('click', _colsAdd);
    document.getElementById('btn-rfq-cols-save')?.addEventListener('click', _colsSave);
    // Chat-Zeile (Rückfrage zur Anfrage)
    document.getElementById('btn-rfq-chat-send')?.addEventListener('click', _askChat);
    document.getElementById('rfq-chat-input')?.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') { e.preventDefault(); _askChat(); }
    });
    document.getElementById('btn-rfq-cap-import')?.addEventListener('click', () =>
      document.getElementById('rfq-cap-csv').click());
    document.getElementById('rfq-cap-csv')?.addEventListener('change', (e) => {
      if (e.target.files && e.target.files[0]) _capImportCsv(e.target.files[0]);
      e.target.value = '';
    });
    // Beim ersten Öffnen des Tabs Modelle + Wissensdatenbanken + Ressourcenlisten laden
    document.querySelector('.tab-btn[data-tab="rfq"]')?.addEventListener('click', () => {
      if (!document.getElementById('rfq-model').options.length) _loadModels();
      _loadRag();
      _loadCapListsForRun();
    });
  }

  return { init, openCapacity: _openCap };
})();
