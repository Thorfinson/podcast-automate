"""Bounded, verified foundation research within an already approved episode outline."""
from __future__ import annotations

import copy
import json
import re
from collections import Counter

from typing import Literal

from pydantic import Field

from .prompts import instructions
from .errors import AppError
from .editorial import TEACHING_SCOPE, terminology
from .models import Contract, Identifier, NonEmpty
from .research import source_context
from .research_models import Evidence, Finding, ResearchDiscovery, SourceIndex, admissible
from .evidence_models import ClaimContract, FindingSupport, SourceAssessment
from .research_evidence import EVIDENCE_INSTRUCTIONS, scope_assessments, support_errors, verbatim
from .research_patches import corrected_call
from .sources import canonical_url, clean, import_failure, import_source
from .storage import OPERATIONAL_FIELDS, bound_brief, digest, file_hash, inside, read_yaml, write_json

VERSION = "teaching_research.v1"
# The prompt tag is separate from VERSION: VERSION also binds stored supplement receipts, and a
# wording change must not invalidate the receipts of runs that already hold one.
# v5-terms (2026-10-02): the topic's own terminology rule instead of the machine-learning names in every call.
PROMPT_VERSION = "teaching_research.v5-terms"
MAX_SUPPLEMENTS = 3
# Correction attempts of a supplement answer the check rejected, as in research_patches.cached_call; before,
# one rejected answer ended the script run, and a resume replayed it unchanged (Ontologies, 2026-09-27).
MAX_SUPPLEMENT_REJECTIONS = 2
# The source-wide limits of research.validate_dossier plus a small allowance a supplement may add on top, counted
# with the findings already citing a source. Without the allowance a source the dossier had spent could carry no
# correcting sentence at all, while the review rightly asked for one (Ontologies ep_004, 2026-09-28: 146 of 150
# words spent, "two engineers with at least five years" did not fit); the user chose the allowance.
QUOTED_WORDS = 25 + 10
PARAPHRASED_WORDS = 150 + 40
# Said to the writer of a supplement to an assembled dossier, whose findings do not count against the limits (counted_findings).
ASSEMBLED_ALLOWANCE = ("The findings already in the dossier do not count against these limits here; source_budget "
                       "shows the full allowance of each source for this supplement.")
# The defect a correction the supplement review asked for names; every other rejection failed validate_supplement.
REVIEW_CORRECTION = "Die Prüfung der Ergänzung beanstandet: "
QUOTES = "\"'„“”‚‘’«»"
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
    # Why each issue blocks (foundation_review_basis); every other observation is an advisory kept with the supplement.
    issue_basis: list[Literal["factual_error", "unsupported_claim", "source_contradiction", "previous"]] = Field(default_factory=list)
    advisories: list[NonEmpty] = Field(default_factory=list)


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
            row = {"episode_id": data["episode_id"], "question": gap["question"], "why_needed": gap["why_needed"],
                   "references": list(gap.get("references", []))}
            if row not in rows:
                rows.append(row)
    return rows


def probe_questions(work, entry):
    """The routed corpus-probe gaps of an episode: each question with the sections it must read."""
    rows = [g for g in gaps_in(work) if g["episode_id"] == entry.episode_id and g["references"]]
    return [{"question": g["question"], "references": g["references"]}
            for g in {g["question"]: g for g in rows}.values()]


def binding(config, entry, dossier):
    """What a supplement belongs to: the brief's content, the episode and the dossier. The operational fields
    (storage.OPERATIONAL_FIELDS) stay out, as they do for a resumed run's hash (storage.bound_brief): a raised limit
    or a longer timeout saved while the run waited stopped every resume with invalid_supplement (2026-10-02 review)."""
    brief = {key: value for key, value in config.model_dump().items() if key not in OPERATIONAL_FIELDS}
    return digest({"version": VERSION, "config": brief, "episode": entry.model_dump(), "dossier": dossier.model_dump()})


