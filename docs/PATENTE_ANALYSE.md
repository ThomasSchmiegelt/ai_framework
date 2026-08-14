# Das perfekte Patent-Research-Tool — Recherche, Gap-Analyse & Roadmap

*Stand: Juli 2026 · Grundlage: Marktrecherche (PatSnap, Derwent, Orbit, Lens.org, PQAI,
Patlytics, Solve Intelligence, XLSCOUT) + Code-Inventur des Patente-Tabs.*

## 1. Die sechs Säulen der Referenzklasse

Kommerzielle Plattformen kosten 25–80 k$/Jahr (PatSnap ~30–60 k$, Derwent 40–80 k$,
Orbit 25–50 k$). Was sie — und die besten freien Werkzeuge — gemeinsam haben:

| Säule | Kern | Referenz |
|---|---|---|
| **1. Verlässliche Daten** | Amtliche Quellen: Rechtsstand (INPADOC), Patentfamilien, Anmelde-/Prioritäts-/Publikationsdaten, Erfinder, IPC **und** CPC, Volltexte, Vorwärts-/Rückwärtszitate | EPO OPS (kostenlos, 130+ Mio. Dokumente), PATENTSCOPE, Lens.org |
| **2. Mächtige Suche** | Feld-/Boolean-Suche (IPC/CPC, Anmelder, Datum, Land) **plus** semantische Suche über Embeddings | PQAI (Open Source), BERT-for-Patents, PatentSBERTa |
| **3. Strukturierte Analyse** | Element-weises **Claim-Mapping/Merkmalsanalyse**: Anspruch 1 → Merkmale M1…Mn, je Merkmal Fundstelle + Bewertung (identisch/ähnlich/fehlt) — Grundlage für Neuheit, FTO, Umgehung | Patlytics, XLSCOUT ClaimChart LLM |
| **4. Landscape/Statistik** | Anmelder-Ranking, Zeitreihen, IPC-Cluster, Zitationsnetze, White-Space-Analyse | PatSnap 3D-Landscape, PatSeer, VOSviewer/Gephi |
| **5. Monitoring** | Gespeicherte Suchen, Alerts auf neue Anmeldungen/Zitate/Rechtsstandsänderungen — FTO ist ein **Dauerprozess** | Patlytics, IamIP, ClearstoneIP |
| **6. Halluzinationskontrolle** | RAG-Grounding, Quellenpflicht je Aussage, Mensch-im-Loop; LLM-Bewertungen nie ohne Beleg | Clemson-Studie „Hallucinations and Hits", Patlytics-Workflow |

## 2. Ist-Zustand des Patente-Tabs

**Stärken (auf Augenhöhe mit teuren Tools, teils einzigartig):**
- 7-Stufen-Experten-Pipeline (Technik → Recht → Umgehung → Innovation → Entwurf →
  Kritik → Moderator) mit FREIGABE-Prüfschleife (Zwei-Modell-Validierung)
- Fallakten-Prinzip mit `[[Wikilink]]`-Wissens-Wiki, RAG-„Akte fragen", Wissensgraph
- **100 % lokal möglich** — kein anderes Werkzeug dieser Klasse läuft ohne Cloud

**Lücken vor Ausbaustufe 1 (Code-Inventur):**
- Einzige Quelle Google-Scraping: kein Rechtsstand, keine Familien, keine Daten/Erfinder/
  CPC/Beschreibung; ToS-Risiko; kein Rate-Limit/Retry/Cache; Fehler still verschluckt
- Nur Keyword-Suche (max. 50 Treffer), kein Boolean-/Feld-/Datumsfilter
- Ansprüche gekürzt (RAG 4000 Z., Mehrfachanalyse 500 Z.) — Anspruchssätze gingen verloren
- Keine Merkmalsanalyse/Claim-Charts; Prüfschleife nur für Stufe 1
- Kein Monitoring, keine Landscape-Statistik, keine semantische Fremdsuche

## 3. Ausbaustufe 1 — umgesetzt

1. **EPO-OPS-Anbindung** (`tools/epo_ops.py`): OAuth2-Client mit Token-Cache,
   Fair-Use-Drossel + Backoff; Biblio (Titel/Abstract/IPC/CPC/Anmelder/Erfinder/Daten),
   Ansprüche/Beschreibung (wo verfügbar), INPADOC-Familie, Rechtsstand-Ereignisse,
   CQL-Suche. Key-Verwaltung im Tab (`data/epo_ops.json`, gitignored, Backup nur mit
   `secrets`-Schalter). **Google bleibt Fallback/Ergänzung** — der vereinheitlichte
   Abruf `tools/patente.fetch_patent()` merged beide Quellen (amtliche Felder gewinnen,
   `source` dokumentiert die Herkunft).
