"""Can Jev, TypeSafe's decision model on OpenRouter, take over two narrow checks of this pipeline?

1. claim: does a spoken script sentence stay faithful to the findings and source passages it cites?
   Compared with the verdicts of the pipeline's own script reviews (claim_checks: preserved or drift).
2. section: does a source section help answer a research question? Compared with the sections the
   dossier actually cites for that question, and with the BM25 reader ranking used today (SourceReader.search).

The labels are model verdicts and citations, not human annotation. The evaluation measures agreement
with the current reviewer and recall of the cited evidence; it cannot show which of the two is right.

    build   offline: turns saved runs into cases under .studio/jev-eval/<name>/cases.json
    run     sends the cases to OpenRouter (key from OPENROUTER_API_KEY or asked without echo, never stored)
    score   offline: agreement, calibration and recall from cases.json and answers.jsonl

Run from the repository root:
    .\\.venv\\Scripts\\python.exe evals\\jev_decisions\\run.py build
    .\\.venv\\Scripts\\python.exe evals\\jev_decisions\\run.py run
    .\\.venv\\Scripts\\python.exe evals\\jev_decisions\\run.py score
"""
from __future__ import annotations

import argparse
import getpass
import json
import os
import random
import sys
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from podcast_automate.research_ledger import load_index, read_value  # noqa: E402
from podcast_automate.research_reader import SourceReader  # noqa: E402

MODEL = "typesafe/jev-1.13"
PRICE_PER_TOKEN = 0.042 / 1_000_000  # USD per input token, output free (OpenRouter endpoint data, 2026-09-29)
# The path that answered on 2026-09-29 first; the reference showed /api/v1/api/alpha/decisions, which returned 404.
ENDPOINTS = ("https://openrouter.ai/api/alpha/decisions", "https://openrouter.ai/api/v1/api/alpha/decisions",
             "https://openrouter.ai/api/v1/alpha/decisions")
BOOL_TYPES = ("noul", "boolean", "bool")  # "noul" as documented on 2026-09-29
STATE_CHARS = 24_000  # well inside Jev's 32,000-token window
SECTION_CHARS = 6_000
QUESTIONS_PER_REQUEST = 8
PROJECTS = {
    "asimov": "projects/mir-gehts-um-die-inhalte-der-doumente-di-c85f45",
    "ontologies": "projects/ontologies-and-knowledge-work-750124",
}

CLAIM_QUESTION = {
    "instructions": "Does the spoken sentence stay faithful to the cited research findings and source passages?",
    "criteria": {
        "true": "Every claim in the sentence is stated by the findings or passages, with the same relation, "
                "strength, scope, qualifications and numbers.",
        "false": "The sentence strengthens or widens a finding, drops a qualification, changes a number or "
                 "relation, or adds a claim the findings and passages do not support.",
    },
}


def section_question(question):
    return {
        "instructions": "Does this source passage contain information that helps answer the research question: "
                        + question,
        "criteria": {
            "true": "The passage states facts, definitions, results or arguments that a researcher would cite "
                    "when answering the question.",
            "false": "The passage is unrelated, only mentions the topic in passing, or is navigation, "
                     "a reference list or boilerplate.",
        },
    }


# --- build ------------------------------------------------------------------------------------------

def runs_of(project, kind):
    for manifest in sorted((project / "runs").glob("*/run_manifest.yaml")):
        text = manifest.read_text(encoding="utf-8")
        if f"\nkind: {kind}\n" in "\n" + text:
            yield manifest.parent


def research_index(project, research):
    state = read_value(research / "question_research/state.json")
    return load_index(project, research / "question_research/indexes" / f"{state['index_hash']}.json")


def sections_of(index, include_user_material):
    """reference -> (title, text) for every section that may be sent."""
    table = {}
    for source in index.sources:
        if not include_user_material and not (source.url and source.final_url):
            continue
        for section in source.sections:
            table[f"{source.id}#{section.id}"] = (source.title, section.text)
    return table


def finding_block(finding, sections):
    contract = finding.get("claim_contract") or {}
    lines = [f"- {finding['id']}: {finding['statement']}"]
    for key in ("scope", "qualifications"):
        if contract.get(key):
            lines.append(f"  {key.capitalize()}: " + "; ".join(contract[key]))
    if contract.get("quantities"):
        lines.append("  Quantities: " + json.dumps(contract["quantities"], ensure_ascii=False))
    for evidence in finding.get("evidence", []):
        lines.append(f"  Evidence ({evidence['reference']}): \"{evidence['excerpt']}\"")
    return "\n".join(lines)


