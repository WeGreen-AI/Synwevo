"""Scenario C over the API (API-5): an integration the project owner authorized asks for a copy of a
personal project into an organization workspace, follows it through copy.* events and the status
route, and never decides it; the target's approver decides in the browser; execution makes an
independent copy. Real composed server, real OAuth consent, real copy runner."""
from __future__ import annotations

import base64
import hashlib
import urllib.parse

from fastapi.testclient import TestClient

from synwevo_core import oidc
from synwevo_core.auth import Auth
from services.identity.tests.oauth_consent_helper import through_consent
from tests.integration.test_project_copy import acting, build_project, reauth, settle, world  # noqa: F401

REDIRECT = "https://integration.example.test/callback"
V1 = "/api/v1"


def delegated_token(w, who: str, workspace_id: str) -> dict:
    store = w["ident"].store
    if not oidc.Clients(store).get("copy-bot"):
        oidc.Clients(store).register(client_id="copy-bot", app_id="docweave", name="Copy automation fixture",
                                     redirect_uris=[REDIRECT], scopes="openid docweave.read docweave.write")
    c = w["c"][who]
    verifier = "copy-fixture-proof-key-with-more-than-forty-three-characters"
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    q = {"client_id": "copy-bot", "redirect_uri": REDIRECT, "response_type": "code", "scope": "openid docweave.read docweave.write",
         "state": "s", "code_challenge": challenge, "code_challenge_method": "S256", "workspace_id": workspace_id}
    r = through_consent(c, c.get("/oidc/authorize?" + urllib.parse.urlencode(q), follow_redirects=False))
    code = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(r.headers["location"]).query))["code"]
    tok = TestClient(w["api"]).post("/oidc/token", data={"grant_type": "authorization_code", "code": code, "redirect_uri": REDIRECT,
                                                         "client_id": "copy-bot", "code_verifier": verifier,
                                                         "resource": oidc.docs_resource()})
    assert tok.status_code == 200, tok.text
    return {"Authorization": f"Bearer {tok.json()['access_token']}"}


def api(w) -> TestClient:
    return TestClient(w["api"])


def events(w, h, types="copy.requested,copy.approved,copy.rejected,copy.withdrawn,copy.done,copy.failed,copy.stale"):
    r = api(w).get(f"{V1}/events?types={types}", headers=h)
    assert r.status_code == 200, r.text
    return r.json()["items"]


