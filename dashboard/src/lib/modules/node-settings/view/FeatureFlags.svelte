<script lang="ts">
  import { FEATURE_FLAG_CATALOG } from '#lib/modules/node-settings/entities';
  import type { NodeSettingsInteractor } from '#lib/modules/node-settings/interactor.svelte';

  let { presenter }: { presenter: NodeSettingsInteractor } = $props();
</script>

<div class="stack" data-testid="feature-flags-panel">
  <p class="muted">
    Toggle experimental capabilities on the go. Changes persist in the node database and take effect immediately without restarting the worker.
  </p>

  {#if presenter.flagError}
    <div class="notice notice-danger" role="alert" data-testid="feature-flags-error">
      {presenter.flagError}
    </div>
  {/if}

  <div class="stack">
    {#each FEATURE_FLAG_CATALOG as flag (flag.id)}
      {@const enabled = presenter.isFeatureFlagEnabled(flag.id)}
      {@const pending = Boolean(presenter.pendingFlags[flag.id])}
      <div
        class="flex items-center justify-between gap-4 py-3 border-b"
        style="border-color: var(--border)"
        data-testid={`flag-row-${flag.id}`}
      >
        <div class="min-w-0">
          <div class="flex items-center gap-2">
            <strong>{flag.label}</strong>
            <span class="mono small muted">{flag.id}</span>
            <span class="chip {enabled ? 'chip-ok' : 'chip-muted'}">
              {enabled ? 'enabled' : 'disabled'}
            </span>
          </div>
          <p class="small muted mt-1">{flag.description}</p>
        </div>

        <button
          type="button"
          role="switch"
          aria-checked={enabled}
          aria-busy={pending}
          aria-label={`Toggle ${flag.label}`}
          data-testid={`flag-toggle-${flag.id}`}
          class="btn {enabled ? 'btn-primary' : 'btn-ghost'}"
          disabled={pending}
          onclick={() => void presenter.setFeatureFlag(flag.id, !enabled)}
        >
          {#if pending}
            Saving…
          {:else if enabled}
            Disable
          {:else}
            Enable
          {/if}
        </button>
      </div>
    {/each}
  </div>
</div>
