"""The storytelling devices an episode's teaching design chooses from, and the rule that keeps them varied (D-143).

The arc of 2026-10-06 (D-140) gave every episode the same shape: a puzzle, a wrong first guess by host_b, a turn, a
recap and the next question at every chapter end. The user (2026-10-06): after two episodes a listener would guess the
structure. So each design picks one dramaturgy, an opening, a stance for host_b and an ending for every chapter from
these catalogs, fitting its material, and the code keeps neighbouring episodes and an episode's chapter ends from
repeating each other. The labels are German, as the Studio and the readable plan show them; the descriptions are the
model's instructions.
"""
from __future__ import annotations

# id: (label, how the episode is told)
DRAMATURGIES = {
    "mystery": ("Rätsel", "A puzzle or contradiction the listener wants solved. Clues come in order, a plausible "
                "first_answer stands as a guess until a finding overturns it at the turning_point, and the solution "
                "comes at the end."),
    "discovery": ("Entdeckungsgeschichte", "How the idea was found: who asked what, what they tried, what failed, "
                  "the breakthrough and what it changed. People and their reasoning carry it, told from the findings "
                  "about the authors' own work; the turning_point is the breakthrough."),
    "in_medias_res": ("Mitten hinein", "Open inside a concrete scene, failure or moment from the material, then step "
                      "back to how it came to this and build up to that moment again; the payoff revisits it with "
                      "the understanding the episode built."),
    "debate": ("Streitgespräch", "Two serious positions on an open question, each argued at its strongest. The hosts "
               "lean to different sides for a while, and the evidence decides what holds or names honestly what "
               "stays open; no strawman, no forced winner."),
    "thought_experiment": ("Gedankenexperiment", "A what-if scenario the hosts play through step by step, each step "
                           "needing the next idea; the end compares the imagined outcome with what the findings show."),
    "myth_check": ("Mythos gegen Befund", "A widespread belief, stated fairly with why it is plausible, then checked "
                   "against the findings: what holds, what does not, and the better picture that replaces it."),
    "case_autopsy": ("Fallanalyse", "One concrete case or failure taken apart layer by layer; each chapter uncovers "
                     "one cause until the whole mechanism stands."),
    "build_along": ("Bauanleitung", "Build something with the listener step by step, each step solving the problem "
                    "the step before left open; for episodes whose aim is applying."),
}
# How the first minute catches the listener (the opening the episode framing then orients from).
OPENINGS = {
    "scene": ("Szene", "a concrete situation, told as it happens"),
    "number": ("Zahl", "a striking number or result that does not fit what one expects"),
    "question": ("Frage", "the question itself, sharp enough to want an answer"),
    "quote": ("Zitat", "a short quotation of at most 25 words, attributed"),
    "belief": ("Annahme", "a belief most listeners share"),
    "anecdote": ("Anekdote", "a short true story from the material about a person or an event"),
    "problem": ("Problem", "a concrete problem someone has to solve"),
}
# How host_b takes part; the expert explains in every stance.
STANCES = {
    "skeptic": ("Skeptisch", "doubts, asks whether a result rests on one report or a measurement"),
    "enthusiast": ("Begeistert", "excited, links each step to bigger questions and what comes next"),
    "devils_advocate": ("Gegenstimme", "argues the other side on purpose, so the expert has to earn the point"),
    "learner": ("Mitdenkend", "puts things in their own words and is sometimes right before the expert says it"),
    "practice": ("Aus der Praxis", "asks what it means for someone who has to use or decide it"),
}
# How a chapter ends; the last chapter ends with the episode's conclusion.
ENDINGS = {
    "open_question": ("Offene Frage", "the question the next chapter answers"),
    "recap": ("Zwischenfazit", "one or two sentences of what the chapter established"),
    "surprise": ("Überraschung", "a finding or number that changes the picture"),
    "contradiction": ("Widerspruch", "something that does not fit yet and needs the next chapter"),
    "foreshadow": ("Vorausdeutung", "a pointer to what becomes important later"),
    "reflection": ("Nachdenken", "a quiet beat on what it means"),
    "conclusion": ("Abschluss", "the payoff and sign-off; the last chapter only"),
}
# How many earlier episodes a design must differ from in its dramaturgy; its opening and stance differ from the one
# just before. Eight dramaturgies leave enough room to also fit the material.
DRAMATURGY_DISTANCE = 2
# The kind of telling behind each dramaturgy (D-180). Orlagau's 15 designs varied the labels as the rule asked, yet 13
# of them set a guess against the evidence (mystery, debate, myth_check, thought_experiment), and a listener heard one
# pattern: host_b guesses, host_a takes the guess apart. A narrated run's episode with story material leaves the
# family of the episode before when that was a test.
FAMILIES = {"mystery": "test", "debate": "test", "myth_check": "test", "thought_experiment": "test",
            "discovery": "story", "in_medias_res": "story", "case_autopsy": "story", "build_along": "build"}