def test_an_authorized_integration_asks_follows_and_gets_an_independent_copy(world):
    w = world
    oid, wid, personal = w["oid"], w["wid"], w["personal"]["alice"]
    alice = acting(w["c"]["alice"], personal)
    pid, _ = build_project(alice)
    h = delegated_token(w, "alice", personal)
    targets = api(w).get(f"{V1}/copy-targets", headers=h).json()
    assert targets["source_is_personal"] is True and [t["id"] for t in targets["items"]] == [wid]
    manifest = api(w).get(f"{V1}/projects/{pid}/copy-manifest?sources=true", headers=h).json()
    assert manifest["manifest"]["sources"]["count"] == 1 and manifest["digest"]

    body = {"target_workspace_id": wid, "name": "合同项目（API 副本）", "purpose": "自动化复用", "scope": {"sources": True}}
    first = api(w).post(f"{V1}/projects/{pid}/copy-requests", json=body, headers={**h, "Idempotency-Key": "copy-api-1"})
    assert first.status_code == 201, first.text
    req = first.json()
    assert req["status"] == "pending" and "source_workspace_id" not in req
    # A lost response retried with the same key is the same request, not a second one.
    again = api(w).post(f"{V1}/projects/{pid}/copy-requests", json=body, headers={**h, "Idempotency-Key": "copy-api-1"})
    assert again.status_code == 201 and again.json()["id"] == req["id"] and again.headers.get("Idempotent-Replayed") == "true"
    other = api(w).post(f"{V1}/projects/{pid}/copy-requests", json={**body, "name": "changed"}, headers={**h, "Idempotency-Key": "copy-api-1"})
    assert other.status_code == 409 and other.json()["error"]["code"] == "idempotency_conflict"
    dup = api(w).post(f"{V1}/projects/{pid}/copy-requests", json=body, headers=h)
    assert dup.status_code == 409 and dup.json()["error"]["code"] == "copy_already_requested"
    assert w["ident"].store.one("SELECT COUNT(*) AS n FROM project_copies WHERE source_project_id=?", (pid,))["n"] == 1

    # The approver was asked, exactly as for a request made in the workbench.
    n = w["api"].state.notifier
    assert [x["kind"] for x in n.list(w["users"]["bob"]["id"])["items"]] == ["copy.requested"]
    # The API cannot decide; the approver decides in the browser, with re-authentication.
    assert api(w).post(f"{V1}/projects/{pid}/copy-requests", json={**body, "decide": "approve"}, headers=h).json()["error"]["code"] == "decision_in_browser"
    bob = w["c"]["bob"]
    reauth(bob)
    decided = bob.post(f"/api/auth/organizations/{oid}/approvals/{req['id']}/decide",
                       json={"action": "approve", "reason": "team needs it", "version": req["version"]})
    assert decided.status_code == 200 and decided.json()["status"] == "approved", decided.text

    done = settle(w, alice, pid)
    assert done["status"] == "done", done
    status = api(w).get(f"{V1}/copy-requests/{req['id']}", headers=h).json()
    assert status["status"] == "done" and status["result"]["project_id"] == done["result"]["project_id"]
    assert status["decider"]["name"] == "Bob Boss" and status["self_approved"] is False
    # The copy is independent and lives in the target: the delegated token (personal workspace) cannot open it.
    assert api(w).get(f"{V1}/projects/{status['result']['project_id']}", headers=h).status_code == 404
    assert acting(bob, wid).get(f"/api/projects/{status['result']['project_id']}").status_code == 200

    # The integration heard the whole story from the event feed, in order, as ids and status only.
    seen = [(e["type"], e["copy_id"]) for e in events(w, h)]
    assert seen == [("copy.requested", req["id"]), ("copy.approved", req["id"]), ("copy.done", req["id"])], seen
    last = events(w, h)[-1]
    assert last["project_id"] == pid and last["job_id"] is None
    assert set(last["data"]) == {"copy_id", "source_project_id", "target_workspace_id", "status", "version"}
    assert "合同" not in str(events(w, h)), "no names or manifest in events"
    listed = api(w).get(f"{V1}/projects/{pid}/copy-requests?status=done", headers=h).json()["items"]
    assert [x["id"] for x in listed] == [req["id"]]


def test_only_the_owners_delegation_can_ask_and_only_it_can_see_or_withdraw(world):
    w = world
    wid, personal = w["wid"], w["personal"]["alice"]
    alice = acting(w["c"]["alice"], personal)
    pid, _ = build_project(alice)
    # An access key for the same personal workspace has nobody behind it.
    key, _ = Auth(w["ident"].store).create_key("copy key fixture", personal, ("read", "write"))
    r = api(w).post(f"{V1}/projects/{pid}/copy-requests", json={"target_workspace_id": wid}, headers={"Authorization": f"Bearer {key}"})
    assert r.status_code == 403 and r.json()["error"]["code"] == "delegation_required"
    assert api(w).get(f"{V1}/copy-targets", headers={"Authorization": f"Bearer {key}"}).status_code == 403

    h = delegated_token(w, "alice", personal)
    for bad, field in (({"target_workspace_id": wid, "reason": "self"}, "reason"),
                       ({"target_workspace_id": wid, "scope": {"sources": "yes"}}, "scope"),
                       ({"target_workspace_id": wid, "source_workspace_id": "w_other"}, "source_workspace_id")):
        e = api(w).post(f"{V1}/projects/{pid}/copy-requests", json=bad, headers=h).json()["error"]
        assert e["field"] == field, (bad, e)
    req = api(w).post(f"{V1}/projects/{pid}/copy-requests", json={"target_workspace_id": wid}, headers=h).json()

    # Dave (a member of the target, with his own personal workspace and delegation) sees nothing of it.
    dh = delegated_token(w, "dave", w["personal"]["dave"])
    assert api(w).get(f"{V1}/copy-requests/{req['id']}", headers=dh).status_code == 404
    assert api(w).post(f"{V1}/copy-requests/{req['id']}/withdraw", headers=dh).status_code == 404
    assert api(w).get(f"{V1}/projects/{pid}/copy-requests", headers=dh).status_code == 404

    out = api(w).post(f"{V1}/copy-requests/{req['id']}/withdraw", headers=h)
    assert out.status_code == 200 and out.json()["status"] == "withdrawn"
    again = api(w).post(f"{V1}/copy-requests/{req['id']}/withdraw", headers=h)
    assert again.status_code == 409 and again.json()["error"]["code"] == "not_withdrawable"
    assert [e["type"] for e in events(w, h)] == ["copy.requested", "copy.withdrawn"]
    # A withdrawn request can be asked again; the approver's page no longer offers the old one.
    assert api(w).post(f"{V1}/projects/{pid}/copy-requests", json={"target_workspace_id": wid}, headers=h).status_code == 201


