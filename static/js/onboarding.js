/* ── AI_Framework_Thomas · Erst-Start-Einleitung (Onboarding) ─────────────────
 *
 * Beim allerersten Start (noch kein user_profile.json) – oder wenn im Profil
 * „Einleitung beim nächsten Start abspielen" aktiv ist – führt dieser Assistent
 * Schritt für Schritt durch das Framework:
 *   Folie 1: Nutzerdaten + Definition des eigenen (violetten) Modus
 *   Folie 2–10: je ein Screenshot mit kurzer, lesbarer Beschreibung
 * Am Ende wird das Profil gespeichert und das Framework startet im „eigenen Modus".
 */
const Onboarding = (() => {

  // Folie 1 ist interaktiv (Formular über dem Startbild), 2–10 sind Info-Folien.
  const SLIDES = [
    {
      kind: 'form',
      image: 'start.jpg',
      title: 'Willkommen an Bord 🌌',
      text: 'Schön, dass du da bist! Erzähl mir kurz, wer du bist – und richte deinen ganz '
           + 'eigenen Modus ein. In genau diesem Modus startet das Framework gleich.',
    },
    {
      image: 'personalisierung.png',
      title: '1 · Personalisierung',
      text: 'Über dein <strong>Profil</strong> (oben rechts 👤) passt du alles an: Name und Firma '
           + 'fließen in Dokumente und Folien ein. Du wählst einen von sieben <strong>Fachmodi</strong> '
           + '(jeweils eigene Farbe und fachliche Ausrichtung), eine Antwort-Persona, die Sprache '
           + '(Deutsch/Englisch) und lädst dein Logo samt Vorlagenbildern hoch.',
    },
    {
      image: 'chat_fenster.png',
      title: '2 · Chat',
      text: 'Das Herzstück. Stelle Fragen in natürlicher Sprache – die KI rechnet, sucht im Web, '
           + 'erzeugt Diagramme, Tabellen, Karten und Dateien, alles <strong>lokal</strong>. '
           + 'Hänge Dateien per 📎 an, schalte Websuche und Wissensdatenbanken (📚 RAG) zu und '
           + 'blende bei Bedarf den <strong>Denkprozess</strong> rechts ein.',
    },
    {
      image: 'agenten.png',
      title: '3 · Agenten',
      text: 'Agenten sind spezialisierte KI-Profile mit eigenem Auftrag, Werkzeugkasten und Icon – '
           + 'z. B. ein Konstrukteur, ein Rechercheur oder ein Dokument-Agent. Im <strong>🤖 Agenten-Tab</strong> '
           + 'legst du sie an, markierst Favoriten (⭐) für die Schnellauswahl und nutzt sie direkt im Chat. '
           + 'Der <strong>adaptive Agent</strong> leitet die passende Rolle sogar automatisch aus deiner Frage ab.',
    },
    {
      image: 'RAG_Wissensdatenbank.png',
      title: '4 · Wissensdatenbank (RAG)',
      text: 'In <strong>Wissensdatenbanken</strong> legst du eigenes Wissen ab: lade Dokumente hoch oder '
           + 'übernimm ganze Gespräche. Per Schieberegler stellst du „gründlich ↔ schnell" und '
           + '„kreativ ↔ korrekt" ein. Im Chat schaltest du mit <strong>📚 RAG</strong> die passenden Sammlungen '
           + 'zu – die KI antwortet dann <strong>belegt aus deinen Quellen</strong> statt zu raten.',
    },
    {
      image: 'Dokumentengenerator.png',
      title: '5 · Dokumentengenerator',
      text: 'Erzeuge vollständige Dokumente – etwa einen Förderantrag – mit einem '
           + '<strong>Dokument-Agenten</strong>. Optional gestützt auf hochgeladene Quellen, '
           + 'Recherche-Dossiers oder eigene Wissensdatenbanken. Export als Word oder zurück in '
           + 'eine Wissensdatenbank.',
    },
    {
      image: 'Praesentation_canvas.png',
      title: '6 · Präsentation & Canvas',
      text: 'Lass dir Präsentationen als Folien erzeugen und bearbeite sie direkt im '
           + '<strong>Canvas</strong>: Text anklicken, Bilder tauschen, Folien verschieben. '
           + 'Export als PPTX, PDF oder LaTeX/Beamer – im Branding deines Profils.',
    },
    {
      image: 'agentische_recherche.png',
      title: '7 · Agentische Recherche',
      text: 'Die KI zerlegt deine Frage in <strong>Aspekte</strong>, recherchiert im Web, nennt die '
           + 'Quellen und fasst alles zu einem belegten Bericht zusammen – auf Wunsch im strengen '
           + 'Wissenschaftsmodus (keine erfundenen Angaben).',
    },
    {
      image: 'matrix.png',
      title: '8 · Matrix-Recherche',
      text: 'Vergleiche systematisch: Zeilen und Spalten spannen eine <strong>Matrix</strong> auf, '
           + 'jede Zelle wird einzeln recherchiert. Ideal für Markt-, Werkstoff- oder '
           + 'Variantenvergleiche. Im- und Export als CSV.',
    },
    {
      image: 'planer.png',
      title: '9 · Planer',
      text: 'Plane Projekte als <strong>Netzplan mit kritischem Pfad</strong> (CPM): Aufgaben, '
           + 'Abhängigkeiten, Ressourcen mit Kosten und Lieferzeiten, automatischer Bestellplan und '
           + 'Konfliktwarnungen. Die KI hilft beim Ableiten und Detaillieren der Aufgaben.',
    },
    {
      image: 'code.png',
      title: '10 · Code-IDE',
      text: 'Schreibe und führe kleine Programme direkt im Browser aus: links der '
           + '<strong>Code-Editor</strong>, rechts die <strong>Live-Vorschau</strong> mit interaktiven '
           + 'Eingabefeldern und Canvas. Der <strong>KI-Assistent</strong> erzeugt den Code auf Zuruf '
           + '(„was soll das Programm zeigen?") und repariert ihn bei Fehlern. Fertige '
           + '<strong>Beispiele</strong> wie Toleranzanalyse oder Federkennlinie sind ein Klick entfernt. '
           + 'Im Reiter <strong>JSON-Editor</strong> reparierst du defekte JSON-Dateien mit Live-Prüfung.',
    },
    {
      image: 'medizin.png',
      title: '11 · Medizin',
      text: 'Demonstriert eine <strong>Zwei-Modell-Pipeline</strong>: das Standardmodell bereitet deine '
           + 'Frage auf, ein medizinisches Modell (z. B. MedGemma) prüft auf fehlende Angaben und stellt '
           + '<strong>Rückfragen</strong>, bevor es eine fundierte Einschätzung gibt – auf Wunsch in '
           + 'einfaches Deutsch übersetzt. Mit <strong>Patienten-Akten</strong> (eigene Wissensbasis je '
           + 'Patient) und Datei-Upload. <em>Kein Ersatz für ärztliche Beratung.</em>',
    },
    {
      image: 'mathe.png',
      title: '12 · Mathe',
      text: 'Ein eigener <strong>Mathematik-Workspace</strong>: löse Gleichungen, Integrale und '
           + 'Matrizen mit SymPy/NumPy/SciPy, lass <strong>Funktionsgraphen</strong> automatisch '
           + 'zeichnen und exportiere Berichte als <strong>LaTeX/PDF</strong>. Im '
           + '<strong>🎓 Tutor-Modus</strong> führt dich die KI Schritt für Schritt selbst zur Lösung '
           + '– <strong>werkzeuggeprüft</strong> mit SymPy, statt sie sofort zu verraten.',
    },
    {
      image: 'Mail_System.png',
      title: '13 · Mail-Bearbeitung',
      text: 'Lies Postfächer (IMAP/POP3) und verarbeite Mails per Regeln: in eine Wissensdatenbank '
           + 'übernehmen, als Agenten-Aufgabe, an den Dokumentengenerator oder als Notiz. Der '
           + 'Versand bleibt <strong>immer manuell</strong>. <em>(🚧 in Entwicklung)</em>',
    },
    {
      image: 'verzeichnisanalyse.png',
      title: '14 · Verzeichnis-Analyse',
      text: 'Lass die KI einen <strong>Ordner auf dem Rechner</strong> durchsehen: Sie erstellt einen '
           + 'Strukturüberblick, hebt <strong>interessante Dateien</strong> hervor und analysiert sie auf '
           + 'Wunsch im Detail. <strong>Personenbezogene Daten werden dabei automatisch anonymisiert.</strong> '
           + 'Das Ergebnis kommt als <strong>_KI_INDEX.md</strong> in den Ordner zurück oder in eine '
           + 'Wissensdatenbank. <em>(Optionaler Tab – im Profil einblenden.)</em>',
    },
    {
      image: 'morphologischer_kasten.png',
      title: '15 · Morphologischer Kasten',
      text: 'Systematische Ideenfindung (Zwicky-Box): Die KI füllt ein Raster aus <strong>Parametern</strong> '
           + 'und <strong>Ausprägungen</strong>, bewertet Kombinationen und schlägt Alternativen vor. Mit '
           + '<strong>🃏 Ideen wischen</strong> wischst du KI-Konzepte durch (links = gut, rechts = schlecht) '
           + '– ideal am Handy. Gute und schlechte Ideen sammelt ein <strong>Trainingsfile</strong> '
           + 'automatisch. <em>(Optionaler Tab.)</em>',
    },
    {
      image: 'jury.png',
      title: '16 · Jury',
      text: 'Stelle ein <strong>Gremium aus Agenten</strong> zusammen (z. B. ⚖️ Gesetzes-Agenten), das einen '
           + 'beliebigen Text bewertet: Jedes Mitglied gibt Score, Befund, Risiken und Empfehlung ab, danach '
           + 'folgt ein <strong>Gesamturteil</strong>. So prüfst du erzeugte Dokumente, System-Prompts oder '
           + 'Projektpläne. <em>(Optionaler Tab.)</em>',
    },
    {
      image: 'log_file.png',
      title: '17 · Log-Datei',
      text: 'Im <strong>Log-Tab</strong> siehst du bei Bedarf, was im Hintergrund passiert: '
           + 'Modell-Antworten, Tool-Aufrufe und Diagnosen. Praktisch zur Fehlersuche – '
           + 'ein- und ausschaltbar und als Datei exportierbar.',
    },
  ];

  let _idx = 0;
  const _form = { first_name: '', company: '', custom_mode_name: '', custom_mode_prompt: '',
                  model_general: '', enable_systemd: false, data_dir: '' };
  let _models = [];       // Liste der installierten Ollama-Modelle
  let _isLinux = false;   // Plattform-Info vom Backend
  let _embedOk = true;    // Embed-Modell vorhanden?
  let _embedModel = '';   // Name des konfigurierten Embed-Modells

  const IMG_BASE = '/onboarding/';

  function _el(id) { return document.getElementById(id); }

  function _renderFormSlide(s) {
    const modelOptions = _models.length
      ? _models.map(m => `<option value="${m.name}">${m.name}</option>`).join('')
      : '<option value="">Ollama nicht erreichbar – später im Profil wählen</option>';
    const systemdRow = _isLinux ? `
          <div class="ob-field">
            <label class="ob-checkbox-label">
              <input type="checkbox" id="ob-systemd" />
              App beim Systemstart automatisch starten (systemd)
            </label>
          </div>` : '';
    return `
      <div class="ob-slide ob-slide--form" style="background-image:url('${IMG_BASE}${s.image}')">
        <div class="ob-form-panel">
          <h2 class="ob-title">${s.title}</h2>
          <p class="ob-text">${s.text}</p>
          <div class="ob-field">
            <label>Vorname</label>
            <input type="text" id="ob-first-name" class="ob-input" placeholder="z. B. Thomas" autocomplete="off" />
          </div>
          <div class="ob-field">
            <label>Firma <span class="ob-opt">(optional)</span></label>
            <input type="text" id="ob-company" class="ob-input" placeholder="z. B. Muster GmbH" autocomplete="off" />
          </div>
          <div class="ob-divider">⚙ Standard-Einstellungen</div>
          <div class="ob-field">
            <label>Standard-Modell <span class="ob-opt">(jederzeit im Profil änderbar)</span></label>
            <select id="ob-model" class="ob-input">${modelOptions}</select>
          </div>
          <div class="ob-field" id="ob-embed-warn" style="display:none">
            <span style="color:#ffb347;font-size:12.5px">
              ⚠ Embedding-Modell <strong id="ob-embed-name"></strong> nicht gefunden –
              RAG-Wissensdatenbanken werden erst nach
              <code style="background:rgba(255,255,255,0.1);padding:1px 5px;border-radius:4px">ollama pull <span id="ob-embed-name2"></span></code>
              funktionieren.
            </span>
          </div>
          <div class="ob-field">
            <label>Datenverzeichnis <span class="ob-opt">(optional – für Netzlaufwerke oder abweichenden Pfad)</span></label>
            <input type="text" id="ob-data-dir" class="ob-input" placeholder="data" autocomplete="off" />
            <span style="font-size:11px;color:rgba(255,255,255,.4);margin-top:3px;display:block">
              Relativ zum App-Verzeichnis oder absoluter Pfad. Wirkt nach einem Neustart.
            </span>
          </div>
          ${systemdRow}
          <div class="ob-divider">★ Dein eigener Modus ★</div>
          <div class="ob-field">
            <label>Name des Modus</label>
            <input type="text" id="ob-mode-name" class="ob-input" placeholder="z. B. Haustechnik, Recht, Biologie…" autocomplete="off" maxlength="40" />
          </div>
          <div class="ob-field">
            <label>Fachbrille <span class="ob-opt">(wie soll die KI denken?)</span></label>
            <textarea id="ob-mode-prompt" class="ob-input ob-textarea" rows="3"
              placeholder="du bist Ingenieur des Imperialen Sicherheitsbüros (ISB) auf Eadu und ein Experte für Kyber-Kristalle"></textarea>
          </div>
        </div>
      </div>`;
  }

  function _renderInfoSlide(s, big) {
    // „big" (ab Folie „Chat"): Bild füllt die Folie, Text liegt darüber – wie Folie 1.
    if (big) {
      return `
        <div class="ob-slide ob-slide--cover" style="background-image:url('${IMG_BASE}${s.image}')">
          <div class="ob-cover-panel">
            <h2 class="ob-title">${s.title}</h2>
            <p class="ob-text">${s.text}</p>
          </div>
        </div>`;
    }
    // Standard (z. B. Personalisierung): Text + Screenshot nebeneinander, zentriert.
    return `
      <div class="ob-slide ob-slide--info">
        <div class="ob-info-text">
          <h2 class="ob-title">${s.title}</h2>
          <p class="ob-text">${s.text}</p>
        </div>
        <div class="ob-info-image">
          <img src="${IMG_BASE}${s.image}" alt="${s.title}" />
        </div>
      </div>`;
  }

  function render() {
    const s = SLIDES[_idx];
    const body = _el('onboarding-body');
    body.innerHTML = (s.kind === 'form') ? _renderFormSlide(s) : _renderInfoSlide(s, _idx >= 2);

    // Formularwerte (Folie 1) wiederherstellen, falls der Nutzer zurückblättert
    if (s.kind === 'form') {
      _el('ob-first-name').value  = _form.first_name;
      _el('ob-company').value     = _form.company;
      _el('ob-mode-name').value   = _form.custom_mode_name;
      _el('ob-mode-prompt').value = _form.custom_mode_prompt;
      const sel = _el('ob-model');
      if (sel && _form.model_general) sel.value = _form.model_general;
      const dirInp = _el('ob-data-dir');
      if (dirInp) dirInp.value = _form.data_dir;
      const chk = _el('ob-systemd');
      if (chk) chk.checked = _form.enable_systemd;
      setTimeout(() => _el('ob-first-name').focus(), 50);
    }

    // Punkt-Indikator
    const dots = _el('onboarding-dots');
    dots.innerHTML = SLIDES.map((_, i) =>
      `<span class="ob-dot ${i === _idx ? 'active' : ''}" data-i="${i}"></span>`).join('');

    // Navigations-Buttons
    _el('onboarding-back').style.visibility = _idx === 0 ? 'hidden' : 'visible';
    const isLast = _idx === SLIDES.length - 1;
    _el('onboarding-next').textContent = isLast ? 'Los geht’s 🌌' : 'Weiter ›';
  }

  function _captureForm() {
    if (SLIDES[_idx].kind !== 'form') return;
    _form.first_name         = _el('ob-first-name').value.trim();
    _form.company            = _el('ob-company').value.trim();
    _form.custom_mode_name   = _el('ob-mode-name').value.trim();
    _form.custom_mode_prompt = _el('ob-mode-prompt').value.trim();
    const sel = _el('ob-model');
    if (sel) _form.model_general = sel.value;
    const dirInp = _el('ob-data-dir');
    if (dirInp) _form.data_dir = dirInp.value.trim();
    const chk = _el('ob-systemd');
    if (chk) _form.enable_systemd = chk.checked;
  }

  function next() {
    _captureForm();
    if (_idx < SLIDES.length - 1) { _idx++; render(); }
    else finish();
  }

  function back() {
    _captureForm();
    if (_idx > 0) { _idx--; render(); }
  }

  function goto(i) {
    if (i < 0 || i >= SLIDES.length) return;
    _captureForm();
    _idx = i;
    render();
  }

  async function finish() {
    _captureForm();
    // Bestehendes Profil als Basis übernehmen, damit vorhandene Angaben
    // (Persona, Abteilung …) NICHT überschrieben werden.
    const base = (typeof Profile !== 'undefined' && Profile.get) ? (Profile.get() || {}) : {};
    const payload = Object.assign({}, base, {
      first_name:         _form.first_name || base.first_name || '',
      company:            _form.company    || base.company    || '',
      mode:               'custom',
      custom_mode_name:   _form.custom_mode_name   || base.custom_mode_name   || 'Eigener Modus',
      custom_mode_prompt: _form.custom_mode_prompt || base.custom_mode_prompt || '',
      mode_prompt:        true,
      onboarding_done:    true,
      replay_intro:       false,
    });
    // Gewähltes Modell als model_general ins Profil übernehmen
    if (_form.model_general) payload.model_general = _form.model_general;

    try {
      await fetch('/api/profile', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
    } catch (_) { /* lokal – im Zweifel trotzdem schließen */ }

    // Standard-Modell und Datenverzeichnis in config.json persistieren
    if (_form.model_general || _form.data_dir) {
      try {
        const cfg = { default_model: _form.model_general };
        if (_form.data_dir) cfg.data_dir = _form.data_dir;
        await fetch('/api/setup/config', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(cfg),
        });
      } catch (_) {}
    }

    // Systemd-Autostart einrichten (nur Linux, nur wenn Checkbox aktiv)
    if (_form.enable_systemd) {
      try {
        const r = await fetch('/api/setup/systemd', { method: 'POST' });
        const d = await r.json();
        if (!d.ok && typeof showToast === 'function') {
          showToast(`⚠ Systemd: ${d.hint || d.errors?.[0] || 'Fehler'}`, 6000);
        }
      } catch (_) {}
    }

    // Profil neu laden (damit Modal/Branding den gespeicherten Stand zeigen)
    if (typeof Profile !== 'undefined' && Profile.load) { try { await Profile.load(); } catch (_) {} }
    if (typeof Profile !== 'undefined' && Profile.applyMode) Profile.applyMode('custom');
    hide();
    if (typeof showToast === 'function') showToast('🌌 Viel Erfolg im eigenen Modus!');
  }

  async function show() {
    _idx = 0;
    // Formular aus bestehendem Profil vorbefüllen (z. B. bei „erneut abspielen")
    const base = (typeof Profile !== 'undefined' && Profile.get) ? (Profile.get() || {}) : {};
    _form.first_name         = base.first_name || '';
    _form.company            = base.company || '';
    _form.custom_mode_name   = base.custom_mode_name || '';
    _form.custom_mode_prompt = base.custom_mode_prompt || '';
    _form.model_general      = base.model_general || '';
    _form.enable_systemd     = false;
    _form.data_dir           = '';
    _el('onboarding-overlay').style.display = 'flex';
    document.body.style.overflow = 'hidden';

    // Modelle + Plattform + Embed-Check + aktuelle Config parallel laden
    try {
      const [modelsResp, platResp, embedResp, cfgResp] = await Promise.all([
        fetch('/api/models'),
        fetch('/api/platform'),
        fetch('/api/setup/embed-check'),
        fetch('/api/setup/config'),
      ]);
      const modelsData = await modelsResp.json();
      _models = modelsData.models || [];
      const platData = await platResp.json();
      _isLinux = platData.platform === 'linux';
      const embedData = await embedResp.json();
      _embedOk = embedData.ok !== false;
      _embedModel = embedData.embed_model || '';
      try {
        const cfgData = await cfgResp.json();
        if (!_form.data_dir && cfgData.data_dir && cfgData.data_dir !== 'data') {
          _form.data_dir = cfgData.data_dir;
        }
      } catch (_) {}

      // Vorauswahl: gespeichertes Modell, sonst "ministral-3:3b", sonst erstes
      if (!_form.model_general) {
        const preferred = 'ministral-3:3b';
        const found = _models.find(m => m.name === preferred);
        _form.model_general = found ? preferred : (_models[0]?.name || '');
      }
    } catch (_) {}

    render();

    // Embed-Warnung einblenden wenn Modell fehlt (nach render(), damit DOM bereit)
    if (!_embedOk && _embedModel) {
      const warn = _el('ob-embed-warn');
      if (warn) {
        _el('ob-embed-name').textContent  = _embedModel;
        _el('ob-embed-name2').textContent = _embedModel;
        warn.style.display = 'block';
      }
    }
  }

  function hide() {
    _el('onboarding-overlay').style.display = 'none';
    document.body.style.overflow = '';
  }

  // Beim Start aufrufen: zeigt die Einleitung nur beim ersten Mal bzw. auf Wunsch.
  function maybeShow(profile) {
    const p = profile || {};
    if (!p.onboarding_done || p.replay_intro) show();
  }

  function init() {
    _el('onboarding-next').addEventListener('click', next);
    _el('onboarding-back').addEventListener('click', back);
    _el('onboarding-skip').addEventListener('click', finish);
    _el('onboarding-dots').addEventListener('click', e => {
      const dot = e.target.closest('.ob-dot');
      if (dot) goto(parseInt(dot.dataset.i, 10));
    });
    // Tastatur: ← zurück, → / Enter weiter, Esc überspringen
    document.addEventListener('keydown', e => {
      if (_el('onboarding-overlay').style.display !== 'flex') return;
      if (e.key === 'ArrowRight') { e.preventDefault(); next(); }
      else if (e.key === 'ArrowLeft') { e.preventDefault(); back(); }
      else if (e.key === 'Escape') { e.preventDefault(); finish(); }
      else if (e.key === 'Enter' && e.target.tagName !== 'TEXTAREA') { e.preventDefault(); next(); }
    });
  }

  return { init, maybeShow, show, hide };
})();
