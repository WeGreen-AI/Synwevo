"""Synwevo Tables: independent structured business data, exposed through shared operations."""
from synwevo_core.capabilities import AppManifest

MANIFEST = AppManifest(id='tables', name='Synwevo Tables', path='/tables', icon='table',
    summary={'zh-Hans': '管理业务数据、关联记录、保存视图并追溯每次变更。',
             'zh-Hant': '管理業務資料、關聯記錄、儲存檢視並追溯每次變更。',
             'en': 'Manage typed business data, related records, saved views and change history.'},
    object_kinds=('table', 'table_record', 'table_view'),
    object_paths=(('table', '/tables/w/{workspace}/t/{id}'),
                  ('table_record', '/tables/w/{workspace}/r/{id}')),
    machine=True)


def install(ctx):
    from .service import Tables
    from .schema import Schema
    from .query import Queries
    from .transfer import Transfers, install_routes
    from .ops import register

    class TablesService(Schema, Queries, Transfers, Tables):
        pass

    service = TablesService(ctx.store, ctx.identity, ctx.capabilities)
    ctx.api.state.tables = service
    register(ctx.registry, service)
    install_routes(ctx, service)
    if ctx.start_jobs:
        from ..common.worker import Ticker
        Ticker('tables-expiry', service.sweep, 300).start(ctx.api)
