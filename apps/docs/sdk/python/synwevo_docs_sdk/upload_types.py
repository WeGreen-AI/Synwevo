"""Public resumable-upload response types; no runtime validation or server imports."""
from typing import Literal, TypedDict

UploadStatus = Literal["open", "complete", "consumed", "expired", "aborted"]
UploadPurpose = Literal["source", "template"]


class Upload(TypedDict):
    id: str
    filename: str
    size: int
    sha256: str | None
    purpose: UploadPurpose | None
    received: int
    status: UploadStatus
    created_at: float
    updated_at: float
    expires_at: float
    complete: bool
    upload_url: str
