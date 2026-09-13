"""Shared language and teaching requirements for generation and independent reviews."""

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
