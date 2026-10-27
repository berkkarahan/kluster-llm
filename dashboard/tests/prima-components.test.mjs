import assert from 'node:assert/strict';
import test from 'node:test';
import { fileURLToPath } from 'node:url';
import { createServer } from 'vite';

test('Prima pipeline components SSR render with complete telemetry data and edge states', async (t) => {
  process.chdir(fileURLToPath(new URL('..', import.meta.url)));
  const server = await createServer({
    server: { middlewareMode: true, hmr: false, ws: false, watch: null },
    appType: 'custom',
  });

  try {
    const { render } = await server.ssrLoadModule('svelte/server');
    const { default: AssetSyncNodeProgress } = await server.ssrLoadModule('/src/lib/modules/run/view/AssetSyncNodeProgress.svelte');
    const { default: NodeProfileResults } = await server.ssrLoadModule('/src/lib/modules/run/view/NodeProfileResults.svelte');
    const { default: LinkMatrixView } = await server.ssrLoadModule('/src/lib/modules/run/view/LinkMatrixView.svelte');
    const { default: StageDiagnosticsPanel } = await server.ssrLoadModule('/src/lib/modules/run/view/StageDiagnosticsPanel.svelte');
    const { default: StageItem } = await server.ssrLoadModule('/src/lib/modules/run/view/StageItem.svelte');
    const { default: DeploymentReadinessCard } = await server.ssrLoadModule('/src/lib/modules/run/view/DeploymentReadinessCard.svelte');

    await t.test('AssetSyncNodeProgress handles empty and active multi-node transfer data', () => {
      // Empty state
      const emptyHtml = render(AssetSyncNodeProgress, { props: { nodes: {}, bytesDone: 0, bytesTotal: 0 } }).body;
      assert.match(emptyHtml, /No cluster participants/);

      // Active multi-node transfer
      const activeHtml = render(AssetSyncNodeProgress, {
        props: {
          nodes: {
            master: { role: 'master', backend: 'vulkan', status: 'prepared', phase: 'prepared' },
            worker1: {
              role: 'worker',
              backend: 'cpu',
              status: 'running',
              phase: 'preparing',
              progress: { fraction: 0.65, bytes_done: 650000000, bytes_total: 1000000000, rate_bps: 45000000, eta_seconds: 8 },
            },
            worker2: {
              role: 'worker',
              backend: 'vulkan',
              status: 'failed',
              phase: 'failed',
              error: 'Connection reset by peer',
            },
          },
          bytesDone: 1650000000,
          bytesTotal: 2000000000,
        },
      }).body;
      assert.match(activeHtml, /master/);
      assert.match(activeHtml, /worker1/);
      assert.match(activeHtml, /Connection reset by peer/);
    });

    await t.test('NodeProfileResults handles unmeasured and populated hardware metrics', () => {
      // Unmeasured
      const unmeasuredHtml = render(NodeProfileResults, { props: { nodeProfiles: {}, nodes: {} } }).body;
      assert.match(unmeasuredHtml, /unmeasured/);

      // Populated profiles
      const populatedHtml = render(NodeProfileResults, {
        props: {
          nodeProfiles: {
            master: {
              device_name: 'NVIDIA RTX 4090',
              backend: 'vulkan',
              gpu_layer_ms: 1.82,
              cpu_layer_ms: 14.5,
              prompt_ms_per_token: 0.85,
              decode_ms_per_token: 18.2,
              usable_memory_bytes: 24576000000,
              disk_read_mbs: 3500,
            },
            worker1: {
              device_name: 'AMD Ryzen 9',
              backend: 'cpu',
              gpu_layer_ms: null,
              cpu_layer_ms: 19.8,
              prompt_ms_per_token: 2.1,
              decode_ms_per_token: 42.0,
              usable_memory_bytes: 68719476736,
              disk_read_mbs: 2100,
            },
          },
          nodes: {
            master: { role: 'master' },
            worker1: { role: 'worker' },
          },
        },
      }).body;
      assert.match(populatedHtml, /NVIDIA RTX 4090/);
      assert.match(populatedHtml, /1\.82 ms/);
      assert.match(populatedHtml, /3500 MB\/s/);
    });

    await t.test('LinkMatrixView handles single-node and multi-node pairwise latency/bandwidth grids', () => {
      // Empty / loading state: must render unmeasured notice, NOT single-node banner
      const emptyHtml = render(LinkMatrixView, { props: { linkMatrix: [], nodes: {} } }).body;
      assert.match(emptyHtml, /Ring-TCP link benchmarks pending or unmeasured/);
      assert.doesNotMatch(emptyHtml, /Single-node deployment/);

      // Single-node (skipped)
      const singleHtml = render(LinkMatrixView, { props: { linkMatrix: [], nodes: { master: { role: 'master' } } } }).body;
      assert.match(singleHtml, /Single-node deployment/);

      // Multi-node pairwise grid
      const multiHtml = render(LinkMatrixView, {
        props: {
          linkMatrix: [
            { source: 'master', target: 'worker1', latency_ms: 0.35, bandwidth_bytes_s: 1.25e9 },
            { source: 'worker1', target: 'master', latency_ms: 0.36, bandwidth_bytes_s: 1.22e9 },
          ],
          nodes: {
            master: { role: 'master' },
            worker1: { role: 'worker' },
          },
        },
      }).body;
      assert.match(multiHtml, /0\.35 ms/);
      assert.match(multiHtml, /1\.25 GB\/s/);
      assert.match(multiHtml, /Local Bus/);
    });

    await t.test('StageDiagnosticsPanel formats error alerts and structured logs with remediation hints', () => {
      // Offline nodes error with hint
      const alertHtml = render(StageDiagnosticsPanel, {
        props: {
          error: 'bad_request: required nodes are unavailable: a83ac5b6, master1',
          stageName: 'materialize',
          logs: [
            { timestamp: 1700000000, level: 'ERROR', stage_name: 'materialize', node_id: 'master1', message: 'Node resolution failed' },
          ],
        },
      }).body;
      assert.match(alertHtml, /required nodes are unavailable/);
      assert.match(alertHtml, /Cluster nodes rejected as unavailable/);
      assert.match(alertHtml, /Node resolution failed/);

      // Link profile timeout error with hint
      const timeoutHtml = render(StageDiagnosticsPanel, {
        props: {
          error: 'Ring-TCP benchmark timed out on 192.168.1.11:50052',
          stageName: 'link_profile',
          logs: [
            { timestamp: 1700000000, level: 'WARN', stage_name: 'link_profile', node_id: 'master1', message: 'Socket connection retried' },
            { timestamp: 1700000005, level: 'ERROR', stage_name: 'link_profile', node_id: 'worker1', message: 'Timeout reached after 15s' },
          ],
        },
      }).body;
      assert.match(timeoutHtml, /timed out/);
      assert.match(timeoutHtml, /firewall permits Ring-TCP traffic/);
    });

    await t.test('StageItem renders stage status badge, timer, and retry controls', () => {
      const stage = {
        stage_name: 'link_profile',
        stage_order: 4,
        status: 'failed',
        attempt: 2,
        progress_fraction: 0.5,
        progress_label: 'Timeout',
        bytes_done: 0,
        bytes_total: 0,
        error_message: 'Socket timeout',
        started_at: 100,
        finished_at: 105,
      };

      const html = render(StageItem, {
        props: {
          stage,
          index: 3,
          active: true,
          retrying: false,
          onRetry: () => {},
        },
      }).body;

      assert.match(html, /Ring-TCP Link Profiling/);
      assert.match(html, /Attempt 2/);
      assert.match(html, /5\.0s/);
      assert.match(html, /Failed/);
      assert.match(html, /Retry stage/);
      assert.match(html, /Socket timeout/);
    });

    await t.test('DeploymentReadinessCard SSR renders 5 pillars and handles error/warning/ready states', () => {
      const mockRun = {
        selectedModel: 'Qwen3.8-27B.gguf',
        context: 4096,
        parallelSlots: 1,
        cacheTypeK: 'f16',
        cacheTypeV: 'f16',
        status: {
          role: 'master',
          members: {
            master1: { host: '192.168.1.10', online: true },
            worker1: { host: '192.168.1.11', online: true },
          },
        },
      };

      const mockDeployment = {
        fresh: true,
        draftModified: false,
        inspecting: false,
        preview: null,
        placementError: null,
        latestPlan: null,
        activeRun: null,
        coverage: null,
        jobMatches: false,
        preparing: false,
        runtimes: { preparation: { job: null } },
      };

      // Unplanned multi-node render
      const htmlUnplanned = render(DeploymentReadinessCard, {
        props: { deployment: mockDeployment, presenter: mockRun },
      }).body;
      assert.match(htmlUnplanned, /Deployment readiness/);
      assert.match(htmlUnplanned, /Awaiting plan &amp; profiling|Awaiting plan & profiling/);

      // Failed stage render
      mockDeployment.activeRun = {
        stages: [{ stage_name: 'link_profile', status: 'failed' }],
      };
      const htmlFailed = render(DeploymentReadinessCard, {
        props: { deployment: mockDeployment, presenter: mockRun },
      }).body;
      assert.match(htmlFailed, /Link profiling failed/);
      assert.match(htmlFailed, /error-text/);
    });
  } finally {
    await server.close();
  }
});
