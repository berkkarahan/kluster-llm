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
    requests.push({ path, method: options.method ?? 'GET', body });
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
    const runAt = page.indexOf('>Run</button>');
    const stopAt = page.indexOf('>Stop</button>');
    assert.ok(runAt !== -1 && stopAt > runAt, 'Stop sits immediately after Run');
    assert.match(page, /Estimated total memory: 8\.0 GiB/);
    assert.match(page, /Estimated GPU memory: 6\.0 GiB/);
    assert.match(page, /Estimated master CPU RAM: 2\.0 GiB/);
    assert.match(page, /aria-label="Planning"/);
    assert.match(page, /Advanced settings/);
    assert.doesNotMatch(page, /VRAM weight|Latency weight|Placement weights/);
    assert.match(page, /15\.2 GiB/);
    const fitted = appStore.planResult;
    appStore.planResult = {
      estimated_bytes: Math.round(54.4 * 1024 ** 3), budget_bytes: Math.round(20.3 * 1024 ** 3),
      vram_alloc_bytes: 0, cpu_alloc_bytes: Math.round(54.4 * 1024 ** 3), fit_status: 'overflow',
      node_ids: ['master'], budgets_bytes: [Math.round(20.3 * 1024 ** 3)],
      node_gpu_budget_bytes: { master: Math.round(15.1 * 1024 ** 3) },
      hint: 'no contiguous device allocation fits available memory'
    };
    appStore.status = { ...appStore.status, members: { master: { node_id: 'master', role: 'master' } } };
    const overflow = render(RunPage).body;
    assert.match(overflow, /GPU budget: 15\.1 GiB · does not fit/);
    assert.match(overflow, /RAM budget: 20\.3 GiB/);
    assert.doesNotMatch(overflow, /Estimated GPU memory/);
    assert.doesNotMatch(overflow, /Estimated master CPU RAM/);
    appStore.planResult = fitted;
    appStore.status = { ...appStore.status, members: { master: { node_id: 'master' } } };
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
    // A newer running download must not hide the prior terminal reconciliation.
    const originalLoadModels = appStore.loadModels.bind(appStore);
    let resolveInventory;
    let inventoryReads = 0;
    appStore.loadModels = () => {
      inventoryReads++;
      return new Promise((resolve) => { resolveInventory = resolve; });
    };
    const previous = { state: 'complete', operation_id: 'download-previous', filename: 'finished.gguf' };
    const current = { state: 'running', operation_id: 'download-current', filename: 'next.gguf' };
    appStore.applyDownloadRecords({ current, last_terminal: previous });
    appStore.applyDownloadRecords({ current, last_terminal: previous });
    assert.equal(appStore.downloadJob.operation_id, current.operation_id);
    assert.equal(inventoryReads, 1, 'a repeated terminal reconciled inventory more than once');
    await appStore.selectModel('manual-choice');
    resolveInventory();
    await new Promise((resolve) => setTimeout(resolve, 0));
    assert.equal(appStore.selectedModel, 'manual-choice', 'late reconciliation overwrote user selection');
    appStore.loadProgress = { operation_id: 'load-tracked', phase: 'loading', model: 'fixture.gguf',
      bytes_loaded: 1, bytes_total: 2, fraction: 0.5 };
    appStore.applyLiveArea('load', { revision: 2, sampled_at: null, availability: 'available', coverage: 'local',
      upstream_last_success_at: null, data: { current: null, last_terminal: null }, error: null });
    assert.equal(appStore.loadProgress.phase, 'unknown', 'a vanished tracked load must not look idle or complete');
    let legacyInventoryReads = 0;
    appStore.loadModels = async () => { legacyInventoryReads++; };
    const legacyRunning = { state: 'running', filename: 'legacy.gguf' };
    const legacyComplete = { state: 'complete', filename: 'legacy.gguf' };
    appStore.applyDownloadRecords({ current: legacyRunning, last_terminal: null });
    appStore.applyDownloadRecords({ current: null, last_terminal: legacyComplete });
    appStore.applyDownloadRecords({ current: null, last_terminal: legacyComplete });
    assert.equal(appStore.downloadJob.state, 'complete');
    assert.equal(legacyInventoryReads, 1, 'legacy terminal transition should reconcile inventory only once');
    assert.equal(appStore.selectedModel, 'manual-choice', 'unidentified legacy completion must not auto-select');
    appStore.loadModels = originalLoadModels;
    appStore.liveUsage.nodes.master.reading = { observed_at: '2020-01-01T00:00:00Z' };
    appStore.liveUsage.nodes.master.freshness = 'fresh';
    globalThis.fetch = async (path) => path === '/v1/usage/live'
      ? new Response(JSON.stringify({ detail: { message: 'master unavailable', last_successful_save: '2026-09-28T12:00:00Z' } }), { status: 503 })
      : mockFetch(path);
    await appStore.refreshUsage();
    assert.equal(appStore.liveUsage.nodes.master.freshness, 'stale');
    assert.match(appStore.usageError, /master unavailable.*2026-09-28T12:00:00Z/);
    appStore.applyLiveArea('status', { revision: 2, sampled_at: '2026-09-29T12:00:00Z',
      availability: 'available', coverage: 'cluster', upstream_last_success_at: null,
      data: { role: 'master', node_id: 'master', join_key: 'join-from-live-status',
        apply_error: 'model does not fit', members: statusMembers }, error: null });
    assert.equal(appStore.joinKeyInput, 'join-from-live-status');
    assert.equal(appStore.status.join_key, 'join-from-live-status');
    assert.equal(appStore.status.apply_error, 'model does not fit');
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

