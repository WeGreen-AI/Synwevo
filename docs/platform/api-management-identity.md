# 平台管理机器身份（API-3 设计）

状态：设计，待管理员平台 B1-2（委派平台权限 `platform_access.require_platform`）合并后实现。
约定来源：[`../ai/api-platform.md`](../ai/api-platform.md) §4.1「平台管理身份」、§4.3、§14 场景 D。

## 1. 要解决的问题

今天 `/api/admin/*` 只认浏览器会话 Cookie（加 CSRF、双因素会话、近期重新验证与理由）。机器凭据只能访问
Docs 资源（`urn:synwevo:app:docweave`），`docweave.manage` 只覆盖工作空间内的 Webhook 与自身凭据。
于是：安全/运维自动化读不到调用记录与审计；配额调整只能人工点页面；场景 D（受限管理服务身份提交低风险
配置变更）无法成立。

## 2. 边界（不做什么）

* 不给业务凭据加管理能力：Docs 的 `dw_` 密钥、服务账户、用户委托一律不能调用管理端点（资源/受众不同，网关拒绝）。
* 不做万能管理密钥：管理身份只有列明的 scope，每个 scope 对应一个平台权限（B1-2 目录），签发人必须当前持有该权限。
* 不把人工高风险步骤变成机器端点：签发/轮换凭据（会返回秘密）、撤回审批、账户停用、角色授予、恢复与删除仍只在
  浏览器里由有权管理员完成（近期重新验证 + 理由）。机器身份可以**申请**这类变更（返回待审批状态），不能执行。
* 不读业务内容：管理身份没有任何工作空间，也就没有文档、私聊、附件的读取路径。

## 3. 身份与凭据

| 项 | 设计 |
|---|---|
| 形式 | OIDC 服务客户端（`client_credentials`），`kind='service'`、`app_id='platform-admin'`，**不绑定工作空间** |
| 资源 / 受众 | 新资源 `urn:synwevo:platform:admin`（RFC 8707）；访问令牌 `aud` 必须是它；Docs 网关拒绝此受众，管理网关拒绝 Docs 受众 |
| scope | `platform.api.read`（控制台读取：调用、配额、凭据元数据、事件与投递状态）、`platform.api.quota`（修改流量与容量策略）、`platform.audit.read`（审计读取，按 B1-2 的 `audit.read` 权限） |
| 签发 | 管理台「管理身份」页：持有对应平台权限的管理员选择 scope 与期限（≤ 90 天，必填），近期重新验证 + 理由；秘密只显示一次 |
| 每次调用复核 | 客户端未停用、未过期；签发人**当前**仍持有每个 scope 对应的平台权限（B1-2 授予表），否则该 scope 失效；全部失效即 403 `management_authority_withdrawn` |
| 期限与轮换 | 客户端到期即失效（不同于 Docs 服务账户）；轮换沿用 `rotate_secret` 的有界重叠期 |
| 限流 | `api_quotas` 的凭据层（`credential:service_account:<client_id>`）同样适用；另设管理身份默认 60 次/分钟 |

## 4. 路由

机器可调用的管理 API 放在独立前缀 `/api/admin/v1/`（Bearer，无 Cookie、无 CSRF），与浏览器的
`/api/admin/api-platform/*` 调用**同一服务函数**，因此规则只写一处：

| 方法 | 路径 | scope | 对应浏览器操作 |
|---|---|---|---|
| GET | `/api/admin/v1/calls` | `platform.api.read` | 调用记录（同样的精确过滤；不含令牌/正文） |
| GET | `/api/admin/v1/quotas`、`/quotas/effective`、`/quotas/history/{scope}` | `platform.api.read` | 流量与配额读取 |
| POST | `/api/admin/v1/quotas/preview/{scope}` | `platform.api.read` | 预演 |
| PUT | `/api/admin/v1/quotas/{scope}` | `platform.api.quota` | 修改（`base_version` + `reason` 必填；审计 `actor_kind=machine`，记录签发人） |
| GET | `/api/admin/v1/credentials` | `platform.api.read` | 凭据元数据（无秘密） |
| GET | `/api/admin/v1/audit` | `platform.audit.read` | 审计（分页、按动作/对象/时间） |

写操作的「近期重新验证」由签发时的强认证与期限替代；「理由」仍必填并写入审计。

## 5. 验收（场景 D）

1. 管理员签发 `platform.api.quota` 管理身份 → 取令牌 → PUT 工作空间配额 → 下一次 Docs 调用按新值执行。
2. 两个管理身份以同一 `base_version` 并发修改 → 一个成功一个 409 `stale_policy`（附当前值）。
3. Docs 业务凭据调用 `/api/admin/v1/*` → 401/403（受众不符）；管理令牌调用 `/api/v1/*` → 403。
4. 撤销签发人的 `api_platform.manage` 授予 → 该管理身份下一次写 403，读取按剩余权限。
5. 客户端到期 → 401；已排队的操作（本设计中没有异步管理任务）不适用。
6. 审计可按请求编号查到机器写入、签发人与理由；拒绝同样入审计。