def bindings(work, config, entry, dossier):
    """The bindings a supplement of this episode may carry: today's, and the earlier one over the whole brief, as it is
    now and as the run started (its project_snapshot.yaml), so a supplement made before 2026-10-02 still counts."""
    snapshot = work / "project_snapshot.yaml"
    started = bound_brief(config, read_yaml(snapshot) if snapshot.is_file() else None, "script")
    return {binding(config, entry, dossier), *(digest({"version": VERSION, "config": brief.model_dump(),
                                                       "episode": entry.model_dump(), "dossier": dossier.model_dump()})
                                              for brief in (config, started))}


def named_question(text, questions):
    """The question an answer or ``remaining_gaps`` entry names, with the reason written after it, else
    ``(None, None)``. The entry must hold the question verbatim; quotation marks around it and a reason
    after it are tolerated (Ontologies, 2026-09-27: every confirmed gap came back as "'question' reason",
    and none of the sixteen counted)."""
    stripped = text.strip()
    known = {question.strip(): question for question in questions}
    if stripped in known:
        return known[stripped], ""
    body = stripped.lstrip(QUOTES)
    for key in sorted(known, key=len, reverse=True):
        rest = body[len(key):] if body.startswith(key) else None
        if rest is not None and (not rest or rest[0] in QUOTES or rest[0].isspace()):
            return known[key], rest.lstrip(QUOTES).strip()
    return None, None


def counted_findings(dossier):
    """The dossier findings a supplement's source-wide limits count. An assembled dossier holds every verified answer
    without word limits per source (question_synthesis.assemble_dossier), so only the supplement's own words count."""
    return [] if dossier.assembled else dossier.findings


def source_budget(context, dossier):
    """Per source of a supplement's context, the quoted and paraphrased words still free under the
    source-wide limits once the findings already citing it are counted, as validate_supplement counts them."""
    quotes, words = {}, {}
    for finding in counted_findings(dossier):
        for evidence in finding.evidence:
            quotes.setdefault(evidence.reference.split("#")[0], set()).add(clean(evidence.excerpt))
        for source_id in {e.reference.split("#")[0] for e in finding.evidence}:
            words[source_id] = words.get(source_id, 0) + len(finding.statement.split())
    return {source["source_id"]: {
                "quoted_words_left": max(0, QUOTED_WORDS - sum(len(q.split()) for q in quotes.get(source["source_id"], ()))),
                "paraphrased_words_left": max(0, PARAPHRASED_WORDS - words.get(source["source_id"], 0))}
            for source in context}


def spent_corrections(rejected, *, review):
    """The correction attempts one kind of rejection has used: the review's, or the check's. Each kind has its own
    MAX_SUPPLEMENT_REJECTIONS, so an answer the review corrected can still fix a limit it broke on the way
    (Ontologies ep_004, 2026-09-28: two review corrections, then 227 of 150 words, and no attempt left)."""
    kinds = [all(error.startswith(REVIEW_CORRECTION) for error in json.loads(path.read_text(encoding="utf-8"))["errors"])
             for path in rejected]
    return sum(kind == review for kind in kinds)


def stuck_supplements(work):
    """Supplements of a script run stopped with one kind of correction spent, and no receipt."""
    stuck = []
    for directory in sorted((work / "teaching").glob("ep_*/supplement*")):
        if not directory.is_dir() or (directory / "receipt.json").exists():
            continue
        rejected = sorted(directory.glob("evidence_rejected_*.json"))
        if max(spent_corrections(rejected, review=True), spent_corrections(rejected, review=False)) >= MAX_SUPPLEMENT_REJECTIONS:
            stuck.append(directory)
    return stuck


def supersede_corrections(directory):
    """Give a stuck supplement fresh correction attempts (run_budget.approve_fresh_attempts): its spent corrections
    move aside unchanged as ``<kind>_superseded_RR_NN.json``. The issues its reviews raised stay its next review's
    previous_issues, so a fresh round does not start the review over."""
    done = [int(path.name.split("_")[2]) for path in directory.glob("evidence_superseded_*.json")]
    round_number = max(done, default=0) + 1
    for kind in ("evidence", "review"):
        for path in sorted(directory.glob(f"{kind}_rejected_*.json")):
            path.replace(directory / f"{kind}_superseded_{round_number:02d}_{path.stem.rsplit('_', 1)[1]}.json")


def short(text, limit=90):
    return text if len(text) <= limit else text[:limit - 1].rstrip() + "…"


