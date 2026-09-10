/**
 * ConnectivityBanner
 * ───────────────────
 * Displays a thin banner at the top of the dashboard when the backend is
 * offline or when there are unsynced documents / queued jobs.
 *
 * States:
 *  • online + nothing pending  → hidden
 *  • online + pending docs/jobs → subtle amber bar ("Syncing N docs …")
 *  • offline                   → red bar ("Working offline — N jobs queued")
 *  • unknown                   → grey bar ("Checking connectivity …")
 */
import React from 'react';
import { useOfflineStatus } from '../../hooks/useOfflineStatus';

interface BannerConfig {
  bg: string;
  icon: string;
  message: string;
  show: boolean;
}

function useBannerConfig(): BannerConfig {
  const { connectivity, queuedJobs, unsyncedDocs } = useOfflineStatus();

  if (connectivity === 'offline') {
    return {
      bg: 'bg-red-600 text-white',
      icon: '📵',
      message: `Working offline — ${queuedJobs} job${queuedJobs !== 1 ? 's' : ''} queued, ${unsyncedDocs} doc${unsyncedDocs !== 1 ? 's' : ''} unsynced. Results are saved locally and will sync when reconnected.`,
      show: true,
    };
  }

  if (connectivity === 'unknown') {
    return {
      bg: 'bg-gray-500 text-white',
      icon: '⏳',
      message: 'Checking connectivity…',
      show: true,
    };
  }

  // online
  if (unsyncedDocs > 0 || queuedJobs > 0) {
    return {
      bg: 'bg-amber-500 text-white',
      icon: '🔄',
      message: `Syncing: ${unsyncedDocs} doc${unsyncedDocs !== 1 ? 's' : ''} unsynced, ${queuedJobs} job${queuedJobs !== 1 ? 's' : ''} queued.`,
      show: true,
    };
  }

  return { bg: '', icon: '', message: '', show: false };
}

export const ConnectivityBanner: React.FC = () => {
  const config = useBannerConfig();
  if (!config.show) return null;

  return (
    <div
      className={`w-full px-4 py-2 text-sm flex items-center gap-2 ${config.bg}`}
      role="status"
      aria-live="polite"
    >
      <span aria-hidden="true">{config.icon}</span>
      <span>{config.message}</span>
    </div>
  );
};

export default ConnectivityBanner;
