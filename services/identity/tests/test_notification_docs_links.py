"""Links to Synwevo Docs objects in notifications (bell items and mails) come from the Docs app's
registered address, never from the account service's own `SYNWEVO_APP_URL` — which is the account
service's address and produced `accounts…/p/<id>` links in mails."""
from __future__ import annotations

from synwevo_core.notifications import Notifier, resolve_target


def test_docs_links_use_the_registered_docs_origin_not_the_account_address(tmp_path, monkeypatch):
    monkeypatch.setenv("SYNWEVO_DATA_DIR", str(tmp_path / "d"))
    monkeypatch.setenv("SYNWEVO_ROLE", "accounts")
    monkeypatch.setenv("SYNWEVO_APP_URL", "http://accounts.testserver")          # what the account service really holds
    monkeypatch.setenv("SYNWEVO_ISSUER", "http://accounts.testserver")
    from synwevo_core.http import build_platform
    plat = build_platform(str(tmp_path / "d"))
    n = Notifier(plat.store)
    u = plat.identity.create_user("reader@example.test", "harbor lights guide the evening ferry home", status="active", display_name="Reader")
    # no Docs registration yet: no link rather than a guessed one
    n.emit(u["id"], "docs.job_finished", params={"project": "P", "outcome": "已完成", "done": "1", "failed": "0"},
           object_kind="job", object_id="j_1", dedupe_key="t1", target={"origin": "docs", "path": "/p/p_1/jobs/j_1"})
    item = n.list(u["id"])["items"][0]
    assert item["href"] is None
    assert resolve_target({"origin": "docs", "path": "/p/p_1"}) is None
    # the Docs process registered itself (as it does on start, with its own address)
    from synwevo_core.apps import bootstrap_docs_app
    bootstrap_docs_app(plat.store, plat.identity, launch_url="http://docs.testserver/")
    n2 = Notifier(plat.store)                                                     # fresh cache
    item = n2.list(u["id"])["items"][0]
    assert item["href"] == "http://docs.testserver/p/p_1/jobs/j_1"
    # account links still resolve through the issuer; the account address is never used for Docs
    n2.emit(u["id"], "copy.approved", params={"project": "P", "decider": "D", "reason": ""}, dedupe_key="t2",
            target={"origin": "accounts", "path": "/organizations/o_1?approval=cp_1"})
    hrefs = {x["kind"]: x["href"] for x in n2.list(u["id"])["items"]}
    assert hrefs["copy.approved"] == "http://accounts.testserver/organizations/o_1?approval=cp_1"
    assert "accounts.testserver/p/" not in (hrefs["docs.job_finished"] or "")
    # a process that serves Docs itself may still fall back to its own address
    monkeypatch.setenv("SYNWEVO_ROLE", "app")
    assert resolve_target({"origin": "docs", "path": "/p/x"}) == "http://accounts.testserver/p/x"
