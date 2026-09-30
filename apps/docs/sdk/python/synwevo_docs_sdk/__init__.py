"""Public interface of the standalone Synwevo Docs client."""
from .auth import ApiKey, ClientCredentials, credentials_from_env, read_secret
from .client import Client, __version__
from .errors import (
    APIError, AuthenticationError, BadRequestError, ConflictError, GoneError,
    IntegrityError, NotFoundError, PayloadTooLargeError, PermissionDeniedError,
    RangeNotSatisfiableError, RateLimitError, ServerError, SynwevoError,
    TransportError, ValidationError, WaitTimeoutError, WebhookVerificationError,
)
from .webhooks import verify_legacy_signature, verify_webhook
from .workspace import WorkspaceClient

__all__ = [
    "Client", "WorkspaceClient", "ApiKey", "ClientCredentials", "credentials_from_env", "read_secret",
    "__version__", "verify_webhook", "verify_legacy_signature", "SynwevoError", "APIError",
    "AuthenticationError", "BadRequestError", "ConflictError", "GoneError", "IntegrityError",
    "NotFoundError", "PayloadTooLargeError", "PermissionDeniedError", "RangeNotSatisfiableError",
    "RateLimitError", "ServerError", "TransportError", "ValidationError", "WaitTimeoutError",
    "WebhookVerificationError",
]
