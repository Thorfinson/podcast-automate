"""Read-only retrieval/migration check against a saved run. No providers or downloads."""
import argparse
import json
from pathlib import Path

from podcast_automate.research_ledger import bootstrap_legacy
from podcast_automate.research_models import ResearchDiscovery, ResearchDossier, SourceIndex
from podcast_automate.research_reader import SourceReader
from podcast_automate.research_tasks import ReaderWindow
from podcast_automate.storage import inside


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("project", type=Path)
    parser.add_argument("run_id")
    parser.add_argument("--query", required=True)
    parser.add_argument("--key-term", action="append", default=[])
    parser.add_argument("--expect-reference", required=True)
    args = parser.parse_args()
    root = args.project.resolve()
    work = inside(root / "runs", args.run_id)
    discovery = ResearchDiscovery.model_validate_json((work / "discovery.json").read_text(encoding="utf-8"))
    index = SourceIndex.model_validate_json((work / "source_index.json").read_text(encoding="utf-8"))
    draft = next((path for name in ("reviewed_dossier.json", "dossier.json") if (path := work / name).exists()), None)
    dossier = ResearchDossier.model_validate_json(draft.read_text(encoding="utf-8")) if draft else None
    context_path = work / "source_context.json"
    context = json.loads(context_path.read_text(encoding="utf-8")) if context_path.exists() else []
    discovery, index, dossier, context, migration = bootstrap_legacy(root, work, discovery, index, dossier, context)
    reader = SourceReader(index)
    result = reader.search(args.query, key_terms=args.key_term)
    refs = [row["reference"] for row in result["candidates"]]
    found = args.expect_reference in refs
    read = reader.read([ReaderWindow(reference=args.expect_reference, before=1, after=1)]) if found else None
    print(json.dumps({"passed": found, "sources": len(index.sources), "sections": len(reader.entries),
        "rank": refs.index(args.expect_reference) + 1 if found else None,
        "read_sections": sum(len(s["sections"]) for s in read["context"]) if read else 0,
        "migrated_findings": len(dossier.findings) if dossier else 0,
        "latest_draft": migration["drafts"][-1] if migration["drafts"] else None,
        "top_candidates": [{key: row[key] for key in ("reference", "title", "page")} for row in result["candidates"][:4]]},
        ensure_ascii=False, indent=2))
    return 0 if found else 1


if __name__ == "__main__":
    raise SystemExit(main())
