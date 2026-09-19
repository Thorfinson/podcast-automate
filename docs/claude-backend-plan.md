# Implementierungsplan: Claude Code als zweiter Abo-Anbieter neben Codex

Stand: 19.09.2026. Ziel: Die Textpipeline kann jeden Modellaufruf wahlweise über die Codex CLI (ChatGPT-Abo, GPT-6 Astra) oder über die Claude Code CLI (Claude-Max-Abo, Claude Opus 5) ausführen. Vor jedem Aufruf wird geprüft, welches Abo noch Kontingent hat. Haben beide Kontingent, wird Codex verwendet. Hat nur eines Kontingent, wird dieses verwendet. Hat keines Kontingent, pausiert der Lauf mit `waiting_for_quota` und nennt den frühesten Reset-Zeitpunkt.

## 1. Verifizierte Ausgangslage

Alle Angaben wurden am 19.09.2026 auf dem Zielrechner geprüft, nicht aus der Dokumentation übernommen.

**Claude Code CLI 2.1.92** (`claude`, im PATH):

- Nichtinteraktiver Aufruf: `claude -p --output-format json --json-schema '<Schema>' --model claude-opus-5 --effort <low|medium|high|max> --tools "" --permission-mode dontAsk --no-session-persistence --disable-slash-commands --strict-mcp-config --setting-sources "" --max-budget-usd <n>`; der Prompt wird über stdin übergeben.
- Antwortumschlag (eine JSON-Zeile): `type: "result"`, `subtype: "success"`, `is_error`, `num_turns` (mit Schema 2), `stop_reason`, `structured_output` (schemakonformes Objekt), `usage` mit `input_tokens`, `output_tokens`, `cache_read_input_tokens`, `cache_creation_input_tokens` und `server_tool_use.web_search_requests`, `modelUsage["claude-opus-5"]` mit `costUSD`, `contextWindow` 200000 und `maxOutputTokens` 32000, `total_cost_usd`, `terminal_reason`, `permission_denials`, `session_id`.
- Anmeldung: `claude auth status --json` liefert `loggedIn`, `authMethod: "claude.ai"`, `apiProvider: "firstParty"`, `subscriptionType: "max"`. Zugangsdaten liegen in `~/.claude/.credentials.json`; `ANTHROPIC_API_KEY` und `ANTHROPIC_AUTH_TOKEN` haben Vorrang vor dem Abo und würden über die API abrechnen.
- Kontingent: Es gibt keinen nichtinteraktiven Weg, das verbleibende 5-Stunden- oder Wochenkontingent zu lesen. Ein ausgeschöpftes Limit erscheint erst als Fehlerergebnis (`is_error: true`, Text der Form „You've hit your … limit“). Opus hat ein eigenes Limit neben Sonnet/Haiku.
- Einschränkungen dieser Version: `--bare` deaktiviert die Abo-Anmeldung vollständig (nur API-Key) und ist damit unbrauchbar. `--effort` kennt `low`, `medium`, `high`, `max`; kein `xhigh`. Es gibt kein `--max-turns`. `--json-schema` nimmt das Schema als Argument; das größte strikte Schema des Projekts (`ResearchDossier`) hat 6,6 K Zeichen, die Windows-Grenze liegt bei 32 767 Zeichen je Befehlszeile.
- Ein Probeaufruf mit obigen Flags kostete laut Umschlag 0,07 USD Gegenwert, davon rund 9 K gecachte Systemprompt-Tokens von Claude Code selbst.

**Codex CLI 0.154.0-alpha.6.2** (VS-Code-Erweiterung, wird von `codex.executable_command` gefunden; Anmeldung „Logged in using ChatGPT“, `planType: "prolite"`):

- Der App-Server liefert per JSON-RPC `account/rateLimits/read` ohne Modellaufruf: `rateLimits.primary` und `.secondary` je mit `usedPercent`, `windowDurationMins`, `resetsAt` (Unix-Sekunden), dazu `rateLimitReachedType`, `credits` (`hasCredits`, `balance`), `planType`, `spendControlReached`, `rateLimitsByLimitId` (mehrere Limits, hier `codex`). `account/read` liefert `type: "chatgpt"` und `planType`; `account/usage/read` tägliche Tokenzähler.
- Aktueller Zustand: `primary.usedPercent: 100`, Wochenfenster (10080 Minuten), `resetsAt` 1790109078 (22.09.2026, 22:31 MESZ), `rateLimitReachedType: "rate_limit_reached"`, keine Credits. Damit ist Codex bis dahin nicht nutzbar; genau diesen Fall soll die Umschaltung abdecken.
- Die bestehende Anbindung (`codex_stream.run_app_server`) startet je Aufruf einen eigenen App-Server über `initialize`, `config/read`, `thread/start`, `turn/start`; die Kontingentabfrage kann dieselbe Verbindung vor `thread/start` nutzen.

## 2. Entscheidungen

1. **Zwei konkrete Anbieter, eine Auswahlregel.** Neue Anbieterkennung `claude_code` neben `codex_cli` und `openrouter`. Zusätzlich die Auswahlregel `auto`: Codex, wenn Codex Kontingent hat; sonst Claude, wenn Claude nicht als erschöpft vermerkt ist; sonst Pause. `auto` wird das Standardpreset für neue Projekte; bestehende Projekte behalten ihre gespeicherte Auswahl.
2. **Kontingent-Definition.** Codex hat Kontingent, wenn alle vorhandenen Fenster `usedPercent < 100` haben und `rateLimitReachedType` leer ist. Bezahlte Credits werden nicht automatisch verbraucht (`credits` wird ignoriert; ein Projektschalter kann das später erlauben). Claude hat Kontingent, wenn kein Eintrag im lokalen Kontingentprotokoll aktiv ist; der Eintrag entsteht beim ersten Limitfehler und trägt den aus der Fehlermeldung gelesenen Reset-Zeitpunkt oder, falls keiner enthalten ist, eine konservative Sperre (5 Stunden bei Sitzungs- oder Opus-Limit, bis zum nächsten Montag bei Wochenlimit, 30 Minuten bei unklarer Meldung).
3. **Umschaltung je Aufruf, nicht je Lauf.** Die Auswahl wird vor jedem Modellaufruf getroffen, weil sich das Kontingent während eines Laufs ändert. Ein Lauf mit `auto` speichert beide Modellkonfigurationen in `text_generation`; welcher Anbieter einen Aufruf tatsächlich bedient hat, steht in `runs/<run_id>/calls/call_NNN/metadata.json`. Gespeicherte Zwischenstände bleiben gültig, weil sie an den Prompttext und die Eingaben gebunden sind, nicht an den Anbieter.
4. **Kein API-Key-Betrieb für Claude.** Die Umgebung des Kindprozesses entfernt `ANTHROPIC_API_KEY`, `ANTHROPIC_AUTH_TOKEN`, `OPENAI_API_KEY`, `CODEX_API_KEY` und `OPENROUTER_API_KEY`. Der Adapter verlangt `authMethod: "claude.ai"`; alles andere wird wie bei Codex als `subscription_required` abgewiesen.
5. **Recherche zunächst weiter über Codex, Claude-Recherche als eigene Phase.** Claude Code kann mit `--tools "WebSearch,WebFetch"` live suchen und meldet Suchereignisse (`usage.server_tool_use.web_search_requests`, im Stream `tool_use`-Ereignisse). Die Belegpflicht der Recherche (beobachtete Suchereignisse) lässt sich damit abbilden, wird aber erst in Phase 4 umgesetzt.
6. **Effort-Zuordnung.** Codex `xhigh` entspricht bei Claude `high` (Standard); `max` bleibt eine ausdrückliche Wahl. Die Zuordnung steht als Tabelle im Katalog, nicht als stille Umrechnung.

## 3. Bausteine

### 3.1 `claude_code.py`: Adapter mit demselben Vertrag wie `CodexAdapter`

`ClaudeCodeAdapter(settings, *, model, reasoning_effort, cancel_check)` mit `structured(prompt, output_type, directory, *, prompt_version, search=False) -> (output, metadata)`.

- Befehl: `claude -p --output-format stream-json --verbose --json-schema <kompaktes striktes Schema> --model <id> --effort <stufe> --permission-mode dontAsk --no-session-persistence --disable-slash-commands --strict-mcp-config --setting-sources "" --max-budget-usd <Deckel> --tools ""` (bei `search=True`: `--tools "WebSearch,WebFetch" --allowedTools "WebSearch,WebFetch"`). Prompt über stdin, Arbeitsverzeichnis ist das Aufrufverzeichnis (leer, damit kein Projekt-`CLAUDE.md` gelesen wird). Phase 0 klärt, ob `--output-format stream-json` den Umschlag mit `structured_output` als letzte Zeile liefert; sonst Phase 1 mit `--output-format json` und ohne Live-Anzeige.
- Schema: `openrouter.strict_schema` wiederverwenden; bei mehr als 30 000 Zeichen mit `AppError(code="invalid_output_schema")` abbrechen, statt einen kaputten Befehl zu starten.
- Umgebung: `subscription_environment()` aus `codex.py` erweitern (zusätzlich `ANTHROPIC_API_KEY`, `ANTHROPIC_AUTH_TOKEN` entfernen) und `DISABLE_TELEMETRY=1`, `DISABLE_ERROR_REPORTING=1`, `CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1` setzen. `CLAUDE_CONFIG_DIR` bleibt unverändert, weil dort die Abo-Zugangsdaten liegen.
- Prozess: `process.run_process` mit `on_stdout_line`, `cancel_check` und `text_timeout_seconds` wiederverwenden; Stop über `stop_process_tree`.
- Ergebnisprüfung: `type == "result"` und `subtype == "success"` und `is_error == false`; `structured_output` mit `output_type.model_validate` prüfen; fehlt es, `invalid_model_output`. Bei `search=True` gilt der Aufruf nur mit `web_search_requests > 0` oder beobachteten `tool_use`-Ereignissen für `WebSearch` als Recherche (`search_not_observed`, wie heute bei Codex).
- Fehlerklassen (`classify_claude_failure`): Ergebnistext oder `subtype` mit „You've hit your“, „limit“, „rate limit“, „429“ → `AppError(code="claude_quota_exhausted", status="waiting_for_quota")` mit geparstem Reset-Zeitpunkt; „not logged in“, „authentication“, „401“ → `authentication_required`; `--max-budget-usd` erreicht → `claude_budget_cap`; alles andere `claude_failed`. Rohtexte werden wie bei Codex nicht gespeichert, nur `failure.json` mit Code und Version.
- Metadaten: `provider: "claude_code"`, `auth_mode: "claude.ai"`, `requested_model`, `actual_model` (Schlüssel aus `modelUsage`), `requested_reasoning_effort`, `cli_version` (`claude --version`), `usage` (Eingabe, Ausgabe, Cache), `reported_cost_usd` mit dem Hinweis, dass es sich um einen Gegenwert und keine Rechnung handelt, `research_performed`, `web_search_requests`, `observed_search_queries` (aus `tool_use`-Eingaben), `num_turns`, `session_id` nicht speichern.
- Live-Anzeige: `CallActivity` erhält `observe_claude(line)` für Stream-Ereignisse: `assistant`-Nachrichten mit `text`-Blöcken in den Trace, `tool_use` mit Name `WebSearch` als „Websuche gestartet: <query>“, `system`-Ereignisse mit `api_retry` als Diagnose `rate_limit`/`retry`. Reasoning-Zusammenfassungen liefert die CLI nicht; die Anzeige zeigt dann nur Textfortschritt.
- Systemprompt: In Phase 3 prüfen, ob `--system-prompt` mit einem kurzen festen Text den rund 9 K Token großen Standard-Systemprompt ersetzt und ob dann `~/.claude/CLAUDE.md` weiterhin eingelesen wird. Beides beeinflusst Kontingentverbrauch und Hermetik.

### 3.2 `subscriptions.py`: Kontingentabfrage und Auswahlregel

- `codex_rate_limits(settings) -> dict | None`: startet den App-Server, sendet `initialize`, `initialized`, `account/read` und `account/rateLimits/read`, beendet den Prozess (Zeitlimit 20 Sekunden). Rückgabe normalisiert: `{"provider": "codex_cli", "available": bool, "plan": "prolite", "windows": [{"name": "primary", "used_percent": 100, "window_minutes": 10080, "resets_at": "2026-09-22T20:31:18Z"}], "reason": "rate_limit_reached"}`. `None` bei nicht installierter oder nicht angemeldeter CLI. Ergebnis wird mit Zeitstempel zwischengespeichert und höchstens alle zwei Minuten neu gelesen; nach jedem Kontingentfehler sofort neu.
- `claude_login(settings) -> dict | None`: `claude auth status --json` mit Zeitlimit 20 Sekunden; nur `loggedIn`, `authMethod`, `subscriptionType` werden übernommen, E-Mail und IDs nicht gespeichert.
- `claude_quota_state() -> dict`: liest das Kontingentprotokoll; ein Eintrag `{"blocked_until": "<ISO>", "reason": "opus_limit", "detected_at": ..., "message_excerpt": ...}` gilt, solange `blocked_until` in der Zukunft liegt.
- Speicherort des Protokolls: `~/.podcast-automate/subscriptions.json` (kontoweit, nicht projektweit), atomar geschrieben über `storage.atomic_text`, nie Zugangsdaten. Auf Windows unter `%USERPROFILE%`.
- `choose_subscription(settings, *, prefer="codex_cli", require=None) -> Choice`: liefert `provider`, `model`, `reasoning_effort`, `reason` (`"codex_available"`, `"codex_exhausted_until …"`, `"claude_blocked_until …"`), `snapshots`. Regel in dieser Reihenfolge: Codex verfügbar → Codex; sonst Claude nicht gesperrt → Claude; sonst `AppError(code="subscriptions_exhausted", status="waiting_for_quota")` mit dem früheren Reset. `require="claude_code"` oder `require="codex_cli"` überspringt die Regel für feste Anbieter, prüft aber weiterhin die Anmeldung.
- `record_quota_failure(provider, error)`: Codex → Cache verwerfen und neu lesen; Claude → Protokolleintrag schreiben.

### 3.3 Auswahl im Lauf

- `text_settings`: `CLAUDE_MODELS = {"claude-opus-5": "Claude Opus 5"}`, `CLAUDE_EFFORTS = ("low", "medium", "high", "max")`, `EFFORT_EQUIVALENTS = {"xhigh": "high", ...}`, Presets `claude_opus_sub` („Opus 5 · Claude-Abo“) und `auto_subscriptions` („Automatisch · Codex, sonst Claude“). `validate_reasoning` erhält den Anbieter `claude_code`; `provider_model` normalisiert `opus` → `claude-opus-5`.
- `text_generation` (in `script_request.json`, `research_request.json`, `inputs.json`): für feste Anbieter unverändert; für `auto` die Form `{"provider": "auto", "prefer": "codex_cli", "candidates": {"codex_cli": {"model": "gpt-6-astra", "reasoning_effort": "xhigh"}, "claude_code": {"model": "claude-opus-5", "reasoning_effort": "high"}}, "adapter_versions": {"claude_code": "claude_code.v1"}}`. Diese Form geht in den `input_hash`; Fortsetzen verwendet sie unverändert. `text_generation_settings` in `scripting.py` und die Auswahl in `research.py` akzeptieren die neue Form und lehnen wie bisher jede Änderung beim Fortsetzen ab.
- `ScriptRun.invoke` und die `invoke`-Funktion in `run_research` erhalten einen `AdapterPool`: `pool.adapter(search=...)` ruft `choose_subscription` auf (bei festem Anbieter ohne Abfrage), liefert den passenden Adapter und schreibt die Entscheidung als `provider_choice.json` ins Aufrufverzeichnis. Bei `waiting_for_quota` aus einem Adapter im Modus `auto`: `record_quota_failure`, erneut wählen, denselben Aufruf einmal mit dem anderen Anbieter wiederholen (gleiches Aufrufverzeichnis, `provider_switch.json` mit Grund). Bleibt keiner, wird der ursprüngliche Fehler weitergereicht; `execute_stages` speichert wie heute `waiting_for_quota`.
- `build_adapter` in `scripting.py`, `run_research` und `studio_worker.perform` (Assistent) bauen den Pool statt eines einzelnen Adapters. Der Nachrecherche-Pfad in `ScriptRun.invoke` (heute: OpenRouter-Lauf nutzt Codex für `research=True`) bleibt bis Phase 4 auf Codex.
- `status_summary` (Statusberichte alle drei Minuten) nutzt denselben Pool mit dem günstigen Modell des gewählten Anbieters (`gpt-5.6-luna` bzw. `claude-haiku-4-5`); Statusaufrufe zählen weiterhin nicht zum Produktionsbudget.
- `run_budget`/`script_budget` bleiben unverändert: das Aufruflimit zählt Aufrufe, nicht Anbieter.

### 3.4 Studio, CLI, Doctor

- `TextChoice.provider` erlaubt `claude_code` und `auto`; `kwargs()` liefert für `auto` die Kandidatenform. Der Auftragsassistent bekommt den erweiterten Katalog und den Satz, dass Claude über das Claude-Max-Abo läuft, keine API-Kosten verursacht und automatisch einspringt, wenn Codex leer ist.
- UI (`app.js`): Preset-Knöpfe „Opus 5 · Claude-Abo“ und „Automatisch · Codex, sonst Claude“; Zusammenfassung zeigt bei `auto` beide Modelle; im Auftragsstatus eine Zeile „Aktueller Anbieter: Codex (Wochenfenster 62 %) · Claude bereit“ aus `provider_choice.json` des letzten Aufrufs; Kontingentmeldungen nennen den Reset-Zeitpunkt.
- CLI: `--backend claude_code|auto`; `--model` und `--reasoning-effort` gelten für den festen Anbieter, bei `auto` die Katalogstandards. `pla doctor` erhält die Prüfungen `claude_login` (Version, Anmeldung, Abo-Typ) und `subscription_quota` (Codex-Fenster in Prozent mit Reset, Claude-Sperre falls aktiv); beide blockieren `ready` nicht, wenn wenigstens ein Anbieter nutzbar ist.
- Neues Kommando `pla quota [--json]`: gibt dieselbe Übersicht ohne Projekt aus und ist die schnellste Diagnose, wenn ein Lauf pausiert.

## 4. Phasen

**Phase 0: Spike (halber Tag).** Skript im Scratch-Bereich, das (a) `stream-json` mit `--json-schema` prüft und die letzte Zeile mit `structured_output` bestätigt, (b) einen absichtlich zu großen `--max-budget-usd 0.001`-Aufruf provoziert und den Fehlerumschlag festhält, (c) prüft, ob `~/.claude/CLAUDE.md` in einen Aufruf mit `--setting-sources ""` einfließt (Markerzeile), (d) `--system-prompt` mit Minimaltext misst. Ergebnis als Tabelle in diesem Dokument nachtragen; nur wenn (a) scheitert, startet Phase 1 mit `--output-format json`.

**Phase 1: Adapter und Kontingentmodul (1 bis 2 Tage).** `claude_code.py`, `subscriptions.py`, Erweiterung von `subscription_environment`, `CallActivity.observe_claude`, Katalogeinträge in `text_settings.py`, `pla quota`, Doctor-Prüfungen. Tests mit einer gefälschten `claude`-Programmdatei (Python-Skript wie `SERVER` in `tests/test_codex_stream.py`), die Erfolg, Schemaverstoß, Limitfehler mit Reset-Zeit, Anmeldefehler und Suchereignisse simuliert; gefälschter App-Server für `account/rateLimits/read`; tabellengetriebene Tests der Auswahlregel (beide verfügbar, nur Codex, nur Claude, keiner, Codex-Cache abgelaufen). Abnahme: `pla text-probe --backend claude_code` liefert eine validierte Probe mit Metadaten; `pla quota` zeigt beide Abos.

**Phase 2: Auswahl im Skriptlauf (1 bis 2 Tage).** `AdapterPool`, `auto`-Form von `text_generation`, Umschaltung je Aufruf mit `provider_switch.json`, Statusmonitor. Tests: ein Skriptlauf, dessen Codex-Fake beim dritten Aufruf ein Limit meldet, wechselt ohne Wiederholung fertiger Stufen zu Claude und wird abgeschlossen; Fortsetzen mit geänderter Kandidatenform wird als `inputs_changed` abgewiesen; beide Fakes leer → `waiting_for_quota` mit dem früheren Reset; Aufrufzähler und Budgetprognose unverändert. Abnahme: `pla script --backend auto` auf dem Pilotprojekt mit Codex bei 100 % läuft vollständig über Claude.

**Phase 3: Studio (1 Tag).** Presets, Assistentenkatalog, Anbieterzeile im Status, Doctor-Anzeige im Studio, `docs/studio.md`, README-Anbieterabschnitt, `docs/windows-quickstart.md` (Claude-Anmeldung mit `claude auth login`, Prüfung mit `claude auth status --json`). UI-Tests in `tests/studio_ui.test.cjs` für Preset-Rendering und Statuszeile.

**Phase 4: Recherche über Claude (1 bis 2 Tage, optional).** `search=True` im Claude-Adapter mit `WebSearch`/`WebFetch`, Nachweis über `web_search_requests` und `tool_use`-Ereignisse, `observed_search_queries` aus den Werkzeugeingaben, `search_events.json` im heutigen Format. `run_research` und der Nachrecherche-Pfad nutzen den Pool. Abnahme: ein Recherchelauf ohne Codex-Kontingent erzeugt ein Dossier mit belegten Suchereignissen; `research_performed` bleibt bei fehlenden Ereignissen `false` und der Lauf wird wie heute abgewiesen.

## 5. Risiken und offene Punkte

- **Claude-Kontingent ist nicht vorab lesbar.** Die Regel „beide haben Kontingent“ ist für Claude eine Annahme bis zum ersten Fehler. Der Preis ist ein einzelner fehlgeschlagener Aufruf, danach greift die Sperre; die Meldung im Studio muss das erklären.
- **Doppelter Systemprompt-Anteil.** Claude Code hängt seinen eigenen Systemprompt an (rund 9 K Token, meist aus dem Cache). Phase 0 misst, ob `--system-prompt` den Anteil senkt; sonst ist er als Kontingentverbrauch je Aufruf einzupreisen.
- **Zwei Modelle in einem Lauf.** Bei `auto` können Entwurf und Prüfung von verschiedenen Modellen stammen. Die Prüfungen sind darauf ausgelegt (unabhängige Prüfer), aber `reports/script_quality.yaml` muss je Aufruf den tatsächlichen Anbieter ausweisen, damit ein Ergebnis nachvollziehbar bleibt.
- **Versionsdrift der CLI.** Flags wie `--setting-sources` und der Umschlag sind Stand 2.1.92. Der Adapter prüft `claude --version` gegen eine Mindestversion und speichert sie je Aufruf; Doctor warnt bei Abweichung.
- **Ausgabelimit 32 000 Tokens je Antwort.** Reicht für Skripte (etwa 4000 Wörter) und Dossiers; `finish`-Grenzen werden wie bei OpenRouter als `invalid_model_output` behandelt, nicht still übernommen.
- **Credits.** Codex bietet bezahlte Credits nach Erreichen des Limits an; der Plan ignoriert sie bewusst. Ein späterer Projektschalter kann sie freigeben, dann muss `choose_subscription` `credits.hasCredits` berücksichtigen.
- **Hermetik ohne `--bare`.** Ob `~/.claude/CLAUDE.md` und Nutzerhooks bei `--setting-sources ""` wirklich außen vor bleiben, entscheidet Phase 0. Fällt der Test negativ aus, bleibt als Ausweg ein eigener `CLAUDE_CONFIG_DIR` mit kopierten Zugangsdaten, was zusätzliche Sorgfalt bei Dateirechten verlangt.

## 6. Betroffene Dateien

Neu: `src/podcast_automate/claude_code.py`, `src/podcast_automate/subscriptions.py`, `tests/test_claude_code.py`, `tests/test_subscriptions.py`, `tests/test_provider_pool.py`.

Geändert: `codex.py` (`subscription_environment`), `codex_stream.py` (Kontingentabfrage als eigene Funktion), `call_activity.py`, `text_settings.py`, `scripting.py` (`text_generation_settings`, `build_adapter`), `script_pipeline.py` (`invoke` über Pool), `research.py` (Auswahl und `invoke`), `studio.py` (`TextChoice`, Bootstrap-Katalog), `studio_worker.py` (Assistent, Statusmonitor), `status_summary.py`, `doctor.py`, `cli.py` (`--backend`, `pla quota`), `web/app.js`, `docs/studio.md`, `docs/windows-quickstart.md`, `docs/scripts.md`, `README.md`, `SPEC.md` (§9 Textbackends).

## 7. Umsetzungsstand

Stand: 19.09.2026, alle vier Phasen umgesetzt; Tests in `tests/test_claude_code.py`, `tests/test_subscriptions.py` und `tests/test_provider_pool.py`.

**Phase 0, gemessen mit Claude Code 2.1.92 auf dem Zielrechner:**

| Frage | Ergebnis |
| --- | --- |
| (a) `--output-format stream-json` mit `--json-schema` | Ja. Der Stream endet mit einer `result`-Zeile, die `structured_output` trägt. Die Antwort entsteht über einen Werkzeugaufruf `StructuredOutput`; `num_turns` ist 2. Mit `--include-partial-messages` kommen `input_json_delta`-Teile der Antwort für die Live-Anzeige. |
| (b) `--max-budget-usd 0.001` | Umschlag `subtype: "error_max_budget_usd"`, `is_error: true`, `errors: ["Reached maximum budget ($0.001)"]`. Der erste Modellaufruf findet trotzdem statt (0,035 USD Gegenwert); die Grenze wirkt erst danach. |
| (c) Hermetik mit `--setting-sources ""` | Eine `CLAUDE.md` mit Markerzeile im Arbeitsverzeichnis wurde nicht eingelesen; die Antwort meldete „no marker“. Persönliche Einstellungen (`~/.claude/settings.json` mit anderem Modell) griffen nicht. |
| (d) `--system-prompt` mit Minimaltext | Ersetzt den CLI-Systemprompt vollständig: 849 statt 8 841 neu gecachte und 691 statt 8 658 gelesene Tokens, 0,009 statt 0,064 USD Gegenwert je Aufruf. Der Adapter verwendet deshalb einen kurzen festen Systemprompt. |
| Zusatzbefund | Der Stream enthält ein Ereignis `rate_limit_event` mit `status`, `resetsAt` und `rateLimitType` (`five_hour`). Erfolgreiche Aufrufe speichern es als `rate_limit` in `metadata.json` und im Kontingentprotokoll; ein abgelehnter Status liefert den exakten Reset-Zeitpunkt für die Sperre. |

**Abweichungen vom Plan:**

- Kein neues Feld in `RuntimeSettings`: Ein zusätzliches Feld hätte den Projekt-Hash aller bestehenden Projekte verändert und deren Fortsetzung blockiert. `claude` wird über PATH, `~/.local/bin` und npm-Shims gefunden.
- `auto` ist die Vorauswahl neuer Studio-Projekte (`text_defaults` im Bootstrap) und der erste Preset-Knopf; der programmatische Standard von `TextChoice` und `text_backend` in `project.yaml` bleibt `codex_cli`, damit bestehende Projekte und die CLI ohne Option unverändert laufen.
- Sind nach einem Kontingentfehler beide Abos leer, meldet der Lauf `subscriptions_exhausted` mit beiden Reset-Zeitpunkten statt des ursprünglichen Adapterfehlers; der ursprüngliche Fehler bleibt als Ursache verkettet. Ist der andere Anbieter gar nicht nutzbar (nicht installiert oder nicht angemeldet), bleibt der ursprüngliche Kontingentfehler erhalten.
- Bei festem Anbieter liest die Anbindung das Codex-Kontingent nach einem Fehler nicht neu (das würde nur der Regel dienen); eine Claude-Sperre wird in jedem Modus vermerkt.
- Phase 4 ist umgesetzt: Recherche und Nachrecherche folgen dem gewählten Anbieter; OpenRouter-Läufe recherchieren über die Abos nach der automatischen Regel.
- Der Speicherort des Kontingentprotokolls lässt sich mit `PLA_SUBSCRIPTIONS_STORE` überschreiben; die Tests nutzen das, damit sie nie in `~/.podcast-automate` schreiben.
