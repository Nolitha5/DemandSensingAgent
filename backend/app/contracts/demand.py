"""
Stable published contracts for the Demand Sensing domain.

Inventory, Pricing and Procurement must consume ONLY these contracts.
Do not import any internal demand agent classes from outside this domain.
"""
from __future__ import annotations

from datetime import date, datetime
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------

class SignalType(str, Enum):
    LOCAL_EVENT = "LOCAL_EVENT"
    PROMOTION = "PROMOTION"
    HOLIDAY = "HOLIDAY"
    MARKET_DAY = "MARKET_DAY"


class ForecastStatus(str, Enum):
    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"
    ALERT = "ALERT"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"


class AgentRunStatus(str, Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"


class ModelName(str, Enum):
    NAIVE = "naive"
    SEASONAL_NAIVE = "seasonal_naive"
    EXPONENTIAL_SMOOTHING = "exponential_smoothing"
    HOLT_WINTERS = "holt_winters"
    REGRESSION = "regression"
    ROLLING_MEAN = "rolling_mean"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"


# ---------------------------------------------------------------------------
# D1 — CleanDemandSeries
# ---------------------------------------------------------------------------

class DemandPoint(BaseModel):
    """A single daily demand observation."""
    date: date
    qty: float
    is_interpolated: bool = False
    is_outlier: bool = False


class DemandDiagnostics(BaseModel):
    """Diagnostics produced by D1 for human review."""
    product_id: str
    raw_records: int
    invalid_negative: int = 0
    invalid_missing_qty: int = 0
    duplicates_removed: int = 0
    outliers_flagged: int = 0
    missing_dates_filled: int = 0
    coverage_pct: float  # 0-1
    date_range_start: date
    date_range_end: date
    warnings: List[str] = Field(default_factory=list)


class CleanDemandSeries(BaseModel):
    """
    Published output of D1 Sales History Analyzer.
    Primary consumers: D2, D3, D4.
    """
    model_config = ConfigDict(populate_by_name=True)

    product_id: str
    frequency: str = "D"  # Daily
    observations: int
    clean_observations: int
    missing_dates: int
    outliers_detected: int
    series: List[DemandPoint]
    mean_daily_demand: float
    std_daily_demand: float
    diagnostics: DemandDiagnostics
    generated_at: datetime = Field(default_factory=datetime.utcnow)


# ---------------------------------------------------------------------------
# D2 — SeasonalityProfile
# ---------------------------------------------------------------------------

class SeasonalityProfile(BaseModel):
    """
    Published output of D2 Seasonality Detector.
    Primary consumer: D4.
    """
    model_config = ConfigDict(populate_by_name=True)

    product_id: str
    weekly_pattern: Dict[str, float] = Field(
        default_factory=dict,
        description="Day-of-week index relative to weekly mean (1.0 = average)."
    )
    payday_effect: float = Field(
        1.0, description="Multiplicative uplift during payday windows (1.0 = no effect)."
    )
    monthly_pattern: Dict[int, float] = Field(
        default_factory=dict,
        description="Month number -> relative index."
    )
    holiday_effect: float = Field(
        1.0, description="Multiplicative uplift on public holidays."
    )
    seasonality_strength: float = Field(
        0.0, ge=0.0, le=1.0,
        description="0 = no seasonality detected, 1 = strong."
    )
    confidence: float = Field(0.0, ge=0.0, le=1.0)
    detected_patterns: List[str] = Field(default_factory=list)
    data_sufficient: bool = True
    min_observations_used: int = 0
    generated_at: datetime = Field(default_factory=datetime.utcnow)


# ---------------------------------------------------------------------------
# D3 — DemandSignalAdjustment
# ---------------------------------------------------------------------------

class DemandSignalAdjustment(BaseModel):
    """
    Published output of D3 Event & Promotion Signal Agent.
    Primary consumer: D4.
    """
    model_config = ConfigDict(populate_by_name=True)

    product_id: str
    signal_type: SignalType
    event_id: Optional[str] = None
    promo_id: Optional[str] = None
    adjustment_factor: float = Field(
        1.0, description="Multiplicative factor applied to baseline forecast."
    )
    confidence: float = Field(0.0, ge=0.0, le=1.0)
    reason: str = ""
    active_from: Optional[date] = None
    active_to: Optional[date] = None
    generated_at: datetime = Field(default_factory=datetime.utcnow)


# ---------------------------------------------------------------------------
# D4 — DemandForecast  ← STABLE CONTRACT consumed by Inventory/Pricing/Procurement
# ---------------------------------------------------------------------------

class ForecastDay(BaseModel):
    """A single day in the forecast horizon."""
    date: date
    expected_qty: float
    lower_bound: float
    upper_bound: float


class DemandForecast(BaseModel):
    """
    STABLE published output of D4 Forecast Generator.
    Downstream consumers: Inventory (I2, I3), Pricing (P3, P5), Procurement (R4).

    IMPORTANT: Inventory, Pricing and Procurement must depend only on this contract.
    They must never import any internal D1-D5 agent classes.
    """
    model_config = ConfigDict(populate_by_name=True, use_enum_values=True)

    product_id: str
    horizon: int = Field(7, description="Forecast horizon in days.")
    expected_qty: float = Field(description="Total expected demand across horizon.")
    lower_bound: float
    upper_bound: float
    confidence: float = Field(ge=0.0, le=1.0)
    drivers: List[str] = Field(default_factory=list)
    model: ModelName = ModelName.NAIVE
    forecast_days: List[ForecastDay] = Field(default_factory=list)
    status: ForecastStatus = ForecastStatus.HEALTHY
    insufficient_evidence: bool = False
    evidence_notes: str = ""
    generated_at: datetime = Field(default_factory=datetime.utcnow)
    data_freshness_days: Optional[int] = None


# ---------------------------------------------------------------------------
# D5 — ForecastQualityAlert
# ---------------------------------------------------------------------------

class ForecastQualityReport(BaseModel):
    """Per-product quality metrics for D5."""
    model_config = ConfigDict(populate_by_name=True)

    product_id: str
    horizon: int
    mae: Optional[float] = None
    wape: Optional[float] = None
    bias: Optional[float] = None
    drift_detected: bool = False
    coverage_ok: bool = True
    actuals_count: int = 0
    status: ForecastStatus = ForecastStatus.HEALTHY
    notes: str = ""
    evaluated_at: datetime = Field(default_factory=datetime.utcnow)


class ForecastQualityAlert(BaseModel):
    """
    Published output of D5 Forecast Quality Monitor.
    Consumer: Coordinator (can lower trust in D4 output, triggers D4 re-run).
    """
    model_config = ConfigDict(populate_by_name=True)

    alert_id: str
    product_id: str
    alert_type: str  # e.g. "HIGH_WAPE", "BIAS_DRIFT", "INSUFFICIENT_ACTUALS"
    severity: str    # "LOW", "MEDIUM", "HIGH"
    metric_value: Optional[float] = None
    threshold: Optional[float] = None
    message: str
    recommended_action: str = ""
    generated_at: datetime = Field(default_factory=datetime.utcnow)


# ---------------------------------------------------------------------------
# Sentinel — returned when evidence is insufficient
# ---------------------------------------------------------------------------

class InsufficientEvidenceError(Exception):
    """Raised when an agent cannot produce a reliable output due to insufficient data."""
    def __init__(self, agent: str, product_id: str, reason: str):
        self.agent = agent
        self.product_id = product_id
        self.reason = reason
        super().__init__(f"[{agent}] INSUFFICIENT_EVIDENCE for {product_id}: {reason}")
