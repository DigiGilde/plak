"""Fails a pull request that changes the plak plugin without a new version.

Claude Code keeps an installed plugin until the `version` in its manifest
changes, however many commits land (docs/skill.md), so a change under
`plugin/` that keeps the version never reaches an existing install. The
evals under `plugin/evals/` do not change what the plugin does and need no
new version. Both manifests also have to name the same version: the
plugin's own comes first, and a marketplace entry that says otherwise only
misleads.

Usage: check_plugin_version.py <base-ref>
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

PLUGIN_MANIFEST = "plugin/.claude-plugin/plugin.json"
MARKETPLACE = ".claude-plugin/marketplace.json"
PLUGIN_NAME = "plak"
EXEMPT = ("plugin/evals/",)
VERSION_RE = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")


def parse(version: object) -> tuple[int, int, int] | None:
    match = VERSION_RE.match(version) if isinstance(version, str) else None
    return (int(match[1]), int(match[2]), int(match[3])) if match else None


def ships(changed: list[str]) -> bool:
    """Whether any changed path is part of what an install copies and runs."""
    return any(path.startswith("plugin/") and not path.startswith(EXEMPT) for path in changed)


def problems(
    changed: list[str], base_plugin: dict | None, head_plugin: dict, head_marketplace: dict
) -> list[str]:
    head_version = head_plugin.get("version")
    head_parsed = parse(head_version)
    found = []
    if head_parsed is None:
        found.append(f"{PLUGIN_MANIFEST}: version {head_version!r} is not MAJOR.MINOR.PATCH.")
    entries = [
        entry
        for entry in head_marketplace.get("plugins", [])
        if isinstance(entry, dict) and entry.get("name") == PLUGIN_NAME
    ]
    if len(entries) != 1:
        found.append(f"{MARKETPLACE}: expected exactly one entry for the plugin '{PLUGIN_NAME}'.")
    elif entries[0].get("version") != head_version:
        found.append(
            f"{MARKETPLACE}: version {entries[0].get('version')!r} differs from {head_version!r} "
            f"in {PLUGIN_MANIFEST}."
        )
    base_version = base_plugin.get("version") if base_plugin is not None else None
    base_parsed = parse(base_version)
    if ships(changed) and head_parsed is not None and base_parsed is not None and head_parsed <= base_parsed:
        found.append(
            f"plugin/ changed, so raise the version above {base_version} in {PLUGIN_MANIFEST} and "
            f"{MARKETPLACE}: Claude Code keeps an installed copy until the version changes."
        )
    return found


def _git(*args: str) -> str:
    return subprocess.run(["git", *args], check=True, capture_output=True, text=True).stdout


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__.strip().splitlines()[-1], file=sys.stderr)
        return 2
    base = argv[1]
    changed = _git("diff", "--name-only", f"{base}...HEAD").split()
    try:
        base_plugin = json.loads(_git("show", f"{base}:{PLUGIN_MANIFEST}"))
    except subprocess.CalledProcessError:
        base_plugin = None
    head_plugin = json.loads(Path(PLUGIN_MANIFEST).read_text(encoding="utf-8"))
    head_marketplace = json.loads(Path(MARKETPLACE).read_text(encoding="utf-8"))
    found = problems(changed, base_plugin, head_plugin, head_marketplace)
    for problem in found:
        print(f"::error::{problem}")
    if not found:
        print(f"Plugin version {head_plugin.get('version')} is fine for this change.")
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
