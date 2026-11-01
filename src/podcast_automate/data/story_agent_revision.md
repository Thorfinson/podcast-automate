
## This is a revision

The episode was published, and the operator has heard it. `published.json` holds it as published, and
material.json's `revision.feedback` says what the operator wants changed. `sources_used.json` already lists the
corpus sections the published episode used. These steps replace "How to work":

1. Read the feedback and published.json, then the parts of material.json, earlier/ and the corpus the feedback
   touches.
2. Copy published.json to draft.json. Change what the feedback asks and what the change touches, nothing else:
   every other segment keeps its text, speaker, references and segment_id. Where the feedback asks for more than a
   few segments (a new scene, another ending, another order of chapters), plan the change in plan.md first;
   outline.json may then set new chapters.
3. Check the facts of every segment you change as step 9 of "How to work" says, and add every corpus section you
   newly use to sources_used.json.
4. Run `python check.py` until it passes.
5. Run `python critique.py` only where the change touched the telling, at most {critiques} times, and change only
   what a point names within what the feedback asked for.
6. Finish when check.py passes and no fact doubt is left. Say in one line what you changed.
