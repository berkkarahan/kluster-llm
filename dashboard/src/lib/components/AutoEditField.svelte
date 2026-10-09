<script lang="ts">
  import type { Snippet } from 'svelte';

  type Mode = 'auto' | 'edit';

  let {
    label,
    mode = 'auto' as Mode,
    disabled = false,
    onModeChange,
    children
  }: {
    label: string;
    mode?: Mode;
    disabled?: boolean;
    onModeChange?: (mode: Mode) => void;
    children?: Snippet;
  } = $props();
</script>

<div class="flex flex-col gap-1">
  <div class="flex items-center gap-2">
    <span class="mono text-[11px] flex-1" style="color: var(--muted)">{label}</span>
    <select
      aria-label={`${label} mode`}
      class="field mono"
      style="width: auto; min-width: 5.5rem"
      value={mode}
      {disabled}
      onchange={(e) => onModeChange?.((e.currentTarget as HTMLSelectElement).value as Mode)}
    >
      <option value="auto">Auto</option>
      <option value="edit">Edit</option>
    </select>
  </div>
  {#if mode === 'edit' && children}
    <div>
      {@render children()}
    </div>
  {/if}
</div>