def test_scenario_c_the_copy_generates_under_its_own_authorization_and_nothing_crosses_back(world):
    """Scenario C end to end over the API: ask → the target's approver decides → execution re-checks →
    an independent copy → generation from it with an authorization for the target (a separate
    consent), output read back → the requester's notification points at the copy, and neither the
    notification nor the copy opens anything to someone outside the target."""
    import time

    from tests.integration.test_project_copy import docx_text

    w = world
    oid, wid, personal = w["oid"], w["wid"], w["personal"]["alice"]
    alice = acting(w["c"]["alice"], personal)
    pid, _ = build_project(alice)
    h = delegated_token(w, "alice", personal)
    req = api(w).post(f"{V1}/projects/{pid}/copy-requests", json={"target_workspace_id": wid, "scope": {"sources": True}},
                      headers=h).json()
    bob = w["c"]["bob"]
    reauth(bob)
    assert bob.post(f"/api/auth/organizations/{oid}/approvals/{req['id']}/decide",
                    json={"action": "approve", "reason": "team needs it", "version": req["version"]}).status_code == 200
    new_pid = settle(w, alice, pid)["result"]["project_id"]
    # A second tick of the runner (a duplicate wake-up) does not copy again.
    w["api"].state.copy_runner.run_once()
    in_target = acting(bob, wid).get("/api/projects").json()
    assert [p["id"] for p in in_target if p.get("origin", "").startswith("copy:")] == [new_pid]

    # The integration works in the target only with an authorization for the target.
    assert api(w).post(f"{V1}/projects/{new_pid}/jobs", json={"formats": ["docx"]}, headers=h).status_code == 404
    ht = delegated_token(w, "alice", wid)
    assert api(w).get(f"{V1}/projects/{pid}", headers=ht).status_code == 404, "the source stays in the personal workspace"
    submit = {**ht, "Idempotency-Key": "scenario-c-generate-1"}
    job = api(w).post(f"{V1}/projects/{new_pid}/jobs", json={"formats": ["docx"]}, headers=submit)
    assert job.status_code == 202, job.text
    assert api(w).post(f"{V1}/projects/{new_pid}/jobs", json={"formats": ["docx"]}, headers=submit).json()["id"] == job.json()["id"]
    jid, t0 = job.json()["id"], time.time()
    while (st := api(w).get(f"{V1}/jobs/{jid}", headers=ht).json())["status"] in ("queued", "running", "cancelling"):
        assert time.time() - t0 < 120, st
        time.sleep(0.2)
    assert st["status"] == "completed", st
    items = api(w).get(f"{V1}/jobs/{jid}/items?limit=100", headers=ht).json()["items"]
    texts = [docx_text(api(w).get(f"{V1}/artifacts/{a['id']}/content", headers=ht).content) for it in items for a in it["artifacts"]]
    assert any("张三" in t for t in texts) and any("李四" in t for t in texts), texts

    # The requester's card points at the copy; it grants nothing to anyone outside the target.
    done = [x for x in w["api"].state.notifier.list(w["users"]["alice"]["id"])["items"] if x["kind"] == "copy.done"]
    assert len(done) == 1 and done[0]["href"] and done[0]["href"].endswith(f"/p/{new_pid}"), done
    eve = acting(w["c"]["eve"], w["personal"]["eve"])
    assert eve.get(f"/api/projects/{new_pid}").status_code in (403, 404)
    assert acting(w["c"]["eve"], wid).get(f"/api/projects/{new_pid}").status_code in (401, 403, 404)
    # The source's events stop at the copy; the target's generation is the target's own story.
    assert [e["type"] for e in events(w, h)] == ["copy.requested", "copy.approved", "copy.done"]
    assert api(w).get(f"{V1}/events?types=job.completed", headers=h).json()["items"] == []
    assert [e["job_id"] for e in api(w).get(f"{V1}/events?types=job.completed", headers=ht).json()["items"]] == [jid]


