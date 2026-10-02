"""Prerequisite scheduling and precise invalidation of dependent answers."""
from .errors import AppError
from .storage import digest


def ordered_tasks(tasks):
    by_id = {t.id: t for t in tasks}
    if len(by_id) != len(tasks) or any(len(t.depends_on) != len(set(t.depends_on)) or
            not set(t.depends_on) <= by_id.keys() or t.id in t.depends_on for t in tasks):
        raise AppError("Invalid research task prerequisites.", code="invalid_question_plan", status="blocked")
    ordered, pending = [], list(tasks)
    while pending:
        ready = [t for t in pending if set(t.depends_on) <= {t.id for t in ordered}]
        if not ready:
            raise AppError("Research task prerequisites contain a cycle.", code="invalid_question_plan", status="blocked")
        ordered.extend(ready)
        pending = [t for t in pending if t not in ready]
    return ordered


def gap_prerequisites(task, state):
    """Prerequisites of a synthesis that the user accepted as gaps. The synthesis goes on without them and names
    them; any other task still needs every prerequisite verified (Ontologies, 2026-09-30: the series' closing
    question hung on 48 tasks, and one accepted gap among them would have blocked it)."""
    if task.kind != "synthesis":
        return []
    return [identifier for identifier in task.depends_on if state["tasks"][identifier].get("accepted_gap")]


def prerequisite_gaps(task, state):
    """The accepted gaps a synthesis goes on without, as its prompt names them."""
    questions = {t["id"]: t["question"] for t in state["plan"]["tasks"]}
    return [{"task_id": identifier, "question": questions[identifier],
             "reason": (state["tasks"][identifier].get("accepted_gap") or {}).get("reason", "")}
            for identifier in gap_prerequisites(task, state)]


def prerequisite_met(task, identifier, state):
    return state["tasks"][identifier]["status"] == "verified" or identifier in gap_prerequisites(task, state)


def gatekeepers(tasks):
    """Tasks other than a synthesis that hold up more than a third of the plan, directly or through others,
    with the number they hold up. A blocked prerequisite blocks everything after it, so a plan built as a
    reading order stakes most of the run on its first question (Ontologies, 2026-09-30: one framing question
    held up all 58 others and blocked). Up to five dependents are always allowed, for small plans."""
    children = {t.id: [] for t in tasks}
    for task in tasks:
        for identifier in task.depends_on:
            children.setdefault(identifier, []).append(task.id)

    def below(identifier, seen):
        for child in children.get(identifier, []):
            if child not in seen:
                seen.add(child)
                below(child, seen)
        return seen

    limit = max(5, len(tasks) // 3)
    held = {t.id: len(below(t.id, set())) for t in tasks if t.kind != "synthesis"}
    return {identifier: count for identifier, count in held.items() if count > limit}


def prerequisite_answers(task, state):
    return [{"task_id": identifier, "answer_hash": digest(state["tasks"][identifier]["answer"]),
             "answer": state["tasks"][identifier]["answer"]} for identifier in task.depends_on
            if state["tasks"][identifier]["status"] == "verified"]


def prerequisites_current(task, state, verification):
    """Whether ``verification`` was made against today's prerequisites: each one verified or accepted as a gap, and
    the answers it bound unchanged. The resume checks every verified answer so (question_research.initialise), and
    keep_spent_answers an answer that comes back after a failed rework."""
    expected = {a["task_id"]: a["answer_hash"] for a in prerequisite_answers(task, state)}
    covered = set(expected) | set(gap_prerequisites(task, state))
    return covered == set(task.depends_on) and (verification or {}).get("prerequisite_hashes", {}) == expected


PREREQUISITE_FEEDBACK = "A prerequisite changed. Revalidate this answer against the updated verified prerequisite."


def revalidate(row, *, feedback=None, activity="Geänderte Voraussetzung wird gezielt nachgeprüft"):
    """Send a verified answer back to be checked again; this is no rework.

    The answer may come back unchanged: it never failed a review, so ``resubmit`` names it and the reader is not
    told that an identical answer "already failed independent review" (answer_defects). A lock or a correction-only
    mode from an earlier rejection does not carry over either (2026-10-02: a correct answer had to change wording)."""
    row.update(status="researching", draft_answer=row["answer"], answer=None, step=0, pending=None,
               no_progress=0, fallbacks=0, outcome=None, answer_locked=False, lock=None, revise_only=False,
               resubmit=digest(row["answer"]) if row.get("answer") else None,
               feedback=list(feedback or [PREREQUISITE_FEEDBACK]), activity=activity)
    row["dependency_revision"] = row.get("dependency_revision", 0) + 1
    if row.get("reopenings"):
        # The rework of the last reopening is over: the answer was verified again. A block of this check is no failed
        # rework, and the answer from before that reopening, which an audit rejected, does not come back
        # (question_synthesis.failed_rework; 2026-10-02 review).
        row["reopenings"][-1]["settled"] = True
    # Counts against no limit; the report shows how often each question was checked again, on resume as well as after
    # a reworked prerequisite (2026-10-02: one synthesis question four times).
    row["revalidations"] = row.get("revalidations", 0) + 1


def invalidate_dependents(state, changed):
    affected = set(changed)
    while True:
        new = {t["id"] for t in state["plan"]["tasks"] if set(t.get("depends_on", [])) & affected} - affected
        if not new:
            break
        affected.update(new)
    dependents = affected - set(changed)
    for identifier in dependents:
        row = state["tasks"][identifier]
        if row["status"] == "verified":
            revalidate(row)
    return sorted(affected)
