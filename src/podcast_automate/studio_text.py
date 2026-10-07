"""The Studio's interface language (D-152): the catalogs, the language a request is answered in, and the Studio's own
messages in that language.

A catalog (``locales/<language>.json``) is flat: dotted keys, plain-text values with ``{name}`` placeholders and no
markup. A plural value is an object ``{"one": …, "other": …}``, chosen by a ``count`` parameter. ``de.json`` holds the
German the Studio has always shown, so German output stays byte-identical; English is the fallback for a key another
language lacks, and a key no catalog has renders as itself.

Only the Studio's own boundary imports this module (``studio*.py`` and ``production_report.py``; the import guard is
in tests/test_studio_text.py). Prompts, hashes, receipts, the command line and the pipeline's messages stay in their
language: a message a pipeline module raises reaches the browser as it is, marked with its detected language.
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
from contextlib import contextmanager
from contextvars import ContextVar
from importlib.resources import files
from pathlib import Path

from .errors import AppError
from .storage import read_text, write_json

LANGUAGES = ("de", "en")
# What the user can choose; "auto" follows the browser's Accept-Language.
SETTINGS = ("auto", "de", "en")
FALLBACK = "en"
# A request without Accept-Language (tests, the scheduler, a command-line client) is answered in German, as before.
NO_HEADER = "de"
SETTING_FILE = Path(".studio") / "ui.json"
PLACEHOLDER = re.compile(r"\{(\w+)\}")

_current: ContextVar[str | None] = ContextVar("studio_ui_language", default=None)
_catalogs: dict = {}
_lock = threading.Lock()
_missing: set = set()


class Localized(str):
    """Text in one interface language that keeps its catalog key, its parameters and the language it is in. An
    AppError raised with it carries that language to the error response (``message_language``) and to a stop."""

    def __new__(cls, text, *, key="", params=None, language=NO_HEADER):
        value = super().__new__(cls, text)
        value.key, value.params, value.language = key, dict(params or {}), language
        return value

    def __reduce__(self):
        return (_localized, (str(self), self.key, self.params, self.language))


def _localized(text, key, params, language):
    return Localized(text, key=key, params=params, language=language)


def resource(language):
    if language not in LANGUAGES:
        raise ValueError(f"Unknown interface language: {language!r}")
    return files("podcast_automate").joinpath("locales", f"{language}.json")


def catalog(language):
    """The catalog of ``language`` as a dict, read again only after its file changed: a catalog edited while the
    Studio runs reaches the next page load, as app.js does."""
    source = resource(language)
    try:
        info = os.stat(source)
        signature = (info.st_mtime_ns, info.st_size)
    except (TypeError, OSError):
        signature = None  # A resource inside an archive: read once.
    with _lock:
        hit = _catalogs.get(language)
    if hit is not None and (signature is None or hit[0] == signature):
        return hit[1]
    data = json.loads(source.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"locales/{language}.json is not an object")
    with _lock:
        _catalogs[language] = (signature, data)
    return data


def requested():
    """The language of the request being answered, or None outside one (the scheduler, a worker, a test)."""
    return _current.get()


def current_language():
    """The language of the request being answered; German outside a request (NO_HEADER)."""
    return _current.get() or NO_HEADER


@contextmanager
def using(language):
    """Answer everything inside in ``language`` ("de" or "en"; anything else counts as no header)."""
    token = _current.set(language if language in LANGUAGES else NO_HEADER)
    try:
        yield
    finally:
        _current.reset(token)


def lookup(language, key):
    """The catalog value of ``key`` and the language it was found in: ``language``, else English, else the key."""
    for candidate in dict.fromkeys((language, FALLBACK)):
        value = catalog(candidate).get(key)
        if value is not None:
            return value, candidate
    if key not in _missing:
        _missing.add(key)
        logging.getLogger(__name__).warning("Missing Studio text key: %s", key)
    return key, FALLBACK


def plural(value, params):
    if isinstance(value, dict):
        return value.get("one" if params.get("count") == 1 else "other", value.get("other", ""))
    return value


def render(value, params):
    return PLACEHOLDER.sub(lambda match: str(params[match[1]]) if match[1] in params else match[0], value)


def text(language, key, /, **params):
    """``key`` in ``language`` with ``params`` filled in, as Localized."""
    value, found = lookup(language, key)
    return Localized(render(plural(value, params), params), key=key, params=params, language=found)


def t(key, /, **params):
    """``key`` in the current request's language (current_language) with ``params`` filled in, as Localized."""
    return text(current_language(), key, **params)


def resolve(header, setting="auto"):
    """The language a request is answered in: an explicit setting; else the best of de and en in ``header``
    (Accept-Language with q-values, the first of equal ones); English when the header names neither; German without a
    header at all."""
    if setting in LANGUAGES:
        return setting
    if header is None or not header.strip():
        return NO_HEADER
    best, best_q = None, 0.0
    for part in header.split(","):
        tag, *parameters = part.strip().split(";")
        primary = tag.strip().lower().split("-")[0]
        q = 1.0
        for parameter in parameters:
            name, _, value = parameter.strip().partition("=")
            if name.strip().lower() == "q":
                try:
                    q = float(value.strip())
                except ValueError:
                    q = 0.0
        if primary in LANGUAGES and q > best_q:
            best, best_q = primary, q
    return best or FALLBACK


def setting_path(workspace):
    return Path(workspace) / SETTING_FILE


def has_projects(workspace):
    return any((Path(workspace) / "projects").glob("*/project.yaml"))


def setting(workspace):
    """The saved choice ("auto", "de" or "en"). It lives in .studio/ui.json, not in the workspace settings: their mere
    existence switches projects to workspace values. A workspace that had projects before the choice existed keeps
    German until the user chooses (D-152): current users do not flip to their browser's language."""
    path = setting_path(workspace)
    try:
        value = json.loads(read_text(path)).get("ui_language")
    except (OSError, ValueError, AttributeError):
        value = None
    if value in SETTINGS:
        return value
    return "de" if has_projects(workspace) else "auto"


def save_setting(workspace, value):
    if value not in SETTINGS:
        raise AppError(t("server.ui_language.invalid"), code="invalid_request")
    write_json(setting_path(workspace), {"ui_language": value})
    return value


def remember_auto(workspace):
    """Keep "auto" for a workspace whose first project is being created: the legacy default (setting) must not switch
    a new user to German once a project exists."""
    if not setting_path(workspace).exists() and not has_projects(workspace):
        write_json(setting_path(workspace), {"ui_language": "auto"})


def merged(language):
    """English with ``language`` over it: what the browser renders, English for a key the language lacks."""
    return {**catalog(FALLBACK), **catalog(language)}


def locale_script(language, choice):
    """The script /locale.js serves before app.js: the setting, the resolved language and the merged catalog."""
    payload = {"setting": choice, "language": language, "catalog": merged(language)}
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    # JSON is valid JavaScript except for these two line terminators in older engines.
    body = body.replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")
    return f"const STUDIO_LOCALE={body};\n"
