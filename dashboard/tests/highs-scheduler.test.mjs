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
  const runtimeId = '1'.repeat(64);
  const version = {
    runtime_id: runtimeId, engine: 'primacpp', name: 'Prima fixture',
    repository: 'https://github.com/fixture/prima.cpp', commit: 'a'.repeat(40),
    ref: 'a'.repeat(40), ref_type: 'commit',
  };
  const build = {
    runtime_id: runtimeId, build_id: '2'.repeat(64), commit: version.commit,
    engine: 'primacpp', backend: 'vulkan', transport: 'prima-ring-tcp',
  };
  const requests = [];
  const unexpectedRequests = [];
  let missingProfiles = false;
  let server;
  let store;

  try {
    process.chdir(fileURLToPath(new URL('..', import.meta.url)));
    server = await createServer({
      server: { middlewareMode: true, hmr: false, ws: false, watch: null },
      appType: 'custom',
    });

    globalThis.fetch = async (path, options = {}) => {
      const method = options.method ?? 'GET';
      if (path === `/v2/inference/capabilities?model=prima-model.gguf&runtime_id=${runtimeId}` && method === 'GET') {
        return Response.json({ runtime_id: runtimeId, engine: 'primacpp', deployment_review_version: 1, parameters: [], threads_max: 8 });
      }
      if (path === '/v2/inference/engine-model-settings/primacpp/prima-model.gguf' && method === 'GET') {
        return Response.json({
          engine: 'primacpp', model: 'prima-model.gguf', revision: 0,
          requested_parameter_set: { context: 4096, cache_type_k: 'f16', cache_type_v: 'f16', flash_attention: 'off' },
          parameter_set: {}, legacy_parameter_set: null,
        });
      }
      if (path !== `/v2/node/runtimes/${runtimeId}/prima-plan` || method !== 'POST') {
        unexpectedRequests.push({ path, method });
        assert.fail(`Unexpected request: ${method} ${path}`);
      }
      const body = JSON.parse(String(options.body ?? '{}'));
      assert.equal(body.model, 'prima-model.gguf');
      assert.equal(body.settings_source, 'defaults');
      assert.equal(body.remember, false);
      assert.equal(body.advanced_open, false);
      requests.push(body);
      const isFallback = body.placement_strategy !== 'auto';
      const scheduler = missingProfiles && body.placement_strategy === 'auto'
        ? {
            ...buildFallbackHighsReport('auto'),
            fallback_reason: 'missing_device_profiles',
            missing_profile_nodes: ['master'],
          }
        : isFallback
          ? buildFallbackHighsReport(body.placement_strategy)
          : SAMPLE_HIGHS_REPORT;

      return new Response(
        JSON.stringify({
          runtime_id: runtimeId,
          plan_digest: '3'.repeat(64),
          deployment_review_version: 1,
          inference_supported: false,
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
          engine: 'primacpp',
          transport: 'prima-ring-tcp',
          prima_topology: {
            schema_version: 1,
            runtime_id: runtimeId,
            master_node_id: 'master',
            total_layers: 32,
            data_port: 51052,
            signal_port: 51053,
            ranks: [
              {
                node_id: 'master',
                role: 'master',
                rank: 0,
                address: '10.0.0.1',
                next_node_id: 'worker-fast',
                layers: [{ start: 0, end: 20 }],
                ngl: 16,
                devices: ['Vulkan0'],
                tensor_split: [16],
                output_gpu: false,
              },
              {
                node_id: 'worker-fast',
                role: 'worker',
                rank: 1,
                address: '10.0.0.2',
                next_node_id: 'master',
                layers: [{ start: 20, end: 32 }],
                ngl: 12,
                devices: ['Vulkan0'],
                tensor_split: [12],
                output_gpu: true,
              },
            ],
          },
          model_support: {
            supported: true,
            error: null,
            warning: null,
          },
          qualification_errors: ['vulkan_gpu_inference'],
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

    const { parsePrimaExtras, serializePrimaExtras } = await server.ssrLoadModule(
      '/src/lib/modules/run/configuration.ts'
    );
    const { resolveActiveEngine } = await server.ssrLoadModule(
      '/src/lib/modules/run/entities.ts'
    );

    // Verify Prima extra_args round-tripping
    const serializedExtras = serializePrimaExtras({
      swaMode: 'full',
      headMode: 'embedding',
      pooling: 'mean',
      loraAdapters: [
        { path: '/models/adapter-a.gguf', scale: '1' },
        { path: '/models/adapter-b.gguf', scale: '0.75' },
      ],
      templateKwargsText: '{"enable_thinking":true}',
      otherTokens: [],
    });
    const parsedExtras = parsePrimaExtras(serializedExtras);
    assert.equal(parsedExtras.swaMode, 'full');
    assert.equal(parsedExtras.headMode, 'embedding');
    assert.equal(parsedExtras.pooling, 'mean');
    assert.deepEqual(parsedExtras.loraAdapters, [
      { path: '/models/adapter-a.gguf', scale: '1' },
      { path: '/models/adapter-b.gguf', scale: '0.75' },
    ]);
    assert.equal(parsedExtras.templateKwargsText, '{"enable_thinking":true}');

    store = (await server.ssrLoadModule('/src/lib/app.svelte.ts')).createApp();
    store.models.models = [{ id: 'prima-model.gguf' }];
    store.models.selectedModel = 'prima-model.gguf';
    store.http.apiToken = 'fixture-token';
    store.live.connected = true;
    store.live.deliveryHealth = 'live';
    store.live.areaHealth = { status: { availability: 'available' } };
    store.cluster.status = {
      role: 'master',
      node_id: 'master',
      engine: 'primacpp',
      members: {
        master: { node_id: 'master', role: 'master', host: '10.0.0.1', online: true },
        'worker-fast': { node_id: 'worker-fast', role: 'worker', host: '10.0.0.2', online: true },
      },
      runtimes: {
        versions: [{ version, build, inference_supported: false, qualification_errors: ['vulkan_gpu_inference'] }],
        target: { version, revision: 1, cluster_binding_id: 'fixture', master_node_id: 'master' },
        target_state: 'current', selected: runtimeId, running: null, jobs: [],
        nodes: ['master', 'worker-fast'].map(node_id => ({
          node_id, role: node_id === 'master' ? 'master' : 'worker', capability: 1,
          phase: 'ready', prepared: build, target_revision: 1, channel: { connected: true },
        })),
      },
    };
    const deployment = store.runtimes.deployment;
    await deployment.setRuntime(runtimeId);
    assert.equal(store.run.settingsLoadError, '');
    assert.equal(store.run.capabilitiesError, '');
    assert.equal(store.run.settingsScope, 'primacpp');
    assert.equal(store.run.settingsRuntimeId, runtimeId);

    assert.equal(resolveActiveEngine(store.cluster.status, null, null), 'primacpp');
    assert.equal(store.run.placementStrategy, 'auto');
    assert.equal(store.run.planPayload().placement_strategy, 'auto');

    await deployment.inspect();
    assert.equal(deployment.fresh, true);
    assert.equal(deployment.error, '');
    assert.equal(deployment.preview.runtimeId, runtimeId);
    assert.ok(Object.isFrozen(deployment.preview.plan.scheduler));
    assert.equal(deployment.reviewReason, 'vulkan_gpu_inference');
    assert.equal(await deployment.openReview(), null, 'architecture support does not bypass qualification');
    assert.equal(requests.at(-1)?.placement_strategy, 'auto');

    const { render } = await server.ssrLoadModule('svelte/server');
    const { default: RunRoute } = await server.ssrLoadModule('/src/routes/run/+page.svelte');
    const htmlAuto = render(RunRoute).body;

    // Dedicated PrimaRunPage header, status strip, Ring-TCP rank badges & Halda MILP telemetry
    assert.match(htmlAuto, /Deploy · Prima\.cpp/);
    assert.match(htmlAuto, /prima-ring-tcp/);
    assert.match(htmlAuto, /Architecture supported/);
    assert.match(htmlAuto, /Evaluation &amp; profiling mode/);
    assert.match(htmlAuto, /<button\b[^>]*disabled[^>]*>Review deployment<\/button>/);
    assert.match(htmlAuto, /Ring-TCP: 2 rank\(s\) · ports 51052\/51053/);
    assert.match(htmlAuto, /Rank 0 → worker-fast · -lw 0–19 · -ngl 16/);
    assert.match(htmlAuto, /Rank 1 → master · -lw 20–31 · -ngl 12/);
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
    assert.match(htmlAuto, /worker-weak \(weak_device_w_le_1_ngl_0/);

    store.run.setPlacementStrategy('capacity_first');
    assert.equal(store.run.placementStrategy, 'capacity_first');
    assert.equal(store.run.nodePriority, null);
    assert.equal(store.run.planPayload().placement_strategy, 'capacity_first');
    assert.equal(deployment.fresh, false);
    const htmlStale = render(RunRoute).body;
    assert.match(htmlStale, /Settings changed\. Prepare and Review will calculate/);
    assert.doesNotMatch(htmlStale, /Architecture supported|highs_halda · k=2|Rank 0 → worker-fast/);

    await deployment.inspect();
    assert.equal(deployment.fresh, true);
    assert.equal(requests.at(-1)?.placement_strategy, 'capacity_first');
    const htmlCap = render(RunRoute).body;
    assert.match(htmlCap, /capacity_first · k=1 · W=32/);
    assert.match(htmlCap, /Fallback reason: operator_selected_capacity_first/);
    assert.match(htmlCap, /missing profiles: worker-fast/);

    store.run.setPlacementStrategy('priority');
    assert.equal(store.run.placementStrategy, 'priority');
    assert.deepEqual(store.run.nodePriority, ['master', 'worker-fast']);
    assert.equal(store.run.planPayload().placement_strategy, 'priority');

    await deployment.inspect();
    assert.equal(deployment.fresh, true);
    assert.equal(requests.at(-1)?.placement_strategy, 'priority');
    assert.deepEqual(requests.at(-1)?.node_priority, ['master', 'worker-fast']);
    const htmlPrio = render(RunRoute).body;
    assert.match(htmlPrio, /priority · k=1 · W=32/);
    assert.match(htmlPrio, /Fallback reason: operator_selected_priority/);

    // A genuinely unprofiled cluster keeps the accurate reason and points at
    // the Prima preparation that enables HiGHS / Profiled placement.
    missingProfiles = true;
    store.run.setPlacementStrategy('auto');
    assert.equal(store.run.planPayload().placement_strategy, 'auto');
    await deployment.inspect();
    assert.equal(deployment.fresh, true);
    assert.equal(deployment.prepareReason, '');
    const htmlProfiles = render(RunRoute).body;
    assert.match(htmlProfiles, /Fallback reason: missing_device_profiles/);
    assert.match(htmlProfiles, /missing profiles: master/);
    assert.match(htmlProfiles, /Settings → Prima/);
    assert.match(htmlProfiles, /enable HiGHS \/ Profiled placement/);
    const profileButton = htmlProfiles.match(/<button\b[^>]*>Prepare &amp; profile<\/button>/)?.[0];
    assert.ok(profileButton, 'current summary exposes the preparation action');
    assert.doesNotMatch(profileButton, /\bdisabled\b/);
    assert.doesNotMatch(htmlProfiles, /Step 3/);

    // Switching the node engine to llamacpp cleanly renders the standard RunPage
    store.cluster.status = {
      ...store.cluster.status,
      engine: 'llamacpp',
    };
    assert.equal(resolveActiveEngine(store.cluster.status, null, store.run.planResult), 'llamacpp');
    const htmlLlama = render(RunRoute).body;
    assert.doesNotMatch(htmlLlama, /Deploy · Prima\.cpp/);
    assert.match(htmlLlama, /What would you like to run\?/);
    assert.deepEqual(unexpectedRequests, [], 'only scoped settings and runtime-bound preview endpoints are allowed');
    assert.deepEqual(requests.map(body => body.placement_strategy), ['auto', 'capacity_first', 'priority', 'auto']);
  } finally {
    store?.stop();
    globalThis.fetch = originalFetch;
    await server?.watcher?.close();
    await server?.close();
    process.chdir(cwd);
  }
});
