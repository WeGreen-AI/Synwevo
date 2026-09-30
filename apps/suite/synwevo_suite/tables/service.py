"""Tables' transaction-bound business rules; all ingress goes through the operation registry.

Role thresholds are cumulative: workspace, table, record and field must all allow the action.
Views never grant access. Relations and formulas are projected through current source policies.
Archive retains links and cells; uniqueness is reserved by live records only. Restoring reruns
constraints and can conflict. Every effective write and its metadata-only event commit together.
"""
from __future__ import annotations

import json
import sqlite3
from decimal import Decimal, localcontext

from synwevo_core.capabilities import canonical, fingerprint
from synwevo_core.errors import ServiceError
from synwevo_core.store import new_id, now

from . import fields as ft
from .formula import FormulaError, compile_formula, evaluate, formula_input, store_result
from .models import RecordCreate, RecordUpdate, RecordState

MAX_RECORDS = 10000
MAX_FIELDS = 100
MAX_CELL_BYTES = 64 * 1024
MAX_TABLE_CELL_BYTES = 16 * 1024 * 1024


def unpack(v, fallback=None):
    return fallback if v is None else json.loads(v) if isinstance(v, str) else v


def refuse(code, status=422, **detail):
    raise ServiceError(status, code, '操作未完成，请检查输入、权限或最新修订。', detail)


def check_rev(actual, expected):
    if actual != expected:
        refuse('revision_conflict', 409, current_rev=actual)


