export type LoggingStatus = {
  enabled: boolean; generation: string; status: 'applied' | 'partial';
  nodes: Record<string, { status: 'applied' | 'pending' | 'unsupported' | 'error' | 'unreachable' }>;
};

export type LogSource = 'agent' | 'llama' | 'rpc' | 'unknown';

/** Producer tag. `ui` is the control application, `inference` is model serving, `unclassified` is an unknown producer. */
export type LogTag = 'ui' | 'inference' | 'unclassified';

/** One recorded cluster log line from GET /v2/logs. */
export type LogEntry = {
  event_id: string;
  ts: number;
  node_id: string;
  source: LogSource;
  tag: LogTag;
  line: string;
};

/** Bounded recent snapshot plus a resumable receiver-local checkpoint. */
/** Stable producer identity used only by explicitly negotiated log streams. */
export type LogPosition = { slot: number; generation: number; sequence: number };
export type PositionedLogEvent = LogEntry & { position: LogPosition };
export type PositionFrontier = {
  domain_id: string;
  frontier_revision: number;
  retired_through: { slot: number; generation: number; sequence: number }[];
};

export type LogSnapshot = {
  lines: LogEntry[];
  source: 'cluster' | 'local';
  warning?: string;
  cursor: string;
  reset: 'origin_changed' | 'restart' | null;
  gap: boolean;
  gap_reason: 'retention' | 'batch_limit' | 'feed_reset' | null;
};

export type RefreshMode = 'live' | '5s' | '30s' | '1m';
