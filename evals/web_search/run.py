"""Does the Perplexity web search find what the text model's own web search found?

Each case is one sub-question search (QuestionSearch) a finished research run already made with the model's own web
tools: its question, acceptance criteria and earlier queries from ``work_context.json``, and the candidates it
chose from ``response.json``. The evaluation asks the same question again through the Perplexity search
(provider_pool.AdapterPool.searched, D-151) with an OpenRouter model, and compares the two candidate lists: how many,
how many addresses both found, how many share a host, which source types were chosen, and what it cost. The saved
search is the baseline, not ground truth: it shows whether Perplexity reaches the same kind of sources, not which list
is better.

    build   offline: writes .studio/web-search-eval/<name>/cases.json from the saved runs
    run     asks before sending, then runs the Perplexity search per case (keys from OPENROUTER_API_KEY and
            PERPLEXITY_API_KEY, or asked without echo, never stored); answers go to answers.jsonl
    score   offline: report.json from cases.json and answers.jsonl

Run from the repository root:
    .\\.venv\\Scripts\\python.exe evals\\web_search\\run.py build --project projects\\<project> [--cases 20]
    .\\.venv\\Scripts\\python.exe evals\\web_search\\run.py run --model deepseek/deepseek-v4.1-flash
    .\\.venv\\Scripts\\python.exe evals\\web_search\\run.py score
"""
from __future__ import annotations

import argparse
import getpass
import json
import os
import random
import sys
from collections import Counter
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from podcast_automate.models import RuntimeSettings, TopicBrief  # noqa: E402
from podcast_automate.prompts import instructions  # noqa: E402
from podcast_automate.provider_pool import AdapterPool, address, read_billing, text_generation_settings  # noqa: E402
from podcast_automate.research_tasks import QuestionSearch  # noqa: E402

OUT = ROOT / ".studio" / "web-search-eval"


def host(url):
    return (urlsplit(url).hostname or "").removeprefix("www.")


