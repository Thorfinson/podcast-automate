---
title: Security
doc_type: security
status: current
last_reviewed: 2026-10-04
covers:
  - src/podcast_automate/studio.py
  - src/podcast_automate/studio_worker.py
  - src/podcast_automate/scripting.py
  - src/podcast_automate/research.py
  - src/podcast_automate/sources.py
  - src/podcast_automate/downloads.py
  - src/podcast_automate/storage.py
  - src/podcast_automate/attachments.py
  - src/podcast_automate/openrouter.py
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
project files to a hosting service.

## Trust boundaries

Project files, original sources and research contexts are stored locally. Selected content leaves the computer only for
the processing you commissioned:

| What leaves the computer | To whom | When |
| --- | --- | --- |
| Prompts with the selected content: brief, chat messages, text of attachments and provided works, source sections, plans, scripts and reviews | OpenAI (Codex CLI, your ChatGPT subscription) or Anthropic (Claude Code CLI, your claude.ai login, Claude Max) | every text call the selection routes to that subscription, live web research included |
| The same prompts, except web research | OpenRouter and the model provider it routes to, billed to your API credit | only with an OpenRouter text model |
| The spoken text of approved scripts and of voice samples | OpenRouter (Gemini TTS) | only for a Gemini recording or a new Gemini voice sample |
| Reported gaps and corpus sections of the research | OpenRouter (Jev, TypeSafe) | only when the gap probe with Jev is on ([RESEARCH](RESEARCH.md#gap-probe)) |
| Requests for source documents; title or DOI lookups for free copies | the public servers of the sources; OpenAlex, Semantic Scholar and Europe PMC, and Unpaywall or CORE only when you set their contact address or key | during research ([RESEARCH](RESEARCH.md#pipeline)) |
| Quota and login queries | the Codex app server (`account/rateLimits/read`) and `claude auth status` | before model calls, `pla doctor`, `pla quota` |

What never leaves the computer, or never gets in:

- **Keys never go into prompts.** A chat message or upload (name or text) containing the stored session key or an
  OpenRouter key (`sk-or-…`), and an OpenRouter prompt or Gemini text containing the key in use, are refused with
  `credential_in_prompt`; the editorial partner never sees the key.
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
- API-key logins are refused, because they would bill per call (why: D-109): Codex must be logged in with ChatGPT,
  Claude Code must report `authMethod: "claude.ai"`; anything else stops as `subscription_required`.
- The CLIs are started without `OPENAI_API_KEY`, `CODEX_API_KEY`, `OPENROUTER_API_KEY`, `ANTHROPIC_API_KEY` and
  `ANTHROPIC_AUTH_TOKEN` (`codex.subscription_environment`), so they can only use the subscription login.
  `CLAUDE_CONFIG_DIR` stays untouched, because it holds the Claude login.
- The quota store `~/.podcast-automate/subscriptions.json` never holds credentials. The login check keeps only whether
  you are logged in, the login method, the subscription type and the CLI version, no e-mail address or IDs.

### OpenRouter key in the Studio

- The key is entered on the **„Einstellungen“** (settings) page ([STUDIO](STUDIO.md#settings-page)) or in a hold
  card that asks for it, and serves OpenRouter text, Gemini audio and Jev.
- It stays in the local Studio server's memory and reaches a worker through its standard input, not through process
  arguments; it is stored neither in project files nor in browser storage.
- After a Studio restart it must be entered again, unless the server has `OPENROUTER_API_KEY` in its environment.
  **„Sitzungs-Key entfernen“** (remove session key) removes only the key entered in the Studio, not that one.
- The key can be exchanged at any time; a rotated key needs no new run.
- A worker hands the key on only where OpenRouter is used: to a text job while it works with OpenRouter, to runs that
  use the Jev gap probe, to Gemini recordings and to new Gemini voice samples.

### OpenRouter key on the command line

- `--api-key` without a value asks for the key hidden in the terminal. A key given as a value
  (`--api-key "YOUR_KEY"`) is refused unread with `invalid_request`, because it would stand in the process list and
  the shell history. (why: D-112)
- The alternative is `OPENROUTER_API_KEY`, which the OpenRouter adapter reads; a key entered hidden takes precedence.
  Without a secure terminal input the hidden prompt aborts instead of reading the key visibly; use the variable then.
- Keys do not belong in `project.yaml`. The application writes them into no prompt, project or answer file.

### Keys on the wire

- OpenRouter text, Gemini speech and Jev requests keep the key in memory and in the `Authorization` header only, and
  follow no redirects (`openrouter.NoRedirect`).
- A source fetch that carries a service key (CORE's free key, `PLA_CORE_API_KEY`) follows a redirect only on the same
  host and never from https to http (`sources.PublicRedirect`); otherwise it aborts with `source_download_failed`, so
  the key never reaches another server. (why: D-113)

### Credentials in traces, diagnostics and logs

- Before anything is stored, `model_trace.redact` removes the known keys and anything that looks like a credential
  (`sk-…`, `sess-…`, `Bearer …`, values after `api_key`, `access_token`, `token`, `password` or `secret`): in the live
  trace `runs/<run_id>/model_trace.json` and in the tracebacks of failed stages and workers (`logs.record_failure`:
  `runs/<run_id>/failures/`, `<project>/studio/failures/`). Error messages and diagnostic files shown in the Studio
  replace its stored key with „[Key verborgen]“ (key hidden).
- `calls/call_*/diagnostics.json` keeps only sanitised technical diagnostics, no raw error messages, prompts, tool
  outputs or credentials. The one exception is a Codex failure the adapter cannot name (`codex_failed`): its
  `failure.json` and stop message carry Codex's own short reason, redacted by `model_trace.redact` and cut to 300
  characters (`codex.provider_message`; why: D-129). Likewise an OpenRouter refusal (HTTP 403, text and Gemini
  speech) keeps OpenRouter's own short reason in its stop message, without the key and at most 300 characters
  (`openrouter.refusal_reason`; why: D-132); `work_context.json` keeps a bounded selection of a call's inputs, without
  full prompts or source texts.
- The rotating log files `.studio/studio.log`, `<project>/studio/worker.log` and `<project>/logs/pla.log` (tracebacks
  included) and the terminal output of every `pla` process pass through `logs.scrub`: the keys the process knows (the
  Studio's stored key, a worker's key, a key typed after `--api-key`, `OPENROUTER_API_KEY`; `logs.add_secret`) are
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
- Transfers and credential handling: [Trust boundaries](#trust-boundaries), [Secrets and keys](#secrets-and-keys).
- **No PII redaction.** A general detection and removal of personal data is not implemented.

Planned before any public export ([PRODUCT](PRODUCT.md#roadmap)): adjustable rights states and export blocks, a
redaction stage for personal content before model calls, and the transparency note in the exports.
