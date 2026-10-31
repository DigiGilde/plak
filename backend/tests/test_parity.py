"""The rules that live in more than one place, held together.

The backend is the authority. The admin SPA and the CLI keep a copy of the
slug rule, the upload limits and the accepted archive extensions so they can
refuse early; nothing else says that a copy still matches. The frontend and
the CLI sources are read as text: the CLI is not installed in this venv, and
the frontend is TypeScript.
"""

import re
from pathlib import Path

from plak.config import Settings
from plak.constants import SLUG_RE

ROOT = Path(__file__).resolve().parents[2]
SLUG_TS = ROOT / "frontend" / "src" / "composables" / "slug.ts"
PACKING_TS = ROOT / "frontend" / "src" / "components" / "site" / "packing.ts"
CLI = ROOT / "cli" / "plak_cli" / "__init__.py"
UNPACKER = ROOT / "backend" / "src" / "plak" / "ingest" / "unpacker.py"

LIMIT_SETTINGS = {
    "files": "ingest_max_files",
    "depth": "ingest_max_depth",
    "file": "ingest_max_file",
    "total": "ingest_max_total",
}


def _capture(pattern: str, path: Path) -> str:
    match = re.search(pattern, path.read_text(encoding="utf-8"), re.MULTILINE | re.DOTALL)
    assert match, f"{pattern!r} not found in {path.relative_to(ROOT)}"
    return match.group(1)


def _backend_extensions() -> set[str]:
    """The extensions `unpack` dispatches on: its `name.endswith(...)` calls."""
    source = UNPACKER.read_text(encoding="utf-8")
    calls = re.findall(r"name\.endswith\(([^)]*\)?)\)", source)
    extensions = {ext for call in calls for ext in re.findall(r'"(\.[a-z.]+)"', call)}
    assert extensions, "no name.endswith(...) dispatch found in unpacker.py"
    return extensions


def test_the_slug_rule_of_the_cli_is_the_one_of_the_backend():
    cli = _capture(r'^SLUG_RE = re\.compile\(r"([^"]+)"\)', CLI)
    assert cli == SLUG_RE.pattern


def test_the_slug_rule_of_the_frontend_is_the_one_of_the_backend():
    # The hyphen is escaped in the TypeScript string (see slug.ts), and the
    # string is unanchored: the backend pattern is that, between ^ and $.
    ts = _capture(r"^export const SLUG_PATTERN = '([^']+)';", SLUG_TS)
    assert f"^{ts.replace(chr(92) * 2 + '-', '-')}$" == SLUG_RE.pattern


def test_the_upload_limits_of_the_frontend_are_the_ones_of_the_backend():
    block = _capture(r"^export const LIMITS = \{(.*?)\} as const;", PACKING_TS)
    limits = {}
    for key, expression in re.findall(r"(\w+): ([\d\s*]+),", block):
        product = 1
        for factor in expression.split("*"):
            product *= int(factor)
        limits[key] = product
    assert set(limits) == set(LIMIT_SETTINGS)
    for key, setting in LIMIT_SETTINGS.items():
        assert limits[key] == Settings.model_fields[setting].default, key


def test_the_archive_extensions_of_the_cli_are_the_ones_of_the_backend():
    body = _capture(r"^ALLOWED_ARCHIVE_EXTENSIONS = \{([^}]*)\}", CLI)
    assert set(re.findall(r'"(\.[a-z.]+)"', body)) == _backend_extensions()


def test_the_archive_extensions_of_the_frontend_are_the_ones_of_the_backend():
    alternatives = _capture(r"^const ARCHIVE_OR_PAGE = /\\\.\(([^)]*)\)\$/i;", PACKING_TS)
    extensions = {"." + alternative.replace("\\.", ".") for alternative in alternatives.split("|")}
    assert extensions == _backend_extensions()
