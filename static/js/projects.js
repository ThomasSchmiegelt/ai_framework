/* AI_Framework_Thomas — Projektverwaltung */

const Projects = (() => {
  let _projects = [];
  let _activeProjectId = null;

  async function load() {
    try {
      const resp = await fetch('/api/projects');
      _projects = await resp.json();
    } catch (e) {
      _projects = [];
    }
    _renderSelector();
    return _projects;
  }

  function getAll() { return _projects; }
  function getActive() { return _activeProjectId; }

  function _renderSelector() {
    const sel = document.getElementById('project-select');
    if (!sel) return;
    const prev = sel.value;
    sel.innerHTML = '<option value="">— Alle Projekte —</option>';
    for (const p of _projects) {
      const opt = document.createElement('option');
      opt.value = p.id;
      opt.textContent = p.number ? `[${p.number}] ${p.name}` : p.name;
      sel.appendChild(opt);
    }
    if (prev) sel.value = prev;
  }

  function setActive(projectId) {
    _activeProjectId = projectId || null;
    if (typeof Chat !== 'undefined') Chat.loadConversationList();
  }

  function openModal() {
    _renderProjectList();
    document.getElementById('project-modal-overlay').classList.add('active');
  }

  function closeModal() {
    document.getElementById('project-modal-overlay').classList.remove('active');
  }

  async function _renderProjectList() {
    const list = document.getElementById('project-list');
    if (!list) return;
    list.innerHTML = '';
    if (_projects.length === 0) {
      list.innerHTML = '<div style="color:var(--text-dim);font-size:13px">Noch keine Projekte.</div>';
      return;
    }
    // Projekt-gebundene Skill-Agenten einmalig laden und je Projekt gruppieren.
    let allAgents = [];
    try { allAgents = await (await fetch('/api/agents')).json(); } catch (_) {}
    const skillsByProject = {};
    for (const a of allAgents) {
      if (a.project_id) (skillsByProject[a.project_id] ||= []).push(a);
    }
    for (const p of _projects) {
      const skills = skillsByProject[p.id] || [];
      const skillsHtml = skills.length
        ? `<div class="project-skills" style="margin-top:4px;display:flex;flex-wrap:wrap;gap:4px;align-items:center">
             <span style="color:var(--text-dim);font-size:12px">🧩 Skills:</span>`
          + skills.map(a => `<span class="tool-badge" title="${escHtml(a.description || '')}">${(a.icon || '🤖')} ${escHtml(a.name)}</span>`).join('')
          + `</div>`
        : '';
      // Vom Orchestrator (/projekt) verknüpfte Artefakte als Schnell-Buttons.
      const L = (p.links && typeof p.links === 'object') ? p.links : {};
      const lb = [];
      if (L.plan_id) lb.push(`<button class="tool-badge project-link-btn" data-act="plan" data-val="${escHtml(L.plan_id)}" title="Plan öffnen">📅 Plan</button>`);
      if (L.varianten) lb.push(`<button class="tool-badge project-link-btn" data-act="varianten" title="Variantenvergleich">🧮 Varianten</button>`);
      if (L.patente) lb.push(`<button class="tool-badge project-link-btn" data-act="patente" title="Patente">⚖ Patente</button>`);
      if (L.todo_pid) lb.push(`<button class="tool-badge project-link-btn" data-act="todo" title="To-Do">✅ To-Do</button>`);
      if (L.angebot_nr) lb.push(`<button class="tool-badge project-link-btn" data-act="angebot" title="Angebot/Rechnungen">🧾 Angebot</button>`);
      if (L.run) lb.push(`<button class="tool-badge project-link-btn" data-act="run" data-val="${escHtml(p.id)}" title="Gespeicherter Vorgang (JSON)">🗂 Vorgang</button>`);
      const linksHtml = lb.length
        ? `<div class="project-links" style="margin-top:4px;display:flex;flex-wrap:wrap;gap:4px;align-items:center">
             <span style="color:var(--text-dim);font-size:12px">🔗 Verknüpft:</span>${lb.join('')}</div>`
        : '';
      const row = document.createElement('div');
      row.className = 'project-row';
      row.innerHTML = `
        <div class="project-info">
          <span class="project-number">${escHtml(p.number || '')}</span>
          <span class="project-name">${escHtml(p.name)}</span>
          <span class="project-desc">${escHtml(p.description || '')}</span>
          ${skillsHtml}
          ${linksHtml}
        </div>
        <button class="btn-delete-project" data-id="${escHtml(p.id)}" data-skills="${skills.length}" title="Löschen">🗑</button>
      `;
      list.appendChild(row);
    }
    list.querySelectorAll('.project-link-btn').forEach(b => {
      b.addEventListener('click', () => {
        const act = b.dataset.act, val = b.dataset.val;
        if (act === 'run') { showToast('Gespeicherter Vorgang: data/orchestrator/' + (val || '') + '.json'); return; }
        closeModal();
        if (act === 'plan') { if (typeof Planner !== 'undefined' && Planner.openPlan && val) Planner.openPlan(val); else if (typeof switchTab === 'function') switchTab('planner'); }
        else if (act === 'varianten' && typeof switchTab === 'function') switchTab('varianten');
        else if (act === 'patente' && typeof switchTab === 'function') switchTab('patente');
        else if (act === 'todo' && typeof switchTab === 'function') switchTab('todo');
        else if (act === 'angebot' && typeof switchTab === 'function') switchTab('rechnung');
      });
    });
    list.querySelectorAll('.btn-delete-project').forEach(btn => {
      btn.addEventListener('click', async () => {
        const n = parseInt(btn.dataset.skills, 10) || 0;
        const msg = n ? `Projekt löschen? Die ${n} projekt-eigenen Skill-Agenten werden mitgelöscht.` : 'Projekt löschen?';
        if (!confirm(msg)) return;
        await fetch(`/api/projects/${btn.dataset.id}`, { method: 'DELETE' });
        await load();
        _renderProjectList();
        if (typeof AgentManager !== 'undefined' && AgentManager.load) { try { await AgentManager.load(); } catch (_) {} }
      });
    });
  }

  async function createProject() {
    const name   = document.getElementById('new-project-name').value.trim();
    const number = document.getElementById('new-project-number').value.trim();
    const desc   = document.getElementById('new-project-desc').value.trim();
    if (!name) { showToast('Bitte Projektname eingeben'); return; }
    await fetch('/api/projects', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name, number, description: desc }),
    });
    document.getElementById('new-project-name').value   = '';
    document.getElementById('new-project-number').value = '';
    document.getElementById('new-project-desc').value   = '';
    await load();
    _renderProjectList();
    showToast('Projekt angelegt');
  }

  async function assignCurrentChat(projectId) {
    const cid = window._currentConvId;
    if (!cid) { showToast('Kein aktiver Chat'); return; }
    await fetch(`/api/conversations/${cid}/project`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ project_id: projectId || null }),
    });
    showToast(projectId ? 'Projekt zugewiesen' : 'Projektzuordnung entfernt');
  }

  function init() {
    const btnManage = document.getElementById('btn-manage-projects');
    if (btnManage) btnManage.addEventListener('click', openModal);

    const btnClose = document.getElementById('btn-project-modal-close');
    if (btnClose) btnClose.addEventListener('click', closeModal);

    const btnCreate = document.getElementById('btn-create-project');
    if (btnCreate) btnCreate.addEventListener('click', createProject);

    const overlay = document.getElementById('project-modal-overlay');
    if (overlay) overlay.addEventListener('click', e => {
      if (e.target === overlay) closeModal();
    });

    const sel = document.getElementById('project-select');
    if (sel) sel.addEventListener('change', () => setActive(sel.value));

    const assignSel = document.getElementById('chat-project-select');
    if (assignSel) assignSel.addEventListener('change', () => assignCurrentChat(assignSel.value));

    load();
  }

  return { init, load, getAll, getActive, openModal, closeModal, assignCurrentChat };
})();
