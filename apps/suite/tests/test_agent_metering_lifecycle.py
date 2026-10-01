"""Actual suite composition lifecycle; configured fixture provider, no model traffic."""
import pytest

from synwevo_core.http import build_platform, new_api
from synwevo_suite.app import install
from synwevo_suite.agent.gateway import MeteredTurnProvider
from .test_agent_metering import gateway


@pytest.mark.parametrize('start_jobs',[True,False])
def test_composition_owns_recovery_and_shutdown(gateway, monkeypatch, start_jobs):
    monkeypatch.setenv('SYNWEVO_SUITE_APPS','agent')
    g=gateway
    plat=build_platform(str(g.store.root))
    api=new_api('Isolated metering fixture')
    g.model.store=plat.store;g.ledger.store=plat.store
    api.state.agent_provider=MeteredTurnProvider(g.model)
    install(api,plat,start_jobs=start_jobs)
    worker=api.state.agent_metering_recovery
    try:
        assert worker.gateway is g.model
        assert worker.running is start_jobs
        assert worker.close in api.state.shutdown_hooks
        assert g.seen==[]
    finally:
        for hook in api.state.shutdown_hooks:hook()
    assert not worker.running


def test_default_factory_does_not_enable_billing(gateway,monkeypatch):
    from synwevo_suite.app import create_app
    monkeypatch.setenv('SYNWEVO_SUITE_APPS','agent')
    api=create_app(str(gateway.store.root),start_jobs=False)
    try:
        assert not hasattr(api.state,'agent_metering_recovery')
        assert not isinstance(api.state.agent.provider,MeteredTurnProvider)
        assert gateway.seen==[]
    finally:api.state.agent.close()
