import assert from 'node:assert/strict';
import test from 'node:test';
import { fileURLToPath } from 'node:url';
import { createServer } from 'vite';

process.chdir(fileURLToPath(new URL('..', import.meta.url)));

const server = await createServer({
  server: { middlewareMode: true, hmr: false, watch: null },
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
    assert.ok(f.calls.every((c) => c.url.startsWith('/v1/logs?') && (c.opts.method ?? 'GET') === 'GET'));
    trail.stop();
  } finally {
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
      snapshot([{ ...master, source: 'unknown' }], { cursor: `${EPOCH}:1` }),
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
