"""Offline gate replay or scoring of separately obtained model review responses.

Never starts a model or accesses the network. Synthetic receipts exercise contract
enforcement; they do not measure whether a model correctly reads scientific prose.
"""
import argparse
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from podcast_automate.evidence_models import EVIDENCE_VERSION, ResearchObjection, SynthesisRelation
from podcast_automate.errors import AppError
from podcast_automate.models import EpisodeScript
from podcast_automate.research_evidence import (EVIDENCE_INSTRUCTIONS, SYNTHESIS_INSTRUCTIONS,
    support_errors, validate_synthesis, validate_objection)
from podcast_automate.research_models import Finding, ResearchDossier
from podcast_automate.research_review import SourceReview
from podcast_automate.research_tasks import QuestionTask
from podcast_automate.script_evidence import SCRIPT_EVIDENCE_INSTRUCTIONS, validate_claim_checks
from podcast_automate.script_models import ScriptReview
from podcast_automate.storage import digest


def material(case):
    empirical = case["theme"] == "empirical"
    contract = dict(basis="empirical" if empirical else "source_definition", relation="association" if empirical else "definition",
                    scope=["The fictional source's stated population, setting and time"], qualifications=[], quantities=[])
    if case["scenario"] == "uncertainty":
        contract.update(basis="editorial_synthesis", relation="uncertainty", qualifications=["Explicitly unresolved scientific difference."])
    if case["id"] == "heldout_spoken_number":
        contract["quantities"] = [dict(name="share", value="25", unit="percent", direction="unspecified")]
    finding = Finding(id="f_one", kind="claim", statement=case["statement"], claim_contract=contract,
                      evidence=[dict(reference="src_one#s_one", excerpt=case["anchor"])])
    context = [dict(source_id="src_one", sections=[dict(reference="src_one#s_one", text=case["source"])])]
    findings = [finding]
    if case["scenario"] == "independence":
        finding.evidence.append(finding.evidence[0].model_copy(update={"reference": "src_two#s_two"}))
        context.append(dict(source_id="src_two", sections=[dict(reference="src_two#s_two", text=case["source"])]))
    if case["scenario"] == "synthesis":
        findings.append(finding.model_copy(update={"id": "f_two"}))
    return findings, context


def stub(case, findings, context):
    """Deliberately optimistic aggregate flags expose defects old issue-only gates missed."""
    receipts = [dict(finding_id=f.id, verdict=case.get("verdict", "supported"),
        references=[e.reference for e in f.evidence], reason="Scripted annotation of the full synthetic claim.",
        unsupported_clauses=[case["unsupported"]] if case.get("unsupported") else [], suitability="suitable",
        suitability_reason="Appropriate to this fictional assertion.", contract_preserved=True,
        empirical_status="not_applicable", independent_evidence_refs=[]) for f in findings]
    assessments = [dict(source_id=s["source_id"], roles=["empirical_test" if case["theme"] == "empirical" else "original_definition"],
        evidence_refs=[p["reference"] for p in s["sections"]], rationale="Synthetic annotation, not real-world evidence.",
        work_id="", version="", evidence_family="", independence="unknown", method="", research_group="",
        population="", geography="", period="", limitations=[]) for s in context]
    if case["scenario"] == "independence":
        receipts[0].update(empirical_status="independently_tested", independent_evidence_refs=receipts[0]["references"])
        for n, assessment in enumerate(assessments):
            family = "same" if case["shared"] else str(n)
            assessment.update(work_id="work_" + family, evidence_family="study_" + family,
                              independence="shared" if case["shared"] else "independent")
    if case.get("omit_reason"):
        receipts[0]["reason"] = ""
    return dict(issues=[], limitations=[], finding_support=receipts, source_assessments=assessments)


def scenario_data(case, findings):
    scenario = case["scenario"]
    if scenario == "script":
        script = EpisodeScript(episode_id="ep_one", title="Synthetic", purpose="deep_dive",
            chapters=[dict(chapter_id="ch_one", title="Synthetic")], segments=[dict(segment_id="seg_one", chapter_id="ch_one",
                scene_id="ch_one", speaker_id="host_a", text=case["spoken"], knowledge_refs=["f_one"])])
        review = dict(issues=[], limitations=[], claim_checks=[dict(segment_id="seg_one", finding_ids=["f_one"],
            verdict="drift" if case["changed"] else "preserved", quote=case["spoken"], changed_fields=case["changed"],
            reason="Synthetic annotation compares research scope, strength and numerical meaning.")])
        return script, review
    if scenario == "synthesis":
        relation = SynthesisRelation(id="rel_one", finding_ids=[f.id for f in findings], dimension="Response",
            relation=case["relation"], comparability=case["comparability"], conditions=case["source"],
            evidence_refs=["src_one#s_one"], resolution="unresolved", explanation="Compare only under stated conditions.", basis="editorial_synthesis")
        return ResearchDossier(topic="Synthetic", scope_note="Evaluation", findings=findings,
                               coverage=[], open_questions=[], synthesis=[relation]), None
    return None, None


