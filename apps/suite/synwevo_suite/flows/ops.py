"""User-owned automation actions, not platform administrator APIs."""
from __future__ import annotations

from pydantic import Field

from synwevo_core.capabilities import Operation
from synwevo_core.flows.models import Definition, Model, Policy, Trigger


class Create(Model):
    name: str = Field(min_length=1, max_length=200)
    definition: Definition


class FlowRef(Model):
    flow_id: str = Field(pattern=r"^flow_[a-f0-9]{12}$")


class Version(FlowRef):
    version: int = Field(ge=1)


class Publish(Model):
    flow_id: str = Field(pattern=r"^flow_[a-f0-9]{12}$")
    expected_version: int = Field(ge=1)
    definition: Definition


class Start(Version):
    parameters: dict = Field(default_factory=dict)
    idempotency_key: str = Field(min_length=1, max_length=200)


class Run(Model):
    run_id: str = Field(pattern=r"^flowrun_[a-f0-9]{12}$")


class History(Run):
    after: int = Field(default=0, ge=0)
    limit: int = Field(default=100, ge=1, le=200)


class ListFlows(Model):
    after: str = Field(default="", max_length=100)
    limit: int = Field(default=50, ge=1, le=200)


class FlowPage(ListFlows):
    flow_id: str = Field(pattern=r"^flow_[a-f0-9]{12}$")


class AddTrigger(Version):
    trigger: Trigger
    policy: Policy


class TriggerRef(Model):
    trigger_id: str = Field(pattern=r"^flowtrigger_[a-f0-9]{12}$")


class Enable(TriggerRef):
    enabled: bool


class Deliveries(TriggerRef):
    after: str = Field(default="", max_length=100)
    limit: int = Field(default=50, ge=1, le=200)


def register(registry, flows):
    def add(suffix, input_, handler, *, write=False, person=False):
        def invoke(call, inp):
            value = handler(call, inp)
            if isinstance(value, dict):
                if value.get("trigger_id"):
                    call.touches("flow_trigger", value["trigger_id"])
                elif value.get("id"):
                    call.touches("flow_run", value["id"])
                elif value.get("flow_id"):
                    call.touches("flow", value["flow_id"])
            return value
        args = dict(id="agent.flow." + suffix, app="agent", input=input_, handler=invoke,
                    title={"zh-Hans": "流程：" + suffix, "zh-Hant": "流程：" + suffix, "en": "Flow: " + suffix},
                    summary="Personal deterministic flow " + suffix, need="personal",
                    side_effect="write" if write else "none", agent="never" if person else ("confirm" if write else "auto"),
                    unattended=False, object_kind="flow")
        # Upgraded shared baseline revalidates cached receipts. Older baseline accepts the
        # same operations without passing an unsupported dataclass argument.
        if "result_guard" in Operation.__dataclass_fields__:
            def guard(call, row):
                from synwevo_core.flows.engine import decode
                value = decode(row.get("output"), {})
                if value.get("flow_id"):
                    flows._flow(value["flow_id"], call.user_id)
                elif value.get("trigger_id"):
                    flows.trigger(value["trigger_id"], user_id=call.user_id)
                else:
                    flows._owned_run(value["id"], call.user_id)
            args["result_guard"] = guard
        registry.register(Operation(**args))

    add("create", Create, lambda c, i: flows.create(user_id=c.user_id, name=i.name, definition=i.definition.model_dump(mode="json")), write=True)
    add("publish", Publish, lambda c, i: flows.publish(i.flow_id, user_id=c.user_id, definition=i.definition.model_dump(mode="json"), expected_version=i.expected_version), write=True)
    add("get", Version, lambda c, i: flows.definition(i.flow_id, user_id=c.user_id, version=i.version))
    add("list", ListFlows, lambda c, i: flows.list(user_id=c.user_id, after=i.after, limit=i.limit))
    add("runs", FlowPage, lambda c, i: flows.runs(i.flow_id, user_id=c.user_id, after=i.after, limit=i.limit))
    add("start", Start, lambda c, i: flows.start(i.flow_id, user_id=c.user_id, version=i.version, parameters=i.parameters,
                                                idempotency_key=i.idempotency_key, correlation_id=c.correlation_id,
                                                parent_run_id=c.run_id, depth=c.depth), write=True)
    add("run", History, lambda c, i: flows.run(i.run_id, user_id=c.user_id, after=i.after, limit=i.limit))
    add("cancel", Run, lambda c, i: flows.cancel(i.run_id, user_id=c.user_id), write=True)
    add("pause", Run, lambda c, i: flows.pause(i.run_id, user_id=c.user_id), write=True)
    add("resume", Run, lambda c, i: flows.resume(i.run_id, user_id=c.user_id), write=True)
    # Creating/reenabling recurring authority is a deliberate user action in the Agent app.
    add("trigger.create", AddTrigger, lambda c, i: flows.create_trigger(i.flow_id, user_id=c.user_id, version=i.version,
                                                                       trigger=i.trigger.model_dump(mode="json"), policy=i.policy.model_dump(mode="json")), write=True, person=True)
    add("trigger.get", TriggerRef, lambda c, i: flows.trigger_view(i.trigger_id, user_id=c.user_id))
    add("trigger.list", FlowRef, lambda c, i: flows.triggers(i.flow_id, user_id=c.user_id))
    add("trigger.deliveries", Deliveries, lambda c, i: flows.deliveries(i.trigger_id, user_id=c.user_id, after=i.after, limit=i.limit))
    add("trigger.enable", Enable, lambda c, i: flows.set_trigger_enabled(i.trigger_id, user_id=c.user_id, enabled=i.enabled), write=True, person=True)
