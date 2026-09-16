"use strict";
const $ = id => document.getElementById(id);
const escape = value => String(value ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const steps = ["Auftrag & Stimmen", "Recherche", "Inhaltsverzeichnis", "Ausarbeitung", "Skripte lesen", "Vertonung"];
const pageKeys = ["brief", "research", "outline", "production", "scripts", "audio"];
const PAGE = Object.fromEntries(pageKeys.map((key,index)=>[key,index]));
const productionStages = [
  ["teaching", "Lehrkonzept", "Einstieg, Erklärungen und Beispiele für jede Folge ausarbeiten."],
  ["writing", "Skriptentwurf", "Aus dem Lehrkonzept ein vollständiges Gespräch entwickeln."],
  ["polishing", "Dialog-Polishing", "Gesprochene Sprache und die Rollen der beiden Hosts ausarbeiten."],
  ["review", "Qualitätsprüfung", "Quellen, Erklärungstiefe und Verständlichkeit prüfen und überarbeiten."],
  ["publish", "Zur Durchsicht bereitstellen", "Geprüfte Skripte zum Lesen bereitstellen."],
];
const stageNames = {discovery:"Quellensuche",retrieval:"Quellen lesen",dossier:"Dossier",planning:"Inhaltsverzeichnis",teaching:"Lehrkonzept",writing:"Skript",polishing:"Dialog-Polishing",review:"Qualitätsprüfung",publish:"Bereitstellen",synthesis:"Vertonung",assembly:"Audio zusammenfügen"};
const actionNames = {
  assistant: "Redaktion denkt nach",
  research: "Recherche läuft",
  plan: "Inhaltsverzeichnis entsteht",
  replan: "Inhaltsverzeichnis wird überarbeitet",
  script: "Skripte entstehen",
  revise: "Skript wird überarbeitet",
  audio: "Audio entsteht",
  audio_sample: "Gemini-Hörprobe entsteht",
  audio_samples: "Gemini-Stimmenbibliothek entsteht",
  resume: "Auftrag wird fortgesetzt",
  check: "Verbindungen werden geprüft",
};
let boot, project = null, step = 0, episodeIndex = 0, submitting = false, lastJobSignature = "";
let lastJobView = "";
let playingSample = null;
let followWorkflow = true;
let scriptEpisodeId=null, readingSnapshot=null;
let overviewPage=false, overviewData={projects:[],trash:[]};
let navigationEpoch=0;
let setupSending=false;
let pendingAttachments=[], readingAttachments=false;
const scriptStateLabels={draft:"Entwurf",polished:"Dialog überarbeitet",reviewed:"Prüfungen bestanden",published:"Fertig zur Durchsicht"};
const voiceSamples = () => project?.voice_samples || boot.voice_samples || {};
const savedSample = (voice, language) => voiceSamples()[language]?.[voice];
const sampleButtonLabel = (provider, voice, language) =>
  provider === "openrouter_gemini_tts" && !savedSample(voice, language)
    ? "Hörprobe erzeugen · API"
    : "▶ Anhören";
const speechHint = provider => provider === "openrouter_gemini_tts"
  ? "30 Gemini-Stimmen. Neue Hörproben nutzen dein OpenRouter-Guthaben; gespeicherte Proben werden wiederverwendet."
  : "Qwen verwendet die installierte Spracherzeugung auf deinem Computer. Vorhandene Hörproben werden direkt abgespielt.";
const currentAudio = () => project?.audio_settings || {provider:"qwen3_local",voices:(project?.config||boot.defaults).voice_profile};
const audioCatalog = () => boot.audio_catalog || {qwen3_local:{label:"Qwen · auf diesem Computer",voices:boot.voices,defaults:boot.defaults.voice_profile}};
const mediaUrl = path => "/media/"+encodeURIComponent(project.id)+"/"+path.split("/").map(encodeURIComponent).join("/");
const running = () => submitting || project?.job?.status === "running" || (project?.audio_jobs||[]).some(j=>j.status==="running");
const disabled = () => running() ? "disabled" : "";
function audioBlockReason(episode=project?.episodes?.[episodeIndex]?.script?.episode_id) {
  if(submitting)return "Der Auftrag wird gestartet.";
  if(!boot.capabilities?.parallel_audio||currentAudio().provider!=="openrouter_gemini_tts")
    return running()?"Ein Auftrag läuft bereits.":"";
  const active=(project.audio_jobs||[]).filter(j=>j.status==="running");
  if(active.some(j=>j.episode===episode))return "Diese Folge wird bereits vertont.";
  if(project.job?.status==="running"&&!active.some(j=>j.id===project.job.id))return "Zuerst den laufenden Auftrag abschließen oder anhalten.";
  if(project.audio_capacity?.available===0)return "Alle Plätze sind belegt. Sobald eine Folge fertig ist, kannst du die nächste starten.";
  return "";
}
function notice(message) { $("notice").textContent = message; $("notice").hidden = !message; }
async function api(path, data) {
  const options = data === undefined ? {} : {method:"POST",headers:{"Content-Type":"application/json","X-Studio-Token":boot.token},body:JSON.stringify(data)};
  const response = await fetch(path, options);
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || "Anfrage fehlgeschlagen.");
  return result;
}
async function attempt(action) { try { notice(""); await action(); } catch(error) { notice(error.message); } }
function textInput(id,label,value,type="text") { return `<div class="field"><label for="${id}">${label}</label><input id="${id}" type="${type}" value="${escape(value)}"></div>`; }
function area(id,label,value,rows=3) { return `<div class="field"><label for="${id}">${label}</label><textarea id="${id}" rows="${rows}">${escape(value)}</textarea></div>`; }
function heading(n,title,subtitle) { return `<div class="eyebrow">${String(n).padStart(2,"0")} / ${steps[n-1].toUpperCase()}</div><h1>${title}</h1><p class="intro">${subtitle}</p>`; }
function empty(title,body,button,target) { return `<section class="empty"><h2>${title}</h2><p>${body}</p><button data-step="${target}">${button}</button></section>`; }
const currentRun = (p=project) => p?.job?.run || p?.run;
function readableScripts(p=project) {
  const rows=new Map((p?.episodes||[]).map(e=>[e.script.episode_id,{...e,preview:false,state:"published"}]));
  const run=currentRun(p);
  const previews=p?.script_previews??p?.job?.progress?.script_previews??[];
  if(run?.kind==="script"&&run.status!=="completed")for(const e of previews){
    if(e.preview===true&&e.run_id===run.run_id&&e.script?.episode_id)rows.set(e.script.episode_id,e);
  }
  return [...rows.values()].sort((a,b)=>a.script.episode_id.localeCompare(b.script.episode_id));
}
const readerVersion = e => JSON.stringify([e?.run_id,e?.hash,e?.readable_hash,e?.state,e?.preview]);
const scriptCollectionKey = p => JSON.stringify(readableScripts(p).map(e=>[e.script.episode_id,readerVersion(e)]));
const approvedOutline = (p=project) => !!p?.outline?.hash && p.outline.approval?.plan_hash===p.outline.hash;
const hasProduction = run => !!run && productionStages.some(([name])=>run.stages?.[name]?.attempts>0 || ["running","completed","blocked","failed","waiting_for_quota"].includes(run.stages?.[name]?.status));
function runPage(run,p=project) {
  if(run?.kind==="research")return PAGE.research;
  if(run?.kind==="episode_audio")return PAGE.audio;
  if(run?.kind==="script") {
    if(run.status==="completed")return PAGE.scripts;
    return hasProduction(run)||approvedOutline(p)?PAGE.production:PAGE.outline;
  }
  return null;
}
function jobPage(p=project) {
  const action=p?.job?.action;
  return ({assistant:PAGE.brief,check:PAGE.brief,audio_sample:PAGE.brief,audio_samples:PAGE.brief,
    research:PAGE.research,plan:PAGE.outline,replan:PAGE.outline,script:PAGE.production,
    revise:PAGE.production,audio:PAGE.audio}[action]??runPage(currentRun(p),p));
}
function recommendedPage(p=project) {
  if(!p)return PAGE.brief;
  const job=p.job, run=currentRun(p);
  if(job&&job.status!=="completed") {
    if(job.status==="review_ready")return PAGE.outline;
    const destination=jobPage(p);
    if(destination!==null)return destination;
    return ({research:PAGE.research,plan:PAGE.outline,replan:PAGE.outline,script:PAGE.production,
      revise:PAGE.production,audio:PAGE.audio}[job.action]??PAGE.brief);
  }
  if(job?.run&&runPage(job.run,p)!==null)return runPage(job.run,p);
  if((p.episodes||[]).some(e=>e.audio?.length))return PAGE.audio;
  if(readableScripts(p).length)return PAGE.scripts;
  const destination=runPage(run,p);
  if(destination!==null)return destination;
  if(p.outline)return approvedOutline(p)?PAGE.production:PAGE.outline;
  return p.research?PAGE.research:PAGE.brief;
}
function navigationStates() {
  const run=currentRun(), destination=runPage(run), busy=project?.job?.status==="running";
  const state=project?.job?.status||run?.status;
  const blocked=["blocked","failed","interrupted","waiting_for_quota"].includes(state);
  const producing=destination===PAGE.production&&run?.status!=="completed";
  const audioReady=(project?.episodes||[]).some(e=>e.audio_current&&e.audio?.length);
  const readable=readableScripts().length;
  const rows=[
    [project?"Gespeichert":"Hier beginnen",project?"done":"ready"],
    [project?.research?"Dossier vorhanden":"Quellen und Grundlagen",project?.research?"done":"pending"],
    [approvedOutline()?"Freigegeben":project?.outline?"Deine Freigabe":"Nach der Recherche",approvedOutline()?"done":project?.outline?"decision":"pending"],
    [run?.kind==="script"&&run.status==="completed"?"Abgeschlossen":approvedOutline()?"Automatische Schritte":"Nach der Planfreigabe",run?.kind==="script"&&run.status==="completed"?"done":"pending"],
    [readable?`${readable} ${readable===1?"Folge lesbar":"Folgen lesbar"}`:"Sobald ein Entwurf fertig ist",readable?"decision":"pending"],
    [audioReady?"Aufnahmen vorhanden":project?.episodes?.length?"Deine Audio-Freigabe":"Nach deiner Durchsicht",audioReady?"done":project?.episodes?.length?"decision":"pending"],
  ];
  const activePage=jobPage()??destination;
  if(activePage!==undefined&&activePage!==null&&(busy||blocked))rows[activePage]=[busy?"Läuft automatisch":state==="waiting_for_quota"?"Anbieterlimit":"Angehalten",busy?"running":"blocked"];
  return rows;
}
function updatePageUrl(push=false) {
  const url=overviewPage?"/":project?`/?project=${encodeURIComponent(project.id)}&step=${pageKeys[step]}`:"/?new=1";
  const history=window.history;
  if(push&&history?.pushState)history.pushState(null,"",url);
  else history?.replaceState(null,"",url);
}
function navigatePage(target,{automatic=false,push=true}={}) {
  if(!Number.isInteger(target)||target<0||target>=steps.length)return;
  navigationEpoch++;
  overviewPage=false;step=target;followWorkflow=automatic;updatePageUrl(push);render();
}
function renderNavigation() {
  $("steps").hidden=overviewPage;
  if(overviewPage){$("project-title").textContent="Alle Projekte & Podcasts";return;}
  const states=navigationStates();
  $("steps").innerHTML = steps.map((name,i)=>`<button class="step ${states[i][1]}" data-step="${i}" ${i===step?'aria-current="page"':""}><span class="step-number" aria-hidden="true">${states[i][1]==="done"?"✓":i+1}</span><span class="step-label">${name}<small>${states[i][0]}</small></span></button>`).join("");
  $("project-title").textContent = project?.config.topic || "Neues Podcast-Projekt";
}
function renderVoiceLibrary(language) {
  const voices=audioCatalog().openrouter_gemini_tts?.voices||[], ready=voices.filter(v=>savedSample(v,language)).length;
  return `<div class="panel-title"><h2>Gemini-Stimmen zum Vergleichen</h2><span class="tag">${ready} / ${voices.length} gespeichert</span></div><p class="hint">${language==="de-DE"?"Deutsch":"English"} · Alle Stimmen lesen denselben Text. Gespeicherte Proben bleiben auch für andere Projekte verfügbar. Abspielen benötigt keinen API-Key.</p>
    <div class="voice-library">${voices.map(v=>`<div class="sample-row"><strong>${escape(v)}</strong>${savedSample(v,language)?`<button type="button" class="secondary small" data-play-voice="${escape(v)}" data-language="${language}" aria-label="Hörprobe ${escape(v)} abspielen">▶ Play</button>`:'<span class="hint">Noch keine Probe</span>'}</div>`).join("")}</div>
    ${ready<voices.length?`<button type="button" data-action="audio_samples" ${disabled()}>${voices.length-ready} fehlende Hörproben erzeugen · API</button><p class="hint">Nutzt dein OpenRouter-Guthaben. Fertige Stimmen werden übersprungen. Nach einem Abbruch erzeugt derselbe Button nur die fehlenden Proben.</p>`:'<p class="hint">✓ Alle Hörproben sind gespeichert. Wähle deine Favoriten oben für die beiden Rollen aus.</p>'}`;
}
function syncPlayButtons() {
  const player=$("sample-player");
  for(const button of document.querySelectorAll("[data-play-voice]")){
    const active=playingSample?.voice===button.dataset.playVoice&&playingSample?.language===button.dataset.language&&!player.paused;
    button.textContent=active?"❚❚ Pause":"▶ Play";
    button.setAttribute("aria-label",`Hörprobe ${button.dataset.playVoice} ${active?"pausieren":"abspielen"}`);
    button.setAttribute("aria-pressed",String(active));
  }
}
function refreshVoiceLibrary() {
  if(step!==PAGE.brief)return;
  const {config,audio}=setupSelection();
  if($("voice-library-panel")&&audio.provider==="openrouter_gemini_tts")
    $("voice-library-panel").innerHTML=renderVoiceLibrary(config.language);
  syncPlayButtons();
}
async function playSample(voice, language, provider="openrouter_gemini_tts") {
  const url=provider==="openrouter_gemini_tts"?savedSample(voice,language)?.url:`/samples/${language}/${voice.toLowerCase()}`;
  if(!url)throw new Error("Für diese Stimme ist noch keine gespeicherte Hörprobe verfügbar.");
  const player=$("sample-player");
  if(playingSample?.url===url&&!player.paused){player.pause();syncPlayButtons();return;}
  if(playingSample?.url!==url){player.src=url;playingSample={voice,language,url};}
  $("sample-playback").hidden=false;
  $("sample-playing-label").textContent=`Hörprobe: ${voice} · ${language==="de-DE"?"Deutsch":"English"}`;
  try{await player.play();syncPlayButtons();}catch{throw new Error("Die Hörprobe konnte nicht abgespielt werden. Prüfe, ob die Aufnahme noch vorhanden ist.");}
}
function defaultTextChoice(provider="codex_cli") {
  return provider==="codex_cli"?{provider,model:"gpt-6-astra",reasoning_effort:"xhigh",max_output_tokens:32768}:
    {provider,model:"",reasoning_effort:null,max_output_tokens:32768};
}
function renderRunTextChoice(job) {
  if(!["script","research"].includes(job?.run?.kind))return "";
  const t=job.text_generation;
  return `<p class="hint">Für diesen Auftrag gespeichert: ${t?.model?escape(t.model):"Modell nicht festgelegt"} · Reasoning: ${t?.reasoning_effort?escape(t.reasoning_effort):"nicht festgelegt"}.</p>`;
}
function setupSelection() {
  const proposal=[...(project?.chat||[])].reverse().find(m=>m.role==="assistant");
  const draft=project?.proposal_applied?null:proposal;
  const config={...(project?.config||boot.defaults),...Object.fromEntries(Object.entries(draft||{}).filter(([key,value])=>value!==null||key==="target_total_minutes"))};
  return {proposal,config,text:draft?.text||project?.text||boot.text_defaults||defaultTextChoice(),
    audio:draft?.audio_settings||currentAudio(),
    execution:draft?.execution||project?.execution||{text:"sequential",audio:"sequential"}};
}
function setupSummary() {
  const {proposal,config:c,text:t,audio:a,execution:x}=setupSelection();
  if(!project)return "";
  const mode=value=>value==="parallel"?"Parallel · bis zu 3 Folgen":"Sequenziell";
  return `<section class="panel"><div class="panel-title"><h2>${proposal&&!project.proposal_applied?"Deine Auswahl · Vorschlag":"Dein gespeicherter Auftrag"}</h2></div>
    <dl><dt>Thema</dt><dd>${escape(c.topic)}</dd><dt>Leitfrage</dt><dd>${escape(c.central_question||"Noch zu klären")}</dd>
    <dt>Sprache und Umfang</dt><dd>${c.language==="en-US"?"English":"Deutsch"} · ${c.target_total_minutes?escape(c.target_total_minutes)+" Minuten":"Länge nach Erklärbedarf"}</dd>
    <dt>Vorwissen und Tiefe</dt><dd>${escape(c.prior_knowledge||"Keine besonderen Vorkenntnisse")} · ${escape(c.depth_request)}</dd>
    ${c.focus_questions?.length?`<dt>Schwerpunkte</dt><dd>${c.focus_questions.map(escape).join(" · ")}</dd>`:""}
    ${c.excluded_topics?.length?`<dt>Ausgenommen</dt><dd>${c.excluded_topics.map(escape).join(" · ")}</dd>`:""}
    ${c.seed_urls?.length?`<dt>Quellenlinks</dt><dd>${c.seed_urls.map(escape).join(" · ")}</dd>`:""}
    <dt>Textmodell</dt><dd>${t.provider==="codex_cli"?"Codex · Abo":"OpenRouter · API"} · ${escape(t.model||"Standard")} · Reasoning: ${escape(t.reasoning_effort||"Standard")}</dd>
    ${t.provider==="openrouter"?'<dt>Live-Recherche</dt><dd>Weiterhin Codex · Textarbeit wird separat über OpenRouter abgerechnet.</dd>':""}
    <dt>Stimmen</dt><dd>${a.provider==="qwen3_local"?"Qwen · lokal":"Gemini · OpenRouter"} · ${escape(a.voices.host_a)} &amp; ${escape(a.voices.host_b)}</dd>
    <dt>Textausarbeitung</dt><dd>${mode(x.text)}</dd><dt>Vertonung</dt><dd>${a.provider==="qwen3_local"?"Sequenziell · lokale Grafikkarte":mode(x.audio)}</dd></dl>
    <p class="hint">Änderungswünsche schreibst du dem Partner. Parallel gilt für Skript, Polishing und Prüfung; Recherche und Lehrkonzept bleiben in Reihenfolge. Bestehende Textaufträge behalten beim Fortsetzen ihren Modus.</p>
    ${proposal&&!project.proposal_applied?`<button data-action="apply-proposal" ${running()||setupSending||pendingAttachments.length||project.proposal_current===false||!boot.capabilities?.conversational_setup?"disabled":""}>Diese Auswahl übernehmen</button><p class="hint">${project.proposal_current===false?"Die Anhänge haben sich geändert. Bitte den Partner im Chat die Zusammenfassung aktualisieren lassen.":"Das speichert den Auftrag. Recherche, Plan- und Audiofreigabe erfolgen weiterhin auf den folgenden Seiten."}</p>`:""}
    </section>`;
}
function renderAttachments() {
  const saved=project?.attachments||[];
  return `<ul class="attachment-list">${saved.map(row=>`<li><span><strong>${escape(row.name)}</strong><small>Gespeichert · Projektidee & Recherche${row.characters<200?" · kurze Notiz":""}</small></span><button type="button" class="secondary small" data-remove-attachment="${escape(row.id)}" ${disabled()} aria-label="${escape(row.name)} aus den aktiven Anhängen entfernen">Entfernen</button></li>`).join("")}
    ${pendingAttachments.map((row,index)=>`<li><span><strong>${escape(row.name)}</strong><small>Wird mit deiner Nachricht hochgeladen</small></span><button type="button" class="secondary small" data-remove-pending="${index}" aria-label="${escape(row.name)} nicht hochladen">Entfernen</button></li>`).join("")}</ul>`;
}
function refreshAttachmentComposer() {
  if(step!==PAGE.brief||overviewPage)return;
  const draft=$("chat-message")?.value||"";
  render();
  $("chat-message").value=draft;
}
async function queueAttachments(files) {
  if(running()||setupSending||readingAttachments)return;
  if(!boot.capabilities?.project_attachments)throw new Error("Bitte das Studio nach Ende laufender Aufträge neu starten, um Dateien anzuhängen.");
  const limits=boot.attachment_limits||{files:10,file_bytes:262144,docx_bytes:2097152,transfer_bytes:4194304,total_bytes:1048576};
  const selected=Array.from(files), epoch=navigationEpoch;
  if(!selected.length)return;
  if(pendingAttachments.length+selected.length>limits.files)throw new Error("Bitte höchstens 10 Dateien auf einmal auswählen.");
  if(selected.some(file=>! /\.(md|txt|docx)$/i.test(file.name)||!file.size||file.size>(/\.docx$/i.test(file.name)?limits.docx_bytes:limits.file_bytes)))
    throw new Error("Bitte .md, .txt (bis 256 KiB) oder .docx (bis 2 MiB) mit lesbarem Text wählen.");
  if(pendingAttachments.reduce((sum,file)=>sum+file.bytes,0)+selected.reduce((sum,file)=>sum+file.size,0)>limits.transfer_bytes)
    throw new Error("Bitte höchstens 4 MiB auf einmal anhängen.");
  readingAttachments=true;refreshAttachmentComposer();
  try{
    const added=[];
    for(const file of selected){
      const bytes=new Uint8Array(await file.arrayBuffer());
      let binary="";
      for(let i=0;i<bytes.length;i+=8192)binary+=String.fromCharCode(...bytes.subarray(i,i+8192));
      added.push({name:file.name,base64:btoa(binary),bytes:file.size});
    }
    if(epoch===navigationEpoch)pendingAttachments.push(...added);
  }finally{readingAttachments=false;refreshAttachmentComposer();}
}
async function removeAttachment(id) {
  if(running()||setupSending)return;
  const projectId=project.id;
  await api(`/api/projects/${projectId}/remove_attachment`,{id});
  const updated=await api(`/api/projects/${projectId}`);
  if(project?.id===projectId){project=updated;refreshAttachmentComposer();}
}
function renderBrief() {
  const {proposal,config:c,audio:a,text:t}=setupSelection(), chat=project?.chat||[];
  const compatible=boot.capabilities?.conversational_setup;
  const nextPage=recommendedPage()===PAGE.brief?PAGE.research:recommendedPage();
  const voices=audioCatalog()[a.provider]?.voices||[];
  return heading(1,"Dein redaktioneller Partner.","Beschreibe deinen Wunsch. Dein Partner fragt nach, bis Thema, Tiefe, Stimmen und Arbeitsweise passen.")+
    `${!compatible?'<p class="note">Die Gesprächseinrichtung benötigt einen Studio-Neustart. Lass den laufenden Auftrag fertigarbeiten, beende dann das Studio und öffne es erneut.</p>':""}
    <section class="panel"><div class="conversation">${chat.length?chat.map(m=>`<div class="chat-message ${m.role==="user"?"user":""}"><strong>${m.role==="user"?"Du":"Redaktion"}</strong><p>${escape(m.message)}</p></div>`).join(""):'<div class="chat-message"><strong>Redaktion</strong><p>Worum soll dein Podcast gehen – und was möchtest du danach besser verstehen? Du kannst direkt auch Wünsche zu Sprache, Tiefe oder Stimmen nennen.</p></div>'}</div>
    ${!running()&&proposal?.suggested_replies?.length?`<div class="actions">${proposal.suggested_replies.map(reply=>`<button class="secondary small" data-setup-reply="${escape(reply)}">${escape(reply)}</button>`).join("")}</div>`:""}
    <form id="chat-form"><fieldset ${running()||setupSending||readingAttachments||!compatible?"disabled":""}>${area("chat-message","Deine Nachricht","",3)}
    ${boot.text_catalog?.presets?.length?`<div class="text-model-picker"><span>Textmodell wählen</span><div class="actions">${boot.text_catalog.presets.map(p=>`<button type="button" class="secondary small" data-text-preset="${escape(p.id)}" aria-pressed="${t.provider===p.provider&&t.model===p.model&&(!p.reasoning_effort||t.reasoning_effort===p.reasoning_effort)}">${escape(p.label)}</button>`).join("")}</div><p class="hint">Die Auswahl kommt in den Vorschlag und wird mit „Diese Auswahl übernehmen“ gespeichert. OpenRouter nutzt API-Guthaben. Live-Recherche bleibt bei Codex; Stimmen wählst du separat.</p></div>`:""}
    ${boot.capabilities?.project_attachments?`<div class="attachment-picker"><label for="chat-files">Dateien anhängen · .md / .txt / .docx</label><input id="chat-files" type="file" accept=".md,.txt,.docx,text/plain,text/markdown,application/vnd.openxmlformats-officedocument.wordprocessingml.document" multiple aria-describedby="attachment-hint"><p id="attachment-hint" class="hint">Für deine Projektidee und als Ausgangsmaterial der Recherche. Bis zu 10 Dateien: Text je 256 KiB, DOCX je 2 MiB, insgesamt 1 MiB eingelesener Text. DOCX übernimmt Text und Tabellen, keine Bilder. Mit „Senden“ erhält dein Textmodell den Inhalt; bei langen Dateien zunächst gekennzeichnete Auszüge. Die Recherche liest die vollständigen Textkopien ein.</p><div id="attachment-list">${renderAttachments()}</div></div>`:'<p class="hint">Dateianhänge benötigen einen Studio-Neustart nach Ende laufender Aufträge.</p>'}
    <button type="submit">${setupSending?"Wird gesendet …":readingAttachments?"Dateien werden eingelesen …":"Senden"}</button></fieldset></form></section>
    ${setupSummary()}
    <details class="panel"><summary>Stimmen anhören</summary><p class="hint">${a.provider==="qwen3_local"?"Qwen":"Gemini"} · ${c.language==="en-US"?"English":"Deutsch"}. Sag dem Partner anschließend, welche beiden Stimmen du möchtest. Neue Gemini-Proben nutzen dein API-Guthaben.</p><div class="voice-library">${voices.map(v=>`<div class="sample-row"><strong>${escape(v)}</strong><button class="secondary small" data-preview-voice="${escape(v)}" data-preview-provider="${a.provider}" data-language="${c.language}" ${a.provider!=="qwen3_local"&&!savedSample(v,c.language)&&running()?"disabled":""}>${sampleButtonLabel(a.provider,v,c.language)}</button></div>`).join("")}</div>
    ${a.provider==="openrouter_gemini_tts"?`<div id="voice-library-panel">${renderVoiceLibrary(c.language)}</div>`:""}</details>
    <details class="panel"><summary>Geschützter OpenRouter-Key-Eingang</summary><p class="hint">Falls du OpenRouter wählst, hinterlege den Key hier. Er wird nicht an den redaktionellen Partner gesendet und bleibt nur im Sitzungsspeicher.</p>
    ${textInput("api-key","OpenRouter-Key","","password")}<p id="key-status" class="hint">${boot.key_available?"Ein Key ist für diese Sitzung verfügbar.":"Noch kein Key hinterlegt."}</p><div class="actions"><button class="secondary small" data-action="store-key">Key hinterlegen</button><button class="secondary small" data-action="forget-key">Sitzungs-Key entfernen</button></div></details>
    ${project?`<div class="actions"><button class="secondary" data-action="check" ${disabled()}>Verbindungen prüfen</button><button data-step="${nextPage}" ${proposal&&!project.proposal_applied?"disabled":""}>Weiter: ${steps[nextPage]} →</button></div>`:""}`;
}
function renderResearch() {
  let html = heading(2,"Erst verstehen. Dann erzählen.","Die Recherche sammelt belastbare Quellen, erklärt die Grundlagen und macht offene Fragen sichtbar. Sie ist die Grundlage für den roten Faden.");
  if(!project) return html+empty("Ein Thema fehlt noch.","Lege zuerst deinen Podcast-Auftrag an.","Zur Idee",0);
  if(project.attachments?.length)html+=`<section class="panel"><h2>Deine Ausgangsmaterialien</h2><ul>${project.attachments.map(row=>`<li>${escape(row.name)}</li>`).join("")}</ul><p class="hint">Diese Dateien werden als lokale Quellen eingelesen. Aussagen aus deinen Notizen werden anhand weiterer Quellen geprüft. Sehr kurze Notizen dienen vor allem der Projektbeschreibung.</p></section>`;
  html += `<section class="panel"><div class="panel-title"><h2>Quellen und Erkenntnisse</h2><span class="tag">Recherche mit Codex</span></div><p>Der gespeicherte Auftrag: <strong>${escape(project.config.central_question||project.config.topic)}</strong></p><div class="actions">${project.research?(project.outline?'<button data-step="2">Zum Inhaltsverzeichnis →</button>':`<button data-action="plan" ${disabled()}>Inhaltsverzeichnis entwerfen →</button>`):`<button data-action="research" ${disabled()}>Recherche starten</button>`}</div>${project.research?`<details class="restart-options"><summary>Recherche neu beginnen</summary><p>Das startet einen neuen Recherchelauf. Den bisherigen Stand kannst du unten lesen.</p><button class="secondary" data-action="research" ${disabled()}>Neu recherchieren</button></details>`:'<p class="hint">Quellen suchen, lesen und das Dossier prüfen läuft nach dem Start automatisch.</p>'}</section>`;
  if(project.research) html+=`<section class="panel"><h2>Dein Recherche-Dossier</h2><pre class="document">${escape(project.research)}</pre></section>`;
  return html;
}
function renderOutline() {
  let html=heading(3,"Der rote Faden, bevor wir schreiben.","Prüfe, ob die Grundlagen tragen, die Kapitel aufeinander aufbauen und das Ganze deine Frage beantwortet. Erst deine Freigabe startet die Skripte.");
  const outline=project?.outline;
  if(!outline) {
    if(project?.job?.status==="running"&&jobPage()===PAGE.outline)return html+`<section class="panel tinted"><h2>Das Inhaltsverzeichnis wird ausgearbeitet.</h2><p>Folgen und Kapitel erscheinen hier, sobald der Entwurf bereit für deine Durchsicht ist.</p></section>`;
    return html+empty("Das Inhaltsverzeichnis entsteht aus der Recherche.","Nach dem geprüften Dossier entwirft die Redaktion Folgen und Kapitel. Hier kannst du sie anschließend verändern und freigeben.","Zur Recherche",PAGE.research);
  }
  const p=outline.plan, approved=outline.approval?.plan_hash===outline.hash;
  html+=`<section class="panel tinted"><h2>${escape(p.central_question)}</h2><p>${escape(p.explanation_path)}</p><div class="outline-summary"><span><strong>${p.episodes.length}</strong> Folgen</span><span><strong>${Math.round(p.episodes.reduce((s,e)=>s+e.target_minutes,0))}</strong> Minuten geplant</span><span>${approved?"Dieser Stand wurde freigegeben":"Wartet auf deine Durchsicht"}</span></div><p class="hint">${escape(p.scope_note)}</p></section>`;
  html+=p.episodes.map((e,i)=>`<section class="panel"><div class="episode-head"><span class="episode-num">${String(i+1).padStart(2,"0")}</span><div><h2>${escape(e.title)}</h2><p>${escape(e.central_question)}</p></div><span class="tag">ca. ${Math.round(e.target_minutes)} Min.</span></div><ol class="chapters">${e.scenes.map((s,n)=>`<li><span>${String(n+1).padStart(2,"0")}</span><div><strong>${escape(s.title)}</strong><p>${escape(s.question)}</p><details><summary>Was hier erklärt wird</summary>${s.explanation_steps.map(x=>`<p>${escape(x)}</p>`).join("")}</details></div></li>`).join("")}</ol>${e.deferred_questions.length?`<details><summary>Offene oder spätere Fragen</summary>${e.deferred_questions.map(q=>`<p>${escape(q)}</p>`).join("")}</details>`:""}</section>`).join("");
  const canReplan = !Object.entries(project.job?.run?.stages || {}).some(([n,r])=>n!=="planning"&&r.attempts>0);
  html+=approved?`<section class="panel tinted"><h2>Dieser Plan ist freigegeben.</h2><p>Lehrkonzept, Skriptentwurf, Dialog-Polishing und Qualitätsprüfung gehören zur automatischen Ausarbeitung. Deine nächste inhaltliche Entscheidung triffst du beim Lesen der fertigen Skripte.</p><button data-step="${PAGE.production}">Ausarbeitung ansehen →</button></section>`:
    `<section class="panel"><h2>Passt die Dramaturgie?</h2>${area("outline-feedback","Was soll sich ändern?","",3)}<div class="actions"><button class="secondary" data-action="replan" ${running()||!canReplan?"disabled":""}>Plan überarbeiten lassen</button><button data-action="script" ${disabled()}>Plan freigeben & Skripte schreiben</button></div><p class="hint">Deine Freigabe startet die automatische Ausarbeitung. Die fertigen Skripte liest du anschließend vor der Vertonung.</p></section>`;
  if(!canReplan)html+=`<details class="restart-options"><summary>Eine neue Gliederung erstellen</summary><p>Der bisherige Auftrag bleibt gespeichert. Eine neue Gliederung benötigt wieder deine Freigabe.</p><button class="secondary" data-action="plan" ${disabled()}>Neues Inhaltsverzeichnis entwerfen</button></details>`;
  return html;
}
function renderProduction() {
  const html=heading(4,"Vom roten Faden zum fertigen Gespräch.","Hier arbeitet die Redaktion nach deiner Planfreigabe automatisch weiter. Fertige Lehrkonzepte und gespeicherte Skriptfassungen kannst du schon währenddessen lesen.");
  const run=currentRun();
  if(!approvedOutline()&&!hasProduction(run))return html+empty("Zuerst das Inhaltsverzeichnis prüfen.","Deine Freigabe startet Lehrkonzept, Schreiben, Polishing und Qualitätsprüfung als zusammenhängenden Auftrag.","Zum Inhaltsverzeichnis",PAGE.outline);
  return html+'<div id="production-progress"></div>';
}
function renderProductionDetails() {
  const run=currentRun(), scriptRun=run?.kind==="script"?run:null;
  const active=project?.job?.status==="running"&&jobPage()===PAGE.production;
  const finished=scriptRun?.status==="completed";
  const labels={completed:"Abgeschlossen",running:"In Arbeit",pending:"Folgt automatisch",blocked:"Angehalten",failed:"Angehalten",waiting_for_quota:"Wartet auf Anbieter"};
  let html=`<section class="panel"><div class="panel-title"><h2>Die Ausarbeitung</h2><span class="tag">${active?"Läuft automatisch":finished?"Bereit zum Lesen":"Gespeicherter Stand"}</span></div><ol class="production-stages">${productionStages.map(([key,title,description])=>{
    const status=scriptRun?.stages?.[key]?.status||"pending";
    return `<li class="${escape(status)}"><span class="phase-marker" aria-hidden="true">${status==="completed"?"✓":status==="running"?"●":"○"}</span><div><strong>${title}</strong><p>${description}</p></div><span class="phase-status">${labels[status]||"Ausstehend"}</span></li>`;
  }).join("")}</ol><p class="hint">Notwendige Nachrecherche und interne Korrekturen gehören zu diesen Schritten. Gespeicherte Skriptfassungen lassen sich bereits während der Ausarbeitung lesen.</p></section>`;
  html+=renderScriptProgress(project?.job?.progress,active);
  const readable=readableScripts().length;
  if(!finished&&readable)html+=`<section class="panel tinted"><h2>${readable} ${readable===1?"Folge ist bereits lesbar":"Folgen sind bereits lesbar"}.</h2><p>Du kannst die gespeicherten Texte jetzt lesen. Der Prüfstand steht bei jeder Folge; die Ausarbeitung läuft weiter.</p><button data-step="${PAGE.scripts}">Skripte jetzt lesen →</button></section>`;
  const issues=project?.job?.progress?.review_issues||[];
  if(issues.length)html+=`<section class="panel"><h2>${project?.job?.progress?.stage==="review"?"Offene Punkte der Qualitätsprüfung":"Was noch erklärt werden muss"}</h2><ul>${issues.map(issue=>`<li>${escape(typeof issue==="string"?issue:issue.reason||"Offener Prüfpunkt")}</li>`).join("")}</ul></section>`;
  if(project?.job?.research_gaps?.length)html+=`<section class="panel"><h2>Offene Erklärfragen</h2><ul>${project.job.research_gaps.map(g=>`<li><strong>${escape(g.question)}</strong><p>${escape(g.why_needed)}</p></li>`).join("")}</ul><button class="secondary" data-step="${PAGE.research}">Bisherige Recherche ansehen</button></section>`;
  if(finished)html+=`<section class="panel tinted"><h2>Die Skripte sind bereit.</h2><p>Lies die Folgen und gib bei Bedarf Rückmeldung. Anschließend entscheidest du über die Vertonung.</p><button data-step="${PAGE.scripts}">Skripte lesen →</button></section>`;
  else if(active)html+=`<p class="hint">Du kannst währenddessen andere Seiten ansehen. Der Auftrag läuft weiter.</p>`;
  return html;
}
function episodePicker() { return `<div class="field"><label for="episode-select">Folge auswählen</label><select id="episode-select">${project.episodes.map((e,i)=>`<option value="${i}" ${i===episodeIndex?"selected":""}>${i+1}. ${escape(e.script.title)}</option>`).join("")}</select></div>`; }
function readerEntries() {
  const entries=readableScripts();
  if(readingSnapshot?.projectId===project?.id&&!entries.some(e=>e.script.episode_id===readingSnapshot.entry.script.episode_id))entries.push(readingSnapshot.entry);
  return entries;
}
function renderReaderControls() {
  const entries=readerEntries(), e=readingSnapshot.entry;
  const latest=readableScripts().find(row=>row.script.episode_id===e.script.episode_id);
  const updated=latest&&readerVersion(latest)!==readerVersion(e);
  return `<div class="field"><label for="script-select">Lesbare Folge auswählen</label><select id="script-select">${entries.map(row=>`<option value="${escape(row.script.episode_id)}" ${row.script.episode_id===e.script.episode_id?"selected":""}>${escape(row.script.title)} · ${scriptStateLabels[row.state]}</option>`).join("")}</select></div>
    <p><span class="tag">Geöffneter Stand: ${scriptStateLabels[e.state]}</span></p>
    ${updated?'<p class="note">Für diese Folge gibt es einen neueren Text- oder Prüfstand. Dein geöffneter Text bleibt beim Lesen stehen.</p><button class="secondary small" data-action="refresh-script">Aktuellen Stand laden</button>':""}
    ${e.preview?`<p class="note">${e.state==="draft"?"Der Entwurf ist vollständig gespeichert. Dialog-Polishing und Qualitätsprüfungen stehen noch aus.":e.state==="polished"?"Der Dialog ist überarbeitet. Die abschließenden Qualitätsprüfungen stehen noch aus.":"Die automatischen Prüfungen dieser Fassung sind bestanden. Die Bereitstellung des Auftrags steht noch aus."} Du kannst bereits lesen; diese Vorschau hat noch keine Audio-Freigabe.</p>`:""}
    <p class="hint">Weitere Folgen erscheinen hier automatisch, sobald ein vollständiger Entwurf gespeichert ist.</p>`;
}
function refreshScriptReader() {
  if(!readingSnapshot||!$("script-reader-controls")){$("content").innerHTML=renderScript();return;}
  // Only the picker and status change. Keep the text DOM, selection and feedback intact.
  $("script-reader-controls").innerHTML=renderReaderControls();
}
function renderScript() {
  let html=heading(5,"Lies das Gespräch in deinem Tempo.","Jede gespeicherte Folge wird hier einzeln lesbar. Du siehst, ob du einen Entwurf, einen überarbeiteten Dialog oder eine geprüfte Fassung liest.");
  const entries=readerEntries();
  if(!entries.length) return html+(approvedOutline()||hasProduction(currentRun())?
    empty("Der erste Skriptentwurf entsteht noch.","Diese Seite aktualisiert sich automatisch, sobald ein vollständiger Entwurf gespeichert ist. Die übrigen Folgen dürfen währenddessen weiterlaufen.","Zur Ausarbeitung",PAGE.production):
    empty("Zuerst den roten Faden festlegen.","Prüfe das Inhaltsverzeichnis und gib es frei. Danach entsteht das vollständige Gespräch.","Zum Inhaltsverzeichnis",PAGE.outline));
  const selected=entries.find(e=>e.script.episode_id===scriptEpisodeId)||entries[0];
  scriptEpisodeId=selected.script.episode_id;
  if(readingSnapshot?.projectId!==project.id||readingSnapshot.entry.script.episode_id!==scriptEpisodeId)readingSnapshot={projectId:project.id,entry:structuredClone(selected)};
  const e=readingSnapshot.entry,s=e.script;
  if(!e.preview)episodeIndex=Math.max(0,project.episodes.findIndex(row=>row.script.episode_id===s.episode_id));
  html+=`<div id="script-reader-controls">${renderReaderControls()}</div><div class="outline-summary"><span>${e.metrics.words.toLocaleString("de-DE")} Wörter</span><span>ca. ${Math.round(e.metrics.estimated_minutes)} Min. geschätzt</span><span>${s.chapters.length} Kapitel</span></div><article id="script-text" class="panel reader"><h1>${escape(s.title)}</h1>`;
  for(const chapter of s.chapters) html+=`<h2>${escape(chapter.title)}</h2>`+s.segments.filter(x=>x.chapter_id===chapter.chapter_id).map(x=>`<div class="utterance ${x.speaker_id}"><strong>${escape(project.config.voice_profile[x.speaker_id])}</strong><p>${escape(x.text)}</p></div>`).join("");
  html+='</article>';
  if(e.preview)html+=`<p class="hint">Nach Abschluss der Ausarbeitung kannst du Rückmeldung für eine weitere Überarbeitung geben und über die Vertonung entscheiden.</p><button class="secondary" data-step="${PAGE.production}">Ausarbeitung verfolgen</button>`;
  else html+=`<section class="panel"><h2>Deine redaktionelle Rückmeldung</h2>${area("script-feedback","Was fehlt oder klingt noch nicht richtig?","",4)}<div class="actions"><button class="secondary" data-action="revise" ${disabled()}>Diese Folge überarbeiten lassen</button><button data-step="${PAGE.audio}">Weiter zur Audio-Freigabe →</button></div><p class="hint">Eine Überarbeitung durchläuft erneut Polishing und Prüfung. Sie erhält eine neue Audio-Freigabe.</p></section>`;
  return html;
}
function renderAudio() {
  let html=heading(6,"Vom Text zum Gespräch.","Gib eine gelesene Folge mit dem gewählten Audioanbieter ausdrücklich frei. Fertige Abschnitte bleiben für eine Fortsetzung gespeichert.");
  if(!project?.episodes?.length) return html+empty("Zuerst braucht es ein fertiges Skript.","Deine Freigabe gehört immer zu dem Text, den du tatsächlich gelesen hast.","Zu den Skripten",PAGE.scripts);
  episodeIndex=Math.min(episodeIndex,project.episodes.length-1);
  const e=project.episodes[episodeIndex];
  const a=currentAudio(),remote=a.provider==="openrouter_gemini_tts",blocked=audioBlockReason();
  html+=episodePicker()+`<section class="panel"><div class="panel-title"><h2>${escape(e.script.title)}</h2><span class="tag">${remote?"Gemini 3.1 Flash TTS · OpenRouter":"Qwen · lokal"}</span></div><p>${escape(a.voices.host_a)} & ${escape(a.voices.host_b)} · ${project.config.language==="de-DE"?"Deutsch":"English"}</p><p class="hint">${remote?"Gemini erzeugt die Sprache über OpenRouter und nutzt dafür dein API-Guthaben. Deine Grafikkarte wird für die Vertonung nicht benötigt.":"Qwen erzeugt die Sprache auf deinem Computer und beansprucht deine Grafikkarte."} Das Browserfenster darf geschlossen werden; der Studio-Server muss geöffnet bleiben.</p><button class="secondary small" data-step="0">Audioanbieter oder Stimmen ändern</button><label class="approval"><input id="audio-approval" type="checkbox" ${blocked?"disabled":""}><span>Ich habe dieses Skript gelesen und gebe diesen Stand mit dem angezeigten Audioanbieter und den Stimmen für Audio frei.${remote?" Ich möchte die API-Vertonung starten.":""}</span></label><div class="actions"><button id="audio-start" data-action="audio" disabled>Audio erzeugen</button><button class="secondary" data-step="${PAGE.scripts}">Skript nochmals lesen</button></div></section>`;
  if(blocked)html+=`<p class="hint">${escape(blocked)}</p>`;
  if(remote)html+=boot.capabilities?.parallel_audio?`<p class="hint">${project.execution?.audio==="parallel"?"Parallel":"Sequenziell"} · ${project.audio_capacity?.active||0} von ${project.audio_capacity?.limit||1} Plätzen belegt. Weitere gelesene Folgen kannst du oben auswählen und einzeln freigeben.</p>`:'<p class="note">Parallele Vertonung benötigt einen Studio-Neustart nach Ende des laufenden Auftrags.</p>';
  if(project.episodes.some(e=>e.audio.length))html+='<button class="secondary" data-action="overview">Alle fertigen Folgen anhören →</button>';
  return html;
}
function overviewStatus(p) {
  if(p.unavailable)return "Projekt konnte nicht gelesen werden.";
  const active=[p.job,...(p.audio_jobs||[])].filter((j,i,all)=>j?.status==="running"&&all.findIndex(other=>other?.id===j.id)===i);
  if(active.length)return active.length>1?`${active.length} Vertonungen laufen`:(active[0].progress?.activity||actionNames[active[0].action]||"Auftrag läuft");
  if(["blocked","failed","interrupted","waiting_for_quota","pending"].includes(p.job?.status))return "Auftrag angehalten · gespeicherten Stand öffnen";
  if(p.episodes?.some(e=>e.audio?.length))return "Podcast verfügbar";
  if(p.script_count)return `${p.script_count} Skripte fertig`;
  return p.has_outline?"Inhaltsverzeichnis vorhanden":p.has_research?"Recherche vorhanden":"Auftrag vorbereiten";
}
function podcastCard(p,e,index) {
  const url=path=>"/media/"+encodeURIComponent(p.id)+"/"+path.split("/").map(encodeURIComponent).join("/");
  const download=path=>boot.capabilities?.podcast_downloads?"/download/"+encodeURIComponent(p.id)+"/file/"+path.split("/").map(encodeURIComponent).join("/"):url(path);
  const number=Number(/^ep_(\d+)$/.exec(e.episode_id)?.[1])||index+1;
  const shortName=(text,max)=>Array.from(String(text).normalize("NFC").replace(/[<>:"/\\|?*\x00-\x1f]/g," ").replace(/\s+/g," ").trim()).slice(0,max).join("").replace(/[ .-]+$/,"")||"Podcast";
  const fallbackName=i=>`${shortName(p.topic||"Podcast",32)} - Folge ${String(number).padStart(2,"0")} - ${shortName(e.title,56)}${e.audio.length>1?` - Teil ${i+1}`:""}.mp3`;
  return `<article class="podcast-episode" id="podcast-${escape(p.id)}-${escape(e.episode_id)}" data-audio-version="${escape(JSON.stringify(e.audio))}"><h3>Folge ${number}: ${escape(e.title)}</h3>
    <p class="hint" id="recording-status-${escape(p.id)}-${escape(e.episode_id)}">${e.audio_current?"":"Aufnahme eines früheren Skript- oder Stimmenstands."}</p>
    ${e.audio.map((path,i)=>`<div class="audio-track"><strong>${e.audio.length>1?"Teil "+(i+1)+" von "+e.audio.length:"Podcast abspielen"}</strong><audio controls preload="none" src="${url(path)}"></audio><a href="${download(path)}" download="${escape(fallbackName(i))}">Folge ${number}${e.audio.length>1?` · Teil ${i+1}`:""} als MP3 laden</a></div>`).join("")}</article>`;
}
function podcastDownload(p) {
  const finished=(p.episodes||[]).filter(e=>e.audio?.length).length,total=p.episode_count||p.episodes?.length||0;
  if(!finished)return '<p class="hint">Sobald eine Folge fertig vertont ist, kannst du sie hier herunterladen.</p>';
  if(!boot.capabilities?.podcast_downloads)return '<p class="hint">Für den ZIP-Download das Studio nach Ende laufender Aufträge einmal neu starten. Einzelne Folgen kannst du bereits laden.</p>';
  const complete=finished===total;
  return `<a class="download-all" href="/download/${encodeURIComponent(p.id)}/podcast.zip" download>${complete?"Gesamten Podcast herunterladen":"Fertige Folgen herunterladen"} <span>ZIP · ${complete?finished:`${finished} von ${total}`} Folgen</span></a>
    <p class="hint">${complete?"Alle Folgen":"Die bisher fertigen Folgen"} als einzelne MP3s mit kurzen Dateinamen: Folgennummer und Episodentitel.${(p.episodes||[]).some(e=>e.audio?.length&&!e.audio_current)?" Enthält auch die unten gekennzeichneten älteren Aufnahmen.":""}</p>`;
}
function overviewCard(p) {
  return `<section class="panel" id="project-card-${escape(p.id)}" data-project-card="${escape(p.id)}"><div class="panel-title"><h2>${escape(p.topic)}</h2><span class="tag" id="project-state-${escape(p.id)}">${escape(overviewStatus(p))}</span></div>
    <div class="actions"><button data-open-project="${escape(p.id)}">Projekt öffnen</button><button class="secondary small" data-delete-project="${escape(p.id)}" ${p.unavailable||p.job?.status==="running"||(p.audio_jobs||[]).some(j=>j.status==="running")||!boot.capabilities?.project_overview?"disabled":""}>Projekt löschen</button></div>
    <div class="podcast-download" id="project-download-${escape(p.id)}">${podcastDownload(p)}</div>
    <div id="podcasts-${escape(p.id)}">${(p.episodes||[]).map((e,i)=>e.audio?.length?podcastCard(p,e,i):"").join("")}</div>
    <p class="hint" id="project-audio-count-${escape(p.id)}">${(p.episodes||[]).filter(e=>e.audio?.length).length} fertige Folgen zum Anhören.</p></section>`;
}
function trashMarkup() {
  return overviewData.trash?.length?`<details class="panel"><summary>Papierkorb · ${overviewData.trash.length} Projekte</summary>${overviewData.trash.map(p=>`<div class="sample-row"><span>${escape(p.topic)}</span><button class="secondary small" data-restore-project="${escape(p.id)}">Wiederherstellen</button></div>`).join("")}</details>`:"";
}
function renderOverview() {
  return `<div class="eyebrow">DEIN PODCAST STUDIO</div><h1>Deine Projekte &amp; Podcasts.</h1><p class="intro">Arbeitsstände verfolgen, weiterarbeiten und fertige Folgen anhören – auch während die nächste Folge entsteht.</p>
    <button data-new-project>＋ Neues Projekt</button>
    <div id="overview-projects">${overviewData.projects.length?overviewData.projects.map(overviewCard).join(""):'<p id="overview-empty" class="hint">Dein erstes Projekt beginnt mit einem Gespräch.</p>'}</div><div id="overview-trash">${trashMarkup()}</div>`;
}
async function loadOverview() {
  if(boot.capabilities?.project_overview)return await api("/api/projects");
  const details=await Promise.all(boot.projects.map(p=>api("/api/projects/"+encodeURIComponent(p.id))));
  return {projects:details.map(p=>({id:p.id,topic:p.config.topic,job:p.job,script_count:p.episodes.length,has_research:!!p.research,has_outline:!!p.outline,
    episodes:p.episodes.map(e=>({...e,episode_id:e.script.episode_id,title:e.script.title}))})),trash:[]};
}
async function showOverview() {
  if(setupSending)throw new Error("Die Nachricht wird gerade gesendet. Bitte kurz warten.");
  const epoch=++navigationEpoch,data=await loadOverview();if(epoch!==navigationEpoch)return;
  overviewData=data;overviewPage=true;project=null;followWorkflow=false;pendingAttachments=[];
  $("project-select").value="";updatePageUrl();render();
}
function refreshOverview() {
  const container=$("overview-projects");
  if(!container)return;
  for(const card of container.querySelectorAll("[data-project-card]"))
    if(!overviewData.projects.some(p=>p.id===card.dataset.projectCard))card.remove();
  if(overviewData.projects.length&&$("overview-empty"))$("overview-empty").hidden=true;
  for(const p of overviewData.projects){
    const card=$("project-card-"+p.id);
    if(!card){container.insertAdjacentHTML("beforeend",overviewCard(p));continue;}
    $("project-state-"+p.id).textContent=overviewStatus(p);
    const button=card.querySelector("[data-delete-project]");
    if(button)button.disabled=p.unavailable||p.job?.status==="running"||(p.audio_jobs||[]).some(j=>j.status==="running")||!boot.capabilities?.project_overview;
    $("project-audio-count-"+p.id).textContent=`${(p.episodes||[]).filter(e=>e.audio?.length).length} fertige Folgen zum Anhören.`;
    const download=$("project-download-"+p.id),downloadContent=podcastDownload(p);
    if(download&&download.innerHTML!==downloadContent)download.innerHTML=downloadContent;
    (p.episodes||[]).forEach((e,i)=>{
      if(!e.audio?.length)return;
      const audioCard=$("podcast-"+p.id+"-"+e.episode_id);
      if(!audioCard){
        const following=p.episodes.slice(i+1).map(next=>$("podcast-"+p.id+"-"+next.episode_id)).find(Boolean);
        if(following)following.insertAdjacentHTML("beforebegin",podcastCard(p,e,i));
        else $("podcasts-"+p.id).insertAdjacentHTML("beforeend",podcastCard(p,e,i));
        return;
      }
      const label=$("recording-status-"+p.id+"-"+e.episode_id);
      if(label)label.textContent=e.audio_current?"":"Aufnahme eines früheren Skript- oder Stimmenstands.";
      if(audioCard.dataset.audioVersion!==JSON.stringify(e.audio)&&
          ![...audioCard.querySelectorAll("audio")].some(audio=>!audio.paused))audioCard.outerHTML=podcastCard(p,e,i);
    });
  }
  const trash=$("overview-trash"),content=trashMarkup();
  if(trash.innerHTML!==content)trash.innerHTML=content;
}
function renderAudioJobs() {
  const jobs=project?.audio_jobs||[];
  const labels={running:"Wird vertont",completed:"Fertig zum Anhören",interrupted:"Angehalten",pending:"Angehalten",blocked:"Braucht Aufmerksamkeit",failed:"Fehlgeschlagen",waiting_for_quota:"Anbieterlimit"};
  return jobs.map(j=>{
    const e=project.episodes?.find(e=>e.script.episode_id===j.episode),p=j.progress,active=j.status==="running";
    const canResume=!active&&j.status!=="completed"&&j.run;
    return `<section class="audio-job"><div class="job-top"><strong>${escape(e?.script.title||j.episode)} · ${escape(labels[j.status]||j.status)}</strong>
    ${active?`<button class="danger small" data-action="stop" data-job-id="${escape(j.id)}">Diese Folge anhalten</button>`:canResume?`<button class="secondary small" data-action="resume" data-run-id="${escape(j.run.run_id)}" data-episode="${escape(j.episode)}" ${audioBlockReason(j.episode)?"disabled":""}>Diese Folge fortsetzen</button>`:""}</div>
    ${j.message?`<p>${escape(j.message)}</p>`:""}
    ${active&&p?.total_segments!==undefined?`<p>${Number(p.completed_segments)} von ${Number(p.total_segments)} Sprechabschnitten fertig</p><progress value="${Number(p.completed_segments)}" max="${Number(p.total_segments)}"></progress>`:""}
    ${j.status==="completed"?'<button class="secondary small" data-action="overview">Podcast anhören</button>':""}</section>`;
  }).join("");
}

function renderScriptProgress(p, active) {
  if(p?.phase!=="script")return "";
  active=active&&!progressStale(p);
  const elapsed=p.activity_started_at?Math.max(0,Math.floor((Date.now()-Date.parse(p.activity_started_at))/60000)):null;
  return `<section class="script-progress"><p class="current-episode"><strong>${p.current_episode?`Folge ${Number(p.episode_number)} von ${Number(p.total_segments)} · ${escape(p.episode_title)}`:escape(stageNames[p.stage]||"Fortschritt")}</strong></p><p>${active?'<span class="activity-dot" aria-hidden="true"></span>':"Zuletzt: "}${escape(p.activity)}${active&&elapsed!==null?` · seit ${elapsed<1?"weniger als einer Minute":`${elapsed} Min.`}`:""}</p><p class="hint">Die Anzeige aktualisiert sich automatisch. Ein Modellaufruf kann mehrere Minuten dauern.</p>${p.total_segments?`<progress value="${Number(p.completed_segments)}" max="${Number(p.total_segments)}" aria-label="Fertige Folgen in dieser Stufe"></progress><p>${Number(p.completed_segments)} von ${Number(p.total_segments)} Folgen: ${escape(stageNames[p.stage]||p.stage)} abgeschlossen</p>`:""}<ol class="episode-progress">${(p.episodes||[]).map(e=>`<li>${e.completed?"✓":e.episode_id===p.current_episode?"●":"○"} ${escape(e.title)}</li>`).join("")}</ol>${(p.episodes||[]).filter(e=>e.teaching_preview).map(e=>`<details data-progress-episode="${escape(e.episode_id)}"><summary>Lehrkonzept lesen: ${escape(e.title)}</summary><pre class="document" data-progress-preview="${escape(e.episode_id)}">${escape(e.teaching_preview)}</pre></details>`).join("")}</section>`;
}
function progressAge(timestamp) {
  const seconds=Math.max(0,Math.floor((Date.now()-Date.parse(timestamp))/1000));
  return seconds<60?`${seconds} Sek.`:`${Math.floor(seconds/60)} Min.`;
}
function progressStale(p) {
  return !!p?.updated_at&&Date.now()-Date.parse(p.updated_at)>30000;
}
function renderProgressTiming(p, active) {
  if(!active||p?.phase!=="script")return "";
  const stale=progressStale(p);
  const call=p.model_call_started_at?`<p class="hint">${stale?"Zuletzt gemeldeter Modellaufruf gestartet vor":"Aktueller Modellaufruf: seit"} ${progressAge(p.model_call_started_at)}</p>`:"";
  const result=p.last_result_at?`<p class="hint">Letztes gespeichertes Modellergebnis: vor ${progressAge(p.last_result_at)}</p>`:"";
  const freshness=stale?`<p class="note" role="status">Fortschrittsanzeige seit ${progressAge(p.updated_at)} nicht aktualisiert. Ob das Modell weiterarbeitet, lässt sich daraus nicht erkennen. Die Verbindung wird automatisch erneut geprüft.</p>`:p.updated_at?`<p class="hint">Fortschrittsdaten vor ${progressAge(p.updated_at)} aktualisiert.</p>`:"";
  return call+result+freshness;
}
function renderJob() {
  const j=project?.job, box=$("job-status");
  if(overviewPage){box.hidden=true;return;}
  if(project?.audio_jobs?.some(job=>job.id===j?.id)){
    box.hidden=false;
    const view=JSON.stringify({audio_jobs:project.audio_jobs,capacity:project.audio_capacity,submitting});
    if(view!==lastJobView){box.innerHTML=renderAudioJobs();lastJobView=view;}
    return;
  }
  const details=step===PAGE.production?$("production-progress"):null;
  if(details) {
    const opened=new Set(Array.from(details.querySelectorAll?.("details[open][data-progress-episode]")||[],el=>el.dataset.progressEpisode));
    const scrolls=new Map(Array.from(details.querySelectorAll?.("[data-progress-preview]")||[],el=>[el.dataset.progressPreview,el.scrollTop]));
    details.innerHTML=renderProductionDetails();
    for(const detail of details.querySelectorAll?.("[data-progress-episode]")||[])detail.open=opened.has(detail.dataset.progressEpisode);
    for(const preview of details.querySelectorAll?.("[data-progress-preview]")||[])preview.scrollTop=scrolls.get(preview.dataset.progressPreview)||0;
  }
  const legacy=!j&&project?.run&&project.run.status!=="completed"?project.run:null;
  box.hidden=!j&&!legacy;
  if(box.hidden){box.innerHTML="";lastJobView="";return;}
  const r=j?.run||legacy, active=j?.status==="running", state=j?.status||legacy.status;
  const isScript=r?.kind==="script"||j?.progress?.phase==="script";
  const view=JSON.stringify({project:project?.id,job:j,legacy,
    progressClock:active&&isScript?Math.floor(Date.now()/10000):null,
    page:j?.sample?null:step,minute:active?Math.floor((Date.now()-Date.parse(j.started_at))/60000):null});
  // Preserve the audio element and its playback position during status polling.
  if(view===lastJobView)return;
  lastJobView=view;
  const missingFoundation=!active&&Object.values(r?.stages||{}).some(v=>v.error?.code==="teaching_research_required");
  const designBlocked=!active&&Object.values(r?.stages||{}).some(v=>v.error?.code==="teaching_design_failed");
  const foundationResearch=active&&j?.progress?.phase==="foundation_research";
  const title=designBlocked?"Lehrkonzept angehalten: Erklärung noch unvollständig":foundationResearch?"Fehlende Erklärgrundlagen werden automatisch recherchiert":missingFoundation?"Automatische Recherche konnte noch nicht abgeschlossen werden":active?(isScript?"Ausarbeitung läuft":actionNames[j.action]):({completed:"Arbeitsschritt abgeschlossen",review_ready:"Inhaltsverzeichnis bereit zur Durchsicht",interrupted:"Auftrag angehalten",waiting_for_quota:"Anbieterlimit erreicht",blocked:"Dieser Schritt braucht Aufmerksamkeit",failed:"Auftrag fehlgeschlagen",pending:"Auftrag wartet"}[state]||"Gespeicherter Auftrag");
  const resumable=r&&["interrupted","waiting_for_quota","failed","blocked","pending","running"].includes(state)&&!active&&!missingFoundation&&!designBlocked;
  const message=designBlocked&&j?.progress?.review_issues?.length?"Die automatische Überarbeitung hat noch nicht alle Kritikpunkte gelöst. Der bisherige Stand ist gespeichert.":missingFoundation&&/research_needed\.md/.test(j?.message||"")?"Der Abgleich zwischen Quellen und Lehrkonzept ist noch offen. Der bisherige Auftrag bleibt gespeichert.":j?.message;
  box.innerHTML=`<div class="job-top"><strong>${escape(title)}</strong>${active?'<button class="danger small" data-action="stop">Auftrag anhalten</button>':resumable?'<button class="secondary small" data-action="resume">Fortsetzen</button>':""}</div>${message?`<p>${escape(message)}</p>`:""}${active?`<p>Gesamte Laufzeit seit Start/Fortsetzung: ${Math.max(0,Math.floor((Date.now()-Date.parse(j.started_at))/60000))} Min. · Fertige Schritte werden gespeichert.</p>`:""}${r&&!isScript?`<div class="stage-strip">${Object.entries(r.stages).map(([name,v])=>`<span class="${escape(v.status)}">${v.status==="completed"?"✓ ":""}${stageNames[name]||escape(name)}</span>`).join("")}</div>`:""}${j?.progress?.phase!=="script"&&j?.progress?.total_segments!==undefined?`<p>${j.progress.completed_segments} von ${j.progress.total_segments} ${j.action==="audio_samples"?"Hörproben":"Sprechabschnitten"} fertig</p><progress value="${Number(j.progress.completed_segments)}" max="${Number(j.progress.total_segments)}"></progress>`:""}${j?.checks?`<ul class="checks">${j.checks.checks.map(c=>`<li>${c.ok?"✓":"○"} ${escape(c.name)}<span class="hint">${escape(c.detail)}</span></li>`).join("")}</ul><p>Diese Prüfung erzeugt kein Audio.</p>`:""}`;
  if(isScript&&j?.progress?.current_episode)box.innerHTML+=`<p>Folge ${Number(j.progress.episode_number)} von ${Number(j.progress.total_segments)} · ${escape(j.progress.activity)}</p>`;
  const destination=state==="completed"?runPage(r):jobPage();
  const links=["Auftrag ansehen","Recherche ansehen","Inhaltsverzeichnis prüfen","Ausarbeitung ansehen","Skripte lesen","Audio ansehen"];
  if(destination!==null&&destination!==undefined&&destination!==step)box.innerHTML+=`<button class="secondary small status-link" data-step="${destination}">${links[destination]} →</button>`;
  if(j?.sample)box.innerHTML+=`<p>Hörprobe: ${escape(j.sample.voice)} · ${escape(j.sample.language)}</p><audio controls preload="none" src="${mediaUrl(j.sample.audio)}"></audio><div class="actions"><a href="${mediaUrl(j.sample.audio)}" target="_blank" rel="noopener">Hörprobe separat öffnen</a><a href="${mediaUrl(j.sample.audio)}" download>MP3 herunterladen</a></div>`;
  if(j?.action==="audio_samples"&&j?.progress?.current_voice&&active)box.innerHTML+=`<p>Aktuelle Stimme: ${escape(j.progress.current_voice)}</p>`;
  if(Number.isSafeInteger(j?.progress?.model_call_limit)&&j.progress.model_call_limit>0)
    box.innerHTML+=`<p class="hint">Modellaufrufe: ${Number(j.progress.model_calls||0)} von ${j.progress.model_call_limit}</p>`;
  box.innerHTML+=renderProgressTiming(j?.progress,active);
  box.innerHTML+=renderRunTextChoice(j);
  if(j?.progress?.execution?.text==="parallel"){
    const activeEpisodes=j.progress.active_episodes||[];
    box.innerHTML+=`<p class="hint">Textmodus: Parallel · bis zu 3 Folgen je Skript-, Polishing- oder Prüfstufe.${activeEpisodes.length?` In Bearbeitung: ${activeEpisodes.map(id=>escape(j.progress.episodes?.find(e=>e.episode_id===id)?.title||id)).join(", ")}.`:""}</p>`;
  }
}
function render() { renderNavigation(); $("content").innerHTML=overviewPage?renderOverview():[renderBrief,renderResearch,renderOutline,renderProduction,renderScript,renderAudio][step](); renderJob(); syncPlayButtons(); }
async function refreshProjects() {
  boot=await api("/api/bootstrap");
  $("project-select").innerHTML='<option value="">Neues Projekt</option>'+boot.projects.map(p=>`<option value="${escape(p.id)}">${escape(p.topic)}</option>`).join("");
  $("project-select").value=project?.id||"";
}
async function selectProject(id, loaded=null, requestedPage=null) {
  if(setupSending)throw new Error("Die Nachricht wird gerade gesendet. Bitte kurz warten.");
  const epoch=++navigationEpoch;
  const selected=id?(loaded||await api("/api/projects/"+encodeURIComponent(id))):null;
  if(epoch!==navigationEpoch)return;
  overviewPage=false;
  pendingAttachments=[];
  project=selected;
  $("project-select").value=id||"";
  episodeIndex=0;scriptEpisodeId=null;readingSnapshot=null;followWorkflow=requestedPage===null;
  step=requestedPage===null?recommendedPage():requestedPage;
  lastJobSignature=projectJobSignature(project);updatePageUrl();render();
}
async function storeKey() {
  const key=$("api-key")?.value.trim();
  if(key){const value=await api("/api/key",{key});boot.key_available=value.key_available;$("api-key").value="";
    $("key-status").textContent=boot.key_available?"Ein Key ist für diese Sitzung verfügbar.":"Noch kein Key hinterlegt.";}
}
async function sendSetupMessage(message, presetId=null) {
  message=message.trim();
  if((!message&&!pendingAttachments.length)||running()||setupSending||readingAttachments)return;
  if(/sk-or-[A-Za-z0-9_-]{12,}/.test(message))throw new Error("Bitte den geschützten OpenRouter-Key-Eingang verwenden. Keys gehören nicht in den Chat.");
  const initialTopic=message||pendingAttachments[0]?.name.replace(/\.(md|txt|docx)$/i,"");
  message=message||"Bitte leite aus meinen angehängten Dateien einen Vorschlag für das neue Podcast-Projekt ab und nutze sie als Ausgangsmaterial für die Recherche.";
  setupSending=true;refreshAttachmentComposer();
  try{
    if(!project){
      const config={...structuredClone(boot.defaults),topic:initialTopic.slice(0,500)};
      const created=await api("/api/projects",{config,text:boot.text_defaults||defaultTextChoice(),
        execution:{text:"sequential",audio:"sequential"}});
      project=await api("/api/projects/"+created.id);await refreshProjects();updatePageUrl();
    }
    if(pendingAttachments.length){
      await api(`/api/projects/${project.id}/upload`,{files:pendingAttachments.map(({name,base64})=>({name,base64}))});
      pendingAttachments=[];
      project=await api(`/api/projects/${project.id}`);
    }
    await start("assistant",{message,...(presetId?{text_preset:presetId}:{})});
  }finally{setupSending=false;refreshAttachmentComposer();}
}
async function applySetupProposal() {
  await api(`/api/projects/${project.id}/apply_proposal`,{proposal_hash:project.proposal_hash,
    config_hash:project.config_hash,audio_hash:project.audio_hash,execution_hash:project.execution_hash});
  project=await api(`/api/projects/${project.id}`);await refreshProjects();render();notice("Deine Auswahl ist gespeichert.");
}
async function start(action, extra={}) {
  if(!project) throw new Error("Lege zuerst dein Projekt an.");
  const parallelAudio=action==="audio"||(action==="resume"&&extra.episode);
  if(parallelAudio?audioBlockReason(extra.episode):running()) throw new Error(parallelAudio?audioBlockReason(extra.episode):"Ein Auftrag läuft bereits.");
  const id=project.id;
  submitting=true;
  try { await api(`/api/projects/${id}/start`,{action,...extra});project=await api(`/api/projects/${id}`);lastJobSignature=projectJobSignature(project); }
  finally { submitting=false; }
  navigatePage(recommendedPage(),{automatic:true,push:false});
}
function jobSignature(job) { return job?`${job.id}:${job.status}`:""; }
function projectJobSignature(p) { return [jobSignature(p?.job),...(p?.audio_jobs||[]).map(jobSignature)].join("|"); }
document.addEventListener("submit",event=>{
  event.preventDefault();attempt(async()=>{
    if(event.target.id==="chat-form")await sendSetupMessage($("chat-message").value);
  });
});
document.addEventListener("change",event=>attempt(async()=>{
  if(event.target.id==="chat-files")await queueAttachments(event.target.files);
  if(event.target.id==="project-select")await selectProject(event.target.value);
  if(event.target.id==="episode-select"){episodeIndex=Number(event.target.value);render();}
  if(event.target.id==="script-select"){scriptEpisodeId=event.target.value;readingSnapshot=null;render();}
  if(event.target.id==="audio-approval")$("audio-start").disabled=!event.target.checked||!!audioBlockReason();
}));
document.addEventListener("click",event=>{
  const button=event.target.closest("button");if(!button)return;
  attempt(async()=>{
    if(button.dataset.textPreset){
      const p=boot.text_catalog.presets.find(row=>row.id===button.dataset.textPreset);
      if(!p)throw new Error("Bitte die Modellauswahl neu laden.");
      const draft=$("chat-message")?.value.trim();
      await sendSetupMessage(`${draft?draft+"\n\n":""}Nutze für die Textarbeit ${p.label} (Modell ${p.model}${p.reasoning_effort?", Reasoning "+p.reasoning_effort:""}).`,p.id);return;
    }
    if(button.dataset.removePending!==undefined){if(!setupSending){pendingAttachments.splice(Number(button.dataset.removePending),1);refreshAttachmentComposer();}return;}
    if(button.dataset.removeAttachment){await removeAttachment(button.dataset.removeAttachment);return;}
    if(button.id==="new-project"||button.hasAttribute("data-new-project")){await selectProject("");return;}
    if(button.id==="project-overview"||button.dataset.action==="overview"){await showOverview();return;}
    if(button.dataset.openProject){await selectProject(button.dataset.openProject);return;}
    if(button.dataset.deleteProject){
      const p=overviewData.projects.find(p=>p.id===button.dataset.deleteProject);
      if(p&&window.confirm(`„${p.topic}“ mit Recherche, Skripten und Audio in den lokalen Papierkorb verschieben?`)){
        await api(`/api/projects/${p.id}/delete`,{confirm_id:p.id,config_hash:p.config_hash});
        await refreshProjects();overviewData=await loadOverview();refreshOverview();notice("Projekt im Papierkorb. Du kannst es unten wiederherstellen.");
      }return;
    }
    if(button.dataset.restoreProject){await api("/api/restore",{trash_id:button.dataset.restoreProject});await refreshProjects();overviewData=await loadOverview();refreshOverview();return;}
    if(button.dataset.setupReply){await sendSetupMessage(button.dataset.setupReply);return;}
    if(button.dataset.step!==undefined){navigatePage(Number(button.dataset.step));$("main").focus();window.scrollTo(0,0);return;}
    if(button.dataset.previewVoice){
      const voice=button.dataset.previewVoice,language=button.dataset.language,provider=button.dataset.previewProvider;
      if(provider==="openrouter_gemini_tts"&&!savedSample(voice,language)){
        await storeKey();await start("audio_sample",{voice,language,approve_sample:true});
      }else await playSample(voice,language,provider);
      return;
    }
    if(button.dataset.playVoice)await playSample(button.dataset.playVoice,button.dataset.language);
    const action=button.dataset.action;if(!action)return;
    if(action==="refresh-script"){readingSnapshot=null;$("content").innerHTML=renderScript();return;}
    if(action==="quit"){await api("/api/quit",{});project=null;$("job-status").hidden=true;$("content").innerHTML='<section class="empty"><h1>Bis zum nächsten Gespräch.</h1><p>Das Studio ist beendet. Öffne den Podcast-Studio-Starter in deinem Projektordner, um es wieder zu starten.</p></section>';return;}
    if(action==="apply-proposal"){await applySetupProposal();return;}
    if(action==="store-key"){await storeKey();notice("Key im Sitzungsspeicher hinterlegt.");return;}
    if(action==="forget-key"){await api("/api/key",{key:""});await refreshProjects();$("api-key").value="";$("key-status").textContent=boot.key_available?"Key aus der Server-Umgebung verfügbar.":"Sitzungs-Key entfernt.";return;}
    if(action==="stop"){await api(`/api/projects/${project.id}/stop`,{job_id:button.dataset.jobId});project=await api(`/api/projects/${project.id}`);render();return;}
    const extra={};
    if(action==="audio_samples"){
      extra.language=setupSelection().config.language;extra.approve_samples=true;
      await storeKey();
    }
    if(action==="replan")extra.message=$("outline-feedback").value;
    if(action==="script")extra.plan_hash=project.outline.hash;
    if(action==="revise"){extra.message=$("script-feedback").value;extra.episode=project.episodes[episodeIndex].script.episode_id;}
    if(action==="resume"){extra.run_id=button.dataset.runId||project.job?.run?.run_id||project.run?.run_id;if(button.dataset.episode)extra.episode=button.dataset.episode;}
    if(action==="audio"){const e=project.episodes[episodeIndex];Object.assign(extra,{episode:e.script.episode_id,approve_audio:$("audio-approval").checked,script_hash:e.hash,readable_hash:e.readable_hash,config_hash:project.config_hash,audio_hash:project.audio_hash});}
    await start(action,extra);
  });
});
async function poll() {
  try {
    if(overviewPage){const next=await loadOverview();if(overviewPage){overviewData=next;refreshOverview();}return;}
    if(!project||submitting||setupSending||readingAttachments)return;
    const id=project.id,next=await api(`/api/projects/${id}`);
    if(project?.id!==id)return;
    const changed=projectJobSignature(next)!==lastJobSignature;
    const destination=followWorkflow?recommendedPage(next):step;
    const scriptsChanged=scriptCollectionKey(project)!==scriptCollectionKey(next);
    const readerOpen=step===PAGE.scripts&&readingSnapshot;
    // Keep unfinished form edits and the script being reviewed stable during polling.
    const samplesChanged=JSON.stringify(project.voice_samples)!==JSON.stringify(next.voice_samples);
    const chatChanged=JSON.stringify(project.chat)!==JSON.stringify(next.chat);
    const attachmentsChanged=JSON.stringify(project.attachments)!==JSON.stringify(next.attachments);
    project.job=next.job;project.run=next.run;project.voice_samples=next.voice_samples;
    project.chat=next.chat;project.proposal_hash=next.proposal_hash;project.proposal_applied=next.proposal_applied;
    project.attachments=next.attachments;project.proposal_current=next.proposal_current;
    project.audio_jobs=next.audio_jobs;project.audio_capacity=next.audio_capacity;
    project.episodes=next.episodes;project.script_previews=next.script_previews;renderNavigation();renderJob();
    if(samplesChanged)refreshVoiceLibrary();
    if(changed||destination!==step){lastJobSignature=projectJobSignature(next);project=next;
      if(followWorkflow){step=destination;updatePageUrl();}
      if(readerOpen&&step===PAGE.scripts){renderNavigation();renderJob();refreshScriptReader();}
      else render();
      if(next.job?.status==="completed"&&next.job.sample)$("job-status").scrollIntoView({block:"nearest"});
    }else if(scriptsChanged&&step===PAGE.scripts)refreshScriptReader();
    else if((chatChanged||attachmentsChanged)&&step===PAGE.brief){refreshAttachmentComposer();}
  }catch(error){renderJob();notice("Verbindung zum Studio unterbrochen. Ist das Studio-Fenster noch geöffnet?");}
}
attempt(async()=>{
  const startupEpoch=navigationEpoch;
  await refreshProjects();
  if(startupEpoch!==navigationEpoch)return;
  const params=window.location?new URLSearchParams(window.location.search):null;
  const requested=params?.get("project"), requestedStep=pageKeys.indexOf(params?.get("step"));
  if(requested&&boot.projects.some(p=>p.id===requested))await selectProject(requested,null,requestedStep<0?null:requestedStep);
  else if(params?.has("new"))await selectProject("");
  else await showOverview();
});
setInterval(poll,2500);
for(const event of ["play","pause","ended"])$("sample-player").addEventListener(event,syncPlayButtons);
window.addEventListener("popstate",()=>attempt(async()=>{
  const params=new URLSearchParams(window.location.search), id=params.get("project");
  const index=pageKeys.indexOf(params.get("step"));
  if(id&&boot.projects.some(p=>p.id===id)) {
    if(project?.id===id)navigatePage(index<0?recommendedPage():index,{push:false});
    else await selectProject(id,null,index<0?null:index);
  } else if(params.has("new"))await selectProject("");
  else await showOverview();
}));

// A small optional agent surface shares the visible navigation. It cannot approve generation.
if(document.modelContext?.registerTool){
  const lifecycle=new AbortController();
  const register=(tool)=>Promise.resolve(document.modelContext.registerTool(tool,{signal:lifecycle.signal})).catch(()=>{});
  register({name:"read_podcast_workspace",description:"Read the selected project's topic, current step and job status.",inputSchema:{type:"object",properties:{},additionalProperties:false},annotations:{readOnlyHint:true,untrustedContentHint:true},execute:()=>({project:project?.id||null,topic:project?.config.topic||null,step:steps[step],job:project?.job?.status||null})});
  register({name:"navigate_podcast_step",description:"Show a workflow step in the current project. Does not start or approve generation.",inputSchema:{type:"object",properties:{step:{type:"integer",minimum:1,maximum:6}},required:["step"],additionalProperties:false},annotations:{readOnlyHint:false},execute:input=>{if(!Number.isInteger(input?.step)||input.step<1||input.step>6)throw new Error("Schritt 1 bis 6 wählen.");navigatePage(input.step-1);return{step:steps[step]};}});
  window.addEventListener("pagehide",()=>lifecycle.abort(),{once:true});
}
