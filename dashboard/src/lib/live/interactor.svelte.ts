import { errors } from '../shared/errors';
import type { DeliveryHealth, LiveArea, LiveAreaHealth, LiveAreaName } from './entities';
import { DashboardUpdates, type LiveLogListener } from './updates';

export class LiveInteractor {
  updates: DashboardUpdates | null = null;
  deliveryHealth = $state<DeliveryHealth>('loading');
  deliveryError = $state('');
  connected = $state(false);
  areaHealth = $state<Partial<Record<LiveAreaName, LiveAreaHealth>>>({});

  private areaHandlers = new Map<LiveAreaName, (data: unknown) => void>();
  private fingerprintHandlers: Array<(status: unknown) => void> = [];
  onConnectionLost: () => void = () => {};
  onUnavailable: () => void = () => {};
  refreshDirect: () => Promise<void> = async () => {};

  onArea(topic: LiveAreaName, handler: (data: unknown) => void) {
    this.areaHandlers.set(topic, handler);
  }

  /** Status fingerprint changes. Registration order is the call order. */
  onStatusFingerprint(handler: (status: unknown) => void) {
    this.fingerprintHandlers.push(handler);
  }

  emitStatusFingerprint(status: unknown) {
    for (const handler of this.fingerprintHandlers) handler(status);
  }

  applyLiveArea(topic: LiveAreaName, area: LiveArea<unknown>) {
    if (area.availability !== 'available' || area.data === null) return;
    this.areaHandlers.get(topic)?.(area.data);
  }

  private applyLiveHealth(topic: LiveAreaName, area: LiveArea<unknown>) {
    errors.observe(`area:${topic}`, area.error?.message ?? (area.availability === 'denied' || area.availability === 'unavailable' ? 'source unavailable' : ''));
    const previous = this.areaHealth[topic];
    this.areaHealth[topic] = {
      availability: area.availability,
      coverage: area.coverage,
      last_success_at: area.availability === 'available' ? area.sampled_at : previous?.last_success_at ?? null,
      error: area.error
    };
  }

  private onHealth(health: DeliveryHealth, error?: string) {
    const lostConnection = this.connected && health === 'unavailable';
    errors.observe('delivery', error ?? '', '', false);
    if (lostConnection) this.onConnectionLost();
    this.deliveryHealth = health;
    this.deliveryError = error ?? '';
    if (health === 'unavailable') {
      this.connected = false;
      this.onUnavailable();
    }
  }

  start() {
    if (!this.updates) {
      this.updates = new DashboardUpdates({
        onArea: (topic, area) => this.applyLiveArea(topic, area),
        onAreaHealth: (topic, area) => this.applyLiveHealth(topic, area),
        onHealth: (health, error) => this.onHealth(health, error)
      });
    }
    this.updates.start();
  }

  stop() {
    this.updates?.stop();
  }

  setLogListener(listener: LiveLogListener | null): boolean {
    this.updates?.setLogListener(listener);
    return this.updates !== null;
  }

  setLogVisibilityListener(listener: (() => void) | null, expected?: () => void): boolean {
    this.updates?.setLogVisibilityListener(listener, expected);
    return this.updates !== null;
  }

  setUsageInterest(active: boolean) {
    this.updates?.setUsageInterest(active);
  }

  async refresh() {
    if (this.updates) {
      this.updates.refresh();
      return;
    }
    return this.refreshDirect();
  }
}
