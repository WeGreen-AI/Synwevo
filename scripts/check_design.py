#!/usr/bin/env python3
"""设计系统守卫：三个外壳只能有一套视觉来源。

`packages/ui/src/styles.css` 是唯一定义颜色、字号、圆角、阴影与动效令牌的地方。这个脚本检查其余
前端源码有没有绕开它——各应用再写一套色板、深色主题回流、用 `!important` 盖住共享规则——并把每
一处例外归到有名字的理由里（文档内容的颜色不是界面主题，标记色板是"统一规则"的一部分）。

和 `check_brand.py` 一样，它分类而不是数次数：每一处字面颜色要么属于一条写清理由的例外，要么是
漏改，退出码非零。要新增一条例外，在 `ALLOWED` 里加一条并写清为什么它不是界面外壳的颜色。

用法：`python scripts/check_design.py`
"""
from __future__ import annotations

import re
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

#: 唯一的令牌来源。这里允许字面颜色；其他地方不允许。
TOKEN_SOURCE = "packages/ui/src/styles.css"

#: 前端源码目录（相对仓库根）。
FRONTEND_ROOTS = (
    "packages/ui/src", "packages/auth-client/web/src",
    "apps/docs/web/src", "apps/portal/web/src", "services/identity/web/src",
)
SOURCE_SUFFIXES = (".css", ".tsx", ".ts", ".html")

#: 字面颜色：十六进制、rgb()/rgba()、hsl()。命名颜色太容易误报（"red" 是错误码），不检查。
COLOR = re.compile(r"(?<![\w-])#(?:[0-9a-fA-F]{3,4}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})\b|\b(?:rgba?|hsla?)\(")

#: (文件或目录前缀, 行内匹配, 保留理由)。先匹配到的先归类。
ALLOWED: list[tuple[str, str, str]] = [
    # 文档内容：用户文件里的颜色照原样渲染，这不是界面主题。
    ("apps/docs/web/src/lib/css.ts", r"yellow|green|cyan|magenta|blue|red|dark|light|black|white",
     "OOXML 命名颜色表：把用户文档里的颜色名转成可渲染的值，是文档内容不是界面主题"),
    ("apps/docs/web/src/lib/css.ts", r"MARK_GREY|FIND_MARK|MARK_HEX|#rgb|#rrggbb",
     "标记色板：未映射字段的中性灰与查找高亮，与服务端分配的映射颜色属同一套统一规则"),
    ("apps/docs/web/src/views/GridView.tsx", r"borderLeft|borderRight|borderTop|borderBottom",
     "表格单元格边框：按来源工作簿的边框样式绘制，是文档内容"),
    ("apps/docs/web/src/pages/SheetEditor.tsx", r"COLORS = \[|border: s\.bd",
     "内置表格编辑器：字段占位的类别色板（编号+名称同时显示）与用户设定的单元格边框"),
    ("apps/docs/web/src/pages/SheetEditor.tsx", r'type="color"',
     "颜色选择器的当前值：编辯文档单元格的文字/背景颜色，是文档内容"),
    ("apps/docs/web/src/components/SidePanel.tsx", r'#000000',
     "字段样式颜色选择器的默认值：写进生成文档的文字颜色，是文档内容"),
    ("apps/docs/web/src/views/DocView.tsx", r"markColor\(",
     "映射标记的兜底色：服务端未分配颜色时的中性/区块色，与映射色板同属统一规则"),
    ("apps/docs/web/src/views/PdfView.tsx", r"rgba\(255,255,255,\.85\)",
     "PDF 页面上的半透明白色角标底：叠在文档图像上，必须不透明度可调"),
    # 界面里少数需要半透明叠加的地方：CSS 变量不能直接带 alpha，这里的基色与令牌一致（品牌蓝 / 错误红 / 区块紫）。
    (TOKEN_SOURCE, r".*", "令牌来源文件"),
    ("apps/portal/web/src/portal.css", r"rgba\(37, 99, 235",
     "卡片悬停阴影：品牌蓝的半透明叠加，基色与 --accent 一致"),
    ("apps/docs/web/src/views/GridView.tsx", r"return `rgba\(",
     "hexA()：把色板颜色转成带透明度的叠加色，供文档标记使用"),
    # 浏览器外壳元数据
    ("apps/docs/web/index.html", r"theme-color|fill=", "favicon 与浏览器标签色：品牌蓝 / 白"),
    ("apps/portal/web/index.html", r"theme-color|fill=", "favicon 与浏览器标签色：品牌蓝 / 白"),
    ("services/identity/web/index.html", r"theme-color|fill=", "favicon 与浏览器标签色：品牌蓝 / 白"),
]

