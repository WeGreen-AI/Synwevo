"""Installed SDK, real HTTP, synthetic directory operations. Launched by workspace_run.py."""
import json
import os

from synwevo_docs_sdk import ConflictError, WorkspaceClient


def main():
    with WorkspaceClient.from_env() as client:
        who = client.whoami()
        assert who["workspace"]["id"] == os.environ["ACCEPTANCE_DIRECTORY_WORKSPACE"]
        assert set(who["scopes"]) == {"workspace.read", "workspace.members.write"}
        member_id = os.environ["ACCEPTANCE_DIRECTORY_MEMBER"]
        assert member_id in {m["user_id"] for m in client.iter_members(page_size=1)}
        first = client.create_invitation("python-invite@example.invalid", "viewer", idempotency_key="python:invite")
        assert first["mail"] == "queued" and first["state"] == "pending" and first["invited_via"] == who["client_id"]
        replay = client.create_invitation("python-invite@example.invalid", "viewer", idempotency_key="python:invite")
        assert replay["id"] == first["id"] and replay.replayed
        resent = client.resend_invitation(first["id"], idempotency_key="python:resend")
        assert resent["id"] != first["id"]
        assert client.resend_invitation(first["id"], idempotency_key="python:resend")["id"] == resent["id"]
        assert client.revoke_invitation(resent["id"])["state"] == "revoked"
        assert {first["id"], resent["id"]} <= {i["id"] for i in client.iter_invitations(state="revoked", page_size=1)}
        changed = client.change_member(member_id, "editor", expected_role="viewer")
        assert changed["role"] == "editor" and changed["previous_role"] == "viewer"
        try:
            client.change_member(member_id, "viewer", expected_role="viewer")
            raise AssertionError("stale expected_role was accepted")
        except ConflictError as exc:
            assert exc.code == "role_changed"
        assert client.remove_member(member_id, expected_role="editor", reason="Synthetic SDK acceptance cleanup")["removed"]
        assert member_id not in {m["user_id"] for m in client.iter_members(page_size=1)}
        print(json.dumps({"language": "python", "status": "passed", "client_id": who["client_id"], "invitation_ids": [first["id"], resent["id"]],
                          "member_id": member_id, "stale_role": "role_changed", "removed": True}))


if __name__ == "__main__":
    main()
