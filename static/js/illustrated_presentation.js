/* AI_Framework_Thomas — Bebilderte Präsentation
   Wählt einen Bilderordner, leitet aus der Beschreibung einen Analyse-Experten
   (Persona) ab, analysiert jedes Bild per Vision-Modell und baut pro Bild eine
   Zweispalter-Folie (Bild | kurzer Text). Aufbau: Deckblatt → Beschreibung →
   Bildfolien → Abschluss. */

const IllustratedPresentation = (() => {

  const IMG_RE = /\.(jpe?g|png|gif|webp|bmp)$/i;
  let _images = [];          // [{name, dataUrl}]
  let _agents = [];          // vorhandene Agenten (für festen Experten)
  let _busy = false;

  /* ── Panel öffnen/schließen ──────────────────────────────────── */
  function _togglePanel() {
    const panel = document.getElementById('illus-pres-panel');
    if (!panel) return;
    // Anderes Panel ggf. schließen
    const other = document.getElementById('pres-assistant-panel');
    if (other) other.style.display = 'none';
    panel.style.display = panel.style.display === 'none' ? 'flex' : 'none';
  }

  /* ── Bilderordner wählen (webkitdirectory) ───────────────────── */
  function _pickFolder() {
    const inp = document.createElement('input');
    inp.type = 'file';
    inp.webkitdirectory = true;
    inp.multiple = true;
    inp.onchange = async () => {
      const files = [...inp.files].filter(f => IMG_RE.test(f.name));
      files.sort((a, b) => a.name.localeCompare(b.name, 'de', { numeric: true }));
      const info = document.getElementById('illus-folder-info');
      if (!files.length) {
        _images = [];
        if (info) info.textContent = 'Keine Bilder im Ordner gefunden';
        return;
      }
      if (info) info.textContent = `${files.length} Bilder werden geladen…`;
      _images = [];
      for (const f of files) {
        const dataUrl = await _readAsDataURL(f);
        _images.push({ name: f.name, dataUrl });
      }
      if (info) info.textContent = `✓ ${_images.length} Bilder geladen`;
    };
    inp.click();
  }

  function _readAsDataURL(file) {
    return new Promise(res => {
      const r = new FileReader();
      r.onload = e => res(e.target.result);
      r.readAsDataURL(file);
    });
  }

  /* ── Festen Agenten als Experten anbieten ────────────────────── */
  async function _loadAgents() {
    const sel = document.getElementById('illus-agent');
    if (!sel) return;
    try {
      let agents = await (await fetch('/api/agents')).json();
      if (!Array.isArray(agents)) agents = [];
      _agents = agents;
      sel.innerHTML = '<option value="">— Experte aus Beschreibung ableiten —</option>' +
        agents.map(a => `<option value="${a.id}">${(a.icon || '🤖')} ${(a.name || a.id)}</option>`).join('');
    } catch (_) {}
  }

  function _onAgentPick() {
    const sel = document.getElementById('illus-agent');
    const a = _agents.find(x => x.id === sel?.value);
    const ta = document.getElementById('illus-persona');
    const nm = document.getElementById('illus-persona-name');
    if (a) {
      if (ta) ta.value = a.system_prompt || '';
      if (nm) nm.textContent = `Experte: ${a.name || a.id}`;
    } else if (nm) {
      nm.textContent = '';
    }
  }

  /* ── Analyse-Experte (Persona) ableiten ──────────────────────── */
  async function _derivePersona() {
    const desc = (document.getElementById('illus-desc')?.value || '').trim();
    if (!desc) { showToast('Bitte zuerst eine Beschreibung eingeben'); return; }

    const btn = document.getElementById('btn-illus-persona');
    if (btn) { btn.disabled = true; btn.textContent = '🧠 wird abgeleitet…'; }
    try {
      const r = await fetch('/api/derive-persona', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ description: desc }),
      });
      if (!r.ok) throw new Error('HTTP ' + r.status);
      const data = await r.json();
      const ta = document.getElementById('illus-persona');
      if (ta) ta.value = data.system_prompt || '';
      const nm = document.getElementById('illus-persona-name');
      if (nm) nm.textContent = data.persona_name ? `Experte: ${data.persona_name}` : '';
    } catch (e) {
      showToast('Persona-Ableitung fehlgeschlagen: ' + e.message);
    } finally {
      if (btn) { btn.disabled = false; btn.textContent = '🧠 Analyse-Experte ableiten'; }
    }
  }

  /* ── Hilfen für Folienaufbau ─────────────────────────────────── */
  async function _loadAuthorLine() {
    let profile = {};
    try { const r = await fetch('/api/profile'); if (r.ok) profile = await r.json(); } catch (_) {}
    const author = [profile.first_name, profile.last_name].filter(Boolean).join(' ');
    const company = profile.company || '';
    let projectLine = '';
    try {
      const r = await fetch('/api/projects');
      if (r.ok) {
        const projs = await r.json();
        const activeId = typeof Projects !== 'undefined' ? Projects.getActive() : null;
        if (activeId) {
          const p = projs.find(x => x.id === activeId);
          if (p) projectLine = p.number ? `Projekt ${p.number}` : p.name;
        }
      }
    } catch (_) {}
    return { authorLine: [author, company].filter(Boolean).join(' · '), projectLine };
  }

  function _sentences(text, max = 5) {
    return text.split(/(?<=[.!?])\s+/).map(s => s.trim()).filter(Boolean).slice(0, max);
  }

  /* ── Generierung ─────────────────────────────────────────────── */
  async function _generate() {
    if (_busy) return;

    const title = (document.getElementById('illus-title')?.value || '').trim() || 'Präsentation';
    const desc  = (document.getElementById('illus-desc')?.value || '').trim();
    const theme = document.getElementById('illus-theme')?.value || 'dark';
    if (!_images.length) { showToast('Bitte zuerst einen Bilderordner wählen'); return; }

    // Persona sicherstellen
    let persona = (document.getElementById('illus-persona')?.value || '').trim();
    if (!persona && desc) {
      await _derivePersona();
      persona = (document.getElementById('illus-persona')?.value || '').trim();
    }

    _busy = true;
    const btn = document.getElementById('btn-illus-generate');
    const prog = document.getElementById('illus-progress');
    if (btn) btn.disabled = true;
    const setProg = m => { if (prog) { prog.style.display = ''; prog.textContent = m; } };

    const { authorLine } = await _loadAuthorLine();
    const slides = [];

    // Deckblatt — nur der Titel
    slides.push({ layout: 'title', title });

    // Beschreibungsfolie — vom Experten neu formuliert
    if (desc) {
      setProg('Einleitung wird vom Experten formuliert…');
      let introBullets = _sentences(desc);
      try {
        const r = await fetch('/api/illus/intro', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ description: desc, system_prompt: persona, title }),
        });
        if (r.ok) {
          const d = await r.json();
          if (Array.isArray(d.bullets) && d.bullets.length) introBullets = d.bullets;
        }
      } catch (_) {}
      slides.push({ layout: 'bullets', title: 'Über diese Präsentation', bullets: introBullets });
    }

    // Bildfolien
    const topic = [title, desc].filter(Boolean).join(' — ');
    for (let i = 0; i < _images.length; i++) {
      const img = _images[i];
      setProg(`Bild ${i + 1} von ${_images.length} wird analysiert… (${img.name})`);
      let analysis = { title: 'Abbildung', bullets: [], caption: '' };
      try {
        const r = await fetch('/api/analyze-image', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ image: img.dataUrl, system_prompt: persona, filename: img.name, topic }),
        });
        if (r.ok) analysis = await r.json();
      } catch (_) {}

      const lines = [...(analysis.bullets || [])];
      if (analysis.caption) lines.push(analysis.caption);
      slides.push({
        layout: 'two-column',
        title: analysis.title || 'Abbildung',
        left: lines.join('\n'),
        image_right: img.dataUrl,
        _source: img.name,           // für Editor / spätere Neugenerierung
        _caption: analysis.caption || '',
      });
    }

    // Abschluss
    slides.push({ layout: 'title', title: 'Vielen Dank', content: authorLine });

    CanvasRenderer.render({ type: 'presentation', theme, slides, persona, topic });
    switchTab('canvas');
    document.getElementById('illus-pres-panel').style.display = 'none';
    showToast(`✓ Bebilderte Präsentation erstellt: ${slides.length} Folien`);

    if (prog) prog.style.display = 'none';
    if (btn) btn.disabled = false;
    _busy = false;
  }

  /* ── init ────────────────────────────────────────────────────── */
  function init() {
    const btnOpen = document.getElementById('btn-illus-pres');
    if (!btnOpen) return;
    btnOpen.addEventListener('click', () => { _togglePanel(); _loadAgents(); });
    document.getElementById('btn-illus-close')?.addEventListener('click', () => {
      document.getElementById('illus-pres-panel').style.display = 'none';
    });
    document.getElementById('btn-illus-folder')?.addEventListener('click', _pickFolder);
    document.getElementById('btn-illus-persona')?.addEventListener('click', _derivePersona);
    document.getElementById('btn-illus-generate')?.addEventListener('click', _generate);
    document.getElementById('illus-agent')?.addEventListener('change', _onAgentPick);
    _loadAgents();
  }

  return { init };

})();
