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
        assert.match(text, /Managed build/);
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
