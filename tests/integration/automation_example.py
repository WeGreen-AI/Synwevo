#!/usr/bin/env python3
"""End-to-end Synwevo Docs automation without a browser: upload → fields/rule/mappings → preflight
→ submit → wait → download → verify.

    export SYNWEVO_DOCS_API_KEY=dw_xxx_yyy
    python scripts/automation_example.py --base-url http://127.0.0.1:8765 \
        --source testdata/测试_客户100.xlsx --template testdata/测试_合同模板.docx \
        --definition docs/integration/example-definition.json --out ./out

Every id comes from a real response; nothing is hard-coded.  Exit codes: 0 success,
2 preflight found errors, 3 job did not end successfully, 4 downloaded file failed its
checksum, 1 other failures.  Only the standard library is used.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import mimetypes
import os

from synwevo_core import env
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

TERMINAL = ("completed", "completed_with_errors", "failed", "cancelled")


class Api:
    def __init__(self, base_url: str, key: str, timeout: float = 120.0):
        self.base = base_url.rstrip("/")
        self.key = key
        self.timeout = timeout

    def _request(self, method: str, path: str, *, json_body=None, files=None, headers=None, raw=False):
        url = f"{self.base}{path}"
        h = {"Authorization": f"Bearer {self.key}", "Accept": "application/json", **(headers or {})}
        data = None
        if files:
            boundary = uuid.uuid4().hex
            parts = []
            for field, (filename, content) in files.items():
                ctype = mimetypes.guess_type(filename)[0] or "application/octet-stream"
                parts.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{field}\"; filename=\"{filename}\"\r\n"
                             f"Content-Type: {ctype}\r\n\r\n".encode() + content + b"\r\n")
            data = b"".join(parts) + f"--{boundary}--\r\n".encode()
            h["Content-Type"] = f"multipart/form-data; boundary={boundary}"
        elif json_body is not None:
            data = json.dumps(json_body, ensure_ascii=False).encode()
            h["Content-Type"] = "application/json; charset=utf-8"
        req = urllib.request.Request(url, data=data, method=method, headers=h)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                body = resp.read()
                if raw:
                    return body, dict(resp.headers)
                return json.loads(body) if body else {}
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")
            try:
                err = json.loads(detail).get("error", {})
                msg = f"{err.get('code')}: {err.get('message')} (request_id={err.get('request_id')})"
            except Exception:  # noqa: BLE001
                msg = detail[:300]
            raise SystemExit(f"[HTTP {e.code}] {method} {path}\n    {msg}") from e
        except urllib.error.URLError as e:
            raise SystemExit(f"无法连接 {url}：{e.reason}") from e

    def get(self, path, **kw):
        return self._request("GET", path, **kw)

    def post(self, path, json_body=None, **kw):
        return self._request("POST", path, json_body=json_body, **kw)

    def put(self, path, json_body=None, **kw):
        return self._request("PUT", path, json_body=json_body, **kw)


def fetch_service_token(issuer: str, client_id: str, client_secret: str, scopes: str,
                        *, quiet: bool = False) -> str:
    """Exchange a service account's credentials for a short-lived access token.

    The client credentials grant — no browser, no user, no cookie. `trust_env` is not a thing
    urllib does automatically here, but the proxy environment is still worth avoiding for a
    request carrying a client secret, so this builds an opener with no proxy handler.
    """
    body = urllib.parse.urlencode({"grant_type": "client_credentials", "client_id": client_id,
                                   "client_secret": client_secret, "scope": scopes}).encode()
    req = urllib.request.Request(
        issuer.rstrip("/") + "/oidc/token", data=body, method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded"})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(req, timeout=30) as resp:
            out = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        raise SystemExit(f"取令牌失败 [HTTP {e.code}]：{e.read()[:300].decode('utf-8', 'replace')}") from e
    except urllib.error.URLError as e:
        raise SystemExit(f"无法连接账户服务 {issuer}：{e.reason}") from e
    if not quiet:
        print(f"服务账户 {client_id} 已取得访问令牌（权限 {out.get('scope')}，"
              f"{out.get('expires_in')} 秒后过期）")
    return out["access_token"]


def upload(api: Api, path: str, where: str) -> dict:
    p = Path(path)
    if not p.is_file():
        raise SystemExit(f"找不到文件：{p}")
    return api.post(where, files={"file": (p.name, p.read_bytes())})


def wait_file_ready(api: Api, fid: str, timeout: float = 120) -> dict:
    t0 = time.time()
    while time.time() - t0 < timeout:
        f = api.get(f"/api/v1/files/{fid}")
        if f["status"] != "parsing":
            if f["status"] in ("error", "unsupported"):
                raise SystemExit(f"来源文件解析失败：{f.get('error')}")
            return f
        time.sleep(0.3)
    raise SystemExit("来源文件解析超时")


def wait_job(api: Api, jid: str, timeout: float, quiet: bool) -> dict:
    t0, after = time.time(), 0
    while time.time() - t0 < timeout:
        evs = api.get(f"/api/v1/jobs/{jid}/events?after={after}")
        for e in evs["items"]:
            if not quiet:
                counts = (e.get("data") or {}).get("counts") or {}
                extra = f" 成功 {counts.get('success', 0)}/{counts.get('total', '?')}" if counts else ""
                print(f"  · {e['type']}{extra}")
        after = evs["next_after"]
        job = api.get(f"/api/v1/jobs/{jid}")
        if job["status"] in TERMINAL:
            return job
        time.sleep(1.0)
    raise SystemExit("等待任务超时（可用 /api/v1/jobs/<id>/cancel 取消）")


def download_artifacts(api: Api, jid: str, out_dir: Path, quiet: bool) -> int:
    out_dir.mkdir(parents=True, exist_ok=True)
    arts = api.get(f"/api/v1/jobs/{jid}/artifacts?limit=2000")
    bad = 0
    for a in arts["items"]:
        body, headers = api.get(f"/api/v1/artifacts/{a['id']}/content", raw=True)
        target = out_dir / a["filename"]
        target.write_bytes(body)
        digest = hashlib.sha256(body).hexdigest()
        if digest != a["sha256"] or (headers.get("X-Content-SHA256") or digest) != digest:
            print(f"  ✖ 校验失败：{a['filename']}")
            bad += 1
        elif not quiet:
            print(f"  ✓ {a['filename']}  {len(body)} 字节  sha256={digest[:12]}…")
    zip_body, _ = api.get(f"/api/v1/jobs/{jid}/archive", raw=True)
    (out_dir / f"{jid}.zip").write_bytes(zip_body)
    report, _ = api.get(f"/api/v1/jobs/{jid}/report.csv", raw=True)
    (out_dir / f"{jid}-report.csv").write_bytes(report)
    return bad


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base-url", default=env.docs("BASE_URL", "http://127.0.0.1:8765"))
    ap.add_argument("--key", default=env.docs("API_KEY"),
                    help="API 密钥 dw_… 或平台访问令牌（或设置 SYNWEVO_DOCS_API_KEY）")
    # A machine identity on the platform: this fetches its own token instead of carrying a
    # long-lived key. The credential is the client secret, held by whatever runs this, and the
    # token it exchanges for lives fifteen minutes.
    ap.add_argument("--issuer", default=env.get("ISSUER"),
                    help="账户服务地址；与 --client-id/--client-secret 一起用于服务账户")
    ap.add_argument("--client-id", dest="client_id", default=env.get("CLIENT_ID"))
    ap.add_argument("--client-secret", dest="client_secret",
                    default=env.get("CLIENT_SECRET"))
    ap.add_argument("--api-scopes", dest="api_scopes", default="docweave.read docweave.write")
    ap.add_argument("--source", action="append", required=True, help="来源文件（可重复）")
    ap.add_argument("--template", help="模板文件；省略时用 --template-id 复用已有模板")
    ap.add_argument("--template-id")
    ap.add_argument("--definition", required=True, help="字段 + 规则 + 映射定义（JSON，见 docs/integration/automation.md）")
    ap.add_argument("--project-name", default="自动化任务")
    ap.add_argument("--formats", default="", help="输出格式，逗号分隔；默认用模板的原生格式")
    ap.add_argument("--naming", default=None, help="文件命名模板，例如 {客户编号}_{客户名称}")
    ap.add_argument("--out", default="./out")
    ap.add_argument("--idempotency-key", default=None, help="默认随机；重复使用同一个键不会重复生成")
    ap.add_argument("--retry-failed", action="store_true", help="任务结束后对失败样本发起一次重试")
    ap.add_argument("--timeout", type=float, default=1800)
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args()
    if not a.key and a.client_id and a.client_secret and a.issuer:
        a.key = fetch_service_token(a.issuer, a.client_id, a.client_secret, a.api_scopes,
                                    quiet=a.quiet)
    if not a.key:
        raise SystemExit(
            "缺少凭据。二选一：\n"
            "  · API 密钥：--key 或 SYNWEVO_DOCS_API_KEY（python backend/manage.py apikey create）\n"
            "  · 服务账户：--issuer --client-id --client-secret\n"
            "    （python backend/manage.py client register --service --workspace … --api-scopes …）")
    api = Api(a.base_url, a.key)
    definition = json.loads(Path(a.definition).read_text(encoding="utf-8"))

    caps = api.get("/api/v1/capabilities")
    if not a.quiet:
        print(f"服务 {a.base_url} 就绪：接口版本 {caps['api_version']}，"
              f"LibreOffice={'有' if caps['environment']['libreoffice'] else '无'}，PDF 引擎={caps['environment']['pdf_engine']}")

    project = api.post("/api/v1/projects", {"name": a.project_name})
    print(f"项目 {project['id']}（{project['name']}）")

    for src in a.source:
        f = upload(api, src, f"/api/v1/projects/{project['id']}/files")
        f = wait_file_ready(api, f["id"])
        print(f"  来源 {f['id']}  {f['filename']}  {f['format']}  {f['status']}")

    if a.template:
        tpl = upload(api, a.template, "/api/v1/templates")
        print(f"模板 {tpl['id']}（{tpl['name']}，{tpl['format']}）")
        if definition.get("fields"):
            made = api.post(f"/api/v1/templates/{tpl['id']}/fields",
                            {"schema": "docweave.template-fields", "version": 1, "fields": definition["fields"]})
            print(f"  已创建字段：{', '.join(f['name'] for f in made['created'])}")
        tid = tpl["id"]
    else:
        tid = a.template_id or raise_missing()
    api.put(f"/api/v1/projects/{project['id']}/template", {"template_id": tid})

    fields = {f["name"]: f for f in api.get(f"/api/v1/templates/{tid}")["fields"]}
    rule = api.put(f"/api/v1/projects/{project['id']}/rule", {"rule": definition["rule"]})
    print(f"样本规则：识别 {rule['records']} 条样本")
    mappings = []
    for m in definition["mappings"]:
        m = dict(m)
        target = m.pop("field", None)
        if target:
            if target not in fields:
                raise SystemExit(f"模板中没有字段「{target}」；现有字段：{', '.join(fields)}")
            m["target"] = {"binding": fields[target]["binding"]}
        mappings.append(m)
    saved = api.put(f"/api/v1/projects/{project['id']}/mappings", {"mappings": mappings})
    print(f"映射：{len(saved['mappings'])} 条（rev {saved['rev']}）")

    formats = [f for f in a.formats.split(",") if f.strip()]
    pre = api.post(f"/api/v1/projects/{project['id']}/preflight", {"formats": formats or None, "naming": a.naming})
    print(f"预检：样本 {pre['records']}，有效 {pre['valid']}，错误 {pre['invalid']}，警告 {pre['warnings']}，"
          f"预计输出 {pre['expected_outputs']} 个文件")
    for s in pre["invalid_samples"][:5]:
        print(f"  ✖ {s['record']}：{s['issues'][0]['message']}")
    if pre["invalid"]:
        print("预检发现错误样本：请修正映射或数据（也可以用 error_policy=skip 跳过）")
        return 2

    body = {"error_policy": "block"}
    if formats:
        body["formats"] = formats
    if a.naming:
        body["naming"] = a.naming
    idem = a.idempotency_key or f"auto-{uuid.uuid4().hex[:12]}"
    job = api.post(f"/api/v1/projects/{project['id']}/jobs", body, headers={"Idempotency-Key": idem})
    print(f"任务 {job['id']}（幂等键 {idem}）")
    job = wait_job(api, job["id"], a.timeout, a.quiet)
    c = job["counts"]
    print(f"任务结束：{job['status']}；样本 {c['samples']}，成功 {c['documents']}，失败 {c['failed']}，"
          f"跳过 {c['skipped']}，取消 {c['cancelled']}，输出文件 {c['files']}")

    if a.retry_failed and c["failed"]:
        job = api.post(f"/api/v1/jobs/{job['id']}/retry", {}, headers={"Idempotency-Key": f"{idem}-retry"})
        job = wait_job(api, job["id"], a.timeout, a.quiet)
        print(f"重试后：{job['status']}，成功 {job['counts']['documents']}")

    bad = download_artifacts(api, job["id"], Path(a.out), a.quiet)
    print(f"已下载到 {Path(a.out).resolve()}（ZIP 与生成清单同目录）")
    if bad:
        return 4
    return 0 if job["status"] == "completed" else 3


def raise_missing():
    raise SystemExit("需要 --template 或 --template-id")


if __name__ == "__main__":
    sys.exit(main())
