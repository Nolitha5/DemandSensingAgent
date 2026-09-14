"""
Shared Output Repository — 25-Agent Firebase Architecture.

Writes every AgentOutput to two Firestore collections:
  - agent_outputs  : append-only history (doc_id = output_id)
  - agent_state    : latest per (agent_id + entity_id) — upserted

Also writes:
  - system_events  : cross-domain routing events (append-only)
  - agent_runs     : pipeline run tracking

Offline-first: all writes go to local SQLite first.
Firestore is best-effort; the SyncManager handles push when online.

Key guarantee: output_id is deterministic, so offline retries are idempotent —
the same operation produces the same output_id and the same Firestore document.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from app.contracts.shared_output import AgentOutput, SystemEvent
from app.local import cache as local_cache
from app.local.connectivity import is_online

logger = logging.getLogger(__name__)

# Shared Firestore collection names
COLLECTION_AGENT_OUTPUTS = "agent_outputs"
COLLECTION_AGENT_STATE = "agent_state"
COLLECTION_SYSTEM_EVENTS = "system_events"
COLLECTION_AGENT_RUNS = "agent_runs"


def _firestore():
    try:
        from app.repositories.firestore_client import get_firestore_client
        return get_firestore_client()
    except Exception as exc:
        logger.debug("Firestore unavailable: %s", exc)
        return None


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _write_local_and_remote(collection: str, doc_id: str, data: dict, db=None) -> None:
    """
    Core offline-first write:
    1. Always persist to local SQLite.
    2. If Firestore is available, also write there and mark synced.
    """
    local_cache.put(collection, doc_id, data)

    if db is None and is_online():
        db = _firestore()

    if db:
        try:
            db.collection(collection).document(doc_id).set(data)
            meta = local_cache.get_with_meta(collection, doc_id)
            if meta:
                local_cache.mark_synced(collection, doc_id, meta["_cache_version"])
        except Exception as exc:
            logger.warning(
                "Firestore write %s/%s deferred (will sync later): %s",
                collection, doc_id, exc,
            )


class OutputRepository:
    """
    Writes AgentOutputs to the shared Firestore collections.

    Usage:
        repo = OutputRepository()
        output = make_agent_output(...)
        repo.publish(output)
        repo.emit_event(make_system_event(...))
    """

    def __init__(self, db=None):
        self._db = db

    def _get_db(self):
        if self._db is None and is_online():
            self._db = _firestore()
        return self._db

    # ── Publish agent output ──────────────────────────────────────────────────

    def publish(self, output: AgentOutput) -> str:
        """
        Write output to:
          - agent_outputs/{output_id}  (append-only; one doc per output)
          - agent_state/{agent_id}_{entity_id} (upserted; latest per agent+entity)

        Returns the output_id.
        """
        db = self._get_db()
        data = output.model_dump(mode="json")

        # 1. Append-only history — keyed by output_id (never overwritten)
        _write_local_and_remote(COLLECTION_AGENT_OUTPUTS, output.output_id, data, db)

        # 2. Latest state per agent+entity — upserted
        state_doc_id = f"{output.agent_id}_{output.entity_id}"
        _write_local_and_remote(COLLECTION_AGENT_STATE, state_doc_id, data, db)

        logger.info(
            "[OutputRepo] Published %s output_id=%s agent=%s entity=%s",
            output.output_type, output.output_id[:12], output.agent_id, output.entity_id,
        )
        return output.output_id

    # ── Read latest state ─────────────────────────────────────────────────────

    def get_latest_state(self, agent_id: str, entity_id: str) -> Optional[AgentOutput]:
        """Read the latest published output for a given agent + entity."""
        state_doc_id = f"{agent_id}_{entity_id}"
        db = self._get_db()

        raw = None
        if db:
            try:
                snap = db.collection(COLLECTION_AGENT_STATE).document(state_doc_id).get()
                if snap.exists:
                    raw = snap.to_dict()
            except Exception as exc:
                logger.warning("Firestore read agent_state/%s failed: %s", state_doc_id, exc)

        if raw is None:
            raw = local_cache.get(COLLECTION_AGENT_STATE, state_doc_id)

        if raw is None:
            return None
        try:
            return AgentOutput(**raw)
        except Exception as exc:
            logger.warning("Could not parse AgentOutput %s: %s", state_doc_id, exc)
            return None

    # ── System events ─────────────────────────────────────────────────────────

    def emit_event(self, event: SystemEvent) -> str:
        """Write a system event to system_events collection (append-only)."""
        db = self._get_db()
        data = event.model_dump(mode="json")
        _write_local_and_remote(COLLECTION_SYSTEM_EVENTS, event.event_id, data, db)
        logger.info(
            "[OutputRepo] Emitted event %s for %s/%s",
            event.event_type, event.entity_type, event.entity_id,
        )
        return event.event_id

    # ── Agent run tracking ────────────────────────────────────────────────────

    def start_run(
        self,
        run_id: str,
        agent_id: str,
        entity_id: str,
        input_refs: Optional[list] = None,
        schema_version: str = "1.0",
        model_or_rule_version: str = "1.0",
    ) -> str:
        """Record a run start in agent_runs."""
        db = self._get_db()
        doc_id = f"{run_id}_{agent_id}"
        data = {
            "run_id": run_id,
            "agent_id": agent_id,
            "entity_id": entity_id,
            "status": "RUNNING",
            "input_refs": input_refs or [],
            "schema_version": schema_version,
            "model_or_rule_version": model_or_rule_version,
            "started_at": _now_iso(),
            "finished_at": None,
            "error": None,
        }
        _write_local_and_remote(COLLECTION_AGENT_RUNS, doc_id, data, db)
        return doc_id

    def complete_run(
        self,
        run_id: str,
        agent_id: str,
        output_id: str,
        status: str = "COMPLETED",
        error: Optional[str] = None,
    ) -> None:
        """Update an agent_run record with completion info."""
        db = self._get_db()
        doc_id = f"{run_id}_{agent_id}"
        raw = local_cache.get(COLLECTION_AGENT_RUNS, doc_id) or {}
        raw.update({
            "status": status,
            "output_id": output_id,
            "finished_at": _now_iso(),
            "error": error,
        })
        _write_local_and_remote(COLLECTION_AGENT_RUNS, doc_id, raw, db)
