import React from 'react';
import { clsx } from 'clsx';

interface MetricCardProps {
  label: string;
  value: string | number | null;
  subtitle?: string;
  status?: 'healthy' | 'degraded' | 'alert' | 'neutral' | 'insufficient';
  icon?: React.ReactNode;
}

const statusColors = {
  healthy: 'border-l-emerald-500 bg-emerald-50',
  degraded: 'border-l-amber-500 bg-amber-50',
  alert: 'border-l-red-500 bg-red-50',
  insufficient: 'border-l-gray-400 bg-gray-50',
  neutral: 'border-l-blue-400 bg-white',
};

const valuColors = {
  healthy: 'text-emerald-700',
  degraded: 'text-amber-700',
  alert: 'text-red-700',
  insufficient: 'text-gray-500',
  neutral: 'text-gray-800',
};

export function MetricCard({ label, value, subtitle, status = 'neutral', icon }: MetricCardProps) {
  return (
    <div className={clsx(
      'rounded-lg border-l-4 px-4 py-3 shadow-sm',
      statusColors[status]
    )}>
      <div className="flex items-center justify-between mb-1">
        <span className="text-xs font-medium text-gray-500 uppercase tracking-wide">{label}</span>
        {icon && <span className="text-gray-400">{icon}</span>}
      </div>
      <p className={clsx('text-2xl font-bold', valuColors[status])}>
        {value ?? '—'}
      </p>
      {subtitle && <p className="text-xs text-gray-500 mt-1">{subtitle}</p>}
    </div>
  );
}
