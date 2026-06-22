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
  let _partnerMode = false;  // Partner-Auswertung aktiv (Spalten-Kaskade + Token-Label)

  const STORAGE_KEY = 'ai_framework_thomas_matrix_v1';

  function _saveState() {
    try {
      // Laufende Zellen vor dem Speichern als 'empty' markieren
      const saveCells = _cells.map(row =>
        row.map(c => c.status === 'running' ? { status: 'empty', text: '' } : c)
      );
      localStorage.setItem(STORAGE_KEY, JSON.stringify({
        rows: _rows, cols: _cols, cells: saveCells, partnerMode: _partnerMode, savedAt: Date.now(),
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
        _partnerMode = !!state.partnerMode;
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
      const col = _cols[c];
      const active = col.active !== false;
      const th = document.createElement('th');
      const labelHtml = col.label ? `<div class="matrix-col-label">${escHtml(col.label)}</div>` : '';
      th.innerHTML = `
        ${labelHtml}
        <div class="matrix-header-cell-wrap">
          <textarea class="matrix-cell-input" rows="2" placeholder="Suchprompt…" data-col="${c}">${escHtml(col.prompt)}</textarea>
          <button class="btn-del-matrix-col" data-col="${c}" title="Spalte löschen">✕</button>
        </div>
        <select class="matrix-col-agent" data-col="${c}" title="Agent für diese Spalte (nur Favoriten)">${_agentOptionsHtml(col.agent || '')}</select>
        <div class="matrix-col-ctrls">
          <label title="Diese Spalte bei „Alle ausführen“ berücksichtigen"><input type="checkbox" class="matrix-col-active" data-col="${c}"${active ? ' checked' : ''}> ausführen</label>
          <label title="Ergebnisse der vorherigen Spalten dieser Zeile als Kontext mitgeben"><input type="checkbox" class="matrix-col-ctx" data-col="${c}"${col.ctx ? ' checked' : ''}> Kontext</label>
          <button class="export-btn matrix-run-col" data-col="${c}" title="Nur diese Spalte für alle Zeilen ausführen">▶ Spalte</button>
        </div>`;
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
    headerRow.querySelectorAll('.matrix-col-active').forEach(cb => {
      cb.addEventListener('change', () => { _cols[+cb.dataset.col].active = cb.checked; _saveState(); });
    });
    headerRow.querySelectorAll('.matrix-col-ctx').forEach(cb => {
      cb.addEventListener('change', () => { _cols[+cb.dataset.col].ctx = cb.checked; _saveState(); });
    });
    headerRow.querySelectorAll('.matrix-run-col').forEach(btn => {
      btn.addEventListener('click', () => _runColumn(+btn.dataset.col));
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
    let userMsg = `Recherchiere folgendes: "${query}"\n\nGib eine kompakte, strukturierte Antwort, die direkt zum Thema "${topic}" und zur Frage "${prompt}" passt.`;

    // Spalten-Kaskade: Wenn „Kontext" aktiv ist, die bereits fertigen Ergebnisse der
    // vorherigen Spalten dieser Zeile mitgeben (z. B. damit die Mail-Spalte die zuvor
    // gefundenen Kontaktdaten kennt).
    if (_cols[c]?.ctx) {
      const prior = [];
      for (let pc = 0; pc < c; pc++) {
        const pcell = _cells[r]?.[pc];
        if (pcell?.status === 'done' && pcell.text?.trim()) {
          const lbl = _cols[pc].label || _cols[pc].prompt || `Spalte ${pc + 1}`;
          prior.push(`### ${lbl}\n${pcell.text.trim()}`);
        }
      }
      if (prior.length) {
        userMsg += `\n\nBereits ermittelte Informationen zu "${topic}" (als verlässliche Grundlage nutzen, nicht widersprechen):\n\n${prior.join('\n\n')}`;
      }
    }

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
            if (ev.type === 'done') {
              if (ev.tokens && typeof TokenMeter !== 'undefined')
                TokenMeter.add(ev.tokens, _partnerMode ? 'Partner-Auswertung' : 'Matrix-Recherche');
              break;
            }
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
        if (_cols[c].active === false) continue;   // deaktivierte Spalte überspringen
        await _runCell(r, c);
        count++;
      }
    }
    _running = false;
    document.getElementById('btn-matrix-run-all').disabled = false;
    showToast(`${count} Zellen ausgeführt`);
  }

  /* ── Nur eine Spalte (für alle Zeilen) ausführen – „stufenweise" ─────── */
  async function _runColumn(c) {
    if (_running) { showToast('Läuft bereits…'); return; }
    const col = _cols[c];
    if (!col || !col.prompt?.trim()) { showToast('Spalte hat keinen Prompt'); return; }
    _running = true;
    document.getElementById('btn-matrix-run-all').disabled = true;
    let count = 0;
    for (let r = 0; r < _rows.length; r++) {
      if (!_rows[r].topic?.trim()) continue;
      await _runCell(r, c);
      count++;
    }
    _running = false;
    document.getElementById('btn-matrix-run-all').disabled = false;
    showToast(`Spalte „${col.label || col.prompt}“ – ${count} Zellen ausgeführt`);
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

  /* ════════════════════════════════════════════════════════════════════
     Partner-Auswertung: Firmenliste → Recherche-Stufen → Kaltakquise-Mail.
     Baut auf der Matrix-Engine auf (Zeilen = Firmen, Spalten = Stufen).
     ════════════════════════════════════════════════════════════════════ */

  // Die nötigen Agenten. Stabile IDs → idempotent (kein Duplikat beim erneuten
  // Einrichten). „Partner-Rechercheur" und „Akquise-Texter" sind bewusst editierbar
  // (im Agenten-Tab anpassbar) – wie vom Nutzer gewünscht.
  const PARTNER_AGENTS = {
    partner_recherche: {
      id: 'partner_recherche', name: 'Partner-Rechercheur', icon: '🔎',
      category: 'Recherche', favorite: true, tools: ['web_search', 'calculate'],
      model: null, rag_collections: [], example_code: '',
      description: 'Prüft per Websuche, ob eine Firma als Partner/Kunde interessant ist, erstellt ein Profil und findet Ansprechpartner.',
      system_prompt: 'Du bist ein B2B-Partner-Rechercheur. Recherchiere mit der Websuche öffentlich verfügbare Informationen zur genannten Firma. '
        + 'Beurteile zuerst knapp, ob die Firma als Geschäftspartner/Kunde interessant ist (Ja/Nein + ein Satz Begründung). '
        + 'Falls interessant: erstelle ein kurzes Profil (Geschäftsfeld, ungefähre Größe, Standort, Website, Relevanz) und nenne mögliche '
        + 'Ansprechpartner (Name, Rolle/Abteilung), soweit öffentlich auffindbar. Antworte strukturiert auf Deutsch, nur belegbare Fakten, '
        + 'keine Erfindungen. Markiere Unsicheres ausdrücklich als „unbestätigt".',
    },
    partner_kontakt: {
      id: 'partner_kontakt', name: 'Kontaktdaten-Rechercheur', icon: '📇',
      category: 'Recherche', favorite: true, tools: ['web_search', 'calculate'],
      model: null, rag_collections: [], example_code: '',
      description: 'Ermittelt geschäftliche Kontaktdaten (Name, Position, Telefon, E-Mail) der Ansprechpartner aus öffentlichen Quellen.',
      system_prompt: 'Du bist ein Rechercheur für geschäftliche Kontaktdaten. Ermittle über die Websuche die geschäftlichen Kontaktdaten der '
        + 'wichtigsten Ansprechpartner der Firma: Name, Position, geschäftliche Telefonnummer und E-Mail-Adresse. Nutze ausschließlich öffentlich '
        + 'verfügbare, berufliche Quellen (Impressum, Firmen-Website, offizielle Unternehmensprofile). Gib pro Person eine kurze Karteizeile aus. '
        + 'Wenn etwas nicht sicher belegbar ist, schreibe „nicht öffentlich auffindbar" statt zu raten. Antworte auf Deutsch.',
    },
    partner_social: {
      id: 'partner_social', name: 'Social-Media-Rechercheur', icon: '🌐',
      category: 'Recherche', favorite: true, tools: ['web_search', 'calculate'],
      model: null, rag_collections: [], example_code: '',
      description: 'Findet öffentliche Unternehmens-/Personenprofile auf einer sozialen Plattform (LinkedIn, X, Instagram, Facebook, GitHub).',
      system_prompt: 'Du bist ein Rechercheur für öffentliche berufliche bzw. Unternehmens-Profile in sozialen Netzwerken. Finde mit der Websuche '
        + '(z. B. per site:-Suche auf der jeweiligen Plattform) das passende öffentliche Profil zur Firma bzw. zu den genannten Ansprechpartnern '
        + 'auf der in der Aufgabe genannten Plattform. Gib die Profil-URL und die wichtigsten öffentlich sichtbaren Infos (Rolle, Schwerpunkte, '
        + 'letzte relevante Aktivität) an. Nur öffentlich zugängliche Informationen, keine Vermutungen. Wenn kein Profil auffindbar ist, sage das klar. '
        + 'Antworte auf Deutsch.',
    },
    partner_mail: {
      id: 'partner_mail', name: 'Akquise-Texter', icon: '✉️',
      category: 'Recherche', favorite: true, tools: ['web_search', 'calculate'],
      model: null, rag_collections: [], example_code: '',
      description: 'Formuliert auf Basis der Recherche eine personalisierte, seriöse Kaltakquise-E-Mail. Vom Nutzer anpassbar.',
      system_prompt: 'Du bist ein erfahrener Vertriebstexter für seriöse B2B-Kaltakquise. Formuliere auf Basis der zuvor recherchierten Firmen- und '
        + 'Personeninfos eine personalisierte Erstkontakt-E-Mail (Cold Outreach). Struktur: Betreffzeile, persönliche Anrede mit Namen (falls bekannt), '
        + 'kurzer Aufhänger mit konkretem Bezug zur Firma, 2–3 prägnante Absätze zum Mehrwert, ein klarer aber unaufdringlicher Call-to-Action, Grußformel. '
        + 'Höflich, kein Spam, DSGVO-bewusst. Nutze die gefundenen Kontaktdaten in Anrede und Signatur. Antworte auf Deutsch. '
        + 'HINWEIS: Diesen Agenten im Agenten-Tab an die eigene Firma/Tonalität anpassen.',
    },
  };

  // Vordefinierte Spalten der Partner-Auswertung. Spätere Stufen sind anfangs
  // deaktiviert (active:false) und nutzen den Kontext der vorherigen Spalten,
  // damit man sich Stufe für Stufe „durchhangeln" kann.
  function _PARTNER_COLUMNS() {
    const social = (plat, host) => ({
      label: plat, agent: 'partner_social', active: false, ctx: true,
      prompt: `Finde das öffentliche ${plat}-Profil (site:${host}) der Firma bzw. der Ansprechpartner und gib URL + wichtigste öffentliche Infos an.`,
    });
    return [
      {
        label: 'Interesse & Profil', agent: 'partner_recherche', active: true, ctx: false,
        prompt: 'Ist diese Firma ein interessanter Partner/Kunde? Kurzes Urteil (Ja/Nein + Begründung), dann – falls interessant – Detailprofil (Geschäftsfeld, Größe, Standort, Website) und mögliche Ansprechpartner.',
      },
      {
        label: 'Kontaktdaten', agent: 'partner_kontakt', active: false, ctx: true,
        prompt: 'Ermittle die geschäftlichen Kontaktdaten der wichtigsten Ansprechpartner: Name, Position, Telefon, E-Mail (nur öffentlich belegbar).',
      },
      social('LinkedIn', 'linkedin.com'),
      social('X', 'x.com OR twitter.com'),
      social('Instagram', 'instagram.com'),
      social('Facebook', 'facebook.com'),
      social('GitHub', 'github.com'),
      {
        label: 'Kaltakquise-Mail', agent: 'partner_mail', active: false, ctx: true,
        prompt: 'Formuliere eine personalisierte Kaltakquise-E-Mail an den Ansprechpartner mit den gefundenen Kontaktdaten und Infos (Betreff, Anrede, 2–3 Absätze, Call-to-Action, Grußformel).',
      },
    ];
  }

  // Fehlende Partner-Agenten anlegen (vorhandene NICHT überschreiben, damit
  // Nutzer-Anpassungen erhalten bleiben). Gibt die Zahl neu angelegter Agenten zurück.
  async function _ensurePartnerAgents() {
    let existing = [];
    try { existing = await (await fetch('/api/agents')).json(); } catch (_) {}
    const have = new Set((existing || []).map(a => a.id));
    let created = 0;
    for (const def of Object.values(PARTNER_AGENTS)) {
      if (have.has(def.id)) continue;
      try {
        const r = await fetch('/api/agents', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(def),
        });
        if (r.ok) created++;
      } catch (_) {}
    }
    await _loadAgents();
    return created;
  }

  // Schlankes Textfenster (z. B. für lange Firmenlisten) – ohne zusätzliches HTML.
  function _showTextModal(title, placeholder, onOk) {
    const ov = document.createElement('div');
    ov.style.cssText = 'position:fixed;inset:0;z-index:3000;background:rgba(0,0,0,.55);display:flex;align-items:center;justify-content:center';
    const box = document.createElement('div');
    box.style.cssText = 'background:var(--bg-input);border:1px solid var(--border);border-radius:10px;padding:16px;width:min(620px,92vw);box-shadow:0 12px 44px rgba(0,0,0,.55)';
    box.innerHTML = `<h3 style="margin:0 0 8px;font-size:15px">${escHtml(title)}</h3>
      <textarea class="_pl_ta" style="width:100%;height:260px;resize:vertical;font-size:13px;box-sizing:border-box" placeholder="${escHtml(placeholder)}"></textarea>
      <div style="display:flex;gap:8px;justify-content:flex-end;margin-top:10px">
        <button class="export-btn _pl_cancel">Abbrechen</button>
        <button class="export-btn _pl_ok">Übernehmen</button></div>`;
    ov.appendChild(box); document.body.appendChild(ov);
    const close = () => ov.remove();
    box.querySelector('._pl_cancel').addEventListener('click', close);
    ov.addEventListener('click', e => { if (e.target === ov) close(); });
    box.querySelector('._pl_ok').addEventListener('click', () => { const v = box.querySelector('._pl_ta').value; close(); onOk(v); });
    box.querySelector('._pl_ta').focus();
  }

  // Firmenliste einlesen: jede nicht-leere Zeile wird zu einer Tabellenzeile (Firma).
  function _importCompanyList() {
    _showTextModal(
      'Firmenliste einlesen — eine Firma pro Zeile (beliebig lang)',
      'Muster GmbH, Musterstraße 1, 12345 Musterstadt\nBeispiel AG\n…',
      (text) => {
        const lines = (text || '').split(/\r?\n/).map(l => l.trim()).filter(Boolean);
        if (!lines.length) { showToast('Keine Firmen erkannt'); return; }
        if (_rows.some(r => r.topic?.trim()) &&
            !confirm(`${lines.length} Firmen einlesen und bestehende Zeilen ersetzen?`)) return;
        _rows = lines.map(l => ({ topic: l }));
        _initCells();
        _saveState(); _render();
        showToast(`✓ ${lines.length} Firmen eingelesen`);
      });
  }

  // Partner-Vorlage einrichten: Agenten sicherstellen + vordefinierte Spalten setzen.
  async function _applyPartnerTemplate() {
    if (!confirm('Partner-Auswertung einrichten?\n\nDie aktuellen Spalten werden durch die Partner-Stufen ersetzt '
      + '(Firmen-Zeilen bleiben erhalten). Fehlende Agenten werden angelegt; vorhandene Anpassungen bleiben erhalten.')) return;
    showToast('⏳ Partner-Agenten werden vorbereitet…');
    const created = await _ensurePartnerAgents();
    _cols = _PARTNER_COLUMNS();
    _initCells();
    _partnerMode = true;
    _saveState(); _render();
    showToast(`✓ Partner-Auswertung eingerichtet${created ? ` · ${created} Agent(en) angelegt` : ''}. `
      + 'Firmenliste einlesen, dann Spalte für Spalte ausführen (▶ Spalte).');
  }

  /* ── Reset ───────────────────────────────────────────────────────── */
  function _clear() {
    if (!confirm('Tabelle leeren?')) return;
    _rows  = [{ topic: '' }, { topic: '' }, { topic: '' }];
    _cols  = [{ prompt: '', agent: '' }, { prompt: '', agent: '' }];
    _partnerMode = false;
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
    document.getElementById('btn-matrix-partner')?.addEventListener('click', _applyPartnerTemplate);
    document.getElementById('btn-matrix-companies')?.addEventListener('click', _importCompanyList);
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
