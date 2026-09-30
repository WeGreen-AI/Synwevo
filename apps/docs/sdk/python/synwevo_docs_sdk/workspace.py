"""Owner-approved workspace directory client; independent of the Docs API and its audience."""
from __future__ import annotations

import os
from typing import Iterator, Literal, TypedDict

from .auth import ApiKey, ClientCredentials, read_secret
from .client import Client, _q, __version__
from .errors import ValidationError

WORKSPACE_BASE_URL = "https://accounts.synwevo.com"
WORKSPACE_RESOURCE = "urn:synwevo:platform:workspace"
WORKSPACE_API = "/api/workspace/v1"
ManagedRole = Literal["viewer", "editor"]
MemberRole = Literal["viewer", "editor", "admin", "owner"]
InvitationState = Literal["pending", "accepted", "declined", "revoked", "expired"]


class DirectoryWorkspace(TypedDict):
    id: str
    name: str | None
    kind: str | None


class DirectoryWhoami(TypedDict):
    workspace: DirectoryWorkspace
    client_id: str
    name: str | None
    scopes: list[str]


class DirectoryMember(TypedDict):
    user_id: str
    email: str
    display_name: str | None
    role: MemberRole
    account_status: str
    joined_at: float


class DirectoryPage(TypedDict):
    total: int
    offset: int
    limit: int
    next_offset: int | None


class DirectoryMemberPage(DirectoryPage):
    items: list[DirectoryMember]
    owner_count: int


class DirectoryInvitation(TypedDict):
    id: str
    email: str
    role: MemberRole
    state: InvitationState
    created_at: float
    expires_at: float
    invited_via: str | None


class DirectoryInvitationPage(DirectoryPage):
    items: list[DirectoryInvitation]


class DirectoryInvitationCreated(DirectoryInvitation):
    can_export: bool
    mail: Literal["queued"]


class DirectoryMemberChanged(TypedDict):
    user_id: str
    role: ManagedRole
    can_export: bool
    previous_role: ManagedRole
    journaled: bool | None


class DirectoryMemberRemoved(TypedDict):
    user_id: str
    removed: bool
    journaled: bool


class DirectoryInvitationRevoked(TypedDict):
    id: str
    state: Literal["revoked"]
    journaled: bool


def _managed_role(role, field: str) -> ManagedRole:
    if role not in ("viewer", "editor"):
        raise ValidationError(f"{field} must be viewer or editor", field)
    return role


class WorkspaceClient:
    """Directory methods only. Explicit credentials or ``from_env`` use the account origin.

    The environment factory reads only SYNWEVO_WORKSPACE_* variables, defaults to
    workspace.read, and always requests the workspace resource. Add the approved
    workspace.members.write scope explicitly to enable writes.
    """

    def __init__(self, base_url: str = WORKSPACE_BASE_URL, credentials: ApiKey | ClientCredentials | None = None, **options):
        if credentials is None:
            raise ValidationError("WorkspaceClient requires explicit directory credentials or from_env()", "credentials")
        options.setdefault("user_agent", f"synwevo-workspace-sdk-python/{__version__}")
        self._client = Client(base_url, credentials, **options)

    @classmethod
    def from_env(cls, environ: dict | None = None, **options) -> "WorkspaceClient":
        env = os.environ if environ is None else environ
        base = env.get("SYNWEVO_WORKSPACE_BASE_URL") or WORKSPACE_BASE_URL
        client_id = env.get("SYNWEVO_WORKSPACE_CLIENT_ID")
        if not client_id:
            raise ValidationError("Set SYNWEVO_WORKSPACE_CLIENT_ID for an approved directory service account", "SYNWEVO_WORKSPACE_CLIENT_ID")
        secret = read_secret(env.get("SYNWEVO_WORKSPACE_CLIENT_SECRET"), env.get("SYNWEVO_WORKSPACE_CLIENT_SECRET_FILE"), what="workspace client_secret")
        credentials = ClientCredentials(env.get("SYNWEVO_WORKSPACE_TOKEN_URL") or base.rstrip("/") + "/oidc/token", client_id, secret,
                                       scope=env.get("SYNWEVO_WORKSPACE_SCOPE") or "workspace.read", resource=WORKSPACE_RESOURCE)
        return cls(base, credentials, **options)

    @property
    def base_url(self) -> str:
        return self._client.base_url

    def close(self) -> None:
        self._client.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def whoami(self) -> DirectoryWhoami:
        return self._client._get(WORKSPACE_API + "/whoami")

    def list_members(self, *, role: MemberRole | None = None, q: str | None = None, limit: int = 50, offset: int = 0) -> DirectoryMemberPage:
        return self._client._get(WORKSPACE_API + "/members", {"role": role, "q": q, "limit": limit, "offset": offset})

    def iter_members(self, *, role: MemberRole | None = None, q: str | None = None, page_size: int = 50) -> Iterator[DirectoryMember]:
        return self._client._iterate(WORKSPACE_API + "/members", {"role": role, "q": q}, page_size)

    def list_invitations(self, *, state: InvitationState | None = None, limit: int = 50, offset: int = 0) -> DirectoryInvitationPage:
        return self._client._get(WORKSPACE_API + "/invitations", {"state": state, "limit": limit, "offset": offset})

    def iter_invitations(self, *, state: InvitationState | None = None, page_size: int = 50) -> Iterator[DirectoryInvitation]:
        return self._client._iterate(WORKSPACE_API + "/invitations", {"state": state}, page_size)

    def create_invitation(self, email: str, role: ManagedRole, *, can_export: bool | None = None,
                          expires_in_days: int = 7, idempotency_key: str | None = None) -> DirectoryInvitationCreated:
        body = {"email": email, "role": _managed_role(role, "role"), "expires_in_days": expires_in_days}
        if can_export is not None:
            body["can_export"] = can_export
        return self._client._json("POST", WORKSPACE_API + "/invitations", json=body, idempotency_key=self._client._key(idempotency_key))

    def resend_invitation(self, invitation_id: str, *, idempotency_key: str | None = None) -> DirectoryInvitationCreated:
        return self._client._json("POST", f"{WORKSPACE_API}/invitations/{_q(invitation_id)}/resend", json={},
                                  idempotency_key=self._client._key(idempotency_key))

    def revoke_invitation(self, invitation_id: str) -> DirectoryInvitationRevoked:
        return self._client._json("POST", f"{WORKSPACE_API}/invitations/{_q(invitation_id)}/revoke", json={}, retry_safe=False)

    def change_member(self, user_id: str, role: ManagedRole, *, expected_role: ManagedRole) -> DirectoryMemberChanged:
        body = {"role": _managed_role(role, "role"), "expected_role": _managed_role(expected_role, "expected_role")}
        return self._client._json("PATCH", f"{WORKSPACE_API}/members/{_q(user_id)}", json=body, retry_safe=False)

    def remove_member(self, user_id: str, *, expected_role: ManagedRole, reason: str) -> DirectoryMemberRemoved:
        body = {"expected_role": _managed_role(expected_role, "expected_role"), "reason": reason}
        return self._client._json("POST", f"{WORKSPACE_API}/members/{_q(user_id)}/remove", json=body, retry_safe=False)
