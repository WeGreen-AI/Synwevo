"""`python -m synwevo_docs.cli webhooks …`: the operator's way to manage subscriptions (there is no
product UI). Driven through `cli_webhooks.register` so it is tested independently of how the main
CLI wires it in."""
import argparse
import json

import pytest

from synwevo_core.store import Store

from synwevo_docs import cli_webhooks
from synwevo_docs import webhooks as W

from .test_webhook_support import Receiver, fixture_event, signed_by


@pytest.fixture()
def run(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("SYNWEVO_DOCS_WEBHOOK_ALLOW_PRIVATE", "127.0.0.1")
    monkeypatch.setenv("SYNWEVO_DOCS_WEBHOOK_ALLOW_HTTP", "1")
    data = tmp_path / "data"

    def _run(*argv) -> tuple[int, str, str]:
        ap = argparse.ArgumentParser()
        ap.add_argument("--data")
        sub = ap.add_subparsers(dest="cmd", required=True)
        cli_webhooks.register(sub)
        args = ap.parse_args(["--data", str(data), *argv])
        code = args.func(args)
        out = capsys.readouterr()
        return code, out.out, out.err

    _run.data = data
    return _run


def test_cli_lifecycle(run):
    rx = Receiver()
    try:
        code, out, _ = run("webhooks", "add", "--tenant", "ws-cli", "--url", rx.url, "--events", "job.completed,job.failed", "--json")
        assert code == 0
        sub = json.loads(out)
        assert sub["secret"].startswith("whsec_") and sub["events"] == ["job.completed", "job.failed"]
        assert sub["authorized_by"] == "operator"
        code, out, _ = run("webhooks", "list")
        assert code == 0 and sub["id"] in out and "whsec_" not in out
        code, out, _ = run("webhooks", "show", sub["id"], "--json")
        assert "secret" not in json.loads(out)

        code, out, _ = run("webhooks", "ping", sub["id"], "--json")
        assert code == 0 and json.loads(out)["status"] == "delivered"
        assert signed_by(sub["secret"], rx.received[-1])

        code, out, _ = run("webhooks", "rotate", sub["id"], "--overlap-s", "0", "--json")
        new = json.loads(out)["secret"]
        assert new != sub["secret"]
        run("webhooks", "ping", sub["id"])
        assert signed_by(new, rx.received[-1]) and not signed_by(sub["secret"], rx.received[-1])

        # A real event in that workspace, delivered by a dispatcher, then redelivered from the CLI.
        store = Store(run.data)
        d = W.WebhookDispatcher(store, start=False, concurrency=1)
        d.run_once()
        fixture_event(store, "ws-cli", "job.failed")
        d.run_once()
        code, out, _ = run("webhooks", "deliveries", sub["id"], "--json")
        page = json.loads(out)
        job_rows = [x for x in page["items"] if x["event_type"] == "job.failed"]
        assert len(job_rows) == 1 and job_rows[0]["status"] == "delivered"
        code, out, _ = run("webhooks", "redeliver", sub["id"], job_rows[0]["id"], "--json")
        again = json.loads(out)
        assert code == 0 and again["webhook_id"] == job_rows[0]["webhook_id"] and again["redelivery"]
        d.run_once()
        assert rx.received[-1]["headers"]["webhook-id"] == job_rows[0]["webhook_id"]

        code, out, _ = run("webhooks", "pause", sub["id"], "--json")
        assert json.loads(out)["status"] == "paused"
        code, out, _ = run("webhooks", "resume", sub["id"], "--json")
        assert json.loads(out)["status"] == "active"
        code, out, _ = run("webhooks", "delete", sub["id"])
        assert code == 0
        code, _, err = run("webhooks", "show", sub["id"])
        assert code == 1 and "不存在" in err
    finally:
        rx.stop()


def test_cli_refuses_bad_input(run, monkeypatch):
    code, _, err = run("webhooks", "add", "--tenant", "ws-cli", "--url", "http://169.254.169.254/")
    assert code == 1 and "内网" in err
    code, _, err = run("webhooks", "add", "--url", "https://example.com/")
    assert code == 1 and "tenant" in err
    code, _, err = run("webhooks", "add", "--tenant", "ws-cli", "--url", "http://127.0.0.1:9/", "--events", "job.complete")
    assert code == 1 and "job.complete" in err


def test_cli_test_command(run, monkeypatch):
    monkeypatch.setenv("SYNWEVO_DOCS_WEBHOOK_SECRET", "deploy-secret")
    rx = Receiver(lambda n: 204)
    try:
        code, out, _ = run("webhooks", "test", "--url", rx.url)
    finally:
        rx.stop()
    assert code == 0 and "HTTP 204" in out
    got = rx.received[0]
    assert W.verify(got["body"], got["headers"]["x-docweave-timestamp"], got["headers"]["x-docweave-signature"], "deploy-secret")
    monkeypatch.delenv("SYNWEVO_DOCS_WEBHOOK_SECRET")
    code, _, err = run("webhooks", "test", "--url", rx.url)
    assert code == 1 and "SYNWEVO_DOCS_WEBHOOK_SECRET" in err
