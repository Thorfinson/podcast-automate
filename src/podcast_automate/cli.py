from __future__ import annotations

import argparse
import getpass
import json
import os
import re
import warnings
from pathlib import Path

from pydantic import ValidationError

from . import __version__
from .doctor import inspect
from .errors import AppError
from .episode_audio import run_episode_audio
from .logs import add_secret, configure_logging, logger, release_logging
from .models import RuntimeSettings, SCHEMAS, TopicBrief
from .polishing import DialoguePolishReview
from .runner import run_probe, status
from .runner import manifest_path
from .research import run_research
from .research_models import RESEARCH_SCHEMAS
from .script_models import SCRIPT_SCHEMAS
from .series_review import SeriesReview
from .teaching import TEACHING_SCHEMAS
from .scripting import run_script
from .storage import init_project, load_project, read_yaml, write_json
from .platforms import configure_path
from .trial import TRIAL_LIMITS, TRIAL_MINUTES, TRIAL_SUB_QUESTIONS, trial_brief, trial_facts

# pla approve --text-switch VALUE -> run_budget.TEXT_SWITCHES
SWITCH_OPTIONS = {"claude": "claude_first", "astra": "astra_first", "claude-only": "claude", "astra-only": "astra",
                  "openrouter": "openrouter", "claude-api": "claude_api"}


