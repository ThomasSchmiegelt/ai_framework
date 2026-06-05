/* AI_Framework_Thomas — Multi-Agenten-Verfeinerungsschleife
   Verbessert ein Dokument iterativ durch mehrere Agenten bis die
   Änderungsrate unter den Schwellwert fällt. */

const Refine = (() => {

  let _agents = [];        // alle Agenten vom Backend
  let _selectedAgents = []; // [{id, name, system_prompt}]
  let _abortCtrl = null;
  let _iterHistory = [];   // [{n, agent, change_pct}] für Chart
  let _finalText = '';

  // ── Agenten laden ─────────────────────────────────────────────────────────
  async function _loadAgents() {
    try {
      _agents = await (await fetch('/api/agents')).json();
    } catch (_) { _agents = []; }
  }

  function _agentOptions(selectedId) {
    return _agents.map(a =>
      `<option value="${a.id}"${a.id === selectedId ? ' selected' : ''}>${escHtml(a.name)}</option>`
    ).join('');
  }

  // ── Agenten-Liste rendern ──────────────────────────────────────────────────
  function _renderAgentRows() {
    const wrap = document.getElementById('refine-agent-list');
    if (!wrap) return;
    wrap.innerHTML = '';
    _selectedAgents.forEach((ag, i) => {
      const row = document.createElement('div');
      row.style.cssText = 'display:flex;gap:6px;align-items:center';
      row.innerHTML = `
        <select class="sidebar-select refine-agent-sel" style="flex:1;font-size:12px" data-i="${i}">
          ${_agentOptions(ag.id)}
        </select>
        <button class="export-btn refine-agent-del" data-i="${i}" style="font-size:11px" title="Entfernen">✕</button>`;
      wrap.appendChild(row);
    });
    wrap.querySelectorAll('.refine-agent-sel').forEach(sel =>
      sel.addEventListener('change', e => {
        const idx = parseInt(e.target.dataset.i, 10);
        const found = _agents.find(a => a.id === e.target.value);
        if (found) _selectedAgents[idx] = { id: found.id, name: found.name, system_prompt: found.system_prompt };
      }));
    wrap.querySelectorAll('.refine-agent-del').forEach(btn =>
      btn.addEventListener('click', e => {
        _selectedAgents.splice(parseInt(e.target.dataset.i, 10), 1);
        _renderAgentRows();
      }));
  }

  // ── SVG-Balkendiagramm ────────────────────────────────────────────────────
  function _drawChart() {
    const svg = document.getElementById('refine-chart');
    if (!svg || !_iterHistory.length) return;
    document.getElementById('refine-chart-wrap').style.display = '';
    const W = svg.clientWidth || 600;
    const H = 80;
    const maxPct = Math.max(..._iterHistory.map(h => h.change_pct), 5);
    const barW = Math.max(8, Math.min(40, (W - 40) / _iterHistory.length - 4));
    let bars = '';
    _iterHistory.forEach((h, i) => {
      const bh = Math.max(2, Math.round((h.change_pct / maxPct) * (H - 24)));
      const x = 20 + i * (barW + 4);
      const y = H - 16 - bh;
      const col = h.change_pct < 5 ? '#4ade80' : h.change_pct < 15 ? '#facc15' : '#f87171';
      bars += `<rect x="${x}" y="${y}" width="${barW}" height="${bh}" fill="${col}" rx="2"/>`;
      bars += `<text x="${x + barW / 2}" y="${y - 3}" text-anchor="middle" font-size="9" fill="var(--text-muted)">${h.change_pct}%</text>`;
      bars += `<text x="${x + barW / 2}" y="${H - 3}" text-anchor="middle" font-size="9" fill="var(--text-muted)">${h.n}</text>`;
    });
    svg.style.height = H + 'px';
    svg.innerHTML = bars;
  }

  // ── Haupt-Schleife ────────────────────────────────────────────────────────
  async function _run() {
    const text = (document.getElementById('refine-text')?.value || '').trim();
    if (!text) { showToast('Bitte Dokumenttext einfügen'); return; }
    if (!_selectedAgents.length) { showToast('Mindestens einen Agenten hinzufügen'); return; }

    const threshold = parseFloat(document.getElementById('refine-threshold')?.value) || 2.0;
    const maxIter   = parseInt(document.getElementById('refine-max-iter')?.value)    || 10;
    const model = document.getElementById('planner-model-select')?.value ||
                  document.getElementById('docgen-model')?.value || '';

    _iterHistory = [];
    _finalText   = text;
    document.getElementById('refine-log').style.display = 'block';
    document.getElementById('refine-log').textContent = '';
    document.getElementById('refine-chart-wrap').style.display = 'none';
    document.getElementById('refine-export-row').style.display  = 'none';
    document.getElementById('btn-refine-stop').style.display = '';
    document.getElementById('btn-refine-run').disabled = true;
    document.getElementById('refine-status').textContent = '⏳ Läuft…';

    _abortCtrl = new AbortController();

    const _log = msg => {
      const el = document.getElementById('refine-log');
      if (el) { el.textContent += msg + '\n'; el.scrollTop = el.scrollHeight; }
    };

    try {
      const resp = await fetch('/api/refine-document', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        signal: _abortCtrl.signal,
        body: JSON.stringify({
          text, agents: _selectedAgents, threshold, max_iterations: maxIter, model,
        }),
      });
      if (!resp.ok || !resp.body) throw new Error(`HTTP ${resp.status}`);

      const reader = resp.body.getReader();
      const dec = new TextDecoder();
      let buf = '';

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buf += dec.decode(value, { stream: true });
        const lines = buf.split('\n'); buf = lines.pop();
        for (const line of lines) {
          if (!line.startsWith('data:')) continue;
          try {
            const ev = JSON.parse(line.slice(5).trim());
            if (ev.type === 'iteration_start') {
              _log(`▶ Iteration ${ev.n} — Agent: ${ev.agent}`);
              document.getElementById('refine-status').textContent = `⏳ Iteration ${ev.n}/${maxIter} — ${ev.agent}`;
            } else if (ev.type === 'iteration_done') {
              _iterHistory.push({ n: ev.n, agent: ev.agent, change_pct: ev.change_pct });
              _log(`  ✓ ${ev.change_pct} % Änderungen`);
              _finalText = ev.text;
              _drawChart();
            } else if (ev.type === 'converged') {
              _log(`✅ ${ev.message}`);
              document.getElementById('refine-status').textContent = `✅ ${ev.message}`;
            } else if (ev.type === 'done') {
              _finalText = ev.text || _finalText;
              document.getElementById('refine-text').value = _finalText;
              document.getElementById('refine-export-row').style.display = '';
              if (!document.getElementById('refine-status').textContent.startsWith('✅')) {
                document.getElementById('refine-status').textContent = `✓ Abgeschlossen (${_iterHistory.length} Iterationen)`;
              }
            } else if (ev.type === 'error') {
              _log('❌ Fehler: ' + ev.message);
              document.getElementById('refine-status').textContent = 'Fehler: ' + ev.message;
            }
          } catch (_) {}
        }
      }
    } catch (e) {
      if (e.name !== 'AbortError') {
        document.getElementById('refine-status').textContent = 'Fehler: ' + e.message;
      } else {
        document.getElementById('refine-status').textContent = '⏹ Abgebrochen';
      }
    } finally {
      document.getElementById('btn-refine-stop').style.display = 'none';
      document.getElementById('btn-refine-run').disabled = false;
      _abortCtrl = null;
    }
  }

  function _stop() {
    if (_abortCtrl) { _abortCtrl.abort(); }
  }

  // ── Initialisierung ────────────────────────────────────────────────────────
  async function init() {
    await _loadAgents();

    document.getElementById('btn-refine-add-agent')?.addEventListener('click', () => {
      if (!_agents.length) { showToast('Keine Agenten vorhanden – im 🤖 Agenten-Tab anlegen'); return; }
      const first = _agents[0];
      _selectedAgents.push({ id: first.id, name: first.name, system_prompt: first.system_prompt });
      _renderAgentRows();
    });

    document.getElementById('btn-refine-from-docgen')?.addEventListener('click', () => {
      const t = typeof DocGen !== 'undefined' ? DocGen.getText() : '';
      if (!t) { showToast('Dokumentengenerator hat noch keinen Text erzeugt'); return; }
      document.getElementById('refine-text').value = t;
      showToast('✓ Text übernommen');
    });

    document.getElementById('btn-refine-clear')?.addEventListener('click', () => {
      document.getElementById('refine-text').value = '';
    });

    document.getElementById('btn-refine-run')?.addEventListener('click', _run);
    document.getElementById('btn-refine-stop')?.addEventListener('click', _stop);

    document.getElementById('btn-refine-use-docgen')?.addEventListener('click', () => {
      if (!_finalText) return;
      if (typeof DocGen !== 'undefined') { DocGen.setText(_finalText); }
      document.getElementById('docgen-output').textContent = _finalText;
      showToast('✓ In Dokumentengenerator übernommen');
    });

    document.getElementById('btn-refine-export-docx')?.addEventListener('click', async () => {
      if (!_finalText) return;
      try {
        const r = await fetch('/api/export/docx', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ content: _finalText, title: 'Verfeinertes Dokument' }),
        });
        if (!r.ok) throw new Error(r.status);
        const blob = await r.blob();
        const a = document.createElement('a'); a.href = URL.createObjectURL(blob);
        a.download = 'verfeinertes_dokument.docx'; a.click();
      } catch (e) { showToast('DOCX-Export fehlgeschlagen: ' + e.message); }
    });

    document.getElementById('btn-refine-export-rag')?.addEventListener('click', () => {
      if (!_finalText || typeof RAG === 'undefined') return;
      RAG.ingestText('Verfeinertes Dokument', _finalText);
    });

    // Tab-Wechsel: Agentenliste auffrischen
    document.querySelector('.tab-btn[data-tab="docgen"]')?.addEventListener('click', async () => {
      await _loadAgents();
      _renderAgentRows();
    });
  }

  return { init };
})();
