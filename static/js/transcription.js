// transcription.js — Tab „🎙 Transkription" (Audio → Text) + Chat-Diktat-Knopf.
// Quelle: Mikrofon (inkl. USB, Geräteauswahl) oder Datei. Engine lokal
// (faster-whisper) oder API; der Geheim-Modus erzwingt lokal. Aufnahme über
// getUserMedia + MediaRecorder → Blob → POST /api/transcribe.

const Transcription = (function () {
  let _engines = null;
  let _rec = null;            // MediaRecorder
  let _chunks = [];
  let _stream = null;
  let _recording = false;
  let _target = null;         // 'tab' | 'chat' — wohin das Ergebnis geht
  let _lastText = '';

  const _el = (id) => document.getElementById(id);

  // ── Engines/Modelle laden ────────────────────────────────────────────────
  async function _loadEngines() {
    try {
      _engines = await (await fetch('/api/transcribe/engines')).json();
    } catch (_) {
      _engines = { local_available: false, local_models: [], providers: [], local_only: false, api_enabled: false };
    }
    // Geheim-Modus: API-Option sperren
    const engSel = _el('tr-engine');
    if (engSel) {
      const apiOpt = engSel.querySelector('option[value="api"]');
      const blockApi = _engines.local_only || !_engines.api_enabled || (_engines.providers || []).length === 0;
      if (apiOpt) { apiOpt.disabled = blockApi; }
      if (blockApi) engSel.value = 'local';
    }
    _fillModels();
    _updateNote();
  }

  function _fillModels() {
    const sel = _el('tr-model');
    if (!sel || !_engines) return;
    const engine = _el('tr-engine')?.value || 'local';
    sel.innerHTML = '';
    if (engine === 'local') {
      const models = _engines.local_models || [];
      if (!models.length) {
        sel.innerHTML = '<option value="">— faster-whisper nicht installiert —</option>';
        return;
      }
      for (const m of models) {
        const o = document.createElement('option');
        o.value = m; o.textContent = m;
        if (m === (_engines.local_default || 'base')) o.selected = true;
        sel.appendChild(o);
      }
    } else {
      // API: pro Anbieter gängige Whisper-Modelle als Vorschlag (editierbar über Anbieter-Seite)
      const provs = _engines.providers || [];
      if (!provs.length) {
        sel.innerHTML = '<option value="">— kein API-Anbieter konfiguriert —</option>';
        return;
      }
      for (const p of provs) {
        for (const wm of ['whisper-large-v3', 'whisper-1']) {
          const o = document.createElement('option');
          o.value = `${p.id}::${wm}`;
          o.textContent = `${p.name || p.id} · ${wm}`;
          sel.appendChild(o);
        }
      }
    }
  }

  function _updateNote() {
    const note = _el('tr-engine-note');
    if (!note || !_engines) return;
    const engine = _el('tr-engine')?.value || 'local';
    if (_engines.local_only) {
      note.textContent = '🔒 Geheim-Modus aktiv — Transkription läuft lokal auf diesem Rechner.';
    } else if (engine === 'local') {
      note.textContent = _engines.local_available
        ? 'Lokal — die Audiodaten verlassen diesen Rechner nicht.'
        : '⚠ faster-whisper ist nicht installiert — bitte lokale Engine nachinstallieren oder API wählen.';
    } else {
      note.textContent = '⚠ API — die Audiodaten werden an den externen Anbieter gesendet.';
    }
  }

  // ── Mikrofon-Geräte ──────────────────────────────────────────────────────
  async function _listDevices() {
    const sel = _el('tr-mic-device');
    if (!sel || !navigator.mediaDevices?.enumerateDevices) return;
    try {
      const devs = await navigator.mediaDevices.enumerateDevices();
      const mics = devs.filter((d) => d.kind === 'audioinput');
      const prev = sel.value;
      sel.innerHTML = '';
      if (!mics.length) { sel.innerHTML = '<option value="">Standard-Mikrofon</option>'; return; }
      mics.forEach((d, i) => {
        const o = document.createElement('option');
        o.value = d.deviceId;
        o.textContent = d.label || `Mikrofon ${i + 1}`;
        sel.appendChild(o);
      });
      if (prev) sel.value = prev;
    } catch (_) {}
  }

  // ── Aufnahme ─────────────────────────────────────────────────────────────
  async function _startRecording(target) {
    if (_recording) return;
    _target = target;
    const deviceId = _el('tr-mic-device')?.value || '';
    const constraints = { audio: deviceId ? { deviceId: { exact: deviceId } } : true };
    try {
      _stream = await navigator.mediaDevices.getUserMedia(constraints);
    } catch (e) {
      _toast('Mikrofon nicht verfügbar: ' + (e.message || e));
      return;
    }
    await _listDevices();  // jetzt sind Geräte-Labels sichtbar
    _chunks = [];
    _rec = new MediaRecorder(_stream);
    _rec.ondataavailable = (ev) => { if (ev.data && ev.data.size) _chunks.push(ev.data); };
    _rec.onstop = _onRecStop;
    _rec.start();
    _recording = true;
    _reflectRecording(true);
  }

  function _stopRecording() {
    if (!_recording || !_rec) return;
    try { _rec.stop(); } catch (_) {}
    _recording = false;
    _reflectRecording(false);
  }

  function _reflectRecording(on) {
    const tabBtn = _el('tr-mic-toggle');
    if (tabBtn) {
      tabBtn.textContent = on ? '■ Aufnahme stoppen' : '● Aufnahme starten';
      tabBtn.classList.toggle('recording', on && _target === 'tab');
    }
    const chatBtn = _el('btn-chat-mic');
    if (chatBtn) chatBtn.classList.toggle('active', on && _target === 'chat');
    const st = _el('tr-mic-status');
    if (st && _target === 'tab') st.textContent = on ? '● Aufnahme läuft …' : '';
  }

  async function _onRecStop() {
    // Mikrofon freigeben
    if (_stream) { _stream.getTracks().forEach((t) => t.stop()); _stream = null; }
    if (!_chunks.length) return;
    const blob = new Blob(_chunks, { type: _chunks[0].type || 'audio/webm' });
    await _transcribeAndShow(blob, 'aufnahme.webm', _target);
  }

  // ── Transkribieren ───────────────────────────────────────────────────────
  async function _transcribeBlob(blob, filename) {
    const fd = new FormData();
    fd.append('audio', blob, filename || 'audio.webm');
    fd.append('engine', _el('tr-engine')?.value || 'local');
    fd.append('model', _el('tr-model')?.value || '');
    fd.append('language', _el('tr-language')?.value || 'auto');
    fd.append('task', _el('tr-task')?.value || 'transcribe');
    const r = await fetch('/api/transcribe', { method: 'POST', body: fd });
    if (!r.ok) {
      let msg = `HTTP ${r.status}`;
      try { const j = await r.json(); msg = j.detail || msg; } catch (_) {}
      throw new Error(msg);
    }
    return r.json();
  }

  async function _transcribeAndShow(blob, filename, target) {
    const prog = _el('tr-progress');
    if (target === 'tab' && prog) prog.style.display = 'block';
    const chatBtn = _el('btn-chat-mic');
    if (target === 'chat' && chatBtn) chatBtn.textContent = '⏳ …';
    try {
      const res = await _transcribeBlob(blob, filename);
      _lastText = res.text || '';
      if (res.forced_local) _toast('🔒 Geheim-Modus — lokal transkribiert.');
      if (target === 'chat') {
        const inp = _el('message-input');
        if (inp) {
          inp.value = (inp.value ? inp.value.trimEnd() + ' ' : '') + _lastText;
          inp.dispatchEvent(new Event('input', { bubbles: true }));
          inp.focus();
        }
      } else {
        const ta = _el('tr-text');
        if (ta) ta.value = _lastText;
        _renderSegments(res.segments || []);
      }
    } catch (e) {
      _toast('Transkription fehlgeschlagen: ' + (e.message || e));
    } finally {
      if (prog) prog.style.display = 'none';
      if (chatBtn) chatBtn.textContent = '🎙 Diktat';
    }
  }

  function _fmtTime(s) {
    s = Math.max(0, Math.floor(s || 0));
    const m = Math.floor(s / 60), r = s % 60;
    return `${m}:${String(r).padStart(2, '0')}`;
  }

  function _renderSegments(segs) {
    const box = _el('tr-segments');
    if (!box) return;
    if (!segs.length) { box.innerHTML = ''; return; }
    box.innerHTML = '<div class="planner-muted" style="font-size:11.5px;margin-bottom:4px">Zeitmarken</div>' +
      segs.map((s) =>
        `<div class="tr-seg"><span class="tr-seg-t">${_fmtTime(s.start)}</span>${_esc(s.text)}</div>`
      ).join('');
  }

  function _esc(t) {
    return (t || '').replace(/[&<>]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;' }[c]));
  }

  function _toast(m) {
    if (typeof showToast === 'function') showToast(m); else console.log(m);
  }

  // ── Ergebnis-Aktionen ────────────────────────────────────────────────────
  function _copy() {
    const t = _el('tr-text')?.value || '';
    if (!t) return;
    navigator.clipboard?.writeText(t).then(() => _toast('Kopiert.'), () => {});
  }

  function _exportTxt() {
    const t = _el('tr-text')?.value || '';
    if (!t) return;
    const blob = new Blob([t], { type: 'text/plain;charset=utf-8' });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = 'transkript.txt';
    a.click();
    setTimeout(() => URL.revokeObjectURL(a.href), 2000);
  }

  function _toChat() {
    const t = _el('tr-text')?.value || '';
    if (!t) return;
    const inp = _el('message-input');
    if (inp) {
      inp.value = t;
      inp.dispatchEvent(new Event('input', { bubbles: true }));
    }
    if (typeof switchTab === 'function') switchTab('chat');
    inp?.focus();
  }

  function _toTodo() {
    const t = _el('tr-text')?.value || '';
    if (!t) return;
    const note = _el('todo-note');
    if (note) {
      note.value = (note.value ? note.value.trimEnd() + '\n\n' : '') + t;
    }
    if (typeof switchTab === 'function') switchTab('todo');
    _toast('Text als Notiz ins To-Do übernommen — dort „To-Do-Liste ableiten".');
  }

  // ── Init ─────────────────────────────────────────────────────────────────
  function init() {
    _loadEngines();
    _listDevices();
    navigator.mediaDevices?.addEventListener?.('devicechange', _listDevices);

    _el('tr-engine')?.addEventListener('change', () => { _fillModels(); _updateNote(); });
    _el('tr-mic-toggle')?.addEventListener('click', () =>
      _recording ? _stopRecording() : _startRecording('tab'));
    _el('tr-file-go')?.addEventListener('click', async () => {
      const f = _el('tr-file')?.files?.[0];
      if (!f) { _toast('Bitte zuerst eine Audiodatei wählen.'); return; }
      await _transcribeAndShow(f, f.name, 'tab');
    });
    _el('tr-speak')?.addEventListener('click', (e) => {
      const t = _el('tr-text')?.value || '';
      if (!t) { _toast('Kein Text zum Vorlesen.'); return; }
      if (typeof TTS !== 'undefined' && TTS.available()) {
        const lang = _el('tr-language')?.value;
        TTS.toggle(e.currentTarget, t, '');
      } else {
        _toast('Sprachausgabe wird von diesem Browser nicht unterstützt.');
      }
    });
    _el('tr-copy')?.addEventListener('click', _copy);
    _el('tr-export')?.addEventListener('click', _exportTxt);
    _el('tr-to-chat')?.addEventListener('click', _toChat);
    _el('tr-to-todo')?.addEventListener('click', _toTodo);

    // Chat-Diktat-Knopf
    _el('btn-chat-mic')?.addEventListener('click', () =>
      _recording ? _stopRecording() : _startRecording('chat'));
  }

  // Nach einem Umschalten des Geheim-Modus die Engine-Liste neu bewerten.
  function refresh() { _loadEngines(); }

  return { init, refresh };
})();