class Tables:
    def __init__(self, store, identity, caps):
        self.store, self.identity, self.caps = store, identity, caps

    def role(self, c, need='read'):
        # Re-check inside the transaction, after the registry's outer authorization.
        return self.caps.check_workspace(c.user_id, 'tables', c.workspace_id, need)

    def table(self, c, tid, *, write=False, active=False):
        role = self.role(c, 'write' if write else 'read')
        row = self.store.one('SELECT * FROM tb_tables WHERE id=? AND workspace_id=?', (tid, c.workspace_id))
        if not row or not ft.readable(role, row['read_role']):
            refuse('table_not_found', 404)
        if write and not ft.readable(role, row['write_role']):
            refuse('table_read_only', 403)
        if active and row['status'] != 'active':
            refuse('table_archived', 409)
        return row

    def fields(self, tid, *, deleted=False):
        rows = self.store.all('SELECT * FROM tb_fields WHERE table_id=? AND status=? ORDER BY position,id',
                              (tid, 'deleted' if deleted else 'active'))
        for f in rows:
            f['config'] = unpack(f['config'], {})
            f['default'] = unpack(f.pop('default_json'), None)
            f['required'], f['is_unique'] = bool(f['required']), bool(f['is_unique'])
        return rows

    def visible_fields(self, c, fields):
        role = self.role(c)
        visible = {f['id'] for f in fields if ft.readable(role, f['read_role'])}
        # Hiding a dependency hides every derived formula, even after an ACL tightening.
        for _ in fields:
            previous = set(visible)
            for f in fields:
                if f['type'] == 'formula' and not set(f['config'].get('deps', [])) <= visible:
                    visible.discard(f['id'])
            if visible == previous:
                break
        out = []
        for f in fields:
            if f['id'] not in visible:
                continue
            projected = dict(f, can_write=(f['type'] not in ft.COMPUTED and ft.readable(role, f['write_role'])))
            if f['type'] == 'link':
                try:
                    self.table(c, f['config']['table_id'])
                except ServiceError as e:
                    if e.status not in (403, 404):
                        raise
                    projected['config'] = dict(f['config'], table_id=None)
                    projected['can_write'] = False
            out.append(projected)
        return out

    def get_table(self, c, tid):
        table = self.table(c, tid)
        visible = self.visible_fields(c, self.fields(tid))
        table.pop('next_seq', None)
        table['fields'] = visible
        if table['primary_field_id'] not in {f['id'] for f in visible}:
            table['primary_field_id'] = None
        table['can_write'] = ft.readable(self.role(c), table['write_role']) and table['status'] == 'active'
        table['can_manage'] = ft.readable(self.role(c), 'admin')
        table['deleted_fields'] = self.fields(tid, deleted=True) if table['can_manage'] else []
        table['views'] = []
        for row in self.store.all('SELECT * FROM tb_views WHERE table_id=? ORDER BY position,id', (tid,)):
            query = unpack(row['config'], {})
            refs = set(query.get('fields') or []) | {x['field_id'] for x in query.get('filters', []) + query.get('sort', [])}
            if refs <= {f['id'] for f in visible}:
                table['views'].append(dict(row, config=query))
        return table

    def list_tables(self, c, i):
        role = self.role(c)
        rows = self.store.all('SELECT * FROM tb_tables WHERE workspace_id=? AND status=? ORDER BY updated_at DESC,id',
                              (c.workspace_id, i.status))
        rows = [r for r in rows if ft.readable(role, r['read_role']) and i.q.casefold() in r['name'].casefold()]
        public = [{k: v for k, v in row.items() if k not in ('next_seq', 'primary_field_id')} for row in rows[i.offset:i.offset+i.limit]]
        return {'tables': public, 'total': len(rows)}

    def change(self, c, tid, kind, action, *, record=None, field_id=None, diff=None):
        rid = record['id'] if record else None
        self.store.execute('INSERT INTO tb_changes (id,table_id,workspace_id,record_id,kind,action,field_id,rev,diff,actor_id,via,run_id,at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)',
                           (new_id('chg'), tid, c.workspace_id, rid, kind, action, field_id,
                            record['rev'] if record else None, canonical(diff or {}), c.user_id, c.via, c.run_id, now()))
        c.touches('table_record' if record else 'table', rid or tid)
        c.emit('tables.' + kind + '.changed', object_kind='table_record' if record else 'table',
               object_id=rid or tid, data={'table_id': tid, 'record_id': rid, 'action': action,
                                         'rev': record['rev'] if record else None})

    def create_table(self, c, i):
        self.role(c, 'manage')
        if not i.name.strip():
            refuse('name_required')
        tid, ts = new_id('tbl'), now()
        self.store.execute('INSERT INTO tb_tables (id,workspace_id,name,description,status,created_by,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?)',
                           (tid, c.workspace_id, i.name.strip(), i.description, 'active', c.user_id, ts, ts))
        self.change(c, tid, 'table', 'created')
        return {'id': tid, 'schema_rev': 1}

    def edit_table(self, c, i):
        table = self.table(c, i.table_id, write=True)
        self.role(c, 'manage')
        check_rev(table['schema_rev'], i.schema_rev)
        values = i.model_dump(exclude_none=True, exclude={'table_id', 'schema_rev', 'archived'})
        if 'name' in values:
            values['name'] = values['name'].strip()
            if not values['name']:
                refuse('name_required')
        if i.archived is not None:
            values['status'] = 'archived' if i.archived else 'active'
        changed = {k: v for k, v in values.items() if table[k] != v}
        if changed:
            for k, v in changed.items():
                # Column identifiers are chosen from the closed model above.
                self.store.execute(f'UPDATE tb_tables SET {k}=? WHERE id=?', (v, i.table_id))
            self.store.execute('UPDATE tb_tables SET schema_rev=schema_rev+1,updated_at=?,archived_at=? WHERE id=?',
                               (now(), now() if values.get('status', table['status']) == 'archived' else None, i.table_id))
            self.change(c, i.table_id, 'table', 'updated')
        return {'id': i.table_id, 'schema_rev': i.schema_rev + bool(changed), 'changed': bool(changed)}

    def locate_record(self, c, i):
        row = self.store.one('SELECT table_id FROM tb_records WHERE id=? AND workspace_id=?',
                             (i.record_id, c.workspace_id))
        if not row:
            refuse('record_not_found', 404)
        record = self.record(c, row['table_id'], i.record_id)
        return {'table_id': record['table_id'], 'record_id': record['id']}

    def record(self, c, tid, rid, *, write=False):
        self.table(c, tid, write=write, active=write)
        row = self.store.one('SELECT * FROM tb_records WHERE id=? AND table_id=? AND workspace_id=?',
                             (rid, tid, c.workspace_id))
        role = self.role(c)
        if not row or not ft.readable(role, row['read_role']):
            refuse('record_not_found', 404)
        if write and not ft.readable(role, row['write_role']):
            refuse('record_read_only', 403)
        row['cells'] = unpack(row['cells'], {})
        row['formula_errors'] = unpack(row['formula_errors'], {})
        return row

    def project(self, c, row, fields=None):
        fields = fields if fields is not None else self.visible_fields(c, self.fields(row['table_id']))
        cells = unpack(row['cells'], {})
        out = {k: row[k] for k in ('id', 'table_id', 'seq', 'rev', 'created_at', 'updated_at', 'created_by', 'updated_by', 'deleted_at', 'read_role', 'write_role')}
        out['cells'], out['relations'] = {}, {}
        for f in fields:
            fid, typ = f['id'], f['type']
            value = cells.get(fid)
            if typ in ft.AUTO_TYPES:
                value = row[{'created_time': 'created_at', 'updated_time': 'updated_at'}.get(typ, typ)]
            if typ == 'link':
                links = []
                for rid in value or []:
                    try:
                        target = self.record(c, f['config']['table_id'], rid)
                        table = self.table(c, target['table_id'])
                    except ServiceError as e:
                        if e.status in (403, 404):
                            continue
                        raise
                    if target['deleted_at'] or table['status'] != 'active':
                        continue
                    tf = next((x for x in self.visible_fields(c, self.fields(target['table_id']))
                               if x['id'] == table['primary_field_id'] and x['type'] != 'link'), None)
                    title = ft.as_text(tf, target['cells'].get(tf['id'])) if tf else str(target['seq'])
                    links.append({'id': rid, 'table_id': target['table_id'], 'title': title})
                value = [x['id'] for x in links] or None
                out['relations'][fid] = links
            out['cells'][fid] = value
        errors = unpack(row['formula_errors'], {})
        out['formula_errors'] = {f['id']: errors[f['id']] for f in fields if f['id'] in errors}
        out['can_write'] = ft.readable(self.role(c), row['write_role'])
        return out

    def compute(self, fields, cells):
        errors, pending = {}, {f['id']: f for f in fields if f['type'] == 'formula'}
        values = {f['id']: formula_input(f, cells.get(f['id'])) for f in fields if f['type'] != 'formula'}
        while pending:
            ready = [f for f in pending.values() if not set(f['config']['deps']) & pending.keys()]
            if not ready:
                refuse('formula_cycle')
            for f in ready:
                fid = f['id']
                try:
                    if any(dep in errors for dep in f['config']['deps']):
                        raise FormulaError('dependency_error')
                    compiled = compile_formula(f['config']['expression'], fields, self_id=fid)
                    cells[fid] = store_result(compiled.result_type, f['config']['decimals'], evaluate(compiled.tree, values))
                    values[fid] = formula_input(f, cells[fid])
                except FormulaError as e:
                    cells[fid], values[fid], errors[fid] = None, None, e.code
                del pending[fid]
        return errors

    def normalize_cells(self, c, fields, incoming, old=None):
        visible = {f['id']: f for f in self.visible_fields(c, fields)}
        cells = dict(old or {})
        for fid, value in incoming.items():
            f = visible.get(fid)
            if not f or not f['can_write']:
                refuse('field_forbidden', 403)
            try:
                cells[fid] = ft.normalize(f, value)
            except ft.CellError as e:
                refuse('invalid_cell', field_id=fid, reason=e.code)
            if f['type'] == 'link':
                for target in cells[fid] or []:
                    row = self.record(c, f['config']['table_id'], target)
                    if row['deleted_at'] or self.table(c, row['table_id'])['status'] != 'active':
                        refuse('relation_unavailable', 409, field_id=fid)
        if old is None:
            for f in fields:
                if f['id'] not in cells and f['default'] is not None:
                    cells[f['id']] = f['default']
        for f in fields:
            if f['required'] and ft.is_empty(cells.get(f['id'])):
                refuse('required_cell', field_id=f['id'] if f['id'] in visible else None)
        return cells

    def check_size(self, tid, cells, previous=None):
        size = len(canonical(cells).encode('utf-8'))
        if size > MAX_CELL_BYTES:
            refuse('record_too_large', 413, limit=MAX_CELL_BYTES)
        total = self.store.one('SELECT COALESCE(SUM(length(CAST(cells AS BLOB))),0) AS n FROM tb_records WHERE table_id=?', (tid,))['n']
        if total + size - len(canonical(previous or {}).encode('utf-8')) > MAX_TABLE_CELL_BYTES:
            refuse('table_size_limit', 413, limit=MAX_TABLE_CELL_BYTES)

    def indexes(self, tid, rid, fields, cells, *, live=True):
        self.store.execute('DELETE FROM tb_unique WHERE record_id=?', (rid,))
        self.store.execute('DELETE FROM tb_links WHERE record_id=?', (rid,))
        for f in fields:
            value = cells.get(f['id'])
            if live and f['is_unique']:
                key = ft.unique_key(f, value)
                if key is not None:
                    try:
                        self.store.execute('INSERT INTO tb_unique VALUES (?,?,?,?)', (tid, f['id'], key, rid))
                    except sqlite3.IntegrityError:
                        # Never identify the conflicting record or disclose its value.
                        refuse('unique_conflict', 409)
            if f['type'] == 'link':
                for target in value or []:
                    self.store.execute('INSERT INTO tb_links VALUES (?,?,?)', (f['id'], rid, target))

    def create_record(self, c, i):
        table = self.table(c, i.table_id, write=True, active=True)
        if (i.read_role, i.write_role) != ('viewer', 'editor'):
            self.role(c, 'manage')
        count = self.store.one('SELECT COUNT(*) AS n FROM tb_records WHERE table_id=?', (i.table_id,))['n']
        if count >= MAX_RECORDS:
            refuse('record_limit', 409, limit=MAX_RECORDS)
        fields = self.fields(i.table_id)
        cells = self.normalize_cells(c, fields, i.cells)
        errors = self.compute(fields, cells)
        self.check_size(i.table_id, cells)
        rid, ts = new_id('rec'), now()
        self.store.execute('INSERT INTO tb_records (id,table_id,workspace_id,seq,cells,formula_errors,created_by,created_at,updated_by,updated_at,read_role,write_role) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',
                           (rid, i.table_id, c.workspace_id, table['next_seq'], canonical(cells), canonical(errors), c.user_id, ts, c.user_id, ts, i.read_role, i.write_role))
        self.store.execute('UPDATE tb_tables SET next_seq=next_seq+1,updated_at=? WHERE id=?', (ts, i.table_id))
        self.indexes(i.table_id, rid, fields, cells)
        self.change(c, i.table_id, 'record', 'created', record={'id': rid, 'rev': 1},
                    diff={k: {'before': None, 'after': v} for k, v in cells.items()})
        return {'id': rid, 'rev': 1, 'changed': True}

    def update_record(self, c, i):
        row = self.record(c, i.table_id, i.record_id, write=True)
        check_rev(row['rev'], i.rev)
        if row['deleted_at']:
            refuse('record_archived', 409)
        rr, wr = i.read_role or row['read_role'], i.write_role or row['write_role']
        if (rr, wr) != (row['read_role'], row['write_role']):
            self.role(c, 'manage')
        fields = self.fields(i.table_id)
        cells = self.normalize_cells(c, fields, i.cells, row['cells'])
        errors = self.compute(fields, cells)
        self.check_size(i.table_id, cells, row['cells'])
        diff = {k: {'before': row['cells'].get(k), 'after': v} for k, v in cells.items() if row['cells'].get(k) != v}
        if not diff and errors == row['formula_errors'] and (rr, wr) == (row['read_role'], row['write_role']):
            return {'id': row['id'], 'rev': row['rev'], 'changed': False}
        self.indexes(i.table_id, row['id'], fields, cells)
        self.store.execute('UPDATE tb_records SET cells=?,formula_errors=?,rev=rev+1,updated_at=?,updated_by=?,read_role=?,write_role=? WHERE id=? AND rev=?',
                           (canonical(cells), canonical(errors), now(), c.user_id, rr, wr, row['id'], i.rev))
        row['rev'] += 1
        self.change(c, i.table_id, 'record', 'updated', record=row, diff=diff)
        return {'id': row['id'], 'rev': row['rev'], 'changed': True}

    def record_state(self, c, i):
        row = self.record(c, i.table_id, i.record_id, write=True)
        check_rev(row['rev'], i.rev)
        if bool(row['deleted_at']) == i.deleted:
            return {'id': row['id'], 'rev': row['rev'], 'changed': False}
        fields = self.fields(i.table_id)
        if not i.deleted:
            self.normalize_cells(c, fields, {}, row['cells'])
        self.indexes(i.table_id, row['id'], fields, row['cells'], live=not i.deleted)
        self.store.execute('UPDATE tb_records SET deleted_at=?,deleted_by=?,updated_at=?,updated_by=?,rev=rev+1 WHERE id=?',
                           (now() if i.deleted else None, c.user_id if i.deleted else None, now(), c.user_id, row['id']))
        row['rev'] += 1
        self.change(c, i.table_id, 'record', 'archived' if i.deleted else 'restored', record=row)
        return {'id': row['id'], 'rev': row['rev'], 'changed': True}

    def batch(self, c, i):
        self.table(c, i.table_id, write=True, active=True)
        outcomes = []
        for index, item in enumerate(i.items):
            n_events = len(c.events)
            # Store's nested tx does not roll back independently. Each non-atomic row needs a savepoint.
            self.store.execute('SAVEPOINT tables_item')
            try:
                if item.action == 'create':
                    result = self.create_record(c, RecordCreate(table_id=i.table_id, cells=item.cells))
                else:
                    if not item.record_id or item.rev is None:
                        refuse('revision_required')
                    if item.action == 'update':
                        result = self.update_record(c, RecordUpdate(table_id=i.table_id, record_id=item.record_id, rev=item.rev, cells=item.cells))
                    else:
                        result = self.record_state(c, RecordState(table_id=i.table_id, record_id=item.record_id, rev=item.rev, deleted=item.action == 'archive'))
                outcomes.append({'index': index, 'ok': True, **result})
            except ServiceError as e:
                self.store.execute('ROLLBACK TO tables_item')
                del c.events[n_events:]
                if i.atomic:
                    raise
                outcomes.append({'index': index, 'ok': False, 'error': e.code, 'detail': e.detail})
            finally:
                self.store.execute('RELEASE tables_item')
        failed = sum(not x['ok'] for x in outcomes)
        return {'atomic': i.atomic, 'items': outcomes, 'succeeded': len(outcomes)-failed, 'failed': failed,
                '__run_status__': 'partial' if failed else 'succeeded'}
