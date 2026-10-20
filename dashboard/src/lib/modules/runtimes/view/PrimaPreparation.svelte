<script lang="ts">
  import { onMount, onDestroy, untrack } from 'svelte';
  import type { RuntimesInteractor } from '../interactor.svelte';
  import ErrorMessage from '#lib/shared/ErrorMessage.svelte';
  import PrimaPreparationStatus from './PrimaPreparationStatus.svelte';
  let { presenter }: { presenter: RuntimesInteractor } = $props();
  const deployment = $derived(presenter.deployment);
  const runtimeId = $derived(deployment.runtimeId);
  const preview = $derived(deployment.preview?.plan ?? null);
  const busy = $derived(!!deployment.pendingAction || deployment.preparing);
  const error = $derived(deployment.error);
  const versions = $derived((presenter.status?.runtimes?.versions ?? []).filter(v => v.version.engine === 'primacpp' && v.build));
  const master = $derived(presenter.status?.role === 'master');
  const model = $derived(presenter.run.selectedModel);
  const fresh = $derived(deployment.fresh);
  const placementError = $derived(deployment.placementError);
  const ranks = $derived(fresh ? preview?.prima_topology?.ranks ?? [] : []);
  const scheduler = $derived(fresh ? preview?.scheduler ?? preview?.profiling?.scheduler ?? null : null);
  const active = $derived(presenter.preparation.active);
  $effect(() => { const liveJob = presenter.primaPreparation; untrack(() => presenter.preparation.observe(liveJob)); });
  onMount(() => presenter.preparation.acquire());
  onDestroy(() => deployment.stop());
  const prepare = (profile:boolean) => deployment.prepare(profile);
</script>

{#if versions.length && master}
<section class="panel prima-preparation" aria-labelledby="prima-preparation-heading">
  <h2 id="prima-preparation-heading">Prima preparation</h2>
  <p class="hint">Select a model in Deploy, then prepare its files. Placement is calculated automatically. Normal Prima inference remains blocked until qualification passes. Physical assessment uses the standalone test harness.</p>
  <label>Prepared Prima runtime
    <select class="field" value={runtimeId} onchange={e => deployment.setRuntime(e.currentTarget.value)} disabled={busy || !!active}>
      <option value="">Select a runtime</option>
      {#each versions as entry}<option value={entry.version.runtime_id}>{entry.version.name || entry.version.commit.slice(0, 12)}</option>{/each}
    </select>
  </label>
  <p>Model: {model || 'Select a model in Deploy'}</p>
  {#if runtimeId && presenter.run.settingsScope !== 'primacpp'}<button class="btn" onclick={() => deployment.setRuntime(runtimeId)}>Load Prima model settings</button>{/if}
  {#if deployment.inspecting}<p role="status">Calculating deployment…</p>{/if}
  <div class="actions">
    {#if deployment.planningError}<button class="btn" onclick={() => deployment.inspect(true)} disabled={!!deployment.inspectReason || deployment.inspecting || busy}>Retry planning</button>{/if}
    <button class="btn" onclick={() => prepare(false)} disabled={!!deployment.prepareActionReason || busy}>Prepare files</button>
    <button class="btn" onclick={() => prepare(true)} disabled={!!deployment.prepareActionReason || busy}>Prepare &amp; profile</button>
  </div>
  {#if preview}
    {#if placementError}<ErrorMessage source="prima-placement" message={placementError} snapshot={false} />{/if}
    {#if !fresh}<p class="hint">Settings changed. Preparation will calculate the updated placement automatically.</p>{/if}
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
  <PrimaPreparationStatus observer={presenter.preparation} />
  {#if deployment.prepareActionReason}<p>{deployment.prepareActionReason}</p>{/if}
  {#if error}<ErrorMessage source="prima-prepare" message={error} snapshot={false} />{/if}
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

