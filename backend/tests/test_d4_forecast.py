"""Unit tests for D4 – ForecastGenerator."""
import pytest
from datetime import date, timedelta
from app.agents.demand.d1_sales_history.agent import SalesHistoryAnalyzer
from app.agents.demand.d2_seasonality.agent import SeasonalityDetector
from app.agents.demand.d3_event_promotion.agent import EventPromotionSignalAgent
from app.agents.demand.d4_forecast.agent import ForecastGenerator
from app.agents.demand.d4_forecast.model_selector import ForecastModelSelector
import pandas as pd


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
def agent():
    return ForecastGenerator()


@pytest.fixture
def selector():
    return ForecastModelSelector()


class TestModelSelector:
    def test_selects_model_for_60d(self, d1, selector, transactions_60d):
        import numpy as np
        series = d1.run(transactions_60d, "P001", date.today())
        qty = np.array([e.qty for e in series.series])
        model, wape, mae = selector.select(qty, horizon=7, weekly_pattern={})
        assert model is not None
        assert 0.0 <= wape <= 10.0  # WAPE is a fraction
        assert mae >= 0.0

    def test_produces_forecast_array(self, d1, selector, transactions_60d):
        import numpy as np
        series = d1.run(transactions_60d, "P001", date.today())
        qty = np.array([e.qty for e in series.series])
        model, wape, mae = selector.select(qty, horizon=7, weekly_pattern={})
        fc = selector.produce_forecast(qty, model, horizon=7, weekly_pattern={})
        assert len(fc) == 7
        assert all(v >= 0 for v in fc)


class TestD4FullPipeline:
    def _run_chain(self, d1, d2, d3, transactions_60d, products_df, horizon=7):
        ref = date.today() - timedelta(days=1)  # use yesterday to avoid 0-filled today
        series = d1.run(transactions_60d, "P001", ref)
        seasonality = d2.run(series)
        signals = d3.run(pd.DataFrame(), pd.DataFrame(), series, products_df, ref)
        return series, seasonality, signals

    def test_returns_forecast(self, d1, d2, d3, agent, transactions_60d, products_df):
        series, seasonality, signals = self._run_chain(d1, d2, d3, transactions_60d, products_df)
        fc = agent.run(series, seasonality, signals, horizon=7, reference_date=date.today() - timedelta(days=1))
        assert fc is not None
        assert fc.product_id == "P001"

    def test_expected_qty_positive(self, d1, d2, d3, agent, transactions_60d, products_df):
        series, seasonality, signals = self._run_chain(d1, d2, d3, transactions_60d, products_df)
        fc = agent.run(series, seasonality, signals, horizon=7, reference_date=date.today() - timedelta(days=1))
        assert fc.expected_qty > 0

    def test_bounds_valid(self, d1, d2, d3, agent, transactions_60d, products_df):
        series, seasonality, signals = self._run_chain(d1, d2, d3, transactions_60d, products_df)
        fc = agent.run(series, seasonality, signals, horizon=7, reference_date=date.today() - timedelta(days=1))
        assert fc.lower_bound <= fc.expected_qty <= fc.upper_bound

    def test_confidence_in_range(self, d1, d2, d3, agent, transactions_60d, products_df):
        series, seasonality, signals = self._run_chain(d1, d2, d3, transactions_60d, products_df)
        fc = agent.run(series, seasonality, signals, horizon=7, reference_date=date.today() - timedelta(days=1))
        assert 0.0 <= fc.confidence <= 1.0

    def test_forecast_days_matches_horizon(self, d1, d2, d3, agent, transactions_60d, products_df):
        series, seasonality, signals = self._run_chain(d1, d2, d3, transactions_60d, products_df)
        fc = agent.run(series, seasonality, signals, horizon=7, reference_date=date.today() - timedelta(days=1))
        assert len(fc.forecast_days) == 7

    def test_different_horizons(self, d1, d2, d3, agent, transactions_60d, products_df):
        for h in [7, 14, 30]:
            series, seasonality, signals = self._run_chain(
                d1, d2, d3, transactions_60d, products_df, horizon=h
            )
            fc = agent.run(series, seasonality, signals, horizon=h, reference_date=date.today() - timedelta(days=1))
            assert len(fc.forecast_days) == h
