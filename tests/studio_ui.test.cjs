// Dependency-free UI contract tests. No browser, model calls or audio generation.
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('src/podcast_automate/web/app.js', 'utf8');
test('script progress shows the active episode and readable results without audio counts',()=>{
  const app=studio();
  app.run(`project={id:'test',job:{status:'running',action:'resume',started_at:new Date().toISOString(),progress:{phase:'script',stage:'teaching',current_episode:'ep_002',episode_number:2,episode_title:'Attention',activity:'Lehrkonzept wird geprüft',activity_started_at:new Date().toISOString(),completed_segments:1,total_segments:6,episodes:[{episode_id:'ep_001',title:'Introduction',completed:true,teaching_preview:'A safe <script> excerpt'},{episode_id:'ep_002',title:'Attention',completed:false,teaching_preview:''}]},run:{stages:{teaching:{status:'running'}}}}};renderJob();`);
  app.run('step=PAGE.production;renderJob();');
  const html=app.elements.get('production-progress').innerHTML;
  assert.ok(html.includes('Folge 2 von 6'));
  assert.ok(html.includes('1 von 6 Folgen'));
  assert.ok(html.includes('Lehrkonzept lesen'));
  assert.ok(html.includes('A safe &lt;script&gt; excerpt'));
  assert.ok(!html.includes('Sprechabschnitten'));
  assert.ok(!app.elements.get('job-status').innerHTML.includes('Lehrkonzept lesen'));
});
test('choosing a project keeps its identity in the URL for reloads without browser storage',async()=>{
  const app=studio();
  app.run(`window.history={replaceState(state,title,url){window.savedProjectUrl=url;}};`);
  await app.run(`selectProject('example',{id:'example',config:boot.defaults,text:{provider:'codex_cli',model:null,max_output_tokens:32768},chat:[]})`);
  assert.equal(app.run('window.savedProjectUrl'),'/?project=example&step=brief');
  assert.equal(app.elements.get('project-select').value,'example');
});
test('script progress displays the approved run call limit',()=>{
  const app=studio();
  app.run(`project={id:'test',job:{status:'running',action:'resume',started_at:new Date().toISOString(),progress:{model_calls:40,model_call_limit:120},run:{stages:{teaching:{status:'running'}}}}};renderJob();`);
  assert.ok(app.elements.get('job-status').innerHTML.includes('Modellaufrufe: 40 von 120'));
});
test('job duration, pending call, saved result and stale progress are distinguished',()=>{
  const app=studio(), p=workflowProject(app);
  p.job.started_at=new Date(Date.now()-22*60000).toISOString();
  p.job.progress={phase:'script',stage:'teaching',activity:'Review',model_calls:53,model_call_limit:120,
    updated_at:new Date().toISOString(),model_call_started_at:new Date(Date.now()-2*60000).toISOString(),
    last_result_at:new Date(Date.now()-3*60000).toISOString()};
  app.run(`project=${JSON.stringify(p)};renderJob();`);
  let html=app.elements.get('job-status').innerHTML;
  assert.ok(html.includes('Gesamte Laufzeit seit Start/Fortsetzung: 22 Min.'));
  assert.ok(html.includes('Aktueller Modellaufruf: seit 2 Min.'));
  assert.ok(html.includes('Letztes gespeichertes Modellergebnis: vor 3 Min.'));
  assert.ok(!html.includes('nicht aktualisiert'));
  app.run(`project.job.progress.updated_at=new Date(Date.now()-24*60000).toISOString();renderJob();`);
  html=app.elements.get('job-status').innerHTML;
  assert.ok(html.includes('Fortschrittsanzeige seit 24 Min. nicht aktualisiert'));
  assert.ok(html.includes('Zuletzt gemeldeter Modellaufruf gestartet vor'));
  assert.ok(!html.includes('Aktueller Modellaufruf: seit'));
  assert.ok(!app.run('renderScriptProgress(project.job.progress,true)').includes('class="activity-dot"'));
  app.run(`project.job.status='completed';renderJob();`);
  assert.ok(!app.elements.get('job-status').innerHTML.includes('nicht aktualisiert'));
});
test('polling refreshes saved results without changing the job identity or status',async()=>{
  const app=studio(), p=workflowProject(app);
  p.job.progress={phase:'script',stage:'teaching',activity:'Review',model_calls:41,model_call_limit:120,
    updated_at:new Date(Date.now()-24*60000).toISOString()};
  await app.run(`selectProject('test',${JSON.stringify(p)})`);
  const fresh=structuredClone(p);
  fresh.job.progress={...fresh.job.progress,model_calls:55,updated_at:new Date().toISOString(),
    last_result_at:new Date().toISOString()};
  app.responses.set('/api/projects/test',fresh);
  await app.run('poll()');
  const html=app.elements.get('job-status').innerHTML;
  assert.ok(html.includes('Modellaufrufe: 55 von 120'));
  assert.ok(html.includes('Letztes gespeichertes Modellergebnis'));
  assert.ok(!html.includes('nicht aktualisiert'));
  assert.equal(app.requests.filter(r=>r.options?.method==='POST').length,0);
});
test('a stopped teaching review shows its concrete issues instead of a path and futile retry',()=>{
  const app=studio();
  app.run(`project={id:'test',job:{status:'blocked',action:'resume',message:'Review: C:/private/checkpoint.json',progress:{phase:'script',stage:'teaching',activity:'Lehrkonzept wird geprüft',current_episode:'ep_002',episode_number:2,episode_title:'Second lesson',completed_segments:1,total_segments:6,episodes:[],review_issues:['Explain the Key projection.']},run:{stages:{teaching:{status:'blocked',error:{code:'teaching_design_failed'}}}}}};renderJob();`);
  app.run('step=PAGE.production;renderJob();');
  const html=app.elements.get('job-status').innerHTML+app.elements.get('production-progress').innerHTML;
  assert.ok(html.includes('Explain the Key projection.'));
  assert.ok(html.includes('Was noch erklärt werden muss'));
  assert.ok(!html.includes('C:/private'));
  assert.ok(!html.includes('data-action="resume"'));
});
test('foundation research runs without a retry button and exposes real unresolved questions',()=>{
  const app=studio();
  app.run(`project={id:'test',job:{status:'running',action:'resume',started_at:new Date().toISOString(),progress:{phase:'foundation_research'},run:{stages:{teaching:{status:'running'}}}}};renderJob();`);
  let html=app.elements.get('job-status').innerHTML;
  assert.ok(html.includes('automatisch recherchiert'));
  assert.ok(!html.includes('undefined von'));
  assert.ok(!html.includes('data-action="resume"'));
  app.run(`project.job.status='blocked';project.job.message='Erforderliche Erklärgrundlagen fehlen: C:/private/research_needed.md';project.job.run.stages.teaching.error={code:'teaching_research_required'};project.job.research_gaps=[{question:'What changes <script>?',why_needed:'Missing mechanism'}];renderJob();`);
  app.run('step=PAGE.production;renderJob();');
  html=app.elements.get('job-status').innerHTML+app.elements.get('production-progress').innerHTML;
  assert.ok(html.includes('What changes &lt;script&gt;?'));
  assert.ok(html.includes('Missing mechanism'));
  assert.ok(!html.includes('C:/private'));
  assert.ok(!html.includes('data-action="resume"'));
});
function studio() {
  const elements = new Map(), registered = new Map(), requests = [], responses = new Map(), events = new Map();
  const element = id => {
    if (!elements.has(id)) elements.set(id, {value:'',innerHTML:'',hidden:false,textContent:'',paused:true,focus(){},scrollIntoView(){},addEventListener(){},async play(){this.paused=false;},pause(){this.paused=true;}});
    return elements.get(id);
  };
  const defaults = {topic:'New project',central_question:'Why?',voice_profile:{host_a:'Aiden',host_b:'Vivian'},language:'de-DE',prior_knowledge:'',depth_request:'Deep',focus_questions:[],excluded_topics:[],seed_urls:[]};
  const context = vm.createContext({console,structuredClone,AbortController,encodeURIComponent,URLSearchParams,setInterval(){},window:{addEventListener(name,handler){events.set(name,handler);},scrollTo(){}},
    document:{getElementById:element,querySelectorAll(){return [];},addEventListener(){},modelContext:{registerTool(tool){registered.set(tool.name,tool);}}},
    fetch:async(path,options)=>{requests.push({path,options});const data=responses.get(path)??{token:'csrf',voices:['Aiden','Vivian'],projects:[],defaults};return{ok:true,json:async()=>structuredClone(data)};}});
  vm.runInContext(source, context);
  const run = code => vm.runInContext(code,context);
  run(`boot=${JSON.stringify({token:'csrf',voices:['Aiden','Vivian'],projects:[],defaults})}`);
  return {run,context,elements,registered,requests,responses,events};
}
function workflowProject(app) {
  return app.run(`({id:'test',config:structuredClone(boot.defaults),text:{provider:'codex_cli',model:null,max_output_tokens:32768},chat:[],research:'Reviewed dossier',episodes:[],
    outline:{hash:'h',approval:{plan_hash:'h'},plan:{central_question:'Why?',explanation_path:'Build the explanation.',scope_note:'Scope',episodes:[{episode_id:'ep_001',title:'One',central_question:'Why?',target_minutes:20,scenes:[],deferred_questions:[]}]}},
    job:{id:'job-one',action:'resume',status:'running',started_at:new Date().toISOString(),run:{kind:'script',status:'running',stages:{planning:{status:'completed',attempts:1},teaching:{status:'running',attempts:1},writing:{status:'pending'},polishing:{status:'pending'},review:{status:'pending'},publish:{status:'pending'}}}}})`);
}

