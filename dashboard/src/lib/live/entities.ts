import type { DownloadJob } from '../modules/models/entities';
import type { LoadProgress } from '../modules/run/entities';
import type { Status } from '../modules/cluster/entities';
import type { UsageLive } from '../modules/usage/entities';
import type { LogSnapshot } from '../modules/logs/entities';

export type LiveAvailability = 'loading' | 'available' | 'unavailable' | 'denied';
export type LiveCoverage = 'local' | 'cluster' | 'local_with_cluster_view';
export type DeliveryHealth = 'loading' | 'live' | 'reconnecting' | 'periodic-refresh' | 'unavailable' | 'paused';
export type LiveAreaName = 'status' | 'usage' | 'load' | 'download';

export type LiveError = { code: string; message: string };

export type LiveArea<T> = {
  revision: number;
  sampled_at: string | null;
  availability: LiveAvailability;
  coverage: LiveCoverage;
  upstream_last_success_at: string | null;
  data: T | null;
  error: LiveError | null;
};

export type LiveAreaHealth = {
  availability: LiveAvailability;
  coverage: LiveCoverage;
  last_success_at: string | null;
  error: LiveError | null;
};

export type LiveLogSnapshot = LogSnapshot & {
  continuity_id: string;
  discontinuity: { id: string; reason: string; detected_at: string } | null;
};

export type LiveLogs = {
  availability: LiveAvailability;
  sampled_at: string | null;
  snapshot: LiveLogSnapshot | null;
  error: LiveError | null;
};

export type LiveProgress<T> = { current: T | null; last_terminal: T | null };

export type LiveSnapshot = {
  version: 1;
  transport: 'websocket';
  epoch: string;
  server_time: string;
  areas: Partial<{
    status: LiveArea<Status>;
    usage: LiveArea<UsageLive>;
    load: LiveArea<LiveProgress<LoadProgress>>;
    download: LiveArea<LiveProgress<DownloadJob>>;
  }>;
  logs: LiveLogs | null;
};
