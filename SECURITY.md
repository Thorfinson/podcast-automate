# Security policy

Podcast Automate runs on your own computer: a local web server (the Studio), a command-line tool and worker processes
that call the text, search and speech providers you choose with your own logins and keys. How it is meant to be safe
is described in the design document [docs/SECURITY.md](docs/SECURITY.md); this page says how to report a problem.

## Supported versions

| Version | Supported |
| --- | --- |
| The latest release | Yes |
| `main` | Yes; fixes land here first |
| Older releases | No; please update |

## Reporting a vulnerability

Please report it privately, not in a public issue, a pull request or a discussion:

1. Open the repository's **Security** tab and choose **Report a vulnerability**, or go straight to
   [github.com/Thorfinson/podcast-automate/security/advisories/new](https://github.com/Thorfinson/podcast-automate/security/advisories/new).
2. Describe the problem as below. The report stays visible only to you and the maintainer until an advisory is
   published.

There is no e-mail address for reports; GitHub's private vulnerability reporting is the only channel.

## What is in scope

- **The Studio server** (`pla studio`): the address filter, the `Host`, `Origin` and `Sec-Fetch-Site` checks, the
  session token, the Content Security Policy, uploads, downloads and every path it reads or serves.
- **LAN mode** (`pla studio --lan`): a device outside your private network reaching the Studio, or a web page steering
  it, locally or in the home network.
- **Key handling**: an OpenRouter, Google, Anthropic or Perplexity key, or a subscription login, ending up anywhere it
  should not: in a prompt, a project or run file, a log, a trace, a process argument, browser storage, a request to
  another host, or a call of another provider.
- **Source fetching**: a source address or redirect that reaches a private network address, a service key that
  follows a redirect to another host, and parsing of untrusted downloaded documents (PDF, HTML).
- **Local files**: a path that leaves the project folder, through `local_sources`, uploads or artifact paths.
- **Fetched content steering the tools**: text in a fetched source that makes the application leak a key or a local
  file, or run a command. Wrong or slanted episode content alone is a quality bug; please use the bug report form.
- **Release artifacts**: the published package and the release workflow.

Out of scope: vulnerabilities in the providers' own services and CLIs (Claude Code, Codex, OpenRouter, Google,
Perplexity; please report those to their vendors), and setups the docs advise against, such as exposing the Studio to
the internet or to an untrusted network: it serves plain HTTP without a login by design.

## What to include

- The version (`pla --version`) or commit, your operating system and Python version.
- How the Studio or CLI was started (local or `--lan`) and which providers were selected.
- Steps to reproduce, ideally a minimal proof of concept, and what an attacker gains.
- Never a real key: use a made-up value such as `sk-or-v1-test-only-0000`.

## What to expect

This is a personal open-source project without a security team or bug bounty, so the following are aims, not
guarantees:

- an acknowledgement within about a week;
- an assessment, and for a confirmed problem a fix on `main` and in the next release;
- a published GitHub security advisory once the fix is out, crediting you if you wish.

Please give the fix a reasonable time before you disclose details publicly; the advisory is the place to agree on a
date.
