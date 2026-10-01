import assert from 'node:assert/strict';
import test from 'node:test';
import { fileURLToPath } from 'node:url';
import { createServer } from 'vite';

process.chdir(fileURLToPath(new URL('..', import.meta.url)));

const server = await createServer({
  server: { middlewareMode: true, hmr: false, ws: false, watch: null },
  appType: 'custom'
});

test.after(async () => {
  try {
    await server?.watcher?.close();
  } catch {
    /* no watcher */
  }
  await server?.close();
});

function install() {
  const realSetTimeout = globalThis.setTimeout;
  const realClearTimeout = globalThis.clearTimeout;
  const realPerformance = globalThis.performance;
  const realFetch = globalThis.fetch;
  const realDocument = globalThis.document;
  let now = 0;
  let seq = 0;
  const timers = new Map();
  const calls = [];

  globalThis.setTimeout = (fn, delay = 0) => {
    const key = ++seq;
    timers.set(key, { fn, at: now + Number(delay) });
    return key;
  };
  globalThis.clearTimeout = (key) => {
    if (key != null) timers.delete(key);
  };
  globalThis.performance = { now: () => now };
  globalThis.fetch = (url, opts = {}) => {
    let resolve;
    let reject;
    const promise = new Promise((res, rej) => {
      resolve = res;
      reject = rej;
    });
    const record = { url: String(url), opts, resolve, reject };
    calls.push(record);
    const signal = opts.signal;
    if (signal) {
      signal.addEventListener('abort', () => {
        const err = new Error('aborted');
        err.name = 'AbortError';
        reject(err);
      });
    }
    return promise;
  };

  function advance(ms) {
    const target = now + ms;
    for (;;) {
      let nextKey = null;
      let nextAt = Infinity;
      for (const [key, timer] of timers) {
        if (timer.at <= target && timer.at < nextAt) {
          nextAt = timer.at;
          nextKey = key;
        }
      }
      if (nextKey === null) break;
      const timer = timers.get(nextKey);
      timers.delete(nextKey);
      now = timer.at;
      timer.fn();
    }
    now = target;
  }

  function bump(ms) {
    now += ms;
  }

  async function flush() {
    for (let i = 0; i < 8; i += 1) await new Promise((resolve) => setImmediate(resolve));
  }

  function respond(call, payload, status = 200) {
    call.resolve(
      new Response(JSON.stringify(payload), {
        status,
        headers: { 'content-type': 'application/json' }
      })
    );
  }

  function restore() {
    globalThis.setTimeout = realSetTimeout;
    globalThis.clearTimeout = realClearTimeout;
    globalThis.fetch = realFetch;
    if (realPerformance) globalThis.performance = realPerformance;
    else delete globalThis.performance;
    if (realDocument) globalThis.document = realDocument;
    else delete globalThis.document;
  }

  return { advance, bump, flush, calls, respond, restore, now: () => now,
    setDocument(value) { globalThis.document = value; } };
}

function fakeDocument() {
  const listeners = new Map();
  return {
    visibilityState: 'visible',
    addEventListener(name, fn) { listeners.set(name, fn); },
    removeEventListener(name) { listeners.delete(name); },
    changeVisibility(value) {
      this.visibilityState = value;
      listeners.get('visibilitychange')?.();
    }
  };
}

const EPOCH = '00000000-0000-4000-8000-000000000001';
const eventId = (n) => `00000000-0000-4000-8000-${String(n).padStart(12, '0')}`;

function snapshot(lines, { cursor = `${EPOCH}:1`, reset = null, gap = false, gapReason = null,
  source = 'cluster', warning } = {}) {
  return { lines, source, cursor, reset, gap, gap_reason: gapReason,
    ...(warning === undefined ? {} : { warning }) };
}

const master = { event_id: eventId(1), ts: 1, node_id: 'master', source: 'agent', line: '<b>one</b>' };
const worker = { event_id: eventId(2), ts: 2, node_id: 'w1', source: 'rpc', line: 'two' };

