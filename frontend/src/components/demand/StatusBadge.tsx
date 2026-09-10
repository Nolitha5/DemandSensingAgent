import React from 'react';
import { clsx } from 'clsx';
import type { ForecastStatus } from '../../types/demand';

interface Props {
  status: ForecastStatus | string;
  className?: string;
}

const config: Record<string, { label: string; className: string }> = {
  HEALTHY: { label: '✓ Healthy', className: 'bg-emerald-100 text-emerald-800' },
  DEGRADED: { label: '⚠ Degraded', className: 'bg-amber-100 text-amber-800' },
  ALERT: { label: '✗ Alert', className: 'bg-red-100 text-red-800' },
  INSUFFICIENT_EVIDENCE: { label: '? Insufficient Evidence', className: 'bg-gray-100 text-gray-600' },
};

export function StatusBadge({ status, className }: Props) {
  const cfg = config[status] ?? { label: status, className: 'bg-gray-100 text-gray-600' };
  return (
    <span className={clsx('inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium', cfg.className, className)}>
      {cfg.label}
    </span>
  );
}
