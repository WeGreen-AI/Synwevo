"""Bounded, declarative plans. No executable expressions or implicit type inference."""
from __future__ import annotations

from typing import Annotated, Literal
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

MAX_FILE = 8 * 1024 * 1024
MAX_EXPANDED = 32 * 1024 * 1024
MAX_ROWS = 50_000
MAX_COLUMNS = 64
MAX_CELLS = 500_000
MAX_TEXT = 4096
MAX_TABLE_BYTES = 24 * 1024 * 1024
# Bound both registry reads per import and retained source checks on later access.
MAX_EXTRACT_TASKS = 100
MAX_EXTRACT_DETAILS = 100
Name = Annotated[str, StringConstraints(min_length=1, max_length=120)]
Text = Annotated[str, StringConstraints(max_length=MAX_TEXT)]
Scalar = Text | None  # Numbers travel as decimal text, never IEEE-754 JSON numbers.
SourceName = Annotated[str, StringConstraints(pattern=r'^[a-z][a-z0-9_]{0,31}$')]


class In(BaseModel):
    model_config = ConfigDict(extra='forbid')


class ImportOptions(In):
    encoding: Literal['utf-8-sig', 'utf-16', 'gb18030'] = 'utf-8-sig'
    delimiter: Literal[',', ';', '\t', '|'] = ','
    sheet: str | None = Field(None, max_length=120)
    # Inclusive worksheet coordinates; the first selected row contains column names.
    start_row: int = Field(1, ge=1, le=MAX_ROWS + 1)
    end_row: int | None = Field(None, ge=1, le=MAX_ROWS + 1)
    start_column: int = Field(1, ge=1, le=16384)
    end_column: int | None = Field(None, ge=1, le=16384)
    null_tokens: list[Text] = Field(default_factory=lambda: ['\\N'], max_length=10)
    formulas: Literal['reject', 'cached'] = 'reject'

    @model_validator(mode='after')
    def ordered(self):
        if self.end_row is not None and self.end_row < self.start_row:
            raise ValueError('end_row precedes start_row')
        if self.end_column is not None and self.end_column < self.start_column:
            raise ValueError('end_column precedes start_column')
        if self.end_column and self.end_column - self.start_column + 1 > MAX_COLUMNS:
            raise ValueError('too many columns')
        return self


class Step(In):
    id: SourceName


class Trim(Step):
    type: Literal['trim']
    columns: list[Name] = Field(min_length=1, max_length=MAX_COLUMNS)


class Cast(Step):
    type: Literal['cast']
    column: Name
    to: Literal['text', 'integer', 'decimal', 'date', 'datetime', 'boolean']
    scale: int = Field(2, ge=0, le=18)
    rounding: Literal['reject', 'half_even'] = 'reject'
    # Local timestamps require an explicit fixed offset, never the server's local timezone.
    offset: str | None = Field(None, pattern=r'^[+-](?:0\d|1[0-4]):[0-5]\d$')


class Split(Step):
    type: Literal['split']
    column: Name
    into: list[Name] = Field(min_length=2, max_length=10)
    separator: str = Field(min_length=1, max_length=20)


class Merge(Step):
    type: Literal['merge']
    columns: list[Name] = Field(min_length=1, max_length=MAX_COLUMNS)
    target: Name
    separator: str = Field('', max_length=20)
    nulls: Literal['propagate', 'empty'] = 'propagate'


class Predicate(In):
    column: Name
    op: Literal['eq', 'ne', 'gt', 'ge', 'lt', 'le', 'contains', 'is_null', 'is_missing', 'is_empty']
    value: Scalar = None
    numeric: bool = False


class Filter(Step):
    type: Literal['filter']
    condition: Predicate


class Sort(Step):
    type: Literal['sort']
    columns: list[Name] = Field(min_length=1, max_length=MAX_COLUMNS)
    descending: bool = False
    numeric: bool = False


class Dedupe(Step):
    type: Literal['dedupe']
    columns: list[Name] = Field(min_length=1, max_length=MAX_COLUMNS)
    keep: Literal['first', 'last'] = 'first'


class Append(Step):
    type: Literal['append']
    source: SourceName
    alignment: Literal['strict', 'union'] = 'strict'


class Join(Step):
    type: Literal['join']
    source: SourceName
    left: list[Name] = Field(min_length=1, max_length=8)
    right: list[Name] = Field(min_length=1, max_length=8)
    how: Literal['left', 'inner'] = 'left'
    matches: Literal['one', 'first', 'all'] = 'one'
    prefix: str = Field('right_', min_length=1, max_length=20)
    nulls_equal: bool = False

    @model_validator(mode='after')
    def same_keys(self):
        if len(self.left) != len(self.right):
            raise ValueError('join key counts differ')
        return self


class Measure(In):
    op: Literal['count', 'sum', 'min', 'max']
    column: Name | None = None
    target: Name

    @model_validator(mode='after')
    def column_required(self):
        if self.op != 'count' and not self.column:
            raise ValueError('measure needs a column')
        return self


class Aggregate(Step):
    type: Literal['aggregate']
    group_by: list[Name] = Field(default_factory=list, max_length=16)
    measures: list[Measure] = Field(min_length=1, max_length=16)


class Validate(Step):
    type: Literal['validate']
    condition: Predicate
    message: str = Field('rule_failed', max_length=500)
    on_error: Literal['keep', 'drop'] = 'keep'


