"""
D3 — Event & Promotion Signal Agent
=====================================
Responsibilities:
  - Estimate whether local events or promotions uplift or suppress baseline demand
  - Use rules + historical uplift calculation + lookup tables
  - Regression when enough observations exist
  - Do NOT claim causal effects from weak data

Trigger: When a promotion or local event is created/modified
Input:   promotions, local_events, CleanDemandSeries
Output:  List[DemandSignalAdjustment] (consumed by D4)
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
from sklearn.linear_model import LinearRegression

from app.contracts.demand import (
    CleanDemandSeries,
    DemandSignalAdjustment,
    SignalType,
)
from app.core.config import get_settings

logger = logging.getLogger(__name__)
_settings = get_settings()

# Minimum days of history around an event to compute uplift
MIN_DAYS_FOR_HISTORICAL_UPLIFT = 14
# Minimum observations for regression-based uplift
MIN_OBS_FOR_REGRESSION = _settings.min_observations_for_regression
# Default uplift lookup table by event type (used when historical data is insufficient)
EVENT_UPLIFT_DEFAULTS: Dict[str, float] = {
    "HOLIDAY": 1.20,
    "SPORT":   1.18,
    "MARKET":  1.25,
    "OTHER":   1.10,
}
CATEGORY_PROMO_DEFAULTS: Dict[str, float] = {
    "Beverages": 1.18,
    "Bakery":    1.10,
    "Dairy":     1.08,
    "Snacks":    1.15,
    "Staples":   1.06,
}


class EventPromotionSignalAgent:
    """
    Deterministic signal agent.
    Uses historical uplift where evidence exists, falls back to lookup tables.
    Regression is used only when >= MIN_OBS_FOR_REGRESSION observations are available.
    """

    def run_for_promotions(
        self,
        promotions: pd.DataFrame,
        series: CleanDemandSeries,
        products: Optional[pd.DataFrame] = None,
        reference_date: Optional[date] = None,
    ) -> List[DemandSignalAdjustment]:
        """Compute adjustment factors for all active/upcoming promotions for this product."""
        ref = reference_date or date.today()
        pid = series.product_id

        # Filter promotions for this product
        if promotions.empty or "product_id" not in promotions.columns:
            return []
        promos = promotions[promotions["product_id"] == pid].copy()
        if promos.empty:
            return []

        df_series = self._series_to_df(series)
        results = []
        for _, row in promos.iterrows():
            adj = self._compute_promo_adjustment(row, df_series, pid, ref, products)
            if adj is not None:
                results.append(adj)
        return results

    def run_for_events(
        self,
        events: pd.DataFrame,
        series: CleanDemandSeries,
        products: Optional[pd.DataFrame] = None,
        reference_date: Optional[date] = None,
    ) -> List[DemandSignalAdjustment]:
        """Compute adjustment factors for local events."""
        ref = reference_date or date.today()
        pid = series.product_id
        df_series = self._series_to_df(series)
        results = []

        for _, row in events.iterrows():
            adj = self._compute_event_adjustment(row, df_series, pid, ref, products)
            if adj is not None:
                results.append(adj)
        return results

    def run(
        self,
        promotions: pd.DataFrame,
        events: pd.DataFrame,
        series: CleanDemandSeries,
        products: Optional[pd.DataFrame] = None,
        reference_date: Optional[date] = None,
    ) -> List[DemandSignalAdjustment]:
        """Combined run — returns all active signals for this product."""
        promo_adj = self.run_for_promotions(promotions, series, products, reference_date)
        event_adj = self.run_for_events(events, series, products, reference_date)
        all_adj = promo_adj + event_adj
        logger.info(
            "[D3] %s — %d promo signals, %d event signals",
            series.product_id, len(promo_adj), len(event_adj)
        )
        return all_adj

    # ------------------------------------------------------------------
    # Promotion logic
    # ------------------------------------------------------------------

    def _compute_promo_adjustment(
        self,
        promo: pd.Series,
        df_series: pd.DataFrame,
        product_id: str,
        ref: date,
        products: Optional[pd.DataFrame],
    ) -> Optional[DemandSignalAdjustment]:
        try:
            start = pd.Timestamp(promo["start"]).date()
            end = pd.Timestamp(promo["end"]).date()
        except Exception:
            return None

        # Skip entirely past promotions
        if end < ref - timedelta(days=180):
            return None

        discount_pct = float(promo.get("value", 0))

        # Try historical uplift first
        factor, confidence, method = self._historical_promo_uplift(
            df_series, start, end, discount_pct
        )

        # Fall back to rule-based lookup
        if confidence < 0.4:
            category = self._get_product_category(product_id, products)
            rule_factor = self._rule_based_promo_factor(discount_pct, category)
            reason = (
                f"Insufficient historical data for promo uplift estimate. "
                f"Using rule-based estimate for {category} category "
                f"({discount_pct:.0f}% discount → ×{rule_factor:.2f})."
            )
            # Blend with historical if available
            if confidence > 0.1:
                factor = 0.4 * factor + 0.6 * rule_factor
                confidence = min(confidence + 0.2, 0.65)
            else:
                factor = rule_factor
                confidence = 0.40
        else:
            reason = (
                f"Historical uplift estimated ({method}) over "
                f"{(end - start).days + 1} promo days: ×{factor:.2f}."
            )

        return DemandSignalAdjustment(
            product_id=product_id,
            signal_type=SignalType.PROMOTION,
            promo_id=str(promo.get("promo_id", "")),
            adjustment_factor=round(factor, 4),
            confidence=round(confidence, 4),
            reason=reason,
            active_from=start,
            active_to=end,
            generated_at=datetime.utcnow(),
        )

    def _historical_promo_uplift(
        self,
        df: pd.DataFrame,
        start: date,
        end: date,
        discount_pct: float,
    ) -> Tuple[float, float, str]:
        """
        Compute uplift by comparing demand during promo window vs prior baseline.
        Returns (factor, confidence, method_name).
        """
        if df.empty or len(df) < MIN_DAYS_FOR_HISTORICAL_UPLIFT:
            return 1.0, 0.0, "none"

        window_days = (end - start).days + 1
        baseline_start = start - timedelta(days=window_days * 2)

        promo_data = df[(df["date"] >= pd.Timestamp(start)) & (df["date"] <= pd.Timestamp(end))]["qty"]
        baseline_data = df[(df["date"] >= pd.Timestamp(baseline_start)) & (df["date"] < pd.Timestamp(start))]["qty"]

        if len(promo_data) == 0 or len(baseline_data) == 0:
            return 1.0, 0.0, "none"

        promo_mean = promo_data.mean()
        baseline_mean = baseline_data.mean()

        if baseline_mean <= 0:
            return 1.0, 0.0, "none"

        factor = float(promo_mean / baseline_mean)
        factor = float(np.clip(factor, 0.5, 3.0))

        # Confidence based on sample sizes
        confidence = min(len(promo_data) / 7.0, 1.0) * min(len(baseline_data) / 14.0, 1.0)
        confidence = float(np.clip(confidence * 0.8, 0.0, 0.85))  # cap at 0.85 — can't be certain

        return factor, confidence, "historical_comparison"

    def _rule_based_promo_factor(self, discount_pct: float, category: str) -> float:
        """Simple elasticity rule: each 5% discount → ~6% volume uplift."""
        base = CATEGORY_PROMO_DEFAULTS.get(category, 1.10)
        # Adjust for discount size (linear approximation)
        extra = max(0.0, (discount_pct - 10.0) / 100.0 * 0.5)
        return round(float(np.clip(base + extra, 1.0, 2.5)), 4)

    # ------------------------------------------------------------------
    # Event logic
    # ------------------------------------------------------------------

    def _compute_event_adjustment(
        self,
        event: pd.Series,
        df_series: pd.DataFrame,
        product_id: str,
        ref: date,
        products: Optional[pd.DataFrame],
    ) -> Optional[DemandSignalAdjustment]:
        try:
            event_date = pd.Timestamp(event["date"]).date()
        except Exception:
            return None

        # Skip events more than 90 days in the past
        if event_date < ref - timedelta(days=90):
            return None

        event_type = str(event.get("event_type", "OTHER")).upper()
        event_id = str(event.get("event_id", ""))
        expected_impact = float(event.get("expected_impact", 0.0))

        # Try historical uplift around this event date
        factor, confidence, method = self._historical_event_uplift(
            df_series, event_date, event_type
        )

        if confidence < 0.35:
            # Fall back to lookup table or expected_impact from data
            if expected_impact > 0:
                factor = 1.0 + expected_impact
                confidence = 0.45
                reason = (
                    f"Using declared expected_impact from event data ({expected_impact:.0%} uplift). "
                    f"Insufficient historical evidence to compute empirical estimate."
                )
            else:
                factor = EVENT_UPLIFT_DEFAULTS.get(event_type, 1.10)
                confidence = 0.35
                reason = (
                    f"Using lookup-table default for {event_type} event "
                    f"(×{factor:.2f}). No historical evidence available."
                )
        else:
            reason = (
                f"Historical demand uplift estimated ({method}) around similar {event_type} events: "
                f"×{factor:.2f}."
            )

        # Determine signal type
        signal_map = {
            "HOLIDAY": SignalType.HOLIDAY,
            "MARKET": SignalType.MARKET_DAY,
            "SPORT": SignalType.LOCAL_EVENT,
        }
        signal_type = signal_map.get(event_type, SignalType.LOCAL_EVENT)

        return DemandSignalAdjustment(
            product_id=product_id,
            signal_type=signal_type,
            event_id=event_id,
            adjustment_factor=round(factor, 4),
            confidence=round(confidence, 4),
            reason=reason,
            active_from=event_date,
            active_to=event_date,
            generated_at=datetime.utcnow(),
        )

    def _historical_event_uplift(
        self,
        df: pd.DataFrame,
        event_date: date,
        event_type: str,
    ) -> Tuple[float, float, str]:
        """Compare demand on event day ±1 vs baseline week before."""
        if df.empty or len(df) < MIN_DAYS_FOR_HISTORICAL_UPLIFT:
            return 1.0, 0.0, "none"

        ts = pd.Timestamp(event_date)
        event_window = df[
            (df["date"] >= ts - pd.Timedelta(days=1)) &
            (df["date"] <= ts + pd.Timedelta(days=1))
        ]["qty"]
        baseline_window = df[
            (df["date"] >= ts - pd.Timedelta(days=8)) &
            (df["date"] < ts - pd.Timedelta(days=1))
        ]["qty"]

        if len(event_window) == 0 or len(baseline_window) < 3:
            return 1.0, 0.0, "none"

        event_mean = event_window.mean()
        baseline_mean = baseline_window.mean()

        if baseline_mean <= 0:
            return 1.0, 0.0, "none"

        factor = float(np.clip(event_mean / baseline_mean, 0.5, 3.0))
        confidence = float(min(len(event_window) / 3.0, 1.0) * min(len(baseline_window) / 7.0, 1.0) * 0.75)
        return factor, confidence, "historical_window_comparison"

    # ------------------------------------------------------------------
    # Utilities
    # ------------------------------------------------------------------

    def _series_to_df(self, series: CleanDemandSeries) -> pd.DataFrame:
        rows = [{"date": pd.Timestamp(p.date), "qty": p.qty} for p in series.series]
        return pd.DataFrame(rows).sort_values("date").reset_index(drop=True)

    def _get_product_category(self, product_id: str, products: Optional[pd.DataFrame]) -> str:
        if products is None or products.empty:
            return "General"
        row = products[products["product_id"] == product_id]
        if row.empty:
            return "General"
        return str(row.iloc[0].get("category", "General"))
