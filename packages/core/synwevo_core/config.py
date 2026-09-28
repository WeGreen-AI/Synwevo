"""平台级配置：名称、门户地址等三方都要读同一个答案的东西。

`platform_name()` 原本住在门户的路由模块里，于是账户服务为了在登录页上写出平台名称，得从门户
`import`——一个不依赖门户就不能显示登录页的账户服务。它是一个配置读取，属于共享层。
"""
from __future__ import annotations

import os

from . import env


#: 平台正式名称。仍然可配置——自托管的人有权把它改成自己的名字——但默认值不再是占位符。
PLATFORM_NAME = "Synwevo"
#: 平台首页/工作空间的正式名称。导航里写"首页""工作空间"没问题，署名处用全称。
WORKSPACE_NAME = "Synwevo Workspace"
#: 统一账户体系的正式名称。
ACCOUNT_NAME = "Synwevo Account"
#: 文档应用的对外名称。它是平台上的一个应用，不是平台本身。
DOCS_NAME = "Synwevo Docs"
#: 消息应用的对外名称：组织内的私聊与群聊，自 CI-66 起是平台上的第二个应用，有自己的源站
#: （生产 https://message.synwevo.com）。名字里没有 s：产品名是 Message。
MESSAGES_NAME = "Synwevo Message"


def platform_name() -> str:
    """平台显示名，可由部署方覆盖。

    上一版这里的默认值是占位符「工作平台」，注释写着"平台的真名还没定"。定了：Synwevo。
    仍然读配置，因为自托管的人有权把它改成自己的名字；改的是默认值不再假装不知道。
    """
    return env.get("PLATFORM_NAME") or PLATFORM_NAME


def portal_url() -> str:
    """Where the account pages' "back" link goes. Server-controlled, never a query parameter."""
    return (env.get("PORTAL_URL") or "").rstrip("/")


def site_url() -> str:
    """The public site: the home page, 数据与隐私 and 服务说明 (production: https://synwevo.com).

    `SYNWEVO_PORTAL_URL` is the portal process's own address — where the workspace lives and where
    its sign-in round trip returns (production: https://workplace.synwevo.com). The two are the same
    process on two hosts; a deployment that sets no site address keeps both on one host, which is
    what local development and a single-host install do."""
    return (env.get("SITE_URL") or env.get("PORTAL_URL") or "").rstrip("/")
