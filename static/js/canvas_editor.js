/* AI_Framework_Thomas — WYSIWYG-Editor für Präsentationsfolien
   Klick auf eine Textregion der Folie → Overlay-Textfeld an Ort und Stelle.
   Klick auf die Bildregion → Bild tauschen. Zusätzlich eine Folien-Toolbar
   (verschieben, löschen, Bild tauschen, Text neu generieren). */

const CanvasEditor = (() => {

  let _overlay = null;     // aktives Bearbeitungs-Textfeld
  let _toolbar = null;
  let _busyRegen = false;

  /* ── Geometrie: Canvas-interne Koordinaten ↔ Bildschirm ──────── */
  function _scale() {
    const canvas = CanvasRenderer.getCanvasEl();
    const { W, H } = CanvasRenderer.getDims();
    const rect = canvas.getBoundingClientRect();
    const area = document.getElementById('canvas-area').getBoundingClientRect();
    return {
      sx: rect.width / W,
      sy: rect.height / H,
      offX: rect.left - area.left,
      offY: rect.top - area.top,
      rect,
    };
  }

  /* ── Klick auf Canvas → Region treffen ───────────────────────── */
  function _onCanvasClick(e) {
    if (_overlay) return;                       // läuft bereits eine Bearbeitung
    const data = CanvasRenderer.getCurrentData();
    if (!data || data.type !== 'presentation') return;

    const { sx, sy, rect } = _scale();
    const cx = (e.clientX - rect.left) / sx;
    const cy = (e.clientY - rect.top) / sy;

    // Letzte (oberste) passende Region gewinnt
    const regions = CanvasRenderer.getEditRegions();
    let hit = null;
    for (const r of regions) {
      if (cx >= r.x && cx <= r.x + r.w && cy >= r.y && cy <= r.y + r.h) hit = r;
    }
    if (!hit) return;

    if (hit.kind === 'image') { _swapImage(); return; }
    _openEditor(hit);
  }

  /* ── Text-Overlay öffnen ─────────────────────────────────────── */
  function _openEditor(region) {
    const slide = CanvasRenderer.getCurrentSlide();
    if (!slide) return;

    let value = '';
    if (region.field === 'bullets') value = (slide.bullets || []).join('\n');
    else value = slide[region.field] || '';

    const { sx, sy, offX, offY } = _scale();
    const ta = document.createElement('textarea');
    ta.className = 'slide-edit-overlay';
    ta.value = value;
    ta.style.left   = (offX + region.x * sx) + 'px';
    ta.style.top    = (offY + region.y * sy) + 'px';
    ta.style.width  = (region.w * sx) + 'px';
    ta.style.height = (region.h * sy) + 'px';
    ta.style.fontSize = Math.max(12, Math.round(18 * sy)) + 'px';

    document.getElementById('canvas-area').appendChild(ta);
    _overlay = ta;
    ta.focus();
    ta.select();

    let committed = false;
    const commit = () => {
      if (committed) return;
      committed = true;
      CanvasRenderer.setField(region.field, ta.value);
      _close();
    };
    const cancel = () => { committed = true; _close(); };

    ta.addEventListener('keydown', ev => {
      if (ev.key === 'Escape') { ev.preventDefault(); cancel(); }
      // Einzeilige Felder: Enter bestätigt; mehrzeilige: Strg+Enter
      const multiline = region.field === 'left' || region.field === 'bullets';
      if (ev.key === 'Enter' && (!multiline || ev.ctrlKey)) { ev.preventDefault(); commit(); }
    });
    ta.addEventListener('blur', commit);
  }

  function _close() {
    if (_overlay) { _overlay.remove(); _overlay = null; }
  }

  /* ── Bild tauschen ───────────────────────────────────────────── */
  function _swapImage() {
    const inp = document.createElement('input');
    inp.type = 'file';
    inp.accept = 'image/*';
    inp.onchange = () => {
      const f = inp.files[0];
      if (!f) return;
      const r = new FileReader();
      r.onload = e => {
        const slide = CanvasRenderer.getCurrentSlide();
        if (slide) slide._source = f.name;
        CanvasRenderer.setField('image_right', e.target.result);
      };
      r.readAsDataURL(f);
    };
    inp.click();
  }

  /* ── Text per KI neu generieren ──────────────────────────────── */
  async function _regenerate() {
    if (_busyRegen) return;
    const data = CanvasRenderer.getCurrentData();
    const slide = CanvasRenderer.getCurrentSlide();
    if (!slide || slide.layout !== 'two-column' || !slide.image_right) {
      showToast('Nur für Bildfolien verfügbar');
      return;
    }
    _busyRegen = true;
    showToast('🧠 Text wird neu generiert…');
    try {
      const r = await fetch('/api/analyze-image', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          image: slide.image_right,
          system_prompt: data?.persona || '',
          filename: slide._source || '',
          topic: data?.topic || '',
        }),
      });
      if (!r.ok) throw new Error('HTTP ' + r.status);
      const a = await r.json();
      const lines = [...(a.bullets || [])];
      if (a.caption) lines.push(a.caption);
      slide.title = a.title || slide.title;
      slide.left = lines.join('\n');
      slide._caption = a.caption || '';
      CanvasRenderer.rerender();
      showToast('✓ Text aktualisiert');
    } catch (e) {
      showToast('Neugenerierung fehlgeschlagen: ' + e.message);
    } finally {
      _busyRegen = false;
    }
  }

  /* ── Folien-Toolbar ──────────────────────────────────────────── */
  function _buildToolbar() {
    const area = document.getElementById('canvas-area');
    if (!area) return;
    _toolbar = document.createElement('div');
    _toolbar.id = 'slide-edit-toolbar';
    _toolbar.style.display = 'none';
    _toolbar.innerHTML = `
      <span class="set-hint">✎ Klick auf Text bearbeitet</span>
      <button data-act="left"  title="Folie nach vorne">◀</button>
      <button data-act="right" title="Folie nach hinten">▶</button>
      <button data-act="image" title="Bild tauschen">🖼</button>
      <button data-act="regen" title="Text neu generieren">✨</button>
      <button data-act="del"   title="Folie löschen">🗑</button>`;
    area.appendChild(_toolbar);

    _toolbar.addEventListener('click', ev => {
      const act = ev.target.closest('button')?.dataset.act;
      if (!act) return;
      if (act === 'left')  CanvasRenderer.moveSlide(-1);
      else if (act === 'right') CanvasRenderer.moveSlide(1);
      else if (act === 'image') _swapImage();
      else if (act === 'regen') _regenerate();
      else if (act === 'del') {
        if (confirm('Diese Folie löschen?')) CanvasRenderer.deleteSlide();
      }
      refresh();
    });
  }

  /* Toolbar-Sichtbarkeit/Status aktualisieren */
  function refresh() {
    // Toolbar neu aufbauen, falls sie (z.B. durch Spreadsheet-Render) entfernt wurde
    if (!_toolbar || !_toolbar.isConnected) _buildToolbar();
    if (!_toolbar) return;
    const data = CanvasRenderer.getCurrentData();
    const isPres = data && data.type === 'presentation';
    _toolbar.style.display = isPres ? 'flex' : 'none';
    if (!isPres) { _close(); return; }
    const slide = CanvasRenderer.getCurrentSlide();
    const isImg = slide && slide.layout === 'two-column' && slide.image_right;
    _toolbar.querySelector('[data-act="image"]').style.display = isImg ? '' : 'none';
    _toolbar.querySelector('[data-act="regen"]').style.display = isImg ? '' : 'none';
  }

  /* ── init ────────────────────────────────────────────────────── */
  function init() {
    const canvas = CanvasRenderer.getCanvasEl();
    if (!canvas) return;
    _buildToolbar();
    canvas.addEventListener('click', _onCanvasClick);
    canvas.style.cursor = 'pointer';

    // Bei Folienwechsel über die Navigationspfeile Toolbar/Overlay aktualisieren
    document.getElementById('btn-prev-slide')?.addEventListener('click', () => { _close(); refresh(); });
    document.getElementById('btn-next-slide')?.addEventListener('click', () => { _close(); refresh(); });

    // Editoränderungen → Toolbar aktualisieren (+ Autosave-Hook möglich)
    CanvasRenderer.setOnChange(() => refresh());
  }

  return { init, refresh };

})();
