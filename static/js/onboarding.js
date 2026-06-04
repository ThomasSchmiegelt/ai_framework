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
      image: 'Mail_System.png',
      title: '11 · Mail-Bearbeitung',
      text: 'Lies Postfächer (IMAP/POP3) und verarbeite Mails per Regeln: in eine Wissensdatenbank '
           + 'übernehmen, als Agenten-Aufgabe, an den Dokumentengenerator oder als Notiz. Der '
           + 'Versand bleibt <strong>immer manuell</strong>. <em>(🚧 in Entwicklung)</em>',
    },
    {
      image: 'log_file.png',
      title: '12 · Log-Datei',
      text: 'Im <strong>Log-Tab</strong> siehst du bei Bedarf, was im Hintergrund passiert: '
           + 'Modell-Antworten, Tool-Aufrufe und Diagnosen. Praktisch zur Fehlersuche – '
           + 'ein- und ausschaltbar und als Datei exportierbar.',
    },
  ];

  let _idx = 0;
  const _form = { first_name: '', company: '', custom_mode_name: '', custom_mode_prompt: '' };

  const IMG_BASE = '/onboarding/';

  function _el(id) { return document.getElementById(id); }

  function _renderFormSlide(s) {
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
      _el('ob-first-name').value   = _form.first_name;
      _el('ob-company').value      = _form.company;
      _el('ob-mode-name').value    = _form.custom_mode_name;
      _el('ob-mode-prompt').value  = _form.custom_mode_prompt;
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
    _form.first_name        = _el('ob-first-name').value.trim();
    _form.company           = _el('ob-company').value.trim();
    _form.custom_mode_name  = _el('ob-mode-name').value.trim();
    _form.custom_mode_prompt = _el('ob-mode-prompt').value.trim();
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
    // (Modell-Rollen, Persona, Abteilung …) NICHT überschrieben werden.
    const base = (typeof Profile !== 'undefined' && Profile.get) ? (Profile.get() || {}) : {};
    // Profil speichern – Framework startet danach im „eigenen Modus" (violett)
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
    try {
      await fetch('/api/profile', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
    } catch (_) { /* lokal – im Zweifel trotzdem schließen */ }

    // Profil neu laden (damit Modal/Branding den gespeicherten Stand zeigen)
    if (typeof Profile !== 'undefined' && Profile.load) { try { await Profile.load(); } catch (_) {} }
    if (typeof Profile !== 'undefined' && Profile.applyMode) Profile.applyMode('custom');
    hide();
    if (typeof showToast === 'function') showToast('🌌 Viel Erfolg im eigenen Modus!');
  }

  function show() {
    _idx = 0;
    // Formular aus bestehendem Profil vorbefüllen (z. B. bei „erneut abspielen")
    const base = (typeof Profile !== 'undefined' && Profile.get) ? (Profile.get() || {}) : {};
    _form.first_name         = base.first_name || '';
    _form.company            = base.company || '';
    _form.custom_mode_name   = base.custom_mode_name || '';
    _form.custom_mode_prompt = base.custom_mode_prompt || '';
    _el('onboarding-overlay').style.display = 'flex';
    document.body.style.overflow = 'hidden';
    render();
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
