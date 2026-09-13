"use strict";
const $ = id => document.getElementById(id);
const escape = value => String(value ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const steps = ["Auftrag & Stimmen", "Recherche", "Inhaltsverzeichnis", "Ausarbeitung", "Skripte lesen", "Audio & Export"];
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
let voiceDrafts = {};
let lastJobView = "";
let playingSample = null;
let followWorkflow = true;
let scriptEpisodeId=null, readingSnapshot=null;
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
const running = () => submitting || project?.job?.status === "running";
const disabled = () => running() ? "disabled" : "";
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
function options(values,current) { return values.map(v=>`<option value="${escape(v)}" ${v===current?"selected":""}>${escape(v.replaceAll("_"," "))}</option>`).join(""); }
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
  if((p.episodes||[]).some(e=>e.audio_current&&e.audio?.length))return PAGE.audio;
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
  const url=project?`/?project=${encodeURIComponent(project.id)}&step=${pageKeys[step]}`:"/";
  const history=window.history;
  if(push&&history?.pushState)history.pushState(null,"",url);
  else history?.replaceState(null,"",url);
}
function navigatePage(target,{automatic=false,push=true}={}) {
  if(!Number.isInteger(target)||target<0||target>=steps.length)return;
  step=target;followWorkflow=automatic;updatePageUrl(push);render();
}
function renderNavigation() {
  const states=navigationStates();
  $("steps").innerHTML = steps.map((name,i)=>`<button class="step ${states[i][1]}" data-step="${i}" ${i===step?'aria-current="page"':""}><span class="step-number" aria-hidden="true">${states[i][1]==="done"?"✓":i+1}</span><span class="step-label">${name}<small>${states[i][0]}</small></span></button>`).join("");
  $("project-title").textContent = project?.config.topic || "Neues Podcast-Projekt";
}
function voice(role, title, description, audio) {
  const label=sampleButtonLabel(audio.provider,audio.voices[role],(project?.config||boot.defaults).language);
  return `<div class="voice"><div class="voice-label"><div><strong>${title}</strong><span>${description}</span></div></div><div class="actions"><label class="sr-only" for="${role}">Stimme für ${title}</label><select id="${role}">${options(audioCatalog()[audio.provider].voices,audio.voices[role])}</select><button type="button" class="secondary small" data-sample="${role}">${label}</button></div></div>`;
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
  if(step!==0||!$("voice-library-panel"))return;
  const remote=$("tts-provider").value==="openrouter_gemini_tts", language=$("language").value;
  $("voice-library-panel").hidden=!remote;
  if(remote)$("voice-library-panel").innerHTML=renderVoiceLibrary(language);
  for(const button of document.querySelectorAll("[data-sample]")){
    button.textContent=sampleButtonLabel($("tts-provider").value,$(button.dataset.sample).value,language);
  }
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
let textDrafts={};
function defaultTextChoice(provider="codex_cli") {
  return provider==="codex_cli"?{provider,model:"gpt-6-astra",reasoning_effort:"xhigh",max_output_tokens:32768}:
    {provider,model:"",reasoning_effort:null,max_output_tokens:32768};
}
function readTextChoice(validate=true) {
  // Old servers reject new fields. Keep their existing selection until the server is updated.
  if(!boot.capabilities?.text_reasoning_selection)return project?.text||{provider:"codex_cli",model:null,max_output_tokens:32768};
  const model=$("model").value.trim();
  if(validate&&!model)throw new Error("Bitte ein Textmodell auswählen oder eine Modell-ID eingeben.");
  return {provider:$("provider").value,model:model||null,
    reasoning_effort:$("reasoning-effort").value||null,max_output_tokens:Number($("max_output_tokens").value)};
}
function renderTextModelFields(t) {
  const codex=t.provider==="codex_cli", model=t.model||(codex?"gpt-6-astra":"");
  const presets=boot.text_catalog?.codex_models||{"gpt-6-astra":"GPT-6 Astra"};
  const custom=codex&&!Object.hasOwn(presets,model);
  const effort=t.reasoning_effort||(codex?"xhigh":"");
  const labels={low:"low · Weniger Denkaufwand",medium:"medium · Mittlerer Denkaufwand",high:"high · Hoher Denkaufwand",xhigh:"xhigh · Sehr hoher Denkaufwand"};
  return `${codex?`<div class="field"><label for="model-preset">Textmodell</label><select id="model-preset">${Object.entries(presets).map(([id,label])=>`<option value="${escape(id)}" ${id===model?"selected":""}>${escape(label)}</option>`).join("")}<option value="custom" ${custom?"selected":""}>Eigene Modell-ID</option></select></div>`:""}
    <div id="custom-model" ${codex&&!custom?"hidden":""}>${textInput("model",codex?"Eigene Codex-Modell-ID":"OpenRouter-Textmodell · anbieter/modell",model)}</div>
    <div class="field"><label for="reasoning-effort">Denkaufwand · Reasoning</label><select id="reasoning-effort">${!codex?'<option value="">Standard des gewählten Modells</option>':""}${Object.entries(labels).map(([id,label])=>`<option value="${id}" ${id===effort?"selected":""}>${label}</option>`).join("")}</select></div>
    <p class="hint">Mehr Denkaufwand gibt dem Modell mehr Zeit zum Prüfen seiner Antwort und kann länger dauern. ${codex?"Modell und Stufe werden ausdrücklich an Codex übergeben.":"Die gewählte Stufe muss vom OpenRouter-Modell unterstützt werden; sie wird im API-Aufruf angefordert."}</p>`;
}
function changeTextProvider() {
  const provider=$("provider"), previous=provider.dataset.previous||project?.text?.provider||"codex_cli";
  textDrafts[previous]={...readTextChoice(false),provider:previous};
  const selected=textDrafts[provider.value]||defaultTextChoice(provider.value);
  $("text-model-settings").innerHTML=renderTextModelFields(selected);
  $("max_output_tokens").value=selected.max_output_tokens;
  provider.dataset.previous=provider.value;
}
function renderRunTextChoice(job) {
  if(!["script","research"].includes(job?.run?.kind))return "";
  const t=job.text_generation;
  return `<p class="hint">Für diesen Auftrag gespeichert: ${t?.model?escape(t.model):"Modell nicht festgelegt"} · Reasoning: ${t?.reasoning_effort?escape(t.reasoning_effort):"nicht festgelegt"}.</p>`;
}
function renderBrief() {
  const c = project?.config || boot.defaults, t = project?.text || boot.text_defaults || defaultTextChoice();
  const a=currentAudio(), remote=a.provider==="openrouter_gemini_tts";
  const chat = project?.chat || [], proposal = [...chat].reverse().find(m=>m.role==="assistant");
  const nextPage=recommendedPage()===PAGE.brief?PAGE.research:recommendedPage();
  return heading(1,"Ein gutes Gespräch beginnt mit einer Frage.","Lege fest, was du wirklich verstehen möchtest. Dein redaktioneller Partner hilft dir, daraus einen tragfähigen Podcast-Auftrag zu machen.") +
  `<div class="two-col"><div><form id="brief-form"><fieldset ${disabled()}><section class="panel"><div class="panel-title"><h2>Dein Thema</h2><span class="tag">Von Grund auf, mit Tiefe</span></div>
  ${textInput("topic","Worum soll es gehen?",project?c.topic:"")}${area("central_question","Welche Frage soll der Podcast beantworten?",project?c.central_question:"")}
  <div class="row"><div class="field"><label for="language">Sprache</label><select id="language"><option value="de-DE" ${c.language==="de-DE"?"selected":""}>Deutsch</option><option value="en-US" ${c.language==="en-US"?"selected":""}>English</option></select></div>${textInput("target_total_minutes","Gewünschte Gesamtlänge, optional",c.target_total_minutes||"","number")}</div>
  ${area("prior_knowledge","Was weißt du schon?",c.prior_knowledge,2)}
  <details><summary>Tiefe, Schwerpunkte und Quellen</summary>${area("depth_request","Wie soll erklärt werden?",c.depth_request,6)}${area("focus_questions","Schwerpunkte · eine Frage pro Zeile",c.focus_questions.join("\n"))}${area("excluded_topics","Was soll außen vor bleiben?",c.excluded_topics.join("\n"),2)}${area("seed_urls","Eigene Quellenlinks · ein Link pro Zeile",c.seed_urls.join("\n"),2)}<p class="hint">Längere Themen werden auf mehrere Folgen verteilt. Eine Audiodatei dauert höchstens 30 Minuten.</p></details>
  </section><section class="panel"><h2>Wer spricht deinen Podcast?</h2><div class="field"><label for="tts-provider">Audioanbieter</label><select id="tts-provider" data-previous="${a.provider}">${Object.entries(audioCatalog()).map(([id,entry])=>`<option value="${id}" ${id===a.provider?"selected":""}>${escape(entry.label)}</option>`).join("")}</select></div><p class="hint">Rollen geben dem Gespräch Richtung. Längere Erklärungen dürfen zusammenhängend bleiben.</p>
  ${voice("host_a","Der Experte","Ruhig, präzise. Erklärt Zusammenhänge und Mechanismen.",a)}${voice("host_b","Die neugierige Gesprächspartnerin","Hinterfragt, denkt weiter und fragt nach der Bedeutung.",a)}
  <p class="hint" id="speech-hint">${speechHint(a.provider)}</p><p class="hint">Hörproben verwenden die oben gewählte Sprache. Die Audioauswahl lässt sich auch nach dem Schreiben ändern.</p></section>
  <section class="panel"><h2>Wer schreibt mit?</h2>${!boot.capabilities?.text_reasoning_selection?'<p class="note">Die neue Modellauswahl benötigt einen Studio-Neustart. Lass den laufenden Auftrag fertigarbeiten, beende dann das Studio und öffne es erneut.</p>':""}<fieldset ${!boot.capabilities?.text_reasoning_selection?"disabled":""}><div class="field"><label for="provider">Anbieter für Redaktion, Inhaltsverzeichnis und Skript</label><select id="provider" data-previous="${escape(t.provider)}"><option value="codex_cli" ${t.provider==="codex_cli"?"selected":""}>Codex · bestehendes ChatGPT-Abo</option><option value="openrouter" ${t.provider==="openrouter"?"selected":""}>OpenRouter · API-Key</option></select></div>
  <div id="text-model-settings">${renderTextModelFields(t)}</div>
  <div id="router-settings" ${t.provider!=="openrouter"?"hidden":""}>${textInput("max_output_tokens","Maximale Antwortlänge pro Textmodellaufruf (Tokens)",t.max_output_tokens,"number")}<p class="hint">Ein Skript benötigt mehrere Schreib- und Prüfdurchgänge.</p></div>
  </fieldset>
  <div id="key-settings" ${t.provider!=="openrouter"&&!remote?"hidden":""}>${textInput("api-key","OpenRouter-Key · für Gemini-Audio und optionale Textaufrufe","","password")}<p class="hint" id="key-status">${boot.key_available?"Ein Key ist für diese Sitzung verfügbar.":"Der Key bleibt nur für die Sitzung im Speicher."}</p><p class="hint">Gemini-Audio wird über dein OpenRouter-Guthaben abgerechnet. Codex kann unabhängig davon das Skript schreiben.</p><button type="button" class="secondary small" data-action="forget-key">Sitzungs-Key entfernen</button></div>
  <p class="hint">Diese Auswahl gilt für neue Aufträge: Redaktion, Inhaltsverzeichnis, Lehrkonzept, Skript, Dialog-Polishing und Qualitätsprüfungen. Fortsetzen verwendet die im Auftrag gespeicherte Auswahl. Die Web-Recherche nutzt Codex: bei Codex mit derselben Auswahl, bei OpenRouter-Text mit den bisherigen Codex-Rechercheeinstellungen. Audioanbieter und Stimmen sind unabhängig davon.</p>
  <div class="actions"><button type="submit">${project?"Änderungen speichern":"Projekt anlegen"}</button>${project?'<button type="button" class="secondary" data-action="check">Verbindungen prüfen</button>':""}</div>
  ${project?'<p class="hint">Änderungen am Thema oder an Quellen benötigen eine neue Recherche. Ein geändertes Skript benötigt eine neue Freigabe.</p>':""}</section></fieldset></form>
  <section class="panel" id="voice-library-panel" ${remote?"":"hidden"}>${renderVoiceLibrary(c.language)}</section>
  ${project?`<button data-step="${nextPage}" class="secondary">Weiter: ${steps[nextPage]} →</button>`:""}</div>
  <aside class="panel tinted"><div class="panel-title"><h2>Dein redaktioneller Partner</h2><span class="tag">KI</span></div><p>Was soll am Ende wirklich klar sein? Beschreibe deine Wünsche in eigenen Worten.</p>
  <div class="conversation">${chat.length?chat.map(m=>`<div class="chat-message ${m.role=== "user"?"user":""}"><strong>${m.role==="user"?"Du":"Redaktion"}</strong><p>${escape(m.message)}</p></div>`).join(""):'<p class="hint">Zum Beispiel: „Ich möchte verstehen, warum Samsung EUV-Maschinen nicht einfach selbst baut. Fang bei der Chipfertigung an und arbeite dich zu den technischen und wirtschaftlichen Hürden vor.“</p>'}</div>
  ${proposal?`<div class="proposal"><strong>Vorschlag für deinen Auftrag</strong><dl><dt>Thema</dt><dd>${escape(proposal.topic)}</dd><dt>Leitfrage</dt><dd>${escape(proposal.central_question)}</dd></dl><button class="secondary small" data-action="apply-proposal" ${disabled()}>In die Felder übernehmen</button><p class="hint">Du kannst den Vorschlag vor dem Speichern bearbeiten.</p></div>`:""}
  <form id="chat-form"><fieldset ${!project||running()?"disabled":""}>${area("chat-message","Dein Wunsch", "",4)}<button type="submit">Mit der Redaktion besprechen</button></fieldset></form>${!project?'<p class="hint">Lege links dein Projekt an, dann beginnt das Gespräch.</p>':""}</aside></div>`;
}
function renderResearch() {
  let html = heading(2,"Erst verstehen. Dann erzählen.","Die Recherche sammelt belastbare Quellen, erklärt die Grundlagen und macht offene Fragen sichtbar. Sie ist die Grundlage für den roten Faden.");
  if(!project) return html+empty("Ein Thema fehlt noch.","Lege zuerst deinen Podcast-Auftrag an.","Zur Idee",0);
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
  if(issues.length)html+=`<section class="panel"><h2>Was noch erklärt werden muss</h2><ul>${issues.map(issue=>`<li>${escape(issue)}</li>`).join("")}</ul></section>`;
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
  const a=currentAudio(),remote=a.provider==="openrouter_gemini_tts";
  html+=episodePicker()+`<section class="panel"><div class="panel-title"><h2>${escape(e.script.title)}</h2><span class="tag">${remote?"Gemini 3.1 Flash TTS · OpenRouter":"Qwen · lokal"}</span></div><p>${escape(a.voices.host_a)} & ${escape(a.voices.host_b)} · ${project.config.language==="de-DE"?"Deutsch":"English"}</p><p class="hint">${remote?"Gemini erzeugt die Sprache über OpenRouter und nutzt dafür dein API-Guthaben. Deine Grafikkarte wird für die Vertonung nicht benötigt.":"Qwen erzeugt die Sprache auf deinem Computer und beansprucht deine Grafikkarte."} Das Browserfenster darf geschlossen werden; der Studio-Server muss geöffnet bleiben.</p><button class="secondary small" data-step="0">Audioanbieter oder Stimmen ändern</button><label class="approval"><input id="audio-approval" type="checkbox" ${disabled()}><span>Ich habe dieses Skript gelesen und gebe diesen Stand mit dem angezeigten Audioanbieter und den Stimmen für Audio frei.${remote?" Ich möchte die API-Vertonung starten.":""}</span></label><div class="actions"><button id="audio-start" data-action="audio" disabled>Audio erzeugen</button><button class="secondary" data-step="${PAGE.scripts}">Skript nochmals lesen</button></div></section>`;
  if(e.audio.length) html+=`<section class="panel"><h2>Anhören und herunterladen</h2>${!e.audio_current?'<p class="note">Diese Aufnahme gehört zu einem früheren Skript- oder Stimmenstand.</p>':""}${e.audio.map((path,i)=>{const url="/media/"+encodeURIComponent(project.id)+"/"+path.split("/").map(encodeURIComponent).join("/");return `<div class="audio-track"><strong>Audiodatei ${i+1}</strong><audio controls preload="none" src="${url}"></audio><a href="${url}" download>MP3 herunterladen</a><p>${escape(path.split("/").slice(-2).join(" / "))}</p></div>`;}).join("")}</section>`;
  return html;
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
}
function render() { renderNavigation(); $("content").innerHTML=[renderBrief,renderResearch,renderOutline,renderProduction,renderScript,renderAudio][step](); renderJob(); syncPlayButtons(); }
async function refreshProjects() {
  boot=await api("/api/bootstrap");
  $("project-select").innerHTML='<option value="">Neues Projekt</option>'+boot.projects.map(p=>`<option value="${escape(p.id)}">${escape(p.topic)}</option>`).join("");
  $("project-select").value=project?.id||"";
}
async function selectProject(id, loaded=null, requestedPage=null) {
  project=id?(loaded||await api("/api/projects/"+encodeURIComponent(id))):null;
  $("project-select").value=id||"";
  voiceDrafts={};textDrafts={};episodeIndex=0;scriptEpisodeId=null;readingSnapshot=null;followWorkflow=requestedPage===null;
  step=requestedPage===null?recommendedPage():requestedPage;
  lastJobSignature=jobSignature(project?.job);updatePageUrl();render();
}
function configFromForm() {
  const c=structuredClone(project?.config||boot.defaults);
  for(const name of ["topic","central_question","prior_knowledge","depth_request","language"]) c[name]=$(name).value.trim();
  for(const name of ["focus_questions","excluded_topics","seed_urls"]) c[name]=$(name).value.split("\n").map(x=>x.trim()).filter(Boolean);
  c.target_total_minutes=$("target_total_minutes").value?Number($("target_total_minutes").value):null;
  return c;
}
async function storeKey() {
  const key=$("api-key")?.value.trim();
  if(key){const value=await api("/api/key",{key});boot.key_available=value.key_available;$("api-key").value="";}
}
async function saveBrief() {
  await storeKey();
  const data={config:configFromForm(),text:readTextChoice(),config_hash:project?.config_hash,
    audio_settings:{provider:$("tts-provider").value,voices:{host_a:$("host_a").value,host_b:$("host_b").value}},audio_hash:project?.audio_hash};
  let id=project?.id;
  if(id) await api(`/api/projects/${id}/save`,data);
  else id=(await api("/api/projects",data)).id;
  project=await api(`/api/projects/${id}`);await refreshProjects();updatePageUrl();render();notice("Auftrag gespeichert.");
}
async function start(action, extra={}) {
  if(!project) throw new Error("Lege zuerst dein Projekt an.");
  if(running()) throw new Error("Ein Auftrag läuft bereits.");
  const id=project.id;
  submitting=true;
  try { await api(`/api/projects/${id}/start`,{action,...extra});project=await api(`/api/projects/${id}`);lastJobSignature=jobSignature(project.job); }
  finally { submitting=false; }
  navigatePage(recommendedPage(),{automatic:true,push:false});
}
function jobSignature(job) { return job?`${job.id}:${job.status}`:""; }
document.addEventListener("submit",event=>{
  event.preventDefault();attempt(async()=>{
    if(event.target.id==="brief-form")await saveBrief();
    if(event.target.id==="chat-form") { const message=$("chat-message").value;await saveBrief();await start("assistant",{message}); }
  });
});
document.addEventListener("change",event=>attempt(async()=>{
  if(event.target.id==="project-select")await selectProject(event.target.value);
  if(event.target.id==="provider")changeTextProvider();
  if(event.target.id==="model-preset"){
    const custom=event.target.value==="custom";
    $("custom-model").hidden=!custom;$("model").value=custom?"":event.target.value;
    if(custom)$("model").focus();
  }
  if(event.target.id==="tts-provider"){
    const select=event.target,old=select.dataset.previous||currentAudio().provider;
    voiceDrafts[old]={host_a:$("host_a").value,host_b:$("host_b").value};
    const entry=audioCatalog()[select.value],voices=voiceDrafts[select.value]||entry.defaults;
    for(const role of ["host_a","host_b"])$(role).innerHTML=options(entry.voices,voices[role]);
    select.dataset.previous=select.value;
    $("speech-hint").textContent=speechHint(select.value);
  }
  if(["provider","tts-provider"].includes(event.target.id)){
    $("router-settings").hidden=$("provider").value!=="openrouter";
    $("key-settings").hidden=$("provider").value!=="openrouter"&&$("tts-provider").value!=="openrouter_gemini_tts";
  }
  if(event.target.id==="episode-select"){episodeIndex=Number(event.target.value);render();}
  if(event.target.id==="script-select"){scriptEpisodeId=event.target.value;readingSnapshot=null;render();}
  if(["tts-provider","language","host_a","host_b"].includes(event.target.id))refreshVoiceLibrary();
  if(event.target.id==="audio-approval")$("audio-start").disabled=!event.target.checked||running();
}));
document.addEventListener("click",event=>{
  const button=event.target.closest("button");if(!button)return;
  attempt(async()=>{
    if(button.id==="new-project"){await selectProject("");$("project-select").value="";}
    if(button.dataset.step!==undefined){navigatePage(Number(button.dataset.step));$("main").focus();window.scrollTo(0,0);return;}
    if(button.dataset.sample){
      const voice=$(button.dataset.sample).value,language=$("language").value;
      if($("tts-provider").value==="openrouter_gemini_tts"&&!savedSample(voice,language)){await saveBrief();await start("audio_sample",{voice,language,approve_sample:true});}
      else await playSample(voice,language,$("tts-provider").value);
    }
    if(button.dataset.playVoice)await playSample(button.dataset.playVoice,button.dataset.language);
    const action=button.dataset.action;if(!action)return;
    if(action==="refresh-script"){readingSnapshot=null;$("content").innerHTML=renderScript();return;}
    if(action==="quit"){await api("/api/quit",{});project=null;$("job-status").hidden=true;$("content").innerHTML='<section class="empty"><h1>Bis zum nächsten Gespräch.</h1><p>Das Studio ist beendet. Mit einem Doppelklick auf Podcast-Studio.cmd startest du es wieder.</p></section>';return;}
    if(action==="apply-proposal") { const p=[...project.chat].reverse().find(m=>m.role==="assistant");for(const k of ["topic","central_question","prior_knowledge","depth_request","focus_questions","excluded_topics"])$(k).value=Array.isArray(p[k])?p[k].join("\n"):p[k];notice("Vorschlag übernommen. Prüfe die Felder und speichere den Auftrag.");return; }
    if(action==="forget-key"){await api("/api/key",{key:""});await refreshProjects();$("api-key").value="";$("key-status").textContent=boot.key_available?"Key aus der Server-Umgebung verfügbar.":"Sitzungs-Key entfernt.";return;}
    if(action==="stop"){await api(`/api/projects/${project.id}/stop`,{});project=await api(`/api/projects/${project.id}`);render();return;}
    const extra={};
    if(action==="audio_samples"){
      extra.language=$("language").value;extra.approve_samples=true;
      await storeKey();if(!project)await saveBrief();
    }
    if(action==="replan")extra.message=$("outline-feedback").value;
    if(action==="script")extra.plan_hash=project.outline.hash;
    if(action==="revise"){extra.message=$("script-feedback").value;extra.episode=project.episodes[episodeIndex].script.episode_id;}
    if(action==="resume")extra.run_id=project.job?.run?.run_id||project.run?.run_id;
    if(action==="audio"){const e=project.episodes[episodeIndex];Object.assign(extra,{episode:e.script.episode_id,approve_audio:$("audio-approval").checked,script_hash:e.hash,readable_hash:e.readable_hash,config_hash:project.config_hash,audio_hash:project.audio_hash});}
    if(action==="check")await saveBrief();
    await start(action,extra);
  });
});
async function poll() {
  try {
    if(!project||submitting)return;
    const id=project.id,next=await api(`/api/projects/${id}`);
    if(project?.id!==id)return;
    const changed=jobSignature(next.job)!==lastJobSignature;
    const destination=followWorkflow?recommendedPage(next):step;
    const scriptsChanged=scriptCollectionKey(project)!==scriptCollectionKey(next);
    const readerOpen=step===PAGE.scripts&&readingSnapshot;
    // Keep unfinished form edits and the script being reviewed stable during polling.
    const samplesChanged=JSON.stringify(project.voice_samples)!==JSON.stringify(next.voice_samples);
    project.job=next.job;project.run=next.run;project.voice_samples=next.voice_samples;
    project.episodes=next.episodes;project.script_previews=next.script_previews;renderNavigation();renderJob();
    if(samplesChanged)refreshVoiceLibrary();
    if(changed||destination!==step){lastJobSignature=jobSignature(next.job);project=next;
      if(followWorkflow){step=destination;updatePageUrl();}
      if(readerOpen&&step===PAGE.scripts){renderNavigation();renderJob();refreshScriptReader();}
      else render();
      if(next.job?.status==="completed"&&next.job.sample)$("job-status").scrollIntoView({block:"nearest"});
    }else if(scriptsChanged&&step===PAGE.scripts)refreshScriptReader();
  }catch(error){renderJob();notice("Verbindung zum Studio unterbrochen. Ist das Studio-Fenster noch geöffnet?");}
}
attempt(async()=>{
  await refreshProjects();
  const params=window.location?new URLSearchParams(window.location.search):null;
  const requested=params?.get("project"), requestedStep=pageKeys.indexOf(params?.get("step"));
  if(requested&&boot.projects.some(p=>p.id===requested))await selectProject(requested,null,requestedStep<0?null:requestedStep);
  else {
    const saved=await Promise.all(boot.projects.map(p=>api("/api/projects/"+encodeURIComponent(p.id)).catch(()=>null)));
    const active=saved.find(p=>p?.job?.status==="running")||saved.filter(p=>p?.job?.started_at).sort((a,b)=>Date.parse(b.job.started_at)-Date.parse(a.job.started_at))[0];
    if(active)await selectProject(active.id,active);else render();
  }
  setInterval(poll,2500);
});
for(const event of ["play","pause","ended"])$("sample-player").addEventListener(event,syncPlayButtons);
window.addEventListener("popstate",()=>attempt(async()=>{
  const params=new URLSearchParams(window.location.search), id=params.get("project");
  const index=pageKeys.indexOf(params.get("step"));
  if(id&&boot.projects.some(p=>p.id===id)) {
    if(project?.id===id)navigatePage(index<0?recommendedPage():index,{push:false});
    else await selectProject(id,null,index<0?null:index);
  } else await selectProject("");
}));

// A small optional agent surface shares the visible navigation. It cannot approve generation.
if(document.modelContext?.registerTool){
  const lifecycle=new AbortController();
  const register=(tool)=>Promise.resolve(document.modelContext.registerTool(tool,{signal:lifecycle.signal})).catch(()=>{});
  register({name:"read_podcast_workspace",description:"Read the selected project's topic, current step and job status.",inputSchema:{type:"object",properties:{},additionalProperties:false},annotations:{readOnlyHint:true,untrustedContentHint:true},execute:()=>({project:project?.id||null,topic:project?.config.topic||null,step:steps[step],job:project?.job?.status||null})});
  register({name:"navigate_podcast_step",description:"Show a workflow step in the current project. Does not start or approve generation.",inputSchema:{type:"object",properties:{step:{type:"integer",minimum:1,maximum:6}},required:["step"],additionalProperties:false},annotations:{readOnlyHint:false},execute:input=>{if(!Number.isInteger(input?.step)||input.step<1||input.step>6)throw new Error("Schritt 1 bis 6 wählen.");navigatePage(input.step-1);return{step:steps[step]};}});
  window.addEventListener("pagehide",()=>lifecycle.abort(),{once:true});
}
