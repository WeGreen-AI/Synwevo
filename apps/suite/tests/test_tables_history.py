"""Change history names related records the way the grid does, under current permissions
(release acceptance defect 3: history showed rec_… ids)."""
import json


def out(s, operation, data=None, who='alice'):
    return s.caps.invoke('tables.'+operation, data or {}, user_id=s.people.uid(who), workspace_id=s.people.a)['output']


def table(s, name):
    return out(s, 'table.create', {'name': name})['id']


def field(s, tid, name, type='text', **kw):
    rev = out(s, 'table.get', {'table_id': tid})['schema_rev']
    return out(s, 'field.save', {'table_id': tid, 'schema_rev': rev, 'field': {'name': name, 'type': type, **kw}})['field_id']


def record(s, tid, cells, **kw):
    return out(s, 'record.create', {'table_id': tid, 'cells': cells, **kw})


def test_history_names_related_records_it_shows_and_only_those(suite):
    s = suite
    cust = table(s, 'Fixture customers')
    name = field(s, cust, 'Name')
    east = record(s, cust, {name: 'Acme East'})['id']
    south = record(s, cust, {name: 'Acme South'})['id']
    secret = record(s, cust, {name: 'Secret Co'}, read_role='admin')['id']
    orders = table(s, 'Fixture orders')
    link = field(s, orders, 'Customer', 'link', config={'table_id': cust, 'multiple': True})
    order = record(s, orders, {link: [east]})['id']
    out(s, 'record.update', {'table_id': orders, 'record_id': order, 'rev': 1, 'cells': {link: [south, secret]}})

    changes = out(s, 'history.list', {'table_id': orders, 'record_id': order})['changes']
    assert changes[0]['diff'][link] == {'before': [east], 'after': [south, secret]}
    assert changes[0]['titles'] == {east: 'Acme East', south: 'Acme South', secret: 'Secret Co'}

    # bob cannot read the restricted customer: neither its id nor its name reaches him.
    changes = out(s, 'history.list', {'table_id': orders, 'record_id': order}, who='bob')['changes']
    assert changes[0]['diff'][link] == {'before': [east], 'after': [south]}
    assert changes[0]['titles'] == {east: 'Acme East', south: 'Acme South'}
    assert secret not in json.dumps(changes) and 'Secret Co' not in json.dumps(changes)
