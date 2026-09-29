import test from 'node:test';
import assert from 'node:assert/strict';

const epoch = '00000000-0000-4000-8000-000000000001';
const stamp = '2026-09-29T12:00:00Z';
const topics = ['status', 'usage', 'load', 'download'];

function area(revision = 1, data = {}) {
  return { revision, sampled_at: stamp, availability: 'available', coverage: 'local',
    upstream_last_success_at: null, data, error: null };
}

function snapshot() {
  return { version: 1, transport: 'websocket', epoch, server_time: stamp,
    areas: Object.fromEntries(topics.map((topic) => [topic, area(1,
      topic === 'load' || topic === 'download' ? { current: null, last_terminal: null } : {})])), logs: null };
}

async function setup(t) {
  const old = Object.fromEntries(['window', 'document', 'location', 'WebSocket', 'fetch']
    .map((key) => [key, globalThis[key]]));
  const listeners = new Map();
  globalThis.window = {};
  globalThis.document = { visibilityState: 'visible', addEventListener: (name, fn) => listeners.set(name, fn),
    removeEventListener: (name) => listeners.delete(name) };
  globalThis.location = { protocol: 'http:', host: 'test.local' };
  const sockets = [];
  const events = [];
  class FakeWebSocket {
    constructor(url) { this.url = url; this.sent = []; this.readyState = 0; sockets.push(this); }
    send(text) { const message = JSON.parse(text); this.sent.push(message); events.push(message.type); }
    close() { this.readyState = 3; this.onclose?.(); }
    open() { this.readyState = 1; this.onopen?.(); }
    receive(value) { this.onmessage?.({ data: JSON.stringify(value) }); }
  }
  globalThis.WebSocket = FakeWebSocket;
  globalThis.fetch = async () => new Response(JSON.stringify(snapshot()), { status: 200 });
  const { DashboardUpdates } = await import('../src/lib/live-updates.ts');
  const applied = [];
  const health = [];
  const areaHealth = [];
  const updates = new DashboardUpdates({ onArea: (topic, value) => { events.push('commit'); applied.push([topic, value]); },
    onAreaHealth: (topic, value) => areaHealth.push([topic, value]),
    onHealth: (value) => health.push(value) });
  updates.setUsageInterest(true);
  t.after(() => {
    updates.stop();
    Object.entries(old).forEach(([key, value]) => {
      if (value === undefined) delete globalThis[key]; else globalThis[key] = value;
    });
  });
  updates.start();
  for (let i = 0; i < 10 && sockets.length === 0; i++) await new Promise((resolve) => setTimeout(resolve, 0));
  assert.equal(sockets.length, 1);
  sockets[0].open();
  return { updates, sockets, applied, health, events, areaHealth, listeners };
}

test('initial frame is validated as a whole and ACKed only after all requested areas commit', async (t) => {
  const { updates, sockets, applied } = await setup(t);
  const socket = sockets[0];
  const before = applied.length;
  socket.receive({ type: 'data', generation: 1, batch: 1, initial: true, epoch, server_time: stamp,
    areas: { ...snapshot().areas, status: undefined }, logs: null });
  assert.equal(socket.sent.some((item) => item.type === 'ack' && item.batch === 1), false);
  socket.receive({ type: 'data', generation: 1, batch: 1, initial: true, epoch, server_time: stamp,
    areas: { ...snapshot().areas, usage: { ...area(), availability: 'not-valid' } }, logs: null });
  assert.equal(applied.length, before);
  assert.equal(socket.sent.some((item) => item.type === 'ack' && item.batch === 1), false);

  updates.setLogListener(null);
  for (let i = 0; i < 10 && sockets.length < 2; i++) await new Promise((resolve) => setTimeout(resolve, 0));
  const replacement = sockets[1];
  replacement.open();
  replacement.receive({ type: 'data', generation: 1, batch: 2, initial: true, epoch, server_time: stamp,
    areas: snapshot().areas, logs: null });
  assert.equal(replacement.sent.some((item) => item.type === 'ack' && item.batch === 2), true);
  assert.equal(applied.length - before, 0, 'equal HTTP snapshot revisions are not reapplied');
});

