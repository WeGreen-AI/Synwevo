# 可持久化的内置模板模型

`/api/v1` 现在可以直接建立 DOCX 或 XLSX 模板，不需要浏览器先放置字段。模型与产品编辑器共用
`builtin_service` 和实际 Office 文件生成引擎；上传原有模板仍使用 `/templates` 与 `/templates/{tid}/fields`。
以下为明确标识的合成样本，不包含用户数据。

| 方法 / 路径 | operationId | 权限 / 副作用 |
|---|---|---|
| POST `/templates/builtin` | `create_builtin_template` | write；创建模板及版本 1，可带 Idempotency-Key |
| GET `/templates/{tid}/builtin?version=1` | `get_builtin_template` | read；读取指定版本的 editor、fields；省略 version 取当前 head |
| PUT `/templates/{tid}/builtin` | `save_builtin_template` | write；创建新版本；必须带 base_version；可带 Idempotency-Key；编辑器里有未保存的实时修改时 409 `live_edits`，除非 `discard_live: true` |

创建体包含 `kind`、`editor` 和可选 `name`。保存体包含 `editor`、`base_version`、可选 `name` 与可选 `note`（≤200 字，这个版本的说明；留空即「在编辑器中保存」）。
旧版本不会被覆盖；版本已前进时返回 409 `stale_template`，同键同请求的成功重放仍返回原版本。
当前对象权限检查也适用于重放、历史版本及模型读取；跨空间和已删除模板返回 404。
请求体按 `SYNWEVO_DOCS_MAX_EDITOR_BODY_BYTES` 限制，包括没有 Content-Length 的分块请求。
机器可读形状见 [OpenAPI](openapi.yaml) 中 `DocumentEditorModel`、`SpreadsheetEditorModel` 和 `EditorNode`。
上下文约束（节点放置、字段冲突、表格跨度、可用正文区域等）还会由服务端验证。

## DOCX

```json
{
  "kind": "builtin_doc",
  "name": "合成客户通知",
  "editor": {
    "page": {"size": "A4", "orientation": "portrait"},
    "base_size": 11,
    "font_ea": "宋体",
    "body": {"type": "doc", "content": [
      {"type": "paragraph", "content": [
        {"type": "text", "text": "客户："},
        {"type": "field", "attrs": {"id": "customer", "name": "客户", "required": true}}
      ]}
    ]}
  }
}
```

`body/header/footer` 是 ProseMirror 文档节点；旧客户端的裸 `{"type":"doc","content":[...]}` 继续接受。
正文支持段落、1–6 级标题、列表、表格、图片、分隔线、分页和重复内容块。
段落内部支持文本、换行和 field；表格单元格内支持段落及标题。未知节点或标记明确拒绝，不静默丢弃。
文字标记支持 bold、italic、underline、strike、textStyle（color/fontSize/fontFamily）和黄色 highlight。
图片必须是嵌入的 PNG/JPEG/GIF/BMP Base64 数据；不访问远程 URL。纸张大小为 A4/A3/A5/Letter/Legal/B5，
页边距单位为毫米，字号单位沿用编辑器点值；边距不能占满页面。

重复行使用 tableRow 的 `repeat:true`、`repeatId`、`repeatName`、`emptyPolicy`，其子字段成为明细列。
重复内容块使用 `type:"repeatBlock"`，属性为 `repeatId/repeatName/separator/emptyPolicy`；子内容限段落、
标题、列表、图片和分隔线。separator 为 none/blank/page_break；内容块空策略为 remove/placeholder/error，
表格空策略为 remove_row/blank_row/placeholder/remove_table/error。

## XLSX

```json
{
  "kind": "builtin_sheet",
  "name": "合成客户表格",
  "editor": {"sheets": [{
    "name": "结果",
    "cells": {"A1": {"v": "客户"}, "B1": {"v": "待填"}},
    "fields": [{"id": "customer", "name": "客户", "kind": "cell", "ref": "B1", "required": true}]
  }]}
}
```

每张表包含 name、cells 和可选 cols/rows/merges/fields。cells 使用 A1 坐标；cols 的 `w`、rows 的 `h`
沿用编辑器像素尺寸，输出时转换为 Excel 尺寸。字段 kind 为 cell/region/repeat：前两者使用单元格/区域 ref，
重复行使用 **从零起算**的 row、c1、c2。自定义字段 id 在整个模板中唯一，不能覆盖另一个字段。
样式 s 可含 b/i/u/wrap/bd（布尔值或历史 0/1）、sz、color/bg、ha/va 和 nf；未知枚举被拒绝。

`t:"text"` 保留前导零，`=1+2` 仍是文本；`t:"number"` 必须是有限数字，否则拒绝。
省略 t 或使用 auto 时，`=` 开头的模板内容按公式保存；生成仍走现有公式计算与安全规则。
工作表最多 100 张，单元格数量还受当前部署的 MAX_CELLS 限制；合并区域受引擎规模检查。

## 映射与验证

创建/保存后，读取返回的字段 binding，或使用已有映射 API 的 `field:"客户"` 建立来源映射。
给项目绑定明确模板版本，再调用 preflight、preview 和 jobs；不需要直写数据库或预置模板。
`apps/docs/tests/test_api_builtin.py` 通过 HTTP 建立两种模板、修改、读取历史版本、映射、预览、生成，
读回两份真实 DOCX/XLSX，并比较相同模型经 UI/API 创建的 Office ZIP 内容（忽略非业务核心属性）。
此证据使用合成输入，不等于全部排版、设备、字体和办公软件兼容性均已验证。
