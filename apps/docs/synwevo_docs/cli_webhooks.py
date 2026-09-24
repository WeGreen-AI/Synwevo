"""命令行：Webhook 订阅与投递（运营方工具；产品界面里没有入口）。

    python -m synwevo_docs.cli webhooks list [--tenant 工作空间] [--json]
    python -m synwevo_docs.cli webhooks add --tenant 工作空间 --url https://… [--events job.completed,job.failed] [--description …]
    python -m synwevo_docs.cli webhooks show|pause|resume|delete <订阅ID>
    python -m synwevo_docs.cli webhooks update <订阅ID> [--url …] [--events …] [--description …]
    python -m synwevo_docs.cli webhooks rotate <订阅ID> [--overlap-s 86400]
    python -m synwevo_docs.cli webhooks ping <订阅ID>                 # 立即发一次签名的 webhook.ping
    python -m synwevo_docs.cli webhooks deliveries <订阅ID> [--status failed] [--limit 50] [--cursor …]
    python -m synwevo_docs.cli webhooks redeliver <订阅ID> <投递ID>
    python -m synwevo_docs.cli webhooks test --url https://…          # 用部署级密钥发一次签名的 webhook.ping

从命令行创建的订阅以运营方身份投递（与部署级 SYNWEVO_DOCS_WEBHOOK_URLS 同级）；工作空间被冻结或删除时
同样停止。订阅密钥只在 add / rotate 时显示一次，库里只保存加密后的值。

`register(subparsers)` 由 `synwevo_docs.cli` 调用，把这些命令挂到同一个入口下。
"""
from __future__ import annotations

import argparse
import json
import sys
import time

from synwevo_core import authority, env
from synwevo_core.errors import ServiceError
from synwevo_core.http import data_dir_default
from synwevo_core.store import Store

from . import webhooks as W

ACTIONS = ("list", "add", "show", "update", "pause", "resume", "delete", "rotate", "ping",
           "deliveries", "redeliver", "test")


def register(subparsers) -> argparse.ArgumentParser:
    w = subparsers.add_parser("webhooks", help="Webhook 订阅与投递（仅命令行与 API）",
                              description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    w.add_argument("action", choices=ACTIONS)
    w.add_argument("sid", nargs="?", help="订阅 ID")
    w.add_argument("delivery_id", nargs="?", help="投递 ID（redeliver）")
    w.add_argument("--tenant", help="工作空间（add 必填；list 可选，用于筛选）")
    w.add_argument("--url")
    w.add_argument("--events", help=f"逗号分隔；默认 {','.join(W.DEFAULT_EVENTS)}；* 表示全部")
    w.add_argument("--description")
    w.add_argument("--overlap-s", type=int, default=None, help=f"旧密钥继续签名的秒数（0–{W.MAX_OVERLAP_S}）")
    w.add_argument("--status", choices=W.DELIVERY_STATUSES)
    w.add_argument("--limit", type=int, default=None)
    w.add_argument("--cursor")
    w.add_argument("--json", action="store_true", help="输出 JSON")
    w.set_defaults(func=cmd_webhooks)
    return w


def _data_dir(args) -> str:
    # Same resolution as `synwevo_docs.cli.data_dir`; repeated rather than imported so that
    # `cli` can import this module without a cycle.
    return getattr(args, "data", None) or env.get("DATA_DIR") or str(data_dir_default())


def _ts(v) -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(v)) if v else "-"


def _need(args, *names) -> None:
    missing = [n for n in names if not getattr(args, n.replace("-", "_"), None)]
    if missing:
        raise ServiceError(400, "missing_argument", f"缺少参数：{', '.join(missing)}")


def _print_sub(s: dict, as_json: bool) -> None:
    if as_json:
        print(json.dumps(s, ensure_ascii=False, indent=2))
        return
    print(f"订阅 ID  : {s['id']}")
    if s.get("tenant"):
        print(f"工作空间 : {s['tenant']}")
    print(f"地址     : {s['url']}")
    print(f"事件     : {','.join(s['events'])}")
    print(f"状态     : {s['status']}" + (f"（{s['disabled_reason']}）" if s.get("disabled_reason") else ""))
    print(f"授权者   : {s['authorized_by']}")
    print(f"版本     : {s['version']}")
    print(f"最近成功 : {_ts(s.get('last_success_at'))}    最近失败：{_ts(s.get('last_failure_at'))}"
          f"    连续失败：{s.get('consecutive_failures') or 0}")
    if s.get("previous_secret_expires_at"):
        print(f"旧密钥   : 仍在签名，至 {_ts(s['previous_secret_expires_at'])}")
    if s.get("secret"):
        print("\n签名密钥（只显示这一次，服务器只保存加密后的值）：\n")
        print(s["secret"])
        print("\n接收端用它校验 webhook-signature；参考实现见 apps/docs/examples/webhook_receiver.py")


def _print_delivery(d: dict) -> None:
    print(f"{d['id']:<18}{d['webhook_id']:<18}{(d['event_type'] or ''):<28}{d['status']:<11}"
          f"{d['attempts']:<5}{str(d['last_code'] or '-'):<6}{str(d['duration_ms'] if d['duration_ms'] is not None else '-'):<8}"
          f"{_ts(d['created_at']):<21}{'重投' if d['redelivery'] else ''} {d['last_error'] or ''}")


