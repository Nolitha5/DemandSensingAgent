"""
Tests for the local SQLite job queue (app.local.queue).
"""
from __future__ import annotations

import pytest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch


@pytest.fixture(autouse=True)
def isolated_db(tmp_path):
    """Each test gets its own fresh SQLite database."""
    db_file = tmp_path / "queue_test.db"
    with patch("app.local.db._LOCAL_DB_PATH", db_file):
        import app.local.db as db_module
        db_module._resolved_path = None
        db_module.init_db(db_file)
        yield db_file
        db_module._resolved_path = None


import app.local.queue as q


class TestEnqueue:
    def test_enqueue_returns_job_id(self):
        jid = q.enqueue("pipeline", {"product_id": "P001", "horizon": "7"})
        assert len(jid) == 36  # UUID format

    def test_enqueue_idempotent_same_key(self):
        jid1 = q.enqueue("pipeline", {}, idempotency_key="same-key")
        jid2 = q.enqueue("pipeline", {}, idempotency_key="same-key")
        assert jid1 == jid2
        # Only one job should exist
        jobs = q.list_jobs()
        assert len(jobs) == 1

    def test_enqueue_different_keys_creates_two_jobs(self):
        q.enqueue("pipeline", {}, idempotency_key="key-A")
        q.enqueue("pipeline", {}, idempotency_key="key-B")
        assert len(q.list_jobs()) == 2

    def test_enqueue_after_done_creates_new_job(self):
        jid1 = q.enqueue("pipeline", {}, idempotency_key="reuse")
        q.claim_next()
        q.mark_done(jid1)
        # Re-submitting with same key after DONE should create a new job
        jid2 = q.enqueue("pipeline", {}, idempotency_key="reuse")
        assert jid2 != jid1

    def test_enqueue_run_after_future(self):
        future = datetime.now(timezone.utc) + timedelta(hours=1)
        jid = q.enqueue("pipeline", {}, run_after=future)
        # Job should not be claimable yet
        job = q.claim_next()
        assert job is None

    def test_default_idem_key_same_day_deduplicates(self):
        p = {"product_id": "P001", "horizon": "7"}
        jid1 = q.enqueue("pipeline", p)
        jid2 = q.enqueue("pipeline", p)
        assert jid1 == jid2


class TestClaimAndComplete:
    def test_claim_next_returns_job(self):
        q.enqueue("pipeline", {"x": 1}, idempotency_key="c1")
        job = q.claim_next()
        assert job is not None
        assert job["status"] == "RUNNING"
        assert job["attempts"] == 1

    def test_claim_next_empty_queue_returns_none(self):
        assert q.claim_next() is None

    def test_claim_sets_status_running(self):
        q.enqueue("pipeline", {}, idempotency_key="c2")
        q.claim_next()
        jobs = q.list_jobs(status="RUNNING")
        assert len(jobs) == 1

    def test_mark_done_sets_status(self):
        q.enqueue("pipeline", {}, idempotency_key="d1")
        job = q.claim_next()
        q.mark_done(job["job_id"])
        done_jobs = q.list_jobs(status="DONE")
        assert len(done_jobs) == 1

    def test_mark_failed_with_retry_requeues(self):
        q.enqueue("pipeline", {}, idempotency_key="f1", max_attempts=3)
        job = q.claim_next()
        q.mark_failed(job["job_id"], "transient error", retry=True)
        # Should be back to PENDING
        jobs = q.list_jobs(status="PENDING")
        assert len(jobs) == 1

    def test_mark_failed_no_retry_exhausted(self):
        q.enqueue("pipeline", {}, idempotency_key="f2", max_attempts=1)
        job = q.claim_next()
        q.mark_failed(job["job_id"], "fatal error", retry=True)
        # max_attempts=1, attempts=1 → exhausted
        failed_jobs = q.list_jobs(status="FAILED")
        assert len(failed_jobs) == 1

    def test_mark_failed_retry_false_goes_to_failed(self):
        q.enqueue("pipeline", {}, idempotency_key="f3", max_attempts=5)
        job = q.claim_next()
        q.mark_failed(job["job_id"], "forced fail", retry=False)
        assert len(q.list_jobs(status="FAILED")) == 1

    def test_claim_by_type_filters(self):
        q.enqueue("pipeline", {}, idempotency_key="pipe1")
        q.enqueue("import_transactions", {}, idempotency_key="imp1")
        job = q.claim_next("import_transactions")
        assert job is not None
        assert job["job_type"] == "import_transactions"


class TestPendingCount:
    def test_pending_count_includes_running(self):
        q.enqueue("pipeline", {}, idempotency_key="pc1")
        q.enqueue("pipeline", {}, idempotency_key="pc2")
        q.claim_next()   # one moves to RUNNING
        count = q.pending_count()
        # PENDING(1) + RUNNING(1) = 2
        assert count == 2

    def test_pending_count_zero_after_done(self):
        q.enqueue("pipeline", {}, idempotency_key="pc3")
        job = q.claim_next()
        q.mark_done(job["job_id"])
        assert q.pending_count() == 0


class TestClearDone:
    def test_clear_done_removes_old_jobs(self):
        q.enqueue("pipeline", {}, idempotency_key="cl1")
        job = q.claim_next()
        q.mark_done(job["job_id"])
        # clear jobs older than 0 hours (i.e., all done jobs)
        deleted = q.clear_done(older_than_hours=0)
        assert deleted >= 1

    def test_clear_done_preserves_pending(self):
        q.enqueue("pipeline", {}, idempotency_key="cl2")
        q.clear_done(older_than_hours=0)
        assert len(q.list_jobs(status="PENDING")) == 1
