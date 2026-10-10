"""Explicit business-resource context. Reads use the same registry and ACLs as their apps.

This first picker supports form definitions and table schemas/views, not submissions or all
table records. A selected snapshot must still match at admission. Saved provenance remains a
live authorization dependency; nothing here grants a tool permission or exports a workspace.
"""
from __future__ import annotations

import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from synwevo_core.capabilities import canonical, fingerprint
from synwevo_core.errors import ServiceError

from .tools import UNTRUSTED_NOTE, policy_allows, snapshot_read
from .files import FileReference

MAX_CONTEXTS = 5
MAX_CONTEXT_CHARS = 60_000
MAX_RESOURCE_CHARS = 24_000


class ContextInput(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    workspace_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,100}$")
    kind: Literal["form", "table"]


class ContextList(ContextInput):
    q: str = Field(default="", max_length=100)
    offset: int = Field(default=0, strict=True, ge=0, le=100_000)


class ContextResolve(ContextInput):
    resource_id: str = Field(pattern=r"^(frm|tbl)_[a-f0-9]{12}$")


class ContextRef(ContextResolve):
    snapshot: str = Field(pattern=r"^[a-f0-9]{64}$")


class ContextItem(ContextResolve):
    title: str


class ContextPage(BaseModel):
    items: list[ContextItem]
    next_offset: int | None
    limited: bool


class ResolvedContext(BaseModel):
    reference: ContextRef
    title: str
    workspace_name: str
    open_path: str | None


# Explicit public read contracts. Do not infer database tables or dispatch client operation IDs.
READS = {"form": ("forms", "forms.form", "form_id", "title"),
         "table": ("tables", "tables.table", "table_id", "name")}


