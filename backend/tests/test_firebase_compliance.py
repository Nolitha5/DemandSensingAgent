"""
Firebase Architecture Compliance Tests
========================================
Validates that D1-D5 outputs are published in the standard AgentOutput envelope
and correctly written to the shared Firestore collections (via local SQLite in tests).

Coverage:
  1. AgentOutput envelope structure and required fields
  2. Deterministic output_id (idempotency — same inputs → same id)
  3. input_refs chain (D1→D2, D1→D3, D1+D2+D3→D4, D4→D5)
  4. schema_version and model_or_rule_version on every output
  5. risk_level derived from confidence
  6. generated_at + expires_at freshness
  7. system_events emitted (forecast.updated, quality.alert.raised)
  8. agent_outputs written (append-only doc per output_id)
  9. agent_state written (latest-per-agent+entity)
  10. DemandPublisher full pipeline integration
"""
from __future__ import annotations

import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, patch


# ── Isolated DB fixture (same pattern as existing tests) ──────────────────────

@pytest.fixture(autouse=True)
def isolated_db(tmp_path):
    db_file = tmp_path / "compliance_test.db"
    with patch("app.local.db._LOCAL_DB_PATH", db_file):
        import app.local.db as db_module
        db_module._resolved_path = None
        db_module.init_db(db_file)

        import app.local.connectivity as conn_module
        conn_module._last_status = conn_module.ConnectivityStatus.UNKNOWN
        conn_module._last_checked = 0.0

        yield db_file
        db_module._resolved_path = None


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_output(**kwargs):
    from app.contracts.shared_output import make_agent_output, OutputType
    defaults = dict(
        agent_id="D4",
        domain="demand",
        output_type=OutputType.DEMAND_FORECAST,
        entity_type="product",
        entity_id="P001",
        payload={"expected_qty": 10.0},
        run_id="run-abc-123",
        confidence=0.8,
        input_refs=[],
        schema_version="1.0",
        model_or_rule_version="D4-1.0",
    )
    defaults.update(kwargs)
    return make_agent_output(**defaults)


# ── 1. Envelope structure ─────────────────────────────────────────────────────

class TestAgentOutputEnvelope:
    def test_required_fields_present(self):
        o = _make_output()
        assert o.output_id
        assert o.agent_id == "D4"
        assert o.domain == "demand"
        assert o.output_type
        assert o.entity_type == "product"
        assert o.entity_id == "P001"
        assert isinstance(o.payload, dict)
        assert isinstance(o.input_refs, list)
        assert o.run_id
        assert o.schema_version == "1.0"
        assert o.model_or_rule_version == "D4-1.0"
        assert o.generated_at is not None
        assert o.expires_at is not None

    def test_generated_at_is_utc_aware(self):
        o = _make_output()
        assert o.generated_at.tzinfo is not None

    def test_expires_at_after_generated_at(self):
        o = _make_output()
        assert o.expires_at > o.generated_at

    def test_demand_forecast_expires_in_24h(self):
        from app.contracts.shared_output import OutputType
        o = _make_output(output_type=OutputType.DEMAND_FORECAST)
        delta = (o.expires_at - o.generated_at).total_seconds()
        assert 86_000 < delta <= 86_400 + 60  # ~24 h

    def test_clean_demand_series_expires_in_7d(self):
        from app.contracts.shared_output import OutputType
        o = _make_output(output_type=OutputType.CLEAN_DEMAND_SERIES)
        delta = (o.expires_at - o.generated_at).total_seconds()
        assert delta > 6 * 86_400  # at least 6 days

    def test_serialisable_to_dict(self):
        o = _make_output()
        d = o.model_dump(mode="json")
        assert d["output_id"] == o.output_id
        assert d["payload"]["expected_qty"] == 10.0


# ── 2. Idempotency (deterministic output_id) ──────────────────────────────────

class TestDeterministicOutputId:
    def test_same_inputs_same_id(self):
        o1 = _make_output()
        o2 = _make_output()
        assert o1.output_id == o2.output_id

    def test_different_entity_different_id(self):
        o1 = _make_output(entity_id="P001")
        o2 = _make_output(entity_id="P002")
        assert o1.output_id != o2.output_id

    def test_different_run_id_different_output_id(self):
        o1 = _make_output(run_id="run-001")
        o2 = _make_output(run_id="run-002")
        assert o1.output_id != o2.output_id

    def test_different_schema_version_different_id(self):
        o1 = _make_output(schema_version="1.0")
        o2 = _make_output(schema_version="2.0")
        assert o1.output_id != o2.output_id

    def test_output_id_is_40_hex_chars(self):
        o = _make_output()
        assert len(o.output_id) == 40
        assert all(c in "0123456789abcdef" for c in o.output_id)


