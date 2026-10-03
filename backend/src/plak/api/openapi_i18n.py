"""The OpenAPI schema in Dutch as well as English.

The routes, models and docs.py are written in English, and FastAPI generates
the schema from them once: that English schema is what a client that asks for
nothing gets, the same default as problem+json (`i18n.API_DEFAULT`).

The Dutch schema is the English one with every text swapped through
`openapi_nl.NL`, a catalogue keyed by the English text itself, the way
gettext keys a translation by its msgid. Changing an English sentence
therefore drops its translation instead of leaving a stale Dutch one behind;
test_openapi_i18n.py fails until the catalogue has the new sentence.

What counts as text: every `summary` and `description`, and every tag name,
both in the top-level `tags` list and in the `tags` of an operation, so the
Dutch document is consistent with itself. Swagger UI ignores `x-displayName`,
so translating only a display name would leave English tag headings in the
Dutch docs. Example data is not text and stays as it is, except the `title`
and `detail` of a problem+json example, which come from plak/messages.py and
are swapped through that catalogue instead.
"""

from __future__ import annotations

import copy
from collections.abc import Iterator
from typing import Any, Final

from plak import i18n, messages
from plak.api.errors import PROBLEM_CONTENT_TYPE, PROBLEM_SCHEMA_NAME
from plak.api.openapi_nl import NL

TEXT_KEYS: Final[frozenset[str]] = frozenset({"summary", "description"})

# Values under these keys are data a client could send or receive, not prose.
_DATA_KEYS: Final[frozenset[str]] = frozenset({"example", "examples", "default", "enum", "const"})


def texts(schema: dict[str, Any]) -> Iterator[tuple[str, str]]:
    """Every translatable text with its location, as (JSON pointer, text)."""
    yield from _texts(schema, "")


def _texts(node: Any, pointer: str) -> Iterator[tuple[str, str]]:
    if isinstance(node, dict):
        for key, value in node.items():
            if key in _DATA_KEYS:
                continue
            here = f"{pointer}/{key}"
            if key in TEXT_KEYS and isinstance(value, str):
                yield here, value
            elif key == "tags" and isinstance(value, list):
                for index, tag in enumerate(value):
                    if isinstance(tag, str):
                        yield f"{here}/{index}", tag
                    else:
                        yield f"{here}/{index}/name", tag["name"]
                        yield from _texts(tag, f"{here}/{index}")
            else:
                yield from _texts(value, here)
    elif isinstance(node, list):
        for index, item in enumerate(node):
            yield from _texts(item, f"{pointer}/{index}")


def _problem_texts(locale: str) -> dict[str, str]:
    """English problem+json title or fixed detail -> the same in `locale`."""
    table = {
        messages.title(i18n.API_DEFAULT, status): messages.title(locale, status)
        for status in messages.TITLES_EN
    }
    for key, template in messages.EN.items():
        if not messages.placeholders(template):
            table[template] = messages.render(locale, messages.Msg(key))
    return table


def localise(schema: dict[str, Any], locale: str) -> dict[str, Any]:
    """The schema in `locale`. English is the schema itself, untouched."""
    if locale == i18n.API_DEFAULT:
        return schema
    translated = copy.deepcopy(schema)
    _translate(translated, NL)
    _translate_problems(translated, _problem_texts(locale))
    return translated


def _translate(node: Any, catalogue: dict[str, str]) -> None:
    # A text missing from the catalogue stays English rather than failing the
    # whole document; test_openapi_i18n.py is what keeps that from happening.
    if isinstance(node, dict):
        for key, value in node.items():
            if key in _DATA_KEYS:
                continue
            if key in TEXT_KEYS and isinstance(value, str):
                node[key] = catalogue.get(value, value)
            elif key == "tags" and isinstance(value, list):
                for index, tag in enumerate(value):
                    if isinstance(tag, str):
                        value[index] = catalogue.get(tag, tag)
                    else:
                        tag["name"] = catalogue.get(tag["name"], tag["name"])
                        _translate(tag, catalogue)
            else:
                _translate(value, catalogue)
    elif isinstance(node, list):
        for item in node:
            _translate(item, catalogue)


def _translate_problems(schema: dict[str, Any], table: dict[str, str]) -> None:
    for path_part in schema.get("paths", {}).values():
        for operation in path_part.values():
            for response in operation.get("responses", {}).values():
                example = response.get("content", {}).get(PROBLEM_CONTENT_TYPE, {}).get("example")
                if example is None:
                    continue
                for field in ("title", "detail"):
                    example[field] = table.get(example[field], example[field])
    # register_openapi always adds Problem, with an example on both fields.
    properties = schema["components"]["schemas"][PROBLEM_SCHEMA_NAME]["properties"]
    for field in ("title", "detail"):
        properties[field]["examples"] = [table.get(example, example) for example in properties[field]["examples"]]


__all__ = ["TEXT_KEYS", "localise", "texts"]
