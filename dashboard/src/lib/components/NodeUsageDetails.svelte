<script lang="ts">
  import { formatGib } from '$lib/format-memory';
  import type { InferenceActivity, UsageNode } from '$lib/types';

  let { node, assignment, activity }: { node: UsageNode | null; assignment: boolean; activity: InferenceActivity | null } = $props();
  const reading = $derived(node?.reading);
  function percent(value: number | null | undefined) { return value == null ? 'unavailable' : `${value}%`; }
</script>

<section class="panel p-3 mono text-xs space-y-3" aria-label="Node details">
  {#if !node}
    <p>Select a node to inspect its measured usage.</p>
  {:else}
    <h2 class="text-base font-semibold">{node.host} · {node.node_id}</h2>
    <p>{node.role} · {node.connected ? 'connected' : 'disconnected'} · {node.freshness}
      {#if node.age_seconds != null} · updated {Math.round(node.age_seconds)}s ago{/if}</p>
    <p>Hardware: {node.backend ?? 'unknown'} · {node.unified_memory ? 'shared RAM' : 'discrete or CPU'}</p>
    <p>Assignment: {assignment ? 'assigned to inference' : 'none known'} · cluster activity: {activity?.phase ?? 'unavailable'}
      (not a measurement of local computation)</p>
    {#if node.process_state === 'no managed process'}<p>No managed process</p>{/if}
    {#if reading}
      <p>Process {reading.process_role} PID {reading.pid} · started {reading.process_started_at} · {node.process_state ?? 'unobserved'}</p>
      <p>Observed {reading.observed_at}{#if reading.received_at} · received {reading.received_at}{/if}</p>
      <dl class="grid grid-cols-2 gap-2">
        <div><dt>Process CPU · whole machine</dt><dd>{percent(reading.process.cpu_percent_machine)}
          {reading.availability.process_cpu !== 'measured' ? `(${reading.availability.process_cpu})` : ''}</dd></div>
        <div><dt>Process resident memory · {reading.availability.process_rss ?? 'unavailable'}</dt><dd>{formatGib(reading.process.rss_bytes)}</dd></div>
        <div><dt>Host CPU · whole node</dt><dd>{percent(reading.host.cpu_percent)}
          {reading.availability.host_cpu && reading.availability.host_cpu !== 'measured' ? `(${reading.availability.host_cpu})` : ''}</dd></div>
        <div><dt>Host RAM · measured / physical</dt><dd>{formatGib(reading.host.used_bytes)} / {formatGib(reading.host.total_bytes ?? node.total_ram_bytes)}
          {node.unified_memory ? ' · shared, counted once' : ''}
          {reading.availability.host_memory && reading.availability.host_memory !== 'measured' ? ` (${reading.availability.host_memory})` : ''}</dd></div>
      </dl>
      {#if reading.host.used_bytes == null || reading.host.total_bytes == null}<p>Combined measured usage: partial</p>{/if}
      {#each reading.devices as device (device.device_id)}
        <div class="border-t pt-2" style="border-color: var(--border)">
          <h3>Device {device.device_id} · device-wide {node.unified_memory ? 'shared memory' : 'memory'}</h3>
          <p>Utilization {percent(device.utilization_percent)} · memory {formatGib(device.used_bytes)} / {formatGib(device.total_bytes)}</p>
          <p>Temperature {device.temperature_c == null ? 'unavailable' : `${device.temperature_c} °C`}
            · power {device.power_w == null ? 'unavailable' : `${device.power_w} W`}</p>
          {#if device.unavailable_reason}<p>{device.unavailable_reason}</p>{/if}
        </div>
      {/each}
      {#if reading.collector_error}<p>Measurement error: {reading.collector_error}</p>{/if}
    {:else}
      <p>Process and host measurements unavailable; no reading recorded.</p>
      <p>Host physical RAM: {formatGib(node.total_ram_bytes)} · device capacity: {formatGib(node.vram_bytes)}</p>
    {/if}
  {/if}
</section>
