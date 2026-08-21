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

// Welche Funktion/Tab nutzt welche Modell-Rolle (Profil). Tabs ohne Eintrag
// nutzen die Allgemein-Rolle. Steuert das proaktive Vorwärmen beim Funktionswechsel.
const TAB_MODEL_ROLE = {
  chat: 'general', agents: 'general', recherche: 'science', rag: 'general',
  docgen: 'general', medizin: 'medical', mathe: 'science', ide: 'coding',
  planner: 'general', matrix: 'general', diranalyse: 'general',
  morph: 'general', jury: 'general', patente: 'general',
  rechnung: 'general', zeugnis: 'general',
  varianten: 'general', todo: 'general',
};
let _activeModelName = '';

// Beim Funktionswechsel das passende LLM vorab laden (Backend entlädt das alte).
// Fire-and-forget: blockiert die UI nicht; nur wenn sich das Modell wirklich ändert.
function _activateModelForTab(tabId) {
  if (typeof Profile === 'undefined' || !Profile.modelFor) return;
  const role = TAB_MODEL_ROLE[tabId] || 'general';
  const model = Profile.modelFor(role);
  if (!model || model === _activeModelName) return;
  _activeModelName = model;
  fetch('/api/model/activate', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ role }),
  }).catch(() => {});
}

