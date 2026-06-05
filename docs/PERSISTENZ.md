# AI_Framework_Thomas — Persistenz im Detail (Datenbank & Dateispeicher)

> Vertiefung zu Abschnitt 7 der [Technischen Beschreibung](TECHNISCHE_BESCHREIBUNG.md).
> Bezieht sich auf `db.py` und die Persistenz-Stellen in `main.py`.

AI_Framework_Thomas hat bewusst **zwei getrennte Speicherebenen**: eine **SQLite-Datenbank** für
Gespräche (das Herzstück, in `db.py`) und **flache JSON-Dateien** für alles andere
(Agenten, Pläne, Profil, Projekte, Code).

---

## 1. SQLite-Datenbank (`db.py`) — die zentrale Persistenz

### 1.1 Technische Grundlage

- **Treiber:** `aiosqlite` — asynchrone Wrapper um SQLite, passend zum durchgängig async
  aufgebauten Backend.
- **Speicherort:** `data/ai_framework_thomas.db` (Konstante `DB_PATH`).
- **Verbindungsmodell:** Es gibt **keine** dauerhaft offene Verbindung. Jede Operation
  öffnet ihre eigene Verbindung per `async with aiosqlite.connect(DB_PATH) as db:` und
  schließt sie wieder. Das ist einfach und robust (keine geteilte Verbindung über
  Coroutinen hinweg), erkauft sich aber pro Aufruf einen leichten Verbindungs-Overhead.
- **Pragmas** (in `_SCHEMA_STMTS` bzw. pro Operation gesetzt):
  - `PRAGMA journal_mode=WAL` — **Write-Ahead-Logging**. Leser blockieren Schreiber nicht
    und umgekehrt; das erlaubt gleichzeitiges Lesen während eines Schreibvorgangs
    (wichtig im Servermodus mit mehreren Nutzern).
  - `PRAGMA foreign_keys=ON` — Fremdschlüssel werden erzwungen. SQLite hat das
    **standardmäßig aus**, deshalb wird es bei den relevanten Operationen
    (`save_conversation`, `delete_conversation`) explizit erneut gesetzt, da es pro
    Verbindung gilt.

### 1.2 Das Schema

**Tabelle `conversations`** — ein Datensatz pro Gespräch:

| Spalte | Typ | Bedeutung |
|---|---|---|
| `id` | TEXT PRIMARY KEY | Eindeutige Gesprächs-ID (vom Frontend vergeben) |
| `title` | TEXT | Anzeigetitel, Default `'Neues Gespräch'` |
| `created_at` | REAL | Unix-Zeitstempel der Erstellung |
| `updated_at` | REAL | Unix-Zeitstempel der letzten Änderung |
| `model` | TEXT | zuletzt verwendetes Modell |
| `agent_id` | TEXT | optional zugeordneter Agent |
| `canvas_json` | TEXT | serialisierte Canvas-Daten (Folien/Tabelle), falls vorhanden |
| `project_id` | TEXT | optionale Projektzuordnung |

**Tabelle `messages`** — eine Zeile pro Nachricht:

| Spalte | Typ | Bedeutung |
|---|---|---|
| `rowid` | INTEGER PK AUTOINCREMENT | interne Zeilen-ID (zugleich FTS-Kopplung) |
| `conv_id` | TEXT, FK → `conversations(id)` **ON DELETE CASCADE** | Zugehörigkeit |
| `seq` | INTEGER | Reihenfolge innerhalb des Gesprächs |
| `role` | TEXT | `user` / `assistant` / `system` / `tool` |
| `content` | TEXT | Nachrichtentext |
| `images_json` | TEXT | optional: JSON-Array Base64-Bilder |
| `created_at` | REAL | Zeitstempel |

- **Index** `idx_msg_conv ON messages(conv_id, seq)` — beschleunigt das geordnete Laden
  aller Nachrichten eines Gesprächs.
- Das **CASCADE** sorgt dafür, dass beim Löschen eines Gesprächs alle zugehörigen
  Nachrichten automatisch mitgelöscht werden (vorausgesetzt `foreign_keys=ON` ist gesetzt
  — was `delete_conversation` tut).

### 1.3 Volltextsuche mit FTS5 (`messages_fts`)

```sql
CREATE VIRTUAL TABLE messages_fts USING fts5(
    content,
    content='messages',        -- "external content"-Tabelle
    content_rowid='rowid',
    tokenize='unicode61'
);
```

Hier liegt der raffinierteste Teil:

- **`content='messages'`** macht `messages_fts` zu einer *external-content*-FTS-Tabelle.
  Der eigentliche Text wird **nicht doppelt** gespeichert; die FTS-Tabelle hält nur den
  invertierten Index und verweist über `content_rowid='rowid'` zurück auf die
  `messages`-Tabelle. Das spart Speicher.
- **`tokenize='unicode61'`** — Unicode-fähige Tokenisierung, wichtig für deutsche Texte
  (Umlaute etc.).
