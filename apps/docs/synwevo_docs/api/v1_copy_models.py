"""Explicit wire contracts for approved project copies; no stored authority descriptors."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic.json_schema import SkipJsonSchema

from synwevo_core.errors import ServiceError

CopyStatus = Literal["pending", "approved", "rejected", "withdrawn", "expired", "running", "done", "failed", "stale"]


class CopyScope(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    sources: bool = False
    corrections: bool = False


class CopyRequestCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    target_workspace_id: str = Field(min_length=1)
    # Omission selects defaults; explicitly passing null has never been accepted. Hide only
    # the internal omission sentinel from JSON Schema, while retaining the strict wire type.
    name: str | SkipJsonSchema[None] = Field(default=None, min_length=1, max_length=100,
        json_schema_extra=lambda schema: schema.pop("default", None))
    purpose: str | SkipJsonSchema[None] = Field(default=None, max_length=500,
        json_schema_extra=lambda schema: schema.pop("default", None))
    scope: CopyScope = Field(default_factory=CopyScope)

    @model_validator(mode="before")
    @classmethod
    def preserve_error_contract(cls, body):
        if not isinstance(body, dict):
            return body
        if {"decide", "reason"} & set(body):
            raise ServiceError(400, "decision_in_browser", "审批只能由目标组织的审批人在网页中完成（需要重新验证身份）",
                               {"field": "decide" if "decide" in body else "reason"})
        unknown = sorted(set(body) - set(cls.model_fields))
        if unknown:
            raise ServiceError(400, "invalid_field", f"复制申请包含不支持的字段：{unknown[0]}", {"field": unknown[0]})
        for field, maximum in (("target_workspace_id", None), ("name", 100), ("purpose", 500)):
            if field not in body and field != "target_workspace_id":
                continue
            value = body.get(field)
            if not isinstance(value, str) or (field != "purpose" and not value.strip()) or (maximum and len(value) > maximum):
                raise ServiceError(400, "invalid_field", f"{field} 必须是有效字符串" + (f"（最多 {maximum} 个字符）" if maximum else ""),
                                   {"field": field})
        scope = body.get("scope", {})
        if not isinstance(scope, dict) or set(scope) - {"sources", "corrections"} or any(type(v) is not bool for v in scope.values()):
            raise ServiceError(400, "invalid_field", "scope 只能包含布尔值 sources、corrections", {"field": "scope"})
        return body


class CopyProjectManifest(BaseModel):
    id: str
    name: str
    step: str | None
    rev: int | None
    record_rule: dict
    output: dict


class CopyTemplateManifest(BaseModel):
    id: str
    name: str
    kind: str | None
    format: str | None
    version_no: int
    pinned: bool
    working_sha256: str | None
    slots: int
    scan: str | None


class CopySourceSummary(BaseModel):
    filename: str
    size: int
    format: str | None


class CopySourceIncluded(CopySourceSummary):
    id: str
    family: str | None
    sha256: str | None
    version: int
    scan: str | None
    has_ocr: bool
    has_corrections: bool


class CopySourcesManifest(BaseModel):
    included: bool
    count: int
    total_bytes: int
    items: list[CopySourceIncluded | CopySourceSummary]


class CopyMappingsManifest(BaseModel):
    rev: int
    count: int
    enabled: int


class CopyCorrectionsManifest(BaseModel):
    included: bool


class CopyManifest(BaseModel):
    project: CopyProjectManifest
    template: CopyTemplateManifest | None
    mappings: CopyMappingsManifest
    sources: CopySourcesManifest
    corrections: CopyCorrectionsManifest
    excluded: list[str]


class CopyManifestResponse(BaseModel):
    manifest: CopyManifest
    digest: str


class CopyPerson(BaseModel):
    user_id: str
    name: str


class CopyResult(BaseModel):
    project_id: str
    template_id: str | None
    sources: int
    mappings: int
    warnings: list[str]
    manifest_digest: str


class CopyRequestView(BaseModel):
    id: str
    source_project_id: str
    target_organization_id: str
    target_workspace_id: str
    name: str
    purpose: str | None
    scope: CopyScope
    manifest: CopyManifest
    digest: str
    status: CopyStatus
    decided_at: float | None
    decision_reason: str | None
    self_approved: bool
    approval_expires_at: float | None
    executed_at: float | None
    result: CopyResult | None
    error: str | None
    version: int
    created_at: float
    updated_at: float
    expires_at: float
    requester: CopyPerson | None
    decider: CopyPerson | None


class CopyRequestList(BaseModel):
    items: list[CopyRequestView]


class CopyTarget(BaseModel):
    id: str
    name: str
    role: Literal["editor", "admin", "owner"]
    organization_id: str
    organization_name: str | None


class CopyTargetsResponse(BaseModel):
    items: list[CopyTarget]
    source_is_personal: bool
