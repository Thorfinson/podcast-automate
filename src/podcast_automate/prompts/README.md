# Prompt texts

Every instruction sent to a text model lives here as one file per prompt. Code composes them through
`podcast_automate.prompts`:

- `fragment(name)` returns a reusable rule block with exactly one trailing space, so several blocks
  concatenate into running prose (`TERMINOLOGY + TEACHING_SCOPE + ...`).
- `instructions(name, **values)` returns task instructions without trailing whitespace. The caller
  appends `"\n"` and the JSON payload; the last line of every prompt stays the JSON object.
- Placeholders are limited to `{language}` and `{maximum}`. A file may not contain other braces.
- A rule block may depend on the project. `editorial.terminology(language, topic, question)` returns
  the topic-neutral `terminology_rule` and adds `terminology_machine_learning` only for a
  machine-learning topic in a project that is not English (since 2026-10-02). The research prompts
  still compose `terminology`, the earlier rule with the machine-learning names, unchanged.

A file holds one paragraph wrapped at 100 columns. Loading joins the lines with single spaces, so
line breaks are free to change while the text is not.

Changing a file changes the composed prompt. Draft, design, polish and review checkpoints are keyed
by a hash of that prompt, so an in-flight run will redo the affected call after an edit. Bump the
`prompt_version` tag at the call site (for example `write_episode.v7-audit-notes`) whenever the meaning
changes, so receipts show which wording produced a result.

`tests/test_prompts.py` checks that every file is referenced by code and every referenced name exists.