#: 任何应用源码里都不该再出现的东西。
FORBIDDEN: list[tuple[str, str]] = [
    (r'data-theme="dark"|data-theme=\'dark\'|\[data-theme=dark\]', "深色主题已随统一白蓝体系退役"),
    (r"prefers-color-scheme:\s*dark", "系统深色模式不再切换界面主题（color-scheme 固定为 light）"),
    (r"--pt-(green|ink|muted|line)\b", "门户私有色板已并入共享令牌"),
    (r"font-family:\s*Georgia", "各应用不得自选字体族；字体在 --font / --mono 令牌里"),
]


def inventory() -> list[str]:
    try:
        out = subprocess.run(["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard", *FRONTEND_ROOTS],
                             cwd=ROOT, capture_output=True, text=True, check=True)
    except (OSError, subprocess.CalledProcessError) as e:
        print(f"无法枚举前端源码：{e}", file=sys.stderr)
        raise SystemExit(2)
    files = sorted(set(out.stdout.split("\0")) - {""})
    return [f for f in files if f.endswith(SOURCE_SUFFIXES)
            and not any(part in {"node_modules", "dist", "tests"} for part in Path(f).parts)]


def classify(path: str, line: str) -> str | None:
    for prefix, pattern, reason in ALLOWED:
        if path.startswith(prefix) and re.search(pattern, line):
            return reason
    return None


def important_outside_media(text: str) -> list[int]:
    """`!important` 行号，只算 @media 之外的（响应式布局覆盖是合理用法，全局盖住共享规则不是）。"""
    hits, depth, media_open_at = [], 0, None
    for i, line in enumerate(text.splitlines(), 1):
        if "@media" in line and media_open_at is None:
            media_open_at = depth
        if "!important" in line and media_open_at is None:
            hits.append(i)
        depth += line.count("{") - line.count("}")
        if media_open_at is not None and depth <= media_open_at:
            media_open_at = None
    return hits


def main() -> int:
    files = inventory()
    buckets: dict[str, list[str]] = defaultdict(list)
    problems: list[str] = []
    shells = {"apps/docs/web/src/main.tsx", "apps/portal/web/src/main.tsx", "services/identity/web/src/main.tsx"}
    for f in files:
        text = (ROOT / f).read_text(encoding="utf-8")
        if f in shells and '@synwevo/ui/styles.css' not in text:
            problems.append(f"{f}: 外壳入口没有引入共享样式表 @synwevo/ui/styles.css")
        for i, line in enumerate(text.splitlines(), 1):
            for pattern, why in FORBIDDEN:
                if f != TOKEN_SOURCE and re.search(pattern, line):
                    problems.append(f"{f}:{i}: {why} —— {line.strip()[:100]}")
            if not COLOR.search(line) or line.lstrip().startswith(("//", "*", "/*")):
                continue
            reason = classify(f, line)
            if reason is None:
                problems.append(f"{f}:{i}: 字面颜色不在共享令牌里 —— {line.strip()[:100]}")
            else:
                buckets[reason].append(f"{f}:{i}")
        if f.endswith(".css") and f != TOKEN_SOURCE:
            if re.search(r"^\s*:root\s*\{", text, re.M):
                problems.append(f"{f}: 应用样式表不得定义自己的 :root 令牌")
            for n in important_outside_media(text):
                problems.append(f"{f}:{n}: @media 之外的 !important 会盖住共享规则")
    tokens = (ROOT / TOKEN_SOURCE).read_text(encoding="utf-8")
    for must in ("--accent: #2563eb", "--accent-strong: #1d4ed8", "--accent-pressed: #1e40af",
                 "--bg: #f8fafc", "--panel: #ffffff", "--text: #0f172a", "--text-2: #475569",
                 "--line: #e2e8f0", "--accent-weak: #eff6ff", "color-scheme: light"):
        if must not in tokens:
            problems.append(f"{TOKEN_SOURCE}: 缺少基线令牌 {must}")
    if re.search(r':root\[data-theme="dark"\]\s*\{[^}]*--bg', tokens):
        problems.append(f"{TOKEN_SOURCE}: 深色令牌块仍然存在")

    classified = sum(len(v) for v in buckets.values())
    print(f"前端源码 {len(files)} 个文件；共享令牌之外的字面颜色已归类 {classified} 处。\n")
    for reason in sorted(buckets):
        hits = buckets[reason]
        where = sorted({h.rsplit(':', 1)[0] for h in hits})
        print(f"  [{len(hits):>3}] {reason}")
        print(f"        {', '.join(where[:3])}{' …' if len(where) > 3 else ''}")
    if problems:
        print(f"\n✗ {len(problems)} 处需要处理：", file=sys.stderr)
        for p in problems:
            print("    " + p, file=sys.stderr)
        print("\n要么改用共享令牌 / 共享类，要么在 ALLOWED 里加一条并写清为什么它不是界面外壳的颜色。", file=sys.stderr)
        return 1
    print("\n✓ 设计系统检查通过：没有绕开共享令牌的颜色、私有色板或深色主题残留。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
