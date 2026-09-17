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


def prerequisite_answers(task, state):
    return [{"task_id": identifier, "answer_hash": digest(state["tasks"][identifier]["answer"]),
             "answer": state["tasks"][identifier]["answer"]} for identifier in task.depends_on
            if state["tasks"][identifier]["status"] == "verified"]


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
            row.update(status="researching", draft_answer=row["answer"], answer=None, step=0, pending=None,
                       no_progress=0, fallbacks=0, outcome=None,
                       feedback=["A prerequisite changed. Revalidate this answer against the updated verified prerequisite."],
                       activity="Geänderte Voraussetzung wird gezielt nachgeprüft")
            row["dependency_revision"] = row.get("dependency_revision", 0) + 1
    return sorted(affected)
