"""The catalogue of the applications' audited actions (admin coverage G26, decision D7 §9).

The account service's audit page labels the applications' audit rows the way it labels its own
`admin.*` rows (`admin_actions.py`): every `docs.*` action Synwevo Docs writes and every `suite.*`
action the workplace applications write is listed here with its scope and a label in the three
interface languages. The account service may not import the applications (the repository
boundary, `scripts/check_boundaries.py`), so this module is data only: string literals, no code
from Docs or the workplace applications.

It is kept honest from both sides. A source-scan test in each application's suite scans that
application for `"docs.…"` / `"suite.…"` audit literals and refuses an action that is not
catalogued, and an integration test compares the applications' lists with this one, so the
catalogue can neither fall behind the code nor carry actions nobody writes any more.

Labels name the action, never its outcome or its object; the audit row carries those.
"""
from __future__ import annotations

APP_SCOPES: dict[str, dict[str, str]] = {
    "docs.projects": {"zh-Hans": "Synwevo Docs 项目与来源", "zh-Hant": "Synwevo Docs 專案與來源",
                      "en": "Synwevo Docs projects and sources"},
    "docs.templates": {"zh-Hans": "模板、字段与映射", "zh-Hant": "範本、欄位與對應", "en": "Templates, fields and mappings"},
    "docs.sharing": {"zh-Hans": "共享模板、评论与跨空间复制", "zh-Hant": "共享範本、評論與跨空間複製",
                     "en": "Shared templates, comments and cross-workspace copies"},
    "docs.jobs": {"zh-Hans": "生成任务与下载链接", "zh-Hant": "產生任務與下載連結", "en": "Generation jobs and download links"},
    "docs.uploads": {"zh-Hans": "续传上传与回归用例", "zh-Hant": "續傳上傳與回歸案例", "en": "Resumable uploads and regression cases"},
    "docs.webhooks": {"zh-Hans": "Docs 的 Webhook 订阅", "zh-Hant": "Docs 的 Webhook 訂閱", "en": "Docs webhook subscriptions"},
    "suite.forms": {"zh-Hans": "Forms", "zh-Hant": "Forms", "en": "Forms"},
    "suite.tables": {"zh-Hans": "Tables", "zh-Hant": "Tables", "en": "Tables"},
    "suite.dataprep": {"zh-Hans": "DataPrep", "zh-Hant": "DataPrep", "en": "DataPrep"},
    "suite.extract": {"zh-Hans": "Extract", "zh-Hant": "Extract", "en": "Extract"},
    "suite.approvals": {"zh-Hans": "Approvals", "zh-Hant": "Approvals", "en": "Approvals"},
    "suite.agent": {"zh-Hans": "个人 Agent 文件与自动化", "zh-Hant": "個人 Agent 檔案與自動化",
                    "en": "Personal Agent files and automations"},
    "suite.webhooks": {"zh-Hans": "工作空间应用的 Webhook 订阅", "zh-Hant": "工作空間應用的 Webhook 訂閱",
                       "en": "Workplace application webhook subscriptions"},
}


def _a(scope: str, hans: str, hant: str, en: str) -> dict:
    return {"scope": scope, "label": {"zh-Hans": hans, "zh-Hant": hant, "en": en}}


