"""Synwevo DataPrep: deterministic, versioned data preparation."""
from synwevo_core.capabilities import AppManifest

MANIFEST = AppManifest(id='dataprep', name='Synwevo DataPrep', path='/dataprep', icon='table',
    summary={'zh-Hans': '导入、清洗与校验数据，复用处理方案，追踪异常并导出结果。',
             'zh-Hant': '匯入、清理與驗證資料，重用處理方案，追蹤異常並匯出結果。',
             'en': 'Import, clean and validate data with reusable recipes, traceable issues and exports.'},
    object_kinds=('dataprep_dataset', 'dataprep_recipe', 'dataprep_run'),
    object_paths=(('dataprep_dataset', '/dataprep/w/{workspace}/datasets/{id}'),
                  ('dataprep_recipe', '/dataprep/w/{workspace}/recipes/{id}'),
                  ('dataprep_run', '/dataprep/w/{workspace}/runs/{id}')),
    machine=True)


def install(ctx):
    from .service import DataPrep
    from .ops import register
    from .routes import router
    from ..common.worker import Ticker
    service = DataPrep(ctx.store, ctx.identity, ctx.capabilities)
    ctx.api.state.dataprep = service
    register(ctx.registry, service)
    ctx.api.include_router(router(ctx, service))
    from synwevo_core.http import SUITE_V1
    ctx.api.include_router(router(ctx, service, prefix=SUITE_V1 + '/dataprep'))   # the same handlers, for a credential
    if ctx.start_jobs:
        Ticker('dataprep', service.tick, 1).start(ctx.api)
