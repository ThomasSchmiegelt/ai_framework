/* pi_agent.js — Pi-Agent-Tab
 *
 * Fährt den externen Agenten-Harness Pi (https://pi.dev) über
 * POST /api/pi/run (SSE) und zeigt live, was er tut: Werkzeugaufrufe,
 * optional sein Denken, am Ende die Antwort.
 *
 * Der Reiz des Tabs ist die Sichtbarkeit: man SIEHT, wie der Agent die
 * Framework-Kommandozeile aufruft (Websuche, Schraubenauslegung, Rechenkerne)
 * statt selbst zu rechnen. Darum wird jeder Werkzeugaufruf als eigener,
 * aufklappbarer Block gezeigt.
 */
const PiAgent = (function () {
  'use strict';

  let _cfg = null;
  let _abort = null;
  let _timer = null;
  let _start = 0;

  const el = (id) => document.getElementById(id);

  // ── Hilfsdarstellung ──────────────────────────────────────────────────────

  function _md(host, text) {
    if (typeof marked !== 'undefined') {
      host.innerHTML = marked.parse(text || '', { gfm: true, breaks: true });
      host.querySelectorAll('a[href]').forEach(a => {
        a.target = '_blank'; a.rel = 'noopener noreferrer';
      });
    } else {
      host.textContent = text || '';
    }
  }

  function _esc(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  }

  function _out() { return el('pi-out'); }

  function _addBlock(cls) {
    const d = document.createElement('div');
    d.className = cls;
    _out().appendChild(d);
    return d;
  }

  function _scroll() {
    const p = el('pi-panel');
    if (p) p.scrollTop = p.scrollHeight;
  }

  function _tick() {
    const s = Math.round((Date.now() - _start) / 1000);
    const live = el('pi-live');
    if (live) live.textContent = `läuft … ${s} s`;
  }

  // ── Konfiguration laden ───────────────────────────────────────────────────

  async function refresh() {
    try {
      _cfg = await (await fetch('/api/pi/config')).json();
    } catch (e) {
      _cfg = { installed: false, hinweis: 'Konfiguration nicht abrufbar: ' + e.message };
    }
    const st = el('pi-status');
    const zeig = (id, an) => { const x = el(id); if (x) x.style.display = an ? '' : 'none'; };

    if (!_cfg.installed) {
      if (st) st.textContent = 'Pi ist nicht installiert';
      zeig('pi-missing', true); zeig('pi-off', false); zeig('pi-controls', false);
      return;
    }
    if (_cfg.zugriff === 'off') {
      if (st) st.textContent = (_cfg.version || 'Pi') + ' · Zugriff: aus';
      zeig('pi-missing', false); zeig('pi-off', true); zeig('pi-controls', false);
      return;
    }
    const stufe = _cfg.zugriff === 'write' ? 'lesen, anlegen und ändern' : 'nur lesen und rechnen';
    if (st) st.textContent = `${_cfg.version || 'Pi'} · Zugriff: ${stufe}`;
    zeig('pi-missing', false); zeig('pi-off', false); zeig('pi-controls', true);

    // Modell-Auswahl aus Pis eigenem Katalog (meist der lokale Ollama-Provider)
    const sel = el('pi-model');
    if (sel) {
      const vorher = sel.value;
      sel.innerHTML = '';
      const liste = _cfg.models || [];
      if (!liste.length) {
        sel.innerHTML = '<option value="">— Pi hat kein Modell konfiguriert —</option>';
      } else {
        // Zwei Gruppen: Pis eigener Katalog und die übrigen lokalen Modelle.
        // Pi nimmt auch Modelle außerhalb seines Katalogs ("custom model id") —
        // und gerade die kleinen lokalen sind für den Agenten-Loop die
        // brauchbaren, während 27B-Modelle hier zu langsam sind.
        const gruppe = (label) => {
          const g = document.createElement('optgroup');
          g.label = label;
          sel.appendChild(g);
          return g;
        };
        const imKatalog = liste.filter(m => m.katalog);
        const weitere = liste.filter(m => !m.katalog);
        const fuelle = (g, arr) => arr.forEach(m => {
          const o = document.createElement('option');
          o.value = m.provider + '/' + m.model;
          o.textContent = m.provider + ' · ' + m.model;
          g.appendChild(o);
        });
        if (weitere.length) fuelle(gruppe('Lokale Modelle (empfohlen: klein & schnell)'), weitere);
        if (imKatalog.length) fuelle(gruppe('Aus Pis Katalog'), imKatalog);
        const wunsch = (_cfg.provider && _cfg.model)
          ? _cfg.provider + '/' + _cfg.model : '';
        const empf = liste.find(m => m.empfohlen);
        const empfWert = empf ? empf.provider + '/' + empf.model : '';
        const alle = [...sel.options].map(o => o.value);
        sel.value = (alle.includes(vorher) && vorher)
          || (alle.includes(wunsch) && wunsch)
          || (alle.includes(empfWert) && empfWert)
          || alle[0];
      }
    }
  }

  // ── Lauf ──────────────────────────────────────────────────────────────────

  async function run() {
    const task = (el('pi-task')?.value || '').trim();
    if (!task) { showToast('Bitte eine Aufgabe eingeben'); return; }
    if (_abort) { showToast('Es läuft schon ein Agent'); return; }

    const wahl = (el('pi-model')?.value || '').trim();
    const i = wahl.indexOf('/');
    const provider = i > 0 ? wahl.slice(0, i) : '';
    const model = i > 0 ? wahl.slice(i + 1) : wahl;

    // Auftrag als eigener Block, damit mehrere Läufe untereinander stehen
    const auftrag = _addBlock('pi-task-echo');
    auftrag.style.cssText = 'margin:10px 0 4px;font-size:13px;font-weight:600';
    auftrag.textContent = '▶ ' + task;

    const schritte = _addBlock('mathe-stages');
    const antwort = _addBlock('bubble-text');
    antwort.style.cssText = 'font-size:13.5px;margin:6px 0 14px';

    let text = '';
    let denkBlock = null, denkText = '';
    el('pi-run').style.display = 'none';
    el('pi-stop').style.display = '';
    _start = Date.now();
    _timer = setInterval(_tick, 1000);
    _tick();

    _abort = new AbortController();
    try {
      const resp = await fetch('/api/pi/run', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        signal: _abort.signal,
        body: JSON.stringify({
          prompt: task, provider, model,
          thinking: el('pi-thinking')?.value || '',
          continue: !!el('pi-continue')?.checked,
        }),
      });
      if (!resp.ok) {
        let d = 'HTTP ' + resp.status;
        try { d = (await resp.json()).detail || d; } catch (_) {}
        throw new Error(d);
      }
      const reader = resp.body.getReader();
      const dec = new TextDecoder();
      let buf = '';
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buf += dec.decode(value, { stream: true });
        const lines = buf.split('\n');
        buf = lines.pop();
        for (const line of lines) {
          if (!line.startsWith('data: ')) continue;
          let ev; try { ev = JSON.parse(line.slice(6)); } catch (_) { continue; }

          if (ev.type === 'pi_start') {
            const d = document.createElement('div');
            d.className = 'research-log-line';
            d.textContent = `🤝 ${ev.model || 'Modell'} · Arbeitsordner ${ev.workspace}`;
            schritte.appendChild(d);
          } else if (ev.type === 'pi_tool') {
            _renderTool(schritte, ev);
          } else if (ev.type === 'thinking') {
            if (!denkBlock) {
              denkBlock = document.createElement('details');
              denkBlock.className = 'mathe-stage';
              denkBlock.innerHTML = '<summary>🤔 Denken</summary>'
                + '<div class="mathe-stage-body"><pre class="mathe-stage-pre"></pre></div>';
              schritte.appendChild(denkBlock);
            }
            denkText += ev.content || '';
            denkBlock.querySelector('pre').textContent = denkText;
          } else if (ev.type === 'text') {
            text += ev.content || '';
            antwort.textContent = text;
          } else if (ev.type === 'progress') {
            const live = el('pi-live');
            if (live) live.textContent = `läuft … ${ev.seconds} s (Modell denkt)`;
          } else if (ev.type === 'notice') {
            const d = document.createElement('div');
            d.className = 'research-log-line';
            d.style.opacity = '.75';
            d.textContent = 'ℹ ' + (ev.message || '');
            schritte.appendChild(d);
          } else if (ev.type === 'error') {
            const d = document.createElement('div');
            d.style.cssText = 'color:#ef4444;font-size:12.5px;margin:4px 0';
            d.textContent = 'Fehler: ' + (ev.content || '');
            schritte.appendChild(d);
          } else if (ev.type === 'done') {
            if (ev.tokens && typeof TokenMeter !== 'undefined') {
              TokenMeter.add(ev.tokens, 'Pi-Agent');
            }
          }
          _scroll();
        }
      }
      if (text) _md(antwort, text);
      if (!text) antwort.innerHTML = '<em class="planner-muted">Keine Antwort erhalten — '
        + 'die Schritte oben zeigen, wo es hing.</em>';
    } catch (e) {
      if (e.name !== 'AbortError') {
        const d = document.createElement('div');
        d.style.cssText = 'color:#ef4444;font-size:12.5px';
        d.textContent = 'Abgebrochen: ' + e.message;
        schritte.appendChild(d);
      }
    } finally {
      clearInterval(_timer); _timer = null;
      _abort = null;
      el('pi-run').style.display = '';
      el('pi-stop').style.display = 'none';
      const live = el('pi-live');
      if (live) live.textContent = `fertig (${Math.round((Date.now() - _start) / 1000)} s)`;
      _scroll();
    }
  }

  /** Werkzeugaufruf als aufklappbarer Block — das Sichtbarmachen ist der Zweck.
   *
   * Geschlüsselt wird über die toolCallId, nicht über den Namen: dasselbe
   * Werkzeug kann mehrfach aufgerufen werden, und Start und Ergebnis kommen
   * als zwei Frames. Über die ID landen sie zuverlässig im selben Block.
   */
  function _renderTool(host, ev) {
    const name = ev.name || 'Werkzeug';
    const id = ev.id || ('x' + Math.random().toString(36).slice(2));
    let det = host.querySelector(`details[data-call="${CSS.escape(id)}"]`);
    if (!det) {
      det = document.createElement('details');
      det.className = 'mathe-stage';
      det.dataset.call = id;
      // Framework-Werkzeuge hervorheben: genau das soll der Nutzer sehen.
      const istFw = /^fw_/.test(name);
      const marke = istFw ? '🧩' : '🔧';
      const anzeige = istFw ? name.replace(/^fw_/, '') + ' (Framework)' : name;
      det.innerHTML = `<summary>${marke} ${_esc(anzeige)}`
        + '<span class="mathe-stage-spin"> …</span></summary>'
        + '<div class="mathe-stage-body"></div>';
      host.appendChild(det);
    }
    const body = det.querySelector('.mathe-stage-body');
    const add = (titel, wert, fehler) => {
      if (!wert) return;
      const h = document.createElement('div');
      h.style.cssText = 'font-size:11.5px;color:var(--text-muted);margin-top:4px';
      h.textContent = titel;
      const pre = document.createElement('pre');
      pre.className = 'mathe-stage-pre';
      if (fehler) pre.style.color = '#ef4444';
      pre.textContent = wert;
      body.appendChild(h); body.appendChild(pre);
    };
    if (ev.event === 'start') add('Aufruf', ev.input);
    if (ev.event === 'result') {
      add(ev.fehler ? 'Fehler' : 'Ergebnis', ev.output, ev.fehler);
      det.querySelector('.mathe-stage-spin')?.remove();
      if (ev.fehler) det.open = true;
    }
  }

  function stop() {
    if (_abort) { _abort.abort(); showToast('Agent gestoppt'); }
  }

  function clear() {
    if (_out()) _out().innerHTML = '';
    const live = el('pi-live'); if (live) live.textContent = '';
  }

  function init() {
    el('pi-run')?.addEventListener('click', run);
    el('pi-stop')?.addEventListener('click', stop);
    el('pi-clear')?.addEventListener('click', clear);
    el('pi-task')?.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) { e.preventDefault(); run(); }
    });
    refresh();
  }

  return { init, refresh, run, stop, clear };
})();
