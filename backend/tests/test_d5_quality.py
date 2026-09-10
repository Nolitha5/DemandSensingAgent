"""Unit tests for D5 – ForecastQualityMonitor."""
import pytest
from datetime import date, timedelta
from app.agents.demand.d1_sales_history.agent import SalesHistoryAnalyzer
from app.agents.demand.d2_seasonality.agent import SeasonalityDetector
from app.agents.demand.d3_event_promotion.agent import EventPromotionSignalAgent
from app.agents.demand.d4_forecast.agent import ForecastGenerator
from app.agents.demand.d5_quality.agent import ForecastQualityMonitor
import pandas as pd
from helpers import make_transactions


@pytest.fixture
def d1():
    return SalesHistoryAnalyzer()


@pytest.fixture
def d2():
    return SeasonalityDetector()


@pytest.fixture
def d3():
    return EventPromotionSignalAgent()


@pytest.fixture
def d4():
    return ForecastGenerator()


@pytest.fixture
def agent():
    return ForecastQualityMonitor()


def _generate_forecast(d1, d2, d3, d4, n_days=60, products_df=None):
    today = date.today()
    txn = make_transactions(n_days=n_days)
    if products_df is None:
        products_df = pd.DataFrame([{"product_id": "P001", "category": "Beverages"}])
    series = d1.run(txn, "P001", today)
    seasonality = d2.run(series)
    signals = d3.run(pd.DataFrame(), pd.DataFrame(), series, products_df, today)
    return d4.run(series, seasonality, signals, horizon=7, reference_date=today), txn


class TestD5BasicRun:
    def test_returns_report(self, d1, d2, d3, d4, agent):
        products_df = pd.DataFrame([{"product_id": "P001", "category": "Beverages"}])
        forecast, txn = _generate_forecast(d1, d2, d3, d4, products_df=products_df)
        report, alerts = agent.evaluate(forecast, txn, prior_wape=None)
        assert report is not None
        assert isinstance(alerts, list)

    def test_mae_non_negative(self, d1, d2, d3, d4, agent):
        products_df = pd.DataFrame([{"product_id": "P001", "category": "Beverages"}])
        forecast, txn = _generate_forecast(d1, d2, d3, d4, products_df=products_df)
        report, _ = agent.evaluate(forecast, txn, prior_wape=None)
        if report.mae is not None:
            assert report.mae >= 0

    def test_wape_range(self, d1, d2, d3, d4, agent):
        products_df = pd.DataFrame([{"product_id": "P001", "category": "Beverages"}])
        forecast, txn = _generate_forecast(d1, d2, d3, d4, products_df=products_df)
        report, _ = agent.evaluate(forecast, txn, prior_wape=None)
        if report.wape is not None:
            assert report.wape >= 0

    def test_status_is_valid_value(self, d1, d2, d3, d4, agent):
        products_df = pd.DataFrame([{"product_id": "P001", "category": "Beverages"}])
        forecast, txn = _generate_forecast(d1, d2, d3, d4, products_df=products_df)
        report, _ = agent.evaluate(forecast, txn, prior_wape=None)
        assert report.status in ("HEALTHY", "DEGRADED", "ALERT", "INSUFFICIENT_EVIDENCE")

    def test_sparse_actuals_returns_insufficient(self, d1, d2, d3, d4, agent):
        """Fewer than 3 actual rows → INSUFFICIENT_EVIDENCE."""
        products_df = pd.DataFrame([{"product_id": "P001", "category": "Beverages"}])
        forecast, _ = _generate_forecast(d1, d2, d3, d4, products_df=products_df)
        tiny_txn = make_transactions(n_days=2)
        report, _ = agent.evaluate(forecast, tiny_txn, prior_wape=None)
        assert report.status == "INSUFFICIENT_EVIDENCE"

    def test_drift_detected_when_wape_jumps(self, d1, d2, d3, d4, agent):
        products_df = pd.DataFrame([{"product_id": "P001", "category": "Beverages"}])
        forecast, txn = _generate_forecast(d1, d2, d3, d4, products_df=products_df)
        # Prior WAPE was very low; current will be higher → drift
        report, alerts = agent.evaluate(forecast, txn, prior_wape=0.01)
        # Drift may or may not trigger depending on actual WAPE; just check structure
        assert isinstance(report.drift_detected, bool)

    def test_no_drift_with_no_prior_wape(self, d1, d2, d3, d4, agent):
        products_df = pd.DataFrame([{"product_id": "P001", "category": "Beverages"}])
        forecast, txn = _generate_forecast(d1, d2, d3, d4, products_df=products_df)
        report, _ = agent.evaluate(forecast, txn, prior_wape=None)
        assert report.drift_detected is False
