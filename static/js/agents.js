/* ── AI_Framework_Thomas Agenten-Manager ─────────────────────────────────────────────── */

const AgentManager = (() => {
  let agents = [];
  let editingId = null;
  let searchQuery = '';
  let activeCategory = '';

  const AVAILABLE_TOOLS = [
    { id: 'web_search',          label: '🔍 Websuche' },
    { id: 'calculate',           label: '🧮 Berechnung' },
    { id: 'create_presentation', label: '📊 Präsentation' },
    { id: 'create_spreadsheet',  label: '📋 Tabelle' },
  ];

  const CATEGORIES = [
    'Fertigung', 'Qualität', 'Dokumentation', 'Kommunikation',
    'Analyse', 'Recherche', 'Technik', 'Planer', 'Sonstige',
  ];

  const TEMPLATES = [
    {
      name: 'Protokoll-Assistent',
      icon: '📋',
      category: 'Qualität',
      description: 'Erstellt und prüft Besprechungsprotokolle auf Vollständigkeit.',
      system_prompt: 'Du bist ein präziser Protokoll-Assistent für Besprechungen und Arbeitsanweisungen. Du hilfst dabei, Protokolle zu strukturieren, auf Vollständigkeit zu prüfen und professionell zu formulieren. Achte auf Datum, Teilnehmer, Beschlüsse und Maßnahmen mit Verantwortlichen und Terminen. Antworte immer auf Deutsch.',
      tools: [],
    },
    {
      name: 'Dokument-Analyst',
      icon: '📄',
      category: 'Dokumentation',
      description: 'Analysiert hochgeladene Dokumente und beantwortet Fragen dazu.',
      system_prompt: 'Du bist ein Experte für Dokumentenanalyse. Du liest hochgeladene Dokumente sorgfältig und beantwortest Fragen dazu präzise. Du fasst Inhalte zusammen, extrahierst wichtige Informationen und hilfst beim Verstehen komplexer Texte. Antworte immer auf Deutsch.',
      tools: [],
    },
    {
      name: 'E-Mail-Assistent',
      icon: '✉️',
      category: 'Kommunikation',
      description: 'Formuliert und verbessert professionelle E-Mails und Geschäftsbriefe.',
      system_prompt: 'Du bist ein professioneller Schreibassistent für geschäftliche Kommunikation. Du hilfst dabei, E-Mails und Briefe klar, höflich und professionell zu formulieren. Passe den Ton an den Kontext an. Korrigiere Fehler und verbessere die Struktur. Antworte immer auf Deutsch.',
      tools: [],
    },
    {
      name: 'Wartungs-Assistent',
      icon: '🔧',
      category: 'Fertigung',
      description: 'Unterstützt bei Wartungsplanung, Fehlerdiagnose und technischer Dokumentation.',
      system_prompt: 'Du bist ein technischer Wartungsassistent für industrielle Maschinen und Anlagen. Du hilfst bei der Fehlerdiagnose, Wartungsplanung und Erstellung von Wartungsberichten. Du gibst konkrete, umsetzbare Empfehlungen auf Basis der beschriebenen Symptome oder Messwerte. Nutze das calculate-Tool für Berechnungen. Antworte immer auf Deutsch.',
      tools: ['web_search', 'calculate'],
    },
    {
      name: 'Checklisten-Ersteller',
      icon: '✅',
      category: 'Qualität',
      description: 'Erstellt strukturierte Checklisten für Prozesse, Audits und Qualitätssicherung.',
      system_prompt: 'Du bist ein Experte für Prozessdokumentation und Qualitätssicherung. Du erstellst klare, vollständige Checklisten für technische Prozesse, Audits und Qualitätsprüfungen. Strukturiere die Punkte logisch, nummeriere sie und füge Hinweise zu kritischen Schritten hinzu. Antworte immer auf Deutsch.',
      tools: [],
    },
    {
      name: 'Daten-Analyst',
      icon: '📈',
      category: 'Analyse',
      description: 'Analysiert Daten, erstellt Tabellen und berechnet Kennzahlen.',
      system_prompt: 'Du bist ein Datenanalyst. Du analysierst Daten, Tabellen und Messwerte aus hochgeladenen Dateien oder Texteingaben. Du berechnest Kennzahlen, erkennst Muster und erstellst übersichtliche Ergebnistabellen. Nutze das calculate-Tool für Berechnungen und create_spreadsheet für strukturierte Ausgaben. Antworte immer auf Deutsch.',
      tools: ['calculate', 'create_spreadsheet'],
    },
  ];

  // ── Laden & Grundstruktur ──────────────────────────────────────────────────

  async function load() {
    try {
      const resp = await fetch('/api/agents');
      agents = await resp.json();
      renderCategoryFilter();
      renderGrid();
      updateAgentSelector();
    } catch (e) {
      console.error('Agenten laden fehlgeschlagen', e);
    }
  }

  // ── Kategorie-Filter ───────────────────────────────────────────────────────

  function renderCategoryFilter() {
    const container = document.getElementById('category-filter');
    if (!container) return;
    container.innerHTML = '';

    const allBtn = _makeCatBtn('Alle', '');
    container.appendChild(allBtn);

    for (const cat of CATEGORIES) {
      container.appendChild(_makeCatBtn(cat, cat));
    }
  }

  function _makeCatBtn(label, cat) {
    const btn = document.createElement('button');
    btn.className = 'cat-btn' + (activeCategory === cat ? ' active' : '');
    btn.textContent = label;
    btn.dataset.cat = cat;
    btn.addEventListener('click', () => {
      activeCategory = cat;
      document.querySelectorAll('.cat-btn').forEach(b => b.classList.toggle('active', b.dataset.cat === cat));
      renderGrid();
    });
    return btn;
  }

  // ── Agenten-Grid ───────────────────────────────────────────────────────────

  function renderGrid() {
    const grid = document.getElementById('agents-grid');
    if (!grid) return;
    grid.innerHTML = '';

    const q = searchQuery.toLowerCase();
    const filtered = agents.filter(a => {
      const matchCat = !activeCategory || (a.category || 'Sonstige') === activeCategory;
      const matchQ = !q ||
        a.name.toLowerCase().includes(q) ||
        (a.description || '').toLowerCase().includes(q);
      return matchCat && matchQ;
    });

    if (filtered.length === 0) {
      const msg = agents.length === 0
        ? 'Noch keine Agenten. Erstelle deinen ersten!'
        : 'Keine Agenten gefunden.';
      grid.innerHTML = `<p style="color:var(--text-muted);grid-column:1/-1">${msg}</p>`;
      return;
    }

    for (const agent of filtered) {
      const card = document.createElement('div');
      card.className = 'agent-card';
      const cat = agent.category || 'Sonstige';
      card.innerHTML = `
        <div style="display:flex;align-items:flex-start;justify-content:space-between;gap:8px">
          <div class="agent-icon">${agent.icon || '🤖'}</div>
          <span class="agent-category-badge">${escHtml(cat)}</span>
        </div>
        <div class="agent-name">${escHtml(agent.name)}</div>
        <div class="agent-desc">${escHtml(agent.description)}</div>
        <div style="display:flex;flex-wrap:wrap;gap:4px;margin-top:2px">
          ${(agent.tools || []).map(t => {
            const tool = AVAILABLE_TOOLS.find(at => at.id === t);
            return `<span class="tool-badge">${tool ? tool.label : t}</span>`;
          }).join('')}
        </div>
        <div class="agent-actions">
          <button class="btn-fav-agent ${agent.favorite ? 'is-fav' : ''}" data-id="${agent.id}"
            title="${agent.favorite ? 'Favorit – erscheint in der Sidebar' : 'Als Favorit in die Sidebar'}">
            ${agent.favorite ? '⭐' : '☆'}
          </button>
          <button class="btn-use-agent" data-id="${agent.id}">Verwenden</button>
          <button class="btn-edit-agent" data-id="${agent.id}">✏️</button>
        </div>
      `;

      card.querySelector('.btn-fav-agent').addEventListener('click', e => {
        e.stopPropagation();
        toggleFavorite(agent.id);
      });
      card.querySelector('.btn-use-agent').addEventListener('click', e => {
        e.stopPropagation();
        selectAgent(agent.id);
      });
      card.querySelector('.btn-edit-agent').addEventListener('click', e => {
        e.stopPropagation();
        openModal(agent);
      });

      grid.appendChild(card);
    }
  }

  function selectAgent(id) {
    const agent = agents.find(a => a.id === id);
    if (!agent) return;
    const sel = document.getElementById('agent-select');
    if (sel) sel.value = id;
    switchTab('chat');
    AppState.activeAgentId = id;
    showToast(`Aktiv: ${agent.icon} ${agent.name}`);
  }

  // ── Favorit umschalten (nur Favoriten erscheinen in der Sidebar) ───────────

  async function toggleFavorite(id) {
    const agent = agents.find(a => a.id === id);
    if (!agent) return;
    const next = !agent.favorite;
    try {
      const resp = await fetch(`/api/agents/${id}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ...agent, favorite: next }),
      });
      const saved = await resp.json();
      const idx = agents.findIndex(a => a.id === id);
      if (idx >= 0) agents[idx] = saved;
      renderGrid();
      updateAgentSelector();
      showToast(next ? `⭐ „${agent.name}" ist jetzt Favorit` : `„${agent.name}" aus Favoriten entfernt`);
    } catch {
      showToast('Fehler beim Speichern des Favoriten');
    }
  }

  // ── Vorlagen ───────────────────────────────────────────────────────────────

  function renderTemplates() {
    const container = document.getElementById('template-chips');
    if (!container) return;
    container.innerHTML = '';
    for (const tmpl of TEMPLATES) {
      const chip = document.createElement('button');
      chip.className = 'template-chip';
      chip.title = tmpl.description;
      chip.innerHTML = `<span>${tmpl.icon}</span><span>${escHtml(tmpl.name)}</span>`;
      chip.addEventListener('click', () => applyTemplate(tmpl));
      container.appendChild(chip);
    }
  }

  function applyTemplate(tmpl) {
    document.getElementById('field-agent-name').value = tmpl.name;
    document.getElementById('field-agent-icon').value = tmpl.icon;
    document.getElementById('field-agent-desc').value = tmpl.description;
    document.getElementById('field-agent-category').value = tmpl.category;
    document.getElementById('field-agent-prompt').value = tmpl.system_prompt;
    const exTmpl = document.getElementById('field-agent-example-code');
    if (exTmpl) exTmpl.value = tmpl.example_code || '';
    document.getElementById('field-agent-task').value = '';

    document.querySelectorAll('#field-agent-tools .checkbox-item').forEach(el => {
      el.classList.toggle('checked', tmpl.tools.includes(el.dataset.value));
    });

    showToast(`Vorlage „${tmpl.name}" geladen`);
  }

  // ── Modal öffnen / schließen ───────────────────────────────────────────────

  function openModal(agent = null) {
    const isFromChat = agent && !agent.id;
    editingId = (agent && agent.id) ? agent.id : null;

    let title = 'Neuer Agent';
    if (isFromChat) title = '⚡ Skill aus Chat erstellen';
    else if (agent && agent.id) title = 'Agent bearbeiten';
    document.getElementById('modal-title').textContent = title;

    document.getElementById('field-agent-name').value = agent?.name ?? '';
    document.getElementById('field-agent-icon').value = agent?.icon ?? '🤖';
    document.getElementById('field-agent-desc').value = agent?.description ?? '';
    document.getElementById('field-agent-prompt').value = agent?.system_prompt ?? '';
    const exEl = document.getElementById('field-agent-example-code');
    if (exEl) exEl.value = agent?.example_code ?? '';
    document.getElementById('field-agent-model').value = agent?.model ?? '';
    document.getElementById('field-agent-category').value = agent?.category ?? 'Sonstige';
    document.getElementById('field-agent-task').value = '';

    // Vorlagen: nur bei komplett neuem Agent (nicht bei Chat-zu-Skill oder Bearbeiten)
    const tmplSection = document.getElementById('template-section');
    if (tmplSection) {
      const showTemplates = !agent;
      tmplSection.style.display = showTemplates ? 'block' : 'none';
      if (showTemplates) renderTemplates();
    }

    // JSON-Bereich zurücksetzen
    const jsonSection = document.getElementById('json-section');
    const jsonToggle = document.getElementById('btn-toggle-json');
    if (jsonSection) jsonSection.style.display = 'none';
    if (jsonToggle) {
      jsonToggle.textContent = '▶ JSON anzeigen';
      jsonToggle.style.display = (agent && agent.id) ? 'inline-block' : 'none';
    }

    // Tools-Checkboxen
    const toolsContainer = document.getElementById('field-agent-tools');
    toolsContainer.innerHTML = '';
    const agentTools = agent ? (agent.tools || []) : ['web_search', 'calculate'];
    for (const tool of AVAILABLE_TOOLS) {
      const item = document.createElement('div');
      item.className = 'checkbox-item' + (agentTools.includes(tool.id) ? ' checked' : '');
      item.dataset.value = tool.id;
      item.textContent = tool.label;
      item.addEventListener('click', () => item.classList.toggle('checked'));
      toolsContainer.appendChild(item);
    }

    // Wissensdatenbanken (RAG) befüllen + Bindung des Agenten vorauswählen
    _fillRagSelect(agent?.rag_collections || []);

    // Löschen-Button
    const btnDelete = document.getElementById('btn-delete-agent');
    btnDelete.style.display = (agent && agent.id) ? 'block' : 'none';
    btnDelete.dataset.id = agent?.id ?? '';

    document.getElementById('modal-overlay').classList.add('open');
  }

  // Wissensdatenbank-Mehrfachauswahl im Agenten-Modal füllen
  async function _fillRagSelect(selected) {
    const sel = document.getElementById('field-agent-rag');
    if (!sel) return;
    const chosen = new Set(selected || []);
    sel.innerHTML = '';
    try {
      const resp = await fetch('/api/rag/collections');
      const cols = await resp.json();
      (cols || []).forEach(c => {
        const o = document.createElement('option');
        o.value = c.id;
        o.textContent = c.name;
        if (chosen.has(c.id)) o.selected = true;
        sel.appendChild(o);
      });
    } catch (_) { /* ohne RAG-Liste bleibt die Auswahl leer */ }
  }

  function closeModal() {
    document.getElementById('modal-overlay').classList.remove('open');
    editingId = null;
  }

  // ── KI-Prompt-Generator ───────────────────────────────────────────────────

  async function generatePrompt() {
    const task = document.getElementById('field-agent-task').value.trim();
    if (!task) { showToast('Bitte zuerst die Aufgabe beschreiben'); return; }

    const btn = document.getElementById('btn-generate-prompt');
    btn.disabled = true;
    btn.textContent = '⏳ Generiere…';

    try {
      const resp = await fetch('/api/agents/generate-prompt', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ description: task }),
      });
      const data = await resp.json();
      if (data.prompt) {
        document.getElementById('field-agent-prompt').value = data.prompt;
        showToast('System-Prompt generiert');
      } else {
        showToast('Generierung fehlgeschlagen');
      }
    } catch {
      showToast('Fehler bei der Generierung');
    } finally {
      btn.disabled = false;
      btn.textContent = '✨ System-Prompt automatisch generieren';
    }
  }

  // ── JSON anzeigen / kopieren ───────────────────────────────────────────────

  function toggleJson() {
    const section = document.getElementById('json-section');
    const btn = document.getElementById('btn-toggle-json');
    if (!section) return;

    const open = section.style.display !== 'none';
    if (open) {
      section.style.display = 'none';
      btn.textContent = '▶ JSON anzeigen';
    } else {
      document.getElementById('field-agent-json').value = JSON.stringify(_currentFieldData(), null, 2);
      section.style.display = 'block';
      btn.textContent = '▼ JSON ausblenden';
    }
  }

  async function copyJson() {
    const text = document.getElementById('field-agent-json').value;
    try {
      await navigator.clipboard.writeText(text);
    } catch {
      const ta = document.getElementById('field-agent-json');
      ta.select();
      document.execCommand('copy');
    }
    showToast('JSON kopiert');
  }

  function _currentFieldData() {
    const tools = Array.from(
      document.querySelectorAll('#field-agent-tools .checkbox-item.checked')
    ).map(el => el.dataset.value);
    // Favorit-Status des bearbeiteten Agenten erhalten (wird nicht im Formular editiert)
    const existing = editingId ? agents.find(a => a.id === editingId) : null;
    const ragSel = document.getElementById('field-agent-rag');
    const rag_collections = ragSel
      ? Array.from(ragSel.selectedOptions).map(o => o.value)
      : (existing?.rag_collections || []);
    return {
      id: editingId || '(neu)',
      name: document.getElementById('field-agent-name').value.trim(),
      icon: document.getElementById('field-agent-icon').value || '🤖',
      description: document.getElementById('field-agent-desc').value.trim(),
      category: document.getElementById('field-agent-category').value,
      system_prompt: document.getElementById('field-agent-prompt').value.trim(),
      example_code: document.getElementById('field-agent-example-code')?.value || '',
      model: document.getElementById('field-agent-model').value.trim() || null,
      tools,
      rag_collections,
      favorite: existing ? !!existing.favorite : false,
    };
  }

  // ── Speichern / Löschen ───────────────────────────────────────────────────

  async function saveAgent() {
    const name = document.getElementById('field-agent-name').value.trim();
    if (!name) { showToast('Name ist erforderlich'); return; }

    const agentData = { ..._currentFieldData(), id: editingId || undefined };

    try {
      const url = editingId ? `/api/agents/${editingId}` : '/api/agents';
      const method = editingId ? 'PUT' : 'POST';
      const resp = await fetch(url, {
        method,
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(agentData),
      });
      const saved = await resp.json();

      if (editingId) {
        const idx = agents.findIndex(a => a.id === editingId);
        if (idx >= 0) agents[idx] = saved;
      } else {
        agents.unshift(saved);
      }

      renderGrid();
      updateAgentSelector();
      closeModal();
      showToast(`Agent „${saved.name}" gespeichert`);
    } catch {
      showToast('Fehler beim Speichern');
    }
  }

  async function deleteAgent(id) {
    if (!confirm('Agent wirklich löschen?')) return;
    try {
      await fetch(`/api/agents/${id}`, { method: 'DELETE' });
      agents = agents.filter(a => a.id !== id);
      renderGrid();
      updateAgentSelector();
      closeModal();
      showToast('Agent gelöscht');
    } catch {
      showToast('Fehler beim Löschen');
    }
  }

  // ── Agenten-Selector in Sidebar ───────────────────────────────────────────

  function updateAgentSelector() {
    const sel = document.getElementById('agent-select');
    if (!sel) return;
    const current = sel.value;
    sel.innerHTML = '<option value="">— Kein Agent —</option>'
      + '<option value="__adaptive__">🧠 Adaptiver Agent</option>';
    // Nur als Favorit markierte Agenten erscheinen in der Sidebar
    for (const agent of agents.filter(a => a.favorite)) {
      const opt = document.createElement('option');
      opt.value = agent.id;
      opt.textContent = `${agent.icon} ${agent.name}`;
      sel.appendChild(opt);
    }
    sel.value = current;
  }

  // ── Agenten-Suche im Panel ────────────────────────────────────────────────

  function initSearch() {
    const input = document.getElementById('agents-search');
    if (!input) return;
    input.addEventListener('input', e => {
      searchQuery = e.target.value.trim();
      renderGrid();
    });
  }

  function getAgents() { return agents; }

  // Aus einem hochgeladenen Gesetzestext / einer Norm einen spezialisierten
  // Agenten erzeugen. Der Server konvertiert den Text nach Markdown und legt ihn
  // je nach Länge direkt in den Prompt oder in eine eigene Wissensdatenbank.
  async function createLegalAgent(file) {
    if (!file) return;
    const def = (file.name || 'Dokument').replace(/\.[^.]+$/, '');
    const title = prompt('Titel des Dokuments (z. B. „DIN EN ISO 9001", „BGB" oder „Gerthsen Physik"):', def);
    if (title === null) return;   // abgebrochen
    // Fachgebiet/Rolle: leer ⇒ juristischer Modus (rückwärtskompatibel)
    const domain = prompt('Fachgebiet / Rolle des Experten (z. B. „Recht", „Physik", „Medizin"; leer = Recht):', 'Recht');
    if (domain === null) return;
    showToast('📚 Dokument-Experte wird erstellt…');
    const fd = new FormData();
    fd.append('file', file);
    fd.append('title', (title || '').trim());
    fd.append('domain', (domain || '').trim());
    try {
      const r = await fetch('/api/agents/from-legal', { method: 'POST', body: fd });
      if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || ('HTTP ' + r.status));
      const data = await r.json();
      await load();
      const where = data.mode === 'rag'
        ? `mit eigener Wissensdatenbank „${data.coll_prefix || 'Doku'}: ${data.name}"`
        : 'mit Text direkt im Prompt';
      showToast(`✓ Experte „${data.name}" (${data.category || 'Recht'}) erstellt (${where}, ${data.chars} Zeichen)`);
    } catch (e) {
      showToast('Erstellung fehlgeschlagen: ' + e.message);
    }
  }

  return {
    load,
    openModal,
    closeModal,
    saveAgent,
    deleteAgent,
    updateAgentSelector,
    generatePrompt,
    toggleJson,
    copyJson,
    getAgents,
    initSearch,
    createLegalAgent,
  };
})();