def test_copy_contract_rejects_wrong_types_before_any_side_effect(world):
    w = world
    personal = w["personal"]["alice"]
    alice = acting(w["c"]["alice"], personal)
    pid = alice.post("/api/projects", json={"name": "validation fixture"}).json()["id"]
    h = delegated_token(w, "alice", personal)
    invalid = [("target_workspace_id", v) for v in (None, False, [], {}, 7, " ")]
    invalid += [("name", v) for v in (None, False, [], {}, 7, " ", "n" * 101)]
    invalid += [("purpose", v) for v in (None, False, [], {}, 7, "p" * 501)]
    invalid += [("scope", v) for v in (None, False, [], "", 0, {"sources": 1}, {"extra": True})]
    for field, value in invalid:
        r = api(w).post(f"{V1}/projects/{pid}/copy-requests", headers=h,
                        json={"target_workspace_id": w["wid"], field: value})
        assert r.status_code == 400 and r.json()["error"]["field"] == field, (field, value, r.text)
    assert w["ident"].store.one("SELECT COUNT(*) AS n FROM project_copies")["n"] == 0
    assert events(w, h) == []
    # Canonically equivalent default fields share the same request fingerprint.
    keyed = {**h, "Idempotency-Key": "normalized-copy-request"}
    first = api(w).post(f"{V1}/projects/{pid}/copy-requests", headers=keyed, json={"target_workspace_id": w["wid"]})
    replay = api(w).post(f"{V1}/projects/{pid}/copy-requests", headers=keyed,
                         json={"target_workspace_id": w["wid"], "scope": {"sources": False, "corrections": False}, "purpose": " "})
    assert first.status_code == 201 and replay.content == first.content
    assert replay.headers["Idempotent-Replayed"] == "true"


def test_deleted_source_and_revoked_delegation_cannot_replay_copy_manifests(world):
    w = world
    personal = w["personal"]["alice"]
    alice = acting(w["c"]["alice"], personal)
    pid = alice.post("/api/projects", json={"name": "private historical manifest fixture"}).json()["id"]
    h = delegated_token(w, "alice", personal)
    keyed = {**h, "Idempotency-Key": "private-copy-history"}
    body = {"target_workspace_id": w["wid"]}
    req = api(w).post(f"{V1}/projects/{pid}/copy-requests", headers=keyed, json=body).json()
    assert alice.delete(f"/api/projects/{pid}").status_code == 200
    for changed in (body, {**body, "name": "conflicting retry"}):
        r = api(w).post(f"{V1}/projects/{pid}/copy-requests", headers=keyed, json=changed)
        assert r.status_code == 404 and "manifest" not in r.json()["error"]
        assert "resource_id" not in r.json()["error"]
    assert api(w).get(f"{V1}/copy-requests/{req['id']}", headers=h).status_code == 404
    assert oidc.Grants(w["ident"].store).revoke_token(h["Authorization"].removeprefix("Bearer "))
    assert api(w).get(f"{V1}/copy-requests/{req['id']}", headers=h).status_code == 401


