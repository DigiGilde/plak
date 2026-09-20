"""Pytest config for the Python-based e2e tests in this directory
(`test_header_parity.py`). The Playwright suite is Node/TS-based and does not
read this file.
"""

from __future__ import annotations

import pytest


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers", "compose: compares real HTTP headers against a running compose stack (task 6.2)."
    )
