"""
Background sync manager — pushes locally-cached documents to Firestore
when connectivity is restored, and pulls the latest remote versions on
reconnect to keep the local store current.

Design
──────
- Runs as a daemon thread; the main process can call SyncManager.start() /
  stop() without blocking.
- Push: iterates unsynced local_cache rows (oldest first) and writes each
  to Firestore.  Marks them synced only on HTTP 200.
- Pull: on transition OFFLINE→ONLINE, fetches the latest forecast documents
  from Firestore and upserts them into the local cache so stale entries are
  refreshed even if the local pipeline hasn't run yet.
- Conflict strategy (last-writer-wins on version): if Firestore has a higher
  version than local, the remote wins; if local is newer, local is pushed.
- Duplicate prevention: push uses mark_synced(version=...) — only the exact
  version written locally is marked synced; a concurrent local update will
  increment the version and trigger another push on the next cycle.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Any, Dict, Optional, TYPE_CHECKING

from . import cache as local_cache
from . import connectivity

if TYPE_CHECKING:
    pass

log = logging.getLogger(__name__)

# ── Configuration ─────────────────────────────────────────────────────────────

# How often the sync loop wakes up (seconds)
POLL_INTERVAL_SECONDS = 30

# How many unsynced docs to push per cycle (avoid large bursts)
PUSH_BATCH_SIZE = 20

# Collections to pull from Firestore on reconnect
_PULL_COLLECTIONS = ["forecasts", "quality_reports", "demand_signals"]


# ── SyncManager ───────────────────────────────────────────────────────────────

class SyncManager:
    """
    Thread-safe background sync manager.

    Usage:
        mgr = SyncManager(firestore_client)
        mgr.start()
        # ... application runs ...
        mgr.stop()
    """

    def __init__(self, firestore_client: Optional[Any] = None):
        """
        Parameters
        ----------
        firestore_client : google.cloud.firestore.Client or None
            Pass None to run in local-only mode (no Firestore pushes).
        """
        self._fs = firestore_client
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._prev_status = connectivity.ConnectivityStatus.UNKNOWN

        # Stats
        self._pushed: int = 0
        self._pulled: int = 0
        self._errors: int = 0

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def start(self) -> None:
        """Start the background sync thread."""
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._loop,
            name="demand-sync",
            daemon=True,
        )
        self._thread.start()
        log.info("sync.start — poll interval %ds", POLL_INTERVAL_SECONDS)

    def stop(self, timeout: float = 5.0) -> None:
        """Signal the sync thread to stop and wait for it to finish."""
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=timeout)
        log.info("sync.stop — pushed=%d pulled=%d errors=%d",
                 self._pushed, self._pulled, self._errors)

    def flush_now(self) -> Dict[str, int]:
        """
        Synchronously push all unsynced docs and return counts.
        Useful for the /queue/flush API endpoint.
        """
        if not self._fs:
            return {"pushed": 0, "errors": 0, "message": "no_firestore_client"}

        status = connectivity.get_status(force=True)
        if status != connectivity.ConnectivityStatus.ONLINE:
            return {"pushed": 0, "errors": 0, "message": "offline"}

        pushed, errors = self._push_unsynced(limit=500)
        return {"pushed": pushed, "errors": errors}

    def stats(self) -> Dict[str, Any]:
        return {
            "pushed_total": self._pushed,
            "pulled_total": self._pulled,
            "errors_total": self._errors,
            "unsynced_local": local_cache.count_unsynced(),
            "connectivity": connectivity.diagnostics(),
        }

    # ── Internal loop ─────────────────────────────────────────────────────────

    def _loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                self._cycle()
            except Exception as exc:
                self._errors += 1
                log.error("sync.cycle error: %s", exc, exc_info=True)

            self._stop_event.wait(timeout=POLL_INTERVAL_SECONDS)

    def _cycle(self) -> None:
        status = connectivity.get_status()

        # Detect OFFLINE→ONLINE transition
        just_reconnected = (
            self._prev_status == connectivity.ConnectivityStatus.OFFLINE
            and status == connectivity.ConnectivityStatus.ONLINE
        )
        self._prev_status = status

        if status != connectivity.ConnectivityStatus.ONLINE:
            log.debug("sync.cycle OFFLINE — skipping")
            return

        if just_reconnected:
            log.info("sync.cycle reconnected — pulling remote state")
            self._pull_remote()

        pushed, errors = self._push_unsynced(limit=PUSH_BATCH_SIZE)
        self._pushed += pushed
        self._errors += errors

        if pushed or errors:
            log.info("sync.cycle push pushed=%d errors=%d remaining=%d",
                     pushed, errors, local_cache.count_unsynced())

    # ── Push ──────────────────────────────────────────────────────────────────

    def _push_unsynced(self, limit: int = PUSH_BATCH_SIZE):
        if not self._fs:
            return 0, 0

        rows = local_cache.get_unsynced(limit=limit)
        pushed = 0
        errors = 0

        for collection, doc_id, version, data in rows:
            try:
                self._push_one(collection, doc_id, version, data)
                pushed += 1
            except Exception as exc:
                errors += 1
                log.warning("sync.push %s/%s v%d failed: %s",
                            collection, doc_id, version, exc)

        return pushed, errors

    def _push_one(
        self,
        collection: str,
        doc_id: str,
        version: int,
        data: Dict[str, Any],
    ) -> None:
        """Write one document to Firestore and mark it synced locally."""
        doc_ref = self._fs.collection(collection).document(doc_id)

        # Last-writer-wins: check remote version to avoid overwriting newer data
        remote_snap = doc_ref.get()
        if remote_snap.exists:
            remote_version = remote_snap.to_dict().get("_local_version", 0)
            if remote_version > version:
                # Remote is newer — mark local as synced without overwriting
                log.debug("sync.push %s/%s remote v%d > local v%d — skipping push",
                          collection, doc_id, remote_version, version)
                local_cache.mark_synced(collection, doc_id, version)
                return

        # Attach version metadata so future conflict checks work
        payload = {**data, "_local_version": version}
        doc_ref.set(payload)
        local_cache.mark_synced(collection, doc_id, version)
        log.debug("sync.push %s/%s v%d → Firestore OK", collection, doc_id, version)

    # ── Pull ──────────────────────────────────────────────────────────────────

    def _pull_remote(self) -> None:
        """Fetch fresh documents from Firestore and upsert into local cache."""
        if not self._fs:
            return

        pulled = 0
        for collection in _PULL_COLLECTIONS:
            try:
                pulled += self._pull_collection(collection)
            except Exception as exc:
                log.warning("sync.pull collection=%s failed: %s", collection, exc)

        self._pulled += pulled
        if pulled:
            log.info("sync.pull pulled=%d docs from Firestore", pulled)

    def _pull_collection(self, collection: str) -> int:
        docs = self._fs.collection(collection).stream()
        count = 0
        for doc in docs:
            data = doc.to_dict()
            remote_version = data.pop("_local_version", 0)

            # Only overwrite local if remote is newer
            existing = local_cache.get_with_meta(collection, doc.id)
            local_version = existing.get("_cache_version", 0) if existing else 0

            if remote_version >= local_version:
                local_cache.put(collection, doc.id, data)
                # Mark immediately as synced (we just pulled it)
                new_version = local_cache.get_with_meta(collection, doc.id)
                if new_version:
                    local_cache.mark_synced(collection, doc.id,
                                            new_version["_cache_version"])
                count += 1

        return count


# ── Module-level singleton ────────────────────────────────────────────────────

_manager: Optional[SyncManager] = None


def get_manager() -> Optional[SyncManager]:
    return _manager


def init_sync(firestore_client: Optional[Any] = None) -> SyncManager:
    """
    Initialise (or replace) the module-level SyncManager and start it.
    Call once at application startup from the FastAPI lifespan handler.
    """
    global _manager
    if _manager is not None:
        _manager.stop()

    _manager = SyncManager(firestore_client)
    _manager.start()
    return _manager


def shutdown_sync() -> None:
    """Gracefully stop the background sync thread."""
    global _manager
    if _manager is not None:
        _manager.stop()
        _manager = None
