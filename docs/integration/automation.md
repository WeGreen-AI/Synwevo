# 自动化调用指南（完整流程，可直接运行）

本文的每条命令都在本机实跑过（记录见 [../quality/verification.md](../quality/verification.md)）。示例输入使用 `testdata/` 中的**合成测试文件**（名称以「测试」开头），输出由真实服务生成。

## 0. 准备：启动服务并创建密钥

```bash
# 1) 启动（默认 127.0.0.1:8765，端口与绑定见 docs/integration/ports.md）
backend/.venv/bin/python backend/run.py

# 2) 生成测试数据（可选，仅用于示例）
backend/.venv/bin/python scripts/make_fixtures.py

# 3) 创建 API 密钥（只显示一次）
backend/.venv/bin/python backend/manage.py apikey create --name ci --scopes read,write
export DOCWEAVE_API_KEY=dw_xxxxxxxx_yyyyyyyyyyyyyyyyyyyy
export DOCWEAVE_BASE_URL=http://127.0.0.1:8765
```

## 1. 一条命令跑完整流程

```bash
backend/.venv/bin/python scripts/automation_example.py \
  --source testdata/测试_客户100.xlsx \
  --template testdata/测试_合同模板.docx \
  --definition docs/integration/example-definition.json \
  --naming "{客户编号}_{客户名称}" \
  --out ./out
```

脚本依次执行：能力查询 → 创建项目 → 上传来源并等待解析 → 上传模板 → 按定义创建字段 → 绑定模板 → 保存规则 → 保存映射 →
预检 → 提交任务（带幂等键）→ 轮询事件直到终态 → 下载每个产物并逐一校验 SHA-256 → 下载 ZIP 与生成清单。

退出码：`0` 成功；`2` 预检发现错误样本；`3` 任务未全部成功；`4` 下载校验失败；`1` 其它错误。
常用参数：`--formats docx,pdf`、`--retry-failed`、`--idempotency-key <键>`、`--template-id <已有模板>`（复用界面里做好的模板）、`--timeout 1800`。

实跑输出（2026-09-23，本机）：

```
服务 http://127.0.0.1:8765 就绪：接口版本 1.0，LibreOffice=有，PDF 引擎=pymupdf
项目 p_6ad2322f352a（自动化任务）
  来源 s_e9522402d83d  测试_客户100.xlsx  xlsx  ready
模板 t_7530dd37b127（测试_合同模板，docx）
  已创建字段：客户名称, 客户编号, 签约日期, 金额
样本规则：识别 10 条样本
映射：4 条（rev 1）
预检：样本 10，有效 10，错误 0，警告 0，预计输出 10 个文件
任务 j_f3c091735c20（幂等键 auto-7236ae37e35c）
  · job.created / job.started / job.completed 成功 10/10
任务结束：completed；样本 10，成功 10，失败 0，跳过 0，取消 0，输出文件 10
  ✓ 000001_测试钱磊敏.docx  39563 字节  sha256=725d32149c77…
```

## 2. 定义文件

`--definition` 指向一个 JSON（示例：[example-definition.json](example-definition.json)）：

```json
{
  "fields":  [ { "kind": "text", "name": "客户名称", "find": "客户名称" } ],
  "rule":    { "mode": "per_row", "table": { "sheet": "客户", "header_row": 0, "data_end": 10 } },
  "mappings":[ { "field": "客户名称", "type": "single",
                 "sources": [ { "kind": "column", "sheet": "客户", "header": "姓名" } ] } ]
}
```

