---
title: Security
doc_type: security
status: current
last_reviewed: 2026-10-07
covers:
  - src/podcast_automate/google_speech.py
  - src/podcast_automate/studio.py
  - src/podcast_automate/studio_worker.py
  - src/podcast_automate/scripting.py
  - src/podcast_automate/research.py
  - src/podcast_automate/sources.py
  - src/podcast_automate/downloads.py
  - src/podcast_automate/storage.py
  - src/podcast_automate/attachments.py
  - src/podcast_automate/openrouter.py
  - src/podcast_automate/web_search.py
  - src/podcast_automate/provider_pool.py
  - src/podcast_automate/speech.py
  - src/podcast_automate/jev.py
  - src/podcast_automate/claude_code.py
  - src/podcast_automate/codex.py
  - src/podcast_automate/cli.py
  - src/podcast_automate/model_trace.py
  - src/podcast_automate/logs.py
  - src/podcast_automate/research_models.py
  - src/podcast_automate/script_checks.py
---

# Security

Podcast Automate is a personal, local application: no external web host, no login to the Studio, no upload of
project files to a hosting service. How to report a vulnerability, privately through GitHub, is in the repository's
[security policy](../SECURITY.md) (why: D-158).

## Trust boundaries

Project files, original sources and research contexts are stored locally. Selected content leaves the computer only for
the processing you commissioned:

