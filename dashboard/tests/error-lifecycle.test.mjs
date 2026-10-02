import assert from 'node:assert/strict';
import test from 'node:test';
import { get } from 'svelte/store';
import { createSsrServer } from './ssr-server.mjs';

test('store acknowledges historical errors, clears lifecycle messages and rejects stale request failures', async () => {
  const server = await createSsrServer();
  const originalFetch = globalThis.fetch;
  let store;
  try {
    ({ appStore: store } = await server.ssrLoadModule('/src/lib/store.svelte.ts'));
    const { errors } = await server.ssrLoadModule('/src/lib/errors.ts');
    const { toasts } = await server.ssrLoadModule('/src/lib/toast.ts');
    const visible = () => Object.values(get(errors).entries).filter(entry => entry.visible);
    errors.start();
    const status = { role: 'master', node_id: 'master', members: {}, inference_up: true,
      process_pid: 99, model_name: 'fixture.gguf', apply_error: 'old apply failure' };
    store.applyStatus(status);
    store.applyLiveArea('load', { availability: 'available', data: {
      current: null, last_terminal: { operation_id: 'old-load', phase: 'error', error: 'old load failure' }
    } });
    assert.equal(visible().length, 0);
    assert.equal(get(toasts).filter(toast => toast.kind === 'danger').length, 0);
    store.applyStatus({ ...status, apply_error: 'new apply failure' });
    assert.ok(visible().some(entry => entry.message === 'new apply failure'));
    errors.dismiss('apply');
    store.applyStatus({ ...status, apply_error: 'new apply failure' });
    assert.equal(visible().length, 0);
    store.applyStatus({ ...status, inference_activity: { phase: 'error', detail: 'slot failed' } });
    errors.dismiss('inference-activity');
    store.applyStatus({ ...status, inference_activity: { phase: 'ready' } });
    store.applyStatus({ ...status, inference_activity: { phase: 'error', detail: 'slot failed' } });
    assert.ok(visible().some(entry => entry.message === 'slot failed'), 'recovery re-arms the same diagnostic');
    store.clearErrors();

    store.context = 0;
    assert.equal(store.validateRunSettings(), false);
    errors.dismiss('validation:context');
    assert.ok(store.fieldErrors.context, 'dismissal retains authoritative validation');
    assert.equal(store.validateRunSettings(), false);
    store.context = 4096;

    for (const action of ['stopInference', 'stopCluster', 'leaveCluster']) {
      errors.publish('unrelated', 'prior unrelated failure');
      let requested = false;
      globalThis.fetch = async () => {
        requested = true;
        assert.equal(visible().length, 0, `${action} clears before dispatch`);
        return new Response(JSON.stringify({ detail: `${action} refused` }), { status: 409 });
      };
      await assert.rejects(store[action]());
      assert.ok(requested);
      assert.ok(visible().some(entry => entry.message.includes(`${action} refused`)));
    }

    let release;
    globalThis.fetch = () => new Promise(resolve => { release = resolve; });
    store.modelSource = 'fixture';
    const search = store.searchModels();
    store.clearErrors();
    release(new Response(JSON.stringify({ detail: 'late search failure' }), { status: 503 }));
    await assert.rejects(search);
    assert.equal(visible().length, 0);

    store.connected = true;
    const oldRefresh = store.refreshDirect();
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
      store.applyStatus({ ...status, apply_error: null });
      errors.publish('old', 'previous request failure');
      if (ended.role === 'worker') {
        store.applyStatus({ ...status, role: 'worker', members: { master: { node_id: 'master' } } });
      }
      store.applyStatus({ ...ended, apply_error: null });
      assert.equal(visible().length, 0, 'external lifecycle transition clears all messages');
    }
  } finally {
    globalThis.fetch = originalFetch;
    store?.stop();
    await server.close();
  }
});
