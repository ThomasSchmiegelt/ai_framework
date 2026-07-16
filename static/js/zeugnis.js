/* AI_Framework_Thomas — Tab „📜 Arbeitszeugnisse": qualifiziertes Zeugnis in
   codierter Zeugnissprache passend zur Gesamtnote (LLM, ideal API-Modell),
   frei nachbearbeitbar, Ausgabe als PDF + DOCX (über die generischen Exporter). */

const Zeugnis = (() => {

  const $ = id => document.getElementById(id);
  const _model = () => (typeof Profile !== 'undefined' && Profile.modelFor) ? Profile.modelFor('general') : undefined;

  function _status(t) { const el = $('zg-status'); if (el) el.textContent = t || ''; }
  function _spin(on)  { const el = $('zg-spin');   if (el) el.style.display = on ? '' : 'none'; }

  let _currentId = '';

  async function _json(url, opts) {
    const resp = await fetch(url, opts);
    let d = {};
    try { d = await resp.json(); } catch (_) {}
    if (!resp.ok) throw new Error(d.detail || ('HTTP ' + resp.status));
    return d;
  }

  function _readMeta() {
    const aufgaben = ($('zg-aufgaben')?.value || '').split('\n').map(s => s.trim()).filter(Boolean);
    return {
      arbeitgeber: $('zg-arbeitgeber')?.value || '',
      name: $('zg-name')?.value || '',
      geschlecht: $('zg-geschlecht')?.value || 'divers',
      position: $('zg-position')?.value || '',
      abteilung: $('zg-abteilung')?.value || '',
      note: $('zg-note')?.value || '2',
      eintritt: $('zg-eintritt')?.value || '',
      austritt: $('zg-austritt')?.value || '',
      beendigung: $('zg-beendigung')?.value || '',
      fuehrung: $('zg-fuehrung')?.value || '',
      aufgaben,
      staerken: $('zg-staerken')?.value || '',
      ort: $('zg-ort')?.value || '',
      ausstellungsdatum: $('zg-datum')?.value || '',
      unterzeichner: $('zg-unterzeichner')?.value || '',
    };
  }

  async function _generate() {
    const meta = _readMeta();
    if (!meta.name || !meta.position) { _status('Bitte Name und Position angeben.'); return; }
    _spin(true); _status('⏳ Erzeuge Zeugnis…');
    const btn = $('zg-generate'); if (btn) btn.disabled = true;
    try {
      const d = await _json('/api/zeugnis/generate', {
        method:'POST', headers:{'Content-Type':'application/json'},
        body: JSON.stringify({ meta, model: _model() }),
      });
      if (d.tokens && typeof TokenMeter !== 'undefined') TokenMeter.add(d.tokens, 'Zeugnis');
      _currentId = d.id || '';
      $('zg-output').value = d.text || '';
      $('zg-output-wrap').style.display = '';
      _status(`✓ Zeugnis erzeugt (Note: ${d.note || ''}).`);
    } catch (e) { _status('Erzeugung fehlgeschlagen: ' + e.message); }
    finally { _spin(false); if (btn) btn.disabled = false; }
  }

  // Baut den finalen Dokumenttext (Zeugnis + Ort/Datum + Unterzeichner).
  function _content() {
    const meta = _readMeta();
    let md = '# Arbeitszeugnis\n\n' + ($('zg-output')?.value || '').trim();
    const datum = meta.ausstellungsdatum
      ? new Date(meta.ausstellungsdatum).toLocaleDateString('de-DE')
      : new Date().toLocaleDateString('de-DE');
    const ortdatum = [meta.ort, datum].filter(Boolean).join(', ');
    if (ortdatum) md += '\n\n' + ortdatum;
    if (meta.unterzeichner) md += '\n\n\n' + meta.unterzeichner;
    return md;
  }

  async function _save() {
    if (!_currentId) { _status('Kein gespeichertes Zeugnis — bitte zuerst erzeugen.'); return; }
    try {
      await _json(`/api/zeugnis/${encodeURIComponent(_currentId)}/save`, {
        method:'POST', headers:{'Content-Type':'application/json'},
        body: JSON.stringify({ text: $('zg-output')?.value || '' }),
      });
      _status('✓ Änderungen gespeichert.');
    } catch (e) { _status('Speichern fehlgeschlagen: ' + e.message); }
  }

  async function _download(fmt) {
    const url = fmt === 'pdf' ? '/api/export/pdf' : '/api/export/docx';
    _status('⏳ Erzeuge ' + fmt.toUpperCase() + '…');
    try {
      const resp = await fetch(url, {
        method:'POST', headers:{'Content-Type':'application/json'},
        body: JSON.stringify({ title: 'Arbeitszeugnis', content: _content() }),
      });
      if (!resp.ok) throw new Error('HTTP ' + resp.status);
      const blob = await resp.blob();
      const name = (($('zg-name')?.value || 'Zeugnis').trim().replace(/[^\wäöüÄÖÜß-]+/g, '_')) + '.' + fmt;
      const a = document.createElement('a');
      a.href = URL.createObjectURL(blob); a.download = name;
      document.body.appendChild(a); a.click();
      setTimeout(() => { URL.revokeObjectURL(a.href); a.remove(); }, 1000);
      _status('✓ ' + fmt.toUpperCase() + ' erstellt.');
    } catch (e) { _status(fmt.toUpperCase() + '-Export fehlgeschlagen: ' + e.message); }
  }

  /* ── Verlauf ─────────────────────────────────────────────────────────── */
  async function _loadHistory() {
    const host = $('zg-history');
    if (!host) return;
    try {
      const d = await _json('/api/zeugnis/list');
      const rows = d.zeugnisse || [];
      if (!rows.length) { host.innerHTML = '<p class="planner-muted">Noch keine Zeugnisse.</p>'; return; }
      host.innerHTML = `<table class="rz-hist"><thead><tr><th>Name</th><th>Position</th><th>Erstellt</th><th></th></tr></thead><tbody>${
        rows.map(r => `<tr>
          <td>${escHtml(r.name)}</td><td>${escHtml(r.position)}</td>
          <td>${escHtml((r.erstellt_am||'').slice(0,19).replace('T',' '))}</td>
          <td class="rz-hist-act">
            <button class="export-btn zg-open" data-id="${escHtml(r.id)}">✏️ Öffnen</button>
            <button class="export-btn zg-del" data-id="${escHtml(r.id)}">🗑️</button>
          </td></tr>`).join('')}</tbody></table>`;
      host.querySelectorAll('.zg-open').forEach(b => b.addEventListener('click', () => _open(b.dataset.id)));
      host.querySelectorAll('.zg-del').forEach(b => b.addEventListener('click', async () => {
        if (!confirm('Zeugnis löschen?')) return;
        try { await _json(`/api/zeugnis/${encodeURIComponent(b.dataset.id)}`, { method:'DELETE' }); _loadHistory(); }
        catch (e) { _status('Löschen fehlgeschlagen: ' + e.message); }
      }));
    } catch (e) { host.innerHTML = `<p class="planner-muted">Fehler: ${escHtml(e.message)}</p>`; }
  }

  async function _open(id) {
    try {
      const r = await _json(`/api/zeugnis/${encodeURIComponent(id)}`);
      const m = r.meta || {};
      _currentId = r.id || id;
      const set = (elid, v) => { const el = $(elid); if (el != null && v != null) el.value = v; };
      set('zg-arbeitgeber', m.arbeitgeber); set('zg-name', m.name); set('zg-geschlecht', m.geschlecht);
      set('zg-position', m.position); set('zg-abteilung', m.abteilung); set('zg-note', m.note);
      set('zg-eintritt', m.eintritt); set('zg-austritt', m.austritt); set('zg-beendigung', m.beendigung);
      set('zg-fuehrung', m.fuehrung); set('zg-staerken', m.staerken); set('zg-ort', m.ort);
      set('zg-datum', m.ausstellungsdatum); set('zg-unterzeichner', m.unterzeichner);
      if ($('zg-aufgaben')) $('zg-aufgaben').value = Array.isArray(m.aufgaben) ? m.aufgaben.join('\n') : (m.aufgaben || '');
      $('zg-output').value = r.text || '';
      $('zg-output-wrap').style.display = '';
      _switchSub('neu');
      _status('Zeugnis geladen — bearbeitbar.');
    } catch (e) { _status('Öffnen fehlgeschlagen: ' + e.message); }
  }

  function _switchSub(sub) {
    document.querySelectorAll('.rz-subtab[data-panel="zeugnis"]').forEach(b => b.classList.toggle('active', b.dataset.sub === sub));
    document.querySelectorAll('.rz-section[data-panel="zeugnis"]').forEach(s => s.classList.toggle('active', s.dataset.sub === sub));
    if (sub === 'verlauf') _loadHistory();
  }

  function init() {
    document.querySelectorAll('.rz-subtab[data-panel="zeugnis"]').forEach(b =>
      b.addEventListener('click', () => _switchSub(b.dataset.sub)));
    $('zg-generate')?.addEventListener('click', _generate);
    $('zg-save')?.addEventListener('click', _save);
    $('zg-dl-pdf')?.addEventListener('click', e => { e.preventDefault(); _download('pdf'); });
    $('zg-dl-docx')?.addEventListener('click', e => { e.preventDefault(); _download('docx'); });
    $('zg-refresh')?.addEventListener('click', _loadHistory);
    if ($('zg-datum') && !$('zg-datum').value) $('zg-datum').value = new Date().toISOString().slice(0, 10);
  }

  return { init };
})();