def validate_supplement(supplement, questions, entry, context, dossier, probes=()):
    """``probes`` are the routed corpus-probe gaps. Such a gap may stand in ``remaining_gaps``,
    but only when every section the probe found was in the supplement's context: the reader
    must demonstrably have seen them before confirming the gap. Any other remaining gap fails.
    A defect names what is missing or too long, so a correction attempt knows what to change."""
    errors = []
    expected = set(questions)
    sections = {s["reference"]: s["text"] for source in context for s in source["sections"]}
    routed = {row["question"]: row["references"] for row in probes}
    answered = [named_question(q, expected)[0] or q for answer in supplement.explanations for q in answer.questions]
    named = [(gap, named_question(gap, routed)[0]) for gap in supplement.remaining_gaps]
    confirmed = [question for _, question in named if question]
    # Every question at least once. One answered only in part may also stand as a remaining gap: a question that
    # states what is known and what stays open cannot be put one way only (Asimov ep_014, 2026-09-29: the same
    # correct answer rejected three times, with no defect named).
    if (set(answered) | set(confirmed) != expected or not set(answered) <= expected
            or len(confirmed) < len(supplement.remaining_gaps)):
        missing = [q for q in questions if q not in answered and q not in confirmed]
        unknown = [gap for gap, question in named if not question] + [q for q in answered if q not in expected]
        errors.append("Die zusätzlichen Belege beantworten noch nicht alle offenen Erklärfragen."
                      + (" Weder beantwortet noch als Lücke genannt: " + "; ".join(f"„{short(q)}“" for q in missing) + "."
                         if missing else "")
                      + (" Keiner offenen Frage zuzuordnen: " + "; ".join(f"„{short(q)}“" for q in unknown) + "."
                         if unknown else ""))
    if any(not set(routed[gap]) <= sections.keys() for gap in confirmed):
        errors.append("Eine Lücke darf erst bestätigt werden, wenn alle Treffer der Korpusprobe im Quellenkontext gelesen wurden.")
    quotes, words = {}, {}
    for answer in supplement.explanations:
        if not set(answer.finding_ids) <= set(entry.finding_ids):
            errors.append("Die Ergänzung muss zu den bereits geplanten Inhalten der Folge gehören.")
        for evidence in answer.evidence:
            # The dossier's verbatim rule: a line-break hyphen, a ligature or a curly quote of the extraction is no
            # invented quote (Asimov ep_012, 2026-09-29: "institutions and policies" against "pol- icies").
            if evidence.reference not in sections or not verbatim(evidence.excerpt, sections[evidence.reference]):
                errors.append("Eine ergänzende Aussage hat keinen gültigen Textbeleg.")
            quotes.setdefault(evidence.reference.split("#")[0], set()).add(clean(evidence.excerpt))
        for source_id in {e.reference.split("#")[0] for e in answer.evidence}:
            words[source_id] = words.get(source_id, 0) + len(answer.explanation.split())
    # Count earlier excerpts too when a retrieved paper is already in the dossier.
    for finding in counted_findings(dossier):
        for evidence in finding.evidence:
            source_id = evidence.reference.split("#")[0]
            if source_id in quotes:
                quotes[source_id].add(clean(evidence.excerpt))
        for source_id in {e.reference.split("#")[0] for e in finding.evidence} & words.keys():
            words[source_id] += len(finding.statement.split())
    over = sorted(source_id for source_id in quotes.keys() | words.keys()
                  if sum(len(q.split()) for q in quotes.get(source_id, ())) > QUOTED_WORDS
                  or words.get(source_id, 0) > PARAPHRASED_WORDS)
    if over:
        errors.append("Die Rechercheergänzung muss eigenständig und knapper formuliert werden. " + "; ".join(
            f"{source_id}: {sum(len(q.split()) for q in quotes.get(source_id, ()))} von {QUOTED_WORDS} zitierten und "
            f"{words.get(source_id, 0)} von {PARAPHRASED_WORDS} umschriebenen Wörtern, bestehende Befunde eingerechnet"
            for source_id in over) + ".")
    return errors


def supplement_directories(work, entry):
    return sorted(p for p in (work / "teaching" / entry.episode_id).glob("supplement*")
                  if p.is_dir() and re.fullmatch(r"supplement(?:_\d{2})?", p.name))


