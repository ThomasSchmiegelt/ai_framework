/* ── KI-To-Do-Baum mit Wissensgraph (DB-gestützt) ────────────────────────────
 *
 * Ein Projektbaum: Wurzel = Benutzername, darunter beliebig tiefe Unterprojekte.
 * Punkte werden per Besprechungsnotiz abgeleitet, abgehakt, zwischen Projekten
 * verschoben und mit Dokumenten (→ Markdown) belegt. Ein Projekt lässt sich
 * AKTIVIEREN: Suche und Wissensgraph zeigen dann nur diesen Teilbaum; die Wurzel
 * aktiv = alle Projekte verbunden. Speicherung in der SQLite-DB (via /api/todo/*).
 */
const Todo = (() => {
  let _tree = [];       // verschachtelter Baum
  let _flat = [];       // flache Projektliste (für Move-Dropdowns)
  let _pid = '';        // aktuell geöffnetes Projekt
  let _data = null;     // vollständige Projektdaten
  let _active = 'root'; // aktiver Scope (Suche/Graph)
  let _view = 'liste';
  let _cy = null;
  let _showHubs = true;
  let _forceAll = false;
  let _connectMode = false, _connectFrom = null;
  let _fileInput = null, _attachTarget = null;
  let _importInput = null;
  let _searchTimer = null;
  let _graphPerson = '';           // Personenfilter im Wissensgraph
  let _graphMode = '2d';           // '2d' (Cytoscape) | '3d' (Canvas-Kugel)
  const ACTIVE_KEY = 'ai_framework_thomas_todo_active';
  const LAYOUT_KEY = 'ai_framework_thomas_todo_layout';   // Splitter/Collapse-Zustand
  const GRAPH_MODE_KEY = 'ai_framework_thomas_todo_graphmode';
  const _filters = { done: false, from: '', to: '', assign: '' };   // Punkte-Filter

  const STATUS = {
    offen: { color: '#9ca3af' }, laeuft: { color: '#3b82f6' }, erledigt: { color: '#22c55e' },
  };
  const PROJ_COLORS = ['#4f8cff', '#a78bfa', '#22c55e', '#f59e0b', '#ef4444', '#14b8a6', '#ec4899', '#84cc16'];

  function _el(id) { return document.getElementById(id); }
  function _model() { return (typeof Profile !== 'undefined' && Profile.modelFor) ? Profile.modelFor('general') : ''; }
  function _spin(on) { const s = _el('todo-spin'); if (s) s.style.display = on ? '' : 'none'; }
  function _status(t) { const s = _el('todo-status'); if (s) s.textContent = t || ''; }
  function _tok(tokens) { if (tokens && typeof TokenMeter !== 'undefined') TokenMeter.add(tokens, 'To-Do'); }

  async function _api(method, url, body) {
    const opt = { method, headers: { 'Content-Type': 'application/json' } };
    if (body !== undefined) opt.body = JSON.stringify(body);
    const r = await fetch(url, opt);
    if (!r.ok) { let m = 'HTTP ' + r.status; try { m = (await r.json()).detail || m; } catch (_) {} throw new Error(m); }
    return r.json();
  }

  // ── Baum laden / rendern ───────────────────────────────────────────────────
  async function _loadTree(openFirst) {
    try {
      const t = await _api('GET', '/api/todo/tree');
      _tree = t.tree || [];
      _flat = t.flat || [];
      try { _active = localStorage.getItem(ACTIVE_KEY) || 'root'; } catch (_) { _active = 'root'; }
      if (!_flat.some(p => p.id === _active)) _active = 'root';
      _renderTree();
      _updateActiveLabel();
      if (openFirst && !_pid) {
        const root = _flat.find(p => p.id === 'root');
        if (root) _open('root');
      }
    } catch (e) { _status('Baum-Fehler: ' + e.message); }
  }

  function _nodeName(p) { return p.name || p.title || '(ohne Name)'; }

  function _renderNode(p, depth) {
    const done = (p.n_done || 0), total = (p.n_items || 0);
    const badge = total ? `<span class="todo-tree-count">${done}/${total}</span>` : '';
    const isActive = p.id === _active;
    const isOpen = p.id === _pid;
    const rootMark = p.id === 'root' ? '👤 ' : '';
    let html = `<div class="todo-tree-node ${isOpen ? 'open' : ''} ${isActive ? 'active-scope' : ''}" data-id="${p.id}" style="padding-left:${6 + depth * 16}px">
      <span class="todo-tree-name" data-id="${p.id}" title="Öffnen">${rootMark}${escHtml(_nodeName(p))} ${badge}</span>
      <span class="todo-tree-actions">
        <button class="todo-tree-btn ${isActive ? 'on' : ''}" data-act="activate" data-id="${p.id}" title="Aktivieren (Scope für Suche &amp; Graph)">⚡</button>
        <button class="todo-tree-btn" data-act="add" data-id="${p.id}" title="Unterprojekt anlegen">➕</button>
        <button class="todo-tree-btn" data-act="rename" data-id="${p.id}" title="Umbenennen">✎</button>
        ${p.id === 'root' ? '' : `<button class="todo-tree-btn" data-act="delete" data-id="${p.id}" title="Löschen">✕</button>`}
      </span>
    </div>`;
    (p.children || []).forEach(ch => { html += _renderNode(ch, depth + 1); });
    return html;
  }

  function _renderTree() {
    const host = _el('todo-tree');
    host.innerHTML = _tree.map(p => _renderNode(p, 0)).join('') || '<span class="planner-muted">—</span>';
    host.querySelectorAll('.todo-tree-name').forEach(el => el.addEventListener('click', () => _open(el.dataset.id)));
    host.querySelectorAll('.todo-tree-btn').forEach(b => b.addEventListener('click', e => {
      e.stopPropagation();
      const id = b.dataset.id, act = b.dataset.act;
      if (act === 'activate') _activate(id);
      else if (act === 'add') _createSub(id);
      else if (act === 'rename') _rename(id);
      else if (act === 'delete') _deleteProject(id);
    }));
  }

  function _updateActiveLabel() {
    const p = _flat.find(x => x.id === _active);
    const el = _el('todo-active-label');
    if (el) el.textContent = p ? `⚡ aktiv: ${_nodeName(p)}${_active === 'root' ? ' (alle)' : ' + Unterprojekte'}` : '';
  }

  function _activate(id) {
    _active = id;
    try { localStorage.setItem(ACTIVE_KEY, id); } catch (_) {}
    _renderTree(); _updateActiveLabel();
    if (_el('todo-search').value.trim()) _runSearch();
    if (_view === 'graph') _buildGraph();
    else if (_view === 'agenda') _loadAgenda();
    _status('Aktiver Bereich gesetzt.');
    setTimeout(() => _status(''), 1200);
  }

  // ── Projekt anlegen / öffnen / umbenennen / löschen ────────────────────────
  async function _createSub(parentId) {
    const name = (_el('todo-new-name').value || '').trim() || 'Neues Projekt';
    _spin(true);
    try {
      const p = await _api('POST', '/api/todo/projects',
        { name, parent_id: parentId || _active || 'root', type: _el('todo-new-type').value });
      _el('todo-new-name').value = '';
      await _loadTree();
      await _open(p.id);
    } catch (e) { _status('Fehler: ' + e.message); }
    finally { _spin(false); }
  }

  async function _open(pid) {
    if (!pid) return;
    _spin(true);
    try {
      _data = await _api('GET', '/api/todo/projects/' + encodeURIComponent(pid));
      _pid = pid;
      _el('todo-empty').style.display = 'none';
      _clearSearch();
      _renderTree();
      _showView(_view);
      _renderHeader();
      _renderItems();
      _markGraphStale();
    } catch (e) { _status('Fehler: ' + e.message); }
    finally { _spin(false); }
  }

  async function _rename(pid) {
    const cur = _flat.find(p => p.id === pid);
    const name = prompt('Neuer Name:', cur ? _nodeName(cur) : '');
    if (name === null || !name.trim()) return;
    await _api('POST', `/api/todo/projects/${encodeURIComponent(pid)}/rename`, { name: name.trim() });
    await _loadTree();
    if (_pid === pid && _data) { _data.name = name.trim(); }
  }

  async function _deleteProject(pid) {
    const cur = _flat.find(p => p.id === pid);
    const hasKids = _flat.some(p => p.parent_id === pid);
    let reparent = false;
    if (hasKids) {
      reparent = !confirm(`„${cur ? _nodeName(cur) : pid}" hat Unterprojekte.\n\nOK = mitsamt Unterprojekten löschen\nAbbrechen = Unterprojekte eine Ebene hochziehen`);
    } else if (!confirm(`Projekt „${cur ? _nodeName(cur) : pid}" löschen?`)) {
      return;
    }
    await _api('DELETE', `/api/todo/projects/${encodeURIComponent(pid)}?reparent=${reparent ? 1 : 0}`);
    if (_pid === pid) { _pid = ''; _data = null; _el('todo-empty').style.display = 'block'; _showView(null); }
    if (_active === pid) _activate('root');
    await _loadTree();
  }

  function _collect() {
    if (!_data) return;
    _data.title = _el('todo-title').value.trim();
    _data.date = _el('todo-date').value;
    _data.project_ref = _el('todo-project').value;
    _data.participants = (_el('todo-participants').value || '').split(',').map(s => s.trim()).filter(Boolean);
  }

  async function _save() {
    if (!_pid || !_data) { _status('Kein Projekt geöffnet.'); return; }
    _collect();
    _spin(true);
    try {
      _data = await _api('PUT', '/api/todo/projects/' + encodeURIComponent(_pid), {
        type: _data.type, title: _data.title, date: _data.date,
        participants: _data.participants, project_id: _data.project_ref,
        items: _data.items, edges: _data.edges,
        settings: _data.settings || {},
      });
      _status('💾 gespeichert'); await _loadTree();
      setTimeout(() => _status(''), 1200);
    } catch (e) { _status('Speichern: ' + e.message); }
    finally { _spin(false); }
  }

  // ── Ansichten / Header ─────────────────────────────────────────────────────
  function _showView(v) {
    _el('todo-view-liste').style.display = (v === 'liste') ? 'flex' : 'none';
    _el('todo-view-graph').style.display = (v === 'graph') ? 'block' : 'none';
    _el('todo-view-agenda').style.display = (v === 'agenda') ? 'block' : 'none';
    document.querySelectorAll('.todo-subtab').forEach(b => b.classList.toggle('active', b.dataset.view === v));
    if (v !== 'graph') { _graph3d.stop(); return; }   // 3D-Schleife nur im Graph-View laufen lassen
    if (_graphMode === '3d') setTimeout(() => _graph3d.resize(), 30);
    else if (_cy) setTimeout(() => { _cy.resize(); _cy.fit(undefined, 30); }, 30);
  }

  function _renderHeader() {
    _el('todo-title').value = _data.title || '';
    _el('todo-date').value = _data.date || '';
    _el('todo-participants').value = (_data.participants || []).join(', ');
    _loadRefProjects(_data.project_ref || '');
  }

  async function _loadRefProjects(sel) {
    try {
      const projs = await _api('GET', '/api/projects');
      const s = _el('todo-project');
      s.innerHTML = '<option value="">— kein Projekt —</option>' +
        projs.map(p => `<option value="${escHtml(p.id)}">${escHtml(p.name)}</option>`).join('');
      s.value = sel || '';
    } catch (_) {}
  }

  // ── Punkte ─────────────────────────────────────────────────────────────────
  function _itemById(id) { return (_data.items || []).find(it => it.id === id); }

  function _moveOptions(curId) {
    return _flat.filter(p => p.id !== _pid)
      .map(p => `<option value="${escHtml(p.id)}">${escHtml((p.id === 'root' ? '👤 ' : '') + _nodeName(p))}</option>`).join('');
  }

  function _attChips(it) {
    return (it.attachments || []).map(a =>
      `<span class="todo-att-chip"><a href="/api/todo/attachment/${encodeURIComponent(a.id)}" target="_blank" rel="noopener" title="${escHtml(a.name || '')} (als Markdown)">📄 ${escHtml(a.name || 'Anlage')}</a><button class="todo-att-del" data-att="${a.id}" title="Anlage entfernen">✕</button></span>`).join('');
  }

  function _renderItems() {
    const items = _data.items || [];
    const done = items.filter(it => it.status === 'erledigt').length;
    const cntEl = _el('todo-count');
    cntEl.dataset.base = items.length ? `(${done}/${items.length} erledigt)` : '';
    cntEl.textContent = cntEl.dataset.base;
    const host = _el('todo-items');
    if (!items.length) { host.innerHTML = '<span class="planner-muted">Noch keine Punkte. Notiz schreiben und „🪄 To-Do-Liste ableiten" – oder „➕ Punkt".</span>'; return; }
    const id2text = {}; items.forEach(it => id2text[it.id] = it.text);
    host.innerHTML = items.map((it, idx) => {
      const isDone = it.status === 'erledigt';
      const outs = (_data.edges || []).filter(e => e.source === it.id)
        .map(e => `<span class="todo-link">→ ${escHtml(id2text[e.target] || '?')}${e.label ? ' (' + escHtml(e.label) + ')' : ''}</span>`).join(' ');
      const atts = _attChips(it);
      return `<div class="todo-item${isDone ? ' todo-done' : ''}" data-id="${it.id}" data-status="${it.status || 'offen'}" data-due="${escHtml(it.due || '')}" data-assign="${escHtml((it.assignees || []).join(', ').toLowerCase())}">
        <span class="todo-reorder">
          <button class="todo-mini" data-act="up" data-id="${it.id}" ${idx === 0 ? 'disabled' : ''} title="nach oben">▲</button>
          <button class="todo-mini" data-act="down" data-id="${it.id}" ${idx === items.length - 1 ? 'disabled' : ''} title="nach unten">▼</button>
        </span>
        <input type="checkbox" class="todo-check" data-id="${it.id}" ${isDone ? 'checked' : ''} title="Erledigt" />
        <input type="text" class="var-input todo-it-text" data-id="${it.id}" value="${escHtml(it.text)}" placeholder="Aufgabe" />
        <input type="text" class="var-input todo-it-assign" data-id="${it.id}" value="${escHtml((it.assignees || []).join(', '))}" placeholder="Zuständig" style="max-width:130px" />
        <input type="text" class="var-input todo-it-due" data-id="${it.id}" value="${escHtml(it.due || '')}" placeholder="Frist" style="max-width:90px" />
        <button class="export-btn todo-it-attach" data-id="${it.id}" title="Dokument anhängen (→ Markdown)">📎</button>
        <select class="todo-it-move" data-id="${it.id}" title="In anderes Projekt verschieben"><option value="">↪ verschieben…</option>${_moveOptions(it.id)}</select>
        <button class="export-btn btn-danger-sm todo-it-del" data-id="${it.id}" title="Punkt entfernen">✕</button>
        ${(atts || outs) ? `<div class="todo-item-sub">${atts}${outs ? ` <span class="todo-links">${outs}</span>` : ''}</div>` : ''}
      </div>`;
    }).join('');

    host.querySelectorAll('.todo-check').forEach(cb => cb.addEventListener('change', e => {
      const it = _itemById(e.target.dataset.id); if (it) { it.status = e.target.checked ? 'erledigt' : 'offen'; _renderItems(); _markGraphStale(); }
    }));
    host.querySelectorAll('.todo-it-text').forEach(inp => inp.addEventListener('change', e => {
      const it = _itemById(e.target.dataset.id); if (it) { it.text = e.target.value; _markGraphStale(); }
    }));
    host.querySelectorAll('.todo-it-assign').forEach(inp => inp.addEventListener('change', e => {
      const it = _itemById(e.target.dataset.id); if (it) { it.assignees = e.target.value.split(',').map(s => s.trim()).filter(Boolean); _markGraphStale(); }
    }));
    host.querySelectorAll('.todo-it-due').forEach(inp => inp.addEventListener('change', e => {
      const it = _itemById(e.target.dataset.id); if (it) it.due = e.target.value;
    }));
    host.querySelectorAll('.todo-mini').forEach(b => b.addEventListener('click', e => _reorder(e.currentTarget.dataset.id, e.currentTarget.dataset.act)));
    host.querySelectorAll('.todo-it-attach').forEach(b => b.addEventListener('click', e => _pickAttachment(e.currentTarget.dataset.id)));
    host.querySelectorAll('.todo-it-move').forEach(s => s.addEventListener('change', e => { if (e.target.value) _moveItem(e.target.dataset.id, e.target.value); }));
    host.querySelectorAll('.todo-att-del').forEach(b => b.addEventListener('click', e => _deleteAttachment(e.currentTarget.dataset.att)));
    host.querySelectorAll('.todo-it-del').forEach(b => b.addEventListener('click', e => {
      const id = e.currentTarget.dataset.id;
      _data.items = _data.items.filter(it => it.id !== id);
      _data.edges = (_data.edges || []).filter(ed => ed.source !== id && ed.target !== id);
      _renderItems(); _markGraphStale();
    }));
    _populateAssignFilter(items);
    _applyItemFilters();
  }

  // ── Punkte-Filter (erledigt / Datumsbereich / Zuständige) ───────────────────
  function _populateAssignFilter(items) {
    const sel = _el('todo-filter-assign'); if (!sel) return;
    const names = Array.from(new Set((items || []).flatMap(it => it.assignees || []).map(s => s.trim()).filter(Boolean))).sort();
    const cur = sel.value;
    sel.innerHTML = '<option value="">👤 alle</option>' + names.map(n => `<option value="${escHtml(n)}">${escHtml(n)}</option>`).join('');
    sel.value = names.includes(cur) ? cur : '';
    if (sel.value !== cur) _filters.assign = sel.value;
  }

  function _applyItemFilters() {
    const from = _filters.from, to = _filters.to, asg = (_filters.assign || '').toLowerCase();
    let shown = 0, total = 0;
    document.querySelectorAll('#todo-items .todo-item').forEach(row => {
      total++;
      let ok = true;
      if (_filters.done && row.dataset.status === 'erledigt') ok = false;
      if (ok && (from || to)) {
        const d = _parseDue(row.dataset.due);
        if (!d) ok = false;                          // ohne Frist bei aktivem Datumsfilter ausblenden
        else { if (from && d < from) ok = false; if (to && d > to) ok = false; }
      }
      if (ok && asg && !(row.dataset.assign || '').includes(asg)) ok = false;
      row.classList.toggle('todo-filtered', !ok);
      if (ok) shown++;
    });
    const active = _filters.done || _filters.from || _filters.to || _filters.assign;
    const wrap = document.querySelector('.todo-filters');
    if (wrap) wrap.classList.toggle('active', !!active);
    const cnt = _el('todo-count');
    if (cnt) cnt.textContent = (cnt.dataset.base || '') + (active && total ? ` · ${shown}/${total} sichtbar` : '');
  }

  // ISO-/DE-Datum grob nach YYYY-MM-DD normalisieren (für Bereichsvergleich als String)
  function _parseDue(s) {
    s = (s || '').trim(); if (!s) return '';
    let m = s.match(/^(\d{4})-(\d{2})-(\d{2})/); if (m) return `${m[1]}-${m[2]}-${m[3]}`;
    m = s.match(/^(\d{1,2})\.(\d{1,2})\.(\d{4})/);
    if (m) return `${m[3]}-${String(m[2]).padStart(2, '0')}-${String(m[1]).padStart(2, '0')}`;
    return '';
  }

  function _clearFilters() {
    _filters.done = false; _filters.from = ''; _filters.to = ''; _filters.assign = '';
    if (_el('todo-filter-done')) _el('todo-filter-done').checked = false;
    if (_el('todo-filter-from')) _el('todo-filter-from').value = '';
    if (_el('todo-filter-to')) _el('todo-filter-to').value = '';
    if (_el('todo-filter-assign')) _el('todo-filter-assign').value = '';
    _applyItemFilters();
  }

  function _populateGraphPerson(projects) {
    const sel = _el('todo-graph-person'); if (!sel) return;
    const names = Array.from(new Set((projects || []).flatMap(p => (p.items || []).flatMap(it => it.assignees || [])).map(s => s.trim()).filter(Boolean))).sort();
    const cur = sel.value || _graphPerson;
    sel.innerHTML = '<option value="">alle</option>' + names.map(n => `<option value="${escHtml(n)}">${escHtml(n)}</option>`).join('');
    sel.value = names.includes(cur) ? cur : '';
    _graphPerson = sel.value;
  }

  function _newId() { return 'i' + Math.random().toString(36).slice(2, 11); }
  function _addItem() {
    if (!_data) return;
    (_data.items = _data.items || []).push({ id: _newId(), text: '', assignees: [], status: 'offen', due: '', attachments: [] });
    _renderItems();
  }

  async function _reorder(itemId, dir) {
    await _save();  // aktuelle Änderungen sichern, dann serverseitig tauschen
    await _api('POST', `/api/todo/items/${encodeURIComponent(itemId)}/reorder`, { direction: dir });
    await _open(_pid);
  }

  async function _moveItem(itemId, targetPid) {
    await _save();
    await _api('POST', `/api/todo/items/${encodeURIComponent(itemId)}/move`, { project_id: targetPid });
    await _loadTree();
    await _open(_pid);
    _status('Punkt verschoben.');
    setTimeout(() => _status(''), 1200);
  }

  // ── Anlagen ────────────────────────────────────────────────────────────────
  function _pickAttachment(itemId) {
    _attachTarget = itemId;
    if (!_fileInput) {
      _fileInput = document.createElement('input');
      _fileInput.type = 'file'; _fileInput.style.display = 'none';
      _fileInput.addEventListener('change', _uploadAttachment);
      document.body.appendChild(_fileInput);
    }
    _fileInput.value = ''; _fileInput.click();
  }

  async function _uploadAttachment() {
    const f = _fileInput.files && _fileInput.files[0];
    if (!f || !_attachTarget) return;
    _spin(true); _status('Anlage wird gelesen…');
    try {
      await _save();  // Punkt muss in der DB existieren
      const fd = new FormData(); fd.append('file', f);
      const r = await fetch(`/api/todo/items/${encodeURIComponent(_attachTarget)}/attach`, { method: 'POST', body: fd });
      if (!r.ok) { let m = 'HTTP ' + r.status; try { m = (await r.json()).detail || m; } catch (_) {} throw new Error(m); }
      await _open(_pid);
      _status(`📎 „${f.name}" angehängt (als Markdown gespeichert)`);
    } catch (e) { _status('Anlage-Fehler: ' + e.message); }
    finally { _spin(false); _attachTarget = null; }
  }

  async function _deleteAttachment(attId) {
    if (!confirm('Anlage entfernen?')) return;
    await _api('DELETE', `/api/todo/attachment/${encodeURIComponent(attId)}`);
    await _open(_pid);
  }

  // ── KI-Helfer ──────────────────────────────────────────────────────────────
  async function _extract() {
    const text = (_el('todo-note').value || '').trim();
    if (!text) { _status('Bitte eine Notiz eingeben.'); return; }
    if (!_data) { _status('Erst ein Projekt öffnen/anlegen.'); return; }
    _collect();
    _spin(true); _status('KI leitet Punkte ab…');
    try {
      const res = await _api('POST', '/api/todo/extract', {
        text, participants: _data.participants, title: _data.title, date: _data.date, model: _model(),
      });
      _tok(res.tokens);
      if (_el('todo-extract-replace').checked) { _data.items = []; _data.edges = []; }
      (res.items || []).forEach(it => _data.items.push(it));
      (res.edges || []).forEach(e => {
        if (!(_data.edges || []).some(x => x.source === e.source && x.target === e.target)) (_data.edges = _data.edges || []).push(e);
      });
      _el('todo-note').value = '';
      _renderItems(); _markGraphStale();
      _status(`✓ ${(res.items || []).length} Punkte, ${(res.edges || []).length} Verknüpfungen — 💾 Speichern nicht vergessen`);
    } catch (e) { _status('KI-Fehler: ' + e.message); }
    finally { _spin(false); }
  }

  async function _suggestLinks() {
    if (!_data || !(_data.items || []).length) return;
    _spin(true); _status('KI sucht Verknüpfungen…');
    try {
      const res = await _api('POST', '/api/todo/suggest-links', { items: _data.items, model: _model() });
      _tok(res.tokens);
      let added = 0;
      (res.edges || []).forEach(e => {
        if (!(_data.edges || []).some(x => x.source === e.source && x.target === e.target)) { (_data.edges = _data.edges || []).push(e); added++; }
      });
      _renderItems(); _markGraphStale();
      _status(`✓ ${added} neue Verknüpfungen`);
    } catch (e) { _status('KI-Fehler: ' + e.message); }
    finally { _spin(false); }
  }

  async function _next() {
    if (!_pid) return;
    _spin(true); _status('KI…');
    try {
      const res = await _api('POST', '/api/todo/next', { pid: _pid, model: _model() });
      _tok(res.tokens);
      _el('todo-next-out').innerHTML = (typeof marked !== 'undefined') ? marked.parse(res.text || '') : escHtml(res.text || '');
      _status('');
    } catch (e) { _status('KI-Fehler: ' + e.message); }
    finally { _spin(false); }
  }

  // ── Suche (Scope = aktives Projekt) ────────────────────────────────────────
  function _clearSearch() { const b = _el('todo-search-results'); if (b) { b.style.display = 'none'; b.innerHTML = ''; } const i = _el('todo-search'); if (i) i.value = ''; }
  function _searchSoon() { clearTimeout(_searchTimer); _searchTimer = setTimeout(_runSearch, 300); }

  async function _runSearch() {
    const q = (_el('todo-search').value || '').trim();
    const box = _el('todo-search-results');
    if (!q) { box.style.display = 'none'; box.innerHTML = ''; return; }
    try {
      const res = await _api('GET', `/api/todo/search?q=${encodeURIComponent(q)}&root=${encodeURIComponent(_active)}`);
      const rows = res.results || [];
      const scopeName = _active === 'root' ? 'alle Projekte' : (_nodeName(_flat.find(p => p.id === _active) || {}) + ' + Unterprojekte');
      box.style.display = 'block';
      if (!rows.length) { box.innerHTML = `<div class="planner-muted" style="padding:8px">Keine Treffer für „${escHtml(q)}" (${escHtml(scopeName)}).</div>`; return; }
      box.innerHTML = `<div class="todo-search-head">🔍 ${rows.length} Treffer für „${escHtml(q)}" — ${escHtml(scopeName)}</div>` +
        rows.map(r => {
          const badge = r.source === 'attachment' ? '📄 Anlage' : '📝 Punkt';
          const done = r.status === 'erledigt' ? ' ✅' : '';
          const snip = r.attachment && r.attachment.snippet ? `<div class="todo-search-snip">…${escHtml(r.attachment.snippet)}…</div>` : '';
          return `<div class="todo-search-row" data-project="${escHtml(r.project)}" data-item="${escHtml(r.item_id)}">
            <span class="todo-search-badge">${badge}</span><span class="todo-search-proj">${escHtml(r.project_title || r.project)}</span>
            <span class="todo-search-text">${escHtml(r.text)}${done}</span>${snip}</div>`;
        }).join('');
      box.querySelectorAll('.todo-search-row').forEach(row => row.addEventListener('click', async () => {
        await _open(row.dataset.project); _clearSearch();
        setTimeout(() => { const el = document.querySelector(`.todo-item[data-id="${row.dataset.item}"]`); if (el) { el.scrollIntoView({ block: 'center' }); el.classList.add('todo-flash'); setTimeout(() => el.classList.remove('todo-flash'), 1600); } }, 200);
      }));
    } catch (e) { box.style.display = 'block'; box.innerHTML = `<div class="planner-muted" style="padding:8px">Suchfehler: ${escHtml(e.message)}</div>`; }
  }

  // ── Wissensgraph (Scope = aktives Projekt bzw. alle) ───────────────────────
  function _markGraphStale() { const b = _el('btn-todo-graph-build'); if (b) b.classList.add('todo-stale'); }

  function _graphElements(projects) {
    const nodes = [], edges = [], valid = new Set();
    const multi = projects.length > 1;
    const nid = (proj, id) => (multi ? proj + '::' + id : id);
    projects.forEach((pr, ix) => {
      const color = multi ? PROJ_COLORS[ix % PROJ_COLORS.length] : null;
      (pr.items || []).forEach(it => {
        const id = nid(pr.name, it.id); valid.add(id);
        nodes.push({ data: { id, kind: 'item', label: it.text || '(ohne Titel)', color: STATUS[it.status]?.color || '#9ca3af', border: color, project: pr.name } });
      });
    });
    if (_showHubs) {
      const hubs = new Map();
      projects.forEach(pr => (pr.items || []).forEach(it => {
        (it.assignees || []).forEach(a => {
          const hid = 'p::' + a;
          if (!hubs.has(hid)) hubs.set(hid, { label: a, color: '#a78bfa', items: [] });
          hubs.get(hid).items.push(nid(pr.name, it.id));
        });
        if (!multi) {
          const hid = 's::' + it.status;
          if (!hubs.has(hid)) hubs.set(hid, { label: it.status, color: STATUS[it.status]?.color, items: [] });
          hubs.get(hid).items.push(nid(pr.name, it.id));
        }
      }));
      hubs.forEach((meta, hid) => {
        valid.add(hid);
        nodes.push({ data: { id: hid, kind: 'hub', label: meta.label, color: meta.color } });
        meta.items.forEach(iid => edges.push({ data: { id: 'h_' + hid + '__' + iid, source: iid, target: hid, kind: 'hub', color: meta.color } }));
      });
    }
    projects.forEach(pr => (pr.edges || []).forEach(e => {
      const s = nid(pr.name, e.source), t = nid(pr.name, e.target);
      if (valid.has(s) && valid.has(t)) edges.push({ data: { id: s + '__' + t, source: s, target: t, label: e.label || '', kind: 'link' } });
    }));
    return { nodes, edges, multi };
  }

  function _graphStyle() {
    const css = getComputedStyle(document.documentElement);
    const text = (css.getPropertyValue('--text') || '#e8e8e8').trim();
    const border = (css.getPropertyValue('--border') || '#3a3a3a').trim();
    const bg = (css.getPropertyValue('--bg-hover') || '#2a2a2a').trim();
    return [
      { selector: 'node', style: { 'background-color': bg, 'border-color': 'data(color)', 'border-width': 3, 'label': 'data(label)', 'color': text, 'font-size': 11, 'text-wrap': 'wrap', 'text-max-width': 130, 'text-valign': 'center', 'text-halign': 'center', 'width': 'label', 'height': 'label', 'padding': 8, 'shape': 'round-rectangle' } },
      { selector: 'node[border]', style: { 'border-color': 'data(border)', 'border-width': 4 } },
      { selector: 'node[kind="hub"]', style: { 'background-color': 'data(color)', 'border-width': 0, 'shape': 'round-tag', 'color': '#0b0b0b', 'font-size': 10, 'font-weight': 'bold', 'padding': 6, 'text-outline-width': 1.5, 'text-outline-color': '#ffffff' } },
      { selector: 'node.sel', style: { 'border-color': '#22c55e', 'border-width': 5 } },
      { selector: 'edge', style: { 'width': 2, 'line-color': border, 'target-arrow-color': border, 'target-arrow-shape': 'triangle', 'curve-style': 'bezier', 'label': 'data(label)', 'font-size': 9, 'color': text, 'text-background-color': bg, 'text-background-opacity': 0.85, 'text-background-padding': 2 } },
      { selector: 'edge[kind="hub"]', style: { 'width': 1.5, 'line-color': 'data(color)', 'line-opacity': 0.55, 'target-arrow-shape': 'none', 'curve-style': 'haystack', 'label': '' } },
    ];
  }

  async function _buildGraph() {
    const host = _el('todo-graph');
    if (!host || typeof cytoscape === 'undefined') { _status('Graph-Bibliothek nicht geladen.'); return; }
    const root = _forceAll ? 'root' : _active;
    _spin(true);
    let g;
    try { g = await _api('GET', `/api/todo/graph?root=${encodeURIComponent(root)}`); }
    catch (e) { _status('Graph-Fehler: ' + e.message); _spin(false); return; }
    _spin(false);
    // Personen-Dropdown des Graphen befüllen (aus allen Zuständigen im Bereich)
    _populateGraphPerson(g.projects || []);
    let projects = (g.projects || []).filter(p => (p.items || []).length);
    // Personenfilter: nur Punkte der gewählten Person (samt deren Kanten)
    if (_graphPerson) {
      const pl = _graphPerson.toLowerCase();
      projects = projects.map(p => {
        const items = (p.items || []).filter(it => (it.assignees || []).some(a => a.toLowerCase() === pl));
        const ids = new Set(items.map(it => it.id));
        const edges = (p.edges || []).filter(e => ids.has(e.source) && ids.has(e.target));
        return { ...p, items, edges };
      }).filter(p => p.items.length);
    }
    const nItems = projects.reduce((s, p) => s + (p.items || []).length, 0);

    // 3D-Kugel: WebGL-freies Canvas-Rendering, rAF-basiert → blockiert nicht. Verträgt
    // deutlich mehr Knoten als das synchrone 2D-Layout.
    if (_graphMode === '3d') {
      _updateGraphModeUI();
      const LIMIT3D = 1500;
      if (nItems > LIMIT3D) {
        _el('todo-graph-hint').textContent = `${nItems} Punkte – bitte Einzelprojekt (⚡) oder 👤 Personenfilter.`;
        return;
      }
      _graph3d.build(projects);
      _el('btn-todo-graph-build').classList.remove('todo-stale');
      _updateHint(projects, projects.length > 1);
      return;
    }

    // 2D (Cytoscape): Schutz vor dem Einfrieren des Browsers – sehr viele Knoten legen
    // das synchrone Layout lahm. Ab einem Schwellwert erst nach Rückfrage bauen und die
    // 3D-Kugel bzw. ein Einzelprojekt (⚡) empfehlen.
    _updateGraphModeUI();
    const LIMIT = 300;
    if (nItems > LIMIT) {
      const hint = _el('todo-graph-hint');
      if (hint) hint.textContent = `${nItems} Punkte im Bereich – für den 2D-Graphen zu viel.`;
      host.innerHTML = `<div class="dir-hint" style="margin:12px">
        <strong>${nItems} Punkte</strong> im aktiven Bereich – ein 2D-Graph mit so vielen Knoten
        kann den Browser einfrieren. Nutze die <strong>🔮 3D-Kugel</strong> (läuft flüssig),
        aktiviere links ein <strong>Einzelprojekt (⚡)</strong> oder den <strong>👤 Personenfilter</strong>.
        <div style="margin-top:10px"><button id="todo-graph-force" class="export-btn">Trotzdem als 2D aufbauen (${nItems})</button></div>
      </div>`;
      const fb = _el('todo-graph-force');
      if (fb) fb.addEventListener('click', () => { host.innerHTML = ''; _buildGraphForce(projects); });
      return;
    }
    _buildGraphForce(projects);
  }

  function _buildGraphForce(projects) {
    const host = _el('todo-graph');
    if (_cy) { _cy.destroy(); _cy = null; }
    const { nodes, edges, multi } = _graphElements(projects);
    _cy = cytoscape({
      container: host, elements: [...nodes, ...edges], style: _graphStyle(),
      wheelSensitivity: 0.2, minZoom: 0.2, maxZoom: 3,
      // animate:true → das cose-Layout rechnet iterativ und gibt dem Browser zwischen
      // den Schritten Kontrolle zurück (kein „Seite reagiert nicht" bei größeren Graphen).
      layout: { name: 'cose', animate: true, animationThreshold: 60, numIter: 700,
                padding: 30, nodeRepulsion: 9000, idealEdgeLength: 130 },
    });
    if (!multi) {
      _cy.on('tap', 'node', evt => _onNodeTap(evt.target));
      _cy.on('tap', 'edge', evt => _onEdgeTap(evt.target));
    }
    _cy.one('layoutstop', () => setTimeout(() => { _cy.resize(); _cy.fit(undefined, 30); }, 20));
    _el('btn-todo-graph-build').classList.remove('todo-stale');
    _updateHint(projects, multi);
  }

  function _layout() {
    if (_graphMode === '3d') { _graph3d.relayout(); return; }
    if (_cy) { _cy.layout({ name: 'cose', animate: true, animationThreshold: 60, numIter: 700, padding: 30, nodeRepulsion: 9000, idealEdgeLength: 130 }).run(); }
    else _buildGraph();
  }

  // Sichtbarkeit von 2D-Container vs. 3D-Canvas + Toggle-Buttons je Modus.
  function _updateGraphModeUI() {
    const is3d = _graphMode === '3d';
    _el('todo-graph').style.display = is3d ? 'none' : 'block';
    _el('todo-graph3d-wrap').style.display = is3d ? 'block' : 'none';
    const b2 = _el('btn-todo-graph-2d'), b3 = _el('btn-todo-graph-3d');
    if (b2) b2.classList.toggle('active', !is3d);
    if (b3) b3.classList.toggle('active', is3d);
    // Verbinden/Anordnen sind 2D-spezifisch
    const c = _el('btn-todo-graph-connect'); if (c) c.style.display = is3d ? 'none' : '';
  }

  function _setGraphMode(mode) {
    if (mode === _graphMode) return;
    _graphMode = mode;
    try { localStorage.setItem(GRAPH_MODE_KEY, mode); } catch (_) {}
    if (mode !== '3d') _graph3d.stop();
    _updateGraphModeUI();
    if (_view === 'graph') _buildGraph();
  }

  // ── 3D-Kugel-Wissensgraph (eigenes leichtes Canvas-3D, keine Fremd-Dependency) ──
  // Knoten werden auf einer Fibonacci-Kugel verteilt, per Perspektive projiziert und
  // tiefensortiert gezeichnet; Drehung/Zoom/Klick über Pointer-Events. Die Zeichen-
  // schleife läuft über requestAnimationFrame (nicht blockierend).
  const _graph3d = (() => {
    let canvas, ctx, raf = null, pts = [], links = [];
    let rotX = -0.4, rotY = 0.6, autoRot = true, zoom = 1;
    let dragging = false, lastX = 0, lastY = 0, moved = false;
    let hoverIdx = -1, wired = false, R = 220;

    function _ensure() {
      canvas = _el('todo-graph3d');
      if (!canvas) return false;
      ctx = canvas.getContext('2d');
      if (!wired) { _wire(); wired = true; }
      return true;
    }

    function _resize() {
      if (!canvas) return;
      const wrap = _el('todo-graph3d-wrap');
      const w = wrap.clientWidth || 600, h = wrap.clientHeight || 460;
      const dpr = window.devicePixelRatio || 1;
      canvas.width = w * dpr; canvas.height = h * dpr;
      canvas.style.width = w + 'px'; canvas.style.height = h + 'px';
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      R = Math.min(w, h) * 0.42;
    }

    // Rohknoten aus denselben Elementen wie der 2D-Graph, auf die Kugel verteilen.
    function build(projects) {
      if (!_ensure()) return;
      const { nodes, edges } = _graphElements(projects);
      const idx = {}; pts = [];
      const N = nodes.length || 1;
      const golden = Math.PI * (3 - Math.sqrt(5));
      nodes.forEach((n, i) => {
        const y = 1 - (i / Math.max(1, N - 1)) * 2;          // 1 … -1
        const r = Math.sqrt(Math.max(0, 1 - y * y));
        const th = golden * i;
        const isHub = n.data.kind === 'hub';
        idx[n.data.id] = pts.length;
        pts.push({
          id: n.data.id, label: n.data.label || '', kind: n.data.kind,
          color: n.data.color || '#9ca3af', border: n.data.border || null,
          // Hubs etwas nach innen (0.72·R) → wirken als Zentren
          bx: Math.cos(th) * r * (isHub ? 0.72 : 1),
          by: y * (isHub ? 0.72 : 1),
          bz: Math.sin(th) * r * (isHub ? 0.72 : 1),
          rad: isHub ? 7 : 5,
        });
      });
      links = [];
      edges.forEach(e => {
        const s = idx[e.data.source], t = idx[e.data.target];
        if (s != null && t != null) links.push({ s, t, hub: e.data.kind === 'hub', color: e.data.color || null });
      });
      _resize();
      autoRot = true;
      start();
    }

    function _project(p) {
      // Rotation um Y dann X
      const cy = Math.cos(rotY), sy = Math.sin(rotY);
      const cx = Math.cos(rotX), sx = Math.sin(rotX);
      let x = p.bx * cy - p.bz * sy;
      let z = p.bx * sy + p.bz * cy;
      let y = p.by * cx - z * sx;
      z = p.by * sx + z * cx;
      const persp = 520 / (520 + z * R * 1.4);
      const w = canvas.clientWidth || 600, h = canvas.clientHeight || 460;
      return { x: w / 2 + x * R * zoom * persp, y: h / 2 + y * R * zoom * persp, z, scale: persp * zoom };
    }

    function _draw() {
      if (!ctx) return;
      const w = canvas.clientWidth || 600, h = canvas.clientHeight || 460;
      ctx.clearRect(0, 0, w, h);
      const proj = pts.map(_project);
      // Kanten zuerst (hinten → vorne über mittlere Tiefe)
      links.forEach(l => {
        const a = proj[l.s], b = proj[l.t]; if (!a || !b) return;
        const depth = (a.z + b.z) / 2;
        ctx.strokeStyle = l.hub ? _rgba(l.color || '#a78bfa', 0.18 + 0.12 * (1 - depth))
                                : _rgba('#8892a0', 0.22 + 0.18 * (1 - depth));
        ctx.lineWidth = l.hub ? 0.8 : 1.1;
        ctx.beginPath(); ctx.moveTo(a.x, a.y); ctx.lineTo(b.x, b.y); ctx.stroke();
      });
      // Knoten tiefensortiert (hinten zuerst)
      const order = pts.map((_, i) => i).sort((i, j) => proj[i].z - proj[j].z);
      order.forEach(i => {
        const p = pts[i], pr = proj[i];
        const rad = Math.max(1.5, p.rad * pr.scale);
        const fade = 0.45 + 0.55 * (1 - (pr.z + 1) / 2);   // vorne heller
        ctx.beginPath(); ctx.arc(pr.x, pr.y, rad, 0, Math.PI * 2);
        ctx.fillStyle = _rgba(p.color, fade);
        ctx.fill();
        if (p.border) { ctx.lineWidth = 1.5; ctx.strokeStyle = _rgba(p.border, fade); ctx.stroke(); }
        if (i === hoverIdx || p.kind === 'hub') {
          const css = getComputedStyle(document.documentElement);
          ctx.fillStyle = (css.getPropertyValue('--text') || '#e8e8e8').trim();
          ctx.font = (p.kind === 'hub' ? 'bold ' : '') + Math.round(11 * Math.min(1.4, pr.scale)) + 'px sans-serif';
          ctx.textAlign = 'center';
          const lbl = p.label.length > 28 ? p.label.slice(0, 27) + '…' : p.label;
          ctx.fillText(lbl, pr.x, pr.y - rad - 3);
        }
      });
    }

    function _loop() {
      if (autoRot && !dragging) rotY += 0.0035;
      _draw();
      raf = requestAnimationFrame(_loop);
    }

    function start() { stop(); _resize(); raf = requestAnimationFrame(_loop); }
    function stop() { if (raf) { cancelAnimationFrame(raf); raf = null; } }
    function relayout() { rotX = -0.4; rotY = 0.6; zoom = 1; autoRot = true; }

    function _hit(mx, my) {
      let best = -1, bd = 14 * 14;
      pts.forEach((p, i) => {
        const pr = _project(p);
        const d = (pr.x - mx) ** 2 + (pr.y - my) ** 2;
        if (d < bd) { bd = d; best = i; }
      });
      return best;
    }

    function _wire() {
      canvas.addEventListener('pointerdown', e => { dragging = true; moved = false; lastX = e.offsetX; lastY = e.offsetY; canvas.setPointerCapture(e.pointerId); });
      canvas.addEventListener('pointermove', e => {
        if (dragging) {
          const dx = e.offsetX - lastX, dy = e.offsetY - lastY;
          if (Math.abs(dx) + Math.abs(dy) > 2) moved = true;
          rotY += dx * 0.008; rotX += dy * 0.008;
          rotX = Math.max(-1.5, Math.min(1.5, rotX));
          lastX = e.offsetX; lastY = e.offsetY;
        } else {
          const h = _hit(e.offsetX, e.offsetY);
          if (h !== hoverIdx) { hoverIdx = h; canvas.style.cursor = h >= 0 ? 'pointer' : 'grab'; }
        }
      });
      canvas.addEventListener('pointerup', e => {
        dragging = false;
        if (!moved) {
          const h = _hit(e.offsetX, e.offsetY);
          if (h >= 0) { const it = _itemById(pts[h].id.split('::').pop()); if (it) _status(`${it.status === 'erledigt' ? '✅' : '🔲'} ${it.text}` + (it.assignees?.length ? ' · ' + it.assignees.join(', ') : '') + (it.due ? ' · Frist ' + it.due : '')); else _status('👥 ' + pts[h].label); }
        }
      });
      canvas.addEventListener('dblclick', () => { autoRot = !autoRot; });
      canvas.addEventListener('wheel', e => { e.preventDefault(); zoom = Math.max(0.4, Math.min(3, zoom * (e.deltaY < 0 ? 1.1 : 0.9))); }, { passive: false });
    }

    function _rgba(hex, a) {
      const m = /^#?([a-f\d]{2})([a-f\d]{2})([a-f\d]{2})$/i.exec(hex || '');
      if (!m) return hex || `rgba(150,150,150,${a})`;
      return `rgba(${parseInt(m[1], 16)},${parseInt(m[2], 16)},${parseInt(m[3], 16)},${a})`;
    }

    return { build, stop, relayout, resize: _resize };
  })();

  function _onNodeTap(node) {
    if (node.data('kind') !== 'item') return;
    if (_connectMode) {
      if (!_connectFrom) { _connectFrom = node.id(); node.addClass('sel'); _updateHint(); return; }
      if (_connectFrom === node.id()) { node.removeClass('sel'); _connectFrom = null; _updateHint(); return; }
      const label = prompt('Verknüpfung (z. B. blockiert, gehört zu):', 'verknüpft') || '';
      if (!(_data.edges || []).some(e => e.source === _connectFrom && e.target === node.id())) (_data.edges = _data.edges || []).push({ source: _connectFrom, target: node.id(), label: label.trim() });
      _cy.$('.sel').removeClass('sel'); _connectFrom = null; _save().then(() => { _buildGraph(); _renderItems(); });
      return;
    }
    const it = _itemById(node.id());
    if (it) _status(`${it.status === 'erledigt' ? '✅' : '🔲'} ${it.text}` + (it.assignees?.length ? ' · ' + it.assignees.join(', ') : '') + (it.due ? ' · Frist ' + it.due : ''));
  }

  function _onEdgeTap(edge) {
    if (edge.data('kind') !== 'link' || !_data) return;
    const s = edge.data('source'), t = edge.data('target');
    const cur = (_data.edges || []).find(e => e.source === s && e.target === t);
    if (!cur) return;
    const nl = prompt('Verknüpfung umbenennen (leer = löschen):', cur.label || '');
    if (nl === null) return;
    if (nl.trim() === '') _data.edges = _data.edges.filter(e => !(e.source === s && e.target === t)); else cur.label = nl.trim();
    _save().then(() => { _buildGraph(); _renderItems(); });
  }

  function _toggleConnect() {
    if (_forceAll || _active !== _pid) { _status('Verbinden nur im geöffneten Einzelprojekt (Scope = dieses Projekt).'); }
    _connectMode = !_connectMode; _connectFrom = null;
    if (_cy) _cy.$('.sel').removeClass('sel');
    _el('btn-todo-graph-connect').classList.toggle('active', _connectMode);
    _updateHint();
  }

  function _updateHint(projects, multi) {
    const el = _el('todo-graph-hint'); if (!el) return;
    if (_connectMode) { el.textContent = _connectFrom ? 'Zielaufgabe anklicken…' : 'Startaufgabe anklicken…'; return; }
    if (projects) {
      const items = projects.reduce((s, p) => s + (p.items || []).length, 0);
      el.textContent = multi ? `${projects.length} Projekte · ${items} Punkte (verbunden)` : `${items} Punkte`;
    }
  }

  // ── Empfehlung / Agenda (deterministisch) ──────────────────────────────────
  function _dueBadge(r) {
    if (r.days === null || r.days === undefined) return '';
    if (r.days < 0) return `<span class="todo-badge overdue">überfällig (${-r.days} T)</span>`;
    if (r.days === 0) return '<span class="todo-badge soon">heute fällig</span>';
    if (r.days <= 10) return `<span class="todo-badge soon">in ${r.days} T</span>`;
    return `<span class="todo-badge">${escHtml(r.due)}</span>`;
  }

  function _agendaSection(title, rows, ranked) {
    if (!rows.length) return '';
    let h = `<div class="todo-agenda-head">${title} <span class="planner-muted">(${rows.length})</span></div>`;
    rows.forEach((r, i) => {
      const badges = [];
      if (r.status === 'laeuft') badges.push('<span class="todo-badge run">läuft</span>');
      const db = _dueBadge(r); if (db) badges.push(db);
      if (r.unblocks > 0) badges.push(`<span class="todo-badge unblock">entblockt ${r.unblocks}</span>`);
      const who = (r.assignees || []).length ? ` · 👤 ${escHtml(r.assignees.join(', '))}` : '';
      const wait = (r.blockers && r.blockers.length) ? `<div class="todo-agenda-wait">wartet auf: ${r.blockers.map(escHtml).join(', ')}</div>` : '';
      h += `<div class="todo-agenda-row" data-project="${escHtml(r.project)}" data-item="${escHtml(r.id)}" title="Zum Punkt springen">
        <span class="todo-agenda-rank">${ranked ? (i + 1) : '⛔'}</span>
        <div class="todo-agenda-main">
          <div class="todo-agenda-text">${escHtml(r.text)}</div>
          <div class="todo-agenda-meta">${escHtml(r.project_title)}${who} ${badges.join(' ')}</div>${wait}
        </div></div>`;
    });
    return h;
  }

  async function _loadAgenda() {
    const out = _el('todo-agenda-out');
    const person = _el('todo-agenda-person').value || '';
    out.innerHTML = '<span class="planner-muted">Berechne Empfehlung…</span>';
    try {
      const a = await _api('GET', `/api/todo/agenda?root=${encodeURIComponent(_active)}&person=${encodeURIComponent(person)}`);
      const sel = _el('todo-agenda-person'); const cur = sel.value;
      sel.innerHTML = '<option value="">alle</option>' + (a.persons || []).map(p => `<option value="${escHtml(p)}">${escHtml(p)}</option>`).join('');
      sel.value = cur;
      const scopeName = _active === 'root' ? 'alle Projekte' : (_nodeName(_flat.find(p => p.id === _active) || {}) + ' + Unterprojekte');
      let html = `<div class="planner-muted" style="font-size:11.5px;margin-bottom:8px">Bereich: <strong>${escHtml(scopeName)}</strong>${person ? ` · für <strong>${escHtml(person)}</strong>` : ' · für alle'}</div>`;
      if (!a.jetzt.length && !a.demnaechst.length && !a.blocked.length) {
        out.innerHTML = html + '<div class="dir-hint">Nichts Offenes im aktiven Bereich – alles erledigt oder leer. 🎉</div>';
        return;
      }
      html += _agendaSection('🔥 Jetzt dran', a.jetzt, true);
      html += _agendaSection('🕒 Demnächst', a.demnaechst, true);
      html += _agendaSection('⛔ Blockiert – erst Vorarbeit erledigen', a.blocked, false);
      out.innerHTML = html;
      out.querySelectorAll('.todo-agenda-row').forEach(row => row.addEventListener('click', () => _jumpTo(row.dataset.project, row.dataset.item)));
    } catch (e) { out.innerHTML = `<span class="planner-muted">Fehler: ${escHtml(e.message)}</span>`; }
  }

  async function _jumpTo(project, item) {
    await _open(project);
    _view = 'liste'; _showView('liste');
    setTimeout(() => { const el = document.querySelector(`.todo-item[data-id="${item}"]`); if (el) { el.scrollIntoView({ block: 'center' }); el.classList.add('todo-flash'); setTimeout(() => el.classList.remove('todo-flash'), 1600); } }, 200);
  }

  // ── Daten-Chat: über den (aktiven) Bestand sprechen, auch Fragen zu Kollegen ─
  function _scopeLabel() {
    if (!_active || _active === 'root') return '(gesamter Bestand)';
    const p = _flat.find(x => x.id === _active);
    return p ? `(Bereich: ${p.title || p.name})` : '';
  }

  async function _ask() {
    const inp = _el('todo-ask-input');
    const q = (inp.value || '').trim();
    if (!q) { inp.focus(); return; }
    const box = _el('todo-ask-answer');
    const out = _el('todo-ask-out');
    const btn = _el('todo-ask-go');
    _el('todo-ask-scope').textContent = _scopeLabel();
    box.style.display = '';
    out.innerHTML = '<span class="pf-spin"></span> Werte die To-Do-Daten aus… <span class="planner-muted">(lokale Modelle brauchen dafür etwas Zeit; ein aktiviertes Einzelprojekt ⚡ ist schneller)</span>';
    btn.disabled = true; _spin(true);
    try {
      const res = await _api('POST', '/api/todo/ask', { question: q, root: _active, model: _model() });
      _tok(res.tokens);
      out.innerHTML = (typeof marked !== 'undefined')
        ? marked.parse(res.answer || '') : _escapeHtml(res.answer || '');
    } catch (e) {
      out.innerHTML = '<span style="color:var(--danger,#ef4444)">Fehler: ' + _escapeHtml(e.message) + '</span>';
    } finally {
      btn.disabled = false; _spin(false);
    }
  }

  function _escapeHtml(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, c => (
      { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  }

  // ── Vertikaler Splitter (Projektbaum ↔ Inhalt) ─────────────────────────────
  const TREEW_KEY = 'ai_framework_thomas_todo_treew';
  function _initSplitter() {
    const sp = _el('todo-tree-splitter'), main = _el('todo-main'), pane = _el('todo-tree-pane');
    if (!sp || !main || !pane) return;
    try { const w = localStorage.getItem(TREEW_KEY); if (w) pane.style.width = parseInt(w, 10) + 'px'; } catch (_) {}
    let drag = false;
    const mv = e => {
      if (!drag) return;
      const r = main.getBoundingClientRect();
      let w = Math.max(180, Math.min(r.width - 320, e.clientX - r.left));
      pane.style.width = w + 'px';
    };
    sp.addEventListener('mousedown', e => { drag = true; e.preventDefault(); document.body.style.userSelect = 'none'; sp.classList.add('dragging'); });
    window.addEventListener('mousemove', mv);
    window.addEventListener('mouseup', () => {
      if (!drag) return;
      drag = false; document.body.style.userSelect = ''; sp.classList.remove('dragging');
      try { localStorage.setItem(TREEW_KEY, parseInt(pane.style.width, 10)); } catch (_) {}
      if (_cy) _cy.resize();
    });
  }

  // ── Einklappbare + höhenverstellbare Abschnitte im Liste-Untertab ──────────
  function _initVStack() {
    document.querySelectorAll('.todo-sec-toggle').forEach(btn =>
      btn.addEventListener('click', () => _toggleSec(btn.dataset.sec)));
    document.querySelectorAll('.todo-hsplit').forEach(sp =>
      sp.addEventListener('mousedown', e => _startVDrag(e, sp, 'todo-sec-' + sp.dataset.split)));
    _applyLayout();
  }

  // Verstellt die Höhe des Abschnitts ÜBER dem Splitter (nicht des Rumpfes) und begrenzt
  // sie so, dass „Punkte" und die übrigen Abschnitte ihren Mindestplatz behalten → kein
  // Überlappen; der Rumpf (inkl. Notiz-Eingabefeld) füllt den Abschnitt via CSS flex:1.
  function _startVDrag(e, sp, secId) {
    const sec = _el(secId); if (!sec) return;
    const view = _el('todo-view-liste');
    e.preventDefault(); document.body.style.userSelect = 'none'; sp.classList.add('dragging');
    const startY = e.clientY, startH = sec.getBoundingClientRect().height;
    const headH = (sec.querySelector('.var-sec-head') || {}).offsetHeight || 28;
    const grow = view.querySelector('.todo-sec-grow');
    const mv = ev => {
      // reservierter Platz: sichtbare Splitter + andere Abschnitte (Mindesthöhe) + „Punkte"
      let reserved = 0;
      view.querySelectorAll('.todo-hsplit:not(.hidden)').forEach(s => { reserved += s.offsetHeight; });
      view.querySelectorAll('.todo-sec').forEach(s => {
        if (s === sec) return;
        if (s === grow) { reserved += 72; return; }
        const hh = (s.querySelector('.var-sec-head') || {}).offsetHeight || 28;
        reserved += s.classList.contains('collapsed') ? hh : Math.max(hh + 8, s.getBoundingClientRect().height);
      });
      const maxH = Math.max(headH + 12, view.clientHeight - reserved);
      const h = Math.max(headH + 12, Math.min(maxH, startH + (ev.clientY - startY)));
      sec.style.flex = '0 0 auto'; sec.style.height = h + 'px';
    };
    const up = () => {
      window.removeEventListener('mousemove', mv); window.removeEventListener('mouseup', up);
      document.body.style.userSelect = ''; sp.classList.remove('dragging'); _saveLayout();
    };
    window.addEventListener('mousemove', mv); window.addEventListener('mouseup', up);
  }

  function _toggleSec(sec) {
    const el = _el('todo-sec-' + sec); if (!el) return;
    el.classList.toggle('collapsed');
    _updateSplitVisibility(); _saveLayout();
  }

  function _updateSplitVisibility() {
    document.querySelectorAll('.todo-hsplit').forEach(sp => {
      const sec = _el('todo-sec-' + sp.dataset.split);
      sp.classList.toggle('hidden', !!(sec && sec.classList.contains('collapsed')));
    });
  }

  function _saveLayout() {
    const st = { collapsed: {}, heights: {} };
    ['header', 'note', 'items'].forEach(s => { const el = _el('todo-sec-' + s); if (el && el.classList.contains('collapsed')) st.collapsed[s] = true; });
    ['header', 'note'].forEach(s => { const el = _el('todo-sec-' + s); if (el && el.style.height) st.heights[s] = el.style.height; });
    try { localStorage.setItem(LAYOUT_KEY, JSON.stringify(st)); } catch (_) {}
  }

  function _applyLayout() {
    let st = {}; try { st = JSON.parse(localStorage.getItem(LAYOUT_KEY) || '{}'); } catch (_) {}
    ['header', 'note'].forEach(s => {
      const el = _el('todo-sec-' + s);
      if (el && st.heights && st.heights[s]) { el.style.flex = '0 0 auto'; el.style.height = st.heights[s]; }
    });
    ['header', 'note', 'items'].forEach(s => { const el = _el('todo-sec-' + s); if (el) el.classList.toggle('collapsed', !!(st.collapsed && st.collapsed[s])); });
    _updateSplitVisibility();
  }

  // ── Projektliste als JSON exportieren / importieren / komplett zurücksetzen ──
  async function _exportList() {
    _spin(true); _status('Exportiere…');
    try {
      const dump = await _api('GET', '/api/todo/export');
      const blob = new Blob([JSON.stringify(dump, null, 2)], { type: 'application/json' });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url; a.download = `todo_export_${new Date().toISOString().slice(0, 10)}.json`;
      document.body.appendChild(a); a.click(); a.remove();
      setTimeout(() => URL.revokeObjectURL(url), 2000);
      _status('📤 Projektliste exportiert.');
    } catch (e) { _status('Export-Fehler: ' + e.message); }
    finally { _spin(false); setTimeout(() => _status(''), 1500); }
  }

  function _importList() {
    if (!_importInput) {
      _importInput = document.createElement('input');
      _importInput.type = 'file'; _importInput.accept = '.json,application/json'; _importInput.style.display = 'none';
      _importInput.addEventListener('change', _doImport);
      document.body.appendChild(_importInput);
    }
    _importInput.value = ''; _importInput.click();
  }

  async function _doImport() {
    const f = _importInput.files && _importInput.files[0]; if (!f) return;
    if (!confirm(`„${f.name}" importieren?\n\nProjekte mit gleicher Kennung werden ersetzt, andere hinzugefügt. Deine Wurzel bleibt erhalten.`)) return;
    _spin(true); _status('Importiere…');
    try {
      const dump = JSON.parse(await f.text());
      const res = await _api('POST', '/api/todo/import', dump);
      await _loadTree(true);
      _status(`📥 Import: ${res.projects} Projekte, ${res.items} Punkte.`);
    } catch (e) { _status('Import-Fehler: ' + e.message); }
    finally { _spin(false); setTimeout(() => _status(''), 2500); }
  }

  async function _resetList() {
    if (!confirm('Wirklich die KOMPLETTE To-Do-Liste leeren?\n\nEs wird vorher automatisch eine Sicherung angelegt (data/todo_backups/). Danach bleibt nur die leere Wurzel.')) return;
    _spin(true); _status('Leere Liste (mit Sicherung)…');
    try {
      const res = await _api('POST', '/api/todo/reset');
      _pid = ''; _data = null; _el('todo-empty').style.display = 'block'; _showView(null);
      _activate('root');
      await _loadTree(true);
      _status(`🗑 Geleert. Sicherung: ${res.backup}`);
    } catch (e) { _status('Reset-Fehler: ' + e.message); }
    finally { _spin(false); }
  }

  function init() {
    _el('btn-todo-save').addEventListener('click', _save);
    _el('btn-todo-export').addEventListener('click', _exportList);
    _el('btn-todo-import').addEventListener('click', _importList);
    _el('btn-todo-reset').addEventListener('click', _resetList);
    _el('todo-ask-go').addEventListener('click', _ask);
    _el('todo-ask-input').addEventListener('keydown', e => { if (e.key === 'Enter') _ask(); });
    _el('todo-ask-close').addEventListener('click', () => { _el('todo-ask-answer').style.display = 'none'; });
    _el('btn-todo-add-sub').addEventListener('click', () => _createSub(_pid || _active || 'root'));
    _el('todo-new-name').addEventListener('keydown', e => { if (e.key === 'Enter') _createSub(_pid || _active || 'root'); });
    _el('todo-search').addEventListener('input', _searchSoon);
    _el('todo-search').addEventListener('search', _runSearch);
    document.querySelectorAll('.todo-subtab').forEach(b => b.addEventListener('click', () => {
      _view = b.dataset.view; _showView(_view);
      if (_view === 'graph') _buildGraph();
      else if (_view === 'agenda') _loadAgenda();
    }));
    _el('todo-agenda-person').addEventListener('change', _loadAgenda);
    _el('btn-todo-agenda-refresh').addEventListener('click', _loadAgenda);
    ['todo-title', 'todo-date', 'todo-project', 'todo-participants'].forEach(id => _el(id).addEventListener('change', _collect));
    _el('btn-todo-add').addEventListener('click', _addItem);
    _el('btn-todo-extract').addEventListener('click', _extract);
    _el('btn-todo-suggest-links').addEventListener('click', _suggestLinks);
    _el('btn-todo-next').addEventListener('click', _next);
    _el('btn-todo-graph-build').addEventListener('click', _buildGraph);
    _el('btn-todo-graph-connect').addEventListener('click', _toggleConnect);
    _el('btn-todo-graph-layout').addEventListener('click', _layout);
    try { _graphMode = localStorage.getItem(GRAPH_MODE_KEY) || '2d'; } catch (_) { _graphMode = '2d'; }
    _el('btn-todo-graph-2d').addEventListener('click', () => _setGraphMode('2d'));
    _el('btn-todo-graph-3d').addEventListener('click', () => _setGraphMode('3d'));
    _updateGraphModeUI();
    _el('todo-graph-hubs').addEventListener('change', e => { _showHubs = e.target.checked; if (_cy) _buildGraph(); });
    _el('todo-graph-all').addEventListener('change', e => { _forceAll = e.target.checked; if (_view === 'graph') _buildGraph(); });
    _el('todo-graph-person').addEventListener('change', e => { _graphPerson = e.target.value; if (_view === 'graph') _buildGraph(); });
    // Punkte-Filter
    _el('todo-filter-done').addEventListener('change', e => { _filters.done = e.target.checked; _applyItemFilters(); });
    _el('todo-filter-from').addEventListener('change', e => { _filters.from = e.target.value; _applyItemFilters(); });
    _el('todo-filter-to').addEventListener('change', e => { _filters.to = e.target.value; _applyItemFilters(); });
    _el('todo-filter-assign').addEventListener('change', e => { _filters.assign = e.target.value; _applyItemFilters(); });
    _el('todo-filter-clear').addEventListener('click', _clearFilters);
    // Splitter + einklappbare Abschnitte
    _initSplitter();
    _initVStack();
    _loadTree(true);
  }

  // Von außen (z. B. Chat→To-Do-Übernahme) aufrufbar: Baum/Ansicht neu laden.
  function refresh() { try { _loadTree(true); } catch (_) {} }

  return { init, refresh };
})();
