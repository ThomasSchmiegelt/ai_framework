/* AI_Framework_Thomas — RAG / Wissenssammlungen
   Sammlungen anlegen, Dokumente hochladen (Embeddings via Ollama, Speicher in
   SQLite). Im Chat wählbar über den 📚-Umschalter. */

const RAG = (() => {
  let _collections = [];
  let _uploadTargetId = null;

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
    const sel = document.getElementById('rag-chat-select');
    if (!sel) return;
    const prev = new Set(Array.from(sel.selectedOptions).map(o => o.value));
    sel.innerHTML = '';
    for (const c of _collections) {
      const o = document.createElement('option');
      o.value = c.id;
      o.textContent = `${c.name} (${c.n_chunks})`;
      if (prev.has(c.id)) o.selected = true;
      sel.appendChild(o);
    }
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
        <div style="display:flex;justify-content:space-between;align-items:center;font-size:12.5px;padding:3px 0">
          <span>📄 ${escHtml(d.filename)} <span class="planner-muted">· ${d.n_chunks} Chunks</span></span>
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
        <div style="display:flex;gap:8px">
          <button class="export-btn rag-add-doc" data-id="${c.id}">＋ Dokument(e) hinzufügen</button>
          <button class="export-btn rag-del-coll" data-id="${c.id}">🗑 Datenbank löschen</button>
        </div>`;
      wrap.appendChild(card);
    }
    wrap.querySelectorAll('.rag-add-doc').forEach(b =>
      b.addEventListener('click', () => { _uploadTargetId = b.dataset.id; document.getElementById('rag-file-input').click(); }));
    wrap.querySelectorAll('.rag-del-coll').forEach(b =>
      b.addEventListener('click', () => _deleteCollection(b.dataset.id)));
    wrap.querySelectorAll('.rag-del-doc').forEach(b =>
      b.addEventListener('click', () => _deleteDoc(b.dataset.id)));
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
    const sel = document.getElementById('rag-chat-select');
    return Array.from(sel.selectedOptions).map(o => o.value);
  }

  function init() {
    _loadEmbedModel();
    _updateSliderLabels();
    loadCollections();
    _fillConvSelect();
    document.getElementById('rag-speed').addEventListener('input', _updateSliderLabels);
    document.getElementById('rag-strict').addEventListener('input', _updateSliderLabels);
    document.getElementById('btn-rag-create').addEventListener('click', _create);
    document.getElementById('btn-rag-from-chat').addEventListener('click', _importFromChat);
    // Beim Öffnen des RAG-Tabs die Gesprächsliste auffrischen
    document.querySelector('.tab-btn[data-tab="rag"]')?.addEventListener('click', _fillConvSelect);
    document.getElementById('rag-file-input').addEventListener('change', e => {
      if (e.target.files.length) _uploadFiles(Array.from(e.target.files));
      e.target.value = '';
    });
    // Chat-Umschalter: Sammlungswahl ein-/ausblenden
    const toggle = document.getElementById('btn-rag-toggle');
    const chatSel = document.getElementById('rag-chat-select');
    toggle.addEventListener('click', () => {
      toggle.classList.toggle('active');
      chatSel.style.display = toggle.classList.contains('active') ? '' : 'none';
      if (toggle.classList.contains('active')) _fillChatSelect();
    });
  }

  return { init, loadCollections, selectedCollections, pickCollection, ingestText };
})();
