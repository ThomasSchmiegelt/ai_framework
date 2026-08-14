/* ── KI-To-Do-Liste mit Wissensgraph ─────────────────────────────────────────
 *
 * Ablauf: Besprechungsnotiz schreiben → „To-Do-Liste ableiten" (KI macht daraus
 * einzelne Punkte) → Punkte abhaken, Dokumente anhängen (werden als Markdown
 * gespeichert und mitgesucht). Ein Projekt = ein Ordner data/todo/<name>/. Die
 * Suche und der Wissensgraph laufen projektübergreifend („Projekte kommunizieren").
 * Wissensgraph via Cytoscape (Hub-Muster wie Matrix-Recherche).
 */
const Todo = (() => {
  let _name = '';
  let _data = null;   // {type,title,date,participants[],project_id,items[],edges[],positions{}}
  let _view = 'liste';
  let _cy = null;
  let _showHubs = true;
  let _graphAll = false;
  let _connectMode = false;
  let _connectFrom = null;
  let _fileInput = null;
  let _attachTarget = null;
  let _searchTimer = null;

  const STATUS = {
    offen:    { label: 'offen',    color: '#9ca3af' },
    laeuft:   { label: 'läuft',    color: '#3b82f6' },
    erledigt: { label: 'erledigt', color: '#22c55e' },
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

  // ── Projekt laden / anlegen / löschen / speichern ──────────────────────────
  async function _loadList(select) {
    try {
      const list = await _api('GET', '/api/todo/lists');
      const sel = _el('todo-list');
      const typeMark = { besprechung: '👥', projekt: '📁', frei: '📝' };
      sel.innerHTML = '<option value="">— Projekt wählen —</option>' +
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
      _clearSearch();
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
      await _api('POST', '/api/todo/lists', { name: nm, title: nm, type: _el('todo-new-type').value });
      _el('todo-new-list').value = '';
      await _loadList(nm);
      await _open(nm);
    } catch (e) { _status('Fehler: ' + e.message); }
    finally { _spin(false); }
  }

  async function _delete() {
    if (!_name || !confirm(`Projekt „${_name}" mit allen Punkten und Anlagen löschen?`)) return;
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
    if (_cy && !_graphAll) _rememberPositions();
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

  // ── Punkte ─────────────────────────────────────────────────────────────────
  function _itemById(id) { return (_data.items || []).find(it => it.id === id); }

  function _renderCount() {
    const items = _data.items || [];
    const done = items.filter(it => it.status === 'erledigt').length;
    const el = _el('todo-count');
    if (el) el.textContent = items.length ? `(${done}/${items.length} erledigt)` : '';
  }

  function _attChips(it) {
    return (it.attachments || []).map(a => {
      const url = `/api/todo/lists/${encodeURIComponent(_name)}/attachment/${encodeURIComponent(it.id)}/${encodeURIComponent(a.md)}`;
      return `<span class="todo-att-chip"><a href="${url}" target="_blank" rel="noopener" title="${escHtml(a.name || a.md)} (als Markdown)">📄 ${escHtml(a.name || a.md)}</a><button class="todo-att-del" data-id="${it.id}" data-md="${escHtml(a.md)}" title="Anlage entfernen">✕</button></span>`;
    }).join('');
  }

  function _renderItems() {
    _renderCount();
    const host = _el('todo-items');
    const items = _data.items || [];
    if (!items.length) { host.innerHTML = '<span class="planner-muted">Noch keine Punkte. Notiz oben schreiben und „🪄 To-Do-Liste ableiten" – oder „➕ Punkt".</span>'; return; }
    const id2text = {}; items.forEach(it => id2text[it.id] = it.text);
    host.innerHTML = items.map(it => {
      const done = it.status === 'erledigt';
      const outs = (_data.edges || []).filter(e => e.source === it.id)
        .map(e => `<span class="todo-link">→ ${escHtml(id2text[e.target] || '?')}${e.label ? ' (' + escHtml(e.label) + ')' : ''}</span>`).join(' ');
      const atts = _attChips(it);
      return `<div class="todo-item${done ? ' todo-done' : ''}" data-id="${it.id}">
        <input type="checkbox" class="todo-check" data-id="${it.id}" ${done ? 'checked' : ''} title="Erledigt abhaken" />
        <input type="text" class="var-input todo-it-text" data-id="${it.id}" value="${escHtml(it.text)}" placeholder="Aufgabe" />
        <input type="text" class="var-input todo-it-assign" data-id="${it.id}" value="${escHtml((it.assignees || []).join(', '))}" placeholder="Zuständig" style="max-width:140px" />
        <input type="text" class="var-input todo-it-due" data-id="${it.id}" value="${escHtml(it.due || '')}" placeholder="Frist" style="max-width:100px" />
        <button class="export-btn todo-it-attach" data-id="${it.id}" title="Dokument anhängen (wird als Markdown gespeichert)">📎</button>
        <button class="export-btn btn-danger-sm todo-it-del" data-id="${it.id}" title="Punkt entfernen">✕</button>
        ${(atts || outs) ? `<div class="todo-item-sub">${atts}${outs ? ` <span class="todo-links">${outs}</span>` : ''}</div>` : ''}
      </div>`;
    }).join('');

    host.querySelectorAll('.todo-check').forEach(cb => cb.addEventListener('change', e => {
      const it = _itemById(e.target.dataset.id); if (!it) return;
      it.status = e.target.checked ? 'erledigt' : 'offen';
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
    host.querySelectorAll('.todo-it-attach').forEach(b => b.addEventListener('click', e => _pickAttachment(e.currentTarget.dataset.id)));
    host.querySelectorAll('.todo-att-del').forEach(b => b.addEventListener('click', e => _deleteAttachment(e.currentTarget.dataset.id, e.currentTarget.dataset.md)));
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
    _data.items.push({ id: _newId(), text: '', detail: '', assignees: [], status: 'offen', due: '', links: [], attachments: [] });
    _renderItems();
  }

  // ── Anlagen (Dokument → Markdown) ──────────────────────────────────────────
  function _pickAttachment(itemId) {
    if (!_name) { _status('Erst speichern, dann anhängen.'); return; }
    _attachTarget = itemId;
    if (!_fileInput) {
      _fileInput = document.createElement('input');
      _fileInput.type = 'file';
      _fileInput.style.display = 'none';
      _fileInput.addEventListener('change', _uploadAttachment);
      document.body.appendChild(_fileInput);
    }
    _fileInput.value = '';
    _fileInput.click();
  }

  async function _uploadAttachment() {
    const f = _fileInput.files && _fileInput.files[0];
    if (!f || !_attachTarget) return;
    // Punkt muss serverseitig existieren → vorher speichern
    _spin(true); _status('Anlage wird gelesen…');
    try {
      await _save();
      const fd = new FormData();
      fd.append('file', f);
      const r = await fetch(`/api/todo/lists/${encodeURIComponent(_name)}/items/${encodeURIComponent(_attachTarget)}/attach`, { method: 'POST', body: fd });
      if (!r.ok) { let m = 'HTTP ' + r.status; try { m = (await r.json()).detail || m; } catch (_) {} throw new Error(m); }
      _data = (await r.json()).list;
      _renderItems();
      _status(`📎 „${f.name}" angehängt (als Markdown gespeichert)`);
    } catch (e) { _status('Anlage-Fehler: ' + e.message); }
    finally { _spin(false); _attachTarget = null; }
  }

  async function _deleteAttachment(itemId, md) {
    if (!confirm('Anlage entfernen?')) return;
    try {
      const r = await fetch(`/api/todo/lists/${encodeURIComponent(_name)}/items/${encodeURIComponent(itemId)}/attach/${encodeURIComponent(md)}`, { method: 'DELETE' });
      _data = (await r.json()).list;
      _renderItems();
    } catch (e) { _status('Fehler: ' + e.message); }
  }

  // ── KI-Helfer ──────────────────────────────────────────────────────────────
  async function _extract() {
    const text = (_el('todo-note').value || '').trim();
    if (!text) { _status('Bitte eine Notiz eingeben.'); return; }
    _collect();
    _spin(true); _status('KI leitet Punkte ab…');
    try {
      const res = await _api('POST', '/api/todo/extract', { text, participants: _data.participants, model: _model() });
      _tok(res.tokens);
      if (_el('todo-extract-replace').checked) { _data.items = []; _data.edges = []; }
      (res.items || []).forEach(it => _data.items.push(it));
      (res.edges || []).forEach(e => {
        if (!(_data.edges || []).some(x => x.source === e.source && x.target === e.target))
          (_data.edges = _data.edges || []).push(e);
      });
      _renderItems(); _markGraphStale();
      _status(`✓ ${(res.items || []).length} Punkte, ${(res.edges || []).length} Verknüpfungen — nicht vergessen: 💾 Speichern`);
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

  // ── Globale Suche (alle Projekte + Anlagen) ────────────────────────────────
  function _clearSearch() {
    const box = _el('todo-search-results');
    if (box) { box.style.display = 'none'; box.innerHTML = ''; }
    const inp = _el('todo-search'); if (inp) inp.value = '';
  }

  function _searchSoon() { clearTimeout(_searchTimer); _searchTimer = setTimeout(_runSearch, 300); }

  async function _runSearch() {
    const q = (_el('todo-search').value || '').trim();
    const box = _el('todo-search-results');
    if (!q) { box.style.display = 'none'; box.innerHTML = ''; return; }
    try {
      const res = await _api('GET', '/api/todo/search?q=' + encodeURIComponent(q));
      const rows = res.results || [];
      box.style.display = 'block';
      if (!rows.length) { box.innerHTML = `<div class="planner-muted" style="padding:8px">Keine Treffer für „${escHtml(q)}".</div>`; return; }
      box.innerHTML = `<div class="todo-search-head">🔍 ${rows.length} Treffer für „${escHtml(q)}" (alle Projekte)</div>` +
        rows.map(r => {
          const badge = r.source === 'attachment' ? '📄 Anlage' : '📝 Punkt';
          const done = r.status === 'erledigt' ? ' ✅' : '';
          const snip = r.attachment && r.attachment.snippet ? `<div class="todo-search-snip">…${escHtml(r.attachment.snippet)}…</div>` : '';
          return `<div class="todo-search-row" data-project="${escHtml(r.project)}" data-item="${escHtml(r.item_id)}">
            <span class="todo-search-badge">${badge}</span>
            <span class="todo-search-proj">${escHtml(r.project_title || r.project)}</span>
            <span class="todo-search-text">${escHtml(r.text)}${done}</span>${snip}
          </div>`;
        }).join('');
      box.querySelectorAll('.todo-search-row').forEach(row => row.addEventListener('click', async () => {
        const proj = row.dataset.project, item = row.dataset.item;
        _el('todo-list').value = proj;
        await _open(proj);
        _clearSearch();
        setTimeout(() => {
          const el = document.querySelector(`.todo-item[data-id="${item}"]`);
          if (el) { el.scrollIntoView({ block: 'center' }); el.classList.add('todo-flash'); setTimeout(() => el.classList.remove('todo-flash'), 1600); }
        }, 200);
      }));
    } catch (e) { box.style.display = 'block'; box.innerHTML = `<div class="planner-muted" style="padding:8px">Suchfehler: ${escHtml(e.message)}</div>`; }
  }

  // ── Wissensgraph (Cytoscape) ───────────────────────────────────────────────
  function _markGraphStale() {
    const btn = _el('btn-todo-graph-build');
    if (btn) btn.classList.add('todo-stale');
  }

  // Elemente aus einem oder mehreren Projekten bauen. projects = [{name,color,items,edges,positions}]
  function _graphElements(projects, usePositions) {
    const nodes = [], edges = [], valid = new Set();
    const nid = (proj, id) => (projects.length > 1 ? proj + '::' + id : id);
    projects.forEach(pr => {
      const pos = pr.positions || {};
      (pr.items || []).forEach(it => {
        const id = nid(pr.name, it.id);
        valid.add(id);
        const n = { data: { id, kind: 'item', label: it.text || '(ohne Titel)', color: STATUS[it.status]?.color || '#9ca3af', border: pr.color || null, project: pr.name } };
        if (usePositions && pos[it.id]) n.position = { x: pos[it.id].x, y: pos[it.id].y };
        nodes.push(n);
      });
    });
    // Hubs: Zuständige (+ Status im Einzelprojekt) — geteilt über Projekte hinweg → verbinden
    if (_showHubs) {
      const hubs = new Map();
      projects.forEach(pr => (pr.items || []).forEach(it => {
        (it.assignees || []).forEach(a => {
          const hid = 'p::' + a;
          if (!hubs.has(hid)) hubs.set(hid, { label: a, color: '#a78bfa', items: [] });
          hubs.get(hid).items.push(nid(pr.name, it.id));
        });
        if (projects.length === 1) {
          const hid = 's::' + it.status;
          if (!hubs.has(hid)) hubs.set(hid, { label: STATUS[it.status]?.label || it.status, color: STATUS[it.status]?.color, items: [] });
          hubs.get(hid).items.push(nid(pr.name, it.id));
        }
      }));
      hubs.forEach((meta, hid) => {
        valid.add(hid);
        const n = { data: { id: hid, kind: 'hub', label: meta.label, color: meta.color } };
        nodes.push(n);
        meta.items.forEach(iid => edges.push({ data: { id: 'h_' + hid + '__' + iid, source: iid, target: hid, kind: 'hub', color: meta.color } }));
      });
    }
    // Item↔Item-Kanten
    projects.forEach(pr => {
      pr.edges = (pr.edges || []).filter(e => valid.has(nid(pr.name, e.source)) && valid.has(nid(pr.name, e.target)));
      pr.edges.forEach(e => {
        const s = nid(pr.name, e.source), t = nid(pr.name, e.target);
        edges.push({ data: { id: s + '__' + t, source: s, target: t, label: e.label || '', kind: 'link' } });
      });
    });
    return { nodes, edges, allHavePos: usePositions && nodes.every(n => n.position || n.data.kind === 'hub') };
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
      { selector: 'node[border]', style: { 'border-color': 'data(border)', 'border-width': 4 } },
      { selector: 'node[kind="hub"]', style: {
        'background-color': 'data(color)', 'border-width': 0, 'shape': 'round-tag',
        'color': '#0b0b0b', 'font-size': 10, 'font-weight': 'bold', 'padding': 6,
        'text-outline-width': 1.5, 'text-outline-color': '#ffffff',
      }},
      { selector: 'node.sel', style: { 'border-color': '#22c55e', 'border-width': 5 } },
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
    if (!_cy || _graphAll) return;
    _data.positions = _data.positions || {};
    _cy.nodes('[kind="item"]').forEach(n => { const p = n.position(); _data.positions[n.id()] = { x: p.x, y: p.y }; });
  }

  async function _buildGraph() {
    const host = _el('todo-graph');
    if (!host || typeof cytoscape === 'undefined') { _status('Graph-Bibliothek nicht geladen.'); return; }
    let projects;
    if (_graphAll) {
      _spin(true);
      try {
        const lists = await _api('GET', '/api/todo/lists');
        projects = [];
        for (let i = 0; i < lists.length; i++) {
          const d = await _api('GET', '/api/todo/lists/' + encodeURIComponent(lists[i].name));
          projects.push({ name: lists[i].name, color: PROJ_COLORS[i % PROJ_COLORS.length], items: d.items, edges: d.edges, positions: d.positions });
        }
      } catch (e) { _status('Fehler: ' + e.message); _spin(false); return; }
      _spin(false);
    } else {
      if (!_data) return;
      projects = [{ name: _name, color: null, items: _data.items, edges: _data.edges, positions: _data.positions }];
    }
    if (_cy) { _rememberPositions(); _cy.destroy(); _cy = null; }
    const { nodes, edges, allHavePos } = _graphElements(projects, !_graphAll);
    _cy = cytoscape({
      container: host, elements: [...nodes, ...edges], style: _graphStyle(),
      wheelSensitivity: 0.2, minZoom: 0.2, maxZoom: 3,
      layout: allHavePos ? { name: 'preset' } : { name: 'cose', animate: false, padding: 30, nodeRepulsion: 9000, idealEdgeLength: 130 },
    });
    if (!_graphAll) {
      _cy.on('dragfree', 'node', () => _rememberPositions());
      _cy.on('tap', 'node', evt => _onNodeTap(evt.target));
      _cy.on('tap', 'edge', evt => _onEdgeTap(evt.target));
    }
    setTimeout(() => { _cy.resize(); _cy.fit(undefined, 30); }, 20);
    _el('btn-todo-graph-build').classList.remove('todo-stale');
    _updateHint(projects);
  }

  function _layout() {
    if (!_cy) { _buildGraph(); return; }
    _cy.layout({ name: 'cose', animate: false, padding: 30, nodeRepulsion: 9000, idealEdgeLength: 130 }).run();
    _rememberPositions();
    setTimeout(() => _cy.fit(undefined, 30), 20);
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
    const it = _itemById(node.id());
    if (it) _status(`${it.status === 'erledigt' ? '✅' : '🔲'} ${it.text}` + (it.assignees?.length ? ' · ' + it.assignees.join(', ') : '') + (it.due ? ' · Frist ' + it.due : ''));
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
    if (_graphAll) { _status('Verbinden nur im Einzelprojekt.'); return; }
    _connectMode = !_connectMode;
    _connectFrom = null;
    if (_cy) _cy.$('.sel').removeClass('sel');
    _el('btn-todo-graph-connect').classList.toggle('active', _connectMode);
    _updateHint();
  }

  function _updateHint(projects) {
    const el = _el('todo-graph-hint');
    if (!el) return;
    if (_connectMode) { el.textContent = _connectFrom ? 'Zielaufgabe anklicken…' : 'Startaufgabe anklicken…'; return; }
    if (_graphAll && projects) {
      const items = projects.reduce((s, p) => s + (p.items || []).length, 0);
      el.textContent = `${projects.length} Projekte · ${items} Punkte (projektübergreifend)`;
      return;
    }
    const items = (_data?.items || []).length;
    const links = (_data?.edges || []).length;
    el.textContent = `${items} Punkte · ${links} Verknüpfungen`;
  }

  function init() {
    _el('todo-list').addEventListener('change', e => _open(e.target.value));
    _el('btn-todo-create').addEventListener('click', _create);
    _el('btn-todo-delete').addEventListener('click', _delete);
    _el('btn-todo-save').addEventListener('click', _save);
    _el('todo-new-list').addEventListener('keydown', e => { if (e.key === 'Enter') _create(); });
    _el('todo-search').addEventListener('input', _searchSoon);
    _el('todo-search').addEventListener('search', _runSearch);
    document.querySelectorAll('.todo-subtab').forEach(b => b.addEventListener('click', () => {
      _view = b.dataset.view;
      _showView(_view);
      if (_view === 'graph' && (!_cy || _graphAll !== _el('todo-graph-all').checked) && (_data || _graphAll)) _buildGraph();
    }));
    ['todo-title', 'todo-date', 'todo-project', 'todo-participants'].forEach(id => _el(id).addEventListener('change', _collect));
    _el('btn-todo-add').addEventListener('click', _addItem);
    _el('btn-todo-extract').addEventListener('click', _extract);
    _el('btn-todo-suggest-links').addEventListener('click', _suggestLinks);
    _el('btn-todo-next').addEventListener('click', _next);
    _el('btn-todo-graph-build').addEventListener('click', _buildGraph);
    _el('btn-todo-graph-connect').addEventListener('click', _toggleConnect);
    _el('btn-todo-graph-layout').addEventListener('click', _layout);
    _el('todo-graph-hubs').addEventListener('change', e => { _showHubs = e.target.checked; if (_cy) _buildGraph(); });
    _el('todo-graph-all').addEventListener('change', e => { _graphAll = e.target.checked; _connectMode = false; _buildGraph(); });
    _loadList();
    _loadProjects();
  }

  return { init };
})();