def test_copy_status_filter_is_applied_before_limit(world):
    w = world
    personal = w["personal"]["alice"]
    alice = acting(w["c"]["alice"], personal)
    pid = alice.post("/api/projects", json={"name": "copy filter fixture"}).json()["id"]
    h = delegated_token(w, "alice", personal)
    path = f"{V1}/projects/{pid}/copy-requests"
    first = api(w).post(path, headers=h, json={"target_workspace_id": w["wid"]}).json()
    assert api(w).post(f"{V1}/copy-requests/{first['id']}/withdraw", headers=h).status_code == 200
    assert api(w).post(path, headers=h, json={"target_workspace_id": w["wid"]}).status_code == 201
    result = api(w).get(path + "?status=withdrawn&limit=1", headers=h)
    assert result.status_code == 200 and [x["id"] for x in result.json()["items"]] == [first["id"]]


def test_concurrent_copy_requests_have_one_logical_request_and_event(world):
    from concurrent.futures import ThreadPoolExecutor
    import threading

    w = world
    personal = w["personal"]["alice"]
    alice = acting(w["c"]["alice"], personal)
    h = delegated_token(w, "alice", personal)
    for key in (None, "concurrent-copy-fixture"):
        pid = alice.post("/api/projects", json={"name": "concurrent copy fixture"}).json()["id"]
        barrier = threading.Barrier(4)

        def submit(_):
            barrier.wait(10)
            return api(w).post(f"{V1}/projects/{pid}/copy-requests", json={"target_workspace_id": w["wid"]},
                               headers={**h, **({"Idempotency-Key": key} if key else {})})
        with ThreadPoolExecutor(max_workers=4) as pool:
            out = list(pool.map(submit, range(4)))
        assert sorted(r.status_code for r in out) == ([201] * 4 if key else [201, 409, 409, 409])
        ids = {r.json()["id"] for r in out if r.status_code == 201}
        assert len(ids) == 1
        assert w["ident"].store.one("SELECT COUNT(*) AS n FROM project_copies WHERE source_project_id=?", (pid,))["n"] == 1
        assert len([e for e in events(w, h) if e["copy_id"] in ids]) == 1


def _approved_execution_fixture(w):
    """Real approved copy, with the background worker stopped so failures are deterministic."""
    import time
    runner = w["api"].state.copy_runner
    runner.stop()
    until = time.time() + 15
    while runner.alive() and time.time() < until:
        time.sleep(0.01)
    assert not runner.alive()
    personal = w["personal"]["alice"]
    alice = acting(w["c"]["alice"], personal)
    pid = alice.post("/api/projects", json={"name": "publication fixture"}).json()["id"]
    h = delegated_token(w, "alice", personal)
    req = api(w).post(f"{V1}/projects/{pid}/copy-requests", json={"target_workspace_id": w["wid"]}, headers=h).json()
    bob = w["c"]["bob"]
    reauth(bob)
    assert bob.post(f"/api/auth/organizations/{w['oid']}/approvals/{req['id']}/decide",
                    json={"action": "approve", "reason": "synthetic execution fixture"}).status_code == 200
    return runner, pid, req["id"], h


def test_copy_result_and_done_event_publish_atomically(world):
    w = world
    runner, pid, cid, h = _approved_execution_fixture(w)
    store = w["ident"].store
    # Real database failure at the final record, after the copy's project/mapping inserts.
    store.execute("CREATE TRIGGER synthetic_copy_publication_failure BEFORE UPDATE OF status ON project_copies "
                  "WHEN NEW.status='done' BEGIN SELECT RAISE(ABORT, 'synthetic copy publication failure'); END")
    result = runner.executor.run(cid)
    assert result["status"] == "failed", result
    assert store.one("SELECT COUNT(*) AS n FROM projects WHERE origin=?", (f"copy:{cid}",))["n"] == 0
    assert [e["type"] for e in events(w, h)] == ["copy.requested", "copy.approved", "copy.failed"]
    assert not store.one("SELECT 1 FROM messaging_outbox WHERE event_id=?", (f"copy:{cid}:done",))


