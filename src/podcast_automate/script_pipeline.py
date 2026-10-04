"""Stage implementations of a script run: outline, teaching design, drafting, polishing, reviews, publishing.

``scripting.run_script`` resolves inputs, hashes and approvals, then hands ``ScriptRun.stages()`` to the
shared stage runner. Every stage first checks that the approved call allowance can still finish the
selected episodes, so a run stops before spending money it cannot complete with.
"""
from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context

from .call_activity import CALL_SUBJECT
from .editorial import CONTINUITY, EPISODE_FRAMING, TEACHING_SCOPE, episode_series_context, terminology
from .errors import AppError
from .evidence_models import EVIDENCE_VERSION
from .execution import run_episode_stage
from .models import EpisodeScript, host_labels
from .polishing import HOST_ROLES, polish_dialogue
from .prompts import fragment, instructions
from .research import refund_call, reserve_call, unanswered
from .research_evidence import single_group_findings
from .research_gap_probe import coverage_terms, gap_id, hit_sources, probe, settle, statuses, unread
from .research_ledger import read_value
from .research_patches import MAX_REJECTIONS, corrected_call, re_asked
from .run_budget import effective_limits, teaching_redesigns
from .runner import manifest_path, run_observer
from .script_artifacts import publish_scripts, render_script, script_metrics
from .script_budget import ensure_script_budget
# NOTED_CATEGORIES: review points that stop nothing once repairs are spent; shared with progress and projection.
from .script_checkpoints import NOTED_CATEGORIES, series_adoption
from .script_checks import (RELAXED_REVIEW_VERSIONS, SCRIPT_REVIEW_VERSION, WRITER_TARGET_FACTORS, checked_series_plan,
                            episode_limits, episode_sources, planning_dossier, quotation_errors, research_limits,
                            script_review_signature, validate_script, word_budget, writer_target_factor)
from .script_evidence import (SCRIPT_EVIDENCE_INSTRUCTIONS, is_claim_drift, settle_receipts, source_corrections,
                              validate_claim_checks)
from .script_models import KnowledgeModel, ScriptReview, SeriesPlan, episode_findings
from .series_review import assess_series, load_series_review, require_passing_series, reviewed_scripts
from .storage import atomic_text, digest, file_hash, write_json
from .teaching import (EDITORIAL_REVIEW_VERSION, TeachingPlan, assess_teaching, build_teaching_plan,
                       prerequisite_context)
from .teaching_research import apply_foundations, named_question, research_foundations

SPOKEN_DIALOGUE = fragment("spoken_dialogue")
# research.PLAIN_LANGUAGE without its terminology rule, which the script lane takes from the project (plain_language).
PLAIN_WORDING = fragment("plain_language")
# v9: core and supporting findings, research limits stated where the affected statement is used, the framing duties
# left to episode_framing (the final episode as a whole is the synthesis), no whole series plan in the payload, and
# the project's own terminology rule. The series plan, the script review and both repairs changed with it.
# v10: the computed word budget (script_checks.word_budget) instead of a budget the writer derives itself. v11: for
# Sonnet 5.5 alone its target set above the plan (script_checks.WRITER_TARGET_FACTORS), since Sonnet wrote 71 % of the
# plan it was shown.
WRITE_EPISODE_VERSION = "write_episode.v11-raised-target"
# The correction of a draft that breaks its plan repeats the writing prompt. v3: each correction carries the latest
# attempt and its own defects, a short script the words it has and needs (write_episode).
WRITE_REPAIR_VERSION = "write_episode_repair.v3-latest-draft"
MAX_REVIEW_REPAIRS = 3
# v2: an unbacked claim that the sources lack something is deleted, not reworded (Ontologies, 2026-09-29: each
# repair restated such claims and the next review flagged them again). v4: a limit research_limits names is kept.
REVIEW_REPAIR_VERSION = "script_review_repair.v4-limits"
# Attempts one episode's correction of a series review gets before its evidence check rejects it (repair_series).
SERIES_REPAIR_ATTEMPTS = 2
# A new issue on a segment no repair touched blocks a follow-up review only as one of these.
CRITICAL_BASIS = {"factual_error", "source_contradiction"}
# A point the review files as an advisory is its own verdict that the point does not block, and it stands, except for
# these categories: evidence and scope count as issues wherever the scope reaches them. Until 2026-10-04 every other
# category counted too, in a first review all of them: Ontologies ep_019 spent all three repairs on a closing-structure
# point its reviewer filed as an advisory in every round ("trimming is optional … do not block").
STRICT_CATEGORIES = frozenset({"grounding", "scope"})
# The planner reads the editorial brief, not the whole project config. Its max_episode_minutes (30), the audio part
# length until 2026-10-04 and unused since, was taken by the model for an episode cap: it cut one long explanation
# into several 30-minute episodes (Transformer and Ontologies plans, 2026-10-02).
PLANNING_BRIEF = {"topic", "language", "audience_level", "prior_knowledge", "depth_request", "focus_questions",
                  "excluded_topics", "seed_people", "target_total_minutes", "series_goal"}


def goal_and_recency(config):
    """The series goal and the recency rule for prompts, only when the brief sets them (2026-09-30)."""
    return {key: getattr(config, key) for key in ("series_goal", "recency_months") if getattr(config, key) is not None}


def review_blocks(review):
    return any(issue.category not in NOTED_CATEGORIES for issue in review.issues)


def saved_verdict_stands(version, result):
    """Whether a saved script review verdict holds on resume: one of today's review version, or one of a version
    today's only relaxes (RELAXED_REVIEW_VERSIONS) that blocked nothing. Any other is reviewed again."""
    return version == SCRIPT_REVIEW_VERSION or (version in RELAXED_REVIEW_VERSIONS and result is not None
                                                and not review_blocks(result))


def changed_segments(before, after):
    """Segment IDs whose spoken text or references a repair changed, or that it added."""
    earlier = {s.segment_id: (s.speaker_id, s.text, s.knowledge_refs) for s in before.segments}
    return [s.segment_id for s in after.segments if earlier.get(s.segment_id) != (s.speaker_id, s.text, s.knowledge_refs)]


def follow_up_scope(review, previous, changed):
    """The issues a review after a repair may block on: those on a changed segment or on one a previous issue
    named, and a factual error or source contradiction anywhere. Every other point concerns a segment that
    passed the last review unchanged and becomes an advisory, so the review converges instead of finding new
    details in the same text each round (Ontologies, 2026-09-29: ep_005 reached fifteen reviews, each raising
    grounding points on other untouched segments). The code decides the scope; the review's basis only marks
    what is critical, and a missing basis counts as not critical.

    A whole-episode point (no segment) is in scope only as a repeat: the previous review raised a whole-episode
    point of its category. Until 2026-10-02 every such point was in scope, so a new whole-episode structure point
    in a later review blocked although no earlier review had raised it.

    A point the review itself files as an advisory stays one unless it concerns evidence or scope
    (STRICT_CATEGORIES): only such a point counts as an issue in scope."""
    watched = set(changed) | {key for issue in previous.issues for key in issue.segment_ids}
    episode_wide = {issue.category for issue in previous.issues if not issue.segment_ids}

    def in_scope(issue):
        return bool(set(issue.segment_ids) & watched) if issue.segment_ids else issue.category in episode_wide
    issues, advisories = [], []
    for index, issue in enumerate(review.issues):
        basis = review.issue_basis[index] if index < len(review.issue_basis) else None
        (issues if in_scope(issue) or basis in CRITICAL_BASIS else advisories).append(issue)
    for issue in review.advisories:
        # On a segment the repair touched or a previous issue named, an evidence or scope advisory counts as an issue;
        # any other stays an advisory.
        (issues if in_scope(issue) and issue.category in STRICT_CATEGORIES else advisories).append(issue)
    return review.model_copy(update={"issues": issues, "advisories": advisories})