# Who voices a design's first_answer (TeachingPlan.first_answer_voice): one host, or a belief most listeners share.
# host_c only in a project with a third host (D-179; teaching.validate_teaching_plan's roles).
VOICES = ("host_a", "host_b", "host_c", "common")


def catalog():
    """The catalogs as the design prompt receives them: id -> description."""
    return {"dramaturgies": {key: text for key, (_, text) in DRAMATURGIES.items()},
            "openings": {key: text for key, (_, text) in OPENINGS.items()},
            "stances": {key: text for key, (_, text) in STANCES.items()},
            "chapter_endings": {key: text for key, (_, text) in ENDINGS.items()}}


def label(table, key):
    """The German label of ``key`` in ``table``, or the key itself."""
    return table.get(key, (key, ""))[0]


def variety_defects(design, previous, *, narrated=False, story=False):
    """Why the devices of ``design`` repeat what ``previous`` (the earlier episodes' choices, nearest last, as dicts
    with dramaturgy, opening, partner_stance and first_answer_voice) or its own chapters already use; empty when varied.
    An unset field is not checked, so a design saved before the catalogs validates as before. ``narrated`` holds a run
    with the narration stage to who voices the first answer (D-180); ``story`` says its episode has story material, so
    it may not follow a test dramaturgy with another."""
    errors = []
    last = previous[-1] if previous else {}
    if narrated:
        if design.first_answer and not design.first_answer_voice:
            errors.append("Name in first_answer_voice who voices first_answer: one of the hosts or common.")
        if design.first_answer_voice == "host_b" and last.get("first_answer_voice") == "host_b":
            errors.append(f"host_b voiced the first answer that the evidence overturns in the episode before "
                          f"({last.get('episode_id')}); let host_a or a belief most listeners share carry the guess "
                          "(first_answer_voice host_a or common), or tell this episode without a first answer.")
        if story and FAMILIES.get(design.dramaturgy) == "test" and FAMILIES.get(last.get("dramaturgy")) == "test":
            errors.append(f"dramaturgy {design.dramaturgy} sets a guess against the evidence, as {last.get('episode_id')} "
                          "did; this episode has story material: tell it as a story (discovery, in_medias_res or "
                          "case_autopsy) built on its core_story.")
    recent = [row for row in previous if row.get("dramaturgy")][-DRAMATURGY_DISTANCE:]
    if design.dramaturgy and design.dramaturgy in {row["dramaturgy"] for row in recent}:
        errors.append(f"dramaturgy {design.dramaturgy} was used by an episode just before ("
                      + ", ".join(row["episode_id"] for row in recent if row["dramaturgy"] == design.dramaturgy)
                      + "); choose another that fits this material.")
    for field in ("opening", "partner_stance"):
        value = getattr(design, field)
        if value and value == last.get(field):
            errors.append(f"{field} {value} repeats the episode before ({last.get('episode_id')}); vary it.")
    endings = [scene.ending for scene in design.scenes]
    if any(endings):
        if not all(endings):
            errors.append("Give every scene its ending, or none.")
        elif endings[-1] != "conclusion" or "conclusion" in endings[:-1]:
            errors.append("Only the last scene ends with conclusion, and it does.")
        inner = endings[:-1]
        if len(inner) >= 3 and len(set(inner)) < 2:
            errors.append("Vary the chapter endings: at least two kinds before the last chapter.")
    return errors
