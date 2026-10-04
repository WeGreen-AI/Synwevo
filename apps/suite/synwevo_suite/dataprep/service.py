"""Workspace-scoped datasets, versioned recipes and durable, fenced processing runs."""
from __future__ import annotations

import gzip
import hashlib
import io
import json
import logging
import threading
import time

from synwevo_core.capabilities import canonical, fingerprint, record_event
from synwevo_core.errors import ServiceError
from synwevo_core.store import new_id, now
from ..common.files import available_blob, read_blob
from .engine import Table, Row, bounds, execute, refuse, Cancelled
from .files import inspect, read_file, export_csv, export_xlsx
from .history import check_sources, extract_result, extract_snapshot
from .models import (Definition, StartRun, ExtractSource, ExtractDataset, MAX_TABLE_BYTES,
                     MAX_ROWS, MAX_CELLS, MAX_TEXT, MAX_EXTRACT_DETAILS)

log = logging.getLogger('synwevo.dataprep')
ACTIVE = ('queued', 'running')
LEASE_SECONDS = 120


def decode(row):
    return {k[:-5] if k.endswith('_json') else k: json.loads(v) if k.endswith('_json') and v is not None else v
            for k, v in row.items() if k not in ('rows_blob', 'blob', 'scan', 'worker')}


def run_view(row, viewer):
    """A run as `viewer` reads it. Its correlation and operation run are the starter's own — free
    text they chose for their tracing, or their own run id — and reach nobody else (review R5;
    approvals requests and Forms requests never hand theirs to other members either)."""
    out = decode(row)
    if out.get('created_by') != viewer:
        out.pop('correlation_id', None)
        out.pop('op_run_id', None)
    return out


