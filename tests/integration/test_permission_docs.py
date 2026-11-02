"""The two permission tables in the documents, against the live catalogue (authz-6 review).

`docs/security/permissions.md` lists the permission ids and `docs/platform/admin-coverage.md` says
which built-in role holds which. Both are read by people deciding who may do what, and both had
fallen behind: a reader of either missed the super administrator's own second-factor reset, and the
application catalogue's switches. `synwevo_core.platform_access` is the catalogue; these hold the
documents to it, so a permission added there cannot be missing here.
"""
from __future__ import annotations

import re
from pathlib import Path

from synwevo_core.platform_access import PERMISSIONS, ROLE_ORDER, ROLES

ROOT = Path(__file__).resolve().parents[2]
COLUMNS = {"最高": "super_admin", "账户与访问": "access_admin", "支持": "support", "审计查看": "auditor",
           "API 平台管理": "api_platform_admin", "财务": "finance"}


def _between(text: str, start: str, end: str) -> str:
    begin = text.index(start)
    return text[begin:text.index(end, begin)]


def test_the_permission_id_list_names_the_whole_catalogue():
    text = (ROOT / "docs/security/permissions.md").read_text(encoding="utf-8")
    block = _between(text, "权限 ID：", "**模型治理与平台配置分权")
    listed = set()
    for token in re.findall(r"`((?:platform\.|api_platform\.)[a-z_.|]+)`", block):
        head, *others = token.split("|")
        listed.add(head)
        listed.update(f"{head.rsplit('.', 1)[0]}.{other}" for other in others)
    assert listed == set(PERMISSIONS)


def _ids(cell: str) -> list[str]:
    """`platform.workspaces.view` / `.manage` is two permissions of one family, in that order."""
    first, *others = re.findall(r"`([a-z_.]+)`", cell.split("（")[0])
    return [first, *(first.rsplit(".", 1)[0] + other for other in others)]


def test_the_role_table_says_who_holds_each_permission():
    text = (ROOT / "docs/platform/admin-coverage.md").read_text(encoding="utf-8")
    table = _between(text, "| 权限 | 最高 | 账户与访问 |", "\n\n").splitlines()
    header = [cell.strip() for cell in table[0].strip("|").split("|")][1:]
    assert sorted(COLUMNS[name] for name in header) == sorted(ROLE_ORDER)
    seen = set()
    for row in table[2:]:
        cells = [cell.strip() for cell in row.strip("|").split("|")]
        ids = _ids(cells[0])
        seen.update(ids)
        for name, cell in zip(header, cells[1:]):
            marks = cell.split("（")[0].split("/")
            for index, permission in enumerate(ids):
                mark = marks[index] if len(marks) > 1 else marks[0]
                assert (mark.strip() == "✓") == (permission in ROLES[COLUMNS[name]]), (permission, name, cell)
    assert seen == set(PERMISSIONS), {"missing": sorted(set(PERMISSIONS) - seen), "stale": sorted(seen - set(PERMISSIONS))}
