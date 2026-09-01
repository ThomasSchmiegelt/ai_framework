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
  let _kiOn = false, _saveTimer = 0, _kiStop = false;
  let _results = [], _activeResult = -1, _view = 'settings';   // Ergebnis-Reiter je Lauf/Job

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
  // Matrix-Renderer: Schlüssel (fixiert) + je Spalte drei Teilspalten (Tab 1/Tab 2/
  // Vergleich), Kopf fixiert, beidseitig scrollbar. opts: {view, rows, cols[], ki, onlyChanges}.
  // Geteilt mit dem Chat-Overlay `/excelvergleich` (dort nur {onlyChanges:true}).
  const _SUBS = { alles: ['a', 'b', 'v'], tabellen: ['a', 'b'], t1: ['a'], t2: ['b'], vergleich: ['v'], ohne_vergleich: ['a', 'b'] };
  const _SUBLABEL = { a: 'Tab 1', b: 'Tab 2', v: 'Vergleich' };
  function renderCellsHtml(meta, cells, opts) {
    opts = opts || {};
    meta = meta || {};
    const allCols = meta.compared_columns || [];
    const visCols = (opts.cols && opts.cols.length) ? allCols.filter(c => opts.cols.includes(c)) : allCols;
    const view = opts.view || 'alles';
    const sub = _SUBS[view] || _SUBS.alles;
    const rowsFilter = opts.rows || (opts.onlyChanges ? 'geaendert' : 'alle');
    const ki = !!opts.ki;

    // Zeilen normalisieren: gemeinsame (aus cells) + gelöscht (only_in_a) + hinzugefügt (only_in_b)
    const order = [], map = {};
    (cells || []).forEach(rec => {
      if (!(rec.key in map)) { map[rec.key] = {}; order.push(rec.key); }
      map[rec.key][rec.column] = rec;
    });
    const rows = [];
    order.forEach(k => {
      const byCol = {};
      visCols.forEach(c => { const rec = map[k][c] || null; byCol[c] = { a: rec ? rec.a : '', b: rec ? rec.b : '', rec }; });
      rows.push({ key: k, type: 'common', byCol });
    });
    (meta.only_in_a || []).forEach(it => {
      const rd = it.row || {}, byCol = {};
      visCols.forEach(c => { byCol[c] = { a: rd[c] != null ? rd[c] : '', b: '', rec: null }; });
      rows.push({ key: it.key, type: 'deleted', byCol });
    });
    (meta.only_in_b || []).forEach(it => {
      const rd = it.row || {}, byCol = {};
      visCols.forEach(c => { byCol[c] = { a: '', b: rd[c] != null ? rd[c] : '', rec: null }; });
      rows.push({ key: it.key, type: 'added', byCol });
    });
    const filtered = rows.filter(r => {
      if (rowsFilter === 'hinzugefuegt') return r.type === 'added';
      if (rowsFilter === 'geloescht') return r.type === 'deleted';
      if (rowsFilter === 'geaendert') return r.type === 'common' && visCols.some(c => (r.byCol[c].rec || {}).verdict === 'changed');
      return true;
    });
    // Schnellfilter „nur Unterschiede": geänderte Zeilen + hinzugefügt + gelöscht
    const finalRows = opts.onlyChanged
      ? filtered.filter(r => r.type !== 'common' || visCols.some(c => (r.byCol[c].rec || {}).verdict === 'changed'))
      : filtered;

    const c = meta.counts || {};
    let changedRows = 0;
    order.forEach(k => { if (allCols.some(col => (map[k][col] || {}).verdict === 'changed')) changedRows++; });
    let html = `<div class="cmp-summary">Gemeinsame Schlüssel: <b>${c.common != null ? c.common : order.length}</b> · ` +
      `Zeilen mit Änderung: <b>${changedRows}</b> · hinzugefügt: <b>${(meta.only_in_b || []).length}</b> · ` +
      `gelöscht: <b>${(meta.only_in_a || []).length}</b> · Spalten: <b>${visCols.length}/${allCols.length}</b></div>`;
    if ((meta.columns_only_a || []).length)
      html += `<div class="planner-muted" style="font-size:11.5px">Spalten nur in A: ${meta.columns_only_a.map(_esc).join(', ')}</div>`;
    if ((meta.columns_only_b || []).length)
      html += `<div class="planner-muted" style="font-size:11.5px">Spalten nur in B: ${meta.columns_only_b.map(_esc).join(', ')}</div>`;

    // Kopf (zweizeilig, fixiert)
    let head = `<thead><tr><th class="cmp-corner" rowspan="2">Schlüssel</th>` +
      visCols.map(cn => `<th class="cmp-grp" colspan="${sub.length}">${_esc(cn)}</th>`).join('') + '</tr><tr>' +
      visCols.map(() => sub.map(s => `<th class="cmp-subh">${_SUBLABEL[s]}</th>`).join('')).join('') + '</tr></thead>';

    let body = '';
    finalRows.forEach(r => {
      const badge = r.type === 'added' ? '<span class="cmp-badge cmp-add">＋</span> '
        : r.type === 'deleted' ? '<span class="cmp-badge cmp-del">－</span> ' : '';
      let tr = `<tr class="cmp-row-${r.type}"><td class="cmp-key">${badge}<b>${_esc(r.key)}</b></td>`;
      visCols.forEach(cn => {
        const o = r.byCol[cn], rec = o.rec;
        sub.forEach(s => {
          if (s === 'a') {
            const cls = (rec && rec.verdict === 'changed') ? 'cmp-val cmp-a' : (r.type === 'deleted' ? 'cmp-val cmp-del' : 'cmp-val');
            tr += `<td class="${cls}"><span class="cmp-cellbox">${_esc(o.a)}</span></td>`;
          } else if (s === 'b') {
            const cls = (rec && rec.verdict === 'changed') ? 'cmp-val cmp-b' : (r.type === 'added' ? 'cmp-val cmp-add' : 'cmp-val');
            tr += `<td class="${cls}"><span class="cmp-cellbox">${_esc(o.b)}</span></td>`;
          } else {
            if (r.type === 'deleted') { tr += '<td class="cmp-v cmp-del">gelöscht</td>'; return; }
            if (r.type === 'added') { tr += '<td class="cmp-v cmp-add">hinzugefügt</td>'; return; }
            if (!rec) { tr += '<td class="cmp-v cmp-eq">—</td>'; return; }
            if (rec.summary) { tr += `<td class="cmp-v cmp-ne"><span class="cmp-cellbox cmp-ki-sum">🧠 ${_esc(rec.summary)}</span></td>`; return; }
            if (rec.verdict === 'changed') {
              tr += ki
                ? `<td class="cmp-v cmp-ne cmp-ki-cell" data-key="${_esc(r.key)}" data-col="${_esc(cn)}" title="Klicken: KI erklärt den Unterschied">✗ <span class="cmp-ki-hint">🧠</span></td>`
                : '<td class="cmp-v cmp-ne">✗ ungleich</td>';
              return;
            }
            tr += '<td class="cmp-v cmp-eq">✓</td>';
          }
        });
      });
      body += tr + '</tr>';
    });
    if (!finalRows.length) body = `<tr><td colspan="${visCols.length * sub.length + 1}" class="planner-muted">Keine Zeilen für diesen Filter.</td></tr>`;

    return html + `<div class="cmp-matrix-wrap"><table class="cmp-matrix">${head}<tbody>${body}</tbody></table></div>`;
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
      const savedPrompt = (_meta && _meta.ki_prompts && _meta.ki_prompts[name]) || '';
      return { name, inA, inB, mode, metric: p.metric || 'exact', kiPrompt: p.kiPrompt != null ? p.kiPrompt : savedPrompt };
    });
    const metricOpts = [['exact', 'Inhalt exakt'], ['nospace', 'ohne Leerzeichen'], ['numeric', 'Zahl'], ['length', 'nur Länge']];
    let html = '<tr><th>Spalte</th><th>Aktion</th><th>Logik-Metrik</th><th>KI-Vergleich <span class="cmp-col-only">(leer = Standard, sonst eigener Prompt; nur bei Unterschied)</span></th></tr>';
    _cfg.forEach((c, i) => {
      const only = !c.inA || !c.inB;
      const badge = only ? ` <span class="cmp-col-only">(nur ${c.inA ? 'A' : 'B'})</span>` : (c.name === keyA ? ' <span class="cmp-col-only">(Schlüssel)</span>' : '');
      const modeSel = only ? '<span class="cmp-col-only">ignoriert</span>' :
        `<select data-i="${i}" class="cmp-mode">
           <option value="ignore"${c.mode === 'ignore' ? ' selected' : ''}>ignorieren</option>
           <option value="logic"${c.mode === 'logic' ? ' selected' : ''}>vergleichen</option>
         </select>`;
      const metSel = only ? '' :
        `<select data-i="${i}" class="cmp-metric">` +
        metricOpts.map(([v, l]) => `<option value="${v}"${c.metric === v ? ' selected' : ''}>${l}</option>`).join('') +
        '</select>';
      const kiInput = only ? '' :
        `<input type="text" data-i="${i}" class="cmp-ki-prompt" value="${_esc(c.kiPrompt || '')}" placeholder="Standard – oder eigener Prompt, z. B. „Nur Zahlenänderung nennen"">`;
      html += `<tr><td>${_esc(c.name)}${badge}</td><td>${modeSel}</td><td>${metSel}</td><td>${kiInput}</td></tr>`;
    });
    body.innerHTML = html;
    body.querySelectorAll('.cmp-mode').forEach(s => s.addEventListener('change', e => { _cfg[+e.target.dataset.i].mode = e.target.value; }));
    body.querySelectorAll('.cmp-metric').forEach(s => s.addEventListener('change', e => { _cfg[+e.target.dataset.i].metric = e.target.value; }));
    body.querySelectorAll('.cmp-ki-prompt').forEach(s => s.addEventListener('input', e => { _cfg[+e.target.dataset.i].kiPrompt = e.target.value; }));
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

  function _visibleCols() {
    // Spalten-Ein/Ausblenden: angehakte Kästchen in #cmp-cols; keine → alle
    const box = _el('cmp-cols');
    if (!box) return null;
    const on = [...box.querySelectorAll('input[type=checkbox]')].filter(c => c.checked).map(c => c.value);
    const all = (_meta && _meta.compared_columns) || [];
    return on.length ? all.filter(c => on.includes(c)) : all;
  }

  function _renderResult() {
    const opts = {
      view: (_el('cmp-view') && _el('cmp-view').value) || 'alles',
      rows: (_el('cmp-rows') && _el('cmp-rows').value) || 'alle',
      cols: _visibleCols(),
      onlyChanged: !!(_el('cmp-only-changed') && _el('cmp-only-changed').checked),
      ki: _kiOn,
    };
    const host = _el('cmp-result');
    // Spaltenbreite je Teilspalte (langer Text bricht dann um → mehr Pakete nebeneinander)
    const w = _el('cmp-cellw');
    if (w) host.style.setProperty('--cmp-cellw', (parseInt(w.value, 10) || 220) + 'px');
    host.innerHTML = renderCellsHtml(_meta, _cells, opts);
    if (_kiOn) host.querySelectorAll('.cmp-ki-cell').forEach(td =>
      td.addEventListener('click', () => _kiCell(td.dataset.key, td.dataset.col)));
  }

  // Spalten-Ein/Ausblenden aus den verglichenen Spalten aufbauen (bei neuem Ergebnis)
  function _fillColFilter() {
    const box = _el('cmp-cols');
    if (!box) return;
    const cols = (_meta && _meta.compared_columns) || [];
    box.innerHTML = cols.length
      ? cols.map(c => `<label class="cmp-colchk"><input type="checkbox" value="${_esc(c)}" checked> ${_esc(c)}</label>`).join('')
      : '<span class="planner-muted" style="font-size:11.5px">—</span>';
    box.querySelectorAll('input').forEach(i => i.addEventListener('change', _renderResult));
  }

  async function _run(resume) {
    // Bequemlichkeit: sind Dateien gewählt, aber noch nicht eingelesen → automatisch nachholen.
    if (!_A.file_id && _el('cmp-file-a').files && _el('cmp-file-a').files[0]) await _readSide('a', _A, false);
    if (!_B.file_id && _el('cmp-file-b').files && _el('cmp-file-b').files[0]) await _readSide('b', _B, false);
    if (!_A.file_id || !_B.file_id) {
      const msg = 'Bitte zuerst beide Tabellen wählen und „Einlesen" klicken.';
      _status(msg); if (typeof showToast === 'function') showToast(msg); return;
    }
    if (_running) return;
    const _rname = (_el('cmp-save-name').value || '').trim() || 'Ergebnis';
    if (!resume) { _meta = null; _cellMap = {}; _keyOrder = []; _cells = []; _el('cmp-result').innerHTML = ''; }
    _switchSub('result');
    _setRunning(true); _status('Vergleiche zellenweise…');
    let total = 0, _done = false, _errored = false;
    await runCellStream(_params(resume), {
      onReader: (r) => { _reader = r; },
      onMeta: (ev) => { _meta = ev; total = (ev.counts || {}).cells || 0; _upsertResult(_rname, _meta, _cells); _renderSubbar(); _fillColFilter(); _renderResult(); },
      onCell: (ev) => {
        const key = ev.key + '\u0000' + ev.column;
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
        _done = true;
      },
      onError: (msg) => { _errored = true; _setRunning(false); _status('Fehler: ' + msg); _el('cmp-result').innerHTML += `<div class="var-cr-bad">Fehler: ${_esc(msg)}</div>`; },
    });
    if (_errored) return;
    if (!_done) { _setRunning(false); _status('Angehalten.'); return; }   // Stream endete ohne done
    // KI automatisch: nach dem Logikvergleich alle Unterschiede erklären (Voreinstellung).
    if (_autoKiEnabled()) {
      _status('KI erklärt Unterschiede…');
      await _kiExplainAll(_cells.filter(c => c.verdict === 'changed' && !c.summary));
      _setRunning(false); _status('Fertig (inkl. KI).');
    } else {
      _setRunning(false); _status('Fertig.');
    }
  }

  function _autoKiEnabled() { return !!(_el('cmp-ki-auto') && _el('cmp-ki-auto').checked); }

  function _stop() {
    _kiStop = true;
    if (_reader) { try { _reader.cancel(); } catch (_) {} }
    _setRunning(false); _status('Angehalten — „▶ Fortsetzen" setzt fort.');
  }

  // ── Untertabs: „⚙ Einstellen" + je Lauf/Job ein Ergebnis-Reiter (mit Namen) ──
  function _switchSub(name) {
    _view = (name === 'settings') ? 'settings' : 'result';
    const ps = _el('cmp-sub-settings'), pr = _el('cmp-sub-result');
    if (ps) ps.classList.toggle('active', _view === 'settings');
    if (pr) pr.classList.toggle('active', _view === 'result');
    _renderSubbar();
  }

  function _renderSubbar() {
    const bar = _el('cmp-subbar');
    if (!bar) return;
    let h = `<button class="cmp-subtab ${_view === 'settings' ? 'active' : ''}" data-nav="settings">⚙ Einstellen</button>`;
    _results.forEach((r, i) => {
      h += `<button class="cmp-subtab ${(_view === 'result' && _activeResult === i) ? 'active' : ''}" data-nav="result" data-ri="${i}" title="Ergebnis: ${_esc(r.name)}">📊 ${_esc(r.name || 'Ergebnis')}</button>`;
    });
    bar.innerHTML = h;
    bar.querySelectorAll('button').forEach(b => b.addEventListener('click', () => {
      if (b.dataset.nav === 'settings') _switchSub('settings'); else _selectResult(+b.dataset.ri);
    }));
  }

  // Ein Ergebnis (Name → meta+cells) anlegen/aktualisieren und aktiv setzen.
  function _upsertResult(name, meta, cells) {
    name = (name || 'Ergebnis');
    let i = _results.findIndex(r => r.name === name);
    if (i < 0) { _results.push({ name, meta, cells }); i = _results.length - 1; }
    else { _results[i].meta = meta; _results[i].cells = cells; }
    _activeResult = i;
    return i;
  }

  // Zu einem Ergebnis-Reiter wechseln → dessen Daten in die Anzeige laden.
  function _selectResult(i) {
    if (i < 0 || i >= _results.length) return;
    _activeResult = i;
    const r = _results[i];
    _meta = r.meta; _cells = r.cells;
    _cellMap = {}; _cells.forEach((c, k) => _cellMap[c.key + '\u0000' + c.column] = k);
    if (_el('cmp-save-name')) _el('cmp-save-name').value = r.name || '';
    _fillColFilter();
    _switchSub('result');
    _renderResult();
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
      // PUT legt an ODER aktualisiert (kein POST→409→PUT mehr → saubere Logs, eine Anfrage weniger).
      const r = await fetch('/api/compare/projects/' + encodeURIComponent(name), { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
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
          ki_prompts: data.ki_prompts || {},
        };
        _cells = data.cells || []; _cellMap = {}; _cells.forEach((c, i) => _cellMap[c.key + '\u0000' + c.column] = i);
        _el('cmp-save-name').value = name;
        _upsertResult(name, _meta, _cells); _renderSubbar();
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

  // Matrix in einem der Formate (xlsx/csv/json/html) vom Server bauen lassen → Download.
  async function _export(fmt) {
    if (!_cells.length && !_meta) { _status('Erst vergleichen.'); return; }
    const name = (_el('cmp-save-name').value || 'excel-vergleich').trim();
    _status('Exportiere ' + fmt.toUpperCase() + '…');
    try {
      const r = await fetch('/api/compare/export', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ format: fmt, meta: _meta, cells: _cells, name }),
      });
      if (!r.ok) { let m = 'HTTP ' + r.status; try { m = (await r.json()).detail || m; } catch (_) {} _status('Export-Fehler: ' + m); return; }
      const blob = await r.blob();
      const a = document.createElement('a');
      a.href = URL.createObjectURL(blob);
      a.download = name + '.' + fmt;
      a.click();
      _status('');
    } catch (e) { _status('Export-Fehler: ' + e.message); }
  }

  // ── on-demand-KI (Klick in eine leere ungleich-Zelle · KI-Job) ─────────────
  function _toggleKi() {
    _kiOn = !_kiOn;
    if (_el('cmp-ki-toggle')) _el('cmp-ki-toggle').classList.toggle('active', _kiOn);
    if (_el('btn-cmp-ki-job')) _el('btn-cmp-ki-job').style.display = _kiOn ? '' : 'none';
    if (_el('cmp-ki-hint')) _el('cmp-ki-hint').style.display = _kiOn ? '' : 'none';
    _renderResult();
  }

  // Spaltenspezifischer KI-Prompt (aus der Einstellen-Konfig oder gespeichert); leer = Standard.
  function _kiPromptFor(col) {
    const c = _cfg.find(x => x.name === col);
    if (c && c.kiPrompt) return c.kiPrompt;
    if (_meta && _meta.ki_prompts && _meta.ki_prompts[col]) return _meta.ki_prompts[col];
    return '';
  }
  function _collectKiPrompts() {
    const m = Object.assign({}, (_meta && _meta.ki_prompts) || {});
    _cfg.forEach(c => { if (c.kiPrompt) m[c.name] = c.kiPrompt; });
    return m;
  }

  async function _kiOne(rec) {
    const r = await fetch('/api/compare/cell-ki', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ key: rec.key, column: rec.column, a: rec.a, b: rec.b, prompt: _kiPromptFor(rec.column) || undefined, model: _model() || undefined }),
    });
    if (!r.ok) { let m = 'HTTP ' + r.status; try { m = (await r.json()).detail || m; } catch (_) {} throw new Error(m); }
    const d = await r.json();
    rec.summary = d.summary || '(kein Unterschied benannt)';
    if (d.tokens && typeof TokenMeter !== 'undefined') TokenMeter.add(d.tokens, 'Excel-Vergleich');
  }

  async function _kiCell(key, col) {
    const rec = _cells.find(x => String(x.key) === String(key) && String(x.column) === String(col));
    if (!rec || rec.verdict !== 'changed' || rec.summary) return;
    _status('KI erklärt Zelle…');
    try { await _kiOne(rec); _status(''); _renderResult(); _saveResults(); }
    catch (e) { _status('KI-Fehler: ' + e.message); }
  }

  // Erklärt eine Liste geänderter Zellen seriell (geteilt: KI-Job, KI-automatisch, Stapel-Job).
  async function _kiExplainAll(targets) {
    if (!targets || !targets.length) return;
    _kiStop = false;
    _el('cmp-progress-wrap').style.display = '';
    for (let i = 0; i < targets.length; i++) {
      if (_kiStop) break;
      _el('cmp-progress-label').textContent = `KI ${i + 1}/${targets.length}`;
      _el('cmp-progress-fill').style.width = Math.round(i / targets.length * 100) + '%';
      try { await _kiOne(targets[i]); } catch (e) { _status('KI-Fehler: ' + e.message); break; }
      if (i % 5 === 0) { _renderResult(); _saveResults(); }
    }
    _el('cmp-progress-fill').style.width = '100%';
    _renderResult(); _saveResults();
  }

  async function _kiJob() {
    if (_running) return;
    const targets = _cells.filter(c => c.verdict === 'changed' && !c.summary);
    if (!targets.length) { _status('Keine offenen Unterschiede für die KI.'); return; }
    _running = true; _spin(true);
    await _kiExplainAll(targets);
    _running = false; _spin(false); _status('KI-Job fertig.');
  }

  // Live-Speichern der Ergebnisse (inkl. KI-summary), entprellt; nur bei gesetztem Namen.
  function _saveResults() {
    const name = (_el('cmp-save-name').value || '').trim();
    if (!name || _saveTimer) return;
    _saveTimer = setTimeout(async () => {
      _saveTimer = 0;
      try {
        const meta = Object.assign({}, _meta, { ki_prompts: _collectKiPrompts() });
        await fetch('/api/compare/projects/' + encodeURIComponent(name) + '/results', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ meta, cells: _cells, complete: true }),
        });
      } catch (_) {}
    }, 800);
  }

  function _importJson(file) {
    const reader = new FileReader();
    reader.onload = e => {
      try {
        const data = JSON.parse(e.target.result);
        _meta = data.meta || { compared_columns: [], only_in_a: [], only_in_b: [], counts: {} };
        _cells = data.cells || []; _cellMap = {}; _cells.forEach((c, i) => _cellMap[c.key + '\u0000' + c.column] = i);
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
          onMeta: (ev) => { _meta = ev; _upsertResult(job.name, _meta, _cells); _renderSubbar(); _fillColFilter(); _renderResult(); },
          onCell: (ev) => { const k = ev.key + '\u0000' + ev.column; if (!(k in _cellMap)) { _cellMap[k] = _cells.length; _cells.push(ev); } else _cells[_cellMap[k]] = ev; _scheduleResultRender(); },
          onProgress: (ev) => { const t = ev.total || 0; _el('cmp-progress-fill').style.width = (t ? Math.round(ev.done / t * 100) : 0) + '%'; _el('cmp-progress-label').textContent = `${job.name}: ${ev.done}/${t}`; },
          onDone: (ev) => { _renderResult(); if (ev.tokens && typeof TokenMeter !== 'undefined') TokenMeter.add(ev.tokens, 'Excel-Vergleich'); _save(); resolve(); },
          onError: (msg) => { _el('cmp-job-status').textContent = `Paar ${job.name}: ${msg}`; resolve(); },
        });
      });
      // KI automatisch je Paar (Voreinstellung bei Jobs): Unterschiede erklären + speichern
      if (_autoKiEnabled()) {
        _el('cmp-job-status').textContent = `Paar ${i + 1}/${queue.length}: ${job.name} — KI…`;
        await _kiExplainAll(_cells.filter(c => c.verdict === 'changed' && !c.summary));
      }
      // Job-Ergebnisse sofort ungefiltert in ALLEN Formaten exportieren (Excel/CSV/JSON/HTML)
      _el('cmp-job-status').textContent = `Paar ${i + 1}/${queue.length}: ${job.name} — Export…`;
      for (const fmt of ['xlsx', 'csv', 'json', 'html']) { try { await _export(fmt); } catch (_) {} }
      _setRunning(false);
    }
    _el('cmp-job-status').textContent = `Job fertig (${queue.length} Paar(e)) — exportiert.`;
    await _loadProjects();
  }

  // ── Init ───────────────────────────────────────────────────────────────────
  function init() {
    if (!_el('compare-panel')) return;
    const _on = (id, evt, fn) => { const e = _el(id); if (e) e.addEventListener(evt, fn); };
    _on('btn-cmp-read-a', 'click', () => _readSide('a', _A, false));
    _on('btn-cmp-read-b', 'click', () => _readSide('b', _B, false));
    _on('cmp-sheet-a', 'change', () => _readSide('a', _A, true));
    _on('cmp-sheet-b', 'change', () => _readSide('b', _B, true));
    _on('cmp-key-a', 'change', _buildColCfg);
    _on('cmp-key-b', 'change', _buildColCfg);
    _on('btn-cmp-run', 'click', () => _run(false));
    _on('btn-cmp-stop', 'click', _stop);
    _on('btn-cmp-resume', 'click', () => _run(true));
    _on('btn-cmp-save', 'click', _save);
    _on('cmp-project', 'change', e => _openProject(e.target.value));
    _on('btn-cmp-delete', 'click', _deleteProject);
    // Filter (Spalten-Ansicht / Zeilen / nur Unterschiede / Anwenden) + KI + Export
    _on('cmp-view', 'change', _renderResult);
    _on('cmp-rows', 'change', _renderResult);
    _on('cmp-only-changed', 'change', _renderResult);
    _on('btn-cmp-apply', 'click', _renderResult);
    // Spaltenbreite: live (nur CSS-Variable setzen, kein Neu-Rendern nötig)
    _on('cmp-cellw', 'input', () => { const h = _el('cmp-result'); const w = _el('cmp-cellw'); if (h && w) h.style.setProperty('--cmp-cellw', (parseInt(w.value, 10) || 220) + 'px'); });
    _on('cmp-ki-toggle', 'click', _toggleKi);
    _on('btn-cmp-ki-job', 'click', _kiJob);
    _on('btn-cmp-xlsx', 'click', () => _export('xlsx'));
    _on('btn-cmp-csv', 'click', () => _export('csv'));
    _on('btn-cmp-json', 'click', () => _export('json'));
    _on('btn-cmp-html', 'click', () => _export('html'));
    _on('btn-cmp-job-add', 'click', _jobAdd);
    _on('btn-cmp-job-run', 'click', _jobRun);
    _on('btn-cmp-job-clear', 'click', () => { _jobQueue = []; _renderJobList(); });
    _renderJobList();
    _renderSubbar();   // dynamische Reiterleiste (Einstellen + je Ergebnis)
    _loadProjects();
  }

  return { init, preview, runStream, renderDiffHtml, diffCsv, runCellStream, renderCellsHtml };
})();
