import type { ClusterView, Status } from './entities';
import type { UsageLive, UsageNode } from '../usage/entities';

export function clusterViewState(statusSeen: boolean, status: Status | null, connected: boolean): ClusterView {
  if (!statusSeen) return 'loading';
  if (status?.role === 'idle') return 'idle';
  if (!connected) return 'offline';
  const role = status?.role;
  if (role === 'worker' && status?.netsec_warnings?.includes('master heartbeat unreachable')) return 'offline';
  if (role === 'master' || role === 'worker') return 'live';
  return 'idle';
}

/** Membership rows for the topology, with usage overlaid when the cluster is visible. */
export function topologyNodes(status: Status | null, liveUsage: UsageLive | null, connected: boolean, statusSeen: boolean): Record<string, UsageNode> {
  const view = clusterViewState(statusSeen, status, connected);
  const usage = liveUsage?.nodes ?? {};
  if (view !== 'live' && view !== 'offline') return {};
  const members = status?.members ?? {};
  const networkDirectory = Object.fromEntries((status?.network_directory ?? []).map((entry) => [entry.node_id, entry]));
  const current = Object.fromEntries(Object.entries(members).map(([id, member]) => {
    const existing = usage[id];
    const identity = networkDirectory[id];
    const rpc_status = member.role === 'master' ? status?.rpc_lifecycle?.status : member.rpc_status;
    const identityFields = {
      host: identity?.hostname || existing?.host || member.host || id,
      address: identity?.private_address || identity?.rpc_bind || existing?.address || member.host || null,
    };
    if (existing) return [id, { ...existing, ...identityFields, rpc_status }];
    return [id, {
      node_id: id,
      ...identityFields,
      role: member.role === 'worker' ? 'worker' as const : 'master' as const,
      rpc_status,
      backend: member.actual_backend ?? member.backend ?? null,
      devices: member.devices,
      unified_memory: !!member.unified_memory,
      total_ram_bytes: member.total_ram_bytes ?? null,
      vram_bytes: member.vram_bytes ?? null,
      connected: true,
      freshness: 'unavailable' as const,
      reading: null,
    }];
  }));
  if (view === 'offline') {
    return Object.fromEntries(Object.entries({ ...usage, ...current }).map(([id, node]) =>
      [id, { ...node, connected: false, freshness: node.reading ? 'stale' as const : 'unavailable' as const }]
    ));
  }
  return current;
}
