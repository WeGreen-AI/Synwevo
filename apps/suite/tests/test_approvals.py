"""Approvals acceptance with isolated fixture accounts and a real Store/registry/HTTP stack."""
from concurrent.futures import ThreadPoolExecutor
import threading
import pytest
from synwevo_core.errors import ServiceError
from synwevo_suite.approvals.service import Approvals


def call(c,name,payload=None,user="alice",key=None,via="ui",ws=None):
    return c.caps.invoke("approvals."+name,payload or {},user_id=c.people.uid(user),
        workspace_id=ws or c.people.a,idempotency_key=key,via=via)


def out(c,name,payload=None,**kwargs):
    return call(c,name,payload,**kwargs)["output"]


def stage(c,users=("bob",),mode="all",sid="s_first",**kw):
    return {"id":sid,"name":"Independent review","approvers":{"users":[c.people.uid(u) for u in users]},"mode":mode,**kw}


def template(c,stages=None,**extra):
    definition={"fields":[{"id":"f_amount","label":"Amount","type":"money","currency":"USD","required":True}],
                "stages":stages or [stage(c)],**extra}
    t=out(c,"template.create",{"title":"Fixture expense","definition":definition})
    return out(c,"template.publish",{"template_id":t["id"],"draft_rev":1})


def request(c,t,**kw):
    return out(c,"request.create",{"template_id":t["id"],"title":"Fixture claim","values":{"f_amount":"12.50"},"basis":"Invoice fixture",**kw})


def change(r,**kw):
    return {"request_id":r["id"],"state_version":r["state_version"],**kw}


def vote(c,r,user="bob",decision="approve",**kw):
    task=next(t for t in r["tasks"] if t["assignee_id"]==c.people.uid(user) and t["status"]=="pending")
    return out(c,"request.decide",change(r,task_id=task["id"],decision=decision,note="Fixture review"),user=user,**kw)


def add_oscar(c):
    c.people.ident.add_member(c.people.a,c.people.uid("oscar"),"admin")


def error(code,fn):
    with pytest.raises(ServiceError) as e: fn()
    assert e.value.code==code


def test_discovery_permissions_and_http_csrf(suite):
    assert suite.caps.registry.get("approvals.request.decide").agent=="never"
    assert suite.people.store.one("SELECT name FROM sqlite_master WHERE name='approval_waits'")
    t=template(suite);r=request(suite,t)
    error("manage_required",lambda:out(suite,"template.create",{"title":"x"},user="bob"))
    error("read_only_role",lambda:request_as_viewer(suite,t))
    error("app_access_denied",lambda:out(suite,"request.get",{"request_id":r["id"]},user="out"))
    error("request_not_found",lambda:out(suite,"request.get",{"request_id":r["id"]},user="oscar",ws=suite.people.b))
    error("independent_approver_required",lambda:out(suite,"request.decide",change(r,task_id=r["tasks"][0]["id"],decision="approve")))
    error("not_for_agent",lambda:vote(suite,r,via="agent"))
    error("person_required",lambda:vote(suite,r,via="flow"))
    suite.sign_in("bob")
    payload={"workspace_id":suite.people.a,"input":change(r,task_id=r["tasks"][0]["id"],decision="approve")}
    assert suite.post("/api/suite/ops/approvals.request.decide",json=payload).status_code==403
    res=suite.post("/api/suite/ops/approvals.request.decide",json=payload,headers={"X-Synwevo-CSRF":"fixture-csrf"})
    assert res.status_code==200,res.text
    assert res.json()["output"]["status"]=="approved"


def request_as_viewer(c,t):
    return out(c,"request.create",{"template_id":t["id"],"title":"x","values":{"f_amount":"2"}},user="vera")