test('US1: loads, dedupes by id, filters by node, and times out stalled reads', async () => {
  const f = install();
  try {
    const { LogTrail } = await server.ssrLoadModule('/src/lib/log-trail.svelte.ts');
    const trail = new LogTrail();
    trail.start();
    assert.equal(f.calls.length, 1);
    assert.match(f.calls[0].url, /n=200/);
    f.respond(f.calls[0], snapshot([master, worker], { cursor: `${EPOCH}:2` }));
    await f.flush();
    assert.deepEqual(trail.entries.map((e) => e.event_id), [master.event_id, worker.event_id]);
    assert.equal(trail.cursor, `${EPOCH}:2`);
    assert.equal(trail.state, 'ready');

    trail.setNode('w1');
    assert.deepEqual(trail.visibleEntries().map((e) => e.event_id), [worker.event_id]);
    trail.setNode(null);

    f.advance(1000);
    assert.equal(f.calls.length, 2);
    assert.match(f.calls[1].url, /n=5000/);
    assert.match(f.calls[1].url, new RegExp(`after=${encodeURIComponent(`${EPOCH}:2`)}`));
    f.respond(f.calls[1], snapshot([worker], { cursor: `${EPOCH}:2` }));
    await f.flush();
    assert.equal(trail.entries.length, 2, 'replayed ids must not double-add');

    f.advance(1000);
    const before = f.calls.length;
    f.advance(4999);
    assert.equal(trail.errorText, '');
    f.advance(1);
    await f.flush();
    assert.match(trail.errorText, /timed out/i);
    assert.equal(trail.entries.length, 2, 'committed rows survive a timeout');
    f.advance(1000);
    assert.ok(f.calls.length > before, 'timeout must release the slot for retry');

    // A result that arrives after the deadline must not commit, even if its own
    // timer callback never ran.
    f.advance(1000);
    const pending = f.calls.at(-1);
    const stalled = f.calls.length;
    f.bump(6000);
    f.respond(pending, snapshot([{ ...worker, event_id: eventId(9) }], { cursor: `${EPOCH}:9` }));
    await f.flush();
    assert.equal(trail.entries.some((e) => e.event_id === 'late'), false);
    assert.equal(trail.cursor, `${EPOCH}:2`, 'expired result must not advance the checkpoint');
    assert.equal(f.calls.length, stalled);
    trail.stop();
  } finally {
    f.restore();
  }
});

test('read failures stay visibly degraded with retained rows and clear on recovery', async () => {
  const f = install();
  try {
    const { LogTrail } = await server.ssrLoadModule('/src/lib/log-trail.svelte.ts');
    const trail = new LogTrail();
    trail.start();
    f.respond(f.calls[0], snapshot([master], { cursor: `${EPOCH}:1`, warning: 'master unreachable' }));
    await f.flush();
    assert.equal(trail.warning, 'master unreachable');
    f.advance(1000);
    f.calls.at(-1).resolve(new Response('unavailable', { status: 503 }));
    await f.flush();
    assert.equal(trail.state, 'error');
    assert.equal(trail.entries.length, 1, 'failure retains prior rows');
    assert.equal(trail.cursor, `${EPOCH}:1`);
    f.advance(1000);
    f.respond(f.calls.at(-1), snapshot([master], { cursor: `${EPOCH}:1` }));
    await f.flush();
    assert.equal(trail.state, 'ready');
    assert.equal(trail.warning, '', 'recovery clears stale fallback warning');
    trail.stop();
  } finally {
    f.restore();
  }
});

test('US2: fixed cadence, mode transitions, no overlap, cleanup', async () => {
  const f = install();
  try {
    const { LogTrail, REFRESH_MS } = await server.ssrLoadModule('/src/lib/log-trail.svelte.ts');
    const trail = new LogTrail();
    trail.start();
    f.respond(f.calls[0], snapshot([master]));
    await f.flush();

    trail.setMode('30s');
    assert.equal(trail.refreshMode, '30s');
    f.advance(REFRESH_MS['30s'] - 1);
    assert.equal(f.calls.length, 1, 'must not poll before the selected interval');
    f.advance(1);
    assert.equal(f.calls.length, 2);
    // No overlap while the previous response is unresolved.
    f.advance(REFRESH_MS['30s']);
    assert.equal(f.calls.length, 2, 'one request in flight at a time');

    const modes = ['live', '5s', '30s', '1m'];
    for (const from of modes) {
      for (const to of modes) {
        if (from === to) continue;
        trail.setMode(from);
        trail.setMode(to);
        assert.equal(trail.refreshMode, to);
      }
    }

    trail.stop();
    const settled = f.calls.length;
    f.advance(60000);
    assert.equal(f.calls.length, settled, 'no requests after unmount');
    assert.equal(trail.entries.length, 0, 'unmount releases buffered rows');
  } finally {
    f.restore();
  }
});

