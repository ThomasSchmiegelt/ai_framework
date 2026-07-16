/* AI_Framework_Thomas — Tab „🧾 Angebot / Rechnung": Firmenprofil, Angebots- und
   Rechnungserstellung (Freitext→Positionen via API-Modell, deterministische
   Beträge im Backend), Angebot aus Planer-Projekt, Rechnung aus Angebot mit
   Abweichungsbehandlung (gesondert ausweisen / verstecken), Ausgabe PDF + DOCX.
   Beträge werden nie vom LLM berechnet. */

const Rechnung = (() => {

  const $ = id => document.getElementById(id);
  const _model = () => (typeof Profile !== 'undefined' && Profile.modelFor) ? Profile.modelFor('general') : undefined;

  let _docType = 'rechnung';        // 'rechnung' | 'angebot'
  let _baseline = null;             // geladenes Angebot als Rechnungsbasis
  let _loadedProjectId = '';        // Projekt beim Laden aus Plan/Angebot
  let _loadedPlanId = '';

  function _status(t) { const el = $('rz-status'); if (el) el.textContent = t || ''; }
  function _spin(on)  { const el = $('rz-spin');   if (el) el.style.display = on ? '' : 'none'; }

  async function _json(url, opts) {
    const resp = await fetch(url, opts);
    let d = {};
    try { d = await resp.json(); } catch (_) {}
    if (!resp.ok) throw new Error(d.detail || ('HTTP ' + resp.status));
    return d;
  }

  /* ── Deutsche Zahlformatierung (nur Anzeige; Server rechnet verbindlich) ─── */
  function _num(v) {
    let s = String(v == null ? '' : v).trim().replace(/€|\s/g, '');
    if (!s) return 0;
    if (s.includes(',') && (!s.includes('.') || s.lastIndexOf(',') > s.lastIndexOf('.')))
      s = s.replace(/\./g, '').replace(',', '.');
    else s = s.replace(/,/g, '');
    const n = parseFloat(s);
    return isNaN(n) ? 0 : n;
  }
  function _eur(n) {
    return (Math.round(n * 100) / 100).toLocaleString('de-DE',
      { minimumFractionDigits: 2, maximumFractionDigits: 2 }) + ' €';
  }

  /* ── Firmenprofil ────────────────────────────────────────────────────── */
  const _FP = { firma:'fp-firma', inhaber:'fp-inhaber', strasse:'fp-strasse', plz_ort:'fp-plzort',
    ust_id:'fp-ustid', steuernummer:'fp-steuernr', email:'fp-email', telefon:'fp-telefon',
    iban:'fp-iban', bic:'fp-bic', bank:'fp-bank', rechnung_prefix:'fp-prefix',
    angebot_prefix:'fp-angebot-prefix' };

  async function _loadProfile() {
    try {
      const p = await _json('/api/firmenprofil');
      for (const [k, id] of Object.entries(_FP)) { const el = $(id); if (el) el.value = p[k] || ''; }
      if ($('fp-klein')) $('fp-klein').checked = !!p.kleinunternehmer;
      if ($('rz-klein')) $('rz-klein').checked = !!p.kleinunternehmer;
    } catch (_) {}
  }

  async function _saveProfile() {
    const p = {};
    for (const [k, id] of Object.entries(_FP)) p[k] = ($(id)?.value || '').trim();
    p.kleinunternehmer = !!$('fp-klein')?.checked;
    _spin(true);
    try {
      await _json('/api/firmenprofil', { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(p) });
      _status('✓ Firmenprofil gespeichert.');
    } catch (e) { _status('Speichern fehlgeschlagen: ' + e.message); }
    finally { _spin(false); }
  }

  /* ── Positionen ──────────────────────────────────────────────────────── */
  function _posRow(d = {}, baseline = null) {
    const tr = document.createElement('tr');
    tr.innerHTML = `
      <td><input class="rz-p-menge" value="${escHtml(d.menge != null ? String(d.menge) : '1')}" /></td>
      <td><input class="rz-p-einheit" value="${escHtml(d.einheit || '')}" placeholder="Std/Tag/Stk" /></td>
      <td><input class="rz-p-besch" value="${escHtml(d.beschreibung || '')}" placeholder="Leistung" /></td>
      <td><input class="rz-p-preis" value="${escHtml(d.einzelpreis != null ? String(d.einzelpreis) : '')}" placeholder="0,00" /></td>
      <td class="rz-abw-col"></td>
      <td><button class="rz-p-del" title="Position entfernen">🗑️</button></td>`;
    if (baseline) { tr._baseline = baseline; tr.dataset.mode = 'gesondert'; }
    tr.querySelector('.rz-p-del').addEventListener('click', () => { tr.remove(); _liveTotal(); });
    tr.querySelectorAll('input').forEach(i => i.addEventListener('input', () => { _liveTotal(); _renderAbwCell(tr); }));
    _renderAbwCell(tr);
    return tr;
  }
  function _addPos(d) { $('rz-pos-body').appendChild(_posRow(d)); _liveTotal(); }

  /* Abweichungs-Zelle (nur für Positionen mit Angebots-Basis) */
  function _renderAbwCell(tr) {
    const cell = tr.querySelector('.rz-abw-col');
    if (!cell) return;
    const b = tr._baseline;
    if (!b) { cell.innerHTML = ''; return; }
    const agreed = _num(b.menge) * _num(b.einzelpreis);
    const actual = _num(tr.querySelector('.rz-p-menge').value) * _num(tr.querySelector('.rz-p-preis').value);
    const delta = actual - agreed;
    const mode = tr.dataset.mode || 'gesondert';
    if (Math.abs(delta) < 0.005) {
      cell.innerHTML = `<span class="planner-muted">verein.: ${_eur(agreed)} · keine Abw.</span>`;
      return;
    }
    const dtxt = `<span class="rz-delta ${delta > 0 ? 'up' : 'down'}">Δ ${delta > 0 ? '+' : ''}${_eur(delta)}</span>`;
    const btn = `<button type="button" class="rz-abw-toggle" title="Umschalten: gesondert ausweisen ↔ verstecken">${mode === 'versteckt' ? '👁 versteckt' : '⚖ gesondert'}</button>`;
    cell.innerHTML = `<div class="rz-abw"><span class="planner-muted">verein.: ${_eur(agreed)}</span> ${dtxt} ${btn}</div>`;
    cell.querySelector('.rz-abw-toggle').addEventListener('click', () => {
      tr.dataset.mode = (tr.dataset.mode === 'versteckt') ? 'gesondert' : 'versteckt';
      _renderAbwCell(tr);
    });
  }

  function _readPositions() {
    return Array.from($('rz-pos-body').querySelectorAll('tr')).map(tr => ({
      menge: tr.querySelector('.rz-p-menge').value,
      einheit: tr.querySelector('.rz-p-einheit').value,
      beschreibung: tr.querySelector('.rz-p-besch').value,
      einzelpreis: tr.querySelector('.rz-p-preis').value,
    })).filter(p => p.beschreibung.trim() || _num(p.einzelpreis));
  }

  /* Rechnung-aus-Angebot: Positionen inkl. Abweichungsbehandlung erzeugen */
  function _buildFinalPositions() {
    const positionen = [];
    const abweichungen = [];
    $('rz-pos-body').querySelectorAll('tr').forEach(tr => {
      const menge = tr.querySelector('.rz-p-menge').value;
      const einheit = tr.querySelector('.rz-p-einheit').value;
      const besch = tr.querySelector('.rz-p-besch').value;
      const preis = tr.querySelector('.rz-p-preis').value;
      if (!besch.trim() && !_num(preis)) return;
      const b = tr._baseline;
      if (!b) { positionen.push({ menge, einheit, beschreibung: besch, einzelpreis: preis }); return; }
      const agreed = _num(b.menge) * _num(b.einzelpreis);
      const actual = _num(menge) * _num(preis);
      const delta = actual - agreed;
      const mode = tr.dataset.mode || 'gesondert';
      if (Math.abs(delta) < 0.005) {
        positionen.push({ menge, einheit, beschreibung: besch, einzelpreis: preis });
      } else if (mode === 'versteckt') {
        // Ist-Betrag in einer Zeile, kein Hinweis auf Abweichung
        positionen.push({ menge, einheit, beschreibung: besch, einzelpreis: preis });
        abweichungen.push({ beschreibung: besch, vereinbart: agreed, tatsaechlich: actual, modus: 'versteckt' });
      } else {
        // gesondert: vereinbarte Basis-Zeile + separate Nachtrags-/Minderungszeile
        positionen.push({ menge: b.menge, einheit: b.einheit || einheit, beschreibung: b.beschreibung || besch, einzelpreis: b.einzelpreis });
        const label = delta > 0 ? 'Nachtrag' : 'Minderung';
        positionen.push({ menge: 1, einheit: 'pauschal', beschreibung: `${label}: ${besch} (Abweichung z. Angebot)`, einzelpreis: delta });
        abweichungen.push({ beschreibung: besch, vereinbart: agreed, tatsaechlich: actual, modus: 'gesondert' });
      }
    });
    return { positionen, abweichungen };
  }

  function _liveTotal() {
    const klein = $('rz-klein')?.checked;
    const satz = klein ? 0 : _num($('rz-ust')?.value);
    let netto = 0;
    $('rz-pos-body').querySelectorAll('tr').forEach(tr => {
      netto += _num(tr.querySelector('.rz-p-menge').value) * _num(tr.querySelector('.rz-p-preis').value);
    });
    const ust = netto * satz / 100;
    const el = $('rz-live-total');
    if (!el) return;
    el.innerHTML = klein
      ? `Netto: <strong>${_eur(netto)}</strong> · <span class="planner-muted">§19 – keine USt</span>`
      : `Netto: <strong>${_eur(netto)}</strong> · zzgl. ${satz} % USt: <strong>${_eur(ust)}</strong> · Brutto: <strong>${_eur(netto + ust)}</strong>`;
  }

  /* ── Vorgang zerlegen (Leistungskategorien) ─────────────────────────── */
  const _DEFAULT_CATS = ['Recherche', 'Planung', 'Konstruktion', 'Beschaffung',
    'Fremdleistungen', 'Fertigung/Montage', 'Inbetriebnahme', 'Dokumentation', 'Projektmanagement'];
  let _cats = _DEFAULT_CATS.map(name => ({ name, on: true }));

  function _renderCats() {
    const host = $('rz-cat-chips');
    if (!host) return;
    host.innerHTML = _cats.map((c, i) =>
      `<button type="button" class="rz-chip ${c.on ? 'on' : ''}" data-i="${i}">${escHtml(c.name)}</button>`).join('');
    host.querySelectorAll('.rz-chip').forEach(btn => btn.addEventListener('click', () => {
      const i = parseInt(btn.dataset.i, 10);
      _cats[i].on = !_cats[i].on; _renderCats();
    }));
  }
  function _addCat() {
    const inp = $('rz-cat-add');
    const name = (inp?.value || '').trim();
    if (!name) return;
    if (!_cats.some(c => c.name.toLowerCase() === name.toLowerCase())) _cats.push({ name, on: true });
    if (inp) inp.value = '';
    _renderCats();
  }

  async function _breakdown() {
    const vorgang = ($('rz-vorgang')?.value || '').trim();
    if (!vorgang) { _status('Bitte zuerst den Vorgang beschreiben.'); return; }
    const kategorien = _cats.filter(c => c.on).map(c => c.name);
    if (!kategorien.length) { _status('Bitte mindestens eine Kategorie wählen.'); return; }
    const stundensatz = _num($('rz-satz')?.value) || undefined;
    _spin(true); _status('⏳ Zerlege Vorgang in Einzelpositionen…');
    try {
      const d = await _json('/api/rechnung/breakdown', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ vorgang, kategorien, stundensatz, model: _model() }),
      });
      if (d.tokens && typeof TokenMeter !== 'undefined') TokenMeter.add(d.tokens, 'Rechnung');
      const pos = d.positionen || [];
      $('rz-pos-body').innerHTML = '';
      pos.forEach(p => _addPos(p));
      if (!pos.length) _addPos();
      _liveTotal();
      _status(`✓ ${pos.length} Position(en) aus dem Vorgang erzeugt.`);
    } catch (e) { _status('Zerlegung fehlgeschlagen: ' + e.message); }
    finally { _spin(false); }
  }

  async function _parse() {
    const text = ($('rz-freetext')?.value || '').trim();
    if (!text) { _status('Bitte zuerst eine Beschreibung eingeben.'); return; }
    _spin(true); _status('⏳ Zerlege in Positionen…');
    try {
      const d = await _json('/api/rechnung/parse', {
        method:'POST', headers:{'Content-Type':'application/json'},
        body: JSON.stringify({ text, model: _model() }),
      });
      if (d.tokens && typeof TokenMeter !== 'undefined') TokenMeter.add(d.tokens, 'Rechnung');
      $('rz-pos-body').innerHTML = '';
      (d.positionen || []).forEach(p => _addPos(p));
      if (!(d.positionen || []).length) _addPos();
      if (d.leistungsdatum && $('rz-leistung')) $('rz-leistung').value = d.leistungsdatum;
      if (d.einleitung && $('rz-einleitung')) $('rz-einleitung').value = d.einleitung;
      _liveTotal();
      _status(`✓ ${(d.positionen || []).length} Position(en) erkannt.`);
    } catch (e) { _status('Analyse fehlgeschlagen: ' + e.message); }
    finally { _spin(false); }
  }

  /* ── Dokumenttyp-Umschalter (Angebot ⇄ Rechnung) ─────────────────────── */
  function _setDocType(type) {
    _docType = (type === 'angebot') ? 'angebot' : 'rechnung';
    _baseline = null; _loadedProjectId = ''; _loadedPlanId = '';
    const ang = _docType === 'angebot';
    document.querySelectorAll('#rz-doctype button').forEach(b => b.classList.toggle('active', b.dataset.type === _docType));
    const panel = $('rechnung-panel');
    if (panel) { panel.classList.toggle('rz-mode-angebot', ang); panel.classList.toggle('rz-mode-rechnung', !ang); }
    if ($('rz-daten-title')) $('rz-daten-title').textContent = ang ? 'Angebotsdaten' : 'Rechnungsdaten';
    if ($('rz-lbl-nummer')) $('rz-lbl-nummer').textContent = ang ? 'Angebotsnummer' : 'Rechnungsnummer';
    if ($('rz-lbl-datum')) $('rz-lbl-datum').textContent = ang ? 'Angebotsdatum' : 'Rechnungsdatum';
    if ($('rz-lbl-leistung')) $('rz-lbl-leistung').textContent = ang ? 'Ausführungszeitraum' : 'Leistungszeitraum';
    if ($('rz-create')) $('rz-create').textContent = ang ? '📄 Angebot erstellen' : '🧾 Rechnung erstellen';
    if ($('rz-freetext-hint')) $('rz-freetext-hint').textContent = ang ? 'Angebot frei beschreiben' : 'Rechnung frei beschreiben';
    $('rz-pos-table')?.classList.remove('has-baseline');
    if ($('rz-baseline-info')) $('rz-baseline-info').textContent = '';
    if ($('rz-download')) $('rz-download').style.display = 'none';
    if ($('rz-result')) $('rz-result').textContent = '';
    _nextNumber();
    if (ang) _loadProjectsForAngebot(); else _loadAngebotList();
  }

  /* ── Angebot: aus Projekt/Plan laden ─────────────────────────────────── */
  async function _loadProjectsForAngebot() {
    const sel = $('rz-sel-project'); if (!sel) return;
    try {
      const projects = await _json('/api/projects');
      const rel = (Array.isArray(projects) ? projects : []).filter(p => p.status === 'angebot_frei' || p.status === 'angebot');
      sel.innerHTML = rel.length
        ? rel.map(p => `<option value="${escHtml(p.id)}">${escHtml(p.name)} — ${escHtml(p.status_label || p.status || '')}</option>`).join('')
        : '<option value="">— keine für Angebot freigegebenen Projekte —</option>';
    } catch (e) { sel.innerHTML = '<option value="">Fehler beim Laden</option>'; }
  }

  async function _loadFromPlan() {
    const pid = $('rz-sel-project')?.value;
    if (!pid) { _status('Bitte ein freigegebenes Projekt wählen.'); return; }
    _spin(true); _status('⏳ Lade Plan-Positionen…');
    try {
      const d = await _json('/api/angebot/from-plan', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ project_id: pid }),
      });
      $('rz-pos-body').innerHTML = '';
      (d.positionen || []).forEach(p => _addPos(p));
      if (!(d.positionen || []).length) _addPos();
      _loadedProjectId = d.project_id || pid;
      _loadedPlanId = d.plan_id || '';
      const pr = d.projekt || {};
      if (pr.name && $('rz-k-name') && !$('rz-k-name').value) { /* Projektname ≠ Kunde: nicht auto-füllen */ }
      _liveTotal();
      _status(`✓ ${(d.positionen || []).length} Position(en) aus „${d.plan_name || 'Plan'}" übernommen.`);
    } catch (e) { _status('Laden fehlgeschlagen: ' + e.message); }
    finally { _spin(false); }
  }

  /* ── Rechnung: aus Angebot übernehmen ────────────────────────────────── */
  async function _loadAngebotList() {
    const sel = $('rz-sel-angebot'); if (!sel) return;
    try {
      const d = await _json('/api/angebot/list');
      const rows = d.angebote || [];
      sel.innerHTML = rows.length
        ? rows.map(a => `<option value="${escHtml(a.nummer)}">${escHtml(a.nummer)} — ${escHtml(a.kunde || '—')} — ${escHtml(a.brutto)}</option>`).join('')
        : '<option value="">— noch keine Angebote —</option>';
    } catch (e) { sel.innerHTML = '<option value="">Fehler beim Laden</option>'; }
  }

  async function _loadFromAngebot() {
    const nr = $('rz-sel-angebot')?.value;
    if (!nr) { _status('Bitte ein Angebot wählen.'); return; }
    _spin(true); _status('⏳ Lade Angebot als Basis…');
    try {
      const a = await _json(`/api/angebot/${encodeURIComponent(nr)}`);
      _baseline = { nummer: a.nummer, project_id: a.project_id || '', positionen: a.positionen || [] };
      const k = a.kunde || {};
      const kmap = { 'rz-k-name':'name','rz-k-zusatz':'zusatz','rz-k-strasse':'strasse','rz-k-plzort':'plz_ort','rz-k-land':'land','rz-k-kdnr':'kundennummer' };
      for (const [id, key] of Object.entries(kmap)) { if ($(id)) $(id).value = k[key] || ''; }
      if ($('rz-ust')) $('rz-ust').value = String(parseInt(_num(a.ust_satz) || 19, 10));
      if ($('rz-klein')) $('rz-klein').checked = !!a.kleinunternehmer;
      $('rz-pos-body').innerHTML = '';
      (a.positionen || []).forEach(p => {
        const baseline = { menge: p.menge, einheit: p.einheit, beschreibung: p.beschreibung, einzelpreis: p.einzelpreis };
        $('rz-pos-body').appendChild(_posRow(p, baseline));
      });
      if (!(a.positionen || []).length) _addPos();
      $('rz-pos-table')?.classList.add('has-baseline');
      if ($('rz-baseline-info'))
        $('rz-baseline-info').innerHTML = `Basis: <strong>Angebot ${escHtml(a.nummer)}</strong> · vereinbart Brutto ${escHtml(_eur(_num(a.summe_brutto)))}. Passe die tatsächlichen Werte an; Abweichungen je Position schaltbar (⚖ gesondert / 👁 versteckt).`;
      _liveTotal();
      _status(`✓ Angebot ${nr} als Rechnungsbasis geladen.`);
    } catch (e) { _status('Laden fehlgeschlagen: ' + e.message); }
    finally { _spin(false); }
  }

  /* ── Beleg erstellen (Angebot oder Rechnung) ─────────────────────────── */
  async function _nextNumber() {
    try { const d = await _json(`/api/${_docType}/next-number`); if ($('rz-nummer')) $('rz-nummer').value = d.nummer || ''; }
    catch (_) {}
  }

  async function _create() {
    const ang = _docType === 'angebot';
    let positionen, abweichungen = [];
    if (!ang && _baseline) {
      const built = _buildFinalPositions();
      positionen = built.positionen; abweichungen = built.abweichungen;
    } else {
      positionen = _readPositions();
    }
    if (!positionen.length) { _status('Bitte mindestens eine Position mit Beschreibung/Preis erfassen.'); return; }
    const body = {
      nummer: ($('rz-nummer')?.value || '').trim(),
      datum: $('rz-datum')?.value || undefined,
      leistungsdatum: $('rz-leistung')?.value || '',
      kunde: {
        name: $('rz-k-name')?.value || '', zusatz: $('rz-k-zusatz')?.value || '',
        strasse: $('rz-k-strasse')?.value || '', plz_ort: $('rz-k-plzort')?.value || '',
        land: $('rz-k-land')?.value || '', kundennummer: $('rz-k-kdnr')?.value || '',
      },
      positionen,
      ust_satz: _num($('rz-ust')?.value),
      kleinunternehmer: !!$('rz-klein')?.checked,
      einleitung: $('rz-einleitung')?.value || '',
      hinweis: $('rz-hinweis')?.value || '',
    };
    if (ang) {
      body.gueltig_tage = parseInt($('rz-gueltig')?.value, 10) || 30;
      if (_loadedProjectId) { body.project_id = _loadedProjectId; body.plan_id = _loadedPlanId || ''; }
    } else {
      body.zahlungsziel_tage = parseInt($('rz-ziel')?.value, 10) || 14;
      if (_baseline) { body.project_id = _baseline.project_id || ''; body.angebot_nr = _baseline.nummer; body.abweichungen = abweichungen; }
    }
    const url = ang ? '/api/angebot/create' : '/api/rechnung/create';
    const titel = ang ? 'Angebot' : 'Rechnung';
    _spin(true); _status(`⏳ Erstelle ${titel}…`);
    try {
      const d = await _json(url, { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(body) });
      const nr = d.nummer;
      $('rz-result').innerHTML = `✓ <strong>${titel} ${escHtml(nr)}</strong> — Netto ${escHtml(d.summe_netto)} · USt ${escHtml(d.ust_betrag)} · <strong>Brutto ${escHtml(d.summe_brutto)}</strong>`;
      $('rz-dl-pdf').href = `/api/${_docType}/${encodeURIComponent(nr)}/pdf`;
      $('rz-dl-docx').href = `/api/${_docType}/${encodeURIComponent(nr)}/docx`;
      $('rz-download').style.display = '';
      _status(`✓ ${titel} gespeichert.`);
      _nextNumber();
    } catch (e) { _status('Erstellen fehlgeschlagen: ' + e.message); }
    finally { _spin(false); }
  }

  /* ── Verlauf ─────────────────────────────────────────────────────────── */
  async function _loadHistory() {
    const host = $('rz-history');
    if (!host) return;
    const ang = _docType === 'angebot';
    const url = ang ? '/api/angebot/list' : '/api/rechnung/list';
    const key = ang ? 'angebote' : 'rechnungen';
    const label = ang ? 'Angebote' : 'Rechnungen';
    try {
      const d = await _json(url);
      const rows = d[key] || [];
      const head = `<p class="planner-muted" style="font-size:11.5px">${label} (Dokumenttyp über „Neu" umschalten)</p>`;
      if (!rows.length) { host.innerHTML = head + `<p class="planner-muted">Noch keine ${label}.</p>`; return; }
      host.innerHTML = head + `<table class="rz-hist"><thead><tr><th>Nummer</th><th>Datum</th><th>Kunde</th><th>Brutto</th><th></th></tr></thead><tbody>${
        rows.map(r => `<tr>
          <td>${escHtml(r.nummer)}</td><td>${escHtml((r.datum||'').slice(0,10))}</td>
          <td>${escHtml(r.kunde)}</td><td style="text-align:right">${escHtml(r.brutto)}</td>
          <td class="rz-hist-act">
            <a class="export-btn" target="_blank" href="/api/${_docType}/${encodeURIComponent(r.nummer)}/pdf">PDF</a>
            <a class="export-btn" target="_blank" href="/api/${_docType}/${encodeURIComponent(r.nummer)}/docx">DOCX</a>
            <button class="export-btn rz-del" data-nr="${escHtml(r.nummer)}">🗑️</button>
          </td></tr>`).join('')}</tbody></table>`;
      host.querySelectorAll('.rz-del').forEach(b => b.addEventListener('click', async () => {
        if (!confirm(`${ang ? 'Angebot' : 'Rechnung'} ${b.dataset.nr} löschen?`)) return;
        try { await _json(`/api/${_docType}/${encodeURIComponent(b.dataset.nr)}`, { method:'DELETE' }); _loadHistory(); }
        catch (e) { _status('Löschen fehlgeschlagen: ' + e.message); }
      }));
    } catch (e) { host.innerHTML = `<p class="planner-muted">Fehler: ${escHtml(e.message)}</p>`; }
  }

  /* ── Sub-Tabs (geteilt mit Zeugnis über data-panel) ──────────────────── */
  function _switchSub(sub) {
    document.querySelectorAll('.rz-subtab[data-panel="rechnung"]').forEach(b => b.classList.toggle('active', b.dataset.sub === sub));
    document.querySelectorAll('.rz-section[data-panel="rechnung"]').forEach(s => s.classList.toggle('active', s.dataset.sub === sub));
    if (sub === 'verlauf') _loadHistory();
  }

  /* Externe API: Angebot/Rechnung-Tab öffnen und aus einem Plan vorbelegen
     (vom Planer „→ Angebot schreiben" aufgerufen). */
  function openForPlan(projectId) {
    if (typeof switchTab === 'function') switchTab('rechnung');
    else document.querySelector('.tab-btn[data-tab="rechnung"]')?.click();
    _switchSub('neu');
    _setDocType('angebot');
    // Projekt in der Auswahl vorselektieren, sobald geladen
    setTimeout(async () => {
      await _loadProjectsForAngebot();
      const sel = $('rz-sel-project');
      if (sel && projectId) { sel.value = projectId; if (sel.value === projectId) _loadFromPlan(); }
    }, 60);
  }

  function init() {
    document.querySelectorAll('.rz-subtab[data-panel="rechnung"]').forEach(b =>
      b.addEventListener('click', () => _switchSub(b.dataset.sub)));
    document.querySelectorAll('#rz-doctype button').forEach(b =>
      b.addEventListener('click', () => _setDocType(b.dataset.type)));
    $('fp-save')?.addEventListener('click', _saveProfile);
    $('rz-pos-add')?.addEventListener('click', () => _addPos());
    $('rz-breakdown')?.addEventListener('click', _breakdown);
    $('rz-cat-add')?.addEventListener('keydown', e => { if (e.key === 'Enter') { e.preventDefault(); _addCat(); } });
    $('rz-parse')?.addEventListener('click', _parse);
    $('rz-create')?.addEventListener('click', _create);
    $('rz-refresh')?.addEventListener('click', _loadHistory);
    $('rz-load-plan')?.addEventListener('click', _loadFromPlan);
    $('rz-proj-refresh')?.addEventListener('click', _loadProjectsForAngebot);
    $('rz-load-angebot')?.addEventListener('click', _loadFromAngebot);
    $('rz-ang-refresh')?.addEventListener('click', _loadAngebotList);
    $('rz-ust')?.addEventListener('change', _liveTotal);
    $('rz-klein')?.addEventListener('change', _liveTotal);
    if ($('rz-datum') && !$('rz-datum').value) $('rz-datum').value = new Date().toISOString().slice(0, 10);
    _renderCats();
    _addPos();
    _loadProfile();
    _setDocType('rechnung');   // Standard: Rechnung (setzt Labels + Nummer)
  }

  return { init, openForPlan };
})();