def build_parser() -> argparse.ArgumentParser:
    # The CLI speaks English (D-156): its help, its own messages and the doctor's details. Messages that pipeline
    # modules raise keep their wording; codes and --json keys never change.
    parser = argparse.ArgumentParser(
        prog="pla", description="Podcast Automate: project management and technical probes.")
    parser.add_argument("--version", action="version", version=f"podcast-automate {__version__}")
    commands = parser.add_subparsers(dest="command", required=True)
    studio = commands.add_parser("studio", help="Open the guided Podcast Studio locally in the browser")
    studio.add_argument("workspace", type=Path, nargs="?", default=Path.cwd())
    studio.add_argument("--port", type=int, default=8765)
    studio.add_argument("--no-browser", action="store_true")
    studio.add_argument("--lan", action="store_true",
                        help="Also reachable in the home network, for example from a phone on Wi-Fi; never from the "
                             "internet")
    studio.set_defaults(json_output=False)
    init = commands.add_parser("init", help="Create a personal project")
    init.add_argument("project_dir", type=Path)
    # Required unless --trial, which then takes a narrow sample topic (checked in main).
    init.add_argument("--topic", help="Topic of the series; required unless --trial")
    init.add_argument("--total-minutes", type=float, default=None)
    init.add_argument("--tts-python", help="Python of the separate Qwen environment")
    # A trial project (D-157): the whole pipeline once, small and cheap (trial.py).
    init.add_argument("--trial", action="store_true",
                      help=f"Create a trial project: one episode of at most {TRIAL_MINUTES:g} minutes, a research plan "
                           f"of at most {TRIAL_SUB_QUESTIONS} sub-questions and small limits per run "
                           f"({TRIAL_LIMITS.model_calls} model calls, {TRIAL_LIMITS.search_rounds} search rounds, "
                           f"{TRIAL_LIMITS.sources} sources, a money limit of at most {TRIAL_LIMITS.cost_usd:g} USD "
                           "where one is set); without --topic a narrow sample topic")
    # A new version (D-168): the same inputs, made again by the current pipeline; the old version stays as it was.
    version = commands.add_parser("new-version", help="Start the next version of a project beside it: the same brief, "
                                  "attachments, provided works and choices, no runs; the newest completed research "
                                  "run's sources come along as a starting library")
    version.add_argument("project_dir", type=Path)
    version.add_argument("--to", type=Path, dest="target", help="Folder of the new version (default: beside the old one)")
    doctor = commands.add_parser("doctor", help="Check the installation, the subscription logins (Codex, Claude) and "
                                                "their quota; no model call")
    doctor.add_argument("project_dir", type=Path, nargs="?")
    doctor.add_argument("--skip-tts", action="store_true", help="Skip local Qwen, for example with Gemini audio")
    state = commands.add_parser("status", help="Show progress and errors of the last run")
    state.add_argument("project_dir", type=Path)
    state.add_argument("--run-id")
    text_probe = commands.add_parser("text-probe", help="Structured subscription connection probe (Codex or Claude Code)")
    text_probe.add_argument("project_dir", type=Path)
    text_probe.add_argument("--backend", choices=("codex_cli", "claude_code", "auto"),
                            help="Subscription provider of the probe; default codex_cli")
    quota = commands.add_parser("quota", help="Show the quota of both subscriptions (Codex, Claude) without a model call")
    audio_probe = commands.add_parser("audio-probe", help="Assemble a German or English Qwen voice sample")
    audio_probe.add_argument("project_dir", type=Path)
    audio_probe.add_argument("--approve-audio", action="store_true")
    research = commands.add_parser("research", help="Research live, fetch sources and write an evidenced dossier")
    research.add_argument("project_dir", type=Path)
    research.add_argument("--reuse-sources", metavar="RUN_ID",
                          help="Reuse the stored sources for a new dossier text")
    research.add_argument("--seed-corpus", metavar="RUN_ID",
                          help="Offer the stored sources of an earlier research run as a starting library; chosen ones "
                               "are copied instead of fetched again, and the search still runs anew")
    research.add_argument("--backend", choices=("codex_cli", "claude_code", "claude_api", "openrouter", "auto"),
                          help="Text provider of the research; default codex_cli, auto switches on an empty quota, "
                               "claude_api bills your own Anthropic API key")
    research.add_argument("--api-key", nargs="?", const="", default=None, metavar="",
                          help="Ask hidden for the key of the billed provider (claude_api: Anthropic, openrouter: "
                               "OpenRouter), for this call only; alternatively ANTHROPIC_API_KEY or OPENROUTER_API_KEY")
    research.add_argument("--model", help="Model ID of the fixed subscription provider")
    research.add_argument("--web-search", choices=("model", "perplexity"),
                          help="Web search of a new run: model (the text model's own tools, the default) or perplexity "
                               "(Perplexity Search API, key from PERPLEXITY_API_KEY, billed); required for openrouter")
    research.add_argument("--reasoning-effort", choices=("low", "medium", "high", "xhigh", "max"),
                          help="Reasoning effort for the research model calls of the fixed provider")
    research.add_argument("--approve-plan", action="store_true",
                          help="Approve the research plan automatically, without the approval stop; the projection is "
                               "still written to runs/<run_id>/question_research/plan_projection.json")
    script = commands.add_parser("script", help="Turn a reviewed dossier into a series plan and dialogue scripts")
    script.add_argument("project_dir", type=Path)
    script.add_argument("--episode", help="Write only the chosen episode, for example ep_001")
    script.add_argument("--revise", metavar="EPISODE_ID", help="Revise an existing script with its previous plan")
    script.add_argument("--feedback", default="", help="Editorial feedback for --revise")
    series = commands.add_parser("series-review",
        help="Review the published scripts of a run as a series, without changing the run")
    series.add_argument("project_dir", type=Path)
    series.add_argument("--run", dest="run_id", help="Script run; default the last published one")
    series.add_argument("--backend", choices=("codex_cli", "claude_code", "claude_api", "auto"))
    series.add_argument("--model", help="Model ID of the fixed subscription provider")
    series.add_argument("--reasoning-effort", choices=("low", "medium", "high", "xhigh", "max"))
    audio = commands.add_parser("audio", help="Record a reviewed episode with Qwen and assemble it for the listening "
                                              "review")
    audio.add_argument("project_dir", type=Path)
    audio.add_argument("--episode", required=True)
    audio.add_argument("--approve-audio", action="store_true")
    audio.add_argument("--approval-note", default="", help="Feedback on approving this script state")
    kit = commands.add_parser("publish-kit", help="Write an episode's companion material for Spotify and other "
                                                  "platforms: short and episode description, chapter marks, sources; "
                                                  "or the whole podcast's, with the transcript of every episode; "
                                                  "publishes nothing")
    kit.add_argument("project_dir", type=Path)
    scope = kit.add_mutually_exclusive_group(required=True)
    scope.add_argument("--episode")
    scope.add_argument("--podcast", action="store_true",
                       help="The whole podcast: its description, every source and the transcript of every episode")
    kit.add_argument("--fresh", action="store_true",
                     help="Have the descriptions written anew, even if the script has not changed")
    resume = commands.add_parser("resume", help="Continue an interrupted run without repeating finished work")
    resume.add_argument("project_dir", type=Path)
    resume.add_argument("--run-id")
    resume.add_argument("--approve-audio", action="store_true")
    resume.add_argument("--approval-note", default="", help="Feedback on the renewed audio approval")
    for command in (script, resume):
        command.add_argument("--backend", choices=("codex_cli", "openrouter", "claude_code", "claude_api", "auto"),
                             help="Text provider for scripts and reviews; default codex_cli, auto picks Codex or Claude "
                                  "per call by quota, claude_api bills your own Anthropic API key; on resume the saved "
                                  "provider")
        command.add_argument("--model", help="Model ID of the text provider; required for OpenRouter")
        command.add_argument("--reasoning-effort", choices=("low", "medium", "high", "xhigh", "max"),
                             help="Reasoning effort for text model calls; on resume the saved level stays")
        # The key is asked for hidden, never taken as a value: a command line is visible in the process list
        # and the shell history (2026-10-02). A value given anyway is refused unread (run_command).
        command.add_argument("--api-key", nargs="?", const="", default=None, metavar="",
                             help="Ask hidden for the key of the billed provider, for this call only; alternatively "
                                  "OPENROUTER_API_KEY or ANTHROPIC_API_KEY")
        command.add_argument("--max-output-tokens", type=int,
                             help="OpenRouter output limit per model call; default 32768")
    script.add_argument("--web-search", choices=("model", "perplexity"),
                        help="Web search of a new run's supplementary research: model (default) or perplexity "
                             "(key from PERPLEXITY_API_KEY, billed)")
    script.add_argument("--jev-probe", action="store_true",
                        help="Also run the gap probe with Jev (OpenRouter, key from OPENROUTER_API_KEY): finds "
                             "passages across languages too, about 0.60 USD per run; applies to new script runs")
    approve = commands.add_parser("approve", help="Explicit approval for a run: approve the research plan, a higher "
                                                   "call, search-round or source limit, or accept a blocked "
                                                   "sub-question as a gap")
    approve.add_argument("project_dir", type=Path)
    approve.add_argument("--run-id", help="Default: the project's last run")
    approve.add_argument("--model-calls", type=int, help="New limit for model calls of this run")
    approve.add_argument("--search-rounds", type=int, help="New limit for web search rounds of this run")
    approve.add_argument("--sources", type=int, help="New limit for fetched sources of this run")
    approve.add_argument("--cost-usd", type=float,
                         help="Money limit in USD for a run billed to an API key (required for claude_api and "
                              "OpenRouter); can only rise")
    approve.add_argument("--accept-gap", metavar="TASK_ID",
                         help="Blocked sub-question that stays documented as a gap in the dossier")
    approve.add_argument("--reason", default="", help="Short reason for the accepted gap")
    approve.add_argument("--retry", metavar="TASK_ID",
                         help="Retry a blocked sub-question on the next resume, with the allowance of a new question")
    approve.add_argument("--hint", default="", help="Hint for the new attempt; goes to the model as feedback")
    approve.add_argument("--redesign-teaching", metavar="EPISODE_ID",
                         help="Redesign this episode's stopped teaching plan on the next resume; --hint is the "
                              "binding hint")
    approve.add_argument("--access-gap", nargs=2, metavar=("TASK_ID", "CRITERION"),
                         help="Accept one criterion of a blocked sub-question as an access gap (numbered from 0); "
                              "requires --blocked-source")
    approve.add_argument("--fresh-attempts", action="store_true",
                         help="Repeat steps that used up their correction attempts with fresh attempts on the next "
                              "resume (the rejected answers stay readable)")
    approve.add_argument("--text-switch", nargs="?", const="claude", choices=tuple(SWITCH_OPTIONS),
                         help="Continue the script or research job from the next resume with: claude (Claude, else "
                              "Astra; default), astra (Astra, else Claude), claude-only, astra-only, openrouter (billed, "
                              "with --switch-model and a key) or claude-api (billed, Anthropic API key); billed "
                              "providers need a money limit (--cost-usd). Astra works at xhigh, Claude with the "
                              "catalog's default model and level unless --switch-model names one; checkpoints stay valid")
    approve.add_argument("--switch-model", help="Model for --text-switch openrouter (e.g. openai/gpt-6-astra), "
                                                "claude-only or claude-api (e.g. claude-sonnet-5-5, at its preset's level)")
    approve.add_argument("--finish-with-residuals", action="store_true",
                         help="Finish after the next overall review; remaining objections go into the quality report "
                              "(--reason is saved as a note)")
    approve.add_argument("--rebuild-dossier", action="store_true",
                         help="Assemble the dossier from all verified answers on the next resume; the dossier written "
                              "so far and its review objections are set aside, nothing is researched again")
    approve.add_argument("--dispute", nargs=2, metavar=("OBJECTION_ID", "SIDE"),
                         help="Decide a dispute of the overall review: reviewer (follow the reviewer) or objection "
                              "(uphold the objection); --reason is saved as a note")
    approve.add_argument("--blocked-source", metavar="URL",
                         help="Address whose fetch was demonstrably blocked in this run (research_questions.json, "
                              "blocked_sources)")
    approve.add_argument("--research-plan", nargs="?", const=True, default=None, metavar="RUN_ID",
                         help="Approve the waiting research plan of this run (projection in "
                              "runs/<run_id>/question_research/plan_projection.json); without RUN_ID, --run-id or the "
                              "last run applies")
    approve.add_argument("--max-tasks", type=int, metavar="N",
                         help="With --research-plan: at most N sub-questions; the plan is cut once and presented again")
    schemas = commands.add_parser("schemas", help="Export the implemented JSON schemas")
    schemas.add_argument("output_dir", type=Path)
    for command in (init, version, doctor, state, text_probe, audio_probe, research, script, series, audio, kit, resume,
                    schemas, quota, approve):
        command.add_argument("--json", action="store_true", dest="json_output")
    return parser