- **Synchronisierung über Trigger** — da external-content-Tabellen sich nicht selbst
  aktualisieren, halten zwei Trigger den Index synchron:
  - `fts_ai` (AFTER INSERT): fügt den neuen Text in den Index ein.
  - `fts_ad` (AFTER DELETE): schreibt einen speziellen `'delete'`-Eintrag in
    `messages_fts`, der den Indexeintrag wieder entfernt.

  > Hinweis: Es gibt **keinen UPDATE-Trigger**. Das ist unkritisch, weil
  > `save_conversation` Nachrichten nie aktualisiert, sondern immer löscht und neu
  > einfügt (siehe unten) — dabei greifen DELETE- und INSERT-Trigger korrekt.

### 1.4 Die Funktionen im Einzelnen

**`init()`** — beim App-Start (`@app.on_event("startup")`):
- legt das `data/`-Verzeichnis an,
- führt alle `_SCHEMA_STMTS` aus (alle mit `IF NOT EXISTS`, also idempotent),
- rüstet die Spalte `project_id` per `ALTER TABLE … ADD COLUMN` nach. Das ist in ein
  `try/except` gehüllt: existiert die Spalte bereits (zweiter Start), wirft SQLite einen
  Fehler, der bewusst verschluckt wird. Das ist die **Migrationsstrategie** des Projekts
  — kein Migrationsframework, sondern idempotente Einzel-ALTERs.

**`migrate_json(conversations_dir)`** — Einmal-Migration der Altdaten:
- liest alle `*.json` aus `data/conversations/`,
- prüft pro Datei, ob die `id` schon in der DB existiert (dann überspringen),
- importiert ansonsten über `save_conversation`,
- robust gegen kaputte Dateien (jede Datei in eigenem `try/except`).

**`save_conversation(conv_id, messages, …)`** — das zentrale Schreiben:
1. **Titel-Ableitung:** durchläuft die Nachrichten und nimmt die erste nicht-leere
   `user`-Nachricht, gekürzt auf 80 Zeichen, als Titel (Fallback `'Neues Gespräch'`).
2. **UPSERT auf `conversations`:** `INSERT … ON CONFLICT(id) DO UPDATE`. Beim Update werden
   `title` und `updated_at` immer überschrieben; `model`, `agent_id` und `canvas_json`
   aber per `COALESCE(excluded.x, x)` — d. h. ein `NULL`-Parameter **überschreibt einen
   bestehenden Wert nicht**. So bleibt z. B. ein einmal gespeichertes Canvas erhalten,
   auch wenn ein späterer Aufruf es nicht mitliefert.
3. **Nachrichten: Delete-and-Reinsert.** Es werden zuerst **alle** Nachrichten des
   Gesprächs gelöscht (`DELETE FROM messages WHERE conv_id=?`), dann alle aktuell
   übergebenen neu eingefügt — mit fortlaufendem `seq` aus `enumerate`. Bilder werden als
   JSON serialisiert in `images_json`. Diese Strategie hält den Code einfach (kein
   Diffing), und die FTS-Trigger bleiben dabei automatisch konsistent.

**`update_canvas(conv_id, canvas_json)`** — speichert nur das Canvas-JSON, ebenfalls per
UPSERT. Wird während der Chat-Schleife sofort aufgerufen, wenn ein Präsentations-/
Tabellen-Tool ausgeführt wird, damit das Canvas auch dann persistiert ist, wenn das Modell
danach noch weiter Text schreibt. Legt die Konversation bei Bedarf an.

**`list_conversations(limit=300, project_id=None)`** — liefert die Gesprächsliste
(`id`, `title`, `timestamp`, `project_id`), absteigend nach `updated_at`. Optional nach
Projekt gefiltert. Nutzt `aiosqlite.Row` als Row-Factory, um Dicts zurückzugeben.

**`rename_conversation` / `set_project`** — einfache `UPDATE`s; `rename` aktualisiert
zusätzlich `updated_at`.

**`get_conversation(conv_id)`** — lädt ein vollständiges Gespräch: erst den
`conversations`-Datensatz, dann alle Nachrichten geordnet nach `seq`. `images_json` wird
beim Laden wieder zu einer Python-Liste deserialisiert. Gibt `None` zurück, wenn das
Gespräch nicht existiert.

**`delete_conversation(conv_id)`** — setzt `foreign_keys=ON` und löscht den
Gesprächs-Datensatz; die Nachrichten verschwinden per CASCADE, die FTS-Einträge per
DELETE-Trigger.

**`search(query, limit=30)`** — Volltextsuche:
- joint `messages_fts` ↔ `messages` ↔ `conversations`,
- nutzt `messages_fts MATCH ?` und `ORDER BY rank` (FTS5-Relevanz),
- erzeugt mit `snippet(messages_fts, 0, '<b>', '</b>', '…', 24)` einen hervorgehobenen
  Textauszug,
