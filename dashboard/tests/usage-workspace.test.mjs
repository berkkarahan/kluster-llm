import assert from 'node:assert/strict';
import test from 'node:test';
import { fileURLToPath } from 'node:url';
import { createServer as createHttpServer } from 'node:http';
import { spawn } from 'node:child_process';
import { existsSync, mkdtempSync, readFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { createServer } from 'vite';
import { removeBrowserProfile } from './browser-profile.mjs';

const GIB = 1024 ** 3;

test('workspace keeps controls and chat reachable with textual live/offline states', async () => {
  const cwd = process.cwd();
  process.chdir(fileURLToPath(new URL('..', import.meta.url)));
  let server;
  try {
    server = await createServer({ server: { middlewareMode: true, hmr: false, ws: false, watch: null }, appType: 'custom' });
    const appStore = (await server.ssrLoadModule('/src/lib/app.svelte.ts')).createApp();
    const { render } = await server.ssrLoadModule('svelte/server');
    const { default: Home } = await server.ssrLoadModule('/src/routes/+page.svelte');
    appStore.cluster.statusSeen = true;
    appStore.live.connected = true;
    appStore.cluster.status = { role: 'master', node_id: 'master', members: {
      master: { node_id: 'master', host: 'example', role: 'master' }
    }, inference_up: false };
    appStore.usage.liveUsage = { nodes: {
      master: { node_id: 'master', host: 'example', role: 'master',
        connected: true, freshness: 'unavailable', reading: null },
      gone: { node_id: 'gone', host: 'departed-host', role: 'worker',
        connected: false, freshness: 'stale', reading: null },
    }, relationships: [{ from_node_id: 'master', to_node_id: 'gone', kind: 'master-worker' }],
      recording: { recording: 'failed', last_successful_save: null } };
    appStore.usage.selectedNodeId = 'master';
    const live = render(Home).body;
    for (const control of ['Start', 'Stop', 'Join', 'Leave', 'Update workers', 'Details', 'History'])
      assert.match(live, new RegExp(control));
    assert.match(live, /href="\/run"/);
    assert.match(live, /href="\/integrations"/);
    assert.match(live, /Unmeasured/);
    assert.match(live, /recording.*failed/is);
    assert.match(live, /unavailable/);
    assert.match(live, /example.*master/s);
    assert.doesNotMatch(live, /departed-host/);
    assert.match(live, /No known assignment/);
    assert.doesNotMatch(live, /Row \(legacy\)|Tensor \(experimental\)/);
    // No permanent inspector and no eager history content.
    assert.doesNotMatch(live, /node-inspector/);
    assert.doesNotMatch(live, /Recorded usage/);
    appStore.live.connected = false;
    assert.match(render(Home).body, /Disconnected.*not live/is);
    appStore.live.connected = true;
    appStore.cluster.status = { role: 'idle', node_id: 'master', members: {} };
    const stopped = render(Home).body;
    assert.match(stopped, /No members — Start as master or Join a cluster\./);
    assert.doesNotMatch(stopped, /example/);
    assert.doesNotMatch(stopped, /departed-host/);
    // Local agent availability must not make its saved worker mirror live.
    appStore.cluster.status = { role: 'worker', node_id: 'worker', members: {
      master: { node_id: 'master', host: 'saved-master', role: 'master' },
      worker: { node_id: 'worker', host: 'saved-worker', role: 'worker' }
    }, netsec_warnings: ['master heartbeat unreachable'] };
    for (const usage of [null, appStore.usage.liveUsage]) {
      appStore.usage.liveUsage = usage;
      const disconnected = render(Home).body;
      assert.equal(appStore.cluster.viewState(), 'offline');
      assert.match(disconnected, /0 connected/);
      assert.match(disconnected, /data-link-state="disconnected"/);
    }
    // A successful heartbeat clears the warning and restores current members.
    appStore.cluster.status = { ...appStore.cluster.status, netsec_warnings: [] };
    assert.equal(appStore.cluster.viewState(), 'live');
    assert.match(render(Home).body, /2 connected/);
  } finally {
    try { await server?.watcher?.close(); } catch { /* no watcher */ }
    await server?.close();
    process.chdir(cwd);
  }
});

const chrome = [process.env.CHROME_BIN, 'C:/Program Files/Google/Chrome/Application/chrome.exe',
  'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',
  '/usr/bin/google-chrome', '/usr/bin/chromium',
  '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'].find((path) => path && existsSync(path));

function buildCluster(mode) {
  if (mode === 'empty') return { nodes: {}, relationships: [] };
  const count = mode === 'one' ? 1 : mode === 'four' ? 4 : 16;
  const longDeviceName = 'NVIDIA RTX PRO 6000 Blackwell Server Edition GPU Accelerator';
  const ids = ['master', ...Array.from({ length: count - 1 }, (_, i) => `w${i + 1}`)];
  const nodes = {};
  ids.forEach((id, i) => {
    const isMaster = i === 0;
    const devices = i === 1
      ? [{ index: 0, name: 'GPU-A', vram_bytes: 8 * GIB }, { index: 1, name: 'GPU-B' }]
      : [{ index: 0, name: i === 3 ? longDeviceName : 'GPU', vram_bytes: 8 * GIB }];
    nodes[id] = {
      node_id: id, host: i === 0 ? 'master-host' : i <= 2 ? 'shared-worker-host' : `${id}-host`,
      role: isMaster ? 'master' : 'worker', backend: 'vulkan', devices, unified_memory: false,
      total_ram_bytes: 16 * GIB, assignment: isMaster ? 'alpha.gguf' : null,
      connected: true, process_state: 'running', freshness: 'fresh', age_seconds: 2,
      reading: {
        lifetime_id: `${id}:99:2026-09-29T00:00:00Z`, pid: 99,
        process_role: isMaster ? 'master' : 'worker', process_started_at: '2026-09-29T00:00:00Z',
        observed_at: '2026-09-29T00:00:02Z',
        process: { cpu_percent_machine: 4 + i, rss_bytes: 2 * GIB },
        host: { cpu_percent: 10 + i, used_bytes: (i % 4 + 1) * 2 * GIB, total_bytes: 16 * GIB },
        devices: devices.map((d, di) => ({
          device_id: String(d.index), scope: 'device',
          utilization_percent: di === 1 ? 0 : (i * 7) % 100,
          used_bytes: di === 1 ? 0 : 2 * GIB, total_bytes: d.vram_bytes ?? 4 * GIB,
          temperature_c: 50, power_w: 100, unavailable_reason: null
        })),
        availability: { process_cpu: 'measured', process_rss: 'measured',
          host_cpu: 'measured', host_memory: 'measured' }
      }
    };
    if (mode === 'four') {
      const metric = (value, unit = 'bytes') => ({ value, unit, scope: 'device',
        status: value === null ? 'unsupported' : 'measured', reason: value === null ? 'unsupported_counter' : null,
        source: 'test.driver', observed_at: new Date().toISOString() });
      nodes[id].resources = { schema_version: 2, node_id: id, generation: 'a'.repeat(32), sequence: 1,
        snapshot_id: `${'a'.repeat(32)}:1`, requested_at: new Date().toISOString(), completed_at: new Date().toISOString(),
        host: { cpu: metric(10 + i, 'percent_machine'), memory: { used: metric(8 * GIB), total: metric(32 * GIB),
          available: metric(16 * GIB), free: metric(12 * GIB) } },
        devices: [{ device_key: `gpu:${id}`, name: i === 1 ? 'AMD Radeon RX 9070 XT' : i === 2 ? 'Apple M5 Pro' : 'Apple M1 Pro',
          memory_kind: i === 1 ? 'dedicated' : 'shared', utilization: metric(i === 0 ? 0 : 43, 'percent_device'),
          memory: [{ kind: i === 1 ? 'dedicated' : 'shared', backing_pool_id: i === 1 ? null : 'host:ram',
            used: metric(i === 1 ? 15 * GIB : .5 * GIB), total: metric(i === 1 ? 16 * GIB : null),
            free: metric(i === 1 ? GIB : null) }] }],
        processes: [], device_discovery: { status: 'measured', reason: null },
        totals: { physical_capacity: metric(i === 1 ? 48 * GIB : 32 * GIB) } };
      nodes[id].resource_freshness = 'fresh';
      nodes[id].resource_age_seconds = 1;
    }
  });
  const relationships = ids.filter((id) => id !== 'master')
    .map((id) => ({ from_node_id: 'master', to_node_id: id, kind: 'master-worker' }));
  return { nodes, relationships };
}

test('browser workspace fills the viewport, shows node-local telemetry and defers history',
  { skip: !chrome && 'Install Chrome/Edge or set CHROME_BIN to run browser check' }, async () => {
    const cwd = process.cwd();
    process.chdir(fileURLToPath(new URL('..', import.meta.url)));
    const profile = mkdtempSync(join(tmpdir(), 'kllm-monitor-'));
    let browser, vite, server, ws;
    try {
      let mode = 'sixteen';
      const counts = { lifetimes: 0, series: 0, readings: 0, live: 0 };
      let logsWrites = 0;
      let logsFailure = false;
      let logsWarning = '';
      let logsBulk = false;
      let logsEmpty = false;
      let liveIncrementalReads = 0;
      let allowNewLog = false;
      const json = (res, payload, status = 200) =>
        res.writeHead(status, { 'Content-Type': 'application/json' }).end(JSON.stringify(payload));

      vite = await createServer({ server: { middlewareMode: true, hmr: false, ws: false, watch: null }, appType: 'custom' });
      server = createHttpServer((req, res) => {
        const path = req.url.split('?')[0];
        if (path === '/__counts') { json(res, counts); return; }
        if (!path.startsWith('/v2/')) { vite.middlewares(req, res, () => res.writeHead(404).end()); return; }
        if (mode === 'offline') { json(res, { detail: 'offline' }, 503); return; }
        const { nodes, relationships } = buildCluster(mode === 'empty' ? 'empty' : mode);
        const recorder = { recording: 'recording', error: null, last_successful_save: '2026-09-29T00:00:02Z',
          earliest_retained: '2026-09-29T00:00:00Z', latest_retained: '2026-09-29T00:00:02Z',
          storage_truncated: false, coverage: 'recorded' };
        const status = { role: mode === 'empty' ? 'idle' : 'master', node_id: 'master', inference_up: false,
          netsec_warnings: ['RPC port 50052 reachable from the public interface'],
          overlay_status: 'up 10.66.87.1 (listen 51820)',
          update: { phase: 'failed', pct: 40,
            workers: [{ host: 'worker-b7', phase: 'failed', error: 'git pull failed' }] },
          members: mode === 'empty' ? {} : Object.fromEntries(Object.entries(nodes)
            .map(([id, n]) => [id, { node_id: id, host: n.host, role: n.role }])) };
        const usage = { nodes: mode === 'empty' ? {} : nodes,
          relationships: mode === 'empty' ? [] : relationships, recording: recorder };
        if (path === '/v2/events') {
          counts.live++;
          const after = new URL(req.url, 'http://x').searchParams.get('after');
          const lines = logsEmpty
            ? []
            : after && (++liveIncrementalReads === 1 || !allowNewLog)
            ? []
            : after
            ? [{ event_id: '00000000-0000-4000-8000-000000000003', ts: 3, node_id: 'w1', source: 'rpc', line: '<b>three</b>' }]
            : [
                { event_id: '00000000-0000-4000-8000-000000000001', ts: 1, node_id: 'master', source: 'agent', line: 'boot ok' },
                { event_id: '00000000-0000-4000-8000-000000000002', ts: 2, node_id: 'w1', source: 'rpc', line: 'worker ready' }
              ];
          const logs = new URL(req.url, 'http://x').searchParams.get('logs') === '1'
            ? logsFailure
              ? { availability: 'unavailable', sampled_at: null, snapshot: null,
                  error: { code: 'source_unavailable', message: 'Logs are unavailable' } }
              : { availability: 'available', sampled_at: '2026-09-29T00:00:02Z', error: null,
                  snapshot: { lines, source: logsWarning ? 'local' : 'cluster',
                    ...(logsWarning ? { warning: logsWarning } : {}),
                    cursor: after ? '00000000-0000-4000-8000-000000000001:3' : '00000000-0000-4000-8000-000000000001:2',
                    reset: null, gap: false, gap_reason: null, continuity_id: '00000000-0000-4000-8000-000000000001', discontinuity: null } }
            : null;
          const areas = {
            status: { revision: 1, sampled_at: '2026-09-29T00:00:02Z', availability: 'available',
              coverage: 'cluster', upstream_last_success_at: null, data: status, error: null },
            usage: { revision: 1, sampled_at: '2026-09-29T00:00:02Z', availability: 'available',
              coverage: 'cluster', upstream_last_success_at: null, data: usage, error: null },
            load: { revision: 1, sampled_at: '2026-09-29T00:00:02Z', availability: 'available',
              coverage: 'cluster', upstream_last_success_at: null, data: { current: null, last_terminal: null }, error: null },
            download: { revision: 1, sampled_at: '2026-09-29T00:00:02Z', availability: 'available',
              coverage: 'cluster', upstream_last_success_at: null, data: { current: null, last_terminal: null }, error: null }
          };
          if (mode === 'usage_denied') areas.usage = {
            ...areas.usage, availability: 'denied', sampled_at: null, data: null,
            error: { code: 'forbidden', message: 'Access denied' }
          };
          const requested = new URL(req.url, 'http://x').searchParams.get('topics')?.split(',') ?? [];
          json(res, { version: 1, transport: 'websocket', epoch: '00000000-0000-4000-8000-000000000001',
            server_time: '2026-09-29T00:00:02Z',
            areas: Object.fromEntries(requested.filter((topic) => topic in areas).map((topic) => [topic, areas[topic]])), logs });
          return;
        }
        if (path === '/v2/node') {
          json(res, status);
          return;
        }
        if (path === '/v2/telemetry') {
          json(res, usage);
          return;
        }
        if (path === '/v2/telemetry/lifetimes') {
          counts.lifetimes++;
          const nodeId = new URL(req.url, 'http://x').searchParams.get('node_id');
          json(res, { ...recorder, node_id: nodeId, host: nodes[nodeId]?.host ?? nodeId, connected: true,
            lifetimes: [{ lifetime_id: `${nodeId}:99:2026-09-29T00:00:00Z`, pid: 99, role: 'worker',
              started_at: '2026-09-29T00:00:00Z', ended_at: null, state: 'running', model: 'alpha.gguf' }],
            retained_node_ids: [nodeId] });
          return;
        }
        if (path === '/v2/telemetry/series') {
          counts.series++;
          json(res, { ...recorder, window_start: '2026-09-29T00:00:00Z', window_end: '2026-09-29T00:00:02Z',
            time_zone: 'UTC', buckets: [{ start: '2026-09-29T00:00:00Z', end: '2026-09-29T00:00:02Z',
              count: 1, process_cpu_min: 4, process_cpu_max: 4, process_rss_min: 2 * GIB, process_rss_max: 2 * GIB,
              host_cpu_min: 10, host_cpu_max: 10, host_used_bytes_min: 2 * GIB, host_used_bytes_max: 2 * GIB,
              devices: [] }] });
          return;
        }
        if (path === '/v2/logs') {
          if (req.method !== 'GET') logsWrites++;
          if (logsFailure) { json(res, { detail: 'logs unavailable' }, 503); return; }
          if (logsEmpty) {
            json(res, { lines: [], source: 'cluster', cursor: '00000000-0000-4000-8000-000000000001:0',
              reset: null, gap: false, gap_reason: null });
            return;
          }
          const after = new URL(req.url, 'http://x').searchParams.get('after');
          if (logsBulk) {
            const epoch = '00000000-0000-4000-8000-000000000001';
            const seq = after ? Number(after.split(':').at(-1)) : 0;
            const lines = Array.from({ length: 200 }, (_, index) => {
              const number = seq + index + 1;
              return { event_id: `${epoch.slice(0, -12)}${String(number).padStart(12, '0')}`,
                ts: number, node_id: 'master', source: 'agent', line: `bulk ${number}` };
            });
            json(res, { lines, source: 'cluster', cursor: `${epoch}:${seq + lines.length}`,
              reset: null, gap: false, gap_reason: null });
            return;
          }
          const lines = after
            ? [{ event_id: '00000000-0000-4000-8000-000000000003', ts: 3, node_id: 'w1', source: 'rpc', line: '<b>three</b>' }]
            : [
                { event_id: '00000000-0000-4000-8000-000000000001', ts: 1, node_id: 'master', source: 'agent', line: 'boot ok' },
                { event_id: '00000000-0000-4000-8000-000000000002', ts: 2, node_id: 'w1', source: 'rpc', line: 'worker ready' }
              ];
          json(res, { lines, source: logsWarning ? 'local' : 'cluster',
            ...(logsWarning ? { warning: logsWarning } : {}), cursor: after
            ? '00000000-0000-4000-8000-000000000001:3'
            : '00000000-0000-4000-8000-000000000001:2',
            reset: null, gap: false, gap_reason: null });
          return;
        }
        if (path === '/v2/telemetry/readings') {
          counts.readings++;
          json(res, { ...recorder, window_start: '2026-09-29T00:00:00Z', window_end: '2026-09-29T00:00:02Z',
            time_zone: 'UTC', next_cursor: null, readings: [{ lifetime_id: 'l1', pid: 99,
              process_role: 'worker', process_started_at: '2026-09-29T00:00:00Z', observed_at: '2026-09-29T00:00:02Z',
              process: { cpu_percent_machine: 4, rss_bytes: 2 * GIB },
              host: { cpu_percent: 10, used_bytes: 2 * GIB, total_bytes: 16 * GIB }, devices: [],
              availability: { process_cpu: 'measured', process_rss: 'measured', host_cpu: 'measured', host_memory: 'measured' } }] });
          return;
        }
        json(res, {});
      });
      await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
      const base = `http://127.0.0.1:${server.address().port}`;

      browser = spawn(chrome, ['--headless=new', '--disable-gpu', '--no-first-run', '--remote-allow-origins=*',
        '--remote-debugging-port=0', `--user-data-dir=${profile}`, 'about:blank'], { stdio: 'ignore' });
      const portfile = join(profile, 'DevToolsActivePort');
      for (let i = 0; i < 100 && !existsSync(portfile); i++) await new Promise((resolve) => setTimeout(resolve, 100));
      assert.ok(existsSync(portfile), 'Chrome DevTools did not start');
      const port = Number(readFileSync(portfile, 'utf8').split('\n')[0]);
      const pages = await (await fetch(`http://127.0.0.1:${port}/json/list`)).json();
      ws = new WebSocket(pages.find((page) => page.type === 'page').webSocketDebuggerUrl);
      await new Promise((resolve, reject) => { ws.onopen = resolve; ws.onerror = reject; });
      let id = 0;
      const awaiting = new Map();
      ws.onmessage = ({ data }) => {
        const message = JSON.parse(data);
        if (awaiting.has(message.id)) { awaiting.get(message.id)(message); awaiting.delete(message.id); }
      };
      async function call(method, params = {}) {
        const requestId = ++id;
        const answer = new Promise((resolve, reject) => {
          const timer = setTimeout(() => reject(new Error(`CDP timeout: ${method}`)), 10000);
          awaiting.set(requestId, (message) => { clearTimeout(timer); resolve(message); });
        });
        ws.send(JSON.stringify({ id: requestId, method, params }));
        const response = await answer;
        assert.equal(response.error, undefined, JSON.stringify(response.error));
        return response.result;
      }
      async function evaluate(expression) {
        const response = await call('Runtime.evaluate', { expression, returnByValue: true });
        assert.equal(response.exceptionDetails, undefined, JSON.stringify(response.exceptionDetails));
        return response.result.value;
      }
      async function wait(expression) {
        for (let i = 0; i < 300; i++) {
          if (await evaluate(`Boolean(${expression})`)) return;
          await new Promise((resolve) => setTimeout(resolve, 100));
        }
        const diagnostic = await evaluate(`(() => ({
          history: document.querySelector('[aria-label^="Recorded usage"]')?.innerText ?? null,
          details: document.querySelector('.usage-dialog')?.innerText ?? null
        }))()`);
        assert.fail(`Browser timed out: ${expression}; UI: ${JSON.stringify(diagnostic)}`);
      }
      async function press(name, code, vk, text) {
        await call('Input.dispatchKeyEvent', { type: 'rawKeyDown', key: name, code, windowsVirtualKeyCode: vk });
        if (text) await call('Input.dispatchKeyEvent', { type: 'char', key: name, code, text, windowsVirtualKeyCode: vk });
        await call('Input.dispatchKeyEvent', { type: 'keyUp', key: name, code, windowsVirtualKeyCode: vk });
      }
      const enter = () => press('Enter', 'Enter', 13, '\r');
      const escape = () => press('Escape', 'Escape', 27);
      async function transform() { return evaluate("document.querySelector('.node-map-graph > div').style.transform"); }
      function scaleOf(value) { const m = /scale\(([\d.]+)\)/.exec(value); return m ? Number(m[1]) : null; }
      async function cardBounds() {
        return evaluate(`(() => { const g = document.querySelector('.node-map-graph').getBoundingClientRect();
          const cards = [...document.querySelectorAll('.node-map-card')].map((c) => c.getBoundingClientRect());
          if (!cards.length) return null;
          return { graph: { l: g.left, r: g.right, t: g.top, b: g.bottom },
            cards: { l: Math.min(...cards.map((c) => c.left)), r: Math.max(...cards.map((c) => c.right)),
              t: Math.min(...cards.map((c) => c.top)), b: Math.max(...cards.map((c) => c.bottom)) } }; })()`);
      }
      async function countsSnapshot() { return (await fetch(`${base}/__counts`)).json(); }
      async function waitForLiveRead(after) {
        const deadline = Date.now() + 10000;
        while (Date.now() < deadline) {
          if ((await countsSnapshot()).live >= after) return;
          await new Promise((resolve) => setTimeout(resolve, 25));
        }
        assert.fail(`No completed live refresh reached count ${after}`);
      }
      async function shellMetrics() {
        return evaluate(`(() => { const header = document.querySelector('.dashboard-header').getBoundingClientRect();
          const workspace = document.querySelector('.usage-workspace').getBoundingClientRect();
          const graph = document.querySelector('.node-map-graph').getBoundingClientRect();
          const appShell = document.querySelector('.app-shell').getBoundingClientRect();
          const appMain = document.querySelector('.app-main').getBoundingClientRect();
          return { headerBottom: header.bottom, wsTop: workspace.top, wsBottom: workspace.bottom,
            graphWidth: graph.width, graphHeight: graph.height,
            shellHeight: appShell.height, mainHeight: appMain.height, mainBottom: appMain.bottom,
            docClientHeight: document.documentElement.clientHeight,
            innerWidth, innerHeight, scrollWidth: document.documentElement.scrollWidth }; })()`);
      }
      function assertFullArea(metrics, label) {
        assert.ok(Math.abs(metrics.wsTop - metrics.headerBottom) <= 2,
          `${label}: workspace must start at the header bottom: ${JSON.stringify(metrics)}`);
        assert.ok(Math.abs(metrics.wsBottom - metrics.innerHeight) <= 2,
          `${label}: workspace must reach the viewport bottom: ${JSON.stringify(metrics)}`);
        assert.ok(metrics.graphWidth > metrics.innerWidth * 0.85,
          `${label}: graph must span the full width (no permanent sidebars): ${JSON.stringify(metrics)}`);
        assert.ok(metrics.graphHeight > 460,
          `${label}: graph must expand past the old 420px cap: ${JSON.stringify(metrics)}`);
        assert.ok(metrics.scrollWidth <= metrics.innerWidth, `${label}: no page-wide horizontal overflow`);
      }
      function assertContained(bounds, label) {
        assert.ok(bounds, `${label}: no cards`);
        assert.ok(bounds.cards.l >= bounds.graph.l - 2 && bounds.cards.r <= bounds.graph.r + 2
          && bounds.cards.t >= bounds.graph.t - 2 && bounds.cards.b <= bounds.graph.b + 2,
        `${label}: all cards must fit the graph: ${JSON.stringify(bounds)}`);
      }
      async function navigate(nextMode, expected) {
        mode = nextMode;
        await call('Page.navigate', { url: base + '/' });
        await wait(`document.querySelectorAll('.node-map-card').length === ${expected}`);
      }

      await call('Page.enable');
      await call('Emulation.setDeviceMetricsOverride', { width: 1280, height: 720, deviceScaleFactor: 1, mobile: false });

      // Sidebar, measured topology, and deferred history.
      await call('Page.navigate', { url: base + '/' });
      await wait("document.querySelectorAll('.cluster-diagram .topology-node').length === 16");
      const navText = await evaluate("document.querySelector('aside.sidebar').textContent");
      assert.match(navText, /Cluster/);
      assert.match(navText, /Models/);
      assert.match(navText, /Deploy/);
      assert.match(navText, /Activity/);
      assert.match(navText, /Connect/);
      assert.match(navText, /Settings/);
      assert.equal(await evaluate("getComputedStyle(document.querySelector('.cluster-diagram')).display"), 'block');
      assert.equal(await evaluate("getComputedStyle(document.querySelector('.cluster-list')).display"), 'none');
      const cardText = await evaluate("document.querySelector('.cluster-diagram .topology-node').textContent");
      assert.match(cardText, /GiB/);
      assert.match(cardText, /CPU/);
      await wait("document.querySelector('[data-link-state]') !== null");
      assert.match(await evaluate("document.querySelector('[data-link-state]')?.textContent || ''"), /Unmeasured/);
      assert.doesNotMatch(await evaluate("document.querySelector('.cluster-diagram').textContent"), /GbE|packet loss|\d+\s*ms/);
      assert.match(await evaluate("document.querySelector('.cluster-diagram').textContent"), /Blackwell|GPU/);
      assert.match(await evaluate("document.body.textContent"), /RPC port 50052 reachable/);
      assert.doesNotMatch(await evaluate("document.body.textContent"), /up 10\.66\.87\.1 \(listen 51820\)/);

      const beforeSelect = await countsSnapshot();
      assert.deepEqual({ lifetimes: beforeSelect.lifetimes, series: beforeSelect.series, readings: beforeSelect.readings }, { lifetimes: 0, series: 0, readings: 0 },
        'initial polling must not fetch history');
      await evaluate("document.querySelector('.cluster-diagram .topology-node[data-node-id=\"w1\"]').focus()");
      await enter();
      const afterSelect = await countsSnapshot();
      assert.deepEqual({ lifetimes: afterSelect.lifetimes, series: afterSelect.series, readings: afterSelect.readings }, { lifetimes: 0, series: 0, readings: 0 },
        'selecting a node with History closed must not fetch history');
      assert.equal(await evaluate("document.querySelector('.cluster-diagram .topology-node[aria-pressed=\"true\"]')?.dataset.nodeId"), 'w1');

      await evaluate("[...document.querySelectorAll('button')].find((button) => button.textContent.trim() === 'History').focus()");
      await enter();
      await wait("document.querySelector('.usage-dialog')?.open === true");
      await wait("document.querySelector('[aria-label^=\"Recorded usage\"]')");
      const afterHistory = await countsSnapshot();
      assert.ok(afterHistory.lifetimes >= 1 && afterHistory.series >= 1 && afterHistory.readings >= 1,
        `opening History must fetch the endpoints: ${JSON.stringify(afterHistory)}`);
      await escape();
      await wait("document.querySelector('.usage-dialog')?.open === false");

      await call('Emulation.setDeviceMetricsOverride', { width: 390, height: 844, deviceScaleFactor: 1, mobile: false });
      await wait("getComputedStyle(document.querySelector('.cluster-diagram')).display === 'none'");
      assert.equal(await evaluate("getComputedStyle(document.querySelector('.cluster-list')).display"), 'grid');
      assert.match(await evaluate("document.querySelector('.cluster-list').textContent"), /GiB/);
      assert.equal(await evaluate('document.querySelectorAll(".cluster-list .topology-node").length'), 16);
      assert.equal(await evaluate('document.documentElement.scrollWidth <= innerWidth'), true,
        'narrow layout must not overflow horizontally');

      await call('Emulation.setDeviceMetricsOverride', { width: 1280, height: 720, deviceScaleFactor: 1, mobile: false });
      await wait('innerWidth === 1280');
      await call('Page.navigate', { url: base + '/models' });
      await wait("document.querySelector('.app-main')");
      assert.equal(await evaluate("getComputedStyle(document.querySelector('.app-main')).overflowY"), 'auto',
        'other routes scroll inside the main region');

      mode = 'four';
      await call('Page.navigate', { url: base + '/' });
      await wait("document.querySelectorAll('.cluster-diagram .topology-node').length === 4");
      assert.match(await evaluate("document.querySelector('.cluster-diagram').textContent"), /GiB/);

      // --- Logs: master+worker trail, refresh modes, display-only Clear ---------------------
      mode = 'four';
      logsWrites = 0;
      await call('Page.navigate', { url: base + '/logs' });
      await wait("document.querySelectorAll('.logs-row').length === 2");
      assert.match(await evaluate("document.querySelector('aside.sidebar').textContent"), /Activity/);
      const logsText = await evaluate("document.querySelector('.logs-scroll').textContent");
      assert.match(logsText, /boot ok/);
      assert.match(logsText, /worker ready/);
      assert.equal(await evaluate("document.querySelector('.logs-row .logs-msg').textContent"), 'worker ready',
        'the newest log is the first row');
      assert.equal(await evaluate("document.querySelectorAll('.logs-row .logs-msg')[1].textContent"), 'boot ok',
        'the older log follows the newest row');
      assert.match(logsText, /rpc/);
      assert.match(logsText, /master/);
      assert.match(await evaluate("document.querySelector('.logs-ts').textContent"), /Z$/);
      assert.deepEqual(await evaluate("[...document.querySelectorAll('.logs-tag')].map((el) => el.textContent)"),
        ['Inference', 'UI']);
      assert.equal(await evaluate('document.documentElement.scrollWidth <= innerWidth'), true,
        'desktop logs must not overflow horizontally');
      await evaluate("document.querySelector('select[aria-label=\"Filter by tag\"]').focus()");
      assert.equal(await evaluate("document.activeElement === document.querySelector('select[aria-label=\"Filter by tag\"]')"), true);
      await evaluate("(() => { const s = document.querySelector('select[aria-label=\"Filter by tag\"]'); s.value = 'inference'; s.dispatchEvent(new Event('change')); })()");
      await wait("document.querySelectorAll('.logs-row').length === 1");
      assert.match(await evaluate("document.querySelector('.logs-row').textContent"), /worker ready/);
      await evaluate("(() => { const s = document.querySelector('select[aria-label=\"Filter by tag\"]'); s.value = ''; s.dispatchEvent(new Event('change')); })()");
      await wait("document.querySelectorAll('.logs-row').length === 2");
      await evaluate("(() => { const s = document.querySelector('select[aria-label=\"Filter by tag\"]'); s.value = 'unclassified'; s.dispatchEvent(new Event('change')); })()");
      await wait("document.body.textContent.includes('No entries for the selected tag')");
      assert.equal(await evaluate("document.body.textContent.includes('history gap')"), false);
      assert.equal(await evaluate("document.body.textContent.includes('trimmed')"), false);
      await evaluate("(() => { const s = document.querySelector('select[aria-label=\"Filter by tag\"]'); s.value = ''; s.dispatchEvent(new Event('change')); })()");
      await wait("document.querySelectorAll('.logs-row').length === 2");
      assert.equal(await evaluate("document.querySelectorAll('input[name=refresh]').length"), 4);
      assert.equal(await evaluate("document.querySelector('input[name=refresh]:checked').value"), 'live');

      // An independent same-origin document owns an independent log view.
      await evaluate("(() => { const frame = document.createElement('iframe'); frame.src = '/logs'; frame.title = 'Second log viewer'; frame.setAttribute('aria-hidden', 'true'); frame.style.cssText = 'position:fixed;width:1px;height:1px;opacity:0'; document.body.append(frame); })()");
      await wait("document.querySelector('iframe')?.contentDocument?.querySelectorAll('.logs-row').length === 2");
      await evaluate("document.querySelector('iframe').contentDocument.querySelector('input[name=refresh][value=\"1m\"]').click()");
      await wait("document.querySelector('iframe').contentDocument.querySelector('input[name=refresh]:checked').value === '1m'");

      // Keyboard-operable refresh control: selecting 30s switches off Live.
      await evaluate("document.querySelector('input[name=refresh][value=\"30s\"]').focus()");
      await evaluate("document.querySelector('input[name=refresh][value=\"30s\"]').click()");
      await wait("document.querySelector('input[name=refresh]:checked').value === '30s'");
      assert.match(await evaluate("document.querySelector('[role=status]').closest('div').textContent"), /30s/);

      // Node filter is local and distinguishable.
      await evaluate("(() => { const s = document.querySelector('select'); s.value = 'w1'; s.dispatchEvent(new Event('change')); })()");
      await wait("document.querySelectorAll('.logs-row').length === 1");
      assert.match(await evaluate("document.querySelector('.logs-row').textContent"), /w1/);
      await evaluate("(() => { const s = document.querySelector('select'); s.value = ''; s.dispatchEvent(new Event('change')); })()");
      await wait("document.querySelectorAll('.logs-row').length === 2");

      // Display-only Clear: back to Live so the next read is prompt.
      await evaluate("document.querySelector('input[name=refresh][value=\"live\"]').click()");
      await wait("document.querySelector('input[name=refresh]:checked').value === 'live'");
      await evaluate("(() => { const s = document.querySelector('select[aria-label=\"Filter by tag\"]'); s.value = 'ui'; s.dispatchEvent(new Event('change')); })()");
      await wait("document.querySelectorAll('.logs-row').length === 1");
      assert.match(await evaluate("document.querySelector('.logs-row').textContent"), /boot ok/);
      await evaluate("[...document.querySelectorAll('button.btn')].find((b) => b.textContent.trim() === 'Clear').focus()");
      await enter();
      await wait("document.querySelectorAll('.logs-row').length === 0");
      assert.equal(logsWrites, 0, 'Clear must not issue a write request');
      assert.equal(await evaluate("document.querySelector('iframe').contentDocument.querySelectorAll('.logs-row').length"), 2,
        'display-only Clear must not affect another browsing context');
      assert.equal(await evaluate("document.querySelector('input[name=refresh]:checked').value"), 'live');
      await wait("document.querySelector('.logs-page [role=status]')?.textContent?.toLowerCase().includes('cleared')");
      assert.match(await evaluate("document.querySelector('.logs-page [role=status]')?.textContent || ''"), /cleared/i);
      await evaluate("(() => { const s = document.querySelector('select[aria-label=\"Filter by tag\"]'); s.value = ''; s.dispatchEvent(new Event('change')); })()");
      assert.equal(await evaluate("document.body.textContent.includes('boot ok')"), false,
        'Clear removes rows that were hidden by the tag filter');

      // New entries resume after Clear; the message renders as inert text.
      allowNewLog = true;
      await wait("document.querySelectorAll('.logs-row').length === 1");
      assert.equal(await evaluate("document.querySelector('.logs-row .logs-msg')?.textContent"), '<b>three</b>');
      assert.equal(await evaluate("document.querySelector('.logs-row .logs-msg')?.innerHTML"), '&lt;b&gt;three&lt;/b&gt;');

      // Failures keep the rows but must not continue to claim a healthy stream.
      logsFailure = true;
      await wait("document.querySelector('[data-error-source=logs]')?.textContent?.includes('Logs are unavailable')");
      assert.equal(await evaluate("document.querySelectorAll('.logs-row').length"), 1);
      logsFailure = false;
      logsWarning = 'master unreachable; showing local logs';
      await wait("document.querySelector('[role=status]')?.textContent?.includes('Local only')");
      logsWarning = '';
      await wait("document.querySelector('[role=status]')?.textContent?.includes('Streaming cluster logs')");
      assert.equal(await evaluate("document.body.textContent.includes('master unreachable; showing local logs')"), false,
        'successful recovery must clear the old local-only warning');

      // Narrow layout keeps controls reachable without page-wide overflow.
      await call('Emulation.setDeviceMetricsOverride', { width: 390, height: 844, deviceScaleFactor: 1, mobile: false });
      await wait('innerWidth === 390');
      assert.equal(await evaluate('document.documentElement.scrollWidth <= innerWidth'), true);
      assert.ok(await evaluate("document.querySelector('select') && document.querySelector('select[aria-label=\"Filter by tag\"]') && document.querySelector('input[name=refresh]:checked') && [...document.querySelectorAll('button.btn')].some((b) => b.textContent.trim() === 'Clear')"));
      await call('Emulation.setDeviceMetricsOverride', { width: 1280, height: 720, deviceScaleFactor: 1, mobile: false });
      await wait('innerWidth === 1280');

      // A retained-row trim relocates an anchor that is no longer in the buffer.
      logsBulk = true;
      await call('Page.navigate', { url: base + '/logs' });
      await wait("document.querySelectorAll('.logs-row').length === 200");
      await wait("document.querySelectorAll('.logs-row').length === 2000");
      const anchor = await evaluate(`(() => { const pane = document.querySelector('.logs-scroll');
        const oldest = pane.querySelector('.logs-row:last-child');
        pane.scrollTop += oldest.getBoundingClientRect().top - pane.getBoundingClientRect().top;
        pane.dispatchEvent(new Event('scroll'));
        return [...pane.querySelectorAll('.logs-row')].find((r) => r.getBoundingClientRect().bottom > pane.getBoundingClientRect().top + 1)?.dataset.eventId; })()`);
      assert.ok(anchor, 'reading position must identify an anchored row before trimming');
      await wait("document.querySelector('.logs-scroll .logs-row:last-child')?.dataset.eventId === '00000000-0000-4000-8000-000000000201'");
      assert.equal(await evaluate(`document.querySelector('.logs-row[data-event-id="${anchor}"]') === null`), true,
        'the original visible anchor must actually be trimmed in this scenario');
      const anchorAfterTrim = await evaluate(`(() => { const pane = document.querySelector('.logs-scroll');
        const oldest = pane.querySelector('.logs-row:last-child'); const box = pane.getBoundingClientRect();
        return { id: oldest?.dataset.eventId, rowTop: oldest?.getBoundingClientRect().top, paneTop: box.top,
          text: document.body.textContent }; })()`);
      assert.equal(anchorAfterTrim.id, '00000000-0000-4000-8000-000000000201');
      assert.ok(Math.abs(anchorAfterTrim.rowTop - anchorAfterTrim.paneTop) < 3,
        'when the anchor is trimmed, oldest retained row must become the visible anchor');
      assert.match(anchorAfterTrim.text, /trimmed 200/);
      logsBulk = false;

      // An empty visit stays the empty visit when a tag, or a tag and a node, is selected.
      logsEmpty = true;
      await call('Page.navigate', { url: base + '/logs' });
      await wait("document.querySelector('.logs-scroll')?.textContent?.includes('No log entries yet.')");
      await evaluate("(() => { const s = document.querySelector('select[aria-label=\"Filter by tag\"]'); s.value = 'inference'; s.dispatchEvent(new Event('change')); })()");
      await wait("document.querySelector('.logs-scroll')?.textContent?.includes('No log entries yet.')");
      assert.equal(await evaluate("document.querySelector('.logs-scroll').textContent.includes('No entries for the selected tag')"), false,
        'an empty visit must not use the tag filter-empty copy');
      await evaluate("(() => { const node = document.querySelector('select'); node.value = 'w1'; node.dispatchEvent(new Event('change')); })()");
      assert.equal(await evaluate("document.querySelector('.logs-scroll').textContent.includes('No log entries yet.')"), true);
      assert.equal(await evaluate("document.querySelector('.logs-scroll').textContent.includes('No entries match the selected node and tag')"), false,
        'an empty visit must not use the combined filter-empty copy');
      logsEmpty = false;

      // --- US1: empty and offline workspace states ------------------------------------------
      mode = 'empty';
      await call('Page.navigate', { url: base + '/' });
      await wait("document.body.textContent.includes('No members — Start as master')");
      mode = 'offline';
      await call('Page.navigate', { url: base + '/' });
      await wait("document.body.textContent.includes('Disconnected — keeping last cluster view')");
      mode = 'usage_denied';
      await call('Page.navigate', { url: base + '/' });
      await wait("document.body.textContent.includes('usage denied')");
    } finally {
      ws?.close();
      if (browser) {
        await new Promise((resolve, reject) => {
          if (browser.exitCode !== null) { resolve(); return; }
          const timer = setTimeout(() => reject(new Error('Chrome did not exit')), 2000);
          browser.once('exit', () => { clearTimeout(timer); resolve(); });
          browser.kill();
        });
      }
      if (server) await new Promise((resolve) => server.close(resolve));
      try { await vite?.watcher?.close(); } catch { /* no watcher */ }
      await vite?.close();
      await removeBrowserProfile(profile);
      process.chdir(cwd);
    }
  });
