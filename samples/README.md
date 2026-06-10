# Beispieldateien

Beispiel-Daten zum Ausprobieren der **Anfrage-Auswertung (📋 Anfrage)** und des **Planers** —
thematisch rund um die **Raumfahrt**.

## Anfragen (für den Tab „📋 Anfrage")

| Datei | Inhalt |
|---|---|
| `Beispielanfrage_Raumfahrt_CubeSat.xlsx` | Ausschreibung „Entwicklung und Bau eines Erdbeobachtungs-Kleinsatelliten (12U-CubeSat)" mit **26 Arbeitspaketen** (Struktur, Thermal, Energie, Avionik, Lageregelung, Antrieb, optische Nutzlast, Onboard-KI, Kommunikation, Strahlung, Qualifikation/TVAC, Start & Bodensegment). |

**So nutzen:** Tab **📋 Anfrage** → Datei hochladen → als Aufgaben-Spalte **„Beschreibung / Anforderung"** wählen, als ID-Spalte **„Pos"** → Testlauf starten. Danach **„➜ In Planer übernehmen"** für Kapazitäts-/Zukauf-Analyse oder **„📚 In RAG übernehmen"**.

## Ressourcen-/Kapazitätslisten

Format: `Typ;Name;Satz;Land;Kapazität;Skills` (importierbar in **👥 Kapazitätslisten** im Anfrage-Tab und als Katalog im **Planer**).

| Datei | Passt zu |
|---|---|
| `Beispiel_Ressourcenliste_Raumfahrt.csv` | Raumfahrt-Unternehmen (Systemtechnik, Struktur/FEM, Thermal, Avionik/OBC, AOCS, Antrieb, Optik, Flugsoftware, RF, Test/Qualifikation, Launch-Partner …) — zur CubeSat-Anfrage oben. |
| `Beispiel_Ressourcenliste_KI.csv` | Kleiner Raumfahrt-Zulieferer mit **Onboard-KI-Projekt** (Edge-Inferenz, Wolkenerkennung, Datenreduktion an Bord). |
| `Beispiel_Ressourcenliste.csv` | Generische Ressourcenliste (nur `Typ;Name;Satz`). |

**Tipp:** Erst die passende Ressourcenliste unter **👥 Kapazitätslisten → ⬆ CSV-Import → 💾 Speichern** laden und im Anfrage-Tab per Häkchen **aktiv** setzen, dann die Anfrage auswerten und übernehmen — so sind Land/Kapazität/Satz für „Zuständig", „Best-Cost-Country" und die Make-or-Buy-Analyse hinterlegt.
