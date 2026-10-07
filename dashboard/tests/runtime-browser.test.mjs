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

test('llama.cpp settings resolves immutable forks, synchronizes targets, and fits master/worker screens',
  { skip: !chrome && 'Chrome required for runtime settings browser check' }, async () => {
    const upstream = 'https://github.com/ggml-org/llama.cpp.git';
    const a = {name: 'v0.5.0', repository: upstream, ref: 'v0.5.0', ref_type: 'tag', commit: 'a'.repeat(40), runtime_id: 'a'.repeat(64)};
    const b = {name: 'RPC tuning', repository: 'https://github.com/example/llama.cpp.git', ref: 'rpc-tuning', ref_type: 'branch', commit: 'b'.repeat(40), runtime_id: 'b'.repeat(64)};
    const built = v => ({runtime_id: v.runtime_id, commit: v.commit, build_id: 'fixture', backend: 'metal+vulkan', size_bytes: 700 * 1024 ** 2});
    let role = 'master', revision = 1;
    const job = {job_id: '00000000-0000-4000-8000-000000000001', runtime_id: b.runtime_id, phase: 'failed', backend: 'cuda', error: 'Fixture build failure'};
    const library = {versions: [{version: a, build: built(a)}, {version: b, build: null}], jobs: [job], selected: null, bootstrap: {repository: upstream, ref: 'v0.5.0'},
      target: {version: b, revision: 5, cluster_binding_id: 'fixture', master_node_id: 'master'}, running: {...a, ...built(a)},
      nodes: [{node_id: 'master', role: 'master', capability: 1, phase: 'prepared', prepared: built(a), running: built(a)},
        {node_id: 'worker', role: 'worker', capability: 1, phase: 'failed', job, error: job.error},
        {node_id: 'offline', role: 'worker', capability: 1, phase: 'offline'}]};
    const commands = [];
    const status = () => ({role, node_id: role === 'worker' ? 'worker' : 'master', members: {}, process_pid: 99,
      process_role: role, inference_up: true, runtimes: library, capacity: {backend: 'metal'}, accel_backend: 'metal',
      software: {git_head: 'fixture', llama_release: 'v0.5.0'}});
    const profile = mkdtempSync(join(tmpdir(), 'kllm-runtime-browser-'));
    const screenshots = resolve('../artifacts/llamacpp-settings');
    mkdirSync(screenshots, {recursive: true});
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
          if (path.endsWith('/resolve')) return json({...value, commit: 'c'.repeat(40), runtime_id: 'c'.repeat(64)});
          if (path === '/v2/node/runtimes') library.versions.push({version: value, build: null});
          if (path === '/v2/cluster/runtime-target') library.target = {...library.target, version: library.versions.find(v => v.version.runtime_id === value.runtime_id).version, revision: 6};
          revision++;
          return json({ok: true});
        }
        if (path === '/v2/node/runtimes') return json(library);
        if (path.endsWith('/logs')) return json({text: 'Fixture: compiler failed; retry after fixing toolchain.'});
        if (path === '/v2/credentials/api-token') return json({api_token: 'fixture-token'});
        if (path === '/v2/node') return json(status());
        if (path === '/v2/node/home') return json({home: '/fixture', models: '/fixture/models', logs: '/fixture/logs'});
        if (path === '/v2/node/config') return json({device: 'gpu', gpu_ids: [0], ram_percent: 80, vram_percent: 80});
        if (path === '/v2/node/devices') return json({devices: [], backend: 'metal', installed_backends: ['metal', 'vulkan'], allowed_backends: ['metal', 'vulkan']});
        if (path === '/v2/models') return json({data: []});
        if (path === '/v2/events') {
          const area = data => ({revision, sampled_at: new Date().toISOString(), availability: 'available', coverage: 'local', upstream_last_success_at: null, error: null, data});
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
      async function click(text) {await evaluate(`[...document.querySelectorAll('button')].find(b=>b.textContent.trim()===${JSON.stringify(text)} && b.getClientRects().length).click()`);}
      async function navigate() {await call('Page.navigate',{url:base+'/settings?section=llama'});await wait("document.querySelector('#target-heading') && document.querySelector('.version-row')");await evaluate('document.fonts.ready');await pause(150);}
      for(const width of [390,768,1440]) {
        await call('Emulation.setDeviceMetricsOverride',{width,height:1000,deviceScaleFactor:1,mobile:false});
        await navigate();
        assert.ok(await evaluate('document.documentElement.scrollWidth <= innerWidth + 1'), `Runtime tab overflows ${width}px`);
        assert.match(await evaluate('document.body.innerText'),/Pending target/);
        const shot=await call('Page.captureScreenshot',{format:'png'});writeFileSync(join(screenshots,`master-${width}.png`),Buffer.from(shot.data,'base64'));
      }
      await click('+ Add version');
      await wait("document.querySelector('dialog').open");
      await evaluate("[...document.querySelectorAll('dialog input')].forEach((el,i)=>{el.value=['Saved fork','https://github.com/example/other.git','v1'][i];el.dispatchEvent(new Event('input',{bubbles:true}));})");
      await click('Resolve revision');
      await wait("document.querySelector('.resolved-box')");
      assert.match(await evaluate("document.querySelector('.resolved-box').textContent"), /cccccccc/);
      await click('Save version');
      await wait("!document.querySelector('dialog').open && document.querySelectorAll('.version-row').length===3");
      assert.equal(commands.find(c=>c.path==='/v2/node/runtimes').value.commit,'c'.repeat(40));
      await click('Set target & sync');
      await wait("document.querySelector('.pending-note').textContent.includes('Saved fork')");
      assert.equal(commands.find(c=>c.path==='/v2/cluster/runtime-target').value.expected_revision,5);
      assert.match(await evaluate("document.querySelector('.running-value').textContent"),/v0.5.0/);
      role='worker';revision++;
      await navigate();
      assert.equal(await evaluate("document.querySelector('[aria-label=\"Target llama.cpp version\"]').disabled"),true);
      assert.match(await evaluate('document.body.innerText'),/Managed by master/);
      assert.equal(await evaluate("[...document.querySelectorAll('button')].some(b=>b.textContent.trim()==='Set target & sync')"),false);
      const shot=await call('Page.captureScreenshot',{format:'png'});writeFileSync(join(screenshots,'worker-1440.png'),Buffer.from(shot.data,'base64'));
    } finally {
      socket?.close(); browser?.kill();
      if(browser && browser.exitCode===null)await new Promise(r=>browser.once('exit',r));
      await vite?.close();if(server)await new Promise(r=>server.close(r));
      removeBrowserProfile(profile);
    }
  });

