import axios from 'axios';
import type {
  CleanDemandSeries,
  DemandForecast,
  DemandSignalAdjustment,
  ForecastQualityReport,
  MetricsEvaluation,
  SeasonalityProfile,
} from '../types/demand';

const BASE = import.meta.env.VITE_API_URL ||  'https://demandsensingagent.onrender.com';
const api = axios.create({ baseURL: BASE, timeout: 30000 });

export const demandApi = {
  getForecast: (productId: string, horizon = 7): Promise<DemandForecast> =>
    api.get(`/demand/forecast/${productId}`, { params: { horizon } }).then(r => r.data),

  getAllForecasts: (horizon = 7): Promise<Record<string, DemandForecast>> =>
    api.get('/demand/forecast', { params: { horizon } }).then(r => r.data),

  getSeasonality: (productId: string): Promise<SeasonalityProfile> =>
    api.get(`/demand/seasonality/${productId}`).then(r => r.data),

  getSignals: (productId: string): Promise<DemandSignalAdjustment[]> =>
    api.get(`/demand/signals/${productId}`).then(r => r.data),

  getQuality: (productId: string): Promise<{ report: ForecastQualityReport; alerts: any[] }> =>
    api.get(`/demand/quality/${productId}`).then(r => r.data),

  getDemandSeries: (productId: string): Promise<CleanDemandSeries> =>
    api.get(`/demand/series/${productId}`).then(r => r.data),

  getMetrics: (): Promise<MetricsEvaluation> =>
    api.get('/metrics/evaluation').then(r => r.data),

  runD1: (productId?: string) =>
    api.post('/agents/D1/run', null, { params: productId ? { product_id: productId } : {} }).then(r => r.data),

  runD4: (productId: string, horizon = 7): Promise<DemandForecast> =>
    api.post('/agents/D4/run', null, { params: { product_id: productId, horizon } }).then(r => r.data),

  runPipeline: (horizon = 7) =>
    api.post('/demand/pipeline/run', null, { params: { horizon } }).then(r => r.data),

  agentStatus: () =>
    api.get('/agents/status').then(r => r.data),
};
