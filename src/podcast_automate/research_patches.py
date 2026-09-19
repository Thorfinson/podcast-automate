"""Bounded dossier edits with immutable unrelated findings and resumable receipts."""
from __future__ import annotations

import json
from collections import Counter

from pydantic import Field

from .prompts import instructions as prompt_instructions
from .editorial import TERMINOLOGY, TEACHING_SCOPE
from .errors import AppError
from .models import Contract, NonEmpty
from .research_models import Finding, QuestionCoverage, ResearchDossier
from .research_retrieval import references, select_context
from .storage import digest, write_json
from .evidence_models import SynthesisRelation
from .research_evidence import EVIDENCE_INSTRUCTIONS, SYNTHESIS_INSTRUCTIONS, claim_changes

PATCH_VERSION = "research_patch.v1"


class DossierPatch(Contract):
    updates: list[Finding] = Field(max_length=120)
    additions: list[Finding] = Field(max_length=120)
    coverage_updates: list[QuestionCoverage]
    resolved_open_questions: list[NonEmpty]
    new_open_questions: list[NonEmpty]
    synthesis_updates: list[SynthesisRelation] = Field(default_factory=list, max_length=40)
    removed_synthesis_ids: list[NonEmpty] = Field(default_factory=list)


def cached_call(folder, name, schema, prompt, generate):
    path = folder / f"{name}.json"
    signature = digest({"prompt": prompt, "schema": schema.model_json_schema(), "version": PATCH_VERSION})
    if path.exists():
        saved = json.loads(path.read_text(encoding="utf-8"))
        if saved.get("input_hash") != signature or saved.get("sha256") != digest(saved.get("value")):
            raise AppError("Gespeicherte Rechercheänderung passt nicht zu ihren Eingaben.",
                           code="invalid_research_checkpoint", status="blocked")
        return schema.model_validate(saved["value"])
    value = generate(prompt, schema)
    write_json(path, {"input_hash": signature, "sha256": digest(value.model_dump()), "value": value.model_dump()})
    return value


def apply_patch(dossier, patch, allowed_ids, *, allow_additions=True, coverage_ids=None, allow_questions=True):
    existing = {f.id: f for f in dossier.findings}
    updates = [f.id for f in patch.updates]
    additions = [f.id for f in patch.additions]
    coverage = [c.question_id for c in patch.coverage_updates]
    allowed_coverage = {c.question_id for c in dossier.coverage} if coverage_ids is None else set(coverage_ids)
    invalid = (len(set(updates)) != len(updates) or len(set(additions)) != len(additions)
               or not set(updates) <= (set(allowed_ids) & existing.keys())
               or bool(set(additions) & existing.keys()) or (patch.additions and not allow_additions)
               or len(set(coverage)) != len(coverage) or not set(coverage) <= allowed_coverage
               or not set(patch.resolved_open_questions) <= set(dossier.open_questions)
               or (not allow_questions and (patch.resolved_open_questions or patch.new_open_questions)))
    if invalid:
        raise AppError("Die Dossierkorrektur verändert nicht freigegebene Befunde oder Fragen.",
                       code="invalid_research_patch", status="blocked")
    relations = {r.id: r for r in dossier.synthesis}
    edited = set(updates) | set(additions) | set(allowed_ids)
    if (len({r.id for r in patch.synthesis_updates}) != len(patch.synthesis_updates) or
            not set(patch.removed_synthesis_ids) <= relations.keys() or
            any(not set(relations[r].finding_ids) & edited for r in patch.removed_synthesis_ids) or
            any(not set(r.finding_ids) & edited or (r.id in relations and
                not set(relations[r.id].finding_ids) & edited) for r in patch.synthesis_updates)):
        raise AppError("Synthesis edits must concern the editable findings.", code="invalid_research_patch", status="blocked")
    for identifier in patch.removed_synthesis_ids:
        relations.pop(identifier)
    relations.update({r.id: r for r in patch.synthesis_updates})
    replacements = {f.id: f for f in patch.updates}
    coverage_replacements = {c.question_id: c for c in patch.coverage_updates}
    rows = [coverage_replacements.pop(c.question_id, c) for c in dossier.coverage]
    rows.extend(coverage_replacements.values())  # Can repair a missing coverage row.
    result = {**dossier.model_dump(), "synthesis": [r.model_dump() for r in relations.values()],
              "findings": [replacements.get(f.id, f).model_dump() for f in dossier.findings]
              + [f.model_dump() for f in patch.additions], "coverage": [c.model_dump() for c in rows],
              "open_questions": list(dict.fromkeys([q for q in dossier.open_questions
                  if q not in patch.resolved_open_questions] + patch.new_open_questions))}
    return ResearchDossier.model_validate(result)


def error_targets(dossier, errors, discovery):
    """Source-wide quote limits require all findings citing that source, not a rewrite of all sources."""
    ids, questions = set(), set()
    for error in errors:
        prefix = error.split(":", 1)[0]
        ids.update(f.id for f in dossier.findings if f.id == prefix or
                   any(e.reference.split("#")[0] == prefix for e in f.evidence))
        questions.update(c.question_id for c in dossier.coverage if c.question_id == prefix)
        if error.startswith("Coverage must"):
            expected = {q.id for q in discovery.questions}
            counts = Counter(c.question_id for c in dossier.coverage)
            questions.update(q for q in expected if counts[q] != 1)
    return ids, questions


