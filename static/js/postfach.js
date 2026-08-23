/* AI_Framework_Thomas — 📮 Postfach: PST/mbox/eml/msg einlesen, als Wissensgraph mit
   selbst definierten Konnektoren darstellen, Klick auf eine Mail-Blase = ganze Mail.
   Arbeitet ausschließlich lokal (Backend erzwingt ein lokales LLM in Stufe 2).
   Wiederverwendet die Graph-/Hub-Muster der Matrix-Recherche (Cytoscape vendored). */

const Postfach = (() => {

  let _storeId = null;
  let _mails   = [];        // Listen-Ansicht vom Backend
  let _cy      = null;
  let _query   = '';
  let _monthFilter = null;  // 'YYYY-MM' oder null

  let _graphMode = 'conn';  // 'conn' | 'sim' | 'net'
  let _simEdges  = [];      // [{a,b,score}] aus /api/pst/similarity
  let _simThreshold = 0.6;  // Schwellwert für Themen-Nähe-Kanten
  let _dupOnly   = false;   // nur Near-Duplikate zeigen
  let _graphStale = false;  // Einstellungen geändert → Graph muss per ▶ neu gebaut werden
  const _selectedMids = new Set();
  let _lastCollection = null;  // zuletzt genutzte RAG-Sammlung {id,name}
  const NET = 'person:';

  // Konnektoren: [{name, words:[...]}] — im localStorage gemerkt.
  const CONN_KEY = 'pf_connectors_v1';
  let _connectors = [];

  const CAT_COLORS = ['#4f8cff', '#22c55e', '#f59e0b', '#ec4899', '#a855f7', '#14b8a6', '#ef4444', '#84cc16', '#eab308', '#38bdf8'];
  const $ = id => document.getElementById(id);
  const esc = s => (typeof escHtml === 'function') ? escHtml(s) : String(s == null ? '' : s).replace(/[&<>]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;' }[c]));
  const _model = () => (typeof Profile !== 'undefined' && Profile.modelFor) ? Profile.modelFor('general') : undefined;
  // Statuszeile + Arbeits-Spinner: jede „⏳…"-Meldung zeigt automatisch den Spinner,
  // Abschlussmeldungen (✓/Fehler) blenden ihn wieder aus.
  const _status = t => {
    const el = $('pf-status'); if (el) el.textContent = t || '';
    const sp = $('pf-spin'); if (sp) sp.style.display = (t && t.indexOf('⏳') !== -1) ? 'inline-block' : 'none';
  };

  /* ── Konnektoren laden/speichern ─────────────────────────────── */
  function _loadConnectors() {
    try { _connectors = JSON.parse(localStorage.getItem(CONN_KEY) || 'null') || []; } catch (_) { _connectors = []; }
    if (!Array.isArray(_connectors)) _connectors = [];
    if (!_connectors.length) _connectors = [{ name: 'Lebensversicherung', words: ['Allianz', 'Continentale', 'CMI'] }];
  }
  function _saveConnectors() { try { localStorage.setItem(CONN_KEY, JSON.stringify(_connectors)); } catch (_) {} }

  function _color(i) { return CAT_COLORS[i % CAT_COLORS.length]; }

  /* ── Textbasis einer Mail (für Konnektor-Treffer + Suche) ────── */
  function _mailText(m) {
    return [m.sender, m.recipients, m.cc, m.subject, m.body, m.attachments_summary,
      (m.tags || []).join(' '), (m.attachments || []).map(a => a.name).join(' ')]
      .join(' \n ').toLowerCase();
  }
  function _domain(sender) {
    const mm = /@([\w.\-]+)/.exec(sender || '');
    return mm ? mm[1].toLowerCase() : '';
  }
  function _threadKey(subject) {
    return (subject || '(kein Betreff)').replace(/^\s*(re|aw|fwd|wg)\s*:\s*/i, '').replace(/^\s*(re|aw|fwd|wg)\s*:\s*/i, '').trim().toLowerCase();
  }
  function _monthOf(m) { const d = (m.date || '').slice(0, 7); return /^\d{4}-\d{2}$/.test(d) ? d : ''; }

  /* ── Aktive Konnektoren (manuell + optionale Auto-Quellen) ───── */
  function _activeConnectors() {
    const list = _connectors.map((c, i) => ({ name: c.name, words: c.words, color: _color(i), kind: 'manual' }));
    let ci = _connectors.length;
    if ($('pf-sender-clusters')?.checked) {
      const domains = new Map();
      _mails.forEach(m => { const d = _domain(m.sender); if (d) domains.set(d, (domains.get(d) || 0) + 1); });
      [...domains.entries()].filter(([, n]) => n >= 2).forEach(([d]) => {
        list.push({ name: '@' + d, words: ['@' + d], color: _color(ci++), kind: 'domain' });
      });
    }
    if ($('pf-tag-connectors')?.checked) {
      const tags = new Map();
      _mails.forEach(m => (m.tags || []).forEach(t => tags.set(t, (tags.get(t) || 0) + 1)));
      [...tags.entries()].filter(([, n]) => n >= 2).forEach(([t]) => {
        list.push({ name: '#' + t, words: [t], color: _color(ci++), kind: 'tag' });
      });
    }
    return list;
  }

  function _matches(conn, text) {
    return (conn.words || []).some(w => w && text.includes(String(w).toLowerCase()));
  }

  /* ── Anhang-Helfer ───────────────────────────────────────────── */
  function _attExts(m) { return (m.attachments || []).map(a => (a.ext || '').toLowerCase()).filter(Boolean); }
  function _hasAtt(m) { return (m.attachments || []).length > 0; }
  function _attClass(ext) {
    if (ext === 'pdf') return 'pdf';
    if (['jpg', 'jpeg', 'png', 'gif', 'webp', 'bmp', 'tif', 'tiff'].includes(ext)) return 'bild';
    if (['doc', 'docx', 'xls', 'xlsx', 'ppt', 'pptx', 'odt', 'ods', 'csv', 'rtf'].includes(ext)) return 'office';
    return 'andere';
  }

  /* ── Erweiterte Filter (Datum/Absender/Anhang/Tag/Ordner) ────── */
  function _dupMids() {
    const s = new Set();
    _simEdges.forEach(e => { if (e.score >= 0.97) { s.add(e.a); s.add(e.b); } });
    return s;
  }

  function _visibleMails() {
    const val = id => ($(id)?.value || '').trim();
    const dFrom = val('pf-f-from'), dTo = val('pf-f-to');
    const fSender = val('pf-f-sender'), fTag = val('pf-f-tag');
    const fFolder = val('pf-f-folder'), fAtt = val('pf-f-atttype');
    const onlyAtt = $('pf-f-hasatt')?.checked;
    const dupSet = _dupOnly ? _dupMids() : null;
    const connName = val('pf-conn-filter');
    const conn = connName ? _activeConnectors().find(c => c.name === connName) : null;
    return _mails.filter(m => {
      if (_monthFilter && _monthOf(m) !== _monthFilter) return false;
      if (_query && !_mailText(m).includes(_query)) return false;
      const d = (m.date || '').slice(0, 10);
      if (dFrom && d && d < dFrom) return false;
      if (dTo && d && d > dTo) return false;
      if (fSender && _domain(m.sender) !== fSender) return false;
      if (fFolder && (m.folder || '') !== fFolder) return false;
      if (fTag && !(m.tags || []).includes(fTag)) return false;
      if (onlyAtt && !_hasAtt(m)) return false;
      if (fAtt && !_attExts(m).some(e => _attClass(e) === fAtt)) return false;
      if (dupSet && !dupSet.has(m.mid)) return false;
      if (conn && !_matches(conn, _mailText(m))) return false;
      return true;
    });
  }

  // Live-Aktualisierung: nur die günstigen Ansichten (Liste + Zeitleiste). Der teure
  // Graph wird NICHT neu gelayoutet, sondern als „veraltet" markiert (▶ baut ihn).
  function _refresh() { _renderTimeline(); _renderList(); _markStale(); }

  function _fillSelect(id, pairs, allLabel) {
    const el = $(id);
    if (!el) return;
    const cur = el.value;
    el.innerHTML = `<option value="">${esc(allLabel)}</option>`
      + pairs.map(([v, l]) => `<option value="${esc(v)}">${esc(l)}</option>`).join('');
    if ([...el.options].some(o => o.value === cur)) el.value = cur;
  }

  function _populateFilters() {
    const senders = new Map(), folders = new Set(), tags = new Set();
    _mails.forEach(m => {
      const dom = _domain(m.sender); if (dom) senders.set(dom, (senders.get(dom) || 0) + 1);
      if (m.folder) folders.add(m.folder);
      (m.tags || []).forEach(t => tags.add(t));
    });
    _fillSelect('pf-f-sender', [...senders.entries()].sort((a, b) => b[1] - a[1]).map(([d, n]) => [d, `${d} (${n})`]), 'Alle Absender');
    _fillSelect('pf-f-folder', [...folders].sort().map(f => [f, f]), 'Alle Ordner');
    _fillSelect('pf-f-tag', [...tags].sort().map(t => [t, t]), 'Alle Themen-Tags');
    _populateConnFilter();
  }

  // Schnellfilter-Dropdown „Konnektor: …" aus den aktiven Konnektoren füllen.
  function _populateConnFilter() {
    _fillSelect('pf-conn-filter', _activeConnectors().map(c => [c.name, c.name]), 'Konnektor: alle');
  }

  function _mailById(mid) { return _mails.find(m => m.mid === mid); }

  function _resetFilters() {
    ['pf-f-from', 'pf-f-to', 'pf-f-sender', 'pf-f-tag', 'pf-f-folder', 'pf-f-atttype', 'pf-conn-filter'].forEach(id => { const el = $(id); if (el) el.value = ''; });
    const ha = $('pf-f-hasatt'); if (ha) ha.checked = false;
    _query = ''; if ($('pf-search')) $('pf-search').value = '';
    _monthFilter = null; _dupOnly = false;
    $('btn-pf-dup')?.classList.remove('on');
    _refresh();
  }

  /* ── Auswahl-Scope (markiert → gefiltert → alle) ─────────────── */
  function _scopeMids() {
    if (_selectedMids.size) return [..._selectedMids];
    const vis = _visibleMails();
    return (vis.length && vis.length < _mails.length) ? vis.map(m => m.mid) : null;
  }

  /* ── Graph aufbauen (drei Modi) ──────────────────────────────── */
  function _graphElements() {
    const mails = _visibleMails();
    let g;
    if (_graphMode === 'sim') g = _simElements(mails);
    else if (_graphMode === 'net') g = _netElements(mails);
    else g = _connElements(mails);
    return _pruneIsolated(g);
  }

  // „Nur verbundene Elemente": Knoten ohne Kante ausblenden (z. B. Mails, die zu
  // keinem Konnektor/Cluster gehören). Macht den Graphen fokussiert und schneller.
  function _pruneIsolated(g) {
    if (!$('pf-connected-only')?.checked) return g;
    const used = new Set();
    g.edges.forEach(e => { used.add(e.data.source); used.add(e.data.target); });
    return { nodes: g.nodes.filter(n => used.has(n.data.id)), edges: g.edges };
  }

  function _connElements(mails) {
    const nodes = [], edges = [];
    const showHubs = $('pf-show-hubs')?.checked !== false;
    const useThreads = $('pf-threads')?.checked;

    mails.forEach(m => {
      const label = (m.subject || m.sender || '(ohne Betreff)').slice(0, 46);
      nodes.push({ data: { id: 'mail:' + m.mid, kind: 'mail', mid: m.mid, label } });
    });

    if (showHubs) {
      const conns = _activeConnectors();
      const hubs = new Map();
      mails.forEach(m => {
        const text = _mailText(m);
        conns.forEach(c => {
          if (_matches(c, text)) {
            const hid = 'conn:' + c.name;
            if (!hubs.has(hid)) hubs.set(hid, c);
            edges.push({ data: { id: `e_${m.mid}_${hid}`, source: 'mail:' + m.mid, target: hid, catColor: c.color, kind: 'conn' } });
          }
        });
      });
      hubs.forEach((c, hid) => nodes.push({ data: { id: hid, kind: 'hub', label: c.name, catColor: c.color } }));
    }

    if (useThreads) {
      const threads = new Map();
      mails.forEach(m => { const k = _threadKey(m.subject); (threads.get(k) || threads.set(k, []).get(k)).push(m); });
      threads.forEach((ms, k) => {
        if (ms.length < 2) return;
        const tid = 'thread:' + k;
        nodes.push({ data: { id: tid, kind: 'thread', label: '🧵 ' + (ms[0].subject || k).slice(0, 40) } });
        ms.forEach(m => edges.push({ data: { id: `t_${m.mid}_${tid}`, source: 'mail:' + m.mid, target: tid, kind: 'thread' } }));
      });
    }
    return { nodes, edges };
  }

  /* ── Themen-Nähe (semantische Ähnlichkeit, Cluster) ──────────── */
  function _simElements(mails) {
    const nodes = [], edges = [];
    const midset = new Set(mails.map(m => m.mid));
    const active = _simEdges.filter(e => e.score >= _simThreshold && midset.has(e.a) && midset.has(e.b));
    // Union-Find über die aktiven Kanten → Cluster
    const parent = {};
    mails.forEach(m => { parent[m.mid] = m.mid; });
    const find = x => { while (parent[x] !== x) { parent[x] = parent[parent[x]]; x = parent[x]; } return x; };
    active.forEach(e => { const ra = find(e.a), rb = find(e.b); if (ra !== rb) parent[ra] = rb; });
    const groups = {};
    mails.forEach(m => { const r = find(m.mid); (groups[r] = groups[r] || []).push(m.mid); });
    const clusterColor = {};
    let ci = 0;
    Object.keys(groups).forEach(r => { clusterColor[r] = groups[r].length > 1 ? _color(ci++) : null; });
    mails.forEach(m => {
      const label = (m.subject || m.sender || '(ohne Betreff)').slice(0, 46);
      const col = clusterColor[find(m.mid)];
      const data = { id: 'mail:' + m.mid, kind: 'mail', mid: m.mid, label };
      if (col) data.clusterColor = col;
      nodes.push({ data });
    });
    active.forEach(e => edges.push({ data: {
      id: `s_${e.a}_${e.b}`, source: 'mail:' + e.a, target: 'mail:' + e.b,
      kind: 'sim', w: 1 + Math.round(e.score * 4), score: e.score } }));
    return { nodes, edges };
  }

  /* ── Kommunikationsnetz (wer mit wem) ────────────────────────── */
  function _shortPerson(p) {
    const nm = /^\s*"?([^<"]+?)"?\s*</.exec(p || '');
    if (nm && nm[1].trim()) return nm[1].trim().slice(0, 28);
    const em = /([\w.\-]+@[\w.\-]+)/.exec(p || '');
    return (em ? em[1] : (p || '')).slice(0, 28);
  }
  function _personKey(p) {
    const em = /([\w.\-]+@[\w.\-]+)/.exec(p || '');
    return (em ? em[1] : (p || '')).trim().toLowerCase().slice(0, 80);
  }
  function _splitPersons(raw) { return (raw || '').split(/[;,]/).map(s => s.trim()).filter(Boolean); }

  function _netElements(mails) {
    const people = new Map();   // key → {label, count}
    const links = new Map();    // "a|||b" → count
    mails.forEach(m => {
      const from = (m.sender || '').trim();
      if (!from) return;
      const fk = _personKey(from);
      people.set(fk, { label: _shortPerson(from), count: (people.get(fk)?.count || 0) + 1 });
      _splitPersons(m.recipients).concat(_splitPersons(m.cc)).forEach(r => {
        const rk = _personKey(r);
        if (!rk || rk === fk) return;
        people.set(rk, { label: _shortPerson(r), count: (people.get(rk)?.count || 0) + 1 });
        const lk = fk < rk ? fk + '|||' + rk : rk + '|||' + fk;
        links.set(lk, (links.get(lk) || 0) + 1);
      });
    });
    const nodes = [], edges = [];
    const maxc = Math.max(1, ...[...people.values()].map(p => p.count));
    people.forEach((p, k) => nodes.push({ data: {
      id: NET + k, kind: 'person', pkey: k, label: p.label,
      size: 18 + Math.round(34 * p.count / maxc) } }));
    links.forEach((n, lk) => { const [a, b] = lk.split('|||'); edges.push({ data: {
      id: 'p_' + lk, source: NET + a, target: NET + b, kind: 'net', w: 1 + Math.min(8, n), n } }); });
    return { nodes, edges };
  }

  function _graphStyle() {
    const css = getComputedStyle(document.documentElement);
    const cv = (n, fb) => (css.getPropertyValue(n) || fb).trim();
    const accent = cv('--accent', '#4f8cff'), text = cv('--text', '#e8e8e8');
    const border = cv('--border', '#3a3a3a'), bg = cv('--bg-hover', '#2a2a2a');
    return [
      { selector: 'node[kind="mail"]', style: {
        'background-color': bg, 'border-color': accent, 'border-width': 2,
        'label': 'data(label)', 'color': text, 'font-size': 10, 'text-wrap': 'wrap',
        'text-max-width': 130, 'text-valign': 'center', 'text-halign': 'center',
        'width': 'label', 'height': 'label', 'padding': 7, 'shape': 'round-rectangle' } },
      { selector: 'node[kind="hub"]', style: {
        'background-color': 'data(catColor)', 'border-width': 0, 'shape': 'round-tag',
        'label': 'data(label)', 'color': '#0b0b0b', 'font-size': 11, 'font-weight': 'bold',
        'padding': 7, 'text-max-width': 130, 'text-outline-width': 1.5, 'text-outline-color': '#fff' } },
      { selector: 'node[kind="thread"]', style: {
        'background-color': '#334155', 'border-color': '#64748b', 'border-width': 1.5,
        'shape': 'round-rectangle', 'label': 'data(label)', 'color': text, 'font-size': 10,
        'padding': 6, 'text-max-width': 130 } },
      { selector: 'edge[kind="conn"]', style: {
        'width': 1.5, 'line-color': 'data(catColor)', 'line-opacity': 0.6,
        'target-arrow-shape': 'none', 'curve-style': 'haystack' } },
      { selector: 'edge[kind="thread"]', style: {
        'width': 1, 'line-color': '#64748b', 'line-opacity': 0.5,
        'target-arrow-shape': 'none', 'curve-style': 'haystack' } },
      { selector: 'node[clusterColor]', style: {
        'border-color': 'data(clusterColor)', 'border-width': 4 } },
      { selector: 'edge[kind="sim"]', style: {
        'width': 'data(w)', 'line-color': accent, 'line-opacity': 0.45,
        'target-arrow-shape': 'none', 'curve-style': 'haystack' } },
      { selector: 'node[kind="person"]', style: {
        'background-color': accent, 'border-color': border, 'border-width': 1,
        'shape': 'ellipse', 'width': 'data(size)', 'height': 'data(size)',
        'label': 'data(label)', 'color': text, 'font-size': 10, 'text-wrap': 'wrap',
        'text-max-width': 90, 'text-valign': 'bottom', 'text-margin-y': 2 } },
      { selector: 'edge[kind="net"]', style: {
        'width': 'data(w)', 'line-color': '#94a3b8', 'line-opacity': 0.5,
        'target-arrow-shape': 'none', 'curve-style': 'bezier' } },
      { selector: 'node.match', style: { 'border-color': '#22c55e', 'border-width': 4 } },
      { selector: 'node.dim', style: { 'opacity': 0.25 } },
    ];
  }

  function _syncGraph() {
    const host = $('pf-graph');
    if (!host || typeof cytoscape === 'undefined') return;
    const { nodes, edges } = _graphElements();
    if (!_cy) {
      _cy = cytoscape({
        container: host, elements: [...nodes, ...edges], style: _graphStyle(),
        wheelSensitivity: 0.2, minZoom: 0.15, maxZoom: 3,
        layout: { name: 'cose', animate: false, padding: 30, nodeRepulsion: 9000, idealEdgeLength: 110 },
      });
      _cy.on('tap', 'node[kind="mail"]', evt => _openMail(evt.target.data('mid')));
      _cy.on('tap', 'node[kind="person"]', evt => _filterByPerson(evt.target.data('pkey'), evt.target.data('label')));
    } else {
      _cy.json({ elements: [...nodes, ...edges] });
      _cy.layout({ name: 'cose', animate: false, padding: 30, nodeRepulsion: 9000, idealEdgeLength: 110 }).run();
    }
    setTimeout(() => { if (_cy) { _cy.resize(); _cy.fit(undefined, 30); } }, 60);
    _updateLegend();
  }

  function _updateLegend() {
    const leg = $('pf-graph-legend');
    if (!leg) return;
    if (_graphMode === 'sim') {
      leg.innerHTML = `🧬 Themen-Nähe — Kanten = inhaltliche Ähnlichkeit (≥ ${_simThreshold.toFixed(2)}), farbige Rahmen = Cluster. Klick auf Mail = ganze Mail.`;
      return;
    }
    if (_graphMode === 'net') {
      leg.innerHTML = '👥 Kommunikationsnetz — Kreise = Personen (Größe = Korrespondenz), Kanten = Austausch. Klick auf Person filtert die Liste.';
      return;
    }
    leg.innerHTML = _activeConnectors().map(c =>
      `<span style="display:inline-flex;align-items:center;gap:3px;margin-right:8px">`
      + `<span style="width:10px;height:10px;border-radius:50%;background:${c.color};display:inline-block"></span>${esc(c.name)}</span>`
    ).join('');
  }

  /* ── Graph-Modus umschalten ──────────────────────────────────── */
  function _setMode(mode) {
    _graphMode = mode;
    document.querySelectorAll('#pf-graph-mode .pf-mode-btn').forEach(b => b.classList.toggle('on', b.dataset.mode === mode));
    const sc = $('pf-sim-controls'); if (sc) sc.style.display = (mode === 'sim') ? 'flex' : 'none';
    _markStale();
  }

  // Berechnet Embedding-Ähnlichkeit (lokal) und speichert die Kanten. Rendert NICHT
  // selbst – das Zeichnen übernimmt _buildGraph()/▶.
  async function _computeSimilarity(force) {
    if (!_storeId) { _status('Erst ein Postfach einlesen'); return; }
    if (_simEdges.length && !force) return;
    _status('⏳ Verwandtschaft (lokale Embeddings)…');
    try {
      const r = await fetch('/api/pst/similarity', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ store_id: _storeId, mids: _scopeMids(), min_score: 0.45 }),
      });
      const d = await r.json();
      if (!r.ok) throw new Error(d.detail || ('HTTP ' + r.status));
      _simEdges = d.edges || [];
      _status(`✓ ${_simEdges.length} Verwandtschafts-Kanten (${d.count} Mails)`);
    } catch (e) {
      _status('Verwandtschaft fehlgeschlagen: ' + e.message);
      if (typeof showToast === 'function') showToast('Verwandtschaft: ' + e.message);
    }
  }

  // Graph als „veraltet" markieren (Einstellung geändert) – kein teures Re-Layout.
  function _markStale() {
    _graphStale = true;
    $('btn-pf-graph-go')?.classList.add('stale');
    const leg = $('pf-graph-legend');
    if (leg && _storeId) leg.innerHTML = '⚠ Einstellungen geändert — ▶ „Graph anzeigen" drücken.';
  }

  // Graph tatsächlich bauen (auf Knopfdruck). Nur hier läuft das cose-Layout.
  async function _buildGraph() {
    if (!_storeId) { _status('Erst ein Postfach einlesen'); return; }
    const btn = $('btn-pf-graph-go');
    if (btn) btn.disabled = true;
    _status('⏳ Graph wird aufgebaut…');
    try {
      if (_graphMode === 'sim') await _computeSimilarity(false);
      await new Promise(r => setTimeout(r, 20));   // Busy-Anzeige zuerst zeichnen lassen
      _syncGraph();
      _graphStale = false;
      btn?.classList.remove('stale');
      _status(`✓ Graph: ${_visibleMails().length} Mails`);
    } catch (e) {
      _status('Graph fehlgeschlagen: ' + e.message);
    } finally {
      if (btn) btn.disabled = false;
    }
  }

  function _filterByPerson(pkey, label) {
    if (!pkey) return;
    _query = pkey.toLowerCase();
    if ($('pf-search')) $('pf-search').value = label || pkey;
    _renderList();
    _markStale();
  }

  /* ── Mail-Liste + Zeitleiste ─────────────────────────────────── */
  function _renderList() {
    const host = $('pf-list');
    const mails = _visibleMails();
    if ($('pf-count')) $('pf-count').textContent =
      `${mails.length} / ${_mails.length} Mails${_selectedMids.size ? ' · ' + _selectedMids.size + ' markiert' : ''}`;
    if (!host) return;
    host.innerHTML = mails.slice(0, 800).map(m =>
      `<div class="pf-row" data-mid="${esc(m.mid)}">
         <input type="checkbox" class="pf-sel" ${_selectedMids.has(m.mid) ? 'checked' : ''} title="markieren" />
         <div class="pf-row-main">
           <div class="pf-row-top"><span class="pf-row-date">${esc((m.date || '').slice(0, 10))}</span>
           <span class="pf-row-sender">${esc(m.sender || '')}</span></div>
           <div class="pf-row-subj">${esc(m.subject || '(kein Betreff)')}${(m.attachments || []).length ? ' 📎' : ''}
           ${(m.tags || []).map(t => `<span class="pf-tag">${esc(t)}</span>`).join('')}</div>
         </div>
       </div>`).join('');
    host.querySelectorAll('.pf-row').forEach(r => {
      const mid = r.dataset.mid;
      r.querySelector('.pf-row-main')?.addEventListener('click', () => _openMail(mid));
      const cb = r.querySelector('.pf-sel');
      cb?.addEventListener('click', e => e.stopPropagation());
      cb?.addEventListener('change', e => {
        if (e.target.checked) _selectedMids.add(mid); else _selectedMids.delete(mid);
        if ($('pf-count')) $('pf-count').textContent =
          `${_visibleMails().length} / ${_mails.length} Mails${_selectedMids.size ? ' · ' + _selectedMids.size + ' markiert' : ''}`;
      });
    });
  }

  function _renderTimeline() {
    const host = $('pf-timeline');
    if (!host) return;
    const months = new Map();
    _mails.forEach(m => { const k = _monthOf(m); if (k) months.set(k, (months.get(k) || 0) + 1); });
    if (!months.size) { host.style.display = 'none'; return; }
    const keys = [...months.keys()].sort();
    const max = Math.max(...months.values());
    host.style.display = 'flex';
    host.innerHTML = keys.map(k => {
      const h = 6 + Math.round(28 * months.get(k) / max);
      const on = _monthFilter === k ? ' pf-tl-on' : '';
      return `<div class="pf-tl-bar${on}" data-m="${k}" title="${k}: ${months.get(k)} Mails"><span style="height:${h}px"></span><label>${k.slice(2)}</label></div>`;
    }).join('');
    host.querySelectorAll('.pf-tl-bar').forEach(b => b.addEventListener('click', () => {
      _monthFilter = _monthFilter === b.dataset.m ? null : b.dataset.m;
      _renderTimeline(); _renderList(); _markStale();
    }));
  }

  /* ── Volle Mail anzeigen ─────────────────────────────────────── */
  async function _openMail(mid) {
    const modal = $('pf-mail-modal');
    if (!modal || !_storeId) return;
    $('pf-mail-title').textContent = 'Lade…';
    $('pf-mail-body').innerHTML = '';
    modal.style.display = 'flex';
    try {
      const m = await (await fetch(`/api/pst/${_storeId}/mail/${encodeURIComponent(mid)}`)).json();
      $('pf-mail-title').textContent = m.subject || '(kein Betreff)';
      const atts = (m.attachments || []).map(a => `<li>${esc(a.name)} <span class="planner-muted">(${a.ext || '?'}, ${Math.round((a.size || 0) / 1024)} KB)</span></li>`).join('');
      $('pf-mail-body').innerHTML =
        `<table class="pf-hdr"><tr><th>Von</th><td>${esc(m.sender)}</td></tr>
         <tr><th>An</th><td>${esc(m.recipients)}</td></tr>${m.cc ? `<tr><th>Cc</th><td>${esc(m.cc)}</td></tr>` : ''}
         <tr><th>Datum</th><td>${esc(m.date)}</td></tr><tr><th>Ordner</th><td>${esc(m.folder)}</td></tr></table>`
        + ((m.tags || []).length ? `<div class="pf-mail-tags">${m.tags.map(t => `<span class="pf-tag">${esc(t)}</span>`).join('')}</div>` : '')
        + (atts ? `<div class="pf-att"><strong>Anhänge:</strong><ul>${atts}</ul></div>` : '')
        + (m.attachments_summary ? `<div class="pf-att-sum"><strong>Anhang-Zusammenfassung:</strong> ${esc(m.attachments_summary)}</div>` : '')
        + `<pre class="pf-mail-text">${esc(m.body || '')}</pre>`;
    } catch (e) {
      $('pf-mail-title').textContent = 'Fehler';
      $('pf-mail-body').textContent = e.message;
    }
  }

  /* ── Einlesen (Stufe 1) ──────────────────────────────────────── */
  async function _open() {
    const path = ($('pf-path')?.value || '').trim();
    if (!path) { $('pf-path')?.focus(); return; }
    _status('⏳ Postfach wird eingelesen…');
    $('btn-pf-open').disabled = true;
    try {
      const resp = await fetch('/api/pst/open', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ path, password: $('pf-pass')?.value || '' }),
      });
      const d = await resp.json();
      if (!resp.ok) throw new Error(d.detail || ('HTTP ' + resp.status));
      _storeId = d.store_id; _mails = d.mails || [];
      _monthFilter = null; _query = ''; if ($('pf-search')) $('pf-search').value = '';
      _simEdges = []; _selectedMids.clear(); _dupOnly = false;
      $('btn-pf-dup')?.classList.remove('on');
      _populateFilters(); _loadAskCollections();
      _renderTimeline(); _renderList();
      if (_cy) { _cy.destroy(); _cy = null; }
      _markStale();
      let pw = '';
      if (d.password && d.password.checked) {
        pw = d.password.verified ? ' · 🔓 Passwort korrekt'
                                 : ' · ⚠ Passwort falsch/leer (Inhalt trotzdem gelesen)';
      }
      _status(`✓ ${d.count} Mails (${d.source_format})${pw}`);
    } catch (e) {
      _status('Fehlgeschlagen: ' + e.message);
      if (typeof showToast === 'function') showToast('Postfach: ' + e.message);
    } finally {
      $('btn-pf-open').disabled = false;
    }
  }

  /* ── Brücke: Mails aus dem im Mail-Tab konfigurierten Konto laden ─── */
  async function _fromMail() {
    // Konfiguration/Session-Passwort prüfen (Passwort lebt nur im Speicher des Mail-Tabs).
    let cfg = {};
    try { cfg = await (await fetch('/api/mail/config')).json(); } catch (_) {}
    if (!cfg.host || !cfg.user) {
      _status('Kein Mail-Konto konfiguriert — bitte zuerst im Mail-Tab Host/Benutzer eintragen.');
      if (typeof showToast === 'function') showToast('Mail-Konto zuerst im Mail-Tab einrichten');
      return;
    }
    if (!cfg.has_password) {
      _status('Mail-Passwort fehlt — bitte einmalig im Mail-Tab eingeben (nur für diese Sitzung).');
      if (typeof showToast === 'function') showToast('Passwort im Mail-Tab eingeben');
      return;
    }
    const n = parseInt(prompt('Wie viele der neuesten Mails laden?', '300') || '0', 10);
    if (!n || n < 1) return;
    _status(`⏳ Lade die neuesten ${n} Mails aus ${cfg.user}…`);
    $('btn-pf-mail').disabled = true;
    try {
      const r = await fetch('/api/mail/to-postfach', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ limit: n }),
      });
      const d = await r.json();
      if (!r.ok) throw new Error(d.detail || ('HTTP ' + r.status));
      await _reopen(d.store_id);
      _status(`✓ ${d.count} Mails aus dem Konto eingelesen`);
    } catch (e) {
      _status('Mail-Abruf fehlgeschlagen: ' + e.message);
      if (typeof showToast === 'function') showToast('Postfach/Mail: ' + e.message);
    } finally {
      $('btn-pf-mail').disabled = false;
    }
  }

  /* ── Stufe 2: Anhänge + Themen-Tags (lokal) ──────────────────── */
  async function _stage2() {
    if (!_storeId) { _status('Erst ein Postfach einlesen'); return; }
    const mids = _scopeMids();
    _status('⏳ Stufe 2 (Anhänge & Tags) — lokal…');
    $('btn-pf-stage2').disabled = true;
    try {
      const resp = await fetch('/api/pst/analyze', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ store_id: _storeId, mids, model: _model() }),
      });
      const d = await resp.json();
      if (!resp.ok) throw new Error(d.detail || ('HTTP ' + resp.status));
      if (d.tokens && typeof TokenMeter !== 'undefined') TokenMeter.add(d.tokens, 'Postfach');
      _mails = d.mails || _mails;
      _populateFilters(); _renderList(); _markStale();
      _status(`✓ Stufe 2: ${d.analyzed} Mails analysiert`);
    } catch (e) {
      _status('Fehlgeschlagen: ' + e.message);
      if (typeof showToast === 'function') showToast('Stufe 2: ' + e.message);
    } finally {
      $('btn-pf-stage2').disabled = false;
    }
  }

  async function _deleteStore() {
    if (!_storeId) return;
    if (!confirm('Eingelesenes Postfach verwerfen?')) return;
    try { await fetch(`/api/pst/${_storeId}`, { method: 'DELETE' }); } catch (_) {}
    _storeId = null; _mails = []; _simEdges = []; _selectedMids.clear(); _dupOnly = false;
    _renderTimeline(); _renderList();
    if (_cy) { _cy.destroy(); _cy = null; }
    _status('');
  }

  /* ── Gespeicherte Postfächer: auflisten / wieder öffnen / speichern ── */
  async function _openStores() {
    const host = $('pf-stores-list');
    if (host) host.innerHTML = '<span class="pf-spin"></span> Lädt…';
    if ($('pf-stores-modal')) $('pf-stores-modal').style.display = 'flex';
    try {
      const d = await (await fetch('/api/pst/stores')).json();
      const rows = d.stores || [];
      if (!host) return;
      if (!rows.length) { host.innerHTML = '<p class="planner-muted" style="font-size:12.5px">Noch keine gespeicherten Postfächer. „📥 Einlesen" legt automatisch eines an.</p>'; return; }
      host.innerHTML = rows.map(s => {
        const when = s.opened_at ? new Date(s.opened_at * 1000).toLocaleString() : '';
        const meta = `${s.count} Mails${s.stage2 ? ` · ${s.stage2} analysiert` : ''}${s.has_similarity ? ' · 🧬' : ''}${s.has_settings ? ' · 💾' : ''}`;
        return `<div class="pf-store-row" data-id="${esc(s.store_id)}">
          <div class="pf-store-info">
            <div><strong>${esc(s.name || s.store_id)}</strong> <span class="planner-muted">· ${meta}</span></div>
            <div class="planner-muted" style="font-size:11px">${esc(s.source || '')}${when ? ' · ' + esc(when) : ''}</div>
          </div>
          <button class="pf-store-open export-btn">Öffnen</button>
          <button class="pf-store-del export-btn btn-danger-sm" title="Löschen">✕</button>
        </div>`;
      }).join('');
      host.querySelectorAll('.pf-store-row').forEach(row => {
        const id = row.dataset.id;
        row.querySelector('.pf-store-open')?.addEventListener('click', () => _reopen(id));
        row.querySelector('.pf-store-del')?.addEventListener('click', async () => {
          if (!confirm('Dieses gespeicherte Postfach löschen?')) return;
          try { await fetch('/api/pst/' + encodeURIComponent(id), { method: 'DELETE' }); } catch (_) {}
          if (_storeId === id) { _storeId = null; _mails = []; _simEdges = []; _renderList(); _renderTimeline(); if (_cy) { _cy.destroy(); _cy = null; } }
          _openStores();
        });
      });
    } catch (e) {
      if (host) host.textContent = 'Fehler: ' + e.message;
    }
  }

  async function _reopen(id) {
    _status('⏳ Öffne gespeichertes Postfach…');
    try {
      const r = await fetch('/api/pst/' + encodeURIComponent(id));
      const d = await r.json();
      if (!r.ok) throw new Error(d.detail || ('HTTP ' + r.status));
      _storeId = d.store_id; _mails = d.mails || []; _simEdges = d.similarity || [];
      _selectedMids.clear(); _dupOnly = false; _monthFilter = null; _query = '';
      if ($('pf-search')) $('pf-search').value = '';
      $('btn-pf-dup')?.classList.remove('on');
      _applySettings(d.settings);
      _populateFilters(); _loadAskCollections();
      _renderTimeline(); _renderList();
      if (_cy) { _cy.destroy(); _cy = null; }
      _markStale();
      if ($('pf-stores-modal')) $('pf-stores-modal').style.display = 'none';
      _status(`✓ ${d.count} Mails geöffnet${_simEdges.length ? ' · 🧬 Verwandtschaft geladen' : ''}`);
    } catch (e) {
      _status('Öffnen fehlgeschlagen: ' + e.message);
    }
  }

  // Aktuelle Ansicht + Konnektoren zum Postfach speichern.
  async function _saveSession() {
    if (!_storeId) { _status('Erst ein Postfach einlesen'); return; }
    const settings = {
      connectors: _connectors,
      mode: _graphMode,
      connected_only: !!$('pf-connected-only')?.checked,
      sim_threshold: _simThreshold,
      toggles: {
        threads: !!$('pf-threads')?.checked,
        sender_clusters: !!$('pf-sender-clusters')?.checked,
        tag_connectors: !!$('pf-tag-connectors')?.checked,
        show_hubs: !!$('pf-show-hubs')?.checked,
      },
    };
    _status('⏳ Speichere Ansicht…');
    try {
      const r = await fetch(`/api/pst/${encodeURIComponent(_storeId)}/settings`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ settings }),
      });
      if (!r.ok) throw new Error('HTTP ' + r.status);
      _status('✓ Ansicht gespeichert');
    } catch (e) {
      _status('Speichern fehlgeschlagen: ' + e.message);
    }
  }

  // Gespeicherte Einstellungen anwenden (beim Wiederöffnen).
  function _applySettings(s) {
    if (!s) return;
    if (Array.isArray(s.connectors) && s.connectors.length) { _connectors = s.connectors; _saveConnectors(); }
    const t = s.toggles || {};
    const setCk = (id, v) => { const e = $(id); if (e && typeof v === 'boolean') e.checked = v; };
    setCk('pf-threads', t.threads); setCk('pf-sender-clusters', t.sender_clusters);
    setCk('pf-tag-connectors', t.tag_connectors); setCk('pf-show-hubs', t.show_hubs);
    setCk('pf-connected-only', s.connected_only);
    if (typeof s.sim_threshold === 'number') {
      _simThreshold = s.sim_threshold;
      if ($('pf-sim-threshold')) $('pf-sim-threshold').value = Math.round(s.sim_threshold * 100);
      if ($('pf-sim-val')) $('pf-sim-val').textContent = s.sim_threshold.toFixed(2);
    }
    if (s.mode && ['conn', 'sim', 'net'].includes(s.mode)) _setMode(s.mode);
  }

  /* ── In RAG übernehmen (Gruppe C) ────────────────────────────── */
  async function _openRagModal() {
    if (!_storeId) { _status('Erst ein Postfach einlesen'); return; }
    const sel = $('pf-rag-existing');
    if (sel) {
      try {
        const cols = await (await fetch('/api/rag/collections')).json();
        sel.innerHTML = (cols || []).map(c => `<option value="${esc(c.id)}">${esc(c.name)}</option>`).join('') || '<option value="">(keine)</option>';
        if (_lastCollection) sel.value = _lastCollection.id;
      } catch (_) { sel.innerHTML = '<option value="">(Fehler)</option>'; }
    }
    const src = (($('pf-path')?.value || '').split(/[\\/]/).pop() || 'Postfach').trim();
    if ($('pf-rag-name')) $('pf-rag-name').value = 'Postfach ' + src;
    const n = _scopeMids();
    if ($('pf-rag-scope')) $('pf-rag-scope').textContent = `${n ? n.length : _mails.length} Mail(s) werden übernommen`;
    $('pf-rag-modal').style.display = 'flex';
  }

  async function _runToRag() {
    const isNew = $('pf-rag-mode-new')?.checked;
    const payload = { store_id: _storeId, mids: _scopeMids(), include_attachments: !!$('pf-rag-att')?.checked };
    if (isNew) payload.new_collection_name = ($('pf-rag-name')?.value || '').trim() || 'Postfach';
    else payload.collection_id = $('pf-rag-existing')?.value;
    if (!isNew && !payload.collection_id) { _status('Keine Sammlung gewählt'); return; }
    if ($('pf-rag-go')) $('pf-rag-go').disabled = true;
    _status('⏳ Übernehme Mails in RAG (lokal)…');
    try {
      const r = await fetch('/api/pst/to-rag', {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
      });
      const d = await r.json();
      if (!r.ok) throw new Error(d.detail || ('HTTP ' + r.status));
      _lastCollection = { id: d.collection_id, name: d.collection_name };
      $('pf-rag-modal').style.display = 'none';
      _loadAskCollections();
      _status(`✓ ${d.ingested} Mails → „${d.collection_name}" (${d.chunks} Chunks)`);
      if (typeof RAG !== 'undefined' && typeof RAG.reload === 'function') RAG.reload();
    } catch (e) {
      _status('RAG fehlgeschlagen: ' + e.message);
      if (typeof showToast === 'function') showToast('RAG: ' + e.message);
    } finally {
      if ($('pf-rag-go')) $('pf-rag-go').disabled = false;
    }
  }

  /* ── Postfach fragen (RAG-Q&A, lokal) ────────────────────────── */
  async function _loadAskCollections() {
    const sel = $('pf-ask-coll');
    if (!sel) return;
    try {
      const cols = await (await fetch('/api/rag/collections')).json();
      sel.innerHTML = (cols || []).map(c => `<option value="${esc(c.id)}">${esc(c.name)}</option>`).join('');
      if (_lastCollection) sel.value = _lastCollection.id;
    } catch (_) {}
  }

  async function _ask() {
    const q = ($('pf-ask-input')?.value || '').trim();
    if (!q || !_storeId) return;
    const cid = $('pf-ask-coll')?.value;
    const out = $('pf-ask-answer');
    if (!cid) { if (out) out.textContent = 'Bitte zuerst Mails per „📚 In RAG" übernehmen.'; return; }
    if (out) out.innerHTML = '<span class="pf-spin"></span> Antwort wird erzeugt (lokal)…';
    _status('⏳ Postfach fragen…');
    if ($('pf-ask-go')) $('pf-ask-go').disabled = true;
    try {
      const r = await fetch('/api/pst/ask', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ store_id: _storeId, question: q, collection_id: cid, model: _model() }),
      });
      const d = await r.json();
      if (!r.ok) throw new Error(d.detail || ('HTTP ' + r.status));
      if (d.tokens && typeof TokenMeter !== 'undefined') TokenMeter.add(d.tokens, 'Postfach');
      const src = (d.sources || []).map(s => `<li>${esc(s.filename)} <span class="planner-muted">(${s.score})</span></li>`).join('');
      if (out) out.innerHTML = `<div class="pf-ask-txt">${esc(d.answer || '(keine Antwort)')}</div>`
        + (src ? `<details><summary>Quellen</summary><ul>${src}</ul></details>` : '');
    } catch (e) {
      if (out) out.textContent = 'Fehler: ' + e.message;
    } finally {
      if ($('pf-ask-go')) $('pf-ask-go').disabled = false;
      _status('');
    }
  }

  /* ── Ein-/Ausklappen (mehr Platz für die Anzeige) ────────────── */
  function _toggleBlock(id, btnId) {
    const el = $(id);
    if (!el) return;
    const show = el.style.display === 'none' || !el.style.display;
    el.style.display = show ? 'block' : 'none';
    $(btnId)?.classList.toggle('on', show);
    if (_cy) setTimeout(() => { _cy.resize(); _cy.fit(undefined, 30); }, 60);
  }

  /* ── Graph-Befehl: lokaler Chat steuert die Ansicht ──────────── */
  async function _runCommand() {
    const text = ($('pf-cmd-input')?.value || '').trim();
    if (!text || !_storeId) { if (!_storeId) _status('Erst ein Postfach einlesen'); return; }
    const out = $('pf-ask-answer');
    if (out) out.innerHTML = '<span class="pf-spin"></span> Befehl wird interpretiert (lokal)…';
    _status('⏳ Graph-Befehl…');
    if ($('pf-cmd-go')) $('pf-cmd-go').disabled = true;
    try {
      const conns = _activeConnectors().map(c => c.name);
      const r = await fetch('/api/pst/command', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ store_id: _storeId, text, connectors: conns, model: _model() }),
      });
      const d = await r.json();
      if (!r.ok) throw new Error(d.detail || ('HTTP ' + r.status));
      if (d.tokens && typeof TokenMeter !== 'undefined') TokenMeter.add(d.tokens, 'Postfach');
      await _applyDirective(d.directive || {});
      if (out) out.innerHTML = `<div class="pf-ask-txt">🗣 ${esc((d.directive && d.directive.explain) || 'Ansicht aktualisiert.')}</div>`;
    } catch (e) {
      if (out) out.textContent = 'Befehl fehlgeschlagen: ' + e.message;
      _status('');
    } finally {
      if ($('pf-cmd-go')) $('pf-cmd-go').disabled = false;
    }
  }

  // Eine Direktive (vom lokalen LLM) auf die Steuerelemente anwenden und den Graphen bauen.
  async function _applyDirective(dir) {
    const setVal = (id, v) => { const el = $(id); if (el && v != null && v !== '') el.value = v; };
    if (dir.mode && ['conn', 'sim', 'net'].includes(dir.mode)) _setMode(dir.mode);
    if (dir.connector != null) {
      const sel = $('pf-conn-filter');
      if (sel && [...sel.options].some(o => o.value === dir.connector)) sel.value = dir.connector;
    }
    if (dir.sender) {
      const sel = $('pf-f-sender');
      if (sel && [...sel.options].some(o => o.value === dir.sender)) sel.value = dir.sender;
      else { _query = String(dir.sender).toLowerCase(); if ($('pf-search')) $('pf-search').value = dir.sender; }
    }
    setVal('pf-f-from', dir.date_from); setVal('pf-f-to', dir.date_to);
    if (dir.query != null) { _query = String(dir.query).toLowerCase(); if ($('pf-search')) $('pf-search').value = dir.query; }
    if (typeof dir.only_connected === 'boolean' && $('pf-connected-only')) $('pf-connected-only').checked = dir.only_connected;
    if (dir.has_attachment === true && $('pf-f-hasatt')) $('pf-f-hasatt').checked = true;
    _renderTimeline(); _renderList();
    await _buildGraph();
    if (dir.focus) _focusNeighborhood(dir.focus, Math.max(1, Math.min(4, +dir.hops || 1)));
  }

  // Auf passende Knoten zentrieren und ihre Nachbarschaft (Eltern/Kinder) bis „hops"
  // Ebenen zeigen; der Rest wird abgeblendet.
  function _focusNeighborhood(term, hops) {
    if (!_cy || !term) return;
    const t = String(term).toLowerCase();
    const seed = _cy.nodes().filter(n => {
      if ((n.data('label') || '').toLowerCase().includes(t)) return true;
      const m = n.data('mid') && _mailById(n.data('mid'));
      return m ? _mailText(m).includes(t) : false;
    });
    if (!seed.length) { _status('Kein Knoten passt zu: ' + term); return; }
    let hood = seed;
    for (let i = 0; i < hops; i++) hood = hood.closedNeighborhood();
    _cy.elements().addClass('dim');
    hood.removeClass('dim');
    seed.addClass('match');
    _cy.fit(hood, 40);
  }

  /* ── Zusammenfassen (lokal) ──────────────────────────────────── */
  async function _summarize() {
    if (!_storeId) { _status('Erst ein Postfach einlesen'); return; }
    _status('⏳ Zusammenfassung (lokal)…');
    if ($('btn-pf-summarize')) $('btn-pf-summarize').disabled = true;
    try {
      const r = await fetch('/api/pst/summarize', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ store_id: _storeId, mids: _scopeMids(), model: _model() }),
      });
      const d = await r.json();
      if (!r.ok) throw new Error(d.detail || ('HTTP ' + r.status));
      if (d.tokens && typeof TokenMeter !== 'undefined') TokenMeter.add(d.tokens, 'Postfach');
      $('pf-mail-title').textContent = `🧾 Zusammenfassung (${d.count} Mails)`;
      $('pf-mail-body').innerHTML = `<pre class="pf-mail-text">${esc(d.summary || '')}</pre>`;
      $('pf-mail-modal').style.display = 'flex';
      _status(`✓ Zusammenfassung (${d.count} Mails)`);
    } catch (e) {
      _status('Zusammenfassung fehlgeschlagen: ' + e.message);
      if (typeof showToast === 'function') showToast('Zusammenfassung: ' + e.message);
    } finally {
      if ($('btn-pf-summarize')) $('btn-pf-summarize').disabled = false;
    }
  }

  /* ── Near-Duplikate & Export & Statistik (Gruppe D) ──────────── */
  async function _toggleDup() {
    if (!_simEdges.length) await _computeSimilarity(false);
    _dupOnly = !_dupOnly;
    $('btn-pf-dup')?.classList.toggle('on', _dupOnly);
    _renderTimeline(); _renderList(); _markStale();
  }

  function _export(fmt) {
    const mails = _visibleMails();
    if (!mails.length) { _status('Nichts zu exportieren'); return; }
    let blob, name;
    if (fmt === 'json') {
      blob = new Blob([JSON.stringify(mails, null, 2)], { type: 'application/json' });
      name = 'postfach.json';
    } else {
      const cols = ['date', 'sender', 'recipients', 'cc', 'subject', 'tags'];
      const q = v => `"${String(v == null ? '' : v).replace(/"/g, '""').replace(/\r?\n/g, ' ')}"`;
      const rows = [cols.join(',')].concat(mails.map(m =>
        cols.map(c => c === 'tags' ? q((m.tags || []).join('; ')) : q(m[c])).join(',')));
      blob = new Blob(['﻿' + rows.join('\r\n')], { type: 'text/csv' });
      name = 'postfach.csv';
    }
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob); a.download = name; a.click();
    setTimeout(() => URL.revokeObjectURL(a.href), 2000);
  }

  function _toggleStats() {
    const el = $('pf-stats');
    if (!el) return;
    if (el.style.display === 'flex') { el.style.display = 'none'; return; }
    _renderStats();
    el.style.display = 'flex';
  }

  function _renderStats() {
    const el = $('pf-stats');
    if (!el) return;
    const senders = new Map(), months = new Map();
    let withAtt = 0, withTags = 0;
    _mails.forEach(m => {
      const d = _domain(m.sender) || (m.sender || '—');
      senders.set(d, (senders.get(d) || 0) + 1);
      const mo = _monthOf(m); if (mo) months.set(mo, (months.get(mo) || 0) + 1);
      if (_hasAtt(m)) withAtt++;
      if ((m.tags || []).length) withTags++;
    });
    const top = [...senders.entries()].sort((a, b) => b[1] - a[1]).slice(0, 8);
    const maxS = Math.max(1, ...top.map(t => t[1]));
    const moKeys = [...months.keys()].sort();
    const maxM = Math.max(1, ...months.values());
    const bar = (label, n, max) => `<div class="pf-stat-row"><span>${esc(label)}</span><span class="pf-stat-bar"><i style="width:${Math.round(100 * n / max)}%"></i></span><b>${n}</b></div>`;
    el.innerHTML =
      `<div class="pf-stat-block"><h4>Top-Absender</h4>${top.map(([d, n]) => bar(d, n, maxS)).join('')}</div>`
      + `<div class="pf-stat-block"><h4>Volumen / Monat</h4>${moKeys.map(k => bar(k, months.get(k), maxM)).join('')}</div>`
      + `<div class="pf-stat-block"><h4>Kennzahlen</h4>`
      + `<div class="pf-stat-row"><span>Mails gesamt</span><b>${_mails.length}</b></div>`
      + `<div class="pf-stat-row"><span>mit Anhang</span><b>${withAtt}</b></div>`
      + `<div class="pf-stat-row"><span>mit Themen-Tags</span><b>${withTags}</b></div></div>`;
  }

  /* ── Konnektor-Editor ────────────────────────────────────────── */
  function _renderConnEditor() {
    const host = $('pf-conn-list');
    if (!host) return;
    host.innerHTML = _connectors.map((c, i) =>
      `<div class="pf-conn-item" data-i="${i}">
         <input class="pf-conn-name" value="${esc(c.name)}" placeholder="Konnektor-Name" />
         <input class="pf-conn-words" value="${esc((c.words || []).join(', '))}" placeholder="Wörter, mit Komma getrennt" />
         <button class="pf-conn-del" title="Löschen">✕</button>
       </div>`).join('');
    host.querySelectorAll('.pf-conn-item').forEach(row => {
      const i = +row.dataset.i;
      row.querySelector('.pf-conn-name').addEventListener('change', e => { _connectors[i].name = e.target.value.trim() || _connectors[i].name; _saveConnectors(); });
      row.querySelector('.pf-conn-words').addEventListener('change', e => {
        _connectors[i].words = e.target.value.split(',').map(s => s.trim()).filter(Boolean); _saveConnectors();
      });
      row.querySelector('.pf-conn-del').addEventListener('click', () => { _connectors.splice(i, 1); _saveConnectors(); _renderConnEditor(); });
    });
  }
  function _openConnEditor() { _renderConnEditor(); $('pf-conn-modal').style.display = 'flex'; }
  function _closeConnEditor() { $('pf-conn-modal').style.display = 'none'; _populateConnFilter(); _markStale(); }

  /* ── Splitter (Liste ↔ Graph) ────────────────────────────────── */
  function _initSplitter() {
    const sp = $('pf-splitter'), body = $('pf-body'), left = $('pf-left');
    if (!sp || !body || !left) return;
    let drag = false;
    const mv = e => { if (!drag) return; const r = body.getBoundingClientRect(); let w = e.clientX - r.left; w = Math.max(180, Math.min(r.width - 240, w)); left.style.flexBasis = w + 'px'; if (_cy) _cy.resize(); };
    sp.addEventListener('mousedown', e => { drag = true; e.preventDefault(); document.body.style.userSelect = 'none'; });
    window.addEventListener('mousemove', mv);
    window.addEventListener('mouseup', () => { if (drag && _cy) _cy.fit(undefined, 30); drag = false; document.body.style.userSelect = ''; });
  }

  // Verfügbare .pst-Leseart anzeigen (Outlook auf Windows / libpff / nur Export).
  async function _loadFormats() {
    try {
      const f = await (await fetch('/api/pst/formats')).json();
      const r = f.formats?.pst_reader || '';
      const note = r === 'outlook' ? '.pst über Outlook ✓'
        : r === 'libpff' ? '.pst über libpff ✓'
        : '.pst nicht lesbar — bitte in Outlook nach .mbox/.eml/.msg exportieren';
      const local = f.local_llm ? '' : ' · ⚠ kein lokales LLM (Stufe 2 & Analyse deaktiviert)';
      _status(note + local);
      if ($('pf-path')) $('pf-path').title = note;
    } catch (_) {}
  }

  function init() {
    if (!$('postfach-panel')) return;
    _loadConnectors();
    _loadFormats();
    $('btn-pf-open')?.addEventListener('click', _open);
    $('btn-pf-mail')?.addEventListener('click', _fromMail);
    $('pf-path')?.addEventListener('keydown', e => { if (e.key === 'Enter') _open(); });
    $('btn-pf-stage2')?.addEventListener('click', _stage2);
    $('btn-pf-delete')?.addEventListener('click', _deleteStore);
    $('btn-pf-stores')?.addEventListener('click', _openStores);
    $('btn-pf-save')?.addEventListener('click', _saveSession);
    $('pf-stores-close')?.addEventListener('click', () => { $('pf-stores-modal').style.display = 'none'; });
    $('btn-pf-connectors')?.addEventListener('click', _openConnEditor);
    $('pf-conn-close')?.addEventListener('click', _closeConnEditor);
    $('pf-conn-add')?.addEventListener('click', () => { _connectors.push({ name: 'Neuer Konnektor', words: [] }); _saveConnectors(); _renderConnEditor(); });
    $('pf-mail-close')?.addEventListener('click', () => { $('pf-mail-modal').style.display = 'none'; });
    ['pf-conn-modal', 'pf-mail-modal', 'pf-rag-modal', 'pf-stores-modal'].forEach(id => $(id)?.addEventListener('click', e => { if (e.target.id === id) e.target.style.display = 'none'; }));
    let _st = null;
    $('pf-search')?.addEventListener('input', e => { clearTimeout(_st); _st = setTimeout(() => { _query = (e.target.value || '').trim().toLowerCase(); _renderList(); _markStale(); }, 200); });
    // Konnektor-Quellen ändern → Schnellfilter neu füllen + Graph veralten
    ['pf-threads', 'pf-sender-clusters', 'pf-tag-connectors', 'pf-show-hubs', 'pf-connected-only'].forEach(id => $(id)?.addEventListener('change', () => { _populateConnFilter(); _markStale(); }));

    // Erweiterte Filter (Gruppe A) + Konnektor-Schnellfilter
    ['pf-f-from', 'pf-f-to', 'pf-f-sender', 'pf-f-tag', 'pf-f-folder', 'pf-f-atttype', 'pf-f-hasatt', 'pf-conn-filter'].forEach(id => $(id)?.addEventListener('change', _refresh));
    $('btn-pf-filter-reset')?.addEventListener('click', _resetFilters);

    // Anzeige maximieren: Optionen/Chat ein- und ausklappen
    $('btn-pf-options')?.addEventListener('click', () => _toggleBlock('pf-options', 'btn-pf-options'));
    $('btn-pf-chat')?.addEventListener('click', () => _toggleBlock('pf-chat-wrap', 'btn-pf-chat'));
    // Graph-Befehl (lokaler Chat)
    $('pf-cmd-go')?.addEventListener('click', _runCommand);
    $('pf-cmd-input')?.addEventListener('keydown', e => { if (e.key === 'Enter') _runCommand(); });

    // Graph bauen (▶) — einziger Auslöser fürs teure Layout
    $('btn-pf-graph-go')?.addEventListener('click', _buildGraph);

    // Graph-Modi (Gruppe B)
    document.querySelectorAll('#pf-graph-mode .pf-mode-btn').forEach(b => b.addEventListener('click', () => _setMode(b.dataset.mode)));
    $('pf-sim-threshold')?.addEventListener('input', e => {
      _simThreshold = (+e.target.value || 60) / 100;
      if ($('pf-sim-val')) $('pf-sim-val').textContent = _simThreshold.toFixed(2);
      _markStale();
    });
    $('pf-sim-recompute')?.addEventListener('click', () => { _simEdges = []; _buildGraph(); });

    // RAG + fragen (Gruppe C)
    $('btn-pf-rag')?.addEventListener('click', _openRagModal);
    $('pf-rag-go')?.addEventListener('click', _runToRag);
    $('pf-rag-close')?.addEventListener('click', () => { $('pf-rag-modal').style.display = 'none'; });
    $('pf-ask-go')?.addEventListener('click', _ask);
    $('pf-ask-input')?.addEventListener('keydown', e => { if (e.key === 'Enter') _ask(); });

    // Statistik / Duplikate / Zusammenfassen / Export (Gruppe D)
    $('btn-pf-stats')?.addEventListener('click', _toggleStats);
    $('btn-pf-dup')?.addEventListener('click', _toggleDup);
    $('btn-pf-summarize')?.addEventListener('click', _summarize);
    $('btn-pf-export-csv')?.addEventListener('click', () => _export('csv'));
    $('btn-pf-export-json')?.addEventListener('click', () => _export('json'));

    _initSplitter();
    document.querySelector('.tab-btn[data-tab="postfach"]')?.addEventListener('click', () => { if (_cy) setTimeout(() => { _cy.resize(); _cy.fit(undefined, 30); }, 60); });
  }

  return { init };
})();
