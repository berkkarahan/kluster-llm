<script lang="ts">
  import { createApp } from '#lib/app.svelte';
  /**
   * System Settings tab. Centralizes cluster runtime, bind, and secret
   * configuration. The top nav is provided by +layout.svelte, so this page only
   * renders the section column. Hardware & Compute reuses the existing
   * HardwareSettings control; the rest are dedicated panels.
   */
  import { onMount } from 'svelte';
  import HardwareSettings from '#lib/modules/node-settings/view/HardwareSettings.svelte';
  import NetworkSecurity from '#lib/modules/node-settings/view/NetworkSecurity.svelte';
  import Tokens from '#lib/modules/node-settings/view/Tokens.svelte';
  import Software from '#lib/modules/node-settings/view/Software.svelte';
  import FeatureFlags from '#lib/modules/node-settings/view/FeatureFlags.svelte';
  import LlamaRuntimes from '#lib/modules/runtimes/view/LlamaRuntimes.svelte';
  import PrimaRuntimes from '#lib/modules/runtimes/view/PrimaRuntimes.svelte';
  import { resolveActiveEngine } from '#lib/modules/run/entities';
  import type { App } from '#lib/app.svelte';
  let {
    settings = createApp().settings,
    cluster = createApp().cluster,
    runtimes = createApp().runtimes,
    run = createApp().run
  }: {
    settings?: App['settings'];
    cluster?: App['cluster'];
    runtimes?: App['runtimes'];
    run?: App['run'];
  } = $props();

  let section = $state('hardware');

  const live = $derived(cluster.viewState() === 'live');
  const sections = [
    { id: 'hardware', label: 'Hardware' },
    { id: 'network', label: 'Network' },
    { id: 'tokens', label: 'Tokens' },
    { id: 'software', label: 'Software' },
    { id: 'llama', label: 'llama.cpp' },
    { id: 'paths', label: 'Paths' },
    { id: 'flags', label: 'Feature flags' }
  ];

  /**
   * Prima launches replace only the runtime section: the llama.cpp panel keeps
   * its engine indicator and the llama.cpp settings stay available through
   * /settings?section=llama for a llama.cpp library. The signal is the active
   * engine state the agent already reports (the launch flag feeds it), so no
   * second source of truth is introduced here.
   */
  const activeEngine = $derived(
    resolveActiveEngine(
      cluster.status
        ? {
            ...cluster.status,
            runtimes: runtimes.status?.runtimes ?? cluster.status.runtimes
          }
        : runtimes.status,
      run.activeRunConfig,
      run.planResult
    )
  );
  const primaMode = $derived(activeEngine === 'primacpp');
  const runtimeSectionLabel = $derived(primaMode ? 'Prima' : 'llama.cpp');
  const runtimeIntro = $derived(
    primaMode
      ? 'Prima replaces llama.cpp for this launch. Evaluation builds and placement previews only; inference stays blocked until every qualification gate passes.'
      : 'Version changes apply to the next model run. Preparation keeps active inference on its current version.'
  );
  const sectionIntro = $derived(
    section === 'llama'
      ? runtimeIntro
      : section === 'flags'
        ? 'Feature flags toggle experimental workflows immediately without restarting the node.'
        : 'Hardware, network, tokens, and software for this node. Changes apply on the next worker restart.'
  );

  onMount(async () => {
    const requested = new URLSearchParams(location.search).get('section');
    if (requested === 'llama' || requested === 'prima') section = 'llama';
    else if (requested && sections.some((s) => s.id === requested)) section = requested;
    void settings.loadHome();
    void settings.loadFeatureFlags();
  });
</script>

<div class="stack">
  <header class="page-heading">
    <div>
      <p class="eyebrow">Settings</p>
      <h1 id="page-title">Node settings</h1>
      <p>{sectionIntro}</p>
    </div>
    <p class="mono small muted">
      node {cluster.status?.node_id ?? '—'} · probed {settings.probedBackend}
      {#if primaMode}<span class="chip chip-info">Prima engine</span>{/if}
    </p>
  </header>

  <div class="settings-layout">
    <nav class="settings-nav" aria-label="Settings sections">
      {#each sections as item}
        <button type="button" class:active={section === item.id} aria-current={section === item.id ? 'true' : undefined} onclick={() => section = item.id}>{item.id === 'llama' ? runtimeSectionLabel : item.label}</button>
      {/each}
    </nav>
    <div class="settings-body">
    <section class="panel panel-pad" aria-labelledby="hw-label" hidden={section !== 'hardware'}>
      <h2 id="hw-label">Hardware</h2>
      <HardwareSettings presenter={settings} lockBackend={live} />
    </section>

    <div hidden={section !== 'network'}><NetworkSecurity presenter={cluster} /></div>
    <div hidden={section !== 'tokens'}><Tokens /></div>
    <div hidden={section !== 'software'}><Software presenter={cluster} /></div>
    {#if section === 'llama'}{#if primaMode}<PrimaRuntimes presenter={runtimes} />{:else}<LlamaRuntimes presenter={runtimes} />{/if}{/if}

    <section class="panel panel-pad" aria-labelledby="data-label" hidden={section !== 'paths'}>
      <h2 id="data-label">Paths</h2>
      <p class="muted">Paths reported by /v2/node/home.</p>
      <div class="mono text-[12px] nums">
        {#if settings.home}
          {#each [{ label: 'home', path: settings.home.home }, { label: 'models', path: settings.home.models }, { label: 'logs', path: settings.home.logs }] as item (item.label)}
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

    <section class="panel panel-pad" aria-labelledby="flags-label" hidden={section !== 'flags'}>
      <h2 id="flags-label">Feature flags</h2>
      <FeatureFlags presenter={settings} />
    </section>
    </div>
  </div>
</div>
