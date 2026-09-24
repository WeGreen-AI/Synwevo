"""配置读取：新的 `SYNWEVO_*` 名字，兼容旧的 `DOCWEAVE_*`。

改品牌不该让别人的部署在某个早上静默换一套配置。所以这里做的是**兼容读取**而不是重命名：

* 只设了新名字 —— 用新的。
* 只设了旧名字 —— 用旧的，并且在进程生命周期里**提示一次**该换了。
* 两个都设了、值相同 —— 用它，不啰嗦。
* 两个都设了、值不同 —— 对普通项取新的并提示；对**安全或数据路径**相关的项**直接拒绝启动**。

最后一条是这个文件存在的理由。`DOCWEAVE_DATA_DIR=/srv/a` 和 `SYNWEVO_DATA_DIR=/srv/b` 同时存在时，
任何一个"合理默认"都是在猜用户的数据在哪儿；猜错的那一次，服务会带着一个空库正常启动——而那正是
"数据不见了"的样子。这种情况必须由人来解决，不由优先级规则来解决。

作用域分两种，依据是**谁在读它**，不是前缀好不好看：

* 平台级（`SYNWEVO_*`）：账户服务、门户、依赖方适配、组装层与共享层读的配置。
* 文档应用级（`SYNWEVO_DOCS_*`）：只有 Synwevo Docs 自己读的配置（PDF 引擎、OCR、任务上限、Webhook…）。

两种作用域的旧名字都是同一个 `DOCWEAVE_<后缀>`，因为历史上只有一个前缀。
"""
from __future__ import annotations

import os

PLATFORM_PREFIX = "SYNWEVO_"
DOCS_PREFIX = "SYNWEVO_DOCS_"
LEGACY_PREFIX = "DOCWEAVE_"

#: 值不一致时必须由人裁决的后缀：指向数据、密钥、信任边界或身份地址。
CRITICAL = frozenset({
    "DATA_DIR", "SECRET", "ISSUER", "UI_AUTH", "UI_TENANT", "ROLE",
    "RP_ID", "RP_ORIGINS", "COOKIE_SECURE", "TRUST_PROXY",
    "APP_URL", "PUBLIC_URL", "PORTAL_URL", "OIDC_CLIENT_ID", "OIDC_APP_ID", "APP_ID",
    "SMTP_PASSWORD", "SMTP_USER", "WEBHOOK_SECRET", "API_KEY",
    "IMPORT_ROOTS", "REGISTRATION", "TEST_INSTANCE", "UI_DIR", "TESSDATA",
})

_warned: set[str] = set()


class ConfigConflict(RuntimeError):
    """新旧两个名字都设了，而且值不一样，且这一项不能靠猜。"""


def _warn_once(key: str, message: str) -> None:
    if key in _warned:
        return
    _warned.add(key)
    print(f"[synwevo] {message}", flush=True)


def _read(new_name: str, suffix: str, default: str | None) -> str | None:
    legacy_name = LEGACY_PREFIX + suffix
    new = os.environ.get(new_name)
    old = os.environ.get(legacy_name)

    if new is not None and old is not None and new != old:
        if suffix in CRITICAL:
            raise ConfigConflict(
                f"{new_name}={new!r} 与 {legacy_name}={old!r} 同时设置且不一致。"
                f"这一项关系到数据位置、密钥或信任边界，不能替你猜一个。"
                f"请删掉其中一个再启动（推荐保留 {new_name}）。")
        _warn_once(new_name, f"{new_name} 与 {legacy_name} 不一致，用的是 {new_name}；"
                             f"旧名字可以删掉了。")
    if new is not None:
        return new
    if old is not None:
        _warn_once(legacy_name, f"{legacy_name} 是旧名字，仍然可用；新名字是 {new_name}。")
        return old
    return default


def get(suffix: str, default: str | None = None) -> str | None:
    """平台级配置：`SYNWEVO_<后缀>`，回落到 `DOCWEAVE_<后缀>`。"""
    return _read(PLATFORM_PREFIX + suffix, suffix, default)


def docs(suffix: str, default: str | None = None) -> str | None:
    """Synwevo Docs 自己的配置：`SYNWEVO_DOCS_<后缀>`，回落到 `DOCWEAVE_<后缀>`。

    也接受不带 `DOCS_` 的 `SYNWEVO_<后缀>`，因为有几项（数据目录、监听地址）在文档应用的进程里
    读，语义上却是平台级的；让它们两种写法都成立，比让人记住哪一项属于哪一层更不容易出错。
    """
    # Precedence is SYNWEVO_DOCS_ > SYNWEVO_ > DOCWEAVE_, and the legacy name is compared with
    # whichever new name won. It used to be compared with SYNWEVO_DOCS_ only, so with
    # SYNWEVO_IMPORT_ROOTS=/srv/allowed and a leftover DOCWEAVE_IMPORT_ROOTS=/ the legacy value
    # came back — silently widening the import allow-list, on exactly the kind of key this
    # module exists to refuse to guess about.
    scoped_name = DOCS_PREFIX + suffix
    if os.environ.get(scoped_name) is not None:
        return _read(scoped_name, suffix, default)
    plain_name = PLATFORM_PREFIX + suffix
    if os.environ.get(plain_name) is not None:
        return _read(plain_name, suffix, default)
    return _read(scoped_name, suffix, default)


def flag(suffix: str, default: bool = False, *, scope: str = "platform") -> bool:
    raw = (docs(suffix) if scope == "docs" else get(suffix))
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def name_for(suffix: str, *, scope: str = "platform") -> str:
    """当前应当使用的变量名，用于错误信息与文档。"""
    return (DOCS_PREFIX if scope == "docs" else PLATFORM_PREFIX) + suffix
