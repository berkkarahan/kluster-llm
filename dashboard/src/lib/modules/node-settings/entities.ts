export type SoftwareIdentity = {
  git_head?: string | null;
  llama_release?: string | null;
};

export type HomePaths = {
  home: string;
  models: string;
  logs: string;
};

export type FeatureFlagDescriptor = {
  id: string;
  label: string;
  description: string;
};

export const FEATURE_FLAG_CATALOG: readonly FeatureFlagDescriptor[] = [
  {
    id: 'private-network-setup',
    label: 'Guided private network setup',
    description:
      'Enables the guided private Ethernet (10GbE / fast LAN) provisioning and verification workflow in the Network workspace.'
  },
  {
    id: 'prima-auto-deploy',
    label: 'Prima one-click auto-deploy',
    description:
      'Delegates placement and resource planning to Prima so selecting a model and clicking Deploy runs with optimal cluster settings automatically.'
  }
];
