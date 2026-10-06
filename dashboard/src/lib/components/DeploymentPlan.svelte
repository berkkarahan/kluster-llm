<script lang="ts">
  import { appStore } from '$lib/store.svelte';
  import ErrorMessage from './ErrorMessage.svelte';
  import { formatGib, formatMemoryComponent } from '$lib/format-memory';
  const plan = $derived(appStore.planResult);
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
</script>

<aside class="panel plan-summary" aria-label="Memory plan">
  <div class="od-row">
    <h2>Memory plan</h2>
    <span class="tag" role="status">{appStore.estimating ? 'Updating' : fit === 'fits' ? 'Fits budget' : fit === 'tight' ? 'Tight fit' : fit === 'overflow' ? 'Over budget' : 'Awaiting estimate'}</span>
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
    {#if plan.resolved_launch_settings}
      <p class="small">Resolved: context {String(resolved.context)}, batch {String(resolved.batch_size)}, micro-batch {String(resolved.micro_batch)}, {String(resolved.parallel_slots)} slot(s).</p>
      <p class="small muted">K cache: {String(resolved.cache_type_k ?? appStore.cacheTypeK)} · V cache: {String(resolved.cache_type_v ?? appStore.cacheTypeV)}. Weights and host checkpoints are separate.</p>
    {/if}
    {#if appStore.effectiveConfig()?.values.context_per_request != null}
      <p class="small">Context per request: {String(appStore.effectiveConfig()?.values.context_per_request)} tokens</p>
    {/if}
    {#if plan.estimate_complete === false}<p class="notice warning" role="status">Incomplete estimate: some memory costs are approximate or unpriced.</p>{/if}
    {#each (plan.estimate_warnings as string[] | undefined) ?? [] as warning}<p class="small warning">{warning}</p>{/each}
    {#if plan.hint}<p class="small warning">{String(plan.hint)}</p>{/if}
  {/if}
  {#if appStore.estimateError}<ErrorMessage source="estimate" message={appStore.estimateError} snapshot={false} />{/if}
  {#if appStore.estimateNote}<p class="small muted" aria-live="polite">{appStore.estimateNote}</p>{/if}
  <p class="small muted">Planning estimate. Actual memory use varies with the model, backend and workload.</p>
  <button class="btn" type="button" disabled={!!appStore.planRunBlockedReason() || !!appStore.busy || appStore.estimating}
    onclick={() => appStore.refreshEstimate()}>Refresh estimate</button>
</aside>

<style>
  h2 { font-size: 16px; font-weight: 600; }
  .warning { color: var(--warn); }
  .notice { padding: 12px; border: 1px solid var(--border); border-radius: 8px; }
  .od-row { justify-content: space-between; flex-wrap: wrap; }
</style>
