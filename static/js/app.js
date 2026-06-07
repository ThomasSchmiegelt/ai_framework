/* ── AI_Framework_Thomas — Haupt-App ─────────────────────────────────────────────────── */

const AppState = {
  activeAgentId: null,
  currentTab: 'chat',
};

// ── Hilfsfunktionen ────────────────────────────────────────────────────────

function escHtml(str) {
  return String(str)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

function showToast(msg, duration = 2500) {
  const toast = document.getElementById('toast');
  toast.textContent = msg;
  toast.classList.add('show');
  setTimeout(() => toast.classList.remove('show'), duration);
}

function switchTab(tabId) {
  // Don't switch to a hidden tab
  const hidden = (typeof Profile !== 'undefined' && Profile.get) ? (Profile.get().hidden_tabs || []) : [];
  if (hidden.includes(tabId)) return;
  AppState.currentTab = tabId;
  document.querySelectorAll('.tab-btn').forEach(btn => {
    btn.classList.toggle('active', btn.dataset.tab === tabId);
  });
  document.querySelectorAll('.panel').forEach(panel => {
    panel.classList.toggle('active', panel.id === `${tabId}-panel`);
  });
  if (tabId === 'canvas') {
    // Canvas wird erst jetzt sichtbar → Folie neu rendern, damit Formel-Overlays
    // korrekt zur aktuellen Anzeigegröße positioniert werden.
    if (typeof CanvasRenderer !== 'undefined' && CanvasRenderer.rerender) CanvasRenderer.rerender();
    if (typeof CanvasEditor !== 'undefined') CanvasEditor.refresh();
  }
  if (tabId === 'ide') {
    // Code-Editor (CodeMirror) neu vermessen, da das Panel vorher display:none war
    if (typeof CodeIDE !== 'undefined' && CodeIDE.refresh) setTimeout(CodeIDE.refresh, 0);
  }
}

// ── Modelle laden ──────────────────────────────────────────────────────────

async function loadModels() {
  // Es gibt keinen Sidebar-Modellselektor mehr — Modelle werden im Profil pro
  // Rolle gewählt. Funktion bleibt als sicherer No-op erhalten (Aufrufer unverändert).
  const sel = document.getElementById('model-select');
  if (!sel) return;
  try {
    const resp = await fetch('/api/models');
    const data = await resp.json();
    const models = data.models || [];
    sel.innerHTML = '';

    if (models.length === 0) {
      sel.innerHTML = '<option>Ollama nicht erreichbar</option>';
      return;
    }

    for (const m of models) {
      const opt = document.createElement('option');
      opt.value = m.name;
      opt.textContent = m.name;
      sel.appendChild(opt);
    }
    // Standardmäßig das Allgemein-Modell des Profils wählen, sonst das erste Listenelement
    const general = (typeof Profile !== 'undefined' && Profile.get) ? (Profile.get().model_general || '') : '';
    if (general && models.some(m => m.name === general)) {
      sel.value = general;
    } else if (sel.options.length > 0) {
      sel.options[0].selected = true;
    }
  } catch (e) {
    sel.innerHTML = '<option>Fehler beim Laden</option>';
  }
}

// ── Textarea auto-resize ───────────────────────────────────────────────────

function autoResizeTextarea(el) {
  el.style.height = 'auto';
  el.style.height = Math.min(el.scrollHeight, 200) + 'px';
}

// ── Export-Funktionen ──────────────────────────────────────────────────────

async function exportCanvas(format) {
  const data = CanvasRenderer.getCurrentData();
  if (!data) { showToast('Kein Inhalt zum Exportieren'); return; }

  const ext = format === 'latex' ? 'tex' : format;
  try {
    showToast(`Export als ${format.toUpperCase()} wird erstellt…`);
    const resp = await fetch(`/api/export/${format}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data),
    });

    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);

    const blob = await resp.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `ai_framework_thomas_export.${ext}`;
    a.click();
    URL.revokeObjectURL(url);
    showToast(`✓ Als ${format.toUpperCase()} exportiert`);
  } catch (e) {
    showToast(`Export fehlgeschlagen: ${e.message}`);
  }
}

async function exportChat(format) {
  // Aktuellen Chat-Inhalt als Dokument exportieren
  const messages = document.querySelectorAll('.message-row');
  const data = {
    title: 'AI_Framework_Thomas Chat-Export',
    messages: [],
  };

  messages.forEach(row => {
    const role = row.classList.contains('user') ? 'user' : 'assistant';
    const content = row.querySelector('.bubble-content')?.textContent || '';
    if (content.trim()) data.messages.push({ role, content });
  });

  try {
    const resp = await fetch(`/api/export/${format}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data),
    });
    const blob = await resp.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `chat_export.${format}`;
    a.click();
    URL.revokeObjectURL(url);
    showToast(`✓ Chat als ${format.toUpperCase()} exportiert`);
  } catch (e) {
    showToast(`Export fehlgeschlagen`);
  }
}

