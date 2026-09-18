<script lang="ts">
  import { appStore } from '$lib/store.svelte';

  function fmtBytes(n?: number | null) {
    if (n == null) return '—';
    const gb = n / 1e9;
    return `${gb.toFixed(1)} GB`;
  }

  const members = $derived(Object.values(appStore.status?.members ?? {}));
  const view = $derived(appStore.viewState());
  const live = $derived(view === 'live');
  const idle = $derived(view === 'idle');
  const isMaster = $derived(appStore.status?.role === 'master');
  const isWorker = $derived(appStore.status?.role === 'worker');
  const startJoinDisabled = $derived(!idle || !!appStore.busy);
  const networkLocked = $derived(live);
</script>

<div class="grid h-[calc(100vh-40px)] grid-cols-1 lg:grid-cols-[280px_minmax(0,1fr)_360px]">
  <!-- Cluster controls -->
  <aside class="panel border-r flex flex-col min-h-0" style="border-color: var(--border)">
    <div class="px-3 py-2 border-b mono text-[11px] uppercase tracking-wider shrink-0" style="border-color: var(--border); color: var(--muted)">
      Cluster
    </div>
    <div class="p-3 flex flex-col gap-2 overflow-y-auto flex-1 min-h-0">
      {#if !isWorker}
        <label class="mono text-[11px]" style="color: var(--muted)">Join key</label>
        <input
          class="field mono"
          bind:value={appStore.joinKeyInput}
          placeholder={idle ? 'auto or paste' : '—'}
          readonly={networkLocked}
          disabled={view === 'loading' || view === 'offline'}
        />
      {:else}
        <p class="mono text-[11px]" style="color: var(--muted)">Joined as worker</p>
      {/if}
      <label class="mono text-[11px]" style="color: var(--muted)">RPC bind (optional)</label>
      <input
        class="field mono"
        bind:value={appStore.rpcBindInput}
        placeholder={idle ? 'auto private IP' : '—'}
        readonly={networkLocked}
        disabled={view === 'loading' || view === 'offline'}
      />
      <label class="mono text-[11px]" style="color: var(--muted)">Overlay</label>
      <select class="field mono" bind:value={appStore.overlay} disabled={!idle || !!appStore.busy}>
        <option value="none">Off (LAN bind)</option>
        <option value="wireguard">WireGuard</option>
      </select>
      <label class="mono text-[11px] mt-1" style="color: var(--muted)">Device</label>
      <select
        class="field mono"
        value={appStore.device}
        disabled={!!appStore.busy}
        onchange={(e) => {
          const v = (e.currentTarget as HTMLSelectElement).value;
          if (v === 'cpu' || v === 'gpu') void appStore.setDevice(v);
        }}
      >
        <option value="gpu">GPU</option>
        <option value="cpu">CPU</option>
      </select>
      {#if appStore.device === 'gpu'}
        {#if appStore.gpus.length > 0}
          <label class="mono text-[11px] mt-1" style="color: var(--muted)">
            GPUs ({appStore.gpuBackend})
          </label>
          <div class="flex flex-col gap-1">
            {#each appStore.gpus as g}
              <label class="mono text-[11px] flex items-start gap-2 cursor-pointer" style="color: var(--fg)">
                <input
                  type="checkbox"
                  class="mt-0.5"
                  checked={appStore.selectedGpuIds.includes(g.index)}
                  disabled={appStore.gpus.length === 1 || !!appStore.busy}
                  onchange={() => {
                    appStore.toggleGpu(g.index);
                    void appStore.persistGpuSelection();
                  }}
                />
                <span>
                  [{g.index}] {g.name}
                  <span style="color: var(--muted)"> {fmtBytes(g.vram_bytes)}</span>
                </span>
              </label>
            {/each}
          </div>
        {/if}
        <label class="mono text-[11px] mt-1" style="color: var(--muted)" for="home-vram-pct">
          VRAM budget (%)
        </label>
        <div class="flex items-center gap-2">
          <input
            id="home-vram-pct"
            class="flex-1"
            type="range"
            min="10"
            max="100"
            step="1"
            value={appStore.vramPercent}
            disabled={!!appStore.busy}
            oninput={(e) => {
              appStore.vramPercent = Number((e.currentTarget as HTMLInputElement).value);
            }}
            onchange={(e) => {
              const v = Number((e.currentTarget as HTMLInputElement).value);
              void appStore.setVramPercent(v);
            }}
          />
          <input
            class="field mono"
            style="width: 4.5rem"
            type="number"
            min="10"
            max="100"
            step="1"
            value={appStore.vramPercent}
            disabled={!!appStore.busy}
            onchange={(e) => {
              const v = Number((e.currentTarget as HTMLInputElement).value);
              void appStore.setVramPercent(v);
            }}
          />
        </div>
      {:else if appStore.device === 'cpu'}
        <label class="mono text-[11px] mt-1" style="color: var(--muted)" for="home-ram-pct">
          RAM budget (%)
        </label>
        <div class="flex items-center gap-2">
          <input
            id="home-ram-pct"
            class="flex-1"
            type="range"
            min="10"
            max="100"
            step="1"
            value={appStore.ramPercent}
            disabled={!!appStore.busy}
            oninput={(e) => {
              appStore.ramPercent = Number((e.currentTarget as HTMLInputElement).value);
            }}
            onchange={(e) => {
              const v = Number((e.currentTarget as HTMLInputElement).value);
              void appStore.setRamPercent(v);
            }}
          />
          <input
            class="field mono"
            style="width: 4.5rem"
            type="number"
            min="10"
            max="100"
            step="1"
            value={appStore.ramPercent}
            disabled={!!appStore.busy}
            onchange={(e) => {
              const v = Number((e.currentTarget as HTMLInputElement).value);
              void appStore.setRamPercent(v);
            }}
          />
        </div>
      {/if}
      <div class="grid grid-cols-2 gap-2 mt-1">
        <button class="btn btn-accent" disabled={startJoinDisabled} onclick={() => appStore.startMaster()}
          >Start</button
        >
        <button class="btn btn-danger" disabled={!!appStore.busy || !isMaster} onclick={() => appStore.stopCluster()}
          >Stop</button
        >
        <button
          class="btn"
          disabled={startJoinDisabled || !appStore.joinKeyInput}
          onclick={() => appStore.joinCluster()}>Join</button
        >
        <button class="btn" disabled={!!appStore.busy || !isWorker} onclick={() => appStore.leaveCluster()}
          >Leave</button
        >
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
      {#if appStore.status?.gpu_ids?.length}
        <p class="mono text-[11px]" style="color: var(--muted)">
          active GPUs
          <span style="color: var(--fg)">
            {appStore.status.gpu_ids
              .map((id) => {
                const g = appStore.gpus.find((x) => x.index === id);
                return g ? `${id}:${g.name}` : String(id);
              })
              .join(' · ')}
          </span>
        </p>
      {/if}
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
            <th class="font-medium py-1 border-b" style="border-color: var(--border)">gpus</th>
            <th class="font-medium py-1 border-b" style="border-color: var(--border)">ram</th>
            <th class="font-medium py-1 border-b" style="border-color: var(--border)">vram</th>
            <th class="font-medium py-1 border-b" style="border-color: var(--border)">latency</th>
          </tr>
        </thead>
        <tbody>
          {#if view === 'loading'}
            <tr>
              <td colspan="8" class="py-6 mono" style="color: var(--muted)">Loading cluster…</td>
            </tr>
          {:else if members.length}
            {#each members as m}
              <tr class="mono">
                <td class="py-1.5 border-b" style="border-color: var(--border)">{m.node_id}</td>
                <td class="py-1.5 border-b" style="border-color: var(--border)">{m.host}</td>
                <td class="py-1.5 border-b" style="border-color: var(--border)">{m.role}</td>
                <td class="py-1.5 border-b" style="border-color: var(--border)">{m.backend}</td>
                <td class="py-1.5 border-b" style="border-color: var(--border)"
                  >{m.gpu_ids?.length ? m.gpu_ids.join(',') : '—'}</td
                >
                <td class="py-1.5 border-b" style="border-color: var(--border)">{fmtBytes(m.total_ram_bytes)}</td>
                <td class="py-1.5 border-b" style="border-color: var(--border)">{fmtBytes(m.vram_bytes)}</td>
                <td class="py-1.5 border-b" style="border-color: var(--border)">
                  {m.latency_ms != null ? `${Number(m.latency_ms).toFixed(1)} ms` : '—'}
                </td>
              </tr>
            {/each}
          {:else if idle}
            <tr>
              <td colspan="8" class="py-6 mono" style="color: var(--muted)">No members — Start as master or Join a cluster.</td>
            </tr>
          {:else}
            <tr>
              <td colspan="8" class="py-6 mono" style="color: var(--muted)">
                {view === 'offline' ? 'Disconnected — keeping last cluster view.' : 'Waiting for members…'}
              </td>
            </tr>
          {/if}
        </tbody>
      </table>

      {#if appStore.status?.apply_error}
        <p class="mono text-[11px] mt-3" style="color: var(--danger, #c44)">{appStore.status.apply_error}</p>
      {/if}
      {#if appStore.status?.apply_warning}
        <p class="mono text-[11px] mt-2" style="color: var(--warn, #b80)">{appStore.status.apply_warning}</p>
      {/if}
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
