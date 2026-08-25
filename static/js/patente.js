/* AI_Framework_Thomas — Tab „⚖️ Patente": Patent-Fallakten, semantische Suche
   (Framework-RAG, kein ChromaDB), 7-Stufen-Experten-Pipeline und Wissensgraph
   (Cytoscape.js). Portiert aus dem eigenständigen Streamlit-Tool ~/ai-project/patente.
   Modellwahl über die Profil-Rolle „Wissenschaftlich (Recherche)"
   (Profile.modelFor('science')) — dort zugewiesene API-Modelle werden genutzt;
   nur der Profil-Schalter „Web-Recherche lokal" biegt die Analyse lokal um. */

const Patente = (() => {

  const $ = id => document.getElementById(id);

  let _project  = '';
  let _projects = [];
  let _patente  = [];
  let _analyses = [];
  let _detailId = '';
  let _cy       = null;

  const _model = () => (typeof Profile !== 'undefined' && Profile.modelFor) ? Profile.modelFor('science') : undefined;

  function _status(txt) { const el = $('pat-status'); if (el) el.textContent = txt || ''; }
  function _spin(on)    { const el = $('pat-spin');   if (el) el.style.display = on ? '' : 'none'; }

  function _needProject() {
    if (!_project) { _status('Bitte zuerst ein Projekt wählen/anlegen.'); return true; }
    return false;
  }

  async function _json(url, opts) {
    const resp = await fetch(url, opts);
    let d = {};
    try { d = await resp.json(); } catch (_) { /* ignore */ }
    if (!resp.ok) throw new Error(d.detail || ('HTTP ' + resp.status));
    return d;
  }

  /* ── SSE-über-POST (gemeinsames Muster, wie chat.js/_search_expand) ───── */
  async function _sse(url, body, onEvent) {
    const resp = await fetch(url, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    if (!resp.ok || !resp.body) throw new Error('HTTP ' + resp.status);
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
        let ev;
        try { ev = JSON.parse(line.slice(6)); } catch (_) { continue; }
        onEvent(ev);
      }
    }
  }

  /* ── Projektverwaltung ─────────────────────────────────────────────── */
  async function _loadProjects() {
    try {
      const d = await _json('/api/patente/projects');
      _projects = d.projects || [];
    } catch (e) {
      _status('Projekte konnten nicht geladen werden: ' + e.message);
      _projects = [];
    }
    const sel = $('pat-project');
    if (!sel) return;
    const cur = _project;
    sel.innerHTML = '<option value="">— Projekt wählen —</option>' +
      _projects.map(p => `<option value="${escHtml(p.name)}">${escHtml(p.name)} (${p.count})</option>`).join('');
    if (cur && _projects.some(p => p.name === cur)) sel.value = cur;
    else if (_projects.length && !cur) { /* nichts automatisch wählen */ }
  }

  async function _selectProject(name) {
    _project = name || '';
    _detailId = '';
    if (!_project) { _patente = []; _analyses = []; _renderAkte(); _renderAnalyses(); _renderAnalyseSelect(); return; }
    _status('⏳ Lade Fallakte…');
    await Promise.all([_loadAkte(), _loadAnalyses()]);
    _status('');
    _renderAkte();
    _renderAnalyses();
    _renderAnalyseSelect();
  }

  async function _createProject() {
    const name = ($('pat-new-project')?.value || '').trim();
    if (!name) { _status('Bitte einen Projektnamen eingeben.'); return; }
    try {
      const d = await _json('/api/patente/projects', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name }),
      });
      $('pat-new-project').value = '';
      await _loadProjects();
      $('pat-project').value = d.name;
      await _selectProject(d.name);
      _status(`✓ Projekt „${d.name}" angelegt.`);
    } catch (e) {
      _status('Anlegen fehlgeschlagen: ' + e.message);
    }
  }

  async function _deleteProject() {
    if (_needProject()) return;
    if (!confirm(`Projekt „${_project}" inkl. aller Patente und Analysen unwiderruflich löschen?`)) return;
    try {
      await _json(`/api/patente/projects/${encodeURIComponent(_project)}`, { method: 'DELETE' });
      _project = '';
      await _loadProjects();
      await _selectProject('');
      _status('Projekt gelöscht.');
    } catch (e) {
      _status('Löschen fehlgeschlagen: ' + e.message);
    }
  }

  /* ── Fallakte ───────────────────────────────────────────────────────── */
  async function _loadAkte() {
    try {
      const d = await _json(`/api/patente/projects/${encodeURIComponent(_project)}`);
      _patente = d.patente || [];
    } catch (e) {
      _status('Fallakte konnte nicht geladen werden: ' + e.message);
      _patente = [];
    }
  }

  // Sortierung der Akte-Tabelle: Klick auf „Score" sortiert absteigend/aufsteigend
  let _akteSort = null;   // null = Einfügereihenfolge, 'desc' | 'asc' = nach Score

  function _scoreBadge(k) {
    if (!k || typeof k.score !== 'number') return '–';
    const cls = k.score >= 60 ? 'ok' : (k.score >= 30 ? 'warn' : '');
    return `<span class="pat-badge ${cls}" title="Triage-Score (deterministisch): Zitate/Familie/Restlaufzeit/Anspruchsbreite">${k.score}</span>`;
  }

  function _renderAkte() {
    const host = $('pat-akte-table');
    if (!host) return;
    if (!_project) { host.innerHTML = '<p class="planner-muted">Kein Projekt gewählt.</p>'; return; }
    if (!_patente.length) { host.innerHTML = '<p class="planner-muted">Die Akte ist leer — Patente über „Import" hinzufügen.</p>'; return; }
    let list = [..._patente];
    if (_akteSort) {
      list.sort((a, b) => ((b.kennzahlen?.score || 0) - (a.kennzahlen?.score || 0)) * (_akteSort === 'desc' ? 1 : -1));
    }
    const rows = list.map(p => `
      <tr class="pat-row" data-pid="${escHtml(p.patent_id)}">
        <td>${escHtml(p.patent_id)}</td>
        <td>${escHtml((p.title || '').slice(0, 80))}</td>
        <td>${_scoreBadge(p.kennzahlen)}</td>
        <td>${escHtml((p.ipc_klassen || []).join(', '))}</td>
        <td>${escHtml((p.rechteinhaber || []).join(', '))}</td>
        <td>${escHtml((p.publication_date || p.scraped_at || '').slice(0, 10))}</td>
      </tr>`).join('');
    host.innerHTML = `
      <table class="pat-table"><thead><tr>
        <th>Nummer</th><th>Titel</th>
        <th id="pat-th-score" style="cursor:pointer" title="Nach Stärke-Score sortieren">Score ${_akteSort === 'desc' ? '▼' : (_akteSort === 'asc' ? '▲' : '↕')}</th>
        <th>IPC</th><th>Rechteinhaber</th><th>Datum</th>
      </tr></thead><tbody>${rows}</tbody></table>`;
    host.querySelector('#pat-th-score')?.addEventListener('click', () => {
      _akteSort = _akteSort === 'desc' ? 'asc' : 'desc';
      _renderAkte();
    });
    host.querySelectorAll('.pat-row').forEach(tr => {
      tr.addEventListener('click', () => _showDetail(tr.dataset.pid));
    });
    if (_detailId) _showDetail(_detailId);
  }

  // Gemeinsame Metadaten-Zeilen (amtliche Felder, soweit vorhanden) für
  // Akte-Detail und Lese-Modal — mit OPS-Key gefüllt, sonst Google-Teilmenge.
  function _metaRows(p) {
    const legal = (p.legal_status || []).slice(0, 6)
      .map(e => `${escHtml(e.date || '')} ${escHtml(e.desc || e.code || '')}`.trim()).join('<br>');
    const rows = [];
    const dates = [
      p.filing_date ? `Anmeldung ${escHtml(p.filing_date)}` : '',
      p.priority_date ? `Priorität ${escHtml(p.priority_date)}` : '',
      p.publication_date ? `Publikation ${escHtml(p.publication_date)}` : '',
    ].filter(Boolean).join(' · ');
    if (dates) rows.push(`<p><strong>Daten:</strong> ${dates}</p>`);
    rows.push(`<p><strong>IPC:</strong> ${escHtml((p.ipc_klassen || []).join(', ')) || '–'}`
      + ((p.cpc_klassen || []).length ? ` &nbsp;<strong>CPC:</strong> ${escHtml(p.cpc_klassen.join(', '))}` : '') + '</p>');
    rows.push(`<p><strong>Rechteinhaber:</strong> ${escHtml((p.rechteinhaber || []).join(', ')) || '–'}`
      + ((p.inventors || []).length ? ` &nbsp;<strong>Erfinder:</strong> ${escHtml(p.inventors.join(', '))}` : '') + '</p>');
    if (legal) rows.push(`<details><summary><strong>🏛 Rechtsstand (${(p.legal_status || []).length} Ereignisse)</strong></summary><p style="font-size:12px">${legal}</p></details>`);
    if ((p.family || []).length) rows.push(`<details><summary><strong>👪 Patentfamilie (${p.family.length})</strong></summary><p style="font-size:12px">${escHtml(p.family.join(', '))}</p></details>`);
    if (p.source) rows.push(`<p class="planner-muted" style="font-size:11px">Quelle: ${p.source.indexOf('epo') === 0 ? '🏛 EPO OPS (amtlich)' + (p.source.includes('google') ? ' + Google' : '') : '🌐 Google Patents'}</p>`);
    const k = p.kennzahlen;
    if (k && typeof k.score === 'number') {
      rows.push(`<p><strong>📈 Stärke (Triage):</strong> ${_scoreBadge(k)}
        <span class="planner-muted" style="font-size:11.5px">
        Restlaufzeit ${k.restlaufzeit_jahre != null ? k.restlaufzeit_jahre + ' J.' : '?'} ·
        ${k.vorwaertszitate} Vorwärtszitate · Familie ${k.familie} ·
        ${k.anspruchszahl} Ansprüche · Anspruch 1: ${k.anspruch1_worte} Wörter ·
        Rechtsstand: ${escHtml(k.rechtsstand_hinweis || 'unbekannt')}</span></p>`);
    }
    return rows.join('');
  }

  function _showDetail(pid) {
    _detailId = pid;
    const p = _patente.find(x => x.patent_id === pid);
    const host = $('pat-detail');
    if (!host) return;
    if (!p) { host.innerHTML = ''; return; }
    const zitate = p.zitate || [];
    const zvon = p.zitiert_von || [];
    host.innerHTML = `
      <div class="pat-detail-box">
        <h3>${escHtml(p.title || p.patent_id)} (${escHtml(p.patent_id)})</h3>
        ${_metaRows(p)}
        <p><strong>Zusammenfassung</strong><br>${escHtml(p.abstract || '')}</p>
        <p><strong>Ansprüche</strong><br>${escHtml((p.claims || '').slice(0, 4000))}</p>
        <p><strong>Zitierte Dokumente (${zitate.length})</strong><br>${escHtml(zitate.join(', ')) || '–'}
          ${zitate.length ? '<br><button id="btn-pat-cite-import" class="export-btn">🔄 Zitate importieren</button>' : ''}</p>
        ${zvon.length ? `<p><strong>Zitiert von (${zvon.length})</strong><br>${escHtml(zvon.join(', '))}</p>` : ''}
        <p><a href="${escHtml(p.url || '#')}" target="_blank" rel="noopener noreferrer">🔗 Google Patents</a></p>
      </div>`;
    $('btn-pat-cite-import')?.addEventListener('click', () => _importCitations(pid));
  }

  function _exportJson() {
    if (_needProject()) return;
    window.open(`/api/patente/projects/${encodeURIComponent(_project)}/export.json`, '_blank');
  }
  function _exportCsv() {
    if (_needProject()) return;
    window.open(`/api/patente/projects/${encodeURIComponent(_project)}/export.csv`, '_blank');
  }

  async function _importCitations(pid) {
    _status('⏳ Importiere Zitate…'); _spin(true);
    try {
      const d = await _json(`/api/patente/projects/${encodeURIComponent(_project)}/import/citations`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ patent_id: pid }),
      });
      await _loadAkte(); _renderAkte(); _renderAnalyseSelect();
      _status(`✓ ${d.imported.length} Zitat(e) importiert${d.failed.length ? `, ${d.failed.length} fehlgeschlagen` : ''}.`);
    } catch (e) {
      _status('Zitat-Import fehlgeschlagen: ' + e.message);
    } finally { _spin(false); }
  }

  /* ── EPO-OPS-Zugang (amtliche Datenquelle) ──────────────────────────── */
  async function _loadOpsStatus() {
    const el = $('pat-ops-status');
    if (!el) return;
    try {
      const d = await _json('/api/patente/ops-config');
      el.textContent = d.configured ? `✓ verbunden (${d.key_masked})` : '— nicht verbunden (Google-Fallback)';
    } catch (_) { el.textContent = ''; }
  }

  async function _saveOps() {
    const key = ($('pat-ops-key')?.value || '').trim();
    const secret = ($('pat-ops-secret')?.value || '').trim();
    if (!key || !secret) { _status('Bitte Consumer Key UND Secret eingeben.'); return; }
    _status('⏳ Prüfe EPO-OPS-Zugang…'); _spin(true);
    try {
      const d = await _json('/api/patente/ops-config', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ consumer_key: key, consumer_secret: secret }),
      });
      $('pat-ops-key').value = ''; $('pat-ops-secret').value = '';
      _status(d.message || '✓ Verbunden.');
      _loadOpsStatus();
    } catch (e) {
      _status('EPO OPS: ' + e.message);
    } finally { _spin(false); }
  }

  async function _clearOps() {
    if (!confirm('EPO-OPS-Zugang entfernen? Abrufe laufen dann wieder über Google Patents.')) return;
    try {
      const d = await _json('/api/patente/ops-config', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ consumer_key: '', consumer_secret: '' }),
      });
      _status(d.message || 'Zugang entfernt.');
      _loadOpsStatus();
    } catch (e) { _status('Fehler: ' + e.message); }
  }

  /* ── Import ─────────────────────────────────────────────────────────── */
  async function _lookupSingle() {
    if (_needProject()) return;
    const pid = ($('pat-import-id')?.value || '').trim();
    if (!pid) { _status('Bitte eine Patentnummer eingeben.'); return; }
    _status('⏳ Lade Patent…'); _spin(true);
    try {
      const d = await _json(`/api/patente/projects/${encodeURIComponent(_project)}/import/lookup`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ patent_id: pid }),
      });
      $('pat-import-id').value = '';
      await _loadAkte(); await _loadProjects(); _renderAkte(); _renderAnalyseSelect();
      _status(`✓ „${d.patent.title}" übernommen (${d.count} Dokument(e) in der Akte).`);
    } catch (e) {
      _status('Abruf fehlgeschlagen: ' + e.message);
    } finally { _spin(false); }
  }

  async function _searchTerm() {
    const term = ($('pat-search-term')?.value || '').trim();
    const assignee = ($('pat-search-assignee')?.value || '').trim();
    const country = ($('pat-search-country')?.value || '').trim();
    const ipc = ($('pat-search-ipc')?.value || '').trim();
    const dateFrom = ($('pat-search-from')?.value || '').trim();
    const dateTo = ($('pat-search-to')?.value || '').trim();
    const maxResults = Math.max(1, Math.min(parseInt($('pat-search-max')?.value, 10) || 20, 50));
    if (!term && !assignee && !ipc) { _status('Bitte Suchbegriff, Rechteinhaber oder IPC-Klasse angeben.'); return; }
    _status('⏳ Suche läuft…'); _spin(true);
    try {
      const d = await _json('/api/patente/search', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ term, assignee, country, ipc, date_from: dateFrom,
                               date_to: dateTo, max_results: maxResults }),
      });
      const host = $('pat-search-results');
      const results = d.results || [];
      const srcBadge = d.source === 'epo_ops'
        ? '<span class="pat-badge ok" title="amtliche EPO-OPS-Suche">🏛 EPO</span>'
        : '<span class="pat-badge warn" title="Google-Patents-Fallback">🌐 Google</span>';
      if (d.error) _status('⚠ ' + d.error);
      if (!results.length) { host.innerHTML = `<p class="planner-muted">${srcBadge} Keine Treffer.${d.error ? ' (' + escHtml(d.error) + ')' : ''}</p>`; }
      else {
        const bar = `<div class="pat-search-bar">
          ${srcBadge}
          <span class="planner-muted" style="font-size:11.5px">„Übernehmen" legt das Patent in die Massenverarbeitung — dann unten „⚙️ Stapelverarbeitung starten".</span>
          <button class="export-btn" id="pat-add-all" style="margin-left:auto">➕ Alle in Stapel</button>
        </div>`;
        host.innerHTML = bar + results.map(r => `
          <div class="pat-search-hit">
            <strong>${escHtml(r.patent_id)}</strong> — ${escHtml((r.title || '').slice(0, 90))}
            ${r.publication_date ? `<span class="planner-muted" style="font-size:11px">📅 ${escHtml(r.publication_date)}</span>` : ''}
            <button class="export-btn pat-view-hit" data-pid="${escHtml(r.patent_id)}">👁 Ansehen</button>
            <button class="export-btn pat-add-hit" data-pid="${escHtml(r.patent_id)}">➕ Übernehmen</button>
          </div>`).join('');
        host.querySelectorAll('.pat-view-hit').forEach(btn => {
          btn.addEventListener('click', () => _previewPatent(btn.dataset.pid));
        });
        host.querySelectorAll('.pat-add-hit').forEach(btn => {
          btn.addEventListener('click', () => {
            const pid = btn.dataset.pid;
            if (!pid) return;
            const n = _addToBatch(pid);
            btn.textContent = '✓ im Stapel'; btn.disabled = true;
            _status(`„${pid}" in die Massenverarbeitung übernommen (${n} im Stapel). Mit „⚙️ Stapelverarbeitung starten" auswerten.`);
          });
        });
        $('pat-add-all')?.addEventListener('click', () => {
          let n = 0;
          results.forEach(r => { if (r.patent_id) n = _addToBatch(r.patent_id); });
          host.querySelectorAll('.pat-add-hit').forEach(b => { b.textContent = '✓ im Stapel'; b.disabled = true; });
          _status(`${results.length} Treffer in die Massenverarbeitung übernommen (${n} im Stapel). Mit „⚙️ Stapelverarbeitung starten" auswerten.`);
        });
      }
      _status(`✓ ${results.length} Treffer (${d.source === 'epo_ops' ? 'EPO amtlich' : 'Google'}).`
        + (d.error ? ` ⚠ ${d.error}` : ''));
    } catch (e) {
      _status('Suche fehlgeschlagen: ' + e.message);
    } finally { _spin(false); }
  }

  // Legt eine Patentnummer in die Massenverarbeitung (Textfeld). Dedupliziert,
  // scrollt das Feld in Sicht und liefert die neue Stapelgröße zurück.
  function _addToBatch(pid) {
    const ta = $('pat-csv-text');
    if (!ta) return 0;
    const nums = ta.value.split(/[\n,;]+/).map(s => s.trim()).filter(Boolean);
    if (!nums.includes(pid)) nums.push(pid);
    ta.value = nums.join('\n');
    try { ta.scrollIntoView({ behavior: 'smooth', block: 'nearest' }); } catch (_) {}
    return nums.length;
  }

  /* ── Patente lesen (Volltext-Vorschau vor der Verarbeitung) ─────────── */
  const _previewCache = {};

  function _detailHtml(p) {
    const zitate = p.zitate || [];
    return `
      <h3>${escHtml(p.title || p.patent_id)} <span class="planner-muted">(${escHtml(p.patent_id)})</span></h3>
      ${_metaRows(p)}
      <p><strong>Zusammenfassung</strong><br>${escHtml(p.abstract || '')}</p>
      <p><strong>Ansprüche</strong><br><span style="white-space:pre-wrap">${escHtml((p.claims || '').slice(0, 8000))}</span></p>
      <p><strong>Zitierte Dokumente (${zitate.length}):</strong> ${escHtml(zitate.join(', ')) || '–'}</p>
      <p><a href="${escHtml(p.url || '#')}" target="_blank" rel="noopener noreferrer">🔗 Google Patents</a></p>`;
  }

  function _openPreview(html) {
    const modal = $('pat-preview-modal'), body = $('pat-preview-body');
    if (!modal || !body) return;
    body.innerHTML = html;
    modal.style.display = 'flex';
  }
  function _closePreview() { const m = $('pat-preview-modal'); if (m) m.style.display = 'none'; }

  async function _previewPatent(pid) {
    if (!pid) return;
    if (_previewCache[pid]) { _openPreview(_detailHtml(_previewCache[pid])); return; }
    // Bereits in der Akte? Dann ohne Netz aus dem lokalen Datensatz lesen.
    const local = _patente.find(x => x.patent_id === pid);
    if (local && (local.abstract || local.claims)) { _previewCache[pid] = local; _openPreview(_detailHtml(local)); return; }
    _openPreview('<em>⏳ Lade Patent…</em>');
    try {
      const d = await _json('/api/patente/preview', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ patent_id: pid }),
      });
      _previewCache[pid] = d;
      _openPreview(_detailHtml(d));
    } catch (e) {
      _openPreview(`<em style="color:#ef4444">Fehler: ${escHtml(e.message)}</em>`);
    }
  }

  // Listet die Nummern aus der Massenverarbeitung als lesbare Zeilen mit „Ansehen".
  function _readBatch() {
    const raw = ($('pat-csv-text')?.value || '').trim();
    const host = $('pat-read-list');
    if (!host) return;
    const nums = raw.split(/[\n,;]+/).map(s => s.trim()).filter(Boolean);
    if (!nums.length) { host.innerHTML = '<p class="planner-muted">Keine Patentnummern im Stapel.</p>'; return; }
    host.innerHTML = nums.map(n => `
      <div class="pat-search-hit">
        <strong>${escHtml(n)}</strong>
        <button class="export-btn pat-view-batch" data-pid="${escHtml(n)}" style="margin-left:auto">👁 Ansehen</button>
      </div>`).join('');
    host.querySelectorAll('.pat-view-batch').forEach(btn => {
      btn.addEventListener('click', () => _previewPatent(btn.dataset.pid));
    });
  }

  async function _importCsv() {
    if (_needProject()) return;
    const raw = ($('pat-csv-text')?.value || '').trim();
    if (!raw) { _status('Bitte Patentnummern einfügen (eine je Zeile oder Komma-getrennt).'); return; }
    const numbers = raw.split(/[\n,;]+/).map(s => s.trim()).filter(Boolean);
    if (!numbers.length) { _status('Keine gültigen Nummern gefunden.'); return; }
    _status(`⏳ Importiere ${numbers.length} Patent(e)…`); _spin(true);
    try {
      const d = await _json(`/api/patente/projects/${encodeURIComponent(_project)}/import/csv`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ numbers }),
      });
      $('pat-csv-text').value = '';
      await _loadAkte(); await _loadProjects(); _renderAkte(); _renderAnalyseSelect();
      _status(`✓ ${d.imported.length} importiert${d.failed.length ? `, ${d.failed.length} fehlgeschlagen (${d.failed.join(', ')})` : ''}.`);
    } catch (e) {
      _status('Batch-Import fehlgeschlagen: ' + e.message);
    } finally { _spin(false); }
  }

  async function _importJsonFile(file) {
    if (_needProject() || !file) return;
    _status('⏳ Lese JSON-Datei…'); _spin(true);
    try {
      const text = await file.text();
      const items = JSON.parse(text);
      if (!Array.isArray(items) || !items.length) throw new Error('Erwartet wird eine Liste von Patent-Datensätzen.');
      const d = await _json(`/api/patente/projects/${encodeURIComponent(_project)}/import/json`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ items }),
      });
      await _loadAkte(); await _loadProjects(); _renderAkte(); _renderAnalyseSelect();
      _status(`✓ ${d.imported} Dokument(e) importiert.`);
    } catch (e) {
      _status('JSON-Import fehlgeschlagen: ' + e.message);
    } finally { _spin(false); }
  }

  async function _migrate() {
    const src = ($('pat-migrate-src')?.value || '').trim();
    if (!src) { _status('Bitte den Quellpfad der alten Fallakten angeben.'); return; }
    const host = $('pat-migrate-log');
    host.innerHTML = '';
    _status('⏳ Migriere bestehende Akten…'); _spin(true);
    const log = m => { host.innerHTML += `<div>${escHtml(m)}</div>`; host.scrollTop = host.scrollHeight; };
    try {
      await _sse('/api/patente/migrate', { source_dir: src }, ev => {
        if (ev.type === 'copied') log(`📄 ${ev.migrated.length} Projekt(e) kopiert${ev.skipped.length ? `, ${ev.skipped.length} übersprungen` : ''}.`);
        else if (ev.type === 'project_start') log(`▶ ${ev.project} (${ev.count} Patente) — indiziere…`);
        else if (ev.type === 'progress') log(`  … ${ev.indexed}/${ev.total}`);
        else if (ev.type === 'project_done') log(`✓ ${ev.project} fertig.`);
        else if (ev.type === 'project_error') log(`⚠ ${ev.project}: ${ev.message}`);
        else if (ev.type === 'error') log(`✗ Fehler: ${ev.message}`);
        else if (ev.type === 'done') log('✅ Migration abgeschlossen.');
      });
      await _loadProjects();
      _status('✓ Migration abgeschlossen.');
    } catch (e) {
      _status('Migration fehlgeschlagen: ' + e.message);
    } finally { _spin(false); }
  }

  /* ── Chat (RAG-Frage ans Projekt) ──────────────────────────────────── */
  async function _ask() {
    if (_needProject()) return;
    const question = ($('pat-ask-input')?.value || '').trim();
    if (!question) return;
    const host = $('pat-ask-answer');
    host.innerHTML = '<em>⏳ Durchsuche Projekt-Akte…</em>';
    try {
      const d = await _json(`/api/patente/projects/${encodeURIComponent(_project)}/ask`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ question, model: _model() }),
      });
      if (d.tokens && typeof TokenMeter !== 'undefined') TokenMeter.add(d.tokens, 'Patente');
      const src = (d.sources || []).map(s => `<li>${escHtml(s.filename)}</li>`).join('');
      host.innerHTML = `<div class="pat-ask-a">${escHtml(d.answer)}</div>` +
        (src ? `<details><summary>📚 Quellen</summary><ul>${src}</ul></details>` : '');
    } catch (e) {
      host.innerHTML = `<em style="color:#ef4444">Fehler: ${escHtml(e.message)}</em>`;
    }
  }

  /* ── Analyse (7-Stufen-Pipeline) ───────────────────────────────────── */
  function _renderAnalyseSelect() {
    const host = $('pat-analyse-select');
    if (!host) return;
    if (!_patente.length) { host.innerHTML = '<p class="planner-muted">Keine Patente in der Akte.</p>'; return; }
    host.innerHTML = _patente.map(p => `
      <label class="pat-check-row">
        <input type="checkbox" class="pat-analyse-cb" value="${escHtml(p.patent_id)}" />
        ${escHtml(p.patent_id)} — ${escHtml((p.title || '').slice(0, 60))}
      </label>`).join('');
  }

  async function _runAnalysis() {
    if (_needProject()) return;
    const ids = Array.from(document.querySelectorAll('.pat-analyse-cb:checked')).map(cb => cb.value);
    if (!ids.length) { _status('Bitte mindestens ein Dokument auswählen.'); return; }
    const out = $('pat-analyse-result');
    out.innerHTML = '<p class="planner-muted">⏳ Pipeline gestartet…</p>';
    const btn = $('btn-pat-analyze');
    if (btn) btn.disabled = true;
    try {
      let lastMsg = '';
      await _sse(`/api/patente/projects/${encodeURIComponent(_project)}/analyze`, {
        patent_ids: ids, model: _model(),
      }, ev => {
        if (ev.type === 'progress') {
          lastMsg = ev.message;
          out.innerHTML = `<p class="planner-muted">⏳ ${escHtml(lastMsg)}</p>`;
        } else if (ev.type === 'error') {
          out.innerHTML = `<p style="color:#ef4444">Fehler: ${escHtml(ev.message)}</p>`;
        } else if (ev.type === 'done') {
          if (ev.tokens && typeof TokenMeter !== 'undefined') TokenMeter.add(ev.tokens, 'Patente');
          out.innerHTML = _renderErgebnisse(ev.ergebnisse);
          _loadAnalyses().then(_renderAnalyses);
        }
      });
    } catch (e) {
      out.innerHTML = `<p style="color:#ef4444">Fehler: ${escHtml(e.message)}</p>`;
    } finally {
      if (btn) btn.disabled = false;
    }
  }

  // Stufen mit Markdown-Tabellen werden gerendert (marked), reine Textstufen escaped.
  function _stageBody(text, forceMd) {
    const t = text || '–';
    const hasTable = /(^|\n)\s*\|.*\|/.test(t);
    if ((forceMd || hasTable) && typeof marked !== 'undefined') {
      return `<div class="pat-merkmale-table">${marked.parse(t, { gfm: true })}</div>`;
    }
    return escHtml(t);
  }

  function _renderErgebnisse(erg) {
    if (!erg) return '';
    const isFto = Object.keys(erg).some(k => k.indexOf('fto_') === 0);
    const badges = [];
    if ('pruefung_bestanden' in erg) {
      badges.push(erg.pruefung_bestanden
        ? '<span class="pat-badge ok">✓ technisch validiert</span>'
        : '<span class="pat-badge warn">⚠ Prüfschleife nicht freigegeben — mit Vorbehalt</span>');
    }
    if ('merkmale' in erg) {
      badges.push(erg.merkmale_geprueft
        ? '<span class="pat-badge ok">✓ Merkmale validiert</span>'
        : '<span class="pat-badge warn">⚠ Merkmalsanalyse mit Vorbehalt</span>');
    }
    if ('neuheit' in erg) {
      badges.push(erg.neuheit_geprueft
        ? '<span class="pat-badge ok">✓ Neuheitsprüfung validiert</span>'
        : '<span class="pat-badge warn">⚠ Neuheitsprüfung mit Vorbehalt</span>');
    }
    if (isFto) {
      badges.push(erg.fto_geprueft
        ? '<span class="pat-badge ok">✓ FTO-Tabellen validiert</span>'
        : '<span class="pat-badge warn">⚠ FTO-Check mit Vorbehalt</span>');
    }

    const parts = [];
    if (isFto) {
      // FTO-Check: Fazit zuerst, dann die Ampel-Tabellen je Patent
      if (erg.fto_fazit) parts.push(['🛡 FTO-Fazit', erg.fto_fazit, true]);
      Object.keys(erg).filter(k => k.indexOf('fto_') === 0 && !['fto_fazit', 'fto_geprueft'].includes(k))
        .forEach(k => parts.push([`📋 Claim-Chart ${k.slice(4)}`, erg[k], true]));
      if (erg.produktbeschreibung) parts.push(['📦 Geprüfte Produktbeschreibung', erg.produktbeschreibung, false]);
    } else {
      [['moderator', '📊 Moderator (Zusammenfassung)'], ['technik', '⚙️ Technik'],
       ['merkmale', '📐 Merkmalsanalyse (Anspruch 1)'],
       ['neuheit', '🧪 Neuheit & erfinderische Tätigkeit'],
       ['recht', '⚖️ Recht'], ['umgehung', '🚧 Umgehung'], ['innovation', '💡 Innovation'],
       ['entwurf', '📝 Entwurf'], ['kritik', '🛡️ Kritik'],
      ].forEach(([k, label]) => { if (k in erg) parts.push([label, erg[k], k === 'merkmale']); });
    }
    return badges.join(' ') + parts.map(([label, text, md]) =>
      `<div class="pat-stage"><h4>${label}</h4><div>${_stageBody(text, md)}</div></div>`).join('');
  }

  /* ── FTO-Produkt-Check ─────────────────────────────────────────────── */
  async function _runFto() {
    if (_needProject()) return;
    const ids = Array.from(document.querySelectorAll('.pat-analyse-cb:checked')).map(cb => cb.value);
    if (!ids.length) { _status('Bitte oben mindestens ein Patent auswählen.'); return; }
    const produkt = ($('pat-fto-produkt')?.value || '').trim();
    if (produkt.length < 30) { _status('Bitte das eigene Produkt/die Idee ausführlicher beschreiben.'); return; }
    const out = $('pat-analyse-result');
    out.innerHTML = '<p class="planner-muted">⏳ FTO-Check gestartet…</p>';
    const btn = $('btn-pat-fto');
    if (btn) btn.disabled = true;
    try {
      await _sse(`/api/patente/projects/${encodeURIComponent(_project)}/fto`, {
        patent_ids: ids, produkt, model: _model(),
      }, ev => {
        if (ev.type === 'progress') {
          out.innerHTML = `<p class="planner-muted">⏳ ${escHtml(ev.message)}</p>`;
        } else if (ev.type === 'error') {
          out.innerHTML = `<p style="color:#ef4444">Fehler: ${escHtml(ev.message)}</p>`;
        } else if (ev.type === 'done') {
          if (ev.tokens && typeof TokenMeter !== 'undefined') TokenMeter.add(ev.tokens, 'Patente');
          out.innerHTML = _renderErgebnisse(ev.ergebnisse);
          _loadAnalyses().then(_renderAnalyses);
        }
      });
    } catch (e) {
      out.innerHTML = `<p style="color:#ef4444">Fehler: ${escHtml(e.message)}</p>`;
    } finally {
      if (btn) btn.disabled = false;
    }
  }

  /* ── Skizzen/Figuren (Entwurf, kein Einreichen) ─────────────────────── */
  async function _renderPatMermaid(el, def) {
    if (typeof mermaid === 'undefined') { el.textContent = def; return; }
    try { mermaid.initialize({ startOnLoad: false, theme: 'dark' }); } catch (_) {}
    try {
      const { svg } = await mermaid.render('pm-' + Date.now() + Math.random().toString(36).slice(2, 6), def);
      el.innerHTML = svg;
    } catch (_) { el.textContent = def; }
  }

  async function _runSketches() {
    if (_needProject()) return;
    const desc = ($('pat-fto-produkt')?.value || '').trim();
    if (desc.length < 30) { _status('Bitte die Erfindung oben (Aufbau/Komponenten/Funktion) ausführlicher beschreiben.'); return; }
    const out = $('pat-analyse-result');
    out.innerHTML = '<p class="planner-muted">⏳ Skizzen werden erzeugt…</p>';
    const btn = $('btn-pat-sketches'); if (btn) btn.disabled = true;
    try {
      const r = await fetch(`/api/patente/projects/${encodeURIComponent(_project)}/sketches`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ description: desc, want_image: true, figures: 2 }),
      });
      if (!r.ok) { const e = await r.json().catch(() => ({})); throw new Error(e.detail || ('HTTP ' + r.status)); }
      const res = await r.json();
      if (res.tokens && typeof TokenMeter !== 'undefined') TokenMeter.add(res.tokens, 'Patente');
      out.innerHTML = '<div class="pat-stage"><h4>🖼 Skizzen (Entwurf – kein Einreichen)</h4><div id="pat-sketch-body"></div></div>';
      const body = $('pat-sketch-body');
      (res.figures || []).forEach(f => {
        const cap = document.createElement('div'); cap.style.cssText = 'font-weight:600;margin:8px 0 2px'; cap.textContent = f.caption || ''; body.appendChild(cap);
        if (f.mermaid) { const h = document.createElement('div'); h.style.cssText = 'overflow-x:auto'; body.appendChild(h); _renderPatMermaid(h, f.mermaid); }
        if (f.image) { const im = document.createElement('img'); im.src = f.image; im.style.cssText = 'max-width:100%;border-radius:6px;margin-top:4px'; body.appendChild(im); }
      });
      if ((res.bezugszeichen || []).length) {
        const bz = document.createElement('div'); bz.style.cssText = 'margin-top:8px;font-size:12.5px';
        bz.innerHTML = '<strong>Bezugszeichenliste:</strong> ' + res.bezugszeichen.map(z => escHtml((z.n || '') + ' — ' + (z.label || ''))).join(' · ');
        body.appendChild(bz);
      }
      if (res.description) { const d = document.createElement('div'); d.style.cssText = 'margin-top:6px;font-size:12.5px;opacity:.85'; d.textContent = res.description; body.appendChild(d); }
    } catch (e) {
      out.innerHTML = `<p style="color:#ef4444">Fehler: ${escHtml(e.message)}</p>`;
    } finally {
      if (btn) btn.disabled = false;
    }
  }

  /* ── Akte-Statistik (deterministisch, rein Frontend) ────────────────── */
  function _bars(counts, total, max) {
    return Object.entries(counts).sort((a, b) => b[1] - a[1]).slice(0, max || 10)
      .map(([label, n]) => {
        const pct = total ? Math.round(100 * n / total) : 0;
        return `<div class="pat-statbar"><span class="pat-statbar-label" title="${escHtml(label)}">${escHtml(label.slice(0, 34))}</span>
          <span class="pat-statbar-track"><span class="pat-statbar-fill" style="width:${Math.max(3, pct)}%"></span></span>
          <span class="pat-statbar-n">${n}</span></div>`;
      }).join('');
  }

  function _renderStatistik() {
    const host = $('pat-statistik');
    if (!host) return;
    if (!_patente.length) { host.innerHTML = '<p class="planner-muted">Keine Patente in der Akte.</p>'; return; }
    const n = _patente.length;

    const anmelder = {}, jahre = {}, ipc = {}, buckets = { '0–24': 0, '25–49': 0, '50–74': 0, '75–100': 0 };
    _patente.forEach(p => {
      (p.rechteinhaber || []).forEach(r => { anmelder[r] = (anmelder[r] || 0) + 1; });
      const jahr = ((p.filing_date || p.publication_date || '').slice(0, 4));
      if (/^\d{4}$/.test(jahr)) jahre[jahr] = (jahre[jahr] || 0) + 1;
      (p.ipc_klassen || []).forEach(c => { const h = c.slice(0, 3); if (h) ipc[h] = (ipc[h] || 0) + 1; });
      const s = p.kennzahlen?.score;
      if (typeof s === 'number') buckets[s < 25 ? '0–24' : s < 50 ? '25–49' : s < 75 ? '50–74' : '75–100']++;
    });

    // White-Space-Matrix: IPC-Hauptklassen × Top-Anmelder — leere Zellen = Lücke
    const topIpc = Object.entries(ipc).sort((a, b) => b[1] - a[1]).slice(0, 6).map(x => x[0]);
    const topAnm = Object.entries(anmelder).sort((a, b) => b[1] - a[1]).slice(0, 5).map(x => x[0]);
    let matrix = '';
    if (topIpc.length > 1 && topAnm.length > 1) {
      const cell = (rh, kl) => _patente.filter(p =>
        (p.rechteinhaber || []).includes(rh) && (p.ipc_klassen || []).some(c => c.indexOf(kl) === 0)).length;
      matrix = `<h4 style="margin:14px 0 6px">◻️ White-Space (IPC × Anmelder — leere Zellen = unbesetzt)</h4>
        <div class="pat-merkmale-table"><table><thead><tr><th></th>${topIpc.map(k => `<th>${escHtml(k)}</th>`).join('')}</tr></thead><tbody>
        ${topAnm.map(rh => `<tr><td title="${escHtml(rh)}">${escHtml(rh.slice(0, 24))}</td>${topIpc.map(k => {
          const c = cell(rh, k);
          return `<td style="text-align:center">${c ? c : '<span style="opacity:.35">◻</span>'}</td>`;
        }).join('')}</tr>`).join('')}
        </tbody></table></div>`;
    }

    // Jahre chronologisch (nicht nach Häufigkeit)
    const jahrBars = Object.keys(jahre).sort().map(j => {
      const maxJ = Math.max(...Object.values(jahre));
      const pct = Math.round(100 * jahre[j] / maxJ);
      return `<div class="pat-statbar"><span class="pat-statbar-label">${j}</span>
        <span class="pat-statbar-track"><span class="pat-statbar-fill" style="width:${Math.max(3, pct)}%"></span></span>
        <span class="pat-statbar-n">${jahre[j]}</span></div>`;
    }).join('');

    host.innerHTML = `
      <div class="pat-stat-grid">
        <div><h4>🏢 Top-Anmelder (${Object.keys(anmelder).length})</h4>${_bars(anmelder, n) || '<p class="planner-muted">keine Angaben</p>'}</div>
        <div><h4>📅 Anmeldungen pro Jahr</h4>${jahrBars || '<p class="planner-muted">keine Datumsangaben</p>'}</div>
        <div><h4>🏷 IPC-Hauptklassen</h4>${_bars(ipc, n) || '<p class="planner-muted">keine IPC-Angaben</p>'}</div>
        <div><h4>📈 Stärke-Score-Verteilung</h4>${_bars(buckets, n, 4)}</div>
      </div>
      ${matrix}`;
  }

  /* ── Gespeicherte Analysen ─────────────────────────────────────────── */
  async function _loadAnalyses() {
    if (!_project) { _analyses = []; return; }
    try {
      const d = await _json(`/api/patente/projects/${encodeURIComponent(_project)}/analyses`);
      _analyses = d.analysen || [];
    } catch (e) {
      _analyses = [];
    }
  }

  function _renderAnalyses() {
    const host = $('pat-analyses-list');
    if (!host) return;
    if (!_analyses.length) { host.innerHTML = '<p class="planner-muted">Noch keine Analysen gespeichert.</p>'; return; }
    host.innerHTML = _analyses.map(a => {
      const typ = (a.analyse_typ || '').replace(/_/g, ' ');
      const erstellt = (a.erstellt_am || '').slice(0, 19).replace('T', ' ');
      const ids = (a.patent_ids || []).join(', ');
      return `
        <details class="pat-analysis-item">
          <summary>📋 ${escHtml(typ)} · ${escHtml(erstellt)} · ${escHtml(ids)}</summary>
          <div>${_renderErgebnisse(a.ergebnisse)}</div>
          <div class="pat-analysis-actions">
            <a class="export-btn" href="/api/patente/projects/${encodeURIComponent(_project)}/analyses/${encodeURIComponent(a.datei_name)}/markdown">📥 MD</a>
            <button class="export-btn btn-danger-sm pat-del-analysis" data-file="${escHtml(a.datei_name)}">🗑️ Löschen</button>
          </div>
        </details>`;
    }).join('');
    host.querySelectorAll('.pat-del-analysis').forEach(btn => {
      btn.addEventListener('click', async () => {
        if (!confirm('Diese Analyse löschen?')) return;
        try {
          await _json(`/api/patente/projects/${encodeURIComponent(_project)}/analyses/${encodeURIComponent(btn.dataset.file)}`, { method: 'DELETE' });
          await _loadAnalyses(); _renderAnalyses();
        } catch (e) { _status('Löschen fehlgeschlagen: ' + e.message); }
      });
    });
  }

  /* ── Wissensgraph (Cytoscape.js) ───────────────────────────────────── */
  function _graphStyle() {
    const css = getComputedStyle(document.documentElement);
    const text = (css.getPropertyValue('--text') || '#e8e8e8').trim();
    const border = (css.getPropertyValue('--border') || '#3a3a3a').trim();
    const colors = { patent: '#4A90D9', ipc: '#E8A838', rechteinhaber: '#5CB85C', zitat: '#B96AD9' };
    return [
      { selector: 'node', style: {
        'background-color': 'data(color)', 'label': 'data(label)', 'color': text,
        'font-size': 10, 'text-wrap': 'wrap', 'text-max-width': 110,
        'text-valign': 'center', 'text-halign': 'center', 'width': 'label', 'height': 'label',
        'padding': 7, 'shape': 'round-rectangle', 'text-outline-width': 0,
      }},
      { selector: 'edge', style: {
        'width': 1.5, 'line-color': border, 'target-arrow-color': border,
        'target-arrow-shape': 'triangle', 'curve-style': 'bezier', 'opacity': 0.7,
      }},
    ];
    // (Farben pro Knotentyp werden beim Aufbau in _buildGraph als data(color) gesetzt.)
    void colors;
  }

  async function _buildGraph() {
    if (_needProject()) return;
    const host = $('patente-graph');
    if (!host) return;
    const showIpc = $('pat-graph-ipc')?.checked !== false;
    const showRh = $('pat-graph-rh')?.checked !== false;
    const showZitate = $('pat-graph-zitate')?.checked !== false;
    const focus = $('pat-graph-focus')?.value || 'Alle';
    _status('⏳ Baue Wissensgraph…');
    try {
      const d = await _json(`/api/patente/projects/${encodeURIComponent(_project)}/graph`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ show_ipc: showIpc, show_assignee: showRh, show_citations: showZitate, focus_assignee: focus }),
      });
      const colors = { patent: '#4A90D9', ipc: '#E8A838', rechteinhaber: '#5CB85C', zitat: '#B96AD9' };
      const nodes = (d.nodes || []).map(n => ({ data: { id: n.id, label: n.label, color: colors[n.typ] || '#888' } }));
      const edges = (d.edges || []).map((e, i) => ({ data: { id: 'e' + i, source: e.von, target: e.zu } }));
      if (typeof cytoscape === 'undefined') { _status('Cytoscape nicht geladen.'); return; }
      if (_cy) _cy.destroy();
      _cy = cytoscape({
        container: host, elements: [...nodes, ...edges], style: _graphStyle(),
        wheelSensitivity: 0.2, minZoom: 0.2, maxZoom: 3,
        layout: { name: 'cose', animate: false, padding: 30, nodeRepulsion: 9000, idealEdgeLength: 100 },
      });
      _status(`✓ ${nodes.length} Knoten, ${edges.length} Kanten.`);
      _updateFocusOptions();
    } catch (e) {
      _status('Graph fehlgeschlagen: ' + e.message);
    }
  }

  function _updateFocusOptions() {
    const sel = $('pat-graph-focus');
    if (!sel) return;
    const cur = sel.value;
    const rh = new Set();
    _patente.forEach(p => (p.rechteinhaber || []).forEach(r => rh.add(r)));
    sel.innerHTML = '<option value="Alle">Alle Rechteinhaber</option>' +
      Array.from(rh).sort().map(r => `<option value="${escHtml(r)}">${escHtml(r)}</option>`).join('');
    if (Array.from(rh).includes(cur)) sel.value = cur;
  }

  /* ── Sub-Tab-Umschaltung ────────────────────────────────────────────── */
  function _switchSubtab(id) {
    document.querySelectorAll('.pat-subtab').forEach(b => b.classList.toggle('active', b.dataset.subtab === id));
    document.querySelectorAll('.pat-section').forEach(s => s.classList.toggle('active', s.dataset.subtab === id));
    if (id === 'statistik') _renderStatistik();
  }

  function init() {
    document.querySelectorAll('.pat-subtab').forEach(b => b.addEventListener('click', () => _switchSubtab(b.dataset.subtab)));

    $('pat-project')?.addEventListener('change', e => _selectProject(e.target.value));
    $('btn-pat-create')?.addEventListener('click', _createProject);
    $('btn-pat-delete')?.addEventListener('click', _deleteProject);

    $('btn-pat-lookup')?.addEventListener('click', _lookupSingle);
    $('btn-pat-search')?.addEventListener('click', _searchTerm);
    $('btn-pat-ops-save')?.addEventListener('click', _saveOps);
    $('btn-pat-ops-clear')?.addEventListener('click', _clearOps);
    _loadOpsStatus();
    $('btn-pat-csv-import')?.addEventListener('click', _importCsv);
    $('btn-pat-csv-read')?.addEventListener('click', _readBatch);
    $('pat-preview-close')?.addEventListener('click', _closePreview);
    $('pat-preview-modal')?.addEventListener('click', e => { if (e.target.id === 'pat-preview-modal') _closePreview(); });
    document.addEventListener('keydown', e => { if (e.key === 'Escape') _closePreview(); });
    $('pat-json-file')?.addEventListener('change', e => _importJsonFile(e.target.files[0]));
    $('btn-pat-migrate')?.addEventListener('click', _migrate);

    $('btn-pat-export-json')?.addEventListener('click', _exportJson);
    $('btn-pat-export-csv')?.addEventListener('click', _exportCsv);

    $('btn-pat-ask')?.addEventListener('click', _ask);
    $('pat-ask-input')?.addEventListener('keydown', e => { if (e.key === 'Enter') _ask(); });

    $('btn-pat-analyze')?.addEventListener('click', _runAnalysis);
    $('btn-pat-fto')?.addEventListener('click', _runFto);
    $('btn-pat-sketches')?.addEventListener('click', _runSketches);

    $('btn-pat-graph')?.addEventListener('click', _buildGraph);

    _loadProjects();
  }

  return { init };

})();