2. **Robustes Scraping**: globale Drossel (2 s Mindestpause) + Backoff bei 429/5xx,
   Datei-Cache `data/patente/_cache/` (30 Tage), Fehler werden gemeldet statt
   verschluckt; zusätzlich geparst: Beschreibung, Daten, Erfinder, Vorwärtszitate.
3. **Merkmalsanalyse als neue Pipelinestufe** (`run_merkmalsanalyse`): Anspruch 1 wird
   deterministisch extrahiert (`extract_claim1`, ungekürzt) und element-weise in eine
   Markdown-Tabelle zerlegt; bei zwei Dokumenten Gegenüberstellung mit Bewertung
   identisch/ähnlich/fehlt je Merkmal. Eigene FREIGABE-Prüfschleife; Ergebnis fließt
   als Kontext in Recht + Umgehung. Kontextbudget skaliert jetzt mit dem Profil-
   Kontextfenster statt fixer 500-Zeichen-Kürzung.
4. **Feld-/Boolean-Suche**: IPC/CPC, Datum von/bis, Boolean-Freitext; mit OPS-Key als
   amtliche CQL-Suche, sonst als Google-Query; Quelle-Badge (🏛 EPO / 🌐 Google) und
   Fehlertext in der Trefferliste.
5. RAG-Indizierung + CSV-Export um die amtlichen Felder erweitert.

## 4. Ausbaustufe 2 — umgesetzt: die Auswertung (Prüfer-Methodik)

Methodische Grundlage: EPA-**Aufgabe-Lösungs-Ansatz** (nächstliegender Stand der
Technik → Unterschiedsmerkmale → objektive technische Aufgabe → Could-Would-Test),
**Element-für-Element-Methode** der Invalidity-Praxis, **All-Elements-Rule** für
Verletzung/FTO, und die Erkenntnis der PatRe-/AutoPatent-Forschung, dass LLMs
juristische Form imitieren, aber bei belegbasiertem Prior-Art-Reasoning
deterministische Grundwahrheiten + Prüfschleifen brauchen.

1. **Stärke-Score (deterministisch, kein LLM)** — `patent_kennzahlen()`:
   Restlaufzeit (Anmeldetag + 20 J.), Vorwärtszitate, Familiengröße, Anspruchszahl,
   Anspruch-1-Breite (Wortzahl), Rechtsstand-*Hinweis* (Heuristik über
   legal-Events). Score 0–100 = gewichtete Triage (Zitate log 35 % · Familie 20 % ·
   Restlaufzeit 20 % · Anspruchsbreite 15 % · Anspruchszahl 10 %) — Priorisierung,
   keine Wertermittlung. Sichtbar als Spalte (sortierbar) + Badges in Akte/Detail;
   Kennzahlen-Tabelle (`kennzahlen_markdown`) geht als zitierbare Grundwahrheit an
   den Moderator.
2. **Prüfer-Pipeline**: neue Stufe **„🧪 Neuheit & erfinderische Tätigkeit"** nach
   Aufgabe-Lösungs-Ansatz mit eigener FREIGABE-Prüfschleife; nächstliegender SdT
   kommt aus der Projekt-RAG-Collection (Anspruch-1-Query, eigene Dokumente
   ausgefiltert). **Recht** strukturiert je Merkmal (Tabelle Auslegung/Breite/
   Schwachstelle), **Moderator** = Management-Summary mit Kennzahlen-Tabelle und
   expliziter Handlungsempfehlung (Neuanmeldung/Umgehen/Lizenz/Beobachten/Ignorieren).
3. **🛡 FTO-Produkt-Check** (`run_fto_check`, `POST /api/patente/projects/{name}/fto`):
   Claim-Chart Anspruch 1 ↔ **eigene Produkt-/Ideenbeschreibung** je Patent (Cap 5),
   Ampel je Merkmal (verwirklicht/nicht/unklar) mit wörtlicher Fundstellen-Pflicht,
   All-Elements-Rule im Fazit, FREIGABE-Prüfschleife, Pflicht-Hinweis „keine
   Rechtsberatung". Als Analyse-Typ `FTO_Check` gespeichert + RAG-indiziert.
4. **📊 Akte-Statistik** (rein Frontend, kein LLM): Top-Anmelder, Anmeldungen/Jahr,
   IPC-Hauptklassen, Score-Verteilung, **White-Space-Matrix** (IPC × Anmelder,
   leere Zellen = unbesetzte Felder).

