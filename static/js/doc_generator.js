/* AI_Framework_Thomas — Dokumentengenerator
   Erzeugt Dokumente mit einem im Agenten-Tab definierten Dokument-Agenten,
   optional gestützt auf Wissensdatenbanken (RAG). Ergebnis als DOCX exportierbar
   oder zurück in eine Wissensdatenbank übernehmbar. */

const DocGen = (() => {
  let _docText = '';
  let _docTitle = 'Dokument';
  let _busy = false;

  async function _loadAgents() {
    const sel = document.getElementById('docgen-agent');
    if (!sel) return;
    const prev = sel.value;
    let agents = [];
    try { agents = await (await fetch('/api/agents')).json(); } catch (_) {}
    // Dokument-Agenten (Kategorie „Dokumentation") zuerst
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

  function refresh() { _loadAgents(); _loadRag(); }

  async function _run() {
    if (_busy) return;
    const brief = document.getElementById('docgen-brief').value.trim();
    if (!brief) { showToast('Bitte beschreibe das gewünschte Dokument'); return; }
    const agentId = document.getElementById('docgen-agent').value || undefined;
    const model = document.getElementById('model-select')?.value;
    const ragSel = document.getElementById('docgen-rag');
    const rag = Array.from(ragSel.selectedOptions).map(o => o.value);
    const science = document.getElementById('docgen-science').checked;

    _docTitle = brief.split('\n')[0].slice(0, 60) || 'Dokument';
    _busy = true;
    const status = document.getElementById('docgen-status');
    const out = document.getElementById('docgen-output');
    document.getElementById('btn-docgen-run').disabled = true;
    document.getElementById('btn-docgen-export').style.display = 'none';
    document.getElementById('btn-docgen-rag').style.display = 'none';
    status.textContent = '⏳ Dokument wird erzeugt…';
    out.innerHTML = '';
    _docText = '';

    try {
      const resp = await fetch('/api/chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          messages: [{ role: 'user', content: brief }],
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
      // Final als Markdown rendern (inkl. Formeln/Links)
      if (typeof marked !== 'undefined') {
        if (window._ensureKatexMarked) window._ensureKatexMarked();
        out.innerHTML = marked.parse(_docText, { gfm: true, breaks: true });
        out.querySelectorAll('a[href]').forEach(a => { a.target = '_blank'; a.rel = 'noopener noreferrer'; });
      }
      status.textContent = _docText.trim() ? '✓ Fertig' : 'Kein Inhalt erzeugt';
      if (_docText.trim()) {
        document.getElementById('btn-docgen-export').style.display = '';
        document.getElementById('btn-docgen-rag').style.display = '';
      }
    } catch (e) {
      status.textContent = '';
      showToast('Fehler: ' + e.message);
    } finally {
      _busy = false;
      document.getElementById('btn-docgen-run').disabled = false;
    }
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

  function init() {
    refresh();
    document.getElementById('btn-docgen-run')?.addEventListener('click', _run);
    document.getElementById('btn-docgen-export')?.addEventListener('click', _exportDocx);
    document.getElementById('btn-docgen-rag')?.addEventListener('click', () => {
      if (typeof RAG !== 'undefined') RAG.ingestText(_docTitle, _docText);
    });
    // Beim Öffnen des Tabs Agenten + Wissensdatenbanken auffrischen
    document.querySelector('.tab-btn[data-tab="docgen"]')?.addEventListener('click', refresh);
  }

  return { init, refresh };
})();
