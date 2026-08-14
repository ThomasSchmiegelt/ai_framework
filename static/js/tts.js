// tts.js — Sprachausgabe (Text → Sprache) über die Web Speech API des Browsers.
// Zero-Dependency & plattformneutral (Windows/Linux): die Stimmen kommen vom
// Betriebssystem/Browser, es wird NICHTS an einen Server geschickt und NICHTS als
// Datei gespeichert — die Ausgabe entsteht live im Browser.
//
// Jede Antwortstil-Persona (Profil → „tone") bekommt eine passende Stimme. Die Web
// Speech API kennt kein „Alter", daher nähern wir Alter/Klang über Tonhöhe (pitch:
// tiefer = älter) und Tempo (rate) an und wählen – wenn möglich – eine männliche
// bzw. weibliche Systemstimme:
//   roboter   → synthetisch/monoton (tiefe, gleichmäßige Stimme)
//   professor → älterer Mann   (männlich, tiefer, ruhiger)
//   doktor    → ältere Frau    (weiblich, tiefer, ruhig)
//   felix     → junger Mann    (männlich, höher, flotter)
//   sandra    → junge Frau     (weiblich, höher, flott)

const TTS = (function () {
  const PERSONA_VOICE = {
    roboter:   { gender: null,     pitch: 0.35, rate: 0.9,  label: 'Roboter (synthetisch)' },
    professor: { gender: 'male',   pitch: 0.7,  rate: 0.92, label: 'Herr Professor (älterer Mann)' },
    doktor:    { gender: 'female', pitch: 0.82, rate: 0.95, label: 'Frau Doktor (ältere Frau)' },
    felix:     { gender: 'male',   pitch: 1.18, rate: 1.06, label: 'Felix (junger Mann)' },
    sandra:    { gender: 'female', pitch: 1.28, rate: 1.06, label: 'Sandra (junge Frau)' },
    _default:  { gender: null,     pitch: 1.0,  rate: 1.0,  label: 'Standard' },
  };

  // Heuristik für das Geschlecht anhand bekannter Systemstimmen-Namen (best effort;
  // je nach Browser/OS unterschiedlich verfügbar).
  const FEMALE_HINTS = /(female|frau|weib|hedda|katja|zira|helena|anna|marlene|petra|hazel|steffi|vicki|google deutsch|amelie|sonia)/i;
  const MALE_HINTS   = /(male|mann|stefan|david|markus|conrad|hans|paul|jan|klaus|george|ravi|guy|bernd)/i;

  let _voices = [];
  let _current = null;   // aktuell sprechendes Button-Element (für Toggle/Stop)

  function available() {
    return typeof window !== 'undefined' && 'speechSynthesis' in window;
  }

  function _loadVoices() {
    if (!available()) return;
    _voices = window.speechSynthesis.getVoices() || [];
  }

  function _guessGender(v) {
    const n = (v.name || '') + ' ' + (v.voiceURI || '');
    if (FEMALE_HINTS.test(n)) return 'female';
    if (MALE_HINTS.test(n)) return 'male';
    return null;
  }

  // Beste Stimme für Sprache (bevorzugt Deutsch) + gewünschtes Geschlecht wählen.
  function _pickVoice(gender, lang) {
    if (!_voices.length) _loadVoices();
    if (!_voices.length) return null;
    const pref = (lang || 'de').slice(0, 2).toLowerCase();
    const byLang = _voices.filter((v) => (v.lang || '').toLowerCase().startsWith(pref));
    const pool = byLang.length ? byLang : _voices;
    if (gender) {
      const g = pool.find((v) => _guessGender(v) === gender);
      if (g) return g;
    }
    // Bevorzugt eine lokale (offline) Stimme, sonst die erste passende.
    return pool.find((v) => v.localService) || pool[0];
  }

  // Markdown grob zu Vorlese-Text vereinfachen (Zeichen, die man nicht hören will).
  function _clean(text) {
    return (text || '')
      .replace(/```[\s\S]*?```/g, ' Codeblock. ')
      .replace(/`([^`]+)`/g, '$1')
      .replace(/!\[[^\]]*\]\([^)]*\)/g, ' ')
      .replace(/\[([^\]]+)\]\([^)]*\)/g, '$1')
      .replace(/[#>*_~|]/g, ' ')
      .replace(/\s+/g, ' ')
      .trim();
  }

  function stop() {
    if (!available()) return;
    try { window.speechSynthesis.cancel(); } catch (_) {}
    if (_current) { _current.classList.remove('speaking'); _current = null; }
  }

  // Text in der Stimme einer Persona (tone) vorlesen. Ohne tone → Standardstimme.
  function speak(text, tone, opts) {
    if (!available()) return false;
    const clean = _clean(text);
    if (!clean) return false;
    stop();
    const prof = PERSONA_VOICE[tone] || PERSONA_VOICE._default;
    const u = new SpeechSynthesisUtterance(clean.slice(0, 32000));
    const lang = (opts && opts.lang) || 'de-DE';
    const v = _pickVoice(prof.gender, lang);
    if (v) { u.voice = v; u.lang = v.lang || lang; } else { u.lang = lang; }
    u.pitch = prof.pitch;
    u.rate = prof.rate;
    if (opts && opts.onend) u.onend = opts.onend;
    window.speechSynthesis.speak(u);
    return true;
  }

  // Toggle für einen Button: läuft die Ausgabe für DIESEN Button → stoppen,
  // sonst starten und den Button markieren.
  function toggle(btn, text, tone) {
    if (!available()) return;
    if (_current === btn && window.speechSynthesis.speaking) { stop(); return; }
    const ok = speak(text, tone, { onend: () => {
      if (_current === btn) { btn.classList.remove('speaking'); _current = null; }
    } });
    if (ok) { _current = btn; btn.classList.add('speaking'); }
  }

  function personaLabel(tone) {
    return (PERSONA_VOICE[tone] || PERSONA_VOICE._default).label;
  }

  function init() {
    if (!available()) return;
    _loadVoices();
    // Stimmen werden in vielen Browsern asynchron geladen.
    try { window.speechSynthesis.onvoiceschanged = _loadVoices; } catch (_) {}
    // Beim Verlassen der Seite die Ausgabe stoppen.
    window.addEventListener('beforeunload', stop);
  }

  return { init, speak, toggle, stop, available, personaLabel };
})();
