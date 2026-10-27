import assert from 'node:assert/strict';
import test from 'node:test';
import { spawn } from 'node:child_process';
import { existsSync, mkdtempSync, readFileSync } from 'node:fs';
import { createServer as createHttpServer } from 'node:http';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { createServer } from 'vite';
import { removeBrowserProfile } from './browser-profile.mjs';

const chrome = [
  process.env.CHROME_BIN,
  'C:/Program Files/Google/Chrome/Application/chrome.exe',
  'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',
].find((path) => path && existsSync(path));

test('Challenger Milestone 3 Empirical Stress Tests', async (suite) => {
  const cwd = process.cwd();
  process.chdir(fileURLToPath(new URL('..', import.meta.url)));

  const server = await createServer({
    server: { middlewareMode: true, hmr: false, ws: false, watch: null },
    appType: 'custom',
  });

  try {
    const { render } = await server.ssrLoadModule('svelte/server');
    const { PrimaDeployment } = await server.ssrLoadModule('/src/lib/modules/run/prima-deployment.svelte.ts');
    const { default: NextDeploymentView } = await server.ssrLoadModule('/src/lib/modules/run/view/NextDeploymentView.svelte');
    const { default: PrepareProfileStepper } = await server.ssrLoadModule('/src/lib/modules/run/view/PrepareProfileStepper.svelte');
    const { default: DeploymentReadinessCard } = await server.ssrLoadModule('/src/lib/modules/run/view/DeploymentReadinessCard.svelte');
    const { default: LinkMatrixView } = await server.ssrLoadModule('/src/lib/modules/run/view/LinkMatrixView.svelte');

    const runtimeId = 'e'.repeat(64);
    const planDigest = 'f'.repeat(64);

    function createMockFixture(opts = {}) {
      let context = opts.initialContext || 4096;
      let latencyDelay = opts.latencyDelay || 10;
      let planFail = opts.planFail || false;
      let planError = opts.planError || 'Inspection rejected';
      let singleNode = opts.singleNode ?? false;

      const members = singleNode
        ? { master1: { host: '192.168.1.10', online: true, role: 'master', devices: [{ index: 0, name: 'RTX 4090' }] } }
        : {
            master1: { host: '192.168.1.10', online: true, role: 'master', devices: [{ index: 0, name: 'RTX 4090' }] },
            worker1: { host: '192.168.1.11', online: true, role: 'worker', devices: [{ index: 0, name: 'RTX 3090' }] },
          };

      const ranks = singleNode ? [{ node_id: 'master1' }] : [{ node_id: 'master1' }, { node_id: 'worker1' }];

      const run = {
        settingsScope: 'primacpp',
        settingsRuntimeId: runtimeId,
        selectedModel: 'Qwen3.8-27B.gguf',
        selectionGeneration: 1,
        selectedNetworkId: 'lan',
        contextAuto: false,
        get context() { return context; },
        parallelSlots: 1,
        cacheTypeK: 'f16',
        cacheTypeV: 'f16',
        live: { connected: true, deliveryHealth: 'live', areaHealth: { status: { availability: 'available' } } },
        localDraftErrors: {},
        stopParseError: '',
        busy: '',
        loadProgress: null,
        status: {
          role: 'master',
          node_id: 'master1',
          members,
          runtimes: {
            target: { version: { runtime_id: runtimeId, engine: 'primacpp' } },
            nodes: [{ node_id: 'master1', phase: 'ready', prepared: { runtime_id: runtimeId } }],
          },
        },
        networkPlanPayload: () => ({ model: 'Qwen3.8-27B.gguf', context, cache_type_k: 'f16', cache_type_v: 'f16' }),
        validateRunSettings: () => true,
        planRunBlockedReason: () => null,
        refreshEstimate: () => {},
        effectiveConfig: () => ({ values: { context_per_request: context } }),
        http: {
          loadToken: async () => {},
          command: async () => ({}),
          networkRequest: async (url, method, body) => {
            if (url.includes('/retry')) {
              return {
                run_id: 'run-stress-1',
                status: 'running',
                retrying_stage: body?.stage_name || 'link_profile',
                preserved_stages: ['materialize', 'asset_sync', 'node_profile'],
              };
            }
            if (url.includes('/coverage')) {
              return {
                run_id: 'run-stress-1',
                coverage_status: 'profiled',
                node_profiles: {
                  master1: { gpu_layer_ms: 2.1, cpu_layer_ms: 15.0, usable_memory_bytes: 24e9 },
                  worker1: { gpu_layer_ms: 2.4, cpu_layer_ms: 16.5, usable_memory_bytes: 24e9 },
                },
                link_matrix: [
                  { source: 'master1', target: 'worker1', latency_ms: 0.38, bandwidth_bytes_s: 1.25e9 },
                  { source: 'worker1', target: 'master1', latency_ms: 0.41, bandwidth_bytes_s: 1.22e9 },
                ],
              };
            }
            if (url.includes('/logs')) {
              return {
                run_id: 'run-stress-1',
                logs: [
                  { timestamp: 1700000000, level: 'INFO', stage_name: 'asset_sync', message: 'Asset staged' },
                  { timestamp: 1700000005, level: 'ERROR', stage_name: 'link_profile', message: 'Socket timeout 50052' },
                ],
                text: 'Logs',
              };
            }
            return {};
          },
        },
      };

      const runtimes = {
        primaPlan: async (rId, settings, signal) => {
          if (latencyDelay > 0) {
            await new Promise((resolve) => setTimeout(resolve, latencyDelay));
          }
          if (signal?.aborted) {
            throw new Error('Aborted');
          }
          if (planFail) {
            throw new Error(planError);
          }
          return {
            runtime_id: runtimeId,
            plan_digest: planDigest,
            deployment_review_version: 1,
            inference_supported: true,
            fit_status: 'fits',
            prima_topology: { ranks },
            profiling: {
              status: 'profiled',
              node_profiles: { master1: { gpu_layer_ms: 2.1 } },
              link_matrix: singleNode ? [] : [{ source: 'master1', target: 'worker1', latency_ms: 0.4, bandwidth_bytes_s: 1.2e9 }],
            },
            model_support: { supported: true },
          };
        },
        preparePrima: async (rId, settings, digest, profile, reqId) => {
          if (latencyDelay > 0) {
            await new Promise((resolve) => setTimeout(resolve, latencyDelay));
          }
          return {
            run_id: 'run-stress-1',
            job_id: 'run-stress-1',
            status: 'running',
            phase: 'preparing',
            current_stage: 'materialize',
            stages: [
              { stage_name: 'materialize', stage_order: 1, status: 'running', attempt: 1, progress_fraction: 0.5, progress_label: 'Materializing', bytes_done: 0, bytes_total: 0, error_message: null },
              { stage_name: 'asset_sync', stage_order: 2, status: 'pending', attempt: 1, progress_fraction: 0, progress_label: 'Pending', bytes_done: 0, bytes_total: 0, error_message: null },
              { stage_name: 'node_profile', stage_order: 3, status: 'pending', attempt: 1, progress_fraction: 0, progress_label: 'Pending', bytes_done: 0, bytes_total: 0, error_message: null },
              { stage_name: 'link_profile', stage_order: 4, status: 'pending', attempt: 1, progress_fraction: 0, progress_label: 'Pending', bytes_done: 0, bytes_total: 0, error_message: null },
              { stage_name: 'plan_qualification', stage_order: 5, status: 'pending', attempt: 1, progress_fraction: 0, progress_label: 'Pending', bytes_done: 0, bytes_total: 0, error_message: null },
            ],
            nodes: { master1: { role: 'master', status: 'prepared' } },
          };
        },
        preparation: {
          job: null,
          active: false,
          observe: () => {},
          acquire: () => () => {},
        },
      };

      const deployment = new PrimaDeployment(run, runtimes);
      deployment.runtimeId = runtimeId;

      return {
        run,
        runtimes,
        deployment,
        setContext: (c) => { context = c; deployment.changed(); },
        setPlanFail: (f, err) => { planFail = f; if (err) planError = err; },
      };
    }

    // -------------------------------------------------------------------------
    // Test 1: Rapid draft changes while preparation or inspection is pending
    // -------------------------------------------------------------------------
    await suite.test('1. Rapid draft changes while inspection or preparation is pending do not deadlock or crash', async (t) => {
      await t.test('1.1 50 rapid interleaved draft edits while inspect is in-flight settle cleanly', async () => {
        const { deployment, setContext } = createMockFixture({ latencyDelay: 15 });
        try {
          const promises = [];
          for (let i = 0; i < 50; i++) {
            setContext(4096 + i * 128);
            promises.push(deployment.inspect());
            if (i % 5 === 0) {
              await new Promise((r) => setTimeout(r, 2));
            }
          }

          // Await all promises without unhandled rejections
          const results = await Promise.allSettled(promises);
          assert.equal(results.some((r) => r.status === 'rejected'), false, 'No unhandled promise rejection occurred');

          // Verify state is unlocked: inspecting is false, no deadlocks
          assert.equal(deployment.inspecting, false, 'Inspecting lock released');
          assert.equal(deployment.pendingAction, null, 'Pending action lock released');

          // A final inspect succeeds
          const finalPlan = await deployment.inspect(true);
          assert.ok(finalPlan, 'Final inspection produces valid plan');
          assert.equal(deployment.fresh, true);
        } finally {
          deployment.stop();
        }
      });

      await t.test('1.2 Rapid draft changes while prepare() is awaiting inspection cleanly abort without deadlock', async () => {
        const { deployment, setContext } = createMockFixture({ latencyDelay: 20 });
        try {
          await deployment.inspect();
          assert.equal(deployment.fresh, true);

          // Launch prepare in background
          const prepPromise = deployment.prepare(true);

          // Immediately edit draft while prepare is running
          setContext(16384);
          setContext(32768);

          const result = await prepPromise;
          assert.equal(result, null, 'Prepare aborted cleanly because draft changed');
          assert.equal(deployment.pendingAction, null, 'pendingAction must be unlocked');
          assert.match(deployment.notice, /Deployment changed while calculating/);
        } finally {
          deployment.stop();
        }
      });

      await t.test('1.3 Rapid draft changes during in-flight preparePrima HTTP request do not crash', async () => {
        const { deployment, setContext } = createMockFixture({ latencyDelay: 25 });
        try {
          await deployment.inspect();
          const prepPromise = deployment.prepare(true);

          // Change draft while HTTP call is underway
          await new Promise((r) => setTimeout(r, 5));
          setContext(8192);

          await prepPromise;
          assert.equal(deployment.preparing, false, 'preparing flag safely cleared');
          assert.equal(deployment.pendingAction, null, 'pendingAction safely cleared');
        } finally {
          deployment.stop();
        }
      });
    });

    // -------------------------------------------------------------------------
    // Test 2: Stage retry UX
    // -------------------------------------------------------------------------
    await suite.test('2. Stage retry UX: simulate stage failure, verify retry button, and preserve completed stages', async (t) => {
      const { deployment, run } = createMockFixture();
      try {
        // Set up active run with failed link_profile stage
        deployment.activeRun = {
          run_id: 'run-stress-1',
          job_id: 'run-stress-1',
          status: 'failed',
          phase: 'failed',
          current_stage: 'link_profile',
          stages: [
            { stage_name: 'materialize', stage_order: 1, status: 'completed', attempt: 1, progress_fraction: 1, progress_label: 'Done', bytes_done: 0, bytes_total: 0, error_message: null },
            { stage_name: 'asset_sync', stage_order: 2, status: 'completed', attempt: 1, progress_fraction: 1, progress_label: '100 MB / 100 MB', bytes_done: 1e8, bytes_total: 1e8, error_message: null },
            { stage_name: 'node_profile', stage_order: 3, status: 'completed', attempt: 1, progress_fraction: 1, progress_label: 'Hardware profiled', bytes_done: 0, bytes_total: 0, error_message: null },
            { stage_name: 'link_profile', stage_order: 4, status: 'failed', attempt: 1, progress_fraction: 0.3, progress_label: 'Timed out', bytes_done: 0, bytes_total: 0, error_message: 'Socket timeout on 192.168.1.11:50052' },
            { stage_name: 'plan_qualification', stage_order: 5, status: 'pending', attempt: 1, progress_fraction: 0, progress_label: 'Pending', bytes_done: 0, bytes_total: 0, error_message: null },
          ],
          nodes: {
            master1: { role: 'master', status: 'prepared' },
            worker1: { role: 'worker', status: 'prepared' },
          },
        };

        // 1. Verify model properties
        assert.equal(deployment.canRetryStage, true, 'canRetryStage is true');
        assert.equal(deployment.failedStage?.stage_name, 'link_profile');

        // 2. Render PrepareProfileStepper via SSR and verify retry button HTML
        const stepperHtml = render(PrepareProfileStepper, {
          props: {
            deployment,
            presenter: run,
            onReview: () => {},
          },
        }).body;

        // Verify stage status and error alert
        assert.match(stepperHtml, /Ring-TCP Link Profiling/);
        assert.match(stepperHtml, /Socket timeout on 192\.168\.1\.11:50052/);
        assert.match(stepperHtml, /Retry stage/, 'Stage item contains Retry stage button');
        assert.match(stepperHtml, /Retry failed stage \(link_profile\)/, 'Stepper controls contain retry button');

        // 3. Trigger stage retry
        const retryResult = await deployment.retryStage('link_profile');
        assert.ok(retryResult, 'Retry API returned response');

        // 4. Verify stage preservation in activeRun
        const stages = deployment.activeRun.stages;
        assert.equal(stages[0].stage_name, 'materialize');
        assert.equal(stages[0].status, 'completed', 'materialize stage preserved');

        assert.equal(stages[1].stage_name, 'asset_sync');
        assert.equal(stages[1].status, 'completed', 'asset_sync stage preserved');

        assert.equal(stages[2].stage_name, 'node_profile');
        assert.equal(stages[2].status, 'completed', 'node_profile stage preserved');

        assert.equal(stages[3].stage_name, 'link_profile');
        assert.equal(stages[3].status, 'running', 'link_profile transitioned to running');
        assert.equal(stages[3].error_message, null, 'link_profile error cleared');

        assert.equal(stages[4].stage_name, 'plan_qualification');
        assert.equal(stages[4].status, 'pending', 'plan_qualification remains pending');
      } finally {
        deployment.stop();
      }
    });

    // -------------------------------------------------------------------------
    // Test 3: Single-node vs Multi-node cluster configurations
    // -------------------------------------------------------------------------
    await suite.test('3. Single-node skips link profiling gracefully while multi-node displays full link matrix', async (t) => {
      await t.test('3.1 Single-node configuration renders skip banner and Single-node readiness', () => {
        const { deployment, run } = createMockFixture({ singleNode: true });
        try {
          // Single-node LinkMatrixView test
          const matrixHtml = render(LinkMatrixView, {
            props: {
              linkMatrix: [],
              nodes: { master1: { role: 'master' } },
            },
          }).body;
          assert.match(matrixHtml, /Single-node deployment/);
          assert.match(matrixHtml, /link profiling is skipped for single-node/);
          assert.doesNotMatch(matrixHtml, /Source \\ Target/);

          // Single-node ReadinessCard test
          const readinessHtml = render(DeploymentReadinessCard, {
            props: { deployment, presenter: run },
          }).body;
          assert.match(readinessHtml, /Single-node \(skipped\)/);
        } finally {
          deployment.stop();
        }
      });

      await t.test('3.2 Multi-node configuration with plan renders full directed link matrix and Measured readiness', async () => {
        const { deployment, run } = createMockFixture({ singleNode: false });
        try {
          await deployment.inspect();
          deployment.coverage = {
            run_id: 'run-stress-1',
            coverage_status: 'profiled',
            node_profiles: {
              master1: { gpu_layer_ms: 2.1 },
              worker1: { gpu_layer_ms: 2.3 },
            },
            link_matrix: [
              { source: 'master1', target: 'worker1', latency_ms: 0.35, bandwidth_bytes_s: 1.25e9 },
              { source: 'worker1', target: 'master1', latency_ms: 0.42, bandwidth_bytes_s: 1.18e9 },
            ],
          };

          const matrixHtml = render(LinkMatrixView, {
            props: {
              linkMatrix: deployment.coverage.link_matrix,
              nodes: {
                master1: { role: 'master' },
                worker1: { role: 'worker' },
              },
            },
          }).body;

          assert.match(matrixHtml, /Source \\ Target/);
          assert.match(matrixHtml, /master1/);
          assert.match(matrixHtml, /worker1/);
          assert.match(matrixHtml, /Local Bus/);
          assert.match(matrixHtml, /0\.35 ms/);
          assert.match(matrixHtml, /1\.25 GB\/s/);
          assert.match(matrixHtml, /0\.42 ms/);
          assert.match(matrixHtml, /1\.18 GB\/s/);

          const readinessHtml = render(DeploymentReadinessCard, {
            props: { deployment, presenter: run },
          }).body;
          assert.match(readinessHtml, /Measured \(Ring-TCP verified\)/);
        } finally {
          deployment.stop();
        }
      });

      await t.test('3.3 DeploymentReadinessCard accurately reflects link matrix when plan is null on multi-node cluster', () => {
        const { deployment, run } = createMockFixture({ singleNode: false });
        try {
          // deployment has NO plan yet (plan is null), but cluster has 2 nodes and coverage has link_matrix
          assert.equal(deployment.latestPlan, null);
          deployment.coverage = {
            run_id: 'run-stress-1',
            coverage_status: 'profiled',
            node_profiles: { master1: {}, worker1: {} },
            link_matrix: [{ source: 'master1', target: 'worker1', latency_ms: 0.35, bandwidth_bytes_s: 1.25e9 }],
          };

          const readinessHtml = render(DeploymentReadinessCard, {
            props: { deployment, presenter: run },
          }).body;

          const falselyReportsSingleNode = readinessHtml.includes('Single-node (skipped)');
          assert.equal(
            falselyReportsSingleNode,
            false,
            'DeploymentReadinessCard must NOT evaluate null plan ranks as Single-node (skipped) on multi-node clusters'
          );
          assert.match(readinessHtml, /Measured \(Ring-TCP verified\)/);
        } finally {
          deployment.stop();
        }
      });

      await t.test('3.4 DeploymentReadinessCard displays Link profiling failed and error-text on multi-node cluster', () => {
        const { deployment, run } = createMockFixture({ singleNode: false });
        try {
          deployment.activeRun = {
            run_id: 'run-stress-1',
            job_id: 'run-stress-1',
            status: 'failed',
            phase: 'failed',
            current_stage: 'link_profile',
            stages: [
              { stage_name: 'materialize', stage_order: 1, status: 'completed', attempt: 1, progress_fraction: 1, progress_label: 'Done', bytes_done: 0, bytes_total: 0, error_message: null },
              { stage_name: 'asset_sync', stage_order: 2, status: 'completed', attempt: 1, progress_fraction: 1, progress_label: 'Done', bytes_done: 100, bytes_total: 100, error_message: null },
              { stage_name: 'node_profile', stage_order: 3, status: 'completed', attempt: 1, progress_fraction: 1, progress_label: 'Done', bytes_done: 0, bytes_total: 0, error_message: null },
              { stage_name: 'link_profile', stage_order: 4, status: 'failed', attempt: 1, progress_fraction: 0.3, progress_label: 'Timed out', bytes_done: 0, bytes_total: 0, error_message: 'Socket timeout 50052' },
              { stage_name: 'plan_qualification', stage_order: 5, status: 'pending', attempt: 1, progress_fraction: 0, progress_label: 'Pending', bytes_done: 0, bytes_total: 0, error_message: null },
            ],
            nodes: { master1: {}, worker1: {} },
          };

          const readinessHtml = render(DeploymentReadinessCard, {
            props: { deployment, presenter: run },
          }).body;

          assert.equal(readinessHtml.includes('Single-node (skipped)'), false);
          assert.match(readinessHtml, /Link profiling failed/);
          assert.match(readinessHtml, /error-text/);
        } finally {
          deployment.stop();
        }
      });
    });

    // -------------------------------------------------------------------------
    // Test 4: SSR and Client Hydration of NextDeploymentView and PrepareProfileStepper
    // -------------------------------------------------------------------------
    await suite.test('4. Server-Side Rendering (SSR) and Client Hydration', async (t) => {
      await t.test('4.1 NextDeploymentView SSR renders across diverse lifecycle states', async () => {
        const { deployment, run, setContext } = createMockFixture();
        try {
          // State A: Initial clean state
          await deployment.inspect();
          const htmlA = render(NextDeploymentView, {
            props: { deployment, presenter: run, onReview: () => {} },
          }).body;
          assert.match(htmlA, /Next deployment/);
          assert.match(htmlA, /Qwen3\.8-27B\.gguf/);
          assert.match(htmlA, /Prepare &amp; Profile pipeline/);
          assert.match(htmlA, /Memory plan/);

          // State B: Running active run
          deployment.activeRun = {
            run_id: 'run-active',
            job_id: 'run-active',
            status: 'running',
            phase: 'preparing',
            current_stage: 'asset_sync',
            stages: [
              { stage_name: 'materialize', stage_order: 1, status: 'completed', attempt: 1, progress_fraction: 1, progress_label: 'Done', bytes_done: 0, bytes_total: 0, error_message: null },
              { stage_name: 'asset_sync', stage_order: 2, status: 'running', attempt: 1, progress_fraction: 0.4, progress_label: 'Syncing', bytes_done: 400, bytes_total: 1000, error_message: null },
              { stage_name: 'node_profile', stage_order: 3, status: 'pending', attempt: 1, progress_fraction: 0, progress_label: 'Pending', bytes_done: 0, bytes_total: 0, error_message: null },
              { stage_name: 'link_profile', stage_order: 4, status: 'pending', attempt: 1, progress_fraction: 0, progress_label: 'Pending', bytes_done: 0, bytes_total: 0, error_message: null },
              { stage_name: 'plan_qualification', stage_order: 5, status: 'pending', attempt: 1, progress_fraction: 0, progress_label: 'Pending', bytes_done: 0, bytes_total: 0, error_message: null },
            ],
            nodes: { master1: { role: 'master', status: 'preparing' } },
          };
          const htmlB = render(NextDeploymentView, {
            props: { deployment, presenter: run, onReview: () => {} },
          }).body;
          assert.match(htmlB, /Cancel active run/);
          assert.match(htmlB, /running/);

          // State C: Stale draft notice
          setContext(16384);
          const htmlC = render(NextDeploymentView, {
            props: { deployment, presenter: run, onReview: () => {} },
          }).body;
          assert.match(htmlC, /Settings changed\. Prepare and Review will calculate/);
        } finally {
          deployment.stop();
        }
      });

      await t.test('4.2 Client Hydration and Live Interaction in Chrome browser via CDP', { skip: !chrome && 'Chrome required' }, async () => {
        const profile = mkdtempSync(join(tmpdir(), 'kllm-challenger-m3-'));
        let browserProcess, httpServer, ws;
        let retried = false;

        try {
          httpServer = createHttpServer((req, res) => {
            void (async () => {
              const parsed = new URL(req.url, 'http://127.0.0.1');
              const pathname = parsed.pathname;

              if (!pathname.startsWith('/v2/')) {
                server.middlewares(req, res, () => res.writeHead(404).end());
                return;
              }

              const statusData = {
                role: 'master',
                node_id: 'master1',
                inference_up: false,
                process_pid: null,
                process_role: null,
                model_name: null,
                members: {
                  master1: { host: '192.168.1.10', online: true, role: 'master', devices: [{ index: 0, name: 'RTX 4090' }] },
                  worker1: { host: '192.168.1.11', online: true, role: 'worker', devices: [{ index: 0, name: 'RTX 3090' }] },
                },
                runtimes: {
                  target: { version: { runtime_id: runtimeId, engine: 'primacpp' } },
                  nodes: [{ node_id: 'master1', phase: 'ready', prepared: { runtime_id: runtimeId } }],
                },
                software: { git_head: 'challenger', llama_release: 'v0.5.0' },
              };

              let result = {};
              if (pathname === '/v2/events') {
                result = {
                  version: 1,
                  transport: 'websocket',
                  epoch: '11111111-1111-1111-1111-111111111111',
                  server_time: new Date().toISOString(),
                  areas: {
                    status: { revision: 1, sampled_at: new Date().toISOString(), availability: 'available', coverage: 'cluster', error: null, data: statusData },
                    usage: { revision: 1, sampled_at: new Date().toISOString(), availability: 'available', coverage: 'cluster', error: null, data: { nodes: {}, relationships: [], recording: { recording: 'stopped' } } },
                    load: { revision: 1, availability: 'available', coverage: 'local', error: null, data: { current: null, last_terminal: null } },
                    download: { revision: 1, availability: 'available', coverage: 'local', error: null, data: { current: null, last_terminal: null } },
                  },
                  logs: null,
                };
              } else if (pathname === '/v2/node') {
                result = statusData;
              } else if (pathname === '/v2/models') {
                result = { data: [{ id: 'Qwen3.8-27B.gguf', size_bytes: 28e9 }] };
              } else if (pathname === '/v2/credentials/api-token') {
                result = { api_token: 'fixture-token' };
              } else if (pathname === '/v2/node/home') {
                result = { home: '/home', models: '/models', logs: '/logs' };
              } else if (pathname === '/v2/node/config') {
                result = { device: 'gpu', gpu_ids: [0], ram_percent: 80, vram_percent: 80 };
              } else if (pathname === '/v2/node/devices') {
                result = { devices: [], backend: 'vulkan', installed_backends: ['vulkan'], allowed_backends: ['vulkan'] };
              } else if (pathname === '/v2/node/runtimes') {
                result = {
                  versions: [{ version: { runtime_id: runtimeId, engine: 'primacpp', name: 'prima-1', repository: 'https://github.com/berkkarahan/prima.cpp', commit: 'c'.repeat(40) }, build: null }],
                  jobs: [],
                  selected: null,
                  target: { version: { runtime_id: runtimeId, engine: 'primacpp' }, revision: 1 },
                  nodes: [{ node_id: 'master1', phase: 'ready', prepared: { runtime_id: runtimeId } }],
                };
              } else if (pathname === '/v2/inference/capabilities') {
                result = { runtime_id: runtimeId, engine: 'primacpp', deployment_review_version: 1, parameters: [], threads_max: 8 };
              } else if (pathname.startsWith('/v2/inference/engine-model-settings/primacpp/')) {
                result = {
                  engine: 'primacpp',
                  model: 'Qwen3.8-27B.gguf',
                  revision: 0,
                  parameter_set: {},
                  requested_parameter_set: { context: 4096, cache_type_k: 'f16', cache_type_v: 'f16', flash_attention: 'off' },
                  legacy_parameter_set: null,
                };
              } else if (pathname.startsWith('/v2/inference/plans') || pathname.endsWith('/prima-plan')) {
                result = {
                  runtime_id: runtimeId,
                  deployment_review_version: 1,
                  plan_digest: planDigest,
                  inference_supported: true,
                  fit_status: 'fits',
                  prima_topology: { ranks: [{ node_id: 'master1' }, { node_id: 'worker1' }] },
                  estimated_bytes: 38 * (1024 ** 3),
                  budget_bytes: 48 * (1024 ** 3),
                  kv_estimate_bytes: 6 * (1024 ** 3),
                  weights_estimate_bytes: 27 * (1024 ** 3),
                  checkpoint_estimate_bytes: 4 * (1024 ** 3),
                  cpu_alloc_bytes: 5 * (1024 ** 3),
                  ram_alloc_bytes: 5 * (1024 ** 3),
                  vram_alloc_bytes: 30 * (1024 ** 3),
                  node_ram_alloc_bytes: { master1: 5 * (1024 ** 3), worker1: 0 },
                  node_vram_alloc_bytes: { master1: 10 * (1024 ** 3), worker1: 20 * (1024 ** 3) },
                  profiling: {
                    status: 'profiled',
                    node_profiles: { master1: { gpu_layer_ms: 2.1 } },
                    link_matrix: [{ source: 'master1', target: 'worker1', latency_ms: 0.35, bandwidth_bytes_s: 1.2e9 }],
                  },
                  model_support: { supported: true },
                  resolved_launch_settings: { context: 4096, batch_size: 512, micro_batch: 128, parallel_slots: 1, cache_type_k: 'f16', cache_type_v: 'f16' },
                };
              } else if (pathname.includes('/v2/cluster/prima-preparations')) {
                if (pathname.includes('/retry')) {
                  retried = true;
                  result = { run_id: 'run-chrome-1', status: 'running', retrying_stage: 'link_profile' };
                } else if (pathname.includes('/coverage')) {
                  result = {
                    run_id: 'run-chrome-1',
                    coverage_status: 'profiled',
                    node_profiles: { master1: { gpu_layer_ms: 2.1 } },
                    link_matrix: [{ source: 'master1', target: 'worker1', latency_ms: 0.35, bandwidth_bytes_s: 1.2e9 }],
                  };
                } else {
                  result = {
                    run_id: 'run-chrome-1',
                    status: retried ? 'running' : 'failed',
                    phase: retried ? 'preparing' : 'failed',
                    current_stage: 'link_profile',
                    stages: [
                      { stage_name: 'materialize', stage_order: 1, status: 'completed', attempt: 1, progress_fraction: 1, progress_label: 'Done', bytes_done: 0, bytes_total: 0, error_message: null },
                      { stage_name: 'asset_sync', stage_order: 2, status: 'completed', attempt: 1, progress_fraction: 1, progress_label: 'Done', bytes_done: 100, bytes_total: 100, error_message: null },
                      { stage_name: 'node_profile', stage_order: 3, status: 'completed', attempt: 1, progress_fraction: 1, progress_label: 'Done', bytes_done: 0, bytes_total: 0, error_message: null },
                      { stage_name: 'link_profile', stage_order: 4, status: retried ? 'running' : 'failed', attempt: retried ? 2 : 1, progress_fraction: retried ? 0.05 : 0.4, progress_label: retried ? 'Retrying stage...' : 'Timeout', error_message: retried ? null : 'Timed out 50052' },
                      { stage_name: 'plan_qualification', stage_order: 5, status: 'pending', attempt: 1, progress_fraction: 0, progress_label: 'Pending', bytes_done: 0, bytes_total: 0, error_message: null },
                    ],
                    nodes: { master1: { role: 'master', status: 'prepared' }, worker1: { role: 'worker', status: 'prepared' } },
                  };
                }
              }

              res.writeHead(200, { 'Content-Type': 'application/json' }).end(JSON.stringify(result));
            })().catch((err) => {
              console.error('CDP SERVER ERROR:', req.method, req.url, err);
              res.writeHead(500).end(String(err));
            });
          });

          await new Promise((resolve) => httpServer.listen(0, '127.0.0.1', resolve));
          const base = `http://127.0.0.1:${httpServer.address().port}`;

          browserProcess = spawn(chrome, [
            '--headless=new',
            '--disable-gpu',
            '--no-first-run',
            '--remote-allow-origins=*',
            '--remote-debugging-port=0',
            `--user-data-dir=${profile}`,
            'about:blank',
          ], { stdio: 'ignore' });

          const portfile = join(profile, 'DevToolsActivePort');
          for (let i = 0; i < 100 && !existsSync(portfile); i++) {
            await new Promise((resolve) => setTimeout(resolve, 100));
          }
          assert.ok(existsSync(portfile), 'Chrome DevTools started');

          const devToolsPort = Number(readFileSync(portfile, 'utf8').split('\n')[0]);
          const pages = await (await fetch(`http://127.0.0.1:${devToolsPort}/json/list`)).json();
          ws = new WebSocket(pages.find((p) => p.type === 'page').webSocketDebuggerUrl);
          await new Promise((resolve, reject) => { ws.onopen = resolve; ws.onerror = reject; });

          let id = 0;
          const pending = new Map();
          ws.onmessage = ({ data }) => {
            const msg = JSON.parse(data);
            if (msg.method === 'Runtime.exceptionThrown') {
              console.error('BROWSER EXCEPTION:', JSON.stringify(msg.params.exceptionDetails));
            }
            if (msg.method === 'Runtime.consoleAPICalled') {
              console.error('CONSOLE CALL:', msg.params.type, ...(msg.params.args || []).map((a) => a.value || a.description));
            }
            pending.get(msg.id)?.(msg);
          };

          async function cdpCall(method, params = {}) {
            const requestId = ++id;
            return new Promise((resolve, reject) => {
              const timer = setTimeout(() => { pending.delete(requestId); reject(new Error(`Timeout: ${method}`)); }, 10000);
              pending.set(requestId, (msg) => { clearTimeout(timer); pending.delete(requestId); resolve(msg.result); });
              ws.send(JSON.stringify({ id: requestId, method, params }));
            });
          }

          async function evaluate(expression) {
            const res = await cdpCall('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true });
            assert.equal(res.exceptionDetails, undefined, JSON.stringify(res.exceptionDetails));
            return res.result?.value;
          }
          async function waitFor(expression) {
            for (let i = 0; i < 60; i++) {
              if (await evaluate(`Boolean(${expression})`)) return;
              await new Promise((resolve) => setTimeout(resolve, 100));
            }
            const bodyHtml = await evaluate('document.body.innerHTML');
            assert.fail(`Timeout waiting for: ${expression}\nPage HTML:\n${bodyHtml}`);
          }

          await cdpCall('Runtime.enable');
          await cdpCall('Console.enable');
          await cdpCall('Page.navigate', { url: base + '/run' });
          await waitFor("document.querySelector('#run-placement-mode')");

          // Configure appStore with Prima engine and active failed deployment run
          await evaluate(`(async () => {
            const { createApp } = await import('/src/lib/app.svelte.ts');
            const appStore = createApp();
            appStore.stop();

            const primaVersion = {
              runtime_id: '${runtimeId}',
              name: 'Prima.cpp 0.0.1',
              engine: 'primacpp',
              repository: 'https://github.com/berkkarahan/prima.cpp.git',
              ref: '0.0.1',
              ref_type: 'tag',
              commit: '1'.repeat(40),
            };

            appStore.cluster.status = {
              role: 'master',
              node_id: 'master1',
              engine: 'primacpp',
              inference_up: false,
              process_pid: null,
              process_role: null,
              model_name: null,
              members: {
                master1: { host: '192.168.1.10', online: true, role: 'master', devices: [{ index: 0, name: 'RTX 4090' }] },
                worker1: { host: '192.168.1.11', online: true, role: 'worker', devices: [{ index: 0, name: 'RTX 3090' }] },
              },
              runtimes: {
                versions: [{ version: primaVersion, build: { runtime_id: primaVersion.runtime_id, build_id: 'b1', backend: 'vulkan', commit: primaVersion.commit } }],
                jobs: [],
                selected: primaVersion.runtime_id,
                target: { version: primaVersion, revision: 1, cluster_binding_id: 'c1', master_node_id: 'master1' },
                running: null,
                nodes: [{ node_id: 'master1', phase: 'ready', prepared: { runtime_id: '${runtimeId}' } }],
              },
              software: { git_head: 'challenger', llama_release: 'v0.5.0' },
            };

            appStore.models.models = [{ id: 'Qwen3.8-27B.gguf', size_bytes: 28e9 }];
            appStore.models.selectedModel = 'Qwen3.8-27B.gguf';
            appStore.live.connected = true;
            appStore.live.deliveryHealth = 'live';
            appStore.live.areaHealth = { status: { availability: 'available', coverage: 'cluster', error: null } };
            await appStore.run.setSettingsScope('primacpp', '${runtimeId}');

            // Set up active deployment run with failed link_profile stage
            const deployment = appStore.runtimes.deployment;
            deployment.activeRun = {
              run_id: 'run-chrome-1',
              job_id: 'run-chrome-1',
              status: 'failed',
              phase: 'failed',
              current_stage: 'link_profile',
              stages: [
                { stage_name: 'materialize', stage_order: 1, status: 'completed', attempt: 1, progress_fraction: 1, progress_label: 'Done', bytes_done: 0, bytes_total: 0, error_message: null },
                { stage_name: 'asset_sync', stage_order: 2, status: 'completed', attempt: 1, progress_fraction: 1, progress_label: 'Done', bytes_done: 100, bytes_total: 100, error_message: null },
                { stage_name: 'node_profile', stage_order: 3, status: 'completed', attempt: 1, progress_fraction: 1, progress_label: 'Done', bytes_done: 0, bytes_total: 0, error_message: null },
                { stage_name: 'link_profile', stage_order: 4, status: 'failed', attempt: 1, progress_fraction: 0.4, progress_label: 'Timeout', bytes_done: 0, bytes_total: 0, error_message: 'Timed out 50052' },
                { stage_name: 'plan_qualification', stage_order: 5, status: 'pending', attempt: 1, progress_fraction: 0, progress_label: 'Pending', bytes_done: 0, bytes_total: 0, error_message: null },
              ],
              nodes: { master1: { role: 'master', status: 'prepared' }, worker1: { role: 'worker', status: 'prepared' } },
            };
            appStore.run.resetRunSettings();
          })()`);

          // Wait for Prima deployment page tabs to hydrate
          await waitFor("document.querySelector('.prima-deploy-page')");
          await waitFor("document.querySelector('#prima-tab-deployment')");

          // Switch to "Next deployment" tab which contains PrimaDeploymentSummary and NextDeploymentView
          await evaluate("document.querySelector('#prima-tab-deployment').click()");

          // Verify client hydration rendered the stepper and readiness card
          await waitFor("document.querySelector('.stepper-container')");
          await waitFor("document.querySelector('.readiness-card')");

          // Verify hydrated elements exist in browser DOM
          const hydratedTitle = await evaluate("document.querySelector('.stepper-header h3')?.textContent");
          assert.equal(hydratedTitle, 'Prepare & Profile pipeline');

          const failureBadge = await evaluate("document.querySelector('.badge-danger')?.textContent");
          assert.match(failureBadge, /Failed/);

          // Verify all 5 stages rendered in DOM
          const stageCount = await evaluate("document.querySelectorAll('.stage-item').length");
          assert.equal(stageCount, 5, 'Rendered all 5 pipeline stages');

          // Test live interaction: click stage toggle button to collapse/expand
          const initialExpanded = await evaluate("document.querySelectorAll('.stage-toggle-btn')[3]?.getAttribute('aria-expanded')");
          await evaluate("document.querySelectorAll('.stage-toggle-btn')[3]?.click()");
          const toggledExpanded = await evaluate("document.querySelectorAll('.stage-toggle-btn')[3]?.getAttribute('aria-expanded')");
          assert.notEqual(initialExpanded, toggledExpanded, 'Clicking stage header toggled aria-expanded');

          // Verify Retry button exists in browser DOM
          const retryBtnText = await evaluate("document.querySelector('.btn-retry')?.textContent");
          assert.match(retryBtnText, /Retry stage/);

          // Click Retry button in browser
          await evaluate("document.querySelector('.btn-retry')?.click()");

          // Verify stage reflects retrying state in live DOM
          await waitFor("document.querySelectorAll('.stage-subtitle')[3]?.textContent?.includes('Retrying')");
          const retryingSubtitle = await evaluate("document.querySelectorAll('.stage-subtitle')[3]?.textContent");
          assert.match(retryingSubtitle, /Retrying/);
        } finally {
          ws?.close();
          browserProcess?.kill();
          await new Promise((r) => httpServer?.close(r));
          await removeBrowserProfile(profile);
        }
      });
    });
  } finally {
    await server.close();
    process.chdir(cwd);
  }
});
