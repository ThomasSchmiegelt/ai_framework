/* ── KI-To-Do-Liste mit Wissensgraph ─────────────────────────────────────────
 *
 * Eine Liste ist ein Container (Besprechung / Projekt / frei) mit Teilnehmern und
 * Aufgaben. Aufgaben sind über Zuständige, Status und explizite Kanten verknüpft
 * und werden als Cytoscape-Wissensgraph dargestellt (Muster wie Matrix-Recherche).
 * Persistenz je Liste in data/todo/<name>/list.json (Items, Kanten, Positionen).
 */
const Todo = (() => {
  let _name = '';
  let _data = null;   // {type,title,date,participants[],project_id,items[],edges[],positions{},settings}
  let _view = 'liste';
  let _cy = null;
  let _showHubs = true;
  let _connectMode = false;
  let _connectFrom = null;

  const STATUS = {
    offen:    { label: '🔲 offen',    color: '#9ca3af' },
    laeuft:   { label: '⏳ läuft',    color: '#3b82f6' },
    erledigt: { label: '✅ erledigt', color: '#22c55e' },
  };
  const STATUS_ORDER = ['offen', 'laeuft', 'erledigt'];

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

  // ── Liste laden / anlegen / löschen / speichern ────────────────────────────
  async function _loadList(select) {
    try {
      const list = await _api('GET', '/api/todo/lists');
      const sel = _el('todo-list');
      const typeMark = { besprechung: '👥', projekt: '📁', frei: '📝' };
      sel.innerHTML = '<option value="">— Liste wählen —</option>' +
        list.map(l => `<option value="${escHtml(l.name)}">${typeMark[l.type] || ''} ${escHtml(l.title || l.name)}</option>`).join('');
      if (select) sel.value = select;
    } catch (e) { _status('Fehler: ' + e.message); }
  }

  async function _loadProjects() {
    try {
      const projs = await _api('GET', '/api/projects');
      const sel = _el('todo-project');
      sel.innerHTML = '<option value="">— kein Projekt —</option>' +
        projs.map(p => `<option value="${escHtml(p.id)}">${escHtml(p.name)}</option>`).join('');
    } catch (_) {}
  }

  async function _open(name) {
    if (!name) { _name = ''; _data = null; _el('todo-empty').style.display = 'block'; _showView(null); return; }
    _spin(true);
    try {
      _data = await _api('GET', '/api/todo/lists/' + encodeURIComponent(name));
      _name = name;
      _el('todo-empty').style.display = 'none';
      _showView(_view);
      _renderHeader();
      _renderItems();
      _markGraphStale();
    } catch (e) { _status('Fehler: ' + e.message); }
    finally { _spin(false); }
  }

  async function _create() {
    const nm = (_el('todo-new-list').value || '').trim();
    if (!nm) return;
    _spin(true);
    try {
      await _api('POST', '/api/todo/lists', {
        name: nm, title: nm, type: _el('todo-new-type').value,
      });
      _el('todo-new-list').value = '';
      await _loadList(nm);
      await _open(nm);
    } catch (e) { _status('Fehler: ' + e.message); }
    finally { _spin(false); }
  }

  async function _delete() {
    if (!_name || !confirm(`Liste „${_name}" löschen?`)) return;
    await _api('DELETE', '/api/todo/lists/' + encodeURIComponent(_name));
    _name = ''; _data = null;
    _el('todo-list').value = '';
    _el('todo-empty').style.display = 'block';
    _showView(null);
    await _loadList();
  }

  function _collect() {
    if (!_data) return;
    _data.title = _el('todo-title').value.trim();
    _data.date = _el('todo-date').value;
    _data.project_id = _el('todo-project').value;
    _data.participants = (_el('todo-participants').value || '').split(',').map(s => s.trim()).filter(Boolean);
  }

  async function _save() {
    if (!_name || !_data) return;
    _collect();
    if (_cy) _rememberPositions();
    _spin(true);
    try {
      _data = await _api('PUT', '/api/todo/lists/' + encodeURIComponent(_name), _data);
      _status('💾 gespeichert');
      _loadList(_name);
      setTimeout(() => _status(''), 1500);
    } catch (e) { _status('Speichern: ' + e.message); }
    finally { _spin(false); }
  }

  // ── Ansichten ──────────────────────────────────────────────────────────────
  function _showView(v) {
    _el('todo-view-liste').style.display = (v === 'liste') ? 'block' : 'none';
    _el('todo-view-graph').style.display = (v === 'graph') ? 'block' : 'none';
    document.querySelectorAll('.todo-subtab').forEach(b => b.classList.toggle('active', b.dataset.view === v));
    if (v === 'graph' && _cy) setTimeout(() => { _cy.resize(); _cy.fit(undefined, 30); }, 30);
  }

  function _renderHeader() {
    _el('todo-title').value = _data.title || '';
    _el('todo-date').value = _data.date || '';
    _el('todo-participants').value = (_data.participants || []).join(', ');
    _el('todo-project').value = _data.project_id || '';
  }

  // ── Aufgabenliste ──────────────────────────────────────────────────────────
  function _itemById(id) { return (_data.items || []).find(it => it.id === id); }

  function _renderItems() {
    const host = _el('todo-items');
    const items = _data.items || [];
    if (!items.length) { host.innerHTML = '<span class="planner-muted">Noch keine Aufgaben. „➕ Aufgabe" oder „🤖 Aufgaben ableiten".</span>'; return; }
    const id2text = {}; items.forEach(it => id2text[it.id] = it.text);
    host.innerHTML = items.map((it, i) => {
      const outs = (_data.edges || []).filter(e => e.source === it.id)
        .map(e => `<span class="todo-link">→ ${escHtml(id2text[e.target] || '?')}${e.label ? ' (' + escHtml(e.label) + ')' : ''}</span>`).join(' ');
      return `<div class="todo-item" data-id="${it.id}">
        <button class="todo-statusbtn" data-id="${it.id}" title="Status wechseln" style="color:${STATUS[it.status]?.color || '#999'}">${STATUS[it.status]?.label || it.status}</button>
        <input type="text" class="var-input todo-it-text" data-id="${it.id}" value="${escHtml(it.text)}" placeholder="Aufgabe" />
        <input type="text" class="var-input todo-it-assign" data-id="${it.id}" value="${escHtml((it.assignees || []).join(', '))}" placeholder="Zuständig" style="max-width:150px" />
        <input type="text" class="var-input todo-it-due" data-id="${it.id}" value="${escHtml(it.due || '')}" placeholder="Frist" style="max-width:110px" />
        <button class="export-btn btn-danger-sm todo-it-del" data-id="${it.id}" title="Aufgabe entfernen">✕</button>
        ${outs ? `<div class="todo-item-links">${outs}</div>` : ''}
      </div>`;
    }).join('');
    host.querySelectorAll('.todo-statusbtn').forEach(b => b.addEventListener('click', e => {
      const it = _itemById(e.currentTarget.dataset.id); if (!it) return;
      const idx = STATUS_ORDER.indexOf(it.status);
      it.status = STATUS_ORDER[(idx + 1) % STATUS_ORDER.length];
      _renderItems(); _markGraphStale();
    }));
    host.querySelectorAll('.todo-it-text').forEach(inp => inp.addEventListener('change', e => {
      const it = _itemById(e.target.dataset.id); if (it) { it.text = e.target.value; _markGraphStale(); }
    }));
    host.querySelectorAll('.todo-it-assign').forEach(inp => inp.addEventListener('change', e => {
      const it = _itemById(e.target.dataset.id);
      if (it) { it.assignees = e.target.value.split(',').map(s => s.trim()).filter(Boolean); _markGraphStale(); }
    }));
    host.querySelectorAll('.todo-it-due').forEach(inp => inp.addEventListener('change', e => {
      const it = _itemById(e.target.dataset.id); if (it) it.due = e.target.value;
    }));
    host.querySelectorAll('.todo-it-del').forEach(b => b.addEventListener('click', e => {
      const id = e.currentTarget.dataset.id;
      _data.items = _data.items.filter(it => it.id !== id);
      _data.edges = (_data.edges || []).filter(ed => ed.source !== id && ed.target !== id);
      _renderItems(); _markGraphStale();
    }));
  }

  function _newId() { return 'i' + Math.random().toString(36).slice(2, 11); }

  function _addItem() {
    _data.items = _data.items || [];
    _data.items.push({ id: _newId(), text: '', detail: '', assignees: [], status: 'offen', due: '', links: [] });
    _renderItems();
  }

  // ── KI-Helfer ──────────────────────────────────────────────────────────────
  async function _extract() {
    const text = (_el('todo-note').value || '').trim();
    if (!text) { _status('Bitte eine Notiz eingeben.'); return; }
    _collect();
    _spin(true); _status('KI leitet Aufgaben ab…');
    try {
      const res = await _api('POST', '/api/todo/extract', {
        text, participants: _data.participants, model: _model(),
      });
      _tok(res.tokens);
      (res.items || []).forEach(it => _data.items.push(it));
      (res.edges || []).forEach(e => {
        if (!(_data.edges || []).some(x => x.source === e.source && x.target === e.target))
          (_data.edges = _data.edges || []).push(e);
      });
      _el('todo-note').value = '';
      _renderItems(); _markGraphStale();
      _status(`✓ ${(res.items || []).length} Aufgaben, ${(res.edges || []).length} Verknüpfungen`);
    } catch (e) { _status('KI-Fehler: ' + e.message); }
    finally { _spin(false); }
  }

  async function _suggestLinks() {
    if (!(_data.items || []).length) return;
    _spin(true); _status('KI sucht Verknüpfungen…');
    try {
      const res = await _api('POST', '/api/todo/suggest-links', { items: _data.items, model: _model() });
      _tok(res.tokens);
      let added = 0;
      (res.edges || []).forEach(e => {
        if (!(_data.edges || []).some(x => x.source === e.source && x.target === e.target)) {
          (_data.edges = _data.edges || []).push(e); added++;
        }
      });
      _renderItems(); _markGraphStale();
      _status(`✓ ${added} neue Verknüpfungen`);
    } catch (e) { _status('KI-Fehler: ' + e.message); }
    finally { _spin(false); }
  }

  async function _next() {
    _collect();
    _spin(true); _status('KI…');
    try {
      const res = await _api('POST', '/api/todo/next', { data: _data, model: _model() });
      _tok(res.tokens);
      _el('todo-next-out').innerHTML = (typeof marked !== 'undefined') ? marked.parse(res.text || '') : escHtml(res.text || '');
      _status('');
    } catch (e) { _status('KI-Fehler: ' + e.message); }
    finally { _spin(false); }
  }

  // ── Wissensgraph (Cytoscape) ───────────────────────────────────────────────
  function _markGraphStale() {
    const btn = _el('btn-todo-graph-build');
    if (btn) btn.classList.add('todo-stale');
  }

  function _graphElements() {
    const nodes = [], edges = [], valid = new Set();
    const pos = _data.positions || {};
    (_data.items || []).forEach(it => {
      valid.add(it.id);
      const n = { data: { id: it.id, kind: 'item', label: it.text || '(ohne Titel)', color: STATUS[it.status]?.color || '#9ca3af' } };
      if (pos[it.id]) n.position = { x: pos[it.id].x, y: pos[it.id].y };
      nodes.push(n);
    });
    // Hubs: Zuständige + Status
    if (_showHubs) {
      const hubs = new Map();
      (_data.items || []).forEach(it => {
        (it.assignees || []).forEach(a => {
          const hid = 'p::' + a;
          if (!hubs.has(hid)) hubs.set(hid, { label: a, type: 'person' });
          (hubs.get(hid).items = hubs.get(hid).items || []).push(it.id);
        });
        const hid = 's::' + it.status;
        if (!hubs.has(hid)) hubs.set(hid, { label: STATUS[it.status]?.label || it.status, type: 'status', color: STATUS[it.status]?.color });
        (hubs.get(hid).items = hubs.get(hid).items || []).push(it.id);
      });
      hubs.forEach((meta, hid) => {
        valid.add(hid);
        const color = meta.type === 'person' ? '#a78bfa' : (meta.color || '#9ca3af');
        const n = { data: { id: hid, kind: 'hub', label: meta.label, color } };
        if (pos[hid]) n.position = { x: pos[hid].x, y: pos[hid].y };
        nodes.push(n);
        (meta.items || []).forEach(iid => edges.push({ data: { id: 'h_' + hid + '__' + iid, source: iid, target: hid, kind: 'hub', color } }));
      });
    }
    // Explizite Item↔Item-Kanten
    _data.edges = (_data.edges || []).filter(e => valid.has(e.source) && valid.has(e.target));
    _data.edges.forEach(e => edges.push({ data: { id: e.source + '__' + e.target, source: e.source, target: e.target, label: e.label || '', kind: 'link' } }));
    return { nodes, edges, allHavePos: nodes.every(n => n.position) };
  }

  function _graphStyle() {
    const css = getComputedStyle(document.documentElement);
    const text = (css.getPropertyValue('--text') || '#e8e8e8').trim();
    const border = (css.getPropertyValue('--border') || '#3a3a3a').trim();
    const bg = (css.getPropertyValue('--bg-hover') || '#2a2a2a').trim();
    return [
      { selector: 'node', style: {
        'background-color': bg, 'border-color': 'data(color)', 'border-width': 3,
        'label': 'data(label)', 'color': text, 'font-size': 11, 'text-wrap': 'wrap',
        'text-max-width': 130, 'text-valign': 'center', 'text-halign': 'center',
        'width': 'label', 'height': 'label', 'padding': 8, 'shape': 'round-rectangle',
      }},
      { selector: 'node[kind="hub"]', style: {
        'background-color': 'data(color)', 'border-width': 0, 'shape': 'round-tag',
        'color': '#0b0b0b', 'font-size': 10, 'font-weight': 'bold', 'padding': 6,
        'text-outline-width': 1.5, 'text-outline-color': '#ffffff',
      }},
      { selector: 'node.sel', style: { 'border-color': '#22c55e', 'border-width': 5 }},
      { selector: 'edge', style: {
        'width': 2, 'line-color': border, 'target-arrow-color': border,
        'target-arrow-shape': 'triangle', 'curve-style': 'bezier', 'label': 'data(label)',
        'font-size': 9, 'color': text, 'text-background-color': bg,
        'text-background-opacity': 0.85, 'text-background-padding': 2,
      }},
      { selector: 'edge[kind="hub"]', style: {
        'width': 1.5, 'line-color': 'data(color)', 'line-opacity': 0.55,
        'target-arrow-shape': 'none', 'curve-style': 'haystack', 'label': '',
      }},
    ];
  }

  function _rememberPositions() {
    if (!_cy) return;
    _data.positions = _data.positions || {};
    _cy.nodes().forEach(n => { const p = n.position(); _data.positions[n.id()] = { x: p.x, y: p.y }; });
  }

  function _buildGraph() {
    const host = _el('todo-graph');
    if (!host || typeof cytoscape === 'undefined') { _status('Graph-Bibliothek nicht geladen.'); return; }
    if (_cy) { _rememberPositions(); _cy.destroy(); _cy = null; }
    const { nodes, edges, allHavePos } = _graphElements();
    _cy = cytoscape({
      container: host, elements: [...nodes, ...edges], style: _graphStyle(),
      wheelSensitivity: 0.2, minZoom: 0.2, maxZoom: 3,
      layout: allHavePos ? { name: 'preset' } : { name: 'cose', animate: false, padding: 30, nodeRepulsion: 9000, idealEdgeLength: 130 },
    });
    _cy.on('dragfree', 'node', () => _rememberPositions());
    _cy.on('tap', 'node', evt => _onNodeTap(evt.target));
    _cy.on('tap', 'edge', evt => _onEdgeTap(evt.target));
    if (!allHavePos) _rememberPositions();
    _el('btn-todo-graph-build').classList.remove('todo-stale');
    _updateHint();
  }

  function _layout() {
    if (!_cy) { _buildGraph(); return; }
    _cy.layout({ name: 'cose', animate: false, padding: 30, nodeRepulsion: 9000, idealEdgeLength: 130 }).run();
    _rememberPositions();
  }

  function _onNodeTap(node) {
    if (node.data('kind') !== 'item') return;
    if (_connectMode) {
      if (!_connectFrom) { _connectFrom = node.id(); node.addClass('sel'); _updateHint(); return; }
      if (_connectFrom === node.id()) { node.removeClass('sel'); _connectFrom = null; _updateHint(); return; }
      const label = prompt('Bezeichnung der Verknüpfung (z. B. blockiert, gehört zu):', 'verknüpft') || '';
      if (!(_data.edges || []).some(e => e.source === _connectFrom && e.target === node.id())) {
        (_data.edges = _data.edges || []).push({ source: _connectFrom, target: node.id(), label: label.trim() });
      }
      _cy.$('.sel').removeClass('sel'); _connectFrom = null;
      _buildGraph(); _renderItems();
      return;
    }
    // Detailanzeige der Aufgabe
    const it = _itemById(node.id());
    if (it) _status(`${STATUS[it.status]?.label || it.status} · ${it.text}` + (it.assignees?.length ? ' · ' + it.assignees.join(', ') : '') + (it.due ? ' · Frist ' + it.due : ''));
  }

  function _onEdgeTap(edge) {
    if (edge.data('kind') !== 'link') return;
    const s = edge.data('source'), t = edge.data('target');
    const cur = (_data.edges || []).find(e => e.source === s && e.target === t);
    if (!cur) return;
    const nl = prompt('Verknüpfung umbenennen (leer = löschen):', cur.label || '');
    if (nl === null) return;
    if (nl.trim() === '') _data.edges = _data.edges.filter(e => !(e.source === s && e.target === t));
    else cur.label = nl.trim();
    _buildGraph(); _renderItems();
  }

  function _toggleConnect() {
    _connectMode = !_connectMode;
    _connectFrom = null;
    if (_cy) _cy.$('.sel').removeClass('sel');
    _el('btn-todo-graph-connect').classList.toggle('active', _connectMode);
    _updateHint();
  }

  function _updateHint() {
    const el = _el('todo-graph-hint');
    if (!el) return;
    if (_connectMode) { el.textContent = _connectFrom ? 'Zielaufgabe anklicken…' : 'Startaufgabe anklicken…'; return; }
    const items = (_data?.items || []).length;
    const links = (_data?.edges || []).length;
    el.textContent = `${items} Aufgaben · ${links} Verknüpfungen`;
  }

  function init() {
    _el('todo-list').addEventListener('change', e => _open(e.target.value));
    _el('btn-todo-create').addEventListener('click', _create);
    _el('btn-todo-delete').addEventListener('click', _delete);
    _el('btn-todo-save').addEventListener('click', _save);
    _el('todo-new-list').addEventListener('keydown', e => { if (e.key === 'Enter') _create(); });
    document.querySelectorAll('.todo-subtab').forEach(b => b.addEventListener('click', () => {
      _view = b.dataset.view;
      _showView(_view);
      if (_view === 'graph' && !_cy && _data) _buildGraph();
    }));
    ['todo-title', 'todo-date', 'todo-project', 'todo-participants'].forEach(id =>
      _el(id).addEventListener('change', _collect));
    _el('btn-todo-add').addEventListener('click', _addItem);
    _el('btn-todo-extract').addEventListener('click', _extract);
    _el('btn-todo-suggest-links').addEventListener('click', _suggestLinks);
    _el('btn-todo-next').addEventListener('click', _next);
    _el('btn-todo-graph-build').addEventListener('click', _buildGraph);
    _el('btn-todo-graph-connect').addEventListener('click', _toggleConnect);
    _el('btn-todo-graph-layout').addEventListener('click', _layout);
    _el('todo-graph-hubs').addEventListener('change', e => { _showHubs = e.target.checked; if (_cy) _buildGraph(); });
    _loadList();
    _loadProjects();
  }

  return { init };
})();
