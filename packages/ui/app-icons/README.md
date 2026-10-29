# Synwevo 应用图标与平台图标

**这些文件不在本仓库的代码许可之内。** Synwevo 的应用图标与平台图标受 [TRADEMARKS.md](../../../TRADEMARKS.md)
约束：AGPL-3.0-or-later（[LICENSE](../../../LICENSE)）与 Apache-2.0 都不授予它们任何权利。它们随源代码提供，
只是为了让官方版本能原样构建和运行；分发修改过的版本、或把它作为服务提供给他人时，请换成你自己的图标。

| 位置 | 内容 |
|---|---|
| `originals/icons/` | 12 张原图（8 个应用、4 个平台入口），1254×1254 RGBA PNG，原样保存 |
| `source-manifest.json` | 原图包的元数据与每张原图的 SHA-256 |
| `registry.json` | 唯一手写映射：真实应用 ID / 模块路径 → 原图与尺寸 |
| `derived-manifest.json` | 每个变体的来源哈希、输出哈希、尺寸与字节数 |
| `../src/app-icons/` | 界面使用的透明 PNG 变体与 `assets.ts`（由脚本生成） |

**来源。** 原图由生成式图像工具（ChatGPT，gpt-image）于 2026-09-30 生成，文件内嵌 C2PA 来源声明
（`source-manifest.json` 记为 AI-generated raster PNG）。变体只由 `scripts/app_icon_assets.py` 缩放生成，
接入记录见 [docs/platform/app-icons.md](../../../docs/platform/app-icons.md)；不手改变体。

## English

**These files are not covered by the code licenses of this repository.** The Synwevo application and
platform icons fall under [TRADEMARKS.md](../../../TRADEMARKS.md); neither AGPL-3.0-or-later nor
Apache-2.0 grants any rights in them. They ship with the source only so that the official version
builds and runs as published. If you distribute a modified version or offer it to others as a
service, replace them with your own icons. The originals were made with a generative image tool
(ChatGPT, gpt-image) on 2026-09-30 and carry embedded C2PA provenance; the variants are resized from
them by `scripts/app_icon_assets.py` only.
