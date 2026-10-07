"""Reading a CSP and the page it guards, for the tests of the platform pages."""

from __future__ import annotations

import base64
import hashlib
import re

_STYLE = re.compile(r"<style\b[^>]*>(.*?)</style\s*>", re.IGNORECASE | re.DOTALL)
_STYLE_OPEN = re.compile(r"<style\b", re.IGNORECASE)
_STYLE_ATTRIBUTE = re.compile(r"\sstyle\s*=", re.IGNORECASE)


def directives(policy: str) -> dict[str, list[str]]:
    parts = [directive.strip().split() for directive in policy.split(";")]
    return {part[0]: part[1:] for part in parts}


def style_hashes(html: str) -> list[str]:
    """The CSP source expression of every `<style>` element, as a browser
    computes it from the element's text."""
    return [
        f"'sha256-{base64.b64encode(hashlib.sha256(css.encode()).digest()).decode()}'"
        for css in _STYLE.findall(html)
    ]


def assert_runs_no_script_and_only_its_own_style(html: str, policy: str) -> None:
    """A platform page on the content host: no script may run, and the
    only style is its own `<style>`, admitted by hash. A hash does not cover a
    `style=""` attribute, so the page may not carry one."""
    found = directives(policy)
    assert found["default-src"] == ["'none'"]
    assert "script-src" not in found
    # Every opening tag has to be one the hash pattern read, or a second
    # `<style media=...>` would slip past and be blocked in the browser.
    assert len(_STYLE_OPEN.findall(html)) == len(_STYLE.findall(html)) == 1
    assert found["style-src"] == style_hashes(html)
    assert "<script" not in html.lower()
    assert not _STYLE_ATTRIBUTE.search(html)