test('mode change cancels the old request and resumes from its last committed cursor', async () => {
  const f = install();
  try {
    const { LogTrail } = await server.ssrLoadModule('/src/lib/log-trail.svelte.ts');
    const trail = new LogTrail();
    trail.start();
    f.respond(f.calls[0], snapshot([master], { cursor: `${EPOCH}:1` }));
    await f.flush();
    f.advance(1000);
    const oldRequest = f.calls.at(-1);
    trail.setMode('5s');
    assert.equal(oldRequest.opts.signal.aborted, true);
    f.respond(oldRequest, snapshot([worker], { cursor: `${EPOCH}:2` }));
    await f.flush();
    assert.deepEqual(trail.entries.map((row) => row.event_id), [master.event_id]);
    f.advance(4999);
    assert.equal(f.calls.length, 2, 'new cadence starts from the mode-selection time');
    f.advance(1);
    assert.equal(f.calls.length, 3);
    assert.match(f.calls.at(-1).url, new RegExp(`after=${encodeURIComponent(`${EPOCH}:1`)}`));
    f.respond(f.calls.at(-1), snapshot([worker], { cursor: `${EPOCH}:2` }));
    await f.flush();
    assert.deepEqual(trail.entries.map((row) => row.event_id), [master.event_id, worker.event_id]);
    trail.stop();
  } finally {
    f.restore();
  }
});

test('hidden page aborts active read and resumes with one immediate foreground read', async () => {
  const f = install();
  const doc = fakeDocument();
  try {
    const { LogTrail } = await server.ssrLoadModule('/src/lib/log-trail.svelte.ts');
    f.setDocument(doc);
    const trail = new LogTrail();
    trail.start();
    f.respond(f.calls[0], snapshot([master], { cursor: `${EPOCH}:1` }));
    await f.flush();
    f.advance(1000);
    const pending = f.calls.at(-1);
    doc.changeVisibility('hidden');
    assert.equal(pending.opts.signal.aborted, true);
    const count = f.calls.length;
    f.advance(60000);
    assert.equal(f.calls.length, count, 'hidden page suspends polling');
    doc.changeVisibility('visible');
    assert.equal(f.calls.length, count + 1, 'foreground resumes immediately once');
    f.respond(f.calls.at(-1), snapshot([worker], { cursor: `${EPOCH}:2` }));
    await f.flush();
    assert.deepEqual(trail.entries.map((row) => row.event_id), [master.event_id, worker.event_id]);
    trail.stop();
  } finally {
    f.restore();
  }
});

