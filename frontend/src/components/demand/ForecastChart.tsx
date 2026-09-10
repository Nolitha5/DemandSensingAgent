import React from 'react';
import {
  ComposedChart, Area, Line, XAxis, YAxis, CartesianGrid,
  Tooltip, Legend, ResponsiveContainer, ReferenceLine,
} from 'recharts';
import { format, parseISO } from 'date-fns';
import type { DemandPoint, ForecastDay } from '../../types/demand';

interface Props {
  historical: DemandPoint[];
  forecastDays: ForecastDay[];
  productId: string;
}

const COLORS = {
  historical: '#3b82f6',
  forecast: '#f59e0b',
  band: '#fde68a',
  separator: '#ef4444',
};

export function ForecastChart({ historical, forecastDays, productId }: Props) {
  // Build unified data series
  const histData = historical.slice(-60).map(p => ({
    date: p.date,
    label: format(parseISO(p.date), 'MMM d'),
    actual: p.is_outlier ? null : p.qty,
    outlier: p.is_outlier ? p.qty : null,
    type: 'historical' as const,
  }));

  const lastHistDate = histData.length > 0 ? histData[histData.length - 1].date : null;

  const fcData = forecastDays.map(f => ({
    date: f.date,
    label: format(parseISO(f.date), 'MMM d'),
    forecast: f.expected_qty,
    lower: f.lower_bound,
    upper: f.upper_bound,
    band: [f.lower_bound, f.upper_bound] as [number, number],
    type: 'forecast' as const,
  }));

  const combined = [...histData, ...fcData];

  const CustomTooltip = ({ active, payload, label }: any) => {
    if (!active || !payload?.length) return null;
    return (
      <div className="bg-white border border-gray-200 rounded-lg p-3 shadow-lg text-sm">
        <p className="font-semibold text-gray-700 mb-1">{label}</p>
        {payload.map((p: any) => (
          p.value !== null && p.value !== undefined && (
            <p key={p.dataKey} style={{ color: p.color }}>
              {p.name}: {typeof p.value === 'number' ? p.value.toFixed(1) : p.value}
            </p>
          )
        ))}
      </div>
    );
  };

  return (
    <div className="w-full">
      <ResponsiveContainer width="100%" height={320}>
        <ComposedChart data={combined} margin={{ top: 10, right: 20, left: 0, bottom: 0 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="#f0f0f0" />
          <XAxis
            dataKey="label"
            tick={{ fontSize: 11 }}
            interval={Math.floor(combined.length / 8)}
          />
          <YAxis tick={{ fontSize: 11 }} />
          <Tooltip content={<CustomTooltip />} />
          <Legend wrapperStyle={{ fontSize: 12 }} />

          {/* Confidence band */}
          <Area
            data={fcData}
            dataKey="upper"
            stroke="none"
            fill={COLORS.band}
            fillOpacity={0.6}
            name="Upper bound"
          />
          <Area
            data={fcData}
            dataKey="lower"
            stroke="none"
            fill="#ffffff"
            fillOpacity={1}
            name="Lower bound"
          />

          {/* Historical actuals */}
          <Line
            data={histData}
            dataKey="actual"
            stroke={COLORS.historical}
            strokeWidth={2}
            dot={false}
            name="Historical demand"
            connectNulls={false}
          />

          {/* Forecast line */}
          <Line
            data={fcData}
            dataKey="forecast"
            stroke={COLORS.forecast}
            strokeWidth={2}
            strokeDasharray="6 3"
            dot={{ r: 3, fill: COLORS.forecast }}
            name="Forecast"
          />

          {/* Separator line at history/forecast boundary */}
          {lastHistDate && (
            <ReferenceLine
              x={format(parseISO(lastHistDate), 'MMM d')}
              stroke={COLORS.separator}
              strokeDasharray="4 2"
              label={{ value: 'Today', position: 'top', fontSize: 10, fill: COLORS.separator }}
            />
          )}
        </ComposedChart>
      </ResponsiveContainer>
      <p className="text-xs text-gray-400 text-center mt-1">
        Showing last 60 days history + {forecastDays.length}-day forecast with confidence band
      </p>
    </div>
  );
}
