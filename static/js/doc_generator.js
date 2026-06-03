/* AI_Framework_Thomas — Dokumentengenerator
   Erzeugt Dokumente mit einem im Agenten-Tab definierten Dokument-Agenten,
   optional gestützt auf Wissensdatenbanken (RAG). Ergebnis als DOCX exportierbar
   oder zurück in eine Wissensdatenbank übernehmbar. */

const DocGen = (() => {
  let _docText = '';
  let _docTitle = 'Dokument';
  let _busy = false;
  let _attached = [];                       // hochgeladene externe Dokumente: {id, filename}
  let _dossier = { id: '', name: '', text: '' };  // gewähltes Dossier

  async function _loadDossiers() {
    const sel = document.getElementById('docgen-dossier');
    if (!sel) return;
    const prev = sel.value;
    let list = [];
    try { list = await (await fetch('/api/dossiers')).json(); } catch (_) {}
    sel.innerHTML = '<option value="">— kein Dossier —</option>';
    for (const d of list) {
      const o = document.createElement('option');
      o.value = d.id;
      o.textContent = d.plan ? `${d.name} · ${d.plan}` : d.name;
      sel.appendChild(o);
    }
    if (prev) sel.value = prev;
  }

  async function _onDossierChange() {
    const id = document.getElementById('docgen-dossier').value;
    if (!id) { _dossier = { id: '', name: '', text: '' }; return; }
    try {
      const d = await (await fetch('/api/dossiers/load?id=' + encodeURIComponent(id))).json();
      _dossier = { id, name: d.name || 'Dossier', text: d.content || '' };
      showToast(`✓ Dossier „${_dossier.name}" als Quellmaterial geladen`);
    } catch (e) { showToast('Dossier konnte nicht geladen werden'); }
  }

  function _renderFiles() {
    const wrap = document.getElementById('docgen-files');
    if (!wrap) return;
    wrap.innerHTML = '';
    _attached.forEach((f, i) => {
      const chip = document.createElement('span');
      chip.className = 'planner-muted';
      chip.style.cssText = 'font-size:12px;background:var(--bg-input);border:1px solid var(--border);border-radius:6px;padding:2px 8px;display:inline-flex;gap:6px;align-items:center';
      chip.innerHTML = `📄 ${escHtml(f.filename)} <a href="#" data-i="${i}" style="text-decoration:none">✕</a>`;
      chip.querySelector('a').addEventListener('click', (e) => {
        e.preventDefault(); _attached.splice(i, 1); _renderFiles();
      });
      wrap.appendChild(chip);
    });
  }

  async function _onFilesPicked(ev) {
    const files = Array.from(ev.target.files || []);
    for (const file of files) {
      const fd = new FormData();
      fd.append('file', file);
      try {
        const r = await (await fetch('/api/upload', { method: 'POST', body: fd })).json();
        if (r.id) _attached.push({ id: r.id, filename: r.filename || file.name });
      } catch (_) { showToast('Upload fehlgeschlagen: ' + file.name); }
    }
    ev.target.value = '';   // gleiche Datei erneut wählbar
    _renderFiles();
  }

  async function _loadAgents() {
    const sel = document.getElementById('docgen-agent');
    if (!sel) return;
    const prev = sel.value;
    let agents = [];
    try { agents = await (await fetch('/api/agents')).json(); } catch (_) {}
    // Nur als Favorit markierte Agenten anbieten; Dokument-Agenten („Dokumentation") zuerst
    agents = agents.filter(a => a.favorite);
    agents.sort((a, b) => (b.category === 'Dokumentation') - (a.category === 'Dokumentation'));
    sel.innerHTML = '<option value="">(kein Agent — generisch)</option>';
    for (const a of agents) {
      const o = document.createElement('option');
      o.value = a.id;
      o.textContent = `${a.icon || '🤖'} ${a.name}${a.category === 'Dokumentation' ? ' · Dokument' : ''}`;
      sel.appendChild(o);
    }
    if (prev) sel.value = prev;
  }

  async function _loadRag() {
    const sel = document.getElementById('docgen-rag');
    if (!sel) return;
    const prev = new Set(Array.from(sel.selectedOptions).map(o => o.value));
    let colls = [];
    try { colls = await (await fetch('/api/rag/collections')).json(); } catch (_) {}
    sel.innerHTML = '';
    for (const c of colls) {
      const o = document.createElement('option');
      o.value = c.id; o.textContent = `${c.name} (${c.n_chunks})`;
      if (prev.has(c.id)) o.selected = true;
      sel.appendChild(o);
    }
  }

  function refresh() { _loadAgents(); _loadRag(); _loadDossiers(); }

  // Markdown → HTML mit derselben Aufbereitung wie im Chat (Formeln/Links/Code).
  function _renderDoc(el, text) {
    if (typeof Chat !== 'undefined' && Chat.renderMarkdown) {
      Chat.renderMarkdown(el, text);
    } else if (typeof marked !== 'undefined') {
      if (window._ensureKatexMarked) window._ensureKatexMarked();
      el.innerHTML = marked.parse(text, { gfm: true, breaks: true });
      el.querySelectorAll('a[href]').forEach(a => { a.target = '_blank'; a.rel = 'noopener noreferrer'; });
    } else {
      el.textContent = text;
    }
  }

  // Aktionsknöpfe (DOCX / Präsentation / Wissensdatenbank) ein- bzw. ausblenden
  function _showActions(show) {
    const disp = show ? '' : 'none';
    ['btn-docgen-export', 'btn-docgen-pdf', 'btn-docgen-present', 'btn-docgen-rag'].forEach(id => {
      const el = document.getElementById(id);
      if (el) el.style.display = disp;
    });
  }

  // Platzhalter im rechten Bereich aus-/einblenden
  function _hidePlaceholder(hide = true) {
    const ph = document.getElementById('docgen-placeholder');
    if (ph) ph.style.display = hide ? 'none' : '';
  }

  // Erzeugtes Dokument als Präsentation in den Canvas/Präsentations-Bereich schieben
  async function _present() {
    if (!_docText.trim()) return;
    const model = document.getElementById('model-select')?.value;
    showToast('🖥️ Präsentation wird erstellt…');
    try {
      const r = await fetch('/api/presentation/from-text', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text: _docText, model }),
      });
      if (!r.ok) throw new Error((await r.json()).detail || r.status);
      const data = await r.json();
      if (typeof CanvasRenderer !== 'undefined') CanvasRenderer.render(data);
      if (typeof switchTab === 'function') switchTab('canvas');
      else document.querySelector('.tab-btn[data-tab="canvas"]')?.click();
      showToast('✓ Präsentation im Canvas erstellt — bearbeitbar');
    } catch (e) {
      showToast('Präsentation fehlgeschlagen: ' + e.message);
    }
  }

  /* Ziehbarer Trenner: Steuerung ↔ Dokument (Breite in localStorage) */
  const _SPLIT_KEY = 'docgen_left_width';
  function _initSplitter() {
    const splitter = document.getElementById('docgen-splitter');
    const left = document.getElementById('docgen-left');
    const body = document.getElementById('docgen-body');
    if (!splitter || !left || !body) return;
    const saved = parseInt(localStorage.getItem(_SPLIT_KEY) || '', 10);
    if (saved > 0) left.style.width = saved + 'px';

    const _apply = (clientX) => {
      const rect = body.getBoundingClientRect();
      let w = clientX - rect.left;
      w = Math.max(320, Math.min(w, rect.width - 280));
      left.style.width = w + 'px';
    };
    const _onMove = (e) => _apply(e.clientX);
    const _onUp = () => {
      splitter.classList.remove('dragging');
      document.body.style.userSelect = '';
      document.removeEventListener('mousemove', _onMove);
      document.removeEventListener('mouseup', _onUp);
      localStorage.setItem(_SPLIT_KEY, String(parseInt(left.style.width, 10) || 0));
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
      localStorage.removeItem(_SPLIT_KEY);
    });
  }

  async function _run(thenPresent = false) {
    if (_busy) return;
    const brief = document.getElementById('docgen-brief').value.trim();
    if (!brief) { showToast('Bitte beschreibe das gewünschte Dokument'); return; }
    const agentId = document.getElementById('docgen-agent').value || undefined;
    const model = document.getElementById('model-select')?.value;
    const ragSel = document.getElementById('docgen-rag');
    const rag = Array.from(ragSel.selectedOptions).map(o => o.value);
    const science = document.getElementById('docgen-science').checked;

    // Quellmaterial zusammenstellen: eingefügter Text + gewähltes Dossier als
    // Kontext an die Aufgabe hängen; externe Dateien werden serverseitig extrahiert.
    let content = brief;
    const ctx = [];
    const pasteText = (document.getElementById('docgen-paste')?.value || '').trim();
    if (pasteText) ctx.push('[Eingefügter Text]\n' + pasteText);
    if (_dossier.text) ctx.push(`[Dossier: ${_dossier.name}]\n${_dossier.text}`);
    if (ctx.length) {
      content = `${brief}\n\n--- Quellmaterial (als Grundlage verwenden) ---\n\n${ctx.join('\n\n')}`;
    }
    const fileIds = _attached.map(f => f.id);

    _docTitle = brief.split('\n')[0].slice(0, 60) || 'Dokument';
    _busy = true;
    const status = document.getElementById('docgen-status');
    const out = document.getElementById('docgen-output');
    document.getElementById('btn-docgen-run').disabled = true;
    document.getElementById('btn-docgen-run-present').disabled = true;
    _showActions(false);
    _hidePlaceholder();
    status.textContent = thenPresent ? '⏳ Inhalt für die Präsentation wird erzeugt…' : '⏳ Dokument wird erzeugt…';
    out.innerHTML = '';
    _docText = '';

    try {
      const resp = await fetch('/api/chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          messages: [{ role: 'user', content, files: fileIds.length ? fileIds : undefined }],
          model,
          agent_id: agentId,
          use_tools: false,
          rag_collections: rag,
          science,
        }),
      });
      if (!resp.ok) throw new Error('HTTP ' + resp.status);
      const reader = resp.body.getReader();
      const dec = new TextDecoder();
      let buf = '';
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
            if (ev.type === 'text') { _docText += ev.content; out.textContent = _docText; }
            else if (ev.type === 'error') { showToast('Fehler: ' + ev.message); }
          } catch (_) {}
        }
      }
      // Final als Markdown rendern — exakt dieselbe Pipeline wie im Chat
      // (KaTeX-Formeln, Normen-/Gesetzes-Links, Code-Highlighting).
      _renderDoc(out, _docText);
      status.textContent = _docText.trim() ? '✓ Fertig' : 'Kein Inhalt erzeugt';
      if (_docText.trim()) _showActions(true); else _hidePlaceholder(false);
    } catch (e) {
      status.textContent = '';
      showToast('Fehler: ' + e.message);
    } finally {
      _busy = false;
      document.getElementById('btn-docgen-run').disabled = false;
      document.getElementById('btn-docgen-run-present').disabled = false;
    }
    // Direkt im Anschluss als Präsentation in den Canvas übernehmen
    if (thenPresent && _docText.trim()) await _present();
  }

  // Inhalt erzeugen UND direkt als Präsentation im Canvas (Querformat) öffnen
  function _runPresentation() { return _run(true); }

  // Bestehenden, eingefügten Text direkt als Dokument übernehmen (ohne KI).
  function _usePasted() {
    const ta = document.getElementById('docgen-paste');
    const text = (ta?.value || '').trim();
    if (!text) { showToast('Bitte zuerst Text einfügen'); return; }
    _docText = text;
    _docTitle = text.split('\n')[0].replace(/^#+\s*/, '').slice(0, 60) || 'Dokument';
    _renderDoc(document.getElementById('docgen-output'), _docText);
    document.getElementById('docgen-status').textContent = '✓ Text übernommen';
    _hidePlaceholder();
    _showActions(true);
    showToast('✓ Text übernommen — exportierbar / als Präsentation / in Wissensdatenbank');
  }

  async function _exportDocx() {
    if (!_docText) return;
    showToast('Erstelle Dokument…');
    try {
      const profile = await (await fetch('/api/profile')).json();
      const resp = await fetch('/api/export/docx', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ title: _docTitle, content: _docText, _include_header_image: true, _profile: profile }),
      });
      if (!resp.ok) throw new Error('HTTP ' + resp.status);
      const blob = await resp.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url; a.download = _docTitle.replace(/\s+/g, '_').slice(0, 40) + '.docx'; a.click();
      URL.revokeObjectURL(url);
      showToast('✓ Exportiert');
    } catch (e) { showToast('Export fehlgeschlagen: ' + e.message); }
  }

  async function _exportPdf() {
    if (!_docText) return;
    showToast('Erstelle PDF…');
    try {
      const profile = await (await fetch('/api/profile')).json();
      const resp = await fetch('/api/export/pdf', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ title: _docTitle, content: _docText, _profile: profile }),
      });
      if (!resp.ok) throw new Error((await resp.json().catch(() => ({}))).detail || ('HTTP ' + resp.status));
      const blob = await resp.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url; a.download = _docTitle.replace(/\s+/g, '_').slice(0, 40) + '.pdf'; a.click();
      URL.revokeObjectURL(url);
      showToast('✓ PDF exportiert');
    } catch (e) { showToast('PDF-Export fehlgeschlagen: ' + e.message); }
  }

  // Aus dem Chat übernommenen (komprimierten) Verlauf als Quellmaterial laden.
  function loadFromChat(title, text) {
    const ta = document.getElementById('docgen-paste');
    if (ta) {
      ta.value = (text || '').trim();
      const det = ta.closest('details');
      if (det) det.open = true;
    }
    const brief = document.getElementById('docgen-brief');
    if (brief && !brief.value.trim()) {
      brief.value = `Bereite auf Basis des übernommenen Chats „${title || 'Chat'}" vor: erstelle eine strukturierte Besprechungsvorlage (Ziel, Agenda, Teilnehmer/Rollen, Entscheidungen, Aufgaben mit Verantwortlichen).`;
    }
    showToast('✓ Chat als Quellmaterial geladen — Auftrag prüfen und „Dokument erzeugen"');
  }

  function init() {
    refresh();
    _initSplitter();
    document.getElementById('btn-docgen-run')?.addEventListener('click', () => _run(false));
    document.getElementById('btn-docgen-run-present')?.addEventListener('click', _runPresentation);
    document.getElementById('btn-docgen-paste')?.addEventListener('click', _usePasted);
    document.getElementById('btn-docgen-present')?.addEventListener('click', _present);
    document.getElementById('btn-docgen-pdf')?.addEventListener('click', _exportPdf);
    document.getElementById('docgen-file')?.addEventListener('change', _onFilesPicked);
    document.getElementById('docgen-dossier')?.addEventListener('change', _onDossierChange);
    document.getElementById('btn-docgen-dossier-refresh')?.addEventListener('click', _loadDossiers);
    document.getElementById('btn-docgen-export')?.addEventListener('click', _exportDocx);
    document.getElementById('btn-docgen-rag')?.addEventListener('click', () => {
      if (typeof RAG !== 'undefined') RAG.ingestText(_docTitle, _docText);
    });
    // Beim Öffnen des Tabs Agenten + Wissensdatenbanken auffrischen
    document.querySelector('.tab-btn[data-tab="docgen"]')?.addEventListener('click', refresh);
  }

  return { init, refresh, loadFromChat };
})();
