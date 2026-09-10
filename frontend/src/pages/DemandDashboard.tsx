import React, { useEffect, useState, useCallback } from 'react';
import { demandApi } from '../services/api';
import type {
  CleanDemandSeries,
  DemandForecast,
  DemandSignalAdjustment,
  ForecastQualityReport,
  MetricsEvaluation,
  SeasonalityProfile,
} from '../types/demand';
import { ForecastChart } from '../components/demand/ForecastChart';
import { SeasonalityChart } from '../components/demand/SeasonalityChart';
import { MetricCard } from '../components/demand/MetricCard';
import { StatusBadge } from '../components/demand/StatusBadge';
import { SignalsPanel } from '../components/demand/SignalsPanel';
import { ConnectivityBanner } from '../components/demand/ConnectivityBanner';

const PRODUCTS = [
  { id: 'P001', name: '500ml Water' },
  { id: 'P002', name: '2L Soft Drink' },
  { id: 'P003', name: 'White Bread' },
  { id: 'P004', name: 'Milk 1L' },
  { id: 'P005', name: 'Potato Chips' },
  { id: 'P006', name: 'Maize Meal' },
];

const MODEL_LABELS: Record<string, string> = {
  naive: 'Naïve',
  seasonal_naive: 'Seasonal Naïve',
  exponential_smoothing: 'Exp. Smoothing',
  holt_winters: 'Holt-Winters',
  regression: 'Regression',
  rolling_mean: 'Rolling Mean',
  insufficient_evidence: 'Insufficient Evidence',
};

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="bg-white rounded-xl shadow-sm border border-gray-100 p-5">
      <h3 className="text-sm font-semibold text-gray-600 uppercase tracking-wide mb-4">{title}</h3>
      {children}
    </div>
  );
}

function Spinner() {
  return (
    <div className="flex items-center justify-center h-32">
      <div className="w-8 h-8 border-4 border-blue-200 border-t-blue-500 rounded-full animate-spin" />
    </div>
  );
}