def claim_cases(name, project, include_user_material):
    cases = []
    for script in runs_of(project, "script"):
        model = json.loads((script / "knowledge_model.json").read_text(encoding="utf-8"))
        findings = {f["id"]: f for f in model["claims"]}
        index = research_index(project, project / "runs" / model["research_run_id"])
        sections = sections_of(index, include_user_material)
        seen = defaultdict(lambda: {"verdicts": [], "changed_fields": set(), "reasons": [], "flagged": []})
        for call in sorted((script / "calls").glob("call_*")):
            try:
                activity = json.loads((call / "activity.json").read_text(encoding="utf-8"))
                response = json.loads((call / "response.json").read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if activity.get("schema") != "ScriptReview" or "claim_checks" not in response:
                continue
            episode = activity.get("subject") or ""
            flagged = {key: issue["reason"] for issue in response.get("issues", []) if issue["category"] == "grounding"
                       for key in issue["segment_ids"]}
            for check in response["claim_checks"]:
                if check["verdict"] == "no_research_claim" or not check["finding_ids"]:
                    continue
                row = seen[(episode, check["segment_id"], check["quote"], tuple(sorted(check["finding_ids"])))]
                row["verdicts"].append(check["verdict"])
                row["changed_fields"].update(check.get("changed_fields") or [])
                row["reasons"].append(check["reason"])
                if check["segment_id"] in flagged:
                    row["flagged"].append(flagged[check["segment_id"]])
        for (episode, segment, quote, finding_ids), row in seen.items():
            cited = [findings[f] for f in finding_ids if f in findings]
            references = [e["reference"] for f in cited for e in f.get("evidence", [])]
            if not cited or any(ref.split("#")[0] and ref not in sections for ref in references):
                continue  # a finding the model file lacks, or evidence that may not be sent
            passages = "\n\n".join(f"[{ref}] {sections[ref][0]}\n{sections[ref][1][:SECTION_CHARS]}"
                                   for ref in dict.fromkeys(references))
            state = ("Spoken sentence from a two-host podcast script:\n\"" + quote + "\"\n\nResearch findings it cites:\n" +
                     "\n".join(finding_block(f, sections) for f in cited) + "\n\nSource passages:\n" + passages)[:STATE_CHARS]
            verdicts = set(row["verdicts"])
            label = "supported" if verdicts == {"preserved"} else "drift" if verdicts == {"drift"} else "disputed"
            cases.append({"id": f"claim:{name}:{script.name[-8:]}:{episode}:{segment}:{len(cases)}", "kind": "claim",
                          "project": name, "episode": episode, "segment_id": segment, "quote": quote,
                          "finding_ids": list(finding_ids), "label": label, "reviews": len(row["verdicts"]),
                          "verdicts": row["verdicts"], "changed_fields": sorted(row["changed_fields"]),
                          "grounding_flags": len(row["flagged"]), "state": state})
    return cases


def section_cases(name, project, include_user_material, bm25_depth, random_negatives, seed):
    cases = []
    rng = random.Random(seed)
    for research in runs_of(project, "research"):
        if not (research / "reviewed_dossier.json").exists():
            continue
        dossier = json.loads((research / "reviewed_dossier.json").read_text(encoding="utf-8"))
        asked = json.loads((research / "discovery.json").read_text(encoding="utf-8"))["questions"]
        questions = {q["id"]: q["question"] for q in asked}
        # The baseline searches as the research did: its saved search query (in the corpus's language) with the question.
        queries = {q["id"]: " ".join(filter(None, (q.get("search_query"), q["question"]))) for q in asked}
        findings = {f["id"]: f for f in dossier["findings"]}
        index = research_index(project, research)
        sections = sections_of(index, include_user_material)
        reader = SourceReader(index)
        everything = sorted(sections)
        for row in dossier["coverage"]:
            question = questions.get(row["question_id"])
            cited = {e["reference"] for f in row["finding_ids"] if f in findings for e in findings[f]["evidence"]}
            positives = sorted(cited & set(sections))
            if not question or not positives:
                continue
            ranked = reader.search(queries[row["question_id"]], key_terms=row.get("gap_terms") or (), include_notes=include_user_material,
                                   limit=bm25_depth)["candidates"]
            bm25 = [c["reference"] for c in ranked if c["reference"] in sections]
            pool = list(dict.fromkeys([*bm25, *positives, *rng.sample(everything, min(random_negatives, len(everything)))]))
            for reference in pool:
                title, text = sections[reference]
                cases.append({"id": f"section:{name}:{research.name[-8:]}:{row['question_id']}:{reference}",
                              "kind": "section", "project": name, "question_id": row["question_id"], "question": question,
                              "reference": reference, "label": reference in cited,
                              "bm25_rank": bm25.index(reference) + 1 if reference in bm25 else None,
                              "state": f"Source: {title}\n\n{text[:SECTION_CHARS]}"})
    return cases


def build(args):
    out = Path(args.out)
    cases = []
    for name in args.projects:
        project = ROOT / PROJECTS[name]
        cases += claim_cases(name, project, args.include_user_material)
        cases += section_cases(name, project, args.include_user_material, args.bm25_depth, args.random_negatives, args.seed)
    claims = [c for c in cases if c["kind"] == "claim"]
    rng = random.Random(args.seed)
    for name in args.projects:
        # Most checks say "preserved"; every drift and disputed case stays, the rest is sampled.
        supported = [c for c in claims if c["project"] == name and c["label"] == "supported"]
        drop = {c["id"] for c in supported[:]} - {c["id"] for c in rng.sample(supported, min(args.max_supported, len(supported)))}
        cases = [c for c in cases if c["id"] not in drop]
    out.mkdir(parents=True, exist_ok=True)
    (out / "cases.json").write_text(json.dumps({"model": MODEL, "include_user_material": args.include_user_material,
                                                "cases": cases}, ensure_ascii=False, indent=1), encoding="utf-8")
    report_build(cases, out)


def report_build(cases, out):
    tokens = sum(len(c["state"]) for c in cases) / 4
    print(f"{len(cases)} Fälle in {out / 'cases.json'}")
    for name in sorted({c["project"] for c in cases}):
        own = [c for c in cases if c["project"] == name]
        labels = defaultdict(int)
        for c in own:
            labels[(c["kind"], str(c["label"]))] += 1
        print(f"  {name}: " + ", ".join(f"{kind} {label}: {n}" for (kind, label), n in sorted(labels.items())))
    print(f"Geschätzt {tokens / 1e6:.2f} Mio. Eingabe-Tokens, etwa {tokens * PRICE_PER_TOKEN:.2f} USD "
          f"(Abschnitte mit mehreren Fragen teilen sich eine Anfrage, real eher weniger).")


# --- run --------------------------------------------------------------------------------------------

def post(url, key, payload, timeout=120):
    request = Request(url, data=json.dumps(payload).encode("utf-8"), method="POST",
                      headers={"Authorization": "Bearer " + key, "Content-Type": "application/json",
                               "X-Title": "podcast-automate jev eval"})
    try:
        with urlopen(request, timeout=timeout) as response:
            return response.status, json.loads(response.read())
    except HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", "replace").replace(key, "[Key]")[:800]
    except (URLError, TimeoutError, OSError, ValueError) as exc:
        return 0, f"{type(exc).__name__}: {exc}"


def probe(key):
    """Find the endpoint and the name of the yes/no question type with one tiny request each."""
    for url in ENDPOINTS:
        for kind in BOOL_TYPES:
            status, body = post(url, key, {"model": MODEL, "state": "The sky is blue on a clear day.",
                                           "questions": {"q": {"type": kind, "instructions": "Is the text about weather?",
                                                               "criteria": {"true": "It is.", "false": "It is not."}}}})
            print(f"  {url} · {kind}: HTTP {status}" + ("" if status == 200 else f": {body}"))
            if status == 200:
                return url, kind
            if status in (401, 402) or (status == 404 and "privacy" in str(body).lower()):
                sys.exit("Abbruch: Key, Guthaben oder Datenschutzeinstellung (openrouter.ai/settings/privacy) prüfen.")
            if status != 400:
                break  # this path does not exist; the type name is not the problem
    sys.exit("Kein funktionierender Endpunkt gefunden; Ausgaben oben prüfen.")


def requests_for(cases, kind_name):
    """Claim cases each get one request; section cases share a request per section, up to 8 questions."""
    grouped = defaultdict(list)
    for case in cases:
        grouped[case["id"] if case["kind"] == "claim" else case["state"]].append(case)
    for members in grouped.values():
        for start in range(0, len(members), QUESTIONS_PER_REQUEST):
            batch = members[start:start + QUESTIONS_PER_REQUEST]
            questions = {}
            for n, case in enumerate(batch):
                spec = CLAIM_QUESTION if case["kind"] == "claim" else section_question(case["question"])
                questions[f"q{n}"] = {"type": kind_name, **spec}
            yield batch, {"model": MODEL, "state": batch[0]["state"], "questions": questions}


def run(args):
    out = Path(args.out)
    built = json.loads((out / "cases.json").read_text(encoding="utf-8"))
    cases = built["cases"]
    answers_path = out / "answers.jsonl"
    done = set()
    if answers_path.exists():
        done = {json.loads(line)["id"] for line in answers_path.read_text(encoding="utf-8").splitlines() if line.strip()}
    pending = [c for c in cases if c["id"] not in done]
    estimate = sum(len(c["state"]) for c in pending) / 4 * PRICE_PER_TOKEN
    print(f"{len(pending)} von {len(cases)} Fällen offen, geschätzt höchstens {estimate:.2f} USD. Modell {MODEL}.")
    print("Gesendet werden Skriptsätze, Befunde und Quellenabschnitte an TypeSafe über OpenRouter; "
          "dessen Datenrichtlinie ist nicht dokumentiert. " +
          ("Eigene Dokumente sind enthalten (--include-user-material)." if built["include_user_material"]
           else "Eigene Dokumente sind ausgenommen."))
    if estimate > args.max_usd:
        sys.exit(f"Abbruch: Schätzung über --max-usd {args.max_usd}.")
    if not args.yes and input("Senden? (ja/nein) ").strip().lower() != "ja":
        sys.exit("Nichts gesendet.")
    key = os.environ.get("OPENROUTER_API_KEY", "").strip() or getpass.getpass("OpenRouter-Key (wird nicht gespeichert): ").strip()
    if not key:
        sys.exit("Kein Key angegeben.")
    print("Endpunkt prüfen:")
    url, kind_name = probe(key)
    batches = list(requests_for(pending, kind_name))
    spent = {"usd": 0.0, "failed": 0}

    def ask(item):
        batch, payload = item
        for attempt in range(4):
            started = time.monotonic()
            status, body = post(url, key, payload)
            if status == 200:
                rows = [{"id": case["id"], "answer": body["answers"].get(f"q{n}"), "model": body.get("model"),
                         "seconds": round(time.monotonic() - started, 3),
                         "cost": body.get("usage", {}).get("cost", 0) / len(batch)} for n, case in enumerate(batch)]
                return rows, body.get("usage", {}).get("cost", 0)
            if status in (0, 429) or status >= 500:
                time.sleep(2 ** attempt * 2)  # rate limit or server error: wait, then ask again
                continue
            print(f"  HTTP {status} für {batch[0]['id']}: {body}")
            break
        return [], 0

    with ThreadPoolExecutor(max_workers=args.workers) as pool, answers_path.open("a", encoding="utf-8") as sink:
        for n, (rows, cost) in enumerate(pool.map(ask, batches), 1):
            spent["usd"] += cost or 0
            spent["failed"] += 0 if rows else 1
            for row in rows:
                sink.write(json.dumps(row, ensure_ascii=False) + "\n")
            sink.flush()
            if n % 25 == 0 or n == len(batches):
                print(f"  {n}/{len(batches)} Anfragen, {spent['usd']:.4f} USD, {spent['failed']} fehlgeschlagen")
    score(args)


# --- score ------------------------------------------------------------------------------------------

def probability(answer):
    if not isinstance(answer, dict):
        return None
    for key in BOOL_TYPES:
        if isinstance(answer.get(key), (int, float)):
            return float(answer[key])
    return None


def auc(positives, negatives):
    """Chance that a random positive gets a higher probability than a random negative."""
    if not positives or not negatives:
        return None
    wins = sum((p > n) + 0.5 * (p == n) for p in positives for n in negatives)
    return round(wins / (len(positives) * len(negatives)), 3)


def calibration(pairs):
    bins = defaultdict(lambda: [0, 0])
    for p, truth in pairs:
        slot = min(int(p * 5), 4)
        bins[slot][0] += 1
        bins[slot][1] += truth
    return {f"{slot / 5:.1f}-{(slot + 1) / 5:.1f}": {"n": n, "observed": round(hits / n, 2)}
            for slot, (n, hits) in sorted(bins.items())}


def recall_at(ranked, positives, k):
    """Cited sections among the first k, out of as many as k places can hold."""
    return len(set(ranked[:k]) & positives) / min(k, len(positives))


def score(args):
    out = Path(args.out)
    cases = {c["id"]: c for c in json.loads((out / "cases.json").read_text(encoding="utf-8"))["cases"]}
    answers = {}
    seconds, cost = [], 0.0
    for line in (out / "answers.jsonl").read_text(encoding="utf-8").splitlines():
        row = json.loads(line)
        if (p := probability(row["answer"])) is not None and row["id"] in cases:
            answers[row["id"]] = p
            seconds.append(row["seconds"])
            cost += row.get("cost") or 0
    report = {"model": MODEL, "answered": len(answers), "cases": len(cases), "cost_usd": round(cost, 4),
              "median_seconds": sorted(seconds)[len(seconds) // 2] if seconds else None, "claim": {}, "section": {}}
    for name in sorted({c["project"] for c in cases.values()}):
        claims = [(answers[i], c) for i, c in cases.items() if i in answers and c["kind"] == "claim" and c["project"] == name]
        clear = [(p, c["label"] == "supported") for p, c in claims if c["label"] != "disputed"]
        drift = [p for p, c in claims if c["label"] == "drift"]
        report["claim"][name] = {
            "cases": {label: sum(c["label"] == label for _, c in claims) for label in ("supported", "drift", "disputed")},
            "auc": auc([p for p, t in clear if t], [p for p, t in clear if not t]),
            "agreement_at_0.5": round(sum((p >= .5) == t for p, t in clear) / len(clear), 3) if clear else None,
            "drift_caught_at_0.5": round(sum(p < .5 for p in drift) / len(drift), 3) if drift else None,
            "supported_passed_at_0.5": round(sum(p >= .5 for p, t in clear if t) / max(1, sum(t for _, t in clear)), 3),
            "calibration": calibration(clear),
            "disputed_mean_p": round(sum(p for p, c in claims if c["label"] == "disputed") /
                                     max(1, sum(c["label"] == "disputed" for _, c in claims)), 3),
        }
        by_question = defaultdict(list)
        for i, c in cases.items():
            if i in answers and c["kind"] == "section" and c["project"] == name:
                by_question[c["question_id"]].append((answers[i], c))
        # bm25: today's reader order. jev_rerank: Jev reorders exactly those candidates, the realistic use.
        # jev_pool: Jev over a pool that contains every cited section, so it measures separation, not retrieval.
        rows = {"bm25": defaultdict(list), "jev_rerank": defaultdict(list), "jev_pool": defaultdict(list)}
        pos, neg = [], []
        for items in by_question.values():
            positives = {c["reference"] for _, c in items if c["label"]}
            pool = [c["reference"] for p, c in sorted(items, key=lambda x: -x[0])]
            candidates = [x for x in items if x[1]["bm25_rank"]]
            bm25 = [c["reference"] for _, c in sorted(candidates, key=lambda x: x[1]["bm25_rank"])]
            rerank = [c["reference"] for _, c in sorted(candidates, key=lambda x: -x[0])]
            for k in (5, 10, 20):
                rows["bm25"][k].append(recall_at(bm25, positives, k))
                rows["jev_rerank"][k].append(recall_at(rerank, positives, k))
                rows["jev_pool"][k].append(recall_at(pool, positives, k))
            pos += [p for p, c in items if c["label"]]
            neg += [p for p, c in items if not c["label"]]
        report["section"][name] = {
            "questions": len(by_question), "cited_sections": len(pos), "other_sections": len(neg),
            "auc": auc(pos, neg),
            "recall_of_cited": {ranker: {f"@{k}": round(sum(v) / len(v), 3) for k, v in values.items()}
                                for ranker, values in rows.items() if values},
        }
    (out / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=1))


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("command", choices=("build", "run", "score"))
    parser.add_argument("--out", default=str(ROOT / ".studio" / "jev-eval" / "v1"))
    parser.add_argument("--projects", nargs="+", default=list(PROJECTS), choices=list(PROJECTS))
    parser.add_argument("--include-user-material", action="store_true",
                        help="also send passages of user-supplied documents (off by default)")
    parser.add_argument("--max-supported", type=int, default=250, help="sampled 'preserved' claim cases per project")
    parser.add_argument("--bm25-depth", type=int, default=30)
    parser.add_argument("--random-negatives", type=int, default=15)
    parser.add_argument("--seed", type=int, default=20260929)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--max-usd", type=float, default=1.0)
    parser.add_argument("--yes", action="store_true", help="send without asking")
    args = parser.parse_args()
    {"build": build, "run": run, "score": score}[args.command](args)


if __name__ == "__main__":
    main()
