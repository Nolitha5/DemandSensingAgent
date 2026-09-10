"""
D4 — Forecast Generator
========================
Produces short-term quantity forecasts and confidence bands.
Consumes outputs from D1, D2, D3.

Forecast horizon: 7 days (default), 14, 30 supported.
Model selection: via backtesting (WAPE/MAE).
Confidence: built from evidence, not invented.

Trigger: Daily
Input:   CleanDemandSeries, SeasonalityProfile, List[DemandSignalAdjustment]
Output:  DemandForecast (STABLE CONTRACT for Inventory/Pricing/Procurement)
"""
from __future__ import annotations

import logging
import uuid
from datetime import date, datetime, timedelta
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from app.contracts.demand import (
    CleanDemandSeries,
    DemandForecast,
    DemandSignalAdjustment,
    ForecastDay,
    ForecastStatus,
    InsufficientEvidenceError,
    ModelName,
    SeasonalityProfile,
)
from app.core.config import get_settings
from .model_selector import ForecastModelSelector

logger = logging.getLogger(__name__)
_settings = get_settings()

MIN_OBSERVATIONS = 7              # Absolute minimum to produce any forecast
INSUFFICIENT_CONFIDENCE = 0.20   # Below this → mark INSUFFICIENT_EVIDENCE


class ForecastGenerator:
    """
    Core forecasting component.
    Selects the best model via backtesting and applies seasonal/event adjustments.
    Confidence is computed from measurable evidence only.
    """

    def __init__(self):
        self._selector = ForecastModelSelector()

    def run(
        self,
        series: CleanDemandSeries,
        seasonality: Optional[SeasonalityProfile] = None,
        signals: Optional[List[DemandSignalAdjustment]] = None,
        horizon: int = 7,
        reference_date: Optional[date] = None,
    ) -> DemandForecast:
        """
        Produce a DemandForecast for one product.

        Returns DemandForecast with status=INSUFFICIENT_EVIDENCE if data is too sparse,
        rather than raising an exception — so the API can still return a typed response.
        """
        logger.info("[D4] Forecasting %s, horizon=%d days", series.product_id, horizon)

        pid = series.product_id
        ref = reference_date or date.today()
        signals = signals or []
        # Build qty array; strip trailing interpolated zeros so the naive model
        # doesn't anchor on a 0-filled "today" or "yesterday" with no actual sales.
        raw_points = series.series
        while raw_points and raw_points[-1].is_interpolated and raw_points[-1].qty == 0:
            raw_points = raw_points[:-1]
        qty = np.array([p.qty for p in raw_points]) if raw_points else np.array([p.qty for p in series.series])
        n = len(qty)

        # ── Insufficient evidence guard ──────────────────────────────────
        if n < MIN_OBSERVATIONS:
            return self._insufficient_evidence(
                pid, horizon, ref,
                f"Only {n} observations — need at least {MIN_OBSERVATIONS}."
            )

        # ── Model selection ──────────────────────────────────────────────
        weekly_pattern = seasonality.weekly_pattern if seasonality else None
        best_model, backtest_wape, backtest_mae = self._selector.select(
            qty, horizon, weekly_pattern
        )

        # ── Produce raw forecast ─────────────────────────────────────────
        raw_forecast = self._selector.produce_forecast(qty, best_model, horizon, weekly_pattern)

        # ── Apply seasonality adjustments ────────────────────────────────
        forecast_dates = [ref + timedelta(days=i + 1) for i in range(horizon)]
        adjusted = self._apply_seasonality(raw_forecast, forecast_dates, seasonality)

        # ── Apply event/promo signals ────────────────────────────────────
        adjusted, active_signals = self._apply_signals(adjusted, forecast_dates, signals)

        # ── Compute confidence ───────────────────────────────────────────
        confidence = self._compute_confidence(
            n_obs=n,
            backtest_wape=backtest_wape,
            seasonality_confidence=seasonality.confidence if seasonality else 0.0,
            signal_count=len(active_signals),
            series=series,
        )

        # ── Confidence bands ─────────────────────────────────────────────
        lower, upper = self._compute_bands(adjusted, backtest_wape, confidence)

        # ── Build per-day forecast ───────────────────────────────────────
        forecast_days = [
            ForecastDay(
                date=d,
                expected_qty=max(0.0, round(float(q), 2)),
                lower_bound=max(0.0, round(float(l), 2)),
                upper_bound=max(0.0, round(float(u), 2)),
            )
            for d, q, l, u in zip(forecast_dates, adjusted, lower, upper)
        ]

        total_qty = sum(fd.expected_qty for fd in forecast_days)
        total_lower = sum(fd.lower_bound for fd in forecast_days)
        total_upper = sum(fd.upper_bound for fd in forecast_days)

        # ── Drivers ─────────────────────────────────────────────────────
        drivers = self._build_drivers(seasonality, active_signals)

        # ── Status ──────────────────────────────────────────────────────
        status = ForecastStatus.HEALTHY
        insufficient = False
        evidence_notes = ""

        if confidence < INSUFFICIENT_CONFIDENCE:
            status = ForecastStatus.INSUFFICIENT_EVIDENCE
            insufficient = True
            evidence_notes = (
                f"Confidence {confidence:.2f} is below threshold {INSUFFICIENT_CONFIDENCE}. "
                f"Forecast is low-confidence (n={n} obs, WAPE={backtest_wape:.2f})."
            )
        elif confidence < 0.50:
            status = ForecastStatus.DEGRADED
            evidence_notes = f"Limited data ({n} obs) — treat forecast with caution."

        # ── Data freshness ───────────────────────────────────────────────
        if series.series:
            last_date = max(p.date for p in series.series)
            freshness_days = (ref - last_date).days
        else:
            freshness_days = None

        forecast = DemandForecast(
            product_id=pid,
            horizon=horizon,
            expected_qty=round(total_qty, 2),
            lower_bound=round(total_lower, 2),
            upper_bound=round(total_upper, 2),
            confidence=round(confidence, 4),
            drivers=drivers,
            model=best_model,
            forecast_days=forecast_days,
            status=status,
            insufficient_evidence=insufficient,
            evidence_notes=evidence_notes,
            generated_at=datetime.utcnow(),
            data_freshness_days=freshness_days,
        )

        logger.info(
            "[D4] %s — model=%s, total_qty=%.1f, confidence=%.2f, status=%s",
            pid, best_model, total_qty, confidence, status
        )
        return forecast

    # ------------------------------------------------------------------
    # Adjustments
    # ------------------------------------------------------------------

    def _apply_seasonality(
        self,
        raw: np.ndarray,
        dates: List[date],
        seasonality: Optional[SeasonalityProfile],
    ) -> np.ndarray:
        if seasonality is None or not seasonality.data_sufficient:
            return raw.copy()

        adjusted = raw.copy()
        for i, d in enumerate(dates):
            day_name = d.strftime("%A")
            dow_idx = seasonality.weekly_pattern.get(day_name, 1.0)
            # Also apply payday effect
            dom = d.day
            is_payday = any(lo <= dom <= hi for lo, hi in [(13, 16), (27, 31)])
            payday_idx = seasonality.payday_effect if is_payday else 1.0
            adjusted[i] = adjusted[i] * dow_idx * payday_idx

        return adjusted

    def _apply_signals(
        self,
        forecast: np.ndarray,
        dates: List[date],
        signals: List[DemandSignalAdjustment],
    ):
        """Apply each active signal's adjustment_factor to relevant forecast days."""
        adjusted = forecast.copy()
        active = []

        for sig in signals:
            if sig.confidence < 0.30:
                continue  # Skip very low-confidence signals
            active_from = sig.active_from or date.min
            active_to = sig.active_to or date.max
            for i, d in enumerate(dates):
                if active_from <= d <= active_to:
                    adjusted[i] = adjusted[i] * sig.adjustment_factor
                    if sig not in active:
                        active.append(sig)

        return adjusted, active

    def _compute_confidence(
        self,
        n_obs: int,
        backtest_wape: float,
        seasonality_confidence: float,
        signal_count: int,
        series: CleanDemandSeries,
    ) -> float:
        """Build confidence from measurable evidence."""
        # Data volume factor (saturates at 180 days)
        data_factor = min(n_obs / 180.0, 1.0)

        # Model quality factor (lower WAPE → higher confidence)
        if backtest_wape == np.inf or np.isnan(backtest_wape):
            model_factor = 0.0
        else:
            model_factor = max(0.0, 1.0 - backtest_wape)

        # Pattern confidence from D2
        pattern_factor = min(seasonality_confidence, 1.0)

        # Penalise for outliers / missing data
        outlier_penalty = series.outliers_detected / max(n_obs, 1) * 0.5
        missing_penalty = series.missing_dates / max(n_obs, 1) * 0.3

        raw = (
            0.35 * data_factor
            + 0.40 * model_factor
            + 0.15 * pattern_factor
            + 0.10 * min(signal_count / 3.0, 1.0)
            - outlier_penalty
            - missing_penalty
        )
        return float(np.clip(raw, 0.0, 0.95))

    def _compute_bands(
        self,
        forecast: np.ndarray,
        backtest_wape: float,
        confidence: float,
    ):
        """Compute lower and upper confidence bands."""
        if backtest_wape == np.inf or np.isnan(backtest_wape):
            error_margin = 0.50
        else:
            error_margin = max(backtest_wape, 0.10)

        # Wider bands when confidence is low
        spread = error_margin * (1.0 + (1.0 - confidence))
        lower = np.maximum(0.0, forecast * (1.0 - spread))
        upper = forecast * (1.0 + spread)
        return lower, upper

    def _build_drivers(
        self,
        seasonality: Optional[SeasonalityProfile],
        active_signals: List[DemandSignalAdjustment],
    ) -> List[str]:
        drivers = []
        if seasonality and seasonality.data_sufficient:
            for p in seasonality.detected_patterns:
                label_map = {
                    "weekend_uplift": "Weekend uplift",
                    "payday_uplift": "Payday effect",
                    "monthly_variation": "Monthly variation",
                    "holiday_effect": "Holiday effect",
                    "weekday_peak": "Weekday peak",
                    "weekly_autocorrelation": "Weekly pattern",
                }
                drivers.append(label_map.get(p, p))
        for sig in active_signals:
            from app.contracts.demand import SignalType
            label_map = {
                SignalType.PROMOTION: "Active promotion",
                SignalType.LOCAL_EVENT: "Local event",
                SignalType.HOLIDAY: "Public holiday",
                SignalType.MARKET_DAY: "Market day",
            }
            drivers.append(label_map.get(sig.signal_type, str(sig.signal_type)))
        return list(dict.fromkeys(drivers))  # deduplicate, preserve order

    def _insufficient_evidence(
        self, pid: str, horizon: int, ref: date, reason: str
    ) -> DemandForecast:
        return DemandForecast(
            product_id=pid,
            horizon=horizon,
            expected_qty=0.0,
            lower_bound=0.0,
            upper_bound=0.0,
            confidence=0.0,
            drivers=[],
            model=ModelName.INSUFFICIENT_EVIDENCE,
            forecast_days=[],
            status=ForecastStatus.INSUFFICIENT_EVIDENCE,
            insufficient_evidence=True,
            evidence_notes=reason,
            generated_at=datetime.utcnow(),
        )
