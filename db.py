"""
AI_Framework_Thomas — SQLite persistence layer (aiosqlite)
Tabellen: conversations, messages (FTS5 für Suche)
"""

import json
import time
from pathlib import Path
from typing import Optional

import aiosqlite

DB_PATH = Path("data/ai_framework_thomas.db")


def set_db_path(path: Path):
    """Wird von main.py aufgerufen um den Datenbankpfad aus config.json zu setzen."""
    global DB_PATH
    DB_PATH = path

_SCHEMA_STMTS = [
    "PRAGMA journal_mode=WAL",
    "PRAGMA foreign_keys=ON",
    """CREATE TABLE IF NOT EXISTS conversations (
        id          TEXT PRIMARY KEY,
        title       TEXT NOT NULL DEFAULT 'Neues Gespräch',
        created_at  REAL NOT NULL,
        updated_at  REAL NOT NULL,
        model       TEXT,
        agent_id    TEXT,
        canvas_json TEXT,
        project_id  TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS messages (
        rowid       INTEGER PRIMARY KEY AUTOINCREMENT,
        conv_id     TEXT    NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
        seq         INTEGER NOT NULL,
        role        TEXT    NOT NULL,
        content     TEXT    NOT NULL DEFAULT '',
        images_json TEXT,
        created_at  REAL    NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS idx_msg_conv ON messages(conv_id, seq)",
    """CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(
        content,
        content='messages',
        content_rowid='rowid',
        tokenize='unicode61'
    )""",
    """CREATE TRIGGER IF NOT EXISTS fts_ai AFTER INSERT ON messages BEGIN
        INSERT INTO messages_fts(rowid, content) VALUES (new.rowid, new.content);
    END""",
    """CREATE TRIGGER IF NOT EXISTS fts_ad AFTER DELETE ON messages BEGIN
        INSERT INTO messages_fts(messages_fts, rowid, content)
        VALUES ('delete', old.rowid, old.content);
    END""",
    # ── RAG: Sammlungen, Dokumente, Chunks (mit Embedding als BLOB) ──────────
    """CREATE TABLE IF NOT EXISTS rag_collections (
        id            TEXT PRIMARY KEY,
        name          TEXT    NOT NULL,
        embed_model   TEXT    NOT NULL,
        tier          TEXT    NOT NULL,
        chunk_size    INTEGER NOT NULL,
        chunk_overlap INTEGER NOT NULL,
        top_k         INTEGER NOT NULL,
        embed_gpu     INTEGER NOT NULL DEFAULT 0,
        clean         INTEGER NOT NULL DEFAULT 1,
        char_limit    INTEGER NOT NULL DEFAULT 3000,
        strictness    TEXT    NOT NULL DEFAULT 'ausgewogen',
        server_path   TEXT,
        created_at    REAL    NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS rag_documents (
        id            TEXT PRIMARY KEY,
        collection_id TEXT    NOT NULL REFERENCES rag_collections(id) ON DELETE CASCADE,
        filename      TEXT    NOT NULL,
        n_chunks      INTEGER NOT NULL DEFAULT 0,
        created_at    REAL    NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS rag_chunks (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        collection_id TEXT    NOT NULL REFERENCES rag_collections(id) ON DELETE CASCADE,
        document_id   TEXT    NOT NULL REFERENCES rag_documents(id) ON DELETE CASCADE,
        seq           INTEGER NOT NULL,
        text          TEXT    NOT NULL,
        embedding     BLOB    NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS idx_ragchunk_coll ON rag_chunks(collection_id)",
    # ── To-Do: Projektbaum, Punkte, Kanten, Anlagen ─────────────────────────
    """CREATE TABLE IF NOT EXISTS todo_projects (
        id                TEXT PRIMARY KEY,
        name              TEXT NOT NULL,
        parent_id         TEXT,                       -- NULL = Wurzel (Benutzer)
        type              TEXT NOT NULL DEFAULT 'projekt',
        title             TEXT NOT NULL DEFAULT '',
        date              TEXT NOT NULL DEFAULT '',
        participants_json TEXT NOT NULL DEFAULT '[]',
        project_ref       TEXT NOT NULL DEFAULT '',    -- Verknüpfung Projekte-Tab
        settings_json     TEXT NOT NULL DEFAULT '{}',  -- u. a. Graph-Positionen
        sort              INTEGER NOT NULL DEFAULT 0,
        created_at        REAL NOT NULL,
        updated_at        REAL NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS idx_todo_proj_parent ON todo_projects(parent_id)",
    """CREATE TABLE IF NOT EXISTS todo_items (
        id             TEXT PRIMARY KEY,
        project_id     TEXT NOT NULL REFERENCES todo_projects(id) ON DELETE CASCADE,
        seq            INTEGER NOT NULL DEFAULT 0,
        text           TEXT NOT NULL DEFAULT '',
        detail         TEXT NOT NULL DEFAULT '',
        status         TEXT NOT NULL DEFAULT 'offen',
        assignees_json TEXT NOT NULL DEFAULT '[]',
        due            TEXT NOT NULL DEFAULT '',
        created_at     REAL NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS idx_todo_item_proj ON todo_items(project_id, seq)",
    """CREATE TABLE IF NOT EXISTS todo_edges (
        project_id TEXT NOT NULL REFERENCES todo_projects(id) ON DELETE CASCADE,
        source     TEXT NOT NULL,
        target     TEXT NOT NULL,
        label      TEXT NOT NULL DEFAULT ''
    )""",
    "CREATE INDEX IF NOT EXISTS idx_todo_edge_proj ON todo_edges(project_id)",
    """CREATE TABLE IF NOT EXISTS todo_attachments (
        id         TEXT PRIMARY KEY,
        item_id    TEXT NOT NULL REFERENCES todo_items(id) ON DELETE CASCADE,
        name       TEXT NOT NULL DEFAULT '',
        orig_path  TEXT NOT NULL DEFAULT '',
        md_text    TEXT NOT NULL DEFAULT '',
        created_at REAL NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS idx_todo_att_item ON todo_attachments(item_id)",
]


async def init():
    """Datenbank anlegen und Schema initialisieren."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    async with aiosqlite.connect(DB_PATH) as db:
        for stmt in _SCHEMA_STMTS:
            await db.execute(stmt)
        # Migration: project_id-Spalte nachrüsten wenn noch nicht vorhanden
        try:
            await db.execute("ALTER TABLE conversations ADD COLUMN project_id TEXT")
        except Exception:
            pass
        # Migration: char_limit für RAG-Sammlungen nachrüsten
        try:
            await db.execute("ALTER TABLE rag_collections ADD COLUMN char_limit INTEGER NOT NULL DEFAULT 3000")
        except Exception:
            pass
        try:
            await db.execute("ALTER TABLE rag_collections ADD COLUMN strictness TEXT NOT NULL DEFAULT 'ausgewogen'")
        except Exception:
            pass
        try:
            await db.execute("ALTER TABLE rag_collections ADD COLUMN server_path TEXT")
        except Exception:
            pass
        try:
            await db.execute(
                "ALTER TABLE rag_collections ADD COLUMN clean_level TEXT NOT NULL DEFAULT 'standard'")
        except Exception:
            pass
        await db.commit()


async def migrate_json(conversations_dir: Path):
    """Bestehende JSON-Dateien einmalig in die DB importieren."""
    if not conversations_dir.exists():
        return
    imported = 0
    for fp in conversations_dir.glob("*.json"):
        try:
            data = json.loads(fp.read_text(encoding="utf-8"))
            conv_id = data.get("id") or fp.stem
            msgs = data.get("messages", [])
            ts = data.get("timestamp", fp.stat().st_mtime)
            # Schon vorhanden?
            async with aiosqlite.connect(DB_PATH) as db:
                cur = await db.execute("SELECT id FROM conversations WHERE id=?", (conv_id,))
                if await cur.fetchone():
                    continue
            await save_conversation(conv_id, msgs, timestamp=ts)
            imported += 1
        except Exception:
            pass
    if imported:
        print(f"[DB] {imported} JSON-Konversation(en) migriert → {DB_PATH}")


async def save_conversation(
    conv_id: str,
    messages: list,
    model: str = None,
    agent_id: str = None,
    canvas_json: str = None,
    timestamp: float = None,
):
    now = timestamp or time.time()
    title = "Neues Gespräch"
    for m in messages:
        if m.get("role") == "user" and str(m.get("content", "")).strip():
            title = str(m["content"]).strip()[:80]
            break

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("PRAGMA foreign_keys=ON")
        await db.execute(
            """
            INSERT INTO conversations(id, title, created_at, updated_at, model, agent_id, canvas_json)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                title      = excluded.title,
                updated_at = excluded.updated_at,
                model      = COALESCE(excluded.model,      model),
                agent_id   = COALESCE(excluded.agent_id,   agent_id),
                canvas_json= COALESCE(excluded.canvas_json, canvas_json)
            """,
            (conv_id, title, now, now, model, agent_id, canvas_json),
        )
        await db.execute("DELETE FROM messages WHERE conv_id=?", (conv_id,))
        for seq, msg in enumerate(messages):
            images = msg.get("images")
            await db.execute(
                "INSERT INTO messages(conv_id,seq,role,content,images_json,created_at) VALUES(?,?,?,?,?,?)",
                (
                    conv_id,
                    seq,
                    msg.get("role", "user"),
                    str(msg.get("content") or ""),
                    json.dumps(images) if images else None,
                    now,
                ),
            )
        await db.commit()


async def update_canvas(conv_id: str, canvas_json: str):
    """Canvas-JSON speichern – legt Konversation an falls noch nicht vorhanden."""
    now = time.time()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            INSERT INTO conversations(id, title, created_at, updated_at, canvas_json)
            VALUES (?, 'Neues Gespräch', ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET canvas_json=excluded.canvas_json, updated_at=excluded.updated_at
            """,
            (conv_id, now, now, canvas_json),
        )
        await db.commit()


async def list_conversations(limit: int = 300, project_id: Optional[str] = None):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        if project_id is not None:
            cur = await db.execute(
                "SELECT id, title, updated_at AS timestamp, project_id FROM conversations WHERE project_id=? ORDER BY updated_at DESC LIMIT ?",
                (project_id, limit),
            )
        else:
            cur = await db.execute(
                "SELECT id, title, updated_at AS timestamp, project_id FROM conversations ORDER BY updated_at DESC LIMIT ?",
                (limit,),
            )
        rows = await cur.fetchall()
        return [dict(r) for r in rows]


async def rename_conversation(conv_id: str, new_title: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE conversations SET title=?, updated_at=? WHERE id=?",
            (new_title, time.time(), conv_id),
        )
        await db.commit()


async def set_project(conv_id: str, project_id: Optional[str]):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE conversations SET project_id=? WHERE id=?",
            (project_id, conv_id),
        )
        await db.commit()


async def get_conversation(conv_id: str) -> Optional[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM conversations WHERE id=?", (conv_id,))
        conv = await cur.fetchone()
        if not conv:
            return None
        cur = await db.execute(
            "SELECT role, content, images_json FROM messages WHERE conv_id=? ORDER BY seq",
            (conv_id,),
        )
        rows = await cur.fetchall()
        msgs = []
        for r in rows:
            m = {"role": r["role"], "content": r["content"]}
            if r["images_json"]:
                m["images"] = json.loads(r["images_json"])
            msgs.append(m)
        return {
            "id": conv["id"],
            "title": conv["title"],
            "timestamp": conv["updated_at"],
            "model": conv["model"],
            "agent_id": conv["agent_id"],
            "canvas_json": conv["canvas_json"],
            "project_id": conv["project_id"],
            "messages": msgs,
        }


async def delete_conversation(conv_id: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("PRAGMA foreign_keys=ON")
        await db.execute("DELETE FROM conversations WHERE id=?", (conv_id,))
        await db.commit()


async def search(query: str, limit: int = 30) -> list:
    """Volltextsuche in Nachrichten (FTS5), ein Ergebnis pro Gespräch."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        try:
            cur = await db.execute(
                """
                SELECT c.id, c.title, c.updated_at AS timestamp,
                       snippet(messages_fts, 0, '<b>', '</b>', '…', 24) AS excerpt
                FROM messages_fts
                JOIN messages m      ON messages_fts.rowid = m.rowid
                JOIN conversations c ON m.conv_id = c.id
                WHERE messages_fts MATCH ?
                ORDER BY rank
                LIMIT ?
                """,
                (query, limit * 5),  # mehr holen, dann in Python deduplizieren
            )
            rows = await cur.fetchall()
            # Ein Ergebnis pro Konversation (erste / beste Trefferzeile)
            seen: set = set()
            result = []
            for r in rows:
                d = dict(r)
                if d["id"] not in seen:
                    seen.add(d["id"])
                    result.append(d)
                if len(result) >= limit:
                    break
            return result
        except Exception as e:
            print(f"[DB] search error: {e}")
            return []


# ── RAG-Persistenz ──────────────────────────────────────────────────────────

async def rag_create_collection(coll: dict):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """INSERT INTO rag_collections
               (id, name, embed_model, tier, chunk_size, chunk_overlap, top_k,
                embed_gpu, clean, char_limit, strictness, server_path, clean_level, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                coll["id"], coll["name"], coll["embed_model"], coll["tier"],
                coll["chunk_size"], coll["chunk_overlap"], coll["top_k"],
                1 if coll.get("embed_gpu") else 0, 1 if coll.get("clean", True) else 0,
                int(coll.get("char_limit", 3000)), coll.get("strictness", "ausgewogen"),
                coll.get("server_path") or None,
                coll.get("clean_level") or "standard",
                coll.get("created_at") or time.time(),
            ),
        )
        await db.commit()


async def rag_set_server_path(cid: str, server_path: Optional[str]):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE rag_collections SET server_path=? WHERE id=?",
            (server_path or None, cid),
        )
        await db.commit()


async def rag_list_collections() -> list:
    """Sammlungen inkl. Dokument- und Chunk-Zähler."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            """SELECT c.*,
                      (SELECT COUNT(*) FROM rag_documents d WHERE d.collection_id=c.id) AS n_docs,
                      (SELECT COUNT(*) FROM rag_chunks k    WHERE k.collection_id=c.id) AS n_chunks
               FROM rag_collections c ORDER BY c.created_at DESC"""
        )
        return [dict(r) for r in await cur.fetchall()]


async def rag_get_collection(cid: str) -> Optional[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM rag_collections WHERE id=?", (cid,))
        r = await cur.fetchone()
        return dict(r) if r else None


async def rag_delete_collection(cid: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("PRAGMA foreign_keys=ON")
        await db.execute("DELETE FROM rag_collections WHERE id=?", (cid,))
        await db.commit()


async def rag_add_document(doc_id: str, collection_id: str, filename: str,
                           chunks: list, embeddings: list):
    """Legt ein Dokument an und speichert seine Chunks + Embeddings (BLOB)."""
    now = time.time()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("PRAGMA foreign_keys=ON")
        await db.execute(
            "INSERT INTO rag_documents(id, collection_id, filename, n_chunks, created_at) VALUES(?,?,?,?,?)",
            (doc_id, collection_id, filename, len(chunks), now),
        )
        for seq, (text, emb) in enumerate(zip(chunks, embeddings)):
            await db.execute(
                "INSERT INTO rag_chunks(collection_id, document_id, seq, text, embedding) VALUES(?,?,?,?,?)",
                (collection_id, doc_id, seq, text, emb),
            )
        await db.commit()


async def rag_list_documents(cid: str) -> list:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT id, filename, n_chunks, created_at FROM rag_documents WHERE collection_id=? ORDER BY created_at DESC",
            (cid,),
        )
        return [dict(r) for r in await cur.fetchall()]


async def rag_document_chunks(did: str) -> Optional[dict]:
    """Dateiname + Chunk-Texte eines Dokuments (in seq-Reihenfolge) — für den
    Export des Dokumentinhalts als Markdown/TXT."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT filename FROM rag_documents WHERE id=?", (did,))
        row = await cur.fetchone()
        if not row:
            return None
        cur = await db.execute(
            "SELECT text FROM rag_chunks WHERE document_id=? ORDER BY seq", (did,))
        return {"filename": row["filename"], "chunks": [r["text"] for r in await cur.fetchall()]}


async def rag_delete_document(did: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("PRAGMA foreign_keys=ON")
        await db.execute("DELETE FROM rag_documents WHERE id=?", (did,))
        await db.commit()


async def rag_fetch_chunks(collection_ids: list) -> list:
    """Alle Chunks (mit Embedding-BLOB + Quelldatei) der gegebenen Sammlungen."""
    if not collection_ids:
        return []
    ph = ",".join("?" for _ in collection_ids)
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            f"""SELECT k.id, k.collection_id, k.text, k.embedding, d.filename, c.name AS collection_name
                FROM rag_chunks k
                JOIN rag_documents d   ON k.document_id = d.id
                JOIN rag_collections c ON k.collection_id = c.id
                WHERE k.collection_id IN ({ph})""",
            tuple(collection_ids),
        )
        return [dict(r) for r in await cur.fetchall()]


# ── RAG: vollständiger Export / Import (für Backup & Restore) ────────────────

async def rag_export() -> list:
    """Vollständiger Dump aller RAG-Sammlungen inkl. Dokumente, Chunks und
    Embeddings (als rohe Bytes – der Aufrufer base64-kodiert sie fürs ZIP)."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM rag_collections ORDER BY created_at")
        colls = [dict(r) for r in await cur.fetchall()]
        out = []
        for c in colls:
            cur = await db.execute(
                "SELECT id, filename, n_chunks, created_at FROM rag_documents "
                "WHERE collection_id=? ORDER BY created_at",
                (c["id"],),
            )
            docs = [dict(r) for r in await cur.fetchall()]
            for d in docs:
                cur = await db.execute(
                    "SELECT seq, text, embedding FROM rag_chunks WHERE document_id=? ORDER BY seq",
                    (d["id"],),
                )
                d["chunks"] = [
                    {"seq": r["seq"], "text": r["text"], "embedding": bytes(r["embedding"])}
                    for r in await cur.fetchall()
                ]
            out.append({"collection": c, "documents": docs})
        return out


async def rag_collection_exists(cid: str) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT 1 FROM rag_collections WHERE id=?", (cid,))
        return await cur.fetchone() is not None


async def rag_export_collection(cid: str) -> Optional[dict]:
    """Exportiert eine einzelne RAG-Sammlung inkl. Dokumente, Chunks und
    Embeddings als rohe Bytes (Aufrufer base64-kodiert für JSON-Serialisierung)."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM rag_collections WHERE id=?", (cid,))
        c = await cur.fetchone()
        if not c:
            return None
        c = dict(c)
        cur = await db.execute(
            "SELECT id, filename, n_chunks, created_at FROM rag_documents "
            "WHERE collection_id=? ORDER BY created_at",
            (cid,),
        )
        docs = [dict(r) for r in await cur.fetchall()]
        for d in docs:
            cur = await db.execute(
                "SELECT seq, text, embedding FROM rag_chunks WHERE document_id=? ORDER BY seq",
                (d["id"],),
            )
            d["chunks"] = [
                {"seq": r["seq"], "text": r["text"], "embedding": bytes(r["embedding"])}
                for r in await cur.fetchall()
            ]
        return {"collection": c, "documents": docs}


async def rag_import_collection(coll: dict, documents: list):
    """Stellt eine Sammlung inkl. Dokumente/Chunks/Embeddings wieder her
    (Backup-Restore oder Klon). Embeddings werden als rohe Bytes erwartet. Der Aufrufer
    stellt sicher, dass die Sammlung noch nicht existiert."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("PRAGMA foreign_keys=ON")
        await db.execute(
            """INSERT INTO rag_collections
               (id, name, embed_model, tier, chunk_size, chunk_overlap, top_k,
                embed_gpu, clean, char_limit, strictness, server_path, clean_level, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                coll["id"], coll["name"], coll["embed_model"], coll["tier"],
                coll["chunk_size"], coll["chunk_overlap"], coll["top_k"],
                1 if coll.get("embed_gpu") else 0, 1 if coll.get("clean", True) else 0,
                int(coll.get("char_limit", 3000)), coll.get("strictness", "ausgewogen"),
                coll.get("server_path") or None,
                coll.get("clean_level") or "standard",
                coll.get("created_at") or time.time(),
            ),
        )
        for d in documents:
            chunks = d.get("chunks", [])
            await db.execute(
                "INSERT INTO rag_documents(id, collection_id, filename, n_chunks, created_at) "
                "VALUES(?,?,?,?,?)",
                (d["id"], coll["id"], d["filename"],
                 d.get("n_chunks", len(chunks)), d.get("created_at") or time.time()),
            )
            for ch in chunks:
                await db.execute(
                    "INSERT INTO rag_chunks(collection_id, document_id, seq, text, embedding) "
                    "VALUES(?,?,?,?,?)",
                    (coll["id"], d["id"], ch["seq"], ch["text"], ch["embedding"]),
                )
        await db.commit()


# ══════════════════════════════════════════════════════════════════════════════
# To-Do: Projektbaum, Punkte, Kanten, Anlagen
# ══════════════════════════════════════════════════════════════════════════════

def _row_project(r) -> dict:
    d = dict(r)
    d["participants"] = json.loads(d.pop("participants_json", "[]") or "[]")
    d["settings"] = json.loads(d.pop("settings_json", "{}") or "{}")
    return d


def _row_item(r) -> dict:
    d = dict(r)
    d["assignees"] = json.loads(d.pop("assignees_json", "[]") or "[]")
    return d


async def todo_root_ensure(name: str) -> dict:
    """Wurzelprojekt (id 'root', parent NULL) sicherstellen; Name aktualisieren."""
    now = time.time()
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM todo_projects WHERE id='root'")
        row = await cur.fetchone()
        if row is None:
            await db.execute(
                "INSERT INTO todo_projects(id,name,parent_id,type,title,date,"
                "participants_json,project_ref,settings_json,sort,created_at,updated_at) "
                "VALUES('root',?,NULL,'projekt','','','[]','','{}',0,?,?)",
                (name or "Meine To-Dos", now, now),
            )
        else:
            await db.execute("UPDATE todo_projects SET name=?, updated_at=? WHERE id='root'",
                             (name or row["name"] or "Meine To-Dos", now))
        await db.commit()
        cur = await db.execute("SELECT * FROM todo_projects WHERE id='root'")
        return _row_project(await cur.fetchone())


async def todo_project_create(proj: dict) -> dict:
    now = time.time()
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT COALESCE(MAX(sort),0)+1 FROM todo_projects WHERE COALESCE(parent_id,'')=?",
            (proj.get("parent_id") or "",))
        nxt = (await cur.fetchone())[0]
        await db.execute(
            "INSERT INTO todo_projects(id,name,parent_id,type,title,date,"
            "participants_json,project_ref,settings_json,sort,created_at,updated_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (proj["id"], proj["name"], proj.get("parent_id") or None,
             proj.get("type", "projekt"), proj.get("title", ""), proj.get("date", ""),
             json.dumps(proj.get("participants") or [], ensure_ascii=False),
             proj.get("project_ref", ""),
             json.dumps(proj.get("settings") or {}, ensure_ascii=False),
             int(proj.get("sort", nxt)), now, now),
        )
        await db.commit()
        cur = await db.execute("SELECT * FROM todo_projects WHERE id=?", (proj["id"],))
        return _row_project(await cur.fetchone())


async def todo_projects_all() -> list:
    """Alle Projekte (flach) mit Punkt-/Erledigt-Zaehlern - fuer den Baumaufbau."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT p.*, "
            "(SELECT COUNT(*) FROM todo_items i WHERE i.project_id=p.id) AS n_items, "
            "(SELECT COUNT(*) FROM todo_items i WHERE i.project_id=p.id AND i.status='erledigt') AS n_done "
            "FROM todo_projects p ORDER BY p.parent_id IS NOT NULL, p.sort, p.created_at")
        return [_row_project(r) for r in await cur.fetchall()]


async def _item_attachments(db, item_id: str) -> list:
    cur = await db.execute(
        "SELECT id,name,orig_path,created_at FROM todo_attachments WHERE item_id=? ORDER BY created_at",
        (item_id,))
    return [dict(r) for r in await cur.fetchall()]


async def todo_project_get(pid: str):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM todo_projects WHERE id=?", (pid,))
        row = await cur.fetchone()
        if not row:
            return None
        proj = _row_project(row)
        cur = await db.execute("SELECT * FROM todo_items WHERE project_id=? ORDER BY seq, created_at", (pid,))
        items = [_row_item(r) for r in await cur.fetchall()]
        for it in items:
            it["attachments"] = await _item_attachments(db, it["id"])
        proj["items"] = items
        cur = await db.execute("SELECT source,target,label FROM todo_edges WHERE project_id=?", (pid,))
        proj["edges"] = [dict(r) for r in await cur.fetchall()]
        return proj


async def todo_save_project(pid: str, header: dict, items: list, edges: list):
    """Header aktualisieren + Punkte per Diff-Upsert (Anlagen bleiben erhalten) +
    Kanten ersetzen. Punkte werden NICHT geloescht/neu angelegt, damit die per
    FK verknuepften Anlagen nicht kaskadierend verloren gehen."""
    now = time.time()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("PRAGMA foreign_keys=ON")
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT id FROM todo_projects WHERE id=?", (pid,))
        if not await cur.fetchone():
            return None
        await db.execute(
            "UPDATE todo_projects SET type=?,title=?,date=?,participants_json=?,"
            "project_ref=?,settings_json=?,updated_at=? WHERE id=?",
            (header.get("type", "projekt"), header.get("title", ""), header.get("date", ""),
             json.dumps(header.get("participants") or [], ensure_ascii=False),
             header.get("project_ref", ""),
             json.dumps(header.get("settings") or {}, ensure_ascii=False), now, pid))
        cur = await db.execute("SELECT id FROM todo_items WHERE project_id=?", (pid,))
        existing = {r[0] for r in await cur.fetchall()}
        incoming = []
        for seq, it in enumerate(items or []):
            iid = it.get("id")
            if not iid:
                continue
            incoming.append(iid)
            vals = (seq, it.get("text", ""), it.get("detail", ""), it.get("status", "offen"),
                    json.dumps(it.get("assignees") or [], ensure_ascii=False), it.get("due", ""))
            if iid in existing:
                await db.execute(
                    "UPDATE todo_items SET seq=?,text=?,detail=?,status=?,assignees_json=?,due=? WHERE id=? AND project_id=?",
                    (*vals, iid, pid))
            else:
                await db.execute(
                    "INSERT INTO todo_items(id,project_id,seq,text,detail,status,assignees_json,due,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
                    (iid, pid, *vals, now))
        for iid in existing - set(incoming):
            await db.execute("DELETE FROM todo_items WHERE id=? AND project_id=?", (iid, pid))
        await db.execute("DELETE FROM todo_edges WHERE project_id=?", (pid,))
        for e in edges or []:
            if e.get("source") and e.get("target"):
                await db.execute("INSERT INTO todo_edges(project_id,source,target,label) VALUES(?,?,?,?)",
                                 (pid, e["source"], e["target"], e.get("label", "")))
        await db.commit()
    return await todo_project_get(pid)


async def todo_project_move(pid: str, new_parent):
    if pid == "root" or pid == new_parent:
        return
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE todo_projects SET parent_id=?, updated_at=? WHERE id=?",
                         (new_parent or None, time.time(), pid))
        await db.commit()


async def todo_project_rename(pid: str, name: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE todo_projects SET name=?, updated_at=? WHERE id=?",
                         (name, time.time(), pid))
        await db.commit()


async def todo_project_delete(pid: str, reparent: bool = False):
    """Projekt loeschen. reparent=True zieht Kinder eine Ebene hoch, sonst
    kaskadiert das Loeschen ueber parent_id auf alle Nachfahren."""
    if pid == "root":
        return
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("PRAGMA foreign_keys=ON")
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT parent_id FROM todo_projects WHERE id=?", (pid,))
        row = await cur.fetchone()
        parent = row["parent_id"] if row else None
        if reparent:
            await db.execute("UPDATE todo_projects SET parent_id=? WHERE parent_id=?", (parent, pid))
        else:
            ids = []
            frontier = [pid]
            while frontier:
                nxt = []
                for f in frontier:
                    cur = await db.execute("SELECT id FROM todo_projects WHERE parent_id=?", (f,))
                    for r in await cur.fetchall():
                        ids.append(r["id"]); nxt.append(r["id"])
                frontier = nxt
            for i in ids:
                await db.execute("DELETE FROM todo_projects WHERE id=?", (i,))
        await db.execute("DELETE FROM todo_projects WHERE id=?", (pid,))
        await db.commit()


async def todo_descendants(root_id: str) -> list:
    """root_id + alle Nachfahren (IDs). Fuer Scope von Suche/Graph."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        ids = [root_id]; frontier = [root_id]
        while frontier:
            nxt = []
            for f in frontier:
                cur = await db.execute("SELECT id FROM todo_projects WHERE parent_id=?", (f,))
                for r in await cur.fetchall():
                    ids.append(r["id"]); nxt.append(r["id"])
            frontier = nxt
        return ids


async def todo_item_add(pid: str, item: dict) -> dict:
    now = time.time()
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT COALESCE(MAX(seq),-1)+1 FROM todo_items WHERE project_id=?", (pid,))
        seq = (await cur.fetchone())[0]
        await db.execute(
            "INSERT INTO todo_items(id,project_id,seq,text,detail,status,assignees_json,due,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
            (item["id"], pid, seq, item.get("text", ""), item.get("detail", ""),
             item.get("status", "offen"), json.dumps(item.get("assignees") or [], ensure_ascii=False),
             item.get("due", ""), now))
        await db.commit()
    return item


async def todo_item_move(item_id: str, new_pid: str):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT COALESCE(MAX(seq),-1)+1 FROM todo_items WHERE project_id=?", (new_pid,))
        seq = (await cur.fetchone())[0]
        await db.execute("UPDATE todo_items SET project_id=?, seq=? WHERE id=?", (new_pid, seq, item_id))
        await db.execute("DELETE FROM todo_edges WHERE source=? OR target=?", (item_id, item_id))
        await db.commit()


async def todo_item_reorder(item_id: str, direction: str):
    """Punkt eins hoch/runter tauschen (innerhalb seines Projekts)."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT project_id, seq FROM todo_items WHERE id=?", (item_id,))
        row = await cur.fetchone()
        if not row:
            return
        pid, seq = row["project_id"], row["seq"]
        op = "<" if direction == "up" else ">"
        order = "DESC" if direction == "up" else "ASC"
        cur = await db.execute(
            "SELECT id, seq FROM todo_items WHERE project_id=? AND seq " + op + " ? ORDER BY seq " + order + " LIMIT 1",
            (pid, seq))
        nb = await cur.fetchone()
        if not nb:
            return
        await db.execute("UPDATE todo_items SET seq=? WHERE id=?", (nb["seq"], item_id))
        await db.execute("UPDATE todo_items SET seq=? WHERE id=?", (seq, nb["id"]))
        await db.commit()


async def todo_attach_add(att_id: str, item_id: str, name: str, orig_path: str, md_text: str) -> dict:
    now = time.time()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("PRAGMA foreign_keys=ON")
        await db.execute(
            "INSERT INTO todo_attachments(id,item_id,name,orig_path,md_text,created_at) VALUES(?,?,?,?,?,?)",
            (att_id, item_id, name, orig_path, md_text, now))
        await db.commit()
    return {"id": att_id, "item_id": item_id, "name": name, "orig_path": orig_path, "created_at": now}


async def todo_attach_get(att_id: str):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM todo_attachments WHERE id=?", (att_id,))
        r = await cur.fetchone()
        return dict(r) if r else None


async def todo_attach_delete(att_id: str):
    att = await todo_attach_get(att_id)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM todo_attachments WHERE id=?", (att_id,))
        await db.commit()
    return att


async def todo_search(term: str, project_ids=None) -> list:
    """Punkte + Anlagen-MD durchsuchen (optional auf project_ids beschraenkt)."""
    term = (term or "").strip().lower()
    if not term:
        return []
    like = "%" + term + "%"
    scope = ""
    params = [like, like, like]
    if project_ids is not None:
        ph = ",".join("?" for _ in project_ids)
        scope = " AND i.project_id IN (" + ph + ")"
        params += list(project_ids)
    results = []
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT i.id, i.project_id, i.text, i.status, i.assignees_json, p.name AS project_name, "
            "p.title AS project_title FROM todo_items i JOIN todo_projects p ON i.project_id=p.id "
            "WHERE (LOWER(i.text) LIKE ? OR LOWER(i.detail) LIKE ? OR LOWER(i.assignees_json) LIKE ?)" + scope,
            tuple(params))
        seen = set()
        for r in await cur.fetchall():
            seen.add(r["id"])
            results.append({"project": r["project_id"], "project_title": r["project_title"] or r["project_name"],
                            "item_id": r["id"], "text": r["text"], "status": r["status"],
                            "assignees": json.loads(r["assignees_json"] or "[]"), "source": "text",
                            "attachment": None})
        params2 = [like]
        scope2 = ""
        if project_ids is not None:
            ph = ",".join("?" for _ in project_ids)
            scope2 = " AND i.project_id IN (" + ph + ")"
            params2 += list(project_ids)
        cur = await db.execute(
            "SELECT a.name AS att_name, a.md_text, i.id AS item_id, i.project_id, i.text, i.status, "
            "p.name AS project_name, p.title AS project_title FROM todo_attachments a "
            "JOIN todo_items i ON a.item_id=i.id JOIN todo_projects p ON i.project_id=p.id "
            "WHERE LOWER(a.md_text) LIKE ?" + scope2,
            tuple(params2))
        for r in await cur.fetchall():
            if r["item_id"] in seen:
                continue
            content = r["md_text"] or ""
            idx = content.lower().find(term)
            snip = content[max(0, idx - 60):idx + 120].replace("\n", " ").strip() if idx >= 0 else ""
            results.append({"project": r["project_id"], "project_title": r["project_title"] or r["project_name"],
                            "item_id": r["item_id"], "text": r["text"], "status": r["status"], "assignees": [],
                            "source": "attachment", "attachment": {"name": r["att_name"], "snippet": snip}})
    return results[:200]


async def todo_graph_data(project_ids: list) -> dict:
    """Punkte + Kanten der gegebenen Projekte (fuer den projektuebergreifenden Graph)."""
    if not project_ids:
        return {"projects": []}
    out = []
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        for pid in project_ids:
            cur = await db.execute("SELECT name FROM todo_projects WHERE id=?", (pid,))
            pr = await cur.fetchone()
            if not pr:
                continue
            cur = await db.execute("SELECT * FROM todo_items WHERE project_id=? ORDER BY seq", (pid,))
            items = [_row_item(r) for r in await cur.fetchall()]
            cur = await db.execute("SELECT source,target,label FROM todo_edges WHERE project_id=?", (pid,))
            edges = [dict(r) for r in await cur.fetchall()]
            out.append({"name": pid, "title": pr["name"], "items": items, "edges": edges})
    return {"projects": out}


async def todo_export() -> dict:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM todo_projects")
        projects = [dict(r) for r in await cur.fetchall()]
        cur = await db.execute("SELECT * FROM todo_items")
        items = [dict(r) for r in await cur.fetchall()]
        cur = await db.execute("SELECT * FROM todo_edges")
        edges = [dict(r) for r in await cur.fetchall()]
        cur = await db.execute("SELECT * FROM todo_attachments")
        atts = [dict(r) for r in await cur.fetchall()]
    return {"projects": projects, "items": items, "edges": edges, "attachments": atts}


async def todo_import(dump: dict):
    if not dump:
        return
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("PRAGMA foreign_keys=OFF")
        for p in dump.get("projects", []):
            await db.execute(
                "INSERT OR REPLACE INTO todo_projects(id,name,parent_id,type,title,date,"
                "participants_json,project_ref,settings_json,sort,created_at,updated_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (p["id"], p.get("name", ""), p.get("parent_id"), p.get("type", "projekt"),
                 p.get("title", ""), p.get("date", ""), p.get("participants_json", "[]"),
                 p.get("project_ref", ""), p.get("settings_json", "{}"), int(p.get("sort", 0)),
                 p.get("created_at") or time.time(), p.get("updated_at") or time.time()))
        for it in dump.get("items", []):
            await db.execute(
                "INSERT OR REPLACE INTO todo_items(id,project_id,seq,text,detail,status,assignees_json,due,created_at) "
                "VALUES(?,?,?,?,?,?,?,?,?)",
                (it["id"], it["project_id"], int(it.get("seq", 0)), it.get("text", ""), it.get("detail", ""),
                 it.get("status", "offen"), it.get("assignees_json", "[]"), it.get("due", ""),
                 it.get("created_at") or time.time()))
        for e in dump.get("edges", []):
            await db.execute("INSERT INTO todo_edges(project_id,source,target,label) VALUES(?,?,?,?)",
                             (e["project_id"], e["source"], e["target"], e.get("label", "")))
        for a in dump.get("attachments", []):
            await db.execute(
                "INSERT OR REPLACE INTO todo_attachments(id,item_id,name,orig_path,md_text,created_at) "
                "VALUES(?,?,?,?,?,?)",
                (a["id"], a["item_id"], a.get("name", ""), a.get("orig_path", ""), a.get("md_text", ""),
                 a.get("created_at") or time.time()))
        await db.commit()


async def migrate_todo_json(todo_dir: Path, root_name: str):
    """Bestehende data/todo/<name>/list.json einmalig in die DB uebernehmen.
    Jede Altliste wird Kind der Wurzel; Anlagen-MD werden als md_text gespeichert."""
    marker = todo_dir / ".db_migrated"
    if marker.exists() or not todo_dir.exists():
        await todo_root_ensure(root_name)
        return
    import uuid as _uuid
    await todo_root_ensure(root_name)
    imported = 0
    for d in sorted(todo_dir.iterdir()):
        if not d.is_dir() or d.name.startswith("_") or d.name.startswith("."):
            continue
        p = d / "list.json"
        if not p.exists():
            continue
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        pid = "tp_" + _uuid.uuid4().hex[:12]
        header = {"type": data.get("type", "projekt"), "title": data.get("title", ""),
                  "date": data.get("date", ""), "participants": data.get("participants") or [],
                  "project_ref": data.get("project_id", ""),
                  "settings": {"positions": data.get("positions") or {}}}
        await todo_project_create({"id": pid, "name": data.get("title") or d.name,
                                   "parent_id": "root", **header})
        items = data.get("items") or []
        await todo_save_project(pid, header, items, data.get("edges") or [])
        for it in items:
            for a in (it.get("attachments") or []):
                md_fp = d / "attachments" / it.get("id", "") / (a.get("md") or "")
                md_text = ""
                if md_fp.exists():
                    try:
                        md_text = md_fp.read_text(encoding="utf-8", errors="replace")
                    except Exception:
                        md_text = ""
                await todo_attach_add("ta_" + _uuid.uuid4().hex[:12], it["id"],
                                      a.get("name") or a.get("md", ""), str(md_fp), md_text)
        imported += 1
    marker.write_text("ok", encoding="utf-8")
    if imported:
        print("[DB] " + str(imported) + " To-Do-Projekt(e) aus JSON migriert -> " + str(DB_PATH))
