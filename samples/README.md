# Beispieldateien

Beispiel-Daten zum Ausprobieren der **Anfrage-Auswertung (📋 Anfrage)** und des **Planers**.

## Anfragen (für den Tab „📋 Anfrage")

| Datei | Inhalt |
|---|---|
| `Beispielanfrage_Robotik_Muellsammler.xlsx` | Ausschreibung „Konstruktion eines Robotiksystems zum Aufsammeln von Müll in Parkanlagen" mit **26 Arbeitspaketen** (Mechanik, Elektronik, KI/Vision, Navigation, Sicherheit, Zulassung, Service). |

**So nutzen:** Tab **📋 Anfrage** → Datei hochladen → als Aufgaben-Spalte **„Beschreibung / Anforderung"** wählen, als ID-Spalte **„Pos"** → Testlauf starten. Danach **„➜ In Planer übernehmen"** für Kapazitäts-/Zukauf-Analyse.

## Ressourcen-/Kapazitätslisten

Format: `Typ;Name;Satz;Land;Kapazität;Skills` (importierbar in **👥 Kapazitätsliste** im Anfrage-Tab und als Katalog im **Planer**).

| Datei | Passt zu |
|---|---|
| `Beispiel_Ressourcenliste_Robotik.csv` | Robotik-Unternehmen (Mechanik, Elektronik/PCB, Embedded, KI/Vision, SLAM, Safety, CE-Partner …) — zur Robotik-Anfrage oben. |
| `Beispiel_Ressourcenliste_KI.csv` | Kleines Unternehmen mit KI-Einführungsprojekt. |
| `Beispiel_Ressourcenliste.csv` | Generische Ressourcenliste (nur `Typ;Name;Satz`). |

**Tipp:** Erst die passende Ressourcenliste unter **👥 Kapazitätsliste → ⬆ CSV-Import → 💾 Speichern** laden, dann die Anfrage auswerten und übernehmen — so sind Land/Kapazität/Satz für „Zuständig", „Best-Cost-Country" und die Make-or-Buy-Analyse hinterlegt.