test('valid stale/equal records are ACKed without repeating area effects', async (t) => {
  const { sockets, applied, events, areaHealth } = await setup(t);
  const socket = sockets[0];
  socket.receive({ type: 'data', generation: 1, batch: 2, initial: true, epoch, server_time: stamp,
    areas: snapshot().areas, logs: null });
  const before = areaHealth.length;
  socket.receive({ type: 'data', generation: 1, batch: 3, initial: false, epoch, server_time: stamp,
    areas: { status: area(1, { stale: true }) }, logs: null });
  assert.equal(socket.sent.some((item) => item.type === 'ack' && item.batch === 3), true);
  assert.equal(applied.length, 4);
  assert.equal(areaHealth.length, before + 1, 'equal records should refresh source health without reapplying values');

  socket.receive({ type: 'data', generation: 1, batch: 4, initial: false, epoch, server_time: stamp,
    areas: { status: area(2, { newer: true }) }, logs: null });
  assert.deepEqual(applied.at(-1), ['status', area(2, { newer: true })]);
  assert.equal(socket.sent.some((item) => item.type === 'ack' && item.batch === 4), true);
  assert.ok(events.lastIndexOf('commit') < events.lastIndexOf('ack'));
});

test('valid load/download failures do not reject an initial frame or mask healthy status', async (t) => {
  const { updates, sockets, applied, areaHealth } = await setup(t);
  const areas = snapshot().areas;
  areas.status = area(2, { role: 'master', join_key: 'master-key' });
  areas.load = { ...area(2, null), availability: 'unavailable',
    error: { code: 'source_unavailable', message: 'Load unavailable' } };
  areas.download = { ...area(2, null), availability: 'loading', sampled_at: null };
  sockets[0].receive({ type: 'data', generation: 1, batch: 1, initial: true, epoch, server_time: stamp,
    areas, logs: null });
  assert.equal(sockets[0].sent.some(({ type, batch }) => type === 'ack' && batch === 1), true);
  assert.equal(applied.findLast(([topic]) => topic === 'status')[1].data.join_key, 'master-key');
  assert.equal(areaHealth.findLast(([topic]) => topic === 'load')[1].availability, 'unavailable');

  areas.load = { ...areas.load, revision: 3, availability: 'denied',
    error: { code: 'forbidden', message: 'Load access denied' } };
  globalThis.fetch = async () => new Response(JSON.stringify({ ...snapshot(), areas }), { status: 200 });
  updates.refresh();
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(areaHealth.findLast(([topic]) => topic === 'load')[1].availability, 'denied');
});

test('superseded socket callbacks cannot publish into the replacement connection', async (t) => {
  const { updates, sockets, applied } = await setup(t);
  const old = sockets[0];
  updates.setLogListener({ after: () => null, continuity: () => null, onLogs: () => {} });
  for (let i = 0; i < 10 && sockets.length < 2; i++) await new Promise((resolve) => setTimeout(resolve, 0));
  assert.equal(sockets.length, 2);
  const replacement = sockets[1];
  replacement.open();
  const before = applied.length;
  old.receive({ type: 'data', generation: 1, batch: 99, initial: false, epoch, server_time: stamp,
    areas: { status: area(99, { obsolete: true }) }, logs: null });
  assert.equal(applied.length, before);
  assert.equal(old.sent.some((item) => item.type === 'ack' && item.batch === 99), false);
});

test('a replacement socket accepts its first batch after the old socket sent higher batches', async (t) => {
  const { updates, sockets, applied } = await setup(t);
  sockets[0].receive({ type: 'data', generation: 1, batch: 4, initial: true, epoch, server_time: stamp,
    areas: snapshot().areas, logs: null });
  assert.equal(sockets[0].sent.some(({ type, batch }) => type === 'ack' && batch === 4), true);
  updates.setLogListener(null);
  for (let i = 0; i < 10 && sockets.length < 2; i++) await new Promise((resolve) => setTimeout(resolve, 0));
  const replacement = sockets[1];
  assert.ok(replacement);
  replacement.open();
  const areas = snapshot().areas;
  areas.status = area(2, { role: 'master' });
  replacement.receive({ type: 'data', generation: 1, batch: 1, initial: true, epoch, server_time: stamp,
    areas, logs: null });
  assert.equal(replacement.sent.some(({ type, batch }) => type === 'ack' && batch === 1), true);
  assert.equal(applied.findLast(([topic]) => topic === 'status')[1].data.role, 'master');
});

