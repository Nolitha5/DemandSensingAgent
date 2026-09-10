"""
D2 — Seasonality Detector
===========================
Responsibilities:
  - Detect day-of-week patterns
  - Detect payday uplift
  - Detect monthly patterns
  - Detect holiday effects
  - Use autocorrelation / STL / seasonal indices
  - Do NOT claim seasonality when evidence is weak

Trigger: Weekly (or after D1 completes)
Input:   CleanDemandSeries + calendar DataFrame
Output:  SeasonalityProfile (consumed by D4)
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
from statsmodels.tsa.stattools import acf

from app.contracts.demand import CleanDemandSeries, SeasonalityProfile
from app.core.config import get_settings

logger = logging.getLogger(__name__)
_settings = get_settings()

# Thresholds
MIN_OBS_FOR_WEEKLY = _settings.min_observations_for_seasonality   # 28 days
MIN_OBS_FOR_STL = _settings.min_observations_for_stl              # 60 days
PAYDAY_WINDOWS = [(13, 16), (27, 31)]   # mid-month & month-end payday windows
SIGNIFICANCE_THRESHOLD = 0.15          # index must deviate > 15% from 1.0 to be "detected"


class SeasonalityDetector:
    """
    Detects seasonality patterns in clean demand series.
    Uses seasonal indices (primary), autocorrelation, and STL where sufficient data exists.
    All deterministic — no LLM.
    """

    def __init__(self, calendar: Optional[pd.DataFrame] = None):
        """
        Parameters
        ----------
        calendar : optional DataFrame with columns [date, is_weekend, is_payday_window, day_name]
        """
        self.calendar = calendar

    def run(self, series: CleanDemandSeries) -> SeasonalityProfile:
        logger.info("[D2] Running for product %s (%d obs)", series.product_id, series.observations)

        df = self._series_to_df(series)
        n = len(df)

        if n < MIN_OBS_FOR_WEEKLY:
            logger.warning(
                "[D2] %s — insufficient data (%d obs < %d). Returning flat profile.",
                series.product_id, n, MIN_OBS_FOR_WEEKLY
            )
            return SeasonalityProfile(
                product_id=series.product_id,
                data_sufficient=False,
                min_observations_used=n,
                generated_at=datetime.utcnow(),
            )

        # Merge calendar features
        if self.calendar is not None:
            df = self._merge_calendar(df)
        else:
            df = self._derive_calendar_features(df)

        # 1. Weekly pattern (seasonal indices)
        weekly_pattern, weekly_strength = self._compute_weekly_pattern(df)

        # 2. Payday effect
        payday_effect, payday_detected = self._compute_payday_effect(df)

        # 3. Monthly pattern (only if >= 60 days)
        monthly_pattern = {}
        if n >= MIN_OBS_FOR_STL:
            monthly_pattern = self._compute_monthly_pattern(df)

        # 4. Holiday effect
        holiday_effect, holiday_detected = self._compute_holiday_effect(df)

        # 5. Autocorrelation check for weekly period (lag=7)
        autocorr_7 = self._check_autocorr(df["qty"], lag=7) if n >= 14 else 0.0

        # 6. Seasonality strength & confidence
        seasonality_strength = self._compute_strength(weekly_strength, autocorr_7)
        confidence = self._compute_confidence(n, seasonality_strength)

        # 7. Detected patterns list
        detected_patterns = self._build_patterns(
            weekly_pattern, payday_detected, holiday_detected, monthly_pattern, autocorr_7
        )

        profile = SeasonalityProfile(
            product_id=series.product_id,
            weekly_pattern=weekly_pattern,
            payday_effect=round(payday_effect, 4),
            monthly_pattern={str(k): round(v, 4) for k, v in monthly_pattern.items()},
            holiday_effect=round(holiday_effect, 4),
            seasonality_strength=round(seasonality_strength, 4),
            confidence=round(confidence, 4),
            detected_patterns=detected_patterns,
            data_sufficient=True,
            min_observations_used=n,
            generated_at=datetime.utcnow(),
        )

        logger.info(
            "[D2] %s — strength=%.2f, confidence=%.2f, patterns=%s",
            series.product_id, seasonality_strength, confidence, detected_patterns
        )
        return profile

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _series_to_df(self, series: CleanDemandSeries) -> pd.DataFrame:
        rows = [{"date": p.date, "qty": p.qty} for p in series.series]
        df = pd.DataFrame(rows)
        df["date"] = pd.to_datetime(df["date"])
        df = df.sort_values("date").reset_index(drop=True)
        return df

    def _derive_calendar_features(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        df["day_of_week"] = df["date"].dt.dayofweek   # 0=Mon
        df["day_name"] = df["date"].dt.day_name()
        df["day_of_month"] = df["date"].dt.day
        df["month"] = df["date"].dt.month
        df["is_weekend"] = df["day_of_week"].isin([5, 6])
        df["is_payday"] = df["day_of_month"].apply(
            lambda d: any(lo <= d <= hi for lo, hi in PAYDAY_WINDOWS)
        )
        df["is_holiday"] = False  # No holidays in basic calendar
        return df

    def _merge_calendar(self, df: pd.DataFrame) -> pd.DataFrame:
        cal = self.calendar.copy()
        cal["date"] = pd.to_datetime(cal["date"])
        cal = cal.rename(columns={"is_payday_window": "is_payday"})
        df = df.merge(
            cal[["date", "day_name", "is_weekend", "is_payday"]],
            on="date", how="left"
        )
        df["day_of_week"] = df["date"].dt.dayofweek
        df["day_of_month"] = df["date"].dt.day
        df["month"] = df["date"].dt.month
        df["is_holiday"] = False
        return df

    def _compute_weekly_pattern(self, df: pd.DataFrame) -> Tuple[Dict[str, float], float]:
        """Compute seasonal index per day of week."""
        grand_mean = df["qty"].mean()
        if grand_mean == 0:
            days = ["Monday","Tuesday","Wednesday","Thursday","Friday","Saturday","Sunday"]
            return {d: 1.0 for d in days}, 0.0

        day_means = df.groupby("day_name")["qty"].mean()
        days_order = ["Monday","Tuesday","Wednesday","Thursday","Friday","Saturday","Sunday"]
        pattern: Dict[str, float] = {}
        for day in days_order:
            if day in day_means:
                idx = round(float(day_means[day] / grand_mean), 4)
            else:
                idx = 1.0
            pattern[day] = idx

        # Strength = coefficient of variation of indices
        indices = list(pattern.values())
        strength = float(np.std(indices) / np.mean(indices)) if np.mean(indices) > 0 else 0.0
        strength = min(strength, 1.0)
        return pattern, strength

    def _compute_payday_effect(self, df: pd.DataFrame) -> Tuple[float, bool]:
        """Compare demand on payday windows vs non-payday."""
        if "is_payday" not in df.columns:
            return 1.0, False
        payday_mean = df[df["is_payday"] == True]["qty"].mean()
        non_payday_mean = df[df["is_payday"] == False]["qty"].mean()
        if non_payday_mean and non_payday_mean > 0 and not np.isnan(payday_mean):
            effect = float(payday_mean / non_payday_mean)
            detected = abs(effect - 1.0) > SIGNIFICANCE_THRESHOLD
            return round(effect, 4), detected
        return 1.0, False

    def _compute_monthly_pattern(self, df: pd.DataFrame) -> Dict[int, float]:
        grand_mean = df["qty"].mean()
        if grand_mean == 0:
            return {}
        month_means = df.groupby("month")["qty"].mean()
        return {int(m): round(float(v / grand_mean), 4) for m, v in month_means.items()}

    def _compute_holiday_effect(self, df: pd.DataFrame) -> Tuple[float, bool]:
        """Holiday effect (placeholder — requires event data in calendar)."""
        if "is_holiday" not in df.columns or df["is_holiday"].sum() == 0:
            return 1.0, False
        holiday_mean = df[df["is_holiday"]]["qty"].mean()
        non_holiday_mean = df[~df["is_holiday"]]["qty"].mean()
        if non_holiday_mean and non_holiday_mean > 0:
            effect = float(holiday_mean / non_holiday_mean)
            return round(effect, 4), abs(effect - 1.0) > SIGNIFICANCE_THRESHOLD
        return 1.0, False

    def _check_autocorr(self, qty: pd.Series, lag: int = 7) -> float:
        """Return autocorrelation at given lag."""
        try:
            if len(qty) < lag * 2:
                return 0.0
            ac = acf(qty, nlags=lag, fft=True)
            return float(ac[lag])
        except Exception:
            return 0.0

    def _compute_strength(self, weekly_strength: float, autocorr_7: float) -> float:
        """Combine weekly CoV and lag-7 autocorrelation into a single strength metric."""
        return float(np.clip(0.6 * weekly_strength + 0.4 * max(autocorr_7, 0), 0, 1))

    def _compute_confidence(self, n_obs: int, strength: float) -> float:
        """Confidence grows with data length and pattern strength."""
        data_factor = min(n_obs / 180.0, 1.0)   # saturates at 180 days
        return float(np.clip(0.5 * data_factor + 0.5 * strength, 0, 1))

    def _build_patterns(
        self,
        weekly: Dict[str, float],
        payday_detected: bool,
        holiday_detected: bool,
        monthly: Dict,
        autocorr_7: float,
    ) -> List[str]:
        patterns = []
        # Weekend uplift: Saturday or Sunday index > 1.1
        if weekly.get("Saturday", 1.0) > 1.1 or weekly.get("Sunday", 1.0) > 1.1:
            patterns.append("weekend_uplift")
        # Weekday peak
        weekday_indices = [weekly.get(d, 1.0) for d in ["Monday","Tuesday","Wednesday","Thursday","Friday"]]
        if max(weekday_indices) > 1.15:
            patterns.append("weekday_peak")
        if payday_detected:
            patterns.append("payday_uplift")
        if holiday_detected:
            patterns.append("holiday_effect")
        if len(monthly) > 1:
            monthly_cv = np.std(list(monthly.values())) / max(np.mean(list(monthly.values())), 0.001)
            if monthly_cv > 0.05:
                patterns.append("monthly_variation")
        if autocorr_7 > 0.3:
            patterns.append("weekly_autocorrelation")
        return patterns