def test_copy_rechecks_a_source_change_after_claim_before_publication(world):
    w = world
    runner, pid, cid, h = _approved_execution_fixture(w)
    store = w["ident"].store
    # A trigger represents a committed source mutation at the execution boundary. The first
    # digest check has passed; the second must reject it before any target row is published.
    store.execute("CREATE TRIGGER synthetic_source_change AFTER UPDATE OF status ON project_copies "
                  "WHEN NEW.status='running' BEGIN UPDATE projects SET rev=rev+1 WHERE id=NEW.source_project_id; END")
    result = runner.executor.run(cid)
    assert result["status"] == "stale" and result["error"] == "source_changed", result
    assert not store.one("SELECT 1 FROM projects WHERE origin=?", (f"copy:{cid}",))
    assert [e["type"] for e in events(w, h)] == ["copy.requested", "copy.approved", "copy.stale"]


def test_copy_rechecks_source_application_access_before_execution(world):
    w = world
    runner, pid, cid, h = _approved_execution_fixture(w)
    store = w["ident"].store
    store.execute("DELETE FROM workspace_apps WHERE workspace_id=? AND app_id='docweave'", (w["personal"]["alice"],))
    result = runner.executor.run(cid)
    assert result["status"] == "stale" and result["error"] == "source_authority_lost", result
    assert not store.one("SELECT 1 FROM projects WHERE origin=?", (f"copy:{cid}",))


def test_docs_and_directory_share_the_idempotency_implementation():
    from synwevo_core import idempotency as shared
    from synwevo_docs.api import idempotency as docs
    assert docs.Idempotency is shared.Idempotency
    assert docs.purge_expired is shared.purge_expired


def test_a_recovered_copy_claim_fences_the_previous_runner(world):
    from synwevo_docs.copies import CopyRunner

    w = world
    runner, pid, cid, h = _approved_execution_fixture(w)
    copies = runner.copies
    first = copies.claim_approved(cid)
    assert first["status"] == "running"
    # A second process starts after the first committed its claim, before publication.
    recovered = CopyRunner(runner.executor, copies, start=False)
    second = copies.claim_approved(cid)
    assert second["version"] > first["version"]
    assert copies.finish(cid, "failed", error="old execution attempt", expected_version=first["version"]) is None
    assert copies.reset_stale(cid, error="old execution attempt", claimed_version=first["version"]) is None
    assert copies.get(cid)["status"] == "running" and copies.get(cid)["error"] is None
    assert copies.finish(cid, "failed", error="current execution attempt", expected_version=second["version"])["status"] == "failed"
    assert len([e for e in events(w, h) if e["type"] == "copy.failed"]) == 1
    recovered.stop()


def test_restart_reconciles_a_legacy_published_copy_instead_of_copying_again(world):
    from synwevo_docs.copies import CopyRunner

    w = world
    runner, pid, cid, h = _approved_execution_fixture(w)
    store, copies = w["ident"].store, runner.copies
    claimed = copies.claim_approved(cid)
    # Reproduce the old two-commit implementation: the target is durable, `done` is absent.
    published = runner.executor._copy(claimed, claimed["manifest"])
    assert copies.get(cid)["status"] == "running"
    recovered = CopyRunner(runner.executor, copies, start=False)
    assert recovered.run_once() == 0
    result = copies.get(cid)
    assert result["status"] == "done" and result["result"]["project_id"] == published["project_id"]
    assert result["result"]["warnings"] == ["recovered_previous_publication"]
    assert store.one("SELECT COUNT(*) AS n FROM projects WHERE origin=?", (f"copy:{cid}",))["n"] == 1
    assert [e["type"] for e in events(w, h)] == ["copy.requested", "copy.approved", "copy.done"]
    assert copies.finish(cid, "failed", error="old runner", expected_version=claimed["version"]) is None
    recovered.stop()