test('text model presets and reasoning are explicit with custom model support',()=>{
  const app=studio();
  app.run(`boot.capabilities={text_reasoning_selection:true};boot.text_catalog={codex_models:{'gpt-6-astra':'GPT-6 Astra','gpt-5.6-sol':'GPT-5.6 Sol'}};`);
  const html=app.run('renderBrief()');
  assert.ok(html.includes('id="model-preset"'));
  assert.ok(html.includes('value="gpt-6-astra" selected'));
  assert.ok(html.includes('value="xhigh" selected'));
  assert.ok(html.includes('Denkaufwand'));
  assert.ok(!html.includes('benötigt einen Studio-Neustart'));
  const custom=app.run(`renderTextModelFields({provider:'codex_cli',model:'custom-model',reasoning_effort:'low'})`);
  assert.ok(custom.includes('value="custom" selected'));
  assert.ok(custom.includes('value="custom-model"'));
  assert.ok(custom.includes('value="low" selected'));
  const remote=app.run(`renderTextModelFields({provider:'openrouter',model:'vendor/model',reasoning_effort:null})`);
  assert.ok(remote.includes('Standard des gewählten Modells'));
  assert.ok(!remote.includes('id="model-preset"'));
});

test('switching text providers keeps model and reasoning drafts separate',()=>{
  const app=studio();
  app.run(`boot.capabilities={text_reasoning_selection:true};`);
  for(const [id,value] of Object.entries({provider:'openrouter',model:'gpt-6-astra','reasoning-effort':'xhigh',max_output_tokens:'32768'})){
    app.run(`$('${id}').value=${JSON.stringify(value)}`);
  }
  app.run(`$('provider').dataset={previous:'codex_cli'};changeTextProvider();`);
  assert.ok(app.elements.get('text-model-settings').innerHTML.includes('OpenRouter-Textmodell'));
  app.run(`$('provider').value='codex_cli';$('model').value='vendor/remote';$('reasoning-effort').value='low';changeTextProvider();`);
  assert.ok(app.elements.get('text-model-settings').innerHTML.includes('value="xhigh" selected'));
  assert.equal(app.run('textDrafts.openrouter.model'),'vendor/remote');
  assert.equal(app.run('textDrafts.openrouter.reasoning_effort'),'low');
  assert.equal(app.run('textDrafts.codex_cli.model'),'gpt-6-astra');
  app.run(`$('model').value='';`);
  assert.throws(()=>app.run('readTextChoice()'),/Textmodell auswählen/);
  app.run(`$('provider').dataset.previous='openrouter';changeTextProvider();`);
  assert.ok(app.elements.get('text-model-settings').innerHTML.includes('value="xhigh" selected'));
  assert.equal(app.requests.filter(r=>r.options?.method==='POST').length,0);
});

