/* AI_Framework_Thomas — Mail → Wissensdatenbank
   IMAP read-only: Mails auflisten und ausgewählte direkt in eine RAG-Sammlung
   übernehmen. Zugang in data/mail.json (Backend). Keine Auto-Aktionen. */

const Mail = (() => {
  let _messages = [];
  let _filtered = [];    // nach Filter (Absender/Betreff/Domäne) sichtbare Mails
  let _current = null;   // aktuell in der Vorschau geöffnete Mail
  let _actions = [{}, {}, {}, {}];  // bis zu 4 Aktions-Slots
  let _agents = [];      // für Agent-Aktion
  let _abort = false;    // Abbruch der laufenden Aktions-Schleife

  const _domainOf = (addr) => {
    const m = String(addr || '').match(/[\w.+-]+@([\w-]+(?:\.[\w-]+)+)/);
    return m ? m[1].toLowerCase() : '';
  };

  // Notizen pro Mail lokal (uid) – „Markieren / Notiz"-Aktion
  const _NOTE_KEY = 'mail_notes';
  function _notes() { try { return JSON.parse(localStorage.getItem(_NOTE_KEY) || '{}'); } catch (_) { return {}; } }
  function _setNote(uid, txt) {
    const n = _notes(); if (txt) n[uid] = txt; else delete n[uid];
    localStorage.setItem(_NOTE_KEY, JSON.stringify(n));
  }

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
      // Passwort wird NICHT gespeichert – es gilt nur für die laufende Sitzung.
      if (st) st.textContent = c.has_password
        ? '🔓 Passwort für diese Sitzung aktiv'
        : '🔒 Passwort für diese Sitzung eingeben (wird nicht gespeichert)';
      // Einstellungen aufklappen, wenn Server/Benutzer fehlen ODER das Passwort
      // für diese Sitzung noch nicht eingegeben wurde.
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

  let _colls = [];   // RAG-Sammlungen (Cache für Aktions-Slots)

  async function _loadRag() {
    let colls = [];
    try { colls = await (await fetch('/api/rag/collections')).json(); } catch (_) {}
    _colls = colls;
    _renderActions();   // Slot-RAG-Auswahl aktualisieren
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

  function _matchFilter(m) {
    const f = (document.getElementById('mail-f-from')?.value || '').trim().toLowerCase();
    const s = (document.getElementById('mail-f-subject')?.value || '').trim().toLowerCase();
    const d = (document.getElementById('mail-f-domain')?.value || '').trim().toLowerCase();
    if (f && !String(m.from || '').toLowerCase().includes(f)) return false;
    if (s && !String(m.subject || '').toLowerCase().includes(s)) return false;
    if (d && !_domainOf(m.from).includes(d)) return false;
    return true;
  }

  function _render() {
    const list = document.getElementById('mail-list');
    if (!_messages.length) {
      _filtered = [];
      list.innerHTML = '<p class="planner-muted" style="font-size:13px">Keine Mails — Zugang prüfen oder Suche anpassen.</p>';
      return;
    }
    _filtered = _messages.filter(_matchFilter);
    const notes = _notes();
    const rows = _filtered.map(m => {
      const note = notes[m.uid];
      return `
      <div class="mail-row" style="display:flex;gap:10px;align-items:flex-start;padding:7px 6px;border-bottom:1px solid var(--border)">
        <input type="checkbox" class="mail-check" data-uid="${escHtml(m.uid)}" style="margin-top:3px;cursor:pointer" />
        <span class="mail-open" data-uid="${escHtml(m.uid)}" style="flex:1;min-width:0;cursor:pointer" title="Anklicken zum Ansehen/Bearbeiten">
          <span style="font-weight:600">${escHtml(m.subject)}</span>${note ? ' <span title="' + escHtml(note) + '" style="cursor:help">🏷</span>' : ''}<br>
          <span class="planner-muted" style="font-size:12px">${escHtml(m.from)} · ${escHtml(m.date)}</span>
        </span>
      </div>`; }).join('');
    list.innerHTML = `
      <div style="display:flex;gap:8px;align-items:center;margin-bottom:6px">
        <button id="btn-mail-all" class="export-btn" style="font-size:12px">Alle aus-/abwählen</button>
        <span class="planner-muted" style="font-size:12px">${_filtered.length}/${_messages.length} Mail(s) — anklicken = Vorschau, Haken = Aktionen/Sammelübernahme</span>
      </div>${rows || '<p class="planner-muted" style="font-size:13px">Kein Treffer für den Filter.</p>'}`;
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

  /* ── Agenten (für Agent-Aktion) ──────────────────────────────────── */
  async function _loadAgents() {
    try {
      const all = await (await fetch('/api/agents')).json();
      _agents = (all || []).filter(a => a.favorite);   // nur Favoriten-Agenten
    } catch (_) { _agents = []; }
    _renderActions();
  }

  /* ── Aktions-Slots (max. 4) ──────────────────────────────────────── */
  function _ragOptions(sel) {
    if (!_colls.length) return '<option value="">— keine Wissensdatenbank —</option>';
    return _colls.map(c => `<option value="${escHtml(c.id)}"${c.id === sel ? ' selected' : ''}>${escHtml(c.name)} (${c.n_chunks})</option>`).join('');
  }
  function _agentOptions(sel) {
    return '<option value="">— Standard-Assistent —</option>' +
      _agents.map(a => `<option value="${escHtml(a.id)}"${a.id === sel ? ' selected' : ''}>${escHtml((a.icon || '🤖') + ' ' + a.name)}</option>`).join('');
  }

  function _slotParams(i, a) {
    if (a.type === 'rag') {
      return `<select class="sidebar-select mail-act-rag" data-i="${i}" style="min-width:180px">${_ragOptions(a.collection_id)}</select>
        <label style="font-size:12px;display:inline-flex;align-items:center;gap:5px;cursor:pointer"><input type="checkbox" class="mail-act-clean" data-i="${i}" ${a.clean !== false ? 'checked' : ''}/> bereinigen</label>`;
    }
    if (a.type === 'agent') {
      return `<select class="sidebar-select mail-act-agent" data-i="${i}" style="min-width:170px">${_agentOptions(a.agent_id)}</select>
        <input type="text" class="sidebar-select mail-act-instr" data-i="${i}" placeholder="Auftrag, z. B. höfliche Antwort entwerfen" value="${escHtml(a.instruction || '')}" style="flex:1;min-width:200px" />`;
    }
    if (a.type === 'note') {
      return `<input type="text" class="sidebar-select mail-act-note" data-i="${i}" placeholder="Notiz/Markierung…" value="${escHtml(a.note || '')}" style="flex:1;min-width:220px" />`;
    }
    if (a.type === 'doc') {
      return `<span class="planner-muted" style="font-size:12px">Mail als Quellmaterial in den Dokumentengenerator</span>`;
    }
    return '';
  }

  function _renderActions() {
    const wrap = document.getElementById('mail-actions');
    if (!wrap) return;
    wrap.innerHTML = _actions.map((a, i) => `
      <div class="mail-action-slot" style="display:flex;gap:8px;align-items:center;flex-wrap:wrap;padding:6px 0;border-bottom:1px dashed var(--border)">
        <span class="planner-muted" style="font-size:12px;width:18px">${i + 1}.</span>
        <select class="sidebar-select mail-act-type" data-i="${i}" style="min-width:170px">
          <option value=""${!a.type ? ' selected' : ''}>— keine —</option>
          <option value="rag"${a.type === 'rag' ? ' selected' : ''}>📚 In RAG (bereinigt)</option>
          <option value="agent"${a.type === 'agent' ? ' selected' : ''}>🤖 Agent-Aufgabe</option>
          <option value="doc"${a.type === 'doc' ? ' selected' : ''}>📄 → Dokumentengenerator</option>
          <option value="note"${a.type === 'note' ? ' selected' : ''}>🏷 Markieren / Notiz</option>
        </select>
        ${_slotParams(i, a)}
      </div>`).join('');

    wrap.querySelectorAll('.mail-act-type').forEach(el => el.addEventListener('change', () => {
      const i = +el.dataset.i; _actions[i] = { type: el.value }; _renderActions();
    }));
    const bind = (cls, key, prop) => wrap.querySelectorAll(cls).forEach(el => {
      el.addEventListener(prop === 'checked' ? 'change' : 'input', () => {
        _actions[+el.dataset.i][key] = prop === 'checked' ? el.checked : el.value;
      });
    });
    bind('.mail-act-rag', 'collection_id', 'value');
    bind('.mail-act-clean', 'clean', 'checked');
    bind('.mail-act-agent', 'agent_id', 'value');
    bind('.mail-act-instr', 'instruction', 'value');
    bind('.mail-act-note', 'note', 'value');
  }

  /* ── Regeln (Filter + Aktions-Set speichern) ─────────────────────── */
  let _rules = [];
  async function _loadRules() {
    try { _rules = (await (await fetch('/api/mail/rules')).json()).rules || []; } catch (_) { _rules = []; }
    const sel = document.getElementById('mail-rule-select');
    if (!sel) return;
    const prev = sel.value;
    sel.innerHTML = '<option value="">— Regel laden —</option>' +
      _rules.map(r => `<option value="${escHtml(r.id)}">${escHtml(r.name)}</option>`).join('');
    if (prev) sel.value = prev;
  }
  function _currentFilter() {
    return {
      from: document.getElementById('mail-f-from').value.trim(),
      subject: document.getElementById('mail-f-subject').value.trim(),
      domain: document.getElementById('mail-f-domain').value.trim(),
    };
  }
  async function _saveRule() {
    const name = document.getElementById('mail-rule-name').value.trim()
      || document.getElementById('mail-rule-select').selectedOptions[0]?.textContent
      || 'Regel';
    const id = document.getElementById('mail-rule-select').value || undefined;
    const body = { id, name, filter: _currentFilter(), actions: _actions.filter(a => a.type) };
    try {
      const r = await fetch('/api/mail/rules', {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
      });
      if (!r.ok) throw new Error((await r.json()).detail || r.status);
      showToast('✓ Regel gespeichert');
      await _loadRules();
      document.getElementById('mail-rule-select').value = (await r.json()).rule.id;
    } catch (e) { showToast('Fehler: ' + e.message); }
  }
  async function _deleteRule() {
    const id = document.getElementById('mail-rule-select').value;
    if (!id) { showToast('Keine Regel gewählt'); return; }
    if (!confirm('Diese Regel löschen?')) return;
    try {
      await fetch('/api/mail/rules/' + encodeURIComponent(id), { method: 'DELETE' });
      showToast('✓ Regel gelöscht'); _loadRules();
    } catch (e) { showToast('Fehler: ' + e.message); }
  }
  function _applyRule() {
    const id = document.getElementById('mail-rule-select').value;
    const rule = _rules.find(r => r.id === id);
    if (!rule) return;
    const f = rule.filter || {};
    document.getElementById('mail-f-from').value = f.from || '';
    document.getElementById('mail-f-subject').value = f.subject || '';
    document.getElementById('mail-f-domain').value = f.domain || '';
    document.getElementById('mail-rule-name').value = rule.name || '';
    _actions = [{}, {}, {}, {}];
    (rule.actions || []).slice(0, 4).forEach((a, i) => { _actions[i] = { ...a }; });
    _renderActions();
    _render();
    showToast('Regel „' + (rule.name || '') + '" angewendet');
  }

  /* ── Aktionen ausführen (Versand stets manuell) ──────────────────── */
  function _resultCard(title, inner, badge) {
    const div = document.createElement('div');
    div.className = 'mail-result-card';
    div.innerHTML = `<div class="mail-result-head">${badge || ''} ${escHtml(title)}</div>${inner}`;
    document.getElementById('mail-results').appendChild(div);
    return div;
  }

  async function _execAction(uid, a, mailMeta) {
    if (a.type === 'rag') {
      if (!a.collection_id) { _resultCard('RAG übersprungen', '<div class="planner-muted">Keine Wissensdatenbank gewählt</div>'); return; }
      const r = await fetch('/api/mail/action/rag', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ uid, collection_id: a.collection_id, clean: a.clean !== false }),
      });
      const j = await r.json();
      if (!r.ok) throw new Error(j.detail || r.status);
      _resultCard('In Wissensdatenbank übernommen', `<div class="planner-muted">${j.chunks} Chunk(s)${a.clean !== false ? ', bereinigt' : ''}</div>`, '📚');
      if (typeof RAG !== 'undefined' && RAG.loadCollections) RAG.loadCollections();
    } else if (a.type === 'agent') {
      const r = await fetch('/api/mail/action/agent', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ uid, agent_id: a.agent_id, instruction: a.instruction || 'Fasse die Mail zusammen.' }),
      });
      const j = await r.json();
      if (!r.ok) throw new Error(j.detail || r.status);
      if (j.tokens && typeof TokenMeter !== 'undefined') TokenMeter.add(j.tokens, 'Mail');
      const card = _resultCard(`Agent-Ergebnis${a.instruction ? ': ' + a.instruction : ''}`, '', '🤖');
      const ta = document.createElement('textarea');
      ta.className = 'mail-result-text'; ta.value = j.text || '';
      const bar = document.createElement('div'); bar.className = 'mail-result-bar';
      const subj = mailMeta.subject || '';
      bar.innerHTML = `<button class="export-btn mail-r-copy">📋 Kopieren</button>
        <button class="export-btn mail-r-mailto">✉ Im Mailprogramm öffnen</button>
        <button class="export-btn mail-r-doc">📄 → Doku</button>`;
      card.appendChild(ta); card.appendChild(bar);
      bar.querySelector('.mail-r-copy').addEventListener('click', () => {
        navigator.clipboard.writeText(ta.value).then(() => showToast('✓ In Zwischenablage kopiert'));
      });
      bar.querySelector('.mail-r-mailto').addEventListener('click', () => {
        const to = _domainOf(mailMeta.from) ? (String(mailMeta.from).match(/[\w.+-]+@[\w.-]+/) || [''])[0] : '';
        const re = subj.toLowerCase().startsWith('re:') ? subj : 'Re: ' + subj;
        window.location.href = `mailto:${encodeURIComponent(to)}?subject=${encodeURIComponent(re)}&body=${encodeURIComponent(ta.value)}`;
      });
      bar.querySelector('.mail-r-doc').addEventListener('click', () => {
        if (typeof DocGen !== 'undefined' && DocGen.loadFromChat) DocGen.loadFromChat('Mail: ' + subj, ta.value);
        document.querySelector('.tab-btn[data-tab="docgen"]')?.click();
      });
    } else if (a.type === 'doc') {
      const m = await (await fetch('/api/mail/message', {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ uid }),
      })).json();
      const txt = `Von: ${m.from || ''}\nAn: ${m.to || ''}\nDatum: ${m.date || ''}\nBetreff: ${m.subject || ''}\n\n${m.text || ''}`.trim();
      if (typeof DocGen !== 'undefined' && DocGen.loadFromChat) DocGen.loadFromChat('Mail: ' + (m.subject || ''), txt);
      _resultCard('An Dokumentengenerator übergeben', '<div class="planner-muted">Im Tab „Dokumente" weiterbearbeiten</div>', '📄');
    } else if (a.type === 'note') {
      _setNote(uid, a.note || '🏷');
      _resultCard('Markiert / Notiz gesetzt', `<div class="planner-muted">${escHtml(a.note || '🏷')}</div>`, '🏷');
      _render();
    }
  }

  async function _runActions() {
    const acts = _actions.filter(a => a.type);
    if (!acts.length) { showToast('Bitte mindestens eine Aktion konfigurieren'); return; }
    const scopeAll = document.getElementById('mail-act-scope-all').checked;
    let targets;
    if (scopeAll) {
      targets = _filtered.slice();
    } else {
      const checked = Array.from(document.querySelectorAll('.mail-check')).filter(b => b.checked).map(b => b.dataset.uid);
      const uids = checked.length ? checked : (_current ? [_current.uid] : []);
      targets = _filtered.filter(m => uids.includes(String(m.uid)));
    }
    if (!targets.length) { showToast('Bitte Mail(s) auswählen (Haken) oder „auf alle gefilterten" aktivieren'); return; }
    if (targets.length > 5 && !confirm(`${targets.length} Mails × ${acts.length} Aktion(en) über das lokale Modell — das kann dauern. Fortfahren?`)) return;

    _abort = false;
    const status = document.getElementById('mail-run-status');
    const runBtn = document.getElementById('btn-mail-run');
    const abortBtn = document.getElementById('btn-mail-run-abort');
    runBtn.disabled = true; abortBtn.style.display = '';
    document.getElementById('mail-preview-empty').style.display = 'none';
    document.getElementById('mail-preview').style.display = '';
    document.getElementById('mail-results').innerHTML = '';

    let done = 0;
    outer:
    for (const m of targets) {
      _resultCard(`▼ ${m.subject || '(kein Betreff)'} — ${m.from || ''}`, '', '✉');
      for (const a of acts) {
        if (_abort) break outer;
        status.textContent = `⏳ ${done + 1}/${targets.length * acts.length} …`;
        try { await _execAction(String(m.uid), a, m); }
        catch (e) { _resultCard('Fehler', `<div class="planner-muted">${escHtml(e.message)}</div>`, '⚠️'); }
        done++;
      }
    }
    status.textContent = _abort ? `⏹ abgebrochen (${done} erledigt)` : `✓ fertig (${done} Aktion(en))`;
    runBtn.disabled = false; abortBtn.style.display = 'none';
    if (typeof RAG !== 'undefined' && RAG.loadCollections) RAG.loadCollections();
    _loadRag();
  }

  function refresh() { _loadConfig(); _loadRag(); _loadAgents(); _loadRules(); _renderActions(); }

  function init() {
    document.getElementById('btn-mail-save')?.addEventListener('click', _saveConfig);
    document.getElementById('mail-protocol')?.addEventListener('change', _onProtocolChange);
    document.getElementById('mail-ssl')?.addEventListener('change', _onProtocolChange);
    document.getElementById('btn-mail-fetch')?.addEventListener('click', _fetch);
    document.getElementById('btn-mail-to-rag')?.addEventListener('click', _toRag);
    document.getElementById('btn-mail-preview-rag')?.addEventListener('click', _previewToRag);
    document.getElementById('btn-mail-preview-doc')?.addEventListener('click', _previewToDoc);
    // Filter (live)
    ['mail-f-from', 'mail-f-subject', 'mail-f-domain'].forEach(id =>
      document.getElementById(id)?.addEventListener('input', _render));
    // Regeln
    document.getElementById('mail-rule-select')?.addEventListener('change', _applyRule);
    document.getElementById('btn-mail-rule-save')?.addEventListener('click', _saveRule);
    document.getElementById('btn-mail-rule-del')?.addEventListener('click', _deleteRule);
    // Aktionen
    document.getElementById('btn-mail-run')?.addEventListener('click', _runActions);
    document.getElementById('btn-mail-run-abort')?.addEventListener('click', () => { _abort = true; });
    document.querySelector('.tab-btn[data-tab="mail"]')?.addEventListener('click', refresh);
    _initSplitter();
    _renderActions();
    refresh();
  }

  return { init, refresh };
})();
