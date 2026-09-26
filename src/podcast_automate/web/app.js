"use strict";
const $ = id => document.getElementById(id);
const escape = value => String(value ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const steps = ["Auftrag & Stimmen", "Recherche", "Inhaltsverzeichnis", "Ausarbeitung", "Skripte lesen", "Vertonung"];
const pageKeys = ["brief", "research", "outline", "production", "scripts", "audio"];
const PAGE = Object.fromEntries(pageKeys.map((key,index)=>[key,index]));
// The editorial framing of a step introduces a page that has nothing to show yet. A working page
// opens with its name, its state and its action instead.
const pageIntros = {
  brief:["Dein redaktioneller Partner.","Beschreibe deinen Wunsch. Dein Partner fragt nach, bis Thema, Tiefe, Stimmen und Arbeitsweise passen."],
  research:["Erst verstehen. Dann erzählen.","Jede Leitfrage braucht eine belegte Antwort, nachvollziehbare Erklärungen und eine Gegenprüfung. Fehlende Grundlagen werden automatisch nachrecherchiert, bevor das Inhaltsverzeichnis entsteht."],
  outline:["Der rote Faden, bevor wir schreiben.","Prüfe, ob die Grundlagen tragen, die Kapitel aufeinander aufbauen und das Ganze deine Frage beantwortet. Erst deine Freigabe startet die Skripte."],
  production:["Vom roten Faden zum fertigen Gespräch.","Hier arbeitet die Redaktion nach deiner Planfreigabe automatisch weiter. Fertige Lehrkonzepte und gespeicherte Skriptfassungen kannst du schon währenddessen lesen."],
  scripts:["Lies das Gespräch in deinem Tempo.","Jede gespeicherte Folge wird hier einzeln lesbar. Du siehst, ob du einen Entwurf, einen überarbeiteten Dialog oder eine geprüfte Fassung liest."],
  audio:["Vom Text zum Gespräch.","Gib eine gelesene Folge mit dem gewählten Audioanbieter ausdrücklich frei. Fertige Abschnitte bleiben für eine Fortsetzung gespeichert."],
};
const productionStages = [
  ["teaching", "Lehrkonzept", "Einstieg, Erklärungen und Beispiele für jede Folge ausarbeiten."],
  ["writing", "Skriptentwurf", "Aus dem Lehrkonzept ein vollständiges Gespräch entwickeln."],
  ["polishing", "Dialog-Polishing", "Gesprochene Sprache und die Rollen der beiden Hosts ausarbeiten."],
  ["review", "Qualitätsprüfung", "Quellen, Erklärungstiefe und Verständlichkeit prüfen und überarbeiten."],
  ["publish", "Zur Durchsicht bereitstellen", "Geprüfte Skripte zum Lesen bereitstellen."],
];
const stageNames = {discovery:"Quellensuche",retrieval:"Quellen lesen",dossier:"Teilfragen und Dossier",completeness:"Leitfragen vollständig klären",planning:"Inhaltsverzeichnis",teaching:"Lehrkonzept",writing:"Skript",polishing:"Dialog-Polishing",review:"Qualitätsprüfung",publish:"Bereitstellen",synthesis:"Vertonung",assembly:"Audio zusammenfügen"};
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
let drawerOpen=false, connectionLost=false, lastSyncAt=Date.now(), stopHtml="";
const scriptStateLabels={draft:"Entwurf",polished:"Dialog überarbeitet",reviewed:"Prüfungen bestanden",published:"Fertig zur Durchsicht"};
const voiceSamples = () => project?.voice_samples || boot.voice_samples || {};
const savedSample = (voice, language) => voiceSamples()[language]?.[voice];
const sampleButtonLabel = (provider, voice, language) =>
  provider === "openrouter_gemini_tts" && !savedSample(voice, language)
    ? "Hörprobe erzeugen · API"
    : "▶ Anhören";
const currentAudio = () => project?.audio_settings || {provider:"qwen3_local",voices:(project?.config||boot.defaults).voice_profile};
const audioCatalog = () => boot.audio_catalog || {qwen3_local:{label:"Qwen · auf diesem Computer",voices:boot.voices,defaults:boot.defaults.voice_profile}};
// A Gemini choice saved without a model uses the default one; the label names the model an approval binds.
const audioLabel = a => {
  if(a.provider==="qwen3_local")return "Qwen · lokal";
  const gemini=audioCatalog().openrouter_gemini_tts;
  return `${gemini?.models?.[a.model||gemini.default_model]||"Gemini"} · OpenRouter`;
};
const mediaUrl = path => "/media/"+encodeURIComponent(project.id)+"/"+path.split("/").map(encodeURIComponent).join("/");
const running = () => submitting || project?.job?.status === "running" || (project?.audio_jobs||[]).some(j=>j.status==="running");
const disabled = () => running() ? "disabled" : "";
function audioBlockReason(episode=project?.episodes?.[episodeIndex]?.script?.episode_id) {
  if(submitting)return "Der Auftrag wird gestartet.";
  // Gemini fails at its first request without a key; the approval card offers the key field instead.
  if(currentAudio().provider==="openrouter_gemini_tts"&&boot.key_available===false)return "Zuerst den OpenRouter-Key hinterlegen.";
  if(!boot.capabilities?.parallel_audio||currentAudio().provider!=="openrouter_gemini_tts")
    return running()?"Ein Auftrag läuft bereits.":"";
  const active=(project.audio_jobs||[]).filter(j=>j.status==="running");
  if(active.some(j=>j.episode===episode))return "Diese Folge wird bereits vertont.";
  if(project.job?.status==="running"&&!active.some(j=>j.id===project.job.id))return "Zuerst den laufenden Auftrag abschließen oder anhalten.";
  if(project.audio_capacity?.available===0)return "Alle Plätze sind belegt. Sobald eine Folge fertig ist, kannst du die nächste starten.";
  return "";
}
// One message box for outcomes. Errors and confirmations look different; the box sticks below the topbar.
function notice(message, kind="warn") { const box=$("notice"); box.textContent = message; box.hidden = !message; box.className = message?`notice ${kind}`:"notice"; }
async function api(path, data, renewed=false) {
  const options = data === undefined ? {} : {method:"POST",headers:{"Content-Type":"application/json","X-Studio-Token":boot.token},body:JSON.stringify(data)};
  let response;
  try { response = await fetch(path, options); }
  catch { const error=new Error("Das Studio ist nicht erreichbar. Läuft das Studio-Fenster noch?"); error.network=true; throw error; }
  const result = await response.json().catch(()=>({error:"Der Studio-Server hat keine lesbare Antwort geliefert."}));
  if (!response.ok) {
    // A restarted server has a new session token: renew it once instead of failing every click.
    if(response.status===403&&result.code==="forbidden"&&data!==undefined&&!renewed){
      const fresh=await fetch("/api/bootstrap").then(r=>r.ok?r.json():null).catch(()=>null);
      if(fresh?.token&&fresh.token!==boot.token){
        const keyLost=boot.key_available&&!fresh.key_available;
        boot.token=fresh.token;boot.key_available=fresh.key_available;
        const value=await api(path,data,true);
        if(keyLost)notice("Das Studio wurde neu gestartet. Ein zuvor hinterlegter OpenRouter-Key muss erneut eingegeben werden.");
        return value;
      }
    }
    const error=new Error(result.error || "Anfrage fehlgeschlagen.");
    error.code=result.code;error.status=response.status;throw error;
  }
  return result;
}
async function attempt(action) { try { notice(""); await action(); } catch(error) { notice(error.message,"error"); } }
function textInput(id,label,value,type="text") { return `<div class="field"><label for="${id}">${label}</label><input id="${id}" type="${type}" value="${escape(value)}"></div>`; }
function area(id,label,value,rows=3) { return `<div class="field"><label for="${id}">${label}</label><textarea id="${id}" rows="${rows}">${escape(value)}</textarea></div>`; }
// Compact page head: step number and name, then the step's state as a chip.
function heading(n) {
  const state=project?navigationStates()[n-1]:null;
  return `<header class="page-head"><div class="page-title"><span class="eyebrow">${String(n).padStart(2,"0")} / 06</span><h1>${steps[n-1]}</h1></div>${state?`<span class="chip ${state[1]}">${escape(state[0])}</span>`:""}</header>`;
}
function empty(title,body,button,target,intro=null) {
  return `<section class="empty">${intro?`<p class="empty-intro">${intro[0]}</p>`:""}<h2>${title}</h2><p>${body}</p>${intro?`<p>${intro[1]}</p>`:""}<button data-step="${target}">${button}</button></section>`;
}
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
// After an audio run the latest run is no longer the script run; published episodes then stand for the finished work.
const scriptRun = (p=project) => { const run=currentRun(p); return run?.kind==="script"?run:null; };
const scriptsFinished = (p=project) => { const run=scriptRun(p); return run?run.status==="completed":(p?.episodes||[]).length>0; };
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
  const blocked=["blocked","failed","interrupted","waiting_for_quota","pending"].includes(state);
  const info=stopInfo(project?.job);
  const audioReady=(project?.episodes||[]).some(e=>e.audio_current&&e.audio?.length);
  const readable=readableScripts().length;
  const finished=scriptsFinished();
  const rows=[
    [project?"Gespeichert":"Hier beginnen",project?"done":"ready"],
    [project?.research?"Dossier vorhanden":"Quellen und Grundlagen",project?.research?"done":"pending"],
    [approvedOutline()?"Freigegeben":project?.outline?"Deine Freigabe":"Nach der Recherche",approvedOutline()?"done":project?.outline?"decision":"pending"],
    [finished?"Abgeschlossen":approvedOutline()?"Automatische Schritte":"Nach der Planfreigabe",finished?"done":"pending"],
    // Reading stays a decision until the first episode is voiced; afterwards it is done work, not a request.
    [readable?`${readable} ${readable===1?"Folge lesbar":"Folgen lesbar"}`:"Sobald ein Entwurf fertig ist",readable?(audioReady?"done":"decision"):"pending"],
    [audioReady?"Aufnahmen vorhanden":project?.episodes?.length?"Deine Audio-Freigabe":"Nach deiner Durchsicht",audioReady?"done":project?.episodes?.length?"decision":"pending"],
  ];
  const activePage=jobPage()??destination;
  if(activePage!==undefined&&activePage!==null&&(busy||blocked))
    rows[activePage]=busy?["Läuft automatisch","running"]:[STOP_KIND_LABELS[info?.kind]||"Angehalten",info?.kind==="decision"?"decision":"blocked"];
  // Parallel Gemini episodes: a stopped one stays visible beside those still being voiced.
  const halted=stoppedAudio().length, voicing=(project?.audio_jobs||[]).some(j=>j.status==="running");
  if(halted)rows[PAGE.audio]=voicing?[`Läuft · ${halted} angehalten`,"running"]:[`${halted} ${halted===1?"Folge":"Folgen"} angehalten`,"blocked"];
  return rows;
}
// A tab in the background still shows whether the open project runs, waits for a decision or stopped.
function statusGlyph(job) {
  if(job?.status==="running"||(project?.audio_jobs||[]).some(j=>j.status==="running"))return "● ";
  const info=stopInfo(job);
  return info?(info.kind==="decision"?"▲ ":"! "):"";
}
const shortText=(text,max)=>{const value=String(text??"");return value.length>max?value.slice(0,max-1).trimEnd()+"…":value;};
function elapsedText(iso) {
  const minutes=Math.floor((Date.now()-Date.parse(iso))/60000);
  if(!Number.isFinite(minutes)||minutes<1)return "weniger als einer Minute";
  return minutes<60?`${minutes} Min.`:`${Math.floor(minutes/60)} Std. ${minutes%60} Min.`;
}
// The stepper carries time where the pipeline knows it: elapsed on the running step, the projection on a waiting research.
function stepTimeHints() {
  const hints=Array(steps.length).fill(""), j=project?.job;
  if(!j)return hints;
  const page=jobPage();
  if(j.status==="running"&&page!==null&&page!==undefined&&j.started_at)hints[page]=` · seit ${elapsedText(j.started_at)}`;
  // The projection belongs to the plan gate only; a run past its gate has moved on from that estimate.
  const review=j.progress?.plan_review, projection=review?.projection;
  if(j.status!=="running"&&review?.awaiting&&!review.approved&&projection?.projected_hours)hints[PAGE.research]=` · voraussichtlich ${Math.round(Number(projection.projected_hours))} Std.`;
  return hints;
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
  // A conversation opens at its newest reply, right above the pinned composer.
  if(step===PAGE.brief&&(project?.chat||[]).length)scrollChatToEnd();
}
function renderNavigation() {
  if(overviewPage){
    $("steps").hidden=false;$("steps").innerHTML='<p class="sidebar-hint">Wähle ein Projekt, um seine Arbeitsschritte zu sehen.</p>';$("project-title").textContent="Alle Projekte & Podcasts";
    const waiting=overviewData.projects.filter(p=>attentionOf(p)).length;
    document.title=waiting?`(${waiting}) Podcast Studio`:"Podcast Studio";return;
  }
  $("steps").hidden=false;
  const states=navigationStates(), hints=stepTimeHints();
  const glyph=state=>state==="done"?"✓":state==="running"?"●":state==="blocked"?"!":state==="decision"?"▲":null;
  $("steps").innerHTML = steps.map((name,i)=>`<button class="step ${states[i][1]}" data-step="${i}" ${i===step?'aria-current="page"':""}><span class="step-number" aria-hidden="true">${glyph(states[i][1])??i+1}</span><span class="step-label">${name}<small>${escape(states[i][0])}${hints[i]}</small></span></button>`).join("");
  const title=project?.config?.topic || "Neues Podcast-Projekt";
  $("project-title").textContent = title;
  document.title=project?`${statusGlyph(project.job)}${title} · Podcast Studio`:"Podcast Studio";
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
const providerLabels={codex_cli:"Codex · Abo",claude_code:"Claude · Abo",openrouter:"OpenRouter · API",auto:"Automatisch · Claude-Abo, sonst Codex-Abo"};
const providerNames={codex_cli:"Codex",claude_code:"Claude",openrouter:"OpenRouter"};
function autoCandidates() {
  return boot.text_catalog?.auto_candidates||{codex_cli:{model:"gpt-6-astra",reasoning_effort:"xhigh"},claude_code:{model:"claude-opus-5-5",reasoning_effort:"xhigh"}};
}
function candidateText(c) {
  return `Codex ${escape(c?.codex_cli?.model||"Standard")} (${escape(c?.codex_cli?.reasoning_effort||"Standard")}) · Claude ${escape(c?.claude_code?.model||"Standard")} (${escape(c?.claude_code?.reasoning_effort||"Standard")})`;
}
function textChoiceSummary(t) {
  if(t.provider==="auto")return `${providerLabels.auto} · ${candidateText(autoCandidates())}`;
  return `${providerLabels[t.provider]||escape(t.provider)} · ${escape(t.model||"Standard")} · Reasoning: ${escape(t.reasoning_effort||"Standard")}`;
}
function renderRunTextChoice(job) {
  if(!["script","research"].includes(job?.run?.kind))return "";
  const t=job.text_generation;
  const saved=t?.provider==="auto"?`Automatische Abo-Wahl · ${candidateText(t.candidates)}`:
    `${t?.model?escape(t.model):"Modell nicht festgelegt"} · Reasoning: ${t?.reasoning_effort?escape(t.reasoning_effort):"nicht festgelegt"}`;
  return `<p class="hint">Für diesen Auftrag gespeichert: ${saved}.</p>${renderProviderChoice(job)}`;
}
function resetText(iso) {
  const time=iso?Date.parse(iso):NaN;
  return Number.isFinite(time)?` · Reset ${new Date(time).toLocaleString("de-DE",{dateStyle:"short",timeStyle:"short"})}`:"";
}
function windowText(snapshot) {
  const w=(snapshot?.windows||[])[0];
  if(!w||typeof w.used_percent!=="number")return "";
  const label=w.window_minutes===10080?"Wochenfenster":w.window_minutes===300?"5-Stunden-Fenster":"Fenster";
  return ` (${label} ${Number(w.used_percent)} %)`;
}
function renderProviderChoice(job) {
  const c=job?.provider_choice;
  if(!c?.provider)return "";
  const s=c.snapshots||{}, codex=s.codex_cli, claude=s.claude_code, parts=[];
  if(codex)parts.push(`Codex ${codex.available?"bereit":codex.usable?"ohne Kontingent":"nicht nutzbar"}${windowText(codex)}${codex.available?"":resetText(codex.resets_at)}`);
  if(claude)parts.push(`Claude ${claude.available?"bereit":claude.usable?"gesperrt":"nicht nutzbar"}${claude.available?"":resetText(claude.blocked_until||claude.resets_at)}`);
  const current=`Aktueller Anbieter: ${providerNames[c.provider]||escape(c.provider)}${c.model?" · "+escape(c.model):""}${c.mode==="fixed"?" (fest gewählt)":""}`;
  const switched=c.switch?` · Wechsel von ${providerNames[c.switch.from]||escape(c.switch.from)} zu ${providerNames[c.switch.to]||escape(c.switch.to)} nach Kontingentfehler`:"";
  return `<p class="hint provider-choice">${current}${switched}${parts.length?" · "+parts.join(" · "):""}</p>`;
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
  const mode=(value,limit)=>value==="parallel"?`Parallel · bis zu ${limit}`:"Sequenziell";
  return `<section class="panel"><div class="panel-title"><h2>${proposal&&!project.proposal_applied?"Deine Auswahl · Vorschlag":"Dein gespeicherter Auftrag"}</h2></div>
    <dl><dt>Thema</dt><dd>${escape(c.topic)}</dd><dt>Leitfrage</dt><dd>${escape(c.central_question||"Noch zu klären")}</dd>
    <dt>Sprache und Umfang</dt><dd>${c.language==="en-US"?"English":"Deutsch"} · ${c.target_total_minutes?escape(c.target_total_minutes)+" Minuten":"Länge nach Erklärbedarf"}</dd>
    <dt>Vorwissen und Tiefe</dt><dd>${escape(c.prior_knowledge||"Keine besonderen Vorkenntnisse")} · ${escape(c.depth_request)}</dd>
    ${c.focus_questions?.length?`<dt>Schwerpunkte</dt><dd>${c.focus_questions.map(escape).join(" · ")}</dd>`:""}
    ${c.excluded_topics?.length?`<dt>Ausgenommen</dt><dd>${c.excluded_topics.map(escape).join(" · ")}</dd>`:""}
    ${c.seed_urls?.length?`<dt>Quellenlinks</dt><dd>${c.seed_urls.map(escape).join(" · ")}</dd>`:""}
    <dt>Textmodell</dt><dd>${textChoiceSummary(t)}</dd>
    ${t.provider==="openrouter"?'<dt>Live-Recherche</dt><dd>Über die Abos (Claude, sonst Codex) · Textarbeit wird separat über OpenRouter abgerechnet.</dd>':""}
    <dt>Stimmen</dt><dd>${escape(audioLabel(a))} · ${escape(a.voices.host_a)} &amp; ${escape(a.voices.host_b)}</dd>
    <dt>Textausarbeitung</dt><dd>${mode(x.text,"5 gleichzeitig")}</dd><dt>Vertonung</dt><dd>${a.provider==="qwen3_local"?"Sequenziell · lokale Grafikkarte":mode(x.audio,"3 Folgen")}</dd></dl>
    <p class="hint">Änderungswünsche schreibst du dem Partner. Parallel gilt für Skript, Polishing, Prüfung und unabhängige Recherche-Teilfragen; das Lehrkonzept bleibt in Reihenfolge. Bestehende Textaufträge behalten beim Fortsetzen ihren Modus.</p>
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
function scrollChatToEnd() { $("chat-end")?.scrollIntoView?.({block:"end"}); }
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
// The partner's turn in the conversation: writing, or why no answer came and how to send again.
function chatStatusBubble() {
  const j=project?.main_job??project?.job;
  if(j?.action!=="assistant")return "";
  if(j.status==="running")return `<div class="chat-message pending" aria-live="polite"><strong>Redaktion</strong><p><span class="activity-dot" aria-hidden="true"></span>schreibt … seit ${elapsedText(j.started_at)}. Eine Antwort kann einige Minuten dauern.</p></div>`;
  const info=stopInfo(j), last=(project.chat||[]).at(-1);
  if(!info||last?.role!=="user")return "";
  const limit=Number(project.chat_budget?.limit)||0;
  const raise=info.code==="chat_budget"&&limit?`<button class="secondary small" data-action="approve-chat" data-model-calls="${limit+50}">Gesprächslimit auf ${limit+50} erhöhen</button>`:"";
  return `<div class="chat-message failed" role="alert"><strong>Keine Antwort · ${escape(info.title)}</strong><p>${escape(info.text)}</p>${info.message&&info.message!==info.text?`<p class="hint">Meldung: ${escape(info.message)}</p>`:""}
    <div class="actions">${raise}<button class="small" data-action="resend-chat" ${running()?"disabled":""}>Erneut senden</button></div></div>`;
}
// Setup: the conversation fills the working column with the composer pinned at its foot; the proposal sits in the rail.
function renderBrief() {
  const {proposal,config:c,audio:a,text:t}=setupSelection(), chat=project?.chat||[];
  const compatible=boot.capabilities?.conversational_setup;
  const nextPage=recommendedPage()===PAGE.brief?PAGE.research:recommendedPage();
  const voices=audioCatalog()[a.provider]?.voices||[];
  const presets=boot.text_catalog?.presets||[];
  const chosen=presets.find(p=>t.provider===p.provider&&t.model===p.model&&(!p.reasoning_effort||t.reasoning_effort===p.reasoning_effort));
  const attachmentCount=(project?.attachments?.length||0)+pendingAttachments.length;
  const messages=chat.length?chat.map(m=>`<div class="chat-message ${m.role==="user"?"user":""}"><strong>${m.role==="user"?"Du":"Redaktion"}</strong><p>${escape(m.message)}</p></div>`).join(""):'<div class="chat-message"><strong>Redaktion</strong><p>Worum soll dein Podcast gehen – und was möchtest du danach besser verstehen? Du kannst direkt auch Wünsche zu Sprache, Tiefe oder Stimmen nennen.</p></div>';
  const otherJob=running()&&(project?.main_job??project?.job)?.action!=="assistant";
  return heading(1)+
    `${!compatible?'<p class="note">Die Gesprächseinrichtung benötigt einen Studio-Neustart. Lass den laufenden Auftrag fertigarbeiten, beende dann das Studio und öffne es erneut.</p>':""}
    <div id="stop-card"></div>
    <div class="split"><div class="split-main">
    <section class="panel chat-panel"><div class="conversation" id="conversation">${messages}${chatStatusBubble()}<div id="chat-end"></div></div>
    ${!running()&&proposal?.suggested_replies?.length?`<div class="actions suggested">${proposal.suggested_replies.map(reply=>`<button class="secondary small" data-setup-reply="${escape(reply)}">${escape(reply)}</button>`).join("")}</div>`:""}
    ${otherJob?'<p class="hint composer-lock">Während ein Auftrag läuft, ruht das Gespräch. Danach kannst du wieder schreiben.</p>':""}
    <form id="chat-form" class="composer"><fieldset ${running()||setupSending||readingAttachments||!compatible?"disabled":""}>${area("chat-message","Deine Nachricht","",3)}
    <div class="composer-tools">
    ${presets.length?`<details class="composer-menu"><summary>Textmodell: ${chosen?escape(chosen.label):textChoiceSummary(t)}</summary><div class="text-model-picker"><span>Textmodell wählen</span><div class="actions">${presets.map(p=>`<button type="button" class="secondary small" data-text-preset="${escape(p.id)}" aria-pressed="${t.provider===p.provider&&t.model===p.model&&(!p.reasoning_effort||t.reasoning_effort===p.reasoning_effort)}">${escape(p.label)}</button>`).join("")}</div><p class="hint">Die Auswahl kommt in den Vorschlag und wird mit „Diese Auswahl übernehmen“ gespeichert. OpenRouter nutzt API-Guthaben. Codex- und Claude-Abo verursachen keine API-Kosten; die automatische Wahl nimmt Claude und springt bei leerem Kontingent auf Codex um. Live-Recherche läuft über das gewählte Abo; Stimmen wählst du separat.</p></div></details>`:""}
    ${boot.capabilities?.project_attachments?`<details class="composer-menu"${attachmentCount?" open":""}><summary>Dateien anhängen${attachmentCount?` · ${attachmentCount}`:""}</summary><div class="attachment-picker"><label for="chat-files">Dateien anhängen · .md / .txt / .docx</label><input id="chat-files" type="file" accept=".md,.txt,.docx,text/plain,text/markdown,application/vnd.openxmlformats-officedocument.wordprocessingml.document" multiple aria-describedby="attachment-hint"><p id="attachment-hint" class="hint">Für deine Projektidee und als Ausgangsmaterial der Recherche. Bis zu 10 Dateien: Text je 256 KiB, DOCX je 2 MiB, insgesamt 1 MiB eingelesener Text. DOCX übernimmt Text und Tabellen, keine Bilder. Mit „Senden“ erhält dein Textmodell den Inhalt; bei langen Dateien zunächst gekennzeichnete Auszüge. Die Recherche liest die vollständigen Textkopien ein.</p><div id="attachment-list">${renderAttachments()}</div></div></details>`:'<p class="hint">Dateianhänge benötigen einen Studio-Neustart nach Ende laufender Aufträge.</p>'}
    <button type="submit">${setupSending?"Wird gesendet …":readingAttachments?"Dateien werden eingelesen …":"Senden"}</button></div></fieldset></form></section>
    </div><aside class="split-rail">
    ${setupSummary()}${proposalChangesBrief()?pausedHint(["config"]):""}
    <details class="panel"><summary>Stimmen anhören</summary><p class="hint">${a.provider==="qwen3_local"?"Qwen":"Gemini"} · ${c.language==="en-US"?"English":"Deutsch"}. Sag dem Partner anschließend, welche beiden Stimmen du möchtest. Neue Gemini-Proben nutzen dein API-Guthaben.</p><div class="voice-library">${voices.map(v=>`<div class="sample-row"><strong>${escape(v)}</strong><button class="secondary small" data-preview-voice="${escape(v)}" data-preview-provider="${a.provider}" data-language="${c.language}" ${a.provider!=="qwen3_local"&&!savedSample(v,c.language)&&running()?"disabled":""}>${sampleButtonLabel(a.provider,v,c.language)}</button></div>`).join("")}</div>
    ${a.provider==="openrouter_gemini_tts"?`<div id="voice-library-panel">${renderVoiceLibrary(c.language)}</div>`:""}</details>
    <details class="panel"><summary>Geschützter OpenRouter-Key-Eingang</summary><p class="hint">Falls du OpenRouter wählst, hinterlege den Key hier. Er wird nicht an den redaktionellen Partner gesendet und bleibt nur im Sitzungsspeicher.</p>
    ${textInput("api-key","OpenRouter-Key","","password")}<p id="key-status" class="hint">${boot.key_available?"Ein Key ist für diese Sitzung verfügbar.":"Noch kein Key hinterlegt."}</p><div class="actions"><button class="secondary small" data-action="store-key">Key hinterlegen</button><button class="secondary small" data-action="forget-key">Sitzungs-Key entfernen</button></div></details>
    ${project?`<div class="actions"><button class="secondary" data-action="check" ${disabled()}>Verbindungen prüfen</button><button data-step="${nextPage}" ${proposal&&!project.proposal_applied?"disabled":""}>Weiter: ${steps[nextPage]} →</button></div>`:""}
    </aside></div>`;
}
// Render the Markdown used by dossiers, escaping all source text. Raw HTML and
// embedded images stay inert; only explicit HTTP(S) destinations become links.
function markdownLink(label, destination) {
  if (!/^https?:\/\//i.test(destination) || /[\s<>"'\\\u0000-\u001f\u007f]/.test(destination)) return label;
  try {
    const url = new URL(destination);
    if (!url.hostname || url.username || url.password) return label;
    return `<a href="${escape(url.href)}" target="_blank" rel="noopener noreferrer">${label}</a>`;
  } catch { return label; }
}
function markdownInline(value, depth=0, links=true) {
  const text=String(value);
  if (depth>8) return escape(text);
  const tokens=/\\[\\`*{}\[\]()#+.!_>~-]|`+|!?\[[^\]\n]*\]\(|\*\*|__|\*|_|https?:\/\/[^\s<>]+/g;
  let html="", cursor=0, match;
  while ((match=tokens.exec(text))) {
    const token=match[0], start=match.index;
    html+=escape(text.slice(cursor,start));
    let end=tokens.lastIndex, rendered=escape(token);
    if (token.startsWith("\\")) rendered=escape(token.slice(1));
    else if (token.startsWith("`")) {
      const close=text.indexOf(token,end);
      if (close>=0) { rendered=`<code>${escape(text.slice(end,close))}</code>`; end=close+token.length; }
    } else if (/^!?\[/.test(token)) {
      let nesting=1, close=end;
      for (;close<text.length && nesting;close++) {
        if (text[close]==="\\") { close++; continue; }
        if (text[close]==="(") nesting++;
        if (text[close]===")") nesting--;
      }
      if (!nesting) {
        const isImage=token.startsWith("!"), label=token.slice(isImage?2:1,-2);
        const destination=text.slice(end,close-1).trim();
        rendered=markdownInline(label,depth+1,false);
        if (links && !isImage) rendered=markdownLink(rendered,destination);
        end=close;
      }
    } else if (/^https?:\/\//i.test(token)) {
      // Sentence punctuation and an unmatched closing parenthesis are not URL content.
      let url=token.replace(/[.,;:!?]+$/, "");
      while (url.endsWith(")") && (url.match(/\)/g)||[]).length>(url.match(/\(/g)||[]).length) url=url.slice(0,-1);
      rendered=links?markdownLink(escape(url),url):escape(url);
      end=start+url.length;
    } else {
      const close=text.indexOf(token,end);
      const inWord=token.includes("_") && /[\p{L}\p{N}]/u.test(text[start-1]||"");
      if (!inWord && close>end && !/^\s|\s$/.test(text.slice(end,close))) {
        const tag=token.length===2?"strong":"em";
        rendered=`<${tag}>${markdownInline(text.slice(end,close),depth+1,links)}</${tag}>`;
        end=close+token.length;
      }
    }
    html+=rendered; cursor=end; tokens.lastIndex=end;
  }
  return html+escape(text.slice(cursor));
}
// With a `toc` array the headings also get an anchor before them, collected for a table of contents.
function renderMarkdown(value, depth=0, toc=null) {
  if (depth>16) return `<p>${escape(value)}</p>`;
  const lines=String(value??"").replace(/\r\n?/g,"\n").split("\n"), blocks=[];
  const listItem=line=>line.match(/^( *)([-+*]|\d+[.)])\s+(.*)$/);
  const blockStart=line=>/^\s*$|^ {0,3}(#{1,6}\s|`{3,}|~{3,}|>|(?:-\s*){3,}$|(?:\*\s*){3,}$|(?:_\s*){3,}$)/.test(line)||listItem(line);
  let i=0;
  while (i<lines.length) {
    const line=lines[i];
    if (!line.trim()) { i++; continue; }
    const fence=line.match(/^ {0,3}(`{3,}|~{3,})[^`]*$/);
    if (fence) {
      const code=[], closing=new RegExp(`^ {0,3}${fence[1][0]}{${fence[1].length},}\\s*$`);
      i++;
      while(i<lines.length && !closing.test(lines[i])) code.push(lines[i++]);
      if(i<lines.length)i++;
      blocks.push(`<pre><code>${escape(code.join("\n"))}</code></pre>`); continue;
    }
    const heading=line.match(/^ {0,3}(#{1,6})\s+(.+?)\s*#*$/);
    if (heading) {
      // The dossier lives inside a section with its own h2.
      const level=Math.min(6,heading[1].length+2);
      let anchor="";
      if (toc) { const id=`doc-h-${toc.length+1}`; toc.push({level:heading[1].length,id,html:markdownInline(heading[2],0,false)}); anchor=`<span class="doc-anchor" id="${id}"></span>`; }
      blocks.push(`${anchor}<h${level}>${markdownInline(heading[2])}</h${level}>`); i++; continue;
    }
    if (/^ {0,3}(?:(?:-\s*){3,}|(?:\*\s*){3,}|(?:_\s*){3,})$/.test(line)) { blocks.push("<hr>"); i++; continue; }
    if (/^ {0,3}>/.test(line)) {
      const quote=[];
      while(i<lines.length && /^ {0,3}>/.test(lines[i])) quote.push(lines[i++].replace(/^ {0,3}> ?/,""));
      blocks.push(`<blockquote>${renderMarkdown(quote.join("\n"),depth+1,toc)}</blockquote>`); continue;
    }
    const first=listItem(line);
    if (first) {
      const ordered=/^\d/.test(first[2]), indent=first[1].length, items=[];
      while(i<lines.length) {
        const item=listItem(lines[i]);
        if (!item || item[1].length!==indent || /^\d/.test(item[2])!==ordered) break;
        const content=[item[3]], contentIndent=lines[i].length-item[3].length;
        i++;
        while(i<lines.length) {
          if (!lines[i].trim()) {
            let next=i+1;
            while(next<lines.length && !lines[next].trim()) next++;
            if(next<lines.length && lines[next].startsWith(" ".repeat(contentIndent))) {content.push("");i=next;continue;}
            i=next; break;
          }
          if (!lines[i].startsWith(" ".repeat(contentIndent))) break;
          content.push(lines[i++].slice(contentIndent));
        }
        items.push(`<li>${renderMarkdown(content.join("\n"),depth+1,toc)}</li>`);
      }
      const tag=ordered?"ol":"ul", start=ordered?` start="${Number.parseInt(first[2],10)||1}"`:"";
      blocks.push(`<${tag}${start}>${items.join("")}</${tag}>`); continue;
    }
    const paragraph=[line]; i++;
    while(i<lines.length && !blockStart(lines[i])) paragraph.push(lines[i++]);
    blocks.push(`<p>${markdownInline(paragraph.join("\n"))}</p>`);
  }
  return blocks.join("\n");
}
function tocMarkup(entries, title) {
  return `<nav class="toc" aria-label="${title}"><p class="toc-title">${title}</p>${entries.map(e=>`<button type="button" class="toc-link level-${Math.min(3,Math.max(1,Number(e.level)||1))}" data-scroll="${escape(e.id)}">${e.html}</button>`).join("")}</nav>`;
}
function researchProviderTag() {
  const t=project?.job?.text_generation||project?.text, provider=t?.provider;
  if(provider==="auto")return "Recherche über die Abos";
  return provider?`Recherche mit ${providerNames[provider]||escape(provider)}`:"Recherche mit Codex";
}
// Research: while a run is open the ledger is the page; the finished dossier is a document with a table of contents.
function renderResearch() {
  let html=heading(2);
  if(!project) return html+empty("Ein Thema fehlt noch.","Lege zuerst deinen Podcast-Auftrag an.","Zur Idee",0,pageIntros.research);
  html+='<div id="stop-card"></div>';
  const run=currentRun(), researching=run?.kind==="research"&&run.status!=="completed";
  const attachments=project.attachments?.length?`<section class="panel"><h2>Deine Ausgangsmaterialien</h2><ul>${project.attachments.map(row=>`<li>${escape(row.name)}</li>`).join("")}</ul><p class="hint">Diese Dateien werden als lokale Quellen eingelesen. Aussagen aus deinen Notizen werden anhand weiterer Quellen geprüft. Sehr kurze Notizen dienen vor allem der Projektbeschreibung.</p></section>`:"";
  const brief=`<section class="panel"><div class="panel-title"><h2>Quellen und Erkenntnisse</h2><span class="tag">${researchProviderTag()}</span></div><p>Der gespeicherte Auftrag: <strong>${escape(project.config.central_question||project.config.topic)}</strong></p><div class="actions">${researching?'<p>Die aktuelle Recherche ist noch nicht abgeschlossen. Der Prüfstand steht auf dieser Seite; das Inhaltsverzeichnis folgt erst nach bestandener Qualitätsprüfung.</p>':project.research?(project.outline?'<button data-step="2">Zum Inhaltsverzeichnis →</button>':`<button data-action="plan" ${disabled()}>Inhaltsverzeichnis entwerfen →</button>`):`<button data-action="research" ${disabled()}>Recherche starten</button>`}</div>${(project.research||researching)?`<details class="restart-options"><summary>Recherche neu beginnen</summary><p>${researching?"Das startet einen neuen Recherchelauf mit neuem Plan und neuer Hochrechnung. Der angehaltene Lauf bleibt gespeichert, wird aber nicht fortgesetzt.":"Das startet einen neuen Recherchelauf. Den bisherigen Stand kannst du unten lesen."}</p><button class="secondary" data-action="research" ${disabled()}>Neu recherchieren</button></details>`:'<p class="hint">Quellen suchen, lesen, nachrecherchieren und prüfen läuft nach dem Start automatisch.</p>'}</section>`;
  if(researching||(!project.research&&project.job?.progress?.phase==="research"))
    return html+`<div class="split"><div class="split-main"><div id="research-progress"></div>${project.research?`<section class="panel"><h2>Bisheriges Dossier · wird neu recherchiert</h2><article class="markdown-document">${renderMarkdown(project.research)}</article></section>`:""}</div><aside class="split-rail"><div id="research-live"></div>${brief}${attachments}</aside></div>`;
  if(project.research){
    const toc=[], dossier=renderMarkdown(project.research,0,toc);
    return html+`<div class="doc">${tocMarkup(toc,"Inhalt des Dossiers")}<section class="panel doc-main"><h2>Dein Recherche-Dossier</h2><article class="markdown-document">${dossier}</article></section><aside class="doc-rail">${brief}${attachments}</aside></div>`;
  }
  return html+`<div class="split"><div class="split-main">${brief}</div><aside class="split-rail">${attachments}</aside></div>`;
}
function renderOutline() {
  let html=heading(3);
  const outline=project?.outline;
  if(project)html+='<div id="stop-card"></div>';
  const drafting=project?.job?.status==="running"&&jobPage()===PAGE.outline;
  const activity=drafting&&project.job.progress?.activity?`<p><span class="activity-dot" aria-hidden="true"></span>${escape(project.job.progress.activity)} · seit ${elapsedText(project.job.started_at)}</p>`:"";
  if(!outline) {
    if(drafting)return html+`<section class="panel tinted"><h2>Das Inhaltsverzeichnis wird ausgearbeitet.</h2>${activity}<p>Folgen und Kapitel erscheinen hier, sobald der Entwurf bereit für deine Durchsicht ist. Ein Widerspruch im Entwurf wird automatisch bis zu dreimal korrigiert.</p></section>`;
    return html+empty("Das Inhaltsverzeichnis entsteht aus der Recherche.","Nach dem geprüften Dossier entwirft die Redaktion Folgen und Kapitel. Hier kannst du sie anschließend verändern und freigeben.","Zur Recherche",PAGE.research,pageIntros.outline);
  }
  const p=outline.plan, approved=outline.approval?.plan_hash===outline.hash;
  // During a revision the page still shows the previous draft; it cannot be approved until the new one is ready.
  if(drafting)html+=`<section class="panel note-card" role="status"><h2>Das Inhaltsverzeichnis wird überarbeitet.</h2>${activity}<p>Unten steht noch der bisherige Entwurf. Freigeben lässt sich erst der neue Stand.</p></section>`;
  html+=`<section class="panel tinted"><h2 class="outline-question">${escape(p.central_question)}</h2><p>${escape(p.explanation_path)}</p><div class="outline-summary"><span><strong>${p.episodes.length}</strong> Folgen</span><span><strong>${Math.round(p.episodes.reduce((s,e)=>s+e.target_minutes,0))}</strong> Minuten geplant</span><span>${approved?"Dieser Stand wurde freigegeben":"Wartet auf deine Durchsicht"}</span></div><p class="hint">${escape(p.scope_note)}</p></section>`;
  html+=p.episodes.map((e,i)=>`<section class="panel"><div class="episode-head"><span class="episode-num">${String(i+1).padStart(2,"0")}</span><div><h2>${escape(e.title)}</h2><p>${escape(e.central_question)}</p></div><span class="tag">ca. ${Math.round(e.target_minutes)} Min.</span></div><ol class="chapters">${e.scenes.map((s,n)=>`<li><span>${String(n+1).padStart(2,"0")}</span><div><strong>${escape(s.title)}</strong><p>${escape(s.question)}</p><details><summary>Was hier erklärt wird</summary>${s.explanation_steps.map(x=>`<p>${escape(x)}</p>`).join("")}</details></div></li>`).join("")}</ol>${e.deferred_questions.length?`<details><summary>Offene oder spätere Fragen</summary>${e.deferred_questions.map(q=>`<p>${escape(q)}</p>`).join("")}</details>`:""}</section>`).join("");
  const canReplan = !Object.entries(project.job?.run?.stages || {}).some(([n,r])=>n!=="planning"&&r.attempts>0);
  html+=approved?`<section class="panel tinted"><h2>Dieser Plan ist freigegeben.</h2><p>Lehrkonzept, Skriptentwurf, Dialog-Polishing und Qualitätsprüfung gehören zur automatischen Ausarbeitung. Deine nächste inhaltliche Entscheidung triffst du beim Lesen der fertigen Skripte.</p><button data-step="${PAGE.production}">Ausarbeitung ansehen →</button></section>`:
    `<div class="action-bar"><details class="action-note"><summary>Änderungswünsche an die Redaktion</summary>${area("outline-feedback","Was soll sich ändern?","",3)}</details><div class="actions"><button class="secondary" data-action="replan" ${running()||!canReplan?"disabled":""}>Plan überarbeiten lassen</button><button data-action="script" ${disabled()}>Plan freigeben & Skripte schreiben</button></div><p class="hint">Deine Freigabe startet die automatische Ausarbeitung. Die fertigen Skripte liest du anschließend vor der Vertonung.</p></div>`;
  if(!canReplan)html+=`<details class="restart-options"><summary>Eine neue Gliederung erstellen</summary><p>Der bisherige Auftrag bleibt gespeichert. Eine neue Gliederung benötigt wieder deine Freigabe.</p><button class="secondary" data-action="plan" ${disabled()}>Neues Inhaltsverzeichnis entwerfen</button></details>`;
  return html;
}
function renderProduction() {
  const html=heading(4);
  const run=currentRun();
  if(!approvedOutline()&&!hasProduction(run))return html+empty("Zuerst das Inhaltsverzeichnis prüfen.","Deine Freigabe startet Lehrkonzept, Schreiben, Polishing und Qualitätsprüfung als zusammenhängenden Auftrag.","Zum Inhaltsverzeichnis",PAGE.outline,pageIntros.production);
  return html+'<div id="stop-card"></div><div id="production-progress"></div>';
}
function renderProductionDetails() {
  const run=scriptRun();
  const active=project?.job?.status==="running"&&jobPage()===PAGE.production;
  const finished=scriptsFinished();
  const labels={completed:"Abgeschlossen",running:"In Arbeit",pending:"Folgt automatisch",blocked:"Angehalten",failed:"Angehalten",waiting_for_quota:"Wartet auf Anbieter",interrupted:"Unterbrochen"};
  let html=`<section class="panel"><div class="panel-title"><h2>Die Ausarbeitung</h2><span class="tag">${active?"Läuft automatisch":finished?"Bereit zum Lesen":"Gespeicherter Stand"}</span></div><ol class="production-stages">${productionStages.map(([key,title,description])=>{
    const record=run?.stages?.[key];
    // A stopped stage goes back to pending with the interruption noted; it resumes, it does not simply follow.
    const status=record?.status==="pending"&&record?.error?.code==="interrupted"?"interrupted":record?.status||(finished?"completed":"pending");
    return `<li class="${escape(status)}"><span class="phase-marker" aria-hidden="true">${status==="completed"?"✓":status==="running"?"●":status==="pending"?"○":"!"}</span><div><strong>${title}</strong><p>${description}</p></div><span class="phase-status">${labels[status]||"Ausstehend"}</span></li>`;
  }).join("")}</ol><p class="hint">Notwendige Nachrecherche und interne Korrekturen gehören zu diesen Schritten. Gespeicherte Skriptfassungen lassen sich bereits während der Ausarbeitung lesen.</p></section>`;
  html+=renderScriptProgress(project?.job?.progress,active);
  const readable=readableScripts().length;
  if(!finished&&readable)html+=`<section class="panel tinted"><h2>${readable} ${readable===1?"Folge ist bereits lesbar":"Folgen sind bereits lesbar"}.</h2><p>Du kannst die gespeicherten Texte jetzt lesen. Der Prüfstand steht bei jeder Folge; die Ausarbeitung läuft weiter.</p><button data-step="${PAGE.scripts}">Skripte jetzt lesen →</button></section>`;
  const issues=project?.job?.progress?.review_issues||[];
  const issueEpisode=project?.job?.progress?.issues_episode;
  if(issues.length)html+=`<section class="panel"><h2>${project?.job?.progress?.stage==="review"?"Offene Punkte der Qualitätsprüfung":"Was noch erklärt werden muss"}${issueEpisode?` · ${escape(issueEpisode)}`:""}</h2><ul>${issues.map(issue=>`<li>${escape(typeof issue==="string"?issue:issue.reason||"Offener Prüfpunkt")}</li>`).join("")}</ul></section>`;
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
const reviewNoteLabels={script_review:"Quellen- und Skriptprüfung",teaching_review:"Lehrprüfung",editorial_review:"Redaktionelle Prüfung",dialogue_polish:"Dialogvergleich",dismissed_gaps:"Eingeordnete Erklärlücken",advisories:"Deterministische Hinweise"};
function renderReviewNotes(entry) {
  // The published report is the only source; a preview has been through no full review yet.
  const notes=entry.preview?null:project.episodes.find(row=>row.script.episode_id===entry.script.episode_id)?.review_notes;
  const groups=Object.keys(reviewNoteLabels).filter(name=>notes?.[name]?.length);
  if(!groups.length) return "";
  const line=(name,row)=>name==="dismissed_gaps"?`<strong>${escape(row.gap)}</strong><p>${escape(row.reason)}</p>`
    :name==="advisories"?`<strong>${escape(row.code)}</strong><p>${escape(row.detail)}${row.segment_ids?.length?` (${row.segment_ids.map(escape).join(", ")})`:""}</p>`
    :`<p>${escape(row)}</p>`;
  return `<details class="panel review-notes"><summary>Hinweise der Prüfungen</summary>
    <p class="hint">Diese Punkte haben die Veröffentlichung nicht verhindert. Die Prüfungen nennen sie als Grenze ihres eigenen Urteils oder als beobachtetes Muster.</p>
    ${groups.map(name=>`<h3>${escape(reviewNoteLabels[name])}</h3><ul>${notes[name].map(row=>`<li>${line(name,row)}</li>`).join("")}</ul>`).join("")}</details>`;
}
function hostLabels() {
  const names=project?.config?.host_names||{};
  return {host_a:names.host_a||"Host A",host_b:names.host_b||"Host B"};
}
// The same boundary rule as spoken_forms.apply: a hyphen, en dash or slash separates tokens,
// a dot between word characters does not; the longest written form wins in one pass.
function spokenFormPattern(table) {
  const written=[...new Set((table?.entries||[]).map(row=>row.written).filter(Boolean))].sort((a,b)=>b.length-a.length);
  if(!written.length)return null;
  const escaped=written.map(value=>value.replace(/[.*+?^${}()|[\]\\]/g,"\\$&"));
  return new RegExp(`(?<![\\p{L}\\p{N}_])(?<![\\p{L}\\p{N}_]\\.)(${escaped.join("|")})(?![\\p{L}\\p{N}_])(?!\\.[\\p{L}\\p{N}_])`,"gu");
}
function applySpokenForms(text, table) {
  const pattern=spokenFormPattern(table);
  if(!pattern)return String(text);
  const spoken=new Map((table.entries||[]).map(row=>[row.written,row.spoken]));
  return String(text).replace(pattern,match=>spoken.get(match));
}
function renderSpokenOverride(episodeId,segment,overrides) {
  const current=overrides[segment.segment_id]||"";
  // The field starts from what the table already produces, so saving it unchanged creates no
  // override and the table keeps working for this segment.
  return `<details class="spoken-override"><summary>Sprechform${current?" · gesetzt":""}</summary>
    <p class="hint">Nur der Klang ändert sich. Der Text dieser Folge, sein Hash und deine Freigabe bleiben, wie sie sind.</p>
    ${area("spoken-"+segment.segment_id,"Wie soll dieser Abschnitt gesprochen werden?",current||applySpokenForms(segment.text,project.spoken_forms),3)}
    <div class="actions"><button class="secondary small" data-action="spoken-override" data-episode="${escape(episodeId)}" data-segment="${escape(segment.segment_id)}" ${disabled()}>Sprechform speichern</button>
    <button class="secondary small" data-action="audio" data-rerender="true" data-episode="${escape(episodeId)}" ${disabled()}>Nur diesen Abschnitt neu rendern</button></div></details>`;
}
function audioRequest(episodeId, rerender=false) {
  // From the reading page the saved approval stands: script hash and voices are unchanged and
  // only a spoken form differs, so the re-render is not a new editorial decision. The server
  // checks the saved approval by the pipeline's own rule before it starts anything.
  const e=episodeId?project.episodes.find(row=>row.script.episode_id===episodeId):project.episodes[episodeIndex];
  return {episode:e.script.episode_id,approve_audio:!rerender&&!!$("audio-approval")?.checked,
    ...(rerender?{rerender:true}:{}),script_hash:e.hash,readable_hash:e.readable_hash,
    config_hash:project.config_hash,audio_hash:project.audio_hash};
}
async function rerenderEpisode(episodeId) {
  await start("audio",audioRequest(episodeId,true));
}
async function saveSpokenOverride(episodeId, segmentId) {
  const field=$("spoken-"+segmentId), value=field?field.value:"";
  const segment=project.episodes.find(row=>row.script.episode_id===episodeId)?.script.segments.find(s=>s.segment_id===segmentId);
  // What the table already produces is not an override; sending it empty removes a stale one.
  const spoken=segment&&value.trim()===applySpokenForms(segment.text,project.spoken_forms)?"":value;
  const saved=project.episodes.find(row=>row.script.episode_id===episodeId)?.spoken_overrides?.[segmentId]||"";
  if(spoken.trim()!==saved.trim()&&!confirmPaused(["audio"]))return;
  await api(`/api/projects/${project.id}/spoken_override`,{episode:episodeId,segment_id:segmentId,spoken});
  project=await api(`/api/projects/${project.id}`);readingSnapshot=null;render();
  notice(spoken?"Sprechform gespeichert. Mit „Neu rendern“ wird nur dieser Abschnitt neu vertont.":"Keine abweichende Sprechform; die Tabelle gilt für diesen Abschnitt.","ok");
}
async function saveSpeechSettings() {
  const hostA=$("host-name-a").value.trim(), hostB=$("host-name-b").value.trim();
  if(!!hostA!==!!hostB)throw new Error("Beide Hostnamen angeben oder beide Felder leer lassen.");
  const names=project.config?.host_names||{}, pauses=currentAudio().pauses||{same_speaker_ms:250,speaker_change_ms:450,chapter_break_ms:900};
  const changes=[];
  if(hostA!==(names.host_a||"")||hostB!==(names.host_b||""))changes.push("config");
  if(["same_speaker_ms","speaker_change_ms","chapter_break_ms"].some((key,i)=>Number($(["pause-same","pause-change","pause-chapter"][i]).value)!==Number(pauses[key]))||
      JSON.stringify(parseSpokenForms($("spoken-forms").value).entries)!==JSON.stringify(project.spoken_forms?.entries||[]))changes.push("audio");
  if(!confirmPaused(changes))return;
  await api(`/api/projects/${project.id}/save`,{config:{...project.config,host_names:hostA?{host_a:hostA,host_b:hostB}:null},
    config_hash:project.config_hash,text:project.text,audio_settings:{...currentAudio(),pauses:{same_speaker_ms:Number($("pause-same").value),
      speaker_change_ms:Number($("pause-change").value),chapter_break_ms:Number($("pause-chapter").value)}},
    audio_hash:project.audio_hash,spoken_forms:parseSpokenForms($("spoken-forms").value),
    spoken_forms_hash:project.spoken_forms_hash});
  project=await api(`/api/projects/${project.id}`);render();
  notice("Gespeichert. Geänderte Pausen benötigen eine neue Audio-Freigabe; geänderte Hostnamen gelten für neue Skriptläufe.","ok");
}
function refreshScriptReader() {
  if(!readingSnapshot||!$("script-reader-controls")){$("content").innerHTML=renderScript();return;}
  // Only the picker and status change. Keep the text DOM, selection and feedback intact.
  $("script-reader-controls").innerHTML=renderReaderControls();
}
// Reading: a sticky reader bar, chapters as a table of contents, the text in a measured column, notes in the margin.
function renderScript() {
  let html=heading(5);
  const entries=readerEntries();
  if(!entries.length) return html+(approvedOutline()||hasProduction(currentRun())?
    empty("Der erste Skriptentwurf entsteht noch.","Diese Seite aktualisiert sich automatisch, sobald ein vollständiger Entwurf gespeichert ist. Die übrigen Folgen dürfen währenddessen weiterlaufen.","Zur Ausarbeitung",PAGE.production,pageIntros.scripts):
    empty("Zuerst den roten Faden festlegen.","Prüfe das Inhaltsverzeichnis und gib es frei. Danach entsteht das vollständige Gespräch.","Zum Inhaltsverzeichnis",PAGE.outline,pageIntros.scripts));
  const selected=entries.find(e=>e.script.episode_id===scriptEpisodeId)||entries[0];
  scriptEpisodeId=selected.script.episode_id;
  if(readingSnapshot?.projectId!==project.id||readingSnapshot.entry.script.episode_id!==scriptEpisodeId)readingSnapshot={projectId:project.id,entry:structuredClone(selected)};
  const e=readingSnapshot.entry,s=e.script;
  if(!e.preview)episodeIndex=Math.max(0,project.episodes.findIndex(row=>row.script.episode_id===s.episode_id));
  const published=e.preview?null:project.episodes.find(row=>row.script.episode_id===s.episode_id);
  const spoken=published?.audio?.length?(published.spoken_overrides||{}):null;
  const toc=s.chapters.map((chapter,i)=>({level:1,id:`ch-${chapter.chapter_id}`,html:`${i+1}. ${escape(chapter.title)}`}));
  let text=`<h2 class="reader-title">${escape(s.title)}</h2>`;
  for(const chapter of s.chapters) text+=`<span class="doc-anchor" id="ch-${escape(chapter.chapter_id)}"></span><h3>${escape(chapter.title)}</h3>`+s.segments.filter(x=>x.chapter_id===chapter.chapter_id).map(x=>`<div class="utterance ${x.speaker_id}"><strong>${escape(hostLabels()[x.speaker_id])}</strong><p>${escape(x.text)}</p>${spoken?renderSpokenOverride(s.episode_id,x,spoken):""}</div>`).join("");
  const feedback=e.preview?`<section class="panel"><p class="hint">Nach Abschluss der Ausarbeitung kannst du Rückmeldung für eine weitere Überarbeitung geben und über die Vertonung entscheiden.</p><button class="secondary" data-step="${PAGE.production}">Ausarbeitung verfolgen</button></section>`
    :`<section class="panel"><h2>Deine redaktionelle Rückmeldung</h2>${area("script-feedback","Was fehlt oder klingt noch nicht richtig?","",4)}<div class="actions"><button class="secondary" data-action="revise" ${disabled()}>Diese Folge überarbeiten lassen</button><button data-step="${PAGE.audio}">Weiter zur Audio-Freigabe →</button></div><p class="hint">Eine Überarbeitung durchläuft erneut Polishing und Prüfung. Sie erhält eine neue Audio-Freigabe.</p></section>`;
  html+=`<div id="script-reader-controls" class="reader-bar">${renderReaderControls()}</div><div class="doc">${tocMarkup(toc,"Kapitel")}<article id="script-text" class="panel reader doc-main">${text}</article><aside class="doc-rail"><div class="outline-summary"><span>${e.metrics.words.toLocaleString("de-DE")} Wörter</span><span>ca. ${Math.round(e.metrics.estimated_minutes)} Min. geschätzt</span><span>${s.chapters.length} Kapitel</span></div>${renderReviewNotes(e)}${feedback}</aside></div>`;
  return html;
}
const NEWLINE=String.fromCharCode(10);
const pronunciationLabels={numbers:"Mehrstellige Zahlen",abbreviations:"Abkürzungen",versions:"Versions- und Modellnamen",foreign:"Fremdsprachige Wörter"};
function renderPronunciation(e) {
  // Model-free, computed by the server from the published text, the table and the overrides,
  // so it is available before the first audio run and belongs above the approval.
  const report=e.pronunciation;
  if(!report?.flagged||!Object.keys(report.flagged).length)return "";
  return `<details class="pronunciation"><summary>Aussprache prüfen · ${Object.values(report.flagged).reduce((n,rows)=>n+rows.length,0)} auffällige Wörter</summary>
    <p class="hint">Diese Wörter liest die Stimme nach eigener Regel. Prüfe sie vor der Freigabe; eine Sprechform ändert nur den Klang, nie den Text.</p>
    ${Object.entries(report.flagged).map(([name,rows])=>`<h3>${escape(pronunciationLabels[name]||name)}</h3><ul>${rows.map(row=>`<li><strong>${escape(row.token)}</strong> · ${row.count}× (${row.segment_ids.map(escape).join(", ")})</li>`).join("")}</ul>`).join("")}
    ${Object.keys(report.applied||{}).length?`<h3>Angewendete Sprechformen</h3><ul>${Object.entries(report.applied).map(([written,count])=>`<li><strong>${escape(written)}</strong> · ${count}×</li>`).join("")}</ul>`:""}</details>`;
}
function spokenFormsText(table) { return (table?.entries||[]).map(row=>`${row.written} = ${row.spoken}`).join(NEWLINE); }
function parseSpokenForms(text) {
  return {schema_version:"1.0",entries:String(text).split(NEWLINE).map(line=>line.split("=")).filter(parts=>parts.length>=2)
    .map(parts=>({written:parts[0].trim(),spoken:parts.slice(1).join("=").trim()})).filter(row=>row.written&&row.spoken)};
}
// A key field wherever a key is missing, not only on the first page. The key stays in the server's memory.
function inlineKey(id,resume=false,target="") {
  return `<div class="inline-key">${textInput(id,"OpenRouter-Key","","password")}<button class="secondary small" data-action="store-key" data-key-field="${id}"${resume?` data-then-resume="1" ${target}`:""}>Key hinterlegen${resume?" und fortsetzen":""}</button><p class="hint">Der Key bleibt nur im Speicher dieses Studio-Servers und muss nach einem Neustart erneut eingegeben werden.</p></div>`;
}
// Runs are bound to their inputs: saving what a paused run depends on ends its resumability. The warning
// names that before the save. config = brief and host names, notes = style notes, audio = pauses and spoken forms.
const BINDS={config:["research","script","episode_audio"],notes:["script"],audio:["episode_audio"]};
const RUN_LABELS={research:"Recherche",script:"Inhaltsverzeichnis oder Ausarbeitung",episode_audio:"Vertonung"};
function pausedRuns(p=project) {
  const kinds=new Set();
  for(const j of [p?.job,p?.main_job,...(p?.audio_jobs||[])])
    if(j?.run&&!["running","completed"].includes(j.status)&&j.run.status!=="completed")kinds.add(j.run.kind);
  return kinds;
}
function affectedRuns(changes) {
  const paused=pausedRuns();
  return [...new Set(changes.flatMap(change=>BINDS[change]||[]))].filter(kind=>paused.has(kind));
}
function pausedHint(changes) {
  const hit=affectedRuns(changes);
  return hit.length?`<p class="note">Achtung: Ein angehaltener Lauf (${hit.map(k=>RUN_LABELS[k]).join(", ")}) ist an diese Angaben gebunden. Nach dem Speichern lässt er sich nicht mehr fortsetzen und muss neu gestartet werden; fertige Ergebnisse bleiben lesbar.</p>`:"";
}
function confirmPaused(changes) {
  const hit=affectedRuns(changes);
  if(!hit.length||typeof window.confirm!=="function")return true;
  return window.confirm(`Ein angehaltener Lauf (${hit.map(k=>RUN_LABELS[k]).join(", ")}) ist an diese Angaben gebunden und lässt sich nach dem Speichern nicht mehr fortsetzen. Er muss dann neu gestartet werden; fertige Ergebnisse bleiben lesbar. Trotzdem speichern?`);
}
// Whether applying the partner's proposal rewrites the saved brief, which every run is bound to.
function proposalChangesBrief() {
  const {proposal}=setupSelection();
  if(!proposal||project?.proposal_applied)return false;
  const c=project?.config||{};
  const differs=key=>JSON.stringify(proposal[key]??null)!==JSON.stringify(c[key]??null);
  if(["topic","central_question","prior_knowledge","depth_request","focus_questions","excluded_topics","target_total_minutes"].some(differs))return true;
  if(["language","seed_urls"].some(key=>proposal[key]!=null&&differs(key)))return true;
  const a=proposal.audio_settings;
  return !!(a&&a.provider==="qwen3_local"&&JSON.stringify(a.voices)!==JSON.stringify(c.voice_profile));
}
function renderStyleNotes() {
  return `<details class="panel style-notes"><summary>Redaktionelle Notizen</summary>
    <p class="hint">Stehende Korrekturen für alle künftigen Folgen dieses Projekts. Sie gelten beim Schreiben, beim Dialog und in beiden Prüfungen; die Belegregeln haben Vorrang. Eine Änderung führt zu einem neuen Skriptlauf.</p>
    ${area("style-notes","Was soll immer anders gemacht werden?",project.style_notes||"",6)}
    ${pausedHint(["notes"])}
    <div class="actions"><button class="secondary" data-action="save-notes" ${disabled()}>Notizen speichern</button></div></details>`;
}
function renderSpeechSettings(a) {
  const pauses=a.pauses||{same_speaker_ms:250,speaker_change_ms:450,chapter_break_ms:900};
  const names=project.config?.host_names||{};
  const number=(id,label,value)=>`<div class="field"><label for="${id}">${escape(label)}</label><input id="${id}" type="number" min="0" max="10000" step="50" value="${Number(value)}"></div>`;
  return `<details class="panel speech-settings"><summary>Sprechformen, Pausen und Hostnamen</summary>
    <p class="hint">Gilt für alle Folgen dieses Projekts. Eine Änderung der Pausen verlangt eine neue Audio-Freigabe, weil sie hörbar ist.</p>
    ${area("spoken-forms","Sprechformen · eine Zeile je Eintrag: geschrieben = gesprochen",spokenFormsText(project.spoken_forms),5)}
    <p class="hint">Ein Bindestrich trennt Wörter, ein Punkt zwischen Zeichen nicht: der Eintrag „KL“ erreicht „KL-Abweichung“, der Eintrag „V3“ lässt „V3.2-Exp“ unverändert.</p>
    <div class="row">${number("pause-same","Gleiche Stimme (ms)",pauses.same_speaker_ms)}${number("pause-change","Stimmwechsel (ms)",pauses.speaker_change_ms)}</div>
    ${number("pause-chapter","Kapitelwechsel (ms)",pauses.chapter_break_ms)}
    <div class="row">${textInput("host-name-a","Name von Host A (optional)",names.host_a||"")}${textInput("host-name-b","Name von Host B (optional)",names.host_b||"")}</div>
    <p class="hint">Beide Namen oder keinen. Mit Namen sprechen sich die Hosts im Skript so an und Transkript und Leseseite zeigen sie; ohne Namen bleiben es Host A und Host B. Geänderte Namen gelten für neue Skriptläufe.</p>
    ${pausedHint(["config","audio"])}
    <div class="actions"><button class="secondary" data-action="save-speech" ${disabled()}>Sprechformen, Pausen und Hostnamen speichern</button></div></details>`;
}
function renderListeningReview(e) {
  return `<section class="panel"><h2>Hörprüfung</h2>
    <p class="hint">Der Prüfbogen liegt im Export neben der MP3. Diese Angabe setzt nur ein Mensch.</p>
    <label class="approval"><input id="listening-done" type="checkbox" ${e.human_listening_reviewed?"checked":""}><span>Ich habe diese Folge vollständig gehört.</span></label>
    ${area("listening-note","Was ist beim Hören aufgefallen?",e.listening_note||"",3)}
    <div class="actions"><button class="secondary" data-action="listening-review" ${disabled()}>Hörprüfung eintragen</button></div></section>`;
}
// Audio: the approval is a checks card that names exactly what it binds; the recordings follow on the same page.
function renderApprovalCard(e,a,remote) {
  const blocked=audioBlockReason();
  const pauses=a.pauses||{same_speaker_ms:250,speaker_change_ms:450,chapter_break_ms:900};
  const pronunciation=renderPronunciation(e);
  return `<div class="panel-title"><h2>${escape(e.script.title)}</h2><span class="tag">${escape(audioLabel(a))}</span></div>
    <dl>
    <dt>Text</dt><dd>${e.audio?.length?(e.audio_current?"Aufnahme vorhanden · dieser Stand ist bereits vertont":"Aufnahme eines früheren Skript- oder Stimmenstands vorhanden"):"Fertig zur Durchsicht · noch nicht vertont"} <button class="quiet small" data-step="4">Skript lesen</button></dd>
    <dt>Stimmen</dt><dd>${escape(a.voices.host_a)} & ${escape(a.voices.host_b)} · ${project.config.language==="de-DE"?"Deutsch":"English"} <button class="quiet small" data-step="0">Audioanbieter oder Stimmen ändern</button></dd>
    <dt>Anbieter</dt><dd class="hint">${remote?"Gemini erzeugt die Sprache über OpenRouter und nutzt dafür dein API-Guthaben. Deine Grafikkarte wird für die Vertonung nicht benötigt.":"Qwen erzeugt die Sprache auf deinem Computer und beansprucht deine Grafikkarte."} Das Browserfenster darf geschlossen werden; der Studio-Server muss geöffnet bleiben.</dd>
    ${remote&&boot.key_available===false?`<dt>Zugang</dt><dd>${inlineKey("audio-key")}</dd>`:""}
    <dt>Aussprache</dt><dd>${pronunciation||'<span class="hint">Keine auffälligen Wörter im veröffentlichten Text.</span>'}</dd>
    <dt>Pausen</dt><dd class="hint">${Number(pauses.same_speaker_ms)} / ${Number(pauses.speaker_change_ms)} / ${Number(pauses.chapter_break_ms)} ms · gleiche Stimme, Stimmwechsel, Kapitel</dd>
    </dl>
    <label class="approval"><input id="audio-approval" type="checkbox" ${blocked?"disabled":""}><span>Ich habe dieses Skript gelesen und gebe diesen Stand mit dem angezeigten Audioanbieter und den Stimmen für Audio frei.${remote?" Ich möchte die API-Vertonung starten.":""}</span></label>
    <div class="actions"><button id="audio-start" data-action="audio" disabled>Audio erzeugen</button><button class="secondary" data-step="${PAGE.scripts}">Skript nochmals lesen</button></div>${blocked?`<p class="hint">${escape(blocked)}</p>`:""}`;
}
function refreshAudioPanel() {
  const panel=$("audio-panel");
  if(!panel||!project?.episodes?.length)return;
  const a=currentAudio();
  panel.innerHTML=renderApprovalCard(project.episodes[Math.min(episodeIndex,project.episodes.length-1)],a,a.provider==="openrouter_gemini_tts");
}
function renderAudio() {
  let html=heading(6);
  if(!project?.episodes?.length) return html+empty("Zuerst braucht es ein fertiges Skript.","Deine Freigabe gehört immer zu dem Text, den du tatsächlich gelesen hast.","Zu den Skripten",PAGE.scripts,pageIntros.audio);
  episodeIndex=Math.min(episodeIndex,project.episodes.length-1);
  const e=project.episodes[episodeIndex];
  const a=currentAudio(),remote=a.provider==="openrouter_gemini_tts";
  const hasAudio=project.episodes.some(row=>row.audio?.length);
  const capacity=remote?(boot.capabilities?.parallel_audio?`<p class="hint">${project.execution?.audio==="parallel"?"Parallel":"Sequenziell"} · ${project.audio_capacity?.active||0} von ${project.audio_capacity?.limit||1} Plätzen belegt. Weitere gelesene Folgen kannst du oben auswählen und einzeln freigeben.</p>`:'<p class="note">Parallele Vertonung benötigt einen Studio-Neustart nach Ende des laufenden Auftrags.</p>'):"";
  html+=`<div class="split"><div class="split-main">${episodePicker()}<section class="panel check-card" id="audio-panel">${renderApprovalCard(e,a,remote)}</section><div id="audio-jobs"></div>${e.audio?.length?renderListeningReview(e):""}${hasAudio?`<section class="panel recordings" id="recordings"><h2>Alle fertigen Folgen anhören</h2>${renderRecordings(recordingsProject())}</section>`:""}</div><aside class="split-rail">${capacity}${renderSpeechSettings(a)}${renderStyleNotes()}</aside></div>`;
  return html;
}
function overviewStatus(p) {
  if(p.unavailable)return "Projekt konnte nicht gelesen werden.";
  const active=[p.job,...(p.audio_jobs||[])].filter((j,i,all)=>j?.status==="running"&&all.findIndex(other=>other?.id===j.id)===i);
  if(active.length)return active.length>1?`${active.length} Vertonungen laufen`:(active[0].progress?.activity||actionNames[active[0].action]||"Auftrag läuft");
  const info=stopInfo(p.job);
  if(info)return `${STOP_KIND_LABELS[info.kind]||"Angehalten"} · ${info.title}`;
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
// The recordings list works on the overview's episode shape, built here from the project detail.
function recordingsProject(p=project) {
  return {id:p.id,topic:p.config?.topic||"Podcast",episode_count:Math.max((p.episodes||[]).length,(p.outline?.plan?.episodes||[]).length),
    episodes:(p.episodes||[]).map(e=>({episode_id:e.script.episode_id,title:e.script.title,audio:e.audio||[],audio_current:e.audio_current}))};
}
function renderRecordings(p) {
  return `<div class="podcast-download" id="project-download-${escape(p.id)}">${podcastDownload(p)}</div><div id="podcasts-${escape(p.id)}">${(p.episodes||[]).map((e,i)=>e.audio?.length?podcastCard(p,e,i):"").join("")}</div><p class="hint" id="project-audio-count-${escape(p.id)}">${(p.episodes||[]).filter(e=>e.audio?.length).length} fertige Folgen zum Anhören.</p>`;
}
// Polling adds finished episodes and updates labels without rebuilding a player that is in use.
function refreshRecordings(p=project?recordingsProject():null) {
  if(!p)return;
  const count=$("project-audio-count-"+p.id);
  if(count)count.textContent=`${(p.episodes||[]).filter(e=>e.audio?.length).length} fertige Folgen zum Anhören.`;
  const download=$("project-download-"+p.id),downloadContent=podcastDownload(p);
  redraw(download,downloadContent);
  (p.episodes||[]).forEach((e,i)=>{
    if(!e.audio?.length)return;
    const audioCard=$("podcast-"+p.id+"-"+e.episode_id);
    if(!audioCard){
      const following=p.episodes.slice(i+1).map(next=>$("podcast-"+p.id+"-"+next.episode_id)).find(Boolean);
      if(following)following.insertAdjacentHTML("beforebegin",podcastCard(p,e,i));
      else $("podcasts-"+p.id)?.insertAdjacentHTML?.("beforeend",podcastCard(p,e,i));
      return;
    }
    const label=$("recording-status-"+p.id+"-"+e.episode_id);
    if(label)label.textContent=e.audio_current?"":"Aufnahme eines früheren Skript- oder Stimmenstands.";
    if(audioCard.dataset?.audioVersion!==JSON.stringify(e.audio)&&
        ![...(audioCard.querySelectorAll?.("audio")||[])].some(audio=>!audio.paused))audioCard.outerHTML=podcastCard(p,e,i);
  });
}
// Overview: what waits for the user, what runs, then one pipeline row per project.
const runningOf = p => p.job?.status==="running"||(p.audio_jobs||[]).some(a=>a.status==="running");
function attentionOf(p) {
  const j=p.job;
  if(p.unavailable)return null;
  // A paused job waits for the user even while other episodes of the project are being voiced.
  const review=j?.status!=="running"?j?.progress?.plan_review:null;
  if(review?.awaiting&&!review.approved)return {text:"Der Rechercheplan wartet auf deine Freigabe.",button:"Plan freigeben",page:PAGE.research};
  if(j?.status==="review_ready")return {text:"Das Inhaltsverzeichnis ist bereit zur Durchsicht.",button:"Inhaltsverzeichnis prüfen",page:PAGE.outline};
  const info=stopInfo(j);
  if(info)return {text:info.message&&info.message!==info.text?`${info.title}: ${info.message}`:info.title,
    button:info.kind==="decision"?"Entscheiden":"Ansehen",page:jobPage(p)??PAGE.brief};
  const halted=stoppedAudio(p).filter(a=>a.id!==j?.id);
  if(halted.length){
    const title=a=>(p.episodes||[]).find(e=>e.episode_id===a.episode)?.title||a.episode;
    return {text:halted.length===1?`Vertonung von „${title(halted[0])}“ angehalten: ${stopInfo(halted[0]).title}`:
      `${halted.length} Folgen angehalten: ${halted.map(a=>`„${title(a)}“ (${stopInfo(a).title})`).join(", ")}`,button:"Vertonung ansehen",page:PAGE.audio};
  }
  if(!j||runningOf(p))return null;
  if(p.has_outline&&!p.script_count&&j.run?.kind!=="script"&&!["script","revise"].includes(j.action))return {text:"Das Inhaltsverzeichnis wartet auf deine Freigabe.",button:"Inhaltsverzeichnis prüfen",page:PAGE.outline};
  if(p.script_count&&!(p.episodes||[]).some(e=>e.audio?.length))return {text:`${p.script_count} ${p.script_count===1?"Skript ist":"Skripte sind"} fertig zum Lesen und zur Audio-Freigabe.`,button:"Skripte lesen",page:PAGE.scripts};
  return null;
}
function pipelineStates(p) {
  const j=p.job, run=j?.run, busy=runningOf(p);
  const blocked=["blocked","failed","interrupted","waiting_for_quota","pending"].includes(j?.status);
  const hasAudio=(p.episodes||[]).some(e=>e.audio?.length);
  const states=["done",p.has_research?"done":"pending",p.has_outline?"done":"pending",p.script_count?"done":"pending",p.script_count?(hasAudio?"done":"decision"):"pending",hasAudio?"done":(p.script_count?"decision":"pending")];
  if(p.has_outline&&!p.script_count&&run?.kind!=="script"&&!busy)states[PAGE.outline]="decision";
  if(j?.progress?.plan_review?.awaiting&&!j.progress.plan_review.approved&&!busy)states[PAGE.research]="decision";
  const page=jobPage(p);
  if(page!==null&&page!==undefined&&(busy||blocked))states[page]=busy?"running":stopInfo(j)?.kind==="decision"?"decision":"blocked";
  return states;
}
const pipeMarkup = states => states.map((s,i)=>`<span class="${s}" title="${steps[i]}"></span>`).join("");
function overviewCard(p) {
  const busy=runningOf(p), hasAudio=(p.episodes||[]).some(e=>e.audio?.length);
  return `<article class="pipeline" id="project-card-${escape(p.id)}" data-project-card="${escape(p.id)}"><div class="pipeline-main"><h2>${escape(p.topic)}</h2><div class="pipe" id="pipe-${escape(p.id)}" aria-label="Arbeitsschritte">${pipeMarkup(pipelineStates(p))}</div><p class="hint" id="project-state-${escape(p.id)}">${escape(overviewStatus(p))}</p></div>
    <div class="actions"><button data-open-project="${escape(p.id)}">Projekt öffnen</button>${hasAudio?`<button class="secondary small" data-open-project="${escape(p.id)}" data-open-step="${PAGE.audio}">Podcast anhören</button>`:""}<button class="quiet small danger-text" data-delete-project="${escape(p.id)}" ${p.unavailable||busy||!boot.capabilities?.project_overview?"disabled":""}>Projekt löschen</button></div></article>`;
}
function renderInbox() {
  const waiting=[], active=[];
  for(const p of overviewData.projects){ const a=attentionOf(p); if(a)waiting.push([p,a]); else if(runningOf(p))active.push(p); }
  return `${waiting.length?`<section class="inbox" aria-label="Wartet auf dich"><h2>Wartet auf dich</h2>${waiting.map(([p,a])=>`<div class="inbox-item"><span class="job-dot decision" aria-hidden="true"></span><div><strong>${escape(p.topic)}</strong><p>${escape(a.text)}</p></div><button class="small" data-open-project="${escape(p.id)}" data-open-step="${a.page}">${escape(a.button)}</button></div>`).join("")}</section>`:""}
    ${active.length?`<section class="inbox" aria-label="Läuft gerade"><h2>Läuft gerade</h2>${active.map(p=>`<div class="inbox-item running"><span class="job-dot running" aria-hidden="true"></span><div><strong>${escape(p.topic)}</strong><p>${escape(overviewStatus(p))}</p></div><button class="secondary small" data-open-project="${escape(p.id)}">Öffnen</button></div>`).join("")}</section>`:""}`;
}
function trashMarkup() {
  return overviewData.trash?.length?`<details class="panel"><summary>Papierkorb · ${overviewData.trash.length} Projekte</summary>${overviewData.trash.map(p=>`<div class="sample-row"><span>${escape(p.topic)}</span><button class="secondary small" data-restore-project="${escape(p.id)}">Wiederherstellen</button></div>`).join("")}</details>`:"";
}
function renderOverview() {
  return `<header class="page-head"><div class="page-title"><span class="eyebrow">ÜBERSICHT</span><h1>Deine Projekte &amp; Podcasts</h1></div><button data-new-project>＋ Neues Projekt</button></header>
    <div id="overview-inbox">${renderInbox()}</div>
    <section class="inbox" aria-label="Projekte"><h2>Projekte</h2><div id="overview-projects">${overviewData.projects.length?overviewData.projects.map(overviewCard).join(""):'<p id="overview-empty" class="hint">Dein erstes Projekt beginnt mit einem Gespräch.</p>'}</div></section><div id="overview-trash">${trashMarkup()}</div>`;
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
  const inbox=$("overview-inbox"),inboxContent=renderInbox();
  redraw(inbox,inboxContent);
  for(const card of container.querySelectorAll?.("[data-project-card]")||[])
    if(!overviewData.projects.some(p=>p.id===card.dataset.projectCard))card.remove();
  if(overviewData.projects.length&&$("overview-empty"))$("overview-empty").hidden=true;
  for(const p of overviewData.projects){
    const card=$("project-card-"+p.id);
    if(!card){container.insertAdjacentHTML?.("beforeend",overviewCard(p));continue;}
    $("project-state-"+p.id).textContent=overviewStatus(p);
    const pipe=$("pipe-"+p.id),pipeContent=pipeMarkup(pipelineStates(p));
    redraw(pipe,pipeContent);
    const button=card.querySelector?.("[data-delete-project]");
    if(button)button.disabled=p.unavailable||runningOf(p)||!boot.capabilities?.project_overview;
  }
  const trash=$("overview-trash"),content=trashMarkup();
  redraw(trash,content);
}
const AUDIO_STEPS={normalize:"Sprechabschnitte werden angeglichen",loudness:"Lautheit wird gemessen",encode:"MP3 mit Kapitelmarken wird erstellt"};
// What an audio job does right now: loading the voice model, speaking a chapter or assembling the episode.
function audioPhase(p) {
  if(!p)return "";
  if(p.status==="assembly"){
    const part=Number(p.parts)>1?` · Teil ${Number(p.part)} von ${Number(p.parts)}`:"";
    const counting=p.step==="normalize"&&Number(p.total_segments)>0;
    return `<p><span class="activity-dot" aria-hidden="true"></span>Audio wird zusammengefügt: ${escape(AUDIO_STEPS[p.step]||"Montage")}${counting?` · ${Number(p.completed_segments)} von ${Number(p.total_segments)}`:""}${part}</p>${counting?`<progress value="${Number(p.completed_segments)}" max="${Number(p.total_segments)}" aria-label="Angeglichene Sprechabschnitte"></progress>`:""}`;
  }
  if(p.total_segments===undefined)return "";
  const chapter=p.chapters?` · Kapitel ${Number(p.chapter)} von ${Number(p.chapters)}${p.chapter_title?`: ${escape(p.chapter_title)}`:""}`:"";
  const loading=p.tts_status==="loading_model"?'<p class="hint">Das Sprachmodell wird für dieses Kapitel geladen; beim ersten Mal kann das mehrere Minuten dauern.</p>':"";
  return `<p>${Number(p.completed_segments)} von ${Number(p.total_segments)} Sprechabschnitten fertig${chapter}</p><progress value="${Number(p.completed_segments)}" max="${Number(p.total_segments)}" aria-label="Fertige Sprechabschnitte"></progress>${loading}`;
}
// The worker's heartbeat: silence is flagged, with the long steps that legitimately look like it.
function heartbeatNote(j) {
  const age=Number(j?.heartbeat_age_seconds);
  if(j?.status!=="running"||!Number.isSafeInteger(age))return "";
  const loading=j.progress?.tts_status==="loading_model";
  if(age<=(loading?900:300))return "";
  return `<p class="note" role="status">Seit ${Math.floor(age/60)} Min. keine neue Meldung vom Arbeitsprozess. ${loading?"Das Laden des Sprachmodells dauert ungewöhnlich lange.":"Lange Einzelschritte können so aussehen."} Bleibt es dabei: „Anhalten“ und danach fortsetzen; fertige Schritte bleiben gespeichert.</p>`;
}
// One card per episode job; the local Qwen job, which runs as the project's main job, gets the same card.
function renderAudioJob(j,{main=false}={}) {
  const e=project.episodes?.find(row=>row.script.episode_id===j.episode), active=j.status==="running", info=stopInfo(j);
  const state=active?"Wird vertont":j.status==="completed"?"Fertig zum Anhören":info?.title||"Angehalten";
  const target=`data-run-id="${escape(j.run?.run_id||"")}"${main?"":` data-episode="${escape(j.episode)}"`}`;
  // Gemini episodes resume beside each other within the free slots; the local job waits for any other job.
  const blocked=main?(running()?"Ein anderer Auftrag läuft gerade.":""):audioBlockReason(j.episode);
  return `<section class="audio-job${info?" stopped":""}"><div class="job-top"><strong>${escape(e?.script.title||j.episode||"Vertonung")} · ${escape(state)}</strong>
    ${active?`<button class="danger small" data-action="stop"${main?"":` data-job-id="${escape(j.id)}"`}>Diese Folge anhalten</button>`:""}</div>
    ${active?audioPhase(j.progress)+heartbeatNote(j):info?stopBody(j,info,{target,blocked}):""}
    ${j.status==="completed"&&step!==PAGE.audio?`<button class="secondary small" data-step="${PAGE.audio}">Podcast anhören</button>`:""}</section>`;
}
function renderAudioJobs() {
  const cards=(project?.audio_jobs||[]).map(j=>renderAudioJob(j));
  const main=project?.main_job??project?.job;
  if(main&&(main.run?.kind==="episode_audio"||main.action==="audio")&&main.status!=="completed"&&!(project.audio_jobs||[]).some(a=>a.id===main.id))
    cards.unshift(renderAudioJob(main,{main:true}));
  return cards.join("");
}

function renderScriptProgress(p, active) {
  if(p?.phase!=="script")return "";
  active=active&&!progressStale(p);
  const elapsed=p.activity_started_at?Math.max(0,Math.floor((Date.now()-Date.parse(p.activity_started_at))/60000)):null;
  // In parallel mode several episodes of one stage are in work; each is named with its own time.
  const running=e=>e.stage_status==="running"||(p.active_episodes||[]).includes(e.episode_id);
  const inWork=active?(p.episodes||[]).filter(running):[], parallel=inWork.length>1;
  const heading=parallel?`${inWork.length} Folgen in Arbeit · ${escape(stageNames[p.stage]||p.stage)}`:
    p.current_episode?`Folge ${Number(p.episode_number)} von ${Number(p.total_segments)} · ${escape(p.episode_title)}`:escape(stageNames[p.stage]||"Fortschritt");
  const marker=e=>e.completed?"✓":active&&(running(e)||(!inWork.length&&e.episode_id===p.current_episode))?"●":e.stage_status==="interrupted"?"!":"○";
  const winding=active&&p.stopping?.episodes?.length?`<p class="note" role="status"><strong>${p.stopping.episodes.map(escape).join(", ")}: angehalten.</strong> Die übrigen laufenden Folgen werden in dieser Stufe noch fertig bearbeitet; danach hält der Auftrag an und diese Seite nennt den Grund.</p>`:"";
  return `<section class="script-progress"><p class="current-episode"><strong>${heading}</strong></p>${winding}<p>${active?'<span class="activity-dot" aria-hidden="true"></span>':"Zuletzt: "}${parallel?"Zuletzt gestartet: ":""}${escape(p.activity)}${active&&elapsed!==null?` · seit ${elapsed<1?"weniger als einer Minute":`${elapsed} Min.`}`:""}</p><p class="hint">Die Anzeige aktualisiert sich automatisch. Ein Modellaufruf kann mehrere Minuten dauern.</p>${p.total_segments?`<progress value="${Number(p.completed_segments)}" max="${Number(p.total_segments)}" aria-label="Fertige Folgen in dieser Stufe"></progress><p>${Number(p.completed_segments)} von ${Number(p.total_segments)} Folgen: ${escape(stageNames[p.stage]||p.stage)} abgeschlossen</p>`:""}${active&&p.stage==="review"&&p.total_segments&&Number(p.completed_segments)===Number(p.total_segments)?'<p class="hint">Alle Folgen sind einzeln geprüft; jetzt folgen die Prüfung der gesamten Serie und letzte Korrekturen.</p>':""}<ol class="episode-progress">${(p.episodes||[]).map(e=>`<li>${marker(e)} ${escape(e.title)}${parallel&&e.stage_started_at&&running(e)?` <span class="hint">· seit ${elapsedText(e.stage_started_at)}</span>`:""}</li>`).join("")}</ol>${(p.episodes||[]).filter(e=>e.teaching_preview).map(e=>`<details data-progress-episode="${escape(e.episode_id)}"><summary>Lehrkonzept lesen: ${escape(e.title)}</summary><pre class="document" data-progress-preview="${escape(e.episode_id)}">${escape(e.teaching_preview)}</pre></details>`).join("")}</section>`;
}
function progressAge(timestamp) {
  const seconds=Math.max(0,Math.floor((Date.now()-Date.parse(timestamp))/1000));
  return seconds<60?`${seconds} Sek.`:`${Math.floor(seconds/60)} Min.`;
}
// The server stamps every progress read with the current time, so staleness means: no answer from the
// Studio for a while. How long the run itself has not changed is shown separately (changed_at).
function progressStale() {
  return connectionLost||Date.now()-lastSyncAt>30000;
}
function renderProgressTiming(p, active) {
  if(!active||!["script","research"].includes(p?.phase))return "";
  const stale=progressStale(p);
  const open=p.open_calls||[];
  const call=p.model_call_started_at?`<p class="hint">${stale?"Zuletzt gemeldeter Modellaufruf gestartet vor":open.length>1?`${open.length} Modellaufrufe laufen gleichzeitig, der älteste seit`:"Aktueller Modellaufruf: seit"} ${progressAge(p.model_call_started_at)}</p>`:"";
  const result=p.last_result_at?`<p class="hint">Letztes gespeichertes Modellergebnis: vor ${progressAge(p.last_result_at)}</p>`:"";
  const changed=p.changed_at?`<p class="hint">Letzte Änderung im Lauf: vor ${progressAge(p.changed_at)}</p>`:"";
  const freshness=stale?`<p class="note" role="status">Keine Antwort vom Studio seit ${progressAge(new Date(lastSyncAt).toISOString())}. Angezeigt ist der Stand von ${escape(new Date(lastSyncAt).toLocaleTimeString("de-DE"))}; ob der Auftrag weiterläuft, zeigt sich nach der nächsten Verbindung. Die Verbindung wird automatisch erneut geprüft.</p>`:"";
  return call+result+changed+freshness;
}
function renderWorkInsight(job) {
  const info=job?.progress?.work_insight;
  if(!info)return "";
  const signal=info.signals||{},active=job.status==="running"&&signal.state==="running";
  const material=info.material||{};
  const visibleRows=(job.progress.model_trace?.lines||[]).filter(row=>["text","reasoning"].includes(row.kind)&&row.call===signal.call&&signal.call);
  const visibleAt=signal.last_visible_at||visibleRows.map(row=>row.at).filter(Boolean).sort().at(-1);
  // Also works with an already-running worker whose last_content_at counts
  // every delta, including the JSON fields hidden from the readable panel.
  const receivedAt=signal.last_received_at||signal.last_content_at;
  const since=visibleAt||(signal.call?signal.started_at:signal.last_content_at||signal.started_at);
  const quiet=active&&since?Math.max(0,(Date.now()-Date.parse(since))/1000):0;
  const receiving=active&&receivedAt&&Date.now()-Date.parse(receivedAt)<15000;
  const hiddenOutput=receiving&&quiet>=60&&Date.parse(receivedAt)>Date.parse(since);
  const warning=quiet>=180;
  const categories={connection:"Verbindungsprobleme",retry:"Verbindungsversuche",timeout:"Zeitlimits",rate_limit:"Anfragelimits",server_error:"Anbieterfehler",authentication:"Anmeldeprobleme",quota:"Nutzungslimits"};
  return `${info.question?`<p class="trace-focus">${escape(info.question)}</p>`:""}
    <p class="work-label">Auftrag an das Modell</p><p>${escape(info.assignment)}</p>
    ${info.last_step?`<p class="work-label">Im letzten Ergebnis festgehalten</p><p>${escape(info.last_step)}</p>`:""}
    ${info.feedback?.length?`<p class="work-label">Zuletzt bemängelt</p><ul>${info.feedback.map(t=>`<li>${escape(t)}</li>`).join("")}</ul>`:""}
    <details class="work-material"><summary>Material und Prüfpunkte für diesen Schritt</summary>
      <p>${Number(material.section_count||0)} Textstellen aus ${Number(material.source_count||0)} Quellen bereitgestellt${info.candidate_count?`; zusätzlich ${Number(info.candidate_count)} Suchtreffer, die noch keine gelesenen Belege sind`:""}.</p>
      ${material.unresolved_sections?'<p class="hint">Ein Teil der Quellenangaben ist gerade nicht verfügbar.</p>':""}
      ${material.sources?.length?`<ul>${material.sources.map(s=>`<li>${escape(s.title)} · ${Number(s.sections)} Textstellen${s.pages?.length?` · Seiten ${s.pages.map(Number).join(", ")}`:""}</li>`).join("")}</ul>`:""}
      ${material.source_count>6?`<p>Weitere ${Number(material.source_count)-6} Quellen.</p>`:""}
      ${info.queries?.length?`<p>Suchbegriffe: ${info.queries.map(escape).join("; ")}</p>`:""}
      ${info.criteria?.length?`<p>Diese Punkte soll die Antwort klären:</p><ul>${info.criteria.map(t=>`<li>${escape(t)}</li>`).join("")}</ul>`:""}
    </details>
    <div class="work-signals${warning?" quiet":""}">
      ${active&&since?`<p><strong>${visibleAt||(!signal.call&&signal.last_content_at)?`Seit ${progressAge(since)} kein neuer lesbarer Modelltext.`:`Seit ${progressAge(since)} noch keine inhaltliche Zwischenmeldung zum aktuellen Aufruf.`}</strong></p>`:""}
      ${hiddenOutput?'<p class="note"><strong>Ausgabe kommt weiter an, aber ohne neuen lesbaren Text.</strong> Unser Anzeigefilter blendet Teile der strukturierten Antwort aus. Das kann auch Formatdaten oder eine Ausgabe-Schleife verbergen. Empfang allein belegt keinen Recherchefortschritt; die Antwort ist noch nicht abgeschlossen.</p>':""}
      ${active&&receivedAt?`<p class="hint">Letztes empfangenes Fragment: vor ${progressAge(receivedAt)}${signal.stream_deltas!=null?` · ${Number(signal.stream_deltas)} Fragmente empfangen`:""}.</p>`:""}
      ${active?'<p class="hint">Der Auftrag zeigt, was bearbeitet werden soll. Ohne neue Rückmeldung ist nicht erkennbar, ob das Modell weiterkommt oder festhängt.</p>':`<p>${escape(job.status!=="running"?"Gespeicherter Stand; der Auftrag läuft derzeit nicht.":signal.state==="completed"?"Die Modellantwort ist gespeichert; die Verarbeitung folgt.":"Der letzte Modellaufruf meldet einen Fehler.")}</p>`}
      ${signal.last_event_at?`<p class="hint">Letzte Meldung der Modellanbindung: vor ${progressAge(signal.last_event_at)}${!signal.last_content_at?" · bisher nur Status-/Technikmeldungen.":""}</p>`:""}
      ${signal.last_result_at?`<p class="hint">Letzte gespeicherte Modellantwort im Auftrag: vor ${progressAge(signal.last_result_at)}</p>`:""}
      ${active&&signal.timeout_seconds?`<p class="hint">Automatisches Zeitlimit für diesen Aufruf: ${Math.ceil(Number(signal.timeout_seconds)/60)} Min.</p>`:""}
      ${Object.entries(signal.categories||{}).filter(([key])=>categories[key]).map(([key,n])=>`<p>${categories[key]} bei diesem Aufruf: ${Number(n)}.</p>`).join("")}
      ${info.warning?`<p class="note">${escape(info.warning)}</p>`:""}
    </div><p class="hint">${info.basis==="request"?"Aus dem tatsächlich gesendeten Arbeitsauftrag":"Aus dem gespeicherten Recherchestand rekonstruiert"}; keine zusätzliche Modellabfrage.</p>`;
}
function activeTasks(ledger) {
  // Several tasks run side by side in parallel mode; older ledgers name one active task.
  if(Array.isArray(ledger?.active_tasks))return ledger.active_tasks.filter(id=>typeof id==="string");
  return typeof ledger?.active_task==="string"?[ledger.active_task]:[];
}
function renderActiveTasks(ledger, active=true) {
  // A stopped run's ledger still names the tasks it was working on; they are not in work now.
  const ids=active?activeTasks(ledger):[];
  if(ids.length<2)return "";
  const names=new Map((ledger.questions||[]).map(row=>[row.id,row.question]));
  return `<p class="hint">${ids.length} Teilfragen in Arbeit: ${ids.map(id=>escape(names.get(id)||id)).join(" · ")}</p>`;
}
function renderModelTrace(job) {
  if(!["research","script"].includes(job?.progress?.phase))return "";
  const trace=job.progress.model_trace,rows=(trace?.lines||[]).slice(-20);
  const kinds={reasoning:"Öffentliche Reasoning-Zusammenfassung",text:"Live-Text",status:"Arbeitsschritt",diagnostic:"Technischer Hinweis"};
  const active=job.status==="running";
  const ledger=job.progress.research_questions;
  const current=(ledger?.questions||[]).find(row=>row.id===activeTasks(ledger)[0]);
  const started=job.progress.model_call_started_at;
  const currentContent=rows.some(row=>["text","reasoning"].includes(row.kind)&&(!started||Date.parse(row.at)>=Date.parse(started)));
  const insight=renderWorkInsight(job), labels=job.progress.call_labels||{}, open=job.progress.open_calls||[];
  // The live output is the point of the drawer: it stands first and open; the assignment folds underneath.
  return `<section class="model-trace" aria-label="Live-Ausgabe des Modells"><strong>Live-Ausgabe · letzte 20 Meldungen</strong>
    <p class="hint">${active?"Gerade in Arbeit":"Letzte Arbeitsschritte"}${trace?.updated_at?` · Letzte Meldung: vor ${progressAge(trace.updated_at)}`:""}</p>
    ${active&&started&&!currentContent?'<p class="hint">Der aktuelle Modellaufruf läuft. Inhaltliche Zwischenmeldungen liegen dafür noch nicht vor.</p>':""}
    ${rows.length?`<ol class="trace-lines">${rows.map(row=>`<li><small>${escape(row.at?new Date(row.at).toLocaleTimeString("de-DE"):"")} · ${escape(kinds[row.kind]||"Meldung")}${labels[row.call]?` · ${escape(shortText(labels[row.call],70))}`:""}</small><p>${escape(row.text)}</p></li>`).join("")}</ol>`:`<p class="hint">Noch keine Meldungen verfügbar. Manche Anbieter senden Text erst am Ende des Aufrufs.</p>`}
    <p class="hint">Neue Textfragmente und öffentliche Reasoning-Zusammenfassungen erscheinen während des Aufrufs. Aussagen des Modells sind noch ungeprüft.</p>
    ${insight?`<details class="model-events"><summary>${active?(open.length>1?`Zuletzt gestarteter Rechercheauftrag · einer von ${open.length}`:"Aktueller Rechercheauftrag"):"Letzter Rechercheauftrag"}</summary>${insight}</details>`:(current&&active?`<p class="trace-focus">${escape(current.question)}</p><p>${escape(current.activity)}</p>`:"")}${renderActiveTasks(ledger,active)}</section>`;
}
function researchRound(ledger) {
  // The round counts from the first whole-dossier audit; reopened questions belong to a later round.
  const round=Number(ledger?.audit_round||0), reopened=Number(ledger?.reopened||0);
  if(!(round>0||reopened>0||["synthesis","audit"].includes(ledger?.phase)))return "";
  return `<p><strong>Prüfrunde ${round+1}${reopened>0?` · ${reopened} ${reopened===1?"Teilfrage":"Teilfragen"} wieder geöffnet`:""}</strong></p>`;
}

// Every stop names what happened, whether "Fortsetzen" can help and which control leads on:
//   retry    "Fortsetzen" repeats the step; finished work stays.
//   wait     a provider limit resets; then "Fortsetzen" or the scheduler's automatic resume.
//   fix      something outside the Studio needs fixing first (login, key, FFmpeg), then "Fortsetzen".
//   decision a control on the step's page decides (plan, blocked questions, a higher limit).
//   dead     this run cannot continue; the card names the way on and what stays readable.
const STOP_KIND_LABELS={retry:"Angehalten",wait:"Wartet auf Kontingent",fix:"Braucht Einrichtung",decision:"Deine Entscheidung",dead:"Neustart nötig"};
const RETRY_CALL="„Fortsetzen“ wiederholt den Aufruf. Hält der Schritt erneut an, unter „Auftrag & Stimmen“ die Verbindungen prüfen.";
const EXHAUSTED="Das Modell hat für diesen Prüfschritt alle automatischen Korrekturversuche verbraucht; die abgewiesenen Antworten liegen im Laufordner. „Fortsetzen“ würde an derselben Stelle wieder anhalten.";
const STORED_STATE="Ein gespeicherter Zwischenstand oder eine Freigabe dieses Laufs passt nicht mehr zu seinen Eingaben, zum Beispiel nach einer Studio-Aktualisierung. „Fortsetzen“ würde an derselben Stelle wieder anhalten. Fertige Ergebnisse bleiben lesbar; weiter geht es mit einem neuen Lauf.";
const FFMPEG_TEXT="FFmpeg einrichten: unter Windows einmal scripts\\setup-ffmpeg.ps1 ausführen, unter macOS und Linux sh scripts/setup.sh. Danach das Studio neu starten und „Fortsetzen“; die Sprachaufnahmen bleiben gespeichert.";
const KEY_TEXT="Den OpenRouter-Key hier hinterlegen. Er bleibt nur im Speicher dieses Studio-Servers und muss nach jedem Neustart erneut eingegeben werden.";
const reviewAgain=name=>`${name} meldet nach den automatischen Korrekturen weiter Einwände; die offenen Punkte stehen auf dieser Seite. „Fortsetzen“ startet eine neue Prüfrunde, deren Urteil anders ausfallen kann. Hält sie erneut an, hilft ein neues Inhaltsverzeichnis.`;
const searchCapped=c=>/Rechercherunden|Suchrunden/.test(c.job.message||"")||
  (Number(c.progress.search_round_limit)>0&&Number(c.progress.search_rounds)>=Number(c.progress.search_round_limit)&&
    !(Number(c.progress.model_call_limit)>0&&Number(c.progress.model_calls)>=Number(c.progress.model_call_limit)));
function loginHint(c) {
  const text=`${c.job.message||""} ${c.job.provider_choice?.provider||""}`;
  if(/codex/i.test(text)&&!/claude/i.test(text))return "Im Terminal „codex login“ ausführen.";
  if(/claude/i.test(text)&&!/codex/i.test(text))return "Im Terminal „claude auth login“ ausführen.";
  return "Im Terminal „codex login“ oder „claude auth login“ ausführen, je nach Abo.";
}
const STOP_RULES={
  interrupted:{kind:"retry",title:"Angehalten",text:"Der Auftrag wurde angehalten. „Fortsetzen“ macht an der unterbrochenen Stelle weiter; alles Fertige bleibt gespeichert."},
  worker_start:{kind:"retry",title:"Auftrag startete nicht",text:"Der Arbeitsprozess konnte nicht starten. Das Studio beenden, neu öffnen und den Schritt erneut starten."},
  processing_failed:{kind:"retry",title:"Unerwarteter Programmfehler",text:"Ein Programmfehler hat den Auftrag beendet. „Fortsetzen“ versucht den Schritt erneut; alles Fertige bleibt gespeichert. Die technischen Details nennen die Ursache."},
  invalid_local_data:{kind:"retry",title:"Lokale Verarbeitung fehlgeschlagen",text:"Beim Verarbeiten gespeicherter Dateien ist ein Fehler aufgetreten. „Fortsetzen“ versucht es erneut; die technischen Details nennen die Ursache."},
  project_busy:{kind:"retry",title:"Projekt belegt",text:"Ein anderer Auftrag, etwa ein Lauf in der Kommandozeile, hält dieses Projekt. Wenn er fertig ist, „Fortsetzen“."},
  timeout:{kind:"retry",title:"Zeitlimit eines Modellaufrufs",text:"Ein einzelner Modellaufruf hat sein Zeitlimit überschritten; er wurde nicht angerechnet. „Fortsetzen“ wiederholt ihn. Hält er erneut an, ist der Schritt zu groß für einen Aufruf."},
  stall:{kind:"retry",title:"Modellaufruf ohne Ausgabe",text:"Ein Modellaufruf hat lange keine Ausgabe geliefert und wurde beendet; er wurde nicht angerechnet. „Fortsetzen“ wiederholt ihn."},
  claude_failed:{kind:"retry",title:"Claude-Aufruf fehlgeschlagen",text:RETRY_CALL,actions:["check"]},
  codex_failed:{kind:"retry",title:"Codex-Aufruf fehlgeschlagen",text:RETRY_CALL,actions:["check"]},
  openrouter_connection:{kind:"retry",title:"OpenRouter nicht erreichbar",text:RETRY_CALL},
  openrouter_request:{kind:"retry",title:"OpenRouter-Anfrage abgewiesen",text:RETRY_CALL},
  openrouter_unavailable:{kind:"retry",title:"OpenRouter vorübergehend nicht verfügbar",text:"Etwas später „Fortsetzen“; der Aufruf wird dann wiederholt."},
  openrouter_speech_request:{kind:"retry",title:"Gemini-Anfrage fehlgeschlagen",text:"„Fortsetzen“ erzeugt die fehlenden Sprechabschnitte; fertige bleiben gespeichert."},
  invalid_audio:{kind:"retry",title:"Unbrauchbare Audiodaten",text:"Die Sprachausgabe lieferte unbrauchbares Audio. „Fortsetzen“ erzeugt die betroffenen Abschnitte neu."},
  audio_processing_failed:{kind:"retry",title:"Audio-Montage fehlgeschlagen",text:"FFmpeg konnte das Audio nicht verarbeiten. „Fortsetzen“ versucht die Montage erneut; die Sprachaufnahmen bleiben gespeichert."},
  loudness_failed:{kind:"retry",title:"Lautheitsmessung fehlgeschlagen",text:"„Fortsetzen“ versucht die Montage erneut; die Sprachaufnahmen bleiben gespeichert."},
  tts_worker_failed:{kind:"retry",title:"Lokale Sprachausgabe fehlgeschlagen",text:"Qwen konnte nicht sprechen. Bei knappem Grafikspeicher andere Programme schließen, dann „Fortsetzen“; fertige Abschnitte bleiben gespeichert."},
  tts_environment:{kind:"fix",title:"Lokale Sprachausgabe nicht eingerichtet",text:"Die Qwen-Umgebung ist nicht einsatzbereit. Die Einrichtung laut docs/qwen-windows.md prüfen oder unter „Auftrag & Stimmen“ Gemini wählen; danach „Fortsetzen“.",actions:["check"]},
  search_not_observed:{kind:"retry",title:"Websuche nicht nachweisbar",text:"Ein Rechercheaufruf hat keine beobachtbare Websuche ausgeführt und wurde abgewiesen. „Fortsetzen“ wiederholt ihn."},
  invalid_model_output:{kind:"retry",title:"Unlesbare Modellantwort",text:"Die Antwort des Modells war unvollständig oder nicht lesbar. „Fortsetzen“ fragt erneut an."},
  script_review_failed:{kind:"retry",title:"Qualitätsprüfung hat Einwände",text:reviewAgain("Die Qualitätsprüfung"),actions:["new_outline"]},
  series_review_failed:{kind:"retry",title:"Serienprüfung hat Einwände",text:reviewAgain("Die Prüfung der gesamten Serie"),actions:["new_outline"]},
  teaching_review_failed:{kind:"retry",title:"Lehrprüfung nicht bestanden",text:reviewAgain("Die Lehrprüfung"),actions:["new_outline"]},
  dialogue_polish_failed:{kind:"retry",title:"Dialog-Polishing braucht Korrektur",text:reviewAgain("Die Prüfung des Dialog-Polishings"),actions:["new_outline"]},
  subscriptions_exhausted:{kind:"wait",title:"Beide Abos ausgeschöpft",text:"Codex und Claude haben gerade kein Kontingent. Nach dem Reset geht es mit „Fortsetzen“ weiter."},
  claude_quota_exhausted:{kind:"wait",title:"Claude-Kontingent erschöpft",text:"Nach dem Reset geht es mit „Fortsetzen“ weiter; bei automatischer Abo-Wahl übernimmt Codex, sobald es Kontingent hat."},
  quota_exhausted:{kind:"wait",title:"Codex-Kontingent erschöpft",text:"Nach dem Reset geht es mit „Fortsetzen“ weiter."},
  openrouter_rate_limit:{kind:"wait",title:"OpenRouter-Anfragelimit",text:"Kurz warten, dann „Fortsetzen“."},
  waiting_for_quota:{kind:"wait",title:"Anbieterlimit erreicht",text:"Nach dem Reset geht es mit „Fortsetzen“ weiter."},
  openrouter_credits:{kind:"fix",title:"OpenRouter-Guthaben erschöpft",text:"Guthaben oder Key-Limit bei OpenRouter erhöhen, dann „Fortsetzen“. Warten allein hilft hier nicht, deshalb setzt das Studio nicht automatisch fort."},
  authentication_required:{kind:"fix",title:"Anmeldung abgelaufen",text:c=>`${loginHint(c)} Danach „Fortsetzen“.`,actions:["check"]},
  subscription_required:{kind:"fix",title:"Kein Abo nutzbar",text:"Weder Codex noch Claude ist angemeldet und nutzbar. Im Terminal „codex login“ oder „claude auth login“ ausführen, dann „Fortsetzen“.",actions:["check"]},
  codex_missing:{kind:"fix",title:"Codex nicht gefunden",text:"Codex CLI oder die OpenAI-Erweiterung für VS Code installieren und das Studio neu starten, dann „Fortsetzen“.",actions:["check"]},
  claude_missing:{kind:"fix",title:"Claude Code nicht gefunden",text:"Claude Code installieren und anmelden, das Studio neu starten, dann „Fortsetzen“.",actions:["check"]},
  claude_version:{kind:"fix",title:"Claude Code zu alt",text:"Claude Code aktualisieren, das Studio neu starten, dann „Fortsetzen“.",actions:["check"]},
  openrouter_key_required:{kind:"fix",title:"OpenRouter-Key fehlt",text:KEY_TEXT,actions:["key"]},
  invalid_key:{kind:"fix",title:"OpenRouter-Key fehlt",text:KEY_TEXT,actions:["key"]},
  openrouter_authentication:{kind:"fix",title:"OpenRouter-Key abgewiesen",text:"OpenRouter hat den Key nicht angenommen. Einen gültigen Key hinterlegen; er bleibt nur im Speicher dieses Studio-Servers.",actions:["key"]},
  ffmpeg_missing:{kind:"fix",title:"FFmpeg fehlt",text:FFMPEG_TEXT},
  ffprobe_missing:{kind:"fix",title:"FFprobe fehlt",text:FFMPEG_TEXT},
  research_plan_review:{kind:"decision",title:"Rechercheplan wartet auf Freigabe",text:"Prüfe die Hochrechnung auf der Seite Recherche und gib den Plan frei. Bis dahin wird kein weiterer Modellaufruf verbraucht.",card:true},
  research_questions_blocked:{kind:"decision",title:"Teilfragen warten auf deine Entscheidung",text:"Für jede blockierte Teilfrage: noch einmal versuchen oder als Lücke akzeptieren, dann „Fortsetzen“.",card:true},
  review_ready:{kind:"decision",title:"Inhaltsverzeichnis bereit zur Durchsicht",text:"Prüfe Folgen und Kapitel und gib den Plan frei oder lass ihn überarbeiten.",card:true},
  research_budget_insufficient:{kind:"decision",title:"Aufruflimit reicht nicht",text:"Das genehmigte Aufruflimit reicht voraussichtlich nicht bis zum Abschluss. Antworten und Umfang bleiben erhalten; ein höheres Limit genehmigst du hier.",actions:["approve_calls"]},
  research_budget_exhausted:{kind:"decision",title:c=>searchCapped(c)?"Suchrundenlimit erreicht":"Aufruflimit erreicht",
    text:c=>searchCapped(c)?"Das genehmigte Limit für Websuchen ist verbraucht. Der bisherige Stand bleibt gespeichert; mehr Suchrunden genehmigst du hier.":"Das genehmigte Aufruflimit ist verbraucht. Der bisherige Stand bleibt gespeichert; ein höheres Limit genehmigst du hier.",
    actions:c=>[searchCapped(c)?"approve_search":"approve_calls"]},
  script_budget_insufficient:{kind:"decision",title:"Aufruflimit reicht nicht",text:"Für die restlichen Schritte reicht das genehmigte Aufruflimit nicht. Fertige Arbeit bleibt gespeichert; ein höheres Limit genehmigst du hier.",actions:["approve_calls"]},
  plan_exceeds_allowance:{kind:"decision",title:"Rechercheplan passt nicht ins Aufruflimit",text:"Der Rechercheplan braucht mehr Modellaufrufe, als genehmigt sind. Ein höheres Limit genehmigen, dann plant die Recherche weiter.",actions:["approve_calls"]},
  chat_budget:{kind:"decision",title:"Gesprächslimit erreicht",text:"Das Gespräch mit der Redaktion hat sein Aufruflimit für dieses Projekt verbraucht. Ein höheres Limit genehmigen, dann erneut senden."},
  review_disagreement:{kind:"dead",title:"Unbelegter Prüfeinwand",text:"Die Gesamtprüfung erhebt einen Einwand, den sie selbst nicht mit gelesenen Belegen verankern kann. Dafür startet keine automatische Nachrecherche, und „Fortsetzen“ würde dieselbe Prüfung erneut vorlegen. Die geprüften Teilantworten bleiben lesbar; weiter geht es mit einer neuen Recherche.",actions:["restart"]},
  invalid_research_checkpoint:{kind:"dead",title:"Gespeicherter Zwischenstand passt nicht mehr",text:STORED_STATE,actions:["restart"]},
  inputs_changed:{kind:"dead",title:"Eingaben seit dem Anhalten geändert",text:"Seit dem Anhalten haben sich Angaben geändert, an die dieser Lauf gebunden ist, etwa Auftrag, Stimmen, Hostnamen, Notizen, Sprechformen oder eine Studio-Aktualisierung. Er lässt sich nicht fortsetzen; fertige Ergebnisse bleiben lesbar.",actions:["restart"]},
  script_edited:{kind:"dead",title:"Skript seit der Freigabe geändert",text:"Das Skript wurde seit deiner Freigabe geändert. Die aktuelle Fassung lesen und erneut für Audio freigeben.",actions:["restart"]},
  invalid_plan:{kind:"dead",title:"Inhaltsverzeichnis bleibt widersprüchlich",text:"Die automatische Korrektur hat den Widerspruch nach drei Runden nicht aufgelöst; „Fortsetzen“ würde dieselbe Prüfung wiederholen. Mit einem Änderungswunsch entsteht ein neuer Entwurf mit neuen Korrekturrunden.",actions:["replan_feedback","new_outline"]},
  research_required:{kind:"dead",title:"Recherche fehlt oder passt nicht",text:"Für das Inhaltsverzeichnis fehlt ein geprüftes, aktuelles Dossier. Zuerst die Recherche abschließen oder neu beginnen.",actions:["open_research","new_research"]},
  teaching_design_failed:{kind:"dead",title:"Lehrkonzept bleibt unvollständig",text:"Die automatische Überarbeitung hat nicht alle Kritikpunkte gelöst; „Fortsetzen“ würde dieselben Punkte wieder vorlegen. Die offenen Punkte stehen auf dieser Seite. Weiter geht es mit einem neuen Inhaltsverzeichnis: die Recherche bleibt, Lehrkonzepte und Skripte dieses Laufs entstehen neu.",actions:["new_outline"]},
  teaching_research_required:{kind:"dead",title:"Erklärgrundlagen fehlen",text:"Für das Lehrkonzept fehlen belegte Grundlagen, und die automatische Nachrecherche konnte sie nicht schließen. Die offenen Fragen stehen auf dieser Seite. Weiter geht es mit einer neuen Recherche, die diese Fragen abdeckt, oder mit einem neuen Inhaltsverzeichnis, das ohne sie auskommt.",actions:["new_research","new_outline"]},
  research_gap_unread:{kind:"dead",title:"Ungelesene Belege zu gemeldeten Lücken",text:"Die Prüfung meldet Lücken, zu denen das Quellenmaterial noch ungelesene Stellen enthält. Das klärt nur eine neue Recherche.",actions:["new_research"]},
  prompt_too_large:{kind:"dead",title:"Auftrag zu groß für das Modell",text:"Der Auftrag passt nicht in das Kontextfenster des gewählten Modells, und der Lauf bleibt an sein Modell gebunden. Unter „Auftrag & Stimmen“ ein Modell mit größerem Fenster wählen, etwa Automatisch oder Codex, und den Schritt neu starten.",actions:["open_brief","restart"]},
  claude_output_limit:{kind:"dead",title:"Antwort zu lang für einen Aufruf",text:"Die Antwort war länger, als ein Claude-Aufruf liefern kann, und „Fortsetzen“ würde sie unverändert wiederholen. Unter „Auftrag & Stimmen“ ein anderes Modell wählen und den Schritt neu starten.",actions:["open_brief","restart"]},
  claude_budget_cap:{kind:"dead",title:"Kostengrenze eines Aufrufs erreicht",text:"Ein einzelner Claude-Aufruf hat seine Kostengrenze erreicht. Unter „Auftrag & Stimmen“ ein anderes Modell wählen und den Schritt neu starten.",actions:["open_brief","restart"]},
  openrouter_truncated:{kind:"dead",title:"Antwort am Tokenlimit abgeschnitten",text:"OpenRouter hat die Antwort abgeschnitten. Unter „Auftrag & Stimmen“ ein Modell mit höherem Ausgabelimit wählen und den Schritt neu starten.",actions:["open_brief","restart"]},
  no_readable_sources:{kind:"dead",title:"Keine Quelle lesbar",text:"Keine gefundene Quelle ließ sich als Text einlesen; der Abrufbericht auf dieser Seite nennt die Gründe. Unter „Auftrag & Stimmen“ Quellenlinks oder eigene Dateien ergänzen und neu recherchieren.",actions:["open_brief","restart"]},
  duration_exceeded:{kind:"dead",title:"Folge zu lang",text:"Die Folge überschreitet die maximale Länge. Auf der Seite „Skripte lesen“ eine kürzere Fassung anfordern und diese erneut freigeben.",actions:["open_scripts"]},
};
for(const code of ["invalid_source_snapshot","invalid_plan_approval","invalid_budget_approval","invalid_gap_approval","invalid_retry_request"])STOP_RULES[code]=STOP_RULES.invalid_research_checkpoint;
for(const code of ["research_coverage_incomplete","invalid_research"])STOP_RULES[code]=STOP_RULES.research_required;
// Correction loops: a research check replays its saved rejections on a resume, a script stage starts them anew.
for(const code of ["rejected_output","invalid_evidence_review","invalid_question_routing","invalid_research_patch","invalid_research_assessment",
  "invalid_search_receipt","invalid_question_review","invalid_evidence","invalid_question_plan","invalid_question_scope","question_scope_unresolved",
  "invalid_supplement","invalid_teaching_review","invalid_script_evidence_review","invalid_polish_review","invalid_series_review","invalid_script","invalid_revision"])
  STOP_RULES[code]=c=>c.kind==="script"?{kind:"retry",title:"Korrekturversuche aufgebraucht",text:"Das Modell hat die automatischen Korrekturversuche dieses Schritts verbraucht. „Fortsetzen“ startet sie neu; hält der Schritt erneut an, hilft ein neues Inhaltsverzeichnis.",actions:["new_outline"]}:
    {kind:"dead",title:"Korrekturversuche aufgebraucht",text:`${EXHAUSTED} Die geprüften Teilantworten bleiben lesbar; weiter geht es mit einer neuen Recherche.`,actions:["restart"]};
// A chat, a check or a voice sample has no run to continue: the same button starts it again.
const RESTART_VERBS={assistant:"„Erneut senden“",check:"„Verbindungen prüfen“",audio_sample:"„Hörprobe erzeugen“",audio_samples:"„Fehlende Hörproben erzeugen“"};
function stopCodeOf(job) {
  if(job?.stop?.code)return job.stop.code;
  if(job?.error_code)return job.error_code;
  return Object.values(job?.run?.stages||{}).map(record=>record?.error?.code).find(Boolean)||"";
}
function stopInfo(job) {
  if(!job||["running","completed"].includes(job.status))return null;
  const run=job.run||null, kind=run?.kind||job.stop?.run_kind||null, progress=job.progress||{};
  let code=stopCodeOf(job);
  if(job.status==="review_ready")code="review_ready";
  if(!code&&progress.research_questions?.phase==="blocked")code="research_questions_blocked";
  if(!code&&progress.plan_review?.awaiting)code="research_plan_review";
  if(job.action==="assistant"&&code==="research_budget_exhausted")code="chat_budget";
  const context={job,run,kind,code,progress};
  let rule=STOP_RULES[code]??STOP_RULES[job.status];
  if(typeof rule==="function")rule=rule(context);
  rule??={kind:"retry",title:"Angehalten",actions:["restart"],
    text:`Der Arbeitsschritt hat angehalten${code?` (Code ${code})`:""}. „Fortsetzen“ versucht ihn erneut; alles Fertige bleibt gespeichert. Hält er an derselben Stelle wieder an, geht es nur mit einem neuen Lauf weiter.`};
  const value=v=>typeof v==="function"?v(context):v;
  const info={code,kind:rule.kind,title:value(rule.title),text:value(rule.text),actions:[...(value(rule.actions)||[])],card:!!rule.card};
  const verb=!run&&RESTART_VERBS[job.action];
  if(verb){
    info.kind=["wait","fix","decision"].includes(info.kind)?info.kind:"retry";
    info.text=info.text.replaceAll("„Fortsetzen“",verb);
    info.actions=info.actions.filter(a=>["key","check"].includes(a));
    if(job.action==="check"&&!info.actions.includes("check"))info.actions.push("check");
    if(job.action==="audio_samples")info.actions.push("samples");
  }
  const stop=job.stop||{};
  // The server already shortens paths; an older server's message still must not show a local path.
  const scrub=text=>String(text||"").replace(/[A-Za-z]:[\\/][^\s"'<>|]*/g,path=>path.split(/[\\/]/).pop());
  return {...info,message:scrub(stop.message??job.message??""),detail:stop.detail||null,file:stop.file||null,crash:stop.crash_detail||job.crash_detail||null};
}
// "Fortsetzen" is offered where it can help: retry, wait and fix stops, an approved plan, decided questions.
// Blocked questions whose current block has no advice yet (research_advisor.block_key); a waiting question gets none.
const unadvised=ledger=>(ledger?.questions||[]).filter(q=>q.status==="blocked"&&!q.accepted_gap&&!q.retry_requested
  &&q.outcome!=="prerequisite_block"&&q.advice?.key!==`${Number(q.retries||0)}.${Number(q.auto_retries||0)}`).length;
function canResume(job,info=stopInfo(job)) {
  if(!job?.run||!info)return false;
  const ledger=job.progress?.research_questions, review=job.progress?.plan_review;
  if(info.code==="research_plan_review")return !review?.awaiting||!!review.approved;
  // The server lifts the ledger out of "blocked" once every blocked question is decided; a block without advice
  // resumes too, because the run asks the advisor first.
  if(info.code==="research_questions_blocked")return Number(ledger?.reopenable||0)>0||Number(ledger?.retry_requested||0)>0||ledger?.phase!=="blocked"||unadvised(ledger)>0;
  return ["retry","wait","fix"].includes(info.kind)&&!info.actions.includes("key");
}
function restartAction(job) {
  const kind=job.run?.kind||job.stop?.run_kind;
  if(kind==="research")return "new_research";
  if(kind==="episode_audio")return "audio_again";
  if(kind==="script")return runPage(job.run)===PAGE.outline?"replan_feedback":"new_outline";
  return null;
}
function suggestedCalls(job) {
  const p=job.progress||{}, ledger=p.research_questions?.budget_projection, script=p.budget_projection;
  const limit=Number(p.model_call_limit)||0, used=Number(p.model_calls)||0;
  if(ledger&&Number.isFinite(Number(ledger.used)))
    return Math.max(limit+1,Number(ledger.used)+Math.max(Number(ledger.expected_remaining_calls||0),Number(ledger.minimum_remaining_calls||0)));
  // The script projection names minimums without repairs; a quarter more leaves room for corrections.
  if(script&&Number.isFinite(Number(script.minimum_remaining_calls)))
    return Math.max(limit+1,Number(script.used??used)+Math.ceil(Number(script.minimum_remaining_calls)*5/4));
  return Math.max(limit,used)+50;
}
function stopButton(action,job,info,target) {
  const runId=escape(job.run?.run_id||"");
  const onPage=page=>page===step?"":`<button class="secondary" data-step="${page}">${escape(steps[page])} öffnen</button>`;
  switch(action){
    case "approve_calls":{const n=suggestedCalls(job);return `<button data-action="approve-calls" data-run-id="${runId}" data-model-calls="${n}" data-then-resume="1" ${running()?"disabled":""}>Aufruflimit auf ${n} erhöhen und fortsetzen</button>`;}
    case "approve_search":{const n=(Number(job.progress?.search_round_limit)||0)+6;return `<button data-action="approve-search" data-run-id="${runId}" data-search-rounds="${n}" data-then-resume="1" ${running()?"disabled":""}>Suchrunden auf ${n} erhöhen und fortsetzen</button>`;}
    case "key":return inlineKey("stop-key",!!job.run,target||`data-run-id="${runId}"`);
    case "check":return `<button class="secondary" data-action="check" ${disabled()}>Verbindungen prüfen</button>`;
    case "samples":return `<button class="secondary" data-action="audio_samples" ${disabled()}>Fehlende Hörproben erzeugen · API</button>`;
    case "new_research":return `<button class="secondary" data-action="research" data-confirm="Neu recherchieren startet einen neuen Recherchelauf mit neuem Plan. Der angehaltene Lauf bleibt gespeichert, wird aber nicht fortgesetzt. Fortfahren?" ${disabled()}>Neu recherchieren</button>`;
    case "new_outline":return project?.research?`<button class="secondary" data-action="plan" data-confirm="Ein neues Inhaltsverzeichnis entsteht aus der vorhandenen Recherche und braucht wieder deine Freigabe. Lehrkonzepte und Skripte des angehaltenen Laufs werden nicht fortgesetzt. Fortfahren?" ${disabled()}>Neues Inhaltsverzeichnis entwerfen</button>`:"";
    case "replan_feedback":return `<div class="stop-feedback">${area("stop-feedback","Was soll der neue Entwurf anders machen?",info.message||"",3)}<button data-action="replan" data-feedback="stop-feedback" ${disabled()}>Mit diesem Hinweis neu entwerfen</button></div>`;
    case "audio_again":return step===PAGE.audio?'<button class="secondary" data-scroll="audio-panel">Zur Audio-Freigabe</button>':`<button class="secondary" data-step="${PAGE.audio}">Zur Audio-Freigabe</button>`;
    case "open_research":return onPage(PAGE.research);
    case "open_outline":return onPage(PAGE.outline);
    case "open_scripts":return onPage(PAGE.scripts);
    case "open_brief":return onPage(PAGE.brief);
    default:return "";
  }
}
function waitNote(job) {
  const when=iso=>escape(new Date(iso).toLocaleString("de-DE",{dateStyle:"short",timeStyle:"short"}));
  if(job.auto_resume_at){
    if(Date.parse(job.auto_resume_at)<=Date.now())return '<p class="hint">Die automatische Fortsetzung ist fällig und startet, sobald kein anderer Auftrag im Studio läuft.</p>';
    return `<p class="hint">Automatische Fortsetzung geplant für ${when(job.auto_resume_at)}, solange das Studio geöffnet bleibt (Versuch ${Number(job.auto_resume_count||0)+1} von 3).</p>`;
  }
  if(job.auto_resume_exhausted)return '<p class="hint">Die automatischen Fortsetzungen sind aufgebraucht. Nach dem Reset bitte selbst „Fortsetzen“.</p>';
  if(job.retry_at&&job.status==="waiting_for_quota")return `<p class="hint">Voraussichtlich wieder frei: ${when(job.retry_at)}.</p>`;
  return "";
}
function techDetails(info) {
  if(!info.detail&&!info.crash&&!info.file&&!info.code)return "";
  const link=info.file&&project?`<p><a href="/api/projects/${encodeURIComponent(project.id)}/file?path=${encodeURIComponent(info.file)}" target="_blank" rel="noopener">Datei öffnen: ${escape(info.file)}</a></p>`:"";
  return `<details class="tech-details"><summary>Technische Details</summary>${info.code?`<p class="hint">Haltecode: ${escape(info.code)}</p>`:""}${info.detail?`<p>${escape(info.detail)}</p>`:""}${info.crash?`<pre>${escape(info.crash)}</pre>`:""}${link}</details>`;
}
// The stop's explanation, the step's own message, the controls and the technical details, in reading order.
function stopBody(job,info,{target="",blocked=null}={}) {
  // Another job holds the Studio, typically episodes being voiced: the controls wait and say so.
  const reason=blocked??(running()?otherJobText():"");
  const buttons=[...new Set(info.actions.map(a=>a==="restart"?restartAction(job):a).filter(Boolean))].map(a=>stopButton(a,job,info,target)).join("");
  const resume=canResume(job,info)?`<button data-action="resume" ${target||`data-run-id="${escape(job.run?.run_id||"")}"`} ${reason?"disabled":""}>Fortsetzen</button>`:"";
  const message=info.message&&info.message!==info.text?`<p class="stop-message">Meldung: ${escape(info.message)}</p>`:"";
  return `<p>${escape(info.text)}</p>${message}${waitNote(job)}${resume||buttons?`<div class="actions">${resume}${buttons}</div>`:""}${reason&&(resume||buttons)?`<p class="hint">${escape(reason)}</p>`:""}${techDetails(info)}`;
}
function otherJobText() {
  const voicing=(project?.audio_jobs||[]).filter(j=>j.status==="running").length;
  return voicing?`Gerade ${voicing===1?"wird eine Folge":`werden ${voicing} Folgen`} vertont. Fortsetzen und Neustart gehen, sobald die Vertonung fertig ist oder angehalten wurde.`:
    "Gerade läuft ein anderer Auftrag. Fortsetzen und Neustart gehen, sobald er fertig ist oder angehalten wurde.";
}
// Gemini episodes whose latest job stopped and that have no current recording from another run.
function stoppedAudio(p=project) {
  const current=id=>(p?.episodes||[]).some(e=>(e.script?.episode_id??e.episode_id)===id&&e.audio_current&&e.audio?.length);
  return (p?.audio_jobs||[]).filter(j=>stopInfo(j)&&!current(j.episode));
}
function renderStopCard(job,info) {
  return `<section class="panel stop-card ${escape(info.kind)}" role="alert"><div class="panel-title"><h2>${escape(info.title)}</h2><span class="tag">${escape(STOP_KIND_LABELS[info.kind]||"Angehalten")}</span></div>${stopBody(job,info)}</section>`;
}

function renderResearchQuestions(ledger, opened=new Set(), active=false, runId="", searchLimit=0, searchRounds=0, sourceLimit=0) {
  const budget=ledger.budget_projection;
  const expected=Number.isSafeInteger(budget?.expected_remaining_calls)?` Erfahrungsgemäß etwa ${Number(budget.expected_remaining_calls)} Aufrufe (${Number(budget.expected_calls_per_task)} je offener Teilfrage).`:"";
  const suggested=budget?Number(budget.used)+Math.max(Number(budget.expected_remaining_calls||0),Number(budget.minimum_remaining_calls||0)):0;
  const approveCalls=budget&&!budget.feasible&&!active?`<button class="secondary small" data-action="approve-calls" data-run-id="${escape(runId)}" data-model-calls="${suggested}">Aufruflimit auf ${suggested} erhöhen</button>`:"";
  const budgetNote=budget?`<p class="${budget.feasible?"hint":"note"}">Mindestens ${Number(budget.minimum_remaining_calls)} weitere Modellaufrufe, davon ${Number(budget.closing_calls)} für Dossier und Abschlussprüfung; ${Number(budget.remaining)} verfügbar.${escape(expected)} ${budget.feasible?"Zusätzliche Lese-, Such- und Korrekturschritte können mehr benötigen.":`Das genehmigte Limit reicht um mindestens ${Number(budget.shortfall)} Aufrufe nicht aus. Antworten und Umfang bleiben erhalten; ein höheres Limit erfordert eine ausdrückliche Genehmigung.`}</p>${approveCalls}`:"";
  const states={pending:"Wartet",researching:"Wird untersucht",reviewing:"Antwort wird geprüft",verified:"Geprüft abgeschlossen",blocked:"Blockiert"};
  const phases={awaiting_plan_approval:"Wartet auf Freigabe des Rechercheplans",questions:"Einzelne Fragen untersuchen und prüfen",synthesis:"Dossier aus geprüften Antworten erstellen",audit:"Gesamtdossier prüfen",completed:"Recherche abgeschlossen",blocked:"Offene Belegfragen"};
  const outcomes={supported_answer:"Belegte Antwort",supported_uncertainty:"Belegte wissenschaftliche Unsicherheit",access_block:"Quelle nicht zugänglich",extraction_block:"Text nicht zuverlässig extrahiert",search_block:"Suche ohne ausreichenden Abschluss",budget_block:"Recherchebudget ausgeschöpft",evidence_block:"Beleg fehlt",prerequisite_block:"Voraussetzung noch offen",accepted_gap:"Als Lücke akzeptiert"};
  const activeIds=activeTasks(ledger), all=ledger.questions||[];
  // A blocked question names its cause and when it is decided: the decision card appears only once the run stops.
  const blockedNote=row=>{
    const prerequisites=all.filter(q=>(row.depends_on||[]).includes(q.id)&&q.status!=="verified");
    const waiting=row.outcome==="prerequisite_block"&&prerequisites.length&&!prerequisites.some(q=>q.accepted_gap);
    const cause=waiting?`Wartet auf: ${prerequisites.map(q=>q.question).join("; ")}`:row.reason;
    // The counted web searches, next to the model's own wording: with the run's rounds used up it searched only what was read.
    const web=Number(row.web_attempts||0), exhausted=searchLimit>0&&searchRounds>=searchLimit;
    const fetched=Number(ledger.source_attempt_count||0), full=sourceLimit>0&&fetched>=sourceLimit;
    const spent=[exhausted?`Suchrunden des Laufs aufgebraucht (${Number(searchRounds)} von ${Number(searchLimit)})`:"",
      full?`Quellenlimit des Laufs erreicht (${fetched} von ${Number(sourceLimit)} Quellen)`:""].filter(Boolean);
    const raise=exhausted&&full?"Suchrunden und Quellenlimit":full?"das Quellenlimit":"die Suchrunden";
    const searched=waiting?"":`<p class="hint">Websuchen für diese Frage: ${web}${spent.map(text=>` · ${text}`).join("")}.${spent.length&&!web?` Sie hat deshalb nur in den schon gelesenen Quellen gesucht; auch ein neuer Versuch sucht erst wieder im Web, wenn du ${raise} erhöhst.`:""}</p>`;
    const decision=row.retry_requested?"Ein neuer Versuch ist angefordert; „Fortsetzen“ startet ihn."
      :row.reopenable?"Das Web wurde für diese Teilfrage noch nicht durchsucht; „Fortsetzen“ holt das nach."
      :waiting?`Ein neuer Versuch der Voraussetzung nimmt diese Frage automatisch wieder auf.${active?" Entscheiden kannst du, wenn der Lauf anhält.":""}`
      :active?"Entscheiden musst du erst, wenn der Lauf anhält. Dann steht diese Frage oben unter „Wartet auf dich“, mit „Noch einmal versuchen“ und „Als Lücke akzeptieren“. Bis dahin arbeitet der Lauf an den übrigen Fragen weiter."
      :"Entscheide oben unter „Wartet auf dich“: noch einmal versuchen oder als Lücke akzeptieren.";
    return `<div class="note"><p><strong>Blockiert: ${escape(outcomes[row.outcome]||"Beleg fehlt")}</strong>${cause?` · ${escape(cause)}`:""}</p>${searched}${row.advice?`<p><strong>Beratung:</strong> ${escape(row.advice.diagnosis)}</p>`:""}<p>${decision}</p></div>`;
  };
  const rows=all.map(row=>{
    const blocked=row.status==="blocked"&&!row.accepted_gap;
    const answer=row.status==="verified"&&row.answer?`
      <div class="prose">${renderMarkdown(row.answer)}</div>
      ${(row.findings||[]).map(f=>`<p>${escape(f.statement)}</p>`).join("")}
      ${row.sources?.length?`<p>Gelesene Belege:</p><ul>${row.sources.map(source=>`<li>${markdownLink(escape(source.title),source.url)}${source.page?`, Seite ${Number(source.page)}`:""}</li>`).join("")}</ul>`:""}
      ${row.limits?.length?`<p>Grenzen der Antwort:</p><ul>${row.limits.map(l=>`<li>${escape(l)}</li>`).join("")}</ul>`:""}`:"";
    return `<details data-research-question="${escape(row.id)}"${opened.has(row.id)?" open":""}>
      <summary>${row.status==="verified"?"✓":row.accepted_gap?"–":blocked?(row.retry_requested?"↻":"⛔"):active&&activeIds.includes(row.id)?"●":"○"} ${escape(row.question)} · ${escape(row.accepted_gap?"Als Lücke akzeptiert":blocked&&row.retry_requested?"Neuer Versuch angefordert":!active&&["researching","reviewing"].includes(row.status)?"Begonnen · geht beim Fortsetzen weiter":(states[row.status]||row.status))}</summary>
      ${blocked?blockedNote(row):""}
      <p>${escape(row.activity)}</p>
      <p class="hint">${Number(row.read_sections)} Abschnitte gelesen · ${Number(row.steps)} Bearbeitungsschritte${row.reopened?` · ${Number(row.reopened)} Mal mit Einwand wieder geöffnet`:""}</p>
      ${row.support?`<p class="hint">Textbelege vorhanden · Inhalt automatisch je Befund geprüft · ${row.support.findings.filter(f=>f.empirical_status==="independently_tested").length} Befunde mit dokumentierter unabhängiger empirischer Prüfung</p>`:""}
      ${row.outcome&&!blocked?`<p class="hint">Ergebnis: ${escape(outcomes[row.outcome]||row.outcome)}</p>`:""}
      <p>Abschlusskriterien:</p><ul>${(row.acceptance||[]).map(c=>`<li>${escape(c)}</li>`).join("")}</ul>
      ${row.reason&&!blocked?`<p><strong>Noch offen:</strong> ${escape(row.reason)}</p>`:""}${row.reopenable&&!blocked?`<p class="hint">Das Web wurde für diese Teilfrage noch nicht durchsucht; „Fortsetzen“ holt das nach.</p>`:""}${row.accepted_gap?`<p class="hint">Diese Teilfrage bleibt im Dossier als dokumentierte Lücke${row.accepted_reason?`: ${escape(row.accepted_reason)}`:"."}</p>`:""}${answer}</details>`;
  }).join("");
  return `<section class="research-questions">
    <p><strong>${Number(ledger.closed)} von ${Number(ledger.total)} Teilfragen geprüft abgeschlossen${Number(ledger.accepted)>0?` · ${Number(ledger.accepted)} als Lücke akzeptiert`:""}</strong></p>
    ${researchRound(ledger)}
    <progress value="${Number(ledger.closed)}" max="${Number(ledger.total)}" aria-label="Geprüft abgeschlossene Teilfragen"></progress>
    <p>${escape(phases[ledger.phase]||"")}</p>${renderActiveTasks(ledger,active)}${budgetNote}
    <p class="hint">Die Abschlusskriterien bleiben fest. Eine geprüfte Antwort wird nur bei einem konkreten Einwand aus der Gesamtprüfung erneut geöffnet.</p>${rows}</section>`;
}
const calibrationSources={run:"in diesem Lauf gemessen",project:"Erfahrungswert des Projekts",default:"Standardwert"};
function planSummary(p) {
  const hours=Number(p.projected_hours), minutes=Number(p.seconds_per_call)/60;
  const german=(n,d)=>n.toLocaleString("de-DE",{minimumFractionDigits:0,maximumFractionDigits:d});
  return `${Number(p.tasks)} Teilfragen, voraussichtlich ${Number(p.projected_calls)} Aufrufe, etwa ${german(hours,hours>=10?0:1)} Stunden bei ${german(minutes,1)} Minuten je Aufruf`;
}
function planApprovalRequest(runId) {
  // The receipt approves exactly the shown plan; a cap asks for one re-plan that is shown again before it runs.
  const payload={kind:"plan",run_id:runId};
  const raw=String($("plan-max-tasks")?.value??"").trim();
  if(raw){const n=Number(raw);if(!Number.isInteger(n)||n<1)throw new Error("Höchstens N Teilfragen: bitte eine ganze Zahl ab 1 eingeben.");payload.max_tasks=n;}
  return payload;
}
function renderPlanReview(job, runId) {
  const review=job?.progress?.plan_review, p=review?.projection;
  if(!review?.awaiting||!p||job?.status==="running")return "";
  const basis=`<p class="hint">${Number(p.expected_calls_per_task)} Aufrufe je Teilfrage (${escape(calibrationSources[p.expected_calls_source]||"Standardwert")}), dazu ${Number(p.closing_calls)} für Dossier und Abschlussprüfung; Aufrufdauer ${escape(calibrationSources[p.seconds_per_call_source]||"Standardwert")}. Genehmigtes Limit ${Number(p.approved_limit)} Aufrufe, ${Number(p.used)} verbraucht.${p.within_limit===false?" Das Limit reicht dafür voraussichtlich nicht.":""}</p>`;
  if(review.approved)return `<section class="plan-review"><strong>Rechercheplan freigegeben</strong><p>${escape(planSummary(p))}</p>${basis}<p class="hint">„Fortsetzen“ beginnt mit der ersten Teilfrage.${review.approval?.max_tasks?` Angeforderte Obergrenze: ${Number(review.approval.max_tasks)} Teilfragen; der Plan wird zuerst neu zugeschnitten und erneut vorgelegt.`:""}</p><div class="actions"><button data-action="resume" data-run-id="${escape(runId)}" ${running()?"disabled":""}>Fortsetzen</button></div></section>`;
  const caps=(p.plan_caps||[]).map(Number).filter(Number.isInteger);
  const capNote=caps.length&&Number(p.tasks)>Math.min(...caps)?`<p class="note">Eine Obergrenze von ${Math.min(...caps)} Teilfragen wurde bereits angefordert; die Planung konnte den Plan nicht weiter bündeln, ohne Verpflichtungen wegzulassen. Diesen Plan freigeben oder eine neue Recherche starten.</p>`:"";
  // The projection counts the remaining calls; a tenth more leaves room for reading and correction steps.
  const raise=p.within_limit===false?Number(p.used)+Math.ceil(Number(p.projected_calls)*11/10):0;
  return `<section class="plan-review"><strong>Wartet auf Freigabe des Rechercheplans</strong><p>${escape(planSummary(p))}</p>${basis}${capNote}
    <div class="field"><label for="plan-max-tasks">Höchstens N Teilfragen (optional)</label><input id="plan-max-tasks" type="number" inputmode="numeric" min="1" max="${Number(p.tasks)}" step="1" placeholder="${Number(p.tasks)}"></div>
    <div class="actions"><button data-action="approve-plan" data-run-id="${escape(runId)}" data-then-resume="1" ${running()?"disabled":""}>Rechercheplan freigeben und starten</button>${raise>Number(p.approved_limit)?`<button class="secondary" data-action="approve-calls" data-run-id="${escape(runId)}" data-model-calls="${raise}">Aufruflimit auf ${raise} erhöhen</button>`:""}</div>
    <p class="hint">Ohne Freigabe wird kein Modellaufruf verbraucht. Mit einer Obergrenze wird der Plan einmal neu zugeschnitten (Planungsaufrufe) und erneut zur Freigabe vorgelegt; die Freigabe gilt immer genau für den angezeigten Plan.</p></section>`;
}
function gapActionsFor(row, runId, searchLimit) {
  const searchBlocked=row.outcome==="budget_block"&&/Suchbudget|Suchrunden/.test(row.reason||"");
  // A question that only waits for its prerequisite has no failed attempt of its own; retrying the prerequisite takes it up again.
  const retry=row.outcome==="prerequisite_block"?'<span class="hint">Ein neuer Versuch der Voraussetzung nimmt diese Frage automatisch wieder auf.</span>':`<input id="retry-hint-${escape(row.id)}" value="${escape(row.advice?.hint||"")}" placeholder="Hinweis für den neuen Versuch (optional)" aria-label="Hinweis für den neuen Versuch"><button class="small" data-action="retry-task" data-run-id="${escape(runId)}" data-task-id="${escape(row.id)}">Noch einmal versuchen</button>`;
  const gap=`<input id="gap-reason-${escape(row.id)}" placeholder="Begründung für die Lücke (optional)" aria-label="Begründung für die akzeptierte Lücke"><button class="secondary small" data-action="accept-gap" data-run-id="${escape(runId)}" data-task-id="${escape(row.id)}">Als Lücke akzeptieren</button>`;
  return `<div class="actions">${retry}</div><div class="actions">${gap}${searchBlocked?`<button class="secondary small" data-action="approve-search" data-run-id="${escape(runId)}" data-search-rounds="${Number(searchLimit)+6}">Suchrunden auf ${Number(searchLimit)+6} erhöhen</button>`:""}</div>`;
}
// Every open decision stands in one card above the ledger with its buttons visible: nothing to expand, no dialog.
function renderResearchDecisions(j, r, active, reopenable, resumable, searchLimit) {
  const ledger=j.progress.research_questions, rows=ledger?.questions||[], runId=r?.run_id||"";
  if(active||!rows.length)return "";
  const open=rows.filter(q=>q.status==="blocked"&&!q.accepted_gap), accepted=rows.filter(q=>q.accepted_gap);
  const undecided=open.filter(q=>!q.retry_requested), retrying=open.filter(q=>q.retry_requested);
  // Accepted gaps alone are no decision; they only lead the card while the run stopped for the blocked questions.
  const code=stopInfo(j)?.code;
  if(!open.length&&!(accepted.length&&(!code||code==="research_questions_blocked")))return "";
  const rounds=Number(j.progress.search_rounds||0), roundLimit=Number(j.progress.search_round_limit||0);
  const roundsNote=roundLimit&&roundLimit-rounds<=1?`<p class="hint">Suchrunden: ${rounds} von ${roundLimit} verbraucht. Ein neuer Versuch braucht meist eine Websuche. <button class="secondary small" data-action="approve-search" data-run-id="${escape(runId)}" data-search-rounds="${roundLimit+6}">Suchrunden auf ${roundLimit+6} erhöhen</button></p>`:"";
  // Every web search loads new sources; at the run's source limit it ends before it starts.
  const fetched=Number(ledger.source_attempt_count||0), sourceLimit=Number(j.progress.source_limit||0);
  const sourcesNote=sourceLimit&&fetched>=sourceLimit?`<p class="hint">Quellen: ${fetched} von ${sourceLimit} abgerufen. Eine Websuche kann keine neuen Quellen laden, bis du das Quellenlimit erhöhst. <button class="secondary small" data-action="approve-sources" data-run-id="${escape(runId)}" data-sources="${sourceLimit+40}">Quellenlimit auf ${sourceLimit+40} erhöhen</button></p>`:"";
  const names=new Map(rows.map(q=>[q.id,q.question]));
  const outcomes={extraction_block:"Quelle nicht lesbar",search_block:"Keine neuen Belege gefunden",evidence_block:"Beleg fehlt",budget_block:"Recherchebudget ausgeschöpft",access_block:"Quelle nicht zugänglich",prerequisite_block:"Voraussetzung offen"};
  // The advisor's second opinion stands with the question: cause, recommendation, the sources it found.
  const recommendations={retry:"Noch einmal versuchen",accept_gap:"Als Lücke akzeptieren",raise_limit:"Limit erhöhen"};
  const limitNames={sources:"Quellenlimit erhöhen",search_rounds:"Suchrunden erhöhen",model_calls:"Aufruflimit erhöhen"};
  const advice=q=>{
    const a=q.advice;
    if(!a)return "";
    const recommendation=a.recommendation==="raise_limit"&&limitNames[a.limit]?limitNames[a.limit]:recommendations[a.recommendation]||a.recommendation;
    const sources=(a.sources||[]).map(s=>`<li>${s.url?markdownLink(escape(s.title),s.url):escape(s.title)}${s.note?`<span class="hint"> · ${escape(s.note)}</span>`:""}</li>`).join("");
    return `<div class="advice"><p><strong>Beratung:</strong> ${escape(a.diagnosis)}</p><p class="hint">Empfehlung: ${escape(recommendation)}${Number(q.auto_retries||0)?" · Ein automatischer neuer Versuch nach der Beratung lief bereits.":""}</p>${sources?`<ul>${sources}</ul>`:""}</div>`;
  };
  const item=q=>{
    const deps=(q.depends_on||[]).filter(id=>rows.some(x=>x.id===id&&x.status!=="verified")).map(id=>names.get(id)||id);
    const attempts=Number(q.web_attempts||0);
    return `<li><strong>${escape(q.question)}</strong><p class="hint">${escape(outcomes[q.outcome]||q.outcome||"Blockiert")}${attempts?` · ${attempts} ${attempts===1?"Websuche":"Websuchen"}`:Number(q.steps||0)?" · keine Websuche":" · noch nicht bearbeitet"}${deps.length?` · hängt an: ${deps.map(escape).join("; ")}`:""}</p>${q.reason?`<p class="hint">${escape(q.reason)}</p>`:""}${advice(q)}${q.retry_requested?`<p class="hint">↻ Neuer Versuch angefordert${q.retry_hint?` · Hinweis: ${escape(q.retry_hint)}`:""}. „Fortsetzen“ startet ihn.</p>`:q.reopenable?'<p class="hint">Das Web wurde für diese Teilfrage noch nicht durchsucht; „Fortsetzen“ führt diese Websuche aus.</p>':gapActionsFor(q,runId,searchLimit)}</li>`;
  };
  const closing=Number(ledger.budget_projection?.closing_calls||0);
  const intro=undecided.length?`${undecided.length===1?"Eine Teilfrage ist":`${undecided.length} Teilfragen sind`} blockiert. ${reopenable?"„Fortsetzen“ holt zuerst die fehlende Websuche nach.":unadvised(ledger)?"„Fortsetzen“ lässt sie zuerst beraten; einen empfohlenen neuen Versuch startet der Lauf dann selbst, einmal je Frage. Du kannst auch direkt entscheiden: noch einmal versuchen oder als Lücke akzeptieren.":`Für jede: noch einmal versuchen oder als Lücke akzeptieren. Danach schließt „Fortsetzen“ das Dossier mit ${Number(ledger.closed)} geprüften Antworten ab${closing?` (${closing} Aufrufe)`:""}.`}`:retrying.length?`Jede blockierte Teilfrage ist entschieden. „Fortsetzen“ startet ${retrying.length===1?"den neuen Versuch":`die ${retrying.length} neuen Versuche`}.`:"Jede blockierte Teilfrage ist entschieden. „Fortsetzen“ schließt das Dossier ab.";
  return `<section class="panel decision-card" aria-label="Wartet auf dich"><h2>Wartet auf dich</h2><p>${intro}</p>${roundsNote}${sourcesNote}<ol class="decisions">${open.map(item).join("")}${accepted.map(q=>`<li class="done">✓ ${escape(q.question)} · als Lücke akzeptiert${q.accepted_reason?` (${escape(q.accepted_reason)})`:""}</li>`).join("")}</ol>${resumable?`<div class="actions"><button data-action="resume" data-run-id="${escape(runId)}" ${running()?"disabled":""}>Fortsetzen</button></div>${running()?`<p class="hint">${escape(otherJobText())}</p>`:""}`:""}</section>`;
}
// The first retrieval reads every found source; its counter and its report stand where the ledger will appear.
function renderRetrieval(retrieval) {
  if(!retrieval)return "";
  const failures=retrieval.failures||[], failed=Number(retrieval.failed||0);
  const running=retrieval.running?`<p><strong>Originaltexte einlesen:</strong> ${Number(retrieval.attempted)} von bis zu ${Number(retrieval.total)} Quellen abgerufen, ${Number(retrieval.imported)} lesbar${failed?`, ${failed} nicht eingelesen`:""}.</p><progress value="${Number(retrieval.attempted)}" max="${Math.max(1,Number(retrieval.total))}" aria-label="Abgerufene Quellen"></progress>`:"";
  const report=failed?`<details class="retrieval-report"><summary>Abrufbericht · ${failed} ${failed===1?"Quelle":"Quellen"} nicht eingelesen</summary><ul>${failures.map(row=>`<li>${escape(row.source)}<span class="hint"> · ${escape(row.reason)}</span></li>`).join("")}</ul>${failed>failures.length?`<p class="hint">Weitere ${failed-failures.length} stehen im Laufordner.</p>`:""}</details>`:"";
  return running+report;
}
// The research page owns the decision and the ledger; the drawer only carries telemetry.
function renderResearchPanel(j,r,active,questionOpen,researchOpen,researchBlocked,reopenable,resumable) {
  const p=j.progress, ledger=p.research_questions, quality=p.research_quality, runId=r?.run_id||"";
  let html=renderPlanReview(j,runId)+renderResearchDecisions(j,r,active,reopenable,resumable,p.search_round_limit)+renderRetrieval(p.retrieval);
  // What runs right now and what the user can do stand above the question rows, never below them.
  const open=active?(p.open_calls||[]):[];
  const current=`<p class="current-step"><strong>${active?(open.length>1?"Zuletzt gemeldet":"Gerade"):"Zuletzt"}:</strong> ${escape(p.activity||"")}</p>`+
    (open.length>1?`<p class="hint">${open.length} Modellaufrufe laufen gleichzeitig: ${open.map(row=>`${escape(shortText(row.label||"Rechercheschritt",60))} · seit ${progressAge(row.started_at)}`).join("; ")}</p>`:"")+
    (active&&p.stopping?`<p class="note" role="status"><strong>Eine Teilfrage ist angehalten${p.stopping.question?`: „${escape(shortText(p.stopping.question,80))}“`:""}.</strong> Die anderen laufenden Teilfragen beenden noch ihren aktuellen Aufruf; danach hält die Recherche an und diese Seite sagt, was zu tun ist.</p>`:"");
  const next=active?`<p class="next-step"><strong>Nächster Schritt:</strong> Nichts zu tun, der Lauf arbeitet${p.activity?` (${escape(p.activity)})`:""}. Ein Modellaufruf dauert meist 3 bis 8 Minuten; Zusammenstellung und Prüfung eines großen Dossiers laufen in vielen Teilen und können Stunden dauern.</p>`:"";
  if(ledger){
    if(reopenable)html+=`<p>${Number(ledger.reopenable)} blockierte ${Number(ledger.reopenable)===1?"Teilfrage hat":"Teilfragen haben"} das Web noch nicht durchsucht. „Fortsetzen“ holt diese Websuche nach; erst danach gilt eine Frage als konkrete Lücke. Fertige Antworten bleiben gespeichert.</p>`;
    else if(researchBlocked&&unadvised(ledger))html+=`<p>${unadvised(ledger)} blockierte ${unadvised(ledger)===1?"Teilfrage hat":"Teilfragen haben"} noch keine Beratung. „Fortsetzen“ lässt sie zuerst beraten; einen empfohlenen neuen Versuch startet der Lauf selbst, jede andere Entscheidung bleibt bei dir.</p>`;
    else if(researchBlocked)html+=`<p>Die automatischen Versuche sind für die aufgeführten Fragen ausgeschöpft. Fertige Antworten bleiben gespeichert. Fortsetzen allein wiederholt diese Versuche nicht. Eine blockierte Teilfrage kann als Lücke akzeptiert werden; das Dossier wird dann ohne sie abgeschlossen und nennt die Lücke ausdrücklich.</p>`;
    html+=current+next+renderResearchQuestions(ledger,questionOpen,active,runId,p.search_round_limit,p.search_rounds,p.source_limit);
  }
  else html+=current+next;
  html+=`<p class="hint">Rechercherunden: ${Number(p.search_rounds||0)} von ${Number(p.search_round_limit||0)}. Fehlende Belege werden automatisch nachrecherchiert.</p>`;
  if(quality){
    const pending=quality.assessment_status==="pending_after_source_review"||(ledger&&ledger.phase!=="completed");
    html+=`<details class="research-quality"${researchOpen?" open":""}><summary>${pending?(ledger?"Gesamtbewertung folgt nach den Einzelantworten":"Quellenlücken werden gezielt geschlossen · Gesamtbewertung folgt"):`${Number(quality.closed)} von ${Number(quality.total)} Leitfragen erfüllen alle Qualitätsmerkmale`}</summary>${pending?(ledger?"<p>Der aktuelle Stand steht bei den einzelnen Recherchefragen. Die bisherigen Leitfragenbewertungen unten werden vor der Freigabe erneuert.</p>":"<p>Zuerst werden die fehlenden Belege gesucht und gelesen. Die bisherigen Leitfragenbewertungen unten werden danach erneuert.</p>"):""}<p>Geprüft werden vollständige Antworten, nachvollziehbare Erklärungen, gelesene Belege, unabhängige Gegenprüfung und Grenzen.</p>${(quality.requirements||[]).map(row=>`<p><strong>${pending?"·":row.passed?"✓":"○"} ${escape(row.question)}</strong></p><p>${escape(row.reason)}</p>${(row.missing||[]).length?`<ul>${row.missing.map(gap=>`<li>${escape(gap)}</li>`).join("")}</ul>`:""}`).join("")}${quality.blocking_gaps?.length?`<p>Weitere offene Punkte:</p><ul>${quality.blocking_gaps.map(gap=>`<li>${escape(gap)}</li>`).join("")}</ul>`:""}</details>`;
  }
  return `<section class="panel research-panel">${html}</section>`;
}
function drawerToggle() {
  return `<button class="quiet small drawer-toggle" data-action="drawer-toggle" aria-expanded="${drawerOpen}" aria-controls="job-status">${drawerOpen?"Maschinenraum schließen":"Maschinenraum"}</button>`;
}
function drawerMarkup(summary, body) {
  return `<div class="drawer-head"><button class="quiet small drawer-toggle" data-action="drawer-toggle" aria-expanded="${drawerOpen}">${drawerOpen?"▾":"▴"} Maschinenraum</button><span class="hint">${summary}</span></div><div class="drawer-body"${drawerOpen?"":" hidden"}>${body}</div>`;
}
function dock(show) { document.body?.classList?.toggle?.("has-dock",!!show); }
function audioJobSummary() {
  const jobs=project?.audio_jobs||[], active=jobs.filter(j=>j.status==="running");
  const title=id=>project.episodes?.find(e=>e.script.episode_id===id)?.script.title||id;
  const stopped=stoppedAudio().length, halted=stopped?` · ${stopped} angehalten`:"";
  if(active.length===1){const p=active[0].progress;return `Folge wird vertont · ${escape(title(active[0].episode))}${p?.total_segments!==undefined?` · ${Number(p.completed_segments)} von ${Number(p.total_segments)} Sprechabschnitten`:""}${halted}`;}
  if(active.length)return `${active.length} Folgen werden vertont${halted}`;
  const done=jobs.filter(j=>j.status==="completed").length;
  return done===jobs.length?`Vertonung abgeschlossen · ${done===1?escape(title(jobs[0].episode)):`${done} Aufträge`}`:`Vertonung: ${done} von ${jobs.length} Aufträgen fertig, ${jobs.length-done} angehalten`;
}
function renderAudioJobBar() {
  const active=(project?.audio_jobs||[]).some(j=>j.status==="running"), stopped=stoppedAudio().length;
  return `<span class="job-dot ${active?"running":stopped?"blocked":"done"}" aria-hidden="true"></span><span class="job-text">${audioJobSummary()}</span>${step!==PAGE.audio?`<button class="secondary small status-link" data-step="${PAGE.audio}">Vertonung ansehen →</button>`:""}${drawerToggle()}`;
}
// The connection check in German, with the fix next to every failed item.
const CHECK_LABELS={
  python:["Python",""],system:["Betriebssystem",""],model_catalog:["Modellkatalog",""],
  ffmpeg:["FFmpeg","Unter Windows einmal scripts\\setup-ffmpeg.ps1 ausführen, unter macOS und Linux sh scripts/setup.sh; danach das Studio neu starten."],
  ffprobe:["FFprobe","Kommt mit FFmpeg: scripts\\setup-ffmpeg.ps1 oder sh scripts/setup.sh ausführen, danach das Studio neu starten."],
  codex_login:["Codex-Anmeldung","Im Terminal „codex login“ ausführen. Die automatische Abo-Wahl kann stattdessen Claude nutzen."],
  claude_login:["Claude-Anmeldung","Im Terminal „claude auth login“ ausführen. Die automatische Abo-Wahl kann stattdessen Codex nutzen."],
  subscription_quota:["Abo-Kontingent","Ohne Kontingent pausieren Aufträge bis zum genannten Reset."],
  tts_environment:["Lokale Sprachausgabe (Qwen)","Die Einrichtung laut docs/qwen-windows.md prüfen oder Gemini als Audioanbieter wählen."],
  "Gemini-TTS-Key":["OpenRouter-Key für Gemini","Den Key unter „Geschützter OpenRouter-Key-Eingang“ auf dieser Seite hinterlegen."],
};
function renderChecks(checks) {
  return `<ul class="checks">${(checks.checks||[]).map(c=>{const [label,fix]=CHECK_LABELS[c.name]||[c.name,""];
    return `<li>${c.ok?"✓":"○"} ${escape(label)}<span class="hint">${escape(c.detail)}</span>${!c.ok&&fix?`<span class="hint check-fix">${escape(fix)}</span>`:""}</li>`;}).join("")}</ul>
    <p>${checks.ready?"Startbereit: mindestens ein Textanbieter ist nutzbar.":"Noch nicht startbereit: die markierten Punkte zuerst beheben."} Diese Prüfung erzeugt kein Audio.</p>`;
}
const SUMMARY_STATES={unchanged:"keine neuen Daten seit dem letzten Bericht",paused:"pausiert nach wiederholten Fehlern",summarizing:"wird gerade erstellt",unavailable:"letzter Versuch fehlgeschlagen"};
// The run's periodic short report, written by a small model from the saved activity; drafts count as unchecked.
function renderStatusSummary(summary) {
  if(!summary?.summary&&!SUMMARY_STATES[summary?.status])return "";
  return `<section class="status-summary"><strong>Kurzbericht</strong>${summary.generated_at?`<span class="hint"> · vor ${progressAge(summary.generated_at)}${summary.model?` · ${escape(summary.model)}`:""}</span>`:""}
    ${summary.summary?`<p>${escape(summary.summary)}</p>`:""}${SUMMARY_STATES[summary.status]?`<p class="hint">Kurzbericht: ${escape(SUMMARY_STATES[summary.status])}.</p>`:""}</section>`;
}
function runningTitle(job) {
  const run=job.run, stage=Object.entries(run?.stages||{}).find(([,record])=>record?.status==="running")?.[0];
  if(job.progress?.phase==="foundation_research")return "Fehlende Erklärgrundlagen werden automatisch recherchiert";
  if(job.action==="replan")return actionNames.replan;
  if(job.action==="plan"||(run?.kind==="script"&&stage==="planning"))return job.progress?.plan_repair?"Inhaltsverzeichnis wird korrigiert":actionNames.plan;
  if(run?.kind==="script")return job.action==="revise"?actionNames.revise:"Ausarbeitung läuft";
  if(run?.kind==="research"||job.action==="research")return actionNames.research;
  if(run?.kind==="episode_audio")return actionNames.audio;
  return actionNames[job.action]||"Auftrag läuft";
}
function offlineMark() {
  return connectionLost?`<span class="offline-mark" role="status">Keine Verbindung · Stand ${escape(new Date(lastSyncAt).toLocaleTimeString("de-DE",{hour:"2-digit",minute:"2-digit"}))}</span>`:"";
}
// Each <details> of a panel under a stable key: its data attributes or class, numbered when several share one.
function detailKeys(el) {
  const seen=new Map();
  return Array.from(el.querySelectorAll?.("details")||[],detail=>{
    const base=Object.entries(detail.dataset||{}).map(([k,v])=>`${k}=${v}`).join("&")||detail.className||"details";
    const n=(seen.get(base)||0)+1;seen.set(base,n);
    return [`${base}#${n}`,detail];
  });
}
// Each classed element of a panel under its class, numbered like detailKeys; used to keep scroll positions.
function scrollKeys(el) {
  const seen=new Map();
  return Array.from(el.querySelectorAll?.("[class]")||[],node=>{
    const base=String(node.className||"");
    const n=(seen.get(base)||0)+1;seen.set(base,n);
    return [`${base}#${n}`,node];
  });
}
// A redraw keeps typed input, every section the reader opened or closed and where each scrolling area stood.
function replaceKeeping(el, html) {
  const values=new Map(Array.from(el.querySelectorAll?.("input[id]:not([type=checkbox]),textarea[id]")||[],field=>[field.id,field.value]));
  const states=new Map(detailKeys(el).map(([key,detail])=>[key,detail.open]));
  const scrolls=new Map(scrollKeys(el).filter(([,node])=>node.scrollTop>0).map(([key,node])=>[key,node.scrollTop]));
  el.innerHTML=html;
  for(const [key,detail] of detailKeys(el))if(states.has(key))detail.open=states.get(key);
  for(const [key,node] of scrollKeys(el))if(scrolls.has(key))node.scrollTop=scrolls.get(key);
  for(const [id,value] of values){const field=value?document.getElementById(id):null;if(field)field.value=value;}
}
// Redraw a container only when its own markup changed. The browser adds open="" to an opened <details>,
// so comparing with innerHTML would redraw, and close, it on every poll.
const drawnMarkup=new WeakMap();
function redraw(el, html) {
  if(!el||drawnMarkup.get(el)===html)return;
  drawnMarkup.set(el,html);replaceKeeping(el,html);
}
// One line in the topbar carries the state and the stop, resume or next-step action. Telemetry goes to the docked drawer.
// Page panels (stop card, production, research, audio jobs) are filled first because they belong to their step, not to the drawer.
function renderJob() {
  const j=project?.job, box=$("job-status"), bar=$("job-bar");
  const clear=()=>{box.hidden=true;box.innerHTML="";bar.hidden=true;bar.innerHTML="";lastJobView="";dock(false);};
  if(overviewPage||!project){clear();return;}
  const details=step===PAGE.production?$("production-progress"):null;
  if(details) {
    const opened=new Set(Array.from(details.querySelectorAll?.("details[open][data-progress-episode]")||[],el=>el.dataset.progressEpisode));
    const scrolls=new Map(Array.from(details.querySelectorAll?.("[data-progress-preview]")||[],el=>[el.dataset.progressPreview,el.scrollTop]));
    details.innerHTML=renderProductionDetails();
    for(const detail of details.querySelectorAll?.("[data-progress-episode]")||[])detail.open=opened.has(detail.dataset.progressEpisode);
    for(const preview of details.querySelectorAll?.("[data-progress-preview]")||[])preview.scrollTop=scrolls.get(preview.dataset.progressPreview)||0;
  }
  const audioPanel=step===PAGE.audio?$("audio-jobs"):null;
  if(audioPanel){const cards=renderAudioJobs();replaceKeeping(audioPanel,cards?`<section class="panel audio-jobs"><h2>Vertonungsaufträge</h2>${cards}</section>`:"");}
  // A run recorded without a Studio job (an older project) still shows its stop and its resume.
  const job=j||(project?.run&&project.run.status!=="completed"?{status:project.run.status,run:project.run}:null);
  const info=stopInfo(job), owner=j?jobPage():job?runPage(job.run):null;
  const stopBox=$("stop-card");
  if(stopBox){
    // The stop card stands on the page that owns the job; audio cards and the chat carry their own.
    const own=owner===step&&job?.run?.kind!=="episode_audio"&&!["audio","assistant"].includes(job?.action);
    const html=!own?"":info&&!info.card?renderStopCard(job,info):job?.status==="running"?heartbeatNote(job):"";
    if(html!==stopHtml||(html&&stopBox.innerHTML===""))replaceKeeping(stopBox,html);
    stopHtml=html;
  }
  if(project.audio_jobs?.some(a=>a.id===j?.id)){
    box.hidden=false;bar.hidden=false;dock(true);
    const view=JSON.stringify({audio_jobs:project.audio_jobs,capacity:project.audio_capacity,submitting,drawerOpen,step,connectionLost});
    if(view===lastJobView)return;
    lastJobView=view;
    bar.innerHTML=offlineMark()+renderAudioJobBar();
    replaceKeeping(box,drawerMarkup(audioJobSummary(),step===PAGE.audio?'<p class="hint">Fortschritt, Anhalten und Fortsetzen je Folge stehen auf der Seite Vertonung.</p>':renderAudioJobs()));
    return;
  }
  const research=$("research-progress");
  if(!job){clear();if(research)research.innerHTML="";return;}
  box.hidden=false;bar.hidden=false;dock(true);
  const r=job.run, active=job.status==="running", state=job.status;
  const researchOpen=research?.querySelector?.(".research-quality")?.open;
  const questionOpen=new Set(Array.from(research?.querySelectorAll?.("[data-research-question][open]")||[],el=>el.dataset.researchQuestion));
  const previousTrace=box.querySelector?.(".trace-lines");
  const traceAtEnd=!previousTrace||previousTrace.scrollHeight-previousTrace.scrollTop-previousTrace.clientHeight<32;
  const traceScroll=previousTrace?.scrollTop||0;
  const isScript=r?.kind==="script"||job.progress?.phase==="script";
  const audioRunning=(project.audio_jobs||[]).filter(a=>a.status==="running");
  // The server stamps each answer with its read time and the worker's heartbeat age; those alone are no change to redraw.
  const steady={...job,heartbeat_age_seconds:undefined,progress:job.progress&&{...job.progress,updated_at:undefined}};
  const view=JSON.stringify({project:project?.id,job:steady,drawerOpen,connectionLost,audio:audioRunning.map(a=>[a.id,a.progress?.completed_segments]),
    progressClock:active&&["script","research"].includes(job.progress?.phase)?Math.floor(Date.now()/10000):null,
    page:job.sample?null:step,minute:active?Math.floor((Date.now()-Date.parse(job.started_at))/60000):null});
  // A freshly rendered research page has an empty ledger container even when the job itself is unchanged.
  const researchStale=!!research&&research.innerHTML===""&&job.progress?.phase==="research";
  if(view===lastJobView&&!researchStale)return;
  lastJobView=view;
  const planReview=!active&&!!job.progress?.plan_review?.awaiting;
  const planPending=planReview&&!job.progress.plan_review.approved;
  const researchBlocked=!active&&job.progress?.research_questions?.phase==="blocked";
  // Blocked questions that never searched the web get that search on resume; only then is a block a gap to accept.
  const reopenable=researchBlocked&&Number(job.progress?.research_questions?.reopenable||0)>0;
  const openBlocked=(job.progress?.research_questions?.questions||[]).filter(q=>q.status==="blocked"&&!q.accepted_gap&&!q.retry_requested).length;
  const decisionNeeded=researchBlocked&&!reopenable&&openBlocked>0;
  const resumable=!active&&canResume(job,info);
  const title=active?runningTitle(job):decisionNeeded?`${openBlocked} ${openBlocked===1?"Teilfrage wartet":"Teilfragen warten"} auf deine Entscheidung`:
    planReview?(planPending?"Wartet auf Freigabe des Rechercheplans":"Rechercheplan freigegeben – bereit zum Fortsetzen"):
    info?info.title:state==="completed"?"Arbeitsschritt abgeschlossen":"Gespeicherter Auftrag";
  const destination=state==="completed"?runPage(r):owner;
  const links=["Auftrag ansehen","Recherche ansehen","Inhaltsverzeichnis ansehen","Ausarbeitung ansehen","Skripte lesen","Audio ansehen"];
  const tone=active?"running":info?(info.kind==="decision"?"decision":"blocked"):"done";
  const calls=Number.isSafeInteger(job.progress?.model_call_limit)&&job.progress.model_call_limit>0?` · Aufrufe ${Number(job.progress.model_calls||0)} von ${job.progress.model_call_limit}`:"";
  const meta=active&&job.started_at?` · seit ${elapsedText(job.started_at)}${calls}`:"";
  const lastLine=active?(job.progress?.model_trace?.lines||[]).at(-1):null;
  const audioNote=audioRunning.length?(step!==PAGE.audio?`<button class="quiet small status-link" data-step="${PAGE.audio}">${audioJobSummary()} →</button>`:`<span class="hint">${audioJobSummary()}</span>`):"";
  bar.innerHTML=offlineMark()+`<span class="job-dot ${tone}${connectionLost?" offline":""}" aria-hidden="true"></span><span class="job-text"><strong>${escape(title)}</strong>${meta}</span>`+
    (active?'<button class="danger small" data-action="stop" data-confirm="Den laufenden Auftrag anhalten? Fertige Schritte bleiben gespeichert; der gerade laufende Modellaufruf wird beim Fortsetzen wiederholt.">Auftrag anhalten</button>':
      resumable&&running()?`<span class="hint">Fortsetzen, sobald ${audioRunning.length?"die Vertonung":"der laufende Auftrag"} fertig ist</span>`:
      resumable?`<button class="secondary small" data-action="resume" data-run-id="${escape(r?.run_id||"")}">Fortsetzen</button>`:"")+
    (destination!==null&&destination!==undefined&&destination!==step?`<button class="secondary small status-link" data-step="${destination}">${links[destination]} →</button>`:"")+audioNote+drawerToggle();
  if(research)replaceKeeping(research,job.progress?.phase==="research"?renderResearchPanel(job,r,active,questionOpen,researchOpen,researchBlocked,reopenable,resumable):"");
  const live=$("research-live");
  if(live)live.innerHTML=lastLine?.text?`<section class="panel live-panel" aria-live="polite"><div class="panel-title"><h2>Live</h2><span class="hint">${lastLine.at?`vor ${progressAge(lastLine.at)}`:""}</span></div><p class="live-text">${escape(lastLine.text)}</p><button class="quiet small" data-action="drawer-toggle">Alle Meldungen im Maschinenraum</button></section>`:"";
  // The drawer holds telemetry only; what happened and what to do stand on the step's page.
  let body=`<div class="model-observability">${renderModelTrace(job)}</div>`+renderStatusSummary(job.progress?.status_summary);
  if(active)body+=`<p>Gesamte Laufzeit seit Start/Fortsetzung: ${Math.max(0,Math.floor((Date.now()-Date.parse(job.started_at))/60000))} Min. · Fertige Schritte werden gespeichert.</p>`;
  if(r&&!isScript&&!job.progress?.research_questions)body+=`<div class="stage-strip">${Object.entries(r.stages||{}).map(([name,v])=>`<span class="${escape(v.status)}">${v.status==="completed"?"✓ ":""}${stageNames[name]||escape(name)}</span>`).join("")}</div>`;
  if(job.progress&&!["script","research"].includes(job.progress.phase)){
    if(job.action==="audio_samples"&&job.progress.total_segments!==undefined)body+=`<p>${Number(job.progress.completed_segments)} von ${Number(job.progress.total_segments)} Hörproben fertig</p><progress value="${Number(job.progress.completed_segments)}" max="${Number(job.progress.total_segments)}" aria-label="Fortschritt"></progress>`;
    else if(active)body+=audioPhase(job.progress);
  }
  if(job.checks)body+=renderChecks(job.checks);
  if(isScript&&job.progress?.current_episode&&(job.progress.active_episodes||[]).length<2)body+=`<p>Folge ${Number(job.progress.episode_number)} von ${Number(job.progress.total_segments)} · ${escape(job.progress.activity)}</p>`;
  if(job.sample)body+=`<p>Hörprobe: ${escape(job.sample.voice)} · ${escape(job.sample.language)}</p><audio controls preload="none" src="${mediaUrl(job.sample.audio)}"></audio><div class="actions"><a href="${mediaUrl(job.sample.audio)}" target="_blank" rel="noopener">Hörprobe separat öffnen</a><a href="${mediaUrl(job.sample.audio)}" download>MP3 herunterladen</a></div>`;
  if(job.action==="audio_samples"&&job.progress?.current_voice&&active)body+=`<p>Aktuelle Stimme: ${escape(job.progress.current_voice)}</p>`;
  if(Number.isSafeInteger(job.progress?.model_call_limit)&&job.progress.model_call_limit>0){
    const projection=job.progress.budget_projection;
    const outlook=Number.isSafeInteger(projection?.minimum_remaining_calls)?` · mindestens ${projection.minimum_remaining_calls} weitere nötig${projection.feasible===false?" – Limit reicht nicht":""}`:"";
    body+=`<p class="hint">Modellaufrufe: ${Number(job.progress.model_calls||0)} von ${job.progress.model_call_limit}${escape(outlook)}</p>`;
  }
  body+=renderProgressTiming(job.progress,active);
  body+=renderRunTextChoice(job);
  if(job.progress?.execution?.text==="parallel"){
    const activeEpisodes=job.progress.active_episodes||[];
    body+=`<p class="hint">Textmodus: Parallel · bis zu 5 Folgen je Skript-, Polishing- oder Prüfstufe.${activeEpisodes.length?` In Bearbeitung: ${activeEpisodes.map(id=>escape(job.progress.episodes?.find(e=>e.episode_id===id)?.title||id)).join(", ")}.`:""}</p>`;
    if(job.progress.stage==="teaching")body+=`<p class="hint">Die Lehrkonzepte werden nacheinander ausgearbeitet, damit spätere Folgen auf den Erklärungen und Beispielen der früheren aufbauen können. Sobald alle Lehrkonzepte fertig sind, beginnt die parallele Skripterstellung.</p>`;
  }
  if(info)body+=`<p class="hint">Haltegrund: ${escape(info.title)}${info.code?` · Code ${escape(info.code)}`:""}. Was zu tun ist, steht auf der Seite ${escape(steps[owner??PAGE.brief])}.</p>`;
  replaceKeeping(box,drawerMarkup(escape(title)+(lastLine?.text?` · Live: ${escape(lastLine.text)}`:info?.message?` · ${escape(info.message)}`:""),body));
  const traceList=box.querySelector?.(".trace-lines");
  if(traceList)traceList.scrollTop=traceAtEnd?traceList.scrollHeight:traceScroll;
}
// Unfinished input survives a re-render of the same project; a project switch starts clean.
const FORM_IDS=["chat-message","outline-feedback","script-feedback","listening-note","style-notes","spoken-forms","pause-same","pause-change","pause-chapter","host-name-a","host-name-b","plan-max-tasks","api-key","audio-key","stop-key","stop-feedback"];
function formSnapshot() {
  const values={};
  for(const id of FORM_IDS){const el=$(id);if(el&&typeof el.value==="string"&&el.value!=="")values[id]=el.value;}
  const done=$("listening-done");
  return {projectId:project?.id||null,values,listening:!!done?.checked};
}
function formRestore(saved) {
  if(!saved||saved.projectId!==(project?.id||null))return;
  for(const [id,value] of Object.entries(saved.values)){const el=$(id);if(el)el.value=value;}
  const done=$("listening-done");
  if(saved.listening&&done)done.checked=true;
}
function render() {
  const saved=formSnapshot();
  renderNavigation();
  $("content").innerHTML=overviewPage?renderOverview():[renderBrief,renderResearch,renderOutline,renderProduction,renderScript,renderAudio][step]();
  renderJob(); formRestore(saved); syncPlayButtons();
  if(!overviewPage&&window.matchMedia?.("(max-width: 720px)")?.matches)$("steps").querySelector?.('[aria-current="page"]')?.scrollIntoView?.({inline:"center",block:"nearest"});
}
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
  pendingAttachments=[];drawerOpen=false;
  project=selected;
  $("project-select").value=id||"";
  episodeIndex=0;scriptEpisodeId=null;readingSnapshot=null;followWorkflow=requestedPage===null;
  step=requestedPage===null?recommendedPage():requestedPage;
  lastJobSignature=projectJobSignature(project);updatePageUrl();render();
  if(step===PAGE.brief&&(project?.chat||[]).length)scrollChatToEnd();
}
async function storeKey(fieldId="api-key") {
  const key=$(fieldId)?.value.trim();
  if(key){const value=await api("/api/key",{key});boot.key_available=value.key_available;$(fieldId).value="";
    if($("key-status"))$("key-status").textContent=boot.key_available?"Ein Key ist für diese Sitzung verfügbar.":"Noch kein Key hinterlegt.";}
  return !!key;
}
// The ZIP is built before its first byte; fetching it shows that wait and turns a refusal into a readable message.
async function downloadZip(url) {
  notice("Das ZIP wird zusammengestellt … Bei vielen Folgen kann das einen Moment dauern.","ok");
  let response;
  try { response=await fetch(url); }
  catch { throw new Error("Das Studio ist nicht erreichbar. Läuft das Studio-Fenster noch?"); }
  if(!response.ok){const result=await response.json().catch(()=>({}));throw new Error(result.error||"Der Download ist fehlgeschlagen.");}
  const disposition=response.headers.get("Content-Disposition")||"";
  const encoded=/filename\*=UTF-8''([^;]+)/i.exec(disposition)?.[1];
  const name=(encoded?decodeURIComponent(encoded):/filename="([^"]+)"/i.exec(disposition)?.[1])||"podcast.zip";
  const href=URL.createObjectURL(await response.blob()), link=document.createElement("a");
  link.href=href;link.download=name;document.body.appendChild(link);link.click();link.remove();
  setTimeout(()=>URL.revokeObjectURL(href),60000);
  notice("Das ZIP ist fertig und wird gespeichert.","ok");
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
    const field=$("chat-message");
    if(field)field.value="";
  }finally{setupSending=false;refreshAttachmentComposer();}
}
async function applySetupProposal() {
  if(proposalChangesBrief()&&!confirmPaused(["config"]))return;
  await api(`/api/projects/${project.id}/apply_proposal`,{proposal_hash:project.proposal_hash,
    config_hash:project.config_hash,audio_hash:project.audio_hash,execution_hash:project.execution_hash});
  project=await api(`/api/projects/${project.id}`);await refreshProjects();render();notice("Deine Auswahl ist gespeichert.","ok");
}
async function start(action, extra={}) {
  if(!project) throw new Error("Lege zuerst dein Projekt an.");
  const parallelAudio=action==="audio"||(action==="resume"&&extra.episode);
  if(parallelAudio?audioBlockReason(extra.episode):running()) throw new Error(parallelAudio?audioBlockReason(extra.episode):"Ein Auftrag läuft bereits.");
  const id=project.id;
  submitting=true;
  try { await api(`/api/projects/${id}/start`,{action,...extra});project=await api(`/api/projects/${id}`);lastJobSignature=projectJobSignature(project); }
  finally { submitting=false; }
  // Checks and voice samples answer inside the drawer, so it opens for them.
  if(["check","audio_sample","audio_samples"].includes(action))drawerOpen=true;
  navigatePage(recommendedPage(),{automatic:true,push:false});
  if(action==="assistant")scrollChatToEnd();
}
function jobSignature(job) { return job?`${job.id}:${job.status}`:""; }
function projectJobSignature(p) { return [jobSignature(p?.job),jobSignature(p?.main_job),...(p?.audio_jobs||[]).map(jobSignature)].join("|"); }
// Resuming after an approval or a stored key is one click; the resume target travels on the button.
async function resumeFrom(button) {
  await start("resume",{run_id:button.dataset.runId||project.job?.run?.run_id||project.run?.run_id,...(button.dataset.episode?{episode:button.dataset.episode}:{})});
}
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
  const zip=event.target.closest?.("a.download-all");
  if(zip){event.preventDefault();attempt(()=>downloadZip(zip.getAttribute("href")));return;}
  const button=event.target.closest("button");if(!button)return;
  attempt(async()=>{
    // Starting over or stopping costs finished work or a running call; such buttons say so first.
    if(button.dataset.confirm&&typeof window.confirm==="function"&&!window.confirm(button.dataset.confirm))return;
    if(button.dataset.scroll){$(button.dataset.scroll)?.scrollIntoView?.({block:"start"});return;}
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
    if(button.dataset.openProject){await selectProject(button.dataset.openProject,null,button.dataset.openStep!==undefined?Number(button.dataset.openStep):null);return;}
    if(button.dataset.deleteProject){
      const p=overviewData.projects.find(p=>p.id===button.dataset.deleteProject);
      if(p&&window.confirm(`„${p.topic}“ mit Recherche, Skripten und Audio in den lokalen Papierkorb verschieben?`)){
        await api(`/api/projects/${p.id}/delete`,{confirm_id:p.id,config_hash:p.config_hash});
        await refreshProjects();overviewData=await loadOverview();refreshOverview();notice("Projekt im Papierkorb. Du kannst es unten wiederherstellen.","ok");
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
    if(action==="drawer-toggle"){drawerOpen=!drawerOpen;lastJobView="";renderJob();return;}
    if(action==="refresh-script"){readingSnapshot=null;$("content").innerHTML=renderScript();return;}
    if(action==="quit"){
      // Quitting stops every project's jobs, not only the open one's.
      let busy=running()?[project?.config?.topic||"das geöffnete Projekt"]:[];
      if(boot.capabilities?.project_overview){const all=await loadOverview().catch(()=>null);if(all)busy=all.projects.filter(runningOf).map(p=>p.topic);}
      if(busy.length&&typeof window.confirm==="function"&&!window.confirm(`Es laufen noch Aufträge: ${busy.join(", ")}. Studio trotzdem beenden? Die Aufträge werden angehalten und bleiben fortsetzbar.`))return;
      await api("/api/quit",{});project=null;$("job-status").hidden=true;$("job-bar").hidden=true;$("content").innerHTML='<section class="empty"><h1>Bis zum nächsten Gespräch.</h1><p>Das Studio ist beendet. Öffne den Podcast-Studio-Starter in deinem Projektordner, um es wieder zu starten.</p></section>';return;
    }
    if(action==="apply-proposal"){await applySetupProposal();return;}
    if(action==="store-key"){
      if(!await storeKey(button.dataset.keyField||"api-key"))throw new Error("Bitte zuerst den OpenRouter-Key eingeben.");
      if(button.dataset.thenResume){await resumeFrom(button);return;}
      lastJobView="";stopHtml="";refreshAudioPanel();renderJob();notice("Key im Sitzungsspeicher hinterlegt.","ok");return;
    }
    if(action==="resend-chat"){
      const last=[...(project.chat||[])].reverse().find(m=>m.role==="user");
      if(!last)throw new Error("Es gibt keine unbeantwortete Nachricht.");
      await sendSetupMessage(last.message);return;
    }
    if(action==="approve-chat"){
      await api(`/api/projects/${project.id}/approve`,{kind:"chat_calls",model_calls:Number(button.dataset.modelCalls)});
      project=await api(`/api/projects/${project.id}`);render();notice("Gesprächslimit erhöht. „Erneut senden“ schickt deine letzte Nachricht noch einmal.","ok");return;
    }
    if(action==="forget-key"){await api("/api/key",{key:""});await refreshProjects();$("api-key").value="";$("key-status").textContent=boot.key_available?"Key aus der Server-Umgebung verfügbar.":"Sitzungs-Key entfernt.";return;}
    if(action==="stop"){await api(`/api/projects/${project.id}/stop`,{job_id:button.dataset.jobId});project=await api(`/api/projects/${project.id}`);render();return;}
    if(action==="retry-task"){
      const hint=$("retry-hint-"+button.dataset.taskId)?.value||"";
      await api(`/api/projects/${project.id}/approve`,{kind:"retry",run_id:button.dataset.runId,task_id:button.dataset.taskId,hint});
      project=await api(`/api/projects/${project.id}`);lastJobView="";render();notice("Neuer Versuch angefordert. „Fortsetzen“ startet ihn mit dem Spielraum einer neuen Frage.","ok");return;
    }
    if(action==="accept-gap"){
      const reason=$("gap-reason-"+button.dataset.taskId)?.value||"";
      await api(`/api/projects/${project.id}/approve`,{kind:"gap",run_id:button.dataset.runId,task_id:button.dataset.taskId,reason});
      project=await api(`/api/projects/${project.id}`);lastJobView="";render();notice("Lücke akzeptiert. „Fortsetzen“ schließt das Dossier ohne diese Teilfrage ab.","ok");return;
    }
    if(action==="approve-plan"){
      const payload=planApprovalRequest(button.dataset.runId);
      await api(`/api/projects/${project.id}/approve`,payload);
      if(button.dataset.thenResume){await resumeFrom(button);notice(payload.max_tasks?`Obergrenze von ${payload.max_tasks} Teilfragen gespeichert. Der Plan wird neu zugeschnitten und erneut zur Freigabe vorgelegt.`:"Rechercheplan freigegeben. Die Recherche beginnt mit der ersten Teilfrage.","ok");return;}
      project=await api(`/api/projects/${project.id}`);lastJobView="";render();
      notice(payload.max_tasks?`Obergrenze von ${payload.max_tasks} Teilfragen gespeichert. „Fortsetzen“ schneidet den Plan neu zu und legt ihn erneut zur Freigabe vor.`:"Rechercheplan freigegeben. „Fortsetzen“ beginnt mit der ersten Teilfrage.","ok");return;
    }
    if(action==="approve-calls"||action==="approve-search"||action==="approve-sources"){
      const payload={kind:"model_calls",run_id:button.dataset.runId};
      if(action==="approve-calls")payload.model_calls=Number(button.dataset.modelCalls);
      else if(action==="approve-sources")payload.sources=Number(button.dataset.sources);
      else payload.search_rounds=Number(button.dataset.searchRounds);
      await api(`/api/projects/${project.id}/approve`,payload);
      if(button.dataset.thenResume&&!running()){await resumeFrom(button);notice("Limit genehmigt. Der Auftrag läuft weiter.","ok");return;}
      project=await api(`/api/projects/${project.id}`);lastJobView="";render();notice("Limit genehmigt. Ein laufender Auftrag übernimmt es beim nächsten Aufruf, ein angehaltener mit „Fortsetzen“.","ok");return;
    }
    if(action==="save-speech"){await saveSpeechSettings();return;}
    if(action==="save-notes"){
      if($("style-notes").value.trim()!==(project.style_notes||"").trim()&&!confirmPaused(["notes"]))return;
      await api(`/api/projects/${project.id}/save`,{config:project.config,config_hash:project.config_hash,
        text:project.text,style_notes:$("style-notes").value,style_notes_hash:project.style_notes_hash});
      project=await api(`/api/projects/${project.id}`);render();
      notice("Notizen gespeichert. Sie gelten ab dem nächsten Skriptlauf.","ok");return;
    }
    if(action==="listening-review"){
      const e=project.episodes[episodeIndex];
      await api(`/api/projects/${project.id}/listening_review`,{episode:e.script.episode_id,
        reviewed:$("listening-done").checked,note:$("listening-note").value});
      project=await api(`/api/projects/${project.id}`);render();notice("Hörprüfung eingetragen.","ok");return;
    }
    if(action==="spoken-override"){await saveSpokenOverride(button.dataset.episode,button.dataset.segment);return;}
    if(action==="audio"&&button.dataset.rerender){await rerenderEpisode(button.dataset.episode);return;}
    const extra={};
    if(action==="audio_samples"){
      extra.language=setupSelection().config.language;extra.approve_samples=true;
      await storeKey();
    }
    if(action==="replan")extra.message=$(button.dataset.feedback||"outline-feedback")?.value||"";
    if(action==="script")extra.plan_hash=project.outline.hash;
    if(action==="revise"){extra.message=$("script-feedback").value;extra.episode=project.episodes[episodeIndex].script.episode_id;}
    if(action==="resume"){extra.run_id=button.dataset.runId||project.job?.run?.run_id||project.run?.run_id;if(button.dataset.episode)extra.episode=button.dataset.episode;}
    if(action==="audio")Object.assign(extra,audioRequest(button.dataset.episode));
    await start(action,extra);
    // A sent request leaves no stale draft behind; unsent drafts survive re-renders elsewhere.
    if(action==="replan"&&$(button.dataset.feedback||"outline-feedback"))$(button.dataset.feedback||"outline-feedback").value="";
    if(action==="revise"&&$("script-feedback"))$("script-feedback").value="";
  });
});
async function poll() {
  try {
    if(overviewPage){const next=await loadOverview();markSynced();if(overviewPage){overviewData=next;refreshOverview();renderNavigation();}return;}
    if(!project||submitting||setupSending||readingAttachments)return;
    const id=project.id,next=await api(`/api/projects/${id}`);
    if(project?.id!==id)return;
    markSynced();
    const changed=projectJobSignature(next)!==lastJobSignature;
    // A chat, a check or a voice sample answers where it was started; its end does not move the page.
    const auxiliary=["assistant","check","audio_sample","audio_samples"].includes((next.main_job??next.job)?.action);
    const destination=followWorkflow&&!auxiliary?recommendedPage(next):step;
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
    project.episodes=next.episodes;project.script_previews=next.script_previews;
    project.main_job=next.main_job;project.chat_budget=next.chat_budget;renderNavigation();renderJob();
    if(samplesChanged)refreshVoiceLibrary();
    if(changed||destination!==step){lastJobSignature=projectJobSignature(next);project=next;
      const finishedResult=next.job?.status==="completed"&&!!(next.job.sample||next.job.checks);
      if(finishedResult)drawerOpen=true;
      if(followWorkflow){step=destination;updatePageUrl();}
      // A playing episode keeps its player: the audio page refreshes its parts instead of rebuilding.
      const playing=[...document.querySelectorAll("audio")].some(audio=>!audio.paused);
      if(readerOpen&&step===PAGE.scripts){renderNavigation();renderJob();refreshScriptReader();}
      else if(playing&&step===PAGE.audio){renderNavigation();lastJobView="";renderJob();refreshAudioPanel();refreshRecordings();}
      else render();
      if(finishedResult)$("job-status").scrollIntoView({block:"nearest"});
    }else if(scriptsChanged&&step===PAGE.scripts)refreshScriptReader();
    else if((chatChanged||attachmentsChanged)&&step===PAGE.brief){refreshAttachmentComposer();if(chatChanged)scrollChatToEnd();}
  }catch(error){
    // Only a missing answer is a lost connection; a refusal of the running server says what it refused.
    if(error.network){connectionLost=true;lastJobView="";renderJob();notice("Verbindung zum Studio unterbrochen. Ist das Studio-Fenster noch geöffnet? Angezeigt ist der letzte bekannte Stand.","error");}
    else notice(`Das Studio meldet: ${error.message}`,"error");
  }
}
function markSynced() {
  lastSyncAt=Date.now();
  if(connectionLost){connectionLost=false;lastJobView="";notice("");}
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
    if(project?.id===id){const target=index<0?recommendedPage():index;if(target===step&&!overviewPage)return;navigatePage(target,{push:false});}
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
