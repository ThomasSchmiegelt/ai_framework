/* AI_Framework_Thomas — RAG / Wissenssammlungen
   Sammlungen anlegen, Dokumente hochladen (Embeddings via Ollama, Speicher in
   SQLite). Im Chat wählbar über den 📚-Umschalter. */

const RAG = (() => {
  let _collections = [];
  let _uploadTargetId = null;
  let _ragOptimize = localStorage.getItem('rag_optimize') === '1';

  // ── Floating Fortschrittskarte ──────────────────────────────────────────────
  let _optCard = null;

  function _showOptCard(filename) {
    if (!_optCard) {
      _optCard = document.createElement('div');
      _optCard.className = 'rag-opt-card';
      document.body.appendChild(_optCard);
    }
    _optCard.innerHTML = `
      <div class="rag-opt-title">🔄 RAG-Optimierung</div>
      <div class="rag-opt-file">${filename}</div>
      <div class="rag-opt-step" id="rag-opt-step">Startet…</div>
      <div class="rag-opt-bar-wrap"><div class="rag-opt-bar" id="rag-opt-bar" style="width:5%"></div></div>`;
    _optCard.style.display = 'block';
  }

  function _updateOptCard(step, pct) {
    if (!_optCard) return;
    const s = document.getElementById('rag-opt-step');
    const b = document.getElementById('rag-opt-bar');
    if (s) s.textContent = step;
    if (b) b.style.width = pct + '%';
  }

  function _hideOptCard() {
    if (_optCard) { _optCard.style.display = 'none'; }
  }

  // Regler „schnell ↔ gründlich" → konkrete Chunk-/Trefferwerte
  const SPEED = {
    1: { label: 'schnell',        chunk_size: 500,  chunk_overlap: 60,  top_k: 3, char_limit: 1500, detail: 'kleine Abschnitte, wenig Kontext – schnell & sparsam' },
    2: { label: 'ausgewogen',     chunk_size: 900,  chunk_overlap: 120, top_k: 4, char_limit: 3500, detail: 'ausgewogener Standard' },
    3: { label: 'gründlich',      chunk_size: 1200, chunk_overlap: 180, top_k: 6, char_limit: 5000, detail: 'mehr und größere Treffer' },
    4: { label: 'sehr gründlich', chunk_size: 1600, chunk_overlap: 240, top_k: 8, char_limit: 7000, detail: 'maximaler Kontext (langsamer, mehr Speicher)' },
  };
  const STRICT = { 1: 'kreativ', 2: 'ausgewogen', 3: 'korrekt' };
  const STRICT_DETAIL = {
    kreativ:    'darf frei mit eigenem Wissen ergänzen',
    ausgewogen: 'vorrangig aus den Quellen, ergänzt bei Bedarf',
    korrekt:    'antwortet ausschließlich aus den Quellen',
  };

  function _speed() { return SPEED[document.getElementById('rag-speed').value] || SPEED[2]; }
  function _strict() { return STRICT[document.getElementById('rag-strict').value] || 'ausgewogen'; }

  function _updateSliderLabels() {
    const sp = _speed(), st = _strict();
    document.getElementById('rag-speed-label').textContent = sp.label;
    document.getElementById('rag-speed-detail').textContent =
      `${sp.detail} · Chunk ${sp.chunk_size}/${sp.chunk_overlap}, top-k ${sp.top_k}, max ${sp.char_limit} Z.`;
    document.getElementById('rag-strict-label').textContent = st;
    document.getElementById('rag-strict-detail').textContent = STRICT_DETAIL[st];
  }

  async function _loadEmbedModel() {
    try {
      const data = await (await fetch('/api/rag/tiers')).json();
      document.getElementById('rag-embed-model').textContent = data.embed_model || '?';
    } catch (e) { /* ignore */ }
  }

  async function loadCollections() {
    try {
      const r = await fetch('/api/rag/collections');
      _collections = await r.json();
    } catch (e) { _collections = []; }
    _renderCollections();
    _fillChatSelect();
    _fillTargetSelect();
  }

  function _fillTargetSelect() {
    const sel = document.getElementById('rag-target-select');
    if (!sel) return;
    const prev = sel.value;
    sel.innerHTML = '';
    for (const c of _collections) {
      const o = document.createElement('option');
      o.value = c.id;
      o.textContent = c.name;
      sel.appendChild(o);
    }
    if (prev) sel.value = prev;
  }

  async function _fillConvSelect() {
    const sel = document.getElementById('rag-conv-select');
    if (!sel) return;
    try {
      const convs = await (await fetch('/api/conversations')).json();
      sel.innerHTML = '';
      for (const c of convs) {
        const o = document.createElement('option');
        o.value = c.id;
        o.textContent = (c.title || c.id).slice(0, 60);
        sel.appendChild(o);
      }
    } catch (e) { /* ignore */ }
  }

  async function _importFromChat() {
    const convId = document.getElementById('rag-conv-select').value;
    const cid = document.getElementById('rag-target-select').value;
    const del = document.getElementById('rag-move-delete').checked;
    if (!convId || !cid) { showToast('Gespräch und Zielsammlung wählen'); return; }
    if (del && !confirm('Original-Gespräch nach dem Übernehmen löschen?')) return;
    showToast('⏳ Gespräch wird übernommen…');
    try {
      const r = await fetch(`/api/rag/collections/${cid}/from-conversation`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ conversation_id: convId, delete_after: del }),
      });
      const data = await r.json();
      if (!r.ok) throw new Error(data.detail || r.status);
      showToast(`✓ Übernommen: ${data.n_chunks} Chunks${data.deleted ? ' · Original gelöscht' : ''}`);
      if (data.deleted && typeof Chat !== 'undefined' && Chat.loadConversationList) Chat.loadConversationList();
      loadCollections();
      _fillConvSelect();
    } catch (e) { showToast('Fehler: ' + e.message); }
  }

  function _fillChatSelect() {
    const panel = document.getElementById('rag-chat-panel');
    if (!panel) return;
    // Bereits aktivierte IDs merken
    const prev = new Set(
      Array.from(panel.querySelectorAll('input[type="checkbox"]:checked')).map(cb => cb.value)
    );
    panel.innerHTML = '';
    if (!_collections.length) {
      panel.innerHTML = '<span class="rag-chat-empty">Keine Sammlungen</span>';
      return;
    }
    for (const c of _collections) {
      const label = document.createElement('label');
      label.className = 'rag-chat-option';
      const cb = document.createElement('input');
      cb.type = 'checkbox'; cb.value = c.id;
      if (prev.has(c.id)) cb.checked = true;
      const nameSpan = document.createElement('span');
      nameSpan.textContent = c.name;
      const countSpan = document.createElement('span');
      countSpan.className = 'rag-chat-count';
      countSpan.textContent = c.n_chunks != null ? `${c.n_chunks} Chunks` : '';
      label.append(cb, nameSpan, countSpan);
      panel.appendChild(label);
    }
    // Badge auf Toggle aktualisieren wenn sich Selektion ändert
    panel.querySelectorAll('input[type="checkbox"]').forEach(cb => {
      cb.addEventListener('change', _updateRagToggleBadge);
    });
    _updateRagToggleBadge();
  }

  function _updateRagToggleBadge() {
    const btn = document.getElementById('btn-rag-toggle');
    if (!btn) return;
    const checked = document.querySelectorAll('#rag-chat-panel input:checked').length;
    // Text-Label mit Zähler
    btn.textContent = checked > 0 ? `📚 RAG (${checked})` : '📚 RAG';
  }

  async function _renderCollections() {
    const wrap = document.getElementById('rag-collections-list');
    if (!_collections.length) {
      wrap.innerHTML = '<p class="planner-muted" style="font-size:13px">Noch keine Wissensdatenbank angelegt.</p>';
      return;
    }
    wrap.innerHTML = '';
    for (const c of _collections) {
      const card = document.createElement('div');
      card.className = 'agent-card';
      card.style.cssText = 'margin-bottom:12px;padding:12px';
      const docs = await (await fetch(`/api/rag/collections/${c.id}/documents`)).json();
      const docRows = docs.map(d => `
        <div style="display:flex;justify-content:space-between;align-items:center;gap:6px;font-size:12.5px;padding:3px 0">
          <span style="flex:1;min-width:0">📄 ${escHtml(d.filename)} <span class="planner-muted">· ${d.n_chunks} Chunks</span></span>
          <button class="export-btn rag-exp-doc" data-id="${d.id}" data-fmt="md" style="font-size:11px" title="Inhalt als Markdown exportieren">⬇ md</button>
          <button class="export-btn rag-exp-doc" data-id="${d.id}" data-fmt="txt" style="font-size:11px" title="Inhalt als Text exportieren">⬇ txt</button>
          <button class="export-btn rag-del-doc" data-id="${d.id}" style="font-size:11px">entfernen</button>
        </div>`).join('') || '<span class="planner-muted" style="font-size:12px">Keine Dokumente</span>';
      card.innerHTML = `
        <div style="display:flex;justify-content:space-between;align-items:center;gap:8px">
          <strong>📚 ${escHtml(c.name)}</strong>
          <span class="planner-muted" style="font-size:11.5px">
            🔎 ${escHtml(c.tier)} · ✍ ${escHtml(c.strictness || 'ausgewogen')} · top-k ${c.top_k}
            · max ${c.char_limit} Z. · ${c.clean ? 'bereinigt' : 'roh'} · ${c.n_docs} Dok / ${c.n_chunks} Chunks
          </span>
        </div>
        <details class="rag-docs" style="margin:8px 0 6px"${docs.length && docs.length <= 4 ? ' open' : ''}>
          <summary class="planner-muted" style="cursor:pointer;user-select:none;font-size:12.5px">
            📄 ${docs.length} Dokument${docs.length === 1 ? '' : 'e'} – ein-/ausklappen
          </summary>
          <div style="margin-top:6px">${docRows}</div>
        </details>
        <div style="display:flex;gap:8px;flex-wrap:wrap">
          <button class="export-btn rag-add-doc" data-id="${c.id}">＋ Dokument(e) hinzufügen</button>
          <button class="export-btn rag-del-coll" data-id="${c.id}">🗑 Datenbank löschen</button>
          <button class="export-btn rag-pub-coll" data-id="${c.id}" title="Sammlung als .ragpack.json in den Serverpfad exportieren"
            style="${c.server_path ? '' : 'opacity:.5'}">📤 Veröffentlichen</button>
        </div>
        <div style="display:flex;gap:6px;align-items:center;margin-top:8px;font-size:12px">
          <span style="color:var(--text-muted);white-space:nowrap">🗂 Serverpfad:</span>
          <input type="text" class="sidebar-select rag-srv-path-inp" data-id="${c.id}"
            value="${escHtml(c.server_path || '')}"
            placeholder="z.B. /mnt/share/rag oder \\\\server\\rag"
            style="flex:1;font-size:12px;padding:3px 7px" />
          <button class="export-btn rag-srv-path-save" data-id="${c.id}">Speichern</button>
        </div>`;
      wrap.appendChild(card);
    }
    wrap.querySelectorAll('.rag-add-doc').forEach(b =>
      b.addEventListener('click', () => { _uploadTargetId = b.dataset.id; document.getElementById('rag-file-input').click(); }));
    wrap.querySelectorAll('.rag-del-coll').forEach(b =>
      b.addEventListener('click', () => _deleteCollection(b.dataset.id)));
    wrap.querySelectorAll('.rag-del-doc').forEach(b =>
      b.addEventListener('click', () => _deleteDoc(b.dataset.id)));
    wrap.querySelectorAll('.rag-exp-doc').forEach(b =>
      b.addEventListener('click', () => _exportDoc(b.dataset.id, b.dataset.fmt)));
    wrap.querySelectorAll('.rag-srv-path-save').forEach(b =>
      b.addEventListener('click', () => {
        const inp = wrap.querySelector(`.rag-srv-path-inp[data-id="${b.dataset.id}"]`);
        _saveServerPath(b.dataset.id, inp ? inp.value.trim() : '');
      }));
    wrap.querySelectorAll('.rag-pub-coll').forEach(b =>
      b.addEventListener('click', () => _publishCollection(b.dataset.id)));
  }

  async function _saveServerPath(cid, path) {
    try {
      const r = await fetch(`/api/rag/collections/${cid}/server-path`, {
        method: 'PUT', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ server_path: path }),
      });
      if (!r.ok) throw new Error((await r.json()).detail || r.status);
      showToast(path ? `✓ Serverpfad gespeichert` : '✓ Serverpfad gelöscht');
      loadCollections();
    } catch (e) { showToast('Fehler: ' + e.message); }
  }

  async function _publishCollection(cid) {
    showToast('⏳ Wird veröffentlicht…');
    try {
      const r = await fetch(`/api/rag/collections/${cid}/publish`, { method: 'POST' });
      const d = await r.json();
      if (!r.ok) throw new Error(d.detail || r.status);
      showToast(`✓ Veröffentlicht: ${d.file} (${d.n_chunks} Chunks)`);
    } catch (e) { showToast('Fehler: ' + e.message); }
  }

  async function _openCloneModal() {
    const overlay = document.getElementById('rag-clone-overlay');
    if (!overlay) return;
    document.getElementById('rag-clone-dir').value = '';
    document.getElementById('rag-clone-list').innerHTML = '<span class="planner-muted">Verzeichnis eingeben und suchen</span>';
    overlay.classList.add('active');
  }

  async function _scanServerDir() {
    const dir = document.getElementById('rag-clone-dir').value.trim();
    if (!dir) { showToast('Bitte Verzeichnis eingeben'); return; }
    const list = document.getElementById('rag-clone-list');
    list.innerHTML = '<span class="planner-muted">⏳ Suche…</span>';
    try {
      const r = await fetch(`/api/rag/server-packs?dir=${encodeURIComponent(dir)}`);
      if (!r.ok) { const d = await r.json(); throw new Error(d.detail || r.status); }
      const packs = await r.json();
      if (!packs.length) { list.innerHTML = '<span class="planner-muted">Keine .ragpack.json Dateien gefunden</span>'; return; }
      list.innerHTML = packs.map(p => `
        <div style="display:flex;justify-content:space-between;align-items:center;gap:8px;padding:6px 0;border-bottom:1px solid var(--border)">
          <div>
            <strong style="font-size:13px">📚 ${escHtml(p.name)}</strong>
            <span class="planner-muted" style="font-size:11.5px;margin-left:8px">${p.n_docs} Dok / ${p.n_chunks} Chunks</span>
            <div class="planner-muted" style="font-size:11px">${escHtml(p.file)}</div>
          </div>
          <button class="export-btn rag-clone-btn" data-file="${escHtml(p.file)}">🗂 Klonen</button>
        </div>`).join('');
      list.querySelectorAll('.rag-clone-btn').forEach(b =>
        b.addEventListener('click', async () => {
          b.disabled = true; b.textContent = '⏳';
          try {
            const r2 = await fetch('/api/rag/collections/clone', {
              method: 'POST', headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ file_path: b.dataset.file }),
            });
            const d = await r2.json();
            if (!r2.ok) throw new Error(d.detail || r2.status);
            showToast(`✓ Geklont: ${d.name} (${d.n_docs} Dok)`);
            document.getElementById('rag-clone-overlay').classList.remove('active');
            loadCollections();
          } catch (e) { showToast('Fehler: ' + e.message); b.disabled = false; b.textContent = '🗂 Klonen'; }
        }));
    } catch (e) { list.innerHTML = `<span style="color:#ff6b6b">${escHtml(e.message)}</span>`; }
  }

  // Dokumentinhalt (aus Chunks rekonstruiert) als Markdown/TXT herunterladen
  function _exportDoc(did, fmt) {
    const a = document.createElement('a');
    a.href = `/api/rag/documents/${encodeURIComponent(did)}/export?format=${fmt === 'txt' ? 'txt' : 'md'}`;
    document.body.appendChild(a);
    a.click();
    a.remove();
    showToast(`⬇ Export als ${fmt.toUpperCase()}…`);
  }

  async function _create() {
    const name = document.getElementById('rag-new-name').value.trim();
    if (!name) { showToast('Bitte einen Namen eingeben'); return; }
    const sp = _speed();
    const payload = {
      name,
      tier: sp.label,
      chunk_size: sp.chunk_size,
      chunk_overlap: sp.chunk_overlap,
      top_k: sp.top_k,
      char_limit: sp.char_limit,
      strictness: _strict(),
      clean: document.getElementById('rag-clean').checked,
    };
    try {
      const r = await fetch('/api/rag/collections', {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
      });
      if (!r.ok) throw new Error((await r.json()).detail || r.status);
      document.getElementById('rag-new-name').value = '';
      showToast('✓ Wissensdatenbank angelegt');
      loadCollections();
    } catch (e) { showToast('Fehler: ' + e.message); }
  }

  async function _deleteCollection(id) {
    if (!confirm('Wissensdatenbank mit allen Dokumenten löschen?')) return;
    await fetch(`/api/rag/collections/${id}`, { method: 'DELETE' });
    showToast('Wissensdatenbank gelöscht');
    loadCollections();
  }

  async function _deleteDoc(id) {
    await fetch(`/api/rag/documents/${id}`, { method: 'DELETE' });
    showToast('Dokument entfernt');
    loadCollections();
  }

  async function _uploadFiles(files) {
    if (!_uploadTargetId) return;
    for (const f of files) {
      showToast(`⏳ ${f.name} wird verarbeitet…`);
      const fd = new FormData();
      fd.append('file', f);
      try {
        const r = await fetch(`/api/rag/collections/${_uploadTargetId}/documents`, { method: 'POST', body: fd });
        const data = await r.json();
        if (!r.ok) throw new Error(data.detail || r.status);
        showToast(`✓ ${f.name}: ${data.n_chunks} Chunks`);
      } catch (e) { showToast(`Fehler bei ${f.name}: ${e.message}`); }
    }
    loadCollections();
  }

  async function _uploadFilesOptimized(files) {
    if (!_uploadTargetId) return;
    for (const f of files) {
      _showOptCard(f.name);
      const fd = new FormData();
      fd.append('file', f);
      try {
        const resp = await fetch(
          `/api/rag/collections/${_uploadTargetId}/documents/optimized`,
          { method: 'POST', body: fd }
        );
        if (!resp.ok || !resp.body) {
          _hideOptCard();
          showToast(`Fehler bei ${f.name}: HTTP ${resp.status}`);
          continue;
        }
        const reader = resp.body.getReader();
        const dec = new TextDecoder();
        let buf = '';
        let done = false;
        while (!done) {
          const { value, done: d } = await reader.read();
          done = d;
          if (value) buf += dec.decode(value, { stream: true });
          const lines = buf.split('\n');
          buf = lines.pop();
          for (const line of lines) {
            if (!line.startsWith('data:')) continue;
            try {
              const ev = JSON.parse(line.slice(5).trim());
              if (ev.type === 'progress') {
                _updateOptCard(ev.step, ev.pct ?? 50);
              } else if (ev.type === 'done') {
                _updateOptCard(`✓ ${ev.n_chunks} Chunks gespeichert`, 100);
                setTimeout(_hideOptCard, 2000);
                showToast(`✓ ${f.name}: ${ev.n_chunks} Chunks (optimiert)`);
              } else if (ev.type === 'error') {
                _hideOptCard();
                showToast(`Fehler: ${ev.message}`);
              }
            } catch (_) {}
          }
        }
      } catch (e) {
        _hideOptCard();
        showToast(`Fehler bei ${f.name}: ${e.message}`);
      }
    }
    loadCollections();
  }

  // Wiederverwendbarer Auswahl-Dialog: gibt eine collection-ID zurück (oder null)
  function pickCollection(info) {
    return new Promise(async (resolve) => {
      if (!_collections.length) await loadCollections();
      if (!_collections.length) {
        showToast('Lege zuerst eine Wissensdatenbank an (Tab 📚 RAG)');
        resolve(null);
        return;
      }
      const overlay = document.getElementById('rag-pick-overlay');
      const sel = document.getElementById('rag-pick-select');
      sel.innerHTML = '';
      for (const c of _collections) {
        const o = document.createElement('option');
        o.value = c.id; o.textContent = c.name;
        sel.appendChild(o);
      }
      document.getElementById('rag-pick-info').textContent = info || '';
      overlay.classList.add('active');
      const ok = document.getElementById('btn-rag-pick-ok');
      const cancel = document.getElementById('btn-rag-pick-cancel');
      const close = (val) => {
        overlay.classList.remove('active');
        ok.onclick = null; cancel.onclick = null;
        resolve(val);
      };
      ok.onclick = () => close(sel.value);
      cancel.onclick = () => close(null);
    });
  }

  // Beliebigen Text als Dokument in eine gewählte Wissensdatenbank übernehmen
  async function ingestText(title, text, info) {
    if (!text || !text.trim()) { showToast('Kein Inhalt zum Übernehmen'); return; }
    const cid = await pickCollection(info || `„${title}" übernehmen`);
    if (!cid) return;
    showToast('⏳ Wird eingebettet…');
    try {
      const r = await fetch(`/api/rag/collections/${cid}/from-text`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ title, text }),
      });
      const data = await r.json();
      if (!r.ok) throw new Error(data.detail || r.status);
      showToast(`✓ Übernommen: ${data.n_chunks} Chunks`);
      loadCollections();
    } catch (e) { showToast('Fehler: ' + e.message); }
  }

  // Vom Chat genutzt: aktive Sammlungs-IDs (leer, wenn Umschalter aus)
  function selectedCollections() {
    const btn = document.getElementById('btn-rag-toggle');
    if (!btn || !btn.classList.contains('active')) return [];
    return Array.from(document.querySelectorAll('#rag-chat-panel input[type="checkbox"]:checked'))
      .map(cb => cb.value);
  }

  /* Ziehbarer Trenner: Einstellungen ↔ vorhandene Wissensdatenbanken */
  const _SPLIT_KEY = 'rag_left_width';
  function _initSplitter() {
    const splitter = document.getElementById('rag-splitter');
    const left = document.getElementById('rag-left');
    const body = document.getElementById('rag-body');
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

  // Hilfe-Wissensdatenbank + Hilfe-Agent aus der mitgelieferten Doku bauen
  async function _buildHelp() {
    const btn = document.getElementById('btn-rag-help-build');
    if (btn) { btn.disabled = true; btn.textContent = '… wird erstellt'; }
    try {
      const resp = await fetch('/api/help/build', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' });
      if (!resp.ok) throw new Error((await resp.json()).detail || resp.statusText);
      const d = await resp.json();
      if (typeof showToast === 'function') showToast(`✓ Hilfe bereit: ${d.docs} Doku-Dateien · Agent „${d.agent_name}" (im Chat: /Hilfe)`);
      loadCollections();
      if (typeof AgentManager !== 'undefined' && AgentManager.load) AgentManager.load();
    } catch (e) {
      if (typeof showToast === 'function') showToast('Fehler: ' + e.message);
    } finally {
      if (btn) { btn.disabled = false; btn.textContent = 'Hilfe-Wissensdatenbank erstellen/aktualisieren'; }
    }
  }

  function init() {
    _loadEmbedModel();
    _updateSliderLabels();
    _initSplitter();
    loadCollections();
    _fillConvSelect();
    document.getElementById('rag-speed').addEventListener('input', _updateSliderLabels);
    document.getElementById('rag-strict').addEventListener('input', _updateSliderLabels);
    document.getElementById('btn-rag-create').addEventListener('click', _create);
    document.getElementById('btn-rag-from-chat').addEventListener('click', _importFromChat);
    document.getElementById('btn-rag-help-build')?.addEventListener('click', _buildHelp);
    // Beim Öffnen des RAG-Tabs die Gesprächsliste auffrischen
    document.querySelector('.tab-btn[data-tab="rag"]')?.addEventListener('click', _fillConvSelect);
    document.getElementById('rag-file-input').addEventListener('change', e => {
      if (!e.target.files.length) return;
      const files = Array.from(e.target.files);
      e.target.value = '';
      if (_ragOptimize) _uploadFilesOptimized(files);
      else _uploadFiles(files);
    });

    // Optimize-Toggle in die RAG-Konfigurationsleiste einfügen
    const cleanChk = document.getElementById('rag-clean');
    const cleanLabel = cleanChk ? cleanChk.closest('label') : null;
    const optLabel = document.createElement('label');
    optLabel.style.cssText = 'display:inline-flex;align-items:center;gap:6px;cursor:pointer;font-size:13px';
    optLabel.innerHTML = `<input type="checkbox" id="rag-optimize"${_ragOptimize ? ' checked' : ''} /> KI-Optimierung`;
    optLabel.title = 'Dokument vor dem Einbetten per LLM für semantische Suche aufbereiten (langsamer, bessere Trefferqualität)';
    if (cleanLabel && cleanLabel.parentElement) {
      cleanLabel.parentElement.insertBefore(optLabel, cleanLabel.nextSibling);
    }
    document.getElementById('rag-optimize')?.addEventListener('change', e => {
      _ragOptimize = e.target.checked;
      localStorage.setItem('rag_optimize', _ragOptimize ? '1' : '0');
    });
    document.getElementById('btn-rag-clone-server')?.addEventListener('click', _openCloneModal);
    document.getElementById('btn-rag-clone-scan')?.addEventListener('click', _scanServerDir);
    document.getElementById('btn-rag-clone-close')?.addEventListener('click', () =>
      document.getElementById('rag-clone-overlay')?.classList.remove('active'));
    // Chat-Umschalter: Custom-Dropdown anzeigen
    const toggle = document.getElementById('btn-rag-toggle');
    const chatPanel = document.getElementById('rag-chat-panel');
    toggle.addEventListener('click', () => {
      const nowActive = !toggle.classList.contains('active');
      toggle.classList.toggle('active', nowActive);
      chatPanel.style.display = nowActive ? '' : 'none';
      if (nowActive) _fillChatSelect();
    });
    // Panel schließen bei Klick außerhalb
    document.addEventListener('click', e => {
      const wrap = document.getElementById('rag-chat-wrap');
      if (wrap && !wrap.contains(e.target) && chatPanel.style.display !== 'none') {
        chatPanel.style.display = 'none';
        toggle.classList.remove('active');
      }
    });
  }

  return { init, loadCollections, selectedCollections, pickCollection, ingestText };
})();
