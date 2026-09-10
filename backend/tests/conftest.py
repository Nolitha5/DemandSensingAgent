"""Shared fixtures for all demand-sensing tests."""
import pytest
import pandas as pd
import numpy as np
from datetime import date, timedelta
from helpers import make_transactions
from datetime import date, timedelta

# Reference date: yesterday — avoids a zero-filled "today" at the end of the series
REF_DATE = date.today() - timedelta(days=1)


def make_transactions_sparse(product_id: str = "P001", n_days: int = 5) -> pd.DataFrame:
    """Generate too-few rows to exceed min-observation threshold."""
    return make_transactions(product_id=product_id, n_days=n_days)


@pytest.fixture
def transactions_60d():
    return make_transactions(n_days=60)


@pytest.fixture
def transactions_sparse():
    return make_transactions_sparse()


@pytest.fixture
def transactions_with_bad_data():
    """Include rows with negative qty (BAD-001), missing qty (BAD-002)."""
    df = make_transactions(n_days=60)
    # Inject bad rows
    bad = pd.DataFrame(
        [
            {
                "transaction_id": "BAD001",
                "timestamp": (date.today() - timedelta(days=6)).isoformat(),
                "product_id": "P001",
                "store_id": "S001",
                "qty": -10,
                "unit_price": 10.0,
            },
            {
                "transaction_id": "BAD002",
                "timestamp": (date.today() - timedelta(days=4)).isoformat(),
                "product_id": "P001",
                "store_id": "S001",
                "qty": None,
                "unit_price": 10.0,
            },
        ]
    )
    return pd.concat([df, bad], ignore_index=True)


@pytest.fixture
def promotions_df():
    today = date.today()
    return pd.DataFrame(
        [
            {
                "promo_id": "PR001",
                "product_id": "P001",
                "store_id": "S001",
                "value": 20,           # discount_pct — matches CSV loader column name
                "start": (today - timedelta(days=10)).isoformat(),
                "end": (today - timedelta(days=5)).isoformat(),
                "promo_type": "DISCOUNT",
            }
        ]
    )


@pytest.fixture
def events_df():
    today = date.today()
    return pd.DataFrame(
        [
            {
                "event_id": "EV001",
                "event_name": "Market Day",
                "event_type": "MARKET_DAY",
                "store_id": "S001",
                "event_date": (today + timedelta(days=2)).isoformat(),
            }
        ]
    )


@pytest.fixture
def products_df():
    return pd.DataFrame(
        [
            {"product_id": "P001", "product_name": "500ml Water", "category": "Beverages"},
            {"product_id": "P002", "product_name": "2L Soft Drink", "category": "Beverages"},
        ]
    )