* `fields` 会调用 `POST /api/v1/templates/{tid}/fields`（字段类型与选择方式见 [api.md](api.md#33-模板与字段定义)）。
* `mappings[].field` 是模板字段名，脚本会换成该字段的 `binding` 再提交。
* 已经在界面里做好模板和映射时，可改用 `GET /api/v1/projects/{pid}/definition` 导出，再 `PUT` 到新项目复用（见第 5 节）。

## 3. curl 版（最短路径）

```bash
H="Authorization: Bearer $DOCWEAVE_API_KEY"
B=$DOCWEAVE_BASE_URL/api/v1

PID=$(curl -s -X POST "$B/projects" -H "$H" -H 'Content-Type: application/json' \
      -d '{"name":"curl 演示"}' | python3 -c 'import sys,json;print(json.load(sys.stdin)["id"])')

curl -s -X POST "$B/projects/$PID/files" -H "$H" -F "file=@testdata/测试_客户100.xlsx" > /tmp/f.json
FID=$(python3 -c 'import json;print(json.load(open("/tmp/f.json"))["id"])')
until [ "$(curl -s "$B/files/$FID" -H "$H" | python3 -c 'import sys,json;print(json.load(sys.stdin)["status"])')" != "parsing" ]; do sleep 0.5; done

curl -s -X POST "$B/templates" -H "$H" -F "file=@testdata/测试_合同模板.docx" > /tmp/t.json
TID=$(python3 -c 'import json;print(json.load(open("/tmp/t.json"))["id"])')
curl -s -X POST "$B/templates/$TID/fields" -H "$H" -H 'Content-Type: application/json' \
     -d '{"schema":"docweave.template-fields","version":1,
          "fields":[{"kind":"text","name":"客户名称","find":"客户名称"}]}' > /dev/null
curl -s -X PUT "$B/projects/$PID/template" -H "$H" -H 'Content-Type: application/json' -d "{\"template_id\":\"$TID\"}" > /dev/null
curl -s -X PUT "$B/projects/$PID/rule" -H "$H" -H 'Content-Type: application/json' \
     -d '{"rule":{"mode":"per_row","table":{"sheet":"客户","header_row":0,"data_end":5}}}'

BINDING=$(curl -s "$B/templates/$TID" -H "$H" | python3 -c 'import sys,json;print(json.load(sys.stdin)["fields"][0]["binding"])')
curl -s -X PUT "$B/projects/$PID/mappings" -H "$H" -H 'Content-Type: application/json' \
     -d "{\"mappings\":[{\"type\":\"single\",\"sources\":[{\"kind\":\"column\",\"sheet\":\"客户\",\"header\":\"姓名\"}],\"target\":{\"binding\":\"$BINDING\"}}]}" > /dev/null

curl -s -X POST "$B/projects/$PID/preflight" -H "$H" -H 'Content-Type: application/json' -d '{}'
JID=$(curl -s -X POST "$B/projects/$PID/jobs" -H "$H" -H 'Content-Type: application/json' \
      -H "Idempotency-Key: demo-001" -d '{}' | python3 -c 'import sys,json;print(json.load(sys.stdin)["id"])')
until curl -s "$B/jobs/$JID" -H "$H" | grep -qE '"status":"(completed|completed_with_errors|failed|cancelled)"'; do sleep 1; done
curl -s "$B/jobs/$JID/artifacts" -H "$H"
curl -s -L -o out.zip "$B/jobs/$JID/archive" -H "$H"
```

## 4. 常见任务

**复用已有模板 / 规则**
```bash
curl -s "$B/projects/$OLD/definition" -H "$H" > def.json          # 导出（模板绑定 + 规则 + 映射）
curl -s -X PUT "$B/projects/$NEW/definition" -H "$H" -H 'Content-Type: application/json' -d @def.json
```
定义里带模板版本；若目标环境的模板缺少定义中的字段，导入会明确报错而不是悄悄生成半成品。

**只重试失败的样本**
```bash
curl -s -X POST "$B/jobs/$JID/retry" -H "$H" -H "Idempotency-Key: $JID-retry-1" \
     -H 'Content-Type: application/json' -d '{"include_skipped": false}'
curl -s "$B/jobs/$JID/runs" -H "$H"        # 初次与每次重试的记录
```
重试沿用原任务快照。换了模板或规则就不要重试，应当新建任务重新预检。

**链接过期后重新获取**
```bash
LINK=$(curl -s -X POST "$B/artifacts/$AID/link" -H "$H" | python3 -c 'import sys,json;print(json.load(sys.stdin)["url"])')
curl -s -o file.docx "$LINK"       # 链接本身即凭据，默认 15 分钟
# 过期时返回 410 link_expired：再调一次 /link 即可
```

**校验 Webhook 签名（接收端）**
```python
import hmac, hashlib, time

def verify(body: bytes, headers, secret: str, tolerance=300) -> bool:
    ts = headers.get("X-DocWeave-Timestamp", "")
    sig = headers.get("X-DocWeave-Signature", "")
    if abs(time.time() - int(ts)) > tolerance:          # 防重放
        return False
    expect = "v1=" + hmac.new(secret.encode(), f"{ts}.".encode() + body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expect, sig)
# 再按 X-DocWeave-Event-Id 去重（投递可能重复、可能乱序）
```
`backend/docweave/webhooks.py` 里的 `verify()` 就是这段逻辑，`backend/tests/test_webhooks.py` 用它验证真实投递。

**等待任务：轮询事件**
```bash
AFTER=0
while :; do
  R=$(curl -s "$B/jobs/$JID/events?after=$AFTER" -H "$H")
  AFTER=$(python3 -c 'import sys,json;print(json.load(sys.stdin)["next_after"])' <<< "$R")
  python3 -c 'import sys,json;[print(e["type"]) for e in json.load(sys.stdin)["items"]]' <<< "$R"
  curl -s "$B/jobs/$JID" -H "$H" | grep -qE '"status":"(completed|completed_with_errors|failed|cancelled)"' && break
  sleep 2
done
```

## 5. 与界面配合

* 在界面里做模板、字段、样本规则和映射 → `GET /projects/{pid}/definition` 导出 → 交给流水线按定义在新项目上复用。
* 界面与接口的任务、产物、清单共享同一套存储：界面里能看到自动化提交的任务（同一租户），也能下载其产物。
* 界面中不会显示密钥、Webhook 或端口配置；这些只通过 `backend/manage.py` 与环境变量管理。
