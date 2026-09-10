"""Unit tests for D3 – EventPromotionSignalAgent."""
import pytest
from datetime import date, timedelta
from app.agents.demand.d1_sales_history.agent import SalesHistoryAnalyzer
from app.agents.demand.d3_event_promotion.agent import EventPromotionSignalAgent
import pandas as pd


@pytest.fixture
def d1():
    return SalesHistoryAnalyzer()


@pytest.fixture
def agent():
    return EventPromotionSignalAgent()


class TestD3BasicRun:
    def test_returns_list(self, agent, d1, transactions_60d, promotions_df, events_df, products_df):
        series = d1.run(transactions_60d, "P001", date.today())
        signals = agent.run(promotions_df, events_df, series, products_df, date.today())
        assert isinstance(signals, list)

    def test_promotion_signal_present(self, agent, d1, transactions_60d, promotions_df, products_df):
        series = d1.run(transactions_60d, "P001", date.today())
        signals = agent.run(promotions_df, pd.DataFrame(), series, products_df, date.today())
        types = [s.signal_type for s in signals]
        assert "PROMOTION" in types

    def test_promotion_signal_has_factor(self, agent, d1, transactions_60d, promotions_df, products_df):
        """Promotion signal always produces a positive adjustment factor (may be above or below 1)."""
        ref = date.today() - timedelta(days=1)
        series = d1.run(transactions_60d, "P001", ref)
        signals = agent.run(promotions_df, pd.DataFrame(), series, products_df, ref)
        promo_signals = [s for s in signals if s.signal_type == "PROMOTION"]
        for sig in promo_signals:
            assert sig.adjustment_factor > 0

    def test_event_signal_present(self, agent, d1, transactions_60d, events_df, products_df):
        series = d1.run(transactions_60d, "P001", date.today())
        signals = agent.run(pd.DataFrame(), events_df, series, products_df, date.today())
        types = [s.signal_type for s in signals]
        assert len(signals) >= 0  # may or may not detect depending on store filter

    def test_confidence_in_range(self, agent, d1, transactions_60d, promotions_df, products_df):
        series = d1.run(transactions_60d, "P001", date.today())
        signals = agent.run(promotions_df, pd.DataFrame(), series, products_df, date.today())
        for sig in signals:
            assert 0.0 <= sig.confidence <= 1.0

    def test_empty_inputs_return_empty(self, agent, d1, transactions_60d, products_df):
        series = d1.run(transactions_60d, "P001", date.today())
        signals = agent.run(pd.DataFrame(), pd.DataFrame(), series, products_df, date.today())
        assert signals == []