def test_versions_snapshots_and_request_idempotency(suite):
    t=template(suite)
    payload={"template_id":t["id"],"title":"Fixture","values":{"f_amount":"12.5"},"basis":"old"}
    first=call(suite,"request.create",payload,key="one")
    again=call(suite,"request.create",payload,key="one")
    assert again["replayed"] and first["output"]["id"]==again["output"]["id"]
    error("idempotency_conflict",lambda:call(suite,"request.create",{**payload,"basis":"other"},key="one"))
    assert first["output"]["snapshot"]["fields"]["f_amount"]=="12.50"
    t=out(suite,"template.update",{"template_id":t["id"],"draft_rev":1,"title":"new","definition":t["draft"]})
    error("draft_conflict",lambda:out(suite,"template.publish",{"template_id":t["id"],"draft_rev":1}))
    t=out(suite,"template.publish",{"template_id":t["id"],"draft_rev":t["draft_rev"]})
    assert request(suite,t)["template_version"]==2
    out(suite,"template.retire",{"template_id":t["id"],"version":1})
    error("version_retired",lambda:request(suite,t,version=1))
    assert vote(suite,first["output"])["template_version"]==1
    out(suite,"template.archive",{"template_id":t["id"],"archived":True})
    error("template_archived",lambda:request(suite,t))


def test_sequence_all_any_condition_and_distinct_people(suite):
    add_oscar(suite)
    stages=[stage(suite,("bob","oscar")),stage(suite,("bob",),sid="s_high",condition={"rules":[{"field":"f_amount","op":"gt","value":"1000"}]}),stage(suite,("bob","oscar"),"any",sid="s_final")]
    r=request(suite,template(suite,stages))
    r=vote(suite,r)
    assert r["status"]=="in_review" and r["stage_index"]==0
    r=vote(suite,r,"oscar")
    assert r["stage_index"]==2 and r["stages"][1]["status"]=="skipped"
    assert r["stages"][1]["skip"][0]["actual"]=="12.50"
    r=vote(suite,r)
    assert r["status"]=="approved"
    assert sum(t["status"]=="cancelled" for t in r["tasks"])==1
    t=template(suite,[stage(suite),stage(suite,sid="s_last")],rules={"distinct_deciders":True})
    r=vote(suite,request(suite,t))
    assert r["status"]=="stalled" and not r["tasks"]


def test_return_revision_reapproval_reject_withdraw_and_waits(suite):
    r=request(suite,template(suite))
    wait=call(suite,"request.wait",{"request_id":r["id"],"revision":1})
    assert wait["status"]=="waiting_approval"
    old_task=r["tasks"][0]["id"]
    r=vote(suite,r,decision="return")
    assert r["status"]=="returned"
    assert suite.people.store.one("SELECT status FROM op_runs WHERE id=?",(wait["run_id"],))["status"]=="waiting_approval"
    r=out(suite,"request.revise",change(r,values={"f_amount":"99.99"},basis="changed",note="Correct invoice"))
    assert r["revision"]==2 and r["previous"]["fields"]["f_amount"]=="12.50"
    assert r["snapshot"]["basis_hash"]!=r["previous"]["basis_hash"]
    assert suite.people.store.one("SELECT status FROM op_runs WHERE id=?",(wait["run_id"],))["status"]=="cancelled"
    error("independent_approver_required",lambda:out(suite,"request.decide",change(r,task_id=old_task,decision="approve"),user="bob"))
    r=vote(suite,r,decision="reject")
    assert r["status"]=="rejected"
    error("request_closed",lambda:out(suite,"request.withdraw",change(r)))
    r=out(suite,"request.revise",change(r,values={"f_amount":"2"},basis="third",note="New evidence"))
    r=vote(suite,r)
    assert r["status"]=="approved" and r["revision"]==3
    r=out(suite,"request.revise",change(r,values={"f_amount":"3"},basis="fourth",note="Material change"))
    assert r["status"]=="in_review" and r["revision"]==4
    assert out(suite,"request.withdraw",change(r))["status"]=="withdrawn"


