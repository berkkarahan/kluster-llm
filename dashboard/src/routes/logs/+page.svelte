<script module lang="ts">
  import type { LogTrailState } from '$lib/log-trail.svelte';
  import type { LogTag } from '$lib/types';

  /** List copy when nothing is visible. Tag sentences apply only when the buffer holds rows. */
  export function logListEmptyCopy(input: {
    state: LogTrailState;
    buffered: number;
    selectedNodeId: string;
    selectedTag: '' | LogTag;
  }): string {
    if (input.state === 'cleared') return 'Display cleared — waiting for a new entry.';
    if (input.buffered > 0) {
      if (input.selectedNodeId && input.selectedTag) {
        return 'No entries match the selected node and tag in the current buffer.';
      }
      if (input.selectedTag) return 'No entries for the selected tag in the current buffer.';
      if (input.selectedNodeId) return 'No entries for the selected node in the current buffer.';
    } else if (!input.selectedTag && input.selectedNodeId) {
      return 'No entries for the selected node in the current buffer.';
    }
    return 'No log entries yet.';
  }
</script>

<script lang="ts">
  import { onMount } from 'svelte';
  import { appStore } from '$lib/store.svelte';
  import { LogTrail, REFRESH_MODES, REFRESH_MS } from '$lib/log-trail.svelte';
  import type { RefreshMode } from '$lib/types';
  import { pushToast } from '$lib/toast';

  const trail = new LogTrail();
  let changingLogging = $state(false);
  const logging = $derived(appStore.status?.logging);
  $effect(() => { trail.setEnabled(logging?.enabled !== false); });

  async function toggleLogging() {
    changingLogging = true;
    try {
      const response = await fetch('/v2/cluster/logging', {
        method: 'PUT', headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${appStore.apiToken}` },
        body: JSON.stringify({ enabled: logging?.enabled === false })
      });
      if (!response.ok) throw new Error(`Logging control failed (${response.status})`);
      const result = await response.json();
      if (appStore.status) appStore.status = { ...appStore.status, logging: result };
    } catch (error) { pushToast('danger', String(error)); }
    finally { changingLogging = false; }
  }

  const MODE_LABELS: Record<RefreshMode, string> = {
    live: 'Live',
    '5s': '5s',
    '30s': '30s',
    '1m': '1m'
  };

  const TAG_LABELS = {
    ui: 'UI',
    inference: 'Inference',
    unclassified: 'Unclassified'
  } as const;

  let scroller = $state<HTMLDivElement | null>(null);
  /** True while the viewport is pinned to the newest row at the top. */
  let following = $state(true);
  let anchorId = $state<string | null>(null);
  let anchorOffset = 0;

  onMount(() => {
    trail.start();
    return () => trail.stop();
  });

  const nodeOptions = $derived.by(() => {
    const options = new Map<string, string>();
    const members = appStore.status?.members ?? {};
    for (const [id, member] of Object.entries(members)) {
      options.set(id, `${member.role ?? 'node'} · ${member.host ?? id}`);
    }
    for (const entry of trail.entries) {
      if (!options.has(entry.node_id)) options.set(entry.node_id, entry.node_id);
    }
    if (trail.selectedNodeId && !options.has(trail.selectedNodeId)) {
      options.set(trail.selectedNodeId, trail.selectedNodeId);
    }
    return [...options.entries()].map(([id, label]) => ({ id, label }));
  });

  const visible = $derived(trail.visibleEntries());
  /** Receipt order is oldest first; the page paints the latest accepted row first. */
  const visibleNewestFirst = $derived([...visible].reverse());
  const newerMode = $derived(trail.refreshMode !== 'live');

  $effect(() => {
    // Newest rows are prepended. Stay at the top while following; otherwise
    // keep a keyed row's viewport offset. If retention removed it, align the
    // oldest row still available.
    const rows = visibleNewestFirst;
    const newestId = rows[0]?.event_id;
    const oldestId = rows.at(-1)?.event_id;
    const el = scroller;
    if (!el || !newestId || !oldestId) return;
    // Room below the oldest row so a trimmed anchor can sit at the top.
    el.style.setProperty('--logs-tail', `${el.clientHeight}px`);
    if (following) {
      el.scrollTop = 0;
      if (anchorId !== null) anchorId = null;
      return;
    }
    const items = [...el.querySelectorAll<HTMLElement>('.logs-row')];
    const anchor = items.find((row) => row.dataset.eventId === anchorId);
    const scrollerTop = el.getBoundingClientRect().top;
    if (anchor) {
      const offset = anchor.getBoundingClientRect().top - scrollerTop;
      el.scrollTop += offset - anchorOffset;
    } else if (items.length) {
      const oldest = items.at(-1);
      if (!oldest) return;
      el.scrollTop += oldest.getBoundingClientRect().top - scrollerTop;
      anchorId = oldest.dataset.eventId ?? null;
      anchorOffset = 0;
    }
  });

  function captureAnchor(): void {
    const el = scroller;
    if (!el) return;
    const top = el.getBoundingClientRect().top;
    const firstVisible = [...el.querySelectorAll<HTMLElement>('.logs-row')]
      .find((row) => row.getBoundingClientRect().bottom > top + 1);
    anchorId = firstVisible?.dataset.eventId ?? null;
    anchorOffset = firstVisible ? firstVisible.getBoundingClientRect().top - top : 0;
  }

  function utcText(ts: number): string {
    const date = new Date(ts * 1000);
    if (!Number.isFinite(date.getTime())) return '—';
    return `${date.toISOString().slice(11, 23)}Z`;
  }

  function emptyText(): string {
    return logListEmptyCopy({
      state: trail.state,
      buffered: trail.entries.length,
      selectedNodeId: trail.selectedNodeId,
      selectedTag: trail.selectedTag
    });
  }

  function statusText(): string {
    if (trail.paused) return logging?.status === 'applied' ? 'Logging stopped' : 'Stopping logging — some nodes have not acknowledged';
    if (trail.state === 'error') {
      const lastSuccess = trail.lastSuccessAt === null
        ? 'never'
        : new Date(trail.lastSuccessAt).toISOString();
      return `${trail.errorText || 'Log read failed'} · last success ${lastSuccess}`;
    }
    if (trail.state === 'loading') return 'Loading logs…';
    if (trail.state === 'cleared') return 'Display cleared — waiting for a new entry';
    if (trail.state === 'empty') return 'Connected — no log entries';
    if (trail.coverage === 'local') return 'Local only — master unreachable';
    return 'Streaming cluster logs';
  }
</script>

<div class="logs-page p-3 flex flex-col gap-3 min-h-0">
  <header class="flex items-end justify-between gap-3 flex-wrap">
    <div>
      <h1 class="text-[15px] font-semibold tracking-wide m-0">Cluster Log Trail</h1>
      <p class="m-0 text-[11px]" style="color: var(--muted)">
        Master and worker activity · times in UTC · Stop logging discards new output across the cluster; inference continues.
      </p>
    </div>
    <span class="chip chip-muted mono">{trail.entries.length} buffered</span>
  </header>

  <section class="panel p-2 flex flex-wrap items-end gap-3" aria-label="Log controls">
    <button class="btn" disabled={changingLogging || !logging || appStore.status?.role !== 'master'} onclick={toggleLogging}>
      {logging?.enabled === false ? 'Resume logging' : 'Stop logging'}
    </button>
    {#if appStore.status?.role === 'worker'}<span class="text-[11px]">Change cluster logging on the master dashboard.</span>{/if}
    {#if logging?.status === 'partial'}<span class="text-[11px]" role="status">
      {Object.entries(logging.nodes).filter(([, node]) => node.status !== 'applied').map(([id, node]) => `${id}: ${node.status}`).join(' · ')}
    </span>{/if}
    <label class="flex flex-col gap-1">
      <span class="text-[10px] uppercase tracking-wider" style="color: var(--muted)">Node</span>
      <select class="field mono" aria-label="Filter by node" bind:value={trail.selectedNodeId}>
        <option value="">All nodes</option>
        {#each nodeOptions as option (option.id)}
          <option value={option.id}>{option.label}</option>
        {/each}
      </select>
    </label>

    <label class="flex flex-col gap-1">
      <span class="text-[10px] uppercase tracking-wider" style="color: var(--muted)">Tag</span>
      <select class="field mono" aria-label="Filter by tag" bind:value={trail.selectedTag}>
        <option value="">All tags</option>
        <option value="ui">UI</option>
        <option value="inference">Inference</option>
        <option value="unclassified">Unclassified</option>
      </select>
    </label>

    <fieldset class="m-0 p-0 border-0">
      <legend class="text-[10px] uppercase tracking-wider mb-1" style="color: var(--muted)">
        Refresh
      </legend>
      <div class="flex" role="radiogroup" aria-label="Refresh interval">
        {#each REFRESH_MODES as mode (mode)}
          <label class={`btn btn-mode mono text-[12px] ${trail.refreshMode === mode ? 'btn-accent' : ''}`}>
            <input
              type="radio"
              name="refresh"
              value={mode}
              checked={trail.refreshMode === mode}
              class="sr-only"
              onchange={() => trail.setMode(mode)}
            />
            <span>{MODE_LABELS[mode]}</span>
          </label>
        {/each}
      </div>
    </fieldset>

    <button
      type="button"
      class="btn"
      aria-describedby="clear-help"
      onclick={() => trail.clear()}
    >
      Clear
    </button>
    <p id="clear-help" class="m-0 text-[11px]" style="color: var(--muted)">
      Clears this display only; stored logs are unchanged.
    </p>
  </section>

  <div class="flex items-center gap-2 flex-wrap text-[12px]">
    {#if trail.refreshMode === 'live' && !trail.paused}
      <span class="chip chip-muted mono" title="Connection delivery is separate from log source coverage">
        updates {appStore.deliveryHealth}
      </span>
    {/if}
    <span
      class="chip"
      class:chip-ok={trail.state === 'ready' || trail.state === 'empty'}
      class:chip-warn={trail.coverage === 'local' || trail.gap}
      class:chip-danger={trail.state === 'error'}
      role="status"
      aria-live="polite"
    >
      {statusText()}
    </span>
    <span class="chip chip-muted mono">{trail.refreshMode === 'live'
      ? 'Live'
      : `${MODE_LABELS[trail.refreshMode]} · ${REFRESH_MS[trail.refreshMode] / 1000}s`}</span>
    {#if trail.gap}
      <span class="chip chip-warn" role="note">history gap — older entries omitted</span>
    {/if}
    {#if trail.omittedRows > 0}
      <span class="chip chip-warn mono">trimmed {trail.omittedRows}</span>
    {/if}
    {#if newerMode}
      <span class="sr-only">Only the selected interval updates this view; it is not live.</span>
    {/if}
    {#if trail.warning}
      <span class="text-[11px]" style="color: var(--warn)">{trail.warning}</span>
    {/if}
  </div>

  <section class="panel flex-1 min-h-0 flex flex-col overflow-hidden" aria-label="Log entries">
    <div class="logs-head mono text-[10px] uppercase tracking-wider grid shrink-0" style="color: var(--muted)">
      <span>UTC</span>
      <span>Node</span>
      <span>Source</span>
      <span>Tag</span>
      <span>Message</span>
    </div>
    <!-- svelte-ignore a11y_no_noninteractive_tabindex -->
    <!-- role=log is noninteractive by ARIA, but this scroll container needs
         keyboard focus so an operator can read back older entries with the
         keyboard; announcements stay off so new lines are not read aloud. -->
    <div
      class="logs-scroll mono text-[11px] flex-1 min-h-0 overflow-auto"
      bind:this={scroller}
      role="log"
      tabindex="0"
      aria-label="Scrollable cluster log entries"
      aria-live="off"
      onscroll={() => {
        const el = scroller;
        if (el) {
          following = el.scrollTop < 24;
          if (!following) captureAnchor();
        }
      }}
    >
      {#if trail.state === 'loading' && !visible.length}
        <p class="p-3 m-0" style="color: var(--muted)">Loading logs…</p>
      {:else if visible.length === 0}
        <p class="p-3 m-0" style="color: var(--muted)">{emptyText()}</p>
      {:else}
        {#each visibleNewestFirst as entry (entry.event_id)}
          <div class="logs-row grid" data-event-id={entry.event_id}>
            <time class="logs-ts" datetime={new Date(entry.ts * 1000).toISOString()}>{utcText(entry.ts)}</time>
            <span class="logs-node">{entry.node_id}</span>
            <span class="logs-source">{entry.source}</span>
            <span class="logs-tag">{TAG_LABELS[entry.tag]}</span>
            <span class="logs-msg">{entry.line}</span>
          </div>
        {/each}
      {/if}
    </div>
  </section>
</div>

<style>
  .logs-page {
    height: 100%;
    overflow: hidden;
  }

  .logs-scroll::after {
    content: '';
    display: block;
    height: var(--logs-tail, 100%);
  }

  .btn-mode:has(input:focus-visible) {
    outline: 2px solid var(--accent);
    outline-offset: 2px;
  }

  .btn-mode:has(input:checked) {
    border-color: color-mix(in srgb, var(--accent) 50%, var(--border));
    color: var(--accent);
  }

  .logs-page select:focus-visible {
    outline: 2px solid var(--accent);
    outline-offset: 2px;
  }

  .logs-head,
  .logs-row {
    grid-template-columns: 96px minmax(72px, 120px) 52px minmax(88px, 120px) minmax(0, 1fr);
    column-gap: 10px;
    align-items: start;
  }

  .logs-head {
    padding: 6px 10px;
    border-bottom: 1px solid var(--border);
    background: var(--surface-2);
  }

  .logs-row {
    padding: 5px 10px;
    border-bottom: 1px solid color-mix(in srgb, var(--border) 60%, transparent);
  }

  .logs-row:nth-child(even) {
    background: color-mix(in srgb, var(--surface-2) 40%, transparent);
  }

  .logs-ts {
    color: var(--muted);
  }

  .logs-source,
  .logs-tag {
    color: var(--muted);
  }

  .logs-source {
    text-transform: uppercase;
  }

  .logs-msg {
    min-width: 0;
    overflow-wrap: anywhere;
  }

  @media (max-width: 700px) {
    .logs-head {
      display: none;
    }

    .logs-row {
      grid-template-columns: minmax(0, 1fr) auto;
      row-gap: 2px;
      padding: 8px 10px;
    }

    .logs-ts {
      grid-column: 1;
    }

    .logs-source,
    .logs-tag {
      grid-column: 2;
      text-align: right;
    }

    .logs-node {
      grid-column: 1;
      color: var(--muted);
    }

    .logs-tag {
      grid-row: 2;
    }

    .logs-msg {
      grid-column: 1 / -1;
    }
  }
</style>
