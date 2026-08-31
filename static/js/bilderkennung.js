/*
 * Bilderkennung / Objekt-Grounding — Tab-Modul + geteilter Zeichen-Helfer.
 *
 * Bild hochladen, Suchbegriff eingeben („such/markiere X") → das gefundene Objekt
 * wird mit Rahmen (Bounding-Box) markiert. Backend: POST /api/bilderkennung
 * (braucht ein grounding-fähiges Vision-Modell, z. B. qwen2.5vl).
 *
 * Öffentliche API:
 *   init()      — Tab verdrahten
 *   refresh()   — nach Tab-Wechsel Bild/Canvas neu vermessen (war display:none)
 *   detect(dataURL, query, model) → Promise<result>   (auch vom Chat genutzt)
 *   annotate(dataURL, boxes, dims) → Promise<PNG-dataURL>  (Chat rendert damit inline)
 */
const Bilderkennung = (function () {
  const _el = (id) => document.getElementById(id);
  const COLORS = ['#ef4444', '#22c55e', '#3b82f6', '#f59e0b', '#a855f7',
                  '#06b6d4', '#ec4899', '#84cc16', '#f97316', '#14b8a6'];

  let _dataUrl = '';       // aktuell angezeigtes Bild (Data-URL)
  let _img = null;         // geladenes Image-Element
  let _result = null;      // { boxes, image_w, image_h, answer, found }
  let _highlight = -1;     // hervorgehobene Box (Trefferliste-Klick)

  // ── Koordinaten: Boxen sind normiert 0–1000; liefert ein Modell Pixel
  //    (> 1000), wird per image_w/h normiert. Gibt Bruchteile 0..1 zurück.
  function _fractions(box, dims) {
    const hi = Math.max(box[0], box[1], box[2], box[3]);
    const divX = hi > 1000 ? (dims && dims.image_w ? dims.image_w : hi) : 1000;
    const divY = hi > 1000 ? (dims && dims.image_h ? dims.image_h : hi) : 1000;
    return [box[0] / divX, box[1] / divY, box[2] / divX, box[3] / divY];
  }

  // ── Boxen auf einen 2D-Kontext zeichnen (geteilt: Tab-Overlay + Chat-Annotation) ──
  function _drawBoxes(ctx, boxes, dims, w, h, highlight) {
    ctx.clearRect(0, 0, w, h);
    ctx.textBaseline = 'bottom';
    const fs = Math.max(11, Math.round(w / 45));
    ctx.font = `bold ${fs}px system-ui, sans-serif`;
    (boxes || []).forEach((b, i) => {
      const color = COLORS[i % COLORS.length];
      const [fx1, fy1, fx2, fy2] = _fractions(b.box, dims);
      const x = fx1 * w, y = fy1 * h, bw = (fx2 - fx1) * w, bh = (fy2 - fy1) * h;
      const on = (highlight === undefined || highlight < 0 || highlight === i);
      ctx.lineWidth = (highlight === i) ? 4 : 2.5;
      ctx.strokeStyle = color;
      ctx.globalAlpha = on ? 1 : 0.3;
      ctx.strokeRect(x, y, bw, bh);
      const label = (b.label || '').trim() || String(i + 1);
      const tw = ctx.measureText(label).width;
      const ty = Math.max(fs + 2, y);
      ctx.fillStyle = color;
      ctx.fillRect(x, ty - fs - 3, tw + 8, fs + 4);
      ctx.fillStyle = '#fff';
      ctx.fillText(label, x + 4, ty - 1);
      ctx.globalAlpha = 1;
    });
  }

  // ── Backend-Aufruf ─────────────────────────────────────────────────────────
  async function detect(dataUrl, query, model) {
    const resp = await fetch('/api/bilderkennung', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ image: dataUrl, query, model: model || undefined }),
    });
    if (!resp.ok) {
      let m = 'HTTP ' + resp.status;
      try { m = (await resp.json()).detail || m; } catch (_) {}
      throw new Error(m);
    }
    return resp.json();
  }

  // ── Bild + Boxen auf einen Off-Screen-Canvas komponieren → PNG (für den Chat) ──
  function annotate(dataUrl, boxes, dims) {
    return new Promise((resolve, reject) => {
      const im = new Image();
      im.onload = () => {
        const maxW = 1200;
        const scale = im.naturalWidth > maxW ? maxW / im.naturalWidth : 1;
        const w = Math.round(im.naturalWidth * scale);
        const h = Math.round(im.naturalHeight * scale);
        const cv = document.createElement('canvas');
        cv.width = w; cv.height = h;
        const ctx = cv.getContext('2d');
        ctx.drawImage(im, 0, 0, w, h);
        _drawBoxes(ctx, boxes, dims, w, h, -1);
        resolve(cv.toDataURL('image/png'));
      };
      im.onerror = () => reject(new Error('Bild konnte nicht geladen werden'));
      im.src = dataUrl;
    });
  }

  // ── Tab: Bild in den Stage zeichnen (Base-Canvas) + Overlay vorbereiten ──────
  function _renderImage() {
    if (!_img) return;
    const stage = _el('bk-stage');
    const base = _el('bk-base');
    const overlay = _el('bk-overlay');
    if (!stage || !base || !overlay) return;
    const avail = stage.clientWidth || stage.parentElement?.clientWidth || 720;
    const dw = Math.min(_img.naturalWidth, avail || 720, 960) || 480;
    const dh = Math.round(dw * (_img.naturalHeight / _img.naturalWidth));
    base.width = dw; base.height = dh;
    overlay.width = dw; overlay.height = dh;
    overlay.style.width = base.style.width = dw + 'px';
    overlay.style.height = base.style.height = dh + 'px';
    base.getContext('2d').drawImage(_img, 0, 0, dw, dh);
    _renderOverlay();
  }

  function _renderOverlay() {
    const overlay = _el('bk-overlay');
    if (!overlay) return;
    const ctx = overlay.getContext('2d');
    if (!_result || !_result.boxes || !_result.boxes.length) {
      ctx.clearRect(0, 0, overlay.width, overlay.height);
      return;
    }
    _drawBoxes(ctx, _result.boxes, _result, overlay.width, overlay.height, _highlight);
  }

  function _renderResult() {
    const ans = _el('bk-answer');
    const list = _el('bk-list');
    if (ans) {
      ans.textContent = _result ? (_result.answer || '') : '';
      ans.style.display = _result && _result.answer ? 'block' : 'none';
    }
    if (list) {
      list.innerHTML = '';
      const boxes = (_result && _result.boxes) || [];
      if (!boxes.length && _result) {
        const hint = document.createElement('div');
        hint.className = 'bk-hint';
        hint.innerHTML = 'Keine Markierung erhalten. Für zuverlässiges Markieren ein '
          + 'grounding-fähiges Vision-Modell nutzen, z. B. <code>ollama pull qwen2.5vl</code>.';
        list.appendChild(hint);
      }
      boxes.forEach((b, i) => {
        const row = document.createElement('div');
        row.className = 'bk-hit';
        const dot = document.createElement('span');
        dot.className = 'bk-dot';
        dot.style.background = COLORS[i % COLORS.length];
        const lbl = document.createElement('span');
        lbl.textContent = (b.label || '').trim() || ('Treffer ' + (i + 1));
        row.appendChild(dot); row.appendChild(lbl);
        row.addEventListener('mouseenter', () => { _highlight = i; _renderOverlay(); });
        row.addEventListener('mouseleave', () => { _highlight = -1; _renderOverlay(); });
        row.addEventListener('click', () => { _highlight = i; _renderOverlay(); });
        list.appendChild(row);
      });
    }
  }

  function _loadImageFromDataUrl(dataUrl) {
    _dataUrl = dataUrl;
    _result = null; _highlight = -1;
    _renderResult();
    const im = new Image();
    im.onload = () => { _img = im; _renderImage(); };
    im.src = dataUrl;
  }

  async function _run() {
    if (!_dataUrl) { if (typeof showToast === 'function') showToast('Erst ein Bild wählen'); return; }
    const query = (_el('bk-query')?.value || '').trim();
    if (!query) { if (typeof showToast === 'function') showToast('Was soll gesucht werden?'); _el('bk-query')?.focus(); return; }
    const btn = _el('bk-run');
    const model = _el('bk-model')?.value || '';
    if (btn) { btn.disabled = true; btn.dataset._t = btn.textContent; btn.textContent = '… suche'; }
    const ans = _el('bk-answer');
    if (ans) { ans.style.display = 'block'; ans.textContent = 'Das Modell durchsucht das Bild …'; }
    try {
      const data = await detect(_dataUrl, query, model);
      _result = data; _highlight = -1;
      if (data.tokens && typeof TokenMeter !== 'undefined') TokenMeter.add(data.tokens, 'Bilderkennung');
      _renderOverlay();
      _renderResult();
    } catch (e) {
      if (ans) { ans.style.display = 'block'; ans.textContent = 'Fehlgeschlagen: ' + e.message; }
    } finally {
      if (btn) { btn.disabled = false; btn.textContent = btn.dataset._t || '🔎 Markieren'; }
    }
  }

  async function _loadModels() {
    const sel = _el('bk-model');
    if (!sel) return;
    try {
      const data = await (await fetch('/api/bilderkennung/vision-models')).json();
      const models = data.models || [];
      sel.innerHTML = '<option value="">automatisch</option>';
      models.forEach((m) => {
        const o = document.createElement('option');
        o.value = m; o.textContent = m;
        sel.appendChild(o);
      });
      const rec = data.recommended || 'qwen2.5vl';
      const hit = models.find((m) => m.split(':')[0] === rec || m === rec);
      if (hit) sel.value = hit;
      const warn = _el('bk-model-warn');
      if (warn) warn.style.display = models.length ? 'none' : 'block';
    } catch (_) {
      sel.innerHTML = '<option value="">automatisch</option>';
    }
  }

  function init() {
    const file = _el('bk-file');
    if (file) {
      file.addEventListener('change', () => {
        const f = file.files && file.files[0];
        if (!f) return;
        const reader = new FileReader();
        reader.onload = () => _loadImageFromDataUrl(String(reader.result || ''));
        reader.readAsDataURL(f);
      });
    }
    _el('bk-run')?.addEventListener('click', _run);
    _el('bk-query')?.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') { e.preventDefault(); _run(); }
    });
    _loadModels();
  }

  function refresh() {
    // Panel war display:none → Stage misst jetzt korrekt; Bild neu zeichnen.
    if (_dataUrl && _img) _renderImage();
  }

  return { init, refresh, detect, annotate };
})();
