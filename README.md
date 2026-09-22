# DocWeave · 可视化数据映射与模板批量生成工作台

左侧查看来源文件并选择数据，右侧查看目标模板并选择待填区域，建立多组可复用的对应关系；按明确的样本划分方式（每行 / 每文件 / 每组一份）把数据填入模板，批量生成真实的 DOCX / XLSX / PDF / PPTX / CSV 文件。

* 所有处理在本机或自有服务器完成，不向外部服务发送文件；无需任何付费组件。
* 浏览器应用（简体中文界面），处理引擎与界面分离（`backend/docweave/engine` 不依赖 Web 框架）。
* 样本划分、字段映射、输出生成三者分开设计：输出数量只由样本规则决定，不根据映射数量推断，不做笛卡尔积。

![工作台](docs/screenshots/03_映射与连线.png)

## 快速开始

```bash
# 可选系统工具：LibreOffice（旧格式转换、PDF 导出、公式计算）与 Tesseract（OCR）
brew install --cask libreoffice && brew install tesseract      # macOS；Linux 见 docs/DEPLOY.md

scripts/setup.sh      # Python 依赖、界面构建、中文 OCR 模型
scripts/start.sh      # 打开 http://127.0.0.1:8765
```

需要程序调用（无界面）时：

```bash
backend/.venv/bin/python backend/manage.py apikey create --name ci --scopes read,write   # 生成密钥（只显示一次）
export DOCWEAVE_API_KEY=dw_...  DOCWEAVE_BASE_URL=http://127.0.0.1:8765
backend/.venv/bin/python scripts/automation_example.py \
  --source testdata/测试_客户100.xlsx --template testdata/测试_合同模板.docx \
  --definition docs/integration/example-definition.json --out ./out
```

接口与端口文档：[docs/integration/api.md](docs/integration/api.md)、[docs/integration/ports.md](docs/integration/ports.md)。

Docker：`docker compose up -d --build`（镜像内含 LibreOffice、Tesseract 中文模型与中文字体；对外服务请加带认证的反向代理，见 `docs/DEPLOY.md`）。

## 已实现的用户流程

1. **项目与来源**：拖放/多选上传、排序、移除、代表文件；按内容识别真实格式；损坏、加密、不支持的文件显示具体原因；完整查看（表格分页加载全部行、文档全文、PDF 全部页、幻灯片、文本）。
2. **模板**：上传 DOCX/XLSX/PDF/PPTX（旧格式经转换）后在预览上选中内容「设为待填区域」（替换或插入，跨格式片段的文字、单元格、区域、重复明细行、页眉页脚、PDF 框选并真正删除原值、PDF 表单字段）；或用内置**文档型**（格式、表格、合并、图片、纸张页边距、分页、页眉页脚、字段、重复行）与**表格型**（单元格样式、合并、行高列宽、数字格式、字段/区域/重复行）编辑器创建；模板版本化、撤销/重做、复制、模板库。
3. **样本规则**：每行一份 / 每文件一份 / 每组一份（分组字段、组内排序、跨文件开关、冲突报告）；表头行、数据范围、空行排除与计数、过滤条件；确认前显示样本清单与数量；「选中区域的含义」明确 10 行是 10 份还是 1 份。
4. **映射**：单值、固定区域、重复明细三种类型；「随样本变化」与「固定值」明确区分；按列名绑定（列重排不受影响，重名列需消歧）；一对多、多来源显式组合；受控转换；编号+颜色+连线、双侧定位、编辑/禁用/删除/撤销；跨文件校验与单文件重新定位；OCR 值确认与修正。
5. **预览与生成**：真实预览（与生成同一路径）、上一条/下一条/按标识查找/问题定位；生成前统计与错误策略；后台任务、真实进度、取消、失败重试、服务重启后续跑；每个输出重新打开逐值核对；单个下载、ZIP、结果清单 CSV；文件命名可组合字段并安全去重，不覆盖。

## 文档

