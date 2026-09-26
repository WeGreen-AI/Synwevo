"""Non-interactive CLI. Data goes to stdout as JSON; errors to stderr as JSON."""
from __future__ import annotations

import argparse
import json
import os
import sys

from . import Client, __version__, credentials_from_env
from .errors import IntegrityError, ServerError, SynwevoError, TransportError, ValidationError, WaitTimeoutError


class Parser(argparse.ArgumentParser):
    def error(self, message):
        raise ValidationError(message)


def emit(value, *, error=False):
    print(json.dumps(value, ensure_ascii=False), file=sys.stderr if error else sys.stdout, flush=True)


def load_json(path):
    if path == "-":
        return json.load(sys.stdin)
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def parser():
    common = Parser(add_help=False)
    # SUPPRESS preserves options specified before the subcommand when a leaf is parsed.
    common.add_argument("--base-url", default=argparse.SUPPRESS)
    common.add_argument("--timeout", type=float, default=argparse.SUPPRESS, help="HTTP operation timeout, seconds (default 30)")
    common.add_argument("--max-retries", type=int, default=argparse.SUPPRESS)
    common.add_argument("--json", action="store_true", default=argparse.SUPPRESS, help="JSON is already the default")
    p = Parser(prog="synwevo-docs", parents=[common])
    p.add_argument("--version", action="version", version=__version__)
    roots = p.add_subparsers(dest="command", required=True)

    def leaf(parent, name, method=None, positional=()):
        q = parent.add_parser(name, parents=[common])
        q.set_defaults(method=method)
        for arg in positional:
            q.add_argument(arg)
        return q

    def group(parent, name):
        return parent.add_parser(name, parents=[common]).add_subparsers(dest=name + "_action", required=True)

    def keyed(q):
        q.add_argument("--idempotency-key")
        return q

    def paged(q):
        q.add_argument("--limit", type=int)
        q.add_argument("--cursor")
        q.add_argument("--offset", type=int)
        q.add_argument("--all", action="store_true", help="Follow every page")
        return q

    def upload(q):
        keyed(q)
        q.add_argument("--resumable", action="store_true", default=None)
        q.add_argument("--upload-id")
        q.add_argument("--chunk-size", type=int)
        return q

    for name in ("whoami", "capabilities", "usage", "health"):
        leaf(roots, name, name)
    pr = group(roots, "projects")
    keyed(leaf(pr, "create", "create_project", ("name",)))
    paged(leaf(pr, "list", "list_projects"))
    leaf(pr, "get", "get_project", ("project_id",))
    leaf(pr, "delete", "delete_project", ("project_id",))
    q = leaf(pr, "template", "set_project_template", ("project_id", "template_id"))
    q.add_argument("--version", type=int)
    fs = group(roots, "files")
    upload(leaf(fs, "upload", "upload_source", ("project_id", "path")))
    paged(leaf(fs, "list", "list_files", ("project_id",)))
    leaf(fs, "get", "get_file", ("file_id",))
    leaf(fs, "delete", "delete_file", ("file_id",))
    ts = group(roots, "templates")
    q = upload(leaf(ts, "upload", "upload_template", ("path",)))
    q.add_argument("--name")
    paged(leaf(ts, "list", "list_templates"))
    leaf(ts, "get", "get_template", ("template_id",))
    leaf(ts, "delete", "delete_template", ("template_id",))
    builtin = group(ts, "builtin")
    q = keyed(leaf(builtin, "create", "create_builtin_template"))
    q.add_argument("kind", choices=("builtin_doc", "builtin_sheet"))
    q.add_argument("input", help="Editor JSON or exported model envelope; - reads stdin")
    q.add_argument("--name")
    q = keyed(leaf(builtin, "save", "save_builtin_template", ("template_id", "input")))
    q.add_argument("--base-version", type=int, required=True, help="Expected current head; stale edits fail")
    q.add_argument("--name")
    q = leaf(builtin, "export", "get_builtin_template", ("template_id",))
    q.add_argument("--version", type=int, help="Historical revision; default current head")
    fields = group(ts, "fields")
    leaf(fields, "get", "get_template_fields", ("template_id",))
    keyed(leaf(fields, "put", "create_template_fields", ("template_id", "input")))
    rs = group(roots, "rule")
    leaf(rs, "put", "put_rule", ("project_id", "input"))
    for name in ("mappings", "definition"):
        g = group(roots, name)
        leaf(g, "get", "get_" + name, ("project_id",))
        q = leaf(g, "put", "put_" + name, ("project_id", "input"))
        q.add_argument("--base-rev", type=int)
    q = leaf(roots, "preflight", "preflight", ("project_id",))
    q.add_argument("--formats", type=csv)
    q.add_argument("--naming")
    q.add_argument("--simulate", nargs="?", const=True, type=int, default=False)
    q.add_argument("--issues", action="store_true")
    q.add_argument("--level", choices=("error", "warning"))
    q = leaf(roots, "preview", "download_preview", ("project_id", "dest"))
    sample = q.add_mutually_exclusive_group()
    sample.add_argument("--index", type=int)
    sample.add_argument("--key")
    q.add_argument("--signature")
    ot = group(roots, "org-templates")
    q = paged(leaf(ot, "list", "list_organization_templates"))
    q.add_argument("--include-deprecated", action="store_true", default=None)
    leaf(ot, "get", "get_organization_template", ("publication_id",))
    q = keyed(leaf(ot, "publish", "publish_template", ("template_id",)))
    q.add_argument("--description")
    q = leaf(ot, "release", "release_organization_template", ("publication_id",))
    q.add_argument("--description")
    q = leaf(ot, "deprecate", "deprecate_organization_template", ("publication_id",))
    q.add_argument("--note")
    q = leaf(ot, "restore", "deprecate_organization_template", ("publication_id",))
    q.set_defaults(deprecated=False)
    q = keyed(leaf(ot, "use", "use_organization_template", ("publication_id",)))
    q.add_argument("--name")
    q.add_argument("--project-id")
    js = group(roots, "jobs")
    q = keyed(leaf(js, "submit", "create_job", ("project_id",)))
    q.add_argument("--formats", type=csv)
    q.add_argument("--naming")
    q.add_argument("--error-policy", choices=("block", "skip"))
    q.add_argument("--layout", choices=("off", "warn", "fit"))
    q.add_argument("--keep-fields", action="store_true", default=None)
    paged(leaf(js, "list", "list_jobs", ("project_id",)))
    for action, method in (("get", "get_job"), ("cancel", "cancel_job"), ("wait", "wait_for_job"), ("retry", "retry_job")):
        q = leaf(js, action, method, ("job_id",))
        if action == "wait":
            q.add_argument("--wait-timeout", type=float, default=300)
            q.add_argument("--poll-interval", type=float, default=1)
            q.add_argument("--cancel-on-interrupt", action="store_true")
            q.add_argument("--events", action="store_true", help="Write observed job events as JSON to stderr")
        if action == "retry":
            keyed(q)
            q.add_argument("--include-skipped", action="store_true")
            q.add_argument("--failed-only", action="store_true", help="Retry failed items only; excludes cancelled/skipped")
    q = paged(leaf(js, "items", "list_job_items", ("job_id",)))
    q.add_argument("--status")
    ar = group(roots, "artifacts")
    paged(leaf(ar, "list", "list_artifacts", ("job_id",)))
    q = leaf(ar, "download", "download_artifact", ("artifact", "dest"))
    q.add_argument("--expected-sha256")
    q = leaf(ar, "download-all", "download_artifacts", ("job_id", "dest_dir"))
    q.add_argument("--format")
    for name, method in (("archive", "download_archive"), ("report", "download_report")):
        g = group(roots, name)
        q = leaf(g, "download", method, ("job_id", "dest"))
        if name == "archive":
            q.add_argument("--format")
    wh = group(roots, "webhooks")
    leaf(wh, "list", "list_webhooks")
    q = leaf(wh, "add", "create_webhook", ("url",))
    q.add_argument("--events", type=csv)
    q.add_argument("--description")
    for action, method in (("get", "get_webhook"), ("pause", "pause_webhook"), ("resume", "resume_webhook"),
                           ("delete", "delete_webhook"), ("rotate", "rotate_webhook_secret"), ("ping", "ping_webhook")):
        q = leaf(wh, action, method, ("subscription_id",))
        if action == "rotate":
            q.add_argument("--overlap-s", type=int)
    q = paged(leaf(wh, "deliveries", "list_webhook_deliveries", ("subscription_id",)))
    q.add_argument("--status")
    leaf(wh, "redeliver", "redeliver_webhook", ("subscription_id", "delivery_id"))
    ev = group(roots, "events")
    q = leaf(ev, "tail", "iter_event_pages")
    q.add_argument("--cursor")
    q.add_argument("--types", type=csv)
    q.add_argument("--follow", action="store_true")
    q.add_argument("--interval", type=float, default=5)
    q.add_argument("--page-size", type=int)
    return p