def cmd_webhooks(args) -> int:
    try:
        return _run(args)
    except ServiceError as e:
        print(f"错误：{e.message}", file=sys.stderr)
        return 1


def _run(args) -> int:
    if args.action == "test":
        _need(args, "url")
        try:
            out = W.send_test(args.url)
        except W.UrlRejected as e:
            raise ServiceError(400, "invalid_url", str(e)) from e
        except OSError as e:
            print(f"连接失败：{type(e).__name__}: {e}", file=sys.stderr)
            return 1
        print(json.dumps(out, ensure_ascii=False) if args.json else
              f"HTTP {out['status']}（事件 {out['event_id']}，{out['duration_ms']} ms）")
        return 0 if 200 <= out["status"] < 300 else 1

    store = Store(_data_dir(args))
    subs = W.Subscriptions(store)
    # The operator acts for the deployment, not as a workspace member: `tenant=None` is the
    # operator scope, and subscriptions created here deliver under operator authority.
    if args.action == "list":
        rows = subs.list(None)
        if args.tenant:
            rows = [r for r in rows if r["tenant"] == args.tenant]
        if args.json:
            print(json.dumps(rows, ensure_ascii=False, indent=2))
            return 0
        if not rows:
            print("还没有 Webhook 订阅。用 `webhooks add --tenant … --url …` 创建。")
            return 0
        print(f"{'订阅 ID':<18}{'工作空间':<16}{'状态':<10}{'事件':<34}地址")
        for s in rows:
            print(f"{s['id']:<18}{s['tenant'][:14]:<16}{s['status']:<10}{','.join(s['events'])[:32]:<34}{s['url']}")
        return 0

    if args.action == "add":
        _need(args, "tenant", "url")
        if not store.one("SELECT 1 FROM workspaces WHERE id=?", (args.tenant,)):
            print(f"提示：没有 ID 为 {args.tenant} 的工作空间；只有该租户下产生的任务事件才会投递到这里。", file=sys.stderr)
        body = {"url": args.url}
        if args.events:
            body["events"] = args.events.split(",")
        if args.description is not None:
            body["description"] = args.description
        s = subs.create(args.tenant, body, authority.describe(tenant=args.tenant))
        s["tenant"] = args.tenant
        _print_sub(s, args.json)
        return 0

    _need(args, "sid")
    sid = args.sid
    if args.action == "show":
        _print_sub(subs.get(None, sid), args.json)
        return 0
    if args.action == "update":
        body = {}
        if args.url:
            body["url"] = args.url
        if args.events:
            body["events"] = args.events.split(",")
        if args.description is not None:
            body["description"] = args.description
        if not body:
            raise ServiceError(400, "missing_argument", "至少提供 --url、--events 或 --description 之一")
        tenant = subs.get(None, sid)["tenant"]
        _print_sub(subs.update(None, sid, body, None, authority.describe(tenant=tenant)), args.json)
        return 0
    if args.action == "pause":
        _print_sub(subs.pause(None, sid), args.json)
        return 0
    if args.action == "resume":
        tenant = subs.get(None, sid)["tenant"]
        _print_sub(subs.resume(None, sid, authority.describe(tenant=tenant)), args.json)
        return 0
    if args.action == "delete":
        subs.delete(None, sid)
        print(f"已删除订阅 {sid}；排队中的投递已停止。")
        return 0
    if args.action == "rotate":
        tenant = subs.get(None, sid)["tenant"]
        _print_sub(subs.rotate_secret(None, sid, args.overlap_s, authority.describe(tenant=tenant)), args.json)
        return 0
    if args.action == "ping":
        d = subs.ping(None, sid)
        if args.json:
            print(json.dumps(d, ensure_ascii=False, indent=2))
        else:
            print(f"{d['status']}：HTTP {d['last_code'] or '-'}，{d['duration_ms'] if d['duration_ms'] is not None else '-'} ms"
                  + (f"，{d['last_error']}" if d["last_error"] else ""))
        return 0 if d["status"] == "delivered" else 1
    if args.action == "deliveries":
        page = subs.deliveries(None, sid, args.status, args.cursor, args.limit)
        if args.json:
            print(json.dumps(page, ensure_ascii=False, indent=2))
            return 0
        if not page["items"]:
            print("没有投递记录。")
            return 0
        print(f"{'投递 ID':<18}{'webhook-id':<18}{'事件':<28}{'状态':<11}{'次数':<5}{'码':<6}{'耗时ms':<8}{'创建':<21}")
        for d in page["items"]:
            _print_delivery(d)
        if page["has_more"]:
            print(f"\n还有更多：--cursor {page['next_cursor']}")
        return 0
    if args.action == "redeliver":
        _need(args, "delivery_id")
        d = subs.redeliver(None, sid, args.delivery_id)
        print(json.dumps(d, ensure_ascii=False, indent=2) if args.json else
              f"已排队重新投递：{d['id']}（webhook-id {d['webhook_id']} 不变），由运行中的服务发送。")
        return 0
    return 1
