"""
Demand Sensing FastAPI Application
====================================
Entry point for the backend service.
Includes offline-first lifecycle (SQLite init, background sync thread).
"""
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.demand_router import router
from app.api.system_router import router as system_router
from app.core.config import get_settings
from app.local.db import init_db
from app.local import sync as sync_module

_settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # ── Startup ────────────────────────────────────────────────────────────
    # Ensure local SQLite schema exists
    init_db()

    # Start background sync (will use Firestore only when online)
    try:
        from app.repositories.firestore_client import get_firestore_client
        fs = get_firestore_client()
    except Exception:
        fs = None  # run in local-only mode
    sync_module.init_sync(fs)

    yield

    # ── Shutdown ───────────────────────────────────────────────────────────
    sync_module.shutdown_sync()


app = FastAPI(
    title=_settings.app_name,
    version=_settings.app_version,
    description=(
        "Demand Sensing Domain API — D1 Sales History Analyzer, "
        "D2 Seasonality Detector, D3 Event & Promotion Signal Agent, "
        "D4 Forecast Generator, D5 Forecast Quality Monitor. "
        "Offline-first: all agents run locally and sync to Firebase when connected."
    ),
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],   # Tighten in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router, tags=["Demand Sensing"])
app.include_router(system_router, prefix="/system", tags=["System & Offline"])


@app.get("/health")
def health():
    from app.local.connectivity import diagnostics, is_online
    from app.local.cache import count_unsynced
    from app.local.queue import pending_count
    return {
        "status": "ok",
        "service": _settings.app_name,
        "version": _settings.app_version,
        "connectivity": "online" if is_online() else "offline",
        "unsynced_docs": count_unsynced(),
        "queued_jobs": pending_count(),
        "connectivity_details": diagnostics(),
    }


@app.get("/")
def root():
    return {
        "service": "Demand Sensing API",
        "docs": "/docs",
        "health": "/health",
        "offline_status": "/system/status",
    }