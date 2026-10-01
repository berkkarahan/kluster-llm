import type { DeliveryHealth, LiveArea, LiveAreaName, LiveLogs, LiveSnapshot, UsageLive } from './types';

const TOPICS: LiveAreaName[] = ['status', 'usage', 'load', 'download'];
const READ_TIMEOUT_MS = 5000;
const MAX_FRAME_BYTES = 5 * 1024 * 1024;
const bytes = (value: string) => new TextEncoder().encode(value).length;
const LEGACY_ROUTES: Record<LiveAreaName, string> = {
  status: '/v2/node', usage: '/v2/telemetry',
  load: '/v2/inference/load', download: '/v2/models/downloads/current'
};
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
const UTC = /^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d+)?Z$/;

export type LiveLogListener = {
  after: () => string | null;
  continuity: () => string | null;
  onLogs: (logs: LiveLogs) => void;
};

type Options = {
  onArea: (topic: LiveAreaName, area: LiveArea<unknown>) => void;
  onAreaHealth?: (topic: LiveAreaName, area: LiveArea<unknown>) => void;
  onHealth: (health: DeliveryHealth, error?: string) => void;
};

function wsUrl(): string {
  const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
  return `${protocol}//${location.host}/v2/events`;
}

function hidden(): boolean {
  return document.visibilityState === 'hidden';
}

function validTime(value: unknown): value is string {
  return typeof value === 'string' && UTC.test(value) && Number.isFinite(Date.parse(value));
}

function validArea(value: unknown): value is LiveArea<unknown> {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return false;
  const area = value as Record<string, unknown>;
  if (!Number.isSafeInteger(area.revision) || (area.revision as number) < 0) return false;
  if (area.sampled_at !== null && !validTime(area.sampled_at)) return false;
  if (!['loading', 'available', 'unavailable', 'denied'].includes(String(area.availability))) return false;
  if (!['local', 'cluster', 'local_with_cluster_view'].includes(String(area.coverage))) return false;
  if (area.upstream_last_success_at !== null && !validTime(area.upstream_last_success_at)) return false;
  if (area.availability === 'available' ? area.data === null : area.data !== null) return false;
  if (area.error !== null) {
    if (!area.error || typeof area.error !== 'object' || Array.isArray(area.error)) return false;
    const error = area.error as Record<string, unknown>;
    if (typeof error.code !== 'string' || error.code.length > 64
      || typeof error.message !== 'string' || error.message.length > 256) return false;
  } else if (area.availability === 'unavailable' || area.availability === 'denied') return false;
  return true;
}

function validOperation(value: unknown, kind: 'load' | 'download'): boolean {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return false;
  const operation = value as Record<string, unknown>;
  if (operation.kind !== kind || typeof operation.operation_id !== 'string' || !UUID.test(operation.operation_id)
    || typeof operation.node_id !== 'string' || !operation.node_id
    || !['running', 'complete', 'error', 'cancelled'].includes(String(operation.state))
    || !validTime(operation.started_at) || !validTime(operation.updated_at)) return false;
  if (operation.state === 'running' ? operation.finished_at !== null : !validTime(operation.finished_at)) return false;
  return kind === 'load' ? typeof operation.model === 'string' : typeof operation.filename === 'string';
}

function validProgress(value: unknown, kind: 'load' | 'download'): boolean {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return false;
  const progress = value as Record<string, unknown>;
  return (progress.current === null || validOperation(progress.current, kind))
    && (progress.last_terminal === null || validOperation(progress.last_terminal, kind)
      && (progress.last_terminal as Record<string, unknown>).state !== 'running');
}

