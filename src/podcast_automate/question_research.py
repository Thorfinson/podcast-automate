"""Question-driven research with explicit reading, durable answers and bounded recovery.

``QuestionResearch`` owns the checksummed ledger in ``runs/<run_id>/question_research/state.json``
and drives two phases that live in their own modules: per-task answering
(:mod:`question_answering`) and synthesis with audit (:mod:`question_synthesis`).

State layout (all JSON-serialisable):

- ``plan``: the fixed ``QuestionPlan``; ``tasks``: one row per task id as created by
  ``question_scope.pending_task`` (status, step, read_refs, answer, feedback, reopenings, ...).
- ``phase``: ``awaiting_plan_approval`` (only with a plan gate) → ``questions`` → ``synthesis`` →
  ``audit`` → ``completed``, or ``blocked``.
- ``call_timings``: wall-clock seconds per answered call with the task it served; ``plan_caps`` and
  ``plan_revisions``: the task caps an approval asked for and the plans made under them.
- ``active_tasks``: the tasks being answered right now, in plan order; several when the project's
  text execution is ``parallel`` and independent tasks run side by side.
- ``seed_dossier``/``dirty_tasks``/``finding_owners``: which findings a later batch may edit.
- ``objections``: audit objections keyed by a stable id, closed only by a passing audit.
- ``accepted_gap`` on a task row: the user's explicit approval to finish without that task. The
  dossier then records the gap; objections that only concern accepted gaps no longer block.

Concurrency: with ``workers > 1`` the task loop runs independent tasks in a thread pool. One
re-entrant lock guards the ledger: a worker holds it whenever it is not inside a model call (row
edits, ``save``, timings, gap probes, source attempts, the progress callback) and steps out of it
only for the call itself (``generate``). Workers mutate only their own row; a failure in one worker
stops the others at their next step boundary, the state is saved, and the first error is raised.
"""
from __future__ import annotations

import json
import threading
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from contextlib import contextmanager
from contextvars import copy_context

from .editorial import TERMINOLOGY, TEACHING_SCOPE
from .errors import AppError
from .evidence_models import EVIDENCE_VERSION
from .prompts import instructions
from .question_answering import TaskResearchMixin, answer_errors, read_context, review_passes
from .question_budget import (SOURCE_LABELS, affordable_tasks, budget_projection, expected_calls_per_task,
                              plan_projection, plan_review_message, run_timings)
from .question_dependencies import ordered_tasks, prerequisite_answers
from .question_scope import SCOPE_INSTRUCTIONS, QuestionScopeReview, pending_task, scoped_plan
from .question_sources import restore_attempts
from .question_synthesis import PROMPT_GENERATION, SynthesisMixin
from .research_evidence import support_errors
from .research_gap_probe import coverage_terms, gap_id, probe
from .research_ledger import (CALL_VERSION, VERSION, bootstrap_legacy, check_sources, load_index, public_ledger,
                              read_value, reopenable, save_index, save_value)
from .research_models import ResearchDiscovery, ResearchDossier
from .research_patches import cached_call
from .research_quality import quality_brief, requirements_for
from .research_reader import SourceReader
from .research_tasks import AnswerReview, QuestionAnswer, QuestionPlan
from .storage import atomic_text, digest, inside, write_json

__all__ = ["QuestionResearch", "run_question_research", "validate_plan", "answer_errors", "review_passes",
           "read_context", "MAX_STEPS", "MAX_WEB_ATTEMPTS", "MAX_REOPENINGS"]

MAX_STEPS = 10
MAX_WEB_ATTEMPTS = 2
MAX_REOPENINGS = 2


def validate_plan(plan, config, discovery, dossier, gaps):
    ordered_tasks(plan.tasks)
    requirements = {r["id"] for r in requirements_for(config)}
    questions = {q.id for q in discovery.questions}
    finding_ids = {f.id for f in dossier.findings} if dossier else set()
    if len({t.id for t in plan.tasks}) != len(plan.tasks):
        raise AppError("Doppelte Recherchefragen im Arbeitsplan.", code="invalid_question_plan", status="blocked")
    for field, expected in (("requirement_ids", requirements), ("question_ids", questions), ("gap_ids", set(gaps))):
        actual = {value for task in plan.tasks for value in getattr(task, field)}
        if actual != expected:
            raise AppError("Der Rechercheplan muss alle vereinbarten Leitfragen und bestehenden Lücken zuordnen.",
                           code="invalid_question_plan", status="blocked")
    if any(not set(t.finding_ids) <= finding_ids for t in plan.tasks):
        raise AppError("Rechercheplan verweist auf unbekannte Befunde.", code="invalid_question_plan", status="blocked")