def pinned_revision(model: str, project_dir: Path) -> str | None:
    """The commit of the Qwen model this computer already uses, so a new project records it instead of ``main``.

    The Qwen worker records the commit it loaded, and the audio check compares it with ``runtime.tts_revision``;
    with ``main`` every chapter failed as invalid_audio after the GPU work (2026-10-02). Looked up as the Studio does
    (``Studio.runtime``): the workspace's ``.studio/tts-runtime.json``, then a sibling project with the same model,
    then the commit the local Hugging Face cache names for ``main`` or the one snapshot it holds. None when none is
    known."""
    commit = re.compile(r"[a-f0-9]{40}")
    workspaces = dict.fromkeys([Path.cwd(), project_dir.parent.parent])
    for workspace in workspaces:
        try:
            local = json.loads((workspace / ".studio/tts-runtime.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if (isinstance(local, dict) and local.get("tts_model", model) == model
                and commit.fullmatch(str(local.get("tts_revision") or ""))):
            return local["tts_revision"]
    for path in sorted(project_dir.parent.glob("*/project.yaml")):
        try:
            runtime = load_project(path.parent).runtime
        except (AppError, ValueError):
            continue
        if runtime.tts_model == model and commit.fullmatch(runtime.tts_revision):
            return runtime.tts_revision
    hub = (Path(os.environ["HF_HUB_CACHE"]) if os.environ.get("HF_HUB_CACHE") else
           Path(os.environ["HF_HOME"]) / "hub" if os.environ.get("HF_HOME") else Path.home() / ".cache/huggingface/hub")
    cache = hub / ("models--" + model.replace("/", "--"))
    try:
        main_ref = (cache / "refs/main").read_text(encoding="utf-8").strip()
        if commit.fullmatch(main_ref):
            return main_ref
    except OSError:
        pass
    try:
        # Downloaded by its commit, the cache has no ref for main; a single snapshot is still unambiguous.
        snapshots = [entry.name for entry in (cache / "snapshots").iterdir() if commit.fullmatch(entry.name)]
    except OSError:
        return None
    return snapshots[0] if len(snapshots) == 1 else None


def emit(data: dict, as_json: bool):
    if as_json:
        print(json.dumps(data, ensure_ascii=False, indent=2))
        return
    if "checks" in data:
        for check in data["checks"]:
            print(f"{'OK' if check['ok'] else 'MISSING'} {check['name']}: {check['detail']}")
        print("Model inference is checked only by audio-probe.")
        return
    if "lines" in data and "codex_cli" in data:
        for line in data["lines"]:
            print(line)
        print(f"Checked: {data['checked_at']}")
        return
    print(data.get("message", f"Status: {data.get('status', 'ok')}"))
    run = data.get("run")
    if run:
        print(f"Run: {run['run_id']} ({run['kind']})")
        for name, stage in run["stages"].items():
            print(f"  {name}: {stage['status']}")
            if stage["error"]:
                print(f"  {stage['error']['message']}")
            for output in stage["outputs"]:
                if output.startswith(("probes/", "research/", "reports/", "models/", "episodes/")):
                    print(f"  {output}")
    if data.get("project_changed"):
        print("Project configuration changed; start a new run for the new inputs.")
    if data.get("invalid_completed_stages"):
        print("Artifacts missing or changed: " + ", ".join(data["invalid_completed_stages"]))
    if data.get("failure_records"):
        print("Technical failure logs: " + ", ".join(data["failure_records"]))


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "init" and args.topic is None and not args.trial:
        parser.error("init needs --topic; only a trial project (--trial) takes a sample topic")
    configure_path(Path.cwd())
    project_dir = getattr(args, "project_dir", None)
    log_path = project_dir / "logs/pla.log" if isinstance(project_dir, Path) and project_dir.is_dir() else None
    configure_logging(log_path)
    try:
        return run_command(args)
    finally:
        release_logging(log_path)


def hidden_key(args):
    """The key of a billed text provider, asked for hidden when --api-key is given without a value (D-112). Which
    provider it is for follows --backend, or for a resume the run's current choice (run_budget.run_text_generation)."""
    if getattr(args, "api_key", None) != "":
        return None
    backend = getattr(args, "backend", None)
    if backend is None and args.command == "resume":
        from .run_budget import run_text_generation
        selection = run_text_generation(manifest_path(args.project_dir.resolve(), args.run_id).parent) or {}
        backend = selection.get("provider")
    anthropic = backend == "claude_api"
    label, variable = ("Anthropic", "ANTHROPIC_API_KEY") if anthropic else ("OpenRouter", "OPENROUTER_API_KEY")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", getpass.GetPassWarning)
            key = getpass.getpass(f"{label} API key (for this call only): ")
    except (EOFError, getpass.GetPassWarning):
        raise AppError(f"No hidden key input possible; use {variable}.",
                       code="anthropic_key_required" if anthropic else "openrouter_key_required",
                       status="blocked") from None
    add_secret(key)
    return key


def run_command(args) -> int:
    try:
        if getattr(args, "api_key", None):
            raise AppError("--api-key takes no value: a key on the command line shows in the process list and the "
                           "shell history. --api-key without a value asks for it hidden; alternatively set "
                           "OPENROUTER_API_KEY or ANTHROPIC_API_KEY.",
                           code="invalid_request", status="blocked")
        if args.command == "studio":
            from .studio import serve
            serve(args.workspace, port=args.port, open_browser=not args.no_browser, lan=args.lan)
            return 0
        if args.command == "init":
            runtime = RuntimeSettings()
            if args.tts_python:
                runtime.tts_python = str(Path(args.tts_python).resolve())
            revision = pinned_revision(runtime.tts_model, args.project_dir.resolve())
            if revision:
                runtime.tts_revision = revision
            # Without --topic (only a trial, see main) the placeholder gives way to the sample topic at once.
            config = TopicBrief(topic=args.topic or "-", central_question=args.topic or "",
                                target_total_minutes=args.total_minutes, runtime=runtime)
            if args.trial:
                config = trial_brief(config, sample_topic=args.topic is None)
            init_project(args.project_dir.resolve(), config)
            parts = [f"Project created: {args.project_dir.resolve()}"]
            if args.trial:
                parts.append(f'Trial project on "{config.topic}": one episode of at most '
                             f"{config.target_total_minutes:g} minutes; each run stops at {TRIAL_LIMITS.model_calls} "
                             f"model calls (a research plan of at most {TRIAL_SUB_QUESTIONS} sub-questions), "
                             f"{TRIAL_LIMITS.search_rounds} search rounds and {TRIAL_LIMITS.sources} sources, and a "
                             f"money limit, where one is set, at {TRIAL_LIMITS.cost_usd:g} USD at most")
            if not revision:
                parts.append("The Qwen model revision is not pinned yet (runtime.tts_revision: main); enter the commit "
                             "of the loaded model in project.yaml before the first Qwen recording")
            message = ". ".join(parts) + ("." if len(parts) > 1 else "")
            data = {"status": "created", "message": message, "project": config.model_dump(mode="json")}
            if args.trial:
                data["trial"] = trial_facts()
            code = 0
        elif args.command == "new-version":
            from .project_versions import create_version, view
            target = create_version(args.project_dir, target=args.target)
            info = view(target)
            parts = [f"Version {info['version']} created: {target}"]
            if info["library"]:
                run_id = info["library"]["run_id"]
                parts.append(f"Starting library: {info['library']['documents']} sources of research run {run_id}; "
                             f"pla research {target} --seed-corpus {run_id} offers them to the search")
            data = {"status": "created", "message": ". ".join(parts) + ".", "project_dir": str(target), "version": info}
            code = 0
        elif args.command == "doctor":
            runtime = load_project(args.project_dir).runtime if args.project_dir else RuntimeSettings()
            data = inspect(runtime, include_tts=not args.skip_tts)
            code = 0 if data["ready"] else 1
        elif args.command == "status":
            data = status(args.project_dir.resolve(), args.run_id)
            code = 0
        elif args.command == "quota":
            from .subscriptions import quota_overview
            overview = quota_overview(RuntimeSettings(), refresh=True)
            data = {"status": "ready" if overview["any_available"] else
                    "waiting_for_quota" if overview["any_usable"] else "blocked", **overview}
            code = 0 if overview["any_available"] else 2 if overview["any_usable"] else 1
        elif args.command == "approve":
            from .run_budget import (approve_criterion_gap, approve_fresh_attempts, approve_model_call_limit,
                                     approve_research_gap, approve_research_plan, approve_research_retry,
                                     approve_dossier_rebuild, approve_residual_finish, approve_text_switch,
                                     decide_review_disagreement, request_teaching_redesign)
            root = args.project_dir.resolve()
            named = args.research_plan if isinstance(args.research_plan, str) else args.run_id
            run_id = manifest_path(root, named).parent.name
            if (args.model_calls is None and args.search_rounds is None and args.sources is None and args.cost_usd is None
                    and not args.accept_gap
                    and not args.retry and not args.access_gap and not args.dispute and not args.finish_with_residuals
                    and not args.fresh_attempts and args.research_plan is None and not args.redesign_teaching
                    and not args.text_switch and not args.rebuild_dossier):
                raise AppError("Name an approval: --research-plan, --model-calls, --search-rounds, --sources, "
                               "--cost-usd, --accept-gap, --access-gap, --dispute, --finish-with-residuals, "
                               "--fresh-attempts, --retry, --redesign-teaching, --rebuild-dossier or --text-switch.",
                               code="invalid_request", status="blocked")
            if bool(args.access_gap) != bool(args.blocked_source):
                raise AppError("--access-gap and --blocked-source go together.", code="invalid_request", status="blocked")
            if args.max_tasks is not None and args.research_plan is None:
                raise AppError("--max-tasks applies only together with --research-plan.", code="invalid_request",
                               status="blocked")
            data = {"status": "approved", "run_id": run_id}
            if args.research_plan is not None:
                plan = approve_research_plan(root, run_id, max_tasks=args.max_tasks, source="pla approve --research-plan")
                data["plan_approval"] = plan.model_dump(mode="json")
            if (args.model_calls is not None or args.search_rounds is not None or args.sources is not None
                    or args.cost_usd is not None):
                approval = approve_model_call_limit(root, run_id, args.model_calls, search_rounds=args.search_rounds,
                                                    sources=args.sources, cost_usd=args.cost_usd)
                data["budget_approval"] = approval.model_dump(mode="json")
            if args.accept_gap:
                gap = approve_research_gap(root, run_id, args.accept_gap, args.reason)
                data["gap_approval"] = gap.model_dump(mode="json")
            if args.retry:
                request = approve_research_retry(root, run_id, args.retry, args.hint)
                data["retry_request"] = request.model_dump(mode="json")
            if args.fresh_attempts:
                data["fresh_attempts"] = approve_fresh_attempts(root, run_id)
            if args.text_switch:
                data["text_switch"] = approve_text_switch(root, run_id, SWITCH_OPTIONS[args.text_switch],
                                                          model=args.switch_model)
            if args.redesign_teaching:
                redesign = request_teaching_redesign(root, run_id, args.redesign_teaching, args.hint)
                data["teaching_redesign"] = redesign.model_dump(mode="json")
            if args.finish_with_residuals:
                data["residual_finish"] = approve_residual_finish(root, run_id, args.reason).model_dump(mode="json")
            if args.rebuild_dossier:
                data["dossier_rebuild"] = approve_dossier_rebuild(root, run_id).model_dump(mode="json")
            if args.dispute:
                objection_id, side = args.dispute
                choice = decide_review_disagreement(root, run_id, objection_id, side, args.reason)
                data["dispute"] = choice.model_dump(mode="json")
            if args.access_gap:
                task_id, criterion = args.access_gap
                if not criterion.isdigit():
                    raise AppError("The criterion is a number from 0.", code="invalid_request", status="blocked")
                access = approve_criterion_gap(root, run_id, task_id, int(criterion), args.blocked_source, args.reason)
                data["access_gap"] = access.model_dump(mode="json")
            data["message"] = "Approval saved. The run takes it over on its next call or with pla resume."
            code = 0
        elif args.command == "schemas":
            for name, model in (SCHEMAS | RESEARCH_SCHEMAS | SCRIPT_SCHEMAS | TEACHING_SCHEMAS |
                                {"dialogue_polish_review": DialoguePolishReview, "series_review": SeriesReview}).items():
                write_json(args.output_dir / f"{name}.schema.json", model.model_json_schema())
            data = {"status": "completed", "message": f"Schemas exported: {args.output_dir.resolve()}"}
            code = 0
        elif args.command == "series-review":
            from .scripting import run_series_review, series_review_target
            manifest = run_series_review(args.project_dir, run_id=args.run_id, backend=args.backend,
                                         model=args.model, reasoning_effort=args.reasoning_effort)
            target = series_review_target(args.project_dir, manifest.run_id)
            verdict = ("Series review completed." if manifest.status == "completed"
                       else "Series review reports objections; check the report.")
            location = (" The verdict is in reports/script_quality.yaml." if target["report_mirrored"] else
                        f" The reviewed run {target['script_run_id']} is not the published state; "
                        f"the verdict is only in runs/{manifest.run_id}/series_review.json.")
            data = {"status": manifest.status, "run_id": manifest.run_id, **target, "message": verdict + location}
            code = 0 if manifest.status == "completed" else 1
        elif args.command == "publish-kit" and args.podcast:
            from .publish_kit import build_podcast_kit
            kit = build_podcast_kit(args.project_dir, fresh=args.fresh)
            covered = kit["transcript"]
            message = (f"Podcast companion material written: {kit['folder']} (episodes: {covered['episodes']}, "
                       f"recorded: {covered['recorded']}, sources: {len(kit['sources'])}).")
            if covered["recorded"] < covered["episodes"]:
                message += " Episodes without a recording of their script have no time marks in the transcript."
            if kit["descriptions_reused"]:
                message += " Descriptions taken over unchanged, without a model call."
            data = {"status": "completed", "message": message, "kit": kit}
            code = 0
        elif args.command == "publish-kit":
            from .publish_kit import build_publish_kit
            # The OpenRouter key of a script run that wrote with OpenRouter comes from OPENROUTER_API_KEY.
            kit = build_publish_kit(args.project_dir, args.episode, fresh=args.fresh)
            message = f"Companion material written: {kit['folder']}."
            if kit["recording"] is None:
                message += " This script state is not recorded yet; the chapters have no time marks."
            if kit["descriptions_reused"]:
                message += " Descriptions taken over unchanged, without a model call."
            data = {"status": "completed", "message": message, "kit": kit}
            code = 0
        else:
            episode_audio_run = args.command == "audio" or (
                args.command == "resume" and
                read_yaml(manifest_path(args.project_dir.resolve(), args.run_id)).get("kind") == "episode_audio")
            script_run = args.command == "script" or (
                args.command == "resume" and
                read_yaml(manifest_path(args.project_dir.resolve(), args.run_id)).get("kind") == "script")
            research_run = args.command == "research" or (
                args.command == "resume" and
                read_yaml(manifest_path(args.project_dir.resolve(), args.run_id)).get("kind") == "research")
            if (args.command == "resume" and
                    read_yaml(manifest_path(args.project_dir.resolve(), args.run_id)).get("kind") == "series_review"):
                # A review run holds one verdict and nothing to continue; it never becomes a probe.
                raise AppError("A series review run cannot be resumed; call pla series-review again.",
                               code="invalid_run", status="blocked")
            text_options = ("backend", "model", "api_key", "max_output_tokens", "reasoning_effort")
            given = {name for name in text_options if getattr(args, name, None) is not None}
            allowed = (set(text_options) if script_run else {"backend", "model", "reasoning_effort", "api_key"}
                       if research_run else {"backend"} if args.command == "text-probe" else set())
            if given - allowed:
                raise AppError("Text provider options apply to script, research, text-probe and the resume of a script "
                               "or research run; --max-output-tokens only to script.",
                               code="invalid_backend", status="blocked")
            api_key = hidden_key(args) if script_run or research_run else None
            if episode_audio_run:
                manifest = run_episode_audio(args.project_dir, episode=getattr(args, "episode", None),
                    approve_audio=getattr(args, "approve_audio", False), approval_note=getattr(args, "approval_note", ""),
                    resume=args.command == "resume", run_id=getattr(args, "run_id", None))
            elif script_run:
                manifest = run_script(args.project_dir, episode=getattr(args, "episode", None),
                                      revise=getattr(args, "revise", None), feedback=getattr(args, "feedback", ""),
                                      resume=args.command == "resume", run_id=getattr(args, "run_id", None),
                                      backend=args.backend, model=args.model, api_key=api_key,
                                      max_output_tokens=args.max_output_tokens, reasoning_effort=args.reasoning_effort,
                                      jev_probe=getattr(args, "jev_probe", False),
                                      web_search=getattr(args, "web_search", None))
            elif research_run:
                # The plan gate is the CLI default; only an explicit --approve-plan at the start waives it.
                manifest = run_research(args.project_dir, resume=args.command == "resume",
                                        run_id=getattr(args, "run_id", None),
                                        reuse_sources=getattr(args, "reuse_sources", None),
                                        seed_corpus=getattr(args, "seed_corpus", None),
                                        backend=getattr(args, "backend", None), model=getattr(args, "model", None),
                                        reasoning_effort=getattr(args, "reasoning_effort", None),
                                        plan_review="auto" if getattr(args, "approve_plan", False) else "required",
                                        api_key=api_key, web_search=getattr(args, "web_search", None))
            else:
                manifest = run_probe(
                    args.project_dir,
                    kind={"text-probe": "text_probe", "audio-probe": "audio_probe"}.get(args.command),
                    resume=args.command == "resume", run_id=getattr(args, "run_id", None),
                    approve_audio=getattr(args, "approve_audio", False), backend=getattr(args, "backend", None))
            data = {"status": manifest.status, "run": manifest.model_dump(mode="json")}
            code = 0 if manifest.status == "completed" else 2 if manifest.status == "waiting_for_quota" else 1
        emit(data, args.json_output)
        return code
    except AppError as exc:
        emit({"status": exc.status, "code": exc.code, "message": str(exc)}, args.json_output)
        return 1
    except ValidationError as exc:
        errors = "; ".join(".".join(map(str, item["loc"])) + ": " + item["msg"]
                           for item in exc.errors(include_input=False, include_url=False))
        emit({"status": "blocked", "code": "invalid_configuration",
              "message": f"Invalid data: {errors}"}, args.json_output)
        return 1
    except OSError as exc:
        logger("cli").error("File access failed: %s", exc, exc_info=exc)
        emit({"status": "failed", "code": "filesystem_error",
              "message": "File access failed; check the path and the write permissions."}, args.json_output)
        return 1
    except KeyboardInterrupt:
        emit({"status": "pending", "code": "interrupted",
              "message": "Interrupted. Continue the saved run with pla resume."}, args.json_output)
        return 130