def test_no_approver_revocation_transfer_repair(suite):
    add_oscar(suite)
    # A published explicit approver can leave before a request starts. It must stall.
    t=template(suite)
    suite.people.store.execute("UPDATE memberships SET role='viewer' WHERE workspace_id=? AND user_id=?",(suite.people.a,suite.people.uid("bob")))
    r=request(suite,t)
    assert r["status"]=="stalled"
    error("independent_manager_required",lambda:out(suite,"request.repair",change(r,users=[suite.people.uid("oscar")],note="Repair")))
    r=out(suite,"request.repair",change(r,users=[suite.people.uid("oscar")],note="Repair departed member"),user="oscar")
    assert r["status"]=="in_review"
    assert vote(suite,r,"oscar")["status"]=="approved"
    suite.people.store.execute("UPDATE memberships SET role='editor' WHERE workspace_id=? AND user_id=?",(suite.people.a,suite.people.uid("bob")))
    r=request(suite,t)
    error("invalid_approver",lambda:out(suite,"request.transfer",change(r,task_id=r["tasks"][0]["id"],to_user_id=suite.people.uid("alice"),note="Self"),user="bob"))
    r=out(suite,"request.transfer",change(r,task_id=r["tasks"][0]["id"],to_user_id=suite.people.uid("oscar"),note="Coverage"),user="bob")
    assert vote(suite,r,"oscar")["status"]=="approved"


def test_revoked_vote_does_not_lower_all_quorum(suite):
    add_oscar(suite)
    r=vote(suite,request(suite,template(suite,[stage(suite,("bob","oscar"))])))
    suite.people.store.execute("UPDATE users SET status='suspended' WHERE id=?",(suite.people.uid("bob"),))
    r=vote(suite,r,"oscar")
    assert r["status"]=="stalled"
    assert r["stages"][0]["required"]==2
    assert [t["status"] for t in r["tasks"]].count("revoked")==1
    assert suite.people.store.one("SELECT COUNT(*) AS n FROM app_events WHERE type='approvals.request.approved'")["n"]==0


@pytest.mark.parametrize("race",["approve_withdraw","last_votes","duplicate"])
def test_concurrent_actions(suite,race):
    add_oscar(suite)
    r=request(suite,template(suite,[stage(suite,("bob","oscar") if race=="last_votes" else ("bob",))]))
    gate=threading.Barrier(2)
    def action(n):
        gate.wait()
        try:
            if race=="approve_withdraw" and n:
                return ("ok",out(suite,"request.withdraw",change(r)))
            return ("ok",vote(suite,r,"oscar" if race=="last_votes" and n else "bob",key="same-click" if race=="duplicate" else None))
        except ServiceError as e:
            return (e.code,None)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(action,[0,1]))
    fresh=out(suite,"request.get",{"request_id":r["id"]})
    if race=="duplicate":
        assert [x[0] for x in results]==["ok","ok"] and len(fresh["decisions"])==1
    else:
        assert sorted(x[0] for x in results)==["ok","state_conflict"]
        if race=="last_votes":
            pending=next(x for x in fresh["tasks"] if x["status"]=="pending")
            user="bob" if pending["assignee_id"]==suite.people.uid("bob") else "oscar"
            fresh=vote(suite,fresh,user)
            assert fresh["status"]=="approved" and len(fresh["decisions"])==2
        else:
            assert fresh["status"] in ("approved","withdrawn")
    events=suite.people.store.all("SELECT * FROM app_events WHERE object_id=? AND type IN ('approvals.request.approved','approvals.request.withdrawn')",(r["id"],))
    assert len(events)==1


