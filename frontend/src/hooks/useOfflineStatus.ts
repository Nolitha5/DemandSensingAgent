/**
 * useOfflineStatus
 * ─────────────────
 * Polls the /system/status endpoint every 20 seconds and reports:
 *   - connectivity: "online" | "offline" | "unknown"
 *   - queuedJobs:   number of PENDING/RUNNING jobs in the local queue
 *   - unsyncedDocs: number of cache entries not yet pushed to Firestore
 *
 * Falls back gracefully when the backend is unreachable (marks as offline).
 */
import { useEffect, useRef, useState } from 'react';
import axios from 'axios';

const POLL_INTERVAL_MS = 20_000;

export interface OfflineStatus {
  connectivity: 'online' | 'offline' | 'unknown';
  queuedJobs: number;
  unsyncedDocs: number;
  lastChecked: Date | null;
}

const DEFAULT_STATUS: OfflineStatus = {
  connectivity: 'unknown',
  queuedJobs: 0,
  unsyncedDocs: 0,
  lastChecked: null,
};

export function useOfflineStatus(): OfflineStatus {
  const [status, setStatus] = useState<OfflineStatus>(DEFAULT_STATUS);
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const fetchStatus = async () => {
    try {
      const { data } = await axios.get('/system/status', { timeout: 5000 });
      setStatus({
        connectivity: data.connectivity ?? 'unknown',
        queuedJobs: data.queued_jobs ?? 0,
        unsyncedDocs: data.unsynced_docs ?? 0,
        lastChecked: new Date(),
      });
    } catch {
      setStatus((prev) => ({
        ...prev,
        connectivity: 'offline',
        lastChecked: new Date(),
      }));
    }
  };

  useEffect(() => {
    fetchStatus();
    timerRef.current = setInterval(fetchStatus, POLL_INTERVAL_MS);
    return () => {
      if (timerRef.current) clearInterval(timerRef.current);
    };
  }, []);

  return status;
}
