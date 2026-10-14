<script lang="ts">
  import { onDestroy } from 'svelte';
  import type { RuntimesInteractor } from '../interactor.svelte';
  import ErrorMessage from '#lib/shared/ErrorMessage.svelte';
  let { presenter }: { presenter: RuntimesInteractor } = $props();
  let runtimeId = $state('');
  let preview = $state<Record<string, any> | null>(null);
  let job = $state<Record<string, any> | null>(null);
  let busy = $state(false);
  let error = $state('');
  let generation = 0;
  let timer: ReturnType<typeof setTimeout> | null = null;
  const versions = $derived((presenter.status?.runtimes?.versions ?? []).filter(v => v.version.engine === 'primacpp' && v.build));
  const master = $derived(presenter.status?.role === 'master');
  const model = $derived(presenter.run.selectedModel);
  const currentSettings = $derived(JSON.stringify(presenter.run.networkPlanPayload()));
  let reviewedSettings = $state('');
  const fresh = $derived(currentSettings === reviewedSettings);
  const ranks = $derived(preview?.prima_topology?.ranks ?? []);
  const scheduler = $derived(preview?.scheduler ?? preview?.profiling?.scheduler ?? null);
  const active = $derived(job && !['prepared', 'failed', 'cancelled', 'interrupted'].includes(job.phase));
  async function inspect() {
    const seq = ++generation;
    busy = true; error = ''; preview = null;
    try {
      const settings = presenter.run.networkPlanPayload();
      const data = await presenter.primaPlan(runtimeId, settings);
      if (seq === generation) { preview = data; reviewedSettings = JSON.stringify(settings); }
    } catch (e) { if (seq === generation) error = String(e); }
    finally { if (seq === generation) busy = false; }
  }
  async function poll(id: string, seq: number) {
    try {
      const data = await presenter.primaJob(id);
      if (seq !== generation) return;
      job = data;
      if (!['prepared', 'failed', 'cancelled', 'interrupted'].includes(data.phase)) timer = setTimeout(() => void poll(id, seq), 2000);
    } catch (e) { if (seq === generation) error = String(e); }
  }
  async function prepare(profile: boolean) {
    if (!preview || !fresh) return;
    const seq = ++generation;
    busy = true; error = '';
    try {
      const data = await presenter.preparePrima(runtimeId, presenter.run.networkPlanPayload(), preview.plan_digest, profile);
      if (seq === generation) { job = data; void poll(data.job_id, seq); }
    } catch (e) { if (seq === generation) error = String(e); }
    finally { if (seq === generation) busy = false; }
  }
  async function cancel() {
    if (!job) return;
    try { await presenter.cancelPrima(job.job_id); }
    catch (e) { error = String(e); }
  }
  onDestroy(() => { ++generation; if (timer) clearTimeout(timer); });
</script>

