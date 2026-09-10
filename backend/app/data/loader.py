"""
CSV data loader — used for local development and seeding Firestore.
In production, data comes directly from Firestore repositories.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

import pandas as pd

from app.core.config import get_settings

_settings = get_settings()
_DATA_DIR = (
    Path(__file__).resolve().parent
    / "demand_sensing_mock_data"
)


def _csv(name: str) -> Path:
    return _DATA_DIR / name


def load_transactions(data_dir: Optional[str] = None) -> pd.DataFrame:
    path = Path(data_dir) / "transactions.csv" if data_dir else _csv("transactions.csv")
    df = pd.read_csv(path, parse_dates=["timestamp"])
    df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce")
    return df


def load_products(data_dir: Optional[str] = None) -> pd.DataFrame:
    path = Path(data_dir) / "products.csv" if data_dir else _csv("products.csv")
    return pd.read_csv(path)


def load_promotions(data_dir: Optional[str] = None) -> pd.DataFrame:
    path = Path(data_dir) / "promotions.csv" if data_dir else _csv("promotions.csv")
    df = pd.read_csv(path, parse_dates=["start", "end"])
    return df


def load_local_events(data_dir: Optional[str] = None) -> pd.DataFrame:
    path = Path(data_dir) / "local_events.csv" if data_dir else _csv("local_events.csv")
    df = pd.read_csv(path, parse_dates=["date"])
    return df


def load_calendar(data_dir: Optional[str] = None) -> pd.DataFrame:
    path = Path(data_dir) / "calendar.csv" if data_dir else _csv("calendar.csv")
    df = pd.read_csv(path, parse_dates=["date"])
    return df
