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
  assert.ok(card.includes('Empfehlung: Quellenlimit erhöhen · Automatische neue Versuche: 1 von 5</p>'));
  // A limit raise is the editor's decision: no one-click adoption for it.
  assert.ok(!card.includes('data-action="apply-advice"'));
  assert.ok(card.includes('<a href="https://pmc.example/salganik" target="_blank" rel="noopener noreferrer">Salganik 2020</a>'));
  assert.ok(card.includes('<li>Ohne Adresse</li>'));
  // The advisor's hint is already in the field of a new attempt; the editor may change it before retrying.
  assert.ok(card.includes('id="retry-hint-t18" value="Freie Fassung in PubMed Central lesen."'));
  const html=app.run(`renderResearchQuestions(${JSON.stringify(ledger)},new Set(),false,'run_x',24,1,150)`);
  assert.ok(html.includes('<p><strong>Beratung:</strong> Der Verlag sperrt den Download &lt;PNAS&gt;.</p>'));
});

test('the automatic choice at high names the shared level and its own preset',()=>{
  const app=studio();
  app.run(`boot.text_catalog={presets:[{id:'auto_subscriptions',label:'Automatisch · Claude, sonst Codex',provider:'auto',model:null,reasoning_effort:null},
    {id:'auto_subscriptions_high',label:'Automatisch · Claude, sonst Codex · high',provider:'auto',model:null,reasoning_effort:'high'}],
    auto_candidates:{codex_cli:{model:'gpt-6-astra',reasoning_effort:'xhigh'},claude_code:{model:'claude-opus-5-5',reasoning_effort:'xhigh'}}};`);
  assert.ok(app.run(`textChoiceSummary({provider:'auto',model:null,reasoning_effort:'high'})`).includes('Codex gpt-6-astra (high) · Claude claude-opus-5-5 (high)'));
  assert.ok(app.run(`textChoiceSummary({provider:'auto',model:null,reasoning_effort:null})`).includes('Codex gpt-6-astra (xhigh) · Claude claude-opus-5-5 (xhigh)'));
  // Each automatic preset matches only its own level.
  const high={provider:'auto',model:null,reasoning_effort:'high'}, plain={provider:'auto',model:null,reasoning_effort:null};
  assert.equal(app.run(`boot.text_catalog.presets.filter(p=>presetMatches(p,${JSON.stringify(high)})).map(p=>p.id).join()`),'auto_subscriptions_high');
  assert.equal(app.run(`boot.text_catalog.presets.filter(p=>presetMatches(p,${JSON.stringify(plain)})).map(p=>p.id).join()`),'auto_subscriptions');
});

test('a second mark shows the whole-dossier audit next to the question’s own check',()=>{
  const app=studio();
  const q=(id,status,objections)=>({id,question:id,status,activity:'x',steps:3,read_sections:2,acceptance:['k'],reopened:1,objections});
  const ledger={closed:2,total:4,accepted:0,phase:'questions',audit_round:1,questions:[
    q('a','verified',[{rule:'claim_preservation',reason:'„potential“ fehlt.'}]),q('b','verified',[]),
    q('c','researching',[{rule:'support',reason:'Beleg fehlt.'},{rule:'criterion',reason:'Kriterium 2.'}]),q('d','pending',[])]};
  const html=app.run(`renderResearchQuestions(${JSON.stringify(ledger)},new Set(),true,'run_x',24,1,150)`);
  // Reworked and checked again: the objection waits for the next round; that is no warning.
  assert.ok(html.includes('✓<span class="audit-mark" title="Gesamtprüfung: 1 Einwand nachgebessert, Prüfrunde 2 prüft nach">◐</span> a · Geprüft abgeschlossen · Gesamtprüfung: 1 Einwand nachgebessert, Prüfrunde 2 prüft nach</summary>'));
  assert.ok(html.includes('✓<span class="audit-mark" title="Gesamtprüfung bestanden">✓</span> b · Geprüft abgeschlossen · Gesamtprüfung bestanden</summary>'));
  assert.ok(html.includes('<span class="audit-mark" title="Gesamtprüfung: 2 Einwände offen, wird nachgebessert">⚠</span> c'));
  assert.ok(html.includes('○<span class="audit-mark" title="Gesamtprüfung steht noch aus">○</span> d'));
  assert.ok(html.includes('Erstes Zeichen: Prüfung der einzelnen Frage'));
  assert.ok(html.includes('Gesamtprüfung: 1 bestanden, 1 nachgebessert, warten auf Prüfrunde 2, 1 mit offenem Einwand in Arbeit, 1 ausstehend.'));
  // The open objections stand in the question itself.
  assert.ok(html.includes('<strong>Einwände der Gesamtprüfung (nachgebessert, Prüfrunde 2 prüft nach):</strong></p><ul><li>„potential“ fehlt.</li></ul>'));
  // Before the first audit no question has passed it; a ledger without objection data shows no second mark at all.
  const before={...ledger,audit_round:0,questions:[q('b','verified',[])]};
  assert.ok(app.run(`renderResearchQuestions(${JSON.stringify(before)},new Set(),false,'run_x',24,1,150)`).includes('✓<span class="audit-mark" title="Gesamtprüfung steht noch aus">○</span> b'));
  const old={...ledger,questions:[{...q('b','verified',[]),objections:undefined}]};
  const plain=app.run(`renderResearchQuestions(${JSON.stringify(old)},new Set(),false,'run_x',24,1,150)`);
  assert.ok(!plain.includes('audit-mark')&&!plain.includes('Zweites Zeichen'));
});

test('when the automatic attempts stop, one click adopts every retry advice and resumes',async()=>{
  const app=studio();
  await new Promise(resolve=>setTimeout(resolve,0)); // the page's own start-up read settles before the project is set
  const advice=(key,hint)=>({key,diagnosis:'Neue Quelle nötig.',recommendation:'retry',limit:'none',hint,sources:[]});
  const row=(id,extra)=>({id,question:id,status:'blocked',outcome:'evidence_block',web_attempts:1,reason:'Fehlt.',activity:'x',steps:9,read_sections:3,acceptance:['k'],reopened:0,...extra});
  const ledger={closed:0,total:4,accepted:0,phase:'blocked',auto_retry_limit:5,questions:[
    row('a',{auto_retries:5,auto_stop:'limit',advice:advice('0.5','Hinweis A')}),
    row('b',{auto_retries:2,auto_stop:'no_progress',advice:advice('0.2','Hinweis B')}),
    row('c',{auto_retries:1,advice:{...advice('0.1',''),recommendation:'accept_gap'}}),
    row('d',{auto_retries:0,advice:advice('0.0','alt'),retries:1}),
    row('e',{auto_retries:0,advice:{...advice('0.0','Hinweis E'),recommendation:'raise_limit',limit:'sources'}})]};
  app.run(`project={id:'p',config:boot.defaults,job:{id:'j1',status:'blocked',action:'research',started_at:new Date().toISOString(),run:{run_id:'run_x',kind:'research',stages:{}},progress:{phase:'research',research_questions:${JSON.stringify(ledger)},search_round_limit:24}}};overviewPage=false;step=PAGE.research;render();`);
  const page=jobView(app);
  assert.ok(page.includes('Automatische neue Versuche: 5 von 5 · Die 5 automatischen Versuche sind ausgeschöpft.'));
  assert.ok(page.includes('Automatische neue Versuche: 2 von 5 · Der letzte automatische Versuch hat keinen neuen Abschnitt gelesen'));
  // Only current retry advice counts, a raised limit included: not a gap recommendation, not advice from before a new attempt.
  assert.ok(page.includes('data-action="apply-advice" data-run-id="run_x">Empfehlungen übernehmen und fortsetzen</button><span class="hint">3 Teilfragen mit dem Hinweis des Beraters erneut versuchen.'));
  app.run(`$('retry-hint-b').value='Hinweis B, ergänzt';`);
  app.responses.set('/api/projects/p',app.run('structuredClone(project)'));
  await app.run(`applyAdvice({dataset:{runId:'run_x'}})`);
  const approvals=app.requests.filter(r=>r.path==='/api/projects/p/approve').map(r=>JSON.parse(r.options.body));
  assert.deepEqual(approvals,[{kind:'retry',run_id:'run_x',task_id:'a',hint:'Hinweis A'},{kind:'retry',run_id:'run_x',task_id:'b',hint:'Hinweis B, ergänzt'},{kind:'retry',run_id:'run_x',task_id:'e',hint:'Hinweis E'}]);
  assert.deepEqual(JSON.parse(app.requests.find(r=>r.path==='/api/projects/p/start').options.body),{action:'resume',run_id:'run_x'});
  // Reworking reopened questions is named as such, with what is still open.
  const round=app.run(`researchRound({phase:'questions',audit_round:1,reopened:18,questions:[{reopened:1,status:'verified'},{reopened:1,status:'researching'}]})`);
  assert.ok(round.includes('Nachbesserung nach Prüfrunde 1</strong> · 18 Teilfragen wieder geöffnet, davon 1 noch offen. Danach wird das Dossier neu zusammengesetzt und in Prüfrunde 2 geprüft.'));
  assert.ok(app.run(`researchRound({phase:'audit',audit_round:1,reopened:18})`).includes('Prüfrunde 2</strong> · Gesamtprüfung läuft'));
});

test('a criterion whose source refused retrieval is accepted as an access gap from the card and the run resumes',async()=>{
  const app=studio();
  await new Promise(resolve=>setTimeout(resolve,0)); // the page's own start-up read settles before the project is set
  const refused='https://link.springer.com/chapter/10.1007/978-3-031-77847-6_18';
  const row={id:'t03',question:'Benchmark',status:'blocked',outcome:'evidence_block',web_attempts:2,steps:57,read_sections:9,reopened:0,activity:'x',
    reason:'Die unabhängige Prüfung hat die Antwort abgewiesen. Unerfüllt: Kriterium 2: Verifies the 65.63% figure',
    acceptance:['Explains the mechanism','Reports the design','Verifies the 16% vs 54% and 72% vs 65.63% figures'],
    advice:{key:'6.1',diagnosis:'Zugriffssperre.',recommendation:'accept_gap',limit:'none',hint:'',sources:[]}};
  const ledger={closed:16,total:18,accepted:0,phase:'blocked',questions:[row,
      {id:'t17',question:'Synthese',status:'blocked',outcome:'prerequisite_block',web_attempts:0,steps:0,read_sections:0,reopened:0,activity:'y',reason:'',acceptance:['s'],depends_on:['t03']}],
    blocked_sources:[{url:refused,evidence:'Bot-Abwehrseite statt Inhalt („Client Challenge“)'},{url:'https://publisher.example/b',evidence:'Quellenabruf fehlgeschlagen (HTTP 403).'}]};
  app.run(`project={id:'p',config:boot.defaults,job:{id:'j1',status:'blocked',action:'research',started_at:new Date().toISOString(),run:{run_id:'run_x',kind:'research',stages:{}},progress:{phase:'research',research_questions:${JSON.stringify(ledger)},search_round_limit:46}}};overviewPage=false;step=PAGE.research;render();`);
  const page=jobView(app);
  assert.ok(page.includes('data-action="accept-access-gap" data-run-id="run_x" data-task-id="t03"'));
  // The criterion the review failed is preselected; every refused address of the run is offered.
  assert.ok(page.includes('<option value="2" selected>Kriterium 2: Verifies the 16% vs 54% and 72% vs 65.63% figures</option>'));
  assert.ok(page.includes(`<option value="${refused}">`)&&page.includes('Bot-Abwehrseite statt Inhalt'));
  // The refused source is chosen, never preselected: the first address of the list was the wrong one once.
  assert.ok(page.includes('<option value="" selected>Gesperrte Quelle wählen …</option>'));
  await assert.rejects(app.run(`acceptAccessGap({dataset:{runId:'run_x',taskId:'t03'}})`),/gesperrte Quelle wählen/);
  assert.ok(!app.requests.some(r=>r.path==='/api/projects/p/approve'),'nothing is approved without a chosen source');
  assert.ok(!page.includes('id="access-criterion-t17"'),'a question that only waits for its prerequisite has nothing to narrow');
  app.run(`project.job.progress.research_questions.blocked_sources=[];lastJobView='';render();`);
  assert.ok(!jobView(app).includes('data-action="accept-access-gap"'),'without a refused source there is no access gap to accept');
  app.run(`project.job.progress.research_questions.blocked_sources=${JSON.stringify(ledger.blocked_sources)};lastJobView='';render();`);
  // The reloaded project shows the gap as requested: the last open decision, so the run resumes at once.
  const decided=app.run('structuredClone(project)');
  decided.job.progress.research_questions.questions[0]={...row,access_gap_requested:true,requested_access_gaps:[{task_id:'t03',criterion:2,source:refused}]};
  decided.job.progress.research_questions.phase='questions';
  app.responses.set('/api/projects/p',decided);
  app.run(`$('access-criterion-t03').value='2';$('access-source-t03').value=${JSON.stringify(refused)};`); // what the selects hold
  assert.equal(await app.run(`acceptAccessGap({dataset:{runId:'run_x',taskId:'t03'}})`),true);
  const approval=app.requests.find(r=>r.path==='/api/projects/p/approve');
  assert.deepEqual(JSON.parse(approval.options.body),{kind:'access_gap',run_id:'run_x',task_id:'t03',criterion:2,source:refused});
  assert.deepEqual(JSON.parse(app.requests.find(r=>r.path==='/api/projects/p/start').options.body),{action:'resume',run_id:'run_x'});
  app.run(`project.job.status='blocked';lastJobView='';render();`);
  const shown=jobView(app);
  assert.ok(shown.includes(`↻ Zugangslücke akzeptiert: Kriterium 2 · ${refused}. „Fortsetzen“ prüft die Antwort ohne den gesperrten Teil.`));
  assert.ok(!shown.includes('data-task-id="t03"'),'an accepted access gap offers no further buttons');
});

test('a disputed objection shows both positions and one click decides and resumes',async()=>{
  const app=studio();
  await new Promise(resolve=>setTimeout(resolve,0)); // the page's own start-up read settles before the project is set
  const dispute={objection_id:'obj_1',task_id:'t14',question:'Turchin <Modell>',decision:null,
    objection:{reason:'Die Neuformulierung steht in keiner Stelle.',correction:'Neuformulierung streichen.',closure_condition:'x'},
    review:{reason:'Die Stelle trägt die Neuformulierung jetzt.',references:['src#s1']}};
  const ledger={closed:18,total:18,accepted:0,phase:'audit',audit_round:1,questions:[]};
  app.run(`project={id:'p',config:boot.defaults,job:{id:'j1',status:'blocked',action:'resume',stop:{code:'review_disagreement'},started_at:new Date().toISOString(),run:{run_id:'run_x',kind:'research',stages:{}},progress:{phase:'research',research_questions:${JSON.stringify(ledger)},review_disagreement:${JSON.stringify(dispute)},search_round_limit:40}}};overviewPage=false;step=PAGE.research;render();`);
  const page=jobView(app);
  assert.ok(page.includes('Streitfall in der Gesamtprüfung'));
  assert.ok(page.includes('Turchin &lt;Modell&gt;'));
  assert.ok(page.includes('Die Neuformulierung steht in keiner Stelle.')&&page.includes('Die Stelle trägt die Neuformulierung jetzt.'));
  assert.ok(page.includes('data-action="decide-dispute" data-decision="reviewer" data-objection-id="obj_1" data-run-id="run_x"'));
  assert.ok(page.includes('data-action="decide-dispute" data-decision="objection"'));
  assert.ok(!page.includes('Neu recherchieren'),'a dispute never ends the run');
  app.run(`$('dispute-note-obj_1').value='Stelle 4147 trägt.';`);
  const decided=app.run('structuredClone(project)');
  decided.job.progress.review_disagreement.decision={decision:'reviewer',note:'Stelle 4147 trägt.'};
  app.responses.set('/api/projects/p',decided);
  assert.equal(await app.run(`decideDispute({dataset:{runId:'run_x',objectionId:'obj_1',decision:'reviewer'}})`),true);
  assert.deepEqual(JSON.parse(app.requests.find(r=>r.path==='/api/projects/p/approve').options.body),
    {kind:'dispute',run_id:'run_x',objection_id:'obj_1',decision:'reviewer',note:'Stelle 4147 trägt.'});
  assert.deepEqual(JSON.parse(app.requests.find(r=>r.path==='/api/projects/p/start').options.body),{action:'resume',run_id:'run_x'});
  // A disagreement without a stored dispute, a new unanchored objection, still ends the run as before.
  const rule=app.run(`stopInfo({status:'blocked',action:'resume',stop:{code:'review_disagreement'},run:{run_id:'run_x',kind:'research',stages:{}},progress:{phase:'research'}})`);
  assert.equal(rule.kind,'dead');
});

test('a run that keeps spent answers resumes past questions whose reworks are spent, without a decision',()=>{
  // Transformer, 2026-10-02: the run stopped on two such questions and one waiting on them, and no card offered
  // "Fortsetzen", although the resume keeps their verified answers (question_synthesis.keep_spent_answers).
  const app=studio();
  const row=(id,extra)=>({id,question:id,status:'verified',activity:'x',steps:3,read_sections:2,acceptance:['k'],reopened:2,answer:'A',findings:[],sources:[],limits:[],depends_on:[],...extra});
  const ledger={closed:55,total:60,accepted:2,phase:'blocked',audit_round:6,keeps_spent_answers:true,residual_finish:null,questions:[
    row('mqa',{status:'blocked',outcome:'audit_block'}),row('aufbau',{status:'blocked',outcome:'audit_block'}),
    row('messungen',{status:'blocked',outcome:'prerequisite_block',depends_on:['aufbau']}),
    row('synthese',{status:'blocked',outcome:'accepted_gap',accepted_gap:true})]};
  const job=l=>({status:'blocked',action:'resume',stop:{code:'research_questions_blocked'},run:{run_id:'run_x',kind:'research',stages:{}},progress:{phase:'research',research_questions:l}});
  assert.equal(app.run(`blockedSettled(${JSON.stringify(ledger)}).map(q=>q.id).join(',')`),'mqa,aufbau,messungen');
  assert.equal(app.run(`canResume(${JSON.stringify(job(ledger))})`),true);
  // A run of an earlier generation still waits for the decision.
  assert.equal(app.run(`canResume(${JSON.stringify(job({...ledger,keeps_spent_answers:false}))})`),false);
  // Any other advised block stays a decision of its own.
  const other={...ledger,questions:[...ledger.questions,row('t19',{status:'blocked',outcome:'evidence_block',
    advice:{key:'0.0',recommendation:'retry',diagnosis:'d',hint:'h'}})]};
  assert.equal(app.run(`canResume(${JSON.stringify(job(other))})`),false);
});

test('with the finish requested, spent reworks and questions waiting on passed prerequisites make the run resumable',()=>{
  const app=studio();
  const row=(id,extra)=>({id,question:id,status:'verified',activity:'x',steps:3,read_sections:2,acceptance:['k'],reopened:2,answer:'A',findings:[],sources:[],limits:[],depends_on:[],...extra});
  const ledger={closed:12,total:16,accepted:0,phase:'blocked',audit_round:3,residual_finish:{note:'',approved_at:'x'},questions:[
    row('t14'),row('t01',{status:'blocked',outcome:'audit_block'}),
    row('t15',{status:'blocked',outcome:'prerequisite_block',depends_on:['t14']}),
    row('t16',{status:'blocked',outcome:'prerequisite_block',depends_on:['t14','t15']})]};
  const job=l=>({status:'blocked',action:'resume',stop:{code:'research_questions_blocked'},run:{run_id:'run_x',kind:'research',stages:{}},progress:{phase:'research',research_questions:l}});
  assert.equal(app.run(`canResume(${JSON.stringify(job(ledger))})`),true);
  // Without the finish, the spent rework is an open decision.
  assert.equal(app.run(`canResume(${JSON.stringify(job({...ledger,residual_finish:null}))})`),false);
  // A prerequisite accepted as a gap never lets its dependent move on by itself.
  const gap={...ledger,questions:ledger.questions.map(q=>q.id==='t14'?{...q,status:'blocked',accepted_gap:true}:q)};
  assert.equal(app.run(`blockedSettled(${JSON.stringify(gap)}).map(q=>q.id).join(',')`),'t01');
  // Except a synthesis: it goes on without the gap and names it (Ontologies, 2026-09-30).
  const synthesis={...gap,questions:gap.questions.map(q=>q.id==='t15'?{...q,kind:'synthesis'}:q)};
  assert.equal(app.run(`blockedSettled(${JSON.stringify(synthesis)}).map(q=>q.id).join(',')`),'t01,t15');
});

test('a synthesis behind prerequisites accepted as gaps says that a resume takes it up without them',()=>{
  const app=studio();
  const q=(id,question,extra)=>({id,question,activity:'x',steps:1,read_sections:1,acceptance:['k'],reopened:0,...extra});
  const ledger={closed:0,total:3,accepted:1,phase:'blocked',questions:[
    q('t1','Merton?',{status:'blocked',outcome:'accepted_gap',accepted_gap:{reason:'x'}}),
    q('t2','Vergleich?',{status:'blocked',outcome:'prerequisite_block',kind:'synthesis',depends_on:['t1']}),
    q('t3','Test?',{status:'blocked',outcome:'prerequisite_block',kind:'empirical',depends_on:['t1'],reason:'Eine vorausgesetzte Teilfrage wurde als Lücke akzeptiert.'})]};
  const html=app.run(`renderResearchQuestions(${JSON.stringify(ledger)},new Set(),false,'run_x',0,12)`);
  assert.ok(html.includes('Die offenen Voraussetzungen sind als Lücke akzeptiert; „Fortsetzen“ fasst ohne sie zusammen'));
  assert.ok(html.includes('„Fortsetzen“ nimmt diese Frage wieder auf.'));
  // Any other question stays blocked by the accepted gap and asks for a decision of its own.
  assert.ok(html.includes('Eine vorausgesetzte Teilfrage wurde als Lücke akzeptiert.'));
});

test('a research step that spent its correction attempts offers fresh attempts, a script step does not',()=>{
  const app=studio();
  const job=kind=>({id:'j',status:'blocked',action:'resume',stop:{code:'invalid_question_routing'},run:{run_id:'run_x',kind,stages:{}},progress:{phase:kind}});
  const research=app.run(`renderStopCard(${JSON.stringify(job('research'))},stopInfo(${JSON.stringify(job('research'))}))`);
  assert.ok(research.includes('data-action="fresh-attempts" data-run-id="run_x" data-then-resume="1"'));
  assert.ok(research.includes('Mit neuen Anläufen fortsetzen'));
  const script=app.run(`renderStopCard(${JSON.stringify(job('script'))},stopInfo(${JSON.stringify(job('script'))}))`);
  assert.ok(!script.includes('fresh-attempts'),'a script stage starts its attempts anew on a resume');
});

test('every dispute of a round stands in one card, and the run resumes once, after the last decision',async()=>{
  const app=studio();
  await new Promise(resolve=>setTimeout(resolve,0)); // the page's own start-up read settles before the project is set
  const dispute=(id,question)=>({objection_id:id,task_id:'t',question,decision:null,
    objection:{reason:`Einwand ${id}`,correction:'x',closure_condition:'y'},review:{reason:`Prüfer ${id}`,references:[]}});
  const rows=[dispute('obj_a','Frage A'),dispute('obj_b','Frage B'),dispute('obj_c','Frage C')];
  const ledger={closed:18,total:18,accepted:0,phase:'audit',audit_round:1,questions:[]};
  app.run(`project={id:'p',config:boot.defaults,job:{id:'j1',status:'blocked',action:'resume',stop:{code:'review_disagreement'},started_at:new Date().toISOString(),run:{run_id:'run_x',kind:'research',stages:{}},progress:{phase:'research',research_questions:${JSON.stringify(ledger)},review_disagreement:${JSON.stringify(rows[0])},review_disagreements:${JSON.stringify(rows)},search_round_limit:40}}};overviewPage=false;step=PAGE.research;render();`);
  const page=jobView(app);
  assert.ok(page.includes('3 Streitfälle in der Gesamtprüfung'));
  for(const id of ['obj_a','obj_b','obj_c'])assert.ok(page.includes(`data-decision="reviewer" data-objection-id="${id}"`),id);
  assert.ok(!page.includes('data-action="resume"'),'no resume while a dispute is open');
  // The first two decisions only record; the run starts once, after the third.
  const answer=decisions=>{const next=app.run('structuredClone(project)');next.job.progress.review_disagreements.forEach((d,i)=>{d.decision=decisions[i]?{decision:decisions[i]}:null;});return next;};
  app.responses.set('/api/projects/p',answer(['reviewer']));
  assert.equal(await app.run(`decideDispute({dataset:{runId:'run_x',objectionId:'obj_a',decision:'reviewer'}})`),false);
  app.responses.set('/api/projects/p',answer(['reviewer','objection']));
  assert.equal(await app.run(`decideDispute({dataset:{runId:'run_x',objectionId:'obj_b',decision:'objection'}})`),false);
  assert.ok(jobView(app).includes('✓ Entschieden: Einwand aufrechterhalten'));
  assert.ok(!app.requests.some(r=>r.path==='/api/projects/p/start'));
  app.responses.set('/api/projects/p',answer(['reviewer','objection','reviewer']));
  assert.equal(await app.run(`decideDispute({dataset:{runId:'run_x',objectionId:'obj_c',decision:'reviewer'}})`),true);
  assert.equal(app.requests.filter(r=>r.path==='/api/projects/p/start').length,1);
});

