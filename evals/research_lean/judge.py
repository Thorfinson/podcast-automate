"""The narration lab's v3 judge and its pair judge (2026-10-27), kept for ``run.py judge``.

The lab's own run.py went with the writing prompt on 2026-11-01 (D-184; the whole script is in git history, commit
f6d51cd). These are its judging parts, unchanged: the scores (``quality``, judge_quality.txt, ``Judgment``), the pair
judge (``compare``, judge_pair.txt, ``Preference``) and the structured call that saves each answer (``ask``). The two
prompts stay in the lab's archive, evals/narration_lab/, so the judge the operator calibrated keeps one home.
"""
import json
import sys
import time
from pathlib import Path
from types import SimpleNamespace

from pydantic import Field

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
LAB = REPO / "evals" / "narration_lab"
QUALITY_VERSION = "workshop_quality.v3-judge"
PAIR_VERSION = "workshop_pair.v2-names"

sys.path.insert(0, str(REPO / "src"))
from podcast_automate.errors import AppError  # noqa: E402
from podcast_automate.models import Contract, NonEmpty  # noqa: E402
from podcast_automate.provider_pool import text_generation_settings  # noqa: E402
from podcast_automate.scripting import build_adapter  # noqa: E402
from podcast_automate.storage import digest, write_json  # noqa: E402
from podcast_automate.subscriptions import parse_iso, quota_retry_at  # noqa: E402

# What run.py uses: the two judges, the script text the judges read, and the adapter of each judge's model.
__all__ = ["Judgment", "Preference", "build_adapter", "compare", "quality", "script_text", "text_generation_settings"]


class Scale(Contract):
    score: int = Field(ge=1, le=10)
    reason: NonEmpty


class QualityScores(Contract):
    tension: Scale
    interest: Scale
    storytelling: Scale


class Shortfall(Contract):
    segment_ids: list[NonEmpty]
    problem: NonEmpty
    fix: NonEmpty


class Judgment(Contract):
    """The judge's answer (judge_quality.txt, v3): scores, then only the weak spots that cost points. The critic's
    list of up to eight points filled up and held every score at 8 or below (2026-10-27)."""
    scores: QualityScores
    weak_spots: list[Shortfall] = Field(default_factory=list, description=(
        "Only weak spots that actually cost points; empty if there are none."))


class Preference(Contract):
    tension: str
    interest: str
    storytelling: str
    overall: str
    reason: NonEmpty


def pair_check(answer):
    allowed = {"first", "second", "tie"}
    wrong = [key for key in ("tension", "interest", "storytelling", "overall") if getattr(answer, key) not in allowed]
    return [f"{', '.join(wrong)}: answer first, second or tie."] if wrong else []


def script_text(script):
    return "\n".join(f"[{s.segment_id}] {s.speaker_id}: {s.text}" for s in script.segments)


def wait_for_quota(error=None, attempt=0):
    """Sleep until the subscription's quota is back, as the Studio waits before it resumes a paused run."""
    retry = parse_iso(quota_retry_at(error or SimpleNamespace(details={"provider": "claude_code"}), attempt=attempt))
    print(json.dumps({"waiting_for_quota_until": retry.isoformat()}), flush=True)
    time.sleep(max(60.0, retry.timestamp() - time.time() + 120))


def ask(adapter, prompt, output_type, version, folder, check=None):
    """One structured answer, saved in ``folder``; a quota stop waits for the reset, an answer ``check`` refuses is
    asked once more with its defects named."""
    saved, stamp = folder / "answer.json", folder / "prompt.sha256"
    signature = digest({"version": version, "prompt": prompt})
    if saved.is_file() and stamp.is_file() and stamp.read_text(encoding="utf-8") == signature:
        return output_type.model_validate_json(saved.read_text(encoding="utf-8"))
    # A judgment under another judge prompt (or saved before the stamp) is asked again; the old one is kept aside.
    if saved.is_file():
        saved.replace(folder / f"answer_{len(list(folder.glob('answer_*.json'))) + 1}.json")
    attempt, retry = 0, prompt
    for _ in range(2):
        while True:
            number = len(list((folder / "calls").glob("call_*"))) + 1
            try:
                answer = adapter.structured(retry, output_type, folder / "calls" / f"call_{number:03d}",
                                            prompt_version=version)[0]
                break
            except AppError as exc:
                if exc.status != "waiting_for_quota":
                    raise
                wait_for_quota(exc, attempt)
                attempt += 1
        defects = check(answer) if check else []
        if not defects:
            break
        retry = prompt + "\n" + json.dumps({"defects_of_your_last_answer": defects}, ensure_ascii=False)
    write_json(saved, answer.model_dump())
    stamp.write_text(signature, encoding="utf-8")
    return answer


def quality(adapter, script, folder):
    """The judge's scores (judge_quality.txt, Judgment)."""
    prompt = (LAB / "judge_quality.txt").read_text(encoding="utf-8") + "\n" + json.dumps(
        {"script": script_text(script)}, ensure_ascii=False)
    return ask(adapter, prompt, Judgment, QUALITY_VERSION, folder)


def compare(adapter, first, second, folder):
    """The pair judge's preference per scale (judge_pair.txt, Preference)."""
    prompt = (LAB / "judge_pair.txt").read_text(encoding="utf-8") + "\n" + json.dumps(
        {"first": script_text(first), "second": script_text(second)}, ensure_ascii=False)
    return ask(adapter, prompt, Preference, PAIR_VERSION, folder, pair_check)