function switchTab(tabId) {
  // Don't switch to a hidden tab
  const hidden = (typeof Profile !== 'undefined' && Profile.get) ? (Profile.get().hidden_tabs || []) : [];
  if (hidden.includes(tabId)) return;
  AppState.currentTab = tabId;
  _activateModelForTab(tabId);
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

// Gemeinsamer Text-Download (MD/CSV) ohne Server-Roundtrip
function downloadTextBlob(name, content, mime) {
  const blob = new Blob([content], { type: mime || 'text/plain;charset=utf-8' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url; a.download = name; a.click();
  URL.revokeObjectURL(url);
}

// Ganzen Chat als Markdown-Datei speichern (nutzt das von chat.js an den Bubbles
// hinterlegte Roh-Markdown _rawMd; Fallback: sichtbarer Text)
function exportChatMd() {
  const rows = document.querySelectorAll('.message-row');
  const parts = [];
  rows.forEach(row => {
    const role = row.classList.contains('user') ? 'Du' : 'Assistent';
    const c = row.querySelector('.bubble-content');
    const raw = ((c && (c._rawMd || c.textContent)) || '').trim();
    if (raw) parts.push(`## ${role}\n\n${raw}`);
  });
  if (!parts.length) { showToast('Kein Chat-Inhalt vorhanden'); return; }
  const title = (document.querySelector('.conv-item.active .conv-title')?.textContent || 'Chat').trim();
  downloadTextBlob('chat_export.md', `# ${title}\n\n` + parts.join('\n\n---\n\n') + '\n', 'text/markdown;charset=utf-8');
  showToast('✓ Chat als MD gespeichert');
}

// Canvas-Tabelle (create_spreadsheet) als CSV speichern — Excel-kompatibel:
// Semikolon-Trenner, Felder gequotet, UTF-8-BOM für Umlaute
function exportCanvasCsv() {
  const data = CanvasRenderer.getCurrentData();
  if (!data || data.type !== 'spreadsheet' || !(data.headers || data.rows)) {
    showToast('Keine Tabelle im Canvas — CSV-Export gilt für Tabellen'); return;
  }
  const esc = v => {
    const s = String(v == null ? '' : v).replace(/\s+/g, ' ').trim();
    return /[";\n]/.test(s) ? '"' + s.replace(/"/g, '""') + '"' : s;
  };
  const lines = [];
  if (data.headers) lines.push(data.headers.map(esc).join(';'));
  (data.rows || []).forEach(r => lines.push((r || []).map(esc).join(';')));
  downloadTextBlob('tabelle.csv', '\uFEFF' + lines.join('\r\n'), 'text/csv;charset=utf-8');
  showToast('✓ Tabelle als CSV gespeichert');
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

  // Geheim-/Lokal-Modus: Schnell-Umschalter in der Sidebar. Flippt das Profil-Flag
  // (voller Round-Trip über /api/profile, damit keine anderen Einstellungen verloren
  // gehen) und spiegelt den Zustand ins Badge + <body>-Klasse.
  const _secretBtn = document.getElementById('btn-secret-mode');
  function _reflectSecret(on) {
    // Die Persona »Hartman« erzwingt den Geheim-/Lokal-Modus → Badge zeigt „an" und
    // der Button ist gesperrt (nicht umschaltbar), solange Hartman aktiv ist.
    const hartman = (typeof Profile !== 'undefined' && Profile.isHartman && Profile.isHartman());
    const eff = !!on || hartman;
    document.body.classList.toggle('secret-mode', eff);
    if (_secretBtn) {
      _secretBtn.textContent = eff ? '🔒 Geheim-Modus: an' : '🔓 Geheim-Modus: aus';
      _secretBtn.classList.toggle('secret-on', eff);
      _secretBtn.disabled = hartman;
      _secretBtn.title = hartman
        ? 'Durch die Persona „Gunnery Sergeant Hartman" erzwungen — alles bleibt lokal'
        : 'Geheim-/Lokal-Modus umschalten';
    }
  }
  // Von der Persona-Umschaltung (profile.js) aufrufbar, um das Badge nachzuziehen.
  window.__reflectSecret = () => _reflectSecret(!!((typeof Profile !== 'undefined' && Profile.get) ? (Profile.get() || {}).local_only_mode : _profile.local_only_mode));
  _reflectSecret(!!_profile.local_only_mode);
  if (_secretBtn) {
    _secretBtn.addEventListener('click', async () => {
      if (typeof Profile !== 'undefined' && Profile.isHartman && Profile.isHartman()) {
        if (typeof showToast === 'function') showToast('Durch die Persona „Gunnery Sergeant Hartman" erzwungen — bitte zuerst den Antwortstil wechseln.');
        return;
      }
      _secretBtn.disabled = true;
      try {
        const cur = await (await fetch('/api/profile')).json();
        cur.local_only_mode = !cur.local_only_mode;
        await fetch('/api/profile', {
          method: 'PUT', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(cur),
        });
        _reflectSecret(cur.local_only_mode);
        if (typeof Profile !== 'undefined' && Profile.load) { try { await Profile.load(); } catch (_) {} }
        if (typeof Transcription !== 'undefined' && Transcription.refresh) { try { Transcription.refresh(); } catch (_) {} }
        if (typeof showToast === 'function') {
          showToast(cur.local_only_mode
            ? '🔒 Geheim-Modus an — alle Modelle laufen lokal.'
            : '🔓 Geheim-Modus aus — konfigurierte Modelle gelten wieder.');
        }
      } catch (_) {
      } finally { _secretBtn.disabled = false; }
    });
  }

  // 🧭 Assistent-Modus: nur Chat-Tab, das Modell wählt Werkzeuge selbst. Spiegelt sich in
  // <body class="assistant-mode"> (blendet alle Tabs außer Chat aus) und im Badge.
  const _assistBtn = document.getElementById('btn-assistant-mode');
  function _reflectAssistant(on) {
    document.body.classList.toggle('assistant-mode', !!on);
    if (_assistBtn) {
      _assistBtn.textContent = on ? '🧭 Assistent-Modus: an' : '🧭 Assistent-Modus: aus';
      _assistBtn.classList.toggle('secret-on', !!on);
    }
    if (on && typeof switchTab === 'function') switchTab('chat');
  }
  window.__reflectAssistant = () => _reflectAssistant(!!((typeof Profile !== 'undefined' && Profile.get) ? (Profile.get() || {}).assistant_mode : _profile.assistant_mode));
  _reflectAssistant(!!_profile.assistant_mode);
  if (_assistBtn) {
    _assistBtn.addEventListener('click', async () => {
      _assistBtn.disabled = true;
      try {
        const cur = await (await fetch('/api/profile')).json();
        cur.assistant_mode = !cur.assistant_mode;
        await fetch('/api/profile', {
          method: 'PUT', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(cur),
        });
        _reflectAssistant(cur.assistant_mode);
        if (typeof Profile !== 'undefined' && Profile.load) { try { await Profile.load(); } catch (_) {} }
        if (typeof showToast === 'function') {
          showToast(cur.assistant_mode
            ? '🧭 Assistent-Modus an — nur Chat; das Modell wählt Werkzeuge selbst (fähiges Modell nötig).'
            : '🧭 Assistent-Modus aus — alle Tabs wieder sichtbar.');
        }
      } catch (_) {
      } finally { _assistBtn.disabled = false; }
    });
  }

  // Neues Gespräch
  document.getElementById('btn-new-chat').addEventListener('click', () => {
    Chat.newConversation();
    switchTab('chat');
  });


  // Nachricht senden
  const input = document.getElementById('message-input');
  input.addEventListener('keydown', e => {
    // Befehls-Vorschau („/"): ↑/↓ wählen, Tab übernehmen, Esc schließen
    if (Chat.onSlashHintKeydown && Chat.onSlashHintKeydown(e)) { e.preventDefault(); return; }
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      Chat.sendMessage();
    }
  });
  input.addEventListener('input', () => {
    autoResizeTextarea(input);
    if (Chat.updateSlashHints) Chat.updateSlashHints(input.value);
  });
  if (Chat.initSlashHints) Chat.initSlashHints();

  document.getElementById('btn-send').addEventListener('click', () => Chat.sendOrAbort());

  // Such-Toggle
  document.getElementById('btn-search-toggle').addEventListener('click', function() {
    // Persona »Hartman«: Websuche gesperrt (alles rein lokal)
    if (typeof Profile !== 'undefined' && Profile.isHartman && Profile.isHartman()) {
      showToast('REKRUT, im Ausbildungsmodus läuft NICHTS nach draußen – KEINE Websuche!');
      return;
    }
    this.classList.toggle('active');
  });

  // Bild-Modus-Toggle (🎨): nächste Nachricht wird zum Bild-Prompt (One-shot in sendMessage)
  document.getElementById('btn-image-toggle')?.addEventListener('click', function() {
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
  document.getElementById('btn-merge-agents')?.addEventListener('click', () => AgentManager.mergeSelected());
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
  document.getElementById('btn-slide-image')?.addEventListener('click', () => CanvasRenderer.generateSlideImage());
  document.getElementById('btn-slides-images')?.addEventListener('click', () => CanvasRenderer.generateAllImages());

  // Export-Buttons
  document.getElementById('btn-export-pptx').addEventListener('click', () => exportCanvas('pptx'));
  document.getElementById('btn-export-pdf')?.addEventListener('click', () => exportCanvas('pdf'));
  document.getElementById('btn-export-latex')?.addEventListener('click', () => exportCanvas('latex'));
  document.getElementById('btn-export-xlsx').addEventListener('click', () => exportCanvas('xlsx'));
  document.getElementById('btn-export-docx').addEventListener('click', () => exportChat('docx'));
  document.getElementById('btn-export-md')?.addEventListener('click', () => exportChatMd());
  document.getElementById('btn-export-csv')?.addEventListener('click', () => exportCanvasCsv());
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

  // ── Export/Import im Profil ───────────────────────────────────────────────
  // Gleicher Endpunkt wie die Knöpfe in der Seitenleiste, aber mit wählbarem
  // Umfang: Uploads, Postfach-Archive und API-Schlüssel sind groß bzw.
  // vertraulich und darum standardmäßig NICHT enthalten.
  document.getElementById('btn-profile-export')?.addEventListener('click', () => {
    const q = new URLSearchParams();
    if (document.getElementById('exp-uploads')?.checked) q.set('uploads', '1');
    if (document.getElementById('exp-pst')?.checked)     q.set('pst', '1');
    if (document.getElementById('exp-secrets')?.checked) q.set('secrets', '1');
    if (q.get('secrets') &&
        !confirm('Die ZIP-Datei enthält dann deine API-Schlüssel im Klartext.\n\n'
               + 'Nur für den Umzug auf einen anderen Rechner gedacht — die Datei '
               + 'danach nicht weitergeben.\n\nTrotzdem exportieren?')) return;
    showToast('Export wird erstellt… bei großem Umfang kann das dauern.', 5000);
    const a = document.createElement('a');
    a.href = '/api/backup' + (q.toString() ? `?${q}` : '');
    a.download = '';
    a.click();
  });

  const _impInput = document.getElementById('profile-import-input');
  document.getElementById('btn-profile-import')?.addEventListener('click', () => _impInput?.click());
  _impInput?.addEventListener('change', async e => {
    const file = e.target.files[0];
    if (!file) return;
    e.target.value = '';
    const mode = document.querySelector('input[name="imp-mode"]:checked')?.value || 'merge';
    if (mode === 'replace' &&
        !confirm('„Vorhandenes ersetzen" überschreibt gleichnamige Dateien mit dem '
               + 'Stand aus dem Archiv. Das lässt sich nicht rückgängig machen.\n\nFortfahren?')) return;
    const out = document.getElementById('profile-import-result');
    if (out) out.textContent = '⏳ Wiederherstellung läuft…';
    try {
      const fd = new FormData();
      fd.append('file', file);
      const resp = await fetch(`/api/restore?replace=${mode === 'replace' ? '1' : '0'}`,
                               { method: 'POST', body: fd });
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const s = await resp.json();
      // Alle Zähler/Merker aus der Antwort anzeigen — so sieht man auch die
      // neuen Bereiche (angebote, rechnungen, …), ohne sie hier zu pflegen.
      const parts = Object.entries(s)
        .filter(([k, v]) => k !== 'errors' && v)
        .map(([k, v]) => (v === true ? `✓ ${k}` : `✓ ${v} ${k}`));
      if (s.errors?.length) parts.push(`⚠ ${s.errors.length} Fehler`);
      if (out) out.textContent = parts.join(' · ') || 'Nichts zu übernehmen.';
      showToast('Wiederherstellung abgeschlossen', 4000);
      if (typeof Chat !== 'undefined' && Chat.loadConversationList) { try { await Chat.loadConversationList(); } catch (_) {} }
      if (typeof Projects !== 'undefined' && Projects.load) { try { await Projects.load(); } catch (_) {} }
      if (typeof Profile !== 'undefined' && Profile.load) { try { await Profile.load(); } catch (_) {} }
      if (typeof AgentManager !== 'undefined' && AgentManager.load) { try { await AgentManager.load(); } catch (_) {} }
      if (typeof RAG !== 'undefined' && RAG.loadCollections) { try { await RAG.loadCollections(); } catch (_) {} }
    } catch (err) {
      if (out) out.textContent = '⚠ Fehlgeschlagen: ' + err.message;
      showToast('Wiederherstellung fehlgeschlagen: ' + err.message);
    }
  });

  // Modul-Initialisierung isoliert: ein Fehler in EINEM Modul darf die übrigen Tabs
  // nicht mehr lahmlegen (früher hat z. B. ein Fehler im Code-Workspace verhindert,
  // dass Mathe/Verzeichnis/Jury/… überhaupt verdrahtet wurden).
  const _safeInit = (label, fn) => { try { fn(); } catch (e) { console.error('Init fehlgeschlagen:', label, e); } };

  _safeInit('Planner', () => Planner.init());
  _safeInit('MatrixResearch', () => MatrixResearch.init());
  _safeInit('RFQ', () => { if (typeof RFQ !== 'undefined') RFQ.init(); });
  _safeInit('PresentationAssistant', () => PresentationAssistant.init());
  _safeInit('IllustratedPresentation', () => IllustratedPresentation.init());
  _safeInit('CanvasEditor', () => CanvasEditor.init());
  _safeInit('CodeWorkspace', () => { if (typeof CodeWorkspace !== 'undefined') CodeWorkspace.init(); });
  _safeInit('MedizinChat', () => { if (typeof MedizinChat !== 'undefined') MedizinChat.init(); });
  _safeInit('MatheChat', () => { if (typeof MatheChat !== 'undefined') MatheChat.init(); });
  _safeInit('DirAnalysis', () => { if (typeof DirAnalysis !== 'undefined') DirAnalysis.init(); });
  _safeInit('MorphBox', () => { if (typeof MorphBox !== 'undefined') MorphBox.init(); });
  _safeInit('Postfach', () => { if (typeof Postfach !== 'undefined') Postfach.init(); });
  _safeInit('Patente', () => { if (typeof Patente !== 'undefined') Patente.init(); });
  _safeInit('Rechnung', () => { if (typeof Rechnung !== 'undefined') Rechnung.init(); });
  _safeInit('Zeugnis', () => { if (typeof Zeugnis !== 'undefined') Zeugnis.init(); });
  _safeInit('Varianten', () => { if (typeof Varianten !== 'undefined') Varianten.init(); });
  _safeInit('Compare', () => { if (typeof Compare !== 'undefined') Compare.init(); });
  _safeInit('Todo', () => { if (typeof Todo !== 'undefined') Todo.init(); });
  _safeInit('Transcription', () => { if (typeof Transcription !== 'undefined') Transcription.init(); });
  _safeInit('TTS', () => { if (typeof TTS !== 'undefined') TTS.init(); });
  _safeInit('Jury', () => { if (typeof Jury !== 'undefined') Jury.init(); });

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

  // Token-Zähler (Sitzung) — nach Profile.init(), damit der Preis verfügbar ist
  _safeInit('TokenMeter', () => { if (typeof TokenMeter !== 'undefined') TokenMeter.init(); });

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
