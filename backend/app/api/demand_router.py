"""
Demand Sensing API Router
==========================
All endpoints are under /demand and /agents (D1-D5 manual triggers).
"""
from __future__ import annotations

import logging
from datetime import date
from typing import Dict, List, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from pydantic import BaseModel

from app.contracts.demand import (
    CleanDemandSeries,
    DemandForecast,
    DemandSignalAdjustment,
    ForecastQualityAlert,
    ForecastQualityReport,
    SeasonalityProfile,
)
from app.services.demand_service import DemandService
from app.core.config import get_settings
from app.local import queue as local_queue
from app.local.connectivity import is_online

logger = logging.getLogger(__name__)
router = APIRouter()

_settings = get_settings()


def get_service() -> DemandService:
    return DemandService()


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------

class TransactionImportRequest(BaseModel):
    store_id: str = "STORE-001"
    source: str = "csv"
    note: Optional[str] = None


class AgentRunResponse(BaseModel):
    status: str
    agent: str
    product_id: Optional[str] = None
    message: str


class PipelineRunResponse(BaseModel):
    status: str
    products_processed: int
    forecasts: List[DemandForecast]
    alerts: List[ForecastQualityAlert]


# ---------------------------------------------------------------------------
# Import endpoints
# ---------------------------------------------------------------------------

@router.post("/imports/transactions", summary="Import transactions from CSV / Firestore seed")
def import_transactions(
    request: TransactionImportRequest,
    background: BackgroundTasks,
    service: DemandService = Depends(get_service),
):
    """Trigger D1 for all products after transaction import."""
    def _run():
        try:
            results = service.run_d1()
            logger.info("[API] Transaction import D1 completed for %d products", len(results))
        except Exception as e:
            logger.error("[API] D1 background run failed: %s", e)

    background.add_task(_run)
    return {"status": "accepted", "message": "D1 queued for all products."}


@router.post("/imports/promotions", summary="Notify that promotions have been updated")
def import_promotions(service: DemandService = Depends(get_service)):
    """Trigger D3 re-run for all products."""
    return {"status": "accepted", "message": "D3 will re-run on next agent trigger."}


@router.post("/imports/local-events", summary="Notify that local events have been updated")
def import_local_events(service: DemandService = Depends(get_service)):
    return {"status": "accepted", "message": "D3 will re-run on next agent trigger."}


# ---------------------------------------------------------------------------
# Manual agent triggers
# ---------------------------------------------------------------------------

