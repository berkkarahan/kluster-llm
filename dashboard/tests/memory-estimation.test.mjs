import assert from 'node:assert/strict';
import test from 'node:test';
import { fileURLToPath } from 'node:url';
import { createServer } from 'vite';

test('memory controls refresh previews, explain constant batch totals and suppress stale responses', async () => {
  const cwd = process.cwd();
  const originalFetch = globalThis.fetch;
  const requests = [];
  const pending = [];
  let defer = false;
  let store;
  let server;
  const response = (body, total = 1024 ** 3) => new Response(JSON.stringify({
    estimated_bytes: total, budget_bytes: 8 * 1024 ** 3, fit_status: 'fits',
    weights_estimate_bytes: 512 * 1024 ** 2, auxiliary_weights_bytes: 0,
    kv_estimate_bytes: (body.cache_type_k === 'q4_0' && body.cache_type_v === 'q4_0' ? 16 : 32) * 1024 ** 2, recurrent_state_bytes: 0,
    compute_estimate_bytes: 40 * 1024 ** 2, checkpoint_estimate_bytes: 0,
    auxiliary_runtime_bytes: 0, execution_overhead_bytes: 256 * 1024 ** 2,
    estimate_complete: false, estimate_warnings: ['Fixture unpriced argument'],
    resolved_launch_settings: { context: body.context, batch_size: body.batch_size,
      micro_batch: 128, parallel_slots: body.parallel_slots, cache_type_k: body.cache_type_k, cache_type_v: body.cache_type_v }
  }), { status: 200 });
  try {
    process.chdir(fileURLToPath(new URL('..', import.meta.url)));
    server = await createServer({ server: { middlewareMode: true, hmr: false, ws: false, watch: null }, appType: 'custom' });
    globalThis.fetch = async (path, options) => {
      assert.equal(path, '/v2/inference/plans?preview=true');
      const body = JSON.parse(options.body);
      requests.push(body);
      if (defer) return new Promise((resolve) => pending.push({ body, resolve }));
      return response(body);
    };
    ({ appStore: store } = await server.ssrLoadModule('/src/lib/store.svelte.ts'));
    store.models = [{ id: 'fixture.gguf' }];
    store.selectedModel = 'fixture.gguf';
    store.status = { role: 'master', node_id: 'master', members: { master: { node_id: 'master' } } };
    store.batchSize = 512;
    await store.refreshEstimate();
    store.batchSize = 1024;
    await store.refreshEstimate();
    assert.equal(requests.length, 2);
    assert.match(store.estimateNote, /Preview recalculated.*logical batch changed.*micro-batch remains 128/);
    assert.equal(store.estimateRecalculations, 2);

    // Exercise the debounced path used by the controls with each wire-format input.
    for (const [field, value, key] of [
      ['context', 8192, 'context'], ['contextAuto', true, 'context_auto'],
      ['parallelSlots', 4, 'parallel_slots'], ['batchSize', 2048, 'batch_size'],
      ['microBatch', 256, 'micro_batch'], ['cacheTypeK', 'f16', 'cache_type_k'],
      ['cacheTypeV', 'f32', 'cache_type_v'], ['flashAttention', 'on', 'flash_attention'],
      ['checkpoints', 4, 'checkpoints'], ['draftModel', '/draft.gguf', 'draft_model'],
      ['speculative', 'draft', 'speculative'], ['vision', true, 'vision'],
      ['mmproj', '/projector.gguf', 'mmproj'], ['extraArgsText', '--swa-full', 'extra_args']
    ]) {
      const before = requests.length;
      store[field] = value;
      store.scheduleEstimate(0);
      await new Promise((resolve) => setTimeout(resolve, 15));
      assert.equal(requests.length, before + 1, field);
      assert.deepEqual(requests.at(-1)[key], field === 'extraArgsText' ? ['--swa-full'] : value);
    }

    const { render } = await server.ssrLoadModule('svelte/server');
    const { default: RunPage } = await server.ssrLoadModule('/src/routes/run/+page.svelte');
    const html = render(RunPage).body;
    assert.match(html, /Model weights/);
    assert.match(html, /KV cache/);
    assert.match(html, /32\.00 MiB/);
    assert.match(html, /40\.00 MiB/);
    assert.match(html, /Resolved: context 8192/);
    assert.match(html, /Incomplete estimate/);
    assert.match(html, /Fixture unpriced argument/);

    store.cacheTypeK = 'q8_0'; store.cacheTypeV = 'q8_0';
    await store.refreshEstimate();
    store.cacheTypeK = 'q4_0'; store.cacheTypeV = 'q4_0';
    await store.refreshEstimate();
    assert.match(store.estimateNote, /KV cache decreased by 16\.00 MiB.*weights and host checkpoints are unchanged/);
    const quantized = render(RunPage).body;
    assert.match(quantized, /K cache: q4_0.*V cache: q4_0/);
    assert.match(quantized, /Must use this node/);
    store.setNodeRequired('master', true);
    if (store.estimateTimer) clearTimeout(store.estimateTimer);
    await store.refreshEstimate();
    assert.deepEqual(requests.at(-1).required_nodes, ['master']);
    store.applyParameterSet({ required_nodes: ['master', 'missing'] });
    assert.match(render(RunPage).body, /missing.*unavailable/);
    store.nodeShares = { master: 1 };
    assert.deepEqual(store.planPayload().required_nodes, []);
    store.nodeShares = null;
    store.setNodeRequired('missing', false);
    if (store.estimateTimer) clearTimeout(store.estimateTimer);
    assert.deepEqual(store.requiredNodes, ['master']);
    store.applyParameterSet({ node_shares: { missing: 0.8, master: 0.2 } });
    assert.deepEqual(store.nodePriority, ['missing', 'master']);
    assert.equal(store.nodeShares, null);
    assert.match(render(RunPage).body, /Remove unavailable missing/);
    store.removeNodePriority('missing');
    assert.deepEqual(store.planPayload().node_priority, ['master']);
    store.applyParameterSet({ node_priority: ['master'], required_nodes: ['master'] });
    assert.deepEqual(store.nodePriority, ['master']);
    store.setPriorityPlanning(false);
    assert.deepEqual(store.planPayload().node_priority, []);
    assert.deepEqual(store.planPayload().required_nodes, ['master']);
    if (store.estimateTimer) clearTimeout(store.estimateTimer);

    // Schedule invalidates in-flight previews before the next request even starts.
    defer = true;
    const old = store.refreshEstimate();
    store.batchSize = 4096;
    store.scheduleEstimate(0);
    await new Promise((resolve) => setTimeout(resolve, 15));
    assert.equal(pending.length, 2);
    pending[1].resolve(response(pending[1].body, 2 * 1024 ** 3));
    await new Promise((resolve) => setTimeout(resolve, 0));
    const recalculations = store.estimateRecalculations;
    pending[0].resolve(response(pending[0].body, 3 * 1024 ** 3));
    await old;
    assert.equal(store.planResult.estimated_bytes, 2 * 1024 ** 3);
    assert.equal(store.estimateRecalculations, recalculations);
    assert.equal(store.estimating, false);
  } finally {
    store?.stop();
    globalThis.fetch = originalFetch;
    await server?.watcher?.close();
    await server?.close();
    process.chdir(cwd);
  }
});
