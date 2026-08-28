/* ── Excel-Vergleich (zellenweise, mehrstufig) ────────────────────────────────
 *
 * Zwei Excel-/CSV-Blätter über eine Schlüsselspalte matchen und Zelle für Zelle
 * vergleichen: erst ein billiger Logikvergleich (Server: tools/tablediff.py), bei
 * Unterschied optional eine KI-Bewertung je Zelle mit Kontext-Reset. Ergebnis in
 * drei Untertabs (Tabelle 1 mit Spaltenaktionen / Tabelle 2 / Ergebnis mit Filtern).
 * Läuft laufend gespeichert, mit Stop/Fortsetzen, JSON-Export und einer Mehrdatei-
 * Job-Queue.
 *
 * Exportiert die geteilten Helfer, die das Chat-Overlay `/excelvergleich` und der
 * Assistent-Modus-Vorschlag mitnutzen: preview / runStream / renderDiffHtml /
 * diffCsv (Legacy, zeilenweise) sowie runCellStream / renderCellsHtml (zellenweise).
 */
const Compare = (() => {
  const _A = { file: null, file_id: '', filename: '', sheets: [], headers: [], sheet: '', sampleRows: [], nRows: 0 };
  const _B = { file: null, file_id: '', filename: '', sheets: [], headers: [], sheet: '', sampleRows: [], nRows: 0 };
  let _lastDiff = null, _lastEval = '';          // Legacy (zeilenweiser Diff + KI-Text)
  let _cfg = [];                                  // [{name, mode, metric, inA, inB}]
  let _meta = null, _cellMap = {}, _keyOrder = [], _cells = [];
  let _reader = null, _running = false, _renderTimer = 0;
  let _jobQueue = [];                             // [{name, params}]

  function _el(id) { return document.getElementById(id); }
  function _model() { return (typeof Profile !== 'undefined' && Profile.modelFor) ? Profile.modelFor('general') : ''; }
  function _spin(on) { const s = _el('cmp-spin'); if (s) s.style.display = on ? '' : 'none'; }
  function _status(t) { const s = _el('cmp-status'); if (s) s.textContent = t || ''; }
  function _esc(s) { return (typeof escHtml === 'function') ? escHtml(s) : String(s == null ? '' : s); }
  function _md(t) { return (typeof marked !== 'undefined') ? marked.parse(t || '') : _esc(t).replace(/\n/g, '<br>'); }

  // ── Exportierte Kernhelfer ─────────────────────────────────────────────────
  async function preview(fileOrId, sheet, headerRow) {
    const fd = new FormData();
    if (fileOrId && typeof fileOrId === 'object') fd.append('file', fileOrId);
    else fd.append('file_id', String(fileOrId || ''));
    fd.append('sheet', sheet || '');
    fd.append('header_row', String(headerRow || 0));
    const r = await fetch('/api/compare/preview', { method: 'POST', body: fd });
    if (!r.ok) {
      let msg = 'HTTP ' + r.status;
      try { msg = (await r.json()).detail || msg; } catch (_) {}
      throw new Error(msg);
    }
    return r.json();
  }

  async function runStream(params, h) {   // Legacy: zeilenweiser Diff + KI-Gesamttext
    h = h || {};
    let resp;
    try {
      resp = await fetch('/api/compare/run', {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(params),
      });
    } catch (e) { if (h.onError) h.onError(e.message); return; }
    if (!resp.ok || !resp.body) { if (h.onError) h.onError('HTTP ' + resp.status); return; }
    const reader = resp.body.getReader();
    const dec = new TextDecoder();
    let buf = '';
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += dec.decode(value, { stream: true });
      let idx;
      while ((idx = buf.indexOf('\n\n')) >= 0) {
        const chunk = buf.slice(0, idx); buf = buf.slice(idx + 2);
        const line = chunk.split('\n').find(l => l.startsWith('data:'));
        if (!line) continue;
        let ev; try { ev = JSON.parse(line.slice(5).trim()); } catch (_) { continue; }
        if (ev.type === 'diff' && h.onDiff) h.onDiff(ev.diff);
        else if (ev.type === 'text' && h.onText) h.onText(ev.content);
        else if (ev.type === 'done' && h.onDone) h.onDone(ev.evaluation || '', ev.tokens);
        else if (ev.type === 'error' && h.onError) h.onError(ev.message);
      }
    }
  }

  // Zellenweiser SSE-Lauf. Handler: onMeta/onCell/onProgress/onDone/onError/onReader.
  async function runCellStream(params, h) {
    h = h || {};
    let resp;
    try {
      resp = await fetch('/api/compare/run-cells', {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(params),
      });
    } catch (e) { if (h.onError) h.onError(e.message); return; }
    if (!resp.ok || !resp.body) { if (h.onError) h.onError('HTTP ' + resp.status); return; }
    const reader = resp.body.getReader();
    if (h.onReader) h.onReader(reader);
    const dec = new TextDecoder();
    let buf = '';
    while (true) {
      let res;
      try { res = await reader.read(); } catch (_) { break; }   // cancel() → abbrechen
      if (res.done) break;
      buf += dec.decode(res.value, { stream: true });
      let idx;
      while ((idx = buf.indexOf('\n\n')) >= 0) {
        const chunk = buf.slice(0, idx); buf = buf.slice(idx + 2);
        const line = chunk.split('\n').find(l => l.startsWith('data:'));
        if (!line) continue;
        let ev; try { ev = JSON.parse(line.slice(5).trim()); } catch (_) { continue; }
        if (ev.type === 'meta' && h.onMeta) h.onMeta(ev);
        else if (ev.type === 'cell' && h.onCell) h.onCell(ev);
        else if (ev.type === 'progress' && h.onProgress) h.onProgress(ev);
        else if (ev.type === 'done' && h.onDone) h.onDone(ev);
        else if (ev.type === 'error' && h.onError) h.onError(ev.message);
      }
    }
  }

  // ── Legacy-Rendern (Chat-Overlay `/excelvergleich`) ────────────────────────
  function _listTable(title, arr) {
    if (!arr || !arr.length) return '';
    let h = `<h4 style="margin:12px 0 4px">${_esc(title)} (${arr.length})</h4>` +
      '<div style="overflow-x:auto"><table class="cmp-table"><tbody>';
    arr.slice(0, 200).forEach(item => {
      const vals = Object.entries(item.row || {}).slice(0, 8)
        .map(([k, v]) => `${_esc(k)}: ${_esc(v)}`).join(' · ');
      h += `<tr><td class="cmp-key"><b>${_esc(item.key)}</b></td><td>${vals}</td></tr>`;
    });
    return h + '</tbody></table></div>';
  }

  function renderDiffHtml(diff) {
    if (!diff || !diff.counts) return '<span class="planner-muted">Kein Diff.</span>';
    const c = diff.counts;
    let html = `<div class="cmp-summary">Zeilen A: <b>${c.rows_a}</b> · B: <b>${c.rows_b}</b> — ` +
      `<b>${c.only_a}</b> nur in A, <b>${c.only_b}</b> nur in B, <b>${c.changed}</b> geändert ` +
      `(von ${c.common} gemeinsamen Schlüsseln)</div>`;
    if (diff.columns_only_a && diff.columns_only_a.length)
      html += `<div class="planner-muted" style="font-size:11.5px">Spalten nur in A: ${diff.columns_only_a.map(_esc).join(', ')}</div>`;
    if (diff.columns_only_b && diff.columns_only_b.length)
      html += `<div class="planner-muted" style="font-size:11.5px">Spalten nur in B: ${diff.columns_only_b.map(_esc).join(', ')}</div>`;
    if (diff.changed && diff.changed.length) {
      html += '<h4 style="margin:12px 0 4px">Geänderte Zeilen</h4>' +
        '<div style="overflow-x:auto"><table class="cmp-table"><thead><tr>' +
        '<th>Schlüssel</th><th>Spalte</th><th>A</th><th>B</th></tr></thead><tbody>';
      diff.changed.forEach(item => {
        (item.changes || []).forEach((ch, k) => {
          html += `<tr><td class="cmp-key">${k === 0 ? '<b>' + _esc(item.key) + '</b>' : ''}</td>` +
            `<td>${_esc(ch.column)}</td><td class="cmp-a">${_esc(ch.a)}</td><td class="cmp-b">${_esc(ch.b)}</td></tr>`;
        });
      });
      html += '</tbody></table></div>';
    }
    html += _listTable('Nur in A', diff.only_in_a);
    html += _listTable('Nur in B', diff.only_in_b);
    return html;
  }

  function diffCsv(diff) {
    const rows = [['Art', 'Schlüssel', 'Spalte', 'A', 'B']];
    (diff.changed || []).forEach(item => (item.changes || []).forEach(ch =>
      rows.push(['geändert', item.key, ch.column, ch.a, ch.b])));
    (diff.only_in_a || []).forEach(item =>
      rows.push(['nur A', item.key, '', Object.values(item.row || {}).join(' | '), '']));
    (diff.only_in_b || []).forEach(item =>
      rows.push(['nur B', item.key, '', '', Object.values(item.row || {}).join(' | ')]));
    return '﻿' + rows.map(r => r.map(c => `"${String(c == null ? '' : c).replace(/"/g, '""')}"`).join(';')).join('\r\n');
  }

  // ── Zellen-Ergebnis rendern (Tab + Chat) ───────────────────────────────────
  // meta: {compared_columns, only_in_a, only_in_b, columns_only_a/b, counts}
  // cells: [{key, column, a, b, verdict, detail, summary}]
  function renderCellsHtml(meta, cells, opts) {
    opts = opts || {};
    meta = meta || {};
    const cols = meta.compared_columns || [];
    // gruppieren key → col → rec (Reihenfolge des ersten Auftretens)
    const order = [], map = {};
    (cells || []).forEach(rec => {
      if (!(rec.key in map)) { map[rec.key] = {}; order.push(rec.key); }
      map[rec.key][rec.column] = rec;
    });
    const c = meta.counts || {};
    let changedRows = 0;
    order.forEach(k => { if (cols.some(col => (map[k][col] || {}).verdict === 'changed')) changedRows++; });
    let html = `<div class="cmp-summary">Gemeinsame Schlüssel: <b>${c.common != null ? c.common : order.length}</b> · ` +
      `Zeilen mit Änderung: <b>${changedRows}</b> · nur in A: <b>${(meta.only_in_a || []).length}</b> · ` +
      `nur in B: <b>${(meta.only_in_b || []).length}</b> · verglichene Spalten: <b>${cols.length}</b></div>`;
    if ((meta.columns_only_a || []).length)
      html += `<div class="planner-muted" style="font-size:11.5px">Spalten nur in A: ${meta.columns_only_a.map(_esc).join(', ')}</div>`;
    if ((meta.columns_only_b || []).length)
      html += `<div class="planner-muted" style="font-size:11.5px">Spalten nur in B: ${meta.columns_only_b.map(_esc).join(', ')}</div>`;

    const colFilter = opts.col || '';
    const showCols = colFilter ? cols.filter(x => x === colFilter) : cols;
    html += '<table class="cmp-table"><thead><tr><th>Schlüssel</th>' +
      showCols.map(x => `<th>${_esc(x)}</th>`).join('') + '</tr></thead><tbody>';
    let shown = 0;
    order.forEach(k => {
      const row = map[k];
      const rowChanged = showCols.some(col => (row[col] || {}).verdict === 'changed');
      const rowEval = showCols.some(col => (row[col] || {}).summary);
      if (opts.onlyChanges && !rowChanged) return;
      if (opts.onlyEval && !rowEval) return;
      shown++;
      html += `<tr><td class="cmp-key"><b>${_esc(k)}</b></td>`;
      showCols.forEach(col => {
        const rec = row[col];
        if (!rec) { html += '<td class="cmp-cell-eq">—</td>'; return; }
        if (rec.verdict === 'changed') {
          html += '<td><span class="cmp-cell-changed">' +
            `<span class="cmp-a">A: ${_esc(rec.a)}</span>` +
            `<span class="cmp-b">B: ${_esc(rec.b)}</span>` +
            (rec.summary ? `<span class="cmp-cell-sum">🧠 ${_esc(rec.summary)}</span>` : '') +
            '</span></td>';
        } else {
          html += `<td class="cmp-cell-eq">${_esc(rec.a)}</td>`;
        }
      });
      html += '</tr>';
    });
    if (!shown) html += '<tr><td colspan="' + (showCols.length + 1) + '" class="planner-muted">Keine Zeilen für diesen Filter.</td></tr>';
    html += '</tbody></table>';
    html += _listTable('Nur in A', meta.only_in_a);
    html += _listTable('Nur in B', meta.only_in_b);
    return html;
  }

  // ── Tab-UI: Dateien einlesen ───────────────────────────────────────────────
  function _fillSideUI(pfx, side, data) {
    _el('cmp-' + pfx + '-meta').textContent =
      `${data.filename} · ${data.n_rows} Zeilen · Blatt „${data.sheet}"`;
    const ssel = _el('cmp-sheet-' + pfx);
    ssel.innerHTML = (data.sheets || []).map(s =>
      `<option ${s === data.sheet ? 'selected' : ''}>${_esc(s)}</option>`).join('');
    const ksel = _el('cmp-key-' + pfx);
    ksel.innerHTML = (data.headers || []).map((h, i) =>
      `<option value="${i}">${_esc(h || ('Spalte ' + (i + 1)))}</option>`).join('');
    _renderPreview(pfx, side);
  }

  function _renderPreview(pfx, side) {
    const el = _el('cmp-table-' + pfx);
    if (!el) return;
    const hs = side.headers || [];
    if (!hs.length) { el.innerHTML = '<span class="planner-muted">—</span>'; return; }
    let h = '<table class="cmp-table"><thead><tr>' + hs.map(x => `<th>${_esc(x || '')}</th>`).join('') + '</tr></thead><tbody>';
    (side.sampleRows || []).slice(0, 5).forEach(r => {
      h += '<tr>' + hs.map((_, i) => `<td>${_esc((r || [])[i])}</td>`).join('') + '</tr>';
    });
    el.innerHTML = h + '</tbody></table>';
  }

  async function _readSide(pfx, side, useSelectedSheet) {
    const fileEl = _el('cmp-file-' + pfx);
    const f = (fileEl.files && fileEl.files[0]) || side.file;
    if (!f && !side.file_id) { _status('Bitte Datei ' + pfx.toUpperCase() + ' wählen.'); return; }
    if (f) side.file = f;
    const hr = parseInt(_el('cmp-header-' + pfx).value || '0', 10) || 0;
    const chosenSheet = useSelectedSheet ? (_el('cmp-sheet-' + pfx).value || '') : '';
    _spin(true); _status('Lese ' + (f ? f.name : side.filename) + '…');
    try {
      const data = await preview(useSelectedSheet && side.file_id && !f ? side.file_id : (f || side.file_id), chosenSheet, hr);
      side.file_id = data.file_id; side.filename = data.filename;
      side.sheets = data.sheets; side.headers = data.headers; side.sheet = data.sheet;
      side.sampleRows = data.sample_rows || []; side.nRows = data.n_rows || 0;
      _fillSideUI(pfx, side, data);
      _buildColCfg();
      _status('');
    } catch (e) { _status('Fehler: ' + e.message); }
    finally { _spin(false); }
  }

  // ── Spaltenaktionen (Tabelle 1) ────────────────────────────────────────────
  function _buildColCfg() {
    const body = _el('cmp-colcfg');
    if (!body) return;
    if (!_A.headers.length && !_B.headers.length) { body.innerHTML = ''; return; }
    const seen = {}, union = [];
    _A.headers.concat(_B.headers).forEach(h => {
      const n = String(h || '').trim() || '(leer)';
      if (!(n in seen)) { seen[n] = true; union.push(n); }
    });
    const keyA = (_A.headers[parseInt(_el('cmp-key-a').value || '0', 10) || 0] || '').trim();
    // vorhandene Konfig je Name merken
    const prev = {}; _cfg.forEach(c => prev[c.name] = c);
    _cfg = union.map(name => {
      const inA = _A.headers.map(x => String(x || '').trim()).includes(name);
      const inB = _B.headers.map(x => String(x || '').trim()).includes(name);
      const p = prev[name] || {};
      let mode = p.mode || (inA && inB ? 'logic' : 'ignore');
      if (!inA || !inB) mode = 'ignore';
      return { name, inA, inB, mode, metric: p.metric || 'nospace' };
    });
    const metricOpts = [['nospace', 'Zeichen o. Leerz.'], ['numeric', 'Zahl'], ['exact', 'exakt'], ['length', 'Länge']];
    let html = '<tr><th>Spalte</th><th>Aktion</th><th>Logik-Metrik</th></tr>';
    _cfg.forEach((c, i) => {
      const only = !c.inA || !c.inB;
      const badge = only ? ` <span class="cmp-col-only">(nur ${c.inA ? 'A' : 'B'})</span>` : (c.name === keyA ? ' <span class="cmp-col-only">(Schlüssel)</span>' : '');
      const modeSel = only ? '<span class="cmp-col-only">ignoriert</span>' :
        `<select data-i="${i}" class="cmp-mode">
           <option value="ignore"${c.mode === 'ignore' ? ' selected' : ''}>ignorieren</option>
           <option value="logic"${c.mode === 'logic' ? ' selected' : ''}>Logik</option>
           <option value="logic_llm"${c.mode === 'logic_llm' ? ' selected' : ''}>Logik + KI</option>
         </select>`;
      const metSel = only ? '' :
        `<select data-i="${i}" class="cmp-metric">` +
        metricOpts.map(([v, l]) => `<option value="${v}"${c.metric === v ? ' selected' : ''}>${l}</option>`).join('') +
        '</select>';
      html += `<tr><td>${_esc(c.name)}${badge}</td><td>${modeSel}</td><td>${metSel}</td></tr>`;
    });
    body.innerHTML = html;
    body.querySelectorAll('.cmp-mode').forEach(s => s.addEventListener('change', e => { _cfg[+e.target.dataset.i].mode = e.target.value; }));
    body.querySelectorAll('.cmp-metric').forEach(s => s.addEventListener('change', e => { _cfg[+e.target.dataset.i].metric = e.target.value; }));
  }

  function _columnsForRun() {
    // nur gemeinsame Spalten mit ihrer Konfig (inkl. ignore → Server schließt sie aus)
    return _cfg.filter(c => c.inA && c.inB).map(c => ({ name: c.name, mode: c.mode, metric: c.metric }));
  }

  // ── Ausführen (zellenweise) ────────────────────────────────────────────────
  function _params(resume) {
    return {
      file_id_a: _A.file_id, sheet_a: _el('cmp-sheet-a').value || _A.sheet,
      header_row_a: parseInt(_el('cmp-header-a').value || '0', 10) || 0,
      key_a: parseInt(_el('cmp-key-a').value || '0', 10) || 0,
      file_id_b: _B.file_id, sheet_b: _el('cmp-sheet-b').value || _B.sheet,
      header_row_b: parseInt(_el('cmp-header-b').value || '0', 10) || 0,
      key_b: parseInt(_el('cmp-key-b').value || '0', 10) || 0,
      columns: _columnsForRun(),
      name: (_el('cmp-save-name').value || '').trim() || undefined,
      model: _model() || undefined,
      resume: !!resume,
    };
  }

  function _setRunning(on) {
    _running = on;
    _el('btn-cmp-run').style.display = on ? 'none' : '';
    _el('btn-cmp-stop').style.display = on ? '' : 'none';
    _el('btn-cmp-resume').style.display = (!on && _cells.length) ? '' : 'none';
    _el('cmp-progress-wrap').style.display = on ? '' : (_cells.length ? '' : 'none');
    _spin(on);
  }

  function _scheduleResultRender() {
    if (_renderTimer) return;
    _renderTimer = setTimeout(() => { _renderTimer = 0; _renderResult(); }, 300);
  }

  function _renderResult() {
    const opts = {
      onlyChanges: _el('cmp-filter-changed').checked,
      onlyEval: _el('cmp-filter-eval').checked,
      col: _el('cmp-filter-col').value || '',
    };
    _el('cmp-result').innerHTML = renderCellsHtml(_meta, _cells, opts);
  }

  function _fillColFilter() {
    const sel = _el('cmp-filter-col');
    const cur = sel.value;
    const cols = (_meta && _meta.compared_columns) || [];
    sel.innerHTML = '<option value="">alle</option>' + cols.map(c => `<option value="${_esc(c)}">${_esc(c)}</option>`).join('');
    if (cols.includes(cur)) sel.value = cur;
  }

  async function _run(resume) {
    if (!_A.file_id || !_B.file_id) { _status('Bitte beide Dateien einlesen.'); return; }
    if (_running) return;
    if (!resume) { _meta = null; _cellMap = {}; _keyOrder = []; _cells = []; _el('cmp-result').innerHTML = ''; }
    _switchSub('result');
    _setRunning(true); _status('Vergleiche zellenweise…');
    let total = 0;
    await runCellStream(_params(resume), {
      onReader: (r) => { _reader = r; },
      onMeta: (ev) => { _meta = ev; total = (ev.counts || {}).cells || 0; _fillColFilter(); _renderResult(); },
      onCell: (ev) => {
        const key = ev.key + ' ' + ev.column;
        if (!(key in _cellMap)) { _cellMap[key] = _cells.length; _cells.push(ev); }
        else _cells[_cellMap[key]] = ev;
        _scheduleResultRender();
      },
      onProgress: (ev) => {
        total = ev.total || total;
        const pct = total ? Math.round((ev.done / total) * 100) : 0;
        _el('cmp-progress-fill').style.width = pct + '%';
        _el('cmp-progress-label').textContent = `${ev.done} / ${total} Zellen`;
      },
      onDone: (ev) => {
        _renderResult();
        if (ev.tokens && typeof TokenMeter !== 'undefined') TokenMeter.add(ev.tokens, 'Excel-Vergleich');
        _el('cmp-progress-fill').style.width = '100%';
        _setRunning(false); _status('Fertig.');
      },
      onError: (msg) => { _setRunning(false); _status('Fehler: ' + msg); _el('cmp-result').innerHTML += `<div class="var-cr-bad">Fehler: ${_esc(msg)}</div>`; },
    });
    if (_running) { _setRunning(false); _status('Angehalten.'); }   // Stream endete ohne done
  }

  function _stop() {
    if (_reader) { try { _reader.cancel(); } catch (_) {} }
    _setRunning(false); _status('Angehalten — „▶ Fortsetzen" setzt fort.');
  }

  // ── Untertabs ──────────────────────────────────────────────────────────────
  function _switchSub(name) {
    document.querySelectorAll('#compare-panel .cmp-subtab').forEach(b => b.classList.toggle('active', b.dataset.sub === name));
    ['a', 'b', 'result'].forEach(n => { const p = _el('cmp-sub-' + n); if (p) p.classList.toggle('active', n === name); });
  }

  // ── Speichern / Laden / Löschen ────────────────────────────────────────────
  async function _save() {
    const name = (_el('cmp-save-name').value || '').trim();
    if (!name) { _status('Bitte einen Namen zum Speichern eingeben.'); return; }
    if (!_meta && !_cells.length) { _status('Erst einen Vergleich ausführen.'); return; }
    // Metadaten (Zähler/Seiten) für die Projektliste; die Zellen liegen bereits in results.json.
    const counts = (_meta && _meta.counts) || {};
    const body = {
      name, title: name,
      side_a: { file_id: _A.file_id, filename: _A.filename, sheet: _el('cmp-sheet-a').value, header_row: parseInt(_el('cmp-header-a').value || '0', 10) || 0, key: parseInt(_el('cmp-key-a').value || '0', 10) || 0 },
      side_b: { file_id: _B.file_id, filename: _B.filename, sheet: _el('cmp-sheet-b').value, header_row: parseInt(_el('cmp-header-b').value || '0', 10) || 0, key: parseInt(_el('cmp-key-b').value || '0', 10) || 0 },
      diff: { counts: { rows_a: counts.rows_a || 0, rows_b: counts.rows_b || 0, only_a: (_meta && _meta.only_in_a || []).length, only_b: (_meta && _meta.only_in_b || []).length, common: counts.common || 0, changed: _cells.filter(c => c.verdict === 'changed').length } },
      evaluation: '',
    };
    try {
      let r = await fetch('/api/compare/projects', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
      if (r.status === 409) r = await fetch('/api/compare/projects/' + encodeURIComponent(name), { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
      if (!r.ok) throw new Error('HTTP ' + r.status);
      _status('Gespeichert.');
      await _loadProjects(name);
    } catch (e) { _status('Speichern: ' + e.message); }
  }

  async function _loadProjects(select) {
    try {
      const r = await fetch('/api/compare/projects');
      const list = await r.json();
      const sel = _el('cmp-project');
      sel.innerHTML = '<option value="">— gespeicherten Vergleich öffnen —</option>' +
        list.map(p => `<option value="${_esc(p.name)}">${_esc(p.title || p.name)} (${p.changed}Δ)</option>`).join('');
      if (select) sel.value = select;
    } catch (_) {}
  }

  async function _openProject(name) {
    if (!name) return;
    _spin(true); _status('Lade…');
    try {
      const r = await fetch('/api/compare/projects/' + encodeURIComponent(name) + '/results');
      const data = await r.json();
      if (data && (data.cells || data.compared_columns)) {
        _meta = {
          compared_columns: data.compared_columns || [], only_in_a: data.only_in_a || [], only_in_b: data.only_in_b || [],
          columns_only_a: data.columns_only_a || [], columns_only_b: data.columns_only_b || [], counts: data.counts || {},
        };
        _cells = data.cells || []; _cellMap = {}; _cells.forEach((c, i) => _cellMap[c.key + ' ' + c.column] = i);
        _el('cmp-save-name').value = name;
        _fillColFilter(); _renderResult(); _switchSub('result');
        _el('btn-cmp-resume').style.display = (!data.complete && _cells.length) ? '' : 'none';
        _status(data.complete ? 'Geladen.' : 'Geladen (unvollständig — „▶ Fortsetzen").');
      } else { _status('Kein zellenweises Ergebnis gespeichert.'); }
    } catch (e) { _status('Fehler: ' + e.message); }
    finally { _spin(false); }
  }

  async function _deleteProject() {
    const name = _el('cmp-project').value;
    if (!name || !confirm(`Vergleich „${name}" löschen?`)) return;
    await fetch('/api/compare/projects/' + encodeURIComponent(name), { method: 'DELETE' });
    _el('cmp-project').value = '';
    await _loadProjects();
  }

  // ── Export/Import ──────────────────────────────────────────────────────────
  function _download(name, text, type) {
    const a = document.createElement('a');
    a.href = URL.createObjectURL(new Blob([text], { type: type || 'text/plain' }));
    a.download = name; a.click();
  }

  function _exportCsv() {
    if (!_cells.length) { _status('Erst vergleichen.'); return; }
    const rows = [['Schlüssel', 'Spalte', 'Status', 'A', 'B', 'KI-Bewertung']];
    _cells.forEach(c => rows.push([c.key, c.column, c.verdict, c.a, c.b, c.summary || '']));
    const csv = '﻿' + rows.map(r => r.map(x => `"${String(x == null ? '' : x).replace(/"/g, '""')}"`).join(';')).join('\r\n');
    _download(((_el('cmp-save-name').value || 'excel-vergleich').trim()) + '.csv', csv, 'text/csv');
  }

  function _exportJson() {
    if (!_cells.length && !_meta) { _status('Erst vergleichen.'); return; }
    const payload = { version: 1, meta: _meta, cells: _cells, exported_at: new Date().toISOString() };
    _download(((_el('cmp-save-name').value || 'excel-vergleich').trim()) + '.json', JSON.stringify(payload, null, 2), 'application/json');
  }

  function _importJson(file) {
    const reader = new FileReader();
    reader.onload = e => {
      try {
        const data = JSON.parse(e.target.result);
        _meta = data.meta || { compared_columns: [], only_in_a: [], only_in_b: [], counts: {} };
        _cells = data.cells || []; _cellMap = {}; _cells.forEach((c, i) => _cellMap[c.key + ' ' + c.column] = i);
        _fillColFilter(); _renderResult(); _switchSub('result');
        _status('JSON geladen.');
      } catch (err) { _status('JSON ungültig: ' + err.message); }
    };
    reader.readAsText(file);
  }

  // ── Mehrdatei-Job-Queue ────────────────────────────────────────────────────
  function _renderJobList() {
    const el = _el('cmp-job-list');
    if (!el) return;
    el.innerHTML = _jobQueue.map((j, i) =>
      `<div class="cmp-job-row"><span>${i + 1}. <b>${_esc(j.name)}</b> — ${_esc(j.params.file_id_a.slice(0, 20))} ↔ ${_esc(j.params.file_id_b.slice(0, 20))}</span>` +
      `<button class="export-btn btn-danger-sm" data-i="${i}" style="font-size:11px;margin-left:auto">✕</button></div>`).join('') ||
      '<span class="planner-muted" style="font-size:11.5px">Warteschlange leer.</span>';
    el.querySelectorAll('button[data-i]').forEach(b => b.addEventListener('click', e => { _jobQueue.splice(+e.currentTarget.dataset.i, 1); _renderJobList(); }));
  }

  function _jobAdd() {
    if (!_A.file_id || !_B.file_id) { _el('cmp-job-status').textContent = 'Erst beide Dateien einlesen.'; return; }
    const name = (_el('cmp-job-name').value || '').trim();
    if (!name) { _el('cmp-job-status').textContent = 'Bitte einen Namen für das Paar angeben.'; return; }
    const p = _params(false); p.name = name;
    _jobQueue.push({ name, params: p });
    _el('cmp-job-name').value = '';
    _el('cmp-job-status').textContent = '';
    _renderJobList();
  }

  async function _jobRun() {
    if (!_jobQueue.length) { _el('cmp-job-status').textContent = 'Warteschlange leer.'; return; }
    if (_running) { _el('cmp-job-status').textContent = 'Ein Vergleich läuft bereits.'; return; }
    const queue = _jobQueue.slice();
    for (let i = 0; i < queue.length; i++) {
      const job = queue[i];
      _el('cmp-job-status').textContent = `Paar ${i + 1}/${queue.length}: ${job.name}…`;
      _meta = null; _cellMap = {}; _cells = []; _el('cmp-result').innerHTML = '';
      _el('cmp-save-name').value = job.name;
      _setRunning(true); _switchSub('result');
      await new Promise(resolve => {
        runCellStream(job.params, {
          onReader: (r) => { _reader = r; },
          onMeta: (ev) => { _meta = ev; _fillColFilter(); _renderResult(); },
          onCell: (ev) => { const k = ev.key + ' ' + ev.column; if (!(k in _cellMap)) { _cellMap[k] = _cells.length; _cells.push(ev); } else _cells[_cellMap[k]] = ev; _scheduleResultRender(); },
          onProgress: (ev) => { const t = ev.total || 0; _el('cmp-progress-fill').style.width = (t ? Math.round(ev.done / t * 100) : 0) + '%'; _el('cmp-progress-label').textContent = `${job.name}: ${ev.done}/${t}`; },
          onDone: (ev) => { _renderResult(); if (ev.tokens && typeof TokenMeter !== 'undefined') TokenMeter.add(ev.tokens, 'Excel-Vergleich'); _save(); resolve(); },
          onError: (msg) => { _el('cmp-job-status').textContent = `Paar ${job.name}: ${msg}`; resolve(); },
        });
      });
      _setRunning(false);
    }
    _el('cmp-job-status').textContent = `Job fertig (${queue.length} Paar(e)).`;
    await _loadProjects();
  }

  // ── Init ───────────────────────────────────────────────────────────────────
  function init() {
    if (!_el('compare-panel')) return;
    _el('btn-cmp-read-a').addEventListener('click', () => _readSide('a', _A, false));
    _el('btn-cmp-read-b').addEventListener('click', () => _readSide('b', _B, false));
    _el('cmp-sheet-a').addEventListener('change', () => _readSide('a', _A, true));
    _el('cmp-sheet-b').addEventListener('change', () => _readSide('b', _B, true));
    _el('cmp-key-a').addEventListener('change', _buildColCfg);
    _el('cmp-key-b').addEventListener('change', _buildColCfg);
    _el('btn-cmp-run').addEventListener('click', () => _run(false));
    _el('btn-cmp-stop').addEventListener('click', _stop);
    _el('btn-cmp-resume').addEventListener('click', () => _run(true));
    _el('btn-cmp-save').addEventListener('click', _save);
    _el('btn-cmp-csv').addEventListener('click', _exportCsv);
    _el('btn-cmp-json').addEventListener('click', _exportJson);
    _el('btn-cmp-json-import').addEventListener('click', () => _el('cmp-json-input').click());
    _el('cmp-json-input').addEventListener('change', e => { if (e.target.files[0]) _importJson(e.target.files[0]); e.target.value = ''; });
    _el('cmp-project').addEventListener('change', e => _openProject(e.target.value));
    _el('btn-cmp-delete').addEventListener('click', _deleteProject);
    _el('cmp-filter-changed').addEventListener('change', _renderResult);
    _el('cmp-filter-eval').addEventListener('change', _renderResult);
    _el('cmp-filter-col').addEventListener('change', _renderResult);
    document.querySelectorAll('#compare-panel .cmp-subtab').forEach(b => b.addEventListener('click', () => _switchSub(b.dataset.sub)));
    _el('btn-cmp-job-add').addEventListener('click', _jobAdd);
    _el('btn-cmp-job-run').addEventListener('click', _jobRun);
    _el('btn-cmp-job-clear').addEventListener('click', () => { _jobQueue = []; _renderJobList(); });
    _renderJobList();
    _loadProjects();
  }

  return { init, preview, runStream, renderDiffHtml, diffCsv, runCellStream, renderCellsHtml };
})();