def patch_prompt(dossier, discovery, context, config, *, targets, instructions,
                 extra_context=(), allow_additions=True, coverage_ids=None, allow_questions=True):
    selected = [f for f in dossier.findings if f.id in targets]
    refs = {e.reference for f in selected for e in f.evidence} | references(extra_context)
    source_ids = {ref.split("#")[0] for ref in refs}
    # All existing uses of the edited sources count towards the shared limits.
    usage = [{"finding_id": f.id, "statement_words": len(f.statement.split()),
              "evidence": [e.model_dump() for e in f.evidence if e.reference.split("#")[0] in source_ids]}
             for f in dossier.findings if any(e.reference.split("#")[0] in source_ids for e in f.evidence)]
    prompt = (TERMINOLOGY + TEACHING_SCOPE + EVIDENCE_INSTRUCTIONS + SYNTHESIS_INSTRUCTIONS +
        prompt_instructions("dossier_patch", language=config.language) + "\n" + json.dumps({
            "brief": {"topic": config.topic, "central_question": config.central_question,
                      "focus_questions": config.focus_questions, "depth": config.depth_request,
                      "audience": config.audience_level, "prior_knowledge": config.prior_knowledge,
                      "excluded_topics": config.excluded_topics},
            "instructions": instructions, "editable_findings": [f.model_dump() for f in selected],
            "reserved_finding_ids": [f.id for f in dossier.findings],
            "questions": [q.model_dump() for q in discovery.questions],
            "coverage": [c.model_dump() for c in dossier.coverage], "open_questions": dossier.open_questions,
            "synthesis": [r.model_dump() for r in dossier.synthesis],
            "allow_additions": allow_additions, "allow_open_question_changes": allow_questions,
            "editable_coverage_ids": sorted(coverage_ids) if coverage_ids is not None else [q.id for q in discovery.questions],
            "source_usage": usage, "sources": select_context(context, refs),
            "base_hash": digest(dossier.model_dump())}, ensure_ascii=False))
    return prompt


def edit_dossier(folder, name, dossier, discovery, context, config, generate, *, targets, instructions,
                 extra_context=(), allow_additions=True, coverage_ids=None, allow_questions=True, legacy_targets=None):
    options = dict(instructions=instructions, extra_context=extra_context, allow_additions=allow_additions,
                   coverage_ids=coverage_ids, allow_questions=allow_questions)
    prompt = patch_prompt(dossier, discovery, context, config, targets=targets, **options)
    path = folder / f"{name}.json"
    if path.exists() and legacy_targets is not None:
        signature = digest({"prompt": prompt, "schema": DossierPatch.model_json_schema(), "version": PATCH_VERSION})
        saved = json.loads(path.read_text(encoding="utf-8"))
        if saved.get("input_hash") != signature:
            # A pre-ownership receipt may be replayed only if its exact old
            # prompt still matches AND its actual changes obey today's boundary.
            prompt = patch_prompt(dossier, discovery, context, config, targets=legacy_targets, **options)
    patch = cached_call(folder, name, DossierPatch, prompt, generate)
    result = apply_patch(dossier, patch, targets, allow_additions=allow_additions,
                         coverage_ids=coverage_ids if coverage_ids is not None else [q.id for q in discovery.questions],
                         allow_questions=allow_questions)
    write_json(folder / f"{name}_applied.json", {"base_hash": digest(dossier.model_dump()),
               "dossier_hash": digest(result.model_dump()), "updated_ids": [f.id for f in patch.updates],
               "claim_changes": claim_changes(dossier.findings, result.findings),
               "added_ids": [f.id for f in patch.additions], "dossier": result.model_dump()})
    return result


def repair_references(folder, name, dossier, discovery, context, config, generate, *, allowed_ids=None):
    from .research import validate_dossier
    errors = validate_dossier(dossier, discovery, context)
    if errors:
        targets, questions = error_targets(dossier, errors, discovery)
        if allowed_ids is not None and not targets <= set(allowed_ids):
            write_json(folder / f"{name}_errors.json", {"errors": errors})
            raise AppError("Die Belegkorrektur würde Befunde unveränderter Recherchefragen bearbeiten; Prüfdetails sind gespeichert.",
                           code="invalid_evidence", status="blocked")
        # Include available sections of affected sources: a bad anchor can name a
        # nonexistent section, so selecting just that reference cannot repair it.
        affected_sources = {e.reference.split("#")[0] for f in dossier.findings if f.id in targets for e in f.evidence}
        passages = [s for s in context if s["source_id"] in affected_sources]
        if targets and not passages:
            # The erroneous reference may even invent the source ID.
            passages = context
        dossier = edit_dossier(folder, name, dossier, discovery, context, config, generate,
            targets=targets, instructions={"reference_errors": errors}, extra_context=passages,
            allow_additions=False, coverage_ids=questions, allow_questions=False)
        errors = validate_dossier(dossier, discovery, context)
    if errors:
        write_json(folder / f"{name}_errors.json", {"errors": errors})
        raise AppError("Die gezielte Dossierkorrektur enthält noch ungültige Quellenbezüge; Prüfdetails sind gespeichert.",
                       code="invalid_evidence", status="blocked")
    return dossier
