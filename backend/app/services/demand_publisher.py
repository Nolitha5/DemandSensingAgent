"""
Demand Publisher — Firebase Architecture Compliance Layer
=========================================================
Wraps DemandService to publish all D1-D5 outputs in the standard
AgentOutput envelope to the shared Firestore collections.

Design principles
─────────────────
- DemandService is UNCHANGED — all D1-D5 forecasting logic stays intact.
- This publisher is purely additive: it calls the service, wraps results,
  and delegates to OutputRepository.
- input_refs chain: D2 refs D1 output_id, D3 refs D1, D4 refs D1+D2+D3,
  D5 refs D4 output_id.
- run_id is passed in (generated once per pipeline run in the router/worker).
- Offline-first: OutputRepository handles local-first write + sync.

Consumers (Inventory, Pricing, Procurement) MUST:
  - Read DemandForecast from Firestore agent_state collection.
  - Filter by: agent_id="D4", entity_id=<product_id>.
  - Parse payload as DemandForecast — validated against schema_version.
"""
from __future__ import annotations

import logging
import uuid
from datetime import date
from typing import Dict, List, Optional, Tuple

from app.contracts.demand import (
    CleanDemandSeries,
    DemandForecast,
    DemandSignalAdjustment,
    ForecastQualityAlert,
    ForecastQualityReport,
    SeasonalityProfile,
)
from app.contracts.shared_output import (
    OutputType,
    make_agent_output,
    make_system_event,
)
from app.repositories.output_repository import OutputRepository
from app.services.demand_service import DemandService

logger = logging.getLogger(__name__)

# ── Versioning ────────────────────────────────────────────────────────────────

_SCHEMA_VERSION = "1.0"
_AGENT_VERSIONS: Dict[str, str] = {
    "D1": "D1-1.0",
    "D2": "D2-1.0",
    "D3": "D3-1.0",
    "D4": "D4-1.0",
    "D5": "D5-1.0",
}


def new_run_id() -> str:
    """Generate a fresh pipeline run identifier."""
    return str(uuid.uuid4())