function validLegacy(topic: LiveAreaName, value: unknown): value is Record<string, unknown> {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return false;
  const data = value as Record<string, unknown>;
  if (topic === 'status') return ['idle', 'master', 'worker'].includes(String(data.role))
    && typeof data.node_id === 'string' && !!data.node_id
    && !!data.members && typeof data.members === 'object' && !Array.isArray(data.members);
  if (topic === 'usage') return !!data.nodes && typeof data.nodes === 'object' && !Array.isArray(data.nodes)
    && Array.isArray(data.relationships) && !!data.recording && typeof data.recording === 'object';
  if (topic === 'load') return data.phase === null
    || (['starting', 'loading', 'ready', 'error', 'cancelled'].includes(String(data.phase))
      && typeof data.model === 'string' && typeof data.fraction === 'number'
      && Number.isFinite(data.fraction) && data.fraction >= 0 && data.fraction <= 1);
  return ['idle', 'running', 'complete', 'error', 'cancelled'].includes(String(data.state))
    && (data.state === 'idle' || typeof data.filename === 'string');
}

function validSnapshot(value: unknown): value is LiveSnapshot {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return false;
  const data = value as Record<string, unknown>;
  if (data.version !== 1 || data.transport !== 'websocket' || typeof data.epoch !== 'string'
    || !UUID.test(data.epoch) || !validTime(data.server_time)
    || !data.areas || typeof data.areas !== 'object' || Array.isArray(data.areas)) return false;
  const areas = data.areas as Record<string, unknown>;
  return Object.keys(areas).every((topic) => TOPICS.includes(topic as LiveAreaName))
    && Object.entries(areas).every(([topic, area]) => validArea(area)
      && bytes(JSON.stringify(area)) <= 1024 * 1024
      && (area.availability !== 'available' || topic !== 'load' || validProgress(area.data, 'load'))
      && (area.availability !== 'available' || topic !== 'download' || validProgress(area.data, 'download')))
    && (data.logs === null || validLogs(data.logs) && bytes(JSON.stringify(data.logs)) <= 256 * 1024);
}

function tagMatchesSource(source: string, tag: unknown): boolean {
  const expected = source === 'agent' ? 'ui'
    : source === 'llama' || source === 'rpc' ? 'inference'
      : source === 'unknown' ? 'unclassified'
        : null;
  return expected !== null && (tag === undefined || tag === expected);
}

function validLogs(value: unknown): value is LiveLogs {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return false;
  const logs = value as Record<string, unknown>;
  if (!['loading', 'available', 'unavailable', 'denied'].includes(String(logs.availability))) return false;
  if (logs.sampled_at !== null && !validTime(logs.sampled_at)) return false;
  if (logs.availability === 'available') {
    if (!logs.snapshot || typeof logs.snapshot !== 'object' || Array.isArray(logs.snapshot)) return false;
    const snapshot = logs.snapshot as Record<string, unknown>;
    if (!Array.isArray(snapshot.lines) || typeof snapshot.cursor !== 'string' || snapshot.cursor.length > 128) return false;
    const [cursorEpoch, sequence, extra] = snapshot.cursor.split(':');
    if (!UUID.test(cursorEpoch ?? '') || !/^\d+$/.test(sequence ?? '') || extra !== undefined) return false;
    if (snapshot.source !== 'cluster' && snapshot.source !== 'local') return false;
    if (snapshot.reset !== null && snapshot.reset !== 'origin_changed' && snapshot.reset !== 'restart') return false;
    if (typeof snapshot.gap !== 'boolean'
      || (snapshot.gap_reason !== null && !['retention', 'batch_limit', 'feed_reset'].includes(String(snapshot.gap_reason)))) return false;
    if (typeof snapshot.continuity_id !== 'string' || !UUID.test(snapshot.continuity_id)) return false;
    if (snapshot.warning !== undefined && typeof snapshot.warning !== 'string') return false;
    if (snapshot.discontinuity !== null) {
      if (!snapshot.discontinuity || typeof snapshot.discontinuity !== 'object' || Array.isArray(snapshot.discontinuity)) return false;
      const discontinuity = snapshot.discontinuity as Record<string, unknown>;
      if (typeof discontinuity.id !== 'string' || !UUID.test(discontinuity.id)
        || !['upstream_reset', 'upstream_retention', 'upstream_batch_limit', 'coverage_change'].includes(String(discontinuity.reason))
        || !validTime(discontinuity.detected_at)) return false;
    }
    if (snapshot.lines.some((line) => {
      if (!line || typeof line !== 'object' || Array.isArray(line)) return true;
      const entry = line as Record<string, unknown>;
      return typeof entry.event_id !== 'string' || !UUID.test(entry.event_id)
        || typeof entry.ts !== 'number' || !Number.isFinite(entry.ts)
        || typeof entry.node_id !== 'string' || !entry.node_id
        || !tagMatchesSource(String(entry.source), entry.tag)
        || typeof entry.line !== 'string';
    })) return false;
  } else if (logs.snapshot !== null) return false;
  if (logs.error !== null) {
    const error = logs.error as Record<string, unknown> | null;
    if (!error || typeof error.code !== 'string' || typeof error.message !== 'string'
      || error.code.length > 64 || error.message.length > 256) return false;
  } else if (logs.availability === 'unavailable' || logs.availability === 'denied') return false;
  return true;
}

