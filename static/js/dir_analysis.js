// dir_analysis.js — 📁 Verzeichnis-Analyse
// Liest einen lokalen Ordner (Server-Pfad), zeigt Struktur + KI-Überblick mit
// interessanten Dateien, analysiert einzelne Dateien vertieft und schreibt eine
// Index-/„init"-Datei (_KI_INDEX.md) in den Ordner zurück (optional als
// Wissensdatenbank). Personenbezogene Daten in den INHALTEN werden anonymisiert.
const DirAnalysis = (() => {
  const LS_PATH = 'ai_framework_thomas_diranalyse_path';

  let _scan = null;          // letztes Scan-Ergebnis
  const _analyses = {};      // { file_rel: markdown }
  let _busy = false;
  const _queue = [];         // ausstehende Datei-Analysen (rel)
  let _processing = false;   // läuft gerade eine Analyse?

  function _el(id) { return document.getElementById(id); }
  function _model() {
    return (typeof Profile !== 'undefined' ? Profile.modelFor('general') : '') || undefined;
  }
  // Anonymisierung von Personendaten ist Pflicht (immer an, serverseitig erzwungen).
  function _llmNer() { return !!_el('dir-llm-ner')?.checked; }
  function _path() { return (_el('dir-path')?.value || '').trim(); }

  function _render(el, md) {
    if (typeof Chat !== 'undefined' && Chat.renderMarkdown) Chat.renderMarkdown(el, md);
    else el.textContent = md;
  }

  function _setBusy(b, label) {
    _busy = b;
    const btn = _el('btn-dir-scan');
    if (btn) { btn.disabled = b; btn.textContent = b ? (label || '… scannt') : '🔍 Scannen'; }
  }

  async function _scanDir() {
    if (_busy) return;
    const path = _path();
    if (!path) { showToast('Bitte einen Server-Pfad eingeben'); return; }
    localStorage.setItem(LS_PATH, path);
    _setBusy(true, '… scannt');
    _el('dir-summary').innerHTML = '<em>KI erstellt einen Überblick …</em>';
    _el('dir-tree-list').innerHTML = '';
    _el('dir-detail').innerHTML = '';
    Object.keys(_analyses).forEach(k => delete _analyses[k]);
    _el('dir-actions').style.display = 'none';
    try {
      const resp = await fetch('/api/dir/scan', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ path, llm_ner: _llmNer(), model: _model() }),
      });
      if (!resp.ok) { throw new Error((await resp.json()).detail || resp.statusText); }
      _scan = await resp.json();
      _renderScan();
      _el('dir-actions').style.display = 'flex';
    } catch (e) {
      _el('dir-summary').innerHTML = `<span style="color:var(--danger,#e66)">Fehler: ${e.message}</span>`;
    } finally {
      _setBusy(false);
    }
  }

  function _renderScan() {
    if (!_scan) return;
    const meta = `<div class="dir-meta">📁 <strong>${_scan.name}</strong> · ${_scan.n_dirs} Ordner · ${_scan.n_files} Dateien`
      + (_scan.redacted ? ` · 🔒 ${_scan.redacted} Personendaten geschwärzt` : '')
      + (_scan.truncated ? ' · ⚠️ gekürzt' : '') + '</div>';
    _el('dir-summary').innerHTML = meta;
    const sumEl = document.createElement('div');
    sumEl.className = 'dir-summary-text';
    _render(sumEl, _scan.summary || '_(kein Überblick)_');
    _el('dir-summary').appendChild(sumEl);

    const interesting = new Map((_scan.interesting || []).map(i => [i.file, i.reason]));
    const list = _el('dir-tree-list');
    list.innerHTML = '';

    if (interesting.size) {
      const h = document.createElement('div');
      h.className = 'dir-tree-head';
      h.textContent = '⭐ Interessante Dateien';
      list.appendChild(h);
      for (const [file, reason] of interesting) {
        list.appendChild(_fileRow(file, reason, true));
      }
    }

    const h2 = document.createElement('div');
    h2.className = 'dir-tree-head';
    h2.textContent = '🗂️ Struktur';
    list.appendChild(h2);
    for (const f of (_scan.tree || [])) {
      if (f.is_dir) {
        const d = document.createElement('div');
        d.className = 'dir-row dir-row--dir';
        d.textContent = '📂 ' + f.rel + '/';
        list.appendChild(d);
      } else {
        list.appendChild(_fileRow(f.rel, '', interesting.has(f.rel)));
      }
    }
  }

  function _fileRow(rel, reason, highlight) {
    const row = document.createElement('div');
    row.className = 'dir-row dir-row--file' + (highlight ? ' dir-row--int' : '');
    const name = document.createElement('span');
    name.className = 'dir-row-name';
    name.textContent = '📄 ' + rel;
    row.appendChild(name);
    if (reason) {
      const r = document.createElement('span');
      r.className = 'dir-row-reason';
      r.textContent = reason;
      row.appendChild(r);
    }
    row.title = 'Klicken für Detailanalyse';
    row.addEventListener('click', () => _analyzeFile(rel));
    return row;
  }

  // Klick auf eine Datei: in die Warteschlange einreihen (NICHT parallel feuern —
  // der VRAM-Lock im Backend serialisiert ohnehin; parallele Verbindungen würden
  // untätig warten und vom Browser als „Failed to fetch" abgebrochen).
  function _analyzeFile(rel) {
    const detail = _el('dir-detail');
    // bereits analysiert oder schon in der Schlange → dorthin scrollen
    const existing = document.getElementById('dir-an-' + _slug(rel));
    if (existing) { existing.scrollIntoView({ behavior: 'smooth', block: 'start' }); return; }

    const block = document.createElement('div');
    block.className = 'dir-analysis-block';
    block.id = 'dir-an-' + _slug(rel);
    block.innerHTML = `<div class="dir-analysis-title">📄 ${rel}</div><div class="dir-analysis-body"><em>In Warteschlange …</em></div>`;
    detail.prepend(block);

    _queue.push(rel);
    _processQueue();
  }

  async function _processQueue() {
    if (_processing) return;
    _processing = true;
    while (_queue.length) {
      const rel = _queue.shift();
      const block = document.getElementById('dir-an-' + _slug(rel));
      if (!block) continue;
      const body = block.querySelector('.dir-analysis-body');
      body.innerHTML = '<em>Analysiert …</em>';
      try {
        const resp = await fetch('/api/dir/analyze-file', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ path: _path(), file_rel: rel, llm_ner: _llmNer(), model: _model() }),
        });
        if (!resp.ok) {
          let msg = resp.statusText;
          try { msg = (await resp.json()).detail || msg; } catch (_) {}
          throw new Error(msg);
        }
        const data = await resp.json();
        _analyses[rel] = data.analysis || '';
        _render(body, data.analysis || '_(keine Analyse)_');
      } catch (e) {
        body.innerHTML = `<span style="color:var(--danger,#e66)">Fehler: ${e.message}</span> `
          + `<button class="dir-retry">↻ Erneut</button>`;
        body.querySelector('.dir-retry')?.addEventListener('click', () => {
          block.remove();
          _analyzeFile(rel);
        });
      }
    }
    _processing = false;
  }

  function _slug(s) { return s.replace(/[^a-zA-Z0-9]+/g, '_'); }

  function _buildIndex() {
    if (!_scan) return '';
    let md = (_scan.summary || '') + '\n\n';
    md += `**Struktur:** ${_scan.n_dirs} Ordner, ${_scan.n_files} Dateien.\n\n`;
    if ((_scan.interesting || []).length) {
      md += '## Interessante Dateien\n\n';
      for (const i of _scan.interesting) md += `- **${i.file}** — ${i.reason}\n`;
      md += '\n';
    }
    const keys = Object.keys(_analyses);
    if (keys.length) {
      md += '## Detailanalysen\n\n';
      for (const k of keys) md += `### ${k}\n\n${_analyses[k]}\n\n`;
    }
    return md.trim();
  }

  async function _finalize(toRag) {
    if (!_scan) { showToast('Erst scannen'); return; }
    const content = _buildIndex();
    if (!content) { showToast('Kein Inhalt zum Speichern'); return; }
    try {
      const resp = await fetch('/api/dir/finalize', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ path: _path(), content, to_rag: !!toRag }),
      });
      if (!resp.ok) { throw new Error((await resp.json()).detail || resp.statusText); }
      const data = await resp.json();
      if (toRag) {
        showToast('✓ Index gespeichert + Wissensdatenbank angelegt');
        if (typeof RAG !== 'undefined' && RAG.loadCollections) RAG.loadCollections();
      } else {
        showToast('✓ Index gespeichert: ' + data.path);
      }
    } catch (e) {
      showToast('Fehler: ' + e.message);
    }
  }

  function init() {
    const last = localStorage.getItem(LS_PATH);
    if (last && _el('dir-path')) _el('dir-path').value = last;
    _el('btn-dir-scan')?.addEventListener('click', _scanDir);
    _el('dir-path')?.addEventListener('keydown', e => { if (e.key === 'Enter') _scanDir(); });
    _el('btn-dir-save')?.addEventListener('click', () => _finalize(false));
    _el('btn-dir-save-rag')?.addEventListener('click', () => _finalize(true));
  }

  return { init };
})();
