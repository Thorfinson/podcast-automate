"""Bounded, verified foundation research within an already approved episode outline."""
from __future__ import annotations

import copy
import json
import re
from collections import Counter

from pydantic import Field

from .prompts import instructions
from .errors import AppError
from .editorial import TERMINOLOGY, TEACHING_SCOPE
from .models import Contract, Identifier, NonEmpty
from .research import source_context
from .research_models import Evidence, Finding, ResearchDiscovery, SourceIndex
from .evidence_models import ClaimContract, FindingSupport, SourceAssessment
from .research_evidence import EVIDENCE_INSTRUCTIONS, support_errors
from .sources import canonical_url, clean, import_failure, import_source
from .storage import digest, file_hash, inside, write_json

VERSION = "teaching_research.v1"
MAX_SUPPLEMENTS = 3
REQUIRED_FILES = ("request.json", "discovery.json", "source_index.json", "source_context.json", "evidence.json", "review.json")


class FoundationExplanation(Contract):
    questions: list[NonEmpty] = Field(min_length=1)
    finding_ids: list[Identifier] = Field(min_length=1)
    explanation: NonEmpty
    evidence: list[Evidence] = Field(min_length=1)
    claim_contract: ClaimContract | None = None


class FoundationSupplement(Contract):
    explanations: list[FoundationExplanation]
    remaining_gaps: list[NonEmpty]


class FoundationReview(Contract):
    issues: list[NonEmpty]
    scope_change_required: bool
    finding_support: list[FindingSupport] = Field(default_factory=list)
    source_assessments: list[SourceAssessment] = Field(default_factory=list)


def supplement_findings(supplement):
    return [Finding(id=f"foundation_{n:03d}", kind="mechanism", statement=a.explanation,
                    evidence=a.evidence, claim_contract=a.claim_contract)
            for n, a in enumerate(supplement.explanations)]


