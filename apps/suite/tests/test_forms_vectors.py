"""The server side of the shared form-rule vectors (the browser runs the same file)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from synwevo_suite.forms.definition import Definition, check_answers

VECTORS = json.loads((Path(__file__).resolve().parents[1] / "web" / "tests" / "forms-vectors.json").read_text("utf-8"))


@pytest.mark.parametrize("case", VECTORS["cases"], ids=[c["name"] for c in VECTORS["cases"]])
def test_vector(case):
    _, errors = check_answers(Definition.model_validate(case["definition"]), case["answers"], files={}, final=True)
    assert sorted((e["field"], e["code"]) for e in errors) == sorted(tuple(x) for x in case["errors"])
