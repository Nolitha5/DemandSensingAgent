import React from 'react';
import type { DemandSignalAdjustment } from '../../types/demand';
import { format, parseISO } from 'date-fns';

interface Props {
  signals: DemandSignalAdjustment[];
}

const signalIcon = (type: string) => ({
  PROMOTION: '🏷',
  LOCAL_EVENT: '🎪',
  HOLIDAY: '🏖',
  MARKET_DAY: '🛒',
}[type] ?? '📡');

const signalColor = (factor: number) => {
  if (factor > 1.2) return 'text-emerald-700 bg-emerald-50 border-emerald-200';
  if (factor > 1.0) return 'text-blue-700 bg-blue-50 border-blue-200';
  if (factor < 0.9) return 'text-red-700 bg-red-50 border-red-200';
  return 'text-gray-700 bg-gray-50 border-gray-200';
};

export function SignalsPanel({ signals }: Props) {
  if (!signals.length) {
    return (
      <div className="text-center py-6 text-gray-400 text-sm">
        No active event or promotion signals
      </div>
    );
  }

  return (
    <div className="space-y-3">
      {signals.map((sig, i) => (
        <div key={i} className={`rounded-lg border p-3 ${signalColor(sig.adjustment_factor)}`}>
          <div className="flex items-start justify-between gap-2">
            <div className="flex items-center gap-2">
              <span className="text-lg">{signalIcon(sig.signal_type)}</span>
              <div>
                <p className="text-sm font-semibold">
                  {sig.signal_type.replace('_', ' ')}
                  {sig.promo_id && <span className="ml-1 text-xs font-normal opacity-70">#{sig.promo_id}</span>}
                  {sig.event_id && <span className="ml-1 text-xs font-normal opacity-70">#{sig.event_id}</span>}
                </p>
                {sig.active_from && sig.active_to && (
                  <p className="text-xs opacity-70">
                    {format(parseISO(sig.active_from), 'MMM d')} – {format(parseISO(sig.active_to), 'MMM d')}
                  </p>
                )}
              </div>
            </div>
            <div className="text-right shrink-0">
              <p className="text-lg font-bold">×{sig.adjustment_factor.toFixed(2)}</p>
              <p className="text-xs opacity-70">{(sig.confidence * 100).toFixed(0)}% conf.</p>
            </div>
          </div>
          {sig.reason && (
            <p className="text-xs mt-2 opacity-80 border-t border-current/10 pt-2">{sig.reason}</p>
          )}
        </div>
      ))}
    </div>
  );
}
