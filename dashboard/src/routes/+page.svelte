<script lang="ts">
  import { appStore } from '$lib/store.svelte';

  function fmtBytes(n?: number | null) {
    if (n == null) return '—';
    const gb = n / 1e9;
    return `${gb.toFixed(1)} GB`;
  }

  const members = $derived(Object.values(appStore.status?.members ?? {}));
</script>

<div class="grid h-[calc(100vh-40px)] grid-cols-1 lg:grid-cols-[280px_minmax(0,1fr)_360px]">
  <!-- Cluster controls -->
  <aside class="panel border-r flex flex-col min-h-0" style="border-color: var(--border)">
    <div class="px-3 py-2 border-b mono text-[11px] uppercase tracking-wider" style="border-color: var(--border); color: var(--muted)">
      Cluster
    </div>
    <div class="p-3 flex flex-col gap-2">
      <label class="mono text-[11px]" style="color: var(--muted)">Join key</label>
      <input class="field mono" bind:value={appStore.joinKeyInput} placeholder="auto or paste" />
      <label class="mono text-[11px]" style="color: var(--muted)">Master host (join)</label>
      <input class="field mono" bind:value={appStore.masterHost} placeholder="192.168.10.1" />
      <label class="mono text-[11px]" style="color: var(--muted)">RPC bind (optional)</label>
      <input class="field mono" bind:value={appStore.rpcBindInput} placeholder="auto private IP" />
      <label class="mono text-[11px]" style="color: var(--muted)">Overlay</label>
      <select class="field mono" bind:value={appStore.overlay}>
        <option value="none">Off (LAN bind)</option>
        <option value="wireguard">WireGuard</option>
      </select>
      <div class="grid grid-cols-2 gap-2 mt-1">
        <button class="btn btn-accent" disabled={!!appStore.busy} onclick={() => appStore.startMaster()}>Start</button>
        <button class="btn btn-danger" disabled={!!appStore.busy} onclick={() => appStore.stopCluster()}>Stop</button>
        <button class="btn" disabled={!!appStore.busy || !appStore.joinKeyInput || !appStore.masterHost} onclick={() => appStore.joinCluster()}
          >Join</button
        >
        <button class="btn" disabled={!!appStore.busy} onclick={() => appStore.leaveCluster()}>Leave</button>
      </div>
      {#if appStore.status?.join_key}
        <p class="mono text-[11px] mt-2 break-all" style="color: var(--muted)">
          key <span style="color: var(--accent)">{appStore.status.join_key}</span>
        </p>
      {/if}
      <p class="mono text-[11px]" style="color: var(--muted)">
        node <span style="color: var(--fg)">{appStore.status?.node_id ?? '—'}</span>
      </p>
      <p class="mono text-[11px]" style="color: var(--muted)">
        rpc <span style="color: var(--fg)">{appStore.status?.rpc_bind ?? '—'}</span>
      </p>
      <p class="mono text-[11px]" style="color: var(--muted)">
        overlay <span style="color: var(--fg)">{appStore.status?.overlay ?? 'none'}</span>
        {#if appStore.status?.wg_address}
          <span style="color: var(--accent)"> {appStore.status.wg_address}</span>
        {/if}
      </p>
      {#if appStore.status?.overlay_status}
        <p class="mono text-[11px]" style="color: var(--muted)">{appStore.status.overlay_status}</p>
      {/if}
      {#each appStore.status?.netsec_warnings ?? [] as w}
        <p class="mono text-[11px]" style="color: var(--danger, #c44)">{w}</p>
      {/each}
    </div>
  </aside>

  <!-- Topology / status -->
  <section class="flex flex-col min-h-0 border-r" style="border-color: var(--border)">
    <div class="px-3 py-2 border-b mono text-[11px] uppercase tracking-wider" style="border-color: var(--border); color: var(--muted)">
      Topology
    </div>
    <div class="p-3 overflow-auto flex-1">
      <table class="w-full text-left border-collapse">
        <thead>
          <tr class="mono text-[11px]" style="color: var(--muted)">
            <th class="font-medium py-1 border-b" style="border-color: var(--border)">id</th>
            <th class="font-medium py-1 border-b" style="border-color: var(--border)">host</th>
            <th class="font-medium py-1 border-b" style="border-color: var(--border)">role</th>
            <th class="font-medium py-1 border-b" style="border-color: var(--border)">backend</th>
            <th class="font-medium py-1 border-b" style="border-color: var(--border)">ram</th>
            <th class="font-medium py-1 border-b" style="border-color: var(--border)">vram</th>
          </tr>
        </thead>
        <tbody>
          {#each members as m}
            <tr class="mono">
              <td class="py-1.5 border-b" style="border-color: var(--border)">{m.node_id}</td>
              <td class="py-1.5 border-b" style="border-color: var(--border)">{m.host}</td>
              <td class="py-1.5 border-b" style="border-color: var(--border)">{m.role}</td>
              <td class="py-1.5 border-b" style="border-color: var(--border)">{m.backend}</td>
              <td class="py-1.5 border-b" style="border-color: var(--border)">{fmtBytes(m.total_ram_bytes)}</td>
              <td class="py-1.5 border-b" style="border-color: var(--border)">{fmtBytes(m.vram_bytes)}</td>
            </tr>
          {:else}
            <tr>
              <td colspan="6" class="py-6 mono" style="color: var(--muted)">No members — Start as master or Join a cluster.</td>
            </tr>
          {/each}
        </tbody>
      </table>

      {#if appStore.status?.last_plan}
        <pre
          class="mt-4 p-3 panel mono text-[11px] overflow-auto max-h-48"
          style="border-color: var(--border)">{JSON.stringify(appStore.status.last_plan, null, 2)}</pre
        >
      {/if}
    </div>
  </section>

  <!-- Chat -->
  <aside class="flex flex-col min-h-0 panel" style="border-color: var(--border)">
    <div class="px-3 py-2 border-b mono text-[11px] uppercase tracking-wider flex items-center" style="border-color: var(--border); color: var(--muted)">
      Chat
      <span class="ml-auto" style="color: var(--fg)">{appStore.status?.model_name ?? 'no model'}</span>
    </div>
    <div class="flex-1 overflow-auto p-3 flex flex-col gap-3">
      {#each appStore.chat as msg}
        <div class="mono text-[12px]">
          <div style="color: var(--muted)">{msg.role}</div>
          <div class="whitespace-pre-wrap" style="color: var(--fg)">{msg.content || '…'}</div>
        </div>
      {:else}
        <p class="mono text-[12px]" style="color: var(--muted)">
          Run a model, then chat via OpenAI-compatible proxy on the agent.
        </p>
      {/each}
    </div>
    <div class="p-3 border-t flex gap-2" style="border-color: var(--border)">
      <input
        class="field mono flex-1"
        placeholder={appStore.status?.inference_up ? 'Message…' : 'Start inference first'}
        bind:value={appStore.chatInput}
        disabled={!appStore.status?.inference_up || appStore.chatStreaming}
        onkeydown={(e) => e.key === 'Enter' && appStore.sendChat()}
      />
      {#if appStore.chatStreaming}
        <button class="btn btn-danger" onclick={() => appStore.stopChat()}>Stop</button>
      {:else}
        <button class="btn btn-accent" disabled={!appStore.status?.inference_up} onclick={() => appStore.sendChat()}
          >Send</button
        >
      {/if}
    </div>
  </aside>
</div>
