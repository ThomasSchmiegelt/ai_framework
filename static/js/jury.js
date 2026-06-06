// jury.js — ⚖️ Bewertungs-Jurys (Gremien aus Agenten)
// Verwaltung im Agenten-Tab (Button „⚖️ Jurys") + ein wiederverwendbares
// Bewertungs-Overlay Jury.evaluate(text, {title, context}) für Dokumente/Agenten/
// Planer. Eine Jury bewertet einen Text mit je einem Votum pro Mitglied (SSE) plus
// einem synthetisierten Gesamturteil.
const Jury = (() => {
  let _juries = [];
  let _agents = [];
  let _editId = null;            // aktuell bearbeitete Jury (null = neu)
  let _evalCtx = null;           // {text, context} für das Bewertungs-Overlay
  let _running = false;

  function _el(id) { return document.getElementById(id); }

  async function _fetchAll() {
    try { _juries = await (await fetch('/api/juries')).json(); } catch (_) { _juries = []; }
    try { _agents = await (await fetch('/api/agents')).json(); } catch (_) { _agents = []; }
  }

  function _esc(s) {
    return String(s == null ? '' : s).replace(/[&<>]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;' }[c]));
  }

  // Gesetz-/Favoriten-Agenten zuerst, dann alphabetisch
  function _sortedAgents() {
    return [..._agents].sort((a, b) => {
      const ra = (a.category === 'Recht' ? 0 : a.favorite ? 1 : 2);
      const rb = (b.category === 'Recht' ? 0 : b.favorite ? 1 : 2);
      return ra - rb || (a.name || '').localeCompare(b.name || '');
    });
  }

  // ── Verwaltung ───────────────────────────────────────────────────────────
  async function openManager() {
    await _fetchAll();
    _resetForm();
    _renderJuryList();
    _renderMemberPicker([]);
    _el('jury-modal-overlay').classList.add('active');
  }
  function _closeManager() { _el('jury-modal-overlay').classList.remove('active'); }

  function _renderJuryList() {
    const list = _el('jury-list');
    list.innerHTML = '';
    if (!_juries.length) {
      list.innerHTML = '<span class="planner-muted" style="font-size:12px">Noch keine Jury angelegt.</span>';
      return;
    }
    for (const j of _juries) {
      const row = document.createElement('div');
      row.style.cssText = 'display:flex;align-items:center;gap:8px;background:var(--bg-main);border:1px solid var(--border);border-radius:8px;padding:6px 10px;font-size:12.5px';
      row.innerHTML = `<strong>${_esc(j.name)}</strong> <span class="planner-muted">${(j.member_agent_ids || []).length} Mitglieder</span>`;
      const edit = document.createElement('button');
      edit.className = 'export-btn'; edit.textContent = '✏'; edit.style.marginLeft = 'auto';
      edit.addEventListener('click', () => _editJury(j));
      const del = document.createElement('button');
      del.className = 'export-btn'; del.textContent = '✕';
      del.addEventListener('click', () => _deleteJury(j.id));
      row.appendChild(edit); row.appendChild(del);
      list.appendChild(row);
    }
  }

  function _renderMemberPicker(selected) {
    const box = _el('jury-members');
    box.innerHTML = '';
    const sel = new Set(selected || []);
    for (const a of _sortedAgents()) {
      const lab = document.createElement('label');
      lab.style.cssText = 'display:flex;align-items:center;gap:8px;font-size:12.5px;cursor:pointer';
      const cb = document.createElement('input');
      cb.type = 'checkbox'; cb.value = a.id; cb.checked = sel.has(a.id);
      lab.appendChild(cb);
      lab.appendChild(document.createTextNode(`${a.icon || '🤖'} ${a.name}` + (a.category === 'Recht' ? '  ⚖️' : '')));
      box.appendChild(lab);
    }
  }

  function _selectedMembers() {
    return [...document.querySelectorAll('#jury-members input:checked')].map(cb => cb.value);
  }

  function _resetForm() {
    _editId = null;
    _el('jury-form-title').textContent = 'Neue Jury';
    _el('jury-name').value = '';
    _el('jury-desc').value = '';
    _el('jury-msg').textContent = '';
    _renderMemberPicker([]);
  }

  function _editJury(j) {
    _editId = j.id;
    _el('jury-form-title').textContent = 'Jury bearbeiten: ' + j.name;
    _el('jury-name').value = j.name || '';
    _el('jury-desc').value = j.description || '';
    _renderMemberPicker(j.member_agent_ids || []);
  }

  async function _saveJury() {
    const name = _el('jury-name').value.trim();
    const description = _el('jury-desc').value.trim();
    const member_agent_ids = _selectedMembers();
    const msg = _el('jury-msg');
    if (!name) { msg.textContent = 'Name fehlt.'; return; }
    if (!member_agent_ids.length) { msg.textContent = 'Mindestens ein Mitglied wählen.'; return; }
    try {
      const url = _editId ? '/api/juries/' + encodeURIComponent(_editId) : '/api/juries';
      const method = _editId ? 'PUT' : 'POST';
      const resp = await fetch(url, {
        method, headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name, description, member_agent_ids }),
      });
      if (!resp.ok) throw new Error((await resp.json()).detail || resp.statusText);
      msg.textContent = '✓ Gespeichert';
      await _fetchAll();
      _renderJuryList();
      _resetForm();
    } catch (e) { msg.textContent = 'Fehler: ' + e.message; }
  }

  async function _deleteJury(jid) {
    if (!confirm('Jury löschen?')) return;
    try {
      await fetch('/api/juries/' + encodeURIComponent(jid), { method: 'DELETE' });
      await _fetchAll();
      _renderJuryList();
    } catch (_) {}
  }

  // ── Wiederverwendbares Bewertungs-Overlay ──────────────────────────────────
  async function evaluate(text, opts) {
    opts = opts || {};
    text = (text || '').trim();
    if (!text) { if (typeof showToast === 'function') showToast('Kein Text zum Bewerten'); return; }
    _evalCtx = { text, context: opts.context || '' };
    await _fetchAll();
    _el('jury-eval-title').textContent = '⚖️ ' + (opts.title || 'Jury-Bewertung');
    const sel = _el('jury-eval-select');
    sel.innerHTML = '';
    if (!_juries.length) {
      sel.innerHTML = '<option value="">— keine Jury vorhanden (erst im Agenten-Tab anlegen) —</option>';
    } else {
      for (const j of _juries) {
        const opt = document.createElement('option');
        opt.value = j.id; opt.textContent = `${j.name} (${(j.member_agent_ids || []).length})`;
        sel.appendChild(opt);
      }
    }
    _el('jury-eval-body').innerHTML = '<p class="planner-muted" style="font-size:12.5px">Jury wählen und „▶ Bewerten".</p>';
    _el('jury-eval-overlay').classList.add('active');
  }
  function _closeEval() { if (!_running) _el('jury-eval-overlay').classList.remove('active'); }

  // Gemeinsamer Streamer: bewertet `text` mit Jury `jid` und rendert die Karten in
  // `bodyEl`. Liefert {members:[], summary} zurück (für „Bewertung mitspeichern").
  async function _streamEval(jid, text, context, bodyEl) {
    bodyEl.innerHTML = '';
    const out = { members: [], summary: null };
    const card = (html) => { const d = document.createElement('div'); d.className = 'jury-card'; d.innerHTML = html; bodyEl.appendChild(d); return d; };
    const resp = await fetch('/api/jury/evaluate', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ jury_id: jid, text, context: context || '' }),
    });
    if (!resp.ok || !resp.body) throw new Error('Bewertung fehlgeschlagen');
    const reader = resp.body.getReader();
    const dec = new TextDecoder();
    let buf = '';
    const pending = {};   // agent → card element (start → done)
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += dec.decode(value, { stream: true });
      let idx;
      while ((idx = buf.indexOf('\n\n')) >= 0) {
        const line = buf.slice(0, idx); buf = buf.slice(idx + 2);
        if (!line.startsWith('data:')) continue;
        let f; try { f = JSON.parse(line.slice(5).trim()); } catch (_) { continue; }
        if (f.type === 'member' && f.status === 'start') {
          pending[f.agent] = card(`<div class="jury-card-head">${f.icon || '⚖️'} <strong>${_esc(f.agent)}</strong> <span class="planner-muted">bewertet…</span></div>`);
        } else if (f.type === 'member' && f.status === 'done') {
          (pending[f.agent] || card('')).innerHTML = _memberHtml(f);
          out.members.push(f);
        } else if (f.type === 'member' && f.status === 'error') {
          const el = pending[f.agent] || card('');
          el.innerHTML = `<div class="jury-card-head">${f.icon || '⚖️'} <strong>${_esc(f.agent)}</strong></div><div style="color:var(--danger,#e66);font-size:12.5px">Fehler: ${_esc(f.message)}</div>`;
        } else if (f.type === 'summary') {
          card(_summaryHtml(f)).classList.add('jury-summary');
          out.summary = f;
        } else if (f.type === 'error') {
          card(`<div style="color:var(--danger,#e66)">${_esc(f.message)}</div>`);
        }
      }
    }
    return out;
  }

  async function _runEval() {
    if (_running || !_evalCtx) return;
    const jid = _el('jury-eval-select').value;
    if (!jid) { if (typeof showToast === 'function') showToast('Keine Jury gewählt'); return; }
    _running = true;
    _el('btn-jury-eval-run').disabled = true;
    try {
      await _streamEval(jid, _evalCtx.text, _evalCtx.context, _el('jury-eval-body'));
    } catch (e) {
      _el('jury-eval-body').innerHTML += `<div style="color:var(--danger,#e66)">Fehler: ${_esc(e.message)}</div>`;
    } finally {
      _running = false;
      _el('btn-jury-eval-run').disabled = false;
    }
  }

  // ── Jury-Tab: Dokument-Werkbank (sehen, bearbeiten, prüfen, speichern) ──────
  let _docs = [];
  let _curDocId = null;
  let _lastTabEval = null;

  async function openTab() {
    await _fetchAll();
    _renderTabJuryList();
    _fillDocJurySelect();
    await _loadDocs();
  }

  function _renderTabJuryList() {
    const box = _el('jury-tab-jurylist');
    if (!box) return;
    box.innerHTML = '';
    if (!_juries.length) {
      box.innerHTML = '<span class="planner-muted" style="font-size:12px">Noch keine Jury – „Jurys verwalten".</span>';
      return;
    }
    for (const j of _juries) {
      const row = document.createElement('div');
      row.className = 'jury-tab-listitem';
      row.innerHTML = `<span>⚖️ ${_esc(j.name)} <span class="planner-muted">(${(j.member_agent_ids || []).length})</span></span>`;
      row.addEventListener('click', () => { const s = _el('jury-doc-jury'); if (s) s.value = j.id; });
      box.appendChild(row);
    }
  }

  function _fillDocJurySelect() {
    const sel = _el('jury-doc-jury');
    if (!sel) return;
    const prev = sel.value;
    sel.innerHTML = '';
    if (!_juries.length) {
      sel.innerHTML = '<option value="">— keine Jury —</option>';
      return;
    }
    for (const j of _juries) {
      const opt = document.createElement('option');
      opt.value = j.id; opt.textContent = `${j.name} (${(j.member_agent_ids || []).length})`;
      sel.appendChild(opt);
    }
    if (prev) sel.value = prev;
  }

  async function _loadDocs() {
    try { _docs = await (await fetch('/api/jury-docs')).json(); } catch (_) { _docs = []; }
    _renderDocList();
  }

  function _renderDocList() {
    const box = _el('jury-tab-doclist');
    if (!box) return;
    box.innerHTML = '';
    if (!_docs.length) {
      box.innerHTML = '<span class="planner-muted" style="font-size:12px">Noch keine Dokumente.</span>';
      return;
    }
    for (const d of _docs) {
      const row = document.createElement('div');
      row.className = 'jury-tab-listitem' + (d.id === _curDocId ? ' active' : '');
      row.innerHTML = `<span>📄 ${_esc(d.name || 'Unbenannt')}</span>`;
      const del = document.createElement('button');
      del.className = 'export-btn'; del.textContent = '✕'; del.style.marginLeft = 'auto';
      del.addEventListener('click', (e) => { e.stopPropagation(); _deleteDoc(d.id); });
      row.addEventListener('click', () => _loadDoc(d.id));
      row.appendChild(del);
      box.appendChild(row);
    }
  }

  async function _loadDoc(id) {
    try {
      const d = await (await fetch('/api/jury-docs/' + encodeURIComponent(id))).json();
      _curDocId = d.id;
      _lastTabEval = d.evaluation || null;
      if (_el('jury-doc-name')) _el('jury-doc-name').value = d.name || '';
      if (_el('jury-doc-text')) _el('jury-doc-text').value = d.text || '';
      _el('jury-tab-eval').innerHTML = '';
      _refreshPreview();
      _renderDocList();
    } catch (e) { if (typeof showToast === 'function') showToast('Laden fehlgeschlagen: ' + e.message); }
  }

  function _newDoc() {
    _curDocId = null; _lastTabEval = null;
    if (_el('jury-doc-name')) _el('jury-doc-name').value = '';
    if (_el('jury-doc-text')) _el('jury-doc-text').value = '';
    _el('jury-tab-eval').innerHTML = '';
    _refreshPreview();
    _renderDocList();
    _el('jury-doc-text')?.focus();
  }

  async function _saveDoc() {
    const name = (_el('jury-doc-name')?.value || '').trim() || 'Unbenannt';
    const text = _el('jury-doc-text')?.value || '';
    try {
      const d = await (await fetch('/api/jury-docs', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ id: _curDocId, name, text, evaluation: _lastTabEval }),
      })).json();
      _curDocId = d.id;
      await _loadDocs();
      if (typeof showToast === 'function') showToast('✓ Dokument gespeichert');
    } catch (e) { if (typeof showToast === 'function') showToast('Speichern fehlgeschlagen: ' + e.message); }
  }

  async function _deleteDoc(id) {
    if (!confirm('Dokument löschen?')) return;
    try {
      await fetch('/api/jury-docs/' + encodeURIComponent(id), { method: 'DELETE' });
      if (id === _curDocId) _newDoc();
      await _loadDocs();
    } catch (_) {}
  }

  async function _checkDoc() {
    if (_running) return;
    const text = (_el('jury-doc-text')?.value || '').trim();
    if (!text) { if (typeof showToast === 'function') showToast('Kein Text zum Bewerten'); return; }
    const jid = _el('jury-doc-jury')?.value;
    if (!jid) { if (typeof showToast === 'function') showToast('Keine Jury gewählt'); return; }
    _running = true;
    _el('btn-jury-doc-check').disabled = true;
    try {
      const res = await _streamEval(jid, text, '', _el('jury-tab-eval'));
      _lastTabEval = res.summary || null;
    } catch (e) {
      _el('jury-tab-eval').innerHTML += `<div style="color:var(--danger,#e66)">Fehler: ${_esc(e.message)}</div>`;
    } finally {
      _running = false;
      _el('btn-jury-doc-check').disabled = false;
    }
  }

  function _refreshPreview() {
    const on = _el('jury-doc-preview-toggle')?.checked;
    const ta = _el('jury-doc-text');
    const pv = _el('jury-doc-preview');
    if (!ta || !pv) return;
    if (on) {
      ta.style.display = 'none';
      pv.style.display = 'block';
      if (typeof marked !== 'undefined') {
        if (window._ensureKatexMarked) window._ensureKatexMarked();
        pv.innerHTML = marked.parse(ta.value || '', { gfm: true, breaks: true });
        pv.querySelectorAll('a[href]').forEach(a => { a.target = '_blank'; a.rel = 'noopener noreferrer'; });
      } else { pv.textContent = ta.value || ''; }
    } else {
      ta.style.display = 'block';
      pv.style.display = 'none';
    }
  }

  function _docDocx() {
    const name = (_el('jury-doc-name')?.value || 'Dokument').trim();
    const text = _el('jury-doc-text')?.value || '';
    fetch('/api/export/docx', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ type: 'document', title: name, content: text }),
    }).then(r => r.blob()).then(blob => {
      const a = document.createElement('a');
      a.href = URL.createObjectURL(blob); a.download = (name || 'dokument') + '.docx';
      a.click(); URL.revokeObjectURL(a.href);
    }).catch(e => { if (typeof showToast === 'function') showToast('Export fehlgeschlagen: ' + e.message); });
  }

  function _docToDoku() {
    const text = _el('jury-doc-text')?.value || '';
    if (typeof DocGen === 'undefined') { if (typeof showToast === 'function') showToast('Dokumente-Tab nicht verfügbar'); return; }
    DocGen.showResult(text);
    if (typeof switchTab === 'function') switchTab('docgen');
  }

  function _docToRag() {
    const name = (_el('jury-doc-name')?.value || 'Dokument').trim();
    const text = _el('jury-doc-text')?.value || '';
    if (typeof RAG === 'undefined' || !RAG.ingestText) { if (typeof showToast === 'function') showToast('RAG nicht verfügbar'); return; }
    RAG.ingestText(name, text);
  }

  // Lädt einen extern erzeugten Text (z. B. aus DocGen) in den Jury-Tab.
  function loadDocument(name, text) {
    if (typeof switchTab === 'function') switchTab('jury');
    _newDoc();
    if (_el('jury-doc-name')) _el('jury-doc-name').value = name || 'Dokument';
    if (_el('jury-doc-text')) _el('jury-doc-text').value = text || '';
    _refreshPreview();
  }

  // Ziehbarer Trenner im Jury-Tab (Planer-Muster)
  const _JT_SPLIT_KEY = 'jury_tab_left_w';
  function _initTabSplitter() {
    const splitter = _el('jury-tab-splitter');
    const left = _el('jury-tab-left');
    const body = _el('jury-tab-body');
    if (!splitter || !left || !body) return;
    const saved = parseInt(localStorage.getItem(_JT_SPLIT_KEY) || '', 10);
    if (saved > 0) left.style.width = saved + 'px';
    const _apply = (clientX) => {
      const rect = body.getBoundingClientRect();
      let w = clientX - rect.left;
      const max = rect.width - 320;
      w = Math.max(200, Math.min(w, max));
      left.style.width = w + 'px';
    };
    const _onMove = (e) => _apply(e.clientX);
    const _onUp = () => {
      splitter.classList.remove('dragging');
      document.body.style.userSelect = '';
      document.removeEventListener('mousemove', _onMove);
      document.removeEventListener('mouseup', _onUp);
      localStorage.setItem(_JT_SPLIT_KEY, String(parseInt(left.style.width, 10) || 0));
    };
    splitter.addEventListener('mousedown', (e) => {
      e.preventDefault();
      splitter.classList.add('dragging');
      document.body.style.userSelect = 'none';
      document.addEventListener('mousemove', _onMove);
      document.addEventListener('mouseup', _onUp);
    });
    splitter.addEventListener('dblclick', () => {
      left.style.width = '';
      localStorage.removeItem(_JT_SPLIT_KEY);
    });
  }

  function _scoreColor(s) {
    if (typeof s !== 'number') return 'var(--text-dim)';
    if (s >= 70) return '#3ba55d';
    if (s >= 40) return '#d9a23b';
    return '#e0594b';
  }

  function _memberHtml(f) {
    let h = `<div class="jury-card-head">${f.icon || '⚖️'} <strong>${_esc(f.agent)}</strong>`;
    if (typeof f.score === 'number') h += ` <span class="jury-score" style="color:${_scoreColor(f.score)}">${f.score}</span>`;
    h += '</div>';
    if (f.befund) h += `<p class="jury-befund">${_esc(f.befund)}</p>`;
    if ((f.risiken || []).length) h += '<ul class="jury-risks">' + f.risiken.map(r => `<li>${_esc(r)}</li>`).join('') + '</ul>';
    if (f.empfehlung) h += `<p class="jury-emp"><strong>Empfehlung:</strong> ${_esc(f.empfehlung)}</p>`;
    return h;
  }

  function _summaryHtml(f) {
    let h = `<div class="jury-card-head">🏛️ <strong>Gesamturteil</strong>`;
    if (typeof f.score === 'number') h += ` <span class="jury-score" style="color:${_scoreColor(f.score)}">${f.score}</span>`;
    h += '</div>';
    if (f.gesamturteil) h += `<p class="jury-befund">${_esc(f.gesamturteil)}</p>`;
    if (f.konsens) h += `<p style="font-size:12.5px"><strong>Konsens:</strong> ${_esc(f.konsens)}</p>`;
    if ((f.hauptkritik || []).length) h += '<div class="jury-sub">Hauptkritik</div><ul class="jury-risks">' + f.hauptkritik.map(r => `<li>${_esc(r)}</li>`).join('') + '</ul>';
    if ((f.empfehlungen || []).length) h += '<div class="jury-sub">Empfehlungen</div><ul class="jury-risks">' + f.empfehlungen.map(r => `<li>${_esc(r)}</li>`).join('') + '</ul>';
    return h;
  }

  function init() {
    _el('btn-juries')?.addEventListener('click', openManager);
    _el('btn-jury-close')?.addEventListener('click', _closeManager);
    _el('btn-jury-save')?.addEventListener('click', _saveJury);
    _el('btn-jury-new')?.addEventListener('click', _resetForm);
    _el('jury-modal-overlay')?.addEventListener('click', e => { if (e.target === _el('jury-modal-overlay')) _closeManager(); });
    _el('btn-jury-eval-close')?.addEventListener('click', _closeEval);
    _el('btn-jury-eval-run')?.addEventListener('click', _runEval);
    _el('jury-eval-overlay')?.addEventListener('click', e => { if (e.target === _el('jury-eval-overlay')) _closeEval(); });

    // Jury-Tab (Dokument-Werkbank)
    _el('btn-jury-tab-manage')?.addEventListener('click', openManager);
    _el('btn-jury-tab-newdoc')?.addEventListener('click', _newDoc);
    _el('btn-jury-doc-check')?.addEventListener('click', _checkDoc);
    _el('btn-jury-doc-save')?.addEventListener('click', _saveDoc);
    _el('btn-jury-doc-docx')?.addEventListener('click', _docDocx);
    _el('btn-jury-doc-doku')?.addEventListener('click', _docToDoku);
    _el('btn-jury-doc-rag')?.addEventListener('click', _docToRag);
    _el('jury-doc-preview-toggle')?.addEventListener('change', _refreshPreview);
    // Tab-Button öffnet die Werkbank (zusätzlich zum generischen switchTab)
    document.querySelector('.tab-btn[data-tab="jury"]')?.addEventListener('click', openTab);
    _initTabSplitter();
  }

  return { init, evaluate, openManager, openTab, loadDocument };
})();
