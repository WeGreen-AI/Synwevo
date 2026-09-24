#!/usr/bin/env python3
"""模块依赖边界检查：应用不得导入另一个应用的内部实现。

重组之后最容易悄悄退回去的一件事，就是某个文件为了省事 `from synwevo_docs.services import …`。
这个检查让它变成一次失败而不是一次习惯。规则来自目录归属本身：

* `docweave`（业务应用）、`synwevo_portal`（门户）是**应用**，互不可见；
* `synwevo_identity`（账户服务）是**服务**，应用不得导入它的实现，只能经协议与它交互；
* `synwevo_core` / `synwevo_auth_client` 是**共享包**，谁都可以用，但它们不得反向依赖任何
  应用或服务——共享包一旦依赖应用，"独立运行"就只剩一句话；
* `synwevo_server` 是**部署组装**，只有它可以同时导入三者。

同时检查三件在重组里特别容易留下的痕迹：`sys.path` 改写、机器绝对路径、指向旧目录的符号链接。
它们都能让"模块化"在本机看起来成立，而换一台机器就不成立。
"""
from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# 目录 → 这个目录里的代码允许导入的顶层包
ALLOWED: dict[str, set[str]] = {
    "packages/core": {"synwevo_core"},
    # 依赖方适配需要共享层；它不认识任何应用。
    "packages/auth-client/python": {"synwevo_core", "synwevo_auth_client"},
    "services/identity": {"synwevo_core", "synwevo_auth_client", "synwevo_identity"},
    "apps/portal": {"synwevo_core", "synwevo_auth_client", "synwevo_portal"},
    "apps/docs": {"synwevo_core", "synwevo_auth_client", "synwevo_docs"},
    # 部署组装：唯一可以看见全部的地方。
    "infra/server": {"synwevo_core", "synwevo_auth_client", "synwevo_identity",
                     "synwevo_portal", "synwevo_docs", "synwevo_server"},
    # 跨模块验证本来就要跨模块。
    "tests": {"synwevo_core", "synwevo_auth_client", "synwevo_identity",
              "synwevo_portal", "synwevo_docs", "synwevo_server"},
}
OWN = {"synwevo_core", "synwevo_auth_client", "synwevo_identity", "synwevo_portal",
       "synwevo_docs", "synwevo_server"}
SKIP_PARTS = {"__pycache__", ".venv", "node_modules", "dist", ".pytest_cache"}


def owner(path: Path) -> str | None:
    rel = path.relative_to(ROOT).as_posix()
    # 最长前缀优先：apps/docs 要在 apps 之前匹配到。
    best = None
    for key in ALLOWED:
        if rel.startswith(key + "/") and (best is None or len(key) > len(best)):
            best = key
    return best


def imported_roots(tree: ast.AST) -> set[str]:
    out: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            out.add(node.module.split(".")[0])
    return out


def main() -> int:
    problems: list[str] = []
    checked = 0
    for p in sorted(ROOT.rglob("*.py")):
        if set(p.relative_to(ROOT).parts) & SKIP_PARTS or ".egg-info" in str(p):
            continue
        key = owner(p)
        if key is None:
            continue
        checked += 1
        src = p.read_text(encoding="utf-8", errors="replace")
        try:
            tree = ast.parse(src)
        except SyntaxError as e:
            problems.append(f"{p.relative_to(ROOT)}: 语法错误 {e}")
            continue
        for root in imported_roots(tree) & OWN:
            if root not in ALLOWED[key]:
                problems.append(f"{p.relative_to(ROOT)}: 不允许导入 `{root}`（{key} 只能用 "
                                f"{', '.join(sorted(ALLOWED[key]))}）")
        if re.search(r"^\s*sys\.path\.(insert|append)", src, re.M):
            problems.append(f"{p.relative_to(ROOT)}: 改写 sys.path —— 包边界要靠安装，"
                            f"不是靠运行时拼路径")
        for m in re.finditer(r"[\"'](/Users/|/home/|C:\\\\)[^\"']*[\"']", src):
            problems.append(f"{p.relative_to(ROOT)}: 写死了本机绝对路径 {m.group(0)[:60]}")

    # 指向旧目录的符号链接会让一切在这台机器上"能跑"，换一台就不能。
    for p in ROOT.rglob("*"):
        if set(p.relative_to(ROOT).parts) & SKIP_PARTS:
            continue
        if p.is_symlink():
            problems.append(f"{p.relative_to(ROOT)}: 符号链接 → {p.readlink()}；"
                            f"模块化不能靠链接回旧路径")

    print(f"检查了 {checked} 个文件，覆盖 {len(ALLOWED)} 个模块边界。")
    if problems:
        print(f"\n发现 {len(problems)} 处越界：", file=sys.stderr)
        for line in problems:
            print("  ✗ " + line, file=sys.stderr)
        return 1
    print("没有跨应用导入、没有 sys.path 改写、没有机器绝对路径、没有指向旧目录的链接。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
