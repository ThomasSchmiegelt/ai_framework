/* AI_Framework_Thomas — Dynamische Rückfragen („/frag")
   Gemeinsames Modul für Chat, Medizin und Mathe. Fragt den Backend-Endpunkt
   /api/clarify, ob zu einer Aufgabe Rückfragen nötig sind, rendert daraus eine
   Eingabemaske (Text-, Einfach- und Mehrfachauswahl) in ein übergebenes Element
   und liefert die zusammengetragenen Antworten als Promise zurück, die der Aufrufer
   an die normale Verarbeitung des jeweiligen Tabs anhängt.

   Verwendung:
     const res = await Clarify.ask({ task, domain, model, mount });
     // res = { augmentedTask, answered, tokens, noQuestions? }  (oder null bei Abbruch)
*/
const Clarify = (() => {
  'use strict';

  function esc(s) {
    return String(s == null ? '' : s).replace(/[<>&"]/g, c =>
      ({ '<': '&lt;', '>': '&gt;', '&': '&amp;', '"': '&quot;' }[c]));
  }

  // Hauptfunktion: holt die Rückfragen und rendert ggf. die Maske.
  async function ask(opts) {
    const task   = (opts.task || '').trim();
    const domain = opts.domain || 'chat';
    const model  = opts.model || undefined;
    const mount  = opts.mount;
    if (!task) return null;

    if (mount) mount.innerHTML = '<em>⏳ prüfe, ob Rückfragen nötig sind…</em>';

    let data;
    try {
      const r = await fetch('/api/clarify', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ prompt: task, domain, model }),
      });
      data = await r.json();
      if (!r.ok) throw new Error(data.detail || ('HTTP ' + r.status));
    } catch (e) {
      // Bei Fehler nicht blockieren – ohne Rückfragen fortfahren.
      if (mount) mount.innerHTML = '<em>⚠ Rückfragen nicht möglich – fahre direkt fort.</em>';
      return { augmentedTask: task, answered: false, tokens: null };
    }

    const tokens = data.tokens || null;
    const questions = (data.type === 'questions' && Array.isArray(data.questions)) ? data.questions : [];
    if (!questions.length) {
      if (mount) mount.innerHTML = '<em>✓ Keine Rückfragen nötig – bearbeite die Aufgabe direkt.</em>';
      return { augmentedTask: task, answered: false, tokens, noQuestions: true };
    }

    return await new Promise(resolve => {
      _renderForm(mount, questions, (compiled, answered) => {
        resolve({
          augmentedTask: (answered && compiled) ? `${task}\n\n${compiled}` : task,
          answered, tokens,
        });
      });
    });
  }

  // Variante: strukturiert BEREITS gestellte Rückfragen (Freitext des Modells) in
  // dieselbe Maske. Reicht die zusammengetragenen Antworten als Anhang zur Aufgabe
  // zurück, damit der Aufrufer die Aufgabe vervollständigen kann.
  //   const res = await Clarify.askFromText({ questionsText, task, domain, model, mount });
  async function askFromText(opts) {
    const questionsText = (opts.questionsText || '').trim();
    const task   = (opts.task || '').trim();
    const domain = opts.domain || 'chat';
    const model  = opts.model || undefined;
    const mount  = opts.mount;
    if (!questionsText) return null;

    if (mount) mount.innerHTML = '<em>⏳ bereite strukturierte Rückfragen vor…</em>';

    let data;
    try {
      const r = await fetch('/api/clarify/structure', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ questions_text: questionsText, task, domain, model }),
      });
      data = await r.json();
      if (!r.ok) throw new Error(data.detail || ('HTTP ' + r.status));
    } catch (e) {
      if (mount) mount.innerHTML = '<em>⚠ Konnte keine Maske erzeugen – bitte im Chat frei antworten.</em>';
      return null;
    }

    const tokens = data.tokens || null;
    const questions = (data.type === 'questions' && Array.isArray(data.questions)) ? data.questions : [];
    if (!questions.length) {
      if (mount) mount.innerHTML = '<em>Keine strukturierbaren Rückfragen erkannt.</em>';
      return { compiled: '', augmentedTask: task, answered: false, tokens, noQuestions: true };
    }

    return await new Promise(resolve => {
      _renderForm(mount, questions, (compiled, answered) => {
        resolve({
          compiled: (answered && compiled) ? compiled : '',
          augmentedTask: (answered && compiled)
            ? (task ? `${task}\n\n${compiled}` : compiled) : task,
          answered, tokens,
        });
      });
    });
  }

  // Rendert die Eingabemaske in `mount` und ruft `done(compiledText, answered)`.
  function _renderForm(mount, questions, done) {
    if (!mount) { done('', false); return; }
    let html = '<div class="clarify-form">'
      + '<div class="clarify-intro">❓ Ein paar Rückfragen für eine bessere Antwort:</div>';
    questions.forEach((q, i) => {
      html += `<div class="clarify-q" data-i="${i}"><div class="clarify-q-text">${i + 1}. ${esc(q.question)}</div>`;
      if (q.type === 'single' || q.type === 'multi') {
        const inputType = q.type === 'single' ? 'radio' : 'checkbox';
        html += '<div class="clarify-opts">';
        (q.options || []).forEach((o, k) => {
          html += `<label class="clarify-opt"><input type="${inputType}" name="cq${i}" value="${esc(o)}" data-i="${i}"> ${esc(o)}</label>`;
        });
        // „Andere"-Option mit Freitext
        html += `<label class="clarify-opt"><input type="${inputType}" name="cq${i}" value="__other__" data-i="${i}" class="clarify-other-toggle"> Andere:`
          + ` <input type="text" class="clarify-other-text" data-i="${i}" placeholder="…" disabled></label>`;
        html += '</div>';
      } else {
        html += `<textarea class="clarify-text" data-i="${i}" rows="2" placeholder="Antwort…"></textarea>`;
      }
      html += '</div>';
    });
    html += '<div class="clarify-actions">'
      + '<button type="button" class="export-btn clarify-submit">↑ Antworten &amp; fortfahren</button>'
      + '<button type="button" class="export-btn clarify-skip">⏭ Ohne Rückfragen</button>'
      + '</div></div>';
    mount.innerHTML = html;

    // „Andere"-Freitextfeld aktivieren/deaktivieren je nach Auswahl
    mount.querySelectorAll('.clarify-opts').forEach(group => {
      group.addEventListener('change', () => {
        const otherToggle = group.querySelector('.clarify-other-toggle');
        const otherText = group.querySelector('.clarify-other-text');
        if (otherToggle && otherText) {
          otherText.disabled = !otherToggle.checked;
          if (otherToggle.checked) otherText.focus();
        }
      });
    });

    const finish = (answered) => {
      const compiled = answered ? _compile(mount, questions) : '';
      const reallyAnswered = answered && !!compiled;
      _renderSummary(mount, questions, reallyAnswered);
      done(compiled, reallyAnswered);
    };
    mount.querySelector('.clarify-submit')?.addEventListener('click', () => finish(true));
    mount.querySelector('.clarify-skip')?.addEventListener('click', () => finish(false));
  }

  // Sammelt die Antworten und baut den Anhangstext.
  function _compile(mount, questions) {
    const lines = [];
    questions.forEach((q, i) => {
      let answer = '';
      if (q.type === 'single' || q.type === 'multi') {
        const sel = Array.from(mount.querySelectorAll(`input[name="cq${i}"]:checked`));
        const vals = [];
        for (const el of sel) {
          if (el.value === '__other__') {
            const other = mount.querySelector(`.clarify-other-text[data-i="${i}"]`);
            const ov = (other?.value || '').trim();
            if (ov) vals.push(ov);
          } else {
            vals.push(el.value);
          }
        }
        answer = vals.join(', ');
      } else {
        const ta = mount.querySelector(`.clarify-text[data-i="${i}"]`);
        answer = (ta?.value || '').trim();
      }
      if (answer) lines.push(`- ${q.question} ${answer}`);
    });
    if (!lines.length) return '';
    return 'Zusätzliche Angaben zur Aufgabe:\n' + lines.join('\n');
  }

  // Ersetzt die Maske durch eine schlanke Zusammenfassung (Beleg im Verlauf).
  function _renderSummary(mount, questions, answered) {
    if (!answered) { mount.innerHTML = '<em>⏭ Ohne Rückfragen fortgefahren.</em>'; return; }
    let html = '<div class="clarify-summary"><div class="clarify-intro">✓ Rückfragen beantwortet:</div><ul>';
    questions.forEach((q, i) => {
      let answer = '';
      if (q.type === 'single' || q.type === 'multi') {
        const sel = Array.from(mount.querySelectorAll(`input[name="cq${i}"]:checked`));
        const vals = [];
        for (const el of sel) {
          if (el.value === '__other__') {
            const other = mount.querySelector(`.clarify-other-text[data-i="${i}"]`);
            const ov = (other?.value || '').trim();
            if (ov) vals.push(ov);
          } else { vals.push(el.value); }
        }
        answer = vals.join(', ');
      } else {
        const ta = mount.querySelector(`.clarify-text[data-i="${i}"]`);
        answer = (ta?.value || '').trim();
      }
      if (answer) html += `<li><strong>${esc(q.question)}</strong> ${esc(answer)}</li>`;
    });
    html += '</ul></div>';
    mount.innerHTML = html;
  }

  return { ask, askFromText };
})();
