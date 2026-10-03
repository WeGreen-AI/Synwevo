"""UI, automation and Agent share these operations and current workspace authorization."""
from synwevo_core.capabilities import Operation
from . import models as M
from .service import decode
from .files import inspect
from .history import result_guard, check_sources
from ..common.files import read_blob


def register(registry, service):
    def run_read(call, rid):
        row = service.get('runs', call.workspace_id, rid)
        service.check_run_sources(call.workspace_id, call.user_id, row)
        return decode(row)

    def add(id, title, model, handler, *, write=False, events=(), **kw):
        # Older worktrees do not yet have the coordinator-owned history contract.
        # Attach the guard now; upgraded core calls it on every historical return.
        guard = (lambda call, historic: result_guard(service, call, historic)) if write else None
        if 'result_guard' in Operation.__dataclass_fields__:
            kw['result_guard'] = guard
        operation = Operation(id='dataprep.' + id, app='dataprep',
                          title={'zh-Hans': title[0], 'zh-Hant': title[1], 'en': title[2]}, summary=title[2],
                          input=model, handler=handler, need='write' if write else 'read',
                          side_effect='write' if write else 'none', agent='confirm' if write else 'auto', events=events, **kw)
        if write and 'result_guard' not in Operation.__dataclass_fields__:
            object.__setattr__(operation, 'result_guard', guard)
        registry.register(operation)

    for table, word in [('datasets', ('数据集', '資料集', 'datasets')), ('recipes', ('方案', '方案', 'recipes')), ('runs', ('运行', '執行', 'runs'))]:
        add(table[:-1] + '.list', ('列出' + word[0], '列出' + word[1], 'List ' + word[2]), M.Page,
            lambda c, i, table=table: service.page(table, c.workspace_id, i.offset, i.limit, c.user_id))
    add('file.inspect', ('查看工作表', '查看工作表', 'Inspect worksheets and import limits'), M.FileRef,
        lambda c, i: inspect(read_blob(service.store, service.get('files', c.workspace_id, i.file_id)['blob']), service.get('files', c.workspace_id, i.file_id)['format']))
    created = ('dataprep.dataset.created',)
    add('dataset.import', ('导入文件', '匯入檔案', 'Import the complete selected CSV or XLSX range'), M.ImportFile, service.import_file, write=True, events=created)
    add('dataset.from_records', ('导入记录', '匯入記錄', 'Import scalar records; numeric values must be decimal strings'), M.Records, service.records, write=True, events=created)
    add('dataset.from_forms', ('导入表单提交', '匯入表單提交', 'Snapshot authorized Forms submissions through the registry'), M.FormRecords, service.form_records, write=True, events=created,
        reaches=('forms.read',))
    add('dataset.from_extract', ('导入已确认的提取结果', '匯入已確認的提取結果', 'Import confirmed extraction results'),
        M.ExtractRecords, service.extract_records, write=True, events=created, version=1,
        output=M.ExtractDataset, object_kind='dataprep_dataset', reaches=('extract.read',))
    add('dataset.get', ('查看数据', '查看資料', 'Read dataset metadata and a bounded page with source positions'), M.DatasetPage,
        lambda c, i: (check_sources(service, c.workspace_id, c.user_id, i.dataset_id),
                      service.dataset(c.workspace_id, i.dataset_id, i.offset, i.limit))[1])
    def dataset_issues(c, i):
        import json
        check_sources(service, c.workspace_id, c.user_id, i.dataset_id)
        found = json.loads(service.get('datasets', c.workspace_id, i.dataset_id)['read_problems_json'])
        return {'items': found[i.offset:i.offset + i.limit], 'total': len(found), 'next_offset': i.offset + i.limit if i.offset + i.limit < len(found) else None}
    add('dataset.issues', ('查看导入异常', '查看匯入異常', 'Read all import issues by page'), M.DatasetPage, dataset_issues)
    add('recipe.create', ('新建方案', '新增方案', 'Create a reusable draft recipe'), M.CreateRecipe, service.create, write=True)
    add('recipe.get', ('查看方案', '查看方案', 'Read recipe draft and immutable published versions'), M.RecipeRef,
        lambda c, i: service.recipe(c.workspace_id, i.recipe_id))
    add('recipe.save', ('保存方案', '儲存方案', 'Save draft with optimistic revision conflict protection'), M.SaveRecipe, service.save, write=True)
    add('recipe.publish', ('发布方案', '發佈方案', 'Publish an immutable recipe version'), M.Publish, service.publish, write=True, events=('dataprep.recipe.published',))
    add('recipe.version', ('查看方案版本', '查看方案版本', 'Read a pinned recipe version'), M.Version,
        lambda c, i: decode(service.version(c.workspace_id, i.recipe_id, i.version)))
    add('recipe.retire', ('停用版本', '停用版本', 'Retire a version for new runs; existing runs continue'), M.Version, service.retire, write=True)
    add('recipe.archive', ('归档方案', '封存方案', 'Archive or restore a recipe'), M.Archive, service.archive, write=True)
    add('recipe.preview', ('预览样本', '預覽樣本', 'Transform actual selected samples of every input; never represents a full run'), M.Preview, service.preview)
    lifecycle = tuple('dataprep.run.' + s for s in ('queued', 'succeeded', 'partial', 'failed', 'cancelled'))
    add('run.start', ('全量运行', '完整執行', 'Queue deterministic processing of all pinned input rows'), M.StartRun, service.start,
        write=True, mode='async', cancellable=True, events=lifecycle)
    add('run.get', ('查看运行', '查看執行', 'Read persisted processing status, counts and result reference'), M.RunRef,
        lambda c, i: run_read(c, i.run_id))
    add('run.cancel', ('取消运行', '取消執行', 'Cancel queued or running processing before result commit'), M.RunRef, service.cancel, write=True, events=lifecycle)
    add('run.retry', ('重新运行', '重新執行', 'Create a separate run using the same pinned version, sources and parameters'), M.RunRef, service.retry,
        write=True, mode='async', cancellable=True, events=lifecycle)
    add('run.issues', ('查看异常行', '查看異常列', 'Read the complete issue list by page, with source row positions'), M.Issues,
        lambda c, i: (run_read(c, i.run_id), service.issues(c.workspace_id, i.run_id, i.offset, i.limit))[1])
