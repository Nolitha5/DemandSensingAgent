# Demand Sensing Domain — Architecture

Member 1 · SMME Retail Agentic AI System

---

## Agent pipeline

```mermaid
flowchart TD
    CSV[("📂 CSV Data\n(transactions, promotions,\nevents, calendar)")]

    D1["D1 – Sales History Analyzer\nCleans & validates raw sales\nDetects outliers (IQR + Z-score)\nAggregates to daily series"]

    D2["D2 – Seasonality Detector\nWeekly seasonal indices\nPayday effect\nAutocorrelation (lag-7)"]

    D3["D3 – Event & Promotion Signal Agent\nHistorical promo uplift\nEvent window comparison\nRule-based fallback table"]

    D4["D4 – Forecast Generator\nModel selection by data volume\n(Naïve → Holt-Winters)\nConfidence scoring\nSignal application"]

    D5["D5 – Forecast Quality Monitor\nMAE / WAPE / Bias\nDrift detection\nAlert generation"]

    CONTRACT[["🔒 DemandForecast\n(Stable published contract)\nConsumed by Inventory,\nPricing, Procurement"]]

    CSV --> D1
    D1 --> D2
    D1 --> D3
    D1 --> D4
    D2 --> D4
    D3 --> D4
    D4 --> D5
    D4 --> CONTRACT

    style CONTRACT fill:#dbeafe,stroke:#2563eb,stroke-width:2px
    style D4 fill:#f0fdf4,stroke:#16a34a
```

---

## Five-Layer Reference Architecture

```mermaid
flowchart TB
    H["🧑 Human Interface Layer\nReact/Vite Dashboard\n(DemandDashboard.tsx)"]
    A["🤖 Agent Layer\nD1 D2 D3 D4 D5\nStateless deterministic agents"]
    C["🔄 Coordination Layer\nEventBus (pub/sub)\nDemandCoordinator (gate: D4 fires when D1+D2+D3 done)"]
    I["🗄 Information Layer\nFirestore Repository\nDemandRepository (versioned forecasts)"]
    G["🛡 Governance Layer\nPydantic contracts\nInsufficientEvidenceError sentinel\nConfig thresholds"]

    H --> A --> C --> I --> G
```

---

## Data quality pipeline (D1)

```mermaid
flowchart LR
    RAW[Raw transactions] --> V1{qty < 0?}
    V1 -->|Remove| V2{qty is null?}
    V1 -->|Keep| V2
    V2 -->|Remove| V3{Duplicate?}
    V2 -->|Keep| V3
    V3 -->|Keep first| OUT1[De-duplicated rows]
    V3 -->|Keep| OUT1
    OUT1 --> OD[Outlier detection\nIQR + Z-score]
    OD -->|Winsorize| AGG[Daily aggregation]
    AGG --> FILL[Fill missing dates with 0]
    FILL --> CHECK{obs >= 7?}
    CHECK -->|No| ERR[InsufficientEvidenceError]
    CHECK -->|Yes| SERIES[CleanDemandSeries ✓]
```

---

## Model selection (D4)

| Observations | Model candidates |
|---|---|
| ≥ 7 | Naïve, Rolling Mean |
| ≥ 14 | + Seasonal Naïve |
| ≥ 28 | + Exponential Smoothing |
| ≥ 60 | + Holt-Winters |

Selection criterion: lowest WAPE on 7-day backtest window.

---

## API endpoints summary

| Method | Path | Description |
|---|---|---|
| `POST` | `/demand/pipeline/run` | Run full D1→D5 for all products |
| `GET` | `/demand/forecast/{product_id}` | Latest forecast |
| `GET` | `/demand/series/{product_id}` | Clean demand series |
| `GET` | `/demand/seasonality/{product_id}` | Seasonality profile |
| `GET` | `/demand/signals/{product_id}` | Active signals |
| `GET` | `/demand/quality/{product_id}` | Quality report |
| `GET` | `/metrics/evaluation` | Cross-product WAPE/MAE table |
| `POST` | `/agents/D1/run` | Run D1 only |
| `POST` | `/agents/D2/run` | Run D2 only |
| `POST` | `/agents/D3/run` | Run D3 only |
| `POST` | `/agents/D4/run` | Run D4 only |
| `POST` | `/agents/D5/run` | Run D5 only |
