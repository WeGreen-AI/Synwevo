# Synwevo Logo

**这些文件不在本仓库的代码许可之内。** Synwevo 名称与 Logo 受 [TRADEMARKS.md](../../../TRADEMARKS.md) 约束：
AGPL-3.0-or-later（[LICENSE](../../../LICENSE)）与 Apache-2.0 都不授予它们任何权利。它们随源代码提供，
只是为了让官方版本能原样构建和运行；分发修改过的版本、或把它作为服务提供给他人时，请换成你自己的图形。

| 位置 | 内容 |
|---|---|
| `originals/` | 三张确认原图：完整版 `synwevo-logo-full.png`、图形版 `synwevo-logo-mark.png`、字标版 `synwevo-logo-wordmark.webp`，原样保存 |
| `derived/` | 从完整版裁出的全尺寸衍生图及白色版本 |
| `assets.json` | 每个衍生文件的来源哈希、裁切框、尺寸与哈希 |
| `../src/brand/` | 界面使用的各尺寸 Logo 与浏览器 / 主屏图标（由脚本生成） |
| `../public-brand/` | `favicon.ico` 与链接预览图 `share-card.png`（由脚本生成） |

`services/identity/synwevo_identity/static/email-logo.png`（系统邮件中的 Logo）同样受商标政策约束。

**来源。** 完整版与图形版原图由生成式图像工具（ChatGPT，gpt-image）于 2026-09-25 生成，文件内嵌 C2PA 来源声明；
字标版 `.webp` 不含该声明。衍生文件只经过 `scripts/brand_assets.py` 的裁切、缩放、放入图标底板和改为白色，
规则见 [docs/design.md](../../../docs/design.md) §2.5。要改 Logo，只替换确认原图并重新生成，不手改衍生文件。

## English

**These files are not covered by the code licenses of this repository.** The Synwevo name and logos
fall under [TRADEMARKS.md](../../../TRADEMARKS.md); neither AGPL-3.0-or-later nor Apache-2.0 grants
any rights in them. They ship with the source only so that the official version builds and runs as
published. If you distribute a modified version or offer it to others as a service, replace them
with your own artwork. The full and mark originals were made with a generative image tool (ChatGPT,
gpt-image) on 2026-09-25 and carry embedded C2PA provenance; every derived file is produced from the
full lockup by `scripts/brand_assets.py` (crop, resize, icon tile, white recolour only).
