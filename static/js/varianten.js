/* ── Variantenvergleich (gewichtete Entscheidung, AHP-Hybrid) ─────────────────
 *
 * Paarvergleich der Kriterien → Gewichte + Konsistenz (CR); Varianten je Kriterium
 * direkt auf 1–10 bewertet → gewichtete Nutzwertsumme + Ranking. Alle Zahlen kommen
 * deterministisch vom Server (POST/PUT liefert `result`); die KI-Knöpfe füllen nur
 * Kriterien/Varianten/Urteile vor. Persistenz je Vergleich in data/varianten/<name>/.
 */
const Varianten = (() => {
  let _name = '';
  let _data = null;          // {title, description, criteria[], variants[], pairwise[][], ratings[][], result}
  let _saveTimer = null;

  // Saaty-Auswahlwerte für den Paarvergleich (Zeile wichtiger als Spalte → >1).
  // Voller Bereich inkl. Zwischenwerte (auch KI-Vorschläge landen so exakt im Menü).
  const SAATY = (() => {
    const out = [];
    for (let k = 9; k >= 2; k--) out.push({ v: 1 / k, t: '1/' + k });
    out.push({ v: 1, t: '1' });
    for (let k = 2; k <= 9; k++) out.push({ v: k, t: String(k) });
    return out;
  })();

  // Nächstgelegener Menüwert (damit ein beliebiger Roh-/KI-Wert im Dropdown erscheint)
  function _nearestSaaty(v) {
    let best = SAATY[0].v, d = Infinity;
    for (const s of SAATY) { const dd = Math.abs(Math.log(s.v) - Math.log(v || 1)); if (dd < d) { d = dd; best = s.v; } }
    return best;
  }

  function _el(id) { return document.getElementById(id); }
  function _model() { return (typeof Profile !== 'undefined' && Profile.modelFor) ? Profile.modelFor('general') : ''; }
  function _spin(on) { const s = _el('var-spin'); if (s) s.style.display = on ? '' : 'none'; }
  function _status(t) { const s = _el('var-status'); if (s) s.textContent = t || ''; }
  function _tok(tokens, label) {
    if (tokens && typeof TokenMeter !== 'undefined') TokenMeter.add(tokens, label || 'Variantenvergleich');
  }

  async function _api(method, url, body) {
    const opt = { method, headers: { 'Content-Type': 'application/json' } };
    if (body !== undefined) opt.body = JSON.stringify(body);
    const r = await fetch(url, opt);
    if (!r.ok) {
      let msg = 'HTTP ' + r.status;
      try { msg = (await r.json()).detail || msg; } catch (_) {}
      throw new Error(msg);
    }
    return r.json();
  }

  // ── Laden / Anlegen / Löschen ──────────────────────────────────────────────
  async function _loadList(select) {
    try {
      const list = await _api('GET', '/api/varianten/projects');
      const sel = _el('var-project');
      sel.innerHTML = '<option value="">— Vergleich wählen —</option>' +
        list.map(p => `<option value="${escHtml(p.name)}">${escHtml(p.title || p.name)}</option>`).join('');
      if (select) sel.value = select;
    } catch (e) { _status('Fehler: ' + e.message); }
  }

  async function _open(name) {
    if (!name) { _name = ''; _data = null; _el('var-editor').style.display = 'none'; _el('var-empty').style.display = 'block'; return; }
    _spin(true);
    try {
      _data = await _api('GET', '/api/varianten/projects/' + encodeURIComponent(name));
      _name = name;
      _el('var-empty').style.display = 'none';
      _el('var-editor').style.display = 'block';
      _render();
    } catch (e) { _status('Fehler: ' + e.message); }
    finally { _spin(false); }
  }

  async function _create() {
    const nm = (_el('var-new-project').value || '').trim();
    if (!nm) return;
    _spin(true);
    try {
      await _api('POST', '/api/varianten/projects', { name: nm, title: nm });
      _el('var-new-project').value = '';
      await _loadList(nm);
      await _open(nm);
    } catch (e) { _status('Fehler: ' + e.message); }
    finally { _spin(false); }
  }

  async function _delete() {
    if (!_name || !confirm(`Vergleich „${_name}" löschen?`)) return;
    await _api('DELETE', '/api/varianten/projects/' + encodeURIComponent(_name));
    _name = ''; _data = null;
    _el('var-editor').style.display = 'none'; _el('var-empty').style.display = 'block';
    _el('var-project').value = '';
    await _loadList();
  }

  // ── Speichern (debounced) → Server rechnet result ──────────────────────────
  function _collect() {
    _data.title = _el('var-title').value.trim();
    _data.description = _el('var-desc').value.trim();
  }

  function _saveSoon() { clearTimeout(_saveTimer); _saveTimer = setTimeout(_save, 350); }

  async function _save() {
    if (!_name || !_data) return;
    try {
      const saved = await _api('PUT', '/api/varianten/projects/' + encodeURIComponent(_name), {
        title: _data.title, description: _data.description,
        criteria: _data.criteria, variants: _data.variants,
        pairwise: _data.pairwise, ratings: _data.ratings,
      });
      _data.result = saved.result;
      _renderResult();
      _renderCR();
      _loadList(_name);   // Titel in der Auswahl aktualisieren
    } catch (e) { _status('Speichern: ' + e.message); }
  }

  // Matrizen an die aktuelle Kriterien-/Variantenzahl anpassen (Werte erhalten)
  function _resizeMatrices() {
    const nc = _data.criteria.length, nv = _data.variants.length;
    // pairwise nc×nc (Diagonale 1)
    const pw = [];
    for (let i = 0; i < nc; i++) {
      const row = [];
      for (let j = 0; j < nc; j++) {
        if (i === j) { row.push(1); continue; }
        // nur die obere Dreiecksmatrix auf einen Menüwert einrasten; untere = Kehrwert
        const raw = (_data.pairwise[i] && _data.pairwise[i][j]) || 1;
        row.push(i < j ? _nearestSaaty(raw) : 0);   // untere gleich unten füllen
      }
      pw.push(row);
    }
    for (let i = 0; i < nc; i++) for (let j = i + 1; j < nc; j++) pw[j][i] = 1 / pw[i][j];
    _data.pairwise = pw;
    // ratings nv×nc (Standard 5)
    const rt = [];
    for (let v = 0; v < nv; v++) {
      const row = [];
      for (let c = 0; c < nc; c++) {
        row.push((_data.ratings[v] && _data.ratings[v][c] != null) ? _data.ratings[v][c] : 5);
      }
      rt.push(row);
    }
    _data.ratings = rt;
  }

  // ── Rendering ──────────────────────────────────────────────────────────────
  function _render() {
    _el('var-title').value = _data.title || '';
    _el('var-desc').value = _data.description || '';
    _resizeMatrices();
    _renderCriteria();
    _renderPairwise();
    _renderVariants();
    _renderRatings();
    _renderResult();
    _renderCR();
    _el('var-explain').innerHTML = '';
  }

  function _renderCriteria() {
    const host = _el('var-criteria');
    host.innerHTML = _data.criteria.map((c, i) => `
      <div class="var-row" data-i="${i}">
        <input type="text" class="var-input var-crit-name" data-i="${i}" value="${escHtml(c.name || '')}" placeholder="Kriterium" />
        <select class="var-crit-dir" data-i="${i}" title="Ist ein hoher Wert gut oder schlecht?">
          <option value="benefit" ${c.direction !== 'cost' ? 'selected' : ''}>höher = besser</option>
          <option value="cost" ${c.direction === 'cost' ? 'selected' : ''}>höher = schlechter</option>
        </select>
        <button class="export-btn btn-danger-sm var-del-crit" data-i="${i}" title="Kriterium entfernen">✕</button>
      </div>`).join('') || '<span class="planner-muted">Noch keine Kriterien.</span>';
    host.querySelectorAll('.var-crit-name').forEach(inp => {
      inp.addEventListener('input', e => { _data.criteria[+e.target.dataset.i].name = e.target.value; });
      inp.addEventListener('change', () => { _renderPairwise(); _renderRatings(); _saveSoon(); });
    });
    host.querySelectorAll('.var-crit-dir').forEach(sel => sel.addEventListener('change', e => {
      _data.criteria[+e.target.dataset.i].direction = e.target.value; _saveSoon();
    }));
    host.querySelectorAll('.var-del-crit').forEach(b => b.addEventListener('click', e => {
      _data.criteria.splice(+e.target.dataset.i, 1); _resizeMatrices(); _render(); _saveSoon();
    }));
  }

  function _renderVariants() {
    const host = _el('var-variants');
    host.innerHTML = _data.variants.map((v, i) => `
      <div class="var-row" data-i="${i}">
        <input type="text" class="var-input var-var-name" data-i="${i}" value="${escHtml(v.name || '')}" placeholder="Variante" />
        <input type="text" class="var-input var-var-desc" data-i="${i}" value="${escHtml(v.description || '')}" placeholder="Kurzbeschreibung (hilft der KI-Bewertung)" />
        <button class="export-btn btn-danger-sm var-del-var" data-i="${i}" title="Variante entfernen">✕</button>
      </div>`).join('') || '<span class="planner-muted">Noch keine Varianten.</span>';
    host.querySelectorAll('.var-var-name').forEach(inp => {
      inp.addEventListener('input', e => { _data.variants[+e.target.dataset.i].name = e.target.value; });
      inp.addEventListener('change', () => { _renderRatings(); _renderResult(); _saveSoon(); });
    });
    host.querySelectorAll('.var-var-desc').forEach(inp => inp.addEventListener('change', e => {
      _data.variants[+e.target.dataset.i].description = e.target.value; _saveSoon();
    }));
    host.querySelectorAll('.var-del-var').forEach(b => b.addEventListener('click', e => {
      _data.variants.splice(+e.target.dataset.i, 1); _resizeMatrices(); _render(); _saveSoon();
    }));
  }

  function _saatySelect(i, j, val) {
    const near = _nearestSaaty(val);
    const opts = SAATY.map(s =>
      `<option value="${s.v}" ${Math.abs(s.v - near) < 1e-6 ? 'selected' : ''}>${s.t}</option>`).join('');
    return `<select class="var-pw" data-i="${i}" data-j="${j}">${opts}</select>`;
  }

  function _renderPairwise() {
    const host = _el('var-pairwise');
    const cr = _data.criteria;
    if (cr.length < 2) { host.innerHTML = '<span class="planner-muted">Mindestens zwei Kriterien für den Paarvergleich.</span>'; return; }
    let html = '<table class="var-matrix"><thead><tr><th></th>' +
      cr.map(c => `<th>${escHtml(c.name || '?')}</th>`).join('') + '</tr></thead><tbody>';
    for (let i = 0; i < cr.length; i++) {
      html += `<tr><th>${escHtml(cr[i].name || '?')}</th>`;
      for (let j = 0; j < cr.length; j++) {
        if (i === j) html += '<td class="var-diag">1</td>';
        else if (i < j) html += `<td>${_saatySelect(i, j, (_data.pairwise[i] && _data.pairwise[i][j]) || 1)}</td>`;
        else html += `<td class="var-recip">${_fmtRecip((_data.pairwise[i] && _data.pairwise[i][j]) || 1)}</td>`;
      }
      html += '</tr>';
    }
    host.innerHTML = html + '</tbody></table>';
    host.querySelectorAll('.var-pw').forEach(sel => sel.addEventListener('change', e => {
      const i = +e.target.dataset.i, j = +e.target.dataset.j, v = parseFloat(e.target.value);
      _data.pairwise[i][j] = v;
      _data.pairwise[j][i] = 1 / v;
      _renderPairwise();
      _saveSoon();
    }));
  }

  function _fmtRecip(v) {
    if (Math.abs(v - 1) < 1e-6) return '1';
    if (v < 1) return '1/' + Math.round(1 / v);
    return String(Math.round(v));
  }

  function _renderRatings() {
    const host = _el('var-ratings');
    const cr = _data.criteria, vr = _data.variants;
    if (!cr.length || !vr.length) { host.innerHTML = '<span class="planner-muted">Kriterien und Varianten anlegen, dann bewerten.</span>'; return; }
    let html = '<table class="var-matrix"><thead><tr><th>Variante \\ Kriterium</th>' +
      cr.map(c => `<th>${escHtml(c.name || '?')}${c.direction === 'cost' ? ' ↓' : ''}</th>`).join('') + '</tr></thead><tbody>';
    for (let v = 0; v < vr.length; v++) {
      html += `<tr><th>${escHtml(vr[v].name || '?')}</th>`;
      for (let c = 0; c < cr.length; c++) {
        html += `<td><input type="number" class="var-rt" data-v="${v}" data-c="${c}" min="1" max="10" step="1" value="${(_data.ratings[v] && _data.ratings[v][c]) || 5}" /></td>`;
      }
      html += '</tr>';
    }
    host.innerHTML = html + '</tbody></table>';
    host.querySelectorAll('.var-rt').forEach(inp => inp.addEventListener('change', e => {
      let val = parseFloat(e.target.value);
      if (isNaN(val)) val = 5;
      val = Math.max(1, Math.min(10, val));
      e.target.value = val;
      _data.ratings[+e.target.dataset.v][+e.target.dataset.c] = val;
      _saveSoon();
    }));
  }

  function _renderCR() {
    const el = _el('var-cr');
    const r = _data.result || {};
    if (!_data.criteria || _data.criteria.length < 3) { el.innerHTML = ''; return; }
    const cr = r.cr != null ? r.cr : 0;
    const ok = r.consistent !== false;
    let html = `<span>Konsistenz CR = ${cr.toFixed(2)} ${ok ? '✓' : '⚠ zu inkonsistent (>0,10)'}</span>`;
    // Bei Inkonsistenz das strittigste Urteil benennen + gezielt neu bewerten anbieten
    const wp = r.worst_pair;
    if (!ok && wp && _data.criteria[wp.i] && _data.criteria[wp.j]) {
      const a = escHtml(_data.criteria[wp.i].name || ('Kriterium ' + (wp.i + 1)));
      const b = escHtml(_data.criteria[wp.j].name || ('Kriterium ' + (wp.j + 1)));
      html += ` <button class="export-btn var-cr-fix" data-i="${wp.i}" data-j="${wp.j}" ` +
        `title="Dieses Urteil weicht am stärksten von den übrigen ab">⚠ ${a} ↔ ${b} — erneut bewerten</button>`;
    }
    el.innerHTML = html;
    el.className = 'var-cr ' + (ok ? 'var-cr-ok' : 'var-cr-bad');
    const fix = el.querySelector('.var-cr-fix');
    if (fix) fix.addEventListener('click', () => _openStepwisePairs([[+fix.dataset.i, +fix.dataset.j]]));
  }

  function _renderResult() {
    const host = _el('var-result');
    const r = _data.result || {};
    const ranking = r.ranking || [];
    const vr = _data.variants || [];
    if (!ranking.length) { host.innerHTML = '<span class="planner-muted">Ergebnis erscheint, sobald Kriterien, Gewichte und Bewertungen vorliegen.</span>'; return; }
    const weights = r.weights || [];
    const wLine = _data.criteria.map((c, i) =>
      `${escHtml(c.name || '?')} ${((weights[i] || 0) * 100).toFixed(0)}%`).join(' · ');
    let html = `<div class="planner-muted" style="font-size:11.5px;margin-bottom:6px">Gewichte: ${wLine}</div>`;
    html += '<table class="var-rank">';
    ranking.forEach((row, idx) => {
      const v = vr[row.index] || {};
      html += `<tr class="${idx === 0 ? 'var-winner' : ''}">
        <td class="var-rank-pos">${idx + 1}.</td>
        <td class="var-rank-name">${idx === 0 ? '🏆 ' : ''}${escHtml(v.name || '?')}</td>
        <td class="var-rank-bar"><div class="var-bar" style="width:${row.percent}%"></div></td>
        <td class="var-rank-score">${row.score.toFixed(2)} <span class="planner-muted">(${row.percent}%)</span></td>
      </tr>`;
    });
    host.innerHTML = html + '</table>';
  }

  // ── KI-Helfer ──────────────────────────────────────────────────────────────
  async function _ai(url, body, onDone) {
    _spin(true); _status('KI…');
    try {
      const res = await _api('POST', url, Object.assign({ model: _model() }, body));
      _tok(res.tokens);
      onDone(res);
      _status('');
    } catch (e) { _status('KI-Fehler: ' + e.message); }
    finally { _spin(false); }
  }

  function _aiCriteria() {
    _collect();
    _ai('/api/varianten/suggest-criteria', { title: _data.title, description: _data.description }, res => {
      if (res.criteria && res.criteria.length) {
        _data.criteria = res.criteria; _resizeMatrices(); _render(); _save();
      }
    });
  }
  function _aiVariants() {
    _collect();
    _ai('/api/varianten/suggest-variants', { title: _data.title, description: _data.description, criteria: _data.criteria }, res => {
      if (res.variants && res.variants.length) {
        _data.variants = res.variants; _resizeMatrices(); _render(); _save();
      }
    });
  }
  function _aiPairwise() {
    _collect();
    if (_data.criteria.length < 2) { _status('Erst Kriterien anlegen.'); return; }
    _ai('/api/varianten/suggest-pairwise', { title: _data.title, criteria: _data.criteria }, res => {
      if (res.pairwise && res.pairwise.length) { _data.pairwise = res.pairwise; _renderPairwise(); _save(); }
    });
  }
  function _aiRatings() {
    _collect();
    if (!_data.criteria.length || !_data.variants.length) { _status('Erst Kriterien und Varianten anlegen.'); return; }
    _ai('/api/varianten/suggest-ratings', { title: _data.title, criteria: _data.criteria, variants: _data.variants }, res => {
      if (res.ratings && res.ratings.length) { _data.ratings = res.ratings; _renderRatings(); _save(); }
    });
  }
  function _aiExplain() {
    _ai('/api/varianten/explain', { name: _name }, res => {
      _el('var-explain').innerHTML = (typeof marked !== 'undefined')
        ? marked.parse(res.text || '') : escHtml(res.text || '');
    });
  }

  function _exportCsv() {
    if (!_data) return;
    const r = _data.result || {};
    const rows = [['Rang', 'Variante', 'Nutzwert', 'Prozent']];
    (r.ranking || []).forEach((row, i) => {
      const v = _data.variants[row.index] || {};
      rows.push([i + 1, v.name || '', row.score.toFixed(3), row.percent]);
    });
    const csv = '﻿' + rows.map(r => r.map(c => `"${String(c).replace(/"/g, '""')}"`).join(';')).join('\r\n');
    const a = document.createElement('a');
    a.href = URL.createObjectURL(new Blob([csv], { type: 'text/csv' }));
    a.download = (_name || 'variantenvergleich') + '.csv';
    a.click();
  }

  // ── Schnellvergleich (Wischtechnik) ────────────────────────────────────────
  // Iteriert die obere Dreiecksmatrix der Kriterienpaare (i<j). Feste Stärke:
  // ← linke wichtiger (Saaty 3), → rechte wichtiger (1/3), ↑ gleich (1).
  const _SWIPE_WIN = 3;
  let _swipePairs = [], _swipeIdx = 0, _swipeKeyHandler = null;

  function _openSwipe() {
    if (!_data || _data.criteria.length < 2) { _status('Mindestens zwei Kriterien nötig.'); return; }
    _swipePairs = [];
    for (let i = 0; i < _data.criteria.length; i++)
      for (let j = i + 1; j < _data.criteria.length; j++) _swipePairs.push([i, j]);
    _swipeIdx = 0;
    _el('var-swipe').style.display = 'flex';
    _swipeKeyHandler = (e) => {
      if (e.key === 'ArrowLeft') { e.preventDefault(); _swipeAnswer('left'); }
      else if (e.key === 'ArrowRight') { e.preventDefault(); _swipeAnswer('right'); }
      else if (e.key === 'ArrowUp') { e.preventDefault(); _swipeAnswer('up'); }
      else if (e.key === 'Escape') { e.preventDefault(); _closeSwipe(); }
    };
    document.addEventListener('keydown', _swipeKeyHandler);
    _renderSwipe();
  }

  function _renderSwipe() {
    const pair = _swipePairs[_swipeIdx];
    if (!pair) { _finishSwipe(); return; }
    const [i, j] = pair;
    _el('var-swipe-a').textContent = _data.criteria[i].name || 'Kriterium ' + (i + 1);
    _el('var-swipe-b').textContent = _data.criteria[j].name || 'Kriterium ' + (j + 1);
    _el('var-swipe-progress').textContent = `${_swipeIdx + 1} / ${_swipePairs.length}`;
  }

  function _swipeAnswer(dir) {
    const pair = _swipePairs[_swipeIdx];
    if (!pair) return;
    const [i, j] = pair;
    let v = 1;                       // ↑ gleich
    if (dir === 'left') v = _SWIPE_WIN;         // linke (i) wichtiger
    else if (dir === 'right') v = 1 / _SWIPE_WIN; // rechte (j) wichtiger
    _data.pairwise[i][j] = v;
    _data.pairwise[j][i] = 1 / v;
    _swipeIdx++;
    _renderSwipe();
  }

  function _finishSwipe() {
    _closeSwipe();
    _renderPairwise();
    _save();
    _status('Schnellvergleich übernommen – Feinschliff in der Matrix möglich.');
  }

  function _closeSwipe() {
    _el('var-swipe').style.display = 'none';
    if (_swipeKeyHandler) { document.removeEventListener('keydown', _swipeKeyHandler); _swipeKeyHandler = null; }
  }

  // ── Schritt-für-Schritt (inkrementeller Paarvergleich) ─────────────────────
  // Merkmal eingeben → gegen jedes bereits vorhandene abfragen. Erst Stärke 3
  // (Standard „etwas"), pro Urteil optional feinere Stärke (5/7/9). Beliebig oft
  // wiederholbar. Läuft über dasselbe _data.pairwise + _save wie die Matrix.
  let _stepQueue = [], _stepIdx = 0, _stepStrength = 3, _stepOnDone = null;

  function _stepPhase(which) {
    _el('var-stepwise-add').style.display = which === 'add' ? 'block' : 'none';
    _el('var-stepwise-q').style.display = which === 'q' ? 'block' : 'none';
  }

  function _renderStepList() {
    const el = _el('var-stepwise-list');
    if (!el) return;
    const names = (_data.criteria || []).map((c, i) => `${i + 1}. ${escHtml(c.name || '—')}`);
    el.innerHTML = names.length
      ? 'Merkmale: ' + names.join(' · ')
      : 'Noch keine Merkmale — das erste eingeben.';
  }

  function _openStepwise() {
    if (!_data) { _status('Erst einen Vergleich anlegen/öffnen.'); return; }
    _stepQueue = []; _stepIdx = 0; _stepStrength = 3;
    _el('var-stepwise').style.display = 'flex';
    _stepPhase('add');
    _renderStepList();
    _highlightStrength();
    const inp = _el('var-stepwise-input');
    if (inp) { inp.value = ''; setTimeout(() => inp.focus(), 30); }
  }

  // Direkt in die Frage-Phase mit einer festen Paarliste (z. B. „erneut bewerten")
  function _openStepwisePairs(pairs) {
    if (!_data) { _status('Erst einen Vergleich anlegen/öffnen.'); return; }
    _stepQueue = pairs.slice(); _stepIdx = 0; _stepStrength = 3;
    _el('var-stepwise').style.display = 'flex';
    _highlightStrength();
    _renderStepQuestion();
  }

  function _stepAddCriterion() {
    const inp = _el('var-stepwise-input');
    const nm = (inp && inp.value || '').trim();
    if (!nm) return;
    _data.criteria.push({ name: nm, direction: 'benefit' });
    _resizeMatrices();
    const k = _data.criteria.length - 1;
    if (inp) { inp.value = ''; inp.focus(); }
    _renderStepList();
    // Neues Merkmal gegen jedes vorherige abfragen (neu vs. alle bisherigen)
    if (k >= 1) {
      _stepQueue = [];
      for (let x = 0; x < k; x++) _stepQueue.push([k, x]);
      _stepIdx = 0;
      _stepStrength = 3; _highlightStrength();
      _renderStepQuestion();
    } else {
      _saveSoon();   // erstes Merkmal: nichts zu fragen
    }
  }

  function _renderStepQuestion() {
    const pair = _stepQueue[_stepIdx];
    if (!pair) { _finishStepQueue(); return; }
    const [i, j] = pair;
    _stepPhase('q');
    _el('var-stepwise-a').textContent = _data.criteria[i] ? (_data.criteria[i].name || 'Merkmal ' + (i + 1)) : '?';
    _el('var-stepwise-b').textContent = _data.criteria[j] ? (_data.criteria[j].name || 'Merkmal ' + (j + 1)) : '?';
    _el('var-stepwise-progress').textContent = `${_stepIdx + 1} / ${_stepQueue.length}`;
  }

  function _highlightStrength() {
    document.querySelectorAll('#var-stepwise .var-strength-btn').forEach(b => {
      b.classList.toggle('active', +b.dataset.s === _stepStrength);
    });
  }

  // dir: 'more' (i wichtiger als j), 'less' (i unwichtiger), 'eq' (gleich)
  function _stepAnswer(dir) {
    const pair = _stepQueue[_stepIdx];
    if (!pair) return;
    const [i, j] = pair;
    let v = 1;
    if (dir === 'more') v = _stepStrength;
    else if (dir === 'less') v = 1 / _stepStrength;
    _data.pairwise[i][j] = v;
    _data.pairwise[j][i] = 1 / v;
    _stepIdx++;
    _stepStrength = 3; _highlightStrength();   // Stärke je Frage zurück auf Standard
    _renderStepQuestion();
  }

  function _finishStepQueue() {
    _stepQueue = []; _stepIdx = 0;
    _renderPairwise();
    _save();
    _stepPhase('add');
    _renderStepList();
    const inp = _el('var-stepwise-input');
    if (inp) setTimeout(() => inp.focus(), 30);
  }

  function _stepRepeat() {
    if (!_data || _data.criteria.length < 2) { _status('Mindestens zwei Merkmale nötig.'); return; }
    _stepQueue = [];
    for (let i = 0; i < _data.criteria.length; i++)
      for (let j = i + 1; j < _data.criteria.length; j++) _stepQueue.push([i, j]);
    _stepIdx = 0; _stepStrength = 3; _highlightStrength();
    _renderStepQuestion();
  }

  function _stepDone() {
    _el('var-stepwise').style.display = 'none';
    _render();
    _save();
    if (typeof _stepOnDone === 'function') { const cb = _stepOnDone; _stepOnDone = null; cb(_data); }
  }

  // ── Chat-Einstieg: Overlay über dem Chat öffnen (Assistent-Modus tauglich) ──
  async function openStepwise(opts) {
    opts = opts || {};
    _stepOnDone = opts.onDone || null;
    try {
      if (opts.name) {
        // anlegen (falls neu), dann öffnen — 409 = existiert bereits, ignorieren
        try { await _api('POST', '/api/varianten/projects', { name: opts.name, title: opts.title || opts.name }); }
        catch (e) { /* existiert schon */ }
        await _loadList(opts.name);
        _el('var-project').value = opts.name;
        await _open(opts.name);
      }
      if (!_data) { _status('Kein Vergleich geöffnet.'); return; }
      _openStepwise();
    } catch (e) { _status('Fehler: ' + e.message); }
  }

  // ── Problem → komplette Tabelle (Auto-Fill) ────────────────────────────────
  async function _generateAll() {
    const problem = (_el('var-problem').value || '').trim();
    if (!problem) { _status('Bitte das Problem beschreiben.'); return; }
    let description = problem;
    const mount = _el('var-gen-clarify');
    if (mount) mount.innerHTML = '';
    _el('var-gen-sources').textContent = '';

    // 1) Optionales Interview (Rückfragen) – hängt Antworten an die Beschreibung
    if (_el('var-gen-interview').checked && typeof Clarify !== 'undefined') {
      const c = await Clarify.ask({ task: problem, domain: 'varianten', model: _model(), mount });
      if (c && c.augmentedTask) description = c.augmentedTask;
      if (c && c.tokens) _tok(c.tokens, 'Variantenvergleich');
    }

    // 2) Ein Orchestrator-Aufruf: Kriterien → Paarvergleich → Varianten → Bewertungen
    _spin(true); _status('KI erzeugt die Tabelle… (kann bei lokalen Modellen dauern)');
    try {
      const web = _el('var-gen-web').checked;
      const res = await _api('POST', '/api/varianten/auto-fill', {
        title: _data.title || problem.slice(0, 80), description, web, model: _model(),
      });
      _tok(res.tokens, 'Variantenvergleich');
      if (res.criteria && res.criteria.length) _data.criteria = res.criteria;
      if (res.variants && res.variants.length) _data.variants = res.variants;
      _resizeMatrices();
      if (Array.isArray(res.pairwise) && res.pairwise.length === _data.criteria.length) _data.pairwise = res.pairwise;
      if (Array.isArray(res.ratings) && res.ratings.length === _data.variants.length) _data.ratings = res.ratings;
      // Titel/Beschreibung übernehmen, falls noch leer
      if (!_data.title) _data.title = problem.slice(0, 80);
      if (!_data.description) _data.description = problem;
      _render();
      await _save();
      const src = res.sources || [];
      if (src.length) {
        _el('var-gen-sources').innerHTML = '🌐 Belege: ' + src.slice(0, 5).map(s =>
          `<a href="${escHtml(s.url || s.href || '#')}" target="_blank" rel="noopener">${escHtml((s.title || s.url || 'Quelle').slice(0, 40))}</a>`).join(' · ');
      }
      _status('Tabelle erzeugt – bitte prüfen und anpassen.');
    } catch (e) { _status('KI-Fehler: ' + e.message); }
    finally { _spin(false); }
  }

  function init() {
    _el('var-project').addEventListener('change', e => _open(e.target.value));
    _el('btn-var-create').addEventListener('click', _create);
    _el('btn-var-delete').addEventListener('click', _delete);
    _el('btn-var-csv').addEventListener('click', _exportCsv);
    _el('var-new-project').addEventListener('keydown', e => { if (e.key === 'Enter') _create(); });
    _el('var-title').addEventListener('change', () => { _collect(); _saveSoon(); });
    _el('var-desc').addEventListener('change', () => { _collect(); _saveSoon(); });
    _el('btn-var-add-criterion').addEventListener('click', () => {
      _data.criteria.push({ name: '', direction: 'benefit' }); _resizeMatrices(); _render();
    });
    _el('btn-var-add-variant').addEventListener('click', () => {
      _data.variants.push({ name: '', description: '' }); _resizeMatrices(); _render();
    });
    _el('btn-var-ai-criteria').addEventListener('click', _aiCriteria);
    _el('btn-var-ai-variants').addEventListener('click', _aiVariants);
    _el('btn-var-ai-pairwise').addEventListener('click', _aiPairwise);
    _el('btn-var-ai-ratings').addEventListener('click', _aiRatings);
    _el('btn-var-ai-explain').addEventListener('click', _aiExplain);
    // Schnellvergleich (Wischtechnik)
    _el('btn-var-swipe').addEventListener('click', _openSwipe);
    _el('var-swipe-close').addEventListener('click', _closeSwipe);
    _el('var-swipe-left').addEventListener('click', () => _swipeAnswer('left'));
    _el('var-swipe-right').addEventListener('click', () => _swipeAnswer('right'));
    _el('var-swipe-eq').addEventListener('click', () => _swipeAnswer('up'));
    _el('var-swipe').addEventListener('click', e => { if (e.target.id === 'var-swipe') _closeSwipe(); });
    // Schritt-für-Schritt (inkrementeller Paarvergleich)
    _el('btn-var-stepwise').addEventListener('click', _openStepwise);
    _el('var-stepwise-close').addEventListener('click', () => { _el('var-stepwise').style.display = 'none'; _render(); });
    _el('var-stepwise-add-btn').addEventListener('click', _stepAddCriterion);
    _el('var-stepwise-input').addEventListener('keydown', e => { if (e.key === 'Enter') { e.preventDefault(); _stepAddCriterion(); } });
    _el('var-stepwise-repeat').addEventListener('click', _stepRepeat);
    _el('var-stepwise-done').addEventListener('click', _stepDone);
    _el('var-stepwise-more').addEventListener('click', () => _stepAnswer('more'));
    _el('var-stepwise-eq').addEventListener('click', () => _stepAnswer('eq'));
    _el('var-stepwise-less').addEventListener('click', () => _stepAnswer('less'));
    document.querySelectorAll('#var-stepwise .var-strength-btn').forEach(b =>
      b.addEventListener('click', () => { _stepStrength = +b.dataset.s; _highlightStrength(); }));
    _el('var-stepwise').addEventListener('click', e => { if (e.target.id === 'var-stepwise') { _el('var-stepwise').style.display = 'none'; _render(); } });
    // Problem → komplette Tabelle
    _el('btn-var-generate').addEventListener('click', _generateAll);
    _loadList();
  }

  return { init, openStepwise };
})();
