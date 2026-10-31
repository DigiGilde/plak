"""The one exception the release tooling raises."""

from __future__ import annotations


class ReleaseError(Exception):
    """One or more reasons to refuse, each a sentence of its own."""

    def __init__(self, *problems: str) -> None:
        super().__init__("\n".join(problems))
        self.problems = list(problems)
