"""Independent, versioned human approvals on the workplace origin."""
from synwevo_core.capabilities import AppManifest, EventFeed, EventType

MANIFEST = AppManifest(
    id="approvals", name="Synwevo Approvals", path="/approvals", icon="check",
    summary={"zh-Hans": "发起审批，审阅依据，独立决定并追踪每次修订。",
             "zh-Hant": "發起審批，審閱依據，獨立決定並追蹤每次修訂。",
             "en": "Request approval, review the evidence, decide independently and track every revision."},
    object_kinds=("approval_template", "approval_request"),
    object_paths=(("approval_template", "/approvals/w/{workspace}/t/{id}"),
                  ("approval_request", "/approvals/w/{workspace}/r/{id}")),
    machine=True)


def install(ctx):
    from ..common.worker import Ticker
    from .service import Approvals
    from .ops import register
    service = Approvals(ctx.store, ctx.identity, ctx.capabilities)
    ctx.api.state.approvals = service
    register(ctx.registry, service)
    from .ops import EVENTS
    # Requests and templates are readable by everyone who can read Approvals in the workspace.
    ctx.registry.register_events(MANIFEST.id, EventFeed(types=tuple(
        EventType(name, "approval_template" if name == "approvals.template.published" else "approval_request")
        for name in EVENTS)))
    if ctx.start_jobs:
        Ticker("approvals-waits", service.sweep, 30).start(ctx.api)
