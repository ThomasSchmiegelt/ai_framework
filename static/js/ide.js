/* AI_Framework_Thomas — Code-IDE */

const CodeIDE = (() => {

  /* ── Zustand ─────────────────────────────────────────────────── */
  let _currentId   = null;
  let _currentName = '';
  let _dirty       = false;
  let _generating  = false;
  let _lastErrors  = [];   // letzte Konsolen-Fehler für Auto-Reparatur
  let _cm          = null; // CodeMirror-Instanz (null = Textarea-Fallback)
  let _lang        = 'js'; // 'js' = Canvas-Vorschau (Browser), 'py' = Python (Server)

  /* ── KI-Prompts ──────────────────────────────────────────────── */
  const SYSTEM_PROMPT =
    'Du bist ein Code-Assistent für AI_Framework_Thomas. Du erstellst technische Visualisierungen mit HTML5 Canvas.\n' +
    '\n' +
    'GRUNDREGELN:\n' +
    '- Nur Vanilla JavaScript — KEIN require(), KEIN import, KEIN Chart.js, KEIN D3\n' +
    '- "canvas" und "ctx" sind bereits definiert — NICHT neu deklarieren\n' +
    '- Nutze canvas.width und canvas.height für alle Positionen (kein festes 660×430)\n' +
    '\n' +
    'INTERAKTIVE EINGABEN mit ai_framework_thomas_input():\n' +
    '  const wert = ai_framework_thomas_input(id, beschriftung, standardwert, {min, max, step})\n' +
    '  → erzeugt automatisch ein Eingabefeld unter dem Canvas\n' +
    '  → gibt immer den aktuellen Wert zurück\n' +
    '  Beispiel: const c = ai_framework_thomas_input("c", "Federrate [N/mm]", 25, {min:1, max:200, step:1})\n' +
    '\n' +
    'PFLICHT-STRUKTUR — JEDES Programm endet mit ai_framework_thomas_run(draw):\n' +
    '  function draw() {\n' +
    '    const W = canvas.width, H = canvas.height;\n' +
    '    ctx.clearRect(0,0,W,H);\n' +
    '    const param = ai_framework_thomas_input("param","Beschriftung",defaultwert,{min,max,step});\n' +
    '    // Zeichencode...\n' +
    '  }\n' +
    '  ai_framework_thomas_run(draw);\n' +
    '\n' +
    'Antworte IMMER mit vollständigem Code in einem ```javascript Block, dann 1-2 Sätze Erklärung.\n' +
    'Beschriftungen, Achsen und Einheiten auf Deutsch. Farben: #3b76ba, #d4e8f8, #0a1e33.';

  const REPAIR_PROMPT =
    'Du bist ein Code-Reparatur-Assistent für AI_Framework_Thomas. Ein Canvas-Programm hat einen Fehler.\n' +
    'Regeln: Nur Vanilla JS, KEIN require/import, "canvas" und "ctx" sind bereits definiert.\n' +
    'Das Programm MUSS mit ai_framework_thomas_run(draw) enden. ai_framework_thomas_input() für Eingabefelder.\n' +
    'Antworte IMMER mit dem vollständigen korrigierten Code in einem ```javascript Block.\n' +
    'Danach 1 Satz: was war der Fehler und wie wurde er behoben.';

  const PY_SYSTEM_PROMPT =
    'Du bist ein Python-Code-Assistent für AI_Framework_Thomas. Der Code wird serverseitig ausgeführt.\n' +
    '\n' +
    'GRUNDREGELN:\n' +
    '- Schreibe eigenständigen Python-Code; gib Ergebnisse mit print(...) aus.\n' +
    '- Verfügbar: math, statistics, random, datetime, json, re, itertools, functools, collections,\n' +
    '  numpy (np), scipy, sympy (sp), pandas (pd) und matplotlib.pyplot (plt).\n' +
    '- Für Diagramme matplotlib nutzen: plt.plot(...) usw. KEIN plt.show() nötig — erzeugte Figuren\n' +
    '  werden automatisch angezeigt.\n' +
    '- KEIN Datei-/Netzwerkzugriff, kein os/sys/subprocess, keine Endlosschleifen (Zeitlimit 15 s).\n' +
    '\n' +
    'Antworte IMMER mit vollständigem Code in einem ```python Block, dann 1–2 Sätze Erklärung. Deutsch.';

  const PY_REPAIR_PROMPT =
    'Du bist ein Python-Code-Reparatur-Assistent für AI_Framework_Thomas. Der Code wird serverseitig ausgeführt.\n' +
    'Regeln: eigenständiges Python, Ausgaben mit print(...); Diagramme via matplotlib.pyplot (plt), kein plt.show();\n' +
    'verfügbar sind numpy/scipy/sympy/pandas; kein Datei-/Netzwerkzugriff.\n' +
    'Antworte IMMER mit dem vollständigen korrigierten Code in einem ```python Block, danach 1 Satz Erklärung.';

  /* ── Hilfsfunktionen ─────────────────────────────────────────── */
  const _editor  = () => document.getElementById('ide-editor');
  const _preview = () => document.getElementById('ide-preview');
  const _console = () => document.getElementById('ide-console');
  const _nameEl  = () => document.getElementById('ide-name');

  // Sprache aus dem Code erkennen (für Übernahme aus Chat / Assistent):
  // Canvas-/JS-Marker → 'js', Python-Marker → 'py', sonst null (Auswahl behalten).
  function _detectLang(code) {
    if (!code) return null;
    if (/ai_framework_thomas_run\s*\(|getContext\s*\(|canvas\.(width|height)|\bctx\b/.test(code)) return 'js';
    if (/^\s*(import\s+\w|from\s+\w|def\s+\w|print\s*\()/m.test(code) || /\bplt\.|\bnp\.|\bsp\.|\bpd\./.test(code)) return 'py';
    return null;
  }
  // gewünschte Sprache anwenden, falls erlaubt und abweichend
  function _applyLang(lang, code) {
    let l = null;
    if (lang) l = /^(py|python)$/i.test(lang) ? 'py' : (/^(js|javascript|html)$/i.test(lang) ? 'js' : null);
    if (!l) l = _detectLang(code);
    if (l && l !== _lang && !(l === 'py' && window.AllowPythonExec === false)) _setLang(l, { silent: true });
  }

  function _getCode() { return _cm ? _cm.getValue() : (_editor()?.value || ''); }
  function _setCode(c) {
    if (_cm) { _cm.setValue(c || ''); }
    else { const e = _editor(); if (e) e.value = c || ''; }
  }
  function refresh() { if (_cm) _cm.refresh(); }

  function _setDirty() {
    _dirty = true;
    const btn = document.getElementById('btn-ide-save');
    if (btn) btn.textContent = '💾 Speichern *';
  }
  function _clearDirty() {
    _dirty = false;
    const btn = document.getElementById('btn-ide-save');
    if (btn) btn.textContent = '💾 Speichern';
  }

  /* ── Ausführen ───────────────────────────────────────────────── */
  /* ── AI_Framework_Thomas iframe-Framework (wird bei jedem Run eingebettet) ─── */
  const _IFRAME_FRAMEWORK = `
<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<style>
  *{box-sizing:border-box;margin:0;padding:0}
  html,body{height:100%;overflow:hidden;font-family:Arial,sans-serif;background:#fff}
  #layout{display:flex;flex-direction:column;height:100vh}
  #canvas-wrap{flex:1;overflow:hidden;background:__CANVASBG__;min-height:0}
  #canvas-wrap canvas{display:block}
  #ai_framework_thomas-inputs{
    flex-shrink:0;background:__INPUTBG__;border-top:2px solid __ACCENT__;
    padding:7px 12px;display:none;flex-wrap:wrap;gap:10px 18px;
    align-items:center;min-height:42px;max-height:90px;overflow-y:auto
  }
  .ai_framework_thomas-field{display:flex;align-items:center;gap:5px}
  .ai_framework_thomas-field label{font-size:12px;color:__TEXTDARK__;font-weight:600;white-space:nowrap}
  .ai_framework_thomas-field input[type=number],.ai_framework_thomas-field input[type=range]{
    border:1.5px solid __TEXTDIM__;border-radius:4px;
    padding:3px 6px;font-size:12px;color:__TEXTDARK__;background:#fff
  }
  .ai_framework_thomas-field input[type=number]{width:75px}
  .ai_framework_thomas-field input[type=number]:focus,.ai_framework_thomas-field input[type=range]:focus{
    border-color:__ACCENT__;outline:none
  }
  .ai_framework_thomas-field .ai_framework_thomas-val{font-size:12px;color:__ACCENT__;font-weight:700;min-width:28px}
</style>
</head>
<body>
<div id="layout">
  <div id="canvas-wrap"><canvas id="canvas"></canvas></div>
  <div id="ai_framework_thomas-inputs"></div>
</div>
<script>
// ── AI_Framework_Thomas Canvas-Framework ────────────────────────────────────────
const canvas  = document.getElementById('canvas');
const ctx     = canvas.getContext('2d');
const _panel  = document.getElementById('ai_framework_thomas-inputs');
const _fields = {};
let   _drawFn = null;

function _resize() {
  const w = document.getElementById('canvas-wrap');
  canvas.width  = w.offsetWidth;
  canvas.height = w.offsetHeight;
}

// ai_framework_thomas_input(id, label, defaultVal, {min, max, step, type})
// Erzeugt Eingabefeld beim ersten Aufruf; gibt immer aktuellen Wert zurück
function ai_framework_thomas_input(id, label, defaultVal, opts) {
  opts = opts || {};
  if (!_fields[id]) {
    _panel.style.display = 'flex';
    const field = document.createElement('div');
    field.className = 'ai_framework_thomas-field';
    const lbl = document.createElement('label');
    lbl.textContent = label + ':';
    const type = opts.type || 'number';
    const inp  = document.createElement('input');
    inp.type  = type;
    inp.value = defaultVal;
    if (opts.min  !== undefined) inp.min  = opts.min;
    if (opts.max  !== undefined) inp.max  = opts.max;
    if (opts.step !== undefined) inp.step = opts.step || 'any';
    inp.style.width = (type === 'range') ? '90px' : '75px';
    let valSpan = null;
    if (type === 'range') {
      valSpan = document.createElement('span');
      valSpan.className = 'ai_framework_thomas-val';
      valSpan.textContent = defaultVal;
    }
    inp.addEventListener('input', function() {
      if (valSpan) valSpan.textContent = this.value;
      if (_drawFn) { _resize(); _drawFn(); }
    });
    field.appendChild(lbl);
    field.appendChild(inp);
    if (valSpan) field.appendChild(valSpan);
    _panel.appendChild(field);
    _fields[id] = inp;
  }
  const v = parseFloat(_fields[id].value);
  return isNaN(v) ? defaultVal : v;
}

// ai_framework_thomas_run(fn) — Zeichenfunktion registrieren und sofort ausführen
function ai_framework_thomas_run(fn) {
  _drawFn = fn;
  _resize();
  fn();
}

window.addEventListener('resize', function() {
  if (_drawFn) { _resize(); _drawFn(); }
});

// ── Fehler-Weiterleitung an Eltern-Frame ─────────────────────────
(function() {
  var fwd = function(level, args) {
    window.parent.postMessage({
      type: 'console', level: level,
      text: args.map(function(a){ return typeof a==='object'?JSON.stringify(a):String(a); }).join(' ')
    }, '*');
  };
  console.log   = function(){fwd('log',  Array.prototype.slice.call(arguments));};
  console.warn  = function(){fwd('warn', Array.prototype.slice.call(arguments));};
  console.error = function(){fwd('error',Array.prototype.slice.call(arguments));};
  window.onerror = function(msg,_s,line){
    window.parent.postMessage({type:'console',level:'error',text:'Zeile '+line+': '+msg},'*');
  };
})();

// ── NUTZER-CODE ──────────────────────────────────────────────────
`;

  const _IFRAME_CLOSE = `
// ── Ende Nutzer-Code ─────────────────────────────────────────────
// Fallback: wenn kein ai_framework_thomas_run() aufgerufen wurde, einmal resize
if (!_drawFn) { _resize(); }
<\/script>
</body>
</html>`;

  function _run() {
    if (_lang === 'py') return _runPython();
    const code  = _getCode().trim();
    const frame = _preview();
    const cons  = _console();
    if (!frame) return;
    _showPyOutput(false);   // Vorschau-iframe wieder einblenden (war evtl. von Python verdeckt)
    if (cons) cons.innerHTML = '';
    _lastErrors = [];
    document.getElementById('btn-ide-repair').style.display = 'none';

    // Immer in das Framework-Template einbetten — Chrome-Farben aus dem aktiven Modus
    const cs = getComputedStyle(document.documentElement);
    const cv = (n, fb) => (cs.getPropertyValue(n).trim() || fb);
    const tokens = {
      '__CANVASBG__': cv('--bg-sidebar', '#0a1e33'),
      '__INPUTBG__':  cv('--accent-dim', '#f0f6fb'),
      '__ACCENT__':   cv('--accent', '#3b76ba'),
      '__TEXTDARK__': cv('--bg-input', '#11314f'),
      '__TEXTDIM__':  cv('--text-dim', '#a3c8eb'),
    };
    let fw = _IFRAME_FRAMEWORK;
    for (const [k, val] of Object.entries(tokens)) fw = fw.split(k).join(val);
    frame.srcdoc = fw + code + _IFRAME_CLOSE;
    if (typeof Logger !== 'undefined') Logger.log('ide_run', { name: _currentName, code_len: code.length });
  }

  /* ── Python serverseitig ausführen ───────────────────────────── */
  const _esc = s => String(s == null ? '' : s).replace(/[&<>]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;' }[c]));

  async function _runPython() {
    const code = _getCode().trim();
    const out  = document.getElementById('ide-py-output');
    const cons = _console();
    if (cons) cons.innerHTML = '';
    _lastErrors = [];
    const repairBtn = document.getElementById('btn-ide-repair');
    if (repairBtn) repairBtn.style.display = 'none';
    _showPyOutput(true);
    if (!out) return;
    if (!code) { out.innerHTML = ''; return; }
    out.innerHTML = '<div class="ide-py-run"><span class="spinner"></span> Python läuft…</div>';
    try {
      const resp = await fetch('/api/code/run-python', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ code }),
      });
      let data;
      try { data = await resp.json(); } catch { data = {}; }
      if (!resp.ok) throw new Error(data.detail || ('HTTP ' + resp.status));
      _renderPyResult(data);
    } catch (e) {
      out.innerHTML = `<pre class="ide-py-err">Fehler: ${_esc(e.message)}</pre>`;
    }
    if (typeof Logger !== 'undefined') Logger.log('ide_run_py', { name: _currentName, code_len: code.length });
  }

  function _renderPyResult(data) {
    const out = document.getElementById('ide-py-output');
    if (!out) return;
    out.innerHTML = '';
    for (const src of (data.images || [])) {
      const img = document.createElement('img');
      img.className = 'ide-py-img'; img.src = src;
      out.appendChild(img);
    }
    const txt = (data.output || '').replace(/\s+$/, '');
    if (txt) {
      const pre = document.createElement('pre');
      pre.className = 'ide-py-out'; pre.textContent = txt;
      out.appendChild(pre);
    }
    if (data.error) {
      const pre = document.createElement('pre');
      pre.className = 'ide-py-err'; pre.textContent = data.error;
      out.appendChild(pre);
      _lastErrors = [data.error];
      const repairBtn = document.getElementById('btn-ide-repair');
      if (repairBtn) repairBtn.style.display = '';   // Auto-Reparatur auch für Python
    }
    if (!txt && !data.error && !(data.images || []).length) {
      out.innerHTML = '<div class="ide-py-run">✓ Ausgeführt (keine Ausgabe)</div>';
    }
  }

  /* Vorschau (JS-Canvas) vs. Python-Ausgabe umschalten */
  function _showPyOutput(showPy) {
    const frame = _preview();
    const out   = document.getElementById('ide-py-output');
    if (frame) frame.style.display = showPy ? 'none' : '';
    if (out)   out.style.display   = showPy ? '' : 'none';
  }

  /* Sprache umschalten (JS ⇄ Python): Editor-Modus, Vorschau, gespeicherte Wahl */
  function _setLang(lang, opts) {
    // Python serverseitig deaktiviert (Installer/Server) → immer JS
    if (lang === 'py' && window.AllowPythonExec === false) lang = 'js';
    _lang = (lang === 'py') ? 'py' : 'js';
    try { localStorage.setItem('ide_lang', _lang); } catch (_) {}
    const sel = document.getElementById('ide-lang');
    if (sel) sel.value = _lang;
    if (_cm) _cm.setOption('mode', _lang === 'py' ? 'python' : 'javascript');
    _showPyOutput(_lang === 'py');
    // Beim manuellen Umschalten alte Ausgaben leeren (nicht beim Initialisieren)
    if (!opts || !opts.silent) {
      const out = document.getElementById('ide-py-output'); if (out) out.innerHTML = '';
      const f = _preview(); if (f && _lang === 'js') f.srcdoc = '';
      const c = _console(); if (c) c.innerHTML = '';
      const rb = document.getElementById('btn-ide-repair'); if (rb) rb.style.display = 'none';
    }
  }

  /* ── Speichern ───────────────────────────────────────────────── */
  async function _save() {
    const name = (_nameEl()?.value || '').trim() || 'Unbenannt';
    const code = _getCode();
    try {
      const resp = await fetch('/api/code', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ id: _currentId, name, code }),
      });
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const data = await resp.json();
      _currentId   = data.id;
      _currentName = data.name;
      _clearDirty();
      showToast(`✓ Gespeichert: ${name}`);
      await _loadList();
    } catch (e) {
      showToast('Speichern fehlgeschlagen: ' + e.message);
    }
  }

  /* ── Dateiliste ──────────────────────────────────────────────── */
  async function _loadList() {
    const listEl = document.getElementById('ide-file-list-inline');
    const sel    = document.getElementById('ide-load-select');
    try {
      const programs = await (await fetch('/api/code')).json();
      if (listEl) {
        listEl.innerHTML = '';
        for (const p of programs) {
          const item = document.createElement('span');
          item.className = 'ide-file-chip' + (p.id === _currentId ? ' active' : '');
          item.textContent = p.name;
          item.title = p.name;
          item.addEventListener('click', () => _openProgram(p.id));
          listEl.appendChild(item);
        }
      }
      if (sel) {
        sel.innerHTML = `<option value="">📂 Gespeicherte laden… (${programs.length})</option>`
          + programs.map(p => `<option value="${_esc(p.id)}"${p.id === _currentId ? ' selected' : ''}>${_esc(p.name)}</option>`).join('');
      }
    } catch (_) {}
  }

  // Aktuell geladenes Programm löschen (DELETE /api/code/{id}), dann Editor leeren.
  async function _deleteCurrent() {
    if (!_currentId) { showToast('Kein gespeichertes Programm geladen'); return; }
    if (!confirm(`Programm „${_currentName}" wirklich löschen?`)) return;
    try {
      const resp = await fetch(`/api/code/${_currentId}`, { method: 'DELETE' });
      if (!resp.ok) throw new Error('HTTP ' + resp.status);
      showToast('✓ Gelöscht');
      _clearDirty();
      _new();   // Editor/Vorschau leeren + Liste neu laden
    } catch (e) {
      showToast('Löschen fehlgeschlagen: ' + e.message);
    }
  }

  async function _openProgram(id) {
    if (_dirty && !confirm('Ungespeicherte Änderungen verwerfen?')) return;
    try {
      const data = await (await fetch(`/api/code/${id}`)).json();
      _currentId   = data.id;
      _currentName = data.name;
      if (_nameEl()) _nameEl().value = data.name;
      _setCode(data.code);
      _clearDirty();
      await _loadList();
    } catch (e) {
      showToast('Laden fehlgeschlagen: ' + e.message);
    }
  }

  /* ── Code aus dem Chat übernehmen (Programmier-Agent) ────────── */
  function loadFromChat(code, name, lang) {
    if (!code) return;
    if (_dirty && !confirm('Ungespeicherte Änderungen in der IDE verwerfen und Chat-Code übernehmen?')) return;
    if (typeof switchTab === 'function') switchTab('ide');
    _applyLang(lang, code);   // Sprache aus Codeblock/Heuristik → richtige Ausführ-Engine
    _currentId = null;
    _currentName = (name || 'Chat-Programm').trim();
    if (_nameEl()) _nameEl().value = _currentName;
    _setCode(code);
    _setDirty();
    refresh();
    const c = _console(); if (c) c.innerHTML = '';
    _lastErrors = [];
    const repairBtn = document.getElementById('btn-ide-repair');
    if (repairBtn) repairBtn.style.display = 'none';
    _run();
    if (typeof showToast === 'function') showToast('✓ Code aus dem Chat in die IDE übernommen');
  }

  /* ── Neu ─────────────────────────────────────────────────────── */
  function _new() {
    if (_dirty && !confirm('Ungespeicherte Änderungen verwerfen?')) return;
    _currentId = null; _currentName = '';
    if (_nameEl()) _nameEl().value = '';
    _setCode(''); _clearDirty();
    const f = _preview(); if (f) f.srcdoc = '';
    const c = _console(); if (c) c.innerHTML = '';
    _lastErrors = [];
    document.getElementById('btn-ide-repair').style.display = 'none';
    _loadList();
  }

  /* ── Beispiele ───────────────────────────────────────────────── */
  function _loadExample(key) {
    if (_dirty && !confirm('Ungespeicherte Änderungen verwerfen?')) return;
    const tbl = {
      toleranz: ['Toleranzanalyse', EXAMPLE_TOLERANZ],
      kurve:    ['Federkennlinie',   EXAMPLE_FEDERKURVE],
      python:   ['Python-Beispiel',  EXAMPLE_PYTHON],
      leer:     ['Neues Programm',   EXAMPLE_LEER],
    };
    const [name, code] = tbl[key] || tbl.leer;
    _setLang(key === 'python' ? 'py' : 'js', { silent: true });
    _currentId = null; _currentName = name;
    if (_nameEl()) _nameEl().value = name;
    _setCode(code); _clearDirty();
    const c = _console(); if (c) c.innerHTML = '';
    const f = _preview(); if (f) f.srcdoc = '';
    _lastErrors = [];
    document.getElementById('btn-ide-repair').style.display = 'none';
  }

  /* ── Chat-Nachrichten ────────────────────────────────────────── */
  function _addChatMsg(role, text) {
    const history = document.getElementById('ide-chat-history');
    if (!history) return null;
    const div = document.createElement('div');
    div.className = `ide-chat-msg ${role}`;
    div.textContent = text;
    history.appendChild(div);
    history.scrollTop = history.scrollHeight;
    return div;
  }

  /* ── Code-Block aus KI-Antwort extrahieren ───────────────────── */
  function _extractCodeBlock(text) {
    // Versucht alle gängigen Code-Block-Formate zu erkennen
    const patterns = [
      /```(?:javascript|js|html|HTML|JS|JavaScript|python|py|Python)\s*\n([\s\S]*?)\n?```/i,
      /```(?:javascript|js|html|python|py)\s*([\s\S]*?)```/i,
      /```\s*\n([\s\S]*?)\n?```/,
      /```([\s\S]*?)```/,
    ];
    for (const p of patterns) {
      const m = text.match(p);
      if (m && m[1] && m[1].trim().length > 10) return m[1].trim();
    }
    return null;
  }

  /* ── UI-Zustand während Generierung ─────────────────────────── */
  function _setGenerating(active) {
    _generating = active;
    const btn   = document.getElementById('btn-ide-chat-send');
    const icon  = document.getElementById('btn-ide-send-icon');
    const label = document.getElementById('btn-ide-send-label');
    const status = document.getElementById('ide-chat-status');
    if (active) {
      if (btn)    btn.disabled = true;
      if (icon)   icon.textContent = '⏳';
      if (label)  label.textContent = 'Wird erstellt…';
      if (status) status.textContent = '';
    } else {
      if (btn)    btn.disabled = false;
      if (icon)   icon.textContent = '▶';
      if (label)  label.textContent = 'Code erstellen';
      if (status) status.textContent = '';
    }
  }

  /* ── Coding-Agenten laden ────────────────────────────────────── */
  let _pendingPrompt = '';   // ursprüngliche Aufgabe während der Rückfragen-Phase

  async function _loadCodingAgents() {
    const sel = document.getElementById('ide-agent-select');
    if (!sel) return;
    try {
      let agents = await (await fetch('/api/agents')).json();
      if (!Array.isArray(agents)) agents = [];
      // Coding-nahe Agenten zuerst (Kategorie/Beispielcode), dann der Rest
      const isCode = a => /program|cod|entwickl|software/i.test((a.category || '') + ' ' + (a.name || '')) || a.example_code;
      agents.sort((a, b) => (isCode(b) ? 1 : 0) - (isCode(a) ? 1 : 0));
      sel.innerHTML = '<option value="">— keiner —</option>' +
        agents.map(a => `<option value="${a.id}">${(a.icon || '🤖')} ${(a.name || a.id)}${a.example_code ? ' · 📎' : ''}</option>`).join('');
    } catch (_) {}
  }

  /* ── KI-Chat senden (Code-Assistent mit Rückfragen) ──────────── */
  async function _sendChat() {
    if (_generating) return;
    const inputEl = document.getElementById('ide-chat-input');
    const msg = inputEl?.value.trim();
    if (!msg) { inputEl?.focus(); return; }
    inputEl.value = '';
    _pendingPrompt = msg;
    await _assist(msg, '', false);
  }

  /* ── Code-Assistent: Rückfragen ⇄ Code ───────────────────────── */
  async function _assist(prompt, answers, forceCode) {
    _setGenerating(true);
    const clarify = document.getElementById('ide-clarify-toggle')?.checked !== false;
    const adaptive = !!document.getElementById('ide-adaptive-toggle')?.checked;
    const agent_id = document.getElementById('ide-agent-select')?.value || '';
    if (answers) _addChatMsg('user', '↳ ' + answers);
    else _addChatMsg('user', prompt.substring(0, 80) + (prompt.length > 80 ? '…' : ''));
    const wrap = _addChatMsg('assistant', '⏳ …');
    const model = (typeof Profile !== 'undefined' && Profile.modelFor) ? Profile.modelFor('coding') : undefined;
    try {
      const resp = await fetch('/api/code/assist', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          prompt, answers, agent_id, adaptive,
          force_code: forceCode || !clarify,
          language: _lang === 'py' ? 'Python' : 'JavaScript',
          current_code: _getCode(), model,
        }),
      });
      const d = await resp.json();
      if (!resp.ok) throw new Error(d.detail || ('HTTP ' + resp.status));
      if (d.tokens && typeof TokenMeter !== 'undefined') TokenMeter.add(d.tokens, 'Code-Assistent');
      if (d.type === 'questions') {
        _renderClarify(wrap, d.questions, d.adaptive_role);
      } else {
        if (d.code) {
          _applyLang(d.language, d.code);   // Engine (JS-Canvas ⇄ Python) zum Code passend setzen
          _setCode(d.code); _setDirty(); refresh(); _run();
        }
        if (wrap) wrap.textContent = (d.adaptive_role ? `🧠 ${d.adaptive_role}\n` : '')
          + (d.note || '✓ Code erstellt') + (d.code ? '\n→ in den Editor übernommen und ausgeführt.' : '');
      }
    } catch (e) {
      if (wrap) wrap.textContent = '❌ ' + e.message;
    } finally {
      _setGenerating(false);
    }
  }

  function _renderClarify(wrap, questions, role) {
    if (wrap) wrap.textContent = (role ? `🧠 ${role}\n` : '') + '❓ Rückfragen vor dem Coden:';
    const box = document.createElement('div');
    box.className = 'ide-chat-msg assistant';
    box.style.cssText = 'display:flex;flex-direction:column;gap:6px';
    box.innerHTML = '<ol style="margin:2px 0 4px 18px;padding:0">' +
      questions.map(q => `<li>${_esc(q)}</li>`).join('') + '</ol>' +
      '<textarea class="ide-clarify-answers" placeholder="Antworten hier (frei, je Zeile)…" ' +
      'style="width:100%;min-height:54px;font-size:12px;background:var(--bg-input);color:var(--text);border:1px solid var(--border);border-radius:4px;padding:5px"></textarea>' +
      '<div style="display:flex;gap:6px"><button class="export-btn ide-clarify-go" style="font-size:11.5px">↑ Antworten &amp; Code erstellen</button>' +
      '<button class="export-btn ide-clarify-skip" style="font-size:11.5px">⏭ Trotzdem coden</button></div>';
    document.getElementById('ide-chat-history').appendChild(box);
    document.getElementById('ide-chat-history').scrollTop = 99999;
    const ta = box.querySelector('.ide-clarify-answers');
    box.querySelector('.ide-clarify-go').addEventListener('click', () => {
      const a = (ta.value || '').trim();
      box.remove();
      _assist(_pendingPrompt, a || '(keine weiteren Angaben)', true);
    });
    box.querySelector('.ide-clarify-skip').addEventListener('click', () => {
      box.remove();
      _assist(_pendingPrompt, '', true);
    });
    ta.focus();
  }

  /* ── Auto-Reparatur ──────────────────────────────────────────── */
  async function _autoRepair() {
    if (_generating || !_lastErrors.length) return;
    const code = _getCode().trim();
    if (!code) { showToast('Kein Code zum Reparieren'); return; }

    const errText = _lastErrors.join('\n');
    _lastErrors = [];
    document.getElementById('btn-ide-repair').style.display = 'none';

    const userMsg = `Dieser Code hat einen Fehler:\n\`\`\`\n${code}\n\`\`\`\n\nFehler:\n${errText}`;
    await _runAI(_lang === 'py' ? PY_REPAIR_PROMPT : REPAIR_PROMPT, userMsg, null, '🔧 Repariere Code…');
  }

  /* ── Gemeinsamer KI-Stream ───────────────────────────────────── */
  async function _runAI(systemPrompt, userMsg, existingCode = null, statusText = null) {
    _setGenerating(true);

    let fullUserMsg = userMsg;
    if (existingCode && existingCode.length > 10) {
      fullUserMsg = userMsg + '\n\nBestehender Code:\n```\n' + existingCode + '\n```';
    }

    const labelText = statusText || userMsg.substring(0, 60) + (userMsg.length > 60 ? '…' : '');
    _addChatMsg('user', labelText);
    const assistantDiv = _addChatMsg('assistant', '⏳ Generiere Code…');

    // IDE-Assistent nutzt das Programmier-Modell aus dem Profil (Fallback: Standardmodell)
    const model = (typeof Profile !== 'undefined' && Profile.modelFor) ? Profile.modelFor('coding') : 'ministral-3:3b';

    try {
      const resp = await fetch('/api/chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          model,
          messages: [
            { role: 'system', content: systemPrompt },
            { role: 'user',   content: fullUserMsg },
          ],
          use_tools: false,
        }),
      });

      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);

      const reader = resp.body.getReader();
      const dec    = new TextDecoder();
      let buf = '', fullText = '';

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buf += dec.decode(value, { stream: true });
        const lines = buf.split('\n');
        buf = lines.pop();

        for (const line of lines) {
          if (!line.startsWith('data: ')) continue;
          let ev;
          try { ev = JSON.parse(line.slice(6)); } catch { continue; }

          if (ev.type === 'text') {
            fullText += ev.content;
            if (assistantDiv) {
              assistantDiv.textContent = fullText;
              document.getElementById('ide-chat-history').scrollTop = 99999;
            }
          }

          if (ev.type === 'done') {
            const code = _extractCodeBlock(fullText);
            if (code) {
              _setCode(code);
              _setDirty();
              _run();
              showToast('✓ Code übernommen und ausgeführt');
              // Kurze Erklärung anzeigen (alles nach dem Code-Block)
              const explanation = fullText.replace(/```[\s\S]*?```/g, '').trim();
              if (explanation && assistantDiv) {
                assistantDiv.textContent = explanation || '✓ Code generiert';
              }
            } else {
              if (assistantDiv) {
                assistantDiv.textContent = '⚠ Kein Code-Block gefunden. Versuchen Sie eine präzisere Beschreibung.';
              }
            }
          }
        }
      }
    } catch (e) {
      if (assistantDiv) assistantDiv.textContent = '⚠ Fehler: ' + e.message;
      showToast('KI-Verbindung fehlgeschlagen');
    } finally {
      _setGenerating(false);
    }
  }

  /* ── Konsolen-Nachrichten aus iframe ─────────────────────────── */
  window.addEventListener('message', e => {
    if (!e.data || e.data.type !== 'console') return;
    const cons = _console();
    if (!cons) return;

    const line = document.createElement('div');
    line.className = `ide-con-line ${e.data.level}`;
    const ts = new Date().toLocaleTimeString('de-DE',
      { hour: '2-digit', minute: '2-digit', second: '2-digit' });
    line.textContent = `[${ts}] ${e.data.text}`;
    cons.appendChild(line);
    cons.scrollTop = cons.scrollHeight;

    // Fehler → Auto-Repair-Button einblenden
    if (e.data.level === 'error') {
      _lastErrors.push(e.data.text);
      const repairBtn = document.getElementById('btn-ide-repair');
      if (repairBtn) repairBtn.style.display = '';
    }
  });

  /* ── Init ────────────────────────────────────────────────────── */
  function init() {
    document.getElementById('btn-ide-run')?.addEventListener('click', _run);
    document.getElementById('btn-ide-save')?.addEventListener('click', _save);
    document.getElementById('btn-ide-new')?.addEventListener('click', _new);
    document.getElementById('ide-load-select')?.addEventListener('change', e => {
      const id = e.target.value;
      if (id) _openProgram(id);
      e.target.value = '';   // Auswahl zurücksetzen, Bezeichnung „laden…" wieder zeigen
    });
    document.getElementById('btn-ide-delete')?.addEventListener('click', _deleteCurrent);
    document.getElementById('btn-ide-example-toleranz')?.addEventListener('click', () => _loadExample('toleranz'));
    document.getElementById('btn-ide-example-kurve')?.addEventListener('click',    () => _loadExample('kurve'));
    document.getElementById('btn-ide-example-py')?.addEventListener('click',       () => _loadExample('python'));
    document.getElementById('btn-ide-example-leer')?.addEventListener('click',     () => _loadExample('leer'));
    document.getElementById('ide-lang')?.addEventListener('change', e => _setLang(e.target.value));
    document.getElementById('btn-ide-chat-send')?.addEventListener('click', _sendChat);
    document.getElementById('btn-ide-repair')?.addEventListener('click', _autoRepair);
    _loadCodingAgents();
    document.querySelector('.tab-btn[data-tab="ide"]')?.addEventListener('click', _loadCodingAgents);

    const chatInput = document.getElementById('ide-chat-input');
    chatInput?.addEventListener('keydown', e => {
      if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); _sendChat(); }
    });

    const ed = _editor();
    if (ed && window.CodeMirror) {
      // Echter Code-Editor (CodeMirror 5) über die vorhandene Textarea
      _cm = CodeMirror.fromTextArea(ed, {
        mode: 'javascript',
        lineNumbers: true,
        indentUnit: 2, tabSize: 2, indentWithTabs: false,
        autoCloseBrackets: true, matchBrackets: true, styleActiveLine: true,
        extraKeys: {
          'Ctrl-Enter': _run, 'Cmd-Enter': _run,
          'Ctrl-S': () => _save(), 'Cmd-S': () => _save(),
          'Ctrl-Space': 'autocomplete',
          Tab: cm => cm.somethingSelected() ? cm.indentSelection('add') : cm.replaceSelection('  '),
        },
      });
      _cm.on('change', _setDirty);
    } else if (ed) {
      // Fallback: nackte Textarea (z. B. wenn CodeMirror-CDN nicht erreichbar)
      ed.addEventListener('keydown', e => {
        if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') { e.preventDefault(); _run(); return; }
        if ((e.ctrlKey || e.metaKey) && e.key === 's')     { e.preventDefault(); _save(); return; }
        if (e.key === 'Tab') {
          e.preventDefault();
          const s = ed.selectionStart, end = ed.selectionEnd;
          ed.value = ed.value.substring(0, s) + '  ' + ed.value.substring(end);
          ed.selectionStart = ed.selectionEnd = s + 2;
          return;
        }
        _setDirty();
      });
      ed.addEventListener('input', _setDirty);
    }

    // Gespeicherte Sprache (JS/Python) wiederherstellen – nach CodeMirror-Init,
    // damit der Editor-Modus korrekt gesetzt wird.
    let savedLang = 'js';
    try { savedLang = localStorage.getItem('ide_lang') || 'js'; } catch (_) {}
    _setLang(savedLang, { silent: true });

    document.querySelector('[data-tab="ide"]')?.addEventListener('click', () => { _loadList(); setTimeout(refresh, 0); });
    _initSplitter();
    _loadList();
  }

  /* ── Ziehbarer Trenner: Breite Editor ↔ Vorschau ─────────────────── */
  function _initSplitter() {
    const splitter = document.getElementById('ide-splitter');
    const body     = document.getElementById('ide-body');
    if (!splitter || !body) return;

    const KEY = 'ide_left_width';
    const saved = parseInt(localStorage.getItem(KEY) || '', 10);
    if (saved > 0) body.style.setProperty('--ide-left-w', saved + 'px');

    const _apply = (clientX) => {
      const rect = body.getBoundingClientRect();
      let w = clientX - rect.left;
      w = Math.max(240, Math.min(w, rect.width - 220));  // Editor ≥240, Vorschau ≥220
      body.style.setProperty('--ide-left-w', w + 'px');
      if (_cm) _cm.refresh();
    };
    const _onMove = (e) => _apply(e.clientX);
    const _onUp = () => {
      splitter.classList.remove('dragging');
      document.body.style.userSelect = '';
      document.removeEventListener('mousemove', _onMove);
      document.removeEventListener('mouseup', _onUp);
      const cur = body.style.getPropertyValue('--ide-left-w');
      if (cur) localStorage.setItem(KEY, String(parseInt(cur, 10) || 0));
      if (_cm) _cm.refresh();
    };
    splitter.addEventListener('mousedown', (e) => {
      e.preventDefault();
      splitter.classList.add('dragging');
      document.body.style.userSelect = 'none';
      document.addEventListener('mousemove', _onMove);
      document.addEventListener('mouseup', _onUp);
    });
    splitter.addEventListener('dblclick', () => {
      body.style.removeProperty('--ide-left-w');
      localStorage.removeItem(KEY);
    });
  }

  /* Python-Option abschalten (vom Profil aufgerufen, wenn allow_python_exec=false) */
  function disablePython() {
    const pyOpt = document.querySelector('#ide-lang option[value="py"]');
    if (pyOpt) pyOpt.style.display = 'none';
    if (_lang === 'py') _setLang('js', { silent: true });
  }

  return { init, loadFromChat, refresh, disablePython };

})();