test('master installs the bootstrap pin, reuses saved identity, and leaves active inference untouched',
  { skip: !chrome && 'Chrome required for runtime settings browser check' }, async () => {
    const upstream = 'https://github.com/ggml-org/llama.cpp.git';
    const bootstrap = {repository: upstream, ref: 'v0.5.0'};
    const library = {versions: [], bootstrap, jobs: [], selected: null, target: null,
      running: {name: 'active run', repository: upstream, ref: 'old', ref_type: 'tag', commit: 'a'.repeat(40), runtime_id: 'a'.repeat(64)},
      nodes: [{node_id: 'master', role: 'master', capability: 1, phase: 'prepared'}]};
    const commands = [];
    const profile = mkdtempSync(join(tmpdir(), 'kllm-runtime-default-'));
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
          if (path.endsWith('/resolve')) return json({...value, commit: 'c'.repeat(40), runtime_id: 'c'.repeat(64)});
          if (path === '/v2/node/runtimes') library.versions.push({version: value, build: null});
          if (path === '/v2/cluster/runtime-target') library.target = {version: library.versions.find(v => v.version.runtime_id === value.runtime_id).version, revision: 1, master_node_id: 'master'};
          return json({ok: true});
        }
        if (path === '/v2/node/runtimes') return json(library);
        if (path === '/v2/credentials/api-token') return json({api_token: 'fixture-token'});
        if (path === '/v2/node') return json({role: 'master', node_id: 'master', members: {}, runtimes: library, accel_backend: 'metal'});
        if (path === '/v2/node/home') return json({home: '/fixture', models: '/fixture/models', logs: '/fixture/logs'});
        if (path === '/v2/node/config') return json({device: 'gpu', gpu_ids: [0], ram_percent: 80, vram_percent: 80});
        if (path === '/v2/node/devices') return json({devices: [], backend: 'metal', installed_backends: ['metal'], allowed_backends: ['metal']});
        if (path === '/v2/models') return json({data: []});
        if (path === '/v2/events') {
          const area = data => ({revision: 1, sampled_at: new Date().toISOString(), availability: 'available', coverage: 'local', upstream_last_success_at: null, error: null, data});
          const areas = {status: area({role: 'master', node_id: 'master', members: {}, runtimes: library}), usage: area({nodes: {}, relationships: [], recording: {recording: 'stopped'}}), load: area({current: null, last_terminal: null}), download: area({current: null, last_terminal: null})};
          const topics = url.searchParams.get('topics')?.split(',') ?? Object.keys(areas);
          return json({version: 1, transport: 'websocket', epoch: '00000000-0000-4000-8000-000000000002', server_time: new Date().toISOString(), areas: Object.fromEntries(topics.filter(t => t in areas).map(t => [t, areas[t]])), logs: null});
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
        const request=++id;
        const result=new Promise((resolve,reject)=>{const timer=setTimeout(()=>reject(new Error(method)),15000);pending.set(request,m=>{clearTimeout(timer);resolve(m);});});
        socket.send(JSON.stringify({id:request,method,params}));const message=await result;assert.equal(message.error,undefined);return message.result;
      }
      async function evaluate(expression) {const r=await call('Runtime.evaluate',{expression,returnByValue:true,awaitPromise:true});assert.equal(r.exceptionDetails,undefined);return r.result.value;}
      async function wait(expression) {for(let i=0;i<100;i++){if(await evaluate(`Boolean(${expression})`))return;await pause(100);}assert.fail(`Timeout ${expression}: ${await evaluate('document.body.innerText')}`);}
      await call('Page.navigate',{url:base+'/settings?section=llama'});
      const install = "[...document.querySelectorAll('button')].find(b=>b.textContent.trim()==='Install v0.5.0 and set target')";
      await wait(`${install} && !${install}.disabled`);
      await evaluate(`${install}.click()`);
      await wait("document.querySelector('.pending-note')?.textContent.includes('v0.5.0')");
      assert.deepEqual(commands.map(c=>c.path), ['/v2/node/runtimes/resolve','/v2/node/runtimes','/v2/cluster/runtime-target']);
      assert.deepEqual([commands[0].value.repository, commands[0].value.ref, commands[0].value.ref_type], [upstream, 'v0.5.0', 'tag']);
      assert.equal(commands[1].value.commit, 'c'.repeat(40));
      assert.equal(commands[2].value.runtime_id, 'c'.repeat(64));
      assert.equal(commands[2].value.expected_revision, 0);
      assert.match(await evaluate("document.querySelector('.running-value').textContent"), /active run/);
      const beforeReuse = commands.length;
      await evaluate("[...document.querySelectorAll('button')].find(b=>b.textContent.trim()==='Install v0.5.0 and set target').click()");
      await wait("document.querySelector('.pending-note')?.textContent.includes('v0.5.0')");
      assert.equal(commands.slice(beforeReuse).some(c=>c.path.endsWith('/resolve') || c.path==='/v2/node/runtimes'), false);
    } finally {
      socket?.close();browser?.kill();if(browser&&browser.exitCode===null)await new Promise(r=>browser.once('exit',r));
      await vite?.close();if(server)await new Promise(r=>server.close(r));removeBrowserProfile(profile);
    }
  });
