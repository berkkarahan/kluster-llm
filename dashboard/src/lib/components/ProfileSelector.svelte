<script lang="ts">
  /**
   * Profile selector dropdown: manage llama.cpp release profiles.
   * Allows switching between pinned versions and backends, with build support.
   */
  import { appStore } from '$lib/store.svelte';

  type Props = {};
  let {}: Props = $props();

  function statusIcon(status: string): string {
    switch (status) {
      case 'installed': return '✓';
      case 'missing': return '⚡';
      case 'error': return '✗';
      default: return '?';
    }
  }

  function statusColor(status: string): string {
    switch (status) {
      case 'installed': return 'var(--success, #4a6)';
      case 'missing': return 'var(--warn, #b80)';
      case 'error': return 'var(--danger, #c44)';
      default: return 'var(--muted, #888)';
    }
  }

  async function handleBuild(profile: typeof appStore.profiles[0]) {
    if (!profile) return;
    await appStore.buildProfile(profile.name);
  }
</script>

<div class="profile-selector">
  <label class="mono text-[11px]" style="color: var(--muted)" for="profile-select">
    Inference Profile
  </label>
  <select
    id="profile-select"
    class="field mono"
    value={appStore.currentProfile}
    disabled={!!appStore.busy}
    onchange={(e) => {
      const v = (e.currentTarget as HTMLSelectElement).value;
      if (v) void appStore.setActiveProfile(v);
    }}
  >
    {#each appStore.profiles as p}
      <option value={p.name}>
        {p.name} — {p.tag}{p.backend !== 'auto' ? ' • ' + p.backend : ''}
      </option>
    {/each}
  </select>

  <div class="flex items-center gap-2 mt-1">
    {#if appStore.currentProfile}
      {#each appStore.profiles as p}
        {#if p.name === appStore.currentProfile}
          <span class="mono text-[10px]" style="color: {statusColor(p.build_status)}">
            {statusIcon(p.build_status)} {p.build_status}
          </span>
        {/if}
      {/each}
    {:else}
      <span class="mono text-[10px]" style="color: var(--muted)">
        No active profile
      </span>
    {/if}

    {#each appStore.profiles as p}
      {#if !p.is_active && p.build_status === 'missing'}
        <button
          class="btn btn-sm"
          style="font-size: 10px; padding: 2px 8px;"
          onclick={() => handleBuild(p)}
        >
          Build {p.name}
        </button>
      {/if}
    {/each}
  </div>

  <p class="mono text-[10px]" style="color: var(--muted)">
    Switch before starting the cluster. Each profile stores its own llama.cpp binary set.
  </p>
</div>
