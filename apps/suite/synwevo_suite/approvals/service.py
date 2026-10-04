"""Transactional human approvals. Operations run under Store.tx (BEGIN IMMEDIATE).

Quorums are frozen when a stage opens. Losing an approver never reduces a quorum; it stalls
until a manager supplies eligible replacements. Requesters cannot decide, transfer to themselves,
or repair their own approvals. Decisions never migrate between content revisions. Published
versions being retired/archived only blocks new requests; already pinned requests continue.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

from synwevo_core.capabilities import canonical, fingerprint, record_event
from synwevo_core.errors import ServiceError
from synwevo_core.store import new_id, now
from .definition import Definition, ROLE_RANK, check_values, evaluate, problems

OPEN = ("in_review", "stalled", "returned")
TERMINAL = ("approved", "rejected", "withdrawn")


def fail(code, message, status=409, detail=None):
    raise ServiceError(status, code, message, detail)


def decode(row):
    out = dict(row)
    for key in list(out):
        if key.endswith("_json"):
            value = out.pop(key)
            out[key[:-5]] = json.loads(value) if value is not None else None
    return out


def _without_correlation(request):
    """A request as members read it. Its stored correlation is the requester's own — free text
    they chose or their own run id — and reaches nobody else (review R4: list/get handed it to
    every member who can read approvals); Forms requests never return theirs either."""
    request.pop("correlation_id", None)
    return request


class Approvals:
    def __init__(self, store, identity, capabilities):
        self.store, self.identity, self.caps = store, identity, capabilities

    def _auth(self, call, need=None):
        # The registry's first check precedes its transaction. Recheck after the write lock so
        # a revocation that wins that lock cannot be followed by a previously authorized write.
        call.user, call.role = self.caps.authorize(call.op, call.user_id, call.workspace_id)
        if need:
            self.caps.check_workspace(call.user_id, "approvals", call.workspace_id, need)

    def result_guard(self, call, row):
        """Allow an old write result only while its complete projection is still readable and
        identical. Never invoke an operation, decide again, emit an event, or mutate business data.

        Approvals has workspace-wide reading (no field ACL or external-source dereferencing).
        Every snapshot is an independent copy owned by this workspace. Current workspace/app
        access, the original run owner, object existence and projection equality are all required.
        A changed draft, revision, status, assignment, name or eligibility hides the old result.
        The coordinator calls this before *any* history or idempotency result leaves core.
        """
        def hidden():
            fail("approval_result_unavailable", "历史结果已改变或无法重新验证，请查看当前审批记录", 403)

        with self.store.tx():
            self._auth(call)
            if (row.get("user_id") != call.user_id or row.get("workspace_id") != call.workspace_id
                    or row.get("op") != call.op.id or row.get("app") != "approvals"):
                hidden()
            try:
                output = json.loads(row["output"]) if isinstance(row.get("output"), str) else row.get("output")
                payload = json.loads(row["input"]) if isinstance(row.get("input"), str) else row.get("input")
            except (ValueError, TypeError):
                hidden()
            if not isinstance(output, dict) or not isinstance(payload, dict):
                hidden()
            name = call.op.id.removeprefix("approvals.")
            if name.startswith("template."):
                tid = output.get("id")
                if not tid or (payload.get("template_id") and payload["template_id"] != tid):
                    hidden()
                current = self.template(call, SimpleNamespace(template_id=tid))
            elif name == "request.comment":
                rid = output.get("request_id")
                if not rid or payload.get("request_id") != rid:
                    hidden()
                r = self._request(call.workspace_id, rid)
                current = {"request_id": rid, "state_version": r["state_version"]}
            elif name == "request.wait":
                rid = output.get("request_id")
                if not rid or payload.get("request_id") != rid:
                    hidden()
                r = self._request(call.workspace_id, rid)
                # A cancellation/timeout reason is safe after checking the exact stored wait.
                wait = self.store.one("SELECT * FROM approval_waits WHERE run_id=? AND workspace_id=? AND user_id=?",
                                      (row["id"], call.workspace_id, call.user_id))
                if output.get("reason"):
                    if not wait or wait["request_id"] != rid or wait["status"] != row["status"]:
                        hidden()
                    if output["reason"] not in ("revision_changed", "approval_timeout"):
                        hidden()
                    current = {"request_id": rid, "reason": output["reason"]}
                else:
                    if payload.get("revision") != r["revision"]:
                        hidden()
                    current = self._wait_output(r)
            elif name in ("request.create", "request.revise", "request.withdraw", "request.decide",
                          "request.transfer", "request.repair"):
                rid = output.get("id")
                if not rid or (payload.get("request_id") and payload["request_id"] != rid):
                    hidden()
                if output.get("workspace_id") != call.workspace_id:
                    hidden()
                current = self.get(call, SimpleNamespace(request_id=rid))
            else:
                hidden()
            if fingerprint(output) != fingerprint(current):
                hidden()

    def _eligible(self, wid, uid, requester=None, excluded=()):
        if uid == requester or uid in excluded:
            return False
        user = self.identity.get_user(uid)
        if not user or user["status"] != "active" or user.get("deleted_at"):
            return False
        try:
            self.caps.check_workspace(uid, "approvals", wid, "write")
        except ServiceError:
            return False
        return True

    def _template(self, wid, tid):
        row = self.store.one("SELECT * FROM approval_templates WHERE id=? AND workspace_id=?", (tid, wid))
        if not row:
            fail("template_not_found", "审批模板不存在", 404)
        return row

    def _version(self, wid, tid, version):
        row = self.store.one("SELECT * FROM approval_template_versions WHERE template_id=? AND workspace_id=? AND version=?",
                             (tid, wid, version))
        if not row:
            fail("version_not_found", "审批版本不存在", 404)
        return row

    def _request(self, wid, rid):
        row = self.store.one("SELECT * FROM approval_requests WHERE id=? AND workspace_id=?", (rid, wid))
        if not row:
            fail("request_not_found", "审批请求不存在", 404)
        return row

    def _def(self, r):
        return Definition.model_validate_json(self._version(r["workspace_id"], r["template_id"], r["template_version"])["definition_json"])

    def _page(self, table, where, args, i, order, columns="*"):
        total = self.store.one(f"SELECT COUNT(*) AS n FROM {table} WHERE {where}", tuple(args))["n"]
        items = self.store.all(f"SELECT {columns} FROM {table} WHERE {where} ORDER BY {order} LIMIT ? OFFSET ?",
                               (*args, i.limit, i.offset))
        return {"items": [decode(r) for r in items], "total": total,
                "next_offset": i.offset + len(items) if i.offset + len(items) < total else None}

    def members(self, c, i):
        self._auth(c)
        # All candidates are authorized; no inactive account, viewer or revoked entitlement can
        # become an approver simply because their identifier was supplied by a browser.
        rows = self.store.all("SELECT u.id,u.display_name,m.role FROM memberships m JOIN users u ON u.id=m.user_id "
                              "WHERE m.workspace_id=? AND u.status='active' ORDER BY u.id", (c.workspace_id,))
        # A display name is optional on an account; a member without one is still a candidate.
        rows = [r for r in rows if self._eligible(c.workspace_id, r["id"]) and i.q.casefold() in (r["display_name"] or "").casefold()]
        return {"items": rows[i.offset:i.offset+i.limit], "total": len(rows),
                "next_offset": i.offset+i.limit if i.offset+i.limit < len(rows) else None}

    def templates(self, c, i):
        self._auth(c)
        return self._page("approval_templates", "workspace_id=? AND status=? AND instr(lower(title),lower(?))>0",
                          [c.workspace_id, i.status, i.q], i, "updated_at DESC,id", "id,title,description,status,published_version,updated_at")

    def template(self, c, i):
        self._auth(c)
        row = decode(self._template(c.workspace_id, i.template_id))
        row["versions"] = self.store.all("SELECT version,title,status,note,published_at,published_by,definition_hash "
            "FROM approval_template_versions WHERE template_id=? AND workspace_id=? ORDER BY version DESC",
            (i.template_id, c.workspace_id))
        row["problems"] = problems(Definition.model_validate(row["draft"]))
        return row

    def create_template(self, c, i):
        self._auth(c)
        tid, ts = new_id("apt"), now()
        self.store.execute("INSERT INTO approval_templates (id,workspace_id,title,description,status,draft_json,created_by,created_at,updated_at) "
            "VALUES (?,?,?,?,'active',?,?,?,?)", (tid,c.workspace_id,i.title,i.description,canonical(i.definition.model_dump()),c.user_id,ts,ts))
        c.touches("approval_template", tid)
        return self.template(c, SimpleNamespace(template_id=tid))

    def update_template(self, c, i):
        self._auth(c)
        row = self._template(c.workspace_id, i.template_id)
        if row["status"] != "active":
            fail("template_archived", "请先恢复已归档的模板")
        n = self.store.execute("UPDATE approval_templates SET title=?,description=?,draft_json=?,draft_rev=draft_rev+1,updated_at=? "
            "WHERE id=? AND workspace_id=? AND draft_rev=?", (i.title,i.description,canonical(i.definition.model_dump()),now(),i.template_id,c.workspace_id,i.draft_rev))
        if n != 1:
            fail("draft_conflict", "草稿已改变，请重新载入后保存")
        c.touches("approval_template", i.template_id)
        return self.template(c, i)

    def _validate_members(self, wid, defn):
        for stage in defn.stages:
            selectors = [stage.approvers]
            if stage.due and stage.due.escalation and stage.due.escalation.action == "add_approvers":
                selectors.append(stage.due.escalation.approvers)
            for selector in selectors:
                if any(not self._eligible(wid, uid) for uid in selector.users):
                    fail("invalid_approver", "指定的审批人必须具有本空间的有效编辑权限", 422, {"stage": stage.id})

    def publish(self, c, i):
        self._auth(c)
        row = self._template(c.workspace_id, i.template_id)
        if row["status"] != "active":
            fail("template_archived", "请先恢复已归档的模板")
        if row["draft_rev"] != i.draft_rev:
            fail("draft_conflict", "草稿已改变，请重新载入后发布")
        defn = Definition.model_validate_json(row["draft_json"])
        found = problems(defn)
        if found:
            fail("not_publishable", "请修正审批模板中的问题", 422, {"problems": found})
        self._validate_members(c.workspace_id, defn)
        last = self.store.one("SELECT MAX(version) AS v FROM approval_template_versions WHERE template_id=? AND workspace_id=?",
                              (i.template_id,c.workspace_id))["v"] or 0
        digest = fingerprint(defn.model_dump())
        if last:
            prev = self._version(c.workspace_id, i.template_id, last)
            if prev["definition_hash"] == digest and prev["title"] == row["title"] and prev["status"] == "published":
                fail("nothing_to_publish", "内容与最新版本相同")
        self.store.execute("INSERT INTO approval_template_versions (template_id,version,workspace_id,definition_json,definition_hash,title,status,note,published_by,published_at) "
            "VALUES (?,?,?,?,?,?,'published',?,?,?)", (i.template_id,last+1,c.workspace_id,row["draft_json"],digest,row["title"],i.note,c.user_id,now()))
        self.store.execute("UPDATE approval_templates SET published_version=?,updated_at=? WHERE id=? AND workspace_id=?",
                           (last+1,now(),i.template_id,c.workspace_id))
        c.touches("approval_template",i.template_id)
        c.emit("approvals.template.published",object_kind="approval_template",object_id=i.template_id,data={"template_id":i.template_id,"version":last+1})
        return self.template(c,i)

    def version(self,c,i):
        self._auth(c)
        return decode(self._version(c.workspace_id,i.template_id,i.version))

    def retire(self,c,i):
        self._auth(c)
        self._version(c.workspace_id,i.template_id,i.version)
        self.store.execute("UPDATE approval_template_versions SET status='retired',retired_at=? WHERE template_id=? AND workspace_id=? AND version=? AND status='published'",
                           (now(),i.template_id,c.workspace_id,i.version))
        v = self.store.one("SELECT MAX(version) AS v FROM approval_template_versions WHERE template_id=? AND workspace_id=? AND status='published'",(i.template_id,c.workspace_id))["v"]
        self.store.execute("UPDATE approval_templates SET published_version=?,updated_at=? WHERE id=? AND workspace_id=?",(v,now(),i.template_id,c.workspace_id))
        c.touches("approval_template",i.template_id)
        return self.template(c,i)

    def archive(self,c,i):
        self._auth(c)
        self._template(c.workspace_id,i.template_id)
        self.store.execute("UPDATE approval_templates SET status=?,archived_at=?,updated_at=? WHERE id=? AND workspace_id=?",
                           ("archived" if i.archived else "active",now() if i.archived else None,now(),i.template_id,c.workspace_id))
        c.touches("approval_template",i.template_id)
        return self.template(c,i)

    def _record(self,r,kind,detail=None,actor=None,c=None):
        detail = detail or {}
        self.store.execute("INSERT INTO approval_timeline (id,request_id,workspace_id,at,actor_id,kind,revision,stage_index,detail_json) "
            "VALUES (?,?,?,?,?,?,?,?,?)", (new_id("atl"),r["id"],r["workspace_id"],now(),actor or (c.user_id if c else None),kind,r["revision"],r.get("stage_index"),canonical(detail)))
        payload = {"request_id":r["id"],"revision":r["revision"],"state_version":r["state_version"],"status":r["status"],**detail}
        if c:
            c.emit("approvals."+kind,object_kind="approval_request",object_id=r["id"],data=payload)
        else:
            with self.store.tx() as t:
                event = record_event(t,type_="approvals."+kind,app="approvals",workspace_id=r["workspace_id"],object_kind="approval_request",
                    object_id=r["id"],data=payload,correlation_id=r["correlation_id"])
                self.store.after_commit(lambda: self.caps.announce([event]))

    def _bump(self,r,**changes):
        n = self.store.execute("UPDATE approval_requests SET state_version=state_version+1,updated_at=?"+
            "".join(","+key+"=?" for key in changes)+" WHERE id=? AND workspace_id=? AND state_version=?",
            (now(),*changes.values(),r["id"],r["workspace_id"],r["state_version"]))
        if n != 1:
            fail("state_conflict","审批状态已改变，请重新载入")
        r.update(changes)
        r["state_version"] += 1

    def _snapshot(self,r,i,c):
        values,errors = check_values(self._def(r),i.values)
        if errors:
            fail("invalid_values","请修正申请内容",422,{"errors":errors})
        self.store.execute("INSERT INTO approval_revisions (request_id,revision,workspace_id,fields_json,fields_hash,basis_json,basis_hash,note,submitted_by,submitted_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",(r["id"],r["revision"],r["workspace_id"],canonical(values),fingerprint(values),canonical(i.basis),fingerprint(i.basis),getattr(i,"note",""),c.user_id,now()))

    def _excluded(self,r):
        if not self._def(r).rules.distinct_deciders:
            return set()
        return {x["decider_id"] for x in self.store.all("SELECT decider_id FROM approval_decisions WHERE request_id=? AND workspace_id=? AND revision=? AND stage_run_id!=?",
            (r["id"],r["workspace_id"],r["revision"],r.get("stage_run_id") or ""))}

    def _resolve(self,r,selector):
        desired = set(selector.users)
        excluded = self._excluded(r)
        if selector.min_role:
            rows = self.store.all("SELECT user_id,role FROM memberships WHERE workspace_id=?",(r["workspace_id"],))
            desired.update(x["user_id"] for x in rows if ROLE_RANK.get(x["role"],-1)>=ROLE_RANK[selector.min_role]
                           and self._eligible(r["workspace_id"],x["user_id"],r["requester_id"],excluded))
        valid = {u for u in desired if self._eligible(r["workspace_id"],u,r["requester_id"],excluded)}
        if len(desired)>50:
            fail("too_many_approvers","审批人超过 50 人，请收窄角色范围",422)
        return desired,valid

    def _task(self,r,uid,source,from_id=None):
        count = self.store.one("SELECT COUNT(*) AS n FROM approval_tasks WHERE request_id=? AND workspace_id=? AND stage_run_id=?",(r["id"],r["workspace_id"],r["stage_run_id"]))["n"]
        if count >= 500:
            fail("task_limit","该阶段已达到任务上限，请撤回后发起新申请",422)
        self.store.execute("INSERT INTO approval_tasks (id,request_id,workspace_id,stage_run_id,assignee_id,status,source,transferred_from,created_at) "
            "VALUES (?,?,?,?,?,'pending',?,?,?)",(new_id("atk"),r["id"],r["workspace_id"],r["stage_run_id"],uid,source,from_id,now()))

    def _tasks(self,r):
        return self.store.all("SELECT * FROM approval_tasks WHERE request_id=? AND workspace_id=? AND stage_run_id=? ORDER BY created_at,id",
                              (r["id"],r["workspace_id"],r["stage_run_id"]))

    def _open(self,r,index,c=None):
        definition = self._def(r)
        values = json.loads(self.store.one("SELECT fields_json FROM approval_revisions WHERE request_id=? AND workspace_id=? AND revision=?",(r["id"],r["workspace_id"],r["revision"]))["fields_json"])
        for n in range(index,len(definition.stages)):
            stage,ts = definition.stages[n],now()
            held,checks = evaluate(stage.condition,definition,values) if stage.condition else (True,[])
            sid = new_id("ast")
            r.update(stage_index=n,stage_run_id=sid)
            due = ts+stage.due.hours*3600 if stage.due else None
            escalate = due+stage.due.escalation.after_hours*3600 if stage.due and stage.due.escalation else None
            desired,valid = self._resolve(r,stage.approvers) if held else (set(),set())
            required = max(1,len(desired)) if stage.mode=="all" else stage.required if stage.mode=="n_of_m" else 1
            state = "skipped" if not held else "active" if len(valid)>=required else "stalled"
            self.store.execute("INSERT INTO approval_stage_runs (id,request_id,workspace_id,revision,stage_index,stage_id,stage_name,mode,required,status,stall_reason,skip_json,opened_at,due_at,remind,escalate_at,closed_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",(sid,r["id"],r["workspace_id"],r["revision"],n,stage.id,stage.name,stage.mode,required,state,
                "no_eligible_approvers" if state=="stalled" else None,canonical(checks),ts,due,int(bool(stage.due and stage.due.remind)),escalate,ts if not held else None))
            if not held:
                self._record(r,"stage.skipped",{"stage_id":stage.id,"checks":checks},c=c)
                continue
            for uid in sorted(valid):
                self._task(r,uid,"explicit" if uid in stage.approvers.users else "role")
            self._bump(r,status="stalled" if state=="stalled" else "in_review",stage_index=n,stage_run_id=sid,finished_at=None)
            self._record(r,"stage.opened",{"stage_id":stage.id,"required":required,"assignees":sorted(valid)},c=c)
            if state=="stalled":
                self._record(r,"request.stalled",{"reason":"no_eligible_approvers"},c=c)
            return
        self._bump(r,status="approved",finished_at=now())
        self._record(r,"request.approved",c=c)
        self._settle(r)

    def create(self,c,i):
        self._auth(c)
        template = self._template(c.workspace_id,i.template_id)
        if template["status"]!="active":
            fail("template_archived","模板已归档")
        version = self._version(c.workspace_id,i.template_id,i.version or template["published_version"])
        if version["status"]!="published":
            fail("version_retired","该版本已停用")
        rid,ts = new_id("apr"),now()
        self.store.execute("INSERT INTO approval_requests (id,workspace_id,template_id,template_version,title,requester_id,status,correlation_id,created_at,updated_at) "
            "VALUES (?,?,?,?,?,?,'in_review',?,?,?)",(rid,c.workspace_id,i.template_id,version["version"],i.title,c.user_id,c.correlation_id,ts,ts))
        r = self._request(c.workspace_id,rid)
        self._snapshot(r,i,c)
        self._record(r,"request.created",c=c)
        self._open(r,0,c)
        c.touches("approval_request",rid)
        return self.get(c,SimpleNamespace(request_id=rid))

    def requests(self,c,i):
        self._auth(c)
        where,args = "workspace_id=? AND instr(lower(title),lower(?))>0",[c.workspace_id,i.q]
        if i.status:
            where+=" AND status=?"; args.append(i.status)
        if i.view=="sent":
            where+=" AND requester_id=?"; args.append(c.user_id)
        elif i.view=="todo":
            where+=" AND status IN ('in_review','stalled') AND EXISTS (SELECT 1 FROM approval_tasks t WHERE t.request_id=approval_requests.id AND t.stage_run_id=approval_requests.stage_run_id AND t.assignee_id=? AND t.status='pending')"
            args.append(c.user_id)
        elif i.view=="handled":
            where+=" AND EXISTS (SELECT 1 FROM approval_decisions d WHERE d.request_id=approval_requests.id AND d.decider_id=?)"; args.append(c.user_id)
        page = self._page("approval_requests",where,args,i,"updated_at DESC,id")
        page["items"] = [_without_correlation(x) for x in page["items"]]
        return page

    def get(self,c,i):
        self._auth(c)
        r = self._request(c.workspace_id,i.request_id)
        out = _without_correlation(decode(r))
        out["definition"] = self._def(r).model_dump()
        out["revisions"] = self.store.all("SELECT revision,fields_hash,basis_hash,note,submitted_by,submitted_at FROM approval_revisions WHERE request_id=? AND workspace_id=? ORDER BY revision",(r["id"],c.workspace_id))
        out["snapshot"] = self.revision(c,SimpleNamespace(request_id=r["id"],revision=r["revision"]))
        out["previous"] = self.revision(c,SimpleNamespace(request_id=r["id"],revision=r["revision"]-1)) if r["revision"]>1 else None
        out["stages"] = [decode(x) for x in self.store.all("SELECT * FROM approval_stage_runs WHERE request_id=? AND workspace_id=? AND revision=? ORDER BY stage_index",(r["id"],c.workspace_id,r["revision"]))]
        out["tasks"] = self._tasks(r) if r["stage_run_id"] else []
        excluded = self._excluded(r)
        for task in out["tasks"]:
            task["eligible"] = self._eligible(c.workspace_id,task["assignee_id"],r["requester_id"],excluded)
        out["decisions"] = self.store.all("SELECT id,revision,stage_run_id,task_id,decider_id,decision,created_at FROM approval_decisions WHERE request_id=? AND workspace_id=? AND revision=? ORDER BY created_at,id",(r["id"],c.workspace_id,r["revision"]))
        people = {r["requester_id"]} | {x["assignee_id"] for x in out["tasks"]} | {x["decider_id"] for x in out["decisions"]}
        out["people"] = {uid:(self.identity.get_user(uid) or {}).get("display_name",uid) for uid in people}
        out["permissions"] = {"requester":r["requester_id"]==c.user_id and c.can("write"),"manage":c.can("manage") and r["requester_id"]!=c.user_id,
            "task_ids":[t["id"] for t in out["tasks"] if t["assignee_id"]==c.user_id and t["status"]=="pending" and t["eligible"]]}
        return out

    def revision(self,c,i):
        self._auth(c)
        self._request(c.workspace_id,i.request_id)
        row = self.store.one("SELECT * FROM approval_revisions WHERE request_id=? AND workspace_id=? AND revision=?",(i.request_id,c.workspace_id,i.revision))
        if not row:
            fail("revision_not_found","申请修订不存在",404)
        return decode(row)

    def history(self,c,i):
        self._auth(c)
        self._request(c.workspace_id,i.request_id)
        rows = self.store.all("SELECT * FROM approval_timeline WHERE request_id=? AND workspace_id=? AND seq>? ORDER BY seq LIMIT ?",(i.request_id,c.workspace_id,i.after,i.limit+1))
        items = rows[:i.limit]
        return {"items":[{**decode(x),"actor_name":(self.identity.get_user(x["actor_id"]) or {}).get("display_name","") if x["actor_id"] else ""} for x in items],"next_after":items[-1]["seq"] if len(rows)>i.limit else None}

    def _change(self,c,i,statuses=OPEN):
        self._auth(c)
        r = self._request(c.workspace_id,i.request_id)
        if r["state_version"]!=i.state_version:
            fail("state_conflict","审批状态已改变，请重新载入",detail={"state_version":r["state_version"]})
        if r["status"] not in statuses:
            fail("request_closed","这个状态不能进行该操作")
        c.touches("approval_request",r["id"])
        return r

    def _owner(self,c,r):
        if r["requester_id"]!=c.user_id:
            fail("requester_required","只有发起人可以修改或撤回申请",403)

    def _cancel_tasks(self,r,reason):
        self.store.execute("UPDATE approval_tasks SET status='cancelled',close_reason=?,closed_at=? WHERE request_id=? AND workspace_id=? AND status='pending'",(reason,now(),r["id"],r["workspace_id"]))
        self.store.execute("UPDATE approval_stage_runs SET status='cancelled',closed_at=? WHERE request_id=? AND workspace_id=? AND status IN ('active','stalled')",(now(),r["id"],r["workspace_id"]))

    def revise(self,c,i):
        r = self._change(c,i,(*OPEN,"approved","rejected"))
        self._owner(c,r)
        if not i.note.strip():
            fail("note_required","请说明修订原因",422)
        if r["revision"]>=50:
            fail("revision_limit","已达到 50 次修订上限，请发起新的申请",422)
        self._cancel_tasks(r,"revised")
        self._bump(r,revision=r["revision"]+1,status="in_review",stage_index=None,stage_run_id=None,finished_at=None)
        self._snapshot(r,i,c)
        self._record(r,"request.revised",{"note":i.note},c=c)
        self._settle(r)
        self._open(r,0,c)
        return self.get(c,i)

    def withdraw(self,c,i):
        r = self._change(c,i)
        self._owner(c,r)
        self._cancel_tasks(r,"withdrawn")
        self._bump(r,status="withdrawn",finished_at=now())
        self._record(r,"request.withdrawn",{"note":i.note},c=c)
        self._settle(r)
        return self.get(c,i)

    def _refresh(self,r,c=None):
        changed = False
        excluded = self._excluded(r)
        for task in self._tasks(r):
            if task["status"] in ("pending","approved") and not self._eligible(r["workspace_id"],task["assignee_id"],r["requester_id"],excluded):
                self.store.execute("UPDATE approval_tasks SET status='revoked',close_reason='access_revoked',closed_at=? WHERE id=? AND workspace_id=?",(now(),task["id"],r["workspace_id"]))
                self._record(r,"task.revoked",{"task_id":task["id"],"assignee_id":task["assignee_id"]},c=c)
                changed = True
        stage = self.store.one("SELECT * FROM approval_stage_runs WHERE id=? AND workspace_id=?",(r["stage_run_id"],r["workspace_id"]))
        tasks = self._tasks(r)
        enough = sum(t["status"] in ("pending","approved") for t in tasks)>=stage["required"]
        desired = "in_review" if enough else "stalled"
        if r["status"]!=desired or changed:
            old = r["status"]
            self._bump(r,status=desired)
            self.store.execute("UPDATE approval_stage_runs SET status=?,stall_reason=? WHERE id=? AND workspace_id=?",
                ("active" if enough else "stalled",None if enough else "insufficient_approvers",r["stage_run_id"],r["workspace_id"]))
            if old!=desired:
                self._record(r,"request.resumed" if enough else "request.stalled",c=c)
        return stage

    def _own_task(self,c,r,tid):
        task = next((x for x in self._tasks(r) if x["id"]==tid),None)
        if not task or task["assignee_id"]!=c.user_id or not self._eligible(c.workspace_id,c.user_id,r["requester_id"],self._excluded(r)):
            fail("independent_approver_required","必须由当前指定的独立审批人操作",403)
        if task["status"]!="pending":
            fail("task_closed","该审批任务已处理")
        return task

    def decide(self,c,i):
        r = self._change(c,i,("in_review","stalled"))
        self._own_task(c,r,i.task_id)
        if i.decision!="approve" and not i.note.strip():
            fail("note_required","退回或驳回必须说明原因",422)
        stage = self._refresh(r,c)
        state = {"approve":"approved","reject":"rejected","return":"returned"}[i.decision]
        self._bump(r)
        self.store.execute("UPDATE approval_tasks SET status=?,closed_at=? WHERE id=? AND workspace_id=?",(state,now(),i.task_id,c.workspace_id))
        self.store.execute("INSERT INTO approval_decisions (id,request_id,workspace_id,revision,stage_run_id,task_id,decider_id,decision,comment,state_version,via,run_id,created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",(new_id("adc"),r["id"],c.workspace_id,r["revision"],r["stage_run_id"],i.task_id,c.user_id,i.decision,i.note,r["state_version"],c.via,c.run_id,now()))
        self._record(r,"task.decided",{"task_id":i.task_id,"decision":i.decision,"note":i.note},c=c)
        if i.decision in ("reject","return"):
            self._cancel_tasks(r,state)
            self.store.execute("UPDATE approval_stage_runs SET status=?,closed_at=? WHERE id=? AND workspace_id=?",(state,now(),r["stage_run_id"],c.workspace_id))
            self._bump(r,status=state,finished_at=now() if state=="rejected" else None)
            self._record(r,"request."+state,c=c)
            self._settle(r)
        elif sum(t["status"]=="approved" for t in self._tasks(r))>=stage["required"]:
            self.store.execute("UPDATE approval_stage_runs SET status='approved',closed_at=? WHERE id=? AND workspace_id=?",(now(),r["stage_run_id"],c.workspace_id))
            self.store.execute("UPDATE approval_tasks SET status='cancelled',closed_at=?,close_reason='quorum_met' WHERE stage_run_id=? AND workspace_id=? AND status='pending'",(now(),r["stage_run_id"],c.workspace_id))
            self._record(r,"stage.approved",c=c)
            self._open(r,r["stage_index"]+1,c)
        return self.get(c,i)

    def transfer(self,c,i):
        r = self._change(c,i,("in_review","stalled"))
        self._own_task(c,r,i.task_id)
        if not i.note.strip():
            fail("note_required","请说明转交原因",422)
        if not self._eligible(c.workspace_id,i.to_user_id,r["requester_id"],self._excluded(r)) or any(t["assignee_id"]==i.to_user_id and t["status"] in ("pending","approved") for t in self._tasks(r)):
            fail("invalid_approver","接收人必须是尚未参与此阶段的有效独立审批人",422)
        self.store.execute("UPDATE approval_tasks SET status='transferred',closed_at=? WHERE id=? AND workspace_id=?",(now(),i.task_id,c.workspace_id))
        self._task(r,i.to_user_id,"transfer",i.task_id)
        self._bump(r)
        self._record(r,"task.transferred",{"from_task":i.task_id,"to_user_id":i.to_user_id,"note":i.note},c=c)
        self._refresh(r,c)
        return self.get(c,i)

    def repair(self,c,i):
        r = self._change(c,i,("stalled","in_review"))
        if c.user_id==r["requester_id"]:
            fail("independent_manager_required","发起人不能更改自己申请的审批人",403)
        if not i.note.strip():
            fail("note_required","请说明修正原因",422)
        self._refresh(r,c)
        if r["status"]!="stalled":
            fail("not_stalled","当前阶段无需修正审批人")
        current = {t["assignee_id"] for t in self._tasks(r) if t["status"] in ("pending","approved")}
        if any(u in current or not self._eligible(c.workspace_id,u,r["requester_id"],self._excluded(r)) for u in i.users):
            fail("invalid_approver","请选择有效且尚未参与的独立审批人",422)
        if len(current) + len(i.users) > 50:
            fail("too_many_approvers","审批人超过 50 人，请收窄角色范围",422)
        for uid in i.users:
            self._task(r,uid,"reassign")
        self._bump(r)
        self._record(r,"task.reassigned",{"users":i.users,"note":i.note},c=c)
        self._refresh(r,c)
        return self.get(c,i)

    def comment(self,c,i):
        r = self._change(c,i,(*OPEN,*TERMINAL))
        if not i.note.strip():
            fail("note_required","请输入审批意见",422)
        self._bump(r)
        self._record(r,"request.commented",{"note":i.note},c=c)
        return {"request_id":r["id"],"state_version":r["state_version"]}

    def wait(self,c,i):
        self._auth(c)
        r = self._request(c.workspace_id,i.request_id)
        if i.revision!=r["revision"]:
            fail("revision_changed","申请已修订，请核实新内容")
        c.touches("approval_request",r["id"])
        if r["status"] in TERMINAL:
            return {**self._wait_output(r), "__run_status__": "cancelled" if r["status"] == "withdrawn" else "succeeded"}
        self.store.execute("INSERT INTO approval_waits (run_id,request_id,workspace_id,revision,user_id,deadline,created_at) VALUES (?,?,?,?,?,?,?)",
            (c.run_id,r["id"],c.workspace_id,r["revision"],c.user_id,now()+i.timeout_hours*3600,now()))
        return {**self._wait_output(r),"__run_status__":"waiting_approval"}

    def _wait_output(self,r):
        return {"request_id":r["id"],"revision":r["revision"],"decision":r["status"],"state_version":r["state_version"]}

    def _credential_gone(self,run_id):
        """Why the client that started this wait may no longer be told its result, or None. A wait
        a person started in the page has no credential and is never stopped by this."""
        try:
            self.caps.standing(run_id)
        except ServiceError as gone:
            return gone.code
        return None

    def _settle(self,r,ts=None):
        ts = ts or now()
        for w in self.store.all("SELECT * FROM approval_waits WHERE request_id=? AND workspace_id=? AND status='waiting'",(r["id"],r["workspace_id"])):
            status,reason = None,None
            gone = self._credential_gone(w["run_id"])
            if gone:
                status,reason = "failed",gone
            elif not self._eligible(r["workspace_id"],w["user_id"]):
                status,reason = "failed","access_revoked"
            elif w["revision"]!=r["revision"]:
                status,reason = "cancelled","revision_changed"
            elif r["status"] in TERMINAL:
                status = "cancelled" if r["status"]=="withdrawn" else "succeeded"
            elif w["deadline"]<=ts:
                status,reason = "failed","approval_timeout"
            if status:
                output = {"request_id":r["id"],"reason":reason} if reason else self._wait_output(r)
                self.store.execute("UPDATE approval_waits SET status=?,finished_at=? WHERE run_id=? AND status='waiting'",(status,ts,w["run_id"]))
                self.store.execute("UPDATE op_runs SET status=?,output=?,error_code=?,error_message=?,updated_at=?,finished_at=? WHERE id=? AND status='waiting_approval'",
                                   (status,canonical(output),reason if status=="failed" else None,"审批等待已停止" if status=="failed" else None,ts,ts,w["run_id"]))

    def alerts(self,c,i):
        self._auth(c)
        return self._page("approval_alerts","workspace_id=? AND user_id=?",[c.workspace_id,c.user_id],i,"created_at DESC,id")

    def _alert(self,r,kind,ts):
        recipients = {r["requester_id"]} | {t["assignee_id"] for t in self._tasks(r) if t["status"]=="pending"}
        for uid in recipients:
            if self._eligible(r["workspace_id"],uid):
                self.store.execute("INSERT OR IGNORE INTO approval_alerts (id,request_id,workspace_id,stage_run_id,user_id,kind,created_at) VALUES (?,?,?,?,?,?,?)",
                    (new_id("aal"),r["id"],r["workspace_id"],r["stage_run_id"],uid,kind,ts))

    def sweep(self,at=None):
        """Restart-safe, bounded batches. Persistent flags and Store's write lock deduplicate
        overlapping workers; the outbox event and each reminder/escalation commit together."""
        ts = at or now()
        cursor = ""
        while True:
            batch = self.store.all("SELECT id,workspace_id FROM approval_requests WHERE id>? AND (status IN ('in_review','stalled') OR EXISTS "
                "(SELECT 1 FROM approval_waits w WHERE w.request_id=approval_requests.id AND w.status='waiting')) ORDER BY id LIMIT 100",(cursor,))
            if not batch:
                break
            for item in batch:
                with self.store.tx():
                    r = self._request(item["workspace_id"],item["id"])
                    if r["status"] in ("in_review","stalled"):
                        self._refresh(r)
                        stage = self.store.one("SELECT * FROM approval_stage_runs WHERE id=? AND workspace_id=?",(r["stage_run_id"],r["workspace_id"]))
                        if stage["due_at"] and stage["due_at"]<=ts and stage["remind"] and not stage["reminded_at"]:
                            self.store.execute("UPDATE approval_stage_runs SET reminded_at=? WHERE id=? AND workspace_id=?",(ts,stage["id"],r["workspace_id"]))
                            self._alert(r,"reminder",ts)
                            self._record(r,"stage.reminder")
                        if stage["escalate_at"] and stage["escalate_at"]<=ts and not stage["escalated_at"]:
                            rule = self._def(r).stages[r["stage_index"]].due.escalation
                            if rule.action=="add_approvers":
                                _,valid = self._resolve(r,rule.approvers)
                                current = {t["assignee_id"] for t in self._tasks(r) if t["status"] in ("pending","approved")}
                                for uid in sorted(valid-current):
                                    self._task(r,uid,"escalation")
                            self.store.execute("UPDATE approval_stage_runs SET escalated_at=? WHERE id=? AND workspace_id=?",(ts,stage["id"],r["workspace_id"]))
                            self._bump(r)
                            self._alert(r,"escalation",ts)
                            self._record(r,"stage.escalated",{"action":rule.action})
                            self._refresh(r)
                    self._settle(r,ts)
            cursor = batch[-1]["id"]
