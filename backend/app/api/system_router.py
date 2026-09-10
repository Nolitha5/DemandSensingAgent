"""
System & Offline Status Router
================================
Endpoints for monitoring connectivity, the job queue, and sync state.
These are consumed by the React frontend connectivity banner.

  GET  /system/status           — overall offline/online health snapshot
  GET  /system/queue            — list queued/running jobs
  POST /system/queue/flush      — push all unsynced docs to Firestore now
  POST /system/queue/enqueue    — manually enqueue a pipeline job
  DELETE /system/queue/{job_id} — cancel a PENDING job (marks as FAILED)
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from app.local import cache as local_cache
from app.local import connectivity
from app.local import queue as local_queue
from app.local import sync as sync_module

logger = logging.getLogger(__name__)
router = APIRouter()


# ── Response models ────────────────────────────────────────────────────────────

class OfflineStatusResponse(BaseModel):
    connectivity: str           # "online" | "offline" | "unknown"
    unsynced_docs: int
    queued_jobs: int
    sync_stats: Dict[str, Any]
    connectivity_details: Dict[str, Any]


class JobSummary(BaseModel):
    job_id: str
    job_type: str
    status: str
    attempts: int
    max_attempts: int
    created_at: str
    run_after: Optional[str] = None
    error: Optional[str] = None


class EnqueueRequest(BaseModel):
    job_type: str = "pipeline"
    product_id: Optional[str] = None
    horizon: int = 7


# ── Endpoints ──────────────────────────────────────────────────────────────────

@router.get("/status", response_model=OfflineStatusResponse, summary="Offline/online status")
def system_status():
    """
    Returns the current connectivity status, number of unsynced local
    documents, and queued job count. Used by the React connectivity banner.
    """
    status = connectivity.get_status()
    manager = sync_module.get_manager()

    return OfflineStatusResponse(
        connectivity=status.value.lower(),
        unsynced_docs=local_cache.count_unsynced(),
        queued_jobs=local_queue.pending_count(),
        sync_stats=manager.stats() if manager else {},
        connectivity_details=connectivity.diagnostics(),
    )


@router.get("/queue", summary="List jobs in the local queue")
def list_jobs(
    status: Optional[str] = Query(None, description="Filter by status: PENDING RUNNING DONE FAILED"),
    limit: int = Query(50, ge=1, le=200),
) -> List[JobSummary]:
    """List jobs in the local SQLite job queue."""
    jobs = local_queue.list_jobs(status=status, limit=limit)
    return [
        JobSummary(
            job_id=j["job_id"],
            job_type=j["job_type"],
            status=j["status"],
            attempts=j["attempts"],
            max_attempts=j["max_attempts"],
            created_at=j["created_at"],
            run_after=j.get("run_after"),
            error=j.get("error"),
        )
        for j in jobs
    ]


@router.post("/queue/flush", summary="Push all unsynced docs to Firestore now")
def flush_queue():
    """
    Synchronously flush all locally-cached unsynced documents to Firestore.
    Returns immediately when offline. Useful after re-establishing connectivity.
    """
    manager = sync_module.get_manager()
    if not manager:
        raise HTTPException(status_code=503, detail="Sync manager not initialised.")

    # Force a fresh connectivity check
    connectivity.invalidate_cache()
    result = manager.flush_now()
    return {"status": "ok", **result}


@router.post("/queue/enqueue", summary="Manually enqueue a pipeline job")
def enqueue_job(request: EnqueueRequest):
    """
    Enqueue a pipeline run for one or all products. Idempotent — submitting
    the same (product_id, horizon, today) combination twice is a no-op.
    """
    payload = {
        "product_id": request.product_id or "ALL",
        "horizon": str(request.horizon),
    }
    job_id = local_queue.enqueue(
        job_type=request.job_type,
        payload=payload,
        max_attempts=3,
    )
    return {
        "status": "accepted",
        "job_id": job_id,
        "message": (
            f"Job {request.job_type} enqueued for "
            f"product_id={payload['product_id']} horizon={request.horizon}d."
        ),
    }


@router.delete("/queue/{job_id}", summary="Cancel a PENDING job")
def cancel_job(job_id: str):
    """Mark a PENDING job as FAILED (cancelled). Has no effect on RUNNING jobs."""
    jobs = local_queue.list_jobs(limit=1000)
    match = next((j for j in jobs if j["job_id"] == job_id), None)
    if not match:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found.")
    if match["status"] != "PENDING":
        raise HTTPException(
            status_code=409,
            detail=f"Job {job_id} is {match['status']} and cannot be cancelled.",
        )
    local_queue.mark_failed(job_id, error="Cancelled via API", retry=False)
    return {"status": "cancelled", "job_id": job_id}


@router.post("/queue/clear-done", summary="Prune DONE/FAILED jobs older than N hours")
def clear_done_jobs(older_than_hours: int = Query(24, ge=1)):
    """Remove completed/failed jobs from the local queue to keep it tidy."""
    deleted = local_queue.clear_done(older_than_hours=older_than_hours)
    return {"status": "ok", "deleted": deleted}