test('saving the brief sends the visible model and reasoning and reloads the stored values',async()=>{
  const app=studio(), p=workflowProject(app);
  p.job.status='completed';
  app.run(`boot.capabilities={text_reasoning_selection:true};project=${JSON.stringify(p)};`);
  const values={provider:'codex_cli',model:'gpt-6-astra','reasoning-effort':'xhigh',max_output_tokens:'32768',
    topic:'Topic',central_question:'Why?',prior_knowledge:'',depth_request:'Deep',language:'de-DE',focus_questions:'',
    excluded_topics:'',seed_urls:'',target_total_minutes:'',host_a:'Aiden',host_b:'Vivian','tts-provider':'qwen3_local','api-key':''};
  for(const [id,value] of Object.entries(values))app.run(`$(${JSON.stringify(id)}).value=${JSON.stringify(value)}`);
  p.text={provider:'codex_cli',model:'gpt-6-astra',reasoning_effort:'xhigh',max_output_tokens:32768};
  app.responses.set('/api/projects/test',p);
  await app.run('saveBrief()');
  const request=app.requests.find(r=>r.path==='/api/projects/test/save');
  assert.deepEqual(JSON.parse(request.options.body).text,p.text);
  assert.equal(app.run('project.text.reasoning_effort'),'xhigh');
  assert.ok(app.run('renderBrief()').includes('value="xhigh" selected'));
});

