"""`python -m toon_cli`, the same entry point as the `toon` command."""

from __future__ import annotations

import sys

from toon_cli import main

if __name__ == "__main__":  # pragma: no cover - entry point guard, exercised by running `python -m toon_cli`
    sys.exit(main())
