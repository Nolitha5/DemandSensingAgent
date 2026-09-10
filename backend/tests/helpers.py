"""Shared test helpers (not fixtures) that can be imported directly."""
import pandas as pd
import numpy as np
from datetime import date, timedelta


def make_transactions(
    product_id: str = "P001",
    n_days: int = 60,
    base_qty: float = 50.0,
    start: date | None = None,
    noise: float = 0.15,
) -> pd.DataFrame:
    """Generate synthetic daily transaction rows using D1's expected column names."""
    rng = np.random.default_rng(42)
    if start is None:
        # End yesterday so the series doesn't have a 0-filled "today" entry
        start = date.today() - timedelta(days=n_days + 1)
    dates = [start + timedelta(days=i) for i in range(n_days)]
    qty = base_qty + rng.normal(0, base_qty * noise, n_days)
    qty = np.clip(qty, 1, None)
    return pd.DataFrame(
        {
            "transaction_id": [f"T{i:04d}" for i in range(n_days)],
            "timestamp": [d.isoformat() for d in dates],
            "product_id": product_id,
            "store_id": "S001",
            "qty": qty.round(0).astype(int),
            "unit_price": 10.0,
        }
    )
