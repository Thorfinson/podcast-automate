"""Synthetic control-flow responses; these do not establish dialogue quality."""
import json

from podcast_automate.polishing import DEMANDING_PASSAGES, DialoguePolishReview, POLISH_CRITERIA


def demanding_passages(candidate):
    """Up to three segments that are neither the greeting nor the sign-off, all referents resolved."""
    middle = candidate["segments"][1:-1][:DEMANDING_PASSAGES]
    return [{"segment_id": segment["segment_id"], "load": "Synthetic fixture; density is not measured.",
             "referents": [{"expression": "dieser Schritt",
                            "resolved_by": candidate["segments"][0]["segment_id"]}]}
            for segment in middle]


def polish_review(prompt):
    data = json.loads(prompt.splitlines()[-1])
    before, after = data["original"]["segments"][0], data["candidate"]["segments"][0]
    return DialoguePolishReview.model_validate({"checks": [
        {"criterion": criterion, "verdict": "pass", "reason": "Synthetic fixture; human quality is not measured.",
         "before": [{"segment_id": before["segment_id"], "quote": before["text"]}],
         "after": [{"segment_id": segment["segment_id"], "quote": segment["text"]}
                   for segment in ([after, data["candidate"]["segments"][-1]]
                                   if criterion == "episode_framing" else [after])]} for criterion in POLISH_CRITERIA],
         "demanding_passages": demanding_passages(data["candidate"]),
         "limitations": ["Mock response for orchestration tests only."]})