test('US3: clear is a no-op before first success and display-only afterwards', async () => {
  const f = install();
  try {
    const { LogTrail } = await server.ssrLoadModule('/src/lib/log-trail.svelte.ts');
    const trail = new LogTrail();
    trail.start();
    // Clear before any committed cursor: must not discard the pending initial read.
    trail.clear();
    assert.equal(trail.cursor, null);
    f.respond(f.calls[0], snapshot([master, worker], { cursor: `${EPOCH}:2` }));
    await f.flush();
    assert.deepEqual(trail.entries.map((e) => e.event_id), [master.event_id, worker.event_id]);
    assert.equal(trail.cursor, `${EPOCH}:2`);

    // Clear after an initial error is still a no-op.
    f.advance(1000);
    f.respond(f.calls.at(-1), { detail: 'boom' }, 503);
    await f.flush();
    assert.equal(trail.state, 'error');
    trail.clear();
    assert.equal(trail.entries.length, 0);
    assert.equal(trail.cursor, `${EPOCH}:2`);
    assert.equal(trail.state, 'cleared');

    // Clear during an in-flight read: the stale result cannot restore cleared rows.
    f.advance(1000);
    const pending = f.calls.at(-1);
    trail.clear();
    f.respond(pending, snapshot([master, worker], { cursor: `${EPOCH}:2` }));
    await f.flush();
    assert.equal(trail.entries.length, 0, 'stale in-flight result must not restore cleared rows');

    // A feed reset after an effective clear adopts the checkpoint and reports the gap.
    f.advance(1000);
    f.respond(
      f.calls.at(-1),
      snapshot([{ ...master, event_id: eventId(3) }], {
        cursor: '00000000-0000-4000-8000-000000000002:5',
        reset: 'origin_changed',
        gap: true,
        gapReason: 'feed_reset'
      })
    );
    await f.flush();
    assert.equal(trail.entries.length, 0);
    assert.equal(trail.cursor, '00000000-0000-4000-8000-000000000002:5');
    assert.equal(trail.gap, true);

    // Newer, unseen events resume normally.
    f.advance(1000);
    f.respond(f.calls.at(-1), snapshot([{ ...worker, event_id: eventId(4) }], { cursor: '00000000-0000-4000-8000-000000000002:6' }));
    await f.flush();
    assert.deepEqual(trail.entries.map((e) => e.event_id), [eventId(4)]);
    assert.equal(trail.state, 'ready');

    // Clear must never issue a write.
    assert.ok(f.calls.every((c) => c.url.startsWith('/v2/logs?') && (c.opts.method ?? 'GET') === 'GET'));
    trail.stop();
  } finally {
    f.restore();
  }
});

test('long visits retain exact UUID dedup across row eviction, Clear and overlapping snapshots', async () => {
  const f = install();
  const doc = fakeDocument();
  f.setDocument(doc);
  try {
    const { LogTrail } = await server.ssrLoadModule('/src/lib/log-trail.svelte.ts');
    const trail = new LogTrail();
    trail.setMode('5s');
    trail.start();
    const retained = Array.from({ length: 2001 }, (_, index) => ({
      ...master,
      event_id: eventId(index + 100),
      ts: 10,
      line: 'same text'
    }));
    f.respond(f.calls[0], snapshot(retained, { cursor: `${EPOCH}:2001` }));
    await f.flush();
    assert.equal(trail.entries.length, 2000);
    assert.equal(trail.seenIds.size, 2001);
    assert.equal(trail.omittedRows, 1);

    trail.clear();
    doc.changeVisibility('hidden');
    doc.changeVisibility('visible');
    assert.equal(f.calls.length, 2);
    const fresh = { ...worker, event_id: eventId(9000), ts: 10, line: 'same text' };
    f.respond(f.calls[1], snapshot([retained[0], fresh], { cursor: `${EPOCH}:2002` }));
    await f.flush();
    assert.deepEqual(trail.entries.map((entry) => entry.event_id), [fresh.event_id]);
    assert.equal(trail.seenIds.size, 2002);
    trail.stop();
    assert.equal(trail.seenIds.size, 0);
  } finally {
    f.restore();
  }
});

test('Clear treats an unseen log-continuity change as a baseline and keeps the gap visible', async () => {
  const f = install();
  try {
    const { LogTrail } = await server.ssrLoadModule('/src/lib/log-trail.svelte.ts');
    const trail = new LogTrail();
    const continuityA = '00000000-0000-4000-8000-000000000010';
    const continuityB = '00000000-0000-4000-8000-000000000011';
    trail.commit({ ...snapshot([master], { cursor: `${EPOCH}:1` }), continuity_id: continuityA, discontinuity: null });
    trail.clear();
    trail.commit({ ...snapshot([worker], { cursor: `${EPOCH}:1` }), continuity_id: continuityB,
      discontinuity: { id: continuityB, reason: 'coverage_change', detected_at: '2026-09-29T12:00:00Z' } });
    assert.equal(trail.entries.length, 0, 'rows from a missed continuity interval stay cleared');
    assert.equal(trail.continuityId, continuityB);
    assert.equal(trail.gap, true);

    const next = { ...worker, event_id: eventId(3), line: 'after recovery' };
    trail.commit({ ...snapshot([next], { cursor: `${EPOCH}:2` }), continuity_id: continuityB, discontinuity: null });
    assert.deepEqual(trail.entries.map((entry) => entry.event_id), [next.event_id]);
    trail.stop();
  } finally {
    f.restore();
  }
});

