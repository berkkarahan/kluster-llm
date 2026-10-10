<script lang="ts">
  import { createApp } from '$lib/app.svelte';
  import ErrorMessage from '$lib/shared/ErrorMessage.svelte';
  import type { ClusterInteractor } from '../../cluster/interactor.svelte';
  let { presenter = createApp().cluster }: { presenter?: ClusterInteractor } = $props();
  /**
   * System Settings > Software & Updates: local checkout identity (git HEAD +
   * llama-release pin), runtime ports, and the master-only "Update workers"
   * action (git pull → smart-rebuild → rejoin).
   */

  const isMaster = $derived(presenter.status?.role === 'master');
  const sw = $derived(presenter.status?.software);
  const upd = $derived(presenter.status?.update);
  const ports = $derived(
    `control ${presenter.status?.control_port ?? 18765} · rpc ${presenter.status?.rpc_port ?? 50052} · infer ${presenter.status?.infer_port ?? 8080}`
  );
</script>

<section class="panel p-3.5" aria-labelledby="sw-label">
  <h2 id="sw-label" class="mono text-[11px] uppercase tracking-wider mb-3" style="color: var(--muted)">
    Software &amp; Updates
  </h2>

  <div class="grid grid-cols-2 gap-2">
    <div class="border px-2.5 py-2" style="border-color: var(--border)">
      <div class="mono text-[10px]" style="color: var(--muted)">git HEAD</div>
      <div class="mono text-[12px] nums">{sw?.git_head ?? '—'}</div>
    </div>
    <div class="border px-2.5 py-2" style="border-color: var(--border)">
      <div class="mono text-[10px]" style="color: var(--muted)">llama-release</div>
      <div class="mono text-[12px] nums">{sw?.llama_release ?? '—'}</div>
    </div>
  </div>

  <div class="mt-3 grid grid-cols-[72px_1fr] gap-x-4 gap-y-1.5 text-[12px]">
    <span class="mono" style="color: var(--muted)">node id</span>
    <span class="mono nums">{presenter.status?.node_id ?? '—'}</span>
    <span class="mono" style="color: var(--muted)">ports</span>
    <span class="mono nums" style="color: var(--muted)">{ports} <span style="color: var(--muted)">(rpc LAN-only)</span></span>
  </div>

  <div class="mt-3 flex flex-col gap-1">
    <div class="flex items-center gap-2">
      <button
        type="button"
        class="btn btn-primary"
        disabled={!isMaster || !!presenter.busy}
        onclick={() => void presenter.updateWorkers()}
      >
        Update workers
      </button>
      {#if upd?.phase && upd.phase !== 'done'}
        <span class="mono text-[11px]" style="color: var(--muted)">
          {upd.phase ?? '…'}{#if (upd.pct ?? 0) > 0} · {Math.round(upd.pct ?? 0)}% {/if}
        </span>
      {/if}
    </div>
    {#if upd?.error}
      <ErrorMessage source="update" message={upd.error} />
    {/if}
    {#if upd?.workers?.length}
      {#each upd.workers as w}
        <div class="mono text-[10px]" style="color: var(--muted)">
          {w.host ?? w.node_id ?? '?'} <span style="color: var(--fg)"> {w.phase}</span>
          {#if w.error} <ErrorMessage source={`update-worker:${w.node_id ?? w.host}`} message={w.error} /> {/if}
        </div>
      {/each}
    {/if}
    <p class="mono text-[10px] mt-1" style="color: var(--muted)">
      Master only — git pull, smart-rebuild, rejoin.
    </p>
  </div>
</section>