test('after the first audit the loop can be ended with residual objections, and the request shows in place',async()=>{
  const app=studio();
  await new Promise(resolve=>setTimeout(resolve,0)); // the page's own start-up read settles before the project is set
  const row=(id,extra)=>({id,question:id,status:'verified',activity:'x',steps:3,read_sections:2,acceptance:['k'],reopened:1,answer:'A',findings:[],sources:[],limits:[],...extra});
  const ledger={closed:17,total:18,accepted:0,phase:'blocked',audit_round:2,questions:[row('a'),
    row('b',{status:'blocked',outcome:'audit_block',reason:'Wiederholte Gesamtprüfung widerspricht dem Abschluss: x'})]};
  app.run(`project={id:'p',config:boot.defaults,job:{id:'j1',status:'blocked',action:'resume',stop:{code:'research_questions_blocked'},started_at:new Date().toISOString(),run:{run_id:'run_x',kind:'research',stages:{}},progress:{phase:'research',research_questions:${JSON.stringify(ledger)},search_round_limit:48}}};overviewPage=false;step=PAGE.research;render();`);
  let page=jobView(app);
  // D-155: the finish is the highlighted choice of the card and carries the proposal's shorter label.
  assert.ok(page.includes('<button class="small" data-action="finish-residual" data-run-id="run_x">Nach der nächsten Prüfung abschließen'));
  assert.ok(page.includes('Einwände nach zwei Nachbesserungen offen'));
  // Requested: the note replaces the button, and the question blocked only by spent reworks is decided.
  app.run(`project.job.progress.research_questions.residual_finish={note:'Reicht <so>',approved_at:'x'};lastJobView='';render();`);
  page=jobView(app);
  assert.ok(!page.includes('data-action="finish-residual"'));
  assert.ok(page.includes('Abschluss mit dokumentierten Resteinwänden angefordert (Reicht &lt;so&gt;)'));
  assert.ok(page.includes('Jede blockierte Teilfrage ist entschieden'));
  // Before the first audit there is nothing to finish with.
  app.run(`project.job.progress.research_questions.audit_round=0;project.job.progress.research_questions.residual_finish=null;lastJobView='';render();`);
  assert.ok(!jobView(app).includes('finish-residual'));
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
  // Without room for the advice and one new attempt the run would stop at once: no resume promise, but the raise that makes room.
  app.run("project.job.progress.research_questions.budget_projection={used:278,limit:289,remaining:11,minimum_remaining_calls:3,expected_calls_per_task:10};lastJobView='';render();");
  const page=jobView(app);
  assert.ok(!page.includes('>Fortsetzen<'));
  assert.ok(!page.includes('„Fortsetzen“ lässt sie zuerst beraten'));
  assert.ok(page.includes('Für eine Beratung reicht das Aufruflimit nicht (11 Aufrufe frei, der Abschluss braucht mindestens 3)'));
  // 278 used + (3 closing + 1 question × (1 advice + 10 per attempt)) × 1.1 = 278 + 16
  assert.ok(page.includes('data-action="approve-calls" data-run-id="run_x" data-model-calls="294" data-then-resume="1">Aufruflimit auf 294 erhöhen und beraten lassen'));
  // A question waiting on the blocked one needs its own attempt afterwards: + 10 per task, 278 + ceil(24 × 1.1) = 305.
  app.run(`project.job.progress.research_questions.questions.push({id:'c',question:'Vergleich?',status:'blocked',outcome:'prerequisite_block',depends_on:['b'],web_attempts:0,reason:'',activity:'y',steps:0,read_sections:0,acceptance:['v'],reopened:0});lastJobView='';render();`);
  assert.ok(jobView(app).includes('data-model-calls="305"'));
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

test('the settings page sets text model, audio, modes, pre-approvals, limits and Claude for every project',async()=>{
  // The user's choice of 2026-10-03: one settings page instead of per-project choices; a model button in the chat
  // started a call to the partner, and only an applied proposal saved the model for that one project.
  const app=studio();
  await new Promise(resolve=>setTimeout(resolve,0)); // the page's own start-up read replaces boot first
  const presets=[{id:'auto_subscriptions',label:'Automatisch · Claude, sonst Codex',provider:'auto',model:null,reasoning_effort:null},
    {id:'openrouter_opus',label:'Opus 5.5 · medium · OpenRouter',provider:'openrouter',model:'anthropic/claude-opus-5.5',reasoning_effort:'medium'}];
  const view={settings:{text:{provider:'auto',model:null,reasoning_effort:null,max_output_tokens:32768},
      audio:{provider:'openrouter_gemini_tts',voices:{host_a:'Sadaltager',host_b:'Aoede'},pauses:{same_speaker_ms:250,speaker_change_ms:450,chapter_break_ms:900}},
      execution:{text:'sequential',audio:'sequential'},allowances:{fresh_attempts:0,extra_calls:0},
      research_limits:{model_calls:750,sources:150,search_rounds:48},text_timeout_seconds:1800},
    hash:'h1',global:false,source_project:'transformer',claude_extra_usage:false,key_available:false,
    allowance_choices:{fresh_attempts:[0,1,2,3],extra_calls:[0,100,250,500,1000]}};
  app.run(`boot.text_catalog={presets:${JSON.stringify(presets)}};boot.audio_catalog={qwen3_local:{label:'Qwen',voices:['Aiden','Vivian'],defaults:{host_a:'Aiden',host_b:'Vivian'}},
    openrouter_gemini_tts:{label:'Gemini',voices:['Aoede','Sadaltager','Puck'],defaults:{host_a:'Sadaltager',host_b:'Aoede'},models:{'gemini-a':'Gemini A','gemini-b':'Gemini B'},default_model:'gemini-a'}};`);
  app.responses.set('/api/settings',view);
  await app.run('showSettings()');
  const html=app.elements.get('content').innerHTML;
  assert.ok(html.includes('<h1>Einstellungen</h1>')&&html.includes('Noch je Projekt')&&html.includes('von „transformer“'));
  for(const p of presets)assert.ok(html.includes(`name="settings-text" value="${p.id}"`),p.id);
  assert.ok(html.includes('value="auto_subscriptions" checked'),'the saved choice is the chosen preset');
  for(const id of ['settings-audio-model','settings-voice-a','settings-pause-chapter','settings-exec-text','settings-fresh','settings-calls','settings-timeout','settings-claude-extra','api-key'])
    assert.ok(html.includes(`id="${id}"`),id);
  assert.ok(html.includes('<option value="Sadaltager" selected>'));
  assert.ok(html.includes('id="settings-timeout" type="number" min="5" max="240" step="1" value="30"'));
  app.run(`document.querySelector=selector=>selector.includes('settings-text')?{value:'openrouter_opus'}:null;
    const set=(id,value)=>{$(id).value=value;};set('settings-max-tokens','65536');set('settings-audio-provider','openrouter_gemini_tts');set('settings-audio-model','gemini-b');
    set('settings-voice-a','Puck');set('settings-voice-b','Aoede');set('settings-pause-same','300');set('settings-pause-change','500');set('settings-pause-chapter','1000');
    $('settings-expression').checked=true;set('settings-exec-text','parallel');set('settings-exec-audio','parallel');set('settings-fresh','2');set('settings-extra','250');
    set('settings-calls','1200');set('settings-sources','150');set('settings-rounds','48');set('settings-timeout','90');$('settings-claude-extra').checked=true;`);
  await app.run('saveSettings()');
  const body=JSON.parse(app.requests.filter(r=>r.path==='/api/settings'&&r.options?.method==='POST').at(-1).options.body);
  assert.equal(body.hash,'h1');
  assert.equal(body.claude_extra_usage,true);
  assert.deepEqual(body.settings.text,{provider:'openrouter',model:'anthropic/claude-opus-5.5',reasoning_effort:'medium',max_output_tokens:65536});
  assert.deepEqual(body.settings.audio,{provider:'openrouter_gemini_tts',voices:{host_a:'Puck',host_b:'Aoede'},
    pauses:{same_speaker_ms:300,speaker_change_ms:500,chapter_break_ms:1000},model:'gemini-b',expression:true});
  assert.deepEqual(body.settings.execution,{text:'parallel',audio:'parallel'});
  assert.deepEqual(body.settings.allowances,{fresh_attempts:2,extra_calls:250});
  assert.deepEqual(body.settings.research_limits,{model_calls:1200,sources:150,search_rounds:48});
  assert.equal(body.settings.text_timeout_seconds,5400);
});

test('the Google route offers styles, alternating roles, its key and a conversation sample of the selection',async()=>{
  // The user's wish of 2026-10-06: switch to Google, keep OpenRouter, and listen to a style before choosing it.
  const app=studio();
  await new Promise(resolve=>setTimeout(resolve,0));
  const styles={neugierig:{label:'Neugierig',host_a:'curious a',host_b:'curious b'},ohne:{label:'Ohne',host_a:'',host_b:''}};
  const view={settings:{text:{provider:'auto',model:null,reasoning_effort:null,max_output_tokens:32768},
      audio:{provider:'google_gemini_tts',voices:{host_a:'Erinome',host_b:'Sadachbia'},alternate_roles:true,
             pauses:{same_speaker_ms:250,speaker_change_ms:450,chapter_break_ms:900}},
      execution:{text:'sequential',audio:'sequential'},allowances:{fresh_attempts:0,extra_calls:0},
      research_limits:{model_calls:750,sources:150,search_rounds:48},text_timeout_seconds:1800},
    hash:'h1',global:true,claude_extra_usage:false,key_available:true,google_key_available:true,
    allowance_choices:{fresh_attempts:[0,1,2,3],extra_calls:[0,100,250,500,1000]}};
  app.run(`boot.text_catalog={presets:[]};boot.google_key_available=true;boot.audio_catalog={
    google_gemini_tts:{label:'Gemini TTS · Google',voices:['Erinome','Sadachbia','Puck'],defaults:{host_a:'Erinome',host_b:'Sadachbia'},
      models:{'gemini-a':'Gemini A'},default_model:'gemini-a',alternate_roles:true,style_presets:${JSON.stringify(styles)},
      default_styles:{host_a:'curious a',host_b:'curious b'},max_style_characters:80},
    openrouter_gemini_tts:{label:'Gemini TTS · OpenRouter',voices:['Erinome','Sadachbia'],defaults:{host_a:'Sadaltager',host_b:'Aoede'}}};`);
  app.responses.set('/api/settings',view);
  await app.run('showSettings()');
  const html=app.elements.get('content').innerHTML;
  for(const id of ['settings-style-preset','settings-style-a','settings-style-b','settings-alternate','google-key'])
    assert.ok(html.includes(`id="${id}"`),id);
  assert.ok(html.includes('value="curious a"') && html.includes('<option value="neugierig" selected>'));
  assert.ok(html.includes('data-action="pair-sample" data-swap="1"'),'alternating roles can be heard swapped');
  // Both keys' state at the top and on their panels (the user's wish of 2026-10-06).
  assert.ok(html.includes('<p class="key-states">Keys: ')&&(html.match(/✓ hinterlegt/g)||[]).length===4);
  // The Anthropic key (Claude on the user's API key) and the Perplexity key (web search) have chips too (2026-10-07).
  // CORE's key joined them with the credential store (D-167).
  app.run(`settingsData.google_key_available=false;settingsData.anthropic_key_available=true;settingsData.perplexity_key_available=true;settingsData.core_key_available=true;render();`);
  const missing=app.elements.get('content').innerHTML;
  assert.equal((missing.match(/✓ hinterlegt/g)||[]).length,8);
  assert.equal((missing.match(/<span class="chip decision">fehlt<\/span>/g)||[]).length,2);
  app.run(`$('settings-audio-provider').value='google_gemini_tts';$('settings-voice-a').value='Erinome';$('settings-voice-b').value='Sadachbia';
    $('settings-style-a').value='calm';$('settings-style-b').value='';$('settings-alternate').checked=true;$('settings-pair-language').value='en-US';`);
  assert.deepEqual(JSON.parse(app.run('JSON.stringify(settingsFromForm().audio.styles)')),{host_a:'calm',host_b:''});
  // A sample not yet made asks once, then the Google request makes it; the player gets its URL.
  app.responses.set('/api/pair-sample',{url:'/samples/google/en-US/abc',ready:true});
  app.run('window.confirm=()=>true');
  await app.run(`pairSample({dataset:{swap:'1'}})`);
  const asked=JSON.parse(app.requests.filter(r=>r.path==='/api/pair-sample').at(-1).options.body);
  assert.deepEqual(asked,{voices:{host_a:'Sadachbia',host_b:'Erinome'},styles:{host_a:'calm',host_b:''},language:'en-US'});
  assert.equal(app.elements.get('sample-player').src,'/samples/google/en-US/abc');
});

test('each language has its own pace: a slower montage, for Google a calm delivery too, heard in the sample',async()=>{
  // D-147, the user's choice of 2026-10-07: English at 93 % and unhurried, German as it is.
  const app=studio();
  await new Promise(resolve=>setTimeout(resolve,0));
  const view={settings:{text:{provider:'auto',model:null,reasoning_effort:null,max_output_tokens:32768},
      audio:{provider:'google_gemini_tts',voices:{host_a:'Erinome',host_b:'Sadachbia'},
             pauses:{same_speaker_ms:250,speaker_change_ms:450,chapter_break_ms:900},pace:{'en-US':{tempo:0.93,unhurried:true}}},
      execution:{text:'sequential',audio:'sequential'},allowances:{fresh_attempts:0,extra_calls:0},
      research_limits:{model_calls:750,sources:150,search_rounds:48},text_timeout_seconds:1800},
    hash:'h1',global:true,claude_extra_usage:false,key_available:true,google_key_available:true,
    allowance_choices:{fresh_attempts:[0,1,2,3],extra_calls:[0,100,250,500,1000]}};
  app.run(`boot.text_catalog={presets:[]};boot.google_key_available=true;boot.audio_catalog={
    google_gemini_tts:{label:'Gemini TTS · Google',voices:['Erinome','Sadachbia'],defaults:{host_a:'Erinome',host_b:'Sadachbia'},
      models:{'gemini-a':'Gemini A'},default_model:'gemini-a',alternate_roles:true,style_presets:{},
      default_styles:{host_a:'a',host_b:'b'},max_style_characters:80,unhurried:'unhurried'}};`);
  app.responses.set('/api/settings',view);
  await app.run('showSettings()');
  const html=app.elements.get('content').innerHTML;
  assert.ok(html.includes('id="settings-tempo-de-DE"')&&html.includes('<option value="1" selected>100 %</option>'));
  assert.ok(html.includes('<option value="0.93" selected>93 %</option>'),'the saved English tempo is chosen');
  assert.ok(html.includes('id="settings-unhurried-en-US" type="checkbox" checked'));
  assert.ok(html.includes('id="settings-unhurried-de-DE" type="checkbox" >'));
  assert.ok(html.includes('„unhurried“'));
  const pace=values=>{app.run(`for(const [id,v] of Object.entries(${JSON.stringify(values)})){if(id.startsWith('settings-tempo'))$(id).value=v;else $(id).checked=v;}`);
    return app.run('JSON.stringify(settingsFromForm().audio.pace)');};
  const english={'settings-tempo-en-US':'0.93','settings-unhurried-en-US':true,'settings-tempo-de-DE':'1','settings-unhurried-de-DE':false};
  assert.deepEqual(JSON.parse(pace(english)),{'en-US':{tempo:0.93,unhurried:true}});
  assert.deepEqual(JSON.parse(pace({...english,'settings-tempo-de-DE':'0.95'})),
    {'de-DE':{tempo:0.95,unhurried:false},'en-US':{tempo:0.93,unhurried:true}});
  // Every language at full speed without a calm delivery saves as before the pace existed.
  assert.equal(pace({...english,'settings-tempo-en-US':'1','settings-unhurried-en-US':false}),undefined);
  // The conversation sample carries the pace of its language only.
  pace(english);
  app.responses.set('/api/pair-sample',{url:'/samples/google/en-US/abc',ready:true});
  const asked=async language=>{app.run(`$('settings-pair-language').value='${language}'`);await app.run('pairSample({dataset:{}})');
    return JSON.parse(app.requests.filter(r=>r.path==='/api/pair-sample').at(-1).options.body);};
  assert.deepEqual((await asked('en-US')).pace,{tempo:0.93,unhurried:true});
  assert.ok(!('pace' in await asked('de-DE')));
});

test('the approval card names the pace of the project language it records with',()=>{
  const app=studio(), p=workflowProject(app);
  p.episodes=[publishedEpisode({})];
  p.audio_settings={provider:'google_gemini_tts',voices:{host_a:'Erinome',host_b:'Sadachbia'},pace:{'en-US':{tempo:0.93,unhurried:true}}};
  const card=language=>{app.run(`project=${JSON.stringify({...p,config:{...p.config,language}})};boot.google_key_available=true;episodeIndex=0;`);
    return app.run('renderApprovalCard(project.episodes[0],project.audio_settings,true)');};
  assert.ok(card('en-US').includes('<dt>Tempo</dt><dd class="hint">93 % · ohne Eile · eingestellt für englische Folgen</dd>'));
  assert.ok(!card('de-DE').includes('<dt>Tempo</dt>'),'a German project records as it did');
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
  assert.ok(html.includes('liest dein Textmodell sie'));
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
  assert.ok(app.elements.get('job-bar').innerHTML.includes('seit 22 Min.'));
  assert.ok(!html.includes('Gesamte Laufzeit'),'the run time is not repeated in the engine room');
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

test('a teaching design that keeps its defects offers a new design with the editor\'s note, then resumes',async()=>{
  const app=studio();
  await new Promise(resolve=>setTimeout(resolve,0)); // the page's own start-up read settles before the project is set
  app.run(`project={id:'p',config:boot.defaults,research:'dossier',job:{id:'j1',status:'blocked',action:'resume',message:'Scene 4 reads <SPARQL> aloud.',stop:{code:'teaching_design_failed',stage:'teaching',run_kind:'script'},teaching_failure:{episode_id:'ep_001',title:'Die <Antwort>'},started_at:new Date().toISOString(),run:{run_id:'run_s',kind:'script',stages:{teaching:{status:'blocked',error:{code:'teaching_design_failed'}}}}}};overviewPage=false;step=PAGE.production;render();`);
  const card=app.elements.get('stop-card').innerHTML;
  assert.ok(card.includes('Hinweis für das neue Lehrkonzept von „Die &lt;Antwort&gt;“'));
  assert.ok(card.includes('data-action="redesign-teaching" data-run-id="run_s" data-teaching-episode="ep_001"'));
  assert.ok(card.includes('Neues Inhaltsverzeichnis entwerfen'));
  assert.ok(!card.includes('data-action="resume"'),'a plain resume would replay the same verdict');
  await assert.rejects(()=>app.run(`redesignTeaching({dataset:{runId:'run_s',teachingEpisode:'ep_001'}})`),/Hinweis/);
  assert.equal(app.requests.filter(r=>r.path==='/api/projects/p/approve').length,0,'no request without a note');
  app.run(`$('redesign-note').value='  Keine Abfragesprache vorlesen.  ';`);
  await app.run(`redesignTeaching({dataset:{runId:'run_s',teachingEpisode:'ep_001'}})`);
  assert.deepEqual(JSON.parse(app.requests.find(r=>r.path==='/api/projects/p/approve').options.body),
    {kind:'teaching_redesign',run_id:'run_s',episode_id:'ep_001',note:'Keine Abfragesprache vorlesen.'});
  assert.deepEqual(JSON.parse(app.requests.find(r=>r.path==='/api/projects/p/start').options.body),{action:'resume',run_id:'run_s'});
  // A job without the episode (an older server) shows no redesign control rather than a broken one.
  assert.equal(app.run(`stopButton('redesign_teaching',{run:{run_id:'run_s'}},{},'')`),'');
});

test('a supplementary research that spent its corrections offers fresh attempts in a script run',()=>{
  const app=studio();
  app.run(`project={id:'p',config:boot.defaults,research:'dossier',job:{id:'j1',status:'blocked',action:'resume',message:'m',stop:{code:'teaching_research_required',stage:'teaching',run_kind:'script'},started_at:new Date().toISOString(),run:{run_id:'run_s',kind:'script',stages:{teaching:{status:'blocked',error:{code:'teaching_research_required'}}}}}};overviewPage=false;step=PAGE.production;render();`);
  const card=app.elements.get('stop-card').innerHTML;
  assert.ok(card.includes('data-action="fresh-attempts" data-run-id="run_s" data-then-resume="1"'));
  assert.ok(card.includes('die beanstandeten Punkte bleiben der Prüfung bekannt'));
  // The script review offers them too; another script stop keeps its own controls.
  assert.ok(app.run(`stopButton('fresh_attempts',{run:{run_id:'run_s',kind:'script'}},{code:'script_review_failed'},'')`).includes('data-action="fresh-attempts"'));
  assert.ok(app.run(`stopInfo({status:'blocked',action:'resume',stop:{code:'script_review_failed'},run:{run_id:'run_s',kind:'script',stages:{}}})`).actions.includes('fresh_attempts'));
  assert.equal(app.run(`stopButton('fresh_attempts',{run:{run_id:'run_s',kind:'script'}},{code:'teaching_design_failed'},'')`),'');
});

test('an OpenRouter account limited to zero data retention names its privacy setting and resumes after it',()=>{
  const app=studio();
  const info=app.run(`stopInfo({status:'blocked',action:'audio',stop:{code:'openrouter_privacy'},run:{run_id:'run_a',kind:'episode_audio',stages:{}}})`);
  assert.equal(info.kind,'fix');
  assert.ok(info.text.includes('openrouter.ai/settings/privacy'));
});

test('a source in two versions resumes in a script run and stays a dead end in a research run',()=>{
  const app=studio();
  const stop=kind=>app.run(`stopInfo({status:'blocked',action:'resume',stop:{code:'invalid_source_snapshot'},run:{run_id:'run_x',kind:'${kind}',stages:{}}})`);
  const script=stop('script');
  assert.equal(script.kind,'retry');
  assert.ok(script.text.includes('ohne neue Aufrufe an derselben Stelle'));
  assert.equal(app.run(`canResume({status:'blocked',action:'resume',stop:{code:'invalid_source_snapshot'},run:{run_id:'run_x',kind:'script',stages:{}}})`),true);
  assert.equal(stop('research').kind,'dead');
});

test('the sidebar names where the Studio is reachable: here, here and in the WLAN, or from the home network',()=>{
  const app=studio();
  assert.ok(app.run(`studioPlace({lan:{enabled:false,urls:[]},client:'local'})`).includes('Lokal auf diesem Computer'));
  const both=app.run(`studioPlace({lan:{enabled:true,urls:['http://192.168.178.75:8765']},client:'local'})`);
  assert.ok(both.includes('im WLAN unter http://192.168.178.75:8765'));
  assert.ok(app.run(`studioPlace({lan:{enabled:true,urls:['http://192.168.178.75:8765']},client:'lan'})`).includes('Im Heimnetz verbunden'));
  assert.ok(app.run(`studioPlace({lan:{enabled:true,urls:['http://<b>']},client:'local'})`).includes('&lt;b&gt;'));
  assert.ok(app.run(`studioPlace(null)`).includes('Lokal auf diesem Computer'), 'an older server without the field');
});

test('foundation research runs without a retry button, and its stop exposes real unresolved questions and an honest resume',()=>{
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
  assert.ok(!html.includes('data-action="resume"'), 'a stop without a script run behind it offers nothing to resume');
  // The stop card of a script run: since 2026-09-27 it offers "Fortsetzen" (an update may have changed the
  // rule that stopped it) and says when that helps, beside the new outline.
  app.run(`project.research='dossier';project.job.run.run_id='run_script';project.job.run.kind='script';project.job.run.stages.teaching.status='blocked';renderJob();`);
  const card=app.elements.get('stop-card').innerHTML;
  assert.ok(card.includes('data-action="resume" data-run-id="run_script"'));
  assert.ok(card.includes('ohne neue Aufrufe an derselben Stelle'));
  assert.ok(card.includes('Neues Inhaltsverzeichnis entwerfen'));
  assert.ok(!card.includes('C:/private'));
});
// The page's catalogs as /locale.js serves them (studio_text.locale_script, D-152): English under the active language.
const locales = Object.fromEntries(['de','en'].map(language=>[language,JSON.parse(fs.readFileSync(`src/podcast_automate/locales/${language}.json`,'utf8'))]));
const localeScript = (language, setting='auto') => `const STUDIO_LOCALE=${JSON.stringify({setting,language,catalog:{...locales.en,...locales[language]}})};`;
function studio({language='de', setting='auto'}={}) {
  const elements = new Map(), registered = new Map(), requests = [], responses = new Map(), events = new Map();
  const element = id => {
    if (!elements.has(id)) elements.set(id, {value:'',innerHTML:'',hidden:false,textContent:'',paused:true,focus(){},scrollIntoView(){},addEventListener(){},async play(){this.paused=false;},pause(){this.paused=true;}});
    return elements.get(id);
  };
  const defaults = {topic:'New project',central_question:'Why?',voice_profile:{host_a:'Aiden',host_b:'Vivian'},language:'de-DE',prior_knowledge:'',depth_request:'Deep',focus_questions:[],excluded_topics:[],seed_urls:[]};
  const context = vm.createContext({console,structuredClone,AbortController,encodeURIComponent,URL,URLSearchParams,btoa,setInterval(){},window:{addEventListener(name,handler){events.set(name,handler);},scrollTo(){}},
    document:{getElementById:element,querySelectorAll(){return [];},addEventListener(){},modelContext:{registerTool(tool){registered.set(tool.name,tool);}}},
    fetch:async(path,options)=>{requests.push({path,options});const data=responses.get(path)??{token:'csrf',voices:['Aiden','Vivian'],projects:[],defaults};return{ok:true,json:async()=>structuredClone(data)};}});
  vm.runInContext(localeScript(language, setting), context);
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

test('the audio page offers the spoken-form table and the pronunciation report; pauses are a setting',()=>{
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
  assert.ok(!html.includes('id="pause-same"'),'the pauses moved to the settings page (2026-10-03)');
  assert.ok(html.includes('Audio-Einstellung für alle Projekte'));
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
  // No checkbox on the reading page: the server applies the saved approval by the pipeline's rule. Since 2026-10-02 the
  // re-render also carries the hash of the tags on the page (empty when none were placed), since it speaks them.
  assert.deepEqual(JSON.parse(request.options.body),{action:'audio',episode:'ep_001',approve_audio:false,rerender:true,
    expression_hash:'',script_hash:'final',readable_hash:'final',config_hash:'cfg',audio_hash:'aud'});
  // The approval-page button still sends the checkbox state and no rerender flag, and the hash of the tags read
  // with the script (empty when none were placed).
  app.run('episodeIndex=0;$("audio-approval").checked=true;');
  assert.deepEqual(JSON.parse(JSON.stringify(app.run('audioRequest()'))),{episode:'ep_001',approve_audio:true,expression_hash:'',script_hash:'final',readable_hash:'final',config_hash:'cfg',audio_hash:'aud'});
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
  assert.ok(html.includes('Sprechformen und Hostnamen'));
  app.responses.set('/api/projects/test',p);
  app.run(`$('host-name-a').value='Lena';$('host-name-b').value='Tom';$('spoken-forms').value='H800 = H achthundert';`);
  await app.run('saveSpeechSettings()');
  let request=app.requests.filter(r=>r.path==='/api/projects/test/save').at(-1);
  let body=JSON.parse(request.options.body);
  assert.equal(request.options.headers['X-Studio-Token'],'csrf');
  assert.deepEqual(body.config.host_names,{host_a:'Lena',host_b:'Tom'});
  assert.equal(body.config.topic,'New project');
  assert.equal(body.config_hash,'cfg');
  assert.deepEqual(body.spoken_forms.entries,[{written:'H800',spoken:'H achthundert'}]);
  assert.equal(JSON.stringify(body.audio_settings),app.run('JSON.stringify(currentAudio())'),'the settings page\'s audio choice goes back unchanged');
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
  assert.ok(app.elements.get('production-progress').innerHTML.includes('Dialogschliff'));
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
test('setup starts with a conversation, without configuration forms; the key is entered on the settings page',()=>{
  const app=studio(),html=app.run('renderBrief()');
  assert.ok(html.includes('id="chat-message"'));
  assert.ok(html.includes('Worum soll dein Podcast gehen'));
  assert.ok(!html.includes('id="api-key"'),'the OpenRouter key moved to the settings page (2026-10-03)');
  assert.ok(!html.includes('data-text-preset'),'the chat has no model buttons any more');
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
  assert.ok(player.includes('früherer Stand'));
  assert.ok(player.includes('data-play-episode="ep_001"'));
  assert.ok(player.includes('/media/test/exports/ep_001/run_test/audio.mp3'),'the download link without the ZIP capability');
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
  assert.equal(box.hidden,true,'a finished sample leaves the engine room closed');
  assert.equal(box.innerHTML,'');
  let brief=app.run('renderBrief()');
  assert.ok(brief.includes('Neue Hörprobe')&&brief.includes('data-play-voice="Sadaltager" data-language="de-DE"'));
  assert.ok(brief.includes('MP3 herunterladen'));
  app.run("project.job.sample.voice='Aoede';project.job.sample.audio='studio/samples/de-DE/Aoede/audio.mp3';");
  brief=app.run('renderBrief()');
  assert.ok(brief.includes('data-play-voice="Aoede"'));
  app.run('project=null;renderJob();');
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
  assert.match(app.run('audioBlockReason("ep_002")'),/gleichzeitig wie erlaubt/);
  app.run('project.audio_settings.provider="qwen3_local";project.audio_capacity.available=2');
  assert.match(app.run('audioBlockReason("ep_002")'),/Auftrag läuft/);
});

test('independent audio cards target their own stop or resume action',()=>{
  const app=studio();
  app.run(`boot.capabilities={parallel_audio:true};project={id:'test',config:boot.defaults,episodes:[],
    audio_settings:{provider:'openrouter_gemini_tts',voices:{host_a:'Sadaltager',host_b:'Aoede'}},audio_capacity:{available:2},
    audio_jobs:[{id:'one',episode:'ep_001',status:'running',progress:{completed_segments:2,total_segments:8}},
      {id:'two',episode:'ep_002',status:'blocked',run:{run_id:'run_two'},message:'<blocked>'}],job:{id:'one',status:'running'}};step=PAGE.audio;renderJob();`);
  const html=app.elements.get('audio-jobs').innerHTML, drawer=app.elements.get('job-status').innerHTML;
  assert.ok(html.includes('data-job-id="one"'));
  assert.ok(html.includes('data-run-id="run_two" data-episode="ep_002"'));
  assert.ok(html.includes('&lt;blocked&gt;'));
  assert.ok(drawer.includes('2 von 8'),'the engine room shows the running recording');
  assert.ok(!drawer.includes('data-job-id')&&!drawer.includes('data-action="resume"'),'and no controls');
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
  assert.equal((recordings.match(/data-play-episode=/g)||[]).length,2);
  assert.ok(recordings.includes('2 Teile')&&recordings.includes('MP3 2'));
  assert.ok(!recordings.includes('<audio '),'one player for all episodes, outside the list');
  assert.equal(app.run('steps.length'),6);
});

test('audio-page polling adds the next finished episode while the one player keeps playing',()=>{
  const app=studio();
  app.run(`project={id:'test',config:boot.defaults,episodes:[
    {script:{episode_id:'ep_001',title:'First'},audio:['exports/one.mp3'],audio_current:true,audio_seconds:1800}]};step=PAGE.audio;
    $('podcasts-test').querySelectorAll=()=>[];
    $('episode-player').src='/media/test/exports/one.mp3';$('episode-player').paused=false;
    refreshRecordings();`);
  assert.ok(app.elements.get('podcasts-test').innerHTML.includes('data-play-episode="ep_001"'));
  assert.ok(app.elements.get('podcasts-test').innerHTML.includes('30 Min.'),'each row names its length');
  app.run(`project.episodes.push({script:{episode_id:'ep_002',title:'Second'},audio:['exports/two.mp3'],audio_current:true});refreshRecordings();`);
  assert.ok(app.elements.get('podcasts-test').innerHTML.includes('data-play-episode="ep_002"'));
  assert.equal(app.elements.get('episode-player').src,'/media/test/exports/one.mp3','the player is not touched');
  assert.equal(app.elements.get('episode-player').paused,false);
  assert.ok(app.run('renderAudio()').includes('id="podcasts-test"'));
});

test('credential-like chat text is rejected before project creation or model calls',async()=>{
  const app=studio();
  await assert.rejects(app.run(`sendSetupMessage('sk-or-abcdefghijklmnopqrstuvwxyz')`),/Keys gehören nicht/);
  assert.equal(app.requests.filter(r=>r.options?.method==='POST').length,0);
});

test('complete podcast download is a single ZIP link while each episode plays in the one player',()=>{
  const app=studio();
  app.run('boot.capabilities={podcast_downloads:true}');
  const p={id:'test',topic:'Topic',episode_count:2,episodes:[
    {episode_id:'ep_001',title:'First',audio:['exports/ep_001/one.mp3'],audio_current:true},
    {episode_id:'ep_002',title:'Second',audio:['exports/ep_002/two.mp3'],audio_current:true}]};
  const html=app.run(`renderRecordings(${JSON.stringify(p)})`);
  assert.ok(html.includes('Gesamten Podcast herunterladen'));
  assert.ok(html.includes('href="/download/test/podcast.zip" download'));
  assert.ok(html.includes('href="/download/test/file/exports/ep_001/one.mp3"'));
  assert.ok(html.includes('data-play-episode="ep_001"'));
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
  // D-155: one plain sentence and one raise of both limits replace the source note and its own button. Without rates
  // from the plan (a run planned before 2026-10-07) the raise keeps the fixed steps, +40 sources and +6 search rounds.
  assert.ok(fullCard.includes('4 Teilfragen konnten nicht suchen, weil das Quellenlimit (150 von 150) erreicht ist; mit +40 Quellen und +6 Suchrunden kommen alle weiter.'));
  assert.ok(fullCard.includes('data-action="approve-limits" data-run-id="run_x" data-sources="190" data-search-rounds="30"'));
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
  assert.ok(page.includes('Dialogschliff'));
  assert.ok(page.includes('Die Skripte sind bereit'));
  assert.ok(!page.includes('Folgt automatisch'));
  assert.ok(app.elements.get('steps').innerHTML.includes('Ausarbeitung<small>Abgeschlossen'));
  assert.ok(app.elements.get('job-bar').innerHTML.includes('Podcast verfügbar · 1 Folge'));
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
  assert.ok(!bar.includes('data-action="drawer-toggle"'));
  assert.ok(drawer.includes('data-action="drawer-toggle"'));
  assert.ok(!bar.includes('data-action="resume"'));
  assert.ok(page.includes('data-action="approve-plan"'));
  assert.ok(!drawer.includes('data-action="approve-plan"'));
  assert.ok(drawer.includes('class="drawer-body" hidden'));
  assert.ok(drawer.indexOf('Plan erstellt')>-1&&drawer.indexOf('Plan erstellt')<drawer.indexOf('Für diesen Auftrag gespeichert'),'the live output comes first');
  app.run('drawerOpen=true;lastJobView="";renderJob();');
  assert.ok(app.elements.get('job-status').innerHTML.includes('class="drawer-body">'));
  assert.equal(app.requests.filter(r=>r.options?.method==='POST').length,0);
});

test('the overview puts what waits for the user first, once, with its next action',()=>{
  const app=studio();
  app.run(`boot.capabilities={project_overview:true};overviewData={projects:[
    {id:'a',topic:'Blocked <one>',job:{status:'blocked',action:'research',message:'Beleg fehlt',run:{kind:'research'}},has_research:false,has_outline:false,script_count:0,episodes:[]},
    {id:'b',topic:'Running',job:{status:'running',action:'script',run:{kind:'script'}},has_research:true,has_outline:true,script_count:0,episodes:[]},
    {id:'c',topic:'Readable',job:{status:'completed',action:'script',run:{kind:'script',status:'completed'}},has_research:true,has_outline:true,script_count:3,episodes:[{episode_id:'ep_001',title:'One',audio:[]}]},
    {id:'d',topic:'Done',job:{status:'completed',action:'audio',run:{kind:'episode_audio',status:'completed'}},has_research:true,has_outline:true,script_count:1,episodes:[{episode_id:'ep_001',title:'One',audio:['one.mp3'],audio_current:true}]}],trash:[]};`);
  const html=app.run('renderOverview()');
  const at=id=>html.indexOf(`id="project-card-${id}"`);
  assert.ok(at('a')>-1&&at('a')<at('c')&&at('c')<at('b')&&at('b')<at('d'),'waiting projects first, then running ones, then the rest');
  assert.equal((html.match(/data-project-card="a"/g)||[]).length,1,'each project stands once');
  assert.ok(html.includes('class="pipeline waiting" id="project-card-a"')&&html.includes('class="pipeline running" id="project-card-b"'));
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
  // Reopened questions are reworked before the next round: the label says so instead of "round 2 · 12 reopened".
  assert.ok(html.includes('<strong>Nachbesserung nach Prüfrunde 1</strong> · 12 Teilfragen wieder geöffnet. Danach wird das Dossier neu zusammengesetzt und in Prüfrunde 2 geprüft.'));
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
  // A research step whose corrections ran out, or a check refused a change, keeps its checked answers and may resume.
  app.run(`project.job.status='blocked';project.job.run={run_id:'run_t',kind:'research',stages:{dossier:{status:'blocked',error:{code:'invalid_evidence',message:'Belegkorrektur'}}}};renderJob();`);
  html=jobView(app);
  assert.ok(html.includes('Korrekturversuche aufgebraucht'));
  assert.ok(html.includes('data-action="resume" data-run-id="run_t"'));
  assert.ok(html.includes('die geprüften Teilantworten bleiben erhalten'));
  app.run(`project.job.status='blocked';project.job.run={run_id:'run_t',kind:'research',stages:{dossier:{status:'blocked',error:{code:'prompt_too_large',message:'zu groß'}}}};renderJob();`);
  html=jobView(app);
  assert.ok(html.includes('passt nicht in das Kontextfenster'));
  // A prompt that did not fit was refused before the call and not charged, and a task's prompt changes between
  // attempts and with Studio updates: resuming costs nothing and is offered, next to the new run with another model.
  assert.ok(html.includes('data-action="resume" data-run-id="run_t"'),'a refused prompt may be tried again at no cost');
  assert.ok(html.includes('nicht angerechnet'));
  assert.ok(html.includes('data-action="research"'),'a new research run with another model stays the way out');
  app.run(`project.job.run={run_id:'run_t',kind:'research',stages:{dossier:{status:'blocked',error:{code:'research_budget_insufficient',message:'Limit'}}}};project.job.progress.model_calls=150;project.job.progress.model_call_limit=150;renderJob();`);
  html=jobView(app);
  assert.ok(html.includes('data-action="approve-calls" data-run-id="run_t" data-model-calls="200" data-then-resume="1"'));
  app.run(`project.job.progress.research_questions={closed:1,total:2,phase:'questions',audit_round:0,reopened:0,questions:[]};project.job.run={stages:{}};renderJob();`);
  assert.ok(!jobView(app).includes('Prüfrunde'));
});

// Stops as the user meets them: the page that owns the job says what happened, whether "Fortsetzen"
// can help and which control leads on (docs/STUDIO.md, "Stopping and resuming").
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
  // Once the Studio's code changed after the stop, a correction may fit the checkpoint: "Fortsetzen" is offered too
  // (Asimov, 2026-10-01: morris_evaluate stopped on a receipt a later fix sorts into its own folder).
  app.run(`project.job.finished_at='2026-10-01T15:36:10Z';project.server={code_updated_at:'2026-10-01T15:40:57Z'};render();`);
  const updated=app.elements.get('stop-card').innerHTML;
  assert.ok(updated.includes('Seit dem Stopp wurde das Studio aktualisiert'));
  assert.ok(updated.includes('data-action="resume"'));
  assert.ok(updated.includes('data-action="research" data-confirm='),'a new run stays the way out if it stops again');
  app.run(`project.server={code_updated_at:'2026-10-01T15:30:00Z'};render();`);
  assert.ok(!app.elements.get('stop-card').innerHTML.includes('data-action="resume"'),'older code than the stop changes nothing');
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

test('drafting and revising the outline are named as such, and the replaced draft leaves the page',()=>{
  // 2026-10-02: the replaced plan stayed on the page while it was redone, and after the revision stopped.
  const app=studio(), p=workflowProject(app);
  p.outline.approval=null;
  Object.assign(p.job,{action:'replan',status:'running',run:{run_id:'run_o',kind:'script',status:'running',stages:{planning:{status:'running',attempts:1}}},progress:{phase:'script',stage:'planning',activity:'Inhaltsverzeichnis wird korrigiert · Korrekturrunde 2 von 3',episodes:[]}});
  app.run(`project=${JSON.stringify(p)};step=PAGE.outline;render();`);
  assert.ok(app.elements.get('job-bar').innerHTML.includes('Inhaltsverzeichnis wird überarbeitet'));
  const html=app.elements.get('content').innerHTML;
  assert.ok(html.includes('Das Inhaltsverzeichnis wird überarbeitet.'));
  assert.ok(html.includes('Korrekturrunde 2 von 3'));
  assert.ok(!html.includes('Build the explanation.'),'the replaced draft is not shown');
  assert.ok(!html.includes('data-action="script"')&&!html.includes('data-action="replan"'));
  app.run(`project.job.action='plan';project.outline=null;lastJobView='';render();`);
  assert.ok(app.elements.get('job-bar').innerHTML.includes('Inhaltsverzeichnis entsteht'));
  assert.ok(!app.elements.get('job-bar').innerHTML.includes('Ausarbeitung läuft'));
});

test('an outline that stays contradictory is drafted anew instead of resumed or revised',()=>{
  // The planning stage never completed, so there is no outline to revise: scripting.outline_revision refuses a hint.
  const app=studio();
  app.run(`project={id:'p',config:boot.defaults,research:'Dossier',outline:null,job:{id:'j',action:'plan',status:'blocked',message:'Das Inhaltsverzeichnis enthält nach der automatischen Korrektur noch einen Widerspruch.',
    run:{run_id:'run_o',kind:'script',status:'blocked',stages:{planning:{status:'blocked',attempts:1,error:{code:'invalid_plan'}}}}}};step=PAGE.outline;render();`);
  const card=app.elements.get('stop-card').innerHTML;
  assert.ok(card.includes('Inhaltsverzeichnis bleibt widersprüchlich'));
  assert.ok(card.includes('data-action="plan"'));
  assert.ok(!card.includes('data-action="replan"'));
  assert.ok(!card.includes('data-action="resume"'));
});

test('a revision stopped at the output limit starts a new outline instead of revising the draft it replaced',()=>{
  // 2026-10-02 (Transformer project): the restart revised the stopped run, which stopped at once with invalid_plan
  // and offered the same button again.
  const app=studio();
  app.run(`project={id:'p',config:boot.defaults,research:'Dossier',outline:null,job:{id:'j',action:'replan',status:'blocked',message:'Zu lang.',
    run:{run_id:'run_o',kind:'script',status:'blocked',stages:{planning:{status:'blocked',attempts:2,error:{code:'claude_output_limit'}}}}}};step=PAGE.outline;render();`);
  const card=app.elements.get('stop-card').innerHTML;
  assert.ok(card.includes('Antwort zu lang für einen Aufruf'));
  assert.ok(card.includes('data-action="plan"'));
  assert.ok(!card.includes('data-action="replan"'));
  assert.equal(app.run(`restartAction({run:{kind:'script',status:'blocked',stages:{planning:{status:'completed',attempts:1}}}})`),'replan_feedback',
    'a completed outline still takes a revision hint');
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
  // D-155: the plan card's button says „Plan freigeben und starten“ (before: „Rechercheplan freigeben und starten“).
  assert.ok(html.includes('Plan freigeben und starten'));
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
  assert.match(drawer,/<small>[^<]*· Warum Y\?<\/small>/,'a live line names its sub-question');
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
  assert.ok(full.includes('gleichzeitig wie erlaubt'));
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

test('every text run offers "Weiter mit …", and a stop on a spent fixed subscription offers the other one',()=>{
  const app=studio();
  app.run(`boot.text_catalog={openrouter_models:{'openai/gpt-6-astra':'GPT-6 Astra','anthropic/claude-fable-5.1':'Claude Fable 5.1'}};`);
  const job={id:'j',status:'waiting_for_quota',action:'resume',message:'m',stop:{code:'claude_quota_exhausted'},
    run:{run_id:'run_s',kind:'script',stages:{}},text_switchable:true,text_switched:false,text_switch_choice:'claude',
    text_generation:{provider:'claude_code',model:'claude-opus-5-5',reasoning_effort:'medium'}};
  const card=app.run(`renderStopCard(${JSON.stringify(job)},stopInfo(${JSON.stringify(job)}))`);
  assert.ok(card.includes('data-action="text-switch" data-run-id="run_s" data-choice="astra_first" data-then-resume="1"'));
  assert.ok(card.includes('Mit Astra (xhigh) fortsetzen'));
  const panel=app.run(`renderRunTextChoice(${JSON.stringify(job)})`);
  for(const choice of ['claude_first','astra_first','claude','astra','openrouter'])assert.ok(panel.includes(`<option value="${choice}"`),choice);
  assert.ok(panel.includes('<option value="claude" selected>Nur Claude · aktuell</option>'));
  assert.ok(panel.includes('id="text-switch-model" aria-label="OpenRouter-Modell" hidden'),'the model list waits for OpenRouter');
  assert.ok(panel.includes('<option value="anthropic/claude-fable-5.1" >Claude Fable 5.1</option>'));
  // A research run is switchable too; a Codex stop on a run fixed to Astra offers Claude.
  const research={...job,stop:{code:'quota_exhausted'},run:{run_id:'run_r',kind:'research',stages:{}},text_switch_choice:'astra',
    text_generation:{provider:'codex_cli',model:'gpt-6-astra',reasoning_effort:'xhigh'}};
  assert.ok(app.run(`renderStopCard(${JSON.stringify(research)},stopInfo(${JSON.stringify(research)}))`).includes('data-choice="claude_first"'));
  assert.ok(app.run(`renderRunTextChoice(${JSON.stringify(research)})`).includes('<option value="astra" selected>'));
  // A pair moves on by itself, so its stop card offers no switch; the panel names the order it uses.
  const pair={...job,text_switched:true,text_switch_choice:'astra_first',text_generation:{provider:'auto',prefer:'codex_cli',
    candidates:{codex_cli:{model:'gpt-6-astra',reasoning_effort:'xhigh'},claude_code:{model:'claude-opus-5-5',reasoning_effort:'medium'}}}};
  assert.ok(!app.run(`renderStopCard(${JSON.stringify(pair)},stopInfo(${JSON.stringify(pair)}))`).includes('text-switch'));
  const text=app.run(`renderRunTextChoice(${JSON.stringify(pair)})`);
  assert.ok(text.includes('Automatische Abo-Wahl, zuerst Astra'));
  assert.ok(text.includes('Umgeschaltet gegenüber dem Start.'));
  // An OpenRouter run shows its model preselected.
  const paid={...job,text_switch_choice:'openrouter',text_generation:{provider:'openrouter',model:'anthropic/claude-fable-5.1'}};
  const paidPanel=app.run(`renderRunTextChoice(${JSON.stringify(paid)})`);
  assert.ok(paidPanel.includes('<option value="anthropic/claude-fable-5.1" selected>')&&!paidPanel.includes('OpenRouter-Modell" hidden'));
  assert.ok(paidPanel.includes('id="text-switch-claude-model" aria-label="Claude-Modell" hidden'),'the Claude list waits for a Claude choice');
});

test('"Nur Claude" names its model, preselected with the run\'s own, and a hold card\'s switch keeps the default (D-170)',()=>{
  const app=studio();
  app.run(`boot.text_catalog={openrouter_models:{},claude_models:{'claude-haiku-5-5':'Claude Haiku 5.5','claude-sonnet-5-5':'Claude Sonnet 5.5'}};`);
  const job={id:'j',status:'interrupted',action:'resume',run:{run_id:'run_o',kind:'research',stages:{}},text_switchable:true,
    text_switch_choice:'claude',text_generation:{provider:'claude_code',model:'claude-haiku-5-5',reasoning_effort:'xhigh'}};
  const panel=app.run(`renderRunTextChoice(${JSON.stringify(job)})`);
  assert.ok(panel.includes('<select id="text-switch-claude-model" aria-label="Claude-Modell" >'),'shown for "Nur Claude"');
  assert.ok(panel.includes('<option value="claude-haiku-5-5" selected>Claude Haiku 5.5</option>'));
  assert.ok(panel.includes('<option value="claude-sonnet-5-5" >Claude Sonnet 5.5</option>'));
  app.run(`document.getElementById('text-switch-claude-model').value='claude-sonnet-5-5';document.getElementById('text-switch-model').value='openai/gpt-6-astra';`);
  assert.equal(app.run(`switchModel('claude')`),'claude-sonnet-5-5');
  assert.equal(app.run(`switchModel('claude_api')`),'claude-sonnet-5-5');
  assert.equal(app.run(`switchModel('claude',true)`),'','a hold card\'s "Mit Claude fortsetzen" keeps the default');
  assert.equal(app.run(`switchModel('claude_first')`),'','the pair takes no model');
  assert.equal(app.run(`switchModel('openrouter')`),'openai/gpt-6-astra');
});

test('the setup summary switches the Jev gap probe and script stops name Jev, not Gemini',()=>{
  const app=studio();
  app.run(`project={id:'p',config:boot.defaults,execution:{text:'sequential',audio:'sequential'},jev_probe:false,chat:[]};`);
  const off=app.run('setupSummary()');
  assert.ok(off.includes('data-action="toggle-jev-probe" data-enabled="1">Jev dazunehmen'));
  assert.ok(off.includes('etwa 0,60 USD OpenRouter-Guthaben je neuem Skriptlauf'));
  app.run(`project.jev_probe=true;`);
  assert.ok(app.run('setupSummary()').includes('Wortsuche und Jev · OpenRouter'));
  assert.ok(app.run('setupSummary()').includes('data-enabled="0">Jev ausschalten'));
  const job=kind=>({status:'blocked',action:'resume',stop:{code:'openrouter_privacy'},run:{run_id:'r',kind,stages:{}}});
  assert.equal(app.run(`stopInfo(${JSON.stringify(job('script'))}).title`),'OpenRouter-Datenschutz schließt Jev aus');
  assert.equal(app.run(`stopInfo(${JSON.stringify(job('episode_audio'))}).title`),'OpenRouter-Datenschutz schließt Gemini aus');
  const quiet={status:'blocked',action:'resume',stop:{code:'jev_unavailable'},run:{run_id:'r',kind:'script',stages:{}}};
  assert.ok(app.run(`stopInfo(${JSON.stringify(quiet)}).text`).includes('schon beantwortete Abschnitte werden nicht noch einmal gefragt'));
});

test('a drawer redraw keeps a choice in progress in a select, and the OpenRouter models follow it',()=>{
  const app=studio();
  // The "Weiter mit" control sits in the Maschinenraum drawer, which redraws every few seconds while a job runs.
  const kept=app.run(`(()=>{
    const choice=document.getElementById('text-switch-choice'), model=document.getElementById('text-switch-model');
    choice.id='text-switch-choice'; choice.value='openrouter'; model.hidden=false;
    const box={querySelectorAll:selector=>selector.includes('select[id]')?[choice]:[],
      set innerHTML(html){choice.value='claude';model.hidden=true;}};
    replaceKeeping(box,'<select id="text-switch-choice"></select>');
    return JSON.stringify([choice.value,model.hidden]);
  })()`);
  assert.deepEqual(JSON.parse(kept),['openrouter',false]);
});

test('an episode that finished its review in this pass counts as done even when an older server says open',()=>{
  const app=studio();
  const row=(id,completed,stage_status)=>({episode_id:id,title:`Folge ${id}`,completed,stage_status});
  const p={phase:'script',stage:'review',total_segments:4,completed_segments:1,active_episodes:['ep_4'],activity:'Fakten und Erklärungen werden geprüft',
    episodes:[row('ep_1',true,'completed'),row('ep_2',false,'completed'),row('ep_3',false,'completed'),row('ep_4',false,'running')]};
  const html=app.run(`renderScriptProgress(${JSON.stringify(p)},true)`);
  assert.ok(html.includes('3 von 4 Folgen: Qualitätsprüfung abgeschlossen'),html);
  assert.equal((html.match(/✓/g)||[]).length,3);
  // A step that stopped is not counted.
  const stopped={...p,episodes:[...p.episodes.slice(0,3),row('ep_4',false,'interrupted')]};
  assert.ok(app.run(`renderScriptProgress(${JSON.stringify(stopped)},true)`).includes('3 von 4'));
});

test('once every episode passed its review the page names the series review, not an episode',()=>{
  const app=studio();
  const row=id=>({episode_id:id,title:`Folge ${id}`,completed:false,stage_status:'completed'});
  const p={phase:'script',stage:'review',total_segments:2,completed_segments:0,current_episode:'ep_1',episode_number:1,episode_title:'Folge ep_1',
    activity:'Fakten und Erklärungen werden geprüft',episodes:[row('ep_1'),row('ep_2')]};
  const html=app.run(`lastSyncAt=Date.now();renderScriptProgress(${JSON.stringify(p)},true)`);
  assert.ok(html.includes('Serienprüfung · alle Folgen im Zusammenhang'),html);
  assert.ok(html.includes('Korrektur nach der Serienprüfung · Fakten und Erklärungen werden geprüft'));
  assert.ok(html.includes('prüft ein Aufruf die ganze Serie im Zusammenhang'));
  assert.ok(!html.includes('Folge 1 von 2'));
  // The series review itself keeps its own wording.
  const own=app.run(`renderScriptProgress(${JSON.stringify({...p,activity:'Zusammenhang und Vollständigkeit der gesamten Skriptserie werden geprüft'})},true)`);
  assert.ok(own.includes('Zusammenhang und Vollständigkeit der gesamten Skriptserie werden geprüft')&&!own.includes('Korrektur nach'));
});

test('the reader sees the tags a Gemini recording will speak, marked in the script, and can place them',()=>{
  const app=studio();
  app.run(`project={id:'p',config:boot.defaults,audio_settings:{provider:'openrouter_gemini_tts',voices:{host_a:'Sadaltager',host_b:'Aoede'},expression:true},episodes:[]};`);
  const segment={segment_id:'s1',text:'Das hätte ich jetzt nicht erwartet.'};
  const expression={tags:1,segments:[{segment_id:'s1',text:'<chuckle> Das hätte ich jetzt nicht erwartet.'}],hash:'h1'};
  assert.equal(app.run(`expressiveText(${JSON.stringify(segment)},${JSON.stringify(expression)})`),
    '<p><mark class="expression-tag">&lt;chuckle&gt;</mark> Das hätte ich jetzt nicht erwartet.</p>');
  // A segment spoken differently from its written form shows the tags on what is spoken, below the text.
  const spoken={tags:1,segments:[{segment_id:'s1',text:'<gasp> Das hätte ich jetzt nie erwartet.'}]};
  assert.ok(app.run(`expressiveText(${JSON.stringify(segment)},${JSON.stringify(spoken)})`).includes('Gesprochen mit Ausdruck: <mark'));
  const panel=app.run(`renderExpressionPanel({script:{episode_id:'ep_001'},expression:${JSON.stringify(expression)}})`);
  assert.ok(panel.includes('1 Tags in 1 Abschnitten')&&panel.includes('data-action="expression" data-episode="ep_001"'));
  assert.ok(panel.includes('im Text markiert: 1× Schmunzeln.'),panel);
  // A Google recording's listener reaction (2026-10-06) is marked like a tag, counted, and keeps the text in place.
  const reacted={tags:0,segments:[{segment_id:'s1',text:'Das hätte ich |mhm| jetzt nicht erwartet.'}]};
  assert.equal(app.run(`expressiveText(${JSON.stringify(segment)},${JSON.stringify(reacted)})`),
    '<p>Das hätte ich <mark class="expression-tag" title="Einwurf der anderen Stimme">|mhm|</mark> jetzt nicht erwartet.</p>');
  assert.equal(app.run(`expressionKinds(${JSON.stringify(reacted)})`),'1× Einwurf');
  assert.ok(app.run(`renderExpressionPanel({script:{episode_id:'ep_001'},expression:null})`).includes('Ausdruck setzen'));
  // Qwen speaks no tags: nothing is shown.
  app.run(`project.audio_settings={provider:'qwen3_local',voices:{host_a:'Aiden',host_b:'Vivian'}};`);
  assert.equal(app.run(`renderExpressionPanel({script:{episode_id:'ep_001'},expression:${JSON.stringify(expression)}})`),'');
});

test('the production steps name when the expression is placed, and a tagging job counts its episodes',()=>{
  const app=studio();
  const gemini={provider:'openrouter_gemini_tts',voices:{host_a:'Sadaltager',host_b:'Aoede'},expression:true};
  const ep=(id,tagged)=>({script:{episode_id:id,title:id},expression:tagged?{tags:2,segments:[]}:null});
  app.run(`project={id:'p',config:boot.defaults,audio_settings:${JSON.stringify(gemini)},episodes:[],job:null};`);
  const stage=app.run('renderExpressionStage(false)');
  assert.ok(stage.includes('Folgt automatisch'));
  // All twelve kinds are named, used sparingly: not "a few tags".
  assert.ok(stage.includes('aus zwölf Arten (Lachen, Schmunzeln, Kichern, Atmen, Ausatmen, Seufzen, Aufatmen, Staunen, Schnalzen, Räuspern, kurze Pause, lange Pause)'),stage);
  app.run(`project.episodes=[${JSON.stringify(ep('ep_001',true))},${JSON.stringify(ep('ep_002',false))}];project.job={status:'running'};project.expression_progress={status:'running',done:1,total:2};`);
  assert.ok(app.run('renderExpressionStage(true)').includes('In Arbeit · 1 von 2 Folgen'));
  app.run(`project.job={status:'completed'};project.expression_progress={status:'completed',done:2,total:2};`);
  assert.ok(app.run('renderExpressionStage(true)').includes('1 Folge noch ohne · auf „Skripte lesen“'));
  app.run(`project.episodes=[${JSON.stringify(ep('ep_001',true))},${JSON.stringify(ep('ep_002',true))}];`);
  assert.ok(app.run('renderExpressionStage(true)').includes('Fertig · 2 von 2 Folgen'));
  assert.equal(app.run(`project.job={action:'expression'};jobPage()`),app.run('PAGE.scripts'));
  // Qwen speaks no tags, so the step is not shown.
  app.run(`project.audio_settings={provider:'qwen3_local',voices:{host_a:'Aiden',host_b:'Vivian'}};`);
  assert.equal(app.run('renderExpressionStage(true)'),'');
});

test('all read episodes can be approved at once, and a full studio queues instead of refusing',()=>{
  const app=studio();
  const ep=(id,extra={})=>({script:{episode_id:id,title:`Folge ${id}`},hash:'h',readable_hash:'r',audio_current:false,...extra});
  app.run(`boot.key_available=true;boot.capabilities={parallel_audio:true};project={id:'p',config:boot.defaults,audio_settings:{provider:'openrouter_gemini_tts',voices:{host_a:'Sadaltager',host_b:'Aoede'},expression:true},
    episodes:[${JSON.stringify(ep('ep_001',{audio_current:true}))},${JSON.stringify(ep('ep_002'))},${JSON.stringify(ep('ep_003'))},${JSON.stringify(ep('ep_004'))}],
    audio_jobs:[{id:'a',status:'running',episode:'ep_002'}],audio_capacity:{available:0},audio_queue:[{episode:'ep_003',position:1}]};`);
  // Only what has no current recording, is not recording and is not queued yet.
  assert.equal(app.run('pendingRecordings().map(e=>e.script.episode_id).join(",")'),'ep_004');
  app.run(`project.audio_queue=[];project.audio_jobs=[];`);
  const all=app.run('renderApproveAll(true)');
  assert.ok(all.includes('Alle gelesenen Folgen freigeben · 3')&&all.includes('data-action="audio-all"'));
  // Without the key (it lives only in the server's memory) the button says why and offers the key field.
  app.run('boot.key_available=false;');
  const keyless=app.run('renderApproveAll(true)');
  assert.ok(keyless.includes('data-action="audio-all" disabled')&&keyless.includes('Zuerst den OpenRouter-Key hinterlegen.')&&keyless.includes('Das Feld dafür steht oben in der Freigabe.'));
  assert.ok(!keyless.includes('data-key-field'),'no second key field on the page');
  app.run('boot.key_available=true;');
  // A full studio blocks nothing for a new approval, only the episode already recording.
  app.run(`project.audio_jobs=[{id:'a',status:'running',episode:'ep_002'}];`);
  assert.equal(app.run('audioBlockReason("ep_004",true)'),'');
  assert.equal(app.run('audioBlockReason("ep_002",true)'),'Diese Folge wird bereits vertont.');
  assert.ok(app.run('audioBlockReason("ep_004")').includes('gleichzeitig wie erlaubt'));
  app.run(`project.audio_queue=[{episode:'ep_003',position:1},{episode:'ep_004',position:2,error:'Skript inzwischen geändert.'}];`);
  const queue=app.run('renderAudioQueue()');
  assert.ok(queue.includes('Warteschlange der Vertonung · 2')&&queue.includes('startet nicht: Skript inzwischen geändert.'));
  assert.ok(app.run(`renderQueueState(${JSON.stringify(ep('ep_003'))})`).includes('wartet in der Warteschlange auf Platz 1'));
});

test('the overview card offers the whole podcast as a download, not only a player',()=>{
  const app=studio();
  const card=p=>app.run(`boot.capabilities={podcast_downloads:true,project_overview:true};overviewCard(${JSON.stringify(p)})`);
  const base={id:'asimov',topic:'Asimov',episode_count:2,job:null,audio_jobs:[]};
  assert.ok(!card({...base,episodes:[{episode_id:'ep_001',title:'Eins',audio:[]}]}).includes('podcast.zip'),'nothing to download yet');
  const partial=card({...base,episodes:[{episode_id:'ep_001',title:'Eins',audio:['episodes/ep_001/audio.mp3']},{episode_id:'ep_002',title:'Zwei',audio:[]}]});
  assert.ok(partial.includes('href="/download/asimov/podcast.zip"'));
  assert.ok(partial.includes('Fertige Folgen herunterladen <span>ZIP · 1 von 2 Folgen</span>'));
  assert.ok(partial.includes('class="download-all small"'),'the page click handler fetches it like the audio page ZIP');
  const complete=card({...base,episodes:[{episode_id:'ep_001',title:'Eins',audio:['a.mp3']},{episode_id:'ep_002',title:'Zwei',audio:['b.mp3']}]});
  assert.ok(complete.includes('Podcast herunterladen <span>ZIP · 2 Folgen</span>'));
  assert.ok(complete.includes('Podcast anhören'));
});

test('a project shows its pre-approvals and links to the settings, and a stop they cover says the studio continues by itself',()=>{
  const app=studio();
  app.run(`project={id:'p',config:boot.defaults,execution:{text:'parallel',audio:'parallel'},jev_probe:true,jev_default:true,chat:[],allowances:{fresh_attempts:2,extra_calls:250,used:{fresh_attempts:1,extra_calls:40}}};`);
  const html=app.run('setupSummary()');
  assert.ok(html.includes('bis 2× neue Anläufe · bis zu 250 zusätzliche Aufrufe je Lauf'));
  assert.ok(html.includes('data-action="open-settings"'));
  assert.ok(!html.includes('data-allowance'),'set on the settings page for every project (2026-10-03)');
  assert.ok(html.includes('Standard für deutschsprachige Projekte; ohne Key nur Wortsuche'));
  assert.ok(app.run('allowanceUse(project.allowances)').includes('1 von 2 neuen Anläufen · 40 von 250 zusätzlichen Aufrufen'));
  assert.equal(app.run('allowanceUse({fresh_attempts:0,extra_calls:0})'),'');
  const fresh=app.run(`waitNote({status:'blocked',allowance:{kind:'fresh_attempts',number:2,of:2}})`);
  assert.ok(fresh.includes('neue Anläufe (2 von 2 in diesem Lauf)'));
  const calls=app.run(`waitNote({status:'blocked',allowance:{kind:'model_calls',model_calls:165,extra_calls:15}})`);
  assert.ok(calls.includes('Aufruflimit auf 165 (+15)') && calls.includes('setzt innerhalb einer halben Minute selbst fort'));
});

test('the production report loads on request and shows stages, versions, stops and approvals',()=>{
  const app=studio();
  const job={status:'completed',run:{run_id:'run_s',kind:'script'}};
  assert.ok(app.run(`renderProductionReport(${JSON.stringify(job)})`).includes('data-action="production-report" data-run-id="run_s">Produktionsbericht laden'));
  const report={calls:442,failed_calls:2,model_minutes:1239.7,stages:[{stage:'script_review',label:'Belegprüfung <x>',calls:111,failed:14,minutes:434.7,share:0.351,minutes_per_call:3.9}],
    versions:[{version:'script_review.v9-gaps-notes+followup',calls:38,minutes:300,first:'2026-09-29T12:16:00+00:00'}],
    providers:{claude_code:{calls:372,minutes:730,billed_usd:0},openrouter:{calls:3,minutes:5,billed_usd:1.25}},
    stops:{total:13,by_stage:{review:6,teaching:5}},approvals:{fresh_attempts:2,model_calls:443,allowances:[{}],text_switch:null}};
  app.run(`productionReports.run_s=${JSON.stringify({report})}`);
  const html=app.run(`renderProductionReport(${JSON.stringify(job)})`);
  for(const text of ['442 Aufrufe · 20,7 Std. Modellzeit','Belegprüfung &lt;x&gt;','111 · 14 abgebrochen','7,2 Std.','35 %','3,9 Min.',
    'script_review.v9-gaps-notes+followup','Claude · Abo: 372 Aufrufe','1.25 USD abgerechnet','Qualitätsprüfung 6×, Lehrkonzept 5×',
    '2× neue Anläufe · Aufruflimit 443 · 1× per Vorab-Erlaubnis'])assert.ok(html.includes(text),text);
  assert.ok(!html.includes('<x>'));
});

test('stopped recordings name their shared reason in the bar and resume all at once',async()=>{
  // 2026-10-04: fourteen Transformer recordings stopped on a key's credit limit; the bar said only "14 angehalten".
  const app=studio();
  const stop={code:'openrouter_forbidden',stage:'synthesis',message:'OpenRouter hat die Anfrage abgewiesen: Key limit exceeded.'};
  const job=(n,extra={})=>({id:'a'+n,action:'resume',status:'blocked',episode:'ep_00'+n,stop,run:{run_id:'run_'+n,kind:'episode_audio',stages:{}},...extra});
  const jobs=[job(1),job(2),job(3)];
  app.run(`boot.key_available=true;step=PAGE.brief;project={id:'p',episodes:[1,2,3].map(n=>({script:{episode_id:'ep_00'+n,title:'Folge '+n},audio:[]})),
    audio_jobs:${JSON.stringify(jobs)},job:${JSON.stringify(jobs[0])}};`);
  const bar=app.run('renderAudioJobBar()');
  assert.ok(bar.includes('Vertonung: 0 von 3 Aufträgen fertig, 3 angehalten: OpenRouter verweigert den Zugriff'),bar);
  assert.ok(bar.includes('data-action="resume-stopped-audio">Alle 3 fortsetzen'));
  assert.ok(bar.includes('data-action="open-settings">OpenRouter-Key'),'a key stop leads to the key');
  // Different reasons: the bar counts them without naming one; a single stop has its own card's button.
  app.run(`project.audio_jobs[2].stop={code:'openrouter_connection',stage:'synthesis'};`);
  assert.ok(app.run('audioJobSummary()').endsWith('3 angehalten'));
  // Resume all asks once per stopped recording and names the one the server refused.
  app.run(`project.audio_jobs[2].stop=project.audio_jobs[0].stop;`);
  app.responses.set('/api/projects/p',{id:'p',episodes:[],audio_jobs:[],job:null});
  app.run(`const original=api;api=async(path,data)=>{if(data?.episode==='ep_002')throw new Error('Kein Platz frei.');return original(path,data);};`);
  await app.run('resumeStoppedAudio()');
  const resumes=app.requests.filter(r=>r.path==='/api/projects/p/start').map(r=>JSON.parse(r.options.body));
  assert.deepEqual(resumes.map(r=>[r.action,r.run_id,r.episode]),[['resume','run_1','ep_001'],['resume','run_3','ep_003']]);
  assert.ok(app.elements.get('notice').textContent.includes('2 von 3 Vertonungen fortgesetzt. Nicht fortgesetzt: ep_002: Kein Platz frei.'));
});

test('a missing OpenRouter key is named on every page with what needs it until one is stored',async()=>{
  const app=studio();
  const rows=[{need:'gemini_audio',projects:['Asimov','Ontologien','Transformer']},{need:'jev',projects:['Asimov <alt>']},
    {need:'unknown',projects:['x']}];
  app.run(`overviewPage=true;overviewData={projects:[],trash:[],key_reminder:${JSON.stringify(rows)}};renderKeyNote();`);
  const note=app.elements.get('key-note');
  assert.equal(note.hidden,false);
  assert.ok(note.innerHTML.includes('OpenRouter-Key fehlt.'));
  // Since 2026-10-06 Gemini speaks through Google or OpenRouter; the reminder names the route whose key is missing.
  assert.ok(note.innerHTML.includes('Gemini-Vertonung über OpenRouter (3 Projekte) · Jev in der Lückenprobe („Asimov &lt;alt&gt;“)'));
  assert.ok(note.innerHTML.includes('Ohne Key warten Vertonungen mit Gemini über OpenRouter; suchen neue Skriptläufe Lücken nur per Wortsuche'));
  assert.ok(note.innerHTML.includes('data-action="store-key" data-key-field="reminder-key"'),'the key goes in right there');
  assert.ok(!note.innerHTML.includes('unknown'));
  // A project page reads its own payload; the settings page points to its key field instead of a second one.
  app.run(`overviewPage=false;project={id:'p',key_reminder:[{need:'openrouter_text',projects:['Asimov']}]};renderKeyNote();`);
  assert.ok(note.innerHTML.includes('OpenRouter-Textmodell („Asimov“)'));
  app.run(`project=null;settingsPage=true;settingsData={key_reminder:[{need:'jev',projects:['Asimov']}]};renderKeyNote();`);
  assert.ok(note.innerHTML.includes('Eingabe unten unter „OpenRouter-Key“') && !note.innerHTML.includes('reminder-key'));
  // A stored key ends the reminder at once, before the next poll brings an empty list.
  app.run(`settingsPage=false;project={id:'p',key_reminder:[{need:'jev',projects:['Asimov']}]};renderKeyNote();`);
  app.run(`$("reminder-key").value="sk-or-test"`);
  app.responses.set('/api/key',{key_available:true});
  await app.run('storeKey("reminder-key")');
  assert.equal(note.hidden,true);
  assert.equal(app.run('keyNote()'),'');
});

test('a missing Google key has its own reminder and field, and storing it ends only that reminder',async()=>{
  const app=studio();
  const rows=[{need:'google_audio',projects:['Ontologien'],key:'google'},{need:'jev',projects:['Asimov'],key:'openrouter'}];
  app.run(`overviewPage=false;settingsPage=false;project={id:'p',key_reminder:${JSON.stringify(rows)}};renderKeyNote();`);
  const note=app.elements.get('key-note');
  assert.ok(note.innerHTML.includes('Google-Key fehlt.') && note.innerHTML.includes('Gemini-Vertonung über Google („Ontologien“)'));
  assert.ok(note.innerHTML.includes('OpenRouter-Key fehlt.') && note.innerHTML.includes('Jev in der Lückenprobe'));
  assert.ok(note.innerHTML.includes('data-key-field="reminder-google-key" data-key-kind="google"'));
  app.run(`$("reminder-google-key").value="AIza-test"`);
  app.responses.set('/api/key',{key_available:false,google_key_available:true});
  await app.run('storeKey("reminder-google-key","google")');
  const sent=JSON.parse(app.requests.filter(r=>r.path==='/api/key').at(-1).options.body);
  assert.deepEqual(sent,{key:'AIza-test',kind:'google'});
  assert.ok(!note.innerHTML.includes('Google-Key fehlt.') && note.innerHTML.includes('OpenRouter-Key fehlt.'));
});

test('the companion kit is offered per episode and its texts are ready to copy',()=>{
  const app=studio();
  app.run(`project={id:'p',config:{language:'de-DE'},audio_settings:{provider:'qwen3_local',voices:{host_a:'Aiden',host_b:'Vivian'}},episodes:[]};`);
  const none=app.run(`renderPublishKit({script:{episode_id:'ep_001',title:'T'},publish_kit:null})`);
  assert.ok(none.includes('data-action="publish_kit" data-episode="ep_001"')&&none.includes('Begleitmaterial erstellen'));
  const kit={short:'Kurz <b>',description:'Lang\n\nKapitel\n00:00 Start',characters:2400,limit:4000,sources_listed:9,sources_total:12,
    chapter_problems:[{code:'too_few_chapters'}],recorded:false,folder:'episodes/ep_001/publish'};
  const html=app.run(`renderPublishKit({script:{episode_id:'ep_001',title:'T'},publish_kit:${JSON.stringify(kit)}})`);
  assert.ok(html.includes('Kurz &lt;b&gt;</textarea>'),'the text is escaped');
  assert.ok(html.includes('(2.400 von 4.000 Zeichen)')&&html.includes('9 von 12 Quellen'));
  assert.ok(html.includes('Noch nicht vertont')&&html.includes('ab drei Kapiteln'));
  assert.ok(html.includes('data-action="copy-text" data-source="kit-long-ep_001"')&&html.includes('data-fresh="1"'));
});

test('the whole podcast gets its own companion kit with the transcript of every episode (D-165)',()=>{
  const app=studio();
  app.run(`project={id:'p',config:{language:'de-DE'},audio_settings:{provider:'qwen3_local',voices:{host_a:'Aiden',host_b:'Vivian'}},episodes:[],podcast_kit:null};`);
  const none=app.run(`renderPodcastKit()`);
  assert.ok(none.includes('data-action="publish_kit" data-podcast="1"')&&none.includes('Begleitmaterial für den Podcast erstellen'));
  assert.ok(none.includes('Transkript aller Folgen')&&!none.includes('data-fresh'),'nothing to reword yet');
  // A kit that no longer covers the latest recordings offers the rebuild, not its old texts.
  app.run(`project.podcast_kit={outdated:true};`);
  const old=app.run(`renderPodcastKit()`);
  assert.ok(old.includes('Neu zusammenstellen')&&old.includes('neu vertont')&&!old.includes('textarea')&&!old.includes('data-fresh'));
  const kit={folder:'publish',short:'Kurz <b>',description:'Lang',characters:1800,limit:4000,episodes:6,recorded:4,sources_total:37};
  app.run(`project.podcast_kit=${JSON.stringify(kit)};`);
  const html=app.run(`renderPodcastKit()`);
  assert.ok(html.includes('Kurz &lt;b&gt;</textarea>'),'the text is escaped');
  assert.ok(html.includes('(1.800 von 4.000 Zeichen)')&&html.includes('4 von 6 Folgen vertont'));
  assert.ok(html.includes('Transkript aller 6 Folgen')&&html.includes('<code>publish/</code>')&&html.includes('nennt 37 Quellen'));
  assert.ok(html.includes('data-source="podcast-kit-short"')&&html.includes('data-source="podcast-kit-long"'));
  assert.ok(html.includes('data-podcast="1" data-fresh="1"'));
  app.run(`project.podcast_kit=${JSON.stringify({...kit,recorded:6})};`);
  assert.ok(!app.run(`renderPodcastKit()`).includes('Folgen vertont'),'a fully recorded series needs no note');
  const en=studio({language:'en'});
  en.run(`project={id:'p',config:{language:'en-US'},audio_settings:{provider:'qwen3_local',voices:{host_a:'Aiden',host_b:'Vivian'}},episodes:[],podcast_kit:${JSON.stringify({...kit,episodes:1,recorded:0,sources_total:1})}};`);
  const english=en.run(`renderPodcastKit()`);
  assert.ok(english.includes('Companion kit for the podcast')&&english.includes('The episode is not recorded yet'));
  assert.ok(english.includes("The episode's transcript")&&english.includes('names 1 source.'));
});

test('the podcast kit offers its transcript and sources on their own, without the MP3s of the ZIP',()=>{
  // The user's report of 2026-10-07: the panel offered only the two descriptions, the transcript lay on disk and in the ZIP.
  const app=studio();
  const kit={folder:'publish',short:'Kurz',description:'Lang',characters:1800,limit:4000,episodes:2,recorded:2,sources_total:3};
  app.run(`project={id:'mein projekt',config:{language:'de-DE'},audio_settings:{provider:'qwen3_local',voices:{host_a:'Aiden',host_b:'Vivian'}},episodes:[],podcast_kit:${JSON.stringify(kit)}};boot.capabilities={podcast_downloads:true};`);
  assert.ok(!app.run(`renderPodcastKit()`).includes('/kit/'),'a server without the route offers no link that would fail');
  app.run(`boot.capabilities={podcast_downloads:true,podcast_kit_downloads:true};`);
  const html=app.run(`renderPodcastKit()`);
  assert.ok(html.includes('<a href="/download/mein%20projekt/kit/transcript.md" download>Transkript herunterladen</a>'));
  assert.ok(html.includes('<a href="/download/mein%20projekt/kit/sources.md" download>Quellen herunterladen</a>'));
  assert.ok(!html.includes('download-all'),'a text file is not fetched like the ZIP');
  app.run(`project.podcast_kit={outdated:true};`);
  assert.ok(!app.run(`renderPodcastKit()`).includes('/kit/'),'an outdated kit is not offered, as the ZIP leaves it out');
  const en=studio({language:'en'});
  en.run(`project={id:'p',config:{language:'en-US'},audio_settings:{provider:'qwen3_local',voices:{host_a:'Aiden',host_b:'Vivian'}},episodes:[],podcast_kit:${JSON.stringify(kit)}};boot.capabilities={podcast_kit_downloads:true};`);
  const english=en.run(`renderPodcastKit()`);
  assert.ok(english.includes('>Download transcript</a>')&&english.includes('>Download sources</a>'));
});

test('the Google route shows who explains in this episode and the key it needs',()=>{
  const app=studio();
  app.run(`project={id:'p',config:{language:'de-DE'},audio_settings:{provider:'google_gemini_tts',voices:{host_a:'Erinome',host_b:'Sadachbia'},alternate_roles:true},episodes:[]};boot.google_key_available=false;`);
  const card=episode=>app.run(`renderApprovalCard({script:{episode_id:'${episode}',title:'T'},expression:{tags:2,backchannels:3}},currentAudio(),true)`);
  assert.ok(card('ep_001').includes('Erinome erklärt, Sadachbia fragt'));
  assert.ok(card('ep_002').includes('Sadachbia erklärt, Erinome fragt'));
  assert.ok(card('ep_001').includes('3 Einwürfe'));
  assert.ok(card('ep_001').includes('data-key-kind="google"'));
  assert.equal(app.run('audioBlockReason("ep_001")'),'Zuerst den Google-Key hinterlegen.');
});

test('the server note offers a restart once the code changed and can take it back',()=>{
  const app=studio();
  app.run(`overviewPage=false;project={id:'p',server:{stale:false,restart_requested:false}}`);
  assert.equal(app.run('serverNote()'),'');
  app.run(`project.server.stale=true`);
  assert.ok(app.run('serverNote()').includes('data-action="restart-when-idle">Neu starten, sobald nichts läuft'));
  app.run(`project.server.restart_requested=true`);
  const pending=app.run('serverNote()');
  assert.ok(pending.includes('Neustart vorgemerkt') && pending.includes('data-action="restart-cancel"'));
  app.run('renderServerNote()');
  assert.equal(app.elements.get('server-note').hidden,false);
});

test('the brief summary shows what the series is for and how current its sources must be',()=>{
  const app=studio();
  app.run(`project={id:'p',config:{...boot.defaults,series_goal:{understand:1,evaluate:2,apply:3},recency_months:6},execution:{text:'parallel',audio:'parallel'},chat:[],allowances:{}};`);
  const html=app.run('setupSummary()');
  assert.ok(html.includes('Anwenden ●●● · Bewerten ●●○ · Verstehen ●○○'),'the main aim first');
  assert.ok(html.includes('der letzten 6 Monate'));
  assert.ok(app.run('goalSummary(null)').includes('prüft vor allem, was hält'));
  app.run(`project.config={...boot.defaults};project.proposal_applied=false;project.chat=[{role:'assistant',topic:boot.defaults.topic,central_question:boot.defaults.central_question,prior_knowledge:boot.defaults.prior_knowledge,depth_request:boot.defaults.depth_request,focus_questions:[],excluded_topics:[],target_total_minutes:null,recency_months:6}]`);
  assert.equal(app.run('proposalChangesBrief()'),true,'a proposed recency rule changes the brief');
  assert.ok(app.run('setupSummary()').includes('der letzten 6 Monate'),'the proposal shows before it is applied');
  app.run(`project.chat[0].recency_months=0`);
  assert.equal(app.run('proposalChangesBrief()'),false,'removing a rule that is not set changes nothing');
  assert.ok(app.run('setupSummary()').includes('Keine Vorgabe'));
});

test('the research page lists the missing works and uploads one as raw bytes for its questions',async()=>{
  const app=studio();
  await new Promise(resolve=>setTimeout(resolve,0));
  const works={missing:[{work:'Kuran: Private Truths, Public Lies (1995)',state:'blocked',provided:null,tasks:[{id:'t_kuran',question:'Präferenzfälschung?'}]},
    {work:'Goodhart (1975)',state:'retrying',provided:'work_ab',tasks:[{id:'t_good',question:'Reflexivität?'}]}],
    provided:[{id:'work_ab',citation:'Goodhart (1975)',tasks:['t_good']},{id:'work_cd',citation:'Hacking (1995)',tasks:[]}]};
  app.run(`project={id:'p',config:boot.defaults,works:${JSON.stringify(works)}};`);
  const panel=app.run('worksPanel()');
  assert.ok(panel.includes('Kuran: Private Truths, Public Lies (1995)</strong> <span class="tag">Frage blockiert'));
  assert.ok(panel.includes('Gebraucht für: Präferenzfälschung?'));
  assert.ok(panel.includes('Goodhart (1975)</strong> <span class="tag">hochgeladen'));
  assert.ok(!panel.includes('data-work-index="1"'),'an uploaded work offers no second upload');
  assert.ok(panel.includes('Weitere hochgeladene Werke:</strong> Hacking (1995)'));
  // The file goes as it is, with the citation and its questions in the address.
  app.run(`$('work-file-0').files=[{name:'kuran.pdf'}];`);
  const path='/api/projects/p/work?citation=Kuran%3A+Private+Truths%2C+Public+Lies+%281995%29&task=t_kuran';
  app.responses.set(path,{work:{id:'work_ef'},works:{...works,missing:[{...works.missing[0],provided:'work_ef'}]}});
  await app.run(`uploadWork({dataset:{workIndex:'0'}})`);
  const sent=app.requests.find(r=>r.path===path);
  assert.equal(sent.options.headers['Content-Type'],'application/octet-stream');
  assert.equal(sent.options.body.name,'kuran.pdf');
  assert.equal(app.run('project.works.missing[0].provided'),'work_ef');
});

test('the missing-works panel shows today\'s CORE calls against the daily allowance',()=>{
  const app=studio();
  app.run(`project={id:'p',config:boot.defaults,works:{missing:[],provided:[]},server:{core:{date:'2026-10-01',calls:37,limit:1000}}};`);
  assert.ok(app.run('worksPanel()').includes('CORE heute: 37 von 1000 Abrufen.'));
  app.run(`project.server.core.calls=1000;`);
  assert.ok(app.run('worksPanel()').includes('für heute aufgebraucht'));
  app.run(`project.server.core=null;`);
  assert.ok(!app.run('worksPanel()').includes('CORE heute'),'without a key nothing is shown');
});

// Findings of the product review, 2026-10-02.
test('every stop code the backend raises has its own card instead of the generic one',()=>{
  const app=studio();
  const info=(code,kind='research')=>app.run(`stopInfo({status:'blocked',action:'resume',stop:{code:'${code}'},run:{run_id:'r',kind:'${kind}',stages:{}}})`);
  const expected={missing_executable:'fix',unsupported_codex_launcher:'fix',unsupported_claude_launcher:'fix',openrouter_forbidden:'fix',
    openrouter_search_unsupported:'fix',invalid_backend:'fix',credential_in_prompt:'dead',credential_in_response:'dead',
    invalid_output_schema:'dead',unsupported_run:'dead',missing_outputs:'dead',worker_stop:'dead',research_context_incomplete:'dead',
    research_questions_open:'retry',question_scope_unresolved:'retry',invalid_speech:'retry',audio_approval_required:'decision',
    // Gemini through Google (google_speech, 2026-10-06).
    google_key_required:'fix',invalid_google_key:'fix',google_authentication:'fix',google_quota:'wait',
    google_speech_request:'fix',google_unavailable:'retry',google_connection:'retry'};
  for(const [code,kind] of Object.entries(expected)){
    const stop=info(code);
    assert.equal(stop.kind,kind,code);
    assert.ok(!stop.text.includes(`(Code ${code})`),`${code} falls through to the generic card`);
  }
  for(const code of ['missing_executable','unsupported_codex_launcher','unsupported_claude_launcher'])assert.ok(info(code).actions.includes('check'),code);
  assert.ok(info('openrouter_forbidden').actions.includes('key'));
  for(const code of ['google_key_required','google_authentication'])assert.ok(info(code,'episode_audio').actions.includes('google_key'),code);
  // The text model is chosen on the settings page since 2026-10-03; these cards lead there, not to the brief.
  for(const code of ['openrouter_search_unsupported','invalid_backend'])assert.ok(info(code).actions.includes('open_settings'),code);
  assert.ok(info('audio_approval_required','episode_audio').actions.includes('audio_again'));
  assert.ok(info('research_questions_open').text.includes('„Fortsetzen“ recherchiert sie weiter'));
  assert.ok(info('question_scope_unresolved').text.includes('„Fortsetzen“ kommt jetzt daran vorbei'));
  assert.ok(info('research_context_incomplete').text.includes('nicht mehr lesbar'));
  assert.ok(info('invalid_speech','episode_audio').text.includes('zweimal zu kurz, zu lang oder mit langer Stille'));
  // Correction loops that fell through before.
  for(const [code,kind] of [['invalid_polish_evidence','script'],['invalid_series_evidence','script'],['invalid_teaching_evidence','script'],
    ['invalid_teaching_repair','script'],['invalid_research_gap','script'],['verified_question_split','research'],['invalid_dossier_rebuild','research']])
    assert.equal(info(code,kind).title,'Korrekturversuche aufgebraucht',code);
  // A missing answer is asked again; a spent correction loop no longer hides behind that promise.
  assert.ok(info('invalid_model_output').text.includes('Mit neuen Anläufen fortsetzen'));
  // An audio card uses the same rules: a stopped Gemini episode names the implausible take.
  app.run(`project={id:'p',config:boot.defaults,episodes:[],audio_jobs:[{id:'a1',episode:'ep_001',status:'blocked',error_code:'invalid_speech',message:'Abschnitt seg_004 zu kurz.',run:{run_id:'run_a',kind:'episode_audio',stages:{}}}]};`);
  assert.ok(app.run('renderAudioJob(project.audio_jobs[0])').includes('Unplausible Gemini-Aufnahme'));
});

test('fresh attempts are offered only where the server says the backend accepts them',()=>{
  const app=studio();
  const card=job=>app.run(`renderStopCard(${JSON.stringify(job)},stopInfo(${JSON.stringify(job)}))`);
  const research={id:'j',status:'blocked',action:'resume',stop:{code:'rejected_output'},run:{run_id:'run_x',kind:'research',stages:{}},progress:{phase:'research'}};
  assert.ok(card(research).includes('data-action="fresh-attempts"'),'an older server without the field keeps the earlier rule');
  assert.ok(!card({...research,fresh_attempts:false}).includes('data-action="fresh-attempts"'),'no stuck call: nothing to offer');
  assert.ok(card({...research,fresh_attempts:true}).includes('data-action="fresh-attempts"'));
  // After an allowance reset the repairs, the script review card no longer offers what the backend would refuse.
  const review={id:'s',status:'blocked',action:'resume',stop:{code:'script_review_failed'},run:{run_id:'run_s',kind:'script',stages:{}},progress:{phase:'script'}};
  assert.ok(!card({...review,fresh_attempts:false}).includes('data-action="fresh-attempts"'));
  assert.ok(card({...review,fresh_attempts:false}).includes('data-action="resume"'));
  // The series review and a stored series-repair failure get the path the backend has had all along.
  const series={...review,stop:{code:'series_review_failed'},fresh_attempts:true};
  assert.ok(card(series).includes('data-action="fresh-attempts"'));
  assert.ok(card(series).includes('gibt der Serienkorrektur eine neue Runde'));
  assert.ok(!card({...series,fresh_attempts:false}).includes('neue Runde'));
  assert.ok(card({...review,stop:{code:'invalid_series_evidence'},fresh_attempts:true}).includes('data-action="fresh-attempts"'));
});

test('a transient technical stop announces its automatic resume and says when they are used up',()=>{
  const app=studio();
  const later=new Date(Date.now()+600000).toISOString();
  const job={id:'t',status:'failed',action:'resume',stop:{code:'timeout'},run:{run_id:'r',kind:'research',stages:{}},auto_resume_kind:'transient',auto_resume_at:later,auto_resume_count:1};
  const note=app.run(`waitNote(${JSON.stringify(job)})`);
  assert.ok(note.includes('vorübergehender technischer Fehler'));
  assert.ok(note.includes('Versuch 2 von 3'));
  const spent=app.run(`waitNote(${JSON.stringify({...job,auto_resume_at:undefined,auto_resume_exhausted:true})})`);
  assert.ok(spent.includes('der Fehler kam jedes Mal wieder'));
  assert.ok(!spent.includes('Nach dem Reset'));
});

test('a worker that outlived a Studio restart runs elsewhere and is stopped only where its identity is known',()=>{
  const app=studio();
  app.run(`project={id:'p',config:boot.defaults,research:'d',job:{id:'j',action:'resume',status:'running',external:true,external_stoppable:false,started_at:new Date().toISOString(),run:{run_id:'r',kind:'research',status:'running',stages:{}},progress:{phase:'research'}}};step=PAGE.research;render();`);
  const bar=app.elements.get('job-bar').innerHTML;
  assert.ok(bar.includes('läuft außerhalb dieses Studios'));
  assert.ok(!bar.includes('data-action="stop"'),'without its pid the Studio cannot stop it');
  assert.ok(app.elements.get('stop-card').innerHTML.includes('endet von selbst'));
  app.run(`project.job.external_stoppable=true;lastJobView='';renderJob();`);
  assert.ok(app.elements.get('job-bar').innerHTML.includes('data-action="stop"'));
});

test('a paused run of another lane keeps its stop card on its own page',()=>{
  const app=studio(), p=workflowProject(app);
  p.job={id:'s',action:'script',status:'blocked',stop:{code:'script_review_failed'},run:{run_id:'run_s',kind:'script',status:'blocked',stages:{review:{status:'blocked'}}}};
  p.parked_jobs=[p.job,{id:'r',action:'research',status:'failed',stop:{code:'timeout'},run:{run_id:'run_r',kind:'research',status:'failed',stages:{}}}];
  app.run(`project=${JSON.stringify(p)};overviewPage=false;step=PAGE.research;render();`);
  const card=app.elements.get('stop-card').innerHTML;
  assert.ok(card.includes('Zeitlimit eines Modellaufrufs'));
  assert.ok(card.includes('data-action="resume" data-run-id="run_r"'));
});

test('polls run one at a time, skip a hidden tab but for its title, and poll again when it shows',async()=>{
  const app=studio(), p=workflowProject(app);
  await app.run(`selectProject('test',${JSON.stringify(p)})`);
  app.responses.set('/api/projects/test',p);
  const polls=()=>app.requests.filter(r=>r.path==='/api/projects/test').length;
  const before=polls();
  // Two ticks while the first answer is still on its way: one request.
  await Promise.all([app.run('poll()'),app.run('poll()')]);
  assert.equal(polls(),before+1);
  app.run('document.hidden=true;');
  await app.run('poll()');
  assert.equal(polls(),before+2,'a hidden tab still polls once a minute, for its title');
  await app.run('poll()');
  assert.equal(polls(),before+2,'and not more often');
  app.run('document.hidden=false;');
  await app.run('poll()');
  assert.equal(polls(),before+3);
  // The overview polls every ten seconds.
  app.responses.set('/api/projects',{projects:[],trash:[],server:{}});
  app.run(`overviewPage=true;lastPollAt.overview=0;boot.capabilities={project_overview:true};`);
  await app.run('poll()');await app.run('poll()');
  assert.equal(app.requests.filter(r=>r.path==='/api/projects').length,1);
});

test('a new server instance reloads the session and says when the key is gone and recordings wait for it',async()=>{
  const app=studio(), p=workflowProject(app);
  await app.run(`selectProject('test',${JSON.stringify(p)})`);
  await new Promise(resolve=>setTimeout(resolve,0));  // the page's own start-up reads the bootstrap first
  app.run(`boot.server={instance:'old'};boot.key_available=true;`);
  app.responses.set('/api/projects/test',{...p,server:{instance:'new'}});
  app.responses.set('/api/bootstrap',{token:'fresh',voices:['Aiden','Vivian'],projects:[],defaults:app.run('boot.defaults'),key_available:false,server:{instance:'new'}});
  await app.run('poll()');
  assert.equal(app.run('boot.token'),'fresh');
  assert.equal(app.run('boot.server.instance'),'new');
  assert.ok(app.elements.get('notice').textContent.includes('muss erneut eingegeben werden'));
  // The queue names the key as the reason, with the field to enter it.
  app.run(`project.episodes=[{script:{episode_id:'ep_001',title:'Eins'},speech:{characters:12000,minutes:14.6}}];project.audio_queue=[{episode:'ep_001',position:1,waiting:'key'}];`);
  const queue=app.run('renderAudioQueue()');
  assert.ok(queue.includes('wartet auf den OpenRouter-Key'));
  assert.ok(queue.includes('oben in der Freigabe hinterlegt')&&!queue.includes('id="queue-key"'),'the queue points to the one key field');
  assert.ok(queue.includes('ca. 15 Min. Audio · 12.000 Zeichen Sprechtext'));
  app.run(`project.audio_queue=[{episode:'ep_001',position:1,waiting:'place'}];`);
  assert.ok(app.run('renderAudioQueue()').includes('wartet auf einen freien Platz'));
});

test('a paid recording shows its size before approval, for one episode and for all',()=>{
  const app=studio(), p=workflowProject(app);
  const episode=(id,chars,minutes)=>({...publishedEpisode({}),script:{...publishedEpisode({}).script,episode_id:id,title:id},speech:{characters:chars,minutes}});
  p.episodes=[episode('ep_001',30000,24.4),episode('ep_002',20000,16)];
  p.audio_settings={provider:'openrouter_gemini_tts',voices:{host_a:'Sadaltager',host_b:'Aoede'}};
  app.run(`project=${JSON.stringify(p)};boot.key_available=true;boot.capabilities={parallel_audio:true};episodeIndex=0;`);
  const card=app.run(`renderApprovalCard(project.episodes[0],project.audio_settings,true)`);
  assert.ok(card.includes('ca. 24 Min. Audio · 30.000 Zeichen Sprechtext'));
  assert.ok(!/USD|€|Kosten/.test(card),'no price: it cannot be checked offline');
  const all=app.run('renderApproveAll(true)');
  assert.ok(all.includes('Umfang zusammen:</strong> ca. 40 Min. Audio · 50.000 Zeichen Sprechtext'));
});

test('a calibrated script projection shows the expected calls beside the minimum and suggests the limit from them',()=>{
  const app=studio();
  const progress={phase:'script',model_calls:140,model_call_limit:150,budget_projection:{used:140,minimum_remaining_calls:20,feasible:true,
    expected_remaining_calls:60,expected_shortfall:50,calibration:{run_id:'run_old'},expected_label:'Erwartung nach dem letzten abgeschlossenen Skriptlauf.'}};
  assert.equal(app.run(`suggestedCalls({progress:${JSON.stringify(progress)}})`),200);
  assert.equal(app.run(`suggestedCalls({progress:${JSON.stringify({...progress,budget_projection:{used:140,minimum_remaining_calls:20}})}})`),165);
  app.run(`project={id:'p',config:boot.defaults,job:{id:'j',action:'script',status:'running',started_at:new Date().toISOString(),run:{run_id:'r',kind:'script',status:'running',stages:{}},progress:${JSON.stringify(progress)}}};drawerOpen=true;renderJob();`);
  const html=app.elements.get('job-status').innerHTML;
  assert.ok(html.includes('mindestens 20 weitere nötig · erwartet etwa 60, mehr als verfügbar'));
  assert.ok(html.includes('Erwartung nach dem letzten abgeschlossenen Skriptlauf.'));
});

test('the research page names a pass with noted limits and how often an answer was checked again',()=>{
  const app=studio();
  const ledger={closed:1,total:1,phase:'completed',questions:[{id:'q1',question:'Warum?',status:'verified',steps:2,read_sections:3,revalidations:2,acceptance:['x']}]};
  assert.ok(app.run(`renderResearchQuestions(${JSON.stringify(ledger)},new Set(),false,'r',0,0)`).includes('2 Mal erneut geprüft'));
  app.run(`project={id:'p',config:boot.defaults,job:{id:'j',action:'research',status:'completed',run:{run_id:'r',kind:'research',status:'completed',stages:{}},progress:{phase:'research',research_questions:${JSON.stringify(ledger)},
    research_quality:{closed:1,total:2,passed:true,passed_with_noted_limits:true,requirements:[{question:'Q1',passed:true,reason:'ok'},{question:'Q2',passed:false,recorded_limit:true,reason:'Quelle schweigt'}]}}}};step=PAGE.research;render();`);
  const html=app.elements.get('research-progress').innerHTML;
  assert.ok(html.includes('bestanden mit vermerkten Grenzen'));
  assert.ok(html.includes('◇ Q2</strong> · als Grenze vermerkt'));
});

// Claude on the user's Anthropic key and the money limit of a billed run (D-145, D-146).
test('a billed run shows its money, and a missing or spent limit is set or raised on its stop card',()=>{
  const app=studio();
  const running={id:'p',config:{},job:{id:'j1',status:'running',action:'research',started_at:new Date().toISOString(),
    run:{run_id:'run_m',kind:'research',stages:{}},progress:{phase:'research',model_calls:12,model_call_limit:750,cost_spent_usd:3.2,cost_limit_usd:20}}};
  app.run(`project=${JSON.stringify(running)};project.config=boot.defaults;step=PAGE.research;render();`);
  assert.ok(jobBarView(app).includes('Aufrufe 12 von 750 · Kosten 3,20 USD von 20,00 USD'));
  // A subscription run shows no money at all.
  app.run(`delete project.job.progress.cost_spent_usd;delete project.job.progress.cost_limit_usd;lastJobView='';renderJob();`);
  assert.ok(!jobBarView(app).includes('USD'));
  const stopped=code=>`project.job.status='blocked';project.job.run={run_id:'run_m',kind:'research',stages:{dossier:{status:'blocked',error:{code:'${code}',message:'x'}}}};lastJobView='';renderJob();`;
  app.run(`project.job.progress.cost_spent_usd=20.4;project.job.progress.cost_limit_usd=20;`+stopped('cost_limit_reached'));
  let html=jobView(app);
  assert.ok(html.includes('Kostengrenze erreicht'));
  // At least half again the old limit: max(20 × 1.5, 20.4 × 1.25) = 30.
  assert.ok(html.includes('id="stop-cost" type="number" inputmode="decimal" min="20" step="1" value="30"'));
  assert.ok(html.includes('data-action="approve-cost" data-run-id="run_m" data-then-resume="1"'));
  assert.ok(html.includes('Kostengrenze erhöhen und fortsetzen'));
  assert.ok(html.includes('Bisher ausgegeben: 20,40 USD von 20,00 USD'));
  app.run(`project.job.progress.cost_spent_usd=0;delete project.job.progress.cost_limit_usd;`+stopped('cost_limit_required'));
  html=jobView(app);
  assert.ok(html.includes('Kostengrenze fehlt') && html.includes('Kostengrenze festlegen und fortsetzen') && html.includes('value="10"'));
  app.run(stopped('anthropic_key_required'));
  assert.ok(jobView(app).includes('data-key-field="stop-anthropic-key" data-key-kind="anthropic"'));
  // The plan approval names what the plan is expected to cost under the limit.
  assert.ok(app.run(`planSummary({tasks:8,projected_calls:120,projected_hours:4,seconds_per_call:120,cost:{expected_total_usd:44.4,limit_usd:60}})`)
    .endsWith('etwa 44,40 USD bei einer Grenze von 60,00 USD'));
});

test('the settings page holds the Anthropic key and a money limit, saved only when given',()=>{
  const app=studio();
  const view={settings:{text:{provider:'claude_api',model:'claude-sonnet-5-5',reasoning_effort:'high',max_output_tokens:32768},
    audio:{provider:'qwen_local',voices:{host_a:'Aiden',host_b:'Vivian'},pauses:{same_speaker_ms:250,speaker_change_ms:450,chapter_break_ms:900}},
    execution:{text:'sequential',audio:'sequential'},allowances:{fresh_attempts:0,extra_calls:0},
    research_limits:{model_calls:750,sources:150,search_rounds:48,cost_usd:40},text_timeout_seconds:1800},
    hash:'h',global:true,key_available:true,google_key_available:true,anthropic_key_available:false,claude_extra_usage:false};
  app.responses.set('/api/settings',view);
  return app.run('showSettings()').then(()=>{
    const html=app.elements.get('content').innerHTML;
    assert.ok(html.includes('id="key-panel-anthropic"') && html.includes('data-key-kind="anthropic"'));
    assert.ok(html.includes('id="settings-cost" type="number"') && html.includes('value="40"'));
    // What a call cost when measured, next to the money limit (cost_estimate).
    app.run(`boot.text_catalog={billed_providers:['openrouter','claude_api'],claude_models:{'claude-sonnet-5-5':'Claude Sonnet 5.5'},
      usd_per_call:[{kind:'research',model:'claude-sonnet-5-5',usd:0.37}],usd_per_call_measured_on:'2026-10-07'};`);
    assert.ok(app.run('costHint()').includes('Claude Sonnet 5.5 Recherche 0,37 USD'));
    app.run(`$('settings-cost').value='55';`);
    assert.equal(app.run('settingsFromForm().research_limits.cost_usd'),55);
    app.run(`$('settings-cost').value='';`);
    assert.equal(app.run('"cost_usd" in settingsFromForm().research_limits'),false);
    // The web search is a choice of its own; Perplexity has its key panel (D-151).
    assert.ok(html.includes('name="settings-web-search" value="perplexity"') && html.includes('id="key-panel-perplexity"'));
    assert.equal(app.run('settingsFromForm().web_search'),'model');
  });
});

test('the drawer of a billed script run names its money and what the rest is expected to cost',()=>{
  const app=studio();
  const job={id:'j1',status:'running',action:'script',started_at:new Date().toISOString(),run:{run_id:'run_s',kind:'script',stages:{}},
    progress:{phase:'script',model_calls:40,model_call_limit:750,budget_projection:{minimum_remaining_calls:20,feasible:true,
      cost:{spent_usd:33.2,limit_usd:100,expected_remaining_usd:80.5,feasible:false}}}};
  app.run(`project={id:'p',config:boot.defaults,job:${JSON.stringify(job)}};step=PAGE.production;render();`);
  assert.ok(jobView(app).includes('Kosten: 33,20 USD von 100,00 USD · für den Rest erwartet etwa 80,50 USD, mehr als die Grenze'));
});

// The design review of 2026-10-07 (D-159..D-164).
const DOSSIER=['# Recherchedossier: Wissen','','Recherchelauf: `run_20260930_x`','','Assembled from 2 answers.','',
  '## Befunde mit Quellenbezug','','### t01_blocks__f_chunks — mechanism','','Documents are split into chunks. Each chunk gets a vector.','',
  'Aussagetyp: source_definition / description. Geltungsbereich: Standard RAG.','',
  '- [Paper](https://example.org/p), Seite 2 (`src_0123abcd#sec_4567ef01`): „quote“','',
  '### t02_onto__f1 — definition','','An ontology names classes and relations.','',
  '## Abdeckung und Lücken','','- **What are the building blocks?** — answered; Befunde: t01_blocks__f_chunks.','',
  '- **What is an ontology?** — answered; Befunde: t02_onto__f1.','',
  '## Offene Fragen','','- Who maintains it?','','## Quellenverzeichnis','',
  ...Array.from({length:12},(_,i)=>`- **Source ${i}** — Author, 2024. https://example.org/${i} — \`src_${i}abc\`. Metadaten und Extraktionsgrenzen: siehe \`models/source_index.yaml\`.`)].join('\n');

test('a structured dossier opens with its questions, folds its findings under them and hides internal ids',()=>{
  const app=studio();
  // The page's own t() is a global since D-152, so the scratch list takes another name.
  const toc=app.run(`const tocEntries=[];window.__toc=tocEntries;tocEntries`);
  const html=app.run(`renderDossier(${JSON.stringify(DOSSIER)},window.__toc)`);
  assert.ok(html.indexOf('Leitfragen und Abdeckung')<html.indexOf('Befunde nach Leitfrage'),'the questions come before the findings');
  assert.ok(html.indexOf('Offene Fragen')<html.indexOf('Befunde nach Leitfrage'),'a short open-question list stands before the findings');
  assert.ok(html.indexOf('Befunde nach Leitfrage')<html.indexOf('Quellenverzeichnis'),'the long source list folds at the end');
  assert.ok(html.includes('<details class="dossier-section" id="dossier-section-3"><summary>Quellenverzeichnis · 12</summary>'));
  assert.ok(html.includes('<details class="finding-group" id="dossier-group-0"><summary><span>What are the building blocks?</span><span class="tag">1 Befund</span>'));
  assert.ok(html.includes('<span class="finding-kind">Mechanismus</span> Documents are split into chunks.'));
  assert.ok(html.includes('<span class="finding-kind">Definition</span>'));
  for(const internal of ['t01_blocks__f_chunks','src_0123abcd','Aussagetyp','source_index.yaml','— mechanism'])assert.ok(!html.includes(internal),internal);
  assert.ok(html.includes('Geltungsbereich: Standard RAG.'),'the scope stays');
  assert.ok(html.includes('Recherchelauf: run_20260930_x'),'the run id moves to the technical details');
  assert.ok(html.includes('<strong>2</strong> Befunde zu <strong>2</strong> Leitfragen'));
  const entries=JSON.parse(JSON.stringify(app.run('window.__toc')));
  assert.ok(entries.length<10&&entries.some(e=>e.level===2&&e.html.includes('What is an ontology?')),'the contents list names questions, not finding ids');
});

test('older recordings are named once and ask for nothing; an episode without one asks, alike on the card, the steps and the header',()=>{
  const app=studio();
  const ep=(id,extra)=>({...publishedEpisode({}),script:{...publishedEpisode({}).script,episode_id:id,title:id},...extra});
  app.run(`project={id:'p',config:boot.defaults,job:{id:'a',action:'audio',status:'completed',run:{kind:'episode_audio',status:'completed'}},
    audio_jobs:[{id:'a',episode:'ep_001',status:'completed'}],
    episodes:[${JSON.stringify(ep('ep_001',{audio:['exports/a.mp3'],audio_current:false,audio_stale:['pace']}))},${JSON.stringify(ep('ep_002',{audio:['exports/b.mp3'],audio_current:false,audio_stale:['voices','provider']}))}]};step=PAGE.brief;`);
  let states=JSON.parse(JSON.stringify(app.run('navigationStates()')));
  assert.deepEqual(states[4],['2 Folgen lesbar','done']);
  assert.deepEqual(states[5],['Aufnahmen vorhanden · 2 älter','done']);
  app.run('renderJob()');
  assert.ok(app.elements.get('job-bar').innerHTML.includes('Podcast verfügbar · 2 Folgen · 2 von einem früheren Stand'));
  app.run('step=PAGE.audio');
  const page=app.run('renderAudio()');
  assert.ok(page.includes('Alle Aufnahmen stammen von einem früheren Stand (geändert: Sprechtempo, Stimmen, Audioanbieter)'));
  assert.ok(page.includes('Aufnahme eines früheren Stands vorhanden · geändert: Sprechtempo'));
  const card={id:'p',topic:'T',script_count:2,has_research:true,has_outline:true,job:{status:'completed',action:'audio',run:{kind:'episode_audio',status:'completed'}},audio_jobs:[],
    episodes:[{episode_id:'ep_001',audio:['a.mp3'],audio_current:false},{episode_id:'ep_002',audio:['b.mp3'],audio_current:false}]};
  assert.equal(app.run(`attentionOf(${JSON.stringify(card)})`),null);
  assert.deepEqual(JSON.parse(JSON.stringify(app.run(`pipelineStates(${JSON.stringify(card)})`))).slice(4),['done','done']);
  card.script_count=3;card.episodes.push({episode_id:'ep_003',audio:[]});
  assert.equal(app.run(`attentionOf(${JSON.stringify(card)})`).text,'1 Folge hat noch keine Aufnahme.');
  assert.equal(JSON.parse(JSON.stringify(app.run(`pipelineStates(${JSON.stringify(card)})`)))[5],'decision');
  assert.ok(app.run(`overviewStatus(${JSON.stringify(card)})`).includes('1 ohne Aufnahme'));
});

test('polling reads the light status and loads the whole project only when its content or a job changed',async()=>{
  const app=studio(), p=workflowProject(app);
  p.content_version='v1';p.job.status='completed';p.job.run.status='completed';
  app.run(`boot.capabilities={light_status:true};project=${JSON.stringify(p)};lastJobSignature=projectJobSignature(project);`);
  const light={...p,research:undefined,outline:undefined,episodes:undefined,script_previews:undefined};
  app.responses.set('/api/projects/test/status',light);
  app.responses.set('/api/projects/test',{...p,research:'Neues Dossier',content_version:'v2'});
  let next=await app.run(`pollProject('test')`);
  assert.equal(next.research,'Reviewed dossier','an unchanged content keeps what the page has');
  assert.ok(!app.requests.some(r=>r.path==='/api/projects/test'),'and loads nothing more');
  app.responses.set('/api/projects/test/status',{...light,content_version:'v2'});
  next=await app.run(`pollProject('test')`);
  assert.equal(next.research,'Neues Dossier');
  assert.ok(app.requests.some(r=>r.path==='/api/projects/test'));
});

test('a stop that fresh attempts can move makes them the primary button, and the header leads to the card instead of resuming',()=>{
  const app=studio(), p=workflowProject(app);
  Object.assign(p.job,{status:'blocked',fresh_attempts:true,error_code:'script_review_failed',run:{...p.job.run,run_id:'r1',status:'blocked'}});
  app.run(`project=${JSON.stringify(p)};step=PAGE.production;render();`);
  const card=app.elements.get('stop-card').innerHTML;
  const fresh=card.indexOf('<button data-action="fresh-attempts"'), resume=card.indexOf('<button class="secondary" data-action="resume"');
  assert.ok(fresh>-1&&resume>fresh,'fresh attempts first and filled, "Fortsetzen" after them as a secondary button');
  assert.ok(card.includes('<span class="chip paused">Angehalten</span>'),'a stop that can go on is paused, not broken');
  assert.ok(!app.elements.get('job-bar').innerHTML.includes('data-action="resume"'));
  assert.ok(app.elements.get('job-bar').innerHTML.includes('job-dot paused'));
});

test('the settings group the text models by how they are paid, name a missing key and say when edits are unsaved',()=>{
  const app=studio();
  const presets=[{id:'auto',provider:'auto',label:'Automatisch'},{id:'api',provider:'claude_api',model:'claude-sonnet-5-5',label:'Sonnet · API-Key'},{id:'or',provider:'openrouter',model:'x',label:'OpenRouter X'}];
  const settings={text:{provider:'auto',reasoning_effort:null,max_output_tokens:32768},audio:{provider:'qwen3_local',voices:{host_a:'Aiden',host_b:'Vivian'}},
    execution:{text:'sequential',audio:'sequential'},allowances:{fresh_attempts:0,extra_calls:0},research_limits:{model_calls:100,sources:10,search_rounds:5},text_timeout_seconds:1800};
  app.run(`boot.text_catalog={presets:${JSON.stringify(presets)}};settingsPage=true;settingsData={global:true,settings:${JSON.stringify(settings)},key_available:true,anthropic_key_available:false};settingsDraft=structuredClone(settingsData.settings);`);
  const html=app.run('renderSettings()');
  assert.ok(html.includes('<legend>Über deine Abos · keine API-Kosten</legend>'));
  assert.ok(html.includes('Über deinen Anthropic-API-Key · pro Aufruf bezahlt <span class="chip decision">Anthropic-Key fehlt</span>'));
  assert.ok(html.includes('<legend>Über OpenRouter · pro Aufruf bezahlt</legend>'),'a stored key is not flagged');
  assert.ok(html.includes('data-scroll="settings-section-limits"')&&html.includes('id="settings-section-limits"'));
  assert.ok(html.includes('<label class="sr-only" for="settings-pair-language">')||!html.includes('id="settings-pair-language"'));
  assert.ok(html.includes('Alles gespeichert'));
  app.run(`markSettingsDirty({id:'api-key'})`);
  assert.equal(app.run('settingsDirty'),false,'typing a key is no settings edit');
  app.run(`markSettingsDirty({id:'settings-calls'})`);
  assert.equal(app.run('settingsDirty'),true);
  assert.ok(app.run('renderSettings()').includes('Ungespeicherte Änderungen'));
  app.run('window.confirm=()=>false');
  assert.equal(app.run('leaveSettings()'),false,'leaving with unsaved edits asks first');
  app.run('window.confirm=()=>true');
  assert.equal(app.run('leaveSettings()'),true);
  assert.ok(app.run('sidebarProjects()').includes('Neues Projekt'),'the sidebar lists projects instead of a new project\'s steps');
});

test('the reader names each speaker by voice, steps between episodes and keeps read marks in the project',()=>{
  const app=studio(), p=workflowProject(app);
  p.job.status='completed';p.job.run.status='completed';
  const two={...publishedEpisode({}),hash:'h2',script:{...publishedEpisode({}).script,episode_id:'ep_002',title:'Second'}};
  p.episodes=[publishedEpisode({}),two];p.reader_state={read:{ep_001:'final'}};
  p.audio_settings={provider:'google_gemini_tts',voices:{host_a:'Erinome',host_b:'Sadachbia'},alternate_roles:true};
  app.run(`project=${JSON.stringify(p)};step=PAGE.scripts;scriptEpisodeId='ep_002';render();`);
  const html=app.elements.get('content').innerHTML;
  assert.ok(html.includes('<strong>Sadachbia · Host A</strong>'),'roles swap in an even episode');
  assert.ok(html.includes('data-reader-episode="ep_001"')&&html.includes('‹ Vorherige'));
  assert.ok(html.includes('Folge 2 von 2 · 1 gelesen'));
  assert.ok(html.includes('1. ep_001 Dialogue · ✓ gelesen')&&!html.includes('· Fertig zur Durchsicht'),'a published state is not repeated on every option');
  assert.ok(html.indexOf('Weiter zur Audio-Freigabe')<html.indexOf('Wörter'),'the decision stands first in the margin');
  app.run(`project.reader_state={read:{ep_001:'older'}}`);
  assert.ok(!app.run('renderReaderControls()').includes('✓ gelesen'),'a revised script reads as unread again');
});

test('the engine room shows only what helps: a finished side job closes it, a stopped run folds its last lines',()=>{
  const app=studio();
  app.run(`project={id:'p',config:boot.defaults,job:{id:'k',action:'publish_kit',status:'completed',progress:{completed_segments:1,total_segments:1}},episodes:[]};step=PAGE.audio;renderJob();`);
  assert.equal(app.elements.get('job-status').hidden,true,'nothing to show, no dock');
  assert.ok(!app.elements.get('job-bar').innerHTML.includes('Arbeitsschritt abgeschlossen'));
  app.run(`project.job={id:'r',action:'resume',status:'interrupted',run:{run_id:'r1',kind:'research',stages:{}},progress:{phase:'research',
    status_summary:{status:'summarizing',summary:'Zuletzt wurde geprüft.',generated_at:new Date(Date.now()-3*86400000).toISOString(),model:'small-model'},
    model_trace:{updated_at:new Date(Date.now()-5*86400000).toISOString(),lines:[{at:new Date().toISOString(),call:'c1',kind:'text',text:'Eins'},{at:new Date().toISOString(),call:'c1',kind:'text',text:'Zwei'}]},
    call_labels:{c1:'Eine lange Teilfrage, deren Titel weit über achtundvierzig Zeichen hinausreicht'}}};lastJobView='';drawerOpen=true;renderJob();`);
  const drawer=app.elements.get('job-status').innerHTML;
  assert.ok(drawer.indexOf('Kurzbericht')<drawer.indexOf('Letzte Arbeitsschritte'),'the brief comes first');
  assert.ok(!drawer.includes('wird gerade erstellt'),'a stopped run\'s brief is not being written');
  assert.ok(drawer.includes('vor 3 Tagen')&&drawer.includes('vor 5 Tagen'));
  assert.ok(!drawer.includes('small-model</span>'),'the brief\'s model only as a tooltip');
  assert.ok(drawer.includes('<details class="trace-archive"><summary>Letzte Arbeitsschritte'));
  assert.equal((drawer.match(/<small>/g)||[]).length,1,'lines of one call at one moment share one head');
  assert.ok(!drawer.includes('Haltegrund')&&!drawer.includes('Textmodus:'));
  assert.ok(!app.elements.get('job-bar').innerHTML.includes('drawer-toggle'));
});

test('the production report breaks a research run down by its steps in German numbers',()=>{
  const app=studio();
  const job={status:'completed',run:{run_id:'run_r',kind:'research'}};
  const report={calls:30,model_minutes:90,stages:[{stage:'question_research',label:'question_research',calls:29,failed:1,minutes:87,share:0.97,minutes_per_call:3},
    {stage:'research_discovery',label:'research_discovery',calls:1,failed:0,minutes:3,share:0.03,minutes_per_call:3}],
    versions:[{version:'question_research.v3.view.reader',calls:20,minutes:30},{version:'question_research.v3.search',calls:5,minutes:45},
      {version:'question_research.v3.review_002',calls:4,minutes:12}],providers:{},stops:{total:0,by_stage:{}},approvals:{}};
  app.run(`productionReports.run_r=${JSON.stringify({report})}`);
  const html=app.run(`renderProductionReport(${JSON.stringify(job)})`);
  assert.ok(html.includes('<td>Websuche</td><td>5</td><td>45 Min.</td>'));
  assert.ok(html.includes('<td>Quellen lesen</td><td>20</td>'));
  assert.ok(html.includes('<td>Antworten prüfen</td>'));
  assert.ok(!html.includes('<td>question_research</td>'),'the one stage is broken down');
  assert.ok(html.includes('1,5 Std. Modellzeit'));
});

test('a settled brief leads with the saved brief, folds the conversation and renders the partner\'s Markdown',()=>{
  const app=studio(), p=workflowProject(app);
  p.job.status='completed';p.job.run.status='completed';p.proposal_applied=true;
  p.chat=[{role:'user',message:'**nicht fett**'},{role:'assistant',message:'**Ausführung**\n- parallel'}];
  app.run(`boot.capabilities={conversational_setup:true,project_attachments:true};project=${JSON.stringify(p)};briefChatOpen=false;`);
  let html=app.run('renderBrief()');
  assert.ok(html.indexOf('Dein gespeicherter Auftrag')<html.indexOf('chat-archive'),'the saved brief leads');
  assert.ok(html.includes('<details class="panel chat-archive" id="chat-archive"><summary>Gespräch mit der Redaktion · 2 Nachrichten</summary>'));
  assert.ok(html.includes('<strong>Ausführung</strong>')&&html.includes('<li><p>parallel</p></li>'));
  assert.ok(html.includes('<p>**nicht fett**</p>'),'the editor\'s own words stay plain');
  assert.ok(html.includes('<details class="composer-menu"><summary>'),'saved attachments do not open the attachment panel');
  app.run(`project.chat.push({role:'user',message:'Noch etwas'})`);
  assert.ok(app.run('renderBrief()').includes('id="chat-archive" open'),'an unanswered message keeps the conversation open');
});

test('the key reminder is one folded line that names every missing key',()=>{
  const app=studio();
  app.run(`overviewPage=true;overviewData={projects:[],trash:[],key_reminder:[{need:'google_audio',projects:['A'],key:'google'},{need:'jev',projects:['B']}]};renderKeyNote();`);
  const html=app.elements.get('key-note').innerHTML;
  assert.ok(html.startsWith('<details class="key-details"><summary><strong>2 Keys fehlen</strong> · Google-Key (Gemini-Vertonung über Google) · OpenRouter-Key (Jev in der Lückenprobe)</summary>'));
  assert.ok(!html.includes('<details class="key-details" open'),'folded by default');
  assert.equal((html.match(/Der Key bleibt nur im Speicher/g)||[]).length,0,'one note for all keys, not one per field');
});

// The interface in German or English (D-152). The catalogs agree in keys, plural shapes and placeholders; every key the
// page names exists; an English page shows no German text of its own; a German page keeps its exact wording.
const PLACEHOLDER=/\{(\w+)\}/g;
const catalogForms=value=>typeof value==='string'?[value]:Object.values(value);
const placeholdersOf=value=>[...new Set(catalogForms(value).flatMap(text=>[...text.matchAll(PLACEHOLDER)].map(m=>m[1])))].sort();
// German an English page must not show: umlauts or German quotation marks, two German function words in one text, or
// a German name of a control. The language choice names each language in its own words and stays as it is.
const GERMAN_MARK=/[äöüÄÖÜß„]/;
const GERMAN_WORDS=/\b(?:und|oder|nicht|der|die|das|dem|des|ein|eine|einen|ist|sind|wird|werden|mit|für|auf|noch|zur|zum|bitte|keine|kein|sobald|danach|wurde|sonst)\b/g;
const GERMAN_NAMES=/\b(?:Fortsetzen|Einstellungen|Teilfragen?|Skripte|Auftrag|Folgen?|Recherche|Vertonung|Stimmen|Lauf|Prüfung)\b/;
const BILINGUAL=new Set(['Deutsch','Sprache / Language','Automatisch / Automatic']);
function germanLeft(html) {
  const visible=String(html).replace(/<(script|style|textarea)[^>]*>[\s\S]*?<\/\1>/g,' ');
  const texts=[...visible.matchAll(/\s(?:aria-label|title|placeholder|data-confirm|alt)="([^"]*)"/g)].map(m=>m[1]);
  texts.push(...visible.replace(/<[^>]*>/g,'\n').split('\n').map(text=>text.trim()).filter(Boolean));
  return texts.filter(text=>!BILINGUAL.has(text)&&(GERMAN_MARK.test(text)||(text.match(GERMAN_WORDS)||[]).length>=2||GERMAN_NAMES.test(text)));
}
function watchWarnings(app) {
  const warnings=[];
  app.context.console={...console,warn:(...args)=>warnings.push(args.join(' '))};
  return warnings;
}

test('the German and English catalogs have the same keys, plural shapes and placeholders and carry no markup',()=>{
  assert.deepEqual(Object.keys(locales.en).sort(),Object.keys(locales.de).sort());
  for(const [key,de] of Object.entries(locales.de)){
    const en=locales.en[key];
    assert.equal(typeof en,typeof de,`shape of ${key}`);
    if(typeof de==='object')assert.deepEqual([Object.keys(de).sort(),Object.keys(en).sort()],[['one','other'],['one','other']],key);
    assert.deepEqual(placeholdersOf(en),placeholdersOf(de),`placeholders of ${key}`);
    for(const text of [...catalogForms(de),...catalogForms(en)]){
      assert.ok(text.trim(),`empty text in ${key}`);
      assert.ok(!/[<>"]|&[#A-Za-z]/.test(text),`markup in ${key}: ${text}`);
    }
  }
});

test('every catalog key the page script and its static HTML name exists',()=>{
  const html=fs.readFileSync('src/podcast_automate/web/index.html','utf8');
  const keys=new Set([
    ...[...source.matchAll(/\b(?:t|tp|hasText|say)\("([a-z][\w.-]*)"/g)].map(m=>m[1]),
    ...[...source.matchAll(/\brule\("([a-z_]+)"/g)].map(m=>`stop.${m[1]}.title`),
    ...[...html.matchAll(/data-i18n(?:-label|-content)?="([^"]+)"/g)].map(m=>m[1])]);
  assert.ok(keys.size>700,`only ${keys.size} keys found; the pattern no longer matches the page script`);
  assert.deepEqual([...keys].filter(key=>!(key in locales.en)),[]);
});

test('the static page text follows the interface language and the German catalog keeps the file\'s wording',()=>{
  const html=fs.readFileSync('src/podcast_automate/web/index.html','utf8');
  // Every tag with a catalog key, with its attributes and the text up to its next tag.
  const tags=[...html.matchAll(/<(\w+)([^>]*\sdata-i18n[^>]*)>([^<]*)/g)].map(([,tag,attributes,text])=>{
    const attribute=name=>attributes.match(new RegExp(`\\s${name}="([^"]*)"`))?.[1];
    return {tag,text,key:attribute('data-i18n'),labelKey:attribute('data-i18n-label'),label:attribute('aria-label'),
      contentKey:attribute('data-i18n-content'),content:attribute('content')};
  });
  assert.ok(tags.length>=15,`${tags.length} tags`);
  for(const tag of tags){
    if(tag.key)assert.equal(locales.de[tag.key],tag.text,tag.key);
    if(tag.labelKey)assert.equal(locales.de[tag.labelKey],tag.label,tag.labelKey);
    if(tag.contentKey)assert.equal(locales.de[tag.contentKey],tag.content,tag.contentKey);
  }
  assert.ok(html.indexOf('<script src="/locale.js" defer>')>-1&&html.indexOf('/locale.js')<html.indexOf('/app.js'),'the catalog loads before the page script');
  for(const [language,setting] of [['en','auto'],['de','de']]){
    const app=studio({language,setting});
    const nodes=tags.map(tag=>({dataset:{...(tag.key?{i18n:tag.key}:{}),...(tag.labelKey?{i18nLabel:tag.labelKey}:{}),...(tag.contentKey?{i18nContent:tag.contentKey}:{})},
      textContent:tag.text,attributes:{},setAttribute(name,value){this.attributes[name]=value;}}));
    const select={value:'',dataset:{}}, root={lang:'de'};
    app.context.document.documentElement=root;
    app.context.document.querySelectorAll=selector=>selector==='select[data-ui-language]'?[select]:
      nodes.filter(node=>({'[data-i18n]':'i18n','[data-i18n-label]':'i18nLabel','[data-i18n-content]':'i18nContent'})[selector] in node.dataset);
    app.run('localizePage()');
    assert.equal(root.lang,language);
    assert.equal(select.value,setting,'the sidebar choice shows the saved setting');
    for(const node of nodes){
      if(node.dataset.i18n)assert.equal(node.textContent,locales[language][node.dataset.i18n]);
      if(node.dataset.i18nLabel)assert.equal(node.attributes['aria-label'],locales[language][node.dataset.i18nLabel]);
    }
    if(language==='en'){
      assert.ok(nodes.some(node=>node.textContent==='Settings'),'the English settings link says "Settings"');
      assert.deepEqual(germanLeft(nodes.map(node=>`<p>${node.textContent}</p><p>${node.attributes['aria-label']||''}</p>`).join('')),[]);
    }
  }
});

test('an English page shows every step, the navigation, the settings and the overview without German text',()=>{
  const app=studio({language:'en'}), warnings=watchWarnings(app), p=workflowProject(app);
  p.job.status='completed';p.job.run.status='completed';
  p.episodes=[{...publishedEpisode({script_review:['A model review can miss errors.']}),audio:['exports/ep_001/run/audio.mp3'],audio_current:true,audio_seconds:1500}];
  app.run(`boot.capabilities={conversational_setup:true,project_attachments:true,project_overview:true,podcast_downloads:true};project=${JSON.stringify(p)};episodeIndex=0;readingSnapshot=null;`);
  for(const [name,code] of Object.entries({brief:'renderBrief()',research:'renderResearch()',outline:'renderOutline()',
    production:'renderProduction()',scripts:'renderScript()',audio:'renderAudio()'}))assert.deepEqual(germanLeft(app.run(code)),[],name);
  app.run('step=PAGE.audio;renderNavigation();');
  assert.deepEqual(germanLeft(app.elements.get('steps').innerHTML),[],'navigation');
  app.run(`project.job={id:'j2',action:'resume',status:'running',started_at:new Date().toISOString(),progress:{phase:'script',model_calls:3,model_call_limit:40},
    run:{kind:'script',run_id:'r2',status:'running',stages:{planning:{status:'completed'},teaching:{status:'running'}}}};lastJobView='';drawerOpen=true;renderJob();`);
  assert.deepEqual(germanLeft(jobView(app)),[],'job bar and engine room');
  const presets=[{id:'auto_subscriptions',provider:'auto',label:'Automatisch über die Abos'},{id:'claude_sonnet_api',provider:'claude_api',model:'claude-sonnet-5-5',label:'Sonnet über den API-Key'}];
  const settings={text:{provider:'auto',reasoning_effort:null,max_output_tokens:32768},audio:{provider:'qwen3_local',voices:{host_a:'Aiden',host_b:'Vivian'}},
    execution:{text:'sequential',audio:'sequential'},allowances:{fresh_attempts:0,extra_calls:0},research_limits:{model_calls:100,sources:10,search_rounds:5},text_timeout_seconds:1800};
  app.run(`boot.text_catalog={presets:${JSON.stringify(presets)}};settingsPage=true;settingsData={global:true,settings:${JSON.stringify(settings)},key_available:true,anthropic_key_available:false};settingsDraft=structuredClone(settingsData.settings);`);
  const page=app.run('renderSettings()');
  assert.deepEqual(germanLeft(page),[],'settings');
  assert.ok(page.includes('<h1>Settings</h1>'),'the chat names the settings page "Settings"');
  assert.ok(page.includes('id="ui-language-settings" data-ui-language'));
  app.run('renderNavigation()');
  assert.equal(app.elements.get('project-title').textContent,'Settings');
  app.run(`settingsPage=false;overviewPage=true;overviewData={projects:[{id:'test',topic:'Topic one',job:{status:'running',action:'audio'},
    episodes:[{episode_id:'ep_001',title:'First',audio:['exports/ep_001/one.mp3'],audio_current:true}]}],trash:[]};`);
  assert.deepEqual(germanLeft(app.run('renderOverview()')),[],'overview');
  assert.deepEqual(warnings,[],'no key is missing from the catalog');
});

test('every stop card reads in English for every run kind, a chat and a recording',()=>{
  const app=studio({language:'en'}), warnings=watchWarnings(app), p=workflowProject(app);
  app.run(`project=${JSON.stringify(p)};`);
  const codes=app.run('Object.keys(STOP_RULES)');
  assert.ok(codes.length>60,`${codes.length} stop rules`);
  for(const code of codes)for(const kind of ['research','script','audio',null]){
    const job={id:'j',status:['interrupted','failed'].includes(code)?code:'blocked',action:kind==='audio'?'audio':kind?'resume':'assistant',
      error_code:code,run:kind?{kind,run_id:'r1',status:'blocked',stages:{}}:null,progress:{}};
    const html=app.run(`(job=>{const info=stopInfo(job);return info?renderStopCard(job,info):""})(${JSON.stringify(job)})`);
    assert.deepEqual(germanLeft(html),[],`${code} · ${kind}`);
    assert.ok(!html.includes('{resume}'),`${code} · ${kind}`);
  }
  assert.deepEqual(warnings,[]);
});

test('stop texts name the resume button of the page language in its quotation marks',()=>{
  for(const [language,verb,resend] of [['de','„Fortsetzen“','„Erneut senden“'],['en','“Resume”','“Send again”']]){
    const app=studio({language});
    const info=app.run(`stopInfo({status:'blocked',action:'resume',error_code:'timeout',run:{kind:'script',run_id:'r1',stages:{}},progress:{}})`);
    assert.ok(info.text.includes(verb),info.text);
    const chat=app.run(`stopInfo({status:'failed',action:'assistant',error_code:'timeout',progress:{}})`);
    assert.ok(chat.text.includes(resend),chat.text);
  }
});

test('an English page shows a German server message only in the technical details, an English one in the card',()=>{
  const app=studio({language:'en'}), p=workflowProject(app);
  app.run(`project=${JSON.stringify(p)};`);
  const card=message_language=>app.run(`(job=>renderStopCard(job,stopInfo(job)))(${JSON.stringify({id:'j',status:'blocked',action:'resume',error_code:'timeout',
    stop:{code:'timeout',message:message_language==='de'?'Der Aufruf hat zu lange gedauert.':'The call took too long.',message_language},
    run:{kind:'script',run_id:'r1',stages:{}},progress:{}})})`);
  const german=card('de'), [main,details]=german.split('<details class="tech-details">');
  assert.ok(!main.includes('Der Aufruf hat zu lange gedauert.'));
  assert.ok(details.includes('Der Aufruf hat zu lange gedauert.'),'the original stays reachable');
  assert.ok(card('en').split('<details class="tech-details">')[0].includes('The call took too long.'));
  assert.equal(app.run(`errorText({message:'Projekt nicht gefunden.',language:'de',code:'not_found'})`),'Refused (not_found). Original message (German): Projekt nicht gefunden.');
  assert.equal(studio().run(`errorText({message:'Projekt nicht gefunden.',language:'de',code:'not_found'})`),'Projekt nicht gefunden.');
});

test('a budget stop and a blocked question read their limit from the server fields before the German wording',()=>{
  const app=studio({language:'en'}), p=workflowProject(app);
  app.run(`project=${JSON.stringify(p)};`);
  const actions=(stop,message)=>[...app.run(`stopInfo(${JSON.stringify({status:'blocked',action:'resume',error_code:'research_budget_exhausted',
    stop:{code:'research_budget_exhausted',...stop},message,run:{kind:'research',run_id:'r1',stages:{}},progress:{}})}).actions`)];
  assert.deepEqual(actions({limit:'search_rounds'},''),['approve_search']);
  assert.deepEqual(actions({limit:'model_calls'},'Die Suchrunden sind aufgebraucht.'),['approve_calls'],'the field wins over the wording');
  assert.deepEqual(actions({},'Die Suchrunden sind aufgebraucht.'),['approve_search'],'older stops keep the wording fallback');
  const gap=row=>app.run(`gapActionsFor(${JSON.stringify({id:'q1',outcome:'budget_block',...row})},'r1',5)`).includes('data-action="approve-search"');
  assert.equal(gap({block_cause:'search_budget',reason:'Search budget used up.'}),true);
  assert.equal(gap({block_cause:'source_limit',reason:'Das Suchbudget reicht nicht.'}),false);
  assert.equal(gap({reason:'Das Suchbudget reicht nicht.'}),true,'older rows keep the wording fallback');
});

test('an English research run shows its plan review, decisions, disputes and recordings without German text',()=>{
  const app=studio({language:'en'}), warnings=watchWarnings(app);
  const projection={tasks:12,tasks_pending:12,expected_calls_per_task:5,expected_calls_source:'project',closing_reserve:4,closing_calls:3,
    projected_calls:64,used:7,approved_limit:150,within_limit:true,seconds_per_call:270,seconds_per_call_source:'run',projected_hours:4.8,plan_hash:'h',plan_caps:[]};
  const base=`project={id:'p',config:boot.defaults,audio_jobs:[],job:{id:'j1',status:'blocked',action:'research',started_at:new Date().toISOString(),run:{run_id:'run_x',kind:'research',stages:{}},progress:`;
  const show=progress=>{app.run(`${base}${JSON.stringify(progress)}}};overviewPage=false;step=PAGE.research;lastJobView='';drawerOpen=true;render();`);return jobView(app)+app.elements.get('content').innerHTML;};
  let html=show({phase:'research',research_questions:{closed:0,total:12,accepted:0,phase:'awaiting_plan_approval',questions:[]},
    plan_review:{awaiting:true,approved:false,approval:null,projection},model_call_limit:150,model_calls:7});
  assert.ok(html.includes('data-action="approve-plan"'));
  assert.deepEqual(germanLeft(html),[],'plan review');
  html=show({phase:'research',search_round_limit:5,research_questions:{closed:1,total:3,accepted:0,phase:'blocked',questions:[
    {id:'q1',question:'Definition',status:'verified',steps:2,read_sections:4,acceptance:['Describe the scope'],answer:'**Answer**',limits:['Limited scope'],findings:[]},
    {id:'q2',question:'Independent test?',status:'blocked',outcome:'evidence_block',reason:'Missing original study',steps:3,read_sections:5,acceptance:['Find the test']},
    {id:'q3',question:'Later sources?',status:'blocked',outcome:'budget_block',block_cause:'search_budget',reason:'Search budget used up',steps:1,read_sections:0,acceptance:['Find a source']}]},
    research_quality:{closed:1,total:3,requirements:[],blocking_gaps:[]},model_call_limit:150,model_calls:40});
  assert.ok(html.includes('data-action="accept-gap"')&&html.includes('data-action="approve-search"'));
  assert.deepEqual(germanLeft(html),[],'decisions');
  const dispute=id=>({objection_id:id,task_id:'t',question:`Question ${id}`,decision:null,
    objection:{reason:`Objection ${id}`,correction:'Remove it.',closure_condition:'Cited'},review:{reason:`Review ${id}`,references:[]}});
  app.run(`${base}${JSON.stringify({phase:'research',research_questions:{closed:3,total:3,accepted:0,phase:'audit',audit_round:1,questions:[]},
    review_disagreements:[dispute('a'),dispute('b')],search_round_limit:40})},stop:{code:'review_disagreement'}}};step=PAGE.research;lastJobView='';render();`);
  html=jobView(app);
  assert.ok(html.includes('data-objection-id="a"'));
  assert.deepEqual(germanLeft(html),[],'disputes');
  app.run(`project.job={id:'a1',action:'audio',episode:'ep_001',status:'running',progress:{phase:'audio',completed_segments:2,total_segments:8}};
    project.audio_jobs=[project.job,{id:'a2',action:'audio',episode:'ep_002',status:'blocked',error_code:'speech_failed',run:{run_id:'run_two',kind:'audio',stages:{}},progress:{}}];
    project.audio_capacity={available:2};step=PAGE.audio;lastJobView='';renderJob();`);
  assert.deepEqual(germanLeft(jobView(app)),[],'recordings');
  assert.deepEqual(warnings,[]);
});

test('the language choice saves the setting, reloads the page and is no settings edit',async()=>{
  const app=studio({language:'en',setting:'auto'});
  let reloads=0;
  app.context.window.location={reload(){reloads++;}};
  app.run(`settingsPage=true;settingsData={global:true,settings:{}};`);
  app.run(`markSettingsDirty({id:'ui-language-settings'})`);
  assert.equal(app.run('settingsDirty'),false);
  await app.run('chooseLanguage("auto")');
  await app.run('chooseLanguage("fr")');
  assert.equal(app.requests.filter(r=>r.path==='/api/ui-language').length,0,'the current or an unknown choice sends nothing');
  await app.run('chooseLanguage("de")');
  const request=app.requests.find(r=>r.path==='/api/ui-language');
  assert.deepEqual(JSON.parse(request.options.body),{ui_language:'de'});
  assert.equal(request.options.headers['X-Studio-Token'],'csrf');
  assert.equal(reloads,1);
  assert.ok(app.run('languageSelect("x")').includes('<option value="auto" selected>Automatisch / Automatic</option>'));
});

test('dates, numbers and plurals follow the page language',()=>{
  const de=studio(), en=studio({language:'en'});
  assert.equal(de.run('usd(1234.5)'),'1.234,50 USD');
  assert.equal(en.run('usd(1234.5)'),'1,234.50 USD');
  assert.equal(de.run('quoted("x")'),'„x“');
  assert.equal(en.run('quoted("x")'),'“x”');
  assert.equal(en.run('tp("job.decisions",{count:1})'),'1 sub-question awaits your decision');
  assert.equal(en.run('tp("job.decisions",{count:2})'),'2 sub-questions await your decision');
  assert.equal(de.run('tp("job.decisions",{count:1})'),'1 Teilfrage wartet auf deine Entscheidung');
  assert.equal(de.run('tp("job.decisions",{count:3})'),'3 Teilfragen warten auf deine Entscheidung');
  assert.equal(en.run('t("act.studio_reports",{message:"<b>"})'),'The Studio reports: &lt;b&gt;','parameters are escaped in markup');
  const warnings=watchWarnings(en);
  assert.equal(en.run('tp("missing.key.for.test")'),'missing.key.for.test','a missing text shows its key');
  assert.deepEqual(warnings,['Missing Studio text: missing.key.for.test']);
});

// D-155, D-157, D-153 (2026-10-07): the plan card, the stop and decision cards in one plain sentence each, the trial
// option, the server's download names and the advisories in the page language.
const PLAN={tasks:12,tasks_pending:12,expected_calls_per_task:16,expected_calls_source:'default',closing_reserve:4,closing_calls:3,
  projected_calls:195,used:5,approved_limit:250,within_limit:true,seconds_per_call:240,seconds_per_call_source:'default',projected_hours:13,
  plan_hash:'h',plan_caps:[],sources_used:20,sources_limit:150,sources_per_task:4.5,projected_sources:74,sources_within_limit:true,
  search_rounds_used:2,search_rounds_limit:48,search_rounds_per_task:1.7,projected_search_rounds:23,search_rounds_within_limit:true,
  search_rates_source:'project',raise_to:null};
const SHORT_PLAN={...PLAN,projected_calls:300,within_limit:false,projected_sources:180,sources_within_limit:false,
  raise_to:{model_calls:336,search_rounds:48,sources:199}};
const planCard=(app,projection)=>app.run(`renderPlanReview({status:'blocked',progress:{plan_review:{awaiting:true,approved:false,projection:${JSON.stringify(projection)}}}},'run_x')`);

test('the plan card says in one sentence what the plan takes and whether it fits the limits',()=>{
  const app=studio(), html=planCard(app,PLAN);
  assert.ok(html.includes('<p>Die Recherche plant 12 Teilfragen: etwa 13 Stunden, 195 Aufrufe, 74 Quellen und 23 Suchrunden; das passt in deine Limits.</p>'));
  // The rates' origin, the sources' and search rounds' included.
  assert.ok(html.includes('4,5 Quellen und 1,7 Suchrunden je Teilfrage (Erfahrungswert des Projekts).'));
  assert.ok(html.includes('<button data-action="approve-plan" data-run-id="run_x" data-then-resume="1" >Plan freigeben und starten</button>'));
  assert.ok(!html.includes('data-raise-')&&!html.includes('data-action="approve-calls"'),'nothing to raise');
  const en=studio({language:'en'}), english=planCard(en,PLAN);
  assert.ok(english.includes('<p>The research plans 12 sub-questions: about 13 hours, 195 calls, 74 sources and 23 search rounds; this fits your limits.</p>'));
  assert.ok(english.includes('>Approve plan and start</button>'));
  assert.deepEqual(germanLeft(english),[]);
});

test('a plan over its limits raises only those that do not fit, then approves the plan and starts, in one click',async()=>{
  const app=studio();
  await new Promise(resolve=>setTimeout(resolve,0)); // the page's own start-up read settles before the project is set
  const html=planCard(app,SHORT_PLAN);
  assert.ok(html.includes('<p>Die Recherche plant 12 Teilfragen: etwa 13 Stunden, 300 Aufrufe, 180 Quellen und 23 Suchrunden; dafür steigen die Limits auf 336 Aufrufe und 199 Quellen.</p>'));
  assert.ok(html.includes('<button data-action="approve-plan" data-run-id="run_x" data-raise-calls="336" data-raise-sources="199" data-then-resume="1" >Plan freigeben und Limits anheben</button>'));
  assert.ok(!html.includes('data-raise-rounds'),'a limit that fits stays as it is');
  assert.ok(!html.includes('data-action="approve-calls"'),'the separate call-limit button gives way to the one click');
  assert.ok(!html.includes('Das Limit reicht dafür voraussichtlich nicht'),'the sentence already says which limits rise');
  assert.ok(html.includes('id="plan-max-tasks"'),'cutting the plan stays the alternative');
  app.run(`project={id:'p',config:boot.defaults,job:{id:'j1',status:'blocked',action:'research',run:{run_id:'run_x',kind:'research',stages:{}},progress:{plan_review:{awaiting:true,approved:false,projection:${JSON.stringify(SHORT_PLAN)}}}}};`);
  app.responses.set('/api/projects/p',app.run('structuredClone(project)'));
  const button="{dataset:{runId:'run_x',raiseCalls:'336',raiseSources:'199',thenResume:'1'}}";
  assert.equal(await app.run(`approvePlan(${button}).then(result=>result.raised)`),true);
  const posts=()=>app.requests.filter(r=>r.options?.method==='POST').map(r=>[r.path,JSON.parse(r.options.body)]);
  assert.deepEqual(posts(),[['/api/projects/p/approve',{kind:'model_calls',run_id:'run_x',model_calls:336,sources:199}],
    ['/api/projects/p/approve',{kind:'plan',run_id:'run_x'}],['/api/projects/p/start',{action:'resume',run_id:'run_x'}]]);
  // A cap asks for a shortened plan first, which names its own limits: nothing is raised for it.
  app.requests.length=0;app.elements.get('plan-max-tasks').value='6';
  await app.run(`approvePlan(${button})`);
  assert.deepEqual(posts(),[['/api/projects/p/approve',{kind:'plan',run_id:'run_x',max_tasks:6}],['/api/projects/p/start',{action:'resume',run_id:'run_x'}]]);
  const en=studio({language:'en'}), english=planCard(en,SHORT_PLAN);
  assert.ok(english.includes('for this the limits rise to 336 calls and 199 sources.</p>')&&english.includes('>Approve plan and raise limits</button>'));
  assert.deepEqual(germanLeft(english),[]);
});

test('a script correction loop that asks anew offers fresh attempts in one sentence, without a new table of contents',()=>{
  const card=(app,code,extra={})=>{
    const job={id:'j',status:'blocked',action:'resume',stop:{code},run:{run_id:'run_s',kind:'script',stages:{}},progress:{phase:'script'},fresh_attempts:true,...extra};
    return app.run(`renderStopCard(${JSON.stringify(job)},stopInfo(${JSON.stringify(job)}))`);
  };
  const app=studio();
  app.run(`project={id:'p',config:boot.defaults,research:'dossier'};`);
  let html=card(app,'invalid_script');
  assert.ok(html.includes('<p>Das Skript dieser Folge verletzt nach drei Korrekturen noch Länge oder Quellenzuordnung. „Fortsetzen“ schreibt die Folge mit neuen Versuchen neu; alles andere bleibt gespeichert.</p>'));
  assert.ok(html.includes('<button data-action="fresh-attempts" data-run-id="run_s" data-then-resume="1" >Mit neuen Anläufen fortsetzen</button>'));
  assert.ok(html.includes('<button class="secondary" data-action="resume"'),'plain resume stays beside it');
  assert.ok(!html.includes('data-action="plan"'),'no pointer to a new table of contents');
  for(const code of ['rejected_output','invalid_script_evidence_review','invalid_teaching_review','invalid_teaching_repair','invalid_polish_evidence',
    'invalid_series_review','invalid_supplement','invalid_evidence_review','invalid_model_output']){
    html=card(app,code);
    assert.ok(html.includes('<p>Die Prüfung hat ihre Antworten dreimal als formal fehlerhaft abgewiesen. „Fortsetzen“ fragt diesen Schritt neu an; keine abgewiesene Antwort wird wiederholt.</p>'),code);
    assert.ok(html.includes('data-action="fresh-attempts"')&&!html.includes('data-action="plan"'),code);
  }
  // Without fresh attempts to give, the earlier card and its way out stay.
  html=card(app,'invalid_script',{fresh_attempts:false});
  assert.ok(html.includes('Korrekturversuche dieses Schritts')&&html.includes('data-action="plan"'));
  // An unreadable answer is a transient stop while the Studio resumes it by itself.
  html=card(app,'invalid_model_output',{auto_resume_kind:'transient',auto_resume_at:new Date(Date.now()+600000).toISOString()});
  assert.ok(html.includes('Unlesbare Modellantwort')&&html.includes('vorübergehender technischer Fehler'));
  assert.ok(!html.includes('dreimal als formal fehlerhaft'));
  const en=studio({language:'en'});
  en.run(`project={id:'p',config:boot.defaults,research:'dossier'};`);
  html=card(en,'invalid_script');
  assert.ok(html.includes('script still breaks its length or source mapping. “Resume” writes the episode anew with fresh attempts; everything else stays saved.'));
  assert.ok(html.includes('>Resume with fresh attempts</button>'));
  assert.ok(card(en,'rejected_output').includes('The check rejected its answers three times as malformed. “Resume” asks this step anew; no rejected answer is replayed.'));
  assert.deepEqual(germanLeft(html),[]);
});

test('a search without an observed web search, a stop newer code may pass and an expired login say what the Studio does itself',()=>{
  for(const [language,search,update,login] of [
    ['de','Ein Rechercheaufruf hat auch im zweiten Versuch keine beobachtbare Websuche ausgeführt. Das Studio setzt nach einer Pause selbst fort.',
      'Das Studio hat seit diesem Halt neuen Code; es setzt den Lauf einmal von selbst fort.',
      'Das Studio prüft die Anmeldung alle 5 Minuten, solange es geöffnet ist, und setzt fort, sobald sie gilt.'],
    ['en','A research call ran no observable web search, twice. The Studio continues by itself after a pause.',
      'The Studio has new code since this stop; it continues the run once by itself.',
      'While the Studio is open it checks the login every 5 minutes and continues as soon as it is valid.']]){
    const app=studio({language});
    const stop=(code,kind='research',extra={})=>({id:'j',status:'blocked',action:'resume',stop:{code},run:{run_id:'r',kind,stages:{}},progress:{},...extra});
    assert.equal(app.run(`stopInfo(${JSON.stringify(stop('search_not_observed'))}).text`),search);
    const updated=stop('processing_failed','research',{auto_resume_kind:'code_update',auto_resume_at:'2026-10-07T08:00:00+00:00'});
    assert.ok(app.run(`renderStopCard(${JSON.stringify(updated)},stopInfo(${JSON.stringify(updated)}))`).includes(`<p class="hint">${update}</p>`),language);
    const text=(kind,count)=>app.run(`stopInfo(${JSON.stringify(stop('authentication_required',kind,{auto_resume_count:count}))}).text`);
    assert.ok(text('script',1).endsWith(` ${login}`),language);
    assert.ok(!text('research',3).includes(login),'once the automatic resumes are spent the Studio stops checking');
    assert.ok(!text('episode_audio',0).includes(login),'a recording is not resumed by the login check');
  }
});

test('the research decisions lead each advice with one sentence and highlight the recommended button beside its alternative',()=>{
  const app=studio();
  const advice=(recommendation,extra={})=>({key:'0.0',diagnosis:'Beraten.',recommendation,limit:'none',hint:'',sources:[],...extra});
  const row=(id,extra)=>({id,question:id,status:'blocked',outcome:'evidence_block',web_attempts:1,reason:'Fehlt.',activity:'x',steps:4,read_sections:2,acceptance:['k'],reopened:0,...extra});
  const ledger={closed:0,total:3,accepted:0,phase:'blocked',
    budget_projection:{feasible:true,used:5,remaining:100,minimum_remaining_calls:3,closing_calls:2,expected_remaining_calls:40,expected_calls_per_task:16},
    questions:[row('a',{advice:advice('retry',{sources:[{title:'Salganik 2020',url:'https://pmc.example/s',note:''}],hint:'Freie Fassung lesen'})}),
      row('b',{advice:advice('accept_gap')}),row('c',{advice:advice('retry')})]};
  const decisions=(target,data)=>target.run(`renderResearchDecisions({status:'blocked',progress:{research_questions:${JSON.stringify(data)},search_rounds:2,search_round_limit:24,source_limit:150}},{run_id:'run_x'},false,0,true,24)`);
  const card=decisions(app,ledger);
  assert.ok(card.includes('<p>Der Berater sieht einen Weg zu Belegen (Salganik 2020); ein neuer Versuch kostet etwa 16 Aufrufe.</p>'));
  assert.ok(card.includes('<p>Der Berater sieht einen Weg zu Belegen; ein neuer Versuch kostet etwa 16 Aufrufe.</p>'),'a clause without its figure is left out');
  assert.ok(card.includes('<p>Der Berater findet keine weiteren zugänglichen Belege; als Lücke nennt das Dossier diesen Punkt offen als unbelegt.</p>'));
  // Retry advice: adopting it is the filled button, accepting the gap stays beside it.
  assert.ok(card.includes('<button data-action="apply-advice" data-run-id="run_x">Empfehlungen übernehmen und fortsetzen</button>'));
  assert.ok(card.includes('<button class="small" data-action="retry-task" data-run-id="run_x" data-task-id="a">'));
  assert.ok(card.includes('<button class="secondary small" data-action="accept-gap" data-run-id="run_x" data-task-id="a">'));
  // Gap advice: accepting the gap is highlighted, a new attempt stays beside it.
  assert.ok(card.includes('<button class="small" data-action="accept-gap" data-run-id="run_x" data-task-id="b">'));
  assert.ok(card.includes('<button class="secondary small" data-action="retry-task" data-run-id="run_x" data-task-id="b">'));
  assert.ok(!/\schecked\b/.test(card),'nothing is ticked for the editor');
  // Advice from before the current block is history: no sentence, no highlight.
  const old=decisions(app,{...ledger,questions:[row('b',{advice:advice('accept_gap'),retries:1})]});
  assert.ok(!old.includes('Der Berater findet keine weiteren')&&old.includes('<button class="secondary small" data-action="accept-gap"'));
  const en=studio({language:'en'}), english=decisions(en,ledger);
  assert.ok(english.includes('<p>The advisor sees a way to evidence (Salganik 2020); a new attempt costs about 16 calls.</p>'));
  assert.ok(english.includes('<p>The advisor finds no further accessible evidence; as a gap, the dossier names this point openly as unsupported.</p>'));
  assert.deepEqual(germanLeft(english.replace(/Beraten\.|Fehlt\./g,'')),[]);
});

test('an access gap names its criterion, recommends the upload where a library copy is plausible and waits for a chosen source',()=>{
  const app=studio();
  const row={id:'t03',question:'Benchmark',status:'blocked',outcome:'evidence_block',web_attempts:2,steps:5,read_sections:9,reopened:0,activity:'x',
    reason:'Unerfüllt: Kriterium 2: Verifies the figures',acceptance:['A','B','Verifies the figures']};
  const blocked=[{url:'https://publisher.example/b',evidence:'HTTP 403'}];
  const form=()=>app.run(`accessGapForm(${JSON.stringify(row)},'run_x',${JSON.stringify(blocked)})`);
  app.run(`project={id:'p',config:boot.defaults,works:{missing:[],provided:[]}};`);
  let html=form();
  assert.ok(html.includes('<p>Kriterium 2 braucht eine Quelle, die der Verlag sperrt; akzeptiert, wird der Rest der Antwort geprüft und die Lücke steht im Bericht.</p>'));
  assert.ok(html.includes('<button class="small" id="access-accept-t03" data-action="accept-access-gap" data-run-id="run_x" data-task-id="t03" disabled>'),
    'the highlighted way, enabled only once a source is chosen');
  assert.ok(html.includes('<option value="" selected>Gesperrte Quelle wählen …</option>')&&!html.includes('data-scroll="works-panel"'));
  // The editor's choice (the page's change handler keeps it) enables the button and survives a redraw.
  app.run(`accessChoices.t03='https://publisher.example/b';`);
  html=form();
  assert.ok(html.includes('<option value="https://publisher.example/b" selected>')&&!html.includes('data-task-id="t03" disabled'));
  // A missing work this question needs may exist as a library copy: uploading it is highlighted, the gap stays beside it.
  app.run(`delete accessChoices.t03;project.works={missing:[{work:'Merton (1968)',state:'blocked',tasks:[{id:'t03',question:'Benchmark'}]}],provided:[]};`);
  html=form();
  assert.ok(html.includes('<p>Kriterium 2 braucht Merton (1968), die der Verlag sperrt;'));
  assert.ok(html.includes('<button class="small" data-scroll="works-panel">Werk hochladen</button>'));
  assert.ok(html.includes('<button class="secondary small" id="access-accept-t03" data-action="accept-access-gap"'));
  const en=studio({language:'en'});
  en.run(`project={id:'p',config:boot.defaults,works:{missing:[{work:'Merton (1968)',state:'blocked',tasks:[{id:'t03',question:'Benchmark'}]}],provided:[]}};`);
  const english=en.run(`accessGapForm(${JSON.stringify(row)},'run_x',${JSON.stringify(blocked)})`);
  assert.ok(english.includes('<p>Criterion 2 needs Merton (1968), which the publisher blocks; once accepted, the rest of the answer is checked and the gap stands in the report.</p>'));
  assert.ok(english.includes('>Upload the work</button>'));
});

test('dispute, residual finish and teaching redesign lead with one sentence and highlight the recommended choice',()=>{
  const app=studio();
  const dispute={objection_id:'o1',task_id:'t',question:'Frage',decision:null,objection:{reason:'Einwand',correction:'',closure_condition:''},review:{reason:'Gestützt',references:[]}};
  const disputeJob={status:'blocked',stop:{code:'review_disagreement'},run:{run_id:'run_x',kind:'research',stages:{}},progress:{review_disagreement:dispute,review_disagreements:[dispute]}};
  const disputeCard=app.run(`renderDisputeCard(${JSON.stringify(disputeJob)},'run_x',false)`);
  assert.ok(disputeCard.includes('<p>Die Gesamtprüfung hält ihren früheren Einwand für erledigt, weil eine inzwischen gelesene Stelle die Aussage stützt; beide Positionen stehen im Qualitätsbericht.</p>'));
  assert.ok(disputeCard.includes('<button data-action="decide-dispute" data-decision="reviewer"')&&disputeCard.includes('<button class="secondary" data-action="decide-dispute" data-decision="objection"'));
  // Residual finish: the objections the last round left, and the cost of another round while its rework runs.
  const ledger={phase:'questions',audit_round:2,budget_projection:{expected_remaining_calls:48},questions:[
    {id:'a',status:'verified',objections:[{reason:'x'},{reason:'y'}]},{id:'b',status:'researching',objections:[{reason:'z'}]},
    {id:'c',status:'blocked',accepted_gap:true,objections:[{reason:'q'}]}]};
  let residual=app.run(`residualFinish(${JSON.stringify(ledger)},'run_x')`);
  assert.ok(residual.includes('<p>Runde 2 findet noch 3 Einwände, meist Details; abschließen heißt, sie stehen im Qualitätsbericht statt in einer weiteren Runde (etwa 48 Aufrufe).</p>'));
  assert.ok(residual.includes('<button class="small" data-action="finish-residual" data-run-id="run_x">Nach der nächsten Prüfung abschließen</button>'));
  assert.ok(residual.includes('<p class="hint">Ohne diese Wahl wird weiter nachgebessert: die nächste Gesamtprüfung öffnet ihre Einwände wieder.</p>'),'the alternative stays visible');
  residual=app.run(`residualFinish(${JSON.stringify({...ledger,phase:'audit'})},'run_x')`);
  assert.ok(residual.includes('statt in einer weiteren Runde.</p>'),'without a rework running no cost is named');
  // Teaching redesign: the episode, what a redraft costs, and the note starting with the open points, editable.
  app.run(`project={id:'p',config:boot.defaults,research:'dossier',job:{id:'j1',status:'blocked',action:'resume',message:'Das Lehrkonzept hat auch nach der gezielten automatischen Korrektur noch offene Punkte: Beispiel fehlt. Szene 4 zu lang.',stop:{code:'teaching_design_failed',stage:'teaching',run_kind:'script'},teaching_failure:{episode_id:'ep_003',title:'Drei'},started_at:new Date().toISOString(),run:{run_id:'run_s',kind:'script',stages:{teaching:{status:'blocked',error:{code:'teaching_design_failed'}}}}}};overviewPage=false;step=PAGE.production;render();`);
  const redesign=app.elements.get('stop-card').innerHTML;
  assert.ok(redesign.includes('<p>Das Lehrkonzept von Folge 3 hat nach drei Korrekturen noch offene Punkte; ein Neuentwurf mit diesen Punkten als Hinweis kostet etwa 4–6 Aufrufe, die übrigen Folgen bleiben.</p>'));
  assert.ok(redesign.includes('<textarea id="redesign-note" rows="3">Beispiel fehlt. Szene 4 zu lang.</textarea>'));
  assert.ok(redesign.includes('<button data-action="redesign-teaching" data-run-id="run_s" data-teaching-episode="ep_003" >Mit den offenen Punkten neu entwerfen</button>'));
  assert.ok(redesign.includes('<button class="secondary" data-action="plan"'),'a new table of contents stays the alternative');
  const en=studio({language:'en'});
  assert.ok(en.run(`renderDisputeCard(${JSON.stringify(disputeJob)},'run_x',false)`).includes('<p>The overall review considers its earlier objection settled because a passage read since supports the statement; both positions stand in the quality report.</p>'));
  const english=en.run(`residualFinish(${JSON.stringify(ledger)},'run_x')`);
  assert.ok(english.includes('<p>Round 2 still finds 3 objections, mostly details; finishing means they stand in the quality report instead of another round (about 48 calls).</p>'));
  assert.ok(english.includes('>Finish after the next review</button>'));
  assert.deepEqual(germanLeft(english),[]);
  const teaching=en.run(`stopInfo({status:'blocked',action:'resume',stop:{code:'teaching_design_failed'},teaching_failure:{episode_id:'ep_003',title:'Drei'},run:{run_id:'run_s',kind:'script',stages:{}},progress:{}}).text`);
  assert.equal(teaching,'The teaching plan of episode 3 still has open points after three corrections; a redraft with these points as a note costs about 4–6 calls; the other episodes stay.');
});

test('the table of contents and the audio approval say in one sentence what approving starts',()=>{
  const app=studio(), p=workflowProject(app);
  p.outline.approval=null;p.outline.run_id='run_o';
  p.outline.plan.episodes=[{episode_id:'ep_001',title:'Der Anfang',central_question:'?',target_minutes:24,scenes:[],deferred_questions:[]},
    {episode_id:'ep_002',title:'Die Synthese',central_question:'?',target_minutes:26,scenes:[],deferred_questions:[]}];
  p.job={id:'o',action:'plan',status:'review_ready',run:{run_id:'run_o',kind:'script',status:'pending',stages:{planning:{status:'completed',attempts:1}}},
    progress:{phase:'script',budget_projection:{calibration:{run_id:'run_old'},expected_remaining_calls:88,minimum_remaining_calls:22}}};
  app.run(`project=${JSON.stringify(p)};`);
  let outline=app.run('renderOutline()');
  assert.ok(outline.includes('2 Folgen zu je etwa 25 Minuten, von „Der Anfang“ bis zur Synthese; die Freigabe startet das Schreiben (etwa 88 Aufrufe).'));
  assert.ok(outline.includes('<button class="secondary" data-action="replan"')&&outline.includes('<button data-action="script"'));
  app.run('delete project.job.progress.budget_projection.calibration;');
  assert.ok(app.run('renderOutline()').includes('bis zur Synthese; die Freigabe startet das Schreiben.'),'an uncalibrated minimum is no estimate');
  // Audio: what the approval confirms, who speaks and who pays; the checkbox is never ticked for the editor.
  const ep=id=>({script:{episode_id:id,title:`Folge ${id}`,segments:[],chapters:[]},hash:'h',readable_hash:'r',audio:[],audio_current:false});
  app.run(`boot.key_available=true;boot.capabilities={parallel_audio:true};project={id:'p',config:boot.defaults,audio_settings:{provider:'openrouter_gemini_tts',voices:{host_a:'Sadaltager',host_b:'Aoede'}},
    episodes:[${JSON.stringify(ep('ep_001'))},${JSON.stringify(ep('ep_002'))}],audio_jobs:[],audio_capacity:{available:2},audio_queue:[]};`);
  const all=app.run('renderApproveAll(true)');
  assert.ok(all.includes('<p>Die Freigabe bestätigt, dass du die Skripte gelesen hast; danach spricht Gemini · OpenRouter sie mit Sadaltager und Aoede ein, abgerechnet über deinen Key.</p>'));
  assert.ok(all.includes('<input id="audio-approve-all" type="checkbox">'));
  assert.ok(all.includes('>2 gelesene Folgen freigeben</button><button class="secondary" data-step="4">Zuerst die Skripte lesen</button>'));
  app.run(`project.audio_settings={provider:'qwen3_local',voices:{host_a:'Aiden',host_b:'Vivian'}};`);
  const single=app.run('renderApprovalCard(project.episodes[0],project.audio_settings,false)');
  assert.ok(single.includes('<p>Die Freigabe bestätigt, dass du das Skript gelesen hast; danach spricht Qwen · lokal es mit Aiden und Vivian ein.</p>'),'Qwen bills no key');
  assert.ok(single.includes('<input id="audio-approval" type="checkbox" >'));
  const en=studio({language:'en'});
  en.run(`project={id:'p',config:boot.defaults,audio_settings:{provider:'qwen3_local',voices:{host_a:'Aiden',host_b:'Vivian'}},episodes:[${JSON.stringify(ep('ep_001'))}],audio_jobs:[],audio_queue:[]};`);
  assert.ok(en.run('renderApprovalCard(project.episodes[0],project.audio_settings,false)').includes('<p>Approving confirms that you have read the script; then Qwen · local records it with Aiden and Vivian.</p>'));
  en.run(`project=${JSON.stringify(p)};`);
  assert.ok(en.run('renderOutline()').includes('2 episodes of about 25 minutes each, from “Der Anfang” to the synthesis; approving starts the writing (about 88 calls).'));
});

test('a raise in the research decisions is sized to the open questions and adopts the advice in the same click',async()=>{
  const app=studio();
  await new Promise(resolve=>setTimeout(resolve,0)); // the page's own start-up read settles before the project is set
  const advice={key:'0.0',diagnosis:'Quellenlimit erreicht.',recommendation:'raise_limit',limit:'sources',hint:'Neue Quellen suchen',sources:[]};
  const row=(id,status,extra={})=>({id,question:id,status,activity:'x',steps:3,read_sections:2,acceptance:['k'],reopened:0,...extra});
  const blocked=id=>row(id,'blocked',{outcome:'budget_block',block_cause:'source_limit',reason:'Quellenlimit erreicht.',web_attempts:1,advice});
  const ledger=verified=>({closed:verified,total:verified+3,accepted:0,phase:'blocked',source_attempt_count:150,
    budget_projection:{feasible:true,used:90,remaining:60,minimum_remaining_calls:3,closing_calls:2,expected_remaining_calls:50,expected_calls_per_task:16},
    questions:[...Array.from({length:verified},(_,i)=>row(`v${i}`,'verified')),blocked('b1'),blocked('b2'),row('p1','pending')]});
  const progress=data=>({phase:'research',research_questions:data,search_rounds:20,search_round_limit:24,source_limit:150,
    plan_review:{awaiting:false,approved:true,projection:{sources_per_task:5,sources_used:30,search_rounds_per_task:2,search_rounds_used:4}}});
  const decisions=(target,data)=>target.run(`renderResearchDecisions({status:'blocked',progress:${JSON.stringify(progress(data))}},{run_id:'run_x'},false,0,true,24)`);
  // Two verified answers: the plan's rates, 3 open questions × 5 sources and × 2 rounds, a quarter to spare.
  let card=decisions(app,ledger(2));
  assert.deepEqual([...app.run(`Object.values(sizedRaise({progress:${JSON.stringify(progress(ledger(2)))}}))`)],[169,28]);
  assert.ok(card.includes('data-sources="169" data-search-rounds="28"'));
  // From three verified answers on, the higher measured rate: (150 − 30) sources / 4 answers and (20 − 4) rounds / 4.
  card=decisions(app,ledger(4));
  assert.ok(card.includes('<p>2 Teilfragen konnten nicht suchen, weil das Quellenlimit (150 von 150) erreicht ist; mit +113 Quellen und +11 Suchrunden kommen alle weiter.</p>'));
  assert.ok(card.includes('<button data-action="raise-adopt" data-run-id="run_x" data-sources="263" data-search-rounds="35">Limits anheben und Empfehlungen übernehmen</button>'));
  assert.ok(!card.includes('data-action="apply-advice"')&&!card.includes('data-action="approve-sources"')&&!card.includes('data-action="approve-search"'),'one raise, not three');
  assert.ok(card.includes('data-action="retry-task" data-run-id="run_x" data-task-id="b1"')&&card.includes('data-action="accept-gap" data-run-id="run_x" data-task-id="b2"'),
    'deciding each question on its own stays the alternative');
  // One click: both limits, every retry advice with its hint, then the run.
  app.run(`project={id:'p',config:boot.defaults,job:{id:'j1',status:'blocked',action:'research',started_at:new Date().toISOString(),run:{run_id:'run_x',kind:'research',stages:{}},progress:${JSON.stringify(progress(ledger(4)))}}};`);
  app.responses.set('/api/projects/p',app.run('structuredClone(project)'));
  await app.run(`raiseAndAdopt({dataset:{runId:'run_x',sources:'263',searchRounds:'35'}})`);
  assert.deepEqual(app.requests.filter(r=>r.options?.method==='POST').map(r=>[r.path,JSON.parse(r.options.body)]),[
    ['/api/projects/p/approve',{kind:'model_calls',run_id:'run_x',sources:263,search_rounds:35}],
    ['/api/projects/p/approve',{kind:'retry',run_id:'run_x',task_id:'b1',hint:'Neue Quellen suchen'}],
    ['/api/projects/p/approve',{kind:'retry',run_id:'run_x',task_id:'b2',hint:'Neue Quellen suchen'}],
    ['/api/projects/p/start',{action:'resume',run_id:'run_x'}]]);
  const en=studio({language:'en'}), english=decisions(en,ledger(4));
  assert.ok(english.includes('<p>2 sub-questions could not search because the source limit (150 of 150) is reached; with +113 sources and +11 search rounds all can go on.</p>'));
  assert.ok(english.includes('>Raise limits and adopt recommendations</button>'));
});

test('a new project can start as a trial: never ticked by the page, it says what to expect and allows an empty topic',async()=>{
  const app=studio();
  await new Promise(resolve=>setTimeout(resolve,0)); // the page's own start-up read settles before the project is set
  const facts={sub_questions:3,target_total_minutes:20,limits:{model_calls:110,search_rounds:16,sources:40,cost_usd:45}};
  app.run(`boot.capabilities={conversational_setup:true,project_attachments:true};project=null;`);
  assert.ok(!app.run('renderBrief()').includes('trial-option'),'a server without the trial facts offers no trial');
  app.run(`boot.trial=${JSON.stringify(facts)};`);
  let page=app.run('renderBrief()');
  assert.ok(page.includes('<label class="check trial-option"><input type="checkbox" id="trial-option"> Probelauf: kleines Thema, eine kurze Folge</label>'));
  assert.ok(page.includes('Ein Probelauf recherchiert höchstens 3 Teilfragen und plant eine Folge von etwa 20 Minuten. Er dauert meist einige Stunden; mit einem Abo kostet er nichts extra, mit einem API-Key höchstens die Kostengrenze (bis 45 USD je Lauf).'));
  // Without the trial an empty message sends nothing.
  await app.run(`sendSetupMessage('')`);
  assert.equal(app.requests.filter(r=>r.options?.method==='POST').length,0);
  app.run('trialChecked=true;');
  page=app.run('renderBrief()');
  assert.ok(page.includes('id="trial-option" checked')&&page.includes('Ohne eigenes Thema genügt „Senden“; der Probelauf nimmt dann ein Beispielthema.'));
  // Ticked, an empty message creates the project as a trial without a topic; the server takes the sample topic.
  const created=workflowProject(app);
  Object.assign(created,{id:'trial-1',job:null,research:null,outline:null,episodes:[]});created.config.trial=true;created.config.topic='Wie entsteht ein Regenbogen?';
  app.responses.set('/api/projects',{id:'trial-1'});
  app.responses.set('/api/projects/trial-1',created);
  await app.run(`sendSetupMessage('')`);
  const create=app.requests.find(r=>r.path==='/api/projects'&&r.options?.method==='POST');
  const body=JSON.parse(create.options.body);
  assert.equal(body.trial,true);
  assert.equal(body.config.topic,'');
  assert.deepEqual(JSON.parse(app.requests.find(r=>r.path==='/api/projects/trial-1/start').options.body),
    {action:'assistant',message:'Ich möchte einen Probelauf mit dem Beispielthema machen.'});
  assert.equal(app.run('trialChecked'),false);
  // The trial says so on its pages and its card.
  assert.ok(app.run('heading(1)').includes('<span class="chip trial">Probelauf</span>'));
  assert.ok(app.run(`overviewCardInner({id:'t',topic:'Regenbogen',trial:true,episodes:[]})`).includes('Regenbogen</button> <span class="chip trial">Probelauf</span></h2>'));
  assert.ok(!app.run(`overviewCardInner({id:'u',topic:'Serie',episodes:[]})`).includes('chip trial'));
  // A typed topic stays the topic of the trial.
  const en=studio({language:'en'});
  en.run(`boot.capabilities={conversational_setup:true,project_attachments:true};boot.trial=${JSON.stringify(facts)};project=null;trialChecked=true;`);
  const english=en.run('renderBrief()');
  assert.ok(english.includes('Trial: a narrow topic, one short episode</label>'));
  assert.ok(english.includes('A trial researches at most 3 sub-questions and plans one episode of about 20 minutes. It usually takes a few hours; on a subscription it costs nothing extra, on an API key at most the money limit (up to 45 USD per run).'));
  assert.deepEqual(germanLeft(english),[]);
});

test('download links carry the names the server sends in the content language, the page\'s own only as a fallback',()=>{
  const app=studio();
  app.run('boot.capabilities={podcast_downloads:true}');
  const p={id:'test',topic:'Topic',episode_count:2,download_zip:'Topic - All episodes.zip',episodes:[
    {episode_id:'ep_001',title:'First',audio:['exports/ep_001/one.mp3'],audio_current:true,download_names:['Topic - Episode 01 - First.mp3']},
    {episode_id:'ep_002',title:'Second',audio:['exports/ep_002/two.mp3'],audio_current:true,download_names:[null]}]};
  const html=app.run(`renderRecordings(${JSON.stringify(p)})`);
  assert.ok(html.includes('href="/download/test/podcast.zip" download="Topic - All episodes.zip"'));
  assert.ok(html.includes('download="Topic - Episode 01 - First.mp3"'));
  assert.ok(html.includes('download="Topic - Folge 02 - Second.mp3"'),'a recording without a server name keeps the fallback');
  assert.ok(app.run(`overviewDownload(${JSON.stringify(p)})`).includes('href="/download/test/podcast.zip" download="Topic - All episodes.zip"'));
  // The project page passes them on from its detail; a server without them leaves the bare attribute.
  app.run(`project={id:'test',config:{...boot.defaults,topic:'Topic'},download_zip:'Topic - Alle Folgen.zip',episodes:[{script:{episode_id:'ep_001',title:'First'},audio:['one.mp3'],audio_current:true,download_names:['Topic - Folge 01 - First.mp3']}]};`);
  const own=app.run('recordingsProject()');
  assert.equal(own.download_zip,'Topic - Alle Folgen.zip');
  assert.deepEqual([...own.episodes[0].download_names],['Topic - Folge 01 - First.mp3']);
  assert.ok(app.run(`podcastDownload(${JSON.stringify({...p,download_zip:null})})`).includes('href="/download/test/podcast.zip" download>'));
});

test('deterministic advisories read in the page language from their values, older rows keep their German sentence',()=>{
  const rows=[{code:'long_cold_open',count:157,detail:'Der erste gesprochene Abschnitt hat 157 Wörter; über 100 beginnt die Folge ohne Atempause.',params:{words:157,limit:100},segment_ids:['seg_001']},
    {code:'over_target_duration',count:130,detail:'Geschätzte 27.5 Minuten …',params:{estimated_minutes:27.5,target_minutes:21,percent:130,limit_percent:120},segment_ids:[]},
    {code:'redefined_term',count:3,detail:'«Token <x>» …',params:{term:'Token <x>',definitions:3},segment_ids:['seg_002']},
    {code:'repeated_hedging',count:4,detail:'4 Hinweise darauf, dass ein Beispiel erfunden ist (gespeichert vor dem 7. Oktober).',segment_ids:['seg_003']}];
  for(const [language,texts] of [['de',['Der erste gesprochene Abschnitt hat 157 Wörter; über 100 beginnt die Folge ohne Atempause. (seg_001)',
      'Geschätzte 27,5 Minuten gegenüber geplanten 21, also 130 Prozent des Ziels; über 120 Prozent gilt die Folge als zu lang.',
      '«Token &lt;x&gt;» wird in dieser Folge 3-mal neu definiert, obwohl der Begriff aus einer früheren Folge bekannt ist.']],
    ['en',['The first spoken passage has 157 words; above 100 the episode starts without a breather. (seg_001)',
      'An estimated 27.5 minutes against 21 planned, that is 130 percent of the target; above 120 percent the episode counts as too long.',
      '“Token &lt;x&gt;” is defined anew 3 times in this episode although the term is known from an earlier episode.']]]){
    const app=studio({language}), p=workflowProject(app);
    p.job.status='completed';p.job.run.status='completed';
    p.episodes=[publishedEpisode({advisories:rows})];
    app.run(`project=${JSON.stringify(p)};readingSnapshot=null;`);
    const html=app.run('renderScript()');
    for(const text of texts)assert.ok(html.includes(text),`${language}: ${text}`);
    assert.ok(html.includes('4 Hinweise darauf, dass ein Beispiel erfunden ist (gespeichert vor dem 7. Oktober).'),`${language}: a row without values keeps its German sentence`);
  }
});

test('the settings say where each key is kept, CORE included, and a stored key reports its new place (D-167)',async()=>{
  const app=studio();
  const view={settings:{text:{provider:'claude_code',model:'claude-haiku-5-5',reasoning_effort:'xhigh',max_output_tokens:32768},
    audio:{provider:'qwen_local',voices:{host_a:'Aiden',host_b:'Vivian'},pauses:{same_speaker_ms:250,speaker_change_ms:450,chapter_break_ms:900}},
    execution:{text:'sequential',audio:'sequential'},allowances:{fresh_attempts:0,extra_calls:0},
    research_limits:{model_calls:750,sources:150,search_rounds:48},text_timeout_seconds:1800},
    hash:'h',global:true,key_available:true,google_key_available:false,anthropic_key_available:true,perplexity_key_available:false,
    core_key_available:true,key_vault:true,claude_extra_usage:false,
    key_sources:{openrouter:'vault',google:null,anthropic:'session',perplexity:null,core:'environment'}};
  app.responses.set('/api/settings',view);
  await app.run('showSettings()');
  const html=app.elements.get('content').innerHTML;
  assert.ok(html.includes('id="key-panel-core"') && html.includes('data-key-field="core-key" data-key-kind="core"'));
  assert.ok(html.includes('data-scroll="key-panel-core">CORE</button>'));
  assert.ok(html.includes('id="key-storage">Jeder Key wird im Tresor deines Betriebssystems gespeichert'),'the store is named once');
  assert.ok(html.includes('id="key-status" class="hint">Im Tresor gespeichert; nach einem Neustart wieder da.'));
  assert.ok(html.includes('id="anthropic-key-status" class="hint">Nur für diese Sitzung hinterlegt'));
  assert.ok(html.includes('id="core-key-status" class="hint">Kommt aus einer Umgebungsvariable des Servers.'));
  assert.ok(html.includes('id="google-key-status" class="hint">Noch kein Key hinterlegt.'));
  // A system without a store says the keys last until the next restart.
  app.run('settingsData.key_vault=false;render();');
  assert.ok(app.elements.get('content').innerHTML.includes('id="key-storage">Dieses System hat keinen Schlüsseltresor'));
  // Storing the CORE key takes the server's answer, including where the key now lives.
  app.run('settingsData.key_vault=true;render();$("core-key").value="core-test-key-123";');
  app.responses.set('/api/key',{key_available:true,core_key_available:true,key_vault:true,
    key_sources:{openrouter:'vault',google:null,anthropic:'session',perplexity:null,core:'vault'}});
  await app.run('storeKey("core-key","core")');
  const sent=app.requests.filter(r=>r.path==='/api/key').map(r=>JSON.parse(r.options.body));
  assert.deepEqual(sent,[{key:'core-test-key-123',kind:'core'}]);
  assert.equal(app.elements.get('core-key-status').textContent,'Im Tresor gespeichert; nach einem Neustart wieder da.');
  assert.equal(app.run('boot.key_sources.core'),'vault');
});

test('a podcast gets a new version from its card menu, opens it, and offers the old sources to its first research',async()=>{
  // D-168, the user's wish of 2026-10-07: make an old podcast again from its inputs with today's pipeline.
  const app=studio();
  app.run(`boot.capabilities={project_overview:true,project_versions:true};boot.projects=[{id:'a',topic:'Topic',version:1},{id:'b',topic:'Topic',version:2}];
    overviewData={projects:[{id:'a',topic:'Topic',version:1,episodes:[]},{id:'b',topic:'Topic',version:2,episodes:[]}],trash:[]};`);
  const html=app.run('renderOverview()');
  assert.ok(html.includes('data-new-version="a"')&&html.includes('data-new-version="b"'));
  assert.equal((html.match(/<span class="chip">Version \d<\/span>/g)||[]).join(),'<span class="chip">Version 2</span>','a first version carries no number');
  assert.ok(app.run('sidebarProjects()').includes('Topic · Version 2'));
  app.run('boot.capabilities={project_overview:true}');
  assert.ok(!app.run('renderOverview()').includes('data-new-version'),'an older server offers no new version');
  // Declined, nothing is sent; confirmed, the server makes it and the page opens it.
  app.run('window.confirm=()=>false');
  await app.run(`newVersion('a')`);
  assert.equal(app.requests.filter(r=>r.path.endsWith('/new_version')).length,0);
  app.run('window.confirm=message=>{window.lastConfirm=message;return true;}');
  const made=workflowProject(app);
  Object.assign(made,{id:'c',research:null,outline:null,job:null,version:{version:3,from:'a',library:{run_id:'run_x',documents:42,from_version:1}}});
  app.responses.set('/api/projects/a/new_version',{id:'c',version:3});
  app.responses.set('/api/projects/c',made);
  app.responses.set('/api/bootstrap',{...JSON.parse(app.run('JSON.stringify(boot)')),projects:[{id:'a',topic:'Topic',version:1},{id:'c',topic:'Topic',version:3}],capabilities:{project_versions:true}});
  await app.run(`newVersion('a')`);
  assert.ok(app.run('window.lastConfirm').includes('keine Läufe'));
  assert.equal(app.requests.filter(r=>r.path==='/api/projects/a/new_version'&&r.options?.method==='POST').length,1);
  assert.equal(app.run('project.id'),'c');
  assert.equal(app.elements.get('notice').textContent,'Version 3 angelegt. Sie beginnt mit dem Auftrag der bisherigen Version.');
  assert.ok(app.elements.get('project-select').innerHTML.includes('Topic · Version 3'));
  // The first research of the new version offers the old sources; the start sends that choice like a restart does.
  const page=app.run('renderResearch()');
  assert.ok(page.includes('id="seed-corpus" checked')&&page.includes('Die 42 Quellen aus Version 1 als Startbibliothek anbieten'));
  app.run('project.version.library=null');
  assert.ok(!app.run('renderResearch()').includes('seed-corpus'),'without a library there is nothing to offer');
});

test('a stopped project card offers the step its page recommends as one click, with looking first beside it',()=>{
  // 2026-10-08: every stopped run took "Ansehen" and then "Fortsetzen" on its page.
  const app=studio(), run={run_id:'run_r',kind:'research',stages:{}};
  const card=job=>app.run(`overviewCardInner(${JSON.stringify({id:'p',topic:'Orlagau',episodes:[],job})})`);
  let html=card({id:'j',status:'interrupted',action:'resume',run});
  assert.ok(html.includes('<button class="small" data-card-action="resume" data-card-project="p" data-run-id="run_r">Fortsetzen</button>'));
  assert.ok(html.includes('<button class="secondary small" data-open-project="p"'),'opening the project stays one click beside it');
  assert.ok(!card({id:'j',status:'running',action:'resume',run}).includes('data-card-action'),'a running project has no step to take');
  assert.ok(!card({id:'j',status:'blocked',action:'resume',stop:{code:'invalid_key'},run}).includes('data-card-action'),'a key is typed on its page');
  html=card({id:'j',status:'blocked',action:'resume',stop:{code:'invalid_model_output'},run,fresh_attempts:true});
  assert.ok(html.includes('data-card-action="fresh"')&&html.includes('Mit neuen Anläufen fortsetzen'));
  html=card({id:'j',status:'blocked',action:'resume',stop:{code:'invalid_model_output'},run,fresh_attempts:false});
  assert.ok(html.includes('data-card-action="resume"'),'without fresh attempts the server would accept, the stop resumes as the header offers');
  assert.ok(!card({id:'j',status:'blocked',action:'resume',stop:{code:'invalid_model_output'},run}).includes('data-card-action'),
    'a server that does not say whether it takes fresh attempts leaves the choice to the page');
  html=card({id:'j',status:'blocked',action:'resume',stop:{code:'research_budget_exhausted'},run,progress:{model_call_limit:1200,model_calls:1200}});
  assert.ok(html.includes('data-card-action="calls"')&&html.includes('Aufruflimit auf 1250 erhöhen und fortsetzen'),'the raise the stop card offers, with its number');
});

test('the card adopts the advisor\'s retries with their hints and resumes, as the decision card does',async()=>{
  const app=studio();
  const ledger={phase:'blocked',closed:13,budget_projection:{used:600,limit:1200,remaining:600,minimum_remaining_calls:2,expected_calls_per_task:10},
    questions:[{id:'q1',question:'Eins?',status:'blocked',outcome:'search_block',retries:0,auto_retries:1,advice:{key:'0.1',recommendation:'retry',hint:'Patze lesen.'}},
      {id:'q2',question:'Zwei?',status:'blocked',outcome:'search_block',retries:0,auto_retries:0,advice:{key:'0.0',recommendation:'accept_gap'}}]};
  const p={id:'orla',topic:'Orlagau',episodes:[],job:{id:'j',status:'blocked',action:'resume',run:{run_id:'run_o',kind:'research',stages:{}},
    progress:{phase:'research',research_questions:ledger}}};
  app.run(`overviewData={projects:[${JSON.stringify(p)}],trash:[]};overviewPage=true;`);
  const next=app.run('cardNextAction(overviewData.projects[0])');
  assert.equal(next.kind,'adopt');
  assert.equal(next.label,'Empfehlungen übernehmen und fortsetzen');
  assert.deepEqual(JSON.parse(JSON.stringify(next.retries)),[{id:'q1',hint:'Patze lesen.'}],'only the retry advice, with its hint');
  const unhinted=structuredClone(p);delete unhinted.job.progress.research_questions.questions[0].advice.hint;
  assert.equal(app.run(`cardNextAction(${JSON.stringify(unhinted)})`),null,'rows without hints (an older server) leave the adoption to the page');
  // A card drawn before the state changed does nothing and says so.
  await assert.rejects(app.run(`runCardAction({dataset:{cardAction:'resume',cardProject:'orla',runId:'run_o'}})`),/Stand dieses Projekts/);
  app.run(`overviewData={projects:[${JSON.stringify(p)}],trash:[]};`);
  app.requests.length=0;
  await app.run(`runCardAction({dataset:{cardAction:'adopt',cardProject:'orla',runId:'run_o'}})`);
  const posted=app.requests.filter(r=>r.options?.method==='POST').map(r=>[r.path,JSON.parse(r.options.body)]);
  assert.deepEqual(posted,[['/api/projects/orla/approve',{run_id:'run_o',kind:'retry',task_id:'q1',hint:'Patze lesen.'}],
    ['/api/projects/orla/start',{action:'resume',run_id:'run_o'}]]);
  // Where the source limit stops the questions, the card raises it to the decision card's sized value first.
  const limited=structuredClone(p);Object.assign(limited.job.progress,{source_limit:200,search_round_limit:85,search_rounds:20});limited.job.progress.research_questions.source_attempt_count=200;
  const raise=app.run(`cardNextAction(${JSON.stringify(limited)})`);
  assert.equal(raise.kind,'raise_adopt');
  assert.ok(raise.sources>200&&raise.searchRounds>85,'both limits rise as on the decision card');
});

test('a text run\'s card names its numbers: verified, in work, blocked and the calls of its limit',()=>{
  // 2026-10-08: "49 von 74 geprüft, 5 in Arbeit …" took asking each time.
  const app=studio();
  const rows=[...Array(49).fill({status:'verified'}),...Array(5).fill({status:'researching'}),{status:'reviewing'},
    ...Array(4).fill({status:'blocked',outcome:'prerequisite_block'}),{status:'blocked',outcome:'search_block'},{status:'blocked',outcome:'evidence_block'},
    ...Array(13).fill({status:'pending'})];
  const job={id:'j',status:'running',action:'resume',run:{run_id:'r',kind:'research',stages:{}},
    progress:{model_calls:1119,model_call_limit:1500,research_questions:{total:74,closed:49,blocked:6,questions:rows}}};
  const p={id:'orla',topic:'Orlagau',episodes:[],job};
  assert.equal(app.run(`cardProgress(${JSON.stringify(p)})`),'49 von 74 geprüft · 6 in Arbeit · 6 blockiert, 4 davon nur wegen Vorfragen · Aufrufe 1119 von 1500');
  assert.ok(app.run(`overviewCardInner(${JSON.stringify(p)})`).includes('<p class="card-progress" id="project-progress-orla">49 von 74 geprüft'));
  // A stopped run keeps its numbers; a script run without a ledger counts its segments.
  const script={...p,job:{...job,status:'interrupted',run:{run_id:'s',kind:'script',stages:{}},progress:{completed_segments:3,total_segments:8,model_calls:40,model_call_limit:300}}};
  assert.equal(app.run(`cardProgress(${JSON.stringify(script)})`),'3 von 8 erledigt · Aufrufe 40 von 300');
  // A finished project and a recording show no such line.
  assert.equal(app.run(`cardProgress(${JSON.stringify({...p,job:{...job,status:'completed'}})})`),'');
  assert.ok(!app.run(`overviewCardInner(${JSON.stringify({id:'x',topic:'X',episodes:[],job:{id:'a',status:'running',action:'audio',run:{run_id:'a',kind:'episode_audio',stages:{}}}})})`).includes('card-progress'));
});
