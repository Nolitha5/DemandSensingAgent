"""
D5 — Forecast Quality Monitor
================================
Compares forecasts against actual sales.
Detects drift, poor SKU coverage, deteriorating performance.

Metrics: MAE, WAPE, Bias
Trigger: Daily / Weekly
Input:   DemandForecast, actual transactions
Output:  ForecastQualityReport, ForecastQualityAlert (if degraded)
"""
from __future__ import annotations

import logging
import uuid
from datetime import date, datetime
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from app.contracts.demand import (
    DemandForecast,
    ForecastQualityAlert,
    ForecastQualityReport,
    ForecastStatus,
)
from app.core.config import get_settings

logger = logging.getLogger(__name__)
_settings = get_settings()

MIN_ACTUALS = 3    # Minimum actuals needed to compute any metric


class ForecastQualityMonitor:
    """
    Deterministic quality monitor.
    Uses MAE, WAPE, and bias to assess forecast performance.
    Generates alerts when metrics exceed thresholds.
    """

    def __init__(
        self,
        wape_alert_threshold: float = None,
        bias_alert_threshold: float = None,
        drift_threshold: float = None,
    ):
        self.wape_threshold = wape_alert_threshold or _settings.wape_alert_threshold
        self.bias_threshold = bias_alert_threshold or _settings.bias_alert_threshold
        self.drift_threshold = drift_threshold or _settings.forecast_drift_threshold

    def evaluate(
        self,
        forecast: DemandForecast,
        transactions: pd.DataFrame,
        prior_wape: Optional[float] = None,
    ) -> Tuple[ForecastQualityReport, List[ForecastQualityAlert]]:
        """
        Evaluate one forecast against actual sales.

        Parameters
        ----------
        forecast      : the DemandForecast to evaluate
        transactions  : raw transactions DataFrame
        prior_wape    : previous cycle's WAPE (for drift detection)

        Returns
        -------
        (ForecastQualityReport, list of ForecastQualityAlert)
        """
        pid = forecast.product_id
        logger.info("[D5] Evaluating forecast for %s", pid)

        # Collect actuals for the forecast window
        actuals = self._collect_actuals(forecast, transactions)

        if len(actuals) < MIN_ACTUALS:
            report = ForecastQualityReport(
                product_id=pid,
                horizon=forecast.horizon,
                actuals_count=len(actuals),
                coverage_ok=False,
                status=ForecastStatus.INSUFFICIENT_EVIDENCE,
                notes=f"Only {len(actuals)} actuals available — need {MIN_ACTUALS} to evaluate.",
                evaluated_at=datetime.utcnow(),
            )
            alert = self._make_alert(
                pid, "INSUFFICIENT_ACTUALS", "LOW",
                None, None,
                f"Only {len(actuals)} actual observations for product {pid}.",
                "Wait for more sales data before evaluating forecast quality."
            )
            return report, [alert]

        # Align forecast and actuals
        predicted, observed = self._align(forecast, actuals)

        mae = self._mae(observed, predicted)
        wape = self._wape(observed, predicted)
        bias = self._bias(observed, predicted)
        drift_detected = self._detect_drift(wape, prior_wape)
        coverage_ok = len(actuals) >= (forecast.horizon * 0.7)

        # Determine status
        status = ForecastStatus.HEALTHY
        if wape > self.wape_threshold or abs(bias) > self.bias_threshold:
            status = ForecastStatus.DEGRADED
        if wape > self.wape_threshold * 1.5:
            status = ForecastStatus.ALERT

        report = ForecastQualityReport(
            product_id=pid,
            horizon=forecast.horizon,
            mae=round(mae, 4),
            wape=round(wape, 4),
            bias=round(bias, 4),
            drift_detected=drift_detected,
            coverage_ok=coverage_ok,
            actuals_count=len(actuals),
            status=status,
            notes=self._build_notes(mae, wape, bias, drift_detected),
            evaluated_at=datetime.utcnow(),
        )

        alerts = self._generate_alerts(pid, wape, bias, drift_detected, coverage_ok)

        logger.info(
            "[D5] %s — MAE=%.2f, WAPE=%.3f, bias=%.3f, status=%s",
            pid, mae, wape, bias, status
        )
        return report, alerts

    def evaluate_all(
        self,
        forecasts: List[DemandForecast],
        transactions: pd.DataFrame,
        prior_wapes: Optional[Dict[str, float]] = None,
    ) -> Tuple[List[ForecastQualityReport], List[ForecastQualityAlert]]:
        prior_wapes = prior_wapes or {}
        all_reports = []
        all_alerts = []
        for fc in forecasts:
            report, alerts = self.evaluate(fc, transactions, prior_wapes.get(fc.product_id))
            all_reports.append(report)
            all_alerts.extend(alerts)
        return all_reports, all_alerts

    # ------------------------------------------------------------------
    # Metrics
    # ------------------------------------------------------------------

    @staticmethod
    def _mae(actual: np.ndarray, predicted: np.ndarray) -> float:
        return float(np.mean(np.abs(actual - predicted)))

    @staticmethod
    def _wape(actual: np.ndarray, predicted: np.ndarray) -> float:
        denom = np.sum(np.abs(actual))
        if denom == 0:
            return 0.0
        return float(np.sum(np.abs(actual - predicted)) / denom)

    @staticmethod
    def _bias(actual: np.ndarray, predicted: np.ndarray) -> float:
        """Positive bias → over-forecasting; negative → under-forecasting."""
        if len(actual) == 0:
            return 0.0
        return float(np.mean(predicted - actual) / max(np.mean(actual), 1e-6))

    def _detect_drift(self, current_wape: float, prior_wape: Optional[float]) -> bool:
        if prior_wape is None or prior_wape == 0:
            return False
        change = (current_wape - prior_wape) / prior_wape
        return change > self.drift_threshold

    # ------------------------------------------------------------------
    # Data helpers
    # ------------------------------------------------------------------

    def _collect_actuals(
        self, forecast: DemandForecast, transactions: pd.DataFrame
    ) -> pd.DataFrame:
        """Aggregate actual sales per day for the forecast window."""
        if transactions.empty or not forecast.forecast_days:
            return pd.DataFrame(columns=["date", "qty"])

        forecast_dates = {fd.date for fd in forecast.forecast_days}
        tx = transactions[transactions["product_id"] == forecast.product_id].copy()
        tx["date"] = pd.to_datetime(tx["timestamp"]).dt.date
        tx = tx[tx["date"].isin(forecast_dates)]
        tx = tx[tx["qty"].notna() & (tx["qty"] >= 0)]

        daily = tx.groupby("date")["qty"].sum().reset_index()
        return daily

    def _align(
        self, forecast: DemandForecast, actuals: pd.DataFrame
    ) -> Tuple[np.ndarray, np.ndarray]:
        """Return (predicted, observed) arrays aligned on overlapping dates."""
        fc_map = {fd.date: fd.expected_qty for fd in forecast.forecast_days}
        predicted_list = []
        observed_list = []
        for _, row in actuals.iterrows():
            d = row["date"] if isinstance(row["date"], date) else row["date"].date()
            if d in fc_map:
                predicted_list.append(fc_map[d])
                observed_list.append(float(row["qty"]))
        return np.array(predicted_list), np.array(observed_list)

    # ------------------------------------------------------------------
    # Alert generation
    # ------------------------------------------------------------------

    def _generate_alerts(
        self,
        pid: str,
        wape: float,
        bias: float,
        drift: bool,
        coverage_ok: bool,
    ) -> List[ForecastQualityAlert]:
        alerts = []
        if wape > self.wape_threshold:
            severity = "HIGH" if wape > self.wape_threshold * 1.5 else "MEDIUM"
            alerts.append(self._make_alert(
                pid, "HIGH_WAPE", severity, wape, self.wape_threshold,
                f"WAPE {wape:.1%} exceeds threshold {self.wape_threshold:.1%} for {pid}.",
                "Review forecast model — consider re-running D1 with fresher data or adjusting D2 seasonality."
            ))
        if abs(bias) > self.bias_threshold:
            direction = "over-forecasting" if bias > 0 else "under-forecasting"
            alerts.append(self._make_alert(
                pid, "BIAS_DRIFT", "MEDIUM", bias, self.bias_threshold,
                f"Forecast bias {bias:+.1%} indicates systematic {direction} for {pid}.",
                "Inspect promotion/event signals in D3 and seasonality profile in D2."
            ))
        if drift:
            alerts.append(self._make_alert(
                pid, "WAPE_DRIFT", "MEDIUM", wape, None,
                f"WAPE for {pid} increased by more than {self.drift_threshold:.0%} vs prior cycle.",
                "Check for data quality issues or structural demand shifts."
            ))
        if not coverage_ok:
            alerts.append(self._make_alert(
                pid, "POOR_COVERAGE", "LOW", None, None,
                f"Fewer than 70% of forecast days have matching actuals for {pid}.",
                "Verify that transaction data is being imported correctly."
            ))
        return alerts

    @staticmethod
    def _make_alert(
        pid: str, alert_type: str, severity: str,
        metric_value: Optional[float], threshold: Optional[float],
        message: str, recommended_action: str,
    ) -> ForecastQualityAlert:
        return ForecastQualityAlert(
            alert_id=str(uuid.uuid4()),
            product_id=pid,
            alert_type=alert_type,
            severity=severity,
            metric_value=metric_value,
            threshold=threshold,
            message=message,
            recommended_action=recommended_action,
            generated_at=datetime.utcnow(),
        )

    @staticmethod
    def _build_notes(mae: float, wape: float, bias: float, drift: bool) -> str:
        parts = [f"MAE={mae:.2f}", f"WAPE={wape:.1%}", f"bias={bias:+.1%}"]
        if drift:
            parts.append("DRIFT DETECTED")
        return "; ".join(parts)