def test_restart_waits_reminders_escalation_timeout_and_revocation(suite):
    add_oscar(suite)
    t=template(suite,[stage(suite,due={"hours":1,"remind":True,"escalation":{"after_hours":1,"action":"add_approvers","approvers":{"users":[suite.people.uid("oscar")]}}})])
    r=request(suite,t)
    w=call(suite,"request.wait",{"request_id":r["id"],"revision":1,"timeout_hours":3})
    # New service instance over the same persisted store: no in-memory wait survives/requires it.
    s=Approvals(suite.people.store,suite.people.ident,suite.caps)
    due=r["stages"][0]["due_at"]
    s.sweep(due+3601);s.sweep(due+3601)
    fresh=out(suite,"request.get",{"request_id":r["id"]})
    assert len(fresh["tasks"])==2 and fresh["stages"][0]["reminded_at"]
    for kind in ("stage.reminder","stage.escalated"):
        assert suite.people.store.one("SELECT COUNT(*) AS n FROM app_events WHERE object_id=? AND type=?",(r["id"],"approvals."+kind))["n"]==1
    assert out(suite,"alerts")["total"]==2
    s.sweep(due+7201)
    run=suite.people.store.one("SELECT * FROM op_runs WHERE id=?",(w["run_id"],))
    assert run["status"]=="failed" and run["error_code"]=="approval_timeout"
    assert out(suite,"request.get",{"request_id":r["id"]})["status"]=="in_review"
    w=call(suite,"request.wait",{"request_id":r["id"],"revision":1},user="bob")
    suite.people.store.execute("UPDATE memberships SET role='viewer' WHERE workspace_id=? AND user_id=?",(suite.people.a,suite.people.uid("bob")))
    s.sweep()
    assert suite.people.store.one("SELECT error_code FROM op_runs WHERE id=?",(w["run_id"],))["error_code"]=="access_revoked"
    fresh=out(suite,"request.get",{"request_id":r["id"]})
    w=call(suite,"request.wait",{"request_id":r["id"],"revision":1})
    vote(suite,fresh,"oscar")
    assert suite.people.store.one("SELECT status FROM op_runs WHERE id=?",(w["run_id"],))["status"]=="succeeded"


def test_role_resolution_excludes_requester_and_threshold(suite):
    add_oscar(suite)
    t=template(suite,[{"id":"s_group","name":"Role group","mode":"all","approvers":{"min_role":"editor"}}])
    r=request(suite,t)
    assert r["status"]=="in_review" and len(r["tasks"])==2 and r["stages"][0]["required"]==2
    assert vote(suite,vote(suite,r),"oscar")["status"]=="approved"
    t=template(suite,[stage(suite,("bob","oscar"),"n_of_m",required=1)])
    assert vote(suite,request(suite,t))["status"]=="approved"
    # Explicitly selecting only the requester never becomes an empty automatic approval.
    t=template(suite,[stage(suite,("alice",))])
    r=request(suite,t)
    assert r["status"]=="stalled" and r["stages"][0]["required"]==1


def test_publish_and_snapshot_input_validation(suite):
    from synwevo_suite.approvals.definition import Definition, check_values
    d=Definition.model_validate({"fields":[{"id":"f_amount","type":"money","currency":"USD","label":"Money"}],"stages":[]})
    for value in ("0.001","NaN","1e200","9999999999999999"):
        assert check_values(d,{"f_amount":value})[1]
    t=out(suite,"template.create",{"title":"Invalid"})
    error("not_publishable",lambda:out(suite,"template.publish",{"template_id":t["id"],"draft_rev":1}))
    t=out(suite,"template.create",{"title":"Foreign approver","definition":{"stages":[stage(suite,("out",))]}})
    error("invalid_approver",lambda:out(suite,"template.publish",{"template_id":t["id"],"draft_rev":1}))
    t=template(suite)
    for value in (True,1.2,{"bad":"nested"}):
        error("invalid_input",lambda:request(suite,t,values={"f_amount":value}))
    error("invalid_values",lambda:request(suite,t,values={"f_amount":"-0.001"}))
    error("invalid_values",lambda:request(suite,t,values={"unknown":"x","f_amount":"2"}))


def test_revocation_between_registry_authorization_and_transaction(suite,monkeypatch):
    r=request(suite,template(suite))
    authorize=suite.caps.authorize
    calls=0
    def revoke_after_initial_check(operation,uid,wid):
        nonlocal calls
        checked=authorize(operation,uid,wid)
        calls+=1
        if calls==1:
            suite.people.store.execute("UPDATE memberships SET role='viewer' WHERE workspace_id=? AND user_id=?",(wid,uid))
        return checked
    monkeypatch.setattr(suite.caps,"authorize",revoke_after_initial_check)
    error("read_only_role",lambda:vote(suite,r))
    assert suite.people.store.one("SELECT COUNT(*) AS n FROM approval_decisions")["n"]==0


