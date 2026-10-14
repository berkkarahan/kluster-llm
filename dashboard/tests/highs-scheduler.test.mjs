import assert from 'node:assert/strict';
import test from 'node:test';
import { fileURLToPath } from 'node:url';
import { createServer } from 'vite';
import {
  SAMPLE_HIGHS_REPORT,
  buildFallbackHighsReport,
} from '../src/lib/modules/run/__tests__/highs-scheduler.test.ts';

test('HiGHS scheduler placement strategy selector, payload serialization, and telemetry rendering', async () => {
  const cwd = process.cwd();
  const originalFetch = globalThis.fetch;
  const requests = [];
  let server;
  let store;

  try {
    process.chdir(fileURLToPath(new URL('..', import.meta.url)));
    server = await createServer({
      server: { middlewareMode: true, hmr: false, ws: false, watch: null },
      appType: 'custom',
    });

    globalThis.fetch = async (path, options = {}) => {
      assert.equal(path, '/v2/inference/plans?preview=true');
      const body = JSON.parse(String(options.body ?? '{}'));
      requests.push(body);
      const isFallback = body.placement_strategy !== 'auto';
      const scheduler = isFallback
        ? buildFallbackHighsReport(body.placement_strategy)
        : SAMPLE_HIGHS_REPORT;

      return new Response(
        JSON.stringify({
          estimated_bytes: 6 * 1024 ** 3,
          budget_bytes: 16 * 1024 ** 3,
          fit_status: 'fits',
          weights_estimate_bytes: 4 * 1024 ** 3,
          auxiliary_weights_bytes: 0,
          kv_estimate_bytes: 1024 ** 3,
          recurrent_state_bytes: 0,
          compute_estimate_bytes: 512 * 1024 ** 2,
          checkpoint_estimate_bytes: 0,
          auxiliary_runtime_bytes: 0,
          execution_overhead_bytes: 512 * 1024 ** 2,
          placement_mode: scheduler.scheduler_mode,
          scheduler,
          resolved_launch_settings: {
            context: body.context,
            batch_size: body.batch_size,
            micro_batch: 128,
            parallel_slots: body.parallel_slots,
            cache_type_k: body.cache_type_k,
            cache_type_v: body.cache_type_v,
          },
        }),
        { status: 200 }
      );
    };

    store = (await server.ssrLoadModule('/src/lib/app.svelte.ts')).createApp();
    store.models.models = [{ id: 'prima-model.gguf' }];
    store.models.selectedModel = 'prima-model.gguf';
    store.cluster.status = {
      role: 'master',
      node_id: 'master',
      members: {
        master: { node_id: 'master', role: 'master' },
        'worker-fast': { node_id: 'worker-fast', role: 'worker' },
      },
    };

    assert.equal(store.run.placementStrategy, 'auto');
    assert.equal(store.run.planPayload().placement_strategy, 'auto');

    await store.run.refreshEstimate();
    assert.equal(requests.at(-1)?.placement_strategy, 'auto');

    const { render } = await server.ssrLoadModule('svelte/server');
    const { default: RunPage } = await server.ssrLoadModule('/src/routes/run/+page.svelte');
    const htmlAuto = render(RunPage).body;

    assert.match(htmlAuto, /Automatic \(HiGHS \/ Profiled\)/);
    assert.match(htmlAuto, /Capacity-first/);
    assert.match(htmlAuto, /Priority/);
    assert.match(htmlAuto, /HiGHS Halda MILP/);
    assert.match(htmlAuto, /highs_halda · k=2 · W=16/);
    assert.match(htmlAuto, /Step objective: 42\.50 ms/);
    assert.match(htmlAuto, /M1/);
    assert.match(htmlAuto, /M4/);
    assert.match(htmlAuto, /forced M4/);
    assert.match(htmlAuto, /graphs=4/);
    assert.match(htmlAuto, /worker-weak \(weak_device_w_le_1_ngl_0\)/);

    store.run.setPlacementStrategy('capacity_first');
    if (store.run.estimateTimer) clearTimeout(store.run.estimateTimer);
    assert.equal(store.run.placementStrategy, 'capacity_first');
    assert.equal(store.run.nodePriority, null);
    assert.equal(store.run.planPayload().placement_strategy, 'capacity_first');

    await store.run.refreshEstimate();
    assert.equal(requests.at(-1)?.placement_strategy, 'capacity_first');
    const htmlCap = render(RunPage).body;
    assert.match(htmlCap, /capacity_first · k=1 · W=32/);
    assert.match(htmlCap, /Fallback reason: operator_selected_capacity_first/);
    assert.match(htmlCap, /missing profiles: worker-fast/);

    store.run.setPlacementStrategy('priority');
    if (store.run.estimateTimer) clearTimeout(store.run.estimateTimer);
    assert.equal(store.run.placementStrategy, 'priority');
    assert.deepEqual(store.run.nodePriority, ['master', 'worker-fast']);
    assert.equal(store.run.planPayload().placement_strategy, 'priority');

    await store.run.refreshEstimate();
    assert.equal(requests.at(-1)?.placement_strategy, 'priority');
    const htmlPrio = render(RunPage).body;
    assert.match(htmlPrio, /priority · k=1 · W=32/);
    assert.match(htmlPrio, /Fallback reason: operator_selected_priority/);
  } finally {
    store?.stop();
    globalThis.fetch = originalFetch;
    await server?.watcher?.close();
    await server?.close();
    process.chdir(cwd);
  }
});
