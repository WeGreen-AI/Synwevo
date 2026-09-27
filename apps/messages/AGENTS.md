# Synwevo Message（`apps/messages/`）

平台上的第二个应用：组织内的私聊与群聊，有自己的源站（生产 `https://message.wegreen.ltd`，
本机 `http://message.localhost:8768`）。**先读仓库根目录的 `AGENTS.md`**——这里只写这个模块自己的事。
设计记录在 `docs/platform/messaging.md`。

## 这个模块拥有什么

| 目录 | 内容 |
|---|---|
| `synwevo_messages/app.py` | 工厂：`create_app()`（角色 `messages`）、`install()`（组装层挂载）、`install_legacy()`（账户角色的旧路径兜底） |
| `synwevo_messages/routes.py` | HTTP：`/api/messaging/…` 会话、消息、表情回应、阅读位置、搜索、附件、链接卡片、举报提交、事件流 |
| `synwevo_messages/bridge_routes.py` | 应用联动（CI-75）：`/api/messaging/objects/{app}/{kind}/{id}/conversation`、`/conversations/{cid}/object`，以及把 Docs 外发表变成卡片的 worker 的启动（规则、表与渲染在 `synwevo_core.messaging_bridge`；见 messaging.md §8） |
| `web/` | 消息外壳（独立 Vite 构建，静态资源目录 `messages-assets/`） |
| `tests/` | 这个模块自己的测试（会话直接由 `synwevo_core.sessions.Sessions` 铸造：`messages` 角色不提供 `/api/auth/login`） |

## 这个模块**不**拥有什么

* **业务层与表**在 `synwevo_core.messaging`（`conversations`、`conversation_members`、`messages`、
  `message_reactions`、`message_attachments`、`message_reports`），由 `Store` 统一建表；本应用不做迁移。
* **举报的处理**（列表与裁决）在账户服务：它要求 `org.messaging.manage` 与账户会话上的严格重认证，
  路由在 `services/identity/synwevo_identity/messaging_reports_routes.py`，界面在组织页。本应用只接受举报。
* **未读徽标**（`/api/auth/messaging/unread-count`）由共享会话路由在每个源回答；本应用也挂它。
* 登录、账户与安全、平台管理都在账户服务；这里 `/login`、`/account` 是重定向，不是页面。
* 不 import `synwevo_identity`、`synwevo_portal`、`synwevo_docs`：`python scripts/check_boundaries.py` 会拒绝。

## 独立运行

账户服务正常时，门户与 Docs 停掉，消息应用仍然可用：

```bash
python scripts/platform.py up accounts messages
# http://message.localhost:8768/ 经统一登录进入会话列表
```

## 单一写入源

会话的写入只经这个应用的 `/api/messaging/…`。账户服务在注册表里看到 `messages` 应用后，对旧的
`/api/auth/messaging/…` 答 410 并点名新地址，对 `GET /messages[/{cid}]` 答 303 到本应用的
`/auth/start?next=/c/{cid}`；没有本应用的部署才由组装层把旧路径挂在账户角色上兜底。
事件流的扇出仍是单进程内存：账户服务上处理举报删除的消息，开着的会话标签页在下一次同步时才看到。
