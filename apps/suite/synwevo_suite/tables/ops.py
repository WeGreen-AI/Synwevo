"""Versioned operations shared by pages, API, flows and Agent. Mutations return references,
not saved business bodies; content is read through the current authorization projection."""
from synwevo_core.capabilities import Operation
from . import models as m
from .guard import result_guard

EVENTS = ('tables.table.changed', 'tables.schema.changed', 'tables.record.changed', 'tables.view.changed')


def register(registry, service):
    guard = result_guard(service)

    def add(name, model, method, title, en, hant, *, need='read', summary=None):
        write = need != 'read'
        # Older worktrees can still run focused tests; deployment must include the shared guard contract.
        guards = {'result_guard': guard} if 'result_guard' in Operation.__dataclass_fields__ and write else {}
        registry.register(Operation(**guards, id='tables.'+name, app='tables', input=model,
            title={'zh-Hans': title, 'zh-Hant': hant, 'en': en}, summary=summary or en,
            need=need, side_effect='write' if write else 'none', agent='confirm' if write else 'auto',
            object_kind='table', events=EVENTS if write else (),
            handler=method))

    add('table.list', m.ListTables, service.list_tables, '列出数据表', 'List authorized tables', '列出資料表')
    add('table.get', m.Ref, lambda c, i: service.get_table(c, i.table_id), '查看数据表', 'Read table fields and saved views', '查看資料表')
    add('table.create', m.CreateTable, service.create_table, '新建数据表', 'Create a table', '新增資料表', need='manage')
    add('table.update', m.TableEdit, service.edit_table, '设置数据表', 'Rename, archive, restore or set table permissions', '設定資料表', need='manage')
    add('field.save', m.FieldSave, service.save_field, '配置字段', 'Configure a typed field', '設定欄位', need='manage',
        summary='Stable field IDs. Schema revision required. Type/config changes require a preview token bound to the latest schema and record revisions; lossy originals retained in history.')
    add('field.state', m.FieldState, service.field_state, '删除或恢复字段', 'Archive or restore a field', '刪除或還原欄位', need='manage')
    add('record.query', m.Query, service.query, '查询记录', 'Query authorized records', '查詢記錄',
        summary='Bounded filters, stable sorting and offset pagination over current authorized fields/records/relations. Maximum 10,000 records per table; page size 1–200; next_offset is authoritative.')
    add('record.get', m.RecordRef, lambda c, i: service.project(c, service.record(c, i.table_id, i.record_id)), '查看记录', 'Read a record with authorized relations', '查看記錄')
    add('record.locate', m.RecordLocation, service.locate_record, '打开记录', 'Resolve an authorized record link', '開啟記錄')
    add('record.create', m.RecordCreate, service.create_record, '新建记录', 'Create a record', '新增記錄', need='write')
    add('record.update', m.RecordUpdate, service.update_record, '更新记录', 'Update a record with revision conflict detection', '更新記錄', need='write')
    add('record.state', m.RecordState, service.record_state, '归档或恢复记录', 'Archive or restore a record', '封存或還原記錄', need='write')
    add('record.batch', m.Batch, service.batch, '批量修改记录', 'Batch record writes', '批次修改記錄', need='write',
        summary='At most 100 items. atomic=true rolls back the whole batch on failure; false commits successful items with per-item errors and partial status. No-op updates emit no events.')
    add('record.aggregate', m.Aggregate, service.aggregate, '汇总记录', 'Aggregate only authorized records and fields', '彙總記錄')
    add('record.restore_change', m.RestoreChange, service.restore_change, '撤销记录变更', 'Restore previous cell values with current constraints and permissions', '復原記錄變更', need='write')
    add('view.save', m.ViewSave, service.save_view, '保存视图', 'Save or update a revision-checked query', '儲存檢視', need='write')
    add('view.delete', m.ViewDelete, service.delete_view, '删除视图', 'Delete a saved query', '刪除檢視', need='write')
    add('history.list', m.History, service.history, '查看变更记录', 'Read permission-filtered change history', '查看變更記錄')
    add('import.get', m.ImportRef, service.import_get, '查看导入文件', 'Read a private staged import', '查看匯入檔案', need='write')
    add('import.apply', m.ImportApply, service.import_apply, '导入记录', 'Preview or apply a mapped import once', '匯入記錄', need='write')
    add('export.create', m.Export, service.export_create, '导出记录', 'Create a private CSV/XLSX download ticket', '匯出記錄', need='write',
        summary='Export requires current export permission. Ticket expires in 10 minutes; downloads reauthorize table/record/field/relation policies and query current records. CSV/XLSX formulas cannot execute.')
