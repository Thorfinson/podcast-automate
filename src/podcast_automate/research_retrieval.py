"""Question-specific retrieval from saved sections, without network or model calls."""
from __future__ import annotations

import re
import unicodedata


STOP_WORDS = set("""the and for with from that this these those into what which how why are was were
has have does can its not all any also only full text original paper source sources research evidence
der die das den dem des ein eine einer eines einem einen und oder mit von zur zum auf aus bei für
wie was welche welcher welches warum sind ist wird werden wurde wurden sich noch auch als durch
nicht einer diese dieser dieses diesen einem anhand bitte belege quellen abschnitt abschnitte""".split())


def terms(text):
    text = "".join(c for c in unicodedata.normalize("NFKD", text.casefold()) if not unicodedata.combining(c))
    return [word.rstrip("s") if len(word) > 4 else word
            for word in re.findall(r"[^\W\d_]{3,}", text) if word not in STOP_WORDS]


def references(context):
    return {section["reference"] for source in context for section in source["sections"]}


def select_context(context, refs):
    return [{**source, "sections": sections} for source in context
            if (sections := [s for s in source["sections"] if s["reference"] in refs])]


def merge_context(*contexts):
    """Keep every previously presented section, including anchors of untouched findings."""
    combined = {}
    for context in contexts:
        for source in context:
            if source["source_id"] not in combined:
                combined[source["source_id"]] = {**source, "sections": []}
            row = combined[source["source_id"]]
            seen = {s["reference"] for s in row["sections"]}
            row["sections"].extend(s for s in source["sections"] if s["reference"] not in seen)
    return list(combined.values())
