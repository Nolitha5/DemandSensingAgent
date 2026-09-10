"""
Tests for the connectivity probe and sync manager (app.local.connectivity, app.local.sync).
"""
from __future__ import annotations

import pytest
from unittest.mock import MagicMock, patch


@pytest.fixture(autouse=True)
def isolated_db(tmp_path):
    db_file = tmp_path / "sync_test.db"
    with patch("app.local.db._LOCAL_DB_PATH", db_file):
        import app.local.db as db_module
        db_module._resolved_path = None
        db_module.init_db(db_file)

        # Reset connectivity module state so tests don't share cached probe results
        import app.local.connectivity as conn_module
        conn_module._last_status = conn_module.ConnectivityStatus.UNKNOWN
        conn_module._last_checked = 0.0

        yield db_file
        db_module._resolved_path = None


# ── Connectivity tests ─────────────────────────────────────────────────────────

class TestConnectivity:
    def test_probe_online_when_http_succeeds(self):
        from app.local.connectivity import ConnectivityStatus, _probe
        with patch("urllib.request.urlopen") as mock_open:
            mock_resp = MagicMock()
            mock_resp.__enter__ = MagicMock(return_value=mock_resp)
            mock_resp.__exit__ = MagicMock(return_value=False)
            mock_resp.status = 200
            mock_open.return_value = mock_resp
            assert _probe() == ConnectivityStatus.ONLINE

    def test_probe_offline_when_http_fails(self):
        from app.local.connectivity import ConnectivityStatus, _probe
        with patch("urllib.request.urlopen", side_effect=OSError("network down")):
            assert _probe() == ConnectivityStatus.OFFLINE

    def test_get_status_caches_result(self):
        from app.local import connectivity
        connectivity.invalidate_cache()
        with patch("app.local.connectivity._probe", return_value=connectivity.ConnectivityStatus.ONLINE) as mock_probe:
            connectivity.get_status()
            connectivity.get_status()
            # Should only probe once; second call hits cache
            mock_probe.assert_called_once()

    def test_get_status_force_re_probes(self):
        from app.local import connectivity
        connectivity.invalidate_cache()
        with patch("app.local.connectivity._probe", return_value=connectivity.ConnectivityStatus.ONLINE) as mock_probe:
            connectivity.get_status()
            connectivity.get_status(force=True)
            assert mock_probe.call_count == 2

    def test_invalidate_cache_resets_last_checked(self):
        from app.local import connectivity
        connectivity.get_status()
        # After invalidate, _last_checked should be reset to 0
        connectivity.invalidate_cache()
        import app.local.connectivity as conn_mod
        assert conn_mod._last_checked == 0.0

    def test_diagnostics_returns_dict(self):
        from app.local.connectivity import diagnostics
        d = diagnostics()
        assert "status" in d
        assert "probe_count" in d
        assert "ttl_seconds" in d


# ── SyncManager tests ──────────────────────────────────────────────────────────

class TestSyncManager:
    def _make_manager(self, firestore_client=None):
        from app.local.sync import SyncManager
        return SyncManager(firestore_client)

    def test_start_and_stop_no_error(self):
        mgr = self._make_manager()
        mgr.start()
        mgr.stop(timeout=1.0)

    def test_stats_returns_expected_keys(self):
        mgr = self._make_manager()
        stats = mgr.stats()
        assert "pushed_total" in stats
        assert "pulled_total" in stats
        assert "errors_total" in stats
        assert "unsynced_local" in stats
        assert "connectivity" in stats

    def test_flush_now_returns_offline_when_no_connection(self):
        from app.local import connectivity
        mgr = self._make_manager(firestore_client=MagicMock())
        with patch.object(connectivity, "get_status",
                          return_value=connectivity.ConnectivityStatus.OFFLINE):
            result = mgr.flush_now()
        assert result["message"] == "offline"
        assert result["pushed"] == 0

    def test_flush_now_no_client_returns_no_client(self):
        mgr = self._make_manager(firestore_client=None)
        result = mgr.flush_now()
        assert result["message"] == "no_firestore_client"

    def test_push_marks_local_doc_synced(self):
        import app.local.cache as cache
        from app.local import connectivity

        cache.put("forecasts", "P001", {"expected_qty": 100})

        # Build a mock Firestore client
        mock_snap = MagicMock()
        mock_snap.exists = False
        mock_doc_ref = MagicMock()
        mock_doc_ref.get.return_value = mock_snap
        mock_collection = MagicMock()
        mock_collection.document.return_value = mock_doc_ref
        mock_fs = MagicMock()
        mock_fs.collection.return_value = mock_collection

        mgr = self._make_manager(firestore_client=mock_fs)

        with patch.object(connectivity, "get_status",
                          return_value=connectivity.ConnectivityStatus.ONLINE):
            result = mgr.flush_now()

        assert result["pushed"] == 1
        assert cache.count_unsynced() == 0

    def test_push_skips_if_remote_version_newer(self):
        import app.local.cache as cache
        from app.local import connectivity

        cache.put("forecasts", "P002", {"expected_qty": 50})

        # Remote claims to have a newer version
        mock_snap = MagicMock()
        mock_snap.exists = True
        mock_snap.to_dict.return_value = {"expected_qty": 60, "_local_version": 99}
        mock_doc_ref = MagicMock()
        mock_doc_ref.get.return_value = mock_snap
        mock_collection = MagicMock()
        mock_collection.document.return_value = mock_doc_ref
        mock_fs = MagicMock()
        mock_fs.collection.return_value = mock_collection

        mgr = self._make_manager(firestore_client=mock_fs)

        with patch.object(connectivity, "get_status",
                          return_value=connectivity.ConnectivityStatus.ONLINE):
            result = mgr.flush_now()

        # Remote was newer, so we skipped the push but still marked local synced
        assert result["pushed"] == 1
        assert cache.count_unsynced() == 0
        # set() should NOT have been called (we didn't overwrite remote)
        mock_doc_ref.set.assert_not_called()


# ── init_sync / shutdown_sync ──────────────────────────────────────────────────

class TestSyncModuleLifecycle:
    def test_init_sync_returns_manager(self):
        from app.local import sync
        mgr = sync.init_sync(firestore_client=None)
        assert mgr is not None
        sync.shutdown_sync()

    def test_get_manager_after_init(self):
        from app.local import sync
        sync.init_sync()
        assert sync.get_manager() is not None
        sync.shutdown_sync()
        assert sync.get_manager() is None
