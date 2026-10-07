"""plak/messages.py: the problem+json catalogue and how it is rendered.

The point of this file is the parity: the Dutch and the English side say the
same things, and every raise site says something the catalogue knows. Without
that the Dutch side rots the moment a message is added in English only.
"""

from __future__ import annotations

import ast
from collections.abc import Mapping
from dataclasses import dataclass
from importlib import import_module
from pathlib import Path

import pytest

import plak
from plak import i18n, messages
from plak.messages import Msg


def test_both_catalogues_hold_the_same_keys() -> None:
    assert set(messages.NL) == set(messages.EN)


def test_both_catalogues_cover_the_same_status_titles() -> None:
    assert set(messages.TITLES_NL) == set(messages.TITLES_EN)


def test_every_key_interpolates_the_same_values_in_both_languages() -> None:
    for key, dutch in messages.NL.items():
        assert messages.placeholders(dutch) == messages.placeholders(messages.EN[key]), key


def test_no_catalogue_entry_is_empty() -> None:
    for catalogue in (messages.NL, messages.EN):
        for key, text in catalogue.items():
            assert text.strip(), key


class TestCheckCatalogues:
    """_check_catalogues() runs once at import time, against catalogues that
    are consistent by construction, so its failure branches never fire in a
    normal run. Monkeypatching the module-level dicts it reads lets each
    invariant be broken in turn and the exact refusal checked."""

    def test_a_key_missing_from_one_catalogue_is_refused(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(messages, "NL", {"ONLY_IN_NL": "tekst"})
        monkeypatch.setattr(messages, "EN", {})
        with pytest.raises(RuntimeError, match=r"catalogues disagree on: \['ONLY_IN_NL'\]"):
            messages._check_catalogues()

    def test_a_key_that_is_neither_a_code_nor_a_fragment_is_refused(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        bad = {"not-a-usable-key!": "tekst"}
        monkeypatch.setattr(messages, "NL", bad)
        monkeypatch.setattr(messages, "EN", bad)
        with pytest.raises(RuntimeError, match="not a usable message key: not-a-usable-key!"):
            messages._check_catalogues()

    def test_catalogues_that_interpolate_different_values_are_refused(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(messages, "NL", {"SOME_KEY": "tekst met {waarde}"})
        monkeypatch.setattr(messages, "EN", {"SOME_KEY": "text with {value}"})
        with pytest.raises(RuntimeError, match="catalogues interpolate different values in: SOME_KEY"):
            messages._check_catalogues()

    def test_title_tables_that_disagree_on_status_codes_are_refused(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(messages, "TITLES_NL", {400: "Ongeldig verzoek"})
        monkeypatch.setattr(messages, "TITLES_EN", {404: "Not found"})
        with pytest.raises(RuntimeError, match="title tables disagree on the status codes they cover"):
            messages._check_catalogues()


def test_a_code_key_carries_its_code_and_a_variant_does_not_change_it() -> None:
    assert messages.code_of("UNKNOWN_SITE") == "UNKNOWN_SITE"
    assert messages.code_of("MULTIPART_INVALID.incomplete") == "MULTIPART_INVALID"


def test_a_fragment_is_never_a_code() -> None:
    assert messages.is_fragment("suggestion.nearest")
    assert not messages.is_fragment("UNKNOWN_SITE")


def test_rendering_falls_back_to_the_api_default_for_an_unknown_locale() -> None:
    messages.NL["TEST_KEY"] = "Nederlands"
    messages.EN["TEST_KEY"] = "English"
    try:
        assert messages.render("nl", Msg("TEST_KEY")) == "Nederlands"
        assert messages.render("fr", Msg("TEST_KEY")) == messages.render(
            i18n.API_DEFAULT, Msg("TEST_KEY")
        )
    finally:
        del messages.NL["TEST_KEY"]
        del messages.EN["TEST_KEY"]


def test_a_nested_message_is_rendered_in_the_language_that_holds_it() -> None:
    messages.NL["TEST_OUTER"] = "buiten: {inner}"
    messages.EN["TEST_OUTER"] = "outer: {inner}"
    messages.NL["test.inner"] = "binnen"
    messages.EN["test.inner"] = "inner"
    try:
        outer = Msg("TEST_OUTER", {"inner": Msg("test.inner")})
        assert messages.render("nl", outer) == "buiten: binnen"
        assert messages.render("en", outer) == "outer: inner"
    finally:
        for catalogue in (messages.NL, messages.EN):
            del catalogue["TEST_OUTER"]
            del catalogue["test.inner"]


def test_a_title_falls_back_when_the_status_is_not_in_the_table() -> None:
    assert messages.title("nl", 418) == messages.FALLBACK_TITLE_NL
    assert messages.title("en", 418) == messages.FALLBACK_TITLE_EN


def test_the_api_default_is_english_and_the_page_default_is_dutch() -> None:
    assert i18n.negotiate(None, default=i18n.API_DEFAULT) == "en"
    assert i18n.negotiate(None) == "nl"


def test_a_client_that_asks_for_dutch_gets_dutch_from_the_api_too() -> None:
    assert i18n.negotiate("nl", default=i18n.API_DEFAULT) == "nl"
    assert i18n.negotiate("nl-NL,nl;q=0.9,en;q=0.8", default=i18n.API_DEFAULT) == "nl"


def test_an_unsupported_language_reaches_english_whatever_the_default() -> None:
    assert i18n.negotiate("fr", default=i18n.API_DEFAULT) == "en"
    assert i18n.negotiate("fr") == "en"


def test_refusing_the_default_itself_does_not_return_it_while_dutch_is_available() -> None:
    """`default=API_DEFAULT` equals FOREIGN ("en"); explicitly refusing it must
    not come back anyway just because it is the fallback."""
    assert i18n.negotiate("en;q=0", default=i18n.API_DEFAULT) == "nl"
    assert i18n.negotiate("en;q=0, fr;q=0.8", default=i18n.API_DEFAULT) == "nl"


def test_refusing_every_supported_language_falls_back_to_the_default_anyway() -> None:
    """A header must fall back rather than fail: with both catalogues turned
    down there is nothing left to serve but the default itself."""
    assert i18n.negotiate("nl;q=0, en;q=0", default=i18n.API_DEFAULT) == "en"
    assert i18n.negotiate("nl;q=0, en;q=0") == "nl"


def test_refusing_the_page_default_still_falls_through_to_english() -> None:
    assert i18n.negotiate("nl;q=0") == "en"


def test_rendering_an_unknown_key_is_an_error_rather_than_a_raw_key() -> None:
    with pytest.raises(KeyError):
        messages.render("en", Msg("NO_SUCH_KEY"))


# -- The raise sites --------------------------------------------------------
#
# Every message the API can produce has to sit in the catalogue, in both
# languages. The check reads the source rather than the runtime, so a refusal
# that no test happens to trigger is held to it too.

_SOURCE_ROOT = Path(plak.__file__).parent

# The first argument of each is the message key, except ApiError, where the
# status comes first.
_KEY_ARGUMENT = {
    "ApiError": 1,
    "BundleError": 0,
    "IngestError": 0,
    "CiTokenError": 0,
    "GrantError": 0,
    "ExpiryError": 0,
    "Msg": 0,
    # ci/tokens.py: a shorthand that prefixes the code itself.
    "_invalid": 0,
    # api/admin.py: helpers that raise the key they are handed.
    "_commit_or_409": 1,
    "_write_one_or_404": 2,
}


@dataclass(frozen=True)
class _Site:
    """One raise site: the keys it can name, or the prefix it builds one with."""

    where: str
    keys: frozenset[str]
    prefix: str | None = None


def _module_names(path: Path) -> dict[str, object]:
    """The runtime module behind a source file, to look constants up in."""
    relative = path.relative_to(_SOURCE_ROOT.parent).with_suffix("")
    return vars(import_module(".".join(relative.parts)))


def _literals(node: ast.AST, names: Mapping[str, object], local: Mapping[str, set[str]]) -> set[str] | None:
    """Every string a key expression can evaluate to, or None when the source
    does not say."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return {node.value}
    if isinstance(node, ast.Name):
        value = names.get(node.id)
        if isinstance(value, str):
            return {value}
        return local.get(node.id)
    if isinstance(node, ast.Attribute):
        parts: list[str] = []
        current: ast.AST = node
        while isinstance(current, ast.Attribute):
            parts.append(current.attr)
            current = current.value
        if not isinstance(current, ast.Name):
            return None
        value = names.get(current.id)
        for part in reversed(parts):
            value = getattr(value, part, None)
        return {value} if isinstance(value, str) else None
    if isinstance(node, ast.IfExp):
        left = _literals(node.body, names, local)
        right = _literals(node.orelse, names, local)
        return None if left is None or right is None else left | right
    if isinstance(node, ast.JoinedStr):
        combinations = {""}
        for part in node.values:
            inner = part.value if isinstance(part, ast.FormattedValue) else part
            options = _literals(inner, names, local)
            if options is None:
                return None
            combinations = {done + option for done in combinations for option in options}
        return combinations
    return None


def _prefix(node: ast.AST, names: Mapping[str, object], local: Mapping[str, set[str]]) -> str | None:
    """The fixed head of an f-string key whose tail the source does not say."""
    if not isinstance(node, ast.JoinedStr):
        return None
    head = ""
    for part in node.values:
        inner = part.value if isinstance(part, ast.FormattedValue) else part
        options = _literals(inner, names, local)
        if options is None or len(options) != 1:
            break
        head += next(iter(options))
    return head or None


def _local_strings(tree: ast.AST, names: Mapping[str, object]) -> dict[str, set[str]]:
    """Names assigned a string somewhere in this module, however local.

    Per module rather than per scope, so a name used in two functions gets
    both sets; that only widens what the checks accept, never narrows it. Two
    passes, so a name built out of another one (`f"PREFIX.{what}"`) resolves
    once `what` is known."""
    local: dict[str, set[str]] = {}
    for _ in range(2):
        for node in ast.walk(tree):
            if not isinstance(node, ast.Assign):
                continue
            for target in node.targets:
                if not isinstance(target, ast.Name):
                    continue
                values = _literals(node.value, names, local)
                if values:
                    local.setdefault(target.id, set()).update(values)
    return local


def _raise_sites() -> list[_Site]:
    sites: list[_Site] = []
    for path in sorted(_SOURCE_ROOT.rglob("*.py")):
        source = path.read_text()
        tree = ast.parse(source)
        names = _module_names(path)
        local = _local_strings(tree, names)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            # `ApiError(...)` and `cli.GrantError(...)` alike.
            called = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
            index = _KEY_ARGUMENT.get(called)
            if index is None or len(node.args) <= index:
                continue
            argument = node.args[index]
            where = f"{path.name}:{node.lineno} {called}"
            keys = _literals(argument, names, local)
            if called == "_invalid" and keys is not None:
                keys = {f"CI_TOKEN_INVALID.{key}" for key in keys}
            if keys is not None:
                sites.append(_Site(where, frozenset(keys)))
                continue
            prefix = _prefix(argument, names, local)
            if prefix is not None:
                sites.append(_Site(where, frozenset(), prefix))
                continue
            # What is left passes a key on that was checked where it was
            # made: the `key` parameter the exception classes hand to Msg, and
            # `error.message.key` where one error is raised as another.
            passed_on = (isinstance(argument, ast.Attribute) and argument.attr == "key") or (
                isinstance(argument, ast.Name) and argument.id == "key"
            )
            assert passed_on, f"{where} builds its key in a way this scan cannot follow"
    return sites


def test_the_scan_finds_the_raise_sites_at_all() -> None:
    """A guard on the guard: were the scan to stop matching, the two checks
    below would pass while checking nothing."""
    assert len(_raise_sites()) > 100


def test_every_raise_site_names_a_key_the_catalogue_holds() -> None:
    for site in _raise_sites():
        for key in site.keys:
            assert key in messages.NL, f"{site.where} names {key!r}, which is not in the catalogue"
        if site.prefix is not None:
            assert any(key.startswith(site.prefix) for key in messages.NL), (
                f"{site.where} builds a key starting with {site.prefix!r}, which the catalogue "
                "has nothing for"
            )


def _keys_named_by_constants() -> set[str]:
    """Keys a module holds as a `KEY_...` constant. Such a constant is part of
    the module's interface, so it counts as a use even where the raise site
    that reads it sits elsewhere."""
    named: set[str] = set()
    for path in sorted(_SOURCE_ROOT.rglob("*.py")):
        for name, value in _module_names(path).items():
            if name.startswith("KEY_") and isinstance(value, str):
                named.add(value)
    return named


def test_every_catalogue_entry_is_reachable_from_a_raise_site() -> None:
    """The other direction: a message nothing can raise any more is dead
    weight that the next translation still has to carry."""
    named = {key for site in _raise_sites() for key in site.keys} | _keys_named_by_constants()
    prefixes = {site.prefix for site in _raise_sites() if site.prefix}
    for key in messages.NL:
        if key in named:
            continue
        assert any(key.startswith(prefix) for prefix in prefixes), f"{key} is raised nowhere"
