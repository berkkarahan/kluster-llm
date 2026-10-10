<script lang="ts">
  import ErrorMessage from '$lib/shared/ErrorMessage.svelte';
  import type { InferenceActivity } from '$lib/modules/usage/entities';

  let {
    activity,
    assigned = true
  }: { activity?: InferenceActivity | null; assigned?: boolean } = $props();

  const phase = $derived(activity?.phase ?? 'idle');
  const active = $derived(phase === 'starting' || phase === 'loading_model' || phase === 'processing_input' || phase === 'generating');
  const pct = $derived(Math.round(Math.min(1, Math.max(0, activity?.load_fraction ?? 0)) * 100));
  const label = $derived.by(() => {
    if (!assigned && phase !== 'idle') return 'unassigned';
    if (phase === 'loading_model') return `loading ${pct}%`;
    if (phase === 'processing_input') return `input ${activity?.prompt_tokens_processed ?? 0}/${activity?.prompt_tokens ?? 0}`;
    if (phase === 'generating') return `generating ${activity?.output_tokens ?? 0} tok`;
    if (phase === 'starting') return 'starting';
    if (phase === 'ready') return 'ready';
    if (phase === 'error') return 'stopped';
    return 'idle';
  });
  const chipClass = $derived.by(() => {
    if (phase === 'error') return 'chip-muted';
    if (phase === 'ready') return 'chip-ok';
    if (active) return phase === 'processing_input' || phase === 'generating' ? 'chip-info' : 'chip-warn';
    return 'chip-muted';
  });
</script>

<span class="inline-flex items-center gap-1.5" title={phase === 'error' ? 'Inference stopped' : activity?.detail ?? label}>
  {#if active && assigned}<span class="spinner activity-spinner" aria-hidden="true"></span>{/if}
  <span class="chip {chipClass}">{label}</span>
  {#if assigned && (phase === 'processing_input' || phase === 'generating') && (activity?.active_requests ?? 0) > 1}
    <span class="mono text-[10px]" style="color: var(--muted)">{activity?.active_requests} req</span>
  {/if}
</span>

{#if phase === 'error'}<ErrorMessage source="inference-activity" message={activity?.detail ?? 'Inference failed'} />{/if}