| What leaves the computer | To whom | When |
| --- | --- | --- |
| Prompts with the selected content: brief, chat messages, text of attachments and provided works, source sections, plans, scripts and reviews | OpenAI (Codex CLI, your ChatGPT subscription) or Anthropic (Claude Code CLI, your claude.ai login, Claude Max) | every text call the selection routes to that subscription, live web research included |
| The same prompts; web research only with the Perplexity search, as planning the queries and choosing among the results | OpenRouter and the model provider it routes to, billed to your API credit | only with an OpenRouter text model |
| The same prompts, live web research included | Anthropic (Claude Code CLI on your Anthropic API key), billed to your key | only with Claude on the API key (`claude_api`) |
| The planned search queries and their language filter, never the task's prompt or any source text | Perplexity (Search API), billed to your Perplexity key | only with the web search through Perplexity ([ARCHITECTURE](ARCHITECTURE.md#web-search-through-perplexity)) |
| The spoken text of approved scripts and of conversation samples | Google (Gemini API), billed to your Google key | only for a Gemini recording through Google or a new conversation sample |
| The spoken text of approved scripts and of voice samples | OpenRouter (Gemini TTS) | only for a Gemini recording through OpenRouter or a new Gemini voice sample |
| Reported gaps and corpus sections of the research | OpenRouter (Jev, TypeSafe) | only when the gap probe with Jev is on ([RESEARCH](RESEARCH.md#gap-probe)) |
| Requests for source documents; title or DOI lookups for free copies | the public servers of the sources; OpenAlex, Semantic Scholar and Europe PMC, and Unpaywall or CORE only when you set their contact address or key | during research ([RESEARCH](RESEARCH.md#pipeline)) |
| Quota and login queries | the Codex app server (`account/rateLimits/read`) and `claude auth status` | before model calls, `pla doctor`, `pla quota` |

What never leaves the computer, or never gets in:

- **Keys never go into prompts.** A chat message or upload (name or text) containing a key stored in the Studio (the CORE key included), an
  OpenRouter key (`sk-or-…`), an Anthropic key (`sk-ant-…`), a Perplexity key (`pplx-…`) or a Google key (`AIza…`),
  and an OpenRouter prompt, a prompt of Claude on the API key, a Perplexity search query or Gemini text containing the
  key in use, are refused with `credential_in_prompt`; the editorial partner never sees a key.
- **No browser credentials.** Source retrieval uses no browser cookies or credentials. Each source address and each
  redirect must resolve to a public server (`sources.public_url`); an address in a private network is refused with
  `invalid_source_url`.
- **No access log.** The Studio server logs no requests, only an unexpected failure with its path and error type,
  never the body.
- The Claude Code CLI runs with telemetry, error reporting and non-essential traffic off
  (`claude_code.claude_environment`; isolation: [ARCHITECTURE](ARCHITECTURE.md#text-provider-adapters)).
- An OpenRouter privacy setting (Zero Data Retention) that excludes the Gemini or Jev endpoint stops the run with
  `openrouter_privacy`; the application never changes it ([AUDIO](AUDIO.md#gemini-via-openrouter)).

## Studio access

- **Local by default.** The Studio server binds to `127.0.0.1` (port 8765) and is reachable only from this computer.
- **Home network on request.** `pla studio --lan` (on Windows **`Podcast-Studio-WLAN.cmd`**) listens on all
  interfaces, so that a device in your home network, such as a phone in the Wi-Fi, can use it
  ([STUDIO](STUDIO.md#using-it-from-a-phone)). Such a device can do everything there that it can do at the computer,
  without a login.
- **Outside addresses are refused.** Only this computer (loopback) and, in LAN mode, private or link-local addresses
  are served (`studio.client_scope`); any other address gets `forbidden`.
- **No foreign web page can steer the Studio,** locally or in the home network:
  - the server answers only under the address the device used to reach it: `127.0.0.1:<port>` or `localhost:<port>`
    locally, the computer's home-network address in LAN mode (the `Host` check also blocks a rebound domain);
  - a request from another `Origin`, or with `Sec-Fetch-Site: cross-site`, is refused;
  - every change needs the session key of the opened page (`X-Studio-Token`, new at every server start);
  - responses carry a same-origin Content Security Policy, `X-Content-Type-Options: nosniff`,
    `Referrer-Policy: no-referrer` and `Cache-Control: no-store`.
- **Unencrypted.** Plain HTTP, meant for your own Wi-Fi, not a public one. From outside the home the Studio is not
  reachable; that would need a VPN into the home network.
- **Firewall.** On the first LAN start the Windows firewall asks whether Python may be reachable in the network: allow
  only **„Private Netzwerke“** (private networks).

## Secrets and keys

### Subscription logins

- The application uses the official CLI logins (`codex login`, `claude auth login`); it reads no access tokens and
  builds no access of its own from CLI session tokens.
- For the subscription providers (`codex_cli`, `claude_code`, and both under `auto`) API-key logins are refused,
  because they would bill per call (why: D-109): Codex must be logged in with ChatGPT, Claude Code must report
  `authMethod: "claude.ai"`; anything else stops as `subscription_required`.
- Their CLIs are started without `OPENAI_API_KEY`, `CODEX_API_KEY`, `OPENROUTER_API_KEY`, `GEMINI_API_KEY`,
  `GOOGLE_API_KEY`, `ANTHROPIC_API_KEY` and `ANTHROPIC_AUTH_TOKEN` (`codex.subscription_environment`), so they can
  only use the subscription login.
  `CLAUDE_CONFIG_DIR` stays untouched, because it holds the Claude login.
- Claude on your Anthropic API key (`claude_api`) is a separate, billed provider that uses the key and nothing else:
  its calls drop every variable that would outrank or redirect the key, receive the key as `ANTHROPIC_API_KEY`, and
  are discarded with `claude_api_auth_mismatch` when Claude Code reports that it used no API key (why: D-145;
  environment and check: [ARCHITECTURE](ARCHITECTURE.md#claude-code)). It runs no login check and never touches the
  subscription login.
- The quota store `~/.podcast-automate/subscriptions.json` never holds credentials. The login check keeps only whether
  you are logged in, the login method, the subscription type and the CLI version, no e-mail address or IDs.

### Keys in the credential store

Every key entered in the Studio (OpenRouter, Google, Anthropic, Perplexity, CORE) is kept across restarts in the
operating system's credential store (why: D-167):

- `pla studio` opens the store through the `keyring` package (`key_store.Vault`): on Windows the Credential Manager,
  on macOS the login keychain, on Linux the Secret Service. Each key is one entry under the service
  `podcast-automate` with its kind (`openrouter`, `google`, `anthropic`, `perplexity`, `core`) as the user name,
  encrypted with your login. You find and delete them there too (Windows: „Anmeldeinformationsverwaltung“ →
  „Windows-Anmeldeinformationen“ → „Generische Anmeldeinformationen“).
- At its start the server loads every stored key into memory (`Studio.load_stored_keys`); from there a key reaches a
  worker as before, through its standard input. **„Key entfernen“** (remove key) removes the entry as well.
- The settings page says where each key comes from: the store, this session only, or an environment variable of the
  server (`Studio.key_source`). A system without a store, or a store that refuses, keeps the keys in memory only, gone
  after a restart, and the page says so. A key that could not be stored is also removed from the store, so an older
  one cannot come back.
- Any program running under your account can read the store, as it can read your environment variables; the store
  protects the keys on the disk and from other accounts. A key entered from another device in the home network
  (`pla studio --lan`) crosses the network unencrypted: enter keys at the computer that runs the Studio.
- A Studio built by a test or by `make_server` without a store never opens the system's own.

### OpenRouter key in the Studio

- The key is entered on the **„Einstellungen“** (settings) page ([STUDIO](STUDIO.md#settings-page)) or in a hold
  card that asks for it, and serves OpenRouter text, Gemini audio through OpenRouter and Jev.
- It stays in the credential store and the local Studio server's memory and reaches a worker through its standard
  input, not through process arguments; it is stored neither in project files nor in browser storage.
- Without a store it must be entered again after a Studio restart, unless the server has `OPENROUTER_API_KEY` in its
  environment. **„Key entfernen“** (remove key) removes only the key entered in the Studio, not that one.
- The key can be exchanged at any time; a rotated key needs no new run.
- A worker hands the key on only where OpenRouter is used: to a text job while it works with OpenRouter, to runs that
  use the Jev gap probe, to Gemini recordings and to new Gemini voice samples.

### Google key in the Studio

The Google key serves Gemini audio through Google and the conversation samples (why: D-138):

- It is entered under **„Google-Key“** on the settings page or in a hold card that asks for it, kept in the
  credential store and the Studio server's memory (or read from `GEMINI_API_KEY` in its environment), and handled
  like the OpenRouter key: standard input to a worker, never in project files or browser storage.
- A worker receives it only for audio, resume and the connection check; the conversation sample is spoken by the
  Studio server itself.
- Google's Gemini API terms distinguish unpaid use, whose requests Google may use to improve its products, from paid
  use, whose requests it does not (as known when this was written; not re-checked on 2026-10-06). The key's tier is
  set in Google AI Studio, not here.

### Anthropic key in the Studio

The Anthropic key serves Claude on your API key (`claude_api`) and nothing else (why: D-145):

- It is entered under **„Anthropic-Key“** on the settings page, in a hold card that asks for it or in the note
  **„Anthropic-Key fehlt“** (Anthropic key missing), kept in the credential store and the Studio server's memory (or
  read from `ANTHROPIC_API_KEY` in its environment), and handled like the OpenRouter key: standard input to a worker,
  never in project files or browser storage.
- A worker hands it only to pools that run Claude on the key (`studio_worker.text_key`,
  `provider_pool.use_anthropic_key`): to a text job while it works with `claude_api`, and to the expression tags and
  the companion kit of such a run. A subscription call never receives it.
- Every call is billed to your Anthropic account; the run's [money limit](BUSINESS_LOGIC.md#money-limit) bounds what a
  run may spend.

### Perplexity key in the Studio

The Perplexity key serves the web search through Perplexity and nothing else (why: D-151):

- It is entered under **„Perplexity-Key“** on the settings page, in a hold card that asks for it or in the note
  **„Perplexity-Key fehlt“** (Perplexity key missing), kept in the credential store and the Studio server's memory
  (or read from `PERPLEXITY_API_KEY` in its environment), and handled like the OpenRouter key: standard input to a
  worker, never in project files or browser storage.
- A worker holds it for the pools of runs that search through Perplexity (`provider_pool.use_worker_key`); no text
  model, subscription or OpenRouter request ever receives it, only the search requests to Perplexity.
- Every search request is billed to your Perplexity account; the run's [money limit](BUSINESS_LOGIC.md#money-limit)
  bounds what a run may spend.

### CORE key in the Studio

CORE's free key serves the search for a free copy of a work whose own address refused the download, and nothing
else ([Blocked downloads](RESEARCH.md#blocked-downloads-free-copies-and-open-archives); why: D-167):

- It is entered under **„CORE-Key“** on the settings page, kept in the credential store and the Studio server's
  memory (or read from `PLA_CORE_API_KEY` in its environment), and handled like the OpenRouter key: standard input to a
  worker, never in project files or browser storage; a chat message containing it is refused with
  `credential_in_prompt`.
- A worker holds it for the source search (`sources.use_core_key`); it outranks `PLA_CORE_API_KEY`. Only the title or
  DOI of the work goes to CORE.

### OpenRouter key on the command line

- `--api-key` without a value asks for the key hidden in the terminal. A key given as a value
  (`--api-key "YOUR_KEY"`) is refused unread with `invalid_request`, because it would stand in the process list and
  the shell history. (why: D-112)
- The alternative is `OPENROUTER_API_KEY`, which the OpenRouter adapter reads; a key entered hidden takes precedence.
  Without a secure terminal input the hidden prompt aborts instead of reading the key visibly; use the variable then.
- The Anthropic key of `claude_api` follows the same rules: `pla research`, `pla script` and `pla resume` take
  `--api-key` without a value, and the hidden prompt names Anthropic or OpenRouter after the run's provider; the
  alternative is `ANTHROPIC_API_KEY`, which the adapter reads when no key was entered. A value given to `--api-key` is
  refused naming both variables.
- The Perplexity key of `--web-search perplexity` has no option of its own: `pla research` and `pla script` read it
  from `PERPLEXITY_API_KEY`.
- Keys do not belong in `project.yaml`. The application writes them into no prompt, project or answer file.

### Keys on the wire

- OpenRouter text, Gemini speech and Jev requests keep the key in memory and in the `Authorization` header only, and
  follow no redirects (`openrouter.NoRedirect`). Gemini speech through Google sends its key only in the
  `x-goog-api-key` header, never in the URL, and follows no redirects either.
- A Perplexity search request keeps its key in the `Authorization` header only and follows no redirects
  (`openrouter.NoRedirect`); an answer that contains the key is not used (`credential_in_response`).
- Claude on the API key hands the key to the Claude Code process only in its environment, never as an argument; an
  answer or CLI output that contains the key is not stored (`credential_in_response`).
- A source fetch that carries a service key (CORE's free key) follows a redirect only on the same
  host and never from https to http (`sources.PublicRedirect`); otherwise it aborts with `source_download_failed`, so
  the key never reaches another server. (why: D-113)

### Credentials in traces, diagnostics and logs

- Before anything is stored, `model_trace.redact` removes the known keys and anything that looks like a credential
  (`sk-…`, `sess-…`, `pplx-…`, `AIza…`, `Bearer …`, values after `api_key`, `access_token`, `token`, `password` or `secret`): in the live
  trace `runs/<run_id>/model_trace.json` and in the tracebacks of failed stages and workers (`logs.record_failure`:
  `runs/<run_id>/failures/`, `<project>/studio/failures/`). Error messages and diagnostic files shown in the Studio
  replace each of its stored keys (OpenRouter, Google, Anthropic, Perplexity) with „[Key verborgen]“ (key hidden).
- `calls/call_*/diagnostics.json` keeps only sanitised technical diagnostics, no raw error messages, prompts, tool
  outputs or credentials. The one exception is a Codex failure the adapter cannot name (`codex_failed`): its
  `failure.json` and stop message carry Codex's own short reason, redacted by `model_trace.redact` and cut to 300
  characters (`codex.provider_message`; why: D-129). Likewise an OpenRouter refusal (HTTP 403, text and Gemini
  speech) keeps OpenRouter's own short reason in its stop message, without the key and at most 300 characters
  (`openrouter.refusal_reason`; why: D-132); `work_context.json` keeps a bounded selection of a call's inputs, without
  full prompts or source texts.
- The rotating log files `.studio/studio.log`, `<project>/studio/worker.log` and `<project>/logs/pla.log` (tracebacks
  included) and the terminal output of every `pla` process pass through `logs.scrub`: the keys the process knows (the
  Studio's stored keys, a worker's keys, a key typed after `--api-key`, `OPENROUTER_API_KEY`, `GEMINI_API_KEY`,
  `ANTHROPIC_API_KEY`, `PERPLEXITY_API_KEY`; `logs.add_secret`) are
  replaced whole, then the patterns of `model_trace.redact` apply. The tail of a crashed worker's error output, shown
  in the Studio under „Technische Details“ (technical details), passes the same filter.
- Not filtered: a traceback that Python itself prints when a process dies outside its own error handling. A worker's
  stays raw in `<project>/studio/stderr/<job>.log` until a later job removes it once it is an hour old; a relaunched
  Studio's stays in `.studio/relaunch.log`. A credential in a format the patterns don't know is caught only if the
  process knows it. Log locations and rotation: [OPERATIONS](OPERATIONS.md#logs-and-diagnosis).
- Credentials do not belong in the Git repository; `.env` files and `projects/` are git-ignored.

## Local files

- **Local sources must lie inside the project folder.** Relative paths in `local_sources` refer to the project
  folder. A research run refuses an absolute path or one that leaves the folder before it starts, with
  `local_source_outside`; a script run with `invalid_request` (`scripting.local_source_paths`). To use such
  a file, copy it into the project folder or upload it in the Studio and adjust `local_sources` in `project.yaml`.
  (why: D-111)
- **The browser may only name files under the project's `inputs/`** in `local_sources`, the Studio's own uploads.
  Other entries come only from the `project.yaml` on disk and stay unchanged when the brief is saved
  (`studio.kept_local_sources`).
- **Uploads are copies** under names the server creates itself; the original files on your computer stay unchanged
  (storage, limits and formats: [STUDIO](STUDIO.md#attachments-and-provided-works)).
- **Artifact paths stay inside the project.** Every artifact path the Studio reads or serves, downloads included, is
  resolved inside the project folder; a path that leaves it is refused with `invalid_path` (`storage.inside`).

## Source rights and privacy

What version 0.1 implements:

- **Private learning only.** `export_context` is limited to `private_learning`. The application offers no public
  publishing workflow and grants no rights clearance for republication; an export never publishes anything.
- **Fixed rights labels.** Every imported source stores `license_status: unknown`, `allowed_usage: private_learning`
  and `private: true`: fixed labels, not adjustable rights or export blocks. Private scripts, show notes and audio may
  use these sources.
- **Quotes and paraphrases.** Paraphrase is the default; short quotes stay attributed to their source.
  - A composed dossier of older runs limits direct quotes to 25 words and attributed paraphrases to 150 words per
    source.
  - An assembled dossier holds all verified answers; there the episode script quotes each source at most 25 words
    verbatim (`script_checks.QUOTED_WORDS_PER_SOURCE`, `script_checks.quotation_errors`; counting:
    [SCRIPTS](SCRIPTS.md#script-review)). (why: D-110)
  - These limits do not replace a check of every later spoken wording for usage rights.
- **Companion kit.** The kit for podcast platforms (`publish_kit.py`, D-139) names sources only by title, authors,
  year and web address; an idea source and a local file path never appear in it. It publishes nothing: uploading it
  with an episode is your step, and so is the rights check above. The whole podcast's kit (D-165) follows the same
  rule for its source list, and its `transcript.md` holds the complete approved text of every episode, verbatim quotes
  included; publishing it puts every quote in writing, so the rights check covers the transcript as well.
- **AI marking.** Every exported episode MP3, voice preview and conversation sample says in its tags that it was made
  with AI, and the show notes, the listening sheet and the companion kit's description end with a transparency note
  (EU AI Act Art. 50; why: D-154; tags and wording: [Exports and listening sheet](AUDIO.md#exports-and-listening-sheet)).
  Whether note and tags satisfy Art. 50 for an intended public use has not been checked legally (see V-43).
- Transfers and credential handling: [Trust boundaries](#trust-boundaries), [Secrets and keys](#secrets-and-keys).
- **No PII redaction.** A general detection and removal of personal data is not implemented.

Planned before any public export ([PRODUCT](PRODUCT.md#roadmap)): adjustable rights states and export blocks, and a
redaction stage for personal content before model calls.