test('a socket cannot publish changes before its complete initial frame', async (t) => {
  const { sockets, applied } = await setup(t);
  const before = applied.length;
  sockets[0].receive({ type: 'data', generation: 1, batch: 1, initial: false, epoch, server_time: stamp,
    areas: { status: area(2, { role: 'master' }) }, logs: null });
  assert.equal(applied.length, before);
  assert.equal(sockets[0].sent.some(({ type }) => type === 'ack'), false);
  assert.equal(sockets[0].readyState, 3);
});

test('oversized socket and HTTP snapshots cannot partially commit', async (t) => {
  const { updates, sockets, applied } = await setup(t);
  const before = applied.length;
  const oversized = { ...snapshot(), areas: { ...snapshot().areas,
    status: area(2, { payload: 'x'.repeat(1024 * 1024) }) } };
  sockets[0].receive({ type: 'data', generation: 1, batch: 1, initial: true, epoch, server_time: stamp,
    areas: oversized.areas, logs: null });
  assert.equal(applied.length, before);
  assert.equal(sockets[0].sent.some(({ type }) => type === 'ack'), false);

  const raw = JSON.stringify({ ...snapshot(), areas: { ...snapshot().areas,
    status: area(2, { payload: 'y'.repeat(5 * 1024 * 1024) }) } });
  globalThis.fetch = async () => new Response(raw);
  updates.refresh();
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(applied.length, before);
});

test('unknown socket messages close without refreshing or changing accepted data', async (t) => {
  const { sockets, applied } = await setup(t);
  const before = applied.length;
  sockets[0].receive({ type: 'unexpected', payload: { status: { role: 'wrong' } } });
  assert.equal(sockets[0].readyState, 3);
  assert.equal(applied.length, before);
});

test('a closed socket cannot apply a late update while HTTP fallback owns the view', async (t) => {
  const { sockets, applied } = await setup(t);
  sockets[0].receive({ type: 'data', generation: 1, batch: 1, initial: true, epoch, server_time: stamp,
    areas: snapshot().areas, logs: null });
  const before = applied.length;
  sockets[0].close();
  sockets[0].receive({ type: 'data', generation: 1, batch: 2, initial: false, epoch, server_time: stamp,
    areas: { status: area(2, { role: 'stale-socket' }) }, logs: null });
  assert.equal(applied.length, before);
  assert.equal(sockets[0].sent.some(({ type, batch }) => type === 'ack' && batch === 2), false);
});

test('HTTP-only fallback admits a new serving epoch without opening a new socket', async (t) => {
  const { sockets, applied, updates } = await setup(t);
  sockets[0].receive({ type: 'data', generation: 1, batch: 1, initial: true, epoch, server_time: stamp,
    areas: snapshot().areas, logs: null });
  const newer = snapshot();
  newer.epoch = '00000000-0000-4000-8000-000000000002';
  newer.areas.status = area(1, { role: 'new-agent' });
  globalThis.fetch = async () => new Response(JSON.stringify(newer), { status: 200 });
  sockets[0].close();
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(updates.epoch, newer.epoch);
  assert.equal(applied.findLast(([topic]) => topic === 'status')[1].data.role, 'new-agent');
  assert.equal(sockets.length, 1, 'HTTP recovery must not wait for another upgrade');
});

test('fallback probes live delivery once at the bounded jitter deadline', async (t) => {
  const { sockets } = await setup(t);
  t.mock.timers.enable({ apis: ['setTimeout'] });
  const random = Math.random;
  Math.random = () => 0;
  t.after(() => { Math.random = random; });
  sockets[0].close();
  t.mock.timers.tick(23_999);
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(sockets.length, 1);
  t.mock.timers.tick(1);
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(sockets.length, 2, 'live delivery was not probed at the minimum 24s deadline');
});

