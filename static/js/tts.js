// tts.js — Sprachausgabe (Text → Sprache).
//
// Zwei Wege, automatisch gewählt:
//   • STANDARD: Web Speech API des Browsers (zero-dependency, plattformneutral,
//     lokal, kostenlos, nichts wird gespeichert).
//   • OPTIONAL: ein im Profil gewähltes API-TTS-Modell (``anbieter::modell``,
//     z. B. openai::tts-1) → Backend POST /api/tts liefert Audio, das wir abspielen.
//     Im Geheim-Modus wird immer der Browser genutzt; scheitert die API, fällt die
//     Ausgabe automatisch auf den Browser zurück.
//
// Jede Antwortstil-Persona (Profil → „tone") bekommt eine passende Stimme. Beim
// Browser nähern wir Alter/Klang über Tonhöhe (pitch: tiefer = älter) und wählen –
// wenn möglich – eine männliche/weibliche Systemstimme; beim API-Weg mappt das
// Backend die Persona auf die Anbieter-Stimme.

const TTS = (function () {
  const PERSONA_VOICE = {
    roboter:   { gender: null,     pitch: 0.7,  rate: 1.05, label: 'Roboter (synthetisch)' },
    professor: { gender: 'male',   pitch: 0.92, rate: 1.08, label: 'Herr Professor (älterer Mann)' },
    doktor:    { gender: 'female', pitch: 0.96, rate: 1.08, label: 'Frau Doktor (ältere Frau)' },
    felix:     { gender: 'male',   pitch: 1.06, rate: 1.12, label: 'Felix (junger Mann)' },
    sandra:    { gender: 'female', pitch: 1.1,  rate: 1.12, label: 'Sandra (junge Frau)' },
    hartman:   { gender: 'male',   pitch: 0.8,  rate: 1.28, label: 'Gunnery Sergeant (zackig, laut)' },
    _default:  { gender: null,     pitch: 1.0,  rate: 1.05, label: 'Standard' },
  };

  const FEMALE_HINTS = /(female|frau|weib|hedda|katja|zira|helena|anna|marlene|petra|hazel|steffi|vicki|google deutsch|amelie|sonia)/i;
  const MALE_HINTS   = /(male|mann|stefan|david|markus|conrad|hans|paul|jan|klaus|george|ravi|guy|bernd)/i;

  let _voices = [];
  let _current = null;   // aktuell sprechendes Button-Element (Toggle/Stop)
  let _audio = null;     // aktuelles <audio> für den API-Weg

  function _hasWebSpeech() {
    return typeof window !== 'undefined' && 'speechSynthesis' in window;
  }

  // Im Profil gewähltes API-TTS-Modell (leer, wenn Browser genutzt werden soll bzw.
  // im Geheim-Modus).
  function _apiModel() {
    const p = (typeof Profile !== 'undefined' && Profile.get) ? (Profile.get() || {}) : {};
    const m = (p.tts_model || '').trim();
    if (!m || m.indexOf('::') < 0) return '';
    if (p.local_only_mode) return '';   // Geheim-Modus → Browser
    if (String(p.tone || '').toLowerCase() === 'hartman') return '';  // Ausbildungs-/Lokal-Riegel → Browser
    return m;
  }

  function available() {
    return _hasWebSpeech() || !!_apiModel();
  }

  function _loadVoices() {
    if (!_hasWebSpeech()) return;
    _voices = window.speechSynthesis.getVoices() || [];
  }

  function _guessGender(v) {
    const n = (v.name || '') + ' ' + (v.voiceURI || '');
    if (FEMALE_HINTS.test(n)) return 'female';
    if (MALE_HINTS.test(n)) return 'male';
    return null;
  }

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
    return pool.find((v) => v.localService) || pool[0];
  }

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
    if (_hasWebSpeech()) { try { window.speechSynthesis.cancel(); } catch (_) {} }
    if (_audio) { try { _audio.pause(); } catch (_) {} _audio = null; }
    if (_current) { _current.classList.remove('speaking'); _current = null; }
  }

  // API-Weg: Text am Backend synthetisieren und abspielen. Rückgabe false → Fallback.
  async function _speakApi(text, tone, onend) {
    try {
      const resp = await fetch('/api/tts', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text: text.slice(0, 4000), tone: tone || '' }),
      });
      if (!resp.ok) return false;   // 409 (nicht aktiv) o. Ä. → Browser-Fallback
      const url = URL.createObjectURL(await resp.blob());
      const a = new Audio(url);
      _audio = a;
      const done = () => { URL.revokeObjectURL(url); if (_audio === a) _audio = null; if (onend) onend(); };
      a.onended = done;
      a.onerror = done;
      await a.play();
      return true;
    } catch (_) { return false; }
  }

  function _speakBrowser(text, tone, lang, onend) {
    if (!_hasWebSpeech()) { if (onend) onend(); return false; }
    const clean = _clean(text);
    if (!clean) return false;
    const prof = PERSONA_VOICE[tone] || PERSONA_VOICE._default;
    const u = new SpeechSynthesisUtterance(clean.slice(0, 32000));
    const v = _pickVoice(prof.gender, lang);
    if (v) { u.voice = v; u.lang = v.lang || lang; } else { u.lang = lang; }
    u.pitch = prof.pitch;
    u.rate = prof.rate;
    if (onend) u.onend = onend;
    window.speechSynthesis.speak(u);
    return true;
  }

  // Text in der Stimme einer Persona (tone) vorlesen. API bevorzugt (falls gewählt),
  // sonst Browser; scheitert die API, wird auf den Browser zurückgefallen.
  async function speak(text, tone, opts) {
    const raw = (text || '').trim();
    if (!raw) return false;
    stop();
    const lang = (opts && opts.lang) || 'de-DE';
    const onend = opts && opts.onend;
    if (_apiModel()) {
      const ok = await _speakApi(_clean(raw), tone, onend);
      if (ok) return true;   // sonst: Browser-Fallback
    }
    return _speakBrowser(raw, tone, lang, onend);
  }

  function _isPlaying(btn) {
    if (_current !== btn) return false;
    if (_audio && !_audio.paused) return true;
    return _hasWebSpeech() && window.speechSynthesis.speaking;
  }

  // Toggle für einen Button: läuft die Ausgabe für DIESEN Button → stoppen, sonst
  // starten und den Button markieren.
  async function toggle(btn, text, tone) {
    if (_isPlaying(btn)) { stop(); return; }
    const ok = await speak(text, tone, { onend: () => {
      if (_current === btn) { btn.classList.remove('speaking'); _current = null; }
    } });
    if (ok) { _current = btn; btn.classList.add('speaking'); }
  }

  function personaLabel(tone) {
    return (PERSONA_VOICE[tone] || PERSONA_VOICE._default).label;
  }

  function init() {
    if (_hasWebSpeech()) {
      _loadVoices();
      try { window.speechSynthesis.onvoiceschanged = _loadVoices; } catch (_) {}
    }
    window.addEventListener('beforeunload', stop);
  }

  return { init, speak, toggle, stop, available, personaLabel };
})();
