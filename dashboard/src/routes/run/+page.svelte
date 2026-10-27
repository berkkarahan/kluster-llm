<script lang="ts">
  import { onMount, untrack } from 'svelte';
  import { createApp } from '#lib/app.svelte';
  import { resolveActiveEngine } from '#lib/modules/run/entities';
  import RunPage from '#lib/modules/run/view/RunPage.svelte';
  import PrimaRunPage from '#lib/modules/run/view/PrimaRunPage.svelte';

  const app = createApp();
  let queryEngine = $state<string | null>(null);

  onMount(() => {
    queryEngine = new URLSearchParams(location.search).get('engine');
  });

  const activeEngine = $derived(
    resolveActiveEngine(
      app.cluster.status
        ? {
            ...app.cluster.status,
            runtimes: app.runtimes.status?.runtimes ?? app.cluster.status.runtimes
          }
        : null,
      app.run.activeRunConfig,
      app.run.planResult,
      queryEngine
    )
  );
  $effect(() => {
    if (activeEngine !== 'primacpp' && app.run.settingsScope !== 'legacy')
      untrack(() => void app.run.setSettingsScope('legacy'));
  });
</script>

{#if activeEngine === 'primacpp'}
  <PrimaRunPage presenter={app.run} runtimes={app.runtimes} />
{:else}
  <RunPage presenter={app.run} />
{/if}
