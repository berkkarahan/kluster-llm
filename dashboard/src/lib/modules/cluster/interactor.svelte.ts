import { errors } from '../../shared/errors';
import { apiErrorMessage } from '../../shared/http.svelte';
import { pushToast } from '../../shared/toast';
import type { HttpClient } from '../../shared/http.svelte';
import type { LiveInteractor } from '../../live/interactor.svelte';
import type { GpuInfo } from '../../shared/entities';
import type { ClusterView, FastSubnetReachability, Member, Status } from './entities';
import { clusterViewState } from './presenter.svelte';

/**
 * Plan-relevant projection of a member. Deliberately excludes liveness/telemetry
 * (`last_seen`, `latency_ms`, `available_ram_bytes`) that changes on every 1–2 s
 * heartbeat; including it made status polling re-plan and blank the Plan & Run
 * panel once per poll. Identity/capacity/backend changes still trip it.
 */
function memberFingerprint(m: Member): unknown {
  return {
    role: m.role,
    backend: m.backend,
    actual_backend: m.actual_backend,
    requested_backend: m.requested_backend,
    unified_memory: m.unified_memory,
    rpc_devices: m.rpc_devices,
    rpc_capabilities: m.rpc_capabilities,
    rpc_status: m.rpc_status,
    total_ram_bytes: m.total_ram_bytes,
    vram_bytes: m.vram_bytes,
    gpu_ids: m.gpu_ids,
    host: m.host,
    rpc_port: m.rpc_port,
    devices: (m.devices ?? []).map((d) => ({
      index: d.index,
      name: d.name,
      device: d.device,
      backend: d.backend,
      vram_bytes: d.vram_bytes
    }))
  };
}

function fp(s: Status | null): string {
  if (!s) return '';
  return JSON.stringify({
    role: s.role,
    runtime_target: s.runtimes?.target?.version.runtime_id ?? s.runtimes?.selected,
    runtime_prepared: (s.runtimes?.nodes ?? []).map(n => [n.node_id, n.prepared?.runtime_id, n.prepared?.build_id]),
    members: Object.fromEntries(
      Object.entries(s.members ?? {}).map(([id, m]) => [id, memberFingerprint(m)])
    ),
    model_name: s.model_name,
    inference_up: s.inference_up,
    last_plan: s.last_plan,
    process_pid: s.process_pid,
    rpc_bind: s.rpc_bind,
    netsec_warnings: s.netsec_warnings,
    gpu_ids: s.gpu_ids,
    apply_error: s.apply_error,
    apply_warning: s.apply_warning
  });
}

type NodeSettingsLink = {
  loadGpus(): Promise<void>;
  gpuStartWarning(): void;
  gpus: GpuInfo[];
  selectedGpuIds: number[];
  defaultGpuIds(): number[];
};

type UsageLink = { resetAfterClusterStop(): void };

export class ClusterInteractor {
  status = $state<Status | null>(null);
  statusSeen = $state(false);
  joinKeyInput = $state('');
  masterHostInput = $state('');
  rpcBindInput = $state('');
  fastSubnetReachability = $state<FastSubnetReachability | null>(null);
  fastSubnetProbe = $state<'idle' | 'checking' | 'done' | 'failed'>('idle');
  reconnectingNodeId = $state('');
  fastSubnetErrors = $state<Record<string, string>>({});
  clearingLifecycle = false;
  private fingerprint = '';
  nodeSettings: NodeSettingsLink | null = null;
  usage: UsageLink | null = null;
  clearErrors: () => void = () => {};
  onStatusReplacing: (current: Status | null, next: Status) => void = () => {};
  onAccel: (data: Status) => void = () => {};

  constructor(readonly http: HttpClient, readonly live: LiveInteractor) {}

  get busy() { return this.http.busy; }
  set busy(value: string) { this.http.busy = value; }

  viewState(): ClusterView {
    return clusterViewState(this.statusSeen, this.status, this.live.connected);
  }

  refresh() {
    return this.live.refresh();
  }

  applyStatus(data: Status) {
    const initial = !this.statusSeen;
    const previous = initial ? null : this.status;
    if (!this.clearingLifecycle && previous && ((previous.role !== 'idle' && data.role === 'idle')
      || ((previous.inference_up || previous.process_role === 'master' && previous.process_pid)
        && !data.inference_up && !data.process_pid)
      || (previous.role === 'worker' && previous.members?.[previous.node_id] && !data.members?.[previous.node_id]))) {
      this.clearErrors();
    }
    errors.observe('apply', data.apply_error ?? '');
    errors.observe('inference-activity', data.inference_activity?.phase === 'error'
      ? data.inference_activity.detail ?? 'Inference failed' : '');
    errors.observe('update', data.update?.error ?? '');
    for (const worker of previous?.update?.workers ?? []) {
      if (!data.update?.workers.some(next => (next.node_id ?? next.host) === (worker.node_id ?? worker.host))) {
        errors.observe(`update-worker:${worker.node_id ?? worker.host}`, '');
      }
    }
    for (const worker of data.update?.workers ?? []) {
      errors.observe(`update-worker:${worker.node_id ?? worker.host}`, worker.error ?? '', '', initial);
    }
    this.onStatusReplacing(this.status, data);
    this.statusSeen = true;
    this.status = data;
    this.live.connected = true;
    const live = data.role === 'master' || data.role === 'worker';
    if (live) {
      if (data.join_key) this.joinKeyInput = data.join_key;
      if (data.master_host) this.masterHostInput = data.master_host;
      if (data.rpc_bind) this.rpcBindInput = data.rpc_bind;
    }
    this.onAccel(data);
    const next = fp(data);
    if (next !== this.fingerprint) {
      this.fingerprint = next;
      this.live.emitStatusFingerprint(data);
    }
  }