test('validated old-server reads keep status and usage usable during socket failure', async (t) => {
  const { sockets, applied, health } = await setup(t);
  const reads = [];
  globalThis.fetch = async (url) => {
    reads.push(String(url));
    if (String(url).startsWith('/v1/dashboard/live')) return new Response('missing', { status: 404 });
    const payload = url === '/v1/status'
      ? { role: 'worker', node_id: 'worker-1', members: {} }
      : url === '/v1/usage/live'
        ? { nodes: {}, relationships: [], recording: { recording: 'interrupted' } }
        : url === '/v1/load-progress'
          ? { phase: null } : { state: 'idle' };
    return new Response(JSON.stringify(payload), { status: 200 });
  };
  sockets[0].close();
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(applied.findLast(([topic]) => topic === 'status')[1].data.role, 'worker');
  assert.equal(applied.findLast(([topic]) => topic === 'usage')[1].data.nodes !== undefined, true);
  assert.equal(health.at(-1), 'periodic-refresh');
  assert.ok(reads.includes('/v1/status') && reads.includes('/v1/usage/live'));
});

test('a confirmed new-route denial never probes old routes as an access bypass', async (t) => {
  const { sockets, health } = await setup(t);
  const reads = [];
  globalThis.fetch = async (url) => {
    reads.push(String(url));
    return new Response(JSON.stringify({ detail: 'denied' }), { status: 403 });
  };
  sockets[0].close();
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(health.at(-1), 'unavailable');
  assert.equal(reads.some((url) => url === '/v1/status'), false);
});

test('denied area is omitted from retries until an explicit refresh', async (t) => {
  const { updates, sockets, areaHealth } = await setup(t);
  t.mock.timers.enable({ apis: ['setTimeout'] });
  const requests = [];
  globalThis.fetch = async (url) => {
    requests.push(String(url));
    const response = snapshot();
    response.areas.usage = { ...area(2, null), availability: 'denied',
      error: { code: 'forbidden', message: 'Access denied' } };
    return new Response(JSON.stringify(response));
  };
  sockets[0].close();
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(areaHealth.findLast(([topic]) => topic === 'usage')[1].availability, 'denied');
  t.mock.timers.tick(2000);
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(requests.length, 2);
  assert.match(requests[0], /usage/);
  assert.doesNotMatch(requests[1], /usage/);
  updates.refresh();
  await new Promise((resolve) => setImmediate(resolve));
  assert.ok(requests.at(-1).includes('usage'), 'explicit retry must revisit the denied area');
});

test('one visibility owner survives twenty hide/show cycles and ignores hidden callbacks', async (t) => {
  const { updates, sockets, applied, listeners } = await setup(t);
  let logVisibilityEvents = 0;
  const listener = () => logVisibilityEvents++;
  updates.setLogVisibilityListener(listener);
  const old = sockets[0];
  for (let i = 0; i < 20; i++) {
    globalThis.document.visibilityState = 'hidden';
    listeners.get('visibilitychange')();
    const before = applied.length;
    old.receive({ type: 'data', generation: 1, batch: i + 1, initial: false, epoch, server_time: stamp,
      areas: { status: area(i + 5, { stale: true }) }, logs: null });
    assert.equal(applied.length, before);
    globalThis.document.visibilityState = 'visible';
    listeners.get('visibilitychange')();
    for (let j = 0; j < 10 && sockets.length < i + 2; j++) await new Promise((resolve) => setImmediate(resolve));
    assert.equal(sockets.length, i + 2);
    sockets.at(-1).open();
    assert.equal(listeners.size, 1);
  }
  assert.equal(logVisibilityEvents, 40);
  assert.ok(sockets.slice(0, -1).every((socket) => socket.readyState === 3));
  updates.setLogVisibilityListener(null, listener);
  updates.stop();
  assert.equal(listeners.size, 0);
});

