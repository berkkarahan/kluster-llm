import type { HttpClient } from '../../shared/http.svelte';
import type { ClusterInteractor } from '../cluster/interactor.svelte';

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
