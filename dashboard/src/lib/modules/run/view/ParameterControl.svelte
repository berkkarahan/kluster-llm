<script lang="ts">
  import { PARAMETER_LABELS, parameterBounds } from '#lib/modules/run/configuration';
  import { KV_CACHE_TYPES } from '#lib/modules/run/entities';

  import { createApp } from '#lib/app.svelte';
  import type { RunInteractor } from '../interactor.svelte';
  let { name, label, presenter = createApp().run }: { name: string; label?: string; presenter?: RunInteractor } = $props();
  const descriptor = $derived(presenter.parameterDescriptor(name));
  const value = $derived(presenter.parameterValue(name));
  const title = $derived(label ?? PARAMETER_LABELS[name] ?? name.replaceAll('_', ' '));
  const nullable = $derived(descriptor?.anyOf?.some(item => item.type === 'null')
    ?? ['max_output_tokens', 'threads_batch', 'seed', 'frequency_penalty', 'reasoning_effort', 'reasoning_budget',
      'reasoning_format', 'draft_max', 'device', 'vram_percent', 'ram_percent', 'enable_thinking',
      'temperature', 'top_p', 'top_k', 'min_p', 'presence_penalty', 'repeat_penalty'].includes(name));
  const autoNumber = $derived(['parallel_slots', 'batch_size', 'micro_batch', 'moe_offload', 'checkpoints', 'cache_ram'].includes(name));
  const choices = $derived(descriptor?.choices ?? descriptor?.enum
    ?? descriptor?.anyOf?.find(item => item.enum)?.enum
    ?? (['cache_type_k', 'cache_type_v'].includes(name) ? [...KV_CACHE_TYPES]
      : name === 'flash_attention' ? ['auto', 'on', 'off']
      : name === 'mmap_mlock' ? ['auto', 'mmap', 'mlock', 'mmap+mlock', 'none']
      : name === 'device' ? ['gpu', 'cpu']
      : name === 'reasoning_format' ? ['auto', 'none', 'deepseek', 'deepseek-legacy'] : []));
  const bounds = $derived(parameterBounds(descriptor));
  const isBoolean = $derived(name === 'enable_thinking');
  const disabled = $derived(!!presenter.busy || presenter.settingsLoading || descriptor?.available === false);
  const effective = $derived(presenter.effectiveConfig()?.values[name]);
  const id = $derived(name === 'cache_type_k' ? 'run-cache-k' : name === 'cache_type_v' ? 'run-cache-v'
    : name === 'flash_attention' ? 'run-flash' : `run-${name.replaceAll('_', '-')}`);
  const help = $derived({
    parallel_slots: 'Shares total context capacity across concurrent requests.',
    batch_size: 'Logical prompt batch; memory also depends on the physical micro-batch.',
    micro_batch: 'Physical processing batch. The planner reports any reduction.',
    cache_type_v: 'Quantized V cache requires Flash Attention.',
    threads: 'Threads on the master; worker hardware is configured in Node Settings.',
    threads_batch: 'Inherit uses the generation thread count.',
    max_output_tokens: 'Positive limit, or -1 for unlimited within the available context.',
    seed: '-1 chooses a random seed. Results can differ across devices.',
    reasoning_budget: '-1 unrestricted, 0 ends thinking immediately, or a positive token budget.',
    vram_percent: 'Optional deployment limit. Each node’s safety cap still applies.',
    ram_percent: 'Shared-memory GPUs use the node’s RAM pool.',
    draft_max: 'Applies to draft-model speculation.',
  }[name]);

  function editDefault(): unknown {
    if (typeof effective === 'number') return effective;
    const defaults: Record<string, number> = { parallel_slots: 1, batch_size: 512, micro_batch: 128,
      checkpoints: 32, cache_ram: 8192, seed: -1, max_output_tokens: 1024, draft_max: 3,
      reasoning_budget: -1, vram_percent: presenter.vramPercent, ram_percent: presenter.ramPercent,
      threads_batch: presenter.threads };
    return defaults[name] ?? presenter.samplingDefault(name);
  }
</script>

<div class="parameter-control">
  <div class="parameter-heading">
    <label for={id}>{title}</label>
    <span class="scope-label">Next deployment</span>
  </div>
  {#if isBoolean}
    <select {id} class="field" value={value === null ? 'inherit' : value ? 'on' : 'off'} {disabled}
      onchange={(e) => presenter.setParameter(name, e.currentTarget.value === 'inherit' ? null : e.currentTarget.value === 'on')}>
      <option value="inherit">Model default</option><option value="on">On</option><option value="off">Off</option>
    </select>
  {:else if choices.length && !autoNumber}
    <select {id} class="field" value={value === null ? 'inherit' : String(value)} {disabled}
      onchange={(e) => presenter.setParameter(name, e.currentTarget.value === 'inherit' ? null : e.currentTarget.value)}>
      {#if nullable}<option value="inherit">Inherit default</option>{/if}
      {#each choices as choice}<option value={choice}>{choice === 'auto' ? 'Automatic' : choice}</option>{/each}
    </select>
  {:else}
    {#if nullable || autoNumber}
      <select aria-label={`${title} mode`} class="field" value={value === null || value === 'auto' ? 'default' : 'edit'} {disabled}
        onchange={(e) => presenter.setParameter(name, e.currentTarget.value === 'default' ? (autoNumber ? 'auto' : null) : editDefault())}>
        <option value="default">{autoNumber ? 'Automatic / default' : 'Inherit default'}</option>
        <option value="edit">Set a value</option>
      </select>
    {/if}
    {#if value !== null && value !== 'auto'}
      <input {id} class="field mono" type="number" value={Number(value)} min={bounds.min} max={bounds.max}
        step={bounds.integer || autoNumber || ['threads', 'seed', 'max_output_tokens', 'draft_max', 'reasoning_budget'].includes(name) ? 1 : 'any'}
        {disabled} oninput={(e) => presenter.setParameter(name, e.currentTarget.value === '' ? NaN : Number(e.currentTarget.value))} />
    {/if}
  {/if}
  {#if descriptor?.unavailable_reason}<small>{descriptor.unavailable_reason}</small>
  {:else if help}<small>{help}</small>{/if}
  <small class="effective-value">
    {#if effective !== undefined && effective !== null}
      Effective: {String(effective)} · {presenter.effectiveConfig()?.sources[name] ?? 'default'}
      {#if presenter.effectiveConfig()?.adjustments[name]} · {presenter.effectiveConfig()?.adjustments[name]}{/if}
    {:else}Effective value appears after planning.{/if}
  </small>
  {#if presenter.fieldErrors[name]}<p class="parameter-error" role="alert">{presenter.fieldErrors[name]}</p>{/if}
</div>

<style>
  .parameter-control { display: flex; flex-direction: column; gap: 8px; min-width: 0; }
  .parameter-heading { display: flex; justify-content: space-between; align-items: baseline; gap: 8px; }
  label { font-size: 13px; font-weight: 500; }
  .scope-label, small { font-size: 11px; color: var(--muted); line-height: 1.5; }
  .scope-label { white-space: nowrap; }
  .effective-value { color: var(--accent); }
  .parameter-error { color: var(--danger); font-size: 12px; }
</style>
