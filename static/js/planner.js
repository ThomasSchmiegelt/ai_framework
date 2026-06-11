/* AI_Framework_Thomas — Netzplan-Assistent (CPM) */

const Planner = (() => {

  /* ── Zustand ──────────────────────────────────────────────────────── */
  let _planId   = null;
  let _tasks    = [];       // { id, name, duration, predecessors[], successors[], resources, resource_list[], notes }
  let _desc     = '';       // Projektbeschreibung & Ziel
  let _systemPrompt = '';   // abgeleiteter Projekt-Agent (System-Prompt)
  let _catalog  = [];       // Ressourcen-Katalog [{kind, name, rate}]
  let _resMode  = 'free';   // 'free' | 'extend' | 'strict'
  let _resTaskIdx = -1;     // Aufgabe, deren Ressourcen gerade bearbeitet werden
  let _resDraft = [];       // Arbeitskopie der Ressourcenliste im Modal
  let _suggestData = null;  // { anchorIdx, predecessors[], successors[] }
  let _detailData = null;   // { idx, detail{}, preds[], succs[] }
  let _insertData = null;   // { aId, bId, candidates[] }
  let _replaceIdx = -1;     // Index der zu ersetzenden Aufgabe
  let _startDate = '';      // Projektstart (ISO yyyy-mm-dd) für Kalenderdaten
  let _endDate   = '';      // Projektenddatum / Deadline (ISO yyyy-mm-dd), optional
  let _workdays = false;    // Tage als Arbeitstage rechnen (Wochenenden überspringen)
  let _cpm      = {};       // { id: { ES, EF, LS, LF, float, critical } }
  let _rank     = {};       // { id: Ablaufreihenfolge (1-basiert) }
  let _cycleIds = [];       // Aufgaben-IDs, die in einem Zyklus stecken
  let _zoom     = 1.0;
  let _panX     = 0;
  let _panY     = 0;
  let _dragging = false;
  let _lastMX   = 0;
  let _lastMY   = 0;
  let _layout   = {};       // { id: {x, y, w, h} }
  let _canvas   = null;
  let _ctx      = null;
  let _aiStreaming = false;
  let _selectedTaskId = null; // ID der aktuell selektierten Aufgabe
  let _areaColors = {};       // cache: Bereich-Name → {border, bg}
  let _aiParsedTasks = null;  // vom KI-Chat geparste Aufgabenliste (für "übernehmen")
  let _capacity = null;       // globale Kapazitätsliste (für Auslastung/Zukauf), lazy geladen

  const NODE_W  = 180;
  const NODE_H  = 72;
  const HGAP    = 60;
  const VGAP    = 24;

  /* ── Bereichs-Farben (hash-basiert) ─────────────────────────────── */
  function _areaColor(area) {
    if (!area) return null;
    if (_areaColors[area]) return _areaColors[area];
    let h = 0;
    for (const c of area) h = (Math.imul(31, h) + c.charCodeAt(0)) | 0;
    const hue = ((h >>> 0) % 18) * 20; // 18 Töne, je 20° Abstand
    _areaColors[area] = {
      border: `hsl(${hue},65%,55%)`,
      bg:     `hsla(${hue},55%,22%,0.88)`,
      row:    `hsla(${hue},55%,45%,0.13)`,
    };
    return _areaColors[area];
  }

  /* ── CPM-Berechnung ──────────────────────────────────────────────── */
  function _computeCPM() {
    if (_tasks.length === 0) { _cpm = {}; return; }
    const map = {};
    for (const t of _tasks) map[t.id] = { ...t };

    // Topologische Sortierung (Kahn)
    const inDeg = {};
    for (const t of _tasks) inDeg[t.id] = 0;
    for (const t of _tasks) for (const p of (t.predecessors || [])) {
      if (map[p]) inDeg[t.id]++;
    }
    const queue = _tasks.filter(t => inDeg[t.id] === 0).map(t => t.id);
    const order = [];
    while (queue.length) {
      const id = queue.shift();
      order.push(id);
      for (const s of (map[id]?.successors || [])) {
        if (!map[s]) continue;
        inDeg[s]--;
        if (inDeg[s] === 0) queue.push(s);
      }
    }
    // Zyklen erkennen: was Kahn nicht erreicht hat, steckt in einem Zyklus
    _cycleIds = _tasks.map(t => t.id).filter(id => !order.includes(id));
    for (const id of _cycleIds) order.push(id);

    const res = {};
    for (const t of _tasks) res[t.id] = { ES: 0, EF: 0, LS: 0, LF: 0, float: 0, critical: false };

    // Vorwärtsrechnung
    for (const id of order) {
      const t = map[id];
      const dur = Number(t.duration) || 0;
      let es = 0;
      for (const p of (t.predecessors || [])) {
        if (res[p]) es = Math.max(es, res[p].EF);
      }
      res[id].ES = es;
      res[id].EF = es + dur;
    }

    // Projektenddatum
    const projEnd = Math.max(...Object.values(res).map(r => r.EF));

    // Rückwärtsrechnung
    for (const id of [...order].reverse()) {
      const t = map[id];
      const dur = Number(t.duration) || 0;
      const succs = (t.successors || []).filter(s => res[s]);
      let lf = succs.length === 0 ? projEnd : Math.min(...succs.map(s => res[s].LS));
      res[id].LF = lf;
      res[id].LS = lf - dur;
      res[id].float = res[id].LF - res[id].EF;
      res[id].critical = res[id].float === 0;
    }

    _cpm = res;

    // Ablaufreihenfolge (unabhängig von der ID): nach frühestem Start, dann
    // nach topologischer Position sortieren → stabile 1-basierte Nummer.
    const orderIdx = {};
    order.forEach((id, i) => { orderIdx[id] = i; });
    const ranked = _tasks.map(t => t.id).sort((a, b) => {
      const ea = res[a]?.ES ?? 0, eb = res[b]?.ES ?? 0;
      if (ea !== eb) return ea - eb;
      return (orderIdx[a] ?? 0) - (orderIdx[b] ?? 0);
    });
    _rank = {};
    ranked.forEach((id, i) => { _rank[id] = i + 1; });
  }

  /* ── Ressourcen & Kosten ─────────────────────────────────────────── */
  // Kosten je Ressource: bei Stunden > 0 → Menge·Stunden·Satz, sonst Menge·Satz (pauschal)
  function _resCost(r) {
    const qty = Number(r.qty) || 0, hours = Number(r.hours) || 0, rate = Number(r.rate) || 0;
    return hours > 0 ? qty * hours * rate : qty * rate;
  }
  function _taskCost(t) {
    return (t.resource_list || []).reduce((s, r) => s + _resCost(r), 0);
  }
  function _taskPersonHours(t) {
    return (t.resource_list || [])
      .filter(r => r.kind === 'human')
      .reduce((s, r) => s + (Number(r.qty) || 0) * (Number(r.hours) || 0), 0);
  }
  function _fmtEur(v) {
    return (Math.round(v * 100) / 100).toLocaleString('de-DE', { minimumFractionDigits: 0, maximumFractionDigits: 2 }) + ' €';
  }
  function _uniqueTaskId() {
    let n = _tasks.length + 1;
    let id;
    do { id = 'T' + n; n++; } while (_tasks.some(t => t.id === id));
    return id;
  }

  /* ── Verknüpfungs-Konsistenz ─────────────────────────────────────── */
  // Macht Vorgänger/Nachfolger symmetrisch, entfernt unbekannte IDs,
  // Selbstbezüge und Duplikate. Nach jeder strukturellen Änderung aufrufen.
  function _normalizeLinks() {
    const ids = new Set(_tasks.map(t => t.id));
    const preds = {}, succs = {};
    for (const t of _tasks) { preds[t.id] = new Set(); succs[t.id] = new Set(); }
    for (const t of _tasks) {
      for (const p of (t.predecessors || [])) {
        if (ids.has(p) && p !== t.id) { preds[t.id].add(p); succs[p].add(t.id); }
      }
      for (const s of (t.successors || [])) {
        if (ids.has(s) && s !== t.id) { succs[t.id].add(s); preds[s].add(t.id); }
      }
    }
    for (const t of _tasks) {
      t.predecessors = [...preds[t.id]];
      t.successors = [...succs[t.id]];
    }
  }

  // Aufgaben-ID stabil umbenennen: kaskadiert in alle Verknüpfungen.
  function _renameTaskId(oldId, newId) {
    newId = (newId || '').trim();
    if (!newId || newId === oldId) return false;
    if (_tasks.some(t => t.id === newId)) { showToast(`ID „${newId}" existiert bereits`); return false; }
    for (const t of _tasks) {
      if (t.id === oldId) t.id = newId;
      t.predecessors = (t.predecessors || []).map(p => p === oldId ? newId : p);
      t.successors   = (t.successors   || []).map(s => s === oldId ? newId : s);
    }
    return true;
  }

  // Aufgabe entfernen; bei rebridge werden Vorgänger direkt mit Nachfolgern
  // verbunden, damit die Kette nicht reißt.
  function _removeTask(idx, rebridge = true) {
    const t = _tasks[idx];
    if (!t) return;
    const ps = (t.predecessors || []).slice();
    const ss = (t.successors   || []).slice();
    _tasks.splice(idx, 1);
    for (const o of _tasks) {
      o.predecessors = (o.predecessors || []).filter(x => x !== t.id);
      o.successors   = (o.successors   || []).filter(x => x !== t.id);
    }
    if (rebridge) {
      for (const p of ps) for (const s of ss) {
        if (p === s) continue;
        const pt = _tasks.find(x => x.id === p);
        const st = _tasks.find(x => x.id === s);
        if (pt && st) {
          if (!pt.successors.includes(s)) pt.successors.push(s);
          if (!st.predecessors.includes(p)) st.predecessors.push(p);
        }
      }
    }
    _normalizeLinks();
  }

  /* ── "Mach schön": säubern, sortieren, neu zeichnen ──────────────── */
  function _beautify() {
    if (!_tasks.length) { showToast('Keine Aufgaben'); return; }
    _normalizeLinks();
    _computeCPM();                       // füllt _rank
    _tasks.sort((a, b) => (_rank[a.id] || 0) - (_rank[b.id] || 0));
    _recalcAndRender();
    _fitView();
    showToast('✨ Verknüpfungen geprüft, nach Ablauf sortiert, Netzplan neu gezeichnet');
  }

  /* ── Datums-Mapping (Projektstart + Tag-Offset → Kalenderdatum) ──── */
  // Verschiebt ein Datum um n Arbeitstage (Wochenenden überspringen); n kann
  // negativ sein (rückwärts, z.B. für „bestellen bis").
  function _addWorkdays(base, n) {
    const d = new Date(base);
    let remaining = Math.round(n);
    if (remaining === 0) return d;
    const step = remaining > 0 ? 1 : -1;
    remaining = Math.abs(remaining);
    while (remaining > 0) {
      d.setDate(d.getDate() + step);
      const dow = d.getDay();
      if (dow !== 0 && dow !== 6) remaining--;
    }
    return d;
  }
  function _effectiveStartDate() {
    if (_startDate) return _startDate;
    if (_endDate && Object.keys(_cpm).length) {
      const maxEF = Math.max(...Object.values(_cpm).map(r => r.EF), 0);
      if (maxEF > 0) {
        const end = new Date(_endDate + 'T00:00:00');
        if (!isNaN(end)) {
          const derived = _workdays ? _addWorkdays(end, -maxEF) : new Date(end.getTime() - maxEF * 86400000);
          return derived.toISOString().slice(0, 10);
        }
      }
    }
    return null;
  }

  function _dayToDate(day) {
    const sd = _effectiveStartDate();
    if (!sd) return null;
    const base = new Date(sd + 'T00:00:00');
    if (isNaN(base)) return null;
    if (_workdays) return _addWorkdays(base, Number(day) || 0);
    base.setDate(base.getDate() + Math.round(Number(day) || 0));
    return base;
  }
  function _fmtDay(day) {
    const d = _dayToDate(day);
    if (d) return d.toLocaleDateString('de-DE', { day: '2-digit', month: '2-digit', year: 'numeric' });
    const n = Math.round(Number(day) || 0);
    return _workdays ? `AT ${n}` : `Tag ${n}`;
  }

  /* ── Layout berechnen ────────────────────────────────────────────── */
  function _computeLayout() {
    if (_tasks.length === 0) { _layout = {}; return; }
    const map = {};
    for (const t of _tasks) map[t.id] = { ...t, level: -1 };

    // Topologische Sortierung
    const inDeg = {};
    for (const t of _tasks) inDeg[t.id] = 0;
    for (const t of _tasks) for (const p of (t.predecessors || [])) {
      if (map[p]) inDeg[t.id]++;
    }
    const queue = _tasks.filter(t => inDeg[t.id] === 0).map(t => t.id);
    const levelMap = {};
    while (queue.length) {
      const id = queue.shift();
      const maxPredLevel = Math.max(-1, ...(map[id].predecessors || []).map(p => levelMap[p] ?? -1));
      levelMap[id] = maxPredLevel + 1;
      for (const s of (map[id].successors || [])) {
        if (!map[s]) continue;
        inDeg[s]--;
        if (inDeg[s] === 0) queue.push(s);
      }
    }
    for (const t of _tasks) if (levelMap[t.id] === undefined) levelMap[t.id] = 0;

    // Gruppen pro Level
    const levels = {};
    for (const [id, lv] of Object.entries(levelMap)) {
      if (!levels[lv]) levels[lv] = [];
      levels[lv].push(id);
    }

    _layout = {};
    const colX = {};
    let x = 20;
    const maxLv = Math.max(...Object.keys(levels).map(Number));
    for (let lv = 0; lv <= maxLv; lv++) {
      colX[lv] = x;
      x += NODE_W + HGAP;
    }
    for (const [lv, ids] of Object.entries(levels)) {
      let y = 20;
      for (const id of ids) {
        _layout[id] = { x: colX[+lv], y, w: NODE_W, h: NODE_H };
        y += NODE_H + VGAP;
      }
    }
  }

  /* ── Canvas rendern ──────────────────────────────────────────────── */
  function _render() {
    if (!_canvas || !_ctx) return;
    const W = _canvas.width, H = _canvas.height;
    _ctx.clearRect(0, 0, W, H);
    _ctx.save();
    _ctx.translate(_panX, _panY);
    _ctx.scale(_zoom, _zoom);

    // Pfeile zeichnen
    for (const t of _tasks) {
      const from = _layout[t.id];
      if (!from) continue;
      for (const s of (t.successors || [])) {
        const to = _layout[s];
        if (!to) continue;
        const isCritical = _cpm[t.id]?.critical && _cpm[s]?.critical;
        _drawArrow(
          from.x + NODE_W, from.y + NODE_H / 2,
          to.x,            to.y + NODE_H / 2,
          isCritical ? '#ef4444' : '#3b82f6'
        );
      }
    }

    // Knoten zeichnen
    for (const t of _tasks) {
      const pos = _layout[t.id];
      if (!pos) continue;
      const cpm = _cpm[t.id] || {};
      _drawNode(pos.x, pos.y, t, cpm);
    }

    _ctx.restore();
  }

  function _drawArrow(x1, y1, x2, y2, color) {
    const cx = (x1 + x2) / 2;
    _ctx.beginPath();
    _ctx.moveTo(x1, y1);
    _ctx.bezierCurveTo(cx, y1, cx, y2, x2, y2);
    _ctx.strokeStyle = color;
    _ctx.lineWidth = 1.5;
    _ctx.stroke();

    // Pfeilspitze
    const angle = Math.atan2(y2 - y1, x2 - x1);
    const al = 8, aw = 4;
    _ctx.beginPath();
    _ctx.moveTo(x2, y2);
    _ctx.lineTo(x2 - al * Math.cos(angle - aw), y2 - al * Math.sin(angle - aw));
    _ctx.lineTo(x2 - al * Math.cos(angle + aw), y2 - al * Math.sin(angle + aw));
    _ctx.closePath();
    _ctx.fillStyle = color;
    _ctx.fill();
  }

  function _drawNode(x, y, task, cpm) {
    const W = NODE_W, H = NODE_H;
    const isCrit = cpm.critical;
    const areaCol = (!isCrit && task.area) ? _areaColor(task.area) : null;
    const borderColor = isCrit ? '#ef4444' : (areaCol ? areaCol.border : '#3b82f6');
    const bgColor     = isCrit ? 'rgba(239,68,68,.1)' : (areaCol ? areaCol.bg : 'rgba(15,22,35,.85)');

    // Hintergrund
    _ctx.fillStyle = bgColor;
    _ctx.strokeStyle = borderColor;
    _ctx.lineWidth = isCrit ? 2 : 1;
    _roundRect(x, y, W, H, 6);
    _ctx.fill();
    _ctx.stroke();

    // Trennlinien
    _ctx.strokeStyle = borderColor + '55';
    _ctx.lineWidth = 0.5;
    _ctx.beginPath();
    _ctx.moveTo(x + W / 2, y);
    _ctx.lineTo(x + W / 2, y + H / 2);
    _ctx.moveTo(x, y + H / 2);
    _ctx.lineTo(x + W, y + H / 2);
    _ctx.stroke();

    // Texte
    _ctx.fillStyle = isCrit ? '#fca5a5' : '#94a3b8';
    _ctx.font = '9px sans-serif';
    _ctx.textAlign = 'left';
    _ctx.fillText('ES', x + 4, y + H / 2 - 4);
    _ctx.textAlign = 'right';
    _ctx.fillText('EF', x + W - 4, y + H / 2 - 4);
    _ctx.textAlign = 'left';
    _ctx.fillText('LS', x + 4, y + H - 4);
    _ctx.textAlign = 'right';
    _ctx.fillText('LF', x + W / 2 - 4, y + H - 4);
    _ctx.textAlign = 'center';
    _ctx.fillText('Float', x + W * 3 / 4, y + H - 4);

    // Werte
    _ctx.fillStyle = isCrit ? '#f87171' : '#e2e8f5';
    _ctx.font = 'bold 11px sans-serif';
    _ctx.textAlign = 'left';
    _ctx.fillText(String(cpm.ES ?? ''), x + 4, y + H / 2 - 14);
    _ctx.textAlign = 'right';
    _ctx.fillText(String(cpm.EF ?? ''), x + W - 4, y + H / 2 - 14);
    _ctx.textAlign = 'left';
    _ctx.fillText(String(cpm.LS ?? ''), x + 4, y + H - 14);
    _ctx.textAlign = 'right';
    _ctx.fillText(String(cpm.LF ?? ''), x + W / 2 - 4, y + H - 14);
    _ctx.textAlign = 'center';
    _ctx.fillStyle = cpm.float === 0 ? '#f87171' : '#34d399';
    _ctx.fillText(String(cpm.float ?? ''), x + W * 3 / 4, y + H - 14);

    // ID + Name (oben Mitte)
    _ctx.fillStyle = '#e2e8f5';
    _ctx.font = 'bold 11px sans-serif';
    _ctx.textAlign = 'center';
    const label = `[${task.id}] ${task.name}`.substring(0, 24);
    _ctx.fillText(label, x + W / 2, y + 14);

    // Dauer (oben rechts klein)
    _ctx.fillStyle = '#94a3b8';
    _ctx.font = '9px sans-serif';
    _ctx.textAlign = 'right';
    _ctx.fillText(`${task.duration}d`, x + W - 4, y + 14);
  }

  function _roundRect(x, y, w, h, r) {
    _ctx.beginPath();
    _ctx.moveTo(x + r, y);
    _ctx.lineTo(x + w - r, y);
    _ctx.arcTo(x + w, y, x + w, y + r, r);
    _ctx.lineTo(x + w, y + h - r);
    _ctx.arcTo(x + w, y + h, x + w - r, y + h, r);
    _ctx.lineTo(x + r, y + h);
    _ctx.arcTo(x, y + h, x, y + h - r, r);
    _ctx.lineTo(x, y + r);
    _ctx.arcTo(x, y, x + r, y, r);
    _ctx.closePath();
  }

  /* ── Tabellen-Editor ─────────────────────────────────────────────── */
  function _renderTable() {
    const tbody = document.getElementById('task-tbody');
    if (!tbody) return;
    tbody.innerHTML = '';

    // Datalist für Bereich-Autocomplete aktualisieren (steht im HTML außerhalb der Tabelle)
    const dl = document.getElementById('plan-area-list');
    if (dl) {
      dl.innerHTML = '';
      [...new Set(_tasks.map(t => t.area || '').filter(Boolean))].forEach(a => {
        const o = document.createElement('option'); o.value = a; dl.appendChild(o);
      });
    }

    for (let i = 0; i < _tasks.length; i++) {
      const t = _tasks[i];
      const cpm = _cpm[t.id] || {};
      const cost = _taskCost(t);
      const resLabel = cost > 0
        ? `${_fmtEur(cost)} · ${(t.resource_list||[]).length} Res.`
        : '＋ Ressourcen';
      const areaCol = t.area ? _areaColor(t.area) : null;
      const tr = document.createElement('tr');
      tr.dataset.i = i;
      if (cpm.critical) tr.classList.add('critical');
      if (t.id === _selectedTaskId) tr.classList.add('task-row-selected');
      if (areaCol && !cpm.critical) tr.style.background = areaCol.row;
      if (areaCol) tr.style.borderLeft = `3px solid ${areaCol.border}`;
      tr.innerHTML = `
        <td style="text-align:center;color:var(--text-dim);font-size:11px">${_rank[t.id] ?? ''}</td>
        <td><input value="${escHtml(t.id)}" data-f="id" style="width:50px" /></td>
        <td><input value="${escHtml(t.name)}" data-f="name" style="width:120px" /></td>
        <td><input value="${t.duration}" data-f="duration" type="number" min="0" style="width:50px" /></td>
        <td><input value="${escHtml((t.predecessors||[]).join(','))}" data-f="predecessors" placeholder="T1,T2" style="width:80px" /></td>
        <td><input value="${escHtml((t.successors||[]).join(','))}" data-f="successors" placeholder="T3" style="width:80px" /></td>
        <td><input value="${escHtml(t.area||'')}" data-f="area" list="plan-area-list" placeholder="Bereich…" style="width:90px" /></td>
        <td><button class="btn-task-res" data-i="${i}" title="Ressourcen & Kosten bearbeiten">${escHtml(resLabel)}</button></td>
        <td style="color:var(--text-dim);font-size:11px;text-align:right">${cpm.ES??''}</td>
        <td style="color:var(--text-dim);font-size:11px;text-align:right">${cpm.EF??''}</td>
        <td style="color:var(--text-dim);font-size:11px;text-align:right">${cpm.LS??''}</td>
        <td style="color:var(--text-dim);font-size:11px;text-align:right">${cpm.LF??''}</td>
        <td style="font-size:11px;text-align:right;color:${cpm.float===0?'#f87171':'#34d399'}">${cpm.float??''}</td>
        <td style="white-space:nowrap">
          <button class="btn-mark-start ${t.is_start ? 'active' : ''}" data-i="${i}" title="Als Projektanfang markieren – KI schlägt keine Vorgänger vor">🏁</button>
          <button class="btn-mark-end ${t.is_end ? 'active' : ''}" data-i="${i}" title="Als Projektende markieren – KI schlägt keine Nachfolger vor">🛑</button>
          <button class="btn-suggest-task" data-i="${i}" title="KI: Vorgänger/Nachfolger vorschlagen">✨</button>
          <button class="btn-detail-task" data-i="${i}" title="KI: Aufgabe detaillieren (editierbar)">📝</button>
          <button class="btn-research-task ${t.researched ? 'active' : ''}" data-i="${i}" title="Wissenschaftlich recherchieren → Dossier ins Plan-RAG">🔬</button>
          ${t.doc ? `<button class="btn-view-doc" data-i="${i}" title="Recherche-Dossier ansehen">📄</button>` : ''}
          <button class="btn-replace-task" data-i="${i}" title="Aufgabe ersetzen (durch bestehende oder neue)">🔁</button>
          <button class="btn-del-task" data-i="${i}" title="Aufgabe löschen (Verknüpfungen werden neu verbunden)">🗑</button>
        </td>
      `;
      // Zeile klicken (außerhalb von Inputs/Buttons) → Aufgabe selektieren
      tr.addEventListener('click', e => {
        if (['INPUT','BUTTON','SELECT','TEXTAREA'].includes(e.target.tagName)) return;
        _selectedTaskId = _selectedTaskId === t.id ? null : t.id;
        tbody.querySelectorAll('tr[data-i]').forEach(r => {
          r.classList.toggle('task-row-selected', _tasks[+r.dataset.i]?.id === _selectedTaskId);
        });
        _updateSelectedInfo();
      });
      tbody.appendChild(tr);
    }

    _updateSelectedInfo();

    // Events
    tbody.querySelectorAll('input[data-f]').forEach(inp => {
      inp.addEventListener('change', e => {
        const tr = e.target.closest('tr');
        const i = +tr.dataset.i;
        const f = e.target.dataset.f;
        if (f === 'id') {
          // ID stabil umbenennen → kaskadiert in alle Verknüpfungen
          const oldId = _tasks[i].id;
          if (!_renameTaskId(oldId, inp.value)) { inp.value = oldId; return; }
          _recalcAndRender();
        } else if (f === 'predecessors' || f === 'successors') {
          _tasks[i][f] = inp.value.split(',').map(s => s.trim()).filter(Boolean);
          _normalizeLinks();
          _recalcAndRender();
        } else if (f === 'duration') {
          _tasks[i][f] = Number(inp.value) || 0;
          _recalcAndRender();
        } else if (f === 'area') {
          _tasks[i].area = inp.value.trim();
          _areaColors = {}; // cache invalidieren falls neue Bereiche entstehen
          _recalcAndRender();
        } else {
          _tasks[i][f] = inp.value.trim();
        }
      });
    });
    tbody.querySelectorAll('.btn-del-task').forEach(btn => {
      btn.addEventListener('click', () => {
        _removeTask(+btn.dataset.i, true);
        _recalcAndRender();
      });
    });
    tbody.querySelectorAll('.btn-replace-task').forEach(btn => {
      btn.addEventListener('click', () => _openReplaceModal(+btn.dataset.i));
    });
    tbody.querySelectorAll('.btn-task-res').forEach(btn => {
      btn.addEventListener('click', () => _openResourceModal(+btn.dataset.i));
    });
    tbody.querySelectorAll('.btn-suggest-task').forEach(btn => {
      btn.addEventListener('click', () => _suggestFor(+btn.dataset.i));
    });
    tbody.querySelectorAll('.btn-detail-task').forEach(btn => {
      btn.addEventListener('click', () => _detailTask(+btn.dataset.i));
    });
    tbody.querySelectorAll('.btn-research-task').forEach(btn => {
      btn.addEventListener('click', () => _researchTask(+btn.dataset.i));
    });
    tbody.querySelectorAll('.btn-view-doc').forEach(btn => {
      btn.addEventListener('click', () => _viewDoc(+btn.dataset.i));
    });
    tbody.querySelectorAll('.btn-mark-start').forEach(btn => {
      btn.addEventListener('click', () => {
        const t = _tasks[+btn.dataset.i];
        t.is_start = !t.is_start;
        _renderTable();
      });
    });
    tbody.querySelectorAll('.btn-mark-end').forEach(btn => {
      btn.addEventListener('click', () => {
        const t = _tasks[+btn.dataset.i];
        t.is_end = !t.is_end;
        _renderTable();
      });
    });
  }

  /* ── Rollup-Anzeige (Gesamtkosten, Personenstunden) ──────────────── */
  function _updateRollup() {
    const el = document.getElementById('planner-rollup');
    if (!el) return;
    if (_tasks.length === 0) { el.textContent = ''; return; }
    const totalCost = _tasks.reduce((s, t) => s + _taskCost(t), 0);
    const totalPH   = _tasks.reduce((s, t) => s + _taskPersonHours(t), 0);
    const critCost  = _tasks.reduce((s, t) => s + (_cpm[t.id]?.critical ? _taskCost(t) : 0), 0);
    el.textContent = `Σ ${_fmtEur(totalCost)} · ${totalPH} Pers.-h · krit. Pfad ${_fmtEur(critCost)}`;
  }

  /* ── Vollständige Neuberechnung und Render ───────────────────────── */
  function _recalcAndRender() {
    _computeCPM();
    _computeLayout();
    _resizeCanvas();
    _render();
    _renderTable();
    _updateRollup();
    _updateWarnings();
  }

  /* ── Canvas-Größe anpassen ──────────────────────────────────────── */
  function _resizeCanvas() {
    if (!_canvas) return;
    const parent = _canvas.parentElement;
    _canvas.width  = parent.clientWidth  || 400;
    _canvas.height = parent.clientHeight || 300;
  }

  /* ── Ziehbarer Trenner: Breite Tabelle ↔ Netzplan ───────────────── */
  const _SPLIT_KEY = 'planner_table_width';
  function _initSplitter() {
    const splitter = document.getElementById('planner-splitter');
    const area     = document.getElementById('planner-table-area');
    const main     = document.getElementById('planner-main');
    if (!splitter || !area || !main) return;

    // Gespeicherte Breite wiederherstellen
    const saved = parseInt(localStorage.getItem(_SPLIT_KEY) || '', 10);
    if (saved > 0) area.style.width = saved + 'px';

    const _apply = (clientX) => {
      const rect = main.getBoundingClientRect();
      let w = clientX - rect.left;
      const max = rect.width - 160;          // Netzplan mind. ~160px
      w = Math.max(220, Math.min(w, max));   // Tabelle mind. 220px
      area.style.width = w + 'px';
    };

    const _onMove = (e) => { _apply(e.clientX); };
    const _onUp = () => {
      splitter.classList.remove('dragging');
      document.body.style.userSelect = '';
      document.removeEventListener('mousemove', _onMove);
      document.removeEventListener('mouseup', _onUp);
      localStorage.setItem(_SPLIT_KEY, String(parseInt(area.style.width, 10) || 0));
    };

    splitter.addEventListener('mousedown', (e) => {
      e.preventDefault();
      splitter.classList.add('dragging');
      document.body.style.userSelect = 'none';
      document.addEventListener('mousemove', _onMove);
      document.addEventListener('mouseup', _onUp);
    });

    // Doppelklick → Standardbreite (55%) wiederherstellen
    splitter.addEventListener('dblclick', () => {
      area.style.width = '';
      localStorage.removeItem(_SPLIT_KEY);
      _recalcAndRender();
    });
  }

  /* ── Ziehbarer horizontaler Trenner: Höhe des KI-Chatfensters ─────── */
  const _HSPLIT_KEY = 'planner_ai_height';
  function _initHSplitter() {
    const splitter = document.getElementById('planner-h-splitter');
    const panel    = document.getElementById('planner-panel');
    const ai       = document.getElementById('planner-ai-panel');
    if (!splitter || !panel || !ai) return;

    // Gespeicherte Höhe wiederherstellen
    const saved = parseInt(localStorage.getItem(_HSPLIT_KEY) || '', 10);
    if (saved > 0) panel.style.setProperty('--planner-ai-h', saved + 'px');

    const _apply = (clientY) => {
      const rect = panel.getBoundingClientRect();
      let h = rect.bottom - clientY;             // Abstand vom unteren Rand
      const max = rect.height - 200;             // oberer Bereich mind. ~200px
      h = Math.max(90, Math.min(h, max));        // Chat mind. 90px
      panel.style.setProperty('--planner-ai-h', h + 'px');
    };

    const _onMove = (e) => { _apply(e.clientY); };
    const _onUp = () => {
      splitter.classList.remove('dragging');
      document.body.style.userSelect = '';
      document.removeEventListener('mousemove', _onMove);
      document.removeEventListener('mouseup', _onUp);
      const cur = parseInt(panel.style.getPropertyValue('--planner-ai-h'), 10) || 0;
      localStorage.setItem(_HSPLIT_KEY, String(cur));
      _recalcAndRender();
    };

    splitter.addEventListener('mousedown', (e) => {
      e.preventDefault();
      splitter.classList.add('dragging');
      document.body.style.userSelect = 'none';
      document.addEventListener('mousemove', _onMove);
      document.addEventListener('mouseup', _onUp);
    });

    // Doppelklick → Standardhöhe (220px) wiederherstellen
    splitter.addEventListener('dblclick', () => {
      panel.style.removeProperty('--planner-ai-h');
      localStorage.removeItem(_HSPLIT_KEY);
      _recalcAndRender();
    });
  }

  /* ── Plan-Liste laden ────────────────────────────────────────────── */
  async function _loadPlanList() {
    const sel = document.getElementById('planner-plan-select');
    if (!sel) return;
    try {
      const resp = await fetch('/api/plans');
      const list = await resp.json();
      const prev = sel.value;
      sel.innerHTML = '<option value="">— Plan laden —</option>';
      for (const p of list) {
        const opt = document.createElement('option');
        opt.value = p.id;
        opt.textContent = p.name;
        sel.appendChild(opt);
      }
      if (prev) sel.value = prev;
    } catch (e) {}
  }

  /* ── Plan laden ──────────────────────────────────────────────────── */
  async function _loadPlan(id) {
    try {
      const resp = await fetch(`/api/plans/${id}`);
      const plan = await resp.json();
      _planId = plan.id;
      _tasks  = plan.tasks || [];
      _desc   = plan.description || '';
      _systemPrompt = plan.system_prompt || '';
      _catalog = plan.resource_catalog || [];
      _resMode = plan.resource_mode || 'free';
      _startDate = plan.start_date || '';
      _endDate   = plan.end_date   || '';
      _workdays = !!plan.workdays;
      const sd = document.getElementById('planner-start-date');
      if (sd) sd.value = _startDate;
      const ed = document.getElementById('planner-end-date');
      if (ed) ed.value = _endDate;
      const wd = document.getElementById('planner-workdays');
      if (wd) wd.checked = _workdays;
      document.getElementById('planner-plan-name').value = plan.name || '';
      const modeSel = document.getElementById('planner-res-mode');
      if (modeSel) modeSel.value = _resMode;
      _updateCatalogStatus();
      const descEl = document.getElementById('planner-desc');
      if (descEl) descEl.value = _desc;
      const status = document.getElementById('planner-agent-status');
      if (status) { status.textContent = _systemPrompt ? '✓ Projekt-Agent geladen' : ''; status.title = _systemPrompt; }
      document.getElementById('btn-delete-plan').style.display = '';
      _recalcAndRender();
    } catch (e) {
      showToast('Plan konnte nicht geladen werden');
    }
  }

  /* ── Plan speichern ──────────────────────────────────────────────── */
  async function _savePlan() {
    const name = document.getElementById('planner-plan-name').value.trim() || 'Unbenannt';
    _desc = (document.getElementById('planner-desc')?.value || '').trim();
    const payload = { name, tasks: _tasks, description: _desc, system_prompt: _systemPrompt,
                      resource_catalog: _catalog, resource_mode: _resMode, start_date: _startDate,
                      end_date: _endDate, workdays: _workdays };
    try {
      if (_planId) {
        await fetch(`/api/plans/${_planId}`, {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload),
        });
      } else {
        const resp = await fetch('/api/plans', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload),
        });
        const plan = await resp.json();
        _planId = plan.id;
        document.getElementById('btn-delete-plan').style.display = '';
      }
      await _loadPlanList();
      document.getElementById('planner-plan-select').value = _planId;
      showToast('Plan gespeichert');
    } catch (e) {
      showToast('Fehler beim Speichern');
    }
  }

  /* ── Tätigkeits-Recherche (wissenschaftlich → Plan-RAG) ──────────── */
  let _researchStop = false;

  async function _ensureSaved() {
    if (!_planId) await _savePlan();
    return !!_planId;
  }

  // Stilles Speichern (ohne Toast/Listen-Reload) – für kontinuierliches Sichern
  async function _savePlanSilent() {
    if (!_planId) return;
    const name = document.getElementById('planner-plan-name').value.trim() || 'Unbenannt';
    const payload = { name, tasks: _tasks, description: _desc, system_prompt: _systemPrompt,
                      resource_catalog: _catalog, resource_mode: _resMode, start_date: _startDate,
                      end_date: _endDate, workdays: _workdays };
    try {
      await fetch(`/api/plans/${_planId}`, {
        method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
      });
    } catch (_) {}
  }

  async function _researchTask(idx) {
    const t = _tasks[idx];
    if (!t) return false;
    if (!(await _ensureSaved())) { showToast('Plan konnte nicht gespeichert werden'); return false; }
    const model = _model() || undefined;
    showToast(`🔬 Recherchiere „${t.name}"…`);
    try {
      const r = await fetch(`/api/plans/${_planId}/research-task`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ task_id: t.id, model }),
      });
      const data = await r.json();
      if (!r.ok) throw new Error(data.detail || r.status);
      t.doc = data.md; t.doc_role = data.role; t.researched = true;
      showToast(`✓ ${t.id}: Dossier in „${data.collection_name}" (${data.n_sources} Quellen)`);
      await _savePlanSilent();   // kontinuierlich sichern
      _renderTable();
      return true;
    } catch (e) {
      console.error('Recherche-Fehler', e);
      showToast(`Fehler bei ${t.id}: ${e.message}`);
      return false;
    }
  }

  async function _researchAll() {
    if (!(await _ensureSaved())) return;
    const todo = _tasks.filter(t => !t.researched);
    if (!todo.length) { showToast('Alle Tätigkeiten bereits recherchiert'); return; }
    if (!confirm(`${todo.length} Tätigkeit(en) recherchieren? Das kann je nach Anzahl lange dauern.`)) return;
    _researchStop = false;
    const prog = document.getElementById('planner-research-progress');
    const bar = document.getElementById('planner-research-bar');
    const btnAll = document.getElementById('btn-research-all');
    const btnStop = document.getElementById('btn-research-stop');
    if (btnAll) btnAll.style.display = 'none';
    if (btnStop) btnStop.style.display = '';
    if (bar) { bar.max = todo.length; bar.value = 0; bar.style.display = ''; }
    let done = 0;
    for (const t of todo) {
      if (_researchStop) break;
      if (prog) prog.textContent = `${done + 1}/${todo.length}: ${t.id}…`;
      await _researchTask(_tasks.indexOf(t));
      done++;
      if (bar) bar.value = done;
    }
    if (prog) prog.textContent = _researchStop ? `abgebrochen (${done}/${todo.length})` : `fertig (${done}/${todo.length})`;
    if (btnAll) btnAll.style.display = '';
    if (btnStop) btnStop.style.display = 'none';
    setTimeout(() => { if (bar) bar.style.display = 'none'; }, 4000);
  }

  function _viewDoc(idx) {
    const t = _tasks[idx];
    if (!t || !t.doc) return;
    document.getElementById('plan-doc-title').textContent =
      `📄 ${t.id} – ${t.name}${t.doc_role ? ' · ' + t.doc_role : ''}`;
    const body = document.getElementById('plan-doc-body');
    if (window._ensureKatexMarked) window._ensureKatexMarked();
    body.innerHTML = (typeof marked !== 'undefined') ? marked.parse(t.doc) : `<pre>${escHtml(t.doc)}</pre>`;
    body.querySelectorAll('a[href]').forEach(a => { a.target = '_blank'; a.rel = 'noopener noreferrer'; });
    document.getElementById('plan-doc-overlay').classList.add('active');
  }

  /* ── KI-Assistent ────────────────────────────────────────────────── */
  async function _sendAI() {
    if (_aiStreaming) return;
    const input = document.getElementById('planner-ai-input');
    const msg = input.value.trim();
    if (!msg) return;
    input.value = '';

    const model = _model() || 'qwen3.6-16k:latest';

    // Selektierte Aufgabe als Kontext hinzufügen
    const selTask = _selectedTaskId ? _tasks.find(t => t.id === _selectedTaskId) : null;
    let contextMsg = msg;
    if (selTask) {
      contextMsg = `[Ausgewählte Aufgabe: ${JSON.stringify({
        id: selTask.id, name: selTask.name, duration: selTask.duration,
        area: selTask.area || '', predecessors: selTask.predecessors, successors: selTask.successors,
        notes: selTask.notes || '',
      })}]\n\n${msg}`;
    }

    _appendAIMsg('user', msg); // Anzeige ohne Kontext-Prefix
    const assistantDiv = _appendAIMsg('assistant', '');
    assistantDiv.innerHTML = '<span class="spinner"></span> <span class="planner-muted">denkt…</span>';
    _aiStreaming = true;
    // Alte Action-Buttons entfernen
    document.querySelectorAll('.planner-ai-action').forEach(el => el.remove());

    const pid = _planId || 'unsaved';
    const url = `/api/plans/${pid}/ai`;

    try {
      const useWeb = document.getElementById('btn-plan-ai-web')?.classList.contains('active');
      const useRag = document.getElementById('btn-plan-ai-rag')?.classList.contains('active');
      const resp = await fetch(url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ message: contextMsg, model, tasks: _tasks, use_web: useWeb, use_rag: useRag, rag_collections: _currentRag() }),
      });

      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const reader = resp.body.getReader();
      const dec = new TextDecoder();
      let buf = '';
      let text = '';

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buf += dec.decode(value, { stream: true });
        const lines = buf.split('\n');
        buf = lines.pop();
        for (const line of lines) {
          if (!line.startsWith('data: ')) continue;
          try {
            const ev = JSON.parse(line.slice(6));
            if (ev.type === 'text') { text += ev.content; assistantDiv.textContent = text; }
          } catch (_) {}
        }
      }
      // Fertige Antwort als Markdown + LaTeX rendern
      if (text && typeof marked !== 'undefined') {
        if (window._ensureKatexMarked) window._ensureKatexMarked();
        assistantDiv.innerHTML = marked.parse(text, { gfm: true, breaks: true });
        assistantDiv.querySelectorAll('a[href]').forEach(a => { a.target = '_blank'; a.rel = 'noopener noreferrer'; });
      } else if (!text) {
        assistantDiv.textContent = '(keine Antwort – evtl. Modellwechsel, bitte erneut versuchen)';
      }
      // Nach dem Streaming: Aufgaben-JSON in der Antwort suchen
      _aiParsedTasks = _parseTasksFromText(text);
      if (_aiParsedTasks && _aiParsedTasks.length > 0) {
        const actionDiv = document.createElement('div');
        actionDiv.className = 'planner-ai-action';
        const label = selTask
          ? `✓ ${_aiParsedTasks.length} Aufgabe(n) übernehmen (ersetzt [${selTask.id}])`
          : `✓ ${_aiParsedTasks.length} Aufgabe(n) zum Plan hinzufügen`;
        actionDiv.innerHTML = `<button class="export-btn btn-ai-apply-tasks"
          style="font-size:12px;background:var(--accent);color:#fff;margin:4px 0">${label}</button>`;
        const container = document.getElementById('planner-ai-messages');
        container?.appendChild(actionDiv);
        actionDiv.querySelector('.btn-ai-apply-tasks')?.addEventListener('click', _applyAITasks);
        container && (container.scrollTop = container.scrollHeight);
      }
    } catch (e) {
      assistantDiv.textContent = `Fehler: ${e.message}`;
    } finally {
      _aiStreaming = false;
    }
  }

  function _appendAIMsg(role, text) {
    const container = document.getElementById('planner-ai-messages');
    if (!container) return document.createElement('div');
    const div = document.createElement('div');
    div.className = `planner-ai-msg ${role}`;
    div.textContent = text;
    container.appendChild(div);
    container.scrollTop = container.scrollHeight;
    return div;
  }

  /* ── Wissensdatenbanken (Informationsbeschaffung) ────────────────── */
  function _currentRag() {
    const sel = document.getElementById('planner-rag-select');
    if (!sel) return [];
    return Array.from(sel.selectedOptions).map(o => o.value).filter(Boolean);
  }

  async function _fillRagSelect() {
    const sel = document.getElementById('planner-rag-select');
    if (!sel) return;
    const cur = new Set(Array.from(sel.selectedOptions).map(o => o.value));
    try {
      const r = await fetch('/api/rag/collections');
      const cols = await r.json();
      sel.innerHTML = '';
      for (const c of (cols || [])) {
        const o = document.createElement('option');
        o.value = c.id; o.textContent = c.name;
        if (cur.has(c.id)) o.selected = true;
        sel.appendChild(o);
      }
    } catch (_) { /* keine RAG-Liste verfügbar */ }
  }

  /* ── Projekt-Agent: bestehenden wählen / als Agent speichern ──────── */
  function _fillAgentPicker() {
    const sel = document.getElementById('btn-plan-pick-agent');
    if (!sel || typeof AgentManager === 'undefined') return;
    const keep = sel.value;
    const agents = AgentManager.getAgents() || [];
    sel.innerHTML = '<option value="">📂 Agent wählen…</option>';
    for (const a of agents) {
      const o = document.createElement('option');
      o.value = a.id;
      o.textContent = (a.icon ? a.icon + ' ' : '') + (a.name || a.id);
      sel.appendChild(o);
    }
    if (keep) sel.value = keep;
  }

  function _pickAgent(id) {
    if (!id || typeof AgentManager === 'undefined') return;
    const a = (AgentManager.getAgents() || []).find(x => x.id === id);
    if (!a || !a.system_prompt) { showToast('Agent hat keinen System-Prompt'); return; }
    _systemPrompt = a.system_prompt;
    const status = document.getElementById('planner-agent-status');
    if (status) { status.textContent = `✓ Agent: ${a.name || a.id}`; status.title = _systemPrompt; }
    // Die an den Agenten gebundenen Wissensdatenbanken im Planer mitaktivieren
    if (a.rag_collections && a.rag_collections.length) {
      const sel = document.getElementById('planner-rag-select');
      if (sel) Array.from(sel.options).forEach(o => { if (a.rag_collections.includes(o.value)) o.selected = true; });
    }
    showToast('Projekt-Agent gesetzt: ' + (a.name || a.id));
  }

  function _saveAsAgent() {
    if (!_systemPrompt) { showToast('Erst „Projekt-Agent ableiten" oder einen Agenten wählen'); return; }
    if (typeof AgentManager === 'undefined') { showToast('Agenten-Modul nicht verfügbar'); return; }
    const base = (document.getElementById('planner-plan-name')?.value || _desc || 'Projekt').trim().slice(0, 30);
    // Öffnet den Agenten-Editor vorausgefüllt – dort speichern & weiter bearbeiten.
    AgentManager.openModal({
      id: null,
      name: `Planer: ${base}`,
      description: 'Projekt-Agent aus dem Planer',
      system_prompt: _systemPrompt,
      icon: '🗂️',
      category: 'Planer',
      tools: ['web_search', 'calculate'],
      rag_collections: _currentRag(),
    });
  }

  /* ── Durchführbarkeit prüfen ─────────────────────────────────────── */
  async function _checkFeasibility() {
    if (!_tasks.length) { showToast('Kein Plan mit Aufgaben vorhanden'); return; }
    _desc = (document.getElementById('planner-desc')?.value || '').trim();
    const btn = document.getElementById('btn-plan-check');
    if (btn) { btn.disabled = true; btn.textContent = '🔍 prüft…'; }
    _appendAIMsg('user', '🔍 Durchführbarkeit prüfen');
    const div = _appendAIMsg('assistant', '');
    div.innerHTML = '<span class="spinner"></span> <span class="planner-muted">prüft den Plan…</span>';
    document.querySelectorAll('.planner-ai-action').forEach(el => el.remove());
    try {
      const pid = _planId || 'unsaved';
      const r = await fetch(`/api/plans/${pid}/check-feasibility`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ tasks: _tasks, description: _desc, system_prompt: _systemPrompt,
                               model: _model(), rag_collections: _currentRag() }),
      });
      if (!r.ok) { let m = 'HTTP ' + r.status; try { m = (await r.json()).detail || m; } catch (_) {} throw new Error(m); }
      _renderFeasibility(div, await r.json());
    } catch (e) {
      div.textContent = 'Fehler: ' + e.message;
    } finally {
      if (btn) { btn.disabled = false; btn.textContent = '🔍 Durchführbarkeit prüfen'; }
    }
  }

  function _renderFeasibility(div, d) {
    const esc = s => String(s == null ? '' : s).replace(/[&<>]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;' }[c]));
    const itemText = x => {
      if (x == null) return '';
      if (typeof x !== 'object') return String(x);
      // Aufgabe (hat id/duration/predecessors) → „[id] name"
      if (x.name && (x.id || x.duration != null || x.predecessors)) return `[${x.id || 'neu'}] ${x.name}`;
      // generisches Objekt: Kopf + Detail aus den gängigen Feldnamen zusammenbauen
      const head = x.name || x.bereich || x.titel || '';
      const body = x.details || x.maßnahmen || x.massnahmen || x.beschreibung || '';
      if (head || body) return head && body ? `${head}: ${body}` : (head || body);
      return JSON.stringify(x);
    };
    const list = arr => (arr && arr.length)
      ? '<ul style="margin:4px 0 8px 18px">' + arr.map(x => `<li>${esc(itemText(x))}</li>`).join('') + '</ul>' : '';
    const ok = d.durchfuehrbar;
    let html = `<div style="font-weight:600;margin-bottom:4px">${ok ? '✅' : '⚠️'} ${esc(d.bewertung || (ok ? 'Plan erscheint durchführbar' : 'Plan hat offene Punkte'))}</div>`;
    if ((d.fehlende_aufgaben || []).length) html += `<div style="margin-top:6px">🧩 <strong>Fehlende Aufgaben</strong>${list(d.fehlende_aufgaben)}</div>`;
    if ((d.luecken || []).length)          html += `<div>🔗 <strong>Lücken / lose Enden</strong>${list(d.luecken)}</div>`;
    if ((d.struktur || []).length)         html += `<div>🧱 <strong>Struktur</strong>${list(d.struktur)}</div>`;
    if ((d.risiken || []).length)          html += `<div>⚠ <strong>Risiken</strong>${list(d.risiken)}</div>`;
    if ((d.empfehlungen || []).length)     html += `<div>💡 <strong>Empfehlungen</strong>${list(d.empfehlungen)}</div>`;
    div.innerHTML = html;
    // Fehlende Aufgaben übernehmbar machen (hinzufügen, nicht ersetzen)
    if ((d.fehlende_aufgaben || []).length) {
      _aiParsedTasks = d.fehlende_aufgaben;
      const actionDiv = document.createElement('div');
      actionDiv.className = 'planner-ai-action';
      actionDiv.innerHTML = `<button class="export-btn btn-ai-apply-tasks" style="font-size:12px;background:var(--accent);color:#fff;margin:4px 0">✓ ${d.fehlende_aufgaben.length} fehlende Aufgabe(n) hinzufügen</button>`;
      const container = document.getElementById('planner-ai-messages');
      container?.appendChild(actionDiv);
      actionDiv.querySelector('.btn-ai-apply-tasks')?.addEventListener('click', () => { _selectedTaskId = null; _applyAITasks(); });
      container && (container.scrollTop = container.scrollHeight);
    }
  }

  /* ── Fertigen Plan in eine Wissensdatenbank überführen ───────────── */
  async function _planToRag() {
    if (!_tasks.length) { showToast('Kein Plan mit Aufgaben'); return; }
    if (typeof RAG === 'undefined') { showToast('RAG-Modul nicht verfügbar'); return; }
    const name = (document.getElementById('planner-plan-name')?.value || 'Plan').trim();
    _desc = (document.getElementById('planner-desc')?.value || '').trim();
    const lines = [`# Projektplan: ${name}`, ''];
    if (_desc) lines.push('## Projektbeschreibung & Ziel', _desc, '');
    if (_systemPrompt) lines.push('## Projekt-Agent', _systemPrompt, '');
    lines.push('## Aufgaben', '');
    for (const t of _tasks) {
      lines.push(`### [${t.id}] ${t.name || ''}`);
      if (t.duration != null) lines.push(`- Dauer: ${t.duration} Tage`);
      if ((t.predecessors || []).length) lines.push(`- Vorgänger: ${t.predecessors.join(', ')}`);
      if ((t.successors || []).length)   lines.push(`- Nachfolger: ${t.successors.join(', ')}`);
      if (t.area)  lines.push(`- Bereich: ${t.area}`);
      if ((t.resource_list || []).length) lines.push(`- Ressourcen: ${t.resource_list.map(r => `${r.name || ''} (${r.kind || ''})`).join(', ')}`);
      if (t.notes) lines.push(`- Notizen: ${t.notes}`);
      if (t.doc)   lines.push('', '#### Recherche-Dossier', t.doc);
      lines.push('');
    }
    const text = lines.join('\n');
    const useNew = confirm('Plan in eine NEUE Wissensdatenbank überführen?\n\nOK = neue anlegen · Abbrechen = bestehende wählen');
    if (useNew) {
      const nm = (prompt('Name der neuen Wissensdatenbank:', `Plan: ${name}`) || '').trim();
      if (!nm) return;
      showToast('⏳ Wird angelegt & eingebettet…');
      try {
        const cr = await fetch('/api/rag/collections', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ name: nm, strictness: 'korrekt' }),
        });
        const coll = await cr.json();
        if (!cr.ok) throw new Error(coll.detail || cr.status);
        const ir = await fetch(`/api/rag/collections/${coll.id}/from-text`, {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ title: `Projektplan: ${name}`, text }),
        });
        const res = await ir.json();
        if (!ir.ok) throw new Error(res.detail || ir.status);
        showToast(`✓ „${nm}" angelegt: ${res.n_chunks} Chunks`);
        _fillRagSelect();
      } catch (e) { showToast('Fehler: ' + e.message); }
    } else {
      await RAG.ingestText(`Projektplan: ${name}`, text, `Plan „${name}" in Wissensdatenbank übernehmen`);
      _fillRagSelect();
    }
  }

  /* ── Selektions-Indikator ────────────────────────────────────────── */
  function _updateSelectedInfo() {
    const el = document.getElementById('planner-selected-info');
    if (!el) return;
    const t = _selectedTaskId ? _tasks.find(t => t.id === _selectedTaskId) : null;
    if (t) {
      el.style.display = '';
      el.innerHTML = `📌 Ausgewählt: <strong>[${escHtml(t.id)}] ${escHtml(t.name)}</strong>
        <button id="btn-deselect-task" style="font-size:10px;padding:1px 5px;margin-left:6px" class="export-btn">✕</button>`;
      el.querySelector('#btn-deselect-task')?.addEventListener('click', () => {
        _selectedTaskId = null;
        const tbody = document.getElementById('task-tbody');
        tbody?.querySelectorAll('tr[data-i]').forEach(r => r.classList.remove('task-row-selected'));
        _updateSelectedInfo();
      });
    } else {
      el.style.display = 'none';
    }
  }

  /* ── KI-Antwort: Aufgaben parsen & übernehmen ───────────────────── */
  function _parseTasksFromText(text) {
    // Balanced-bracket extractor: findet vollständige [...] und {...} ohne greedy-Backtracking
    function _extractBalanced(str, open, close) {
      const result = [];
      for (let i = 0; i < str.length; i++) {
        if (str[i] !== open) continue;
        let depth = 0, inStr = false, escape = false;
        let j = i;
        for (; j < str.length; j++) {
          const c = str[j];
          if (escape) { escape = false; continue; }
          if (c === '\\' && inStr) { escape = true; continue; }
          if (c === '"') { inStr = !inStr; continue; }
          if (inStr) continue;
          if (c === open) depth++;
          else if (c === close) { depth--; if (depth === 0) break; }
        }
        if (depth === 0) { result.push(str.slice(i, j + 1)); i = j; }
      }
      return result;
    }

    const _tryParse = s => {
      try {
        const p = JSON.parse(s.trim());
        if (Array.isArray(p) && p.length > 0 && p[0] && p[0].name) return p;
        if (p && Array.isArray(p.tasks) && p.tasks.length > 0) return p.tasks;
      } catch (_) {}
      return null;
    };

    // 1. Code-Fences zuerst (zuverlässigste Quelle)
    const fenceRe = /```(?:json)?\s*([\s\S]*?)```/g;
    let m;
    while ((m = fenceRe.exec(text)) !== null) {
      const r = _tryParse(m[1]); if (r) return r;
    }
    // 2. Balancierte Arrays und Objekte
    for (const s of _extractBalanced(text, '[', ']')) { const r = _tryParse(s); if (r) return r; }
    for (const s of _extractBalanced(text, '{', '}')) { const r = _tryParse(s); if (r) return r; }
    return null;
  }

  function _applyAITasks() {
    if (!_aiParsedTasks || !_aiParsedTasks.length) return;
    const selTask = _selectedTaskId ? _tasks.find(t => t.id === _selectedTaskId) : null;

    // ID-Kollisionen auflösen: KI-IDs die schon im Plan vorhanden sind → eindeutig machen
    // Baue zuerst eine ID-Mapping-Tabelle (alte KI-ID → sichere Plan-ID)
    const existingIds = new Set(_tasks
      .filter(t => t.id !== _selectedTaskId)  // die zu ersetzende ID wird entfernt
      .map(t => t.id));
    const idMap = {};
    const takenIds = new Set(existingIds);
    for (const t of _aiParsedTasks) {
      const wantedId = String(t.id || '').trim();
      if (!wantedId || takenIds.has(wantedId)) {
        let n = _tasks.length + 1;
        let safe; do { safe = 'T' + n++; } while (takenIds.has(safe));
        idMap[wantedId] = safe; takenIds.add(safe);
      } else {
        idMap[wantedId] = wantedId; takenIds.add(wantedId);
      }
    }

    const normalize = (t, i) => {
      const rawId = String(t.id || '').trim();
      const safeId = idMap[rawId] || _uniqueTaskId();
      return {
        id:            safeId,
        name:          t.name      || `KI-Aufgabe ${i + 1}`,
        duration:      typeof t.duration === 'number' ? t.duration : (parseFloat(t.duration) || 1),
        predecessors:  Array.isArray(t.predecessors) ? t.predecessors.map(p => idMap[String(p)] || String(p)) : [],
        successors:    Array.isArray(t.successors)   ? t.successors.map(s => idMap[String(s)] || String(s))   : [],
        resources:     '',
        resource_list: Array.isArray(t.resource_list) ? t.resource_list : [],
        notes:         t.notes  || '',
        area:          t.area   || selTask?.area || '',
        is_start:      false,
        is_end:        false,
      };
    };

    const newTasks = _aiParsedTasks.map(normalize);

    if (selTask) {
      const selIdx = _tasks.findIndex(t => t.id === _selectedTaskId);
      const firstId = newTasks[0].id;
      const lastId  = newTasks[newTasks.length - 1].id;
      // Erste neue Aufgabe erbt Vorgänger der selektierten (falls KI keine gesetzt hat)
      if (!newTasks[0].predecessors.length) newTasks[0].predecessors = [...(selTask.predecessors || [])];
      // Letzte erbt Nachfolger
      if (!newTasks[newTasks.length-1].successors.length)
        newTasks[newTasks.length-1].successors = [...(selTask.successors || [])];
      // Aufgaben des Plans, die die alte ID referenzieren → auf neue IDs umleiten
      for (const t of _tasks) {
        if (t.id === _selectedTaskId) continue;
        t.predecessors = (t.predecessors||[]).map(p => p === _selectedTaskId ? firstId : p);
        t.successors   = (t.successors  ||[]).map(s => s === _selectedTaskId ? lastId  : s);
      }
      _tasks.splice(selIdx, 1, ...newTasks);
      _selectedTaskId = null;
    } else {
      _tasks.push(...newTasks);
    }

    _aiParsedTasks = null;
    _normalizeLinks();
    _recalcAndRender();
    _updateSelectedInfo();
    showToast(`✓ ${newTasks.length} Aufgabe(n) übernommen`);
    // Action-Buttons entfernen
    document.querySelectorAll('.planner-ai-action').forEach(el => el.remove());
  }

  /* ── Plan-Vergleich & Bewertung ─────────────────────────────────── */
  let _evalImportPlan = null;  // verbesserter Plan (JSON) zum Import

  async function _openEvalModal() {
    // Plan-Selects befüllen
    const plans = await (await fetch('/api/plans')).json();
    [1,2,3].forEach(i => {
      const sel = document.getElementById(`plan-eval-sel-${i}`);
      if (!sel) return;
      if (i > 1) sel.innerHTML = '<option value="">— keiner —</option>';
      else sel.innerHTML = '';
      for (const p of plans) {
        const o = document.createElement('option');
        o.value = p.id; o.textContent = p.name || p.id;
        sel.appendChild(o);
      }
      if (i === 1 && _planId) sel.value = _planId;
    });
    document.getElementById('plan-eval-output').textContent = '';
    document.getElementById('plan-eval-status').textContent = '';
    document.getElementById('btn-plan-eval-import').style.display = 'none';
    _evalImportPlan = null;
    document.getElementById('plan-eval-overlay')?.classList.add('active');
  }

  async function _runEvaluation() {
    const ids = [1,2,3].map(i => document.getElementById(`plan-eval-sel-${i}`)?.value).filter(Boolean);
    if (!ids.length) { showToast('Mindestens Plan 1 auswählen'); return; }
    const model = _model();
    const status = document.getElementById('plan-eval-status');
    const output = document.getElementById('plan-eval-output');
    const btn = document.getElementById('btn-plan-eval-run');
    btn.disabled = true; status.textContent = '⏳ Analyse läuft…'; output.textContent = '';
    document.getElementById('btn-plan-eval-import').style.display = 'none';
    try {
      const resp = await fetch('/api/plans/evaluate', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ plan_ids: ids, model }),
      });
      if (!resp.ok || !resp.body) throw new Error(`HTTP ${resp.status}`);
      const reader = resp.body.getReader();
      const dec = new TextDecoder();
      let buf = '', text = '';
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buf += dec.decode(value, { stream: true });
        const lines = buf.split('\n'); buf = lines.pop();
        for (const line of lines) {
          if (!line.startsWith('data:')) continue;
          try {
            const ev = JSON.parse(line.slice(5).trim());
            if (ev.type === 'text') { text += ev.content; output.textContent = text; output.scrollTop = output.scrollHeight; }
            else if (ev.type === 'plan') {
              _evalImportPlan = ev.plan;
              document.getElementById('btn-plan-eval-import').style.display = '';
              status.textContent = '✓ Verbesserter Plan bereit zum Laden';
            }
            else if (ev.type === 'done') status.textContent = '✓ Analyse abgeschlossen';
          } catch (_) {}
        }
      }
      if (window._ensureKatexMarked && typeof marked !== 'undefined') {
        window._ensureKatexMarked();
        output.innerHTML = marked.parse(text, { gfm: true, breaks: true });
      }
    } catch (e) { status.textContent = 'Fehler: ' + e.message; }
    finally { btn.disabled = false; }
  }

  async function _importEvalPlan() {
    if (!_evalImportPlan) return;
    if (!confirm('Den verbesserten Plan als neuen Plan laden? Der aktuelle Plan bleibt erhalten.')) return;
    try {
      const r = await fetch('/api/plans', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(_evalImportPlan),
      });
      const p = await r.json();
      await _loadPlanList();
      await _loadPlan(p.id);
      document.getElementById('plan-eval-overlay')?.classList.remove('active');
      showToast(`✓ Verbesserter Plan geladen: ${_evalImportPlan.name || p.id}`);
    } catch (e) { showToast('Fehler: ' + e.message); }
  }

  /* ── Plan aus flacher Aufgabenliste generieren ───────────────────── */
  let _fromListPlan = null;

  function _openFromListModal() {
    document.getElementById('plan-from-list-overlay')?.classList.add('active');
    const statusEl = document.getElementById('plan-from-list-status');
    if (statusEl) statusEl.textContent = '';
    const loadBtn = document.getElementById('btn-from-list-load');
    if (loadBtn) loadBtn.style.display = 'none';
    _fromListPlan = null;
  }

  async function _generateFromList() {
    const textarea = document.getElementById('plan-from-list-text');
    const nameInp  = document.getElementById('plan-from-list-name');
    const taskList = (textarea?.value || '').trim();
    if (!taskList) { showToast('Bitte Aufgabenliste einfügen'); return; }
    const model = _model() || '';
    const name  = (nameInp?.value || '').trim() || 'Projekt aus Liste';
    const statusEl = document.getElementById('plan-from-list-status');
    const btn = document.getElementById('btn-from-list-run');
    if (statusEl) statusEl.textContent = '⏳ Generiere Plan…';
    if (btn) btn.disabled = true;
    _fromListPlan = null;
    document.getElementById('btn-from-list-load').style.display = 'none';

    try {
      const resp = await fetch('/api/plans/from-list', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ task_list: taskList, name, model }),
      });
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const reader = resp.body.getReader();
      const dec = new TextDecoder();
      let buf = '';
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buf += dec.decode(value, { stream: true });
        const lines = buf.split('\n'); buf = lines.pop();
        for (const line of lines) {
          if (!line.startsWith('data: ')) continue;
          try {
            const ev = JSON.parse(line.slice(6));
            if (ev.type === 'status' && statusEl) {
              statusEl.textContent = '⏳ ' + ev.message;
            } else if (ev.type === 'plan') {
              _fromListPlan = ev.plan;
              if (statusEl) statusEl.textContent = `✓ Plan mit ${ev.plan.tasks?.length || 0} Aufgaben generiert`;
              const loadBtn = document.getElementById('btn-from-list-load');
              if (loadBtn) loadBtn.style.display = '';
            } else if (ev.type === 'error') {
              if (statusEl) statusEl.textContent = '❌ ' + ev.message;
            }
          } catch (_) {}
        }
      }
    } catch (e) {
      if (statusEl) statusEl.textContent = '❌ Fehler: ' + e.message;
    } finally {
      if (btn) btn.disabled = false;
    }
  }

  function _loadFromListPlan() {
    if (!_fromListPlan) return;
    const p = _fromListPlan;
    _tasks = p.tasks || [];
    _desc  = p.description || '';
    _systemPrompt = '';
    _planId = null;
    _startDate = ''; _endDate = '';
    const sdEl = document.getElementById('planner-start-date');
    const edEl = document.getElementById('planner-end-date');
    if (sdEl) sdEl.value = '';
    if (edEl) edEl.value = '';
    const nameEl = document.getElementById('planner-plan-name');
    if (nameEl) nameEl.value = p.name || '';
    const descEl = document.getElementById('planner-desc');
    if (descEl) descEl.value = _desc;
    document.getElementById('btn-delete-plan').style.display = 'none';
    _normalizeLinks();
    _recalcAndRender();
    document.getElementById('plan-from-list-overlay')?.classList.remove('active');
    showToast(`✓ Plan „${p.name}" geladen – ${_tasks.length} Aufgaben`);
  }

  /* ── Automatisch strukturieren (intelligentes Verknüpfen) ────────── */
  let _autoStructResult = null;   // {links, stats}
  let _autoStructSnapshot = null; // JSON-Klon von _tasks vor dem Anwenden

  function _openAutoStructure() {
    if (!_tasks.length) { showToast('Keine Aufgaben zum Strukturieren'); return; }
    document.getElementById('auto-structure-overlay')?.classList.add('active');
    const statusEl  = document.getElementById('auto-struct-status');
    const previewEl = document.getElementById('auto-struct-preview');
    if (statusEl)  statusEl.textContent = '';
    if (previewEl) previewEl.innerHTML = '';
    document.getElementById('btn-auto-struct-apply').style.display = 'none';
    // „Rückgängig" nur anbieten, solange ein Snapshot vorliegt
    document.getElementById('btn-auto-struct-undo').style.display = _autoStructSnapshot ? '' : 'none';
    _autoStructResult = null;
  }

  // Menschliche Rollen einer Aufgabe (für Ressourcen-Entzerrung)
  function _taskRoles(t) {
    const out = [];
    for (const r of (t.resource_list || [])) {
      if ((r.kind || 'human') === 'human' && r.name) out.push(r.name);
    }
    if (!out.length && t.area) out.push(t.area);
    return out;
  }

  async function _computeAutoStructure() {
    if (!_tasks.length) { showToast('Keine Aufgaben'); return; }
    const opts = {
      dependencies:     document.getElementById('auto-struct-deps')?.checked !== false,
      phases:           document.getElementById('auto-struct-phases')?.checked !== false,
      resource_leveling: document.getElementById('auto-struct-leveling')?.checked !== false,
    };
    if (!opts.dependencies && !opts.phases && !opts.resource_leveling) {
      showToast('Bitte mindestens eine Option wählen'); return;
    }
    const statusEl  = document.getElementById('auto-struct-status');
    const previewEl = document.getElementById('auto-struct-preview');
    const runBtn    = document.getElementById('btn-auto-struct-run');
    if (statusEl) statusEl.textContent = '⏳ KI analysiert Aufgaben…';
    if (runBtn) runBtn.disabled = true;
    document.getElementById('btn-auto-struct-apply').style.display = 'none';
    _autoStructResult = null;

    const tasks = _tasks.map(t => ({
      id: t.id,
      name: t.name || '',
      area: t.area || '',
      duration: Number(t.duration) || 1,
      roles: _taskRoles(t),
    }));

    try {
      const resp = await fetch('/api/plans/auto-structure', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ tasks, options: opts, description: _desc || '', model: _model() || '' }),
      });
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const data = await resp.json();
      _autoStructResult = data;
      _renderAutoStructPreview(data);
      if (statusEl) statusEl.textContent = '✓ Vorschau bereit';
      document.getElementById('btn-auto-struct-apply').style.display = '';
    } catch (e) {
      if (statusEl) statusEl.textContent = '❌ Fehler: ' + e.message;
    } finally {
      if (runBtn) runBtn.disabled = false;
    }
  }

  function _renderAutoStructPreview(data) {
    const previewEl = document.getElementById('auto-struct-preview');
    if (!previewEl) return;
    const st = data.stats || {};
    const byId = {};
    for (const t of _tasks) byId[t.id] = t;
    // Phasen gruppieren
    const phaseMap = {};
    for (const l of (data.links || [])) {
      const ph = l.area || '—';
      (phaseMap[ph] = phaseMap[ph] || []).push(l);
    }
    let html = `<div style="display:flex;gap:14px;flex-wrap:wrap;margin-bottom:12px">
      <span style="background:var(--bg-input);border-radius:6px;padding:4px 10px"><strong>${st.tasks ?? _tasks.length}</strong> Aufgaben</span>
      <span style="background:var(--bg-input);border-radius:6px;padding:4px 10px">🔗 <strong>${st.dep_links ?? 0}</strong> fachliche Abhängigkeiten</span>
      <span style="background:var(--bg-input);border-radius:6px;padding:4px 10px">⛓ <strong>${st.leveled_links ?? 0}</strong> Ressourcen-Entzerrungen</span>
      <span style="background:var(--bg-input);border-radius:6px;padding:4px 10px">📑 <strong>${st.phases ?? 0}</strong> Phasen</span>
    </div>`;
    html += `<table style="width:100%;border-collapse:collapse;font-size:12px">
      <thead><tr style="text-align:left;border-bottom:1px solid var(--border)">
        <th style="padding:4px 6px">Phase</th><th style="padding:4px 6px">Aufgabe</th><th style="padding:4px 6px">hängt ab von</th></tr></thead><tbody>`;
    for (const ph of Object.keys(phaseMap)) {
      const rows = phaseMap[ph];
      rows.forEach((l, i) => {
        const t = byId[l.id];
        const preds = (l.predecessors || []).map(p => (byId[p]?.name ? `${p} (${byId[p].name})` : p)).join(', ') || '—';
        html += `<tr style="border-bottom:1px solid var(--border)">
          <td style="padding:3px 6px;color:var(--text-muted)">${i === 0 ? _esc(ph) : ''}</td>
          <td style="padding:3px 6px">${_esc(l.id)} · ${_esc(t?.name || '')}</td>
          <td style="padding:3px 6px;color:var(--text-muted)">${_esc(preds)}</td></tr>`;
      });
    }
    html += '</tbody></table>';
    html += `<p style="font-size:12px;color:var(--text-muted);margin-top:10px">
      „Anwenden" ersetzt die bestehenden Verknüpfungen und Phasen dieser Aufgaben. Du kannst es mit „Rückgängig" zurücknehmen.</p>`;
    previewEl.innerHTML = html;
  }

  function _esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"]/g, c => ({ '&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;' }[c]));
  }

  function _applyAutoStructure() {
    if (!_autoStructResult || !Array.isArray(_autoStructResult.links)) { showToast('Erst Vorschau berechnen'); return; }
    // Snapshot für Rückgängig
    _autoStructSnapshot = JSON.parse(JSON.stringify(_tasks));
    const byId = {};
    for (const t of _tasks) byId[t.id] = t;
    for (const l of _autoStructResult.links) {
      const t = byId[l.id];
      if (!t) continue;
      t.predecessors = Array.isArray(l.predecessors) ? l.predecessors.filter(p => byId[p] && p !== l.id) : [];
      t.successors = [];
      if (l.area) t.area = l.area;
    }
    // Nachfolger aus Vorgängern symmetrisch rekonstruieren
    _normalizeLinks();
    _computeCPM();
    _tasks.sort((a, b) => (_rank[a.id] || 0) - (_rank[b.id] || 0));
    _recalcAndRender();
    _fitView();
    document.getElementById('btn-auto-struct-undo').style.display = '';
    document.getElementById('auto-structure-overlay')?.classList.remove('active');
    const st = _autoStructResult.stats || {};
    showToast(`🔗 Strukturiert: ${st.dep_links ?? 0} Abhängigkeiten, ${st.leveled_links ?? 0} Entzerrungen, ${st.phases ?? 0} Phasen`);
  }

  function _undoAutoStructure() {
    if (!_autoStructSnapshot) { showToast('Nichts rückgängig zu machen'); return; }
    _tasks = _autoStructSnapshot;
    _autoStructSnapshot = null;
    _normalizeLinks();
    _recalcAndRender();
    _fitView();
    document.getElementById('btn-auto-struct-undo').style.display = 'none';
    document.getElementById('auto-structure-overlay')?.classList.remove('active');
    showToast('↩ Auto-Strukturierung zurückgenommen');
  }

  /* ── Projekt-Agent ableiten ──────────────────────────────────────── */
  async function _deriveAgent() {
    const desc = (document.getElementById('planner-desc')?.value || '').trim();
    if (!desc) { showToast('Bitte zuerst Projektbeschreibung & Ziel eingeben'); return; }
    _desc = desc;
    const btn = document.getElementById('btn-derive-agent');
    const status = document.getElementById('planner-agent-status');
    if (btn) { btn.disabled = true; btn.textContent = '🧠 wird abgeleitet…'; }
    try {
      const model = _model();
      const r = await fetch('/api/plans/derive-agent', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ description: desc, model }),
      });
      if (!r.ok) throw new Error('HTTP ' + r.status);
      const d = await r.json();
      _systemPrompt = d.system_prompt || '';
      if (status) { status.textContent = `✓ Agent: ${d.agent_name || 'Projektplaner'}`; status.title = _systemPrompt; }
      showToast('Projekt-Agent abgeleitet – steuert jetzt die KI-Vorschläge');
    } catch (e) {
      showToast('Ableitung fehlgeschlagen: ' + e.message);
    } finally {
      if (btn) { btn.disabled = false; btn.textContent = '🧠 Projekt-Agent ableiten'; }
    }
  }

  /* ── Komplettes Projekt vom LLM generieren ───────────────────────── */
  async function _generatePlan() {
    const desc = (document.getElementById('planner-desc')?.value || '').trim();
    if (!desc) { showToast('Bitte zuerst Projektbeschreibung & Ziel eingeben'); return; }
    let count = parseInt(document.getElementById('planner-task-count')?.value, 10);
    if (!Number.isFinite(count)) count = 12;
    count = Math.max(5, Math.min(count, 200));
    // Vorwarnung bei großen Plänen: kleine lokale Modelle stoßen an ihre Grenzen
    if (count > 30 && !confirm(
        `${count} Aufgaben angefordert.\n\nKleine lokale Modelle (z. B. ministral-3:3b) ` +
        `liefern bei so großen Plänen oft unvollständige oder inkonsistente Ergebnisse. ` +
        `Empfehlung: größeres/leistungsfähigeres Modell wählen oder den Plan in Phasen ` +
        `generieren.\n\nTrotzdem fortfahren?`)) return;
    if (_tasks.length && !confirm('Bestehende Aufgaben durch ein neu generiertes Projekt ersetzen?')) return;
    _desc = desc;
    const btn = document.getElementById('btn-generate-plan');
    const model = _model();
    if (btn) { btn.disabled = true; btn.textContent = '🪄 generiert…'; }
    showToast(`🪄 KI erstellt den Projektplan (${count} Aufgaben)… ${count > 30 ? 'das kann dauern' : ''}`);
    try {
      const r = await fetch('/api/plans/generate', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ description: desc, system_prompt: _systemPrompt, model,
                               resource_catalog: _catalog, resource_mode: _resMode, max_tasks: count,
                               rag_collections: _currentRag() }),
      });
      if (!r.ok) {
        let msg = 'HTTP ' + r.status;
        try { msg = (await r.json()).detail || msg; } catch (_) {}
        throw new Error(msg);
      }
      const d = await r.json();
      if (!(d.tasks || []).length) throw new Error('Kein gültiger Plan erhalten');
      _tasks = d.tasks;
      _recalcAndRender();
      showToast(`✓ Projekt generiert: ${_tasks.length} Aufgaben`);
      if (d.warning) {
        const status = document.getElementById('planner-agent-status');
        if (status) { status.textContent = `⚠ ${d.warning}`; status.title = d.warning; }
        showToast('⚠ ' + d.warning);
      }
    } catch (e) {
      showToast('Generierung fehlgeschlagen: ' + e.message);
    } finally {
      if (btn) { btn.disabled = false; btn.textContent = '🪄 KI-Projekt generieren'; }
    }
  }

  /* ── Dokument → Plan: Datei importieren, Ressourcen ableiten, Plan bauen ── */
  async function _importDocPlan(file) {
    if (!file) return;
    let count = parseInt(document.getElementById('planner-task-count')?.value, 10);
    if (!Number.isFinite(count)) count = 12;
    count = Math.max(5, Math.min(count, 300));
    if (count > 30 && !confirm(
        `${count} Aufgaben angefordert.\n\nKleine lokale Modelle liefern bei so großen Plänen ` +
        `oft unvollständige Ergebnisse – auf einem leistungsfähigen Rechner mit großem Modell ` +
        `ist das jedoch kein Problem.\n\nFortfahren?`)) return;
    if (_tasks.length && !confirm('Bestehende Aufgaben durch den aus dem Dokument abgeleiteten Plan ersetzen?')) return;

    const fd = new FormData();
    fd.append('file', file);
    fd.append('max_tasks', String(count));
    const model = _model();
    if (model) fd.append('model', model);
    fd.append('resource_mode', _resMode || 'free');
    fd.append('rag_collections', (_currentRag() || []).join(','));

    const btn = document.getElementById('btn-plan-from-doc');
    if (btn) { btn.disabled = true; btn.textContent = '📄 liest…'; }
    showToast(`📄 „${file.name}" wird gelesen, Ressourcen & Plan werden abgeleitet (${count} Aufgaben)… ${count > 30 ? 'das kann dauern' : ''}`);
    try {
      const r = await fetch('/api/plans/from-document', { method: 'POST', body: fd });
      if (!r.ok) {
        let msg = 'HTTP ' + r.status;
        try { msg = (await r.json()).detail || msg; } catch (_) {}
        throw new Error(msg);
      }
      const d = await r.json();
      if (!(d.tasks || []).length) throw new Error('Kein gültiger Plan erhalten');
      _planId = null;
      _tasks = d.tasks;
      if (d.name) {
        const nameEl = document.getElementById('planner-plan-name'); if (nameEl) nameEl.value = d.name;
      }
      if (d.description) {
        _desc = d.description;
        const descEl = document.getElementById('planner-desc'); if (descEl) descEl.value = d.description;
      }
      const sel = document.getElementById('planner-plan-select'); if (sel) sel.value = '';
      const delBtn = document.getElementById('btn-delete-plan'); if (delBtn) delBtn.style.display = 'none';
      _recalcAndRender();
      showToast(`✓ Plan aus „${d.source_document || file.name}" abgeleitet: ${_tasks.length} Aufgaben`);
      if (d.warning) {
        const status = document.getElementById('planner-agent-status');
        if (status) { status.textContent = `⚠ ${d.warning}`; status.title = d.warning; }
        showToast('⚠ ' + d.warning);
      }
    } catch (e) {
      showToast('Import fehlgeschlagen: ' + e.message);
    } finally {
      if (btn) { btn.disabled = false; btn.textContent = '📄 Dokument → Plan'; }
    }
  }

  /* ── Vorschläge für Vorgänger/Nachfolger ─────────────────────────── */
  async function _suggestFor(idx) {
    const anchor = _tasks[idx];
    if (!anchor) return;
    _desc = (document.getElementById('planner-desc')?.value || '').trim();
    const model = _model();
    showToast('✨ KI sucht Vorgänger/Nachfolger…');
    try {
      const r = await fetch('/api/plans/suggest-tasks', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ system_prompt: _systemPrompt, description: _desc, tasks: _tasks, anchor, model,
                               resource_catalog: _catalog, resource_mode: _resMode }),
      });
      if (!r.ok) throw new Error('HTTP ' + r.status);
      const d = await r.json();
      if (!(d.predecessors || []).length && !(d.successors || []).length) {
        showToast('Keine Vorschläge erhalten'); return;
      }
      _suggestData = { anchorIdx: idx, predecessors: d.predecessors || [], successors: d.successors || [] };
      _renderSuggestModal(anchor);
    } catch (e) {
      showToast('Vorschläge fehlgeschlagen: ' + e.message);
    }
  }

  const _KIND_ICON = { human: '👤', hardware: '🔧', software: '💻' };

  function _resSummary(resList) {
    if (!resList || !resList.length) return 'keine Ressourcen';
    return resList.map(r => `${_KIND_ICON[r.kind] || ''} ${r.name}`).join(', ');
  }

  function _renderSuggestModal(anchor) {
    document.getElementById('suggest-modal-title').textContent = `✨ Vorschläge für „${anchor.name}“`;
    const body = document.getElementById('suggest-modal-body');
    const group = (title, items, type) => {
      if (!items.length) return '';
      let h = `<div class="suggest-group-title">${title}</div>`;
      items.forEach((c, i) => {
        const cost = (c.resources || []).reduce((s, r) => s + _resCost(r), 0);
        h += `<label class="suggest-item">
          <input type="checkbox" data-type="${type}" data-i="${i}" />
          <span><strong>${escHtml(c.name)}</strong> · ${c.duration} d
          <br><span class="planner-muted">${escHtml(_resSummary(c.resources))}${cost > 0 ? ' · ' + _fmtEur(cost) : ''}</span></span>
        </label>`;
      });
      return h;
    };
    body.innerHTML =
      group('⬅ Mögliche Vorgänger', _suggestData.predecessors, 'pred') +
      group('➡ Mögliche Nachfolger', _suggestData.successors, 'succ');
    document.getElementById('suggest-modal-overlay').classList.add('active');
  }

  function _applySuggestions() {
    if (!_suggestData) return;
    const anchor = _tasks[_suggestData.anchorIdx];
    const boxes = document.querySelectorAll('#suggest-modal-body input[type=checkbox]:checked');
    let added = 0;
    boxes.forEach(b => {
      const type = b.dataset.type, i = +b.dataset.i;
      const c = (type === 'pred' ? _suggestData.predecessors : _suggestData.successors)[i];
      if (!c) return;
      const newId = _uniqueTaskId();
      const newTask = {
        id: newId, name: c.name, duration: c.duration,
        predecessors: [], successors: [], resources: '',
        resource_list: c.resources || [], notes: '',
      };
      if (type === 'pred') {
        newTask.successors = [anchor.id];
        anchor.predecessors = [...(anchor.predecessors || []), newId];
      } else {
        newTask.predecessors = [anchor.id];
        anchor.successors = [...(anchor.successors || []), newId];
      }
      _tasks.push(newTask);
      added++;
    });
    _closeSuggestModal();
    _recalcAndRender();
    showToast(added ? `✓ ${added} Aufgabe(n) übernommen & verknüpft` : 'Nichts ausgewählt');
  }

  function _closeSuggestModal() {
    document.getElementById('suggest-modal-overlay').classList.remove('active');
    _suggestData = null;
  }

  /* ── Aufgabe detaillieren (editierbare KI-Vorschläge) ────────────── */
  async function _detailTask(idx) {
    const task = _tasks[idx];
    if (!task) return;
    _desc = (document.getElementById('planner-desc')?.value || '').trim();
    const model = _model();
    showToast('📝 KI detailliert die Aufgabe…');
    try {
      const r = await fetch('/api/plans/detail-task', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ task, tasks: _tasks, description: _desc, system_prompt: _systemPrompt,
                               model, resource_catalog: _catalog, resource_mode: _resMode }),
      });
      if (!r.ok) throw new Error('HTTP ' + r.status);
      const d = await r.json();
      _detailData = { idx, detail: d.detail || {}, preds: d.predecessors || [], succs: d.successors || [] };
      _renderDetailModal(task);
    } catch (e) {
      showToast('Detaillierung fehlgeschlagen: ' + e.message);
    }
  }

  function _candidateRows(items, type) {
    if (!items.length) return '<div class="planner-muted" style="padding:2px 0 6px">— keine —</div>';
    return items.map((c, i) => {
      const cost = (c.resources || []).reduce((s, r) => s + _resCost(r), 0);
      return `<div class="detail-cand">
        <input type="checkbox" data-type="${type}" data-i="${i}" checked />
        <input class="detail-cand-name" data-type="${type}" data-i="${i}" value="${escHtml(c.name)}" />
        <input class="detail-cand-dur" type="number" min="0" data-type="${type}" data-i="${i}" value="${c.duration}" title="Dauer (Tage)" />
        <span class="planner-muted detail-cand-res">${escHtml(_resSummary(c.resources))}${cost > 0 ? ' · ' + _fmtEur(cost) : ''}</span>
      </div>`;
    }).join('');
  }

  function _renderDetailModal(task) {
    document.getElementById('detail-modal-title').textContent = `📝 Aufgabe detaillieren: „${task.name}“`;
    const d = _detailData.detail;
    const hasRes = (d.resources || []).length;
    const taskHadRes = (task.resource_list || []).length;
    const resCost = (d.resources || []).reduce((s, r) => s + _resCost(r), 0);
    const body = document.getElementById('detail-modal-body');
    body.innerHTML = `
      <div class="detail-section">
        <div class="suggest-group-title">Aufgaben-Details</div>
        <label class="detail-field"><span>Name</span>
          <input id="detail-name" value="${escHtml(d.name || task.name || '')}" /></label>
        <label class="detail-field"><span>Dauer (Tage)</span>
          <input id="detail-dur" type="number" min="0" value="${d.duration ?? task.duration ?? 1}" /></label>
        <label class="detail-field"><span>Detail / Notiz</span>
          <textarea id="detail-notes" rows="2">${escHtml(d.notes || task.notes || '')}</textarea></label>
        ${hasRes ? `<label class="detail-check">
          <input type="checkbox" id="detail-res-apply" ${taskHadRes ? '' : 'checked'} />
          <span>Vorgeschlagene Ressourcen übernehmen${taskHadRes ? ' (ersetzt vorhandene)' : ''}:
          <em class="planner-muted">${escHtml(_resSummary(d.resources))}${resCost > 0 ? ' · ' + _fmtEur(resCost) : ''}</em></span>
        </label>` : ''}
      </div>
      <div class="detail-section">
        <div class="suggest-group-title">⬅ Mögliche Vorgänger</div>
        ${_candidateRows(_detailData.preds, 'pred')}
      </div>
      <div class="detail-section">
        <div class="suggest-group-title">➡ Mögliche Nachfolger</div>
        ${_candidateRows(_detailData.succs, 'succ')}
      </div>`;
    document.getElementById('detail-modal-overlay').classList.add('active');
  }

  function _applyDetail() {
    if (!_detailData) return;
    const task = _tasks[_detailData.idx];
    if (!task) { _closeDetailModal(); return; }

    // Aufgaben-Details übernehmen
    const nm = document.getElementById('detail-name')?.value.trim();
    if (nm) task.name = nm;
    const dur = Number(document.getElementById('detail-dur')?.value);
    if (!Number.isNaN(dur)) task.duration = dur;
    task.notes = document.getElementById('detail-notes')?.value.trim() || '';
    if (document.getElementById('detail-res-apply')?.checked) {
      task.resource_list = _detailData.detail.resources || [];
    }

    // Ausgewählte (editierte) Vorgänger/Nachfolger anlegen und verknüpfen
    let added = 0;
    document.querySelectorAll('#detail-modal-body input[type=checkbox][data-type]:checked').forEach(b => {
      const type = b.dataset.type, i = +b.dataset.i;
      const cand = (type === 'pred' ? _detailData.preds : _detailData.succs)[i];
      if (!cand) return;
      const nameEl = document.querySelector(`.detail-cand-name[data-type="${type}"][data-i="${i}"]`);
      const durEl = document.querySelector(`.detail-cand-dur[data-type="${type}"][data-i="${i}"]`);
      const name = (nameEl?.value || cand.name).trim();
      if (!name) return;
      const newId = _uniqueTaskId();
      const newTask = { id: newId, name, duration: Number(durEl?.value) || cand.duration || 1,
        predecessors: [], successors: [], resources: '', resource_list: cand.resources || [], notes: '' };
      if (type === 'pred') { newTask.successors = [task.id]; task.predecessors = [...(task.predecessors || []), newId]; }
      else { newTask.predecessors = [task.id]; task.successors = [...(task.successors || []), newId]; }
      _tasks.push(newTask);
      added++;
    });

    _closeDetailModal();
    _recalcAndRender();
    showToast(`✓ Aufgabe detailliert${added ? `, ${added} verknüpfte Aufgabe(n) ergänzt` : ''}`);
  }

  function _closeDetailModal() {
    document.getElementById('detail-modal-overlay').classList.remove('active');
    _detailData = null;
  }

  /* ── Aufgabe ersetzen (T4 → T10 oder durch neue) ─────────────────── */
  function _openReplaceModal(idx) {
    const t = _tasks[idx];
    if (!t) return;
    _replaceIdx = idx;
    document.getElementById('replace-modal-title').textContent = `🔁 Aufgabe ersetzen: ${t.id} – ${t.name}`;
    const sel = document.getElementById('replace-sel-existing');
    sel.innerHTML = '<option value="">— bestehende Aufgabe wählen —</option>' +
      _tasks.filter(x => x.id !== t.id)
        .map(x => `<option value="${escHtml(x.id)}">${escHtml(x.id)} – ${escHtml(x.name)}</option>`)
        .join('');
    sel.value = '';
    document.getElementById('replace-new-name').value = '';
    document.getElementById('replace-new-dur').value = t.duration ?? 1;
    document.getElementById('replace-modal-overlay').classList.add('active');
  }

  function _applyReplace() {
    const t = _tasks[_replaceIdx];
    if (!t) { _closeReplaceModal(); return; }
    const targetId = document.getElementById('replace-sel-existing').value;
    const newName = document.getElementById('replace-new-name').value.trim();

    if (targetId) {
      // Durch bestehende Aufgabe ersetzen: deren Verknüpfungen erben die von t,
      // t wird entfernt.
      const target = _tasks.find(x => x.id === targetId);
      if (target) {
        const merge = (arr1, arr2) => [...new Set([...(arr1 || []), ...(arr2 || [])])].filter(x => x !== target.id && x !== t.id);
        target.predecessors = merge(target.predecessors, t.predecessors);
        target.successors   = merge(target.successors, t.successors);
        // Verweise von t auf target umbiegen
        for (const o of _tasks) {
          o.predecessors = (o.predecessors || []).map(p => p === t.id ? target.id : p);
          o.successors   = (o.successors   || []).map(s => s === t.id ? target.id : s);
        }
        const idx = _tasks.indexOf(t);
        if (idx >= 0) _tasks.splice(idx, 1);
        _normalizeLinks();
        _closeReplaceModal();
        _recalcAndRender();
        showToast(`✓ ${t.id} durch ${target.id} ersetzt – Verknüpfungen übertragen`);
        return;
      }
    }
    if (newName) {
      // Durch neue Aufgabe ersetzen: Inhalt austauschen, ID + Verknüpfungen bleiben
      t.name = newName;
      const dur = Number(document.getElementById('replace-new-dur').value);
      if (!Number.isNaN(dur)) t.duration = dur;
      t.resource_list = [];
      t.notes = '';
      _closeReplaceModal();
      _recalcAndRender();
      showToast(`✓ Aufgabe ${t.id} ersetzt (Verknüpfungen erhalten)`);
      return;
    }
    showToast('Bitte bestehende Aufgabe wählen oder neuen Namen eingeben');
  }

  function _closeReplaceModal() {
    document.getElementById('replace-modal-overlay').classList.remove('active');
    _replaceIdx = -1;
  }

  /* ── Neuen Vorgang zwischen zwei Aufgaben einfügen ───────────────── */
  function _fillInsertSelects() {
    const opts = _tasks.map(t => `<option value="${escHtml(t.id)}">${escHtml(t.id)} – ${escHtml(t.name)}</option>`).join('');
    const a = document.getElementById('insert-sel-a');
    const b = document.getElementById('insert-sel-b');
    a.innerHTML = opts; b.innerHTML = opts;
    // Sinnvolle Vorbelegung: erste Kante A→B suchen
    for (const t of _tasks) {
      if ((t.successors || []).length) { a.value = t.id; b.value = t.successors[0]; return; }
    }
    if (_tasks[0]) a.value = _tasks[0].id;
    if (_tasks[1]) b.value = _tasks[1].id;
  }

  function _openInsertModal() {
    if (_tasks.length < 2) { showToast('Mindestens zwei Aufgaben nötig'); return; }
    _insertData = null;
    document.getElementById('insert-modal-body').innerHTML =
      '<div class="planner-muted" style="padding:6px 0">Wähle A und B und hole KI-Vorschläge.</div>';
    _fillInsertSelects();
    document.getElementById('insert-modal-overlay').classList.add('active');
  }

  async function _fetchInsertCandidates() {
    const aId = document.getElementById('insert-sel-a').value;
    const bId = document.getElementById('insert-sel-b').value;
    if (!aId || !bId || aId === bId) { showToast('Bitte zwei verschiedene Aufgaben wählen'); return; }
    const a = _tasks.find(t => t.id === aId), b = _tasks.find(t => t.id === bId);
    _desc = (document.getElementById('planner-desc')?.value || '').trim();
    const model = _model();
    const body = document.getElementById('insert-modal-body');
    body.innerHTML = '<div class="planner-muted" style="padding:6px 0">✨ KI überlegt passende Zwischenvorgänge…</div>';
    try {
      const r = await fetch('/api/plans/insert-between', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ task_a: a, task_b: b, description: _desc, system_prompt: _systemPrompt,
                               model, resource_catalog: _catalog, resource_mode: _resMode }),
      });
      if (!r.ok) throw new Error('HTTP ' + r.status);
      const d = await r.json();
      const cands = d.tasks || [];
      if (!cands.length) { body.innerHTML = '<div class="planner-muted" style="padding:6px 0">Keine Vorschläge erhalten.</div>'; return; }
      _insertData = { aId, bId, candidates: cands };
      body.innerHTML = `<div class="suggest-group-title">Vorschläge zwischen ${escHtml(aId)} → ${escHtml(bId)}</div>` +
        _candidateRows(cands, 'ins');
    } catch (e) {
      body.innerHTML = `<div class="planner-muted" style="padding:6px 0">Fehlgeschlagen: ${escHtml(e.message)}</div>`;
    }
  }

  function _applyInsert() {
    if (!_insertData) { showToast('Erst KI-Vorschläge holen'); return; }
    const a = _tasks.find(t => t.id === _insertData.aId);
    const b = _tasks.find(t => t.id === _insertData.bId);
    if (!a || !b) { _closeInsertModal(); return; }
    // Ausgewählte Kandidaten als neue Aufgaben anlegen (noch ohne Kanten)
    const newIds = [];
    document.querySelectorAll('#insert-modal-body input[type=checkbox][data-type="ins"]:checked').forEach(cb => {
      const i = +cb.dataset.i;
      const cand = _insertData.candidates[i];
      if (!cand) return;
      const nameEl = document.querySelector(`.detail-cand-name[data-type="ins"][data-i="${i}"]`);
      const durEl = document.querySelector(`.detail-cand-dur[data-type="ins"][data-i="${i}"]`);
      const name = (nameEl?.value || cand.name).trim();
      if (!name) return;
      const newId = _uniqueTaskId();
      _tasks.push({ id: newId, name, duration: Number(durEl?.value) || cand.duration || 1,
        predecessors: [], successors: [], resources: '',
        resource_list: cand.resources || [], notes: cand.notes || '' });
      newIds.push(newId);
    });
    if (!newIds.length) { showToast('Nichts ausgewählt'); return; }

    // direkte Kante A→B auflösen und saubere Kette A → c1 → … → cn → B bauen
    a.successors = (a.successors || []).filter(s => s !== b.id);
    b.predecessors = (b.predecessors || []).filter(p => p !== a.id);
    const chain = [a.id, ...newIds, b.id];
    for (let k = 0; k < chain.length - 1; k++) {
      const from = _tasks.find(t => t.id === chain[k]);
      const to   = _tasks.find(t => t.id === chain[k + 1]);
      if (from && to) {
        if (!from.successors.includes(to.id)) from.successors.push(to.id);
        if (!to.predecessors.includes(from.id)) to.predecessors.push(from.id);
      }
    }
    _normalizeLinks();
    _closeInsertModal();
    _recalcAndRender();
    showToast(`✓ ${newIds.length} Zwischenvorgang(e) eingefügt: ${a.id} → … → ${b.id}`);
  }

  function _closeInsertModal() {
    document.getElementById('insert-modal-overlay').classList.remove('active');
    _insertData = null;
  }

  /* ── Ressourcen-/Bestellplan ─────────────────────────────────────── */
  // Aggregiert je Ressource den frühesten Bedarf (min ES der nutzenden
  // Aufgaben) und – bei Lieferzeit – die späteste Bestellung (Bedarf − Lieferzeit).
  function _scheduleRows() {
    const agg = {};
    for (const t of _tasks) {
      const es = _cpm[t.id]?.ES ?? 0;
      for (const r of (t.resource_list || [])) {
        const key = r.kind + '|' + (r.name || '').toLowerCase();
        const lead = Number(r.lead) || 0;
        if (!agg[key]) agg[key] = { kind: r.kind, name: r.name, needDay: es, lead, tasks: [] };
        agg[key].needDay = Math.min(agg[key].needDay, es);
        agg[key].lead = Math.max(agg[key].lead, lead);     // größte Lieferzeit maßgeblich
        agg[key].tasks.push(t.id);
      }
    }
    return Object.values(agg).map(r => ({
      ...r,
      orderDay: r.needDay - r.lead,
    })).sort((a, b) => a.orderDay - b.orderDay);
  }

  // Ressourcen-Überlast: dieselbe (benannte) Mensch-/Hardware-Ressource wird von
  // zwei Aufgaben mit überlappendem Zeitfenster ES…EF gebraucht → Doppelbelegung.
  function _resourceConflicts() {
    const usage = {};
    for (const t of _tasks) {
      const es = _cpm[t.id]?.ES ?? 0, ef = _cpm[t.id]?.EF ?? es;
      for (const r of (t.resource_list || [])) {
        if (r.kind === 'software') continue;   // SW i.d.R. beliebig parallel nutzbar
        const key = r.kind + '|' + (r.name || '').toLowerCase();
        (usage[key] = usage[key] || []).push({ taskId: t.id, es, ef, kind: r.kind, name: r.name });
      }
    }
    const conflicts = [];
    for (const list of Object.values(usage)) {
      list.sort((a, b) => a.es - b.es);
      for (let i = 0; i < list.length; i++) for (let j = i + 1; j < list.length; j++) {
        const start = Math.max(list[i].es, list[j].es);
        const end   = Math.min(list[i].ef, list[j].ef);
        if (start < end) {
          conflicts.push({ kind: list[i].kind, name: list[i].name,
            a: list[i].taskId, b: list[j].taskId, fromDay: start, toDay: end });
        }
      }
    }
    return conflicts;
  }

  /* ── Warn-Anzeige (Zyklen, Ressourcenkonflikte) ──────────────────── */
  function _updateWarnings() {
    const el = document.getElementById('planner-warn');
    if (!el) return;
    const parts = [];
    if (_cycleIds.length) {
      parts.push(`⚠ Zyklus erkannt (${_cycleIds.join(', ')}) – CPM unzuverlässig; bitte Verknüpfung auflösen oder „✨ Mach schön".`);
    }
    const conf = _resourceConflicts();
    if (conf.length) {
      parts.push(`⚠ ${conf.length} Ressourcenkonflikt(e) – Doppelbelegung; Details im 📅 Bestellplan.`);
    }
    // Deadline-Warnung: Projektende überschreitet gesetztes Enddatum
    if (_endDate && _startDate && Object.keys(_cpm).length) {
      const maxEF = Math.max(...Object.values(_cpm).map(r => r.EF), 0);
      const sd = new Date(_startDate + 'T00:00:00'), ed = new Date(_endDate + 'T00:00:00');
      if (!isNaN(sd) && !isNaN(ed)) {
        const availDays = (_endDate && _startDate) ? Math.round((ed - sd) / 86400000) : Infinity;
        if (maxEF > availDays) parts.push(`🏁 Deadline überschritten: Plan braucht ${maxEF} Tage, Enddatum erlaubt nur ${availDays}.`);
      }
    } else if (_endDate && !_startDate && Object.keys(_cpm).length) {
      const maxEF = Math.max(...Object.values(_cpm).map(r => r.EF), 0);
      const sd = _effectiveStartDate();
      if (sd) parts.push(`📅 Rückwärts geplant: Projektstart ${sd} (aus Enddatum ${_endDate} − ${maxEF} Tage).`);
    }
    el.textContent = parts.join('   ');
  }

  /* ── Kapazität & Zukauf (Auslastung vs. globale Kapazitätsliste) ──── */
  async function _ensureCapacity() {
    if (_capacity) return _capacity;
    try { _capacity = (await (await fetch('/api/capacity')).json()).items || []; }
    catch (_) { _capacity = []; }
    return _capacity;
  }

  function _capLookup(name) {
    const n = String(name || '').toLowerCase().trim();
    if (!n || !_capacity) return null;
    let m = _capacity.find(c => String(c.name || '').toLowerCase().trim() === n);
    if (!m) m = _capacity.find(c => {
      const cn = String(c.name || '').toLowerCase().trim();
      return cn && (cn.includes(n) || n.includes(cn));
    });
    return m || null;
  }

  // Liefert das letzte berechnete Kapazitäts-/Zukauf-Modell (für CSV-Export).
  let _lastCapModel = null;

  function _capacityAnalysisHtml() {
    // Bedarf je Mensch-Rolle aggregieren (Σ qty×hours)
    const demand = {};
    for (const t of _tasks) {
      for (const r of (t.resource_list || [])) {
        if (r.kind !== 'human') continue;
        const key = (r.name || '').trim() || 'unbestimmt';
        if (!demand[key]) demand[key] = { name: key, hours: 0, rate: Number(r.rate) || 0, tasks: 0 };
        demand[key].hours += (Number(r.qty) || 0) * (Number(r.hours) || 0);
        demand[key].tasks += 1;
        if (!demand[key].rate && r.rate) demand[key].rate = Number(r.rate) || 0;
      }
    }
    const roles = Object.values(demand).sort((a, b) => b.hours - a.hours);
    const model = [];
    let intCost = 0, buyCost = 0;
    for (const d of roles) {
      const cap = _capLookup(d.name);
      const capH = cap ? (Number(cap.capacity_h) || 0) : null;
      const rate = d.rate || (cap ? Number(cap.rate) || 0 : 0);
      const shortfall = (capH != null) ? Math.max(0, d.hours - capH) : null;
      const util = (capH && capH > 0) ? Math.round(d.hours / capH * 100) : null;
      intCost += d.hours * rate;
      if (shortfall) buyCost += shortfall * rate;
      model.push({ name: d.name, hours: d.hours, tasks: d.tasks, rate, country: cap ? cap.country : '',
        capH, shortfall, util, hasCap: !!cap });
    }
    _lastCapModel = model;

    const eur = v => (Math.round(v) ).toLocaleString('de-DE');
    let html = '<div class="suggest-group-title">👥 Kapazität & Make-or-Buy (Bedarf vs. freie Kapazität)</div>';
    if (!roles.length) {
      html += '<p class="planner-muted" style="font-size:12px">Keine Mensch-Ressourcen mit Stunden hinterlegt.</p>';
      return html;
    }
    html += `<table class="schedule-table"><thead><tr>
      <th>Rolle</th><th>Land</th><th>Bedarf (h)</th><th>frei (h)</th><th>Auslastung</th>
      <th>Fehlstunden</th><th>Kosten (€)</th><th>Bewertung</th></tr></thead><tbody>`;
    for (const m of model) {
      let verdict, color;
      if (!m.hasCap) { verdict = 'keine Kapazität hinterlegt → zukaufen/anlegen'; color = '#9ca3af'; }
      else if (m.shortfall > 0) { verdict = `Überlast → zukaufen (${Math.round(m.shortfall)} h)`; color = '#f87171'; }
      else { verdict = 'im Rahmen'; color = '#34d399'; }
      html += `<tr>
        <td>${escHtml(m.name)}</td>
        <td>${escHtml(m.country || '–')}</td>
        <td style="text-align:right">${Math.round(m.hours)}</td>
        <td style="text-align:right">${m.capH != null ? Math.round(m.capH) : '–'}</td>
        <td style="text-align:right">${m.util != null ? m.util + '%' : '–'}</td>
        <td style="text-align:right;${m.shortfall ? 'color:#f87171;font-weight:600' : ''}">${m.shortfall != null ? Math.round(m.shortfall) : '–'}</td>
        <td style="text-align:right">${m.rate ? eur(m.hours * m.rate) : '–'}</td>
        <td style="color:${color}">${escHtml(verdict)}</td>
      </tr>`;
    }
    html += `</tbody></table>
      <p class="planner-muted" style="font-size:11.5px;margin-top:4px">
        Geschätzte interne Kosten gesamt: <b>${eur(intCost)} €</b>${buyCost ? ` · davon Zukauf-Bedarf (Fehlstunden × Satz): <b style="color:#f87171">${eur(buyCost)} €</b>` : ''}.
        Auslastung &gt; 100 % bedeutet Überlast → externe Kapazität / Partner / Best-Cost-Country.
      </p>`;

    // Partner- und BCC-Listen aus RFQ-Übergaben (falls vorhanden)
    const partners = _tasks.filter(t => t.rfq && t.rfq.partner_needed);
    const bccs = _tasks.filter(t => t.rfq && t.rfq.bcc_suitable);
    if (partners.length) {
      html += '<div class="suggest-group-title" style="margin-top:14px">🤝 Partner nötig</div><ul style="margin:4px 0 0 18px;font-size:12px">'
        + partners.map(t => `<li>${escHtml(t.name)}${t.rfq.partner_type ? ' — ' + escHtml(t.rfq.partner_type) : ''}</li>`).join('') + '</ul>';
    }
    if (bccs.length) {
      html += '<div class="suggest-group-title" style="margin-top:14px">🌍 Best-Cost-Country</div><ul style="margin:4px 0 0 18px;font-size:12px">'
        + bccs.map(t => `<li>${escHtml(t.name)}${t.rfq.bcc_region ? ' → ' + escHtml(t.rfq.bcc_region) : ''}</li>`).join('') + '</ul>';
    }
    return html;
  }

  async function _openSchedule() {
    if (!_tasks.length) { showToast('Keine Aufgaben'); return; }
    _computeCPM();
    await _ensureCapacity();
    const rows = _scheduleRows();
    const hint = document.getElementById('schedule-hint');
    if (hint) hint.textContent = _startDate
      ? `Kalenderdaten ab Projektstart ${_fmtDay(0)}. „Bestellen bis" = Bedarf − Lieferzeit.`
      : 'Tipp: Projektstart-Datum oben setzen, um echte Kalenderdaten statt „Tag X" zu sehen.';
    const body = document.getElementById('schedule-modal-body');
    // Kapazitäts-/Zukauf-Analyse (primär) zuerst, dann der HW/SW-Bestellplan.
    const capHtml = _capacityAnalysisHtml()
      + '<div class="suggest-group-title" style="margin-top:16px">📦 Beschaffung Hardware/Software (Bestellplan)</div>';
    if (!rows.length) {
      body.innerHTML = capHtml + '<div class="planner-muted" style="padding:8px 0">Noch keine Hardware/Software/Lieferpositionen mit Bedarf hinterlegt.</div>';
    } else {
      body.innerHTML = capHtml + `<table id="schedule-table" class="schedule-table">
        <thead><tr>
          <th>Typ</th><th>Ressource</th><th>Aufgaben</th>
          <th>benötigt ab</th><th>Lieferz. (d)</th><th>bestellen bis</th>
        </tr></thead>
        <tbody>${rows.map(r => {
          const late = r.orderDay < 0;
          return `<tr>
            <td>${_KIND_ICON[r.kind] || ''} ${escHtml(_KIND_DE[r.kind] || r.kind)}</td>
            <td>${escHtml(r.name || '')}</td>
            <td class="planner-muted" style="font-size:11px">${escHtml([...new Set(r.tasks)].join(', '))}</td>
            <td>${escHtml(_fmtDay(r.needDay))}</td>
            <td style="text-align:right">${r.lead || 0}</td>
            <td style="${late ? 'color:#f87171;font-weight:600' : ''}" title="${late ? 'Liegt vor Projektstart – früh bestellen!' : ''}">${escHtml(_fmtDay(r.orderDay))}${late ? ' ⚠' : ''}</td>
          </tr>`;
        }).join('')}</tbody></table>`;
    }

    // Ressourcen-Überlast (Doppelbelegung)
    const conf = _resourceConflicts();
    if (conf.length) {
      body.innerHTML += `<div class="suggest-group-title" style="margin-top:14px;color:#f87171">⚠ Ressourcenkonflikte (Doppelbelegung)</div>
        <table class="schedule-table"><thead><tr>
          <th>Typ</th><th>Ressource</th><th>Aufgabe A</th><th>Aufgabe B</th><th>Überlappung</th>
        </tr></thead><tbody>${conf.map(c => `<tr>
          <td>${_KIND_ICON[c.kind] || ''} ${escHtml(_KIND_DE[c.kind] || c.kind)}</td>
          <td>${escHtml(c.name || '')}</td>
          <td>${escHtml(c.a)}</td><td>${escHtml(c.b)}</td>
          <td>${escHtml(_fmtDay(c.fromDay))} – ${escHtml(_fmtDay(c.toDay))}</td>
        </tr>`).join('')}</tbody></table>
        <p class="planner-muted" style="font-size:11px;margin-top:4px">Dieselbe Ressource wird in überlappenden Zeitfenstern gebraucht – mehr Einheiten einplanen, verschieben oder Abhängigkeit ergänzen.</p>`;
    }
    document.getElementById('schedule-modal-overlay').classList.add('active');
  }

  function _exportSchedule() {
    const rows = _scheduleRows();
    const cap = _lastCapModel || [];
    if (!rows.length && !cap.length) { showToast('Keine Ressourcen vorhanden'); return; }
    const sep = ';';
    const q = v => '"' + String(v ?? '').replace(/"/g, '""') + '"';
    const out = [];
    // Kapazitäts-/Zukauf-Abschnitt
    if (cap.length) {
      out.push([q('Rolle'), q('Land'), q('Bedarf (h)'), q('frei (h)'), q('Auslastung %'),
                q('Fehlstunden'), q('Kosten (€)'), q('Bewertung')].join(sep));
      for (const m of cap) {
        const verdict = !m.hasCap ? 'keine Kapazität hinterlegt'
          : (m.shortfall > 0 ? 'Überlast - zukaufen' : 'im Rahmen');
        out.push([q(m.name), q(m.country || ''), q(Math.round(m.hours)),
          q(m.capH != null ? Math.round(m.capH) : ''), q(m.util != null ? m.util : ''),
          q(m.shortfall != null ? Math.round(m.shortfall) : ''),
          q(m.rate ? Math.round(m.hours * m.rate) : ''), q(verdict)].join(sep));
      }
      out.push('');
    }
    // Bestellplan-Abschnitt (HW/SW Lieferzeiten)
    if (rows.length) {
      out.push([q('Typ'), q('Ressource'), q('Aufgaben'), q('benötigt ab'), q('Lieferzeit (d)'), q('bestellen bis')].join(sep));
      for (const r of rows) {
        out.push([q(_KIND_DE[r.kind] || r.kind), q(r.name), q([...new Set(r.tasks)].join(', ')),
          q(_fmtDay(r.needDay)), q(r.lead || 0), q(_fmtDay(r.orderDay))].join(sep));
      }
    }
    const name = document.getElementById('planner-plan-name')?.value?.trim() || 'plan';
    const blob = new Blob(['﻿' + out.join('\r\n')], { type: 'text/csv;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url; a.download = `${name}_kapazitaet_bestellplan.csv`; a.click();
    URL.revokeObjectURL(url);
    showToast('✓ Kapazität & Bestellplan exportiert');
  }

  function _closeSchedule() {
    document.getElementById('schedule-modal-overlay').classList.remove('active');
  }

  /* ── Ressourcen-Modal ────────────────────────────────────────────── */
  function _openResourceModal(idx) {
    _resTaskIdx = idx;
    const t = _tasks[idx];
    _resDraft = JSON.parse(JSON.stringify(t.resource_list || []));
    document.getElementById('resource-modal-title').textContent = `🧰 Ressourcen – ${t.name || t.id}`;
    _renderResourceRows();
    document.getElementById('resource-modal-overlay').classList.add('active');
  }

  function _renderResourceRows() {
    const tbody = document.getElementById('resource-tbody');
    tbody.innerHTML = '';
    _resDraft.forEach((r, i) => {
      const tr = document.createElement('tr');
      tr.innerHTML = `
        <td><select data-f="kind" data-i="${i}">
          <option value="human" ${r.kind === 'human' ? 'selected' : ''}>👤 Mensch</option>
          <option value="hardware" ${r.kind === 'hardware' ? 'selected' : ''}>🔧 Hardware</option>
          <option value="software" ${r.kind === 'software' ? 'selected' : ''}>💻 Software</option>
        </select></td>
        <td><input data-f="name" data-i="${i}" value="${escHtml(r.name || '')}" style="width:160px" /></td>
        <td><input data-f="qty" data-i="${i}" type="number" min="0" step="0.5" value="${r.qty ?? 1}" style="width:60px" /></td>
        <td><input data-f="hours" data-i="${i}" type="number" min="0" step="0.5" value="${r.hours ?? 0}" style="width:60px" /></td>
        <td><input data-f="rate" data-i="${i}" type="number" min="0" step="1" value="${r.rate ?? 0}" style="width:70px" /></td>
        <td><input data-f="lead" data-i="${i}" type="number" min="0" step="1" value="${r.lead ?? 0}" style="width:60px" title="Lieferzeit / Bestellvorlauf in Tagen" /></td>
        <td class="res-sum" style="text-align:right">${_fmtEur(_resCost(r))}</td>
        <td><button class="btn-del-res" data-i="${i}" title="Entfernen">🗑</button></td>`;
      tbody.appendChild(tr);
    });
    // Eingaben aktualisieren Draft + nur Summe-Zelle + Rollup (kein Voll-Rerender → kein Fokusverlust)
    tbody.querySelectorAll('[data-f]').forEach(inp => {
      const ev = inp.tagName === 'SELECT' ? 'change' : 'input';
      inp.addEventListener(ev, e => {
        const i = +e.target.dataset.i, f = e.target.dataset.f;
        _resDraft[i][f] = (f === 'kind' || f === 'name') ? e.target.value : (Number(e.target.value) || 0);
        const sumCell = e.target.closest('tr').querySelector('.res-sum');
        if (sumCell) sumCell.textContent = _fmtEur(_resCost(_resDraft[i]));
        _updateResourceRollup();
      });
    });
    tbody.querySelectorAll('.btn-del-res').forEach(btn => {
      btn.addEventListener('click', () => { _resDraft.splice(+btn.dataset.i, 1); _renderResourceRows(); });
    });
    _updateResourceRollup();
  }

  function _updateResourceRollup() {
    const cost = _resDraft.reduce((s, r) => s + _resCost(r), 0);
    const ph = _resDraft.filter(r => r.kind === 'human').reduce((s, r) => s + (Number(r.qty) || 0) * (Number(r.hours) || 0), 0);
    const el = document.getElementById('resource-rollup');
    if (el) el.textContent = `Σ ${_fmtEur(cost)} · ${ph} Pers.-h`;
  }

  function _saveResourceModal() {
    if (_resTaskIdx >= 0 && _tasks[_resTaskIdx]) {
      _tasks[_resTaskIdx].resource_list = _resDraft.filter(r => (r.name || '').trim() || _resCost(r) > 0);
    }
    _closeResourceModal();
    _recalcAndRender();
  }

  function _closeResourceModal() {
    document.getElementById('resource-modal-overlay').classList.remove('active');
    _resTaskIdx = -1; _resDraft = [];
  }

  /* ── Ressourcen-Katalog (Import/Export) + Modus ──────────────────── */
  const _KIND_DE = { human: 'Mensch', hardware: 'Hardware', software: 'Software' };

  function _deToKind(s) {
    const t = (s || '').toLowerCase().trim();
    if (/(mensch|human|person)/.test(t)) return 'human';
    if (/(hardware|^hw|gerät|geraet)/.test(t)) return 'hardware';
    if (/(software|^sw|lizenz)/.test(t)) return 'software';
    return 'human';
  }

  function _parseCsvLine(line, sep) {
    const out = []; let cur = '', inQ = false;
    for (let i = 0; i < line.length; i++) {
      const ch = line[i];
      if (ch === '"') { if (inQ && line[i + 1] === '"') { cur += '"'; i++; } else inQ = !inQ; }
      else if (ch === sep && !inQ) { out.push(cur.trim()); cur = ''; }
      else cur += ch;
    }
    out.push(cur.trim());
    return out;
  }

  function _importCatalog(file) {
    const reader = new FileReader();
    reader.onload = e => {
      try {
        let text = e.target.result;
        if (text.charCodeAt(0) === 0xFEFF) text = text.slice(1);
        const lines = text.split(/\r?\n/).filter(l => l.trim());
        if (!lines.length) { showToast('CSV leer'); return; }
        const sep = lines[0].includes(';') ? ';' : ',';
        const hdr = _parseCsvLine(lines[0], sep).map(h => h.toLowerCase());
        const ci = k => hdr.findIndex(h => h.includes(k));
        let iTyp = ci('typ'), iName = ci('name'), iRate = ci('satz') >= 0 ? ci('satz') : ci('kosten');
        let start = 1;
        // Falls keine Kopfzeile erkannt: Spalten Typ;Name;Satz annehmen
        if (iName < 0) { iTyp = 0; iName = 1; iRate = 2; start = 0; }
        const cat = [];
        for (let i = start; i < lines.length; i++) {
          const p = _parseCsvLine(lines[i], sep);
          const name = (p[iName] || '').trim();
          if (!name) continue;
          cat.push({
            kind: _deToKind(p[iTyp]),
            name,
            rate: Number(String(p[iRate] || '').replace(',', '.').replace(/[^\d.]/g, '')) || 0,
          });
        }
        _catalog = cat;
        if (_resMode === 'free' && cat.length) {
          _resMode = 'extend';
          const sel = document.getElementById('planner-res-mode');
          if (sel) sel.value = 'extend';
        }
        _updateCatalogStatus();
        showToast(`✓ Katalog importiert: ${cat.length} Ressourcen`);
      } catch (err) {
        showToast('Katalog-Fehler: ' + err.message);
      }
    };
    reader.readAsText(file, 'utf-8');
  }

  function _updateCatalogStatus() {
    const el = document.getElementById('planner-catalog-status');
    if (el) el.textContent = _catalog.length ? `📋 Katalog: ${_catalog.length} Ressourcen` : '';
  }

  // Ressourcen aus der globalen Kapazitätsliste (Anfrage-Tab) als Katalog übernehmen.
  // Es werden nur die vom Planer genutzten Felder {kind,name,rate} projiziert.
  async function _catalogFromGlobal() {
    try {
      const items = (await (await fetch('/api/capacity')).json()).items || [];
      if (!items.length) { showToast('Globale Kapazitätsliste ist leer (im Anfrage-Tab pflegen)'); return; }
      _catalog = items.map(it => ({ kind: it.kind || 'human', name: it.name, rate: it.rate || 0 }));
      if (_resMode === 'free') {
        _resMode = 'extend';
        const sel = document.getElementById('planner-res-mode');
        if (sel) sel.value = 'extend';
      }
      _updateCatalogStatus();
      showToast(`✓ ${_catalog.length} Ressourcen aus globaler Liste übernommen`);
    } catch (e) { showToast('Fehler: ' + e.message); }
  }

  // Verwendete Ressourcen über alle Aufgaben aggregieren und als CSV exportieren
  function _exportResources() {
    const sep = ';';
    const q = v => '"' + String(v ?? '').replace(/"/g, '""') + '"';
    const agg = {};
    for (const t of _tasks) {
      for (const r of (t.resource_list || [])) {
        const key = r.kind + '|' + (r.name || '').toLowerCase();
        if (!agg[key]) agg[key] = { kind: r.kind, name: r.name, hours: 0, cost: 0, rate: r.rate, tasks: 0 };
        agg[key].hours += (Number(r.qty) || 0) * (Number(r.hours) || 0);
        agg[key].cost += _resCost(r);
        agg[key].rate = r.rate;
        agg[key].tasks += 1;
      }
    }
    const items = Object.values(agg).sort((a, b) => b.cost - a.cost);
    if (!items.length) { showToast('Keine Ressourcen vorhanden'); return; }
    const header = [q('Typ'), q('Name'), q('Satz (€)'), q('Stunden gesamt'), q('Aufgaben'), q('Kosten gesamt (€)')].join(sep);
    const rows = items.map(r => [
      q(_KIND_DE[r.kind] || r.kind), q(r.name), q(r.rate),
      q(Math.round(r.hours * 100) / 100), q(r.tasks), q(Math.round(r.cost * 100) / 100),
    ].join(sep));
    const total = items.reduce((s, r) => s + r.cost, 0);
    const totalH = items.reduce((s, r) => s + r.hours, 0);
    rows.push([q('SUMME'), q(''), q(''), q(Math.round(totalH * 100) / 100), q(''), q(Math.round(total * 100) / 100)].join(sep));

    const name = document.getElementById('planner-plan-name')?.value?.trim() || 'plan';
    const blob = new Blob(['﻿' + [header, ...rows].join('\r\n')], { type: 'text/csv;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url; a.download = `${name}_ressourcen.csv`; a.click();
    URL.revokeObjectURL(url);
    showToast(`✓ ${items.length} Ressourcen exportiert`);
  }

  /* ── CSV-Export ─────────────────────────────────────────────────── */
  function _exportCsv() {
    const sep = ';';
    const q = v => '"' + String(v ?? '').replace(/"/g, '""') + '"';
    const header = [q('ID'), q('Name'), q('Dauer (d)'), q('Vorgänger'), q('Nachfolger'), q('Ressourcen'), q('Kosten (€)'), q('Personenstunden'), q('ES'), q('EF'), q('LS'), q('LF'), q('Puffer')].join(sep);
    const rows = _tasks.map(t => {
      const c = _cpm[t.id] || {};
      const resText = (t.resource_list && t.resource_list.length) ? _resSummary(t.resource_list) : (t.resources || '');
      return [
        q(t.id), q(t.name), q(t.duration),
        q((t.predecessors || []).join(',')),
        q((t.successors   || []).join(',')),
        q(resText),
        q(Math.round(_taskCost(t) * 100) / 100),
        q(_taskPersonHours(t)),
        q(c.ES ?? ''), q(c.EF ?? ''), q(c.LS ?? ''), q(c.LF ?? ''), q(c.float ?? ''),
      ].join(sep);
    });
    const name = document.getElementById('planner-plan-name')?.value?.trim() || 'plan';
    const blob = new Blob(['﻿' + [header, ...rows].join('\r\n')], { type: 'text/csv;charset=utf-8' });
    const url  = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url; a.download = `${name}.csv`; a.click();
    URL.revokeObjectURL(url);
  }

  /* ── CSV-Import ──────────────────────────────────────────────────── */
  function _importCsv(file) {
    const reader = new FileReader();
    reader.onload = e => {
      try {
        let text = e.target.result;
        if (text.charCodeAt(0) === 0xFEFF) text = text.slice(1);
        const lines = text.split(/\r?\n/).filter(l => l.trim());
        if (lines.length < 2) { showToast('CSV zu kurz'); return; }
        const sep = lines[0].includes(';') ? ';' : ',';

        const parse = line => {
          const out = []; let cur = '', inQ = false;
          for (let i = 0; i < line.length; i++) {
            const ch = line[i];
            if (ch === '"') { if (inQ && line[i+1] === '"') { cur += '"'; i++; } else inQ = !inQ; }
            else if (ch === sep && !inQ) { out.push(cur.trim()); cur = ''; }
            else cur += ch;
          }
          out.push(cur.trim());
          return out;
        };

        // Erste Zeile = Kopfzeile → spaltenindex ermitteln
        const hdr = parse(lines[0]).map(h => h.toLowerCase());
        const ci = k => hdr.findIndex(h => h.includes(k));
        const iId = ci('id') >= 0 ? ci('id') : 0;
        const iName = ci('name') >= 0 ? ci('name') : 1;
        const iDur  = ci('dauer') >= 0 ? ci('dauer') : 2;
        const iPred = ci('vorg') >= 0 ? ci('vorg') : 3;
        const iSucc = ci('nachf') >= 0 ? ci('nachf') : 4;
        const iRes  = ci('ress') >= 0 ? ci('ress') : 5;

        const newTasks = [];
        for (let i = 1; i < lines.length; i++) {
          const p = parse(lines[i]);
          if (!p[iId]) continue;
          newTasks.push({
            id: p[iId] || `T${i}`,
            name: p[iName] || '',
            duration: Number(p[iDur]) || 0,
            predecessors: p[iPred] ? p[iPred].split(',').map(s => s.trim()).filter(Boolean) : [],
            successors:   p[iSucc] ? p[iSucc].split(',').map(s => s.trim()).filter(Boolean) : [],
            resources: p[iRes] || '',
            notes: '',
          });
        }
        _tasks = newTasks;
        _recalcAndRender();
        showToast(`CSV importiert: ${newTasks.length} Aufgaben`);
      } catch (err) {
        showToast('CSV-Fehler: ' + err.message);
      }
    };
    reader.readAsText(file, 'utf-8');
  }

  /* ── Zoom / Pan ──────────────────────────────────────────────────── */
  function _fitView() {
    if (_tasks.length === 0 || !_canvas) return;
    const xVals = Object.values(_layout).map(l => l.x);
    const yVals = Object.values(_layout).map(l => l.y);
    const maxX  = Math.max(...xVals) + NODE_W;
    const maxY  = Math.max(...yVals) + NODE_H;
    const padX = _canvas.width  * 0.05;
    const padY = _canvas.height * 0.05;
    const zx = (_canvas.width  - 2 * padX) / maxX;
    const zy = (_canvas.height - 2 * padY) / maxY;
    _zoom = Math.min(zx, zy, 2);
    _panX = padX;
    _panY = padY;
    _render();
  }

  /* ── Modell: zentral aus dem Profil (Rolle „Allgemein") ──────────── */
  function _model() {
    return (typeof Profile !== 'undefined' ? Profile.modelFor('general') : '') || undefined;
  }

  /* ── init ────────────────────────────────────────────────────────── */
  function init() {
    _canvas = document.getElementById('planner-canvas');
    if (!_canvas) return;
    _ctx = _canvas.getContext('2d');

    // Pläne laden
    _loadPlanList();

    // Neuer Plan
    document.getElementById('btn-new-plan')?.addEventListener('click', () => {
      _planId = null; _tasks = []; _cpm = {}; _layout = {};
      _desc = ''; _systemPrompt = ''; _catalog = []; _resMode = 'free'; _startDate = ''; _endDate = ''; _workdays = false;
      document.getElementById('planner-plan-name').value = 'Neuer Plan';
      document.getElementById('planner-plan-select').value = '';
      const sd0 = document.getElementById('planner-start-date'); if (sd0) sd0.value = '';
      const wd0 = document.getElementById('planner-workdays'); if (wd0) wd0.checked = false;
      const descEl = document.getElementById('planner-desc'); if (descEl) descEl.value = '';
      const modeSel = document.getElementById('planner-res-mode'); if (modeSel) modeSel.value = 'free';
      _updateCatalogStatus();
      const status = document.getElementById('planner-agent-status'); if (status) { status.textContent = ''; status.title = ''; }
      document.getElementById('btn-delete-plan').style.display = 'none';
      _recalcAndRender();
    });

    // Plan speichern
    document.getElementById('btn-save-plan')?.addEventListener('click', _savePlan);

    // Plan löschen
    document.getElementById('btn-delete-plan')?.addEventListener('click', async () => {
      if (!_planId || !confirm('Plan löschen?')) return;
      await fetch(`/api/plans/${_planId}`, { method: 'DELETE' });
      _planId = null; _tasks = []; _cpm = {}; _layout = {};
      document.getElementById('planner-plan-name').value = '';
      document.getElementById('btn-delete-plan').style.display = 'none';
      await _loadPlanList();
      _recalcAndRender();
    });

    // Plan-Selektor
    document.getElementById('planner-plan-select')?.addEventListener('change', function() {
      if (this.value) _loadPlan(this.value);
    });

    // Aufgabe hinzufügen
    document.getElementById('btn-add-task')?.addEventListener('click', () => {
      const newId = 'T' + (_tasks.length + 1);
      _tasks.push({ id: newId, name: 'Neue Aufgabe', duration: 1, predecessors: [], successors: [], resources: '', notes: '' });
      _recalcAndRender();
    });

    // CPM berechnen
    document.getElementById('btn-recalc-cpm')?.addEventListener('click', _recalcAndRender);
    document.getElementById('btn-research-all')?.addEventListener('click', _researchAll);
    document.getElementById('btn-research-stop')?.addEventListener('click', () => { _researchStop = true; });
    document.getElementById('btn-plan-doc-close')?.addEventListener('click',
      () => document.getElementById('plan-doc-overlay').classList.remove('active'));

    // Zoom-Buttons
    document.getElementById('btn-zoom-in')?.addEventListener('click', () => { _zoom = Math.min(_zoom * 1.2, 4); _render(); });
    document.getElementById('btn-zoom-out')?.addEventListener('click', () => { _zoom = Math.max(_zoom / 1.2, 0.1); _render(); });
    document.getElementById('btn-zoom-fit')?.addEventListener('click', _fitView);

    // Canvas-Interaktion
    _canvas.addEventListener('wheel', e => {
      e.preventDefault();
      const factor = e.deltaY < 0 ? 1.1 : 0.9;
      _zoom = Math.max(0.1, Math.min(4, _zoom * factor));
      _render();
    }, { passive: false });

    _canvas.addEventListener('mousedown', e => {
      _dragging = true; _lastMX = e.clientX; _lastMY = e.clientY;
    });
    _canvas.addEventListener('mousemove', e => {
      if (!_dragging) return;
      _panX += e.clientX - _lastMX;
      _panY += e.clientY - _lastMY;
      _lastMX = e.clientX; _lastMY = e.clientY;
      _render();
    });
    _canvas.addEventListener('mouseup', () => { _dragging = false; });
    _canvas.addEventListener('mouseleave', () => { _dragging = false; });

    // Touch (Mobile)
    let _lastTX = 0, _lastTY = 0;
    _canvas.addEventListener('touchstart', e => { _lastTX = e.touches[0].clientX; _lastTY = e.touches[0].clientY; }, { passive: true });
    _canvas.addEventListener('touchmove', e => {
      e.preventDefault();
      _panX += e.touches[0].clientX - _lastTX;
      _panY += e.touches[0].clientY - _lastTY;
      _lastTX = e.touches[0].clientX; _lastTY = e.touches[0].clientY;
      _render();
    }, { passive: false });

    // CSV Import/Export
    document.getElementById('btn-plan-export-csv')?.addEventListener('click', _exportCsv);
    const planCsvInput = document.getElementById('plan-csv-input');
    document.getElementById('btn-plan-import-csv')?.addEventListener('click', () => planCsvInput?.click());
    planCsvInput?.addEventListener('change', e => {
      if (e.target.files[0]) _importCsv(e.target.files[0]);
      e.target.value = '';
    });

    // Dokument → Plan (Strategiepapier o. Ä. importieren → Ressourcen + Plan ableiten)
    const planDocInput = document.getElementById('plan-doc-input');
    document.getElementById('btn-plan-from-doc')?.addEventListener('click', () => planDocInput?.click());
    planDocInput?.addEventListener('change', e => {
      if (e.target.files[0]) _importDocPlan(e.target.files[0]);
      e.target.value = '';
    });

    // KI-Assistent
    document.getElementById('btn-planner-ai-send')?.addEventListener('click', _sendAI);
    document.getElementById('planner-ai-input')?.addEventListener('keydown', e => {
      if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); _sendAI(); }
    });

    // Projekt-Agent ableiten + Komplett-Generierung
    document.getElementById('btn-derive-agent')?.addEventListener('click', _deriveAgent);
    document.getElementById('btn-plan-save-agent')?.addEventListener('click', _saveAsAgent);
    const pickSel = document.getElementById('btn-plan-pick-agent');
    pickSel?.addEventListener('mousedown', _fillAgentPicker);   // Liste vor dem Öffnen aktualisieren
    pickSel?.addEventListener('change', e => _pickAgent(e.target.value));
    document.getElementById('btn-generate-plan')?.addEventListener('click', _generatePlan);
    document.getElementById('btn-plan-check')?.addEventListener('click', _checkFeasibility);

    // Wissensdatenbanken (Informationsbeschaffung) + Plan → RAG
    _fillRagSelect();
    _fillAgentPicker();
    document.getElementById('planner-rag-select')?.addEventListener('mousedown', _fillRagSelect);
    document.getElementById('btn-plan-to-rag')?.addEventListener('click', _planToRag);

    // Einklappbarer Setup-Block: Zustand merken (spart Platz beim Arbeiten am Plan)
    const setup = document.getElementById('planner-setup');
    if (setup) {
      // Standardmäßig eingeklappt; nur öffnen, wenn der Nutzer es zuletzt offen ließ.
      try { if (localStorage.getItem('planner-setup-open') === '1') setup.open = true; } catch (_) {}
      setup.addEventListener('toggle', () => {
        try { localStorage.setItem('planner-setup-open', setup.open ? '1' : '0'); } catch (_) {}
      });
    }

    // Ressourcen-Katalog: Modus, Import, Export
    document.getElementById('planner-res-mode')?.addEventListener('change', e => { _resMode = e.target.value; });
    const catInput = document.getElementById('catalog-csv-input');
    document.getElementById('btn-import-catalog')?.addEventListener('click', () => catInput?.click());
    catInput?.addEventListener('change', e => { if (e.target.files[0]) _importCatalog(e.target.files[0]); e.target.value = ''; });
    document.getElementById('btn-catalog-from-global')?.addEventListener('click', _catalogFromGlobal);
    document.getElementById('btn-export-resources')?.addEventListener('click', _exportResources);

    // Ressourcen-Modal
    document.getElementById('btn-add-resource')?.addEventListener('click', () => {
      _resDraft.push({ kind: 'human', name: '', qty: 1, hours: 0, rate: 0, lead: 0 });
      _renderResourceRows();
    });
    document.getElementById('btn-resource-save')?.addEventListener('click', _saveResourceModal);
    document.getElementById('btn-resource-cancel')?.addEventListener('click', _closeResourceModal);

    // Vorschlags-Modal
    document.getElementById('btn-suggest-apply')?.addEventListener('click', _applySuggestions);
    document.getElementById('btn-suggest-cancel')?.addEventListener('click', _closeSuggestModal);

    // Detail-Modal
    document.getElementById('btn-detail-apply')?.addEventListener('click', _applyDetail);
    document.getElementById('btn-detail-cancel')?.addEventListener('click', _closeDetailModal);

    // Mach schön (säubern + sortieren + neu zeichnen)
    document.getElementById('btn-beautify')?.addEventListener('click', _beautify);

    // Vorgang dazwischen einfügen
    document.getElementById('btn-insert-between')?.addEventListener('click', _openInsertModal);
    document.getElementById('btn-insert-fetch')?.addEventListener('click', _fetchInsertCandidates);
    document.getElementById('btn-insert-apply')?.addEventListener('click', _applyInsert);
    document.getElementById('btn-insert-cancel')?.addEventListener('click', _closeInsertModal);

    // Aufgabe ersetzen
    document.getElementById('btn-replace-apply')?.addEventListener('click', _applyReplace);
    document.getElementById('btn-replace-cancel')?.addEventListener('click', _closeReplaceModal);

    // Ressourcen-/Bestellplan
    document.getElementById('btn-schedule')?.addEventListener('click', _openSchedule);
    document.getElementById('btn-schedule-export')?.addEventListener('click', _exportSchedule);
    document.getElementById('btn-schedule-close')?.addEventListener('click', _closeSchedule);

    // Projektstart-/Enddatum + Arbeitstage
    document.getElementById('planner-start-date')?.addEventListener('change', e => { _startDate = e.target.value; _recalcAndRender(); });
    document.getElementById('planner-end-date')?.addEventListener('change', e => { _endDate = e.target.value; _recalcAndRender(); });
    document.getElementById('planner-workdays')?.addEventListener('change', e => { _workdays = e.target.checked; });

    // AI-Panel: Web- und RAG-Toggle
    ['btn-plan-ai-web', 'btn-plan-ai-rag'].forEach(id =>
      document.getElementById(id)?.addEventListener('click', e => e.currentTarget.classList.toggle('active')));

    // Plan-Vergleich
    document.getElementById('btn-plan-evaluate')?.addEventListener('click', _openEvalModal);
    document.getElementById('btn-plan-eval-close')?.addEventListener('click', () =>
      document.getElementById('plan-eval-overlay')?.classList.remove('active'));
    document.getElementById('btn-plan-eval-run')?.addEventListener('click', _runEvaluation);
    document.getElementById('btn-plan-eval-import')?.addEventListener('click', _importEvalPlan);

    // Plan aus Liste generieren
    document.getElementById('btn-from-list')?.addEventListener('click', _openFromListModal);
    document.getElementById('btn-from-list-close')?.addEventListener('click', () =>
      document.getElementById('plan-from-list-overlay')?.classList.remove('active'));
    document.getElementById('btn-from-list-run')?.addEventListener('click', _generateFromList);
    document.getElementById('btn-from-list-load')?.addEventListener('click', _loadFromListPlan);

    // Automatisch strukturieren
    document.getElementById('btn-auto-structure')?.addEventListener('click', _openAutoStructure);
    document.getElementById('btn-auto-structure-close')?.addEventListener('click', () =>
      document.getElementById('auto-structure-overlay')?.classList.remove('active'));
    document.getElementById('btn-auto-struct-run')?.addEventListener('click', _computeAutoStructure);
    document.getElementById('btn-auto-struct-apply')?.addEventListener('click', _applyAutoStructure);
    document.getElementById('btn-auto-struct-undo')?.addEventListener('click', _undoAutoStructure);

    // Resize
    new ResizeObserver(_recalcAndRender).observe(_canvas.parentElement);

    // Ziehbarer Trenner Tabelle ↔ Netzplan
    _initSplitter();
    // Ziehbarer horizontaler Trenner über dem KI-Chatfenster
    _initHSplitter();

    // Tab-Wechsel
    document.querySelector('[data-tab="planner"]')?.addEventListener('click', () => {
      setTimeout(_recalcAndRender, 50);
    });

    // Initial
    _recalcAndRender();
  }

  // Von anderen Tabs (z. B. Anfrage-Auswertung) aufrufbar: Plan per ID laden und
  // in den Planer wechseln.
  async function openPlan(id) {
    await _loadPlanList();
    await _loadPlan(id);
    if (typeof switchTab === 'function') switchTab('planner');
  }

  return { init, openPlan };

})();