{#if versions.length && master}
<section class="panel prima-preparation" aria-labelledby="prima-preparation-heading">
  <h2 id="prima-preparation-heading">Prima preparation</h2>
  <p class="hint">Select a model in Deploy, then inspect placement and prepare its files. Normal Prima inference remains blocked until qualification passes. Physical assessment uses the standalone test harness.</p>
  <label>Prepared Prima runtime
    <select class="field" bind:value={runtimeId} onchange={() => { ++generation; preview = null; }} disabled={busy || !!active}>
      <option value="">Select a runtime</option>
      {#each versions as entry}<option value={entry.version.runtime_id}>{entry.version.name || entry.version.commit.slice(0, 12)}</option>{/each}
    </select>
  </label>
  <p>Model: {model || 'Select a model in Deploy'}</p>
  <div class="actions">
    <button class="btn" onclick={inspect} disabled={!runtimeId || !model || busy || !!active}>Inspect placement</button>
    <button class="btn" onclick={() => prepare(false)} disabled={!preview || !fresh || !ranks.length || busy || !!active}>Prepare files</button>
    <button class="btn" onclick={() => prepare(true)} disabled={!preview || !fresh || !ranks.length || busy || !!active}>Prepare &amp; profile</button>
    {#if active}<button class="btn" onclick={cancel}>Cancel preparation</button>{/if}
  </div>
  {#if preview}
    {#if !fresh}<p class="hint">Settings changed. Inspect placement again before preparation.</p>{/if}
    <p>Profiles: {preview.profiling?.status ?? 'unmeasured'} · Candidates: {preview.candidate_count ?? 0}{#if scheduler} · Scheduler: {scheduler.scheduler_mode} (k={scheduler.cycle_count_k}, W={scheduler.window_size_w}){/if}</p>
    {#if scheduler}
      {#if scheduler.scheduler_mode === 'highs_halda'}
        <p class="hint">HiGHS Halda MILP objective: {Number(scheduler.objective_step_ms ?? 0).toFixed(2)} ms · compute {Number(scheduler.compute_step_ms ?? 0).toFixed(2)} ms · disk reload {Number(scheduler.disk_reload_step_ms ?? 0).toFixed(2)} ms · network {Number(scheduler.network_step_ms ?? 0).toFixed(2)} ms</p>
      {:else if scheduler.fallback_reason}
        <p class="hint">Scheduler fallback: {scheduler.fallback_reason}{scheduler.missing_profile_nodes?.length ? ` (missing profiles: ${scheduler.missing_profile_nodes.join(', ')})` : ''}</p>
      {/if}
      {#if scheduler.pruned_nodes?.length}
        <p class="hint">Pruned weak workers: {scheduler.pruned_nodes.map((p: any) => `${p.node_id} (${p.reason})`).join(', ')}</p>
      {/if}
    {/if}
    <!-- svelte-ignore a11y_no_noninteractive_tabindex (keyboard access to the scrollable placement table) -->
    <div class="table-scroll" role="region" aria-label="Prima rank placement" tabindex="0">
      <table><thead><tr><th>Node</th><th>Rank</th><th>Layers</th><th>Devices</th><th>-ngl</th><th>Tensor split</th>{#if scheduler?.node_allocations?.length}<th>Halda (w, n / set / graphs)</th>{/if}</tr></thead>
        <tbody>{#each ranks as rank}
          {@const halda = scheduler?.node_allocations?.find((a: any) => a.node_id === rank.node_id)}
          <tr><td>{rank.node_id}</td><td>{rank.rank}</td><td>{rank.layers.map((r: any) => `${r.start}–${r.end}`).join(', ') || 'Coordinator'}</td><td>{rank.devices.join(', ') || 'CPU'}</td><td>{rank.ngl}</td><td>{rank.tensor_split.join(', ') || '—'}</td>{#if scheduler?.node_allocations?.length}<td>{halda ? `w=${halda.w_m}, n=${halda.n_m} · ${halda.memory_pressure_set} · graphs=${halda.prima_graphs}` : '—'}</td>{/if}</tr>{/each}</tbody>
      </table>
    </div>
    <details><summary>Qualification blockers and network edges</summary><pre>{JSON.stringify({ blockers: preview.qualification_errors, edges: preview.network_edges }, null, 2)}</pre></details>
  {/if}
  {#if job}<p aria-live="polite">Preparation: {job.phase}</p>{#if job.error}<ErrorMessage source="prima-prepare" message={job.error} />{/if}<details><summary>Participant status</summary><pre>{JSON.stringify(job.nodes ?? {}, null, 2)}</pre></details>{/if}
  {#if error}<ErrorMessage source="prima-prepare" message={error} />{/if}
</section>
{/if}
<style>
  .prima-preparation {padding:24px;display:grid;gap:16px;min-width:0}
  label {display:grid;gap:8px;min-width:0}
  .actions {display:flex;gap:8px;flex-wrap:wrap}
  .table-scroll {overflow:auto;max-width:100%}
  table {min-width:600px;width:100%;text-align:left}
  td,th {padding:8px;overflow-wrap:anywhere}
  pre {white-space:pre-wrap;overflow-wrap:anywhere}
</style>