test('old servers announce a restart and old runs never claim the new default model',()=>{
  const app=studio(), p=workflowProject(app);
  app.run(`project=${JSON.stringify(p)};`);
  assert.ok(app.run('renderBrief()').includes('benötigt einen Studio-Neustart'));
  assert.ok(!Object.hasOwn(app.run('readTextChoice()'),'reasoning_effort'));
  let html=app.run('renderRunTextChoice(project.job)');
  assert.ok(html.includes('Modell nicht festgelegt'));
  assert.ok(!html.includes('gpt-6-astra'));
  app.run(`project.job.text_generation={model:'gpt-5.6-sol',reasoning_effort:'high'};`);
  html=app.run('renderRunTextChoice(project.job)');
  assert.ok(html.includes('gpt-5.6-sol'));
  assert.ok(html.includes('Reasoning: high'));
});

function previewEpisode(id='ep_001',state='draft',text='A first saved explanation.') {
  return {preview:true,run_id:'run_one',state,hash:state+text,
    script:{episode_id:id,title:id+' Dialogue',chapters:[{chapter_id:'intro',title:'Introduction'}],
      segments:[{chapter_id:'intro',speaker_id:'host_a',text}]},metrics:{words:3400,estimated_minutes:27}};
}

test('first draft becomes readable during polling and never enables preview audio',async()=>{
  const app=studio(), p=workflowProject(app);
  p.job.run.run_id='run_one';p.job.run.stages.writing={status:'running'};
  await app.run(`selectProject('test',${JSON.stringify(p)},PAGE.scripts)`);
  assert.ok(app.elements.get('content').innerHTML.includes('Der erste Skriptentwurf entsteht noch'));
  const next=structuredClone(p);
  next.job.progress={phase:'script',script_previews:[previewEpisode()]};
  app.responses.set('/api/projects/test',next);
  await app.run('poll()');
  const html=app.elements.get('content').innerHTML;
  assert.ok(html.includes('A first saved explanation.'));
  assert.ok(html.includes('Geöffneter Stand: Entwurf'));
  assert.ok(html.includes('Qualitätsprüfungen stehen noch aus'));
  assert.ok(!html.includes('data-action="audio"'));
  assert.ok(!html.includes('data-action="revise"'));
  assert.ok(app.elements.get('steps').innerHTML.includes('1 Folge lesbar'));
  assert.ok(!app.run('renderAudio()').includes('id="audio-approval"'));
  assert.equal(app.requests.filter(r=>r.options?.method==='POST').length,0);
});

