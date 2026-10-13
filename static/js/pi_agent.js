/* pi_agent.js — Pi-Agent-Tab
 *
 * Fährt den externen Agenten-Harness Pi (https://pi.dev) über
 * POST /api/pi/run (SSE).
 *
 * Zwei Spalten, weil es zwei verschiedene Dinge sind:
 *   LINKS  der Agent — was er fragt, was er tut (Werkzeugaufrufe, Denken) und
 *          das Eingabefeld. Hier ANTWORTET man ihm: der Agent stellt Rückfragen
 *          („Mit welchen Werten soll ich rechnen?"), und ohne einen Ort dafür
 *          war der Tab eine Sackgasse.
 *   RECHTS das Ergebnis — nur die Antworten, zum Lesen und Kopieren, ohne
 *          Werkzeug-Rauschen.
 *
 * Gespräch statt Einzellauf: eine feste `session_id` je Gespräch geht an den
 * Server, der daraus `--session-id` macht. Pi legt die Sitzung beim ersten Mal
 * an und setzt danach genau dort fort — der Agent kennt also den Verlauf.
 */
const PiAgent = (function () {
  'use strict';

  let _cfg = null;
  let _abort = null;
  let _timer = null;
  let _start = 0;
  let _session = '';
  let _letzteAntwort = '';
  const LS_BREITE = 'ai_framework_pi_leftw';

  const el = (id) => document.getElementById(id);

  // ── Darstellung ───────────────────────────────────────────────────────────

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

  const _esc = (s) => String(s == null ? '' : s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');

  function _neueSession() {
    _session = 'tab-' + Date.now().toString(36) + '-'
      + Math.random().toString(36).slice(2, 8);
  }

  function _scrollConv() {
    const c = el('pi-conv');
    if (c) c.scrollTop = c.scrollHeight;
  }

  function _tick() {
    const live = el('pi-live');
    if (live) live.textContent = `läuft … ${Math.round((Date.now() - _start) / 1000)} s`;
  }

  /** Zeile im Gesprächsverlauf (links). */
  function _convBlock(cls, inhalt, stil) {
    const d = document.createElement('div');
    if (cls) d.className = cls;
    if (stil) d.style.cssText = stil;
    if (inhalt != null) d.textContent = inhalt;
    el('pi-conv').appendChild(d);
    _scrollConv();
    return d;
  }

  // ── Konfiguration ─────────────────────────────────────────────────────────

  async function refresh() {
    try {
      _cfg = await (await fetch('/api/pi/config')).json();
    } catch (e) {
      _cfg = { installed: false, hinweis: 'Konfiguration nicht abrufbar: ' + e.message };
    }
    const st = el('pi-status');
    const zeig = (id, an, anzeige) => {
      const x = el(id);
      if (x) x.style.display = an ? (anzeige || '') : 'none';
    };

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
    zeig('pi-missing', false); zeig('pi-off', false);
    zeig('pi-controls', true, 'flex');

    const sel = el('pi-model');
    if (sel) {
      const vorher = sel.value;
      sel.innerHTML = '';
      const liste = _cfg.models || [];
      if (!liste.length) {
        sel.innerHTML = '<option value="">— Pi hat kein Modell konfiguriert —</option>';
      } else {
        // Zwei Gruppen: Pi nimmt auch Modelle ausserhalb seines Katalogs
        // ("custom model id") — und gerade die kleinen lokalen sind fuer den
        // Agenten-Loop die brauchbaren, waehrend grosse Modelle hier zu
        // langsam sind.
        const gruppe = (label) => {
          const g = document.createElement('optgroup');
          g.label = label; sel.appendChild(g); return g;
        };
        const fuelle = (g, arr) => arr.forEach(m => {
          const o = document.createElement('option');
          o.value = m.provider + '/' + m.model;
          o.textContent = m.provider + ' · ' + m.model + (m.empfohlen ? '  ★' : '');
          g.appendChild(o);
        });
        const weitere = liste.filter(m => !m.katalog);
        const imKatalog = liste.filter(m => m.katalog);
        if (weitere.length) fuelle(gruppe('Lokale Modelle (klein & schnell)'), weitere);
        if (imKatalog.length) fuelle(gruppe('Aus Pis Katalog'), imKatalog);
        const empf = liste.find(m => m.empfohlen);
        const empfWert = empf ? empf.provider + '/' + empf.model : '';
        const alle = [...sel.options].map(o => o.value);
        sel.value = (alle.includes(vorher) && vorher)
          || (alle.includes(empfWert) && empfWert)
          || alle[0];
      }
    }
    if (!_session) _neueSession();
  }

  // ── Lauf ──────────────────────────────────────────────────────────────────

  async function run() {
    const task = (el('pi-task')?.value || '').trim();
    if (!task) { showToast('Bitte etwas eingeben'); return; }
    if (_abort) { showToast('Der Agent arbeitet noch'); return; }
    if (!_session) _neueSession();

    const wahl = (el('pi-model')?.value || '').trim();
    const i = wahl.indexOf('/');
    const provider = i > 0 ? wahl.slice(0, i) : '';
    const model = i > 0 ? wahl.slice(i + 1) : wahl;

    // Links: was ich gesagt habe
    _convBlock('', '▶ ' + task,
      'margin:10px 0 4px;font-size:13px;font-weight:600;color:var(--text)');
    el('pi-task').value = '';
    el('pi-task').placeholder = 'Antwort auf die Rückfrage oder nächste Aufgabe … (Strg+Enter sendet)';

    const schritte = _convBlock('mathe-stages', null, 'margin-bottom:6px');
    // Die laufende Antwort erscheint zuerst links (als Lebenszeichen) und
    // wandert am Ende als fertiger Block nach rechts.
    const vorschau = _convBlock('', null,
      'font-size:12.5px;white-space:pre-wrap;color:var(--text-dim);margin-bottom:8px');

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
          session_id: _session,
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
            d.textContent = `🤝 ${ev.model || 'Modell'}`;
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
            vorschau.textContent = text.slice(-400);
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
          _scrollConv();
        }
      }

      vorschau.remove();
      if (text) {
        _letzteAntwort = text;
        _ergebnis(text);
        // Links nur ein kurzer Verweis — die Antwort steht rechts.
        _convBlock('', '↳ Antwort steht rechts',
          'font-size:11.5px;color:var(--text-muted);margin-bottom:4px');
      } else {
        _convBlock('', 'Keine Antwort erhalten — die Schritte oben zeigen, wo es hing.',
          'font-size:12px;color:var(--text-muted)');
      }
    } catch (e) {
      vorschau.remove();
      if (e.name !== 'AbortError') {
        _convBlock('', 'Abgebrochen: ' + e.message,
          'color:#ef4444;font-size:12.5px');
      }
    } finally {
      clearInterval(_timer); _timer = null;
      _abort = null;
      el('pi-run').style.display = '';
      el('pi-stop').style.display = 'none';
      const live = el('pi-live');
      if (live) live.textContent = `fertig (${Math.round((Date.now() - _start) / 1000)} s)`;
      _scrollConv();
    }
  }

  /** Eine Antwort rechts anhängen (ältere bleiben stehen — nichts geht verloren). */
  function _ergebnis(text) {
    const out = el('pi-out');
    if (out.children.length) {
      const hr = document.createElement('hr');
      hr.style.cssText = 'border:none;border-top:1px solid var(--border);margin:14px 0';
      out.appendChild(hr);
    }
    const d = document.createElement('div');
    d.className = 'bubble-text';
    d.style.cssText = 'font-size:13.5px';
    _md(d, text);
    out.appendChild(d);
    out.scrollTop = out.scrollHeight;
    el('pi-copy').style.display = '';
  }

  /** Werkzeugaufruf als aufklappbarer Block — das Sichtbarmachen ist der Zweck.
   *
   * Geschlüsselt über die toolCallId: dasselbe Werkzeug kann mehrfach laufen,
   * und Start und Ergebnis kommen als zwei Frames.
   */
  function _renderTool(host, ev) {
    const name = ev.name || 'Werkzeug';
    const id = ev.id || ('x' + Math.random().toString(36).slice(2));
    let det = host.querySelector(`details[data-call="${CSS.escape(id)}"]`);
    if (!det) {
      det = document.createElement('details');
      det.className = 'mathe-stage';
      det.dataset.call = id;
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

  // ── Bedienung ─────────────────────────────────────────────────────────────

  function stop() {
    if (_abort) { _abort.abort(); showToast('Agent gestoppt'); }
  }

  function neu() {
    if (_abort) { showToast('Erst den laufenden Agenten stoppen'); return; }
    _neueSession();
    el('pi-conv').innerHTML = '';
    el('pi-out').innerHTML = '';
    el('pi-copy').style.display = 'none';
    _letzteAntwort = '';
    el('pi-task').placeholder = 'Aufgabe für den Agenten … (Strg+Enter sendet)';
    const live = el('pi-live'); if (live) live.textContent = '';
    showToast('Neues Gespräch — der Agent beginnt ohne Verlauf');
  }

  function clear() {
    el('pi-out').innerHTML = '';
    el('pi-copy').style.display = 'none';
  }

  async function kopieren() {
    if (!_letzteAntwort) return;
    try {
      await navigator.clipboard.writeText(_letzteAntwort);
      showToast('Ergebnis kopiert');
    } catch (_) {
      showToast('Kopieren nicht möglich');
    }
  }

  /** Ziehbarer Trenner; Breite bleibt im localStorage. */
  function _initSplitter() {
    const sp = el('pi-splitter');
    const links = el('pi-left');
    const split = el('pi-split');
    if (!sp || !links || !split) return;
    const gespeichert = parseFloat(localStorage.getItem(LS_BREITE) || '');
    if (gespeichert >= 20 && gespeichert <= 80) links.style.width = gespeichert + '%';
    let aktiv = false;
    const bewege = (e) => {
      if (!aktiv) return;
      const r = split.getBoundingClientRect();
      let pct = ((e.clientX - r.left) / r.width) * 100;
      pct = Math.max(20, Math.min(80, pct));
      links.style.width = pct + '%';
    };
    const ende = () => {
      if (!aktiv) return;
      aktiv = false;
      document.body.style.userSelect = '';
      try {
        localStorage.setItem(LS_BREITE, String(parseFloat(links.style.width) || 46));
      } catch (_) {}
    };
    sp.addEventListener('mousedown', (e) => {
      aktiv = true; e.preventDefault();
      document.body.style.userSelect = 'none';
    });
    document.addEventListener('mousemove', bewege);
    document.addEventListener('mouseup', ende);
  }

  function init() {
    el('pi-run')?.addEventListener('click', run);
    el('pi-stop')?.addEventListener('click', stop);
    el('pi-new')?.addEventListener('click', neu);
    el('pi-clear')?.addEventListener('click', clear);
    el('pi-copy')?.addEventListener('click', kopieren);
    el('pi-help')?.addEventListener('click', () => {
      const t = el('pi-intro');
      if (t) t.style.display = t.style.display === 'none' ? '' : 'none';
    });
    el('pi-task')?.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) { e.preventDefault(); run(); }
    });
    _initSplitter();
    _neueSession();
    refresh();
  }

  return { init, refresh, run, stop, neu, clear };
})();
