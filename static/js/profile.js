/* AI_Framework_Thomas — Nutzerprofil (inkl. Modus/Farben + Branding-Assets) */

const Profile = (() => {
  let _data = {};
  const KINDS = ['logo', 'cover', 'header'];
  const MODEL_ROLES = { general: 'model_general', coding: 'model_coding', science: 'model_science', medical: 'model_medical' };

  // Das einer Rolle zugewiesene Modell (leer → Sidebar-Modell bzw. ministral-3:3b)
  function modelFor(role) {
    const key = MODEL_ROLES[role];
    const m = (key && _data[key]) ? String(_data[key]).trim() : '';
    if (m) return m;
    const sidebar = document.getElementById('model-select')?.value;
    return (sidebar && !/Lade|erreichbar|Fehler/.test(sidebar)) ? sidebar : 'ministral-3:3b';
  }

  // Modell-Auswahllisten im Profil aus allen installierten Ollama-Modellen befüllen
  async function _fillModelSelects() {
    let models = [];
    try {
      const resp = await fetch('/api/models');
      const data = await resp.json();
      models = (data.models || []).map(m => m.name);
    } catch (_) {}
    for (const role of Object.keys(MODEL_ROLES)) {
      const sel = document.getElementById('profile-model-' + role);
      if (!sel) continue;
      const current = _data[MODEL_ROLES[role]] || '';
      sel.innerHTML = '<option value="">— Standard (ministral-3:3b) —</option>';
      for (const name of models) {
        const opt = document.createElement('option');
        opt.value = name; opt.textContent = name;
        sel.appendChild(opt);
      }
      // Zugewiesenes Modell auswählen (auch wenn es derzeit nicht installiert ist)
      if (current && !models.includes(current)) {
        const opt = document.createElement('option');
        opt.value = current; opt.textContent = current + ' (nicht installiert)';
        sel.appendChild(opt);
      }
      sel.value = current;
    }
  }

  function applyTabVisibility(hiddenTabs) {
    hiddenTabs = hiddenTabs || [];
    const optionalTabs = ['rag', 'ide', 'mail', 'logs', 'medizin', 'mathe'];
    for (const tab of optionalTabs) {
      const btn = document.querySelector(`.tab-btn[data-tab="${tab}"]`);
      if (btn) btn.style.display = hiddenTabs.includes(tab) ? 'none' : '';
    }
  }

  function applyMode(mode) {
    const m = ['maschinenbau', 'ki', 'soziales', 'marketing', 'finanz', 'geschaeftsfuehrung', 'custom'].includes(mode) ? mode : 'maschinenbau';
    document.documentElement.dataset.mode = m;
    // Offene Präsentation sofort im neuen Farbschema neu zeichnen
    if (typeof CanvasRenderer !== 'undefined' && CanvasRenderer.rerender) {
      try { CanvasRenderer.rerender(); } catch (_) {}
    }
  }

  async function load() {
    try {
      const resp = await fetch('/api/profile');
      _data = await resp.json();
    } catch (e) {
      _data = {};
    }
    applyMode(_data.mode);
    applyTabVisibility(_data.hidden_tabs || []);
    if (typeof I18n !== 'undefined' && _data.lang) I18n.setLang(_data.lang);
    return _data;
  }

  function get() { return _data; }

  function _refreshPreviews() {
    for (const kind of KINDS) {
      const img = document.getElementById('prev-' + kind);
      if (!img) continue;
      // Cache-Bust, damit nach Upload das neue Bild erscheint
      fetch(`/api/profile/asset/${kind}?t=${Date.now()}`).then(r => {
        if (r.ok) { img.src = `/api/profile/asset/${kind}?t=${Date.now()}`; img.style.display = 'block'; }
        else { img.removeAttribute('src'); img.style.display = 'none'; }
      }).catch(() => { img.style.display = 'none'; });
    }
  }

  function openModal() {
    document.getElementById('profile-first-name').value  = _data.first_name  || '';
    document.getElementById('profile-last-name').value   = _data.last_name   || '';
    document.getElementById('profile-company').value     = _data.company     || '';
    document.getElementById('profile-department').value  = _data.department  || '';
    document.getElementById('profile-position').value    = _data.position    || '';
    document.getElementById('profile-email').value       = _data.email       || '';
    document.getElementById('profile-phone').value       = _data.phone       || '';
    document.getElementById('profile-default-project').value = _data.default_project || '';
    const langEl = document.getElementById('profile-lang');
    if (langEl) langEl.value = (_data.lang === 'en') ? 'en' : 'de';
    document.getElementById('profile-mode').value = _data.mode || 'maschinenbau';
    document.getElementById('profile-custom-mode-name').value     = _data.custom_mode_name     || '';
    document.getElementById('profile-custom-mode-prompt').value    = _data.custom_mode_prompt    || '';
    document.getElementById('profile-custom-mode-keywords').value  = _data.custom_mode_keywords  || '';
    _toggleCustomMode(_data.mode || 'maschinenbau');
    document.getElementById('profile-mode-prompt').checked = _data.mode_prompt !== false;
    document.getElementById('profile-pure-llm').checked = !!_data.pure_llm;
    document.getElementById('profile-tone').value = _data.tone || '';
    document.getElementById('profile-auto-compress').checked = !!_data.auto_compress;
    document.getElementById('profile-compress-overflow').value = _data.compress_overflow_chars || 12000;
    document.getElementById('profile-compress-idle').value = _data.compress_idle_min || 10;
    const replayEl = document.getElementById('profile-replay-intro');
    if (replayEl) replayEl.checked = !!_data.replay_intro;
    _fillModelSelects();
    _refreshPreviews();
    // Tab-Sichtbarkeit: ein Häkchen kann mehrere Tabs steuern (data-tabs="ide,mathe").
    // Angehakt = alle zugehörigen Tabs sichtbar (keiner ausgeblendet).
    const hiddenTabs = _data.hidden_tabs || [];
    document.querySelectorAll('#profile-tab-vis input[type="checkbox"]').forEach(cb => {
      const tabs = (cb.dataset.tabs || '').split(',').filter(Boolean);
      cb.checked = tabs.every(t => !hiddenTabs.includes(t));
    });
    document.getElementById('profile-modal-overlay').classList.add('active');
  }

  function closeModal() {
    document.getElementById('profile-modal-overlay').classList.remove('active');
  }

  // Konfigurationsblock des eigenen (violetten) Modus nur bei dessen Auswahl zeigen
  function _toggleCustomMode(mode) {
    const box = document.getElementById('custom-mode-config');
    if (box) box.style.display = (mode === 'custom') ? '' : 'none';
  }

  async function _uploadAsset(kind, file) {
    const fd = new FormData();
    fd.append('file', file);
    try {
      const r = await fetch(`/api/profile/asset/${kind}`, { method: 'POST', body: fd });
      if (!r.ok) throw new Error('HTTP ' + r.status);
      showToast(`✓ ${kind} hochgeladen`);
      _refreshPreviews();
      _propagateBranding();
    } catch (e) {
      showToast('Upload fehlgeschlagen: ' + e.message);
    }
  }

  async function _deleteAsset(kind) {
    try {
      await fetch(`/api/profile/asset/${kind}`, { method: 'DELETE' });
      showToast(`${kind} entfernt`);
      _refreshPreviews();
      _propagateBranding();
    } catch (e) { showToast('Fehler: ' + e.message); }
  }

  // Logo in der Sidebar + Folien-Branding aktualisieren
  function _propagateBranding() {
    const logo = document.getElementById('sidebar-logo');
    if (logo) { logo.src = `/api/profile/asset/logo?t=${Date.now()}`; logo.style.display = ''; }
    if (typeof CanvasRenderer !== 'undefined' && CanvasRenderer.reloadBranding) {
      CanvasRenderer.reloadBranding();
    }
  }

  async function save() {
    const mode = document.getElementById('profile-mode').value;
    const lang = document.getElementById('profile-lang').value === 'en' ? 'en' : 'de';
    const payload = {
      lang:            lang,
      first_name:      document.getElementById('profile-first-name').value.trim(),
      last_name:       document.getElementById('profile-last-name').value.trim(),
      company:         document.getElementById('profile-company').value.trim(),
      department:      document.getElementById('profile-department').value.trim(),
      position:        document.getElementById('profile-position').value.trim(),
      email:           document.getElementById('profile-email').value.trim(),
      phone:           document.getElementById('profile-phone').value.trim(),
      default_project: document.getElementById('profile-default-project').value.trim(),
      mode:            mode,
      custom_mode_name:     document.getElementById('profile-custom-mode-name').value.trim(),
      custom_mode_prompt:   document.getElementById('profile-custom-mode-prompt').value.trim(),
      custom_mode_keywords: document.getElementById('profile-custom-mode-keywords').value.trim(),
      mode_prompt:     document.getElementById('profile-mode-prompt').checked,
      pure_llm:        document.getElementById('profile-pure-llm').checked,
      tone:            document.getElementById('profile-tone').value,
      auto_compress:           document.getElementById('profile-auto-compress').checked,
      compress_overflow_chars: parseInt(document.getElementById('profile-compress-overflow').value, 10) || 12000,
      compress_idle_min:       parseInt(document.getElementById('profile-compress-idle').value, 10) || 10,
      model_general:  document.getElementById('profile-model-general')?.value || '',
      model_coding:   document.getElementById('profile-model-coding')?.value || '',
      model_science:  document.getElementById('profile-model-science')?.value || '',
      model_medical:  document.getElementById('profile-model-medical')?.value || '',
      hidden_tabs: [...new Set(
        Array.from(document.querySelectorAll('#profile-tab-vis input[type="checkbox"]'))
          .filter(cb => !cb.checked)
          .flatMap(cb => (cb.dataset.tabs || '').split(',').filter(Boolean))
      )],
      // Profil gespeichert ⇒ als eingerichtet markieren; Einleitung nur auf Wunsch erneut
      onboarding_done: true,
      replay_intro: !!document.getElementById('profile-replay-intro')?.checked,
    };
    try {
      const resp = await fetch('/api/profile', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      _data = await resp.json();
      applyMode(_data.mode);
      applyTabVisibility(_data.hidden_tabs || []);
      if (typeof I18n !== 'undefined') I18n.setLang(_data.lang || 'de');
      showToast(typeof I18n !== 'undefined' ? I18n.t('Profil gespeichert') : 'Profil gespeichert');
      closeModal();
    } catch (e) {
      showToast('Fehler beim Speichern');
    }
  }

  function init() {
    document.getElementById('btn-profile').addEventListener('click', openModal);
    document.getElementById('btn-profile-close').addEventListener('click', closeModal);
    document.getElementById('btn-profile-save').addEventListener('click', save);
    document.getElementById('profile-modal-overlay').addEventListener('click', e => {
      if (e.target === document.getElementById('profile-modal-overlay')) closeModal();
    });
    // Live-Vorschau des Modus beim Umschalten
    document.getElementById('profile-mode')?.addEventListener('change', e => {
      applyMode(e.target.value);
      _toggleCustomMode(e.target.value);
    });
    // Live-Umschaltung der Oberflächensprache
    document.getElementById('profile-lang')?.addEventListener('change', e => {
      if (typeof I18n !== 'undefined') I18n.setLang(e.target.value);
    });
    // Asset-Uploads
    for (const kind of KINDS) {
      document.getElementById('file-' + kind)?.addEventListener('change', e => {
        if (e.target.files[0]) _uploadAsset(kind, e.target.files[0]);
        e.target.value = '';
      });
    }
    document.querySelectorAll('.profile-asset-del').forEach(btn => {
      btn.addEventListener('click', () => _deleteAsset(btn.dataset.kind));
    });
    load();
  }

  return { init, load, get, openModal, closeModal, applyMode, modelFor, applyTabVisibility };
})();
