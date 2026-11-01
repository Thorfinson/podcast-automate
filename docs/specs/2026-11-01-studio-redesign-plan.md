---
title: Studio redesign — three places, the Sendestudio look, light and dark
status: in progress
last_reviewed: 2026-11-01
---

# Studio redesign (D-185, D-186)

The user accepted the design and layout review of 2026-11-01 (artifact 8b6xFzTttiXEBzcJNyAPZK) in full and chose the
look „Sendestudio“ (C) from the canvas FvvxWcWceW47q47hyDVeQC, with a light and a dark theme the viewer can switch.

## Decisions

- **D-185 Three places.** A project has three places instead of six step pages:
  - **Serie**: the outline and the production as one episode board.
  - **Folge**: one episode, to read and to hear.
  - **Grundlagen**: the brief and the research.

  The six step keys stay internal (`pageKeys`), so old links (`?step=…`) and the navigate tool keep working. Every
  page has one primary action in its head. The running job shows in one strip at the top of the page. Explanations
  open on request.
- **D-186 Sendestudio look.**
  - **Tokens** for colour, type, spacing, radius and state, in a light and a dark theme.
  - **Theme switch:** System, Hell or Dunkel, set on `<html data-theme>`. The choice is kept per device in a cookie,
    because `app.js` must not use browser storage (a test gate).
  - **Fonts:** IBM Plex Sans and IBM Plex Mono, served by the Studio.
  - **Actions:** yellow, `#F2C94C` with dark text.
  - **Five state badges:** läuft (cyan), wartet auf dich (orange), angehalten (rose), fertig (green), geplant (grey).
  - **Motif:** timecodes and waveforms.

## Steps

1. **Fundament.**
   - `style.css` on tokens, in both themes, plus the theme switch.
   - One shell. The topbar project picker and the duplicate „Neu“ go; the job strip is the one place for the running
     job; the Maschinenraum opens from the strip.
   - Raw ids are replaced; dead CSS and JS go.
   - The four small bugs are fixed: `overflow-wrap` on panels, the source names in the rail, the coverage list's
     number, `.tag.ok`.
2. **Serie.**
   - The outline and production pages become one board: one row per episode, with its way (plan, text, check,
     sound) and its next action.
   - The chapters open on request.
   - Writing in order and the series review sit in the page head.
3. **Folge.**
   - The scripts and audio pages become one page with tabs: Lesen, Hören, Prüfhinweise, Begleitmaterial.
   - Episode numbers come from the outline.
   - The approval is the primary action in the head.
4. **Handy and copy diet.**
   - On the phone: a tab bar at the bottom, a sticky primary action, the decision first, and a one-line head.
   - Long stop messages and panel explanations shrink to one sentence each; the rest moves behind a disclosure.

## Working setup (this session)

- **Work copy:** `scratchpad/redesign`, a copy of `src`, `tests`, `docs`, `scripts` and the root docs. The baseline
  hashes are in `scratchpad/redesign-baseline.sha256`. Before syncing back, compare main against the baseline so that
  nobody else's edits are overwritten.
- **Preview:** `scratchpad/preview_server.py` serves the work copy on 127.0.0.1:8766 against the trimmed workspace
  copy `scratchpad/preview-ws`. Starting, stopping, approving, uploading and restarting are blocked, and
  `subprocess.Popen` is refused. The live Studio on 8765 and its running job are not touched.
- **Screenshots:** DevTools device emulation (`scratchpad/phone-shots.mjs`, `check-review.mjs`), not
  `--window-size`.
- **Tests:** the browser suite is `node --test tests/studio_ui.test.cjs`, run from the work copy. Python tests for
  changed modules only, run from the work copy with `PYTHONPATH=src`.

## Progress

- [x] 1 Fundament (browser suite 220/220):
  - `style.css` rewritten on tokens, light and dark. The theme select sits in the sidebar (`chooseTheme`, cookie
    `studio_theme`). IBM Plex is in `web/fonts` and served by the `/fonts/<name>.woff2` route in `studio.py`
    (pyproject package-data).
  - The sidebar shows the places (`PLACES`, `placeOf`, `placeTarget`, `placeState`); `heading()` draws the page tabs
    with their states and time hints. The topbar is only the job strip (`renderJob` wraps `renderJobView`; body class
    `has-strip`). The topbar project picker and „Neu“ are gone.
  - Overview cards have a state chip, a counts line, an episode strip and the download in the ⋯ menu.
  - `voiceName` never prints an ElevenLabs id; `stageNames` has `story`. The dead progress code is gone.
- [x] 2 Serie. An approved outline renders `renderSeries`: the head holds „Nächste Folge schreiben“ and „Alle …
  restlichen“; each row comes from `seriesRow` with its lifeline, its chapters on request and its action
  (`data-open-episode` → `openEpisode`). The old writing panel is gone.
- [x] 3 Folge.
  - `episodeNumber` and `seriesTotal` number episodes by the outline, and the two pickers choose the same episode.
  - `episodeHeading` makes the place one page per episode, with the tabs „Lesen“, „Prüfhinweise“, „Hören“ and
    „Begleitmaterial“ (`episodeTab`, `tabOn`). The tabs keep the step numbers of the reading and the recording page.
  - „Weiter zur Audio-Freigabe“ is the reading tab's head action.
  - The approval stays in its card, because it is bound to the checkbox there.
  - The notes and the kits left the reading margin and the recording page.
- [x] 4 Handy, in part.
  - Done: the tab bar at the foot (`--tabbar`) with every sticky foot offset by it; the eyebrow hidden; the reader's
    decision first (`.doc-rail{display:contents}`); the job strip in one line; the theme choice on the settings page.
  - Copy diet:
    - `howItWorks` folds the provider's, the revision's and the production's explanations.
    - A production step shows its description only while it runs or stopped.
    - A card's stop message is its first sentence (`firstSentence`).
    - The brief's focus questions, exclusions and seed links are lists.
- [x] Docs: STUDIO (Look and layout, phone, navigation, overview), DECISIONS D-185 and D-186, CHANGELOG. The last two
  are inserted into main's current text by `scratchpad/sync_redesign.py`, never copied, because another session adds
  D-187.
- [ ] Sync back into main after the baseline check; the user restarts the Studio when no job runs (font route)

## Verification

- V-74 Both themes at 1440, 1024 and 390 px on every place; no horizontal overflow.
- V-75 Contrast: text at least 4.5:1, borders and controls at least 3:1, in both themes.
