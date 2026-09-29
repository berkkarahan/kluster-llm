<script lang="ts">
  import { formatGib } from '$lib/format-memory';
  import type { RecorderStatus, UsageBucket, UsageLifetime, UsagePage, UsageReading, UsageSeries } from '$lib/types';

  let { nodeId, lifetimes, selectedLifetimeId, window, series, page, recorder, onlifetime, onwindow, onnext, error = '' }: {
    nodeId: string;
    lifetimes: UsageLifetime[];
    selectedLifetimeId: string;
    window: '15m' | '1h' | '24h';
    series: UsageSeries | null;
    page: UsagePage | null;
    recorder: RecorderStatus | null;
    onlifetime: (id: string) => void;
    onwindow: (window: '15m' | '1h' | '24h') => void;
    onnext: () => void;
    error?: string;
  } = $props();
  let selected = $state<UsageReading | null>(null);
  const current = $derived(lifetimes.find((life) => life.lifetime_id === selectedLifetimeId));
  const maxRss = $derived(Math.max(1, ...(series?.buckets.map((bucket) => bucket.process_rss_max ?? 0) ?? [])));
  function bucketDescription(bucket: UsageBucket): string {
    if (!bucket.count) return `${bucket.start} UTC · gap: no reading`;
    return `${bucket.start} UTC · process CPU ${bucket.process_cpu_min ?? 'unavailable'}–${bucket.process_cpu_max ?? 'unavailable'}%`
      + ` · process RSS ${formatGib(bucket.process_rss_min)}–${formatGib(bucket.process_rss_max)}`
      + ` · host CPU ${bucket.host_cpu_min ?? 'unavailable'}–${bucket.host_cpu_max ?? 'unavailable'}%`
      + bucket.devices.map((d) => ` · device ${d.device_id} utilization ${d.utilization_percent_min ?? 'unavailable'}–${d.utilization_percent_max ?? 'unavailable'}%`
        + ` memory ${formatGib(d.used_bytes_min)}–${formatGib(d.used_bytes_max)}`
        + ` temperature ${d.temperature_c_min ?? 'unavailable'}–${d.temperature_c_max ?? 'unavailable'} °C`
        + ` power ${d.power_w_min ?? 'unavailable'}–${d.power_w_max ?? 'unavailable'} W`).join('');
  }
</script>

