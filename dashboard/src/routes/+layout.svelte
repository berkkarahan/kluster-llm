<script lang="ts">
  import { createApp } from '#lib/app.svelte';
  import { routes } from '#lib/shared/navigation';
  import '../app.css';
  import '../prototype.css';
  import { onMount } from 'svelte';
  import { page } from '$app/state';
  import InferenceActivity from '#lib/modules/usage/view/InferenceActivity.svelte';
  import Icon from '#lib/shared/Icon.svelte';
  import { toasts, dismissToast } from '#lib/shared/toast';
  import ErrorMessage from '#lib/shared/ErrorMessage.svelte';
  const app = createApp();
  const cluster = app.cluster;
  const models = app.models;
  const live = app.live;
  const run = app.run;

  let { children } = $props();
  let menuOpen = $state(false);
  let narrowView = $state(false);
  let sidebarEl = $state<HTMLElement | null>(null);

  const nav = [
    { href: routes.cluster, label: 'Cluster', icon: 'cluster' },
    { href: routes.models, label: 'Models', icon: 'cube' },
    { href: routes.run, label: 'Deploy', icon: 'rocket' },
    { href: routes.logs, label: 'Activity', icon: 'activity' },
    { href: routes.integrations, label: 'Connect', icon: 'plug' },
    { href: routes.settings, label: 'Settings', icon: 'settings' }
  ];

  const current = $derived(nav.find((item) => item.href === page.url.pathname) ?? nav[0]);
  const memberCount = $derived(Object.keys(cluster.status?.members ?? {}).length);
  const modelCount = $derived(models.models.length);
  const role = $derived(cluster.status?.role ?? '');
  const view = $derived(cluster.viewState());
  const nodeLabel = $derived(cluster.status?.node_id || 'This node');
  const connectionLabel = $derived.by(() => {
    if (!live.connected) return 'Offline';
    if (live.deliveryHealth === 'reconnecting') return 'Reconnecting';
    if (live.deliveryHealth === 'periodic-refresh') return 'Periodic refresh';
    if (live.deliveryHealth === 'live') return 'Live';
    if (live.deliveryHealth === 'loading') return 'Loading';
    if (live.deliveryHealth === 'paused') return 'Updates paused';
    return 'Updates unavailable';
  });
  const roleLabel = $derived.by(() => {
    if (view === 'loading') return 'Connecting';
    if (role === 'master') return 'Master node';
    if (role === 'worker') return 'Worker node';
    return 'Cluster stopped';
  });

  function countFor(href: string): number | null {
    if (href === '/') return memberCount || null;
    if (href === '/models') return modelCount || null;
    return null;
  }

  function closeMenu() {
    menuOpen = false;
  }

  onMount(() => {
    app.start();
    const media = window.matchMedia('(max-width: 767px)');
    const syncNarrow = () => {
      narrowView = media.matches;
      if (!media.matches) menuOpen = false;
    };
    syncNarrow();
    media.addEventListener('change', syncNarrow);
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape' && menuOpen && !document.querySelector('dialog[open]')) {
        closeMenu();
        document.getElementById('menu-toggle')?.focus();
      }
      if (event.key !== 'Tab' || !menuOpen || !narrowView || !sidebarEl) return;
      const items = [...sidebarEl.querySelectorAll<HTMLElement>('a, button')].filter(
        (item) => !item.hasAttribute('disabled')
      );
      if (!items.length) return;
      const first = items[0];
      const last = items[items.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    };
    document.addEventListener('keydown', onKey);
    return () => {
      media.removeEventListener('change', syncNarrow);
      document.removeEventListener('keydown', onKey);
      app.stop();
    };
  });

  $effect(() => {
    live.setUsageInterest(page.url.pathname === '/');
  });

  $effect(() => {
    page.url.pathname;
    menuOpen = false;
  });
</script>

