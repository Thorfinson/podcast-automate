// Dependency-free UI contract tests. No browser, model calls or audio generation.
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('src/podcast_automate/web/app.js', 'utf8');
test('script progress shows the active episode and readable results without audio counts',()=>{
  const app=studio();
  app.run(`project={id:'test',job:{status:'running',action:'resume',started_at:new Date().toISOString(),progress:{phase:'script',stage:'teaching',current_episode:'ep_002',episode_number:2,episode_title:'Attention',activity:'Lehrkonzept wird geprüft',activity_started_at:new Date().toISOString(),completed_segments:1,total_segments:6,episodes:[{episode_id:'ep_001',title:'Introduction',completed:true,teaching_preview:'A safe <script> excerpt'},{episode_id:'ep_002',title:'Attention',completed:false,teaching_preview:''}]},run:{stages:{teaching:{status:'running'}}}}};renderJob();`);
  const html=app.elements.get('job-status').innerHTML;
  assert.ok(html.includes('Folge 2 von 6'));
  assert.ok(html.includes('1 von 6 Folgen'));
  assert.ok(html.includes('Lehrkonzept lesen'));
  assert.ok(html.includes('A safe &lt;script&gt; excerpt'));
  assert.ok(!html.includes('Sprechabschnitten'));
});
test('choosing a project keeps its identity in the URL for reloads without browser storage',async()=>{
  const app=studio();
  app.run(`window.history={replaceState(state,title,url){window.savedProjectUrl=url;}};`);
  await app.run(`selectProject('example',{id:'example',config:boot.defaults,text:{provider:'codex_cli',model:null,max_output_tokens:32768},chat:[]})`);
  assert.equal(app.run('window.savedProjectUrl'),'/?project=example');
  assert.equal(app.elements.get('project-select').value,'example');
});
test('a stopped teaching review shows its concrete issues instead of a path and futile retry',()=>{
  const app=studio();
  app.run(`project={id:'test',job:{status:'blocked',action:'resume',message:'Review: C:/private/checkpoint.json',progress:{phase:'script',stage:'teaching',activity:'Lehrkonzept wird geprüft',current_episode:'ep_002',episode_number:2,episode_title:'Second lesson',completed_segments:1,total_segments:6,episodes:[],review_issues:['Explain the Key projection.']},run:{stages:{teaching:{status:'blocked',error:{code:'teaching_design_failed'}}}}}};renderJob();`);
  const html=app.elements.get('job-status').innerHTML;
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
  html=app.elements.get('job-status').innerHTML;
  assert.ok(html.includes('What changes &lt;script&gt;?'));
  assert.ok(html.includes('Missing mechanism'));
  assert.ok(!html.includes('C:/private'));
  assert.ok(!html.includes('data-action="resume"'));
});
function studio() {
  const elements = new Map(), registered = new Map(), requests = [];
  const element = id => {
    if (!elements.has(id)) elements.set(id, {value:'',innerHTML:'',hidden:false,textContent:'',paused:true,focus(){},addEventListener(){},async play(){this.paused=false;},pause(){this.paused=true;}});
    return elements.get(id);
  };
  const defaults = {topic:'New project',central_question:'Why?',voice_profile:{host_a:'Aiden',host_b:'Vivian'},language:'de-DE',prior_knowledge:'',depth_request:'Deep',focus_questions:[],excluded_topics:[],seed_urls:[]};
  const context = vm.createContext({console,structuredClone,AbortController,encodeURIComponent,setInterval(){},window:{addEventListener(){},scrollTo(){}},
    document:{getElementById:element,querySelectorAll(){return [];},addEventListener(){},modelContext:{registerTool(tool){registered.set(tool.name,tool);}}},
    fetch:async(path,options)=>{requests.push({path,options});return{ok:true,json:async()=>({token:'csrf',voices:['Aiden','Vivian'],projects:[],defaults})};}});
  vm.runInContext(source, context);
  const run = code => vm.runInContext(code,context);
  run(`boot=${JSON.stringify({token:'csrf',voices:['Aiden','Vivian'],projects:[],defaults})}`);
  return {run,context,elements,registered,requests};
}
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
  assert.throws(()=>navigate.execute({step:6}));
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