def gaps_in(work):
    rows = []
    for path in sorted((work / "teaching").glob("ep_*/research_needed.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("resolved"):
            continue
        for gap in data.get("questions", []):
            if gap.get("kind", "evidence") != "evidence":
                continue
            row = {"episode_id": data["episode_id"], "question": gap["question"], "why_needed": gap["why_needed"]}
            if row not in rows:
                rows.append(row)
    return rows


def binding(config, entry, dossier):
    return digest({"version": VERSION, "config": config.model_dump(),
                   "episode": entry.model_dump(), "dossier": dossier.model_dump()})


def validate_supplement(supplement, questions, entry, context, dossier):
    errors = []
    expected = set(questions)
    answered = [q for answer in supplement.explanations for q in answer.questions]
    if Counter(answered) != Counter(expected) or supplement.remaining_gaps:
        errors.append("Die zusätzlichen Belege beantworten noch nicht alle offenen Erklärfragen.")
    sections = {s["reference"]: s["text"] for source in context for s in source["sections"]}
    quotes, words = {}, {}
    for answer in supplement.explanations:
        if not set(answer.finding_ids) <= set(entry.finding_ids):
            errors.append("Die Ergänzung muss zu den bereits geplanten Inhalten der Folge gehören.")
        for evidence in answer.evidence:
            if evidence.reference not in sections or clean(evidence.excerpt) not in clean(sections[evidence.reference]):
                errors.append("Eine ergänzende Aussage hat keinen gültigen Textbeleg.")
            quotes.setdefault(evidence.reference.split("#")[0], set()).add(clean(evidence.excerpt))
        for source_id in {e.reference.split("#")[0] for e in answer.evidence}:
            words[source_id] = words.get(source_id, 0) + len(answer.explanation.split())
    # Count earlier excerpts too when a retrieved paper is already in the dossier.
    for finding in dossier.findings:
        for evidence in finding.evidence:
            source_id = evidence.reference.split("#")[0]
            if source_id in quotes:
                quotes[source_id].add(clean(evidence.excerpt))
        for source_id in {e.reference.split("#")[0] for e in finding.evidence} & words.keys():
            words[source_id] += len(finding.statement.split())
    if any(sum(len(q.split()) for q in values) > 25 for values in quotes.values()) or any(v > 150 for v in words.values()):
        errors.append("Die Rechercheergänzung muss eigenständig und knapper formuliert werden.")
    return errors


def supplement_directories(work, entry):
    return sorted(p for p in (work / "teaching" / entry.episode_id).glob("supplement*")
                  if p.is_dir() and re.fullmatch(r"supplement(?:_\d{2})?", p.name))


def research_foundations(root, work, config, entry, dossier, invoke, *, current_dossier=None):
    questions = list(dict.fromkeys(g["question"] for g in gaps_in(work) if g["episode_id"] == entry.episode_id))
    if not questions:
        raise AppError("Die offene Erklärfrage fehlt im gespeicherten Auftrag.", code="invalid_research_gap", status="blocked")
    request = {"binding": binding(config, entry, dossier), "questions": questions}
    directories = supplement_directories(work, entry)
    directory = None
    for previous in directories:
        path = previous / "request.json"
        if path.exists() and json.loads(path.read_text(encoding="utf-8")) == request:
            directory = previous
            break
    if directory is None:
        if len(directories) >= MAX_SUPPLEMENTS:
            raise AppError("Die automatische Nachrecherche hat ihre Grenze von drei Ergänzungen für diese Folge erreicht. "
                           "Die offenen Fragen sind gespeichert; die Ursache muss im Rechercheablauf geprüft werden.",
                           code="teaching_research_required", status="blocked")
        name = "supplement" if not directories else f"supplement_{len(directories) + 1:02d}"
        directory = work / "teaching" / entry.episode_id / name
    request_path = directory / "request.json"
    if request_path.exists() and json.loads(request_path.read_text(encoding="utf-8")) != request:
        raise AppError("Die benötigte Nachrecherche hat sich geändert. Den Erklärumfang im Plan prüfen.", code="teaching_research_required", status="blocked")
    write_json(request_path, request)
    if (directory / "receipt.json").exists():
        return
    known = current_dossier if current_dossier is not None else dossier

    def cached(name, schema, prompt, *, search=False):
        path = directory / f"{name}.json"
        if path.exists():
            saved = json.loads(path.read_text(encoding="utf-8"))
            if saved.get("sha256") != digest(saved.get("value")):
                raise AppError("Gespeicherte Nachrecherche wurde verändert.", code="invalid_supplement", status="blocked")
            return schema.model_validate(saved["value"])
        result = invoke(prompt, schema, f"{VERSION}.{name}", search=search, research=True)
        write_json(path, {"value": result.model_dump(), "sha256": digest(result.model_dump())})
        return result

    maximum = min(3, config.research_limits.sources)
    discovery = cached("discovery", ResearchDiscovery,
        TERMINOLOGY + TEACHING_SCOPE +
        instructions("foundation_discovery", maximum=maximum) + "\n" +
        json.dumps({"topic": config.topic, "language": config.language, "episode": entry.model_dump(),
                    "prior_knowledge": config.prior_knowledge, "questions": questions}, ensure_ascii=False), search=True)
    if discovery.topic != config.topic or len(discovery.candidates) > maximum or not all(c.primary_source for c in discovery.candidates):
        raise AppError("Die Nachrecherche überschreitet ihren Auftrag.", code="invalid_supplement", status="blocked")
    index_path = directory / "source_index.json"
    if index_path.exists():
        index = SourceIndex.model_validate_json(index_path.read_text(encoding="utf-8"))
        for source in index.sources:
            if file_hash(inside(root, source.raw_path)) != source.raw_hash:
                raise AppError("Eine ergänzende Quelle wurde verändert.", code="invalid_source_snapshot", status="blocked")
    else:
        index = SourceIndex(sources=[], failures=[])
        seen = set()
        for candidate in discovery.candidates:
            try:
                address = canonical_url(candidate.url)
                if address in seen:
                    continue
                seen.add(address)
                document, _ = import_source(candidate, root, work.name)
                index.sources.append(document)
            except AppError as exc:
                index.failures.append(import_failure(candidate.url, exc))
        if not index.sources:
            raise AppError("Die zusätzlichen Quellen sind derzeit nicht abrufbar. Später fortsetzen.", code="source_download_failed", status="blocked")
        write_json(index_path, index.model_dump())
    context = source_context(index, discovery)
    write_json(directory / "source_context.json", context)
    data = {"questions": questions, "episode": entry.model_dump(), "language": config.language,
            "findings": [f.model_dump() for f in known.findings if f.id in entry.finding_ids], "sources": context}
    supplement = cached("evidence", FoundationSupplement,
        TERMINOLOGY + TEACHING_SCOPE + EVIDENCE_INSTRUCTIONS +
        instructions("foundation_supplement") + "\n" + json.dumps(data, ensure_ascii=False))
    errors = validate_supplement(supplement, questions, entry, context, known)
    if errors:
        raise AppError(" ".join(dict.fromkeys(errors)), code="teaching_research_required", status="blocked")
    review = cached("review", FoundationReview,
        TERMINOLOGY + TEACHING_SCOPE + EVIDENCE_INSTRUCTIONS +
        instructions("foundation_review") + "\n" +
        json.dumps({**data, "supplement": supplement.model_dump(),
                    "findings": [f.model_dump() for f in supplement_findings(supplement)]}, ensure_ascii=False))
    if dossier.evidence_version:
        errors = support_errors(supplement_findings(supplement), review, context)
        if errors:
            raise AppError(" ".join(errors), code="teaching_research_required", status="blocked")
    if review.issues or review.scope_change_required:
        raise AppError("Die ergänzenden Belege reichen noch nicht für eine verlässliche Erklärung. " +
                       " ".join(review.issues or ["Der Erklärumfang im Inhaltsverzeichnis muss angepasst werden."]),
                       code="teaching_research_required", status="blocked")
    files = [directory / name for name in REQUIRED_FILES] + [inside(root, s.raw_path) for s in index.sources]
    write_json(directory / "receipt.json", {"binding": request["binding"],
        "outputs": {p.relative_to(root).as_posix(): file_hash(p) for p in files if p.name != "receipt.json"}})


def apply_foundations(root, work, config, entries, dossier, context, sources):
    augmented, extended, index = dossier.model_copy(deep=True), copy.deepcopy(context), sources.model_copy(deep=True)
    output_files = []
    for entry, directory in [(e, p) for e in entries for p in supplement_directories(work, e)]:
        receipt_path = directory / "receipt.json"
        if not receipt_path.exists():
            continue
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        if receipt["binding"] != binding(config, entry, dossier):
            raise AppError("Die Nachrecherche passt nicht zum freigegebenen Plan.", code="invalid_supplement", status="blocked")
        required = {(directory / name).relative_to(root).as_posix() for name in REQUIRED_FILES}
        if not required <= receipt.get("outputs", {}).keys():
            raise AppError("Die Nachrecherche ist unvollständig gespeichert.", code="invalid_supplement", status="blocked")
        for relative, sha in receipt["outputs"].items():
            path = inside(root, relative)
            if not path.is_file() or file_hash(path) != sha:
                raise AppError("Gespeicherte Nachrecherche fehlt oder wurde verändert.", code="invalid_supplement", status="blocked")
            output_files.append(path)
        output_files.append(receipt_path)
        supplement = FoundationSupplement.model_validate(json.loads((directory / "evidence.json").read_text(encoding="utf-8"))["value"])
        extra_context = json.loads((directory / "source_context.json").read_text(encoding="utf-8"))
        extra_index = SourceIndex.model_validate_json((directory / "source_index.json").read_text(encoding="utf-8"))
        request = json.loads((directory / "request.json").read_text(encoding="utf-8"))
        review = FoundationReview.model_validate(json.loads((directory / "review.json").read_text(encoding="utf-8"))["value"])
        if (request.get("binding") != receipt["binding"] or review.issues or review.scope_change_required or
                validate_supplement(supplement, request["questions"], entry, extra_context, augmented)):
            raise AppError("Die Nachrecherche hat ihre Belegprüfung nicht bestanden.", code="invalid_supplement", status="blocked")
        if dossier.evidence_version and support_errors(supplement_findings(supplement), review, extra_context):
            raise AppError("Supplement semantic support check failed.", code="invalid_supplement", status="blocked")
        for source in extra_index.sources:
            if receipt["outputs"].get(source.raw_path) != source.raw_hash:
                raise AppError("Der Originalbeleg der Nachrecherche fehlt.", code="invalid_supplement", status="blocked")
            previous = next((s for s in index.sources if s.id == source.id), None)
            if previous and previous.raw_hash != source.raw_hash:
                raise AppError("Eine Quelle hat sich seit der ursprünglichen Recherche geändert.", code="invalid_source_snapshot", status="blocked")
            if not previous:
                index.sources.append(source)
        for source in extra_context:
            previous = next((s for s in extended if s["source_id"] == source["source_id"]), None)
            if previous:
                refs = {s["reference"] for s in previous["sections"]}
                previous["sections"].extend(s for s in source["sections"] if s["reference"] not in refs)
            else:
                extended.append(source)
        for answer in supplement.explanations:
            for finding in augmented.findings:
                if finding.id in answer.finding_ids:
                    if answer.explanation not in finding.statement:
                        finding.statement += "\n\n" + answer.explanation
                    finding.evidence.extend(e for e in answer.evidence if e not in finding.evidence)
                    if answer.claim_contract and answer.claim_contract not in finding.supporting_contracts:
                        finding.supporting_contracts.append(answer.claim_contract)
        assessments = {a.source_id: a for a in augmented.source_assessments}
        assessments.update({a.source_id: a for a in review.source_assessments})
        augmented.source_assessments = list(assessments.values())
    return augmented, extended, index, output_files
