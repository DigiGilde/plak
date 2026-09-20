"""`python -m plak_cli`, the same entry point as the `plak` command."""

from __future__ import annotations

import sys

from plak_cli import main

if __name__ == "__main__":  # pragma: no cover - entry point guard, exercised by running `python -m plak_cli`
    sys.exit(main())
