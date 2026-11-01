"""The git calls, run in one repository."""

from __future__ import annotations

import subprocess
from pathlib import Path

from toon_release.errors import ReleaseError


class Git:
    """The git calls, run in one repository; paths are relative to its root."""

    def __init__(self, cwd: Path) -> None:
        self.root = Path(self.run("rev-parse", "--show-toplevel", cwd=cwd).strip())

    def run(self, *args: str, cwd: Path | None = None) -> str:
        # git via PATH, like the workflows; every argument comes from this package.
        result = subprocess.run(  # noqa: S603
            ["git", *args],  # noqa: S607
            cwd=cwd or self.root,
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode:
            raise ReleaseError(f"git {' '.join(args)}: {result.stderr.strip()}")
        return result.stdout

    def tags(self) -> list[str]:
        return self.run("tag", "--list").split()

    def show(self, ref: str, path: str) -> str | None:
        try:
            return self.run("show", f"{ref}:{path}")
        except ReleaseError:
            return None

    def changed(self, since: str, until: str = "HEAD") -> list[str]:
        return [p for p in self.run("diff", "--name-only", "--no-renames", "-z", since, until).split("\0") if p]

    def merge_base(self, ref: str) -> str:
        return self.run("merge-base", ref, "HEAD").strip()

    def tree(self, ref: str, directory: str) -> dict[str, str]:
        """Every file under `directory` at `ref`, by its name below it."""
        names = self.run("ls-tree", "-r", "-z", "--name-only", ref, "--", directory).split("\0")
        return {name[len(directory) :]: self.show(ref, name) or "" for name in names if name}

    def modes(self, ref: str, directory: str) -> dict[str, str]:
        """The git mode of every entry under `directory` at `ref`: 100644 for
        a plain file, 100755 executable, 120000 a symlink, 160000 a submodule."""
        entries = self.run("ls-tree", "-r", "-z", ref, "--", directory).split("\0")
        return {entry.split("\t", 1)[1][len(directory) :]: entry.split(" ", 1)[0] for entry in entries if entry}

    def changes(self, commit: str) -> list[tuple[str, str]]:
        """What `commit` did to its first parent: (status, path), with A, M
        or D as the status."""
        fields = self.run("diff", "--name-status", "--no-renames", "-z", f"{commit}^", commit).split("\0")
        return [(status, path) for status, path in zip(fields[::2], fields[1::2], strict=False) if path]

    def head(self) -> str:
        return self.run("rev-parse", "HEAD").strip()
