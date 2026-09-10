"""
Demand Service
===============
Orchestrates D1 → D2 → D3 → D4 → D5 pipeline.
Keeps agent logic separate from HTTP transport and Firestore persistence.
Used by both the FastAPI router and background workers.
"""
from __future__ import annotations

import logging
from datetime import date
from typing import Dict, List, Optional

import pandas as pd

from app.agents.demand import (
    EventPromotionSignalAgent,
    ForecastGenerator,
    ForecastQualityMonitor,
    SalesHistoryAnalyzer,
    SeasonalityDetector,
)
from app.contracts.demand import (
    CleanDemandSeries,
    DemandForecast,
    DemandSignalAdjustment,
    ForecastQualityAlert,
    ForecastQualityReport,
    InsufficientEvidenceError,
    SeasonalityProfile,
)
from app.data.loader import (
    load_calendar,
    load_local_events,
    load_products,
    load_promotions,
    load_transactions,
)
from app.core.config import get_settings

logger = logging.getLogger(__name__)
_settings = get_settings()


class DemandService:
    """
    Stateless service layer — orchestrates agents and returns typed contracts.
    Does NOT persist to Firestore (that is the router's responsibility).
    """

    def __init__(self, data_dir: Optional[str] = None):
        self._data_dir = data_dir
        self._d1 = SalesHistoryAnalyzer()
        self._d2 = SeasonalityDetector()
        self._d3 = EventPromotionSignalAgent()
        self._d4 = ForecastGenerator()
        self._d5 = ForecastQualityMonitor()

    # ------------------------------------------------------------------
    # Data loading helpers (cached within a request)
    # ------------------------------------------------------------------

    def _load_all(self):
        return {
            "transactions": load_transactions(self._data_dir),
            "products": load_products(self._data_dir),
            "promotions": load_promotions(self._data_dir),
            "events": load_local_events(self._data_dir),
            "calendar": load_calendar(self._data_dir),
        }

    # ------------------------------------------------------------------
    # D1
    # ------------------------------------------------------------------

    def run_d1(
        self,
        product_id: Optional[str] = None,
        reference_date: Optional[date] = None,
    ) -> Dict[str, CleanDemandSeries]:
        data = self._load_all()
        tx = data["transactions"]
        if product_id:
            return {product_id: self._d1.run(tx, product_id, reference_date)}
        return self._d1.run_all_products(tx, reference_date=reference_date)

    # ------------------------------------------------------------------
    # D2
    # ------------------------------------------------------------------

    def run_d2(
        self,
        series: CleanDemandSeries,
    ) -> SeasonalityProfile:
        calendar = load_calendar(self._data_dir)
        detector = SeasonalityDetector(calendar=calendar)
        return detector.run(series)

    def run_d2_for_product(self, product_id: str) -> SeasonalityProfile:
        series_map = self.run_d1(product_id)
        if product_id not in series_map:
            raise InsufficientEvidenceError("D2", product_id, "D1 produced no series.")
        return self.run_d2(series_map[product_id])

    # ------------------------------------------------------------------
    # D3
    # ------------------------------------------------------------------

    def run_d3(
        self,
        series: CleanDemandSeries,
        reference_date: Optional[date] = None,
    ) -> List[DemandSignalAdjustment]:
        data = self._load_all()
        return self._d3.run(
            promotions=data["promotions"],
            events=data["events"],
            series=series,
            products=data["products"],
            reference_date=reference_date,
        )

    def run_d3_for_product(
        self, product_id: str, reference_date: Optional[date] = None
    ) -> List[DemandSignalAdjustment]:
        series_map = self.run_d1(product_id)
        if product_id not in series_map:
            return []
        return self.run_d3(series_map[product_id], reference_date)

    # ------------------------------------------------------------------
    # D4
    # ------------------------------------------------------------------

    def run_d4(
        self,
        product_id: str,
        horizon: int = 7,
        reference_date: Optional[date] = None,
    ) -> DemandForecast:
        # D1
        series_map = self.run_d1(product_id, reference_date)
        if product_id not in series_map:
            return self._d4._insufficient_evidence(
                product_id, horizon, reference_date or date.today(),
                "D1 produced no clean series."
            )
        series = series_map[product_id]

        # D2
        try:
            seasonality = self.run_d2(series)
        except Exception as e:
            logger.warning("[D4] D2 failed for %s: %s", product_id, e)
            seasonality = None

        # D3
        try:
            signals = self.run_d3(series, reference_date)
        except Exception as e:
            logger.warning("[D4] D3 failed for %s: %s", product_id, e)
            signals = []

        return self._d4.run(series, seasonality, signals, horizon, reference_date)

    def run_d4_all_products(
        self, horizon: int = 7, reference_date: Optional[date] = None
    ) -> Dict[str, DemandForecast]:
        data = self._load_all()
        all_product_ids = data["products"]["product_id"].tolist()
        results = {}
        for pid in all_product_ids:
            results[pid] = self.run_d4(pid, horizon, reference_date)
        return results

    # ------------------------------------------------------------------
    # D5
    # ------------------------------------------------------------------

    def run_d5(
        self,
        forecast: DemandForecast,
        prior_wape: Optional[float] = None,
    ):
        tx = load_transactions(self._data_dir)
        return self._d5.evaluate(forecast, tx, prior_wape)

    def run_d5_all(
        self,
        forecasts: List[DemandForecast],
        prior_wapes: Optional[Dict[str, float]] = None,
    ):
        tx = load_transactions(self._data_dir)
        return self._d5.evaluate_all(forecasts, tx, prior_wapes)

    # ------------------------------------------------------------------
    # Full pipeline
    # ------------------------------------------------------------------

    def run_full_pipeline(
        self, horizon: int = 7, reference_date: Optional[date] = None
    ) -> Dict:
        """Run D1→D4 for all products, then D5 on all forecasts."""
        forecasts = self.run_d4_all_products(horizon, reference_date)
        reports, alerts = self.run_d5_all(list(forecasts.values()))
        return {
            "forecasts": forecasts,
            "quality_reports": {r.product_id: r for r in reports},
            "alerts": alerts,
        }
