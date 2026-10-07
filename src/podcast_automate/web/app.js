"use strict";
const $ = id => document.getElementById(id);
const escape = value => String(value ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
// The interface language (D-152). /locale.js defines STUDIO_LOCALE before this script: the saved choice, the language
// and its catalog with English under it. Catalog values are plain text without markup or double quotes (the browser
// suite checks it), so t() leaves them as they are and escapes only its parameters; tp() is the plain text for
// textContent, notices and confirm dialogs. A key no catalog has shows as itself, with one warning.
const LOCALE = (typeof STUDIO_LOCALE==="object"&&STUDIO_LOCALE)||{setting:"auto",language:"de",catalog:{}};
const LANG = LOCALE.language==="en"?"en":"de";
const INTL = LANG==="en"?"en-US":"de-DE";
const PLURALS = new Intl.PluralRules(INTL);
const missingTexts = new Set();
class Markup { constructor(value) { this.value=String(value); } }
// A parameter that already is markup, inserted by t() as it is.
const asHtml = value => new Markup(value);
const hasText = key => Object.prototype.hasOwnProperty.call(LOCALE.catalog||{}, key);
function catalogText(key, params) {
  let value=(LOCALE.catalog||{})[key];
  if(value===undefined){
    if(!missingTexts.has(key)){missingTexts.add(key);console.warn(`Missing Studio text: ${key}`);}
    return key;
  }
  if(value&&typeof value==="object")value=value[PLURALS.select(Number(params?.count))]??value.other;
  return String(value);
}
const fill = (text, params, each) => text.replace(/\{(\w+)\}/g,(match,name)=>Object.prototype.hasOwnProperty.call(params,name)?each(params[name]):match);
function tp(key, params={}) { return fill(catalogText(key,params),params,value=>value instanceof Markup?value.value:String(value??"")); }
function t(key, params={}) { return fill(catalogText(key,params),params,value=>value instanceof Markup?value.value:escape(value)); }
// A label the server sends (a preset, a style, a report stage). The server's labels are German, so a German page shows
// them as they come; another language takes the catalog's by its id and falls back to the server's.
const serverLabel = (key, fallback) => (LANG==="de"&&fallback)||!hasText(key)?String(fallback??""):tp(key);
// A label in quotation marks: „…“ in German, “…” in English.
const quoted = text => LANG==="en"?`“${text}”`:`„${text}“`;
const fmt = {
  number:(value,options)=>Number(value).toLocaleString(INTL,options),
  dateTime:(value,options)=>new Date(value).toLocaleString(INTL,options),
  date:(value,options)=>new Date(value).toLocaleDateString(INTL,options),
  time:(value,options)=>new Date(value).toLocaleTimeString(INTL,options),
  list:(items,type="conjunction")=>new Intl.ListFormat(INTL,{style:"long",type}).format(items.map(String)),
};
// Voices, text model and execution are set on the settings page since 2026-10-03; the first step is the brief alone.
const pageKeys = ["brief", "research", "outline", "production", "scripts", "audio"];
const steps = pageKeys.map(key=>tp(`nav.step.${key}`));
const PAGE = Object.fromEntries(pageKeys.map((key,index)=>[key,index]));
// The editorial framing of a step introduces a page that has nothing to show yet. A working page
// opens with its name, its state and its action instead.
const pageIntros = Object.fromEntries(pageKeys.map(key=>[key,[tp(`page.intro.${key}.title`),tp(`page.intro.${key}.text`)]]));
const productionStages = ["teaching","writing","polishing","review","publish"].map(id=>[id,tp(`production.stage.${id}.name`),tp(`production.stage.${id}.text`)]);
const stageNames = Object.fromEntries(["discovery","retrieval","dossier","completeness","planning","teaching","writing","polishing",
  "review","publish","expression","synthesis","assembly"].map(id=>[id,tp(`stage.${id}`)]));
const actionNames = Object.fromEntries(["assistant","research","plan","replan","expression","publish_kit","script","revise","audio",
  "audio_sample","audio_samples","resume","check"].map(id=>[id,tp(`action.${id}`)]));
let boot, project = null, step = 0, episodeIndex = 0, submitting = false, lastJobSignature = "";
let lastJobView = "";
let playingSample = null;
let followWorkflow = true;
let scriptEpisodeId=null, readingSnapshot=null;
let overviewPage=false, overviewData={projects:[],trash:[]};
// The settings page (studio_settings): what holds for every project, edited as a draft and saved in one go.
let settingsPage=false, settingsData=null, settingsDraft=null, pairLanguage="de-DE";
let navigationEpoch=0;
let setupSending=false;
let pendingAttachments=[], readingAttachments=false;
let drawerOpen=false, connectionLost=false, lastSyncAt=Date.now(), stopHtml="";
// Unsaved edits on the settings page, and whether a settled brief shows its conversation (D-161).
let settingsDirty=false, briefChatOpen=false;
// The trial option of the new-project page (D-157): ticked by the editor only, never preset.
let trialChecked=false;
// What the editor read and heard (Studio.reader_state, D-163): kept in the project, never in browser storage, so it
// follows from the computer to the phone. Saving a playback position fails quietly; a read mark says when it fails.
async function saveReaderState(data, projectId=project?.id) {
  if(!projectId)return null;
  const result=await api(`/api/projects/${encodeURIComponent(projectId)}/reader_state`,data);
  if(project?.id===projectId)project.reader_state=result.reader_state;
  if(playlist?.projectId===projectId)playlist.state=result.reader_state;
  return result.reader_state;
}
// One status for the overview card, the step navigation and the header (D-160): what is recorded, what is older than
// the current script, voices and audio settings, and which published episodes have no recording yet. Works on a card
// (episodes with episode_id) and on the project page (episodes with script).
function audioFacts(p) {
  const rows=p?.episodes||[], voiced=rows.filter(e=>e.audio?.length), outdated=voiced.filter(e=>!e.audio_current);
  const total=Math.max(Number(p?.script_count??rows.length)||0,rows.length);
  return {total,voiced:voiced.length,outdated:outdated.length,unvoiced:Math.max(0,total-voiced.length),
    reasons:[...new Set(outdated.flatMap(e=>e.audio_stale||[]))]};
}
const STALE_LABELS=Object.fromEntries(["script","voices","provider","model","pauses","pace","styles","alternate_roles","audio"]
  .map(id=>[id,tp(`stale.${id}`)]));
const staleText=reasons=>(reasons||[]).map(r=>STALE_LABELS[r]||r).join(", ");
const minutesText=seconds=>{const m=Math.round(Number(seconds)/60);return m>=60?tp("time.hours_minutes",{hours:Math.floor(m/60),minutes:m%60}):tp("time.minutes",{minutes:m});};
const scriptStateLabels=Object.fromEntries(["draft","polished","reviewed","published"].map(id=>[id,tp(`script_state.${id}`)]));
const voiceSamples = () => project?.voice_samples || boot.voice_samples || {};
const savedSample = (voice, language) => voiceSamples()[language]?.[voice];
// Gemini speaks through Google's own API (two hosts per request, the Google key) or through OpenRouter (each segment on
// its own, the OpenRouter key); both use the same thirty voices and the same voice library.
const isGemini = provider => provider === "google_gemini_tts" || provider === "openrouter_gemini_tts";
// The voices an episode is spoken in: swapped in an even-numbered one when the roles alternate (AudioChoice.for_episode).
const episodeVoices = (a, id) => {
  const number=/^ep_0*(\d+)$/.exec(id||"");
  return a.alternate_roles&&number&&Number(number[1])%2===0?{host_a:a.voices.host_b,host_b:a.voices.host_a}:a.voices;
};
const keyOf = provider => provider === "google_gemini_tts" ? "google" : "openrouter";
const KEY_NAMES = Object.fromEntries(["openrouter","google","anthropic","perplexity"].map(key=>[key,tp(`key.name.${key}`)]));
// The field of the bootstrap and the settings view that says whether a key is there.
const KEY_FLAGS = {openrouter:"key_available", google:"google_key_available", anthropic:"anthropic_key_available",
  perplexity:"perplexity_key_available"};
const keyAvailable = (key="openrouter") => boot[KEY_FLAGS[key]||"key_available"] !== false;
const KEY_STATUS = {openrouter:"key-status", google:"google-key-status", anthropic:"anthropic-key-status",
  perplexity:"perplexity-key-status"};
const KEY_FIELDS = {openrouter:"api-key", google:"google-key", anthropic:"anthropic-key", perplexity:"perplexity-key"};
const copyKeyFlags = (from, to) => { for (const flag of Object.values(KEY_FLAGS)) if (from[flag] !== undefined) to[flag] = from[flag]; };
// Money: a run billed to a key (Claude on the Anthropic key, OpenRouter) has a limit in USD (D-146).
const usd = value => `${fmt.number(value,{minimumFractionDigits:2,maximumFractionDigits:2})} USD`;
const billedProvider = provider => (boot?.text_catalog?.billed_providers||["openrouter","claude_api"]).includes(provider);
// The keys a restarted server lost (it keeps them in memory only), as the notice names them; empty when none.
const lostKeys = fresh => [boot.key_available&&!fresh.key_available?"openrouter":"",
  boot.google_key_available&&fresh.google_key_available===false?"google":"",
  boot.anthropic_key_available&&fresh.anthropic_key_available===false?"anthropic":"",
  boot.perplexity_key_available&&fresh.perplexity_key_available===false?"perplexity":""].filter(Boolean);
// The lost keys by name, joined as the notices say them („OpenRouter-Key und der Google-Key“).
const keyList = keys => keys.map(key=>KEY_NAMES[key]).join(tp("act.key_list_join"));
const sampleButtonLabel = (provider, voice, language) =>
  isGemini(provider) && !savedSample(voice, language)
    ? tp("sample.make_api")
    : tp("sample.listen");
const currentAudio = () => project?.audio_settings || {provider:"qwen3_local",voices:(project?.config||boot.defaults).voice_profile};
const audioCatalog = () => boot.audio_catalog || {qwen3_local:{label:tp("audio_provider.qwen3_local"),voices:boot.voices,defaults:boot.defaults.voice_profile}};
// The label of an audio route on the settings page: the catalog's by its id, else the server's.
const audioProviderLabel = (id, row) => serverLabel(`audio_provider.${id}`, row?.label||id);
// A Gemini choice saved without a model uses the default one; the label names the model an approval binds.
const audioLabel = a => {
  if(a.provider==="qwen3_local")return tp("audio.qwen_local");
  const gemini=audioCatalog()[a.provider]||audioCatalog().openrouter_gemini_tts;
  return `${gemini?.models?.[a.model||gemini.default_model]||"Gemini"} · ${a.provider==="google_gemini_tts"?"Google":"OpenRouter"}`;
};
const mediaUrl = path => "/media/"+encodeURIComponent(project.id)+"/"+path.split("/").map(encodeURIComponent).join("/");
const running = () => submitting || project?.job?.status === "running" || (project?.audio_jobs||[]).some(j=>j.status==="running");
const disabled = () => running() ? "disabled" : "";
function audioBlockReason(episode=project?.episodes?.[episodeIndex]?.script?.episode_id, queueable=false) {
  if(submitting)return tp("audio.block.starting");
  // Gemini fails at its first request without a key; the approval card offers the key field instead.
  const provider=currentAudio().provider;
  if(isGemini(provider)&&!keyAvailable(keyOf(provider)))return tp(`audio.block.key.${keyOf(provider)}`);
  if(!boot.capabilities?.parallel_audio||!isGemini(provider))
    return running()?tp("audio.block.running"):"";
  const active=(project.audio_jobs||[]).filter(j=>j.status==="running");
  if(active.some(j=>j.episode===episode))return tp("audio.block.episode_busy");
  // A new approval waits in the queue when no place is free; it starts by itself.
  if(queueable)return "";
  if(project.job?.status==="running"&&!active.some(j=>j.id===project.job.id))return tp("audio.block.project_busy");
  if(project.audio_capacity?.available===0)return tp("audio.block.capacity");
  return "";
}
// One message box for outcomes. Errors and confirmations look different; the box sticks below the topbar.
function notice(message, kind="warn") { const box=$("notice"); box.textContent = message; box.hidden = !message; box.className = message?`notice ${kind}`:"notice"; }
// A refusal as the notice shows it. A server message in another language than the page (a pipeline's German in the
// English interface) is named by its code and quoted as the original (D-152); German keeps showing the message.
function errorText(error) {
  if(LANG!=="en"||!error?.language||error.language===LANG)return error?.message||"";
  return tp("error.foreign",{code:error.code||"?",message:error.message||""});
}
// Whether a stop's server message (stop.message_language) is in the page's language. Older servers name none; their
// messages are German. German pages keep showing every message as before.
const sameLanguage = info => LANG!=="en"||(info?.message_language||"de")===LANG;
async function api(path, data, renewed=false) {
  const options = data === undefined ? {} : {method:"POST",headers:{"Content-Type":"application/json","X-Studio-Token":boot.token},body:JSON.stringify(data)};
  let response;
  try { response = await fetch(path, options); }
  catch { const error=new Error(tp("error.unreachable")); error.network=true; throw error; }
  const result = await response.json().catch(()=>({error:tp("error.unreadable"),message_language:LANG}));
  if (!response.ok) {
    // A restarted server has a new session token: renew it once instead of failing every click.
    if(response.status===403&&result.code==="forbidden"&&data!==undefined&&!renewed){
      const fresh=await fetch("/api/bootstrap").then(r=>r.ok?r.json():null).catch(()=>null);
      if(fresh?.token&&fresh.token!==boot.token){
        const lost=lostKeys(fresh);
        boot.token=fresh.token;boot.key_available=fresh.key_available;boot.google_key_available=fresh.google_key_available;
        boot.anthropic_key_available=fresh.anthropic_key_available;boot.perplexity_key_available=fresh.perplexity_key_available;
        const value=await api(path,data,true);
        if(lost.length)notice(tp("notice.keys_lost",{keys:keyList(lost)}));
        return value;
      }
    }
    const error=new Error(result.error || tp("error.failed"));
    error.code=result.code;error.status=response.status;error.language=result.error?result.message_language:LANG;throw error;
  }
  return result;
}
async function attempt(action) { try { notice(""); await action(); } catch(error) { notice(errorText(error),"error"); } }
function textInput(id,label,value,type="text") { return `<div class="field"><label for="${id}">${label}</label><input id="${id}" type="${type}" value="${escape(value)}"></div>`; }
function area(id,label,value,rows=3) { return `<div class="field"><label for="${id}">${label}</label><textarea id="${id}" rows="${rows}">${escape(value)}</textarea></div>`; }
// Compact page head: step number and name, then the step's state as a chip.
function heading(n) {
  const state=project?navigationStates()[n-1]:null;
  return `<header class="page-head"><div class="page-title"><span class="eyebrow">${String(n).padStart(2,"0")} / 06</span><h1>${steps[n-1]}</h1></div>${project?.config?.trial?trialChip():""}${state?`<span class="chip ${state[1]}">${escape(state[0])}</span>`:""}</header>`;
}
// A trial project („Probelauf“, D-157) says so on its card and on every page of it.
const trialChip=()=>`<span class="chip trial">${t("trial.chip")}</span>`;
// What a trial is limited to, from the server's facts (trial.trial_facts in /api/bootstrap); nothing without them.
function trialNote() {
  const f=boot?.trial, values=[f?.sub_questions,f?.target_total_minutes,f?.limits?.cost_usd].map(Number);
  if(!values.every(value=>Number.isFinite(value)&&value>0))return "";
  return t("trial.note",{questions:values[0],minutes:fmt.number(values[1]),cost:fmt.number(values[2])});
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
    revise:PAGE.production,audio:PAGE.audio,expression:PAGE.scripts}[action]??runPage(currentRun(p),p));
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
  const audio=audioFacts(project);
  const readable=readableScripts().length;
  const finished=scriptsFinished();
  // An older recording is no request: it stays playable and the recording page says what changed. Only a published
  // episode without any recording asks for an audio approval (D-160; before, the overview said "Podcast verfügbar"
  // while the steps showed two decisions for the same project).
  const audioRow=audio.unvoiced?[audio.voiced?tp("nav.state.unvoiced",{count:audio.unvoiced}):tp("nav.state.audio_approval"),"decision"]
    :audio.voiced?[audio.outdated?tp("nav.state.recordings_outdated",{count:audio.outdated}):tp("nav.state.recordings"),"done"]:[tp("nav.state.after_review"),"pending"];
  const rows=[
    [project?tp("nav.state.saved"):tp("nav.state.start"),project?"done":"ready"],
    [project?.research?tp("nav.state.dossier"):tp("nav.state.sources"),project?.research?"done":"pending"],
    [approvedOutline()?tp("nav.state.approved"):project?.outline?tp("nav.state.your_approval"):tp("nav.state.after_research"),approvedOutline()?"done":project?.outline?"decision":"pending"],
    [finished?tp("nav.state.finished"):approvedOutline()?tp("nav.state.automatic"):tp("nav.state.after_plan"),finished?"done":"pending"],
    // Reading stays a decision until the first episode is voiced; afterwards it is done work, not a request.
    [readable?tp("nav.state.readable",{count:readable}):tp("nav.state.after_draft"),readable?(audio.voiced?"done":"decision"):"pending"],
    audioRow,
  ];
  const activePage=jobPage()??destination;
  if(activePage!==undefined&&activePage!==null&&(busy||blocked))
    rows[activePage]=busy?[tp("nav.state.running"),"running"]:[STOP_KIND_LABELS[info?.kind]||tp("nav.state.stopped"),stopTone(info)];
  // Parallel Gemini episodes: a stopped one stays visible beside those still being voiced.
  const halted=stoppedAudio().length, voicing=(project?.audio_jobs||[]).some(j=>j.status==="running");
  if(halted)rows[PAGE.audio]=voicing?[tp("nav.state.running_halted",{count:halted}),"running"]:[tp("nav.state.halted",{count:halted}),"blocked"];
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
  if(!Number.isFinite(minutes)||minutes<1)return tp("time.less_than_minute");
  return minutes<60?tp("time.minutes",{minutes}):tp("time.hours_minutes",{hours:Math.floor(minutes/60),minutes:minutes%60});
}
// The stepper carries time where the pipeline knows it: elapsed on the running step, the projection on a waiting research.
function stepTimeHints() {
  const hints=Array(steps.length).fill(""), j=project?.job;
  if(!j)return hints;
  const page=jobPage();
  if(j.status==="running"&&page!==null&&page!==undefined&&j.started_at)hints[page]=` · ${tp("nav.hint.since",{elapsed:elapsedText(j.started_at)})}`;
  // The projection belongs to the plan gate only; a run past its gate has moved on from that estimate.
  const review=j.progress?.plan_review, projection=review?.projection;
  if(j.status!=="running"&&review?.awaiting&&!review.approved&&projection?.projected_hours)hints[PAGE.research]=` · ${tp("nav.hint.projected",{hours:Math.round(Number(projection.projected_hours))})}`;
  return hints;
}
function updatePageUrl(push=false) {
  const url=settingsPage?"/?view=settings":overviewPage?"/":project?`/?project=${encodeURIComponent(project.id)}&step=${pageKeys[step]}`:"/?new=1";
  const history=window.history;
  if(push&&history?.pushState)history.pushState(null,"",url);
  else history?.replaceState(null,"",url);
}
function navigatePage(target,{automatic=false,push=true}={}) {
  if(!Number.isInteger(target)||target<0||target>=steps.length)return;
  navigationEpoch++;
  overviewPage=false;settingsPage=false;step=target;followWorkflow=automatic;updatePageUrl(push);render();
  // A conversation opens at its newest reply, right above the pinned composer.
  if(step===PAGE.brief&&(project?.chat||[]).length)scrollChatToEnd();
}
// A stop's colour: a step that can go on (a retry, a quota wait) is paused, not broken; a decision is the editor's.
const stopTone=info=>info?.kind==="decision"?"decision":["retry","wait"].includes(info?.kind)?"paused":"blocked";
// Outside a project the sidebar lists the projects instead of six steps that belong to none (2026-10-07: the settings
// page showed a new project's "1 · Hier beginnen" as the current step).
function sidebarProjects() {
  const rows=(overviewPage?overviewData.projects:boot?.projects)||[];
  const mark=p=>!overviewPage?"":runningOf(p)?"●":attentionOf(p)?"▲":"";
  return `<p class="sidebar-label">${t("sidebar.projects")}</p>${rows.map(p=>`<button class="step project-link" data-open-project="${escape(p.id)}" title="${escape(p.topic)}"><span class="step-number" aria-hidden="true">${mark(p)}</span><span class="step-label">${escape(shortText(p.topic,70))}</span></button>`).join("")}
    <button class="step project-link" data-new-project><span class="step-number" aria-hidden="true">＋</span><span class="step-label">${t("project.new")}</span></button>`;
}
// The project picker offers "Neues Projekt" only where it means one; on the overview and the settings it asks for a choice.
function syncProjectSelect() {
  const first=$("project-select")?.options?.[0], browsing=overviewPage||settingsPage;
  if(first){first.textContent=browsing?tp("project.choose"):tp("project.new");first.disabled=browsing;}
}
function renderNavigation() {
  syncProjectSelect();
  if(overviewPage||settingsPage){
    $("steps").hidden=false;$("steps").setAttribute?.("aria-label",tp("sidebar.projects"));$("steps").innerHTML=sidebarProjects();
    $("project-title").textContent=settingsPage?tp("settings.title"):tp("overview.title");
    const waiting=overviewPage?overviewData.projects.filter(p=>attentionOf(p)).length:0;
    document.title=settingsPage?`${tp("settings.title")} · Podcast Studio`:waiting?`(${waiting}) Podcast Studio`:"Podcast Studio";return;
  }
  $("steps").hidden=false;$("steps").setAttribute?.("aria-label",tp("nav.steps_label"));
  const states=navigationStates(), hints=stepTimeHints();
  const glyph=state=>state==="done"?"✓":state==="running"?"●":state==="blocked"?"!":state==="paused"?"‖":state==="decision"?"▲":null;
  $("steps").innerHTML = steps.map((name,i)=>`<button class="step ${states[i][1]}" data-step="${i}" ${i===step?'aria-current="page"':""}><span class="step-number" aria-hidden="true">${glyph(states[i][1])??i+1}</span><span class="step-label">${name}<small>${escape(states[i][0])}${hints[i]}</small></span></button>`).join("");
  const title=project?.config?.topic || tp("project.new_title");
  $("project-title").textContent = title;
  document.title=project?`${statusGlyph(project.job)}${title} · Podcast Studio`:"Podcast Studio";
}
const languageName = language => tp(`project_language.${language==="en-US"?"en-US":"de-DE"}`);
function renderVoiceLibrary(language) {
  const voices=audioCatalog().openrouter_gemini_tts?.voices||[], ready=voices.filter(v=>savedSample(v,language)).length;
  return `<div class="panel-title"><h2>${t("voices.library.title")}</h2><span class="tag">${t("voices.library.saved",{ready,total:voices.length})}</span></div><p class="hint">${languageName(language)} · ${t("voices.library.hint")}</p>
    <div class="voice-library">${voices.map(v=>`<div class="sample-row"><strong>${escape(v)}</strong>${savedSample(v,language)?`<button type="button" class="secondary small" data-play-voice="${escape(v)}" data-language="${language}" aria-label="${t("voices.play_label",{voice:v})}">${t("voices.play")}</button>`:`<span class="hint">${t("voices.no_sample")}</span>`}</div>`).join("")}</div>
    ${ready<voices.length?`<button type="button" data-action="audio_samples" ${disabled()}>${t("voices.make_missing",{count:voices.length-ready})}</button><p class="hint">${t("voices.make_missing_hint")}</p>`:`<p class="hint">${t("voices.all_saved")}</p>`}`;
}
function syncPlayButtons() {
  const player=$("sample-player");
  for(const button of document.querySelectorAll("[data-play-voice]")){
    const active=playingSample?.voice===button.dataset.playVoice&&playingSample?.language===button.dataset.language&&!player.paused;
    button.textContent=active?tp("voices.pause"):tp("voices.play");
    button.setAttribute("aria-label",tp(active?"voices.pause_label":"voices.play_label",{voice:button.dataset.playVoice}));
    button.setAttribute("aria-pressed",String(active));
  }
}
function refreshVoiceLibrary() {
  if(step!==PAGE.brief)return;
  const {config,audio}=setupSelection();
  if($("voice-library-panel")&&isGemini(audio.provider))
    $("voice-library-panel").innerHTML=renderVoiceLibrary(config.language);
  syncPlayButtons();
}
async function playSample(voice, language, provider="openrouter_gemini_tts") {
  const url=isGemini(provider)?savedSample(voice,language)?.url:`/samples/${language}/${voice.toLowerCase()}`;
  if(!url)throw new Error(tp("sample.none"));
  const player=$("sample-player");
  if(playingSample?.url===url&&!player.paused){player.pause();syncPlayButtons();return;}
  if(playingSample?.url!==url){player.src=url;playingSample={voice,language,url};}
  if(playlist)closePlayer();
  $("sample-playback").hidden=false;
  $("sample-playing-label").textContent=tp("sample.playing",{voice,language:languageName(language)});
  try{await player.play();syncPlayButtons();}catch{throw new Error(tp("sample.play_failed"));}
}
function defaultTextChoice(provider="codex_cli") {
  return provider==="codex_cli"?{provider,model:"gpt-6-astra",reasoning_effort:"xhigh",max_output_tokens:32768}:
    {provider,model:"",reasoning_effort:null,max_output_tokens:32768};
}
const providerLabels=Object.fromEntries(["codex_cli","claude_code","openrouter","claude_api","auto"].map(id=>[id,tp(`provider.label.${id}`)]));
const providerNames=Object.fromEntries(["codex_cli","claude_code","openrouter","claude_api"].map(id=>[id,tp(`provider.name.${id}`)]));
// The automatic choice's two candidates; a shared level (e.g. high) replaces both catalog levels.
function autoCandidates(effort=null) {
  const catalog=boot.text_catalog?.auto_candidates||{codex_cli:{model:"gpt-6-astra",reasoning_effort:"xhigh"},claude_code:{model:"claude-sonnet-5-5",reasoning_effort:"high"}};
  return effort?{codex_cli:{...catalog.codex_cli,reasoning_effort:effort},claude_code:{...catalog.claude_code,reasoning_effort:effort}}:catalog;
}
// A preset matches a choice; the automatic ones differ only in their shared level, so that level must match exactly.
const presetMatches=(p,choice)=>choice.provider===p.provider&&choice.model===p.model&&(p.provider==="auto"
  ?(p.reasoning_effort||null)===(choice.reasoning_effort||null):(!p.reasoning_effort||choice.reasoning_effort===p.reasoning_effort));
