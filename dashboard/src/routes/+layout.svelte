<script lang="ts">
  import '../app.css';
  import { onMount } from 'svelte';
  import { page } from '$app/state';
  import { appStore } from '$lib/store.svelte';
  import { toasts } from '$lib/toast';

  let { children } = $props();

  onMount(() => {
    appStore.start();
    return () => appStore.stop();
  });

  const nav = [
    { href: '/', label: 'Home' },
    { href: '/models', label: 'Models' },
    { href: '/run', label: 'Plan & Run' },
    { href: '/integrations', label: 'Connect' }
  ];
</script>

<div class="min-h-screen flex flex-col">
  <header
    class="h-10 shrink-0 flex items-center gap-4 px-3 border-b panel"
    style="border-color: var(--border); background: var(--surface)"
  >
    <a href="/" class="mono font-semibold tracking-wide" style="color: var(--accent); text-decoration: none"
      >kllm</a
    >
    <nav class="flex gap-3">
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
    <div class="ml-auto flex items-center gap-2">
      {#if appStore.connected}
        <span class="chip chip-ok">online</span>
      {:else}
        <span class="chip chip-danger">offline</span>
      {/if}
      <span class="chip chip-muted mono">{appStore.status?.role ?? '…'}</span>
      {#if appStore.status?.inference_up}
        <span class="chip chip-ok">infer</span>
      {/if}
    </div>
  </header>

  <main class="flex-1 min-h-0">
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