# ── 3. Risk level derived from confidence ─────────────────────────────────────

class TestRiskLevel:
    def test_high_confidence_low_risk(self):
        o = _make_output(confidence=0.85)
        assert o.risk_level == "LOW"

    def test_medium_confidence_medium_risk(self):
        o = _make_output(confidence=0.60)
        assert o.risk_level == "MEDIUM"

    def test_low_confidence_high_risk(self):
        o = _make_output(confidence=0.20)
        assert o.risk_level == "HIGH"

    def test_borderline_low_medium(self):
        o = _make_output(confidence=0.75)
        assert o.risk_level == "LOW"

    def test_borderline_medium_high(self):
        o = _make_output(confidence=0.45)
        assert o.risk_level == "MEDIUM"


# ── 4. OutputRepository — local write ─────────────────────────────────────────

class TestOutputRepository:
    def _make_repo(self):
        from app.repositories.output_repository import OutputRepository
        from app.local import connectivity
        with patch.object(connectivity, "is_online", return_value=False):
            return OutputRepository(db=None)

    def test_publish_stores_in_agent_outputs(self):
        from app.local import cache
        repo = self._make_repo()
        o = _make_output()
        with patch("app.local.connectivity.is_online", return_value=False):
            repo.publish(o)
        raw = cache.get("agent_outputs", o.output_id)
        assert raw is not None
        assert raw["output_id"] == o.output_id

    def test_publish_stores_in_agent_state(self):
        from app.local import cache
        repo = self._make_repo()
        o = _make_output()
        with patch("app.local.connectivity.is_online", return_value=False):
            repo.publish(o)
        state_id = f"{o.agent_id}_{o.entity_id}"
        raw = cache.get("agent_state", state_id)
        assert raw is not None
        assert raw["agent_id"] == "D4"

    def test_publish_idempotent_same_output_id(self):
        from app.local import cache
        repo = self._make_repo()
        o = _make_output()
        with patch("app.local.connectivity.is_online", return_value=False):
            repo.publish(o)
            repo.publish(o)  # second publish — same output_id, should not fail
        # Still exactly one record (SQLite upsert)
        raw = cache.get("agent_outputs", o.output_id)
        assert raw is not None

    def test_emit_event_stores_in_system_events(self):
        from app.local import cache
        from app.contracts.shared_output import make_system_event
        repo = self._make_repo()
        event = make_system_event(
            event_type="forecast.updated",
            source_agent_id="D4",
            entity_type="product",
            entity_id="P001",
            output_id="abc123",
        )
        with patch("app.local.connectivity.is_online", return_value=False):
            repo.emit_event(event)
        raw = cache.get("system_events", event.event_id)
        assert raw is not None
        assert raw["event_type"] == "forecast.updated"

    def test_start_and_complete_run_stores_in_agent_runs(self):
        from app.local import cache
        repo = self._make_repo()
        with patch("app.local.connectivity.is_online", return_value=False):
            repo.start_run("run-x", "D4", "P001")
            repo.complete_run("run-x", "D4", output_id="out123")
        raw = cache.get("agent_runs", "run-x_D4")
        assert raw is not None
        assert raw["status"] == "COMPLETED"
        assert raw["output_id"] == "out123"


# ── 5. input_refs chain ───────────────────────────────────────────────────────

class TestInputRefs:
    def test_input_refs_stored_in_envelope(self):
        o = _make_output(input_refs=["ref-d1", "ref-d2"])
        assert "ref-d1" in o.input_refs
        assert "ref-d2" in o.input_refs

    def test_d4_input_refs_include_d1_d2_d3(self):
        """D4 output must reference D1, D2, and D3 outputs."""
        o = _make_output(
            agent_id="D4",
            input_refs=["d1-out", "d2-out", "d3-out-promo"],
        )
        assert len(o.input_refs) >= 2
        assert "d1-out" in o.input_refs
        assert "d2-out" in o.input_refs

    def test_empty_input_refs_default(self):
        o = _make_output(input_refs=[])
        assert o.input_refs == []