// A preset's name: the catalog's by its id, else the server's label (text_settings.TEXT_PRESETS).
const presetLabel = p => serverLabel(`preset.${p.id}`, p.label);
function candidateText(c) {
  const standard=tp("common.default");
  return `Codex ${escape(c?.codex_cli?.model||standard)} (${escape(c?.codex_cli?.reasoning_effort||standard)}) · Claude ${escape(c?.claude_code?.model||standard)} (${escape(c?.claude_code?.reasoning_effort||standard)})`;
}
function textChoiceSummary(choice) {
  if(choice.provider==="auto")return `${providerLabels.auto} · ${candidateText(autoCandidates(choice.reasoning_effort))}`;
  return `${providerLabels[choice.provider]||escape(choice.provider)} · ${escape(choice.model||tp("common.default"))} · Reasoning: ${escape(choice.reasoning_effort||tp("common.default"))}`;
}
function renderRunTextChoice(job) {
  if(!["script","research"].includes(job?.run?.kind))return "";
  const choice=job.text_generation;
  const saved=choice?.provider==="auto"?t("run_text.auto",{first:choice.prefer==="codex_cli"?"Astra":"Claude",candidates:asHtml(candidateText(choice.candidates))}):
    `${choice?.model?escape(choice.model):t("run_text.model_unset")} · Reasoning: ${choice?.reasoning_effort?escape(choice.reasoning_effort):t("run_text.unset")}`;
  // The switch is a decision, not telemetry: it stays reachable from the engine room, folded (D-164).
  const switcher=renderTextSwitch(job);
  return `<p class="hint">${t(job.text_switched?"run_text.now":"run_text.saved",{choice:asHtml(saved)})}</p>${renderProviderChoice(job)}${switcher?`<details class="text-switch-box"><summary>${t("run_text.switch")}</summary>${switcher}</details>`:""}`;
}
const SWITCH_CHOICES=Object.fromEntries(["claude_first","astra_first","claude","astra","openrouter","claude_api"].map(id=>[id,tp(`switch.${id}`)]));
function renderTextSwitch(job) {
  // Every script or research run may continue with another text provider (approve_text_switch).
  if(!job.text_switchable)return "";
  const runId=escape(job.run?.run_id||""), current=job.text_switch_choice, models=boot.text_catalog?.openrouter_models||{};
  const currentModel=job.text_generation?.provider==="openrouter"?job.text_generation.model:"";
  const options=Object.entries(SWITCH_CHOICES).map(([id,name])=>`<option value="${id}" ${id===current?"selected":""}>${escape(name)}${id===current?` · ${t("switch.current")}`:""}</option>`).join("");
  const modelOptions=Object.entries(models).map(([id,name])=>`<option value="${escape(id)}" ${id===currentModel?"selected":""}>${escape(name)}</option>`).join("");
  return `<div class="text-switch"><label for="text-switch-choice">${t("switch.label")}</label> <select id="text-switch-choice">${options}</select>
    <select id="text-switch-model" aria-label="${t("switch.model_label")}" ${current==="openrouter"?"":"hidden"}>${modelOptions}</select>
    <input id="text-switch-cost" type="number" inputmode="decimal" min="1" step="1" aria-label="${t("switch.cost_label")}" placeholder="${t("switch.cost_placeholder")}" ${billedProvider(current)?"":"hidden"} value="${Number(job.progress?.cost_limit_usd)||""}">
    <button class="secondary" data-action="text-switch" data-run-id="${runId}">${t("common.apply")}</button>
    <p class="hint">${job.text_switched?`${t("switch.switched")} `:""}${t("switch.hint")}</p></div>`;
}
function resetText(iso) {
  const time=iso?Date.parse(iso):NaN;
  return Number.isFinite(time)?` · Reset ${fmt.dateTime(time,{dateStyle:"short",timeStyle:"short"})}`:"";
}
function windowText(snapshot) {
  const w=(snapshot?.windows||[])[0];
  if(!w||typeof w.used_percent!=="number")return "";
  const name=tp(w.window_minutes===10080?"provider.window.week":w.window_minutes===300?"provider.window.five_hours":"provider.window.other");
  return ` (${name} ${Number(w.used_percent)} %)`;
}
function renderProviderChoice(job) {
  const c=job?.provider_choice;
  if(!c?.provider)return "";
  const s=c.snapshots||{}, codex=s.codex_cli, claude=s.claude_code, parts=[];
  if(codex)parts.push(`Codex ${tp(codex.available?"provider.state.ready":codex.usable?"provider.state.no_quota":"provider.state.unusable")}${windowText(codex)}${codex.available?"":resetText(codex.resets_at)}`);
  if(claude)parts.push(`Claude ${tp(claude.available?"provider.state.ready":claude.usable?"provider.state.blocked":"provider.state.unusable")}${claude.available?"":resetText(claude.blocked_until||claude.resets_at)}`);
  const current=`${t("provider.current",{provider:asHtml(providerNames[c.provider]||escape(c.provider))})}${c.model?" · "+escape(c.model):""}${c.mode==="fixed"?` (${t("provider.fixed")})`:""}`;
  const switched=c.switch?` · ${t("provider.switched",{from:asHtml(providerNames[c.switch.from]||escape(c.switch.from)),to:asHtml(providerNames[c.switch.to]||escape(c.switch.to))})}`:"";
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
// What the series is for (TopicBrief.series_goal): each aim weighted 0 to 3; unset means the evaluating default.
const GOAL_NAMES=Object.fromEntries(["understand","evaluate","apply"].map(id=>[id,tp(`goal.${id}`)]));
function goalSummary(goal) {
  if(!goal)return tp("goal.unset_default");
  const parts=Object.keys(GOAL_NAMES).filter(key=>Number(goal[key])>0).sort((a,b)=>Number(goal[b])-Number(goal[a]));
  return parts.map(key=>`${GOAL_NAMES[key]} ${"●".repeat(Number(goal[key]))}${"○".repeat(3-Number(goal[key]))}`).join(" · ")||tp("goal.unset");
}
function setupSummary() {
  const {proposal,config:c,text:choice,audio:a,execution:x}=setupSelection();
  if(!project)return "";
  const mode=(value,limit)=>value==="parallel"?tp("summary.mode.parallel",{limit}):tp("execution.sequential");
  return `<section class="panel"><div class="panel-title"><h2>${t(proposal&&!project.proposal_applied?"summary.title.proposal":"summary.title.saved")}</h2></div>
    <dl><dt>${t("summary.topic")}</dt><dd>${escape(c.topic)}</dd><dt>${t("summary.question")}</dt><dd>${escape(c.central_question||tp("summary.question_open"))}</dd>
    <dt>${t("summary.language")}</dt><dd>${languageName(c.language)} · ${c.target_total_minutes?t("summary.minutes",{minutes:c.target_total_minutes}):t("summary.length_open")}</dd>
    <dt>${t("summary.knowledge")}</dt><dd>${escape(c.prior_knowledge||tp("summary.knowledge_none"))} · ${escape(c.depth_request)}</dd>
    <dt>${t("summary.goal")}</dt><dd>${escape(goalSummary(c.series_goal))}</dd>
    <dt>${t("summary.recency")}</dt><dd>${c.recency_months?t("summary.recency_months",{months:Number(c.recency_months)}):t("summary.recency_none")}</dd>
    ${c.focus_questions?.length?`<dt>${t("summary.focus")}</dt><dd>${c.focus_questions.map(escape).join(" · ")}</dd>`:""}
    ${c.excluded_topics?.length?`<dt>${t("summary.excluded")}</dt><dd>${c.excluded_topics.map(escape).join(" · ")}</dd>`:""}
    ${c.seed_urls?.length?`<dt>${t("summary.seed_urls")}</dt><dd>${c.seed_urls.map(escape).join(" · ")}</dd>`:""}
    <dt>${t("summary.text_model")}</dt><dd>${textChoiceSummary(choice)}</dd>
    ${choice.provider==="openrouter"?`<dt>${t("summary.live_research")}</dt><dd>${t("summary.live_research_openrouter")}</dd>`:""}
    <dt>${t("summary.voices")}</dt><dd>${escape(audioLabel(a))} · ${escape(a.voices.host_a)} &amp; ${escape(a.voices.host_b)}</dd>
    <dt>${t("summary.text_execution")}</dt><dd>${mode(x.text,tp("summary.text_parallel"))}</dd><dt>${t("summary.recording")}</dt><dd>${a.provider==="qwen3_local"?t("summary.recording_qwen"):mode(x.audio,tp("summary.recording_parallel"))}</dd>
    <dt>${t("summary.gap_probe")}</dt><dd>${project.jev_probe?`${t("summary.jev_on")}${project.jev_default?` · ${t("summary.jev_default")}`:""}`:t("summary.jev_off")} <button type="button" class="secondary small" data-action="toggle-jev-probe" data-enabled="${project.jev_probe?"0":"1"}">${t(project.jev_probe?"summary.jev_switch_off":"summary.jev_switch_on")}</button></dd>
    <dt>${t("summary.allowances")}</dt><dd>${escape(allowanceSummary(project.allowances))}</dd></dl>
    <p class="hint">${t("summary.settings_hint")}</p>
    <div class="actions"><button type="button" class="secondary small" data-action="open-settings">${t("common.open_settings")}</button></div>
    <details class="hint-toggle"><summary>${t("summary.jev_question")}</summary><p class="hint">${t("summary.jev_explained")}</p></details>
    <p class="hint">${t("summary.changes_hint")}</p>
    ${proposal&&!project.proposal_applied?`<button data-action="apply-proposal" ${running()||setupSending||pendingAttachments.length||project.proposal_current===false||!boot.capabilities?.conversational_setup?"disabled":""}>${t("summary.apply")}</button><p class="hint">${t(project.proposal_current===false?"summary.apply_attachments_changed":"summary.apply_hint")}</p>`:""}
    </section>`;
}
function renderAttachments() {
  const saved=project?.attachments||[];
  return `<ul class="attachment-list">${saved.map(row=>`<li><span><strong>${escape(row.name)}</strong><small>${t("attachments.saved")}${row.characters<200?` · ${t("attachments.short_note")}`:""}</small></span><button type="button" class="secondary small" data-remove-attachment="${escape(row.id)}" ${disabled()} aria-label="${t("attachments.remove_label",{name:row.name})}">${t("common.remove")}</button></li>`).join("")}
    ${pendingAttachments.map((row,index)=>`<li><span><strong>${escape(row.name)}</strong><small>${t("attachments.pending")}</small></span><button type="button" class="secondary small" data-remove-pending="${index}" aria-label="${t("attachments.skip_label",{name:row.name})}">${t("common.remove")}</button></li>`).join("")}</ul>`;
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
  if(!boot.capabilities?.project_attachments)throw new Error(tp("attachments.restart_needed"));
  const limits=boot.attachment_limits||{files:10,file_bytes:262144,docx_bytes:2097152,transfer_bytes:4194304,total_bytes:1048576};
  const selected=Array.from(files), epoch=navigationEpoch;
  if(!selected.length)return;
  if(pendingAttachments.length+selected.length>limits.files)throw new Error(tp("attachments.too_many"));
  if(selected.some(file=>! /\.(md|txt|docx)$/i.test(file.name)||!file.size||file.size>(/\.docx$/i.test(file.name)?limits.docx_bytes:limits.file_bytes)))
    throw new Error(tp("attachments.types"));
  if(pendingAttachments.reduce((sum,file)=>sum+file.bytes,0)+selected.reduce((sum,file)=>sum+file.size,0)>limits.transfer_bytes)
    throw new Error(tp("attachments.too_large"));
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
  if(j.status==="running")return `<div class="chat-message pending" aria-live="polite"><strong>${t("chat.editor")}</strong><p><span class="activity-dot" aria-hidden="true"></span>${t("chat.writing",{elapsed:elapsedText(j.started_at)})}</p></div>`;
  const info=stopInfo(j), last=(project.chat||[]).at(-1);
  if(!info||last?.role!=="user")return "";
  const limit=Number(project.chat_budget?.limit)||0;
  const raise=info.code==="chat_budget"&&limit?`<button class="secondary small" data-action="approve-chat" data-model-calls="${limit+50}">${t("chat.raise_limit",{limit:limit+50})}</button>`:"";
  return `<div class="chat-message failed" role="alert"><strong>${t("chat.no_answer",{title:info.title})}</strong><p>${escape(info.text)}</p>${info.message&&info.message!==info.text?`<p class="hint">${t(sameLanguage(info)?"chat.message":"stop.original_message",{message:info.message})}</p>`:""}
    <div class="actions">${raise}<button class="small" data-action="resend-chat" ${running()?"disabled":""}>${t("button.resend")}</button></div></div>`;
}
// A settled brief: a project with work past the conversation. Its page then leads with the brief (a waiting proposal
// with its button) and folds the conversation, which on a finished project filled the page while its input box
// covered it (D-161).
function briefSettled() {
  return !!project&&!!(project.research||project.outline||project.episodes?.length||currentRun());
}
// The partner writes Markdown (bold labels, lists); the editor's own messages stay plain text.
function chatMessage(m) {
  return m.role==="user"?`<div class="chat-message user"><strong>${t("chat.you")}</strong><p>${escape(m.message)}</p></div>`
    :`<div class="chat-message"><strong>${t("chat.editor")}</strong><div class="chat-text">${renderMarkdown(m.message)}</div></div>`;
}
// The connection check answers where it was started, beside its button (before 2026-10-07 in the engine room).
// A finished voice sample plays from the brief page that asked for it (before 2026-10-07, from the engine room).
function samplePanel() {
  const j=project?.main_job??project?.job;
  if(j?.action!=="audio_sample"||j.status!=="completed"||!j.sample)return "";
  const s=j.sample;
  return `<section class="panel"><h2>${t("sample.new")}</h2><p>${escape(s.voice)} · ${languageName(s.language)}</p>
    <div class="actions"><button class="secondary small" data-play-voice="${escape(s.voice)}" data-language="${escape(s.language)}">${t("voices.play")}</button><a href="${mediaUrl(s.audio)}" download>${t("common.download_mp3")}</a></div></section>`;
}
function checksPanel() {
  const j=project?.main_job??project?.job;
  if(j?.action!=="check")return "";
  if(j.status==="running")return `<section class="panel"><h2>${t("checks.title")}</h2><p><span class="activity-dot" aria-hidden="true"></span>${t("checks.running")}</p></section>`;
  return j.checks?`<section class="panel checks-panel"><h2>${t("checks.title")}</h2>${renderChecks(j.checks)}</section>`:"";
}
// A new project may start as a trial (D-157): the option, unticked until the editor ticks it, and what a trial is
// limited to. Ticked, an empty message is enough: the server takes the sample topic of the brief's language.
function trialOption() {
  if(project||!boot?.trial)return "";
  const note=trialNote(), empty=trialChecked?t("trial.empty_hint",{send:quoted(tp("chat.send"))}):"";
  return `<label class="check trial-option"><input type="checkbox" id="trial-option"${trialChecked?" checked":""}> ${t("trial.label")}</label>${note||empty?`<p class="hint trial-note">${[note,empty].filter(Boolean).join(" ")}</p>`:""}`;
}
// Setup: the conversation fills the working column with the composer at its foot; the proposal sits in the rail.
function renderBrief() {
  const {proposal,config:c,audio:a}=setupSelection(), chat=project?.chat||[];
  const compatible=boot.capabilities?.conversational_setup;
  const nextPage=recommendedPage()===PAGE.brief?PAGE.research:recommendedPage();
  const voices=audioCatalog()[a.provider]?.voices||[];
  const attachmentCount=(project?.attachments?.length||0)+pendingAttachments.length;
  const messages=chat.length?chat.map(chatMessage).join(""):`<div class="chat-message"><strong>${t("chat.editor")}</strong><p>${t("chat.welcome")}</p></div>`;
  const chatJob=project?.main_job??project?.job;
  const otherJob=running()&&chatJob?.action!=="assistant";
  const settled=briefSettled();
  const open=!settled||briefChatOpen||setupSending||(chatJob?.action==="assistant"&&chatJob.status==="running")||chat.at(-1)?.role==="user";
  // The attachment panel opens only for files chosen but not sent yet; saved ones are counted in its summary.
  const conversation=`<div class="conversation" id="conversation">${messages}${chatStatusBubble()}<div id="chat-end"></div></div>
    ${!running()&&proposal?.suggested_replies?.length?`<div class="actions suggested">${proposal.suggested_replies.map(reply=>`<button class="secondary small" data-setup-reply="${escape(reply)}">${escape(reply)}</button>`).join("")}</div>`:""}
    ${otherJob?`<p class="hint composer-lock">${t("chat.locked")}</p>`:""}
    <form id="chat-form" class="composer"><fieldset ${running()||setupSending||readingAttachments||!compatible?"disabled":""}>${area("chat-message",t("chat.your_message"),"",2)}${trialOption()}
    <div class="composer-tools">
    ${boot.capabilities?.project_attachments?`<details class="composer-menu"${pendingAttachments.length?" open":""}><summary>${t("attachments.attach")}${attachmentCount?` · ${attachmentCount}`:""}</summary><div class="attachment-picker"><label for="chat-files">${t("attachments.attach_types")}</label><input id="chat-files" type="file" accept=".md,.txt,.docx,text/plain,text/markdown,application/vnd.openxmlformats-officedocument.wordprocessingml.document" multiple aria-describedby="attachment-hint"><p id="attachment-hint" class="hint">${t("attachments.hint",{send:quoted(tp("chat.send"))})}</p><div id="attachment-list">${renderAttachments()}</div></div></details>`:`<p class="hint">${t("attachments.restart_hint")}</p>`}
    <button type="submit">${t(setupSending?"chat.sending":readingAttachments?"attachments.reading":"chat.send")}</button></div></fieldset></form>`;
  const library=`<details class="panel"><summary>${t("voices.listen")}</summary><p class="hint">${a.provider==="qwen3_local"?"Qwen":"Gemini"} · ${t("voices.listen_hint",{language:languageName(c.language)})}</p><div class="voice-library">${voices.map(v=>`<div class="sample-row"><strong>${escape(v)}</strong><button class="secondary small" data-preview-voice="${escape(v)}" data-preview-provider="${a.provider}" data-language="${c.language}" ${a.provider!=="qwen3_local"&&!savedSample(v,c.language)&&running()?"disabled":""}>${sampleButtonLabel(a.provider,v,c.language)}</button></div>`).join("")}</div>
    ${isGemini(a.provider)?`<div id="voice-library-panel">${renderVoiceLibrary(c.language)}</div>`:""}</details>`;
  const actions=project?`<div class="actions"><button class="secondary" data-action="check" ${disabled()}>${t("button.check")}</button><button data-step="${nextPage}" ${proposal&&!project.proposal_applied?"disabled":""}>${t("brief.next",{step:steps[nextPage]})}</button></div>${checksPanel()}${samplePanel()}`:"";
  const main=settled
    ?`${setupSummary()}<details class="panel chat-archive" id="chat-archive"${open?" open":""}><summary>${t("chat.archive",{count:chat.length})}</summary>${conversation}</details>`
    :`<section class="panel chat-panel">${conversation}</section>`;
  const rail=settled?`${actions}${library}`:`${setupSummary()}${proposalChangesBrief()?pausedHint(["config"]):""}${library}${actions}`;
  return heading(1)+
    `${!compatible?`<p class="note">${t("brief.restart_needed")}</p>`:""}
    <div id="stop-card"></div>
    <div class="split"><div class="split-main">${main}</div><aside class="split-rail">${rail}</aside></div>`;
}
// The settings page: text model, audio, execution, pre-approvals, limits, Claude and the OpenRouter key for every
// project (studio_settings). Running and paused jobs keep what they bound; limits and the time limit of one call
// apply when a job resumes.
const EXECUTION_MODES=[["sequential",tp("execution.sequential")],["parallel",tp("execution.parallel")]];
// Whether a key is there, in the server's memory or its environment; the key itself never reaches the page.
const keyChip=available=>`<span class="chip ${available?"done":"decision"}">${t(available?"key.chip.stored":"key.chip.missing")}</span>`;
function settingField(id,label,control,hint="") {
  return `<div class="field"><label for="${id}">${escape(label)}</label>${control}${hint?`<p class="hint">${hint}</p>`:""}</div>`;
}
function settingSelect(id,options,value) {
  return `<select id="${id}">${options.map(([v,l])=>`<option value="${escape(String(v))}" ${String(v)===String(value)?"selected":""}>${escape(l)}</option>`).join("")}</select>`;
}
function settingNumber(id,value,min,max,step=1) {
  return `<input id="${id}" type="number" min="${min}" max="${max}" step="${step}" value="${Number(value)}">`;
}
// The interface language (D-152): saved for the workspace (.studio/ui.json), outside the settings draft and its hash;
// the page loads again in the chosen language. Each option is named in its own language.
const UI_LANGUAGES=["auto","de","en"];
function languageSelect(id) {
  return `<select id="${id}" data-ui-language aria-label="${t("language.select.label")}">${UI_LANGUAGES.map(value=>`<option value="${value}" ${LOCALE.setting===value?"selected":""}>${t(`language.option.${value}`)}</option>`).join("")}</select>`;
}
async function chooseLanguage(value) {
  if(!UI_LANGUAGES.includes(value)||value===LOCALE.setting)return;
  await api("/api/ui-language",{ui_language:value});
  window.location?.reload?.();
}
function renderSettings() {
  const d=settingsDraft, data=settingsData, catalog=audioCatalog(), presets=boot.text_catalog?.presets||[];
  const a=d.audio, voices=(catalog[a.provider]?.voices||[]).map(v=>[v,v]), gemini=catalog[a.provider];
  const chosen=presets.find(p=>presetMatches(p,d.text));
  const choices=data.allowance_choices||{fresh_attempts:[0,1,2,3],extra_calls:[0,100,250,500,1000]};
  const pauses=a.pauses||{same_speaker_ms:250,speaker_change_ms:450,chapter_break_ms:900};
  const limits=d.research_limits;
  // The presets grouped by how a call is paid, each paid group naming its missing key (D-161): eleven options in one
  // list mixed subscriptions, API keys and OpenRouter, and those without a key looked like the others.
  const radio=p=>`<label class="setting-choice"><input type="radio" name="settings-text" value="${escape(p.id)}" ${p===chosen?"checked":""}><span>${escape(presetLabel(p))}</span></label>`;
  const used=new Set(), group=(title,rows,key=null)=>{
    if(!rows.length)return "";
    rows.forEach(p=>used.add(p));
    const missing=key&&!data[KEY_FLAGS[key]];
    return `<fieldset class="setting-group"><legend>${title}${missing?` <span class="chip decision">${t(`key.missing.${key}`)}</span>`:""}</legend><div class="setting-choices">${rows.map(radio).join("")}</div></fieldset>`;
  };
  const textOptions=group(t("settings.text.group.subscriptions"),presets.filter(p=>["auto","claude_code","codex_cli"].includes(p.provider)))+
    group(t("settings.text.group.anthropic"),presets.filter(p=>p.provider==="claude_api"),"anthropic")+
    group(t("settings.text.group.openrouter"),presets.filter(p=>p.provider==="openrouter"),"openrouter")+
    group(t("settings.text.group.other"),presets.filter(p=>!used.has(p)))+
    (chosen?"":`<label class="setting-choice"><input type="radio" name="settings-text" value="" checked><span>${t("settings.text.previous",{choice:asHtml(textChoiceSummary(d.text))})}</span></label>`);
  const sections=[["settings-section-text","text"],["settings-section-search","search"],["settings-section-audio","audio"],["settings-section-execution","execution"],
    ["settings-section-allowances","allowances"],["settings-section-limits","limits"],["settings-section-claude","claude"],["key-panel-openrouter","keys"],
    ["settings-section-language","language"]];
  return `<header class="page-head"><div class="page-title"><span class="eyebrow">Studio</span><h1>${t("settings.title")}</h1></div><span class="chip ${data.global?"done":"decision"}">${t(data.global?"settings.global":"settings.per_project")}</span></header>
    <nav class="settings-nav" aria-label="${t("settings.nav_label")}">${sections.map(([id,name])=>`<button class="quiet small" data-scroll="${id}">${t(`settings.section.${name}`)}</button>`).join("")}</nav>
    <p class="key-states">Keys: <button class="quiet small" data-scroll="key-panel-openrouter">OpenRouter</button>${keyChip(data.key_available)} <button class="quiet small" data-scroll="key-panel-google">Google</button>${keyChip(data.google_key_available)} <button class="quiet small" data-scroll="key-panel-anthropic">Anthropic</button>${keyChip(data.anthropic_key_available)} <button class="quiet small" data-scroll="key-panel-perplexity">Perplexity</button>${keyChip(data.perplexity_key_available)}</p>
    ${data.global?"":`<p class="note">${data.source_project?t("settings.legacy.project",{project:quoted(data.source_project)}):t("settings.legacy.defaults")}</p>`}
    <p class="hint">${t("settings.running_hint")}</p>
    <section class="panel" id="settings-section-text"><h2>${t("settings.section.text")}</h2>${textOptions}
      ${settingField("settings-max-tokens",tp("settings.max_tokens"),settingNumber("settings-max-tokens",d.text.max_output_tokens||32768,1024,200000,1024))}
      <details class="hint-toggle"><summary>${t("settings.cost_question")}</summary><p class="hint">${t("settings.cost_explained")}</p>${costHint()}</details></section>
    <section class="panel" id="settings-section-search"><h2>${t("settings.section.search")}</h2><div class="setting-choices">
      <label class="setting-choice"><input type="radio" name="settings-web-search" value="model" ${d.web_search!=="perplexity"?"checked":""}><span>${t("settings.search.model")}</span></label>
      <label class="setting-choice"><input type="radio" name="settings-web-search" value="perplexity" ${d.web_search==="perplexity"?"checked":""}><span>${t("settings.search.perplexity")}</span></label></div>
      <p class="hint">${t("settings.search.hint")}</p></section>
    <section class="panel" id="settings-section-audio"><h2>${t("settings.section.audio")}</h2>
      <div class="row">${settingField("settings-audio-provider",tp("settings.audio.provider"),settingSelect("settings-audio-provider",Object.entries(catalog).map(([id,row])=>[id,audioProviderLabel(id,row)]),a.provider))}
      ${isGemini(a.provider)&&gemini?.models?settingField("settings-audio-model",tp("settings.audio.model"),settingSelect("settings-audio-model",Object.entries(gemini.models),a.model||gemini.default_model)):""}</div>
      <div class="row">${settingField("settings-voice-a",tp(a.provider==="google_gemini_tts"?"settings.voice.a_google":"settings.voice.a"),settingSelect("settings-voice-a",voices,a.voices.host_a))}${settingField("settings-voice-b",tp(a.provider==="google_gemini_tts"?"settings.voice.b_google":"settings.voice.b"),settingSelect("settings-voice-b",voices,a.voices.host_b))}</div>
      ${a.provider==="google_gemini_tts"?renderGoogleAudio(a,gemini):""}
      <div class="row">${settingField("settings-pause-same",tp("settings.pause.same"),settingNumber("settings-pause-same",pauses.same_speaker_ms,0,10000,50))}${settingField("settings-pause-change",tp("settings.pause.change"),settingNumber("settings-pause-change",pauses.speaker_change_ms,0,10000,50))}${settingField("settings-pause-chapter",tp("settings.pause.chapter"),settingNumber("settings-pause-chapter",pauses.chapter_break_ms,0,10000,50))}</div>
      ${a.provider==="google_gemini_tts"?`<p class="hint">${t("settings.pause.google_hint")}</p>`:""}
      ${renderPace(a,gemini)}
      ${isGemini(a.provider)?`<label class="approval"><input id="settings-expression" type="checkbox" ${a.expression!==false?"checked":""}><span>${t("settings.expression")}${a.provider==="google_gemini_tts"?` (${t("settings.expression_google")})`:""}</span></label>`:""}
      <p class="hint">${t("settings.audio.hint")}</p></section>
    <section class="panel" id="settings-section-execution"><h2>${t("settings.section.execution")}</h2><div class="row">${settingField("settings-exec-text",tp("summary.text_execution"),settingSelect("settings-exec-text",EXECUTION_MODES,d.execution.text),t("settings.exec.text_hint"))}
      ${settingField("settings-exec-audio",tp("summary.recording"),settingSelect("settings-exec-audio",EXECUTION_MODES,d.execution.audio),t("settings.exec.audio_hint"))}</div></section>
    <section class="panel" id="settings-section-allowances"><h2>${t("settings.section.allowances")}</h2><div class="row">${settingField("settings-fresh",tp("settings.allow.fresh"),settingSelect("settings-fresh",choices.fresh_attempts.map(n=>[n,n?tp("settings.allow.fresh_n",{count:n}):tp("settings.allow.none")]),d.allowances.fresh_attempts))}
      ${settingField("settings-extra",tp("settings.allow.extra"),settingSelect("settings-extra",choices.extra_calls.map(n=>[n,n?tp("settings.allow.extra_n",{count:n}):tp("settings.allow.no")]),d.allowances.extra_calls))}</div>
      <p class="hint">${t("settings.allow.hint")}</p></section>
    <section class="panel" id="settings-section-limits"><h2>${t("settings.section.limits")}</h2><div class="row">${settingField("settings-calls",tp("settings.limits.calls"),settingNumber("settings-calls",limits.model_calls,1,100000))}${settingField("settings-sources",tp("settings.limits.sources"),settingNumber("settings-sources",limits.sources,1,10000))}${settingField("settings-rounds",tp("settings.limits.rounds"),settingNumber("settings-rounds",limits.search_rounds,1,10000))}</div>
      <div class="row">${settingField("settings-cost",tp("settings.limits.cost"),`<input id="settings-cost" type="number" inputmode="decimal" min="1" max="100000" step="1" value="${limits.cost_usd??""}" placeholder="${t("settings.limits.cost_none")}">`,t("settings.limits.cost_hint"))}</div>
      ${settingField("settings-timeout",tp("settings.limits.timeout"),settingNumber("settings-timeout",Math.round(d.text_timeout_seconds/60),5,240),t("settings.limits.timeout_hint"))}</section>
    <section class="panel" id="settings-section-claude"><h2>${t("settings.section.claude")}</h2><label class="approval"><input id="settings-claude-extra" type="checkbox" ${data.claude_extra_usage?"checked":""}><span>${t("settings.claude.extra")}</span></label>
      <p class="hint">${t("settings.claude.hint")}</p></section>
    ${["openrouter","perplexity","anthropic","google"].map(key=>keyPanel(key,data)).join("\n    ")}
    <section class="panel" id="settings-section-language"><h2>${t("settings.section.language")}</h2><div class="field"><label for="ui-language-settings">${t("language.select.label")}</label>${languageSelect("ui-language-settings")}</div>
      <p class="hint">${t("settings.language.hint")}</p></section>
    <div class="action-bar settings-save"><p id="settings-dirty" class="hint${settingsDirty?" dirty":""}">${t(settingsDirty?"settings.dirty":"settings.clean")}</p><button data-action="save-settings">${t("settings.save")}</button></div>`;
}
// One key's panel on the settings page; the OpenRouter key keeps the fields and buttons it had before the others.
function keyPanel(key,data) {
  const field=KEY_FIELDS[key], status=KEY_STATUS[key], available=data[KEY_FLAGS[key]];
  const own=key==="openrouter"?"":` data-key-field="${field}" data-key-kind="${key}"`, forget=key==="openrouter"?"":` data-key-kind="${key}"`;
  return `<section class="panel" id="key-panel-${key}"><div class="panel-title"><h2>${t(`key.name.${key}`)}</h2>${keyChip(available)}</div><p class="hint">${t(`key.panel.${key}`)}</p>
      ${textInput(field,t(`key.name.${key}`),"","password")}<p id="${status}" class="hint">${t(available?"key.available":"key.none")}</p><div class="actions"><button class="secondary small" data-action="store-key"${own}>${t("key.store")}</button><button class="secondary small" data-action="forget-key"${forget}>${t("key.forget")}</button></div></section>`;
}
// Each language's pace (speech.LanguagePace, D-147): a slower montage on every route, with the pitch kept, and for Google
// a calm delivery. A language without an entry is spoken as recorded.
const PACE_LANGUAGES=[["de-DE",tp("pace.language.de-DE")],["en-US",tp("pace.language.en-US")]];
const TEMPO_CHOICES=[1,0.97,0.95,0.93,0.9,0.88,0.85];
function paceOf(a,language) {
  return {tempo:1,unhurried:false,...(a?.pace?.[language]||{})};
}
function renderPace(a,gemini) {
  const tempos=tempo=>[...new Set([...TEMPO_CHOICES,Number(tempo)])].sort((x,y)=>y-x).map(value=>[value,`${Math.round(value*100)} %`]);
  const google=a.provider==="google_gemini_tts";
  return `<div class="row">${PACE_LANGUAGES.map(([id,name])=>settingField(`settings-tempo-${id}`,tp("settings.pace.tempo",{language:name}),settingSelect(`settings-tempo-${id}`,tempos(paceOf(a,id).tempo),paceOf(a,id).tempo))).join("")}</div>
    ${google?PACE_LANGUAGES.map(([id,name])=>`<label class="approval"><input id="settings-unhurried-${id}" type="checkbox" ${paceOf(a,id).unhurried?"checked":""}><span>${t("settings.pace.unhurried",{language:name,word:quoted(gemini?.unhurried||"unhurried")})}</span></label>`).join(""):""}
    <p class="hint">${t("settings.pace.hint")}</p>`;
}
// Google's own choices: the style of each role (a preset or own words), the roles swapping from episode to episode, and
// a short conversation of exactly this selection to listen to (voice_samples.generate_pair).
function renderGoogleAudio(a,gemini) {
  const presets=Object.entries(gemini?.style_presets||{}), styles=a.styles||gemini?.default_styles||{host_a:"",host_b:""};
  const preset=presets.find(([,p])=>p.host_a===styles.host_a&&p.host_b===styles.host_b)?.[0]||"";
  const limit=gemini?.max_style_characters||80, field=(id,value)=>`<input id="${id}" maxlength="${limit}" value="${escape(value)}">`;
  return `<div class="row">${settingField("settings-style-preset",tp("settings.style"),settingSelect("settings-style-preset",[...presets.map(([id,p])=>[id,serverLabel(`style.${id}`,p.label)]),...(preset?[]:[["",tp("settings.style_own")]])],preset))}</div>
    <div class="row">${settingField("settings-style-a",tp("settings.style_a"),field("settings-style-a",styles.host_a))}${settingField("settings-style-b",tp("settings.style_b"),field("settings-style-b",styles.host_b))}</div>
    <p class="hint">${t("settings.style_hint")}</p>
    <label class="approval"><input id="settings-alternate" type="checkbox" ${a.alternate_roles?"checked":""}><span>${t("settings.alternate")}</span></label>
    <div class="actions"><label class="sr-only" for="settings-pair-language">${t("pair.language_label")}</label>${settingSelect("settings-pair-language",[["de-DE",languageName("de-DE")],["en-US",languageName("en-US")]],pairLanguage)}<button type="button" class="secondary small" data-action="pair-sample">${t("pair.play")}</button>${a.alternate_roles?`<button type="button" class="secondary small" data-action="pair-sample" data-swap="1">${t("pair.play_swapped")}</button>`:""}</div>
    <p class="hint" id="pair-sample-status">${t("pair.hint")}</p>`;
}
async function pairSample(button) {
  const a=settingsFromForm().audio, language=$("settings-pair-language")?.value||"de-DE";
  pairLanguage=language;
  const voices=button.dataset.swap?{host_a:a.voices.host_b,host_b:a.voices.host_a}:a.voices;
  // The sample language's pace, so a slower or calmer delivery can be heard before an episode is recorded with it.
  const request={voices,styles:a.styles||null,language,...(a.pace?.[language]?{pace:a.pace[language]}:{})};
  let view=await api("/api/pair-sample",request);
  if(!view.ready){
    if(!keyAvailable("google"))throw new Error(tp("pair.key_first"));
    if(typeof window.confirm==="function"&&!window.confirm(tp("pair.confirm")))return;
    if($("pair-sample-status"))$("pair-sample-status").textContent=tp("pair.making");
    view=await api("/api/pair-sample",{...request,generate:true});
  }
  const player=$("sample-player");
  if(playlist)closePlayer();
  player.src=view.url;playingSample={voice:`${voices.host_a}+${voices.host_b}`,language,url:view.url};
  $("sample-playback").hidden=false;
  $("sample-playing-label").textContent=tp("pair.playing",{explains:voices.host_a,asks:voices.host_b,language:languageName(language)});
  if($("pair-sample-status"))$("pair-sample-status").textContent=tp("pair.saved");
  try{await player.play();}catch{throw new Error(tp("pair.play_failed"));}
}
// The draft as the form holds it now; a preset sets provider, model and level together.
// What a run of the measured models costs per call, as the money limit is set against it (cost_estimate).
function costHint() {
  const rows=boot?.text_catalog?.usd_per_call||[];
  if(!rows.length)return "";
  const name=model=>boot.text_catalog?.claude_models?.[model]||model, kind=k=>tp(k==="research"?"settings.cost.research":"settings.cost.script");
  return `<p class="hint">${t("settings.cost.measured",{date:boot.text_catalog.usd_per_call_measured_on||"",rows:asHtml(rows.map(r=>`${escape(name(r.model))} ${kind(r.kind)} ${usd(r.usd)}`).join(" · "))})}</p>`;
}
function settingsFromForm() {
  const d=structuredClone(settingsDraft), value=id=>$(id)?.value;
  const presetId=document.querySelector?.('input[name="settings-text"]:checked')?.value;
  const preset=(boot.text_catalog?.presets||[]).find(p=>p.id===presetId);
  const tokens=Number(value("settings-max-tokens"))||d.text.max_output_tokens||32768;
  d.text=preset?{provider:preset.provider,model:preset.model,reasoning_effort:preset.reasoning_effort,max_output_tokens:tokens}:{...d.text,max_output_tokens:tokens};
  const provider=value("settings-audio-provider")||d.audio.provider;
  d.audio={...d.audio,provider,voices:{host_a:value("settings-voice-a")||d.audio.voices.host_a,host_b:value("settings-voice-b")||d.audio.voices.host_b},
    pauses:{same_speaker_ms:Number(value("settings-pause-same")),speaker_change_ms:Number(value("settings-pause-change")),chapter_break_ms:Number(value("settings-pause-chapter"))}};
  if($("settings-audio-model"))d.audio.model=value("settings-audio-model");
  if($("settings-expression"))d.audio.expression=!!$("settings-expression").checked;
  // Styles and alternating roles are Google's only; the other routes keep their choice as before.
  if(provider==="google_gemini_tts"&&$("settings-style-a"))d.audio.styles={host_a:value("settings-style-a")||"",host_b:value("settings-style-b")||""};
  if(provider==="google_gemini_tts"&&$("settings-alternate"))d.audio.alternate_roles=!!$("settings-alternate").checked;
  // The pace of each language; one at full speed and without a calm delivery is left out, as the server stores it.
  if($("settings-tempo-de-DE")){
    const pace=PACE_LANGUAGES.map(([id])=>[id,{tempo:Number(value(`settings-tempo-${id}`))||paceOf(d.audio,id).tempo,
      unhurried:$(`settings-unhurried-${id}`)?!!$(`settings-unhurried-${id}`).checked:paceOf(d.audio,id).unhurried}]);
    const kept=pace.filter(([,p])=>p.tempo!==1||p.unhurried);
    if(kept.length)d.audio.pace=Object.fromEntries(kept); else delete d.audio.pace;
  }
  d.execution={text:value("settings-exec-text"),audio:value("settings-exec-audio")};
  d.allowances={fresh_attempts:Number(value("settings-fresh")),extra_calls:Number(value("settings-extra"))};
  d.research_limits={model_calls:Number(value("settings-calls")),sources:Number(value("settings-sources")),search_rounds:Number(value("settings-rounds"))};
  const cost=String(value("settings-cost")??"").trim();
  if(cost)d.research_limits.cost_usd=Number(cost);
  const search=document.querySelector?.('input[name="settings-web-search"]:checked')?.value;
  d.web_search=search||d.web_search||"model";
  d.text_timeout_seconds=Math.round(Number(value("settings-timeout"))*60);
  return d;
}
async function saveSettings() {
  const settings=settingsFromForm();
  const saved=await api("/api/settings",{settings,hash:settingsData.hash,claude_extra_usage:!!$("settings-claude-extra")?.checked});
  settingsData=saved;settingsDraft=structuredClone(saved.settings);settingsDirty=false;render();
  notice(tp("settings.saved_notice"),"ok");
}
// A field of the settings form changed: the save bar says so until it is saved (keys are stored on their own button).
function markSettingsDirty(target) {
  if(!settingsPage||!target||target.id==="settings-pair-language")return;
  if(!String(target.id||"").startsWith("settings-")&&!["settings-text","settings-web-search"].includes(target.name))return;
  settingsDirty=true;
  const note=$("settings-dirty");
  if(note){note.textContent=tp("settings.dirty");note.className="hint dirty";}
}
// Leaving the settings with unsaved edits asks first; false keeps the page.
function leaveSettings() {
  if(!settingsPage||!settingsDirty)return true;
  if(typeof window.confirm==="function"&&!window.confirm(tp("settings.leave_confirm")))return false;
  settingsDirty=false;return true;
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
// The dossier as a reader needs it (D-162): an overview, the guiding questions with their state, the findings grouped
// under the question they answer and folded, the long lists folded at the end. The pipeline's Markdown (research.
// render_dossier) stays unchanged; this view reads its structure, in either content language. 2026-10-07: rendered
// as one page it was about 490,000 px tall, its contents list some 300 internal finding ids, and the coverage and the
// open questions came last.
const FINDING_HEADING=/^(\S+__\S+) — ([a-z_]+)$/;
const FINDING_KINDS=Object.fromEntries(["mechanism","definition","claim","limitation","example"].map(id=>[id,tp(`finding.${id}`)]));
const COVERAGE_STATES={answered:tp("coverage.answered"),partially_answered:tp("coverage.partial"),partial:tp("coverage.partial"),
  unanswered:tp("coverage.open"),open:tp("coverage.open"),gap:tp("coverage.gap"),accepted_gap:tp("coverage.accepted_gap")};
const COVERAGE_LINE=/^- \*\*(.+)\*\* — ([^;]+);\s*[^:;]+:\s*(.*?)\.(?:\s+(.*))?$/;
function parseDossier(text) {
  const doc={title:"",intro:[],sections:[]};
  let section=null, finding=null;
  for(const line of String(text??"").replace(/\r\n?/g,"\n").split("\n")){
    let m;
    if(!section&&!doc.title&&(m=/^# (.+)$/.exec(line))){doc.title=m[1].trim();continue;}
    if((m=/^## (.+)$/.exec(line))){section={title:m[1].trim(),body:[],findings:[]};doc.sections.push(section);finding=null;continue;}
    if(section&&(m=/^### (.+)$/.exec(line))&&(m=FINDING_HEADING.exec(m[1].trim()))){
      finding={id:m[1],kind:m[2],body:[]};section.findings.push(finding);continue;
    }
    (finding?finding.body:section?section.body:doc.intro).push(line);
  }
  return doc;
}
// Internal references a reader cannot use: source-section ids, the claim-type enums, the run id, the index file.
function cleanDossierText(text) {
  return String(text)
    .replace(/\s*\(`src_[0-9a-f]+(?:#sec_[0-9a-f]+)?`\)/g,"")
    .replace(/\s*—\s*`src_[0-9a-f]+`\.?/g,"")
    .replace(/[^.\n]*`models\/source_index\.yaml`\.?/g,"")
    .replace(/\s*sources\/raw\/\S+?\.txt/g,"")
    .replace(/^[^:\n`]+: [a-z_]+ \/ [a-z_]+\.\s*/gm,"")
    .replace(/^[^:\n`]+: `run_[^`]+`\s*$/gm,"");
}
// A finding's heading is its first sentence; the body keeps the rest.
function findingParts(lines) {
  const text=cleanDossierText(lines.join("\n")).trim(), paragraph=text.split(/\n\s*\n/)[0]||"";
  const sentence=/^(.{20,220}?[.!?])(\s|$)/s.exec(paragraph);
  if(!sentence)return {title:shortText(paragraph.replace(/\s+/g," "),160),body:text.slice(paragraph.length)};
  return {title:sentence[1].replace(/\s+/g," "),body:text.slice(sentence[1].length)};
}
function renderDossier(text, toc=null) {
  const doc=parseDossier(text), findings=doc.sections.flatMap(s=>s.findings);
  // Without finding headings (an older dossier.yaml or another format) the plain document stands, its contents list
  // limited to the two upper levels.
  if(!findings.length){
    const entries=toc?[]:null, html=renderMarkdown(text,0,entries);
    if(toc)toc.push(...entries.filter(e=>e.level<=2));
    return `${toc?`<h2>${t("dossier.title")}</h2>`:""}<article class="markdown-document">${html}</article>`;
  }
  const coverageSection=doc.sections.find(s=>s.body.some(line=>COVERAGE_LINE.test(line)));
  const coverage=(coverageSection?.body||[]).map(line=>COVERAGE_LINE.exec(line)).filter(Boolean).map((m,i)=>({
    index:i,question:m[1],state:m[2].trim(),ids:m[3].split(/,\s*/).filter(id=>id.includes("__")),gap:(m[4]||"").trim()}));
  const owner=new Map(coverage.flatMap(row=>row.ids.map(id=>[id,row])));
  const groups=coverage.map(row=>({...row,findings:findings.filter(f=>owner.get(f.id)===row)}));
  const loose=findings.filter(f=>!owner.has(f.id));
  if(loose.length)groups.push({index:groups.length,question:tp("dossier.more_findings"),state:"",gap:"",findings:loose});
  const add=(level,id,label)=>toc?.push({level,id,html:escape(label)});
  const intro=cleanDossierText(doc.intro.join("\n")).trim();
  const others=doc.sections.filter(s=>!s.findings.length&&s!==coverageSection);
  const listItems=s=>s.body.filter(line=>/^\s*[-*]\s/.test(line)).length;
  add(1,"dossier-overview",tp("dossier.overview"));
  let html=`<section class="dossier-overview"><span class="doc-anchor" id="dossier-overview"></span>${doc.title?`<h3>${escape(doc.title)}</h3>`:""}
    <p class="dossier-stats">${t("dossier.stats",{findings:asHtml(`<strong>${findings.length}</strong>`),questions:asHtml(`<strong>${groups.filter(g=>g.findings.length).length}</strong>`)})}</p>
    ${intro?renderMarkdown(intro):""}</section>`;
  if(groups.length){
    add(1,"dossier-questions",tp("dossier.coverage"));
    html+=`<section class="dossier-coverage"><span class="doc-anchor" id="dossier-questions"></span><h4>${t("dossier.coverage")}</h4><ol class="coverage-list">${groups.map(g=>`<li><button type="button" class="link-button" data-scroll="dossier-group-${g.index}">${escape(g.question)}</button>
      <span class="hint">${g.state?`${escape(COVERAGE_STATES[g.state]||g.state)} · `:""}${t("dossier.findings_count",{count:g.findings.length})}</span>${g.gap?`<p class="hint">${markdownInline(g.gap)}</p>`:""}</li>`).join("")}</ol></section>`;
  }
  // Short lists such as the open questions stay open; long ones such as the sources fold.
  const otherSection=s=>{
    const id=`dossier-section-${doc.sections.indexOf(s)}`, body=cleanDossierText(s.body.join("\n")).trim();
    if(!body)return "";
    add(1,id,s.title);
    const long=listItems(s)>8;
    return `<details class="dossier-section" id="${id}"${long?"":" open"}><summary>${escape(s.title)}${long?` · ${listItems(s)}`:""}</summary><div class="markdown-document">${renderMarkdown(body)}</div></details>`;
  };
  html+=others.filter(s=>listItems(s)<=8).map(otherSection).join("");
  add(1,"dossier-findings",tp("dossier.findings"));
  html+=`<section class="dossier-findings"><span class="doc-anchor" id="dossier-findings"></span><h4>${t("dossier.findings")}</h4>${groups.filter(g=>g.findings.length).map(g=>{
    add(2,`dossier-group-${g.index}`,shortText(g.question,70));
    return `<details class="finding-group" id="dossier-group-${g.index}"><summary><span>${escape(g.question)}</span><span class="tag">${t("dossier.findings_count",{count:g.findings.length})}</span></summary>${g.findings.map(f=>{
      const part=findingParts(f.body);
      return `<article class="finding"><h5><span class="finding-kind">${escape(FINDING_KINDS[f.kind]||f.kind)}</span> ${markdownInline(part.title)}</h5><div class="markdown-document">${renderMarkdown(part.body)}</div></article>`;
    }).join("")}</details>`;
  }).join("")}</section>`;
  html+=others.filter(s=>listItems(s)>8).map(otherSection).join("");
  const run=/`(run_[^`]+)`/.exec(doc.intro.join("\n"))?.[1];
  if(run)html+=`<details class="tech-details"><summary>${t("dossier.tech")}</summary><p class="hint">${t("dossier.run",{run})}</p></details>`;
  return html;
}
function researchProviderTag() {
  const choice=project?.job?.text_generation||project?.text, provider=choice?.provider;
  if(provider==="auto")return t("research.tag.auto");
  return t("research.tag.with",{provider:asHtml(provider?providerNames[provider]||escape(provider):"Codex")});
}
// Research: while a run is open the ledger is the page; the finished dossier is a document with a table of contents.
function renderResearch() {
  let html=heading(2);
  if(!project) return html+empty(t("research.empty.title"),t("research.empty.text"),t("research.empty.button"),0,pageIntros.research);
  html+='<div id="stop-card"></div>';
  const run=currentRun(), researching=run?.kind==="research"&&run.status!=="completed";
  const attachments=project.attachments?.length?`<section class="panel"><h2>${t("research.materials")}</h2><ul>${project.attachments.map(row=>`<li>${escape(row.name)}</li>`).join("")}</ul><p class="hint">${t("research.materials_hint")}</p></section>`:"";
  const brief=`<section class="panel"><div class="panel-title"><h2>${t("research.sources")}</h2><span class="tag">${researchProviderTag()}</span></div><p>${t("research.saved_brief",{brief:asHtml(`<strong>${escape(project.config.central_question||project.config.topic)}</strong>`)})}</p><div class="actions">${researching?`<p>${t("research.running_hint")}</p>`:project.research?(project.outline?`<button data-step="2">${t("research.to_outline")}</button>`:`<button data-action="plan" ${disabled()}>${t("research.plan")}</button>`):`<button data-action="research" ${disabled()}>${t("research.start")}</button>`}</div>${(project.research||researching)?`<details class="restart-options"><summary>${t("research.restart")}</summary><p>${t(researching?"research.restart_running":"research.restart_hint")}</p><label class="check"><input type="checkbox" id="seed-corpus" checked> ${t("research.seed_corpus")}</label><button class="secondary" data-action="research" ${disabled()}>${t("research.again")}</button></details>`:`<p class="hint">${t("research.automatic")}</p>`}</section>`;
  // The missing works stand beside a run in progress, and afterwards as long as the list is not empty.
  const works=researching||project.works?.missing?.length||project.works?.provided?.length?worksPanel():"";
  if(researching||(!project.research&&project.job?.progress?.phase==="research"))
    return html+`<div class="split"><div class="split-main"><div id="research-progress"></div>${project.research?`<details class="panel"><summary>${t("research.previous_dossier")}</summary>${renderDossier(project.research)}</details>`:""}</div><aside class="split-rail"><div id="research-live"></div>${brief}${works}${attachments}</aside></div>`;
  if(project.research){
    const toc=[], dossier=renderDossier(project.research,toc);
    return html+`<div class="doc">${tocMarkup(toc,t("research.toc"))}<section class="panel doc-main dossier">${dossier}</section><aside class="doc-rail">${brief}${works}${attachments}</aside></div>`;
  }
  return html+`<div class="split"><div class="split-main">${brief}</div><aside class="split-rail">${attachments}</aside></div>`;
}
// Books and articles the research could not read freely: the editor fetches them from a library or a purchase and
// uploads them here; the run reads each at its next pass and retries its questions (provided_works, 2026-10-01).
function worksPanel() {
  const works=project?.works||{missing:[],provided:[]};
  const states={blocked:tp("works.state.blocked"),retrying:tp("works.state.retrying")};
  const item=(row,index)=>`<li><strong>${escape(row.work)}</strong> <span class="tag">${row.provided?t("works.uploaded"):escape(states[row.state]||"")}</span>
    <p class="hint">${t("works.needed_for",{questions:asHtml(row.tasks.map(task=>escape(task.question)).join(" · "))})}</p>
    ${row.provided?`<p class="hint">${t("works.provided_hint")}</p>`
      :`<div class="actions"><input type="file" id="work-file-${index}" accept=".pdf,.html,.htm,.txt" aria-label="${t("works.file_label",{work:row.work})}"><button class="small" data-action="upload-work" data-work-index="${index}">${t("works.upload")}</button></div>`}</li>`;
  const missing=works.missing||[], others=(works.provided||[]).filter(row=>!missing.some(m=>m.provided===row.id));
  const body=`<p class="hint">${t("works.hint")}</p>
    ${missing.length?`<ul class="works">${missing.map(item).join("")}</ul>`:""}
    ${others.length?`<p><strong>${t("works.others")}</strong> ${others.map(row=>escape(row.citation)).join(" · ")}</p>`:""}
    <details><summary>${t("works.other_upload")}</summary><div class="field"><label for="work-citation-new">${t("works.citation")}</label><input id="work-citation-new" placeholder="${t("works.citation_example")}"></div>
    <div class="actions"><input type="file" id="work-file-new" accept=".pdf,.html,.htm,.txt" aria-label="${t("works.other_file")}"><button class="small secondary" data-action="upload-work" data-work-index="new">${t("works.upload")}</button></div></details>${coreUsage()}`;
  // Nothing missing: one folded line instead of a panel that explains an empty list.
  if(!missing.length)return `<details class="panel" id="works-panel"><summary>${t("works.none")}</summary>${body}</details>`;
  return `<section class="panel" id="works-panel"><h2>${t("works.title",{count:missing.length})}</h2>${body}</section>`;
}
// Today's calls against CORE's daily allowance (sources.core_usage), once any were made.
function coreUsage() {
  const core=project?.server?.core;
  if(!core||!Number(core.calls))return "";
  const full=Number(core.calls)>=Number(core.limit);
  return `<p class="hint">${t("core.usage",{calls:Number(core.calls),limit:Number(core.limit)})}${full?` · ${t("core.full")}`:""}.</p>`;
}
async function uploadWork(button) {
  const index=button.dataset.workIndex, row=index==="new"?null:project.works.missing[Number(index)];
  const file=$(`work-file-${index}`)?.files?.[0];
  const citation=row?row.work:($("work-citation-new")?.value||"").trim();
  if(!file)throw new Error(tp("works.choose_file"));
  if(!citation)throw new Error(tp("works.citation_missing"));
  const query=new URLSearchParams({citation});
  for(const task of row?.tasks||[])query.append("task",task.id);
  button.disabled=true;notice(tp("works.uploading",{work:quoted(citation)}));
  let response;
  try { response=await fetch(`/api/projects/${encodeURIComponent(project.id)}/work?${query}`,{method:"POST",
    headers:{"Content-Type":"application/octet-stream","X-Studio-Token":boot.token},body:file}); }
  finally { button.disabled=false; }
  const result=await response.json().catch(()=>({error:tp("error.unreadable"),message_language:LANG}));
  if(!response.ok){const error=new Error(result.error||tp("works.upload_failed"));error.code=result.code;error.language=result.error?result.message_language:LANG;throw error;}
  project.works=result.works;render();
  notice(tp(row?"works.uploaded_retry":"works.uploaded_other"),"ok");
}
// The table of contents in one plain sentence beside its approval (D-155): episodes, minutes, the opening episode and,
// where the outline's script run has a calibrated projection, about how many calls the writing takes.
function outlineSentence(p) {
  const episodes=p?.episodes||[];
  if(!episodes.length)return t("outline.approve_hint");
  const minutes=Math.round(episodes.reduce((sum,e)=>sum+Number(e.target_minutes||0),0)/episodes.length);
  const own=project?.job?.run?.run_id&&project.job.run.run_id===project?.outline?.run_id;
  const projection=own?project.job.progress?.budget_projection:null;
  const calls=projection?.calibration&&Number.isSafeInteger(projection.expected_remaining_calls)?projection.expected_remaining_calls:0;
  const values={count:episodes.length,minutes,opening:quoted(episodes[0].title)};
  return calls?t("outline.sentence",{...values,calls}):t("outline.sentence_plain",values);
}
function renderOutline() {
  let html=heading(3);
  const outline=project?.outline;
  if(project)html+='<div id="stop-card"></div>';
  const drafting=project?.job?.status==="running"&&jobPage()===PAGE.outline;
  const activity=drafting&&project.job.progress?.activity?`<p><span class="activity-dot" aria-hidden="true"></span>${escape(project.job.progress.activity)} · ${t("nav.hint.since",{elapsed:elapsedText(project.job.started_at)})}</p>`:"";
  // A new draft or a revision replaces the outline, so the page shows no earlier plan meanwhile; a stopped draft
  // arrives without one (Studio.outline_work). 2026-10-02: the replaced plan stayed on the page as if current.
  if(!outline||drafting) {
    if(drafting)return html+(project.job.action==="replan"
      ?`<section class="panel tinted" role="status"><h2>${t("outline.replanning.title")}</h2>${activity}<p>${t("outline.replanning.text")}</p></section>`
      :`<section class="panel tinted"><h2>${t("outline.drafting.title")}</h2>${activity}<p>${t("outline.drafting.text")}</p></section>`);
    return html+empty(t("outline.empty.title"),t("outline.empty.text"),t("outline.empty.button"),PAGE.research,pageIntros.outline);
  }
  const p=outline.plan, approved=outline.approval?.plan_hash===outline.hash;
  html+=`<section class="panel tinted"><h2 class="outline-question">${escape(p.central_question)}</h2><p>${escape(p.explanation_path)}</p><div class="outline-summary"><span>${t("outline.episodes",{count:asHtml(`<strong>${p.episodes.length}</strong>`)})}</span><span>${t("outline.minutes",{minutes:asHtml(`<strong>${Math.round(p.episodes.reduce((s,e)=>s+e.target_minutes,0))}</strong>`)})}</span><span>${t(approved?"outline.approved_state":"outline.awaiting")}</span></div><p class="hint">${escape(p.scope_note)}</p></section>`;
  html+=p.episodes.map((e,i)=>`<section class="panel"><div class="episode-head"><span class="episode-num">${String(i+1).padStart(2,"0")}</span><div><h2>${escape(e.title)}</h2><p>${escape(e.central_question)}</p></div><span class="tag">${t("outline.episode_minutes",{minutes:Math.round(e.target_minutes)})}</span></div><ol class="chapters">${e.scenes.map((s,n)=>`<li><span>${String(n+1).padStart(2,"0")}</span><div><strong>${escape(s.title)}</strong><p>${escape(s.question)}</p><details><summary>${t("outline.explained")}</summary>${s.explanation_steps.map(x=>`<p>${escape(x)}</p>`).join("")}</details></div></li>`).join("")}</ol>${e.deferred_questions.length?`<details><summary>${t("outline.deferred")}</summary>${e.deferred_questions.map(q=>`<p>${escape(q)}</p>`).join("")}</details>`:""}</section>`).join("");
  const canReplan = !Object.entries(project.job?.run?.stages || {}).some(([n,r])=>n!=="planning"&&r.attempts>0);
  html+=approved?`<section class="panel tinted"><h2>${t("outline.approved.title")}</h2><p>${t("outline.approved.text")}</p><button data-step="${PAGE.production}">${t("outline.approved.button")}</button></section>`:
    `<div class="action-bar"><details class="action-note"><summary>${t("outline.feedback.summary")}</summary>${area("outline-feedback",t("outline.feedback.label"),"",3)}</details><div class="actions"><button class="secondary" data-action="replan" ${running()||!canReplan?"disabled":""}>${t("outline.replan")}</button><button data-action="script" ${disabled()}>${t("outline.approve")}</button></div><p class="hint">${outlineSentence(p)}</p></div>`;
  if(!canReplan)html+=`<details class="restart-options"><summary>${t("outline.new.summary")}</summary><p>${t("outline.new.text")}</p><button class="secondary" data-action="plan" ${disabled()}>${t("button.new_outline")}</button></details>`;
  return html;
}
function renderProduction() {
  const html=heading(4);
  const run=currentRun();
  if(!approvedOutline()&&!hasProduction(run))return html+empty(t("production.empty.title"),t("production.empty.text"),t("production.empty.button"),PAGE.outline,pageIntros.production);
  return html+'<div id="stop-card"></div><div id="production-progress"></div>';
}
function renderProductionDetails() {
  const run=scriptRun();
  const active=project?.job?.status==="running"&&jobPage()===PAGE.production;
  const finished=scriptsFinished();
  const labels=Object.fromEntries(["completed","running","pending","blocked","failed","waiting_for_quota","interrupted"].map(id=>[id,t(`production.status.${id}`)]));
  let html=`<section class="panel"><div class="panel-title"><h2>${t("production.title")}</h2><span class="tag">${t(active?"nav.state.running":finished?"production.ready":"production.saved_state")}</span></div><ol class="production-stages">${productionStages.map(([key,title,description])=>{
    const record=run?.stages?.[key];
    // A stopped stage goes back to pending with the interruption noted; it resumes, it does not simply follow.
    const status=record?.status==="pending"&&record?.error?.code==="interrupted"?"interrupted":record?.status||(finished?"completed":"pending");
    return `<li class="${escape(status)}"><span class="phase-marker" aria-hidden="true">${status==="completed"?"✓":status==="running"?"●":status==="pending"?"○":"!"}</span><div><strong>${title}</strong><p>${description}</p></div><span class="phase-status">${labels[status]||t("production.status.open")}</span></li>`;
  }).join("")}${renderExpressionStage(finished)}</ol><p class="hint">${t("production.hint")}</p></section>`;
  html+=renderScriptProgress(project?.job?.progress,active);
  const readable=readableScripts().length;
  if(!finished&&readable)html+=`<section class="panel tinted"><h2>${t("production.readable",{count:readable})}</h2><p>${t("production.readable_text")}</p><button data-step="${PAGE.scripts}">${t("production.read_now")}</button></section>`;
  const issues=project?.job?.progress?.review_issues||[];
  const issueEpisode=project?.job?.progress?.issues_episode;
  if(issues.length)html+=`<section class="panel"><h2>${t(project?.job?.progress?.stage==="review"?"production.issues.review":"production.issues.teaching")}${issueEpisode?` · ${escape(issueEpisode)}`:""}</h2><ul>${issues.map(issue=>`<li>${escape(typeof issue==="string"?issue:issue.reason||tp("production.issue_open"))}</li>`).join("")}</ul></section>`;
  if(project?.job?.research_gaps?.length)html+=`<section class="panel"><h2>${t("production.gaps")}</h2><ul>${project.job.research_gaps.map(g=>`<li><strong>${escape(g.question)}</strong><p>${escape(g.why_needed)}</p></li>`).join("")}</ul><button class="secondary" data-step="${PAGE.research}">${t("production.gaps_button")}</button></section>`;
  if(finished)html+=`<section class="panel tinted"><h2>${t("production.finished.title")}</h2><p>${t("production.finished.text")}</p><button data-step="${PAGE.scripts}">${t("production.finished.button")}</button></section>`;
  else if(active)html+=`<p class="hint">${t("production.active_hint")}</p>`;
  return html;
}
function episodePicker() { return `<div class="field"><label for="episode-select">${t("audio.pick_episode")}</label><select id="episode-select">${project.episodes.map((e,i)=>`<option value="${i}" ${i===episodeIndex?"selected":""}>${i+1}. ${escape(e.script.title)}</option>`).join("")}</select></div>`; }
function readerEntries() {
  const entries=readableScripts();
  if(readingSnapshot?.projectId===project?.id&&!entries.some(e=>e.script.episode_id===readingSnapshot.entry.script.episode_id))entries.push(readingSnapshot.entry);
  return entries;
}
// A read mark holds the script state it was set for: a revised episode reads as unread again.
const isRead=e=>!e.preview&&!!e.hash&&project?.reader_state?.read?.[e.script.episode_id]===e.hash;
// The reader bar: previous and next episode around the picker, which names each episode in full; the state shows only
// where it differs from a published one, and read episodes are marked (D-161).
function renderReaderControls() {
  const entries=readerEntries(), e=readingSnapshot.entry;
  const index=Math.max(0,entries.findIndex(row=>row.script.episode_id===e.script.episode_id));
  const latest=readableScripts().find(row=>row.script.episode_id===e.script.episode_id);
  const updated=latest&&readerVersion(latest)!==readerVersion(e);
  const read=entries.filter(isRead).length;
  const label=(row,i)=>`${i+1}. ${escape(row.script.title)}${row.preview?` · ${scriptStateLabels[row.state]}`:""}${isRead(row)?` · ${t("reader.read_mark")}`:""}`;
  const neighbour=(delta,text)=>{const row=entries[index+delta];return `<button type="button" class="secondary small" data-reader-episode="${row?escape(row.script.episode_id):""}" ${row?"":"disabled"}>${text}</button>`;};
  return `<div class="reader-nav">${neighbour(-1,t("reader.previous"))}<div class="field"><label for="script-select">${t("reader.position",{index:index+1,total:entries.length})}${read?` · ${t("reader.read_count",{count:read})}`:""}</label><select id="script-select">${entries.map((row,i)=>`<option value="${escape(row.script.episode_id)}" ${row.script.episode_id===e.script.episode_id?"selected":""}>${label(row,i)}</option>`).join("")}</select></div>${neighbour(1,t("reader.next"))}</div>
    ${e.preview?`<p><span class="tag">${t("reader.opened",{state:scriptStateLabels[e.state]})}</span></p>`:""}
    ${updated?`<p class="note">${t("reader.updated")}</p><button class="secondary small" data-action="refresh-script">${t("reader.load_current")}</button>`:""}
    ${e.preview?`<p class="note">${t(e.state==="draft"?"reader.preview.draft":e.state==="polished"?"reader.preview.polished":"reader.preview.reviewed")} ${t("reader.preview.tail")}</p>`:""}
    ${scriptsFinished()?"":`<p class="hint">${t("reader.more")}</p>`}`;
}
const reviewNoteLabels=Object.fromEntries(["script_review","teaching_review","editorial_review","dialogue_polish","dismissed_gaps","advisories"].map(id=>[id,tp(`review_note.${id}`)]));
// Inline audio tags a Gemini recording speaks (episode_audio.tag_episode), shown where the reader reads the script.
function expressionActive() {
  const a=currentAudio();
  return isGemini(a.provider)&&a.expression!==false;
}
const EXPRESSION_TAG=/<[^<>\n]{1,40}>/g;
// The twelve kinds a recording may use (expression.ALLOWED_TAGS), in the reader's words.
// Each tag with the catalog id of its name (D-152); test_expression reads the tags from this table.
const EXPRESSION_KINDS=Object.fromEntries(Object.entries({"<laugh>":"laugh","<chuckle>":"chuckle","<giggle>":"giggle","<breath>":"breath",
  "<exhales>":"exhales","<sigh>":"sigh","<phew>":"phew","<gasp>":"gasp","<tsk>":"tsk","<throat-clearing>":"throat_clearing",
  "<short pause>":"short_pause","<long pause>":"long_pause"}).map(([tag,id])=>[tag,tp(`expression.kind.${id}`)]));
// The other host's short reaction in a Google recording (expression.BACKCHANNEL), spoken in the other voice.
const BACKCHANNEL=/\|[^|<>\n]{1,20}\|/g;
function expressionKinds(x) {
  const counts={};
  for(const row of x.segments||[]){
    for(const tag of row.text.match(EXPRESSION_TAG)||[])counts[tag]=(counts[tag]||0)+1;
    for(const _ of row.text.match(BACKCHANNEL)||[])counts["|"]=(counts["|"]||0)+1;
  }
  return Object.entries(counts).sort((a,b)=>b[1]-a[1]).map(([tag,n])=>`${n}× ${escape(tag==="|"?tp("expression.backchannel"):EXPRESSION_KINDS[tag]||tag)}`).join(", ");
}
function markTags(text) {
  return escape(text).replace(/&lt;[^&<>]{1,40}&gt;/g,tag=>`<mark class="expression-tag">${tag}</mark>`)
    .replace(/\|[^|<>&\n]{1,20}\|/g,reaction=>`<mark class="expression-tag" title="${t("expression.backchannel_title")}">${reaction}</mark>`);
}
function expressiveText(segment, expression) {
  const row=expression?.segments?.find(r=>r.segment_id===segment.segment_id);
  if(!row)return `<p>${escape(segment.text)}</p>`;
  const words=text=>text.replace(EXPRESSION_TAG," ").replace(BACKCHANNEL," ").split(/\s+/).filter(Boolean).join(" ");
  // A segment read with a spoken form shows the tags on what is spoken, below the written text.
  if(words(row.text)===words(segment.text))return `<p>${markTags(row.text)}</p>`;
  return `<p>${escape(segment.text)}</p><p class="hint">${t("expression.spoken",{text:asHtml(markTags(row.text))})}</p>`;
}
function renderExpressionPanel(e) {
  if(!expressionActive())return "";
  const x=e.expression, id=escape(e.script.episode_id);
  const body=x?`<p>${t("expression.count",{tags:Number(x.tags),segments:x.segments.length})}${x.tags?`: ${expressionKinds(x)}`:""}. ${t("expression.how")}</p>${x.rejected?`<p class="hint">${t("expression.rejected")}</p>`:""}
      <button class="secondary" data-action="expression" data-episode="${id}" ${disabled()}>${t("expression.redo")}</button><p class="hint">${t("expression.redo_hint")}</p>`:
    `<p>${t("expression.none")}</p>
      <div class="actions"><button class="secondary" data-action="expression" data-episode="${id}" ${disabled()}>${t("expression.place")}</button><button class="quiet" data-action="expression" ${disabled()}>${t("common.all_episodes")}</button></div>
      <p class="hint">${t("expression.none_hint")}</p>`;
  return `<section class="panel"><h2>${t("expression.title")}</h2>${body}</section>`;
}
// The step after publishing for a Gemini project: the tags are placed so the reader sees them (tag_episodes).
function renderExpressionStage(finished) {
  if(!expressionActive())return "";
  const published=project?.episodes||[], tagged=published.filter(e=>e.expression).length, p=project?.expression_progress;
  const running=p?.status==="running"&&project?.job?.status==="running";
  const status=running?"running":published.length&&tagged===published.length?"completed":finished&&published.length?"open":"pending";
  const label={running:t("expression.stage.running",{done:Number(p?.done||0),total:Number(p?.total||0)}),completed:t("expression.stage.completed",{done:tagged,total:published.length}),
    open:t("expression.stage.open",{count:published.length-tagged,page:quoted(tp("nav.step.scripts"))}),pending:t("production.status.pending")}[status];
  return `<li class="${status==="open"?"blocked":status}"><span class="phase-marker" aria-hidden="true">${status==="completed"?"✓":status==="running"?"●":status==="open"?"!":"○"}</span><div><strong>${t("expression.title")}</strong><p>${t("expression.stage.text",{kinds:Object.values(EXPRESSION_KINDS).join(", ")})}</p></div><span class="phase-status">${label}</span></li>`;
}
// A deterministic advisory in the page's language (D-153): worded by its code from the values the server sends
// (script_advisories, params); a row saved before 2026-10-07, or of a code the catalog does not know, keeps its German
// sentence.
function advisoryText(row) {
  const key=`advisory.${row.code}`;
  if(!row.params||typeof row.params!=="object"||!hasText(key))return String(row.detail??"");
  return tp(key,Object.fromEntries(Object.entries(row.params).map(([name,value])=>[name,typeof value==="number"?fmt.number(value,{maximumFractionDigits:1}):String(value??"")])));
}
function renderReviewNotes(entry) {
  // The published report is the only source; a preview has been through no full review yet.
  const notes=entry.preview?null:project.episodes.find(row=>row.script.episode_id===entry.script.episode_id)?.review_notes;
  const groups=Object.keys(reviewNoteLabels).filter(name=>notes?.[name]?.length);
  if(!groups.length) return "";
  const line=(name,row)=>name==="dismissed_gaps"?`<strong>${escape(row.gap)}</strong><p>${escape(row.reason)}</p>`
    :name==="advisories"?`<strong>${escape(row.code)}</strong><p>${escape(advisoryText(row))}${row.segment_ids?.length?` (${row.segment_ids.map(escape).join(", ")})`:""}</p>`
    :`<p>${escape(row)}</p>`;
  return `<details class="panel review-notes"><summary>${t("review_notes.title")}</summary>
    <p class="hint">${t("review_notes.hint")}</p>
    ${groups.map(name=>`<h3>${escape(reviewNoteLabels[name])}</h3><ul>${notes[name].map(row=>`<li>${line(name,row)}</li>`).join("")}</ul>`).join("")}</details>`;
}
// The speakers as the reader hears them: the host names when set, otherwise the voice each host speaks with in this
// episode (swapped in an even one when the roles alternate), so "Host A" says who it is.
function hostLabels(episodeId=null) {
  const names=project?.config?.host_names||{}, a=currentAudio();
  const voices=a?.voices?(episodeId?episodeVoices(a,episodeId):a.voices):{};
  const fallback=(role,letter)=>voices?.[role]?`${voices[role]} · Host ${letter}`:`Host ${letter}`;
  return {host_a:names.host_a||fallback("host_a","A"),host_b:names.host_b||fallback("host_b","B")};
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
  return `<details class="spoken-override"><summary>${t("spoken.title")}${current?` · ${t("spoken.set")}`:""}</summary>
    <p class="hint">${t("spoken.hint")}</p>
    ${area("spoken-"+segment.segment_id,t("spoken.label"),current||applySpokenForms(segment.text,project.spoken_forms),3)}
    <div class="actions"><button class="secondary small" data-action="spoken-override" data-episode="${escape(episodeId)}" data-segment="${escape(segment.segment_id)}" ${disabled()}>${t("spoken.save")}</button>
    <button class="secondary small" data-action="audio" data-rerender="true" data-episode="${escape(episodeId)}" ${disabled()}>${t("spoken.rerender")}</button></div></details>`;
}
function audioRequest(episodeId, rerender=false) {
  // From the reading page the saved approval stands: script hash and voices are unchanged and
  // only a spoken form differs, so the re-render is not a new editorial decision. The server
  // checks the saved approval by the pipeline's own rule before it starts anything.
  const e=episodeId?project.episodes.find(row=>row.script.episode_id===episodeId):project.episodes[episodeIndex];
  return {episode:e.script.episode_id,approve_audio:!rerender&&!!$("audio-approval")?.checked,
    // An approval covers the tags the reader saw; a re-render keeps the saved approval and is bound to the tags on
    // the reader's page as well, since it speaks them (2026-10-02).
    ...(rerender?{rerender:true}:{}),expression_hash:e.expression?.hash||"",script_hash:e.hash,readable_hash:e.readable_hash,
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
  notice(tp(spoken?"spoken.saved":"spoken.none"),"ok");
}
async function saveSpeechSettings() {
  const hostA=$("host-name-a").value.trim(), hostB=$("host-name-b").value.trim();
  if(!!hostA!==!!hostB)throw new Error(tp("speech.hosts_both"));
  const names=project.config?.host_names||{};
  const changes=[];
  if(hostA!==(names.host_a||"")||hostB!==(names.host_b||""))changes.push("config");
  if(JSON.stringify(parseSpokenForms($("spoken-forms").value).entries)!==JSON.stringify(project.spoken_forms?.entries||[]))changes.push("audio");
  if(!confirmPaused(changes))return;
  await api(`/api/projects/${project.id}/save`,{config:{...project.config,host_names:hostA?{host_a:hostA,host_b:hostB}:null},
    config_hash:project.config_hash,text:project.text,audio_settings:currentAudio(),
    audio_hash:project.audio_hash,spoken_forms:parseSpokenForms($("spoken-forms").value),
    spoken_forms_hash:project.spoken_forms_hash});
  project=await api(`/api/projects/${project.id}`);render();
  notice(tp("speech.saved"),"ok");
}
function refreshScriptReader() {
  if(!readingSnapshot||!$("script-reader-controls")){$("content").innerHTML=renderScript();return;}
  // Only the picker and status change. Keep the text DOM, selection and feedback intact.
  $("script-reader-controls").innerHTML=renderReaderControls();
  syncReaderOffset();
}
// Reading: a sticky reader bar, chapters as a table of contents, the text in a measured column, notes in the margin.
function renderScript() {
  let html=heading(5);
  const entries=readerEntries();
  if(!entries.length) return html+(approvedOutline()||hasProduction(currentRun())?
    empty(t("script.empty.waiting.title"),t("script.empty.waiting.text"),t("script.empty.waiting.button"),PAGE.production,pageIntros.scripts):
    empty(t("script.empty.outline.title"),t("script.empty.outline.text"),t("production.empty.button"),PAGE.outline,pageIntros.scripts));
  const selected=entries.find(e=>e.script.episode_id===scriptEpisodeId)||entries[0];
  scriptEpisodeId=selected.script.episode_id;
  if(readingSnapshot?.projectId!==project.id||readingSnapshot.entry.script.episode_id!==scriptEpisodeId)readingSnapshot={projectId:project.id,entry:structuredClone(selected)};
  const e=readingSnapshot.entry,s=e.script;
  if(!e.preview)episodeIndex=Math.max(0,project.episodes.findIndex(row=>row.script.episode_id===s.episode_id));
  const published=e.preview?null:project.episodes.find(row=>row.script.episode_id===s.episode_id);
  const spoken=published?.audio?.length?(published.spoken_overrides||{}):null;
  const expression=expressionActive()?published?.expression:null;
  const toc=s.chapters.map((chapter,i)=>({level:1,id:`ch-${chapter.chapter_id}`,html:`${i+1}. ${escape(chapter.title)}`}));
  const speakers=hostLabels(s.episode_id);
  let text=`<h2 class="reader-title">${escape(s.title)}</h2>`;
  for(const chapter of s.chapters) text+=`<span class="doc-anchor" id="ch-${escape(chapter.chapter_id)}"></span><h3>${escape(chapter.title)}</h3>`+s.segments.filter(x=>x.chapter_id===chapter.chapter_id).map(x=>`<div class="utterance ${x.speaker_id}"><strong>${escape(speakers[x.speaker_id])}</strong>${expressiveText(x,expression)}${spoken?renderSpokenOverride(s.episode_id,x,spoken):""}</div>`).join("");
  // The end of an episode marks it read and leads to the next one.
  const entries2=readerEntries(), next=entries2[entries2.findIndex(row=>row.script.episode_id===s.episode_id)+1];
  const readButton=e.preview?"":`<button type="button" class="secondary small" data-action="mark-read" data-episode="${escape(s.episode_id)}">${t(isRead(e)?"reader.unmark":"reader.mark")}</button>`;
  text+=`<div class="reader-end"><p class="hint">${t("reader.end")}</p><div class="actions">${readButton}${next?`<button type="button" class="small" data-reader-episode="${escape(next.script.episode_id)}" data-mark-read="${e.preview?"":escape(s.episode_id)}">${t("reader.next_episode")}</button>`:""}</div></div>`;
  // What the editor decides stands first in the margin, above the facts and the review notes (D-161): before, the
  // approval sat at the foot of a panel that scrolled on its own.
  const feedback=e.preview?`<section class="panel"><p class="hint">${t("reader.preview_feedback")}</p><button class="secondary" data-step="${PAGE.production}">${t("reader.follow_production")}</button></section>`
    :`<section class="panel"><h2>${t("reader.feedback.title")}</h2><div class="actions"><button data-step="${PAGE.audio}">${t("reader.to_audio")}</button>${readButton}</div>${area("script-feedback",t("reader.feedback.label"),"",3)}<div class="actions"><button class="secondary" data-action="revise" ${disabled()}>${t("reader.revise")}</button></div><p class="hint">${t("reader.revise_hint")}</p></section>`;
  html+=`<div id="script-reader-controls" class="reader-bar">${renderReaderControls()}</div><div class="doc reader-doc">${tocMarkup(toc,t("reader.chapters"))}<article id="script-text" class="panel reader doc-main">${text}</article><aside class="doc-rail">${feedback}<div class="outline-summary"><span>${t("reader.words",{words:fmt.number(e.metrics.words)})}</span><span>${t("reader.minutes",{minutes:Math.round(e.metrics.estimated_minutes)})}</span><span>${t("reader.chapter_count",{count:s.chapters.length})}</span></div>${published?renderExpressionPanel(published):""}${renderReviewNotes(e)}</aside></div>`;
  return html;
}
// The sticky reader bar's height, so the chapter list and the margin stick below it instead of under it.
function syncReaderOffset() {
  const bar=$("script-reader-controls");
  document.documentElement?.style?.setProperty?.("--reader",`${bar?.offsetHeight||0}px`);
}
const NEWLINE=String.fromCharCode(10);
const pronunciationLabels=Object.fromEntries(["numbers","abbreviations","versions","foreign"].map(id=>[id,tp(`pronunciation.${id}`)]));
function renderPronunciation(e) {
  // Model-free, computed by the server from the published text, the table and the overrides,
  // so it is available before the first audio run and belongs above the approval.
  const report=e.pronunciation;
  if(!report?.flagged||!Object.keys(report.flagged).length)return "";
  return `<details class="pronunciation"><summary>${t("pronunciation.title",{count:Object.values(report.flagged).reduce((n,rows)=>n+rows.length,0)})}</summary>
    <p class="hint">${t("pronunciation.hint")}</p>
    ${Object.entries(report.flagged).map(([name,rows])=>`<h3>${escape(pronunciationLabels[name]||name)}</h3><ul>${rows.map(row=>`<li><strong>${escape(row.token)}</strong> · ${row.count}× (${row.segment_ids.map(escape).join(", ")})</li>`).join("")}</ul>`).join("")}
    ${Object.keys(report.applied||{}).length?`<h3>${t("pronunciation.applied")}</h3><ul>${Object.entries(report.applied).map(([written,count])=>`<li><strong>${escape(written)}</strong> · ${count}×</li>`).join("")}</ul>`:""}</details>`;
}
function spokenFormsText(table) { return (table?.entries||[]).map(row=>`${row.written} = ${row.spoken}`).join(NEWLINE); }
function parseSpokenForms(text) {
  return {schema_version:"1.0",entries:String(text).split(NEWLINE).map(line=>line.split("=")).filter(parts=>parts.length>=2)
    .map(parts=>({written:parts[0].trim(),spoken:parts.slice(1).join("=").trim()})).filter(row=>row.written&&row.spoken)};
}
// A key field wherever a key is missing, not only on the first page. The key stays in the server's memory.
function inlineKey(id,resume=false,target="",kind="openrouter",note=true) {
  return `<div class="inline-key">${textInput(id,KEY_NAMES[kind],"","password")}<button class="secondary small" data-action="store-key" data-key-field="${id}" data-key-kind="${kind}"${resume?` data-then-resume="1" ${target}`:""}>${t(resume?"key.store_resume":"key.store")}</button>${note?`<p class="hint">${t("key.inline_hint")}</p>`:""}</div>`;
}
// Runs are bound to their inputs: saving what a paused run depends on ends its resumability. The warning
// names that before the save. config = brief and host names, notes = style notes, audio = pauses and spoken forms.
const BINDS={config:["research","script","episode_audio"],notes:["script"],audio:["episode_audio"]};
const RUN_LABELS=Object.fromEntries(["research","script","episode_audio"].map(kind=>[kind,tp(`run_label.${kind}`)]));
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
  return hit.length?`<p class="note">${t("paused.hint",{runs:hit.map(k=>RUN_LABELS[k]).join(", ")})}</p>`:"";
}
function confirmPaused(changes) {
  const hit=affectedRuns(changes);
  if(!hit.length||typeof window.confirm!=="function")return true;
  return window.confirm(tp("paused.confirm",{runs:hit.map(k=>RUN_LABELS[k]).join(", ")}));
}
// Whether applying the partner's proposal rewrites the saved brief, which every run is bound to.
function proposalChangesBrief() {
  const {proposal}=setupSelection();
  if(!proposal||project?.proposal_applied)return false;
  const c=project?.config||{};
  const differs=key=>JSON.stringify(proposal[key]??null)!==JSON.stringify(c[key]??null);
  if(["topic","central_question","prior_knowledge","depth_request","focus_questions","excluded_topics","target_total_minutes"].some(differs))return true;
  if(["language","seed_urls","series_goal"].some(key=>proposal[key]!=null&&differs(key)))return true;
  // recency_months 0 in a proposal removes a saved rule.
  if(proposal.recency_months!=null&&(proposal.recency_months||null)!==(c.recency_months??null))return true;
  const a=proposal.audio_settings;
  return !!(a&&a.provider==="qwen3_local"&&JSON.stringify(a.voices)!==JSON.stringify(c.voice_profile));
}
function renderStyleNotes() {
  return `<details class="panel style-notes"><summary>${t("notes.title")}</summary>
    <p class="hint">${t("notes.hint")}</p>
    ${area("style-notes",t("notes.label"),project.style_notes||"",6)}
    ${pausedHint(["notes"])}
    <div class="actions"><button class="secondary" data-action="save-notes" ${disabled()}>${t("notes.save")}</button></div></details>`;
}
function renderSpeechSettings(a) {
  const names=project.config?.host_names||{};
  return `<details class="panel speech-settings"><summary>${t("speech.title")}</summary>
    <p class="hint">${t("speech.hint")}</p>
    ${area("spoken-forms",t("speech.forms_label"),spokenFormsText(project.spoken_forms),5)}
    <p class="hint">${t("speech.forms_hint")}</p>
    <div class="row">${textInput("host-name-a",t("speech.host_a"),names.host_a||"")}${textInput("host-name-b",t("speech.host_b"),names.host_b||"")}</div>
    <p class="hint">${t("speech.hosts_hint")}</p>
    ${pausedHint(["config","audio"])}
    <div class="actions"><button class="secondary" data-action="save-speech" ${disabled()}>${t("speech.save")}</button></div></details>`;
}
function renderListeningReview(e) {
  return `<section class="panel"><h2>${t("listening.title")}</h2>
    <p class="hint">${t("listening.hint")}</p>
    <label class="approval"><input id="listening-done" type="checkbox" ${e.human_listening_reviewed?"checked":""}><span>${t("listening.done")}</span></label>
    ${area("listening-note",t("listening.label"),e.listening_note||"",3)}
    <div class="actions"><button class="secondary" data-action="listening-review" ${disabled()}>${t("listening.save")}</button></div></section>`;
}
// Audio: the approval is a checks card that names exactly what it binds; the recordings follow on the same page.
// Approved Gemini recordings wait here for a free place (Studio.start_queued starts them in approval order).
function audioWouldQueue() {
  const active=(project.audio_jobs||[]).filter(j=>j.status==="running");
  return project.audio_capacity?.available===0||(project.job?.status==="running"&&!active.some(j=>j.id===project.job.id));
}
function queuedEntry(episode) { return (project.audio_queue||[]).find(row=>row.episode===episode); }
// A queued recording waits for a place, or for the key, which lives only in the server's memory (2026-10-02: after a
// restart the queue still said it waited for a place while the scheduler stopped at the missing key).
const queueKey=row=>row.key||keyOf(currentAudio().provider);
const queueWaitsForKey=row=>!row.error&&(row.waiting==="key"||(row.waiting===undefined&&!keyAvailable(queueKey(row))));
// A server message as the page shows it: as written in the page's language, otherwise named by its code and quoted
// as the original (D-152). Messages of older servers name no language and are German.
const shownMessage=(message,language,code)=>sameLanguage({message_language:language})?String(message??""):tp("error.foreign",{code:code||"?",message});
const queueError=row=>shownMessage(row.error,row.error_language,row.error_code);
function renderQueueState(e) {
  const row=queuedEntry(e.script.episode_id);
  if(!row)return "";
  const remove=`<button class="quiet small" data-action="unqueue" data-episode="${escape(row.episode)}">${t("queue.remove")}</button>`;
  return row.error?`<p class="note">${t("queue.refused",{error:queueError(row)})} ${remove}</p>`:
    queueWaitsForKey(row)?`<p class="note">${t(`queue.waits_key.${queueKey(row)}`,{position:Number(row.position)})} ${remove}</p>`:
    `<p class="hint">${t("queue.place",{position:Number(row.position)})} ${remove}</p>`;
}
// What a recording sends to speech, from the server's estimate per episode: no price, it cannot be checked offline.
function speechSize(rows) {
  const sized=rows.filter(e=>e?.speech&&Number.isFinite(Number(e.speech.characters)));
  if(!sized.length)return "";
  const minutes=sized.reduce((sum,e)=>sum+Number(e.speech.minutes||0),0), chars=sized.reduce((sum,e)=>sum+Number(e.speech.characters),0);
  return tp("audio.size",{minutes:fmt.number(Math.max(1,Math.round(minutes))),characters:fmt.number(chars)});
}
const SPEECH_SIZE_NOTE=tp("audio.size_note");
function renderAudioQueue() {
  const rows=project?.audio_queue||[];
  if(!rows.length)return "";
  const title=id=>project.episodes.find(e=>e.script.episode_id===id)?.script.title||id;
  const waiting=rows.find(queueWaitsForKey), missing=waiting?queueKey(waiting):null;
  const size=speechSize(rows.filter(row=>!row.error).map(row=>project.episodes.find(e=>e.script.episode_id===row.episode)));
  return `<section class="panel"><h2>${t("queue.title",{count:rows.length})}</h2><ol>${rows.map(row=>`<li><strong>${escape(title(row.episode))}</strong> · ${row.error?t("queue.row.refused",{error:queueError(row)}):queueWaitsForKey(row)?t(`queue.row.key.${queueKey(row)}`):t("queue.row.place")} <button class="quiet small" data-action="unqueue" data-episode="${escape(row.episode)}">${t("common.remove")}</button></li>`).join("")}</ol>${missing?`<p class="note">${t(`queue.key_missing.${missing}`)}</p>`:""}${size?`<p class="hint">${t("queue.size",{size})} ${t("audio.size_note")}</p>`:""}<p class="hint">${t("queue.hint")}</p></section>`;
}
// Every published episode without a current recording, not recording and not queued: what "approve all" covers.
function pendingRecordings() {
  const active=new Set((project.audio_jobs||[]).filter(j=>j.status==="running").map(j=>j.episode));
  return (project.episodes||[]).filter(e=>!e.audio_current&&!active.has(e.script.episode_id)&&!queuedEntry(e.script.episode_id));
}
// What an audio approval confirms and who then speaks, in one plain sentence (D-155); an API voice adds that the key
// pays for it, Qwen on this computer does not.
function audioSentence(a,voices,count) {
  return t(isGemini(a.provider)?"audio.sentence.api":"audio.sentence.local",{count,provider:audioLabel(a),voices:fmt.list([voices.host_a,voices.host_b])});
}
function renderApproveAll(remote) {
  const rows=pendingRecordings();
  if(!remote||rows.length<2)return "";
  // Why the button waits, and the key field when that is the reason (the key lives only in the server's memory).
  const blocked=audioBlockReason(undefined,true);
  const size=speechSize(rows);
  // The approval is the highlighted button and its checkbox is never ticked for the editor; reading first stays beside it.
  return `<section class="panel"><h2>${t("approve_all.title",{count:rows.length})}</h2><ul>${rows.map(e=>`<li>${escape(e.script.title)}${e.expression?` · ${t("audio.tags",{count:Number(e.expression.tags)})}`:""}${speechSize([e])?` · ${escape(speechSize([e]))}`:""}</li>`).join("")}</ul>
    ${size?`<p><strong>${t("approve_all.size_label")}</strong> ${escape(size)}. <span class="hint">${SPEECH_SIZE_NOTE}</span></p>`:""}
    <p>${audioSentence(currentAudio(),currentAudio().voices,rows.length)}</p>
    <label class="approval"><input id="audio-approve-all" type="checkbox"><span>${t("approve_all.confirm",{count:rows.length})}</span></label>
    <div class="actions"><button data-action="audio-all" ${blocked?"disabled":""}>${t("approve_all.button",{count:rows.length})}</button><button class="secondary" data-step="${PAGE.scripts}">${t("approve_all.read_first")}</button></div>
    ${blocked?`<p class="hint">${escape(blocked)}${!keyAvailable(keyOf(currentAudio().provider))?` ${t("approve_all.key_field")}`:""}</p>`:""}
    <p class="hint">${t("approve_all.hint")}</p></section>`;
}
function renderApprovalCard(e,a,remote) {
  const blocked=audioBlockReason();
  const pauses=a.pauses||{same_speaker_ms:250,speaker_change_ms:450,chapter_break_ms:900};
  const pace=paceOf(a,project.config.language);
  const pronunciation=renderPronunciation(e);
  const google=a.provider==="google_gemini_tts", voiced=episodeVoices(a,e.script.episode_id), key=keyOf(a.provider);
  const voices=google?`${t("approval.voices_google",{explains:voiced.host_a,asks:voiced.host_b})}${a.alternate_roles?` · ${t("approval.alternating")}`:""}`:`${escape(a.voices.host_a)} & ${escape(a.voices.host_b)}`;
  return `<div class="panel-title"><h2>${escape(e.script.title)}</h2><span class="tag">${escape(audioLabel(a))}</span></div>
    <dl>
    <dt>${t("approval.text")}</dt><dd>${e.audio?.length?(e.audio_current?t("approval.text.current"):t("approval.text.outdated",{changes:staleText(e.audio_stale)||tp("approval.text.changes_default")})):t("approval.text.unvoiced")} <button class="quiet small" data-step="4">${t("approval.read_script")}</button></dd>
    <dt>${t("summary.voices")}</dt><dd>${voices} · ${languageName(project.config.language)} <button class="quiet small" data-action="open-settings">${t("approval.change_voices")}</button></dd>
    <dt>${t("settings.audio.provider")}</dt><dd class="hint">${t(google?"approval.provider.google":remote?"approval.provider.openrouter":"approval.provider.qwen")} ${t("approval.provider.tail")}</dd>
    ${remote&&speechSize([e])?`<dt>${t("approval.size")}</dt><dd>${escape(speechSize([e]))} <span class="hint">${SPEECH_SIZE_NOTE}</span></dd>`:""}
    ${remote&&!keyAvailable(key)?`<dt>${t("approval.access")}</dt><dd>${inlineKey("audio-key",false,"",key)}</dd>`:""}
    <dt>${t("approval.pronunciation")}</dt><dd>${pronunciation||`<span class="hint">${t("approval.pronunciation_none")}</span>`}</dd>
    ${remote&&a.expression!==false?`<dt>${t("approval.expression")}</dt><dd>${e.expression?`${t("audio.tags",{count:Number(e.expression.tags)})}${google?` · ${t("approval.backchannels",{count:Number(e.expression.backchannels||0)})}`:""} · ${t("approval.expression_marked")}`:t("approval.expression_none")} <button class="quiet small" data-step="${PAGE.scripts}">${t("approval.see_in_script")}</button></dd>`:""}
    <dt>${t("approval.pauses")}</dt><dd class="hint">${t("approval.pauses_text",{same:Number(pauses.same_speaker_ms),change:Number(pauses.speaker_change_ms),chapter:Number(pauses.chapter_break_ms)})}</dd>
    ${pace.tempo!==1||(google&&pace.unhurried)?`<dt>${t("approval.tempo")}</dt><dd class="hint">${Math.round(pace.tempo*100)} %${google&&pace.unhurried?` · ${t("approval.unhurried")}`:""} · ${t(project.config.language==="de-DE"?"approval.tempo_for.de":"approval.tempo_for.en")}</dd>`:""}
    </dl>
    <p>${audioSentence(a,voiced,1)}</p>
    <label class="approval"><input id="audio-approval" type="checkbox" ${blocked?"disabled":""}><span>${t("approval.confirm")}${remote?` ${t("approval.confirm_api")}`:""}</span></label>
    ${renderQueueState(e)}
    <div class="actions"><button id="audio-start" data-action="audio" disabled>${t(remote&&audioWouldQueue()?"approval.queue":"approval.start")}</button><button class="secondary" data-step="${PAGE.scripts}">${t("approval.reread")}</button></div>${blocked?`<p class="hint">${escape(blocked)}</p>`:""}`;
}
// The companion kit for podcast platforms (publish_kit): the user's wish of 2026-10-06, a short description and the
// episode text for Spotify and Apple Podcasts with chapters and sources, to copy; one text-model call per episode.
function renderPublishKit(e) {
  const id=escape(e.script.episode_id), k=e.publish_kit;
  const buttons=`<div class="actions"><button class="secondary" data-action="publish_kit" data-episode="${id}" ${disabled()}>${t(k?"kit.rebuild":"kit.create")}</button>${k?`<button class="quiet" data-action="publish_kit" data-episode="${id}" data-fresh="1" ${disabled()}>${t("kit.reword")}</button>`:""}<button class="quiet" data-action="publish_kit" ${disabled()}>${t("common.all_episodes")}</button></div>`;
  if(!k)return `<section class="panel"><h2>${t("kit.title")}</h2><p class="hint">${t("kit.hint")}</p>${buttons}</section>`;
  return `<section class="panel"><h2>${t("kit.title")}</h2>
    ${k.recorded?"":`<p class="note">${t("kit.unrecorded")}</p>`}
    ${k.chapter_problems?.length?`<p class="hint">${t("kit.chapters_hint")}</p>`:""}
    <div class="field"><label for="kit-short-${id}">${t("kit.short")}</label><textarea id="kit-short-${id}" rows="3" readonly>${escape(k.short)}</textarea><button class="quiet small" data-action="copy-text" data-source="kit-short-${id}">${t("common.copy")}</button></div>
    <div class="field"><label for="kit-long-${id}">${t("kit.long",{characters:fmt.number(Number(k.characters)),limit:fmt.number(Number(k.limit))})}</label><textarea id="kit-long-${id}" rows="10" readonly>${escape(k.description)}</textarea><button class="quiet small" data-action="copy-text" data-source="kit-long-${id}">${t("common.copy")}</button></div>
    <p class="hint">${t("kit.sources",{listed:Number(k.sources_listed),total:Number(k.sources_total),file:asHtml(`<code>${escape(k.folder)}/sources.md</code>`)})}</p>${buttons}</section>`;
}
// The whole podcast's companion kit (publish_kit.build_podcast_kit, D-165): the user's wish of 2026-10-07, the podcast's
// two descriptions to copy, and the transcript of every episode with the series' sources in publish/ and the ZIP.
function renderPodcastKit() {
  const k=project?.podcast_kit, current=Boolean(k&&!k.outdated);
  const buttons=`<div class="actions"><button class="secondary" data-action="publish_kit" data-podcast="1" ${disabled()}>${t(k?"kit.rebuild":"podcast_kit.create")}</button>${current?`<button class="quiet" data-action="publish_kit" data-podcast="1" data-fresh="1" ${disabled()}>${t("kit.reword")}</button>`:""}</div>`;
  if(!current)return `<section class="panel"><h2>${t("podcast_kit.title")}</h2><p class="${k?"note":"hint"}">${t(k?"podcast_kit.outdated":"podcast_kit.hint")}</p>${buttons}</section>`;
  const episodes=Number(k.episodes),recorded=Number(k.recorded);
  return `<section class="panel"><h2>${t("podcast_kit.title")}</h2>
    ${recorded<episodes?`<p class="note">${t("podcast_kit.unrecorded",{count:episodes,recorded})}</p>`:""}
    <div class="field"><label for="podcast-kit-short">${t("podcast_kit.short")}</label><textarea id="podcast-kit-short" rows="3" readonly>${escape(k.short)}</textarea><button class="quiet small" data-action="copy-text" data-source="podcast-kit-short">${t("common.copy")}</button></div>
    <div class="field"><label for="podcast-kit-long">${t("podcast_kit.long",{characters:fmt.number(Number(k.characters)),limit:fmt.number(Number(k.limit))})}</label><textarea id="podcast-kit-long" rows="10" readonly>${escape(k.description)}</textarea><button class="quiet small" data-action="copy-text" data-source="podcast-kit-long">${t("common.copy")}</button></div>
    <p class="hint">${t("podcast_kit.files",{count:episodes,folder:asHtml(`<code>${escape(k.folder)}/</code>`)})} ${t("podcast_kit.sources",{count:Number(k.sources_total)})}</p>${buttons}</section>`;
}
function refreshAudioPanel() {
  const panel=$("audio-panel");
  if(!panel||!project?.episodes?.length)return;
  const a=currentAudio();
  panel.innerHTML=renderApprovalCard(project.episodes[Math.min(episodeIndex,project.episodes.length-1)],a,isGemini(a.provider));
}
function renderAudio() {
  let html=heading(6);
  if(!project?.episodes?.length) return html+empty(t("audio.empty.title"),t("audio.empty.text"),t("audio.empty.button"),PAGE.scripts,pageIntros.audio);
  episodeIndex=Math.min(episodeIndex,project.episodes.length-1);
  const e=project.episodes[episodeIndex];
  const a=currentAudio(),remote=isGemini(a.provider);
  const hasAudio=project.episodes.some(row=>row.audio?.length);
  const facts=audioFacts(project);
  const capacity=remote?(boot.capabilities?.parallel_audio?`<p class="hint">${t(project.execution?.audio==="parallel"?"execution.parallel":"execution.sequential")} · ${t("audio.capacity",{active:project.audio_capacity?.active||0,limit:project.audio_capacity?.limit||1})}</p>`:`<p class="note">${t("audio.capacity_restart")}</p>`):"";
  // Older recordings stay playable; the page says once what they differ in instead of nineteen times (D-160).
  const outdated=facts.outdated?`<p class="note">${t(facts.outdated===facts.voiced?"audio.outdated.all":"audio.outdated.some",{count:facts.outdated})}${facts.reasons.length?` (${t("audio.outdated.changed",{reasons:staleText(facts.reasons)})})`:""}. ${t("audio.outdated.tail")}</p>`:"";
  // The recordings come before the per-episode extras: listening is what the page is visited for most.
  html+=`<div class="split"><div class="split-main">${outdated}${episodePicker()}<section class="panel check-card" id="audio-panel">${renderApprovalCard(e,a,remote)}</section><div id="audio-jobs"></div>${renderAudioQueue()}${renderApproveAll(remote)}${hasAudio?`<section class="panel recordings" id="recordings"><h2>${t("audio.recordings_title")}</h2>${renderRecordings(recordingsProject())}</section>`:""}${e.audio?.length?renderListeningReview(e):""}${renderPublishKit(e)}${renderPodcastKit()}</div><aside class="split-rail">${capacity}${renderSpeechSettings(a)}${renderStyleNotes()}</aside></div>`;
  return html;
}
// The card's one line, from the same facts the steps and the header use (D-160).
function overviewStatus(p) {
  if(p.unavailable)return tp("overview.unreadable");
  const active=[p.job,...(p.audio_jobs||[])].filter((j,i,all)=>j?.status==="running"&&all.findIndex(other=>other?.id===j.id)===i);
  if(active.length)return active.length>1?tp("overview.recordings_running",{count:active.length}):(active[0].progress?.activity||actionNames[active[0].action]||tp("job.running"));
  const info=stopInfo(p.job);
  if(info)return `${STOP_KIND_LABELS[info.kind]||tp("nav.state.stopped")} · ${info.title}`;
  return projectSummary(p);
}
// What a project has reached, in one line: the overview card, and the header once its last job is done.
function projectSummary(p=project) {
  const f=audioFacts(p);
  if(f.voiced)return `${tp("summary_line.available",{count:f.voiced})}${f.outdated?` · ${tp("summary_line.outdated",{count:f.outdated})}`:""}${f.unvoiced?` · ${tp("nav.state.unvoiced",{count:f.unvoiced})}`:""}`;
  if(f.total)return tp("summary_line.scripts",{count:f.total});
  const outline=p?.has_outline??!!p?.outline, research=p?.has_research??!!p?.research;
  return tp(outline?"summary_line.outline":research?"summary_line.research":"summary_line.brief");
}
const clockText=seconds=>{const s=Math.max(0,Math.floor(Number(seconds)||0));return `${Math.floor(s/60)}:${String(s%60).padStart(2,"0")}`;};
const projectMedia=(p,path)=>"/media/"+encodeURIComponent(p.id)+"/"+path.split("/").map(encodeURIComponent).join("/");
// One row per recorded episode and one player for all of them (D-163): before, nineteen full-width players without
// their length stood on the page, each labelled "Podcast abspielen". Where the editor stopped and what was heard
// are kept in the project (reader_state).
function podcastCard(p,e,index) {
  const download=path=>boot.capabilities?.podcast_downloads?"/download/"+encodeURIComponent(p.id)+"/file/"+path.split("/").map(encodeURIComponent).join("/"):projectMedia(p,path);
  const number=Number(/^ep_(\d+)$/.exec(e.episode_id)?.[1])||index+1;
  const shortName=(text,max)=>Array.from(String(text).normalize("NFC").replace(/[<>:"/\\|?*\x00-\x1f]/g," ").replace(/\s+/g," ").trim()).slice(0,max).join("").replace(/[ .-]+$/,"")||"Podcast";
  // The server names each recording in the podcast's content language (e.download_names, downloads.download_names,
  // D-153); a server without them leaves the page's own German name.
  const fallbackName=i=>`${shortName(p.topic||"Podcast",32)} - Folge ${String(number).padStart(2,"0")} - ${shortName(e.title,56)}${e.audio.length>1?` - Teil ${i+1}`:""}.mp3`;
  const state=p.reader_state||{}, position=Number(state.positions?.[e.audio[0]])||0, heard=!!state.heard?.[e.audio[0]];
  const current=playlist?.projectId===p.id&&playlist.items[playlist.index]?.episode_id===e.episode_id;
  const playing=current&&!$("episode-player")?.paused;
  const line=[e.audio_seconds?minutesText(e.audio_seconds):"",e.audio.length>1?tp("recordings.parts",{count:e.audio.length}):"",
    e.audio_current?"":`${tp("recordings.earlier")}${e.audio_stale?.length?` (${staleText(e.audio_stale)})`:""}`,
    heard?tp("recordings.heard"):position>30?tp("recordings.resume_at",{time:clockText(position)}):""].filter(Boolean).join(" · ");
  return `<li class="track${current?" current":""}" id="podcast-${escape(p.id)}-${escape(e.episode_id)}" data-audio-version="${escape(JSON.stringify(e.audio))}">
    <button type="button" class="track-play" data-play-episode="${escape(e.episode_id)}" aria-label="${t(playing?"recordings.pause_label":"recordings.play_label",{number})}">${playing?"❚❚":"▶"}</button>
    <div class="track-main"><strong>${t("recordings.title",{number,title:e.title})}</strong><span class="hint" id="recording-status-${escape(p.id)}-${escape(e.episode_id)}">${escape(line)}</span></div>
    <span class="track-links">${e.audio.map((path,i)=>`<a href="${download(path)}" download="${escape(e.download_names?.[i]||fallbackName(i))}" aria-label="${e.audio.length>1?t("recordings.mp3_part_label",{number,part:i+1}):t("recordings.mp3_label",{number})}">MP3${e.audio.length>1?` ${i+1}`:""}</a>`).join(" ")}</span></li>`;
}
function podcastDownload(p) {
  const finished=(p.episodes||[]).filter(e=>e.audio?.length).length,total=p.episode_count||p.episodes?.length||0;
  if(!finished)return `<p class="hint">${t("download.none")}</p>`;
  if(!boot.capabilities?.podcast_downloads)return `<p class="hint">${t("download.restart")}</p>`;
  const complete=finished===total;
  return `<a class="download-all" href="/download/${encodeURIComponent(p.id)}/podcast.zip" ${zipName(p)}>${t(complete?"download.all":"download.finished")} <span>${complete?t("download.zip_all",{count:finished}):t("download.zip_some",{done:finished,total})}</span></a>
    <p class="hint">${t(complete?"download.hint.all":"download.hint.some")}${(p.episodes||[]).some(e=>e.audio?.length&&!e.audio_current)?` ${t("download.hint.older")}`:""}</p>`;
}
// The ZIP's download name as the server sends it in the podcast's content language (download_zip, D-153); the
// download itself takes the name the response carries, and an older server's page keeps the bare attribute.
const zipName=p=>p?.download_zip?`download="${escape(p.download_zip)}"`:"download";
// The recordings list works on the overview's episode shape, built here from the project detail.
function recordingsProject(p=project) {
  return {id:p.id,topic:p.config?.topic||"Podcast",reader_state:p.reader_state||{},episode_count:Math.max((p.episodes||[]).length,(p.outline?.plan?.episodes||[]).length),
    download_zip:p.download_zip||null,
    episodes:(p.episodes||[]).map(e=>({episode_id:e.script.episode_id,title:e.script.title,audio:e.audio||[],audio_current:e.audio_current,
      audio_stale:e.audio_stale||[],audio_seconds:e.audio_seconds||null,download_names:e.download_names||[]}))};
}
function recordingCount(p) {
  const rows=(p.episodes||[]).filter(e=>e.audio?.length), seconds=rows.reduce((sum,e)=>sum+(Number(e.audio_seconds)||0),0);
  return `${tp("recordings.count",{count:rows.length})}${seconds?` · ${tp("recordings.total",{duration:minutesText(seconds)})}`:""}.`;
}
function renderRecordings(p) {
  return `<div class="podcast-download" id="project-download-${escape(p.id)}">${podcastDownload(p)}</div><ol class="playlist" id="podcasts-${escape(p.id)}">${(p.episodes||[]).map((e,i)=>e.audio?.length?podcastCard(p,e,i):"").join("")}</ol><p class="hint" id="project-audio-count-${escape(p.id)}">${escape(recordingCount(p))}</p>`;
}
// Polling adds finished episodes and updates their lines; the one player lives outside the page and keeps playing.
function refreshRecordings(p=project?recordingsProject():null) {
  if(!p)return;
  const count=$("project-audio-count-"+p.id);
  if(count)count.textContent=recordingCount(p);
  redraw($("project-download-"+p.id),podcastDownload(p));
  redraw($("podcasts-"+p.id),(p.episodes||[]).map((e,i)=>e.audio?.length?podcastCard(p,e,i):"").join(""));
}
// The episode player (D-163): one bar for the whole podcast, docked like the voice samples, with speed, previous and
// next; an episode in parts plays its parts in a row.
let playlist=null;
const RATES=[0.8,0.9,1,1.1,1.25,1.5,1.75,2];
function playlistItems(p) {
  return (p.episodes||[]).filter(e=>e.audio?.length).map((e,i)=>({episode_id:e.episode_id,title:e.title,
    number:Number(/^ep_(\d+)$/.exec(e.episode_id)?.[1])||i+1,paths:e.audio,urls:e.audio.map(path=>projectMedia(p,path))}));
}
function playerLabel() {
  const item=playlist?.items[playlist.index];
  if(!item)return "";
  return `${tp("recordings.title",{number:item.number,title:item.title})}${item.urls.length>1?` · ${tp("player.part",{part:playlist.part+1,total:item.urls.length})}`:""}`;
}
async function playEpisode(episodeId, part=0) {
  const p=recordingsProject(), items=playlistItems(p), index=items.findIndex(item=>item.episode_id===episodeId), player=$("episode-player");
  if(index<0||!player)return;
  const same=playlist?.projectId===p.id&&playlist.items[playlist.index]?.episode_id===episodeId&&playlist.part===part&&player.src;
  if(same&&!player.paused){player.pause();return;}
  if(!same){playlist={projectId:p.id,items,index,part,state:p.reader_state||{}};player.src=items[index].urls[part];}
  $("sample-player")?.pause?.();$("sample-playback").hidden=true;
  $("episode-playback").hidden=false;document.body?.classList?.add?.("has-player");
  $("episode-playing-label").textContent=playerLabel();
  player.playbackRate=Number(playlist.state?.rate)||1;
  if($("player-rate"))$("player-rate").value=String(player.playbackRate);
  try{await player.play();}catch{throw new Error(tp("player.play_failed"));}
}
async function stepPlaylist(delta) {
  if(!playlist)return;
  const item=playlist.items[playlist.index];
  if(delta>0&&playlist.part+1<item.urls.length)return playEpisode(item.episode_id,playlist.part+1);
  const next=playlist.items[playlist.index+delta];
  if(next)return playEpisode(next.episode_id,0);
}
function closePlayer() {
  $("episode-player")?.pause?.();$("episode-playback").hidden=true;document.body?.classList?.remove?.("has-player");
  playlist=null;if(step===PAGE.audio&&project)refreshRecordings();
}
// Where each recording stopped and which were heard to the end, saved every ten seconds of listening.
function wirePlayer() {
  const player=$("episode-player"), rate=$("player-rate");
  if(!player||player.dataset?.wired)return;
  if(player.dataset)player.dataset.wired="1";
  if(rate)rate.innerHTML=RATES.map(r=>`<option value="${r}">${fmt.number(r)}×</option>`).join("");
  let saved=0;
  const path=()=>playlist?.items[playlist.index]?.paths[playlist.part];
  const quiet=data=>{if(playlist)saveReaderState(data,playlist.projectId).catch(()=>{});};
  player.addEventListener?.("loadedmetadata",()=>{
    const position=Number(playlist?.state?.positions?.[path()])||0;
    saved=position;
    if(position>10&&position<player.duration-30)player.currentTime=position;
  });
  player.addEventListener?.("timeupdate",()=>{
    if(!path()||Math.abs(player.currentTime-saved)<10)return;
    saved=player.currentTime;quiet({recording:path(),position:Math.floor(saved)});
  });
  player.addEventListener?.("pause",()=>{if(path()&&!player.ended)quiet({recording:path(),position:Math.floor(player.currentTime)});});
  player.addEventListener?.("ended",()=>{
    if(path())quiet({recording:path(),heard:true});
    attempt(()=>stepPlaylist(1));
  });
  for(const name of ["play","pause"])player.addEventListener?.(name,()=>{if(step===PAGE.audio&&project&&!overviewPage&&!settingsPage)refreshRecordings();});
  rate?.addEventListener?.("change",()=>{player.playbackRate=Number(rate.value)||1;quiet({rate:Number(rate.value)||1});});
}
// Overview: one card per project, those waiting for the editor first, each with its next action (D-161). Before,
// waiting projects stood twice, once in a "Wartet auf dich" list and again in the project list below it.
const runningOf = p => p.job?.status==="running"||(p.audio_jobs||[]).some(a=>a.status==="running");
function attentionOf(p) {
  const j=p.job;
  if(p.unavailable)return null;
  // A paused job waits for the user even while other episodes of the project are being voiced.
  const review=j?.status!=="running"?j?.progress?.plan_review:null;
  if(review?.awaiting&&!review.approved)return {text:tp("attention.plan.text"),button:tp("attention.plan.button"),page:PAGE.research};
  if(j?.status==="review_ready")return {text:tp("attention.outline_ready"),button:tp("attention.outline_button"),page:PAGE.outline};
  const info=stopInfo(j);
  if(info)return {text:info.message&&info.message!==info.text&&sameLanguage(info)?`${info.title}: ${info.message}`:info.title,
    button:tp(info.kind==="decision"?"attention.decide":"attention.view"),page:jobPage(p)??PAGE.brief};
  const halted=stoppedAudio(p).filter(a=>a.id!==j?.id);
  if(halted.length){
    const title=a=>(p.episodes||[]).find(e=>e.episode_id===a.episode)?.title||a.episode;
    const shared=halted.every(a=>stopInfo(a).code===stopInfo(halted[0]).code);
    return {text:halted.length===1?tp("attention.halted.one",{episode:quoted(title(halted[0])),reason:stopInfo(halted[0]).title}):
      shared?tp("attention.halted.shared",{count:halted.length,reason:stopInfo(halted[0]).title}):
      tp("attention.halted.mixed",{count:halted.length,list:halted.map(a=>`${quoted(title(a))} (${stopInfo(a).title})`).join(", ")}),button:tp("attention.view_audio"),page:PAGE.audio};
  }
  if(!j||runningOf(p))return null;
  if(p.has_outline&&!p.script_count&&j.run?.kind!=="script"&&!["script","revise"].includes(j.action))return {text:tp("attention.outline_waiting"),button:tp("attention.outline_button"),page:PAGE.outline};
  const audio=audioFacts(p);
  if(p.script_count&&!audio.voiced)return {text:tp("attention.scripts",{count:p.script_count}),button:tp("attention.read"),page:PAGE.scripts};
  // Recordings from an earlier state stay playable and ask for nothing; an episode without any recording does.
  if(p.script_count&&audio.unvoiced)return {text:tp("attention.unvoiced",{count:audio.unvoiced}),button:tp("attention.view_audio"),page:PAGE.audio};
  return null;
}
function pipelineStates(p) {
  const j=p.job, run=j?.run, busy=runningOf(p);
  const blocked=["blocked","failed","interrupted","waiting_for_quota","pending"].includes(j?.status);
  const audio=audioFacts(p);
  const states=["done",p.has_research?"done":"pending",p.has_outline?"done":"pending",p.script_count?"done":"pending",
    p.script_count?(audio.voiced?"done":"decision"):"pending",audio.voiced?(audio.unvoiced?"decision":"done"):(p.script_count?"decision":"pending")];
  if(p.has_outline&&!p.script_count&&run?.kind!=="script"&&!busy)states[PAGE.outline]="decision";
  if(j?.progress?.plan_review?.awaiting&&!j.progress.plan_review.approved&&!busy)states[PAGE.research]="decision";
  const page=jobPage(p);
  if(page!==null&&page!==undefined&&(busy||blocked))states[page]=busy?"running":stopTone(stopInfo(j));
  return states;
}
// Each segment names its step and its state, for the tooltip and for a screen reader (colour alone said it before).
const PIPE_WORDS=Object.fromEntries(["done","running","decision","paused","blocked","pending","ready"].map(id=>[id,tp(`pipe.${id}`)]));
const pipeMarkup = states => states.map((s,i)=>`<span class="${s}" title="${steps[i]}: ${PIPE_WORDS[s]||s}"></span>`).join("");
const pipeLabel = states => states.map((s,i)=>`${steps[i]}: ${PIPE_WORDS[s]||s}`).join(", ");
const cardClass = p => `pipeline${runningOf(p)?" running":attentionOf(p)?" waiting":""}`;
// The title opens the project where its work is; the card's one filled button is the next action, deleting sits in a
// menu instead of beside "Öffnen".
function overviewCardInner(p) {
  const a=attentionOf(p), busy=runningOf(p), hasAudio=(p.episodes||[]).some(e=>e.audio?.length), states=pipelineStates(p), id=escape(p.id);
  const open=a?`<button class="small" data-open-project="${id}" data-open-step="${a.page}">${escape(a.button)}</button>`
    :`<button class="secondary small" data-open-project="${id}">${t("overview.open")}</button>`;
  return `<div class="pipeline-main"><h2><button type="button" class="card-title" data-open-project="${id}" title="${escape(p.topic)}">${escape(p.topic)}</button>${p.trial?` ${trialChip()}`:""}</h2>
    <div class="pipe" id="pipe-${id}" role="img" aria-label="${escape(pipeLabel(states))}">${pipeMarkup(states)}</div>
    <p class="card-state" id="project-state-${id}">${a?`<strong>${escape(a.text)}</strong>`:escape(overviewStatus(p))}</p></div>
    <div class="actions">${open}${hasAudio?`<button class="secondary small" data-open-project="${id}" data-open-step="${PAGE.audio}">${t("overview.listen")}</button>`:""}<span id="overview-download-${id}">${overviewDownload(p)}</span>
    <details class="card-menu"><summary aria-label="${t("overview.more_label",{topic:shortText(p.topic,40)})}">⋯</summary><div class="card-menu-list"><button class="quiet small danger-text" data-delete-project="${id}" ${p.unavailable||busy||!boot.capabilities?.project_overview?"disabled":""}>${t("overview.delete")}</button></div></details></div>`;
}
function overviewCard(p) {
  return `<article class="${cardClass(p)}" id="project-card-${escape(p.id)}" data-project-card="${escape(p.id)}">${overviewCardInner(p)}</article>`;
}
// The whole podcast straight from the overview, as the same ZIP the audio page offers (the user's wish, 2026-09-29).
function overviewDownload(p) {
  const finished=(p.episodes||[]).filter(e=>e.audio?.length).length,total=p.episode_count||p.episodes?.length||0;
  if(!finished||!boot.capabilities?.podcast_downloads)return "";
  const complete=finished>=total;
  return `<a class="download-all small" href="/download/${encodeURIComponent(p.id)}/podcast.zip" ${zipName(p)}>${t(complete?"overview.download":"download.finished")} <span>${complete?t("download.zip_all",{count:finished}):t("download.zip_some",{done:finished,total})}</span></a>`;
}
// Waiting projects first, then running ones, each group in the server's order.
function sortedProjects() {
  const rank=p=>attentionOf(p)?0:runningOf(p)?1:2;
  return overviewData.projects.map((p,i)=>[p,i]).sort((a,b)=>rank(a[0])-rank(b[0])||a[1]-b[1]).map(([p])=>p);
}
function trashMarkup() {
  return overviewData.trash?.length?`<details class="panel"><summary>${t("overview.trash",{count:overviewData.trash.length})}</summary>${overviewData.trash.map(p=>`<div class="sample-row"><span>${escape(p.topic)}</span><button class="secondary small" data-restore-project="${escape(p.id)}">${t("overview.restore")}</button></div>`).join("")}</details>`:"";
}
function renderOverview() {
  const legend=["done","running","decision","paused","blocked"].map(s=>`<span class="${s}"></span>${PIPE_WORDS[s]}`).join(" ");
  return `<header class="page-head"><div class="page-title"><span class="eyebrow">${t("overview.eyebrow")}</span><h1>${escape(tp("overview.heading"))}</h1></div><button data-new-project>＋ ${t("project.new")}</button></header>
    <section class="projects" aria-label="${t("sidebar.projects")}"><div class="projects-head"><h2>${t("sidebar.projects")}</h2>${overviewData.projects.length?`<p class="pipe-legend" aria-hidden="true">${legend}</p>`:""}</div>
    <div id="overview-projects">${overviewData.projects.length?sortedProjects().map(overviewCard).join(""):`<p id="overview-empty" class="hint">${t("overview.empty")}</p>`}</div></section><div id="overview-trash">${trashMarkup()}</div>`;
}
async function loadOverview() {
  if(boot.capabilities?.project_overview)return await api("/api/projects");
  const details=await Promise.all(boot.projects.map(p=>api("/api/projects/"+encodeURIComponent(p.id))));
  return {projects:details.map(p=>({id:p.id,topic:p.config.topic,job:p.job,script_count:p.episodes.length,has_research:!!p.research,has_outline:!!p.outline,
    episodes:p.episodes.map(e=>({...e,episode_id:e.script.episode_id,title:e.script.title}))})),trash:[]};
}
async function showOverview() {
  if(setupSending)throw new Error(tp("chat.sending_wait"));
  if(!leaveSettings())return;
  const epoch=++navigationEpoch,data=await loadOverview();if(epoch!==navigationEpoch)return;
  overviewData=data;overviewPage=true;settingsPage=false;project=null;followWorkflow=false;pendingAttachments=[];
  $("project-select").value="";updatePageUrl();render();
}
async function showSettings(push=true) {
  if(setupSending)throw new Error(tp("chat.sending_wait"));
  const epoch=++navigationEpoch,data=await api("/api/settings");if(epoch!==navigationEpoch)return;
  settingsData=data;settingsDraft=structuredClone(data.settings);settingsPage=true;overviewPage=false;project=null;settingsDirty=false;
  followWorkflow=false;pendingAttachments=[];drawerOpen=false;
  $("project-select").value="";updatePageUrl(push);render();
}
function refreshOverview() {
  renderKeyNote();
  const container=$("overview-projects");
  if(!container)return;
  for(const card of container.querySelectorAll?.("[data-project-card]")||[])
    if(!overviewData.projects.some(p=>p.id===card.dataset.projectCard))card.remove();
  if(overviewData.projects.length&&$("overview-empty"))$("overview-empty").hidden=true;
  for(const p of sortedProjects()){
    const card=$("project-card-"+p.id);
    if(!card){container.insertAdjacentHTML?.("beforeend",overviewCard(p));continue;}
    card.className=cardClass(p);
    redraw(card,overviewCardInner(p));
  }
  const trash=$("overview-trash"),content=trashMarkup();
  redraw(trash,content);
}
const AUDIO_STEPS=Object.fromEntries(["normalize","loudness","encode"].map(id=>[id,tp(`audio_step.${id}`)]));
// What an audio job does right now: loading the voice model, speaking a chapter or assembling the episode.
function audioPhase(p) {
  if(!p)return "";
  // Before a Gemini recording the text model places the inline audio tags (expression.py).
  if(p.status==="expression")return `<p><span class="activity-dot" aria-hidden="true"></span>${t("audio.phase.expression")}</p>`;
  if(p.status==="assembly"){
    const part=Number(p.parts)>1?` · ${t("player.part",{part:Number(p.part),total:Number(p.parts)})}`:"";
    const counting=p.step==="normalize"&&Number(p.total_segments)>0;
    return `<p><span class="activity-dot" aria-hidden="true"></span>${t("audio.phase.assembly",{step:AUDIO_STEPS[p.step]||tp("audio_step.default")})}${counting?` · ${t("common.of",{done:Number(p.completed_segments),total:Number(p.total_segments)})}`:""}${part}</p>${counting?`<progress value="${Number(p.completed_segments)}" max="${Number(p.total_segments)}" aria-label="${t("audio.phase.normalized_label")}"></progress>`:""}`;
  }
  if(p.total_segments===undefined)return "";
  const chapter=p.chapters?` · ${t("audio.phase.chapter",{chapter:Number(p.chapter),chapters:Number(p.chapters)})}${p.chapter_title?`: ${escape(p.chapter_title)}`:""}`:"";
  const loading=p.tts_status==="loading_model"?`<p class="hint">${t("audio.phase.loading")}</p>`:"";
  return `<p>${t("audio.phase.segments",{done:Number(p.completed_segments),total:Number(p.total_segments)})}${chapter}</p><progress value="${Number(p.completed_segments)}" max="${Number(p.total_segments)}" aria-label="${t("audio.phase.segments_label")}"></progress>${loading}`;
}
// The worker's heartbeat: silence is flagged, with the long steps that legitimately look like it.
function heartbeatNote(j) {
  // 2026-10-02: a worker that outlived a Studio restart was shown as interrupted, and "Fortsetzen" met the busy project.
  const elsewhere=j?.status==="running"&&j.external?`<p class="note" role="status">${t("job.elsewhere")} ${t(j.external_stoppable?"job.elsewhere_stoppable":"job.elsewhere_unknown")}</p>`:"";
  const age=Number(j?.heartbeat_age_seconds);
  if(j?.status!=="running"||!Number.isSafeInteger(age))return elsewhere;
  return elsewhere+silenceNote(j,age);
}
function silenceNote(j,age) {
  const loading=j.progress?.tts_status==="loading_model";
  if(age<=(loading?900:300))return "";
  return `<p class="note" role="status">${t("job.silence",{minutes:Math.floor(age/60)})} ${t(loading?"job.silence_loading":"job.silence_long")} ${t("job.silence_tail")}</p>`;
}
// One card per episode job; the local Qwen job, which runs as the project's main job, gets the same card.
function renderAudioJob(j,{main=false}={}) {
  const e=project.episodes?.find(row=>row.script.episode_id===j.episode), active=j.status==="running", info=stopInfo(j);
  const state=active?tp("audio_job.recording"):j.status==="completed"?tp("audio_job.done"):info?.title||tp("nav.state.stopped");
  const target=`data-run-id="${escape(j.run?.run_id||"")}"${main?"":` data-episode="${escape(j.episode)}"`}`;
  // Gemini episodes resume beside each other within the free slots; the local job waits for any other job.
  const blocked=main?(running()?tp("audio_job.other_running"):""):audioBlockReason(j.episode);
  return `<section class="audio-job${info?" stopped":""}"><div class="job-top"><strong>${escape(e?.script.title||j.episode||tp("run_label.episode_audio"))} · ${escape(state)}</strong>
    ${active&&!(j.external&&!j.external_stoppable)?`<button class="danger small" data-action="stop"${main?"":` data-job-id="${escape(j.id)}"`}>${t("audio_job.stop")}</button>`:""}</div>
    ${active?audioPhase(j.progress)+heartbeatNote(j):info?stopBody(j,info,{target,blocked}):""}
</section>`;
}
// Running and stopped recordings; finished ones are in the list of recordings (D-163).
function renderAudioJobs() {
  const cards=(project?.audio_jobs||[]).filter(j=>j.status!=="completed").map(j=>renderAudioJob(j));
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
  // An episode that finished its step in this pass is done, also where an older server's check disagrees
  // (2026-09-29: episodes accepted with notes stayed "open" for hours).
  const done=e=>e.completed||(["teaching","writing","polishing","review"].includes(p.stage)&&e.stage_status==="completed");
  const finished=Math.max(Number(p.completed_segments)||0,(p.episodes||[]).filter(done).length);
  // Every episode passed its own review: what runs is the series review or its one correction round.
  const series=p.stage==="review"&&Number(p.total_segments)>0&&finished===Number(p.total_segments);
  const activity=series&&!/serie/i.test(p.activity||"")?tp("progress_view.series_correction",{activity:p.activity}):p.activity;
  const heading=series?t("progress_view.series"):parallel?t("progress_view.parallel",{count:inWork.length,stage:stageNames[p.stage]||p.stage}):
    p.current_episode?t("progress_view.episode",{number:Number(p.episode_number),total:Number(p.total_segments),title:p.episode_title}):escape(stageNames[p.stage]||tp("progress_view.progress"));
  const marker=e=>done(e)?"✓":active&&(running(e)||(!inWork.length&&e.episode_id===p.current_episode))?"●":e.stage_status==="interrupted"?"!":"○";
  const winding=active&&p.stopping?.episodes?.length?`<p class="note" role="status"><strong>${t("progress_view.stopping",{episodes:p.stopping.episodes.join(", ")})}</strong> ${t("progress_view.stopping_tail")}</p>`:"";
  const since=elapsed!==null?tp("nav.hint.since",{elapsed:elapsed<1?tp("time.less_than_minute"):tp("time.minutes",{minutes:elapsed})}):"";
  return `<section class="script-progress"><p class="current-episode"><strong>${heading}</strong></p>${winding}<p>${active?'<span class="activity-dot" aria-hidden="true"></span>':`${t("progress_view.last")} `}${parallel&&!series?`${t("progress_view.last_started")} `:""}${escape(activity)}${active&&elapsed!==null?` · ${escape(since)}`:""}</p><p class="hint">${t("progress_view.auto_hint")}</p>${p.total_segments?`<progress value="${finished}" max="${Number(p.total_segments)}" aria-label="${t("progress_view.bar_label")}"></progress><p>${t("progress_view.finished",{done:finished,total:Number(p.total_segments),stage:stageNames[p.stage]||p.stage})}</p>`:""}${active&&series?`<p class="note" role="status">${t("progress_view.series_note")}</p>`:""}<ol class="episode-progress">${(p.episodes||[]).map(e=>`<li>${marker(e)} ${escape(e.title)}${parallel&&e.stage_started_at&&running(e)?` <span class="hint">· ${t("nav.hint.since",{elapsed:elapsedText(e.stage_started_at)})}</span>`:""}</li>`).join("")}</ol>${(p.episodes||[]).filter(e=>e.teaching_preview).map(e=>`<details data-progress-episode="${escape(e.episode_id)}"><summary>${t("progress_view.read_plan",{title:e.title})}</summary><pre class="document" data-progress-preview="${escape(e.episode_id)}">${escape(e.teaching_preview)}</pre></details>`).join("")}</section>`;
}
function progressAge(timestamp) {
  const seconds=Math.max(0,Math.floor((Date.now()-Date.parse(timestamp))/1000)), minutes=Math.floor(seconds/60), hours=Math.floor(minutes/60);
  if(seconds<60)return tp("age.seconds",{count:seconds});
  if(minutes<90)return tp("age.minutes",{count:minutes});
  if(hours<48)return tp("age.hours",{count:hours});
  return tp("age.days",{count:Math.floor(hours/24)});
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
  const age=p.model_call_started_at?progressAge(p.model_call_started_at):"";
  const call=p.model_call_started_at?`<p class="hint">${stale?t("timing.call_stale",{age}):open.length>1?t("timing.calls_parallel",{count:open.length,age}):t("timing.call",{age})}</p>`:"";
  const result=p.last_result_at?`<p class="hint">${t("timing.result",{age:progressAge(p.last_result_at)})}</p>`:"";
  const changed=p.changed_at?`<p class="hint">${t("timing.changed",{age:progressAge(p.changed_at)})}</p>`:"";
  const freshness=stale?`<p class="note" role="status">${t("timing.stale",{age:progressAge(new Date(lastSyncAt).toISOString()),time:fmt.time(lastSyncAt)})}</p>`:"";
  return call+result+changed+freshness;
}
// The research assignment in the page's language: its catalog text by id, else the server's German (research_status).
const assignmentText=info=>info.assignment_id&&hasText(`assignment.${info.assignment_id}`)?tp(`assignment.${info.assignment_id}`):info.assignment;
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
  const categories=Object.fromEntries(["connection","retry","timeout","rate_limit","server_error","authentication","quota"].map(id=>[id,tp(`insight.category.${id}`)]));
  // The warning comes from the server in German; other languages say it from the count it names.
  const stuck=info.warning&&LANG!=="de"&&Number(info.no_progress_steps)>=2?tp("assignment.warning",{count:Number(info.no_progress_steps)}):info.warning;
  return `${info.question?`<p class="trace-focus">${escape(info.question)}</p>`:""}
    <p class="work-label">${t("insight.assignment_label")}</p><p>${escape(assignmentText(info))}</p>
    ${info.last_step?`<p class="work-label">${t("insight.last_step")}</p><p>${escape(info.last_step)}</p>`:""}
    ${info.feedback?.length?`<p class="work-label">${t("insight.feedback")}</p><ul>${info.feedback.map(line=>`<li>${escape(line)}</li>`).join("")}</ul>`:""}
    <details class="work-material"><summary>${t("insight.material")}</summary>
      <p>${t("insight.material_count",{sections:Number(material.section_count||0),sources:Number(material.source_count||0)})}${info.candidate_count?`; ${t("insight.candidates",{count:Number(info.candidate_count)})}`:""}.</p>
      ${material.unresolved_sections?`<p class="hint">${t("insight.unresolved")}</p>`:""}
      ${material.sources?.length?`<ul>${material.sources.map(s=>`<li>${escape(s.title)} · ${t("insight.source_sections",{count:Number(s.sections)})}${s.pages?.length?` · ${t("insight.pages",{pages:s.pages.map(Number).join(", ")})}`:""}</li>`).join("")}</ul>`:""}
      ${material.source_count>6?`<p>${t("insight.more_sources",{count:Number(material.source_count)-6})}</p>`:""}
      ${info.queries?.length?`<p>${t("insight.queries",{queries:info.queries.join("; ")})}</p>`:""}
      ${info.criteria?.length?`<p>${t("insight.criteria")}</p><ul>${info.criteria.map(line=>`<li>${escape(line)}</li>`).join("")}</ul>`:""}
    </details>
    <div class="work-signals${warning?" quiet":""}">
      ${active&&since?`<p><strong>${visibleAt||(!signal.call&&signal.last_content_at)?t("insight.quiet_text",{age:progressAge(since)}):t("insight.quiet_call",{age:progressAge(since)})}</strong></p>`:""}
      ${hiddenOutput?`<p class="note"><strong>${t("insight.hidden_title")}</strong> ${t("insight.hidden_text")}</p>`:""}
      ${active&&receivedAt?`<p class="hint">${t("insight.last_fragment",{age:progressAge(receivedAt)})}${signal.stream_deltas!=null?` · ${t("insight.fragments",{count:Number(signal.stream_deltas)})}`:""}.</p>`:""}
      ${active?`<p class="hint">${t("insight.active_hint")}</p>`:`<p>${t(job.status!=="running"?"insight.stopped":signal.state==="completed"?"insight.completed":"insight.failed")}</p>`}
      ${signal.last_event_at?`<p class="hint">${t("insight.last_event",{age:progressAge(signal.last_event_at)})}${!signal.last_content_at?` · ${t("insight.status_only")}`:""}</p>`:""}
      ${signal.last_result_at?`<p class="hint">${t("insight.last_result",{age:progressAge(signal.last_result_at)})}</p>`:""}
      ${active&&signal.timeout_seconds?`<p class="hint">${t("insight.timeout",{minutes:Math.ceil(Number(signal.timeout_seconds)/60)})}</p>`:""}
      ${Object.entries(signal.categories||{}).filter(([key])=>categories[key]).map(([key,n])=>`<p>${t("insight.category_line",{category:categories[key],count:Number(n)})}</p>`).join("")}
      ${stuck?`<p class="note">${escape(stuck)}</p>`:""}
    </div><p class="hint">${t(info.basis==="request"?"insight.basis.request":"insight.basis.saved")}; ${t("insight.basis_tail")}</p>`;
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
  return `<p class="hint">${t("active_tasks.text",{count:ids.length,names:ids.map(id=>names.get(id)||id).join(" · ")})}</p>`;
}
function renderModelTrace(job) {
  if(!["research","script"].includes(job?.progress?.phase))return "";
  const trace=job.progress.model_trace,rows=(trace?.lines||[]).slice(-20);
  const kinds=Object.fromEntries(["reasoning","text","status","diagnostic"].map(id=>[id,tp(`trace.kind.${id}`)]));
  const active=job.status==="running";
  const ledger=job.progress.research_questions;
  const current=(ledger?.questions||[]).find(row=>row.id===activeTasks(ledger)[0]);
  const started=job.progress.model_call_started_at;
  const currentContent=rows.some(row=>["text","reasoning"].includes(row.kind)&&(!started||Date.parse(row.at)>=Date.parse(started)));
  const insight=renderWorkInsight(job), labels=job.progress.call_labels||{}, open=job.progress.open_calls||[];
  // Consecutive lines of one call at one moment share their head: a finished structured answer arrives as a burst of
  // twenty lines with the same time and the same long episode title (2026-10-07). Plain text needs no kind label.
  let previous="";
  const items=rows.map(row=>{
    const head=[row.at?fmt.time(row.at):"",row.kind!=="text"?kinds[row.kind]||tp("trace.kind.other"):"",labels[row.call]?shortText(labels[row.call],48):""].filter(Boolean).join(" · ");
    const same=head===previous;previous=head;
    return `<li${same?' class="same"':""}>${same?"":`<small>${escape(head)}</small>`}<p>${escape(row.text)}</p></li>`;
  }).join("");
  const list=rows.length?`<ol class="trace-lines">${items}</ol>`:`<p class="hint">${t("trace.empty")}</p>`;
  const age=trace?.updated_at?` · ${t("trace.last",{age:progressAge(trace.updated_at)})}`:"";
  // Running, the live output stands open; stopped, the last lines fold under one line (D-164).
  return `<section class="model-trace" aria-label="${t("trace.label")}">${active?`<strong>${t("trace.title")}</strong>
    <p class="hint">${t("trace.working")}${age}</p>
    ${started&&!currentContent?`<p class="hint">${t("trace.no_content")}</p>`:""}${list}`
    :`<details class="trace-archive"><summary>${t("trace.archive")}${age}</summary>${list}</details>`}
    <p class="hint">${t("trace.unchecked")}</p>
    ${insight?`<details class="model-events"><summary>${active?(open.length>1?t("trace.task_latest",{count:open.length}):t("trace.task_current")):t("trace.task_last")}</summary>${insight}</details>`:(current&&active?`<p class="trace-focus">${escape(current.question)}</p><p>${escape(current.activity)}</p>`:"")}${renderActiveTasks(ledger,active)}</section>`;
}
// Ending the audit loop: after the next whole-dossier audit the run finishes, its open objections on record.
function residualFinish(ledger, runId) {
  if(!ledger||Number(ledger.audit_round||0)<1||ledger.phase==="completed")return "";
  const requested=ledger.residual_finish;
  if(requested)return `<p class="note">${t("residual.requested")}${requested.note?` (${escape(requested.note)})`:""}. ${t("residual.requested_tail")}</p>`;
  // One plain sentence (D-155): the objections the last round left open and, while their rework runs, about what
  // another round costs; a ledger without objection data keeps the general explanation. Finishing is the highlighted
  // choice, reworking on stays what happens without it.
  const count=(ledger.questions||[]).filter(q=>Array.isArray(q.objections)&&!q.accepted_gap).reduce((sum,q)=>sum+q.objections.length,0);
  const calls=ledger.phase==="questions"?Number(ledger.budget_projection?.expected_remaining_calls):NaN, round=Number(ledger.audit_round);
  const sentence=!count?t("residual.hint"):Number.isFinite(calls)&&calls>0?t("residual.sentence",{count,round,calls}):t("residual.sentence_plain",{count,round});
  return `<p>${sentence}</p><div class="actions residual-finish"><input id="residual-note" placeholder="${t("residual.note_placeholder")}" aria-label="${t("residual.note_label")}"><button class="small" data-action="finish-residual" data-run-id="${escape(runId)}">${t("residual.button")}</button></div><p class="hint">${t("residual.alternative")}</p>`;
}
function researchRound(ledger) {
  // The round counts from the first whole-dossier audit; reopened questions belong to a later round.
  const round=Number(ledger?.audit_round||0), reopened=Number(ledger?.reopened||0);
  if(!(round>0||reopened>0||["synthesis","audit"].includes(ledger?.phase)))return "";
  // After an audit its reopened questions are reworked first; the dossier is recomposed and reviewed once they pass.
  if(ledger?.phase==="questions"&&round>0){
    const rows=ledger.questions||[], open=rows.filter(q=>Number(q.reopened||0)>0&&q.status!=="verified").length;
    return `<p><strong>${t("round.rework",{round})}</strong> · ${t("round.reopened",{count:reopened})}${rows.length?`, ${t("round.still_open",{count:open})}`:""}. ${t("round.then",{next:round+1})}</p>`;
  }
  const doing=["synthesis","audit"].includes(ledger?.phase)?` · ${t(`round.doing.${ledger.phase}`)}`:"";
  return `<p><strong>${t("round.round",{round:round+1})}</strong>${doing}${reopened>0&&!doing?` · ${t("round.reopened",{count:reopened})}`:""}</p>`;
}

// Every stop names what happened, whether "Fortsetzen" can help and which control leads on:
//   retry    "Fortsetzen" repeats the step; finished work stays.
//   wait     a provider limit resets; then "Fortsetzen" or the scheduler's automatic resume.
//   fix      something outside the Studio needs fixing first (login, key, FFmpeg), then "Fortsetzen".
//   decision a control on the step's page decides (plan, blocked questions, a higher limit).
//   dead     this run cannot continue; the card names the way on and what stays readable.
// The texts are stop.<code>.title and stop.<code>.text in the catalog (D-152); {resume} names the button that goes on,
// quoted: „Fortsetzen“, or for a chat, a check or a voice sample the button that starts it again (RESTART_VERBS).
const STOP_KIND_LABELS=Object.fromEntries(["retry","wait","fix","decision","dead"].map(kind=>[kind,tp(`stop.kind.${kind}`)]));
// Whether the Studio's code changed after this job stopped (studio.code_updated_at).
const codeUpdatedSince=job=>{const at=project?.server?.code_updated_at;return !!(at&&job?.finished_at&&Date.parse(at)>Date.parse(job.finished_at));};
// A stop text from the catalog with its resume button filled in from the context stopInfo builds.
const say=key=>c=>tp(key,{resume:c.resume});
const rule=(code,kind,extra={})=>({kind,title:tp(`stop.${code}.title`),text:say(`stop.${code}.text`),...extra});
// Which limit a budget stop reached: the server names it from the run's failure details (job.stop.limit, D-152).
// Fallback for runs stopped before that field existed: their German message and the counters.
const searchCapped=c=>c.job.stop?.limit?c.job.stop.limit==="search_rounds":/Rechercherunden|Suchrunden/.test(c.job.message||"")||
  (Number(c.progress.search_round_limit)>0&&Number(c.progress.search_rounds)>=Number(c.progress.search_round_limit)&&
    !(Number(c.progress.model_call_limit)>0&&Number(c.progress.model_calls)>=Number(c.progress.model_call_limit)));
function loginHint(c) {
  const text=`${c.job.message||""} ${c.job.provider_choice?.provider||""}`;
  if(/codex/i.test(text)&&!/claude/i.test(text))return tp("stop.login.codex");
  if(/claude/i.test(text)&&!/codex/i.test(text))return tp("stop.login.claude");
  return tp("stop.login.either");
}
// Google's own reason where it is in the page's language, otherwise the card's own sentence (D-152).
const googleReason=(c,fallback)=>c.job.message&&sameLanguage(c.job.stop||{message_language:c.job.message_language})?c.job.message:tp(fallback);
const STOP_RULES={
  interrupted:rule("interrupted","retry"),
  worker_start:rule("worker_start","retry"),
  processing_failed:rule("processing_failed","retry"),
  invalid_local_data:rule("invalid_local_data","retry"),
  project_busy:rule("project_busy","retry"),
  timeout:rule("timeout","retry"),
  claude_structured_output:rule("claude_structured_output","retry"),
  stall:rule("stall","retry"),
  claude_failed:rule("claude_failed","retry",{actions:["check"]}),
  codex_failed:rule("codex_failed","retry",{actions:["check"]}),
  openrouter_connection:rule("openrouter_connection","retry"),
  openrouter_request:rule("openrouter_request","retry"),
  openrouter_unavailable:rule("openrouter_unavailable","retry"),
  openrouter_speech_request:rule("openrouter_speech_request","retry"),
  invalid_audio:rule("invalid_audio","retry"),
  audio_processing_failed:rule("audio_processing_failed","retry"),
  loudness_failed:rule("loudness_failed","retry"),
  tts_worker_failed:rule("tts_worker_failed","retry"),
  tts_environment:rule("tts_environment","fix",{actions:["check","open_settings"]}),
  search_not_observed:rule("search_not_observed","retry"),
  invalid_model_output:rule("invalid_model_output","retry",{actions:["fresh_attempts"]}),
  research_questions_open:rule("research_questions_open","retry"),
  question_scope_unresolved:rule("question_scope_unresolved","retry",{actions:["fresh_attempts"]}),
  invalid_speech:rule("invalid_speech","retry"),
  invalid_expression:rule("invalid_expression","retry",{actions:["open_scripts"]}),
  missing_executable:rule("missing_executable","fix",{actions:["check"]}),
  unsupported_codex_launcher:rule("unsupported_codex_launcher","fix",{actions:["check"]}),
  unsupported_claude_launcher:rule("unsupported_claude_launcher","fix",{actions:["check"]}),
  openrouter_forbidden:rule("openrouter_forbidden","fix",{actions:["key"]}),
  openrouter_search_unsupported:rule("openrouter_search_unsupported","fix",{actions:["open_settings"]}),
  invalid_backend:rule("invalid_backend","fix",{actions:["open_settings","restart"]}),
  audio_approval_required:rule("audio_approval_required","decision",{actions:["audio_again"]}),
  credential_in_prompt:rule("credential_in_prompt","dead",{actions:["open_brief","open_settings","restart"]}),
  credential_in_response:rule("credential_in_response","dead",{actions:["restart"]}),
  invalid_output_schema:rule("invalid_output_schema","dead"),
  unsupported_run:rule("unsupported_run","dead",{actions:["restart"]}),
  missing_outputs:rule("missing_outputs","dead",{actions:["restart"]}),
  worker_stop:rule("worker_stop","dead"),
  research_context_incomplete:rule("research_context_incomplete","dead",{actions:["restart"]}),
  script_review_failed:rule("script_review_failed","retry",{actions:["fresh_attempts","new_outline"]}),
  dialogue_polish_failed:rule("dialogue_polish_failed","retry",{actions:["new_outline"]}),
  subscriptions_exhausted:rule("subscriptions_exhausted","wait"),
  claude_quota_exhausted:rule("claude_quota_exhausted","wait",{actions:["text_switch"]}),
  quota_exhausted:rule("quota_exhausted","wait",{actions:["text_switch"]}),
  openrouter_rate_limit:rule("openrouter_rate_limit","wait"),
  waiting_for_quota:rule("waiting_for_quota","wait"),
  jev_unavailable:rule("jev_unavailable","retry"),
  openrouter_credits:rule("openrouter_credits","fix"),
  subscription_required:rule("subscription_required","fix",{actions:["check"]}),
  codex_missing:rule("codex_missing","fix",{actions:["check"]}),
  claude_missing:rule("claude_missing","fix",{actions:["check"]}),
  claude_version:rule("claude_version","fix",{actions:["check"]}),
  openrouter_key_required:rule("openrouter_key_required","fix",{actions:["key"]}),
  anthropic_key_required:rule("anthropic_key_required","fix",{actions:["anthropic_key"]}),
  anthropic_authentication:rule("anthropic_authentication","fix",{actions:["anthropic_key"]}),
  anthropic_credits:rule("anthropic_credits","fix"),
  anthropic_rate_limit:rule("anthropic_rate_limit","wait"),
  claude_api_auth_mismatch:rule("claude_api_auth_mismatch","fix"),
  perplexity_key_required:rule("perplexity_key_required","fix",{actions:["perplexity_key"]}),
  perplexity_authentication:rule("perplexity_authentication","fix",{actions:["perplexity_key"]}),
  perplexity_credits:rule("perplexity_credits","fix"),
  perplexity_rate_limit:rule("perplexity_rate_limit","wait"),
  perplexity_failed:rule("perplexity_failed","retry"),
  invalid_search_selection:rule("invalid_search_selection","retry"),
  cost_limit_required:rule("cost_limit_required","decision",{actions:["approve_cost"]}),
  cost_limit_reached:rule("cost_limit_reached","decision",{actions:["approve_cost"]}),
  invalid_key:rule("invalid_key","fix",{actions:["key"]}),
  openrouter_authentication:rule("openrouter_authentication","fix",{actions:["key"]}),
  google_key_required:rule("google_key_required","fix",{actions:["google_key"]}),
  invalid_google_key:rule("invalid_google_key","fix",{actions:["google_key"]}),
  google_unavailable:rule("google_unavailable","retry"),
  google_connection:rule("google_connection","retry"),
  ffmpeg_missing:rule("ffmpeg_missing","fix"),
  ffprobe_missing:rule("ffprobe_missing","fix"),
  research_plan_review:rule("research_plan_review","decision",{card:true}),
  research_questions_blocked:rule("research_questions_blocked","decision",{card:true}),
  review_ready:rule("review_ready","decision",{card:true}),
  research_budget_insufficient:rule("research_budget_insufficient","decision",{actions:["approve_calls"]}),
  script_budget_insufficient:rule("script_budget_insufficient","decision",{actions:["approve_calls"]}),
  plan_exceeds_allowance:rule("plan_exceeds_allowance","decision",{actions:["approve_calls"]}),
  chat_budget:rule("chat_budget","decision"),
  inputs_changed:rule("inputs_changed","dead",{actions:["restart"]}),
  script_edited:rule("script_edited","dead",{actions:["restart"]}),
  invalid_plan:rule("invalid_plan","dead",{actions:["new_outline"]}),
  research_required:rule("research_required","dead",{actions:["open_research","new_research"]}),
  // With the episode known (job.teaching_failure), one plain sentence names it and what a redraft costs (D-155).
  teaching_design_failed:rule("teaching_design_failed","dead",{actions:["redesign_teaching","new_outline"],text:c=>{
    const number=Number(/^ep_0*(\d+)$/.exec(c.job?.teaching_failure?.episode_id||"")?.[1]);
    return number?tp("stop.teaching_design_failed.plain",{episode:number}):tp("stop.teaching_design_failed.text",{resume:c.resume});
  }}),
  teaching_research_required:rule("teaching_research_required","retry",{actions:["fresh_attempts","new_research","new_outline"]}),
  research_gap_unread:rule("research_gap_unread","dead",{actions:["new_research"]}),
  prompt_too_large:rule("prompt_too_large","fix",{actions:["open_settings","restart"]}),
  claude_output_limit:rule("claude_output_limit","dead",{actions:["open_settings","restart"]}),
  claude_budget_cap:rule("claude_budget_cap","dead",{actions:["open_settings","restart"]}),
  openrouter_truncated:rule("openrouter_truncated","dead",{actions:["open_settings","restart"]}),
  no_readable_sources:rule("no_readable_sources","dead",{actions:["open_brief","restart"]}),
  duration_exceeded:rule("duration_exceeded","dead",{actions:["open_scripts"]}),
  // A spent series correction round can be set aside for a new one (run_budget.approve_fresh_attempts).
  series_review_failed:rule("series_review_failed","retry",{actions:["fresh_attempts","new_outline"],
    text:c=>tp("stop.series_review_failed.text",{resume:c.resume})+(freshOffered(c.job,c.code)?` ${tp("stop.series_review_failed.fresh")}`:"")}),
  teaching_review_failed:rule("teaching_review_failed","retry",{actions:["new_outline"]}),
  // The open Studio checks the login of a stopped research or script run itself, until its three automatic resumes
  // (studio.MAX_AUTO_RESUMES) are spent (D-155).
  authentication_required:rule("authentication_required","fix",{actions:["check"],
    text:c=>tp("stop.authentication_required.text",{hint:loginHint(c),resume:c.resume})+
      (["research","script"].includes(c.kind)&&Number(c.job?.auto_resume_count||0)<3?` ${tp("stop.authentication_required.watch")}`:"")}),
  // Gemini through Google (google_speech): its own key, and Google's own reasons.
  google_authentication:rule("google_authentication","fix",{actions:["google_key"],
    text:c=>tp("stop.google_authentication.text",{message:googleReason(c,"stop.google_authentication.fallback"),resume:c.resume})}),
  google_quota:rule("google_quota","wait",{
    text:c=>tp("stop.google_quota.text",{message:googleReason(c,"stop.google_quota.fallback"),resume:c.resume})}),
  google_speech_request:rule("google_speech_request","fix",{actions:["open_settings"],
    text:c=>tp("stop.google_speech_request.text",{message:googleReason(c,"stop.google_speech_request.fallback"),resume:c.resume})}),
  // A script run meets this only in the Jev gap probe; a recording only with Gemini.
  openrouter_privacy:({run})=>run?.kind==="script"?{kind:"fix",title:tp("stop.openrouter_privacy.jev.title"),text:say("stop.openrouter_privacy.jev.text")}:
    {kind:"fix",title:tp("stop.openrouter_privacy.gemini.title"),text:say("stop.openrouter_privacy.gemini.text")},
  research_budget_exhausted:{kind:"decision",title:c=>tp(searchCapped(c)?"stop.research_budget_exhausted.search.title":"stop.research_budget_exhausted.calls.title"),
    text:c=>tp(searchCapped(c)?"stop.research_budget_exhausted.search.text":"stop.research_budget_exhausted.calls.text",{resume:c.resume}),
    actions:c=>[searchCapped(c)?"approve_search":"approve_calls"]},
  // A disputed earlier objection is the editor's call (research page); an unanchored new one still ends the run.
  review_disagreement:c=>c.progress?.review_disagreement?{kind:"decision",title:tp("stop.review_disagreement.dispute.title"),card:true,
    text:say("stop.review_disagreement.dispute.text")}:
    {kind:"dead",title:tp("stop.review_disagreement.unanchored.title"),text:say("stop.review_disagreement.unanchored.text"),actions:["restart"]},
  // A dead end until the code changes: a correction after the stop may be exactly what this checkpoint needs.
  invalid_research_checkpoint:({job})=>codeUpdatedSince(job)
    ?{kind:"retry",title:tp("stop.invalid_research_checkpoint.title"),text:say("stop.invalid_research_checkpoint.text_updated"),actions:["restart"]}
    :{kind:"dead",title:tp("stop.invalid_research_checkpoint.title"),text:say("stop.invalid_research_checkpoint.text"),actions:["restart"]},
};
for(const code of ["invalid_source_snapshot","invalid_plan_approval","invalid_budget_approval","invalid_gap_approval","invalid_retry_request"])STOP_RULES[code]=STOP_RULES.invalid_research_checkpoint;
// In a script run the changed source is a page a supplement fetched again (Ontologies, 2026-09-28); an update keeps the
// research's copy, so a resume can pass. A research run's changed source file stays a dead end.
STOP_RULES.invalid_source_snapshot=c=>c.kind==="script"?{kind:"retry",title:tp("stop.invalid_source_snapshot.title"),text:say("stop.invalid_source_snapshot.text"),actions:["new_outline"]}:STOP_RULES.invalid_research_checkpoint(c);
for(const code of ["research_coverage_incomplete","invalid_research"])STOP_RULES[code]=STOP_RULES.research_required;
// Correction loops: a research check replays its saved rejections on a resume, a script stage starts them anew.
// 2026-10-02: the evidence and repair checks of polishing, the series, teaching and the gap research, a split verified
// question and a dossier rebuild are correction loops as well; they fell through to the generic card.
// A script stage whose correction loop keeps no rejections asks the model anew on a resume (run_budget.REASKED_CODES).
// Where the server offers fresh attempts for it, the card says so in one sentence and offers them, without pointing to
// a new table of contents (D-155).
const REASKED_CODES=new Set(["invalid_script","rejected_output","invalid_model_output","invalid_script_evidence_review",
  "invalid_teaching_review","invalid_teaching_evidence","invalid_teaching_repair","invalid_polish_review","invalid_polish_evidence",
  "invalid_series_review","invalid_series_evidence","invalid_supplement","invalid_evidence_review"]);
const reasked=c=>c.kind==="script"&&REASKED_CODES.has(c.code)&&freshOffered(c.job,c.code)?{kind:"retry",title:tp("stop.correction.title"),
  text:c.code==="invalid_script"?say("stop.correction.invalid_script.text"):say("stop.correction.reasked.text"),actions:["fresh_attempts"]}:null;
// An unreadable answer is a transient stop while the scheduler resumes it; once the step spent its corrections, the card above.
{const plain=STOP_RULES.invalid_model_output;STOP_RULES.invalid_model_output=c=>(c.job?.auto_resume_kind!=="transient"&&reasked(c))||plain;}
for(const code of ["rejected_output","invalid_evidence_review","invalid_question_routing","invalid_research_patch","invalid_research_assessment",
  "invalid_search_receipt","invalid_question_review","invalid_evidence","invalid_question_plan","invalid_question_scope",
  "invalid_supplement","invalid_teaching_review","invalid_script_evidence_review","invalid_polish_review","invalid_series_review","invalid_script","invalid_revision",
  "invalid_polish_evidence","invalid_series_evidence","invalid_teaching_evidence","invalid_teaching_repair","invalid_research_gap",
  "verified_question_split","invalid_dossier_rebuild"])
  // A step that saved its rejected answers replays them on a resume and stops again without a call; fresh attempts are
  // offered where the backend would set them aside (job.fresh_attempts).
  STOP_RULES[code]=c=>reasked(c)??(c.kind==="script"?{kind:"retry",title:tp("stop.correction.title"),text:say("stop.correction.script.text"),actions:["fresh_attempts","new_outline"]}:
    // A research run keeps hours of checked answers: resuming retries the step first (an update may have fixed it).
    {kind:"retry",title:tp("stop.correction.title"),text:say("stop.correction.research.text"),actions:["fresh_attempts","restart"]});
// A chat, a check or a voice sample has no run to continue: the same button starts it again.
const RESTART_VERBS={assistant:tp("button.resend"),check:tp("button.check"),audio_sample:tp("restart_verb.audio_sample"),audio_samples:tp("restart_verb.audio_samples")};
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
  const verb=!run&&RESTART_VERBS[job.action];
  const context={job,run,kind,code,progress,resume:quoted(verb||tp("button.resume"))};
  let rule=STOP_RULES[code]??STOP_RULES[job.status];
  if(typeof rule==="function")rule=rule(context);
  rule??={kind:"retry",title:tp("stop.generic.title"),actions:["restart"],
    text:code?tp("stop.generic.text_code",{code,resume:context.resume}):tp("stop.generic.text",{resume:context.resume})};
  const value=v=>typeof v==="function"?v(context):v;
  const info={code,kind:rule.kind,title:value(rule.title),text:value(rule.text),actions:[...(value(rule.actions)||[])],card:!!rule.card};
  if(verb){
    info.kind=["wait","fix","decision"].includes(info.kind)?info.kind:"retry";
    info.actions=info.actions.filter(a=>["key","check"].includes(a));
    if(job.action==="check"&&!info.actions.includes("check"))info.actions.push("check");
    if(job.action==="audio_samples")info.actions.push("samples");
  }
  const stop=job.stop||{};
  // The server already shortens paths; an older server's message still must not show a local path.
  const scrub=text=>String(text||"").replace(/[A-Za-z]:[\\/][^\s"'<>|]*/g,path=>path.split(/[\\/]/).pop());
  // The message's language decides where the page shows it (sameLanguage); without a stop record the job names it.
  return {...info,message:scrub(stop.message??job.message??""),message_language:stop.message_language??job.message_language,
    detail:stop.detail||null,file:stop.file||null,crash:stop.crash_detail||job.crash_detail||null};
}
// "Fortsetzen" is offered where it can help: retry, wait and fix stops, an approved plan, decided questions.
// Blocked questions whose current block has no advice yet (research_advisor.block_key); a waiting question gets none.
const unadvised=ledger=>(ledger?.questions||[]).filter(q=>q.status==="blocked"&&!q.accepted_gap&&!q.retry_requested
  &&!["prerequisite_block","audit_block"].includes(q.outcome)&&q.advice?.key!==`${Number(q.retries||0)}.${Number(q.auto_retries||0)}`).length;
// Blocked questions whose advice for the current block recommends a new attempt the automatic ones did not start.
// Advice to raise a run limit is one too: once the limit is raised, the attempt follows (Ontologies, 2026-10-01:
// 14 questions were advised so, and after the raise each needed its own click).
const adviceRetries=ledger=>(ledger?.questions||[]).filter(q=>q.status==="blocked"&&!q.accepted_gap&&!q.retry_requested
  &&["retry","raise_limit"].includes(q.advice?.recommendation)&&q.advice?.key===`${Number(q.retries||0)}.${Number(q.auto_retries||0)}`);
// A question's advice when it is for its current block (research_advisor.block_key), else null.
const currentAdvice=q=>q?.advice&&q.advice.key===`${Number(q.retries||0)}.${Number(q.auto_retries||0)}`?q.advice:null;
// The run asks the advisor only with room for the advice and one new attempt (question_research.advice_affordable).
const adviceAffordable=ledger=>{
  const b=ledger?.budget_projection;
  return !b||Number(b.remaining)-Number(b.minimum_remaining_calls)>=1+Number(b.expected_calls_per_task);
};
// The call limit that leaves that room for every question still without advice and for the questions waiting on
// them, with a tenth to spare.
const adviceCallLimit=ledger=>{
  const b=ledger.budget_projection, perTask=Number(b.expected_calls_per_task);
  const waiting=(ledger.questions||[]).filter(q=>q.status==="blocked"&&!q.accepted_gap&&q.outcome==="prerequisite_block").length;
  return Number(b.used)+Math.ceil((Number(b.minimum_remaining_calls)+unadvised(ledger)*(1+perTask)+waiting*perTask)*11/10);
};
// Blocked questions a resume moves on without a decision of their own: those whose reworks are spent, with the
// finish requested or in a run that keeps their verified answers (keeps_spent_answers, from prompt generation 3);
// and one that only waits for prerequisites that passed or are themselves moving on. A synthesis also goes on
// without a prerequisite accepted as a gap.
function blockedSettled(ledger) {
  const rows=ledger?.questions||[], byId=new Map(rows.map(q=>[q.id,q])), memo=new Map();
  const settled=(q,seen=new Set())=>{
    if(memo.has(q.id))return memo.get(q.id);
    let result=false;
    if((ledger?.residual_finish||ledger?.keeps_spent_answers)&&q.outcome==="audit_block")result=true;
    else if(q.outcome==="prerequisite_block"&&!seen.has(q.id)){
      seen.add(q.id);
      result=(q.depends_on||[]).every(id=>{const d=byId.get(id);return d&&(d.status==="verified"||(q.kind==="synthesis"&&d.accepted_gap)
        ||(d.status==="blocked"&&!d.accepted_gap&&settled(d,seen)));});
    }
    memo.set(q.id,result);return result;
  };
  return rows.filter(q=>q.status==="blocked"&&!q.accepted_gap&&settled(q));
}
function canResume(job,info=stopInfo(job)) {
  if(!job?.run||!info)return false;
  const ledger=job.progress?.research_questions, review=job.progress?.plan_review;
  if(info.code==="research_plan_review")return !review?.awaiting||!!review.approved;
  // The server lifts the ledger out of "blocked" once every blocked question is decided; a block without advice
  // resumes too, because the run asks the advisor first.
  if(info.code==="research_questions_blocked"){
    const settled=new Set(blockedSettled(ledger).map(q=>q.id));
    const undecided=(ledger?.questions||[]).filter(q=>q.status==="blocked"&&!q.accepted_gap&&!q.retry_requested&&!q.access_gap_requested&&!settled.has(q.id));
    return Number(ledger?.reopenable||0)>0||Number(ledger?.retry_requested||0)>0||ledger?.phase!=="blocked"||(unadvised(ledger)>0&&adviceAffordable(ledger))
      ||(settled.size>0&&!undecided.length);
  }
  return ["retry","wait","fix"].includes(info.kind)&&!info.actions.includes("key");
}
function restartAction(job) {
  const kind=job.run?.kind||job.stop?.run_kind;
  if(kind==="research")return "new_research";
  if(kind==="episode_audio")return "audio_again";
  // Only a completed outline takes a revision hint (scripting.outline_revision); a stopped draft has none and starts anew.
  if(kind==="script")return runPage(job.run)===PAGE.outline&&job.run?.stages?.planning?.status==="completed"?"replan_feedback":"new_outline";
  return null;
}
function suggestedCalls(job) {
  const p=job.progress||{}, ledger=p.research_questions?.budget_projection, script=p.budget_projection;
  const limit=Number(p.model_call_limit)||0, used=Number(p.model_calls)||0;
  if(ledger&&Number.isFinite(Number(ledger.used)))
    return Math.max(limit+1,Number(ledger.used)+Math.max(Number(ledger.expected_remaining_calls||0),Number(ledger.minimum_remaining_calls||0)));
  // The script projection names minimums without repairs: a quarter more leaves room for corrections. Calibrated on
  // the project's last completed script run, its expectation is the need, the minimum still the floor (studio_allowances).
  if(script&&Number.isFinite(Number(script.minimum_remaining_calls))){
    const minimum=Number(script.minimum_remaining_calls), expected=Number(script.expected_remaining_calls);
    const need=script.calibration&&Number.isFinite(expected)?Math.max(expected,minimum):Math.ceil(minimum*5/4);
    return Math.max(limit+1,Number(script.used??used)+need);
  }
  return Math.max(limit,used)+50;
}
// Fresh attempts only where the backend would set something aside (job.fresh_attempts, run_budget.fresh_attempts_available):
// 2026-10-02 the button stood on research stops without a stuck call and again after an allowance had reset the
// repairs, and the backend refused both. A server without the field keeps the earlier rule.
const freshRecommended=(job,info)=>!!info?.actions?.includes("fresh_attempts")&&freshOffered(job,info.code);
function freshOffered(job,code) {
  if(typeof job?.fresh_attempts==="boolean")return job.fresh_attempts;
  return job?.run?.kind==="research"||(job?.run?.kind==="script"&&["teaching_research_required","script_review_failed"].includes(code));
}
function stopButton(action,job,info,target) {
  const runId=escape(job.run?.run_id||"");
  const onPage=page=>page===step?"":`<button class="secondary" data-step="${page}">${t("stop_button.open_page",{page:steps[page]})}</button>`;
  switch(action){
    case "approve_calls":{const n=suggestedCalls(job);return `<button data-action="approve-calls" data-run-id="${runId}" data-model-calls="${n}" data-then-resume="1" ${running()?"disabled":""}>${t("stop_button.calls",{count:n})}</button>`;}
    case "approve_search":{const n=(Number(job.progress?.search_round_limit)||0)+6;return `<button data-action="approve-search" data-run-id="${runId}" data-search-rounds="${n}" data-then-resume="1" ${running()?"disabled":""}>${t("stop_button.search",{count:n})}</button>`;}
    // Offered only where the step spent its corrections: then it is the way on, so it is the primary button.
    case "fresh_attempts":return freshOffered(job,info.code)?`<button data-action="fresh-attempts" data-run-id="${runId}" data-then-resume="1" ${running()?"disabled":""}>${t("stop_button.fresh")}</button>`:"";
    case "text_switch":{
      // Offered only where a run fixed on the spent subscription stops; a pair already moves on by itself.
      const codexOut=info.code==="quota_exhausted";
      if(!job.text_switchable||job.text_switch_choice!==(codexOut?"astra":"claude"))return "";
      return `<button data-action="text-switch" data-run-id="${runId}" data-choice="${codexOut?"claude_first":"astra_first"}" data-then-resume="1" ${running()?"disabled":""}>${t(codexOut?"stop_button.with_claude":"stop_button.with_astra")}</button>`;
    }
    case "key":return inlineKey("stop-key",!!job.run,target||`data-run-id="${runId}"`);
    case "google_key":return inlineKey("stop-google-key",!!job.run,target||`data-run-id="${runId}"`,"google");
    case "anthropic_key":return inlineKey("stop-anthropic-key",!!job.run,target||`data-run-id="${runId}"`,"anthropic");
    case "perplexity_key":return inlineKey("stop-perplexity-key",!!job.run,target||`data-run-id="${runId}"`,"perplexity");
    case "approve_cost":{
      // A run without a limit gets one; a run at its limit a higher one, at least half again what it allowed.
      const p=job.progress||{}, limit=Number(p.cost_limit_usd)||0, spent=Number(p.cost_spent_usd)||0;
      const n=Math.max(1,Math.ceil(limit?Math.max(limit*1.5,spent*1.25):Math.max(10,spent*2)));
      return `<div class="actions"><label class="sr-only" for="stop-cost">${t("switch.cost_label")}</label><input id="stop-cost" type="number" inputmode="decimal" min="${Math.ceil(Math.max(limit,1))}" step="1" value="${n}">
        <button data-action="approve-cost" data-run-id="${runId}" data-then-resume="1" ${running()?"disabled":""}>${t(limit?"stop_button.cost_raise":"stop_button.cost_set")}</button></div>
        <p class="hint">${spent?`${limit?t("stop_button.spent_limit",{spent:usd(spent),limit:usd(limit)}):t("stop_button.spent",{spent:usd(spent)})}. `:""}${t("stop_button.cost_hint")}</p>`;
    }
    case "check":return `<button class="secondary" data-action="check" ${disabled()}>${t("button.check")}</button>`;
    case "samples":return `<button class="secondary" data-action="audio_samples" ${disabled()}>${t("stop_button.samples")}</button>`;
    case "new_research":return `<button class="secondary" data-action="research" data-confirm="${t("stop_button.new_research_confirm")}" ${disabled()}>${t("research.again")}</button>`;
    case "new_outline":return project?.research?`<button class="secondary" data-action="plan" data-confirm="${t("stop_button.new_outline_confirm")}" ${disabled()}>${t("button.new_outline")}</button>`:"";
    case "redesign_teaching":{
      const failed=job.teaching_failure;
      if(!failed?.episode_id)return "";
      // The note starts with the open points the stop names, for the editor to keep, shorten or replace (D-155).
      const points=String(info.message||"").replace(/^.*?(?:offene Punkte|open points):\s*/,"").trim();
      return `<div class="stop-feedback">${area("redesign-note",t("stop_button.redesign_label",{episode:quoted(failed.title||failed.episode_id)}),points,3)}<button data-action="redesign-teaching" data-run-id="${runId}" data-teaching-episode="${escape(failed.episode_id)}" ${disabled()}>${t("stop_button.redesign")}</button></div>`;
    }
    case "replan_feedback":return `<div class="stop-feedback">${area("stop-feedback",t("stop_button.replan_label"),info.message||"",3)}<button data-action="replan" data-feedback="stop-feedback" ${disabled()}>${t("stop_button.replan")}</button></div>`;
    case "audio_again":return step===PAGE.audio?`<button class="secondary" data-scroll="audio-panel">${t("stop_button.to_audio")}</button>`:`<button class="secondary" data-step="${PAGE.audio}">${t("stop_button.to_audio")}</button>`;
    case "open_research":return onPage(PAGE.research);
    case "open_outline":return onPage(PAGE.outline);
    case "open_scripts":return onPage(PAGE.scripts);
    case "open_brief":return onPage(PAGE.brief);
    case "open_settings":return `<button class="secondary" data-action="open-settings">${t("common.open_settings")}</button>`;
    default:return "";
  }
}
const FRESH_CHOICES=[0,1,2,3], CALL_CHOICES=[0,100,250,500,1000];
function allowanceSummary(a={}) {
  return tp("allowance.per_run",{fresh:a.fresh_attempts?tp("allowance.fresh",{count:Number(a.fresh_attempts)}):tp("allowance.no_fresh"),
    extra:a.extra_calls?tp("allowance.extra",{count:Number(a.extra_calls)}):tp("allowance.no_extra")});
}
// What the allowances already gave the run on screen, so their use stays visible.
function allowanceUse(a) {
  const used=a?.used||{};
  // Only once one was spent; an unused allowance stands in the settings and the brief's summary.
  if(!a||!(a.fresh_attempts||a.extra_calls)||!(Number(used.fresh_attempts)||Number(used.extra_calls)))return "";
  return `<p class="hint">${t("allowance.use",{fresh_used:Number(used.fresh_attempts||0),fresh:Number(a.fresh_attempts||0),calls_used:Number(used.extra_calls||0),calls:Number(a.extra_calls||0)})}</p>`;
}
// Where a run spent its calls and time, per stage and prompt version (production_report.py); loaded on request.
const productionReports={};
// The research steps a question_research prompt version names after its family and generation, checked in this order.
const RESEARCH_STEPS=["block_advice","compose","dossier","grounding","assessment","routes","references","correction","review","reader","search","scope","plan"]
  .map(key=>[key,tp(`report.step.${key}`)]);
function researchSteps(versions,total) {
  const rows=new Map();
  for(const v of versions){
    const [family,,...rest]=String(v.version).split(".");
    if(family!=="question_research")continue;
    const label=RESEARCH_STEPS.find(([key])=>rest.join(".").includes(key))?.[1]||tp("report.step.other");
    const row=rows.get(label)||{label,calls:0,minutes:0,failed:0};
    row.calls+=Number(v.calls)||0;row.minutes+=Number(v.minutes)||0;rows.set(label,row);
  }
  return [...rows.values()].sort((a,b)=>b.minutes-a.minutes).map(row=>({...row,share:row.minutes/total,minutes_per_call:row.calls?row.minutes/row.calls:0}));
}
function renderProductionReport(job) {
  const runId=job.run?.run_id, loaded=productionReports[runId];
  if(!runId)return "";
  const load=label=>`<button class="secondary small" data-action="production-report" data-run-id="${escape(runId)}">${label}</button>`;
  if(!loaded)return `<div class="production-report">${load(t("report.load"))}<span class="hint"> ${t("report.load_hint")}</span></div>`;
  const r=loaded.report, num=(n,d=1)=>fmt.number(n,{maximumFractionDigits:d});
  const time=m=>Number(m)>=60?tp("age.hours",{count:num(Number(m)/60)}):tp("time.minutes",{minutes:Math.round(Number(m))});
  // A research run spends nearly every call in one stage; its prompt versions name the step (D-164).
  const total=r.stages.reduce((sum,s)=>sum+Number(s.minutes||0),0)||1;
  const stages=r.stages.flatMap(s=>s.stage==="question_research"?researchSteps(r.versions||[],total):[s]);
  // A stage's label: the catalog's by its id (production_report.STAGE_LABELS), else the server's.
  const stageLabel=s=>s.stage?serverLabel(`report.stage.${s.stage}`,s.label):s.label;
  const rows=stages.map(s=>`<tr><td>${escape(stageLabel(s))}</td><td>${Number(s.calls)}${s.failed?` · ${t("report.failed",{count:Number(s.failed)})}`:""}</td><td>${time(s.minutes)}</td><td>${Math.round(Number(s.share)*100)} %</td><td>${t("time.minutes",{minutes:num(s.minutes_per_call)})}</td></tr>`).join("");
  const providers=Object.entries(r.providers||{}).map(([name,p])=>`${t("report.provider",{provider:providerLabels[name]||name,calls:Number(p.calls),time:time(p.minutes)})}${Number(p.billed_usd)?`, ${t("report.billed",{usd:Number(p.billed_usd).toFixed(2)})}`:""}${Number(p.unpriced_attempts)?`, ${t("report.unpriced",{count:Number(p.unpriced_attempts)})}`:""}`).join(" · ");
  const stops=r.stops.total?Object.entries(r.stops.by_stage).map(([stage,n])=>`${escape(stageNames[stage]||stage)} ${Number(n)}×`).join(", "):t("report.none");
  const a=r.approvals||{}, granted=[a.fresh_attempts?tp("report.fresh",{count:Number(a.fresh_attempts)}):"",a.model_calls?tp("report.call_limit",{count:Number(a.model_calls)}):"",a.allowances?.length?tp("report.allowances",{count:a.allowances.length}):"",a.text_switch?tp("report.switched"):""].filter(Boolean).join(" · ")||tp("report.none");
  const versions=r.versions.map(v=>`<tr><td>${escape(v.version)}</td><td>${Number(v.calls)}</td><td>${time(v.minutes)}</td><td>${v.first?escape(fmt.dateTime(v.first,{dateStyle:"short",timeStyle:"short"})):""}</td></tr>`).join("");
  return `<section class="production-report"><h3>${t("report.title",{calls:Number(r.calls),time:time(r.model_minutes)})}</h3>
    <table><thead><tr><th>${t("report.col.stage")}</th><th>${t("report.col.calls")}</th><th>${t("report.col.time")}</th><th>${t("report.col.share")}</th><th>${t("report.col.per_call")}</th></tr></thead><tbody>${rows}</tbody></table>
    <p class="hint">${t("report.providers",{providers:asHtml(providers||t("report.none"))})}</p><p class="hint">${t("report.stops",{stops:asHtml(stops),granted})}</p>
    <details><summary>${t("report.by_version")}</summary><p class="hint">${t("report.version_hint")}</p><table><thead><tr><th>${t("report.col.version")}</th><th>${t("report.col.calls")}</th><th>${t("report.col.time")}</th><th>${t("report.col.first")}</th></tr></thead><tbody>${versions}</tbody></table></details>
    <div class="actions">${load(t("report.refresh"))}<button class="quiet small" data-action="production-report-close" data-run-id="${escape(runId)}">${t("common.close")}</button></div></section>`;
}
function waitNote(job) {
  const when=iso=>fmt.dateTime(iso,{dateStyle:"short",timeStyle:"short"});
  if(job.allowance){
    const a=job.allowance;
    return `<p class="hint">${t("wait.allowance",{what:a.kind==="fresh_attempts"?tp("wait.allowance.fresh",{number:Number(a.number),of:Number(a.of)}):tp("wait.allowance.calls",{calls:Number(a.model_calls),extra:Number(a.extra_calls)})})}</p>`;
  }
  // A stop the Studio's newer code may get past is resumed once, as soon as nothing else runs (studio.CODE_UPDATE_STOPS).
  if(job.auto_resume_kind==="code_update"&&job.auto_resume_at)return `<p class="hint">${t("wait.code_update")}</p>`;
  // A technical stop (time limit, silent or failed call, dropped connection) is resumed after a pause, like a quota wait.
  const transient=job.auto_resume_kind==="transient";
  if(job.auto_resume_at){
    if(Date.parse(job.auto_resume_at)<=Date.now())return `<p class="hint">${t("wait.due")}</p>`;
    return `<p class="hint">${transient?`${t("wait.transient_prefix")} `:""}${t("wait.planned",{time:when(job.auto_resume_at),attempt:Number(job.auto_resume_count||0)+1})}${transient?` ${t("wait.now",{resume:quoted(tp("button.resume"))})}`:""}</p>`;
  }
  if(job.auto_resume_exhausted)return `<p class="hint">${t(transient?"wait.exhausted_transient":"wait.exhausted")}</p>`;
  if(job.retry_at&&job.status==="waiting_for_quota")return `<p class="hint">${t("wait.retry_at",{time:when(job.retry_at)})}</p>`;
  return "";
}
// The stop's code, its file, and a server message in another language than the page, which shows there as the
// original instead of beside the card's own text (D-152).
function techDetails(info) {
  const foreign=info.message&&info.message!==info.text&&!sameLanguage(info);
  if(!info.detail&&!info.crash&&!info.file&&!info.code&&!foreign)return "";
  const link=info.file&&project?`<p><a href="/api/projects/${encodeURIComponent(project.id)}/file?path=${encodeURIComponent(info.file)}" target="_blank" rel="noopener">${t("tech.open_file",{file:info.file})}</a></p>`:"";
  return `<details class="tech-details"><summary>${t("tech.title")}</summary>${info.code?`<p class="hint">${t("tech.code",{code:info.code})}</p>`:""}${foreign?`<p>${t("stop.original_message",{message:info.message})}</p>`:""}${info.detail?`<p>${escape(info.detail)}</p>`:""}${info.crash?`<pre>${escape(info.crash)}</pre>`:""}${link}</details>`;
}
// The stop's explanation, the step's own message, the controls and the technical details, in reading order.
function stopBody(job,info,{target="",blocked=null}={}) {
  // Another job holds the Studio, typically episodes being voiced: the controls wait and say so.
  const reason=blocked??(running()?otherJobText():"");
  const buttons=[...new Set(info.actions.map(a=>a==="restart"?restartAction(job):a).filter(Boolean))].map(a=>stopButton(a,job,info,target)).join("");
  // Where fresh attempts are offered, plain "Fortsetzen" only helps after an update: it follows them as a secondary
  // button (2026-10-07: the card said so while "Fortsetzen" was its filled button).
  const fresh=freshRecommended(job,info);
  const resume=canResume(job,info)?`<button ${fresh?'class="secondary" ':""}data-action="resume" ${target||`data-run-id="${escape(job.run?.run_id||"")}"`} ${reason?"disabled":""}>${t("button.resume")}</button>`:"";
  // The step's own message stands beside the card's text in the page's language; another language goes to the details.
  const message=info.message&&info.message!==info.text&&sameLanguage(info)?`<p class="stop-message">${t("chat.message",{message:info.message})}</p>`:"";
  return `<p>${escape(info.text)}</p>${message}${waitNote(job)}${resume||buttons?`<div class="actions">${fresh?buttons+resume:resume+buttons}</div>`:""}${reason&&(resume||buttons)?`<p class="hint">${escape(reason)}</p>`:""}${techDetails(info)}`;
}
function otherJobText() {
  const voicing=(project?.audio_jobs||[]).filter(j=>j.status==="running").length;
  return voicing?tp("other_job.voicing",{count:voicing}):tp("other_job.running");
}
// Gemini episodes whose latest job stopped and that have no current recording from another run.
function stoppedAudio(p=project) {
  const current=id=>(p?.episodes||[]).some(e=>(e.script?.episode_id??e.episode_id)===id&&e.audio_current&&e.audio?.length);
  return (p?.audio_jobs||[]).filter(j=>stopInfo(j)&&!current(j.episode));
}
function renderStopCard(job,info) {
  return `<section class="panel stop-card ${escape(info.kind)}" role="alert"><div class="panel-title"><h2>${escape(info.title)}</h2><span class="chip ${stopTone(info)}">${escape(STOP_KIND_LABELS[info.kind]||tp("nav.state.stopped"))}</span></div>${stopBody(job,info)}</section>`;
}

function renderResearchQuestions(ledger, opened=new Set(), active=false, runId="", searchLimit=0, searchRounds=0, sourceLimit=0) {
  const budget=ledger.budget_projection;
  const resume=quoted(tp("button.resume"));
  const expected=Number.isSafeInteger(budget?.expected_remaining_calls)?` ${tp("research_q.expected",{calls:Number(budget.expected_remaining_calls),per:Number(budget.expected_calls_per_task)})}`:"";
  const suggested=budget?Number(budget.used)+Math.max(Number(budget.expected_remaining_calls||0),Number(budget.minimum_remaining_calls||0)):0;
  const approveCalls=budget&&!budget.feasible&&!active?`<button class="secondary small" data-action="approve-calls" data-run-id="${escape(runId)}" data-model-calls="${suggested}">${t("research_q.raise",{count:suggested})}</button>`:"";
  const budgetNote=budget?`<p class="${budget.feasible?"hint":"note"}">${t("research_q.budget",{minimum:Number(budget.minimum_remaining_calls),closing:Number(budget.closing_calls),remaining:Number(budget.remaining)})}${escape(expected)} ${budget.feasible?t("research_q.feasible"):t("research_q.shortfall",{count:Number(budget.shortfall)})}</p>${approveCalls}`:"";
  const states=Object.fromEntries(["pending","researching","reviewing","verified","blocked"].map(id=>[id,tp(`research_q.state.${id}`)]));
  const phases=Object.fromEntries(["awaiting_plan_approval","questions","synthesis","audit","completed","blocked"].map(id=>[id,tp(`research_q.phase.${id}`)]));
  const outcomes=Object.fromEntries(["audit_block","supported_answer","supported_uncertainty","access_block","extraction_block","search_block","budget_block",
    "evidence_block","prerequisite_block","accepted_gap"].map(id=>[id,tp(`research_q.outcome.${id}`)]));
  const activeIds=activeTasks(ledger), all=ledger.questions||[];
  // A blocked question names its cause and when it is decided: the decision card appears only once the run stops.
  const blockedNote=row=>{
    // A synthesis goes on without a prerequisite accepted as a gap; any other question stays blocked by it.
    const gapTolerant=row.kind==="synthesis";
    const prerequisites=all.filter(q=>(row.depends_on||[]).includes(q.id)&&q.status!=="verified"&&!(gapTolerant&&q.accepted_gap));
    const waiting=row.outcome==="prerequisite_block"&&!prerequisites.some(q=>q.accepted_gap)
      &&(prerequisites.length>0||gapTolerant);
    const cause=waiting?(prerequisites.length?tp("research_q.waits_for",{questions:prerequisites.map(q=>q.question).join("; ")})
      :tp("research_q.prereq_gaps",{resume})):row.reason;
    // The counted web searches, next to the model's own wording: with the run's rounds used up it searched only what was read.
    const web=Number(row.web_attempts||0), exhausted=searchLimit>0&&searchRounds>=searchLimit;
    const fetched=Number(ledger.source_attempt_count||0), full=sourceLimit>0&&fetched>=sourceLimit;
    const spent=[exhausted?tp("research_q.rounds_spent",{used:Number(searchRounds),limit:Number(searchLimit)}):"",
      full?tp("research_q.sources_full",{fetched,limit:Number(sourceLimit)}):""].filter(Boolean);
    const raise=tp(exhausted&&full?"research_q.raise_both":full?"research_q.raise_sources":"research_q.raise_rounds");
    const searched=waiting?"":`<p class="hint">${t("research_q.web",{count:web})}${spent.map(text=>` · ${escape(text)}`).join("")}.${spent.length&&!web?` ${t("research_q.only_read",{raise})}`:""}</p>`;
    const decision=row.retry_requested?t("research_q.retry_requested",{resume})
      :row.reopenable?t("research_q.reopenable",{resume})
      :waiting&&!prerequisites.length?t("research_q.resume_takes",{resume})
      :waiting?`${t("research_q.prereq_retry")}${gapTolerant?` ${t("research_q.prereq_gap_synthesis")}`:""}${active?` ${t("research_q.decide_later")}`:""}`
      :active?t("research_q.decide_when_stopped")
      :t("research_q.decide_above");
    return `<div class="note"><p><strong>${t("research_q.blocked",{outcome:outcomes[row.outcome]||tp("research_q.outcome.evidence_block")})}</strong>${cause?` · ${escape(cause)}`:""}</p>${searched}${row.advice?`<p><strong>${t("research_q.advice_label")}</strong> ${escape(row.advice.diagnosis)}</p>`:""}<p>${decision}</p></div>`;
  };
  // The second mark is the whole-dossier audit: a ledger without objection data (an older run) shows none.
  const round=Number(ledger.audit_round||0), audited=all.some(row=>Array.isArray(row.objections));
  const auditMark=row=>{
    if(!Array.isArray(row.objections)||row.accepted_gap)return null;
    const open=row.objections.length;
    // Reworked and checked again: the objections wait for the next round to close them, nothing is wrong meanwhile.
    if(open&&row.status==="verified")return {mark:"◐",kind:"reworked",text:tp("audit.reworked",{count:open,round:round+1})};
    if(open)return {mark:"⚠",kind:"open",text:tp("audit.open",{count:open})};
    if(ledger.phase==="completed"||(round>0&&row.status==="verified"))return {mark:"✓",kind:"passed",text:tp("audit.passed")};
    return {mark:"○",kind:"pending",text:tp("audit.pending")};
  };
  const marks=all.map(auditMark).filter(Boolean), count=kind=>marks.filter(m=>m.kind===kind).length;
  const auditLegend=audited?`<p class="hint">${t("audit.legend")}${round>0||ledger.phase==="completed"?` ${t("audit.summary",{items:["passed","reworked","open","pending"].filter(kind=>count(kind)).map(kind=>tp(`audit.count.${kind}`,{count:count(kind),round:round+1})).join(", ")})}`:""}</p>`:"";
  const rows=all.map(row=>{
    const blocked=row.status==="blocked"&&!row.accepted_gap, audit=auditMark(row);
    const answer=row.status==="verified"&&row.answer?`
      <div class="prose">${renderMarkdown(row.answer)}</div>
      ${(row.findings||[]).map(f=>`<p>${escape(f.statement)}</p>`).join("")}
      ${row.sources?.length?`<p>${t("research_q.read_sources")}</p><ul>${row.sources.map(source=>`<li>${markdownLink(escape(source.title),source.url)}${source.page?`, ${t("research_q.page",{page:Number(source.page)})}`:""}</li>`).join("")}</ul>`:""}
      ${row.limits?.length?`<p>${t("research_q.limits")}</p><ul>${row.limits.map(l=>`<li>${escape(l)}</li>`).join("")}</ul>`:""}
      ${row.access_gaps?.length?`<p class="note">${t("research_q.access_gap",{gaps:asHtml(row.access_gaps.map(g=>t("research_q.access_gap_item",{criterion:Number(g.criterion),source:g.source,evidence:g.evidence})).join("; "))})}</p>`:""}`:"";
    const state=row.accepted_gap?tp("research_q.outcome.accepted_gap"):blocked&&row.access_gap_requested?tp("research_q.access_accepted"):blocked&&row.retry_requested?tp("research_q.retry_state")
      :!active&&["researching","reviewing"].includes(row.status)?tp("research_q.started"):(states[row.status]||row.status);
    return `<details data-research-question="${escape(row.id)}"${opened.has(row.id)?" open":""}>
      <summary>${row.status==="verified"?"✓":row.accepted_gap?"–":blocked?(row.retry_requested||row.access_gap_requested?"↻":"⛔"):active&&activeIds.includes(row.id)?"●":"○"}${audit?`<span class="audit-mark" title="${escape(audit.text)}">${audit.mark}</span>`:""} ${escape(row.question)} · ${escape(state)}${audit?` · ${escape(audit.text)}`:""}</summary>
      ${blocked?blockedNote(row):""}${["open","reworked"].includes(audit?.kind)?`<div class="note"><p><strong>${t("research_q.objections")}${audit.kind==="reworked"?` (${t("research_q.objections_reworked",{round:round+1})})`:""}:</strong></p><ul>${row.objections.map(o=>`<li>${escape(o.reason)}</li>`).join("")}</ul></div>`:""}
      <p>${escape(row.activity)}</p>
      <p class="hint">${t("research_q.read",{sections:Number(row.read_sections),steps:Number(row.steps)})}${row.reopened?` · ${t("research_q.reopened",{count:Number(row.reopened)})}`:""}${Number(row.revalidations)>0?` · ${t("research_q.revalidated",{count:Number(row.revalidations)})}`:""}</p>
      ${row.support?`<p class="hint">${t("research_q.support",{count:row.support.findings.filter(f=>f.empirical_status==="independently_tested").length})}</p>`:""}
      ${row.outcome&&!blocked?`<p class="hint">${t("research_q.result",{outcome:outcomes[row.outcome]||row.outcome})}</p>`:""}
      <p>${t("research_q.criteria")}</p><ul>${(row.acceptance||[]).map(c=>`<li>${escape(c)}</li>`).join("")}</ul>
      ${row.reason&&!blocked?`<p><strong>${t("research_q.still_open")}</strong> ${escape(row.reason)}</p>`:""}${row.reopenable&&!blocked?`<p class="hint">${t("research_q.reopenable",{resume})}</p>`:""}${row.accepted_gap?`<p class="hint">${t("research_q.gap_kept")}${row.accepted_reason?`: ${escape(row.accepted_reason)}`:"."}</p>`:""}${answer}</details>`;
  }).join("");
  return `<section class="research-questions">
    <p><strong>${t("research_q.closed",{closed:Number(ledger.closed),total:Number(ledger.total)})}${Number(ledger.accepted)>0?` · ${t("research_q.accepted",{count:Number(ledger.accepted)})}`:""}</strong></p>
    ${researchRound(ledger)}${auditLegend}${residualFinish(ledger,runId)}
    <progress value="${Number(ledger.closed)}" max="${Number(ledger.total)}" aria-label="${t("research_q.progress_label")}"></progress>
    ${ledger.phase==="awaiting_plan_approval"?"":`<p>${escape(phases[ledger.phase]||"")}</p>`}${renderActiveTasks(ledger,active)}${budgetNote}
    <p class="hint">${t("research_q.fixed_hint")}</p>${rows}</section>`;
}
const calibrationSources=Object.fromEntries(["run","project","default"].map(id=>[id,tp(`calibration.${id}`)]));
function planSummary(p) {
  const hours=Number(p.projected_hours), minutes=Number(p.seconds_per_call)/60;
  const number=(n,d)=>fmt.number(n,{minimumFractionDigits:0,maximumFractionDigits:d});
  const cost=p.cost?.expected_total_usd!=null?`, ${tp("plan.cost",{cost:usd(p.cost.expected_total_usd)})}${p.cost.limit_usd?` ${tp("plan.cost_limit",{limit:usd(p.cost.limit_usd)})}`:""}`:"";
  return `${tp("plan.summary",{tasks:Number(p.tasks),calls:Number(p.projected_calls),hours:number(hours,hours>=10?0:1),minutes:number(minutes,1)})}${cost}`;
}
function planApprovalRequest(runId) {
  // The receipt approves exactly the shown plan; a cap asks for one re-plan that is shown again before it runs.
  const payload={kind:"plan",run_id:runId};
  const raw=String($("plan-max-tasks")?.value??"").trim();
  if(raw){const n=Number(raw);if(!Number.isInteger(n)||n<1)throw new Error(tp("plan.cap_invalid"));payload.max_tasks=n;}
  return payload;
}
// The limits a single approval raises so the plan fits (question_budget.plan_projection's raise_to): only those that
// rise, by their approval field.
function planRising(p) {
  const raise=p?.raise_to||{}, rising={};
  for(const [key,current] of [["model_calls",p?.approved_limit],["search_rounds",p?.search_rounds_limit],["sources",p?.sources_limit]])
    if(Number.isInteger(raise[key])&&raise[key]>Number(current||0))rising[key]=raise[key];
  return rising;
}
// The plan in one plain sentence (D-155): hours, calls, sources and search rounds, and whether that fits the limits
// or which of them rise. A projection written before 2026-10-07 names no sources or rounds and keeps its summary.
function planSentence(p) {
  if(!("projected_sources" in p))return escape(planSummary(p));
  const hours=Number(p.projected_hours), number=(n,d)=>fmt.number(n,{minimumFractionDigits:0,maximumFractionDigits:d});
  const values={count:Number(p.tasks),hours:number(hours,hours>=10?0:1),calls:Number(p.projected_calls),
    sources:Number(p.projected_sources),rounds:Number(p.projected_search_rounds)};
  const rising=Object.entries(planRising(p));
  if(!rising.length)return t("plan.fits",values);
  return t("plan.raise",{...values,limits:fmt.list(rising.map(([key,n])=>tp(`plan.limit.${key}`,{count:n})))});
}
function renderPlanReview(job, runId) {
  const review=job?.progress?.plan_review, p=review?.projection;
  if(!review?.awaiting||!p||job?.status==="running")return "";
  const current="projected_sources" in p, rising=planRising(p), raising=current&&Object.keys(rising).length>0;
  const number=n=>fmt.number(n,{minimumFractionDigits:0,maximumFractionDigits:1});
  // Where each rate comes from, the sources' and search rounds' included (calibrationSources, search_rates_source).
  const search=current&&"sources_per_task" in p?` ${t("plan.search_basis",{sources:number(p.sources_per_task),rounds:number(p.search_rounds_per_task),source:calibrationSources[p.search_rates_source]||tp("calibration.default")})}`:"";
  const basis=`<p class="hint">${t("plan.basis",{per:Number(p.expected_calls_per_task),source:calibrationSources[p.expected_calls_source]||tp("calibration.default"),closing:Number(p.closing_calls),duration_source:calibrationSources[p.seconds_per_call_source]||tp("calibration.default"),limit:Number(p.approved_limit),used:Number(p.used)})}${p.within_limit===false&&!raising?` ${t("plan.not_enough")}`:""}${search}</p>`;
  const cost=current&&p.cost?.expected_total_usd!=null?`<p class="hint">${t("plan.cost_line",{cost:`${tp("plan.cost",{cost:usd(p.cost.expected_total_usd)})}${p.cost.limit_usd?` ${tp("plan.cost_limit",{limit:usd(p.cost.limit_usd)})}`:""}`})}</p>`:"";
  if(review.approved)return `<section class="plan-review"><strong>${t("plan.approved_title")}</strong><p>${planSentence(p)}</p>${cost}${basis}<p class="hint">${t("plan.approved_hint",{resume:quoted(tp("button.resume"))})}${review.approval?.max_tasks?` ${t("plan.cap_requested",{count:Number(review.approval.max_tasks)})}`:""}</p><div class="actions"><button data-action="resume" data-run-id="${escape(runId)}" ${running()?"disabled":""}>${t("button.resume")}</button></div></section>`;
  const caps=(p.plan_caps||[]).map(Number).filter(Number.isInteger);
  const capNote=caps.length&&Number(p.tasks)>Math.min(...caps)?`<p class="note">${t("plan.cap_note",{cap:Math.min(...caps)})}</p>`:"";
  // A projection without raise_to (before 2026-10-07) keeps the separate call-limit button: the remaining calls and a
  // tenth more for reading and correction steps. One with raise_to raises its limits in the approval's own click.
  const raise=!current&&p.within_limit===false?Number(p.used)+Math.ceil(Number(p.projected_calls)*11/10):0;
  const attributes=raising?[["calls","model_calls"],["rounds","search_rounds"],["sources","sources"]].filter(([,key])=>rising[key]).map(([name,key])=>` data-raise-${name}="${Number(rising[key])}"`).join(""):"";
  return `<section class="plan-review"><strong>${t("research_q.phase.awaiting_plan_approval")}</strong><p>${planSentence(p)}</p>${cost}${basis}${capNote}
    <div class="field"><label for="plan-max-tasks">${t("plan.cap_label")}</label><input id="plan-max-tasks" type="number" inputmode="numeric" min="1" max="${Number(p.tasks)}" step="1" placeholder="${Number(p.tasks)}"></div>
    <div class="actions"><button data-action="approve-plan" data-run-id="${escape(runId)}"${attributes} data-then-resume="1" ${running()?"disabled":""}>${t(raising?"plan.approve_raise":"plan.approve")}</button>${raise>Number(p.approved_limit)?`<button class="secondary" data-action="approve-calls" data-run-id="${escape(runId)}" data-model-calls="${raise}">${t("research_q.raise",{count:raise})}</button>`:""}</div>
    <p class="hint">${t("plan.hint")}${raising?` ${t("plan.cap_no_raise")}`:""}</p></section>`;
}
// A criterion that needs a source the run could not read: accepted as an access gap, the question keeps its verified parts.
// The refused source the editor chose per question, kept across redraws: accepting an access gap waits for it.
const accessChoices={};
function accessGapForm(row, runId, blocked) {
  if(!blocked.length||row.outcome==="prerequisite_block"||!(row.acceptance||[]).length)return "";
  // The failed criterion's number, as the pipeline's German reason names it.
  const failed=Number((/Kriterium (\d+)/.exec(row.reason||"")||[])[1]);
  const criteria=row.acceptance.map((text,i)=>`<option value="${i}"${i===failed?" selected":""}>${t("access.criterion",{index:i,text:shortText(text,90)})}</option>`).join("");
  // No address is preselected: which refused source the criterion needs is the editor's call, not the list order.
  const chosen=accessChoices[row.id]||"";
  const sources=blocked.map(s=>`<option value="${escape(s.url)}"${chosen&&s.url===chosen?" selected":""}>${escape(shortText(s.url,70))} · ${escape(shortText(s.evidence,50))}</option>`).join("");
  // A book or article of the missing works that this question needs may exist as a library copy: uploading it is the
  // highlighted way (provided_works); otherwise accepting the access gap is (D-155).
  const work=(project?.works?.missing||[]).find(w=>!w.provided&&(w.tasks||[]).some(task=>task.id===row.id));
  const sentence=Number.isInteger(failed)&&row.acceptance[failed]!==undefined
    ?`<p>${work?t("access.sentence",{criterion:failed,source:work.work}):t("access.sentence_any",{criterion:failed})}</p>`:"";
  const upload=work?`<button class="small" data-scroll="works-panel">${t("access.upload_work")}</button>`:"";
  return `${sentence}<div class="actions access-gap">${upload}<select id="access-criterion-${escape(row.id)}" aria-label="${t("access.criterion_label")}">${criteria}</select><select id="access-source-${escape(row.id)}" aria-label="${t("access.source_label")}"><option value=""${chosen?"":" selected"}>${t("access.choose")}</option>${sources}</select><button class="${work?"secondary small":"small"}" id="access-accept-${escape(row.id)}" data-action="accept-access-gap" data-run-id="${escape(runId)}" data-task-id="${escape(row.id)}" ${chosen?"":"disabled"}>${t("access.accept")}</button></div><p class="hint">${t("access.hint")}</p>`;
}
// searchRaise: the search-round limit a blocked search offers to raise to (sizedRaise), none when 0.
function gapActionsFor(row, runId, searchRaise, blocked=[]) {
  // A block on the run's search rounds: the ledger names its cause (row.block_cause, D-152); rows saved before the field
  // existed only say it in their German reason.
  const searchBlocked=row.outcome==="budget_block"&&(row.block_cause?row.block_cause==="search_budget":/Suchbudget|Suchrunden/.test(row.reason||""));
  // The advisor's current recommendation is the highlighted button, the other choice stays beside it (D-155).
  const gapAdvised=currentAdvice(row)?.recommendation==="accept_gap";
  // A question that only waits for its prerequisite has no failed attempt of its own; retrying the prerequisite takes it up again.
  const retry=row.outcome==="prerequisite_block"?`<span class="hint">${t("research_q.prereq_retry")}</span>`:`<input id="retry-hint-${escape(row.id)}" value="${escape(row.advice?.hint||"")}" placeholder="${t("gap.retry_placeholder")}" aria-label="${t("gap.retry_label")}"><button class="${gapAdvised?"secondary small":"small"}" data-action="retry-task" data-run-id="${escape(runId)}" data-task-id="${escape(row.id)}">${t("gap.retry")}</button>`;
  const gap=`<input id="gap-reason-${escape(row.id)}" placeholder="${t("gap.reason_placeholder")}" aria-label="${t("gap.reason_label")}"><button class="${gapAdvised?"small":"secondary small"}" data-action="accept-gap" data-run-id="${escape(runId)}" data-task-id="${escape(row.id)}">${t("gap.accept")}</button>`;
  const raise=Number(searchRaise)>0?Number(searchRaise):0;
  return `<div class="actions">${retry}</div>${accessGapForm(row,runId,blocked)}<div class="actions">${gap}${searchBlocked&&raise?`<button class="secondary small" data-action="approve-search" data-run-id="${escape(runId)}" data-search-rounds="${raise}">${t("gap.raise_search",{count:raise})}</button>`:""}</div>`;
}
// The search rounds and sources that let every open question search again (D-155, replacing the fixed +6 and +40):
// the questions not answered yet, blocked ones included, at the higher of the plan's rate per sub-question and the
// run's measured one (from three verified questions on), a quarter to spare, at least one more than the limit. A run
// whose plan named no rates (before 2026-10-07) keeps the fixed steps. A limit the page does not know stays null.
function sizedRaise(j) {
  const progress=j?.progress||{}, ledger=progress.research_questions||{}, rows=ledger.questions||[], p=progress.plan_review?.projection||{};
  const open=rows.filter(q=>q.status!=="verified"&&!q.accepted_gap).length, verified=rows.filter(q=>q.status==="verified").length;
  const size=(limit,used,planned,before,step)=>{
    if(!(limit>0))return null;
    const rates=[Number(planned)];
    if(verified>=3&&Number.isFinite(Number(before)))rates.push((used-Number(before))/verified);
    const known=rates.filter(rate=>Number.isFinite(rate)&&rate>=0);
    if(!known.length)return limit+step;
    return Math.max(limit+1,used+Math.ceil(open*Math.max(...known)*1.25));
  };
  return {sources:size(Number(progress.source_limit||0),Number(ledger.source_attempt_count||0),p.sources_per_task,p.sources_used,40),
    search_rounds:size(Number(progress.search_round_limit||0),Number(progress.search_rounds||0),p.search_rounds_per_task,p.search_rounds_used,6)};
}
// Disputes of the current audit round: every one side by side with its two positions; once all are decided,
// the run resumes once instead of stopping at each in turn.
function disputes(j) {
  const rows=j.progress?.review_disagreements;
  return Array.isArray(rows)&&rows.length?rows:(j.progress?.review_disagreement?[j.progress.review_disagreement]:[]);
}
function renderDisputeCard(j, runId, active) {
  const rows=disputes(j);
  if(active||!rows.length||stopInfo(j)?.code!=="review_disagreement")return "";
  const open=rows.filter(d=>!d.decision?.decision);
  const item=(d,n)=>{
    const decided=d.decision?.decision, id=escape(d.objection_id);
    const choice=decided?`<p class="hint">${t("dispute.decided",{choice:tp(decided==="reviewer"?"dispute.followed":"dispute.kept")})}${d.decision.note?` (${escape(d.decision.note)})`:""}.</p>`:
      `<input id="dispute-note-${id}" placeholder="${t("residual.note_placeholder")}" aria-label="${t("dispute.note_label")}"><div class="actions"><button data-action="decide-dispute" data-decision="reviewer" data-objection-id="${id}" data-run-id="${escape(runId)}">${t("dispute.follow")}</button><button class="secondary" data-action="decide-dispute" data-decision="objection" data-objection-id="${id}" data-run-id="${escape(runId)}">${t("dispute.keep")}</button></div>`;
    return `<li><p>${rows.length>1?`<strong>${n+1}.</strong> `:""}${t("dispute.question",{question:asHtml(`<strong>${escape(d.question||d.task_id||"")}</strong>`)})}</p>
      <div class="note"><p><strong>${t("dispute.earlier")}</strong> ${escape(d.objection?.reason||"")}</p>${d.objection?.correction?`<p class="hint">${t("dispute.demanded",{correction:d.objection.correction})}</p>`:""}
      <p><strong>${t("dispute.reviewer_now")}</strong> ${escape(d.review?.reason||"")}</p></div>${choice}</li>`;
  };
  const resume=!open.length?`<div class="actions"><button data-action="resume" data-run-id="${escape(runId)}" ${running()?"disabled":""}>${t("button.resume")}</button></div>`:"";
  // One plain sentence first (D-155); following the reviewer is the highlighted choice, upholding stays beside it.
  return `<section class="panel decision-card" aria-label="${t("dispute.label")}"><h2>${t("dispute.title",{count:rows.length})}</h2>
    <p>${t("dispute.sentence")}</p><p class="hint">${t("dispute.hint")} ${t(rows.length>1?"dispute.after_all":"dispute.after_one")}</p>
    <ol class="decisions">${rows.map(item).join("")}</ol>${resume}</section>`;
}
// Every open decision stands in one card above the ledger with its buttons visible: nothing to expand, no dialog.
function renderResearchDecisions(j, r, active, reopenable, resumable, searchLimit) {
  const ledger=j.progress.research_questions, rows=ledger?.questions||[], runId=r?.run_id||"";
  if(active||!rows.length)return "";
  const resume=quoted(tp("button.resume"));
  // With the finish requested, a question blocked only by spent reworks is decided: its objections go on record.
  // A run that keeps such answers (keeps_spent_answers) decides it the same way, without the finish.
  const residual=rows.filter(q=>(ledger.residual_finish||ledger.keeps_spent_answers)&&q.status==="blocked"&&!q.accepted_gap&&q.outcome==="audit_block");
  const open=rows.filter(q=>q.status==="blocked"&&!q.accepted_gap&&!residual.includes(q)), accepted=rows.filter(q=>q.accepted_gap);
  const undecided=open.filter(q=>!q.retry_requested&&!q.access_gap_requested), retrying=open.filter(q=>q.retry_requested||q.access_gap_requested);
  // Accepted gaps alone are no decision; they only lead the card while the run stopped for the blocked questions.
  const code=stopInfo(j)?.code;
  if(!open.length&&!((accepted.length||residual.length)&&(!code||code==="research_questions_blocked")))return "";
  const rounds=Number(j.progress.search_rounds||0), roundLimit=Number(j.progress.search_round_limit||0);
  // Every web search loads new sources; at the run's source limit it ends before it starts.
  const fetched=Number(ledger.source_attempt_count||0), sourceLimit=Number(j.progress.source_limit||0);
  const sized=sizedRaise(j), advised=adviceRetries(ledger);
  const full=sourceLimit>0&&fetched>=sourceLimit, spent=roundLimit>0&&rounds>=roundLimit, low=roundLimit>0&&roundLimit-rounds<=1;
  const raiseAdvised=open.filter(q=>currentAdvice(q)?.recommendation==="raise_limit"&&["sources","search_rounds"].includes(q.advice.limit));
  // Limits that stop the open questions: one sentence and one button that raises both to the sized values, adopts the
  // advisor's retries and resumes (D-155); deciding each question on its own stays below. Without advice to adopt the
  // button only raises. A run whose page knows only one of the two limits keeps the earlier notes.
  let limitsNote="", roundsNote="", sourcesNote="";
  if((full||low||raiseAdvised.length)&&sized.sources&&sized.search_rounds){
    const values={count:open.length,sources:sized.sources-sourceLimit,rounds:sized.search_rounds-roundLimit};
    const sentence=full?t("decisions.raise.sources",{...values,used:fetched,limit:sourceLimit})
      :spent?t("decisions.raise.rounds",{...values,used:rounds,limit:roundLimit})
      :low?t("decisions.raise.low",{...values,used:rounds,limit:roundLimit})
      :t("decisions.raise.advice",{...values,count:raiseAdvised.length});
    // The advice itself needs room too, where the run could not afford it yet.
    const calls=unadvised(ledger)&&!adviceAffordable(ledger)?` data-model-calls="${adviceCallLimit(ledger)}"`:"";
    const target=`data-run-id="${escape(runId)}" data-sources="${sized.sources}" data-search-rounds="${sized.search_rounds}"${calls}`;
    const button=running()?"":advised.length?`<button data-action="raise-adopt" ${target}>${t("decisions.raise_adopt")}</button>`
      :`<button class="secondary" data-action="approve-limits" ${target}>${t("decisions.raise_limits",{sources:sized.sources,rounds:sized.search_rounds})}</button>`;
    limitsNote=`<p>${sentence}</p>${button?`<div class="actions">${button}</div>`:""}`;
  }else{
    if(low&&sized.search_rounds)roundsNote=`<p class="hint">${t("decisions.rounds",{used:rounds,limit:roundLimit})} <button class="secondary small" data-action="approve-search" data-run-id="${escape(runId)}" data-search-rounds="${sized.search_rounds}">${t("gap.raise_search",{count:sized.search_rounds})}</button></p>`;
    if(full&&sized.sources)sourcesNote=`<p class="hint">${t("decisions.sources",{fetched,limit:sourceLimit})} <button class="secondary small" data-action="approve-sources" data-run-id="${escape(runId)}" data-sources="${sized.sources}">${t("decisions.raise_sources",{count:sized.sources})}</button></p>`;
  }
  const names=new Map(rows.map(q=>[q.id,q.question]));
  const outcomes=Object.fromEntries(["audit_block","extraction_block","search_block","evidence_block","budget_block","access_block","prerequisite_block"]
    .map(id=>[id,tp(`decisions.outcome.${id}`)]));
  // The advisor's second opinion stands with the question: cause, recommendation, the sources it found.
  const recommendations={retry:tp("gap.retry"),accept_gap:tp("gap.accept"),raise_limit:tp("decisions.raise_limit")};
  const limit=Number(ledger.auto_retry_limit||5);
  const limitNames=Object.fromEntries(["sources","search_rounds","model_calls"].map(id=>[id,tp(`decisions.limit.${id}`)]));
  const advice=q=>{
    const a=q.advice;
    if(!a)return "";
    const recommendation=a.recommendation==="raise_limit"&&limitNames[a.limit]?limitNames[a.limit]:recommendations[a.recommendation]||a.recommendation;
    const sources=(a.sources||[]).map(s=>`<li>${s.url?markdownLink(escape(s.title),s.url):escape(s.title)}${s.note?`<span class="hint"> · ${escape(s.note)}</span>`:""}</li>`).join("");
    const auto=Number(q.auto_retries||0), stop={limit:tp("decisions.auto_spent",{count:limit}),
      no_progress:tp("decisions.auto_no_progress")}[q.auto_stop]||"";
    return `<div class="advice">${plainAdvice(q)}<p><strong>${t("research_q.advice_label")}</strong> ${escape(a.diagnosis)}</p><p class="hint">${t("decisions.recommendation",{recommendation})}${auto?` · ${t("decisions.auto_attempts",{done:auto,limit})}`:""}${stop?` · ${escape(stop)}`:""}</p>${sources?`<ul>${sources}</ul>`:""}</div>`;
  };
  // The current advice in one plain sentence (D-155): a retry names the way to evidence the advisor sees and what a
  // new attempt costs (the run's calls per sub-question), a gap what accepting it means. A clause without its figure
  // is left out; a raise is said once, above the questions.
  const perTask=Number(ledger.budget_projection?.expected_calls_per_task);
  const plainAdvice=q=>{
    const a=currentAdvice(q);
    if(a?.recommendation==="accept_gap")return `<p>${t("decisions.advice.gap")}</p>`;
    if(a?.recommendation!=="retry")return "";
    const work=shortText((a.sources||[]).map(s=>s.title).filter(Boolean).join("; ")||a.hint||"",120);
    const calls=Number.isFinite(perTask)&&perTask>0?perTask:0;
    return `<p>${work&&calls?t("decisions.advice.retry_work_calls",{work,calls}):work?t("decisions.advice.retry_work",{work})
      :calls?t("decisions.advice.retry_calls",{calls}):t("decisions.advice.retry_plain")}</p>`;
  };
  // Retry advice the automatic attempts did not act on: one click adopts every hint and resumes. Where limits stop the
  // questions, the raise above adopts them in its own click.
  const adoptAll=advised.length&&!running()&&!limitsNote.includes('data-action="raise-adopt"')?`<div class="actions"><button data-action="apply-advice" data-run-id="${escape(runId)}">${t("decisions.adopt")}</button><span class="hint">${t("decisions.adopt_hint",{count:advised.length})}</span></div>`:"";
  const item=q=>{
    const deps=(q.depends_on||[]).filter(id=>rows.some(x=>x.id===id&&x.status!=="verified")).map(id=>names.get(id)||id);
    const attempts=Number(q.web_attempts||0);
    const state=attempts?tp("decisions.web_searches",{count:attempts}):tp(Number(q.steps||0)?"decisions.no_web":"decisions.not_started");
    return `<li><strong>${escape(q.question)}</strong><p class="hint">${escape(outcomes[q.outcome]||q.outcome||tp("research_q.state.blocked"))} · ${escape(state)}${deps.length?` · ${t("decisions.depends",{deps:deps.join("; ")})}`:""}</p>${q.reason?`<p class="hint">${escape(q.reason)}</p>`:""}${advice(q)}${q.access_gap_requested?`<p class="hint">${t("decisions.access_requested",{gaps:asHtml((q.requested_access_gaps||[]).map(g=>t("decisions.access_item",{criterion:Number(g.criterion),source:g.source})).join("; ")),resume})}</p>`:q.retry_requested?`<p class="hint">${t("decisions.retry_requested")}${q.retry_hint?` · ${t("decisions.hint",{hint:q.retry_hint})}`:""}. ${t("decisions.retry_starts",{resume})}</p>`:q.reopenable?`<p class="hint">${t("decisions.reopenable",{resume})}</p>`:gapActionsFor(q,runId,limitsNote?0:sized.search_rounds||Number(searchLimit)+6,ledger.blocked_sources||[])}</li>`;
  };
  const closing=Number(ledger.budget_projection?.closing_calls||0);
  const intro=undecided.length?`${t("decisions.intro.blocked",{count:undecided.length})} ${reopenable?t("decisions.intro.reopenable",{resume}):unadvised(ledger)&&adviceAffordable(ledger)?t("decisions.intro.advise",{resume}):unadvised(ledger)?t("decisions.intro.no_budget",{remaining:Number(ledger.budget_projection.remaining),minimum:Number(ledger.budget_projection.minimum_remaining_calls)}):`${t("decisions.intro.decide",{resume,closed:Number(ledger.closed)})}${closing?` (${t("decisions.calls",{count:closing})})`:""}.`}`
    :retrying.length?t("decisions.intro.retrying",{count:retrying.length,resume}):t("decisions.intro.done",{resume});
  return `<section class="panel decision-card" aria-label="${t("decisions.title")}"><h2>${t("decisions.title")}</h2><p>${intro}</p>${limitsNote}${adoptAll}${roundsNote}${sourcesNote}${unadvised(ledger)&&!adviceAffordable(ledger)&&!running()&&!limitsNote.includes('data-action="raise-adopt"')?`<div class="actions"><button class="secondary" data-action="approve-calls" data-run-id="${escape(runId)}" data-model-calls="${adviceCallLimit(ledger)}" data-then-resume="1">${t("decisions.raise_advise",{count:adviceCallLimit(ledger)})}</button></div>`:""}<ol class="decisions">${open.map(item).join("")}${accepted.map(q=>`<li class="done">✓ ${escape(q.question)} · ${t("decisions.accepted")}${q.accepted_reason?` (${escape(q.accepted_reason)})`:""}</li>`).join("")}${residual.map(q=>`<li class="done">✓ ${escape(q.question)} · ${t(ledger.residual_finish?"decisions.residual.finish":"decisions.residual.kept")}</li>`).join("")}</ol>${resumable?`<div class="actions"><button data-action="resume" data-run-id="${escape(runId)}" ${running()?"disabled":""}>${t("button.resume")}</button></div>${running()?`<p class="hint">${escape(otherJobText())}</p>`:""}`:""}</section>`;
}
// The first retrieval reads every found source; its counter and its report stand where the ledger will appear.
function renderRetrieval(retrieval) {
  if(!retrieval)return "";
  const failures=retrieval.failures||[], failed=Number(retrieval.failed||0);
  const running=retrieval.running?`<p><strong>${t("retrieval.label")}</strong> ${t("retrieval.counts",{attempted:Number(retrieval.attempted),total:Number(retrieval.total),imported:Number(retrieval.imported)})}${failed?`, ${t("retrieval.failed",{count:failed})}`:""}.</p><progress value="${Number(retrieval.attempted)}" max="${Math.max(1,Number(retrieval.total))}" aria-label="${t("retrieval.bar_label")}"></progress>`:"";
  const report=failed?`<details class="retrieval-report"><summary>${t("retrieval.report",{count:failed})}${retrieval.running?"":` · ${t("retrieval.first")}`}</summary>${retrieval.running?"":`<p class="hint">${t("retrieval.first_hint")}</p>`}<ul>${failures.map(row=>`<li>${escape(row.source)}<span class="hint"> · ${escape(row.reason)}</span></li>`).join("")}</ul>${failed>failures.length?`<p class="hint">${t("retrieval.more",{count:failed-failures.length})}</p>`:""}</details>`:"";
  return running+report;
}
// The research page owns the decision and the ledger; the drawer only carries telemetry.
function renderResearchPanel(j,r,active,questionOpen,researchOpen,researchBlocked,reopenable,resumable) {
  const p=j.progress, ledger=p.research_questions, quality=p.research_quality, runId=r?.run_id||"";
  const resume=quoted(tp("button.resume"));
  let html=renderPlanReview(j,runId)+renderDisputeCard(j,runId,active)+renderResearchDecisions(j,r,active,reopenable,resumable,p.search_round_limit)+renderRetrieval(p.retrieval);
  // What runs right now and what the user can do stand above the question rows, never below them.
  const open=active?(p.open_calls||[]):[];
  const planWaiting=!active&&p.plan_review?.awaiting&&!p.plan_review.approved;
  const current=(planWaiting||!p.activity?"":`<p class="current-step"><strong>${t(active?(open.length>1?"panel.reported":"panel.now"):"panel.last")}:</strong> ${escape(p.activity)}</p>`)+
    (open.length>1?`<p class="hint">${t("panel.calls",{count:open.length,calls:open.map(row=>tp("panel.call_row",{label:shortText(row.label||tp("panel.step_default"),60),age:progressAge(row.started_at)})).join("; ")})}</p>`:"")+
    (active&&p.stopping?`<p class="note" role="status"><strong>${t("panel.stopped_question")}${p.stopping.question?`: ${escape(quoted(shortText(p.stopping.question,80)))}`:""}.</strong> ${t("panel.stopping_tail")}</p>`:"");
  const next=active?`<p class="next-step"><strong>${t("panel.next_label")}</strong> ${t("panel.nothing")}${p.activity?` (${escape(p.activity)})`:""}. ${t("panel.duration")}</p>`:"";
  if(ledger){
    if(reopenable)html+=`<p>${t("panel.reopenable",{count:Number(ledger.reopenable),resume})}</p>`;
    else if(researchBlocked&&unadvised(ledger)&&adviceAffordable(ledger))html+=`<p>${t("panel.unadvised",{count:unadvised(ledger),resume})}</p>`;
    else if(researchBlocked)html+=`<p>${t("panel.exhausted")}</p>`;
    html+=current+next+renderResearchQuestions(ledger,questionOpen,active,runId,p.search_round_limit,p.search_rounds,p.source_limit);
  }
  else html+=current+next;
  if(Number(p.search_round_limit)>0)html+=`<p class="hint">${t("panel.rounds",{used:Number(p.search_rounds||0),limit:Number(p.search_round_limit)})}</p>`;
  if(quality){
    const pending=quality.assessment_status==="pending_after_source_review"||(ledger&&ledger.phase!=="completed");
    html+=`<details class="research-quality"${researchOpen?" open":""}><summary>${pending?t(ledger?"quality.pending_ledger":"quality.pending_sources"):`${t("quality.closed",{closed:Number(quality.closed),total:Number(quality.total)})}${quality.passed_with_noted_limits?` · ${t("quality.noted_limits")}`:""}`}</summary>${pending?`<p>${t(ledger?"quality.pending_ledger_text":"quality.pending_sources_text")}</p>`:""}<p>${t("quality.criteria")}</p>${quality.passed_with_noted_limits&&!pending?`<p class="hint">${t("quality.noted_hint")}</p>`:""}${(quality.requirements||[]).map(row=>`<p><strong>${pending?"·":row.passed?"✓":row.recorded_limit||row.source_limit?"◇":"○"} ${escape(row.question)}</strong>${!pending&&!row.passed&&(row.recorded_limit||row.source_limit)?` · ${t("quality.as_limit")}`:""}</p><p>${escape(row.reason)}</p>${(row.missing||[]).length?`<ul>${row.missing.map(gap=>`<li>${escape(gap)}</li>`).join("")}</ul>`:""}`).join("")}${quality.blocking_gaps?.length?`<p>${t("quality.more")}</p><ul>${quality.blocking_gaps.map(gap=>`<li>${escape(gap)}</li>`).join("")}</ul>`:""}</details>`;
  }
  return `<section class="panel research-panel">${html}</section>`;
}
function drawerToggle() {
  return `<button class="quiet small drawer-toggle" data-action="drawer-toggle" aria-expanded="${drawerOpen}" aria-controls="job-status">${t(drawerOpen?"drawer.close":"drawer.title")}</button>`;
}
function drawerMarkup(summary, body) {
  return `<div class="drawer-head"><button class="quiet small drawer-toggle" data-action="drawer-toggle" aria-expanded="${drawerOpen}">${drawerOpen?"▾":"▴"} ${t("drawer.title")}</button><span class="hint">${summary}</span></div><div class="drawer-body"${drawerOpen?"":" hidden"}>${body}</div>`;
}
function dock(show) { document.body?.classList?.toggle?.("has-dock",!!show); }
// The reason stopped recordings share, or null when they stop for different ones. Until 2026-10-04 the bar said only
// "14 angehalten": fourteen recordings refused by OpenRouter for a key's credit limit named the reason on the
// recording page's job list alone.
function sharedAudioStop(p=project) {
  const infos=stoppedAudio(p).map(j=>stopInfo(j));
  return infos.length&&infos.every(info=>info.code===infos[0].code)?infos[0]:null;
}
// A stopped recording "Alle fortsetzen" may resume: one whose card offers to resume it, or a key stop once a key is there.
const resumableAudio=j=>canResume(j)||(!!stopInfo(j)?.actions.includes("key")&&keyAvailable("openrouter"))
  ||(!!stopInfo(j)?.actions.includes("google_key")&&keyAvailable("google"));
function audioJobSummary() {
  const jobs=project?.audio_jobs||[], active=jobs.filter(j=>j.status==="running");
  const title=id=>project.episodes?.find(e=>e.script.episode_id===id)?.script.title||id;
  const stopped=stoppedAudio().length, reason=sharedAudioStop(), because=reason?`: ${escape(reason.title)}`:"";
  const halted=stopped?` · ${t("audio_bar.halted",{count:stopped})}${because}`:"";
  if(active.length===1){const p=active[0].progress;return `${t("audio_bar.one",{title:title(active[0].episode)})}${p?.total_segments!==undefined?` · ${t("audio_bar.segments",{done:Number(p.completed_segments),total:Number(p.total_segments)})}`:""}${halted}`;}
  if(active.length)return `${t("audio_bar.many",{count:active.length})}${halted}`;
  const done=jobs.filter(j=>j.status==="completed").length;
  // Every recording finished: the header names what the project has, not how many jobs ran (D-160).
  return done===jobs.length?escape(projectSummary()):`${t("audio_bar.mixed",{done,total:jobs.length,stopped:jobs.length-done})}${because}`;
}
function renderAudioJobBar() {
  const active=(project?.audio_jobs||[]).some(j=>j.status==="running"), stopped=stoppedAudio(), reason=sharedAudioStop();
  const resume=stopped.length>1&&stopped.every(resumableAudio)?`<button class="small" data-action="resume-stopped-audio">${t("audio_bar.resume_all",{count:stopped.length})}</button>`:"";
  const key=reason?.actions.includes("key")?`<button class="secondary small" data-action="open-settings">${t("key.name.openrouter")}</button>`:"";
  // One line; the engine room has its own toggle in the dock (D-164: the header carried a second one).
  return `<span class="job-dot ${active?"running":stopped.length?"blocked":"done"}" aria-hidden="true"></span><span class="job-text">${audioJobSummary()}</span>${key}${resume}${step!==PAGE.audio&&(active||stopped.length)?`<button class="secondary small status-link" data-step="${PAGE.audio}">${t("audio_bar.view")}</button>`:""}`;
}
// Resumes every stopped recording at once, each as its own card's „Fortsetzen“ would; a refusal is named, not hidden.
async function resumeStoppedAudio() {
  const jobs=stoppedAudio().filter(resumableAudio), id=project.id, refused=[];
  if(!jobs.length)throw new Error(tp("audio_bar.none"));
  submitting=true;
  try {
    for(const job of jobs){
      try{await api(`/api/projects/${id}/start`,{action:"resume",run_id:job.run.run_id,episode:job.episode});}
      catch(error){refused.push(`${job.episode}: ${errorText(error)}`);}
    }
    project=await api(`/api/projects/${id}`);lastJobSignature=projectJobSignature(project);
  } finally { submitting=false; }
  lastJobView="";render();
  notice(refused.length?tp("audio_bar.partial",{done:jobs.length-refused.length,total:jobs.length,refused:refused.join("; ")})
    :tp("audio_bar.resumed",{count:jobs.length}),refused.length?"warn":"ok");
}
// The connection check in the page's language, with the fix next to every failed item: check.<name>.label and .fix
// (doctor's check names; two older ones carry a key's name).
const CHECK_IDS={"Gemini-TTS-Key":"gemini_tts_key","Google-Key":"google_key"};
function checkLabel(name) {
  const id=CHECK_IDS[name]||name;
  if(!hasText(`check.${id}.label`))return [name,""];
  return [tp(`check.${id}.label`),hasText(`check.${id}.fix`)?tp(`check.${id}.fix`):""];
}
function renderChecks(checks) {
  return `<ul class="checks">${(checks.checks||[]).map(c=>{const [label,fix]=checkLabel(c.name);
    return `<li>${c.ok?"✓":"○"} ${escape(label)}<span class="hint">${escape(c.detail)}</span>${!c.ok&&fix?`<span class="hint check-fix">${escape(fix)}</span>`:""}</li>`;}).join("")}</ul>
    <p>${t(checks.ready?"checks.ready":"checks.not_ready")} ${t("checks.no_audio")}</p>`;
}
const SUMMARY_STATES=Object.fromEntries(["unchanged","paused","summarizing","unavailable","off"].map(id=>[id,tp(`summary_state.${id}`)]));
// The run's periodic short report, written by a small model from the saved activity; drafts count as unchecked.
// A stopped run's brief never says it "is being written" (2026-10-07: it did, days after the stop).
function renderStatusSummary(summary, active=true) {
  const state=summary?.status==="summarizing"&&!active?null:SUMMARY_STATES[summary?.status];
  if(!summary?.summary&&!state)return "";
  return `<section class="status-summary"><strong>${t("summary_brief.title")}</strong>${summary.generated_at?`<span class="hint"${summary.model?` title="${escape(summary.model)}"`:""}> · ${t("summary_brief.age",{age:progressAge(summary.generated_at)})}</span>`:""}
    ${summary.summary?`<p>${escape(summary.summary)}</p>`:""}${state?`<p class="hint">${t("summary_brief.state",{state})}</p>`:""}</section>`;
}
function runningTitle(job) {
  const run=job.run, stage=Object.entries(run?.stages||{}).find(([,record])=>record?.status==="running")?.[0];
  if(job.progress?.phase==="foundation_research")return tp("running.foundation");
  if(job.action==="replan")return actionNames.replan;
  if(job.action==="plan"||(run?.kind==="script"&&stage==="planning"))return job.progress?.plan_repair?tp("running.plan_repair"):actionNames.plan;
  if(run?.kind==="script")return job.action==="revise"?actionNames.revise:tp("running.production");
  if(run?.kind==="research"||job.action==="research")return actionNames.research;
  if(run?.kind==="episode_audio")return actionNames.audio;
  return actionNames[job.action]||tp("job.running");
}
function offlineMark() {
  return connectionLost?`<span class="offline-mark" role="status">${t("offline.mark",{time:fmt.time(lastSyncAt,{hour:"2-digit",minute:"2-digit"})})}</span>`:"";
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
  // Selects too: the drawer redraws every few seconds while a job runs, and a choice in progress must survive it.
  const values=new Map(Array.from(el.querySelectorAll?.("input[id]:not([type=checkbox]),textarea[id],select[id]")||[],field=>[field.id,field.value]));
  const states=new Map(detailKeys(el).map(([key,detail])=>[key,detail.open]));
  const scrolls=new Map(scrollKeys(el).filter(([,node])=>node.scrollTop>0).map(([key,node])=>[key,node.scrollTop]));
  el.innerHTML=html;
  for(const [key,detail] of detailKeys(el))if(states.has(key))detail.open=states.get(key);
  for(const [key,node] of scrollKeys(el))if(scrolls.has(key))node.scrollTop=scrolls.get(key);
  for(const [id,value] of values){const field=value?document.getElementById(id):null;if(field)field.value=value;}
  // The model list belongs to the kept choice, not to the one the markup was drawn with.
  const choice=document.getElementById("text-switch-choice"), model=document.getElementById("text-switch-model");
  if(choice&&model)model.hidden=choice.value!=="openrouter";
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
function serverNote() {
  const s=(overviewPage?overviewData:project)?.server;
  if(!s?.stale&&!s?.restart_requested)return "";
  return s.restart_requested
    ?`<p>${t("server_note.requested")}</p><button class="quiet small" data-action="restart-cancel">${t("server_note.cancel")}</button>`
    :`<p>${t("server_note.stale")}</p><button class="small" data-action="restart-when-idle">${t("server_note.restart")}</button>`;
}
function renderServerNote() {
  const box=$("server-note");
  if(!box)return;
  const html=serverNote();
  box.hidden=!html;redraw(box,html);
  renderKeyNote();
}
// The reminder of a missing key on every page (studio.key_reminder): the Studio keeps its keys in memory only, so they
// are gone after every restart, and a fresh page said nothing of it (2026-10-04: Jev went without it unseen). It names
// each missing key, what needs it, in which projects, and what happens without it.
const KEY_NEED_TEXT=Object.fromEntries(["google_audio","gemini_audio","jev","openrouter_text","anthropic_text","perplexity_search"]
  .map(need=>[need,[tp(`key_need.${need}.what`),tp(`key_need.${need}.without`)]]));
function keyReminderRows() {
  const data=overviewPage?overviewData:settingsPage?settingsData:project;
  return ((data?.key_reminder??boot?.key_reminder)||[]).filter(row=>KEY_NEED_TEXT[row.need]);
}
// One folded line on every page: which keys are missing and for what. Opened, it says what waits without them and
// holds the fields (none on the settings page, whose key panels are right there). 2026-10-07: two full key forms
// stood above every page, about 300 px on a computer and one and a half screens on a phone.
function keyNote() {
  const rows=keyReminderRows();
  if(!rows.length)return "";
  const where=row=>row.projects.length>2?` (${t("key_note.projects",{count:row.projects.length})})`
    :` (${row.projects.map(topic=>escape(quoted(shortText(topic,48)))).join(", ")})`;
  const keys=["google","openrouter","anthropic","perplexity"].filter(key=>rows.some(row=>(row.key||"openrouter")===key));
  const mine=key=>rows.filter(row=>(row.key||"openrouter")===key);
  const summary=keys.map(key=>`${KEY_NAMES[key]} (${[...new Set(mine(key).map(row=>KEY_NEED_TEXT[row.need][0]))].map(escape).join(", ")})`).join(" · ");
  const details=keys.map(key=>`<p><strong>${t(`key.missing.${key}`)}.</strong> ${t("key_note.needed",{uses:asHtml(mine(key).map(row=>escape(KEY_NEED_TEXT[row.need][0])+where(row)).join(" · "))})} `+
    `${t("key_note.without",{consequences:mine(key).map(row=>KEY_NEED_TEXT[row.need][1]).join("; ")})}</p>`+
    (settingsPage?`<p class="hint">${t("key_note.below",{key:quoted(KEY_NAMES[key])})}</p>`:inlineKey(key==="openrouter"?"reminder-key":`reminder-${key}-key`,false,"",key,false))).join("");
  return `<details class="key-details"><summary><strong>${keys.length===1?t(`key.missing.${keys[0]}`):t("key_note.many",{count:keys.length})}</strong> · ${summary}</summary>${details}
    <p class="hint">${t("key_note.hint")}</p></details>`;
}
function renderKeyNote() {
  const box=$("key-note");
  if(!box)return;
  const html=keyNote();
  box.hidden=!html;redraw(box,html);
}
function clearKeyReminder(key="openrouter") {
  for(const data of [boot,overviewData,settingsData,project])
    if(data)data.key_reminder=(data.key_reminder||[]).filter(row=>(row.key||"openrouter")!==key);
  renderKeyNote();
}
function renderJob() {
  renderServerNote();
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
  if(audioPanel){const cards=renderAudioJobs();replaceKeeping(audioPanel,cards?`<section class="panel audio-jobs"><h2>${t("audio_jobs.title")}</h2>${cards}</section>`:"");}
  // A run recorded without a Studio job (an older project) still shows its stop and its resume.
  const job=j||(project?.run&&project.run.status!=="completed"?{status:project.run.status,run:project.run}:null);
  const info=stopInfo(job), owner=j?jobPage():job?runPage(job.run):null;
  const stopBox=$("stop-card");
  if(stopBox){
    // The stop card stands on the page that owns the job; audio cards and the chat carry their own.
    const own=owner===step&&job?.run?.kind!=="episode_audio"&&!["audio","assistant"].includes(job?.action);
    let html=!own?"":info&&!info.card?renderStopCard(job,info):job?.status==="running"?heartbeatNote(job):"";
    // A paused run of another lane keeps its stop card on its own page while the project shows a newer job
    // (2026-10-02: a later stop of another lane hid it).
    const parked=html?null:(project.parked_jobs||[]).find(p=>p?.id!==job?.id&&p.run?.kind!=="episode_audio"&&runPage(p.run)===step&&stopInfo(p));
    if(parked)html=renderStopCard(parked,stopInfo(parked));
    if(html!==stopHtml||(html&&stopBox.innerHTML===""))replaceKeeping(stopBox,html);
    stopHtml=html;
  }
  if(project.audio_jobs?.some(a=>a.id===j?.id)){
    bar.hidden=false;
    // The text run behind the recordings keeps its telemetry in the engine room (2026-10-07: after a recording the
    // drawer listed nineteen finished recordings with buttons on every page, and the script run's report was gone).
    const text=["research","script"].includes(project.main_job?.run?.kind)?project.main_job:null;
    const view=JSON.stringify({audio_jobs:project.audio_jobs,capacity:project.audio_capacity,submitting,drawerOpen,step,connectionLost,text:text&&{id:text.id,status:text.status,run:text.run?.run_id}});
    if(view===lastJobView)return;
    lastJobView=view;
    bar.innerHTML=offlineMark()+renderAudioJobBar();
    const body=audioTelemetry()+(text?telemetryBody(text):"");
    showDrawer(audioJobSummary(),body);
    return;
  }
  const research=$("research-progress");
  if(!job){clear();if(research)research.innerHTML="";return;}
  bar.hidden=false;
  const r=job.run, active=job.status==="running", state=job.status;
  const researchOpen=research?.querySelector?.(".research-quality")?.open;
  const questionOpen=new Set(Array.from(research?.querySelectorAll?.("[data-research-question][open]")||[],el=>el.dataset.researchQuestion));
  const previousTrace=box.querySelector?.(".trace-lines");
  const traceAtEnd=!previousTrace||previousTrace.scrollHeight-previousTrace.scrollTop-previousTrace.clientHeight<32;
  const traceScroll=previousTrace?.scrollTop||0;
  const audioRunning=(project.audio_jobs||[]).filter(a=>a.status==="running");
  // The server stamps each answer with its read time and the worker's heartbeat age; those alone are no change to redraw.
  const steady={...job,heartbeat_age_seconds:undefined,progress:job.progress&&{...job.progress,updated_at:undefined}};
  const view=JSON.stringify({project:project?.id,job:steady,drawerOpen,connectionLost,audio:audioRunning.map(a=>[a.id,a.progress?.completed_segments]),
    progressClock:active&&["script","research"].includes(job.progress?.phase)?Math.floor(Date.now()/10000):null,
    page:job.sample?null:step,minute:active?Math.floor((Date.now()-Date.parse(job.started_at))/60000):null,main:project.main_job?.id});
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
  // A finished job of its own (a chat, a check, a companion kit) leaves the header to the project's state (D-160).
  const finishedTitle=r?.kind==="research"?tp("research_q.phase.completed"):r?.kind==="script"?tp(job.action==="revise"?"job.revised":"job.scripts_done"):projectSummary();
  const title=active?runningTitle(job):decisionNeeded?tp("job.decisions",{count:openBlocked}):
    planReview?tp(planPending?"research_q.phase.awaiting_plan_approval":"job.plan_ready"):
    info?info.title:state==="completed"?finishedTitle:tp("job.saved");
  const destination=state==="completed"?runPage(r):owner;
  const links=pageKeys.map(key=>tp(`job.link.${key}`));
  const tone=active?"running":info?stopTone(info):"done";
  const calls=(Number.isSafeInteger(job.progress?.model_call_limit)&&job.progress.model_call_limit>0?` · ${t("job.calls",{used:Number(job.progress.model_calls||0),limit:job.progress.model_call_limit})}`:"")+
    (job.progress?.cost_spent_usd!==undefined?` · ${job.progress.cost_limit_usd?t("job.cost_limit",{spent:usd(job.progress.cost_spent_usd),limit:usd(job.progress.cost_limit_usd)}):t("job.cost",{spent:usd(job.progress.cost_spent_usd)})}`:"");
  const meta=active&&job.started_at?` · ${t("nav.hint.since",{elapsed:elapsedText(job.started_at)})}${calls}`:"";
  const lastLine=active?(job.progress?.model_trace?.lines||[]).at(-1):null;
  const audioNote=audioRunning.length?(step!==PAGE.audio?`<button class="quiet small status-link" data-step="${PAGE.audio}">${audioJobSummary()} →</button>`:`<span class="hint">${audioJobSummary()}</span>`):"";
  // A worker an earlier Studio started still runs: shown as running elsewhere; stopped only when its identity is on record.
  const elsewhere=active&&job.external?` · ${t("job.elsewhere_short")}`:"";
  // The header resumes only where plain "Fortsetzen" is the way on; where the stop card recommends fresh attempts it
  // leads there instead.
  const fresh=freshRecommended(job,info);
  bar.innerHTML=offlineMark()+`<span class="job-dot ${tone}${connectionLost?" offline":""}" aria-hidden="true"></span><span class="job-text"><strong>${escape(title)}</strong>${meta}${elsewhere}</span>`+
    (active&&job.external&&!job.external_stoppable?`<span class="hint">${t("job.ends_itself")}</span>`:
    active?`<button class="danger small" data-action="stop" data-confirm="${t("job.stop_confirm")}">${t("job.stop")}</button>`:
      resumable&&running()?`<span class="hint">${t(audioRunning.length?"job.resume_after_audio":"job.resume_after_job")}</span>`:
      resumable&&!fresh?`<button class="secondary small" data-action="resume" data-run-id="${escape(r?.run_id||"")}">${t("button.resume")}</button>`:"")+
    (destination!==null&&destination!==undefined&&destination!==step?`<button class="secondary small status-link" data-step="${destination}">${links[destination]} →</button>`:"")+audioNote;
  if(research)replaceKeeping(research,job.progress?.phase==="research"?renderResearchPanel(job,r,active,questionOpen,researchOpen,researchBlocked,reopenable,resumable):"");
  const live=$("research-live");
  if(live)live.innerHTML=lastLine?.text?`<section class="panel live-panel" aria-live="polite"><div class="panel-title"><h2>Live</h2><span class="hint">${lastLine.at?t("time.ago",{age:progressAge(lastLine.at)}):""}</span></div><p class="live-text">${escape(lastLine.text)}</p><button class="quiet small" data-action="drawer-toggle">${t("live.all")}</button></section>`:"";
  // The engine room holds telemetry only (D-164): what happened and what to do stand on the step's page. A text run
  // shows its own; a running chat, check, sample or companion kit its progress; a finished one hands the room to the
  // project's text run, or the room closes.
  const text=["script","research"].includes(r?.kind)||["script","research"].includes(job.progress?.phase)||Number(job.progress?.model_call_limit)>0?job
    :!active&&["research","script"].includes(project.main_job?.run?.kind)?project.main_job:null;
  const body=text?telemetryBody(text):auxiliaryBody(job);
  showDrawer(escape(title)+(lastLine?.text?` · Live: ${escape(lastLine.text)}`:info?.message&&sameLanguage(info)?` · ${escape(info.message)}`:""),body);
  const traceList=box.querySelector?.(".trace-lines");
  if(traceList)traceList.scrollTop=traceAtEnd?traceList.scrollHeight:traceScroll;
}
// The dock opens only with something to show; its head is the one toggle (D-164).
function showDrawer(summary, body) {
  const box=$("job-status");
  if(!body){box.hidden=true;box.innerHTML="";dock(false);return;}
  box.hidden=false;dock(true);
  replaceKeeping(box,drawerMarkup(summary,body));
}
// A research or script run: its brief, the live output, the budget, the timing, the text model and the report.
function telemetryBody(textJob) {
  const p=textJob.progress||{}, active=textJob.status==="running";
  let body=renderStatusSummary(p.status_summary,active)+`<div class="model-observability">${renderModelTrace(textJob)}</div>`;
  if(Number.isSafeInteger(p.model_call_limit)&&p.model_call_limit>0){
    const projection=p.budget_projection;
    const outlook=Number.isSafeInteger(projection?.minimum_remaining_calls)?` · ${tp("telemetry.minimum",{count:projection.minimum_remaining_calls})}${projection.feasible===false?` – ${tp("telemetry.short")}`:""}`:"";
    // The expectation from the project's last completed script run stands beside the minimum; it stops nothing.
    const expected=projection?.calibration&&Number.isSafeInteger(projection?.expected_remaining_calls)?` · ${tp("telemetry.expected",{count:projection.expected_remaining_calls})}${projection.expected_shortfall>0?`, ${tp("telemetry.more_than_available")}`:""}`:"";
    body+=`<p class="hint">${t("telemetry.calls",{used:Number(p.model_calls||0),limit:p.model_call_limit})}${escape(outlook)}${escape(expected)}</p>${expected&&projection.expected_label?`<p class="hint">${escape(projection.expected_label)}</p>`:""}`;
    // A run billed to a key also names its money: spent, the limit and what the rest is expected to cost (D-146).
    const cost=projection?.cost;
    if(cost)body+=`<p class="hint">${cost.limit_usd?t("telemetry.cost_limit",{spent:usd(cost.spent_usd),limit:usd(cost.limit_usd)}):t("telemetry.cost",{spent:usd(cost.spent_usd)})}${cost.expected_remaining_usd!=null?` · ${t("telemetry.rest",{cost:usd(cost.expected_remaining_usd)})}${cost.feasible===false?`, ${t("telemetry.over_limit")}`:""}`:""}</p>`;
  }
  body+=renderProgressTiming(p,active)+renderRunTextChoice(textJob);
  if(["script","research"].includes(textJob.run?.kind))body+=allowanceUse(project.allowances)+renderProductionReport(textJob);
  return body;
}
// A running job of its own: how far it got. Finished, it leaves nothing behind here.
function auxiliaryBody(job) {
  const p=job.progress;
  if(job.status!=="running")return "";
  let body="";
  if(job.run&&!p?.research_questions)body+=`<div class="stage-strip">${Object.entries(job.run.stages||{}).map(([name,v])=>`<span class="${escape(v.status)}">${v.status==="completed"?"✓ ":""}${stageNames[name]||escape(name)}</span>`).join("")}</div>`;
  if(p){
    const counts={done:Number(p.completed_segments),total:Number(p.total_segments)};
    if(job.action==="expression"&&p.total_segments!==undefined)body+=`<p>${t("aux.expression",counts)}</p><progress value="${Number(p.completed_segments)}" max="${Number(p.total_segments)}" aria-label="${t("aux.expression_label")}"></progress>`;
    else if(job.action==="publish_kit"&&p.total_segments!==undefined)body+=`<p>${t("aux.kit",counts)}</p><progress value="${Number(p.completed_segments)}" max="${Number(p.total_segments)}" aria-label="${t("aux.kit_label")}"></progress>`;
    else if(job.action==="audio_samples"&&p.total_segments!==undefined)body+=`<p>${t("aux.samples",counts)}</p><progress value="${Number(p.completed_segments)}" max="${Number(p.total_segments)}" aria-label="${t("progress_view.progress")}"></progress>`;
    else body+=audioPhase(p);
    if(job.action==="audio_samples"&&p.current_voice)body+=`<p>${t("aux.voice",{voice:p.current_voice})}</p>`;
  }
  return body;
}
// The running recordings' progress; stopping, resuming and listening stay on the recording page.
function audioTelemetry() {
  const title=id=>project.episodes?.find(e=>e.script.episode_id===id)?.script.title||id;
  return (project?.audio_jobs||[]).filter(a=>a.status==="running").map(a=>`<section class="audio-job"><strong>${escape(title(a.episode))}</strong>${audioPhase(a.progress)}</section>`).join("");
}
// Unfinished input survives a re-render of the same project; a project switch starts clean.
const FORM_IDS=["chat-message","outline-feedback","script-feedback","listening-note","style-notes","spoken-forms","host-name-a","host-name-b","plan-max-tasks","api-key","audio-key","stop-key","stop-feedback","queue-key","redesign-note"];
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
  $("content").innerHTML=settingsPage?renderSettings():overviewPage?renderOverview():[renderBrief,renderResearch,renderOutline,renderProduction,renderScript,renderAudio][step]();
  renderJob(); formRestore(saved); syncPlayButtons(); syncReaderOffset();
  if(!overviewPage&&window.matchMedia?.("(max-width: 720px)")?.matches)$("steps").querySelector?.('[aria-current="page"]')?.scrollIntoView?.({inline:"center",block:"nearest"});
}
// Where this Studio is reachable: on the computer that runs it the address for the phone, when it was started for the WLAN.
function studioPlace(b) {
  const where=b?.client==="lan"?tp("place.lan"):b?.lan?.enabled&&b.lan.urls?.length?tp("place.local_lan",{url:b.lan.urls[0]}):tp("place.local");
  return `<span class="live-dot"></span>${escape(where)}`;
}
async function refreshProjects() {
  boot=await api("/api/bootstrap");
  $("studio-place").innerHTML=studioPlace(boot);
  $("project-select").innerHTML=`<option value="">${t("project.new")}</option>`+boot.projects.map(p=>`<option value="${escape(p.id)}">${escape(p.topic)}</option>`).join("");
  $("project-select").value=project?.id||"";
  syncProjectSelect();
}
async function selectProject(id, loaded=null, requestedPage=null) {
  if(setupSending)throw new Error(tp("chat.sending_wait"));
  if(!leaveSettings()){$("project-select").value="";return;}
  const epoch=++navigationEpoch;
  const selected=id?(loaded||await api("/api/projects/"+encodeURIComponent(id))):null;
  if(epoch!==navigationEpoch)return;
  overviewPage=false;settingsPage=false;
  pendingAttachments=[];drawerOpen=false;trialChecked=false;
  for(const key of Object.keys(accessChoices))delete accessChoices[key];
  project=selected;
  $("project-select").value=id||"";
  episodeIndex=0;scriptEpisodeId=null;readingSnapshot=null;followWorkflow=requestedPage===null;briefChatOpen=false;
  step=requestedPage===null?recommendedPage():requestedPage;
  lastJobSignature=projectJobSignature(project);updatePageUrl();render();
  if(step===PAGE.brief&&(project?.chat||[]).length)scrollChatToEnd();
}
async function storeKey(fieldId="api-key",kind="openrouter") {
  const key=$(fieldId)?.value.trim();
  if(key){const value=await api("/api/key",{key,kind});boot.key_available=value.key_available;
    if(value.google_key_available!==undefined)boot.google_key_available=value.google_key_available;
    copyKeyFlags(value,boot);$(fieldId).value="";
    if(settingsData)copyKeyFlags(boot,settingsData);
    const status=$(KEY_STATUS[kind]||"key-status");
    if(status)status.textContent=tp(keyAvailable(kind)?"key.available":"key.none");
    if(keyAvailable(kind))clearKeyReminder(kind);}
  return !!key;
}
// The ZIP is built before its first byte; fetching it shows that wait and turns a refusal into a readable message.
async function downloadZip(url, fallback="") {
  notice(tp("download.zip_building"),"ok");
  let response;
  try { response=await fetch(url); }
  catch { throw new Error(tp("error.unreachable")); }
  if(!response.ok){const result=await response.json().catch(()=>({}));const error=new Error(result.error||tp("download.failed"));
    error.code=result.code;error.language=result.error?result.message_language:LANG;throw error;}
  const disposition=response.headers.get("Content-Disposition")||"";
  const encoded=/filename\*=UTF-8''([^;]+)/i.exec(disposition)?.[1];
  const name=(encoded?decodeURIComponent(encoded):/filename="([^"]+)"/i.exec(disposition)?.[1])||fallback||"podcast.zip";
  const href=URL.createObjectURL(await response.blob()), link=document.createElement("a");
  link.href=href;link.download=name;document.body.appendChild(link);link.click();link.remove();
  setTimeout(()=>URL.revokeObjectURL(href),60000);
  notice(tp("download.zip_ready"),"ok");
}
async function sendSetupMessage(message) {
  message=message.trim();
  // A new trial needs no topic of its own (D-157): the server takes the sample topic of the brief's language.
  const trial=!project&&trialChecked&&!!boot?.trial;
  if((!message&&!pendingAttachments.length&&!trial)||running()||setupSending||readingAttachments)return;
  if(/sk-or-[A-Za-z0-9_-]{12,}/.test(message))throw new Error(tp("chat.key_in_chat"));
  const initialTopic=message||pendingAttachments[0]?.name.replace(/\.(md|txt|docx)$/i,"")||"";
  message=message||(pendingAttachments.length?tp("chat.attachments_only"):tp("chat.trial_only"));
  setupSending=true;refreshAttachmentComposer();
  try{
    if(!project){
      const config={...structuredClone(boot.defaults),topic:initialTopic.slice(0,500)};
      const created=await api("/api/projects",{config,text:boot.text_defaults||defaultTextChoice(),
        execution:{text:"sequential",audio:"sequential"},...(trial?{trial:true}:{})});
      trialChecked=false;
      project=await api("/api/projects/"+created.id);await refreshProjects();updatePageUrl();
    }
    if(pendingAttachments.length){
      await api(`/api/projects/${project.id}/upload`,{files:pendingAttachments.map(({name,base64})=>({name,base64}))});
      pendingAttachments=[];
      project=await api(`/api/projects/${project.id}`);
    }
    await start("assistant",{message});
    const field=$("chat-message");
    if(field)field.value="";
  }finally{setupSending=false;refreshAttachmentComposer();}
}
async function applySetupProposal() {
  if(proposalChangesBrief()&&!confirmPaused(["config"]))return;
  await api(`/api/projects/${project.id}/apply_proposal`,{proposal_hash:project.proposal_hash,
    config_hash:project.config_hash,audio_hash:project.audio_hash,execution_hash:project.execution_hash});
  project=await api(`/api/projects/${project.id}`);await refreshProjects();render();notice(tp("summary.applied"),"ok");
}
async function start(action, extra={}) {
  if(!project) throw new Error(tp("start.no_project"));
  const parallelAudio=action==="audio"||(action==="resume"&&extra.episode);
  const queueable=action==="audio"&&!extra.rerender;
  if(parallelAudio?audioBlockReason(extra.episode,queueable):running()) throw new Error(parallelAudio?audioBlockReason(extra.episode,queueable):tp("audio.block.running"));
  const id=project.id;
  submitting=true;
  let response;
  try { response=await api(`/api/projects/${id}/start`,{action,...extra});project=await api(`/api/projects/${id}`);lastJobSignature=projectJobSignature(project); }
  finally { submitting=false; }
  if(response?.queued){notice(tp("start.queued",{position:Number(response.position)}),"ok");render();return response;}
  navigatePage(recommendedPage(),{automatic:true,push:false});
  if(action==="assistant")scrollChatToEnd();
  return response;
}
function jobSignature(job) { return job?`${job.id}:${job.status}`:""; }
function projectJobSignature(p) { return [jobSignature(p?.job),jobSignature(p?.main_job),...(p?.audio_jobs||[]).map(jobSignature)].join("|"); }
// Resuming after an approval or a stored key is one click; the resume target travels on the button.
// Adopt every open retry recommendation with its hint (as edited in its field), then resume once.
async function applyAdvice(button) {
  for(const q of adviceRetries(project.job?.progress?.research_questions)){
    const hint=$("retry-hint-"+q.id)?.value||q.advice.hint||"";
    await api(`/api/projects/${project.id}/approve`,{kind:"retry",run_id:button.dataset.runId,task_id:q.id,hint});
  }
  await resumeFrom(button);
}
// Limits that stop the open questions rise to the sized values in one approval (and the call limit where the advice
// needs room), then every retry advice is adopted and the run resumes (D-155).
async function raiseAndAdopt(button) {
  const payload={kind:"model_calls",run_id:button.dataset.runId,sources:Number(button.dataset.sources),search_rounds:Number(button.dataset.searchRounds)};
  if(button.dataset.modelCalls)payload.model_calls=Number(button.dataset.modelCalls);
  await api(`/api/projects/${project.id}/approve`,payload);
  await applyAdvice(button);
}
// The editor's side in a disputed objection; the run stopped only for it, so it resumes at once.
async function decideDispute(button) {
  const id=button.dataset.objectionId;
  await api(`/api/projects/${project.id}/approve`,{kind:"dispute",run_id:button.dataset.runId,objection_id:id,
    decision:button.dataset.decision,note:$("dispute-note-"+id)?.value||""});
  project=await api(`/api/projects/${project.id}`);lastJobView="";
  // The run resumes once, when the last dispute of the round is decided.
  if(!running()&&!disputes(project.job||{}).some(d=>!d.decision?.decision)){await resumeFrom(button);return true;}
  render();return false;
}
// The accepted access gap; when it was the last open decision, the run resumes with it at once.
async function acceptAccessGap(button) {
  const id=button.dataset.taskId, source=$("access-source-"+id)?.value||"";
  if(!source)throw new Error(tp("act.access_source_missing"));
  await api(`/api/projects/${project.id}/approve`,{kind:"access_gap",run_id:button.dataset.runId,task_id:id,
    criterion:Number($("access-criterion-"+id)?.value),source});
  project=await api(`/api/projects/${project.id}`);lastJobView="";
  const rows=project.job?.progress?.research_questions?.questions||[];
  // A question that only waits for its prerequisite is decided with it.
  if(!running()&&!rows.some(q=>q.status==="blocked"&&q.outcome!=="prerequisite_block"&&!q.accepted_gap&&!q.retry_requested&&!q.access_gap_requested)){await resumeFrom(button);return true;}
  render();return false;
}
// A new teaching design of the stopped episode with the editor's note, then the resume that starts it.
async function redesignTeaching(button) {
  const note=($("redesign-note")?.value||"").trim();
  if(!note)throw new Error(tp("act.redesign_note_missing"));
  await api(`/api/projects/${project.id}/approve`,{kind:"teaching_redesign",run_id:button.dataset.runId,episode_id:button.dataset.teachingEpisode,note});
  await resumeFrom(button);
}
// The plan approval in one click (D-155): the limits the plan needs rise first (raise_to, only those that rise), then
// the plan is approved and, with data-then-resume, the run starts. A cap asks for a shortened plan instead, which is
// shown again with its own limits, so nothing is raised for it.
async function approvePlan(button) {
  const payload=planApprovalRequest(button.dataset.runId);
  const raise=Object.fromEntries([["model_calls",button.dataset.raiseCalls],["search_rounds",button.dataset.raiseRounds],["sources",button.dataset.raiseSources]]
    .filter(([,value])=>value).map(([key,value])=>[key,Number(value)]));
  const raised=!payload.max_tasks&&Object.keys(raise).length>0;
  if(raised)await api(`/api/projects/${project.id}/approve`,{kind:"model_calls",run_id:button.dataset.runId,...raise});
  await api(`/api/projects/${project.id}/approve`,payload);
  if(button.dataset.thenResume){await resumeFrom(button);return {payload,raised,resumed:true};}
  project=await api(`/api/projects/${project.id}`);lastJobView="";render();
  return {payload,raised,resumed:false};
}
async function resumeFrom(button) {
  await start("resume",{run_id:button.dataset.runId||project.job?.run?.run_id||project.run?.run_id,...(button.dataset.episode?{episode:button.dataset.episode}:{})});
}
document.addEventListener("submit",event=>{
  event.preventDefault();attempt(async()=>{
    if(event.target.id==="chat-form")await sendSetupMessage($("chat-message").value);
  });
});
// Typing in the settings marks them unsaved; the key fields store on their own button and do not count.
document.addEventListener("input",event=>markSettingsDirty(event.target));
document.addEventListener("change",event=>attempt(async()=>{
  markSettingsDirty(event.target);
  if(event.target.id==="chat-files")await queueAttachments(event.target.files);
  if(event.target.id==="trial-option"){trialChecked=!!event.target.checked;refreshAttachmentComposer();}
  if(event.target.id==="project-select")await selectProject(event.target.value);
  if(event.target.id==="text-switch-choice"&&$("text-switch-model"))$("text-switch-model").hidden=event.target.value!=="openrouter";
  if(event.target.id==="text-switch-choice"&&$("text-switch-cost"))$("text-switch-cost").hidden=!billedProvider(event.target.value);
  if(event.target.id==="episode-select"){episodeIndex=Number(event.target.value);render();}
  if(event.target.id==="script-select"){scriptEpisodeId=event.target.value;readingSnapshot=null;render();}
  if(event.target.id==="audio-approval")$("audio-start").disabled=!event.target.checked||!!audioBlockReason();
  // An access gap is accepted only for a refused source the editor chose (accessGapForm).
  if(String(event.target.id||"").startsWith("access-source-")){
    const id=event.target.id.slice("access-source-".length);accessChoices[id]=event.target.value;
    const accept=$("access-accept-"+id);if(accept)accept.disabled=!event.target.value;
  }
  if(event.target.id==="settings-audio-provider"){
    // Another provider has other voices: its defaults stand until the user picks two of them. Google brings its own
    // defaults for the styles and the alternating roles; the other routes have neither.
    const provider=event.target.value, draft=settingsFromForm(), row=audioCatalog()[provider]||{};
    const {styles,alternate_roles,...rest}=draft.audio;
    settingsDraft={...draft,audio:{...rest,provider,voices:{...(row.defaults||draft.audio.voices)},
      ...(provider==="google_gemini_tts"?{styles:{...(row.default_styles||styles)},alternate_roles:!!row.alternate_roles}:{})}};
    render();
  }
  if(event.target.id==="settings-style-preset"&&event.target.value){
    const preset=audioCatalog().google_gemini_tts?.style_presets?.[event.target.value], draft=settingsFromForm();
    if(preset){settingsDraft={...draft,audio:{...draft.audio,styles:{host_a:preset.host_a,host_b:preset.host_b}}};render();}
  }
  if(event.target.id==="settings-alternate"){settingsDraft=settingsFromForm();render();}
  if(event.target.hasAttribute?.("data-ui-language"))await chooseLanguage(event.target.value);
}));
document.addEventListener("click",event=>{
  const zip=event.target.closest?.("a.download-all");
  if(zip){event.preventDefault();attempt(()=>downloadZip(zip.getAttribute("href"),zip.getAttribute("download")||""));return;}
  // A settled brief remembers whether its conversation is open; the click comes before the toggle.
  const archive=event.target.closest?.("#chat-archive > summary");
  if(archive)briefChatOpen=!archive.parentElement.open;
  const button=event.target.closest?.("button");
  if(!button){
    // A project card opens where its work waits, wherever it is clicked outside its controls.
    const card=event.target.closest?.("[data-project-card]");
    if(card&&!event.target.closest("a,summary,details,input,select")){
      const attention=attentionOf(overviewData.projects.find(p=>p.id===card.dataset.projectCard)||{});
      attempt(()=>selectProject(card.dataset.projectCard,null,attention?attention.page:null));
    }
    return;
  }
  attempt(async()=>{
    // Starting over or stopping costs finished work or a running call; such buttons say so first.
    if(button.dataset.confirm&&typeof window.confirm==="function"&&!window.confirm(button.dataset.confirm))return;
    if(button.dataset.scroll){
      // A folded target (a dossier group, a long list) opens on the way.
      const target=$(button.dataset.scroll);
      if(target?.tagName==="DETAILS")target.open=true;
      target?.scrollIntoView?.({block:"start"});return;
    }
    if(button.dataset.readerEpisode!==undefined){
      const done=button.dataset.markRead&&readerEntries().find(row=>row.script.episode_id===button.dataset.markRead);
      if(done&&!done.preview&&done.hash&&!isRead(done))await saveReaderState({episode:done.script.episode_id,read:done.hash});
      scriptEpisodeId=button.dataset.readerEpisode;readingSnapshot=null;render();window.scrollTo(0,0);return;
    }
    if(button.dataset.playEpisode){await playEpisode(button.dataset.playEpisode);return;}
    if(button.dataset.player){if(button.dataset.player==="close")closePlayer();else await stepPlaylist(button.dataset.player==="next"?1:-1);return;}
    if(button.dataset.removePending!==undefined){if(!setupSending){pendingAttachments.splice(Number(button.dataset.removePending),1);refreshAttachmentComposer();}return;}
    if(button.dataset.removeAttachment){await removeAttachment(button.dataset.removeAttachment);return;}
    if(button.id==="new-project"||button.hasAttribute("data-new-project")){await selectProject("");return;}
    if(button.id==="project-overview"||button.dataset.action==="overview"){await showOverview();return;}
    if(button.dataset.openProject){await selectProject(button.dataset.openProject,null,button.dataset.openStep!==undefined?Number(button.dataset.openStep):null);return;}
    if(button.dataset.deleteProject){
      const p=overviewData.projects.find(p=>p.id===button.dataset.deleteProject);
      if(p&&window.confirm(tp("act.delete_confirm",{topic:quoted(p.topic)}))){
        await api(`/api/projects/${p.id}/delete`,{confirm_id:p.id,config_hash:p.config_hash});
        await refreshProjects();overviewData=await loadOverview();refreshOverview();notice(tp("act.deleted"),"ok");
      }return;
    }
    if(button.dataset.restoreProject){await api("/api/restore",{trash_id:button.dataset.restoreProject});await refreshProjects();overviewData=await loadOverview();refreshOverview();return;}
    if(button.dataset.setupReply){await sendSetupMessage(button.dataset.setupReply);return;}
    if(button.dataset.step!==undefined){navigatePage(Number(button.dataset.step));$("main").focus();window.scrollTo(0,0);return;}
    if(button.dataset.previewVoice){
      const voice=button.dataset.previewVoice,language=button.dataset.language,provider=button.dataset.previewProvider;
      if(isGemini(provider)&&!savedSample(voice,language)){
        await storeKey();await start("audio_sample",{voice,language,approve_sample:true});
      }else await playSample(voice,language,provider);
      return;
    }
    if(button.dataset.playVoice)await playSample(button.dataset.playVoice,button.dataset.language);
    const action=button.dataset.action;if(!action)return;
    if(action==="drawer-toggle"){drawerOpen=!drawerOpen;lastJobView="";renderJob();return;}
    if(action==="mark-read"){
      const e=readerEntries().find(row=>row.script.episode_id===button.dataset.episode);
      if(e)await saveReaderState({episode:e.script.episode_id,read:isRead(e)?"":e.hash});
      render();return;
    }
    if(action==="refresh-script"){readingSnapshot=null;$("content").innerHTML=renderScript();return;}
    if(action==="quit"){
      // Quitting stops every project's jobs, not only the open one's.
      let busy=running()?[project?.config?.topic||tp("act.quit.open_project")]:[];
      if(boot.capabilities?.project_overview){const all=await loadOverview().catch(()=>null);if(all)busy=all.projects.filter(runningOf).map(p=>p.topic);}
      if(busy.length&&typeof window.confirm==="function"&&!window.confirm(tp("act.quit.confirm",{jobs:busy.join(", ")})))return;
      await api("/api/quit",{});project=null;$("job-status").hidden=true;$("job-bar").hidden=true;$("content").innerHTML=`<section class="empty"><h1>${t("act.quit.title")}</h1><p>${t("act.quit.text")}</p></section>`;return;
    }
    if(action==="apply-proposal"){await applySetupProposal();return;}
    if(action==="open-settings"){await showSettings();return;}
    if(action==="resume-stopped-audio"){await resumeStoppedAudio();return;}
    if(action==="save-settings"){await saveSettings();return;}
    if(action==="pair-sample"){await pairSample(button);return;}
    if(action==="store-key"){
      const kind=button.dataset.keyKind||"openrouter";
      if(!await storeKey(button.dataset.keyField||"api-key",kind))throw new Error(tp("act.key_missing",{key:KEY_NAMES[kind]}));
      if(button.dataset.thenResume){await resumeFrom(button);return;}
      // The settings page keeps what was typed but not saved yet when it shows the new key state.
      if(settingsPage&&settingsDraft)settingsDraft=settingsFromForm();
      lastJobView="";stopHtml="";render();notice(tp("act.key_stored"),"ok");return;
    }
    if(action==="resend-chat"){
      const last=[...(project.chat||[])].reverse().find(m=>m.role==="user");
      if(!last)throw new Error(tp("act.no_unanswered"));
      await sendSetupMessage(last.message);return;
    }
    if(action==="approve-chat"){
      await api(`/api/projects/${project.id}/approve`,{kind:"chat_calls",model_calls:Number(button.dataset.modelCalls)});
      project=await api(`/api/projects/${project.id}`);render();notice(tp("act.chat_limit",{resend:quoted(tp("button.resend"))}),"ok");return;
    }
    if(action==="forget-key"){
      const kind=button.dataset.keyKind||"openrouter", field=KEY_FIELDS[kind]||"api-key";
      await api("/api/key",{key:"",kind});await refreshProjects();if($(field))$(field).value="";
      if(settingsData){copyKeyFlags(boot,settingsData);settingsData.key_reminder=boot.key_reminder;}
      if(settingsPage&&settingsDraft){settingsDraft=settingsFromForm();render();}
      renderKeyNote();$(KEY_STATUS[kind]||"key-status").textContent=tp(keyAvailable(kind)?"act.key_env":"act.key_removed");return;
    }
    if(action==="stop"){await api(`/api/projects/${project.id}/stop`,{job_id:button.dataset.jobId});project=await api(`/api/projects/${project.id}`);render();return;}
    if(action==="apply-advice"){await applyAdvice(button);notice(tp("act.advice_applied"),"ok");return;}
    if(action==="raise-adopt"){await raiseAndAdopt(button);notice(tp("act.limits_advice_applied"),"ok");return;}
    if(action==="upload-work"){await uploadWork(button);return;}
    if(action==="retry-task"){
      const hint=$("retry-hint-"+button.dataset.taskId)?.value||"";
      await api(`/api/projects/${project.id}/approve`,{kind:"retry",run_id:button.dataset.runId,task_id:button.dataset.taskId,hint});
      project=await api(`/api/projects/${project.id}`);lastJobView="";render();notice(tp("act.retry_requested",{resume:quoted(tp("button.resume"))}),"ok");return;
    }
    if(action==="redesign-teaching"){await redesignTeaching(button);notice(tp("act.redesign_saved"),"ok");return;}
    if(action==="production-report"){
      productionReports[button.dataset.runId]=await api(`/api/projects/${project.id}/report?run_id=${encodeURIComponent(button.dataset.runId)}`);
      lastJobView="";renderJob();return;
    }
    if(action==="production-report-close"){delete productionReports[button.dataset.runId];lastJobView="";renderJob();return;}
    if(action==="restart-when-idle"||action==="restart-cancel"){
      const state=await api("/api/server/restart",action==="restart-cancel"?{cancel:true}:{});
      if(project)project.server=state.server;if(overviewData)overviewData.server=state.server;renderServerNote();
      notice(tp(state.server.restart_requested?"act.restart_requested":"act.restart_cancelled"),"ok");return;
    }
    if(action==="toggle-jev-probe"){
      const enabled=button.dataset.enabled==="1";
      await api(`/api/projects/${project.id}/jev_probe`,{enabled});
      project=await api(`/api/projects/${project.id}`);render();
      notice(tp(enabled?(boot.key_available?"act.jev_on":"act.jev_on_key"):"act.jev_off"),"ok");return;
    }
    if(action==="unqueue"){
      await api(`/api/projects/${project.id}/audio_queue`,{episode:button.dataset.episode});
      project=await api(`/api/projects/${project.id}`);render();notice(tp("act.unqueued"),"ok");return;
    }
    if(action==="audio-all"){
      if(!$("audio-approve-all")?.checked)throw new Error(tp("act.confirm_read"));
      const rows=pendingRecordings();let started=0,queued=0;
      for(const e of rows){
        const response=await api(`/api/projects/${project.id}/start`,{action:"audio",...audioRequest(e.script.episode_id),approve_audio:true});
        if(response?.queued)queued++;else started++;
      }
      project=await api(`/api/projects/${project.id}`);lastJobSignature=projectJobSignature(project);render();
      notice(tp("act.audio_all",{count:rows.length,started,queued}),"ok");return;
    }
    if(action==="text-switch"){
      const choice=button.dataset.choice||$("text-switch-choice")?.value||"claude_first";
      const model=choice==="openrouter"?$("text-switch-model")?.value||"":"";
      const cost=billedProvider(choice)?Number($("text-switch-cost")?.value||0):0;
      const order=choice==="openrouter"?`OpenRouter · ${boot.text_catalog?.openrouter_models?.[model]||model}`:SWITCH_CHOICES[choice]||choice;
      await api(`/api/projects/${project.id}/approve`,{kind:"text_switch",run_id:button.dataset.runId,choice,...(model?{model}:{}),...(cost>0?{cost_usd:cost}:{})});
      if(button.dataset.thenResume&&!running()){await resumeFrom(button);notice(`${tp("act.switched",{order})} ${tp("act.carries_on")}`,"ok");return;}
      project=await api(`/api/projects/${project.id}`);lastJobView="";render();
      notice(running()?tp("act.switched_next",{order}):tp("act.switched_resume",{order,resume:quoted(tp("button.resume"))}),"ok");return;
    }
    if(action==="fresh-attempts"){
      await api(`/api/projects/${project.id}/approve`,{kind:"fresh_attempts",run_id:button.dataset.runId});
      await resumeFrom(button);notice(tp("act.fresh_attempts"),"ok");return;
    }
    if(action==="finish-residual"){
      await api(`/api/projects/${project.id}/approve`,{kind:"residual",run_id:button.dataset.runId,note:$("residual-note")?.value||""});
      project=await api(`/api/projects/${project.id}`);lastJobView="";render();
      notice(running()?tp("act.residual_running"):tp("act.residual_resume",{resume:quoted(tp("button.resume"))}),"ok");return;
    }
    if(action==="decide-dispute"){
      const resumed=await decideDispute(button), side=tp(button.dataset.decision==="reviewer"?"act.dispute.reviewer":"act.dispute.upheld");
      notice(`${side}. ${resumed?tp("act.carries_on"):tp("act.dispute.resume",{resume:quoted(tp("button.resume"))})}`,"ok");return;
    }
    if(action==="accept-access-gap"){
      const criterion=Number($("access-criterion-"+button.dataset.taskId)?.value);
      const resumed=await acceptAccessGap(button);
      notice(resumed?tp("act.access_gap_resumed",{criterion}):tp("act.access_gap_resume",{criterion,resume:quoted(tp("button.resume"))}),"ok");return;
    }
    if(action==="accept-gap"){
      const reason=$("gap-reason-"+button.dataset.taskId)?.value||"";
      await api(`/api/projects/${project.id}/approve`,{kind:"gap",run_id:button.dataset.runId,task_id:button.dataset.taskId,reason});
      project=await api(`/api/projects/${project.id}`);lastJobView="";render();notice(tp("act.gap_accepted",{resume:quoted(tp("button.resume"))}),"ok");return;
    }
    if(action==="approve-plan"){
      const {payload,raised,resumed}=await approvePlan(button);
      if(resumed){notice(payload.max_tasks?tp("act.plan_cap_resumed",{count:payload.max_tasks}):raised?tp("act.plan_raised_resumed"):tp("act.plan_approved_resumed"),"ok");return;}
      const resume=quoted(tp("button.resume"));
      notice(payload.max_tasks?tp("act.plan_cap_resume",{count:payload.max_tasks,resume}):tp("act.plan_approved_resume",{resume}),"ok");return;
    }
    if(action==="approve-cost"){
      const cost=Number($("stop-cost")?.value||0);
      if(!(cost>0))throw new Error(tp("act.cost_missing"));
      await api(`/api/projects/${project.id}/approve`,{kind:"model_calls",run_id:button.dataset.runId,cost_usd:cost});
      if(button.dataset.thenResume&&!running()){await resumeFrom(button);notice(`${tp("act.cost_approved",{cost:usd(cost)})} ${tp("act.carries_on")}`,"ok");return;}
      project=await api(`/api/projects/${project.id}`);lastJobView="";render();notice(tp("act.cost_approved",{cost:usd(cost)}),"ok");return;
    }
    if(action==="approve-calls"||action==="approve-search"||action==="approve-sources"||action==="approve-limits"){
      // Each button carries the limits it raises; "approve-limits" raises sources and search rounds together.
      const payload={kind:"model_calls",run_id:button.dataset.runId};
      if(button.dataset.modelCalls)payload.model_calls=Number(button.dataset.modelCalls);
      if(button.dataset.sources)payload.sources=Number(button.dataset.sources);
      if(button.dataset.searchRounds)payload.search_rounds=Number(button.dataset.searchRounds);
      await api(`/api/projects/${project.id}/approve`,payload);
      if(button.dataset.thenResume&&!running()){await resumeFrom(button);notice(tp("act.limit_resumed"),"ok");return;}
      project=await api(`/api/projects/${project.id}`);lastJobView="";render();notice(tp("act.limit_approved",{resume:quoted(tp("button.resume"))}),"ok");return;
    }
    if(action==="save-speech"){await saveSpeechSettings();return;}
    if(action==="save-notes"){
      if($("style-notes").value.trim()!==(project.style_notes||"").trim()&&!confirmPaused(["notes"]))return;
      await api(`/api/projects/${project.id}/save`,{config:project.config,config_hash:project.config_hash,
        text:project.text,style_notes:$("style-notes").value,style_notes_hash:project.style_notes_hash});
      project=await api(`/api/projects/${project.id}`);render();
      notice(tp("act.notes_saved"),"ok");return;
    }
    if(action==="listening-review"){
      const e=project.episodes[episodeIndex];
      await api(`/api/projects/${project.id}/listening_review`,{episode:e.script.episode_id,
        reviewed:$("listening-done").checked,note:$("listening-note").value});
      project=await api(`/api/projects/${project.id}`);render();notice(tp("act.listening_saved"),"ok");return;
    }
    if(action==="spoken-override"){await saveSpokenOverride(button.dataset.episode,button.dataset.segment);return;}
    if(action==="audio"&&button.dataset.rerender){await rerenderEpisode(button.dataset.episode);return;}
    const extra={};
    if(action==="audio_samples"){
      extra.language=setupSelection().config.language;extra.approve_samples=true;
      await storeKey();
    }
    if(action==="research"&&$("seed-corpus")?.checked)extra.seed_corpus=true;
    if(action==="replan")extra.message=$(button.dataset.feedback||"outline-feedback")?.value||"";
    if(action==="script")extra.plan_hash=project.outline.hash;
    if(action==="revise"){extra.message=$("script-feedback").value;extra.episode=project.episodes[episodeIndex].script.episode_id;}
    if(action==="resume"){extra.run_id=button.dataset.runId||project.job?.run?.run_id||project.run?.run_id;if(button.dataset.episode)extra.episode=button.dataset.episode;}
    if(action==="audio")Object.assign(extra,audioRequest(button.dataset.episode));
    if(action==="expression"||action==="publish_kit")extra.episodes=button.dataset.episode?[button.dataset.episode]:[];
    if(action==="publish_kit"&&button.dataset.fresh)extra.fresh=true;
    if(action==="publish_kit"&&button.dataset.podcast)extra.podcast=true;
    if(action==="copy-text"){await navigator.clipboard?.writeText($(button.dataset.source)?.value||"");notice(tp("act.copied"),"ok");return;}
    await start(action,extra);
    // A sent request leaves no stale draft behind; unsent drafts survive re-renders elsewhere.
    if(action==="replan"&&$(button.dataset.feedback||"outline-feedback"))$(button.dataset.feedback||"outline-feedback").value="";
    if(action==="revise"&&$("script-feedback"))$("script-feedback").value="";
  });
});
// Polls run one at a time, the overview's every ten seconds, and a hidden tab's once a minute, enough for its title;
// showing the tab again polls at once (2026-10-02: hidden tabs polled every 2.5 s and slow answers overlapped, each
// poll holding the server's lock). Each answer names the server instance: a new one means a restart, so the session
// and the key state are read again.
const POLL_MS={overview:10000,hidden:60000};
let pollRunning=false, lastPollAt={overview:0,hidden:0};
async function poll() {
  if(pollRunning)return;
  const now=Date.now(), hidden=!!document.hidden;
  if(hidden&&now-lastPollAt.hidden<POLL_MS.hidden)return;
  if(!hidden&&overviewPage&&now-lastPollAt.overview<POLL_MS.overview)return;
  if(hidden)lastPollAt.hidden=now;
  if(overviewPage)lastPollAt.overview=now;
  pollRunning=true;
  try{await pollOnce();}finally{pollRunning=false;}
}
async function checkInstance(server) {
  const known=boot?.server?.instance;
  if(!server?.instance||!known||server.instance===known)return;
  const fresh=await fetch("/api/bootstrap").then(r=>r.ok?r.json():null).catch(()=>null);
  if(!fresh?.token)return;
  const lost=lostKeys(fresh);
  boot={...boot,...fresh};
  if(lost.length){notice(tp("act.restart_keys_lost",{count:lost.length,keys:keyList(lost)}));lastJobView="";if(!overviewPage&&project)render();}
}
// The page polls the project's light status and loads the whole project only when a job or the content changed (D-159;
// 2026-10-07: every poll carried all scripts, the dossier and every recording job, 2.5 to 3.7 MB each 2.5 s).
async function pollProject(id) {
  if(!boot.capabilities?.light_status)return api(`/api/projects/${id}`);
  const light=await api(`/api/projects/${id}/status`);
  if(project?.id!==id||light.content_version!==project.content_version||projectJobSignature(light)!==lastJobSignature)
    return api(`/api/projects/${id}`);
  return {...light,research:project.research,outline:project.outline,episodes:project.episodes,script_previews:project.script_previews};
}
async function pollOnce() {
  try {
    if(overviewPage){const next=await loadOverview();markSynced();await checkInstance(next.server);if(overviewPage){overviewData=next;refreshOverview();renderNavigation();}return;}
    if(!project||submitting||setupSending||readingAttachments)return;
    const id=project.id,next=await pollProject(id);
    if(project?.id!==id)return;
    markSynced();
    await checkInstance(next.server);
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
    project.main_job=next.main_job;project.chat_budget=next.chat_budget;project.reader_state=next.reader_state;project.download_zip=next.download_zip;renderNavigation();renderJob();
    if(samplesChanged)refreshVoiceLibrary();
    if(changed||destination!==step){lastJobSignature=projectJobSignature(next);project=next;
      const finishedResult=next.job?.status==="completed"&&!!(next.job.sample||next.job.checks);
      if(followWorkflow){step=destination;updatePageUrl();}
      // A playing episode keeps its player: the audio page refreshes its parts instead of rebuilding.
      const playing=[...document.querySelectorAll("audio")].some(audio=>!audio.paused);
      if(readerOpen&&step===PAGE.scripts){renderNavigation();renderJob();refreshScriptReader();}
      else if(playing&&step===PAGE.audio){renderNavigation();lastJobView="";renderJob();refreshAudioPanel();refreshRecordings();}
      else render();
      // A check answers beside its button, a voice sample in the voice list; the engine room stays closed.
      if(finishedResult)notice(next.job.checks?tp("act.check_done",{checks:quoted(tp("checks.title"))}):tp("act.sample_done",{voices:quoted(tp("voices.listen"))}),"ok");
    }else if(scriptsChanged&&step===PAGE.scripts)refreshScriptReader();
    else if((chatChanged||attachmentsChanged)&&step===PAGE.brief){refreshAttachmentComposer();if(chatChanged)scrollChatToEnd();}
  }catch(error){
    // Only a missing answer is a lost connection; a refusal of the running server says what it refused.
    if(error.network){connectionLost=true;lastJobView="";renderJob();notice(tp("act.connection_lost"),"error");}
    else notice(tp("act.studio_reports",{message:errorText(error)}),"error");
  }
}
function markSynced() {
  lastSyncAt=Date.now();
  if(connectionLost){connectionLost=false;lastJobView="";notice("");}
}
// The page's static text (index.html) in the interface language (D-152): data-i18n names an element's text,
// data-i18n-label its aria-label, data-i18n-content a meta's content. The German stands in the file as the fallback.
function localizePage() {
  if(document.documentElement)document.documentElement.lang=LANG;
  for(const node of document.querySelectorAll("[data-i18n]"))node.textContent=tp(node.dataset.i18n);
  for(const node of document.querySelectorAll("[data-i18n-label]"))node.setAttribute("aria-label",tp(node.dataset.i18nLabel));
  for(const node of document.querySelectorAll("[data-i18n-content]"))node.setAttribute("content",tp(node.dataset.i18nContent));
  for(const node of document.querySelectorAll("select[data-ui-language]"))node.value=LOCALE.setting;
}
localizePage();
wirePlayer();
window.addEventListener("resize",syncReaderOffset);
window.addEventListener("beforeunload",event=>{if(settingsPage&&settingsDirty){event.preventDefault();event.returnValue="";}});
attempt(async()=>{
  const startupEpoch=navigationEpoch;
  await refreshProjects();
  if(startupEpoch!==navigationEpoch)return;
  const params=window.location?new URLSearchParams(window.location.search):null;
  const requested=params?.get("project"), requestedStep=pageKeys.indexOf(params?.get("step"));
  if(requested&&boot.projects.some(p=>p.id===requested))await selectProject(requested,null,requestedStep<0?null:requestedStep);
  else if(params?.get("view")==="settings")await showSettings(false);
  else if(params?.has("new"))await selectProject("");
  else await showOverview();
});
setInterval(poll,2500);
document.addEventListener("visibilitychange",()=>{if(!document.hidden){lastPollAt.overview=0;poll();}});
for(const event of ["play","pause","ended"])$("sample-player").addEventListener(event,syncPlayButtons);
window.addEventListener("popstate",()=>attempt(async()=>{
  const params=new URLSearchParams(window.location.search), id=params.get("project");
  const index=pageKeys.indexOf(params.get("step"));
  if(id&&boot.projects.some(p=>p.id===id)) {
    if(project?.id===id){const target=index<0?recommendedPage():index;if(target===step&&!overviewPage)return;navigatePage(target,{push:false});}
    else await selectProject(id,null,index<0?null:index);
  } else if(params.get("view")==="settings")await showSettings(false);
  else if(params.has("new"))await selectProject("");
  else await showOverview();
}));

// A small optional agent surface shares the visible navigation. It cannot approve generation.
if(document.modelContext?.registerTool){
  const lifecycle=new AbortController();
  const register=(tool)=>Promise.resolve(document.modelContext.registerTool(tool,{signal:lifecycle.signal})).catch(()=>{});
  register({name:"read_podcast_workspace",description:"Read the selected project's topic, current step and job status.",inputSchema:{type:"object",properties:{},additionalProperties:false},annotations:{readOnlyHint:true,untrustedContentHint:true},execute:()=>({project:project?.id||null,topic:project?.config.topic||null,step:steps[step],job:project?.job?.status||null})});
  register({name:"navigate_podcast_step",description:"Show a workflow step in the current project. Does not start or approve generation.",inputSchema:{type:"object",properties:{step:{type:"integer",minimum:1,maximum:6}},required:["step"],additionalProperties:false},annotations:{readOnlyHint:false},execute:input=>{if(!Number.isInteger(input?.step)||input.step<1||input.step>6)throw new Error(tp("act.webmcp_step"));navigatePage(input.step-1);return{step:steps[step]};}});
  window.addEventListener("pagehide",()=>lifecycle.abort(),{once:true});
}