test('new episodes and later polish do not replace an open script or its reading position',async()=>{
  const app=studio(), p=workflowProject(app);
  p.job.run.run_id='run_one';
  p.job.progress={phase:'script',script_previews:[previewEpisode()]};
  await app.run(`selectProject('test',${JSON.stringify(p)},PAGE.scripts)`);
  const body=app.elements.get('content').innerHTML;
  app.run(`$('script-text').scrollTop=500;$('script-feedback').value='My reading notes';`);
  const next=structuredClone(p);
  next.job.progress.script_previews=[previewEpisode('ep_001','polished','A polished explanation.'),previewEpisode('ep_002')];
  app.responses.set('/api/projects/test',next);
  await app.run('poll()');
  assert.equal(app.elements.get('content').innerHTML,body);
  assert.equal(app.elements.get('script-text').scrollTop,500);
  assert.equal(app.elements.get('script-feedback').value,'My reading notes');
  const controls=app.elements.get('script-reader-controls').innerHTML;
  assert.ok(controls.includes('ep_002 Dialogue'));
  assert.ok(controls.includes('Aktuellen Stand laden'));
  assert.ok(controls.includes('Geöffneter Stand: Entwurf'));
  app.run('readingSnapshot=null;$("content").innerHTML=renderScript();');
  assert.ok(app.elements.get('content').innerHTML.includes('A polished explanation.'));
  assert.ok(app.elements.get('content').innerHTML.includes('Geöffneter Stand: Dialog überarbeitet'));
  assert.equal(app.requests.filter(r=>r.options?.method==='POST').length,0);
});

test('publishing the series preserves an open preview until the reader loads the final version',async()=>{
  const app=studio(), p=workflowProject(app);
  p.job.run.run_id='run_one';p.job.progress={phase:'script',script_previews:[previewEpisode()]};
  await app.run(`selectProject('test',${JSON.stringify(p)},PAGE.scripts)`);
  const body=app.elements.get('content').innerHTML;
  const next=structuredClone(p);
  next.job.status='completed';next.job.run.status='completed';
  next.episodes=[{...previewEpisode('ep_001','reviewed','Final checked dialogue.'),preview:false,readable_hash:'final',audio:[]}];
  app.responses.set('/api/projects/test',next);
  await app.run('poll()');
  assert.equal(app.elements.get('content').innerHTML,body);
  assert.ok(app.elements.get('script-reader-controls').innerHTML.includes('Aktuellen Stand laden'));
  app.run('readingSnapshot=null;$("content").innerHTML=renderScript();');
  assert.ok(app.elements.get('content').innerHTML.includes('Final checked dialogue.'));
  assert.ok(app.elements.get('content').innerHTML.includes('Weiter zur Audio-Freigabe'));
});