APP_ACTIONS: dict[str, dict] = {
    # Synwevo Docs: projects and sources
    "docs.project.create": _a("docs.projects", "创建项目", "建立專案", "Create a project"),
    "docs.project.update": _a("docs.projects", "修改项目", "修改專案", "Update a project"),
    "docs.project.delete": _a("docs.projects", "删除项目", "刪除專案", "Delete a project"),
    "docs.project.import": _a("docs.projects", "导入项目定义", "匯入專案定義", "Import a project definition"),
    "docs.source.add": _a("docs.projects", "添加来源文件", "新增來源檔案", "Add a source file"),
    "docs.source.remove": _a("docs.projects", "移除来源文件", "移除來源檔案", "Remove a source file"),
    "docs.source.reorder": _a("docs.projects", "调整来源顺序", "調整來源順序", "Reorder the sources"),
    "docs.source.representative": _a("docs.projects", "选定代表样本", "選定代表樣本", "Choose the representative sample"),
    "docs.source.options": _a("docs.projects", "修改解析选项并重新解析", "修改解析選項並重新解析", "Change the parse options and reparse"),
    "docs.source.desensitize": _a("docs.projects", "生成脱敏副本", "產生去識別化副本", "Make desensitized copies"),
    "docs.source.ocr": _a("docs.projects", "运行文字识别", "執行文字辨識", "Run text recognition"),
    "docs.source.correction": _a("docs.projects", "修正识别值", "修正辨識值", "Correct a recognised value"),
    "docs.source.scan.retry": _a("docs.projects", "重试文件安全扫描", "重試檔案安全掃描", "Retry the file safety scan"),
    # Synwevo Docs: templates, fields and mappings
    "docs.template.upload": _a("docs.templates", "上传模板", "上傳範本", "Upload a template"),
    "docs.template.create": _a("docs.templates", "创建模板", "建立範本", "Create a template"),
    "docs.template.version": _a("docs.templates", "保存模板新版本", "儲存範本新版本", "Save a new template version"),
    "docs.template.restore": _a("docs.templates", "恢复模板版本", "還原範本版本", "Restore a template version"),
    "docs.template.undo": _a("docs.templates", "撤销模板修改", "復原範本修改", "Undo a template change"),
    "docs.template.redo": _a("docs.templates", "重做模板修改", "重做範本修改", "Redo a template change"),
    "docs.template.rename": _a("docs.templates", "重命名模板", "重新命名範本", "Rename a template"),
    "docs.template.delete": _a("docs.templates", "删除模板", "刪除範本", "Delete a template"),
    "docs.template.duplicate": _a("docs.templates", "复制模板", "複製範本", "Duplicate a template"),
    "docs.template.fields.import": _a("docs.templates", "从定义导入字段", "從定義匯入欄位", "Import fields from a definition"),
    "docs.mappings.save": _a("docs.templates", "保存映射", "儲存對應", "Save the mappings"),
    "docs.mappings.undo": _a("docs.templates", "撤销映射修改", "復原對應修改", "Undo a mapping change"),
    "docs.mappings.redo": _a("docs.templates", "重做映射修改", "重做對應修改", "Redo a mapping change"),
    # Synwevo Docs: shared templates, comments and cross-workspace copies
    "docs.shared_template.publish": _a("docs.sharing", "发布共享模板", "發布共享範本", "Publish a shared template"),
    "docs.shared_template.release": _a("docs.sharing", "发布共享模板新版本", "發布共享範本新版本", "Publish a new shared-template release"),
    "docs.shared_template.deprecate": _a("docs.sharing", "弃用共享模板", "棄用共享範本", "Deprecate a shared template"),
    "docs.shared_template.restore": _a("docs.sharing", "恢复共享模板", "恢復共享範本", "Restore a shared template"),
    "docs.shared_template.use": _a("docs.sharing", "使用共享模板", "使用共享範本", "Use a shared template"),
    "docs.comment.create": _a("docs.sharing", "发表评论", "發表評論", "Post a comment"),
    "docs.comment.edit": _a("docs.sharing", "编辑评论", "編輯評論", "Edit a comment"),
    "docs.comment.delete": _a("docs.sharing", "删除评论", "刪除評論", "Delete a comment"),
    "docs.comment.resolve": _a("docs.sharing", "解决评论", "解決評論", "Resolve a comment"),
    "docs.comment.reopen": _a("docs.sharing", "重新打开评论", "重新開啟評論", "Reopen a comment"),
    "docs.copy.request": _a("docs.sharing", "发起跨空间复制请求", "發起跨空間複製請求", "Request a cross-workspace copy"),
    "docs.copy.decide": _a("docs.sharing", "批准或拒绝跨空间复制", "核准或拒絕跨空間複製", "Approve or refuse a cross-workspace copy"),
    "docs.copy.withdraw": _a("docs.sharing", "撤回跨空间复制请求", "撤回跨空間複製請求", "Withdraw a cross-workspace copy request"),
    # Synwevo Docs: generation jobs and download links
    "docs.job.create": _a("docs.jobs", "创建生成任务", "建立產生任務", "Create a generation job"),
    "docs.job.cancel": _a("docs.jobs", "取消生成任务", "取消產生任務", "Cancel a generation job"),
    "docs.job.retry": _a("docs.jobs", "重试生成任务", "重試產生任務", "Retry a generation job"),
    "docs.job.archive_link": _a("docs.jobs", "签发任务打包下载链接", "簽發任務打包下載連結", "Issue a job archive download link"),
    "docs.artifact.link": _a("docs.jobs", "签发产物下载链接", "簽發產物下載連結", "Issue an artifact download link"),
    # Synwevo Docs: resumable uploads and regression cases
    "docs.upload.create": _a("docs.uploads", "开始续传上传", "開始續傳上傳", "Start a resumable upload"),
    "docs.upload.complete": _a("docs.uploads", "完成续传上传", "完成續傳上傳", "Complete a resumable upload"),
    "docs.upload.abort": _a("docs.uploads", "中止续传上传", "中止續傳上傳", "Abort a resumable upload"),
    "docs.upload.consume": _a("docs.uploads", "将上传转为来源或模板", "將上傳轉為來源或範本", "Turn an upload into a source or template"),
    "docs.regression_case.create": _a("docs.uploads", "创建回归用例", "建立回歸案例", "Create a regression case"),
    "docs.regression_case.delete": _a("docs.uploads", "删除回归用例", "刪除回歸案例", "Delete a regression case"),
    # Synwevo Docs: webhook subscriptions
    "docs.webhook.create": _a("docs.webhooks", "创建 Webhook 订阅", "建立 Webhook 訂閱", "Create a webhook subscription"),
    "docs.webhook.update": _a("docs.webhooks", "修改 Webhook 订阅", "修改 Webhook 訂閱", "Update a webhook subscription"),
    "docs.webhook.delete": _a("docs.webhooks", "删除 Webhook 订阅", "刪除 Webhook 訂閱", "Delete a webhook subscription"),
    "docs.webhook.pause": _a("docs.webhooks", "暂停 Webhook 订阅", "暫停 Webhook 訂閱", "Pause a webhook subscription"),
    "docs.webhook.resume": _a("docs.webhooks", "恢复 Webhook 订阅", "恢復 Webhook 訂閱", "Resume a webhook subscription"),
    "docs.webhook.rotate_secret": _a("docs.webhooks", "轮换 Webhook 密钥", "輪換 Webhook 密鑰", "Rotate a webhook secret"),
    "docs.webhook.ping": _a("docs.webhooks", "发送 Webhook 测试事件", "傳送 Webhook 測試事件", "Send a webhook test event"),
    "docs.webhook.redeliver": _a("docs.webhooks", "重新投递 Webhook 事件", "重新投遞 Webhook 事件", "Queue a webhook delivery again"),
    "docs.webhook.disable": _a("docs.webhooks", "调度器停用订阅", "調度器停用訂閱", "Dispatcher disables a subscription"),
    # Forms
    "suite.forms.download": _a("suite.forms", "下载表单文件", "下載表單檔案", "Download a form file"),
    "suite.forms.file.upload": _a("suite.forms", "上传表单附件", "上傳表單附件", "Upload a form attachment"),
    "suite.forms.file.discard": _a("suite.forms", "丢弃表单附件", "捨棄表單附件", "Discard a form attachment"),
    "suite.forms.submission.create": _a("suite.forms", "提交表单（填写人）", "提交表單（填寫人）", "Submit a form (filler)"),
    "suite.forms.submission.resubmit": _a("suite.forms", "重新提交表单", "重新提交表單", "Resubmit a form"),
    "suite.forms.request.expire": _a("suite.forms", "填写请求到期", "填寫請求到期", "Expire a fill request"),
    # Tables
    "suite.tables.upload": _a("suite.tables", "上传表格文件", "上傳表格檔案", "Upload a table file"),
    "suite.tables.download": _a("suite.tables", "下载表格文件", "下載表格檔案", "Download a table file"),
    # DataPrep
    "suite.dataprep.upload": _a("suite.dataprep", "上传数据文件", "上傳資料檔案", "Upload a data file"),
    "suite.dataprep.export": _a("suite.dataprep", "导出数据集或问题清单", "匯出資料集或問題清單", "Export a dataset or a run's issues"),
    # Extract
    "suite.extract.upload": _a("suite.extract", "上传待抽取文件", "上傳待擷取檔案", "Upload a file for extraction"),
    "suite.extract.download": _a("suite.extract", "下载抽取文件", "下載擷取檔案", "Download an extraction file"),
    "suite.extract.export": _a("suite.extract", "导出抽取结果", "匯出擷取結果", "Export a task's results"),
    # Approvals
    "suite.approvals.stage.reminder": _a("suite.approvals", "发送审批环节提醒", "傳送審批環節提醒", "Send a stage reminder"),
    "suite.approvals.stage.escalated": _a("suite.approvals", "引擎升级逾期环节", "引擎升級逾期環節", "Engine escalates an overdue stage"),
    # Personal Agent files and automations
    "suite.agent.file.upload": _a("suite.agent", "上传 Agent 文件", "上傳 Agent 檔案", "Upload an Agent file"),
    "suite.agent.file.download": _a("suite.agent", "下载 Agent 文件", "下載 Agent 檔案", "Download an Agent file"),
    "suite.agent.flow.trigger": _a("suite.agent", "触发器启动自动化", "觸發器啟動自動化", "Trigger starts an automation"),
    # The Agent's connections to external MCP servers
    "suite.agent.mcp.connection.create": _a("suite.agent", "添加 MCP 连接", "新增 MCP 連線", "Add an MCP connection"),
    "suite.agent.mcp.connection.update": _a("suite.agent", "修改 MCP 连接", "修改 MCP 連線", "Update an MCP connection"),
    "suite.agent.mcp.connection.delete": _a("suite.agent", "删除 MCP 连接", "刪除 MCP 連線", "Delete an MCP connection"),
    "suite.agent.mcp.connection.enable": _a("suite.agent", "启用 MCP 连接", "啟用 MCP 連線", "Enable an MCP connection"),
    "suite.agent.mcp.connection.disable": _a("suite.agent", "停用 MCP 连接", "停用 MCP 連線", "Disable an MCP connection"),
    "suite.agent.mcp.credential.revoke": _a("suite.agent", "撤销 MCP 连接凭据", "撤銷 MCP 連線憑據", "Revoke an MCP connection credential"),
    "suite.agent.mcp.grant.set": _a("suite.agent", "设置 MCP 工具授权", "設定 MCP 工具授權", "Set an MCP tool grant"),
    "suite.agent.mcp.binding.set": _a("suite.agent", "设置 Agent 使用的 MCP 连接", "設定 Agent 使用的 MCP 連線", "Set which MCP connection the Agent uses"),
    "suite.agent.mcp.oauth.start": _a("suite.agent", "开始 MCP 连接的认证", "開始 MCP 連線的認證", "Start an MCP connection authorization"),
    "suite.agent.mcp.oauth.complete": _a("suite.agent", "完成 MCP 连接的认证", "完成 MCP 連線的認證", "Complete an MCP connection authorization"),
    # Workplace application webhook subscriptions
    "suite.webhook.create": _a("suite.webhooks", "创建 Webhook 订阅", "建立 Webhook 訂閱", "Create a webhook subscription"),
    "suite.webhook.update": _a("suite.webhooks", "修改 Webhook 订阅", "修改 Webhook 訂閱", "Update a webhook subscription"),
    "suite.webhook.delete": _a("suite.webhooks", "删除 Webhook 订阅", "刪除 Webhook 訂閱", "Delete a webhook subscription"),
    "suite.webhook.pause": _a("suite.webhooks", "暂停 Webhook 订阅", "暫停 Webhook 訂閱", "Pause a webhook subscription"),
    "suite.webhook.resume": _a("suite.webhooks", "恢复 Webhook 订阅", "恢復 Webhook 訂閱", "Resume a webhook subscription"),
    "suite.webhook.rotate_secret": _a("suite.webhooks", "轮换 Webhook 密钥", "輪換 Webhook 密鑰", "Rotate a webhook secret"),
    "suite.webhook.ping": _a("suite.webhooks", "发送 Webhook 测试事件", "傳送 Webhook 測試事件", "Send a webhook test event"),
    "suite.webhook.redeliver": _a("suite.webhooks", "重新投递 Webhook 事件", "重新投遞 Webhook 事件", "Queue a webhook delivery again"),
    "suite.webhook.disable": _a("suite.webhooks", "调度器停用订阅", "調度器停用訂閱", "Dispatcher disables a subscription"),
}


def catalogue() -> dict:
    """What the audit page offers: every application action with its scope and labels, sorted by code."""
    return {"items": [{"code": code, **item} for code, item in sorted(APP_ACTIONS.items())],
            "scopes": [{"id": sid, "label": label} for sid, label in APP_SCOPES.items()]}