/* ════════════════════════════════════════════════════════════════
   Beispiel-Programme
════════════════════════════════════════════════════════════════ */

const EXAMPLE_TOLERANZ = `// Toleranzanalyse – Schließmaß (Worst-Case & RSS)
// Werte über die Eingabefelder unten anpassen

function draw() {
  const W = canvas.width, H = canvas.height;
  ctx.fillStyle = '#0a1e33';
  ctx.fillRect(0, 0, W, H);

  // Maßkettenglieder mit interaktiven Toleranzwerten
  const glieder = [
    { name: 'Welle Ø',       nenn:  ai_framework_thomas_input('nenn1','Welle [mm]',   50,  {min:1,max:500,step:0.5}),
      tol: ai_framework_thomas_input('tol1','± Tol. Welle',  0.025,{min:0.001,max:2,step:0.001}), inc:true,  f:'#3b76ba' },
    { name: 'Abstandshülse', nenn:  ai_framework_thomas_input('nenn2','Hülse [mm]',    15,  {min:1,max:200,step:0.5}),
      tol: ai_framework_thomas_input('tol2','± Tol. Hülse',  0.018,{min:0.001,max:2,step:0.001}), inc:true,  f:'#2879c0' },
    { name: 'Lagerring',     nenn:  ai_framework_thomas_input('nenn3','Lager [mm]',    12,  {min:1,max:200,step:0.5}),
      tol: ai_framework_thomas_input('tol3','± Tol. Lager',  0.015,{min:0.001,max:2,step:0.001}), inc:true,  f:'#a3c8eb' },
    { name: 'Gehäuse',       nenn: -ai_framework_thomas_input('nenn4','Gehäuse [mm]',  77,  {min:1,max:800,step:0.5}),
      tol: ai_framework_thomas_input('tol4','± Tol. Gehäuse',0.030,{min:0.001,max:2,step:0.001}), inc:false, f:'#6c6f76' },
  ];

  const nenn    = glieder.reduce((s,g) => s + g.nenn, 0);
  const obGrenz = glieder.reduce((s,g) => g.inc ? s + g.tol : s - g.tol, 0);
  const unGrenz = glieder.reduce((s,g) => g.inc ? s - g.tol : s + g.tol, 0);
  const wcTol   = obGrenz - unGrenz;
  const rssTol  = Math.sqrt(glieder.reduce((s,g) => s + g.tol * g.tol * 4, 0));

  // Titel
  ctx.fillStyle = '#d4e8f8'; ctx.font = 'bold ' + Math.round(W/50) + 'px Arial';
  ctx.fillText('Toleranzanalyse – Schließmaßberechnung', 14, 28);
  ctx.fillStyle = '#6c6f76'; ctx.font = Math.round(W/65) + 'px Arial';
  ctx.fillText('Methode: Worst-Case & RSS  |  Einheit: mm', 14, 46);

  // Balken
  const bX = W * 0.27, bH = Math.max(24, Math.round(H / 14)), bGap = 8;
  const scale = (W * 0.3) / Math.max(...glieder.map(g => Math.abs(g.nenn)));
  glieder.forEach((g, i) => {
    const y = 58 + i * (bH + bGap), bW = Math.abs(g.nenn) * scale;
    const tW = g.tol * scale * 30;
    ctx.fillStyle = g.f + '44'; ctx.fillRect(bX, y, bW, bH);
    ctx.strokeStyle = g.f; ctx.lineWidth = 1.5; ctx.strokeRect(bX, y, bW, bH);
    ctx.fillStyle = g.f + 'cc'; ctx.fillRect(bX + bW + 4, y + 4, Math.max(tW, 3), bH - 8);
    ctx.fillStyle = '#d4e8f8'; ctx.font = '12px Arial';
    ctx.fillText(g.name, 6, y + bH / 2 + 4);
    ctx.fillStyle = '#a3c8eb'; ctx.font = '11px monospace';
    ctx.fillText(g.nenn.toFixed(1) + ' ±' + g.tol.toFixed(3), bX + bW + tW + 10, y + bH / 2 + 4);
  });

  // Ergebnisfeld
  const ry = 58 + glieder.length * (bH + bGap) + 14;
  const rh = Math.min(90, H - ry - 10);
  ctx.fillStyle = '#11314f'; ctx.fillRect(12, ry, W - 24, rh);
  ctx.strokeStyle = '#003a74'; ctx.lineWidth = 1; ctx.strokeRect(12, ry, W - 24, rh);
  const fs = Math.round(W / 60);
  ctx.fillStyle = '#6c6f76'; ctx.font = '10px Arial';
  ctx.fillText('SCHLIESSMAS', 24, ry + 16);
  ctx.fillStyle = '#d4e8f8'; ctx.font = 'bold ' + (fs + 4) + 'px monospace';
  ctx.fillText(nenn.toFixed(3) + ' mm', 120, ry + 18);
  ctx.fillStyle = '#a3c8eb'; ctx.font = fs + 'px Arial';
  ctx.fillText('Worst-Case: ±' + (wcTol/2).toFixed(4) + ' mm', 24, ry + 38);
  ctx.fillStyle = '#2879c0';
  if (ry + 56 < H) ctx.fillText('RSS (stat.): ±' + (rssTol/2).toFixed(4) + ' mm  (−' + ((1-rssTol/wcTol)*100).toFixed(0) + '%)', 24, ry + 56);

  console.log('Schließmaß:', nenn.toFixed(3), 'mm | WC: ±' + (wcTol/2).toFixed(4) + ' | RSS: ±' + (rssTol/2).toFixed(4));
}

ai_framework_thomas_run(draw);
`;

