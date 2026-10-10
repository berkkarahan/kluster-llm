<script lang="ts">
  import { createApp } from '$lib/app.svelte';
  import type { ClusterInteractor } from '../../cluster/interactor.svelte';
  let { presenter = createApp().cluster }: { presenter?: ClusterInteractor } = $props();
  /**
   * System Settings > Network & Security: RPC bind, WireGuard overlay, join key /
   * master host, and live overlay / netsec status. Values are the draft applied
   * on the next Start/Join (PUT has no network fields), so controls lock when the
   * cluster is live. Mirrors the Home sidebar inputs.
   */

  const live = $derived(presenter.viewState() === 'live');
  const isWorker = $derived(presenter.status?.role === 'worker');
  const wg = $derived(presenter.status?.wg_address);
  const overlayStatus = $derived(presenter.status?.overlay_status);
  const warnings = $derived(presenter.status?.netsec_warnings ?? []);
</script>

<section class="panel p-3.5" aria-labelledby="net-label">
  <h2 id="net-label" class="mono text-[11px] uppercase tracking-wider mb-3" style="color: var(--muted)">
    Network &amp; Security
  </h2>

  <div class="grid grid-cols-1 gap-3 sm:grid-cols-2">
    <div>
      <label class="mono text-[11px]" style="color: var(--muted)" for="net-rpc-bind">RPC bind</label>
      <input
        id="net-rpc-bind"
        class="field mono"
        bind:value={presenter.rpcBindInput}
        placeholder="auto private IP"
        disabled={live}
      />
    </div>
    <div>
      <label class="mono text-[11px]" style="color: var(--muted)" for="net-overlay">Overlay</label>
      <select
        id="net-overlay"
        class="field mono"
        bind:value={presenter.overlay}
        disabled={live}
      >
        <option value="none">Off (LAN bind)</option>
        <option value="wireguard">WireGuard</option>
      </select>
    </div>
    {#if !isWorker}
      <div>
        <label class="mono text-[11px]" style="color: var(--muted)" for="net-join-key">
          Join key <span style="color: var(--muted)">(master)</span>
        </label>
        <input
          id="net-join-key"
          class="field mono"
          bind:value={presenter.joinKeyInput}
          disabled={live}
        />
      </div>
      <div>
        <label class="mono text-[11px]" style="color: var(--muted)" for="net-master-host">
          Master host <span style="color: var(--muted)">(optional)</span>
        </label>
        <input
          id="net-master-host"
          class="field mono"
          bind:value={presenter.masterHostInput}
          placeholder="master LAN IP if mDNS fails"
          disabled={live}
        />
      </div>
    {:else}
      <p class="mono text-[11px] self-end" style="color: var(--muted)">Joined as worker</p>
    {/if}
  </div>

  {#if wg}
    <p class="mono text-[11px] mt-3" style="color: var(--muted)">
      wg {wg} <span style="color: var(--accent)">up</span>
    </p>
  {/if}
  {#if overlayStatus}
    <p class="mono text-[11px] mt-1" style="color: var(--muted)">{overlayStatus}</p>
  {/if}
  {#each warnings as w (w)}
    <p class="flex items-center gap-2 mt-2" style="color: var(--danger, #c44)">
      <span class="chip chip-danger">netsec</span>
      <span>{w}</span>
    </p>
  {/each}
  <p class="mono text-[10px] mt-3" style="color: var(--muted)">
    RPC port 50052 stays LAN-only; prefer the WireGuard overlay on shared networks.
  </p>
</section>
