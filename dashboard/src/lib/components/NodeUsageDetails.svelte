<script lang="ts">
  import { formatGib } from '$lib/format-memory';
  import type { InferenceActivity, UsageNode } from '$lib/types';

  let { node, assignment, activity }: { node: UsageNode | null; assignment: boolean; activity: InferenceActivity | null } = $props();
  const reading = $derived(node?.reading);
  const resources = $derived(node?.resources);
  const freshness = $derived(resources ? node?.resource_freshness : node?.freshness);
  const age = $derived(resources ? node?.resource_age_seconds : node?.age_seconds);
  function percent(value: number | null | undefined) { return value == null ? 'unavailable' : `${Math.round(value * 10) / 10}%`; }
</script>

<section class="panel p-3 mono text-xs space-y-3" aria-label="Node details">
  {#if !node}
    <p>Select a node to inspect its measured usage.</p>
  {:else}
    <h2 class="text-base font-semibold">{node.host} · {node.node_id}</h2>
    <p>{node.role} · {node.connected ? 'connected' : 'disconnected'} · {freshness ?? 'unavailable'}
      {#if age != null} · updated {Math.round(age)}s ago{/if}</p>
    <p>Hardware: {node.backend ?? 'unknown'} · {resources ? 'per-device memory topology below' : node.unified_memory ? 'shared RAM' : 'discrete or CPU'}</p>
    <p>Assignment: {assignment ? 'assigned to inference' : 'none known'} · cluster activity: {activity?.phase ?? 'unavailable'}
      (not a measurement of local computation)</p>
    {#if node.process_state === 'no managed process'}<p>No managed process</p>{/if}
    {#if resources}
      <div class="border-t pt-2 space-y-2" style="border-color: var(--border)" aria-label="Native resource readings">
        <h3>Native resources · {node.resource_freshness ?? 'unavailable'}
          {#if node.resource_age_seconds != null} · {Math.round(node.resource_age_seconds)}s old{/if}</h3>
        <p>Host CPU {percent(resources.host.cpu.value)} · {resources.host.cpu.reason ?? resources.host.cpu.status}</p>
        <p>Host RAM: {formatGib(resources.host.memory.used.value)} used / {formatGib(resources.host.memory.total.value)} physical</p>
        <p>Free {formatGib(resources.host.memory.free.value)} · available {formatGib(resources.host.memory.available.value)}
          {#if resources.host.memory.free.reason} · free: {resources.host.memory.free.reason}{/if}</p>
        {#each resources.processes as process (process.lifetime_id)}
          <p>Process {process.role} PID {process.pid} · {process.state} · started {process.started_at}</p>
          <p>Process CPU · whole machine: {percent(process.cpu.value)} · {process.cpu.reason ?? process.cpu.status}</p>
          <p>Process resident memory: {formatGib(process.rss.value)} · {process.rss.reason ?? process.rss.status}</p>
          <p>Observed {process.rss.observed_at}</p>
        {/each}
        {#if node.process_state === 'unavailable'}<p>Managed-process identity or readings unavailable.</p>{/if}
        {#each resources.devices as device (device.device_key)}
          <div><h4>{device.name} · {device.memory_kind} · {device.device_key}</h4>
            {#if device.utilization}
              <p>GPU utilization: {percent(device.utilization.value)} · {device.utilization.reason ?? device.utilization.status}</p>
              <p>Source {device.utilization.source} · observed {device.utilization.observed_at}</p>
            {/if}
            {#each device.memory as memory (memory.kind)}
              <p>{memory.kind}: {formatGib(memory.used.value)} used · {formatGib(memory.free.value)} free
                {memory.backing_pool_id === 'host:ram' ? ' · shares host RAM, counted once' : ` / ${formatGib(memory.total.value)} physical`}</p>
              {#if memory.used.reason || memory.free.reason}<p>{memory.used.reason ?? memory.free.reason}</p>{/if}
              <p>Observed {memory.used.observed_at}</p>
              <p>Source {memory.used.source}</p>
            {/each}
          </div>
        {/each}
        {#if resources.device_discovery.reason}<p>GPU discovery: {resources.device_discovery.reason}</p>{/if}
        <p>Combined physical capacity: {formatGib(resources.totals.physical_capacity.value)} · {resources.totals.physical_capacity.status}</p>
      </div>
    {/if}
    {#if reading && !resources}
      <p>Legacy-format measurements · native resource snapshot unavailable.</p>
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
          <p>Utilization {percent(device.utilization_percent)} · memory {device.used_bytes == null
            ? `usage unavailable · reported ${formatGib(device.total_bytes ?? node.devices?.find((gpu) => String(gpu.index) === device.device_id)?.vram_bytes)} capacity`
            : `${formatGib(device.used_bytes)} used / ${formatGib(device.total_bytes ?? node.devices?.find((gpu) => String(gpu.index) === device.device_id)?.vram_bytes)} capacity`}</p>
          <p>Temperature {device.temperature_c == null ? 'unavailable' : `${device.temperature_c} °C`}
            · power {device.power_w == null ? 'unavailable' : `${device.power_w} W`}</p>
          {#if device.unavailable_reason}<p>{device.unavailable_reason}</p>{/if}
        </div>
      {/each}
      {#if reading.collector_error}<p>Measurement error: {reading.collector_error}</p>{/if}
    {:else if !resources}
      <p>{resources ? 'No verified managed-process reading recorded.' : 'Process and host measurements unavailable; no reading recorded.'}</p>
      <p>Host physical RAM: {formatGib(node.total_ram_bytes)} · device capacity: {formatGib(node.vram_bytes)}</p>
    {/if}
    {#if resources && reading?.devices.length}
      <div aria-label="Additional device sensors">
        {#each reading.devices as device (device.device_id)}
          <p>Legacy device {device.device_id}: utilization {percent(device.utilization_percent)} · temperature {device.temperature_c ?? 'unavailable'} °C · power {device.power_w ?? 'unavailable'} W</p>
        {/each}
        <p>Observed {reading.observed_at}</p>
      </div>
    {/if}
  {/if}
</section>
