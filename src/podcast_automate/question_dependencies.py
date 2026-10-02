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


def revalidate(row):
    """Send a verified answer back to be checked against its changed prerequisites; this is no rework."""
    row.update(status="researching", draft_answer=row["answer"], answer=None, step=0, pending=None,
               no_progress=0, fallbacks=0, outcome=None,
               feedback=["A prerequisite changed. Revalidate this answer against the updated verified prerequisite."],
               activity="Geänderte Voraussetzung wird gezielt nachgeprüft")
    row["dependency_revision"] = row.get("dependency_revision", 0) + 1


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
