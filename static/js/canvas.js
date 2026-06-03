/* ── AI_Framework_Thomas Canvas Renderer ─────────────────────────────────────────────
   Rendert Präsentationen und Tabellen auf HTML5-Canvas
   ─────────────────────────────────────────────────────────────────────── */

const CanvasRenderer = (() => {
  // Canvas-Element aus dem DOM holen und dauerhaft referenzieren
  const canvas = document.getElementById('main-canvas');
  const ctx = canvas.getContext('2d');
  const W = 1280, H = 720;
  canvas.width = W;
  canvas.height = H;

  // Stellt sicher dass der Canvas im canvas-area Container ist
  function _ensureCanvasAttached() {
    const area = document.getElementById('canvas-area');
    if (!canvas.parentElement || canvas.parentElement.id !== 'canvas-area') {
      area.innerHTML = '';
      area.appendChild(canvas);
    }
    canvas.style.display = 'block';
  }

  /* ── Branding-Bilder aus dem Nutzerprofil vorladen ─────────────────────── */
  // Deckblatt/Kopfzeile/Logo werden im Profil hochgeladen (data/profile_assets).
  // Sind keine hinterlegt, bleiben die Folien schlicht ohne Bild.
  const _corpImg = { deckblatt: null, kopfzeile: null, logo: null };
  const _KOPFZEILE_H = Math.round(W * 90 / 1341);  // ~86 px bei W=1280

  function reloadBranding() {
    const specs = [['deckblatt', 'cover'], ['kopfzeile', 'header'], ['logo', 'logo']];
    for (const [key, kind] of specs) {
      const img = new Image();
      img.onload = () => { _corpImg[key] = img; };
      img.onerror = () => { _corpImg[key] = null; };
      img.src = `/api/profile/asset/${kind}?t=${Date.now()}`;
    }
  }
  reloadBranding();

  // Skaliert der Canvas (Tab-Wechsel sichtbar werden, Fenster-Resize), müssen die
  // Formel-Overlays neu positioniert werden → komplette Folie neu rendern.
  try {
    let _ro;
    new ResizeObserver(() => {
      clearTimeout(_ro);
      _ro = setTimeout(() => { if (currentData && currentData.type === 'presentation') rerender(); }, 60);
    }).observe(canvas);
  } catch (_) {}

  /* Folienpalette aus den aktiven CSS-Variablen (folgt dem Profil-Modus) */
  function _cssVar(name, fb) {
    try {
      const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
      return v || fb;
    } catch (_) { return fb; }
  }
  function _pal() {
    return {
      dark:   _cssVar('--bg-input', '#11314f'),
      deep:   _cssVar('--bg-hover', '#003a74'),
      accent: _cssVar('--accent',   '#3b76ba'),
      light:  _cssVar('--text',     '#d4e8f8'),
    };
  }

  const THEMES = {
    /* Standard-Palette */
    corporate: {
      bg:      '#ffffff',
      bg2:     '#11314f',
      title:   '#ffffff',
      text:    '#11314f',
      dim:     '#6c6f76',
      accent:  '#3b76ba',
      accent2: '#003a74',
      border:  'rgba(59,118,186,0.25)',
    },
    dark: {
      bg:      '#212121',
      bg2:     '#2a2a2a',
      title:   '#ffffff',
      text:    '#ececec',
      dim:     '#8e8ea0',
      accent:  '#10a37f',
      accent2: '#0d7a5f',
      border:  'rgba(255,255,255,0.12)',
    },
    blue: {
      bg:      '#0d2540',
      bg2:     '#11314f',
      title:   '#d4e8f8',
      text:    '#a3c8eb',
      dim:     '#6c6f76',
      accent:  '#3b76ba',
      accent2: '#003a74',
      border:  'rgba(59,118,186,0.28)',
    },
    light: {
      bg:      '#f9f9f9',
      bg2:     '#eef0f4',
      title:   '#111111',
      text:    '#333333',
      dim:     '#666666',
      accent:  '#10a37f',
      accent2: '#0d8f6e',
      border:  'rgba(0,0,0,0.12)',
    },
    green: {
      bg:      '#0d1f16',
      bg2:     '#122b1e',
      title:   '#e0ffe8',
      text:    '#c8efd4',
      dim:     '#7ab890',
      accent:  '#2de08a',
      accent2: '#1bb870',
      border:  'rgba(45,224,138,0.25)',
    },
  };

  let currentData = null;
  let currentSlide = 0;
  let _editRegions = [];        // anklickbare Felder der aktuell gezeigten Folie
  let _onChange = null;         // Callback bei Folienänderung (für Editor/Persistenz)

  /* Editierbares Feld der aktuellen Folie registrieren (Canvas-Koordinaten) */
  function _region(field, x, y, w, h, kind = 'text') {
    _editRegions.push({ field, x, y, w, h, kind });
  }

  // ── Public API ─────────────────────────────────────────────────────────────

  function render(data) {
    currentData = data;
    currentSlide = 0;

    // Folien-Navigation nur bei Präsentationen einblenden
    const slideNav = document.getElementById('slide-nav');
    if (slideNav) slideNav.style.display = data.type === 'presentation' ? 'flex' : 'none';

    if (data.type === 'presentation') {
      _ensureCanvasAttached();
      renderPresentation(data, 0);
      updateSlideNav();
    } else if (data.type === 'spreadsheet') {
      renderSpreadsheet(data);
    }
  }

  /* ── Editor-API ──────────────────────────────────────────────────────────── */
  function setOnChange(fn) { _onChange = fn; }
  function getEditRegions() { return _editRegions; }
  function getCanvasEl() { return canvas; }
  function getDims() { return { W, H }; }
  function getCurrentSlideIndex() { return currentSlide; }
  function getCurrentSlide() {
    if (!currentData || currentData.type !== 'presentation') return null;
    return currentData.slides[currentSlide] || null;
  }

  function rerender() {
    if (!currentData || currentData.type !== 'presentation') return;
    renderPresentation(currentData, currentSlide);
    updateSlideNav();
  }

  function goToSlide(idx) {
    if (!currentData || currentData.type !== 'presentation') return;
    currentSlide = Math.max(0, Math.min(idx, currentData.slides.length - 1));
    rerender();
  }

  /* Feld der aktuellen Folie ändern und neu rendern */
  function setField(field, value) {
    const slide = getCurrentSlide();
    if (!slide) return;
    if (field === 'bullets') {
      slide.bullets = String(value).split('\n').map(s => s.trim()).filter(Boolean);
    } else {
      slide[field] = value;
    }
    rerender();
    if (_onChange) _onChange(currentData);
  }

  /* Folie verschieben / löschen */
  function moveSlide(dir) {
    if (!currentData || currentData.type !== 'presentation') return;
    const slides = currentData.slides;
    const j = currentSlide + dir;
    if (j < 0 || j >= slides.length) {
      if (typeof showToast === 'function') {
        showToast(dir < 0 ? 'Bereits die erste Folie' : 'Bereits die letzte Folie');
      }
      return;
    }
    [slides[currentSlide], slides[j]] = [slides[j], slides[currentSlide]];
    currentSlide = j;
    rerender();
    if (_onChange) _onChange(currentData);
    if (typeof showToast === 'function') {
      showToast(`Folie verschoben → Position ${j + 1} / ${slides.length}`);
    }
  }

  function deleteSlide() {
    if (!currentData || currentData.type !== 'presentation') return;
    const slides = currentData.slides;
    if (slides.length <= 1) return;
    slides.splice(currentSlide, 1);
    if (currentSlide >= slides.length) currentSlide = slides.length - 1;
    rerender();
    if (_onChange) _onChange(currentData);
  }

  function nextSlide() {
    if (!currentData || currentData.type !== 'presentation') return;
    if (currentSlide < currentData.slides.length - 1) {
      currentSlide++;
      renderPresentation(currentData, currentSlide);
      updateSlideNav();
    }
  }

  function prevSlide() {
    if (!currentData || currentData.type !== 'presentation') return;
    if (currentSlide > 0) {
      currentSlide--;
      renderPresentation(currentData, currentSlide);
      updateSlideNav();
    }
  }

  function getCurrentData() { return currentData; }

  // ── Hilfsfunktionen ────────────────────────────────────────────────────────

  function updateSlideNav() {
    if (!currentData || currentData.type !== 'presentation') return;
    const total = currentData.slides.length;
    document.getElementById('slide-counter').textContent = `${currentSlide + 1} / ${total}`;
    document.getElementById('btn-prev-slide').disabled = currentSlide === 0;
    document.getElementById('btn-next-slide').disabled = currentSlide === total - 1;
    document.getElementById('canvas-title').textContent = currentData.title || 'Präsentation';
  }

  function getTheme(name) {
    return THEMES[name] || THEMES.dark;
  }

  function clear(color) {
    ctx.fillStyle = color;
    ctx.fillRect(0, 0, W, H);
  }

  function drawRect(x, y, w, h, color, radius = 0) {
    ctx.fillStyle = color;
    if (radius > 0) {
      ctx.beginPath();
      ctx.roundRect(x, y, w, h, radius);
      ctx.fill();
    } else {
      ctx.fillRect(x, y, w, h);
    }
  }

  function drawText(text, x, y, opts = {}) {
    const {
      color = '#fff', size = 18, weight = 'normal', align = 'left',
      maxWidth = W, lineHeight = 1.45, font = 'system-ui, sans-serif'
    } = opts;
    ctx.fillStyle = color;
    ctx.font = `${weight} ${size}px ${font}`;
    ctx.textAlign = align;
    ctx.textBaseline = 'top';

    if (typeof text !== 'string') text = String(text);

    // Word wrap
    const words = text.split(' ');
    let line = '';
    let cy = y;
    for (const word of words) {
      const test = line + (line ? ' ' : '') + word;
      if (ctx.measureText(test).width > maxWidth && line) {
        ctx.fillText(line, x, cy, maxWidth);
        line = word;
        cy += size * lineHeight;
      } else {
        line = test;
      }
    }
    if (line) ctx.fillText(line, x, cy, maxWidth);
    return cy + size * lineHeight;
  }

  function drawAccentBar(x, y, h, color) {
    ctx.fillStyle = color;
    ctx.fillRect(x, y, 5, h);
  }

  /* ── Mathematische Formeln (KaTeX) als HTML-Overlay über dem Canvas ────────
     Canvas kann kein LaTeX zeichnen. KaTeX rendert aber synchron zu HTML, daher
     legen wir die Formeln als absolut positionierte, klickdurchlässige Elemente
     exakt über die Canvas-Positionen (in Anzeige-Pixeln, an die CSS-Skalierung
     des Canvas angepasst). Höhe wird sofort gemessen → korrekter vertikaler Fluss. */
  let _mathLayer = null;
  const _MATH_RE = /\$\$([\s\S]+?)\$\$|\\\[([\s\S]+?)\\\]|\$([^\n$]+?)\$|\\\(([^\n]+?)\\\)/g;

  function _esc(s) {
    return String(s).replace(/[&<>]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;' }[c]));
  }
  function _hasMath(s) {
    return typeof s === 'string' && typeof katex !== 'undefined'
        && /\$\$[\s\S]+?\$\$|\$[^\n$]+?\$|\\\([^\n]+?\\\)|\\\[[\s\S]+?\\\]/.test(s);
  }
  // Text mit eingebetteten Formeln → HTML (Text escaped, Formeln via KaTeX gerendert)
  function _texToHtml(text) {
    let out = '', last = 0, m;
    _MATH_RE.lastIndex = 0;
    while ((m = _MATH_RE.exec(text))) {
      out += _esc(text.slice(last, m.index));
      const display = m[1] !== undefined || m[2] !== undefined;
      const tex = m[1] !== undefined ? m[1] : m[2] !== undefined ? m[2]
                : m[3] !== undefined ? m[3] : m[4];
      try { out += katex.renderToString(tex, { displayMode: display, throwOnError: false }); }
      catch (_) { out += _esc(m[0]); }
      last = m.index + m[0].length;
    }
    out += _esc(text.slice(last));
    return out;
  }

  function _ensureMathLayer() {
    const area = document.getElementById('canvas-area');
    if (!_mathLayer || !_mathLayer.isConnected) {
      _mathLayer = document.createElement('div');
      _mathLayer.id = 'canvas-math-layer';
      _mathLayer.style.cssText = 'position:absolute;top:0;left:0;right:0;bottom:0;pointer-events:none;overflow:visible';
    }
    if (_mathLayer.parentElement !== area && area) area.appendChild(_mathLayer);
    return _mathLayer;
  }
  function _clearMathLayer() {
    if (_mathLayer) _mathLayer.innerHTML = '';
  }
  // Canvas-Skalierung/Offset (Anzeige-Pixel relativ zum positionierten canvas-area)
  function _metrics() {
    const sx = canvas.clientWidth / W;
    const sy = canvas.clientHeight / H;
    return { sx, sy, ox: canvas.offsetLeft, oy: canvas.offsetTop, visible: sx > 0 && sy > 0 };
  }

  // Formel-Zeile als Overlay platzieren; gibt die belegte Höhe in CANVAS-Pixeln zurück.
  function _placeMath(text, x, y, maxWidthCanvas, opts = {}) {
    const { color = '#11314f', size = 22, align = 'left' } = opts;
    const layer = _ensureMathLayer();
    const mtr = _metrics();
    const el = document.createElement('div');
    const wDisp = maxWidthCanvas * mtr.sx;
    el.className = 'canvas-math-line';
    el.style.cssText =
      `position:absolute;left:${mtr.ox + x * mtr.sx}px;top:${mtr.oy + y * mtr.sy}px;` +
      `width:${wDisp}px;color:${color};font-size:${size * mtr.sy}px;line-height:1.4;` +
      `text-align:${align};white-space:normal;overflow-wrap:break-word`;
    el.innerHTML = _texToHtml(text);
    layer.appendChild(el);
    // Höhe sofort messen; bei verstecktem Canvas (sy≈0) Heuristik, korrekt beim Re-Render.
    const hDisp = el.offsetHeight;
    return mtr.visible && hDisp > 0 ? hDisp / mtr.sy : size * 1.5;
  }

  // Zeichnet Text mit oder ohne Formeln; gibt End-Y (Canvas-Pixel) zurück.
  function _drawMaybeMath(text, x, y, opts = {}) {
    if (_hasMath(text)) {
      const h = _placeMath(text, x, y, opts.maxWidth || (W - x - 40), opts);
      return y + h;
    }
    return drawText(text, x, y, opts);
  }

  // ── Präsentation ───────────────────────────────────────────────────────────

  function renderPresentation(data, slideIdx) {
    const theme = getTheme(data.theme || 'dark');
    const slide = data.slides[slideIdx];
    if (!slide) return;

    _editRegions = [];   // Regionen der neuen Folie sammeln
    _clearMathLayer();   // Formel-Overlays der vorherigen Folie entfernen
    clear(theme.bg);
    drawSlide(slide, theme, data);
    drawSlideNumber(slideIdx, data.slides.length, theme);
    drawBrandingBar(theme, data.title);
  }

  /* Kopfzeile-Bild auf Canvas zeichnen */
  function _drawKopfzeile(slideH) {
    if (_corpImg.kopfzeile) {
      ctx.drawImage(_corpImg.kopfzeile, 0, 0, W, _KOPFZEILE_H);
    }
  }

  function drawBrandingBar(theme, presentationTitle) {
    // Untere Leiste in der Modus-Dunkelfarbe
    drawRect(0, H - 44, W, 44, _pal().dark);
    ctx.strokeStyle = 'rgba(163,200,235,0.2)';
    ctx.lineWidth = 1;
    ctx.beginPath(); ctx.moveTo(0, H - 44); ctx.lineTo(W, H - 44); ctx.stroke();

    // Logo/Branding kommt aus dem Nutzerprofil und steckt bereits in der
    // Kopfzeile (Vorlagen-Kopfzeile). Daher hier kein zusätzliches Logo.

    // Titel mittig
    if (presentationTitle) {
      ctx.fillStyle = '#6c6f76';
      ctx.font = '13px system-ui';
      ctx.textAlign = 'center';
      ctx.fillText(presentationTitle, W / 2, H - 22);
    }
  }

  function drawSlideNumber(idx, total, theme) {
    ctx.fillStyle = theme.dim;
    ctx.font = '12px system-ui';
    ctx.textAlign = 'right';
    ctx.textBaseline = 'middle';
    ctx.fillText(`${idx + 1} / ${total}`, W - 24, H - 22);
  }

  function drawSlide(slide, theme, data) {
    const layout = slide.layout || 'bullets';
    const slideH = H - 44; // ohne Branding-Bar

    if (layout === 'title') {
      drawTitleSlide(slide, theme, slideH);
    } else if (layout === 'section') {
      drawSectionSlide(slide, theme, slideH);
    } else if (layout === 'two-column') {
      drawTwoColumnSlide(slide, theme, slideH);
    } else if (layout === 'blank') {
      drawBlankSlide(slide, theme, slideH);
    } else {
      drawContentSlide(slide, theme, slideH);
    }
  }

  function drawTitleSlide(slide, theme, slideH) {
    // Editierbare Regionen (Titel + Untertitel/Content)
    _region('title', W * 0.06, slideH * 0.30, W * 0.60, 120);
    _region('content', W * 0.06, slideH * 0.30 + 120, W * 0.60, 80);

    if (_corpImg.deckblatt) {
      // Corporate Deckblatt als vollflächiger Hintergrund
      ctx.drawImage(_corpImg.deckblatt, 0, 0, W, slideH);

      // Titeltext in der blauen Hexagon-Fläche (linke Hälfte, vertikale Mitte)
      const titleY = slideH * 0.42;
      wrapTextCenter(ctx, slide.title || '', W * 0.33, titleY, W * 0.6, 50, '#ffffff', 'bold 50px system-ui');

      if (slide.content) {
        wrapTextCenter(ctx, slide.content, W * 0.33, titleY + 72, W * 0.6, 22,
                       'rgba(163,200,235,0.95)', '22px system-ui');
      }
    } else {
      // Fallback: Modus-Verlauf
      const P = _pal();
      const grad = ctx.createLinearGradient(0, 0, W, slideH);
      grad.addColorStop(0, P.dark);
      grad.addColorStop(1, P.deep);
      ctx.fillStyle = grad;
      ctx.fillRect(0, 0, W, slideH);

      const titleY = slide.content ? slideH / 2 - 50 : slideH / 2;
      wrapTextCenter(ctx, slide.title || '', W / 2, titleY, W - 200, 54, P.light, 'bold 54px system-ui');
      if (slide.content) {
        wrapTextCenter(ctx, slide.content, W / 2, slideH / 2 + 50, W - 300, 26, P.light, '26px system-ui');
      }
      drawRect(W / 2 - 60, slideH - 80, 120, 4, P.accent, 2);
    }
  }

  function drawSectionSlide(slide, theme, slideH) {
    // Kopfzeile oben
    _drawKopfzeile(slideH);
    const bodyY = _corpImg.kopfzeile ? _KOPFZEILE_H : 0;

    // Modus-Block darunter
    const P = _pal();
    const grad = ctx.createLinearGradient(0, bodyY, W, slideH);
    grad.addColorStop(0, P.deep);
    grad.addColorStop(1, P.dark);
    ctx.fillStyle = grad;
    ctx.fillRect(0, bodyY, W, slideH - bodyY);

    const midY = bodyY + (slideH - bodyY) / 2;
    _region('title', 60, midY - 60, W - 120, 70);
    _region('subtitle', 60, midY + 12, W - 120, 50);
    wrapTextCenter(ctx, slide.title || '', W / 2, midY - 30, W - 160, 44, P.light, 'bold 44px system-ui');
    const sub = slide.subtitle || slide.content || '';
    if (sub) {
      wrapTextCenter(ctx, sub, W / 2, midY + 32, W - 240, 22, 'rgba(163,200,235,0.85)', '22px system-ui');
    }
  }

  function drawContentSlide(slide, theme, slideH) {
    // Weißer Hintergrund
    ctx.fillStyle = '#ffffff';
    ctx.fillRect(0, 0, W, slideH);

    // Kopfzeile
    _drawKopfzeile(slideH);
    const hdrH = _corpImg.kopfzeile ? _KOPFZEILE_H : 0;

    // Titelstreifen in der Modus-Dunkelfarbe
    const P = _pal();
    const titleH = 72;
    drawRect(0, hdrH, W, titleH, P.dark);
    ctx.fillStyle = P.light;
    ctx.font = 'bold 28px system-ui';
    ctx.textAlign = 'left';
    ctx.textBaseline = 'middle';
    ctx.fillText(slide.title || '', 48, hdrH + titleH / 2);

    // Dünner Akzent-Strich
    drawRect(0, hdrH + titleH, W, 3, P.accent);

    // Editierbare Regionen
    _region('title', 0, hdrH, W, titleH);
    _region('bullets', 0, hdrH + titleH + 3, W, slideH - (hdrH + titleH + 3));

    // Inhalt
    const bullets  = slide.bullets || (slide.content ? slide.content.split('\n').filter(Boolean) : []);
    let y          = hdrH + titleH + 36;
    const lineH    = 44;
    const dotR     = 5;
    const indent   = 80;

    for (const bullet of bullets) {
      if (y > slideH - 40) break;
      ctx.fillStyle = P.accent;
      ctx.beginPath();
      ctx.arc(48, y + 10, dotR, 0, Math.PI * 2);
      ctx.fill();
      const endY = _drawMaybeMath(bullet, indent, y, {
        color: P.dark, size: 22, maxWidth: W - indent - 60, lineHeight: 1.4,
      });
      y = Math.max(endY, y + lineH) + 4;
    }
  }

  function drawTwoColumnSlide(slide, theme, slideH) {
    ctx.fillStyle = '#ffffff';
    ctx.fillRect(0, 0, W, slideH);

    _drawKopfzeile(slideH);
    const P = _pal();
    const hdrH  = _corpImg.kopfzeile ? _KOPFZEILE_H : 0;
    const titleH = 72;
    drawRect(0, hdrH, W, titleH, P.dark);
    ctx.fillStyle = P.light;
    ctx.font = 'bold 28px system-ui';
    ctx.textAlign = 'left';
    ctx.textBaseline = 'middle';
    ctx.fillText(slide.title || '', 48, hdrH + titleH / 2);
    drawRect(0, hdrH + titleH, W, 3, P.accent);

    const bodyY = hdrH + titleH + 3;
    const midX  = W / 2;

    // Editierbare Regionen
    _region('title', 0, hdrH, W, titleH);
    _region('left', 0, bodyY, midX, slideH - bodyY);
    _region('image_right', midX, bodyY, midX, slideH - bodyY, 'image');

    // Vertikale Trennlinie
    ctx.strokeStyle = 'rgba(59,118,186,0.25)';
    ctx.lineWidth = 1;
    ctx.setLineDash([6, 4]);
    ctx.beginPath();
    ctx.moveTo(midX, bodyY + 20);
    ctx.lineTo(midX, slideH - 20);
    ctx.stroke();
    ctx.setLineDash([]);

    // Bild rechts (wenn vorhanden)
    if (slide.image_right) {
      const img = new Image();
      img.onload = () => {
        const aspect = img.width / img.height;
        const maxW   = midX - 40;
        const maxH   = slideH - bodyY - 40;
        let dw = maxW, dh = dw / aspect;
        if (dh > maxH) { dh = maxH; dw = dh * aspect; }
        const dx = midX + (midX - dw) / 2;
        const dy = bodyY + (maxH - dh) / 2 + 20;
        ctx.drawImage(img, dx, dy, dw, dh);
      };
      img.src = slide.image_right;
    }

    // Links: Bullets
    const leftItems = (slide.left || '').split('\n').filter(Boolean);
    let ly = bodyY + 30;
    for (const item of leftItems) {
      if (ly > slideH - 40) break;
      ctx.fillStyle = P.accent;
      ctx.beginPath(); ctx.arc(44, ly + 8, 4, 0, Math.PI * 2); ctx.fill();
      ly = _drawMaybeMath(item, 62, ly, { color: P.dark, size: 20, maxWidth: midX - 80, lineHeight: 1.4 }) + 8;
    }

    // Rechts: Text (nur wenn kein Bild)
    if (!slide.image_right) {
      const rightItems = (slide.right || '').split('\n').filter(Boolean);
      let ry = bodyY + 30;
      for (const item of rightItems) {
        if (ry > slideH - 40) break;
        ctx.fillStyle = P.accent;
        ctx.beginPath(); ctx.arc(midX + 28, ry + 8, 4, 0, Math.PI * 2); ctx.fill();
        ry = _drawMaybeMath(item, midX + 46, ry, { color: P.dark, size: 20, maxWidth: midX - 80, lineHeight: 1.4 }) + 8;
      }
    }
  }

  function drawBlankSlide(slide, theme, slideH) {
    clear(theme.bg);
    if (slide.content) {
      _drawMaybeMath(slide.content, 60, 60, { color: theme.text, size: 22, maxWidth: W - 120 });
    }
  }

  // Hilfsfunktion: Text zentriert mit Wrap
  function wrapTextCenter(ctx, text, cx, cy, maxW, size, color, fontStr) {
    ctx.fillStyle = color;
    ctx.font = fontStr;
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';

    const words = text.split(' ');
    let line = '';
    let currentY = cy;
    const lineH = size * 1.4;

    for (const word of words) {
      const test = line + (line ? ' ' : '') + word;
      if (ctx.measureText(test).width > maxW && line) {
        ctx.fillText(line, cx, currentY);
        line = word;
        currentY += lineH;
      } else {
        line = test;
      }
    }
    if (line) ctx.fillText(line, cx, currentY);
  }

  // ── Spreadsheet ────────────────────────────────────────────────────────────

  function renderSpreadsheet(data) {
    document.getElementById('canvas-title').textContent = data.title || 'Tabelle';
    document.getElementById('slide-nav').style.display = 'none';
    _clearMathLayer();

    // Canvas sicher aus dem Container entfernen (nicht zerstören)
    const area = document.getElementById('canvas-area');
    if (canvas.parentElement) canvas.parentElement.removeChild(canvas);
    area.innerHTML = '';

    const container = document.createElement('div');
    container.id = 'sheet-container';

    const table = document.createElement('table');
    table.id = 'sheet-table';

    // Header
    const thead = document.createElement('thead');
    const hr = document.createElement('tr');
    for (const h of data.headers || []) {
      const th = document.createElement('th');
      th.textContent = h;
      hr.appendChild(th);
    }
    thead.appendChild(hr);
    table.appendChild(thead);

    // Rows
    const tbody = document.createElement('tbody');
    for (const row of data.rows || []) {
      const tr = document.createElement('tr');
      for (let ci = 0; ci < (data.headers || []).length; ci++) {
        const td = document.createElement('td');
        td.textContent = row[ci] !== undefined && row[ci] !== null ? String(row[ci]) : '';
        tr.appendChild(td);
      }
      tbody.appendChild(tr);
    }
    table.appendChild(tbody);
    container.appendChild(table);
    area.appendChild(container);
  }

  return {
    render, nextSlide, prevSlide, getCurrentData,
    // Editor-API
    setOnChange, getEditRegions, getCanvasEl, getDims,
    getCurrentSlide, getCurrentSlideIndex, goToSlide,
    setField, moveSlide, deleteSlide, rerender, reloadBranding,
  };
})();