def csv(value):
    return [s.strip() for s in value.split(",") if s.strip()]


def dispatch(c, args):
    method = args.method
    kw = {k: v for k, v in vars(args).items() if k not in {
        "method", "command", "base_url", "timeout", "max_retries", "json", "all",
        "input", "issues", "level", "wait_timeout", "cancel_on_interrupt",
    } and not k.endswith("_action") and v is not None}
    if hasattr(args, "input"):
        value = load_json(args.input)
        if method == "put_rule":
            kw["rule"] = value.get("rule", value) if isinstance(value, dict) else value
        elif method == "put_mappings":
            kw["mappings"] = value.get("mappings", value) if isinstance(value, dict) else value
        elif method in ("create_builtin_template", "save_builtin_template"):
            kw["editor"] = value.get("editor", value) if isinstance(value, dict) else value
        else:
            kw["definition"] = value
    if method == "preflight" and args.issues:
        if args.simulate:
            raise ValidationError("--issues and --simulate are separate API operations")
        kw.pop("simulate")
        kw["level"] = args.level
        return {"items": list(c.iter_preflight_issues(**kw))}
    if getattr(args, "all", False):
        if args.cursor is not None or args.offset is not None:
            raise ValidationError("--all starts at the first page; omit --cursor/--offset")
        kw.pop("cursor", None)
        kw.pop("offset", None)
        kw["page_size"] = kw.pop("limit", None)
        method = method.replace("list_", "iter_", 1)
        return {"items": list(getattr(c, method)(**kw))}
    if method == "list_webhook_deliveries":
        if kw.pop("offset", None) is not None:
            raise ValidationError("Webhook deliveries use cursor pagination, not --offset")
    if method == "wait_for_job":
        kw["timeout"] = args.wait_timeout
        if kw.pop("events"):
            kw["on_event"] = lambda e: emit({"event": e}, error=True)
        try:
            return c.wait_for_job(**kw)
        except KeyboardInterrupt:
            if args.cancel_on_interrupt:
                try:
                    c.cancel_job(args.job_id)
                except SynwevoError as exc:
                    emit({"cancel_error": exc.to_dict()}, error=True)
            raise
    if method == "iter_event_pages":
        for page in c.iter_event_pages(**kw):
            emit(page)
        return None
    return getattr(c, method)(**kw)