test('late closure of an old socket cannot cancel the new initialization deadline', async (t) => {
  const { updates, sockets } = await setup(t);
  t.mock.timers.enable({ apis: ['setTimeout'] });
  const lateClose = sockets[0].onclose;
  updates.setLogListener(null);
  for (let i = 0; i < 10 && sockets.length < 2; i++) await new Promise((resolve) => setImmediate(resolve));
  const replacement = sockets[1];
  assert.ok(replacement);
  lateClose();
  t.mock.timers.tick(3000);
  assert.equal(replacement.readyState, 3, 'obsolete onclose released the new socket deadline');
});

test('usage ages locally, equal observation replay cannot freshen it, and a clock jump is conservative', async (t) => {
  const { updates, sockets, applied } = await setup(t);
  clearTimeout(updates.pingTimer);
  t.mock.timers.enable({ apis: ['setTimeout'] });
  const oldPerformance = globalThis.performance;
  const oldDateNow = Date.now;
  let wall = Date.parse(stamp);
  let monotonic = 1000;
  globalThis.performance = { now: () => monotonic };
  Date.now = () => wall;
  t.after(() => { globalThis.performance = oldPerformance; Date.now = oldDateNow; });
  for (const skew of [-3 * 60 * 60_000, 3 * 60 * 60_000]) {
    wall = Date.parse(stamp) + skew;
    updates.sampleClock(stamp, wall, monotonic);
    assert.ok(updates.transitUpper(stamp) >= 0.9 && updates.transitUpper(stamp) <= 1.1,
      'wall-clock skew must not make source readings younger');
  }
  wall = Date.parse(stamp);
  updates.sampleClock(stamp, wall, monotonic);

  const nodes = { n1: { node_id: 'n1', freshness: 'fresh', age_seconds: 7.5,
    reading: { observed_at: stamp, lifetime_id: 'first' } } };
  const areas = snapshot().areas;
  areas.usage = area(2, { nodes, relationships: [], recording: { recording: 'recording' } });
  sockets[0].receive({ type: 'data', generation: 1, batch: 1, initial: true, epoch, server_time: stamp,
    areas, logs: null });
  assert.equal(applied.findLast(([topic]) => topic === 'usage')[1].data.nodes.n1.freshness, 'fresh');
  monotonic += 1500;
  wall += 1500;
  t.mock.timers.tick(1500);
  assert.equal(applied.findLast(([topic]) => topic === 'usage')[1].data.nodes.n1.freshness, 'stale');

  areas.usage = area(3, { nodes: { n1: { ...nodes.n1, age_seconds: 0 } }, relationships: [],
    recording: { recording: 'recording' } });
  sockets[0].receive({ type: 'data', generation: 1, batch: 2, initial: false, epoch, server_time: stamp,
    areas: { usage: areas.usage }, logs: null });
  assert.equal(applied.findLast(([topic]) => topic === 'usage')[1].data.nodes.n1.freshness, 'stale');

  wall += 30_000;
  const next = { ...nodes.n1, age_seconds: 0, reading: { observed_at: stamp, lifetime_id: 'second' } };
  sockets[0].receive({ type: 'data', generation: 1, batch: 3, initial: false, epoch, server_time: stamp,
    areas: { usage: area(4, { nodes: { n1: next }, relationships: [], recording: {} }) }, logs: null });
  assert.equal(applied.findLast(([topic]) => topic === 'usage')[1].data.nodes.n1.freshness, 'stale');
});

test('only the matching client time-probe reply updates clock bounds', async (t) => {
  const { updates, sockets } = await setup(t);
  assert.ok(updates.openingProbe);
  sockets[0].receive({ type: 'hello', version: 1, epoch, server_time: stamp, heartbeat_ms: 2000 });
  assert.equal(updates.openingProbe, null);
  const probe = sockets[0].sent.find(({ type }) => type === 'ping');
  assert.ok(probe);
  sockets[0].receive({ type: 'pong', id: probe.id + 1, server_time: stamp });
  assert.equal(updates.pendingProbe?.id, probe.id);
  sockets[0].receive({ type: 'pong', id: probe.id, server_time: stamp });
  assert.equal(updates.pendingProbe, null);
  assert.ok(updates.clockSample);
});