class Contexts:
    def __init__(self, agent):
        self.agent, self.caps, self.store = agent, agent.caps, agent.store

    def _allow(self, user_id, ref, credential=None):
        app, operation, _, _ = READS[ref.kind]
        op = self.caps.registry.get(operation + ".get")
        if op is None:
            raise ServiceError(404, "context_unavailable", "此应用暂不支持选择上下文")
        self.caps.authorize(op, user_id, ref.workspace_id)
        credential = credential or self.agent._credential(user_id)
        self.agent._check_person(user_id, credential)
        if credential is None:
            raise ServiceError(503, "agent_not_configured", "个人 Agent 尚未配置模型服务")
        if not policy_allows(self.store, ref.workspace_id, credential.kind, credential.provider):
            raise ServiceError(403, "source_policy_changed", "来源空间不允许当前模型处理业务内容")
        return app, operation

    def list(self, user_id, ref: ContextList):
        _, operation = self._allow(user_id, ref)
        args = {"q": ref.q}
        if ref.kind == "table":
            args.update(offset=ref.offset, limit=50)
        result = snapshot_read(self.caps, user_id, operation + ".list", ref.workspace_id, args)
        if ref.kind == "form":
            rows = result["forms"]
            limited = len(rows) == 500  # The existing Forms catalogue has this explicit cap.
            total, rows = len(rows), rows[ref.offset:ref.offset + 50]
        else:
            rows, total, limited = result["tables"], result["total"], False
        return {"items": [{"kind": ref.kind, "workspace_id": ref.workspace_id,
                           "resource_id": r["id"], "title": r[READS[ref.kind][3]]} for r in rows],
                "next_offset": ref.offset + len(rows) if ref.offset + len(rows) < total else None,
                "limited": limited}

    def _read(self, user_id, ref, credential=None):
        app, operation = self._allow(user_id, ref, credential)
        payload = {READS[ref.kind][2]: ref.resource_id}
        result = snapshot_read(self.caps, user_id, operation + ".get", ref.workspace_id, payload)
        if len(canonical(result)) > MAX_RESOURCE_CHARS:
            raise ServiceError(413, "context_too_large", "所选资源过大，请在应用中缩小内容范围后重试")
        digest = fingerprint(result)
        if isinstance(ref, ContextRef) and digest != ref.snapshot:
            raise ServiceError(409, "context_changed", "所选资源内容或权限已变化，请移除后重新选择")
        workspace_name = self.agent._ws_name(ref.workspace_id)
        manifest = self.caps.registry.app(app)
        return result, {"reference": {**ref.model_dump(exclude={"snapshot"}), "snapshot": digest},
                        "title": result[READS[ref.kind][3]], "workspace_name": workspace_name,
                        "open_path": manifest.object_path(ref.kind, ref.resource_id, ref.workspace_id)}, [
            {"operation": operation + ".get", "workspace_id": ref.workspace_id, "input": payload,
             "snapshot_hash": digest},
            {"workspace_id": ref.workspace_id, "app": app, "catalogue_name": workspace_name}]

    def resolve(self, user_id, ref: ContextResolve):
        return self._read(user_id, ref)[1]

    def prepare(self, user_id, conversation, refs, *, file_refs=None, regenerate_of=None, credential=None):
        """Inside admission's transaction: current projection, policy, scope and exact version."""
        if not refs and not file_refs:
            return None, [], [], []
        refs, file_refs = refs or [], file_refs or []
        if regenerate_of or not isinstance(refs, list) or not isinstance(file_refs, list) or len(refs)+len(file_refs) > MAX_CONTEXTS:
            raise ServiceError(422, "invalid_context", "最多选择五项资源；重新生成不能替换原有来源")
        scope = json.loads(conversation["scope_json"]) if conversation["scope_json"] else None
        items, sources, clean, seen = [], [], [], set()
        for raw in refs:
            try:
                ref = ContextRef.model_validate(raw)
            except ValidationError:
                raise ServiceError(422, "invalid_context", "所选资源引用无效，请重新选择") from None
            key = (ref.workspace_id, ref.kind, ref.resource_id)
            if key in seen:
                raise ServiceError(422, "invalid_context", "不能重复选择同一资源")
            seen.add(key)
            if scope is not None and ref.workspace_id not in scope:
                raise ServiceError(403, "outside_conversation_scope", "所选资源不在此会话的工作空间范围内")
            result, shown, provenance = self._read(user_id, ref, credential)
            items.append({**shown, "data": result})
            sources.extend(provenance)
            # Match ordinary tool reads: a conversation's writes can have carried content
            # here, including in another branch or with the copy receipt withheld. The
            # independent target remains readable under its own policy, while its origins
            # must still constrain a subsequent export to a different audience.
            carried = self.agent._carried_into({"conversation_id": conversation["id"], "user_id": user_id},
                                               {ref.workspace_id})
            sources.extend({"workspace_id": source, "carried_into": ref.workspace_id} for source in sorted(carried))
            clean.append(ref.model_dump())
        clean_files = []
        if file_refs:
            self.agent._check_person(user_id, credential or self.agent._credential(user_id))
        for raw in file_refs:
            try:
                ref = FileReference.model_validate(raw)
            except ValidationError:
                raise ServiceError(422, "invalid_context", "文件引用无效，请重新选择") from None
            if ref.file_id in seen:
                raise ServiceError(422, "invalid_context", "不能重复选择同一文件")
            seen.add(ref.file_id)
            row = self.agent.files.resolve(user_id, ref)
            clean_files.append(ref.model_dump())
            items.append({"kind": "file", "file_id": row["id"], "title": row["filename"],
                          "workspace_name": None, "open_path": "/api/suite/agent/files/"+row["id"]+"/download",
                          "data": json.loads(row["parsed_json"])})
            # Null is genuinely no workspace. Pre-file engines conservatively hide an
            # unrecognized source with no read proof; they must never replay this as plain text.
            sources.append({"file_id": row["id"], "workspace_id": None, "snapshot_hash": row["snapshot_hash"]})
        payload = {"type": "business_context_v1", "notice": UNTRUSTED_NOTE, "items": items}
        if len(canonical(payload)) > MAX_CONTEXT_CHARS:
            raise ServiceError(413, "context_too_large", "所选资源总内容过大，请减少所选资源")
        return payload, sources, clean, clean_files


def visible_contexts(api):
    if not isinstance(api, dict) or api.get("type") != "business_context_v1":
        return []
    return [{**{k: item[k] for k in ("title", "workspace_name", "open_path")},
             **({"kind": "file"} if item.get("kind") == "file" else {})} for item in api["items"]]
