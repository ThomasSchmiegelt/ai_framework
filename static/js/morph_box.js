// morph_box.js — 🧩 Morphologischer Kasten (Zwicky-Box) mit KI
// Ideenfindungs-Raster: Parameter (Zeilen) × Ausprägungen (Werte). Eine Lösung =
// je Parameter eine Ausprägung. Die KI generiert Parameter/Ausprägungen, bewertet
// die gewählte Kombination (+ schlägt Alternativen vor) und verfeinert Zellen.
// Export über bestehende Wege (DOCX / Doku / Wissensdatenbank).
const MorphBox = (() => {
  const LS = 'ai_framework_thomas_morph_v1';

  let _problem = '';
  let _params = [];          // [{ name, values:[…] }]
  const _selection = {};     // { paramIndex: valueIndex }

  function _el(id) { return document.getElementById(id); }
  function _model() {
    return (typeof Profile !== 'undefined' ? Profile.modelFor('general') : '') || undefined;
  }

  function _save() {
    try {
      localStorage.setItem(LS, JSON.stringify({ problem: _problem, params: _params, selection: _selection }));
    } catch (_) {}
    _flashSaved();
  }
  function _flashSaved() {
    const l = _el('morph-autosave');
    if (!l) return;
    l.style.display = 'inline';
    clearTimeout(_flashSaved._t);
    _flashSaved._t = setTimeout(() => { l.style.display = 'none'; }, 1200);
  }
  function _load() {
    try {
      const raw = localStorage.getItem(LS);
      if (!raw) return;
      const d = JSON.parse(raw);
      _problem = d.problem || '';
      _params = Array.isArray(d.params) ? d.params : [];
      Object.assign(_selection, d.selection || {});
    } catch (_) {}
  }

  // ── KI-Aktionen ────────────────────────────────────────────────────────────
  async function _generate() {
    _problem = (_el('morph-problem')?.value || '').trim();
    if (!_problem) { showToast('Bitte zuerst eine Aufgabenstellung eingeben'); return; }
    const btn = _el('btn-morph-generate');
    if (btn) { btn.disabled = true; btn.textContent = '… generiert'; }
    try {
      const resp = await fetch('/api/morph/generate', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ problem: _problem, model: _model() }),
      });
      if (!resp.ok) throw new Error((await resp.json()).detail || resp.statusText);
      const data = await resp.json();
      _params = data.parameters || [];
      Object.keys(_selection).forEach(k => delete _selection[k]);
      _render(); _save();
    } catch (e) {
      showToast('Fehler: ' + e.message);
    } finally {
      if (btn) { btn.disabled = false; btn.textContent = '🤖 Parameter generieren'; }
    }
  }

  function _selectionList() {
    const sel = [];
    _params.forEach((p, i) => {
      if (_selection[i] != null && p.values[_selection[i]] != null) {
        sel.push({ parameter: p.name, value: p.values[_selection[i]] });
      }
    });
    return sel;
  }

  async function _evaluate() {
    const selection = _selectionList();
    if (selection.length < 2) { showToast('Bitte in mindestens zwei Zeilen eine Ausprägung wählen'); return; }
    const out = _el('morph-eval');
    out.style.display = 'block';
    out.innerHTML = '<em>KI bewertet die Kombination …</em>';
    try {
      const resp = await fetch('/api/morph/evaluate', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ problem: _problem, selection, parameters: _params, model: _model() }),
      });
      if (!resp.ok) throw new Error((await resp.json()).detail || resp.statusText);
      _renderEval(await resp.json());
    } catch (e) {
      out.innerHTML = `<span style="color:var(--danger,#e66)">Fehler: ${e.message}</span>`;
    }
  }

  function _renderEval(d) {
    const out = _el('morph-eval');
    const bar = (label, v) => {
      const n = Math.max(0, Math.min(100, Number(v) || 0));
      return `<div class="morph-bar"><span>${label}</span><div class="morph-bar-track"><div class="morph-bar-fill" style="width:${n}%"></div></div><b>${n}</b></div>`;
    };
    let html = '<div class="morph-eval-head">📊 Bewertung</div>';
    html += bar('Gesamt', d.score) + bar('Machbarkeit', d.machbarkeit) + bar('Innovation', d.innovation);
    if (d.begruendung) html += `<p>${_esc(d.begruendung)}</p>`;
    if ((d.risiken || []).length) {
      html += '<div class="morph-eval-sub">⚠️ Risiken</div><ul>' +
        d.risiken.map(r => `<li>${_esc(r)}</li>`).join('') + '</ul>';
    }
    if ((d.vorschlaege || []).length) {
      html += '<div class="morph-eval-sub">💡 Vorgeschlagene Kombinationen</div>';
      d.vorschlaege.forEach((v, idx) => {
        const picks = (v.picks || []).map(p => `${_esc(p.parameter)}: <b>${_esc(p.value)}</b>`).join(' · ');
        html += `<div class="morph-suggest"><button class="morph-apply" data-i="${idx}">Übernehmen</button> <span class="morph-suggest-score">${v.score != null ? v.score : ''}</span> ${picks}<br><small>${_esc(v.begruendung || '')}</small></div>`;
      });
    }
    out.innerHTML = html;
    out.querySelectorAll('.morph-apply').forEach(b => {
      b.addEventListener('click', () => _applySuggestion(d.vorschlaege[+b.dataset.i]));
    });
  }

  function _applySuggestion(v) {
    if (!v || !v.picks) return;
    v.picks.forEach(pick => {
      const pi = _params.findIndex(p => p.name === pick.parameter);
      if (pi < 0) return;
      let vi = _params[pi].values.indexOf(pick.value);
      if (vi < 0) { _params[pi].values.push(pick.value); vi = _params[pi].values.length - 1; }
      _selection[pi] = vi;
    });
    _render(); _save();
    showToast('✓ Kombination übernommen');
  }

  async function _refineCell(pi, vi, action) {
    const p = _params[pi];
    const value = p.values[vi];
    try {
      const resp = await fetch('/api/morph/refine-cell', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ problem: _problem, parameter: p.name, value, action, model: _model() }),
      });
      if (!resp.ok) throw new Error((await resp.json()).detail || resp.statusText);
      const d = await resp.json();
      if (action === 'expand' && d.text) {
        p.values[vi] = d.text;
      } else if (action === 'critique') {
        (d.alternativen || []).forEach(a => { if (a && !p.values.includes(a)) p.values.push(a); });
        if (d.text) showToast('💬 ' + d.text);
      }
      _render(); _save();
    } catch (e) {
      showToast('Fehler: ' + e.message);
    }
  }

  // ── Rendering ──────────────────────────────────────────────────────────────
  function _render() {
    if (_el('morph-problem') && document.activeElement !== _el('morph-problem')) {
      _el('morph-problem').value = _problem;
    }
    const grid = _el('morph-grid');
    grid.innerHTML = '';
    _params.forEach((p, pi) => {
      const row = document.createElement('div');
      row.className = 'morph-row';

      const nameWrap = document.createElement('div');
      nameWrap.className = 'morph-param';
      const name = document.createElement('input');
      name.className = 'morph-param-name';
      name.value = p.name;
      name.addEventListener('change', () => { p.name = name.value.trim(); _save(); });
      const del = document.createElement('button');
      del.className = 'morph-param-del';
      del.textContent = '✕'; del.title = 'Parameter löschen';
      del.addEventListener('click', () => { _params.splice(pi, 1); delete _selection[pi]; _reindex(); _render(); _save(); });
      nameWrap.appendChild(name); nameWrap.appendChild(del);
      row.appendChild(nameWrap);

      const cells = document.createElement('div');
      cells.className = 'morph-cells';
      p.values.forEach((v, vi) => cells.appendChild(_chip(pi, vi)));
      const add = document.createElement('button');
      add.className = 'morph-chip-add';
      add.textContent = '＋';
      add.title = 'Ausprägung hinzufügen';
      add.addEventListener('click', () => { p.values.push('Neue Ausprägung'); _render(); _save(); });
      cells.appendChild(add);
      row.appendChild(cells);
      grid.appendChild(row);
    });

    const sol = _selectionList();
    _el('morph-solution').textContent = sol.length
      ? '✓ Lösung: ' + sol.map(s => s.value).join(' · ') : '';
  }

  function _chip(pi, vi) {
    const chip = document.createElement('div');
    const selected = _selection[pi] === vi;
    chip.className = 'morph-chip' + (selected ? ' morph-chip--sel' : '');

    const txt = document.createElement('span');
    txt.className = 'morph-chip-txt';
    txt.textContent = _params[pi].values[vi];
    txt.title = 'Klicken = für die Kombination wählen · Doppelklick = bearbeiten';
    txt.addEventListener('click', () => {
      _selection[pi] = (_selection[pi] === vi) ? undefined : vi;
      if (_selection[pi] === undefined) delete _selection[pi];
      _render(); _save();
    });
    txt.addEventListener('dblclick', () => _editChip(pi, vi, txt));
    chip.appendChild(txt);

    const tools = document.createElement('span');
    tools.className = 'morph-chip-tools';
    const star = document.createElement('button');
    star.textContent = '✨'; star.title = 'Ausformulieren (KI)';
    star.addEventListener('click', e => { e.stopPropagation(); _refineCell(pi, vi, 'expand'); });
    const crit = document.createElement('button');
    crit.textContent = '💬'; crit.title = 'Kritik / Alternativen (KI)';
    crit.addEventListener('click', e => { e.stopPropagation(); _refineCell(pi, vi, 'critique'); });
    const x = document.createElement('button');
    x.textContent = '✕'; x.title = 'Ausprägung löschen';
    x.addEventListener('click', e => {
      e.stopPropagation();
      _params[pi].values.splice(vi, 1);
      if (_selection[pi] === vi) delete _selection[pi];
      else if (_selection[pi] > vi) _selection[pi]--;
      _render(); _save();
    });
    tools.appendChild(star); tools.appendChild(crit); tools.appendChild(x);
    chip.appendChild(tools);
    return chip;
  }

  function _editChip(pi, vi, txtEl) {
    const inp = document.createElement('input');
    inp.className = 'morph-chip-edit';
    inp.value = _params[pi].values[vi];
    txtEl.replaceWith(inp);
    inp.focus(); inp.select();
    const commit = () => { _params[pi].values[vi] = inp.value.trim() || _params[pi].values[vi]; _render(); _save(); };
    inp.addEventListener('blur', commit);
    inp.addEventListener('keydown', e => { if (e.key === 'Enter') inp.blur(); });
  }

  function _reindex() {
    // Selection-Keys nach Parameter-Löschung neu ausrichten
    const old = { ..._selection };
    Object.keys(_selection).forEach(k => delete _selection[k]);
    Object.keys(old).forEach(k => { if (+k < _params.length) _selection[k] = old[k]; });
  }

  function _addParam() {
    _params.push({ name: 'Neuer Parameter', values: ['Ausprägung 1'] });
    _render(); _save();
  }

  // ── Export / CSV ─────────────────────────────────────────────────────────
  function _toMarkdown() {
    let md = `# Morphologischer Kasten\n\n**Aufgabe:** ${_problem || '—'}\n\n`;
    md += '| Parameter | Ausprägungen |\n|---|---|\n';
    _params.forEach((p, pi) => {
      const vals = p.values.map((v, vi) => _selection[pi] === vi ? `**${v}**` : v).join(' · ');
      md += `| ${p.name} | ${vals} |\n`;
    });
    const sol = _selectionList();
    if (sol.length) {
      md += '\n## Gewählte Lösung\n\n';
      sol.forEach(s => { md += `- **${s.parameter}:** ${s.value}\n`; });
    }
    return md;
  }

  function _title() { return 'Morphologischer Kasten' + (_problem ? ' – ' + _problem.slice(0, 40) : ''); }

  async function _exportDocx() {
    try {
      const resp = await fetch('/api/export/docx', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ type: 'document', title: _title(), content: _toMarkdown() }),
      });
      if (!resp.ok) throw new Error(resp.statusText);
      const blob = await resp.blob();
      const a = document.createElement('a');
      a.href = URL.createObjectURL(blob);
      a.download = 'morphologischer_kasten.docx';
      a.click(); URL.revokeObjectURL(a.href);
    } catch (e) { showToast('Fehler: ' + e.message); }
  }

  function _exportDoku() {
    if (typeof DocGen === 'undefined') { showToast('Dokumente-Tab nicht verfügbar'); return; }
    DocGen.showResult(_toMarkdown());
    if (typeof switchTab === 'function') switchTab('docgen');
    showToast('✓ In Dokumente übernommen');
  }

  function _exportRag() {
    if (typeof RAG === 'undefined' || !RAG.ingestText) { showToast('RAG nicht verfügbar'); return; }
    RAG.ingestText(_title(), _toMarkdown());
  }

  function _exportCsv() {
    let csv = 'Parameter,Ausprägungen\n';
    _params.forEach(p => {
      csv += `"${(p.name || '').replace(/"/g, '""')}","${p.values.join(' | ').replace(/"/g, '""')}"\n`;
    });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(new Blob([csv], { type: 'text/csv' }));
    a.download = 'morphologischer_kasten.csv';
    a.click(); URL.revokeObjectURL(a.href);
  }

  function _importCsv(file) {
    const reader = new FileReader();
    reader.onload = () => {
      const lines = String(reader.result).split(/\r?\n/).filter(l => l.trim());
      const out = [];
      lines.slice(1).forEach(line => {
        const m = line.match(/^"((?:[^"]|"")*)","((?:[^"]|"")*)"/);
        if (!m) return;
        const name = m[1].replace(/""/g, '"');
        const vals = m[2].replace(/""/g, '"').split('|').map(s => s.trim()).filter(Boolean);
        if (name && vals.length) out.push({ name, values: vals });
      });
      if (out.length) {
        _params = out;
        Object.keys(_selection).forEach(k => delete _selection[k]);
        _render(); _save();
        showToast('✓ CSV importiert');
      } else showToast('Keine Daten erkannt');
    };
    reader.readAsText(file);
  }

  function _clear() {
    if (!confirm('Morphologischen Kasten leeren?')) return;
    _problem = ''; _params = [];
    Object.keys(_selection).forEach(k => delete _selection[k]);
    if (_el('morph-problem')) _el('morph-problem').value = '';
    if (_el('morph-eval')) _el('morph-eval').style.display = 'none';
    _render(); _save();
  }

  function _esc(s) {
    return String(s == null ? '' : s).replace(/[&<>]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;' }[c]));
  }

  function init() {
    _load();
    _el('morph-problem')?.addEventListener('input', () => { _problem = _el('morph-problem').value; _save(); });
    _el('btn-morph-generate')?.addEventListener('click', _generate);
    _el('btn-morph-add-param')?.addEventListener('click', _addParam);
    _el('btn-morph-eval')?.addEventListener('click', _evaluate);
    _el('btn-morph-export-docx')?.addEventListener('click', _exportDocx);
    _el('btn-morph-export-doku')?.addEventListener('click', _exportDoku);
    _el('btn-morph-export-rag')?.addEventListener('click', _exportRag);
    _el('btn-morph-export-csv')?.addEventListener('click', _exportCsv);
    _el('btn-morph-import-csv')?.addEventListener('click', () => _el('morph-csv-input')?.click());
    _el('morph-csv-input')?.addEventListener('change', e => { if (e.target.files[0]) _importCsv(e.target.files[0]); e.target.value = ''; });
    _el('btn-morph-clear')?.addEventListener('click', _clear);
    _render();
  }

  return { init };
})();
