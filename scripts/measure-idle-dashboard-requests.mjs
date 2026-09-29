#!/usr/bin/env node
/**
 * Count browser application-state HTTP requests during idle dashboard viewing.
 * Used for SC-002 / T002 / T048 evidence (baseline commit vs live implementation).
 *
 * Usage (from repo root):
 *   node scripts/measure-idle-dashboard-requests.mjs --dashboard ./dashboard --viewers 1 --seconds 120 --route /logs
 *
 * Env: CHROME_BIN, IDLE_MEASURE_SECONDS (overrides --seconds)
 */
import assert from 'node:assert/strict';
import { createServer as createHttpServer } from 'node:http';
import { spawn } from 'node:child_process';
import { existsSync, mkdtempSync, readFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import { pathToFileURL } from 'node:url';
import { createRequire } from 'node:module';
const require = createRequire(new URL('../dashboard/package.json', import.meta.url));
const { createServer } = await import(pathToFileURL(require.resolve('vite')).href);

const STATE_PATHS = [
  '/v1/status',
  '/v1/usage/live',
  '/v1/load-progress',
  '/v1/models/download-progress',
  '/v1/dashboard/live',
  '/v1/logs'
];

function parseArgs(argv) {
  const out = {
    dashboard: resolve('dashboard'),
    viewers: 1,
    seconds: Number(process.env.IDLE_MEASURE_SECONDS || 120),
    route: '/logs'
  };
  for (let i = 2; i < argv.length; i++) {
    const arg = argv[i];
    if (arg === '--dashboard') out.dashboard = resolve(argv[++i]);
    else if (arg === '--viewers') out.viewers = Number(argv[++i]);
    else if (arg === '--seconds') out.seconds = Number(argv[++i]);
    else if (arg === '--route') out.route = argv[++i];
  }
  return out;
}

const chrome = [
  process.env.CHROME_BIN,
  'C:/Program Files/Google/Chrome/Application/chrome.exe',
  'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',
  '/usr/bin/google-chrome',
  '/usr/bin/chromium',
  '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'
].find((path) => path && existsSync(path));

function isStateRequest(url) {
  try {
    const path = new URL(url).pathname;
    return STATE_PATHS.some((prefix) => path === prefix || path.startsWith(prefix + '?'));
  } catch {
    return false;
  }
}

async function main() {
  const opts = parseArgs(process.argv);
  if (!chrome) {
    console.error(JSON.stringify({ error: 'no_chrome', message: 'Set CHROME_BIN or install Chrome/Edge' }));
    process.exit(2);
  }

  const cwd = process.cwd();
  process.chdir(opts.dashboard);
  const profile = mkdtempSync(join(tmpdir(), 'kllm-idle-measure-'));
  let browser;
  let vite;
  let server;
  let ws;
  const idleCounts = { stateRequests: 0, byPath: Object.fromEntries(STATE_PATHS.map((p) => [p, 0])) };
  let idleWindow = false;

  try {
    vite = await createServer({
      root: opts.dashboard,
      server: { middlewareMode: true, hmr: false, watch: null },
      appType: 'custom'
    });

    server = createHttpServer((req, res) => {
      const path = req.url.split('?')[0];
      if (path.startsWith('/v1/')) {
        res.writeHead(200, { 'Content-Type': 'application/json' });
        if (path === '/v1/status') {
          res.end(JSON.stringify({ role: 'master', node_id: 'master', members: {}, inference_up: false }));
          return;
        }
        if (path === '/v1/usage/live') {
          res.end(JSON.stringify({ nodes: {}, relationships: [], recording: { recording: 'idle' } }));
          return;
        }
        if (path === '/v1/load-progress') {
          res.end(JSON.stringify(null));
          return;
        }
        if (path === '/v1/models/download-progress') {
          res.end(JSON.stringify(null));
          return;
        }
        if (path === '/v1/dashboard/live') {
          res.end(JSON.stringify({
            version: 1, transport: 'websocket', epoch: '00000000-0000-4000-8000-000000000001',
            server_time: new Date().toISOString(),
            areas: {
              status: { revision: 1, availability: 'available', data: { role: 'master', members: {} } }
            }
          }));
          return;
        }
        if (path === '/v1/logs') {
          res.end(JSON.stringify({ lines: [], cursor: '00000000-0000-4000-8000-000000000001:0', gap: false }));
          return;
        }
        res.end('{}');
        return;
      }
      vite.middlewares(req, res, () => res.writeHead(404).end());
    });

    await new Promise((resolveListen) => server.listen(0, '127.0.0.1', resolveListen));
    const base = `http://127.0.0.1:${server.address().port}`;

    browser = spawn(
      chrome,
      ['--headless=new', '--disable-gpu', '--no-first-run', '--remote-allow-origins=*',
        '--remote-debugging-port=0', `--user-data-dir=${profile}`, 'about:blank'],
      { stdio: 'ignore' }
    );
    const portfile = join(profile, 'DevToolsActivePort');
    for (let i = 0; i < 100 && !existsSync(portfile); i++) {
      await new Promise((r) => setTimeout(r, 100));
    }
    assert.ok(existsSync(portfile), 'Chrome DevTools did not start');
    const port = Number(readFileSync(portfile, 'utf8').split('\n')[0]);
    async function listPages() {
      return (await fetch(`http://127.0.0.1:${port}/json/list`)).json();
    }

    async function attachPage() {
      let pages = await listPages();
      if (pages.length === 0) throw new Error('no CDP pages');
      const pageWs = new WebSocket(pages[pages.length - 1].webSocketDebuggerUrl);
      await new Promise((resolveOpen, reject) => { pageWs.onopen = resolveOpen; pageWs.onerror = reject; });
      let id = 0;
      const awaiting = new Map();
      pageWs.onmessage = ({ data }) => {
        const message = JSON.parse(data);
        if (idleWindow && message.method === 'Network.requestWillBeSent') {
          const url = message.params?.request?.url ?? '';
          if (isStateRequest(url)) {
            idleCounts.stateRequests++;
            const pathname = new URL(url).pathname;
            const key = STATE_PATHS.find((p) => pathname === p);
            if (key) idleCounts.byPath[key]++;
          }
        }
        if (awaiting.has(message.id)) {
          awaiting.get(message.id)(message);
          awaiting.delete(message.id);
        }
      };
      async function call(method, params = {}) {
        const requestId = ++id;
        const answer = new Promise((resolve, reject) => {
          const timer = setTimeout(() => reject(new Error(`CDP timeout: ${method}`)), 15000);
          awaiting.set(requestId, (message) => { clearTimeout(timer); resolve(message); });
        });
        pageWs.send(JSON.stringify({ id: requestId, method, params }));
        const response = await answer;
        assert.equal(response.error, undefined, JSON.stringify(response.error));
        return response.result;
      }
      await call('Network.enable');
      await call('Page.navigate', { url: base + opts.route });
      await call('Runtime.evaluate', {
        expression: `new Promise((resolve) => {
          const start = Date.now();
          const tick = () => {
            const ready = document.querySelector('.dashboard-header') &&
              (document.querySelector('.logs-scroll') || document.querySelector('.node-map'));
            if (ready) resolve(true);
            else if (Date.now() - start > 45000) resolve(false);
            else setTimeout(tick, 200);
          };
          tick();
        })`,
        awaitPromise: true
      });
      return pageWs;
    }

    const sockets = [];
    for (let v = 0; v < opts.viewers; v++) {
      if (v > 0) {
        await fetch(`http://127.0.0.1:${port}/json/new?${encodeURIComponent(base + opts.route)}`, { method: 'PUT' });
        await new Promise((r) => setTimeout(r, 500));
      }
      const socket = await attachPage();
      if (v === 0) ws = socket;
      else sockets.push(socket);
    }

    // Warm-up: allow initial load/recovery to finish before the idle window.
    await new Promise((r) => setTimeout(r, 15000));

    idleWindow = true;
    await new Promise((r) => setTimeout(r, opts.seconds * 1000));
    idleWindow = false;

    const result = {
      dashboardRoot: opts.dashboard,
      viewers: opts.viewers,
      route: opts.route,
      measureSeconds: opts.seconds,
      extrapolatedTenMinute: Math.round(idleCounts.stateRequests * (600 / opts.seconds)),
      idleStateRequests: idleCounts.stateRequests,
      byPath: idleCounts.byPath,
      host: '127.0.0.1',
      measuredAt: new Date().toISOString()
    };
    console.log(JSON.stringify(result, null, 2));
  } finally {
    ws?.close();
    browser?.kill();
    if (browser) await new Promise((resolve) => browser.once('exit', resolve));
    server?.close();
    await vite?.close();
    rmSync(profile, { recursive: true, force: true });
    process.chdir(cwd);
  }
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
