/* AI_Framework_Thomas — Token-Zähler (Sitzung + pro Vorgang)
   Sammelt die vom Backend gemeldeten Token-Zahlen (Prompt/Antwort) über die
   Sitzung hinweg und schätzt die Kosten anhand des im Profil hinterlegten
   Preises je 1.000 Tokens. Zusätzlich wird jeder Vorgang (Chat, Matrix,
   Plan, Jury, Anfrage, Code …) mit einem Label protokolliert, sodass sich der
   Aufwand einzelner Vorgänge und die Summe je Vorgangsart nachvollziehen lässt.
   Anbieter-unabhängig: lokale Ollama-Modelle liefern prompt_eval_count/
   eval_count, Remote-APIs werden in tools/llm.py auf dieselben Felder gemappt.
   Bei Preis 0 (lokale Modelle) bleibt die Kostenzeile leer. */
const TokenMeter = (() => {
  'use strict';
  const KEY = 'token_meter_session';
  const MAX_LOG = 100;          // letzte N Vorgänge aufbewahren
  let _in = 0, _out = 0;
  let _log = [];                // [{label, in, out, t}]

  function _load() {
    try {
      const s = JSON.parse(localStorage.getItem(KEY) || '{}');
      _in = Number(s.in) || 0; _out = Number(s.out) || 0;
      _log = Array.isArray(s.log) ? s.log : [];
    } catch (_) { _in = 0; _out = 0; _log = []; }
  }
  function _persist() {
    try { localStorage.setItem(KEY, JSON.stringify({ in: _in, out: _out, log: _log.slice(-MAX_LOG) })); } catch (_) {}
  }

  function _price() {
    let p = {};
    try { p = (typeof Profile !== 'undefined' ? Profile.get() : {}) || {}; } catch (_) {}
    return {
      in:  Number(p.price_per_1k_in)  || 0,
      out: Number(p.price_per_1k_out) || 0,
      cur: p.currency || '€',
    };
  }

  function _fmtNum(n) {
    return n >= 1000 ? (n / 1000).toFixed(n >= 100000 ? 0 : 1) + 'k' : String(n);
  }
  function _cost(tin, tout) {
    const pr = _price();
    if (!pr.in && !pr.out) return '';
    const c = (tin / 1000) * pr.in + (tout / 1000) * pr.out;
    return `≈ ${c.toFixed(c < 1 ? 4 : 2)} ${pr.cur}`;
  }

  function render() {
    const box = document.getElementById('token-meter');
    if (!box) return;
    box.style.display = '';
    const total = _in + _out;
    const totEl = document.getElementById('token-meter-total');
    const ioEl  = document.getElementById('token-meter-io');
    const costEl = document.getElementById('token-meter-cost');
    const lastEl = document.getElementById('token-meter-last');
    if (totEl) totEl.textContent = _fmtNum(total);
    if (ioEl)  ioEl.textContent = `↓${_fmtNum(_in)} ↑${_fmtNum(_out)}`;
    if (costEl) costEl.textContent = _cost(_in, _out);
    if (lastEl) {
      const last = _log[_log.length - 1];
      if (last) {
        lastEl.style.display = '';
        lastEl.textContent = `↳ ${last.label}: ${_fmtNum((last.in || 0) + (last.out || 0))}`;
      } else {
        lastEl.style.display = 'none';
      }
    }
    const bd = document.getElementById('token-meter-breakdown');
    if (bd && bd.style.display !== 'none') _renderBreakdown(bd);
  }

  // Aufschlüsselung nach Vorgangsart (Summe je Label) + letzte Vorgänge.
  function _renderBreakdown(bd) {
    const byLabel = {};
    for (const e of _log) {
      const k = e.label || 'Sonstige';
      if (!byLabel[k]) byLabel[k] = { in: 0, out: 0, n: 0 };
      byLabel[k].in += e.in || 0; byLabel[k].out += e.out || 0; byLabel[k].n += 1;
    }
    const cats = Object.entries(byLabel).sort((a, b) => (b[1].in + b[1].out) - (a[1].in + a[1].out));
    const esc = s => String(s).replace(/[<>&]/g, c => ({ '<': '&lt;', '>': '&gt;', '&': '&amp;' }[c]));
    let html = '<div style="font-weight:600;margin-bottom:6px">Token pro Vorgangsart</div>';
    if (!cats.length) {
      html += '<div style="opacity:.6">Noch keine Vorgänge erfasst.</div>';
    } else {
      html += '<table style="width:100%;border-collapse:collapse;font-size:11px">';
      for (const [label, v] of cats) {
        const cst = _cost(v.in, v.out);
        html += `<tr style="border-bottom:1px solid var(--border)">
          <td style="padding:2px 4px">${esc(label)} <span style="opacity:.5">×${v.n}</span></td>
          <td style="padding:2px 4px;text-align:right;white-space:nowrap">${_fmtNum(v.in + v.out)}${cst ? ' · ' + cst : ''}</td></tr>`;
      }
      html += '</table>';
      // Letzte Vorgänge (chronologisch, neueste zuerst)
      const recent = _log.slice(-8).reverse();
      html += '<div style="font-weight:600;margin:8px 0 4px">Letzte Vorgänge</div>';
      for (const e of recent) {
        const ts = e.t ? new Date(e.t).toLocaleTimeString() : '';
        html += `<div style="display:flex;justify-content:space-between;opacity:.8">
          <span>${esc(e.label || 'Vorgang')} <span style="opacity:.5">${ts}</span></span>
          <span>↓${_fmtNum(e.in || 0)} ↑${_fmtNum(e.out || 0)}</span></div>`;
      }
    }
    html += '<button id="token-meter-reset" style="margin-top:8px;width:100%;font-size:11px;cursor:pointer;'
          + 'padding:4px;border:1px solid var(--border);border-radius:6px;background:var(--bg-hover);color:var(--text)">'
          + 'Zähler zurücksetzen</button>';
    bd.innerHTML = html;
    bd.querySelector('#token-meter-reset')?.addEventListener('click', (ev) => {
      ev.stopPropagation();
      if (confirm('Token-Zähler dieser Sitzung zurücksetzen?')) reset();
    });
  }

  // tokens = { in, out } aus dem 'done'-SSE-Frame; label = Vorgangsart (z. B. 'Matrix')
  function add(tokens, label) {
    if (!tokens) return;
    const ti = Number(tokens.in) || 0, to = Number(tokens.out) || 0;
    if (!ti && !to) return;
    _in += ti; _out += to;
    _log.push({ label: label || 'Chat', in: ti, out: to, t: Date.now() });
    if (_log.length > MAX_LOG) _log = _log.slice(-MAX_LOG);
    _persist();
    render();
  }

  function reset() {
    _in = 0; _out = 0; _log = [];
    _persist();
    const bd = document.getElementById('token-meter-breakdown');
    if (bd) bd.style.display = 'none';
    render();
    if (typeof showToast === 'function') showToast('Token-Zähler zurückgesetzt');
  }

  function init() {
    _load();
    const box = document.getElementById('token-meter');
    const bd = document.getElementById('token-meter-breakdown');
    if (box && bd) {
      box.addEventListener('click', (e) => {
        // Klick innerhalb der Aufschlüsselung nicht als Toggle werten
        if (bd.contains(e.target)) return;
        bd.style.display = bd.style.display === 'none' ? '' : 'none';
        if (bd.style.display !== 'none') _renderBreakdown(bd);
      });
      document.addEventListener('click', (e) => {
        if (!box.contains(e.target) && bd.style.display !== 'none') bd.style.display = 'none';
      });
    }
    render();
  }

  return { init, add, reset, render };
})();