@router.post("/agents/D1/run", response_model=Dict[str, AgentRunResponse])
def run_d1(
    product_id: Optional[str] = Query(None, description="Run for specific product only"),
    service: DemandService = Depends(get_service),
):
    """Run D1 Sales History Analyzer."""
    try:
        results = service.run_d1(product_id)
        return {
            pid: AgentRunResponse(
                status="completed", agent="D1", product_id=pid,
                message=f"{s.clean_observations} clean observations produced."
            )
            for pid, s in results.items()
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/agents/D2/run", response_model=SeasonalityProfile)
def run_d2(
    product_id: str = Query(..., description="Product ID"),
    service: DemandService = Depends(get_service),
):
    """Run D2 Seasonality Detector for one product."""
    try:
        return service.run_d2_for_product(product_id)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/agents/D3/run", response_model=List[DemandSignalAdjustment])
def run_d3(
    product_id: str = Query(...),
    service: DemandService = Depends(get_service),
):
    """Run D3 Event & Promotion Signal Agent for one product."""
    try:
        return service.run_d3_for_product(product_id)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/agents/D4/run", response_model=DemandForecast)
def run_d4(
    product_id: str = Query(...),
    horizon: int = Query(7, ge=1, le=30),
    service: DemandService = Depends(get_service),
):
    """Run D4 Forecast Generator for one product."""
    try:
        return service.run_d4(product_id, horizon)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/agents/D5/run")
def run_d5(
    product_id: str = Query(...),
    horizon: int = Query(7, ge=1, le=30),
    service: DemandService = Depends(get_service),
):
    """Run D5 Forecast Quality Monitor for one product."""
    try:
        forecast = service.run_d4(product_id, horizon)
        report, alerts = service.run_d5(forecast)
        return {"report": report, "alerts": alerts}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/agents/status")
def agent_status():
    return {
        "agents": {
            "D1": {"name": "Sales History Analyzer", "status": "available"},
            "D2": {"name": "Seasonality Detector", "status": "available"},
            "D3": {"name": "Event & Promotion Signal Agent", "status": "available"},
            "D4": {"name": "Forecast Generator", "status": "available"},
            "D5": {"name": "Forecast Quality Monitor", "status": "available"},
        }
    }


# ---------------------------------------------------------------------------
# Demand read endpoints (consumed by Inventory, Pricing, Procurement)
# ---------------------------------------------------------------------------

@router.get("/demand/forecast/{product_id}", response_model=DemandForecast)
def get_forecast(
    product_id: str,
    horizon: int = Query(7, ge=1, le=30),
    service: DemandService = Depends(get_service),
):
    """Get demand forecast for a specific product. This is the STABLE CONTRACT endpoint."""
    try:
        return service.run_d4(product_id, horizon)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/demand/forecast", response_model=Dict[str, DemandForecast])
def get_all_forecasts(
    horizon: int = Query(7, ge=1, le=30),
    service: DemandService = Depends(get_service),
):
    """Get demand forecasts for all products."""
    return service.run_d4_all_products(horizon)


@router.get("/demand/seasonality/{product_id}", response_model=SeasonalityProfile)
def get_seasonality(
    product_id: str,
    service: DemandService = Depends(get_service),
):
    return service.run_d2_for_product(product_id)


@router.get("/demand/signals/{product_id}", response_model=List[DemandSignalAdjustment])
def get_signals(
    product_id: str,
    service: DemandService = Depends(get_service),
):
    return service.run_d3_for_product(product_id)


@router.get("/demand/quality/{product_id}")
def get_quality(
    product_id: str,
    horizon: int = Query(7),
    service: DemandService = Depends(get_service),
):
    forecast = service.run_d4(product_id, horizon)
    report, alerts = service.run_d5(forecast)
    return {"report": report, "alerts": alerts}


@router.get("/demand/series/{product_id}", response_model=CleanDemandSeries)
def get_demand_series(
    product_id: str,
    service: DemandService = Depends(get_service),
):
    results = service.run_d1(product_id)
    if product_id not in results:
        raise HTTPException(status_code=404, detail=f"No clean series for {product_id}")
    return results[product_id]


@router.get("/metrics/evaluation")
def get_evaluation_metrics(
    service: DemandService = Depends(get_service),
):
    """Run full pipeline and return quality metrics for all products."""
    pipeline_result = service.run_full_pipeline()
    forecasts = pipeline_result["forecasts"]
    quality_reports = pipeline_result["quality_reports"]
    alerts = pipeline_result["alerts"]

    summary = {}
    for pid, fc in forecasts.items():
        qr = quality_reports.get(pid)
        summary[pid] = {
            "forecast_status": fc.status,
            "model": fc.model,
            "confidence": fc.confidence,
            "expected_qty_7d": fc.expected_qty,
            "mae": qr.mae if qr else None,
            "wape": qr.wape if qr else None,
            "bias": qr.bias if qr else None,
            "quality_status": qr.status if qr else None,
        }

    return {
        "products_evaluated": len(summary),
        "alerts_generated": len(alerts),
        "summary": summary,
        "alerts": [a.dict() for a in alerts],
    }


# Full pipeline trigger
@router.post("/demand/pipeline/run")
def run_pipeline(
    horizon: int = Query(7, ge=1, le=30),
    product_id: Optional[str] = Query(None, description="Run for specific product; omit for all"),
    background: BackgroundTasks = None,
    service: DemandService = Depends(get_service),
):
    """
    Trigger full D1→D5 pipeline for all products (or a specific one).
    Offline: the pipeline runs locally regardless of connectivity. Results are
    cached locally and pushed to Firestore by the background sync manager when
    the connection is restored.
    The job is also recorded in the local queue for deduplication and retry.
    """
    payload = {"product_id": product_id or "ALL", "horizon": str(horizon)}
    job_id = local_queue.enqueue("pipeline", payload, max_attempts=3)

    def _run_and_complete():
        try:
            job = local_queue.claim_next("pipeline")
            if not job:
                return   # another worker already claimed it
            if product_id:
                fc = service.run_d4(product_id, horizon)
                report, alerts = service.run_d5(fc)
                result = {
                    "forecasts": {product_id: fc},
                    "quality_reports": {product_id: report},
                    "alerts": alerts,
                }
            else:
                result = service.run_full_pipeline(horizon)
            local_queue.mark_done(job["job_id"])
            logger.info(
                "[Pipeline] Completed for %s products; alerts=%d",
                len(result["forecasts"]), len(result["alerts"])
            )
        except Exception as exc:
            logger.error("[Pipeline] Failed: %s", exc)
            if job:
                local_queue.mark_failed(job["job_id"], str(exc))

    if background:
        background.add_task(_run_and_complete)
        return {
            "status": "accepted",
            "job_id": job_id,
            "online": is_online(),
            "message": "Pipeline enqueued. Results saved locally and synced when online.",
        }

    # Synchronous fallback (no background task available)
    _run_and_complete()
    return {"status": "completed", "job_id": job_id, "online": is_online()}
