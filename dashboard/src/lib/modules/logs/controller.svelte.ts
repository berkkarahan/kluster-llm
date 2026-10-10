import type { HttpClient } from '../../shared/http.svelte';
import type { ClusterInteractor } from '../cluster/interactor.svelte';
import type { LoggingStatus } from './entities';

export class LogsController {
  constructor(readonly http: HttpClient, readonly cluster: ClusterInteractor) {}

  async setLogging(enabled: boolean): Promise<LoggingStatus> {
    if (!this.http.apiToken) await this.http.loadToken();
    const response = await fetch('/v2/cluster/logging', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${this.http.apiToken}` },
      body: JSON.stringify({ enabled })
    });
    if (!response.ok) throw new Error(`Logging control failed (${response.status})`);
    const result = (await response.json()) as LoggingStatus;
    if (this.cluster.status) this.cluster.status = { ...this.cluster.status, logging: result };
    return result;
  }
}
