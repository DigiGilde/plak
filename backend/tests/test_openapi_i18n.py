"""The OpenAPI schema in English and Dutch (api/openapi_i18n.py, api/openapi_nl.py).

The catalogue is keyed by the English text, so the interesting failures are
the ones a code change causes silently: a reworded English sentence whose
Dutch entry no longer matches, an entry nobody uses any more, a Dutch
document whose tags no longer agree with each other. No database needed.
"""

from __future__ import annotations

import copy
from typing import Any

import pytest

from plak import messages
from plak.api.errors import PROBLEM_CONTENT_TYPE, PROBLEM_SCHEMA_NAME
from plak.api.openapi_file import schema as english_schema
from plak.api.openapi_i18n import localise, texts
from plak.api.openapi_nl import NL

# Texts that read the same in both languages: names and loanwords Dutch uses
# as they are. Anything else that comes out of the Dutch schema unchanged is
# English that slipped into the catalogue as its own translation.
SAME_IN_BOTH = frozenset({"Sites", "Deploys", "Previews"})

# Em dash and en dash, written as code points so this file holds neither.
DASHES = frozenset({chr(0x2014), chr(0x2013)})


@pytest.fixture(scope="module")
def english() -> dict[str, Any]:
    return english_schema()


@pytest.fixture(scope="module")
def dutch(english) -> dict[str, Any]:
    return localise(english, "nl")


def _problem_examples(schema: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        response["content"][PROBLEM_CONTENT_TYPE]["example"]
        for path_part in schema["paths"].values()
        for operation in path_part.values()
        for response in operation["responses"].values()
        if PROBLEM_CONTENT_TYPE in response.get("content", {})
    ]


class TestCatalogue:
    def test_every_english_text_has_a_dutch_translation(self, english) -> None:
        missing = sorted({text for _, text in texts(english) if text not in NL})
        assert missing == [], f"add these to api/openapi_nl.py: {missing}"

    def test_no_translation_is_left_unused(self, english) -> None:
        used = {text for _, text in texts(english)}
        unused = sorted(set(NL) - used)
        assert unused == [], f"no English text matches these entries any more: {unused}"

    def test_the_dutch_schema_has_no_english_left(self, english, dutch) -> None:
        english_texts = dict(texts(english))
        unchanged = sorted(
            {
                text
                for pointer, text in texts(dutch)
                if text == english_texts[pointer] and text not in SAME_IN_BOTH
            }
        )
        assert unchanged == []

    def test_no_translation_has_an_em_or_en_dash(self) -> None:
        dashed = sorted(text for text in NL.values() if DASHES & set(text))
        assert dashed == []


class TestLocalise:
    def test_english_is_the_schema_itself(self, english) -> None:
        assert localise(english, "en") is english

    def test_dutch_leaves_the_english_schema_untouched(self, english) -> None:
        before = copy.deepcopy(english)
        localise(english, "nl")
        assert english == before

    def test_every_text_is_at_the_same_place_in_both(self, english, dutch) -> None:
        assert [pointer for pointer, _ in texts(dutch)] == [pointer for pointer, _ in texts(english)]

    def test_the_dutch_tags_agree_with_each_other(self, english, dutch) -> None:
        declared = [tag["name"] for tag in dutch["tags"]]
        assert declared == [NL[tag["name"]] for tag in english["tags"]]
        used = {tag for path_part in dutch["paths"].values() for op in path_part.values() for tag in op["tags"]}
        assert used <= set(declared)
        assert "Sessie" in declared

    def test_a_problem_example_carries_the_dutch_title(self, dutch) -> None:
        examples = _problem_examples(dutch)
        assert examples
        for example in examples:
            assert example["title"] == messages.title("nl", example["status"])

    def test_a_problem_example_carries_the_dutch_detail(self, dutch) -> None:
        details = {example["status"]: example["detail"] for example in _problem_examples(dutch)}
        assert details[404] == messages.NL["example.404"]
        assert details[403] == messages.NL["example.403"]

    def test_the_problem_schema_examples_are_dutch(self, english, dutch) -> None:
        properties = dutch["components"]["schemas"][PROBLEM_SCHEMA_NAME]["properties"]
        assert properties["title"]["examples"] == [messages.title("nl", 403)]
        assert properties["detail"]["examples"] == [messages.NL["NOT_GROUP_MEMBER.you"]]
        english_properties = english["components"]["schemas"][PROBLEM_SCHEMA_NAME]["properties"]
        assert english_properties["title"]["examples"] == [messages.title("en", 403)]

    def test_example_data_stays_as_it_is(self, english, dutch) -> None:
        """Only prose is translated: a slug or a name in an example is data a
        client sends, and it is the same data in either language."""
        for name, component in english["components"]["schemas"].items():
            if name == PROBLEM_SCHEMA_NAME:
                continue
            assert dutch["components"]["schemas"][name].get("examples") == component.get("examples"), name

    def test_a_text_without_translation_stays_english(self, english) -> None:
        untranslated = copy.deepcopy(english)
        untranslated["info"]["description"] = "Nothing in the catalogue reads like this."
        assert localise(untranslated, "nl")["info"]["description"] == "Nothing in the catalogue reads like this."
