<script lang="ts">
  import { createApp } from '#lib/app.svelte';
  import { routes } from '#lib/shared/navigation';
  import ErrorMessage from '#lib/shared/ErrorMessage.svelte';
  import { formatGib, formatMemoryComponent } from '#lib/shared/format-memory';
  import { runtimeRunReason } from '#lib/modules/run/runtime-readiness';
  import type { HighsScheduleReport, PrimaTopology } from '#lib/modules/run/entities';
  import type { RunInteractor } from '../interactor.svelte';
  let {
    presenter = createApp().run,
    onRequestProfile
  }: {
    presenter?: RunInteractor;
    onRequestProfile?: () => void;
  } = $props();
  const plan = $derived(presenter.planResult);
  const scheduler = $derived((plan?.scheduler as HighsScheduleReport | undefined) ?? null);
  const topology = $derived((plan?.prima_topology as PrimaTopology | undefined) ?? null);
  const runtimeBlocked = $derived(runtimeRunReason(presenter.status?.runtimes, presenter.status?.node_id ?? '', presenter.planResult));
  const resolved = $derived((plan?.resolved_launch_settings ?? {}) as Record<string, unknown>);
  const fit = $derived(String(plan?.fit_status ?? ''));
  const gpuBudget = $derived.by(() => {
    const budgets = plan?.node_gpu_budget_bytes as Record<string, number> | undefined;
    return budgets ? Object.values(budgets).reduce((sum, value) => sum + value, 0) : null;
  });
  const ramBudget = $derived.by(() => {
    const budgets = plan?.node_ram_budget_bytes as Record<string, number> | undefined;
    if (budgets) return Object.values(budgets).reduce((sum, value) => sum + value, 0);
    return Array.isArray(plan?.budgets_bytes) && Array.isArray(plan?.node_ids) && plan.node_ids.length === 1 ? Number(plan.budgets_bytes[0]) : null;
  });
  const components = [
    ['Model weights', 'weights_estimate_bytes'], ['Auxiliary weights', 'auxiliary_weights_bytes'],
    ['KV cache', 'kv_estimate_bytes'], ['Recurrent state', 'recurrent_state_bytes'],
    ['Host checkpoints', 'checkpoint_estimate_bytes'], ['Compute buffers', 'compute_estimate_bytes'],
    ['Auxiliary runtime', 'auxiliary_runtime_bytes'], ['Execution reserve', 'execution_overhead_bytes'],
  ];

  function schedulerLabel(mode: string | undefined): string {
    if (mode === 'highs_halda') return 'HiGHS Halda MILP';
    if (mode === 'priority') return 'Priority';
    return 'Capacity-first';
  }
</script>

