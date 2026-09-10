"""
SQLite-backed job queue for offline-first operation.

Guarantees
──────────
- Idempotency: submitting the same logical job twice (same idempotency_key) is a no-op.
  The existing job's ID is returned unchanged.
- Durability: jobs survive process restarts (written to SQLite before returning).
- At-most-once execution: a job is atomically claimed (status → RUNNING) before
  execution, so two workers cannot pick the same job even if they race.
- Back-off: failed jobs are re-queued with exponential delay (min 60s → max 30min).

Job types
─────────
  "pipeline"              : run full D1→D5 for one or all products
  "import_transactions"   : load a CSV of transactions into the local data store
  "import_promotions"     : load a CSV of promotions
  "import_events"         : load a CSV of local events
  "sync_push"             : push a specific cached doc to Firestore (internal)
"""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from .db import connect

log = logging.getLogger(__name__)

# Delay schedule: after attempt N the job waits _BACKOFF_SECONDS[min(N, last)] seconds
_BACKOFF_SECONDS = [0, 60, 300, 900, 1800]  # 0s, 1min, 5min, 15min, 30min


# ── Submit ────────────────────────────────────────────────────────────────────

def enqueue(
    job_type: str,
    payload: Dict[str, Any],
    idempotency_key: Optional[str] = None,
    max_attempts: int = 3,
    run_after: Optional[datetime] = None,
) -> str:
    """
    Submit a job. If a job with the same idempotency_key already exists and is
    not FAILED/DONE, returns its existing job_id (no duplicate created).

    Returns the job_id.
    """
    idem = idempotency_key or _default_idem_key(job_type, payload)
    now = _now_iso()
    ra_iso = run_after.strftime("%Y-%m-%dT%H:%M:%S.%fZ") if run_after else now
    job_id = str(uuid.uuid4())

    conn = connect()
    try:
        conn.execute("BEGIN IMMEDIATE")

        # Check for existing active job with same key
        existing = conn.execute(
            "SELECT job_id, status FROM job_queue WHERE idempotency_key=?",
            (idem,),
        ).fetchone()

        if existing and existing["status"] not in ("DONE", "FAILED"):
            conn.execute("ROLLBACK")
            log.debug("queue.enqueue DEDUP %s → %s", idem, existing["job_id"])
            return existing["job_id"]

        conn.execute(
            """INSERT OR REPLACE INTO job_queue
               (job_id, idempotency_key, job_type, payload, status, attempts, max_attempts,
                created_at, updated_at, run_after)
               VALUES (?, ?, ?, ?, 'PENDING', 0, ?, ?, ?, ?)""",
            (job_id, idem, job_type, json.dumps(payload), max_attempts, now, now, ra_iso),
        )
        conn.execute("COMMIT")
        log.info("queue.enqueue %s [%s] key=%s", job_type, job_id[:8], idem)
        return job_id

    except Exception:
        conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()


# ── Claim & complete ──────────────────────────────────────────────────────────

