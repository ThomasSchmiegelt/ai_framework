/* AI_Framework_Thomas — Agentische Recherche */

const Research = (() => {
  const aspects = [];
  let _reportText = '';
  let _reportTopic = '';

  // ── Aspekt-Tags ─────────────────────────────────────────────────────────

  function renderTags() {
    const container = document.getElementById('aspect-tags');
    container.innerHTML = '';
    aspects.forEach((a, i) => {
      const tag = document.createElement('span');
      tag.className = 'aspect-tag';
      tag.innerHTML = `${escHtml(a)}<button class="remove-tag" data-i="${i}" title="Entfernen">×</button>`;
      container.appendChild(tag);
    });
    container.querySelectorAll('.remove-tag').forEach(btn => {
      btn.addEventListener('click', () => {
        aspects.splice(+btn.dataset.i, 1);
        renderTags();
      });
    });
  }

  function addAspect() {
    const input = document.getElementById('aspect-input');
    const val = input.value.trim();
    if (!val || aspects.includes(val)) { input.value = ''; return; }
    aspects.push(val);
    input.value = '';
    renderTags();
  }

  // ── Recherche starten ────────────────────────────────────────────────────

  function buildProgressList(topic, aspectList) {
    const list = document.getElementById('research-progress-list');
    list.innerHTML = `
      <div class="rp-header">
        <strong>🔍 Suchen für: ${escHtml(topic)}</strong>
      </div>
    `;
    aspectList.forEach(a => {
      const row = document.createElement('div');
      row.className = 'rp-step pending';
      row.id = `rp-${cssId(a)}`;
      row.innerHTML = `<span class="rp-spinner"></span><span class="rp-label">${escHtml(topic)} · ${escHtml(a)}</span><span class="rp-status">Wartend…</span>`;
      list.appendChild(row);
    });

    const synthRow = document.createElement('div');
    synthRow.className = 'rp-step pending';
    synthRow.id = 'rp-synth';
    synthRow.innerHTML = `<span class="rp-spinner"></span><span class="rp-label">KI-Synthese</span><span class="rp-status">Wartend…</span>`;
    list.appendChild(synthRow);
  }

  function setStepState(id, state, statusText) {
    const row = document.getElementById(id);
    if (!row) return;
    row.className = `rp-step ${state}`;
    const s = row.querySelector('.rp-status');
    if (s) s.textContent = statusText;
  }

  function cssId(str) {
    return str.replace(/[^a-zA-Z0-9]/g, '_');
  }

  async function startResearch() {
    const topic = document.getElementById('research-topic').value.trim();
    if (!topic) { showToast('Bitte Thema eingeben'); return; }
    if (aspects.length === 0) { showToast('Bitte mindestens einen Aspekt hinzufügen'); return; }
    _reportTopic = topic;
    _reportText = '';

    const model = (typeof Profile !== 'undefined' ? Profile.modelFor('science') : '') || undefined;

    // UI-Zustand
    document.getElementById('btn-start-research').disabled = true;
    document.getElementById('btn-reset-research').style.display = 'none';
    const outputArea = document.getElementById('research-output-area');
    outputArea.style.display = 'block';
    const ph = document.getElementById('research-placeholder');
    if (ph) ph.style.display = 'none';
    document.getElementById('research-report').innerHTML = '';

    buildProgressList(topic, aspects);

    // Alle Schritte auf "laufend" setzen (parallele Suchen)
    aspects.forEach(a => setStepState(`rp-${cssId(a)}`, 'running', 'Suche läuft…'));

    let reportText = '';
    const reportEl = document.getElementById('research-report');
    reportEl.innerHTML = '';

    try {
      const resp = await fetch('/api/research', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ topic, aspects: [...aspects], model }),
      });

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
          let ev;
          try { ev = JSON.parse(line.slice(6)); } catch { continue; }

          if (ev.type === 'research_start') {
            ev.aspects.forEach(a => setStepState(`rp-${cssId(a)}`, 'running', 'Suche läuft…'));
          }

          if (ev.type === 'search_done') {
            setStepState(`rp-${cssId(ev.aspect)}`, 'done', 'Fertig ✓');
          }

          if (ev.type === 'synthesizing') {
            setStepState('rp-synth', 'running', 'Zusammenfassung läuft…');
            reportEl.innerHTML = '<div class="report-placeholder">Bericht wird erstellt…</div>';
          }

          if (ev.type === 'text') {
            if (reportEl.querySelector('.report-placeholder')) {
              reportEl.innerHTML = '';
            }
            reportText += ev.content;
            _reportText = reportText;
            if (window._ensureKatexMarked) window._ensureKatexMarked();
            reportEl.innerHTML = typeof marked !== 'undefined'
              ? marked.parse(reportText, { gfm: true, breaks: true })
              : `<pre>${escHtml(reportText)}</pre>`;
            reportEl.querySelectorAll('a[href]').forEach(a => {
              a.target = '_blank';
              a.rel = 'noopener noreferrer';
            });
            reportEl.scrollIntoView({ behavior: 'smooth', block: 'end' });
          }

          if (ev.type === 'sources') {
            renderSources(ev.data);
          }

          if (ev.type === 'done') {
            if (ev.tokens && typeof TokenMeter !== 'undefined') TokenMeter.add(ev.tokens, 'Recherche');
            setStepState('rp-synth', 'done', 'Fertig ✓');
            document.getElementById('btn-start-research').disabled = false;
            document.getElementById('btn-reset-research').style.display = 'inline-flex';
            _showResultActions(true);
            if (typeof hljs !== 'undefined') {
              reportEl.querySelectorAll('pre code').forEach(el => hljs.highlightElement(el));
            }
          }

          if (ev.type === 'error') {
            showToast(`Fehler: ${ev.message}`);
            setStepState('rp-synth', 'pending', 'Fehler');
            document.getElementById('btn-start-research').disabled = false;
          }
        }
      }
    } catch (e) {
      showToast(`Verbindungsfehler: ${e.message}`);
      document.getElementById('btn-start-research').disabled = false;
    }
  }

  function renderSources(allSources) {
    const reportEl = document.getElementById('research-report');
    const wrapper = document.createElement('div');
    wrapper.className = 'research-sources';
    wrapper.innerHTML = '<h3 class="sources-heading">🔗 Quellen</h3>';

    for (const group of allSources) {
      if (!group.sources || group.sources.length === 0) continue;
      const section = document.createElement('div');
      section.className = 'sources-group';
      const heading = document.createElement('div');
      heading.className = 'sources-aspect';
      heading.textContent = group.aspect;
      section.appendChild(heading);

      const list = document.createElement('ol');
      list.className = 'sources-list';
      for (const s of group.sources) {
        const li = document.createElement('li');
        li.innerHTML = `<a href="${escHtml(s.url)}" target="_blank" rel="noopener">${escHtml(s.title)}</a>
          <span class="source-snippet">${escHtml(s.body.slice(0, 120))}…</span>`;
        list.appendChild(li);
      }
      section.appendChild(list);
      wrapper.appendChild(section);
    }

    reportEl.appendChild(wrapper);
  }

  function reset() {
    document.getElementById('research-output-area').style.display = 'none';
    document.getElementById('research-progress-list').innerHTML = '';
    document.getElementById('research-report').innerHTML = '';
    document.getElementById('btn-reset-research').style.display = 'none';
    _showResultActions(false);
    const ph = document.getElementById('research-placeholder');
    if (ph) ph.style.display = '';
    document.getElementById('btn-start-research').disabled = false;
    document.getElementById('research-topic').focus();
  }

  // Ergebnis-Aktionen (Präsentation/Dokument/DOCX/PDF/RAG) ein-/ausblenden
  function _showResultActions(show) {
    const disp = show ? '' : 'none';
    ['btn-research-present', 'btn-research-to-doc', 'btn-research-export',
     'btn-research-pdf', 'btn-research-rag'].forEach(id => {
      const el = document.getElementById(id);
      if (el) el.style.display = disp;
    });
  }

  // ── Recherche-Export ─────────────────────────────────────────────────────

  async function exportDocx() {
    if (!_reportText) { showToast('Kein Bericht zum Exportieren'); return; }
    showToast('Erstelle Dokument…');
    try {
      const profileResp = await fetch('/api/profile');
      const profile = await profileResp.json();

      const resp = await fetch('/api/export/docx', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          title: 'Recherche: ' + _reportTopic,
          content: _reportText,
          _include_header_image: true,
          _profile: profile
        }),
      });
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const blob = await resp.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `recherche_${_reportTopic.substring(0, 20).replace(/\s/g, '_')}.docx`;
      a.click();
      URL.revokeObjectURL(url);
      showToast('✓ Dokument exportiert');
    } catch (e) {
      showToast('Export fehlgeschlagen: ' + e.message);
    }
  }

  async function exportPdf() {
    if (!_reportText) { showToast('Kein Bericht zum Exportieren'); return; }
    showToast('Erstelle PDF…');
    try {
      const profile = await (await fetch('/api/profile')).json();
      const resp = await fetch('/api/export/pdf', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ title: 'Recherche: ' + _reportTopic, content: _reportText, _profile: profile }),
      });
      if (!resp.ok) throw new Error((await resp.json().catch(() => ({}))).detail || ('HTTP ' + resp.status));
      const blob = await resp.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url; a.download = `recherche_${_reportTopic.substring(0, 20).replace(/\s/g, '_')}.pdf`; a.click();
      URL.revokeObjectURL(url);
      showToast('✓ PDF exportiert');
    } catch (e) { showToast('PDF-Export fehlgeschlagen: ' + e.message); }
  }

  // Bericht als Präsentation in den Canvas (Querformat). Formeln ($…$) bleiben
  // erhalten und werden auf dem Canvas via KaTeX gerendert.
  async function present() {
    if (!_reportText) { showToast('Kein Bericht'); return; }
    const model = (typeof Profile !== 'undefined' ? Profile.modelFor('general') : '') || undefined;
    showToast('🖥️ Präsentation wird erstellt…');
    try {
      const r = await fetch('/api/presentation/from-text', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text: _reportText, model }),
      });
      if (!r.ok) throw new Error((await r.json()).detail || r.status);
      const data = await r.json();
      if (data.tokens && typeof TokenMeter !== 'undefined') TokenMeter.add(data.tokens, 'Recherche');
      delete data.tokens;
      if (typeof CanvasRenderer !== 'undefined') CanvasRenderer.render(data);
      if (typeof switchTab === 'function') switchTab('canvas');
      showToast('✓ Präsentation im Canvas erstellt');
    } catch (e) { showToast('Präsentation fehlgeschlagen: ' + e.message); }
  }

  // Bericht (inkl. Formeln) an den Dokumentengenerator übergeben — dort kann ein
  // formelbewusstes Dokument erzeugt und exportiert/präsentiert werden.
  function toDocGen() {
    if (!_reportText) { showToast('Kein Bericht'); return; }
    if (typeof DocGen !== 'undefined' && DocGen.loadFromChat) {
      DocGen.loadFromChat(`Recherche: ${_reportTopic}`, _reportText);
    }
    document.querySelector('.tab-btn[data-tab="docgen"]')?.click();
  }

  /* Ziehbarer Trenner: Einstellungen ↔ Bericht */
  const _SPLIT_KEY = 'research_left_width';
  function _initSplitter() {
    const splitter = document.getElementById('research-splitter');
    const left = document.getElementById('research-left');
    const body = document.getElementById('research-body');
    if (!splitter || !left || !body) return;
    const saved = parseInt(localStorage.getItem(_SPLIT_KEY) || '', 10);
    if (saved > 0) left.style.width = saved + 'px';
    const _apply = (x) => {
      const rect = body.getBoundingClientRect();
      left.style.width = Math.max(340, Math.min(x - rect.left, rect.width - 280)) + 'px';
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
      e.preventDefault(); splitter.classList.add('dragging');
      document.body.style.userSelect = 'none';
      document.addEventListener('mousemove', _onMove);
      document.addEventListener('mouseup', _onUp);
    });
    splitter.addEventListener('dblclick', () => { left.style.width = ''; localStorage.removeItem(_SPLIT_KEY); });
  }

  // ── Init ─────────────────────────────────────────────────────────────────

  function init() {
    _initSplitter();
    document.getElementById('btn-add-aspect').addEventListener('click', addAspect);

    document.getElementById('aspect-input').addEventListener('keydown', e => {
      if (e.key === 'Enter') { e.preventDefault(); addAspect(); }
    });

    document.getElementById('btn-start-research').addEventListener('click', startResearch);
    document.getElementById('btn-reset-research').addEventListener('click', reset);
    document.getElementById('btn-research-export')?.addEventListener('click', exportDocx);
    document.getElementById('btn-research-pdf')?.addEventListener('click', exportPdf);
    document.getElementById('btn-research-present')?.addEventListener('click', present);
    document.getElementById('btn-research-to-doc')?.addEventListener('click', toDocGen);
    document.getElementById('btn-research-rag')?.addEventListener('click', () => {
      const topic = document.getElementById('research-topic').value.trim() || 'Recherche';
      if (typeof RAG !== 'undefined') RAG.ingestText(`Recherche: ${topic}`, _reportText);
    });

    // Enter im Thema-Feld springt zum Aspekt-Feld
    document.getElementById('research-topic').addEventListener('keydown', e => {
      if (e.key === 'Enter') {
        e.preventDefault();
        document.getElementById('aspect-input').focus();
      }
    });
  }

  return { init, addAspect, exportDocx };
})();