  async refreshDirect() {
    const errorGeneration = errors.generation;
    try {
      const r = await fetch('/v2/node');
      if (!r.ok) throw new Error(`status ${r.status}`);
      const data = (await r.json()) as Status;
      if (errorGeneration === errors.generation) this.applyStatus(data);
    } catch {
      if (errorGeneration !== errors.generation) return;
      if (this.live.connected) this.clearErrors();
      this.statusSeen = true;
      this.live.connected = false;
    }
  }

  private gpuBody(): Record<string, unknown> {
    const settings = this.nodeSettings;
    const body: Record<string, unknown> = {};
    if (settings && settings.gpus.length > 0) {
      body.gpu_ids = settings.selectedGpuIds.length > 0 ? settings.selectedGpuIds : settings.defaultGpuIds();
    }
    return body;
  }

  async startMaster() {
    await this.nodeSettings?.loadGpus();
    this.nodeSettings?.gpuStartWarning();
    const body: Record<string, unknown> = {
      key: this.joinKeyInput || null,
      rpc_bind: this.rpcBindInput || null,
      ...this.gpuBody()
    };
    await this.http.command('/v2/cluster', body);
  }

  async stopCluster() {
    this.clearErrors();
    this.clearingLifecycle = true;
    try {
      await this.http.command('/v2/cluster', undefined, 'DELETE');
      this.resetClusterView();
    } finally { this.clearingLifecycle = false; }
  }

  async joinCluster() {
    await this.nodeSettings?.loadGpus();
    this.nodeSettings?.gpuStartWarning();
    const masterHost = this.masterHostInput.trim();
    const body: Record<string, unknown> = {
      key: this.joinKeyInput,
      master_port: 18765,
      rpc_port: 50052,
      rpc_bind: this.rpcBindInput || null,
      ...this.gpuBody()
    };
    if (masterHost) body.master_host = masterHost;
    await this.http.command('/v2/cluster/membership', body);
  }

  async leaveCluster() {
    this.clearErrors();
    this.clearingLifecycle = true;
    try {
      await this.http.command('/v2/cluster/membership', undefined, 'DELETE');
      this.resetClusterView();
    } finally { this.clearingLifecycle = false; }
  }

  private resetClusterView() {
    if (this.status) {
      this.status = {
        ...this.status,
        role: 'idle',
        members: {},
        join_key: null,
        master_host: null,
      };
    }
    this.usage?.resetAfterClusterStop();
    this.rpcBindInput = '';
  }

    async probeFastSubnetOnce() {
    if (this.fastSubnetProbe !== 'idle' || this.status?.role !== 'master') return;
    this.fastSubnetProbe = 'checking';
    try {
      const response = await fetch('/v2/cluster/fast-subnet/reachability');
      const text = await response.text();
      let data: unknown = null;
      try {
        data = text ? JSON.parse(text) : null;
      } catch {
        data = null;
      }
      const reachability = data as FastSubnetReachability | null;
      if (!response.ok || !reachability || !Array.isArray(reachability.workers)) {
        this.fastSubnetProbe = 'failed';
        this.fastSubnetReachability = null;
        return;
      }
      this.fastSubnetReachability = reachability;
      this.fastSubnetProbe = 'done';
    } catch {
      this.fastSubnetProbe = 'failed';
      this.fastSubnetReachability = null;
    }
  }

  async reconnectWorkerFastSubnet(nodeId: string) {
    const source = `fast-subnet:${nodeId}`;
    const errorGeneration = errors.generation;
    errors.observe(source, '');
    this.fastSubnetErrors = { ...this.fastSubnetErrors, [nodeId]: '' };
    this.reconnectingNodeId = nodeId;
    try {
      const response = await fetch('/v2/cluster/fast-subnet/reconnect', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ node_id: nodeId })
      });
      const text = await response.text();
      let data: unknown = null;
      try {
        data = text ? JSON.parse(text) : null;
      } catch {
        data = text;
      }
      if (!response.ok) {
        throw new Error(apiErrorMessage(data, text, response.status, 'reconnect'));
      }
      pushToast('ok', 'Worker rejoined on the fast subnet');
      await this.refresh();
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      this.fastSubnetErrors = { ...this.fastSubnetErrors, [nodeId]: message };
      errors.publish(source, message, errorGeneration);
      pushToast('danger', message, 4000, { generation: errorGeneration });
    } finally {
      if (this.reconnectingNodeId === nodeId) this.reconnectingNodeId = '';
    }
  }

  async updateWorkers() {
    const errorGeneration = errors.generation;
    errors.observe('update', '');
    for (const worker of this.status?.update?.workers ?? []) errors.observe(`update-worker:${worker.node_id ?? worker.host}`, '');
    this.busy = '/v2/cluster/updates';
    try {
      const r = await fetch('/v2/cluster/updates', { method: 'POST' });
      const text = await r.text();
      let data: unknown = null;
      try {
        data = text ? JSON.parse(text) : null;
      } catch {
        data = text;
      }
      if (!r.ok) {
        const msg = apiErrorMessage(data, text, r.status, 'update');
        throw new Error(msg);
      }
      pushToast('ok', 'worker update started');
      await this.refresh();
      return data;
    } catch (error) {
      pushToast('danger', String(error), 4000, { generation: errorGeneration });
      throw error;
    } finally {
      this.busy = '';
    }
  }

}

