/* AI_Framework_Thomas — Präsentations-Assistent (Tabellen-Ansicht) */

const PresentationAssistant = (() => {

  /* ── Zustand ──────────────────────────────────────────────────── */
  let _presName  = '';
  let _theme     = 'dark';
  let _rows      = [
    { type: 'text', title: '', content: '', image_data: null, image_filename: '' },
    { type: 'text', title: '', content: '', image_data: null, image_filename: '' },
    { type: 'text', title: '', content: '', image_data: null, image_filename: '' },
  ];
  let _generating = false;

  /* ── Tabelle rendern ─────────────────────────────────────────── */
  function _render() {
    const container = document.getElementById('pres-table-container');
    if (!container) return;

    const displayName = escHtml(_presName || '(Präsentationstitel)');

    let html = `<table class="pres-slide-table">
      <thead><tr>
        <th class="pres-th-num">#</th>
        <th class="pres-th-type">Typ</th>
        <th class="pres-th-title">Titel</th>
        <th>Inhalt / Thema für KI</th>
        <th class="pres-th-img">Bild</th>
        <th class="pres-th-del"></th>
      </tr></thead>
      <tbody>`;

    // Deckblatt (gesperrt)
    html += `<tr class="pres-row-fixed">
      <td class="pres-td-num pres-td-muted">0</td>
      <td><span class="pres-type-badge">Deckblatt</span></td>
      <td colspan="2"><span class="pres-auto-label">${displayName} · Profil + Projekt (automatisch)</span></td>
      <td></td><td></td>
    </tr>`;

    // Inhaltsfolien
    for (let i = 0; i < _rows.length; i++) {
      const row = _rows[i];
      const needsImg = row.type === 'two-column' || row.type === 'image';
      let imgCell = '';
      if (needsImg) {
        if (row.image_data) {
          imgCell = `<span class="pres-img-ok" data-idx="${i}" title="Bild: ${escHtml(row.image_filename || '')}" style="cursor:pointer" title="Klick zum Ändern">✓</span>`;
        } else {
          imgCell = `<button class="btn-pres-img pres-icon-btn" data-idx="${i}" title="Bild hochladen">📎</button>`;
        }
      }

      html += `<tr class="pres-row-content">
        <td class="pres-td-num pres-td-muted">${i + 1}</td>
        <td>
          <select class="pres-type-select" data-idx="${i}">
            <option value="text"       ${row.type === 'text'       ? 'selected' : ''}>Text</option>
            <option value="two-column" ${row.type === 'two-column' ? 'selected' : ''}>Text + Bild</option>
            <option value="image"      ${row.type === 'image'      ? 'selected' : ''}>Nur Bild</option>
            <option value="section"    ${row.type === 'section'    ? 'selected' : ''}>Abschnitt</option>
          </select>
        </td>
        <td>
          <input class="pres-title-input" data-idx="${i}" type="text"
            value="${escHtml(row.title)}" placeholder="Folientitel…" />
        </td>
        <td>
          <textarea class="pres-content-input" data-idx="${i}" rows="2"
            placeholder="${row.type === 'image' ? '(kein Text benötigt)' : 'Stichpunkte oder Thema für KI…'}">${escHtml(row.content)}</textarea>
        </td>
        <td class="pres-td-center">${imgCell}</td>
        <td class="pres-td-center">
          <button class="btn-pres-del-row pres-icon-btn" data-idx="${i}" title="Folie entfernen">🗑</button>
        </td>
      </tr>`;
    }

    // Abschlussfolie (gesperrt)
    html += `<tr class="pres-row-fixed">
      <td class="pres-td-num pres-td-muted">${_rows.length + 1}</td>
      <td><span class="pres-type-badge">Abschluss</span></td>
      <td colspan="2"><span class="pres-auto-label">Vielen Dank · automatisch aus Profil</span></td>
      <td></td><td></td>
    </tr>`;

    html += '</tbody></table>';
    container.innerHTML = html;

    // Events verdrahten
    container.querySelectorAll('.pres-type-select').forEach(sel => {
      sel.addEventListener('change', () => { _rows[+sel.dataset.idx].type = sel.value; _render(); });
    });
    container.querySelectorAll('.pres-title-input').forEach(inp => {
      inp.addEventListener('input', () => { _rows[+inp.dataset.idx].title = inp.value; });
    });
    container.querySelectorAll('.pres-content-input').forEach(ta => {
      ta.addEventListener('input', () => { _rows[+ta.dataset.idx].content = ta.value; });
    });
    container.querySelectorAll('.btn-pres-del-row').forEach(btn => {
      btn.addEventListener('click', () => { _rows.splice(+btn.dataset.idx, 1); _render(); });
    });
    container.querySelectorAll('.btn-pres-img, .pres-img-ok').forEach(btn => {
      btn.addEventListener('click', () => _triggerImageUpload(+btn.dataset.idx));
    });
  }

  /* ── Bild-Upload ─────────────────────────────────────────────── */
  function _triggerImageUpload(idx) {
    const inp = document.createElement('input');
    inp.type = 'file';
    inp.accept = 'image/*';
    inp.onchange = () => {
      const file = inp.files[0];
      if (!file) return;
      const reader = new FileReader();
      reader.onload = e => {
        _rows[idx].image_data     = e.target.result;
        _rows[idx].image_filename = file.name;
        _render();
        showToast(`✓ Bild für Folie ${idx + 1} gesetzt`);
      };
      reader.readAsDataURL(file);
    };
    inp.click();
  }

  /* ── Generierung ─────────────────────────────────────────────── */
  async function _generate() {
    if (_generating) return;

    const presName = (document.getElementById('pres-name-input')?.value || '').trim() || 'Präsentation';
    const theme    = document.getElementById('pres-theme-select')?.value || 'dark';
    _presName = presName;
    _theme    = theme;

    const validRows = _rows.filter(r => r.title.trim() || r.content.trim() || r.image_data);
    if (validRows.length === 0) {
      showToast('Bitte mindestens eine Folie mit Titel, Inhalt oder Bild ausfüllen');
      return;
    }

    _generating = true;
    const btn      = document.getElementById('btn-pres-generate');
    const progress = document.getElementById('pres-progress');
    if (btn) btn.disabled = true;

    const setProgress = msg => {
      if (progress) { progress.style.display = ''; progress.textContent = msg; }
    };

    const model = document.getElementById('model-select')?.value || 'qwen3.6-16k:latest';
    const slides = [];

    // Profil laden
    let profile = {};
    try {
      const r = await fetch('/api/profile');
      if (r.ok) profile = await r.json();
    } catch (_) {}
    const authorName = [profile.first_name, profile.last_name].filter(Boolean).join(' ');
    const company    = profile.company || '';
    const authorLine = [authorName, company].filter(Boolean).join(' · ');

    // Aktives Projekt
    let projectLine = '';
    try {
      const r = await fetch('/api/projects');
      if (r.ok) {
        const projs    = await r.json();
        const activeId = typeof Projects !== 'undefined' ? Projects.getActive() : null;
        if (activeId) {
          const proj = projs.find(p => p.id === activeId);
          if (proj) projectLine = proj.number ? `Projekt ${proj.number}` : proj.name;
        }
      }
    } catch (_) {}

    // Deckblatt
    setProgress('Deckblatt wird erstellt…');
    const coverContent = [authorLine, projectLine].filter(Boolean).join('  |  ');
    slides.push({ layout: 'title', title: presName, content: coverContent });

    // Inhaltsfolien — Folie für Folie
    const total = _rows.length + 2;
    for (let i = 0; i < _rows.length; i++) {
      setProgress(`Folie ${i + 2} von ${total} wird erstellt…`);
      try {
        slides.push(await _generateSlide(model, presName, _rows[i], i));
      } catch (_) {
        slides.push({ layout: 'bullets', title: _rows[i].title || `Folie ${i + 1}`, bullets: [_rows[i].content || ''] });
      }
    }

    // Abschlussfolie
    setProgress('Abschlussfolie wird erstellt…');
    slides.push({ layout: 'title', title: 'Vielen Dank', content: authorLine });

    // In Canvas rendern
    CanvasRenderer.render({ type: 'presentation', theme, slides });
    switchTab('canvas');
    document.getElementById('pres-assistant-panel').style.display = 'none';
    showToast(`✓ Präsentation erstellt: ${slides.length} Folien`);

    if (progress) progress.style.display = 'none';
    if (btn) btn.disabled = false;
    _generating = false;
  }

  async function _generateSlide(model, presName, row, index) {
    // Nur Bild: kein KI-Aufruf nötig
    if (row.type === 'image') {
      return {
        layout: 'two-column',
        title: row.title || 'Abbildung',
        left: row.content || '',
        image_right: row.image_data || null,
      };
    }

    // Leere Folie
    if (!row.content.trim() && !row.title.trim()) {
      return { layout: 'bullets', title: `Folie ${index + 1}`, bullets: [''] };
    }

    // Abschnitt ohne Inhalt: kein KI-Aufruf
    if (row.type === 'section' && !row.content.trim()) {
      return { layout: 'section', title: row.title, subtitle: '' };
    }

    let formatHint = '';
    if (row.type === 'text') {
      formatHint = 'Antworte NUR mit JSON: {"bullets":["Punkt 1","Punkt 2","Punkt 3","Punkt 4"]}. Max. 5 prägnante Stichpunkte.';
    } else if (row.type === 'two-column') {
      formatHint = 'Antworte NUR mit JSON: {"left":"• Punkt 1\\n• Punkt 2\\n• Punkt 3","right":"Kurze Ergänzung oder Fazit (2–3 Sätze)"}';
    } else if (row.type === 'section') {
      formatHint = 'Antworte NUR mit JSON: {"subtitle":"Kurzer Untertitel (max. eine Zeile)"}';
    }

    const resp = await fetch('/api/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        model,
        messages: [
          {
            role: 'system',
            content: `Du bist ein Präsentations-Texter. Erstelle professionellen, prägnanten Inhalt auf Deutsch für eine Folie in der Präsentation "${presName}". ${formatHint}`,
          },
          {
            role: 'user',
            content: `Folientitel: "${row.title || '(kein Titel)'}"\nThema / Hinweise: "${row.content}"`,
          },
        ],
        tools_enabled: false,
      }),
    });

    let fullText = '';
    const reader = resp.body.getReader();
    const dec    = new TextDecoder();
    let buf = '';
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
          if (ev.type === 'text') fullText += ev.content;
        } catch (_) {}
      }
    }

    let aiData = null;
    const m = fullText.match(/\{[\s\S]*\}/);
    if (m) { try { aiData = JSON.parse(m[0]); } catch (_) {} }

    if (row.type === 'text') {
      return { layout: 'bullets', title: row.title, bullets: aiData?.bullets || [row.content] };
    } else if (row.type === 'two-column') {
      return {
        layout: 'two-column',
        title: row.title,
        left: aiData?.left || row.content,
        right: row.image_data ? '' : (aiData?.right || ''),
        image_right: row.image_data || null,
      };
    } else if (row.type === 'section') {
      return { layout: 'section', title: row.title, subtitle: aiData?.subtitle || '' };
    }

    return { layout: 'bullets', title: row.title, bullets: [row.content] };
  }

  /* ── init ────────────────────────────────────────────────────── */
  function init() {
    const btnOpen  = document.getElementById('btn-pres-assistant');
    const panel    = document.getElementById('pres-assistant-panel');
    const btnClose = document.getElementById('btn-pres-assistant-close');

    if (!btnOpen) return;

    btnOpen.addEventListener('click', () => {
      if (!panel) return;
      const isOpen = panel.style.display !== 'none';
      panel.style.display = isOpen ? 'none' : 'flex';
      if (!isOpen) _render();
    });

    btnClose?.addEventListener('click', () => { if (panel) panel.style.display = 'none'; });

    document.getElementById('pres-name-input')?.addEventListener('input', e => {
      _presName = e.target.value;
      _render();
    });

    document.getElementById('btn-pres-add-slide')?.addEventListener('click', () => {
      _rows.push({ type: 'text', title: '', content: '', image_data: null, image_filename: '' });
      _render();
    });

    document.getElementById('btn-pres-generate')?.addEventListener('click', _generate);
  }

  return { init };

})();