test('preview selection is confined to the current run and a project switch clears the old reader',async()=>{
  const app=studio(), p=workflowProject(app);
  p.job.run.run_id='run_one';p.job.progress={phase:'script',script_previews:[previewEpisode()]};
  await app.run(`selectProject('test',${JSON.stringify(p)},PAGE.scripts)`);
  assert.equal(app.run('readableScripts().length'),1);
  app.run(`project.job.progress.script_previews[0].run_id='run_other';`);
  assert.equal(app.run('readableScripts().length'),0);
  await app.run(`selectProject('other',{...${JSON.stringify(p)},id:'other',job:null},PAGE.scripts)`);
  assert.equal(app.run('readingSnapshot'),null);
  assert.ok(!app.elements.get('content').innerHTML.includes('A first saved explanation.'));
});
test('an existing active project opens its real workflow page, and explicit reading pages are restored',async()=>{
  const app=studio(), p=workflowProject(app);
  await app.run(`selectProject('test',${JSON.stringify(p)})`);
  assert.equal(app.run('step'),3);
  assert.ok(app.elements.get('content').innerHTML.includes('id="production-progress"'));
  assert.ok(app.elements.get('production-progress').innerHTML.includes('Dialog-Polishing'));
  await app.run(`selectProject('test',${JSON.stringify(p)},PAGE.research)`);
  assert.equal(app.run('step'),1);
  assert.ok(app.elements.get('job-status').innerHTML.includes('Ausarbeitung ansehen'));
  assert.ok(!app.elements.get('job-status').innerHTML.includes('production-stages'));
  assert.equal(app.requests.filter(r=>r.options?.method==='POST').length,0);
});
test('navigation distinguishes approved, automatic, reading and audio steps',()=>{
  const app=studio(), p=workflowProject(app);
  app.run(`project=${JSON.stringify(p)};step=PAGE.production;renderNavigation();`);
  const html=app.elements.get('steps').innerHTML;
  assert.equal((html.match(/class="step /g)||[]).length,6);
  assert.ok(html.includes('Freigegeben'));
  assert.ok(html.includes('Läuft automatisch'));
  assert.ok(html.includes('Sobald ein Entwurf fertig ist'));
  assert.ok(html.includes('aria-current="page"'));
  assert.ok(app.run('renderScript()').includes('Zur Ausarbeitung'));
  assert.ok(!app.run('renderScript()').includes('Gib zuerst das Inhaltsverzeichnis frei'));
  assert.ok(app.run('renderAudio()').includes('data-step="4"'));
  const outline=app.run('renderOutline()');
  assert.ok(outline.includes('Ausarbeitung ansehen'));
  assert.ok(!outline.includes('data-action="script"'));
  assert.ok(!app.run('renderResearch()').includes('data-action="plan"'));
});
test('resume routes by the actual run kind and stage, including failures and auxiliary jobs',()=>{
  const app=studio(), p=workflowProject(app);
  for(const [kind,status,stages,page] of [
    ['script','running',{planning:{status:'running'}},2],
    ['script','blocked',{teaching:{status:'blocked',attempts:1}},3],
    ['script','running',{polishing:{status:'running',attempts:1}},3],
    ['script','completed',{publish:{status:'completed'}},4],
    ['research','completed',{dossier:{status:'completed'}},1],
    ['episode_audio','running',{synthesis:{status:'running'}},5],
  ]) {
    const candidate=structuredClone(p);
    candidate.outline.approval=null;
    candidate.job.status=status;candidate.job.run={kind,status,stages};
    assert.equal(app.run(`recommendedPage(${JSON.stringify(candidate)})`),page);
  }
  p.job.action='audio_samples';p.job.run=null;p.run={kind:'script',status:'completed'};
  assert.equal(app.run(`recommendedPage(${JSON.stringify(p)})`),0);
});
test('finishing a resumed script advances to reading while a manually chosen page stays put',async()=>{
  for(const manual of [false,true]) {
    const app=studio(), p=workflowProject(app);
    await app.run(`selectProject('test',${JSON.stringify(p)})`);
    if(manual)app.run('navigatePage(PAGE.research);');
    const finished=structuredClone(p);
    finished.job.status='completed';finished.job.run.status='completed';
    finished.episodes=[{script:{episode_id:'ep_001',title:'One',chapters:[],segments:[]},metrics:{words:100,estimated_minutes:1},audio:[]}];
    app.responses.set('/api/projects/test',finished);
    await app.run('poll()');
    assert.equal(app.run('step'),manual?1:4);
    if(!manual)assert.ok(app.elements.get('content').innerHTML.includes('data-step="5"'));
    assert.equal(app.requests.filter(r=>r.options?.method==='POST').length,0);
  }
});
test('a starting job routes before its worker has written a run manifest',()=>{
  const app=studio(), p=workflowProject(app);
  p.job.run=null;p.run={kind:'research',status:'completed'};p.job.action='script';
  assert.equal(app.run(`recommendedPage(${JSON.stringify(p)})`),3);
  p.job.action='plan';
  assert.equal(app.run(`recommendedPage(${JSON.stringify(p)})`),2);
});
test('browser history restores the requested page without starting work',async()=>{
  const app=studio(), p=workflowProject(app);
  await app.run(`selectProject('test',${JSON.stringify(p)})`);
  app.run(`boot.projects=[{id:'test'}];window.location={search:'?project=test&step=research'};`);
  await app.events.get('popstate')();
  assert.equal(app.run('step'),1);
  assert.equal(app.run('followWorkflow'),false);
  assert.equal(app.requests.filter(r=>r.options?.method==='POST').length,0);
});
test('polling the production page keeps a teaching preview open at its reading position',()=>{
  const app=studio(), p=workflowProject(app);
  p.job.progress={phase:'script',stage:'teaching',activity:'Prüfung',completed_segments:1,total_segments:2,
    episodes:[{episode_id:'ep_001',title:'One',completed:true,teaching_preview:'Accepted concept'}]};
  app.run(`project=${JSON.stringify(p)};step=PAGE.production;renderJob();`);
  const box=app.elements.get('production-progress');
  let markup=box.innerHTML, detail={dataset:{progressEpisode:'ep_001'},open:true}, preview={dataset:{progressPreview:'ep_001'},scrollTop:180};
  box.querySelectorAll=selector=>selector.includes('data-progress-preview')?[preview]:selector.includes('[open]')?(detail.open?[detail]:[]):[detail];
  Object.defineProperty(box,'innerHTML',{get:()=>markup,set:value=>{
    markup=value;detail={dataset:{progressEpisode:'ep_001'},open:false};preview={dataset:{progressPreview:'ep_001'},scrollTop:0};
  }});
  app.run('renderJob();');
  assert.equal(detail.open,true);
  assert.equal(preview.scrollTop,180);
});
test('the initial screen exposes a real brief, provider and both voice controls',()=>{
  const app=studio(),html=app.run('renderBrief()');
  for(const id of ['brief-form','topic','central_question','provider','model','api-key','host_a','host_b']) assert.ok(html.includes(`id="${id}"`));
  assert.ok(html.includes('Projekt anlegen'));
  assert.ok(html.includes('Codex · bestehendes ChatGPT-Abo'));
});
test('a plan is readable and approval is an explicit action, with hostile model text escaped',()=>{
  const app=studio();
  app.run(`project={config:boot.defaults,outline:{hash:'h',plan:{central_question:'<script>bad()</script>',explanation_path:'Foundations first',scope_note:'Scope',episodes:[{title:'Begin here',central_question:'Why?',target_minutes:20,scenes:[{title:'Mechanism',question:'How?',explanation_steps:['First step']}],deferred_questions:[]}]}}}`);
  const html=app.run('renderOutline()');
  assert.ok(html.includes('Plan freigeben & Skripte schreiben'));
  assert.ok(html.includes('First step'));
  assert.ok(!html.includes('<script>bad()'));
  assert.ok(html.includes('&lt;script&gt;'));
});
test('audio starts disabled, names selected voices and marks an old recording',()=>{
  const app=studio();
  app.run(`project={config:boot.defaults,id:'test',episodes:[{script:{title:'A useful episode',episode_id:'ep_001'},audio:['exports/ep_001/run_test/audio.mp3'],audio_current:false}]}`);
  const html=app.run('renderAudio()');
  assert.ok(html.includes('id="audio-start" data-action="audio" disabled'));
  assert.ok(html.includes('id="audio-approval" type="checkbox"'));
  assert.ok(html.includes('Aiden & Vivian'));
  assert.ok(html.includes('früheren Skript- oder Stimmenstand'));
  assert.ok(html.includes('/media/test/exports/ep_001/run_test/audio.mp3'));
});
test('optional agent navigation uses visible state and cannot approve a job',async()=>{
  const app=studio();
  assert.deepEqual([...app.registered.keys()],['read_podcast_workspace','navigate_podcast_step']);
  const navigate=app.registered.get('navigate_podcast_step');
  assert.throws(()=>navigate.execute({step:7}));
  assert.equal(navigate.execute({step:3}).step,'Inhaltsverzeichnis');
  assert.equal(app.run('step'),2);
  assert.equal(app.registered.get('read_podcast_workspace').execute().job,null);
  assert.equal(app.requests.filter(r=>r.options?.method==='POST').length,0);
});
test('API mutations carry the session token and no key is persisted by browser storage',async()=>{
  const app=studio();
  await app.run(`api('/api/projects/test/start',{action:'research'})`);
  const request=app.requests.find(r=>r.path.includes('/start'));
  assert.equal(request.options.headers['X-Studio-Token'],'csrf');
  assert.equal(request.options.method,'POST');
  assert.ok(!source.includes('localStorage'));
  assert.ok(!source.includes('sessionStorage'));
});
test('Gemini audio offers its own voices while Codex remains the writer',()=>{
  const app=studio();
  app.run(`boot.audio_catalog={qwen3_local:{label:'Qwen',voices:['Aiden','Vivian'],defaults:{host_a:'Aiden',host_b:'Vivian'}},openrouter_gemini_tts:{label:'Gemini 3.1 Flash TTS · OpenRouter',voices:['Sadaltager','Aoede','Charon'],defaults:{host_a:'Sadaltager',host_b:'Aoede'}}};
    project={config:boot.defaults,text:{provider:'codex_cli',model:null,max_output_tokens:32768},audio_settings:{provider:'openrouter_gemini_tts',voices:{host_a:'Sadaltager',host_b:'Aoede'}},chat:[]};`);
  const html=app.run('renderBrief()');
  assert.ok(html.includes('id="tts-provider"'));
  assert.ok(html.includes('value="openrouter_gemini_tts" selected'));
  assert.ok(html.includes('value="Sadaltager" selected'));
  assert.ok(html.includes('value="Aoede" selected'));
  assert.ok(html.includes('value="codex_cli" selected'));
  assert.ok(html.includes('Hörprobe erzeugen · API'));
  assert.ok(!html.includes('id="key-settings" hidden'));
});
test('Gemini approval names the remote provider, chosen voices and API charge',()=>{
  const app=studio();
  app.run(`project={config:boot.defaults,id:'test',audio_settings:{provider:'openrouter_gemini_tts',voices:{host_a:'Sadaltager',host_b:'Aoede'}},episodes:[{script:{title:'Episode',episode_id:'ep_001'},audio:[]}]}`);
  const html=app.run('renderAudio()');
  assert.ok(html.includes('Gemini 3.1 Flash TTS · OpenRouter'));
  assert.ok(html.includes('Sadaltager & Aoede'));
  assert.ok(html.includes('API-Guthaben'));
  assert.ok(html.includes('id="audio-start" data-action="audio" disabled'));
});
test('polling an unchanged finished sample preserves its player, and leaving clears it',()=>{
  const app=studio();
  app.run(`project={id:'test',config:boot.defaults,job:{id:'sample-job',action:'audio_sample',status:'completed',sample:{voice:'Sadaltager',language:'de-DE',audio:'studio/samples/de-DE/Sadaltager/audio.mp3'}}};renderJob();`);
  const box=app.elements.get('job-status');
  let markup=box.innerHTML,writes=0;
  Object.defineProperty(box,'innerHTML',{get:()=>markup,set:value=>{writes++;markup=value;}});
  app.run('project.job=structuredClone(project.job);renderJob();renderJob();');
  assert.equal(writes,0,'An unchanged job must not replace a playing audio element');
  assert.ok(markup.includes('Hörprobe separat öffnen'));
  assert.ok(markup.includes('MP3 herunterladen'));
  app.run("project.job.sample.voice='Aoede';project.job.sample.audio='studio/samples/de-DE/Aoede/audio.mp3';renderJob();");
  assert.ok(writes>0);
  assert.ok(markup.includes('Aoede'));
  app.run('project=null;renderJob();');
  assert.equal(markup,'');
  assert.equal(box.hidden,true);
});

test('voice library lists every voice with playback only for saved samples in the selected language',()=>{
  const app=studio();
  app.run(`boot.audio_catalog={openrouter_gemini_tts:{voices:['Aoede','Puck','Sadaltager']}};
    project={voice_samples:{'de-DE':{Aoede:{url:'/samples/gemini/de-DE/Aoede'}}}};`);
  const html=app.run(`renderVoiceLibrary('de-DE')`);
  assert.ok(html.includes('1 / 3 gespeichert'));
  assert.ok(html.includes('data-play-voice="Aoede"'));
  assert.ok(!html.includes('data-play-voice="Puck"'));
  assert.ok(html.includes('2 fehlende Hörproben erzeugen · API'));
  const english=app.run(`renderVoiceLibrary('en-US')`);
  assert.ok(english.includes('0 / 3 gespeichert'));
  assert.ok(!english.includes('data-play-voice='));
  app.run(`project.voice_samples['de-DE'].Puck={url:'p'};project.voice_samples['de-DE'].Sadaltager={url:'s'};`);
  assert.ok(!app.run(`renderVoiceLibrary('de-DE')`).includes('data-action="audio_samples"'));
});

test('Play and Pause reuse the recording without a generation request or key, including across re-render',async()=>{
  const app=studio();
  app.run(`project={id:'test',config:boot.defaults,voice_samples:{'de-DE':{Aoede:{url:'/samples/gemini/de-DE/Aoede'}}}};`);
  await app.run(`playSample('Aoede','de-DE')`);
  const player=app.elements.get('sample-player');
  assert.equal(player.src,'/samples/gemini/de-DE/Aoede');
  assert.equal(player.paused,false);
  assert.equal(app.elements.get('sample-playback').hidden,false);
  player.currentTime=3;
  app.run('render();');
  assert.equal(player.currentTime,3);
  assert.equal(player.paused,false);
  await app.run(`playSample('Aoede','de-DE')`);
  assert.equal(player.paused,true);
  await assert.rejects(app.run(`playSample('Puck','de-DE')`));
  assert.equal(app.requests.filter(r=>r.options?.method==='POST').length,0);
});