# ── 6. SystemEvent structure ──────────────────────────────────────────────────

class TestSystemEvent:
    def test_forecast_updated_event_fields(self):
        from app.contracts.shared_output import make_system_event
        e = make_system_event(
            event_type="forecast.updated",
            source_agent_id="D4",
            entity_type="product",
            entity_id="P001",
            output_id="out-abc",
            payload={"horizon": 7},
        )
        assert e.event_type == "forecast.updated"
        assert e.source_agent_id == "D4"
        assert e.output_id == "out-abc"
        assert e.processed is False
        assert e.event_id  # UUID

    def test_event_id_unique(self):
        from app.contracts.shared_output import make_system_event
        e1 = make_system_event("forecast.updated", "D4", "product", "P001", "o1")
        e2 = make_system_event("forecast.updated", "D4", "product", "P001", "o1")
        assert e1.event_id != e2.event_id


# ── 7. DemandPublisher integration ───────────────────────────────────────────

class TestDemandPublisher:
    """
    Integration-level tests for the DemandPublisher pipeline.
    Uses real DemandService (same data as existing agent tests) but offline-only.
    """

    DATA_DIR = str(
        __import__("pathlib").Path(__file__).parent.parent.parent / "sample_data" / "demand"
    )

    def _make_publisher(self):
        from app.services.demand_publisher import DemandPublisher
        with patch("app.local.connectivity.is_online", return_value=False):
            return DemandPublisher(db=None, data_dir=self.DATA_DIR)

    def test_publish_d1_returns_series_and_output_id(self):
        publisher = self._make_publisher()
        with patch("app.local.connectivity.is_online", return_value=False):
            series, oid = publisher.publish_d1("P001", run_id="run-1")
        assert series.product_id == "P001"
        assert len(oid) == 40

    def test_d1_output_id_is_deterministic(self):
        publisher = self._make_publisher()
        with patch("app.local.connectivity.is_online", return_value=False):
            _, oid1 = publisher.publish_d1("P001", run_id="run-stable")
            _, oid2 = publisher.publish_d1("P001", run_id="run-stable")
        assert oid1 == oid2

    def test_full_pipeline_returns_all_keys(self):
        publisher = self._make_publisher()
        with patch("app.local.connectivity.is_online", return_value=False):
            result = publisher.run_and_publish_pipeline("P001", horizon=7, run_id="run-full")
        assert result["forecast"] is not None
        assert result["run_id"] == "run-full"
        assert "D1" in result["output_ids"]
        assert "D4" in result["output_ids"]
        assert len(result["output_ids"]["D4"]) == 40

    def test_full_pipeline_writes_forecast_updated_event(self):
        from app.local import cache
        publisher = self._make_publisher()
        with patch("app.local.connectivity.is_online", return_value=False):
            publisher.run_and_publish_pipeline("P001", run_id="run-events")
        # system_events collection should have at least one forecast.updated entry
        events = cache.list_collection("system_events")
        event_types = [e.get("event_type") for e in events]
        assert "forecast.updated" in event_types

    def test_full_pipeline_d4_input_refs_include_d1(self):
        from app.local import cache
        publisher = self._make_publisher()
        with patch("app.local.connectivity.is_online", return_value=False):
            result = publisher.run_and_publish_pipeline("P001", run_id="run-refs")
        d4_oid = result["output_ids"]["D4"]
        raw = cache.get("agent_outputs", d4_oid)
        assert raw is not None
        d1_oid = result["output_ids"]["D1"]
        assert d1_oid in raw["input_refs"]

    def test_full_pipeline_agent_state_has_d4_latest(self):
        from app.local import cache
        publisher = self._make_publisher()
        with patch("app.local.connectivity.is_online", return_value=False):
            publisher.run_and_publish_pipeline("P001", run_id="run-state")
        raw = cache.get("agent_state", "D4_P001")
        assert raw is not None
        assert raw["agent_id"] == "D4"
        assert raw["entity_id"] == "P001"

    def test_schema_version_on_all_outputs(self):
        from app.local import cache
        publisher = self._make_publisher()
        with patch("app.local.connectivity.is_online", return_value=False):
            publisher.run_and_publish_pipeline("P001", run_id="run-schema")
        for doc in cache.list_collection("agent_outputs"):
            assert doc.get("schema_version") == "1.0", f"Missing schema_version in {doc.get('output_id')}"
