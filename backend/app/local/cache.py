"""
Local cache operations — read/write Pydantic models to SQLite.

Rules
─────
- Writes are always local-first (instant, never blocked by network).
- Every write marks synced=0 so the sync manager knows to push it.
- Reads return the local copy; callers should treat it as potentially stale
  if `synced=0` and the age exceeds their staleness threshold.
- Version numbers increment on every update; optimistic-lock helpers let the
  sync manager detect concurrent writes.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from .db import connect

log = logging.getLogger(__name__)

# How old a cached entry can be before we flag it as STALE (seconds)
STALE_THRESHOLD_SECONDS = 3600  # 1 hour


# ── Write ─────────────────────────────────────────────────────────────────────

def put(collection: str, doc_id: str, data: Dict[str, Any]) -> int:
    """
    Upsert a JSON document into the local cache.
    Returns the new version number.
    Marks the record as unsynced (synced=0).
    """
    conn = connect()
    try:
        now = _now_iso()
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT version FROM local_cache WHERE collection=? AND doc_id=?",
            (collection, doc_id),
        ).fetchone()
        if row:
            new_version = row["version"] + 1
            conn.execute(
                """UPDATE local_cache
                   SET data=?, version=?, updated_at=?, synced=0, sync_at=NULL
                   WHERE collection=? AND doc_id=?""",
                (json.dumps(data), new_version, now, collection, doc_id),
            )
        else:
            new_version = 1
            conn.execute(
                """INSERT INTO local_cache (collection, doc_id, data, version, created_at, updated_at, synced)
                   VALUES (?, ?, ?, 1, ?, ?, 0)""",
                (collection, doc_id, json.dumps(data), now, now),
            )
        conn.execute("COMMIT")
        log.debug("cache.put %s/%s v%d", collection, doc_id, new_version)
        return new_version
    except Exception:
        conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()


def mark_synced(collection: str, doc_id: str, version: int) -> None:
    """Mark a specific version as synced to Firestore. Ignores if version has moved on."""
    conn = connect()
    try:
        now = _now_iso()
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            """UPDATE local_cache SET synced=1, sync_at=?
               WHERE collection=? AND doc_id=? AND version=?""",
            (now, collection, doc_id, version),
        )
        conn.execute(
            "INSERT INTO sync_log (collection, doc_id, synced_at) VALUES (?, ?, ?)",
            (collection, doc_id, now),
        )
        conn.execute("COMMIT")
    except Exception:
        try:
            conn.execute("ROLLBACK")
        except Exception:
            pass
        raise
    finally:
        conn.close()


# ── Read ──────────────────────────────────────────────────────────────────────

def get(collection: str, doc_id: str) -> Optional[Dict[str, Any]]:
    """Return the cached document or None if not found."""
    conn = connect()
    try:
        row = conn.execute(
            "SELECT data FROM local_cache WHERE collection=? AND doc_id=?",
            (collection, doc_id),
        ).fetchone()
        return json.loads(row["data"]) if row else None
    finally:
        conn.close()


def get_with_meta(collection: str, doc_id: str) -> Optional[Dict[str, Any]]:
    """Return the cached document including cache metadata (version, synced, updated_at)."""
    conn = connect()
    try:
        row = conn.execute(
            """SELECT data, version, synced, sync_at, updated_at, created_at
               FROM local_cache WHERE collection=? AND doc_id=?""",
            (collection, doc_id),
        ).fetchone()
        if not row:
            return None
        d = json.loads(row["data"])
        d["_cache_version"] = row["version"]
        d["_cache_synced"] = bool(row["synced"])
        d["_cache_updated_at"] = row["updated_at"]
        d["_cache_stale"] = _is_stale(row["updated_at"])
        return d
    finally:
        conn.close()


def list_collection(collection: str) -> List[Dict[str, Any]]:
    """Return all documents in a collection."""
    conn = connect()
    try:
        rows = conn.execute(
            "SELECT doc_id, data FROM local_cache WHERE collection=? ORDER BY updated_at DESC",
            (collection,),
        ).fetchall()
        return [{"_doc_id": r["doc_id"], **json.loads(r["data"])} for r in rows]
    finally:
        conn.close()


def get_unsynced(limit: int = 100) -> List[Tuple[str, str, int, Dict[str, Any]]]:
    """
    Return up to `limit` unsynced (collection, doc_id, version, data) tuples,
    ordered oldest-first so we sync in write order.
    """
    conn = connect()
    try:
        rows = conn.execute(
            """SELECT collection, doc_id, version, data
               FROM local_cache WHERE synced=0
               ORDER BY updated_at ASC LIMIT ?""",
            (limit,),
        ).fetchall()
        return [(r["collection"], r["doc_id"], r["version"], json.loads(r["data"])) for r in rows]
    finally:
        conn.close()


def delete(collection: str, doc_id: str) -> None:
    """Remove a document from the local cache."""
    conn = connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            "DELETE FROM local_cache WHERE collection=? AND doc_id=?",
            (collection, doc_id),
        )
        conn.execute("COMMIT")
    except Exception:
        try:
            conn.execute("ROLLBACK")
        except Exception:
            pass
        raise
    finally:
        conn.close()


def count_unsynced() -> int:
    conn = connect()
    try:
        row = conn.execute("SELECT COUNT(*) AS n FROM local_cache WHERE synced=0").fetchone()
        return row["n"]
    finally:
        conn.close()


# ── Helpers ───────────────────────────────────────────────────────────────────

def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _is_stale(updated_at_iso: str) -> bool:
    try:
        dt = datetime.fromisoformat(updated_at_iso.replace("Z", "+00:00"))
        age = (datetime.now(timezone.utc) - dt).total_seconds()
        return age > STALE_THRESHOLD_SECONDS
    except Exception:
        return False
