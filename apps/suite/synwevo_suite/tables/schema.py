"""Schema edits with dependency checks and revision-bound migration previews."""
from synwevo_core.capabilities import canonical, fingerprint
from synwevo_core.store import new_id, now
from . import fields as ft
from .formula import FormulaError, compile_formula
from .service import MAX_FIELDS, MAX_CELL_BYTES, MAX_TABLE_CELL_BYTES, refuse, unpack, check_rev


class Schema:
    def schema_rows(self, tid):
        rows = self.store.all('SELECT * FROM tb_records WHERE table_id=? ORDER BY seq', (tid,))
        for row in rows:
            row['cells'] = unpack(row['cells'], {})
        return rows

    def schema_version(self, c, tid, rev):
        table = self.table(c, tid, write=True, active=True)
        self.role(c, 'manage')
        check_rev(table['schema_rev'], rev)
        return table

    def dependencies(self, tid, fid):
        formulas = [f['id'] for f in self.fields(tid) if fid in f['config'].get('deps', [])]
        views = []
        for v in self.store.all('SELECT * FROM tb_views WHERE table_id=?', (tid,)):
            q = unpack(v['config'], {})
            refs = set(q.get('fields') or []) | {x['field_id'] for x in q.get('filters', []) + q.get('sort', [])}
            if fid in refs:
                views.append(v['id'])
        return {'formulas': formulas, 'views': views}

    def prepare_field(self, c, tid, spec, fid, fields, previous):
        field = spec.model_dump()
        field['id'] = fid
        if not field['name'].strip():
            refuse('name_required')
        field['name'] = field['name'].strip()
        if any(f['id'] != fid and f['name'].casefold() == field['name'].casefold() for f in fields):
            refuse('duplicate_field_name', 409)
        try:
            field['config'] = ft.clean_config(field['type'], field['config'], previous=previous['config'] if previous else None)
            if field['required'] and field['type'] not in ft.REQUIRED_OK:
                refuse('required_not_supported')
            if field['is_unique'] and field['type'] not in ft.UNIQUE_OK:
                refuse('unique_not_supported')
            if field['default'] is not None:
                if field['type'] not in ft.DEFAULT_OK:
                    refuse('default_not_supported')
                field['default'] = ft.normalize(field, field['default'])
            if field['type'] == 'link':
                self.table(c, field['config']['table_id'], active=True)
            if field['type'] == 'formula':
                compiled = compile_formula(field['config']['expression'], fields + ([field] if not previous else []), self_id=fid)
                field['config'].update(expression=compiled.expression, deps=compiled.deps, result_type=compiled.result_type)
        except (ft.CellError, FormulaError) as e:
            refuse('invalid_field', reason=e.code, **e.params)
        return field

    def save_field(self, c, i):
        self.schema_version(c, i.table_id, i.schema_rev)
        fields = self.fields(i.table_id)
        previous = next((f for f in fields if f['id'] == i.field_id), None)
        if i.field_id and not previous:
            refuse('field_not_found', 404)
        if not previous and len(fields) + len(self.fields(i.table_id, deleted=True)) >= MAX_FIELDS:
            refuse('field_limit', 409, limit=MAX_FIELDS)
        fid = i.field_id or new_id('fld')
        field = self.prepare_field(c, i.table_id, i.field, fid, fields, previous)
        if previous and all(previous[k] == field[k] for k in type(i.field).model_fields):
            return {'field_id': fid, 'schema_rev': i.schema_rev, 'changed': False}
        migrate = previous is not None and (previous['type'], previous['config']) != (field['type'], field['config'])
        deps = self.dependencies(i.table_id, fid) if previous else {'formulas': [], 'views': []}
        if migrate:
            if deps['formulas'] or deps['views']:
                refuse('field_dependencies', 409, **deps)
            if previous['type'] != field['type'] and (previous['type'] not in ft.CONVERTIBLE or field['type'] not in ft.CONVERTIBLE):
                refuse('conversion_not_supported')
            if previous['type'] == 'link' and previous['config']['table_id'] != field['config']['table_id']:
                refuse('conversion_not_supported')
            if previous['type'] == 'money' and field['type'] == 'money' and previous['config']['currency'] != field['config']['currency']:
                refuse('currency_conversion_required')
        new_fields = [field if f['id'] == fid else f for f in fields] + ([] if previous else [field])
        # Recompile the whole dependency graph to catch cycles and incompatible inferred types.
        for f in new_fields:
            if f['type'] == 'formula':
                try:
                    compiled = compile_formula(f['config']['expression'], new_fields, self_id=f['id'])
                except FormulaError as e:
                    refuse('invalid_field', reason=e.code, **e.params)
                f['config'].update(deps=compiled.deps, result_type=compiled.result_type)
        self.compute(new_fields, {})
        rows = self.schema_rows(i.table_id)
        prepared, counts, issues = [], {'unchanged': 0, 'ok': 0, 'lossy': 0, 'failed': 0}, []
        unique = set()
        for row in rows:
            cells = dict(row['cells'])
            old = cells.get(fid)
            if migrate and field['type'] not in ft.COMPUTED:
                value, outcome = ft.convert(previous, field, old)
            else:
                value, outcome = (old if previous else field['default']), 'unchanged'
            if not previous and not ft.is_empty(value):
                outcome = 'ok'
            if migrate and field['type'] not in ft.COMPUTED and value is not None:
                try:
                    value = ft.normalize(field, value)
                except ft.CellError:
                    value, outcome = None, 'failed'
            cells[fid] = value
            errors = self.compute(new_fields, cells)
            counts[outcome] += 1
            if not row['deleted_at']:
                if field['required'] and ft.is_empty(cells.get(fid)):
                    issues.append({'record_id': row['id'], 'reason': 'required_cell'})
                if field['is_unique']:
                    key = ft.unique_key(field, cells.get(fid))
                    if key is not None:
                        if key in unique:
                            issues.append({'record_id': row['id'], 'reason': 'unique_conflict'})
                        unique.add(key)
            prepared.append((row, cells, errors, outcome))
        sizes = [len(canonical(cells).encode()) for _, cells, _, _ in prepared]
        if any(size > MAX_CELL_BYTES for size in sizes) or sum(sizes) > MAX_TABLE_CELL_BYTES:
            refuse('table_size_limit', 413)
        token = fingerprint({'schema_rev': i.schema_rev, 'field': {k: field[k] for k in type(i.field).model_fields},
                             'records': [(r['id'], r['rev']) for r in rows]})
        normalized = {k: field[k] for k in type(i.field).model_fields}
        # Server-derived formula metadata isn't an editable setting.
        normalized['config'] = {k: v for k, v in normalized['config'].items() if k not in ('deps', 'result_type')}
        if i.preview:
            return {'preview_token': token, 'field': normalized, 'counts': counts, 'issues': issues[:100],
                    'issue_count': len(issues), 'dependencies': deps, 'records': len(rows)}
        if migrate and rows and i.preview_token != token:
            refuse('preview_required', 409)
        if issues:
            refuse('migration_constraints', 409, issues=issues[:100], issue_count=len(issues))
        ts = now()
        if previous:
            self.store.execute('UPDATE tb_fields SET name=?,description=?,type=?,config=?,required=?,is_unique=?,default_json=?,read_role=?,write_role=?,updated_at=? WHERE id=?',
                               (field['name'], field['description'], field['type'], canonical(field['config']), field['required'], field['is_unique'], canonical(field['default']), field['read_role'], field['write_role'], ts, fid))
        else:
            self.store.execute('INSERT INTO tb_fields (id,table_id,workspace_id,name,description,type,config,required,is_unique,default_json,read_role,write_role,position,status,created_by,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                               (fid, i.table_id, c.workspace_id, field['name'], field['description'], field['type'], canonical(field['config']), field['required'], field['is_unique'], canonical(field['default']), field['read_role'], field['write_role'], len(fields), 'active', c.user_id, ts, ts))
            if not fields:
                self.store.execute('UPDATE tb_tables SET primary_field_id=? WHERE id=?', (fid, i.table_id))
        self.store.execute('DELETE FROM tb_unique WHERE table_id=?', (i.table_id,))
        for row, cells, errors, outcome in prepared:
            if outcome in ('failed', 'lossy'):
                self.store.execute('INSERT INTO tb_leftovers (id,table_id,workspace_id,field_id,record_id,from_type,from_config,original_json,converted_json,outcome,schema_rev,created_by,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)',
                                   (new_id('left'), i.table_id, c.workspace_id, fid, row['id'], previous['type'], canonical(previous['config']), canonical(row['cells'].get(fid)), canonical(cells.get(fid)), outcome, i.schema_rev+1, c.user_id, ts))
            self.indexes(i.table_id, row['id'], new_fields, cells, live=not row['deleted_at'])
            diff = {k: {'before': row['cells'].get(k), 'after': v} for k, v in cells.items() if row['cells'].get(k) != v}
            if diff or unpack(row['formula_errors'], {}) != errors:
                self.store.execute('UPDATE tb_records SET cells=?,formula_errors=?,rev=rev+1,updated_at=?,updated_by=? WHERE id=?',
                                   (canonical(cells), canonical(errors), ts, c.user_id, row['id']))
                row['rev'] += 1
                self.change(c, i.table_id, 'record', 'migrated', record=row, diff=diff)
        self.store.execute('UPDATE tb_tables SET schema_rev=schema_rev+1,updated_at=? WHERE id=?', (ts, i.table_id))
        self.change(c, i.table_id, 'schema', 'field_saved', field_id=fid)
        return {'field_id': fid, 'schema_rev': i.schema_rev+1, 'changed': True, 'counts': counts}

    def field_state(self, c, i):
        self.schema_version(c, i.table_id, i.schema_rev)
        fields = self.fields(i.table_id)
        deleted = self.fields(i.table_id, deleted=True)
        f = next((f for f in fields + deleted if f['id'] == i.field_id), None)
        if not f:
            refuse('field_not_found', 404)
        if (f['status'] == 'deleted') == i.deleted:
            return {'field_id': f['id'], 'schema_rev': i.schema_rev, 'changed': False}
        deps = self.dependencies(i.table_id, i.field_id)
        rows = self.schema_rows(i.table_id)
        token = fingerprint({'field': i.field_id, 'deleted': i.deleted, 'schema_rev': i.schema_rev,
                             'records': [(r['id'], r['rev']) for r in rows]})
        if i.preview:
            return {'preview_token': token, 'records': sum(not ft.is_empty(r['cells'].get(f['id'])) for r in rows),
                    'dependencies': deps, 'retains_values': True}
        if i.preview_token != token:
            refuse('preview_required', 409)
        if i.deleted and (deps['formulas'] or deps['views']):
            refuse('field_dependencies', 409, **deps)
        if not i.deleted and any(x['name'].casefold() == f['name'].casefold() for x in fields):
            refuse('duplicate_field_name', 409)
        new_fields = [x for x in fields if x['id'] != f['id']] if i.deleted else fields + [f]
        for candidate in new_fields:
            if candidate['type'] == 'formula':
                try:
                    compile_formula(candidate['config']['expression'], new_fields, self_id=candidate['id'])
                except FormulaError as e:
                    refuse('field_dependencies', 409, reason=e.code)
        for row in rows:
            if not i.deleted and not row['deleted_at'] and f['required'] and ft.is_empty(row['cells'].get(f['id'])):
                refuse('required_cell', 409, record_id=row['id'])
            errors = self.compute(new_fields, row['cells'])
            self.indexes(i.table_id, row['id'], new_fields, row['cells'], live=not row['deleted_at'])
            self.store.execute('UPDATE tb_records SET cells=?,formula_errors=?,rev=rev+1,updated_at=? WHERE id=?',
                               (canonical(row['cells']), canonical(errors), now(), row['id']))
        self.store.execute('UPDATE tb_fields SET status=?,deleted_at=?,deleted_by=? WHERE id=?',
                           ('deleted' if i.deleted else 'active', now() if i.deleted else None, c.user_id if i.deleted else None, f['id']))
        self.store.execute('UPDATE tb_tables SET schema_rev=schema_rev+1,updated_at=? WHERE id=?', (now(), i.table_id))
        self.change(c, i.table_id, 'schema', 'field_archived' if i.deleted else 'field_restored', field_id=f['id'])
        return {'field_id': f['id'], 'schema_rev': i.schema_rev+1, 'changed': True}
