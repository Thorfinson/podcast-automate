"""Synthetic control-flow responses; these do not establish dialogue quality."""
import json

from podcast_automate.polishing import DialoguePolishReview, POLISH_CRITERIA


def polish_review(prompt):
    data = json.loads(prompt.splitlines()[-1])
    before, after = data["original"]["segments"][0], data["candidate"]["segments"][0]
    return DialoguePolishReview.model_validate({"checks": [
        {"criterion": criterion, "verdict": "pass", "reason": "Synthetic fixture; human quality is not measured.",
         "before": [{"segment_id": before["segment_id"], "quote": before["text"]}],
         "after": [{"segment_id": after["segment_id"], "quote": after["text"]}]} for criterion in POLISH_CRITERIA],
         "limitations": ["Mock response for orchestration tests only."]})
