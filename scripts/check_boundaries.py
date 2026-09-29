#!/usr/bin/env python3
"""模块依赖边界检查：应用不得导入另一个应用的内部实现。

重组之后最容易悄悄退回去的一件事，就是某个文件为了省事 `from synwevo_docs.services import …`。
这个检查让它变成一次失败而不是一次习惯。规则来自目录归属本身：

* `synwevo_docs`（业务应用）、`synwevo_portal`（门户）是**应用**，互不可见；
* `synwevo_identity`（账户服务）是**服务**，应用不得导入它的实现，只能经协议与它交互；
* `synwevo_core` / `synwevo_auth_client` 是**共享包**，谁都可以用，但它们不得反向依赖任何
  应用或服务——共享包一旦依赖应用，"独立运行"就只剩一句话；
* `synwevo_server` 是**部署组装**，只有它可以同时导入三者；
* `apps/docs/sdk`（若存在）是给外部集成方的客户端，**不得导入本仓库的任何包**：它要能单独装到
  一台没有这个仓库的机器上。

另外四件事也在这里检查，因为它们都能让"模块化"在本机看起来成立、换一台机器就不成立：

* `sys.path` 改写、机器绝对路径、符号链接；
* **声明的依赖**：每个 Python 包导入的第三方库必须写在它自己的 `pyproject.toml` 里，并且在它的
  requirements 文件链里有固定版本。只在"别的包顺带装了"时才能导入，就是单独安装时的 ImportError；
* **前端边界**：三个外壳之间不得互相导入，共享前端包不得导入外壳，相对路径不得跳出自己的目录。
"""
from __future__ import annotations

import ast
import os
import re
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# 目录 → 这个目录里的代码允许导入的本仓库顶层包
ALLOWED: dict[str, set[str]] = {
    "packages/core": {"synwevo_core"},
    # 依赖方适配需要共享层；它不认识任何应用。
    "packages/auth-client/python": {"synwevo_core", "synwevo_auth_client"},
    "services/identity": {"synwevo_core", "synwevo_auth_client", "synwevo_identity"},
    "apps/portal": {"synwevo_core", "synwevo_auth_client", "synwevo_portal"},
    "apps/docs": {"synwevo_core", "synwevo_auth_client", "synwevo_docs"},
    # 消息应用（Synwevo Message）：业务层在 synwevo_core.messaging，这里只有 HTTP 与外壳。
    "apps/messages": {"synwevo_core", "synwevo_auth_client", "synwevo_messages"},
    # 业务应用（Forms、Tables、DataPrep、Approvals、Extract、个人 Agent）：一个模块，挂在工作空间源站上。
    # 它们之间的边界在模块内部（每个应用一个子包，兄弟子包互不导入），见 check_suite_siblings。
    "apps/suite": {"synwevo_core", "synwevo_auth_client", "synwevo_suite"},
    # 外部客户端：一个本仓库的包都不许用（最长前缀优先，所以它不会继承 apps/docs 的许可）。
    "apps/docs/sdk": set(),
    # 部署组装：唯一可以看见全部的地方。
    "infra/server": {"synwevo_core", "synwevo_auth_client", "synwevo_identity",
                     "synwevo_portal", "synwevo_docs", "synwevo_messages", "synwevo_suite", "synwevo_server"},
    # 跨模块验证本来就要跨模块。
    "tests": {"synwevo_core", "synwevo_auth_client", "synwevo_identity",
              "synwevo_portal", "synwevo_docs", "synwevo_messages", "synwevo_suite", "synwevo_server"},
    # 根目录命令：只读配置（synwevo_core.env），其余都经子进程调用各模块自己的入口。
    "scripts": {"synwevo_core"},
}
OWN = {"synwevo_core", "synwevo_auth_client", "synwevo_identity", "synwevo_portal",
       "synwevo_docs", "synwevo_messages", "synwevo_suite", "synwevo_server"}
# Pruned during the walk, not filtered afterwards: data/ holds every user blob and .git every
# object, and statting them all made this check slower the more the instance was used.
#
# Two kinds, deliberately. Tool and build output is pruned by name wherever it appears — no source
# ever lives in a __pycache__ or a node_modules. The repository's own non-source directories are
# pruned only *at their place in the repository*: pruned by name, a future
# `synwevo_docs/data/loader.py` would have escaped every check in this file without a word.
SKIP_ANYWHERE = {"__pycache__", ".venv", "node_modules", "dist", ".pytest_cache", ".git"}
SKIP_PATHS = {"data", ".local", "testdata", ".claude", "docs/screenshots"}

