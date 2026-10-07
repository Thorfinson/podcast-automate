"""The companion kit of a published episode for podcast platforms: a short and a long description, chapter marks and
the sources the episode relies on (the user's wish of 2026-10-06: "Begleitmaterial: Quellen, kurze Zusammenfassung
fürs Hochladen bei Spotify etc.").

One text-model call per episode writes the two descriptions from the published script. Everything else is built from
the data, never by a model: the chapter marks from the recording's measured ``chapters.json``, the sources from the
findings the script cites (``Segment.knowledge_refs``) through the script run's frozen dossier and source index, plus
the evidence its supplementary research added. The kit lies next to the recording in
``exports/<ep>/<audio run>/publish/``, or in ``episodes/<ep>/publish/`` while the current script has no recording.
The descriptions are kept in ``episodes/<ep>/publish_descriptions.json`` under the hash of their prompt, so a rebuild
or a new recording of the same script costs no model call. Nothing here publishes anything.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone

from pydantic import Field

from .episode_audio import expression_invoke, script_text_generation
from .errors import AppError
from .models import Contract, EpisodeScript, NonEmpty, ResearchLimits, RunManifest, now
from .prompts import instructions
from .provider_pool import AdapterPool
from .research_models import SourceIndex, is_idea
from .research_patches import MAX_REJECTIONS, corrected_call
from .runner import manifest_path
from .script_models import EpisodePlan
from .storage import (atomic_text, digest, file_hash, file_lock, inside, load_project, read_optional_json, read_yaml,
                      write_json)

# The layout of kit.json and publish_descriptions.json; a saved record of another version is not reused.
KIT_VERSION = "publish_kit.v1"
# The prompt's own tag (prompts/publish_kit.txt).
PROMPT_VERSION = "publish_kit.v1"
# Spotify for Creators and Apple Podcasts take at most 4,000 characters per episode description (checked 2026-10-06).
DESCRIPTION_LIMIT = 4000
SHORT_CHARS = (80, 300)
LONG_CHARS = (400, 1500)
# Room the long description leaves for at least a heading and two sources after the chapters.
SOURCE_RESERVE = 400
# Spotify turns description lines into chapters only as MM:SS or HH:MM:SS, the first at 00:00, at least three of
# them, 30 seconds apart (support article "Episode chapters", read 2026-10-06). The width budgets every line as if
# the episode ran past an hour, so the prompt, and the saved descriptions, do not depend on the recording.
SPOTIFY_MIN_CHAPTERS = 3
SPOTIFY_MIN_GAP_SECONDS = 30
TIMESTAMP_WIDTH = len("00:00:00 ")
TEXT_FILES = ("description_short.txt", "description.txt", "sources.md")
KIT_FILES = (*TEXT_FILES, "kit.json")
DESCRIPTION_KEYS = ("short", "long", "prompt_version", "input_sha256", "written_at")
RECORDING_KEYS = ("run_id", "audio", "audio_sha256", "chapters_sha256", "duration_seconds")
# The fixed words of the kit, in the project's language: the kit is for the listeners, not for the Studio.
LABELS = {
    "de-DE": {"chapters": "Kapitel", "sources": "Quellen", "more": "Weitere Quellen: {count}",
              "sources_title": "Quellen: {title}",
              "sources_note": "Die Quellen der Rechercheergebnisse, auf die sich diese Folge stützt, in der Reihenfolge "
                              "ihrer ersten Verwendung.",
              "no_sources": "Diese Folge stützt sich auf keine Quelle der Recherche."},
    "en-US": {"chapters": "Chapters", "sources": "Sources", "more": "Further sources: {count}",
              "sources_title": "Sources: {title}",
              "sources_note": "The sources of the research findings this episode relies on, in the order of their "
                              "first use.",
              "no_sources": "This episode relies on no source of the research."},
}
# Function words that only one of the two languages uses; enough of them tell a text's language apart.
MARKERS = {
    "de-DE": frozenset("der die das und ist nicht mit ein eine einen einer wie warum für auf den dem des sich von "
                       "zum zur auch wird werden sind wir über oder aber dass im".split()),
    "en-US": frozenset("the and is are of to with how why for this that not from what we or but it its into which "
                       "their".split()),
}
WEB_ADDRESS = re.compile(r"https?://|www\.|\b[\w-]+\.(?:com|org|net|io|ai|de|edu|gov|pdf)\b", re.IGNORECASE)
MARKDOWN = re.compile(r"(?m)^\s*(?:#{1,6}\s|[-*+•]\s|\d+[.)]\s|>)|\*\*|__|\[[^\]\n]*\]\(|`")
TIMESTAMP = re.compile(r"\b\d{1,2}:\d{2}\b")
YEAR = re.compile(r"\b(1[5-9]\d\d|20\d\d)\b")
FOOTNOTE_MARKS = "†‡*§¶"


class EpisodeDescriptions(Contract):
    short: NonEmpty = Field(description="Two or three sentences for list views, plain text.")
    long: NonEmpty = Field(description="The episode description for podcast platforms, plain text, paragraphs "
                                       "separated by a blank line.")


def normalized(answer):
    """One line for the short text, paragraphs of single lines for the long one."""
    paragraphs = [" ".join(part.split()) for part in re.split(r"\n\s*\n", answer.long) if part.strip()]
    return EpisodeDescriptions(short=" ".join(answer.short.split()), long="\n\n".join(paragraphs))


def language_plausible(text, language):
    words = re.findall(r"[^\W\d_]+", text.casefold())
    own = sum(word in MARKERS[language] for word in words)
    other = sum(word in markers for key, markers in MARKERS.items() if key != language for word in words)
    return own >= 2 and own > other


def description_defects(answer, *, language, max_long):
    """What keeps the two texts from being pasted as they are; the rest of the kit is appended by code."""
    errors = []
    for name, text, low, high in (("short", answer.short, *SHORT_CHARS), ("long", answer.long, LONG_CHARS[0], max_long)):
        if not low <= len(text) <= high:
            errors.append(f"{name}: {len(text)} characters; write between {low} and {high}.")
        if WEB_ADDRESS.search(text):
            errors.append(f"{name}: no web addresses; the sources are listed separately.")
        if "_" in text:
            errors.append(f"{name}: no internal ids such as segment, finding or source ids.")
        if MARKDOWN.search(text):
            errors.append(f"{name}: plain text only, without Markdown, headings or lists.")
        if TIMESTAMP.search(text):
            errors.append(f"{name}: no timestamps or chapter list; the chapters are appended separately.")
        if "!" in text or any(ord(c) >= 0x1F000 or 0x2600 <= ord(c) <= 0x27BF for c in text):
            errors.append(f"{name}: no exclamation marks or emojis; describe calmly.")
        if not language_plausible(text, language):
            errors.append(f"{name}: write in {language}.")
    return errors


def published_episode(root, episode):
    """The published script and plan of ``episode`` with its script run, while both files are the ones that run
    published. Unlike episode_audio.reviewed_episode this does not ask for the current research: a kit describes
    the text that was read and recorded, also after a newer research run."""
    if not isinstance(episode, str) or not re.fullmatch(r"ep_[a-z0-9_]+", episode):
        raise AppError("Unbekannte Folge.", code="unknown_episode", status="blocked")
    folder = inside(root / "episodes", episode)
    pointer = folder / "latest.json" if (folder / "latest.json").is_file() else root / "episodes/latest.json"
    latest = read_optional_json(pointer, {})
    missing = AppError("Für diese Folge fehlt ein veröffentlichtes Skript.", code="unknown_episode", status="blocked")
    if not isinstance(latest, dict) or episode not in (latest.get("episode_ids") or []):
        raise missing
    path = manifest_path(root, latest.get("run_id"))
    manifest = RunManifest.model_validate(read_yaml(path))
    outputs = manifest.stages["publish"].outputs if manifest.kind == "script" and "publish" in manifest.stages else {}
    for name in ("script.yaml", "episode_plan.yaml"):
        relative = f"episodes/{episode}/{name}"
        if relative not in outputs:
            raise missing
        if not (root / relative).is_file() or file_hash(root / relative) != outputs[relative]:
            raise AppError("Skript oder Folgenplan seit der Prüfung geändert; zuerst den neuen Text prüfen.",
                           code="script_edited", status="blocked")
    script = EpisodeScript.model_validate(read_yaml(folder / "script.yaml"))
    entry = EpisodePlan.model_validate(read_yaml(folder / "episode_plan.yaml"))
    return manifest, path.parent, script, entry, outputs[f"episodes/{episode}/script.yaml"]


def current_recording(root, episode, script, script_hash, *, hash_audio=True):
    """The latest recording of exactly this script with its measured chapter starts, or None: a recording of an
    earlier text, one in parts (each part's times start at 0:00, before 2026-10-04) or one whose chapters are not the
    script's carries no times for this text."""
    report = read_optional_json(root / "episodes" / episode / "audio_latest.json", {})
    if not isinstance(report, dict) or report.get("script_sha256") != script_hash or len(report.get("parts") or []) != 1:
        return None
    part = report["parts"][0]
    try:
        audio = inside(root, part["audio"])
    except (AppError, KeyError, TypeError):
        return None
    if not audio.is_file() or not audio.is_relative_to((root / "exports" / episode).resolve()):
        return None
    measured = read_optional_json(audio.parent / "chapters.json", {})
    rows = measured.get("chapters") if isinstance(measured, dict) else None
    try:
        if [row["chapter_id"] for row in rows] != [chapter.chapter_id for chapter in script.chapters]:
            return None
        starts = [float(row["start_seconds"]) for row in rows]
    except (KeyError, TypeError, ValueError):
        return None
    return {"run_id": report.get("run_id"), "folder": audio.parent, "audio": audio.relative_to(root).as_posix(),
            "audio_sha256": file_hash(audio) if hash_audio else None,
            "chapters_sha256": file_hash(audio.parent / "chapters.json"),
            "duration_seconds": part.get("duration_seconds"), "starts": starts}


def kit_folder(root, episode, recording):
    return recording["folder"] / "publish" if recording else root / "episodes" / episode / "publish"


def clock(seconds, hours):
    total = int(seconds)
    return (f"{total // 3600:02d}:{total % 3600 // 60:02d}:{total % 60:02d}" if hours
            else f"{total // 60:02d}:{total % 60:02d}")


def chapter_marks(script, recording):
    """Chapter titles with their measured start; without a recording, titles only."""
    starts = recording["starts"] if recording else [None] * len(script.chapters)
    hours = any(start is not None and start >= 3600 for start in starts)
    rows = []
    for index, (chapter, start) in enumerate(zip(script.chapters, starts, strict=True)):
        # Platforms want the first chapter at 00:00; the first measured start is the episode's start anyway.
        seconds = None if start is None else 0.0 if index == 0 else start
        rows.append({"chapter_id": chapter.chapter_id, "title": chapter.title, "start_seconds": seconds,
                     "timestamp": None if seconds is None else clock(seconds, hours)})
    return rows


def chapter_problems(rows):
    """Why Spotify would not turn these lines into chapters (SPOTIFY_MIN_CHAPTERS, SPOTIFY_MIN_GAP_SECONDS)."""
    if not rows or rows[0]["timestamp"] is None:
        return [{"code": "no_recording"}]
    problems = [] if len(rows) >= SPOTIFY_MIN_CHAPTERS else [{"code": "too_few_chapters", "count": len(rows)}]
    problems.extend({"code": "chapter_too_short", "chapter_id": row["chapter_id"]} for row, following in zip(rows, rows[1:])
                    if following["start_seconds"] - row["start_seconds"] < SPOTIFY_MIN_GAP_SECONDS)
    return problems


def run_evidence(work):
    """What the script run worked with: each finding's evidence references, the source documents and the work
    identity of assessed sources, from the run's frozen inputs plus every supplementary research it applied
    (teaching_research.apply_foundations: a supplement adds evidence to existing findings and new sources, and a
    source the research already holds keeps the research's copy)."""
    inputs = json.loads((work / "inputs.json").read_text(encoding="utf-8"))
    dossier = inputs.get("dossier") or {}
    refs = {f["id"]: [e["reference"] for e in f.get("evidence", [])] for f in dossier.get("findings", [])}
    works = {a["source_id"]: a["work_id"] for a in dossier.get("source_assessments", []) if a.get("work_id")}
    documents = {source.id: source for source in SourceIndex.model_validate(inputs["sources"]).sources}
    for receipt in sorted(work.glob("teaching/ep_*/supplement*/receipt.json")):
        folder = receipt.parent
        if not re.fullmatch(r"supplement(?:_\d{2})?", folder.name):
            continue
        supplement = json.loads((folder / "evidence.json").read_text(encoding="utf-8"))["value"]
        for source in SourceIndex.model_validate_json((folder / "source_index.json").read_text(encoding="utf-8")).sources:
            documents.setdefault(source.id, source)
        for answer in supplement.get("explanations", []):
            added = [e["reference"] for e in answer.get("evidence", [])]
            for finding_id in answer.get("finding_ids", []):
                if finding_id in refs:
                    refs[finding_id].extend(r for r in added if r not in refs[finding_id])
    return refs, documents, works, inputs.get("research_run")


def author_names(authors):
    """A list of names. One string holding a whole author list (``A ; B`` or ``A, B, C``) is split, and footnote
    marks a PDF's metadata carries (``Ruibin Xiong†*``) are dropped."""
    names = [" ".join(name.strip(FOOTNOTE_MARKS + " ").split()) for name in authors if name and name.strip(FOOTNOTE_MARKS + " ")]
    if len(names) == 1 and (";" in names[0] or names[0].count(",") >= 2):
        names = [part.strip(FOOTNOTE_MARKS + " ") for part in re.split(";" if ";" in names[0] else ",", names[0])]
    return [name for name in names if name]


def public_url(source):
    """A web address a listener can open; a provided work's local path never leaves the machine."""
    return next((url for url in (source.final_url, source.url) if re.match(r"https?://", url or "")), "")


def episode_sources(script, refs, documents, works):
    """The sources behind the findings the script cites, in the order of their first use. Two copies of one work (the
    research's ``work_id``, else the same normalised title) are one source. An idea source (research_models.is_idea:
    the user's notes, LLM-written maps, social posts) is never evidence and never named publicly."""
    cited = list(dict.fromkeys(ref for segment in script.segments for ref in segment.knowledge_refs))
    rows, identities, omitted, missing_findings, missing_references = [], {}, {}, [], []
    for finding_id in cited:
        if finding_id not in refs:
            missing_findings.append(finding_id)
            continue
        for reference in refs[finding_id]:
            source_id = reference.split("#")[0]
            source = documents.get(source_id)
            if source is None:
                if reference not in missing_references:
                    missing_references.append(reference)
                continue
            if is_idea(source):
                row = omitted.setdefault(source_id, {"source_id": source_id, "reason": "idea_source", "finding_ids": []})
            else:
                identity = works.get(source_id) or re.sub(r"\W+", " ", source.title).strip().casefold()
                row = identities.get(identity)
                if row is None:
                    year = YEAR.search(source.published_date or "")
                    row = identities[identity] = {
                        "source_ids": [], "title": " ".join(source.title.split()),
                        "authors": author_names(source.authors), "year": year[0] if year else "",
                        "url": public_url(source), "source_type": source.source_type, "finding_ids": []}
                    rows.append(row)
                row["url"] = row["url"] or public_url(source)
                if source_id not in row["source_ids"]:
                    row["source_ids"].append(source_id)
            if finding_id not in row["finding_ids"]:
                row["finding_ids"].append(finding_id)
    return rows, list(omitted.values()), {"findings": missing_findings, "references": missing_references}


def author_text(names, limit):
    if not names:
        return ""
    if len(names) > limit:
        return f"{names[0]} et al."
    return ("; " if any("," in name for name in names) else ", ").join(names)


def attribution(row, limit):
    """The authors and the year in brackets as far as known; empty when neither is."""
    return " ".join(part for part in (author_text(row["authors"], limit), f"({row['year']})" if row["year"] else "") if part)


def closed(title):
    """A title as a sentence: a full stop unless it already ends with a question mark or the like."""
    return title if title.endswith((".", "?", "!")) else title + "."


def source_line(row):
    """One compact line of the description: authors (year): title. address."""
    lead = attribution(row, 2)
    return (f"{lead}: " if lead else "") + closed(row["title"]) + (f" {row['url']}" if row["url"] else "")


def sources_markdown(title, rows, language):
    labels = LABELS[language]
    lines = [f"# {labels['sources_title'].format(title=title)}", "",
             labels["sources_note"] if rows else labels["no_sources"], ""]
    for number, row in enumerate(rows, 1):
        # The title in italics, its closing mark outside them.
        lead, name = attribution(row, 6), re.sub(r"([\\`*_\[\]])", r"\\\1", closed(row["title"]))
        lines.append(f"{number}. " + (f"{lead}: " if lead else "") + f"*{name[:-1]}*{name[-1]}"
                     + (f" <{row['url']}>" if row["url"] else ""))
    return "\n".join(lines).rstrip() + "\n"


def chapter_line(row):
    return f"{row['timestamp']} {row['title']}" if row["timestamp"] else row["title"]


def compose_description(long, chapters, sources, language):
    """The text to paste into a platform: the long description, the chapter marks, then as many sources as fit into
    DESCRIPTION_LIMIT, the rest counted. Returns the text and the number of sources listed."""
    labels = LABELS[language]
    head = long + "\n\n" + labels["chapters"] + "\n" + "\n".join(chapter_line(row) for row in chapters)
    if len(head) > DESCRIPTION_LIMIT:
        raise AppError(f"Beschreibung und Kapitel überschreiten {DESCRIPTION_LIMIT} Zeichen.",
                       code="description_too_long", status="blocked")
    lines = [source_line(row) for row in sources]
    for count in range(len(lines), 0, -1):
        rest = len(lines) - count
        block = ("\n\n" + labels["sources"] + "\n" + "\n".join(lines[:count])
                 + ("\n" + labels["more"].format(count=rest) if rest else ""))
        if len(head) + len(block) <= DESCRIPTION_LIMIT:
            return head + block, count
    return head, 0


def long_limit(script, language):
    """The longest long description that leaves room for the chapters and some sources within DESCRIPTION_LIMIT."""
    chapters = len("\n\n" + LABELS[language]["chapters"]) + sum(TIMESTAMP_WIDTH + len(chapter.title) + 1
                                                                 for chapter in script.chapters)
    return max(LONG_CHARS[0], min(LONG_CHARS[1], DESCRIPTION_LIMIT - chapters - SOURCE_RESERVE))


def description_prompt(config, script, entry, max_long):
    episode = {"title": script.title, "central_question": entry.central_question,
               **({"series_role": entry.series_role} if entry.series_role else {})}
    payload = {"language": config.language, "series_topic": config.topic, "episode": episode,
               "limits": {"short": {"min_chars": SHORT_CHARS[0], "max_chars": SHORT_CHARS[1]},
                          "long": {"min_chars": LONG_CHARS[0], "max_chars": max_long}},
               "transcript": [{"chapter": chapter.title,
                               "text": " ".join(s.text for s in script.segments if s.chapter_id == chapter.chapter_id)}
                              for chapter in script.chapters]}
    return instructions("publish_kit", language=config.language) + "\n" + json.dumps(payload, ensure_ascii=False)


def episode_descriptions(root, config, manifest, script, entry, script_hash, *, api_key=None, fresh=False):
    """The two descriptions of the published script: the saved ones while their prompt is unchanged, else one call of
    the script run's text model with at most MAX_REJECTIONS corrections, under a fresh allowance of its own in
    ``studio/publish_kit/<episode>/<time>`` as the expression layer has (episode_audio.tag_episode). ``fresh`` asks
    anew although nothing changed. Returns the descriptions and whether they were reused."""
    max_long = long_limit(script, config.language)
    prompt = description_prompt(config, script, entry, max_long)
    signature = digest({"prompt": prompt, "schema": EpisodeDescriptions.model_json_schema(), "version": PROMPT_VERSION})
    store = root / "episodes" / script.episode_id / "publish_descriptions.json"
    saved = read_optional_json(store)
    if (not fresh and isinstance(saved, dict) and saved.get("version") == KIT_VERSION
            and saved.get("input_sha256") == signature):
        try:
            kept = EpisodeDescriptions(short=saved["short"], long=saved["long"])
        except (KeyError, ValueError):
            kept = None
        if kept is not None and not description_defects(kept, language=config.language, max_long=max_long):
            return {key: saved[key] for key in DESCRIPTION_KEYS}, True
    work = root / "studio" / "publish_kit" / script.episode_id / datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
    generation = script_text_generation(root, config, manifest.run_id)
    pool = AdapterPool(config.runtime, generation, api_key=api_key)
    limits = ResearchLimits(model_calls=MAX_REJECTIONS + 1, search_rounds=1, sources=1)

    def check(answer):
        errors = description_defects(normalized(answer), language=config.language, max_long=max_long)
        if errors:
            raise AppError(" ".join(errors), code="invalid_description", status="blocked")

    # The same charged call as the expression layer's: an unanswered call is refunded, a rejected answer stays charged.
    answer = normalized(corrected_call(expression_invoke(pool, work, limits), prompt, EpisodeDescriptions,
                                       PROMPT_VERSION, check))
    record = {"version": KIT_VERSION, "prompt_version": PROMPT_VERSION, "input_sha256": signature,
              "script_sha256": script_hash, "short": answer.short, "long": answer.long, "written_at": now(),
              "calls": work.relative_to(root).as_posix(),
              "text_generation": {key: generation.get(key) for key in ("provider", "model")}}
    write_json(store, record)
    return {key: record[key] for key in DESCRIPTION_KEYS}, False


def build_publish_kit(root, episode, *, api_key=None, fresh=False):
    """Write the companion kit of a published episode and return kit.json's data with the kit's ``folder`` (relative
    to the project) and whether the descriptions were reused (``descriptions_reused``). ``api_key`` is the OpenRouter
    key for a script run that wrote with OpenRouter; otherwise OPENROUTER_API_KEY is used. The sources are resolved
    before the model call, so a broken run folder costs no call."""
    root = root.resolve()
    config = load_project(root)
    manifest, work, script, entry, script_hash = published_episode(root, episode)
    with file_lock(root / "episodes" / episode / ".publish_kit.lock"):
        recording = current_recording(root, episode, script, script_hash)
        refs, documents, works, research_run = run_evidence(work)
        sources, omitted, unresolved = episode_sources(script, refs, documents, works)
        chapters = chapter_marks(script, recording)
        descriptions, reused = episode_descriptions(root, config, manifest, script, entry, script_hash,
                                                    api_key=api_key, fresh=fresh)
        text, listed = compose_description(descriptions["long"], chapters, sources, config.language)
        folder = kit_folder(root, episode, recording)
        atomic_text(folder / "description_short.txt", descriptions["short"] + "\n")
        atomic_text(folder / "description.txt", text + "\n")
        atomic_text(folder / "sources.md", sources_markdown(script.title, sources, config.language))
        kit = {"version": KIT_VERSION, "episode_id": episode, "title": script.title, "language": config.language,
               "script_sha256": script_hash, "script_run_id": manifest.run_id, "research_run_id": research_run,
               "recording": None if recording is None else {key: recording[key] for key in RECORDING_KEYS},
               "descriptions": descriptions, "chapters": chapters, "chapter_problems": chapter_problems(chapters),
               "sources": sources, "omitted_sources": omitted, "unresolved": unresolved,
               "description": {"characters": len(text), "limit": DESCRIPTION_LIMIT, "sources_listed": listed,
                               "sources_total": len(sources)},
               "files": {name: file_hash(folder / name) for name in TEXT_FILES}}
        # kit.json last: it marks a complete kit.
        write_json(folder / "kit.json", kit)
    return {**kit, "folder": folder.relative_to(root).as_posix(), "descriptions_reused": reused}


def saved_kit(root, episode):
    """The kit of the episode's current script and latest recording, read without a model call or hashing the MP3;
    None when there is none or it belongs to another text or recording."""
    root = root.resolve()
    try:
        _, _, script, _, script_hash = published_episode(root, episode)
    except AppError:
        return None
    recording = current_recording(root, episode, script, script_hash, hash_audio=False)
    folder = kit_folder(root, episode, recording)
    kit = read_optional_json(folder / "kit.json")
    if not isinstance(kit, dict) or kit.get("version") != KIT_VERSION or kit.get("script_sha256") != script_hash:
        return None
    bound, current = kit.get("recording") or {}, recording or {}
    if (bound.get("run_id"), bound.get("chapters_sha256")) != (current.get("run_id"), current.get("chapters_sha256")):
        return None
    return {**kit, "folder": folder.relative_to(root).as_posix()}