test('known log gap remains visible until the visit is cleared', async () => {
  const f = install();
  try {
    const { LogTrail } = await server.ssrLoadModule('/src/lib/log-trail.svelte.ts');
    const trail = new LogTrail();
    trail.commit(snapshot([master], { cursor: `${EPOCH}:1`, gap: true, gapReason: 'retention' }));
    trail.commit(snapshot([worker], { cursor: `${EPOCH}:2` }));
    assert.equal(trail.gap, true);
    trail.clear();
    assert.equal(trail.gap, false);
    trail.stop();
  } finally {
    f.restore();
  }
});

test('Live fallback owns an independent log GET lane and resets old-server cursor after Clear', async () => {
  const f = install();
  const { appStore } = await server.ssrLoadModule('/src/lib/store.svelte.ts');
  const realListener = appStore.setLogListener;
  const realHealth = appStore.deliveryHealth;
  const doc = fakeDocument();
  f.setDocument(doc);
  appStore.setLogListener = () => true;
  appStore.deliveryHealth = 'periodic-refresh';
  let trail;
  try {
    const { LogTrail } = await server.ssrLoadModule('/src/lib/log-trail.svelte.ts');
    trail = new LogTrail();
    trail.start();
    f.advance(2000);
    assert.equal(f.calls.length, 1);
    assert.match(f.calls[0].url, /\/v2\/events\?topics=&logs=1/);
    const continuity = '00000000-0000-4000-8000-000000000003';
    f.respond(f.calls[0], { version: 1, logs: { availability: 'available', snapshot: {
      ...snapshot([master], { cursor: `${EPOCH}:1` }), continuity_id: continuity, discontinuity: null },
      error: null } });
    await f.flush();
    assert.equal(trail.cursor, `${EPOCH}:1`);
    trail.clear();
    f.advance(2000);
    assert.equal(f.calls.length, 2);
    assert.match(f.calls[1].url, /continuity_id=/);
    f.respond(f.calls[1], { detail: 'old route' }, 404);
    await f.flush();
    assert.equal(f.calls.length, 3);
    assert.match(f.calls[2].url, /\/v2\/logs\?n=5000/);
    assert.doesNotMatch(f.calls[2].url, /after=/, 'a mirror cursor must not be passed to old-server Logs');
    f.respond(f.calls[2], snapshot([worker], { cursor: `${EPOCH}:2` }));
    await f.flush();
    assert.equal(trail.entries.length, 0, 'Clear must not restore legacy handoff history');
    assert.equal(trail.gap, true);
    assert.equal(trail.continuityId, null);
  } finally {
    trail?.stop();
    appStore.setLogListener = realListener;
    appStore.deliveryHealth = realHealth;
    f.restore();
  }
});

test('Live log access denial stops fallback reads without bypassing through old routes', async () => {
  const f = install();
  const { appStore } = await server.ssrLoadModule('/src/lib/store.svelte.ts');
  const realListener = appStore.setLogListener;
  const realHealth = appStore.deliveryHealth;
  appStore.setLogListener = () => true;
  appStore.deliveryHealth = 'periodic-refresh';
  let trail;
  try {
    const { LogTrail } = await server.ssrLoadModule('/src/lib/log-trail.svelte.ts');
    trail = new LogTrail();
    trail.start();
    f.advance(2000);
    assert.equal(f.calls.length, 1);
    f.respond(f.calls[0], { version: 1, logs: { availability: 'denied', snapshot: null,
      error: { code: 'forbidden', message: 'Log access denied' } } });
    await f.flush();
    assert.equal(trail.state, 'error');
    assert.match(trail.errorText, /access denied/i);
    f.advance(6000);
    assert.equal(f.calls.length, 1, 'a denied log area was retried without explicit access change');
  } finally {
    trail?.stop();
    appStore.setLogListener = realListener;
    appStore.deliveryHealth = realHealth;
    f.restore();
  }
});

