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

Changing a file changes the composed prompt. The story material and orientation checkpoints are
keyed by a hash of their prompt, so an in-flight run will redo the affected call after an edit; the
script review's saved verdicts are bound to its version tags instead
([Saved verdicts](../../../docs/SCRIPTS.md#saved-verdicts)). Bump the `prompt_version` tag at the
call site (for example `story_review_repair.v3-source-refs`) whenever the meaning changes, so
receipts show which wording produced a result.

The story agent's own instructions are no prompt file: `TASK.md` of its session is composed from
`data/story_agent_task.md` and, for a revision, `data/story_agent_revision.md` (`story_agent.task_text`),
documents rather than one paragraph. Its draft is bound to that text and its material, and the
session's tag is `story_agent.SESSION_VERSION`. The prompt files of the writing prompt, its teaching
design, narration review and polishing were removed on 2026-11-01 (D-184).

`tests/test_prompts.py` checks that every file is referenced by code and every referenced name exists.
