/* AI_Framework_Thomas — Tabellen-Recherche (Matrix) */

const MatrixResearch = (() => {

  /* ── Zustand ──────────────────────────────────────────────────────── */
  // rows: Array von { topic: string }
  // cols: Array von { prompt: string }
  // cells[r][c]: { status: 'empty'|'running'|'done', text: string }
  let _rows  = [{ topic: '' }, { topic: '' }, { topic: '' }];
  let _cols  = [{ prompt: '', agent: '' }, { prompt: '', agent: '' }];
  let _cells = [];  // _cells[r][c]
  let _running = false;
  let _favAgents = [];  // nur als Favorit markierte Agenten (auswählbar pro Spalte)

  const STORAGE_KEY = 'ai_framework_thomas_matrix_v1';

  function _saveState() {
    try {
      // Laufende Zellen vor dem Speichern als 'empty' markieren
      const saveCells = _cells.map(row =>
        row.map(c => c.status === 'running' ? { status: 'empty', text: '' } : c)
      );
      localStorage.setItem(STORAGE_KEY, JSON.stringify({
        rows: _rows, cols: _cols, cells: saveCells, savedAt: Date.now(),
      }));
      _showSaveIndicator();
    } catch (_) {}
  }

  function _loadState() {
    try {
      const raw = localStorage.getItem(STORAGE_KEY);
      if (!raw) return false;
      const state = JSON.parse(raw);
      if (state.rows?.length && state.cols?.length && state.cells?.length) {
        _rows  = state.rows;
        _cols  = state.cols;
        _cells = state.cells;
        return true;
      }
    } catch (_) {}
    return false;
  }

  let _saveIndicatorTimer = null;
  function _showSaveIndicator() {
    const el = document.getElementById('matrix-autosave-label');
    if (!el) return;
    el.style.display = '';
    clearTimeout(_saveIndicatorTimer);
    _saveIndicatorTimer = setTimeout(() => { el.style.display = 'none'; }, 2000);
  }

  function _initCells() {
    _cells = _rows.map(() => _cols.map(() => ({ status: 'empty', text: '' })));
  }

  /* ── Modell: zentral aus dem Profil (Matrix nutzt science:true) ───── */
  function _getModel() {
    return (typeof Profile !== 'undefined' ? Profile.modelFor('science') : '') || undefined;
  }

  /* ── Recherche-/Prüf-Agenten (pro Spalte wählbar) ─────────────────── */
  // Nur als Favorit markierte Agenten laden (Recherche-/Prüf-Agenten zuerst,
  // damit z. B. der Firmenagent für den Messebesuch schnell auffindbar ist).
  async function _loadAgents() {
    let agents = [];
    try {
      agents = await (await fetch('/api/agents')).json();
    } catch (_) { return; }
    const rank = a => (a.category === 'Recherche' || a.category === 'Prüfung') ? 0 : 1;
    _favAgents = agents
      .filter(a => a.favorite)
      .sort((a, b) => rank(a) - rank(b) || (a.name || '').localeCompare(b.name || ''));
    _render();
  }

  // Options-HTML für ein Spalten-Agenten-Dropdown (markiert den gewählten Agenten)
  function _agentOptionsHtml(selectedId) {
    let html = `<option value="">Kein Agent</option>`;
    for (const a of _favAgents) {
      const sel = a.id === selectedId ? ' selected' : '';
      html += `<option value="${escHtml(a.id)}"${sel}>${escHtml((a.icon || '🤖') + ' ' + a.name)}</option>`;
    }
    return html;
  }

  /* ── Zelleninhalt rendern (Markdown + LaTeX bei fertigen Zellen) ──── */
  function _renderCell(div, cell) {
    if (cell.status === 'running') { div.textContent = '⟳ Suche läuft…'; return; }
    if (cell.status === 'done' && cell.text && typeof marked !== 'undefined') {
      if (window._ensureKatexMarked) window._ensureKatexMarked();
      div.innerHTML = marked.parse(cell.text, { gfm: true, breaks: true });
      div.querySelectorAll('a[href]').forEach(a => { a.target = '_blank'; a.rel = 'noopener noreferrer'; });
    } else {
      div.textContent = cell.text || '';
    }
  }

  /* ── Tabelle rendern ─────────────────────────────────────────────── */
  function _render() {
    const headerRow = document.getElementById('matrix-header-row');
    const tbody     = document.getElementById('matrix-body');
    if (!headerRow || !tbody) return;

    // Header
    headerRow.innerHTML = '';

    // Linke obere Ecke
    const thEmpty = document.createElement('th');
    thEmpty.style.minWidth = '160px';
    thEmpty.innerHTML = '<div style="padding:6px 8px;color:var(--text-muted);font-size:11px">Thema / Prompt →</div>';
    headerRow.appendChild(thEmpty);

    // Spalten-Köpfe
    for (let c = 0; c < _cols.length; c++) {
      const th = document.createElement('th');
      th.innerHTML = `
        <div class="matrix-header-cell-wrap">
          <textarea class="matrix-cell-input" rows="2" placeholder="Suchprompt…" data-col="${c}">${escHtml(_cols[c].prompt)}</textarea>
          <button class="btn-del-matrix-col" data-col="${c}" title="Spalte löschen">✕</button>
        </div>
        <select class="matrix-col-agent" data-col="${c}" title="Agent für diese Spalte (nur Favoriten)">${_agentOptionsHtml(_cols[c].agent || '')}</select>`;
      headerRow.appendChild(th);
    }

    // Body
    tbody.innerHTML = '';
    for (let r = 0; r < _rows.length; r++) {
      const tr = document.createElement('tr');

      // Erste Spalte: Thema
      const tdTopic = document.createElement('td');
      tdTopic.innerHTML = `
        <div style="display:flex;align-items:center;gap:4px;padding:4px 6px">
          <textarea class="matrix-cell-input" rows="2" placeholder="Thema / Information…" data-row="${r}" style="flex:1">${escHtml(_rows[r].topic)}</textarea>
          <button class="btn-del-matrix-col" data-row="${r}" title="Zeile löschen" style="font-size:11px">✕</button>
        </div>`;
      tr.appendChild(tdTopic);

      // Ergebniszellen
      for (let c = 0; c < _cols.length; c++) {
        const cell = _cells[r]?.[c] || { status: 'empty', text: '' };
        const td = document.createElement('td');
        const div = document.createElement('div');
        div.className = `matrix-cell-result ${cell.status}`;
        div.dataset.row = r;
        div.dataset.col = c;
        _renderCell(div, cell);
        div.title = cell.status === 'done' ? 'Klick zum erneuten Ausführen' : 'Klick zum Ausführen';
        div.style.cursor = 'pointer';
        div.addEventListener('click', () => _runCell(r, c));
        td.appendChild(div);
        tr.appendChild(td);
      }
      tbody.appendChild(tr);
    }

    // Events für Header-Prompts
    headerRow.querySelectorAll('textarea[data-col]').forEach(ta => {
      ta.addEventListener('input', e => {
        _cols[+ta.dataset.col].prompt = ta.value;
        _saveState();
      });
    });
    headerRow.querySelectorAll('.matrix-col-agent[data-col]').forEach(sel => {
      sel.addEventListener('change', () => {
        _cols[+sel.dataset.col].agent = sel.value;
        _saveState();
      });
    });
    headerRow.querySelectorAll('.btn-del-matrix-col[data-col]').forEach(btn => {
      btn.addEventListener('click', () => {
        const c = +btn.dataset.col;
        _cols.splice(c, 1);
        _cells.forEach(row => row.splice(c, 1));
        _saveState();
        _render();
      });
    });

    // Events für Zeilen-Themen
    tbody.querySelectorAll('textarea[data-row]').forEach(ta => {
      ta.addEventListener('input', () => {
        _rows[+ta.dataset.row].topic = ta.value;
        _saveState();
      });
    });
    tbody.querySelectorAll('.btn-del-matrix-col[data-row]').forEach(btn => {
      btn.addEventListener('click', () => {
        const r = +btn.dataset.row;
        _rows.splice(r, 1);
        _cells.splice(r, 1);
        _saveState();
        _render();
      });
    });
  }

  /* ── Einzelne Zelle ausführen ────────────────────────────────────── */
  async function _runCell(r, c) {
    const topic  = _rows[r]?.topic?.trim();
    const prompt = _cols[c]?.prompt?.trim();
    if (!topic || !prompt) { showToast('Bitte Thema und Prompt eingeben'); return; }

    _cells[r][c] = { status: 'running', text: '' };
    _updateCellUI(r, c);

    const model = _getModel();
    const agentId = _cols[c]?.agent || '';
    const query = `${topic}: ${prompt}`;
    const userMsg = `Recherchiere folgendes: "${query}"\n\nGib eine kompakte, strukturierte Antwort, die direkt zum Thema "${topic}" und zur Frage "${prompt}" passt.`;

    // Mit gewähltem Agent: dessen System-Prompt (z. B. Bewertung/Plausibilitätsprüfung)
    // steuert die Antwort. Ohne Agent: einfacher Recherche-Assistent.
    const body = { model, use_tools: true, science: true };
    if (agentId) {
      body.agent_id = agentId;
      body.messages = [{ role: 'user', content: userMsg }];
    } else {
      body.messages = [
        {
          role: 'system',
          content: 'Du bist ein Recherche-Assistent. Suche präzise Informationen und gib eine kompakte, strukturierte Antwort auf Deutsch. Maximal 3-4 Sätze oder eine kurze Liste.',
        },
        { role: 'user', content: userMsg },
      ];
    }

    try {
      const resp = await fetch('/api/chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });

      const reader = resp.body.getReader();
      const dec    = new TextDecoder();
      let buf = '', text = '';

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buf += dec.decode(value, { stream: true });
        const lines = buf.split('\n');
        buf = lines.pop();
        for (const line of lines) {
          if (!line.startsWith('data: ')) continue;
          try {
            const ev = JSON.parse(line.slice(6));
            if (ev.type === 'text') { text += ev.content; }
            if (ev.type === 'done') break;
          } catch (_) {}
        }
      }
      _cells[r][c] = { status: 'done', text: text.trim() || '(kein Ergebnis)' };
    } catch (e) {
      _cells[r][c] = { status: 'done', text: `Fehler: ${e.message}` };
    }
    _updateCellUI(r, c);
    _saveState();
  }

  function _updateCellUI(r, c) {
    const cell = _cells[r]?.[c];
    if (!cell) return;
    const div = document.querySelector(`.matrix-cell-result[data-row="${r}"][data-col="${c}"]`);
    if (!div) return;
    div.className = `matrix-cell-result ${cell.status}`;
    _renderCell(div, cell);
  }

  /* ── Alle Zellen ausführen ───────────────────────────────────────── */
  async function _runAll() {
    if (_running) { showToast('Läuft bereits…'); return; }
    _running = true;
    document.getElementById('btn-matrix-run-all').disabled = true;
    let count = 0;
    for (let r = 0; r < _rows.length; r++) {
      for (let c = 0; c < _cols.length; c++) {
        if (!_rows[r].topic?.trim() || !_cols[c].prompt?.trim()) continue;
        await _runCell(r, c);
        count++;
      }
    }
    _running = false;
    document.getElementById('btn-matrix-run-all').disabled = false;
    showToast(`${count} Zellen ausgeführt`);
  }

  /* ── Als XLSX exportieren ────────────────────────────────────────── */
  async function _exportXlsx() {
    const headers = ['Thema', ..._cols.map(c => c.prompt || '(leer)')];
    const rows = _rows.map((row, r) => [
      row.topic,
      ..._cols.map((_, c) => _cells[r]?.[c]?.text || ''),
    ]);
    try {
      const resp = await fetch('/api/export/xlsx', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ title: 'Matrix-Recherche', headers, rows }),
      });
      const blob = await resp.blob();
      const url  = URL.createObjectURL(blob);
      const a    = document.createElement('a');
      a.href = url; a.download = 'matrix_recherche.xlsx'; a.click();
      URL.revokeObjectURL(url);
    } catch (e) {
      showToast('Export fehlgeschlagen');
    }
  }

  /* ── Eine Zelle = ein Markdown-Dokument (thema_prompt.md) ─────────── */
  // Bereinigt Text zu einem Dateinamen-tauglichen Slug.
  function _slug(s) {
    return (s || '').trim().toLowerCase()
      .replace(/[^\p{L}\p{N}]+/gu, '_')
      .replace(/^_+|_+$/g, '')
      .slice(0, 60) || 'leer';
  }

  // Baut für jede befüllte Zelle ein Dokument: Thema + Prompt als Überschrift
  // (Kontext für den Embedder!), dann der Zellinhalt. Name = thema_prompt.
  function _cellDocs() {
    const docs = [];
    const used = {};
    for (let r = 0; r < _rows.length; r++) {
      const topic = _rows[r]?.topic?.trim();
      if (!topic) continue;
      for (let c = 0; c < _cols.length; c++) {
        const prompt = _cols[c]?.prompt?.trim();
        const txt = _cells[r]?.[c]?.text?.trim();
        if (!prompt || !txt) continue;
        const base = `${_slug(topic)}_${_slug(prompt)}`;
        let name = base, i = 2;
        while (used[name]) name = `${base}_${i++}`;
        used[name] = true;
        docs.push({ name: `${name}.md`, title: name, content: `# ${topic}\n## ${prompt}\n\n${txt}\n` });
      }
    }
    return docs;
  }

  // Alle Zellen einzeln als .md-Dateien in einem ZIP herunterladen.
  async function _exportMdZip() {
    const docs = _cellDocs();
    if (!docs.length) { showToast('Keine befüllten Zellen'); return; }
    showToast('⏳ ZIP wird erstellt…');
    try {
      const resp = await fetch('/api/matrix/export-md-zip', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ zipname: 'matrix_markdown', files: docs.map(d => ({ name: d.name, content: d.content })) }),
      });
      if (!resp.ok) throw new Error(resp.status);
      const blob = await resp.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url; a.download = 'matrix_markdown.zip'; a.click();
      URL.revokeObjectURL(url);
      showToast(`✓ ${docs.length} Markdown-Dateien als ZIP`);
    } catch (e) {
      showToast('Export fehlgeschlagen: ' + e.message);
    }
  }

  // Jede Zelle als eigenes Dokument direkt in eine Wissensdatenbank übernehmen.
  async function _ingestPerCell() {
    const docs = _cellDocs();
    if (!docs.length) { showToast('Keine befüllten Zellen'); return; }
    if (typeof RAG === 'undefined' || !RAG.pickCollection) { showToast('RAG nicht verfügbar'); return; }
    const cid = await RAG.pickCollection(`${docs.length} Zellen einzeln übernehmen (thema_prompt.md)`);
    if (!cid) return;
    let ok = 0, fail = 0;
    for (const d of docs) {
      try {
        const r = await fetch(`/api/rag/collections/${cid}/from-text`, {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ title: d.title, text: d.content }),
        });
        if (!r.ok) throw new Error();
        ok++;
      } catch (_) { fail++; }
      showToast(`⏳ ${ok + fail}/${docs.length} eingebettet…`);
    }
    showToast(`✓ ${ok} Dokumente übernommen${fail ? `, ${fail} fehlgeschlagen` : ''}`);
    if (RAG.loadCollections) RAG.loadCollections();
  }

  /* ── In Wissensdatenbank übernehmen (alles in EIN Dokument) ───────── */
  function _exportRag() {
    const parts = [`# Matrix-Recherche`];
    for (let r = 0; r < _rows.length; r++) {
      if (!_rows[r].topic?.trim()) continue;
      parts.push(`\n## ${_rows[r].topic}`);
      for (let c = 0; c < _cols.length; c++) {
        const ans = _cells[r]?.[c]?.text;
        if (_cols[c].prompt?.trim() && ans) parts.push(`\n### ${_cols[c].prompt}\n${ans}`);
      }
    }
    const text = parts.join('\n');
    if (typeof RAG !== 'undefined') RAG.ingestText('Matrix-Recherche', text);
  }

  /* ── CSV-Export ─────────────────────────────────────────────────── */
  function _exportCsv() {
    const sep = ';';
    const quote = v => '"' + String(v).replace(/"/g, '""') + '"';
    const lines = [];
    // Kopfzeile: leer + Prompts
    lines.push([quote('Thema'), ..._cols.map(c => quote(c.prompt || ''))].join(sep));
    // Datenzeilen
    for (let r = 0; r < _rows.length; r++) {
      const row = [quote(_rows[r].topic || '')];
      for (let c = 0; c < _cols.length; c++) {
        row.push(quote(_cells[r]?.[c]?.text || ''));
      }
      lines.push(row.join(sep));
    }
    const blob = new Blob(['﻿' + lines.join('\r\n')], { type: 'text/csv;charset=utf-8' });
    const url  = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url; a.download = 'matrix_recherche.csv'; a.click();
    URL.revokeObjectURL(url);
  }

  /* ── CSV-Import ──────────────────────────────────────────────────── */
  function _importCsv(file) {
    const reader = new FileReader();
    reader.onload = e => {
      try {
        let text = e.target.result;
        // BOM entfernen
        if (text.charCodeAt(0) === 0xFEFF) text = text.slice(1);
        const lines = text.split(/\r?\n/).filter(l => l.trim());
        if (lines.length < 2) { showToast('CSV zu kurz'); return; }

        // Trennzeichen erkennen (Semikolon oder Komma)
        const sep = lines[0].includes(';') ? ';' : ',';

        const parseLine = line => {
          const result = [];
          let cur = '', inQ = false;
          for (let i = 0; i < line.length; i++) {
            const ch = line[i];
            if (ch === '"') {
              if (inQ && line[i + 1] === '"') { cur += '"'; i++; }
              else inQ = !inQ;
            } else if (ch === sep && !inQ) {
              result.push(cur); cur = '';
            } else cur += ch;
          }
          result.push(cur);
          return result;
        };

        const header = parseLine(lines[0]);
        const newCols = header.slice(1).map(p => ({ prompt: p.trim(), agent: '' }));
        const newRows = [], newCells = [];

        for (let i = 1; i < lines.length; i++) {
          const parts = parseLine(lines[i]);
          newRows.push({ topic: parts[0]?.trim() || '' });
          const cellRow = newCols.map((_, c) => ({
            status: parts[c + 1]?.trim() ? 'done' : 'empty',
            text: parts[c + 1]?.trim() || '',
          }));
          newCells.push(cellRow);
        }

        _cols  = newCols;
        _rows  = newRows;
        _cells = newCells;
        _saveState();
        _render();
        showToast(`CSV importiert: ${newRows.length} Zeilen, ${newCols.length} Spalten`);
      } catch (err) {
        showToast('Fehler beim CSV-Lesen: ' + err.message);
      }
    };
    reader.readAsText(file, 'utf-8');
  }

  /* ── Reset ───────────────────────────────────────────────────────── */
  function _clear() {
    if (!confirm('Tabelle leeren?')) return;
    _rows  = [{ topic: '' }, { topic: '' }, { topic: '' }];
    _cols  = [{ prompt: '', agent: '' }, { prompt: '', agent: '' }];
    _initCells();
    _saveState();
    _render();
  }

  /* ── init ────────────────────────────────────────────────────────── */
  function init() {
    if (!_loadState()) _initCells();

    _loadAgents();

    document.getElementById('btn-matrix-add-row')?.addEventListener('click', () => {
      _rows.push({ topic: '' });
      _cells.push(_cols.map(() => ({ status: 'empty', text: '' })));
      _saveState();
      _render();
    });

    document.getElementById('btn-matrix-add-col')?.addEventListener('click', () => {
      _cols.push({ prompt: '', agent: '' });
      _cells.forEach(row => row.push({ status: 'empty', text: '' }));
      _saveState();
      _render();
    });

    document.getElementById('btn-matrix-run-all')?.addEventListener('click', _runAll);
    document.getElementById('btn-matrix-export')?.addEventListener('click', _exportXlsx);
    document.getElementById('btn-matrix-export-csv')?.addEventListener('click', _exportCsv);
    document.getElementById('btn-matrix-rag')?.addEventListener('click', _exportRag);
    document.getElementById('btn-matrix-rag-cells')?.addEventListener('click', _ingestPerCell);
    document.getElementById('btn-matrix-md-zip')?.addEventListener('click', _exportMdZip);
    document.getElementById('btn-matrix-clear')?.addEventListener('click', _clear);

    const csvInput = document.getElementById('matrix-csv-input');
    document.getElementById('btn-matrix-import-csv')?.addEventListener('click', () => csvInput?.click());
    csvInput?.addEventListener('change', e => {
      if (e.target.files[0]) _importCsv(e.target.files[0]);
      e.target.value = '';
    });

    document.querySelector('[data-tab="matrix"]')?.addEventListener('click', () => {
      _loadAgents();
      _render();
    });

    _render();
  }

  return { init };

})();
