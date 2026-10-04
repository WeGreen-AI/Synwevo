"""Import and export of Synwevo Tables as people use them: files round-trip, and every refused row
says which column and value it is about (release acceptance defects 2 and 5)."""
import csv
import io


def out(s, operation, data=None, who='alice'):
    return s.caps.invoke('tables.'+operation, data or {}, user_id=s.people.uid(who), workspace_id=s.people.a)['output']


def table(s, name):
    return out(s, 'table.create', {'name': name})['id']


def get(s, tid):
    return out(s, 'table.get', {'table_id': tid})


def field(s, tid, name, type='text', **kw):
    args = {'table_id': tid, 'schema_rev': get(s, tid)['schema_rev'], 'field': {'name': name, 'type': type, **kw}}
    return out(s, 'field.save', args)['field_id']


def record(s, tid, cells, **kw):
    return out(s, 'record.create', {'table_id': tid, 'cells': cells, **kw})


def query(s, tid, who='alice'):
    return out(s, 'record.query', {'table_id': tid, 'limit': 200}, who=who)


def upload(s, who, tid, rows):
    """Stage `rows` (a header row first) as a CSV import of `who`; returns the import id."""
    s.sign_in(who)
    buffer = io.StringIO()
    csv.writer(buffer).writerows(rows)
    response = s.post(f'/api/suite/tables/{tid}/imports', params={'workspace_id': s.people.a},
                      files={'file': ('fixture.csv', buffer.getvalue().encode(), 'text/csv')},
                      headers={'X-Synwevo-CSRF': 'fixture-csrf'})
    assert response.status_code == 200, response.text
    return response.json()['id']


def customers(s):
    """A customer table whose first field (its display field) is the name, and an order table
    with a multiple link to it. Fixture data."""
    cust = table(s, 'Fixture customers')
    name = field(s, cust, 'Name')
    ids = {}
    for label, extra in (('Acme East', {}), ('Acme South', {}), ('Twin', {}), ('Twin', {}),
                         ('Secret Co', {'read_role': 'admin'}), ('Archived Co', {})):
        ids.setdefault(label, []).append(record(s, cust, {name: label}, **extra)['id'])
    out(s, 'record.state', {'table_id': cust, 'record_id': ids['Archived Co'][0], 'rev': 1, 'deleted': True})
    # The same name in another table of the workspace is not a candidate.
    other = table(s, 'Fixture suppliers')
    record(s, other, {field(s, other, 'Name'): 'Acme East'})
    orders = table(s, 'Fixture orders')
    number = field(s, orders, 'Order No')
    link = field(s, orders, 'Customer', 'link', config={'table_id': cust, 'multiple': True})
    return cust, name, ids, orders, number, link


def test_import_resolves_links_by_the_name_export_writes(suite):
    s = suite
    cust, name, ids, orders, number, link = customers(s)
    record(s, orders, {number: 'SEED-1', link: ids['Acme East']})
    s.sign_in('alice')
    exported = s.get(out(s, 'export.create', {'table_id': orders})['url'])
    assert exported.status_code == 200
    rows = list(csv.reader(io.StringIO(exported.content.decode('utf-8-sig'))))
    assert rows == [['Order No', 'Customer'], ['SEED-1', 'Acme East']]

    # Re-import the exported file unchanged, then a hand-written one.
    iid = upload(s, 'alice', orders, rows)
    args = {'table_id': orders, 'import_id': iid, 'mapping': {'Order No': number, 'Customer': link}, 'atomic': True}
    assert out(s, 'import.apply', args)['issues'] == []
    assert out(s, 'import.apply', dict(args, preview=False))['succeeded'] == 1

    iid = upload(s, 'bob', orders, [['Order No', 'Customer'],
                                    ['A-1', 'Acme East'], ['A-2', ' acme south '], ['A-3', ids['Acme South'][0]],
                                    ['A-4', 'Acme East, Acme South'], ['A-5', '']])
    args = {'table_id': orders, 'import_id': iid, 'mapping': {'Order No': number, 'Customer': link}, 'atomic': True}
    preview = out(s, 'import.apply', args, who='bob')
    assert preview['valid'] == 5 and preview['issues'] == []
    result = out(s, 'import.apply', dict(args, preview=False), who='bob')
    assert result['succeeded'] == 5
    got = {r['cells'][number]: [x['title'] for x in r['relations'][link]] for r in query(s, orders)['records']}
    assert got['A-1'] == ['Acme East'] and got['A-2'] == ['Acme South'] and got['A-3'] == ['Acme South']
    assert got['A-4'] == ['Acme East', 'Acme South'] and got['A-5'] == []