def main(argv=None):
    try:
        args = parser().parse_args(argv)
        base = getattr(args, "base_url", None) or os.environ.get("SYNWEVO_BASE_URL")
        if not base:
            raise ValidationError("Set SYNWEVO_BASE_URL or --base-url")
        with Client(base, credentials_from_env(), timeout=getattr(args, "timeout", 30),
                    max_retries=getattr(args, "max_retries", 4)) as c:
            result = dispatch(c, args)
        if result is not None:
            emit(result)
        if args.method == "wait_for_job" and result.get("status") in ("failed", "cancelled", "completed_with_errors"):
            return 5
        return 0
    except KeyboardInterrupt:
        emit({"error": {"code": "interrupted", "message": "Interrupted"}}, error=True)
        return 130
    except WaitTimeoutError as exc:
        emit({"error": exc.to_dict(), "job": exc.job}, error=True)
        return 6
    except SynwevoError as exc:
        emit({"error": exc.to_dict()}, error=True)
        if isinstance(exc, (TransportError, ServerError, IntegrityError)) or exc.status and exc.status >= 500:
            return 4
        return 3 if exc.status is not None else 2
    except (OSError, ValueError, TypeError) as exc:
        emit({"error": {"code": "invalid_input", "message": str(exc)}}, error=True)
        return 2
