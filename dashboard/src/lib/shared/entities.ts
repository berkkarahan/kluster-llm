export type AccelBackend = 'metal' | 'cuda' | 'rocm' | 'vulkan';
export type SplitMode = 'layer' | 'row' | 'tensor';
export type RpcStatus = 'idle' | 'starting' | 'serving' | 'recovering' | 'failed' | 'cleanup_pending';

export type GpuInfo = {
  index: number;
  name: string;
  vram_bytes?: number | null;
  device?: string;
  backend?: string;
  free_bytes?: number;
  /** Shares host RAM (iGPU / APU graphics); not counted as discrete VRAM. */
  integrated?: boolean;
};