def merge_pinned(context, pinned):
    """Add already-retrieved sections to a supplement's context without duplicating anything."""
    merged = copy.deepcopy(context)
    for source in pinned:
        existing = next((s for s in merged if s["source_id"] == source["source_id"]), None)
        if existing is None:
            merged.append(copy.deepcopy(source))
            continue
        known = {section["reference"] for section in existing["sections"]}
        existing["sections"].extend(s for s in source["sections"] if s["reference"] not in known)
    return merged


def saved_supplement(directory):
    return FoundationSupplement.model_validate(json.loads((directory / "evidence.json").read_text(encoding="utf-8"))["value"])


def research_foundations(root, work, config, entry, dossier, invoke, *, current_dossier=None, pinned=(), known_sources=None):
    """Run or reload one supplement for the episode's open questions; returns the verified supplement.
    ``known_sources`` is the run's source index: a candidate it already holds is reused, not fetched again."""
    questions = list(dict.fromkeys(g["question"] for g in gaps_in(work) if g["episode_id"] == entry.episode_id))
    if not questions:
        raise AppError("Die offene Erklärfrage fehlt im gespeicherten Auftrag.", code="invalid_research_gap", status="blocked")
    request = {"binding": binding(config, entry, dossier), "questions": questions}
    probes = probe_questions(work, entry)
    if probes:
        request["probes"] = probes
    directories = supplement_directories(work, entry)
    directory = None
    accepted = bindings(work, config, entry, dossier)
    for previous in directories:
        path = previous / "request.json"
        saved = json.loads(path.read_text(encoding="utf-8")) if path.exists() else None
        if saved is not None and saved.get("binding") in accepted and {**saved, "binding": None} == {**request, "binding": None}:
            # The request as it was saved, an earlier binding form included: its files stay as they are.
            directory, request = previous, saved
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
        return saved_supplement(directory)
    known = current_dossier if current_dossier is not None else dossier

    def cached(name, schema, prompt, *, search=False, check=None):
        """``check`` raises for an answer that breaks the contract: it is asked again with the defect named
        (research_patches.corrected_call), and a stored answer it rejects, saved before the check ran, is
        asked again instead of stopping every resume at the same defect."""
        path = directory / f"{name}.json"
        if path.exists():
            saved = json.loads(path.read_text(encoding="utf-8"))
            if saved.get("sha256") != digest(saved.get("value")):
                raise AppError("Gespeicherte Nachrecherche wurde verändert.", code="invalid_supplement", status="blocked")
            result = schema.model_validate(saved["value"])
            try:
                if check is not None:
                    check(result)
                return result
            except AppError:
                path.replace(directory / f"{name}_before_check.json")
        result = corrected_call(invoke, prompt, schema, f"{PROMPT_VERSION}.{name}", check or (lambda answer: None),
                                search=search, research=True)
        write_json(path, {"value": result.model_dump(), "sha256": digest(result.model_dump())})
        return result

    maximum = min(3, config.research_limits.sources)
    # ``cached`` replays a stored answer without comparing its prompt, so a saved supplement survives a changed rule.
    terms = terminology(config.language, config.topic, config.central_question)

    def within_assignment(found):
        if found.topic != config.topic or len(found.candidates) > maximum or not all(
                admissible(c) for c in found.candidates):
            raise AppError(f"Die Nachrecherche überschreitet ihren Auftrag: Thema „{config.topic}“ unverändert übernehmen, "
                           f"höchstens {maximum} Kandidaten, jeder mit Quellentyp, keine Ideenquelle.",
                           code="invalid_supplement", status="blocked")

    discovery = cached("discovery", ResearchDiscovery,
        terms + TEACHING_SCOPE +
        instructions("foundation_discovery", maximum=maximum) + "\n" +
        json.dumps({"topic": config.topic, "language": config.language, "episode": entry.model_dump(),
                    "prior_knowledge": config.prior_knowledge, "questions": questions}, ensure_ascii=False),
        search=True, check=within_assignment)
    index_path = directory / "source_index.json"
    if index_path.exists():
        index = SourceIndex.model_validate_json(index_path.read_text(encoding="utf-8"))
        for source in index.sources:
            if file_hash(inside(root, source.raw_path)) != source.raw_hash:
                raise AppError("Eine ergänzende Quelle wurde verändert.", code="invalid_source_snapshot", status="blocked")
    else:
        index = SourceIndex(sources=[], failures=[])
        seen = set()
        # A page the research already stored keeps its stored text: fetched again it may have changed since, and two
        # versions of one source cannot join the dossier (Ontologies, 2026-09-28: the dbt MetricFlow page).
        stored = {canonical_url(url): source for source in (known_sources.sources if known_sources else [])
                  for url in (source.url, source.final_url) if url}
        for candidate in discovery.candidates:
            try:
                address = canonical_url(candidate.url)
                if address in seen:
                    continue
                seen.add(address)
                if address in stored:
                    index.sources.append(stored[address])
                    continue
                document, _ = import_source(candidate, root, work.name)
                index.sources.append(document)
            except AppError as exc:
                index.failures.append(import_failure(candidate.url, exc))
        if not index.sources:
            raise AppError("Die zusätzlichen Quellen sind derzeit nicht abrufbar. Später fortsetzen.", code="source_download_failed", status="blocked")
        write_json(index_path, index.model_dump())
    context = source_context(index, discovery)
    # Sections the corpus probe already matched are part of the supplement's reading material,
    # so an answer can cite a stored passage instead of insisting the evidence is absent.
    context = merge_pinned(context, pinned)
    write_json(directory / "source_context.json", context)
    data = {"questions": questions, "episode": entry.model_dump(), "language": config.language,
            "findings": [f.model_dump() for f in known.findings if f.id in entry.finding_ids], "sources": context,
            "source_budget": source_budget(context, known)}
    text = terms + TEACHING_SCOPE + EVIDENCE_INSTRUCTIONS + instructions("foundation_supplement")
    if known.assembled:
        text += " " + ASSEMBLED_ALLOWANCE
    rejected = sorted(directory.glob("evidence_rejected_*.json"))
    # What earlier reviews of this supplement found critical; its next review blocks only on these or on new critical defects.
    previous = list(dict.fromkeys(issue for path in [*sorted(directory.glob("review_superseded_*.json")),
                                                      *sorted(directory.glob("review_rejected_*.json"))]
                                  for issue in json.loads(path.read_text(encoding="utf-8"))["value"]["issues"]))
    while True:
        while True:
            payload = data
            if rejected:
                # The last rejected answer and its defects; the rest of the request is asked again unchanged.
                last = json.loads(rejected[-1].read_text(encoding="utf-8"))
                payload = {**data, "rejected_attempt": {"answer": last["value"], "defects": last["errors"]}}
            prompt = (text + (" " + instructions("foundation_supplement_retry") if rejected else "") + "\n" +
                      json.dumps(payload, ensure_ascii=False))
            supplement = cached("evidence", FoundationSupplement, prompt)
            errors = validate_supplement(supplement, questions, entry, context, known, probes)
            if not errors:
                break
            if spent_corrections(rejected, review=False) >= MAX_SUPPLEMENT_REJECTIONS:
                # The attempts are spent: the last answer stays as evidence.json, so a resume stops here again.
                raise AppError(" ".join(dict.fromkeys(errors)), code="teaching_research_required", status="blocked")
            target = directory / f"evidence_rejected_{len(rejected):02d}.json"
            write_json(target, {**json.loads((directory / "evidence.json").read_text(encoding="utf-8")), "errors": errors})
            (directory / "evidence.json").unlink()
            rejected.append(target)
        if not supplement.explanations:
            break
        findings = supplement_findings(supplement)

        def well_formed(answer, previous=tuple(previous)):
            # A shape defect is asked again with the defect named; a substantive non-pass stays the verdict
            # below. Assessments of supplied sources no explanation cites are out of scope, as in a dossier review.
            if dossier.evidence_version:
                support_errors(findings, scope_assessments(answer, findings, context), context)
            # Only a critical defect blocks, and it says which (Asimov, 2026-09-28: wording, an unexplained term and
            # an attribution each stopped the run). A previous issue repeated word for word is its own basis.
            if [i for i in answer.issues if i not in previous] and len(answer.issue_basis) != len(answer.issues):
                raise AppError("Give issue_basis for every issue, in the same order: factual_error, unsupported_claim, "
                               "source_contradiction or previous. Move every other observation to advisories.",
                               code="invalid_supplement", status="blocked")

        review = cached("review", FoundationReview,
            terms + TEACHING_SCOPE + EVIDENCE_INSTRUCTIONS +
            instructions("foundation_review") + " " + instructions("foundation_review_basis") + "\n" +
            json.dumps({**data, "supplement": supplement.model_dump(),
                        "findings": [f.model_dump() for f in findings],
                        **({"previous_issues": previous} if previous else {})}, ensure_ascii=False), check=well_formed)
        # A receipt the review judged only partly supported fails like a critical issue (Asimov ep_012, 2026-09-29:
        # it stopped the run after the review, with no correction).
        unsupported = support_errors(findings, review, context) if dossier.evidence_version else []
        if (not (review.issues or unsupported) or review.scope_change_required
                or spent_corrections(rejected, review=True) >= MAX_SUPPLEMENT_REJECTIONS):
            break
        # A critical defect goes back to the supplement as a correction, like a failed check, with attempts of its
        # own; the review that found it stays beside the answer it rejected.
        target = directory / f"evidence_rejected_{len(rejected):02d}.json"
        write_json(target, {**json.loads((directory / "evidence.json").read_text(encoding="utf-8")),
                            "errors": [REVIEW_CORRECTION + issue for issue in [*review.issues, *unsupported]]})
        (directory / "evidence.json").unlink()
        (directory / "review.json").replace(directory / f"review_rejected_{len(rejected):02d}.json")
        rejected.append(target)
        previous = list(dict.fromkeys([*previous, *review.issues]))
    if not supplement.explanations:
        # Only confirmed corpus-probe gaps (validate_supplement admits no other empty answer): nothing enters the
        # dossier, and the check above already saw every pinned section in the context, so an independent review
        # has nothing to assess. Asked anyway, it assessed eight uncited sources and blocked the run for explaining
        # nothing (Ontologies, 2026-09-28); such a stored review stays beside the empty one that replaces it.
        review = FoundationReview(issues=[], scope_change_required=False)
        path = directory / "review.json"
        if path.exists() and json.loads(path.read_text(encoding="utf-8")).get("value") != review.model_dump():
            path.replace(directory / "review_before_skip.json")
        write_json(path, {"value": review.model_dump(), "sha256": digest(review.model_dump())})
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
    return supplement


