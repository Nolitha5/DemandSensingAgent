import React from 'react';
import { BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, ReferenceLine, ResponsiveContainer, Cell } from 'recharts';
import type { SeasonalityProfile } from '../../types/demand';

interface Props {
  profile: SeasonalityProfile;
}

const DAYS = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday'];
const SHORT = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];

export function SeasonalityChart({ profile }: Props) {
  const data = DAYS.map((day, i) => ({
    day: SHORT[i],
    index: profile.weekly_pattern[day] ?? 1.0,
  }));

  const getColor = (idx: number) => {
    if (idx > 1.15) return '#10b981';
    if (idx < 0.85) return '#ef4444';
    return '#3b82f6';
  };

  if (!profile.data_sufficient) {
    return (
      <div className="flex items-center justify-center h-40 text-gray-400 text-sm">
        Insufficient data for seasonality detection
      </div>
    );
  }

  return (
    <div className="w-full">
      <ResponsiveContainer width="100%" height={200}>
        <BarChart data={data} margin={{ top: 10, right: 10, left: -10, bottom: 0 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="#f0f0f0" />
          <XAxis dataKey="day" tick={{ fontSize: 11 }} />
          <YAxis domain={[0.5, 1.8]} tick={{ fontSize: 11 }} tickFormatter={v => `×${v.toFixed(1)}`} />
          <Tooltip formatter={(v: number) => [`×${v.toFixed(3)}`, 'Demand index']} />
          <ReferenceLine y={1.0} stroke="#9ca3af" strokeDasharray="4 2" />
          <Bar dataKey="index" radius={[4, 4, 0, 0]}>
            {data.map((entry, idx) => (
              <Cell key={idx} fill={getColor(entry.index)} />
            ))}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
      <div className="flex gap-4 justify-center mt-2 text-xs text-gray-500">
        <span className="flex items-center gap-1"><span className="w-3 h-3 rounded-sm bg-emerald-500 inline-block" /> Above average (&gt;1.15×)</span>
        <span className="flex items-center gap-1"><span className="w-3 h-3 rounded-sm bg-blue-500 inline-block" /> Average</span>
        <span className="flex items-center gap-1"><span className="w-3 h-3 rounded-sm bg-red-500 inline-block" /> Below average (&lt;0.85×)</span>
      </div>
    </div>
  );
}
