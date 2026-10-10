export type SoftwareIdentity = {
  git_head?: string | null;
  llama_release?: string | null;
};

export type HomePaths = {
  home: string;
  models: string;
  logs: string;
};
