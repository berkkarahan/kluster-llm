<script lang="ts">
  import '../app.css';
  import { onMount } from 'svelte';
  import { page } from '$app/state';
  import { appStore } from '$lib/store.svelte';
  import InferenceActivity from '$lib/components/InferenceActivity.svelte';
  import { toasts } from '$lib/toast';

  let { children } = $props();

  onMount(() => {
    appStore.start();
    return () => appStore.stop();
  });

  $effect(() => appStore.setUsageInterest(page.url.pathname === '/'));

  const nav = [
    { href: '/', label: 'Home' },
    { href: '/models', label: 'Models' },
    { href: '/run', label: 'Plan & Run' },
    { href: '/integrations', label: 'Connect' },
    { href: '/logs', label: 'Logs' },
    { href: '/settings', label: 'Settings' }
  ];
</script>

<div class="app-shell">
  <header
    class="dashboard-header min-h-10 shrink-0 flex items-center gap-4 px-3 border-b panel"
    style="border-color: var(--border); background: var(--surface)"
  >
    <a href="/" class="mono font-semibold tracking-wide" style="color: var(--accent); text-decoration: none"
      >kllm</a
    >
    <nav class="flex gap-3 min-w-0 overflow-x-auto">
      {#each nav as item}
        <a
          href={item.href}
          class="mono text-[12px] uppercase tracking-wider"
          style:color={page.url.pathname === item.href ? 'var(--accent)' : 'var(--muted)'}
          style:text-decoration="none"
        >
          {item.label}
        </a>
      {/each}
    </nav>
    <div class="ml-auto flex flex-wrap items-center justify-end gap-2 min-w-0">
      {#if appStore.connected}
        <span class="chip chip-ok">online</span>
      {:else}
        <span class="chip chip-danger">offline</span>
      {/if}
      {#if appStore.deliveryHealth === 'live'}
        <span class="chip chip-ok">live</span>
      {:else if appStore.deliveryHealth === 'loading'}
        <span class="chip chip-muted">updates loading</span>
      {:else if appStore.deliveryHealth === 'periodic-refresh'}
        <span class="chip chip-warn">refresh</span>
      {:else if appStore.deliveryHealth === 'unavailable'}
        <span class="chip chip-danger" title={appStore.deliveryError}>updates unavailable</span>
      {:else if appStore.deliveryHealth === 'reconnecting'}
        <span class="chip chip-warn">reconnecting</span>
      {:else if appStore.deliveryHealth === 'paused'}
        <span class="chip chip-muted">updates paused</span>
      {/if}
      {#each Object.entries(appStore.areaHealth) as [topic, health]}
        {#if health.availability === 'denied' || health.availability === 'unavailable'}
          <span class="chip chip-danger" role="status" aria-live="polite" style="white-space:normal;overflow-wrap:anywhere">
            {topic} {health.availability}: {health.error?.message ?? 'source unavailable'} · last success {health.last_success_at ?? 'never'}
          </span>
        {:else if (topic === 'status' || topic === 'usage') && health.coverage === 'local_with_cluster_view'}
          <span class="chip chip-warn" title="Local worker view with mirrored cluster information">{topic} mirrored</span>
        {:else if (topic === 'status' || topic === 'usage') && health.coverage === 'local'}
          <span class="chip chip-muted" title="This area contains local-node coverage only">{topic} local</span>
        {/if}
      {/each}
      <span class="chip chip-muted mono">{appStore.status?.role ?? (appStore.statusSeen ? '…' : 'loading')}</span>
      {#if appStore.downloadJob && appStore.downloadJob.state !== 'idle'}
        {#if appStore.downloadJob.state === 'running'}
          <span class="chip chip-ok">dl {Math.round(appStore.downloadJob.pct ?? 0)}%</span>
        {:else if appStore.downloadJob.state === 'complete'}
          <span class="chip chip-ok">dl ✓</span>
        {:else if appStore.downloadJob.state === 'error'}
          <span class="chip chip-danger">dl !</span>
        {:else if appStore.downloadJob.state === 'unknown'}
          <span class="chip chip-warn">dl ?</span>
        {:else}
          <span class="chip chip-muted">dl cancelled</span>
        {/if}
      {:else if appStore.status?.download}
        <span class="chip chip-ok">dl {Math.round(appStore.status.download.pct)}%</span>
      {/if}
      {#if appStore.loadProgress && (appStore.loadProgress.phase === 'starting' || appStore.loadProgress.phase === 'loading')}
        <span class="chip chip-warn">load {Math.round(appStore.loadProgress.fraction * 100)}%</span>
      {:else if appStore.loadProgress?.phase === 'ready'}
        <span class="chip chip-ok">load ✓</span>
      {/if}
      {#if appStore.status?.update && appStore.status.update.phase && appStore.status.update.phase !== 'done'}
        <span class="chip chip-warn">upd {Math.round(appStore.status.update.pct ?? 0)}%</span>
      {/if}
      {#if appStore.status?.inference_activity && appStore.status.inference_activity.phase !== 'idle'}
        <InferenceActivity activity={appStore.status.inference_activity} />
      {:else if appStore.status?.inference_up}
        <span class="chip chip-ok">infer</span>
      {/if}
    </div>
  </header>

  <main class="app-main">
    {@render children()}
  </main>

  <div class="fixed bottom-3 right-3 flex flex-col gap-2 z-50 max-w-sm">
    {#each $toasts as t (t.id)}
      <div
        class="panel px-3 py-2 mono text-[12px]"
        style:border-color={t.kind === 'danger'
          ? 'var(--danger)'
          : t.kind === 'warn'
            ? 'var(--warn)'
            : 'var(--accent)'}
      >
        {t.text}
      </div>
    {/each}
  </div>
</div>
