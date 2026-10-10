export type NetworkAdapter = {
  adapter_id: string;
  os_interface: string;
  inventory_revision: string;
  connected: boolean;
  link_speed_mbps: number | null;
  dhcp: boolean | null;
  permission: boolean;
  physical_ethernet: boolean;
  addresses: string[];
};
export type NetworkIdentity = {
  node_id: string;
  key_id: string;
  public_key: string;
  cluster_binding_id: string | null;
  permission: boolean;
  paired_peers: Array<{node_id: string; key_id: string; expected_role: string}>;
};
export type NetworkInventory = {
  node_id: string;
  adapters: NetworkAdapter[];
  selection?: string | null;
  error_code?: string;
};

export type ManagedNetwork = {
  network_id: string;
  subnet: string;
  revision: number;
  desired_state: string;
  observed_state: string;
  current_operation_id: string | null;
  participants: Array<{
    node_id: string; adapter_id: string; address: string; prefix_length: number;
    state: string; ownership: string; link_speed_mbps: number | null;
    logical_rpc_endpoint: string | null; verification_freshness: string;
    error?: {code: string; message: string} | null;
  }>;
  blocking_runs: Array<{run_id: string; state: string}>;
};
