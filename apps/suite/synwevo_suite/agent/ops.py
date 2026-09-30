"""Synwevo Agent's operations: the person's conversations, turns, confirmations, settings and
memories (all personal), and each workspace's AI-processing policy (set by its owners/admins)."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field as F, SecretStr, field_serializer, field_validator

from synwevo_core import sealed
from synwevo_core.capabilities import Operation, fingerprint
from synwevo_core.errors import ServiceError
from synwevo_core.store import new_id, now

from .engine import Agent, _j
from .provider import EFFORTS, KEY_PURPOSE, configured_model, resolve_credential

CONV = r"^conv_[a-f0-9]{12}$"
MSG = r"^msg_[a-f0-9]{12}$"
TURN = r"^turn_[a-f0-9]{12}$"
CALL = r"^tc_[a-f0-9]{12}$"
MEM = r"^mem_[a-f0-9]{12}$"
WS = r"^[A-Za-z0-9_-]{1,100}$"


def T(zh, hant, en):
    return {"zh-Hans": zh, "zh-Hant": hant, "en": en}


class _In(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)


class ListConversations(_In):
    status: Literal["active", "archived"] = "active"
    q: str | None = F(default=None, max_length=100)
    limit: int = F(default=50, ge=1, le=200)
    before: float | None = None


class CreateConversation(_In):
    title: str | None = F(default=None, max_length=200)
    scope: list[str] | None = F(default=None, max_length=50)


class ConversationRef(_In):
    conversation_id: str = F(pattern=CONV)


class UpdateConversation(_In):
    conversation_id: str = F(pattern=CONV)
    title: str | None = F(default=None, max_length=200)
    scope: list[str] | None = F(default=None, max_length=50)
    clear_scope: bool = False
    status: Literal["active", "archived"] | None = None
    head_id: str | None = F(default=None, pattern=MSG)


class StartTurn(_In):
    conversation_id: str = F(pattern=CONV)
    text: str | None = F(default=None, max_length=20_000)
    parent_message_id: str | None = F(default=None, pattern=MSG)
    regenerate_of: str | None = F(default=None, pattern=MSG)


class TurnRef(_In):
    turn_id: str = F(pattern=TURN)


class Decide(_In):
    tool_call_id: str = F(pattern=CALL)
    confirmation: str | None = F(default=None, max_length=100)


class Settings(_In):
    effort: Literal["low", "medium", "high", "xhigh", "max"] | None = None
    prefer_personal_key: bool | None = None
    memory_enabled: bool | None = None
    personal_key: SecretStr | None = F(default=None, min_length=20, max_length=300)
    clear_personal_key: bool = False

    @field_validator("personal_key")
    @classmethod
    def valid_key(cls, value):
        if value is not None and any(c.isspace() for c in value.get_secret_value()):
            raise ValueError("密钥格式不正确")
        return value

    @field_serializer("personal_key")
    def key_snapshot(self, value):
        # invoke() persists model_dump(), including failed runs. Keep the raw secret only on
        # the validated instance; a digest distinguishes two keys under the same idempotency key.
        return {"sha256": fingerprint(value.get_secret_value())} if value is not None else None


class Empty(_In):
    pass


class MemoryAdd(_In):
    body: str = F(min_length=1, max_length=500)


class MemoryUpdate(_In):
    memory_id: str = F(pattern=MEM)
    body: str = F(min_length=1, max_length=500)


class MemoryRef(_In):
    memory_id: str = F(pattern=MEM)


class PolicySet(_In):
    processing: Literal["none", "platform", "any"]


def settings_json(store, user_id: str) -> dict:
    row = store.one("SELECT * FROM agent_settings WHERE user_id=?", (user_id,))
    cred = resolve_credential(store, user_id)
    from synwevo_core import env
    return {"effort": row["effort"] if row else "medium",
            "prefer_personal_key": bool(row and row["prefer_personal_key"]),
            "memory_enabled": bool(row["memory_enabled"]) if row else True,
            "personal_key": {"set": bool(row and row["personal_key_sealed"]),
                             "hint": row["personal_key_hint"] if row else None},
            "model_configured": bool(configured_model()),
            "platform_configured": bool((env.get("AGENT_ANTHROPIC_API_KEY") or "").strip()
                                        and configured_model()),
            "active": {"credential": cred.kind, "model": cred.model} if cred else None,
            "efforts": list(EFFORTS)}


def update_settings(store, user_id: str, i: Settings) -> dict:
    row = store.one("SELECT * FROM agent_settings WHERE user_id=?", (user_id,))
    if not row:
        store.execute("INSERT INTO agent_settings (user_id, updated_at) VALUES (?,?)", (user_id, now()))
    sets, args = ["updated_at=?"], [now()]
    if i.effort is not None:
        sets.append("effort=?")
        args.append(i.effort)
    if i.prefer_personal_key is not None:
        sets.append("prefer_personal_key=?")
        args.append(int(i.prefer_personal_key))
    if i.memory_enabled is not None:
        sets.append("memory_enabled=?")
        args.append(int(i.memory_enabled))
    if i.clear_personal_key:
        sets += ["personal_key_sealed=NULL", "personal_key_hint=NULL"]
    elif i.personal_key is not None:
        key = i.personal_key.get_secret_value()
        if any(c.isspace() for c in key):
            raise ServiceError(400, "bad_key", "密钥格式不正确")
        # Kept sealed; only a short hint (the last four characters) is ever shown again.
        sets += ["personal_key_sealed=?", "personal_key_hint=?"]
        args += [sealed.seal(store, KEY_PURPOSE, key), "…" + key[-4:]]
    store.execute(f"UPDATE agent_settings SET {', '.join(sets)} WHERE user_id=?", (*args, user_id))
    return settings_json(store, user_id)


def policies_json(store, identity, user_id: str) -> dict:
    """Every workspace the person belongs to, with its AI-processing policy and whether they may change it."""
    out = []
    for w in identity.workspaces_for(user_id):
        row = store.one("SELECT processing, updated_at FROM agent_workspace_policies WHERE workspace_id=?", (w["id"],))
        effective = row["processing"] if row else ("any" if w["kind"] == "personal" else "none")
        out.append({"workspace_id": w["id"], "name": w["name"], "kind": w["kind"], "role": w["role"],
                    "processing": effective, "explicit": bool(row), "can_change": w["role"] in ("owner", "admin")})
    return {"workspaces": out}


def register(registry, agent: Agent, store, identity) -> None:
    def result_guard(c, row):
        inp, out = _j(row.get("input"), {}), _j(row.get("output"), {})
        cid = inp.get("conversation_id")
        if c.op.id == "agent.conversation.create":
            cid = out.get("id")
        if c.op.id in ("agent.conversation.delete", "agent.memory.delete", "agent.settings.update",
                       "agent.workspace_policy.set"):
            # Only a deletion receipt, masked key state or policy enum; no business body.
            return
        if c.op.id.startswith("agent.memory."):
            mem = store.one("SELECT body FROM agent_memories WHERE id=? AND user_id=?", (out.get("id"), c.user_id))
            if not mem or mem["body"] != out.get("body"):
                raise ServiceError(409, "result_access_changed", "记忆已更改或删除")
            return
        if c.op.id.startswith("agent.tool_call."):
            item = store.one("SELECT conversation_id FROM agent_tool_calls WHERE id=? AND user_id=?",
                             (inp.get("tool_call_id"), c.user_id))
            cid = item["conversation_id"] if item else None
        elif c.op.id.startswith("agent.turn."):
            item = store.one("SELECT conversation_id FROM agent_turns WHERE id=? AND user_id=?",
                             (inp.get("turn_id") or out.get("turn_id"), c.user_id))
            cid = item["conversation_id"] if item else None
        if not cid:
            raise ServiceError(409, "result_access_unverifiable", "无法核实原结果的访问权限")
        conv = agent.conversation(c.user_id, cid)
        if agent._sealed(c.user_id, conv):
            raise ServiceError(409, "result_access_changed", "来源访问权限已变化")

    def op(**kw):
        kw.setdefault("need", "personal")
        if kw.get("side_effect", "none") != "none":
            kw.setdefault("result_guard", result_guard)
        registry.register(Operation(app="agent", **kw))

    op(id="agent.conversation.list", title=T("列出会话", "列出會話", "List conversations"),
       summary="The person's own Agent conversations.", input=ListConversations, agent="never",
       handler=lambda c, i: agent.list_conversations(c.user_id, status=i.status, q=i.q, limit=i.limit, before=i.before))
    op(id="agent.conversation.create", title=T("新建会话", "新建會話", "New conversation"),
       summary="Start a conversation.", input=CreateConversation, side_effect="write", agent="never",
       handler=lambda c, i: agent.create_conversation(c.user_id, i.title, i.scope))
    op(id="agent.conversation.get", title=T("打开会话", "開啟會話", "Open a conversation"),
       summary="A conversation with its messages, branches and tool calls.", input=ConversationRef, agent="never",
       handler=lambda c, i: agent.conversation_json(c.user_id, agent.conversation(c.user_id, i.conversation_id), full=True))
    op(id="agent.conversation.update", title=T("修改会话", "修改會話", "Update a conversation"),
       summary="Rename, archive or restore a conversation, set its workspace scope, or switch branch.",
       input=UpdateConversation, side_effect="write", agent="never",
       handler=lambda c, i: agent.update_conversation(
           c.user_id, i.conversation_id, title=i.title,
           scope=(None if i.clear_scope else i.scope) if (i.clear_scope or i.scope is not None) else ...,
           status=i.status, head_id=i.head_id))
    op(id="agent.conversation.delete", title=T("删除会话", "刪除會話", "Delete a conversation"),
       summary="Delete a conversation permanently.", input=ConversationRef, side_effect="write", agent="never",
       handler=lambda c, i: agent.delete_conversation(c.user_id, i.conversation_id))
    op(id="agent.turn.start", title=T("发送消息", "傳送訊息", "Send a message"),
       summary="Send a message (or regenerate an answer) and start the answer.", input=StartTurn,
       side_effect="write", agent="never", transactional=False,
       handler=lambda c, i: agent.start_turn(c.user, i.conversation_id, i.text, parent_id=i.parent_message_id,
                                             regenerate_of=i.regenerate_of))
    op(id="agent.turn.cancel", title=T("停止回答", "停止回答", "Stop the answer"),
       summary="Stop an answer: nothing further runs.", input=TurnRef, side_effect="write", agent="never",
       transactional=False, handler=lambda c, i: agent.cancel(c.user_id, i.turn_id))
    op(id="agent.tool_call.confirm", title=T("确认执行", "確認執行", "Confirm an action"),
       summary="Run the exact action the person was shown.", input=Decide, side_effect="write", agent="never",
       transactional=False, unattended=False,
       handler=lambda c, i: agent.decide(c.user, i.tool_call_id, confirm=True, confirmation=i.confirmation))
    op(id="agent.tool_call.decline", title=T("拒绝执行", "拒絕執行", "Decline an action"),
       summary="Decline a proposed action.", input=Decide, side_effect="write", agent="never", transactional=False,
       unattended=False, handler=lambda c, i: agent.decide(c.user, i.tool_call_id, confirm=False, confirmation=None))
    op(id="agent.settings.get", title=T("查看 Agent 设置", "查看 Agent 設定", "Agent settings"),
       summary="Model access and preferences.", input=Empty, agent="never",
       handler=lambda c, i: settings_json(store, c.user_id))
    op(id="agent.settings.update", title=T("修改 Agent 设置", "修改 Agent 設定", "Change Agent settings"),
       summary="Change effort, memory, or the person's own provider key.", input=Settings, side_effect="write",
       agent="never", handler=lambda c, i: update_settings(store, c.user_id, i))
    op(id="agent.memory.list", title=T("查看记忆", "查看記憶", "Memories"), summary="What the Agent remembers about the person.",
       input=Empty, agent="never",
       handler=lambda c, i: {"memories": store.all("SELECT id, body, created_at, updated_at FROM agent_memories "
                                                   "WHERE user_id=? ORDER BY created_at", (c.user_id,))})

    def add_memory(c, i):
        n = store.one("SELECT COUNT(*) AS n FROM agent_memories WHERE user_id=?", (c.user_id,))["n"]
        if n >= 50:
            raise ServiceError(409, "too_many_memories", "记忆已满 50 条，请先删除一些")
        mid = new_id("mem")
        store.execute("INSERT INTO agent_memories (id,user_id,body,created_at,updated_at) VALUES (?,?,?,?,?)",
                      (mid, c.user_id, i.body.strip(), now(), now()))
        return {"id": mid, "body": i.body.strip()}

    op(id="agent.memory.add", title=T("记住一条偏好", "記住一條偏好", "Remember a preference"),
       summary="Remember a personal preference the person stated (never business facts from a workspace).",
       input=MemoryAdd, side_effect="write", agent="confirm", handler=add_memory)

    def update_memory(c, i):
        n = store.execute("UPDATE agent_memories SET body=?, updated_at=? WHERE id=? AND user_id=?",
                          (i.body.strip(), now(), i.memory_id, c.user_id))
        if not n:
            raise ServiceError(404, "memory_not_found", "记忆不存在")
        return {"id": i.memory_id, "body": i.body.strip()}

    op(id="agent.memory.update", title=T("修改记忆", "修改記憶", "Edit a memory"), summary="Edit a memory.",
       input=MemoryUpdate, side_effect="write", agent="never", handler=update_memory)

    def delete_memory(c, i):
        if not store.execute("DELETE FROM agent_memories WHERE id=? AND user_id=?", (i.memory_id, c.user_id)):
            raise ServiceError(404, "memory_not_found", "记忆不存在")
        return {"deleted": i.memory_id}

    op(id="agent.memory.delete", title=T("删除记忆", "刪除記憶", "Delete a memory"), summary="Delete a memory.",
       input=MemoryRef, side_effect="write", agent="never", handler=delete_memory)
    op(id="agent.workspace_policy.list", title=T("查看 AI 处理政策", "查看 AI 處理政策", "AI processing policies"),
       summary="Each of the person's workspaces and whether its content may be processed by the Agent's model.",
       input=Empty, agent="never", handler=lambda c, i: policies_json(store, identity, c.user_id))

    def set_policy(c, i):
        store.execute("INSERT INTO agent_workspace_policies (workspace_id, processing, updated_by, updated_at) VALUES (?,?,?,?) "
                      "ON CONFLICT(workspace_id) DO UPDATE SET processing=excluded.processing, updated_by=excluded.updated_by, "
                      "updated_at=excluded.updated_at", (c.workspace_id, i.processing, c.user_id, now()))
        c.touches("workspace", c.workspace_id)
        return {"workspace_id": c.workspace_id, "processing": i.processing}

    op(id="agent.workspace_policy.set", title=T("设置 AI 处理政策", "設定 AI 處理政策", "Set the AI processing policy"),
       summary="Owners and admins decide whether the workspace's content may be sent to the Agent's model provider.",
       input=PolicySet, need="manage", side_effect="write", agent="never", unattended=False, handler=set_policy)