def failed_teaching(work):
    """The episode whose teaching design stopped after its focused repair, as ``{episode_id, title}``, or None.
    The stop card of ``teaching_design_failed`` offers a new design of exactly this episode."""
    try:
        plan = SeriesPlan.model_validate_json((work / "series_plan.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    for entry in plan.episodes:
        directory = work / "teaching" / entry.episode_id
        checkpoint = directory / "checkpoint.json"
        if (directory / "plan.json").exists() or not checkpoint.exists():
            continue
        try:
            if json.loads(checkpoint.read_text(encoding="utf-8")).get("focused_repair"):
                return {"episode_id": entry.episode_id, "title": entry.title}
        except (OSError, ValueError):
            continue
    return None


def unread_references(row):
    """The hits of a probe row nobody has read yet: all of them until a reader settled some."""
    return row.get("unread_references") or [hit["reference"] for hit in row["hits"]]


class ScriptRun:
    """State of one script run. ``dossier``, ``context`` and ``sources`` grow with foundation research."""

    def __init__(self, *, root, work, config, manifest, adapter, input_hash, research_id, dossier, discovery,
                 sources, context, episode, revision, execution, plan_only, previous_outline, outline_feedback,
                 series_review_version, text_generation, resume, style_notes="", probe_key=None):
        self.root, self.work, self.config, self.manifest, self.adapter = root, work, config, manifest, adapter
        self.input_hash, self.research_id, self.discovery = input_hash, research_id, discovery
        self.base_dossier, self.base_context, self.base_sources = dossier, context, sources
        self.dossier, self.context, self.sources = dossier, context, sources
        self.episode, self.revision, self.execution, self.plan_only = episode, revision, execution, plan_only
        self.previous_outline, self.outline_feedback = previous_outline, outline_feedback
        self.series_review_version, self.text_generation, self.resume = series_review_version, text_generation, resume
        self.central_question = config.central_question or config.topic
        self.style_notes = style_notes
        # The OpenRouter key for the Jev gap probe; only in memory, and only a run that asked for the probe uses it.
        self.probe_key = probe_key
        self._research_limits = None

    # --- shared helpers -------------------------------------------------------------------------

    def limits(self):
        return effective_limits(self.work, self.config.research_limits, self.input_hash)

    def terms(self):
        """The project's terminology rule (editorial.terminology): the machine-learning names only for such a topic.
        Until 2026-10-02 every script prompt named Query, Key and Value, also for the Asimov series."""
        return terminology(self.config.language, self.config.topic, self.central_question)

    def plain_language(self):
        """research.PLAIN_LANGUAGE with the project's terminology rule in place of the machine-learning one."""
        return self.terms() + TEACHING_SCOPE + PLAIN_WORDING

    def research_limits(self):
        """The limits the research run noted for the script (script_checks.research_limits), read once per run."""
        if self._research_limits is None:
            gate = manifest_path(self.root, self.research_id).parent / "research_quality_gate.json"
            saved = json.loads(gate.read_text(encoding="utf-8")) if gate.is_file() else {}
            self._research_limits = research_limits(saved if isinstance(saved, dict) else {}, self.base_dossier)
        return self._research_limits

    def invoke(self, prompt, output_type, version, *, search=False, research=False):
        """One validated answer; a parsed answer the contract rejects is re-asked with the defects named."""
        # The pool applies the saved choice per call; supplementary research follows the subscription rule.
        self.adapter.require_key()

        def once(attempt):
            number = reserve_call(self.work, self.limits(), search=search)
            try:
                return self.adapter.structured(attempt, output_type, self.work / "calls" / f"call_{number:03d}",
                                               prompt_version=version, search=search, research=research)[0]
            except AppError as exc:
                # A call without any model response is not charged; rejected model work stays charged.
                if unanswered(exc):
                    refund_call(self.work, number, search=search)
                raise
            except BaseException:
                refund_call(self.work, number, search=search)
                raise
        return re_asked(once, prompt)

    def selected(self):
        plan = SeriesPlan.model_validate_json((self.work / "series_plan.json").read_text(encoding="utf-8"))
        return plan, [e for e in plan.episodes if not self.episode or e.episode_id == self.episode]

    def teaching_for(self, entry):
        return TeachingPlan.model_validate_json(
            (self.work / "teaching" / entry.episode_id / "plan.json").read_text(encoding="utf-8"))

    def refresh_foundations(self, entries):
        """Apply verified supplementary research to the working dossier; returns the receipt files."""
        self.dossier, self.context, self.sources, supplements = apply_foundations(
            self.root, self.work, self.config, entries, self.base_dossier, self.base_context, self.base_sources)
        return supplements

    def check_budget(self, entries):
        return ensure_script_budget(self.work, self.manifest, self.limits(), entries,
                                    series_review=bool(self.series_review_version) and self.episode is None
                                    and not self.plan_only, root=self.root)

    def stages(self):
        return {"planning": self.planning, "teaching": self.teaching, "writing": self.writing,
                "polishing": self.polishing, "review": self.review, "publish": self.publish}

    # --- planning -------------------------------------------------------------------------------

    def planning(self):
        self.check_budget([])
        config, dossier = self.config, self.dossier
        brief = {key: value for key, value in config.model_dump(mode="json").items() if key in PLANNING_BRIEF}
        limits = self.research_limits()
        prompt = (instructions("series_plan", language=config.language) + " " + self.plain_language() +
                  instructions("series_plan_tail") + "\n" +
                  json.dumps({"brief": {**brief, "central_question": self.central_question},
                              "dossier": planning_dossier(dossier),
                              "research_questions": [q.model_dump() for q in self.discovery.questions],
                              **({"research_limits": limits} if limits else {})}, ensure_ascii=False))
        if self.previous_outline is not None:
            prompt += "\n" + instructions("series_plan_revision") + "\n" + json.dumps(
                {"previous_outline": self.previous_outline, "feedback": self.outline_feedback}, ensure_ascii=False)
        signature = digest({"input": self.input_hash, "previous_outline": self.previous_outline,
                            "feedback": self.outline_feedback})
        plan = checked_series_plan(self.work, prompt, self.invoke, dossier, self.central_question, signature,
                                   allow_legacy=self.resume and self.previous_outline is None,
                                   limit_ids=[limit["limit_id"] for limit in limits])
        if self.episode and self.episode not in {e.episode_id for e in plan.episodes}:
            raise AppError("Gewünschte Folge kommt im Serienplan nicht vor.", code="unknown_episode", status="blocked")
        knowledge = KnowledgeModel(research_run_id=self.research_id, topic=dossier.topic,
            claims=dossier.findings, key_terms=[f.id for f in dossier.findings if f.kind == "definition"],
            mechanisms=[f.id for f in dossier.findings if f.kind == "mechanism"],
            examples=[f.id for f in dossier.findings if f.kind == "example" or f.illustration],
            counterpoints=[f.id for f in dossier.findings if f.kind == "limitation"],
            synthesis=dossier.synthesis, source_assessments=dossier.source_assessments,
            dependencies=plan.dependencies, uncertainties=dossier.open_questions +
            [c.gap for c in dossier.coverage if c.gap] +
            [r.explanation for r in dossier.synthesis if r.resolution == "unresolved"], editorial_priorities=plan.explanation_path)
        write_json(self.work / "series_plan.json", plan.model_dump())
        write_json(self.work / "knowledge_model.json", knowledge.model_dump())
        # The corpus probe is computed here, once per run, but declared by the teaching stage:
        # settling a row changes the file, and a changed planning output would re-run every stage.
        self.run_probes(plan)
        return [self.work / name for name in ("series_plan.json", "knowledge_model.json", "inputs.json",
                                              "script_request.json", "project_snapshot.yaml")]

    # --- teaching design ------------------------------------------------------------------------

    def teaching(self):
        plan, entries = self.selected()
        self.check_budget(entries)
        outputs = []
        for entry in entries:
            directory = self.work / "teaching" / entry.episode_id
            continuity = prerequisite_context(plan, entry, self.work)
            write_json(directory / "continuity.json", continuity)
            while True:
                teaching_sources = episode_sources(entry, self.dossier, self.context, self.sources)
                write_json(directory / "source_context.json", teaching_sources)
                try:
                    self.route_probe_gaps(entry)
                    _, files = build_teaching_plan(self.config, entry, self.dossier, teaching_sources, self.invoke, directory,
                                                   continuity=continuity, series_context=episode_series_context(plan, entry),
                                                   editor_note=self.adopt_redesign(entry, directory))
                    break
                except AppError as exc:
                    if exc.code != "teaching_research_required":
                        raise
                    routed = exc.details.get("gap_ids", [])
                self.research_missing_foundations(entry, entries, routed)
            outputs.extend([*files, directory / "source_context.json", directory / "continuity.json"])
        supplements = self.refresh_foundations(entries)
        return [*outputs, *supplements, self.probe_path()]

    def adopt_redesign(self, entry, directory):
        """The editor's note for a new teaching design of this episode (run_budget.request_teaching_redesign), or None.

        Each request is adopted once: the stopped design, its checkpoint and its focused repair move to
        ``redesign_NN``, so the note starts a fresh design with fresh correction rounds. The note stays part of
        the design prompt on every later resume, which keeps the new checkpoint valid (``redesign.json``)."""
        request = teaching_redesigns(self.work, self.input_hash).get(entry.episode_id)
        if request is None:
            return None
        marker = directory / "redesign.json"
        adopted = json.loads(marker.read_text(encoding="utf-8")) if marker.exists() else []
        if request["requested_at"] not in {row["requested_at"] for row in adopted}:
            archive = directory / f"redesign_{len(adopted) + 1:02d}"
            archive.mkdir(parents=True, exist_ok=True)
            for name in ("checkpoint.json", "focused_repair.json", "review.json", "plan.md", "dismissed_gaps.json"):
                if (directory / name).exists():
                    (directory / name).replace(archive / name)
            write_json(marker, [*adopted, {**request, "archive": archive.name}])
        return request["note"]

    # --- gap probe ------------------------------------------------------------------------------

    def knowledge_gaps(self):
        """Every uncertainty the knowledge model carries into the script lane, keyed stably."""
        knowledge = json.loads((self.work / "knowledge_model.json").read_text(encoding="utf-8"))
        texts = [text for text in knowledge.get("uncertainties", []) if text and text.strip()]
        return {gap_id(text): text for text in dict.fromkeys(texts)}

    def single_group(self, entry):
        """This episode's findings whose evidence all comes from one known research group.

        Rows whose group is unknown stay in the research quality report; the writer and the
        reviewer only see findings they can attribute to a named group.
        """
        findings = [f for f in self.dossier.findings if f.id in entry.finding_ids]
        return [row for row in single_group_findings(findings, self.dossier.source_assessments)
                if row["research_group"]]

    def probe_path(self, entry=None):
        """One probe file per run. ``entry`` is accepted for callers written against the old
        per-episode file and ignored."""
        return self.work / "gap_probes.json"

    def jev_proposals(self, gaps):
        """Jev's candidate sections for every gap, from one scan of the corpus (jev.scan). The answers are kept in
        ``jev_scan.jsonl``, so a stop mid-scan resumes where it was; ``jev_probe.json`` reports progress and cost."""
        from .jev import JevClient, scan
        report = self.work / "jev_probe.json"
        state = {"status": "running", "done": 0, "total": 0}
        write_json(report, state)

        def progress(done, total):
            if done % 50 == 0 or done == total:
                write_json(report, {**state, "done": done, "total": total})
        try:
            found, summary = scan(JevClient(self.probe_key), self.sources, gaps, self.work / "jev_scan.jsonl",
                                  progress=progress)
        except AppError:
            write_json(report, {**state, "status": "stopped"})
            raise
        write_json(report, {**summary, "status": "completed", "gaps": len(gaps),
                            "proposals": sum(len(rows) for rows in found.values())})
        return found

    def episode_source_ids(self, entry):
        """The sources this episode's findings cite; hits elsewhere are not its reading."""
        return {e.reference.split("#")[0] for f in self.dossier.findings if f.id in entry.finding_ids
                for e in f.evidence}

    def run_probes(self, plan=None):
        """Probe the knowledge model's uncertainties once per run; afterwards the saved rows are the truth.

        Each row records ``owner_episodes``, the episodes whose sources hold a hit. A row with
        hits that no episode's sources contain is ``hits_unowned``: nobody in this lane can be
        asked to read those sections, so it is reported at publish, never routed or blocking.
        Hits the research's readers already read are settled (settle_by_research), also in a
        file saved before that rule; settling again changes nothing, so a resume keeps the file.
        """
        path = self.probe_path()
        plan = plan or self.selected()[0]
        owned = {episode.episode_id: self.episode_source_ids(episode) for episode in plan.episodes}
        if path.exists():
            saved = json.loads(path.read_text(encoding="utf-8"))
            rows = self.settle_by_research(saved, owned)
            if rows != saved:
                write_json(path, rows)
            return rows
        rows = []
        gaps = self.knowledge_gaps()
        proposals = None
        if self.execution.jev_probe and gaps:
            try:
                proposals = self.jev_proposals(gaps)
            except AppError as exc:
                # On only by the German project's default: no key means the word search alone, not a stop.
                if exc.code != "openrouter_key_required" or not self.execution.jev_default:
                    raise
                write_json(self.work / "jev_probe.json", {"status": "skipped", "reason": "no_key"})
        for row in probe(self.sources, gaps, gap_terms=coverage_terms(self.dossier), proposals=proposals):
            owners = [eid for eid, sources in owned.items() if hit_sources(row) & sources]
            status = "hits_unowned" if row["hits"] and not owners else row["status"]
            rows.append({**row, "status": status, "owner_episodes": owners})
        rows = self.settle_by_research(rows, owned)
        write_json(path, rows)
        return rows

    def research_reads(self):
        """Every section the research's question readers read; empty for research without a question ledger."""
        path = manifest_path(self.root, self.research_id).parent / "question_research/state.json"
        if not path.exists():
            return set()
        return {ref for row in read_value(path)["tasks"].values() for ref in row.get("read_refs", [])}

    def settle_by_research(self, rows, owned):
        """A hit the research already read is not unread, as in the research lane's own closing probe
        (question_synthesis.probe_declared_gaps). A row keeps only its unread references and the episodes
        holding them; with every hit read it stands as ``hits_read_confirmed``, and with the rest in no
        episode's sources as ``hits_unowned`` (Ontologies, 2026-09-27: 102 of 140 hits had been read by the
        research, yet all 28 gaps counted as unread and 18 went to the first episode's supplement)."""
        reads, settled = None, []
        for row in rows:
            if row["status"] == "hits_unread" and "research_read" not in row:
                reads = self.research_reads() if reads is None else reads
                read = [hit["reference"] for hit in row["hits"] if hit["reference"] in reads]
                if read:
                    row = {**settle(row, read_refs=read), "research_read": read}
                    if row["status"] == "hits_read_confirmed":
                        row["settled_by"] = "research"
                    else:
                        left = {reference.split("#")[0] for reference in row["unread_references"]}
                        row["owner_episodes"] = [eid for eid, sources in owned.items() if left & sources]
                        if not row["owner_episodes"]:
                            row["status"] = "hits_unowned"
            settled.append(row)
        return settled

    def routable_probes(self, entry):
        """Unread rows with an unread hit in this episode's sources: exactly what its supplement can read
        and exactly what its review waits for. A row settled by an earlier episode is not unread."""
        owned = self.episode_source_ids(entry)
        return [row for row in unread(self.run_probes())
                if {reference.split("#")[0] for reference in unread_references(row)} & owned]

    def route_probe_gaps(self, entry):
        """Send gaps with unread corpus hits into the existing supplementary research path.

        Only gaps whose hits sit in this episode's own sources are routed; a series-wide gap
        about another episode's material has no place in this episode's supplement.
        """
        routed = self.routable_probes(entry)
        if not routed:
            return
        directory = self.work / "teaching" / entry.episode_id
        questions = [{"question": row["text"], "kind": "evidence",
                      "why_needed": "Die Korpusprobe hat zu dieser gemeldeten Lücke passende, noch ungelesene "
                                    "Abschnitte gefunden: " + ", ".join(unread_references(row)) +
                                    ". Diese Abschnitte müssen gelesen werden, bevor die Lücke behauptet wird.",
                      "references": unread_references(row)}
                     for row in routed]
        write_json(directory / "research_needed.json", {"episode_id": entry.episode_id, "questions": questions,
                                                        "gap_ids": [row["gap_id"] for row in routed]})
        atomic_text(directory / "research_needed.md", "# Gemeldete Lücken mit ungelesenen Korpustreffern\n\n" +
                    "\n\n".join(f"- {q['question']}\n\n  {q['why_needed']}" for q in questions) + "\n")
        raise AppError("Gemeldete Lücken haben ungelesene Abschnitte im Korpus: " +
                       f"{directory / 'research_needed.md'}",
                       code="teaching_research_required", status="blocked",
                       details={"gap_ids": [row["gap_id"] for row in routed]})

    def pinned_sections(self, gap_ids):
        """The full text of every section the probe matched for the routed gaps and nobody has read yet."""
        wanted = {reference for row in self.run_probes() if row["gap_id"] in set(gap_ids)
                  for reference in unread_references(row)}
        pinned = []
        for source in self.sources.sources:
            sections = [{"reference": f"{source.id}#{s.id}", "text": s.text, "page": s.page}
                        for s in source.sections if f"{source.id}#{s.id}" in wanted]
            if sections:
                pinned.append({"source_id": source.id, "title": source.title, "url": source.final_url,
                               "total_sections": len(source.sections), "sections": sections})
        return pinned

    def settle_probe_gaps(self, entry, gap_ids, supplement):
        """Record what the supplement did with each routed gap; returns the ids it settled.

        A gap the supplement answered is ``resolved``. A gap it lists in ``remaining_gaps`` is
        ``hits_read_confirmed``: ``validate_supplement`` has already checked that every pinned
        section was in the reader's context, so the confirmation is an informed one. A reason the
        supplement wrote after the question is kept as ``confirmation_note``.
        """
        if not gap_ids:
            return []
        texts = [row["text"] for row in self.run_probes() if row["gap_id"] in set(gap_ids)]
        answered = {named_question(q, texts)[0] for answer in supplement.explanations for q in answer.questions}
        confirmed = dict(named_question(gap, texts) for gap in supplement.remaining_gaps)
        rows, settled = [], []
        for row in self.run_probes():
            if row["gap_id"] in set(gap_ids) and row["status"] == "hits_unread":
                # A question answered in part and still named a gap stands as a confirmed gap, not as resolved.
                if row["text"] in answered and row["text"] not in confirmed:
                    row = settle(row, resolved=True)
                elif row["text"] in confirmed:
                    row = settle(row, read_refs=[hit["reference"] for hit in row["hits"]])
                    if confirmed[row["text"]]:
                        row["confirmation_note"] = confirmed[row["text"]]
                if row["status"] != "hits_unread":
                    row["settled_by"] = entry.episode_id
                    settled.append(row["gap_id"])
            rows.append(row)
        write_json(self.probe_path(), rows)
        request = self.work / "teaching" / entry.episode_id / "research_needed.json"
        if request.exists():
            saved = json.loads(request.read_text(encoding="utf-8"))
            if set(saved.get("gap_ids", [])) == set(gap_ids) <= set(settled):
                write_json(request, {**saved, "resolved": True})
        return settled

    def research_missing_foundations(self, entry, entries, routed=()):
        """One bounded supplementary research pass; stop when it adds no evidence.

        ``routed`` are the probe gaps this pass was asked to read. Settling one of them is
        progress in itself, even when the supplement adds no finding because the gap stands.
        """
        previous = digest({"dossier": self.dossier.model_dump(), "context": self.context})
        write_json(self.work / "progress.json", {"phase": "foundation_research", "episode_id": entry.episode_id})
        observer = run_observer.get()
        if observer:
            observer(self.manifest)
        try:
            supplement = research_foundations(self.root, self.work, self.config, entry, self.base_dossier, self.invoke,
                                              current_dossier=self.dossier, pinned=self.pinned_sections(routed),
                                              known_sources=self.sources)
        finally:
            write_json(self.work / "progress.json", {})
            if observer:
                observer(self.manifest)
        self.refresh_foundations(entries)
        settled = self.settle_probe_gaps(entry, list(routed), supplement)
        if not settled and previous == digest({"dossier": self.dossier.model_dump(), "context": self.context}):
            raise AppError("Die Lehrprüfung meldet erneut eine bereits recherchierte Frage. "
                           "Der Abgleich zwischen Belegen und Lehrkonzept muss geprüft werden; "
                           "der Auftrag bleibt gespeichert.", code="teaching_research_required", status="blocked")

    # --- writing --------------------------------------------------------------------------------

    def source_sections(self, entry, sources=None):
        """The source section references a review of ``entry`` is given, and each finding's anchors among them."""
        sources = sources if sources is not None else episode_sources(entry, self.dossier, self.context, self.sources)
        sections = {section["reference"] for document in sources for section in document["sections"]}
        cited = set(episode_findings(entry))
        anchors = {f.id: [e.reference for e in f.evidence if e.reference in sections]
                   for f in self.dossier.findings if f.id in cited}
        return sections, anchors

    def writing_prompt(self, plan, entry, *, budget=True, factor=None):
        """The writing prompt; ``budget=False`` leaves out the word budget (``length``), which gives the prompt as it
        was before 2026-10-04, byte for byte, so write_episode can recognise a draft accepted under it. ``factor`` sets
        the budget's target above the plan; by default the run's writer's (writer_target_factor)."""
        config, dossier = self.config, self.dossier
        if factor is None:
            factor = writer_target_factor(getattr(self.adapter, "text_generation", None))
        design_review = json.loads((self.work / "teaching" / entry.episode_id / "review.json").read_text(encoding="utf-8"))
        # The design review's non-blocking notes (teaching.review_scope); a design without them keeps its prompt.
        advisories = " " + instructions("write_episode_advisories") if design_review.get("advisories") else ""
        length = " " + instructions("write_episode_length") if budget else ""
        cited = set(episode_findings(entry))
        limits = episode_limits(plan, entry, self.research_limits())
        prompt = (instructions("write_episode_opening", language=config.language) + " "
                  + self.plain_language() + SPOKEN_DIALOGUE + CONTINUITY + EPISODE_FRAMING + SCRIPT_EVIDENCE_INSTRUCTIONS +
                  instructions("write_episode") + advisories + length + "\n" +
                  json.dumps({"brief": {"language": config.language, "voices": config.voice_profile,
                                        "host_names": config.host_names,
                                        "audience": config.audience_level, "prior_knowledge": config.prior_knowledge,
                                        "style": config.depth_request, "style_notes": self.style_notes,
                                        **goal_and_recency(config)},
                              "host_roles": HOST_ROLES,
                              # Not the whole plan (2026-10-02: the Asimov finale's writing prompt reached about 780 000
                              # characters): series_context carries the order, roles and questions the prompt uses, the
                              # episode entry its own scenes. What remains is the series' scope and the order its
                              # findings need.
                              "series": {"scope_note": plan.scope_note,
                                         "dependencies": [d.model_dump() for d in plan.dependencies
                                                          if d.before in cited and d.after in cited]},
                              "episode": entry.model_dump(),
                              **({"length": word_budget(entry, factor)} if budget else {}),
                              "series_context": episode_series_context(plan, entry),
                              "prerequisite_context": prerequisite_context(plan, entry, self.work),
                              "teaching_design": self.teaching_for(entry).model_dump(),
                              "teaching_design_review": design_review,
                              "findings": [f.model_dump() for f in dossier.findings if f.id in episode_findings(entry)],
                              "single_group_findings": self.single_group(entry),
                              "synthesis": [r.model_dump() for r in dossier.synthesis if set(r.finding_ids) & set(episode_findings(entry))],
                              **({"research_limits": limits} if limits else {}),
                              "sources": episode_sources(entry, dossier, self.context, self.sources)}, ensure_ascii=False))
        if self.revision:
            prompt += ("\n" + instructions("write_episode_revision") + "\n" +
                       json.dumps(self.revision, ensure_ascii=False))
        return prompt

    def script_errors(self, draft, entry):
        """validate_script, and for an assembled dossier the quotation rule over the passages the writer was given:
        at most 25 words of one source per episode (script_checks.quotation_errors)."""
        errors = validate_script(draft, entry)
        if self.dossier.assembled:
            errors += quotation_errors(draft, episode_sources(entry, self.dossier, self.context, self.sources))
        return errors

    def script_defects(self, draft, entry, errors_file, message):
        """Raise for a rewritten script that breaks its plan, naming every defect so the correction can fix it."""
        errors = self.script_errors(draft, entry)
        if errors:
            write_json(errors_file, errors)
            raise AppError(message + " " + " ".join(errors), code="invalid_script", status="blocked")

    def write_episode(self, plan, entry):
        destination = self.work / "drafts" / f"{entry.episode_id}.json"
        stamp = destination.with_suffix(".checkpoint.json")
        prompt = self.writing_prompt(plan, entry)
        signature = digest({"input": self.input_hash, "prompt": prompt})
        if stamp.exists() and destination.exists():
            saved = json.loads(stamp.read_text(encoding="utf-8"))
            if saved == {"input_hash": signature, "sha256": file_hash(destination)}:
                return [destination, stamp]
            # A draft accepted under another word budget, or before there was one, stands while it passes every check:
            # the budget only guides the writer toward the length validate_script holds a draft to. So neither the
            # budget's arrival (the Transformer run had 13 accepted drafts on 2026-10-04) nor a text switch to a writer
            # with another target writes accepted drafts anew.
            prompts = [self.writing_prompt(plan, entry, budget=False),
                       *(self.writing_prompt(plan, entry, factor=f) for f in {1.0, *WRITER_TARGET_FACTORS.values()})]
            if any(saved == {"input_hash": digest({"input": self.input_hash, "prompt": other}),
                             "sha256": file_hash(destination)} for other in prompts) and not self.script_errors(
                    EpisodeScript.model_validate_json(destination.read_text(encoding="utf-8")), entry):
                return [destination, stamp]
        draft = self.invoke(prompt, EpisodeScript, WRITE_EPISODE_VERSION)
        errors, repairs = self.script_errors(draft, entry), 0
        while errors:
            # Each correction reworks the latest attempt against its own defects, as corrected_call's re-asks do not:
            # they repeat the first draft. Transformer, 2026-10-03: ep_005, 40% short, came back 32, 27 and 33% short,
            # and an ep_006 attempt one finding ID from passing gave way to two that fell short again.
            if repairs:
                write_json(self.work / f"{entry.episode_id}_script_errors.json", errors)
            if repairs > MAX_REJECTIONS:
                raise AppError("Skript verletzt Struktur- oder Quellenzuordnung. " + " ".join(errors) +
                               f" Der Aufruf wurde {repairs} Mal mit Korrekturhinweis wiederholt; die "
                               "abgewiesenen Antworten liegen bei den Aufrufen.", code="invalid_script", status="blocked")
            draft = self.invoke(prompt + "\n" + instructions("write_episode_repair") + "\n" + json.dumps(
                {"errors": errors, "draft": draft.model_dump()}, ensure_ascii=False), EpisodeScript, WRITE_REPAIR_VERSION)
            errors, repairs = self.script_errors(draft, entry), repairs + 1
        write_json(destination, draft.model_dump())
        write_json(stamp, {"input_hash": signature, "sha256": file_hash(destination)})
        return [destination, stamp]

    def writing(self):
        plan, entries = self.selected()
        self.check_budget(entries)
        return run_episode_stage(entries, lambda entry: self.write_episode(plan, entry),
                                 workers=self.execution.text_workers, work=self.work, stage="writing")

    # --- polishing ------------------------------------------------------------------------------

    def polish_episode(self, plan, entry):
        original = EpisodeScript.model_validate_json(
            (self.work / "drafts" / f"{entry.episode_id}.json").read_text(encoding="utf-8"))
        folder = self.work / "polishing" / entry.episode_id
        candidate, files = polish_dialogue(self.config, entry, original, self.teaching_for(entry),
                                           self.invoke, folder, self.script_errors,
                                           series_context=episode_series_context(plan, entry),
                                           prerequisite_context=prerequisite_context(plan, entry, self.work),
                                           style_notes=self.style_notes)
        atomic_text(folder / "before.md", render_script(original, host_labels(self.config)))
        atomic_text(folder / "after.md", render_script(candidate, host_labels(self.config)))
        return [*files, folder / "before.md", folder / "after.md"]

    def polishing(self):
        plan, entries = self.selected()
        self.check_budget(entries)
        return run_episode_stage(entries, lambda entry: self.polish_episode(plan, entry),
                                 workers=self.execution.text_workers, work=self.work, stage="polishing")

    # --- reviews --------------------------------------------------------------------------------

    def review_episode(self, plan, entry):
        config, dossier, work = self.config, self.dossier, self.work
        # A gap whose corpus hits nobody read must not be carried into a published script.
        # The teaching stage routes such gaps; reaching the review with one is a defect. The
        # guard has the routing scope: hits in sources this episode does not use are not its
        # reading, and blocking on them could never be resolved here.
        probes = self.run_probes()
        blocking = self.routable_probes(entry)
        if blocking:
            raise AppError("Gemeldete Lücken haben ungelesene Abschnitte im Korpus: " +
                           "; ".join(row["text"] for row in blocking),
                           code="research_gap_unread", status="blocked")
        draft_file = work / "polishing" / entry.episode_id / "script.json"
        original_draft = json.loads((work / "drafts" / f"{entry.episode_id}.json").read_text(encoding="utf-8"))
        draft = EpisodeScript.model_validate_json(draft_file.read_text(encoding="utf-8"))
        checkpoint = work / "reviews" / f"{entry.episode_id}_checkpoint.json"
        signature = script_review_signature(self.input_hash, file_hash(draft_file), plan, entry, work)
        # ``previous``: the draft and review before the last repair, which scope the review after it.
        # ``passed``: the latest draft whose review left nothing blocking, kept if a later repair breaks it.
        result, repairs, previous, passed, teaching_pending = None, 0, None, None, False
        if checkpoint.exists():
            saved = json.loads(checkpoint.read_text(encoding="utf-8"))
            if saved.get("input_hash") == signature:
                draft = EpisodeScript.model_validate(saved["draft"])
                repairs = saved["repairs"]
                previous, passed = saved.get("previous"), saved.get("passed")
                result = ScriptReview.model_validate(saved["review"]) if saved["review"] else None
                teaching_pending = bool(saved.get("teaching_pending"))
                # Reassess an older verdict after a review-policy fix, keeping the
                # latest corrected script and consumed repair allowance intact.
                if saved.get("editorial_review_version") != EDITORIAL_REVIEW_VERSION:
                    result = None
                if not saved_verdict_stands(saved.get("script_review_version"), result):
                    result = None
                if dossier.evidence_version and saved.get("evidence_review_version") != EVIDENCE_VERSION:
                    result = None
                if self.script_errors(draft, entry):
                    raise AppError("Gespeicherter Review-Entwurf ist ungültig.", code="invalid_script", status="blocked")

        def save(pending=False):
            write_json(checkpoint, {"input_hash": signature, "draft": draft.model_dump(),
                                   "review": result.model_dump() if result else None, "repairs": repairs,
                                   "editorial_review_version": EDITORIAL_REVIEW_VERSION,
                                   "script_review_version": SCRIPT_REVIEW_VERSION,
                                   "evidence_review_version": EVIDENCE_VERSION,
                                   "previous": previous, "passed": passed,
                                   **({"teaching_pending": True} if pending else {})})

        def taught(reviewed):
            """A review that found nothing is joined by the teaching assessment, whose points are repaired alike."""
            nonlocal passed
            if not reviewed.issues:
                teaching_issues, _, _ = self.assess_episode_teaching(plan, entry, draft)
                reviewed.issues.extend(teaching_issues)
            if not review_blocks(reviewed):
                passed = {"draft": draft.model_dump(), "review": reviewed.model_dump()}
            return reviewed

        def check():
            nonlocal result
            follow_up = None
            if previous:
                follow_up = (ScriptReview.model_validate(previous["review"]),
                             changed_segments(EpisodeScript.model_validate(previous["draft"]), draft))
            reviewed = self.review_script(plan, entry, draft, original_draft, probes, follow_up=follow_up)
            if not reviewed.issues:
                # Kept before the teaching assessment starts, so a stop during it does not ask this review again
                # (found by stopping a run at each of its calls, 2026-09-29).
                result = reviewed
                save(pending=True)
            return taught(reviewed.model_copy(deep=True))

        if result is not None:
            try:
                drift = validate_claim_checks(result, draft, dossier.findings, required=bool(dossier.evidence_version),
                                              sections=self.source_sections(entry)[0])
            except AppError:
                # Saved before its check ran: reviewed again instead of stopping every resume here.
                result = None
            else:
                for issue in drift:
                    if issue not in result.issues and issue not in result.advisories:
                        result.issues.append(issue)
                if teaching_pending:
                    result = taught(result)
                    save()
        if result is None:
            result = check()
            save()
        while result.issues and repairs < MAX_REVIEW_REPAIRS:
            previous = {"draft": draft.model_dump(), "review": result.model_dump()}
            draft = corrected_call(self.invoke, self.writing_prompt(plan, entry) +
                "\n" + instructions("script_review_repair") + "\n" +
                json.dumps({"draft": draft.model_dump(), "review": result.model_dump()}, ensure_ascii=False),
                EpisodeScript, REVIEW_REPAIR_VERSION,
                lambda answer: self.script_defects(answer, entry, work / f"{entry.episode_id}_review_errors.json",
                                                   "Überarbeitetes Skript verletzt die Quellenzuordnung oder Struktur."))
            repairs += 1
            result = None
            save()
            result = check()
            save()
        if review_blocks(result) and passed:
            # A repair made for clarity or depth points broke the evidence of a draft that had passed it: that
            # draft stands with its points, and the set-aside draft and review are kept beside it.
            write_json(work / "reviews" / f"{entry.episode_id}_kept_draft.json",
                       {"set_aside": {"draft": draft.model_dump(), "review": result.model_dump()}, "repairs": repairs})
            draft, result = EpisodeScript.model_validate(passed["draft"]), ScriptReview.model_validate(passed["review"])
            save()
        # A correction the series review adopted for exactly this draft is what stands (series_adoption).
        adopted = series_adoption(work, entry.episode_id, draft.model_dump())
        report = work / "reviews" / f"{entry.episode_id}.json"
        write_json(report, adopted["review"] if adopted else result.model_dump())
        # Repairs spent and only clarity, depth or dialogue left: the script stands, the points stay in its review
        # report, and the user reads them before approving audio. Grounding, scope and structure keep stopping the
        # run (the user's choice, 2026-09-29: Ontologies episodes 2 and 4 held only listener-clarity points).
        # Advisories, the points a follow-up review found outside its scope, are reported the same way.
        accepted = bool(result.issues) and not review_blocks(result)
        if review_blocks(result):
            raise AppError("Skriptreview meldet weiterhin Einwände; Reviewbericht prüfen.",
                           code="script_review_failed", status="blocked")
        if result.issues or result.advisories:
            write_json(work / "reviews" / f"{entry.episode_id}_accepted_notes.json",
                       [i.model_dump() for i in [*result.issues, *result.advisories]])
        reviewed_file = work / "reviewed" / f"{entry.episode_id}.json"
        teaching_issues, teaching_report, teaching_outputs = self.assess_episode_teaching(plan, entry, draft)
        if teaching_issues and not (accepted and all(issue.category in NOTED_CATEGORIES for issue in teaching_issues)):
            raise AppError("Lehrprüfung nicht bestanden.", code="teaching_review_failed", status="blocked")
        teaching_report_file = work / "reviews" / f"{entry.episode_id}_teaching.json"
        write_json(teaching_report_file, teaching_report)
        write_json(reviewed_file, adopted["draft"] if adopted else draft.model_dump())
        return [reviewed_file, report, teaching_report_file, *teaching_outputs]

    def review_script(self, plan, entry, draft, original_draft, probes, follow_up=None):
        """One evidence-bound script review call, with its deterministic claim checks. ``follow_up`` is the review
        before a repair and the segments the repair changed; the review then blocks only within follow_up_scope."""
        config, dossier, work = self.config, self.dossier, self.work
        required = bool(dossier.evidence_version)
        ids = {s.segment_id for s in draft.segments}
        sources = episode_sources(entry, dossier, self.context, self.sources)
        sections, anchors = self.source_sections(entry, sources)

        def well_formed(answer):
            validate_claim_checks(answer, draft, dossier.findings, required=required, sections=sections)
            unknown = sorted({key for issue in [*answer.issues, *answer.advisories] for key in issue.segment_ids} - ids)
            if unknown:
                raise AppError("Review verweist auf unbekannte Segmente: " + ", ".join(unknown) + ".",
                               code="invalid_model_output", status="blocked")
            # A previous issue repeated word for word is its own basis; any new one needs every issue's basis.
            fresh = [issue for issue in answer.issues if follow_up and issue not in follow_up[0].issues]
            if fresh and len(answer.issue_basis) != len(answer.issues):
                raise AppError("Give issue_basis for every issue, in the same order: previous, changed, factual_error "
                               "or source_contradiction. Move every other point about an unchanged segment to advisories.",
                               code="invalid_model_output", status="blocked")

        payload = {"brief": {"audience": config.audience_level, "depth": config.depth_request,
                             "style_notes": self.style_notes, **goal_and_recency(config)},
                   "host_roles": HOST_ROLES, "original_draft": original_draft,
                   "metrics": script_metrics(draft), "episode": entry.model_dump(), "script": draft.model_dump(),
                   "series_context": episode_series_context(plan, entry),
                   "prerequisite_context": prerequisite_context(plan, entry, work),
                   "gap_probes": statuses(probes),
                   "single_group_findings": self.single_group(entry),
                   "findings": [f.model_dump() for f in dossier.findings if f.id in episode_findings(entry)],
                   "synthesis": [r.model_dump() for r in dossier.synthesis if set(r.finding_ids) & set(episode_findings(entry))],
                   "sources": sources}
        limits = episode_limits(plan, entry, self.research_limits())
        if limits:
            # The limits the writer was asked to state back such a statement, as a gap probe backs an absence claim.
            payload["research_limits"] = limits
        task, version = instructions("script_review"), SCRIPT_REVIEW_VERSION
        if follow_up:
            # A drift issue is judged again by this review's claim check of its segment, not handed over to be reported
            # again: copied back as a previous issue, a correct segment stayed blocked round after round (2026-10-03).
            payload["previous_issues"] = [issue.model_dump() for issue in follow_up[0].issues if not is_claim_drift(issue)]
            payload["changed_segments"] = follow_up[1]
            task, version = task + " " + instructions("script_review_followup"), version + "+followup"
        # Two receipt slips are read as meant (settle_receipts) instead of re-asking the whole review.
        reviewed = corrected_call(lambda *args, **kwargs: settle_receipts(self.invoke(*args, **kwargs), draft, anchors),
            self.terms() + TEACHING_SCOPE + CONTINUITY + EPISODE_FRAMING + SCRIPT_EVIDENCE_INSTRUCTIONS +
            task + "\n" + json.dumps(payload, ensure_ascii=False),
            ScriptReview, version, well_formed)
        # The drift receipts become issues; well_formed accepted their shape, so this cannot raise.
        reviewed.issues.extend(validate_claim_checks(reviewed, draft, dossier.findings, required=required,
                                                     sections=sections))
        reviewed.limitations.extend(note for note in source_corrections(reviewed) if note not in reviewed.limitations)
        if follow_up:
            return follow_up_scope(reviewed, *follow_up)
        # A first review has no scope: an evidence or scope point it set aside as an advisory counts as an issue. Any
        # other advisory is the review's own verdict that the point does not block; it is reported as a note.
        strict = [issue for issue in reviewed.advisories if issue.category in STRICT_CATEGORIES]
        return reviewed.model_copy(update={"issues": [*reviewed.issues, *strict],
                                           "advisories": [issue for issue in reviewed.advisories
                                                          if issue.category not in STRICT_CATEGORIES],
                                           "issue_basis": []})

    def repair_series(self, plan, grouped):
        """Rewrite the segments a failing series check cites, then review the result again.

        Three calls per affected episode: one repair, one script review, and the shared series
        re-check. Polishing and the teaching review are not repeated because the edit is bounded
        to named segments within unchanged objectives.

        An adopted repair replaces both ``reviewed/<ep>.json`` and ``reviews/<ep>.json``; the
        review of the text before the repair is kept as ``reviews/<ep>_before_series_repair.json``,
        and ``reviews/<ep>_series_adopted.json`` binds the correction to the episode review's draft.
        A rejected repair changes neither and is saved as ``reviews/<ep>_series_repair_rejected.json``.
        """
        entries = {entry.episode_id: entry for entry in plan.episodes}
        tasks = [(entries[episode_id], issues) for episode_id, issues in grouped.items() if episode_id in entries]

        def correct(entry, issues):
            """One episode's correction and its scoped review; the caller decides whether it is adopted."""
            episode_id = entry.episode_id
            token = CALL_SUBJECT.set(episode_id)
            try:
                reviewed_file = self.work / "reviewed" / f"{episode_id}.json"
                draft = EpisodeScript.model_validate_json(reviewed_file.read_text(encoding="utf-8"))
                review = ScriptReview(issues=issues, limitations=[
                    "Cross-episode correction from the series review; the episode's own review passed before."])
                probes = json.loads(self.probe_path(entry).read_text(encoding="utf-8")) if self.probe_path(entry).exists() else []
                # A correction whose evidence check still objects gets one more attempt against those objections, as the
                # episode review's repairs do (Ontologies ep_008, 2026-09-29: the single attempt restated an absence claim
                # the check then named with its fix, and the whole series stopped).
                asked, current = review, draft
                for _ in range(SERIES_REPAIR_ATTEMPTS):
                    # Kept per round (a fresh-attempts approval sets the round aside and starts a new one), so a stop
                    # during the check that follows does not ask the correction again.
                    kept = self.work / "reviews" / "series_corrections" / (
                        f"{episode_id}_{len(list(self.work.glob('series_repair_superseded_*.json'))):02d}_"
                        f"{digest({'draft': current.model_dump(), 'review': asked.model_dump()})[:16]}.json")
                    if kept.exists():
                        repaired = EpisodeScript.model_validate_json(kept.read_text(encoding="utf-8"))
                    else:
                        repaired = corrected_call(self.invoke, self.writing_prompt(plan, entry) +
                            "\n" + instructions("script_review_repair") + "\n" +
                            json.dumps({"draft": current.model_dump(), "review": asked.model_dump()}, ensure_ascii=False),
                            EpisodeScript, REVIEW_REPAIR_VERSION,
                            lambda answer: self.script_defects(answer, entry, self.work / f"{episode_id}_series_repair_errors.json",
                                                               "Die Korrektur der Serienprüfung verletzt die Quellenzuordnung oder Struktur."))
                        write_json(kept, repaired.model_dump())
                    # Scoped like every review after a repair: the issues it answered and the segments it changed.
                    checked = self.review_script(plan, entry, repaired, draft.model_dump(), probes,
                                                 follow_up=(asked, changed_segments(current, repaired)))
                    if not review_blocks(checked):
                        break
                    # The next attempt answers the check's objections and keeps the series issue it was made for;
                    # until 2026-10-02 it saw only the objections, and a correction that satisfied them lost the point.
                    asked, current = checked.model_copy(update={"issues": [
                        *checked.issues, *(issue for issue in review.issues if issue not in checked.issues)]}), repaired
                return repaired, checked
            finally:
                CALL_SUBJECT.reset(token)

        # The episodes of one round are corrected at once in a parallel run (2026-09-29: one after another, a round
        # over five episodes took about an hour). Every correction finishes before any outcome is written.
        outcomes = {}
        workers = min(self.execution.text_workers, len(tasks)) or 1
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="series") as pool:
            futures = {entry.episode_id: pool.submit(copy_context().run, correct, entry, issues) for entry, issues in tasks}
            for episode_id, future in futures.items():
                try:
                    outcomes[episode_id] = future.result()
                except Exception as exc:  # noqa: BLE001 -- kept until the others are adopted, then raised
                    outcomes[episode_id] = exc
        changed, rejections = False, []
        for episode_id, outcome in outcomes.items():
            if isinstance(outcome, Exception):
                continue
            repaired, checked = outcome
            if review_blocks(checked):
                # The rejected text is not adopted: ``reviewed/`` and ``reviews/`` keep the pair
                # the episode review passed, and the rejection is saved next to them.
                rejected = self.work / "reviews" / f"{episode_id}_series_repair_rejected.json"
                write_json(rejected, {"review": checked.model_dump(), "draft": repaired.model_dump()})
                rejections.append(rejected.relative_to(self.root).as_posix())
                continue
            # ``reviews/<ep>.json`` is what publish reports next to the script hash, so it must
            # judge the text that is published. The review of the pre-repair text stays beside it.
            review_file = self.work / "reviews" / f"{episode_id}.json"
            before = self.work / "reviews" / f"{episode_id}_before_series_repair.json"
            if not before.exists():
                before.write_bytes(review_file.read_bytes())
            # Bound to the episode review's own final draft, so a resume of the review stage keeps the correction.
            base = json.loads((self.work / "reviews" / f"{episode_id}_checkpoint.json").read_text(encoding="utf-8"))["draft"]
            write_json(self.work / "reviews" / f"{episode_id}_series_adopted.json",
                       {"base": digest(base), "draft": repaired.model_dump(), "review": checked.model_dump()})
            write_json(review_file, checked.model_dump())
            write_json(self.work / "reviewed" / f"{episode_id}.json", repaired.model_dump())
            changed = True
        failure = next((outcome for outcome in outcomes.values() if isinstance(outcome, Exception)), None)
        # A rejection is the round's decision (series_review.resumable) and goes first. Raised after another episode's
        # timeout, it was forgotten: the resume checked the kept correction again and could adopt it unasked (2026-10-02).
        if failure is not None and not rejections:
            raise failure
        if rejections:
            raise AppError("Die Korrektur der Serienprüfung hat die Belegprüfung nicht bestanden; "
                           f"{'der Bericht ist' if len(rejections) == 1 else 'die Berichte sind'} gespeichert: "
                           f"{', '.join(rejections)}. "
                           "„Mit neuen Anläufen fortsetzen“ prüft die Serie mit dem heutigen Stand neu und gibt der "
                           "Korrektur eine neue Runde.", code="script_review_failed", status="blocked")
        return reviewed_scripts(self.work, plan, self.episode) if changed else None

    def assess_episode_teaching(self, plan, entry, draft):
        return assess_teaching(draft, self.teaching_for(entry), self.invoke,
                               self.work / "reviews" / "teaching" / entry.episode_id,
                               audience=self.config.audience_level, prior_knowledge=self.config.prior_knowledge,
                               depth=self.config.depth_request, series_context=episode_series_context(plan, entry),
                               parallel=self.execution.text_workers > 1, series_goal=self.config.series_goal,
                               language=self.config.language)

    def review(self):
        plan, entries = self.selected()
        self.check_budget(entries)
        outputs = run_episode_stage(entries, lambda entry: self.review_episode(plan, entry),
                                    workers=self.execution.text_workers, work=self.work, stage="review")
        if self.series_review_version:
            outputs.extend(assess_series(self.work, self.config, plan, reviewed_scripts(self.work, plan, self.episode),
                                         self.input_hash, self.invoke,
                                         repair=lambda grouped: self.repair_series(plan, grouped)))
        return outputs

    # --- publishing -----------------------------------------------------------------------------

    def publish(self):
        plan, entries = self.selected()
        series_report = None
        if self.series_review_version:
            scripts = reviewed_scripts(self.work, plan, self.episode)
            series_report = load_series_review(self.work, plan, scripts, self.input_hash)
            require_passing_series(series_report)
        return publish_scripts(self.root, self.work, plan=plan, entries=entries, dossier=self.dossier, sources=self.sources,
            config=self.config, teaching_for=self.teaching_for, research_id=self.research_id, manifest=self.manifest,
            input_hash=self.input_hash, text_generation=self.text_generation, series_report=series_report)
