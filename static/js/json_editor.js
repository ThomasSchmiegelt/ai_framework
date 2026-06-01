/* AI_Framework_Thomas — JSON-Editor
   Öffnet JSON-Dateien, prüft sie live (mit Zeile/Spalte bei Fehlern),
   formatiert und lädt die korrigierte Datei wieder herunter.
   Gedacht für nicht programmieraffine Nutzer zum Reparieren kaputter JSON-Dateien. */

const JsonEditor = (() => {

  let _filename = 'daten.json';
  let _area = null, _gutter = null, _status = null;
  let _debounce = null;

  function _setStatus(ok, msg) {
    if (!_status) return;
    _status.textContent = msg;
    _status.classList.toggle('ok', ok);
    _status.classList.toggle('err', !ok);
  }

  /* Validiert den Text und gibt {ok, msg} mit Zeile/Spalte bei Fehlern zurück */
  function _validate(text) {
    if (!text.trim()) return { ok: true, msg: '' };
    try {
      JSON.parse(text);
      return { ok: true, msg: '✓ Gültiges JSON' };
    } catch (e) {
      let where = '';
      const m = /position (\d+)/i.exec(e.message);
      if (m) {
        const pos = +m[1];
        const before = text.slice(0, pos);
        const line = before.split('\n').length;
        const col = pos - before.lastIndexOf('\n');
        where = ` (Zeile ${line}, Spalte ${col})`;
      }
      return { ok: false, msg: '✗ ' + e.message + where };
    }
  }

  function _updateGutter() {
    if (!_gutter || !_area) return;
    const lines = _area.value.split('\n').length;
    let s = '';
    for (let i = 1; i <= lines; i++) s += i + '\n';
    _gutter.textContent = s;
    _gutter.scrollTop = _area.scrollTop;
  }

  function _onInput() {
    _updateGutter();
    clearTimeout(_debounce);
    _debounce = setTimeout(() => {
      const v = _validate(_area.value);
      _setStatus(v.ok, v.msg);
    }, 250);
  }

  function _openFile() {
    const inp = document.getElementById('json-file-input');
    inp.value = '';
    inp.click();
  }

  function _loadFile(file) {
    _filename = file.name || 'daten.json';
    const fn = document.getElementById('json-filename');
    if (fn) fn.textContent = _filename;
    const reader = new FileReader();
    reader.onload = e => {
      _area.value = e.target.result;
      _onInput();
    };
    reader.readAsText(file, 'utf-8');
  }

  function _format() {
    const v = _validate(_area.value);
    if (!v.ok) { _setStatus(false, v.msg); showToast('Erst Fehler beheben – ' + v.msg); return; }
    if (!_area.value.trim()) return;
    try {
      _area.value = JSON.stringify(JSON.parse(_area.value), null, 2);
      _onInput();
      showToast('✓ Formatiert');
    } catch (_) {}
  }

  function _validateBtn() {
    const v = _validate(_area.value);
    _setStatus(v.ok, v.msg);
    showToast(v.ok ? '✓ Gültiges JSON' : v.msg);
  }

  function _download() {
    const blob = new Blob([_area.value], { type: 'application/json;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = _filename.endsWith('.json') ? _filename : _filename + '.json';
    a.click();
    URL.revokeObjectURL(url);
  }

  function init() {
    _area = document.getElementById('json-editor-area');
    _gutter = document.getElementById('json-gutter');
    _status = document.getElementById('json-status');
    if (!_area) return;

    _area.addEventListener('input', _onInput);
    _area.addEventListener('scroll', () => { if (_gutter) _gutter.scrollTop = _area.scrollTop; });
    document.getElementById('btn-json-open')?.addEventListener('click', _openFile);
    document.getElementById('json-file-input')?.addEventListener('change', e => {
      if (e.target.files[0]) _loadFile(e.target.files[0]);
    });
    document.getElementById('btn-json-format')?.addEventListener('click', _format);
    document.getElementById('btn-json-validate')?.addEventListener('click', _validateBtn);
    document.getElementById('btn-json-download')?.addEventListener('click', _download);

    // Tab-Taste im Editor erlauben (2 Leerzeichen statt Fokuswechsel)
    _area.addEventListener('keydown', e => {
      if (e.key === 'Tab') {
        e.preventDefault();
        const s = _area.selectionStart, en = _area.selectionEnd;
        _area.value = _area.value.slice(0, s) + '  ' + _area.value.slice(en);
        _area.selectionStart = _area.selectionEnd = s + 2;
      }
    });

    _updateGutter();
  }

  return { init };

})();
