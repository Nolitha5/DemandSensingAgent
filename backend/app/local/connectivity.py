"""
Firebase / internet connectivity probe.

Uses a lightweight HTTP HEAD request to the Firebase REST API rather than
importing firebase-admin so this module works even when credentials are absent.

Results are cached for TTL_SECONDS to avoid hammering the network on every
cache read; the cache is in-process only (not persisted).
"""
from __future__ import annotations

import logging
import threading
import time
import urllib.request
from enum import Enum
from typing import Optional

log = logging.getLogger(__name__)

# ── Configuration ─────────────────────────────────────────────────────────────

# How long to reuse a connectivity result before re-probing (seconds)
TTL_SECONDS = 15

# Firebase REST endpoint used as the probe target (tiny, no auth needed)
_PROBE_URL = "https://smmes-7adc8-default-rtdb.firebaseio.com/.json?shallow=true"

# Fallback plain HTTPS probe if Firebase is unreachable for non-auth reasons
_FALLBACK_URL = "https://www.google.com"

# HTTP timeout for the probe (seconds)
_TIMEOUT = 5


# ── Status enum ───────────────────────────────────────────────────────────────

class ConnectivityStatus(str, Enum):
    ONLINE = "ONLINE"
    OFFLINE = "OFFLINE"
    UNKNOWN = "UNKNOWN"


# ── Module-level cache ────────────────────────────────────────────────────────

_lock = threading.Lock()
_last_status: ConnectivityStatus = ConnectivityStatus.UNKNOWN
_last_checked: float = 0.0          # unix timestamp of last probe
_check_count: int = 0               # total probes performed (for diagnostics)


# ── Public API ────────────────────────────────────────────────────────────────

def get_status(force: bool = False) -> ConnectivityStatus:
    """
    Return the current connectivity status.

    Results are cached for TTL_SECONDS; pass force=True to bypass the cache
    and re-probe immediately (useful for post-reconnect checks).
    """
    global _last_status, _last_checked, _check_count

    with _lock:
        age = time.monotonic() - _last_checked
        if not force and age < TTL_SECONDS and _last_status != ConnectivityStatus.UNKNOWN:
            return _last_status

        status = _probe()
        _last_status = status
        _last_checked = time.monotonic()
        _check_count += 1
        log.debug("connectivity probe #%d → %s", _check_count, status.value)
        return status


def is_online(force: bool = False) -> bool:
    return get_status(force=force) == ConnectivityStatus.ONLINE


def is_offline(force: bool = False) -> bool:
    return get_status(force=force) == ConnectivityStatus.OFFLINE


def invalidate_cache() -> None:
    """Force the next call to re-probe (e.g., after a network change event)."""
    global _last_checked
    with _lock:
        _last_checked = 0.0


def diagnostics() -> dict:
    """Return a snapshot of the probe state for health-check endpoints."""
    with _lock:
        return {
            "status": _last_status.value,
            "last_checked_ago_seconds": round(time.monotonic() - _last_checked, 1),
            "probe_count": _check_count,
            "ttl_seconds": TTL_SECONDS,
        }


# ── Internal probe ────────────────────────────────────────────────────────────

def _probe() -> ConnectivityStatus:
    """Attempt a HEAD request to the Firebase REST endpoint."""
    for url in (_PROBE_URL, _FALLBACK_URL):
        try:
            req = urllib.request.Request(url, method="HEAD")
            with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
                if resp.status < 500:
                    return ConnectivityStatus.ONLINE
        except Exception as exc:
            log.debug("connectivity probe to %s failed: %s", url, exc)

    return ConnectivityStatus.OFFLINE
