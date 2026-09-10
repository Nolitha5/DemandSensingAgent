# Demand Sensing Domain — Setup Guide

Member 1 · SMME Retail Agentic AI System

---

## Prerequisites

| Tool | Version |
|---|---|
| Python | 3.11+ |
| Node.js | 20+ |
| npm | 10+ |
| Docker + Docker Compose | optional, for containerised run |
| Firebase service account key | required for Firestore |

---

## 1 — Clone / open the project

```bash
# The project lives at:
# C:\Users\FiLo\Documents\SMMEs\DemandSensingAgenticDomain\retail-agent-system
cd retail-agent-system
```

---

## 2 — Firebase service account key

1. Open [Firebase Console](https://console.firebase.google.com/) → Project `smmes-7adc8`
2. **Project Settings → Service Accounts → Generate new private key**
3. Save the downloaded JSON as `firebase-credentials.json` in the project root
4. **Never commit this file** (it is already in `.gitignore`)

---

## 3 — Copy CSV data files

Copy the contents of your `demand_sensing_mock_data/` folder into `backend/data/csv/`:

```
backend/data/csv/
  transactions.csv
  products.csv
  promotions.csv
  local_events.csv
  south_africa_calendar.csv
```

---

## 4 — Environment variables

```bash
cp .env.example .env
# Edit .env — set FIREBASE_CREDENTIALS_PATH and DATA_DIR
```

---

## 5A — Run with Docker Compose (recommended)

```bash
docker-compose up --build
```

- Backend: http://localhost:8000
- Frontend: http://localhost:3000
- API docs: http://localhost:8000/docs

---

## 5B — Run locally (dev mode)

### Backend

```bash
cd backend
pip install -r requirements.txt

# Set env vars (or use .env via python-dotenv)
export FIREBASE_CREDENTIALS_PATH=../firebase-credentials.json
export DATA_DIR=./data/csv

uvicorn app.main:app --reload --port 8000
```

### Frontend

```bash
cd frontend
npm install
npm run dev
# Opens at http://localhost:5173
```

---

## 6 — Run the pipeline

### Via the dashboard

1. Open http://localhost:5173
2. Select a product tab (P001–P006)
3. Choose a forecast horizon (7 / 14 / 30 days)
4. Click **▶ Run Pipeline**

### Via API

```bash
# Run full pipeline for all products, 7-day horizon
curl -X POST "http://localhost:8000/demand/pipeline/run?horizon=7"

# Get forecast for P001
curl "http://localhost:8000/demand/forecast/P001"
```

---

## 7 — Run tests

### Backend (pytest)

```bash
cd backend
pytest -v
```

### Frontend (vitest)

```bash
cd frontend
npm test
```

---

## 8 — Project structure

```
retail-agent-system/
├── backend/
│   ├── app/
│   │   ├── agents/demand/
│   │   │   ├── d1_sales_history/   D1 – cleans & validates
│   │   │   ├── d2_seasonality/     D2 – weekly indices + payday
│   │   │   ├── d3_event_promotion/ D3 – promo & event signals
│   │   │   ├── d4_forecast/        D4 – model selection + forecast
│   │   │   └── d5_quality/         D5 – MAE / WAPE / bias / drift
│   │   ├── api/                    FastAPI router
│   │   ├── contracts/              Stable published Pydantic types
│   │   ├── coordination/           EventBus + DemandCoordinator
│   │   ├── core/                   Config (pydantic-settings)
│   │   ├── data/                   CSV loaders
│   │   ├── repositories/           Firestore layer
│   │   └── services/               Stateless orchestration
│   ├── tests/                      pytest suite
│   ├── Dockerfile
│   └── requirements.txt
├── frontend/
│   ├── src/
│   │   ├── components/demand/      ForecastChart, SeasonalityChart, …
│   │   ├── pages/                  DemandDashboard.tsx
│   │   ├── services/               api.ts (axios client)
│   │   └── types/                  demand.ts (TypeScript interfaces)
│   ├── Dockerfile
│   └── package.json
├── docs/
│   └── demand-sensing-architecture.md   Mermaid diagrams
├── .github/workflows/ci.yml
├── docker-compose.yml
├── .env.example
└── SETUP.md
```

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| "⚠ Failed to load demand data — Is the backend running?" | Start `uvicorn app.main:app --reload` in `backend/` |
| Firestore permission error on first run | Check `FIREBASE_CREDENTIALS_PATH` points to the correct service account key |
| "Insufficient Evidence" banner for all products | Run the pipeline first via ▶ Run Pipeline or `POST /demand/pipeline/run` |
| CSV not found | Confirm `DATA_DIR` in `.env` matches where you placed the CSV files |
