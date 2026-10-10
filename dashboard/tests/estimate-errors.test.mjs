import assert from 'node:assert/strict';
import test from 'node:test';
import { fileURLToPath } from 'node:url';
import { createServer } from 'vite';

test('estimate failures surface the structured v2 error and runtime readiness guidance', async () => {
  const cwd = process.cwd();
  const originalFetch = globalThis.fetch;
  let store;
  let server;
  let nextResponse;
  const json = (body, status) => new Response(JSON.stringify(body), { status });
  try {
    process.chdir(fileURLToPath(new URL('..', import.meta.url)));
    server = await createServer({ server: { middlewareMode: true, hmr: false, ws: false, watch: null }, appType: 'custom' });
    globalThis.fetch = async (path) => {
      assert.equal(path, '/v2/inference/plans?preview=true');
      return nextResponse;
    };
    store = (await server.ssrLoadModule('/src/lib/app.svelte.ts')).createApp();
    store.models.models = [{ id: 'fixture.gguf' }];
    store.models.selectedModel = 'fixture.gguf';
    store.run.threads = 1;
    store.cluster.status = {
      role: 'master', node_id: 'master', members: { master: { node_id: 'master' } },
      runtimes: {
        target: { version: { runtime_id: 'runtime-1' } }, selected: null,
        nodes: [{ node_id: 'master', phase: 'failed', prepared: null }]
      }
    };

    // v2 envelope: the generic message must not hide details.message.
    nextResponse = json({
      error: {
        code: 'conflict', message: 'The request could not be completed.',
        details: { code: 'RUNTIME_NOT_READY', message: 'Runtime is not prepared; open Settings → llama.cpp and download/build the target' }
      }
    }, 409);
    await store.run.refreshEstimate();
    assert.equal(store.run.planResult, null);
    assert.equal(store.run.estimateError, 'Runtime is not prepared; open Settings → llama.cpp and download/build the target');
    assert.equal(store.run.estimating, false);

    const { render } = await server.ssrLoadModule('svelte/server');
    const { default: RunPage } = await server.ssrLoadModule('/src/routes/run/+page.svelte');
    const html = render(RunPage).body;
    assert.match(html, /Prepare the target llama.cpp version on master/);
    assert.match(html, /Open llama.cpp settings/);

    // Preflight field maps keep their actionable text too.
    nextResponse = json({
      error: {
        code: 'validation_error', message: 'The request could not be completed.',
        details: { context: 'Model context limit is 131072' }
      }
    }, 422);
    await store.run.refreshEstimate();
    assert.equal(store.run.estimateError, 'Model context limit is 131072');
  } finally {
    store?.stop();
    globalThis.fetch = originalFetch;
    await server?.watcher?.close();
    await server?.close();
    process.chdir(cwd);
  }
});
