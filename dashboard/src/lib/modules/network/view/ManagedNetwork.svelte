<script lang="ts">
  import { createApp } from '$lib/app.svelte';
  import ErrorMessage from '$lib/shared/ErrorMessage.svelte';
  import { onDestroy } from 'svelte';
  import { errors } from '$lib/shared/errors';
  import type { ManagedNetwork } from '$lib/modules/network/entities';
  import NetworkAdvanced from './NetworkAdvanced.svelte';
  import type { NetworkInteractor } from '../interactor.svelte';
  let { presenter = createApp().network }: { presenter?: NetworkInteractor } = $props();

  type Adapter = { adapter_id: string; os_interface: string; link_speed_mbps: number | null; reason: string };
  type Computer = { node_id: string; name: string; host: string; master: boolean; administrator_enabled: boolean; adapters: Adapter[]; selected_adapter: string; ready: boolean; error_code: string | null; message: string };
  type Assessment = { administrator_notice: string; master_node_id: string; computers: Computer[]; assessment_revision: string; networks: ManagedNetwork[] };
  type LocalStatus = { administrator_enabled: boolean; administrator_notice: string; message: string; cleanup_pending?: string[] };
  const notice = 'Administrator access is required on each computer to configure its dedicated network adapter. Kluster will save the original settings and restore its changes when the network is stopped. After you enable access once on each computer, this cluster’s master can set up the private network.';
  let assessment = $state<Assessment | null>(null);
  let local = $state<LocalStatus | null>(null);
  let included = $state<Record<string, boolean>>({});
  let adapters = $state<Record<string, string>>({});
  let acknowledged = $state(false);
  let advanced = $state(false);
  let subnet = $state('');
  let opened = $state(false);
  let busy = $state(false);
  let error = $state('');
  let errorGeneration = $state(errors.generation);
  let diagnostic = $state('');
  let operation = $state('');
  let operationGeneration = errors.generation;
  let operationRevision = $state(1);
  let phase = $state('');
  let recovery = $state<{network_id: string; snapshot_digest: string; exact_address_prefix: string; current: unknown} | null>(null);
  let participants = $state<Array<{node_id: string; status: string; stage: string}>>([]);
  let controller: AbortController | null = null;
  let timer: ReturnType<typeof setTimeout> | null = null;
  const master = $derived(presenter.master);
  const network = $derived(assessment?.networks.find(n => n.observed_state !== 'stopped'));
  const selected = $derived(assessment?.computers.filter(c => c.master || included[c.node_id]) ?? []);
  const canStart = $derived(selected.length >= 2 && selected.every(c => !c.error_code && !!(adapters[c.node_id] || c.selected_adapter)));
  const networkLabel = $derived(network?.observed_state === 'ready' ? 'Private network ready' : operation ? 'Setting up' : network ? ({preparing:'Checking computers', applying:'Configuring adapters', verifying:'Testing connections', degraded:'Connection needs attention', failed:'Setup needs attention', stopping:'Restoring settings', cleanup_pending:'Restoration needs attention'}[network.observed_state] ?? 'Not set up') : 'Not set up');

  function readable(failure: unknown): string {
    const value = String(failure).replace(/^Error:\s*/, '');
    const code = value.split(':', 1)[0];
    const messages: Record<string, string> = {
      ADMIN_REQUIRED: 'Administrator access was not enabled. Open Kluster on that computer and approve the operating system prompt.',
      STALE_REVISION: 'The network changed. Refresh the computer list and try again.',
      INFERENCE_ACTIVE: 'Stop the inference run using this network before changing it.',
      UNSUPPORTED_CAPABILITY: 'Update Kluster on the computer that needs attention, then refresh.',
      UPGRADE_REQUIRED: 'Update Kluster on this computer to install administrator support.',
      UNPAIRED_IDENTITY: 'A computer’s identity needs attention. Review its pairing in Advanced details.',
      HELPER_REFUSED: 'Network settings could not be applied safely. Review the diagnostic message in Advanced details.'
    };
    return messages[code] ?? value.replace(/^[A-Z_]+:\s*/, '');
  }

  async function refresh(signal?: AbortSignal) {
    const generation = errors.generation;
    const result = await presenter.localSetup<LocalStatus>(signal);
    if (generation !== errors.generation) return;
    local = result;
    if (master) {
      const next = await presenter.clusterSetup<Assessment>(signal);
      if (generation !== errors.generation) return;
      assessment = next;
      for (const c of assessment.computers) {
        included[c.node_id] ??= true;
        if (!adapters[c.node_id] || !c.adapters.some(a => a.adapter_id === adapters[c.node_id] && !a.reason)) adapters[c.node_id] = c.selected_adapter;
      }
    }
  }

  async function work(action: () => Promise<void>, explicit = true) {
    const requestGeneration = errors.generation;
    errorGeneration = requestGeneration;
    busy = true; error = ''; diagnostic = '';
    if (explicit) errors.observe('managed-network', '', '', false, requestGeneration);
    try { await action(); }
    catch (failure) { if (!controller?.signal.aborted && requestGeneration === errors.generation) { diagnostic = String(failure); error = readable(failure); errors.publish('managed-network', error, errorGeneration); } }
    finally { busy = false; }
  }

  async function load() {
    await work(async () => {
      await refresh(controller?.signal);
      if (timer) clearTimeout(timer);
      if (!operation && network?.current_operation_id && ['preparing', 'applying', 'verifying', 'stopping'].includes(network.observed_state)) {
        operation = network.current_operation_id;
        operationGeneration = errors.generation;
        errors.observe('managed-network', '');
      }
      if (operation) await poll();
    }, false);
  }

  async function enableAdministrator() {
    await work(async () => {
      await presenter.acknowledgeAdministrator(acknowledged);
      await refresh(controller?.signal);
    });
  }

  async function reviewRecovery(networkId: string) {
    await work(async () => { recovery = await presenter.recovery(networkId); });
  }

  async function resolveRecovery(decision: 'keep_current' | 'restore_owned') {
    await work(async () => {
      if (!recovery) return;
      await presenter.decideRecovery(recovery.network_id, decision, recovery.snapshot_digest);
      recovery = null; await refresh(controller?.signal);
    });
  }

  async function disableAdministrator() {
    await work(async () => { await presenter.clearAdministrator(); await refresh(controller?.signal); });
  }

  async function poll() {
    const requestGeneration = operationGeneration;
    if (!opened || !operation) return;
    try {
      const result = await presenter.setupOperation<{phase: string; operation_revision: number; terminal_result: {error_code?: string; message?: string} | null; message?: string; participants?: typeof participants}>(operation, controller?.signal);
      phase = result.phase; operationRevision = result.operation_revision;
      participants = result.participants ?? [];
      if (result.terminal_result) {
        operation = '';
        if (requestGeneration === errors.generation) await refresh(controller?.signal);
        if (result.terminal_result.error_code && requestGeneration === errors.generation) { errorGeneration = requestGeneration; error = result.message || readable(`${result.terminal_result.error_code}: ${result.terminal_result.message ?? 'Review computer status'}`); errors.observe('managed-network', error); }
      } else timer = setTimeout(poll, 1000);
    } catch (failure) { if (!controller?.signal.aborted && requestGeneration === errors.generation) { errorGeneration = requestGeneration; operation = ''; error = readable(failure); errors.observe('managed-network', error, '', false, requestGeneration); } }
  }

  async function setup(action: 'start' | 'add' | 'retry' = 'start') {
    await work(async () => {
      if (!assessment) return;
      const accepted = await presenter.startSetup<{operation_id: string}>({
        request_id: crypto.randomUUID(), nodes: selected.map(c => c.node_id), adapters,
        assessment_revision: assessment.assessment_revision, ...(subnet ? {subnet} : {}), action,
        ...(network && action !== 'start' ? {network_id: network.network_id, expected_revision: network.revision} : {})
      });
      operation = accepted.operation_id; operationGeneration = errorGeneration; phase = 'preparing'; await poll();
    });
  }

  async function stop() {
    await work(async () => {
      if (!network) return;
      const accepted = await presenter.networkLifecycle<{operation_id: string}>(network.network_id, {request_id: crypto.randomUUID(), expected_revision: network.revision, action: 'stop'});
      operation = accepted.operation_id; operationGeneration = errorGeneration; phase = 'cleaning'; await poll();
    });
  }

  async function cancel() {
    await work(async () => {
      await presenter.cancelOperation(operation, operationRevision);
    });
  }

  function toggle(event: Event) {
    if (event.target !== event.currentTarget) return;
    opened = (event.currentTarget as HTMLDetailsElement).open;
    if (opened) { controller = new AbortController(); void load(); }
    else { controller?.abort(); if (timer) clearTimeout(timer); }
  }
  onDestroy(() => { opened = false; controller?.abort(); if (timer) clearTimeout(timer); });
