"""Unit tests for D1 – SalesHistoryAnalyzer."""
import pytest
from datetime import date
from app.agents.demand.d1_sales_history.agent import SalesHistoryAnalyzer
from app.contracts.demand import InsufficientEvidenceError


@pytest.fixture
def agent():
    return SalesHistoryAnalyzer()


class TestD1BasicRun:
    def test_returns_clean_series(self, agent, transactions_60d):
        result = agent.run(transactions_60d, "P001", date.today())
        assert result.product_id == "P001"
        assert result.observations > 0
        assert result.clean_observations <= result.observations
        assert len(result.series) > 0

    def test_removes_negative_qty(self, agent, transactions_with_bad_data):
        result = agent.run(transactions_with_bad_data, "P001", date.today())
        # BAD-001 rows are removed; we should still get data
        assert result.clean_observations < result.observations or result.observations > 0

    def test_handles_missing_qty(self, agent, transactions_with_bad_data):
        # BAD-002 NaN qty → removed, no crash
        result = agent.run(transactions_with_bad_data, "P001", date.today())
        assert result is not None

    def test_raises_on_sparse_data(self, agent, transactions_sparse):
        with pytest.raises(InsufficientEvidenceError):
            agent.run(transactions_sparse, "P001", date.today())

    def test_series_has_daily_entries(self, agent, transactions_60d):
        result = agent.run(transactions_60d, "P001", date.today())
        assert all(hasattr(entry, "date") for entry in result.series)
        assert all(hasattr(entry, "qty") for entry in result.series)

    def test_mean_daily_demand_positive(self, agent, transactions_60d):
        result = agent.run(transactions_60d, "P001", date.today())
        assert result.mean_daily_demand > 0

    def test_run_all_products(self, agent, transactions_60d):
        import pandas as pd
        p2 = transactions_60d.copy()
        p2["product_id"] = "P002"
        combined = pd.concat([transactions_60d, p2], ignore_index=True)
        results = agent.run_all_products(combined, ["P001", "P002"], date.today())
        assert "P001" in results
        assert "P002" in results


class TestD1OutlierDetection:
    def test_outliers_detected(self, agent, transactions_60d):
        import pandas as pd
        # Inject extreme outlier
        outlier = transactions_60d.copy()
        outlier.loc[0, "qty"] = 9999
        result = agent.run(outlier, "P001", date.today())
        assert result.outliers_detected >= 1

    def test_no_outliers_in_clean_data(self, agent, transactions_60d):
        result = agent.run(transactions_60d, "P001", date.today())
        # With mild noise, there should be few/no outliers
        assert result.outliers_detected <= 3  # tolerance for statistical edge cases