<section class="panel p-3 mono text-xs mt-3" aria-label={`Recorded usage for ${nodeId}`}>
  <h2 class="text-sm font-semibold">Recorded usage · {nodeId}</h2>
  <p>Recording: {recorder?.recording ?? 'interrupted'} · last saved {recorder?.last_successful_save ?? 'never'}
    {#if recorder?.storage_truncated} · storage-limited coverage{/if}</p>
  {#if recorder?.error || error}<p role="alert">{recorder?.error ?? error}</p>{/if}
  <p>Earliest retained: {series?.earliest_retained ?? 'unrecorded'} · UTC</p>
  {#if !lifetimes.length}<p>Unrecorded · no retained process lifetime for this node.</p>{/if}
  <div class="flex flex-wrap items-center gap-2 my-2">
    <label for="usage-lifetime">Process lifetime</label>
    <select id="usage-lifetime" class="field max-w-56" value={selectedLifetimeId}
      onchange={(e) => onlifetime(e.currentTarget.value)}>
      {#each lifetimes as life (life.lifetime_id)}
        <option value={life.lifetime_id}>PID {life.pid} · {life.started_at} · {life.state}</option>
      {/each}
    </select>
    {#each ['15m', '1h', '24h'] as choice}
      <button class="btn" aria-pressed={window === choice} onclick={() => onwindow(choice as '15m' | '1h' | '24h')}>{choice}</button>
    {/each}
  </div>
  {#if current}<p>Process {current.state} · began {current.started_at}{#if current.ended_at} · ended {current.ended_at}{/if}
      {#if current.model} · model {current.model}{/if}</p>{/if}
  {#each current?.associations ?? [] as association (association.observed_from)}
    <p>Model {association.model} · known {association.observed_from} – {association.observed_until ?? 'ongoing'}</p>
  {/each}
  {#if series}
    <div class="usage-bars" aria-label="Bounded process CPU and RSS time buckets; RSS height relative to window maximum; gaps are not zeros">
      {#each series.buckets as bucket (bucket.start)}
        <button class="usage-bar" class:missing={bucket.count === 0}
          title={bucketDescription(bucket)} aria-label={bucketDescription(bucket)}
          style={`--cpu-height: ${Math.max(3, bucket.process_cpu_max ?? 0)}%; --rss-height: ${Math.max(3, (bucket.process_rss_max ?? 0) / maxRss * 100)}%;`}></button>
      {/each}
    </div>
    {#if series.buckets.some((bucket) => bucket.count === 0)}<p>Gap = no reading; measured zero is 0% with a recorded point.</p>{/if}
  {/if}
  <h3 class="mt-3 font-semibold">Exact readings (UTC)</h3>
  {#each page?.readings ?? [] as point (point.observed_at)}
    <button class="btn block my-1 text-left h-auto py-1" onclick={() => selected = point}
      aria-label={`${point.observed_at} UTC process CPU ${point.process.cpu_percent_machine ?? 'unavailable'} percent, process resident memory ${formatGib(point.process.rss_bytes)}`}>
      {point.observed_at} · process CPU {point.process.cpu_percent_machine == null ? 'unavailable' : `${point.process.cpu_percent_machine}%`}
      · process RSS {formatGib(point.process.rss_bytes)} · host CPU {point.host.cpu_percent == null ? 'unavailable' : `${point.host.cpu_percent}%`}
      · host RAM {formatGib(point.host.used_bytes)} / {formatGib(point.host.total_bytes)}
      {#each point.devices as device (device.device_id)}
        · device {device.device_id} utilization {device.utilization_percent == null ? 'unavailable' : `${device.utilization_percent}%`}
        · device memory {formatGib(device.used_bytes)} / {formatGib(device.total_bytes)}
        · {device.temperature_c == null ? 'unavailable' : `${device.temperature_c} °C`}
        · {device.power_w == null ? 'unavailable' : `${device.power_w} W`}
      {/each}
    </button>
  {:else}
    <p>No saved readings in this window; intervals before collection are unrecorded.</p>
  {/each}
  {#if selected}<p role="status">Selected {selected.observed_at} UTC · process CPU {selected.process.cpu_percent_machine ?? 'unavailable'}%
      · process RSS {formatGib(selected.process.rss_bytes)} · host RAM {formatGib(selected.host.used_bytes)}
      {#each selected.devices as device} · device {device.device_id} {device.utilization_percent ?? 'unavailable'}%
        · memory {formatGib(device.used_bytes)} / {formatGib(device.total_bytes)}
        · temperature {device.temperature_c == null ? 'unavailable' : `${device.temperature_c} °C`}
        · power {device.power_w == null ? 'unavailable' : `${device.power_w} W`}{/each}</p>{/if}
  {#if page?.next_cursor}<button class="btn mt-2" onclick={onnext}>More exact readings</button>{/if}
</section>

<style>
  .usage-bars { display: flex; align-items: end; gap: 1px; height: 64px; overflow: hidden; }
  .usage-bar { position: relative; height: 100%; flex: 1 1 1px; min-width: 1px; padding: 0; border: 0; background: transparent; cursor: pointer; }
  .usage-bar:not(.missing)::before, .usage-bar:not(.missing)::after { content: ''; position: absolute; bottom: 0; pointer-events: none; }
  .usage-bar:not(.missing)::before { left: 0; width: 55%; height: var(--cpu-height); background: var(--monitor-accent, var(--info)); }
  .usage-bar:not(.missing)::after { right: 0; width: 45%; height: var(--rss-height); background: var(--info); }
  .usage-bar.missing { background: var(--border); }
  .usage-bar:focus-visible { outline: 2px solid var(--fg); outline-offset: 0; }
</style>
