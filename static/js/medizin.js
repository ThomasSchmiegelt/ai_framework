/* AI_Framework_Thomas — Medizin-Tab (eigenständiger Chat + Patienten-RAG) */

const MedizinChat = (() => {
  let _streaming    = false;
  let _attachedFiles = [];   // [{id, filename, is_image, src?}]
  let _patientRags  = [];    // Gefilterte RAG-Sammlungen (Name beginnt mit "Patient:")
  let _history      = [];    // Gesprächsverlauf [{role, content, files?}]
  let _expertMode   = true;  // 2-Modell-Pipeline (Ministral ↔ MedGemma) aktiv?
  let _round        = 0;     // aktuelle Rückfrage-Runde (0 = neue Konsultation)

  // ── Modell ──────────────────────────────────────────────────────────────

  // Im Medizin-Tab dürfen NUR medizinische Modelle (medgemma-Familie, z. B.
  // medgemma:4b / medgemma:27b) als Analyse-Modell gewählt werden – keine
  // allgemeinen Chat-Modelle.
  // Nur das offizielle MedGemma (medgemma:4b / medgemma:27b o. ä.) zulassen –
  // keine allgemeinen Chat-Modelle und keine Community-Ports.
  function _isMedModel(name) { return /^medgemma:/i.test(name || ''); }

  // ── Patienten-RAG ────────────────────────────────────────────────────────

  async function _loadPatientRags() {
    try {
      const resp = await fetch('/api/rag/collections');
      const all  = await resp.json();
      _patientRags = all.filter(c => c.name && c.name.startsWith('Patient:'));
    } catch (_) { _patientRags = []; }

    const sel = document.getElementById('medizin-rag-select');
    if (!sel) return;
    const prev = sel.value;
    sel.innerHTML = '<option value="">— Allgemein (keine Akte) —</option>';
    for (const c of _patientRags) {
      const opt = document.createElement('option');
      opt.value = c.id;
      opt.textContent = c.name;
      sel.appendChild(opt);
    }
    if (prev && Array.from(sel.options).some(o => o.value === prev)) sel.value = prev;
    _onRagChange();
  }

  function _onRagChange() {
    const sel  = document.getElementById('medizin-rag-select');
    const btn  = document.getElementById('btn-medizin-doc-to-rag');
    const cnt  = document.getElementById('medizin-rag-doc-count');
    const cid  = sel?.value || '';
    if (btn)  btn.style.display  = cid ? '' : 'none';
    if (cnt)  cnt.style.display  = cid ? '' : 'none';
    if (cid) _updateRagDocCount(cid);
  }

  async function _updateRagDocCount(cid) {
    const cnt = document.getElementById('medizin-rag-doc-count');
    if (!cnt) return;
    try {
      const resp = await fetch(`/api/rag/collections/${cid}/documents`);
      const docs = await resp.json();
      cnt.textContent = `${docs.length} Dokument(e) in der Akte`;
    } catch (_) { cnt.textContent = ''; }
  }

  function _showNewRagForm() {
    const nameInput = document.getElementById('medizin-new-rag-name');
    const btnNew    = document.getElementById('btn-medizin-new-rag');
    const btnOk     = document.getElementById('btn-medizin-new-rag-ok');
    const btnCancel = document.getElementById('btn-medizin-new-rag-cancel');
    nameInput.style.display = ''; nameInput.value = ''; nameInput.focus();
    btnNew.style.display    = 'none';
    btnOk.style.display     = '';
    btnCancel.style.display = '';
  }

  function _hideNewRagForm() {
    const nameInput = document.getElementById('medizin-new-rag-name');
    const btnNew    = document.getElementById('btn-medizin-new-rag');
    const btnOk     = document.getElementById('btn-medizin-new-rag-ok');
    const btnCancel = document.getElementById('btn-medizin-new-rag-cancel');
    nameInput.style.display = 'none';
    btnNew.style.display    = '';
    btnOk.style.display     = 'none';
    btnCancel.style.display = 'none';
  }

  async function _createPatientRag() {
    const nameInput = document.getElementById('medizin-new-rag-name');
    const name = (nameInput?.value || '').trim();
    if (!name) { _showToast('Bitte einen Namen eingeben'); return; }
    _hideNewRagForm();
    try {
      const resp = await fetch('/api/rag/collections', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name: `Patient: ${name}`, tier: 'ausgewogen', clean: true }),
      });
      if (!resp.ok) throw new Error('HTTP ' + resp.status);
      const coll = await resp.json();
      await _loadPatientRags();
      const sel = document.getElementById('medizin-rag-select');
      if (sel) { sel.value = coll.id; _onRagChange(); }
      _showToast(`Akte „${coll.name}" angelegt`);
    } catch (e) { _showToast('Fehler: ' + e.message); }
  }

  async function _uploadDocToRag(file) {
    const cid = document.getElementById('medizin-rag-select')?.value;
    if (!cid) { _showToast('Zuerst eine Patienten-Akte auswählen'); return; }
    _showToast(`⏳ „${file.name}" wird eingelesen…`);
    const fd = new FormData();
    fd.append('file', file);
    try {
      const resp = await fetch(`/api/rag/collections/${cid}/documents`, {
        method: 'POST', body: fd,
      });
      if (!resp.ok) throw new Error('HTTP ' + resp.status);
      _showToast(`✓ „${file.name}" in Akte eingelesen`);
      _updateRagDocCount(cid);
    } catch (e) { _showToast('Fehler: ' + e.message); }
  }

  // ── Chat-Dateianhang ──────────────────────────────────────────────────────

  async function _attachChatFile(file) {
    try {
      const fd = new FormData();
      fd.append('file', file);
      const resp = await fetch('/api/upload', { method: 'POST', body: fd });
      if (!resp.ok) throw new Error('HTTP ' + resp.status);
      const info = await resp.json();

      // Für Bilder: lokale Vorschau
      if (info.is_image) {
        const reader = new FileReader();
        reader.onload = e => {
          info.src = e.target.result;
          _attachedFiles.push(info);
          _renderAttachments();
        };
        reader.readAsDataURL(file);
      } else {
        _attachedFiles.push(info);
        _renderAttachments();
      }
    } catch (e) { _showToast('Upload fehlgeschlagen: ' + e.message); }
  }

  function _renderAttachments() {
    const box = document.getElementById('medizin-attachments');
    if (!box) return;
    box.innerHTML = '';
    if (!_attachedFiles.length) { box.style.display = 'none'; return; }
    box.style.display = 'flex';
    for (let i = 0; i < _attachedFiles.length; i++) {
      const f = _attachedFiles[i];
      const chip = document.createElement('div');
      chip.className = 'medizin-attach-chip';
      if (f.is_image && f.src) {
        chip.innerHTML = `<img src="${f.src}" alt="${f.filename}" />
          <span>${f.filename}</span>
          <button data-idx="${i}">✕</button>`;
      } else {
        chip.innerHTML = `<span class="medizin-file-icon">📄</span>
          <span>${f.filename}</span>
          <button data-idx="${i}">✕</button>`;
      }
      chip.querySelector('button').addEventListener('click', e => {
        _attachedFiles.splice(Number(e.target.dataset.idx), 1);
        _renderAttachments();
      });
      box.appendChild(chip);
    }
  }

  // ── Nachrichten-Rendering ─────────────────────────────────────────────────

  function _appendMsg(role, text) {
    const box = document.getElementById('medizin-messages');
    if (!box) return null;

    // Welcome-Screen beim ersten Senden entfernen
    box.querySelector('.medizin-welcome')?.remove();

    const row = document.createElement('div');
    row.className = `medizin-msg ${role}`;

    const bubble = document.createElement('div');
    bubble.className = 'medizin-bubble';

    // Bilder-Vorschau bei Nutzer-Nachrichten mit Anhängen
    if (role === 'user' && _attachedFiles.length > 0) {
      const imgs = _attachedFiles.filter(f => f.is_image && f.src);
      if (imgs.length > 0) {
        const imgRow = document.createElement('div');
        imgRow.className = 'medizin-msg-images';
        imgs.forEach(f => {
          const img = document.createElement('img');
          img.src = f.src; img.alt = f.filename;
          imgRow.appendChild(img);
        });
        bubble.appendChild(imgRow);
      }
      const docs = _attachedFiles.filter(f => !f.is_image);
      if (docs.length > 0) {
        const docRow = document.createElement('div');
        docRow.className = 'medizin-msg-docs';
        docs.forEach(f => {
          docRow.innerHTML += `<span class="medizin-file-icon">📄</span> ${escHtml(f.filename)}  `;
        });
        bubble.appendChild(docRow);
      }
    }

    const textDiv = document.createElement('div');
    textDiv.className = 'medizin-bubble-text';
    textDiv.textContent = text;
    bubble.appendChild(textDiv);
    row.appendChild(bubble);
    box.appendChild(row);
    box.scrollTop = box.scrollHeight;
    return textDiv;
  }

  function _renderMd(el, text) {
    if (!el) return;
    if (typeof marked !== 'undefined') {
      if (window._ensureKatexMarked) window._ensureKatexMarked();
      el.innerHTML = marked.parse(text, { gfm: true, breaks: true });
      el.querySelectorAll('a[href]').forEach(a => { a.target = '_blank'; a.rel = 'noopener noreferrer'; });
    } else {
      el.textContent = text;
    }
  }

  // ── Nachricht senden ──────────────────────────────────────────────────────

  // Router: Experten-Pipeline (2 Modelle) oder einfacher Direkt-Chat
  async function _sendMessage() {
    if (_streaming) return;
    if (_expertMode) return _sendPipeline();
    return _sendSimple();
  }

  async function _sendSimple() {
    if (_streaming) return;
    const input = document.getElementById('medizin-input');
    const text  = (input?.value || '').trim();
    if (!text && !_attachedFiles.length) return;

    const model   = (typeof Profile !== 'undefined' ? Profile.modelFor('medical') : '') || 'ministral-3:3b';
    const ragSel  = document.getElementById('medizin-rag-select')?.value || '';
    const fileIds = _attachedFiles.map(f => f.id);

    // Nutzer-Nachricht in Verlauf und UI aufnehmen
    const userMsg = { role: 'user', content: text || '(Anhang)', files: fileIds.length ? fileIds : undefined };
    _history.push(userMsg);
    _appendMsg('user', text || '(Anhang)');
    const assistantEl = _appendMsg('assistant', '');
    if (input) { input.value = ''; if (typeof autoResizeTextarea === 'function') autoResizeTextarea(input); }

    // Anhänge leeren
    _attachedFiles = [];
    _renderAttachments();

    _streaming = true;
    _setBtnState(true);

    // API-Payload: alle bisherigen Nachrichten senden (Kontexterhalt)
    const body = {
      messages:        _history.map(m => ({ role: m.role, content: m.content, files: m.files || [] })),
      model:           model,
      agent_id:        'medizin_assistent',
      rag_collections: ragSel ? [ragSel] : [],
      science:         false,
    };

    try {
      const resp = await fetch('/api/chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);

      const reader = resp.body.getReader();
      const dec = new TextDecoder();
      let buf = '', fullText = '';

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
            if (ev.type === 'text') {
              fullText += ev.content;
              if (assistantEl) assistantEl.textContent = fullText;
              document.getElementById('medizin-messages').scrollTop = 99999;
            } else if (ev.type === 'done') {
              _renderMd(assistantEl, fullText);
            } else if (ev.type === 'error') {
              if (assistantEl) assistantEl.textContent = 'Fehler: ' + (ev.content || 'Unbekannter Fehler');
            }
          } catch (_) {}
        }
      }
      if (fullText && assistantEl) _renderMd(assistantEl, fullText);
      // Assistent-Antwort in den Verlauf aufnehmen
      if (fullText) _history.push({ role: 'assistant', content: fullText });

    } catch (e) {
      if (assistantEl) assistantEl.textContent = 'Verbindungsfehler: ' + e.message;
    } finally {
      _streaming = false;
      _setBtnState(false);
    }
  }

  // ── Experten-Pipeline (2-Modell-Konsultation mit Rückfragen) ───────────────

  const _STAGE_META = {
    refine:    { icon: '🔧', label: 'Aufbereitung der Anfrage' },
    analyze:   { icon: '🔍', label: 'Analyse auf fehlende Angaben' },
    formulate: { icon: '✍️', label: 'Rückfrage wird formuliert' },
    final:     { icon: '🩺', label: 'Medizinische Einschätzung' },
  };

  // Aufklappbaren Zwischenschritt-Block anlegen/aktualisieren
  function _renderStage(container, ev) {
    const meta = _STAGE_META[ev.stage] || { icon: '⚙️', label: ev.stage };
    let det = container.querySelector(`details[data-stage="${ev.stage}"]`);
    if (ev.status === 'start') {
      if (!det) {
        det = document.createElement('details');
        det.className = 'medizin-stage';
        det.dataset.stage = ev.stage;
        det.innerHTML = `<summary><span class="medizin-stage-ico">${meta.icon}</span>
          <span class="medizin-stage-label">${meta.label}</span>
          <span class="medizin-stage-status">⏳ ${escHtml(ev.label || 'läuft…')}</span></summary>
          <div class="medizin-stage-body"></div>`;
        container.appendChild(det);
      }
    } else if (ev.status === 'done' && det) {
      const st = det.querySelector('.medizin-stage-status');
      if (st) st.textContent = '✓';
      const body = det.querySelector('.medizin-stage-body');
      if (body && ev.content) _renderMd(body, ev.content);
      else if (body && !ev.content) det.remove(); // leerer Schritt (z. B. formulate) → weg
    }
  }

  async function _sendPipeline() {
    if (_streaming) return;
    const input = document.getElementById('medizin-input');
    const text  = (input?.value || '').trim();
    if (!text) return;

    const ragSel = document.getElementById('medizin-rag-select')?.value || '';
    const modelMedical = (typeof Profile !== 'undefined' && Profile.modelFor)
      ? Profile.modelFor('medical') : '';
    const modelGeneral = (typeof Profile !== 'undefined' && Profile.modelFor)
      ? Profile.modelFor('general') : '';

    // Nutzer-Nachricht (sauber) in Verlauf + UI
    _history.push({ role: 'user', content: text });
    _appendMsg('user', text);
    if (input) { input.value = ''; if (typeof autoResizeTextarea === 'function') autoResizeTextarea(input); }

    // Pipeline-Container mit Zwischenschritt-Bereich + Ergebnis-Bereich
    const box = document.getElementById('medizin-messages');
    box.querySelector('.medizin-welcome')?.remove();
    const wrap = document.createElement('div');
    wrap.className = 'medizin-msg assistant';
    wrap.innerHTML = `<div class="medizin-bubble">
      <div class="medizin-pipeline-steps"></div>
      <div class="medizin-pipeline-out"></div>
    </div>`;
    box.appendChild(wrap);
    const stepsBox = wrap.querySelector('.medizin-pipeline-steps');
    const outBox   = wrap.querySelector('.medizin-pipeline-out');
    box.scrollTop = box.scrollHeight;

    _streaming = true; _setBtnState(true);

    const body = {
      messages:        _history.map(m => ({ role: m.role, content: m.content })),
      round:           _round,
      rag_collections: ragSel ? [ragSel] : [],
      model_general:   modelGeneral,
      model_medical:   modelMedical,
    };

    let finalText = '', questionText = '', isQuestion = false, nextRound = _round;
    try {
      const resp = await fetch('/api/medizin/consult', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);

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
          let ev; try { ev = JSON.parse(line.slice(6)); } catch (_) { continue; }
          if (ev.type === 'stage') {
            _renderStage(stepsBox, ev);
          } else if (ev.type === 'question') {
            isQuestion = true; questionText = ev.content || '';
            nextRound = ev.round || (_round + 1);
            outBox.classList.add('medizin-pipeline-question');
            _renderMd(outBox, questionText);
          } else if (ev.type === 'text') {
            finalText += ev.content;
            outBox.textContent = finalText;
          } else if (ev.type === 'error') {
            outBox.classList.add('medizin-pipeline-error');
            outBox.textContent = 'Fehler: ' + (ev.content || 'Unbekannter Fehler');
          } else if (ev.type === 'done') {
            if (typeof ev.round === 'number') nextRound = ev.round;
            if (ev.needs_followup === false) nextRound = 0;
          }
          box.scrollTop = box.scrollHeight;
        }
      }

      if (isQuestion) {
        // Rückfrage als Assistent-Turn in den Verlauf; auf Nutzerantwort warten
        _history.push({ role: 'assistant', content: questionText });
        _round = nextRound;
        if (input) input.placeholder = 'Antwort auf die Rückfrage eingeben…';
      } else if (finalText) {
        _renderMd(outBox, finalText);
        _history.push({ role: 'assistant', content: finalText });
        _round = 0;
        if (input) input.placeholder = 'Medizinische Frage eingeben… (Shift+Enter = Zeilenumbruch)';
        _offerTranslate(wrap, finalText);
      } else {
        _round = 0;
      }
    } catch (e) {
      outBox.classList.add('medizin-pipeline-error');
      outBox.textContent = 'Verbindungsfehler: ' + e.message;
      _round = 0;
    } finally {
      _streaming = false; _setBtnState(false);
    }
  }

  // „In einfaches Deutsch übersetzen"-Leiste unter eine Einschätzung hängen
  function _offerTranslate(wrap, text) {
    const bar = document.createElement('div');
    bar.className = 'medizin-translate-bar';
    bar.innerHTML = `<button class="export-btn">🗣 In einfaches Deutsch übersetzen</button>`;
    wrap.querySelector('.medizin-bubble').appendChild(bar);
    bar.querySelector('button').addEventListener('click', () => _translate(wrap, text, bar));
  }

  async function _translate(wrap, text, bar) {
    if (_streaming) return;
    _streaming = true; _setBtnState(true);
    bar.querySelector('button').disabled = true;
    const out = document.createElement('div');
    out.className = 'medizin-translation';
    out.innerHTML = `<div class="medizin-translation-head">🗣 Laienverständliche Fassung</div>
      <div class="medizin-translation-body"></div>`;
    wrap.querySelector('.medizin-bubble').appendChild(out);
    const bodyEl = out.querySelector('.medizin-translation-body');
    const modelGeneral = (typeof Profile !== 'undefined' && Profile.modelFor)
      ? Profile.modelFor('general') : '';
    let acc = '';
    try {
      const resp = await fetch('/api/medizin/translate', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text, model_general: modelGeneral }),
      });
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
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
          let ev; try { ev = JSON.parse(line.slice(6)); } catch (_) { continue; }
          if (ev.type === 'text') { acc += ev.content; bodyEl.textContent = acc; }
          else if (ev.type === 'error') bodyEl.textContent = 'Fehler: ' + (ev.content || '');
        }
        document.getElementById('medizin-messages').scrollTop = 99999;
      }
      if (acc) _renderMd(bodyEl, acc);
    } catch (e) {
      bodyEl.textContent = 'Übersetzung fehlgeschlagen: ' + e.message;
    } finally {
      _streaming = false; _setBtnState(false);
      bar.remove();
    }
  }

  function clearHistory() {
    _history = [];
    _round = 0;
    const inp = document.getElementById('medizin-input');
    if (inp) inp.placeholder = 'Medizinische Frage eingeben… (Shift+Enter = Zeilenumbruch)';
    const box = document.getElementById('medizin-messages');
    if (box) box.innerHTML = `<div class="medizin-welcome">
      <div style="font-size:36px;margin-bottom:12px">🩺</div>
      <div style="font-weight:600;font-size:15px;margin-bottom:6px">Medizin-Assistent</div>
      <div style="color:var(--text-muted);font-size:13px;max-width:400px;text-align:center">
        Stellen Sie medizinische Fragen, laden Sie Befunde oder Bilder hoch.<br>
        Legen Sie eine Patienten-Akte an, um Dokumente dauerhaft zu verwalten.
      </div>
      <div class="medizin-disclaimer" style="margin-top:14px;max-width:480px;text-align:left">
        <strong>⚠️ Hinweis:</strong> Dieser Assistent ersetzt keine ärztliche Beratung, Diagnose oder Behandlung.
        Bei Notfällen: Notruf <strong>112</strong> anrufen.
      </div>
    </div>`;
  }

  function _setBtnState(busy) {
    const btn = document.getElementById('btn-medizin-send');
    if (!btn) return;
    btn.textContent = busy ? '■' : '↑';
    btn.title = busy ? 'Antwort läuft…' : 'Senden';
  }

  // ── Toast ─────────────────────────────────────────────────────────────────

  function _showToast(msg) {
    if (typeof showToast === 'function') { showToast(msg); return; }
    const t = document.getElementById('toast');
    if (t) { t.textContent = msg; t.classList.add('show'); setTimeout(() => t.classList.remove('show'), 2500); }
  }

  // ── Init ──────────────────────────────────────────────────────────────────

  function init() {
    // Modell kommt aus dem Profil (Rolle „Medizin", MedGemma) — kein Selektor mehr.

    // Patienten-RAG
    _loadPatientRags();
    document.getElementById('medizin-rag-select')?.addEventListener('change', _onRagChange);
    document.getElementById('btn-medizin-new-rag')?.addEventListener('click', _showNewRagForm);
    document.getElementById('btn-medizin-new-rag-ok')?.addEventListener('click', _createPatientRag);
    document.getElementById('btn-medizin-new-rag-cancel')?.addEventListener('click', _hideNewRagForm);
    document.getElementById('medizin-new-rag-name')?.addEventListener('keydown', e => {
      if (e.key === 'Enter') _createPatientRag();
      if (e.key === 'Escape') _hideNewRagForm();
    });

    // Dokument zur Akte hochladen
    const ragFileInput = document.getElementById('medizin-rag-file-input');
    document.getElementById('btn-medizin-doc-to-rag')?.addEventListener('click', () => ragFileInput?.click());
    ragFileInput?.addEventListener('change', async () => {
      for (const file of ragFileInput.files) await _uploadDocToRag(file);
      ragFileInput.value = '';
    });

    // Chat-Dateianhang
    const chatFileInput = document.getElementById('medizin-chat-file-input');
    document.getElementById('btn-medizin-attach')?.addEventListener('click', () => chatFileInput?.click());
    chatFileInput?.addEventListener('change', async () => {
      for (const file of chatFileInput.files) await _attachChatFile(file);
      chatFileInput.value = '';
    });

    // Drag & Drop auf Eingabebereich
    const inputArea = document.getElementById('medizin-input-area');
    inputArea?.addEventListener('dragover', e => { e.preventDefault(); inputArea.style.borderColor = 'var(--accent)'; });
    inputArea?.addEventListener('dragleave', () => { inputArea.style.borderColor = ''; });
    inputArea?.addEventListener('drop', async e => {
      e.preventDefault(); inputArea.style.borderColor = '';
      for (const file of e.dataTransfer.files) await _attachChatFile(file);
    });

    // Senden
    document.getElementById('btn-medizin-send')?.addEventListener('click', _sendMessage);
    const input = document.getElementById('medizin-input');
    input?.addEventListener('keydown', e => {
      if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); _sendMessage(); }
    });
    input?.addEventListener('input', () => { if (typeof autoResizeTextarea === 'function') autoResizeTextarea(input); });

    // Schnellstart-Toggle
    document.getElementById('btn-medizin-quick-toggle')?.addEventListener('click', () => {
      const grid = document.getElementById('medizin-quick-grid');
      if (!grid) return;
      const open = grid.style.display !== 'none';
      grid.style.display = open ? 'none' : 'grid';
      document.getElementById('btn-medizin-quick-toggle').classList.toggle('active', !open);
    });

    // Experten-Pipeline ein-/ausschalten
    const expertBtn = document.getElementById('btn-medizin-expert');
    if (expertBtn) {
      expertBtn.classList.toggle('active', _expertMode);
      expertBtn.addEventListener('click', () => {
        _expertMode = !_expertMode;
        expertBtn.classList.toggle('active', _expertMode);
        _showToast(_expertMode
          ? '🔬 Experten-Pipeline aktiv (2 Modelle, mit Rückfragen)'
          : 'Einfacher Direkt-Chat (ein Modell)');
      });
    }

    // Verlauf leeren
    document.getElementById('btn-medizin-clear')?.addEventListener('click', () => {
      if (confirm('Gesprächsverlauf leeren?')) clearHistory();
    });

    // Schnellstart-Prompts
    document.querySelectorAll('.medizin-prompt-btn').forEach(btn => {
      btn.addEventListener('click', () => {
        const input = document.getElementById('medizin-input');
        if (input) { input.value = btn.dataset.prompt; if (typeof autoResizeTextarea === 'function') autoResizeTextarea(input); input.focus(); }
        // Schnellstart-Grid schließen
        document.getElementById('medizin-quick-grid').style.display = 'none';
        document.getElementById('btn-medizin-quick-toggle')?.classList.remove('active');
      });
    });
  }

  return { init, clearHistory };
})();