def test_real_factory_restart_and_event_replay(suite,tmp_path,monkeypatch):
    from synwevo_suite.app import create_app
    r=request(suite,template(suite))
    w=call(suite,"request.wait",{"request_id":r["id"],"revision":1},key="durable-wait")
    # Rebuild the complete app and registry; Store reapplies the same additive migrations.
    fresh=create_app(str(tmp_path / "d"),start_jobs=False)
    assert fresh.state.identity.store is not suite.people.store
    caps=fresh.state.capabilities
    payload=change(r,task_id=r["tasks"][0]["id"],decision="approve")
    args={"user_id":suite.people.uid("bob"),"workspace_id":suite.people.a,"idempotency_key":"survive-restart"}
    assert caps.invoke("approvals.request.decide",payload,**args)["output"]["status"]=="approved"
    # Another restart plus retry cannot produce a second approval event.
    restarted=create_app(str(tmp_path / "d"),start_jobs=False)
    assert restarted.state.capabilities.invoke("approvals.request.decide",payload,**args)["replayed"]
    store=restarted.state.identity.store
    assert store.one("SELECT status FROM op_runs WHERE id=?",(w["run_id"],))["status"]=="succeeded"
    assert store.one("SELECT COUNT(*) AS n FROM app_events WHERE type='approvals.request.approved' AND object_id=?",(r["id"],))["n"]==1
    assert store.one("SELECT COUNT(*) AS n FROM approval_decisions WHERE request_id=?",(r["id"],))["n"]==1


def test_list_views_pagination_history_and_isolation(suite):
    t=template(suite)
    rows=[request(suite,t,title=f"Fixture {n}") for n in range(3)]
    page=out(suite,"request.list",{"view":"sent","limit":2})
    assert page["total"]==3 and page["next_offset"]==2
    tail=out(suite,"request.list",{"view":"sent","limit":2,"offset":2})
    assert len(tail["items"])==1 and not set(x["id"] for x in page["items"]) & {tail["items"][0]["id"]}
    assert out(suite,"request.list",{"view":"todo"},user="bob")["total"]==3
    vote(suite,rows[0])
    assert out(suite,"request.list",{"view":"handled"},user="bob")["total"]==1
    assert out(suite,"request.list",{"view":"sent"},user="bob")["total"]==0
    history=out(suite,"request.timeline",{"request_id":rows[0]["id"],"limit":1})
    seen=[]
    while True:
        seen.extend(x["seq"] for x in history["items"])
        if history["next_after"] is None: break
        history=out(suite,"request.timeline",{"request_id":rows[0]["id"],"limit":1,"after":history["next_after"]})
    assert len(seen)==len(set(seen))>=5
    error("request_not_found",lambda:out(suite,"request.timeline",{"request_id":rows[0]["id"]},user="oscar",ws=suite.people.b))
    error("request_not_found",lambda:out(suite,"request.revision",{"request_id":rows[0]["id"],"revision":1},user="oscar",ws=suite.people.b))


def test_wait_cannot_leak_final_content_after_access_revocation(suite):
    r=request(suite,template(suite))
    w=call(suite,"request.wait",{"request_id":r["id"],"revision":1},user="bob")
    suite.people.store.execute("UPDATE users SET status='suspended' WHERE id=?",(suite.people.uid("bob"),))
    suite.api.state.approvals.sweep()
    result=suite.people.store.one("SELECT * FROM op_runs WHERE id=?",(w["run_id"],))
    assert result["status"]=="failed" and result["error_code"]=="access_revoked"
    assert "decision" not in result["output"]