class DataPrep:
    def __init__(self, store, identity, caps):
        self.store, self.identity, self.caps = store, identity, caps
        self.lock = threading.Lock()

    def get(self, table, workspace, id):
        # table is always a local constant, never an operation input.
        row = self.store.one(f'SELECT * FROM dataprep_{table} WHERE id=? AND workspace_id=?', (id, workspace))
        if row is None:
            raise ServiceError(404, 'dataprep_not_found', '数据或方案不存在')
        if table == 'files':
            available_blob(self.store, row['blob'])
        return row

    def check_run_sources(self, workspace, user, row):
        seen = set()
        for ref in json.loads(row['inputs_json']).values():
            check_sources(self, workspace, user, ref['dataset_id'], seen)
        if row['output_dataset_id']:
            check_sources(self, workspace, user, row['output_dataset_id'], seen)

    def events_visible(self, user, workspace, events):
        """Which of these events this caller may know about: the rule `page` applies to the lists.
        A dataset or a run whose sources the caller may not read now — a source withdrawn or
        changed, an application the person lost, a scope a credential does not hold — is withheld,
        and so is every event about it.

        A dataset's sources are read once per batch however many runs used it: `reach` keeps what
        each check read, and a run is readable when every dataset it took and made is, within the
        same bound `check_sources` puts on one walk (`check_run_sources` shares one walk across a
        run's datasets; the union of the separate walks is that walk)."""
        reach, verdict, shown = {}, {}, set()

        def sources(dataset_id):
            if dataset_id not in reach:
                seen = set()
                try:
                    check_sources(self, workspace, user, dataset_id, seen)
                    reach[dataset_id] = frozenset(seen)
                except ServiceError:
                    reach[dataset_id] = None
            return reach[dataset_id]

        for e in events:
            key = (e['object_kind'], e['object_id'])
            if key not in verdict:
                try:
                    if key[0] == 'dataprep_dataset':
                        verdict[key] = sources(key[1]) is not None
                    elif key[0] == 'dataprep_run':
                        row = self.get('runs', workspace, key[1])
                        ids = [ref['dataset_id'] for ref in json.loads(row['inputs_json']).values()]
                        ids += [row['output_dataset_id']] if row['output_dataset_id'] else []
                        walked = [sources(d) for d in ids]
                        verdict[key] = all(w is not None for w in walked) and len(frozenset().union(*walked)) <= 64
                    else:
                        self.get('recipes', workspace, key[1])
                        verdict[key] = True
                except ServiceError:
                    verdict[key] = False
            if verdict[key]:
                shown.add(e['id'])
        return shown

    def page(self, table, workspace, offset, limit, user):
        selection = ('id,workspace_id,name,origin,file_id,run_id,columns_json,row_count,column_count,content_hash,status,created_by,created_at'
                     if table == 'datasets' else 'id,workspace_id,name,description,status,draft_rev,published_version,created_at,updated_at'
                     if table == 'recipes' else '*')
        rows = self.store.all(f'SELECT {selection} FROM dataprep_{table} WHERE workspace_id=? ORDER BY created_at DESC,id DESC LIMIT ? OFFSET ?',
                              (workspace, limit + 1, offset))
        items, hidden = [], 0
        for row in rows[:limit]:
            # A row whose sources this caller may not read now — a source withdrawn or changed, an
            # application the person lost, a scope a credential does not hold — is withheld, name
            # and all, and counted. One such row must not close the whole list, and the refusal for
            # it must not say where it came from; opening the row itself still refuses, as before.
            try:
                if table == 'datasets':
                    check_sources(self, workspace, user, row['id'])
                elif table == 'runs':
                    self.check_run_sources(workspace, user, row)
            except ServiceError:
                hidden += 1
                continue
            items.append(run_view(row, user) if table == 'runs' else decode(row))
        return {'items': items, 'next_offset': offset + limit if len(rows) > limit else None, 'hidden': hidden}

    def upload(self, workspace, user, filename, data, key):
        from synwevo_core.member_import import accept_upload
        name = filename.replace('\\', '/').rsplit('/', 1)[-1][:180]
        if any(ord(c) < 32 for c in name):
            refuse('file_type')
        ext = name.rsplit('.', 1)[-1].lower()
        if ext not in ('csv', 'xlsx'):
            refuse('file_type')
        available_blob(self.store, hashlib.sha256(data).hexdigest())
        scan = accept_upload(name, data)
        info = inspect(data, ext)
        with self.store.tx():
            self.caps.check_workspace(user, 'dataprep', workspace, 'write')
            old = self.store.one('SELECT * FROM dataprep_files WHERE workspace_id=? AND uploaded_by=? AND upload_key=?',
                                 (workspace, user, key))
            if old:
                if old['blob'] != hashlib.sha256(data).hexdigest() or old['filename'] != name:
                    raise ServiceError(409, 'idempotency_conflict', '重试参数已改变')
                return {**decode(old), **info, 'scan_status': old['scan'].get('status')}
            usage = self.store.one('SELECT COUNT(*) AS n,COALESCE(SUM(size),0) AS bytes FROM dataprep_files WHERE workspace_id=?', (workspace,))
            if usage['n'] >= 500 or usage['bytes'] + len(data) > 256 * 1024 * 1024:
                refuse('storage_limit')
            available_blob(self.store, hashlib.sha256(data).hexdigest())
            fid = new_id('dpf')
            blob = self.store.put_blob(data)
            self.store.insert('dataprep_files', {'id': fid, 'workspace_id': workspace, 'blob': blob,
                              'filename': name, 'format': ext, 'size': len(data), 'scan': scan,
                              'uploaded_by': user, 'created_at': now(), 'upload_key': key})
        return {**decode(self.get('files', workspace, fid)), **info, 'scan_status': scan.get('status')}

    def save_table(self, workspace, user, name, table, *, origin='file', source=None, file_id=None, run_id=None, notes=None):
        bounds(table)
        payload = {'columns': table.columns, 'rows': [{'values': r.values, 'origins': r.origins} for r in table.rows]}
        raw = canonical(payload).encode()
        problems = canonical(table.issues)
        stored_bytes = len(raw) + len(problems.encode())
        if stored_bytes > MAX_TABLE_BYTES:
            refuse('table_limit')
        quota = self.store.one('SELECT COUNT(*) AS n,COALESCE(SUM(stored_bytes),0) AS bytes FROM dataprep_datasets WHERE workspace_id=?', (workspace,))
        if quota['n'] >= 1000 or quota['bytes'] + stored_bytes > 256 * 1024 * 1024:
            refuse('storage_limit')
        blob = self.store.put_blob(gzip.compress(raw, mtime=0))
        did = new_id('dset')
        self.store.insert('dataprep_datasets', {'id': did, 'workspace_id': workspace, 'name': name, 'origin': origin,
                          'file_id': file_id, 'run_id': run_id, 'source_json': canonical(source or {}),
                          'columns_json': canonical(table.columns), 'read_notes_json': canonical(notes or []),
                          'read_problems_json': problems, 'row_count': len(table.rows),
                          'column_count': len(table.columns), 'rows_blob': blob, 'content_hash': fingerprint(payload),
                          'status': 'active', 'created_by': user, 'created_at': now(), 'stored_bytes': stored_bytes})
        return decode(self.get('datasets', workspace, did))

    def load(self, workspace, did):
        row = self.get('datasets', workspace, did)
        with gzip.GzipFile(fileobj=io.BytesIO(read_blob(self.store, row['rows_blob']))) as stream:
            raw = stream.read(MAX_TABLE_BYTES + 1)
        if len(raw) > MAX_TABLE_BYTES:
            refuse('table_limit')
        payload = json.loads(raw)
        if fingerprint(payload) != row['content_hash']:
            refuse('input_changed')
        return bounds(Table(payload['columns'], [Row(r['values'], r['origins']) for r in payload['rows']],
                            json.loads(row['read_problems_json'])))

    def dataset(self, workspace, did, offset=0, limit=50):
        meta = decode(self.get('datasets', workspace, did))
        table = self.load(workspace, did)
        # Keep operation outputs bounded even with wide cells/provenance; no silent truncation.
        rows, size = [], 0
        for row in table.rows[offset:offset + limit]:
            item = {'values': row.values, 'origins': row.origins}
            length = len(canonical(item).encode())
            if rows and size + length > 500_000:
                break
            if length > 500_000:
                # Aggregation provenance remains downloadable; a grid need not carry it all.
                item = {'values': row.values, 'origins': row.origins[:20], 'origin_count': len(row.origins)}
            rows.append(item)
            size += len(canonical(item).encode())
        meta.pop('read_problems', None)
        return {**meta, 'rows': rows, 'offset': offset, 'next_offset': offset + len(rows) if offset + len(rows) < len(table.rows) else None,
                'issue_count': len(table.issues)}

    def import_file(self, call, input):
        file = self.get('files', call.workspace_id, input.file_id)
        table, notes = read_file(read_blob(self.store, file['blob']), file['format'], input.options, file['id'])
        out = self.save_table(call.workspace_id, call.user_id, input.name, table, file_id=file['id'],
                              source={'file_id': file['id'], 'options': input.options.model_dump()}, notes=notes)
        call.touches('dataprep_dataset', out['id'])
        call.emit('dataprep.dataset.created', object_kind='dataprep_dataset', object_id=out['id'], data={'dataset_id': out['id']})
        # Issues are paged through dataset.issues, not duplicated in operation records.
        out.pop('read_problems', None)
        return out

    def records(self, call, input):
        table = bounds(Table(input.columns, [Row(r, [['records', n + 1]]) for n, r in enumerate(input.records)]))
        out = self.save_table(call.workspace_id, call.user_id, input.name, table, origin='records', source={'kind': 'records'})
        call.touches('dataprep_dataset', out['id'])
        call.emit('dataprep.dataset.created', object_kind='dataprep_dataset', object_id=out['id'], data={'dataset_id': out['id']})
        return out

    def form_records(self, call, input):
        columns, rows, snapshots = [], [], {}
        # Calls go through the shared registry, including Forms' current permissions.
        for sid in input.submission_ids:
            sub = self.caps.invoke('forms.submission.get', {'submission_id': sid}, user_id=call.user_id,
                                  workspace_id=call.workspace_id, via=call.via, parent_run_id=call.run_id,
                                  correlation_id=call.correlation_id, depth=call.depth + 1)['output']
            snapshots[sid] = fingerprint(sub['answers'])
            values = {}
            for k, v in sub['answers'].items():
                values[k] = v if isinstance(v, str) or v is None else canonical(v)
                if k not in columns:
                    columns.append(k)
            rows.append(Row(values, [[sid, 1]]))
        out = self.save_table(call.workspace_id, call.user_id, input.name, bounds(Table(columns, rows)),
                              origin='form', source={'submission_ids': input.submission_ids, 'answer_fingerprints': snapshots})
        call.touches('dataprep_dataset', out['id'])
        call.emit('dataprep.dataset.created', object_kind='dataprep_dataset', object_id=out['id'], data={'dataset_id': out['id']})
        return out

    def extract_records(self, call, input):
        """Atomic snapshot of explicit confirmed revisions. No sibling imports or source writes.

        Repeated fields zip by position; unequal lengths fail the entire import. Scalars
        broadcast. Empty detail lists produce zero rows, never a fabricated summary row.
        """
        rows, snapshots, result_bytes, row_bytes = [], [], 0, 0
        for ref in input.tasks:
            result = extract_result(self, call.workspace_id, call.user_id, ref)
            result_bytes += len(canonical(result).encode())
            if result_bytes > MAX_TABLE_BYTES:
                refuse('extract_source_limit')
            snapshots.append(extract_snapshot(result))
            selected, lengths = {}, set()
            for fid in input.field_ids:
                field = result['values'].get(fid)
                if not isinstance(field, dict) or 'value' not in field:
                    refuse('extract_field_missing', {'task_id': ref.task_id, 'field_id': fid})
                value, status = field['value'], field.get('status')
                if status not in ('extracted', 'unverified', 'missing'):
                    refuse('extract_value_invalid', {'task_id': ref.task_id, 'field_id': fid})
                parts = value if isinstance(value, list) else [value]
                if (len(parts) > MAX_EXTRACT_DETAILS
                        or any(v is not None and (not isinstance(v, str) or len(v) > MAX_TEXT) for v in parts)
                        or (status == 'missing' and value is not None and value != [])):
                    refuse('extract_value_invalid', {'task_id': ref.task_id, 'field_id': fid})
                if isinstance(value, list):
                    lengths.add(len(value))
                if status != 'missing':
                    selected[fid] = value
            if len(lengths) > 1:
                refuse('extract_detail_length', {'task_id': ref.task_id, 'lengths': sorted(lengths)})
            count = next(iter(lengths)) if lengths else 1
            if len(rows) + count > MAX_ROWS or (len(rows) + count) * len(input.field_ids) > MAX_CELLS:
                refuse('table_limit')
            for index in range(count):
                values = {fid: value[index] if isinstance(value, list) else value for fid, value in selected.items()}
                origins = [[ref.task_id, index + 1, ref.revision]]
                row_bytes += len(canonical({'values': values, 'origins': origins}).encode())
                if row_bytes > MAX_TABLE_BYTES:
                    refuse('table_limit')
                rows.append(Row(values, origins))
        source = ExtractSource(workspace_id=call.workspace_id, field_ids=input.field_ids,
                               detail_policy=input.detail_policy, tasks=snapshots)
        # The existing schema permits records, not a new extract enum. The typed source
        # is the authoritative discriminator; a new storage enum would require a migration.
        out = self.save_table(call.workspace_id, call.user_id, input.name, Table(input.field_ids, rows),
                              origin='records', source=source.model_dump(mode='json'))
        call.touches('dataprep_dataset', out['id'])
        call.emit('dataprep.dataset.created', object_kind='dataprep_dataset', object_id=out['id'], data={'dataset_id': out['id']})
        return {key: out[key] for key in ExtractDataset.model_fields}

    def recipe(self, workspace, rid):
        out = decode(self.get('recipes', workspace, rid))
        out['versions'] = [decode(v) for v in self.store.all('SELECT recipe_id,version,workspace_id,name,note,status,definition_hash,published_by,published_at FROM dataprep_recipe_versions WHERE recipe_id=? AND workspace_id=? ORDER BY version DESC', (rid, workspace))]
        return out

    def create(self, call, input):
        if self.store.one('SELECT COUNT(*) AS n FROM dataprep_recipes WHERE workspace_id=?', (call.workspace_id,))['n'] >= 1000:
            refuse('storage_limit')
        rid, ts = new_id('dpr'), now()
        self.store.insert('dataprep_recipes', {'id': rid, 'workspace_id': call.workspace_id, 'name': input.name,
                          'description': input.description, 'status': 'active', 'draft_json': canonical(input.definition.model_dump()),
                          'created_by': call.user_id, 'created_at': ts, 'updated_at': ts})
        call.touches('dataprep_recipe', rid)
        return self.recipe(call.workspace_id, rid)

    def save(self, call, input):
        row = self.get('recipes', call.workspace_id, input.recipe_id)
        if row['status'] != 'active':
            refuse('recipe_archived')
        changed = self.store.execute('UPDATE dataprep_recipes SET name=?,description=?,draft_json=?,draft_rev=draft_rev+1,updated_at=? WHERE id=? AND workspace_id=? AND draft_rev=?',
                                     (input.name, input.description, canonical(input.definition.model_dump()), now(), row['id'], call.workspace_id, input.draft_rev))
        if not changed:
            raise ServiceError(409, 'draft_conflict', '方案已被修改，请重新载入')
        call.touches('dataprep_recipe', row['id'])
        return self.recipe(call.workspace_id, row['id'])

    def publish(self, call, input):
        row = self.get('recipes', call.workspace_id, input.recipe_id)
        if row['status'] != 'active':
            refuse('recipe_archived')
        if row['draft_rev'] != input.draft_rev:
            raise ServiceError(409, 'draft_conflict', '方案已被修改，请重新载入')
        definition = Definition.model_validate_json(row['draft_json'])
        last = self.store.one('SELECT MAX(version) AS v FROM dataprep_recipe_versions WHERE recipe_id=? AND workspace_id=?', (row['id'], call.workspace_id))
        version = (last['v'] or 0) + 1
        if version > 500:
            refuse('storage_limit')
        payload = definition.model_dump()
        self.store.insert('dataprep_recipe_versions', {'recipe_id': row['id'], 'version': version,
                          'workspace_id': call.workspace_id, 'definition_json': canonical(payload), 'definition_hash': fingerprint(payload),
                          'name': row['name'], 'note': input.note, 'published_by': call.user_id, 'published_at': now()})
        self.store.update('dataprep_recipes', {'id': row['id'], 'workspace_id': call.workspace_id}, {'published_version': version, 'updated_at': now()})
        call.touches('dataprep_recipe', row['id'])
        call.emit('dataprep.recipe.published', object_kind='dataprep_recipe', object_id=row['id'], data={'recipe_id': row['id'], 'version': version})
        return self.recipe(call.workspace_id, row['id'])

    def archive(self, call, input):
        self.get('recipes', call.workspace_id, input.recipe_id)
        self.store.update('dataprep_recipes', {'id': input.recipe_id, 'workspace_id': call.workspace_id},
                          {'status': 'archived' if input.archived else 'active', 'archived_at': now() if input.archived else None, 'updated_at': now()})
        call.touches('dataprep_recipe', input.recipe_id)
        return self.recipe(call.workspace_id, input.recipe_id)

    def version(self, workspace, rid, version):
        self.get('recipes', workspace, rid)
        row = self.store.one('SELECT * FROM dataprep_recipe_versions WHERE recipe_id=? AND workspace_id=? AND version=?', (rid, workspace, version))
        if not row:
            raise ServiceError(404, 'version_missing', '方案版本不存在')
        return row

    def retire(self, call, input):
        self.version(call.workspace_id, input.recipe_id, input.version)
        self.store.update('dataprep_recipe_versions', {'recipe_id': input.recipe_id, 'workspace_id': call.workspace_id, 'version': input.version}, {'status': 'retired'})
        call.touches('dataprep_recipe', input.recipe_id)
        return self.recipe(call.workspace_id, input.recipe_id)

    def inputs(self, workspace, inputs, definition):
        required = {definition.source} | {s.source for s in definition.steps if s.type in ('join', 'append')}
        if set(inputs) != required:
            refuse('source_missing', {'required': sorted(required)})
        meta = {name: self.get('datasets', workspace, did) for name, did in inputs.items()}
        if sum(d['row_count'] for d in meta.values()) > MAX_ROWS or sum(d['row_count'] * d['column_count'] for d in meta.values()) > MAX_CELLS or sum(d['stored_bytes'] for d in meta.values()) > MAX_TABLE_BYTES:
            refuse('table_limit')
        return meta

    def preview(self, call, input):
        meta = self.inputs(call.workspace_id, input.inputs, input.definition)
        for ref in meta.values():
            check_sources(self, call.workspace_id, call.user_id, ref['id'])
        sources, ranges, before = {}, {}, None
        for name, row in meta.items():
            table = self.load(call.workspace_id, row['id'])
            sample = table.rows[input.offset:input.offset + input.limit]
            positions = [o[1] for r in sample for o in r.origins]
            low, high = (min(positions), max(positions)) if positions else (input.offset + 1, input.offset + input.limit)
            sample_issues = [i for i in table.issues if low <= (i.get('source_row') or 0) <= high]
            sources[name] = Table(table.columns, sample, sample_issues)
            ranges[name] = {'offset': input.offset, 'selected': len(sample), 'total': len(table.rows)}
            if name == input.definition.source:
                before = [{'values': dict(r.values), 'origins': r.origins[:20]} for r in sample]
        out, issues, reports = execute(input.definition, sources, input.parameters)
        result = {'sample_only': True, 'ranges': ranges, 'before': before, 'columns': out.columns,
                  'rows': [{'values': r.values, 'origins': r.origins[:20]} for r in out.rows[:200]],
                  'output_rows': len(out.rows), 'issues': issues, 'reports': reports}
        if len(canonical(result).encode()) > 400_000:
            refuse('preview_limit')
        return result

    def start(self, call, input):
        recipe = self.get('recipes', call.workspace_id, input.recipe_id)
        version = self.version(call.workspace_id, input.recipe_id, input.version)
        if recipe['status'] != 'active' or version['status'] != 'published':
            refuse('version_retired')
        definition = Definition.model_validate_json(version['definition_json'])
        meta = self.inputs(call.workspace_id, input.inputs, definition)
        for ref in meta.values():
            check_sources(self, call.workspace_id, call.user_id, ref['id'])
        if set(input.parameters) - set(definition.parameters):
            refuse('unknown_parameter')
        count = self.store.one("SELECT COUNT(*) AS n FROM dataprep_runs WHERE workspace_id=? AND status IN ('queued','running')", (call.workspace_id,))['n']
        if count >= 20:
            refuse('queue_limit')
        if self.store.one('SELECT COUNT(*) AS n FROM dataprep_runs WHERE workspace_id=?', (call.workspace_id,))['n'] >= 10_000:
            refuse('storage_limit')
        rid, ts = new_id('dpu'), now()
        self.store.insert('dataprep_runs', {'id': rid, 'workspace_id': call.workspace_id, 'recipe_id': input.recipe_id,
                          'recipe_version': input.version, 'definition_hash': version['definition_hash'],
                          'inputs_json': canonical({k: {'dataset_id': r['id'], 'content_hash': r['content_hash']} for k, r in meta.items()}),
                          'params_json': canonical(input.parameters), 'output_name': input.output_name or recipe['name'],
                          'status': 'queued', 'op_run_id': call.run_id, 'correlation_id': call.correlation_id, 'via': call.via,
                          'created_by': call.user_id, 'created_at': ts, 'updated_at': ts})
        call.touches('dataprep_run', rid)
        call.emit('dataprep.run.queued', object_kind='dataprep_run', object_id=rid, data={'run_id': rid})
        return {**decode(self.get('runs', call.workspace_id, rid)), '__run_status__': 'queued'}

    def cancel(self, call, input):
        row = self.get('runs', call.workspace_id, input.run_id)
        # The registry already requires current DataPrep write authority. Stopping work
        # must remain possible after a source is revoked or quarantined; return no data.
        if row['status'] in ACTIVE:
            self.store.update('dataprep_runs', {'id': row['id'], 'workspace_id': call.workspace_id}, {'cancel_requested': 1, 'updated_at': now()})
            if row['status'] == 'queued':
                self.finish(row, 'cancelled')
        call.touches('dataprep_run', row['id'])
        current = self.get('runs', call.workspace_id, row['id'])
        return {k: current[k] for k in ('id', 'status', 'cancel_requested')}

    def retry(self, call, input):
        row = self.get('runs', call.workspace_id, input.run_id)
        if row['status'] in ACTIVE:
            raise ServiceError(409, 'run_active', '运行尚未结束')
        return self.start(call, StartRun(recipe_id=row['recipe_id'], version=row['recipe_version'],
                          inputs={k: v['dataset_id'] for k, v in json.loads(row['inputs_json']).items()},
                          parameters=json.loads(row['params_json']), output_name=row['output_name']))

    def issues(self, workspace, rid, offset, limit):
        row = self.get('runs', workspace, rid)
        found = self.store.all('SELECT * FROM dataprep_run_issues WHERE run_id=? AND workspace_id=? ORDER BY seq LIMIT ? OFFSET ?', (rid, workspace, limit, offset))
        return {'items': [decode(r) for r in found], 'total': row['issue_total'], 'next_offset': offset + limit if offset + limit < row['issue_total'] else None}

    def finish(self, row, status, *, output=None, issues=None, reports=None, error=None):
        # Caller holds a transaction and, for workers, has checked its current lease token.
        if len(canonical(issues or []).encode()) > MAX_TABLE_BYTES:
            refuse('issue_limit')
        rid, ws = row['id'], row['workspace_id']
        self.store.update('dataprep_runs', {'id': rid, 'workspace_id': ws},
                          {'status': status, 'finished_at': now(), 'updated_at': now(),
                           'output_dataset_id': output['id'] if output else None,
                           'output_hash': output['content_hash'] if output else None,
                           'counts_json': canonical({'output_rows': output['row_count'] if output else 0, 'issues': len(issues or [])}),
                           'reports_json': canonical(reports or []), 'issue_total': len(issues or []),
                           'error_code': error.code if error else None, 'error_message': error.message if error else None})
        for seq, issue in enumerate(issues or []):
            self.store.insert('dataprep_run_issues', {'run_id': rid, 'workspace_id': ws, 'seq': seq,
                              'severity': issue.get('severity', 'error'), 'code': issue['code'],
                              'step_index': issue.get('step_index'), 'step_id': issue.get('step_id'),
                              'input_name': issue.get('input_name'), 'source_row': issue.get('source_row'),
                              'column_name': issue.get('column_name'), 'value': issue.get('value'),
                              'params_json': canonical(issue.get('params', {})), 'message': issue['message']})
        result = {'run_id': rid, 'status': status, 'output_dataset_id': output['id'] if output else None, 'issue_total': len(issues or [])}
        self.caps.update_run(row['op_run_id'], status, output=result, error=error)
        parent = self.store.one('SELECT depth FROM op_runs WHERE id=?', (row['op_run_id'],))
        with self.store.tx() as t:
            event = record_event(t, type_='dataprep.run.' + status, app='dataprep', workspace_id=ws,
                                 object_kind='dataprep_run', object_id=rid, data=result, user_id=row['created_by'],
                                 run_id=row['op_run_id'], correlation_id=row['correlation_id'], depth=parent['depth'] if parent else 0)
        self.store.after_commit(lambda: self.caps.announce([event]))

    def tick(self):
        """Claim a persisted job with a fencing token. Expired pure computations restart from
        immutable inputs; at most three attempts. Nothing becomes visible before atomic commit."""
        if not self.lock.acquire(blocking=False):
            return False
        try:
            with self.store.tx():
                # One job per database at a time bounds total memory across server processes.
                if self.store.one("SELECT id FROM dataprep_runs WHERE status='running' AND heartbeat_at>?", (now() - LEASE_SECONDS,)):
                    return False
                row = self.store.one("SELECT * FROM dataprep_runs WHERE status='queued' OR (status='running' AND heartbeat_at<=?) ORDER BY created_at,id LIMIT 1", (now() - LEASE_SECONDS,))
                if not row:
                    return False
                token = new_id('lease')
                self.store.update('dataprep_runs', {'id': row['id']}, {'worker': token, 'status': 'running', 'heartbeat_at': now(),
                                  'started_at': row['started_at'] or now(), 'attempts': row['attempts'] + 1})
            started, last = time.monotonic(), [0.0]

            def checkpoint(step=0, done=0, total=0, force=False):
                if not force and time.monotonic() - last[0] < .1:
                    return
                current = self.get('runs', row['workspace_id'], row['id'])
                if current['worker'] != token or current['status'] != 'running':
                    raise Cancelled()
                if current['cancel_requested']:
                    raise Cancelled()
                # A run a client started stops with that client's grant, and reads its sources
                # only as far as the grant still reaches; a person's own run is unaffected.
                with self.caps.resumed(row['op_run_id']):
                    self.caps.authorize(self.caps.registry.get('dataprep.run.start'), row['created_by'], row['workspace_id'])
                    for ref in json.loads(row['inputs_json']).values():
                        check_sources(self, row['workspace_id'], row['created_by'], ref['dataset_id'])
                if time.monotonic() - started > 90:
                    raise ServiceError(408, 'run_timeout', '运行超时，请缩小数据范围')
                self.store.execute("UPDATE dataprep_runs SET heartbeat_at=?,progress_json=?,updated_at=? WHERE id=? AND worker=? AND status='running'",
                                   (now(), canonical({'step': step, 'processed': done, 'total': total}), now(), row['id'], token))
                last[0] = time.monotonic()

            try:
                checkpoint(force=True)
                if row['attempts'] >= 3:
                    raise ServiceError(409, 'recovery_exhausted', '运行多次中断，请检查后重新运行')
                version = self.version(row['workspace_id'], row['recipe_id'], row['recipe_version'])
                if version['definition_hash'] != row['definition_hash']:
                    refuse('input_changed')
                sources = {}
                for name, ref in json.loads(row['inputs_json']).items():
                    meta = self.get('datasets', row['workspace_id'], ref['dataset_id'])
                    if meta['content_hash'] != ref['content_hash']:
                        refuse('input_changed')
                    sources[name] = self.load(row['workspace_id'], ref['dataset_id'])
                    checkpoint(force=True)
                table, issues, reports = execute(Definition.model_validate_json(version['definition_json']), sources, json.loads(row['params_json']), checkpoint)
                with self.store.tx():
                    checkpoint(force=True)
                    out = self.save_table(row['workspace_id'], row['created_by'], row['output_name'], table,
                                          origin='run', run_id=row['id'], source={'inputs': json.loads(row['inputs_json']),
                                          'recipe_id': row['recipe_id'], 'version': row['recipe_version'], 'parameters': json.loads(row['params_json'])})
                    self.finish(row, 'partial' if issues else 'succeeded', output=out, issues=issues, reports=reports)
            except (Cancelled, ServiceError) as err:
                with self.store.tx():
                    current = self.get('runs', row['workspace_id'], row['id'])
                    if current['worker'] == token and current['status'] == 'running':
                        self.finish(row, 'cancelled' if isinstance(err, Cancelled) else 'failed', error=err if isinstance(err, ServiceError) else None)
            except Exception:
                # Never log user cell values, file paths or exception representations.
                log.error('DataPrep run %s failed during bounded processing', row['id'])
                with self.store.tx():
                    current = self.get('runs', row['workspace_id'], row['id'])
                    if current['worker'] == token and current['status'] == 'running':
                        self.finish(row, 'failed', error=ServiceError(500, 'processing_failed', '处理失败，请重试或联系管理员'))
            return True
        finally:
            self.lock.release()

    def export(self, workspace, did, format):
        table = self.load(workspace, did)
        if format == 'csv':
            return export_csv(table), 'text/csv; charset=utf-8'
        if format == 'xlsx':
            return export_xlsx(table), 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        return canonical({'columns': table.columns, 'rows': [{'values': r.values, 'origins': r.origins} for r in table.rows]}).encode(), 'application/json'
