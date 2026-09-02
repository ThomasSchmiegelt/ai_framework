/*
 * Videoerzeugung — Tab-Modul (lokal Wan über die z-video-Brücke).
 *
 * Modi: 🎞 flf2v (Erst-+Letztbild) · 🖼 i2v (Einzelbild) · 📝 t2v (Text).
 * Bilder werden NICHT verzerrt: pro Bild ein positionierbarer Rahmen (Pan per
 * Ziehen + Zoom-Regler) im Ziel-Seitenverhältnis → beim Erzeugen exakt auf die
 * Zielauflösung zugeschnitten (Cover-Crop, kein Strecken).
 * Optional „✨ Prompt verbessern": ein LLM formuliert die Beschreibung aus.
 *
 * Öffentliche API:
 *   init()/refresh()                         — Tab
 *   frameImage(host, dataUrl, w, h)          — geteilter Rahmen (auch vom Chat /video)
 *   enhancePrompt(prompt, mode)              — geteilte Prompt-Erweiterung (Chat /video)
 */
const Video = (function () {
  const _el = (id) => document.getElementById(id);

  let _mode = 'flf2v';
  let _cfg = null;
  let _busy = false;
  let _firstFr = null;   // Framer-Instanz Startbild
  let _lastFr = null;    // Framer-Instanz Endbild

  // Fertige Vorlagen-Prompts je Modus (klickbare Chips → füllen das Prompt-Feld).
  const _PRESETS = {
    flf2v: [
      { label: '🌀 Morph', text: 'nahtloses, flüssiges Morphing vom ersten zum letzten Bild, weiche Überblendung, gleichmäßige Bewegung, cinematisch' },
      { label: '↔️ Sanfter Übergang', text: 'sanfter, ruhiger Übergang vom Startbild zum Endbild, langsame Verwandlung, weiche Bewegung' },
      { label: '🎥 Übergang mit Kamerafahrt', text: 'langsame Kamerafahrt, während sich das Motiv vom ersten zum letzten Bild verändert, filmisch und ruhig' },
      { label: '⚡ Schnelle Verwandlung', text: 'schnelle, dynamische Verwandlung vom ersten zum letzten Bild, energiegeladen, klare Bewegung' },
      // Maschinenbau / technisch
      { label: '🔩 Explosionsdarstellung', text: 'technische Explosionszeichnung: die Bauteile fahren aus der Baugruppe auseinander und wieder zusammen, präzise, neutraler Hintergrund, saubere Konturen' },
      { label: '📐 Querschnitt öffnet sich', text: 'animierte Schnittansicht: die Baugruppe öffnet sich und legt das Innenleben frei, technische Darstellung, klare Kanten' },
      { label: '🔧 Zustand A → B', text: 'gleichmäßiger technischer Übergang vom Ausgangszustand zum Endzustand des Bauteils, exakte Passung, ruhige Kamera' },
      { label: '🏗 Aufbau/Montage', text: 'schrittweiser Zusammenbau der Baugruppe vom Einzelteil zum fertigen Produkt, Montagesequenz, technisch präzise' },
    ],
    i2v: [
      { label: '🍃 Leichte Bewegung', text: 'subtile, natürliche Bewegung im Bild, leichter Wind, lebendige Details, ruhige Kamera' },
      { label: '🔍 Langsamer Zoom', text: 'langsamer, ruhiger Zoom in das Motiv, filmische Tiefe, weiches Licht' },
      { label: '🎥 Kamerafahrt rechts', text: 'sanfte Kamerafahrt nach rechts, leichte Parallaxe, cinematisch' },
      { label: '✨ Cinematisch', text: 'filmische Belebung des Bildes, atmosphärisch, weiche Lichtstimmung' },
      // Maschinenbau / technisch
      { label: '🔄 360°-Drehung', text: 'gleichmäßige 360-Grad-Drehung des Objekts um die Hochachse (Turntable), konstante Geschwindigkeit, neutraler Hintergrund' },
      { label: '⚙️ Mechanik läuft', text: 'die Mechanik setzt sich in Bewegung, Zahnräder und Bauteile arbeiten ineinander, technisch realistisch' },
      { label: '🔎 Detail-Zoom Bauteil', text: 'langsamer Zoom auf das Maschinenbauteil, Fokus auf Oberfläche, Kanten und Passung, technische Präzision' },
      { label: '💡 Funktion zeigen', text: 'das Bauteil demonstriert seine Funktion in einer ruhigen, klaren Bewegung, technische Visualisierung' },
    ],
    t2v: [
      { label: '🎬 Cinematisch', text: 'filmische Weitwinkelaufnahme, goldenes Abendlicht, ruhige gleichmäßige Kamerafahrt, hohe Detailtiefe' },
      { label: '🏞 Landschaft', text: 'weite Landschaftsaufnahme, langsame Drohnenfahrt, natürliches Sonnenlicht, sanfte Bewegung' },
      { label: '⚡ Action', text: 'dynamische Actionszene, schnelle Kamerabewegung, hohe Energie, scharfe Details' },
      { label: '🌙 Stimmung', text: 'ruhige, atmosphärische Szene, weiches Licht, sanfte langsame Bewegung, cinematisch' },
      // Maschinenbau / technisch
      { label: '⚙️ Technische Animation', text: 'saubere technische Produktvisualisierung, CAD-artiges Rendering, neutraler Hintergrund, gleichmäßige Studio-Beleuchtung, langsame Kamerafahrt' },
      { label: '🏭 Fertigung', text: 'laufende Industriemaschine in einer Werkshalle, bewegte Mechanik und Förderband, realistisch, ruhige Kamera' },
      { label: '🤖 Roboterarm', text: 'Industrieroboter-Arm führt eine präzise, flüssige Bewegung aus, Fertigungsumgebung, technisch realistisch' },
      { label: '🔩 CAD-Turntable', text: 'technisches Bauteil dreht sich langsam auf einem Turntable, CAD-Rendering, neutraler Hintergrund, klare Kanten' },
    ],
  };
  function promptPresets(mode) { return _PRESETS[mode] || _PRESETS.t2v; }

  // Vorlage anhängen statt ersetzen (mehrere kombinierbar); Duplikate vermeiden.
  function appendPrompt(el, text) {
    if (!el) return;
    const cur = (el.value || '').trim();
    if (!cur) el.value = text;
    else if (cur.includes(text)) { /* schon enthalten */ }
    else el.value = cur.replace(/[,;\s]+$/, '') + ', ' + text;
    el.focus();
  }

  // ── Geteilter Rahmen: Bild im Ziel-Seitenverhältnis positionieren (Pan+Zoom) ──
  function frameImage(host, dataUrl, outW, outH) {
    host.innerHTML = '';
    const frame = document.createElement('div');
    frame.className = 'vg-frame';
    const cv = document.createElement('canvas');
    frame.appendChild(cv);
    const zoom = document.createElement('input');
    zoom.type = 'range'; zoom.min = '1'; zoom.max = '3'; zoom.step = '0.01'; zoom.value = '1';
    zoom.className = 'vg-zoom';
    const hint = document.createElement('div');
    hint.className = 'vg-frame-hint';
    hint.textContent = 'Bild ziehen zum Verschieben · Regler = Zoom';
    host.appendChild(frame); host.appendChild(zoom); host.appendChild(hint);

    const img = new Image();
    let fw = 0, fh = 0, minZoom = 1, z = 1, offX = 0, offY = 0, natW = 0, natH = 0;
    let tW = outW || 1280, tH = outH || 720, ready = false;

    function clamp() {
      const dw = natW * z, dh = natH * z;
      offX = Math.min(0, Math.max(fw - dw, offX));
      offY = Math.min(0, Math.max(fh - dh, offY));
    }
    function draw() {
      const ctx = cv.getContext('2d');
      ctx.clearRect(0, 0, fw, fh);
      ctx.drawImage(img, offX, offY, natW * z, natH * z);
    }
    function layout() {
      if (!natW) return;
      const availW = host.clientWidth || 360;
      fw = Math.max(120, Math.min(availW, 460));
      fh = Math.round(fw * tH / tW);
      frame.style.height = fh + 'px';
      cv.width = fw; cv.height = fh;
      minZoom = Math.max(fw / natW, fh / natH);
      z = minZoom * parseFloat(zoom.value || '1');
      // zentrieren
      offX = (fw - natW * z) / 2;
      offY = (fh - natH * z) / 2;
      clamp(); draw();
      ready = true;
    }
    img.onload = () => { natW = img.naturalWidth; natH = img.naturalHeight; layout(); };
    img.src = dataUrl;

    zoom.oninput = () => {
      if (!ready) return;
      const cx = (fw / 2 - offX) / z, cy = (fh / 2 - offY) / z;   // Bildpunkt in Rahmenmitte
      z = minZoom * parseFloat(zoom.value);
      offX = fw / 2 - cx * z; offY = fh / 2 - cy * z;
      clamp(); draw();
    };
    let dragging = false, sx = 0, sy = 0, sox = 0, soy = 0;
    frame.addEventListener('pointerdown', (e) => {
      if (!ready) return;
      dragging = true; try { frame.setPointerCapture(e.pointerId); } catch (_) {}
      sx = e.clientX; sy = e.clientY; sox = offX; soy = offY;
    });
    frame.addEventListener('pointermove', (e) => {
      if (!dragging) return;
      offX = sox + (e.clientX - sx); offY = soy + (e.clientY - sy);
      clamp(); draw();
    });
    const stop = () => { dragging = false; };
    frame.addEventListener('pointerup', stop);
    frame.addEventListener('pointerleave', stop);

    return {
      export() {
        if (!ready) return dataUrl;
        const o = document.createElement('canvas');
        o.width = tW; o.height = tH;
        const k = tW / fw;
        o.getContext('2d').drawImage(img, offX * k, offY * k, natW * z * k, natH * z * k);
        return o.toDataURL('image/png');
      },
      setTarget(w, h) { tW = w || tW; tH = h || tH; layout(); },
      relayout() { layout(); },
      dispose() { host.innerHTML = ''; },
    };
  }

  // ── Geteilte Prompt-Erweiterung (LLM, optional) ─────────────────────────────
  async function enhancePrompt(prompt, mode) {
    try {
      const r = await fetch('/api/video/enhance-prompt', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ prompt, mode: mode || 't2v' }),
      });
      if (!r.ok) return { prompt, tokens: null };
      const d = await r.json();
      if (d.tokens && typeof TokenMeter !== 'undefined') TokenMeter.add(d.tokens, 'Videoerzeugung Prompt');
      return { prompt: (d.prompt || prompt), tokens: d.tokens };
    } catch (_) { return { prompt, tokens: null }; }
  }

  // ── Tab-spezifisch ──────────────────────────────────────────────────────────
  function _targetWH() {
    const v = _el('vg-size')?.value || '720p';
    const s = (_cfg && _cfg.sizes || []).find(x => x.value === v);
    return s ? [s.w, s.h] : [1280, 720];
  }

  function _setMode(m) {
    _mode = ['flf2v', 'i2v', 't2v'].includes(m) ? m : 'flf2v';
    _el('vg-mode-flf2v')?.classList.toggle('active', _mode === 'flf2v');
    _el('vg-mode-i2v')?.classList.toggle('active', _mode === 'i2v');
    _el('vg-mode-t2v')?.classList.toggle('active', _mode === 't2v');
    const ff = _el('vg-first-field'), lf = _el('vg-last-field');
    if (ff) ff.style.display = (_mode === 't2v') ? 'none' : '';
    if (lf) lf.style.display = (_mode === 'flf2v') ? '' : 'none';
    _renderPresets();
  }

  function _renderPresets() {
    const host = _el('vg-presets');
    if (!host) return;
    host.innerHTML = '';
    promptPresets(_mode).forEach((p) => {
      const b = document.createElement('button');
      b.type = 'button'; b.className = 'vg-chip'; b.textContent = p.label; b.title = p.text;
      b.onclick = () => appendPrompt(_el('vg-prompt'), p.text);
      host.appendChild(b);
    });
  }

  function _mountFramer(inputId, holderId, setFr) {
    const inp = _el(inputId), holder = _el(holderId);
    if (!inp || !holder) return;
    inp.addEventListener('change', () => {
      const f = inp.files && inp.files[0];
      if (!f) return;
      const reader = new FileReader();
      reader.onload = () => {
        const [w, h] = _targetWH();
        setFr(frameImage(holder, String(reader.result || ''), w, h));
      };
      reader.readAsDataURL(f);
    });
  }

  async function _loadConfig() {
    try { _cfg = await (await fetch('/api/video/config')).json(); } catch (_) { _cfg = null; }
    const sizeSel = _el('vg-size');
    if (sizeSel && _cfg && Array.isArray(_cfg.sizes)) {
      const cur = sizeSel.value;
      sizeSel.innerHTML = '';
      _cfg.sizes.forEach((s) => {
        const o = document.createElement('option');
        o.value = s.value; o.textContent = s.label;
        sizeSel.appendChild(o);
      });
      if (cur) sizeSel.value = cur;
    }
    const warn = _el('vg-config-warn');
    if (warn) warn.style.display = (_cfg && _cfg.video_model) ? 'none' : 'block';
    // Erststart-Hinweis: Modell konfiguriert, aber noch nicht im HF-Cache.
    const fr = _el('vg-firstrun');
    if (fr) fr.style.display = (_cfg && _cfg.video_model && _cfg.model_cached === false) ? 'block' : 'none';
  }

  function _validate() {
    const prompt = (_el('vg-prompt')?.value || '').trim();
    if (_mode === 't2v' && !prompt) { showToast('Bitte eine Beschreibung eingeben'); return null; }
    if ((_mode === 'i2v' || _mode === 'flf2v') && !_firstFr) { showToast('Bitte ein Startbild wählen'); return null; }
    if (_mode === 'flf2v' && !_lastFr) { showToast('Bitte auch ein Endbild wählen'); return null; }
    const body = {
      mode: _mode, prompt,
      size: _el('vg-size')?.value || '720p',
      frames: parseInt(_el('vg-frames')?.value, 10) || 81,
      fps: parseInt(_el('vg-fps')?.value, 10) || 16,
      steps: parseInt(_el('vg-steps')?.value, 10) || 30,
      memory_saver: !!_el('vg-memsave')?.checked,
    };
    const seed = parseInt(_el('vg-seed')?.value, 10);
    if (!Number.isNaN(seed)) body.seed = seed;
    if (_mode !== 't2v') body.first = _firstFr.export();
    if (_mode === 'flf2v') body.last = _lastFr.export();
    return body;
  }

  async function _run() {
    if (_busy) { showToast('Ein Video wird bereits erzeugt'); return; }
    const body = _validate();
    if (!body) return;
    const btn = _el('vg-run');
    const status = _el('vg-status');
    const result = _el('vg-result');
    if (result) result.innerHTML = '';
    _busy = true;
    if (btn) { btn.disabled = true; btn.dataset._t = btn.textContent; btn.textContent = '… erzeugt'; }
    if (status) { status.style.display = 'block'; status.textContent = 'Video wird vorbereitet …'; }
    try {
      // Optional: Prompt per LLM ausformulieren.
      if (_el('vg-enhance')?.checked && body.prompt) {
        if (status) status.textContent = 'Prompt wird verbessert …';
        const e = await enhancePrompt(body.prompt, body.mode);
        body.prompt = e.prompt;
        if (_el('vg-prompt')) _el('vg-prompt').value = body.prompt;
      }
      if (status) status.textContent = 'Video wird erzeugt … (das lokale Modell rechnet, das kann einige Minuten dauern)';
      const resp = await fetch('/api/video/generate', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      if (!resp.ok || !resp.body) {
        let m = 'HTTP ' + resp.status; try { m = (await resp.json()).detail || m; } catch (_) {}
        throw new Error(m);
      }
      const reader = resp.body.getReader();
      const dec = new TextDecoder();
      let buf = '', videoUrl = '';
      while (true) {
        const { done, value } = await reader.read(); if (done) break;
        buf += dec.decode(value, { stream: true });
        const lines = buf.split('\n'); buf = lines.pop();
        for (const line of lines) {
          if (!line.startsWith('data: ')) continue;
          let ev; try { ev = JSON.parse(line.slice(6)); } catch (_) { continue; }
          if (ev.type === 'progress') {
            if (status) {
              const s = ev.elapsed || 0;
              status.textContent = (s >= 20)
                ? `Falls Erststart: das Modell wird geladen (mehrere GB, Netzwerk ausgelastet) – bitte warten. Läuft: ${s} s`
                : `Video wird erzeugt … ${s} s`;
            }
          } else if (ev.type === 'video') {
            videoUrl = ev.video_url || '';
          } else if (ev.type === 'error') {
            throw new Error(ev.message || 'unbekannter Fehler');
          }
        }
      }
      if (!videoUrl) throw new Error('kein Video erhalten');
      if (status) status.style.display = 'none';
      _showResult(videoUrl);
    } catch (e) {
      if (status) { status.style.display = 'block'; status.textContent = 'Fehlgeschlagen: ' + e.message; }
    } finally {
      _busy = false;
      if (btn) { btn.disabled = false; btn.textContent = btn.dataset._t || '🎬 Video erzeugen'; }
    }
  }

  function _showResult(url) {
    const result = _el('vg-result');
    if (!result) return;
    result.innerHTML = '';
    const vid = document.createElement('video');
    vid.src = url; vid.controls = true; vid.autoplay = true; vid.loop = true; vid.muted = true;
    vid.style.cssText = 'max-width:100%;border-radius:8px;display:block';
    result.appendChild(vid);
    const bar = document.createElement('div');
    bar.style.cssText = 'margin-top:8px';
    const dl = document.createElement('a');
    dl.href = url; dl.download = 'video.mp4'; dl.className = 'export-btn';
    dl.textContent = '⬇ Video herunterladen';
    bar.appendChild(dl);
    result.appendChild(bar);
  }

  function init() {
    _mountFramer('vg-first', 'vg-first-frame', (fr) => { _firstFr = fr; });
    _mountFramer('vg-last', 'vg-last-frame', (fr) => { _lastFr = fr; });
    _el('vg-mode-flf2v')?.addEventListener('click', () => _setMode('flf2v'));
    _el('vg-mode-i2v')?.addEventListener('click', () => _setMode('i2v'));
    _el('vg-mode-t2v')?.addEventListener('click', () => _setMode('t2v'));
    _el('vg-run')?.addEventListener('click', _run);
    _el('vg-size')?.addEventListener('change', () => {
      const [w, h] = _targetWH();
      if (_firstFr) _firstFr.setTarget(w, h);
      if (_lastFr) _lastFr.setTarget(w, h);
    });
    _setMode('flf2v');
    _loadConfig();
  }

  function refresh() {
    _loadConfig();
    if (_firstFr) _firstFr.relayout();
    if (_lastFr) _lastFr.relayout();
  }

  return { init, refresh, frameImage, enhancePrompt, promptPresets, appendPrompt };
})();
