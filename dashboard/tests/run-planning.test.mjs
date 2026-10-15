import assert from 'node:assert/strict';
import test from 'node:test';
import { spawn } from 'node:child_process';
import { existsSync, mkdtempSync, readFileSync } from 'node:fs';
import { createServer as createHttpServer } from 'node:http';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { createServer } from 'vite';
import { removeBrowserProfile } from './browser-profile.mjs';

const chrome = [process.env.CHROME_BIN, '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
  '/usr/bin/google-chrome', '/usr/bin/chromium', 'C:/Program Files/Google/Chrome/Application/chrome.exe',
  'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe'].find((path) => path && existsSync(path));

test('run page cache dropdowns and required-node checkbox update the visible plan',
  { skip: !chrome && 'Install Chrome/Edge or set CHROME_BIN' }, async () => {
    const cwd = process.cwd();
    process.chdir(fileURLToPath(new URL('..', import.meta.url)));
    const profile = mkdtempSync(join(tmpdir(), 'kllm-run-planning-'));
    const requests = [];
    const gib = 1024 ** 3;
    let browser, vite, server, ws;
    try {
      vite = await createServer({ server: { middlewareMode: true, hmr: false, ws: false, watch: null }, appType: 'custom' });
      server = createHttpServer((req, res) => {
        void (async () => {
          if (!req.url.startsWith('/v2/')) {
            vite.middlewares(req, res, () => res.writeHead(404).end());
            return;
          }
          let result = {};
          if (req.url.startsWith('/v2/inference/plans')) {
            const chunks = [];
            for await (const chunk of req) chunks.push(chunk);
            const body = JSON.parse(Buffer.concat(chunks).toString());
            requests.push(body);
            const quantized = body.cache_type_k === 'q4_0' && body.cache_type_v === 'q4_0';
            result = { estimated_bytes: (quantized ? 35 : 38) * gib, budget_bytes: 48 * gib,
              fit_status: 'fits', kv_estimate_bytes: (quantized ? 3 : 6) * gib,
              weights_estimate_bytes: 27 * gib, checkpoint_estimate_bytes: 4 * gib,
              cpu_alloc_bytes: 5 * gib, ram_alloc_bytes: 5 * gib, vram_alloc_bytes: 30 * gib,
              node_ram_alloc_bytes: { master: 5 * gib, worker: 0 },
              node_vram_alloc_bytes: { master: 10 * gib, worker: 20 * gib },
              device_allocations: [{ node_id: 'worker', layers: 20, output_layer: true }],
              cpu_layers: 0, estimated_layers: 32,
              resolved_launch_settings: { context: body.context, batch_size: 512, micro_batch: 128,
                parallel_slots: 1, cache_type_k: body.cache_type_k, cache_type_v: body.cache_type_v } };
          }
          res.writeHead(200, { 'Content-Type': 'application/json' }).end(JSON.stringify(result));
        })().catch((error) => { res.writeHead(500).end(String(error)); });
      });
      await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
      const base = `http://127.0.0.1:${server.address().port}`;
      browser = spawn(chrome, ['--headless=new', '--disable-gpu', '--no-first-run', '--remote-allow-origins=*',
        '--remote-debugging-port=0', `--user-data-dir=${profile}`, 'about:blank'], { stdio: 'ignore' });
      const portfile = join(profile, 'DevToolsActivePort');
      for (let i = 0; i < 100 && !existsSync(portfile); i++) await new Promise((resolve) => setTimeout(resolve, 100));
      assert.ok(existsSync(portfile), 'Chrome DevTools did not start');
      const port = Number(readFileSync(portfile, 'utf8').split('\n')[0]);
      const pages = await (await fetch(`http://127.0.0.1:${port}/json/list`)).json();
      ws = new WebSocket(pages.find((page) => page.type === 'page').webSocketDebuggerUrl);
      await new Promise((resolve, reject) => { ws.onopen = resolve; ws.onerror = reject; });
      let id = 0;
      const pending = new Map();
      ws.onmessage = ({ data }) => {
        const message = JSON.parse(data);
        pending.get(message.id)?.(message);
      };
      async function call(method, params = {}) {
        const requestId = ++id;
        const answer = new Promise((resolve, reject) => {
          const timer = setTimeout(() => { pending.delete(requestId); reject(new Error(`CDP timeout: ${method}`)); }, 10000);
          pending.set(requestId, (message) => { clearTimeout(timer); pending.delete(requestId); resolve(message); });
        });
        ws.send(JSON.stringify({ id: requestId, method, params }));
        const response = await answer;
        assert.equal(response.error, undefined);
        return response.result;
      }
      async function evaluate(expression) {
        const response = await call('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true });
        assert.equal(response.exceptionDetails, undefined, JSON.stringify(response.exceptionDetails));
        return response.result.value;
      }
      async function wait(expression) {
        for (let i = 0; i < 200; i++) {
          if (await evaluate(`Boolean(${expression})`)) return;
          await new Promise((resolve) => setTimeout(resolve, 100));
        }
        assert.fail(`Browser timed out: ${expression}\n${await evaluate('document.body.innerText')}`);
      }
      await call('Page.navigate', { url: base + '/run' });
      await wait("document.querySelector('#run-placement-mode')");
      await evaluate(`(async () => {
        const { createApp } = await import('/src/lib/app.svelte.ts');
        const appStore = createApp();
        appStore.stop();
        appStore.cluster.status = { role: 'master', node_id: 'master', members: {
          master: { node_id: 'master', role: 'master', host: 'master', devices: [{ index: 0, name: 'Apple M4' }] },
          worker: { node_id: 'worker', role: 'worker', host: 'worker', devices: [{ index: 0, name: 'AMD Radeon 8060S' }] }
        }};
        appStore.models.models = [{ id: 'fixture.gguf' }];
        appStore.models.selectedModel = 'fixture.gguf';
        appStore.run.resetRunSettings();
        appStore.run.advancedOpen = true;
        appStore.run.scheduleEstimate(0);
      })()`);
      await wait("document.body.innerText.includes('K cache: q8_0') && document.body.innerText.includes('6.0 GiB')");
      await evaluate("[...document.querySelectorAll('button')].find(button => button.textContent.trim() === 'Configure').click()");
      await wait("document.querySelector('#run-cache-k')");
      await evaluate("[...document.querySelectorAll('summary')].find(summary => summary.textContent.includes('Memory & loading')).click()");
      await evaluate(`(() => {
        for (const select of [document.querySelector('#run-cache-k'), document.querySelector('#run-cache-v')]) {
          select.value = 'q4_0'; select.dispatchEvent(new Event('change', { bubbles: true }));
        }
      })()`);
      await wait("document.body.innerText.includes('K cache: q4_0') && document.body.innerText.includes('KV cache decreased by 3.0 GiB')");
      assert.equal(requests.at(-1).cache_type_k, 'q4_0');
      assert.equal(requests.at(-1).cache_type_v, 'q4_0');
      await evaluate("[...document.querySelectorAll('button')].find(button => button.textContent.trim() === 'Back').click()");
      await evaluate("document.querySelector('#required-worker').click()");
      await wait("document.querySelector('#required-worker').checked && document.body.innerText.includes('Preview recalculated.')");
      assert.deepEqual(requests.at(-1).required_nodes, ['worker']);
      const text = await evaluate('document.body.innerText');
      assert.match(text, /Apple M4/);
      assert.match(text, /AMD Radeon 8060S/);
      assert.match(text, /CPU RAM: 5\.0 GiB/);
      assert.match(text, /19 model layers/);
      await evaluate(`(() => {
        const select = document.querySelector('#run-placement-mode');
        select.value = 'priority'; select.dispatchEvent(new Event('change', { bubbles: true }));
      })()`);
      await wait("document.querySelector('[aria-label=\"Move worker up\"]')");
      await evaluate("document.querySelector('[aria-label=\"Move worker up\"]').click()");
      await wait(`document.querySelector('[aria-label="Move worker up"]').disabled && document.body.innerText.includes('Preview recalculated.')`);
      assert.deepEqual(requests.at(-1).node_priority, ['worker', 'master']);
      assert.equal(requests.at(-1).node_shares, null);
      assert.deepEqual(requests.at(-1).required_nodes, ['worker']);
      assert.equal(await evaluate("Boolean(document.querySelector('[id^=share-]'))"), false);

      // Switch to Prima mode and verify f16 KV + flash_attention='off' defaults and progress bars.
      await evaluate(`(async () => {
        const { createApp } = await import('/src/lib/app.svelte.ts');
        const appStore = createApp();
        const primaVersion = {
          runtime_id: 'a'.repeat(64),
          name: 'Prima.cpp 0.0.1',
          engine: 'primacpp',
          repository: 'https://github.com/berkkarahan/prima.cpp.git',
          ref: '0.0.1',
          ref_type: 'tag',
          commit: '1'.repeat(40)
        };
        appStore.cluster.status = {
          ...appStore.cluster.status,
          engine: 'primacpp',
          runtimes: {
            versions: [{ version: primaVersion, build: { runtime_id: primaVersion.runtime_id, build_id: 'b1', backend: 'vulkan', commit: primaVersion.commit } }],
            jobs: [],
            selected: primaVersion.runtime_id,
            target: { version: primaVersion, revision: 1, cluster_binding_id: 'c1', master_node_id: 'master' },
            running: null,
            prima_preparation: {
              job_id: 'prep-1',
              phase: 'profiling',
              progress: {
                fraction: 0.65,
                step: 2,
                steps_total: 3,
                label: 'Profiling devices (1/1): master',
                detail: 'Pass 2/2 (VULKAN): running benchmark request…',
                bytes_done: 536870912,
                bytes_total: 1073741824,
                rate_bps: 268435456,
                eta_seconds: 2
              },
              nodes: {
                master: {
                  phase: 'profiling',
                  progress: {
                    fraction: 0.65,
                    step: 2,
                    steps_total: 3,
                    label: 'Profiling Vulkan0',
                    detail: 'Pass 2/2 (VULKAN)'
                  }
                }
              }
            }
          }
        };
        appStore.run.resetRunSettings();
      })()`);
      await wait("document.querySelector('.prima-deploy-page') && document.body.innerText.includes('K cache: f16')");
      assert.equal(requests.at(-1).cache_type_k, 'f16');
      assert.equal(requests.at(-1).cache_type_v, 'f16');
      assert.equal(requests.at(-1).flash_attention, 'off');

      // Navigate to Step 3 (Review & deploy) and check both progress bars
      await evaluate("[...document.querySelectorAll('.steps .step')].at(-1).click()");
      await wait("document.querySelector('[aria-label=\"Prima preparation and profiling progress\"]')");
      const prepVal = await evaluate("document.querySelector('[aria-label=\"Prima preparation and profiling progress\"]').getAttribute('aria-valuenow')");
      assert.equal(prepVal, '65');
      const step3Text = await evaluate('document.body.innerText');
      assert.match(step3Text, /Step 2 of 3/);
      assert.match(step3Text, /Pass 2\/2 \(VULKAN\)/);

      // Verify deployment progress bar appears immediately above .form-foot when busy or loading
      await evaluate(`(async () => {
        const { createApp } = await import('/src/lib/app.svelte.ts');
        const appStore = createApp();
        appStore.run.busy = '/v2/inference/runs';
        appStore.run.loadProgress = {
          phase: 'starting',
          model: 'fixture.gguf',
          bytes_loaded: 536870912,
          bytes_total: 1073741824,
          fraction: 0.42,
          rate_bps: 134217728,
          eta_seconds: 4,
          detail: 'Starting Ring-TCP rank 1/2 (master)…'
        };
      })()`);
      await wait("document.querySelector('.deploy-progress-slot [aria-label=\"Model load\"]')");
      const loadVal = await evaluate("document.querySelector('.deploy-progress-slot [aria-label=\"Model load\"]').getAttribute('aria-valuenow')");
      assert.equal(loadVal, '42');
      assert.match(await evaluate('document.body.innerText'), /Starting Ring-TCP rank 1\/2 \(master\)…/);
    } finally {
      ws?.close();
      if (browser && browser.exitCode === null) {
        const exited = new Promise((resolve) => browser.once('exit', resolve));
        browser.kill(); await exited;
      }
      await vite?.watcher?.close(); await vite?.close();
      if (server) await new Promise((resolve) => server.close(resolve));
      await removeBrowserProfile(profile);
      process.chdir(cwd);
    }
  });