const EXAMPLE_FEDERKURVE = `// Federkennlinie & Arbeitspunkt — Werte unten anpassen

function draw() {
  const c    = ai_framework_thomas_input('c',   'Federrate c [N/mm]',    25,  {min:1,  max:500, step:1});
  const F0   = ai_framework_thomas_input('F0',  'Vorspannung F₀ [N]',   200,  {min:0,  max:2000,step:10});
  const fMax = ai_framework_thomas_input('fmax','Max. Weg [mm]',          20,  {min:2,  max:200, step:1});
  const fA   = ai_framework_thomas_input('fA',  'Arbeitspunkt [mm]',      12,  {min:0,  max:200, step:0.5});
  const FA   = F0 + c * Math.min(fA, fMax);

  const CW = canvas.width, CH = canvas.height;
  ctx.fillStyle = '#0a1e33'; ctx.fillRect(0, 0, CW, CH);

  const mL=70,mR=20,mT=40,mB=50;
  const W=CW-mL-mR, H=CH-mT-mB;
  const yMax = (F0 + c * fMax) * 1.1;
  const xP = x => mL + (x / fMax) * W;
  const yP = y => mT + H - (y / yMax) * H;

  // Gitternetz
  ctx.strokeStyle = '#003a74'; ctx.lineWidth = 1;
  const xStep = fMax <= 10 ? 2 : fMax <= 30 ? 5 : 10;
  const yStep = yMax < 500 ? 100 : yMax < 2000 ? 200 : 500;
  for (let gx = 0; gx <= fMax; gx += xStep) { ctx.beginPath(); ctx.moveTo(xP(gx),mT); ctx.lineTo(xP(gx),mT+H); ctx.stroke(); }
  for (let gy = 0; gy <= yMax; gy += yStep) { ctx.beginPath(); ctx.moveTo(mL,yP(gy)); ctx.lineTo(mL+W,yP(gy)); ctx.stroke(); }

  // Achsen
  ctx.strokeStyle='#a3c8eb'; ctx.lineWidth=2;
  ctx.beginPath(); ctx.moveTo(mL,mT); ctx.lineTo(mL,mT+H); ctx.lineTo(mL+W,mT+H); ctx.stroke();

  // Beschriftungen
  ctx.fillStyle='#a3c8eb'; ctx.font='11px Arial'; ctx.textAlign='center';
  for (let gx=0;gx<=fMax;gx+=xStep) ctx.fillText(gx, xP(gx), mT+H+16);
  ctx.textAlign='right';
  for (let gy=0;gy<=yMax;gy+=yStep) ctx.fillText(Math.round(gy), mL-5, yP(gy)+4);
  ctx.fillStyle='#6c6f76'; ctx.font='12px Arial'; ctx.textAlign='center';
  ctx.fillText('Federweg f [mm]', mL+W/2, CH-6);
  ctx.save(); ctx.translate(14, mT+H/2); ctx.rotate(-Math.PI/2);
  ctx.fillText('Federkraft F [N]', 0, 0); ctx.restore();

  // Kennlinie + Schraffur
  const pts=[];
  for(let x=0;x<=fMax;x+=fMax/200) pts.push({x,y:F0+c*x});
  ctx.strokeStyle='#3b76ba'; ctx.lineWidth=2.5; ctx.beginPath();
  pts.forEach((p,i)=>i===0?ctx.moveTo(xP(p.x),yP(p.y)):ctx.lineTo(xP(p.x),yP(p.y)));
  ctx.stroke();
  ctx.fillStyle='rgba(59,118,186,0.12)'; ctx.beginPath();
  ctx.moveTo(xP(0),yP(F0)); pts.forEach(p=>ctx.lineTo(xP(p.x),yP(p.y)));
  ctx.lineTo(xP(fMax),mT+H); ctx.lineTo(xP(0),mT+H); ctx.closePath(); ctx.fill();

  // Arbeitspunkt
  const fAc = Math.min(fA, fMax);
  ctx.strokeStyle='#f59e0b'; ctx.lineWidth=1.5; ctx.setLineDash([5,4]);
  ctx.beginPath(); ctx.moveTo(xP(fAc),mT+H); ctx.lineTo(xP(fAc),yP(FA)); ctx.lineTo(mL,yP(FA)); ctx.stroke();
  ctx.setLineDash([]); ctx.fillStyle='#f59e0b'; ctx.beginPath(); ctx.arc(xP(fAc),yP(FA),6,0,2*Math.PI); ctx.fill();

  // Legende
  ctx.fillStyle='#d4e8f8'; ctx.font='bold 13px Arial'; ctx.textAlign='left';
  ctx.fillText('c = '+c+' N/mm   F₀ = '+F0+' N', mL+10, mT+18);
  ctx.fillStyle='#f59e0b'; ctx.font='12px Arial';
  ctx.fillText('Arbeitspunkt: f = '+fAc.toFixed(1)+' mm  →  F = '+FA.toFixed(0)+' N', mL+10, mT+36);
  const W_feder = (FA*FA - F0*F0) / (2*c);
  ctx.fillStyle='#a3c8eb';
  ctx.fillText('Federarbeit: W = '+W_feder.toFixed(1)+' N·mm', mL+10, mT+52);
  console.log('FA =', FA.toFixed(1), 'N | Federarbeit =', W_feder.toFixed(1), 'N·mm');
}

ai_framework_thomas_run(draw);
`;

