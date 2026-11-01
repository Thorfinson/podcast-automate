"""The hosts' parts as every script prompt and the story agent are told them, and the cast note of a third host.

Until 2026-11-01 these lived in ``polishing.py``, the dialogue pass the story agent replaced (D-184). The texts are
unchanged byte for byte: they are part of every script run's inputs (``host_roles``) and of the prompts whose
checkpoints a resumed run keeps.
"""
from __future__ import annotations

import json

from .models import THIRD_HOST
from .prompts import instructions

# The version every script run's inputs name since the dialogue pass of 2026-09 (scripting.run_inputs). Polishing ended
# with D-184; the value stays so the inputs, and with them the hashes of runs that wait, stay as they were, and so a
# recording can tell a run that once had the stage (episode_audio.reviewed_episode).
POLISH_VERSION = "dialogue_polish.v1"
HOST_ROLES = {
    "host_a": "The expert: calm, precise and analytical. Develop mechanisms and relevant details, "
              "explain why each step follows and acknowledge uncertainty. Respond to the partner's actual objection.",
    "host_b": "The curious, thoughtful conversation partner: voice questions that arise while listening, "
              "test assumptions and connect details to the larger significance. Bring a reasonable alternative "
              "or deduction rather than ask for the next definition. Do not act unintelligent or merely praise the expert.",
}


def host_roles(config=None):
    """The part of every host the prompts describe: HOST_ROLES, and the third host's own role when the brief has one
    (D-179). A project of two hosts gets HOST_ROLES itself, so its prompts and hashes stay as they were."""
    third = getattr(config, "third_host", None)
    return {**HOST_ROLES, THIRD_HOST: third.role} if third else HOST_ROLES


def cast_note(config=None) -> str:
    """What a script run's model call is told before its prompt when the project has a third host (D-179): the rule
    for three hosts and the third host's name and part. Empty for two hosts, whose prompts stay byte-identical; the
    payload's JSON stays the last line of every prompt."""
    third = getattr(config, "third_host", None)
    if not third:
        return ""
    return (instructions("third_host") + "\n" + json.dumps(
        {"third_host": {"speaker_id": THIRD_HOST, "name": third.name, "role": third.role}}, ensure_ascii=False) + "\n")