def apply_foundations(root, work, config, entries, dossier, context, sources):
    augmented, extended, index = dossier.model_copy(deep=True), copy.deepcopy(context), sources.model_copy(deep=True)
    output_files = []
    for entry, directory in [(e, p) for e in entries for p in supplement_directories(work, e)]:
        receipt_path = directory / "receipt.json"
        if not receipt_path.exists():
            continue
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        if receipt["binding"] not in bindings(work, config, entry, dossier):
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
                validate_supplement(supplement, request["questions"], entry, extra_context, augmented,
                                    request.get("probes", []))):
            raise AppError("Die Nachrecherche hat ihre Belegprüfung nicht bestanden.", code="invalid_supplement", status="blocked")
        if dossier.evidence_version and support_errors(supplement_findings(supplement), review, extra_context):
            raise AppError("Supplement semantic support check failed.", code="invalid_supplement", status="blocked")
        cited = {e.reference.split("#")[0] for answer in supplement.explanations for e in answer.evidence}
        stale = set()
        for source in extra_index.sources:
            if receipt["outputs"].get(source.raw_path) != source.raw_hash:
                raise AppError("Der Originalbeleg der Nachrecherche fehlt.", code="invalid_supplement", status="blocked")
            previous = next((s for s in index.sources if s.id == source.id), None)
            if previous and previous.raw_hash != source.raw_hash:
                if source.id in cited:
                    raise AppError("Eine Quelle hat sich seit der ursprünglichen Recherche geändert.", code="invalid_source_snapshot", status="blocked")
                # A newer copy of a stored page that no explanation cites: the research's snapshot stays.
                stale.add(source.id)
                continue
            if not previous:
                index.sources.append(source)
        for source in extra_context:
            if source["source_id"] in stale:
                continue
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
