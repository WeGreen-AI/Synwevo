# 安全策略 · Security policy

[简体中文](#简体中文) · [English](#english)

## 简体中文

### 报告漏洞

请通过 GitHub 的**私密漏洞报告**提交：仓库的 Security 页 → "Report a vulnerability"
（<https://github.com/WeGreen-AI/Synwevo/security/advisories/new>）。报告只有你和维护者可见，
后续沟通、修复与披露都在这份安全公告里进行。

* **不要**在公开 issue、PR、讨论或提交信息里写漏洞细节、利用步骤或概念验证。
* 请写明：受影响的组件或路径、提交 SHA、在你自己的实例上复现的步骤、影响范围。
* 只在你自己的部署或本机实例上测试。不要访问不属于你的数据，不要做拒绝服务或大量自动化扫描。

### 支持范围

只支持 `dev` 当前部署到生产的版本，也就是 `dev` 的最新头部。`main` 是 `dev` 的镜像；更早的提交、
fork 与自行部署的副本不单独发布修复，请更新到最新版本。

### 修复流程

修复在安全公告的临时私有 fork 里开发与审阅。那里不跑状态检查，分支规则也不生效，所以**不从安全公告里合并**。
修复准备好、可以立即部署时，作为普通 PR 提到 `dev`：必需检查 `verify` 照常运行，以 merge commit 合入，合入即部署。
提交与 PR 文字只说明修了什么，不写利用方法。部署完成后再公开安全公告，随后同步到 `main`。

## English

### Reporting a vulnerability

Use GitHub's **private vulnerability reporting**: the repository's Security tab → "Report a vulnerability"
(<https://github.com/WeGreen-AI/Synwevo/security/advisories/new>). Only you and the maintainers can see the
report; discussion, the fix and disclosure all happen in that security advisory.

* **Do not** put vulnerability details, exploitation steps or a proof of concept in a public issue, pull
  request, discussion or commit message.
* Please include the affected component or path, the commit SHA, steps to reproduce on your own instance,
  and the impact.
* Test only against your own deployment or a local instance. Do not access data that is not yours, and do
  not run denial-of-service tests or heavy automated scanning.

### Supported versions

Only what `dev` currently deploys to production, i.e. the latest head of `dev`, is supported. `main` mirrors
`dev`; older commits, forks and self-hosted copies get no separate fixes: update to the latest version.

### How fixes are handled

A fix is developed and reviewed in the security advisory's temporary private fork. Status checks do not run
there and branch rules are not enforced, so a fix is **never merged from the advisory**. When it can be
deployed at once, it goes to `dev` as an ordinary pull request: the required `verify` check runs as usual, it
is merged with a merge commit, and the merge deploys. Commit and pull request text says what was fixed, never
how to exploit it. The advisory is published after the deploy, and the fix then syncs to `main`.
