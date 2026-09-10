"""
SQLite-backed local storage for offline-first operation.

Schema
──────
local_cache     : stores serialised agent outputs keyed by (collection, doc_id)
job_queue       : pending import/pipeline jobs; deduplicated via idempotency_key
sync_log        : record of every successful push to Firestore

Thread safety: all writes use WAL mode + per-connection BEGIN IMMEDIATE.
"""
from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Iterator, Optional

_lock = threading.Lock()
_LOCAL_DB_PATH = Path(__file__).parent.parent.parent / "local_data" / "demand_local.db"


def _default_db_path() -> Path:
    return _LOCAL_DB_PATH


def init_db(db_path: Optional[Path] = None) -> Path:
    """Create the SQLite database and tables if they don't exist. Returns the resolved path."""
    path = db_path or _default_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    with _connect(path) as conn:
        conn.executescript("""
            PRAGMA journal_mode=WAL;
            PRAGMA foreign_keys=ON;

            -- ── Local cache ──────────────────────────────────────────────────
            CREATE TABLE IF NOT EXISTS local_cache (
                collection      TEXT NOT NULL,
                doc_id          TEXT NOT NULL,
                data            TEXT NOT NULL,          -- JSON blob
                version         INTEGER NOT NULL DEFAULT 1,
                created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
                updated_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
                synced          INTEGER NOT NULL DEFAULT 0,  -- 0=pending sync, 1=synced
                sync_at         TEXT,                        -- timestamp of last successful sync
                PRIMARY KEY (collection, doc_id)
            );

            -- ── Job queue ─────────────────────────────────────────────────────
            CREATE TABLE IF NOT EXISTS job_queue (
                job_id          TEXT PRIMARY KEY,
                idempotency_key TEXT NOT NULL UNIQUE,   -- prevents duplicate jobs
                job_type        TEXT NOT NULL,           -- "pipeline", "import_transactions", …
                payload         TEXT NOT NULL,           -- JSON: {product_id, horizon, …}
                status          TEXT NOT NULL DEFAULT 'PENDING',  -- PENDING/RUNNING/DONE/FAILED
                attempts        INTEGER NOT NULL DEFAULT 0,
                max_attempts    INTEGER NOT NULL DEFAULT 3,
                error           TEXT,
                created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
                updated_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
                run_after       TEXT                     -- ISO timestamp: earliest execution time
            );
            CREATE INDEX IF NOT EXISTS idx_job_status   ON job_queue (status, run_after);
            CREATE INDEX IF NOT EXISTS idx_job_idem     ON job_queue (idempotency_key);

            -- ── Sync log ─────────────────────────────────────────────────────
            CREATE TABLE IF NOT EXISTS sync_log (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                collection      TEXT NOT NULL,
                doc_id          TEXT NOT NULL,
                synced_at       TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
                direction       TEXT NOT NULL DEFAULT 'push'   -- 'push' or 'pull'
            );
            CREATE INDEX IF NOT EXISTS idx_sync_col ON sync_log (collection, doc_id);
        """)
    return path


@contextmanager
def _connect(db_path: Optional[Path] = None) -> Iterator[sqlite3.Connection]:
    path = db_path or _default_db_path()
    conn = sqlite3.connect(str(path), timeout=10, isolation_level=None)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


# ── Shared module-level resolved path ────────────────────────────────────────
_resolved_path: Optional[Path] = None


def get_db_path() -> Path:
    global _resolved_path
    if _resolved_path is None:
        _resolved_path = init_db()
    return _resolved_path


def connect() -> sqlite3.Connection:
    """Return a new connection to the local SQLite database."""
    path = get_db_path()
    conn = sqlite3.connect(str(path), timeout=10, isolation_level=None)
    conn.row_factory = sqlite3.Row
    return conn
