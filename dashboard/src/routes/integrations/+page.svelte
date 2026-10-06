<script lang="ts">
  import { appStore } from '$lib/store.svelte';
  import { pushToast } from '$lib/toast';
  import { onMount } from 'svelte';
  import ChatPanel from '$lib/components/ChatPanel.svelte';
  import { connectCommand, shellQuote, type CommandShell } from '$lib/connect-command';

  const agents = [
    { id: 'claude', name: 'Claude' },
    { id: 'opencode', name: 'OpenCode' },
    { id: 'codex', name: 'Codex' },
    { id: 'hermes', name: 'Hermes' },
    { id: 'openclaw', name: 'OpenClaw' },
    { id: 'pi', name: 'Pi' }
  ];

  let shell = $state<CommandShell>('bash');
  let harness = $state('codex');
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

<div class="stack">
  <header class="page-heading">
    <div>
      <p class="eyebrow">Connect</p>
      <h1 id="page-title">Connect a harness</h1>
      <p>Point a coding agent at this cluster, then chat with the running model.</p>
    </div>
  </header>

  <section class="panel panel-pad stack-sm">
    <div class="od-stat">
      <span class="stat-label">Endpoint</span>
      <strong class="mono">{endpoint || '—'}</strong>
    </div>
    <p>Model: {model || 'Select or run a model first'}</p>
    <label class="field" style="max-width: 240px">
      Shell
      <select class="field" bind:value={shell}>
        <option value="bash">Bash / Zsh</option>
        <option value="powershell">PowerShell</option>
      </select>
    </label>
    <div class="actions">
      <button class="btn" type="button" disabled={!appStore.apiToken || remoteMaster} onclick={() => copy(appStore.apiToken || '')}>Copy token</button>
      <button class="btn" type="button" onclick={() => appStore.loadToken()}>Refresh</button>
      <button class="btn" type="button" disabled={!appStore.apiToken || remoteMaster} onclick={() => copy(tokenCommand())}>Copy token setup</button>
    </div>
    {#if remoteMaster}<p>Get KLLM_API_KEY from the <a href={endpoint}>master dashboard</a>. This worker token cannot authenticate inference.</p>{/if}
  </section>

  <div class="connection-grid">
    {#each agents as agent}
      <button class="harness" class:active={harness === agent.id} type="button" onclick={() => harness = agent.id}>
        <span class="harness-letter">{agent.name.slice(0, 1)}</span>
        <span class="od-field">
          <strong>{agent.name}</strong>
          <small>{agent.id}</small>
        </span>
      </button>
    {/each}
  </div>

  <div class="code-row">
    <code>{cmd(harness) || 'Select or run a model first'}</code>
    <button class="btn" type="button" disabled={!model || !endpoint} onclick={() => copy(cmd(harness))}>Copy</button>
    <button class="btn" type="button" disabled={!model || !endpoint} onclick={() => copy(cmd(harness, true))}>--no-launch</button>
  </div>

  <ChatPanel />
</div>