const EXAMPLE_LEER = `// Leere Vorlage – KI-Assistent oder eigener Code
// Struktur: draw()-Funktion + ai_framework_thomas_run(draw) am Ende

function draw() {
  const W = canvas.width, H = canvas.height;
  // Hintergrund
  ctx.fillStyle = '#0a1e33';
  ctx.fillRect(0, 0, W, H);

  // Beispiel: ein interaktiver Wert
  const wert = ai_framework_thomas_input('wert', 'Beispielwert', 50, {min: 0, max: 100, step: 1});

  // Balken für den Wert
  const bW = (wert / 100) * (W - 80);
  ctx.fillStyle = '#3b76ba';
  ctx.fillRect(40, H/2 - 20, bW, 40);

  // Beschriftung
  ctx.fillStyle = '#d4e8f8'; ctx.font = 'bold 16px Arial'; ctx.textAlign = 'center';
  ctx.fillText('Wert: ' + wert + '%', W/2, H/2 - 40);
  ctx.fillStyle = '#6c6f76'; ctx.font = '13px Arial';
  ctx.fillText('Passen Sie den Wert unten an oder beschreiben Sie dem KI-Assistenten', W/2, H - 60);
  ctx.fillText('was das Programm zeigen soll (z.B. Toleranzanalyse, Diagramm, Berechnung)', W/2, H - 40);
}

ai_framework_thomas_run(draw);
`;

