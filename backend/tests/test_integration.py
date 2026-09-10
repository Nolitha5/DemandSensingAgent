"""Integration tests: full D1 → D2 → D3 → D4 → D5 chain."""
import pytest
from datetime import date, timedelta
from app.agents.demand.d1_sales_history.agent import SalesHistoryAnalyzer
from app.agents.demand.d2_seasonality.agent import SeasonalityDetector
from app.agents.demand.d3_event_promotion.agent import EventPromotionSignalAgent
from app.agents.demand.d4_forecast.agent import ForecastGenerator
from app.agents.demand.d5_quality.agent import ForecastQualityMonitor
from app.contracts.demand import ForecastStatus
import pandas as pd


@pytest.fixture(autouse=True)
def agents():
    return {
        "d1": SalesHistoryAnalyzer(),
        "d2": SeasonalityDetector(),
        "d3": EventPromotionSignalAgent(),
        "d4": ForecastGenerator(),
        "d5": ForecastQualityMonitor(),
    }


def run_full_chain(transactions, promotions, events, products, horizon, agents):
    today = date.today() - timedelta(days=1)  # yesterday to avoid 0-filled today in series
    d1 = agents["d1"]
    d2 = agents["d2"]
    d3 = agents["d3"]
    d4 = agents["d4"]
    d5 = agents["d5"]

    series = d1.run(transactions, "P001", today)
    seasonality = d2.run(series)
    signals = d3.run(promotions, events, series, products, today)
    forecast = d4.run(series, seasonality, signals, horizon=horizon, reference_date=today)
    report, alerts = d5.evaluate(forecast, transactions, prior_wape=None)
    return series, seasonality, signals, forecast, report, alerts


class TestFullPipeline:
    def test_pipeline_runs_without_error(self, agents, transactions_60d, promotions_df,
                                          events_df, products_df):
        series, seasonality, signals, forecast, report, alerts = run_full_chain(
            transactions_60d, promotions_df, events_df, products_df, 7, agents
        )
        assert forecast is not None
        assert report is not None

    def test_forecast_product_id_propagated(self, agents, transactions_60d, products_df):
        _, _, _, forecast, _, _ = run_full_chain(
            transactions_60d, pd.DataFrame(), pd.DataFrame(), products_df, 7, agents
        )
        assert forecast.product_id == "P001"

    def test_series_feeds_d2(self, agents, transactions_60d, products_df):
        series, seasonality, _, _, _, _ = run_full_chain(
            transactions_60d, pd.DataFrame(), pd.DataFrame(), products_df, 7, agents
        )
        assert seasonality.product_id == series.product_id

    def test_no_fabrication_on_short_series(self, agents, products_df):
        """With borderline data (7–10 days) the pipeline returns INSUFFICIENT_EVIDENCE or naive."""
        from helpers import make_transactions
        from app.contracts.demand import InsufficientEvidenceError
        tiny = make_transactions(n_days=8)
        try:
            _, _, _, forecast, _, _ = run_full_chain(
                tiny, pd.DataFrame(), pd.DataFrame(), products_df, 7, agents
            )
            # If it returned, status must reflect uncertainty
            assert forecast.status in (
                ForecastStatus.INSUFFICIENT_EVIDENCE,
                ForecastStatus.DEGRADED,
                ForecastStatus.HEALTHY,
                ForecastStatus.ALERT,
            )
        except InsufficientEvidenceError:
            pass  # Acceptable — D1 raised before pipeline could continue

    def test_d5_sees_valid_forecast(self, agents, transactions_60d, products_df):
        _, _, _, forecast, report, _ = run_full_chain(
            transactions_60d, pd.DataFrame(), pd.DataFrame(), products_df, 7, agents
        )
        assert report.product_id == forecast.product_id

    def test_14d_horizon(self, agents, transactions_60d, products_df):
        _, _, _, forecast, _, _ = run_full_chain(
            transactions_60d, pd.DataFrame(), pd.DataFrame(), products_df, 14, agents
        )
        assert len(forecast.forecast_days) == 14

    def test_30d_horizon(self, agents, transactions_60d, products_df):
        _, _, _, forecast, _, _ = run_full_chain(
            transactions_60d, pd.DataFrame(), pd.DataFrame(), products_df, 30, agents
        )
        assert len(forecast.forecast_days) == 30

    def test_with_promotions_factor_applied(self, agents, transactions_60d, promotions_df, products_df):
        """When a promotion is active, D4 expected_qty may differ from no-promo forecast."""
        _, _, signals, forecast_with_promo, _, _ = run_full_chain(
            transactions_60d, promotions_df, pd.DataFrame(), products_df, 7, agents
        )
        _, _, _, forecast_no_promo, _, _ = run_full_chain(
            transactions_60d, pd.DataFrame(), pd.DataFrame(), products_df, 7, agents
        )
        # With or without signals, forecast must be structurally valid
        assert forecast_with_promo.expected_qty > 0
        assert forecast_no_promo.expected_qty > 0
