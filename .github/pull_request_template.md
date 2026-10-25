<!--
基础分支请选 `dev`；`main` 只接受维护者从 `dev` 发起的同步 PR。安全问题不要在这里描述，见 SECURITY.md。
Base branch: `dev`. `main` only takes sync pull requests from `dev`. Never describe a security issue here: see SECURITY.md.
-->

## 改动与原因 · What and why

<!-- 解决什么问题，改了哪些模块；有关联 issue 就写上。What problem this solves and which modules change; link the issue if there is one. -->

## 验证 · Verification

<!--
passed / failed / skipped / not run 分开写，附命令。Report passed / failed / skipped / not run separately, with the commands.
只改 docs/ 记录的 PR 在 CI 里只跑全仓守卫。A pull request that only changes records under docs/ runs only the repository-wide guards in CI.
-->

- Python（`.venv/bin/python -m pytest …`）：
- 守卫 · Guards（`scripts/check_*.py`、`brand_assets.py --check`、`app_icon_assets.py --check`）：
- 前端 · Front end（`npm run typecheck && npm run build`、`npm test --workspaces --if-present`）：

## 检查清单 · Checklist

- [ ] 基础分支是 `dev` · The base branch is `dev`
- [ ] 每个提交都有 `Signed-off-by:`（`git commit -s`，DCO）· Every commit is signed off (DCO)
- [ ] 不含 `data/`、密钥、凭据或真实个人数据；测试夹具已标明是夹具 · No `data/`, keys, credentials or real personal data; fixtures are labelled
- [ ] 遵守 AGENTS.md 的模块边界与"不要做的事" · Follows the module boundaries and "what not to do" in AGENTS.md
- [ ] 用户可见的改动更新了相关文档 · User-visible changes update the relevant documentation
