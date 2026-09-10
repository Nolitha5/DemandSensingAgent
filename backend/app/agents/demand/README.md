# Demand Sensing Agents — D1 through D5

Member 1 responsibility · SMME Retail Agentic AI System

---

## Agent overview

| Agent | Class | Input | Output |
|---|---|---|---|
| **D1** SalesHistoryAnalyzer | `d1_sales_history/agent.py` | Raw transactions CSV | `CleanDemandSeries` |
| **D2** SeasonalityDetector | `d2_seasonality/agent.py` | `CleanDemandSeries` | `SeasonalityProfile` |
| **D3** EventPromotionSignalAgent | `d3_event_promotion/agent.py` | Promotions + Events CSVs + `CleanDemandSeries` | `List[DemandSignalAdjustment]` |
| **D4** ForecastGenerator | `d4_forecast/agent.py` | D1 + D2 + D3 outputs | `DemandForecast` |
| **D5** ForecastQualityMonitor | `d5_quality/agent.py` | `DemandForecast` + actuals | `ForecastQualityReport` |

---

## SMME constraints

- Works with as few as **7 days** of history (D1 enforces this minimum)
- Never fabricates demand — returns `INSUFFICIENT_EVIDENCE` sentinel when data is sparse
- Model tier selection by data volume:
  - ≥ 7 obs → Naïve / Rolling Mean
  - ≥ 14 obs → Seasonal Naïve
  - ≥ 28 obs → Exponential Smoothing
  - ≥ 60 obs → Holt-Winters

## Stable contract

`DemandForecast` (in `app/contracts/demand.py`) is the only interface exposed to downstream teams (Inventory Agent, Pricing Agent, Procurement Agent). Internal agent types are private.

## Data quality rules

| Code | Rule | Handling |
|---|---|---|
| BAD-001 | Negative qty | Remove |
| BAD-002 | Missing qty | Remove |
| BAD-003 | Statistical outlier (IQR + Z-score) | Winsorize (cap, not drop) |
| BAD-004 | Missing date | Remove |
| BAD-005 | Duplicate transaction | Keep first |

## Running agents individually (dev / debug)

```python
from app.data.loader import load_transactions, load_products
from app.agents.demand.d1_sales_history.agent import SalesHistoryAnalyzer
from datetime import date

txn = load_transactions()
series = SalesHistoryAnalyzer().run(txn, "P001", date.today())
print(series)
```
