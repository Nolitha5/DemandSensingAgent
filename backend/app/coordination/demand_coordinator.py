"""
Demand Domain Coordinator
==========================
Wires up event subscriptions so agents trigger each other
through the event bus without knowing about each other.

Rules implemented here:
- D1 runs after transactions are imported
- D2 and D3 run after D1 completes
- D4 runs once D1 + D2 + D3 have all completed for a product
- D5 runs after D4 completes
"""
from __future__ import annotations

import logging
from datetime import date
from threading import Lock
from typing import Dict, Optional, Set

from .event_bus import DomainEvent, EventBus, EventType
from app.services.demand_service import DemandService

logger = logging.getLogger(__name__)


class DemandCoordinator:
    """
    Lightweight coordinator — knows WHAT happened, WHO should react.
    Contains NO forecasting or ML logic.
    """

    def __init__(self, bus: EventBus, service: DemandService):
        self._bus = bus
        self._service = service
        self._lock = Lock()
        # Track which agents have completed for each product (for D4 gate)
        self._completed: Dict[str, Set[str]] = {}

        # Wire subscriptions
        bus.subscribe(EventType.TRANSACTIONS_IMPORTED, self._on_transactions_imported)
        bus.subscribe(EventType.PROMOTIONS_UPDATED, self._on_promotion_or_event_changed)
        bus.subscribe(EventType.LOCAL_EVENT_CREATED, self._on_promotion_or_event_changed)
        bus.subscribe(EventType.D1_COMPLETED, self._on_d1_completed)
        bus.subscribe(EventType.D2_COMPLETED, self._on_dependency_completed)
        bus.subscribe(EventType.D3_COMPLETED, self._on_dependency_completed)
        bus.subscribe(EventType.D4_COMPLETED, self._on_d4_completed)

    # ------------------------------------------------------------------
    # Handlers
    # ------------------------------------------------------------------

    def _on_transactions_imported(self, event: DomainEvent) -> None:
        """New transactions → trigger D1 for affected products."""
        logger.info("[Coordinator] Transactions imported — triggering D1")
        product_ids = event.payload.get("product_ids")
        ref_date = event.payload.get("reference_date")
        self._run_d1(product_ids, ref_date)

    def _on_promotion_or_event_changed(self, event: DomainEvent) -> None:
        """Promotion or event changed → re-run D3 for the product."""
        pid = event.product_id
        logger.info("[Coordinator] Signal changed for %s — triggering D3", pid)
        if pid:
            self._run_d3_for_product(pid)

    def _on_d1_completed(self, event: DomainEvent) -> None:
        """D1 done → trigger D2 + D3 in parallel (sequentially here for simplicity)."""
        pid = event.product_id
        series = event.payload.get("series")
        if not pid or series is None:
            return
        logger.info("[Coordinator] D1 completed for %s — triggering D2 + D3", pid)
        self._run_d2(pid, series)
        self._run_d3(pid, series)

    def _on_dependency_completed(self, event: DomainEvent) -> None:
        """D2 or D3 completed — check if D4 gate is satisfied."""
        pid = event.product_id
        agent = event.payload.get("agent")
        if not pid or not agent:
            return
        with self._lock:
            self._completed.setdefault(pid, set()).add(agent)
            completed = self._completed[pid]
        logger.info("[Coordinator] %s completed for %s (completed: %s)", agent, pid, completed)
        if {"D1", "D2", "D3"}.issubset(completed):
            logger.info("[Coordinator] Gate satisfied for %s — triggering D4", pid)
            self._run_d4(pid)

    def _on_d4_completed(self, event: DomainEvent) -> None:
        """D4 done → trigger D5."""
        pid = event.product_id
        forecast = event.payload.get("forecast")
        logger.info("[Coordinator] D4 completed for %s — triggering D5", pid)
        if forecast:
            self._run_d5(pid, forecast)

    # ------------------------------------------------------------------
    # Internal triggers (each publishes the relevant .completed event)
    # ------------------------------------------------------------------

    def _run_d1(self, product_ids, ref_date):
        try:
            results = self._service.run_d1(reference_date=ref_date)
            for pid, series in results.items():
                with self._lock:
                    self._completed.setdefault(pid, set()).add("D1")
                self._bus.publish(DomainEvent(
                    EventType.D1_COMPLETED, pid, {"series": series, "agent": "D1"}
                ))
        except Exception as e:
            logger.error("[Coordinator] D1 failed: %s", e, exc_info=True)
            self._bus.publish(DomainEvent(EventType.AGENT_FAILED, payload={"agent": "D1", "error": str(e)}))

    def _run_d2(self, pid, series):
        try:
            profile = self._service.run_d2(series)
            with self._lock:
                self._completed.setdefault(pid, set()).add("D2")
            self._bus.publish(DomainEvent(
                EventType.D2_COMPLETED, pid, {"profile": profile, "agent": "D2"}
            ))
        except Exception as e:
            logger.error("[Coordinator] D2 failed for %s: %s", pid, e, exc_info=True)

    def _run_d3(self, pid, series):
        try:
            signals = self._service.run_d3(series)
            with self._lock:
                self._completed.setdefault(pid, set()).add("D3")
            self._bus.publish(DomainEvent(
                EventType.D3_COMPLETED, pid, {"signals": signals, "agent": "D3"}
            ))
        except Exception as e:
            logger.error("[Coordinator] D3 failed for %s: %s", pid, e, exc_info=True)

    def _run_d3_for_product(self, pid):
        try:
            signals = self._service.run_d3_for_product(pid)
            with self._lock:
                self._completed.setdefault(pid, set()).add("D3")
            self._bus.publish(DomainEvent(
                EventType.D3_COMPLETED, pid, {"signals": signals, "agent": "D3"}
            ))
        except Exception as e:
            logger.error("[Coordinator] D3 (re-run) failed for %s: %s", pid, e, exc_info=True)

    def _run_d4(self, pid):
        try:
            forecast = self._service.run_d4(pid)
            self._bus.publish(DomainEvent(
                EventType.D4_COMPLETED, pid, {"forecast": forecast, "agent": "D4"}
            ))
        except Exception as e:
            logger.error("[Coordinator] D4 failed for %s: %s", pid, e, exc_info=True)

    def _run_d5(self, pid, forecast):
        try:
            report, alerts = self._service.run_d5(forecast)
            self._bus.publish(DomainEvent(
                EventType.D5_COMPLETED, pid, {"report": report, "alerts": alerts, "agent": "D5"}
            ))
        except Exception as e:
            logger.error("[Coordinator] D5 failed for %s: %s", pid, e, exc_info=True)
