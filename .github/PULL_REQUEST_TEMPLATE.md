## What and why

<!-- What changes, and why. Link the issue for a bigger change ("Closes #123"). -->

## Tests

<!-- What ran, how long it took, and what did not run and why. A proposed command is not an executed one.
     Selection rules: AGENTS.md, "When to run what" and "Choosing what to run". -->

- [ ] Python suite in full: `python -m unittest discover -s tests` (time: …)
- [ ] Browser suite in full: `node --test tests/studio_ui.test.cjs`
- Focused tests run during the work: …
- Not run, and why: …

## Checklist

- [ ] No test makes a real model call, download, speech synthesis or network request.
- [ ] No test was made green by retrying, skipping or relaxing an assertion; a replaced test names the protection it keeps.
- [ ] `AppError` codes are unchanged, or the change is intended and documented.
- [ ] Where a prompt's meaning changed, its `prompt_version` is bumped, and the real-model evals named in AGENTS.md ran by hand or the deferral is stated above.
- [ ] Every changed documented fact is updated in its home doc, with `last_reviewed` bumped; decisions are in DECISIONS, traps in GOTCHAS.
- [ ] A user-visible change has a line in CHANGELOG.md under `[Unreleased]`.
- [ ] New or changed Studio text exists in English and German.
- [ ] No keys, personal paths, project folders or logs are committed.