def test_restart_preserves_ambiguous_legacy_results_and_never_executes_again(world):
    from synwevo_docs.copies import CopyRunner

    w = world
    runner, pid, cid, h = _approved_execution_fixture(w)
    store, copies = w["ident"].store, runner.copies
    claimed = copies.claim_approved(cid)
    # Synthetic legacy fault: two committed publications claim the same request origin.
    first = runner.executor._copy(claimed, claimed["manifest"])
    second = runner.executor._copy(claimed, claimed["manifest"])
    recovered = CopyRunner(runner.executor, copies, start=False)
    assert recovered.run_once() == 0
    result = copies.get(cid)
    assert result["status"] == "failed" and result["error"] == "copy_recovery_ambiguous"
    assert {r["id"] for r in store.all("SELECT id FROM projects WHERE origin=?", (f"copy:{cid}",))} == {first["project_id"], second["project_id"]}
    assert [e["type"] for e in events(w, h)] == ["copy.requested", "copy.approved", "copy.failed"]
    recovered.stop()


def test_an_approved_copy_stops_when_its_oauth_delegation_is_revoked(world):
    _assert_delegation_stops_copy(world, "revoke")


def test_an_approved_copy_stops_when_its_integration_is_disabled(world):
    _assert_delegation_stops_copy(world, "disable")


def _assert_delegation_stops_copy(w, mutation):
    import json
    from synwevo_core.store import now

    runner, pid, cid, h = _approved_execution_fixture(w)
    store = w["ident"].store
    desc = store.one("SELECT requested_authority FROM project_copies WHERE id=?", (cid,))["requested_authority"]
    assert json.loads(desc)["kind"] == "delegated"
    assert h["Authorization"].removeprefix("Bearer ") not in desc
    assert "requested_authority" not in api(w).get(f"{V1}/copy-requests/{cid}", headers=h).json()
    if mutation == "revoke":
        assert oidc.Grants(store).revoke_token(h["Authorization"].removeprefix("Bearer "))
    else:
        store.execute("UPDATE oauth_clients SET disabled_at=? WHERE client_id='copy-bot'", (now(),))
    result = runner.executor.run(cid)
    assert result["status"] == "stale" and result["error"].startswith("delegation:"), result
    assert not store.one("SELECT 1 FROM projects WHERE origin=?", (f"copy:{cid}",))
    assert [r["type"] for r in store.all("SELECT type FROM job_events WHERE tenant=? AND type LIKE 'copy.%' ORDER BY seq",
                                         (w["personal"]["alice"],))] == ["copy.requested", "copy.approved", "copy.stale"]


def test_copy_rechecks_delegation_after_claim_inside_the_publication_transaction(world):
    w = world
    runner, pid, cid, h = _approved_execution_fixture(w)
    store = w["ident"].store
    # Synthetic revocation at the committed claim boundary; pre-claim validation has passed.
    store.execute("CREATE TRIGGER synthetic_copy_delegation_revocation AFTER UPDATE OF status ON project_copies "
                  "WHEN NEW.status='running' BEGIN UPDATE oauth_clients SET disabled_at=1 WHERE client_id='copy-bot'; END")
    result = runner.executor.run(cid)
    assert result["status"] == "stale" and result["error"] == "delegation:credential_revoked", result
    assert not store.one("SELECT 1 FROM projects WHERE origin=?", (f"copy:{cid}",))


def test_corrupt_copy_authority_never_becomes_a_legacy_unrestricted_request(world):
    w = world
    runner, pid, cid, h = _approved_execution_fixture(w)
    store = w["ident"].store
    store.execute("UPDATE project_copies SET requested_authority='[]' WHERE id=?", (cid,))
    result = runner.executor.run(cid)
    assert result["status"] == "stale" and result["error"] == "delegation:invalid_authority", result
    assert not store.one("SELECT 1 FROM projects WHERE origin=?", (f"copy:{cid}",))
