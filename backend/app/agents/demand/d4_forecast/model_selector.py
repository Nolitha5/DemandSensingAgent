"""
Forecast Model Selector
========================
Selects the best forecasting model via backtesting.
Compares candidates against naive baseline using WAPE and MAE.
The selected model must not be materially worse than Seasonal Naive.

Model hierarchy (SMME-appropriate, data-volume gated):
  ≥ 42 obs → Regression, Holt-Winters, Exp Smoothing, Seasonal Naive, Naive
  ≥ 28 obs → Exp Smoothing, Seasonal Naive, Naive
  ≥ 14 obs → Seasonal Naive, Rolling Mean, Naive
  < 14 obs → Naive only (or INSUFFICIENT_EVIDENCE)
"""
from __future__ import annotations

import logging
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
from statsmodels.tsa.holtwinters import ExponentialSmoothing

from app.contracts.demand import ModelName
from app.core.config import get_settings

logger = logging.getLogger(__name__)
_settings = get_settings()

BACKTEST_WINDOW = 7          # Use last 7 days as holdout
MIN_TRAIN = 14               # Minimum training days
WAPE_MATERIALLY_WORSE = 0.10  # 10% worse WAPE → reject over simpler model


def _wape(actual: np.ndarray, predicted: np.ndarray) -> float:
    denom = np.sum(np.abs(actual))
    if denom == 0:
        return 0.0
    return float(np.sum(np.abs(actual - predicted)) / denom)


def _mae(actual: np.ndarray, predicted: np.ndarray) -> float:
    return float(np.mean(np.abs(actual - predicted)))


class ForecastModelSelector:
    """
    Selects the best model via backtesting on held-out window.
    All methods are deterministic / classical ML.
    """

    def select(
        self,
        qty: np.ndarray,
        horizon: int = 7,
        weekly_pattern: Optional[Dict[str, float]] = None,
    ) -> Tuple[ModelName, float, float]:
        """
        Returns: (model_name, wape, mae) for the winning model.
        """
        n = len(qty)

        if n < MIN_TRAIN:
            return ModelName.NAIVE, np.inf, np.inf

        # Split into train / test
        test = qty[-BACKTEST_WINDOW:]
        train = qty[:-BACKTEST_WINDOW]

        if len(train) < 7:
            return ModelName.NAIVE, np.inf, np.inf

        candidates: List[Tuple[ModelName, float, float]] = []

        # --- Naive ---
        naive_pred = np.full(BACKTEST_WINDOW, float(train[-1]))
        candidates.append((ModelName.NAIVE, _wape(test, naive_pred), _mae(test, naive_pred)))

        # --- Rolling mean ---
        roll_mean = float(np.mean(train[-7:]))
        roll_pred = np.full(BACKTEST_WINDOW, roll_mean)
        candidates.append((ModelName.ROLLING_MEAN, _wape(test, roll_pred), _mae(test, roll_pred)))

        # --- Seasonal Naive (lag-7) ---
        if len(train) >= 14:
            sn_pred = self._seasonal_naive_forecast(train, BACKTEST_WINDOW, weekly_pattern)
            candidates.append((ModelName.SEASONAL_NAIVE, _wape(test, sn_pred), _mae(test, sn_pred)))

        # --- Simple Exponential Smoothing ---
        if len(train) >= 28:
            try:
                es_pred = self._exp_smoothing_forecast(train, BACKTEST_WINDOW)
                candidates.append((ModelName.EXPONENTIAL_SMOOTHING, _wape(test, es_pred), _mae(test, es_pred)))
            except Exception as e:
                logger.debug("ETS failed: %s", e)

        # --- Holt-Winters ---
        if len(train) >= _settings.min_observations_for_stl:
            try:
                hw_pred = self._holt_winters_forecast(train, BACKTEST_WINDOW)
                candidates.append((ModelName.HOLT_WINTERS, _wape(test, hw_pred), _mae(test, hw_pred)))
            except Exception as e:
                logger.debug("HW failed: %s", e)

        # Pick best by WAPE
        candidates.sort(key=lambda x: x[1])
        best_model, best_wape, best_mae = candidates[0]

        # Sanity check: don't accept a model that's materially worse than seasonal naive
        if best_model not in (ModelName.NAIVE, ModelName.ROLLING_MEAN, ModelName.SEASONAL_NAIVE):
            sn_result = next((c for c in candidates if c[0] == ModelName.SEASONAL_NAIVE), None)
            if sn_result and (best_wape - sn_result[1]) > WAPE_MATERIALLY_WORSE:
                best_model, best_wape, best_mae = sn_result

        logger.debug("Model selected: %s (WAPE=%.3f, MAE=%.2f)", best_model, best_wape, best_mae)
        return best_model, best_wape, best_mae

    # ------------------------------------------------------------------
    # Individual model forecasters (return arrays of length `horizon`)
    # ------------------------------------------------------------------

    def seasonal_naive_forecast(
        self, qty: np.ndarray, horizon: int,
        weekly_pattern: Optional[Dict[str, float]] = None
    ) -> np.ndarray:
        return self._seasonal_naive_forecast(qty, horizon, weekly_pattern)

    def _seasonal_naive_forecast(
        self, train: np.ndarray, horizon: int,
        weekly_pattern: Optional[Dict[str, float]] = None,
    ) -> np.ndarray:
        """Repeat the last 7-day block, scaled by weekly pattern if available."""
        if len(train) < 7:
            return np.full(horizon, float(np.mean(train)))
        last_week = train[-7:]
        result = np.tile(last_week, (horizon // 7) + 1)[:horizon]
        return result.astype(float)

    def _exp_smoothing_forecast(self, train: np.ndarray, horizon: int) -> np.ndarray:
        """Simple Exponential Smoothing (Holt)."""
        model = ExponentialSmoothing(
            train.astype(float), trend=None, seasonal=None, initialization_method="estimated"
        )
        fit = model.fit(optimized=True, disp=False)
        return fit.forecast(horizon)

    def _holt_winters_forecast(self, train: np.ndarray, horizon: int) -> np.ndarray:
        """Holt-Winters with additive seasonality (weekly = 7 periods)."""
        model = ExponentialSmoothing(
            train.astype(float),
            trend="add",
            seasonal="add",
            seasonal_periods=7,
            initialization_method="estimated",
        )
        fit = model.fit(optimized=True, disp=False)
        return fit.forecast(horizon)

    def produce_forecast(
        self,
        qty: np.ndarray,
        model: ModelName,
        horizon: int,
        weekly_pattern: Optional[Dict[str, float]] = None,
    ) -> np.ndarray:
        """Produce the actual forecast array for the selected model."""
        if model == ModelName.NAIVE:
            return np.full(horizon, float(qty[-1]))
        elif model == ModelName.ROLLING_MEAN:
            return np.full(horizon, float(np.mean(qty[-7:])))
        elif model == ModelName.SEASONAL_NAIVE:
            return self._seasonal_naive_forecast(qty, horizon, weekly_pattern)
        elif model == ModelName.EXPONENTIAL_SMOOTHING:
            try:
                return self._exp_smoothing_forecast(qty, horizon)
            except Exception:
                return self._seasonal_naive_forecast(qty, horizon, weekly_pattern)
        elif model == ModelName.HOLT_WINTERS:
            try:
                return self._holt_winters_forecast(qty, horizon)
            except Exception:
                return self._seasonal_naive_forecast(qty, horizon, weekly_pattern)
        else:
            return np.full(horizon, float(np.mean(qty[-14:])))
