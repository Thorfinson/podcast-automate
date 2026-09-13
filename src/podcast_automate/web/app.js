"use strict";
const $ = id => document.getElementById(id);
const escape = value => String(value ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const steps = ["Idee & Stimmen", "Recherche", "Inhaltsverzeichnis", "Skript lesen", "Audio & Export"];
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
function renderNavigation() {
  $("steps").innerHTML = steps.map((name,i)=>`<button class="step" data-step="${i}" ${i===step?'aria-current="step"':""}><span class="step-number">${i+1}</span>${name}</button>`).join("");
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
function renderBrief() {
  const c = project?.config || boot.defaults, t = project?.text || {provider:"codex_cli",model:"",max_output_tokens:32768};
  const a=currentAudio(), remote=a.provider==="openrouter_gemini_tts";
  const chat = project?.chat || [], proposal = [...chat].reverse().find(m=>m.role==="assistant");
  return heading(1,"Ein gutes Gespräch beginnt mit einer Frage.","Lege fest, was du wirklich verstehen möchtest. Dein redaktioneller Partner hilft dir, daraus einen tragfähigen Podcast-Auftrag zu machen.") +
  `<div class="two-col"><div><form id="brief-form"><fieldset ${disabled()}><section class="panel"><div class="panel-title"><h2>Dein Thema</h2><span class="tag">Von Grund auf, mit Tiefe</span></div>
  ${textInput("topic","Worum soll es gehen?",project?c.topic:"")}${area("central_question","Welche Frage soll der Podcast beantworten?",project?c.central_question:"")}
  <div class="row"><div class="field"><label for="language">Sprache</label><select id="language"><option value="de-DE" ${c.language==="de-DE"?"selected":""}>Deutsch</option><option value="en-US" ${c.language==="en-US"?"selected":""}>English</option></select></div>${textInput("target_total_minutes","Gewünschte Gesamtlänge, optional",c.target_total_minutes||"","number")}</div>
  ${area("prior_knowledge","Was weißt du schon?",c.prior_knowledge,2)}
  <details><summary>Tiefe, Schwerpunkte und Quellen</summary>${area("depth_request","Wie soll erklärt werden?",c.depth_request,6)}${area("focus_questions","Schwerpunkte · eine Frage pro Zeile",c.focus_questions.join("\n"))}${area("excluded_topics","Was soll außen vor bleiben?",c.excluded_topics.join("\n"),2)}${area("seed_urls","Eigene Quellenlinks · ein Link pro Zeile",c.seed_urls.join("\n"),2)}<p class="hint">Längere Themen werden auf mehrere Folgen verteilt. Eine Audiodatei dauert höchstens 30 Minuten.</p></details>
  </section><section class="panel"><h2>Wer spricht deinen Podcast?</h2><div class="field"><label for="tts-provider">Audioanbieter</label><select id="tts-provider" data-previous="${a.provider}">${Object.entries(audioCatalog()).map(([id,entry])=>`<option value="${id}" ${id===a.provider?"selected":""}>${escape(entry.label)}</option>`).join("")}</select></div><p class="hint">Rollen geben dem Gespräch Richtung. Längere Erklärungen dürfen zusammenhängend bleiben.</p>
  ${voice("host_a","Der Experte","Ruhig, präzise. Erklärt Zusammenhänge und Mechanismen.",a)}${voice("host_b","Die neugierige Gesprächspartnerin","Hinterfragt, denkt weiter und fragt nach der Bedeutung.",a)}
  <p class="hint" id="speech-hint">${speechHint(a.provider)}</p><p class="hint">Hörproben verwenden die oben gewählte Sprache. Die Audioauswahl lässt sich auch nach dem Schreiben ändern.</p></section>
  <section class="panel"><h2>Wer schreibt mit?</h2><div class="field"><label for="provider">Anbieter für Redaktion, Inhaltsverzeichnis und Skript</label><select id="provider"><option value="codex_cli" ${t.provider==="codex_cli"?"selected":""}>Codex · bestehendes ChatGPT-Abo</option><option value="openrouter" ${t.provider==="openrouter"?"selected":""}>OpenRouter · API-Key</option></select></div>
  ${textInput("model","Modell-ID · bei Codex optional",t.model||"")}
  <div id="router-settings" ${t.provider!=="openrouter"?"hidden":""}>${textInput("max_output_tokens","Maximale Antwortlänge pro Textmodellaufruf (Tokens)",t.max_output_tokens,"number")}<p class="hint">Ein Skript benötigt mehrere Schreib- und Prüfdurchgänge.</p></div>
  <div id="key-settings" ${t.provider!=="openrouter"&&!remote?"hidden":""}>${textInput("api-key","OpenRouter-Key · für Gemini-Audio und optionale Textaufrufe","","password")}<p class="hint" id="key-status">${boot.key_available?"Ein Key ist für diese Sitzung verfügbar.":"Der Key bleibt nur für die Sitzung im Speicher."}</p><p class="hint">Gemini-Audio wird über dein OpenRouter-Guthaben abgerechnet. Codex kann unabhängig davon das Skript schreiben.</p><button type="button" class="secondary small" data-action="forget-key">Sitzungs-Key entfernen</button></div>
  <p class="hint">Codex wird über die lokale Installation mit deinem Abo aufgerufen. Die belegte Web-Recherche nutzt derzeit immer Codex. Das gewählte Textmodell bleibt für einen laufenden Auftrag fest.</p>
  <div class="actions"><button type="submit">${project?"Änderungen speichern":"Projekt anlegen"}</button>${project?'<button type="button" class="secondary" data-action="check">Verbindungen prüfen</button>':""}</div>
  ${project?'<p class="hint">Änderungen am Thema oder an Quellen benötigen eine neue Recherche. Ein geändertes Skript benötigt eine neue Freigabe.</p>':""}</section></fieldset></form>
  <section class="panel" id="voice-library-panel" ${remote?"":"hidden"}>${renderVoiceLibrary(c.language)}</section>
  ${project?'<button data-step="1" class="secondary">Weiter zur Recherche →</button>':""}</div>
  <aside class="panel tinted"><div class="panel-title"><h2>Dein redaktioneller Partner</h2><span class="tag">KI</span></div><p>Was soll am Ende wirklich klar sein? Beschreibe deine Wünsche in eigenen Worten.</p>
  <div class="conversation">${chat.length?chat.map(m=>`<div class="chat-message ${m.role=== "user"?"user":""}"><strong>${m.role==="user"?"Du":"Redaktion"}</strong><p>${escape(m.message)}</p></div>`).join(""):'<p class="hint">Zum Beispiel: „Ich möchte verstehen, warum Samsung EUV-Maschinen nicht einfach selbst baut. Fang bei der Chipfertigung an und arbeite dich zu den technischen und wirtschaftlichen Hürden vor.“</p>'}</div>
  ${proposal?`<div class="proposal"><strong>Vorschlag für deinen Auftrag</strong><dl><dt>Thema</dt><dd>${escape(proposal.topic)}</dd><dt>Leitfrage</dt><dd>${escape(proposal.central_question)}</dd></dl><button class="secondary small" data-action="apply-proposal" ${disabled()}>In die Felder übernehmen</button><p class="hint">Du kannst den Vorschlag vor dem Speichern bearbeiten.</p></div>`:""}
  <form id="chat-form"><fieldset ${!project||running()?"disabled":""}>${area("chat-message","Dein Wunsch", "",4)}<button type="submit">Mit der Redaktion besprechen</button></fieldset></form>${!project?'<p class="hint">Lege links dein Projekt an, dann beginnt das Gespräch.</p>':""}</aside></div>`;
}
function renderResearch() {
  let html = heading(2,"Erst verstehen. Dann erzählen.","Die Recherche sammelt belastbare Quellen, erklärt die Grundlagen und macht offene Fragen sichtbar. Sie ist die Grundlage für den roten Faden.");
  if(!project) return html+empty("Ein Thema fehlt noch.","Lege zuerst deinen Podcast-Auftrag an.","Zur Idee",0);
  html += `<section class="panel"><div class="panel-title"><h2>Quellen und Erkenntnisse</h2><span class="tag">Recherche mit Codex</span></div><p>Der gespeicherte Auftrag: <strong>${escape(project.config.central_question||project.config.topic)}</strong></p><div class="actions"><button data-action="research" ${disabled()}>${project.research?"Neu recherchieren":"Recherche starten"}</button>${project.research?`<button data-action="plan" class="secondary" ${disabled()}>Inhaltsverzeichnis entwerfen →</button>`:""}</div><p class="hint">Neue Recherche startet einen neuen Lauf. Unterbrochene Arbeit kannst du oben fortsetzen.</p></section>`;
  if(project.research) html+=`<section class="panel"><h2>Dein Recherche-Dossier</h2><pre class="document">${escape(project.research)}</pre></section>`;
  return html;
}
function renderOutline() {
  let html=heading(3,"Der rote Faden, bevor wir schreiben.","Prüfe, ob die Grundlagen tragen, die Kapitel aufeinander aufbauen und das Ganze deine Frage beantwortet. Erst deine Freigabe startet die Skripte.");
  const outline=project?.outline;
  if(!outline) return html+empty("Das Inhaltsverzeichnis entsteht aus der Recherche.","Nach dem geprüften Dossier entwirft die Redaktion Folgen und Kapitel. Hier kannst du sie anschließend verändern und freigeben.","Zur Recherche",1);
  const p=outline.plan, approved=outline.approval?.plan_hash===outline.hash;
  html+=`<section class="panel tinted"><h2>${escape(p.central_question)}</h2><p>${escape(p.explanation_path)}</p><div class="outline-summary"><span><strong>${p.episodes.length}</strong> Folgen</span><span><strong>${Math.round(p.episodes.reduce((s,e)=>s+e.target_minutes,0))}</strong> Minuten geplant</span><span>${approved?"Dieser Stand wurde freigegeben":"Wartet auf deine Durchsicht"}</span></div><p class="hint">${escape(p.scope_note)}</p></section>`;
  html+=p.episodes.map((e,i)=>`<section class="panel"><div class="episode-head"><span class="episode-num">${String(i+1).padStart(2,"0")}</span><div><h2>${escape(e.title)}</h2><p>${escape(e.central_question)}</p></div><span class="tag">ca. ${Math.round(e.target_minutes)} Min.</span></div><ol class="chapters">${e.scenes.map((s,n)=>`<li><span>${String(n+1).padStart(2,"0")}</span><div><strong>${escape(s.title)}</strong><p>${escape(s.question)}</p><details><summary>Was hier erklärt wird</summary>${s.explanation_steps.map(x=>`<p>${escape(x)}</p>`).join("")}</details></div></li>`).join("")}</ol>${e.deferred_questions.length?`<details><summary>Offene oder spätere Fragen</summary>${e.deferred_questions.map(q=>`<p>${escape(q)}</p>`).join("")}</details>`:""}</section>`).join("");
  const canReplan = !Object.entries(project.job?.run?.stages || {}).some(([n,r])=>n!=="planning"&&r.attempts>0);
  html+=`<section class="panel"><h2>Passt die Dramaturgie?</h2>${area("outline-feedback","Was soll sich ändern?","",3)}<div class="actions"><button class="secondary" data-action="replan" ${running()||!canReplan?"disabled":""}>Plan überarbeiten lassen</button><button data-action="script" ${disabled()}>${approved?"Skripterstellung fortsetzen":"Plan freigeben & Skripte schreiben"}</button></div><p class="hint">Die Skripte durchlaufen Lehrkonzept, Schreiben, Dialog-Polishing und Qualitätsprüfung. Anschließend liest du sie hier vor der Vertonung.</p>${!canReplan?`<button class="quiet" data-action="plan" ${disabled()}>Neues Inhaltsverzeichnis entwerfen</button>`:""}</section>`;
  return html;
}
function episodePicker() { return `<div class="field"><label for="episode-select">Folge auswählen</label><select id="episode-select">${project.episodes.map((e,i)=>`<option value="${i}" ${i===episodeIndex?"selected":""}>${i+1}. ${escape(e.script.title)}</option>`).join("")}</select></div>`; }
function renderScript() {
  let html=heading(4,"Lies das Gespräch in deinem Tempo.","Hier steht das geprüfte und sprachlich überarbeitete Skript. Entscheide selbst, ob Einstieg, Erklärungen und Gespräch für dich funktionieren.");
  if(!project?.episodes.length) return html+empty("Dein Skript wartet noch auf seinen roten Faden.","Gib zuerst das Inhaltsverzeichnis frei. Die Redaktion schreibt und prüft anschließend die vollständigen Folgen.","Zum Inhaltsverzeichnis",2);
  episodeIndex=Math.min(episodeIndex,project.episodes.length-1);
  const e=project.episodes[episodeIndex],s=e.script;
  html+=episodePicker()+`<div class="outline-summary"><span>${e.metrics.words.toLocaleString("de-DE")} Wörter</span><span>ca. ${Math.round(e.metrics.estimated_minutes)} Min. geschätzt</span><span>${s.chapters.length} Kapitel</span></div><article class="panel reader"><h1>${escape(s.title)}</h1>`;
  for(const chapter of s.chapters) html+=`<h2>${escape(chapter.title)}</h2>`+s.segments.filter(x=>x.chapter_id===chapter.chapter_id).map(x=>`<div class="utterance ${x.speaker_id}"><strong>${escape(project.config.voice_profile[x.speaker_id])}</strong><p>${escape(x.text)}</p></div>`).join("");
  html+=`</article><section class="panel"><h2>Deine redaktionelle Rückmeldung</h2>${area("script-feedback","Was fehlt oder klingt noch nicht richtig?","",4)}<div class="actions"><button class="secondary" data-action="revise" ${disabled()}>Diese Folge überarbeiten lassen</button><button data-step="4">Weiter zur Audio-Freigabe →</button></div><p class="hint">Eine Überarbeitung durchläuft erneut Polishing und Prüfung. Sie erhält eine neue Audio-Freigabe.</p></section>`;
  return html;
}
function renderAudio() {
  let html=heading(5,"Vom Text zum Gespräch.","Gib eine gelesene Folge mit dem gewählten Audioanbieter ausdrücklich frei. Fertige Abschnitte bleiben für eine Fortsetzung gespeichert.");
  if(!project?.episodes.length) return html+empty("Zuerst braucht es ein fertiges Skript.","Deine Freigabe gehört immer zu dem Text, den du tatsächlich gelesen hast.","Zum Skript",3);
  episodeIndex=Math.min(episodeIndex,project.episodes.length-1);
  const e=project.episodes[episodeIndex];
  const a=currentAudio(),remote=a.provider==="openrouter_gemini_tts";
  html+=episodePicker()+`<section class="panel"><div class="panel-title"><h2>${escape(e.script.title)}</h2><span class="tag">${remote?"Gemini 3.1 Flash TTS · OpenRouter":"Qwen · lokal"}</span></div><p>${escape(a.voices.host_a)} & ${escape(a.voices.host_b)} · ${project.config.language==="de-DE"?"Deutsch":"English"}</p><p class="hint">${remote?"Gemini erzeugt die Sprache über OpenRouter und nutzt dafür dein API-Guthaben. Deine Grafikkarte wird für die Vertonung nicht benötigt.":"Qwen erzeugt die Sprache auf deinem Computer und beansprucht deine Grafikkarte."} Das Browserfenster darf geschlossen werden; der Studio-Server muss geöffnet bleiben.</p><button class="secondary small" data-step="0">Audioanbieter oder Stimmen ändern</button><label class="approval"><input id="audio-approval" type="checkbox" ${disabled()}><span>Ich habe dieses Skript gelesen und gebe diesen Stand mit dem angezeigten Audioanbieter und den Stimmen für Audio frei.${remote?" Ich möchte die API-Vertonung starten.":""}</span></label><div class="actions"><button id="audio-start" data-action="audio" disabled>Audio erzeugen</button><button class="secondary" data-step="3">Skript nochmals lesen</button></div></section>`;
  if(e.audio.length) html+=`<section class="panel"><h2>Anhören und herunterladen</h2>${!e.audio_current?'<p class="note">Diese Aufnahme gehört zu einem früheren Skript- oder Stimmenstand.</p>':""}${e.audio.map((path,i)=>{const url="/media/"+encodeURIComponent(project.id)+"/"+path.split("/").map(encodeURIComponent).join("/");return `<div class="audio-track"><strong>Audiodatei ${i+1}</strong><audio controls preload="none" src="${url}"></audio><a href="${url}" download>MP3 herunterladen</a><p>${escape(path.split("/").slice(-2).join(" / "))}</p></div>`;}).join("")}</section>`;
  return html;
}
function renderScriptProgress(p, active) {
  if(p?.phase!=="script")return "";
  const elapsed=p.activity_started_at?Math.max(0,Math.floor((Date.now()-Date.parse(p.activity_started_at))/60000)):null;
  return `<section class="script-progress"><p class="current-episode"><strong>${p.current_episode?`Folge ${Number(p.episode_number)} von ${Number(p.total_segments)} · ${escape(p.episode_title)}`:escape(stageNames[p.stage]||"Fortschritt")}</strong></p><p>${active?'<span class="activity-dot" aria-hidden="true"></span>':"Zuletzt: "}${escape(p.activity)}${active&&elapsed!==null?` · seit ${elapsed<1?"weniger als einer Minute":`${elapsed} Min.`}`:""}</p><p class="hint">Die Anzeige aktualisiert sich automatisch. Ein Modellaufruf kann mehrere Minuten dauern.</p>${p.total_segments?`<progress value="${Number(p.completed_segments)}" max="${Number(p.total_segments)}" aria-label="Fertige Folgen in dieser Stufe"></progress><p>${Number(p.completed_segments)} von ${Number(p.total_segments)} Folgen: ${escape(stageNames[p.stage]||p.stage)} abgeschlossen</p>`:""}<ol class="episode-progress">${(p.episodes||[]).map(e=>`<li>${e.completed?"✓":e.episode_id===p.current_episode?"●":"○"} ${escape(e.title)}</li>`).join("")}</ol>${(p.episodes||[]).filter(e=>e.teaching_preview).map(e=>`<details data-progress-episode="${escape(e.episode_id)}"><summary>Lehrkonzept lesen: ${escape(e.title)}</summary><pre class="document" data-progress-preview="${escape(e.episode_id)}">${escape(e.teaching_preview)}</pre></details>`).join("")}</section>`;
}
function renderJob() {
  const j=project?.job, box=$("job-status");
  const legacy=!j&&project?.run&&project.run.status!=="completed"?project.run:null;
  box.hidden=!j&&!legacy;
  if(box.hidden){box.innerHTML="";lastJobView="";return;}
  const r=j?.run||legacy, active=j?.status==="running", state=j?.status||legacy.status;
  const view=JSON.stringify({project:project?.id,job:j,legacy,
    minute:active?Math.floor((Date.now()-Date.parse(j.started_at))/60000):null});
  // Preserve the audio element and its playback position during status polling.
  if(view===lastJobView)return;
  lastJobView=view;
  const opened=new Set(Array.from(box.querySelectorAll?.("details[open][data-progress-episode]")||[],el=>el.dataset.progressEpisode));
  const scrolls=new Map(Array.from(box.querySelectorAll?.("[data-progress-preview]")||[],el=>[el.dataset.progressPreview,el.scrollTop]));
  const missingFoundation=!active&&Object.values(r?.stages||{}).some(v=>v.error?.code==="teaching_research_required");
  const designBlocked=!active&&Object.values(r?.stages||{}).some(v=>v.error?.code==="teaching_design_failed");
  const foundationResearch=active&&j?.progress?.phase==="foundation_research";
  const title=designBlocked?"Lehrkonzept angehalten: Erklärung noch unvollständig":foundationResearch?"Fehlende Erklärgrundlagen werden automatisch recherchiert":missingFoundation?"Automatische Recherche konnte noch nicht abgeschlossen werden":active?actionNames[j.action]:({completed:"Arbeitsschritt abgeschlossen",review_ready:"Inhaltsverzeichnis bereit zur Durchsicht",interrupted:"Auftrag angehalten",waiting_for_quota:"Anbieterlimit erreicht",blocked:"Dieser Schritt braucht Aufmerksamkeit",failed:"Auftrag fehlgeschlagen",pending:"Auftrag wartet"}[state]||"Gespeicherter Auftrag");
  const resumable=r&&["interrupted","waiting_for_quota","failed","blocked","pending","running"].includes(state)&&!active&&!missingFoundation&&!designBlocked;
  const message=designBlocked&&j?.progress?.review_issues?.length?"Die automatische Überarbeitung hat noch nicht alle Kritikpunkte gelöst. Der bisherige Stand ist gespeichert.":missingFoundation&&/research_needed\.md/.test(j?.message||"")?"Der Abgleich zwischen Quellen und Lehrkonzept ist noch offen. Der bisherige Auftrag bleibt gespeichert.":j?.message;
  box.innerHTML=`<div class="job-top"><strong>${escape(title)}</strong>${active?'<button class="danger small" data-action="stop">Auftrag anhalten</button>':resumable?'<button class="secondary small" data-action="resume">Fortsetzen</button>':""}</div>${message?`<p>${escape(message)}</p>`:""}${active?`<p>Seit ${Math.max(0,Math.floor((Date.now()-Date.parse(j.started_at))/60000))} Min. · Fertige Schritte werden gespeichert.</p>`:""}${r?`<div class="stage-strip">${Object.entries(r.stages).map(([name,v])=>`<span class="${v.status}">${v.status==="completed"?"✓ ":""}${stageNames[name]||escape(name)}</span>`).join("")}</div>`:""}${j?.progress?.phase!=="script"&&j?.progress?.total_segments!==undefined?`<p>${j.progress.completed_segments} von ${j.progress.total_segments} ${j.action==="audio_samples"?"Hörproben":"Sprechabschnitten"} fertig</p><progress value="${Number(j.progress.completed_segments)}" max="${Number(j.progress.total_segments)}"></progress>`:""}${j?.checks?`<ul class="checks">${j.checks.checks.map(c=>`<li>${c.ok?"✓":"○"} ${escape(c.name)}<span class="hint">${escape(c.detail)}</span></li>`).join("")}</ul><p>Diese Prüfung erzeugt kein Audio.</p>`:""}`;
  if(missingFoundation)box.innerHTML+=`<p>Der aktuelle Stand und die bisherigen Belege sind gespeichert. Noch offene Fragen:</p>${j?.research_gaps?.length?`<ul>${j.research_gaps.map(g=>`<li><strong>${escape(g.question)}</strong><p>${escape(g.why_needed)}</p></li>`).join("")}</ul>`:""}<button class="secondary small" data-step="1">Bisherige Recherche ansehen</button>`;
  if(j?.sample)box.innerHTML+=`<p>Hörprobe: ${escape(j.sample.voice)} · ${escape(j.sample.language)}</p><audio controls preload="none" src="${mediaUrl(j.sample.audio)}"></audio><div class="actions"><a href="${mediaUrl(j.sample.audio)}" target="_blank" rel="noopener">Hörprobe separat öffnen</a><a href="${mediaUrl(j.sample.audio)}" download>MP3 herunterladen</a></div>`;
  if(j?.action==="audio_samples"&&j?.progress?.current_voice&&active)box.innerHTML+=`<p>Aktuelle Stimme: ${escape(j.progress.current_voice)}</p>`;
  box.innerHTML+=renderScriptProgress(j?.progress,active);
  if(j?.progress?.review_issues?.length)box.innerHTML+=`<details class="review-points" open><summary>Was noch erklärt werden muss</summary><ul>${j.progress.review_issues.map(issue=>`<li>${escape(issue)}</li>`).join("")}</ul></details>`;
  for(const detail of box.querySelectorAll?.("[data-progress-episode]")||[])detail.open=opened.has(detail.dataset.progressEpisode);
  for(const preview of box.querySelectorAll?.("[data-progress-preview]")||[])preview.scrollTop=scrolls.get(preview.dataset.progressPreview)||0;
}
function render() { renderNavigation(); renderJob(); $("content").innerHTML=[renderBrief,renderResearch,renderOutline,renderScript,renderAudio][step](); syncPlayButtons(); }
async function refreshProjects() {
  boot=await api("/api/bootstrap");
  $("project-select").innerHTML='<option value="">Neues Projekt</option>'+boot.projects.map(p=>`<option value="${escape(p.id)}">${escape(p.topic)}</option>`).join("");
  $("project-select").value=project?.id||"";
}
async function selectProject(id, loaded=null) {
  project=id?(loaded||await api("/api/projects/"+encodeURIComponent(id))):null;
  window.history?.replaceState(null,"",id?"/?project="+encodeURIComponent(id):"/");
  $("project-select").value=id||"";
  voiceDrafts={};episodeIndex=0; step=0; lastJobSignature=jobSignature(project?.job); render();
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
  const data={config:configFromForm(),text:{provider:$("provider").value,model:$("model").value.trim()||null,max_output_tokens:Number($("max_output_tokens").value)},config_hash:project?.config_hash,
    audio_settings:{provider:$("tts-provider").value,voices:{host_a:$("host_a").value,host_b:$("host_b").value}},audio_hash:project?.audio_hash};
  let id=project?.id;
  if(id) await api(`/api/projects/${id}/save`,data);
  else id=(await api("/api/projects",data)).id;
  project=await api(`/api/projects/${id}`);await refreshProjects();render();notice("Auftrag gespeichert.");
}
async function start(action, extra={}) {
  if(!project) throw new Error("Lege zuerst dein Projekt an.");
  if(running()) throw new Error("Ein Auftrag läuft bereits.");
  const id=project.id;
  submitting=true;
  try { await api(`/api/projects/${id}/start`,{action,...extra});project=await api(`/api/projects/${id}`);lastJobSignature=jobSignature(project.job); }
  finally { submitting=false; }
  render();
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
  if(["tts-provider","language","host_a","host_b"].includes(event.target.id))refreshVoiceLibrary();
  if(event.target.id==="audio-approval")$("audio-start").disabled=!event.target.checked||running();
}));
document.addEventListener("click",event=>{
  const button=event.target.closest("button");if(!button)return;
  attempt(async()=>{
    if(button.id==="new-project"){await selectProject("");$("project-select").value="";}
    if(button.dataset.step!==undefined){step=Number(button.dataset.step);render();$("main").focus();window.scrollTo(0,0);}
    if(button.dataset.sample){
      const voice=$(button.dataset.sample).value,language=$("language").value;
      if($("tts-provider").value==="openrouter_gemini_tts"&&!savedSample(voice,language)){await saveBrief();await start("audio_sample",{voice,language,approve_sample:true});}
      else await playSample(voice,language,$("tts-provider").value);
    }
    if(button.dataset.playVoice)await playSample(button.dataset.playVoice,button.dataset.language);
    const action=button.dataset.action;if(!action)return;
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
    // Keep unfinished form edits and the script being reviewed stable during polling.
    const samplesChanged=JSON.stringify(project.voice_samples)!==JSON.stringify(next.voice_samples);
    project.job=next.job;project.run=next.run;project.voice_samples=next.voice_samples;renderJob();
    if(samplesChanged)refreshVoiceLibrary();
    if(changed){lastJobSignature=jobSignature(next.job);project=next;
      if(next.job?.status==="review_ready")step=2;
      else if(next.job?.status==="completed")step=({research:1,plan:2,replan:2,script:3,revise:3,audio:4}[next.job.action]??step);
      render();
      if(next.job?.status==="completed"&&next.job.sample)$("job-status").scrollIntoView({block:"nearest"});
    }
  }catch(error){notice("Verbindung zum Studio unterbrochen. Ist das Studio-Fenster noch geöffnet?");}
}
attempt(async()=>{
  await refreshProjects();
  const requested=window.location?new URLSearchParams(window.location.search).get("project"):null;
  if(requested&&boot.projects.some(p=>p.id===requested))await selectProject(requested);
  else {
    const saved=await Promise.all(boot.projects.map(p=>api("/api/projects/"+encodeURIComponent(p.id)).catch(()=>null)));
    const active=saved.find(p=>p?.job?.status==="running")||saved.filter(p=>p?.job?.started_at).sort((a,b)=>Date.parse(b.job.started_at)-Date.parse(a.job.started_at))[0];
    if(active)await selectProject(active.id,active);else render();
  }
  setInterval(poll,2500);
});
for(const event of ["play","pause","ended"])$("sample-player").addEventListener(event,syncPlayButtons);

// A small optional agent surface shares the visible navigation. It cannot approve generation.
if(document.modelContext?.registerTool){
  const lifecycle=new AbortController();
  const register=(tool)=>Promise.resolve(document.modelContext.registerTool(tool,{signal:lifecycle.signal})).catch(()=>{});
  register({name:"read_podcast_workspace",description:"Read the selected project's topic, current step and job status.",inputSchema:{type:"object",properties:{},additionalProperties:false},annotations:{readOnlyHint:true,untrustedContentHint:true},execute:()=>({project:project?.id||null,topic:project?.config.topic||null,step:steps[step],job:project?.job?.status||null})});
  register({name:"navigate_podcast_step",description:"Show a workflow step in the current project. Does not start or approve generation.",inputSchema:{type:"object",properties:{step:{type:"integer",minimum:1,maximum:5}},required:["step"],additionalProperties:false},annotations:{readOnlyHint:false},execute:input=>{if(!Number.isInteger(input?.step)||input.step<1||input.step>5)throw new Error("Schritt 1 bis 5 wählen.");step=input.step-1;render();return{step:steps[step]};}});
  window.addEventListener("pagehide",()=>lifecycle.abort(),{once:true});
}