// Chat (komprimiert) als Quellmaterial in den Dokumentengenerator übernehmen
function chatToDocGen() {
  const rows = document.querySelectorAll('.message-row');
  const parts = [];
  rows.forEach(row => {
    const role = row.classList.contains('user') ? 'Nutzer' : 'Assistent';
    const content = row.querySelector('.bubble-content')?.textContent?.trim();
    if (content) parts.push(`**${role}:** ${content}`);
  });
  if (!parts.length) { showToast('Kein Chat-Inhalt vorhanden'); return; }
  const transcript = parts.join('\n\n');
  const title = (document.querySelector('.conv-item.active .conv-title')?.textContent || 'Chat').trim();
  if (typeof DocGen !== 'undefined' && DocGen.loadFromChat) {
    DocGen.loadFromChat(title, transcript);
  }
  document.querySelector('.tab-btn[data-tab="docgen"]')?.click();
  showToast('✓ Chat in den Dokumentengenerator übernommen');
}

// ── Suchergebnisse rendern ─────────────────────────────────────────────────

function renderSearchResults(results) {
  const container = document.getElementById('conversations');
  container.innerHTML = '';

  if (results.length === 0) {
    container.innerHTML = '<div style="padding:12px;color:var(--text-muted);font-size:13px">Keine Treffer</div>';
    return;
  }

  const label = document.createElement('div');
  label.className = 'conv-group-label';
  label.textContent = `${results.length} Treffer`;
  container.appendChild(label);

  for (const r of results) {
    const item = document.createElement('div');
    item.className = 'conv-item';
    item.innerHTML = `
      <span class="conv-title">${escHtml(r.title)}</span>
      ${r.excerpt ? `<div class="search-excerpt">${r.excerpt}</div>` : ''}
    `;
    item.addEventListener('click', async () => {
      document.querySelectorAll('.conv-item').forEach(el => el.classList.remove('active'));
      item.classList.add('active');
      await Chat.loadConversation(r.id);
      switchTab('chat');
    });
    container.appendChild(item);
  }
}

// Medizin-Tab wird von MedizinChat.init() (medizin.js) initialisiert

// ── Initialisierung ────────────────────────────────────────────────────────

// PWA: Service Worker registrieren (nur in Secure Contexts: HTTPS oder localhost;
// über http://<lan-ip> registriert der Browser keinen SW → App läuft trotzdem im
// Browser, ist dann aber nicht „installierbar"). Siehe BEDIENUNGSANLEITUNG (HTTPS).
if ('serviceWorker' in navigator && window.isSecureContext) {
  window.addEventListener('load', () => {
    navigator.serviceWorker.register('/sw.js').catch(() => {});
  });
}

