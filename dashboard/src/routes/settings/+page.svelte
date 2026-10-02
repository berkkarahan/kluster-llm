<script lang="ts">
  /**
   * System Settings tab. Centralizes cluster runtime, bind, and secret
   * configuration. The top nav is provided by +layout.svelte, so this page only
   * renders the section column. Hardware & Compute reuses the existing
   * HardwareSettings control; the rest are dedicated panels.
   */
  import { onMount } from 'svelte';
  import HardwareSettings from '$lib/components/HardwareSettings.svelte';
  import NetworkSecurity from '$lib/components/NetworkSecurity.svelte';
  import Tokens from '$lib/components/Tokens.svelte';
  import Software from '$lib/components/Software.svelte';
  import { appStore } from '$lib/store.svelte';
  import type { HomePaths } from '$lib/types';

  let home = $state<HomePaths | null>(null);

  const live = $derived(appStore.viewState() === 'live');

  onMount(async () => {
    try {
      const r = await fetch('/v2/node/home');
      if (r.ok) home = (await r.json()) as HomePaths;
    } catch {
      /* ignore — data directory stays informational */
    }
  });
</script>

<div class="page-frame mx-auto w-full max-w-[960px] px-4 py-5">
  <div class="mb-4 flex flex-wrap items-end justify-between gap-2">
    <div>
      <h1 class="page-title">System Settings</h1>
      <p class="page-description">
        Cluster runtime, bind, and secret configuration. Applies on next worker restart.
      </p>
    </div>
    <p class="mono m-0 text-[11px] nums" style="color: var(--muted)">
      node {appStore.status?.node_id ?? '—'} · probed {appStore.probedBackend}
    </p>
  </div>

  <div class="flex flex-col gap-3">
    <section class="panel p-3.5" aria-labelledby="hw-label">
      <h2 id="hw-label" class="mono text-[11px] uppercase tracking-wider mb-3" style="color: var(--muted)">
        Hardware &amp; Compute
      </h2>
      <HardwareSettings lockBackend={live} />
    </section>

    <NetworkSecurity />
    <Tokens />
    <Software />

    <section class="panel p-3.5" aria-labelledby="data-label">
      <h2 id="data-label" class="mono text-[11px] uppercase tracking-wider mb-2" style="color: var(--muted)">
        Data directory
      </h2>
      <div class="mono text-[12px] nums">
        {#if home}
          {#each [{ label: 'home', path: home.home }, { label: 'models', path: home.models }, { label: 'logs', path: home.logs }] as item (item.label)}
            <div class="flex items-baseline justify-between gap-4 py-1 border-b" style="border-color: var(--border)">
              <span style="color: var(--muted)">{item.label}</span>
              <span class="min-w-0 text-right break-all">{item.path}</span>
            </div>
          {/each}
        {:else}
          <div class="flex items-baseline justify-between gap-4 py-1 border-b" style="border-color: var(--border)">
            <span style="color: var(--muted)">home</span>
            <span>~/.kllm</span>
          </div>
        {/if}
      </div>
    </section>
  </div>
</div>
