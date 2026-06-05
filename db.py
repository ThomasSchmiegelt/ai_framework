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
                embed_gpu, clean, char_limit, strictness, server_path, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                coll["id"], coll["name"], coll["embed_model"], coll["tier"],
                coll["chunk_size"], coll["chunk_overlap"], coll["top_k"],
                1 if coll.get("embed_gpu") else 0, 1 if coll.get("clean", True) else 0,
                int(coll.get("char_limit", 3000)), coll.get("strictness", "ausgewogen"),
                coll.get("server_path") or None,
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
                embed_gpu, clean, char_limit, strictness, server_path, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                coll["id"], coll["name"], coll["embed_model"], coll["tier"],
                coll["chunk_size"], coll["chunk_overlap"], coll["top_k"],
                1 if coll.get("embed_gpu") else 0, 1 if coll.get("clean", True) else 0,
                int(coll.get("char_limit", 3000)), coll.get("strictness", "ausgewogen"),
                coll.get("server_path") or None,
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