def guard_call(suite,row):
    """Construct the coordinator's Call contract from a stored operation, without running it."""
    from synwevo_core.capabilities import Call
    op=suite.caps.registry.get(row["op"])
    user,role=suite.caps.authorize(op,row["user_id"],row["workspace_id"])
    return Call(store=suite.people.store,identity=suite.people.ident,op=op,user=user,role=role,
        workspace_id=row["workspace_id"],run_id=row["id"],via=row["via"],correlation_id=row["correlation_id"],
        principal={},capabilities=suite.caps)


@pytest.mark.parametrize("encoded",[False,True])
def test_write_result_guard_rechecks_complete_projection_without_writing(suite,encoded):
    import json
    t=template(suite)
    result=call(suite,"request.create",{"template_id":t["id"],"title":"Guard fixture","values":{"f_amount":"4"}},key="guarded-create")
    row=suite.people.store.one("SELECT * FROM op_runs WHERE id=?",(result["run_id"],))
    c=guard_call(suite,row)
    for k in ("input","output"):
        parsed=json.loads(row[k]) if isinstance(row[k],str) else row[k]
        row[k]=json.dumps(parsed) if encoded else parsed
    counts=lambda:tuple(suite.people.store.one(f"SELECT COUNT(*) AS n FROM {table}")["n"] for table in ("op_runs","app_events","approval_decisions","approval_requests"))
    before=counts()
    assert c.op.result_guard is not None
    c.op.result_guard(c,row)
    assert counts()==before
    # Same workspace and same template still exist, but an old output now has stale state,
    # permissions and tasks. It must not be exposed as the current result of the write.
    vote(suite,result["output"])
    before=counts()
    error("approval_result_unavailable",lambda:c.op.result_guard(c,row))
    assert counts()==before


def test_write_result_guard_rejects_revoked_account_workspace_and_unproven_rows(suite):
    t=template(suite)
    result=call(suite,"request.create",{"template_id":t["id"],"title":"Guard fixture","values":{"f_amount":"4"}})
    row=suite.people.store.one("SELECT * FROM op_runs WHERE id=?",(result["run_id"],))
    c=guard_call(suite,row)
    for replacement in ({"workspace_id":suite.people.b},{"user_id":suite.people.uid("bob")},{"output":None},{"output":"not json"},{"input":"not json"}):
        error("approval_result_unavailable",lambda:c.op.result_guard(c,{**row,**replacement}))
    suite.people.store.execute("UPDATE memberships SET role='viewer' WHERE workspace_id=? AND user_id=?",(suite.people.a,suite.people.uid("alice")))
    error("read_only_role",lambda:c.op.result_guard(c,row))
    suite.people.store.execute("UPDATE users SET status='suspended' WHERE id=?",(suite.people.uid("alice"),))
    error("account_unavailable",lambda:c.op.result_guard(c,row))


def test_template_guard_hides_old_draft_and_all_writes_declare_guard(suite):
    for operation in suite.caps.registry.operations("approvals"):
        assert bool(operation.result_guard)==(operation.side_effect!="none")
    t=template(suite)
    result=call(suite,"template.update",{"template_id":t["id"],"draft_rev":1,"title":"First edit","definition":t["draft"]})
    row=suite.people.store.one("SELECT * FROM op_runs WHERE id=?",(result["run_id"],))
    c=guard_call(suite,row)
    c.op.result_guard(c,row)
    out(suite,"template.update",{"template_id":t["id"],"draft_rev":2,"title":"Second edit","definition":t["draft"]})
    error("approval_result_unavailable",lambda:c.op.result_guard(c,row))


def test_settled_wait_guard_and_immediate_cancel_agree(suite):
    r=request(suite,template(suite))
    waiting=call(suite,"request.wait",{"request_id":r["id"],"revision":1})
    row=suite.people.store.one("SELECT * FROM op_runs WHERE id=?",(waiting["run_id"],))
    c=guard_call(suite,row)
    c.op.result_guard(c,row)
    r=out(suite,"request.withdraw",change(r))
    row=suite.people.store.one("SELECT * FROM op_runs WHERE id=?",(waiting["run_id"],))
    c.op.result_guard(c,row)
    assert row["status"]==call(suite,"request.wait",{"request_id":r["id"],"revision":1})["status"]=="cancelled"


