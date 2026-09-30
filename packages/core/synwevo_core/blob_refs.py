"""Where stored bytes are referenced — data only, no behaviour.

User bytes live content-addressed in ``<data>/blobs/<h[:2]>/<h>`` (``Store.put_blob``) and are
shared across applications: one digest may be a Docs source, a Synwevo Message attachment, an
organization publication and an input pinned in a job snapshot at once. Anything that ever
decides "these bytes are no longer referenced" must know every place a reference can hide. This
module is that list, kept as data so the read-only inventory (``apps/docs/scripts/blob_audit.py``)
and the tests that guard it (``tests/integration/test_blob_references.py``: every candidate column
of a fresh database classified, every blob writer registered, every read gate covered) use one
source instead of drifting apart. Design: ``docs/platform/blob-reclamation.md``.

Nothing here imports an application.
"""
from __future__ import annotations

#: Columns that hold a blob digest directly.
DIRECT: tuple[tuple[str, str], ...] = (
    ("source_files", "blob"),
    ("source_files", "parsed_blob"),
    ("source_files", "working_blob"),
    ("templates", "original_blob"),
    ("template_versions", "working_blob"),
    ("artifacts", "blob"),
    ("blob_grants", "blob"),                      # previews, layout PDFs, desensitized outputs: the grant is the reference
    ("message_attachments", "blob"),              # Synwevo Message
    ("form_files", "blob"),                       # Synwevo Forms attachments (staged, attached or discarded)
    ("uploads", "blob"),                          # a completed upload not used yet: its only reference
    ("organization_templates", "working_blob"),   # outlives the source template by design
    ("organization_template_releases", "working_blob"),
)

#: References inside JSON documents, invisible to a per-column scan. The inventory walks each
#: column's whole document (any 64-hex string counts); the paths say where they are today.
JSON_PATHS: tuple[tuple[str, str, str], ...] = (
    ("jobs", "snapshot", "$.template.working_blob"),
    ("jobs", "snapshot", "$.template.fill_options.image.original_blob"),
    ("jobs", "snapshot", "$.sources[*].blob"),
    ("jobs", "snapshot", "$.sources[*].parsed_blob"),
    ("jobs", "snapshot", "$.sources[*].working_blob"),
    ("templates", "info", "$.image.original_blob"),
    ("organization_templates", "info", "$.image.original_blob"),
    ("organization_template_releases", "info", "$.image.original_blob"),
)

#: Files on disk, outside the database, that name blobs: (root under the data directory, glob,
#: what they are). A captured regression case keeps its template's fill options.
FILE_ROOTS: tuple[tuple[str, str, str], ...] = (
    ("regression", "*/*/case.json", "回归用例的 case.json（模板图片原图）"),
)

#: Columns that look like a digest but are not a blob reference, with the reason.
LOOKALIKES: tuple[tuple[str, str, str], ...] = (
    ("idempotency", "request_hash", "请求指纹，不是 blob"),
    ("organization_idempotency", "request_hash", "请求指纹，不是 blob"),
    ("organization_idempotency", "key_hash", "幂等键的哈希，不是 blob"),
    ("api_keys", "secret_hash", "密钥哈希，不是 blob"),
    ("oauth_clients", "secret_hash", "客户端密钥哈希，不是 blob"),
    ("sessions", "id", "Cookie 值的 SHA-256，不是 blob"),
    ("oauth_tokens", "access_hash", "令牌哈希，不是 blob"),
    ("oauth_tokens", "refresh_hash", "令牌哈希，不是 blob"),
    ("oauth_codes", "code_hash", "授权码哈希，不是 blob"),
    ("recovery_codes", "code_hash", "恢复码哈希，不是 blob"),
    ("login_codes", "code_hash", "登录验证码哈希，不是 blob"),
    ("email_tokens", "token_hash", "邮件令牌哈希，不是 blob"),
    ("account_recoveries", "token_hash", "找回令牌哈希，不是 blob"),
    ("bootstrap_tokens", "token_hash", "初始化令牌哈希，不是 blob"),
    ("invitations", "token_hash", "邀请令牌哈希，不是 blob"),
    ("op_runs", "input_hash", "操作输入的指纹（幂等比对），不是 blob"),
    ("forms", "public_token_hash", "公开填写链接令牌的哈希，不是 blob"),
    ("form_versions", "definition_hash", "表单版本定义的指纹，不是 blob"),
    ("form_requests", "token_hash", "填写链接令牌的哈希，不是 blob"),
    ("form_submissions", "answers_hash", "提交内容的指纹，不是 blob"),
    ("agent_tool_calls", "input_hash", "Agent 工具调用参数的指纹（确认绑定），不是 blob"),
    ("external_auth_requests", "state_hash", "外部登录 state 的哈希，不是 blob"),
    ("external_link_offers", "token_hash", "外部账户关联令牌哈希，不是 blob"),
    ("push_subscriptions", "endpoint_hash", "推送端点的哈希，不是 blob"),
    ("ui_users", "hash", "口令哈希，不是 blob"),
    ("passkeys", "credential_id", "WebAuthn 凭据 id，不是 blob"),
    ("uploads", "sha256", "客户端声明的摘要；完成后才等于 uploads.blob"),
    ("member_imports", "sha256", "导入原文件在 member_imports/，不在 blob 存储里"),
    ("project_copies", "digest", "复制申请的清单指纹；执行时重读活行，不钉住字节"),
)

#: Columns that hold the bytes themselves (SQLite BLOBs), not a digest: guessing "a column named
#: *blob* holds an id" would hand these to ``blob_path()``.
INLINE_BYTES: tuple[tuple[str, str], ...] = (
    ("users", "avatar_blob"),
    ("organization_avatars", "image"),
    ("conversation_avatars", "image"),
    ("collab_docs", "state"),
)


def referencing_tables() -> set[str]:
    """Every table whose rows (a column or a JSON document) can name a blob."""
    return {t for t, _ in DIRECT} | {t for t, _, _ in JSON_PATHS}
