import assert from 'node:assert/strict';
import test from 'node:test';
import { fileURLToPath } from 'node:url';
import { createServer } from 'vite';

test('history includes bounded keyboard-readable zero, gaps, UTC and failure state', async () => {
  const cwd = process.cwd();
  process.chdir(fileURLToPath(new URL('..', import.meta.url)));
  let server;
  try {
    server = await createServer({ server: { middlewareMode: true, hmr: false, watch: null }, appType: 'custom' });
    const { render } = await server.ssrLoadModule('svelte/server');
    const { default: UsageHistory } = await server.ssrLoadModule('/src/lib/components/UsageHistory.svelte');
    const life = { lifetime_id: 'w1:12:2026-09-28T12:00:00Z', pid: 12, state: 'ended', started_at: '2026-09-28T12:00:00Z',
      associations: [{ model: 'alpha.gguf', observed_from: '2026-09-28T12:00:00Z', observed_until: '2026-09-28T12:00:02Z' }] };
    const reading = { observed_at: '2026-09-28T12:00:02Z', process: { cpu_percent_machine: 0, rss_bytes: 0 },
      host: { cpu_percent: 12, used_bytes: 2 * 1024 ** 3, total_bytes: 8 * 1024 ** 3 },
      devices: [{ device_id: '0', scope: 'device', utilization_percent: 33,
        used_bytes: 1 * 1024 ** 3, total_bytes: 8 * 1024 ** 3, temperature_c: 55, power_w: 130 }] };
    const body = render(UsageHistory, { props: {
      nodeId: 'w1', lifetimes: [life], selectedLifetimeId: life.lifetime_id, window: '15m',
      series: { buckets: [{ start: '2026-09-28T12:00:00Z', count: 1, process_cpu_min: 0, process_rss_min: 0,
        devices: [{ device_id: '0', scope: 'device', utilization_percent_min: 33, utilization_percent_max: 33,
          used_bytes_min: 1 * 1024 ** 3, used_bytes_max: 1 * 1024 ** 3 }] },
        { start: '2026-09-28T12:00:04Z', count: 0 }] },
      page: { readings: [reading], next_cursor: null },
      recorder: { recording: 'failed', last_successful_save: '2026-09-28T12:00:02Z', storage_truncated: true },
      onlifetime: () => {}, onwindow: () => {}, onnext: () => {}
    } }).body;
    assert.match(body, /15m/);
    assert.match(body, /1h/);
    assert.match(body, /24h/);
    assert.match(body, /gap/i);
    assert.match(body, /0%/);
    assert.match(body, /failed/i);
    assert.match(body, /2026-09-28T12:00:02Z/);
    assert.match(body, /storage/i);
    assert.match(body, /alpha\.gguf.*2026-09-28T12:00:00Z.*2026-09-28T12:00:02Z/s);
    assert.match(body, /RSS.*device 0.*33%/is);
    assert.match(body, /55 °C.*130 W/is);

    const { appStore } = await server.ssrLoadModule('/src/lib/store.svelte.ts');
    const priorFetch = globalThis.fetch;
    const pending = [];
    globalThis.fetch = (path) => new Promise((resolve) => pending.push({ path: String(path), resolve }));
    try {
      const reply = (call, payload) => call.resolve(new Response(JSON.stringify(payload), { status: 200 }));
      appStore.selectedNodeId = 'w1';
      appStore.selectedLifetimeId = 'l1';
      appStore.usageWindow = '15m';
      appStore.usageSeries = { buckets: [{ count: 1, start: 'old' }] };
      appStore.usagePage = { readings: [{ observed_at: 'old' }], next_cursor: 'old' };
      const oldLoad = appStore.loadUsageHistory();
      assert.equal(appStore.usageSeries, null, 'old series must not look like a new filter');
      assert.equal(appStore.usagePage, null);
      const newLoad = appStore.selectUsageWindow('1h');
      reply(pending[2], { buckets: [{ count: 1, start: 'new' }], window_start: 'new' });
      reply(pending[3], { readings: [{ observed_at: 'new' }], next_cursor: 'next' });
      await newLoad;
      reply(pending[0], { buckets: [{ count: 1, start: 'old' }] });
      reply(pending[1], { readings: [{ observed_at: 'old' }] });
      await oldLoad;
      assert.equal(appStore.usagePage.readings[0].observed_at, 'new');
      const oldPage = appStore.nextUsagePage();
      const latest = appStore.selectUsageWindow('24h');
      reply(pending[4], { readings: [{ observed_at: 'outdated-page' }], next_cursor: null });
      await oldPage;
      reply(pending[5], { buckets: [{ count: 1, start: 'latest' }] });
      reply(pending[6], { readings: [{ observed_at: 'latest' }], next_cursor: null });
      await latest;
      assert.deepEqual(appStore.usagePage.readings.map((point) => point.observed_at), ['latest']);
    } finally {
      globalThis.fetch = priorFetch;
      appStore.stop();
    }
  } finally {
    try { await server?.watcher?.close(); } catch { /* no watcher */ }
    await server?.close();
    process.chdir(cwd);
  }
});
