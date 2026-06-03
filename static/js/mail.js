/* AI_Framework_Thomas — Mail → Wissensdatenbank
   IMAP read-only: Mails auflisten und ausgewählte direkt in eine RAG-Sammlung
   übernehmen. Zugang in data/mail.json (Backend). Keine Auto-Aktionen. */

const Mail = (() => {
  let _messages = [];
  let _current = null;   // aktuell in der Vorschau geöffnete Mail

  async function _loadConfig() {
    try {
      const c = await (await fetch('/api/mail/config')).json();
      const proto = document.getElementById('mail-protocol');
      if (proto) proto.value = c.protocol === 'pop3' ? 'pop3' : 'imap';
      document.getElementById('mail-host').value = c.host || '';
      document.getElementById('mail-port').value = c.port || 993;
      document.getElementById('mail-user').value = c.user || '';
      document.getElementById('mail-ssl').checked = c.ssl !== false;
      const st = document.getElementById('mail-cfg-status');
      if (st) st.textContent = c.has_password ? '✓ Zugang gespeichert' : '';
      // Einstellungen aufklappen, wenn noch kein Zugang hinterlegt ist
      const det = document.getElementById('mail-settings');
      if (det && !(c.host && c.user && c.has_password)) det.open = true;
    } catch (_) {}
  }

  // Beim Protokoll-/SSL-Wechsel sinnvollen Standard-Port und Platzhalter setzen,
  // sofern der Nutzer noch keinen abweichenden Wert eingetragen hat.
  function _onProtocolChange() {
    const proto = document.getElementById('mail-protocol').value;
    const ssl = document.getElementById('mail-ssl').checked;
    const portEl = document.getElementById('mail-port');
    const defaults = { imap: ssl ? 993 : 143, pop3: ssl ? 995 : 110 };
    const known = [993, 143, 995, 110];
    const cur = parseInt(portEl.value, 10);
    if (!cur || known.includes(cur)) portEl.value = defaults[proto];
    document.getElementById('mail-host').placeholder =
      proto === 'pop3' ? 'pop.gmail.com' : 'imap.gmail.com';
  }

  async function _saveConfig() {
    const body = {
      protocol: document.getElementById('mail-protocol').value,
      host: document.getElementById('mail-host').value.trim(),
      port: parseInt(document.getElementById('mail-port').value, 10) || 993,
      user: document.getElementById('mail-user').value.trim(),
      ssl: document.getElementById('mail-ssl').checked,
      password: document.getElementById('mail-pass').value,   // leer = unverändert
    };
    try {
      const r = await fetch('/api/mail/config', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      if (!r.ok) throw new Error((await r.json()).detail || r.status);
      document.getElementById('mail-pass').value = '';
      showToast('✓ Mail-Zugang gespeichert');
      _loadConfig();
    } catch (e) { showToast('Fehler: ' + e.message); }
  }

  async function _loadRag() {
    let colls = [];
    try { colls = await (await fetch('/api/rag/collections')).json(); } catch (_) {}
    for (const id of ['mail-rag', 'mail-preview-rag']) {
      const sel = document.getElementById(id);
      if (!sel) continue;
      const prev = sel.value;
      sel.innerHTML = colls.length
        ? '' : '<option value="">— keine Wissensdatenbank vorhanden —</option>';
      for (const c of colls) {
        const o = document.createElement('option');
        o.value = c.id; o.textContent = `${c.name} (${c.n_chunks})`;
        sel.appendChild(o);
      }
      if (prev) sel.value = prev;
    }
  }

  async function _fetch() {
    const status = document.getElementById('mail-status');
    const list = document.getElementById('mail-list');
    status.textContent = '⏳ Rufe Postfach ab…';
    list.innerHTML = '';
    const body = {
      limit: parseInt(document.getElementById('mail-limit').value, 10) || 25,
      search: document.getElementById('mail-search').value.trim(),
    };
    try {
      const r = await fetch('/api/mail/list', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      if (!r.ok) throw new Error((await r.json()).detail || r.status);
      _messages = (await r.json()).messages || [];
      status.textContent = `${_messages.length} Mail(s)`;
      _render();
    } catch (e) {
      status.textContent = '';
      showToast('Abruf fehlgeschlagen: ' + e.message);
    }
  }

  function _render() {
    const list = document.getElementById('mail-list');
    if (!_messages.length) {
      list.innerHTML = '<p class="planner-muted" style="font-size:13px">Keine Mails — Zugang prüfen oder Suche anpassen.</p>';
      return;
    }
    const rows = _messages.map(m => `
      <div class="mail-row" style="display:flex;gap:10px;align-items:flex-start;padding:7px 6px;border-bottom:1px solid var(--border)">
        <input type="checkbox" class="mail-check" data-uid="${escHtml(m.uid)}" style="margin-top:3px;cursor:pointer" />
        <span class="mail-open" data-uid="${escHtml(m.uid)}" style="flex:1;min-width:0;cursor:pointer" title="Anklicken zum Ansehen/Bearbeiten">
          <span style="font-weight:600">${escHtml(m.subject)}</span><br>
          <span class="planner-muted" style="font-size:12px">${escHtml(m.from)} · ${escHtml(m.date)}</span>
        </span>
      </div>`).join('');
    list.innerHTML = `
      <div style="display:flex;gap:8px;align-items:center;margin-bottom:6px">
        <button id="btn-mail-all" class="export-btn" style="font-size:12px">Alle aus-/abwählen</button>
        <span class="planner-muted" style="font-size:12px">${_messages.length} Mail(s) — anklicken = Vorschau, Haken = Sammelübernahme</span>
      </div>${rows}`;
    document.getElementById('btn-mail-all')?.addEventListener('click', () => {
      const boxes = list.querySelectorAll('.mail-check');
      const anyOff = Array.from(boxes).some(b => !b.checked);
      boxes.forEach(b => { b.checked = anyOff; });
    });
    list.querySelectorAll('.mail-open').forEach(el => {
      el.addEventListener('click', () => _openMessage(el.dataset.uid));
    });
  }

  // Eine Mail vollständig laden und im rechten Bereich anzeigen/bearbeiten
  async function _openMessage(uid) {
    const head = document.getElementById('mail-preview-head');
    const body = document.getElementById('mail-preview-body');
    document.getElementById('mail-preview-empty').style.display = 'none';
    document.getElementById('mail-preview').style.display = '';
    head.innerHTML = '<span class="planner-muted">⏳ Mail wird geladen…</span>';
    body.value = '';
    try {
      const m = await (await fetch('/api/mail/message', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ uid }),
      })).json();
      _current = m;
      head.innerHTML =
        `<span class="mail-subj">${escHtml(m.subject || '(kein Betreff)')}</span>` +
        `<span class="planner-muted">Von: ${escHtml(m.from || '')}<br>` +
        (m.to ? `An: ${escHtml(m.to)}<br>` : '') +
        `Datum: ${escHtml(m.date || '')}</span>`;
      body.value = m.text || '';
    } catch (e) {
      head.innerHTML = '<span class="planner-muted">Mail konnte nicht geladen werden</span>';
    }
  }

  function _previewText() {
    const m = _current || {};
    const body = document.getElementById('mail-preview-body').value;
    return `Von: ${m.from || ''}\nAn: ${m.to || ''}\nDatum: ${m.date || ''}\n` +
           `Betreff: ${m.subject || ''}\n\n${body}`.trim();
  }

  // Bearbeiteten Mail-Text in die gewählte Wissensdatenbank übernehmen
  async function _previewToRag() {
    const cid = document.getElementById('mail-preview-rag').value;
    if (!cid) { showToast('Bitte eine Wissensdatenbank wählen'); return; }
    const text = document.getElementById('mail-preview-body').value.trim();
    if (!text) { showToast('Kein Text vorhanden'); return; }
    const status = document.getElementById('mail-preview-status');
    status.textContent = '⏳ Übernehme…';
    try {
      const r = await fetch(`/api/rag/collections/${encodeURIComponent(cid)}/from-text`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text: _previewText(), title: `Mail: ${(_current || {}).subject || 'Mail'}` }),
      });
      if (!r.ok) throw new Error((await r.json()).detail || r.status);
      status.textContent = '✓ In Wissensdatenbank übernommen';
      showToast('✓ Mail in die Wissensdatenbank übernommen');
      if (typeof RAG !== 'undefined' && RAG.loadCollections) RAG.loadCollections();
      _loadRag();
    } catch (e) {
      status.textContent = '';
      showToast('Übernahme fehlgeschlagen: ' + e.message);
    }
  }

  // Bearbeiteten Mail-Text als Quellmaterial in den Dokumentengenerator geben
  function _previewToDoc() {
    const text = document.getElementById('mail-preview-body').value.trim();
    if (!text) { showToast('Kein Text vorhanden'); return; }
    if (typeof DocGen !== 'undefined' && DocGen.loadFromChat) {
      DocGen.loadFromChat(`Mail: ${(_current || {}).subject || 'Mail'}`, _previewText());
    }
    document.querySelector('.tab-btn[data-tab="docgen"]')?.click();
  }

  /* Ziehbarer Trenner: Liste ↔ Vorschau */
  const _SPLIT_KEY = 'mail_left_width';
  function _initSplitter() {
    const splitter = document.getElementById('mail-splitter');
    const left = document.getElementById('mail-left');
    const body = document.getElementById('mail-body');
    if (!splitter || !left || !body) return;
    const saved = parseInt(localStorage.getItem(_SPLIT_KEY) || '', 10);
    if (saved > 0) left.style.width = saved + 'px';
    const _apply = (clientX) => {
      const rect = body.getBoundingClientRect();
      let w = Math.max(340, Math.min(clientX - rect.left, rect.width - 280));
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
      left.style.width = ''; localStorage.removeItem(_SPLIT_KEY);
    });
  }

  async function _toRag() {
    const cid = document.getElementById('mail-rag').value;
    if (!cid) { showToast('Bitte eine Wissensdatenbank wählen (im RAG-Tab anlegen)'); return; }
    const uids = Array.from(document.querySelectorAll('.mail-check'))
      .filter(b => b.checked).map(b => b.dataset.uid);
    if (!uids.length) { showToast('Bitte mindestens eine Mail auswählen'); return; }
    const status = document.getElementById('mail-status');
    status.textContent = '⏳ Übernehme in Wissensdatenbank…';
    try {
      const r = await fetch('/api/mail/to-rag', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ collection_id: cid, uids }),
      });
      if (!r.ok) throw new Error((await r.json()).detail || r.status);
      const res = await r.json();
      status.textContent = `✓ ${res.ingested} Mail(s), ${res.chunks} Chunks übernommen`;
      showToast(`✓ ${res.ingested} Mail(s) in die Wissensdatenbank übernommen`);
      if (typeof RAG !== 'undefined' && RAG.loadCollections) RAG.loadCollections();
      _loadRag();   // Chunk-Zahlen im Mail-Select aktualisieren
    } catch (e) {
      status.textContent = '';
      showToast('Übernahme fehlgeschlagen: ' + e.message);
    }
  }

  function refresh() { _loadConfig(); _loadRag(); }

  function init() {
    document.getElementById('btn-mail-save')?.addEventListener('click', _saveConfig);
    document.getElementById('mail-protocol')?.addEventListener('change', _onProtocolChange);
    document.getElementById('mail-ssl')?.addEventListener('change', _onProtocolChange);
    document.getElementById('btn-mail-fetch')?.addEventListener('click', _fetch);
    document.getElementById('btn-mail-to-rag')?.addEventListener('click', _toRag);
    document.getElementById('btn-mail-preview-rag')?.addEventListener('click', _previewToRag);
    document.getElementById('btn-mail-preview-doc')?.addEventListener('click', _previewToDoc);
    document.querySelector('.tab-btn[data-tab="mail"]')?.addEventListener('click', refresh);
    _initSplitter();
    refresh();
  }

  return { init, refresh };
})();
