# Web search through Perplexity

Does the Perplexity web search (D-151) find what the text model's own web search found? Each case is one
sub-question search a finished research run already made with Claude's or Codex's web tools. The evaluation asks the
same question through the Perplexity search with an OpenRouter model and compares the candidate lists: how many, how
many addresses and hosts both found, which source types were chosen, and the cost.

The saved search is the baseline, not ground truth. A low overlap can mean Perplexity finds other good sources; read
a sample of differing cases before deciding. Recommend the Perplexity search in the Studio only after this has run
(verification item V-36 in the [MVP plan](../../docs/specs/2026-09-10-mvp-acceptance-plan.md)).

## Running it

```powershell
.\.venv\Scripts\python.exe evals\web_search\run.py build --project projects\<project> --cases 20   # offline
.\.venv\Scripts\python.exe evals\web_search\run.py run --model deepseek/deepseek-v4.1-flash        # asks, then sends
.\.venv\Scripts\python.exe evals\web_search\run.py score                                          # offline
```

- **The keys:** from `OPENROUTER_API_KEY` and `PERPLEXITY_API_KEY`, or asked without echo; never written.
- **What is sent:** the sub-question, its acceptance criteria and earlier queries go to OpenRouter; up to five
  queries per case go to Perplexity. No source text leaves the computer.
- **Cost:** about two model calls and one or two Perplexity requests per case, below 0.05 USD per case with
  DeepSeek V4.1 Flash; `report.json` names the spent amount.
- **The prompt:** each case recomposes the question-search instructions with the saved question, so it is close to,
  not identical with, the run's own prompt.
- **Resuming:** answers are appended to `answers.jsonl`; a new `run` sends only the missing cases.
