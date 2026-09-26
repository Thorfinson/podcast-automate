// Dependency-free UI contract tests. No browser, model calls or audio generation.
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('src/podcast_automate/web/app.js', 'utf8');

test('live model traces show only the last twenty escaped lines beside the summary',()=>{
  const app=studio();
  const rows=Array.from({length:25},(_,i)=>({at:new Date().toISOString(),call:'call_'+i,model:'Astra',kind:'reasoning',text:'line-'+i+' <script>untrusted</script>'}));
  app.run(`project={id:'test',job:{id:'j1',status:'running',progress:{phase:'research',model_trace:{updated_at:new Date().toISOString(),lines:${JSON.stringify(rows)}}}}};renderJob();`);
  const html=app.elements.get('job-status').innerHTML;
  assert.ok(html.includes('Gerade in Arbeit'));
  assert.ok(html.includes('Öffentliche Reasoning-Zusammenfassung'));
  assert.ok(html.includes('line-24 &lt;script&gt;'));
  assert.ok(!html.includes('line-4 &lt;script&gt;'));
  assert.ok(!html.includes('<script>untrusted'));
  assert.ok(app.elements.get('research-live').innerHTML.includes('<p class="live-text">line-24 &lt;script&gt;untrusted&lt;/script&gt;</p>'),'the newest line stands in the rail');
  app.run("project.job.status='failed';renderJob()");
  assert.equal(app.elements.get('research-live').innerHTML,'','a stale line leaves the rail');
  assert.ok(app.elements.get('job-status').innerHTML.includes('Letzte Arbeitsschritte'));
  app.run("project.job.progress.model_trace=null;renderJob()");
  assert.ok(app.elements.get('job-status').innerHTML.includes('Noch keine Meldungen verfügbar'));
});

test('live panel identifies current question and does not imply old text belongs to this call',()=>{
  const app=studio();
  app.run(`project={job:{status:'running',progress:{phase:'research',model_call_started_at:'2026-09-16T12:00:00Z',
    research_questions:{active_task:'q1',questions:[{id:'q1',question:'Wie wirkt <Kontext>?',activity:'Belege vergleichen'}]},
    model_trace:{lines:[{kind:'text',at:'2026-09-16T11:00:00Z',text:'Frühere Suche'}]}}}};`);
  const html=app.run('renderModelTrace(project.job)');
  assert.ok(html.includes('Wie wirkt &lt;Kontext&gt;?'));
  assert.ok(html.includes('Belege vergleichen'));
  assert.ok(html.includes('Inhaltliche Zwischenmeldungen liegen dafür noch nicht vor'));
  assert.ok(!html.includes('<pre>'));
});

test('research work explanation separates saved input, waiting, and checked progress',()=>{
  const app=studio();
  app.run(`project={job:{status:'running',progress:{phase:'research',updated_at:new Date().toISOString(),work_insight:{
    basis:'saved_state',question:'Causation <script>',assignment:'Definitionen anhand der Quellen prüfen.',
    last_step:'Gemeinsamer Einfluss fehlt.',feedback:['Vergleich erklären.'],criteria:['Definition prüfen.'],
    material:{section_count:6,source_count:2,sources:[{title:'Original <paper>',sections:3,pages:[6]}]},candidate_count:48,
    signals:{state:'running',started_at:new Date(Date.now()-900000).toISOString(),last_event_at:new Date(Date.now()-900000).toISOString(),last_content_at:null,timeout_seconds:1800}
  }}}};`);
  let html=app.run('renderModelTrace(project.job)');
  for(const text of ['Causation &lt;script&gt;','Gemeinsamer Einfluss fehlt.','Vergleich erklären.',
    '6 Textstellen aus 2 Quellen','48 Suchtreffer','Seit 15 Min. noch keine inhaltliche Zwischenmeldung',
    'Aus dem gespeicherten Recherchestand rekonstruiert','30 Min.'])assert.ok(html.includes(text),text);
  assert.ok(html.includes('work-signals quiet'));
  assert.ok(html.includes('<details class="model-events">'),'the assignment folds under the live output');
  assert.ok(html.includes('Live-Ausgabe · letzte 20 Meldungen'));
  assert.ok(html.indexOf('Noch keine Meldungen')<html.indexOf('Causation &lt;script&gt;'),'the live output comes first');
  assert.ok(!html.includes('Das Modell ist abgestürzt'));
  assert.ok(!html.includes('<script>'));
  app.run("project.job.status='interrupted'");
  html=app.run('renderModelTrace(project.job)');
  assert.ok(html.includes('der Auftrag läuft derzeit nicht'));
  assert.ok(!html.includes('work-signals quiet'));
  assert.ok(!html.includes('Seit 15 Min. noch keine'));
});

test('continued hidden output is distinguished from readable text even with an older worker',()=>{
  const app=studio();
  app.run(`project={job:{status:'running',progress:{phase:'research',work_insight:{assignment:'Nächste Lesestelle auswählen.',
    signals:{call:'call_033',state:'running',started_at:new Date(Date.now()-300000).toISOString(),
      last_content_at:new Date().toISOString()}},model_trace:{lines:[
        {call:'call_033',kind:'text',text:'Weitere Quellenabschnitte lesen',at:new Date(Date.now()-240000).toISOString()}
      ]}}}};`);
  let html=app.run('renderModelTrace(project.job)');
  assert.ok(html.includes('Seit 4 Min. kein neuer lesbarer Modelltext'));
  assert.ok(html.includes('Ausgabe kommt weiter an, aber ohne neuen lesbaren Text'));
  assert.ok(html.includes('Empfang allein belegt keinen Recherchefortschritt'));
  assert.ok(html.includes('Letztes empfangenes Fragment: vor 0 Sek.'));
  app.run("project.job.progress.work_insight.signals.last_content_at=new Date(Date.now()-120000).toISOString()");
  html=app.run('renderModelTrace(project.job)');
  assert.ok(!html.includes('Ausgabe kommt weiter an, aber ohne neuen lesbaren Text'));
  app.run("project.job.status='interrupted'");
  html=app.run('renderModelTrace(project.job)');
  assert.ok(!html.includes('Letztes empfangenes Fragment'));
});

test('question research shows fixed criteria, verified answers and specific blocks safely',()=>{
  const app=studio();
  app.run(`project={id:'test',job:{status:'interrupted',action:'research',progress:{phase:'research',
    research_questions:{closed:1,total:2,phase:'questions',active_task:'empirical',questions:[
      {id:'definition',question:'Definition <script>',status:'verified',activity:'Geprüft',steps:2,read_sections:4,
       acceptance:['Describe <scope>'],answer:'**Supported answer**',limits:['Limited scope'],findings:[]},
      {id:'empirical',question:'Independent test?',status:'blocked',steps:3,read_sections:5,
       acceptance:['Find original test'],answer:'Unverified must stay hidden',reason:'Missing original study',findings:[]}]},
    research_quality:{closed:0,total:1,requirements:[],blocking_gaps:[]}}}};renderJob();`);
  const html=jobView(app);
  assert.ok(html.includes('1 von 2 Teilfragen geprüft abgeschlossen'));
  assert.ok(html.includes('<strong>Supported answer</strong>'));
  assert.ok(html.includes('Missing original study'));
  assert.ok(html.includes('Describe &lt;scope&gt;'));
  assert.ok(html.includes('Definition &lt;script&gt;'));
  assert.ok(html.includes('Gesamtbewertung folgt'));
  assert.ok(!html.includes('Unverified must stay hidden'));
});

test('expanded question remains open in the regenerated status panel',()=>{
  const app=studio();
  app.run(`project={id:'test',job:{status:'interrupted',progress:{phase:'research',research_questions:{
    closed:0,total:1,phase:'questions',questions:[{id:'q_one',question:'One',status:'researching',
    steps:1,read_sections:2,acceptance:['Explain']} ]}}}};renderJob();`);
  app.elements.get('research-progress').querySelectorAll=()=>[{dataset:{researchQuestion:'q_one'}}];
  app.run('project.job.progress.research_questions.questions[0].steps=2;renderJob();');
  assert.ok(jobView(app).includes('data-research-question="q_one" open'));
});

function blockedResearch(app, extra='') {
  app.run(`project={id:'test',job:{status:'blocked',progress:{phase:'research',updated_at:'2026-09-26T20:00:00Z',${extra}research_questions:{
    closed:0,total:1,phase:'blocked',questions:[{id:'q_one',question:'One',status:'blocked',outcome:'evidence_block',reason:'Fehlt.',
    steps:1,read_sections:2,acceptance:['Explain']}]}}}};renderJob();`);
  return app.elements.get('research-progress');
}

test('a poll that brings only a new read time leaves the research page untouched',()=>{
  const app=studio(), panel=blockedResearch(app);
  let markup=panel.innerHTML, writes=0;
  Object.defineProperty(panel,'innerHTML',{get:()=>markup,set:value=>{markup=value;writes++;}});
  app.run("project.job.progress.updated_at='2026-09-26T20:00:02Z';project.job.heartbeat_age_seconds=4;renderJob();");
  assert.equal(writes,0,'the read time and heartbeat age alone redraw nothing');
  app.run("project.job.progress.research_questions.questions[0].reason='Fehlt weiterhin.';renderJob();");
  assert.equal(writes,1);
});

test('a redraw keeps every section the reader opened or closed in the research panel',()=>{
  const app=studio(), panel=blockedResearch(app,"retrieval:{failed:1,failures:[{source:'https://a.example',reason:'HTTP 403'}]},");
  assert.ok(panel.innerHTML.includes('class="retrieval-report"'));
  let markup=panel.innerHTML, report={dataset:{},className:'retrieval-report',open:true}, quality={dataset:{},className:'tech-details',open:false};
  panel.querySelectorAll=selector=>selector==='details'?[report,quality]:[];
  Object.defineProperty(panel,'innerHTML',{get:()=>markup,set:value=>{
    markup=value;report={dataset:{},className:'retrieval-report',open:false};quality={dataset:{},className:'tech-details',open:true};
  }});
  app.run("project.job.progress.research_questions.questions[0].reason='Fehlt weiterhin.';renderJob();");
  assert.equal(report.open,true,'an opened report stays open');
  assert.equal(quality.open,false,'a closed section stays closed');
});

test('the advisor’s cause, recommendation, sources and hint stand with the blocked question',()=>{
  const app=studio();
  const advice={diagnosis:'Der Verlag sperrt den Download <PNAS>.',recommendation:'raise_limit',limit:'sources',hint:'Freie Fassung in PubMed Central lesen.',
    sources:[{title:'Salganik 2020',url:'https://pmc.example/salganik',note:'frei lesbar'},{title:'Ohne Adresse',url:'',note:''}]};
  const ledger={closed:0,total:1,accepted:0,phase:'blocked',questions:[{id:'t18',question:'Vorhersage?',status:'blocked',outcome:'evidence_block',
    reason:'Kriterium 1 fehlt.',web_attempts:1,steps:9,read_sections:3,acceptance:['k'],reopened:0,advice,auto_retries:1}]};
  const card=app.run(`renderResearchDecisions({status:'blocked',progress:{research_questions:${JSON.stringify(ledger)},search_rounds:1,search_round_limit:24,source_limit:150}},{run_id:'run_x'},false,0,true,24)`);
  assert.ok(card.includes('<strong>Beratung:</strong> Der Verlag sperrt den Download &lt;PNAS&gt;.'));
  assert.ok(card.includes('Empfehlung: Quellenlimit erhöhen · Ein automatischer neuer Versuch nach der Beratung lief bereits.'));
  assert.ok(card.includes('<a href="https://pmc.example/salganik" target="_blank" rel="noopener noreferrer">Salganik 2020</a>'));
  assert.ok(card.includes('<li>Ohne Adresse</li>'));
  // The advisor's hint is already in the field of a new attempt; the editor may change it before retrying.
  assert.ok(card.includes('id="retry-hint-t18" value="Freie Fassung in PubMed Central lesen."'));
  const html=app.run(`renderResearchQuestions(${JSON.stringify(ledger)},new Set(),false,'run_x',24,1,150)`);
  assert.ok(html.includes('<p><strong>Beratung:</strong> Der Verlag sperrt den Download &lt;PNAS&gt;.</p>'));
});

test('a block without advice offers to resume, because the run asks the advisor first',()=>{
  const app=studio();
  const ledger={closed:1,total:2,accepted:0,phase:'blocked',questions:[
    {id:'a',question:'Beantwortet',status:'verified',activity:'ok',steps:2,read_sections:3,acceptance:['x'],answer:'Antwort',findings:[],sources:[],limits:[],reopened:0},
    {id:'b',question:'Offen?',status:'blocked',outcome:'evidence_block',web_attempts:1,reason:'Fehlt.',activity:'x',steps:5,read_sections:2,acceptance:['k'],reopened:0}]};
  app.run(`project={id:'p',config:boot.defaults,job:{id:'j1',status:'blocked',action:'research',started_at:new Date().toISOString(),run:{run_id:'run_x',kind:'research',stages:{}},progress:{phase:'research',research_questions:${JSON.stringify(ledger)},search_round_limit:24}}};step=PAGE.research;render();`);
  assert.ok(jobView(app).includes('>Fortsetzen<'));
  assert.ok(jobView(app).includes('„Fortsetzen“ lässt sie zuerst beraten'));
  // Advice for the current block: no blind resume, the decision is the editor's.
  app.run("project.job.progress.research_questions.questions[1].advice={key:'0.0',diagnosis:'d',recommendation:'accept_gap',limit:'none',hint:'',sources:[]};lastJobView='';render();");
  assert.ok(!jobView(app).includes('>Fortsetzen<'));
  // A new attempt is a new block; the advice from before no longer counts.
  app.run("project.job.progress.research_questions.questions[1].retries=1;lastJobView='';render();");
  assert.ok(jobView(app).includes('>Fortsetzen<'));
});

test('the machine room and the overview keep opened sections while polling',()=>{
  const app=studio();
  blockedResearch(app);
  const box=app.elements.get('job-status');
  let markup=box.innerHTML, events={dataset:{},className:'model-events',open:true};
  box.querySelectorAll=selector=>selector==='details'?[events]:[];
  Object.defineProperty(box,'innerHTML',{get:()=>markup,set:value=>{markup=value;events={dataset:{},className:'model-events',open:false};}});
  app.run("project.job.progress.research_questions.questions[0].reason='Fehlt weiterhin.';renderJob();");
  assert.equal(events.open,true,'an opened section of the machine room stays open');
  // A new message redraws the machine room; the reader's place in it stays where it was.
  let body={className:'drawer-body',scrollTop:320};
  box.querySelectorAll=selector=>selector==='details'?[events]:selector==='[class]'?[body]:[];
  Object.defineProperty(box,'innerHTML',{get:()=>markup,set:value=>{markup=value;body={className:'drawer-body',scrollTop:0};}});
  app.run("project.job.progress.research_questions.questions[0].reason='Noch immer offen.';renderJob();");
  assert.equal(body.scrollTop,320,'the machine room keeps its scroll position');
  // The browser marks an opened trash with open=""; an unchanged trash must not be redrawn, and closed, by the next poll.
  app.run(`overviewData={projects:[],trash:[{id:'${'a'.repeat(32)}',topic:'Alt',deleted_at:'2026-09-26'}]};refreshOverview();`);
  const trash=app.elements.get('overview-trash');
  assert.ok(trash.innerHTML.includes('<details class="panel">'));
  trash.innerHTML=trash.innerHTML.replace('<details class="panel">','<details class="panel" open="">');
  app.run('refreshOverview();');
  assert.ok(trash.innerHTML.includes('<details class="panel" open="">'));
});

test('evidence status distinguishes automated support, empirical testing and supported uncertainty',()=>{
  const app=studio();
  app.run(`project={id:'test',job:{status:'running',action:'research',progress:{phase:'research',
    research_questions:{closed:1,total:1,phase:'questions',questions:[
      {id:'task_one',question:'What remains unresolved?',status:'verified',steps:2,read_sections:3,
       acceptance:['Explain limits'],answer:'Supported uncertainty',findings:[],outcome:'supported_uncertainty',
       support:{findings:[{empirical_status:'tested_in_source'},{empirical_status:'independently_tested'}]}}]}}}};renderJob();`);
  const html=jobView(app);
  assert.ok(html.includes('Textbelege vorhanden'));
  assert.ok(html.includes('Inhalt automatisch je Befund geprüft'));
  assert.ok(html.includes('1 Befunde mit dokumentierter unabhängiger empirischer Prüfung'));
  assert.ok(html.includes('Belegte wissenschaftliche Unsicherheit'));
});

test('research call projection explains completion reserve and insufficient allowance',()=>{
  const app=studio();
  const ledger={closed:1,total:77,phase:'questions',questions:[],budget_projection:{
    minimum_remaining_calls:174,closing_calls:22,remaining:217,shortfall:0,feasible:true}};
  let html=app.run(`renderResearchQuestions(${JSON.stringify(ledger)})`);
  assert.ok(html.includes('Mindestens 174 weitere Modellaufrufe'));
  assert.ok(html.includes('22 für Dossier und Abschlussprüfung; 217 verfügbar'));
  assert.ok(html.includes('können mehr benötigen'));
  ledger.budget_projection={minimum_remaining_calls:157,closing_calls:3,remaining:147,shortfall:10,feasible:false};
  html=app.run(`renderResearchQuestions(${JSON.stringify(ledger)})`);
  assert.ok(html.includes('um mindestens 10 Aufrufe nicht aus'));
  assert.ok(html.includes('Antworten und Umfang bleiben erhalten'));
  assert.ok(html.includes('ausdrückliche Genehmigung'));
});

