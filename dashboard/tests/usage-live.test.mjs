import assert from 'node:assert/strict';
import test from 'node:test';
import { fileURLToPath } from 'node:url';
import { createServer } from 'vite';

const GIB = 1024 ** 3;

function reading(over = {}) {
  return {
    lifetime_id: 'l1',
    pid: 42,
    process_role: 'worker',
    process_started_at: '2026-09-29T00:00:00Z',
    observed_at: '2026-09-29T00:00:02Z',
    process: { cpu_percent_machine: 3, rss_bytes: 2 * GIB },
    host: { cpu_percent: 25, used_bytes: 8 * GIB, total_bytes: 16 * GIB },
    devices: [],
    availability: {
      process_cpu: 'measured', process_rss: 'measured',
      host_cpu: 'measured', host_memory: 'measured'
    },
    ...over
  };
}

function node(id, over = {}) {
  return {
    node_id: id, host: id, role: 'worker', connected: true, freshness: 'fresh',
    reading: reading(), ...over
  };
}

test('node summaries expose host CPU, RAM and per-device readings before selection', async () => {
  const cwd = process.cwd();
  process.chdir(fileURLToPath(new URL('..', import.meta.url)));
  let server;
  try {
    server = await createServer({ server: { middlewareMode: true, hmr: false, watch: null }, appType: 'custom' });
    const { render } = await server.ssrLoadModule('svelte/server');
    const { default: NodeMap } = await server.ssrLoadModule('/src/lib/components/NodeMap.svelte');
    const { default: NodeUsageDetails } = await server.ssrLoadModule('/src/lib/components/NodeUsageDetails.svelte');

    const nodes = {
      // Multi-GPU union: device 0 has capacity but no usage sensor; device 1 has measured zero.
      master: node('master', {
        host: 'same-host', role: 'master', assignment: 'model.gguf',
        devices: [{ index: 0, name: 'Radeon RX', vram_bytes: 8 * GIB }, { index: 1, name: 'Radeon RX 2' }],
        reading: reading({
          host: { cpu_percent: 25, used_bytes: 8 * GIB, total_bytes: 16 * GIB },
          devices: [{ device_id: '0', scope: 'device', utilization_percent: null, used_bytes: null,
            total_bytes: 8 * GIB, temperature_c: null, power_w: null, unavailable_reason: 'device sensor unavailable' },
            { device_id: '1', scope: 'device', utilization_percent: 0, used_bytes: 0,
              total_bytes: 4 * GIB, temperature_c: null, power_w: null, unavailable_reason: 'temp unsupported' }]
        })
      }),
      // Duplicate host name, distinct identity; zero CPU; stale/disconnected; shared memory.
      w1: node('w1', {
        host: 'same-host', connected: false, freshness: 'stale', age_seconds: 11,
        unified_memory: true, devices: [{ index: 0, name: 'Apple M5 Pro', vram_bytes: 8 * GIB }],
        reading: reading({
          process: { cpu_percent_machine: 0, rss_bytes: 0 },
          host: { cpu_percent: 0, used_bytes: 0, total_bytes: 8 * GIB },
          devices: []
        })
      }),
      // Field-level errors with no reading.
      w2: node('w2', { connected: true, freshness: 'unavailable', backend: 'cpu', reading: null }),
      // Unknown backend with no devices.
      w3: node('w3', { backend: null, reading: reading({ host: { cpu_percent: null, used_bytes: null, total_bytes: 8 * GIB },
        availability: { process_cpu: 'measured', process_rss: 'measured', host_cpu: 'error', host_memory: 'initializing' } }) })
    };

    const map = render(NodeMap, { props: {
      nodes, relationships: [{ from_node_id: 'master', to_node_id: 'w1', kind: 'master-worker' }],
      selectedId: 'w1', onselect: () => {}
    } }).body;

    // Host metrics appear on every node before selection, with binary GiB and derived percentage.
    assert.match(map, /Host CPU/);
    assert.match(map, /Host RAM/);
    assert.match(map, /25%/);
    assert.match(map, /8\.0 GiB \/ 16\.0 GiB · 50%/);
    assert.match(map, /same-host/);

    // Process measurements stay out of the node summary (they belong to Details scope).
    assert.doesNotMatch(map, /2\.0 GiB/);

    // Unsupported usage reports capacity without suggesting a measured zero; measured zero remains numeric.
    assert.match(map, /GPU 0 · Radeon RX/);
    assert.match(map, /reported 8\.0 GiB capacity/);
    assert.doesNotMatch(map, /— \/ 8\.0 GiB/);
    assert.match(map, /GPU 1/);
    assert.match(map, /0% · 0\.0 GiB \/ 4\.0 GiB/);
    assert.match(map, /temp unsupported/);

    // Zero is measured; null with an availability state is explanatory text.
    assert.match(map, /0\.0 GiB \/ 8\.0 GiB · 0%/);
    assert.match(map, /error/);
    assert.match(map, /initializing/);

    // Shared memory is labeled, disconnected/stale/age are textual.
    assert.match(map, /reported 8\.0 GiB capacity · shared with host RAM/);
    assert.match(map, /disconnected · stale · 11s ago/);

    // Qualified GPU absence for CPU versus unknown backend.
    assert.match(map, /CPU backend · no GPUs reported/);
    assert.match(map, /GPU information unavailable/);

    // Duplicate host names stay distinguishable by stable node identity.
    assert.match(map, /w1/);
    assert.match(map, /master/);

    // Relationships stay limited to supplied membership edges.
    assert.match(map, /master → w1/);
    assert.doesNotMatch(map, /worker–worker/);

    // Cards let their visible summary name them instead of an overriding identity-only label.
    assert.match(map, /aria-pressed=/);
    assert.doesNotMatch(map, /aria-label="[^"]*connected/);

    // Details keeps process scope separate from host/device scope.
    const details = render(NodeUsageDetails, { props: { node: nodes.master, assignment: false, activity: null } }).body;
    assert.match(details, /usage unavailable · reported 8\.0 GiB capacity/);
    assert.match(details, /0\.0 GiB used \/ 4\.0 GiB capacity/);
    assert.match(details, /3%/);
    assert.match(details, /2\.0 GiB/);
    assert.match(details, /25%/);
    assert.match(details, /8\.0 GiB/);
  } finally {
    try { await server?.watcher?.close(); } catch { /* no watcher */ }
    await server?.close();
    process.chdir(cwd);
  }
});
