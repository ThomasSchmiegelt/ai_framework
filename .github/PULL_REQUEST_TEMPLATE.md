<!-- Danke für deinen Beitrag! Bitte kurz ausfüllen. -->

## Was & Warum
<!-- Was ändert dieser PR und warum? Bei Bezug auf ein Issue: „Closes #123". -->

## Betroffene Bereiche
<!-- z. B. main.py-Endpoint(s), Frontend-Modul(e), Tab(s), Doku … -->

## Art der Änderung
- [ ] Fehlerbehebung
- [ ] Neue Funktion
- [ ] Dokumentation
- [ ] Refactoring / Aufräumen

## Getestet
<!-- Wie geprüft? (App gestartet, betroffener Tab bedient, ggf. test_chat.py) -->
- Betriebssystem:
- Schritte:

## Checkliste
- [ ] Läuft auf **Windows und Linux** (keine OS-spezifischen Pfade/Abhängigkeiten)
- [ ] **MIT-kompatibel** (keine neue Copyleft-Abhängigkeit)
- [ ] Neue `@app`-Routen liegen **vor** dem `StaticFiles`-Mount in `main.py`
- [ ] Ollama-Aufrufe im `_model_session`-Guard; Modellwahl über `_pick_model`
- [ ] Bei JS/CSS-Änderungen: `?v=`-Cache-Marke (und ggf. `sw.js` `CACHE`) angehoben
- [ ] **Keine Geheimnisse/Laufzeitdaten** committet (`.gitignore` beachtet)
- [ ] Doku aktualisiert (`CLAUDE.md` / `docs/ENTWICKLUNG.md` / `BEDIENUNGSANLEITUNG.md`)
