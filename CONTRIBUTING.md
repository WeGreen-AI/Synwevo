# 参与 Synwevo · Contributing to Synwevo

[简体中文](#简体中文) · [English](#english)

## 简体中文

### 分支：PR 指向 `dev`

* **`dev`** 承接全部开发，也是生产部署的来源。请把 PR 的基础分支设为 `dev`。
* **`main`** 是公开的开源分支，是 `dev` 的合并镜像，只接受维护者从 `dev` 发起的同步 PR。指向 `main` 的 PR
  会被 `sync-source` 检查拒绝：把基础分支改成 `dev`，再推一个提交（或请维护者关闭后重开 PR），`verify` 才会运行。
* 合并一律用 merge commit（不 squash、不 rebase），提交 SHA 在记录里会被引用。合入 `dev` 会部署到生产，
  所以每个 PR 都要通过必需检查 `verify` 并经维护者审阅。

```bash
git fetch origin dev
git switch --no-track -c <你的分支> origin/dev   # 不跟踪 dev，避免一次裸 push 推到 dev 上
# … 修改、提交（带签名，见下）…
git push <你的 fork> HEAD:refs/heads/<你的分支>
```

### 动手之前

* 读 [AGENTS.md](AGENTS.md)：模块边界、验证、数据与安全、不要做的事，对人和 AI 代理一视同仁；
  目录与数据所有权见 [docs/architecture/monorepo.md](docs/architecture/monorepo.md)。
* 较大的改动先开 issue 说明要解决的问题。
* **安全问题不要开公开 issue 或 PR**，按 [SECURITY.md](SECURITY.md) 私下报告。

### 本地检查

需要 Python 3.12+ 与 Node 22。在仓库根目录：

```bash
python scripts/platform.py setup                  # 建 .venv、按锁定版本装依赖、构建前端
.venv/bin/python -m pytest                        # 全部 Python 套件
.venv/bin/python scripts/check_boundaries.py      # 模块依赖边界
.venv/bin/python scripts/check_brand.py           # 旧品牌残留
.venv/bin/python scripts/check_design.py          # 颜色只来自共享令牌
.venv/bin/python scripts/check_component_names.py # 界面只写能力名
.venv/bin/python scripts/brand_assets.py --check  # Logo 衍生文件
.venv/bin/python scripts/app_icon_assets.py --check
npm run typecheck && npm run build
npm test --workspaces --if-present
```

CI 的 `verify` 还会在生产运行时镜像里跑完整套件（见 [.github/workflows/production.yml](.github/workflows/production.yml)）。
只改 `docs/` 记录的 PR 只跑全仓守卫；`docs/integration/` 与 `docs/operations.md` 有测试读取，按代码处理。

在 PR 里把 passed / failed / skipped / not run 分开写，不要在没有实测的情况下宣称"全部通过"。
测试只用标明是夹具的合成数据，永远不指向 `data/`；不要 Mock 产品本身、假登录或假数据。

### DCO 签名

每个提交都要带一行签名，表示你按 [Developer Certificate of Origin 1.1](https://developercertificate.org/)
提交这份改动：

```text
Signed-off-by: 你的名字 <you@example.com>
```

用 `git commit -s` 自动加上；名字与邮箱用你愿意公开的身份（可以用 GitHub 的 noreply 邮箱）。
漏签的提交用 `git rebase --signoff origin/dev` 补上后再推送到你的分支。

### 许可

你的贡献按它所在文件适用的许可提供（仓库与 SDK 目录里的 LICENSE 文件；inbound = outbound）。
Synwevo 的名称、Logo 与应用图标不随代码许可授予。

---

## English

### Branches: open pull requests against `dev`

* **`dev`** carries all development and is the source of production deploys. Set your pull request's
  base branch to `dev`.
* **`main`** is the public open-source branch, a merge-mirror of `dev`; it only takes sync pull requests
  that maintainers open from `dev`. A pull request against `main` fails the `sync-source` check: change
  its base branch to `dev`, then push a commit (or ask a maintainer to close and reopen it) so that
  `verify` runs.
* Merges are always merge commits (no squash, no rebase), because records cite commit SHAs. A merge into
  `dev` deploys to production, so every pull request must pass the required `verify` check and a
  maintainer's review.

```bash
git fetch origin dev
git switch --no-track -c <your-branch> origin/dev   # no upstream: a bare push can never land on dev
# … change, commit with a sign-off (below) …
git push <your-fork> HEAD:refs/heads/<your-branch>
```

### Before you start

* Read [AGENTS.md](AGENTS.md): module boundaries, verification, data and security, and what not to do
  apply to people and AI agents alike. Directories and data ownership are in
  [docs/architecture/monorepo.md](docs/architecture/monorepo.md).
* For a larger change, open an issue first that describes the problem.
* **Never report a security issue in a public issue or pull request**; follow [SECURITY.md](SECURITY.md).

### Local checks

You need Python 3.12+ and Node 22. From the repository root:

```bash
python scripts/platform.py setup                  # .venv, locked dependencies, front-end builds
.venv/bin/python -m pytest                        # every Python suite
.venv/bin/python scripts/check_boundaries.py      # module dependency boundaries
.venv/bin/python scripts/check_brand.py           # legacy brand strings
.venv/bin/python scripts/check_design.py          # colors only from the shared tokens
.venv/bin/python scripts/check_component_names.py # the interface names capabilities, not components
.venv/bin/python scripts/brand_assets.py --check  # logo derivatives
.venv/bin/python scripts/app_icon_assets.py --check
npm run typecheck && npm run build
npm test --workspaces --if-present
```

CI's `verify` also runs the complete suite inside the production runtime image (see
[.github/workflows/production.yml](.github/workflows/production.yml)). A pull request that only changes
records under `docs/` runs the repository-wide guards only; `docs/integration/` and `docs/operations.md`
are read by tests and count as code.

In the pull request, report passed / failed / skipped / not run separately, and never claim "all green"
without running the checks. Tests use clearly labelled synthetic fixtures and never point at `data/`; do
not mock the product itself, fake a sign-in or fake data.

### DCO sign-off

Every commit carries a sign-off line stating that you submit it under the
[Developer Certificate of Origin 1.1](https://developercertificate.org/):

```text
Signed-off-by: Your Name <you@example.com>
```

`git commit -s` adds it; use a name and e-mail address you are willing to publish (GitHub's noreply
address is fine). Add a missing sign-off with `git rebase --signoff origin/dev` and push your branch again.

### Licensing

Your contribution is provided under the licence that applies to the files it changes (the LICENSE files
of the repository and of the SDK directories; inbound = outbound). The Synwevo name, logos and app icons
are not licensed with the code.