OperationStep = Annotated[Trim | Cast | Split | Merge | Filter | Sort | Dedupe | Append | Join | Aggregate | Validate,
                          Field(discriminator='type')]


class Definition(In):
    source: SourceName = 'main'
    parameters: dict[SourceName, Scalar] = Field(default_factory=dict, max_length=20)
    steps: list[OperationStep] = Field(default_factory=list, max_length=30)

    @model_validator(mode='after')
    def unique_steps(self):
        if len({s.id for s in self.steps}) != len(self.steps):
            raise ValueError('step IDs must be unique')
        for s in self.steps:
            if isinstance(s, (Filter, Validate)) and isinstance(s.condition.value, str) and s.condition.value.startswith('$'):
                if s.condition.value[1:] not in self.parameters:
                    raise ValueError('undeclared parameter')
        return self


class Page(In):
    offset: int = Field(0, ge=0, le=1_000_000)
    limit: int = Field(50, ge=1, le=200)


class DatasetRef(In):
    dataset_id: str = Field(pattern=r'^dset_[a-f0-9]+$')


class DatasetPage(DatasetRef, Page):
    pass


class FileRef(In):
    file_id: str = Field(pattern=r'^dpf_[a-f0-9]+$')


class ImportFile(FileRef):
    name: Name
    options: ImportOptions = Field(default_factory=ImportOptions)


class Records(In):
    name: Name
    columns: list[Name] = Field(min_length=1, max_length=MAX_COLUMNS)
    records: list[dict[Name, Scalar]] = Field(max_length=5000)


class FormRecords(In):
    name: Name
    submission_ids: list[Annotated[str, StringConstraints(pattern=r'^fsub_[a-f0-9]+$')]] = Field(min_length=1, max_length=100)


ExtractFieldID = Annotated[str, StringConstraints(pattern=r'^[a-z][a-z0-9_]{0,39}$')]
Digest = Annotated[str, StringConstraints(pattern=r'^[a-f0-9]{64}$')]


class ExtractRef(In):
    task_id: str = Field(pattern=r'^extask_[a-f0-9]{12}$')
    revision: int = Field(ge=1, le=201, strict=True)


class ExtractRecords(In):
    name: Name
    tasks: list[ExtractRef] = Field(min_length=1, max_length=MAX_EXTRACT_TASKS)
    field_ids: list[ExtractFieldID] = Field(min_length=1, max_length=MAX_COLUMNS)
    detail_policy: Literal['zip_strict'] = 'zip_strict'

    @model_validator(mode='after')
    def unique_refs(self):
        if len({t.task_id for t in self.tasks}) != len(self.tasks):
            raise ValueError('duplicate task ID')
        if len(set(self.field_ids)) != len(self.field_ids):
            raise ValueError('duplicate field ID')
        return self


class ExtractSnapshot(ExtractRef):
    template_id: str = Field(pattern=r'^extpl_[a-f0-9]{12}$')
    template_version: int = Field(ge=1, le=100)
    definition_digest: Digest
    file_id: str = Field(pattern=r'^exfile_[a-f0-9]{12}$')
    content_digest: Digest
    result_fingerprint: Digest


class ExtractSource(In):
    kind: Literal['extract'] = 'extract'
    workspace_id: str
    operation: Literal['extract.task.result'] = 'extract.task.result'
    operation_version: Literal[1] = 1
    field_ids: list[ExtractFieldID] = Field(min_length=1, max_length=MAX_COLUMNS)
    detail_policy: Literal['zip_strict'] = 'zip_strict'
    tasks: list[ExtractSnapshot] = Field(min_length=1, max_length=MAX_EXTRACT_TASKS)


class ExtractDataset(In):
    """Version 1 import receipt; rows are read through dataset.get, never a fake preview."""
    id: str
    workspace_id: str
    name: Name
    origin: Literal['records']
    source: ExtractSource
    columns: list[ExtractFieldID]
    row_count: int
    column_count: int
    content_hash: Digest


class RecipeRef(In):
    recipe_id: str = Field(pattern=r'^dpr_[a-f0-9]+$')


class CreateRecipe(In):
    name: Name
    description: str = Field('', max_length=2000)
    definition: Definition = Field(default_factory=Definition)


class SaveRecipe(RecipeRef, CreateRecipe):
    draft_rev: int = Field(ge=1)


class Publish(RecipeRef):
    draft_rev: int = Field(ge=1)
    note: str = Field('', max_length=1000)


class Version(RecipeRef):
    version: int = Field(ge=1)


class Archive(RecipeRef):
    archived: bool


class Inputs(In):
    inputs: dict[SourceName, Annotated[str, StringConstraints(pattern=r'^dset_[a-f0-9]+$')]] = Field(min_length=1, max_length=8)
    parameters: dict[SourceName, Scalar] = Field(default_factory=dict, max_length=20)


class Preview(Inputs):
    definition: Definition
    offset: int = Field(0, ge=0, le=MAX_ROWS)
    limit: int = Field(50, ge=1, le=200)


class StartRun(Version, Inputs):
    output_name: str = Field('', max_length=120)


class RunRef(In):
    run_id: str = Field(pattern=r'^dpu_[a-f0-9]+$')


class Issues(RunRef, Page):
    pass