</script>

<details class="panel managed-network" ontoggle={toggle} data-testid="managed-network">
  <summary><span>Private 10GbE network</span><span class:ready={network?.observed_state === 'ready'}>{networkLabel}</span></summary>
  <div class="guided-content">
    <p class="intro">Connect your computers to a dedicated Ethernet switch or cable. Links at 1 Gbps or faster are supported; 10 Gbps is preferred. Kluster handles the network settings and tests the connection.</p>
    <section class="admin-notice" aria-label="Administrator access requirements">
      <strong>Administrator access is needed first</strong>
      <p>{assessment?.administrator_notice ?? local?.administrator_notice ?? notice}</p>
      {#if !local?.administrator_enabled}
        <label class="check"><input type="checkbox" bind:checked={acknowledged} /> I understand and allow this cluster to manage this computer’s dedicated network adapter.</label>
        <button class="btn" disabled={!acknowledged || busy || !!operation} onclick={enableAdministrator}>Enable administrator access on this computer</button>
        <p class="hint">Your operating system will ask for permission. Do this once in Kluster on each participating computer.</p>
      {:else}<p class="granted">Administrator access enabled on this computer.</p>{#if local?.message}<p>{local.message}</p>{/if}{/if}
    </section>
    {#if error}<ErrorMessage source="managed-network" message={error} snapshot={false} generation={errorGeneration} />{/if}
    {#each local?.cleanup_pending ?? [] as pending}
      <p>Network settings on this computer need review before setup can continue.</p>
      <button class="btn" disabled={busy} onclick={() => reviewRecovery(pending)}>Review network recovery</button>
    {/each}
    {#if recovery}
      <section class="admin-notice" aria-label="Network recovery decision">
        <p>Kluster lost track of changes on this computer’s dedicated adapter. Choose whether to keep its current settings or remove Kluster’s private address ({recovery.exact_address_prefix}) and restore the saved settings.</p>
        <button class="btn" disabled={busy} onclick={() => resolveRecovery('keep_current')}>Keep current settings</button>
        <button class="btn" disabled={busy} onclick={() => resolveRecovery('restore_owned')}>Restore Kluster’s changes</button>
        <details><summary>Settings to review</summary><pre>{JSON.stringify(recovery.current, null, 2)}</pre></details>
      </section>
    {/if}
    {#if master}
      <fieldset disabled={busy || !!operation} class="computer-list">
        <legend>Computers to connect</legend>
        {#each assessment?.computers ?? [] as computer (computer.node_id)}
          <div class="computer">
            <label class="check"><input type="checkbox" bind:checked={included[computer.node_id]} disabled={computer.master || !!network?.participants.some(p => p.node_id === computer.node_id)} /> <strong>{computer.name}</strong> {#if computer.master}<span class="hint">This computer · master</span>{/if}</label>
            <p class:granted={!computer.error_code && !!(adapters[computer.node_id] || computer.selected_adapter)}>{computer.error_code ? 'Needs attention' : computer.message}</p>
            <ErrorMessage source={`network-computer:${computer.node_id}`} message={computer.error_code ? computer.message : ''} />
            {#if computer.adapters.filter(a => !a.reason).length > 1}
              <label>Dedicated Ethernet adapter
                <select aria-label={`Dedicated adapter for ${computer.name}`} bind:value={adapters[computer.node_id]}>
                  <option value="">Choose an adapter</option>
                  {#each computer.adapters.filter(a => !a.reason) as adapter}<option value={adapter.adapter_id}>{adapter.os_interface} · {(adapter.link_speed_mbps ?? 0) / 1000} Gbps</option>{/each}
                </select>
              </label>
            {:else if computer.selected_adapter}
              <p class="hint">{computer.selected_adapter} · {(computer.adapters.find(a => a.adapter_id === computer.selected_adapter)?.link_speed_mbps ?? 0) / 1000} Gbps · Settings handled automatically</p>
            {/if}
            {#if !computer.administrator_enabled && !computer.master}<p class="hint">Open Kluster locally on {computer.name} to enable administrator access.</p>{/if}
          </div>
        {/each}
        {#if !assessment}<p>Refresh to check connected computers.</p>{/if}
      </fieldset>
      {#if network}
        <section class="network-status" aria-label="Private network status">
          <strong>{networkLabel}</strong>
          {#each network.participants as participant}<p>{assessment?.computers.find(c => c.node_id === participant.node_id)?.name ?? participant.node_id} · {participant.state === 'ready' ? 'Connection verified' : participant.state.replaceAll('_', ' ')}</p>{/each}
          {#if network.blocking_runs.length}<p>Stop the inference run using this network before changing it.</p>{/if}
        </section>
      {/if}
      {#if operation}
        <p role="status">{phase === 'preparing' ? 'Checking each computer…' : phase === 'applying' ? 'Configuring dedicated adapters…' : phase === 'verifying' ? 'Testing private connections…' : 'Restoring network settings…'}</p>
        {#each participants as participant}<p>{assessment?.computers.find(c => c.node_id === participant.node_id)?.name ?? participant.node_id}: {participant.status.replaceAll('_', ' ')}</p>{/each}
        <button class="btn" disabled={busy} onclick={cancel}>Cancel setup and restore settings</button>
      {:else if !network}<button class="btn btn-primary" disabled={busy || !canStart} onclick={() => setup()}>Set up private network</button>
      {:else}
        {#if ['failed', 'degraded', 'verifying'].includes(network.observed_state)}<button class="btn" disabled={busy || !canStart || network.blocking_runs.length > 0} onclick={() => setup('retry')}>Retry setup</button>{/if}
        {#if network.observed_state === 'ready' && selected.some(c => !network?.participants.some(p => p.node_id === c.node_id))}<button class="btn" disabled={busy || !canStart || network.blocking_runs.length > 0} onclick={() => setup('add')}>Add selected computers</button>{/if}
        <button class="btn" disabled={busy || network.blocking_runs.length > 0} onclick={stop}>Stop private network and restore settings</button>
      {/if}
    {:else}<p>The cluster’s master handles private-network setup. After enabling administrator access here, continue on the master.</p>{/if}
    <button class="btn" disabled={busy || !!operation} onclick={load}>Refresh computers</button>
    <details class="advanced" bind:open={advanced}>
      <summary>Advanced details</summary>
      <ErrorMessage source="managed-network" message={error} detail={diagnostic} snapshot={false} generation={errorGeneration} />
      <label>Private address range (optional)<input bind:value={subnet} placeholder="Chosen automatically" aria-label="Private subnet override" /></label>
      <p class="hint">Leave this empty to let Kluster choose a range that avoids existing networks.</p>
      {#if local?.administrator_enabled}<button class="btn" disabled={busy || !!network || !!operation} onclick={disableAdministrator}>Disable administrator access on this computer</button>{/if}
      {#if advanced}<NetworkAdvanced {presenter} />{/if}
    </details>
  </div>
</details>

<style>
  .managed-network { margin-top: 12px; }
  summary { padding: 12px; cursor: pointer; display: flex; justify-content: space-between; gap: 12px; }
  .guided-content { padding: 0 12px 12px; display: grid; gap: 14px; font-size: 13px; }
  p { margin: 6px 0; }
  .intro, .hint { color: var(--muted); }
  .hint { font-size: 12px; }
  .admin-notice { padding: 12px; border: 1px solid var(--border); border-radius: 6px; }
  .check { display: flex; align-items: center; flex-wrap: wrap; gap: 8px; margin-bottom: 8px; }
  .check input { flex: 0 0 auto; width: auto; margin: 0; }
  .computer-list, .network-status { border: 1px solid var(--border); padding: 12px; border-radius: 6px; }
  .computer + .computer { border-top: 1px solid var(--border); margin-top: 10px; padding-top: 10px; }
  .computer p, .computer > label:not(.check) { margin-left: 24px; }
  select, input:not([type='checkbox']) { width: 100%; padding: 8px; background: var(--bg); color: var(--fg); border: 1px solid var(--border); border-radius: 4px; }
  .ready, .granted { color: var(--success, #69b884); }
  .advanced { border-top: 1px solid var(--border); padding-top: 8px; }
  button { justify-self: start; }
</style>