<div class="app app-shell dashboard-shell">
  <a class="skip-link" href="#main-content">Skip to content</a>
  <aside
    id="sidebar"
    class="sidebar"
    class:open={menuOpen}
    bind:this={sidebarEl}
    inert={narrowView && !menuOpen ? true : undefined}
    aria-label="Main navigation"
  >
    <div class="od-row" style="--od-gap: 8px">
      <a class="brand" href="/" style="flex: 1">
        <span class="brand-glyph" aria-hidden="true"><i></i><i></i><i></i><i></i></span>
        <span class="brand-word">kluster <small>llm</small></span>
      </a>
      <button class="btn icon-btn ghost mobile-close" type="button" aria-label="Close menu" onclick={closeMenu}>
        <Icon name="close" />
      </button>
    </div>
    <a class="workspace-picker" href={routes.settings}>
      <span class="workspace-avatar">{nodeLabel.slice(0, 2).toUpperCase()}</span>
      <span class="od-field od-fill">
        <strong>{nodeLabel}</strong>
        <small class="muted">{roleLabel}</small>
      </span>
      <Icon name="chevron" small />
    </a>
    <nav class="navigation" aria-label="Sections">
      <div class="nav-caption">Workspace</div>
      {#each nav as item}
        <a
          class="nav-link"
          class:active={page.url.pathname === item.href}
          href={item.href}
          aria-current={page.url.pathname === item.href ? 'page' : undefined}
          onclick={closeMenu}
        >
          <Icon name={item.icon} />
          {item.label}
          {#if countFor(item.href)}<span class="nav-count">{countFor(item.href)}</span>{/if}
        </a>
      {/each}
    </nav>
    <div class="sidebar-bottom">
      <div class="sidebar-note">
        <div class="connection-line">
          <span class="connection-dot" style:background={live.connected ? 'var(--accent)' : 'var(--danger)'}></span>
          {connectionLabel}
        </div>
        <p>{roleLabel}</p>
      </div>
    </div>
  </aside>
  <button class="scrim" class:open={menuOpen} type="button" aria-label="Close menu" onclick={closeMenu}></button>
  <div class="main-shell">
    <header class="topbar">
      <div class="od-row">
        <button
          id="menu-toggle"
          class="btn icon-btn ghost mobile-menu"
          type="button"
          aria-label="Open menu"
          aria-expanded={menuOpen}
          aria-controls="sidebar"
          onclick={() => (menuOpen = true)}
        >
          <Icon name="menu" />
        </button>
        <nav class="breadcrumbs" aria-label="Breadcrumb">
          <span class="parent-crumb">Local cluster</span>
          <Icon name="chevron" small />
          <strong>{current.label}</strong>
        </nav>
      </div>
      <div class="topbar-status shell-health" aria-label="Connection health">
        {#if live.connected}
          <span class="chip chip-ok">online</span>
        {:else}
          <span class="chip chip-danger">offline</span>
        {/if}
        {#if live.deliveryHealth === 'live'}
          <span class="chip chip-ok">live</span>
        {:else if live.deliveryHealth === 'loading'}
          <span class="chip chip-muted">updates loading</span>
        {:else if live.deliveryHealth === 'periodic-refresh'}
          <span class="chip chip-warn">refresh</span>
        {:else if live.deliveryHealth === 'unavailable'}
          <span class="chip chip-muted">updates unavailable</span>
          <ErrorMessage source="delivery" message={live.deliveryError} snapshot={false} />
        {:else if live.deliveryHealth === 'reconnecting'}
          <span class="chip chip-warn">reconnecting</span>
        {:else if live.deliveryHealth === 'paused'}
          <span class="chip chip-muted">updates paused</span>
        {/if}
        {#each Object.entries(live.areaHealth) as [topic, health]}
          {#if health.availability === 'denied' || health.availability === 'unavailable'}
            <span class="chip chip-muted">{topic} {health.availability}</span>
            <ErrorMessage source={`area:${topic}`} message={health.error?.message ?? 'source unavailable'} label={topic} />
          {:else if (topic === 'status' || topic === 'usage') && health.coverage === 'local_with_cluster_view'}
            <span class="chip chip-warn" title="Local worker view with mirrored cluster information">{topic} mirrored</span>
          {:else if (topic === 'status' || topic === 'usage') && health.coverage === 'local'}
            <span class="chip chip-muted" title="This area contains local-node coverage only">{topic} local</span>
          {/if}
        {/each}
        <span class="chip chip-muted mono">{cluster.status?.role ?? (cluster.statusSeen ? '…' : 'loading')}</span>
        {#if models.downloadJob && models.downloadJob.state !== 'idle'}
          {#if models.downloadJob.state === 'running'}
            <span class="chip chip-ok">dl {Math.round(models.downloadJob.pct ?? 0)}%</span>
          {:else if models.downloadJob.state === 'complete'}
            <span class="chip chip-ok">dl ✓</span>
          {:else if models.downloadJob.state === 'error'}
            <span class="chip chip-muted">dl stopped</span>
            <ErrorMessage source="download" message={models.downloadJob.error ?? 'unknown error'} identity={models.downloadJob.operation_id} />
          {:else if models.downloadJob.state === 'unknown'}
            <span class="chip chip-warn">dl ?</span>
          {:else}
            <span class="chip chip-muted">dl cancelled</span>
          {/if}
        {:else if cluster.status?.download}
          <span class="chip chip-ok">dl {Math.round(cluster.status.download.pct)}%</span>
        {/if}
        {#if run.loadProgress && (run.loadProgress.phase === 'starting' || run.loadProgress.phase === 'loading')}
          <span class="chip chip-warn">load {Math.round(run.loadProgress.fraction * 100)}%</span>
        {:else if run.loadProgress?.phase === 'ready'}
          <span class="chip chip-ok">load ✓</span>
        {/if}
        {#if cluster.status?.update && cluster.status.update.phase && cluster.status.update.phase !== 'done'}
          <span class="chip chip-warn">upd {Math.round(cluster.status.update.pct ?? 0)}%</span>
        {/if}
        {#if cluster.status?.inference_activity && cluster.status.inference_activity.phase !== 'idle'}
          <InferenceActivity activity={cluster.status.inference_activity} />
        {:else if cluster.status?.inference_up}
          <span class="chip chip-ok">infer</span>
        {/if}
      </div>
    </header>
    <main id="main-content" class="content app-main">
      {@render children()}
    </main>
  </div>
  <div class="toast-region toast-stack" aria-live="polite">
    {#each $toasts as t (t.id)}
      <div class="toast" style:border-color={t.kind === 'danger' ? 'var(--danger)' : t.kind === 'warn' ? 'var(--warning)' : 'var(--accent)'}>
        {#if t.kind === 'danger'}
          <ErrorMessage source={t.source} message={t.text} />
        {:else}
          <p>{t.text}</p>
          <button class="btn icon-btn ghost" type="button" aria-label="Dismiss notification" onclick={() => dismissToast(t.id)}>
            <Icon name="close" small />
          </button>
        {/if}
      </div>
    {/each}
  </div>
</div>

<style>
  .app-shell {
    height: 100dvh;
    overflow: hidden;
  }
  .main-shell {
    display: grid;
    grid-template-rows: auto minmax(0, 1fr);
    min-height: 0;
    height: 100dvh;
  }
  main.app-main {
    min-height: 0;
    overflow-y: auto;
  }
  aside.sidebar {
    overflow: auto;
  }
  @media (min-width: 768px) {
    aside.sidebar {
      transform: none !important;
    }
  }
</style>