test('exhausted research questions explain the block without a futile resume button',()=>{
  const app=studio();
  app.run(`project={id:'test',job:{status:'blocked',action:'research',run:{kind:'research',stages:{}},
    progress:{phase:'research',research_questions:{closed:1,total:2,phase:'blocked',questions:[]}}}};renderJob();`);
  const html=jobView(app);
  assert.ok(html.includes('Fortsetzen allein wiederholt diese Versuche nicht'));
  assert.ok(!html.includes('data-step="0"'),'no detour to the brief page, where nothing about the block can be decided');
  assert.ok(!html.includes('data-action="resume"'));
});

test('research progress displays missing requirements safely and does not report speech segments',()=>{
  const app=studio();
  app.run(`project={id:'test',job:{status:'running',action:'research',started_at:new Date().toISOString(),
    run:{kind:'research',status:'running',stages:{completeness:{status:'running'}}},
    progress:{phase:'research',activity:'Offene Leitfragen werden recherchiert',model_calls:8,model_call_limit:150,
      total_segments:3,completed_segments:1,search_rounds:2,search_round_limit:12,
      research_quality:{closed:1,total:3,requirements:[{question:'<script>Question</script>',passed:false,reason:'Actual text missing',missing:['Read full chapter']}],blocking_gaps:['A further gap']}}}};renderJob();`);
  const html=jobView(app);
  assert.ok(html.includes('1 von 3 Leitfragen'));
  assert.ok(html.includes('2 von 12'));
  assert.ok(html.includes('Read full chapter'));
  assert.ok(html.includes('A further gap'));
  assert.ok(html.includes('&lt;script&gt;Question'));
  assert.ok(!html.includes('<script>Question'));
  assert.ok(!html.includes('Sprechabschnitten'));
});

test('evidence-first research labels the overall assessment as pending',()=>{
  const app=studio();
  app.run(`project={id:'test',job:{status:'running',action:'research',started_at:new Date().toISOString(),
    progress:{phase:'research',activity:'Quellen werden gesucht',research_quality:{closed:0,total:10,
      assessment_status:'pending_after_source_review',requirements:[],blocking_gaps:['Missing <chapter>']}}}};renderJob();`);
  const html=jobView(app);
  assert.ok(html.includes('Gesamtbewertung folgt'));
  assert.ok(html.includes('bisherigen Leitfragenbewertungen'));
  assert.ok(html.includes('Missing &lt;chapter&gt;'));
  assert.ok(!html.includes('0 von 10 Leitfragen erfüllen'));
});

test('new unfinished research cannot offer planning based on the previous dossier',()=>{
  const app=studio();
  app.run(`project={id:'test',config:boot.defaults,research:'The previous dossier',
    job:{status:'blocked',action:'research',run:{kind:'research',status:'blocked',stages:{completeness:{status:'blocked'}}}}};`);
  const html=app.run('renderResearch()');
  assert.ok(html.includes('Bisheriges Dossier'));
  assert.ok(html.includes('noch nicht abgeschlossen'));
  assert.ok(!html.includes('data-action="plan"'));
});

test('automatic subscription choice shows both models, the current provider and a switch',()=>{
  const app=studio(), p=workflowProject(app);
  p.text={provider:'auto',model:null,reasoning_effort:null};
  p.job.text_generation={provider:'auto',candidates:{codex_cli:{model:'gpt-6-astra',reasoning_effort:'xhigh'},claude_code:{model:'claude-opus-5',reasoning_effort:'high'}}};
  p.job.provider_choice={call:'call_003',provider:'claude_code',model:'claude-opus-5',mode:'auto',reason:'codex_exhausted_until x; claude_available',
    snapshots:{codex_cli:{available:false,usable:true,resets_at:'2026-09-22T20:31:18+00:00',windows:[{window_minutes:10080,used_percent:100}]},claude_code:{available:true,usable:true}},
    switch:{from:'codex_cli',to:'claude_code',error_code:'quota_exhausted'}};
  app.run(`boot.capabilities={conversational_setup:true};boot.text_catalog={auto_candidates:{codex_cli:{model:'gpt-6-astra',reasoning_effort:'xhigh'},claude_code:{model:'claude-opus-5',reasoning_effort:'high'}}};project=${JSON.stringify(p)};`);
  const brief=app.run('renderBrief()');
  assert.ok(brief.includes('Automatisch · Claude-Abo, sonst Codex-Abo'));
  assert.ok(brief.includes('claude-opus-5 (high)'));
  assert.ok(brief.includes('gpt-6-astra (xhigh)'));
  const status=app.run('renderRunTextChoice(project.job)');
  assert.ok(status.includes('Automatische Abo-Wahl'));
  assert.ok(status.includes('Aktueller Anbieter: Claude · claude-opus-5'));
  assert.ok(status.includes('Wechsel von Codex zu Claude'));
  assert.ok(status.includes('Codex ohne Kontingent (Wochenfenster 100 %)'));
  assert.ok(status.includes('Reset '));
  assert.ok(status.includes('Claude bereit'));
  app.run("project.job.provider_choice.provider='<script>x</script>';project.job.provider_choice.model='<b>m</b>';project.job.provider_choice.switch=null;project.job.provider_choice.snapshots={}");
  const hostile=app.run('renderRunTextChoice(project.job)');
  assert.ok(!hostile.includes('<script>')&&!hostile.includes('<b>'));
  assert.ok(hostile.includes('&lt;script&gt;'));
  app.run("project.job.provider_choice={provider:'codex_cli',model:'gpt-6-astra',mode:'fixed'}");
  assert.ok(app.run('renderRunTextChoice(project.job)').includes('Aktueller Anbieter: Codex · gpt-6-astra (fest gewählt)'));
  app.run("project.job.provider_choice=null");
  assert.ok(!app.run('renderRunTextChoice(project.job)').includes('Aktueller Anbieter'));
});

test('current and previous dossiers render readable Markdown without changing their contents',()=>{
  const app=studio();
  const dossier='# Ein Thema\n\nEin **belegter** Befund mit *Grenzen*.\n\n## Quellen\n\n- [Studie](https://example.org/paper?a=1&b=2) (`src_one#sec_two`)\n- [Meine Notiz](../sources/raw/note.txt)';
  app.run(`project={id:'test',config:boot.defaults,research:${JSON.stringify(dossier)},job:{action:'research',run:{kind:'research',status:'running'}}};`);
  let html=app.run('renderResearch()');
  assert.ok(html.includes('Bisheriges Dossier · wird neu recherchiert'));
  assert.ok(html.includes('<article class="markdown-document"><h3>Ein Thema</h3>'));
  assert.ok(html.includes('<strong>belegter</strong>'));
  assert.ok(html.includes('<em>Grenzen</em>'));
  assert.ok(html.includes('<h4>Quellen</h4>'));
  assert.ok(html.includes('<ul><li><p><a href="https://example.org/paper?a=1&amp;b=2"'));
  assert.ok(html.includes('<code>src_one#sec_two</code>'));
  assert.ok(html.includes('<li><p>Meine Notiz</p></li>'));
  assert.ok(!html.includes('<pre class="document">'));
  assert.equal(app.run('project.research'),dossier);
  app.run("project.job.run.status='completed'");
  html=app.run('renderResearch()');
  assert.ok(html.includes('Dein Recherche-Dossier'));
  assert.ok(html.includes('<h3>Ein Thema</h3>'));
});

test('dossier Markdown preserves code, quoted passages and nested numbered lists',()=>{
  const app=studio();
  const markdown='3. Eine **Frage**\n   - Ein Beleg\n   - Noch ein Beleg\n4. Die Grenze\n\n> Ein Zitat mit `**Originaltext**`.\n\n```html\n<script>unsafe()</script>\n```\n\n---\n\nEin Absatz\nauf zwei Zeilen.';
  const html=app.run(`renderMarkdown(${JSON.stringify(markdown)})`);
  assert.ok(html.includes('<ol start="3"><li><p>Eine <strong>Frage</strong></p>\n<ul>'));
  assert.ok(html.includes('</ul></li><li><p>Die Grenze</p></li></ol>'));
  assert.ok(html.includes('<blockquote><p>Ein Zitat mit <code>**Originaltext**</code>.</p></blockquote>'));
  assert.ok(html.includes('<pre><code>&lt;script&gt;unsafe()&lt;/script&gt;</code></pre>'));
  assert.ok(html.includes('<hr>'));
  assert.ok(html.includes('<p>Ein Absatz\nauf zwei Zeilen.</p>'));
});

test('Markdown source links handle parentheses and plain source URLs',()=>{
  const app=studio();
  const markdown='[Paper](https://example.org/paper_(2026)?a=1&b=2). Quelle: https://example.org/paper_(2026).';
  const html=app.run(`renderMarkdown(${JSON.stringify(markdown)})`);
  assert.ok(html.includes('href="https://example.org/paper_(2026)?a=1&amp;b=2" target="_blank" rel="noopener noreferrer">Paper</a>.'));
  assert.ok(html.includes('href="https://example.org/paper_(2026)"'));
  assert.ok(html.endsWith('</a>.</p>'));
});

