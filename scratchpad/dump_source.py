"""Print every section of one corpus source in order (reading aid for the story agent session)."""
import sys
from pathlib import Path

from podcast_automate.story_agent import corpus_sections

sys.stdout.reconfigure(encoding="utf-8")
root, wanted = Path(sys.argv[1]), sys.argv[2]
for reference, title, text in corpus_sections(root):
    if reference.split("#", 1)[0] == wanted:
        print(f"=== {reference}\n{text}\n")
