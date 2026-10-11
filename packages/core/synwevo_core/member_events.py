"""Workspace membership and invitation events, as outbox rows (API-5 event catalogue).

An integration that keeps a directory in step wants to hear that someone joined, changed role,
left, or was invited — without polling the directory API. Memberships and invitations are written
from several places (the members page, invitation acceptance, the administrator's claim and undo,
an account's erasure, a restore from the security journal), so a trigger writes the event **in the
same transaction as the row change**: no committed change without its event, no event for a change
that rolled back. The rows use the job event log's shape with ``job_id = ''`` (they belong to no
job) and go to the workspace's tenant, which is whose subscriptions and event feed may see them
(synwevo_docs.webhooks reads ``job_events`` by tenant; the feed is ``GET /api/v1/events``).

The data is identifiers and the facts of the change only — never an e-mail address, a name or an
invitation token: a receiver that wants more reads the directory with its own credential, so a
revoked credential learns nothing beyond the ids it was already sent.

Types:

* ``member.added`` — a membership row appeared (acceptance of an invitation, a direct addition, a
  new workspace's owner, an administrator's claim);
* ``member.changed`` — the role or the export right changed (``previous_role`` /
  ``previous_can_export`` say from what);
* ``member.removed`` — the membership row is gone (removal, an owner handover's cleanup, an account
  erasure, a restore that undid an addition);
* ``invitation.created`` / ``invitation.accepted`` / ``invitation.revoked`` — an invitation's
  life; acceptance also produces ``member.added`` for the membership it creates. Expiry is not a
  row change and emits nothing (``expires_at`` is in ``invitation.created``).

Store alone executes ``SCHEMA_SQL``. Rollback: older code ignores the event types it does not know
(its dispatcher still delivers them to subscriptions filtering on ``*``, with ``job: null``).
"""
from __future__ import annotations

MEMBER_EVENT_TYPES = ("member.added", "member.changed", "member.removed",
                      "invitation.created", "invitation.accepted", "invitation.revoked")

_NOW = "(julianday('now') - 2440587.5) * 86400.0"
_ID = "'ev_' || lower(hex(randomblob(10)))"


def _event(trigger: str, when: str, type_: str, data: str) -> str:
    return f"""
CREATE TRIGGER IF NOT EXISTS {trigger}
{when}
BEGIN
  INSERT INTO job_events (id, job_id, tenant, type, version, occurred_at, data)
  VALUES ({_ID}, '', {{tenant}}, '{type_}', 1, {_NOW}, {data});
END;
"""


SCHEMA_SQL = "".join([
    _event("memberships_added_event", "AFTER INSERT ON memberships", "member.added",
           "json_object('workspace_id', NEW.workspace_id, 'user_id', NEW.user_id, 'role', NEW.role, "
           "'can_export', json(CASE WHEN NEW.can_export THEN 'true' ELSE 'false' END))").format(tenant="NEW.workspace_id"),
    _event("memberships_changed_event",
           "AFTER UPDATE OF role, can_export ON memberships\nWHEN NEW.role IS NOT OLD.role OR NEW.can_export IS NOT OLD.can_export",
           "member.changed",
           "json_object('workspace_id', NEW.workspace_id, 'user_id', NEW.user_id, 'role', NEW.role, "
           "'can_export', json(CASE WHEN NEW.can_export THEN 'true' ELSE 'false' END), 'previous_role', OLD.role, "
           "'previous_can_export', json(CASE WHEN OLD.can_export THEN 'true' ELSE 'false' END))").format(tenant="NEW.workspace_id"),
    _event("memberships_removed_event", "AFTER DELETE ON memberships", "member.removed",
           "json_object('workspace_id', OLD.workspace_id, 'user_id', OLD.user_id, 'role', OLD.role)").format(tenant="OLD.workspace_id"),
    _event("invitations_created_event", "AFTER INSERT ON invitations", "invitation.created",
           "json_object('workspace_id', NEW.workspace_id, 'invitation_id', NEW.id, 'role', NEW.role, "
           "'can_export', json(CASE WHEN NEW.can_export THEN 'true' ELSE 'false' END), 'expires_at', NEW.expires_at)").format(tenant="NEW.workspace_id"),
    _event("invitations_accepted_event",
           "AFTER UPDATE OF accepted_at ON invitations\nWHEN NEW.accepted_at IS NOT NULL AND OLD.accepted_at IS NULL",
           "invitation.accepted",
           "json_object('workspace_id', NEW.workspace_id, 'invitation_id', NEW.id, 'user_id', NEW.accepted_by, 'role', NEW.role)").format(tenant="NEW.workspace_id"),
    _event("invitations_revoked_event",
           "AFTER UPDATE OF revoked_at ON invitations\nWHEN NEW.revoked_at IS NOT NULL AND OLD.revoked_at IS NULL AND NEW.accepted_at IS NULL",
           "invitation.revoked",
           "json_object('workspace_id', NEW.workspace_id, 'invitation_id', NEW.id)").format(tenant="NEW.workspace_id"),
])
