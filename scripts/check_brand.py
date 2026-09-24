#!/usr/bin/env python3
"""旧品牌残留审计：把每一处 `docweave` 归到一类，剩下的就是漏改。

改品牌最容易犯的错误是把"搜不到旧名字"当成目标。有些字符串看着像旧品牌，实际上是别人系统里的
主键：应用 ID 写在已经签发的令牌里，客户端 ID 绑在登记过的回调上，Cookie 名字在每一个还开着的
标签页里。把它们一起改掉，换来的是干净的 grep，代价是别人的账号和定时任务。

所以这个脚本不数次数，它**分类**：每一处要么属于一个有名字的保留理由，要么就是漏改，退出码非零。
新增一处确实该保留的旧标识时，在 `STABLE` 里加一条并写清理由——理由本身就是这个文件的产出。
"""
from __future__ import annotations

import re
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

#: 历史记录：当时怎么写的就怎么留着。改写它们会把证据变成主张。
HISTORICAL_FILES = {
    "docs/migrations/platform-restructure.md",
    "docs/migrations/synwevo-rebrand.md",
    "docs/quality/issues.md",
    "docs/quality/progress.md",
    "docs/quality/verification.md",
    "scripts/check_brand.py",
}

#: (匹配, 保留理由)。顺序有意义：先匹配到的先归类。
STABLE: list[tuple[str, str]] = [
    (r"docweave-web",                    "OIDC client_id：登记在 oauth_clients，与回调白名单绑定"),
    (r"docweave-api",                    "API audience：写在已签发的访问令牌的 aud 里"),
    (r"docweave\.(read|write|\*)",       "API scope：写在已签发的访问令牌的 scope 里"),
    (r"docweave\.localhost",             "本机主机名：注册过的 OIDC 回调、Passkey 可信 origin、Cookie 作用域"),
    (r"docweave\.sqlite3",               "数据库文件名：改名是一次数据迁移，收益只有看起来一致"),
    (r"[xX]-[dD]oc[wW]eave-[a-zA-Z-]+",  "HTTP 头：Webhook 接收方与已打开的页面按名字解析它"),
    (r"urn:docweave",                    "协议里的稳定 URN"),
    (r"docweave\.template-fields",       "字段定义 schema 标识：版本化契约"),
    (r"DOCWEAVE_[A-Z0-9_]+",             "旧环境变量名：兼容读取（见 synwevo_core/env.py）"),
    (r"PLATFORM_DOCWEAVE_PORT",          "旧运行器变量：兼容读取"),
    (r"--docweave\b",                    "旧命令行参数：保留为别名"),
    (r"(app_id|--app|app (enable|grant|revoke|disable)|OIDC_APP_ID).*docweave|docweave.*app_id",
                                          "app_id 值：apps / workspace_apps / app_entitlements 的主键"),
    (r"[\"'`]docweave[\"'`]",            "app_id 值（字面量）"),
    (r"docker (exec|logs|restart|stop|compose exec).*docweave|容器 `docweave`|docweave:local|/opt/docweave",
                                          "已部署实例的容器名与主机路径：本轮未部署，命令仍然有效"),
    (r"服务名.*`docweave`|仍然叫 `docweave`",
                                          "Compose 服务改名的说明"),
    (r"hooks/docweave",                  "文档里的示例 Webhook 地址"),
    (r"docweave/(mfa-seed|recovery)/v\d",
                                          "密钥派生的域分隔标签：改掉它，库里已加密的 TOTP 种子与已发出的恢复码全部失效"),
    (r"LEGACY_PREFIX|回落到 `DOCWEAVE_|兼容旧的 `DOCWEAVE_|旧的 DOCWEAVE_\* 名字|DOCWEAVE_<后缀>|DOCWEAVE_\* 配置",
                                          "配置兼容层自身的实现与说明"),
    (r"docweave-is-my-password",         "口令黑名单的测试用例"),
    (r"DOCWEAVE_\{suffix\}|配置必须继续可用",
                                          "配置兼容层的测试"),
    (r"later\[\"docweave\"\]",           "测试内部的服务字典键"),
    (r"docweave-rehearsal-|docweave-test-|docweave-snapshot-",
                                          "临时目录前缀"),
    (r"docweave\.[a-z_]+",               "内部稳定标识 / 本地存储旧键"),
]
SKIP_SUFFIX = (".png", ".jpg", ".xlsx", ".docx", ".pdf", ".pptx", ".traineddata", ".bin", ".csv", ".zip")


def main() -> int:
    files = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True).stdout.split()
    buckets: dict[str, list[str]] = defaultdict(list)
    for f in files:
        p = ROOT / f
        if p.suffix in SKIP_SUFFIX or "package-lock" in f:
            continue
        try:
            text = p.read_text(encoding="utf-8")
        except (UnicodeDecodeError, FileNotFoundError, OSError):
            continue
        if not re.search(r"docweave", text, re.I):
            continue
        if f in HISTORICAL_FILES:
            buckets["历史记录：不改写"].append(f)
            continue
        for i, line in enumerate(text.splitlines(), 1):
            if not re.search(r"docweave", line, re.I):
                continue
            reason = next((r for pat, r in STABLE if re.search(pat, line, re.I)), None)
            buckets[reason or "未分类 —— 漏改或需要新增一条保留理由"].append(
                f"{f}:{i}: {line.strip()[:120]}")

    unclassified = buckets.pop("未分类 —— 漏改或需要新增一条保留理由", [])
    total = sum(len(v) for v in buckets.values())
    print(f"旧品牌字符串共 {total + len(unclassified)} 处，已归类 {total} 处。\n")
    for reason in sorted(buckets):
        hits = buckets[reason]
        where = sorted({h.split(":")[0] for h in hits})
        print(f"  [{len(hits):>3}] {reason}")
        print(f"        {', '.join(where[:4])}{' …' if len(where) > 4 else ''}")
    if unclassified:
        print(f"\n✗ {len(unclassified)} 处没有归类：", file=sys.stderr)
        for line in unclassified:
            print("    " + line, file=sys.stderr)
        print("\n要么改掉它，要么在 STABLE 里加一条并写清为什么必须保留。", file=sys.stderr)
        return 1
    print("\n✓ 没有未分类的旧品牌字符串。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