/** One visible dashboard owns one feed; components only register their interest. */
export class DashboardUpdates {
  private readonly options: Options;
  private socket: WebSocket | null = null;
  private timer: ReturnType<typeof setTimeout> | null = null;
  private probeTimer: ReturnType<typeof setTimeout> | null = null;
  private connectionTimer: ReturnType<typeof setTimeout> | null = null;
  private silenceTimer: ReturnType<typeof setTimeout> | null = null;
  private pingTimer: ReturnType<typeof setTimeout> | null = null;
  private staleTimer: ReturnType<typeof setTimeout> | null = null;
  private abort: AbortController | null = null;
  private generation = 0;
  private batch = 0;
  private running = false;
  private usageInterest = false;
  private deniedTopics = new Set<LiveAreaName>();
  private logListener: LiveLogListener | null = null;
  private logVisibilityListener: (() => void) | null = null;
  private revisions: Partial<Record<LiveAreaName, number>> = {};
  private epoch = '';
  private clockUpperOffset: number | null = null;
  private clockSample: { wall: number; monotonic: number } | null = null;
  private probeId = 0;
  private pendingProbe: { id: number; wall: number; monotonic: number } | null = null;
  private openingProbe: { gen: number; wall: number; monotonic: number } | null = null;
  private usageArea: LiveArea<UsageLive> | null = null;
  private usageAnchors = new Map<string, { observation: string; deadline: number }>();
  private resourceAnchors = new Map<string, { observation: string; deadline: number }>();
  private legacyAttempt = 0;

  constructor(options: Options) {
    this.options = options;
  }

  start(): void {
    if (this.running || typeof window === 'undefined') return;
    this.running = true;
    document.addEventListener('visibilitychange', this.visibility);
    void this.connect();
  }

  stop(): void {
    this.running = false;
    this.generation += 1;
    document.removeEventListener('visibilitychange', this.visibility);
    this.abort?.abort();
    this.abort = null;
    if (this.timer) clearTimeout(this.timer);
    this.timer = null;
    this.clearConnectionTimers();
    if (this.staleTimer) clearTimeout(this.staleTimer);
    this.staleTimer = null;
    if (this.probeTimer) clearTimeout(this.probeTimer);
    this.probeTimer = null;
    this.socket?.close(1000);
    this.socket = null;
  }

  setLogListener(listener: LiveLogListener | null): void {
    this.logListener = listener;
    if (this.running && !hidden()) this.reconnect();
  }

  setLogVisibilityListener(listener: (() => void) | null, expected?: () => void): void {
    if (listener === null && expected && this.logVisibilityListener !== expected) return;
    this.logVisibilityListener = listener;
  }

  setUsageInterest(active: boolean): void {
    if (this.usageInterest === active) return;
    this.usageInterest = active;
    if (this.running && !hidden()) this.reconnect();
  }

