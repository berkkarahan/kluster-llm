<script lang="ts">
  /**
   * Live inference load strip — Unsloth Studio chat toast analogue.
   * Phases: Starting engine → Loading to VRAM/RAM → Ready | error | OOM Warning.
   * Reads appStore.loadProgress (PollLoop → GET /v1/load-progress); never blocks UI.
   */
  import { appStore } from '$lib/store.svelte';
  import { composeProgressLabel } from '$lib/format-transfer';

  let dismissedReady = $state(false);
  let readyKey = $state('');

  const p = $derived(appStore.loadProgress);
  const oom = $derived(appStore.planResult?.fit_status === 'overflow');
  const tight = $derived(appStore.planResult?.fit_status === 'tight');
  const active = $derived(p?.phase === 'starting' || p?.phase === 'loading');
  const ready = $derived(p?.phase === 'ready' && !dismissedReady);
  const failed = $derived(p?.phase === 'error');
  const showOom = $derived(oom && !active && !ready && !failed);

  // Auto-dismiss Ready after a short confirmation (Unsloth toast pattern).
  $effect(() => {
    if (p?.phase === 'ready') {
      const key = `${p.model}:${p.phase}`;
      if (key !== readyKey) {
        readyKey = key;
        dismissedReady = false;
      }
      const t = setTimeout(() => {
        dismissedReady = true;
      }, 8000);
      return () => clearTimeout(t);
    }
    if (p?.phase === 'starting' || p?.phase === 'loading' || p?.phase === 'error') {
      dismissedReady = false;
    }
  });

  const label = $derived.by(() => {
    if (showOom) return 'OOM Warning';
    if (!p) return '';
    switch (p.phase) {
      case 'starting':
        return 'Starting engine…';
      case 'loading':
        return `Loading to ${appStore.device === 'gpu' ? 'VRAM' : 'RAM'}…`;
      case 'ready':
        return 'Ready';
      case 'error':
        return 'Load failed';
      default:
        return '';
    }
  });

  const pct = $derived(p ? Math.min(100, Math.max(0, p.fraction * 100)) : 0);
  const detail = $derived(
    p
      ? composeProgressLabel(p.bytes_loaded, p.bytes_total, p.rate_bps, p.eta_seconds)
      : ''
  );
</script>

{#if active || ready || failed || showOom}
  <div
    class="panel flex flex-col gap-2 p-3"
    style:border-color={failed || showOom
      ? 'var(--danger)'
      : ready
        ? 'var(--accent)'
        : tight
          ? 'var(--warn)'
          : 'var(--border)'}
  >
    <div class="flex items-center gap-2">
      {#if active}<span class="spinner" aria-hidden="true"></span>{/if}
      <span
        class:chip={true}
        class:chip-ok={ready}
        class:chip-warn={active || tight}
        class:chip-danger={failed || showOom}>{label}</span
      >
      <span class="ml-auto mono text-[11px]" style="color: var(--muted)">{p?.model ?? appStore.selectedModel}</span>
    </div>

    {#if showOom}
      <p class="mono text-[12px]" style="color: var(--danger)">
        {String(appStore.planResult?.hint ?? 'Model + context exceed cluster budget — lower context or pick a smaller quant.')}
      </p>
    {:else if active}
      <div
        class="progress"
        role="progressbar"
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={Math.round(pct)}
        aria-label="Model load"
      >
        <span style:width={`${pct}%`}></span>
      </div>
      <div class="mono text-[11px]" style="color: var(--muted)">
        {detail}
        {#if p?.phase === 'starting'}
          <span> — engine warming up, bar fills as weights page in</span>
        {/if}
      </div>
    {:else if ready}
      <p class="mono text-[11px]" style="color: var(--accent)">Inference is up and answering requests.</p>
    {:else if failed}
      <p class="mono text-[12px]" style="color: var(--danger)">{p?.error ?? 'unknown error'}</p>
    {/if}
  </div>
{/if}
