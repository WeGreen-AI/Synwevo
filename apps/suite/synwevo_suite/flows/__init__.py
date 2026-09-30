"""Install flow operations under the already enabled personal Agent application.

The composition root calls this after Agent and the business operation adapters. This module
does not register or enable an application and does not own routes or schema migrations.
"""
from synwevo_core.flows import Flows

from ..common.worker import Ticker
from .ops import register


def install(ctx):
    if not ctx.registry.app("agent") or ctx.registry.app("agent").scope != "personal":
        raise RuntimeError("flows.install requires the explicitly enabled personal Agent application")
    if getattr(ctx.api.state, "flows", None) is not None:
        return ctx.api.state.flows
    flows = Flows(ctx.store, ctx.capabilities)
    register(ctx.registry, flows)
    ctx.api.state.flows = flows
    if ctx.start_jobs:
        ticker = Ticker("flows", flows.tick, 5)
        # Old capabilities may announce inside a parent transaction. Defer all wake-ups.
        def wake(events):
            ctx.store.after_commit(ticker.wake)
        ctx.capabilities.listeners.append(wake)
        ticker.start(ctx.api)
        hooks = getattr(ctx.api.state, "shutdown_hooks", None)
        if hooks is not None:
            hooks.append(lambda: ctx.capabilities.listeners.remove(wake) if wake in ctx.capabilities.listeners else None)
    return flows
