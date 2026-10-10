import assert from 'node:assert/strict';
import test from 'node:test';
import { get } from 'svelte/store';
import { createSsrServer } from './ssr-server.mjs';

test('store acknowledges historical errors, clears lifecycle messages and rejects stale request failures', async () => {
  const server = await createSsrServer();
  const originalFetch = globalThis.fetch;
  let store;
  try {
    store = (await server.ssrLoadModule('/src/lib/app.svelte.ts')).createApp();
    const { errors } = await server.ssrLoadModule('/src/lib/shared/errors.ts');
    const { toasts } = await server.ssrLoadModule('/src/lib/shared/toast.ts');
    const visible = () => Object.values(get(errors).entries).filter(entry => entry.visible);
    errors.start();
    const status = { role: 'master', node_id: 'master', members: {}, inference_up: true,
      process_pid: 99, model_name: 'fixture.gguf', apply_error: 'old apply failure' };
    store.cluster.applyStatus(status);
    store.live.applyLiveArea('load', { availability: 'available', data: {
      current: null, last_terminal: { operation_id: 'old-load', phase: 'error', error: 'old load failure' }
    } });
    assert.equal(visible().length, 0);
    assert.equal(get(toasts).filter(toast => toast.kind === 'danger').length, 0);
    store.cluster.applyStatus({ ...status, apply_error: 'new apply failure' });
    assert.ok(visible().some(entry => entry.message === 'new apply failure'));
    errors.dismiss('apply');
    store.cluster.applyStatus({ ...status, apply_error: 'new apply failure' });
    assert.equal(visible().length, 0);
    store.cluster.applyStatus({ ...status, inference_activity: { phase: 'error', detail: 'slot failed' } });
    errors.dismiss('inference-activity');
    store.cluster.applyStatus({ ...status, inference_activity: { phase: 'ready' } });
    store.cluster.applyStatus({ ...status, inference_activity: { phase: 'error', detail: 'slot failed' } });
    assert.ok(visible().some(entry => entry.message === 'slot failed'), 'recovery re-arms the same diagnostic');
    store.clearErrors();

    store.run.context = 0;
    assert.equal(store.run.validateRunSettings(), false);
    errors.dismiss('validation:context');
    assert.ok(store.run.fieldErrors.context, 'dismissal retains authoritative validation');
    assert.equal(store.run.validateRunSettings(), false);
    store.run.context = 4096;

    for (const action of ['stopInference', 'stopCluster', 'leaveCluster']) {
      errors.publish('unrelated', 'prior unrelated failure');
      let requested = false;
      globalThis.fetch = async () => {
        requested = true;
        assert.equal(visible().length, 0, `${action} clears before dispatch`);
        return new Response(JSON.stringify({ detail: `${action} refused` }), { status: 409 });
      };
      const call = {
        stopInference: () => store.run.stopInference(),
        stopCluster: () => store.cluster.stopCluster(),
        leaveCluster: () => store.cluster.leaveCluster()
      }[action];
      await assert.rejects(call());
      assert.ok(requested);
      assert.ok(visible().some(entry => entry.message.includes(`${action} refused`)));
    }

    let release;
    globalThis.fetch = () => new Promise(resolve => { release = resolve; });
    store.models.modelSource = 'fixture';
    const search = store.models.searchModels();
    store.clearErrors();
    release(new Response(JSON.stringify({ detail: 'late search failure' }), { status: 503 }));
    await assert.rejects(search);
    assert.equal(visible().length, 0);

    store.live.connected = true;
    const oldRefresh = store.cluster.refreshDirect();
    store.clearErrors();
    const currentGeneration = errors.generation;
    errors.publish('stop-result', 'Fresh lifecycle failure');
    release(new Response('', { status: 503 }));
    await oldRefresh;
    assert.equal(errors.generation, currentGeneration, 'a stale offline response must not clear again');
    assert.ok(visible().some(entry => entry.message === 'Fresh lifecycle failure'));
    store.clearErrors();

    for (const ended of [
      { ...status, inference_up: false, model_name: null, process_pid: null },
      { ...status, role: 'idle', members: {}, inference_up: false, model_name: null },
      { ...status, role: 'worker', members: {} }
    ]) {
      store.cluster.applyStatus({ ...status, apply_error: null });
      errors.publish('old', 'previous request failure');
      if (ended.role === 'worker') {
        store.cluster.applyStatus({ ...status, role: 'worker', members: { master: { node_id: 'master' } } });
      }
      store.cluster.applyStatus({ ...ended, apply_error: null });
      assert.equal(visible().length, 0, 'external lifecycle transition clears all messages');
    }
  } finally {
    globalThis.fetch = originalFetch;
    store?.stop();
    await server.close();
  }
});