  private topics(): LiveAreaName[] {
    return TOPICS.filter((topic) => (topic !== 'usage' || this.usageInterest) && !this.deniedTopics.has(topic));
  }

  refresh(): void {
    if (!this.running || hidden()) return;
    if (this.deniedTopics.size) {
      this.deniedTopics.clear();
      this.reconnect();
    } else void this.fetchSnapshot();
  }

  private visibility = (): void => {
    if (document.visibilityState === 'hidden') {
      this.options.onHealth('paused');
      this.generation += 1;
      this.abort?.abort();
      this.socket?.close(1000);
      this.socket = null;
      if (this.timer) clearTimeout(this.timer);
      this.timer = null;
      this.clearConnectionTimers();
      if (this.staleTimer) clearTimeout(this.staleTimer);
      this.staleTimer = null;
      if (this.probeTimer) clearTimeout(this.probeTimer);
      this.probeTimer = null;
      this.logVisibilityListener?.();
      return;
    }
    if (this.running) void this.connect();
    this.logVisibilityListener?.();
  };

  private reconnect(): void {
    this.generation += 1;
    this.clearConnectionTimers();
    if (this.timer) clearTimeout(this.timer);
    this.timer = null;
    if (this.probeTimer) clearTimeout(this.probeTimer);
    this.probeTimer = null;
    this.socket?.close(1000);
    this.socket = null;
    void this.connect();
  }

  private async connect(): Promise<void> {
    if (!this.running || hidden()) return;
    const gen = ++this.generation;
    this.batch = 0;
    if (this.timer === null) this.options.onHealth('loading');
    await this.fetchSnapshot(gen);
    if (!this.running || gen !== this.generation || hidden()) return;
    if (this.topics().length === 0 && !this.logListener) {
      this.options.onHealth('unavailable', 'Dashboard area access denied');
      return;
    }
    try {
      this.openingProbe = { gen, wall: Date.now(), monotonic: performance.now() };
      const socket = new WebSocket(wsUrl());
      this.socket = socket;
      let initialized = false;
      let lastPeerAt = Date.now();
      this.connectionTimer = setTimeout(() => {
        if (gen === this.generation && !initialized) socket.close(1000);
      }, 3000);
      socket.onopen = () => {
        if (gen !== this.generation) return;
        lastPeerAt = Date.now();
        socket.send(JSON.stringify({
          type: 'subscribe', version: 1, generation: 1, topics: this.topics(),
          logs: this.logListener ? { after: this.logListener.after(), continuity_id: this.logListener.continuity() } : null
        }));
        this.sendProbe(gen, socket);
      };
      socket.onmessage = (event) => {
        if (gen !== this.generation) return;
        const accepted = this.message(gen, socket, event.data);
        if (!accepted) return;
        lastPeerAt = Date.now();
        if (accepted === 'initial') {
          initialized = true;
          if (this.connectionTimer) clearTimeout(this.connectionTimer);
          this.connectionTimer = null;
          if (this.timer) clearTimeout(this.timer);
          this.timer = null;
          if (this.probeTimer) clearTimeout(this.probeTimer);
          this.probeTimer = null;
          this.options.onHealth('live');
        }
        if (initialized) {
          if (this.silenceTimer) clearTimeout(this.silenceTimer);
          this.silenceTimer = setTimeout(() => {
            if (gen === this.generation && Date.now() - lastPeerAt >= 4000) socket.close(1000);
          }, 4000);
        }
      };
      socket.onerror = () => undefined;
      socket.onclose = () => {
        if (gen !== this.generation) return;
        this.clearConnectionTimers();
        if (!this.running || hidden()) return;
        this.generation += 1;
        this.socket = null;
        this.abort?.abort();
        this.abort = null;
        this.scheduleReconnect(this.generation);
      };
    } catch {
      this.scheduleReconnect(gen);
    }
  }

