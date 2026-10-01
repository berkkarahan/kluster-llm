<script lang="ts">
  import { appStore } from '$lib/store.svelte';
  import { pushToast } from '$lib/toast';
  import { onMount } from 'svelte';
  import { connectCommand, shellQuote, type CommandShell } from '$lib/connect-command';

  const agents = ['claude', 'opencode', 'codex', 'hermes', 'openclaw', 'pi'];

  let shell = $state<CommandShell>('bash');
  let origin = $state('');
  onMount(() => { origin = window.location.origin; shell = /Win/.test(navigator.platform) ? 'powershell' : 'bash'; });
  const model = $derived((appStore.status?.process_pid || appStore.status?.inference_up)
    ? appStore.status.model_name || '' : appStore.selectedModel || '');
  const endpoint = $derived(appStore.status?.role === 'worker' && appStore.status.master_host
    ? `http://${appStore.status.master_host}:${appStore.status.master_port ?? 18765}` : origin);
  const remoteMaster = $derived(appStore.status?.role === 'worker');

  function cmd(agent: string, noLaunch = false) {
    return connectCommand(agent, model, endpoint, shell, noLaunch);
  }

  function tokenCommand() {
    const token = shellQuote(appStore.apiToken || '', shell);
    return shell === 'powershell' ? `$env:KLLM_API_KEY = ${token}` : `export KLLM_API_KEY=${token}`;
  }

  async function copy(text: string) {
    await navigator.clipboard.writeText(text);
    pushToast('ok', 'copied');
  }
</script>

<div class="max-w-3xl mx-auto p-4">
  <h1 class="mono text-[12px] uppercase tracking-wider mb-2" style="color: var(--muted)">Connect harnesses</h1>
  <p class="mono text-[12px] mb-4" style="color: var(--muted)">
    Launch an installed coding agent using the cluster model. Set KLLM_API_KEY in your terminal first.
  </p>

  <div class="panel p-3 mb-4 mono text-[12px]" style="border-color: var(--border)">
    <div>API base <span style="color: var(--accent)">{endpoint}</span></div>
    <div>Model: {model || 'Select or run a model first'}</div>
    <label>Shell <select class="field" bind:value={shell}><option value="bash">Bash / Zsh</option><option value="powershell">PowerShell</option></select></label>
    <div class="mt-1">
      Token
      <button class="btn ml-2" disabled={!appStore.apiToken || remoteMaster} onclick={() => copy(appStore.apiToken || '')}>Copy token</button>
      <button class="btn ml-1" onclick={() => appStore.loadToken()}>Refresh</button>
      <button class="btn ml-1" disabled={!appStore.apiToken || remoteMaster} onclick={() => copy(tokenCommand())}>Copy token setup</button>
    </div>
    {#if remoteMaster}<p>Get KLLM_API_KEY from the <a href={endpoint}>master dashboard</a>; this worker's token cannot authenticate inference.</p>{/if}
  </div>

  <div class="flex flex-col gap-2">
    {#each agents as agent}
      <div class="panel px-3 py-2 flex items-center gap-3" style="border-color: var(--border)">
        <span class="mono w-24" style="color: var(--accent)">{agent}</span>
        <code class="mono flex-1 text-[12px] truncate" style="color: var(--muted)">{cmd(agent)}</code>
        <button class="btn" disabled={!model || !endpoint} onclick={() => copy(cmd(agent))}>Copy</button>
        <button class="btn" disabled={!model || !endpoint} onclick={() => copy(cmd(agent, true))}>--no-launch</button>
      </div>
    {/each}
  </div>
</div>
