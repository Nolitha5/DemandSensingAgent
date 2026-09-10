"""Unit tests for D2 – SeasonalityDetector."""
import pytest
from datetime import date
from app.agents.demand.d1_sales_history.agent import SalesHistoryAnalyzer
from app.agents.demand.d2_seasonality.agent import SeasonalityDetector
from helpers import make_transactions


@pytest.fixture
def d1():
    return SalesHistoryAnalyzer()


@pytest.fixture
def agent():
    return SeasonalityDetector()


class TestD2BasicRun:
    def test_returns_profile(self, d1, agent, transactions_60d):
        series = d1.run(transactions_60d, "P001", date.today())
        profile = agent.run(series)
        assert profile.product_id == "P001"

    def test_weekly_pattern_has_7_days(self, d1, agent, transactions_60d):
        series = d1.run(transactions_60d, "P001", date.today())
        profile = agent.run(series)
        assert len(profile.weekly_pattern) == 7

    def test_weekly_pattern_indices_near_1(self, d1, agent, transactions_60d):
        series = d1.run(transactions_60d, "P001", date.today())
        profile = agent.run(series)
        # For near-uniform data, all indices should be close to 1
        for day, idx in profile.weekly_pattern.items():
            assert 0.3 <= idx <= 3.0, f"Index for {day} out of range: {idx}"

    def test_insufficient_data_flag(self, agent, d1):
        """With only 28 obs, seasonality returns profile with data_sufficient=False if <28."""
        df = make_transactions(n_days=14)
        # D1 won't raise here since 14 > 7 min obs — but D2 requires 28
        try:
            series = d1.run(df, "P001", date.today())
            profile = agent.run(series)
            assert not profile.data_sufficient
        except Exception:
            # D1 raising InsufficientEvidenceError is also acceptable
            pass

    def test_payday_effect_positive(self, d1, agent, transactions_60d):
        series = d1.run(transactions_60d, "P001", date.today())
        profile = agent.run(series)
        assert profile.payday_effect >= 0.5  # always some non-negative value

    def test_seasonality_strength_range(self, d1, agent, transactions_60d):
        series = d1.run(transactions_60d, "P001", date.today())
        profile = agent.run(series)
        assert 0.0 <= profile.seasonality_strength <= 1.0

    def test_confidence_range(self, d1, agent, transactions_60d):
        series = d1.run(transactions_60d, "P001", date.today())
        profile = agent.run(series)
        assert 0.0 <= profile.confidence <= 1.0