test('model search keeps one in-flight request and forwards the selected source', async () => {
  const cwd = process.cwd();
  const originalFetch = globalThis.fetch;
  const requests = [];
  let releaseSearch = () => {};
  let searchGate = new Promise((resolve) => { releaseSearch = resolve; });
  let mode = 'held';
  const mockFetch = async (path, options = {}) => {
    const body = options.body ? JSON.parse(options.body) : null;
    requests.push({ path, method: options.method ?? 'GET', body });
    if (path === '/v1/models/search') {
      if (mode === 'held') await searchGate;
      if (mode === 'error') {
        return new Response(JSON.stringify({ detail: 'Supply a supported model repository, tree, or file link.' }), { status: 400 });
      }
      if (mode === 'legacy') {
        return new Response(JSON.stringify({
          query: body.query,
          total_vram_bytes: 1024,
          results: [{ repo_id: 'org/old', filename: 'old.gguf', quant: null, size_bytes: 10,
            gated: false, source: 'org/old:old.gguf', fit: 'unknown' }]
        }), { status: 200 });
      }
      if (mode === 'partial') {
        return new Response(JSON.stringify({
          query: body.query,
          total_vram_bytes: null,
          results: [{ repo_id: 'org/good', filename: 'good.gguf', quant: 'Q4_K_M', size_bytes: 10,
            gated: false, source: 'org/good:good.gguf', fit: 'fits' }],
          warnings: ['org/bad could not be searched']
        }), { status: 200 });
      }
      return new Response(JSON.stringify({
        query: body.query,
        total_vram_bytes: 8,
        results: [{
          repo_id: 'demo/model-GGUF', filename: 'model.Q4_K_M.gguf',
          path: 'gguf/model.Q4_K_M.gguf', revision: 'release/v2', quant: 'Q4_K_M',
          size_bytes: 0, gated: false, fit: 'unknown',
          source: 'https://huggingface.co/demo/model-GGUF/resolve/release%2Fv2/gguf/model.Q4_K_M.gguf'
        }],
        warnings: []
      }), { status: 200 });
    }
    if (path === '/v1/models/download') {
      return new Response(JSON.stringify({ ok: true, accepted: true, source: body.source }), { status: 200 });
    }
    return new Response('{}', { status: 200 });
  };
  let server;
  let appStore;
  try {
    process.chdir(fileURLToPath(new URL('..', import.meta.url)));
    server = await createServer({
      server: { middlewareMode: true, hmr: false, watch: null },
      appType: 'custom'
    });
    globalThis.fetch = mockFetch;
    ({ appStore } = await server.ssrLoadModule('/src/lib/store.svelte.ts'));
    appStore.modelSource = '  https://huggingface.co/demo/model-GGUF/tree/main  ';
    const first = appStore.searchModels();
    await new Promise((resolve) => setTimeout(resolve, 30));
    assert.equal(appStore.searching, true);
    assert.deepEqual(appStore.searchResults, []);
    assert.deepEqual(appStore.searchWarnings, []);
    assert.equal(appStore.searchError, '');
    appStore.modelSource = 'unsubmitted edit';
    assert.equal(appStore.searchQuery, 'https://huggingface.co/demo/model-GGUF/tree/main');
    assert.equal(await appStore.searchModels(), null);
    assert.equal(requests.filter(({ path }) => path === '/v1/models/search').length, 1);
    assert.equal(requests[0].body.query, 'https://huggingface.co/demo/model-GGUF/tree/main');
    releaseSearch();
    const accepted = await first;
    assert.equal(accepted.results[0].revision, 'release/v2');
    assert.equal(appStore.searchResults[0].path, 'gguf/model.Q4_K_M.gguf');
    assert.equal(appStore.searching, false);
    assert.equal(appStore.searchQuery, 'https://huggingface.co/demo/model-GGUF/tree/main');
    appStore.modelSource = 'edited after results';
    assert.equal(appStore.searchQuery, 'https://huggingface.co/demo/model-GGUF/tree/main');
    assert.equal(requests.filter(({ path }) => path === '/v1/models/download').length, 0);

    mode = 'legacy';
    appStore.modelSource = 'legacy keyword';
    await appStore.searchModels();
    assert.equal(appStore.searchResults[0].filename, 'old.gguf');
    assert.deepEqual(appStore.searchWarnings, []);
    assert.equal(appStore.searchError, '');
    assert.equal(appStore.searchQuery, 'legacy keyword');

    mode = 'error';
    appStore.modelSource = 'https://huggingface.co/datasets/demo/set';
    await assert.rejects(() => appStore.searchModels());
    assert.equal(appStore.searchResults.length, 0);
    assert.equal(appStore.searchTotalVram, null);
    assert.deepEqual(appStore.searchWarnings, []);
    assert.match(appStore.searchError, /supported model repository/);
    assert.equal(appStore.searchQuery, 'https://huggingface.co/datasets/demo/set');
    appStore.modelSource = 'edited during error';
    assert.equal(appStore.searchQuery, 'https://huggingface.co/datasets/demo/set');
    assert.equal(requests.filter(({ path }) => path === '/v1/models/download').length, 0);

    mode = 'partial';
    appStore.modelSource = 'qwen partial';
    await appStore.searchModels();
    assert.equal(appStore.searchError, '');
    assert.equal(appStore.searchResults.length, 1);
    assert.deepEqual(appStore.searchWarnings, ['org/bad could not be searched']);
    assert.equal(appStore.searchQuery, 'qwen partial');
    appStore.modelSource = 'edited during partial results';
    assert.equal(appStore.searchQuery, 'qwen partial');

    const source = 'https://huggingface.co/demo/model-GGUF/resolve/release%2Fv2/gguf/model.Q4_K_M.gguf';
    await appStore.downloadModel(source);
    const download = requests.filter(({ path }) => path === '/v1/models/download');
    assert.equal(download.length, 1);
    assert.equal(download[0].body.source, source);

    requests.length = 0;
    appStore.status = {
      role: 'worker', node_id: 'worker', join_key: 'secret', master_host: '10.0.0.1',
      members: {
        master: { node_id: 'master', host: 'example', role: 'master' },
        worker: { node_id: 'worker', host: 'worker-host', role: 'worker' },
      },
    };
    appStore.liveUsage = {
      nodes: {
        master: { node_id: 'master', host: 'example', role: 'master', connected: true, freshness: 'unavailable', reading: null },
        worker: { node_id: 'worker', host: 'worker-host', role: 'worker', connected: true, freshness: 'unavailable', reading: null },
      },
      relationships: [{ from_node_id: 'master', to_node_id: 'worker', kind: 'master-worker' }],
      recording: { recording: 'interrupted', last_successful_save: null },
    };
    await appStore.leaveCluster();
    assert.ok(requests.some((req) => req.path === '/v1/leave' && req.method === 'POST'));
    assert.equal(appStore.status.role, 'idle');
    assert.deepEqual(appStore.status.members, {});
    assert.equal(appStore.status.join_key, null);
    assert.equal(appStore.status.master_host, null);
    assert.deepEqual(appStore.liveUsage.nodes, {});
    assert.deepEqual(appStore.liveUsage.relationships, []);
    assert.equal(appStore.selectedNodeId, '');

    appStore.status = {
      role: 'master', node_id: 'master', join_key: 'secret',
      members: { master: { node_id: 'master', role: 'master' } },
    };
    appStore.liveUsage = {
      nodes: { master: { node_id: 'master', host: 'old-host', role: 'master', connected: false,
        freshness: 'stale', reading: null } },
      relationships: [],
      recording: { recording: 'interrupted', last_successful_save: null },
    };
    appStore.selectedNodeId = 'master';
    await appStore.stopCluster();
    assert.ok(requests.some((req) => req.path === '/v1/stop' && req.method === 'POST'));
    assert.equal(appStore.status.role, 'idle');
    assert.deepEqual(appStore.status.members, {});
    assert.equal(appStore.status.join_key, null);
    assert.deepEqual(appStore.liveUsage.nodes, {});
    assert.deepEqual(appStore.liveUsage.relationships, []);
    assert.equal(appStore.selectedNodeId, '');
    appStore.connected = false;
    assert.equal(appStore.viewState(), 'idle');

    requests.length = 0;
    appStore.chatStreaming = true;
    await appStore.stopInference();
    assert.equal(appStore.chatStreaming, false);
    assert.ok(requests.some((req) => req.path === '/v1/inference/stop' && req.method === 'POST'));
  } finally {
    appStore?.stop();
    globalThis.fetch = originalFetch;
    try { await server?.watcher?.close(); } catch { /* ignore */ }
    await server?.close();
    process.chdir(cwd);
  }
});