document.addEventListener('DOMContentLoaded', async () => {
  // Profil zuerst laden, damit das Allgemein-Modell als Standard greift
  let _profile = {};
  if (typeof Profile !== 'undefined' && Profile.load) { try { _profile = await Profile.load(); } catch (_) {} }
  // Erst-Start-Einleitung beim ersten Mal (oder auf Wunsch) anzeigen
  if (typeof Onboarding !== 'undefined') { Onboarding.init(); Onboarding.maybeShow(_profile); }
  // Modelle und Agenten laden
  await loadModels();
  await AgentManager.load();
  await Chat.loadConversationList();

  // Suchfeld in der Sidebar
  const searchInput = document.getElementById('search-input');
  let searchTimer = null;
  searchInput.addEventListener('input', () => {
    clearTimeout(searchTimer);
    const q = searchInput.value.trim();
    if (!q) {
      Chat.loadConversationList();
      return;
    }
    searchTimer = setTimeout(async () => {
      try {
        const resp = await fetch(`/api/search?q=${encodeURIComponent(q)}`);
        if (!resp.ok) return;
        const results = await resp.json();
        renderSearchResults(results);
      } catch (_) {}
    }, 280);
  });
  searchInput.addEventListener('keydown', e => {
    if (e.key === 'Escape') { searchInput.value = ''; Chat.loadConversationList(); }
  });

  // Tab-Wechsel
  document.querySelectorAll('.tab-btn').forEach(btn => {
    btn.addEventListener('click', () => switchTab(btn.dataset.tab));
  });

  // Neues Gespräch
  document.getElementById('btn-new-chat').addEventListener('click', () => {
    Chat.newConversation();
    switchTab('chat');
  });


  // Nachricht senden
  const input = document.getElementById('message-input');
  input.addEventListener('keydown', e => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      Chat.sendMessage();
    }
  });
  input.addEventListener('input', () => autoResizeTextarea(input));

  document.getElementById('btn-send').addEventListener('click', () => Chat.sendOrAbort());

  // Such-Toggle
  document.getElementById('btn-search-toggle').addEventListener('click', function() {
    this.classList.toggle('active');
  });

  // Denkprozess-Toggle (Panel rechts ein-/ausblenden)
  document.getElementById('btn-thinking-toggle').addEventListener('click', () => Chat.toggleThinking());
  Chat.initThinking();

  // Datei-Upload
  const fileInput = document.getElementById('file-input');
  document.getElementById('btn-upload').addEventListener('click', () => fileInput.click());
  fileInput.addEventListener('change', async () => {
    for (const file of fileInput.files) {
      await Chat.uploadFile(file);
    }
    fileInput.value = '';
  });

  // Drag & Drop
  const inputBox = document.getElementById('input-box');
  inputBox.addEventListener('dragover', e => { e.preventDefault(); inputBox.style.borderColor = 'var(--accent)'; });
  inputBox.addEventListener('dragleave', () => { inputBox.style.borderColor = ''; });
  inputBox.addEventListener('drop', async e => {
    e.preventDefault();
    inputBox.style.borderColor = '';
    for (const file of e.dataTransfer.files) {
      await Chat.uploadFile(file);
    }
  });

  // Agenten-Verwaltung
  document.getElementById('btn-agents').addEventListener('click', () => switchTab('agents'));
  document.getElementById('btn-new-agent').addEventListener('click', () => AgentManager.openModal());
  // Gesetz-/Regel-Agent aus Datei: Datei wählen → an /api/agents/from-legal
  document.getElementById('btn-legal-agent')?.addEventListener('click', () =>
    document.getElementById('legal-agent-file')?.click());
  document.getElementById('legal-agent-file')?.addEventListener('change', e => {
    if (e.target.files[0]) AgentManager.createLegalAgent(e.target.files[0]);
    e.target.value = '';
  });
  AgentManager.initSearch();

  // Agenten-Selektor Änderung
  document.getElementById('agent-select').addEventListener('change', function() {
    AppState.activeAgentId = this.value || null;
    _syncAgentButtons();
  });

  // Schnellauswahl-Buttons in der Chatbox: Präsentations- / Programmier-Agent
  function _syncAgentButtons() {
    const cur = document.getElementById('agent-select').value;
    document.getElementById('btn-agent-presenter')?.classList.toggle('active', cur === 'presenter');
    document.getElementById('btn-agent-coder')?.classList.toggle('active', cur === 'coder');
  }
  function _toggleAgentButton(agentId) {
    const sel = document.getElementById('agent-select');
    if (!sel) return;
    // Falls der Agent (mangels Favorit) nicht in der Liste steht, Option ergänzen,
    // damit der Button unabhängig vom Favoritenstatus funktioniert.
    if (![...sel.options].some(o => o.value === agentId)) {
      const a = (typeof AgentManager !== 'undefined' ? AgentManager.getAgents() : [])
        .find(x => x.id === agentId);
      if (a) {
        const opt = document.createElement('option');
        opt.value = agentId;
        opt.textContent = `${a.icon || '🤖'} ${a.name}`;
        sel.appendChild(opt);
      }
    }
    // Erneuter Klick auf den aktiven Agenten hebt die Auswahl auf
    sel.value = (sel.value === agentId) ? '' : agentId;
    AppState.activeAgentId = sel.value || null;
    _syncAgentButtons();
  }
  document.getElementById('btn-agent-presenter')?.addEventListener('click', () => _toggleAgentButton('presenter'));
  document.getElementById('btn-agent-coder')?.addEventListener('click', () => _toggleAgentButton('coder'));

  // Folien-Navigation
  document.getElementById('btn-prev-slide').addEventListener('click', () => CanvasRenderer.prevSlide());
  document.getElementById('btn-next-slide').addEventListener('click', () => CanvasRenderer.nextSlide());

  // Export-Buttons
  document.getElementById('btn-export-pptx').addEventListener('click', () => exportCanvas('pptx'));
  document.getElementById('btn-export-pdf')?.addEventListener('click', () => exportCanvas('pdf'));
  document.getElementById('btn-export-latex')?.addEventListener('click', () => exportCanvas('latex'));
  document.getElementById('btn-export-xlsx').addEventListener('click', () => exportCanvas('xlsx'));
  document.getElementById('btn-export-docx').addEventListener('click', () => exportChat('docx'));
  document.getElementById('btn-chat-to-doc')?.addEventListener('click', chatToDocGen);

  // Modal
  document.getElementById('btn-modal-cancel').addEventListener('click', () => AgentManager.closeModal());
  document.getElementById('btn-modal-save').addEventListener('click', () => AgentManager.saveAgent());
  document.getElementById('btn-delete-agent').addEventListener('click', function() {
    AgentManager.deleteAgent(this.dataset.id);
  });
  document.getElementById('modal-overlay').addEventListener('click', e => {
    if (e.target === document.getElementById('modal-overlay')) AgentManager.closeModal();
  });
  document.getElementById('btn-generate-prompt').addEventListener('click', () => AgentManager.generatePrompt());
  document.getElementById('btn-agent-prompt-jury')?.addEventListener('click', () => {
    const txt = document.getElementById('field-agent-prompt')?.value || '';
    if (typeof Jury !== 'undefined') Jury.evaluate(txt, { title: 'System-Prompt-Prüfung' });
  });
  document.getElementById('btn-toggle-json').addEventListener('click', () => AgentManager.toggleJson());
  document.getElementById('btn-copy-json').addEventListener('click', () => AgentManager.copyJson());

  // Suggestion Chips
  document.querySelectorAll('.chip').forEach(chip => {
    chip.addEventListener('click', () => {
      document.getElementById('message-input').value = chip.textContent.trim();
      autoResizeTextarea(document.getElementById('message-input'));
      Chat.sendMessage();
    });
  });

  // Recherche-Modul
  Research.init();

  // RAG / Wissenssammlungen
  RAG.init();

  // Dokumentengenerator + Verfeinerungsschleife
  DocGen.init();
  if (typeof Refine !== 'undefined') Refine.init();

  // Mail → Wissensdatenbank
  if (typeof Mail !== 'undefined') Mail.init();

  // Profil + Projekte
  Profile.init();
  Projects.init();

  // Konversation Import/Export
  const convImportInput = document.getElementById('conv-import-input');
  document.getElementById('btn-import-conv')?.addEventListener('click', () => convImportInput?.click());
  convImportInput?.addEventListener('change', async e => {
    if (e.target.files[0]) await Chat.importConversation(e.target.files[0]);
    e.target.value = '';
  });
  document.getElementById('btn-export-all-conv')?.addEventListener('click', async () => {
    showToast('Exportiere alle Gespräche…');
    const a = document.createElement('a');
    a.href = '/api/conversations/export-all';
    a.download = 'ai_framework_thomas_gespraeche.zip';
    a.click();
  });

  // Vollständiges Backup / Restore
  document.getElementById('btn-backup-all')?.addEventListener('click', () => {
    showToast('Backup wird erstellt…');
    const a = document.createElement('a');
    a.href = '/api/backup';
    a.download = '';
    a.click();
  });

  const restoreInput = document.getElementById('restore-input');
  document.getElementById('btn-restore-all')?.addEventListener('click', () => restoreInput?.click());
  restoreInput?.addEventListener('change', async e => {
    const file = e.target.files[0];
    if (!file) return;
    e.target.value = '';
    showToast('Restore läuft…');
    try {
      const fd = new FormData();
      fd.append('file', file);
      const resp = await fetch('/api/restore', { method: 'POST', body: fd });
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const stats = await resp.json();
      const msg = [
        stats.profile   ? '✓ Profil'     : '',
        stats.projects  ? '✓ Projekte'   : '',
        stats.conversations ? `✓ ${stats.conversations} Gespräche` : '',
        stats.plans         ? `✓ ${stats.plans} Pläne`             : '',
        stats.agents        ? `✓ ${stats.agents} Agenten`          : '',
        stats.rag_collections ? `✓ ${stats.rag_collections} Wissensdatenbanken` : '',
        stats.profile_assets  ? `✓ ${stats.profile_assets} Branding-Bilder`     : '',
        stats.errors?.length ? `⚠ ${stats.errors.length} Fehler`  : '',
      ].filter(Boolean).join(' · ');
      showToast(msg || 'Restore abgeschlossen', 4000);
      await Chat.loadConversationList();
      await Projects.load();
      // Profil/Branding und Wissensdatenbanken nach dem Restore aktualisieren
      if (typeof Profile !== 'undefined' && Profile.load) { try { await Profile.load(); } catch (_) {} }
      if (typeof AgentManager !== 'undefined' && AgentManager.load) { try { await AgentManager.load(); } catch (_) {} }
      if (typeof RAG !== 'undefined' && RAG.loadCollections) { try { await RAG.loadCollections(); } catch (_) {} }
      const _logo = document.getElementById('sidebar-logo');
      if (_logo) { _logo.src = `/api/profile/asset/logo?t=${Date.now()}`; _logo.style.display = ''; }
      if (typeof CanvasRenderer !== 'undefined' && CanvasRenderer.reloadBranding) {
        try { CanvasRenderer.reloadBranding(); } catch (_) {}
      }
    } catch (err) {
      showToast('Restore fehlgeschlagen: ' + err.message);
    }
  });

  // Planer
  Planner.init();

  // Matrix-Recherche
  MatrixResearch.init();

  // Präsentations-Assistent
  PresentationAssistant.init();

  // Bebilderte Präsentation
  IllustratedPresentation.init();

  // WYSIWYG-Folieneditor
  CanvasEditor.init();

  // JSON-Editor
  JsonEditor.init();

  // Code-IDE
  CodeIDE.init();

  // Untertab-Umschalter im Code-Tab (IDE | JSON-Editor)
  document.querySelectorAll('.code-subtab').forEach(btn => {
    btn.addEventListener('click', () => {
      const target = btn.dataset.subtab;
      document.querySelectorAll('.code-subtab').forEach(b =>
        b.classList.toggle('active', b.dataset.subtab === target));
      document.querySelectorAll('.code-view').forEach(v =>
        v.classList.toggle('active', v.id === `code-view-${target}`));
      // CodeMirror muss neu vermessen, wenn der IDE-Unterview wieder sichtbar wird
      if (target === 'ide' && typeof CodeIDE !== 'undefined' && CodeIDE.refresh) CodeIDE.refresh();
    });
  });

  // Medizin-Tab initialisieren
  if (typeof MedizinChat !== 'undefined') MedizinChat.init();

  // Mathe-Tab initialisieren
  if (typeof MatheChat !== 'undefined') MatheChat.init();

  // Verzeichnis-Analyse + Morphologischer Kasten (optionale Tabs)
  if (typeof DirAnalysis !== 'undefined') DirAnalysis.init();
  if (typeof MorphBox !== 'undefined') MorphBox.init();

  // Jury (Bewertungs-Gremien)
  if (typeof Jury !== 'undefined') Jury.init();

  // Anleitung „Handy & FritzBox" als Fenster (Button im Nutzerprofil)
  (function wireGuide() {
    const overlay = document.getElementById('guide-modal-overlay');
    const content = document.getElementById('guide-content');
    const btnOpen = document.getElementById('btn-open-guide');
    const btnClose = document.getElementById('btn-guide-close');
    if (!overlay || !btnOpen) return;
    let loaded = false;
    const open = async () => {
      overlay.classList.add('active');
      if (loaded) return;
      try {
        const resp = await fetch('/api/help/guide');
        const data = await resp.json();
        const md = data.markdown || '';
        content.innerHTML = (window.marked && marked.parse) ? marked.parse(md) : md.replace(/\n/g, '<br>');
        content.querySelectorAll('a').forEach(a => { a.target = '_blank'; a.rel = 'noopener'; });
        loaded = true;
      } catch (_) {
        content.innerHTML = '<p>Anleitung konnte nicht geladen werden.</p>';
      }
    };
    const close = () => overlay.classList.remove('active');
    btnOpen.addEventListener('click', open);
    btnClose.addEventListener('click', close);
    overlay.addEventListener('click', e => { if (e.target === overlay) close(); });
  })();

  // Diagnose-Logger (als letztes, damit alle anderen Module bereits verdrahtet sind)
  await Logger.init();

  // Standard Tab
  switchTab('chat');
  Chat.newConversation();

  // Keyboard-Shortcuts
  document.addEventListener('keydown', e => {
    if (e.key === 'Escape') {
      AgentManager.closeModal();
      document.getElementById('profile-modal-overlay').classList.remove('active');
      document.getElementById('project-modal-overlay').classList.remove('active');
    }
    if ((e.ctrlKey || e.metaKey) && e.key === 'k') {
      e.preventDefault();
      Chat.newConversation();
      switchTab('chat');
      document.getElementById('message-input').focus();
    }
  });
});
