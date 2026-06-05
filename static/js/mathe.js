/* AI_Framework_Thomas — Mathe-Tab (Chat + Plots + LaTeX/PDF) */

const MatheChat = (() => {
  let _streaming    = false;
  let _attachedFiles = [];
  let _history      = [];
  let _tutorMode    = false;  // 🎓 Tutor-Modus: Schritt für Schritt statt Sofortlösung

  // ── Modell ──────────────────────────────────────────────────────────────
  // Mathe teilt sich das Modell mit dem Code-Tab → Profil-Rolle „Programmieren / Mathe".
  function _model() {
    return (typeof Profile !== 'undefined' && Profile.modelFor)
      ? Profile.modelFor('coding') : 'ministral-3:3b';
  }

  // ── Dateianhänge ─────────────────────────────────────────────────────────

  async function _attachFile(file) {
    try {
      const fd = new FormData();
      fd.append('file', file);
      const resp = await fetch('/api/upload', { method: 'POST', body: fd });
      if (!resp.ok) throw new Error('HTTP ' + resp.status);
      const info = await resp.json();
      if (info.is_image) {
        const reader = new FileReader();
        reader.onload = e => { info.src = e.target.result; _attachedFiles.push(info); _renderAttachments(); };
        reader.readAsDataURL(file);
      } else {
        _attachedFiles.push(info);
        _renderAttachments();
      }
    } catch (e) { _toast('Upload fehlgeschlagen: ' + e.message); }
  }

  function _renderAttachments() {
    const box = document.getElementById('mathe-attachments');
    if (!box) return;
    box.innerHTML = '';
    if (!_attachedFiles.length) { box.style.display = 'none'; return; }
    box.style.display = 'flex';
    _attachedFiles.forEach((f, i) => {
      const chip = document.createElement('div');
      chip.className = 'medizin-attach-chip'; // reuse medizin chip styles
      chip.innerHTML = f.is_image && f.src
        ? `<img src="${f.src}" alt="${f.filename}" /><span>${f.filename}</span><button data-idx="${i}">✕</button>`
        : `<span class="medizin-file-icon">📄</span><span>${f.filename}</span><button data-idx="${i}">✕</button>`;
      chip.querySelector('button').addEventListener('click', e => {
        _attachedFiles.splice(Number(e.target.dataset.idx), 1); _renderAttachments();
      });
      box.appendChild(chip);
    });
  }

  // ── Nachrichten rendern ───────────────────────────────────────────────────

  function _appendMsg(role, text) {
    const box = document.getElementById('mathe-messages');
    if (!box) return null;
    box.querySelector('.mathe-welcome')?.remove();

    const row = document.createElement('div');
    row.className = `medizin-msg ${role}`; // reuse medizin-msg layout

    const bubble = document.createElement('div');
    bubble.className = `medizin-bubble mathe-bubble`;

    if (role === 'user' && _attachedFiles.length) {
      const imgs = _attachedFiles.filter(f => f.is_image && f.src);
      if (imgs.length) {
        const imgRow = document.createElement('div');
        imgRow.className = 'medizin-msg-images';
        imgs.forEach(f => { const img = document.createElement('img'); img.src = f.src; img.alt = f.filename; imgRow.appendChild(img); });
        bubble.appendChild(imgRow);
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

  // ── Bild-Frame aus SSE anzeigen ───────────────────────────────────────────

  function _appendImageFrame(src, alt) {
    const box = document.getElementById('mathe-messages');
    if (!box) return;
    const row = document.createElement('div');
    row.className = 'mathe-plot-row';
    const img = document.createElement('img');
    // Backend liefert bereits eine vollständige Data-URI ("data:image/png;base64,…").
    // Nur falls (alt) ein nacktes Base64 ankommt, das Präfix ergänzen — niemals doppeln.
    img.src = (typeof src === 'string' && src.startsWith('data:'))
      ? src : `data:image/png;base64,${src}`;
    img.alt = alt || 'Plot';
    img.className = 'mathe-plot-img';
    row.appendChild(img);
    box.appendChild(row);
    box.scrollTop = box.scrollHeight;
  }

  // ── Nachricht senden ──────────────────────────────────────────────────────

  async function _sendMessage() {
    if (_streaming) return;
    const input = document.getElementById('mathe-input');
    const text  = (input?.value || '').trim();
    if (!text && !_attachedFiles.length) return;

    const model   = _model();
    const wantPlot  = document.getElementById('mathe-opt-plot')?.checked !== false;
    const wantLatex = true;  // LaTeX ist im Mathe-Tab Standard (keine Auswahl mehr)
    const fileIds = _attachedFiles.map(f => f.id);

    // WICHTIG: Den SAUBEREN Nutzertext im Verlauf speichern – keine Hinweise
    // einbacken, sonst akkumulieren sie über alle Folgeturns und verschmutzen
    // den Kontext.
    const userMsg = { role: 'user', content: text || '(Anhang)', files: fileIds.length ? fileIds : undefined };
    _history.push(userMsg);
    _appendMsg('user', text || '(Anhang)');
    const assistantEl = _appendMsg('assistant', '');
    if (input) { input.value = ''; if (typeof autoResizeTextarea === 'function') autoResizeTextarea(input); }

    _attachedFiles = []; _renderAttachments();
    _streaming = true; _setBtnState(true);

    // Ausgehende Nachrichten aus dem sauberen Verlauf bauen; den Ausgabe-Hinweis
    // NUR an die letzte (aktuelle) Nachricht hängen und WEICH formulieren, damit
    // reine Theorie-Fragen (z. B. „Riemannsche Vermutung") nicht zu einem
    // erzwungenen, sinnlosen Plot-Aufruf führen. Im Tutor-Modus KEIN Hinweis –
    // der Tutor-Agent steuert sein Verhalten vollständig über seinen Prompt.
    const outMessages = _history.map(m => ({ role: m.role, content: m.content, files: m.files || [] }));
    if (!_tutorMode) {
      const hints = [];
      if (wantPlot)  hints.push('Falls die Frage eine konkrete Funktion oder Wertereihe enthält, visualisiere sie mit plot_function bzw. plot_chart (sonst nicht).');
      if (wantLatex) hints.push('Formatiere mathematische Formeln in LaTeX ($…$ inline, $$…$$ als Block).');
      if (hints.length && outMessages.length) {
        const last = outMessages[outMessages.length - 1];
        last.content = `${last.content}\n\n[Formatierungshinweis: ${hints.join(' ')}]`;
      }
    } else {
      // Werkzeuggeprüft: SymPy-Grundwahrheit serverseitig holen und dem Tutor als
      // verifizierte Fakten mitgeben (kleine Modelle rufen Tools selbst nicht zuverlässig auf).
      try {
        const gr = await fetch('/api/mathe/ground', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ messages: _history.map(m => ({ role: m.role, content: m.content })), model }),
        });
        if (gr.ok) {
          const { facts } = await gr.json();
          if (facts && outMessages.length) {
            const last = outMessages[outMessages.length - 1];
            last.content = `${last.content}\n\n[Verifizierte Fakten (per SymPy berechnet — intern zum Prüfen und gezielten Anleiten nutzen, NICHT ungefragt komplett verraten):\n${facts}]`;
          }
        }
      } catch (_) { /* ohne Fakten weiter (rein sokratisch) */ }
    }

    const body = {
      messages:        outMessages,
      model:           model,
      agent_id:        _tutorMode ? 'mathe_tutor' : 'mathe_experte',
      rag_collections: [],
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
      let buf = '', fullText = '', hasPlot = false;

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
              document.getElementById('mathe-messages').scrollTop = 99999;
            } else if (ev.type === 'image') {
              // Plot-Bild direkt im Chat anzeigen
              _appendImageFrame(ev.data, ev.alt || 'Diagramm');
              hasPlot = true;
            } else if (ev.type === 'done') {
              _renderMd(assistantEl, fullText);
            } else if (ev.type === 'error') {
              if (assistantEl) assistantEl.textContent = 'Fehler: ' + (ev.content || 'Unbekannter Fehler');
            }
          } catch (_) {}
        }
      }
      if (fullText && assistantEl) _renderMd(assistantEl, fullText);
      if (fullText) _history.push({ role: 'assistant', content: fullText });

      // LaTeX-Export anbieten wenn Ergebnis LaTeX enthält und Option aktiv
      if (wantLatex && fullText && fullText.includes('$') && _history.length > 0) {
        _offerLatexExport(fullText);
      }
    } catch (e) {
      if (assistantEl) assistantEl.textContent = 'Verbindungsfehler: ' + e.message;
    } finally {
      _streaming = false; _setBtnState(false);
    }
  }

  // ── LaTeX/PDF Export ──────────────────────────────────────────────────────

  function _offerLatexExport(text) {
    const box = document.getElementById('mathe-messages');
    if (!box) return;
    // Kein doppelter Export-Button
    if (box.querySelector('.mathe-export-bar')) return;

    const bar = document.createElement('div');
    bar.className = 'mathe-export-bar';
    bar.innerHTML = `
      <span style="font-size:12px;color:var(--text-muted)">Exportieren:</span>
      <button class="export-btn" id="btn-mathe-export-pdf" style="font-size:12px">📄 PDF</button>
      <button class="export-btn" id="btn-mathe-export-latex" style="font-size:12px">📋 LaTeX</button>`;
    box.appendChild(bar);
    box.scrollTop = box.scrollHeight;

    bar.querySelector('#btn-mathe-export-pdf').addEventListener('click', () => _exportLatex('pdf', text));
    bar.querySelector('#btn-mathe-export-latex').addEventListener('click', () => _exportLatex('latex', text));
  }

  async function _exportLatex(format, text) {
    try {
      const title = 'Mathe-Lösung';
      const payload = { type: 'document', title, content: text };
      const resp = await fetch(`/api/export/${format}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      if (!resp.ok) throw new Error('HTTP ' + resp.status);
      const blob = await resp.blob();
      const ext  = format === 'latex' ? 'tex' : 'pdf';
      const a = document.createElement('a');
      a.href = URL.createObjectURL(blob);
      a.download = `mathe_loesung.${ext}`;
      a.click();
      URL.revokeObjectURL(a.href);
      _toast(`✓ Als ${format.toUpperCase()} exportiert`);
    } catch (e) { _toast('Export fehlgeschlagen: ' + e.message); }
  }

  // ── Hilfs-Funktionen ──────────────────────────────────────────────────────

  function _setBtnState(busy) {
    const btn = document.getElementById('btn-mathe-send');
    if (!btn) return;
    btn.textContent = busy ? '■' : '↑';
  }

  function _toast(msg) {
    if (typeof showToast === 'function') showToast(msg);
  }

  function clearHistory() {
    _history = [];
    const box = document.getElementById('mathe-messages');
    if (box) box.innerHTML = `
      <div class="mathe-welcome">
        <div style="font-size:36px;margin-bottom:12px">🔢</div>
        <div style="font-weight:600;font-size:15px;margin-bottom:8px">Mathe-Assistent</div>
        <div style="color:var(--text-muted);font-size:13px;max-width:440px;text-align:center">
          Löst Gleichungen, plottet Funktionen, rechnet Integrals und erstellt LaTeX-Berichte.<br>
          Nutzt SymPy, NumPy, SciPy und Matplotlib direkt im Chat.
        </div>
        <div class="mathe-feature-chips">
          <span>∫ Analysis</span><span>📐 Algebra</span><span>📈 Plots</span>
          <span>📊 Statistik</span><span>📋 LaTeX/PDF</span><span>🔢 Numerik</span>
        </div>
      </div>`;
  }

  // ── Init ──────────────────────────────────────────────────────────────────

  function init() {

    // Datei-Anhang
    const chatFileInput = document.getElementById('mathe-chat-file-input');
    document.getElementById('btn-mathe-attach')?.addEventListener('click', () => chatFileInput?.click());
    chatFileInput?.addEventListener('change', async () => {
      for (const file of chatFileInput.files) await _attachFile(file);
      chatFileInput.value = '';
    });

    // Drag & Drop
    const inputArea = document.getElementById('mathe-input-area');
    inputArea?.addEventListener('dragover', e => { e.preventDefault(); inputArea.style.borderColor = 'var(--accent)'; });
    inputArea?.addEventListener('dragleave', () => { inputArea.style.borderColor = ''; });
    inputArea?.addEventListener('drop', async e => {
      e.preventDefault(); inputArea.style.borderColor = '';
      for (const file of e.dataTransfer.files) await _attachFile(file);
    });

    // Senden
    document.getElementById('btn-mathe-send')?.addEventListener('click', _sendMessage);
    const input = document.getElementById('mathe-input');
    input?.addEventListener('keydown', e => {
      if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); _sendMessage(); }
    });
    input?.addEventListener('input', () => { if (typeof autoResizeTextarea === 'function') autoResizeTextarea(input); });

    // Schnellstart-Toggle
    document.getElementById('btn-mathe-quick-toggle')?.addEventListener('click', () => {
      const grid = document.getElementById('mathe-quick-grid');
      if (!grid) return;
      const open = grid.style.display !== 'none';
      grid.style.display = open ? 'none' : 'grid';
      document.getElementById('btn-mathe-quick-toggle')?.classList.toggle('active', !open);
    });

    // Schnellstart-Prompts
    document.querySelectorAll('.mathe-prompt-btn').forEach(btn => {
      btn.addEventListener('click', () => {
        const input = document.getElementById('mathe-input');
        if (input) { input.value = btn.dataset.prompt; if (typeof autoResizeTextarea === 'function') autoResizeTextarea(input); input.focus(); }
        document.getElementById('mathe-quick-grid').style.display = 'none';
        document.getElementById('btn-mathe-quick-toggle')?.classList.remove('active');
      });
    });

    // Tutor-Modus ein-/ausschalten
    const tutorBtn = document.getElementById('btn-mathe-tutor');
    const solBtn   = document.getElementById('btn-mathe-solution');
    if (tutorBtn) {
      tutorBtn.addEventListener('click', () => {
        _tutorMode = !_tutorMode;
        tutorBtn.classList.toggle('active', _tutorMode);
        if (solBtn) solBtn.style.display = _tutorMode ? '' : 'none';
        const inp = document.getElementById('mathe-input');
        if (inp) inp.placeholder = _tutorMode
          ? 'Aufgabe eingeben – der Tutor führt dich Schritt für Schritt…'
          : 'Mathematische Aufgabe oder Frage eingeben…';
        if (typeof showToast === 'function') showToast(_tutorMode
          ? '🎓 Tutor-Modus aktiv – ich löse nicht sofort, sondern leite dich an'
          : 'Tutor-Modus aus – direkte Lösungen');
      });
    }

    // „Lösung zeigen" – fordert die vollständige Lösung an (Notausgang)
    solBtn?.addEventListener('click', () => {
      if (_streaming) return;
      const inp = document.getElementById('mathe-input');
      if (inp) {
        inp.value = 'Bitte zeige mir jetzt die vollständige Lösung Schritt für Schritt mit Endergebnis.';
        _sendMessage();
      }
    });

    // Verlauf leeren
    document.getElementById('btn-mathe-clear')?.addEventListener('click', () => {
      if (confirm('Gesprächsverlauf leeren?')) clearHistory();
    });
  }

  return { init, clearHistory };
})();