# ------------------------------------------------------------------ declared dependencies
# Python package directory → the pyproject that must declare what it imports.
PACKAGES = {
    "packages/core/synwevo_core": "packages/core",
    "packages/auth-client/python/synwevo_auth_client": "packages/auth-client/python",
    "services/identity/synwevo_identity": "services/identity",
    "apps/portal/synwevo_portal": "apps/portal",
    "apps/docs/synwevo_docs": "apps/docs",
    "apps/messages/synwevo_messages": "apps/messages",
    "apps/suite/synwevo_suite": "apps/suite",
    "infra/server/synwevo_server": "infra/server",
}
# Import name → distribution name, where the two differ.
DIST_OF = {
    "PIL": "pillow", "docx": "python-docx", "pptx": "python-pptx", "fontTools": "fonttools",
    "argon2": "argon2-cffi", "yaml": "pyyaml", "multipart": "python-multipart",
    "fitz": "pymupdf", "charset_normalizer": "charset-normalizer",
    "synwevo_core": "synwevo-core", "synwevo_auth_client": "synwevo-auth-client",
    "synwevo_identity": "synwevo-identity", "synwevo_portal": "synwevo-portal",
    "synwevo_docs": "synwevo-docs", "synwevo_messages": "synwevo-messages", "synwevo_suite": "synwevo-suite",
    "synwevo_server": "synwevo-server",
}
STDLIB = set(sys.stdlib_module_names)


