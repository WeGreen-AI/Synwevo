"""A bounded query language, applied only to the caller's current authorized projection."""
from datetime import datetime
from decimal import Decimal, localcontext
from synwevo_core.capabilities import canonical
from synwevo_core.errors import ServiceError
from synwevo_core.store import new_id, now
from . import fields as ft
from .models import Query, RecordUpdate
from .service import MAX_RECORDS, refuse, unpack, check_rev


def comparable(f, value):
    if value is None:
        return None
    if f['type'] == 'money':
        return Decimal(value['amount'])
    if f['type'] == 'number' or (f['type'] == 'formula' and f['config']['result_type'] == 'number'):
        return Decimal(value)
    if f['type'] == 'datetime':
        return datetime.fromisoformat(value).timestamp()
    if isinstance(value, list):
        return canonical(value)
    return value.casefold() if isinstance(value, str) else value


class Queries:
    def resolve_query(self, c, i):
        self.table(c, i.table_id)
        if i.view_id:
            row = self.store.one('SELECT * FROM tb_views WHERE id=? AND table_id=? AND workspace_id=?',
                                 (i.view_id, i.table_id, c.workspace_id))
            if not row:
                refuse('view_not_found', 404)
            config = unpack(row['config'], {})
            i = Query.model_validate({**config, 'table_id': i.table_id, 'offset': i.offset, 'limit': i.limit, 'view_id': None})
        fields = self.visible_fields(c, self.fields(i.table_id))
        visible = {f['id']: f for f in fields}
        requested = set(i.fields or []) | {x.field_id for x in i.filters + i.sort}
        if not requested <= visible.keys():
            refuse('field_forbidden', 403)
        return i, fields, visible

    def query_rows(self, c, i):
        i, fields, visible = self.resolve_query(c, i)
        rank = ft.ROLE_RANK[self.role(c)]
        allowed = [r for r in ft.READ_ROLES if ft.ROLE_RANK[r] <= rank]
        marks = ','.join('?' for _ in allowed)
        rows = self.store.all(f'SELECT * FROM tb_records WHERE table_id=? AND workspace_id=? AND read_role IN ({marks}) AND deleted_at IS {"NOT " if i.deleted else ""}NULL ORDER BY seq LIMIT ?',
                              (i.table_id, c.workspace_id, *allowed, MAX_RECORDS+1))
        if len(rows) > MAX_RECORDS:
            refuse('query_limit', 409, limit=MAX_RECORDS)
        # Projection precedes search/filter/sort/count: hidden cells and linked IDs cannot act as oracles.
        rows = [self.project(c, r, fields) for r in rows]
        if i.q:
            needle = i.q.casefold()
            rows = [r for r in rows if any(needle in ft.as_text(f, r['cells'].get(f['id']),
                    titles={x['id']: x['title'] for x in r['relations'].get(f['id'], [])}).casefold() for f in fields)]
        for condition in i.filters:
            f = visible[condition.field_id]
            val = condition.value
            if condition.op not in ('empty', 'not_empty', 'contains'):
                try:
                    target_field = f
                    if f['type'] == 'formula':
                        target_field = dict(f, type=f['config']['result_type'])
                    if target_field['type'] in ft.AUTO_TYPES:
                        if not isinstance(val, (str, int, float)) or isinstance(val, bool):
                            refuse('invalid_filter')
                    else:
                        val = ft.normalize(target_field, val)
                    val = comparable(f, val)
                except (ft.CellError, ValueError, TypeError, ArithmeticError):
                    refuse('invalid_filter')
            elif condition.op == 'contains' and (not isinstance(val, str) or len(val) > 200):
                refuse('invalid_filter')
            def matches(row):
                cell = row['cells'].get(f['id'])
                if condition.op == 'empty':
                    return ft.is_empty(cell)
                if condition.op == 'not_empty':
                    return not ft.is_empty(cell)
                if condition.op == 'contains':
                    return val.casefold() in ft.as_text(f, cell, titles={x['id']: x['title'] for x in row['relations'].get(f['id'], [])}).casefold()
                v = comparable(f, cell)
                if condition.op == 'eq':
                    return v == val
                if condition.op == 'ne':
                    return v != val
                if v is None or val is None:
                    return False
                try:
                    return {'gt': lambda: v > val, 'gte': lambda: v >= val,
                            'lt': lambda: v < val, 'lte': lambda: v <= val}[condition.op]()
                except TypeError:
                    refuse('invalid_filter')
            rows = [r for r in rows if matches(r)]
        for spec in reversed(i.sort):
            field = visible[spec.field_id]
            present = [r for r in rows if r['cells'].get(spec.field_id) is not None]
            missing = [r for r in rows if r['cells'].get(spec.field_id) is None]
            present.sort(key=lambda r: comparable(field, r['cells'][spec.field_id]), reverse=spec.direction == 'desc')
            rows = present + missing
        if i.fields is not None:
            fields = [visible[fid] for fid in dict.fromkeys(i.fields)]
            for r in rows:
                for key in ('cells', 'relations', 'formula_errors'):
                    r[key] = {k: v for k, v in r[key].items() if k in i.fields}
        return rows, fields, i

    def query(self, c, i):
        rows, fields, i = self.query_rows(c, i)
        page = rows[i.offset:i.offset+i.limit]
        # The registry bounds persisted output. Limit by bytes as well as row count, honestly.
        while page and len(canonical(page).encode()) > 700000:
            page.pop()
        if not page and rows[i.offset:i.offset+1]:
            refuse('record_output_too_large', 413)
        return {'records': page, 'total': len(rows), 'fields': fields,
                'next_offset': i.offset+len(page) if i.offset+len(page) < len(rows) else None}

    def aggregate(self, c, i):
        rows, fields, _ = self.query_rows(c, i)
        if i.field_id and i.field_id not in {f['id'] for f in fields}:
            refuse('field_forbidden', 403)
        if i.function == 'count':
            return {'value': len(rows)}
        f = next((f for f in fields if f['id'] == i.field_id), None)
        if not f:
            refuse('field_forbidden', 403)
        if f['type'] not in ('number', 'money') and not (f['type'] == 'formula' and f['config']['result_type'] == 'number'):
            refuse('aggregate_requires_number')
        values = [comparable(f, r['cells'].get(f['id'])) for r in rows if r['cells'].get(f['id']) is not None]
        with localcontext() as ctx:
            ctx.prec = 40
            value = None if not values else {'sum': lambda: sum(values), 'min': lambda: min(values), 'max': lambda: max(values),
                                             'average': lambda: sum(values)/len(values)}[i.function]()
        return {'value': str(value) if value is not None else None, 'count': len(values)}

    def save_view(self, c, i):
        self.table(c, i.table_id, write=True, active=True)
        if i.query.table_id != i.table_id or i.query.view_id:
            refuse('invalid_view')
        self.resolve_query(c, i.query)
        config = i.query.model_dump(exclude={'offset', 'limit', 'view_id'})
        row = None
        if i.view_id:
            row = self.store.one('SELECT * FROM tb_views WHERE id=? AND table_id=?', (i.view_id, i.table_id))
            if not row:
                refuse('view_not_found', 404)
            if row['created_by'] != c.user_id:
                self.role(c, 'manage')
            check_rev(row['rev'], i.rev)
            if unpack(row['config']) == config and row['name'] == i.name:
                return {'id': row['id'], 'rev': row['rev'], 'changed': False}
        vid, ts = i.view_id or new_id('view'), now()
        if row:
            self.store.execute('UPDATE tb_views SET name=?,config=?,rev=rev+1,updated_at=? WHERE id=?', (i.name, canonical(config), ts, vid))
        else:
            if self.store.one('SELECT COUNT(*) AS n FROM tb_views WHERE table_id=?', (i.table_id,))['n'] >= 100:
                refuse('view_limit', 409)
            self.store.execute('INSERT INTO tb_views (id,table_id,workspace_id,name,config,position,created_by,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?)',
                               (vid, i.table_id, c.workspace_id, i.name, canonical(config), ts, c.user_id, ts, ts))
        self.change(c, i.table_id, 'view', 'saved')
        return {'id': vid, 'rev': row['rev']+1 if row else 1, 'changed': True}

    def delete_view(self, c, i):
        self.table(c, i.table_id, write=True, active=True)
        row = self.store.one('SELECT * FROM tb_views WHERE id=? AND table_id=?', (i.view_id, i.table_id))
        if not row:
            refuse('view_not_found', 404)
        if row['created_by'] != c.user_id:
            self.role(c, 'manage')
        check_rev(row['rev'], i.rev)
        self.store.execute('DELETE FROM tb_views WHERE id=?', (i.view_id,))
        self.change(c, i.table_id, 'view', 'deleted')
        return {'id': i.view_id, 'deleted': True}

    def history(self, c, i):
        self.table(c, i.table_id)
        fields = self.visible_fields(c, self.fields(i.table_id))
        if i.record_id:
            self.record(c, i.table_id, i.record_id)
        sql = 'SELECT * FROM tb_changes WHERE table_id=? AND workspace_id=?'
        args = [i.table_id, c.workspace_id]
        if i.record_id:
            sql += ' AND record_id=?'
            args.append(i.record_id)
        if i.before:
            sql += ' AND seq<?'
            args.append(i.before)
        sql += ' ORDER BY seq DESC'
        out, last = [], None
        # Scan in bounded chunks so sparse ACLs never leak counts or history existence.
        rows = self.store.all(sql + ' LIMIT 2000', tuple(args))
        for row in rows:
            last = row['seq']
            if row['kind'] == 'record':
                try:
                    current = self.record(c, i.table_id, row['record_id'])
                except ServiceError as e:
                    if e.status in (403, 404):
                        continue
                    raise
                diff = unpack(row['diff'], {})
                # Do not show old relation identifiers without resolving their CURRENT permissions.
                projected = {}
                for edge in ('before', 'after'):
                    cells = {k: v.get(edge) for k, v in diff.items()}
                    projected[edge] = self.project(c, dict(current, cells=cells), fields)['cells']
                row['diff'] = {f['id']: {edge: projected[edge].get(f['id']) for edge in ('before', 'after')}
                               for f in fields if f['id'] in diff}
            elif not ft.readable(self.role(c), 'admin'):
                continue
            else:
                row['diff'] = {}
            actor = self.identity.get_user(row['actor_id']) if row['actor_id'] else None
            row['actor_name'] = actor.get('display_name') if actor else None
            out.append(row)
            if len(out) >= i.limit:
                break
        return {'changes': out, 'next_before': last if rows and (len(out) >= i.limit or len(rows) == 2000) else None}

    def restore_change(self, c, i):
        row = self.record(c, i.table_id, i.record_id, write=True)
        check_rev(row['rev'], i.rev)
        change = self.store.one('SELECT * FROM tb_changes WHERE id=? AND table_id=? AND record_id=? AND workspace_id=?',
                                (i.change_id, i.table_id, i.record_id, c.workspace_id))
        if not change:
            refuse('change_not_found', 404)
        diff = unpack(change['diff'], {})
        writable = {f['id'] for f in self.visible_fields(c, self.fields(i.table_id)) if f['can_write']}
        active_ids = {f['id'] for f in self.fields(i.table_id) if f['type'] not in ft.COMPUTED}
        if not set(diff) & active_ids <= writable:
            refuse('field_forbidden', 403)
        cells = {k: v['before'] for k, v in diff.items() if k in writable}
        if not cells:
            refuse('nothing_to_restore', 409)
        return self.update_record(c, RecordUpdate(table_id=i.table_id, record_id=i.record_id, rev=i.rev, cells=cells))