class DemandPublisher:
    """
    Orchestrates D1→D5 and publishes every output in the shared envelope.
    Wraps DemandService — never modifies its logic.
    """

    def __init__(self, db=None, data_dir: Optional[str] = None):
        self._service = DemandService(data_dir=data_dir)
        self._repo = OutputRepository(db=db)

    # ── D1 ────────────────────────────────────────────────────────────────────

    def publish_d1(
        self,
        product_id: str,
        run_id: str,
        reference_date: Optional[date] = None,
    ) -> Tuple[CleanDemandSeries, str]:
        """
        Run D1 and publish the result.
        Returns (CleanDemandSeries, output_id).
        """
        self._repo.start_run(run_id, "D1", product_id, model_or_rule_version=_AGENT_VERSIONS["D1"])

        series_map = self._service.run_d1(product_id, reference_date)
        if product_id not in series_map:
            self._repo.complete_run(run_id, "D1", output_id="", status="FAILED",
                                    error="D1 produced no series")
            raise ValueError(f"D1 produced no series for {product_id}")

        series = series_map[product_id]
        output = make_agent_output(
            agent_id="D1",
            domain="demand",
            output_type=OutputType.CLEAN_DEMAND_SERIES,
            entity_type="product",
            entity_id=product_id,
            payload=series.model_dump(mode="json"),
            run_id=run_id,
            confidence=series.diagnostics.coverage_pct,
            input_refs=[],
            schema_version=_SCHEMA_VERSION,
            model_or_rule_version=_AGENT_VERSIONS["D1"],
        )
        output_id = self._repo.publish(output)
        self._repo.complete_run(run_id, "D1", output_id=output_id)
        return series, output_id

    # ── D2 ────────────────────────────────────────────────────────────────────

    def publish_d2(
        self,
        series: CleanDemandSeries,
        run_id: str,
        d1_output_id: str,
    ) -> Tuple[SeasonalityProfile, str]:
        self._repo.start_run(run_id, "D2", series.product_id,
                             input_refs=[d1_output_id],
                             model_or_rule_version=_AGENT_VERSIONS["D2"])

        profile = self._service.run_d2(series)
        output = make_agent_output(
            agent_id="D2",
            domain="demand",
            output_type=OutputType.SEASONALITY_PROFILE,
            entity_type="product",
            entity_id=series.product_id,
            payload=profile.model_dump(mode="json"),
            run_id=run_id,
            confidence=profile.confidence,
            input_refs=[d1_output_id],
            schema_version=_SCHEMA_VERSION,
            model_or_rule_version=_AGENT_VERSIONS["D2"],
        )
        output_id = self._repo.publish(output)
        self._repo.complete_run(run_id, "D2", output_id=output_id)
        return profile, output_id

    # ── D3 ────────────────────────────────────────────────────────────────────

    def publish_d3(
        self,
        series: CleanDemandSeries,
        run_id: str,
        d1_output_id: str,
        reference_date: Optional[date] = None,
    ) -> Tuple[List[DemandSignalAdjustment], List[str]]:
        """Returns (adjustments list, list of output_ids — one per adjustment)."""
        self._repo.start_run(run_id, "D3", series.product_id,
                             input_refs=[d1_output_id],
                             model_or_rule_version=_AGENT_VERSIONS["D3"])

        adjustments = self._service.run_d3(series, reference_date)
        output_ids = []
        for adj in adjustments:
            confidence = adj.confidence
            # sub-run-id to make each adjustment's output_id unique within the run
            sub_run_id = f"{run_id}_{adj.signal_type}"
            output = make_agent_output(
                agent_id="D3",
                domain="demand",
                output_type=OutputType.DEMAND_SIGNAL_ADJUSTMENT,
                entity_type="product",
                entity_id=series.product_id,
                payload=adj.model_dump(mode="json"),
                run_id=sub_run_id,
                confidence=confidence,
                input_refs=[d1_output_id],
                schema_version=_SCHEMA_VERSION,
                model_or_rule_version=_AGENT_VERSIONS["D3"],
            )
            oid = self._repo.publish(output)
            output_ids.append(oid)

        # If no adjustments, still record a completed run
        sentinel_oid = output_ids[0] if output_ids else ""
        self._repo.complete_run(run_id, "D3", output_id=sentinel_oid)
        return adjustments, output_ids

    # ── D4 ────────────────────────────────────────────────────────────────────

    def publish_d4(
        self,
        product_id: str,
        run_id: str,
        d1_output_id: str,
        d2_output_id: str,
        d3_output_ids: List[str],
        horizon: int = 7,
        reference_date: Optional[date] = None,
    ) -> Tuple[DemandForecast, str]:
        """Publishes DemandForecast and emits forecast.updated system event."""
        all_input_refs = [d1_output_id, d2_output_id] + d3_output_ids

        self._repo.start_run(run_id, "D4", product_id,
                             input_refs=all_input_refs,
                             model_or_rule_version=_AGENT_VERSIONS["D4"])

        forecast = self._service.run_d4(product_id, horizon, reference_date)

        output = make_agent_output(
            agent_id="D4",
            domain="demand",
            output_type=OutputType.DEMAND_FORECAST,
            entity_type="product",
            entity_id=product_id,
            payload=forecast.model_dump(mode="json"),
            run_id=run_id,
            confidence=forecast.confidence,
            input_refs=all_input_refs,
            schema_version=_SCHEMA_VERSION,
            model_or_rule_version=_AGENT_VERSIONS["D4"],
        )
        output_id = self._repo.publish(output)
        self._repo.complete_run(run_id, "D4", output_id=output_id)

        # Emit cross-domain system event so Inventory/Pricing/Procurement can react
        event = make_system_event(
            event_type="forecast.updated",
            source_agent_id="D4",
            entity_type="product",
            entity_id=product_id,
            output_id=output_id,
            payload={
                "product_id": product_id,
                "horizon": horizon,
                "confidence": forecast.confidence,
                "status": forecast.status if isinstance(forecast.status, str) else forecast.status.value,
            },
        )
        self._repo.emit_event(event)
        logger.info("[Publisher] D4 forecast.updated event emitted for %s", product_id)

        return forecast, output_id

    # ── D5 ────────────────────────────────────────────────────────────────────

    def publish_d5(
        self,
        forecast: DemandForecast,
        run_id: str,
        d4_output_id: str,
        prior_wape: Optional[float] = None,
    ) -> Tuple[ForecastQualityReport, List[ForecastQualityAlert], List[str]]:
        """
        Runs D5 and publishes quality alerts. Returns (report, alerts, alert_output_ids).
        """
        self._repo.start_run(run_id, "D5", forecast.product_id,
                             input_refs=[d4_output_id],
                             model_or_rule_version=_AGENT_VERSIONS["D5"])

        report, alerts = self._service.run_d5(forecast, prior_wape)
        alert_output_ids = []

        for alert in alerts:
            severity_conf = {"LOW": 0.9, "MEDIUM": 0.6, "HIGH": 0.3}.get(alert.severity, 0.5)
            sub_run_id = f"{run_id}_{alert.alert_id}"
            output = make_agent_output(
                agent_id="D5",
                domain="demand",
                output_type=OutputType.FORECAST_QUALITY_ALERT,
                entity_type="product",
                entity_id=forecast.product_id,
                payload=alert.model_dump(mode="json"),
                run_id=sub_run_id,
                confidence=severity_conf,
                input_refs=[d4_output_id],
                schema_version=_SCHEMA_VERSION,
                model_or_rule_version=_AGENT_VERSIONS["D5"],
            )
            oid = self._repo.publish(output)
            alert_output_ids.append(oid)

            # Emit quality alert system event for coordinator
            if alert.severity in ("MEDIUM", "HIGH"):
                event = make_system_event(
                    event_type="quality.alert.raised",
                    source_agent_id="D5",
                    entity_type="product",
                    entity_id=forecast.product_id,
                    output_id=oid,
                    payload={
                        "alert_type": alert.alert_type,
                        "severity": alert.severity,
                        "product_id": forecast.product_id,
                    },
                )
                self._repo.emit_event(event)

        sentinel_oid = alert_output_ids[0] if alert_output_ids else d4_output_id
        self._repo.complete_run(run_id, "D5", output_id=sentinel_oid)
        return report, alerts, alert_output_ids

    # ── Full pipeline with publishing ─────────────────────────────────────────

    def run_and_publish_pipeline(
        self,
        product_id: str,
        horizon: int = 7,
        reference_date: Optional[date] = None,
        run_id: Optional[str] = None,
    ) -> Dict:
        """
        Run full D1→D2→D3→D4→D5 chain and publish each output.
        All input_refs are wired up in order.
        Returns a dict with all results and output_ids for traceability.
        """
        if run_id is None:
            run_id = new_run_id()

        logger.info("[Publisher] Starting pipeline run_id=%s product=%s", run_id[:8], product_id)

        # D1
        series, d1_oid = self.publish_d1(product_id, run_id, reference_date)

        # D2
        try:
            profile, d2_oid = self.publish_d2(series, run_id, d1_oid)
        except Exception as exc:
            logger.warning("[Publisher] D2 failed for %s: %s", product_id, exc)
            profile, d2_oid = None, ""

        # D3
        try:
            adjustments, d3_oids = self.publish_d3(series, run_id, d1_oid, reference_date)
        except Exception as exc:
            logger.warning("[Publisher] D3 failed for %s: %s", product_id, exc)
            adjustments, d3_oids = [], []

        # D4
        forecast, d4_oid = self.publish_d4(
            product_id, run_id,
            d1_output_id=d1_oid,
            d2_output_id=d2_oid,
            d3_output_ids=d3_oids,
            horizon=horizon,
            reference_date=reference_date,
        )

        # D5
        try:
            report, alerts, d5_oids = self.publish_d5(forecast, run_id, d4_oid)
        except Exception as exc:
            logger.warning("[Publisher] D5 failed for %s: %s", product_id, exc)
            report, alerts, d5_oids = None, [], []

        return {
            "run_id": run_id,
            "product_id": product_id,
            "forecast": forecast,
            "quality_report": report,
            "alerts": alerts,
            "output_ids": {
                "D1": d1_oid,
                "D2": d2_oid,
                "D3": d3_oids,
                "D4": d4_oid,
                "D5": d5_oids,
            },
        }
