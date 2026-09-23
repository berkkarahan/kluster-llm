import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { appStore } from './store.svelte';

describe('MPI dashboard run settings', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    appStore.stop();
    appStore.transport = 'rpc';
    appStore.mpiDeviceIds = null;
    appStore.models = [{ id: 'toy.gguf' }];
    appStore.selectedModel = 'toy.gguf';
    appStore.status = { role: 'master', node_id: 'master', members: {
      master: { node_id: 'master', host: '127.0.0.1', role: 'master' }
    } };
    appStore.planResult = null;
    appStore.busy = '';
  });

  afterEach(() => {
    appStore.stop();
    vi.restoreAllMocks();
    vi.useRealTimers();
  });

  it('persists mode and physical IDs in the plan payload and clears stale estimates', () => {
    appStore.planResult = { transport: 'rpc', fit_status: 'fits' };
    appStore.setTransport('mpi');
    expect(appStore.planResult).toBeNull();
    appStore.setMpiDevice('master', 'gpu-a', false, ['gpu-a', 'gpu-b']);
    expect(appStore.planPayload()).toMatchObject({
      transport: 'mpi', mpi_device_ids: { master: ['gpu-b'] }
    });
    appStore.setMpiDevice('master', 'gpu-a', true, ['gpu-a', 'gpu-b']);
    expect(appStore.planPayload().mpi_device_ids).toBeNull();
  });

  it('fetches a fresh plan and submits its fingerprint before MPI Run', async () => {
    appStore.transport = 'mpi';
    const post = vi.spyOn(appStore, 'post').mockImplementation(async (path) =>
      path === '/v1/plan'
        ? { transport: 'mpi', launchable: true, plan_fingerprint: 'sha256:fresh' }
        : { plan: { transport: 'mpi' } }
    );
    await appStore.run();
    expect(post).toHaveBeenCalledTimes(2);
    expect(post.mock.calls[0][0]).toBe('/v1/plan');
    expect(post.mock.calls[1][0]).toBe('/v1/run');
    expect(post.mock.calls[1][1]).toMatchObject({ plan_fingerprint: 'sha256:fresh' });
  });

  it('submits the fresh fingerprint when admission is blocked', async () => {
    appStore.transport = 'mpi';
    const post = vi.spyOn(appStore, 'post').mockImplementation(async (path) =>
      path === '/v1/plan'
        ? { transport: 'mpi', launchable: false, blockers: ['engine unavailable'], plan_fingerprint: 'sha256:blocked' }
        : { detail: { blockers: ['engine unavailable'] } }
    );
    await appStore.run();
    expect(post).toHaveBeenCalledTimes(2);
    expect(post.mock.calls[0][0]).toBe('/v1/plan');
    expect(post.mock.calls[1][0]).toBe('/v1/run');
    expect(post.mock.calls[1][1]).toMatchObject({ plan_fingerprint: 'sha256:blocked' });
  });

  it('does not let an older estimate replace a newer MPI decision', async () => {
    let resolveFirst: ((value: Response) => void) | undefined;
    vi.spyOn(globalThis, 'fetch')
      .mockImplementationOnce(() => new Promise<Response>((resolve) => { resolveFirst = resolve; }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ marker: 'new', effective_backend: 'vulkan' }), { status: 200 }));

    const first = appStore.refreshEstimate();
    appStore.transport = 'mpi';
    const second = appStore.refreshEstimate();
    await second;
    resolveFirst?.(new Response(JSON.stringify({ marker: 'old', effective_backend: 'cuda' }), { status: 200 }));
    await first;

    expect(appStore.planResult).toMatchObject({ marker: 'new', effective_backend: 'vulkan' });
  });

  it('ignores stale model settings and defaults an unsaved model to RPC', async () => {
    appStore.models = [{ id: 'old.gguf' }, { id: 'new.gguf' }];
    appStore.transport = 'mpi';
    appStore.mpiDeviceIds = { master: ['gpu-a'] };
    let resolveOld: ((value: Response) => void) | undefined;
    vi.spyOn(globalThis, 'fetch')
      .mockImplementationOnce(() => new Promise<Response>((resolve) => { resolveOld = resolve; }))
      .mockResolvedValueOnce(new Response('', { status: 404 }));

    const oldSelection = appStore.selectModel('old.gguf');
    await appStore.selectModel('new.gguf');
    resolveOld?.(new Response(JSON.stringify({
      parameter_set: { transport: 'mpi', mpi_device_ids: { master: ['gpu-a'] } }
    }), { status: 200 }));
    await oldSelection;

    expect(appStore.selectedModel).toBe('new.gguf');
    expect(appStore.transport).toBe('rpc');
    expect(appStore.mpiDeviceIds).toBeNull();
  });
});
