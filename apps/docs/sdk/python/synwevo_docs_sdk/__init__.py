"""Public interface of the standalone Synwevo Docs client."""
from .auth import ApiKey, ClientCredentials, UserToken, credentials_from_env, read_secret
from .client import Client, __version__
from .errors import (
    APIError, AuthenticationError, BadRequestError, ConflictError, GoneError,
    IntegrityError, NotFoundError, PayloadTooLargeError, PermissionDeniedError,
    RangeNotSatisfiableError, RateLimitError, ServerError, SynwevoError,
    TransportError, ValidationError, WaitTimeoutError, WebhookVerificationError,
)
from .webhooks import verify_legacy_signature, verify_webhook
from .workspace import WorkspaceClient
from .suite import SuiteClient
from .upload_types import Upload, UploadPurpose, UploadStatus
from .job_types import (
    Artifact, ArtifactsPage, CancelledJob, ItemArtifact, ItemStatus, Job, JobCancel,
    JobCounts, JobEvent, JobEventsPage, JobEventType, JobItem, JobItemsPage, JobListItem,
    JobOptions, JobPhase, JobRun, JobRunsPage, JobsPage, JobStatus, JobTemplate, JSONValue,
    RawJobCounts,
)

__all__ = [
    "Client", "WorkspaceClient", "SuiteClient", "ApiKey", "ClientCredentials", "UserToken",
    "credentials_from_env", "read_secret",
    "__version__", "verify_webhook", "verify_legacy_signature", "SynwevoError", "APIError",
    "AuthenticationError", "BadRequestError", "ConflictError", "GoneError", "IntegrityError",
    "NotFoundError", "PayloadTooLargeError", "PermissionDeniedError", "RangeNotSatisfiableError",
    "RateLimitError", "ServerError", "TransportError", "ValidationError", "WaitTimeoutError",
    "WebhookVerificationError",
    "Artifact", "ArtifactsPage", "CancelledJob", "ItemArtifact", "ItemStatus", "Job", "JobCancel",
    "JobCounts", "JobEvent", "JobEventsPage", "JobEventType", "JobItem", "JobItemsPage", "JobListItem",
    "JobOptions", "JobPhase", "JobRun", "JobRunsPage", "JobsPage", "JobStatus", "JobTemplate", "JSONValue",
    "RawJobCounts", "Upload", "UploadPurpose", "UploadStatus",
]