const EXAMPLE_PYTHON = `# Python-Beispiel — wird serverseitig ausgeführt
# Verfügbar u.a.: numpy (np), sympy (sp), pandas (pd), matplotlib.pyplot (plt)
# Ausgaben mit print(...); Diagramme einfach mit plt erzeugen (kein plt.show() nötig).

import numpy as np
import matplotlib.pyplot as plt

# 1) Rechnen & ausgeben
werte = np.array([1, 2, 3, 4, 5, 6])
print("Summe:", werte.sum(), "| Mittelwert:", werte.mean(), "| Std:", round(float(werte.std()), 3))

# 2) Symbolisch (SymPy)
import sympy as sp
x = sp.symbols('x')
f = x**2 * sp.sin(x)
print("Ableitung von x^2*sin(x):", sp.diff(f, x))

# 3) Diagramm (matplotlib) — erscheint automatisch in der Vorschau
t = np.linspace(0, 2 * np.pi, 400)
plt.figure(figsize=(6, 3.2))
plt.plot(t, np.sin(t), label='sin')
plt.plot(t, np.cos(t), label='cos')
plt.title('Sinus & Kosinus')
plt.xlabel('t'); plt.ylabel('f(t)')
plt.legend(); plt.grid(True, alpha=0.3)
`;
