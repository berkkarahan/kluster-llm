import { errors } from '../../shared/errors';
import type { HttpClient } from '../../shared/http.svelte';
import type { ClusterInteractor } from '../cluster/interactor.svelte';
import type { RunInteractor } from '../run/interactor.svelte';
import type { RuntimeLibrary, RuntimeSources, RuntimeVersion, PrimaDeviceReport } from './entities';

/** llama.cpp library commands. The view keeps dialogs, busy flags, and focus. */
export class RuntimesInteractor {
  constructor(
    readonly http: HttpClient,
    readonly cluster: ClusterInteractor,
    readonly run: RunInteractor
  ) {}

  get status() {
    return this.cluster.status;
  }

  get planResult() {
    return this.run.planResult;
  }

  async refresh(signal?: AbortSignal) {
    const generation = errors.generation;
    const data = await this.http.networkRequest<RuntimeLibrary>('/v2/node/runtimes', 'GET', undefined, signal);
    if (generation === errors.generation && this.cluster.status) {
      this.cluster.status = { ...this.cluster.status, runtimes: data };
    }
    return data;
  }

  private async act(path: string, method: string, value?: unknown) {
    await this.http.networkRequest(path, method, value);
    await this.refresh();
  }

  add(value: unknown) {
    return this.act('/v2/node/runtimes', 'POST', value);
  }

  setTarget(body: unknown) {
    return this.act('/v2/cluster/runtime-target', 'PUT', body);
  }

  build(runtimeId: string) {
    return this.act('/v2/node/runtime-builds', 'POST', {
      runtime_id: runtimeId,
      backend: this.cluster.status?.accel_backend ?? 'auto',
      request_id: crypto.randomUUID()
    });
  }

  selectLocal(runtimeId: string) {
    return this.act('/v2/node/runtime-selection', 'PUT', {
      runtime_id: runtimeId,
      expected_revision: 0,
      request_id: crypto.randomUUID()
    });
  }

  remove(runtimeId: string) {
    return this.act(`/v2/node/runtimes/${runtimeId}`, 'DELETE');
  }

  retryNode(nodeId: string) {
    return this.act(`/v2/cluster/runtime-target/nodes/${encodeURIComponent(nodeId)}/retry`, 'POST');
  }

  cancelLocal(jobId: string) {
    return this.act(`/v2/node/runtime-builds/${jobId}/cancel`, 'POST');
  }

  cancelRemote(nodeId: string, jobId: string) {
    return this.act(`/v2/cluster/runtime-target/nodes/${encodeURIComponent(nodeId)}/builds/${jobId}/cancel`, 'POST');
  }

  primaPlan(runtimeId: string, settings: unknown) {
    return this.http.networkRequest<Record<string, any>>(`/v2/node/runtimes/${runtimeId}/prima-plan`, 'POST', settings);
  }

  preparePrima(runtimeId: string, settings: unknown, planDigest: string, profile: boolean) {
    return this.http.networkRequest<Record<string, any>>('/v2/cluster/prima-preparations', 'POST', {
      runtime_id: runtimeId, settings, plan_digest: planDigest,
      request_id: crypto.randomUUID(), include_profile: profile
    });
  }

  primaJob(id: string) {
    return this.http.networkRequest<Record<string, any>>(`/v2/cluster/prima-preparations/${id}`, 'GET');
  }

  cancelPrima(id: string) {
    return this.http.networkRequest(`/v2/cluster/prima-preparations/${id}/cancel`, 'POST');
  }

  sources(repository: string, signal?: AbortSignal) {
    return this.http.networkRequest<RuntimeSources>(
      `/v2/node/runtimes/sources?repository=${encodeURIComponent(repository)}`,
      'GET',
      undefined,
      signal
    );
  }

  primaDevices(signal?: AbortSignal) {
    return this.http.networkRequest<PrimaDeviceReport>(
      '/v2/node/prima-devices', 'GET', undefined, signal
    );
  }

  resolve<T = RuntimeVersion>(body: unknown, signal?: AbortSignal) {
    return this.http.networkRequest<T>('/v2/node/runtimes/resolve', 'POST', body, signal);
  }

  localLogs(jobId: string) {
    return this.http.networkRequest<{ text: string }>(`/v2/node/runtime-builds/${jobId}/logs`);
  }

  remoteLogs(nodeId: string, jobId: string) {
    return this.http.networkRequest<{ text: string }>(
      `/v2/cluster/runtime-target/nodes/${encodeURIComponent(nodeId)}/builds/${jobId}/logs`
    );
  }
}
