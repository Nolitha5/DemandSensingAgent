"""
Demand Repository — offline-first edition
==========================================
All Firestore read/write operations for the Demand Sensing domain.

Offline-first strategy
───────────────────────
- Writes: always save to local SQLite cache first (instant, never blocked).
  If Firestore is reachable, also write there immediately; otherwise the
  background SyncManager will push the record when connectivity returns.
- Reads: try Firestore first (fresh data); on failure fall back to the local
  cache. Callers receive a `_cache_stale` flag so they can surface a
  "data may be stale" warning in the UI.
- Deduplication: the local cache uses (collection, doc_id) as a primary key
  and a monotonic version counter, so re-saving the same record is safe.

Collections:
  demand_series
  seasonality_profiles
  demand_signal_adjustments
  demand_forecasts
  forecast_quality_alerts
  forecast_quality_reports
  agent_runs
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Dict, List, Optional

from app.contracts.demand import (
    CleanDemandSeries,
    DemandForecast,
    DemandSignalAdjustment,
    ForecastQualityAlert,
    ForecastQualityReport,
    SeasonalityProfile,
)
from app.local import cache as local_cache
from app.local.connectivity import is_online

logger = logging.getLogger(__name__)


def _firestore():
    """Lazy import so the app starts without Firebase credentials."""
    try:
        from app.repositories.firestore_client import get_firestore_client
        return get_firestore_client()
    except Exception as exc:
        logger.warning("Firestore unavailable: %s", exc)
        return None


def _now() -> str:
    return datetime.utcnow().isoformat()


class DemandRepository:
    """
    Repository for all Demand Sensing operations — offline-first.
    Every method returns typed Pydantic models.
    """

    def __init__(self, db=None):
        # db may be injected (tests) or lazily resolved on first use
        self._db = db

    def _get_db(self):
        if self._db is None and is_online():
            self._db = _firestore()
        return self._db

    # ── Generic save/load helpers ──────────────────────────────────────────

    def _save(self, collection: str, doc_id: str, data: dict) -> str:
        """
        Local-first write.
        1. Persist to SQLite (always).
        2. If online, also write to Firestore immediately.
        """
        local_cache.put(collection, doc_id, data)

        db = self._get_db()
        if db:
            try:
                db.collection(collection).document(doc_id).set(
                    {**data, "saved_at": _now()}
                )
                # Mark synced so SyncManager doesn't duplicate the push
                meta = local_cache.get_with_meta(collection, doc_id)
                if meta:
                    local_cache.mark_synced(collection, doc_id, meta["_cache_version"])
            except Exception as exc:
                logger.warning("Firestore write %s/%s deferred: %s", collection, doc_id, exc)

        return doc_id

    def _load(self, collection: str, doc_id: str) -> Optional[dict]:
        """
        Try Firestore first; fall back to local cache on any failure.
        Returns the raw dict (caller parses into Pydantic model).
        """
        db = self._get_db()
        if db:
            try:
                snap = db.collection(collection).document(doc_id).get()
                if snap.exists:
                    data = snap.to_dict()
                    # Refresh local cache with fresh remote data
                    local_cache.put(collection, doc_id, data)
                    meta = local_cache.get_with_meta(collection, doc_id)
                    if meta:
                        local_cache.mark_synced(collection, doc_id, meta["_cache_version"])
                    return data
            except Exception as exc:
                logger.warning("Firestore read %s/%s failed, using cache: %s",
                               collection, doc_id, exc)

        # Fallback: local cache
        return local_cache.get(collection, doc_id)

    # ── D1: Clean Demand Series ────────────────────────────────────────────

    def save_demand_series(self, series: CleanDemandSeries) -> str:
        doc_id = series.product_id
        data = series.model_dump(mode="json")
        data["saved_at"] = _now()
        self._save("demand_series", doc_id, data)
        logger.info("[Repo] Saved demand_series for %s (online=%s)", doc_id, is_online())
        return doc_id

    def get_demand_series(self, product_id: str) -> Optional[CleanDemandSeries]:
        raw = self._load("demand_series", product_id)
        if not raw:
            return None
        try:
            return CleanDemandSeries(**raw)
        except Exception as exc:
            logger.warning("[Repo] Could not parse demand_series %s: %s", product_id, exc)
            return None

    def list_demand_series_product_ids(self) -> List[str]:
        db = self._get_db()
        if db:
            try:
                return [d.id for d in db.collection("demand_series").stream()]
            except Exception:
                pass
        return [d["_doc_id"] for d in local_cache.list_collection("demand_series")]

    # ── D2: Seasonality Profiles ───────────────────────────────────────────

    def save_seasonality_profile(self, profile: SeasonalityProfile) -> str:
        doc_id = profile.product_id
        data = profile.model_dump(mode="json")
        data["saved_at"] = _now()
        self._save("seasonality_profiles", doc_id, data)
        logger.info("[Repo] Saved seasonality_profile for %s", doc_id)
        return doc_id

    def get_seasonality_profile(self, product_id: str) -> Optional[SeasonalityProfile]:
        raw = self._load("seasonality_profiles", product_id)
        if not raw:
            return None
        try:
            return SeasonalityProfile(**raw)
        except Exception as exc:
            logger.warning("[Repo] Could not parse seasonality_profile %s: %s", product_id, exc)
            return None

    # ── D3: Demand Signal Adjustments ─────────────────────────────────────

    def save_signal_adjustments(
        self, product_id: str, adjustments: List[DemandSignalAdjustment]
    ) -> List[str]:
        ids = []
        for adj in adjustments:
            doc_id = (
                f"{product_id}_{adj.signal_type}"
                f"_{adj.promo_id or adj.event_id or 'general'}"
            )
            data = adj.model_dump(mode="json")
            data["saved_at"] = _now()
            self._save("demand_signal_adjustments", doc_id, data)
            ids.append(doc_id)
        logger.info("[Repo] Saved %d signal adjustments for %s", len(adjustments), product_id)
        return ids

    def get_signal_adjustments(self, product_id: str) -> List[DemandSignalAdjustment]:
        db = self._get_db()
        results = []

        if db:
            try:
                docs = (
                    db.collection("demand_signal_adjustments")
                    .where("product_id", "==", product_id)
                    .stream()
                )
                for doc in docs:
                    try:
                        results.append(DemandSignalAdjustment(**doc.to_dict()))
                    except Exception as exc:
                        logger.warning("[Repo] Bad signal_adjustment %s: %s", doc.id, exc)
                return results
            except Exception as exc:
                logger.warning("Firestore signal adjustments query failed: %s", exc)

        # Fallback: scan local cache
        for entry in local_cache.list_collection("demand_signal_adjustments"):
            if entry.get("product_id") == product_id:
                try:
                    results.append(DemandSignalAdjustment(**entry))
                except Exception:
                    pass
        return results

    # ── D4: Demand Forecasts ───────────────────────────────────────────────

    def save_forecast(self, forecast: DemandForecast) -> str:
        doc_id = (
            f"{forecast.product_id}"
            f"_{forecast.generated_at.strftime('%Y%m%dT%H%M%S')}"
        )
        data = forecast.model_dump(mode="json")
        data["saved_at"] = _now()
        self._save("demand_forecasts", doc_id, data)

        # Also save "latest" pointer
        latest_id = f"{forecast.product_id}_latest"
        self._save("demand_forecasts", latest_id, data)

        logger.info("[Repo] Saved forecast for %s (online=%s)", forecast.product_id, is_online())
        return doc_id

    def get_latest_forecast(self, product_id: str) -> Optional[DemandForecast]:
        raw = self._load("demand_forecasts", f"{product_id}_latest")
        if not raw:
            return None
        try:
            return DemandForecast(**raw)
        except Exception as exc:
            logger.warning("[Repo] Could not parse forecast %s: %s", product_id, exc)
            return None

    def list_latest_forecasts(self) -> List[DemandForecast]:
        db = self._get_db()
        results: Dict[str, DemandForecast] = {}

        if db:
            try:
                docs = db.collection("demand_forecasts").stream()
                for doc in docs:
                    if doc.id.endswith("_latest"):
                        try:
                            fc = DemandForecast(**doc.to_dict())
                            results[fc.product_id] = fc
                        except Exception:
                            pass
                return list(results.values())
            except Exception as exc:
                logger.warning("Firestore list_latest_forecasts failed: %s", exc)

        # Fallback: local cache
        for entry in local_cache.list_collection("demand_forecasts"):
            doc_id = entry.get("_doc_id", "")
            if doc_id.endswith("_latest"):
                try:
                    fc = DemandForecast(**entry)
                    results[fc.product_id] = fc
                except Exception:
                    pass
        return list(results.values())

    # ── D5: Forecast Quality ───────────────────────────────────────────────

    def save_quality_report(self, report: ForecastQualityReport) -> str:
        doc_id = (
            f"{report.product_id}"
            f"_{report.evaluated_at.strftime('%Y%m%dT%H%M%S')}"
        )
        data = report.model_dump(mode="json")
        data["saved_at"] = _now()
        self._save("forecast_quality_reports", doc_id, data)
        return doc_id

    def save_quality_alert(self, alert: ForecastQualityAlert) -> str:
        data = alert.model_dump(mode="json")
        data["saved_at"] = _now()
        self._save("forecast_quality_alerts", alert.alert_id, data)
        logger.info("[Repo] Saved quality alert %s for %s", alert.alert_type, alert.product_id)
        return alert.alert_id

    def get_quality_reports(self, product_id: str) -> List[ForecastQualityReport]:
        db = self._get_db()
        results = []

        if db:
            try:
                from google.cloud import firestore as _fs
                docs = (
                    db.collection("forecast_quality_reports")
                    .where("product_id", "==", product_id)
                    .order_by("evaluated_at", direction=_fs.Query.DESCENDING)
                    .limit(10)
                    .stream()
                )
                for doc in docs:
                    try:
                        results.append(ForecastQualityReport(**doc.to_dict()))
                    except Exception as exc:
                        logger.warning("[Repo] Bad quality_report %s: %s", doc.id, exc)
                return results
            except Exception as exc:
                logger.warning("Firestore quality_reports query failed: %s", exc)

        for entry in local_cache.list_collection("forecast_quality_reports"):
            if entry.get("product_id") == product_id:
                try:
                    results.append(ForecastQualityReport(**entry))
                except Exception:
                    pass
        return results[:10]

    def get_quality_alerts(self, product_id: Optional[str] = None) -> List[ForecastQualityAlert]:
        db = self._get_db()
        results = []

        if db:
            try:
                col = db.collection("forecast_quality_alerts")
                docs = (
                    col.where("product_id", "==", product_id).stream()
                    if product_id else col.stream()
                )
                for doc in docs:
                    try:
                        results.append(ForecastQualityAlert(**doc.to_dict()))
                    except Exception as exc:
                        logger.warning("[Repo] Bad quality_alert %s: %s", doc.id, exc)
                return results
            except Exception as exc:
                logger.warning("Firestore quality_alerts query failed: %s", exc)

        for entry in local_cache.list_collection("forecast_quality_alerts"):
            if product_id is None or entry.get("product_id") == product_id:
                try:
                    results.append(ForecastQualityAlert(**entry))
                except Exception:
                    pass
        return results

    # ── Agent Run Log ──────────────────────────────────────────────────────

    def log_agent_run(
        self,
        agent_id: str,
        product_id: str,
        status: str,
        details: Optional[dict] = None,
    ) -> str:
        run_id = f"{agent_id}_{datetime.utcnow().strftime('%Y%m%dT%H%M%S')}"
        data = {
            "run_id": run_id,
            "agent_id": agent_id,
            "product_id": product_id,
            "status": status,
            "details": details or {},
            "created_at": _now(),
        }
        self._save("agent_runs", run_id, data)
        return run_id
