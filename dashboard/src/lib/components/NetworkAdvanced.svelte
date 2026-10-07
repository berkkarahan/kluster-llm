<script lang="ts">
  import ErrorMessage from '$lib/components/ErrorMessage.svelte';
  import { onDestroy } from 'svelte';
  import { errors } from '$lib/errors';
  import { appStore } from '$lib/store.svelte';
  import type { ManagedNetwork, NetworkAdapter, NetworkIdentity, NetworkInventory } from '$lib/types';

  let identity = $state<NetworkIdentity | null>(null);
  let localAdapters = $state<NetworkAdapter[]>([]);
  let nodes = $state<Record<string, NetworkInventory>>({});
  let networks = $state<ManagedNetwork[]>([]);
  let selections = $state<Record<string, string>>({});
  let included = $state<Record<string, boolean>>({});
  let localAdapter = $state('');
  let peerIdentity = $state('');
  let replacePeer = $state(false);
  let subnet = $state('192.168.234.0/24');
  let error = $state('');
  let errorGeneration = $state(errors.generation);
  let busy = $state(false);
  let opened = $state(false);
  let activeOperation = $state('');
  let activeOperationRevision = $state(1);
  let cleanupJournals = $state<Array<{journal_id: string; exact_address_prefix: string; phase: string}>>([]);
  let cleanupReview = $state<{journal: {journal_id: string; exact_address_prefix: string}; snapshot: unknown; snapshot_digest: string} | null>(null);
  let controller: AbortController | null = null;
  let pollController: AbortController | null = null;
  let timer: ReturnType<typeof setTimeout> | null = null;
  const master = $derived(appStore.status?.role === 'master');
  const activeNetwork = $derived(networks.find((network) => network.observed_state !== 'stopped'));

  function selectedAdapter(node: NetworkInventory): string {
    try { return JSON.parse(node.selection ?? '{}').adapter_id ?? ''; }
    catch { return ''; }
  }

  async function refresh() {
    const requestGeneration = errors.generation;
    errorGeneration = requestGeneration;
    controller?.abort();
    controller = new AbortController();
    const signal = controller.signal;
    error = '';
    try {
      const [publicIdentity, local, current] = await Promise.all([
        appStore.networkRequest<NetworkIdentity>('/v2/node/network-identity', 'GET', undefined, signal),
        appStore.networkRequest<NetworkInventory>('/v2/node/network-adapters', 'GET', undefined, signal),
        appStore.networkRequest<{networks: ManagedNetwork[]}>('/v2/cluster/networks', 'GET', undefined, signal)
      ]);
      cleanupJournals = (await appStore.networkRequest<{journals: typeof cleanupJournals}>('/v2/node/network-cleanup', 'GET', undefined, signal)).journals;
      if (requestGeneration !== errors.generation) return;
      identity = publicIdentity;
      localAdapters = local.adapters;
      localAdapter = selectedAdapter(local);
      networks = current.networks;
      if (master) {
        const inventory = await appStore.networkRequest<{nodes: Record<string, NetworkInventory>}>('/v2/cluster/network-adapters', 'GET', undefined, signal);
        if (requestGeneration !== errors.generation) return;
        nodes = inventory.nodes;
        for (const [nodeId, node] of Object.entries(nodes)) {
          selections[nodeId] = selectedAdapter(node);
          included[nodeId] ??= true;
        }
      }
    } catch (failure) {
      if (!signal.aborted && requestGeneration === errors.generation) { error = String(failure); errors.observe('advanced-network', error, '', false, requestGeneration); }
    }
  }

  async function action(work: () => Promise<void>) {
    const requestGeneration = errors.generation;
    errorGeneration = requestGeneration;
    busy = true;
    error = '';
    errors.observe('advanced-network', '', '', false, requestGeneration);
    try { await work(); }
    catch (failure) { if (requestGeneration === errors.generation) { error = String(failure); errors.publish('advanced-network', error, requestGeneration); } }
    finally { busy = false; }
  }

  async function pair() {
    await action(async () => {
      const peer = JSON.parse(peerIdentity);
      await appStore.networkRequest(`/v2/node/network-identity/peers/${encodeURIComponent(peer.node_id)}`, 'PUT', {
        node_id: peer.node_id, public_key: peer.public_key, key_id: peer.key_id,
        cluster_binding_id: master ? identity?.cluster_binding_id : peer.cluster_binding_id,
        expected_role: master ? 'worker' : 'master', replace: replacePeer
      });
      peerIdentity = '';
      await refresh();
    });
  }

  async function saveSelection() {
    await action(async () => {
      const adapter = localAdapters.find((item) => item.adapter_id === localAdapter);
      if (!adapter) throw new Error('Choose an adapter explicitly');
      await appStore.networkRequest('/v2/node/network-selection', 'PUT', {
        adapter_id: adapter.adapter_id, expected_inventory_revision: adapter.inventory_revision
      });
      await refresh();
    });
  }

  async function pollOperation(operationId: string) {
    const operationGeneration = errorGeneration;
    activeOperation = operationId;
    pollController?.abort();
    pollController = new AbortController();
    const signal = pollController.signal;
    if (timer) clearTimeout(timer);
    const poll = async () => {
      const requestGeneration = operationGeneration;
      if (!opened || !activeOperation) return;
      try {
        const operation = await appStore.networkRequest<{phase: string; operation_revision: number; terminal_result: {error_code?: string; message?: string} | null}>(`/v2/cluster/network-operations/${operationId}`, 'GET', undefined, signal);
        activeOperationRevision = operation.operation_revision;
        if (operation.terminal_result) {
          activeOperation = '';
          if (requestGeneration === errors.generation) await refresh();
          if (operation.terminal_result.error_code && requestGeneration === errors.generation) { errorGeneration = requestGeneration; error = `${operation.terminal_result.error_code}: ${operation.terminal_result.message ?? 'Inspect participant results'}`; errors.observe('advanced-network', error); }
          return;
        }
        const current = await appStore.networkRequest<{networks: ManagedNetwork[]}>('/v2/cluster/networks', 'GET', undefined, signal);
        networks = current.networks;
        timer = setTimeout(poll, 1000);
      } catch (failure) { if (!signal.aborted && requestGeneration === errors.generation) { errorGeneration = requestGeneration; activeOperation = ''; error = String(failure); errors.observe('advanced-network', error, '', false, requestGeneration); } }
    };
    await poll();
  }

  async function start() {
    await action(async () => {
      const participants = Object.entries(nodes).filter(([nodeId]) => included[nodeId] || nodeId === identity?.node_id).map(([nodeId, node]) => {
        const adapter = node.adapters.find((item) => item.adapter_id === selections[nodeId]);
        if (!adapter) throw new Error(`${nodeId}: choose an adapter in that node's local app first`);
        return {node_id: nodeId, adapter_id: adapter.adapter_id, inventory_revision: adapter.inventory_revision};
      });
      const accepted = await appStore.networkRequest<{operation_id: string}>('/v2/cluster/networks', 'POST', {request_id: crypto.randomUUID(), subnet, participants});
      await pollOperation(accepted.operation_id);
    });
  }

  async function stop(network: ManagedNetwork) {
    await action(async () => {
      const accepted = await appStore.networkRequest<{operation_id: string}>(`/v2/cluster/networks/${network.network_id}/operations`, 'POST', {request_id: crypto.randomUUID(), expected_revision: network.revision, action: 'stop'});
      await pollOperation(accepted.operation_id);
    });
  }

  async function reconfigure(network: ManagedNetwork, kind: 'add' | 'retry') {
    await action(async () => {
      const participants = kind === 'add' ? Object.entries(nodes).filter(([nodeId]) => included[nodeId] && !network.participants.some((participant) => participant.node_id === nodeId)).map(([nodeId, node]) => {
        const adapter = node.adapters.find((item) => item.adapter_id === selections[nodeId]);
        if (!adapter) throw new Error(`${nodeId}: select an adapter locally first`);
        return {node_id: nodeId, adapter_id: adapter.adapter_id, inventory_revision: adapter.inventory_revision};
      }) : [];
      if (kind === 'add' && !participants.length) throw new Error('Select a new participant first');
      const accepted = await appStore.networkRequest<{operation_id: string}>(`/v2/cluster/networks/${network.network_id}/operations`, 'POST', {request_id: crypto.randomUUID(), expected_revision: network.revision, action: kind, participants});
      await pollOperation(accepted.operation_id);
    });
  }

  async function cancel() {
    await action(async () => {
      await appStore.networkRequest(`/v2/cluster/network-operations/${activeOperation}/cancel`, 'POST', {request_id: crypto.randomUUID(), expected_operation_revision: activeOperationRevision});
    });
  }

  async function reviewCleanup(journalId: string) {
    await action(async () => {
      cleanupReview = await appStore.networkRequest(`/v2/node/network-cleanup/${journalId}`);
    });
  }

  async function resolveCleanup(decision: 'release_ownership' | 'confirm_owned') {
    await action(async () => {
      if (!cleanupReview) return;
      await appStore.networkRequest(`/v2/node/network-cleanup/${cleanupReview.journal.journal_id}/resolve`, 'POST', {decision, snapshot_digest: cleanupReview.snapshot_digest});
      cleanupReview = null;
      await refresh();
    });
  }

  function toggle(event: Event) {
    opened = (event.currentTarget as HTMLDetailsElement).open;
    if (opened) void refresh();
    else {
      controller?.abort();
      pollController?.abort();
      if (timer) clearTimeout(timer);
      activeOperation = '';
    }
  }

  onDestroy(() => {
    opened = false;
    controller?.abort();
    pollController?.abort();
    if (timer) clearTimeout(timer);
  });
