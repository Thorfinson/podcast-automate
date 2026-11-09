"""Model answers for a script run's story material (narration.py) and the story agent's calls in pipeline tests."""
from podcast_automate.narration import StoryMaterial
from podcast_automate.story_agent import (CritiqueScores, OrientationCheck, OrientationItem, OrientationReview,
                                          OrientationSheet, Scale, StoryCritique)


def narration_response(output_type):
    """The fixture answer for the story material or a call of the story agent's run, or None for any other schema:
    an empty story material, a one-entry orientation sheet the check finds correct, and a critique of 8 throughout."""
    if output_type is StoryMaterial:
        return StoryMaterial(items=[])
    if output_type is OrientationSheet:
        return OrientationSheet(items=[OrientationItem(name="Probe", kind="term", orientation="Ein Begriff.",
                                                       certain=True)])
    if output_type is OrientationReview:
        return OrientationReview(checks=[OrientationCheck(name="Probe", verdict="correct")])
    if output_type is StoryCritique:
        scale = Scale(score=8, reason="Trägt.")
        return StoryCritique(scores=CritiqueScores(tension=scale, interest=scale, storytelling=scale), keeps_from_10=[])
    return None
