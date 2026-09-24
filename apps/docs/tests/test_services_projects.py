"""The project list, paged (contract 6), and project settings that used to be accepted and dropped.

GET /api/projects without parameters keeps the unpaged list the workbench has always used; any
of q / sort / order / offset / limit / archived turns it into one page searched and sorted in SQL.
"""
from __future__ import annotations

import pytest


def _names(page):
    return [p["name"] for p in page["items"]]


def test_unparameterised_list_is_unchanged(api):
    a = api.project("甲")
    rows = api.ok(api.c.get("/api/projects"))
    assert isinstance(rows, list) and any(p["id"] == a for p in rows)


def test_paging_search_and_sort(api):
    for n in ("报价单 A", "报价单 B", "合同 C", "报价_单 D", "报价%单 E"):
        api.project(n)
    page = api.ok(api.c.get("/api/projects", params={"sort": "name", "order": "asc", "limit": 2}))
    assert page["total"] == 5 and page["limit"] == 2 and page["offset"] == 0 and page["next_offset"] == 2
    assert len(page["items"]) == 2
    rest = api.ok(api.c.get("/api/projects", params={"sort": "name", "order": "asc", "offset": 4, "limit": 2}))
    assert len(rest["items"]) == 1 and rest["next_offset"] is None
    everything = api.ok(api.c.get("/api/projects", params={"sort": "name", "order": "asc", "limit": 200}))
    assert _names(everything) == sorted(_names(everything))
    desc = api.ok(api.c.get("/api/projects", params={"sort": "name", "order": "desc", "limit": 200}))
    assert _names(desc) == list(reversed(_names(everything)))
    # search matches the name; LIKE wildcards typed by the user are literal characters
    assert sorted(_names(api.ok(api.c.get("/api/projects", params={"q": "报价单"})))) == ["报价单 A", "报价单 B"]
    assert _names(api.ok(api.c.get("/api/projects", params={"q": "报价_单"}))) == ["报价_单 D"]
    assert _names(api.ok(api.c.get("/api/projects", params={"q": "%"}))) == ["报价%单 E"]
    # each page row carries the details the dense list renders
    row = everything["items"][0]
    assert {"sources", "last_job", "template_name", "archived_at", "origin"} <= set(row)


@pytest.mark.parametrize("params,field", [({"sort": "size"}, "sort"), ({"order": "sideways"}, "order"),
                                          ({"limit": 0}, "limit"), ({"limit": 201}, "limit"), ({"offset": -1}, "offset")])
def test_bad_list_parameters_are_refused(api, params, field):
    r = api.c.get("/api/projects", params=params)
    assert r.status_code == 400 and r.json()["detail"]["field"] == field, r.text


def test_archived_projects_only_with_archived_1(api):
    keep, gone = api.project("留着"), api.project("归档")
    api.ok(api.c.patch(f"/api/projects/{gone}", json={"archived": True}))
    ids = {p["id"] for p in api.ok(api.c.get("/api/projects", params={"limit": 50}))["items"]}
    assert keep in ids and gone not in ids
    ids = {p["id"] for p in api.ok(api.c.get("/api/projects", params={"archived": 1}))["items"]}
    assert {keep, gone} <= ids


def test_invalid_output_defaults_are_refused_when_saved(api):
    """F142/F123: a stored layout 'fitt' used to be accepted and then silently become 'off'
    when a job ran. It is now refused when the setting is saved."""
    pid = api.project("输出设置")
    for bad, field in (({"layout": "fitt"}, "output.layout"), ({"error_policy": "maybe"}, "output.error_policy"),
                       ({"naming": 5}, "output.naming"), ({"formats": "pdf"}, "output.formats")):
        r = api.c.patch(f"/api/projects/{pid}", json={"output": {"naming": "{序号}", **bad}})
        assert r.status_code == 400 and r.json()["detail"]["field"] == field, (bad, r.text)
    ok = api.ok(api.c.patch(f"/api/projects/{pid}", json={"output": {"naming": "{序号}", "layout": "warn", "keep_fields": True}}))
    assert ok["output"]["layout"] == "warn"
