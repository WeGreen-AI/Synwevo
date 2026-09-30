"""Bounded, version-one operation inputs. Values are keyed by stable field identifiers."""
from typing import Any, Literal
from pydantic import BaseModel, ConfigDict, Field as F

TableId = str
ROLE = Literal['viewer', 'editor', 'admin']
TYPE = Literal['text', 'long_text', 'number', 'money', 'date', 'datetime', 'boolean',
               'single_select', 'multi_select', 'email', 'url', 'phone', 'link', 'formula',
               'created_time', 'updated_time', 'created_by', 'updated_by']

class Input(BaseModel):
    model_config = ConfigDict(extra='forbid')

class Ref(Input):
    table_id: str = F(pattern=r'^tbl_[a-f0-9]{12}$')

class RecordLocation(Input):
    record_id: str = F(pattern=r'^rec_[a-f0-9]{12}$')

class ListTables(Input):
    status: Literal['active', 'archived'] = 'active'
    q: str = F(default='', max_length=100)
    offset: int = F(default=0, ge=0, le=100000)
    limit: int = F(default=50, ge=1, le=200)

class CreateTable(Input):
    name: str = F(min_length=1, max_length=100)
    description: str = F(default='', max_length=2000)

class TableEdit(Ref):
    schema_rev: int = F(ge=1)
    name: str | None = F(default=None, min_length=1, max_length=100)
    description: str | None = F(default=None, max_length=2000)
    archived: bool | None = None
    read_role: ROLE | None = None
    write_role: Literal['editor', 'admin'] | None = None

class FieldSpec(Input):
    name: str = F(min_length=1, max_length=100)
    description: str = F(default='', max_length=1000)
    type: TYPE = 'text'
    config: dict = F(default_factory=dict)
    required: bool = False
    is_unique: bool = False
    default: Any = None
    read_role: ROLE = 'viewer'
    write_role: Literal['editor', 'admin'] = 'editor'

class FieldSave(Ref):
    schema_rev: int = F(ge=1)
    field_id: str | None = F(default=None, pattern=r'^fld_[a-f0-9]{12}$')
    field: FieldSpec
    preview: bool = False
    preview_token: str | None = F(default=None, max_length=64)

class FieldState(Ref):
    schema_rev: int = F(ge=1)
    field_id: str = F(pattern=r'^fld_[a-f0-9]{12}$')
    deleted: bool
    preview: bool = False
    preview_token: str | None = F(default=None, max_length=64)

class Filter(Input):
    field_id: str = F(pattern=r'^fld_[a-f0-9]{12}$')
    op: Literal['eq', 'ne', 'contains', 'gt', 'gte', 'lt', 'lte', 'empty', 'not_empty'] = 'eq'
    value: Any = None

class Sort(Input):
    field_id: str = F(pattern=r'^fld_[a-f0-9]{12}$')
    direction: Literal['asc', 'desc'] = 'asc'

class Query(Ref):
    q: str = F(default='', max_length=200)
    filters: list[Filter] = F(default_factory=list, max_length=20)
    sort: list[Sort] = F(default_factory=list, max_length=5)
    fields: list[str] | None = F(default=None, max_length=100)
    deleted: bool = False
    offset: int = F(default=0, ge=0, le=10000)
    limit: int = F(default=50, ge=1, le=200)
    view_id: str | None = F(default=None, pattern=r'^view_[a-f0-9]{12}$')

class RecordRef(Ref):
    record_id: str = F(pattern=r'^rec_[a-f0-9]{12}$')

class RecordCreate(Ref):
    cells: dict[str, Any] = F(default_factory=dict, max_length=100)
    read_role: ROLE = 'viewer'
    write_role: Literal['editor', 'admin'] = 'editor'

class RecordUpdate(RecordRef):
    rev: int = F(ge=1)
    cells: dict[str, Any] = F(default_factory=dict, max_length=100)
    read_role: ROLE | None = None
    write_role: Literal['editor', 'admin'] | None = None

class RecordState(RecordRef):
    rev: int = F(ge=1)
    deleted: bool

class BatchItem(Input):
    action: Literal['create', 'update', 'archive', 'restore']
    record_id: str | None = F(default=None, pattern=r'^rec_[a-f0-9]{12}$')
    rev: int | None = F(default=None, ge=1)
    cells: dict[str, Any] = F(default_factory=dict, max_length=100)

class Batch(Ref):
    items: list[BatchItem] = F(min_length=1, max_length=100)
    atomic: bool = True

class ViewSave(Ref):
    view_id: str | None = F(default=None, pattern=r'^view_[a-f0-9]{12}$')
    rev: int | None = F(default=None, ge=1)
    name: str = F(min_length=1, max_length=100)
    query: Query

class ViewDelete(Ref):
    view_id: str = F(pattern=r'^view_[a-f0-9]{12}$')
    rev: int = F(ge=1)

class History(Ref):
    record_id: str | None = F(default=None, pattern=r'^rec_[a-f0-9]{12}$')
    before: int | None = F(default=None, ge=1)
    limit: int = F(default=50, ge=1, le=100)

class Aggregate(Query):
    field_id: str | None = F(default=None, pattern=r'^fld_[a-f0-9]{12}$')
    function: Literal['count', 'sum', 'min', 'max', 'average'] = 'count'

class Export(Query):
    format: Literal['csv', 'xlsx'] = 'csv'

class ImportRef(Ref):
    import_id: str = F(pattern=r'^imp_[a-f0-9]{12}$')

class ImportApply(ImportRef):
    mapping: dict[str, str] = F(min_length=1, max_length=100)
    atomic: bool = True
    preview: bool = True

class RestoreChange(RecordRef):
    rev: int = F(ge=1)
    change_id: str = F(pattern=r'^chg_[a-f0-9]{12}$')
