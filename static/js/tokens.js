/* AI_Framework_Thomas — Token-Zähler (Sitzung)
   Sammelt die vom Backend gemeldeten Token-Zahlen (Prompt/Antwort) über die
   Sitzung hinweg und schätzt die Kosten anhand des im Profil hinterlegten
   Preises je 1.000 Tokens. Anbieter-unabhängig: lokale Ollama-Modelle liefern
   prompt_eval_count/eval_count, Remote-APIs werden in tools/llm.py auf dieselben
   Felder gemappt. Bei Preis 0 (lokale Modelle) bleibt die Kostenzeile leer. */
const TokenMeter = (() => {
  'use strict';
  const KEY = 'token_meter_session';
  let _in = 0, _out = 0;

  function _load() {
    try {
      const s = JSON.parse(localStorage.getItem(KEY) || '{}');
      _in = Number(s.in) || 0; _out = Number(s.out) || 0;
    } catch (_) { _in = 0; _out = 0; }
  }
  function _persist() {
    try { localStorage.setItem(KEY, JSON.stringify({ in: _in, out: _out })); } catch (_) {}
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

  function render() {
    const box = document.getElementById('token-meter');
    if (!box) return;
    box.style.display = '';
    const total = _in + _out;
    const totEl = document.getElementById('token-meter-total');
    const ioEl  = document.getElementById('token-meter-io');
    const costEl = document.getElementById('token-meter-cost');
    if (totEl) totEl.textContent = _fmtNum(total);
    if (ioEl)  ioEl.textContent = `↓${_fmtNum(_in)} ↑${_fmtNum(_out)}`;
    if (costEl) {
      const pr = _price();
      const cost = (_in / 1000) * pr.in + (_out / 1000) * pr.out;
      costEl.textContent = (pr.in || pr.out) ? `≈ ${cost.toFixed(cost < 1 ? 4 : 2)} ${pr.cur}` : '';
    }
  }

  // tokens = { in, out } aus dem 'done'-SSE-Frame
  function add(tokens) {
    if (!tokens) return;
    _in  += Number(tokens.in)  || 0;
    _out += Number(tokens.out) || 0;
    _persist();
    render();
  }

  function reset() {
    _in = 0; _out = 0;
    _persist();
    render();
    if (typeof showToast === 'function') showToast('Token-Zähler zurückgesetzt');
  }

  function init() {
    _load();
    const box = document.getElementById('token-meter');
    if (box) box.addEventListener('click', () => {
      if (confirm('Token-Zähler dieser Sitzung zurücksetzen?')) reset();
    });
    render();
  }

  return { init, add, reset, render };
})();
