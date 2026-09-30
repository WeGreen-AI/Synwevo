"""Revalidate old mutation results without executing their handlers again.

The coordinator's Operation.result_guard contract passes the complete op_runs row. References
are returned only while every input/output resource remains readable. Content-bearing previews
are deliberately refused: a fresh preview is cheap and cannot replay a mutation. Read operations
are re-inspected by the shared capability layer instead.
"""
from synwevo_core.errors import ServiceError
from .service import unpack, refuse


def result_guard(service):
    def guard(c, run):
        payload, output = unpack(run.get('input'), {}), unpack(run.get('output'), {})
        if run.get('op_version', 1) != 1 or not isinstance(payload, dict) or not isinstance(output, dict):
            refuse('historical_result_unavailable', 403)
        name = run.get('op', '').removeprefix('tables.')
        if run.get('user_id') != c.user_id or run.get('workspace_id') != c.workspace_id:
            refuse('historical_result_unavailable', 403)
        # These results contain an old preview or original staged file content, not just refs.
        if name == 'import.get' or payload.get('preview'):
            refuse('historical_result_unavailable', 403)
        tid = payload.get('table_id') or (output.get('id') if name == 'table.create' else None)
        if not tid:
            refuse('historical_result_unavailable', 403)
        service.table(c, tid)
        fields = {f['id']: f for f in service.visible_fields(c, service.fields(tid))}
        needed = set(payload.get('cells', {})) | set(payload.get('mapping', {}).values())
        record_ids = set()
        if payload.get('record_id'):
            record_ids.add(payload['record_id'])
        if name in ('record.create', 'record.update', 'record.state', 'record.restore_change') and output.get('id'):
            record_ids.add(output['id'])
        cells_to_check = [payload.get('cells', {})]
        for item in payload.get('items', []):
            if not isinstance(item, dict):
                refuse('historical_result_unavailable', 403)
            needed.update(item.get('cells', {}))
            cells_to_check.append(item.get('cells', {}))
            if item.get('record_id'):
                record_ids.add(item['record_id'])
        for item in output.get('items', []):
            if item.get('id'):
                record_ids.add(item['id'])
        if not needed <= fields.keys():
            refuse('historical_result_unavailable', 403)
        for rid in record_ids:
            service.record(c, tid, rid)
        for cells in cells_to_check:
            for fid, value in cells.items():
                if fields[fid]['type'] == 'link':
                    for rid in value if isinstance(value, list) else [value] if value else []:
                        service.record(c, fields[fid]['config']['table_id'], rid)
        if name.startswith('field.'):
            service.role(c, 'manage')
            target = payload.get('field', {}).get('config', {}).get('table_id')
            if target:
                service.table(c, target)
        if name in ('view.save', 'export.create'):
            from .models import Query
            q = payload.get('query', payload)
            query = Query.model_validate({k: v for k, v in q.items() if k in Query.model_fields})
            service.resolve_query(c, query)
            if name == 'export.create':
                service.export_right(c)
        if name == 'record.restore_change':
            # The old request may restore hidden cells without listing them in its input.
            change = service.store.one('SELECT diff FROM tb_changes WHERE id=? AND table_id=? AND workspace_id=?',
                                       (payload.get('change_id'), tid, c.workspace_id))
            if not change or not set(unpack(change['diff'], {})) <= fields.keys():
                refuse('historical_result_unavailable', 403)
        known = {'table.create', 'table.update', 'field.save', 'field.state', 'record.create', 'record.update',
                 'record.state', 'record.batch', 'record.restore_change', 'view.save', 'view.delete',
                 'import.apply', 'export.create'}
        if name not in known:
            refuse('historical_result_unavailable', 403)
    return guard