test('buffer caps at 2000 rows and reports trimming', async () => {
  const f = install();
  try {
    const { LogTrail, MAX_ROWS } = await server.ssrLoadModule('/src/lib/log-trail.svelte.ts');
    const trail = new LogTrail();
    trail.start();
    const lines = Array.from({ length: MAX_ROWS + 100 }, (_, i) => ({
      event_id: eventId(i + 100),
      ts: i,
      node_id: 'master',
      source: 'agent',
      line: `line ${i}`
    }));
    f.respond(f.calls[0], snapshot(lines, { cursor: `${EPOCH}:1` }));
    await f.flush();
    assert.equal(trail.entries.length, MAX_ROWS);
    assert.equal(trail.omittedRows, 100);
    assert.equal(trail.gap, true);
    trail.stop();
  } finally {
    f.restore();
  }
});

test('rejects malformed snapshots and times out while consuming a response body', async () => {
  const f = install();
  try {
    const { LogTrail } = await server.ssrLoadModule('/src/lib/log-trail.svelte.ts');
    const trail = new LogTrail();
    trail.start();
    f.respond(f.calls[0], { lines: [master], source: 'cluster' });
    await f.flush();
    assert.equal(trail.entries.length, 0);
    assert.match(trail.errorText, /missing its cursor/i);
    assert.equal(trail.cursor, null);

    const malformed = [
      { ...snapshot([], { cursor: `${EPOCH}:1` }), lines: 'not-an-array' },
      snapshot([{ ...master, event_id: 'not-a-uuid' }], { cursor: `${EPOCH}:1` }),
      snapshot([{ ...master, ts: null }], { cursor: `${EPOCH}:1` }),
      snapshot([{ ...master, source: 'not-a-source' }], { cursor: `${EPOCH}:1` }),
      snapshot([], { cursor: 'not-a-canonical-cursor' })
    ];
    for (const payload of malformed) {
      f.advance(1000);
      f.respond(f.calls.at(-1), payload);
      await f.flush();
      assert.equal(trail.state, 'error');
      assert.equal(trail.cursor, null, 'invalid snapshots cannot advance a checkpoint');
    }

    // The next cadence starts a read whose headers arrive but whose body stalls.
    f.advance(1000);
    const call = f.calls.at(-1);
    call.resolve({
      ok: true,
      status: 200,
      json: () => new Promise((_resolve, reject) => {
        call.opts.signal.addEventListener('abort', () => {
          const error = new Error('aborted body');
          error.name = 'AbortError';
          reject(error);
        }, { once: true });
      })
    });
    await f.flush();
    f.advance(5000);
    await f.flush();
    assert.equal(trail.entries.length, 0);
    assert.match(trail.errorText, /timed out/i);
    trail.stop();
  } finally {
    f.restore();
  }
});