def _has_coordinator_guard_contract():
    from synwevo_core.capabilities import Operation,Capabilities
    return "result_guard" in Operation.__dataclass_fields__ and hasattr(Capabilities,"inspect")


@pytest.mark.skipif(not _has_coordinator_guard_contract(),reason="Coordinator's core result_guard/inspect integration is not in this delegated worktree")
def test_integrated_history_and_idempotency_never_reexecute_or_expose_revoked_results(suite):
    t=template(suite)
    payload={"template_id":t["id"],"title":"Replay ACL fixture","values":{"f_amount":"5"}}
    result=call(suite,"request.create",payload,key="guarded-intent")
    assert call(suite,"request.create",payload,key="guarded-intent")["replayed"]
    vote(suite,result["output"])
    before=suite.people.store.one("SELECT COUNT(*) AS n FROM approval_requests")["n"]
    # Changed write outputs are refused; neither returning old content nor replaying the
    # original mutation is an acceptable way to satisfy this retry.
    def hidden_or_refused(fn):
        try:
            response=fn()
        except ServiceError:
            return
        assert not response.get("output") and not response.get("input")
    hidden_or_refused(lambda:call(suite,"request.create",payload,key="guarded-intent"))
    hidden_or_refused(lambda:suite.caps.run(result["run_id"],user_id=suite.people.uid("alice")))
    suite.people.store.execute("UPDATE memberships SET role='viewer' WHERE workspace_id=? AND user_id=?",(suite.people.a,suite.people.uid("alice")))
    hidden_or_refused(lambda:call(suite,"request.create",payload,key="guarded-intent"))
    hidden_or_refused(lambda:suite.caps.run(result["run_id"],user_id=suite.people.uid("alice")))
    assert suite.people.store.one("SELECT COUNT(*) AS n FROM approval_requests")["n"]==before
    assert suite.people.store.one("SELECT COUNT(*) AS n FROM approval_decisions WHERE request_id=?",(result["output"]["id"],))["n"]==1


def test_guard_hides_old_eligibility_before_sweeper_updates_task(suite):
    t=template(suite)
    result=call(suite,"request.create",{"template_id":t["id"],"title":"Eligibility fixture","values":{"f_amount":"5"}})
    row=suite.people.store.one("SELECT * FROM op_runs WHERE id=?",(result["run_id"],))
    c=guard_call(suite,row)
    c.op.result_guard(c,row)
    suite.people.store.execute("UPDATE memberships SET role='viewer' WHERE workspace_id=? AND user_id=?",(suite.people.a,suite.people.uid("bob")))
    # The requester still has full access, and the task row is still pending. Live eligibility
    # in the old projection is false now; no background reconciliation is needed to refuse it.
    error("approval_result_unavailable",lambda:c.op.result_guard(c,row))
    assert suite.people.store.one("SELECT status FROM approval_tasks WHERE id=?",(result["output"]["tasks"][0]["id"],))["status"]=="pending"


def test_a_member_without_a_display_name_does_not_break_the_approver_list(suite):
    p=suite.people
    nameless=p.ident.create_user("nameless@example.test","harbor lights guide the evening ferry home",status="active")
    assert nameless.get("display_name") is None
    p.ident.add_member(p.a,nameless["id"],"editor")
    everyone=out(suite,"members")
    assert nameless["id"] in {m["id"] for m in everyone["items"]} and p.uid("bob") in {m["id"] for m in everyone["items"]}
    named=out(suite,"members",{"q":"bob"})
    assert {m["id"] for m in named["items"]}=={p.uid("bob")}


