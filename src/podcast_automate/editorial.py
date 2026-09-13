"""Shared language and teaching requirements for generation and independent reviews."""

EPISODE_FRAMING = (
    "Every complete episode needs an audible intro and outro in its spoken segments, not only chapter "
    "headings, a subject-matter hook or a final technical question. In the first chapter, briefly welcome "
    "the listener, orient them to this episode's question and purpose, and lead naturally into the opening "
    "example. A short cold open may precede that welcome; orient the listener before sustained technical "
    "explanation. Use series_context when supplied to distinguish a first, middle, final or standalone "
    "episode. In episode 1, introduce the overall series topic, why its central question matters and the "
    "path from the starting ideas to the later topics in series_context.episode_path, before narrowing "
    "to this episode. Give a connected, concise orientation, not a roll call of titles or unexplained "
    "advanced terms. Later episodes need only the relevant connection, not the full introduction repeated. "
    "Do not assume the listener has heard an episode that "
    "is not a listed prerequisite. In the last chapter, bring the opening question to a supported conclusion "
    "and give a clear, natural sign-off. If series_context.next_episode is supplied, a brief outlook may "
    "name that planned question without pre-teaching its findings. For a final or standalone episode, "
    "close the subject without promising another episode. At the end of the final episode of a series, "
    "also recap the series' main insights and connect them into an answer to its overall central question: "
    "show how the initial ideas made the later conclusions possible and which important limits remain. "
    "Make this an earned series synthesis, not only a recap of the last episode or a list of titles. "
    "Use the supplied substantive material; the episode_path describes planned topics, not proof of "
    "scientific claims or of what a previous recording actually said. Do not invent past explanations. "
    "For a standalone episode, one integrated introduction and conclusion suffice. "
    "Without series_context, omit unverified series "
    "numbering and next-episode promises. Neither a cliffhanger alone nor a list of facts is a complete "
    "outro. Keep both ends concise, specific and adult; no fixed duration or compulsory speaker alternation. "
    "Keep any effective hook and synthesis rather than repeating them in added boilerplate. No invented "
    "show name, host biography, credentials, release date, sponsor, subscription appeal or music cue. Voice "
    "preset names are not automatically host identities. Greeting, orientation based on supplied episode "
    "metadata and sign-off are editorial framing, not new research claims; they may have empty "
    "knowledge_refs. Any substantive claim, including a recap, still needs its assigned evidence. "
    "Integrate the framing within the existing first and last chapters without changing the approved "
    "outline or exceeding the episode's word budget. "
)


def episode_series_context(plan, entry):
    """Use the full approved order, also when only one episode is being generated."""
    index = next(i for i, episode in enumerate(plan.episodes) if episode.episode_id == entry.episode_id)
    following = plan.episodes[index + 1] if index + 1 < len(plan.episodes) else None
    return {"topic": plan.topic, "central_question": plan.central_question,
            "explanation_path": plan.explanation_path,
            "episode_path": [{"episode_id": episode.episode_id, "title": episode.title,
                              "central_question": episode.central_question} for episode in plan.episodes],
            "episode_number": index + 1, "episode_count": len(plan.episodes),
            "is_first": index == 0, "is_last": index == len(plan.episodes) - 1,
            "next_episode": {"episode_id": following.episode_id, "title": following.title,
                             "central_question": following.central_question} if following else None}


TERMINOLOGY = (
    "Write the surrounding explanation in the requested language, but retain established English technical "
    "terms in German prose. For machine learning use Query, Key, Value, Attention, Attention scores, "
    "Attention weights, Softmax, Embedding, Layer Normalization, Residual Connection, Training and Inference. "
    "Explain each unfamiliar term's role briefly in natural German when first needed, then use the term "
    "consistently. Do not replace these names with literal translations such as Suchanfrage, Schluessel, "
    "skalierte Passung or Mischgewichte. Do not copy awkward translated terminology from historical "
    "dossiers or outlines. This applies to plans, research questions, explanations and review feedback. "
    "Keep normal German prose and ordinary words German; do not invent English jargon. Preserve exact "
    "source quotations and identifiers. Replacing an awkward translation with its established English "
    "technical name is a terminology correction, not a new factual claim. "
)

TEACHING_SCOPE = (
    "Honor the stated prior knowledge and explanation format. University-level depth requires supported "
    "mechanisms, reasons and limits; it does not automatically require a numerical reconstruction, spoken "
    "equations, a proof or exact model parameters. A worked example can trace concrete inputs, operations "
    "and consequences qualitatively when that answers the learning objective. Require arithmetic only "
    "when necessary for the actual brief and objective. Mathematical sources may support clear German "
    "conceptual explanations. Distinguish an indispensable unsupported mechanism from optional numerical "
    "detail; neither fabricate support nor declare a research gap just because numbers are not supplied. "
    "Use the current supplied source passages to assess availability of evidence. An older outline may say "
    "a foundation could not yet be explained because evidence was missing. If current sources now support "
    "it, that availability note is resolved; do not keep it as a factual limitation or demand a new outline "
    "approval solely to remove the stale note. Preserve intentional topic exclusions and the approved "
    "episode scope, order and learning objectives. The supplied approved outline is an immutable input: "
    "ask for terminology and evidence-availability corrections in the generated design or dialogue, "
    "not edits to input objects that the output schema cannot change. "
)

CONTINUITY = (
    "The prerequisite_context contains prior episode material for editorial continuity, not new scientific "
    "evidence. Reuse the concrete example text and already established meanings when the outline carries "
    "them forward. A teaching plan describes what will be taught; it is not an already recorded transcript. "
    "If a marked position, illustrative values or a representation needed now were not specified earlier, "
    "choose and explain them in this episode within the same example. This is an editorial construction, "
    "not missing external research and not a scope change. Distinguish illustrative token boundaries from "
    "the output of a real model's tokenizer; never claim invented splits or outputs were measured. If only "
    "a prerequisite outline is supplied, do not invent a quotation or claim an earlier episode already used "
    "a particular text. Introduce the current illustration honestly within the approved scope. Preserve "
    "concrete inherited details and explicitly explain any necessary change of representation or task. "
    "Keep scientific claims grounded in the assigned findings and source passages. "
)