## 5. Roadmap (Ausbaustufe 3 — noch offen)

| Baustein | Skizze |
|---|---|
| **Monitoring/Alerts** | Gespeicherte Suchen je Projekt (`meta.json`), manuell oder per Cron ausgeführt; Diff gegen letzte Trefferliste → „Neu seit …"-Ansicht; Rechtsstand-Watch über OPS-legal |
| **Familien-Deduplizierung** | Aktenansicht gruppiert Familienmitglieder (Feld `family` vorhanden) — ein Eintrag je Erfindung statt je Publikation |
| **Semantische Fremdsuche** | Kandidaten via OPS/Google holen → lokal embedden (RAG-Engine) → Cosine-Ranking gegen die eigene Erfindungsbeschreibung („Prior-Art-Radar") |
| **Zeichnungen/PDF** | OPS published-data images → Ablage im Projekt, Vision-Modell beschreibt Hauptzeichnung |

## 6. Quellen

- Markt: [PatSnap Plattformvergleich](https://www.patsnap.com/resources/blog/articles/best-patent-search-platforms-2025/) · [Tool-Liste 2025 (Bosankic)](https://medium.com/@leopold.bosankic_32459/patent-analysis-tools-list-2025-ip-analytics-platforms-landscaping-tools-and-more-buyers-470f1a1c1a64) · [PatSnap vs Derwent vs Orbit](https://www.patsnap.com/resources/blog/articles/patsnap-vs-derwent-vs-orbit-patent-visualization-tools-comparison-2025/)
- Daten: [EPO OPS](https://ops.epo.org) · [WIPO Open Source Patent Analytics Manual](https://wipo-analytics.github.io/manual/databases.html) · [PQAI-API-Guide](https://projectpq.ai/best-patent-search-apis-2025/)
- KI-Analyse: [Patlytics AI Patent Search](https://www.patlytics.ai/blog/ai-patent-search) · [Patlytics FTO-Guide](https://www.patlytics.ai/blog/freedom-to-operate-analysis-guide) · [XLSCOUT ClaimChart LLM](https://xlscout.ai/claimchart-llm/) · [Solve Intelligence Prior-Art-Ranking](https://www.solveintelligence.com/blog/post/prior-art-search-ai-tools)
- Embeddings: [PatentSBERTa](https://arxiv.org/pdf/2103.11933) · [BERT for Patents (Google)](https://services.google.com/fh/files/blogs/bert_for_patents_white_paper.pdf) · [Google Patents Public Datasets](https://github.com/google/patents-public-data)
- Grenzen von LLMs: [Hallucinations and Hits (Clemson)](https://open.clemson.edu/jptrca/vol36/iss1/2/) · [PatRe: Office-Action-Benchmark](https://arxiv.org/pdf/2605.03571) · [AutoPatent: Multi-Agent-Framework](https://arxiv.org/pdf/2412.09796)
- Prüfer-Methodik: [epi: Problem-Solution-Approach (Art. 56 EPÜ)](https://information.patentepi.org/1-16/problem-solution-approach-article-56-epc.html) · [ipwiki: Aufgabe-Lösungs-Ansatz](https://www.ipwiki.de/ep:aufgabe-loesungs-ansatz) · [Bardehle: Beurteilung der erfinderischen Tätigkeit](https://media.bardehle.com/contentdocuments/broschures/Beurteilung_der_erfinderischen_Taetigkeit_BARDEHLE_PAGENBERG_IP-Fachbroschuere.pdf) · [TT Consultants: Invalidity Contentions](https://ttconsultants.com/invalidity-contentions-patent-litigation-strategies/) · [DisputeSoft: Claims/Limitations/Infringement](https://www.disputesoft.com/patent-litigation-part-five-an-introduction-to-patent-claims-limitations-infringement-and-invalidity/)
- Scoring: [IAM: Patent Strength Tools](https://www.iam-media.com/article/tools-and-technologies-analysing-patent-strength-in-portfolio) · [Patent Quality Index erklärt](https://beyondelevation.com/blog/posts/patent-quality-index-explained/) · [ScienceDirect: Patent Technological Value](https://www.sciencedirect.com/science/article/abs/pii/S1751157722001237)
- Landscape/White-Space: [PatSeer White-Space](https://patseer.com/white-space-analysis-how-to-identify-gaps-in-patent-landscape/) · [IamIP Landscape-Guide](https://iamip.com/how-to-do-a-patent-landscape-analysis-step-by-step-guide/)
