import assert from 'node:assert/strict';
import test from 'node:test';
import { spawn } from 'node:child_process';
import { createServer as createHttpServer } from 'node:http';
import { existsSync, mkdtempSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { resolve, join } from 'node:path';
import { createServer } from 'vite';
import { removeBrowserProfile } from './browser-profile.mjs';

const chrome = [process.env.CHROME_BIN, 'C:/Program Files/Google/Chrome/Application/chrome.exe',
  'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe', '/usr/bin/chromium',
  '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'].find(path => path && existsSync(path));
const pause = ms => new Promise(resolve => setTimeout(resolve, ms));
const GIB = 1024 ** 3;
const longModel = 'operator-fixture-' + 'long-model-name-'.repeat(5) + 'Q4_K_M.gguf';
const epoch = '00000000-0000-4000-8000-000000000001';

test('all routes fit narrow screens; errors dismiss, reset and remain clear after refresh',
  { skip: !chrome && 'Chrome/Edge required for dashboard browser verification' }, async () => {
    const originalCwd = process.cwd();
    const baseline = !!process.env.KLLM_POLISH_BASELINE;
    const screenshotDir = resolve(originalCwd, '../artifacts/dashboard-polish', baseline ? 'before' : 'after');
    if (baseline) process.chdir(process.env.KLLM_POLISH_BASELINE);
    mkdirSync(screenshotDir, { recursive: true });
    const profile = mkdtempSync(join(tmpdir(), 'kllm-polish-'));
    let browser, vite, server, ws;
    try {
      let role = 'master', inference = true, applyError = 'historical apply failure';
      let revision = 1, loadError = 'historical load failure', stopFails = false;
      const nodes = Object.fromEntries(['master', 'worker-a', 'worker-b', 'worker-c'].map((id, i) => [id, {
        node_id: id, host: i === 3 ? 'long-worker-hostname-' + 'label-'.repeat(12) : `${id}-host`,
        role: i ? 'worker' : 'master', connected: true, freshness: 'fresh', age_seconds: 1,
        total_ram_bytes: 32 * GIB, backend: 'vulkan', assignment: longModel,
        devices: [{ index: 0, name: 'NVIDIA RTX 4090', vram_bytes: 24 * GIB }],
        reading: { lifetime_id: `${id}:99`, pid: 99, process_role: i ? 'worker' : 'master',
          process_started_at: '2026-10-02T00:00:00Z', observed_at: '2026-10-02T00:00:01Z',
          process: { cpu_percent_machine: 12, rss_bytes: 3 * GIB },
          host: { cpu_percent: 12 + i * 10, used_bytes: (8 + i) * GIB, total_bytes: 32 * GIB },
          devices: [{ device_id: '0', scope: 'device', utilization_percent: 30 + i * 10,
            used_bytes: (6 + i) * GIB, total_bytes: 24 * GIB }],
          availability: { process_cpu: 'measured', process_rss: 'measured', host_cpu: 'measured', host_memory: 'measured' } }
      }]));
      const recorder = { recording: 'recording', error: null, last_successful_save: '2026-10-02T00:00:01Z',
        coverage: 'recorded', storage_truncated: false };
      const status = () => ({ role, node_id: 'master', members: role === 'idle' ? {} : nodes,
        inference_up: inference, process_pid: inference ? 99 : null, process_role: inference ? 'master' : null,
        model_name: inference ? longModel : null, apply_error: applyError, logging: { enabled: true },
        software: { git_head: 'fixture-123456', llama_release: 'fixture-stable' } });
      const logs = { lines: [{ event_id: epoch.slice(0, -1) + '2', ts: 1790899200,
        node_id: 'master', source: 'agent', tag: 'ui', line: 'Fixture: cluster ready; ' + longModel }],
        source: 'cluster', cursor: `${epoch}:1`, reset: null, gap: false, gap_reason: null,
        continuity_id: epoch, discontinuity: null };
      const load = () => loadError ? { current: null, last_terminal: { operation_id: epoch,
        kind: 'load', node_id: 'master', state: 'error', started_at: '2026-10-02T00:00:00Z',
        updated_at: '2026-10-02T00:00:01Z', finished_at: '2026-10-02T00:00:01Z',
        phase: 'error', model: longModel, bytes_loaded: 0, bytes_total: GIB, fraction: 0, error: loadError } }
        : { current: null, last_terminal: null };
      const json = (res, value, code = 200) => res.writeHead(code, { 'Content-Type': 'application/json' }).end(JSON.stringify(value));
      vite = await createServer({ server: { middlewareMode: true, hmr: false, ws: false, watch: null }, appType: 'custom' });
      server = createHttpServer((req, res) => {
        const url = new URL(req.url, 'http://fixture'), path = url.pathname;
        if (!path.startsWith('/v2/')) return vite.middlewares(req, res, () => res.writeHead(404).end());
        if (path === '/v2/events') {
          const area = data => ({ revision, sampled_at: '2026-10-02T00:00:01Z', availability: 'available',
            coverage: 'cluster', upstream_last_success_at: null, error: null, data });
          const areas = { status: area(status()), usage: area({ nodes: role === 'idle' ? {} : nodes,
            relationships: Object.keys(nodes).slice(1).map(id => ({ from_node_id: 'master', to_node_id: id, kind: 'master-worker' })), recording: recorder }),
            load: area(load()), download: area({ current: null, last_terminal: null }) };
          const requested = url.searchParams.get('topics')?.split(',') ?? Object.keys(areas);
          return json(res, { version: 1, transport: 'websocket', epoch, server_time: '2026-10-02T00:00:01Z',
            areas: Object.fromEntries(requested.filter(topic => topic in areas).map(topic => [topic, areas[topic]])),
            logs: url.searchParams.get('logs') === '1' ? { availability: 'available', error: null, sampled_at: '2026-10-02T00:00:01Z', snapshot: logs } : null });
        }
        if (path === '/v2/node') return json(res, status());
        if (path === '/v2/models') return json(res, { data: [{ id: longModel, size_bytes: 8 * GIB, loaded: inference }] });
        if (path === '/v2/models/search') return json(res, { error: { message: 'Fixture search failure' } }, 503);
        if (path === '/v2/node/devices') return json(res, { gpus: [{ index: 0, name: 'NVIDIA RTX 4090', vram_bytes: 24 * GIB }], backend: 'vulkan', installed_backends: ['vulkan'], allowed_backends: ['vulkan'] });
        if (path === '/v2/node/config') return json(res, { device: 'gpu', split_mode: 'layer', gpu_ids: [0], vram_percent: 80, ram_percent: 80 });
        if (path === '/v2/node/home') return json(res, { home: 'C:/Users/operator/' + 'long-directory/'.repeat(7), models: 'C:/Models/' + longModel, logs: 'C:/Logs/cluster' });
        if (path === '/v2/logs') return json(res, logs);
        if (path.includes('/defaults')) return json(res, { context: 4096, model_max_context: 32768 });
        if (path === '/v2/inference/capabilities') return json(res, { threads_max: 16 });
        if (path === '/v2/inference/plans') return json(res, { fit_status: 'fits', estimated_bytes: 8 * GIB, budget_bytes: 24 * GIB,
          node_ids: ['master'], budgets_bytes: [24 * GIB], resolved_launch_settings: { context: 4096, batch_size: 512, micro_batch: 128, slots: 1 } });
        if (path === '/v2/inference/runs/stop' || (req.method === 'DELETE' && ['/v2/cluster', '/v2/cluster/membership'].includes(path))) {
          if (stopFails) return json(res, { detail: 'Fixture stop refused' }, 409);
          inference = false; applyError = null; loadError = ''; revision++;
          if (path !== '/v2/inference/runs/stop') role = 'idle';
          return json(res, { ok: true });
        }
        return json(res, {});
      });
      await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
      const base = `http://127.0.0.1:${server.address().port}`;
      browser = spawn(chrome, ['--headless=new', '--disable-gpu', '--no-first-run', '--remote-allow-origins=*',
        '--remote-debugging-port=0', `--user-data-dir=${profile}`, 'about:blank'], { stdio: 'ignore' });
      const portfile = join(profile, 'DevToolsActivePort');
      for (let i = 0; i < 100 && !existsSync(portfile); i++) await pause(100);
      assert.ok(existsSync(portfile));
      const port = Number(readFileSync(portfile, 'utf8').split('\n')[0]);
      const pages = await (await fetch(`http://127.0.0.1:${port}/json/list`)).json();
      ws = new WebSocket(pages.find(page => page.type === 'page').webSocketDebuggerUrl);
      await new Promise((resolve, reject) => { ws.onopen = resolve; ws.onerror = reject; });
      let id = 0;
      const pending = new Map();
      ws.onmessage = ({ data }) => {
        const message = JSON.parse(data);
        if (pending.has(message.id)) { pending.get(message.id)(message); pending.delete(message.id); }
      };
      async function call(method, params = {}) {
        const requestId = ++id;
        const promise = new Promise((resolve, reject) => {
          const timer = setTimeout(() => reject(new Error(`CDP timed out: ${method}`)), 15000);
          pending.set(requestId, message => { clearTimeout(timer); resolve(message); });
        });
        ws.send(JSON.stringify({ id: requestId, method, params }));
        const response = await promise;
        assert.equal(response.error, undefined);
        return response.result;
      }
      async function evaluate(expression) {
        const response = await call('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true });
        assert.equal(response.exceptionDetails, undefined, JSON.stringify(response.exceptionDetails));
        return response.result.value;
      }
      async function wait(expression) {
        for (let i = 0; i < 150; i++) { if (await evaluate(`Boolean(${expression})`)) return; await pause(100); }
        assert.fail(`Browser timed out: ${expression}; ${await evaluate('document.body.innerText')}`);
      }
      async function navigate(route) {
        await call('Page.navigate', { url: base + route });
        await wait("document.querySelector('.app-shell') && document.querySelector('.chip-ok')?.textContent === 'online'");
        await evaluate('document.fonts.ready');
        await pause(500);
      }
      async function screenshot(name) {
        const { data } = await call('Page.captureScreenshot', { format: 'png', captureBeyondViewport: false });
        writeFileSync(join(screenshotDir, name + '.png'), Buffer.from(data, 'base64'));
      }
      async function verifyFieldGroups(route, width) {
        const violations = await evaluate(`([...document.querySelectorAll('label.field')]
          .filter(label => label.getClientRects().length).flatMap(label => {
            const control = label.querySelector('input, select, textarea');
            if (!control) return [];
            const group = label.getBoundingClientRect(), child = control.getBoundingClientRect();
            const style = getComputedStyle(label);
            const next = label.closest('.toolbar')?.nextElementSibling ?? label.nextElementSibling;
            return [
              child.bottom > group.bottom + 1 && 'control extends outside label group',
              parseFloat(style.borderTopWidth) > 0 && 'label has input border',
              control.type === 'text' && child.width < 160 && 'text input is too narrow to use',
              next && next.getBoundingClientRect().top < child.bottom - 1 &&
                next.getBoundingClientRect().left < child.right && 'following content overlaps control'
            ].filter(Boolean).map(problem => label.textContent.trim() + ': ' + problem);
          }))`);
        assert.deepEqual(violations, [], `${route} at ${width}px`);
      }
      const routes = ['/', '/models', '/run', '/integrations', '/logs', '/settings'];
      const metrics = [];
      for (const width of [390, 768, 1440]) {
        await call('Emulation.setDeviceMetricsOverride', { width, height: 900, deviceScaleFactor: 1, mobile: false });
        for (const route of routes) {
          await navigate(route);
          const result = await evaluate(`({width:innerWidth, scroll:document.documentElement.scrollWidth,
            alerts:document.querySelectorAll('[data-error-source]').length})`);
          metrics.push({ route, ...result });
          if (!baseline) {
            assert.ok(result.scroll <= width + 1, `${route} at ${width}px overflows: ${JSON.stringify(result)}`);
            assert.equal(result.alerts, 0, `${route} must suppress historical failures after hydration`);
          }
          await screenshot(`${route === '/' ? 'home' : route.slice(1)}-${width}`);
          if (!baseline) await verifyFieldGroups(route, width);
          if (route === '/models') {
            await evaluate("[...document.querySelectorAll('[role=tab]')].find(el => el.textContent.includes('Local')).click()");
            if (!baseline) await verifyFieldGroups(route, width);
            if (!baseline) assert.ok(await evaluate(`(() => {
              const main = document.querySelector('main');
              return main.scrollWidth <= main.clientWidth + 1;
            })()`), `Local models overflow their viewport at ${width}px`);
            await screenshot(`models-local-${width}`);
          }
          if (route === '/logs' && !baseline) {
            const contrast = await evaluate(`(() => {
              const label = document.querySelector('.btn-mode:has(input:checked)');
              const fg = getComputedStyle(label.querySelector('span')).color;
              const bg = getComputedStyle(label).backgroundColor;
              const luminance = color => {
                const channels = color.match(/[\\d.]+/g).slice(0, 3).map(Number).map(v => {
                  v /= 255; return v <= .04045 ? v / 12.92 : ((v + .055) / 1.055) ** 2.4;
                });
                return channels[0] * .2126 + channels[1] * .7152 + channels[2] * .0722;
              };
              const values = [luminance(fg), luminance(bg)].sort((a,b) => b-a);
              return (values[0] + .05) / (values[1] + .05);
            })()`);
            assert.ok(contrast >= 4.5, `Selected refresh label contrast: ${contrast}`);
          }
        }
      }
      role = 'worker'; revision++;
      for (const width of [390, 768, 1440]) {
        await call('Emulation.setDeviceMetricsOverride', { width, height: 900, deviceScaleFactor: 1, mobile: false });
        for (const route of ['/', '/integrations', '/logs']) {
          await navigate(route);
          if (!baseline) await verifyFieldGroups(route, width);
          await screenshot(`worker-${route === '/' ? 'home' : route.slice(1)}-${width}`);
        }
      }
      role = 'master'; revision++;
      await call('Emulation.setDeviceMetricsOverride', { width: 1280, height: 720, deviceScaleFactor: 1, mobile: false });
      await navigate('/');
      await screenshot('home-1280x720');
      // A 640x360 CSS viewport exercises layout at 200% zoom of 1280x720.
      await call('Emulation.setDeviceMetricsOverride', { width: 640, height: 360, deviceScaleFactor: 2, mobile: false });
      await navigate('/');
      await screenshot('home-200-percent-layout');
      if (!baseline) {
        assert.ok(await evaluate('document.documentElement.scrollWidth <= innerWidth + 1'));
        await call('Emulation.setDeviceMetricsOverride', { width: 1440, height: 900, deviceScaleFactor: 1, mobile: false });
        await navigate('/');
        await evaluate("import('/src/lib/app.svelte.ts').then(({createApp})=>{const appStore=createApp();return appStore.live.refresh()})");
        applyError = 'Fresh fixture apply failure'; loadError = ''; revision++;
        await evaluate("import('/src/lib/app.svelte.ts').then(({createApp})=>{const appStore=createApp();return appStore.live.refresh()})");
        await wait("document.querySelector('[data-error-source=apply]')");
        await screenshot('home-new-error');
        await evaluate("document.querySelector('[data-error-source=apply] button').click()");
        await wait("!document.querySelector('[data-error-source=apply]')");
        await evaluate("import('/src/lib/app.svelte.ts').then(({createApp})=>{const appStore=createApp();return appStore.live.refresh()})");
        await pause(250);
        assert.equal(await evaluate("document.querySelectorAll('[data-error-source=apply]').length"), 0);
        await evaluate("document.querySelector('a[href=\"/run\"]').click()");
        await wait("location.pathname === '/run'");
        await pause(250);
        assert.equal(await evaluate("document.querySelectorAll('[data-error-source=apply]').length"), 0);
        // Dismissal is presentation-only: invalid settings continue to block execution.
        await evaluate("[...document.querySelectorAll('button')].find(button => button.textContent.trim() === 'Configure').click()");
        await wait("document.querySelector('#run-context')");
        await evaluate("import('/src/lib/app.svelte.ts').then(({createApp})=>{const appStore=createApp();appStore.run.context=0;appStore.run.validateRunSettings();})");
        await wait("document.querySelector('[data-error-source=\"validation:context\"]')");
        await evaluate("document.querySelector('[data-error-source=\"validation:context\"] button').click()");
        assert.equal(await evaluate("import('/src/lib/app.svelte.ts').then(({createApp})=>{const appStore=createApp();return !!appStore.run.fieldErrors.context})"), true);
        await evaluate("import('/src/lib/app.svelte.ts').then(({createApp})=>{const appStore=createApp();appStore.run.context=4096;appStore.run.validateRunSettings();})");
        await evaluate("import('/src/lib/shared/errors.ts').then(({errors})=>errors.publish('fixture-local','Local fixture error'))");
        await evaluate("import('/src/lib/app.svelte.ts').then(({createApp})=>{const appStore=createApp();return appStore.run.stopInference()})");
        assert.equal(await evaluate("import('/src/lib/shared/errors.ts').then(({errors})=>{let result;const unsub=errors.subscribe(s=>result=Object.values(s.entries).filter(e=>e.visible).length);unsub();return result;})"), 0);
        stopFails = true;
        await evaluate("import('/src/lib/app.svelte.ts').then(({createApp})=>{const appStore=createApp();return appStore.cluster.stopCluster().catch(()=>{})})");
        await wait("[...document.querySelectorAll('[data-error-source]')].some(e=>e.textContent.includes('Fixture stop refused'))");
        await screenshot('run-failed-stop');
        await navigate('/run');
        assert.equal(await evaluate("document.querySelectorAll('[data-error-source]').length"), 0);
        await call('Emulation.setEmulatedMedia', { features: [{ name: 'prefers-reduced-motion', value: 'reduce' }] });
        await call('Input.dispatchKeyEvent', { type: 'keyDown', key: 'Tab', code: 'Tab', windowsVirtualKeyCode: 9 });
        await call('Input.dispatchKeyEvent', { type: 'keyUp', key: 'Tab', code: 'Tab', windowsVirtualKeyCode: 9 });
        await evaluate("document.querySelector('nav a').focus()");
        const focus = await evaluate("getComputedStyle(document.querySelector('nav a')).outlineStyle");
        assert.notEqual(focus, 'none');
        // A connection drop clears once; repeated offline/reconnect notifications retain new messages.
        const dropped = await evaluate(`Promise.all([import('/src/lib/app.svelte.ts'),import('/src/lib/shared/errors.ts')]).then(([{createApp},{errors}])=>{const appStore=createApp();
          errors.publish('fixture-local','Previous local failure');
          appStore.live.updates.options.onHealth('unavailable','Fixture connection lost');
          let state; const unsubscribe=errors.subscribe(value=>state=value); unsubscribe();
          return {connected:appStore.live.connected,visible:Object.values(state.entries).filter(e=>e.visible).length,generation:state.generation};
        })`);
        assert.equal(dropped.connected, false);
        assert.equal(dropped.visible, 0);
        const repeated = await evaluate(`Promise.all([import('/src/lib/app.svelte.ts'),import('/src/lib/shared/errors.ts')]).then(([{createApp},{errors}])=>{const appStore=createApp();
          errors.publish('fixture-new','Fresh request failure while offline');
          appStore.live.updates.options.onHealth('unavailable','Fixture connection lost');
          appStore.live.updates.options.onHealth('loading');
          let state; const unsubscribe=errors.subscribe(value=>state=value); unsubscribe();
          return {visible:Object.values(state.entries).filter(e=>e.visible).length,generation:state.generation};
        })`);
        assert.equal(repeated.generation, dropped.generation);
        assert.equal(repeated.visible, 1);
      }
      writeFileSync(join(screenshotDir, 'metrics.json'), JSON.stringify(metrics, null, 2));
    } finally {
      ws?.close();
      if (browser && browser.exitCode === null) { browser.kill(); await new Promise(resolve => browser.once('exit', resolve)); }
      await removeBrowserProfile(profile);
      await vite?.watcher?.close();
      await vite?.close();
      if (server) await new Promise(resolve => server.close(resolve));
      process.chdir(originalCwd);
    }
  });
