<script lang="ts">
  import { appStore } from '$lib/store.svelte';
  import { pushToast } from '$lib/toast';

  const agents = ['claude', 'opencode', 'codex', 'hermes', 'openclaw', 'pi'];

  function cmd(agent: string) {
    const model = appStore.selectedModel || appStore.status?.model_name || 'MODEL.gguf';
    return `kllm connect ${agent} --model ${model}`;
  }

  async function copy(text: string) {
    await navigator.clipboard.writeText(text);
    pushToast('ok', 'copied');
  }
</script>

<div class="max-w-3xl mx-auto p-4">
  <h1 class="mono text-[12px] uppercase tracking-wider mb-2" style="color: var(--muted)">Connect harnesses</h1>
  <p class="mono text-[12px] mb-4" style="color: var(--muted)">
    Same as Unsloth-style wrapping: session-scoped env pointing at the kllm agent API. CLI is the launcher; copy
    commands here for parity.
  </p>

  <div class="panel p-3 mb-4 mono text-[12px]" style="border-color: var(--border)">
    <div>API base <span style="color: var(--accent)">http://127.0.0.1:{appStore.status?.control_port ?? 18765}</span></div>
    <div class="mt-1">
      Token
      <button class="btn ml-2" onclick={() => copy(appStore.apiToken || '')}>Copy token</button>
      <button class="btn ml-1" onclick={() => appStore.loadToken()}>Refresh</button>
    </div>
  </div>

  <div class="flex flex-col gap-2">
    {#each agents as agent}
      <div class="panel px-3 py-2 flex items-center gap-3" style="border-color: var(--border)">
        <span class="mono w-24" style="color: var(--accent)">{agent}</span>
        <code class="mono flex-1 text-[12px] truncate" style="color: var(--muted)">{cmd(agent)}</code>
        <button class="btn" onclick={() => copy(cmd(agent))}>Copy</button>
        <button class="btn" onclick={() => copy(`kllm connect ${agent} --no-launch`)}>--no-launch</button>
      </div>
    {/each}
  </div>
</div>