| 文档 | 内容 |
|---|---|
| [docs/USER_GUIDE.md](docs/USER_GUIDE.md) | 操作说明 |
| [docs/FORMATS.md](docs/FORMATS.md) | **格式能力矩阵**（逐项验证）、各格式规则与缺口 |
| [docs/ACCEPTANCE.md](docs/ACCEPTANCE.md) | 14 项验收场景结果、Office 实机打开检查、性能实测 |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | 架构、数据模型、定位规则、任务与安全 |
| [docs/DEPLOY.md](docs/DEPLOY.md) | 运行、Docker、反向代理、限制与升级 |
| [docs/DEPENDENCIES.md](docs/DEPENDENCIES.md) | 组件、版本、许可证（含 PyMuPDF AGPL 说明）、费用与部署要求 |
| [docs/TESTING.md](docs/TESTING.md) | 测试数据、自动化测试、浏览器端到端测试、性能测试 |
| [docs/integration/ports.md](docs/integration/ports.md) | **端口、绑定地址、健康检查**（实测）与反向代理配置 |
| [docs/integration/api.md](docs/integration/api.md) | **机器接口 `/api/v1`**：鉴权、路由、任务语义、幂等、事件与 Webhook |
| [docs/integration/automation.md](docs/integration/automation.md) | 无界面调用完整流程、可运行脚本与 curl 示例 |
| [docs/integration/openapi.yaml](docs/integration/openapi.yaml) | 机器可读接口规范（由代码导出，测试校验一致） |
| [docs/operations.md](docs/operations.md) | 生产启动、备份恢复、迁移与回滚、故障处理 |
| [docs/quality/issues.md](docs/quality/issues.md) | 问题清单与修复证据 |
| [docs/quality/verification.md](docs/quality/verification.md) | 实际执行的验证命令与结果 |
| [docs/design.md](docs/design.md) | 设计规范、参考来源、交互与动效 |
| `.env.example` | 所有配置项与上限 |

## 验证结果摘要

* `pytest`：**172 项通过、1 项跳过**（引擎单元测试、14 项验收场景、格式覆盖、任务可靠性、机器接口、Webhook、编辑流程、能力声明佐证）。
* 浏览器端到端（Playwright 驱动本机 Chrome）：完整流程通过。
* 无界面调用：按 `docs/integration/automation.md` 从干净环境跑通上传 → 模板 → 规则 → 预检 → 提交 → 下载 → 校验。
* Microsoft Word / Excel / PowerPoint 实机打开生成文件：无修复提示。
* 性能（Apple Silicon 笔记本，合成数据）：1 000 份合同 11.1 秒；5 000 份 65 秒（峰值 RSS 452 MB）；200 份分组 XLSX 1.0 秒；300 份 PDF 模板 23 秒；双格式（DOCX+PDF）约 355 ms/份。
* 完整命令、结果与环境见 [docs/quality/verification.md](docs/quality/verification.md)；问题与修复证据见 [docs/quality/issues.md](docs/quality/issues.md)。

## 工作假设（未收到补充信息时的默认方案）

* 使用方式：电脑浏览器，单人或小团队自托管；未提供真实样本文件，全部使用合成测试数据验收。**拿到脱敏的真实文件后应补充回归测试。**
* 默认同一批来源文件结构相同，但会逐文件、逐映射校验，不相同处明确报错。
* 文件全程本地处理；未使用商业组件。PyMuPDF 为 AGPL（或商业许可）——对外提供服务前请确认许可证义务（见 `docs/DEPENDENCIES.md`）。

## 已知限制

* 公式：内置引擎计算 90+ 常用函数并写入结果，不支持的函数改用 LibreOffice 计算；同时设置 `fullCalcOnLoad`，Excel 打开时仍会重算。**不声称与 Excel 逐函数一致**，差异见 `docs/FORMATS.md`。
* 更长的新内容可能改变 DOCX/PPTX 的分页或排版，不承诺像素级不变；演示文稿不会自动分页（超出时警告）。
* PDF 不能转换为可编辑 Word；表单字段输出为扁平化文字。
* 图片模板的字段区域按周围背景重建像素，非字段区域不变，但不保证与原图逐像素一致。
* 界面为单团队自托管定位：界面鉴权支持 `basic` 或交由反向代理（`DOCWEAVE_UI_AUTH`），机器接口有独立的 API 密钥与租户隔离；不提供面向终端用户的注册/角色体系。
* 大批量为单进程串行处理；PDF 导出受 LibreOffice 串行转换速度限制。
* 全部验收使用合成测试数据；未获得真实业务文件，真实结构差异未覆盖。

## 目录

```
backend/            Python 服务端（FastAPI）与处理引擎
  docweave/engine/  与 Web 无关的格式适配器、样本/映射/计划引擎
  tests/            pytest（单元 + 验收）
  tessdata/         Tesseract 中文/英文模型
frontend/           React 界面（Vite 构建为静态文件，由服务端提供）
scripts/            安装/启动、测试数据生成、浏览器端到端测试、性能测试
testdata/           合成测试数据（生成）
docs/               文档与截图
```