def _norm(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _req_name(spec: str) -> str:
    return _norm(re.split(r"[\s<>=!~;\[(]", spec.strip(), maxsplit=1)[0])


def declared(module_dir: str) -> tuple[set[str], set[str]]:
    """(required, optional) distribution names from a module's pyproject.toml."""
    data = tomllib.loads((ROOT / module_dir / "pyproject.toml").read_text(encoding="utf-8"))
    project = data.get("project", {})
    required = {_req_name(d) for d in project.get("dependencies", [])}
    optional = {_req_name(d) for deps in project.get("optional-dependencies", {}).values()
                for d in deps}
    return required, optional


def pinned(req_file: Path, seen: set[Path] | None = None) -> set[str]:
    """Distribution names pinned with == in a requirements file and everything it -r includes."""
    seen = seen or set()
    if req_file in seen or not req_file.is_file():
        return set()
    seen.add(req_file)
    out: set[str] = set()
    for line in req_file.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        if line.startswith("-r "):
            out |= pinned((req_file.parent / line[3:].strip()).resolve(), seen)
        elif "==" in line:
            out.add(_req_name(line))
    return out


def check_dependencies(imports: dict[str, set[str]]) -> list[str]:
    problems = []
    for pkg_dir, module_dir in PACKAGES.items():
        if not (ROOT / module_dir / "pyproject.toml").is_file():
            continue
        required, optional = declared(module_dir)
        own_dist = _norm(DIST_OF.get(Path(pkg_dir).name, Path(pkg_dir).name))
        pins = pinned(ROOT / module_dir / "requirements.txt")
        for name in sorted(imports.get(pkg_dir, set())):
            if name in STDLIB:
                continue
            dist = _norm(DIST_OF.get(name, name))
            if dist == own_dist:
                continue
            if dist not in required and dist not in optional:
                problems.append(f"{module_dir}: 导入了 `{name}`，但 pyproject.toml 没有声明 `{dist}`"
                                f" —— 单独安装这个包时它会 ImportError")
        for dist in sorted(required):
            if dist.startswith("synwevo-"):
                continue
            if dist not in pins:
                problems.append(f"{module_dir}: 声明了 `{dist}`，但 {module_dir}/requirements.txt "
                                f"（含 -r 引入的文件）里没有固定版本")
    # SDK distributions can live below sdk/ (currently sdk/python/). Checking only
    # sdk/pyproject.toml silently missed declared runtime dependencies in the real package.
    sdk_projects = [p for p in walk() if p.name == "pyproject.toml"
                    and p.is_relative_to(ROOT / "apps/docs/sdk") and not p.is_symlink()]
    for sdk in sdk_projects:
        module_dir = sdk.parent.relative_to(ROOT).as_posix()
        required, optional = declared(module_dir)
        for dist in sorted((required | optional)):
            if dist.startswith("synwevo-") and dist != _norm(tomllib.loads(sdk.read_text("utf-8"))
                                                            .get("project", {}).get("name", "")):
                problems.append(f"{module_dir}: 依赖了本仓库的包 `{dist}` —— 外部客户端必须能单独安装")
    return problems


# ------------------------------------------------------------------ front-end boundaries
WEB: dict[str, set[str]] = {
    "apps/docs/web": {"@synwevo/ui", "@synwevo/auth-client", "@synwevo/docs-web"},
    "apps/portal/web": {"@synwevo/ui", "@synwevo/auth-client", "@synwevo/workspace-web"},
    "apps/messages/web": {"@synwevo/ui", "@synwevo/auth-client", "@synwevo/messages-web"},
    "apps/suite/web": {"@synwevo/ui", "@synwevo/auth-client", "@synwevo/suite-web"},
    "services/identity/web": {"@synwevo/ui", "@synwevo/auth-client", "@synwevo/account-web"},
    "packages/auth-client/web": {"@synwevo/ui", "@synwevo/auth-client"},
    "packages/ui": {"@synwevo/ui"},
}
TS_SUFFIXES = {".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"}
TS_IMPORT = re.compile(r"""(?:\bfrom\s*|\bimport\s*\(\s*|\bimport\s+|\brequire\s*\(\s*)["']([^"']+)["']""")


def check_web(path: Path, web_root: str, src: str) -> list[str]:
    problems = []
    rel = path.relative_to(ROOT)
    for spec in TS_IMPORT.findall(src):
        if spec.startswith("."):
            target = (path.parent / spec).resolve()
            if not target.is_relative_to((ROOT / web_root).resolve()):
                problems.append(f"{rel}: 相对导入 `{spec}` 跳出了 {web_root} —— 共用的代码放进共享前端包")
        elif spec.startswith("@synwevo/"):
            pkg = "/".join(spec.split("/")[:2])
            if pkg not in WEB[web_root]:
                problems.append(f"{rel}: 不允许导入 `{pkg}`（{web_root} 只能用 "
                                f"{', '.join(sorted(WEB[web_root]))}）")
    return problems


# ------------------------------------------------------------------ business-application siblings
#: The applications inside `synwevo_suite` (and their page folders in `apps/suite/web/src/apps/`).
#: One module for deployment, six applications for everything else: an application reaches another
#: only through the capability registry in synwevo_core, never by importing its internals.
SUITE_PKG = "apps/suite/synwevo_suite"
SUITE_WEB = "apps/suite/web/src/apps"
SUITE_APPS = {"forms", "tables", "dataprep", "approvals", "extract", "agent"}


def _suite_app_of(rel: str, base: str) -> str | None:
    if not rel.startswith(base + "/"):
        return None
    head = rel[len(base) + 1:].split("/", 1)[0]
    return head if head in SUITE_APPS else None


def check_suite_python(rel: str, tree: ast.AST) -> list[str]:
    mine = _suite_app_of(rel, SUITE_PKG)
    problems = []
    depth_to_pkg = rel[len(SUITE_PKG) + 1:].count("/") if rel.startswith(SUITE_PKG + "/") else 0
    for node in ast.walk(tree):
        targets: list[str] = []
        if isinstance(node, ast.Import):
            targets = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0 and node.module:
                targets = [node.module] + [f"{node.module}.{a.name}" for a in node.names]
            elif node.level > 0 and rel.startswith(SUITE_PKG + "/"):
                # Resolve a relative import to synwevo_suite.<…> from this file's position.
                up = node.level - 1
                parts = rel[len(SUITE_PKG) + 1:].split("/")[:-1]
                if up <= len(parts):
                    base = ["synwevo_suite", *parts[:len(parts) - up]]
                    mod = ".".join(base + ([node.module] if node.module else []))
                    targets = [mod] + [f"{mod}.{a.name}" for a in node.names]
        for t in targets:
            bits = t.split(".")
            if len(bits) >= 2 and bits[0] == "synwevo_suite" and bits[1] in SUITE_APPS and bits[1] != mine:
                if mine is None and depth_to_pkg == 0 and bits[1] in SUITE_APPS:
                    continue            # the module's own app.py installs each application
                problems.append(f"{rel}: 导入了兄弟应用 `{'.'.join(bits[:2])}` —— 应用之间只能经能力注册表调用")
                break
    return problems


def check_suite_web(path: Path, rel: str, src: str) -> list[str]:
    mine = _suite_app_of(rel, SUITE_WEB)
    if mine is None:
        return []
    problems = []
    for spec in TS_IMPORT.findall(src):
        if spec.startswith("."):
            target = (path.parent / spec).resolve().relative_to(ROOT.resolve()).as_posix()
            other = _suite_app_of(target, SUITE_WEB)
            if other and other != mine:
                problems.append(f"{rel}: 导入了兄弟应用的页面代码 `{spec}` —— 共用的放进 src/shared")
    return problems


# ------------------------------------------------------------------ walk
def _longest(rel: str, keys) -> str | None:
    best = None
    for key in keys:
        if (rel == key or rel.startswith(key + "/")) and (best is None or len(key) > len(best)):
            best = key
    return best


def owner(path: Path) -> str | None:
    # 最长前缀优先：apps/docs/sdk 要在 apps/docs 之前匹配到。
    return _longest(path.relative_to(ROOT).as_posix(), ALLOWED)


def imported_roots(tree: ast.AST) -> set[str]:
    out: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            out.add(node.module.split(".")[0])
    return out


def _skipped(dirpath: str, name: str) -> bool:
    if name in SKIP_ANYWHERE or name.endswith(".egg-info"):
        return True
    return (Path(dirpath) / name).relative_to(ROOT).as_posix() in SKIP_PATHS


def walk():
    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = sorted(d for d in dirnames if not _skipped(dirpath, d))
        for d in dirnames:
            if (Path(dirpath) / d).is_symlink():
                yield Path(dirpath) / d
        for f in sorted(filenames):
            yield Path(dirpath) / f


def main() -> int:
    problems: list[str] = []
    checked = 0
    web_checked = 0
    imports: dict[str, set[str]] = {}
    for p in walk():
        rel = p.relative_to(ROOT).as_posix()
        # 指向旧目录的符号链接会让一切在这台机器上"能跑"，换一台就不能。
        if p.is_symlink():
            problems.append(f"{rel}: 符号链接 → {p.readlink()}；模块化不能靠链接回旧路径")
            continue
        web_root = _longest(rel, WEB)
        if web_root and p.suffix in TS_SUFFIXES:
            web_checked += 1
            src = p.read_text(encoding="utf-8", errors="replace")
            problems += check_web(p, web_root, src)
            problems += check_suite_web(p, rel, src)
            continue
        if p.suffix != ".py":
            continue
        key = owner(p)
        if key is None:
            continue
        checked += 1
        src = p.read_text(encoding="utf-8", errors="replace")
        try:
            tree = ast.parse(src)
        except SyntaxError as e:
            problems.append(f"{rel}: 语法错误 {e}")
            continue
        roots = imported_roots(tree)
        problems += check_suite_python(rel, tree)
        pkg = _longest(rel, PACKAGES)
        if pkg:
            imports.setdefault(pkg, set()).update(roots)
        for root in roots & OWN:
            if root not in ALLOWED[key]:
                allowed = ", ".join(sorted(ALLOWED[key])) or "（本仓库的任何包都不行）"
                problems.append(f"{rel}: 不允许导入 `{root}`（{key} 只能用 {allowed}）")
        if re.search(r"^\s*sys\.path\.(insert|append)", src, re.M):
            problems.append(f"{rel}: 改写 sys.path —— 包边界要靠安装，不是靠运行时拼路径")
        for m in re.finditer(r"[\"'](/Users/|/home/|C:\\\\)[^\"']*[\"']", src):
            problems.append(f"{rel}: 写死了本机绝对路径 {m.group(0)[:60]}")

    problems += check_dependencies(imports)

    print(f"检查了 {checked} 个 Python 文件、{web_checked} 个前端文件，覆盖 {len(ALLOWED)} 个模块边界、"
          f"{len(PACKAGES)} 个包的依赖声明。")
    if problems:
        print(f"\n发现 {len(problems)} 处越界：", file=sys.stderr)
        for line in problems:
            print("  ✗ " + line, file=sys.stderr)
        return 1
    print("没有跨应用导入、没有 sys.path 改写、没有机器绝对路径、没有指向旧目录的链接；"
          "每个包导入的第三方库都已声明并固定版本；前端外壳互不导入。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
