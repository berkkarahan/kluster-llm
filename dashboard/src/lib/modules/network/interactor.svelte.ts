import type { HttpClient } from '../../shared/http.svelte';
import type { ClusterInteractor } from '../cluster/interactor.svelte';
import type { PrimaListenerResource, PrimaListenerReview } from './entities';

/** Named network-setup and adapter requests. Views keep polling, dialogs, and abort control. */
export class NetworkInteractor {
  selectedNetworkId = $state('');

  constructor(readonly http: HttpClient, readonly cluster: ClusterInteractor) {}

  get master() {
    return this.cluster.status?.role === 'master';
  }

  localSetup<T>(signal?: AbortSignal) {
    return this.http.networkRequest<T>('/v2/node/network-setup', 'GET', undefined, signal);
  }

  clusterSetup<T>(signal?: AbortSignal) {
    return this.http.networkRequest<T>('/v2/cluster/network-setup', 'GET', undefined, signal);
  }

  acknowledgeAdministrator(acknowledged: boolean) {
    return this.http.networkRequest('/v2/node/network-setup/administrator', 'POST', { acknowledged });
  }

  recovery<T>(networkId: string) {
    return this.http.networkRequest<T>(`/v2/node/network-setup/recovery/${networkId}`);
  }

  decideRecovery(networkId: string, decision: string, snapshot_digest: string) {
    return this.http.networkRequest(`/v2/node/network-setup/recovery/${networkId}`, 'POST', { decision, snapshot_digest });
  }

  async primaRecovery(resource: PrimaListenerResource, signal?: AbortSignal) {
    const value = await this.http.networkRequest<PrimaListenerReview>(
      `/v2/node/network-setup/recovery/${encodeURIComponent(resource.network_id)}/prima/${encodeURIComponent(resource.run_id)}`,
      'GET', undefined, signal);
    if (value?.network_id !== resource.network_id || value?.run_id !== resource.run_id
      || value?.manifest_digest !== resource.manifest_digest) {
      throw new Error('Prima rule review differs from the selected run');
    }
    if (typeof value.snapshot_digest !== 'string' || !/^[a-f0-9]{64}$/.test(value.snapshot_digest) || !Array.isArray(value.rules)
      || value.rules.length < 1 || value.rules.length > 2
      || value.rules.some(rule => !rule || typeof rule !== 'object' || !Number.isSafeInteger(rule.port) || rule.port < 1024 || rule.port > 65535
        || !['exact', 'changed', 'unknown', 'absent'].includes(rule.ownership)
        || !Array.isArray(rule.peers) || !rule.peers.length || rule.peers.length > 32 || !rule.peers.every(peer => typeof peer === 'string'))) {
      throw new Error('Prima rule review is incomplete; update this node and review again');
    }
    return value;
  }

  async decidePrimaRecovery(review: PrimaListenerReview, decision: 'keep_current' | 'restore_owned', signal?: AbortSignal) {
    const result = await this.http.networkRequest<{phase: string; decision: string}>(
      `/v2/node/network-setup/recovery/${encodeURIComponent(review.network_id)}/prima/${encodeURIComponent(review.run_id)}`,
      'POST', {decision, snapshot_digest: review.snapshot_digest}, signal);
    if (result?.phase !== 'released' || result?.decision !== decision) {
      throw new Error('Prima rule recovery was not acknowledged; refresh this computer before retrying');
    }
    return result;
  }

  clearAdministrator() {
    return this.http.networkRequest('/v2/node/network-setup/administrator', 'DELETE');
  }

  setupOperation<T>(operation: string, signal?: AbortSignal) {
    return this.http.networkRequest<T>(`/v2/cluster/network-setup/operations/${operation}`, 'GET', undefined, signal);
  }

  startSetup<T>(body: unknown) {
    return this.http.networkRequest<T>('/v2/cluster/network-setup', 'POST', body);
  }

  networkLifecycle<T>(networkId: string, body: unknown) {
    return this.http.networkRequest<T>(`/v2/cluster/networks/${networkId}/operations`, 'POST', body);
  }

  cancelOperation(operation: string, expected_operation_revision: number) {
    return this.http.networkRequest(`/v2/cluster/network-operations/${operation}/cancel`, 'POST', {
      request_id: crypto.randomUUID(),
      expected_operation_revision
    });
  }

  identity<T>(signal?: AbortSignal) {
    return this.http.networkRequest<T>('/v2/node/network-identity', 'GET', undefined, signal);
  }

  adapters<T>(signal?: AbortSignal) {
    return this.http.networkRequest<T>('/v2/node/network-adapters', 'GET', undefined, signal);
  }

  networks<T>(signal?: AbortSignal) {
    return this.http.networkRequest<T>('/v2/cluster/networks', 'GET', undefined, signal);
  }

  cleanupJournals<T>(signal?: AbortSignal) {
    return this.http.networkRequest<T>('/v2/node/network-cleanup', 'GET', undefined, signal);
  }

  clusterAdapters<T>(signal?: AbortSignal) {
    return this.http.networkRequest<T>('/v2/cluster/network-adapters', 'GET', undefined, signal);
  }

  putPeer(nodeId: string, body: unknown) {
    return this.http.networkRequest(`/v2/node/network-identity/peers/${encodeURIComponent(nodeId)}`, 'PUT', body);
  }

  selectAdapter(body: unknown) {
    return this.http.networkRequest('/v2/node/network-selection', 'PUT', body);
  }

  operation<T>(operationId: string, signal?: AbortSignal) {
    return this.http.networkRequest<T>(`/v2/cluster/network-operations/${operationId}`, 'GET', undefined, signal);
  }

  createNetwork<T>(body: unknown) {
    return this.http.networkRequest<T>('/v2/cluster/networks', 'POST', body);
  }

  cleanupReview<T>(journalId: string) {
    return this.http.networkRequest<T>(`/v2/node/network-cleanup/${journalId}`);
  }

  resolveCleanup(journalId: string, decision: string, snapshot_digest: string) {
    return this.http.networkRequest(`/v2/node/network-cleanup/${journalId}/resolve`, 'POST', { decision, snapshot_digest });
  }
}
