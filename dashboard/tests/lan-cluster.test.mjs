import assert from 'node:assert/strict';
import test from 'node:test';
import { fileURLToPath } from 'node:url';
import { createServer } from 'vite';

test('cluster start and join send LAN-only requests without legacy overlay fields', async () => {
  const cwd = process.cwd();
  let server;
  try {
    process.chdir(fileURLToPath(new URL('..', import.meta.url)));
    server = await createServer({ server: { middlewareMode: true, hmr: false, ws: false, watch: null }, appType: 'custom' });
    const { ClusterInteractor } = await server.ssrLoadModule('/src/lib/modules/cluster/interactor.svelte.ts');
    const requests = [];
    const cluster = new ClusterInteractor({
      command: async (path, body) => { requests.push({ path, body }); }
    }, {});
    cluster.joinKeyInput = 'secret';
    cluster.masterHostInput = '192.168.1.10';
    cluster.rpcBindInput = '192.168.1.11';
    await cluster.startMaster();
    await cluster.joinCluster();
    assert.deepEqual(requests.map(({ path }) => path), ['/v2/cluster', '/v2/cluster/membership']);
    for (const { body } of requests) {
      assert.equal(body.rpc_bind, '192.168.1.11');
      assert.equal('overlay' in body, false);
    }
    assert.equal(requests[1].body.master_host, '192.168.1.10');
  } finally {
    await server?.close();
    process.chdir(cwd);
  }
});
