# Weitere Vorlagen — „Modern Blau"

Dieser Ordner enthält die **firmeneigenen Branding-Vorlagen**, die **ausschließlich im
Modus „Modern Blau"** (Profil → Modus) verwendet werden — für **Präsentationen**
(Deckblatt / Kopfzeile / Abschlussfolie) **und** für die **Dokument-Exporte** (Logo in
PDF/DOCX von Rechnungen, Zeugnissen, Berichten …).

> **Diese Bilddateien werden NICHT ins GitHub eingecheckt** (siehe `.gitignore`).
> Nur diese `README.md` und die `.gitkeep` sind versioniert. Kopiere deine eigenen
> Vorlagen hier hinein — sie bleiben rein lokal.

## Erwartete Dateien (einfach hier hineinkopieren)

| Zweck | Dateiname (bevorzugt) | Auch erkannt | Empfohlene Größe |
|---|---|---|---|
| **Deckblatt** (Titelfolie, vollflächig) | `Deckblatt.png` | `cover.png/.jpg` | 1920×1080 px |
| **Kopfzeile** (Banner oben auf Inhaltsfolien) | `inhaltsfolie.png` | `header.png/.jpg` | 1920×~240 px |
| **Abschlussfolie** (letzte Folie, vollflächig) | `Abschlussfolie.png` | `closing.png/.jpg` | 1920×1080 px |
| **Logo** (Ecke, transparent) | `logo.png` | `logo.jpg` | 512×512 px, PNG mit Transparenz |

Die Namen sind **nicht case-sensitiv**; PNG **oder** JPG wird akzeptiert. Fehlt eine
Datei, greift für diesen Zweck das normale Profil-Branding bzw. eine schlichte
Farb­folie — es entsteht kein Fehler.

## Wirkung

* Aktiv nur, solange im Profil der Modus **„Modern Blau"** gewählt ist.
* In **anderen Modi** werden diese Dateien **ignoriert** (dort gilt das übliche
  Profil-Branding aus `data/profile_assets/`).
* Änderungen wirken nach dem Umschalten des Modus (Präsentation ggf. neu rendern);
  bei Dokument-Exporten sofort beim nächsten Export.
