/* Handy-Kopplung per QR-Code (WLAN). Holt die erreichbaren Adressen vom Server und
   zeigt je Adresse einen QR-Code (serverseitig erzeugt, tools/qrcode_pure.py – nichts
   verlässt den Rechner). Umschalter: gesamtes Tool ODER nur Chat (Assistent). */
const Pairing = (function () {
  let _scope = 'full';        // 'full' | 'assistant'
  let _info = null;

  function $(id) { return document.getElementById(id); }

  function _renderList() {
    const box = $('pair-list');
    if (!box) return;
    if (!_info || !_info.entries || !_info.entries.length) {
      box.innerHTML = '<div style="font-size:12.5px;color:var(--text-muted)">' +
        'Keine Netzwerk-Adresse gefunden. Ist der Rechner mit dem WLAN verbunden und ' +
        'läuft der Server mit <code>AI_HOST=0.0.0.0</code>?</div>';
      return;
    }
    const asst = _scope === 'assistant' ? 1 : 0;
    box.innerHTML = '';
    _info.entries.forEach(e => {
      const url = asst ? e.url_assistant : e.url_full;
      const row = document.createElement('div');
      row.style.cssText = 'display:flex;gap:12px;align-items:center;border:1px solid var(--border);' +
        'border-radius:8px;padding:10px;background:var(--bg-main)';
      const qr = document.createElement('img');
      qr.alt = 'QR-Code für ' + url;
      qr.width = 168; qr.height = 168;
      qr.style.cssText = 'width:168px;height:168px;border-radius:6px;background:#fff;flex:0 0 auto';
      qr.src = `/api/pairing/qr?ip=${encodeURIComponent(e.ip)}&assistant=${asst}&t=${Date.now()}`;
      const meta = document.createElement('div');
      meta.style.cssText = 'min-width:0;flex:1';
      const badge = e.hotspot
        ? '<span style="font-size:10.5px;background:var(--accent-dim);color:var(--text-dim);border-radius:4px;padding:1px 6px">Hotspot/anderes Netz</span>'
        : '<span style="font-size:10.5px;background:var(--accent-dim);color:var(--text-dim);border-radius:4px;padding:1px 6px">Heim-WLAN</span>';
      meta.innerHTML =
        `<div style="font-size:12.5px;margin-bottom:4px">${badge}</div>` +
        `<a href="${url}" target="_blank" rel="noopener" style="font-size:13px;color:var(--accent);` +
        `word-break:break-all;font-family:var(--font-mono)">${url}</a>` +
        `<div style="margin-top:6px"><button class="export-btn pair-copy" data-url="${url}" ` +
        `style="font-size:11.5px">📋 Adresse kopieren</button></div>`;
      row.appendChild(qr); row.appendChild(meta);
      box.appendChild(row);
    });
    box.querySelectorAll('.pair-copy').forEach(b => {
      b.addEventListener('click', () => {
        const u = b.getAttribute('data-url');
        navigator.clipboard?.writeText(u).then(
          () => { b.textContent = '✓ kopiert'; setTimeout(() => b.textContent = '📋 Adresse kopieren', 1500); },
          () => {}
        );
      });
    });
  }

  async function open() {
    const ov = $('pair-overlay');
    if (ov) ov.classList.add('active');
    const box = $('pair-list');
    if (box) box.innerHTML = '<div style="font-size:12.5px;color:var(--text-muted)">Suche Adressen …</div>';
    try {
      const r = await fetch('/api/pairing/info');
      _info = await r.json();
    } catch (_) {
      _info = null;
    }
    const hint = $('pair-hint');
    if (hint && _info && _info.hint) hint.textContent = 'ℹ ' + _info.hint;
    _renderList();
  }

  function close() {
    const ov = $('pair-overlay');
    if (ov) ov.classList.remove('active');
  }

  function _setScope(scope) {
    _scope = scope;
    document.querySelectorAll('.pair-scope').forEach(b => {
      b.classList.toggle('active', b.getAttribute('data-scope') === scope);
    });
    _renderList();
  }

  function init() {
    $('btn-pair-phone')?.addEventListener('click', open);
    $('btn-pair-close')?.addEventListener('click', close);
    $('pair-overlay')?.addEventListener('click', e => { if (e.target.id === 'pair-overlay') close(); });
    document.querySelectorAll('.pair-scope').forEach(b => {
      b.addEventListener('click', () => _setScope(b.getAttribute('data-scope')));
    });
  }

  return { init, open, close };
})();