  private message(gen: number, socket: WebSocket, raw: unknown): 'initial' | 'valid' | false {
    if (gen !== this.generation || typeof raw !== 'string') return false;
    if (bytes(raw) > MAX_FRAME_BYTES) { socket.close(1009); return false; }
    let message: unknown;
    try { message = JSON.parse(raw); } catch { socket.close(1008); return false; }
    if (!message || typeof message !== 'object') { socket.close(1008); return false; }
    const data = message as Record<string, unknown>;
    if (data.type === 'ping' && Number.isSafeInteger(data.id) && (data.id as number) >= 0) {
      socket.send(JSON.stringify({ type: 'pong', id: data.id }));
      return 'valid';
    }
    const pending = this.pendingProbe;
    if (data.type === 'pong') {
      if (!pending || pending.id !== data.id || !validTime(data.server_time)) return false;
      this.sampleClock(data.server_time, pending.wall, pending.monotonic);
      this.pendingProbe = null;
      return 'valid';
    }
    if (data.type === 'hello') {
      if (data.version !== 1 || typeof data.epoch !== 'string' || !UUID.test(data.epoch)
        || !validTime(data.server_time) || data.heartbeat_ms !== 2000) {
        socket.close(1008);
        return false;
      }
      const opening = this.openingProbe;
      if (opening?.gen === gen) this.sampleClock(data.server_time, opening.wall, opening.monotonic);
      this.openingProbe = null;
      return 'valid';
    }
    if (data.type !== 'data' || typeof data.batch !== 'number' || typeof data.generation !== 'number') {
      socket.close(1008);
      return false;
    }
    if (!Number.isSafeInteger(data.batch) || (data.batch as number) <= 0 || !Number.isSafeInteger(data.generation)
      || typeof data.initial !== 'boolean' || typeof data.epoch !== 'string' || !UUID.test(data.epoch)
      || !validTime(data.server_time) || !data.areas || typeof data.areas !== 'object' || Array.isArray(data.areas)) {
      socket.close(1008);
      return false;
    }
    const areas = data.areas as Record<string, unknown>;
    if (!Object.keys(areas).every((topic) => TOPICS.includes(topic as LiveAreaName))
      || Object.keys(areas).some((topic) => !this.topics().includes(topic as LiveAreaName))
      || !Object.values(areas).every(validArea) || (data.logs !== null && !validLogs(data.logs))) {
      socket.close(1008);
      return false;
    }
    const snapshot: LiveSnapshot = {
      version: 1, transport: 'websocket', epoch: String(data.epoch ?? ''), server_time: String(data.server_time ?? ''),
      areas: areas as LiveSnapshot['areas'],
      logs: (data.logs ?? null) as LiveLogs | null
    };
    if (!validSnapshot(snapshot) || data.generation !== 1) { socket.close(1008); return false; }
    if (data.batch <= this.batch) return 'valid';
    if (data.initial !== (this.batch === 0)) { socket.close(1008); return false; }
    if (data.initial && (this.topics().some((topic) => !(topic in areas))
      || (this.logListener !== null && data.logs === null))) { socket.close(1008); return false; }
    if (this.epoch && this.epoch !== snapshot.epoch && !data.initial) { socket.close(1008); return false; }
    if (data.initial) {
      this.legacyAttempt += 1;
      this.abort?.abort();
      this.abort = null;
    }
    this.apply(snapshot);
    this.batch = data.batch;
    socket.send(JSON.stringify({ type: 'ack', generation: 1, batch: data.batch }));
    return data.initial ? 'initial' : 'valid';
  }

