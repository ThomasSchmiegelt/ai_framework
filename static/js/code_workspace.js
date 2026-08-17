/* AI_Framework_Thomas — Code-Workspace (aider-inspiriert, minimalistisch)
   Ein einziger, chat-getriebener Arbeitsbereich statt getrennter Tabs IDE/Projekt/JSON.
   Ein Workspace = Liste von Dateien [{path, content}] + aktive Datei. Eine Datei
   verhält sich wie die frühere IDE, mehrere wie eine Projektstruktur.

   • Prompt oben → KI erzeugt/ändert Code (POST /api/code/assist, aktive Datei) oder eine
     ganze Mehrdatei-Struktur (POST /api/code/project).
   • ▶ Ausführen bezieht sich auf die AKTIVE Datei: .js/.html → Canvas-iframe,
     .py → Server-Python, .json → Prüfen/Formatieren statt Ausführen.
   • Persistenz: Workspace bleibt im localStorage; einzelne Dateien dauerhaft speichern
     via /api/code; Mehrdatei-Export als ZIP (/api/code/project-zip). Kein Backend-Umbau. */

const CodeWorkspace = (() => {

  /* ── Zustand ─────────────────────────────────────────────────── */
  let _files   = [];     // [{path, content, savedId?}]
  let _active  = -1;     // Index in _files
  let _dirty   = false;
  let _generating = false;
  let _lastErrors = [];
  let _cm      = null;   // CodeMirror-Instanz (auf der aktiven Datei)
  let _lang    = 'js';   // aus der Endung der aktiven Datei abgeleitet (überschreibbar)
  let _pendingPrompt = '';
  const WS_KEY = 'code_workspace_v1';

  const _esc = s => String(s == null ? '' : s).replace(/[&<>]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;' }[c]));
  const $ = id => document.getElementById(id);
  const _model = () => (typeof Profile !== 'undefined' && Profile.modelFor) ? Profile.modelFor('coding') : undefined;

  /* ── iframe-Framework (Canvas-Vorschau, aus der früheren IDE übernommen) ── */
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

function ai_framework_thomas_run(fn) { _drawFn = fn; _resize(); fn(); }
window.addEventListener('resize', function() { if (_drawFn) { _resize(); _drawFn(); } });

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
if (!_drawFn) { _resize(); }
<\/script>
</body>
</html>`;

  // Konsole-/Fehler-Weiterleitung an das Eltern-Fenster (für vollständige HTML-Dokumente,
  // die der Agent erzeugt und die NICHT ins Canvas-Framework eingebettet werden).
  const _CONSOLE_HOOK = `<script>(function(){var fwd=function(level,args){window.parent.postMessage({type:'console',level:level,text:args.map(function(a){return typeof a==='object'?JSON.stringify(a):String(a);}).join(' ')},'*');};console.log=function(){fwd('log',[].slice.call(arguments));};console.warn=function(){fwd('warn',[].slice.call(arguments));};console.error=function(){fwd('error',[].slice.call(arguments));};window.onerror=function(msg,_s,line){window.parent.postMessage({type:'console',level:'error',text:'Zeile '+line+': '+msg},'*');return false;};})();<\/script>`;

  /* ── Persistenz (localStorage) ───────────────────────────────── */
  function _wsSave() {
    if (_active >= 0 && _cm) _files[_active].content = _cm.getValue();
    try { localStorage.setItem(WS_KEY, JSON.stringify({ files: _files, active: _active })); } catch (_) {}
  }
  function _wsLoad() {
    try {
      const s = JSON.parse(localStorage.getItem(WS_KEY) || 'null');
      if (s && Array.isArray(s.files)) { _files = s.files; _active = (s.active >= 0 && s.active < s.files.length) ? s.active : (s.files.length ? 0 : -1); return true; }
    } catch (_) {}
    return false;
  }

  function _setDirty(on) { _dirty = on !== false; }

  /* ── Sprache aus Endung ──────────────────────────────────────── */
  function _langForPath(path) {
    if (/\.py$/i.test(path)) return 'py';
    if (/\.json$/i.test(path)) return 'json';
    if (/\.(js|mjs|html?)$/i.test(path)) return 'js';
    return null;
  }
  // Heuristik für Code ohne Endung (Chat-Übernahme)
  function _detectLang(code) {
    if (!code) return null;
    if (/ai_framework_thomas_run\s*\(|getContext\s*\(|canvas\.(width|height)|\bctx\b/.test(code)) return 'js';
    if (/^\s*(import\s+\w|from\s+\w|def\s+\w|print\s*\()/m.test(code) || /\bplt\.|\bnp\.|\bsp\.|\bpd\./.test(code)) return 'py';
    if (/^\s*[\{\[]/.test(code.trim())) return 'json';
    return null;
  }

  /* ── Dateibaum ───────────────────────────────────────────────── */
  function _buildTree(files) {
    const root = { dirs: {}, files: [] };
    files.forEach((f, idx) => {
      const parts = (f.path || ('datei_' + (idx + 1))).split('/').filter(Boolean);
      let node = root;
      for (let i = 0; i < parts.length - 1; i++) {
        node.dirs[parts[i]] = node.dirs[parts[i]] || { dirs: {}, files: [] };
        node = node.dirs[parts[i]];
      }
      node.files.push({ name: parts[parts.length - 1] || ('datei_' + (idx + 1)), idx });
    });
    return root;
  }
  function _renderNode(node, depth) {
    let html = '';
    const pad = d => `padding-left:${6 + d * 13}px`;
    Object.keys(node.dirs).sort().forEach(name => {
      html += `<div class="cw-row cw-dir" style="${pad(depth)}">📁 ${_esc(name)}</div>`;
      html += _renderNode(node.dirs[name], depth + 1);
    });
    node.files.sort((a, b) => a.name.localeCompare(b.name)).forEach(f => {
      const sel = f.idx === _active ? ' sel' : '';
      html += `<div class="cw-row cw-file-row${sel}" data-idx="${f.idx}" style="${pad(depth)}" title="${_esc(_files[f.idx]?.path || '')}">📄 ${_esc(f.name)}</div>`;
    });
    return html;
  }
  function _renderTree() {
    const host = $('cw-tree');
    if (!host) return;
    if (!_files.length) {
      host.innerHTML = '<div class="planner-muted" style="padding:10px;font-size:12px">Noch keine Datei. Prompt oben ausfüllen oder „+ Neu“.</div>';
      return;
    }
    host.innerHTML = _renderNode(_buildTree(_files), 0);
    host.querySelectorAll('.cw-file-row').forEach(row =>
      row.addEventListener('click', () => _setActive(parseInt(row.dataset.idx, 10))));
  }

  /* ── Aktive Datei wechseln/setzen ────────────────────────────── */
  function _applyLangUI() {
    const sel = $('cw-lang');
    if (sel) sel.value = _lang;
    if (_cm) _cm.setOption('mode', _lang === 'py' ? 'python' : _lang === 'json' ? 'application/json' : 'javascript');
    // JSON-Aktionen ein-/ausblenden, Ausführen-Button ausblenden bei JSON
    const jsonActs = $('cw-json-actions'); if (jsonActs) jsonActs.style.display = _lang === 'json' ? '' : 'none';
    const runBtn = $('cw-run'); if (runBtn) runBtn.style.display = _lang === 'json' ? 'none' : '';
    _showPyOutput(_lang === 'py');
  }

  function _setActive(idx, opts) {
    if (_active >= 0 && _cm && (!opts || !opts.noStash)) _files[_active].content = _cm.getValue();
    if (idx < 0 || idx >= _files.length) { _active = -1; if (_cm) _cm.setValue(''); _renderTree(); return; }
    _active = idx;
    const f = _files[idx];
    _lang = _langForPath(f.path) || _detectLang(f.content) || 'js';
    if ($('cw-name')) $('cw-name').value = f.path;
    if (_cm) _cm.setValue(f.content || '');
    _applyLangUI();
    _renderTree();
    if (_cm) setTimeout(() => _cm.refresh(), 0);
  }

  function _uniquePath(base) {
    let p = base, n = 2;
    while (_files.some(f => f.path === p)) {
      p = base.includes('.') ? base.replace(/(\.[^.]+)$/, `_${n}$1`) : `${base}_${n}`;
      n++;
    }
    return p;
  }

  function _addFile(path, content, activate) {
    const p = _uniquePath(path || 'neu.js');
    _files.push({ path: p, content: content || '' });
    if (activate !== false) _setActive(_files.length - 1, { noStash: false });
    else _renderTree();
    _wsSave();
    return _files.length - 1;
  }

  /* ── Ausführen (aktive Datei) ────────────────────────────────── */
  const _preview = () => $('cw-preview');
  const _console = () => $('cw-console');

  function _showPyOutput(showPy) {
    const frame = _preview(), out = $('cw-py-output');
    if (frame) frame.style.display = showPy ? 'none' : '';
    if (out)   out.style.display   = showPy ? '' : 'none';
  }

  function _run() {
    if (_active < 0) { showToast('Keine Datei gewählt'); return; }
    _files[_active].content = _cm ? _cm.getValue() : _files[_active].content;
    if (_lang === 'py') return _runPython();
    if (_lang === 'json') { _validateJson(true); return; }
    _renderPreview();
    if (typeof Logger !== 'undefined') Logger.log('cw_run', { path: _files[_active].path, len: (_files[_active].content || '').length });
  }

  // Rendert die aktive Datei im Canvas-iframe. Ein vollständiges HTML-Dokument (vom Agenten)
  // wird direkt gerendert (nur Konsole-Hook eingeschleust); ein reines JS-/Canvas-Snippet
  // läuft im bestehenden Framework (mit ai_framework_thomas_run/ctx-Helfern).
  function _renderPreview() {
    const frame = _preview(), cons = _console();
    if (!frame || _active < 0) return;
    const f = _files[_active];
    const content = (f.content || '');
    _showPyOutput(false);
    if (cons) cons.innerHTML = '';
    _lastErrors = [];
    if ($('cw-repair')) $('cw-repair').style.display = 'none';
    const isFullHtml = /\.html?$/i.test(f.path) && /<html[\s>]|<!doctype/i.test(content);
    if (isFullHtml) {
      let doc = content;
      if (/<head[\s>]/i.test(doc))      doc = doc.replace(/<head([^>]*)>/i, '<head$1>' + _CONSOLE_HOOK);
      else if (/<html[\s>]/i.test(doc)) doc = doc.replace(/<html([^>]*)>/i, '<html$1>' + _CONSOLE_HOOK);
      else                              doc = _CONSOLE_HOOK + doc;
      frame.srcdoc = doc;
      return;
    }
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
    frame.srcdoc = fw + content.trim() + _IFRAME_CLOSE;
  }

  async function _runPython() {
    const code = (_files[_active]?.content || '').trim();
    const out = $('cw-py-output'), cons = _console();
    if (cons) cons.innerHTML = '';
    _lastErrors = [];
    if ($('cw-repair')) $('cw-repair').style.display = 'none';
    _showPyOutput(true);
    if (!out) return;
    if (!code) { out.innerHTML = ''; return; }
    out.innerHTML = '<div class="ide-py-run"><span class="spinner"></span> Python läuft…</div>';
    try {
      const resp = await fetch('/api/code/run-python', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ code }),
      });
      let data; try { data = await resp.json(); } catch { data = {}; }
      if (!resp.ok) throw new Error(data.detail || ('HTTP ' + resp.status));
      _renderPyResult(data);
    } catch (e) {
      out.innerHTML = `<pre class="ide-py-err">Fehler: ${_esc(e.message)}</pre>`;
    }
  }

  function _renderPyResult(data) {
    const out = $('cw-py-output');
    if (!out) return;
    out.innerHTML = '';
    for (const src of (data.images || [])) {
      const img = document.createElement('img');
      img.className = 'ide-py-img'; img.src = src; out.appendChild(img);
    }
    const txt = (data.output || '').replace(/\s+$/, '');
    if (txt) { const pre = document.createElement('pre'); pre.className = 'ide-py-out'; pre.textContent = txt; out.appendChild(pre); }
    if (data.error) {
      const pre = document.createElement('pre'); pre.className = 'ide-py-err'; pre.textContent = data.error; out.appendChild(pre);
      _lastErrors = [data.error];
      if ($('cw-repair')) $('cw-repair').style.display = '';
    }
    if (!txt && !data.error && !(data.images || []).length) out.innerHTML = '<div class="ide-py-run">✓ Ausgeführt (keine Ausgabe)</div>';
  }

  /* ── JSON prüfen/formatieren (aus dem früheren JSON-Editor) ───── */
  function _validateJson(toast) {
    const text = _cm ? _cm.getValue() : (_files[_active]?.content || '');
    const out = $('cw-py-output');
    _showPyOutput(true);
    if (!text.trim()) { if (out) out.innerHTML = '<div class="ide-py-run">Leer</div>'; return true; }
    try {
      JSON.parse(text);
      if (out) out.innerHTML = '<div class="ide-py-run">✓ Gültiges JSON</div>';
      if (toast) showToast('✓ Gültiges JSON');
      return true;
    } catch (e) {
      let where = '';
      const m = /position (\d+)/i.exec(e.message);
      if (m) { const pos = +m[1]; const before = text.slice(0, pos); where = ` (Zeile ${before.split('\n').length}, Spalte ${pos - before.lastIndexOf('\n')})`; }
      if (out) out.innerHTML = `<pre class="ide-py-err">✗ ${_esc(e.message + where)}</pre>`;
      return false;
    }
  }
  function _formatJson() {
    if (!_validateJson(false)) { showToast('Erst JSON-Fehler beheben'); return; }
    try {
      const v = JSON.stringify(JSON.parse(_cm.getValue()), null, 2);
      _cm.setValue(v); _files[_active].content = v; _wsSave();
      showToast('✓ Formatiert');
    } catch (_) {}
  }

  /* ── Konsole aus iframe ──────────────────────────────────────── */
  function _onConsoleMsg(e) {
    if (!e.data || e.data.type !== 'console') return;
    const cons = _console();
    if (!cons) return;
    const line = document.createElement('div');
    line.className = `ide-con-line ${e.data.level}`;
    const ts = new Date().toLocaleTimeString('de-DE', { hour: '2-digit', minute: '2-digit', second: '2-digit' });
    line.textContent = `[${ts}] ${e.data.text}`;
    cons.appendChild(line); cons.scrollTop = cons.scrollHeight;
    if (e.data.level === 'error') {
      _lastErrors.push(e.data.text);
      if ($('cw-repair')) $('cw-repair').style.display = '';
    }
  }

  /* ── KI: Code erzeugen/ändern (aktive Datei) ─────────────────── */
  function _chatMsg(role, text) {
    const h = $('cw-chat'); if (!h) return null;
    const div = document.createElement('div');
    div.className = `ide-chat-msg ${role}`; div.textContent = text;
    h.appendChild(div); h.scrollTop = h.scrollHeight;
    return div;
  }
  function _setGenerating(on) {
    _generating = on;
    const b = $('cw-send');
    if (b) { b.disabled = on; b.textContent = on ? '⏳ …' : '▶ Senden'; }
    const p = $('cw-project'); if (p) p.disabled = on;
  }

  async function _send() {
    if (_generating) return;
    const inp = $('cw-prompt');
    const msg = (inp?.value || '').trim();
    if (!msg) { inp?.focus(); return; }
    inp.value = '';
    _pendingPrompt = msg;
    await _assist(msg, '', false);
  }

  async function _assist(prompt, answers, forceCode) {
    _setGenerating(true);
    const clarify  = $('cw-opt-clarify')?.checked !== false;
    const adaptive = !!$('cw-opt-adaptive')?.checked;
    const agent_id = $('cw-agent')?.value || '';
    if (answers) _chatMsg('user', '↳ ' + answers);
    else _chatMsg('user', prompt.substring(0, 90) + (prompt.length > 90 ? '…' : ''));
    const wrap = _chatMsg('assistant', '⏳ …');
    const curCode = (_active >= 0 && _cm) ? _cm.getValue() : '';
    try {
      const resp = await fetch('/api/code/assist', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          prompt, answers, agent_id, adaptive,
          force_code: forceCode || !clarify,
          language: _lang === 'py' ? 'Python' : _lang === 'json' ? 'JSON' : 'JavaScript',
          current_code: curCode, model: _model(),
        }),
      });
      const d = await resp.json();
      if (!resp.ok) throw new Error(d.detail || ('HTTP ' + resp.status));
      if (d.tokens && typeof TokenMeter !== 'undefined') TokenMeter.add(d.tokens, 'Code-Assistent');
      if (d.type === 'questions') {
        _renderClarify(wrap, d.questions, d.adaptive_role);
      } else if (d.code) {
        const lang = /^(py|python)$/i.test(d.language) ? 'py' : (/json/i.test(d.language) ? 'json' : _detectLang(d.code));
        if (_active < 0) _addFile(lang === 'py' ? 'programm.py' : lang === 'json' ? 'daten.json' : 'programm.js', d.code);
        else { _files[_active].content = d.code; if (_cm) _cm.setValue(d.code); }
        if (lang && lang !== _lang) { _lang = lang; _applyLangUI(); }
        _setDirty(true); _wsSave();
        if (_cm) setTimeout(() => _cm.refresh(), 0);
        _run();
        if (wrap) wrap.textContent = (d.adaptive_role ? `🧠 ${d.adaptive_role}\n` : '') + (d.note || '✓ Code erstellt') + '\n→ in die aktive Datei übernommen und ausgeführt.';
      } else if (wrap) {
        wrap.textContent = d.note || '⚠ Kein Code erhalten.';
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
      '<textarea class="cw-clarify-answers" placeholder="Antworten (je Zeile)…" style="width:100%;min-height:50px;font-size:12px;background:var(--bg-input);color:var(--text);border:1px solid var(--border);border-radius:4px;padding:5px"></textarea>' +
      '<div style="display:flex;gap:6px"><button class="export-btn cw-clarify-go" style="font-size:11.5px">↑ Antworten & Code</button>' +
      '<button class="export-btn cw-clarify-skip" style="font-size:11.5px">⏭ Trotzdem coden</button></div>';
    $('cw-chat').appendChild(box);
    $('cw-chat').scrollTop = 99999;
    const ta = box.querySelector('.cw-clarify-answers');
    box.querySelector('.cw-clarify-go').addEventListener('click', () => { const a = (ta.value || '').trim(); box.remove(); _assist(_pendingPrompt, a || '(keine weiteren Angaben)', true); });
    box.querySelector('.cw-clarify-skip').addEventListener('click', () => { box.remove(); _assist(_pendingPrompt, '', true); });
    ta.focus();
  }

  function _autoRepair() {
    if (_generating || !_lastErrors.length) return;
    const err = _lastErrors.join('\n');
    _lastErrors = [];
    if ($('cw-repair')) $('cw-repair').style.display = 'none';
    _assist(`Behebe den Fehler in der aktuellen Datei. Gib die vollständige korrigierte Datei zurück.\n\nFehlermeldung:\n${err}`, '', true);
  }

  /* ── Autonomer Coding-Agent (Agent-Harness) ──────────────────── */
  let _agentAbort = null;
  let _agentSnapshot = null;   // Workspace-Stand vor dem Agentenlauf (für Undo)
  let _agentRepairRounds = 0;
  const _AGENT_ICON = { list_files: '📁', read_file: '📖', write_file: '✏️', run_python: '▶️', delete_file: '🗑' };

  function _agentLog(kind, text) {
    const host = $('cw-agent-log'); if (!host) return;
    const row = document.createElement('div');
    row.className = 'cw-agent-step cw-agent-' + kind;
    row.textContent = text;
    host.appendChild(row); host.scrollTop = host.scrollHeight;
  }

  function _toggleAgentPanel() {
    const p = $('cw-agent-panel'); if (!p) return;
    const show = p.style.display === 'none' || !p.style.display;
    p.style.display = show ? '' : 'none';
    $('cw-agent-toggle')?.classList.toggle('active', show);
    if (show) $('cw-agent-task')?.focus();
  }

  function _markChanged(changed) {
    const set = new Set(changed || []);
    document.querySelectorAll('#cw-tree .cw-file-row').forEach(row => {
      const i = +row.dataset.idx;
      const p = _files[i] && _files[i].path;
      row.classList.toggle('cw-file-changed', set.has(p));
    });
  }

  function _applyAgentFiles(newFiles, changed) {
    const savedBy = {};
    _files.forEach(f => { if (f.savedId) savedBy[f.path] = f.savedId; });
    _files = (newFiles || []).map(nf => {
      const o = { path: nf.path, content: nf.content };
      if (savedBy[nf.path]) o.savedId = savedBy[nf.path];
      return o;
    });
    if (!_files.length) _active = -1;
    else if (_active < 0 || _active >= _files.length) _active = 0;
    _wsSave();
    _renderTree();
    if (_active >= 0) _setActive(_active, { noStash: true });
    _markChanged(changed);
  }

  // Bestimmt die anzuzeigende Einstiegsdatei und rendert sie; bei HTML/JS anschließend
  // Konsolenfehler prüfen und ggf. eine Auto-Reparatur-Runde starten (max. 2).
  function _agentPreviewAndRepair(changed) {
    if (!_files.length) return;
    const find = pred => _files.findIndex(pred);
    let idx = find(f => /(^|\/)index\.html?$/i.test(f.path));
    if (idx < 0) idx = find(f => /\.html?$/i.test(f.path));
    if (idx < 0) idx = find(f => /\.(js|mjs)$/i.test(f.path));
    if (idx < 0) idx = find(f => /\.py$/i.test(f.path));
    if (idx < 0) idx = _active >= 0 ? _active : 0;
    _setActive(idx);
    _run();
    if (_lang === 'js') {
      setTimeout(() => {
        if (_generating) return;                       // läuft bereits wieder
        if (_lastErrors.length && _agentRepairRounds < 2) {
          _agentRepairRounds++;
          const err = _lastErrors.join('\n').slice(0, 1500);
          _agentLog('repair', `🔧 ${_agentRepairRounds}. Auto-Reparatur — Konsolenfehler erkannt`);
          _runAgent('Behebe diese Laufzeit-/Konsolenfehler im Canvas-Programm und gib die '
                  + 'vollständigen korrigierten Dateien zurück.\n\nFehler:\n' + err, true);
        }
      }, 1300);
    }
  }

  function _stopAgent() { if (_agentAbort) _agentAbort.abort(); }

  function _agentUndo() {
    if (!_agentSnapshot) return;
    _files = JSON.parse(JSON.stringify(_agentSnapshot));
    _active = _files.length ? 0 : -1;
    _wsSave(); _renderTree();
    if (_active >= 0) _setActive(0, { noStash: true });
    _agentSnapshot = null;
    $('cw-agent-undo').style.display = 'none';
    _agentLog('final', '↩ Änderungen des Agenten rückgängig gemacht.');
  }

  async function _runAgent(task, isRepair) {
    if (_generating) return;
    task = (task || (isRepair ? '' : $('cw-agent-task')?.value) || '').trim();
    if (!task) { $('cw-agent-task')?.focus(); return; }
    if (!isRepair) {
      _agentSnapshot = JSON.parse(JSON.stringify(_files));
      _agentRepairRounds = 0;
      const log = $('cw-agent-log'); if (log) log.innerHTML = '';
      $('cw-agent-undo').style.display = 'none';
    }
    _setGenerating(true);
    $('cw-agent-run').style.display = 'none';
    $('cw-agent-stop').style.display = '';
    _agentLog('task', (isRepair ? '🔧 Reparatur' : '🤖 Aufgabe') + ': ' + task.slice(0, 140));
    _agentAbort = new AbortController();
    let changed = [];
    try {
      const resp = await fetch('/api/code/agent', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        signal: _agentAbort.signal,
        body: JSON.stringify({ task, files: _files, model: _model() }),
      });
      if (!resp.ok || !resp.body) throw new Error('HTTP ' + resp.status);
      const reader = resp.body.getReader(); const dec = new TextDecoder(); let buf = '';
      while (true) {
        const { done, value } = await reader.read(); if (done) break;
        buf += dec.decode(value, { stream: true });
        const lines = buf.split('\n'); buf = lines.pop();
        for (const line of lines) {
          if (!line.startsWith('data: ')) continue;
          let ev; try { ev = JSON.parse(line.slice(6)); } catch (_) { continue; }
          if (ev.type === 'step') {
            const ic = _AGENT_ICON[ev.tool] || '•';
            _agentLog('step', `${ic} ${ev.tool}${ev.arg ? ' ' + ev.arg : ''}${ev.result ? ' — ' + ev.result : ''}`);
          } else if (ev.type === 'text') {
            _agentLog('final', '✅ ' + ev.content);
          } else if (ev.type === 'files') {
            _applyAgentFiles(ev.files, ev.changed || []);
            changed = ev.changed || [];
          } else if (ev.type === 'done') {
            if (ev.tokens && typeof TokenMeter !== 'undefined') TokenMeter.add(ev.tokens, 'Code-Agent');
          } else if (ev.type === 'error') {
            _agentLog('error', '⚠ ' + (ev.message || ev.detail || 'Fehler'));
          }
        }
      }
    } catch (e) {
      if (e.name !== 'AbortError') _agentLog('error', '⚠ ' + (e.message || e));
    } finally {
      _setGenerating(false);
      $('cw-agent-run').style.display = '';
      $('cw-agent-stop').style.display = 'none';
      if (_agentSnapshot) $('cw-agent-undo').style.display = '';
      const aborted = _agentAbort && _agentAbort.signal.aborted;
      _agentAbort = null;
      if (!aborted) _agentPreviewAndRepair(changed);
    }
  }

  /* ── KI: ganze Projektstruktur erzeugen ──────────────────────── */
  async function _project() {
    if (_generating) return;
    const prompt = ($('cw-prompt')?.value || '').trim();
    if (!prompt) { $('cw-prompt')?.focus(); showToast('Aufgabe oben beschreiben'); return; }
    if (_files.length && !confirm('Aktuellen Workspace durch die erzeugte Projektstruktur ersetzen?')) return;
    _setGenerating(true);
    const wrap = _chatMsg('assistant', '⏳ Projektstruktur…');
    try {
      const resp = await fetch('/api/code/project', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ prompt, language: '', agent_id: $('cw-agent')?.value || '', max_files: 12, model: _model() }),
      });
      const d = await resp.json();
      if (!resp.ok) throw new Error(d.detail || ('HTTP ' + resp.status));
      if (d.tokens && typeof TokenMeter !== 'undefined') TokenMeter.add(d.tokens, 'Code-Projekt');
      _files = Array.isArray(d.files) ? d.files : [];
      _active = -1;
      _renderTree();
      if (_files.length) _setActive(0);
      _wsSave();
      if (wrap) wrap.textContent = _files.length ? `✓ ${_files.length} Datei(en)${d.note ? ' · ' + d.note : ''}` : '⚠ Keine Dateien erzeugt.';
    } catch (e) {
      if (wrap) wrap.textContent = '❌ ' + e.message;
    } finally {
      _setGenerating(false);
    }
  }

  /* ── Speichern / Laden (einzelne Datei, Back-compat /api/code) ─ */
  async function _save() {
    if (_active < 0) { showToast('Keine Datei zum Speichern'); return; }
    _files[_active].content = _cm ? _cm.getValue() : _files[_active].content;
    const f = _files[_active];
    const name = f.path || 'Unbenannt';
    try {
      const resp = await fetch('/api/code', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ id: f.savedId || null, name, code: f.content }),
      });
      if (!resp.ok) throw new Error('HTTP ' + resp.status);
      const data = await resp.json();
      f.savedId = data.id;
      _setDirty(false); _wsSave();
      showToast(`✓ Gespeichert: ${name}`);
      await _loadSavedList();
    } catch (e) { showToast('Speichern fehlgeschlagen: ' + e.message); }
  }

  async function _loadSavedList() {
    const sel = $('cw-load');
    if (!sel) return;
    try {
      const programs = await (await fetch('/api/code')).json();
      sel.innerHTML = `<option value="">📂 Laden… (${programs.length})</option>` +
        programs.map(p => `<option value="${_esc(p.id)}">${_esc(p.name)}</option>`).join('');
    } catch (_) {}
  }
  async function _loadSaved(id) {
    if (!id) return;
    try {
      const data = await (await fetch(`/api/code/${id}`)).json();
      const idx = _addFile(data.name || 'programm', data.code || '');
      _files[idx].savedId = data.id;
      _wsSave();
    } catch (e) { showToast('Laden fehlgeschlagen: ' + e.message); }
  }
  async function _deleteActive() {
    if (_active < 0) return;
    const f = _files[_active];
    if (!confirm(`Datei „${f.path}“ aus dem Workspace entfernen?${f.savedId ? ' (auch gespeicherte Version löschen)' : ''}`)) return;
    if (f.savedId) { try { await fetch(`/api/code/${f.savedId}`, { method: 'DELETE' }); await _loadSavedList(); } catch (_) {} }
    _files.splice(_active, 1);
    _active = -1;
    if (_files.length) _setActive(0); else { if (_cm) _cm.setValue(''); if ($('cw-name')) $('cw-name').value = ''; _renderTree(); }
    _wsSave();
  }

  /* ── ZIP-Export (Mehrdatei) ──────────────────────────────────── */
  async function _zip() {
    if (_active >= 0 && _cm) _files[_active].content = _cm.getValue();
    if (!_files.length) { showToast('Kein Projekt zum Exportieren'); return; }
    const zipname = (_files[0]?.path?.split('/')[0] || 'projekt').replace(/\.[^.]+$/, '') || 'projekt';
    try {
      const resp = await fetch('/api/code/project-zip', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ files: _files, zipname }),
      });
      if (!resp.ok) throw new Error('HTTP ' + resp.status);
      const blob = await resp.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url; a.download = zipname.replace(/[^\w\-]+/g, '_') + '.zip';
      document.body.appendChild(a); a.click(); a.remove();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (e) { showToast('ZIP fehlgeschlagen: ' + e.message); }
  }

  /* ── Chat-Übernahme (Kompatibilität mit chat.js „▶ Code-Tab") ── */
  function loadFromChat(code, name, lang) {
    if (!code) return;
    if (typeof switchTab === 'function') switchTab('ide');
    const l = (/^(py|python)$/i.test(lang) ? 'py' : /^(js|javascript|html)$/i.test(lang) ? 'js' : /json/i.test(lang) ? 'json' : _detectLang(code)) || 'js';
    const ext = l === 'py' ? '.py' : l === 'json' ? '.json' : '.js';
    const base = (name || 'chat-code').replace(/\.[^.]+$/, '') + ext;
    _addFile(base, code);
    _run();
    if (typeof showToast === 'function') showToast('✓ Code in den Code-Tab übernommen');
  }

  /* ── Beispiele (ins ⋯-Menü verschoben) ───────────────────────── */
  function _loadExample(key) {
    const tbl = {
      toleranz: ['toleranzanalyse.js', window.EXAMPLE_TOLERANZ],
      kurve:    ['federkennlinie.js',  window.EXAMPLE_FEDERKURVE],
      python:   ['python_beispiel.py', window.EXAMPLE_PYTHON],
      leer:     ['neu.js',             window.EXAMPLE_LEER],
    };
    const [name, code] = tbl[key] || tbl.leer;
    _addFile(name, code || '');
    $('cw-more')?.removeAttribute('open');
  }

  /* ── Splitter (Sidebar ↔ Haupt, Editor ↔ Ausgabe) ────────────── */
  // Während eines Drags fängt sonst das Vorschau-iframe (und die Python-Ausgabe) die
  // Mausevents ab → der Drag „reißt ab". Deshalb blenden wir deren pointer-events aus,
  // solange gezogen wird, und schalten sie danach wieder ein.
  function _dragCapture(on) {
    const frame = _preview(), out = $('cw-py-output');
    if (frame) frame.style.pointerEvents = on ? 'none' : '';
    if (out)   out.style.pointerEvents   = on ? 'none' : '';
    document.body.style.userSelect = on ? 'none' : '';
    document.body.style.cursor = on ? (on === 'row' ? 'row-resize' : 'col-resize') : '';
  }

  function _initSplitters() {
    const hs = $('cw-splitter'), body = $('cw-body'), side = $('cw-sidebar');
    if (hs && body && side) {
      let drag = false;
      const mv = e => { if (!drag) return; const r = body.getBoundingClientRect(); let w = e.clientX - r.left; w = Math.max(120, Math.min(r.width - 260, w)); side.style.flexBasis = w + 'px'; };
      hs.addEventListener('mousedown', e => { drag = true; e.preventDefault(); _dragCapture('col'); });
      window.addEventListener('mousemove', mv);
      window.addEventListener('mouseup', () => { if (!drag) return; drag = false; _dragCapture(false); if (_cm) _cm.refresh(); });
    }
    const vs = $('cw-splitter-v'), main = $('cw-main'), out = $('cw-output');
    if (vs && main && out) {
      let drag = false;
      const mv = e => { if (!drag) return; const r = main.getBoundingClientRect(); let h = r.bottom - e.clientY; h = Math.max(80, Math.min(r.height - 120, h)); out.style.flexBasis = h + 'px'; };
      vs.addEventListener('mousedown', e => { drag = true; e.preventDefault(); _dragCapture('row'); });
      window.addEventListener('mousemove', mv);
      window.addEventListener('mouseup', () => { if (!drag) return; drag = false; _dragCapture(false); });
    }
  }

  /* ── Coding-Agenten laden ────────────────────────────────────── */
  async function _loadAgents() {
    const sel = $('cw-agent');
    if (!sel) return;
    try {
      let agents = await (await fetch('/api/agents')).json();
      if (!Array.isArray(agents)) agents = [];
      const isCode = a => /program|cod|entwickl|software/i.test((a.category || '') + ' ' + (a.name || '')) || a.example_code;
      agents.sort((a, b) => (isCode(b) ? 1 : 0) - (isCode(a) ? 1 : 0));
      sel.innerHTML = '<option value="">— kein Agent —</option>' +
        agents.map(a => `<option value="${_esc(a.id)}">${(a.icon || '🤖')} ${_esc(a.name || a.id)}${a.example_code ? ' · 📎' : ''}</option>`).join('');
    } catch (_) {}
  }

  /* ── Init ────────────────────────────────────────────────────── */
  function init() {
    if (!$('cw-editor')) return;   // Workspace-Markup nicht vorhanden
    _wsLoad();

    // CodeMirror auf der zentralen Textarea
    const ed = $('cw-editor');
    if (ed && window.CodeMirror) {
      _cm = CodeMirror.fromTextArea(ed, {
        mode: 'javascript', lineNumbers: true, indentUnit: 2, tabSize: 2, indentWithTabs: false,
        autoCloseBrackets: true, matchBrackets: true, styleActiveLine: true,
        extraKeys: { 'Ctrl-Enter': _run, 'Cmd-Enter': _run, 'Ctrl-S': () => _save(), 'Cmd-S': () => _save(), 'Ctrl-Space': 'autocomplete' },
      });
      _cm.on('change', () => { if (_active >= 0) { _files[_active].content = _cm.getValue(); _setDirty(true); } });
    }

    // Prompt & KI
    $('cw-send')?.addEventListener('click', _send);
    $('cw-project')?.addEventListener('click', _project);
    $('cw-prompt')?.addEventListener('keydown', e => { if (e.key === 'Enter' && !e.shiftKey && (e.ctrlKey || e.metaKey)) { e.preventDefault(); _send(); } });
    $('cw-repair')?.addEventListener('click', _autoRepair);

    // Autonomer Coding-Agent
    $('cw-agent-toggle')?.addEventListener('click', _toggleAgentPanel);
    $('cw-agent-run')?.addEventListener('click', () => _runAgent(null, false));
    $('cw-agent-stop')?.addEventListener('click', _stopAgent);
    $('cw-agent-undo')?.addEventListener('click', _agentUndo);
    $('cw-agent-task')?.addEventListener('keydown', e => { if (e.key === 'Enter' && !e.shiftKey && (e.ctrlKey || e.metaKey)) { e.preventDefault(); _runAgent(null, false); } });

    // Datei-Aktionen
    $('cw-new')?.addEventListener('click', () => _addFile('neu.js', ''));
    $('cw-save')?.addEventListener('click', _save);
    $('cw-del')?.addEventListener('click', _deleteActive);
    $('cw-zip')?.addEventListener('click', _zip);
    $('cw-load')?.addEventListener('change', e => { const id = e.target.value; e.target.value = ''; _loadSaved(id); });
    $('cw-run')?.addEventListener('click', _run);
    $('cw-json-validate')?.addEventListener('click', () => _validateJson(true));
    $('cw-json-format')?.addEventListener('click', _formatJson);

    // Dateiname / Sprache
    $('cw-name')?.addEventListener('change', e => {
      if (_active < 0) return;
      _files[_active].path = (e.target.value || '').trim() || _files[_active].path;
      const l = _langForPath(_files[_active].path); if (l && l !== _lang) { _lang = l; _applyLangUI(); }
      _renderTree(); _wsSave();
    });
    $('cw-lang')?.addEventListener('change', e => { _lang = e.target.value; _applyLangUI(); if (_cm) setTimeout(() => _cm.refresh(), 0); });

    // Beispiele im ⋯-Menü
    $('cw-ex-toleranz')?.addEventListener('click', () => _loadExample('toleranz'));
    $('cw-ex-kurve')?.addEventListener('click',    () => _loadExample('kurve'));
    $('cw-ex-py')?.addEventListener('click',       () => _loadExample('python'));
    $('cw-ex-leer')?.addEventListener('click',     () => _loadExample('leer'));

    window.addEventListener('message', _onConsoleMsg);
    _initSplitters();
    _loadAgents();
    _loadSavedList();
    document.querySelector('.tab-btn[data-tab="ide"]')?.addEventListener('click', () => { _loadAgents(); _loadSavedList(); if (_cm) setTimeout(() => _cm.refresh(), 0); });

    _renderTree();
    if (_files.length) _setActive(_active >= 0 ? _active : 0);
    else _applyLangUI();
  }

  // Python-Option abschalten (vom Profil aufgerufen, wenn allow_python_exec=false)
  function disablePython() {
    const opt = document.querySelector('#cw-lang option[value="py"]');
    if (opt) opt.style.display = 'none';
  }

  return { init, loadFromChat, disablePython };

})();

// Rückwärtskompatibilität: chat.js ruft evtl. noch CodeIDE.loadFromChat auf.
if (typeof window !== 'undefined' && typeof window.CodeIDE === 'undefined') {
  window.CodeIDE = { loadFromChat: (c, n, l) => CodeWorkspace.loadFromChat(c, n, l), init() {}, refresh() {}, disablePython: () => CodeWorkspace.disablePython() };
}

/* ══ Beispiel-Programme (aus der früheren IDE übernommen, als window.*) ══ */
window.EXAMPLE_TOLERANZ = `// Toleranzanalyse – Schließmaß (Worst-Case & RSS)
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

window.EXAMPLE_FEDERKURVE = `// Federkennlinie & Arbeitspunkt — Werte unten anpassen

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

window.EXAMPLE_LEER = `// Leere Vorlage – KI-Assistent oder eigener Code
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

window.EXAMPLE_PYTHON = `# Python-Beispiel — wird serverseitig ausgeführt
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