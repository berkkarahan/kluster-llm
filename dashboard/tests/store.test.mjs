import assert from 'node:assert/strict';
import test from 'node:test';
import { fileURLToPath } from 'node:url';
import { createServer } from 'vite';

test('dashboard always requests Layer and normalizes legacy hardware saves', async () => {
  const cwd = process.cwd();
  const originalFetch = globalThis.fetch;
  const requests = [];
  let savedMode = 'layer';
  let gpuInventory = { gpus: [], backend: 'cpu' };
  let plannedBudget = 32 * 1024 ** 3;
  let lastPlan = null;
  let savedGpuIds = null;
  let statusMembers = { master: { node_id: 'master' } };
  const mockFetch = async (path, options = {}) => {
    const body = options.body ? JSON.parse(options.body) : null;
    requests.push({ path, body });
    if (path === '/v1/config' && options.method === 'PUT') {
      savedMode = body.split_mode;
      if ('gpu_ids' in body) savedGpuIds = body.gpu_ids;
      if (body.gpu_ids?.length === 1) plannedBudget = 15.2 * 1024 ** 3;
    }
    const data = path === '/v1/usage/live'
      ? { nodes: { master: { node_id: 'master', host: 'example', role: 'master', connected: true,
          freshness: 'unavailable', reading: null } }, relationships: [],
          recording: { recording: 'interrupted', last_successful_save: null } }
      : path === '/v1/config'
      ? { device: 'gpu', vram_percent: 95, ram_percent: 80, split_mode: savedMode }
      : path === '/v1/models'
        ? { data: [{ id: 'fixture.gguf' }] }
      : path === '/v1/plan' || path === '/v1/plan?preview=true'
        ? { split_mode: body.split_mode ?? savedMode, fit_status: 'fits',
          estimated_bytes: 1024, budget_bytes: plannedBudget }
        : path === '/v1/run'
          ? { plan: { split_mode: body.split_mode ?? savedMode, fit_status: 'fits' } }
          : path === '/v1/status'
            ? { role: 'master', node_id: 'master', members: statusMembers, last_plan: lastPlan }
            : path === '/v1/gpus' ? gpuInventory : {};
    return new Response(JSON.stringify(data), { status: 200 });
  };
  let server;
  let appStore;
  try {
    process.chdir(fileURLToPath(new URL('..', import.meta.url)));
    server = await createServer({
      // Prefer no watcher; SvelteKit may still attach one — closed explicitly below.
      server: { middlewareMode: true, hmr: false, watch: null },
      appType: 'custom'
    });
    globalThis.fetch = mockFetch;
    ({ appStore } = await server.ssrLoadModule('/src/lib/store.svelte.ts'));
    appStore.models = [{ id: 'fixture.gguf' }];
    appStore.selectedModel = 'fixture.gguf';
    appStore.status = { role: 'master', node_id: 'master', members: { master: { node_id: 'master' } } };
    assert.equal(appStore.planRunBlockedReason(), null);

    for (const legacy of ['row', 'tensor']) {
      savedMode = legacy;
      await appStore.loadConfig();
      assert.equal(appStore.planPayload().split_mode, 'layer');
      await appStore.refreshEstimate();
      await appStore.plan();
      assert.equal(appStore.planResult?.fit_status, 'fits', JSON.stringify(requests));
      assert.equal(appStore.planRunBlockedReason(), null);
      assert.equal(appStore.validateRunSettings(), true, JSON.stringify(appStore.fieldErrors));
      await appStore.run();
      assert.ok(requests.some(({ path }) => path === '/v1/plan'));
      assert.ok(requests.some(({ path }) => path === '/v1/run'));
      for (const { path, body } of requests.filter(({ path }) => path === '/v1/plan' || path === '/v1/run')) {
        assert.equal(body.split_mode, 'layer', path);
      }
      assert.equal(appStore.planResult.split_mode, 'layer');
      await appStore.saveConfig();
      assert.equal(savedMode, 'layer');
      appStore.resetRunSettings();
      await appStore.loadConfig();
      assert.equal(appStore.planPayload().split_mode, 'layer');
      requests.length = 0;
    }

    appStore.gpus = [
      { index: 0, name: 'first', vram_bytes: 16 * 1024 ** 3 },
      { index: 1, name: 'second', vram_bytes: 8 * 1024 ** 3 }
    ];
    appStore.selectedGpuIds = [0, 1];
    appStore.planResult = {
      estimated_bytes: 8 * 1024 ** 3,
      budget_bytes: 32 * 1024 ** 3,
      vram_alloc_bytes: 6 * 1024 ** 3,
      cpu_alloc_bytes: 2 * 1024 ** 3,
      device_allocations: [{ node_id: 'master', device: 'GPU0', backend: 'cuda',
        layer_start: 0, layer_end: 1, output_layer: true,
        estimated_bytes: 6 * 1024 ** 3, budget_bytes: 15.2 * 1024 ** 3 }]
    };
    const { render } = await server.ssrLoadModule('svelte/server');
    const { default: HardwareSettings } = await server.ssrLoadModule('/src/lib/components/HardwareSettings.svelte');
    const hardware = render(HardwareSettings).body;
    assert.match(hardware, /16\.0 GiB/);
    assert.match(hardware, /8\.0 GiB/);
    assert.match(hardware, /Selected VRAM capacity: 24\.0 GiB/);
    assert.doesNotMatch(hardware, /hw-split-mode|Row \(legacy|Tensor \(experimental/);
    const { default: RunPage } = await server.ssrLoadModule('/src/routes/run/+page.svelte');
    const page = render(RunPage).body;
    assert.match(page, /Estimated total memory: 8\.0 GiB/);
    assert.match(page, /Estimated GPU memory: 6\.0 GiB/);
    assert.match(page, /Estimated master CPU RAM: 2\.0 GiB/);
    assert.match(page, /aria-label="Planning"/);
    assert.match(page, /Advanced settings/);
    assert.doesNotMatch(page, /VRAM weight|Latency weight|Placement weights/);
    assert.match(page, /15\.2 GiB/);
    appStore.gpus = [...appStore.gpus, { index: 2, name: 'unknown', vram_bytes: null }];
    appStore.selectedGpuIds = [0, 2];
    assert.match(render(HardwareSettings).body, /Selected VRAM capacity: —/);
    appStore.status = { ...appStore.status, capacity: { unified_memory: true } };
    const shared = render(HardwareSettings).body;
    assert.match(shared, /shared capacity/);
    assert.doesNotMatch(shared, /Selected VRAM capacity:/);
    appStore.device = 'cpu';
    const cpu = render(RunPage).body;
    assert.match(cpu, /Estimated total memory: 8\.0 GiB/);
    assert.doesNotMatch(cpu, /Estimated GPU memory:/);
    // Inventory can arrive before status: its unified flag must prevent a false discrete total.
    appStore.device = 'gpu';
    appStore.status = null;
    gpuInventory = { gpus: [
      { index: 0, name: 'shared first', vram_bytes: 16 * 1024 ** 3 },
      { index: 1, name: 'shared second', vram_bytes: 16 * 1024 ** 3 }
    ], backend: 'metal', unified_memory: true };
    await appStore.loadGpus();
    const beforeStatus = render(HardwareSettings).body;
    assert.match(beforeStatus, /shared capacity/);
    assert.doesNotMatch(beforeStatus, /Selected VRAM capacity: 32\.0 GiB/);

    // A settings-page selection save must not briefly show the old status plan as current.
    appStore.models = [{ id: 'fixture.gguf' }];
    appStore.selectedModel = 'fixture.gguf';
    appStore.status = { role: 'master', node_id: 'master', members: { master: { node_id: 'master' } } };
    lastPlan = { estimated_bytes: 1024, budget_bytes: 64 * 1024 ** 3 };
    appStore.planResult = lastPlan;
    appStore.selectedGpuIds = [0];
    await appStore.saveConfig({ gpu_ids: [0] });
    assert.equal(appStore.planResult, null);
    await new Promise((resolve) => setTimeout(resolve, 400));
    assert.equal(appStore.planResult.budget_bytes, 15.2 * 1024 ** 3);
    assert.match(render(RunPage).body, /usable total memory budget 15\.2 GiB/);
    appStore.nodeShares = { master: 1 };
    appStore.flashAttention = 'off';
    assert.deepEqual(appStore.planPayload().node_shares, { master: 1 });
    assert.equal(appStore.planPayload().flash_attention, 'off');
    assert.match(render(RunPage).body, /target GPU workload|Target GPU workload/);
    appStore.setNodeShare('master', 0.5);
    assert.equal(appStore.nodeShares.master, 1);
    appStore.status = { ...appStore.status, members: {
      master: { node_id: 'master', role: 'master' }, worker: { node_id: 'worker', role: 'worker' }
    } };
    appStore.nodeShares = null;
    appStore.setNodeShare('worker', 0.4);
    assert.equal(appStore.nodeShares.worker, 0.4);
    assert.equal(appStore.nodeShares.master, 0.6);
    assert.match(render(RunPage).body, /share-worker/);
    appStore.status = { ...appStore.status, members: { master: { node_id: 'master' } } };
    appStore.nodeShares = null;

    gpuInventory = { gpus: [
      { index: 2, name: 'new first', vram_bytes: 16 * 1024 ** 3 },
      { index: 3, name: 'new second', vram_bytes: 8 * 1024 ** 3 }
    ], backend: 'cuda', unified_memory: false };
    await appStore.loadGpus();
    assert.deepEqual([...appStore.selectedGpuIds], [2, 3]);
    assert.deepEqual(savedGpuIds, [2, 3]);
    assert.match(render(HardwareSettings).body, /Selected VRAM capacity: 24\.0 GiB/);
    appStore.selectedGpuIds = [2];
    await appStore.loadGpus();
    assert.deepEqual([...appStore.selectedGpuIds], [2]);
    appStore.selectedGpuIds = [];
    await appStore.loadGpus();
    assert.deepEqual([...appStore.selectedGpuIds], [2, 3]);
    appStore.selectedNodeId = 'master';
    requests.length = 0;
    await appStore.refreshUsage();
    await appStore.refresh();
    assert.equal(appStore.selectedNodeId, 'master');
    assert.deepEqual(requests.map(({ path }) => path), ['/v1/usage/live', '/v1/status']);
    assert.equal(appStore.planPayload().split_mode, 'layer');
    // Regression: heartbeat-only liveness churn must not blank the live plan view.
    appStore.planResult = { fit_status: 'fits', estimated_bytes: 1024, budget_bytes: 4096 };
    requests.length = 0;
    statusMembers.master.last_seen = 11;
    statusMembers.master.latency_ms = 3;
    await appStore.refresh();
    assert.equal(appStore.planResult?.fit_status, 'fits', 'heartbeat cleared the plan');
    assert.equal(requests.filter(({ path }) => path === '/v1/plan?preview=true').length, 0,
      'heartbeat triggered a re-estimate');
    // A real membership change still refreshes it.
    statusMembers.worker = { node_id: 'worker', role: 'worker' };
    await appStore.refresh();
    await new Promise((resolve) => setTimeout(resolve, 400));
    assert.ok(requests.some(({ path }) => path === '/v1/plan?preview=true'),
      'membership change did not re-estimate');
    appStore.liveUsage.nodes.master.reading = { observed_at: '2020-01-01T00:00:00Z' };
    appStore.liveUsage.nodes.master.freshness = 'fresh';
    globalThis.fetch = async (path) => path === '/v1/usage/live'
      ? new Response(JSON.stringify({ detail: { message: 'master unavailable', last_successful_save: '2026-09-28T12:00:00Z' } }), { status: 503 })
      : mockFetch(path);
    await appStore.refreshUsage();
    assert.equal(appStore.liveUsage.nodes.master.freshness, 'stale');
    assert.match(appStore.usageError, /master unavailable.*2026-09-28T12:00:00Z/);
  } finally {
    appStore?.stop();
    globalThis.fetch = originalFetch;
    try {
      try {
        await server?.watcher?.close();
      } catch {
        /* ignore */
      }
      await server?.close();
    } finally {
      process.chdir(cwd);
    }
  }
});
