"""Pure field/formula vectors: exact values, hostile syntax and bounded work."""
from decimal import Decimal
import pytest
from synwevo_suite.tables import fields as f
from synwevo_suite.tables.formula import compile_formula, evaluate, FormulaError


def spec(type, **cfg):
    return {'id': 'fld_000000000001', 'name': 'Fixture field', 'type': type, 'config': f.clean_config(type, cfg)}


@pytest.mark.parametrize('typ,cfg,value,expected', [
    ('text', {}, '001', '001'), ('number', {'decimals': 2}, '0.10', '0.10'),
    ('number', {'decimals': 10}, '999999999999999.1234567890', '999999999999999.1234567890'),
    ('money', {'currency': 'HKD'}, {'amount': '12.30', 'currency': 'HKD'}, {'amount': '12.30', 'currency': 'HKD'}),
    ('date', {}, '2024-02-29', '2024-02-29'), ('datetime', {}, '2026-09-30T09:00:00+08:00', '2026-09-30T09:00:00+08:00'),
    ('boolean', {}, False, False), ('text', {'max_length': 5}, 'hello', 'hello'),
])
def test_normalized_values(typ, cfg, value, expected):
    assert f.normalize(spec(typ, **cfg), value) == expected


@pytest.mark.parametrize('typ,cfg,value', [
    ('number', {}, True), ('number', {}, '1e5'), ('number', {}, 'NaN'),
    ('number', {}, '9'*100+'.1'), ('number', {}, '1.5'), ('date', {}, '2025-02-29'),
    ('datetime', {}, '2026-09-30T09:00:00'), ('datetime', {}, '2026-09-30T09:00:00+14:01'),
    ('money', {'currency': 'CNY'}, {'amount': '1', 'currency': 'USD'}),
    ('text', {'max_length': 2}, 'abc'), ('number', {'min': '1'}, '0'), ('date', {'max': '2026-01-01'}, '2026-01-02'),
    ('url', {}, 'javascript:alert(1)'), ('text', {}, 'two\nlines'),
])
def test_refused_values(typ, cfg, value):
    with pytest.raises(f.CellError):
        f.normalize(spec(typ, **cfg), value)


def test_exact_arithmetic_and_stable_expression():
    field = spec('number', decimals=2)
    compiled = compile_formula('{Fixture field} + 0.20', [field])
    assert compiled.expression == '{fld_000000000001} + 0.20'
    assert evaluate(compiled.tree, {field['id']: Decimal('0.10')}) == Decimal('0.30')
    assert evaluate(compile_formula('IF(FALSE, 1 / 0, 5)', []).tree, {}) == 5


@pytest.mark.parametrize('expr', [
    '__import__("os")', 'OPEN("file")', '{unknown}', '1.__class__', '[1]',
    '"a" + 1', '1 < 2 < 3', '('*200+'1'+')'*200, '-'*200+'1',
    'CONCAT('+','.join('"x"' for _ in range(100))+')',
])
def test_restricted_formula(expr):
    with pytest.raises(FormulaError):
        compile_formula(expr, [])
