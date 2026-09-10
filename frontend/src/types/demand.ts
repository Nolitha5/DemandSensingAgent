// Shared TypeScript types matching Pydantic contracts

export interface ForecastDay {
  date: string;
  expected_qty: number;
  lower_bound: number;
  upper_bound: number;
}

export type ModelName =
  | 'naive'
  | 'seasonal_naive'
  | 'exponential_smoothing'
  | 'holt_winters'
  | 'regression'
  | 'rolling_mean'
  | 'insufficient_evidence';

export type ForecastStatus = 'HEALTHY' | 'DEGRADED' | 'ALERT' | 'INSUFFICIENT_EVIDENCE';

export interface DemandForecast {
  product_id: string;
  horizon: number;
  expected_qty: number;
  lower_bound: number;
  upper_bound: number;
  confidence: number;
  drivers: string[];
  model: ModelName;
  forecast_days: ForecastDay[];
  status: ForecastStatus;
  insufficient_evidence: boolean;
  evidence_notes: string;
  generated_at: string;
  data_freshness_days: number | null;
}

export interface SeasonalityProfile {
  product_id: string;
  weekly_pattern: Record<string, number>;
  payday_effect: number;
  monthly_pattern: Record<string, number>;
  holiday_effect: number;
  seasonality_strength: number;
  confidence: number;
  detected_patterns: string[];
  data_sufficient: boolean;
  generated_at: string;
}

export interface DemandSignalAdjustment {
  product_id: string;
  signal_type: 'LOCAL_EVENT' | 'PROMOTION' | 'HOLIDAY' | 'MARKET_DAY';
  event_id?: string;
  promo_id?: string;
  adjustment_factor: number;
  confidence: number;
  reason: string;
  active_from?: string;
  active_to?: string;
}

export interface ForecastQualityReport {
  product_id: string;
  horizon: number;
  mae: number | null;
  wape: number | null;
  bias: number | null;
  drift_detected: boolean;
  coverage_ok: boolean;
  actuals_count: number;
  status: ForecastStatus;
  notes: string;
  evaluated_at: string;
}

export interface DemandPoint {
  date: string;
  qty: number;
  is_outlier: boolean;
  is_interpolated: boolean;
}

export interface CleanDemandSeries {
  product_id: string;
  observations: number;
  clean_observations: number;
  missing_dates: number;
  outliers_detected: number;
  mean_daily_demand: number;
  std_daily_demand: number;
  series: DemandPoint[];
  generated_at: string;
}

export interface MetricsEvaluation {
  products_evaluated: number;
  alerts_generated: number;
  summary: Record<string, {
    forecast_status: ForecastStatus;
    model: ModelName;
    confidence: number;
    expected_qty_7d: number;
    mae: number | null;
    wape: number | null;
    bias: number | null;
    quality_status: ForecastStatus | null;
  }>;
}