def test_import_link_names_that_are_ambiguous_unknown_or_unreadable_are_row_errors(suite):
    s = suite
    cust, name, ids, orders, number, link = customers(s)
    iid = upload(s, 'bob', orders, [['Order No', 'Customer'],
                                    ['B-1', 'Twin'], ['B-2', 'Nobody'], ['B-3', 'Secret Co'],
                                    ['B-4', ids['Secret Co'][0]], ['B-5', 'Archived Co'], ['B-6', 'Acme East']])
    args = {'table_id': orders, 'import_id': iid, 'mapping': {'Order No': number, 'Customer': link}, 'atomic': False}
    preview = out(s, 'import.apply', args, who='bob')
    issues = {x['index']: (x['error'], x['detail']) for x in preview['issues']}
    assert preview['valid'] == 1
    assert issues[0] == ('link_ambiguous', {'column': 'Customer', 'value': 'Twin'})
    assert issues[1] == ('link_not_found', {'column': 'Customer', 'value': 'Nobody'})
    # A record bob cannot read answers exactly like one that does not exist, by name or by id.
    assert issues[2] == ('link_not_found', {'column': 'Customer', 'value': 'Secret Co'})
    assert issues[3] == ('link_not_found', {'column': 'Customer', 'value': ids['Secret Co'][0]})
    assert issues[4] == ('link_not_found', {'column': 'Customer', 'value': 'Archived Co'})
    # alice can read the restricted record, so for her the name resolves.
    iid = upload(s, 'alice', orders, [['Order No', 'Customer'], ['C-1', 'Secret Co']])
    preview = out(s, 'import.apply', {'table_id': orders, 'import_id': iid, 'mapping': {'Order No': number, 'Customer': link}})
    assert preview['issues'] == [] and preview['valid'] == 1


def test_import_link_names_follow_the_display_field_the_importer_can_read(suite):
    s = suite
    cust, name, ids, orders, number, link = customers(s)
    # Hide the display field from editors: bob's exports now name customers by number, and his
    # imports resolve those numbers and nothing else.
    out(s, 'field.save', {'table_id': cust, 'schema_rev': get(s, cust)['schema_rev'], 'field_id': name,
                          'field': {'name': 'Name', 'read_role': 'admin'}})
    record(s, orders, {number: 'SEED-1', link: ids['Acme East']})
    s.sign_in('bob')
    exported = s.get(out(s, 'export.create', {'table_id': orders}, who='bob')['url'])
    rows = list(csv.reader(io.StringIO(exported.content.decode('utf-8-sig'))))
    assert rows[1] == ['SEED-1', '1']
    iid = upload(s, 'bob', orders, [['Order No', 'Customer'], ['D-1', '1'], ['D-2', 'Acme East']])
    preview = out(s, 'import.apply', {'table_id': orders, 'import_id': iid, 'atomic': False,
                                      'mapping': {'Order No': number, 'Customer': link}}, who='bob')
    assert preview['valid'] == 1
    assert [(x['index'], x['error'], x['detail']) for x in preview['issues']] == [
        (1, 'link_not_found', {'column': 'Customer', 'value': 'Acme East'})]


def refused(fn):
    from synwevo_core.errors import ServiceError
    try:
        fn()
    except ServiceError as e:
        return e.code, e.detail
    raise AssertionError('not refused')


def test_a_duplicate_names_the_field_it_is_in_and_never_a_hidden_one(suite):
    s = suite
    tid = table(s, 'Fixture orders')
    number = field(s, tid, 'Order No', is_unique=True)
    # A unique field editors cannot read, filled by its default (fixture setup).
    hidden = field(s, tid, 'Audit key', is_unique=True, default='fixed', read_role='admin', write_role='admin')
    record(s, tid, {number: 'A-1'})
    assert refused(lambda: record(s, tid, {number: ' a-1 '})) == ('unique_conflict', {'field_id': number})
    code, detail = refused(lambda: out(s, 'record.create', {'table_id': tid, 'cells': {number: 'A-2'}}, who='bob'))
    assert code == 'unique_conflict' and hidden not in str(detail) and detail == {}


def test_import_row_errors_name_their_column_or_field(suite):
    s = suite
    tid = table(s, 'Fixture orders')
    number = field(s, tid, 'Order No', is_unique=True)
    qty = field(s, tid, 'Qty', 'number')
    required = field(s, tid, 'Region', required=True)
    record(s, tid, {number: 'A-1', required: 'South'})
    iid = upload(s, 'alice', tid, [['No', 'Quantity', 'Area'], ['A-2', 'many', 'East'], ['A-1', '1', 'East'],
                                   ['A-3', '2', ''], ['A-4', '3', 'West']])
    mapping = {'No': number, 'Quantity': qty, 'Area': required}
    preview = out(s, 'import.apply', {'table_id': tid, 'import_id': iid, 'mapping': mapping, 'atomic': False})
    issues = {x['index']: (x['error'], x['detail']) for x in preview['issues']}
    assert preview['valid'] == 1
    assert issues[0] == ('invalid_cell', {'reason': 'not_number', 'column': 'Quantity'})
    assert issues[1] == ('unique_conflict', {'field_id': number, 'column': 'No'})
    assert issues[2] == ('required_cell', {'field_id': required, 'column': 'Area'})
    # A required field the file does not map is named as a field.
    iid = upload(s, 'alice', tid, [['No'], ['A-5']])
    preview = out(s, 'import.apply', {'table_id': tid, 'import_id': iid, 'mapping': {'No': number}})
    assert [(x['error'], x['detail']) for x in preview['issues']] == [('required_cell', {'field_id': required})]
