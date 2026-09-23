"""平台级配置：名称、门户地址等三方都要读同一个答案的东西。

`platform_name()` 原本住在门户的路由模块里，于是账户服务为了在登录页上写出平台名称，得从门户
`import`——一个不依赖门户就不能显示登录页的账户服务。它是一个配置读取，属于共享层。
"""
from __future__ import annotations

import os


def platform_name() -> str:
    """Configurable, defaulting to something neutral.

    The platform's real name is not decided yet, and hard-coding DocWeave as the name of the
    platform it is only one application on would have to be undone later in every string.
    """
    return os.environ.get("DOCWEAVE_PLATFORM_NAME") or "工作平台"


def portal_url() -> str:
    """Where the account pages' "back" link goes. Server-controlled, never a query parameter."""
    return (os.environ.get("DOCWEAVE_PORTAL_URL") or "").rstrip("/")