def score(case, response=None):
    findings, context = material(case)
    extra, script_stub = scenario_data(case, findings)
    raw = response if response is not None else script_stub if script_stub is not None else stub(case, findings, context)
    baseline = not raw.get("issues", [])
    error = ""
    try:
        if case["scenario"] == "script":
            review = ScriptReview.model_validate(raw)
            errors = validate_claim_checks(review, extra, findings)
        else:
            review = SourceReview.model_validate(raw)
            errors = support_errors(findings, review, context)
            if case["scenario"] == "synthesis":
                validate_synthesis(extra, context)
            if case["scenario"] == "objection":
                task = QuestionTask(id="task_definition", requirement_ids=["rq_one"], question_ids=["q_one"],
                    question="Define the label", kind="definition", acceptance=["Define the label"], queries=["definition"],
                    key_terms=[], finding_ids=["f_one"], gap_ids=[])
                objection = ResearchObjection(id="obj_one", rule="support", task_id=task.id,
                    finding_ids=["f_one"], evidence_refs=["src_one#s_one"], missing_evidence="", reason="Test a different mechanism",
                    correction="Conduct unrelated experiment", closure_condition="Experiment supplied", resolution="research")
                empirical = task.model_copy(update={"id": "task_empirical", "kind": "empirical"})
                validate_objection(objection, [task, empirical], findings, context, target_task=task.id, owners={"f_one": ["task_empirical"]})
        accepted = not review.issues and not errors
        error = "; ".join(str(e) for e in errors)
    except (AppError, ValueError) as exc:
        accepted, error = False, str(exc)
    return dict(id=case["id"], scenario=case["scenario"], split=case["split"], valid=case["valid"],
                baseline_accepted=baseline, accepted=accepted, explanation=error or "All supplied gate receipts passed.")


def metrics(rows, field):
    invalid, valid = [r for r in rows if not r["valid"]], [r for r in rows if r["valid"]]
    return dict(unsupported_acceptances=sum(r[field] for r in invalid), invalid_cases=len(invalid),
                valid_false_blocks=sum(not r[field] for r in valid), valid_cases=len(valid),
                missing_explanations=sum(not r["explanation"] for r in rows),
                unjustified_reopenings=sum(r[field] for r in invalid if r["scenario"] == "objection"),
                semantic_drift_acceptances=sum(r[field] for r in invalid if r["scenario"] == "script"))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--split", choices=["development", "heldout", "all"], default="development")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--responses", type=Path, help="Optional captured reviews: corpus_hash, model, cases keyed by ID.")
    parser.add_argument("--export-prompts", type=Path)
    args = parser.parse_args()
    corpus = json.loads(Path(__file__).with_name("corpus.json").read_text(encoding="utf-8"))
    cases = [c for c in corpus["cases"] if args.split == "all" or c["split"] == args.split]
    saved = json.loads(args.responses.read_text(encoding="utf-8")) if args.responses else None
    if saved and (saved["corpus_hash"] != digest(corpus) or not set(c["id"] for c in cases) <= saved["cases"].keys()):
        parser.error("Captured reviews must match the corpus hash and cover every selected case.")
    prompts = []
    for case in cases:
        findings, context = material(case)
        extra, _ = scenario_data(case, findings)
        schema = ScriptReview if case["scenario"] == "script" else SourceReview
        instructions = SCRIPT_EVIDENCE_INSTRUCTIONS if schema is ScriptReview else EVIDENCE_INSTRUCTIONS + SYNTHESIS_INSTRUCTIONS
        payload = dict(findings=[f.model_dump() for f in findings], sources=context,
                       candidate=extra.model_dump() if extra else None)
        prompts.append(dict(id=case["id"], prompt=instructions + "\n" + json.dumps(payload), schema=schema.model_json_schema()))
    if args.export_prompts:
        args.export_prompts.parent.mkdir(parents=True, exist_ok=True)
        args.export_prompts.write_text(json.dumps(dict(corpus_hash=digest(corpus), prompts=prompts), indent=2), encoding="utf-8")
    rows = [score(c, saved["cases"][c["id"]] if saved else None) for c in cases]
    report = dict(version=EVIDENCE_VERSION, mode="captured_reviews" if saved else "scripted_gate_replay",
        annotation_status=corpus["annotation_status"], model=saved["model"] if saved else None, model_calls=0,
        human_correction_minutes=None, corpus_hash=digest(corpus), prompt_hash=digest(prompts),
        schema_hash=digest([SourceReview.model_json_schema(), ScriptReview.model_json_schema()]),
        baseline="Previous empty-issue-list acceptance; same supplied receipts, no historical model calls.",
        metrics={split: {"baseline": metrics([r for r in rows if r["split"] == split], "baseline_accepted"),
                         "current": metrics([r for r in rows if r["split"] == split], "accepted")}
                 for split in dict.fromkeys(r["split"] for r in rows)}, cases=rows)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if k != "cases"}, indent=2))
    return int(any(r["accepted"] != r["valid"] for r in rows))


if __name__ == "__main__":
    raise SystemExit(main())
