"""Detect untranslated API-console prose against the real, current Docs contract."""
import json
import re
from pathlib import Path

from tests.integration.test_api_platform import plane


def test_every_visible_contract_summary_and_description_has_three_languages(plane):
    source = (Path(__file__).resolve().parents[2] / "services/identity/web/src/pages/apiplatform/api-platform-contract.messages.ts").read_text()
    entries, _ = json.JSONDecoder().raw_decode(source.split("export const contractEntries: [string, string, string][] = ", 1)[1])
    by_source = {row[0]: row for row in entries}
    by_english = {row[1]: row for row in entries}
    assert len(by_source) == len(entries)
    assert all(len(row) == 3 and all(part.strip() for part in row) for row in entries)
    assert all(not re.search(r"[\u4e00-\u9fff]", row[1]) for row in entries)
    missing = set()

    def walk(value):
        if isinstance(value, list):
            for item in value:
                walk(item)
        if isinstance(value, dict):
            for key, item in value.items():
                if key in ("description", "summary") and isinstance(item, str):
                    for part in item.split("\n\n"):
                        # Status enum strings are wire values, not translatable prose.
                        if not part or part == "pending、running、success、failed、skipped、cancelled":
                            continue
                        if part not in by_source and part not in by_english:
                            missing.add(part)
                else:
                    walk(item)

    response = plane["admin"].get("/api/admin/api-platform/openapi")
    assert response.status_code == 200, response.text
    walk(response.json()["paths"])
    assert not missing, "Add module-local translations for current contract prose: " + repr(sorted(missing))
