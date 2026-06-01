/* AI_Framework_Thomas — Diagnose-Logger */

const Logger = (() => {

  /* ── Zustand ─────────────────────────────────────────────────── */
  let _active  = false;
  let _filter  = 'all';
  let _entries = [];     // im Speicher (für Filter/Refresh ohne Server-Roundtrip)

  const TYPE_META = {
    chat:    { label: 'CHAT',    cls: 'log-chat'    },
    tool:    { label: 'TOOL',    cls: 'log-tool'    },
    export:  { label: 'EXPORT',  cls: 'log-export'  },
    ide_run: { label: 'IDE',     cls: 'log-ide'     },
    nav:     { label: 'NAV',     cls: 'log-nav'     },
    error:   { label: 'FEHLER',  cls: 'log-error'   },
    frontend:{ label: 'FE',      cls: 'log-fe'      },
  };

  /* ── Logging-API ─────────────────────────────────────────────── */
  function log(type, data) {
    if (!_active) return;
    const entry = { ts: Date.now() / 1000, type, ...data };
    _entries.push(entry);
    _appendEntry(entry);
    // Eintrag asynchron ans Backend senden (best-effort, kein await)
    fetch('/api/logs/entry', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ type, ...data }),
    }).catch(() => {});
  }

  function isActive() { return _active; }

  /* ── Toggle ──────────────────────────────────────────────────── */
  async function _toggle() {
    _active = !_active;
    try {
      await fetch('/api/logs/config', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ active: _active }),
      });
    } catch (_) {}
    _updateToggleBtn();
    if (_active) {
      showToast('🔴 Logging aktiviert');
      log('frontend', { event: 'logging_start' });
    } else {
      showToast('⏸ Logging pausiert');
    }
  }

  function _updateToggleBtn() {
    const btn = document.getElementById('btn-log-toggle');
    if (!btn) return;
    btn.textContent = _active ? '⏸ Logging pausieren' : '▶ Logging aktivieren';
    btn.classList.toggle('log-btn-active', _active);
    const status = document.getElementById('log-status-dot');
    if (status) {
      status.textContent = _active ? '🔴 Aktiv' : '⏺ Inaktiv';
      status.style.color = _active ? '#ef4444' : 'var(--text-muted)';
    }
  }

  /* ── Einträge rendern ────────────────────────────────────────── */
  function _formatEntry(entry) {
    const meta = TYPE_META[entry.type] || { label: entry.type?.toUpperCase() || '?', cls: '' };
    const ts   = entry.ts ? new Date(entry.ts * 1000).toLocaleTimeString('de-DE') : '--:--:--';

    let detail = '';
    switch (entry.type) {
      case 'chat':
        detail = `${entry.model || '?'}  ${entry.msg_count || 0} Nachr. → ${entry.resp_len || 0} Zeichen` +
                 (entry.tools_called?.length ? `  [${entry.tools_called.join(', ')}]` : '') +
                 `  ${entry.ms || 0} ms`;
        break;
      case 'tool':
        detail = `${entry.name || '?'}  → ${entry.result_len || 0} Bytes  ${entry.ms || 0} ms`;
        break;
      case 'export':
        detail = `${entry.format?.toUpperCase() || '?'}  ${entry.size ? entry.size + ' Bytes' : ''}`;
        break;
      case 'ide_run':
        detail = `${entry.name || 'Unbenannt'}  (${entry.code_len || 0} Zeichen)`;
        break;
      case 'nav':
        detail = `Tab → ${entry.tab || '?'}`;
        break;
      case 'error':
        detail = `${entry.source || '?'}: ${entry.message || ''}`;
        break;
      default:
        detail = JSON.stringify(entry).replace(/^.|.$/g, '').slice(0, 120);
    }

    return { ts, meta, detail };
  }

  function _appendEntry(entry) {
    if (_filter !== 'all' && entry.type !== _filter) return;
    const container = document.getElementById('log-entries');
    if (!container) return;

    const { ts, meta, detail } = _formatEntry(entry);
    const row = document.createElement('div');
    row.className = `log-row ${meta.cls}`;
    row.innerHTML = `<span class="log-ts">${ts}</span><span class="log-badge">${meta.label}</span><span class="log-detail">${escHtml(detail)}</span>`;
    container.appendChild(row);
    container.scrollTop = container.scrollHeight;

    const countEl = document.getElementById('log-count');
    if (countEl) countEl.textContent = `${_entries.length} Einträge`;
  }

  /* ── Vom Server laden ────────────────────────────────────────── */
  async function _reload() {
    try {
      const data = await (await fetch('/api/logs')).json();
      _entries = data;
      _renderAll();
    } catch (_) {}
  }

  function _renderAll() {
    const container = document.getElementById('log-entries');
    if (!container) return;
    container.innerHTML = '';
    const filtered = _filter === 'all' ? _entries : _entries.filter(e => e.type === _filter);
    if (filtered.length === 0) {
      container.innerHTML = '<div class="log-empty">Keine Einträge' + (_filter !== 'all' ? ` für Filter "${_filter}"` : '') + '</div>';
      return;
    }
    for (const entry of filtered) _appendEntry(entry);
    const countEl = document.getElementById('log-count');
    if (countEl) countEl.textContent = `${filtered.length} Einträge`;
  }

  /* ── Leeren ──────────────────────────────────────────────────── */
  async function _clear() {
    if (!confirm('Alle Log-Einträge löschen?')) return;
    await fetch('/api/logs', { method: 'DELETE' });
    _entries = [];
    _renderAll();
    showToast('Log geleert');
  }

  /* ── Download ────────────────────────────────────────────────── */
  function _download() {
    const a = document.createElement('a');
    a.href = '/api/logs/download';
    a.download = '';
    a.click();
  }

  /* ── Tab-Navigation abfangen ─────────────────────────────────── */
  function _patchTabNavigation() {
    document.querySelectorAll('.tab-btn').forEach(btn => {
      btn.addEventListener('click', () => {
        log('nav', { tab: btn.dataset.tab });
      });
    });
  }

  /* ── init ────────────────────────────────────────────────────── */
  async function init() {
    // Status vom Server holen
    try {
      const s = await (await fetch('/api/logs/active')).json();
      _active = s.active || false;
    } catch (_) {}

    document.getElementById('btn-log-toggle')?.addEventListener('click', _toggle);
    document.getElementById('btn-log-clear')?.addEventListener('click', _clear);
    document.getElementById('btn-log-download')?.addEventListener('click', _download);
    document.getElementById('btn-log-reload')?.addEventListener('click', _reload);

    const filterSel = document.getElementById('log-filter-select');
    filterSel?.addEventListener('change', () => {
      _filter = filterSel.value;
      _renderAll();
    });

    // Beim Öffnen des Tabs Daten laden
    document.querySelector('[data-tab="logs"]')?.addEventListener('click', _reload);

    _updateToggleBtn();
    _patchTabNavigation();

    // Globale JS-Fehler abfangen
    window.addEventListener('error', e => {
      log('error', { source: 'frontend', message: e.message, file: e.filename, line: e.lineno });
    });
    window.addEventListener('unhandledrejection', e => {
      log('error', { source: 'frontend', message: String(e.reason) });
    });
  }

  return { init, log, isActive };

})();