export function DemandDashboard() {
  const [selectedProduct, setSelectedProduct] = useState('P001');
  const [horizon, setHorizon] = useState(7);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [forecast, setForecast] = useState<DemandForecast | null>(null);
  const [series, setSeries] = useState<CleanDemandSeries | null>(null);
  const [seasonality, setSeasonality] = useState<SeasonalityProfile | null>(null);
  const [signals, setSignals] = useState<DemandSignalAdjustment[]>([]);
  const [quality, setQuality] = useState<ForecastQualityReport | null>(null);
  const [metrics, setMetrics] = useState<MetricsEvaluation | null>(null);
  const [running, setRunning] = useState(false);

  const fetchData = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [fc, ser, seas, sigs, qual] = await Promise.allSettled([
        demandApi.getForecast(selectedProduct, horizon),
        demandApi.getDemandSeries(selectedProduct),
        demandApi.getSeasonality(selectedProduct),
        demandApi.getSignals(selectedProduct),
        demandApi.getQuality(selectedProduct),
      ]);

      if (fc.status === 'fulfilled') setForecast(fc.value);
      if (ser.status === 'fulfilled') setSeries(ser.value);
      if (seas.status === 'fulfilled') setSeasonality(seas.value);
      if (sigs.status === 'fulfilled') setSignals(sigs.value);
      if (qual.status === 'fulfilled') setQuality(qual.value.report);
    } catch (e: any) {
      setError(e.message || 'Failed to load demand data');
    } finally {
      setLoading(false);
    }
  }, [selectedProduct, horizon]);

  useEffect(() => { fetchData(); }, [fetchData]);

  const handleRunPipeline = async () => {
    setRunning(true);
    try {
      await demandApi.runPipeline(horizon);
      await fetchData();
    } finally {
      setRunning(false);
    }
  };

  const qualityStatus = quality?.status === 'HEALTHY' ? 'healthy'
    : quality?.status === 'DEGRADED' ? 'degraded'
    : quality?.status === 'ALERT' ? 'alert'
    : 'insufficient';

  const forecastStatus = forecast?.status === 'HEALTHY' ? 'healthy'
    : forecast?.status === 'DEGRADED' ? 'degraded'
    : forecast?.status === 'ALERT' ? 'alert'
    : 'insufficient';

  return (
    <div className="min-h-screen bg-gray-50">
      {/* Offline / sync status banner */}
      <ConnectivityBanner />
      {/* Header */}
      <header className="bg-brand-900 text-white px-6 py-4 shadow-md">
        <div className="max-w-7xl mx-auto flex items-center justify-between">
          <div>
            <h1 className="text-xl font-bold tracking-tight">📊 Demand Sensing</h1>
            <p className="text-blue-200 text-sm mt-0.5">
              D1 · D2 · D3 · D4 · D5 — SMME Retail Intelligence
            </p>
          </div>
          <div className="flex items-center gap-3">
            <select
              className="bg-blue-800 border border-blue-600 text-white text-sm rounded-lg px-3 py-2 focus:outline-none"
              value={horizon}
              onChange={e => setHorizon(Number(e.target.value))}
            >
              <option value={7}>7-day horizon</option>
              <option value={14}>14-day horizon</option>
              <option value={30}>30-day horizon</option>
            </select>
            <button
              onClick={handleRunPipeline}
              disabled={running}
              className="bg-blue-500 hover:bg-blue-400 disabled:opacity-50 text-white text-sm font-medium px-4 py-2 rounded-lg transition-colors"
            >
              {running ? 'Running…' : '▶ Run Pipeline'}
            </button>
          </div>
        </div>
      </header>

      <main className="max-w-7xl mx-auto px-6 py-6 space-y-6">
        {/* Product tabs */}
        <div className="flex gap-2 flex-wrap">
          {PRODUCTS.map(p => (
            <button
              key={p.id}
              onClick={() => setSelectedProduct(p.id)}
              className={`px-4 py-2 rounded-full text-sm font-medium transition-colors ${
                selectedProduct === p.id
                  ? 'bg-blue-600 text-white shadow'
                  : 'bg-white text-gray-600 border border-gray-200 hover:bg-gray-50'
              }`}
            >
              {p.id} · {p.name}
            </button>
          ))}
        </div>

        {error && (
          <div className="bg-red-50 border border-red-200 rounded-lg p-4 text-red-700 text-sm">
            ⚠ {error} — Is the backend running? (<code>uvicorn app.main:app --reload</code>)
          </div>
        )}

        {/* Insufficient evidence warning */}
        {forecast?.insufficient_evidence && (
          <div className="bg-amber-50 border border-amber-300 rounded-lg p-4 text-amber-800 text-sm">
            <strong>⚠ Insufficient Evidence:</strong> {forecast.evidence_notes}
          </div>
        )}

        {/* KPI row */}
        <div className="grid grid-cols-2 md:grid-cols-4 lg:grid-cols-6 gap-4">
          <MetricCard
            label={`${horizon}d Forecast`}
            value={forecast ? `${forecast.expected_qty.toFixed(0)} units` : null}
            subtitle={`${forecast?.lower_bound.toFixed(0)}–${forecast?.upper_bound.toFixed(0)} range`}
            status={forecastStatus}
          />
          <MetricCard
            label="Confidence"
            value={forecast ? `${(forecast.confidence * 100).toFixed(0)}%` : null}
            status={
              (forecast?.confidence ?? 0) >= 0.7 ? 'healthy'
              : (forecast?.confidence ?? 0) >= 0.4 ? 'degraded' : 'alert'
            }
          />
          <MetricCard
            label="Model"
            value={forecast ? MODEL_LABELS[forecast.model] ?? forecast.model : null}
            status="neutral"
          />
          <MetricCard
            label="MAE"
            value={quality?.mae != null ? quality.mae.toFixed(2) : null}
            status={qualityStatus}
            subtitle="Mean Absolute Error"
          />
          <MetricCard
            label="WAPE"
            value={quality?.wape != null ? `${(quality.wape * 100).toFixed(1)}%` : null}
            status={qualityStatus}
            subtitle="Weighted Abs % Error"
          />
          <MetricCard
            label="Bias"
            value={quality?.bias != null ? `${quality.bias > 0 ? '+' : ''}${(quality.bias * 100).toFixed(1)}%` : null}
            subtitle={quality?.bias != null ? (quality.bias > 0.05 ? 'Over-forecasting' : quality.bias < -0.05 ? 'Under-forecasting' : 'Neutral') : undefined}
            status={
              quality?.bias == null ? 'neutral'
              : Math.abs(quality.bias) < 0.05 ? 'healthy'
              : Math.abs(quality.bias) < 0.15 ? 'degraded' : 'alert'
            }
          />
        </div>

        {/* Status row */}
        <div className="flex items-center gap-4 text-sm text-gray-600 bg-white rounded-lg px-4 py-3 border border-gray-100 shadow-sm">
          <span className="font-medium">Forecast quality:</span>
          {quality ? <StatusBadge status={quality.status} /> : <span className="text-gray-400">—</span>}
          {quality?.drift_detected && (
            <span className="text-amber-600 font-medium">⚡ Drift detected</span>
          )}
          {forecast?.data_freshness_days != null && (
            <span className={`ml-auto ${forecast.data_freshness_days > 3 ? 'text-amber-600' : 'text-gray-400'}`}>
              Data freshness: {forecast.data_freshness_days}d ago
            </span>
          )}
          {forecast?.drivers && forecast.drivers.length > 0 && (
            <div className="flex gap-2 flex-wrap">
              {forecast.drivers.map(d => (
                <span key={d} className="bg-blue-50 text-blue-700 text-xs px-2 py-0.5 rounded-full border border-blue-100">
                  {d}
                </span>
              ))}
            </div>
          )}
        </div>

        {/* Main chart */}
        <Section title="Demand Forecast — Historical + Projection">
          {loading ? <Spinner /> : (
            forecast && series ? (
              <ForecastChart
                historical={series.series}
                forecastDays={forecast.forecast_days}
                productId={selectedProduct}
              />
            ) : (
              <div className="text-gray-400 text-sm text-center py-8">No data available</div>
            )
          )}
        </Section>

        {/* Seasonality + Signals */}
        <div className="grid md:grid-cols-2 gap-6">
          <Section title="Seasonality Profile">
            {loading ? <Spinner /> : (
              seasonality ? (
                <>
                  <SeasonalityChart profile={seasonality} />
                  <div className="mt-4 grid grid-cols-2 gap-3 text-sm">
                    <div className="bg-gray-50 rounded-lg p-3">
                      <p className="text-xs text-gray-400 uppercase mb-1">Payday Effect</p>
                      <p className="text-lg font-bold text-gray-800">×{seasonality.payday_effect.toFixed(2)}</p>
                    </div>
                    <div className="bg-gray-50 rounded-lg p-3">
                      <p className="text-xs text-gray-400 uppercase mb-1">Seasonality Strength</p>
                      <p className="text-lg font-bold text-gray-800">{(seasonality.seasonality_strength * 100).toFixed(0)}%</p>
                    </div>
                  </div>
                  {seasonality.detected_patterns.length > 0 && (
                    <div className="mt-3 flex gap-2 flex-wrap">
                      {seasonality.detected_patterns.map(p => (
                        <span key={p} className="bg-indigo-50 text-indigo-700 text-xs px-2 py-1 rounded-full border border-indigo-100">
                          {p.replace('_', ' ')}
                        </span>
                      ))}
                    </div>
                  )}
                </>
              ) : <div className="text-gray-400 text-sm text-center py-8">No seasonality data</div>
            )}
          </Section>

          <Section title="Event & Promotion Signals">
            {loading ? <Spinner /> : <SignalsPanel signals={signals} />}
          </Section>
        </div>

        {/* Data diagnostics */}
        {series && (
          <Section title="Data Quality Diagnostics (D1)">
            <div className="grid grid-cols-2 md:grid-cols-5 gap-4 text-sm">
              {[
                { label: 'Observations', value: series.observations, good: true },
                { label: 'Clean', value: series.clean_observations, good: true },
                { label: 'Missing Dates', value: series.missing_dates, good: series.missing_dates === 0 },
                { label: 'Outliers', value: series.outliers_detected, good: series.outliers_detected === 0 },
                { label: 'Mean Daily Demand', value: series.mean_daily_demand.toFixed(1), good: true },
              ].map(item => (
                <div key={item.label} className="bg-gray-50 rounded-lg p-3">
                  <p className="text-xs text-gray-400 uppercase mb-1">{item.label}</p>
                  <p className={`text-xl font-bold ${item.good ? 'text-gray-800' : 'text-amber-600'}`}>
                    {item.value}
                  </p>
                </div>
              ))}
            </div>
          </Section>
        )}
      </main>

      <footer className="text-center text-xs text-gray-400 py-6">
        Demand Sensing Domain · Member 1 · SMME Retail Agentic AI System
      </footer>
    </div>
  );
}
