(() => {
  'use strict';
  const STORAGE = 'kluster.llamacpp-settings.v1';
  const $ = (s, root = document) => root.querySelector(s);
  const $$ = (s, root = document) => [...root.querySelectorAll(s)];
  const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const paths = {
    cluster:'M12 3v6M5 15v-4h14v4M3 15h4v6H3zM10 3h4v6h-4zM17 15h4v6h-4z',
    cube:'m12 3 9 5v9l-9 5-9-5V8zM3 8l9 5 9-5M12 13v9M7.5 5.5l9 5',
    rocket:'M14 4c2-1 4-1 6-1 0 2 0 4-1 6l-7 7-5-5zM7 11H3l4-5h4M12 16v5l5-5v-4M5 15l-2 6 6-2M14 8h.01',
    activity:'M2 12h5l3-8 4 16 3-8h5',
    plug:'M8 3v5M16 3v5M6 8h12v5a6 6 0 0 1-12 0zM12 19v3',
    settings:'M12 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8M9 3h6l1 3 3 1 2 5-2 5-3 1-1 3H9l-1-3-3-1-2-5 2-5 3-1z',
    chevron:'m9 5 7 7-7 7', check:'m5 12 4 4L19 6', close:'m6 6 12 12M6 18 18 6', plus:'M12 5v14M5 12h14',
    info:'M12 16v-4M12 8h.01M22 12a10 10 0 1 1-20 0 10 10 0 0 1 20 0',
    shield:'m12 3 8 3v6c0 5-8 9-8 9s-8-4-8-9V6zM8 12l3 3 5-6',
    clock:'M12 7v5l3 2M22 12a10 10 0 1 1-20 0 10 10 0 0 1 20 0',
    code:'m8 7-5 5 5 5M16 7l5 5-5 5M14 4l-4 16',
    download:'M12 3v12m-5-5 5 5 5-5M4 16v5h16v-5',
    hammer:'m14 3 7 7-4 4-3-3L5 21l-3-3 10-10-3-3z',
    log:'M5 3h14v18H5zM8 7h8M8 11h8M8 15h5',
    trash:'M3 6h18M9 6V3h6v3M5 6l1 15h12l1-15M10 10v7M14 10v7',
    retry:'M20 7v5h-5M20 12a8 8 0 1 0-2 6',
    server:'M4 3h16v7H4zM4 14h16v7H4zM8 6.5h.01M8 17.5h.01M12 6.5h5M12 17.5h5',
    monitor:'M3 4h18v13H3zM8 21h8M12 17v4',
    warning:'m12 3 10 18H2zM12 9v5M12 17h.01',
    stop:'M6 6h12v12H6z', play:'m7 4 13 8-13 8z', lock:'M7 10V7a5 5 0 0 1 10 0v3M5 10h14v11H5zM12 14v3',
    branch:'M6 3v12a3 3 0 1 0 3 3M6 12h7a5 5 0 0 0 5-5M18 2a3 3 0 1 0 0 6 3 3 0 0 0 0-6',
    menu:'M4 6h16M4 12h16M4 18h16', copy:'M9 9h12v12H9zM15 9V3H3v12h6', arrow:'M4 12h16m-6-6 6 6-6 6', wifi:'M3 7a15 15 0 0 1 18 0M6 11a10 10 0 0 1 12 0M9 15a5 5 0 0 1 6 0M12 19h.01',
    offline:'M3 3l18 18M3 7c2-2 4-3 7-3M14 4c3 0 5 1 7 3M6 11c2-2 4-2 6-2M9 15c2-1 4-1 6 0M12 19h.01'
  };
  const icon = (name, small = false) => `<svg ${small?'class="icon-sm"':''} viewBox="0 0 24 24" aria-hidden="true"><path d="${paths[name] || paths.info}"/></svg>`;
  const badge = (text, tone = '') => `<span class="badge ${tone}">${esc(text)}</span>`;
  const btn = (text, action, symbol = '', cls = '', value = '', disabled = false, title = '') => `<button type="button" class="btn ${cls}" data-action="${action}" data-value="${esc(value)}" ${disabled?'disabled':''} ${title?`title="${esc(title)}"`:''}>${symbol?icon(symbol,true):''}${esc(text)}</button>`;
  const shaFor = text => {
    let h = 2166136261, result = '';
    for (let n = 0; n < 5; n++) {
      for (const c of `${text}:${n}`) {h ^= c.charCodeAt(0); h = Math.imul(h,16777619);}
      result += (h >>> 0).toString(16).padStart(8,'0');
    }
    return result;
  };
  function initial() {
    const versions = [
      {id:'upstream-050',name:'v0.5.0',kind:'upstream',url:'https://github.com/ggml-org/llama.cpp',refType:'tag',ref:'v0.5.0',sha:shaFor('https://github.com/ggml-org/llama.cpp:tag:v0.5.0'),supported:['Metal','CUDA','Vulkan','ROCm','CPU'],added:'Bundled example'},
      {id:'fork-rpc',name:'rpc-tuning',kind:'fork',url:'https://github.com/example-lab/llama.cpp',refType:'branch',ref:'rpc-tuning',sha:shaFor('https://github.com/example-lab/llama.cpp:branch:rpc-tuning'),supported:['Metal','CUDA','Vulkan','ROCm','CPU'],added:'Illustrative fork'},
      {id:'upstream-040',name:'v0.4.0',kind:'upstream',url:'https://github.com/ggml-org/llama.cpp',refType:'tag',ref:'v0.4.0',sha:shaFor('https://github.com/ggml-org/llama.cpp:tag:v0.4.0'),supported:['Metal','CUDA','Vulkan','ROCm','CPU'],added:'Archived example'}
    ];
    const nodes = [
      {id:'studio',name:'studio-master',role:'master',backend:'Metal',device:'Apple M2 Ultra',address:'192.168.10.10',online:true,selected:true,artifacts:{'upstream-050':{backend:'Metal',disk:684},'upstream-040':{backend:'Metal',disk:652}}},
      {id:'atlas',name:'atlas-01',role:'worker',backend:'CUDA',device:'NVIDIA RTX 4090',address:'192.168.10.11',online:true,selected:true,artifacts:{'upstream-050':{backend:'CUDA',disk:1148}}},
      {id:'forge',name:'forge-02',role:'worker',backend:'Vulkan',device:'AMD Radeon RX 7900 XTX',address:'192.168.10.12',online:true,selected:true,artifacts:{'upstream-050':{backend:'Vulkan',disk:812}}},
      {id:'reserve',name:'reserve-03',role:'worker',backend:'ROCm',device:'AMD Radeon PRO',address:'192.168.10.13',online:false,selected:false,artifacts:{}}
    ];
    return {schema:1,versions,nodes,target:'upstream-050',draft:'upstream-050',enforce:true,role:'master',activated:'upstream-050',run:{phase:'running',version:'upstream-050',nodes:['studio','atlas','forge']},logs:[{at:new Date().toISOString(),node:'studio',version:'upstream-050',level:'success',message:'Example cluster loaded. Three required nodes are running the same source revision with local backend builds.'}],scenario:'baseline'};
  }
  let storageOK = true, restored = false, S;
  try {
    const raw = localStorage.getItem(STORAGE), candidate = raw ? JSON.parse(raw) : null;
    if(candidate?.schema===1 && Array.isArray(candidate.versions) && Array.isArray(candidate.nodes) && candidate.versions.some(v=>v.id===candidate.target) && candidate.nodes.some(n=>n.role==='master') && candidate.run && Array.isArray(candidate.logs)) {S=candidate;restored=true;}
    else S=initial();
  } catch {S=initial();storageOK=false;}
  let section = 'llamacpp', scenarioOpen = false, sequence = 0, dialogContext = null, dialogGeneration = 0, openerId = '', menuOpen = false;
  const timers = new Set();
  const busyStages = ['downloading','building','verifying'];
  const V = id => S.versions.find(v=>v.id===id);
  const N = id => S.nodes.find(n=>n.id===id);
  const localNode = () => S.role==='master' ? S.nodes.find(n=>n.role==='master') : (N('atlas') || S.nodes.find(n=>n.role==='worker'));
  const busy = n => !!n.job && busyStages.includes(n.job.stage);
  const artifact = (n,id) => !!n.artifacts[id] && n.artifacts[id].backend===n.backend;
  const compatible = (n,id) => !!V(id)?.supported.includes(n.backend);
  const required = () => S.nodes.filter(n=>n.role==='master'||n.selected);
  const ready = n => n.online && compatible(n,S.target) && artifact(n,S.target);
  const blockers = () => required().filter(n=>!ready(n)).map(n=>({node:n,reason:!n.online?'Offline — reconnect or remove this worker from the next run.':!compatible(n,S.target)?`${n.backend} is incompatible with this target. Choose a compatible target or exclude this worker.`:n.job?.version===S.target&&n.job.stage==='failed'?'Build failed — inspect the log and retry.':busy(n)?`${n.job.stage[0].toUpperCase()+n.job.stage.slice(1)} the target revision.`:'Target is not prepared. Sync the cluster to continue.'}));
  const activeOn = n => S.run.phase!=='idle' && S.run.nodes.includes(n.id);
  const persist = () => {try {localStorage.setItem(STORAGE,JSON.stringify(S));}catch {storageOK=false;}};
  const later = (fn, ms) => {const id=setTimeout(()=>{timers.delete(id);fn();},ms);timers.add(id);return id;};
  const timestamp = iso => new Date(iso).toLocaleTimeString('en-GB',{hour12:false});
  function log(node,version,message,level='info') {S.logs.push({at:new Date().toISOString(),node,version,message,level});S.logs=S.logs.slice(-300);}
  function toast(message) {
    const region=$('#toasts'), el=document.createElement('div');el.className='toast';el.setAttribute('role','status');
    el.innerHTML=`${icon('check')}<span>${esc(message)}</span><button class="btn ghost" type="button" aria-label="Dismiss notification">${icon('close')}</button>`;
    el.querySelector('button').onclick=()=>el.remove();region.append(el);later(()=>el.remove(),6500);
  }
  function notify(message) {$('#announcer').textContent=message;}
  const short = id => V(id)?.sha.slice(0,8)||'—';
  const disk = mb => mb>=1024 ? `${(mb/1024).toFixed(2)} GiB` : `${mb} MiB`;
  function nodeState(n) {
    if(!n.online)return {label:'Offline',tone:'muted',symbol:'offline',help:n.selected?'Required for next run':'Not selected · does not block'};
    if(!compatible(n,S.target))return {label:'Incompatible',tone:'red',symbol:'warning',help:`${n.backend} is not supported by target`};
    if(busy(n))return {label:n.job.stage[0].toUpperCase()+n.job.stage.slice(1),tone:'blue',symbol:n.job.stage==='building'?'hammer':n.job.stage==='verifying'?'shield':'download',help:`${V(n.job.version)?.name||'Saved version'} · ${n.job.percent}%`,progress:n.job.percent};
    if(n.job?.version===S.target&&n.job.stage==='failed')return {label:'Failed',tone:'red',symbol:'warning',help:artifact(n,S.target)?'Rebuild failed · prior build usable':'Build failed · retry available'};
    if(ready(n)&&activeOn(n)&&S.run.version===S.target)return {label:S.run.phase==='stopping'?'Stopping':'Running',tone:S.run.phase==='stopping'?'amber':'green',symbol:S.run.phase==='stopping'?'clock':'activity',help:'Target revision is active'};
    if(ready(n))return {label:'Prepared',tone:'green',symbol:'check',help:activeOn(n)?'Waiting for current run to stop':'Ready for the next run'};
    return {label:'Needs sync',tone:'amber',symbol:'clock',help:'No local build for target'};
  }
  const stateMarkup = n => {const s=nodeState(n);return `<div class="node-state"><span class="state-label ${s.tone}">${icon(s.symbol,true)}${s.label}</span>${s.progress!==undefined?`<progress class="job-progress" value="${s.progress}" max="100" aria-label="${esc(n.name)} ${s.label} progress">${s.progress}%</progress>`:''}<span class="state-help">${esc(s.help)}</span></div>`;};
  function targetPanel() {
    const target=V(S.target), draft=V(S.draft)||target, running=V(S.run.version), dirty=draft.id!==target.id;
    const locked=S.role!=='master', anyBusy=S.nodes.some(busy), pending=S.run.phase!=='idle'&&S.run.version!==S.target;
    return `<section class="panel" aria-labelledby="target-heading"><div class="panel-head"><div><h3 id="target-heading">Cluster target</h3><p>A single source revision, built for each node.</p></div>${badge(locked?'Managed by master':'Enforced by master',locked?'':'green')}</div><div class="target-grid"><div class="target-picker"><div class="target-control"><div class="field"><label for="target-select">Target version ${dirty?'<span class="warn">· Unsaved selection</span>':''}</label><select id="target-select" class="control" ${locked||anyBusy?'disabled':''} aria-describedby="target-help">${S.versions.map(v=>`<option value="${esc(v.id)}" ${v.id===draft.id?'selected':''}>${esc(v.name)} · ${v.kind==='fork'?'Fork':'Upstream'}</option>`).join('')}</select></div>${btn('Set target & sync','sync','retry','primary','',locked||anyBusy,locked?'Only the master can set the cluster target':anyBusy?'Wait for active preparation jobs to finish':'Save this target and prepare connected nodes')}</div><div class="target-meta"><span class="od-row">${icon(draft.kind==='fork'?'branch':'code',true)}${esc(draft.refType)}: ${esc(draft.ref)}</span><span class="mono">${esc(draft.sha.slice(0,8))}</span>${badge('Immutable SHA')}</div><p id="target-help" class="target-note">${locked?`Target is assigned by <strong>studio-master</strong>. Local builds use ${esc(localNode().backend)}.`:anyBusy?'Preparing local builds. The saved target remains pinned while work is in progress.':dirty?'Choose Set target & sync to save this revision across the cluster.':`Saved target: ${esc(target.name)}. New and returning nodes prepare it automatically.`}</p></div><div class="running-box"><div class="running-top"><span class="small muted">${S.run.phase==='idle'?'Next run':'Running version'}</span>${badge(S.run.phase==='running'?'Run active':S.run.phase==='stopping'?'Stopping':'No active run',S.run.phase==='running'?'green':S.run.phase==='stopping'?'amber':'')}</div><div class="running-value">${S.run.phase==='idle'?(S.activated===S.target?esc(target.name):'Awaiting readiness'):esc(running?.name||'Unknown revision')}<small class="mono">${S.run.phase==='idle'?(S.activated===S.target?short(S.target):''):short(S.run.version)}</small></div><p class="run-caption">${pending?'The active run keeps its original binaries. Prepared builds switch only after it fully stops.':S.run.phase==='idle'?'The next run uses the saved target once required nodes are ready.':`${S.run.nodes.length} nodes on the same revision, each using its own backend.`}</p><div class="run-foot"><span class="micro muted od-row">${icon('info',true)}Simulated inference run</span>${S.run.phase==='idle'?btn('Start sample run','start-run','play','ghost','',locked||blockers().length>0,locked?'Managed by master':blockers().length?'Resolve the required-node blockers below':'Start with the prepared target'):btn(S.run.phase==='stopping'?'Stopping…':'Stop sample run','stop-run','stop','ghost','',locked||S.run.phase==='stopping',locked?'Managed by master':'Fully stop the example run')}</div></div></div><div class="policy-strip">${icon('shield')}<div class="od-field"><strong>One target. Local builds. Persistent enforcement.</strong><span>Joining and reconnecting workers inherit the saved revision. Backend builds may differ; source revisions must match.</span></div></div>${pending?`<div class="pending-note">${icon('clock')}<div><strong>Pending target: ${esc(target.name)} <span class="mono micro">${short(S.target)}</span></strong><p>Preparation runs beside ${esc(running?.name)}. No active process is replaced.</p></div></div>`:''}</section>`;
  }
  function protectedReason(id) {
    if(id===S.target)return 'This is the enforced cluster target.';
    if(S.run.phase!=='idle'&&id===S.run.version)return 'This version is used by the active run.';
    if(S.nodes.some(n=>busy(n)&&n.job.version===id))return 'A preparation job is using this version.';
    return '';
  }
  function versionLibrary() {
    const node=localNode(), worker=S.role!=='master';
    return `<section class="panel" aria-labelledby="library-heading"><div class="panel-head"><div><h3 id="library-heading">Saved versions <span class="muted small">/ ${S.versions.length}</span></h3><p>Keep known-good builds. Try a new revision without replacing them.</p></div>${btn('Add version','add-version','plus','ghost','',worker,'Only the master manages the version library')}</div><div class="library-head" aria-hidden="true"><span>Source / revision</span><span>Local build</span><span>Disk usage</span><span style="text-align:right">Actions</span></div>${S.versions.map(v=>{
      const built=artifact(node,v.id), job=busy(node)&&node.job.version===v.id, failed=node.job?.version===v.id&&node.job.stage==='failed', reason=protectedReason(v.id), canBuild=(!worker||v.id===S.target)&&!busy(node)&&node.online&&compatible(node,v.id);
      return `<article class="version-row"><div class="version-info"><div class="version-name"><button class="text-link" type="button" data-action="version-details" data-value="${esc(v.id)}">${esc(v.name)}</button>${v.id===S.target?badge('Target','green'):v.id===S.run.version&&S.run.phase!=='idle'?badge('Running','blue'):v.kind==='fork'?badge('Fork'):''}</div><div class="version-source">${v.kind==='fork'?'Fork · ':'Upstream · '}${esc(v.url.replace('https://github.com/',''))}</div><div class="version-ref"><span class="ref-chip">${esc(v.refType)}</span><span>${esc(v.sha.slice(0,8))}</span><span>· sample SHA</span></div></div><div class="version-backend"><span class="mobile-label">Local build</span><span class="od-row">${icon('cube',true)}${esc(node.backend)}</span><span class="state-label ${job?'blue':failed?'red':built?'green':'muted'}">${icon(job?'hammer':failed?'warning':built?'check':'clock',true)}${job?'Preparing':failed?'Failed':built?'Built':'Not built'}</span></div><div class="version-disk"><span class="mobile-label">Disk usage</span>${built?disk(node.artifacts[v.id].disk):'—'}</div><div class="version-actions">${btn(built?'Rebuild':'Build','build-version','hammer','ghost',v.id,!canBuild,worker&&v.id!==S.target?'Workers only prepare the assigned target':busy(node)?'A local build is already in progress':'Build for this node’s configured backend')}${btn('Logs','version-logs','log','ghost',v.id)}${btn('Select','select-version','','ghost',v.id,worker||S.draft===v.id||S.nodes.some(busy),worker?'Managed by master':S.draft===v.id?'Already selected in the target picker':'Choose this version in the target picker')}<button class="btn ghost icon-btn" type="button" data-action="remove-version" data-value="${esc(v.id)}" aria-label="Remove ${esc(v.name)}" title="${esc(worker?'Managed by master':reason||'Remove saved version')}" ${worker||reason?'disabled':''}>${icon('trash',true)}</button></div></article>`;
    }).join('')}<div class="library-foot"><span>Builds and disk usage shown for <strong>${esc(node.name)}</strong> · ${esc(node.backend)}</span><span>Revisions and sizes are sample data</span></div></section>`;
  }
  function readinessPanel() {
    const requiredNodes=required(), blocked=blockers(), readyCount=requiredNodes.filter(ready).length, preparing=S.nodes.filter(busy).length, offline=S.nodes.filter(n=>!n.online).length, worker=S.role!=='master';
    return `<section class="panel" aria-labelledby="readiness-heading"><div class="panel-head"><div><h3 id="readiness-heading">Node readiness</h3><p>Source revision is shared. Each node builds for its local backend.</p></div>${badge(`${requiredNodes.length} required for next run`)}</div><div class="readiness-summary"><div class="readiness-status ${blocked.length?'blocked':''}">${icon(blocked.length?'clock':'check')}<div class="od-field"><strong>${blocked.length?`${readyCount} of ${requiredNodes.length} required nodes ready`:'All required nodes are ready'}</strong><span class="micro muted">${S.run.phase!=='idle'&&S.run.version!==S.target?'Target activates after the current run fully stops.':blocked.length?'Required-node issues block the next run.':'Unselected offline workers do not block a run.'}</span></div></div><div class="readiness-counts"><span><strong>${readyCount}</strong> ready</span><span><strong>${preparing}</strong> preparing</span><span><strong>${offline}</strong> offline</span></div></div><div class="table-scroll" id="node-table-scroll" tabindex="0" role="region" aria-label="Node readiness table, scroll horizontally for all columns"><table class="node-table"><thead><tr><th scope="col">Use</th><th scope="col">Node</th><th scope="col">Backend</th><th scope="col">Running SHA</th><th scope="col">Target SHA</th><th scope="col">Preparation</th><th scope="col" style="text-align:right">Actions</th></tr></thead><tbody>${S.nodes.map(n=>`<tr><td><label class="checkbox"><input id="node-select-${n.id}" type="checkbox" data-node-select="${n.id}" ${n.selected||n.role==='master'?'checked':''} ${worker||n.role==='master'?'disabled':''} aria-label="${n.role==='master'?'Master is always required':`Use ${esc(n.name)} for the next run`}"></label></td><td><div class="node-name"><span class="node-mark">${icon(n.role==='master'?'monitor':'server',true)}</span><span class="od-field"><strong>${esc(n.name)}</strong><small>${n.role==='master'?'MASTER':'WORKER'}${n.id===localNode().id?' · THIS NODE':''}</small></span></div></td><td><span class="backend-label"><i class="backend-mark" aria-hidden="true"></i>${esc(n.backend)}</span></td><td><div class="revision-cell"><span>${activeOn(n)?short(S.run.version):'—'}</span><small>${activeOn(n)?esc(V(S.run.version)?.name||'Running'):'Not running'}</small></div></td><td><div class="revision-cell"><span>${short(S.target)}</span><small>${artifact(n,S.target)?'Prepared locally':'Awaiting local build'}</small></div></td><td>${stateMarkup(n)}</td><td><div class="table-actions">${n.job?.stage==='failed'&&n.online?btn('Retry','retry-node','retry','ghost',n.id,worker&&n.id!==localNode().id,'Retry this node’s failed build'):''}${btn('Logs','node-logs','log','ghost',n.id)}</div></td></tr>`).join('')}</tbody></table></div>${blocked.length?`<div class="panel-pad" style="padding-block:16px;border-top:1px solid var(--line)"><strong class="small">Before the next run</strong><ul class="blocker-list">${blocked.map(b=>`<li>${icon('warning')}<span><strong>${esc(b.node.name)}</strong> — ${esc(b.reason)}</span></li>`).join('')}</ul></div>`:''}<div class="table-note">${icon('info')}<span>The master is always required. Check workers to include them in the <strong>next</strong> run. Changing selection never changes an active run.</span></div></section>`;
  }
  const scenarios = [['baseline','Running cluster · all required nodes ready'],['fork','Add a public HTTPS fork'],['sync','Sync Metal, CUDA and Vulkan nodes'],['failure','Build failure and retry'],['prepare','Prepare a target while a run is active'],['worker','Worker view · managed by master'],['join','New worker joins the cluster'],['reconnect','Offline worker reconnects'],['incompatible','A selected backend is incompatible'],['offline','A selected worker is offline']];
  function scenarioPanel() {return `<details class="scenarios" id="scenario-panel" ${scenarioOpen?'open':''}><summary><span class="od-row">${icon('code',true)}Prototype scenarios <span class="badge">Local only</span></span></summary><div class="scenario-body"><p class="scenario-note">Explore the connected flows with example data. Loading a scenario replaces saved sample state; it never contacts a cluster.</p><div class="scenario-controls"><div class="field"><label for="scenario-select">Scenario</label><select id="scenario-select" class="control">${scenarios.map(([id,label])=>`<option value="${id}" ${S.scenario===id?'selected':''}>${label}</option>`).join('')}</select></div>${btn('Load scenario','load-scenario','play','ghost')}${btn('Reset sample','reset-sample','retry','ghost')}</div></div></details>`;}
  function otherSection() {
    const node=localNode();
    const rows={hardware:[['Device',node.device],['Local backend',node.backend],['Build strategy','Build the cluster source revision for this node']],network:[['Private address',node.address],['RPC port','50052 · private network only'],['Master','studio-master · 192.168.10.10']],tokens:[['Credentials','No credentials are stored or sent by this prototype'],['Fork sources','Public HTTPS repositories only']],software:[['Kluster','Example installation'],['llama.cpp','Version selection and preparation have a dedicated tab'],['Update policy','Prepare beside the active build']],paths:[['Saved sources','~/.kllm/llama/sources/<sha>'],['Local builds','~/.kllm/llama/builds/<sha>/<backend>'],['Build logs','~/.kllm/logs/llama/']]};
    const title={hardware:'Hardware',network:'Network',tokens:'Tokens',software:'Software',paths:'Paths'}[section];
    return `<section class="panel context-card"><div><h2>${title}</h2><p class="small" style="margin-top:8px">Local sample node · ${esc(node.name)}</p></div><div class="detail-grid">${rows[section].map(([label,value])=>`<div class="detail-row"><span>${label}</span><span>${esc(value)}</span></div>`).join('')}</div>${section==='paths'?'<p class="micro">Illustrative versioned storage layout, not a report of existing files.</p>':''}<div>${btn('Open llama.cpp settings','open-llama','arrow','ghost')}</div></section>`;
  }
  function render() {
    const focus=document.activeElement?.id, scroll=$('#node-table-scroll')?.scrollLeft||0, node=localNode();
    const master=S.role==='master';
    $('#workspace-name').textContent=node.name;$('#workspace-role').textContent=master?'Master node':'Worker node';$('#workspace-avatar').textContent=master?'SM':'A1';$('#nav-node-count').textContent=S.nodes.length;
    $('#shell-status').innerHTML=`${badge('Example data')}${badge(master?'Master':'Worker',master?'green':'')}<span class="detail-chip">${badge(master?'Local preview':'Status mirrored',master?'':'amber')}</span>`;
    $('#page-content').innerHTML=`<header class="page-heading"><div><p class="eyebrow">Settings</p><h1 id="page-title">Node settings</h1><p>Hardware, network, tokens, and software for your cluster.</p></div><div class="node-context">node ${esc(node.name)}<br>probed ${esc(node.backend.toLowerCase())}</div></header><nav class="settings-tabs" aria-label="Settings sections">${[['hardware','Hardware'],['network','Network'],['tokens','Tokens'],['software','Software'],['llamacpp','llama.cpp'],['paths','Paths']].map(([id,label])=>`<button type="button" id="tab-${id}" class="settings-tab ${section===id?'active':''}" data-action="settings-tab" data-value="${id}" ${section===id?'aria-current="page"':''}>${label}${id==='llamacpp'?'<span class="tab-mark" aria-hidden="true"></span>':''}</button>`).join('')}</nav>${section==='llamacpp'?`<div class="section-intro"><div><h2>llama.cpp</h2><p>Pin the source. Prepare every node. Keep the current run safe.</p></div><button class="text-link" type="button" data-action="how-it-works">How syncing works ${icon('info',true)}</button></div>${!master?`<div class="notice blue managed-banner">${icon('lock')}<div><strong>Managed by master</strong><p>studio-master assigns the cluster target. You can inspect local builds and retry preparation for ${esc(node.name)}.</p></div></div>`:''}<div class="section-stack">${targetPanel()}${versionLibrary()}${readinessPanel()}${scenarioPanel()}</div>`:otherSection()}<footer class="page-footer"><span>EXAMPLE CLUSTER · NO PRODUCTION CONNECTION</span><span>${storageOK?'Sample target and library saved in this browser':'Session only · browser storage unavailable'}</span></footer>`;
    if(scroll&&$('#node-table-scroll'))$('#node-table-scroll').scrollLeft=scroll;
    if(focus&&!$('#modal').open)document.getElementById(focus)?.focus({preventScroll:true});
    refreshLogs();
  }
  function commit(message) {persist();render();if(message)notify(message);}
  function maybeActivate() {
    if(S.run.phase==='idle' && blockers().length===0 && S.activated!==S.target) {
      S.activated=S.target;
      log('studio',S.target,`Target ${V(S.target).name} is prepared on all required nodes and activated for the next run.`,'success');
    }
  }
  function enqueue(node, versionId, {force=false,retry=false}={}) {
    const version=V(versionId);
    if(!version || !node.online || busy(node))return;
    if(!compatible(node,versionId)) {
      node.job={version:versionId,stage:'incompatible',percent:0,error:`${node.backend} is not supported by this sample target.`};
      log(node.id,versionId,`${node.backend} is incompatible with this target. Choose a supported source revision or exclude this worker from the next run.`,'error');
      return;
    }
    if(artifact(node,versionId)&&!force) {log(node.id,versionId,'Verified existing local artifact: immutable source SHA and backend match.','success');return;}
    if(retry)node.failNext=false;
    const token=`${Date.now()}-${++sequence}`;
    node.job={token,version:versionId,stage:'downloading',percent:8,error:null};
    log(node.id,versionId,`${retry?'Retrying':'Preparing'} ${version.name} at sample SHA ${version.sha} for ${node.backend}.`);
    log(node.id,versionId,'Downloading source into a separate version directory. Active binaries remain untouched.');
    const transitions=[
      [900,'downloading',24,'Source download complete; checking immutable revision.'],
      [1800,'building',42,`Configuring the ${node.backend} backend on ${node.name}.`],
      [3000,'building',68,'Compiling llama-server and rpc-server beside existing builds.'],
      [4200,'verifying',92,'Verifying source identity, backend and local executable readiness.'],
      [5400,'prepared',100,'Local build verified and prepared.']
    ];
    transitions.forEach(([delay,stage,percent,message])=>later(()=>{
      const current=N(node.id);
      if(!current || current.job?.token!==token || !busy(current) || !current.online)return;
      if(stage==='building'&&current.failNext) {
        current.failNext=false;current.job.stage='failed';current.job.error='Sample build error: backend toolchain was not available.';
        log(current.id,versionId,'BUILD FAILED (sample): the local backend toolchain was unavailable. Retry simulates a repaired environment. The running build has not changed.','error');
        commit(`${current.name}: build failed. Retry is available.`);toast(`${current.name} could not build. Open its log or retry.`);return;
      }
      current.job.stage=stage;current.job.percent=percent;
      log(current.id,versionId,message,stage==='prepared'?'success':'info');
      if(stage==='prepared') {
        const sizes={Metal:704,CUDA:1186,Vulkan:836,ROCm:1096,CPU:416};
        current.artifacts[versionId]={backend:current.backend,disk:sizes[current.backend]||704};
        if(activeOn(current))log(current.id,versionId,`Prepared beside the active ${V(S.run.version)?.name} build. Activation waits for the run to fully stop.`);
        maybeActivate();
      }
      commit(`${current.name}: ${stage}.`);
    },delay));
  }
  function syncTarget() {
    if(S.role!=='master')return toast('The master manages the cluster target.');
    if(S.nodes.some(busy))return toast('Let current preparation finish before changing the target.');
    if(!V(S.draft))return;
    const changed=S.target!==S.draft;
    S.target=S.draft;S.enforce=true;
    if(changed&&S.run.phase==='idle')S.activated=null;
    log('studio',S.target,`Master saved target ${V(S.target).name} at ${V(S.target).sha}. Persistent preparation policy includes joining and returning workers.`,'success');
    S.nodes.forEach(n=>{if(n.online)enqueue(n,S.target);else log(n.id,S.target,'Node is offline. The saved target will be prepared when this worker reconnects.');});
    maybeActivate();commit('Cluster target saved. Preparation started on connected nodes.');toast('Target saved. Connected nodes are syncing.');
  }
  function retryNode(id) {
    const n=N(id);
    if(!n || !n.online || (S.role!=='master'&&n.id!==localNode().id))return;
    if(!compatible(n,S.target))return toast('Choose a compatible target or exclude this worker from the next run.');
    enqueue(n,n.job?.version||S.target,{retry:true,force:true});commit(`Retrying ${n.name}.`);
  }
  function stopRun() {
    if(S.role!=='master'||S.run.phase!=='running')return;
    S.run.phase='stopping';const version=S.run.version;
    log('studio',version,'Stopping the sample run. Waiting for every participating process to finish.');commit('Stopping sample run. Prepared versions are not activated yet.');
    later(()=>{
      if(S.run.phase!=='stopping')return;
      S.run.phase='idle';S.run.nodes=[];
      log('studio',version,'All sample inference processes stopped. The active run is fully released.','success');
      maybeActivate();commit('Run fully stopped.');toast(blockers().length?'Run stopped. Resolve readiness issues before the next run.':'Run stopped. The prepared target is ready for the next run.');
    },1800);
  }
  function startRun() {
    if(S.role!=='master'||S.run.phase!=='idle')return;
    if(blockers().length)return toast('Resolve the required-node blockers before starting.');
    maybeActivate();S.run={phase:'running',version:S.target,nodes:required().map(n=>n.id)};
    log('studio',S.target,`Started a sample run on ${S.run.nodes.length} required nodes at ${V(S.target).sha}.`,'success');
    commit('Sample run started on the prepared target.');toast('Sample run started. All participating source revisions match.');
  }
  function joinNode(reconnect=false) {
    let n;
    if(reconnect) {n=N('reserve');n.online=true;}
    else {
      n={id:'nova',name:'nova-04',role:'worker',backend:'ROCm',device:'AMD Instinct',address:'192.168.10.14',online:true,selected:false,artifacts:{}};
      if(N(n.id))return;S.nodes.push(n);
    }
    log(n.id,S.target,`${n.name} ${reconnect?'reconnected':'joined'}. Master assigned saved target ${V(S.target).name}.`);
    if(S.enforce)enqueue(n,S.target);
    commit(`${n.name} is preparing the master’s target automatically.`);toast(`${n.name} inherited the saved target and started preparation.`);
  }
  let opener = null;
  function openDialog(title, body, foot='', wide=false, context=null) {
    const dialog=$('#modal'), active=document.activeElement;
    if(!dialog.open)opener={id:active?.id,action:active?.dataset?.action,value:active?.dataset?.value};
    dialogGeneration++;dialogContext=context;dialog.classList.toggle('wide',wide);
    dialog.innerHTML=`<div class="dialog-head"><h2 id="dialog-title">${esc(title)}</h2><button class="btn ghost icon-btn" type="button" data-action="close-dialog" aria-label="Close dialog">${icon('close')}</button></div><div class="dialog-body">${body}</div><div class="dialog-foot">${foot||btn('Done','close-dialog','','ghost')}</div>`;
    if(!dialog.open)dialog.showModal();
    const autofocus=$('[autofocus]',dialog);if(autofocus)autofocus.focus();else $('button',dialog)?.focus();
  }
  function closeDialog() {
    const dialog=$('#modal');dialogGeneration++;dialogContext=null;dialog.close();
    let focus=opener?.id?document.getElementById(opener.id):null;
    if(!focus&&opener?.action)focus=$(`[data-action="${CSS.escape(opener.action)}"][data-value="${CSS.escape(opener.value||'')}"]`);
    if(focus&&!focus.disabled)focus.focus({preventScroll:true});else $('#tab-llamacpp')?.focus({preventScroll:true});
  }
  function showVersion(id) {
    const v=V(id);if(!v)return;
    const node=localNode(), built=artifact(node,id);
    openDialog(v.name,`<div class="notice blue">${icon('info')}<div><strong>Example source identity</strong><p>Revisions, resolution and disk usage are simulated. This page has not contacted the repository.</p></div></div><div class="detail-grid">${[['Source',v.kind==='fork'?'Public HTTPS fork':'Upstream release'],['Repository',v.url],['Requested ref',`${v.refType}: ${v.ref}`],['Resolved SHA',v.sha],['Local backend',node.backend],['Local build',built?'Prepared':'Not built'],['Disk usage',built?disk(node.artifacts[id].disk):'No local build'],['Compatible backends',v.supported.join(', ')],['Removal',protectedReason(id)||'Available when not targeted, running or building']].map(([k,val])=>`<div class="detail-row"><span>${esc(k)}</span><span class="${['Repository','Resolved SHA','Requested ref'].includes(k)?'mono micro':''}">${esc(val)}</span></div>`).join('')}</div>`,`${btn('Copy sample SHA','copy-sha','copy','ghost',id)}${btn('Done','close-dialog','','ghost')}`);
  }
  function logsFor(ctx) {return S.logs.filter(x=>ctx.kind==='node'?x.node===ctx.id:x.version===ctx.id);}
  function logLines(ctx) {
    const logs=logsFor(ctx);
    return logs.length?logs.map(x=>`<div class="log-line"><time>${timestamp(x.at)}</time><span class="${x.level==='error'?'danger':x.level==='success'?'good':''}">${ctx.kind==='version'?`[${esc(N(x.node)?.name||x.node)}] `:''}${esc(x.message)}</span></div>`).join(''):'<p class="muted">No preparation events yet. Start a build or sync to create a sample log.</p>';
  }
  function showLogs(kind,id) {
    const ctx={kind,id};
    const label=kind==='node'?N(id)?.name:V(id)?.name;
    const n=kind==='node'?N(id):localNode();
    openDialog(`Build log · ${label||'Unknown'}`,`<div class="log-meta"><span class="od-row">${icon('log')}${badge('Simulated events')}</span><span class="small muted">Last 300 events retained</span></div><div class="log-console" id="build-log" role="log" tabindex="0" aria-label="Sample build events" aria-live="off">${logLines(ctx)}</div><p class="micro muted">These events illustrate preparation and recovery. They are not output from a real build or remote node.</p>`,`${n?.job?.stage==='failed'&&n.online&&(S.role==='master'||n.id===localNode().id)?btn('Retry build','retry-node','retry','ghost',n.id):''}${btn('Copy log','copy-log','copy','ghost')}${btn('Done','close-dialog','','ghost')}`,true,ctx);
    const console=$('#build-log');console.scrollTop=console.scrollHeight;
  }
  function refreshLogs() {
    if(!dialogContext||!$('#modal').open||!$('#build-log'))return;
    const console=$('#build-log'), follows=console.scrollHeight-console.scrollTop-console.clientHeight<48;
    console.innerHTML=logLines(dialogContext);if(follows)console.scrollTop=console.scrollHeight;
  }
  async function copyText(text) {
    try {if(!navigator.clipboard)throw new Error('Clipboard unavailable');await navigator.clipboard.writeText(text);toast('Copied to clipboard.');}
    catch {
      openDialog('Copy text',`<p class="small muted">Select and copy the text below.</p><div class="field"><label for="copy-text">Text to copy</label><textarea id="copy-text" class="control mono small" rows="7" readonly autofocus>${esc(text)}</textarea></div>`);
      $('#copy-text').select();
    }
  }
  let addState=null;
  function addVersion(fork=false) {
    if(S.role!=='master')return toast('The master manages the version library.');
    addState={resolved:null,resolving:false};
    openDialog('Add version',`<p class="muted small">Save an upstream release or a public fork. A tag or branch is pinned to one immutable source revision.</p><form id="add-version-form" novalidate><div id="add-errors" role="alert" hidden></div><div class="field"><label for="add-source">Source</label><select id="add-source" class="control" name="source" autofocus><option value="upstream" ${!fork?'selected':''}>Upstream · ggml-org/llama.cpp</option><option value="fork" ${fork?'selected':''}>Public HTTPS fork</option></select></div><div class="field"><label for="add-url">Repository URL <span class="muted">· required</span></label><input id="add-url" class="control" type="url" name="url" value="${fork?'https://github.com/example-lab/llama.cpp':'https://github.com/ggml-org/llama.cpp'}" ${!fork?'readonly':''} autocomplete="off" spellcheck="false" aria-describedby="add-url-error add-url-help"><span class="error" id="add-url-error"></span><small id="add-url-help">Public HTTPS only. No embedded credentials. Fork examples are illustrative URLs.</small></div><div class="field-grid"><div class="field"><label for="add-ref-type">Reference type</label><select id="add-ref-type" class="control" name="refType"><option value="tag" ${!fork?'selected':''}>Tag</option><option value="branch" ${fork?'selected':''}>Branch</option><option value="commit">Commit SHA</option></select></div><div class="field"><label for="add-ref">Reference <span class="muted">· required</span></label><input id="add-ref" class="control" name="ref" value="${fork?'rpc-experiment':'v0.5.0'}" autocomplete="off" spellcheck="false" aria-describedby="add-ref-error add-ref-help"><span class="error" id="add-ref-error"></span><small id="add-ref-help">Enter a tag, branch, or a full 40-character commit.</small></div></div><div class="notice blue">${icon('info')}<div><strong>Simulated revision resolution</strong><p>No repository is contacted. The returned SHA is a stable sample identifier, not a verified upstream commit.</p></div></div><div id="resolve-result" aria-live="polite"></div><div>${btn('Resolve revision','resolve-version','branch','ghost')}</div></form>`,`<span class="foot-note">Public sources only</span>${btn('Cancel','close-dialog','','ghost')}<button class="btn primary" type="submit" form="add-version-form" id="save-version" disabled>Add version</button>`);
  }
  function addValues() {return {kind:$('#add-source').value,url:$('#add-url').value.trim(),refType:$('#add-ref-type').value,ref:$('#add-ref').value.trim()};}
  function addError(name,value) {const el=$(`#add-${name}`);if(el)el.setAttribute('aria-invalid',value?'true':'false');$(`#add-${name}-error`).textContent=value;}
  function validateAdd(only='') {
    const values=addValues();let urlError='',refError='';
    try {
      const url=new URL(values.url), hostname=url.hostname.toLowerCase();
      if(url.protocol!=='https:'||url.username||url.password||url.search||url.hash||url.port)urlError='Use a public HTTPS repository URL without credentials, a port, query or fragment.';
      else if(!hostname.includes('.')||hostname==='localhost'||hostname.endsWith('.local')||hostname.endsWith('.internal')||/^\[|^\d+(?:\.\d+){3}$/.test(hostname)||url.pathname.split('/').filter(Boolean).length<2)urlError='Use a public repository address, such as https://github.com/owner/repository.';
    } catch {urlError='Enter a complete HTTPS repository URL.';}
    if(!values.ref)refError='Enter the tag, branch or commit you want to save.';
    else if(values.refType==='commit'&&!/^[a-f0-9]{40}$/i.test(values.ref))refError='A commit must contain all 40 hexadecimal characters.';
    else if(values.refType!=='commit'&&(values.ref.length>160||/\s|[~^:?*\[\\]|\.\.|@\{|^\/|\/$|\.lock$/i.test(values.ref)))refError='Enter a valid Git reference without spaces or unsupported characters.';
    if(!only||only==='url')addError('url',urlError);
    if(!only||only==='ref')addError('ref',refError);
    if(!only) {
      const summary=$('#add-errors'), messages=[urlError?'<a href="#add-url">Check the repository URL</a>':'',refError?'<a href="#add-ref">Check the reference</a>':''].filter(Boolean);
      summary.hidden=!messages.length;summary.className='form-error-summary';summary.innerHTML=messages.join(' · ');
    }
    return !urlError&&!refError;
  }
  function invalidateResolution() {
    if(!addState||!$('#add-version-form'))return;
    addState.resolved=null;addState.resolving=false;dialogGeneration++;
    $('#resolve-result').innerHTML='';$('#save-version').disabled=true;
    const button=$('[data-action="resolve-version"]');button.disabled=false;button.innerHTML=`${icon('branch',true)}Resolve revision`;
  }
  function resolveVersion() {
    if(!addState||addState.resolving||!validateAdd())return;
    const values=addValues(), token=dialogGeneration;
    addState.resolving=true;$('#save-version').disabled=true;
    const button=$('[data-action="resolve-version"]');button.disabled=true;button.textContent='Resolving…';
    $('#resolve-result').innerHTML='<div class="resolve-status"><span>Resolving a sample immutable revision…</span><progress class="job-progress" aria-label="Resolving sample source revision"></progress></div>';
    later(()=>{
      if(token!==dialogGeneration||!$('#add-version-form')||!addState)return;
      const normalized=new URL(values.url);normalized.pathname=normalized.pathname.replace(/\/$/,'');
      const sha=values.refType==='commit'?values.ref.toLowerCase():shaFor(`${normalized.href}:${values.refType}:${values.ref}`);
      const duplicate=S.versions.find(v=>v.url===normalized.href&&v.sha===sha);
      if(duplicate) {
        addState.resolving=false;button.disabled=false;button.textContent='Resolve revision';
        $('#resolve-result').innerHTML=`<div class="notice amber">${icon('info')}<div><strong>Already saved as ${esc(duplicate.name)}</strong><p>Close this dialog and select the existing version in your library.</p></div></div>`;return;
      }
      addState.resolved={...values,url:normalized.href,sha};addState.resolving=false;
      $('#resolve-result').innerHTML=`<div class="resolved-box"><span class="od-row good">${icon('check',true)}Sample revision resolved</span><code>${sha}</code><span class="micro muted">${esc(values.refType)} ${esc(values.ref)} → immutable sample SHA</span></div>`;
      button.disabled=false;button.innerHTML=`${icon('retry',true)}Resolve again`;$('#save-version').disabled=false;
    },1100);
  }
  function saveVersion() {
    if(!addState?.resolved||addState.resolving||!validateAdd())return;
    const resolved=addState.resolved,id=`saved-${Date.now()}-${++sequence}`;
    S.versions.push({...resolved,id,name:resolved.refType==='commit'?resolved.sha.slice(0,12):resolved.ref,supported:['Metal','CUDA','Vulkan','ROCm','CPU'],added:'User-added sample'});
    S.draft=id;log('studio',id,`Saved ${resolved.kind} source at immutable sample SHA ${resolved.sha}. No build has started.`,'success');
    closeDialog();commit('Version added. Select Set target & sync to prepare it.');toast('Version saved and selected. Set target & sync when ready.');
    $('#target-select')?.focus({preventScroll:true});
  }
  function removeVersion(id) {
    const v=V(id);if(!v||S.role!=='master')return;
    const reason=protectedReason(id);if(reason)return toast(reason);
    openDialog('Remove saved version?',`<p>Remove <strong>${esc(v.name)}</strong> and its cached sample builds from this version library?</p><div class="detail-row"><span>Immutable sample SHA</span><span class="mono micro">${esc(v.sha)}</span></div><p class="small muted">The cluster target and active run stay unchanged. This action only changes local prototype data.</p>`,`${btn('Cancel','close-dialog','','ghost')}${btn('Remove version','confirm-remove','trash','danger-btn',id)}`);
  }
  function confirmRemove(id) {
    if(S.role!=='master'||protectedReason(id))return toast('This version is in use and cannot be removed.');
    const name=V(id)?.name;S.versions=S.versions.filter(v=>v.id!==id);S.nodes.forEach(n=>{delete n.artifacts[id];if(n.job?.version===id)n.job=null;});
    if(S.draft===id)S.draft=S.target;S.logs=S.logs.filter(x=>x.version!==id);closeDialog();commit('Saved version removed.');toast(`${name} removed from the sample library.`);
  }
  function loadScenario(id) {
    if(!scenarios.some(([key])=>key===id))return;
    timers.forEach(clearTimeout);timers.clear();$('#toasts').replaceChildren();
    if($('#modal').open)closeDialog();
    S=initial();S.scenario=id;section='llamacpp';scenarioOpen=true;
    if(id==='fork') {commit('Add-fork scenario loaded.');addVersion(true);return;}
    if(['sync','failure','prepare','worker','incompatible'].includes(id)) {
      S.draft='fork-rpc';
      if(['sync','failure','incompatible'].includes(id))S.run={phase:'idle',version:'upstream-050',nodes:[]};
      if(id==='failure')N('atlas').failNext=true;
      if(id==='incompatible')V('fork-rpc').supported=['Metal','CUDA'];
      syncTarget();
      if(id==='worker'){S.role='worker';commit('Worker view loaded. The master manages the target.');}
      return;
    }
    if(id==='join'||id==='reconnect') {
      S.target=S.draft=S.activated='fork-rpc';S.run.version='fork-rpc';
      S.nodes.filter(n=>n.online).forEach(n=>{n.artifacts['fork-rpc']={backend:n.backend,disk:n.backend==='CUDA'?1186:n.backend==='Metal'?704:836};});
      log('studio',S.target,'The saved target is enforced. Waiting for the next membership event.','success');
      commit('Membership scenario loaded. A worker will arrive shortly.');later(()=>joinNode(id==='reconnect'),1200);return;
    }
    if(id==='offline') {S.run={phase:'idle',version:'upstream-050',nodes:[]};N('reserve').selected=true;S.activated=null;}
    commit('Sample scenario loaded.');toast(id==='baseline'?'Sample state reset.': 'Scenario loaded. Inspect readiness or change the selected workers.');
  }
  function howItWorks() {
    openDialog('How target sync works',`<div class="detail-grid">${[
      ['01 · Pin source','The master saves one immutable source SHA. A branch or tag is resolved once; it does not move silently.'],
      ['02 · Prepare locally','Every connected node downloads and builds for its own backend. Metal, CUDA, Vulkan and ROCm can share the source revision.'],
      ['03 · Keep running','Preparation uses a separate version directory. The active run keeps the binaries it started with.'],
      ['04 · Activate safely','After the run fully stops, the target becomes eligible when the master and selected workers have compatible builds.'],
      ['05 · Enforce on join','New and reconnecting workers receive the saved target automatically. An unrelated offline worker does not hold up a run.']
    ].map(([title,description])=>`<div class="detail-row"><span class="mono micro">${title}</span><span>${description}</span></div>`).join('')}</div><div class="notice blue">${icon('info')}<div><strong>Local interactive prototype</strong><p>All revisions, transitions, logs and measurements are examples. No production endpoint or repository is contacted.</p></div></div>`);
  }
  const tabScroll={};
  function navigate(next) {
    if(!['hardware','network','tokens','software','llamacpp','paths'].includes(next))return;
    tabScroll[section]=window.scrollY;section=next;render();window.scrollTo({top:tabScroll[next]||0,behavior:'instant'});
  }
  const narrow=window.matchMedia('(max-width:767px)');
  function setMenu(open,restoreFocus=true) {
    menuOpen=open&&narrow.matches;
    const sidebar=$('#sidebar'), main=$('#main-shell');
    sidebar.classList.toggle('open',menuOpen);$('#menu-scrim').classList.toggle('open',menuOpen);$('#menu-toggle').setAttribute('aria-expanded',String(menuOpen));
    sidebar.inert=narrow.matches&&!menuOpen;main.inert=menuOpen;document.body.style.overflow=menuOpen?'hidden':'';
    if(menuOpen)$('[data-action="close-menu"]',sidebar).focus();else if(restoreFocus&&narrow.matches)$('#menu-toggle').focus();
  }
  document.addEventListener('click',event=>{
    const control=event.target.closest('[data-action]');if(!control||control.disabled)return;
    const action=control.dataset.action,id=control.dataset.value;
    switch(action) {
      case 'settings-tab':navigate(id);break;
      case 'open-llama':navigate('llamacpp');break;
      case 'sync':syncTarget();break;
      case 'select-version':
        if(S.role==='master'&&!S.nodes.some(busy)&&V(id)){S.draft=id;commit('Version selected. Set target & sync to save it.');$('#target-select')?.focus();}break;
      case 'add-version':addVersion();break;
      case 'resolve-version':resolveVersion();break;
      case 'version-details':showVersion(id);break;
      case 'version-logs':showLogs('version',id);break;
      case 'node-logs':showLogs('node',id);break;
      case 'retry-node':retryNode(id);break;
      case 'build-version':{
        const node=localNode();
        if(S.role!=='master'&&id!==S.target)return toast('Workers only prepare the master’s assigned target.');
        if(busy(node))return toast('A local build is already in progress.');
        if(!compatible(node,id))return toast('This version does not support the configured backend.');
        enqueue(node,id,{force:true,retry:true});commit('Local build started.');toast(`Building ${V(id).name} for ${node.backend}.`);break;
      }
      case 'remove-version':removeVersion(id);break;
      case 'confirm-remove':confirmRemove(id);break;
      case 'stop-run':stopRun();break;
      case 'start-run':startRun();break;
      case 'how-it-works':howItWorks();break;
      case 'close-dialog':closeDialog();break;
      case 'copy-sha':if(V(id))copyText(V(id).sha);break;
      case 'copy-log':if(dialogContext)copyText(logsFor(dialogContext).map(x=>`${x.at} [${N(x.node)?.name||x.node}] ${x.message}`).join('\n')||'No sample preparation events.');break;
      case 'load-scenario':loadScenario($('#scenario-select').value);break;
      case 'reset-sample':openDialog('Reset sample state?', '<p>Restore the example version library, cluster target and node states. Any versions added in this prototype will be removed.</p><p class="small muted">Your real cluster and repository are never affected.</p>',`${btn('Cancel','close-dialog','','ghost')}${btn('Reset sample','confirm-reset','retry','danger-btn')}`);break;
      case 'confirm-reset':loadScenario('baseline');break;
      case 'open-menu':setMenu(true);break;
      case 'close-menu':setMenu(false);break;
      case 'nav-context':
        setMenu(false,false);
        openDialog(id,`<p class="muted">${esc({Cluster:'Cluster health and membership live here. The llama.cpp target applies to the same master and workers.',Models:'Your model library is independent from saved llama.cpp source versions.',Deploy:'The next deployment uses the enforced target, once its master and selected workers are ready.',Activity:'Preparation and failure events are available in the node and version build logs.',Connect:'Client connections keep using the active inference run while another version is prepared.'}[id]||'Return to Settings to manage the cluster runtime.')}</p><p class="small muted">This focused prototype keeps you in the llama.cpp settings workflow.</p>`,btn('Return to settings','close-dialog','arrow','ghost'));break;
      case 'workspace':setMenu(false,false);openDialog('Local sample node',`<div class="detail-grid">${[['Node',localNode().name],['Role',S.role==='master'?'Master':'Worker'],['Backend',localNode().backend],['Connection','No production cluster connected']].map(([key,value])=>`<div class="detail-row"><span>${key}</span><span>${esc(value)}</span></div>`).join('')}</div><p class="small muted">Use Prototype scenarios to explore the managed worker view.</p>`);break;
    }
  });
  document.addEventListener('change',event=>{
    const el=event.target;
    if(el.id==='target-select'&&S.role==='master'&&!S.nodes.some(busy)&&V(el.value)){S.draft=el.value;commit('Selection changed. The saved target is unchanged until you sync.');}
    if(el.dataset.nodeSelect&&S.role==='master'){
      const node=N(el.dataset.nodeSelect);if(!node||node.role==='master')return;
      node.selected=el.checked;maybeActivate();commit(`${node.name} ${node.selected?'included in':'excluded from'} the next run.`);
    }
    if(el.id==='add-source'){
      const fork=el.value==='fork';$('#add-url').readOnly=!fork;$('#add-url').value=fork?'https://github.com/example-lab/llama.cpp':'https://github.com/ggml-org/llama.cpp';
      $('#add-ref-type').value=fork?'branch':'tag';$('#add-ref').value=fork?'rpc-experiment':'v0.5.0';addError('url','');addError('ref','');invalidateResolution();
    }
    if(el.id==='add-ref-type') {$('#add-ref').value=el.value==='commit'?shaFor('user-selected-sample-commit'):el.value==='branch'?'rpc-experiment':'v0.5.0';addError('ref','');invalidateResolution();}
  });
  document.addEventListener('input',event=>{if(event.target.closest('#add-version-form')&&['add-url','add-ref'].includes(event.target.id))invalidateResolution();});
  document.addEventListener('focusout',event=>{if(!$('#add-version-form'))return;if(event.target.id==='add-url')validateAdd('url');if(event.target.id==='add-ref')validateAdd('ref');});
  document.addEventListener('submit',event=>{if(event.target.id==='add-version-form'){event.preventDefault();if(addState?.resolved)saveVersion();else resolveVersion();}});
  document.addEventListener('toggle',event=>{if(event.target.id==='scenario-panel')scenarioOpen=event.target.open;},true);
  $('#modal').addEventListener('cancel',event=>{event.preventDefault();closeDialog();});
  $('#modal').addEventListener('click',event=>{if(event.target!==$('#modal'))return;const r=event.target.getBoundingClientRect();if(event.clientX<r.left||event.clientX>r.right||event.clientY<r.top||event.clientY>r.bottom)closeDialog();});
  $('#menu-scrim').addEventListener('click',()=>setMenu(false));
  document.addEventListener('keydown',event=>{
    if($('#modal').open)return;
    if(event.key==='Escape'&&menuOpen){setMenu(false);return;}
    if(event.key==='Tab'&&menuOpen){const focusables=$$('button:not(:disabled),a[href]', $('#sidebar')),first=focusables[0],last=focusables.at(-1);if(event.shiftKey&&document.activeElement===first){event.preventDefault();last.focus();}else if(!event.shiftKey&&document.activeElement===last){event.preventDefault();first.focus();}}
  });
  narrow.addEventListener('change',()=>setMenu(false,false));
  if(restored) {
    if(!V(S.draft))S.draft=S.target;
    if(S.run.phase==='stopping'){S.run.phase='idle';S.run.nodes=[];log('studio',S.target,'Interrupted sample stopping transition completed on reopen.','success');}
    S.nodes.forEach(n=>{
      if(busy(n)){const version=n.job.version;n.job=null;log(n.id,version,'Resuming local sample preparation after reopening the prototype.');enqueue(n,version);}
      else if(n.online&&S.enforce&&!artifact(n,S.target)&&!['failed','incompatible'].includes(n.job?.stage))enqueue(n,S.target);
    });
  }
  maybeActivate();persist();render();setMenu(false,false);
})();
