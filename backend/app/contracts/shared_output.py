"""
Shared Agent Output Envelope — 25-Agent Firebase Architecture.

Every agent in every domain publishes its result wrapped in this envelope.
The envelope is written to two Firestore collections:
  - agent_outputs  : append-only history (never overwritten)
  - agent_state    : latest output per (agent_id, entity_id) — upserted

Rules
─────
- output_id   : deterministic, derived from agent_id + entity_id + run_id + schema_version
- input_refs  : list of output_ids this agent consumed (enables audit trace)
- payload     : the domain-specific Pydantic model serialised to dict
- Offline-first: the OutputRepository writes locally first; Firestore is best-effort

Downstream consumers (Inventory, Pricing, Procurement) must:
  1. Read from Firestore agent_state collection, NOT import Demand Python classes.
  2. Filter by agent_id and entity_id.
  3. Validate the schema_version before parsing payload.
"""
from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timezone, timedelta
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field


# ── Risk level ────────────────────────────────────────────────────────────────

class RiskLevel(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    UNKNOWN = "UNKNOWN"


# ── Output types ──────────────────────────────────────────────────────────────

class OutputType(str, Enum):
    CLEAN_DEMAND_SERIES = "CleanDemandSeries"
    SEASONALITY_PROFILE = "SeasonalityProfile"
    DEMAND_SIGNAL_ADJUSTMENT = "DemandSignalAdjustment"
    DEMAND_FORECAST = "DemandForecast"
    FORECAST_QUALITY_ALERT = "ForecastQualityAlert"
    FORECAST_QUALITY_REPORT = "ForecastQualityReport"


# ── Freshness defaults per output type (seconds) ─────────────────────────────

_EXPIRY_SECONDS: Dict[str, int] = {
    OutputType.DEMAND_FORECAST: 86_400,          # 24 h — consumers need fresh forecasts
    OutputType.CLEAN_DEMAND_SERIES: 7 * 86_400,  # 7 days
    OutputType.SEASONALITY_PROFILE: 30 * 86_400, # 30 days
    OutputType.DEMAND_SIGNAL_ADJUSTMENT: 7 * 86_400,
    OutputType.FORECAST_QUALITY_ALERT: 3 * 86_400,
    OutputType.FORECAST_QUALITY_REPORT: 7 * 86_400,
}


def _make_output_id(agent_id: str, entity_id: str, run_id: str, schema_version: str) -> str:
    """
    Deterministic output_id — the same inputs always produce the same ID.
    This prevents duplicate published outputs during offline retries.
    """
    raw = f"{agent_id}:{entity_id}:{run_id}:{schema_version}"
    return hashlib.sha256(raw.encode()).hexdigest()[:40]


def _risk_from_confidence(confidence: float) -> RiskLevel:
    if confidence >= 0.75:
        return RiskLevel.LOW
    if confidence >= 0.45:
        return RiskLevel.MEDIUM
    return RiskLevel.HIGH


# ── The envelope ──────────────────────────────────────────────────────────────

class AgentOutput(BaseModel):
    """
    Standard output envelope for all 25 agents across all 5 domains.
    Written to agent_outputs (append-only) and agent_state (latest-per-entity).

    Fields that may NOT be set by callers (computed by factory):
      - output_id      : always deterministic
      - generated_at   : always UTC now
      - expires_at     : derived from output_type default
    """
    model_config = ConfigDict(populate_by_name=True, use_enum_values=True)

    # Identity
    output_id: str = Field(description="Deterministic SHA-256 based ID.")
    agent_id: str = Field(description="e.g. 'D1', 'D4', 'I2'")
    domain: str = Field(description="e.g. 'demand', 'inventory', 'pricing'")

    # Type / entity
    output_type: str = Field(description="One of OutputType enum values.")
    entity_type: str = Field(description="e.g. 'product', 'sku', 'store'")
    entity_id: str = Field(description="e.g. product_id")

    # Payload — the actual agent result serialised to plain dict
    payload: Dict[str, Any] = Field(description="Domain-specific result.")

    # Provenance
    input_refs: List[str] = Field(
        default_factory=list,
        description="output_ids of upstream AgentOutputs consumed to produce this output.",
    )
    run_id: str = Field(description="Pipeline run identifier.")

    # Quality / risk
    confidence: float = Field(0.0, ge=0.0, le=1.0)
    risk_level: str = Field(default=RiskLevel.UNKNOWN)

    # Versioning
    schema_version: str = Field(default="1.0")
    model_or_rule_version: str = Field(default="1.0")

    # Freshness
    generated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    expires_at: Optional[datetime] = None


# ── Factory ───────────────────────────────────────────────────────────────────

def make_agent_output(
    *,
    agent_id: str,
    domain: str,
    output_type: str,  # OutputType value
    entity_type: str,
    entity_id: str,
    payload: Dict[str, Any],
    run_id: str,
    confidence: float = 0.0,
    input_refs: Optional[List[str]] = None,
    schema_version: str = "1.0",
    model_or_rule_version: str = "1.0",
    expiry_seconds: Optional[int] = None,
) -> AgentOutput:
    """
    Construct a fully populated AgentOutput with a deterministic output_id.
    Use this factory everywhere — never construct AgentOutput manually.
    """
    now = datetime.now(timezone.utc)
    output_id = _make_output_id(agent_id, entity_id, run_id, schema_version)
    risk_level = _risk_from_confidence(confidence)

    # Determine expiry
    secs = expiry_seconds if expiry_seconds is not None else _EXPIRY_SECONDS.get(output_type, 86_400)
    expires_at = now + timedelta(seconds=secs)

    return AgentOutput(
        output_id=output_id,
        agent_id=agent_id,
        domain=domain,
        output_type=output_type,
        entity_type=entity_type,
        entity_id=entity_id,
        payload=payload,
        input_refs=input_refs or [],
        run_id=run_id,
        confidence=confidence,
        risk_level=risk_level,
        schema_version=schema_version,
        model_or_rule_version=model_or_rule_version,
        generated_at=now,
        expires_at=expires_at,
    )


# ── System Event ──────────────────────────────────────────────────────────────

class SystemEvent(BaseModel):
    """
    Written to the system_events Firestore collection for cross-domain routing.
    e.g. "forecast.updated" triggers Inventory/Pricing re-runs.
    """
    model_config = ConfigDict(populate_by_name=True)

    event_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    event_type: str = Field(description="e.g. 'forecast.updated', 'quality.alert.raised'")
    source_agent_id: str
    entity_type: str
    entity_id: str
    output_id: str = Field(description="The AgentOutput that triggered this event.")
    payload: Dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    processed: bool = False


def make_system_event(
    event_type: str,
    source_agent_id: str,
    entity_type: str,
    entity_id: str,
    output_id: str,
    payload: Optional[Dict[str, Any]] = None,
) -> SystemEvent:
    return SystemEvent(
        event_type=event_type,
        source_agent_id=source_agent_id,
        entity_type=entity_type,
        entity_id=entity_id,
        output_id=output_id,
        payload=payload or {},
    )