def claim_next(job_type: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """
    Atomically claim the oldest eligible PENDING job.
    Returns the job dict or None if the queue is empty / no job is ready.
    """
    conn = connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        now_iso = _now_iso()
        type_filter = "AND job_type=?" if job_type else ""
        params = [now_iso]
        if job_type:
            params.append(job_type)
        params = tuple(params)

        row = conn.execute(
            f"""SELECT * FROM job_queue
                WHERE status='PENDING'
                  AND (run_after IS NULL OR run_after <= ?)
                  {type_filter}
                ORDER BY created_at ASC
                LIMIT 1""",
            params,
        ).fetchone()

        if not row:
            conn.execute("ROLLBACK")
            return None

        conn.execute(
            "UPDATE job_queue SET status='RUNNING', attempts=attempts+1, updated_at=? WHERE job_id=?",
            (now_iso, row["job_id"]),
        )
        conn.execute("COMMIT")
        job = dict(row)
        job["payload"] = json.loads(job["payload"])
        # Row was fetched before UPDATE; patch the fields that changed
        job["status"] = "RUNNING"
        job["attempts"] = job["attempts"] + 1
        return job

    except Exception:
        conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()


def mark_done(job_id: str) -> None:
    """Mark a job as successfully completed."""
    _update_status(job_id, "DONE", error=None, run_after=None)
    log.info("queue.done %s", job_id[:8])


def mark_failed(job_id: str, error: str, retry: bool = True) -> None:
    """
    Mark a job as failed. If retry=True and attempts < max_attempts, re-queue
    with an exponential back-off delay.
    """
    conn = connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT attempts, max_attempts FROM job_queue WHERE job_id=?",
            (job_id,),
        ).fetchone()

        if row and retry and row["attempts"] < row["max_attempts"]:
            delay = _BACKOFF_SECONDS[min(row["attempts"], len(_BACKOFF_SECONDS) - 1)]
            run_after = (datetime.now(timezone.utc) + timedelta(seconds=delay)).strftime(
                "%Y-%m-%dT%H:%M:%S.%fZ"
            )
            conn.execute(
                """UPDATE job_queue
                   SET status='PENDING', error=?, updated_at=?, run_after=?
                   WHERE job_id=?""",
                (error[:2000], _now_iso(), run_after, job_id),
            )
            log.warning("queue.retry %s in %ds — %s", job_id[:8], delay, error[:120])
        else:
            conn.execute(
                "UPDATE job_queue SET status='FAILED', error=?, updated_at=? WHERE job_id=?",
                (error[:2000], _now_iso(), job_id),
            )
            log.error("queue.failed %s (exhausted) — %s", job_id[:8], error[:120])

        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()


# ── Queries ───────────────────────────────────────────────────────────────────

def list_jobs(status: Optional[str] = None, limit: int = 50) -> List[Dict[str, Any]]:
    conn = connect()
    try:
        if status:
            rows = conn.execute(
                "SELECT * FROM job_queue WHERE status=? ORDER BY created_at DESC LIMIT ?",
                (status, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM job_queue ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [_row_to_dict(r) for r in rows]
    finally:
        conn.close()


def pending_count() -> int:
    conn = connect()
    try:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM job_queue WHERE status IN ('PENDING','RUNNING')"
        ).fetchone()
        return row["n"]
    finally:
        conn.close()


def clear_done(older_than_hours: int = 24) -> int:
    """Prune DONE/FAILED jobs older than the specified age. Returns count deleted."""
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=older_than_hours)).strftime(
        "%Y-%m-%dT%H:%M:%S.%fZ"
    )
    conn = connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        cur = conn.execute(
            "DELETE FROM job_queue WHERE status IN ('DONE','FAILED') AND updated_at < ?",
            (cutoff,),
        )
        conn.execute("COMMIT")
        return cur.rowcount
    except Exception:
        try:
            conn.execute("ROLLBACK")
        except Exception:
            pass
        raise
    finally:
        conn.close()


# ── Helpers ───────────────────────────────────────────────────────────────────

def _update_status(
    job_id: str,
    status: str,
    error: Optional[str],
    run_after: Optional[str],
) -> None:
    conn = connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            "UPDATE job_queue SET status=?, error=?, updated_at=?, run_after=? WHERE job_id=?",
            (status, error, _now_iso(), run_after, job_id),
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


def _row_to_dict(row: Any) -> Dict[str, Any]:
    d = dict(row)
    try:
        d["payload"] = json.loads(d["payload"])
    except Exception:
        pass
    return d


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _default_idem_key(job_type: str, payload: Dict[str, Any]) -> str:
    """
    Deterministic idempotency key from job type + stable payload fields.
    Pipeline jobs are keyed by (product_id, horizon, date) so the same pipeline
    requested twice on the same day is deduplicated.
    """
    import hashlib, datetime as dt

    today = dt.date.today().isoformat()
    pid = payload.get("product_id", "ALL")
    horizon = payload.get("horizon", "7")
    raw = f"{job_type}:{pid}:{horizon}:{today}"
    return hashlib.sha256(raw.encode()).hexdigest()[:32]
