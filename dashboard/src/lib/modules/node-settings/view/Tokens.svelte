<script lang="ts">
  import { createApp } from '$lib/app.svelte';
  import type { App } from '$lib/app.svelte';
  let { http = createApp().http, cluster = createApp().cluster, models = createApp().models }: {
    http?: App['http'];
    cluster?: App['cluster'];
    models?: App['models'];
  } = $props();
  /**
   * System Settings > Tokens & Secrets: loopback API token (read-only helper for
   * coding harnesses) and the optional Hugging Face Hub token for gated GGUF
   * downloads. No secrets are ever printed to the dashboard body.
   */

  let revealed = $state(false);
  let copied = $state(false);

  const masked = $derived(http.apiToken ? 'sk-••••' : '');
  const plain = $derived(http.apiToken ?? '');

  function toggleShow() {
    revealed = !revealed;
  }

  async function copy() {
    if (!plain || !navigator.clipboard?.writeText) return;
    await navigator.clipboard.writeText(plain);
    copied = true;
    setTimeout(() => {
      copied = false;
    }, 1200);
  }

  const hfSource = $derived(models.hfTokenSource ? ` · sourced from ${models.hfTokenSource}` : '');
</script>

<section class="panel p-3.5" aria-labelledby="tok-label">
  <h2 id="tok-label" class="mono text-[11px] uppercase tracking-wider mb-3" style="color: var(--muted)">
    Tokens &amp; Secrets
  </h2>

  <div>
    <label class="mono text-[11px]" style="color: var(--muted)" for="tok-api-token">API token</label>
    <div class="flex flex-wrap items-center gap-2">
      <input
        id="tok-api-token"
        class="field mono flex-1 min-w-[200px]"
        type={revealed ? 'text' : 'password'}
        value={revealed && plain ? plain : masked}
        readonly
        spellcheck="false"
      />
      <button type="button" class="btn" disabled={!plain} onclick={toggleShow}>
        {revealed ? 'hide' : 'show'}
      </button>
      <button type="button" class="btn" disabled={!plain} onclick={copy}>
        {copied ? 'copied' : 'copy'}
      </button>
    </div>
    <p class="mono text-[10px] mt-2" style="color: var(--muted)">
      Loopback helper — points harness agents at 127.0.0.1:{cluster.status?.control_port ?? 18765}.
    </p>
  </div>

  <div class="mt-4">
    <label class="mono text-[11px]" style="color: var(--muted)" for="tok-hf-token">Hugging Face Hub token</label>
    <input
      id="tok-hf-token"
      class="field mono"
      type="password"
      placeholder="paste hub token to set"
      bind:value={models.hfTokenInput}
      disabled={!!http.busy}
      spellcheck="false"
    />
    <p class="mono text-[10px] mt-2" style="color: var(--muted)">
      Required for gated GGUF downloads. Stored chmod 600 at ~/.kllm/hf_token{hfSource}.
    </p>
    <div class="flex gap-2 mt-2">
      <button type="button" class="btn btn-accent" disabled={!!http.busy} onclick={() => models.saveHfToken()}>
        Save
      </button>
      {#if models.hfTokenConfigured}
        <button type="button" class="btn" disabled={!!http.busy} onclick={() => models.clearHfToken()}>
          Clear
        </button>
      {/if}
    </div>
  </div>
</section>
