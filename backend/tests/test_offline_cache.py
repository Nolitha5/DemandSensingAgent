"""
Tests for the local SQLite cache (app.local.cache).
"""
from __future__ import annotations

import pytest
from pathlib import Path
from unittest.mock import patch


@pytest.fixture(autouse=True)
def isolated_db(tmp_path):
    """Each test gets its own fresh SQLite database."""
    db_file = tmp_path / "test_demand.db"
    with patch("app.local.db._LOCAL_DB_PATH", db_file):
        # Reset module-level resolved path so init_db() runs fresh
        import app.local.db as db_module
        db_module._resolved_path = None
        db_module.init_db(db_file)
        yield db_file
        db_module._resolved_path = None


import app.local.cache as cache


class TestCachePut:
    def test_put_returns_version_1_for_new_doc(self):
        v = cache.put("forecasts", "P001", {"expected_qty": 100})
        assert v == 1

    def test_put_increments_version_on_update(self):
        cache.put("forecasts", "P001", {"expected_qty": 100})
        v2 = cache.put("forecasts", "P001", {"expected_qty": 110})
        assert v2 == 2

    def test_put_marks_unsynced(self):
        cache.put("forecasts", "P001", {"expected_qty": 50})
        assert cache.count_unsynced() == 1

    def test_put_different_collections_independent(self):
        cache.put("forecasts", "P001", {"a": 1})
        cache.put("seasonality", "P001", {"b": 2})
        assert cache.count_unsynced() == 2


class TestCacheGet:
    def test_get_returns_none_for_missing(self):
        assert cache.get("forecasts", "MISSING") is None

    def test_get_returns_stored_data(self):
        cache.put("forecasts", "P002", {"expected_qty": 77})
        result = cache.get("forecasts", "P002")
        assert result == {"expected_qty": 77}

    def test_get_with_meta_includes_flags(self):
        cache.put("forecasts", "P003", {"model": "naive"})
        meta = cache.get_with_meta("forecasts", "P003")
        assert meta is not None
        assert meta["_cache_version"] == 1
        assert meta["_cache_synced"] is False
        assert "_cache_stale" in meta
        assert "_cache_updated_at" in meta

    def test_list_collection_returns_all_docs(self):
        cache.put("forecasts", "P001", {"x": 1})
        cache.put("forecasts", "P002", {"x": 2})
        items = cache.list_collection("forecasts")
        assert len(items) == 2
        ids = {i["_doc_id"] for i in items}
        assert ids == {"P001", "P002"}


class TestMarkSynced:
    def test_mark_synced_clears_unsynced(self):
        v = cache.put("forecasts", "P001", {"q": 10})
        cache.mark_synced("forecasts", "P001", v)
        assert cache.count_unsynced() == 0

    def test_mark_synced_wrong_version_is_noop(self):
        cache.put("forecasts", "P001", {"q": 10})
        cache.put("forecasts", "P001", {"q": 20})   # version=2 now
        cache.mark_synced("forecasts", "P001", 1)    # stale version
        # Still unsynced because version 2 hasn't been marked
        assert cache.count_unsynced() == 1


class TestGetUnsynced:
    def test_returns_oldest_first(self):
        import time
        cache.put("forecasts", "P001", {"order": 1})
        time.sleep(0.01)
        cache.put("forecasts", "P002", {"order": 2})
        rows = cache.get_unsynced(limit=10)
        assert rows[0][1] == "P001"   # (collection, doc_id, version, data)
        assert rows[1][1] == "P002"

    def test_returns_correct_tuple_structure(self):
        cache.put("quality", "Q001", {"status": "OK"})
        rows = cache.get_unsynced()
        assert len(rows) == 1
        collection, doc_id, version, data = rows[0]
        assert collection == "quality"
        assert doc_id == "Q001"
        assert version == 1
        assert data == {"status": "OK"}


class TestDelete:
    def test_delete_removes_doc(self):
        cache.put("forecasts", "P001", {"x": 1})
        cache.delete("forecasts", "P001")
        assert cache.get("forecasts", "P001") is None

    def test_delete_nonexistent_is_noop(self):
        cache.delete("forecasts", "NOPE")  # should not raise
