# Synwevo Account（`services/identity/`）

平台上所有应用共用的那一个登录。**先读仓库根目录的 `AGENTS.md`。**

## 这个模块拥有什么

| 文件 | 内容 |
|---|---|
| `synwevo_identity/auth_routes.py` | 登录、首位管理员、重认证、会话列表与撤销、邀请、找回、邮箱验证 |
| `synwevo_identity/mfa.py`、`passkeys.py` | TOTP 与恢复码、WebAuthn/Passkey 仪式 |
| `synwevo_identity/provider_routes.py` | OIDC 协议端点：discovery/JWKS/authorize/token/userinfo/revoke/logout |
| `synwevo_identity/admin_routes.py` | 平台管理后台：概览、账户、工作空间、审计检索 |
| `synwevo_identity/mail.py`、`invites.py` | 邮件投递与邀请 |
| `synwevo_identity/cli.py` | 账户侧 CLI：bootstrap、应用与资格、OIDC 客户端、签名密钥 |
| `web/` | **账户与安全界面，全平台唯一一份**（独立 Vite 构建，`account-assets/`） |

## 这个模块**不**拥有什么

* 用户、工作空间、成员、应用资格的**数据访问**在 `synwevo_core`——门户和业务应用也要读它们，
  留在这里就意味着别人要 import 这个服务才能判断成员资格。
* OIDC 的**令牌存储与校验**也在 `synwevo_core`：验证令牌的一方不是签发它的一方。
* `/api/auth/state`、`/me`、`/logout`、`/workspace` 在 `synwevo_auth_client.session_routes`——
  三个角色都要提供这四条，写两遍的那一遍会忘记重新核对工作空间成员资格。

## 硬约束

* **不导入任何业务应用。** `synwevo_identity` 的依赖清单里没有 `docweave`，这是可机械检查的事实：
  账户服务必须能在文档引擎缺席时启动。
* **不新建第二套账户权威。** 唯一的用户表就是现有的 `users` / `user_passwords` / `sessions` /
  `webauthn_credentials` / `totp_secrets`。
* **不放宽回调白名单、不关闭安全校验**来让某个页面"能打开"。
* **账户与安全界面只有一份。** 不要为了让某个应用"自己也有一个账户页"复制第二套。

## 独立运行

```bash
# 从仓库根目录
python scripts/platform.py up accounts       # 只启动账户服务
# http://accounts.localhost:8770/login
python -m synwevo_identity.cli info         # 账户侧配置
```

Synwevo Docs 停用时，账户服务与账户页照常工作；门户的应用卡片按注册表如实反映应用状态。

## 验证

```bash
python -m pytest services/identity/tests
python -m pytest tests/integration            # 跨模块的登录链路
```