def test_a_requests_correlation_is_not_handed_to_other_members(suite):
    """Review R4: approvals.request.list and .get returned whole rows, so every member who can read
    approvals saw the requester's correlation — free text they chose for their own tracing, or
    their own run id. Requests are read without it, as Forms requests are."""
    c=suite
    t=template(c)
    named=c.caps.invoke("approvals.request.create",{"template_id":t["id"],"title":"Fixture traced claim","values":{"f_amount":"9.00"},
                                                    "basis":"Invoice fixture"},user_id=c.people.uid("alice"),workspace_id=c.people.a,
                         correlation_id="fixture-alice-private-trace")["output"]
    plain=request(c,t)
    stored={r["correlation_id"] for r in c.people.store.all("SELECT correlation_id FROM approval_requests")}
    assert "fixture-alice-private-trace" in stored, "the requester's correlation is still kept for her runs"
    for who in ("bob","alice"):
        listed=out(c,"request.list",{"view":"all"},user=who)["items"]
        assert {x["id"] for x in listed}>={named["id"],plain["id"]} and all("correlation_id" not in x for x in listed), who
        for rid in (named["id"],plain["id"]):
            assert "correlation_id" not in out(c,"request.get",{"request_id":rid},user=who), (who,rid)


def test_a_result_stored_before_requests_lost_their_correlation_still_replays(suite):
    """Review R5: requests are read without the requester's correlation now, but results stored
    before that still hold it; compared with the request as it reads now they no longer matched,
    and the idempotent replay answered run_result_unavailable instead of the request."""
    import json as _json
    c=suite
    t=template(c)
    payload={"template_id":t["id"],"title":"Fixture claim","values":{"f_amount":"12.50"},"basis":"Invoice fixture"}
    first=call(c,"request.create",payload,key="fixture-replay-k1")
    row=c.people.store.one("SELECT output, correlation_id FROM op_runs WHERE id=?",(first["run_id"],))
    old=dict(row["output"]) if isinstance(row["output"],dict) else _json.loads(row["output"])
    old["correlation_id"]=row["correlation_id"]          # the shape such a result was stored in
    c.people.store.execute("UPDATE op_runs SET output=? WHERE id=?",(_json.dumps(old),first["run_id"]))
    again=call(c,"request.create",payload,key="fixture-replay-k1")
    assert again["run_id"]==first["run_id"] and again["output"]["id"]==first["output"]["id"]
    assert c.people.store.one("SELECT COUNT(*) AS n FROM approval_requests")["n"]==1


def test_an_approvers_result_stored_with_the_requesters_correlation_stays_hidden(suite):
    """Review R6: results stored before requests lost their correlation hold the requester's; the
    stored result is replayed as it is, so an approver's decision result passed back to the
    approver would carry the requester's private tracing text. Only the requester's own replays."""
    import json as _json
    c=suite
    t=template(c)
    r=c.caps.invoke("approvals.request.create",{"template_id":t["id"],"title":"Fixture traced claim","values":{"f_amount":"9.00"},
                                                "basis":"Invoice fixture"},user_id=c.people.uid("alice"),workspace_id=c.people.a,
                     correlation_id="fixture-alice-private-trace")["output"]
    task=next(x for x in r["tasks"] if x["assignee_id"]==c.people.uid("bob") and x["status"]=="pending")
    payload=change(r,task_id=task["id"],decision="approve",note="Fixture review")
    first=call(c,"request.decide",payload,user="bob",key="fixture-bob-decide-k1")
    row=c.people.store.one("SELECT output FROM op_runs WHERE id=?",(first["run_id"],))
    old=dict(row["output"]) if isinstance(row["output"],dict) else _json.loads(row["output"])
    old["correlation_id"]="fixture-alice-private-trace"     # the shape such a result was stored in
    c.people.store.execute("UPDATE op_runs SET output=? WHERE id=?",(_json.dumps(old),first["run_id"]))
    error("run_result_unavailable",lambda: call(c,"request.decide",payload,user="bob",key="fixture-bob-decide-k1"))
    receipt=c.caps.run(first["run_id"],user_id=c.people.uid("bob"))
    assert "fixture-alice-private-trace" not in _json.dumps(receipt), receipt
