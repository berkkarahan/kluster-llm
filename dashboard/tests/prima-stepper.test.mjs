import assert from 'node:assert/strict';
import test from 'node:test';
import { createServer } from 'vite';

test('Prima stepper manages 5-stage pipeline, stage retry and active run cancellation', async (t) => {
  const server = await createServer({
    server: { middlewareMode: true, hmr: false, ws: false, watch: null },
    appType: 'custom',
  });

  try {
    const { PrimaDeployment } = await server.ssrLoadModule('/src/lib/modules/run/prima-deployment.svelte.ts');
    const runtimeId = 'a'.repeat(64);
    const runId = 'test-run-1234';

    function fixture() {
      const requests = [];
      let currentRunState = {
        run_id: runId,
        job_id: runId,
        status: 'failed',
        phase: 'failed',
        current_stage: 'link_profile',
        stages: [
          { stage_name: 'materialize', stage_order: 1, status: 'completed', attempt: 1, progress_fraction: 1, progress_label: 'Done', bytes_done: 0, bytes_total: 0, error_message: null },
          { stage_name: 'asset_sync', stage_order: 2, status: 'completed', attempt: 1, progress_fraction: 1, progress_label: 'Done', bytes_done: 1000, bytes_total: 1000, error_message: null },
          { stage_name: 'node_profile', stage_order: 3, status: 'completed', attempt: 1, progress_fraction: 1, progress_label: 'Done', bytes_done: 0, bytes_total: 0, error_message: null },
          { stage_name: 'link_profile', stage_order: 4, status: 'failed', attempt: 1, progress_fraction: 0.5, progress_label: 'Timed out', bytes_done: 0, bytes_total: 0, error_message: 'Socket timeout on 192.168.1.11:50052' },
          { stage_name: 'plan_qualification', stage_order: 5, status: 'pending', attempt: 1, progress_fraction: 0, progress_label: 'Pending', bytes_done: 0, bytes_total: 0, error_message: null },
        ],
        nodes: {
          master: { role: 'master', status: 'prepared', phase: 'prepared' },
          worker1: { role: 'worker', status: 'prepared', phase: 'prepared' },
        },
      };

      const run = {
        settingsScope: 'primacpp',
        settingsRuntimeId: runtimeId,
        selectedModel: 'test-model.gguf',
        selectionGeneration: 1,
        live: { connected: true },
        localDraftErrors: {},
        stopParseError: '',
        busy: '',
        loadProgress: null,
        status: {
          role: 'master',
          node_id: 'master',
          members: {
            master: { host: '192.168.1.10', online: true, role: 'master' },
            worker1: { host: '192.168.1.11', online: true, role: 'worker' },
          },
          runtimes: {
            target: { version: { runtime_id: runtimeId, engine: 'primacpp' } },
            nodes: [{ node_id: 'master', phase: 'ready', prepared: { runtime_id: runtimeId } }],
          },
        },
        networkPlanPayload: () => ({ model: 'test-model.gguf', context: 4096 }),
        validateRunSettings: () => true,
        http: {
          loadToken: async () => {},
          command: async () => ({}),
          networkRequest: async (url, method, body) => {
            requests.push({ url, method, body });
            if (url.includes('/retry')) {
              const targetStage = body?.stage_name || 'link_profile';
              currentRunState = {
                ...currentRunState,
                status: 'running',
                phase: 'preparing',
                current_stage: targetStage,
                stages: currentRunState.stages.map(s => (
                  s.stage_name === targetStage
                    ? { ...s, status: 'running', error_message: null, progress_label: 'Retrying...' }
                    : s
                )),
              };
              return {
                run_id: runId,
                status: 'running',
                retrying_stage: targetStage,
                preserved_stages: ['materialize', 'asset_sync', 'node_profile'],
              };
            }
            if (url.includes('/cancel')) {
              currentRunState = { ...currentRunState, status: 'cancelled', phase: 'cancelled' };
              return { run_id: runId, status: 'cancelled' };
            }
            if (url.includes('/coverage')) {
              return {
                run_id: runId,
                coverage_status: 'profiled',
                node_profiles: {
                  master: { gpu_layer_ms: 1.8, cpu_layer_ms: 12.0, prompt_ms_per_token: 0.8 },
                  worker1: { gpu_layer_ms: 2.1, cpu_layer_ms: 14.5, prompt_ms_per_token: 0.9 },
                },
                link_matrix: [
                  { source: 'master', target: 'worker1', latency_ms: 0.45, bandwidth_bytes_s: 1.2e9 },
                  { source: 'worker1', target: 'master', latency_ms: 0.46, bandwidth_bytes_s: 1.18e9 },
                ],
              };
            }
            if (url.includes('/logs')) {
              return {
                run_id: runId,
                logs: [
                  { timestamp: 1700000000, level: 'INFO', stage_name: 'asset_sync', message: 'Chunk transfer verified' },
                  { timestamp: 1700000005, level: 'ERROR', stage_name: 'link_profile', message: 'Socket timeout on 192.168.1.11:50052' },
                ],
                text: 'Logs summary',
              };
            }
            if (url.includes(`/prima-preparations/${runId}`)) {
              return JSON.parse(JSON.stringify(currentRunState));
            }
            return {};
          },
        },
      };

      const runtimes = {
        primaPlan: async () => ({
          runtime_id: runtimeId,
          plan_digest: 'digest-1',
          deployment_review_version: 1,
          inference_supported: true,
          fit_status: 'fits',
          prima_topology: { ranks: [{ node_id: 'master' }, { node_id: 'worker1' }] },
        }),
        preparation: {
          job: null,
          active: false,
          observe: () => {},
          acquire: () => () => {},
        },
      };

      const deployment = new PrimaDeployment(run, runtimes);
      deployment.runtimeId = runtimeId;
      return { run, deployment, requests };
    }

    await t.test('activeRun tracks 5 stages and identifies failedStage for retry', async () => {
      const { deployment } = fixture();
      try {
        await deployment.fetchRun(runId);
        assert.ok(deployment.activeRun);
        assert.equal(deployment.activeRun.stages.length, 5);
        assert.equal(deployment.failedStage?.stage_name, 'link_profile');
        assert.equal(deployment.canRetryStage, true);
      } finally {
        deployment.stop();
      }
    });

    await t.test('retryStage dispatches POST with stage query and preserves earlier completed stages', async () => {
      const { deployment, requests } = fixture();
      try {
        await deployment.fetchRun(runId);
        assert.equal(deployment.canRetryStage, true);
        const retryResult = await deployment.retryStage('link_profile');
        assert.ok(retryResult);

        // Verify request was dispatched with stage parameter
        const retryReq = requests.find(r => r.url.includes('/retry'));
        assert.ok(retryReq);
        assert.match(retryReq.url, /stage=link_profile/);
        assert.equal(retryReq.method, 'POST');
        assert.equal(retryReq.body?.stage_name, 'link_profile');

        // Verify that completed earlier stages remain completed in state
        const stages = deployment.activeRun.stages;
        assert.equal(stages.find(s => s.stage_name === 'materialize').status, 'completed');
        assert.equal(stages.find(s => s.stage_name === 'asset_sync').status, 'completed');
        assert.equal(stages.find(s => s.stage_name === 'node_profile').status, 'completed');
        assert.equal(stages.find(s => s.stage_name === 'link_profile').status, 'running');
      } finally {
        deployment.stop();
      }
    });

    await t.test('cancelRun dispatches cancellation endpoint', async () => {
      const { deployment, requests } = fixture();
      try {
        await deployment.fetchRun(runId);
        await deployment.cancelRun(runId);
        const cancelReq = requests.find(r => r.url.includes('/cancel'));
        assert.ok(cancelReq);
        assert.equal(cancelReq.method, 'POST');
      } finally {
        deployment.stop();
      }
    });

    await t.test('fetchCoverage and fetchLogs populate measurement matrices and logs', async () => {
      const { deployment } = fixture();
      try {
        await deployment.fetchRun(runId);
        const coverage = await deployment.fetchCoverage(runId);
        assert.ok(coverage);
        assert.equal(coverage.coverage_status, 'profiled');
        assert.equal(coverage.link_matrix.length, 2);
        assert.equal(coverage.link_matrix[0].latency_ms, 0.45);

        const logs = await deployment.fetchLogs(runId);
        assert.equal(logs.length, 2);
        assert.equal(logs[1].level, 'ERROR');
      } finally {
        deployment.stop();
      }
    });
  } finally {
    await server.close();
  }
});