test('Markdown cannot inject HTML, load images or create unsafe or local links',()=>{
  const app=studio();
  const markdown='<script>alert(1)</script>\n\n<img src=x onerror="alert(1)">\n\n[Bad](javascript:alert(1)) [File](file:///C:/secret) [Data](data:text/html,evil) [Encoded](jav&#x61;script:evil) [Local](../secret) [Relative](//evil.example) ![Tracking](https://evil.example/pixel) [Attribute](https://example.org/"onclick="evil)';
  const html=app.run(`renderMarkdown(${JSON.stringify(markdown)})`);
  assert.ok(!/<script|<img|<a\b|onclick="/.test(html));
  assert.ok(html.includes('&lt;script&gt;alert(1)&lt;/script&gt;'));
  assert.ok(html.includes('Bad File Data Encoded Local Relative Tracking Attribute'));
});

test('Markdown escapes link labels and leaves malformed syntax readable',()=>{
  const app=studio();
  const markdown='[<img onerror="evil">](https://example.org/)\n\nAn unfinished [link](target and **bold, `code, src_some_id.';
  const html=app.run(`renderMarkdown(${JSON.stringify(markdown)})`);
  assert.ok(html.includes('&lt;img onerror=&quot;evil&quot;&gt;</a>'));
  assert.ok(html.includes('An unfinished [link](target and **bold, `code, src_some_id.'));
  assert.ok(!html.includes('<img'));
});

test('model presets remain distinct and sending one carries its explicit choice to the partner',async()=>{
  const app=studio();
  const p=app.run(`({id:'test',config:boot.defaults,chat:[]})`);
  await app.run(`selectProject('test',${JSON.stringify(p)})`);
  const presets=[{id:'auto_subscriptions',label:'Automatisch · Claude, sonst Codex',provider:'auto',model:null,reasoning_effort:null},
    {id:'claude_opus_sub',label:'Opus 5.5 · Claude-Abo',provider:'claude_code',model:'claude-opus-5-5',reasoning_effort:'xhigh'},
    {id:'codex_astra',label:'Astra · Codex-Abo',provider:'codex_cli',model:'gpt-6-astra',reasoning_effort:'xhigh'},
    {id:'openrouter_astra',label:'Astra · OpenRouter',provider:'openrouter',model:'openai/gpt-6-astra',reasoning_effort:null},
    {id:'openrouter_astra_pro',label:'Astra Pro · OpenRouter',provider:'openrouter',model:'openai/gpt-6-astra-pro',reasoning_effort:null},
    {id:'openrouter_fable',label:'Claude Fable 5.1 · OpenRouter',provider:'openrouter',model:'anthropic/claude-fable-5.1',reasoning_effort:null},
    {id:'openrouter_deepseek',label:'DeepSeek V4.1 Flash · max · OpenRouter',provider:'openrouter',model:'deepseek/deepseek-v4.1-flash',reasoning_effort:'max'}];
  app.run(`boot.capabilities={conversational_setup:true};boot.text_catalog={presets:${JSON.stringify(presets)}};`);
  const html=app.run('renderBrief()');
  for(const preset of presets)assert.ok(html.includes(`data-text-preset="${preset.id}"`));
  assert.ok(html.includes('Live-Recherche läuft über das gewählte Abo'));
  assert.ok(html.includes('springt bei leerem Kontingent auf Codex um'));
  app.responses.set('/api/projects/test',p);
  await app.run(`sendSetupMessage('Nutze DeepSeek mit max','openrouter_deepseek')`);
  const request=app.requests.find(r=>r.path==='/api/projects/test/start');
  assert.equal(JSON.parse(request.options.body).text_preset,'openrouter_deepseek');
  assert.ok(!app.requests.some(r=>r.path.endsWith('/save')||r.path.endsWith('/apply_proposal')));
});

test('attachment picker supports text and DOCX with escaped names and no configuration forms',async()=>{
  const app=studio();
  await app.run(`selectProject('test',{id:'test',config:boot.defaults,chat:[],attachments:[{id:'abc',name:'<b>Notizen</b>.md',characters:150}]})`);
  app.run('boot.capabilities={conversational_setup:true,project_attachments:true};');
  const html=app.run('renderBrief()');
  assert.ok(html.includes('id="chat-files"'));
  assert.ok(html.includes('.md,.txt,.docx'));
  assert.ok(html.includes('multiple'));
  assert.ok(html.includes('&lt;b&gt;Notizen&lt;/b&gt;.md'));
  assert.ok(!html.includes('<b>Notizen</b>'));
  assert.ok(html.includes('dein Textmodell den Inhalt'));
  assert.ok(app.run('renderResearch()').includes('&lt;b&gt;Notizen'));
});

test('file-only new project uploads before the assistant starts and preserves source selection',async()=>{
  const app=studio();
  await app.run(`selectProject('')`);
  app.run('boot.capabilities={conversational_setup:true,project_attachments:true};');
  app.responses.set('/api/bootstrap',app.run('structuredClone(boot)'));
  app.responses.set('/api/projects',{id:'new-podcast'});
  const p=app.run(`({id:'new-podcast',config:structuredClone(boot.defaults),chat:[],attachments:[{id:'abc',name:'Idee.docx',characters:300}]})`);
  app.responses.set('/api/projects/new-podcast',p);
  await app.run(`queueAttachments([{name:'Idee.docx',size:3,arrayBuffer:async()=>new Uint8Array([65,66,67]).buffer}])`);
  assert.equal(app.run('pendingAttachments[0].base64'),'QUJD');
  await app.run(`sendSetupMessage('')`);
  const sent=app.requests.filter(r=>r.options?.method==='POST');
  assert.deepEqual(sent.map(r=>r.path),['/api/projects','/api/projects/new-podcast/upload','/api/projects/new-podcast/start']);
  assert.equal(JSON.parse(sent[0].options.body).config.topic,'Idee');
  assert.deepEqual(JSON.parse(sent[1].options.body).files,[{name:'Idee.docx',base64:'QUJD'}]);
  assert.equal(JSON.parse(sent[2].options.body).action,'assistant');
  assert.ok(JSON.parse(sent[2].options.body).message.includes('angehängten Dateien'));
  assert.equal(app.run('pendingAttachments.length'),0);
  assert.equal(app.run('setupSending'),false);
});

test('failed upload keeps pending files and message and never starts the model',async()=>{
  const app=studio();
  await app.run(`selectProject('test',{id:'test',config:boot.defaults,chat:[]})`);
  app.run(`boot.capabilities={conversational_setup:true,project_attachments:true};
    pendingAttachments=[{name:'Notizen.txt',base64:'QUJD',bytes:3}];
    $('chat-message').value='Meine Wünsche';
    const baseFetch=fetch;fetch=async(path,options)=>path.endsWith('/upload')?{ok:false,json:async()=>({error:'Upload fehlgeschlagen'})}:baseFetch(path,options);`);
  await assert.rejects(app.run(`sendSetupMessage('Meine Wünsche')`),/Upload fehlgeschlagen/);
  assert.equal(app.run('pendingAttachments.length'),1);
  assert.equal(app.run('setupSending'),false);
  assert.equal(app.elements.get('chat-message').value,'Meine Wünsche');
  assert.ok(!app.requests.some(r=>r.path.endsWith('/start')));
});

test('pending file content survives render but cannot leak into another project',async()=>{
  const app=studio();
  await app.run(`selectProject('test',{id:'test',config:boot.defaults,chat:[]})`);
  app.run(`boot.capabilities={conversational_setup:true,project_attachments:true};`);
  await app.run(`queueAttachments([{name:'Notizen.md',size:3,arrayBuffer:async()=>new Uint8Array([65,66,67]).buffer}])`);
  app.run('render();render();');
  assert.equal(app.run('pendingAttachments.length'),1);
  await app.run(`selectProject('other',{id:'other',config:boot.defaults,chat:[]})`);
  assert.equal(app.run('pendingAttachments.length'),0);
  app.run('setupSending=true;');
  await assert.rejects(app.run(`selectProject('')`),/gerade gesendet/);
  await assert.rejects(app.run('showOverview()'),/gerade gesendet/);
});

test('invalid extensions and oversized text are rejected before reading file contents',async()=>{
  const app=studio();
  await app.run(`selectProject('')`);
  for(const file of [{name:'run.exe',size:1},{name:'long.txt',size:262145},{name:'huge.docx',size:2097153}]){
    app.run('boot.capabilities={project_attachments:true};');
    await assert.rejects(app.run(`queueAttachments([{...${JSON.stringify(file)},arrayBuffer(){throw new Error('must not read');}}])`),/lesbarem Text/);
  }
  assert.equal(app.run('pendingAttachments.length'),0);
});

test('a proposal based on outdated attachments cannot be applied from the summary',async()=>{
  const app=studio();
  await app.run(`selectProject('test',{id:'test',config:boot.defaults,chat:[{role:'assistant',message:'Old proposal'}],proposal_current:false,proposal_applied:false})`);
  app.run('boot.capabilities={conversational_setup:true,project_attachments:true};');
  const html=app.run('setupSummary()');
  assert.ok(html.includes('data-action="apply-proposal" disabled'));
  assert.ok(html.includes('Anhänge haben sich geändert'));
});
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
  app.run("project.job.progress.budget_projection={minimum_remaining_calls:10,feasible:true};renderJob()");
  let html=app.elements.get('job-status').innerHTML;
  assert.ok(html.includes('Modellaufrufe: 40 von 120 · mindestens 10 weitere nötig'));
  assert.ok(!html.includes('Limit reicht nicht'));
  app.run("project.job.progress.budget_projection={minimum_remaining_calls:81,feasible:false};renderJob()");
  html=app.elements.get('job-status').innerHTML;
  assert.ok(html.includes('mindestens 81 weitere nötig – Limit reicht nicht'));
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
  assert.ok(!html.includes('Keine Antwort vom Studio'));
  // The server stamps updated_at at every read, so an old stamp alone is no staleness; the run's own change time is shown.
  app.run(`project.job.progress.updated_at=new Date(Date.now()-24*60000).toISOString();project.job.progress.changed_at=new Date(Date.now()-5*60000).toISOString();lastJobView='';renderJob();`);
  html=app.elements.get('job-status').innerHTML;
  assert.ok(!html.includes('Keine Antwort vom Studio'));
  assert.ok(html.includes('Letzte Änderung im Lauf: vor 5 Min.'));
  // Staleness is a missing answer from the Studio: the view says whose state it shows and stops pulsing.
  app.run(`connectionLost=true;lastJobView='';renderJob();`);
  html=app.elements.get('job-status').innerHTML;
  assert.ok(html.includes('Keine Antwort vom Studio seit'));
  assert.ok(html.includes('Zuletzt gemeldeter Modellaufruf gestartet vor'));
  assert.ok(!html.includes('Aktueller Modellaufruf: seit'));
  assert.ok(!app.run('renderScriptProgress(project.job.progress,true)').includes('class="activity-dot"'));
  assert.ok(app.elements.get('job-bar').innerHTML.includes('Keine Verbindung'));
  assert.ok(app.elements.get('job-bar').innerHTML.includes('job-dot running offline'));
  app.run(`markSynced();project.job.status='completed';renderJob();`);
  assert.ok(!app.elements.get('job-status').innerHTML.includes('Keine Antwort vom Studio'));
  assert.ok(!app.elements.get('job-bar').innerHTML.includes('Keine Verbindung'));
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
test('a blocked final review displays readable escaped findings',()=>{
  const app=studio();
  app.run(`project={id:'test',job:{status:'blocked',action:'resume',progress:{phase:'script',stage:'review',episodes:[],review_issues:[{category:'depth',segment_ids:['seg_001'],reason:'Explain the <missing> connection.'}]},run:{kind:'script',stages:{review:{status:'blocked',error:{code:'script_review_failed'}}}}}};step=PAGE.production;renderJob();`);
  const html=app.elements.get('production-progress').innerHTML;
  assert.ok(html.includes('Offene Punkte der Qualitätsprüfung'));
  assert.ok(html.includes('Explain the &lt;missing&gt; connection.'));
  assert.ok(!html.includes('[object Object]'));
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
  const context = vm.createContext({console,structuredClone,AbortController,encodeURIComponent,URL,URLSearchParams,btoa,setInterval(){},window:{addEventListener(name,handler){events.set(name,handler);},scrollTo(){}},
    document:{getElementById:element,querySelectorAll(){return [];},addEventListener(){},modelContext:{registerTool(tool){registered.set(tool.name,tool);}}},
    fetch:async(path,options)=>{requests.push({path,options});const data=responses.get(path)??{token:'csrf',voices:['Aiden','Vivian'],projects:[],defaults};return{ok:true,json:async()=>structuredClone(data)};}});
  vm.runInContext(source, context);
  const run = code => vm.runInContext(code,context);
  run(`boot=${JSON.stringify({token:'csrf',voices:['Aiden','Vivian'],projects:[],defaults})}`);
  return {run,context,elements,registered,requests,responses,events};
}
// The job UI spans the topbar line, the docked drawer and the panel a step page owns.
function jobView(app) {
  return ['job-bar','job-status','stop-card','research-progress','production-progress','audio-jobs'].map(id=>app.elements.get(id)?.innerHTML||'').join('');
}
function jobBarView(app) {
  return ['job-bar','job-status'].map(id=>app.elements.get(id)?.innerHTML||'').join('');
}
function workflowProject(app) {
  return app.run(`({id:'test',config:structuredClone(boot.defaults),text:{provider:'codex_cli',model:null,max_output_tokens:32768},chat:[],research:'Reviewed dossier',episodes:[],
    outline:{hash:'h',approval:{plan_hash:'h'},plan:{central_question:'Why?',explanation_path:'Build the explanation.',scope_note:'Scope',episodes:[{episode_id:'ep_001',title:'One',central_question:'Why?',target_minutes:20,scenes:[],deferred_questions:[]}]}},
    job:{id:'job-one',action:'resume',status:'running',started_at:new Date().toISOString(),run:{kind:'script',status:'running',stages:{planning:{status:'completed',attempts:1},teaching:{status:'running',attempts:1},writing:{status:'pending'},polishing:{status:'pending'},review:{status:'pending'},publish:{status:'pending'}}}}})`);
}

test('the conversational summary shows model, reasoning and independent execution preferences',()=>{
  const app=studio(),p=workflowProject(app);p.job.status='completed';
  p.text={provider:'codex_cli',model:'custom-model',reasoning_effort:'high'};
  p.execution={text:'parallel',audio:'sequential'};
  app.run(`boot.capabilities={conversational_setup:true};project=${JSON.stringify(p)};`);
  const html=app.run('renderBrief()');
  assert.ok(html.includes('custom-model'));
  assert.ok(html.includes('Reasoning: high'));
  assert.ok(html.includes('Parallel · bis zu 5 gleichzeitig'));
  assert.ok(!html.includes('id="model-preset"'));
});

test('unapplied chat proposals render without changing saved project settings',()=>{
  const app=studio(),p=workflowProject(app);p.job.status='completed';
  p.text={provider:'codex_cli',model:'saved',reasoning_effort:'xhigh'};
  p.chat=[{role:'assistant',message:'Choose this?',text:{provider:'openrouter',model:'vendor/new',reasoning_effort:'low'},execution:{text:'parallel',audio:'parallel'}}];
  app.run(`project=${JSON.stringify(p)};`);
  const html=app.run('renderBrief()');
  assert.ok(html.includes('vendor/new'));
  assert.ok(html.includes('data-action="apply-proposal"'));
  assert.equal(app.run('project.text.model'),'saved');
  assert.equal(app.requests.filter(r=>r.options?.method==='POST').length,0);
});

test('applying a chat proposal sends only its review hashes and reloads saved preferences',async()=>{
  const app=studio(),p=workflowProject(app);p.job.status='completed';
  Object.assign(p,{proposal_hash:'proposal',config_hash:'config',audio_hash:'audio',execution_hash:'execution'});
  app.run(`project=${JSON.stringify(p)};`);
  p.text={provider:'codex_cli',model:'gpt-6-astra',reasoning_effort:'xhigh'};
  p.proposal_applied=true;
  app.responses.set('/api/projects/test',p);
  await app.run('applySetupProposal()');
  const request=app.requests.find(r=>r.path==='/api/projects/test/apply_proposal');
  assert.deepEqual(JSON.parse(request.options.body),{proposal_hash:'proposal',config_hash:'config',audio_hash:'audio',execution_hash:'execution'});
  assert.equal(app.run('project.text.reasoning_effort'),'xhigh');
});

test('old servers announce a restart and old runs never claim the new default model',()=>{
  const app=studio(), p=workflowProject(app);
  app.run(`project=${JSON.stringify(p)};`);
  assert.ok(app.run('renderBrief()').includes('benötigt einen Studio-Neustart'));
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

test('the audio page offers the spoken-form table, pause fields and the pronunciation report',()=>{
  const app=studio(), p=workflowProject(app);
  p.job.status='completed';p.job.run.status='completed';
  p.spoken_forms={schema_version:'1.0',entries:[{written:'H800',spoken:'H achthundert'}]};
  p.spoken_forms_hash='forms-hash';
  p.audio_settings={provider:'qwen3_local',voices:{host_a:'Aiden',host_b:'Vivian'},
    pauses:{same_speaker_ms:250,speaker_change_ms:450,chapter_break_ms:900}};
  p.episodes=[{...publishedEpisode({}),pronunciation:{applied:{H800:2},
    flagged:{versions:[{token:'H800',count:2,segment_ids:['seg_001']}]}}}];
  app.run(`project=${JSON.stringify(p)};episodeIndex=0;`);
  const html=app.run('renderAudio()');
  assert.ok(html.includes('H800 = H achthundert'));
  assert.ok(html.includes('id="pause-same"'));
  assert.ok(html.includes('value="450"'));
  assert.ok(html.includes('value="900"'));
  assert.ok(html.includes('Aussprache prüfen'));
  assert.ok(html.includes('Versions- und Modellnamen'));
  assert.ok(html.includes('data-action="save-speech"'));
  assert.equal(app.requests.filter(r=>r.options?.method==='POST').length,0);
});

test('a project with no flagged tokens shows no pronunciation panel',()=>{
  const app=studio(), p=workflowProject(app);
  p.job.status='completed';p.job.run.status='completed';
  p.episodes=[{...publishedEpisode({}),pronunciation:{applied:{},flagged:{}}}];
  app.run(`project=${JSON.stringify(p)};episodeIndex=0;`);
  assert.ok(!app.run('renderAudio()').includes('Aussprache prüfen'));
});

test('a published episode with audio offers a per-segment spoken form and a re-render',()=>{
  const app=studio(), p=workflowProject(app);
  p.job.status='completed';p.job.run.status='completed';
  p.config={...p.config,host_names:{host_a:'Mara',host_b:'Jonas'}};
  p.episodes=[{...publishedEpisode({}),audio:['exports/ep_001/run/audio.mp3'],
    spoken_overrides:{seg_001:'Ganz anders <b>gesprochen</b>.'}}];
  app.run(`project=${JSON.stringify(p)};readingSnapshot=null;`);
  const html=app.run('renderScript()');
  assert.ok(html.includes('<strong>Mara</strong>'));
  assert.ok(!html.includes('<strong>Aiden</strong>'));
  assert.ok(html.includes('Sprechform · gesetzt'));
  assert.ok(html.includes('data-action="spoken-override"'));
  assert.ok(html.includes('data-segment="seg_001"'));
  assert.ok(html.includes('Nur diesen Abschnitt neu rendern'));
  assert.ok(html.includes('Ganz anders &lt;b&gt;gesprochen&lt;/b&gt;.'));
  assert.equal(app.requests.filter(r=>r.options?.method==='POST').length,0);
});

test('a re-render posts the rerender flag with the session token and no fresh approval',async()=>{
  const app=studio(), p=workflowProject(app);
  p.job.status='completed';p.job.run.status='completed';p.config_hash='cfg';p.audio_hash='aud';
  p.episodes=[{...publishedEpisode({}),audio:['exports/ep_001/run/audio.mp3'],spoken_overrides:{seg_001:'Anders.'}}];
  await app.run(`selectProject('test',${JSON.stringify(p)},PAGE.scripts)`);
  app.responses.set('/api/projects/test',p);
  assert.ok(app.run('renderScript()').includes('data-action="audio" data-rerender="true" data-episode="ep_001"'));
  assert.equal(app.requests.filter(r=>r.options?.method==='POST').length,0);
  await app.run(`rerenderEpisode('ep_001')`);
  const request=app.requests.find(r=>r.path==='/api/projects/test/start');
  assert.equal(request.options.method,'POST');
  assert.equal(request.options.headers['X-Studio-Token'],'csrf');
  // No checkbox on the reading page: the server applies the saved approval by the pipeline's rule.
  assert.deepEqual(JSON.parse(request.options.body),{action:'audio',episode:'ep_001',approve_audio:false,rerender:true,
    script_hash:'final',readable_hash:'final',config_hash:'cfg',audio_hash:'aud'});
  // The approval-page button still sends the checkbox state and no rerender flag.
  app.run('episodeIndex=0;$("audio-approval").checked=true;');
  assert.deepEqual(JSON.parse(JSON.stringify(app.run('audioRequest()'))),{episode:'ep_001',approve_audio:true,script_hash:'final',readable_hash:'final',config_hash:'cfg',audio_hash:'aud'});
});

test('the spoken-form field starts from the table result and an unchanged save creates no override',async()=>{
  const app=studio(), p=workflowProject(app);
  p.job.status='completed';p.job.run.status='completed';
  p.spoken_forms={schema_version:'1.0',entries:[{written:'checked',spoken:'geprüfte'},{written:'Final',spoken:'Finale'}]};
  p.episodes=[{...publishedEpisode({}),audio:['exports/ep_001/run/audio.mp3'],spoken_overrides:{}}];
  await app.run(`selectProject('test',${JSON.stringify(p)},PAGE.scripts)`);
  assert.ok(app.run('renderScript()').includes('>Finale geprüfte dialogue.</textarea>'));
  assert.equal(app.run(`applySpokenForms('V3.2-Exp on H800-GPUs, 1.000.000 KL.',{entries:[{written:'V3',spoken:'x'},{written:'H800',spoken:'y'},{written:'1.000',spoken:'z'},{written:'KL',spoken:'w'}]})`),
    'V3.2-Exp on y-GPUs, 1.000.000 w.');
  app.responses.set('/api/projects/test',p);
  app.run(`$('spoken-seg_001').value='Finale geprüfte dialogue.'`);
  await app.run(`saveSpokenOverride('ep_001','seg_001')`);
  let request=app.requests.filter(r=>r.path==='/api/projects/test/spoken_override').at(-1);
  assert.deepEqual(JSON.parse(request.options.body),{episode:'ep_001',segment_id:'seg_001',spoken:''});
  app.run(`$('spoken-seg_001').value='Ganz anders.'`);
  await app.run(`saveSpokenOverride('ep_001','seg_001')`);
  request=app.requests.filter(r=>r.path==='/api/projects/test/spoken_override').at(-1);
  assert.equal(JSON.parse(request.options.body).spoken,'Ganz anders.');
});

test('the pronunciation report stands above the approval checkbox',()=>{
  const app=studio(), p=workflowProject(app);
  p.job.status='completed';p.job.run.status='completed';
  p.episodes=[{...publishedEpisode({}),pronunciation:{applied:{},flagged:{versions:[{token:'H800',count:1,segment_ids:['seg_001']}]}}}];
  app.run(`project=${JSON.stringify(p)};episodeIndex=0;`);
  const html=app.run('renderAudio()');
  const report=html.indexOf('Aussprache prüfen'), checkbox=html.indexOf('id="audio-approval"');
  assert.ok(report>-1&&checkbox>-1,'both rendered');
  assert.ok(report<checkbox,'the report precedes the approval');
  assert.ok(html.includes('Prüfe sie vor der Freigabe'));
});

test('host names are edited in the speech panel and saved as a pair or not at all',async()=>{
  const app=studio(), p=workflowProject(app);
  p.job.status='completed';p.job.run.status='completed';p.config_hash='cfg';p.audio_hash='aud';p.spoken_forms_hash='forms';
  p.config={...p.config,host_names:{host_a:'Mara',host_b:'Jonas'}};
  p.episodes=[publishedEpisode({})];
  app.run(`project=${JSON.stringify(p)};episodeIndex=0;`);
  const html=app.run('renderAudio()');
  assert.ok(html.includes('id="host-name-a" type="text" value="Mara"'));
  assert.ok(html.includes('id="host-name-b" type="text" value="Jonas"'));
  assert.ok(html.includes('Sprechformen, Pausen und Hostnamen'));
  app.responses.set('/api/projects/test',p);
  app.run(`$('host-name-a').value='Lena';$('host-name-b').value='Tom';$('pause-same').value='250';$('pause-change').value='450';$('pause-chapter').value='900';$('spoken-forms').value='H800 = H achthundert';`);
  await app.run('saveSpeechSettings()');
  let request=app.requests.filter(r=>r.path==='/api/projects/test/save').at(-1);
  let body=JSON.parse(request.options.body);
  assert.equal(request.options.headers['X-Studio-Token'],'csrf');
  assert.deepEqual(body.config.host_names,{host_a:'Lena',host_b:'Tom'});
  assert.equal(body.config.topic,'New project');
  assert.equal(body.config_hash,'cfg');
  assert.deepEqual(body.spoken_forms.entries,[{written:'H800',spoken:'H achthundert'}]);
  assert.deepEqual(body.audio_settings.pauses,{same_speaker_ms:250,speaker_change_ms:450,chapter_break_ms:900});
  app.run(`$('host-name-b').value='';`);
  const before=app.requests.length;
  await assert.rejects(app.run('saveSpeechSettings()'),/Beide Hostnamen/);
  assert.equal(app.requests.length,before);
  app.run(`$('host-name-a').value='';`);
  await app.run('saveSpeechSettings()');
  request=app.requests.filter(r=>r.path==='/api/projects/test/save').at(-1);
  assert.equal(JSON.parse(request.options.body).config.host_names,null);
});

test('a preview and an episode without audio offer no spoken-form control',()=>{
  const app=studio(), p=workflowProject(app);
  p.job.status='completed';p.job.run.status='completed';
  p.episodes=[publishedEpisode({})];
  app.run(`project=${JSON.stringify(p)};readingSnapshot=null;`);
  assert.ok(!app.run('renderScript()').includes('data-action="spoken-override"'));
  const preview=structuredClone(p);
  preview.episodes=[];preview.job.status='running';preview.job.run.status='running';
  preview.job.run.run_id='run_one';preview.job.progress={phase:'script',script_previews:[previewEpisode()]};
  app.run(`project=${JSON.stringify(preview)};readingSnapshot=null;`);
  assert.ok(!app.run('renderScript()').includes('data-action="spoken-override"'));
});

function publishedEpisode(notes) {
  return {preview:false,readable_hash:'final',hash:'final',audio:[],audio_current:false,
    script:{episode_id:'ep_001',title:'ep_001 Dialogue',chapters:[{chapter_id:'intro',title:'Introduction'}],
      segments:[{segment_id:'seg_001',chapter_id:'intro',speaker_id:'host_a',text:'Final checked dialogue.'}]},
    metrics:{words:3400,estimated_minutes:27},review_notes:notes};
}

test('the reading page shows every recorded review caveat under one collapsible panel',()=>{
  const app=studio(), p=workflowProject(app);
  p.job.status='completed';p.job.run.status='completed';
  p.episodes=[publishedEpisode({script_review:['Eine Modellprüfung kann Fehler übersehen.','Zwei.','Drei.','Vier.','Fünf.'],
    teaching_review:['Die Lehrprüfung misst kein echtes Lernen.'],
    dismissed_gaps:[{stage:'teaching_review',objective_id:'goal_one',gap:'Wie wird trainiert?',reason:'Für das Lernziel nicht nötig.'}],
    advisories:[{code:'long_cold_open',count:157,detail:'Der erste Abschnitt hat 157 Wörter.',segment_ids:['seg_001']}]})];
  app.run(`project=${JSON.stringify(p)};readingSnapshot=null;`);
  const html=app.run('renderScript()');
  assert.ok(html.includes('Hinweise der Prüfungen'));
  assert.equal((html.match(/<li>/g)||[]).length,8);
  assert.ok(html.includes('Eine Modellprüfung kann Fehler übersehen.'));
  assert.ok(html.includes('Wie wird trainiert?'));
  assert.ok(html.includes('long_cold_open'));
  assert.ok(html.includes('(seg_001)'));
  assert.ok(!html.includes('Dialogvergleich</h3>'));
});

test('a script without recorded caveats and an unpublished preview render no panel',()=>{
  const app=studio(), p=workflowProject(app);
  p.job.status='completed';p.job.run.status='completed';
  p.episodes=[publishedEpisode({})];
  app.run(`project=${JSON.stringify(p)};readingSnapshot=null;`);
  assert.ok(!app.run('renderScript()').includes('Hinweise der Prüfungen'));
  const preview=structuredClone(p);
  preview.episodes=[];preview.job.status='running';preview.job.run.status='running';
  preview.job.run.run_id='run_one';preview.job.progress={phase:'script',script_previews:[previewEpisode()]};
  app.run(`project=${JSON.stringify(preview)};readingSnapshot=null;`);
  assert.ok(!app.run('renderScript()').includes('Hinweise der Prüfungen'));
});

test('a caveat containing markup is escaped before it reaches the reading page',()=>{
  const app=studio(), p=workflowProject(app);
  p.job.status='completed';p.job.run.status='completed';
  p.episodes=[publishedEpisode({script_review:['<script>alert(1)</script>'],
    advisories:[{code:'<img src=x>',count:1,detail:'Ein "gefährlicher" Hinweis.',segment_ids:["<b>"]}]})];
  app.run(`project=${JSON.stringify(p)};readingSnapshot=null;`);
  const html=app.run('renderScript()');
  assert.ok(!html.includes('<script>alert(1)'));
  assert.ok(html.includes('&lt;script&gt;alert(1)&lt;/script&gt;'));
  assert.ok(html.includes('&lt;img src=x&gt;'));
  assert.ok(html.includes('&quot;gefährlicher&quot;'));
  assert.ok(html.includes('(&lt;b&gt;)'));
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
  assert.ok(jobBarView(app).includes('Ausarbeitung ansehen'));
  assert.ok(!jobBarView(app).includes('production-stages'));
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
test('setup starts with a conversation and a protected key entry, without configuration forms',()=>{
  const app=studio(),html=app.run('renderBrief()');
  assert.ok(html.includes('id="chat-message"'));
  assert.ok(html.includes('Worum soll dein Podcast gehen'));
  assert.ok(html.includes('id="api-key"'));
  for(const id of ['brief-form','topic','central_question','provider','model','host_a','host_b'])
    assert.ok(!html.includes(`id="${id}"`));
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
  assert.ok(html.includes('Alle fertigen Folgen anhören'));
  const player=app.run("podcastCard({id:'test'},{episode_id:'ep_001',title:'Episode',audio:['exports/ep_001/run_test/audio.mp3'],audio_current:false},0)");
  assert.ok(player.includes('früheren Skript- oder Stimmenstands'));
  assert.ok(player.includes('/media/test/exports/ep_001/run_test/audio.mp3'));
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
test('chat summary and previews distinguish Gemini audio from the Codex writer',()=>{
  const app=studio();
  app.run(`boot.audio_catalog={qwen3_local:{label:'Qwen',voices:['Aiden','Vivian'],defaults:{host_a:'Aiden',host_b:'Vivian'}},openrouter_gemini_tts:{label:'Gemini',voices:['Sadaltager','Aoede','Charon'],defaults:{host_a:'Sadaltager',host_b:'Aoede'}}};
    project={config:boot.defaults,text:{provider:'codex_cli',model:'gpt-6-astra'},audio_settings:{provider:'openrouter_gemini_tts',voices:{host_a:'Sadaltager',host_b:'Aoede'}},chat:[]};`);
  const html=app.run('renderBrief()');
  assert.ok(html.includes('Codex · Abo'));
  assert.ok(html.includes('Sadaltager &amp; Aoede'));
  assert.ok(html.includes('data-preview-voice="Charon"'));
  assert.ok(html.includes('Hörprobe erzeugen · API'));
  assert.ok(!html.includes('id="tts-provider"'));
});
test('Gemini approval names the remote provider, chosen voices and API charge',()=>{
  const app=studio();
  app.run(`boot.audio_catalog={openrouter_gemini_tts:{label:'Gemini TTS · OpenRouter',voices:['Sadaltager','Aoede'],
      models:{'google/gemini-3.8-flash-tts':'Gemini 3.8 Flash TTS','google/gemini-3.8-flash-lite-tts':'Gemini 3.8 Flash Lite TTS'},
      default_model:'google/gemini-3.8-flash-tts'}};
    project={config:boot.defaults,id:'test',audio_settings:{provider:'openrouter_gemini_tts',voices:{host_a:'Sadaltager',host_b:'Aoede'}},episodes:[{script:{title:'Episode',episode_id:'ep_001'},audio:[]}]}`);
  const html=app.run('renderAudio()');
  // A choice saved without a model uses the default; the card names the model the approval binds.
  assert.ok(html.includes('Gemini 3.8 Flash TTS · OpenRouter'));
  app.run(`project.audio_settings={...project.audio_settings,model:'google/gemini-3.8-flash-lite-tts'}`);
  assert.ok(app.run('renderAudio()').includes('Gemini 3.8 Flash Lite TTS · OpenRouter'));
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

test('remote audio enables a different reviewed episode, but blocks duplicates and full capacity',()=>{
  const app=studio();
  app.run(`boot.capabilities={parallel_audio:true};project={id:'test',config:boot.defaults,
    audio_settings:{provider:'openrouter_gemini_tts',voices:{host_a:'Sadaltager',host_b:'Aoede'}},
    execution:{audio:'parallel'},audio_capacity:{limit:3,active:1,available:2},
    episodes:[{script:{episode_id:'ep_001',title:'First'},audio:[]},{script:{episode_id:'ep_002',title:'Second'},audio:[]}],
    audio_jobs:[{id:'a',episode:'ep_001',status:'running'}],job:{id:'a',action:'audio',status:'running'}};`);
  assert.match(app.run('audioBlockReason("ep_001")'),/bereits vertont/);
  assert.equal(app.run('audioBlockReason("ep_002")'),'');
  app.run('episodeIndex=1');
  assert.ok(!app.run('renderAudio()').includes('id="audio-approval" type="checkbox" disabled'));
  assert.ok(app.run('renderAudio()').includes('id="audio-start" data-action="audio" disabled'));
  app.run('project.audio_capacity.available=0');
  assert.match(app.run('audioBlockReason("ep_002")'),/Plätze/);
  app.run('project.audio_settings.provider="qwen3_local";project.audio_capacity.available=2');
  assert.match(app.run('audioBlockReason("ep_002")'),/Auftrag läuft/);
});

test('independent audio cards target their own stop or resume action',()=>{
  const app=studio();
  app.run(`boot.capabilities={parallel_audio:true};project={id:'test',config:boot.defaults,episodes:[],
    audio_settings:{provider:'openrouter_gemini_tts',voices:{host_a:'Sadaltager',host_b:'Aoede'}},audio_capacity:{available:2},
    audio_jobs:[{id:'one',episode:'ep_001',status:'running',progress:{completed_segments:2,total_segments:8}},
      {id:'two',episode:'ep_002',status:'blocked',run:{run_id:'run_two'},message:'<blocked>'}],job:{id:'one',status:'running'}};renderJob();`);
  const html=app.elements.get('job-status').innerHTML;
  assert.ok(html.includes('data-job-id="one"'));
  assert.ok(html.includes('data-run-id="run_two" data-episode="ep_002"'));
  assert.ok(html.includes('&lt;blocked&gt;'));
  assert.ok(html.includes('2 von 8'));
});

test('the overview lists projects as pipeline rows while the recordings list keeps every available episode',()=>{
  const app=studio();
  app.run(`boot.capabilities={project_overview:true};overviewData={projects:[{id:'test',topic:'Topic <one>',
    job:{status:'running',action:'audio'},episodes:[
      {episode_id:'ep_001',title:'First',audio:['exports/ep_001/one.mp3'],audio_current:true},
      {episode_id:'ep_002',title:'Second',audio:['exports/ep_002/two.mp3','exports/ep_002/three.mp3'],audio_current:true},
      {episode_id:'ep_003',title:'Third',audio:[]}]}],trash:[]};`);
  const html=app.run('renderOverview()');
  assert.ok(html.includes('Topic &lt;one&gt;'));
  assert.ok(html.includes('Audio entsteht'));
  assert.ok(html.includes('data-open-project="test"'));
  assert.ok(html.includes('data-delete-project="test" disabled'));
  assert.ok(html.includes('class="pipe"'));
  assert.ok(!html.includes('<audio '),'players belong to the audio page, not the overview');
  const recordings=app.run('renderRecordings(overviewData.projects[0])');
  assert.equal((recordings.match(/<audio /g)||[]).length,3);
  assert.equal(app.run('steps.length'),6);
});

test('audio-page polling preserves an existing audio element and adds the next finished episode',()=>{
  const app=studio();
  app.run(`project={id:'test',config:boot.defaults,episodes:[
    {script:{episode_id:'ep_001',title:'First'},audio:['one.mp3'],audio_current:true},
    {script:{episode_id:'ep_002',title:'Second'},audio:['two.mp3'],audio_current:true}]};step=PAGE.audio;
    $('podcast-test-ep_001').dataset={audioVersion:JSON.stringify(['one.mp3'])};
    $('podcast-test-ep_001').innerHTML='playing at 123 seconds';
    $('podcast-test-ep_002').dataset={audioVersion:JSON.stringify([])};
    $('podcast-test-ep_002').querySelectorAll=()=>[];
    refreshRecordings();`);
  assert.equal(app.elements.get('podcast-test-ep_001').innerHTML,'playing at 123 seconds');
  assert.ok(app.elements.get('podcast-test-ep_002').outerHTML.includes('two.mp3'));
  assert.ok(app.run('renderAudio()').includes('id="podcasts-test"'));
});

test('credential-like chat text is rejected before project creation or model calls',async()=>{
  const app=studio();
  await assert.rejects(app.run(`sendSetupMessage('sk-or-abcdefghijklmnopqrstuvwxyz')`),/Keys gehören nicht/);
  assert.equal(app.requests.filter(r=>r.options?.method==='POST').length,0);
});

test('complete podcast download is a single ZIP link while episode playback stays inline',()=>{
  const app=studio();
  app.run('boot.capabilities={podcast_downloads:true}');
  const p={id:'test',topic:'Topic',episode_count:2,episodes:[
    {episode_id:'ep_001',title:'First',audio:['exports/ep_001/one.mp3'],audio_current:true},
    {episode_id:'ep_002',title:'Second',audio:['exports/ep_002/two.mp3'],audio_current:true}]};
  const html=app.run(`renderRecordings(${JSON.stringify(p)})`);
  assert.ok(html.includes('Gesamten Podcast herunterladen'));
  assert.ok(html.includes('href="/download/test/podcast.zip" download'));
  assert.ok(html.includes('href="/download/test/file/exports/ep_001/one.mp3"'));
  assert.ok(html.includes('src="/media/test/exports/ep_001/one.mp3"'));
  assert.ok(html.includes('download="Topic - Folge 02 - Second.mp3"'));
});

test('partial downloads are labelled honestly and update without replacing players',()=>{
  const app=studio();
  app.run(`boot.capabilities={podcast_downloads:true};project={id:'test',config:{...boot.defaults,topic:'Topic'},
    outline:{plan:{episodes:[{episode_id:'ep_001'},{episode_id:'ep_002'},{episode_id:'ep_003'}]}},
    episodes:[{script:{episode_id:'ep_002',title:'Second'},audio:['two.mp3'],audio_current:true}]};step=PAGE.audio;
    $('podcast-test-ep_002').dataset={audioVersion:JSON.stringify(['two.mp3'])};
    $('podcast-test-ep_002').innerHTML='playing at 123 seconds';refreshRecordings();`);
  const html=app.elements.get('project-download-test').innerHTML;
  assert.ok(html.includes('Fertige Folgen herunterladen'));
  assert.ok(html.includes('1 von 3'));
  assert.ok(!html.includes('Gesamten Podcast'));
  assert.equal(app.elements.get('podcast-test-ep_002').innerHTML,'playing at 123 seconds');
  const episode=app.run('podcastCard(recordingsProject(),recordingsProject().episodes[0],0)');
  assert.ok(episode.includes('Folge 2: Second'));
});

test('old servers keep named individual downloads and explain how to enable ZIP',()=>{
  const app=studio();
  app.run('boot.capabilities={}');
  const html=app.run(`renderRecordings({id:'test',topic:'Topic',episodes:[{episode_id:'ep_001',title:'First',audio:['one.mp3']}]})`);
  assert.ok(html.includes('Studio nach Ende laufender Aufträge einmal neu starten'));
  assert.ok(html.includes('download="Topic - Folge 01 - First.mp3"'));
  assert.ok(!html.includes('href="/download/'));
});

test('individual download fallback names stay short even before a server restart',()=>{
  const app=studio();
  app.run('boot.capabilities={}');
  const topic='Ein besonders langer Podcasttitel '.repeat(10),title='Eine sehr lange Erklärung mit Umlauten '.repeat(10);
  const html=app.run(`podcastCard({id:'test',topic:${JSON.stringify(topic)}},{episode_id:'ep_012',title:${JSON.stringify(title)},audio:['one.mp3']},0)`);
  const name=/ download="([^"]+)"/.exec(html)[1];
  assert.ok(name.length<120);
  assert.ok(name.includes('Folge 12'));
  assert.ok(!name.includes('…'));
});

test('blocked research questions offer an explicit gap approval only while no job runs',()=>{
  const app=studio();
  const ledger={closed:1,total:2,accepted:0,phase:'blocked',active_task:null,
    budget_projection:{feasible:true,used:5,remaining:10,minimum_remaining_calls:2,closing_calls:2,expected_remaining_calls:7,expected_calls_per_task:5},
    questions:[{id:'task_definition',question:'Was ist Energie?',status:'verified',activity:'ok',steps:2,read_sections:3,acceptance:['x'],answer:'Antwort',findings:[],sources:[],limits:[],reopened:0},
      {id:'task_empirical',question:'Gibt es <Belege>?',status:'blocked',outcome:'budget_block',advice:{key:'0.0',diagnosis:'Beraten.',recommendation:'accept_gap',limit:'none',hint:'',sources:[]},reason:'Das Web-Suchbudget ist ausgeschöpft.',activity:'Beleg fehlt',steps:4,read_sections:2,acceptance:['y'],reopened:0}]};
  app.run(`project={id:'p',job:{id:'j1',status:'blocked',started_at:new Date().toISOString(),run:{run_id:'run_x',kind:'research',stages:{}},progress:{phase:'research',research_questions:${JSON.stringify(ledger)},search_round_limit:12,model_call_limit:150,model_calls:5}}};renderJob();`);
  let html=jobView(app);
  assert.ok(html.includes('data-action="accept-gap"'));
  assert.ok(html.includes('data-task-id="task_empirical"'));
  assert.ok(html.includes('data-run-id="run_x"'));
  assert.ok(html.includes('Suchrunden auf 18 erhöhen'));
  assert.ok(html.includes('Erfahrungsgemäß etwa 7 Aufrufe'));
  assert.ok(html.includes('Gibt es &lt;Belege&gt;?'));
  assert.ok(!html.includes('<Belege>'));
  assert.ok(!html.includes('>Fortsetzen<'));
  app.run("project.job.status='running';renderJob()");
  html=jobView(app);
  assert.ok(!html.includes('data-action="accept-gap"'));
  app.run("project.job.status='blocked';const q=project.job.progress.research_questions;q.questions[1].accepted_gap=true;q.questions[1].accepted_reason='Nicht nötig';q.accepted=1;q.blocked=0;q.phase='questions';renderJob()");
  html=jobView(app);
  assert.ok(html.includes('Als Lücke akzeptiert'));
  assert.ok(html.includes('1 als Lücke akzeptiert'));
  assert.ok(html.includes('Nicht nötig'));
  assert.ok(!html.includes('data-action="accept-gap"'));
  assert.ok(html.includes('>Fortsetzen<'));
  app.run("project.job.progress.research_questions.budget_projection.feasible=false;project.job.progress.research_questions.budget_projection.shortfall=3;renderJob()");
  html=jobView(app);
  assert.ok(html.includes('data-action="approve-calls"'));
  assert.ok(html.includes('Aufruflimit auf 12 erhöhen'));
});

test('blocked research questions that never searched the web keep the resume button and say why',()=>{
  const app=studio();
  const ledger={closed:1,total:3,accepted:0,blocked:2,reopenable:1,phase:'blocked',active_task:null,
    questions:[{id:'task_definition',question:'Was ist Energie?',status:'verified',activity:'ok',steps:2,read_sections:3,acceptance:['x'],answer:'Antwort',findings:[],sources:[],limits:[],reopened:0},
      {id:'task_norms',question:'Wie wirken Normen?',status:'blocked',outcome:'evidence_block',advice:{key:'0.0',diagnosis:'Beraten.',recommendation:'accept_gap',limit:'none',hint:'',sources:[]},reopenable:true,web_attempts:0,reason:'Die gespeicherten Quellen brachten keine neuen Belege.',activity:'Beleg fehlt',steps:3,read_sections:30,acceptance:['y'],reopened:0},
      {id:'task_other',question:'Gibt es Belege?',status:'blocked',outcome:'evidence_block',advice:{key:'0.0',diagnosis:'Beraten.',recommendation:'accept_gap',limit:'none',hint:'',sources:[]},reopenable:false,web_attempts:1,reason:'Auch die Websuche brachte nichts.',activity:'Beleg fehlt',steps:5,read_sections:12,acceptance:['z'],reopened:0}]};
  app.run(`project={id:'p',job:{id:'j1',status:'blocked',started_at:new Date().toISOString(),run:{run_id:'run_x',kind:'research',stages:{}},progress:{phase:'research',research_questions:${JSON.stringify(ledger)},search_round_limit:12,model_call_limit:150,model_calls:73}}};renderJob();`);
  let html=jobView(app);
  assert.ok(html.includes('>Fortsetzen<'));
  assert.ok(html.includes('1 blockierte Teilfrage hat das Web noch nicht durchsucht'));
  assert.ok(!html.includes('Fortsetzen allein wiederholt diese Versuche nicht'));
  assert.equal((html.match(/holt das nach/g)||[]).length,1);
  assert.ok(html.includes('data-action="accept-gap"'));
  // Once every block has had its web search, the old rule applies again: no resume, gaps to accept.
  app.run("const q=project.job.progress.research_questions;q.reopenable=0;q.questions[1].reopenable=false;q.questions[1].web_attempts=1;renderJob()");
  html=jobView(app);
  assert.ok(!html.includes('>Fortsetzen<'));
  assert.ok(html.includes('Fortsetzen allein wiederholt diese Versuche nicht'));
  assert.ok(!html.includes('holt das nach'));
});

test('a blocked question shows its own mark, its cause and when it is decided, also while the run works',()=>{
  const app=studio();
  const q=(id,question,extra)=>({id,question,activity:'x',steps:1,read_sections:1,acceptance:['k'],reopened:0,...extra});
  const ledger={closed:1,total:4,accepted:0,phase:'questions',questions:[
    q('t1','Erste Frage?',{status:'verified',answer:'Antwort',findings:[],sources:[],limits:[]}),
    q('t2','Merton?',{status:'blocked',outcome:'evidence_block',reason:'Mertons Original fehlt.',web_attempts:2}),
    q('t3','Vergleich?',{status:'blocked',outcome:'prerequisite_block',reason:'A required prerequisite has not passed evidence review.',depends_on:['t1','t2']}),
    q('t4','Scheffer?',{status:'blocked',outcome:'evidence_block',reason:'Scheffer fehlt.',retry_requested:true,web_attempts:2}),
    q('t5','Salganik?',{status:'blocked',outcome:'evidence_block',reason:'Nach zwei Websuchen nichts gefunden.',web_attempts:0,steps:8})]};
  let html=app.run(`renderResearchQuestions(${JSON.stringify(ledger)},new Set(),true,'run_x',12,12)`);
  // The counted web searches stand next to the model's wording; used-up rounds explain a question that never searched the web.
  assert.ok(html.includes('Websuchen für diese Frage: 2 · Suchrunden des Laufs aufgebraucht (12 von 12).'));
  assert.ok(html.includes('Websuchen für diese Frage: 0 · Suchrunden des Laufs aufgebraucht (12 von 12). Sie hat deshalb nur in den schon gelesenen Quellen gesucht'));
  assert.equal((html.match(/Websuchen für diese Frage/g)||[]).length,3,'not for a question that only waits for its prerequisite');
  const card=app.run(`renderResearchDecisions({status:'blocked',progress:{research_questions:${JSON.stringify(ledger)},search_rounds:12,search_round_limit:12}},{run_id:'run_x'},false,0,true,12)`);
  assert.ok(card.includes('Beleg fehlt · keine Websuche'));
  assert.ok(!card.includes('Salganik?</strong><p class="hint">Beleg fehlt · noch nicht bearbeitet'));
  assert.ok(card.includes('Suchrunden auf 18 erhöhen'));
  assert.ok(html.includes('⛔ Merton? · Blockiert</summary>'));
  assert.ok(html.includes('⛔ Vergleich? · Blockiert</summary>'));
  assert.ok(html.includes('<strong>Blockiert: Beleg fehlt</strong> · Mertons Original fehlt.'));
  assert.ok(html.includes('Entscheiden musst du erst, wenn der Lauf anhält'));
  // The cause stands once, in the note at the top of the question, not again further down.
  assert.equal((html.match(/Mertons Original fehlt/g)||[]).length,1);
  // A question behind a blocked prerequisite names that question instead of the pipeline's internal reason.
  assert.ok(html.includes('Wartet auf: Merton?'));
  assert.ok(!html.includes('A required prerequisite'));
  assert.ok(html.includes('nimmt diese Frage automatisch wieder auf. Entscheiden kannst du, wenn der Lauf anhält.'));
  assert.ok(html.includes('↻ Scheffer? · Neuer Versuch angefordert</summary>'));
  assert.ok(html.includes('„Fortsetzen“ startet ihn'));
  html=app.run(`renderResearchQuestions(${JSON.stringify(ledger)},new Set(),false,'run_x',12)`);
  assert.ok(html.includes('Entscheide oben unter „Wartet auf dich“'));
  assert.ok(!html.includes('Entscheiden musst du erst'));
  // At the run's source limit a web search cannot load anything: the question and the card name that limit.
  const full={...ledger,source_attempt_count:150};
  html=app.run(`renderResearchQuestions(${JSON.stringify(full)},new Set(),false,'run_x',24,12,150)`);
  assert.ok(html.includes('Websuchen für diese Frage: 0 · Quellenlimit des Laufs erreicht (150 von 150 Quellen). Sie hat deshalb nur in den schon gelesenen Quellen gesucht; auch ein neuer Versuch sucht erst wieder im Web, wenn du das Quellenlimit erhöhst.'));
  const fullCard=app.run(`renderResearchDecisions({status:'blocked',progress:{research_questions:${JSON.stringify(full)},search_rounds:12,search_round_limit:24,source_limit:150}},{run_id:'run_x'},false,0,true,24)`);
  assert.ok(fullCard.includes('Quellen: 150 von 150 abgerufen'));
  assert.ok(fullCard.includes('data-action="approve-sources" data-run-id="run_x" data-sources="190"'));
  assert.ok(!fullCard.includes('data-action="approve-search"'));
});

test('a paused job announces its automatic resume on its page and a silent worker is flagged',()=>{
  const app=studio();
  app.run("project={id:'p',job:{id:'j2',action:'resume',status:'waiting_for_quota',started_at:new Date().toISOString(),run:{run_id:'run_y',kind:'research',stages:{}},auto_resume_at:'2099-09-22T20:31:18+00:00'}};step=PAGE.research;renderJob();");
  let card=app.elements.get('stop-card').innerHTML;
  assert.ok(card.includes('Anbieterlimit erreicht'));
  assert.ok(card.includes('Automatische Fortsetzung geplant'));
  assert.ok(card.includes('Versuch 1 von 3'));
  assert.ok(card.includes('data-action="resume" data-run-id="run_y"'));
  // A due resume that waits for another job says so instead of showing a time in the past.
  app.run("project.job.auto_resume_at=new Date(Date.now()-60000).toISOString();renderJob();");
  assert.ok(app.elements.get('stop-card').innerHTML.includes('ist fällig'));
  app.run("delete project.job.auto_resume_at;project.job.auto_resume_exhausted=true;renderJob();");
  assert.ok(app.elements.get('stop-card').innerHTML.includes('automatischen Fortsetzungen sind aufgebraucht'));
  app.run("project.job.status='running';project.job.auto_resume_exhausted=false;project.job.heartbeat_age_seconds=900;renderJob();");
  card=app.elements.get('stop-card').innerHTML;
  assert.ok(card.includes('Seit 15 Min. keine neue Meldung vom Arbeitsprozess'));
  assert.ok(!card.includes('Automatische Fortsetzung'));
  app.run("project.job.heartbeat_age_seconds=4;renderJob();");
  assert.ok(!app.elements.get('stop-card').innerHTML.includes('keine neue Meldung'));
});

test('a waiting research plan offers the approval and the cap field only while blocked and unapproved',()=>{
  const app=studio();
  const projection={tasks:29,tasks_pending:29,expected_calls_per_task:5,expected_calls_source:'project',closing_reserve:4,closing_calls:3,
    projected_calls:148,used:7,approved_limit:150,within_limit:true,seconds_per_call:270,seconds_per_call_source:'run',projected_hours:11.1,plan_hash:'h',plan_caps:[]};
  const ledger={closed:0,total:29,accepted:0,phase:'awaiting_plan_approval',active_task:null,questions:[]};
  app.run(`project={id:'p',job:{id:'j1',status:'blocked',action:'research',started_at:new Date().toISOString(),run:{run_id:'run_x',kind:'research',stages:{dossier:{status:'blocked',error:{code:'research_plan_review'}}}},
    progress:{phase:'research',activity:'Der Rechercheplan wartet auf Freigabe',research_questions:${JSON.stringify(ledger)},plan_review:{awaiting:true,approved:false,approval:null,projection:${JSON.stringify(projection)}},model_call_limit:150,model_calls:7}}};renderJob();`);
  let html=jobView(app);
  assert.ok(html.includes('Wartet auf Freigabe des Rechercheplans'));
  assert.ok(html.includes('29 Teilfragen, voraussichtlich 148 Aufrufe, etwa 11 Stunden bei 4,5 Minuten je Aufruf'));
  assert.ok(html.includes('5 Aufrufe je Teilfrage (Erfahrungswert des Projekts)'));
  assert.ok(html.includes('data-action="approve-plan"'));
  assert.ok(html.includes('data-run-id="run_x"'));
  assert.ok(html.includes('id="plan-max-tasks"'));
  assert.ok(!html.includes('>Fortsetzen<'));
  // The button posts kind plan; the field adds the cap only when it holds a whole number.
  const request=()=>JSON.parse(JSON.stringify(app.run("planApprovalRequest('run_x')")));
  assert.deepEqual(request(),{kind:'plan',run_id:'run_x'});
  app.elements.get('plan-max-tasks').value='10';
  assert.deepEqual(request(),{kind:'plan',run_id:'run_x',max_tasks:10});
  app.elements.get('plan-max-tasks').value='viele';
  assert.throws(()=>app.run("planApprovalRequest('run_x')"),/ganze Zahl/);
  app.run("project.job.progress.plan_review.projection.plan_caps=[1];project.job.progress.plan_review.projection.within_limit=false;renderJob()");
  html=jobView(app);
  assert.ok(html.includes('Obergrenze von 1 Teilfragen wurde bereits angefordert'));
  assert.ok(html.includes('Das Limit reicht dafür voraussichtlich nicht'));
  app.run("project.job.status='running';renderJob()");
  html=jobView(app);
  assert.ok(!html.includes('data-action="approve-plan"'));
  assert.ok(!html.includes('class="plan-review"'));
  app.run("project.job.status='blocked';project.job.progress.plan_review.approved=true;project.job.progress.plan_review.approval={max_tasks:null};renderJob()");
  html=jobView(app);
  assert.ok(html.includes('Rechercheplan freigegeben'));
  assert.ok(!html.includes('data-action="approve-plan"'));
  assert.ok(!html.includes('id="plan-max-tasks"'));
  assert.ok(html.includes('>Fortsetzen<'));
  app.run("project.job.progress.plan_review={awaiting:false,approved:false,projection:null,approval:null};project.job.progress.research_questions.phase='questions';renderJob()");
  html=jobView(app);
  assert.ok(!html.includes('class="plan-review"'));
  assert.ok(!html.includes('Freigabe des Rechercheplans'));
  assert.ok(html.includes('>Fortsetzen<'));
});

test('several research tasks in flight are counted, named and marked while the first stays the current question',()=>{
  const app=studio();
  const ledger={closed:0,total:3,accepted:0,phase:'questions',active_task:'a',active_tasks:['a','b','c'],questions:[
    {id:'a',question:'Frage A <x>',status:'researching',activity:'Liest Abschnitte',steps:1,read_sections:2,acceptance:['x'],findings:[]},
    {id:'b',question:'Frage B',status:'reviewing',activity:'Antwort wird geprüft',steps:2,read_sections:3,acceptance:['y'],findings:[]},
    {id:'c',question:'Frage C',status:'researching',activity:'Sucht',steps:1,read_sections:1,acceptance:['z'],findings:[]}]};
  app.run(`project={id:'p',job:{id:'j1',status:'running',action:'research',started_at:new Date().toISOString(),run:{run_id:'run_x',kind:'research',stages:{}},progress:{phase:'research',research_questions:${JSON.stringify(ledger)}}}};renderJob();`);
  const html=jobView(app);
  assert.ok(html.includes('3 Teilfragen in Arbeit: Frage A &lt;x&gt; · Frage B · Frage C'));
  assert.ok(!html.includes('<x>'));
  assert.equal((html.match(/●/g)||[]).length,3);
  const trace=app.run('renderModelTrace(project.job)');
  assert.ok(trace.includes('<p class="trace-focus">Frage A &lt;x&gt;</p>'));
  assert.ok(trace.includes('3 Teilfragen in Arbeit'));
  // One task at a time, as before: no count line and one marker; a ledger without active_tasks still marks its task.
  app.run("project.job.progress.research_questions.active_tasks=['b'];renderJob()");
  let single=jobView(app);
  assert.ok(!single.includes('Teilfragen in Arbeit'));
  assert.equal((single.match(/●/g)||[]).length,1);
  assert.ok(single.includes('● Frage B'));
  app.run("delete project.job.progress.research_questions.active_tasks;project.job.progress.research_questions.active_task='c';renderJob()");
  single=jobView(app);
  assert.equal((single.match(/●/g)||[]).length,1);
  assert.ok(single.includes('● Frage C'));
});

test('a paused research run without a dossier still offers a fresh research start',()=>{
  const app=studio();
  app.run(`project={id:'test',config:boot.defaults,research:null,job:{action:'research',status:'waiting_for_quota',run:{kind:'research',status:'waiting_for_quota'}}};`);
  let html=app.run('renderResearch()');
  assert.ok(html.includes('Die aktuelle Recherche ist noch nicht abgeschlossen'));
  assert.ok(html.includes('Recherche neu beginnen'));
  assert.ok(html.includes('neuem Plan und neuer Hochrechnung'));
  assert.ok(html.includes('<button class="secondary" data-action="research" >Neu recherchieren</button>'));
  // While a job is actually running the button is present but disabled, like every other start.
  app.run("project.job.status='running';project.job.run.status='running'");
  html=app.run('renderResearch()');
  assert.ok(html.includes('data-action="research" disabled>Neu recherchieren'));
  // A completed run without a dossier falls back to the plain start button, unchanged.
  app.run("project.job.status='completed';project.job.run.status='completed'");
  html=app.run('renderResearch()');
  assert.ok(html.includes('data-action="research" >Recherche starten'));
  assert.ok(!html.includes('Recherche neu beginnen'));
});

test('finished scripts stay visible on the production page and in the stepper after an audio run',()=>{
  const app=studio(), p=workflowProject(app);
  p.job={id:'audio-one',action:'audio',status:'completed',run:{kind:'episode_audio',status:'completed',stages:{synthesis:{status:'completed'},assembly:{status:'completed'}}}};
  p.audio_jobs=[{id:'audio-one',episode:'ep_001',status:'completed'}];
  p.episodes=[{...publishedEpisode({}),audio:['exports/ep_001/run/audio.mp3'],audio_current:true}];
  app.run(`project=${JSON.stringify(p)};step=PAGE.production;render();`);
  const page=app.elements.get('production-progress').innerHTML;
  assert.ok(page.includes('Dialog-Polishing'));
  assert.ok(page.includes('Die Skripte sind bereit'));
  assert.ok(!page.includes('Folgt automatisch'));
  assert.ok(app.elements.get('steps').innerHTML.includes('Ausarbeitung<small>Abgeschlossen'));
  assert.ok(app.elements.get('job-bar').innerHTML.includes('Vertonung abgeschlossen'));
  assert.equal(app.requests.filter(r=>r.options?.method==='POST').length,0);
});

test('the job bar names the decision and links to its page while the drawer keeps the telemetry',()=>{
  const app=studio();
  const projection={tasks:5,tasks_pending:5,expected_calls_per_task:8,expected_calls_source:'default',closing_reserve:4,closing_calls:3,projected_calls:43,used:2,approved_limit:150,within_limit:true,seconds_per_call:300,seconds_per_call_source:'default',projected_hours:3.6,plan_hash:'h',plan_caps:[]};
  app.run(`project={id:'p',config:boot.defaults,job:{id:'j1',status:'blocked',action:'research',started_at:new Date().toISOString(),run:{run_id:'run_x',kind:'research',stages:{}},
    progress:{phase:'research',activity:'Wartet',research_questions:{closed:0,total:5,accepted:0,phase:'awaiting_plan_approval',questions:[]},plan_review:{awaiting:true,approved:false,approval:null,projection:${JSON.stringify(projection)}},
      model_trace:{updated_at:new Date().toISOString(),lines:[{at:new Date().toISOString(),kind:'status',text:'Plan erstellt'}]}}}};step=PAGE.brief;renderJob();`);
  const bar=app.elements.get('job-bar').innerHTML, drawer=app.elements.get('job-status').innerHTML, page=app.elements.get('research-progress').innerHTML;
  assert.ok(bar.includes('Wartet auf Freigabe des Rechercheplans'));
  assert.ok(bar.includes('data-step="1"'));
  assert.ok(bar.includes('data-action="drawer-toggle"'));
  assert.ok(!bar.includes('data-action="resume"'));
  assert.ok(page.includes('data-action="approve-plan"'));
  assert.ok(!drawer.includes('data-action="approve-plan"'));
  assert.ok(drawer.includes('class="drawer-body" hidden'));
  assert.ok(drawer.indexOf('Plan erstellt')>-1&&drawer.indexOf('Plan erstellt')<drawer.indexOf('Für diesen Auftrag gespeichert'),'the live output comes first');
  app.run('drawerOpen=true;lastJobView="";renderJob();');
  assert.ok(app.elements.get('job-status').innerHTML.includes('class="drawer-body">'));
  assert.equal(app.requests.filter(r=>r.options?.method==='POST').length,0);
});

test('the overview lists what waits for the user before the pipeline rows',()=>{
  const app=studio();
  app.run(`boot.capabilities={project_overview:true};overviewData={projects:[
    {id:'a',topic:'Blocked <one>',job:{status:'blocked',action:'research',message:'Beleg fehlt',run:{kind:'research'}},has_research:false,has_outline:false,script_count:0,episodes:[]},
    {id:'b',topic:'Running',job:{status:'running',action:'script',run:{kind:'script'}},has_research:true,has_outline:true,script_count:0,episodes:[]},
    {id:'c',topic:'Readable',job:{status:'completed',action:'script',run:{kind:'script',status:'completed'}},has_research:true,has_outline:true,script_count:3,episodes:[{episode_id:'ep_001',title:'One',audio:[]}]},
    {id:'d',topic:'Done',job:{status:'completed',action:'audio',run:{kind:'episode_audio',status:'completed'}},has_research:true,has_outline:true,script_count:1,episodes:[{episode_id:'ep_001',title:'One',audio:['one.mp3'],audio_current:true}]}],trash:[]};`);
  const html=app.run('renderOverview()');
  const waiting=html.indexOf('Wartet auf dich'), running=html.indexOf('Läuft gerade'), rows=html.indexOf('id="overview-projects"');
  assert.ok(waiting>-1&&running>waiting&&rows>running,'decisions first, then running work, then the rows');
  assert.ok(html.includes('Blocked &lt;one&gt;'));
  assert.ok(html.includes('Beleg fehlt'));
  assert.ok(html.includes('data-open-project="a" data-open-step="1"'));
  assert.ok(html.includes('data-open-project="c" data-open-step="4"'));
  assert.ok(!html.includes('data-open-project="b" data-open-step'));
  assert.ok(html.includes('data-open-project="d" data-open-step="5"'));
  assert.ok(html.includes('data-delete-project="b" disabled'));
  assert.ok(!html.includes('<audio '));
});

test('unsaved form input survives a polling re-render of the same project',async()=>{
  const app=studio(), p=workflowProject(app);
  p.job.status='completed';p.job.run.status='completed';
  p.episodes=[{...publishedEpisode({}),audio:['exports/ep_001/run/audio.mp3'],audio_current:true}];
  await app.run(`selectProject('test',${JSON.stringify(p)},PAGE.audio)`);
  const content=app.elements.get('content');
  let markup=content.innerHTML;
  // A real DOM drops every field with the old markup; the fake one must forget the values too.
  Object.defineProperty(content,'innerHTML',{get:()=>markup,set:value=>{markup=value;app.elements.get('listening-note').value='';app.elements.get('host-name-a').value='';}});
  app.run(`$('listening-note').value='Beim Hören notiert';$('host-name-a').value='Lena';`);
  const next=structuredClone(p);
  next.job={...next.job,id:'job-two',status:'running',action:'audio'};
  app.responses.set('/api/projects/test',next);
  await app.run('poll()');
  assert.equal(app.run('step'),5);
  assert.equal(app.elements.get('listening-note').value,'Beim Hören notiert');
  assert.equal(app.elements.get('host-name-a').value,'Lena');
  assert.equal(app.requests.filter(r=>r.options?.method==='POST').length,0);
});

test('a lost connection notice clears itself on the next successful poll',async()=>{
  const app=studio(), p=workflowProject(app);
  await app.run(`selectProject('test',${JSON.stringify(p)})`);
  app.run(`const baseFetch=fetch;let fail=true;fetch=async(path,options)=>{if(fail&&path==='/api/projects/test')throw new Error('offline');return baseFetch(path,options);};window.stopFailing=()=>{fail=false;};`);
  await app.run('poll()');
  assert.ok(app.elements.get('notice').textContent.includes('Verbindung zum Studio unterbrochen'));
  assert.equal(app.elements.get('notice').hidden,false);
  app.run('window.stopFailing();');
  app.responses.set('/api/projects/test',p);
  await app.run('poll()');
  assert.equal(app.elements.get('notice').hidden,true);
  assert.equal(app.elements.get('notice').textContent,'');
});

test('sending a message scrolls the conversation to its newest reply and clears the draft',async()=>{
  const app=studio();
  const p=app.run(`({id:'test',config:boot.defaults,chat:[]})`);
  await app.run(`selectProject('test',${JSON.stringify(p)})`);
  app.run(`boot.capabilities={conversational_setup:true};$('chat-end').scrollIntoView=()=>{window.scrolledToEnd=true;};`);
  app.responses.set('/api/projects/test',p);
  await app.run(`sendSetupMessage('Worum es geht')`);
  assert.equal(app.run('window.scrolledToEnd'),true);
  assert.equal(app.elements.get('chat-message').value,'');
});

test('blocked questions are decided from a card above the ledger without expanding a row or a dialog',()=>{
  const app=studio();
  const ledger={closed:2,total:4,accepted:0,blocked:2,reopenable:0,phase:'blocked',active_task:null,
    budget_projection:{feasible:true,used:10,remaining:20,minimum_remaining_calls:3,closing_calls:3,expected_remaining_calls:3,expected_calls_per_task:5},
    questions:[{id:'root',question:'Wurzelfrage <x>',status:'blocked',outcome:'search_block',advice:{key:'0.0',diagnosis:'Beraten.',recommendation:'accept_gap',limit:'none',hint:'',sources:[]},web_attempts:1,reason:'Keine Belege.',activity:'x',steps:6,read_sections:2,acceptance:['a'],depends_on:[]},
      {id:'child',question:'Folgefrage',status:'blocked',outcome:'prerequisite_block',web_attempts:0,reason:'A required prerequisite has not passed evidence review.',activity:'y',steps:0,read_sections:0,acceptance:['b'],depends_on:['root']},
      {id:'ok',question:'Geklärt',status:'verified',answer:'A',findings:[],sources:[],limits:[],activity:'z',steps:2,read_sections:3,acceptance:['c'],depends_on:[]}]};
  app.run(`project={id:'p',config:boot.defaults,job:{id:'j1',status:'blocked',action:'research',started_at:new Date().toISOString(),run:{run_id:'run_x',kind:'research',stages:{}},progress:{phase:'research',research_questions:${JSON.stringify(ledger)},search_round_limit:12}}};step=PAGE.research;render();`);
  const page=app.elements.get('research-progress').innerHTML;
  const card=page.indexOf('class="panel decision-card"'), rows=page.indexOf('class="research-questions"');
  assert.ok(card>-1&&rows>card,'the decision card precedes the ledger');
  assert.ok(page.includes('2 Teilfragen sind blockiert'));
  assert.ok(page.includes('Wurzelfrage &lt;x&gt;'));
  assert.ok(page.includes('id="retry-hint-root"'));
  assert.ok(page.includes('data-action="retry-task" data-run-id="run_x" data-task-id="root"'));
  assert.ok(page.includes('data-action="accept-gap" data-run-id="run_x" data-task-id="child"'));
  assert.ok(page.includes('hängt an: Wurzelfrage &lt;x&gt;'));
  assert.ok(!page.includes('>Fortsetzen<'));
  assert.ok(app.elements.get('job-bar').innerHTML.includes('2 Teilfragen warten auf deine Entscheidung'));
  assert.ok(!source.includes('window.prompt'),'no modal dialog stands between the user and the decision');
  app.run("for(const q of project.job.progress.research_questions.questions)if(q.status==='blocked'){q.accepted_gap=true;q.accepted_reason='ok';}const l=project.job.progress.research_questions;l.accepted=2;l.blocked=0;l.phase='questions';render();");
  const decided=app.elements.get('research-progress').innerHTML;
  assert.ok(decided.includes('Jede blockierte Teilfrage ist entschieden'));
  assert.ok(decided.includes('data-action="resume"'));
  assert.ok(!decided.includes('data-action="accept-gap"'));
  assert.equal(app.requests.filter(r=>r.options?.method==='POST').length,0);
});

test('a requested new attempt shows in place, keeps the other decision open and makes the run resumable',async()=>{
  const app=studio();
  const ledger={closed:2,total:4,accepted:0,blocked:2,reopenable:0,retry_requested:1,phase:'blocked',active_task:null,
    questions:[{id:'root',question:'Wurzelfrage',status:'blocked',outcome:'search_block',web_attempts:1,reason:'Keine Belege.',activity:'x',steps:6,read_sections:2,acceptance:['a'],depends_on:[],retry_requested:true,retry_hint:'Originalpaper <lesen>'},
      {id:'other',question:'Andere Frage',status:'blocked',outcome:'extraction_block',web_attempts:1,reason:'Tabelle nicht lesbar.',activity:'y',steps:7,read_sections:3,acceptance:['b'],depends_on:[]}]};
  app.run(`project={id:'p',config:boot.defaults,job:{id:'j1',status:'blocked',action:'research',started_at:new Date().toISOString(),run:{run_id:'run_x',kind:'research',stages:{}},progress:{phase:'research',research_questions:${JSON.stringify(ledger)},search_rounds:11,search_round_limit:12}}};step=PAGE.research;render();`);
  const page=app.elements.get('research-progress').innerHTML;
  assert.ok(page.includes('Neuer Versuch angefordert · Hinweis: Originalpaper &lt;lesen&gt;'));
  assert.ok(!page.includes('data-task-id="root"'),'a requested retry offers no further buttons');
  assert.ok(page.includes('data-action="retry-task" data-run-id="run_x" data-task-id="other"'));
  assert.ok(page.includes('Eine Teilfrage ist blockiert'));
  assert.ok(page.includes('Suchrunden auf 18 erhöhen'),'a nearly exhausted search budget is raised from the card');
  assert.ok(page.includes('>Fortsetzen<'),'the requested attempt can start while the other decision stays open');
  assert.ok(app.elements.get('job-bar').innerHTML.includes('1 Teilfrage wartet auf deine Entscheidung'));
  app.run(`$('retry-hint-other').value='Seite 35 bis 39 als Text';`);
  app.responses.set('/api/projects/p',app.run('structuredClone(project)'));
  await app.run(`(async()=>{const button={dataset:{action:'retry-task',runId:'run_x',taskId:'other'}};const hint=$('retry-hint-'+button.dataset.taskId)?.value||'';await api('/api/projects/'+project.id+'/approve',{kind:'retry',run_id:button.dataset.runId,task_id:button.dataset.taskId,hint});})()`);
  const request=app.requests.find(r=>r.path==='/api/projects/p/approve');
  assert.deepEqual(JSON.parse(request.options.body),{kind:'retry',run_id:'run_x',task_id:'other',hint:'Seite 35 bis 39 als Text'});
  assert.equal(request.options.headers['X-Studio-Token'],'csrf');
});

test('the research card names the audit round and the job names the next step',()=>{
  const app=studio();
  app.run(`project={id:'test',job:{id:'j1',status:'running',action:'research',run:{stages:{}},progress:{phase:'research',activity:'Zuordnung der Einwände: Teil 1 von 5',
    research_questions:{closed:16,total:18,phase:'questions',audit_round:1,reopened:12,questions:[]}}}};renderJob();`);
  let html=jobView(app);
  assert.ok(html.includes('Prüfrunde 2 · 12 Teilfragen wieder geöffnet'));
  assert.ok(html.includes('Nächster Schritt:'));
  assert.ok(html.includes('Nichts zu tun, der Lauf arbeitet (Zuordnung der Einwände: Teil 1 von 5)'));
  const panel=app.elements.get('research-progress').innerHTML;
  assert.ok(panel.indexOf('Gerade:</strong> Zuordnung der Einwände: Teil 1 von 5')<panel.indexOf('Teilfragen geprüft abgeschlossen'),'the current step stands above the question rows');
  assert.ok(panel.indexOf('Nächster Schritt:')<panel.indexOf('Teilfragen geprüft abgeschlossen'),'the next step stands above the question rows');
  // A stop is explained on the research page itself, with the control its rule names.
  app.run(`step=PAGE.research;project.job.status='failed';project.job.run={run_id:'run_t',kind:'research',stages:{dossier:{status:'failed',error:{code:'timeout',message:'Zeitlimit'}}}};renderJob();`);
  html=jobView(app);
  assert.ok(html.includes('Zeitlimit eines Modellaufrufs'));
  assert.ok(html.includes('„Fortsetzen“ wiederholt ihn'));
  assert.ok(html.includes('data-action="resume" data-run-id="run_t"'));
  app.run(`project.job.status='blocked';project.job.run={run_id:'run_t',kind:'research',stages:{dossier:{status:'blocked',error:{code:'prompt_too_large',message:'zu groß'}}}};renderJob();`);
  html=jobView(app);
  assert.ok(html.includes('passt nicht in das Kontextfenster'));
  assert.ok(!html.includes('data-action="resume"'),'a prompt that does not fit is not offered the same attempt again');
  assert.ok(html.includes('data-action="research"'),'the way on is a new research run');
  app.run(`project.job.run={run_id:'run_t',kind:'research',stages:{dossier:{status:'blocked',error:{code:'research_budget_insufficient',message:'Limit'}}}};project.job.progress.model_calls=150;project.job.progress.model_call_limit=150;renderJob();`);
  html=jobView(app);
  assert.ok(html.includes('data-action="approve-calls" data-run-id="run_t" data-model-calls="200" data-then-resume="1"'));
  app.run(`project.job.progress.research_questions={closed:1,total:2,phase:'questions',audit_round:0,reopened:0,questions:[]};project.job.run={stages:{}};renderJob();`);
  assert.ok(!jobView(app).includes('Prüfrunde'));
});

// Stops as the user meets them: the page that owns the job says what happened, whether "Fortsetzen"
// can help and which control leads on (docs/studio.md, "Anhalten und Fortsetzen").
test('a dead end names its exit instead of a futile resume, and accepted gaps alone make no decision card',()=>{
  const app=studio();
  const ledger={closed:16,total:18,accepted:2,phase:'audit',questions:[
    {id:'a',question:'Angenommene Lücke',status:'blocked',accepted_gap:true,outcome:'accepted_gap',activity:'x',steps:3,read_sections:2,acceptance:['a']}]};
  app.run(`project={id:'p',config:boot.defaults,research:'Dossier',job:{id:'j1',action:'resume',status:'blocked',message:'Gespeicherte Rechercheänderung passt nicht zu ihren Eingaben.',
    stop:{code:'invalid_research_checkpoint',stage:'dossier',message:'Gespeicherte Rechercheänderung passt nicht zu ihren Eingaben.',detail:null,file:null},
    run:{run_id:'run_p',kind:'research',status:'blocked',stages:{dossier:{status:'blocked',error:{code:'invalid_research_checkpoint'}}}},
    progress:{phase:'research',activity:'Quellenprüfung in Teilen: Teil 1 von 30',research_questions:${JSON.stringify(ledger)}}}};step=PAGE.research;render();`);
  const card=app.elements.get('stop-card').innerHTML, page=app.elements.get('research-progress').innerHTML, bar=app.elements.get('job-bar').innerHTML;
  assert.ok(card.includes('Gespeicherter Zwischenstand passt nicht mehr'));
  assert.ok(card.includes('Meldung: Gespeicherte Rechercheänderung passt nicht zu ihren Eingaben.'));
  assert.ok(card.includes('data-action="research" data-confirm='),'the exit is a new research run, confirmed first');
  assert.ok(!card.includes('data-action="resume"')&&!bar.includes('data-action="resume"'),'resuming would stop at the same place');
  assert.ok(bar.includes('Gespeicherter Zwischenstand passt nicht mehr'));
  assert.ok(!page.includes('decision-card'),'accepted gaps alone are no pending decision');
  assert.ok(!page.includes('Nächster Schritt: Fortsetzen'));
  assert.ok(page.includes('Ergebnis: Als Lücke akzeptiert'));
  assert.equal(app.run('navigationStates()[1][0]'),'Neustart nötig');
});

test('a script run over its call limit gets the approval as a button that also resumes',()=>{
  const app=studio(), p=workflowProject(app);
  Object.assign(p.job,{status:'blocked',message:'Mindestens 40 weitere Modellaufrufe erforderlich',run:{run_id:'run_s',kind:'script',status:'blocked',stages:{planning:{status:'completed',attempts:1},teaching:{status:'completed',attempts:1},writing:{status:'blocked',attempts:1,error:{code:'script_budget_insufficient'}},polishing:{status:'pending'},review:{status:'pending'},publish:{status:'pending'}}},
    progress:{phase:'script',stage:'writing',activity:'Skript wird ausgearbeitet',episodes:[],model_calls:138,model_call_limit:150,budget_projection:{used:138,limit:150,remaining:12,minimum_remaining_calls:40,feasible:false}}});
  app.run(`project=${JSON.stringify(p)};step=PAGE.production;render();`);
  const card=app.elements.get('stop-card').innerHTML;
  assert.ok(card.includes('Aufruflimit reicht nicht'));
  // The minimum without repairs plus a quarter for corrections.
  assert.ok(card.includes('data-action="approve-calls" data-run-id="run_s" data-model-calls="188" data-then-resume="1"'));
  assert.ok(!card.includes('>Fortsetzen<'),'without a higher limit the run would stop again at once');
  assert.ok(!app.elements.get('job-bar').innerHTML.includes('data-action="resume"'));
});

test('a teaching concept that stays incomplete offers a new outline, never a futile resume',()=>{
  const app=studio(), p=workflowProject(app);
  p.research='Dossier';
  Object.assign(p.job,{status:'blocked',message:'Das Lehrkonzept hat auch nach der gezielten automatischen Korrektur noch offene Punkte: X',run:{run_id:'run_s',kind:'script',status:'blocked',stages:{planning:{status:'completed',attempts:1},teaching:{status:'blocked',attempts:3,error:{code:'teaching_design_failed'}},writing:{status:'pending'},polishing:{status:'pending'},review:{status:'pending'},publish:{status:'pending'}}},progress:{phase:'script',stage:'teaching',activity:'Lehrkonzept wird geprüft',episodes:[],review_issues:['Beispiel fehlt']}});
  app.run(`project=${JSON.stringify(p)};step=PAGE.production;render();`);
  const html=jobView(app);
  assert.ok(html.includes('Lehrkonzept bleibt unvollständig'));
  assert.ok(html.includes('data-action="plan" data-confirm='));
  assert.ok(!html.includes('data-action="resume"'));
  assert.ok(!html.includes('Fortsetzen wiederholt den unterbrochenen Schritt'));
  assert.ok(html.includes('Beispiel fehlt'));
});

test('a refused resume keeps its run, is named by the worker code and shows the interrupted stage',()=>{
  const app=studio(), p=workflowProject(app);
  p.research='Dossier';
  Object.assign(p.job,{action:'resume',status:'blocked',error_code:'inputs_changed',message:'Skripteingaben geändert; einen neuen Skriptlauf starten.',run:{run_id:'run_s',kind:'script',status:'pending',stages:{planning:{status:'completed',attempts:1},teaching:{status:'pending',attempts:1,error:{code:'interrupted'}},writing:{status:'pending'},polishing:{status:'pending'},review:{status:'pending'},publish:{status:'pending'}}}});
  app.run(`project=${JSON.stringify(p)};step=PAGE.production;render();`);
  const html=jobView(app);
  assert.ok(html.includes('Eingaben seit dem Anhalten geändert'),'the newer worker code wins over the older stage record');
  assert.ok(!html.includes('data-action="resume"'));
  assert.ok(html.includes('data-action="plan"'));
  assert.ok(app.elements.get('production-progress').innerHTML.includes('Unterbrochen'));
});

test('the chat shows the partner writing, a failed answer with its reason and a resend, and a limit raise',()=>{
  const app=studio(), p=workflowProject(app);
  p.chat=[{role:'user',message:'Mach es kürzer'}];
  p.job={id:'c1',action:'assistant',status:'running',started_at:new Date().toISOString(),run:null};
  app.run(`boot.capabilities={conversational_setup:true};project=${JSON.stringify(p)};step=PAGE.brief;`);
  let html=app.run('renderBrief()');
  assert.ok(html.includes('schreibt …'));
  assert.ok(!html.includes('data-action="resend-chat"'));
  app.run(`project.job={id:'c1',action:'assistant',status:'failed',error_code:'codex_failed',message:'Codex-Aufruf fehlgeschlagen.',run:null};`);
  html=app.run('renderBrief()');
  assert.ok(html.includes('Keine Antwort · Codex-Aufruf fehlgeschlagen'));
  assert.ok(html.includes('„Erneut senden“ wiederholt den Aufruf'));
  assert.ok(!html.includes('„Fortsetzen“'),'a chat has no run to continue');
  assert.ok(html.includes('data-action="resend-chat"'));
  app.run(`project.job={id:'c1',action:'assistant',status:'blocked',error_code:'research_budget_exhausted',message:'Limit von 150 Modellaufrufen erreicht.',run:null};project.chat_budget={used:150,limit:150};`);
  html=app.run('renderBrief()');
  assert.ok(html.includes('Gesprächslimit erreicht'));
  assert.ok(html.includes('data-action="approve-chat" data-model-calls="200"'));
  // A paused run shown as the project's job keeps the chat's own state in main_job.
  app.run(`project.main_job=project.job;project.job={id:'r1',action:'research',status:'blocked',run:{run_id:'run_r',kind:'research',stages:{}}};`);
  assert.ok(app.run('renderBrief()').includes('Gesprächslimit erreicht'));
});

test('a failed connection check is restarted by its own button and names its diagnostics file',()=>{
  const app=studio();
  app.run(`project={id:'p',config:boot.defaults,chat:[],job:{id:'k',action:'check',status:'failed',error_code:'processing_failed',message:'Auftrag unterbrochen oder Verarbeitung fehlgeschlagen.',stop:{code:'processing_failed',file:'studio/failures/worker_1.txt',message:'Auftrag unterbrochen oder Verarbeitung fehlgeschlagen.'},run:null}};step=PAGE.brief;render();`);
  const card=app.elements.get('stop-card').innerHTML;
  assert.ok(card.includes('Unerwarteter Programmfehler'));
  assert.ok(card.includes('data-action="check"'));
  assert.ok(!card.includes('data-action="resume"'));
  assert.ok(!card.includes('„Fortsetzen“'));
  assert.ok(card.includes('/api/projects/p/file?path=studio%2Ffailures%2Fworker_1.txt'));
  assert.ok(!app.elements.get('job-bar').innerHTML.includes('data-action="resume"'));
});

test('a Gemini job stopped for a missing key carries the key field and resumes the episode from it',()=>{
  const app=studio();
  app.run(`boot.key_available=false;boot.capabilities={parallel_audio:true};project={id:'p',config:boot.defaults,audio_settings:{provider:'openrouter_gemini_tts',voices:{host_a:'Kore',host_b:'Puck'}},audio_capacity:{limit:3,active:0,available:3},
    episodes:[{script:{episode_id:'ep_001',title:'Eins',segments:[],chapters:[]},hash:'h',readable_hash:'r',audio:[],metrics:{words:1,estimated_minutes:1}}],
    audio_jobs:[{id:'a1',episode:'ep_001',status:'blocked',message:'Für Gemini-Audio den OpenRouter-Key im Studio hinterlegen.',stop:{code:'openrouter_key_required',message:'Für Gemini-Audio den OpenRouter-Key im Studio hinterlegen.'},run:{run_id:'run_a',kind:'episode_audio',stages:{}}}]};
    project.job=project.audio_jobs[0];step=PAGE.audio;render();`);
  const panel=app.elements.get('audio-jobs').innerHTML;
  assert.ok(panel.includes('OpenRouter-Key fehlt'));
  assert.ok(panel.includes('id="stop-key"'));
  assert.ok(panel.includes('data-then-resume="1" data-run-id="run_a" data-episode="ep_001"'));
  assert.ok(!panel.includes('Diese Folge fortsetzen'),'a resume without the key would fail at its first request');
  const approval=app.run('renderAudio()');
  assert.ok(approval.includes('id="audio-key"'));
  assert.ok(approval.includes('Zuerst den OpenRouter-Key hinterlegen.'));
});

test('audio jobs show model loading, chapters and the montage instead of a finished counter',()=>{
  const app=studio();
  app.run(`project={id:'p',config:boot.defaults,audio_settings:{provider:'qwen3_local',voices:{host_a:'Aiden',host_b:'Vivian'}},episodes:[{script:{episode_id:'ep_001',title:'Eins',segments:[],chapters:[]},hash:'h',readable_hash:'r',audio:[],metrics:{words:1,estimated_minutes:1}}],audio_jobs:[],
    job:{id:'q1',action:'audio',episode:'ep_001',status:'running',started_at:new Date().toISOString(),heartbeat_age_seconds:600,run:{run_id:'run_q',kind:'episode_audio',stages:{synthesis:{status:'running'}}},
      progress:{status:'synthesis',chapter:2,chapters:5,chapter_title:'Zwei',completed_segments:10,total_segments:40,tts_status:'loading_model'}}};step=PAGE.audio;render();`);
  let panel=app.elements.get('audio-jobs').innerHTML;
  assert.ok(panel.includes('Eins · Wird vertont'),'the local job has its card on the audio page');
  assert.ok(panel.includes('10 von 40 Sprechabschnitten fertig · Kapitel 2 von 5: Zwei'));
  assert.ok(panel.includes('Sprachmodell wird für dieses Kapitel geladen'));
  assert.ok(!panel.includes('keine neue Meldung'),'loading the model may take longer than a segment');
  assert.ok(panel.includes('data-action="stop"'));
  app.run(`project.job.progress={status:'assembly',step:'normalize',part:1,parts:2,completed_segments:12,total_segments:40};project.job.heartbeat_age_seconds=20;lastJobView='';renderJob();`);
  panel=app.elements.get('audio-jobs').innerHTML;
  assert.ok(panel.includes('Audio wird zusammengefügt: Sprechabschnitte werden angeglichen · 12 von 40 · Teil 1 von 2'));
  app.run(`project.job.progress={status:'assembly',step:'encode',part:1,parts:1,completed_segments:40,total_segments:40};renderJob();`);
  panel=app.elements.get('audio-jobs').innerHTML;
  assert.ok(panel.includes('MP3 mit Kapitelmarken wird erstellt'));
  assert.ok(!panel.includes('40 von 40 Sprechabschnitten'));
  app.run(`project.job.progress={status:'synthesis',completed_segments:10,total_segments:40};project.job.heartbeat_age_seconds=600;renderJob();`);
  assert.ok(app.elements.get('audio-jobs').innerHTML.includes('Seit 10 Min. keine neue Meldung'));
});

test('the overview lists a stopped audio episode and marks reading as done once audio exists',()=>{
  const app=studio();
  const project={id:'p',topic:'Serie',has_research:true,has_outline:true,script_count:2,episodes:[{episode_id:'ep_001',title:'Eins',audio:['exports/a.mp3']},{episode_id:'ep_002',title:'Zwei',audio:[]}],
    job:{id:'a1',status:'completed',action:'audio'},audio_jobs:[{id:'a2',episode:'ep_002',status:'failed',error_code:'openrouter_credits',message:'Guthaben erschöpft',run:{run_id:'r2',kind:'episode_audio',stages:{}}}]};
  app.run(`overviewData={projects:[${JSON.stringify(project)}],trash:[]};overviewPage=true;`);
  const attention=app.run(`attentionOf(overviewData.projects[0])`);
  assert.equal(attention.page,5);
  assert.ok(attention.text.includes('Vertonung von „Zwei“ angehalten: OpenRouter-Guthaben erschöpft'));
  assert.equal(app.run('pipelineStates(overviewData.projects[0])[4]'),'done');
  app.run('renderNavigation()');
  assert.equal(app.run('document.title'),'(1) Podcast Studio');
});

test('saving what a paused run is bound to warns before the save',()=>{
  const app=studio(), p=workflowProject(app);
  Object.assign(p.job,{status:'review_ready',run:{run_id:'run_o',kind:'script',status:'pending',stages:{planning:{status:'completed',attempts:1}}}});
  p.style_notes='';
  app.run(`project=${JSON.stringify(p)};`);
  assert.ok(app.run('renderStyleNotes()').includes('Ein angehaltener Lauf (Inhaltsverzeichnis oder Ausarbeitung)'));
  app.run(`window.confirm=message=>{window.lastConfirm=message;return false;};`);
  assert.equal(app.run('confirmPaused(["notes"])'),false);
  assert.ok(app.run('window.lastConfirm').includes('lässt sich nach dem Speichern nicht mehr fortsetzen'));
  assert.equal(app.run('confirmPaused(["audio"])'),true,'spoken forms do not bind an outline');
  app.run(`project.job.status='completed';project.job.run.status='completed';`);
  assert.ok(!app.run('renderStyleNotes()').includes('angehaltener Lauf'));
});

test('drafting and revising the outline are named as such, with the previous draft marked as not approvable',()=>{
  const app=studio(), p=workflowProject(app);
  p.outline.approval=null;
  Object.assign(p.job,{action:'replan',status:'running',run:{run_id:'run_o',kind:'script',status:'running',stages:{planning:{status:'running',attempts:1}}},progress:{phase:'script',stage:'planning',activity:'Inhaltsverzeichnis wird korrigiert · Korrekturrunde 2 von 3',episodes:[]}});
  app.run(`project=${JSON.stringify(p)};step=PAGE.outline;render();`);
  assert.ok(app.elements.get('job-bar').innerHTML.includes('Inhaltsverzeichnis wird überarbeitet'));
  const html=app.elements.get('content').innerHTML;
  assert.ok(html.includes('Das Inhaltsverzeichnis wird überarbeitet.'));
  assert.ok(html.includes('Korrekturrunde 2 von 3'));
  app.run(`project.job.action='plan';project.outline=null;lastJobView='';render();`);
  assert.ok(app.elements.get('job-bar').innerHTML.includes('Inhaltsverzeichnis entsteht'));
  assert.ok(!app.elements.get('job-bar').innerHTML.includes('Ausarbeitung läuft'));
});

test('an outline that stays contradictory is redrafted with a hint instead of resumed',()=>{
  const app=studio();
  app.run(`project={id:'p',config:boot.defaults,research:'Dossier',outline:null,job:{id:'j',action:'plan',status:'blocked',message:'Das Inhaltsverzeichnis enthält nach der automatischen Korrektur noch einen Widerspruch.',
    run:{run_id:'run_o',kind:'script',status:'blocked',stages:{planning:{status:'blocked',attempts:1,error:{code:'invalid_plan'}}}}}};step=PAGE.outline;render();`);
  const card=app.elements.get('stop-card').innerHTML;
  assert.ok(card.includes('Inhaltsverzeichnis bleibt widersprüchlich'));
  assert.ok(card.includes('id="stop-feedback"'));
  assert.ok(card.includes('data-action="replan" data-feedback="stop-feedback"'));
  assert.ok(!card.includes('data-action="resume"'));
});

test('a restarted server renews the session token once instead of failing every click',async()=>{
  const app=studio();
  await new Promise(resolve=>setTimeout(resolve,0)); // the page's own start-up read replaces boot first
  // Reads need no token; only the POST meets the restarted server's check.
  app.run(`window.fetchCalls=0;fetch=async(path,options)=>{if(path==='/api/bootstrap')return{ok:true,json:async()=>({token:'fresh',key_available:false,projects:[]})};
    if(!options?.method)return{ok:true,json:async()=>({projects:[],trash:[]})};window.fetchCalls++;
    if(options?.headers?.['X-Studio-Token']!=='fresh')return{ok:false,status:403,json:async()=>({error:'Studio-Sitzung neu laden.',code:'forbidden'})};
    return{ok:true,json:async()=>({saved:true})};};boot.key_available=true;`);
  const result=await app.run(`api('/api/projects/p/save',{a:1})`);
  assert.equal(result.saved,true);
  assert.equal(app.run('boot.token'),'fresh');
  assert.equal(app.run('window.fetchCalls'),2);
  assert.ok(app.elements.get('notice').textContent.includes('OpenRouter-Key muss erneut eingegeben werden'));
});

test('a refusal of the running server is not reported as a lost connection',async()=>{
  const app=studio(), p=workflowProject(app);
  await app.run(`selectProject('test',${JSON.stringify(p)})`);
  app.run(`fetch=async()=>({ok:false,status:400,json:async()=>({error:'Projekt nicht gefunden.',code:'unknown_project'})});`);
  await app.run('poll()');
  assert.ok(app.elements.get('notice').textContent.includes('Das Studio meldet: Projekt nicht gefunden.'));
  assert.equal(app.run('connectionLost'),false);
});

test('the first source retrieval shows a counter and a readable report of unread sources',()=>{
  const app=studio();
  app.run(`project={id:'p',config:boot.defaults,job:{id:'j',action:'research',status:'running',started_at:new Date().toISOString(),run:{run_id:'run_r',kind:'research',status:'running',stages:{retrieval:{status:'running'}}},
    progress:{phase:'research',activity:'Originaltexte werden eingelesen',retrieval:{running:true,attempted:7,imported:5,total:20,failed:2,failures:[{source:'https://x.test/a.pdf',reason:'Quellenabruf fehlgeschlagen (HTTP 403).'},{source:'https://x.test/b',reason:'Identischer Quellentext bereits eingelesen.'}]}}}};step=PAGE.research;render();`);
  const page=app.elements.get('research-progress').innerHTML;
  assert.ok(page.includes('7 von bis zu 20 Quellen abgerufen, 5 lesbar, 2 nicht eingelesen'));
  assert.ok(page.includes('Abrufbericht · 2 Quellen nicht eingelesen'));
  assert.ok(page.includes('HTTP 403'));
});

test('an accepted gap can carry its reason and the connection check names fixes in German',()=>{
  const app=studio();
  const html=app.run(`gapActionsFor({id:'t1',outcome:'evidence_block',reason:'x'},'run_x',12)`);
  assert.ok(html.includes('id="gap-reason-t1"'));
  const checks=app.run(`renderChecks({ready:false,checks:[{name:'codex_login',ok:false,detail:'Nicht angemeldet'},{name:'ffmpeg',ok:true,detail:'C:/ffmpeg.exe'}]})`);
  assert.ok(checks.includes('Codex-Anmeldung'));
  assert.ok(checks.includes('codex login'));
  assert.ok(!checks.includes('codex_login'));
  assert.ok(checks.includes('Noch nicht startbereit'));
  assert.ok(!checks.split('FFmpeg')[1].includes('setup-ffmpeg'),'a passed check needs no fix');
});

test('the plan approval starts the research in one click and offers a higher limit when the plan does not fit',()=>{
  const app=studio();
  const projection={tasks:20,expected_calls_per_task:5,expected_calls_source:'default',closing_calls:3,projected_calls:180,used:7,approved_limit:150,within_limit:false,seconds_per_call:270,seconds_per_call_source:'default',projected_hours:13,plan_caps:[]};
  const html=app.run(`renderPlanReview({status:'blocked',progress:{plan_review:{awaiting:true,approved:false,projection:${JSON.stringify(projection)}}}},'run_x')`);
  assert.ok(html.includes('data-action="approve-plan" data-run-id="run_x" data-then-resume="1"'));
  assert.ok(html.includes('Rechercheplan freigeben und starten'));
  assert.ok(html.includes('data-action="approve-calls" data-run-id="run_x" data-model-calls="205"'));
});

test('the tab title shows a running job, a decision or a stop',()=>{
  const app=studio(), p=workflowProject(app);
  app.run(`project=${JSON.stringify(p)};renderNavigation();`);
  assert.ok(app.run('document.title').startsWith('● '));
  app.run(`project.job.status='review_ready';renderNavigation();`);
  assert.ok(app.run('document.title').startsWith('▲ '));
  app.run(`project.job.status='failed';renderNavigation();`);
  assert.ok(app.run('document.title').startsWith('! '));
});

test('a chat reply does not move the page to a paused run that waits behind it',async()=>{
  const app=studio(), p=workflowProject(app);
  p.job={id:'c1',action:'assistant',status:'running',started_at:new Date().toISOString(),run:null};
  p.main_job=p.job;
  await app.run(`selectProject('test',${JSON.stringify(p)},PAGE.brief)`);
  app.run('followWorkflow=true');
  const next=structuredClone(p);
  next.main_job={...p.job,status:'completed'};
  next.job={id:'r1',action:'research',status:'blocked',run:{run_id:'run_r',kind:'research',stages:{}}};
  app.responses.set('/api/projects/test',next);
  await app.run('poll()');
  assert.equal(app.run('step'),0);
  assert.ok(app.elements.get('job-bar').innerHTML.includes('Recherche ansehen'),'the paused run is one click away');
});

// Parallel runs: several sub-questions, episodes or voicings at once are shown as several, and a stop of one is visible.
test('a stopped parallel research run no longer shows its tasks as in work',()=>{
  const app=studio();
  const q=(id,status)=>({id,question:'Frage '+id,status,activity:'…',steps:2,read_sections:3,acceptance:['x']});
  app.run(`project={id:'p',config:boot.defaults,job:{id:'j',action:'resume',status:'failed',error_code:'processing_failed',message:'Fehler',run:{run_id:'r',kind:'research',status:'failed',stages:{}},
    progress:{phase:'research',activity:'Antwort wird geprüft',research_questions:{closed:1,total:4,phase:'questions',active_tasks:['a','b','c'],questions:${JSON.stringify([q('a','researching'),q('b','reviewing'),q('c','researching'),q('d','verified')])}}}}};step=PAGE.research;render();`);
  const page=app.elements.get('research-progress').innerHTML;
  assert.ok(!page.includes('● Frage'),'nothing is in work while the run is stopped');
  assert.ok(!page.includes('Teilfragen in Arbeit'));
  assert.ok(page.includes('○ Frage a · Begonnen · geht beim Fortsetzen weiter'));
});

test('a running parallel research run names every open call, labels its live lines and shows the wind-down after a failure',()=>{
  const app=studio(), now=Date.now(), at=m=>new Date(now-m*60000).toISOString();
  app.run(`project={id:'p',config:boot.defaults,job:{id:'j',action:'research',status:'running',started_at:'${at(30)}',run:{run_id:'r',kind:'research',status:'running',stages:{}},
    progress:{phase:'research',activity:'Antwort wird geprüft',model_call_started_at:'${at(6)}',updated_at:new Date().toISOString(),
      open_calls:[{call:'call_011',started_at:'${at(6)}',label:'Wie wirkt X?'},{call:'call_012',started_at:'${at(2)}',label:'Warum Y?'}],
      call_labels:{call_011:'Wie wirkt X?',call_012:'Warum Y?'},stopping:{question:'Was ist Z?',code:'timeout'},
      model_trace:{lines:[{call:'call_012',kind:'text',at:'${at(1)}',text:'Belege vergleichen'}]},
      research_questions:{closed:0,total:3,phase:'questions',active_tasks:['a','b'],questions:[{id:'a',question:'Wie wirkt X?',status:'researching',activity:'x',steps:1,read_sections:1,acceptance:['x']},{id:'b',question:'Warum Y?',status:'researching',activity:'y',steps:1,read_sections:1,acceptance:['y']}]}}}};step=PAGE.research;render();`);
  const page=app.elements.get('research-progress').innerHTML;
  assert.ok(page.includes('2 Modellaufrufe laufen gleichzeitig: Wie wirkt X? · seit 6 Min.; Warum Y? · seit 2 Min.'));
  assert.ok(page.includes('Zuletzt gemeldet:'),'the single activity line is the latest report, not the one current step');
  assert.ok(page.includes('Eine Teilfrage ist angehalten: „Was ist Z?“'));
  assert.ok(page.includes('● Wie wirkt X?')&&page.includes('● Warum Y?'));
  const drawer=app.elements.get('job-status').innerHTML;
  assert.ok(drawer.includes('Live-Text · Warum Y?'),'a live line names its sub-question');
  assert.ok(drawer.includes('2 Modellaufrufe laufen gleichzeitig, der älteste seit 6 Min.'));
});

test('parallel script episodes are all shown in work, with their time, and a failed one winds the stage down',()=>{
  const app=studio(), p=workflowProject(app), started=new Date(Date.now()-5*60000).toISOString();
  Object.assign(p.job,{action:'script',run:{run_id:'r',kind:'script',status:'running',stages:{planning:{status:'completed',attempts:1},teaching:{status:'completed',attempts:1},writing:{status:'running',attempts:1}}},
    progress:{phase:'script',stage:'writing',activity:'Skript wird ausgearbeitet',current_episode:'ep_001',episode_number:1,episode_title:'Eins',completed_segments:0,total_segments:4,
      active_episodes:['ep_001','ep_002'],stopping:{episodes:['Drei']},execution:{text:'parallel'},
      episodes:[{episode_id:'ep_001',title:'Eins',stage_status:'running',stage_started_at:started},{episode_id:'ep_002',title:'Zwei',stage_status:'running',stage_started_at:started},
        {episode_id:'ep_003',title:'Drei',stage_status:'interrupted'},{episode_id:'ep_004',title:'Vier',stage_status:'pending'}]}});
  app.run(`project=${JSON.stringify(p)};step=PAGE.production;render();`);
  const html=app.elements.get('production-progress').innerHTML;
  assert.ok(html.includes('2 Folgen in Arbeit · Skript'));
  assert.ok(html.includes('● Eins <span class="hint">· seit 5 Min.</span>'));
  assert.ok(html.includes('● Zwei'));
  assert.ok(html.includes('! Drei'));
  assert.ok(html.includes('○ Vier'));
  assert.ok(html.includes('Drei: angehalten.'));
  assert.ok(html.includes('Zuletzt gestartet: Skript wird ausgearbeitet'));
  assert.ok(!app.elements.get('job-status').innerHTML.includes('Folge 1 von 4 · Skript wird ausgearbeitet'),'the drawer does not single out one episode');
  // The issues of a stopped parallel stage name their episode.
  app.run(`project.job.status='blocked';project.job.run.status='blocked';project.job.progress.stage='review';project.job.progress.review_issues=['Beleg fehlt'];project.job.progress.issues_episode='Drei';lastJobView='';renderJob();`);
  assert.ok(app.elements.get('production-progress').innerHTML.includes('Offene Punkte der Qualitätsprüfung · Drei'));
});

test('a stopped Gemini episode stays visible while the others are voiced and resumes within the free slots',()=>{
  const app=studio();
  app.run(`boot.key_available=true;boot.capabilities={parallel_audio:true};project={id:'p',config:boot.defaults,audio_settings:{provider:'openrouter_gemini_tts',voices:{host_a:'Kore',host_b:'Puck'}},audio_capacity:{limit:3,active:2,available:1},
    episodes:['ep_001','ep_002','ep_003','ep_004'].map((id,i)=>({script:{episode_id:id,title:'F'+(i+1),segments:[],chapters:[]},hash:'h',readable_hash:'r',audio:i===3?['exports/a.mp3']:[],audio_current:i===3,metrics:{words:1,estimated_minutes:1}})),
    audio_jobs:[{id:'a1',episode:'ep_001',status:'running',started_at:new Date().toISOString(),progress:{completed_segments:3,total_segments:9},run:{run_id:'x1',kind:'episode_audio'}},
      {id:'a2',episode:'ep_002',status:'failed',error_code:'openrouter_speech_request',message:'Anfrage fehlgeschlagen',run:{run_id:'x2',kind:'episode_audio',stages:{}}},
      {id:'a3',episode:'ep_003',status:'running',started_at:new Date().toISOString(),progress:{completed_segments:1,total_segments:9},run:{run_id:'x3',kind:'episode_audio'}},
      {id:'a4',episode:'ep_004',status:'failed',error_code:'openrouter_speech_request',message:'alt',run:{run_id:'x4',kind:'episode_audio',stages:{}}}]};
    project.job=project.audio_jobs[0];step=PAGE.audio;render();`);
  assert.ok(app.elements.get('job-bar').innerHTML.includes('2 Folgen werden vertont · 1 angehalten'),'an older stop of a since voiced episode is not counted');
  assert.deepEqual(JSON.parse(JSON.stringify(app.run('navigationStates()[5]'))),['Läuft · 1 angehalten','running']);
  const panel=app.elements.get('audio-jobs').innerHTML;
  const card=panel.slice(panel.indexOf('F2 ·'),panel.indexOf('F3 ·'));
  assert.ok(card.includes('data-action="resume" data-run-id="x2" data-episode="ep_002" >'),'a free slot lets it resume beside the running ones');
  app.run(`project.audio_capacity={limit:3,active:3,available:0};lastJobView='';renderJob();`);
  const full=app.elements.get('audio-jobs').innerHTML;
  assert.ok(full.includes('Alle Plätze sind belegt'));
  const overview={id:'p',topic:'Serie',episodes:[{episode_id:'ep_001',title:'F1',audio:[]},{episode_id:'ep_002',title:'F2',audio:[]},{episode_id:'ep_003',title:'F3',audio:[]}],
    job:{id:'a1',status:'running',action:'audio'},audio_jobs:[{id:'a2',episode:'ep_002',status:'failed',error_code:'openrouter_speech_request',run:{run_id:'x2'}},{id:'a3',episode:'ep_003',status:'failed',error_code:'openrouter_credits',run:{run_id:'x3'}}]};
  const attention=app.run(`attentionOf(${JSON.stringify(overview)})`);
  assert.ok(attention.text.startsWith('2 Folgen angehalten: „F2“'),attention.text);
  assert.ok(attention.text.includes('„F3“ (OpenRouter-Guthaben erschöpft)'));
});

test('a paused text run waits for running episodes instead of offering a resume that would fail',()=>{
  const app=studio();
  app.run(`project={id:'p',config:boot.defaults,audio_jobs:[{id:'a1',episode:'ep_001',status:'running',started_at:new Date().toISOString(),run:{run_id:'x1',kind:'episode_audio'}}],
    job:{id:'j',action:'resume',status:'interrupted',run:{run_id:'r',kind:'research',status:'pending',stages:{dossier:{status:'pending',error:{code:'interrupted'}}}}}};step=PAGE.research;render();`);
  const bar=app.elements.get('job-bar').innerHTML, card=app.elements.get('stop-card').innerHTML;
  assert.ok(!bar.includes('data-action="resume"'));
  assert.ok(bar.includes('Fortsetzen, sobald die Vertonung fertig ist'));
  assert.ok(card.includes('data-action="resume" data-run-id="r" disabled'));
  assert.ok(card.includes('Gerade wird eine Folge vertont. Fortsetzen und Neustart gehen, sobald die Vertonung fertig ist'));
});
