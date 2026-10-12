# Example project: Geld verstehen

A German podcast for twelve-year-olds about money, from pocket money to an economy of AI and robots, with three
speakers: Mara explains, Leo (twelve) asks and objects, and Ea-nasir, the copper merchant of Ur to whom the oldest known
customer complaint is addressed, brings the view of someone 3,750 years old. It is the reference project for the
third host ([D-174](../../docs/DECISIONS.md)).

| File | Content |
| --- | --- |
| `project.yaml` | The brief: audience, the six learning stages in `depth_request`, focus questions, excluded topics, the two hosts' names and the `third_host` |
| `style_notes.md` | The standing editorial notes: the three voices, the shape of every episode, the storytelling rules, controversy and safeguards, the season-long story |
| `inhaltsverzeichnis.md` | The planned table of contents (26 numbered episodes, four of them in two parts: 30 episodes), to give as feedback on the outline when the planned one differs |

## Set it up

1. Create a project: `pla init projects/geld-verstehen --topic "Geld verstehen – ein Podcast für 12-Jährige"`, or
   create one in the Studio.
2. Copy `project.yaml` and `style_notes.md` from this folder into the project folder, replacing the generated
   `project.yaml`. With local Qwen, set `runtime.tts_python` to the Qwen environment again (`pla init --tts-python`
   writes it).
3. In the Studio's settings, choose the voices. „Stimme Host C" sets Ea-nasir's voice for every route; left on
   automatic, the provider's first free voice speaks. On Google, „Stil von Host C" gives him a style of his own.
4. Start with a trial: set `trial: true` in `project.yaml` for one episode of at most 20 minutes before the whole
   series ([Trial project](../../docs/BUSINESS_LOGIC.md#trial-project)). A full series of this length is a large job;
   see [What it costs](../../README.md#what-it-costs).

When the outline comes for approval and differs from `inhaltsverzeichnis.md`, give that file's content as feedback.

## What the third host changes

Every script call is told the cast first, the script check requires all three hosts in every episode, and a Google
recording speaks at most two of them per passage, as Google's speech accepts two speakers per request
([Three hosts](../../docs/SCRIPTS.md#three-hosts), [AUDIO](../../docs/AUDIO.md#three-hosts)).
