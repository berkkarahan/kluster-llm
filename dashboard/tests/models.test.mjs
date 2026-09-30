import assert from 'node:assert/strict';
import test from 'node:test';
import { fileURLToPath } from 'node:url';
import { createServer as createHttpServer } from 'node:http';
import { spawn } from 'node:child_process';
import { existsSync, mkdtempSync, readFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { createServer } from 'vite';
import { removeBrowserProfile } from './browser-profile.mjs';

const chrome = [process.env.CHROME_BIN, 'C:/Program Files/Google/Chrome/Application/chrome.exe',
  'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',
  '/usr/bin/google-chrome', '/usr/bin/chromium',
  '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'].find((path) => path && existsSync(path));

const TREE = 'https://huggingface.co/demo/model-GGUF/tree/release%2Fv2/gguf';
const FILE = 'https://huggingface.co/demo/model-GGUF/resolve/release%2Fv2/other/model.Q4_K_M.gguf';
const SOURCE_A = 'https://huggingface.co/demo/model-GGUF/resolve/release%2Fv2/gguf/model.Q4_K_M.gguf';
const SOURCE_B = 'https://huggingface.co/demo/model-GGUF/resolve/release%2Fv2/other/model.Q4_K_M.gguf';

function row(path, source, size, fit) {
  return {
    repo_id: 'demo/model-GGUF',
    filename: path.split('/').at(-1),
    path,
    revision: 'release/v2',
    quant: 'Q4_K_M',
    size_bytes: size,
    gated: false,
    source,
    fit
  };
}

test('models search keeps the bar and opens downloadable results for keywords and URLs',
  { skip: !chrome && 'Install Chrome/Edge or set CHROME_BIN to run browser check' }, async () => {
    const cwd = process.cwd();
    process.chdir(fileURLToPath(new URL('..', import.meta.url)));
    const profile = mkdtempSync(join(tmpdir(), 'kllm-models-'));
    const searches = [];
    const downloads = [];
    let releaseTree = () => {};
    const treeGate = new Promise((resolve) => { releaseTree = resolve; });
    let browser;
    let vite;
    let server;
    let ws;
    try {
      const readBody = (req) => new Promise((resolve, reject) => {
        const chunks = [];
        req.on('data', (chunk) => chunks.push(chunk));
        req.on('end', () => resolve(Buffer.concat(chunks).toString('utf8')));
        req.on('error', reject);
      });
      const json = (res, payload, status = 200) =>
        res.writeHead(status, { 'Content-Type': 'application/json' }).end(JSON.stringify(payload));
      const live = (req) => {
        const requested = new URL(req.url, 'http://x').searchParams.get('topics')?.split(',') ??
          ['status', 'usage', 'load', 'download'];
        const areas = {
          status: { revision: 1, sampled_at: '2026-09-29T00:00:02Z', availability: 'available',
            coverage: 'local', upstream_last_success_at: null,
            data: { role: 'idle', node_id: 'local', members: {} }, error: null },
          usage: { revision: 1, sampled_at: '2026-09-29T00:00:02Z', availability: 'available',
            coverage: 'local', upstream_last_success_at: null,
            data: { nodes: {}, relationships: [], recording: { recording: 'idle' } }, error: null },
          load: { revision: 1, sampled_at: null, availability: 'available', coverage: 'local',
            upstream_last_success_at: null, data: { current: null, last_terminal: null }, error: null },
          download: { revision: 1, sampled_at: null, availability: 'available', coverage: 'local',
            upstream_last_success_at: null, data: { current: null, last_terminal: null }, error: null }
        };
        return {
          version: 1, transport: 'poll', epoch: '00000000-0000-4000-8000-000000000001',
          server_time: '2026-09-29T00:00:02Z', logs: null,
          areas: Object.fromEntries(requested.filter((topic) => topic in areas).map((topic) => [topic, areas[topic]]))
        };
      };

      vite = await createServer({ server: { middlewareMode: true, hmr: false, watch: null }, appType: 'custom' });
      server = createHttpServer((req, res) => {
        void (async () => {
          const path = req.url.split('?')[0];
          if (path === '/__release') { releaseTree(); json(res, { ok: true }); return; }
          if (!path.startsWith('/v1/')) {
            vite.middlewares(req, res, () => res.writeHead(404).end());
            return;
          }
          if (path === '/v1/dashboard/live') { json(res, live(req)); return; }
          if (path === '/v1/models/search' && req.method === 'POST') {
            const body = JSON.parse(await readBody(req) || '{}');
            searches.push(body.query);
            const query = body.query;
            if (query.includes('/datasets/')) {
              json(res, { detail: 'Supply a supported model repository, tree, or file link on huggingface.co or hf.co.' }, 400);
              return;
            }
            if (query.includes('/tree/main/empty-docs')) {
              json(res, { query, total_vram_bytes: null, results: [], warnings: [] });
              return;
            }
            if (query.includes('partial')) {
              json(res, {
                query, total_vram_bytes: 1024, warnings: ['org/bad could not be searched'],
                results: [row('good.Q4_K_M.gguf', 'org/good:good.Q4_K_M.gguf', 10, 'fits')]
              });
              return;
            }
            if (query.includes('/resolve/')) {
              json(res, { query, total_vram_bytes: 1024, warnings: [], results: [row('other/model.Q4_K_M.gguf', SOURCE_B, 4, 'fits')] });
              return;
            }
            if (query === 'qwen keyword') {
              json(res, { query, total_vram_bytes: 1024, warnings: [], results: [row('keyword.Q4_K_M.gguf', 'org/keyword:keyword.Q4_K_M.gguf', 10, 'fits')] });
              return;
            }
            if (query === TREE) await treeGate;
            json(res, {
              query, total_vram_bytes: null, warnings: [],
              results: [
                row('gguf/model.Q4_K_M.gguf', SOURCE_A, 4_000_000_000, 'fits'),
                row('other/model.Q4_K_M.gguf', SOURCE_B, 0, 'unknown')
              ]
            });
            return;
          }
          if (path === '/v1/models/download' && req.method === 'POST') {
            downloads.push(JSON.parse(await readBody(req) || '{}'));
            json(res, { ok: true, accepted: true, source: downloads.at(-1).source, state: 'running' });
            return;
          }
          if (path === '/v1/models') { json(res, { data: [] }); return; }
          if (path === '/v1/status') { json(res, { role: 'idle', node_id: 'local', members: {} }); return; }
          if (path === '/v1/hf-token') { json(res, { configured: false, source: null }); return; }
          if (path === '/v1/config') { json(res, { device: 'gpu', split_mode: 'layer' }); return; }
          if (path === '/v1/gpus') { json(res, { gpus: [], backend: 'cpu' }); return; }
          json(res, {});
        })().catch((error) => {
          if (!res.headersSent) res.writeHead(500);
          res.end(String(error));
        });
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
      const awaiting = new Map();
      ws.onmessage = ({ data }) => {
        const message = JSON.parse(data);
        if (awaiting.has(message.id)) { awaiting.get(message.id)(message); awaiting.delete(message.id); }
      };
      async function call(method, params = {}) {
        const requestId = ++id;
        const answer = new Promise((resolve, reject) => {
          const timer = setTimeout(() => reject(new Error(`CDP timeout: ${method}`)), 10000);
          awaiting.set(requestId, (message) => { clearTimeout(timer); resolve(message); });
        });
        ws.send(JSON.stringify({ id: requestId, method, params }));
        const response = await answer;
        assert.equal(response.error, undefined, JSON.stringify(response.error));
        return response.result;
      }
      async function evaluate(expression, awaitPromise = false) {
        const response = await call('Runtime.evaluate', { expression, returnByValue: true, awaitPromise });
        assert.equal(response.exceptionDetails, undefined, JSON.stringify(response.exceptionDetails));
        return response.result.value;
      }
      async function wait(expression) {
        for (let i = 0; i < 80; i++) {
          if (await evaluate(`Boolean(${expression})`)) return;
          await new Promise((resolve) => setTimeout(resolve, 100));
        }
        assert.fail(`Browser timed out: ${expression}\n${await evaluate('document.body.innerText')}`);
      }
      async function press(name, code, vk, text) {
        await call('Input.dispatchKeyEvent', { type: 'rawKeyDown', key: name, code, windowsVirtualKeyCode: vk });
        if (text) await call('Input.dispatchKeyEvent', { type: 'char', key: name, code, text, windowsVirtualKeyCode: vk });
        await call('Input.dispatchKeyEvent', { type: 'keyUp', key: name, code, windowsVirtualKeyCode: vk });
      }
      const enter = () => press('Enter', 'Enter', 13, '\r');
      async function setQuery(value) {
        await evaluate(`(() => {
          const input = document.querySelector('#model-source');
          input.focus();
          input.select();
        })()`);
        await call('Input.insertText', { text: value });
        await wait(`document.querySelector('#model-source').value === ${JSON.stringify(value)} && !document.querySelector('#model-source').form.querySelector('button[type=submit]').disabled`);
      }
      async function barSnapshot() {
        return evaluate(`(() => {
          const panel = document.querySelector('#model-source').closest('.panel');
          const form = document.querySelector('#model-source').form;
          const rect = panel.getBoundingClientRect();
          return {
            placeholder: document.querySelector('#model-source').placeholder,
            label: document.querySelector('label[for="model-source"]').textContent.trim(),
            button: form.querySelector('button[type=submit]').textContent.trim(),
            nav: document.querySelector('nav').textContent.replace(/\\s+/g, ' ').trim(),
            gap: getComputedStyle(form).columnGap || getComputedStyle(form).gap,
            x: rect.x, y: rect.y, width: rect.width, height: rect.height
          };
        })()`);
      }

      await call('Emulation.setDeviceMetricsOverride', { width: 1280, height: 720, deviceScaleFactor: 1, mobile: false });
      await call('Page.navigate', { url: base + '/models' });
      await wait("document.querySelector('#model-source')");
      await evaluate('document.fonts ? document.fonts.ready.then(() => true) : true', true);
      const baseline = await barSnapshot();
      assert.equal(baseline.placeholder, 'Qwen3.8-27B-Uncensored-GGUF:Q4_K_M, org/repo:Q4_K_M, or URL');
      assert.equal(baseline.label, 'Search');
      assert.equal(baseline.button, 'Search');
      assert.match(baseline.nav, /Home/);
      assert.match(baseline.nav, /Models/);
      assert.match(baseline.nav, /Plan & Run/);
      assert.ok(Math.abs(parseFloat(baseline.gap) - 6.5) < 0.6, `form gap ${baseline.gap}`);

      await setQuery(TREE);
      await enter();
      await wait("document.querySelector('[role=dialog] [role=status]')?.textContent.includes('Searching')");
      assert.equal(await evaluate(`document.querySelector('[role=dialog]').innerText.includes(${JSON.stringify(TREE)})`), true);
      assert.equal(downloads.length, 0);
      await fetch(base + '/__release');
      await wait("document.querySelector('[role=dialog]').innerText.includes('gguf/model.Q4_K_M.gguf')");
      const dialog = await evaluate("document.querySelector('[role=dialog]').innerText");
      assert.match(dialog, /other\/model\.Q4_K_M\.gguf/);
      assert.match(dialog, /unknown size/i);
      assert.match(dialog, /unknown fit/i);
      assert.match(dialog, /fits \(weight-size heuristic\)/i);
      assert.match(dialog, /revision release\/v2/i);
      assert.match(dialog, /Latest search/);
      assert.equal(dialog.includes(TREE), true);
      await setQuery('edited-after-results');
      const afterEdit = await evaluate("document.querySelector('[role=dialog]').innerText");
      assert.equal(afterEdit.includes(TREE), true);
      assert.equal(afterEdit.includes('edited-after-results'), false);
      assert.equal(downloads.length, 0, 'search must not start a transfer');
      assert.equal(searches[0], TREE);

      await evaluate("document.querySelector('[aria-label=\"Download gguf/model.Q4_K_M.gguf\"]').click()");
      await wait('document.querySelector(\'[role=dialog]\') == null');
      assert.equal(downloads.length, 1);
      assert.equal(downloads[0].source, SOURCE_A);

      await setQuery('qwen keyword');
      await evaluate("document.querySelector('#model-source').form.querySelector('button[type=submit]').click()");
      await wait("document.querySelector('[role=dialog]').innerText.includes('keyword.Q4_K_M.gguf')");
      const afterKeyword = await barSnapshot();
      assert.equal(afterKeyword.placeholder, baseline.placeholder);
      assert.equal(afterKeyword.label, baseline.label);
      assert.equal(afterKeyword.button, baseline.button);
      assert.equal(afterKeyword.nav, baseline.nav);
      assert.equal(afterKeyword.gap, baseline.gap);

      await evaluate("document.querySelector('#model-source').focus()");
      await setQuery(FILE);
      await enter();
      await wait("document.querySelectorAll('[role=dialog] [aria-label^=Download]').length === 1");
      const exact = await evaluate("document.querySelector('[role=dialog]').innerText");
      assert.match(exact, /other\/model\.Q4_K_M\.gguf/);
      assert.doesNotMatch(exact, /gguf\/model\.Q4_K_M\.gguf/);
      const beforeExactDownload = downloads.length;
      await evaluate("document.querySelector('[aria-label=\"Download other/model.Q4_K_M.gguf\"]').click()");
      for (let i = 0; i < 30 && downloads.length < beforeExactDownload + 1; i++) {
        await new Promise((resolve) => setTimeout(resolve, 100));
      }
      assert.equal(downloads.at(-1).source, SOURCE_B);
      assert.equal(downloads.length, beforeExactDownload + 1);

      await setQuery('https://huggingface.co/datasets/demo/set');
      await enter();
      await wait("document.querySelector('[role=dialog] [role=status]')?.textContent.includes('supported model repository')");
      assert.equal(await evaluate("document.querySelector('[role=dialog]').innerText.includes('https://huggingface.co/datasets/demo/set')"), true);
      await setQuery('edited-during-error');
      assert.equal(await evaluate("document.querySelector('[role=dialog]').innerText.includes('https://huggingface.co/datasets/demo/set')"), true);
      assert.equal(await evaluate("document.querySelector('[role=dialog]').innerText.includes('edited-during-error')"), false);
      assert.equal(await evaluate("document.querySelectorAll('[role=dialog] [aria-label^=Download]').length"), 0);

      await setQuery('https://huggingface.co/demo/model-GGUF/tree/main/empty-docs');
      await enter();
      await wait("document.querySelector('[role=dialog] [role=status]')?.textContent.includes('No supported downloadable options')");
      assert.equal(await evaluate("document.querySelector('[role=dialog]').innerText.includes('https://huggingface.co/demo/model-GGUF/tree/main/empty-docs')"), true);

      await setQuery('partial qwen');
      await enter();
      await wait("document.querySelector('[aria-label=\"Partial search warnings\"]')");
      assert.match(await evaluate("document.querySelector('[role=dialog]').innerText"), /org\/bad could not be searched/);
      assert.match(await evaluate("document.querySelector('[role=dialog]').innerText"), /good\.Q4_K_M\.gguf/);

      const focusedBefore = await evaluate("document.activeElement?.id || document.activeElement?.textContent");
      await press('Escape', 'Escape', 27);
      await wait("document.querySelector('[role=dialog]') == null");
      await wait("document.activeElement && (document.activeElement.id === 'model-source' || document.activeElement.closest('form'))");
      assert.ok(focusedBefore !== undefined);

      await call('Emulation.setDeviceMetricsOverride', { width: 390, height: 844, deviceScaleFactor: 1, mobile: true });
      await setQuery(TREE);
      await enter();
      await wait("document.querySelector('[role=dialog]').innerText.includes('gguf/model.Q4_K_M.gguf')");
      assert.equal(await evaluate('document.documentElement.scrollWidth <= window.innerWidth + 1'), true,
        'mobile models page must not overflow horizontally');
      const wrapped = await evaluate(`(() => {
        const path = [...document.querySelectorAll('[role=dialog] div')].find((el) => el.textContent === 'gguf/model.Q4_K_M.gguf');
        const style = getComputedStyle(path);
        return style.overflowWrap === 'anywhere' || style.overflowWrap === 'break-word';
      })()`);
      assert.equal(wrapped, true);
      await evaluate("document.querySelector('[role=dialog] button').focus()");
      await press('Tab', 'Tab', 9);
      assert.equal(await evaluate("document.activeElement?.getAttribute('aria-label')?.startsWith('Download')"), true);
    } finally {
      try { ws?.close(); } catch { /* ignore */ }
      if (browser) {
        await new Promise((resolve, reject) => {
          if (browser.exitCode !== null) { resolve(); return; }
          const timer = setTimeout(() => reject(new Error('Chrome did not exit')), 2000);
          browser.once('exit', () => { clearTimeout(timer); resolve(); });
          browser.kill();
        });
      }
      if (server) await new Promise((resolve) => server.close(resolve));
      try { await vite?.watcher?.close(); } catch { /* ignore */ }
      try { await vite?.close(); } catch { /* ignore */ }
      await removeBrowserProfile(profile);
      process.chdir(cwd);
    }
  });
