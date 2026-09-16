"""Project execution preferences; separate from factual/audio input fingerprints."""
from pathlib import Path
from typing import Literal
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from contextvars import copy_context

from .models import Contract, now
from .storage import write_json

MAX_PARALLEL = 3


class ExecutionChoice(Contract):
    text: Literal["sequential", "parallel"] = "sequential"
    audio: Literal["sequential", "parallel"] = "sequential"

    @property
    def text_workers(self):
        return MAX_PARALLEL if self.text == "parallel" else 1


def selected_execution(root: Path):
    path = root / "studio/execution.json"
    return ExecutionChoice.model_validate_json(path.read_text(encoding="utf-8")) if path.exists() else ExecutionChoice()


def run_episode_stage(entries, action, *, workers, work, stage):
    """Bound independent episode tasks, preserve order and drain in-flight work on failure."""
    for entry in entries:
        write_json(work / "stage_activity" / stage / (entry.episode_id + ".json"),
                   {"episode_id": entry.episode_id, "status": "pending"})

    def perform(entry):
        path = work / "stage_activity" / stage / (entry.episode_id + ".json")
        record = {"episode_id": entry.episode_id, "status": "running", "started_at": now()}
        write_json(path, record)
        try:
            result = action(entry)
        except BaseException:
            write_json(path, {**record, "status": "interrupted", "finished_at": now()})
            raise
        write_json(path, {**record, "status": "completed", "finished_at": now()})
        return result

    if workers == 1:
        return [path for entry in entries for path in perform(entry)]
    results, remaining = {}, iter(enumerate(entries))
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="episode") as pool:
        pending = {}

        def submit_next():
            item = next(remaining, None)
            if item is not None:
                index, entry = item
                pending[pool.submit(copy_context().run, perform, entry)] = index

        for _ in range(min(workers, len(entries))):
            submit_next()
        try:
            while pending:
                done, _ = wait(pending, return_when=FIRST_COMPLETED)
                # Check all completed tasks before starting more work after an error.
                for future in done:
                    results[pending.pop(future)] = future.result()
                for _ in done:
                    submit_next()
        except BaseException:
            for future in pending:
                future.cancel()
            raise
    return [path for index in range(len(entries)) for path in results[index]]
