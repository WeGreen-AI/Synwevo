"""Deterministic bounded table transformations; missing, null and empty remain distinct.

Invalid casts/sort keys/aggregate values produce issues and exclude the offending row. Validation
can retain or drop rows explicitly. Null join keys do not match by default. `one` rejects duplicate
right keys structurally; `all` permits Cartesian matches subject to the output budget.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation, localcontext, ROUND_HALF_EVEN
from typing import Callable
import json

from synwevo_core.errors import ServiceError
from .models import Definition, MAX_CELLS, MAX_COLUMNS, MAX_ROWS, MAX_TABLE_BYTES, MAX_TEXT


class Cancelled(Exception):
    pass


def refuse(code: str, detail=None):
    raise ServiceError(422, code, '数据或处理规则不符合要求', detail)


@dataclass
class Row:
    values: dict[str, str | None]
    origins: list[list] = field(default_factory=list)


@dataclass
class Table:
    columns: list[str]
    rows: list[Row]
    issues: list[dict] = field(default_factory=list)


def bounds(table: Table):
    if len(table.columns) > MAX_COLUMNS or len(table.rows) > MAX_ROWS or len(table.columns) * len(table.rows) > MAX_CELLS:
        refuse('table_limit')
    if not table.columns or len(set(table.columns)) != len(table.columns) or any(not c or len(c) > 120 for c in table.columns):
        refuse('invalid_columns')
    size = refs = 0
    for r in table.rows:
        if set(r.values) - set(table.columns):
            refuse('unknown_column')
        for v in r.values.values():
            if v is not None and (not isinstance(v, str) or len(v) > MAX_TEXT):
                refuse('cell_limit')
        refs += len(r.origins)
        size += len(json.dumps([r.values, r.origins], ensure_ascii=False).encode())
        if size > MAX_TABLE_BYTES or refs > MAX_CELLS:
            refuse('table_limit')
    return table


def decimal(value):
    if not isinstance(value, str) or len(value) > 100:
        raise ValueError('invalid_decimal')
    try:
        d = Decimal(value)
    except InvalidOperation:
        raise ValueError('invalid_decimal') from None
    if not d.is_finite() or len(d.as_tuple().digits) > 38 or abs(d.as_tuple().exponent) > 38 or d.adjusted() > 37:
        raise ValueError('invalid_decimal')
    return d


def cast(value, step):
    if value is None or step.to == 'text':
        return value
    if step.to in ('integer', 'decimal'):
        d = decimal(value)
        with localcontext() as ctx:
            ctx.prec = 80
            out = d.quantize(Decimal(1).scaleb(-step.scale if step.to == 'decimal' else 0), rounding=ROUND_HALF_EVEN)
        if d != out and step.rounding == 'reject':
            raise ValueError('precision_loss')
        return format(out, 'f')
    if step.to == 'date':
        try:
            return date.fromisoformat(value).isoformat()
        except ValueError:
            raise ValueError('invalid_date') from None
    if step.to == 'datetime':
        try:
            dt = datetime.fromisoformat(value.replace('Z', '+00:00'))
            if dt.tzinfo is None:
                if step.offset is None:
                    raise ValueError('timezone_required')
                dt = datetime.fromisoformat(dt.isoformat() + step.offset)
            return dt.astimezone(timezone.utc).isoformat().replace('+00:00', 'Z')
        except (ValueError, OverflowError) as e:
            raise ValueError('timezone_required' if str(e) == 'timezone_required' else 'invalid_datetime') from None
    if step.to == 'boolean':
        if value.lower() not in ('true', 'false', '1', '0'):
            raise ValueError('invalid_boolean')
        return 'true' if value.lower() in ('true', '1') else 'false'
    raise ValueError('invalid_type')


def key(row, columns):
    return tuple(('missing', '') if c not in row.values else ('null', '') if row.values[c] is None
                 else ('value', row.values[c]) for c in columns)


def predicate(row, condition, parameters):
    c = condition.column
    if condition.op == 'is_missing':
        return c not in row.values
    if condition.op == 'is_null':
        return c in row.values and row.values[c] is None
    if condition.op == 'is_empty':
        return row.values.get(c) == ''
    if c not in row.values:
        return False
    value, other = row.values[c], condition.value
    if isinstance(other, str) and other.startswith('$'):
        other = parameters[other[1:]]
    if condition.numeric and value is not None and other is not None:
        value, other = decimal(value), decimal(other)
    if condition.op == 'eq':
        return value == other
    if condition.op == 'ne':
        return value != other
    if value is None or other is None:
        return False
    if condition.op == 'contains':
        return str(other) in str(value)
    return {'gt': lambda: value > other, 'ge': lambda: value >= other,
            'lt': lambda: value < other, 'le': lambda: value <= other}[condition.op]()


def execute(definition: Definition, sources: dict[str, Table], parameters: dict, checkpoint: Callable = lambda *a: None):
    if set(parameters) - set(definition.parameters):
        refuse('unknown_parameter')
    params = {**definition.parameters, **parameters}
    if definition.source not in sources:
        refuse('source_missing')
    table = Table(list(sources[definition.source].columns),
                  [Row(dict(r.values), list(r.origins)) for r in sources[definition.source].rows])
    issues, reports = [], []
    consumed = set()

    def consume(name):
        if name not in sources:
            refuse('source_missing', {'source': name})
        if name not in consumed:
            issues.extend(sources[name].issues)
            consumed.add(name)
        return sources[name]

    consume(definition.source)

    def need(columns, available=None):
        if set(columns) - set(available if available is not None else table.columns):
            refuse('unknown_column', {'columns': columns})

    def issue(row, step, index, code, column=None):
        if len(issues) >= MAX_CELLS:
            refuse('issue_limit')
        origin = row.origins[0] if row.origins else [None, None]
        issues.append({'severity': 'error', 'code': code, 'step_index': index, 'step_id': step.id,
                       'input_name': origin[0], 'source_row': origin[1], 'column_name': column,
                       'value': row.values.get(column) if column else None,
                       'message': step.message if step.type == 'validate' else code,
                       'params': {'origins': row.origins}})

    for index, step in enumerate(definition.steps):
        checkpoint(index, 0, len(table.rows))
        before, errors = len(table.rows), len(issues)
        kind = step.type
        if kind in ('trim', 'merge', 'sort', 'dedupe'):
            need(step.columns)
        if kind in ('cast', 'split'):
            need([step.column])
        if kind in ('filter', 'validate'):
            need([step.condition.column])
        if kind in ('trim', 'cast', 'split', 'merge', 'filter', 'validate'):
            targets = step.into if kind == 'split' else [step.target] if kind == 'merge' else []
            if len(set(targets)) != len(targets) or set(targets) & set(table.columns):
                refuse('column_collision')
            out = []
            for n, row in enumerate(table.rows):
                if n % 128 == 0:
                    checkpoint(index, n, before)
                try:
                    if kind == 'trim':
                        for c in step.columns:
                            if row.values.get(c) is not None:
                                row.values[c] = row.values[c].strip()
                    elif kind == 'cast' and step.column in row.values:
                        row.values[step.column] = cast(row.values[step.column], step)
                    elif kind == 'split' and step.column in row.values:
                        value = row.values[step.column]
                        pieces = [None] * len(step.into) if value is None else value.split(step.separator, len(step.into) - 1)
                        row.values.update(zip(step.into, pieces))
                    elif kind == 'merge':
                        values = [row.values.get(c) for c in step.columns]
                        row.values[step.target] = None if None in values and step.nulls == 'propagate' else step.separator.join(v or '' for v in values)
                    elif kind == 'filter' and not predicate(row, step.condition, params):
                        continue
                    elif kind == 'validate' and not predicate(row, step.condition, params):
                        issue(row, step, index, 'rule_failed', step.condition.column)
                        if step.on_error == 'drop':
                            continue
                    out.append(row)
                except (ValueError, InvalidOperation) as exc:
                    issue(row, step, index, str(exc) if isinstance(exc, ValueError) else 'invalid_decimal',
                          getattr(step, 'column', None) or getattr(getattr(step, 'condition', None), 'column', None))
            table = Table(table.columns + targets, out)
        elif kind == 'sort':
            decorated = []
            for n, row in enumerate(table.rows):
                if n % 128 == 0:
                    checkpoint(index, n, before)
                try:
                    k = tuple((0, '') if c not in row.values else (1, '') if row.values[c] is None else
                              (2, decimal(row.values[c]) if step.numeric else row.values[c]) for c in step.columns)
                    decorated.append((k, row))
                except ValueError:
                    issue(row, step, index, 'invalid_decimal')
            # Python's stable sort preserves the incoming order of equal keys, including descending.
            decorated.sort(key=lambda pair: pair[0], reverse=step.descending)
            table.rows = [r for _, r in decorated]
        elif kind == 'dedupe':
            seen, out = set(), []
            rows = table.rows if step.keep == 'first' else reversed(table.rows)
            for n, row in enumerate(rows):
                if n % 128 == 0:
                    checkpoint(index, n, before)
                k = key(row, step.columns)
                if k not in seen:
                    seen.add(k)
                    out.append(row)
            table.rows = out if step.keep == 'first' else list(reversed(out))
        elif kind == 'append':
            other = consume(step.source)
            if step.alignment == 'strict' and set(table.columns) != set(other.columns):
                refuse('append_columns')
            table.columns += [c for c in other.columns if c not in table.columns]
            table.rows += [Row(dict(r.values), list(r.origins)) for r in other.rows]
        elif kind == 'join':
            other = consume(step.source)
            need(step.left)
            need(step.right, other.columns)
            targets = [step.prefix + c for c in other.columns]
            if set(targets) & set(table.columns):
                refuse('column_collision')
            lookup = {}
            for n, row in enumerate(other.rows):
                if n % 128 == 0:
                    checkpoint(index, n, len(other.rows))
                k = key(row, step.right)
                if not step.nulls_equal and any(tag != 'value' for tag, _ in k):
                    continue
                bucket = lookup.setdefault(k, [])
                if bucket and step.matches == 'one':
                    refuse('duplicate_match')
                bucket.append(row)
            out = []
            for n, row in enumerate(table.rows):
                if n % 128 == 0:
                    checkpoint(index, n, before)
                k = key(row, step.left)
                matches = lookup.get(k, [])
                if step.matches == 'first':
                    matches = matches[:1]
                for other_row in matches or ([None] if step.how == 'left' else []):
                    values, origins = dict(row.values), list(row.origins)
                    if other_row is not None:
                        values.update({step.prefix + c: v for c, v in other_row.values.items()})
                        origins += other_row.origins
                    out.append(Row(values, origins))
                    if len(out) > MAX_ROWS or len(out) * (len(table.columns) + len(targets)) > MAX_CELLS:
                        refuse('join_limit')
            table = Table(table.columns + targets, out)
        elif kind == 'aggregate':
            need(step.group_by + [m.column for m in step.measures if m.column])
            columns = step.group_by + [m.target for m in step.measures]
            if len(set(columns)) != len(columns):
                refuse('column_collision')
            groups = {}
            with localcontext() as ctx:
                ctx.prec = 80  # max 38 input digits plus 5 count digits, never the default 28
                for n, row in enumerate(table.rows):
                    if n % 128 == 0:
                        checkpoint(index, n, before)
                    try:
                        values = [None if m.op == 'count' or row.values.get(m.column) is None else decimal(row.values[m.column]) for m in step.measures]
                    except ValueError:
                        issue(row, step, index, 'invalid_decimal')
                        continue
                    k = key(row, step.group_by)
                    group, acc = groups.setdefault(k, (Row({c: row.values[c] for c in step.group_by if c in row.values}), [None] * len(step.measures)))
                    group.origins += row.origins
                    for j, m in enumerate(step.measures):
                        value = values[j]
                        if m.op == 'count':
                            acc[j] = (acc[j] or 0) + 1
                        elif value is not None:
                            acc[j] = value if acc[j] is None else acc[j] + value if m.op == 'sum' else min(acc[j], value) if m.op == 'min' else max(acc[j], value)
                out = []
                for group, acc in groups.values():
                    group.values.update({m.target: None if acc[j] is None else format(acc[j], 'f') for j, m in enumerate(step.measures)})
                    out.append(group)
                table = Table(columns, out)
        bounds(table)
        reports.append({'step_id': step.id, 'type': kind, 'input_rows': before, 'output_rows': len(table.rows), 'issues': len(issues) - errors})
    checkpoint(len(definition.steps), len(table.rows), len(table.rows))
    return bounds(table), issues, reports
