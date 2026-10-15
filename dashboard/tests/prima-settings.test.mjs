import assert from 'node:assert/strict';
import test from 'node:test';
import { spawn } from 'node:child_process';
import { createServer as httpServer } from 'node:http';
import { existsSync, mkdtempSync, readFileSync, mkdirSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { resolve, join } from 'node:path';
import { createServer } from 'vite';
import { removeBrowserProfile } from './browser-profile.mjs';

const chrome = [process.env.CHROME_BIN, '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
  '/usr/bin/chromium', 'C:/Program Files/Google/Chrome/Application/chrome.exe'].find(p => p && existsSync(p));
const pause = ms => new Promise(r => setTimeout(r, ms));

test('Prima mode manages versions (add, select, inspect, logs, remove), centers all modals, and sets the cluster target',
  { skip: !chrome && 'Chrome required for Prima pin browser check' }, async () => {
    const primaRepo = 'https://github.com/berkkarahan/prima.cpp.git';
    const prima = {
      name: 'Prima.cpp 0.0.1', engine: 'primacpp', repository: primaRepo, ref: '0.0.1',
      ref_type: 'tag', commit: '1'.repeat(40), runtime_id: '2'.repeat(64), recipe_digest: '3'.repeat(64)
    };
    const experimental = {
      name: 'Vulkan ring tuning', engine: 'primacpp', repository: primaRepo, ref: 'codex/ring-tuning',
      ref_type: 'branch', commit: '4'.repeat(40), runtime_id: '5'.repeat(64), recipe_digest: '3'.repeat(64)
    };
    const gates = ['vulkan_gpu_inference', 'api_parity', 'platform_backend_parity',
      'model_and_placement_parity', 'signed_rank_lifecycle', 'hardware_performance_gate'];
    const job = { job_id: '00000000-0000-4000-8000-000000000099', runtime_id: prima.runtime_id, phase: 'prepared', backend: 'vulkan' };
    // Pre-pin state: the launch engine is not in the library yet.
    const library = {
      versions: [], jobs: [job], selected: null,
      bootstrap: { repository: 'https://github.com/ggml-org/llama.cpp.git', ref: 'v0.5.0' },
      target: null, running: null,
      engine_sources: [{ engine: 'primacpp', repository: primaRepo, ref: prima.commit, ref_type: 'commit', release: '0.0.1', release_commit: prima.commit, qualification_errors: gates }],
      nodes: [{ node_id: 'master', name: 'control-box', address: '10.10.10.1', role: 'master', capability: 1, phase: 'offline', job }]
    };
    const status = () => ({ role: 'master', node_id: 'master', members: {}, process_pid: 99,
      process_role: 'master', inference_up: false, engine: 'primacpp', runtimes: library, capacity: { backend: 'vulkan' },
      accel_backend: 'vulkan', software: { git_head: 'fixture', llama_release: 'v0.5.0' } });
    const commands = [];
    const profile = mkdtempSync(join(tmpdir(), 'kllm-prima-pin-'));
    const screenshots = resolve('../artifacts/prima-settings');
    mkdirSync(screenshots, { recursive: true });
    let vite, server, browser, socket;
    try {
      vite = await createServer({server: {middlewareMode: true, hmr: false, ws: false, watch: null}, appType: 'custom'});
      server = httpServer(async (req, res) => {
        const url = new URL(req.url, 'http://fixture'), path = url.pathname;
        const json = (value, code = 200) => res.writeHead(code, {'Content-Type': 'application/json'}).end(JSON.stringify(value));
        if (!path.startsWith('/v2/')) return vite.middlewares(req, res, () => res.writeHead(404).end());
        if (req.method === 'DELETE' && path.startsWith('/v2/node/runtimes/')) {
          const runtimeId = decodeURIComponent(path.slice('/v2/node/runtimes/'.length));
          commands.push({method: 'DELETE', path, value: {runtime_id: runtimeId}});
          library.versions = library.versions.filter(v => v.version.runtime_id !== runtimeId);
          return json({ok: true});
        }
        if (req.method !== 'GET') {
          let raw = ''; for await (const chunk of req) raw += chunk;
          const value = raw ? JSON.parse(raw) : {};
          commands.push({method: req.method, path, value});
          if (path === '/v2/node/runtimes/resolve') {
            if (value.ref === 'codex/ring-tuning') {
              return json({...value, commit: experimental.commit, recipe_digest: experimental.recipe_digest, runtime_id: experimental.runtime_id});
            }
            return json({...value, commit: prima.commit, recipe_digest: prima.recipe_digest, runtime_id: prima.runtime_id});
          }
          if (path === '/v2/node/runtimes') library.versions.push({version: value, build: null, inference_supported: false, qualification_errors: gates});
          if (path === '/v2/cluster/runtime-target') {
            const matched = library.versions.find(v => v.version.runtime_id === value.runtime_id)?.version ?? prima;
            library.target = {version: matched, revision: (library.target?.revision ?? 0) + 1, cluster_binding_id: 'fixture', master_node_id: 'master'};
          }
          return json({ok: true});
        }
        if (path === '/v2/node/runtimes') return json(library);
        if (path.endsWith('/logs')) return json({text: '[12/12] Linking CXX executable bin/llama-server\nVerified Prima capabilities.'});
        if (path === '/v2/credentials/api-token') return json({api_token: 'fixture-token'});
        if (path === '/v2/node') return json(status());
        if (path === '/v2/node/home') return json({home: '/fixture', models: '/fixture/models', logs: '/fixture/logs'});
        if (path === '/v2/node/config') return json({device: 'gpu', gpu_ids: [0], ram_percent: 80, vram_percent: 80, accel_backend: 'vulkan', engine: 'primacpp'});
        if (path === '/v2/node/devices') return json({devices: [], backend: 'vulkan', installed_backends: ['vulkan'], allowed_backends: ['vulkan']});
        if (path === '/v2/node/prima-devices') return json({available: false, features: [], backends: [], devices: []});
        if (path === '/v2/models') return json({data: []});
        if (path === '/v2/events') {
          const area = data => ({revision: 1, sampled_at: new Date().toISOString(), availability: 'available', coverage: 'local', upstream_last_success_at: null, error: null, data});
          const areas = {status: area(status()), usage: area({nodes: {}, relationships: [], recording: {recording: 'stopped'}}), load: area({current: null, last_terminal: null}), download: area({current: null, last_terminal: null})};
          const topics = url.searchParams.get('topics')?.split(',') ?? Object.keys(areas);
          return json({version: 1, transport: 'websocket', epoch: '00000000-0000-4000-8000-000000000003', server_time: new Date().toISOString(),
            areas: Object.fromEntries(topics.filter(t => t in areas).map(t => [t, areas[t]])), logs: null});
        }
        return json({});
      });
      await new Promise(r => server.listen(0, '127.0.0.1', r));
      const base = `http://127.0.0.1:${server.address().port}`;
      browser = spawn(chrome, ['--headless=new', '--no-sandbox', '--disable-gpu', '--remote-debugging-port=0', `--user-data-dir=${profile}`, 'about:blank'], {stdio: 'ignore'});
      for (let i=0; i<100 && !existsSync(join(profile, 'DevToolsActivePort')); i++) await pause(100);
      const port = readFileSync(join(profile, 'DevToolsActivePort'), 'utf8').split('\n')[0];
      const tab = (await (await fetch(`http://127.0.0.1:${port}/json/list`)).json()).find(t => t.type === 'page');
      socket = new WebSocket(tab.webSocketDebuggerUrl);
      await new Promise(r => socket.onopen = r);
      let id=0; const pending = new Map();
      socket.onmessage = ({data}) => {const message=JSON.parse(data); pending.get(message.id)?.(message); pending.delete(message.id);};
      async function call(method, params={}) {
        const request = ++id;
        const result = new Promise((resolve, reject) => {const timer=setTimeout(()=>reject(new Error(method)), 15000);pending.set(request,m=>{clearTimeout(timer);resolve(m);});});
        socket.send(JSON.stringify({id:request,method,params}));
        const message=await result; assert.equal(message.error,undefined);return message.result;
      }
      async function evaluate(expression) {const r=await call('Runtime.evaluate',{expression,returnByValue:true,awaitPromise:true});assert.equal(r.exceptionDetails,undefined);return r.result.value;}
      async function wait(expression) {for(let i=0;i<100;i++){if(await evaluate(`Boolean(${expression})`))return;await pause(100);}assert.fail(`Timeout: ${expression}; ${await evaluate('document.body.innerText')}`);}
      async function click(text) {await evaluate(`[...document.querySelectorAll('button')].find(b=>b.textContent.trim()===${JSON.stringify(text)} && b.getClientRects().length).click()`);}
      async function navigate(section) {await call('Page.navigate',{url:`${base}/settings?section=${section}`});await wait("document.querySelector('.settings-nav')");await evaluate('document.fonts.ready');await pause(150);}
      async function assertDialogCentered(label) {
        const metrics = await evaluate(`(() => {
          const dlg = document.querySelector('dialog[open]');
          if (!dlg) return null;
          const r = dlg.getBoundingClientRect();
          return { cx: r.left + r.width / 2, cy: r.top + r.height / 2, vw: innerWidth / 2, vh: innerHeight / 2 };
        })()`);
        assert.ok(metrics, `${label} dialog should be open`);
        assert.ok(Math.abs(metrics.cx - metrics.vw) <= 2, `${label} dialog not horizontally centered: ${JSON.stringify(metrics)}`);
        assert.ok(Math.abs(metrics.cy - metrics.vh) <= 2, `${label} dialog not vertically centered: ${JSON.stringify(metrics)}`);
      }

      // Empty state: nothing pinned yet, Saved versions panel sits above Runtime identity.
      await call('Emulation.setDeviceMetricsOverride',{width:390,height:1000,deviceScaleFactor:1,mobile:false});
      await navigate('prima');
      assert.ok(await evaluate('document.documentElement.scrollWidth <= innerWidth + 1'), 'Prima pin tab overflows 390px');
      const empty = await evaluate('document.body.innerText');
      assert.match(empty, /Saved versions/);
      assert.match(empty, /No pinned Prima source/);
      assert.match(empty, /0 pinned sources/i);
      assert.doesNotMatch(empty, /Open the llama\.cpp library/);
      const shot=await call('Page.captureScreenshot',{format:'png'});writeFileSync(join(screenshots,'prima-pin-empty-390.png'),Buffer.from(shot.data,'base64'));

      // Open the add dialog: it opens centered and prefilled from the recipe source (release).
      await call('Emulation.setDeviceMetricsOverride',{width:1440,height:1000,deviceScaleFactor:1,mobile:false});
      await navigate('prima');
      await click('+ Add version');
      await wait("document.querySelector('dialog').open");
      await assertDialogCentered('Add Prima version');
      assert.match(await evaluate("document.querySelector('dialog h2').textContent"), /Pin a Prima version/);
      const inputs = await evaluate("[...document.querySelectorAll('dialog input')].map(el => el.value)");
      assert.deepEqual(inputs.slice(0, 2), ['', primaRepo]);
      assert.equal(inputs[2], '0.0.1');
      assert.equal(await evaluate("document.querySelector('dialog select').value"), 'tag');
      // Resolve then save & set target.
      await click('Resolve revision');
      await wait("document.querySelector('.resolved-box')");
      assert.match(await evaluate("document.querySelector('.resolved-box').textContent"), new RegExp(prima.commit.slice(0, 8)));
      const shotPin=await call('Page.captureScreenshot',{format:'png'});writeFileSync(join(screenshots,'prima-pin-resolved.png'),Buffer.from(shotPin.data,'base64'));
      await click('Save version & set target');
      await wait("!document.querySelector('dialog').open && document.body.textContent.includes('Pinned source')");
      assert.deepEqual(commands.map(c => c.path), ['/v2/node/runtimes/resolve', '/v2/node/runtimes', '/v2/cluster/runtime-target']);
      assert.deepEqual([commands[0].value.engine, commands[0].value.repository, commands[0].value.ref, commands[0].value.ref_type], ['primacpp', primaRepo, '0.0.1', 'tag']);
      assert.equal(commands[2].value.runtime_id, prima.runtime_id);
      assert.equal(commands[2].value.expected_revision, 0);
      const pinnedText = await evaluate('document.body.innerText');
      assert.doesNotMatch(pinnedText, /No pinned Prima source/);
      assert.match(pinnedText, /Pinned by Kluster's build recipe/);
      assert.match(pinnedText, new RegExp(primaRepo.replaceAll('.', '\\.')));
      assert.match(pinnedText, /1 pinned source/i);
      assert.equal(await evaluate("[...document.querySelectorAll('.runtime-badge')].some(b => b.textContent.trim() === 'Pinned source')"), true);
      // Targeted version is protected from removal.
      assert.equal(await evaluate("document.querySelector('.version-row .runtime-remove').disabled"), true);

      // Add a second Prima version (branch) without changing target, then inspect, view logs, and remove it.
      await click('+ Add version');
      await wait("document.querySelector('dialog').open");
      await evaluate(`(() => {
        const inputs = [...document.querySelectorAll('dialog input')];
        inputs[0].value = 'Vulkan ring tuning';
        inputs[0].dispatchEvent(new Event('input', { bubbles: true }));
        const sel = document.querySelector('dialog .ref-grid select');
        sel.value = 'branch';
        sel.dispatchEvent(new Event('change', { bubbles: true }));
        inputs[2].value = 'codex/ring-tuning';
        inputs[2].dispatchEvent(new Event('input', { bubbles: true }));
      })()`);
      await click('Resolve revision');
      await wait("document.querySelector('.resolved-box')");
      assert.match(await evaluate("document.querySelector('.resolved-box').textContent"), new RegExp(experimental.commit.slice(0, 8)));
      await click('Save version');
      await wait("!document.querySelector('dialog').open && document.querySelectorAll('.version-row').length === 2");
      assert.match(await evaluate('document.body.innerText'), /2 pinned sources/i);

      // Inspect version details modal and verify centering.
      await evaluate("[...document.querySelectorAll('.version-name .text-button')].find(b => b.textContent.trim() === 'Vulkan ring tuning').click()");
      await wait("document.querySelector('dialog').open");
      await assertDialogCentered('Version details');
      assert.match(await evaluate("document.querySelector('dialog').textContent"), /codex\/ring-tuning/);
      await click('Close');
      await wait("!document.querySelector('dialog').open");

      // Open Build log modal and verify centering.
      await click('Logs');
      await wait("document.querySelector('dialog').open && document.querySelector('.log-console')?.value.includes('Verified Prima capabilities')");
      await assertDialogCentered('Build log');
      await click('Close');
      await wait("!document.querySelector('dialog').open");

      // Remove the unprotected second version and verify centering of Remove modal.
      await evaluate("document.querySelector('[aria-label=\"Remove Vulkan ring tuning\"]').click()");
      await wait("document.querySelector('dialog').open");
      await assertDialogCentered('Remove version');
      assert.match(await evaluate("document.querySelector('dialog h2').textContent"), /Remove version/);
      await click('Remove local version');
      await wait("!document.querySelector('dialog').open && document.querySelectorAll('.version-row').length === 1");
      assert.equal(commands.at(-1).method, 'DELETE');
      assert.equal(commands.at(-1).value.runtime_id, experimental.runtime_id);

      const shotDone=await call('Page.captureScreenshot',{format:'png'});writeFileSync(join(screenshots,'prima-pin-pinned.png'),Buffer.from(shotDone.data,'base64'));
    } finally {
      socket?.close(); browser?.kill();
      if(browser && browser.exitCode===null)await new Promise(r=>browser.once('exit',r));
      await vite?.close();if(server)await new Promise(r=>server.close(r));
      removeBrowserProfile(profile);
    }
  });

test('Prima settings swaps the runtime section on the engine mode and keeps llama.cpp intact',
  { skip: !chrome && 'Chrome required for Prima settings browser check' }, async () => {
    const primaRepo = 'https://github.com/berkkarahan/prima.cpp.git';
    const prima = {
      name: 'Prima.cpp', engine: 'primacpp', repository: primaRepo, ref: 'codex/kluster-integration',
      ref_type: 'branch', commit: '1'.repeat(40), runtime_id: '1'.repeat(64), recipe_digest: '2'.repeat(64)
    };
    const gates = ['vulkan_gpu_inference', 'api_parity', 'platform_backend_parity',
      'model_and_placement_parity', 'signed_rank_lifecycle', 'hardware_performance_gate'];
    const build = { runtime_id: prima.runtime_id, commit: prima.commit, build_id: 'fixture', backend: 'vulkan', size_bytes: 700 * 1024 ** 2 };
    const job = { job_id: '00000000-0000-4000-8000-000000000001', runtime_id: prima.runtime_id, phase: 'building', backend: 'vulkan' };
    const library = {
      versions: [{ version: prima, build, inference_supported: false, qualification_errors: gates }],
      jobs: [job], selected: null,
      bootstrap: { repository: 'https://github.com/ggml-org/llama.cpp.git', ref: 'v0.5.0' },
      target: { version: prima, revision: 5, cluster_binding_id: 'fixture', master_node_id: 'master' }, running: null,
      engine_sources: [{ engine: 'primacpp', repository: primaRepo, ref: prima.ref, ref_type: 'commit', release: '0.0.1', release_commit: prima.commit, qualification_errors: gates }],
      nodes: [
        { node_id: 'master', name: 'control-box', address: '10.10.10.1', role: 'master', capability: 1, phase: 'prepared', prepared: build },
        { node_id: 'worker', name: 'gpu-box', address: '10.10.10.2', role: 'worker', capability: 1, phase: 'building', job }
      ]
    };
    let engine = 'primacpp';
    const status = () => ({ role: 'master', node_id: 'master', members: {}, process_pid: 99,
      process_role: 'master', inference_up: false, engine, runtimes: library, capacity: { backend: 'vulkan' },
      accel_backend: 'vulkan', software: { git_head: 'fixture', llama_release: 'v0.5.0' } });
    const profile = mkdtempSync(join(tmpdir(), 'kllm-prima-settings-'));
    const screenshots = resolve('../artifacts/prima-settings');
    mkdirSync(screenshots, { recursive: true });
    let vite, server, browser, socket;
    try {
      vite = await createServer({server: {middlewareMode: true, hmr: false, ws: false, watch: null}, appType: 'custom'});
      server = httpServer((req, res) => {
        const url = new URL(req.url, 'http://fixture'), path = url.pathname;
        const json = (value, code = 200) => res.writeHead(code, {'Content-Type': 'application/json'}).end(JSON.stringify(value));
        if (!path.startsWith('/v2/')) return vite.middlewares(req, res, () => res.writeHead(404).end());
        if (req.method !== 'GET') return json({ok: true});
        if (path === '/v2/node/runtimes') return json(library);
        if (path === '/v2/credentials/api-token') return json({api_token: 'fixture-token'});
        if (path === '/v2/node') return json(status());
        if (path === '/v2/node/home') return json({home: '/fixture', models: '/fixture/models', logs: '/fixture/logs'});
        if (path === '/v2/node/config') return json({device: 'gpu', gpu_ids: [0], ram_percent: 80, vram_percent: 80, accel_backend: 'vulkan', engine});
        if (path === '/v2/node/devices') return json({devices: [], backend: 'vulkan', installed_backends: ['vulkan'], allowed_backends: ['vulkan']});
        if (path === '/v2/node/prima-devices') return json({available: true,
          features: ['ring_tcp', 'explicit_devices_v1'], backends: ['vulkan'],
          devices: [{backend: 'vulkan', name: 'Vulkan0', description: 'NVIDIA GeForce RTX 3090',
            device_identity: 'a'.repeat(32), driver_identity: 'f'.repeat(16), physical_index: 0}]});
        if (path === '/v2/models') return json({data: []});
        if (path === '/v2/events') {
          const area = data => ({revision: 1, sampled_at: new Date().toISOString(), availability: 'available', coverage: 'local', upstream_last_success_at: null, error: null, data});
          const areas = {status: area(status()), usage: area({nodes: {}, relationships: [], recording: {recording: 'stopped'}}), load: area({current: null, last_terminal: null}), download: area({current: null, last_terminal: null})};
          const topics = url.searchParams.get('topics')?.split(',') ?? Object.keys(areas);
          return json({version: 1, transport: 'websocket', epoch: '00000000-0000-4000-8000-000000000002', server_time: new Date().toISOString(),
            areas: Object.fromEntries(topics.filter(t => t in areas).map(t => [t, areas[t]])), logs: null});
        }
        return json({});
      });
      await new Promise(r => server.listen(0, '127.0.0.1', r));
      const base = `http://127.0.0.1:${server.address().port}`;
      browser = spawn(chrome, ['--headless=new', '--no-sandbox', '--disable-gpu', '--remote-debugging-port=0', `--user-data-dir=${profile}`, 'about:blank'], {stdio: 'ignore'});
      for (let i=0; i<100 && !existsSync(join(profile, 'DevToolsActivePort')); i++) await pause(100);
      const port = readFileSync(join(profile, 'DevToolsActivePort'), 'utf8').split('\n')[0];
      const tab = (await (await fetch(`http://127.0.0.1:${port}/json/list`)).json()).find(t => t.type === 'page');
      socket = new WebSocket(tab.webSocketDebuggerUrl);
      await new Promise(r => socket.onopen = r);
      let id=0; const pending = new Map();
      socket.onmessage = ({data}) => {const message=JSON.parse(data); pending.get(message.id)?.(message); pending.delete(message.id);};
      async function call(method, params={}) {
        const request = ++id;
        const result = new Promise((resolve, reject) => {const timer=setTimeout(()=>reject(new Error(method)), 15000);pending.set(request,m=>{clearTimeout(timer);resolve(m);});});
        socket.send(JSON.stringify({id:request,method,params}));
        const message=await result; assert.equal(message.error,undefined);return message.result;
      }
      async function evaluate(expression) {const r=await call('Runtime.evaluate',{expression,returnByValue:true,awaitPromise:true});assert.equal(r.exceptionDetails,undefined,JSON.stringify(r.exceptionDetails));return r.result.value;}
      async function wait(expression) {for(let i=0;i<100;i++){if(await evaluate(`Boolean(${expression})`))return;await pause(100);}assert.fail(`Timeout: ${expression}; ${await evaluate('document.body.innerText')}`);}
      async function navigate(section) {await call('Page.navigate',{url:`${base}/settings?section=${section}`});await wait("document.querySelector('.settings-nav')");await evaluate('document.fonts.ready');await pause(150);}

      // Prima mode: the runtime section swaps and the Prima panels render honestly.
      for (const width of [390, 768, 1440]) {
        await call('Emulation.setDeviceMetricsOverride',{width,height:1000,deviceScaleFactor:1,mobile:false});
        await navigate('prima');
        assert.ok(await evaluate('document.documentElement.scrollWidth <= innerWidth + 1'), `Prima tab overflows ${width}px`);
        const text = await evaluate('document.body.innerText');
        assert.match(text, /Prima is not qualified for inference/);
        assert.match(text, /0 of 6 passed/i);
        assert.match(text, /Runtime identity/);
        assert.match(text, /Qualification gates/);
        assert.match(text, /Build & run/);
        assert.match(text, /control-box/);
        assert.match(text, /10\.10\.10\.2/);
        assert.match(text, /Blocked by qualification/i);
        assert.doesNotMatch(text, /Pin the source\. Prepare every node\./);
        assert.equal(await evaluate("[...document.querySelectorAll('.settings-nav button')].some(b => b.textContent.trim() === 'Prima' && b.classList.contains('active'))"), true);
        assert.equal(await evaluate("Boolean(document.querySelector('.cap-wrap'))"), true);
        assert.match(text, /NVIDIA GeForce RTX 3090/);
        assert.match(text, /aaaaaaaaaaaa/i);
        assert.match(text, /probed device/i);
        assert.match(text, /Install 0\.0\.1 and set target/);
        assert.match(text, /Prima\.cpp 0\.0\.1/);
        const shot=await call('Page.captureScreenshot',{format:'png'});writeFileSync(join(screenshots,`prima-${width}.png`),Buffer.from(shot.data,'base64'));
      }

      // Deep link compatibility: ?section=llama lands on the same Prima section in primacpp mode.
      await navigate('llama');
      assert.match(await evaluate('document.body.innerText'), /Qualification gates/);

      // llama.cpp mode: the engine off leaves the existing UI byte-for-byte in place.
      engine = 'llamacpp';
      await navigate('llama');
      const llamaText = await evaluate('document.body.innerText');
      assert.match(llamaText, /Pin the source\. Prepare every node\./);
      assert.doesNotMatch(llamaText, /Prima is not qualified for inference/);
      assert.equal(await evaluate("[...document.querySelectorAll('.settings-nav button')].some(b => b.textContent.trim() === 'llama.cpp')"), true);
      const shot=await call('Page.captureScreenshot',{format:'png'});writeFileSync(join(screenshots,'llama-1440.png'),Buffer.from(shot.data,'base64'));
    } finally {
      socket?.close(); browser?.kill();
      if(browser && browser.exitCode===null)await new Promise(r=>browser.once('exit',r));
      await vite?.close();if(server)await new Promise(r=>server.close(r));
      removeBrowserProfile(profile);
    }
  });

test('a Prima pin saved under an earlier recipe is surfaced as stale and recovered by re-resolving',
  { skip: !chrome && 'Chrome required for Prima stale-pin browser check' }, async () => {
    const primaRepo = 'https://github.com/berkkarahan/prima.cpp.git';
    const staleDigest = '2'.repeat(64);
    // The saved pin: the source commit the recipe still names, but an earlier
    // recipe digest — it can never be built again.
    const stale = {
      name: 'Prima.cpp 0.0.1', engine: 'primacpp', repository: primaRepo, ref: '0.0.1',
      ref_type: 'tag', commit: '1'.repeat(40), runtime_id: '5'.repeat(64), recipe_digest: staleDigest
    };
    const currentDigest = '9'.repeat(64);
    const currentId = '6'.repeat(64);
    const staleBuild = { runtime_id: stale.runtime_id, commit: stale.commit, build_id: 'fixture',
      backend: 'vulkan', recipe_digest: staleDigest, transport: 'prima-ring-tcp', size_bytes: 512 * 1024 ** 2 };
    // The mismatch is the only blocking reason: the six gate ids still pass, so
    // the panel must not render an empty reason list.
    const library = {
      versions: [{ version: stale, build: staleBuild, inference_supported: false, qualification_errors: ['qualification_identity_mismatch'] }],
      jobs: [], selected: null,
      bootstrap: { repository: 'https://github.com/ggml-org/llama.cpp.git', ref: 'v0.5.0' },
      target: { version: stale, revision: 33, cluster_binding_id: 'fixture', master_node_id: 'master' }, running: null,
      engine_sources: [{ engine: 'primacpp', repository: primaRepo, ref: stale.commit, ref_type: 'commit',
        release: '0.0.1', release_commit: stale.commit, recipe_digest: currentDigest, qualification_errors: [] }],
      nodes: [{ node_id: 'master', name: 'control-box', address: '10.10.10.1', role: 'master', capability: 1, phase: 'failed' }]
    };
    const status = () => ({ role: 'master', node_id: 'master', members: {}, process_pid: 99,
      process_role: 'master', inference_up: false, engine: 'primacpp', runtimes: library, capacity: { backend: 'vulkan' },
      accel_backend: 'vulkan', software: { git_head: 'fixture', llama_release: 'v0.5.0' } });
    const commands = [];
    const profile = mkdtempSync(join(tmpdir(), 'kllm-prima-stale-'));
    const screenshots = resolve('../artifacts/prima-settings');
    mkdirSync(screenshots, { recursive: true });
    let vite, server, browser, socket;
    try {
      vite = await createServer({server: {middlewareMode: true, hmr: false, ws: false, watch: null}, appType: 'custom'});
      server = httpServer(async (req, res) => {
        const url = new URL(req.url, 'http://fixture'), path = url.pathname;
        const json = (value, code = 200) => res.writeHead(code, {'Content-Type': 'application/json'}).end(JSON.stringify(value));
        if (!path.startsWith('/v2/')) return vite.middlewares(req, res, () => res.writeHead(404).end());
        if (req.method !== 'GET') {
          let raw = ''; for await (const chunk of req) raw += chunk;
          const value = raw ? JSON.parse(raw) : {};
          commands.push({path, value});
          if (path === '/v2/node/runtimes/resolve') return json({...value, commit: stale.commit, recipe_digest: currentDigest, runtime_id: currentId});
          if (path === '/v2/node/runtimes') library.versions.push({version: value, build: null, inference_supported: true, qualification_errors: []});
          if (path === '/v2/cluster/runtime-target') {
            const version = library.versions.find(entry => entry.version.runtime_id === value.runtime_id)?.version ?? value;
            library.target = {version, revision: value.expected_revision + 1, cluster_binding_id: 'fixture', master_node_id: 'master'};
          }
          return json({ok: true});
        }
        if (path === '/v2/node/runtimes') return json(library);
        if (path === '/v2/credentials/api-token') return json({api_token: 'fixture-token'});
        if (path === '/v2/node') return json(status());
        if (path === '/v2/node/home') return json({home: '/fixture', models: '/fixture/models', logs: '/fixture/logs'});
        if (path === '/v2/node/config') return json({device: 'gpu', gpu_ids: [0], ram_percent: 80, vram_percent: 80, accel_backend: 'vulkan', engine: 'primacpp'});
        if (path === '/v2/node/devices') return json({devices: [], backend: 'vulkan', installed_backends: ['vulkan'], allowed_backends: ['vulkan']});
        if (path === '/v2/node/prima-devices') return json({available: false, features: [], backends: [], devices: []});
        if (path === '/v2/models') return json({data: []});
        if (path === '/v2/events') {
          const area = data => ({revision: 1, sampled_at: new Date().toISOString(), availability: 'available', coverage: 'local', upstream_last_success_at: null, error: null, data});
          const areas = {status: area(status()), usage: area({nodes: {}, relationships: [], recording: {recording: 'stopped'}}), load: area({current: null, last_terminal: null}), download: area({current: null, last_terminal: null})};
          const topics = url.searchParams.get('topics')?.split(',') ?? Object.keys(areas);
          return json({version: 1, transport: 'websocket', epoch: '00000000-0000-4000-8000-000000000004', server_time: new Date().toISOString(),
            areas: Object.fromEntries(topics.filter(t => t in areas).map(t => [t, areas[t]])), logs: null});
        }
        return json({});
      });
      await new Promise(r => server.listen(0, '127.0.0.1', r));
      const base = `http://127.0.0.1:${server.address().port}`;
      browser = spawn(chrome, ['--headless=new', '--no-sandbox', '--disable-gpu', '--remote-debugging-port=0', `--user-data-dir=${profile}`, 'about:blank'], {stdio: 'ignore'});
      for (let i=0; i<100 && !existsSync(join(profile, 'DevToolsActivePort')); i++) await pause(100);
      const port = readFileSync(join(profile, 'DevToolsActivePort'), 'utf8').split('\n')[0];
      const tab = (await (await fetch(`http://127.0.0.1:${port}/json/list`)).json()).find(t => t.type === 'page');
      socket = new WebSocket(tab.webSocketDebuggerUrl);
      await new Promise(r => socket.onopen = r);
      let id=0; const pending = new Map();
      socket.onmessage = ({data}) => {const message=JSON.parse(data); pending.get(message.id)?.(message); pending.delete(message.id);};
      async function call(method, params={}) {
        const request = ++id;
        const result = new Promise((resolve, reject) => {const timer=setTimeout(()=>reject(new Error(method)), 15000);pending.set(request,m=>{clearTimeout(timer);resolve(m);});});
        socket.send(JSON.stringify({id:request,method,params}));
        const message=await result; assert.equal(message.error,undefined);return message.result;
      }
      async function evaluate(expression) {const r=await call('Runtime.evaluate',{expression,returnByValue:true,awaitPromise:true});assert.equal(r.exceptionDetails,undefined,JSON.stringify(r.exceptionDetails));return r.result.value;}
      async function wait(expression) {for(let i=0;i<100;i++){if(await evaluate(`Boolean(${expression})`))return;await pause(100);}assert.fail(`Timeout: ${expression}; ${await evaluate('document.body.innerText')}`);}
      async function click(text) {await evaluate(`[...document.querySelectorAll('button')].find(b=>b.textContent.trim()===${JSON.stringify(text)} && b.getClientRects().length).click()`);}
      async function navigate(section) {await call('Page.navigate',{url:`${base}/settings?section=${section}`});await wait("document.querySelector('.settings-nav')");await evaluate('document.fonts.ready');await pause(150);}

      // Stale state: the saved pin belongs to an earlier recipe and says so,
      // including the identity-mismatch reason no gate label covers.
      await call('Emulation.setDeviceMetricsOverride',{width:1440,height:1100,deviceScaleFactor:1,mobile:false});
      await navigate('prima');
      const staleText = await evaluate('document.body.innerText');
      assert.match(staleText, /build stale · recipe changed/i);
      assert.match(staleText, /The saved pin is stale/);
      assert.match(staleText, /blocked by stale build/i);
      assert.match(staleText, /Inference remains blocked while qualification is incomplete: qualification identity mismatch\./);
      const shot=await call('Page.captureScreenshot',{format:'png'});writeFileSync(join(screenshots,'prima-stale-1440.png'),Buffer.from(shot.data,'base64'));

      // Recover: the install action re-resolves the release against the current
      // recipe instead of reusing the saved (stale) identity; past builds stay.
      assert.equal(await evaluate("[...document.querySelectorAll('button')].find(b => b.textContent.trim() === 'Install 0.0.1 and set target').disabled"), false);
      await click('Install 0.0.1 and set target');
      await wait("!document.body.innerText.includes('The saved pin is stale')");
      assert.deepEqual(commands.map(c => c.path), ['/v2/node/runtimes/resolve', '/v2/node/runtimes', '/v2/cluster/runtime-target']);
      assert.deepEqual([commands[0].value.engine, commands[0].value.ref, commands[0].value.ref_type], ['primacpp', '0.0.1', 'tag']);
      assert.equal(commands[1].value.runtime_id, currentId);
      assert.equal(commands[2].value.runtime_id, currentId);
      assert.equal(commands[2].value.expected_revision, 33);
      const recovered = await evaluate('document.body.innerText');
      assert.match(recovered, /build required/i);
      assert.doesNotMatch(recovered, /build stale/i);
      assert.equal(library.versions.length, 2);
    } finally {
      socket?.close(); browser?.kill();
      if(browser && browser.exitCode===null)await new Promise(r=>browser.once('exit',r));
      await vite?.close();if(server)await new Promise(r=>server.close(r));
      removeBrowserProfile(profile);
    }
  });