  private async fetchSnapshot(expected = this.generation, withLogs = true): Promise<boolean> {
    if (!this.running || hidden()) return false;
    const sentWall = Date.now();
    const sentMono = performance.now();
    this.abort?.abort();
    const controller = new AbortController();
    this.abort = controller;
    const deadline = setTimeout(() => controller.abort(), READ_TIMEOUT_MS);
    try {
      const params = new URLSearchParams({ topics: this.topics().join(',') });
      if (this.logListener && withLogs) {
        params.set('logs', '1');
        const after = this.logListener.after();
        if (after) params.set('after', after);
        const continuity = this.logListener.continuity();
        if (continuity) params.set('continuity_id', continuity);
      }
      const response = await fetch(`/v2/events?${params}`, { signal: controller.signal });
      if (!response.ok) {
        if (response.status === 401 || response.status === 403) throw new Error('Dashboard access denied');
        if (await this.readLegacy(expected, controller)) return true;
        throw new Error('Dashboard updates unavailable');
      }
      const raw = await response.text();
      if (bytes(raw) > MAX_FRAME_BYTES) throw new Error('Dashboard snapshot is too large');
      const snapshot: unknown = JSON.parse(raw);
      if (!validSnapshot(snapshot)) {
        if (await this.readLegacy(expected, controller)) return true;
        throw new Error('Dashboard returned an invalid snapshot');
      }
      if (expected !== this.generation || this.abort !== controller
        || this.topics().some((topic) => !(topic in snapshot.areas))
        || Object.keys(snapshot.areas).some((topic) => !this.topics().includes(topic as LiveAreaName))) return false;
      this.legacyAttempt += 1;
      this.sampleClock(snapshot.server_time, sentWall, sentMono);
      this.apply(snapshot);
      return true;
    } catch (error) {
      if (expected === this.generation && !controller.signal.aborted) {
        this.options.onHealth('unavailable', String(error));
      }
      return false;
    } finally {
      clearTimeout(deadline);
      if (this.abort === controller) this.abort = null;
    }
  }

  private async readLegacy(expected: number, controller: AbortController): Promise<boolean> {
    const attempt = ++this.legacyAttempt;
    const results = await Promise.all(this.topics().map(async (topic) => {
      try {
        const response = await fetch(LEGACY_ROUTES[topic], { signal: controller.signal });
        if (!response.ok) return { topic, availability: response.status === 401 || response.status === 403
          ? 'denied' as const : 'unavailable' as const, data: null };
        const data: unknown = await response.json();
        if (!validLegacy(topic, data)) return { topic, availability: 'unavailable' as const, data: null };
        if (topic === 'load') {
          const load = data as Record<string, unknown>;
          return { topic, availability: 'available' as const, data: {
            current: load.phase === 'starting' || load.phase === 'loading' ? data : null,
            last_terminal: load.phase === null || load.phase === 'starting' || load.phase === 'loading' ? null : data
          } };
        }
        if (topic === 'download') {
          const job = data as Record<string, unknown>;
          return { topic, availability: 'available' as const, data: {
            current: job.state === 'running' ? data : null,
            last_terminal: job.state === 'idle' || job.state === 'running' ? null : data
          } };
        }
        return { topic, availability: 'available' as const, data };
      } catch {
        return { topic, availability: 'unavailable' as const, data: null };
      }
    }));
    if (!this.running || hidden() || controller.signal.aborted || this.abort !== controller
      || expected !== this.generation || attempt !== this.legacyAttempt) return false;
    // Versionless observations belong only to this fallback attempt; never fabricate source revisions or operation IDs.
    for (const result of results) {
      if (result.availability === 'denied') this.deniedTopics.add(result.topic);
      const area: LiveArea<unknown> = {
        revision: 0, sampled_at: result.availability === 'available' ? new Date().toISOString() : null,
        availability: result.availability, coverage: 'local', upstream_last_success_at: null,
        data: result.data,
        error: result.availability === 'available' ? null : { code: result.availability === 'denied'
          ? 'forbidden' : 'source_unavailable', message: result.availability === 'denied'
            ? 'Area access denied' : 'Area unavailable' }
      };
      this.options.onAreaHealth?.(result.topic, area);
      if (result.availability === 'available') this.options.onArea(result.topic, area);
    }
    if (!results.some((result) => result.availability === 'available')) return false;
    this.options.onHealth('periodic-refresh', 'Compatibility refresh; live updates unavailable');
    return true;
  }