- holt absichtlich das **5-fache** des Limits und **dedupliziert in Python auf ein
  Ergebnis pro Gespräch** (nur der beste Treffer je Konversation), bis das eigentliche
  Limit erreicht ist,
- ist komplett in `try/except` gekapselt und gibt im Fehlerfall eine leere Liste zurück
  (z. B. bei FTS-Syntaxfehlern in der Query).

### 1.5 Wie das Backend die DB nutzt

- **Chat** (`_chat_generator`): Nach einer abgeschlossenen Antwort wird das vollständige
  Gespräch via `save_conversation` gespeichert (inkl. Canvas-JSON). Bei Tool-erzeugten
  Canvas-Objekten zusätzlich sofort `update_canvas`.
- **Verdichten** (`POST /api/conversations/{cid}/compress`): lädt das Gespräch, lässt es
  vom Modell zusammenfassen, ersetzt es durch eine `system`-Zusammenfassung **plus die
  letzten zwei Austausche** und speichert das verkürzte Gespräch zurück per
  `save_conversation`. So bleibt ein langes Gespräch im Kontextfenster handhabbar.
- **Suche** (`GET /api/search`): direkt `db.search`.

---

## 2. Dateibasierte Persistenz (JSON im Dateisystem)

Alles, was **kein** Gespräch ist, wird als einzelne JSON-Datei gespeichert — nicht in
SQLite:

| Objekt | Ort | Auffinden |
|---|---|---|
| Agenten | `data/agents/*.json` | per **ID im Dateiinhalt** (`_agent_path_by_id`), unabhängig vom Dateinamen; Dateiname ist ein Slug (`_to_slug`, mit Umlaut-Ersatz, Kollisions-Suffix) |
| Pläne (Netzplan) | `data/plans/*.json` | per ID |
| Code-Programme (IDE) | `data/code/*.json` | per ID (`_code_path_by_id`) |
| Profil | `data/user_profile.json` (Einzeldatei) | `_load_profile` — enthält u. a. die vier Modell-Rollen (`model_general/coding/science/medical`) und `hidden_tabs` (ausgeblendete optionale Tabs; beim Erstaufruf alle sechs) |
| Projekte | `data/projects.json` (Einzelliste) | `_load_projects` |

Begründung dieser Trennung: Diese Objekte sind wenige, werden einzeln bearbeitet und
profitieren nicht von relationaler Abfrage oder Volltextsuche. Als Klartext-JSON sind sie
leicht inspizierbar, versionierbar und manuell editierbar. Gespräche dagegen sind viele,
wachsen unbegrenzt und brauchen Suche und geordnetes Laden — dafür ist SQLite ideal.

---

## 3. Backup & Restore (`/api/backup`, `/api/restore`)

Diese beiden Endpunkte **vereinen beide Speicherebenen** in einem ZIP:

**Backup** (`GET /api/backup`) baut im Speicher ein ZIP zusammen:
- `profile.json`, `projects.json` (direkt),
- `conversations/<slug>_<id8>.json` — jedes Gespräch wird aus der DB gelesen
  (`get_conversation`) und als JSON exportiert,
- `plans/*.json`, `agents/*.json`, `code/*.json` (Dateien direkt kopiert),
- Ergebnis als Streaming-Download `ai_framework_thomas_backup_<datum>.zip`.

**Restore** (`POST /api/restore`) liest das ZIP und führt zusammen — mit durchdachter
Kollisionsbehandlung:
- **Profil:** überschreibt.
- **Projekte:** *merge* — nur Projekte mit neuer ID werden ergänzt.
- **Gespräche:** werden mit **neuer ID** (`restore_<hex>`) eingefügt (nie überschrieben),
  inkl. Projektzuordnung.
- **Pläne:** übersprungen, wenn ein Plan gleichen Namens existiert; sonst neue ID.
- **Agenten / Code:** übersprungen, wenn die ID bereits existiert.
- Liefert eine `stats`-Bilanz mit Zählern und einer `errors`-Liste; jede Datei in eigenem
  `try/except`, ungültiges ZIP → HTTP 400.

---

## 4. Designcharakteristik (Zusammenfassung)

- **Hybrid:** relationales SQLite (WAL, FTS5) für viele/wachsende Gespräche; flache
  JSON-Dateien für wenige, manuell pflegbare Objekte.
- **Async durchgängig** über `aiosqlite`, Verbindung pro Operation.
- **Idempotentes Schema** mit `IF NOT EXISTS` + try/except-`ALTER` als leichtgewichtige
  Migration.
- **Delete-and-Reinsert** statt Diffing beim Speichern von Nachrichten — einfach und
  trigger-konsistent.
- **External-Content-FTS5** vermeidet doppelte Textspeicherung; Trigger halten den Index
  synchron.
- **Defensive Fehlerbehandlung** überall, wo Nutzerdaten/Fremddateien gelesen werden.
