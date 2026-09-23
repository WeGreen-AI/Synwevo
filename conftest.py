"""Repository-wide pytest configuration.

`testpaths` in pyproject.toml lists three suites that belong to three modules, and this file is
what makes running them together mean the same thing as running each on its own: the packages
are installed (editable) rather than reached by inserting directories into `sys.path`, so a test
that accidentally imports across a module boundary fails here exactly as it would in production.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from platform_core.http import repo_root

ROOT = repo_root() or Path(__file__).resolve().parent


@pytest.fixture(scope="session")
def platform_root() -> Path:
    return ROOT
