<script lang="ts">
  import { createApp } from '$lib/app.svelte';
  import { pushToast } from '$lib/shared/toast';
  import { onMount } from 'svelte';
  import ChatPanel from '$lib/modules/integrations/view/ChatPanel.svelte';
  import { connectCommand, type CommandShell } from '$lib/modules/integrations/connect-command';
  import type { App } from '$lib/app.svelte';
  let { cluster = createApp().cluster, integrations = createApp().integrations }: {
    cluster?: App['cluster'];
    integrations?: App['integrations'];
  } = $props();

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
  type ConnectionSnapshot = { role: 'master'; node_id: string; url: string; model: string; api_token: string };
  let connection = $state<ConnectionSnapshot | null>(null);
  let connectionError = $state('');
  let loading = $state(true);
  let generation = 0;
  let disposed = false;
  const remoteMaster = $derived(cluster.status?.role === 'worker');
  const connectionIdentity = $derived([
    cluster.status?.role,
    cluster.status?.node_id,
    cluster.status?.master_host,
    cluster.status?.process_pid,
    cluster.status?.inference_up,
    cluster.status?.model_name,
    cluster.status?.active_config?.run_id
  ].join(':'));

  async function refreshConnection(): Promise<ConnectionSnapshot | null> {
    const current = ++generation;
    loading = true;
    connectionError = '';
    try {
      const result = await integrations.connectionSnapshot();
      if (disposed || current !== generation) return null;
      if (result.role !== 'master' || !result.url || !result.model || !result.api_token) {
        throw new Error('Master returned incomplete Connect details');
      }
      connection = result;
      return result;
    } catch (failure) {
      if (!disposed && current === generation) {
        connection = null;
        connectionError = String(failure).replace(/^Error:\s*/, '');
      }
      return null;
    } finally {
      if (!disposed && current === generation) loading = false;
    }
  }

  function cmd(snapshot: ConnectionSnapshot | null, agent = harness, noLaunch = false, redact = true) {
    return snapshot
      ? connectCommand(agent, snapshot.model, snapshot.url, snapshot.api_token, shell, noLaunch, redact)
      : '';
  }

  async function copy(noLaunch = false) {
    const snapshot = await refreshConnection();
    if (!snapshot) return;
    await navigator.clipboard.writeText(cmd(snapshot, harness, noLaunch, false));
    pushToast('ok', 'copied');
  }

  onMount(() => {
    shell = /Win/.test(navigator.platform) ? 'powershell' : 'bash';
    void refreshConnection();
    return () => { disposed = true; generation += 1; };
  });

  $effect(() => {
    if (connectionIdentity !== '::::::') void refreshConnection();
  });
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
      <strong class="mono">{connection?.url || '—'}</strong>
    </div>
    <p>Model: {connection?.model || (loading ? 'Checking master…' : 'Start a model on the master first')}</p>
    <label class="field" style="max-width: 240px">
      Shell
      <select class="field" bind:value={shell}>
        <option value="bash">Bash / Zsh</option>
        <option value="powershell">PowerShell</option>
      </select>
    </label>
    <div class="actions">
      <button class="btn" type="button" disabled={loading} onclick={() => refreshConnection()}>Refresh</button>
    </div>
    {#if remoteMaster}<p>Commands use the master’s inference endpoint, model, and token.</p>{/if}
    {#if connectionError}<p role="status">{connectionError}</p>{/if}
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
    <code>{cmd(connection) || (loading ? 'Checking master…' : 'Connect command unavailable')}</code>
    <button class="btn" type="button" disabled={loading || !connection} onclick={() => copy()}>Copy command</button>
    <button class="btn" type="button" disabled={loading || !connection} onclick={() => copy(true)}>--no-launch</button>
  </div>

  <ChatPanel presenter={integrations} />
</div>