def build(args):
    folder = OUT / args.name
    cases = []
    for call in sorted(Path(args.project).resolve().glob("runs/run_*/calls/call_*")):
        try:
            context = json.loads((call / "work_context.json").read_text(encoding="utf-8"))
            response = json.loads((call / "response.json").read_text(encoding="utf-8"))
            metadata = json.loads((call / "metadata.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if context.get("schema") != "QuestionSearch" or not context.get("question"):
            continue
        cases.append({"id": f"{call.parent.parent.name}/{call.name}", "question": context["question"],
                      "criteria": context.get("criteria", []), "earlier_queries": context.get("queries", []),
                      "baseline": {"candidates": [{"url": c["url"], "source_type": c.get("source_type", "unknown")}
                                                  for c in response.get("candidates", [])],
                                   "queries": metadata.get("observed_search_queries", []),
                                   "model": metadata.get("requested_model"),
                                   "usd": metadata.get("separately_billed_cost") or metadata.get("reported_cost_usd")}})
    random.Random(7).shuffle(cases)
    cases = cases[:args.cases]
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "cases.json").write_text(json.dumps(cases, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"{len(cases)} cases written to {folder / 'cases.json'}")


def key(variable, label):
    value = os.environ.get(variable, "").strip()
    return value or getpass.getpass(f"{label} (nur für diesen Aufruf): ").strip()


def run(args):
    folder = OUT / args.name
    cases = json.loads((folder / "cases.json").read_text(encoding="utf-8"))
    answers_path = folder / "answers.jsonl"
    done = {json.loads(line)["id"] for line in answers_path.read_text(encoding="utf-8").splitlines()} \
        if answers_path.exists() else set()
    pending = [case for case in cases if case["id"] not in done]
    if not pending:
        print("Nothing to send.")
        return
    print(f"{len(pending)} cases: each sends one question, its criteria and earlier queries to OpenRouter "
          f"({args.model}) and up to five queries to Perplexity. Estimated cost below 0.05 USD per case.")
    if input("Send? [y/N] ").strip().lower() != "y":
        return
    config = TopicBrief(topic="Web search evaluation")
    selection = text_generation_settings(config, backend="openrouter", model=args.model, web_search="perplexity")
    pool = AdapterPool(RuntimeSettings(text_timeout_seconds=600), selection,
                       api_key=key("OPENROUTER_API_KEY", "OpenRouter-Key"),
                       perplexity_key=key("PERPLEXITY_API_KEY", "Perplexity-Key"))
    with answers_path.open("a", encoding="utf-8") as answers:
        for case in pending:
            directory = folder / "calls" / case["id"].replace("/", "__")
            prompt = instructions("question_search", maximum=4) + "\n" + json.dumps(
                {"question": case["question"], "acceptance": case["criteria"],
                 "earlier_queries": case["earlier_queries"]}, ensure_ascii=False)
            try:
                output, metadata = pool.structured(prompt, QuestionSearch, directory,
                                                   prompt_version="eval_web_search.v1", search=True)
                row = {"id": case["id"], "candidates": [{"url": c.url, "source_type": c.source_type}
                                                        for c in output.candidates],
                       "queries": metadata.get("observed_search_queries", []),
                       "usd": round(sum(r.get("usd") or 0 for r in read_billing(directory)), 6)}
            except Exception as exc:  # noqa: BLE001  (one failed case is recorded, the rest continue)
                row = {"id": case["id"], "error": getattr(exc, "code", type(exc).__name__)}
            answers.write(json.dumps(row, ensure_ascii=False) + "\n")
            answers.flush()
            print(case["id"], row.get("error") or f"{len(row['candidates'])} candidates")


def score(args):
    folder = OUT / args.name
    cases = {case["id"]: case for case in json.loads((folder / "cases.json").read_text(encoding="utf-8"))}
    rows = [json.loads(line) for line in (folder / "answers.jsonl").read_text(encoding="utf-8").splitlines()]
    totals = Counter()
    types = {"baseline": Counter(), "perplexity": Counter()}
    for row in rows:
        if row.get("error"):
            totals["errors"] += 1
            continue
        base = cases[row["id"]]["baseline"]["candidates"]
        mine = row["candidates"]
        totals["cases"] += 1
        totals["baseline_candidates"] += len(base)
        totals["perplexity_candidates"] += len(mine)
        totals["same_address"] += len({address(c["url"]) for c in base} & {address(c["url"]) for c in mine})
        totals["same_host"] += len({host(c["url"]) for c in base} & {host(c["url"]) for c in mine})
        totals["perplexity_empty"] += not mine
        totals["baseline_empty"] += not base
        totals["perplexity_usd_micro"] += round(row.get("usd", 0) * 1_000_000)
        types["baseline"].update(c["source_type"] for c in base)
        types["perplexity"].update(c["source_type"] for c in mine)
    report = {"cases": totals["cases"], "errors": totals["errors"],
              "candidates": {"baseline": totals["baseline_candidates"], "perplexity": totals["perplexity_candidates"]},
              "empty_results": {"baseline": totals["baseline_empty"], "perplexity": totals["perplexity_empty"]},
              "shared": {"addresses": totals["same_address"], "hosts": totals["same_host"]},
              "source_types": {name: dict(counter) for name, counter in types.items()},
              "perplexity_usd": round(totals["perplexity_usd_micro"] / 1_000_000, 4)}
    (folder / "report.json").write_text(json.dumps(report, indent=1), encoding="utf-8")
    print(json.dumps(report, indent=1))


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--name", default="v1")
    commands = parser.add_subparsers(dest="command", required=True)
    built = commands.add_parser("build")
    built.add_argument("--project", required=True)
    built.add_argument("--cases", type=int, default=20)
    sent = commands.add_parser("run")
    sent.add_argument("--model", default="deepseek/deepseek-v4.1-flash")
    commands.add_parser("score")
    args = parser.parse_args()
    {"build": build, "run": run, "score": score}[args.command](args)


if __name__ == "__main__":
    main()
