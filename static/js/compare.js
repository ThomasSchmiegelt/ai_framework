/* ── Excel-Vergleich ──────────────────────────────────────────────────────────
 *
 * Zwei Excel-/CSV-Blätter über eine Schlüsselspalte vergleichen: deterministischer
 * Zeilen-Diff (Server: tools/tablediff.py) + gestreamte KI-Bewertung der
 * Unterschiede. Vergleiche werden benannt gespeichert (data/compare/<name>/).
 *
 * Die Render-/Lauf-Helfer (preview/runStream/renderDiffHtml) sind exportiert, damit
 * der Chat-Befehl `/excelvergleich` (chat.js, Overlay #compare-help) sie mitnutzt —
 * eine gemeinsame Logik, zwei Einstiege (Tab + Chat/Assistent-Modus).
 */
const Compare = (() => {
  const _A = { file: null, file_id: '', filename: '', sheets: [], headers: [], sheet: '', key: 0 };
  const _B = { file: null, file_id: '', filename: '', sheets: [], headers: [], sheet: '', key: 0 };
  let _lastDiff = null, _lastEval = '';

  function _el(id) { return document.getElementById(id); }
  function _model() { return (typeof Profile !== 'undefined' && Profile.modelFor) ? Profile.modelFor('general') : ''; }
  function _spin(on) { const s = _el('cmp-spin'); if (s) s.style.display = on ? '' : 'none'; }
  function _status(t) { const s = _el('cmp-status'); if (s) s.textContent = t || ''; }
  function _esc(s) { return (typeof escHtml === 'function') ? escHtml(s) : String(s == null ? '' : s); }

  // ── Exportierte Kernhelfer (auch vom Chat-Overlay genutzt) ─────────────────
  async function preview(file, sheet, headerRow) {
    const fd = new FormData();
    fd.append('file', file);
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

  async function runStream(params, h) {
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

  // ── Tab-UI ─────────────────────────────────────────────────────────────────
  function _fillSideUI(pfx, side, data) {
    _el('cmp-' + pfx + '-meta').textContent =
      `${data.filename} · ${data.n_rows} Zeilen · Blatt „${data.sheet}"`;
    const ssel = _el('cmp-sheet-' + pfx);
    ssel.innerHTML = (data.sheets || []).map(s =>
      `<option ${s === data.sheet ? 'selected' : ''}>${_esc(s)}</option>`).join('');
    const ksel = _el('cmp-key-' + pfx);
    ksel.innerHTML = (data.headers || []).map((h, i) =>
      `<option value="${i}">${_esc(h || ('Spalte ' + (i + 1)))}</option>`).join('');
    side.key = 0;
  }

  async function _readSide(pfx, side, useSelectedSheet) {
    const fileEl = _el('cmp-file-' + pfx);
    const f = (fileEl.files && fileEl.files[0]) || side.file;
    if (!f) { _status('Bitte Datei ' + pfx.toUpperCase() + ' wählen.'); return; }
    side.file = f;
    const hr = parseInt(_el('cmp-header-' + pfx).value || '0', 10) || 0;
    const chosenSheet = useSelectedSheet ? (_el('cmp-sheet-' + pfx).value || '') : '';
    _spin(true); _status('Lese ' + f.name + '…');
    try {
      const data = await preview(f, chosenSheet, hr);
      side.file_id = data.file_id; side.filename = data.filename;
      side.sheets = data.sheets; side.headers = data.headers; side.sheet = data.sheet;
      _fillSideUI(pfx, side, data);
      _status('');
    } catch (e) { _status('Fehler: ' + e.message); }
    finally { _spin(false); }
  }

  function _params() {
    return {
      file_id_a: _A.file_id, sheet_a: _el('cmp-sheet-a').value || _A.sheet,
      header_row_a: parseInt(_el('cmp-header-a').value || '0', 10) || 0,
      key_a: parseInt(_el('cmp-key-a').value || '0', 10) || 0,
      file_id_b: _B.file_id, sheet_b: _el('cmp-sheet-b').value || _B.sheet,
      header_row_b: parseInt(_el('cmp-header-b').value || '0', 10) || 0,
      key_b: parseInt(_el('cmp-key-b').value || '0', 10) || 0,
      model: _model(),
    };
  }

  async function _run() {
    if (!_A.file_id || !_B.file_id) { _status('Bitte beide Dateien einlesen.'); return; }
    _lastDiff = null; _lastEval = '';
    _el('cmp-diff').innerHTML = '';
    _el('cmp-eval').innerHTML = '<span class="working">🔍 vergleicht…</span>';
    _spin(true); _status('Vergleiche…');
    let evalText = '';
    await runStream(_params(), {
      onDiff: (diff) => { _lastDiff = diff; _el('cmp-diff').innerHTML = renderDiffHtml(diff); _el('cmp-eval').innerHTML = '<span class="working">🧠 KI bewertet…</span>'; },
      onText: (chunk) => { evalText += chunk; _el('cmp-eval').innerHTML = _md(evalText); },
      onDone: (evaluation, tokens) => {
        _lastEval = evaluation || evalText;
        _el('cmp-eval').innerHTML = _md(_lastEval);
        if (tokens && typeof TokenMeter !== 'undefined') TokenMeter.add(tokens, 'Excel-Vergleich');
        _spin(false); _status('Fertig.');
      },
      onError: (msg) => { _el('cmp-eval').innerHTML = '<span class="var-cr-bad">Fehler: ' + _esc(msg) + '</span>'; _spin(false); _status(''); },
    });
  }

  function _md(t) { return (typeof marked !== 'undefined') ? marked.parse(t || '') : _esc(t).replace(/\n/g, '<br>'); }

  async function _save() {
    const name = (_el('cmp-save-name').value || '').trim();
    if (!name) { _status('Bitte einen Namen zum Speichern eingeben.'); return; }
    if (!_lastDiff) { _status('Erst einen Vergleich ausführen.'); return; }
    const body = {
      name, title: name,
      side_a: { file_id: _A.file_id, filename: _A.filename, sheet: _el('cmp-sheet-a').value, header_row: parseInt(_el('cmp-header-a').value || '0', 10) || 0, key: parseInt(_el('cmp-key-a').value || '0', 10) || 0 },
      side_b: { file_id: _B.file_id, filename: _B.filename, sheet: _el('cmp-sheet-b').value, header_row: parseInt(_el('cmp-header-b').value || '0', 10) || 0, key: parseInt(_el('cmp-key-b').value || '0', 10) || 0 },
      diff: _lastDiff, evaluation: _lastEval,
    };
    try {
      let r = await fetch('/api/compare/projects', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
      if (r.status === 409) {
        r = await fetch('/api/compare/projects/' + encodeURIComponent(name), { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
      }
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
      const r = await fetch('/api/compare/projects/' + encodeURIComponent(name));
      const data = await r.json();
      _lastDiff = data.diff || null; _lastEval = data.evaluation || '';
      _el('cmp-diff').innerHTML = _lastDiff ? renderDiffHtml(_lastDiff) : '';
      _el('cmp-eval').innerHTML = _md(_lastEval);
      _el('cmp-save-name').value = name;
      if (data.side_a) _el('cmp-a-meta').textContent = `${data.side_a.filename || ''} · Blatt „${data.side_a.sheet || ''}"`;
      if (data.side_b) _el('cmp-b-meta').textContent = `${data.side_b.filename || ''} · Blatt „${data.side_b.sheet || ''}"`;
      _status('Geladen (gespeicherter Stand).');
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

  function _exportCsv() {
    if (!_lastDiff) { _status('Erst vergleichen.'); return; }
    const a = document.createElement('a');
    a.href = URL.createObjectURL(new Blob([diffCsv(_lastDiff)], { type: 'text/csv' }));
    a.download = ((_el('cmp-save-name').value || 'excel-vergleich').trim()) + '.csv';
    a.click();
  }

  function init() {
    if (!_el('compare-panel')) return;
    _el('btn-cmp-read-a').addEventListener('click', () => _readSide('a', _A, false));
    _el('btn-cmp-read-b').addEventListener('click', () => _readSide('b', _B, false));
    _el('cmp-sheet-a').addEventListener('change', () => _readSide('a', _A, true));
    _el('cmp-sheet-b').addEventListener('change', () => _readSide('b', _B, true));
    _el('btn-cmp-run').addEventListener('click', _run);
    _el('btn-cmp-save').addEventListener('click', _save);
    _el('btn-cmp-csv').addEventListener('click', _exportCsv);
    _el('cmp-project').addEventListener('change', e => _openProject(e.target.value));
    _el('btn-cmp-delete').addEventListener('click', _deleteProject);
    _loadProjects();
  }

  return { init, preview, runStream, renderDiffHtml, diffCsv };
})();
