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
    ['btn-docgen-export', 'btn-docgen-pdf', 'btn-docgen-latex', 'btn-docgen-present', 'btn-docgen-rag', 'btn-docgen-jury', 'btn-docgen-edit'].forEach(id => {
      const el = document.getElementById(id);
      if (el) el.style.display = disp;
    });
  }

  // ── WYSIWYG-Bearbeitung des erzeugten Dokuments ─────────────────────────────
  let _editing = false;

  // Editier-Rendering: wie die Vorschau, aber Mermaid bleibt als Codeblock (Quelltext
  // bliebe sonst verloren) und Formeln werden per KaTeX gerendert (Quelltext steckt in
  // der MathML-Annotation → beim Zurückwandeln rekonstruierbar).
  function _renderEditable(el, text) {
    if (typeof marked === 'undefined') { el.textContent = text; return; }
    // Mathe-Extension registrieren: $…$/$$…$$ → KaTeX (mit TeX-Annotation, rekonstruierbar)
    // und vor Markdown geschützt. Fehlt sie, bleibt die Formel als $…$-Text (rundet ebenso).
    if (window._ensureKatexMarked) try { window._ensureKatexMarked(); } catch (_) {}
    el.innerHTML = marked.parse(text, { gfm: true, breaks: true });
    el.querySelectorAll('a[href]').forEach(a => { a.target = '_blank'; a.rel = 'noopener noreferrer'; });
    // Mermaid bewusst NICHT zu SVG rendern → bleibt als Codeblock (Quelltext erhalten)
    el.querySelectorAll('pre code').forEach(b => {
      if (typeof hljs !== 'undefined' && !b.classList.contains('language-mermaid')) {
        try { hljs.highlightElement(b); } catch (_) {}
      }
    });
  }

  // Gerendertes HTML → Markdown (Turndown + GFM-Tabellen + KaTeX-Rückgewinnung).
  function _htmlToMarkdown(el) {
    if (typeof TurndownService === 'undefined') {
      // Fallback ohne Bibliothek: reiner Text (verlustbehaftet)
      return (el.innerText || el.textContent || '').trim();
    }
    const td = new TurndownService({
      headingStyle: 'atx', codeBlockStyle: 'fenced',
      bulletListMarker: '-', emDelimiter: '*', hr: '---',
    });
    if (window.turndownPluginGfm && window.turndownPluginGfm.gfm) td.use(window.turndownPluginGfm.gfm);
    // KaTeX-Formeln aus der TeX-Annotation zurückholen (inline $…$ / display $$…$$)
    td.addRule('katex', {
      filter: (node) => node.classList && node.classList.contains('katex'),
      replacement: (content, node) => {
        const tex = (node.querySelector('annotation[encoding="application/x-tex"]') || {}).textContent || '';
        if (!tex) return content;
        return node.closest('.katex-display') ? `\n\n$$${tex}$$\n\n` : `$${tex}$`;
      },
    });
    // Kopier-Buttons (von der Vorschau) nicht mitnehmen
    td.remove((node) => node.nodeName === 'BUTTON');
    return td.turndown(el.innerHTML).replace(/\n{3,}/g, '\n\n').trim();
  }

  function _setEditUI(editing) {
    _editing = editing;
    const out = document.getElementById('docgen-output');
    if (out) out.classList.toggle('docgen-editing', editing);
    // Export-/Aktionsknöpfe während der Bearbeitung ausblenden, Save/Cancel zeigen
    ['btn-docgen-export', 'btn-docgen-pdf', 'btn-docgen-latex', 'btn-docgen-present',
     'btn-docgen-rag', 'btn-docgen-jury', 'btn-docgen-edit'].forEach(id => {
      const el = document.getElementById(id); if (el) el.style.display = editing ? 'none' : '';
    });
    ['btn-docgen-edit-save', 'btn-docgen-edit-cancel'].forEach(id => {
      const el = document.getElementById(id); if (el) el.style.display = editing ? '' : 'none';
    });
  }

  function _startEdit() {
    if (!_docText.trim()) { showToast('Kein Dokument zum Bearbeiten'); return; }
    const out = document.getElementById('docgen-output');
    if (!out) return;
    _renderEditable(out, _docText);
    out.contentEditable = 'true';
    out.focus();
    _setEditUI(true);
    document.getElementById('docgen-status').textContent =
      '✏️ Bearbeitung — Text direkt ändern, dann „✓ Übernehmen". (Mermaid-Diagramme erscheinen als Quelltext.)';
  }

  function _saveEdit() {
    const out = document.getElementById('docgen-output');
    if (!out) return;
    _docText = _htmlToMarkdown(out);
    out.contentEditable = 'false';
    _setEditUI(false);
    _renderDoc(out, _docText);   // sauber neu rendern (Mermaid wieder als Diagramm)
    document.getElementById('docgen-status').textContent = '✓ Änderungen übernommen';
    if (typeof TurndownService === 'undefined')
      showToast('Hinweis: Markdown-Konverter nicht geladen — Formatierung evtl. vereinfacht');
  }

  function _cancelEdit() {
    const out = document.getElementById('docgen-output');
    if (!out) return;
    out.contentEditable = 'false';
    _setEditUI(false);
    _renderDoc(out, _docText);   // Originaltext wiederherstellen
    document.getElementById('docgen-status').textContent = '';
  }

  // Platzhalter im rechten Bereich aus-/einblenden
  function _hidePlaceholder(hide = true) {
    const ph = document.getElementById('docgen-placeholder');
    if (ph) ph.style.display = hide ? 'none' : '';
  }

  // Erzeugtes Dokument als Präsentation in den Canvas/Präsentations-Bereich schieben
  async function _present() {
    if (!_docText.trim()) return;
    const model = (typeof Profile !== 'undefined' ? Profile.modelFor('general') : '') || undefined;
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
    let brief = document.getElementById('docgen-brief').value.trim();
    if (!brief) { showToast('Bitte beschreibe das gewünschte Dokument'); return; }
    let agentId = document.getElementById('docgen-agent').value || undefined;
    // Slash-Agent: führendes „/Name" überschreibt den Dokument-Agenten für diesen Lauf
    const _slash = (typeof window.resolveSlashAgent === 'function') ? window.resolveSlashAgent(brief) : null;
    if (_slash && _slash.agent) {
      agentId = _slash.agent.id; brief = (_slash.rest || '').trim();
      showToast('➜ Agent: ' + (_slash.agent.name || _slash.agent.id));
      if (!brief) { showToast('Bitte nach /' + (_slash.agent.name || '') + ' noch eine Beschreibung eingeben'); return; }
    } else if (_slash && _slash.notFound) {
      showToast('Kein Agent für „/' + _slash.token + '" gefunden');
    }
    const model = (typeof Profile !== 'undefined' ? Profile.modelFor('general') : '') || undefined;
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
    if (_editing) { out.contentEditable = 'false'; _setEditUI(false); }  // ggf. laufende Bearbeitung beenden
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

  // Mermaid-Codeblöcke (```mermaid …```) im Browser zu PNG rendern und als
  // Markdown-Bild (data-URL) einsetzen, damit DOCX/PDF das Diagramm einbetten.
  function _svgDims(svg) {
    let w = 0, h = 0;
    const vb = svg.match(/viewBox="[\d.+-]+ [\d.+-]+ ([\d.]+) ([\d.]+)"/);
    if (vb) { w = parseFloat(vb[1]); h = parseFloat(vb[2]); }
    const wm = svg.match(/\bwidth="([\d.]+)"/), hm = svg.match(/\bheight="([\d.]+)"/);
    if (wm) w = parseFloat(wm[1]) || w;
    if (hm) h = parseFloat(hm[1]) || h;
    return { w: w || 800, h: h || 600 };
  }

  function _svgToPng(svg) {
    return new Promise((resolve, reject) => {
      try {
        const { w, h } = _svgDims(svg);
        const blob = new Blob([svg], { type: 'image/svg+xml;charset=utf-8' });
        const url = URL.createObjectURL(blob);
        const img = new Image();
        img.onload = () => {
          const scale = 2;  // schärfer für Druck
          const canvas = document.createElement('canvas');
          canvas.width = w * scale; canvas.height = h * scale;
          const ctx = canvas.getContext('2d');
          ctx.fillStyle = '#ffffff'; ctx.fillRect(0, 0, canvas.width, canvas.height);
          ctx.scale(scale, scale);
          ctx.drawImage(img, 0, 0, w, h);
          URL.revokeObjectURL(url);
          resolve(canvas.toDataURL('image/png'));
        };
        img.onerror = (e) => { URL.revokeObjectURL(url); reject(e); };
        img.src = url;
      } catch (e) { reject(e); }
    });
  }

  async function _mermaidToImages(md) {
    if (typeof mermaid === 'undefined' || !/```mermaid/.test(md)) return md;
    try { mermaid.initialize({ startOnLoad: false, theme: 'default' }); } catch (_) {}
    const re = /```mermaid\s*([\s\S]*?)```/g;
    const blocks = [];
    let m;
    while ((m = re.exec(md)) !== null) blocks.push({ full: m[0], def: m[1].trim() });
    let out = md, idx = 0;
    for (const b of blocks) {
      try {
        const { svg } = await mermaid.render('mmx-' + Date.now() + '-' + (idx++), b.def);
        const png = await _svgToPng(svg);
        out = out.replace(b.full, '\n\n![Diagramm](' + png + ')\n\n');
      } catch (_) { /* nicht renderbar → Codeblock bleibt stehen */ }
    }
    return out;
  }

  async function _exportDocx() {
    if (!_docText) return;
    showToast('Erstelle Dokument…');
    try {
      const profile = await (await fetch('/api/profile')).json();
      const content = await _mermaidToImages(_docText);
      const resp = await fetch('/api/export/docx', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ title: _docTitle, content, _include_header_image: true, _profile: profile }),
      });
      if (!resp.ok) throw new Error('HTTP ' + resp.status);
      const blob = await resp.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url; a.download = _docTitle.replace(/\s+/g, '_').slice(0, 40) + '.docx'; a.click();
      URL.revokeObjectURL(url);
      showToast('✓ Exportiert');
      _clearNote(true);
    } catch (e) { showToast('Export fehlgeschlagen: ' + e.message); }
  }

  async function _exportPdf() {
    if (!_docText) return;
    showToast('Erstelle PDF…');
    try {
      const profile = await (await fetch('/api/profile')).json();
      const content = await _mermaidToImages(_docText);
      const resp = await fetch('/api/export/pdf', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ title: _docTitle, content, _profile: profile }),
      });
      if (!resp.ok) throw new Error((await resp.json().catch(() => ({}))).detail || ('HTTP ' + resp.status));
      const blob = await resp.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url; a.download = _docTitle.replace(/\s+/g, '_').slice(0, 40) + '.pdf'; a.click();
      URL.revokeObjectURL(url);
      showToast('✓ PDF exportiert');
      _clearNote(true);
    } catch (e) { showToast('PDF-Export fehlgeschlagen: ' + e.message); }
  }

  async function _exportLatex() {
    if (!_docText) return;
    showToast('Erstelle LaTeX…');
    try {
      const profile = await (await fetch('/api/profile')).json();
      const resp = await fetch('/api/export/latex', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ title: _docTitle, content: _docText, _profile: profile }),
      });
      if (!resp.ok) throw new Error((await resp.json().catch(() => ({}))).detail || ('HTTP ' + resp.status));
      const blob = await resp.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url; a.download = _docTitle.replace(/\s+/g, '_').slice(0, 40) + '.tex'; a.click();
      URL.revokeObjectURL(url);
      showToast('✓ LaTeX (.tex) exportiert');
      _clearNote(true);
    } catch (e) { showToast('LaTeX-Export fehlgeschlagen: ' + e.message); }
  }

  // ── Besprechungsnotizen: automatisch sichern / wiederherstellen / leeren ──
  // Das „Bestehenden Text einfügen"-Feld wird laufend in localStorage gesichert,
  // sodass Notizen während einer Besprechung einen Reload überstehen. Nach dem
  // erfolgreichen Abspeichern/Export des erzeugten Dokuments wird es geleert.
  const _NOTE_KEY = 'docgen_paste_notes';

  function _noteStatus(msg) {
    const el = document.getElementById('docgen-note-status');
    if (el) el.textContent = msg || '';
  }

  function _autosaveNote() {
    const ta = document.getElementById('docgen-paste');
    if (!ta) return;
    try { localStorage.setItem(_NOTE_KEY, ta.value || ''); } catch (e) {}
  }

  function _restoreNote() {
    const ta = document.getElementById('docgen-paste');
    if (!ta) return;
    let saved = '';
    try { saved = localStorage.getItem(_NOTE_KEY) || ''; } catch (e) {}
    if (saved && !ta.value.trim()) {
      ta.value = saved;
      _noteStatus('↺ gespeicherte Notiz wiederhergestellt');
    }
  }

  function _saveNote() {
    _autosaveNote();
    const t = new Date().toLocaleTimeString();
    _noteStatus('💾 gespeichert ' + t);
    showToast('✓ Notiz gespeichert (bleibt nach Neuladen erhalten)');
  }

  // auto=true → stilles Leeren nach Export (kein Toast); sonst manuell per Button
  function _clearNote(auto) {
    const ta = document.getElementById('docgen-paste');
    if (ta) ta.value = '';
    try { localStorage.removeItem(_NOTE_KEY); } catch (e) {}
    _noteStatus(auto ? '🗑 Notiz nach Export automatisch geleert' : '🗑 Notiz geleert');
    if (!auto) showToast('✓ Notiz geleert');
  }

  // Fertiges Dokument von außen (z. B. Verfeinerungsschleife) anzeigen:
  // rendern, Platzhalter weg, Export-/Übernahme-Knöpfe einblenden.
  function showResult(text) {
    _docText = (text || '').trim();
    if (!_docText) return;
    _docTitle = _docText.split('\n')[0].replace(/^#+\s*/, '').slice(0, 60) || 'Dokument';
    _renderDoc(document.getElementById('docgen-output'), _docText);
    const st = document.getElementById('docgen-status');
    if (st) st.textContent = '✓ Verfeinert';
    _hidePlaceholder();
    _showActions(true);
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
    document.getElementById('btn-docgen-latex')?.addEventListener('click', _exportLatex);
    // Besprechungsnotizen: laufendes Autospeichern + Wiederherstellen + Buttons
    _restoreNote();
    document.getElementById('docgen-paste')?.addEventListener('input', _autosaveNote);
    document.getElementById('btn-docgen-note-save')?.addEventListener('click', _saveNote);
    document.getElementById('btn-docgen-note-clear')?.addEventListener('click', () => _clearNote(false));
    document.getElementById('docgen-file')?.addEventListener('change', _onFilesPicked);
    document.getElementById('docgen-dossier')?.addEventListener('change', _onDossierChange);
    document.getElementById('btn-docgen-dossier-refresh')?.addEventListener('click', _loadDossiers);
    document.getElementById('btn-docgen-export')?.addEventListener('click', _exportDocx);
    document.getElementById('btn-docgen-rag')?.addEventListener('click', () => {
      if (typeof RAG !== 'undefined') RAG.ingestText(_docTitle, _docText);
    });
    document.getElementById('btn-docgen-jury')?.addEventListener('click', () => {
      if (typeof Jury !== 'undefined') Jury.evaluate(_docText, { title: 'Dokumentenprüfung' });
    });
    document.getElementById('btn-docgen-edit')?.addEventListener('click', _startEdit);
    document.getElementById('btn-docgen-edit-save')?.addEventListener('click', _saveEdit);
    document.getElementById('btn-docgen-edit-cancel')?.addEventListener('click', _cancelEdit);
    // Beim Öffnen des Tabs Agenten + Wissensdatenbanken auffrischen
    document.querySelector('.tab-btn[data-tab="docgen"]')?.addEventListener('click', refresh);
  }

  return { init, refresh, loadFromChat, showResult, getText: () => _docText, setText: t => { _docText = t; } };
})();
