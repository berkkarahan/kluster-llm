import assert from 'node:assert/strict';
import test from 'node:test';
import { fileURLToPath } from 'node:url';
import { createServer } from 'vite';

test('Prima readiness sync decouples draft modifications from SQLite evidence and preserves coverage on transient errors', async (t) => {
  process.chdir(fileURLToPath(new URL('..', import.meta.url)));
  const server = await createServer({
    server: { middlewareMode: true, hmr: false, ws: false, watch: null },
    appType: 'custom',
  });

  try {
    const { PrimaDeployment } = await server.ssrLoadModule('/src/lib/modules/run/prima-deployment.svelte.ts');
    const { render } = await server.ssrLoadModule('svelte/server');
    const { default: DeploymentReadinessCard } = await server.ssrLoadModule('/src/lib/modules/run/view/DeploymentReadinessCard.svelte');
    const { default: NextDeploymentView } = await server.ssrLoadModule('/src/lib/modules/run/view/NextDeploymentView.svelte');
    const runtimeId = 'b'.repeat(64);
    const planDigest = 'c'.repeat(64);

    function fixture() {
      let context = 4096;
      let shouldFailInspect = false;
      let inspectErrorMessage = 'bad_request: required nodes are unavailable: a83ac5b6, master1';

      const run = {
        settingsScope: 'primacpp',
        settingsRuntimeId: runtimeId,
        selectedModel: 'Qwen3.8-27B.gguf',
        selectionGeneration: 1,
        live: { connected: true },
        localDraftErrors: {},
        stopParseError: '',
        busy: '',
        loadProgress: null,
        status: {
          role: 'master',
          node_id: 'master1',
          members: {
            master1: { host: '192.168.1.10', online: true, role: 'master' },
            a83ac5b6: { host: '192.168.1.11', online: true, role: 'worker' },
          },
          runtimes: {
            target: { version: { runtime_id: runtimeId, engine: 'primacpp' } },
            nodes: [{ node_id: 'master1', phase: 'ready', prepared: { runtime_id: runtimeId } }],
          },
        },
        networkPlanPayload: () => ({ model: 'Qwen3.8-27B.gguf', context }),
        validateRunSettings: () => true,
        planRunBlockedReason: () => null,
        refreshEstimate: () => {},
        effectiveConfig: () => ({ values: { context_per_request: context } }),
        http: {
          loadToken: async () => {},
          command: async () => ({}),
          networkRequest: async (url) => {
            if (url.includes('/coverage')) {
              return {
                run_id: 'run-1',
                coverage_status: 'profiled',
                node_profiles: {
                  master1: { gpu_layer_ms: 2.1, cpu_layer_ms: 15.0 },
                  a83ac5b6: { gpu_layer_ms: 2.3, cpu_layer_ms: 16.0 },
                },
                link_matrix: [
                  { source: 'master1', target: 'a83ac5b6', latency_ms: 0.38, bandwidth_bytes_s: 1.15e9 },
                  { source: 'a83ac5b6', target: 'master1', latency_ms: 0.39, bandwidth_bytes_s: 1.12e9 },
                ],
              };
            }
            return {};
          },
        },
      };

      const runtimes = {
        primaPlan: async () => {
          if (shouldFailInspect) {
            throw new Error(inspectErrorMessage);
          }
          return {
            runtime_id: runtimeId,
            plan_digest: planDigest,
            deployment_review_version: 1,
            inference_supported: true,
            fit_status: 'fits',
            prima_topology: { ranks: [{ node_id: 'master1' }, { node_id: 'a83ac5b6' }] },
            profiling: {
              status: 'profiled',
              node_profiles: {
                master1: { gpu_layer_ms: 2.1 },
                a83ac5b6: { gpu_layer_ms: 2.3 },
              },
            },
            model_support: { supported: true },
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
        deployment,
        editDraft: (newCtx = 8192) => {
          context = newCtx;
          deployment.changed();
        },
        setInspectFailure: (fail = true, msg) => {
          shouldFailInspect = fail;
          if (msg) inspectErrorMessage = msg;
        },
      };
    }

    await t.test('editing draft does not reset profile coverage or lastPlan evidence to unmeasured/null', async () => {
      const { deployment, editDraft } = fixture();
      try {
        await deployment.inspect();
        assert.equal(deployment.fresh, true);
        assert.equal(deployment.profileCoverageStatus, 'profiled');
        assert.ok(deployment.latestPlan);
        assert.equal(deployment.qualificationStatus, 'Qualified');

        // Edit the draft context
        editDraft(16384);
        assert.equal(deployment.fresh, false, 'fresh is false because draft changed');
        assert.equal(deployment.draftModified, true, 'draftModified is flagged');

        // Key requirement: Profile coverage must NOT collapse to Unmeasured
        assert.equal(deployment.profileCoverageStatus, 'profiled', 'Coverage remains profiled even with draft modified');
        assert.ok(deployment.latestPlan, 'latestPlan is not null');
        assert.equal(deployment.qualificationStatus, 'Qualified');
      } finally {
        deployment.stop();
      }
    });

    await t.test('transient inspect error retains lastPlan evidence and captures clear diagnostic', async () => {
      const { deployment, editDraft, setInspectFailure } = fixture();
      try {
        await deployment.inspect();
        assert.equal(deployment.fresh, true);

        // Simulate draft modification + node availability error on inspect
        editDraft(32768);
        setInspectFailure(true, 'bad_request: required nodes are unavailable: a83ac5b6, master1');

        const inspectResult = await deployment.inspect(true);
        assert.equal(inspectResult, null);
        assert.match(deployment.planningError, /required nodes are unavailable/);
        assert.match(deployment.error, /required nodes are unavailable/);

        // Crucial: lastPlan and coverage are preserved despite the transient inspection failure!
        assert.ok(deployment.lastPlan, 'lastPlan must not be cleared on transient inspect error');
        assert.ok(deployment.latestPlan, 'latestPlan remains available for UI inspection');
        assert.equal(deployment.profileCoverageStatus, 'profiled');
      } finally {
        deployment.stop();
      }
    });

    await t.test('DeploymentReadinessCard SSR renders linkStatus correctly across all 6 lifecycle states', async (st) => {
      // 1. Multi-node with failed link_profile stage and plan: null -> "Link profiling failed" (error badge)
      await st.test('1. Multi-node with failed link_profile stage and plan: null renders error badge', () => {
        const { deployment, run } = fixture();
        try {
          deployment.activeRun = {
            run_id: 'run-fail',
            status: 'failed',
            current_stage: 'link_profile',
            stages: [
              { stage_name: 'materialize', status: 'completed' },
              { stage_name: 'asset_sync', status: 'completed' },
              { stage_name: 'node_profile', status: 'completed' },
              { stage_name: 'link_profile', status: 'failed', error_message: 'Connection refused on port 50052' },
              { stage_name: 'plan_qualification', status: 'pending' },
            ],
            nodes: { master1: { role: 'master' }, a83ac5b6: { role: 'worker' } },
          };
          const html = render(DeploymentReadinessCard, { props: { deployment, presenter: run } }).body;
          assert.match(html, /Link profiling failed/);
          assert.match(html, /error-text/);
          assert.equal(html.includes('Single-node (skipped)'), false);
        } finally {
          deployment.stop();
        }
      });

      // 2. Multi-node with running link_profile stage -> "Profiling links…" (warning badge)
      await st.test('2. Multi-node with running link_profile stage renders warning badge', () => {
        const { deployment, run } = fixture();
        try {
          deployment.activeRun = {
            run_id: 'run-running',
            status: 'running',
            current_stage: 'link_profile',
            stages: [
              { stage_name: 'materialize', status: 'completed' },
              { stage_name: 'asset_sync', status: 'completed' },
              { stage_name: 'node_profile', status: 'completed' },
              { stage_name: 'link_profile', status: 'running', progress_label: 'Probing Ring-TCP' },
              { stage_name: 'plan_qualification', status: 'pending' },
            ],
            nodes: { master1: { role: 'master' }, a83ac5b6: { role: 'worker' } },
          };
          const html = render(DeploymentReadinessCard, { props: { deployment, presenter: run } }).body;
          assert.match(html, /Profiling links…/);
          assert.match(html, /warn-text/);
          assert.equal(html.includes('Single-node (skipped)'), false);
        } finally {
          deployment.stop();
        }
      });

      // 3. Multi-node with plan: null and no run -> "Awaiting plan & profiling"
      await st.test('3. Multi-node with plan: null and no run renders Awaiting plan & profiling', () => {
        const { deployment, run } = fixture();
        try {
          assert.equal(deployment.latestPlan, null);
          assert.equal(deployment.activeRun, null);
          const html = render(DeploymentReadinessCard, { props: { deployment, presenter: run } }).body;
          assert.match(html, /Awaiting plan &amp; profiling|Awaiting plan & profiling/);
          assert.equal(html.includes('Single-node (skipped)'), false);
          assert.equal(html.includes('Measured (Ring-TCP verified)'), false);
        } finally {
          deployment.stop();
        }
      });

      // 4. Multi-node with verified link_matrix -> "Measured (Ring-TCP verified)" (ready badge)
      await st.test('4. Multi-node with verified link_matrix renders ready badge', () => {
        const { deployment, run } = fixture();
        try {
          deployment.coverage = {
            run_id: 'run-cov',
            coverage_status: 'profiled',
            node_profiles: { master1: {}, a83ac5b6: {} },
            link_matrix: [
              { source: 'master1', target: 'a83ac5b6', latency_ms: 0.38, bandwidth_bytes_s: 1.15e9 },
              { source: 'a83ac5b6', target: 'master1', latency_ms: 0.39, bandwidth_bytes_s: 1.12e9 },
            ],
          };
          const html = render(DeploymentReadinessCard, { props: { deployment, presenter: run } }).body;
          assert.match(html, /Measured \(Ring-TCP verified\)/);
          assert.match(html, /<dd[^>]*class="[^"]*ready[^"]*"[^>]*>[^<]*Measured/);
          assert.equal(html.includes('Single-node (skipped)'), false);
        } finally {
          deployment.stop();
        }
      });

      // 5. Single-node cluster (explicit 1 rank or 1 member) -> "Single-node (skipped)" (ready badge)
      await st.test('5. Single-node cluster renders Single-node (skipped) with ready badge', () => {
        const { deployment, run } = fixture();
        try {
          run.status.members = { master1: { host: '192.168.1.10', online: true, role: 'master' } };
          const html = render(DeploymentReadinessCard, { props: { deployment, presenter: run } }).body;
          assert.match(html, /Single-node \(skipped\)/);
          assert.match(html, /<dd[^>]*class="[^"]*ready[^"]*"[^>]*>[^<]*Single-node/);
          assert.equal(html.includes('Link profiling failed'), false);
        } finally {
          deployment.stop();
        }
      });

      // 6. Multi-node with preparing: true -> "Awaiting link profiling"
      await st.test('6. Multi-node with preparing: true renders Awaiting link profiling', () => {
        const { deployment, run } = fixture();
        try {
          deployment.preparing = true;
          deployment.activeRun = {
            run_id: 'run-prep',
            status: 'running',
            current_stage: 'materialize',
            stages: [
              { stage_name: 'materialize', status: 'running' },
              { stage_name: 'asset_sync', status: 'pending' },
              { stage_name: 'node_profile', status: 'pending' },
              { stage_name: 'link_profile', status: 'pending' },
              { stage_name: 'plan_qualification', status: 'pending' },
            ],
            nodes: { master1: { role: 'master' }, a83ac5b6: { role: 'worker' } },
          };
          const html = render(DeploymentReadinessCard, { props: { deployment, presenter: run } }).body;
          assert.match(html, /Awaiting link profiling/);
          assert.equal(html.includes('Single-node (skipped)'), false);
        } finally {
          deployment.stop();
        }
      });
    });

    await t.test('NextDeploymentView and DeploymentReadinessCard SSR render cleanly without throwing', async () => {
      const { deployment, run } = fixture();
      try {
        await deployment.inspect();
        const html = render(NextDeploymentView, {
          props: {
            deployment,
            presenter: run,
            onReview: () => {},
          },
        }).body;
        assert.ok(html.length > 0, 'SSR output body must not be empty');
        assert.match(html, /Next deployment/);
        assert.match(html, /Deployment readiness/);
        assert.match(html, /Prepare &amp; Profile pipeline/);

        const cardHtml = render(DeploymentReadinessCard, {
          props: { deployment, presenter: run },
        }).body;
        assert.ok(cardHtml.includes('Deployment readiness'));
      } finally {
        deployment.stop();
      }
    });
  } finally {
    await server.close();
  }
});
