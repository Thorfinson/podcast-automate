"""Model answers for the narration stage (narration.py) in pipeline tests."""
from podcast_automate.narration import NarrationReview, NarrationScores, Score, StoryMaterial


def narration_review(points=()):
    """A narration review with middling scores and the given points (none by default, so no rework follows)."""
    def score():
        return Score(score=5, reason="A fixture is not a listener.")
    return NarrationReview(scores=NarrationScores(tension=score(), interest=score(), storytelling=score()),
                           points=list(points), strengths=[])


def narration_response(output_type):
    """The fixture answer for a narration-stage schema, or None for any other schema."""
    if output_type is StoryMaterial:
        return StoryMaterial(items=[])
    if output_type is NarrationReview:
        return narration_review()
    return None