def _gaps(dossier, migration):
    texts = list(dossier.open_questions) if dossier else []
    if dossier:
        texts.extend(c.gap for c in dossier.coverage if c.gap)
    texts.extend(i["reason"] for i in (migration.get("last_review") or {}).get("issues", []))
    return {gap_id(text): text for text in dict.fromkeys(texts)}


class QuestionResearch(TaskResearchMixin, SynthesisMixin):
    def __init__(self, root, work, config, invoke, progress, *, limits=None, accepted=None, retries=None, plan_gate=None,
                 workers=1):
        self.root, self.work, self.config, self.invoke, self.progress = root, work, config, invoke, progress
        self.folder = work / "question_research"
        self.state = None
        self.reader = None
        self.index = None
        self.attempts = None
        self.limits = limits or (lambda: self.config.research_limits)
        # Explicit gap approvals, read afresh on every pass so an approval written during a run counts.
        self.accepted = accepted or (lambda: {})
        # Explicit requests to attempt a blocked task again; each is adopted once, at the start of a resume.
        self.retries = retries or (lambda: {})
        # ``plan_gate(projection)`` returns the valid approval of the projected plan or None. Without a
        # gate the caller has taken that decision (the CLI and Studio always pass one).
        self.plan_gate = plan_gate
        # Wall-clock seconds of this run's answered calls, kept in the ledger as ``call_timings``.
        self.timings = []
        # The call tag of every engine call; the ledger version above binds the receipts.
        self.call_version = CALL_VERSION
        # How many independent tasks may be answered at once; one keeps the plain sequential loop.
        self.workers = max(1, int(workers))
        # The one lock over everything shared between tasks, and the depth this thread holds it at,
        # so a worker can step out of it for a model call however deeply it is inside.
        self.lock = threading.RLock()
        self.held = threading.local()
        # Set by the first failing worker: the others finish their current call and stop at the next step.
        self.stopping = threading.Event()

    @contextmanager
    def guarded(self):
        """Hold the ledger lock; re-entrant within a thread, so nested saves and checks stay simple."""
        self.lock.acquire()
        self.held.depth = getattr(self.held, "depth", 0) + 1
        try:
            yield
        finally:
            self.held.depth -= 1
            self.lock.release()

    @contextmanager
    def unguarded(self):
        """Release the ledger lock completely for the duration of a model call, then take it back."""
        depth = getattr(self.held, "depth", 0)
        for _ in range(depth):
            self.lock.release()
        self.held.depth = 0
        try:
            yield
        finally:
            for _ in range(depth):
                self.lock.acquire()
            self.held.depth = depth

    def task_of(self, path):
        """The task a receipt path belongs to, or None for planning and closing calls."""
        tasks = self.folder / "tasks"
        return path.relative_to(tasks).parts[0] if path is not None and tasks in path.parents else None

    def ensure_budget(self, request=None):
        if self.state is None:
            return
        with self.guarded():
            projection = budget_projection(self.work, self.state, self.limits(), request, root=self.root)
            self.state["budget_projection"] = projection
            if not projection["feasible"]:
                task = self.task_of(request)
                if task:
                    self.state["tasks"][task]["outcome"] = "budget_block"
                self.save("Das genehmigte Aufruflimit reicht nicht für die verbleibenden Fragen und Abschlussprüfungen", budget_request=request)
                raise AppError(f"Mindestens {projection['minimum_remaining_calls']} weitere Modellaufrufe erforderlich, "
                               f"aber nur {projection['remaining']} genehmigt verfügbar. "
                               "Antworten und Rechercheumfang bleiben gespeichert; ein höheres Aufruflimit muss ausdrücklich genehmigt werden.",
                               code="research_budget_insufficient", status="blocked")

    def generate(self, folder, name, prompt, schema, version, *, search=False):
        self.ensure_budget(folder / f"{name}.json")
        started = time.monotonic()
        # The call itself runs outside the ledger lock, so other tasks keep working meanwhile.
        with self.unguarded():
            value, metadata = self.invoke(prompt, schema, version, search=search)
        self.record_timing(folder, name, time.monotonic() - started)
        if search:
            save_value(folder / f"{name}_metadata.json", metadata)
        return value

    def record_timing(self, folder, name, seconds):
        """One answered call's wall-clock seconds, attributed to its task for the per-task calibration."""
        with self.guarded():
            self.timings.append({"name": name, "task": self.task_of(folder), "seconds": round(seconds, 3)})
            if self.state is not None:
                self.state["call_timings"] = self.timings

    def call(self, folder, name, schema, prompt, *, search=False, validate=None, tag=""):
        """``tag`` marks a call whose prompt changed with a prompt generation (question_synthesis.PROMPT_GENERATION)."""
        return cached_call(folder, name, schema, prompt,
            lambda p, s: self.generate(folder, name, p, s, f"{self.call_version}{tag}.{name}", search=search), validate=validate)

    def set_index(self, index):
        self.index = index
        signature = digest(index.model_dump())
        save_index(self.root, self.work.name, self.folder / "indexes" / f"{signature}.json", index)
        self.state["index_hash"] = signature
        self.reader = SourceReader(index)

    def accepted_tasks(self):
        return {tid: row["accepted_gap"] for tid, row in self.state["tasks"].items() if row.get("accepted_gap")}

    def accepted_summary(self):
        """Accepted gaps with their fixed obligations, for the quality report and the closing prompts."""
        tasks = {t.id: t for t in QuestionPlan.model_validate(self.state["plan"]).tasks}
        return {tid: {"question": tasks[tid].question, "question_ids": list(tasks[tid].question_ids),
                      "requirement_ids": list(tasks[tid].requirement_ids), "reason": gap.get("reason", "")}
                for tid, gap in self.accepted_tasks().items() if tid in tasks}

    def reopen_unsearched(self):
        """Tasks an earlier recovery rule blocked without ever searching the web get that search now.

        Until 2026-09-20 the single automatic strategy change was spent on unread saved passages, so
        a task ended as ``evidence_block`` or ``search_block`` with no web attempt while the run had
        search rounds left. Such a task is not a concrete gap yet: it resumes at its saved step with
        the web search as its next recovery. Accepted gaps, budget blocks and tasks blocked by the
        new rule (a web attempt made or none left) stay as they are; prerequisite blocks that only
        waited for such a task are decided again once it finishes.
        """
        with self.guarded():
            reopened = []
            for task_id, row in self.state["tasks"].items():
                if reopenable(row, self.state.get("limits")):
                    row.update(status="researching", pending=None, fallbacks=1, no_progress=2, outcome=None,
                               activity="Websuche für diese Frage wird nachgeholt")
                    reopened.append(task_id)
            if reopened:
                for row in self.state["tasks"].values():
                    if row["status"] == "blocked" and row.get("outcome") == "prerequisite_block" and not row.get("accepted_gap"):
                        row.update(status="pending", outcome=None, reason="", activity="Noch nicht bearbeitet")
                self.save("Blockierte Teilfragen ohne Websuche werden mit einer Websuche fortgesetzt")
            return reopened

    def adopt_accepted_gaps(self):
        with self.guarded():
            changed = False
            for task_id, approval in self.accepted().items():
                row = self.state["tasks"].get(task_id)
                if row is not None and row["status"] == "blocked" and not row.get("accepted_gap"):
                    row.update(accepted_gap=approval, outcome="accepted_gap",
                               activity="Lücke ausdrücklich akzeptiert; das Dossier wird ohne diese Teilfrage abgeschlossen")
                    changed = True
            if changed:
                self.save("Akzeptierte Lücken werden übernommen")

    def adopt_retries(self):
        """A new attempt the user asked for: the blocked task returns to research with a fresh recovery
        ladder, the allowance of a new question on top of what it used, and the hint as feedback.

        Each request is adopted once. A task that blocks again waits for a new explicit request, so a
        resume never repeats a failed attempt on its own. Tasks that only waited for such a task are
        decided again once it finishes, as after the automatic web search.
        """
        with self.guarded():
            reopened = []
            for task_id, request in self.retries().items():
                row = self.state["tasks"].get(task_id)
                if (row is None or row["status"] != "blocked" or row.get("accepted_gap")
                        or row.get("retry_adopted") == request["requested_at"]):
                    continue
                limits = self.state["limits"]
                hint = (request.get("hint") or "").strip()
                row.update(status="researching", pending=None, outcome=None, reason="", no_progress=0, fallbacks=0,
                           answer_locked=False, retry_adopted=request["requested_at"],
                           retries=row.get("retries", 0) + 1,
                           extra_steps=row.get("extra_steps", 0) + limits["steps_per_question"],
                           extra_web_attempts=row.get("extra_web_attempts", 0) + limits["web_attempts"],
                           activity="Neuer Versuch auf ausdrücklichen Wunsch",
                           feedback=[*(["Note from the editor: " + hint] if hint else []),
                                     "The editor asked for a new attempt after this question was blocked. Change the "
                                     "strategy: other search terms, other sources or other passages. Do not repeat the "
                                     "steps that already failed."])
                reopened.append(task_id)
            if reopened:
                for row in self.state["tasks"].values():
                    if row["status"] == "blocked" and row.get("outcome") == "prerequisite_block" and not row.get("accepted_gap"):
                        row.update(status="pending", outcome=None, reason="", activity="Noch nicht bearbeitet")
                self.save("Blockierte Teilfragen werden auf ausdrücklichen Wunsch erneut versucht")
            return reopened

    def save(self, activity=None, *, budget_request=None):
        # Whole-ledger writes: state, probes, the public ledger and its markdown, then the progress line.
        with self.guarded():
            self._save(activity, budget_request)

    def _save(self, activity, budget_request):
        self.state["budget_projection"] = budget_projection(self.work, self.state, self.limits(), budget_request,
                                                            root=self.root)
        if self.attempts is not None:
            self.state["source_attempt_count"] = len(self.attempts)
        save_value(self.folder / "state.json", self.state)
        write_json(self.folder / "gap_probes.json", self.state.get("gap_probes", []))
        public = public_ledger(self.state, self.index)
        write_json(self.work / "research_questions.json", public)
        lines = ["# Recherchefragen", "", f"{public['closed']} von {public['total']} Teilfragen geprüft abgeschlossen.", ""]
        if public["accepted"]:
            lines += [f"{public['accepted']} Teilfragen als Lücke ausdrücklich akzeptiert.", ""]
        budget = self.state["budget_projection"]
        if self.state["phase"] == "awaiting_plan_approval":
            lines += ["Der Rechercheplan wartet auf Freigabe; bis dahin wird kein weiterer Modellaufruf verbraucht.", ""]
        lines += [f"Mindestens {budget['minimum_remaining_calls']} weitere Modellaufrufe, davon "
                  f"{budget['closing_calls']} für Dossier und Abschlussprüfung; {budget['remaining']} verfügbar. "
                  f"Erfahrungsgemäß etwa {budget['expected_remaining_calls']} Aufrufe "
                  f"({budget['expected_calls_per_task']} je offener Teilfrage, "
                  f"{SOURCE_LABELS.get(budget.get('expected_calls_source'), SOURCE_LABELS['default'])}). "
                  "Zusätzliche Lese-, Such- und Korrekturschritte können mehr benötigen.", ""]
        for row in public["questions"]:
            status = row["status"] + (" (akzeptierte Lücke)" if row["accepted_gap"] else "")
            lines += [f"## {row['question']}", "", f"Status: {status}", "", row["answer"] or row["activity"], ""]
            lines += [f"- Abschlusskriterium: {c}" for c in row["acceptance"]]
            if row["reason"]:
                lines += ["", row["reason"]]
            for finding in row["findings"]:
                lines += ["", finding["statement"]]
                for evidence in finding["evidence"]:
                    source, _, section = self.reader.lookup[evidence["reference"]]
                    lines += [f"- [{source.title.replace('[', '').replace(']', '')}]({source.final_url})"
                              + (f", Seite {section.page}" if section.page else "")]
            lines += [""]
        atomic_text(self.work / "research_questions.md", "\n".join(lines))
        if activity:
            self.progress(activity)

    def initialise(self, discovery, index, dossier, context):
        binding = digest({"brief": quality_brief(self.config), "questions": [q.model_dump() for q in discovery.questions],
                          "initial_sources": [(s.id, s.raw_hash, s.text_hash) for s in index.sources]})
        path = self.folder / "state.json"
        if path.exists():
            self.state = read_value(path)
            if self.state["version"] != VERSION or self.state["input_hash"] != binding:
                raise AppError("Rechercheplan passt nicht zum ursprünglichen Auftrag.", code="inputs_changed", status="blocked")
            self.index = load_index(self.root, self.folder / "indexes" / f"{self.state['index_hash']}.json")
            if digest(self.index.model_dump()) != self.state["index_hash"]:
                raise AppError("Quellenindex passt nicht zum Recherchestand.", code="invalid_source_snapshot", status="blocked")
            check_sources(self.root, self.index)
            self.reader = SourceReader(self.index)
            self.attempts = restore_attempts(self.folder, self.index)
            for task in QuestionPlan.model_validate(self.state["plan"]).tasks:
                row = self.state["tasks"][task.id]
                if row["status"] != "verified":
                    continue
                answer = QuestionAnswer.model_validate(row["answer"])
                verification = row.get("verification", {})
                if (verification.get("answer_hash") != digest(row["answer"]) or
                    answer_errors(answer, task, self.reader, set(row["read_refs"])) or
                    not review_passes(AnswerReview.model_validate(verification["review"]), task) or
                    any(self.reader.sources[sid].text_hash != value for sid, value in verification["source_hashes"].items())):
                    raise AppError("Gespeicherte Antwortprüfung ist nicht konsistent.",
                                   code="invalid_research_checkpoint", status="blocked")
                if self.state.get("evidence_version") == EVIDENCE_VERSION:
                    verdict = AnswerReview.model_validate(verification["review"])
                    refs = [e.reference for f in answer.findings for e in f.evidence]
                    if support_errors(answer.findings, verdict, read_context(self.reader, refs)):
                        raise AppError("Stored support review no longer passes.", code="invalid_research_checkpoint", status="blocked")
                    expected = {a["task_id"]: a["answer_hash"] for a in prerequisite_answers(task, self.state)}
                    if set(expected) != set(task.depends_on) or verification.get("prerequisite_hashes", {}) != expected:
                        raise AppError("The verified prerequisites changed.", code="invalid_research_checkpoint", status="blocked")
            # No task is running when a resume starts, whatever the interrupted worker had in flight;
            # ledgers of an earlier version named that one task in ``active_task``.
            self.state["active_tasks"] = []
            self.state.pop("active_task", None)
            if (self.plan_gate is not None and self.state["phase"] == "questions"
                    and not self.state.get("plan_approval")
                    and all(row["status"] not in {"verified", "blocked"} for row in self.state["tasks"].values())):
                # A plan made before the gate existed, with no task finished yet: nothing is lost by
                # projecting it now and letting the operator approve it or cap it before the first call.
                self.state["phase"] = "awaiting_plan_approval"
            if self.state.get("evidence_version") != EVIDENCE_VERSION:
                # Preserve the old receipt verbatim, but never relabel it as clause-level verification.
                save_value(self.folder / "legacy_evidence_state.json", self.state)
                for row in self.state["tasks"].values():
                    if row.get("answer"):
                        row["legacy_verification"] = row.get("verification")
                        row.update(draft_answer=row["answer"], answer=None, status="researching", step=0,
                                   pending=None, no_progress=0, fallbacks=0,
                                   feedback=["Retain this answer and add explicit claim contracts for the stronger evidence review."])
                    row["dependency_revision"] = row.get("dependency_revision", 0) + 1
                self.state.update(evidence_version=EVIDENCE_VERSION, phase="questions",
                                  audit_round=self.state["audit_round"] + 1,
                                  dirty_tasks=[t["id"] for t in self.state["plan"]["tasks"]])
            self.timings = self.state.setdefault("call_timings", [])
            self.save("Gespeicherte Antworten und offene Recherchefragen werden übernommen")
            return
        discovery, index, dossier, context, migration = bootstrap_legacy(
            self.root, self.work, discovery, index, dossier, context)
        gaps = _gaps(dossier, migration)
        self.progress("Leitfragen werden in überprüfbare Rechercheaufgaben aufgeteilt")
        # Calls before the ledger existed (discovery, an earlier planning attempt) count for the projection too.
        self.timings = run_timings(self.work)
        planning_path = self.folder / "planning_budget.json"
        planning_budget = None
        if planning_path.exists() or not (self.folder / "plan.json").exists():
            planning_budget = self.planning_allowance(planning_path)
        suffix = ""
        if (self.folder / "plan.json").exists() and any("depends_on" not in task for task in read_value(self.folder / "plan.json")["tasks"]):
            # A pre-ledger planning receipt has no binding for the new schema. Retain it and
            # make a separately budgeted plan; do not overwrite or mislabel the old receipt.
            suffix = "_evidence_v1"
        plan, groups = self.plan_tasks(discovery, dossier, gaps, planning_budget, suffix)
        tasks = {t.id: pending_task() for t in plan.tasks}
        self.state = {"version": VERSION, "input_hash": binding, "plan": plan.model_dump(), "tasks": tasks,
                      "evidence_version": EVIDENCE_VERSION, "prompt_generation": PROMPT_GENERATION,
                      "discovery": discovery.model_dump(), "seed_dossier": dossier.model_dump() if dossier else None,
                      "migration": migration, "gaps": gaps, "phase": "questions", "audit_round": 0,
                      "task_groups": groups,
                      "dirty_tasks": [t.id for t in plan.tasks], "active_tasks": [],
                      "limits": {"steps_per_question": MAX_STEPS, "web_attempts": MAX_WEB_ATTEMPTS,
                                 "reopenings": MAX_REOPENINGS},
                      "call_timings": self.timings, "plan_caps": [], "plan_revisions": []}
        self.set_index(index)
        self.attempts = restore_attempts(self.folder, index)
        # Model-free: a lexical pass that asks whether each declared gap already has
        # candidate sections in the corpus. It costs nothing and blocks nothing yet.
        self.state["gap_probes"] = probe(index, gaps, gap_terms=coverage_terms(dossier) if dossier else {})
        self.project_plan()
        if self.plan_gate is None:
            self.save("Rechercheplan gespeichert – einzelne Fragen werden untersucht")
            return
        # No task call before the projected cost was seen and approved (plan_approval.json).
        self.state["phase"] = "awaiting_plan_approval"
        self.save("Rechercheplan gespeichert – wartet auf Freigabe")

    def planning_allowance(self, path, *, cap=None):
        """The allowance the planner sees, saved once so replays judge a plan by the same rule."""
        if path.exists():
            return read_value(path)
        budget_path = self.work / "budget.json"
        used = json.loads(budget_path.read_text(encoding="utf-8")).get("model_calls", 0) if budget_path.exists() else 0
        limit = self.limits().model_calls
        per_task, source = expected_calls_per_task(self.work, self.root, self.state)
        affordable = affordable_tasks(used, limit, per_task)
        allowance = {"used": used, "approved_limit": limit, "minimum_calls_per_task": 2,
                     "expected_calls_per_task": per_task, "expected_calls_source": source,
                     "max_tasks": min(cap, affordable) if cap else affordable,
                     "synthesis": "One call for a fresh dossier, or one patch per four tasks for an inherited dossier, plus two final audits."}
        if cap:
            allowance["requested_max_tasks"] = cap
        save_value(path, allowance)
        return allowance

    def project_plan(self):
        """What the current plan is expected to cost; written beside the ledger for Studio, CLI and the gate."""
        projection = plan_projection(self.work, self.root, self.state, self.limits())
        write_json(self.folder / "plan_projection.json", projection)
        return projection

    def review_plan(self):
        """Stop before the first task call until the projected plan is approved.

        An approval with a cap below the task count plans again under that cap, once per cap, and
        presents the new plan; the operator always approves the plan that will actually run.
        """
        if self.state["phase"] != "awaiting_plan_approval":
            return
        if self.plan_gate is None:
            self.state["phase"] = "questions"
            self.save("Rechercheplan übernommen – einzelne Fragen werden untersucht")
            return
        while True:
            projection = self.project_plan()
            approval = self.plan_gate(projection)
            cap = (approval or {}).get("max_tasks")
            if approval is None or (cap is not None and cap < projection["tasks"] and cap in self.state.get("plan_caps", [])):
                self.save("Der Rechercheplan wartet auf Freigabe")
                raise AppError(plan_review_message(projection, self.work.name), code="research_plan_review", status="blocked")
            if cap is not None and cap < projection["tasks"]:
                self.replan(cap)
                continue
            self.state.update(phase="questions", plan_approval=approval)
            self.save("Rechercheplan freigegeben – einzelne Fragen werden untersucht")
            return

    def replan(self, cap):
        """Plan again under an approved cap; the previous plan's receipts stay, the new one waits for approval."""
        discovery = ResearchDiscovery.model_validate(self.state["discovery"])
        dossier = ResearchDossier.model_validate(self.state["seed_dossier"]) if self.state.get("seed_dossier") else None
        gaps = self.state["gaps"]
        previous = digest(self.state["plan"])
        self.progress(f"Der Rechercheplan wird auf höchstens {cap} Teilfragen neu zugeschnitten")
        suffix = f"_cap{cap}"
        planning_budget = self.planning_allowance(self.folder / f"planning_budget{suffix}.json", cap=cap)
        plan, groups = self.plan_tasks(discovery, dossier, gaps, planning_budget, suffix)
        self.state.update(plan=plan.model_dump(), tasks={t.id: pending_task() for t in plan.tasks}, task_groups=groups,
                          dirty_tasks=[t.id for t in plan.tasks], active_tasks=[],
                          plan_caps=[*self.state.get("plan_caps", []), cap],
                          plan_revisions=[*self.state.get("plan_revisions", []),
                                          {"cap": cap, "previous_plan_hash": previous,
                                           "plan_hash": digest(plan.model_dump()), "tasks": len(plan.tasks)}])
        self.state["gap_probes"] = probe(self.index, gaps, gap_terms=coverage_terms(dossier) if dossier else {})
        self.save(f"Rechercheplan mit {len(plan.tasks)} Teilfragen gespeichert – wartet auf Freigabe")

    def plan_tasks(self, discovery, dossier, gaps, planning_budget, suffix=""):
        """One planning call, then the scope review in at most two passes; returns the plan and its split groups."""
        prompt = (TERMINOLOGY + TEACHING_SCOPE +
            instructions("question_plan", language=self.config.language) + "\n" + json.dumps({
                "brief": quality_brief(self.config), "questions": [q.model_dump() for q in discovery.questions],
                "gaps": gaps, "existing_findings": [f.model_dump() for f in dossier.findings] if dossier else []}, ensure_ascii=False))
        if planning_budget is not None:
            head, data = prompt.rsplit("\n", 1)
            prompt = (head + " " + instructions("question_plan_allowance") + "\n" +
                json.dumps({**json.loads(data), "planning_budget": planning_budget}, ensure_ascii=False))
        # The cap is a stable snapshot from planning time, so replays judge a plan by the same rule.
        max_tasks = (planning_budget or {}).get("max_tasks")

        def within_cap(candidate, final, limit):
            if not final and limit and len(candidate.tasks) > limit:
                raise AppError(f"Der Plan umfasst {len(candidate.tasks)} Aufgaben; das genehmigte Aufruflimit trägt "
                               f"erfahrungsgemäß höchstens {limit}. Zusammengehörige Verpflichtungen in einer "
                               "Aufgabe bündeln, ohne Anforderungen wegzulassen.",
                               code="plan_exceeds_allowance", status="blocked")

        def check_plan(candidate, final):
            validate_plan(candidate, self.config, discovery, dossier, gaps)
            within_cap(candidate, final, max_tasks)

        plan = self.call(self.folder, "plan" + suffix, QuestionPlan, prompt, validate=check_plan)
        validate_plan(plan, self.config, discovery, dossier, gaps)
        groups = {}
        # Separate model calls, bounded to two passes; no source-reading budget is
        # spent on a plan whose obligations are still bundled into broad chapters.
        for attempt in range(2):
            self.progress("Recherchefragen werden auf einen klar begrenzten Umfang geprüft")
            payload = {"tasks": plan.model_dump()["tasks"]}
            scope_prompt = SCOPE_INSTRUCTIONS
            if max_tasks:
                payload["task_budget"] = {"max_tasks": max_tasks, "current_tasks": len(plan.tasks)}
                scope_prompt = scope_prompt + " " + instructions("scope_task_budget")
            current = plan

            def check_scope(review, final, current=current):
                candidate, _ = scoped_plan(current, review)
                validate_plan(candidate, self.config, discovery, dossier, gaps)
                # A split may not push the plan past the cap; a plan already above it is not the reviewer's to shrink.
                within_cap(candidate, final, max(max_tasks, len(current.tasks)) if max_tasks else None)

            review = self.call(self.folder, f"scope_{attempt}" + suffix, QuestionScopeReview,
                scope_prompt + "\n" + json.dumps(payload, ensure_ascii=False), validate=check_scope)
            plan, splits = scoped_plan(plan, review)
            validate_plan(plan, self.config, discovery, dossier, gaps)
            groups.update(splits)
            if not splits:
                break
        else:
            raise AppError("Recherchefragen bleiben nach der Umfangsprüfung zu breit; die überarbeitete Aufteilung ist gespeichert.",
                           code="question_scope_unresolved", status="blocked")
        return plan, groups

    def task_readiness(self, task, *, wait_for_open=False):
        """Whether a task can start: ``done`` (verified or blocked), ``ready`` (every prerequisite
        verified), ``waiting`` (a prerequisite is still being answered; only asked for when tasks run
        side by side) or ``blocked`` after marking the row, because a prerequisite ended without a
        verified answer, an accepted gap included."""
        row = self.state["tasks"][task.id]
        if row["status"] in {"verified", "blocked"}:
            return "done"
        unmet = [dep for dep in task.depends_on if self.state["tasks"][dep]["status"] != "verified"]
        if not unmet:
            return "ready"
        if wait_for_open and not any(self.state["tasks"][dep]["status"] == "blocked" for dep in unmet):
            return "waiting"
        accepted = [dep for dep in unmet if self.state["tasks"][dep].get("accepted_gap")]
        row.update(status="blocked", outcome="prerequisite_block",
                   reason=("Eine vorausgesetzte Teilfrage wurde als Lücke akzeptiert; diese Teilfrage "
                           "kann ohne sie nicht geprüft werden." if accepted else
                           "A required prerequisite has not passed evidence review."))
        return "blocked"

    def research_tasks(self):
        """Answer every open task of the plan: one at a time in plan order, or with several workers
        up to that many tasks whose prerequisites are verified at once."""
        tasks = ordered_tasks(QuestionPlan.model_validate(self.state["plan"]).tasks)
        self.stopping.clear()
        if self.workers == 1:
            for task in tasks:
                if self.task_readiness(task) == "ready":
                    self.ensure_budget()
                    self.research_task(task)
            self.state["active_tasks"] = []
            return
        self.research_tasks_concurrently(tasks)

    def research_tasks_concurrently(self, tasks):
        """Submit ready tasks in plan order, refill as tasks finish, drain in-flight work on failure."""
        remaining, running = list(tasks), {}

        def perform(task):
            try:
                self.research_task(task)
            except BaseException:
                # The other workers finish their current call and stop at their next step boundary.
                self.stopping.set()
                raise

        def fill(pool):
            kept = []
            for task in remaining:
                readiness = self.task_readiness(task, wait_for_open=True)
                if readiness in {"done", "blocked"}:
                    continue
                if readiness == "ready" and len(running) < self.workers and not self.stopping.is_set():
                    self.ensure_budget()
                    running[pool.submit(copy_context().run, perform, task)] = task
                    continue
                kept.append(task)
            remaining[:] = kept

        with ThreadPoolExecutor(max_workers=self.workers, thread_name_prefix="research") as pool:
            try:
                while True:
                    with self.guarded():
                        fill(pool)
                    if not running:
                        if remaining and not self.stopping.is_set():
                            raise AppError("Research task prerequisites contain a cycle.",
                                           code="invalid_question_plan", status="blocked")
                        break
                    done, _ = wait(running, return_when=FIRST_COMPLETED)
                    for future in done:
                        running.pop(future)
                        future.result()
            except BaseException:
                self.stopping.set()
                wait(running)
                self.save()
                raise
        with self.guarded():
            self.state["active_tasks"] = []

    def run(self, discovery, index, dossier=None, context=()):
        self.initialise(discovery, index, dossier, context)
        self.review_plan()
        self.reopen_unsearched()
        self.adopt_retries()
        while True:
            self.adopt_accepted_gaps()
            self.research_tasks()
            self.adopt_accepted_gaps()
            blocked = [r for r in public_ledger(self.state)["questions"] if r["status"] == "blocked" and not r["accepted_gap"]]
            if blocked:
                self.state["phase"] = "blocked"
                self.save("Einzelne Recherchefragen bleiben konkret unbelegt; geprüfte Antworten sind gespeichert")
                raise AppError("Einzelne Recherchefragen bleiben offen. Die geprüften Antworten und konkreten Blockaden sind "
                               "gespeichert. Jede blockierte Teilfrage kann ausdrücklich als Lücke akzeptiert werden, damit das "
                               "Dossier ohne sie abgeschlossen wird.",
                               code="research_questions_blocked", status="blocked")
            dossier, discovery, context = self.compose()
            dossier, review, report = self.audit(dossier, discovery, context)
            if not report["passed"]:
                reopened, newly_blocked = self.reopen(dossier, review, report)
                if reopened or newly_blocked:
                    continue
                # Every objection targets an explicitly accepted gap: nothing is left to research.
                report = self.tolerate(dossier, review, report)
            self.state.update(phase="completed", active_tasks=[])
            for objection in self.state.get("objections", {}).values():
                objection.update(status="closed", closure_audit=self.state["audit_round"],
                                 dossier_hash=digest(dossier.model_dump()))
            self.save("Alle Recherchefragen und die Gesamtprüfung sind abgeschlossen")
            destination = self.work / "complete_research"
            files = {"discovery.json": discovery.model_dump(), "source_index.json": self.index.model_dump(),
                     "source_context.json": context, "dossier.json": dossier.model_dump(), "source_review.json": review.model_dump(),
                     "evidence_report.json": report["evidence"],
                     "search_receipts.json": {t: row.get("search_receipts", []) for t, row in self.state["tasks"].items()},
                     "objections.json": self.state.get("objections", {}),
                     "accepted_gaps.json": {"gaps": report.get("accepted_gaps", []),
                                            "residual_objections": report.get("residual_objections", []),
                                            "objections": self.state.get("accepted_gap_objections", {})}}
            for name, value in files.items():
                write_json(destination / name, value)
            return [*(destination / name for name in files), self.work / "research_quality_gate.json", self.work / "research_quality.md",
                    self.work / "research_questions.json", self.work / "research_questions.md",
                    *(inside(self.root, s.raw_path) for s in self.index.sources)]


def run_question_research(root, work, config, discovery, index, invoke, progress, *, dossier=None, context=(),
                          limits=None, accepted=None, retries=None, plan_gate=None, workers=1):
    return QuestionResearch(root, work, config, invoke, progress, limits=limits, accepted=accepted, retries=retries,
                            plan_gate=plan_gate, workers=workers).run(discovery, index, dossier, context)