test('late HTTP cleanup cannot release the newer snapshot request', async (t) => {
  const { updates } = await setup(t);
  const requests = [];
  globalThis.fetch = (_url, { signal }) => new Promise((resolve) => requests.push({ resolve, signal }));
  updates.refresh();
  updates.refresh();
  assert.equal(requests.length, 2);
  assert.equal(requests[0].signal.aborted, true);
  requests[0].resolve(new Response(JSON.stringify(snapshot())));
  await new Promise((resolve) => setTimeout(resolve, 0));
  assert.equal(requests[1].signal.aborted, false);

  updates.refresh();
  assert.equal(requests.length, 3);
  assert.equal(requests[1].signal.aborted, true);
  requests[1].resolve(new Response(JSON.stringify(snapshot())));
  requests[2].resolve(new Response(JSON.stringify(snapshot())));
});

test('snapshot deadline is deterministic under a controlled clock', async (t) => {
  const { updates } = await setup(t);
  t.mock.timers.enable({ apis: ['setTimeout'] });
  let signal;
  globalThis.fetch = (_url, options) => {
    signal = options.signal;
    return new Promise(() => {});
  };
  updates.refresh();
  t.mock.timers.tick(5000);
  assert.equal(signal.aborted, true);
});

test('silent socket switches to a labeled two-second snapshot fallback', async (t) => {
  const { sockets, health } = await setup(t);
  t.mock.timers.enable({ apis: ['setTimeout'] });
  let reads = 0;
  globalThis.fetch = async () => {
    reads++;
    return new Response(JSON.stringify(snapshot()), { status: 200 });
  };
  sockets[0].close();
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(health.at(-1), 'periodic-refresh');
  assert.equal(reads, 1);
  t.mock.timers.tick(2000);
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(reads, 2);
});

test('live log lines accept a missing tag and reject a tag that disagrees with source', async (t) => {
  const { sockets } = await setup(t);
  const socket = sockets[0];
  const frame = (batch, initial, entry) => ({
    type: 'data', generation: 1, batch, initial, epoch, server_time: stamp,
    areas: snapshot().areas,
    logs: {
      availability: 'available',
      sampled_at: stamp,
      snapshot: {
        lines: [entry],
        source: 'cluster',
        cursor: `${epoch}:${batch}`,
        reset: null,
        gap: false,
        gap_reason: null,
        continuity_id: epoch,
        discontinuity: null
      },
      error: null
    }
  });
  const entry = (id, source, tag) => ({
    event_id: `00000000-0000-4000-8000-${String(id).padStart(12, '0')}`,
    ts: 1,
    node_id: 'master',
    source,
    line: 'starting the dashboard',
    ...(tag === undefined ? {} : { tag })
  });

  socket.receive(frame(1, true, entry(1, 'llama')));
  assert.equal(socket.sent.some((item) => item.type === 'ack' && item.batch === 1), true);
  socket.receive(frame(2, false, entry(2, 'unknown')));
  assert.equal(socket.sent.some((item) => item.type === 'ack' && item.batch === 2), true);
  socket.receive(frame(3, false, entry(3, 'llama', 'ui')));
  assert.equal(socket.sent.some((item) => item.type === 'ack' && item.batch === 3), false);
  assert.equal(socket.readyState, 3);
});

test('live log lines reject a source outside the stored set', async (t) => {
  const { sockets } = await setup(t);
  const socket = sockets[0];
  socket.receive({
    type: 'data', generation: 1, batch: 1, initial: true, epoch, server_time: stamp,
    areas: snapshot().areas,
    logs: {
      availability: 'available',
      sampled_at: stamp,
      snapshot: {
        lines: [{
          event_id: '00000000-0000-4000-8000-000000000099',
          ts: 1,
          node_id: 'master',
          source: 'not-a-source',
          line: 'nope'
        }],
        source: 'cluster',
        cursor: `${epoch}:1`,
        reset: null,
        gap: false,
        gap_reason: null,
        continuity_id: epoch,
        discontinuity: null
      },
      error: null
    }
  });
  assert.equal(socket.sent.some((item) => item.type === 'ack' && item.batch === 1), false);
  assert.equal(socket.readyState, 3);
});