  private apply(snapshot: LiveSnapshot): void {
    if (this.epoch && this.epoch !== snapshot.epoch) {
      this.revisions = {};
      this.usageAnchors.clear();
      this.resourceAnchors.clear();
    }
    this.epoch = snapshot.epoch;
    for (const topic of TOPICS) {
      const area = snapshot.areas[topic];
      if (!area || typeof area.revision !== 'number') continue;
      if ((this.revisions[topic] ?? -1) > area.revision) continue;
      if (area.availability === 'denied') this.deniedTopics.add(topic);
      else this.deniedTopics.delete(topic);
      this.options.onAreaHealth?.(topic, area as LiveArea<unknown>);
      if ((this.revisions[topic] ?? -1) === area.revision) continue;
      this.revisions[topic] = area.revision;
      if (topic === 'usage' && area.availability === 'available' && area.data) {
        this.usageArea = this.ageUsage(area as LiveArea<UsageLive>, snapshot.server_time);
        this.options.onArea(topic, this.usageArea);
      } else this.options.onArea(topic, area as LiveArea<unknown>);
    }
    if (snapshot.logs && this.logListener) this.logListener.onLogs(snapshot.logs);
  }

  private scheduleReconnect(gen: number): void {
    if (this.timer) clearTimeout(this.timer);
    this.options.onHealth('periodic-refresh', 'Live updates unavailable; refreshing periodically');
    const refresh = () => {
      if (!this.running || hidden() || gen !== this.generation) return;
      void this.fetchSnapshot(gen, false).then((ok) => {
        if (ok && this.running && gen === this.generation) {
          this.options.onHealth('periodic-refresh', 'Live updates unavailable; refreshing periodically');
        }
      });
      this.timer = setTimeout(refresh, 2000);
    };
    void this.fetchSnapshot(gen, false).then((ok) => {
      if (ok && this.running && gen === this.generation) {
        this.options.onHealth('periodic-refresh', 'Live updates unavailable; refreshing periodically');
      }
    });
    this.timer = setTimeout(refresh, 2000);
    if (this.probeTimer) clearTimeout(this.probeTimer);
    this.probeTimer = setTimeout(() => {
      this.probeTimer = null;
      if (this.running && gen === this.generation && !hidden()) void this.connect();
    }, 30000 * (0.8 + Math.random() * 0.4));
  }

  private clearConnectionTimers(): void {
    if (this.connectionTimer) clearTimeout(this.connectionTimer);
    if (this.silenceTimer) clearTimeout(this.silenceTimer);
    if (this.pingTimer) clearTimeout(this.pingTimer);
    this.connectionTimer = null;
    this.silenceTimer = null;
    this.pingTimer = null;
    this.pendingProbe = null;
    this.openingProbe = null;
  }

  private sendProbe(gen: number, socket: WebSocket): void {
    if (gen !== this.generation || socket.readyState !== 1) return;
    const id = ++this.probeId;
    this.pendingProbe = { id, wall: Date.now(), monotonic: performance.now() };
    socket.send(JSON.stringify({ type: 'ping', id }));
    this.pingTimer = setTimeout(() => this.sendProbe(gen, socket), 2000);
  }

  private sampleClock(serverTime: string, wall: number, monotonic: number): void {
    const elapsed = performance.now() - monotonic;
    if (!Number.isFinite(elapsed) || elapsed < 0 || elapsed > READ_TIMEOUT_MS) return;
    // server_time has second precision; offset's upper bound includes its rounding.
    this.clockUpperOffset = Date.parse(serverTime) + 1000 - wall;
    this.clockSample = { wall: Date.now(), monotonic: performance.now() };
  }