<aside class="panel plan-summary" aria-label="Memory plan">
  <div class="od-row">
    <h2>Memory plan</h2>
    <span class="tag" role="status">{presenter.estimating ? 'Updating' : fit === 'fits' ? 'Fits budget' : fit === 'tight' ? 'Tight fit' : fit === 'overflow' ? 'Over budget' : 'Awaiting estimate'}</span>
  </div>
  <div>
    <p class="plan-total">{plan?.estimated_bytes != null ? formatGib(Number(plan.estimated_bytes)) : '—'}
      {#if plan?.budget_bytes != null}<small>/ {formatGib(Number(plan.budget_bytes))}</small>{/if}</p>
    <p class="small muted">Estimated total memory / usable total memory budget</p>
  </div>
  {#if plan}
    <div class="stack-sm">
      {#each components as [label, key]}
        <div class="estimate-line"><span class="label">{label}</span><span class="value">{formatMemoryComponent(plan[key] as number)}</span></div>
      {/each}
    </div>
    {#if fit === 'overflow'}
      {#if gpuBudget !== null}<p class="small">GPU budget: {formatGib(gpuBudget)}</p>{/if}
      {#if ramBudget !== null}<p class="small">RAM budget: {formatGib(ramBudget)}</p>{/if}
    {:else}
      {#if plan.vram_alloc_bytes != null}<p class="small">Estimated GPU memory: {formatGib(Number(plan.vram_alloc_bytes))}</p>{/if}
      {#if plan.cpu_alloc_bytes != null}<p class="small">Estimated master CPU RAM: {formatGib(Number(plan.cpu_alloc_bytes))}</p>{/if}
    {/if}
    {#if topology?.ranks?.length}
      <p class="small muted mono">Ring-TCP: {topology.ranks.length} rank(s) · ports {topology.data_port}/{topology.signal_port}</p>
    {/if}
    {#if scheduler}
      <div class="scheduler-card stack-sm" aria-label="Prima scheduler summary">
        <div class="od-row">
          <span class="small"><strong>Scheduler:</strong> {schedulerLabel(scheduler.scheduler_mode)}</span>
          <span class="tag">{scheduler.scheduler_mode} · k={scheduler.cycle_count_k} · W={scheduler.window_size_w}</span>
        </div>
        {#if scheduler.scheduler_mode === 'highs_halda'}
          <p class="small muted">Step objective: {scheduler.objective_step_ms.toFixed(2)} ms (compute {scheduler.compute_step_ms.toFixed(2)} ms · disk reload {scheduler.disk_reload_step_ms.toFixed(2)} ms · network {scheduler.network_step_ms.toFixed(2)} ms)</p>
        {:else if scheduler.fallback_reason}
          <p class="small muted">Fallback reason: {scheduler.fallback_reason}{scheduler.missing_profile_nodes?.length ? ` (missing profiles: ${scheduler.missing_profile_nodes.join(', ')})` : ''}</p>
          {#if scheduler.fallback_reason === 'missing_device_profiles'}
            <p class="small muted">
              Prepare and profile this model under <a href={routes.settingsLlama}>Settings → Prima</a> to enable HiGHS / Profiled placement.
              {#if onRequestProfile}
                <button type="button" class="text-button" onclick={onRequestProfile}>Prepare &amp; profile in Step 3 →</button>
              {/if}
            </p>
          {/if}
        {/if}
        {#if scheduler.node_allocations?.length}
          <div class="stack-sm">
            {#each scheduler.node_allocations as alloc (alloc.node_id)}
              <p class="small">
                <strong>{alloc.node_id}</strong> (rank {alloc.rank}): w={alloc.w_m}, n={alloc.n_m} · set <span class="tag">{alloc.memory_pressure_set}</span>{alloc.forced_m4 ? ' (forced M4)' : ''} · graphs={alloc.prima_graphs}
              </p>
            {/each}
          </div>
        {/if}
        {#if scheduler.pruned_nodes?.length}
          <p class="small muted">Pruned weak workers: {scheduler.pruned_nodes.map((p) => `${p.node_id} (${p.reason})`).join(', ')}</p>
        {/if}
      </div>
    {/if}
    {#if plan.resolved_launch_settings}
      <p class="small">Resolved: context {String(resolved.context)}, batch {String(resolved.batch_size)}, micro-batch {String(resolved.micro_batch)}, {String(resolved.parallel_slots)} slot(s).</p>
      <p class="small muted">K cache: {String(resolved.cache_type_k ?? presenter.cacheTypeK)} · V cache: {String(resolved.cache_type_v ?? presenter.cacheTypeV)}. Weights and host checkpoints are separate.</p>
    {/if}
    {#if presenter.effectiveConfig()?.values.context_per_request != null}
      <p class="small">Context per request: {String(presenter.effectiveConfig()?.values.context_per_request)} tokens</p>
    {/if}
    {#if plan.estimate_complete === false}<p class="notice warning" role="status">Incomplete estimate: some memory costs are approximate or unpriced.</p>{/if}
    {#each (plan.estimate_warnings as string[] | undefined) ?? [] as warning}<p class="small warning">{warning}</p>{/each}
    {#if plan.hint}<p class="small warning">{String(plan.hint)}</p>{/if}
  {/if}
  {#if runtimeBlocked}
    <p class="notice small" role="status">{runtimeBlocked} <a href={routes.settingsLlama}>{plan?.engine === 'primacpp' || presenter.status?.engine === 'primacpp' ? 'Open Prima settings →' : 'Open llama.cpp settings →'}</a></p>
  {/if}
  {#if presenter.estimateError}<ErrorMessage source="estimate" message={presenter.estimateError} snapshot={false} />{/if}
  {#if presenter.estimateNote}<p class="small muted" aria-live="polite">{presenter.estimateNote}</p>{/if}
  <p class="small muted">Planning estimate. Actual memory use varies with the model, backend and workload.</p>
  <button class="btn" type="button" disabled={!!presenter.planRunBlockedReason() || !!presenter.busy || presenter.estimating}
    onclick={() => presenter.refreshEstimate()}>Refresh estimate</button>
</aside>

<style>
  h2 { font-size: 16px; font-weight: 600; }
  .warning { color: var(--warn); }
  .notice { padding: 12px; border: 1px solid var(--border); border-radius: 8px; }
  .scheduler-card { padding: 10px 12px; border: 1px solid var(--border); border-radius: 8px; }
  .od-row { justify-content: space-between; flex-wrap: wrap; }
</style>