</script>

<details class="panel legacy-network" ontoggle={toggle} data-testid="network-advanced">
  <summary>Manual configuration and diagnostics</summary>
  <div class="network-content">
    <p>Use a dedicated Ethernet adapter with DHCP disabled, at least 1 Gbps negotiated speed (10 Gbps preferred), and a privileged agent. Pair each worker and master locally before starting.</p>
    {#if error}<ErrorMessage source="advanced-network" message={error} snapshot={false} generation={errorGeneration} />{/if}
    {#if identity}
      <label>Public identity to copy to the paired node
        <textarea readonly rows="4" value={JSON.stringify({node_id: identity.node_id, key_id: identity.key_id, public_key: identity.public_key, cluster_binding_id: identity.cluster_binding_id}, null, 2)} aria-label="Local network public identity"></textarea>
      </label>
      <p>Paired: {identity.paired_peers.map((peer) => peer.node_id).join(', ') || 'none'}</p>
      <label>Paste {master ? 'worker' : 'master'} public identity JSON
        <textarea bind:value={peerIdentity} rows="3" aria-label="Peer network public identity"></textarea>
      </label>
      <label><input type="checkbox" bind:checked={replacePeer} /> Explicitly replace an existing pairing (requires settled networking)</label>
      <button class="btn" disabled={busy || !peerIdentity} onclick={pair}>Pair node</button>
      <label>Local dedicated adapter
        <select bind:value={localAdapter} aria-label="Local dedicated network adapter">
          <option value="">Choose explicitly</option>
          {#each localAdapters as adapter}
            <option value={adapter.adapter_id}>{adapter.os_interface} · {adapter.link_speed_mbps === null ? 'speed unknown' : `${adapter.link_speed_mbps} Mbps`} · DHCP {adapter.dhcp === null ? 'unknown' : adapter.dhcp ? 'enabled' : 'disabled'}</option>
          {/each}
        </select>
      </label>
      <button class="btn" disabled={busy || !localAdapter || !!activeNetwork} onclick={saveSelection}>Save local adapter</button>
      {#if !identity.permission}<p>Restart this node's agent with administrator/root privileges before setup.</p>{/if}
    {/if}
    {#if master}
      <fieldset disabled={busy || !!activeOperation}>
        <legend>Participants</legend>
        {#each Object.entries(nodes) as [nodeId, node]}
          <label><input type="checkbox" bind:checked={included[nodeId]} disabled={nodeId === identity?.node_id} /> {nodeId}: {selections[nodeId] || (node.error_code ? 'Needs attention' : 'local adapter not selected')}</label>
          <ErrorMessage source={`network-adapter:${nodeId}`} message={node.error_code} />
        {/each}
      </fieldset>
      <label>Private subnet <input bind:value={subnet} aria-label="Dedicated private subnet" placeholder="192.168.234.0/24" /></label>
      <button class="btn btn-primary" disabled={busy || !!activeNetwork || !!activeOperation} onclick={start}>Start private network</button>
    {/if}
    {#each networks as network}
      <section class="network-status" aria-label="Private network status">
        <p><strong>{network.subnet}</strong> · {network.observed_state} · revision {network.revision}</p>
        {#each network.participants as participant}
          <p>{participant.node_id} · <span class="mono">{participant.address}/{participant.prefix_length}</span> · {participant.state} · {participant.ownership} · {participant.verification_freshness}</p>
          {#if participant.error}<ErrorMessage source={`network-participant:${participant.node_id}`} message={`${participant.error.code}: ${participant.error.message}`} />{/if}
        {/each}
        {#if network.blocking_runs.length}<p>Stop dependent inference first: {network.blocking_runs.map((run) => `${run.run_id} (${run.state})`).join(', ')}</p>{/if}
        {#if master && network.observed_state !== 'stopped'}
          <button class="btn" disabled={busy || !!activeOperation || network.blocking_runs.length > 0} onclick={() => stop(network)}>Stop private network</button>
          {#if network.observed_state === 'ready'}
            <button class="btn" disabled={busy || !!activeOperation || network.blocking_runs.length > 0} onclick={() => reconfigure(network, 'add')}>Add selected participants</button>
          {/if}
          {#if ['failed', 'degraded', 'verifying'].includes(network.observed_state)}
            <button class="btn" disabled={busy || !!activeOperation || network.blocking_runs.length > 0} onclick={() => reconfigure(network, 'retry')}>Retry verification and setup</button>
          {/if}
        {/if}
      </section>
    {/each}
    {#if activeOperation}<p role="status">Operation in progress. Participant results are being checked.</p><button class="btn" disabled={busy} onclick={cancel}>Cancel and roll back</button>{/if}
    {#each cleanupJournals as journal}
      <p>Local cleanup: {journal.exact_address_prefix} · {journal.phase}</p>
      <button class="btn" disabled={busy} onclick={() => reviewCleanup(journal.journal_id)}>Review local address</button>
    {/each}
    {#if cleanupReview}
      <p>Review this exact address: {cleanupReview.journal.exact_address_prefix}</p>
      <pre>{JSON.stringify(cleanupReview.snapshot, null, 2)}</pre>
      <button class="btn" disabled={busy} onclick={() => resolveCleanup('release_ownership')}>Keep address and release app ownership</button>
      <button class="btn" disabled={busy} onclick={() => resolveCleanup('confirm_owned')}>Confirm app ownership and remove exact address</button>
    {/if}
    <button class="btn" disabled={busy} onclick={refresh}>Refresh prerequisites and status</button>
  </div>
</details>

<style>
  .legacy-network { margin-top: 12px; }
  summary { padding: 12px; cursor: pointer; display: flex; justify-content: space-between; gap: 12px; }
  .network-content { padding: 0 12px 12px; display: grid; gap: 10px; font-size: 12px; }
  label { display: grid; gap: 5px; }
  label:has(input[type="checkbox"]) { display: flex; align-items: center; gap: 8px; }
  input[type="checkbox"] { width: auto; margin: 0; }
  textarea, select, input:not([type='checkbox']) { width: 100%; padding: 7px; background: var(--bg); color: var(--fg); border: 1px solid var(--border); border-radius: 4px; }
  textarea { font-family: monospace; }
  fieldset, .network-status { border: 1px solid var(--border); padding: 10px; }
  button { justify-self: start; }
</style>
