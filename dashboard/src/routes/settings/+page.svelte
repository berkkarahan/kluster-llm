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
  import LlamaRuntimes from '$lib/components/LlamaRuntimes.svelte';
  import { appStore } from '$lib/store.svelte';
  import type { HomePaths } from '$lib/types';

  let home = $state<HomePaths | null>(null);
  let section = $state('hardware');

  const live = $derived(appStore.viewState() === 'live');
  const sections = [
    { id: 'hardware', label: 'Hardware' },
    { id: 'network', label: 'Network' },
    { id: 'tokens', label: 'Tokens' },
    { id: 'software', label: 'Software' },
    { id: 'llama', label: 'llama.cpp' },
    { id: 'paths', label: 'Paths' }
  ];

  onMount(async () => {
    if (new URLSearchParams(location.search).get('section') === 'llama') section = 'llama';
    try {
      const r = await fetch('/v2/node/home');
      if (r.ok) home = (await r.json()) as HomePaths;
    } catch {
      /* ignore — data directory stays informational */
    }
  });
</script>

<div class="stack">
  <header class="page-heading">
    <div>
      <p class="eyebrow">Settings</p>
      <h1 id="page-title">Node settings</h1>
      <p>{section === 'llama' ? 'Version changes apply to the next model run. Preparation keeps active inference on its current version.' : 'Hardware, network, tokens, and software for this node. Changes apply on the next worker restart.'}</p>
    </div>
    <p class="mono small muted">node {appStore.status?.node_id ?? '—'} · probed {appStore.probedBackend}</p>
  </header>

  <div class="settings-layout">
    <nav class="settings-nav" aria-label="Settings sections">
      {#each sections as item}
        <button type="button" class:active={section === item.id} aria-current={section === item.id ? 'true' : undefined} onclick={() => section = item.id}>{item.label}</button>
      {/each}
    </nav>
    <div class="settings-body">
    <section class="panel panel-pad" aria-labelledby="hw-label" hidden={section !== 'hardware'}>
      <h2 id="hw-label">Hardware</h2>
      <HardwareSettings lockBackend={live} />
    </section>

    <div hidden={section !== 'network'}><NetworkSecurity /></div>
    <div hidden={section !== 'tokens'}><Tokens /></div>
    <div hidden={section !== 'software'}><Software /></div>
    {#if section === 'llama'}<LlamaRuntimes />{/if}

    <section class="panel panel-pad" aria-labelledby="data-label" hidden={section !== 'paths'}>
      <h2 id="data-label">Paths</h2>
      <p class="muted">Paths reported by /v2/node/home.</p>
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
</div>
