/* AI_Framework_Thomas — Code-Tab: Mehrdatei-Projektstruktur erzeugen
   Erzeugt zu einer Aufgabe einen Dateibaum + Inhalte (POST /api/code/project),
   zeigt ihn als Baum, lässt einzelne Dateien ansehen/bearbeiten und als ZIP
   herunterladen (POST /api/code/project-zip). Nicht direkt ausführbar — einzelne
   Dateien lassen sich aber „↪ In IDE" zum Ausführen/Anpassen übernehmen. */

const CodeProject = (() => {

  let _files    = [];   // [{path, content}]
  let _selected = -1;

  const _esc = s => String(s == null ? '' : s).replace(/[&<>]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;' }[c]));
  const _model = () => (typeof Profile !== 'undefined' && Profile.modelFor) ? Profile.modelFor('coding') : undefined;
  const $ = id => document.getElementById(id);

  function _status(txt) { const el = $('cp-status'); if (el) el.textContent = txt || ''; }

  /* Coding-nahe Agenten zuerst (gleiche Logik wie in der IDE) */
  async function _loadAgents() {
    const sel = $('cp-agent');
    if (!sel) return;
    try {
      let agents = await (await fetch('/api/agents')).json();
      if (!Array.isArray(agents)) agents = [];
      const isCode = a => /program|cod|entwickl|software/i.test((a.category || '') + ' ' + (a.name || '')) || a.example_code;
      agents.sort((a, b) => (isCode(b) ? 1 : 0) - (isCode(a) ? 1 : 0));
      sel.innerHTML = '<option value="">— keiner —</option>' +
        agents.map(a => `<option value="${_esc(a.id)}">${(a.icon || '🤖')} ${_esc(a.name || a.id)}${a.example_code ? ' · 📎' : ''}</option>`).join('');
    } catch (_) {}
  }

  /* ── Dateibaum aus flachen Pfaden bauen + rendern ───────────────────── */
  function _buildTree(files) {
    const root = { dirs: {}, files: [] };
    files.forEach((f, idx) => {
      const parts = (f.path || '').split('/').filter(Boolean);
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
    const pad = d => `padding-left:${6 + d * 14}px`;
    Object.keys(node.dirs).sort().forEach(name => {
      html += `<div class="cp-row cp-dir" style="${pad(depth)}">📁 ${_esc(name)}</div>`;
      html += _renderNode(node.dirs[name], depth + 1);
    });
    node.files.sort((a, b) => a.name.localeCompare(b.name)).forEach(f => {
      const sel = f.idx === _selected ? ' sel' : '';
      html += `<div class="cp-row cp-file-row${sel}" data-idx="${f.idx}" style="${pad(depth)}" title="${_esc(_files[f.idx]?.path || '')}">📄 ${_esc(f.name)}</div>`;
    });
    return html;
  }

  function _renderTree() {
    const host = $('cp-tree');
    if (!host) return;
    if (!_files.length) {
      host.innerHTML = '<div class="planner-muted" style="padding:10px;font-size:12px">Noch kein Projekt erzeugt.</div>';
      return;
    }
    host.innerHTML = _renderNode(_buildTree(_files), 0);
    host.querySelectorAll('.cp-file-row').forEach(row => {
      row.addEventListener('click', () => _selectFile(parseInt(row.dataset.idx, 10)));
    });
  }

  function _selectFile(idx) {
    if (idx < 0 || idx >= _files.length) return;
    _selected = idx;
    const f = _files[idx];
    if ($('cp-file-name')) $('cp-file-name').textContent = f.path;
    if ($('cp-content'))   $('cp-content').value = f.content || '';
    _renderTree();
  }

  /* ── Projektstruktur erzeugen ───────────────────────────────────────── */
  async function _generate() {
    const prompt = ($('cp-prompt')?.value || '').trim();
    if (!prompt) { $('cp-prompt')?.focus(); _status('Bitte eine Aufgabe beschreiben.'); return; }
    const btn = $('btn-cp-generate');
    if (btn) btn.disabled = true;
    _status('⏳ Struktur wird erzeugt…');
    try {
      const resp = await fetch('/api/code/project', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          prompt,
          language: $('cp-lang')?.value || '',
          agent_id: $('cp-agent')?.value || '',
          max_files: parseInt($('cp-maxfiles')?.value, 10) || 10,
          model: _model(),
        }),
      });
      const d = await resp.json();
      if (!resp.ok) throw new Error(d.detail || ('HTTP ' + resp.status));
      if (d.tokens && typeof TokenMeter !== 'undefined') TokenMeter.add(d.tokens, 'Code-Projekt');
      _files = Array.isArray(d.files) ? d.files : [];
      _selected = -1;
      _renderTree();
      if (_files.length) _selectFile(0);
      else if ($('cp-content')) $('cp-content').value = '';
      _status(_files.length ? `✓ ${_files.length} Datei(en)${d.note ? ' · ' + d.note : ''}` : '⚠ Keine Dateien erzeugt — Aufgabe präzisieren.');
    } catch (e) {
      _status('Fehlgeschlagen: ' + e.message);
      if (typeof showToast === 'function') showToast('Projekt-Erzeugung fehlgeschlagen: ' + e.message);
    } finally {
      if (btn) btn.disabled = false;
    }
  }

  /* Bearbeitungen am angezeigten Dateiinhalt übernehmen (fürs ZIP) */
  function _syncContentEdit() {
    if (_selected >= 0 && _selected < _files.length) _files[_selected].content = $('cp-content')?.value || '';
  }

  /* ── ZIP-Download ───────────────────────────────────────────────────── */
  async function _downloadZip() {
    _syncContentEdit();
    if (!_files.length) { _status('Kein Projekt zum Herunterladen.'); return; }
    const zipname = (($('cp-prompt')?.value || 'projekt').trim().slice(0, 40).replace(/[^\w\- ]+/g, '').trim() || 'projekt');
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
    } catch (e) {
      _status('ZIP fehlgeschlagen: ' + e.message);
    }
  }

  /* Gewählte Datei in den IDE-Editor laden (zum Ausführen/Anpassen) */
  function _toEditor() {
    _syncContentEdit();
    if (_selected < 0) { _status('Erst links eine Datei wählen.'); return; }
    const f = _files[_selected];
    const lang = /\.py$/i.test(f.path) ? 'py' : /\.(js|mjs|html?)$/i.test(f.path) ? 'js' : '';
    if (typeof CodeIDE !== 'undefined' && CodeIDE.loadFromChat) {
      CodeIDE.loadFromChat(f.content, f.path.split('/').pop(), lang);
    }
  }

  function _clear() {
    if (_files.length && !confirm('Projektstruktur verwerfen?')) return;
    _files = []; _selected = -1;
    if ($('cp-content')) $('cp-content').value = '';
    if ($('cp-file-name')) $('cp-file-name').textContent = 'Keine Datei gewählt';
    _renderTree();
    _status('');
  }

  /* ── Ziehbarer Trenner Baum ↔ Inhalt ───────────────────────────────── */
  function _initSplitter() {
    const splitter = $('cp-splitter'), body = $('cp-body'), tree = $('cp-tree');
    if (!splitter || !body || !tree) return;
    let dragging = false;
    const onMove = e => {
      if (!dragging) return;
      const rect = body.getBoundingClientRect();
      let w = e.clientX - rect.left;
      w = Math.max(140, Math.min(rect.width - 220, w));
      tree.style.flexBasis = w + 'px';
    };
    splitter.addEventListener('mousedown', e => { dragging = true; e.preventDefault(); document.body.style.userSelect = 'none'; });
    window.addEventListener('mousemove', onMove);
    window.addEventListener('mouseup', () => { dragging = false; document.body.style.userSelect = ''; });
  }

  function init() {
    $('btn-cp-generate')?.addEventListener('click', _generate);
    $('btn-cp-zip')?.addEventListener('click', _downloadZip);
    $('btn-cp-clear')?.addEventListener('click', _clear);
    $('btn-cp-to-editor')?.addEventListener('click', _toEditor);
    $('cp-content')?.addEventListener('input', _syncContentEdit);
    _initSplitter();
    _loadAgents();
    // Beim Öffnen des Code-Tabs Agentenliste auffrischen
    document.querySelector('.tab-btn[data-tab="ide"]')?.addEventListener('click', _loadAgents);
    // Beim Wechsel auf den Projekt-Unterview ebenfalls
    document.querySelector('.code-subtab[data-subtab="project"]')?.addEventListener('click', _loadAgents);
  }

  return { init };

})();
