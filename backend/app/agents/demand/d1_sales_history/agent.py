"""
D1 — Sales History Analyzer
============================
Responsibilities:
  1. Load and validate transaction schema
  2. Remove / isolate invalid records (negative qty, missing qty, duplicates)
  3. Detect extreme outliers (IQR + Z-score)
  4. Aggregate to daily demand per product
  5. Fill missing dates with zero / interpolation
  6. Produce CleanDemandSeries + DemandDiagnostics

Trigger: Daily (or on new transaction import)
Output:  CleanDemandSeries (consumed by D2, D3, D4)
"""
from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from app.contracts.demand import (
    CleanDemandSeries,
    DemandDiagnostics,
    DemandPoint,
    InsufficientEvidenceError,
)
from app.core.config import get_settings

logger = logging.getLogger(__name__)
_settings = get_settings()

# Minimum observations required to produce a series (not INSUFFICIENT_EVIDENCE).
MIN_OBSERVATIONS = 7


class SalesHistoryAnalyzer:
    """
    Deterministic data cleaner and demand series builder.
    No LLM is used — all decisions are rule-based.
    """

    def __init__(
        self,
        iqr_multiplier: float = None,
        zscore_threshold: float = None,
    ):
        self.iqr_multiplier = iqr_multiplier or _settings.outlier_iqr_multiplier
        self.zscore_threshold = zscore_threshold or _settings.outlier_zscore_threshold

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    def run(
        self,
        transactions: pd.DataFrame,
        product_id: str,
        reference_date: Optional[date] = None,
    ) -> CleanDemandSeries:
        """
        Produce a CleanDemandSeries for one product.

        Parameters
        ----------
        transactions : raw transaction DataFrame
        product_id   : the product to analyse
        reference_date : last date to include (defaults to max date in data)

        Raises
        ------
        InsufficientEvidenceError if fewer than MIN_OBSERVATIONS clean records remain.
        """
        logger.info("[D1] Running for product %s", product_id)

        # Step 1 — schema validation & product filter
        df, schema_warnings = self._validate_and_filter(transactions, product_id)

        raw_count = len(df)

        # Step 2 — remove invalid records
        df, neg_count, miss_count = self._remove_invalid(df)

        # Step 3 — remove duplicates
        df, dup_count = self._remove_duplicates(df)

        # Step 4 — aggregate to daily
        daily = self._aggregate_daily(df)

        # Step 5 — fill date range
        if len(daily) == 0:
            raise InsufficientEvidenceError(
                "D1", product_id,
                f"No valid transactions remain after cleaning (raw={raw_count})."
            )

        ref = reference_date or daily.index.max().date()
        start = daily.index.min().date()
        daily_full, missing_count = self._fill_date_range(daily, start, ref)

        # Step 6 — detect outliers (on filled series)
        daily_full, outlier_count, outlier_flags = self._detect_outliers(daily_full)

        # Step 7 — build output series
        series_points = self._build_series(daily_full, outlier_flags)

        clean_obs = len([p for p in series_points if not p.is_outlier and not p.is_interpolated])

        if clean_obs < MIN_OBSERVATIONS:
            raise InsufficientEvidenceError(
                "D1", product_id,
                f"Only {clean_obs} clean observations — need at least {MIN_OBSERVATIONS}."
            )

        qty_array = np.array([p.qty for p in series_points])
        mean_demand = float(np.mean(qty_array))
        std_demand = float(np.std(qty_array))

        total_obs = len(series_points)
        coverage = clean_obs / max(total_obs, 1)

        diagnostics = DemandDiagnostics(
            product_id=product_id,
            raw_records=raw_count,
            invalid_negative=neg_count,
            invalid_missing_qty=miss_count,
            duplicates_removed=dup_count,
            outliers_flagged=outlier_count,
            missing_dates_filled=missing_count,
            coverage_pct=round(coverage, 4),
            date_range_start=start,
            date_range_end=ref,
            warnings=schema_warnings,
        )

        result = CleanDemandSeries(
            product_id=product_id,
            frequency="D",
            observations=total_obs,
            clean_observations=clean_obs,
            missing_dates=missing_count,
            outliers_detected=outlier_count,
            series=series_points,
            mean_daily_demand=round(mean_demand, 4),
            std_daily_demand=round(std_demand, 4),
            diagnostics=diagnostics,
            generated_at=datetime.utcnow(),
        )

        logger.info(
            "[D1] %s — %d obs, %d clean, %d outliers, %d missing dates",
            product_id, total_obs, clean_obs, outlier_count, missing_count,
        )
        return result

    def run_all_products(
        self,
        transactions: pd.DataFrame,
        product_ids: Optional[List[str]] = None,
        reference_date: Optional[date] = None,
    ) -> Dict[str, CleanDemandSeries]:
        """Run D1 for all (or specified) products. Returns dict keyed by product_id."""
        if product_ids is None:
            product_ids = transactions["product_id"].dropna().unique().tolist()

        results: Dict[str, CleanDemandSeries] = {}
        for pid in product_ids:
            try:
                results[pid] = self.run(transactions, pid, reference_date)
            except InsufficientEvidenceError as e:
                logger.warning("[D1] Skipping %s: %s", pid, e)
            except Exception as e:
                logger.error("[D1] Unexpected error for %s: %s", pid, e, exc_info=True)
        return results

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _validate_and_filter(
        self, df: pd.DataFrame, product_id: str
    ) -> Tuple[pd.DataFrame, List[str]]:
        warnings: List[str] = []
        required = {"transaction_id", "timestamp", "product_id", "qty"}
        missing_cols = required - set(df.columns)
        if missing_cols:
            raise ValueError(f"Missing required columns: {missing_cols}")

        # Filter to product
        df = df[df["product_id"] == product_id].copy()

        # Ensure timestamp is datetime
        df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce")
        nat_count = df["timestamp"].isna().sum()
        if nat_count > 0:
            warnings.append(f"{nat_count} records had unparseable timestamps and were dropped.")
            df = df[df["timestamp"].notna()]

        df["date"] = df["timestamp"].dt.date

        return df, warnings

    def _remove_invalid(
        self, df: pd.DataFrame
    ) -> Tuple[pd.DataFrame, int, int]:
        """Remove negative qty (BAD-001) and missing qty (BAD-002)."""
        # Missing qty
        miss_mask = df["qty"].isna()
        miss_count = int(miss_mask.sum())

        # Negative qty
        neg_mask = (~miss_mask) & (df["qty"] < 0)
        neg_count = int(neg_mask.sum())

        df = df[~miss_mask & ~neg_mask].copy()
        return df, neg_count, miss_count

    def _remove_duplicates(self, df: pd.DataFrame) -> Tuple[pd.DataFrame, int]:
        """Remove exact duplicate transaction_ids (keep first)."""
        before = len(df)
        df = df.drop_duplicates(subset=["transaction_id"], keep="first")
        dup_count = before - len(df)
        return df, dup_count

    def _aggregate_daily(self, df: pd.DataFrame) -> pd.Series:
        """Sum qty per date → daily demand series."""
        df["date"] = pd.to_datetime(df["date"])
        daily = df.groupby("date")["qty"].sum()
        daily = daily.sort_index()
        return daily

    def _fill_date_range(
        self, daily: pd.Series, start: date, end: date
    ) -> Tuple[pd.Series, int]:
        """Reindex over full date range; fill missing with 0 (store closed / sold nothing)."""
        full_range = pd.date_range(
            start=pd.Timestamp(start),
            end=pd.Timestamp(end),
            freq="D",
        )
        reindexed = daily.reindex(full_range, fill_value=0.0)
        missing_count = int((reindexed == 0).sum()) - int((daily == 0).sum())
        missing_count = max(missing_count, 0)
        return reindexed, missing_count

    def _detect_outliers(
        self, series: pd.Series
    ) -> Tuple[pd.Series, int, pd.Series]:
        """
        Detect extreme outliers using IQR rule + Z-score.
        Outlier values are CAPPED (winsorized) rather than removed,
        to preserve the date sequence.
        BAD-003 (extreme outlier) is caught here.
        """
        q1 = series.quantile(0.25)
        q3 = series.quantile(0.75)
        iqr = q3 - q1
        lower = q1 - self.iqr_multiplier * iqr
        upper = q3 + self.iqr_multiplier * iqr

        # Z-score on non-zero values
        mean = series.mean()
        std = series.std()
        if std > 0:
            zscore = ((series - mean) / std).abs()
        else:
            zscore = pd.Series(0.0, index=series.index)

        outlier_mask = (series < lower) | (series > upper) | (zscore > self.zscore_threshold)
        outlier_count = int(outlier_mask.sum())

        # Winsorize: cap at IQR bounds
        capped = series.copy()
        capped[outlier_mask & (series > upper)] = upper
        capped[outlier_mask & (series < lower)] = 0.0  # negatives → 0

        return capped, outlier_count, outlier_mask

    def _build_series(
        self, daily: pd.Series, outlier_flags: pd.Series
    ) -> List[DemandPoint]:
        points = []
        for ts, qty in daily.items():
            d = ts.date() if hasattr(ts, "date") else ts
            points.append(
                DemandPoint(
                    date=d,
                    qty=float(qty),
                    is_interpolated=(qty == 0.0),  # filled missing → interpolated
                    is_outlier=bool(outlier_flags.get(ts, False)),
                )
            )
        return points