test('producer tags filter the visit without a new request and survive Clear', async () => {
  const f = install();
  try {
    const { LogTrail } = await server.ssrLoadModule('/src/lib/log-trail.svelte.ts');
    const trail = new LogTrail();
    trail.start();
    assert.equal(trail.selectedTag, '');
    const llama = {
      event_id: eventId(3),
      ts: 3,
      node_id: 'master',
      source: 'llama',
      line: 'control application asked for a slot'
    };
    const unknown = { event_id: eventId(4), ts: 4, node_id: 'w1', source: 'unknown', line: 'mystery' };
    f.respond(f.calls[0], snapshot([master, worker, llama, unknown], { cursor: `${EPOCH}:4` }));
    await f.flush();
    assert.deepEqual(trail.entries.map((entry) => entry.tag), ['ui', 'inference', 'inference', 'unclassified']);

    const calls = f.calls.length;
    trail.selectedTag = 'ui';
    assert.deepEqual(trail.visibleEntries().map((entry) => entry.event_id), [master.event_id]);
    trail.selectedTag = 'inference';
    assert.deepEqual(trail.visibleEntries().map((entry) => entry.line), [worker.line, llama.line]);
    trail.selectedTag = 'unclassified';
    assert.deepEqual(trail.visibleEntries().map((entry) => entry.line), ['mystery']);
    trail.selectedTag = '';
    assert.equal(trail.visibleEntries().length, 4);
    assert.equal(f.calls.length, calls, 'tag selection issues no log request');

    trail.setMode('30s');
    trail.setNode('w1');
    trail.selectedTag = 'inference';
    assert.deepEqual(trail.visibleEntries().map((entry) => entry.event_id), [worker.event_id]);
    trail.selectedTag = 'ui';
    assert.equal(trail.selectedNodeId, 'w1');
    assert.equal(trail.refreshMode, '30s');
    assert.equal(trail.state, 'ready');
    assert.equal(trail.visibleEntries().length, 0);
    assert.equal(trail.entries.length, 4);

    trail.gap = true;
    trail.omittedRows = 3;
    trail.selectedTag = '';
    assert.equal(trail.gap, true);
    assert.equal(trail.omittedRows, 3);

    trail.selectedTag = 'ui';
    trail.clear();
    assert.equal(trail.entries.length, 0);
    assert.equal(trail.selectedTag, 'ui');
    assert.equal(trail.selectedNodeId, 'w1');
    assert.equal(trail.refreshMode, '30s');
    trail.selectedTag = '';
    trail.setNode('');
    assert.equal(trail.visibleEntries().length, 0);
    trail.stop();

    const early = new LogTrail();
    early.start();
    early.selectedTag = 'ui';
    const pending = f.calls.at(-1);
    early.clear();
    assert.equal(early.cursor, null);
    assert.equal(early.state, 'loading');
    f.respond(pending, snapshot([llama], { cursor: `${EPOCH}:9` }));
    await f.flush();
    assert.equal(early.entries.length, 1);
    assert.equal(early.entries[0].tag, 'inference');
    assert.equal(early.visibleEntries().length, 0);
    assert.equal(early.state, 'ready');
    early.stop();

    const bad = new LogTrail();
    bad.start();
    f.respond(f.calls.at(-1), snapshot([{ ...llama, event_id: eventId(11), tag: 'ui' }], { cursor: `${EPOCH}:11` }));
    await f.flush();
    assert.equal(bad.state, 'error');
    assert.equal(bad.cursor, null);
    f.advance(1000);
    f.respond(f.calls.at(-1), snapshot([{ ...master, event_id: eventId(12), tag: 'other' }], { cursor: `${EPOCH}:12` }));
    await f.flush();
    assert.equal(bad.cursor, null);
    f.advance(1000);
    f.respond(f.calls.at(-1), snapshot([{ ...unknown, event_id: eventId(13) }], { cursor: `${EPOCH}:13` }));
    await f.flush();
    assert.equal(bad.cursor, `${EPOCH}:13`);
    assert.equal(bad.entries[0].tag, 'unclassified');
    bad.stop();
  } finally {
    f.restore();
  }
});

test('an empty visit keeps the empty-visit copy when a tag is selected', async () => {
  const { logListEmptyCopy } = await server.ssrLoadModule('/src/routes/logs/+page.svelte');
  const copy = (over = {}) => logListEmptyCopy({
    state: 'empty',
    buffered: 0,
    selectedNodeId: '',
    selectedTag: '',
    ...over
  });
  assert.equal(copy({ selectedTag: 'inference' }), 'No log entries yet.');
  assert.equal(copy({ selectedTag: 'ui', selectedNodeId: 'w1' }), 'No log entries yet.');
  assert.equal(copy({ state: 'cleared', selectedTag: 'ui' }), 'Display cleared — waiting for a new entry.');
  assert.equal(
    copy({ state: 'ready', buffered: 4, selectedTag: 'inference' }),
    'No entries for the selected tag in the current buffer.'
  );
  assert.equal(
    copy({ state: 'ready', buffered: 4, selectedNodeId: 'w1', selectedTag: 'ui' }),
    'No entries match the selected node and tag in the current buffer.'
  );
  assert.equal(copy({ selectedNodeId: 'w1' }), 'No entries for the selected node in the current buffer.');
});
