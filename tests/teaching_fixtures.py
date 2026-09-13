"""Synthetic responses for control-flow tests, not evidence of educational quality."""
import json

from podcast_automate.teaching import (
    CRITERIA, TeachingPlan, TeachingPlanReview, ListenerReadback, TeachingReview, EditorialReview,
)


def teaching_response(prompt, output_type):
    payload = json.loads(prompt.splitlines()[-1])
    if output_type is TeachingPlan:
        if "design" in payload:
            return TeachingPlan.model_validate(payload["design"])
        entry = payload["episode"]
        scenes = entry["scenes"]
        first = scenes[0]["scene_id"]
        return TeachingPlan.model_validate({
            "episode_id": entry["episode_id"], "learner_start": "No specialist knowledge.",
            "opening_problem": "How does a model compare candidates?", "relevance": "Choose a compatible outcome.",
            "destination": "Explain which candidate is preferred and why.",
            "objectives": [{"objective_id": "goal_compare", "ability": "Compare a new pair of candidates.",
                "question": "Which candidate is preferred and why?", "expected_reasoning": [
                    "Each candidate receives a score.", "The lower score indicates the better fit."],
                "finding_ids": entry["finding_ids"]}],
            "concepts": [{"concept_id": "candidate", "meaning": "A possible outcome.", "introduced_in": first,
                          "requires": [], "finding_ids": entry["finding_ids"]},
                         {"concept_id": "energy", "meaning": "An assessment of a candidate.", "introduced_in": first,
                          "requires": ["candidate"], "finding_ids": entry["finding_ids"]}],
            "scenes": [{"scene_id": s["scene_id"], "entry_question": s["question"],
                        "builds_on": [scenes[i-1]["scene_id"]] if i else [],
                        "reasoning_steps": s["explanation_steps"], "listener_can_now": "Compare the alternatives."}
                       for i, s in enumerate(scenes)],
            "worked_example": {"scene_ids": [first], "setup": "Two candidate outcomes have different scores.",
                "reasoning_steps": ["Compare their scores.", "Select the lower one because it indicates fit."],
                "misconception": "Lower is always worse.", "correction": "This score uses the lower-is-better convention.",
                "limits": "Scores alone do not provide probabilities."},
            "synthesis": {"premise_concept_ids": ["candidate", "energy"],
                "reasoning_steps": ["Assess each possible outcome.", "Use the comparison to choose an outcome."],
                "conclusion": "Assessment can guide a decision.", "transfer_question": "Compare two new outcomes."},
            "research_gaps": []})
    if output_type is TeachingPlanReview:
        return TeachingPlanReview.model_validate({"issues": [], "research_gaps": [], "gap_assessments": [
            {"gap": g["question"], "required_for_objective": True, "reason": "Indispensable fixture gap."}
            for g in payload["design"]["research_gaps"]]})
    if output_type is ListenerReadback:
        segment = payload["script"]["segments"][-1]
        return ListenerReadback.model_validate({"answers": [{"objective_id": g["objective_id"],
            "answer": "The candidate with lower energy.", "reasoning_steps": ["Compare scores.", "Lower means better fit."],
            "evidence": [{"segment_id": segment["segment_id"], "quote": segment["text"]}],
            "missing_explanations": []} for g in payload["questions"]]})
    if output_type is TeachingReview:
        segment = payload["script"]["segments"][-1]
        evidence = [{"segment_id": segment["segment_id"], "quote": segment["text"]}]
        gaps = {a["objective_id"]: a["missing_explanations"] for a in payload["listener"]["answers"]}
        return TeachingReview.model_validate({
            "checks": [{"criterion": c, "verdict": "pass", "reason": "Synthetic control-flow fixture.",
                        "evidence": evidence} for c in CRITERIA],
            "objectives": [{"objective_id": g["objective_id"], "verdict": "pass", "reason": "Synthetic fixture.",
                            "evidence": evidence, "gap_assessments": [{"gap": text, "required_for_objective": True,
                                "reason": "Fixture marks the absent explanation essential."} for text in gaps[g["objective_id"]]]}
                           for g in payload["design"]["objectives"]],
            "limitations": ["Mocked verdicts do not establish real teaching quality."]})
    if output_type is EditorialReview:
        segment = payload["script"]["segments"][-1]
        return EditorialReview.model_validate({"checks": [{"criterion": c, "verdict": "pass",
            "reason": "Synthetic control-flow fixture.", "evidence": [{"segment_id": segment["segment_id"], "quote": segment["text"]}]}
            for c in CRITERIA], "limitations": ["No real editorial assessment."]})
    return None