  private transitUpper(serverTime: string): number {
    const sample = this.clockSample;
    if (this.clockUpperOffset === null || !sample) return Infinity;
    const elapsed = performance.now() - sample.monotonic;
    if (elapsed < 0 || elapsed > 10_000 || Math.abs((Date.now() - sample.wall) - elapsed) > 1000) {
      this.clockSample = null;
      return Infinity;
    }
    return Math.max(0, (Date.now() + this.clockUpperOffset - Date.parse(serverTime)) / 1000);
  }

  private ageUsage(area: LiveArea<UsageLive>, serverTime: string): LiveArea<UsageLive> {
    const now = performance.now();
    const transit = this.transitUpper(serverTime);
    for (const anchors of [this.usageAnchors, this.resourceAnchors]) {
      for (const id of anchors.keys()) if (!Object.prototype.hasOwnProperty.call(area.data?.nodes ?? {}, id)) anchors.delete(id);
    }
    const nodes = Object.fromEntries(Object.entries(area.data?.nodes ?? {}).map(([id, node]) => {
      if (node.resources && node.resource_freshness === 'fresh') {
        const age = typeof node.resource_age_seconds === 'number' && Number.isFinite(node.resource_age_seconds)
          ? Math.max(0, node.resource_age_seconds) + transit : Infinity;
        const deadline = now + Math.max(0, 10 - age) * 1000;
        const previous = this.resourceAnchors.get(id);
        const observation = node.resources.snapshot_id;
        const anchor = previous?.observation === observation ? Math.min(previous.deadline, deadline) : deadline;
        this.resourceAnchors.set(id, { observation, deadline: anchor });
        if (!node.connected || anchor <= now) node = { ...node, resource_freshness: 'stale' as const };
      } else {
        this.resourceAnchors.delete(id);
      }
      const observed = node.reading?.observed_at;
      if (!observed) {
        this.usageAnchors.delete(id);
        return [id, node];
      }
      const identity = `${node.reading?.lifetime_id ?? ''}:${observed}`;
      if (node.freshness !== 'fresh') return [id, node];
      const age = typeof node.age_seconds === 'number' && Number.isFinite(node.age_seconds)
        ? Math.max(0, node.age_seconds) + transit : Infinity;
      const deadline = now + Math.max(0, 10 - age) * 1000;
      const previous = this.usageAnchors.get(id);
      const anchor = previous?.observation === identity ? Math.min(previous.deadline, deadline) : deadline;
      this.usageAnchors.set(id, { observation: identity, deadline: anchor });
      return [id, anchor <= now ? { ...node, freshness: 'stale' as const } : node];
    }));
    const current = { ...area, data: { ...area.data!, nodes } };
    this.armStale();
    return current;
  }

  private armStale(): void {
    if (this.staleTimer) clearTimeout(this.staleTimer);
    this.staleTimer = null;
    if (!this.running || hidden()) return;
    const now = performance.now();
    const deadlines = [...this.usageAnchors.values(), ...this.resourceAnchors.values()].map(({ deadline }) => deadline).filter((deadline) => deadline > now);
    if (!deadlines.length) return;
    this.staleTimer = setTimeout(() => {
      this.staleTimer = null;
      const area = this.usageArea;
      if (!area?.data || !this.running || hidden()) return;
      const nodes = Object.fromEntries(Object.entries(area.data.nodes).map(([id, node]) => {
        const resourceAnchor = this.resourceAnchors.get(id);
        if (node.resource_freshness === 'fresh' && resourceAnchor && resourceAnchor.deadline <= performance.now()) {
          node = { ...node, resource_freshness: 'stale' as const };
        }
        const anchor = this.usageAnchors.get(id);
        return [id, node.freshness === 'fresh' && anchor && anchor.deadline <= performance.now()
          ? { ...node, freshness: 'stale' as const } : node];
      }));
      if (Object.entries(nodes).some(([id, node]) => node !== area.data!.nodes[id])) {
        this.usageArea = { ...area, data: { ...area.data, nodes } };
        this.options.onArea('usage', this.usageArea);
      }
      this.armStale();
    }, Math.max(1, Math.min(...deadlines) - now));
  }
}
