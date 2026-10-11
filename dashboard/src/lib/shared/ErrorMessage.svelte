<script lang="ts">
  import { errors } from '#lib/shared/errors';

  let { source, message = '', identity = '', snapshot = true, generation, label = '', detail = '' }: {
    source: string; message?: string | null; identity?: string | null;
    snapshot?: boolean; generation?: number; label?: string; detail?: string;
  } = $props();

  $effect(() => {
    errors.observe(source, message ?? '', identity ?? '', snapshot, generation);
  });
  const visible = $derived(
    (!$errors.started || ($errors.entries[source]?.visible
      && $errors.entries[source]?.message === message
      && $errors.entries[source]?.identity === (identity ?? '')))
    && (generation === undefined || generation === $errors.generation)
  );
</script>

{#if message && visible}
  <div class="error-message mono" role="alert" data-error-source={source}>
    <span>{label ? `${label}: ` : ''}{message}
      {#if detail}<details><summary>Details</summary><pre>{detail}</pre></details>{/if}
    </span>
    <button class="error-dismiss" type="button" aria-label="Dismiss error"
      title="Dismiss error" onclick={() => errors.dismiss(source)}>×</button>
  </div>
{/if}
