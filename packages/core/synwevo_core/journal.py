"""The security journal: withdrawals of access, recorded outside the database.

Restoring a backup puts the database back the way it was when the backup was taken. For most
data that is the point. For **withdrawn access** it is the opposite of the point: a key revoked
because it leaked, an account suspended, a member removed, a client disabled — each of those
comes back to life, silently, the moment an older database is restored.

So every withdrawal is also appended here, to a file that lives next to the database but is not
part of it. A restore keeps the live journal (it is never overwritten by the backup's copy) and
re-applies, before the service starts (`apply()`), every withdrawal the backup does not hold.

Every withdrawal is written ahead (`_intent`): inside the transaction that makes the change, before
its COMMIT, with a fresh `attempt_id` and a receipt in `meta` that commits with the change, or not
at all. A restore replays a line whatever its time when the restored database lacks its receipt —
a backup taken between the line and its COMMIT included — and skips it when the database holds the
receipt (it then holds the withdrawal and whatever came after it). An attempt whose transaction did
not commit is voided once that is proven (`_void`, `intent_voided`). One older than the backup and
without its receipt there is either the backup's own race (copied between the line and its COMMIT)
or an attempt that never committed and was never voided (the process stopped first); the backup
itself tells them apart when it holds anything committed after the line (`_never_committed`), and
only the race is replayed. A restore that replayed no journal starts a new history (EPOCH_KEY):
attempts from before it are not replayed into that history's backups. Lines written before a kind
moved onto `_intent` carry no `attempt_id` and keep their old rule: replayed when newer than the
backup (TIME_FILTERED), or whatever its time and by their own ids (ALWAYS_REPLAYED).

Entries hold identifiers and, for credential replacements, the same digests the database already
holds — never a secret, a token or document content. The file is created 0600.

Re-applying is idempotent: each entry is expressed as "make this state true", so applying an
entry twice, or applying one the restored database already reflects, changes nothing.
"""
from __future__ import annotations

import json
import math
import os
import secrets
import sqlite3
import time
from pathlib import Path

from .store import Store, new_id
from .suite_schema import webhooks as suite_webhooks

FILENAME = "security-journal.jsonl"

# Organization withdrawals are write-ahead intents. The same SQLite transaction
# as the withdrawal stores a receipt in the existing meta table. A backup can be
# taken between fsync and COMMIT, so read() must include these even before its
# time cutoff; apply() skips only receipts actually present in that backup.
# This also preserves explicit recovery/regrant completed before the backup.
# An attempt whose transaction did not commit in this process is voided after
# its rollback (_intent/_void: proven by a later committed transaction that
# found no receipt); only an unproven one — the process stopped between the line
# and its COMMIT, or before the void — is replayed by a restore whose backup lacks
# its receipt (fail-closed), once: that restore writes the receipt.
# No historical journal line or tenant association is rewritten.
ORGANIZATION_KINDS = {
    "organization_frozen", "organization_deleted", "organization_member_removed",
    "organization_role_changed", "organization_policy_restricted",
}
_ORG_RECEIPT = "organization_withdrawal:"

# Undoing the assignment of the historical data (`admin.workspace.unclaim`) is one entry, written
# ahead of the transaction it describes, like the organization withdrawals and for the same two
# reasons: a process that dies between COMMIT and a journal line written afterwards would let an
# older backup give the former members their access back, and a backup taken between a line and
# its COMMIT has neither. Instead of a receipt it carries its own time and is applied "as of"
# then: only memberships, grants, enablements, invitations and tokens that existed at `at` are
# withdrawn, and the status goes back to `pending_claim` only if nobody was assigned the data
# since. So it is re-applied on every restore (read() ignores the cutoff for it) and can never
# undo a later assignment — a restore from a backup taken after a new claim keeps that claim.
UNCLAIM_KIND = "workspace_unclaimed"
#: Entries re-applied whatever the backup's time: their own rules decide what is still to do.
# Older restore code must reject this new kind instead of silently losing a demotion.
# Code rollback retains the mirror; recovery requires a reader supporting platform grants.
PLATFORM_GRANT_KIND = "platform_grant_revoked"
_PLATFORM_RECEIPT = "platform_withdrawal:"
# Paid model dispatch stopped by the risk controls (billing/risk.py). Written ahead of the SQL
# change, so a backup taken between the line and its COMMIT lacks both: replayed whatever the
# backup's time and applied by id, which keeps an administrator's later lift that the backup
# already holds, and re-suspends (re-opens) only what the restored database never saw.
RISK_KINDS = {"model_dispatch_suspended", "model_breaker_opened"}
# A wallet frozen by a chargeback (billing/ledger.py record_chargeback): the same write-ahead rule
# and for the same reasons. Replayed whatever the backup's time and applied by the freeze's id, so
# a freeze the restored database already holds stays as it is there — active, or lifted by an
# administrator before the backup — and only a freeze the restored database never saw is
# re-created, which stops the payer's wallet from paying again until an administrator lifts it.
PAYER_FREEZE_KIND = "payer_frozen"
# The personal Agent's and model governance's withdrawals are written ahead of their SQL change for
# the same reason, and replayed whatever the backup's time for the same race. Each is applied so
# that it never undoes what the restored database already holds after it:
# * a suspension by its id (a lifted one keeps its row, so it stays lifted);
# * a consent withdrawal as of its time (a consent given again afterwards stands);
# * a provider switch-off and a credential withdrawal by the operation id of the published version
#   that committed with them — present means the backup has the change and anything after it — and,
#   once replayed, by a receipt in `meta` (credential revisions restart after a restore, so "that
#   revision and older" alone could hit a key published later under a reused number);
# * an AI-policy narrowing by a receipt committed with it (record_ai_policy_narrowed) or written by
#   its replay (the policy is one mutable row; a later re-widening must stand).
AGENT_MODEL_KINDS = {"agent_access_suspended", "agent_provider_consent_withdrawn", "ai_policy_narrowed",
                     "model_credential_withdrawn", "model_provider_disabled"}
_AI_POLICY_RECEIPT = "ai_policy_narrowed:"
_CREDENTIAL_RECEIPT = "model_credential_withdrawn:"
# An application's access policy narrowed to explicit grants (Apps.set_access_policy): the shape of
# ai_policy_narrowed and for the same reasons. The policy is one mutable column that a later
# loosening (not journalled) sets back, so the entry is applied by receipt, not by time: written in
# the transaction that tightens, with a receipt in `meta` that commits with it; replayed whatever
# the backup's time, into a database that lacks the receipt — a backup taken between the entry and
# its COMMIT holds neither the change nor the receipt and is tightened, one that holds the receipt
# holds the tightening and any loosening after it, which stands.
APP_POLICY_KIND = "app_policy_tightened"
_APP_POLICY_RECEIPT = "app_policy_tightened:"
# A workspace deleted by the platform (workspace_lifecycle.py, A44): the organization deletion's
# shape for one workspace. Written ahead of the SQL with a receipt in `meta` that commits with it
# (record_workspace); replayed whatever the backup's time into a database that lacks the receipt:
# the workspace becomes deleted again with the entry's own clock — never restarted, never replacing
# a newer deletion — and the deletion's withdrawals are made again. A recovery is not journalled,
# so a backup holding the receipt keeps a later recovery, and one taken before the deletion (or
# between the entry and its COMMIT) deletes it again. A freeze replayed onto a row that is deleted
# by then (_replay_freeze) is kept for its recovery: `previous_status='frozen'`. Like the
# organization withdrawals, an attempt that fails after its line was appended (a directory fsync,
# a full disk at the SQL or the COMMIT) leaves an intent without a receipt; its rollback voids it
# (`intent_voided`, VOID_KIND) once a later committed transaction has found no receipt, so no
# restore applies it. Only when the process stops before that proof does the intent stay
# unvoided, and a restore whose backup lacks its receipt applies it (then writes the receipt) —
# fail-closed; the console's 503 says the attempt could still count. Older restore code refuses
# the unknown kind.
WORKSPACE_DELETED_KIND = "workspace_deleted"
_WORKSPACE_RECEIPT = "workspace_withdrawal:"
# A workspace frozen by the platform (Identity.set_workspace_frozen): written ahead, inside the
# freeze's transaction and before its UPDATE, with a receipt in `meta` keyed by the line's own
# `attempt_id` (record_workspace_frozen). A line with an `attempt_id` is replayed whatever the
# backup's time into a database without its receipt — one taken between the line and its COMMIT
# included — and skipped by one that holds it (it then holds the freeze and any unfreeze after it).
# A line without `attempt_id` (written before this release) keeps the old rule: replayed only when
# newer than the backup. Older restore code accepts the new lines and filters them by time.
WORKSPACE_FROZEN_KIND = "workspace_frozen"
# The proof that an attempt's transaction never committed: {attempt_id, voided_kind}. Written only
# after a later, committed write transaction found the attempt's receipt absent (_void), first as
# `meta['journal_void:'+attempt_id]` (synchronous=FULL), then as this line. apply() skips a line
# whose `attempt_id` either names, when the void names the line's own kind. Older restore code
# refuses the unknown kind (as it refuses `workspace_deleted`): a restore needs this release or a
# newer one.
VOID_KIND = "intent_voided"
_RECEIPT = "journal_receipt:"
_VOID = "journal_void:"
# Every withdrawal leaves its receipt in `meta` for good (and a failed attempt its void row): one
# short row each, never pruned. A restore of any backup still kept may need them — the receipt is
# what tells its replay that a later backup already holds the withdrawal — and nothing yet records
# which backups are kept, so a pruning rule could only guess. Every restore reads and checks every
# written-ahead line of the journal (read()), which grows the same way.
# Where this database's journal history starts: `meta[EPOCH_KEY]`, the time of a restore that
# replayed no journal (apps/docs/scripts/snapshot.py fence with --no-journal), written into the
# restored copy. That restore decided, by choice, that nothing from before it is replayed; the live
# journal stays beside the restored database all the same (only appended to). Every later backup
# of the new history carries the row, so apply() skips an attempted line older than it that lacks
# its receipt there (an abandoned history's withdrawal, or one the --no-journal restore chose not
# to replay) — as the time filter skipped every old line older than such a backup. Lines without
# `attempt_id` keep their old rule.
EPOCH_KEY = "journal_epoch"
#: Lines without `attempt_id` (written before their kind moved onto _intent) of these kinds are
#: re-applied whatever the backup's time: their own rules decide what is still to do. Every other
#: kind's old lines are TIME_FILTERED. A line with an `attempt_id` is read whatever its time.
ALWAYS_REPLAYED = (ORGANIZATION_KINDS | {UNCLAIM_KIND, PLATFORM_GRANT_KIND} | RISK_KINDS | AGENT_MODEL_KINDS
                   | {PAYER_FREEZE_KIND, APP_POLICY_KIND, WORKSPACE_DELETED_KIND, VOID_KIND})

KINDS = {
    "api_platform_approval_revoked",  # {approval_id}, including unconsumed approvals
    "api_key_revoked",          # {key_id} — and every key of its lineage
    "client_disabled",          # {client_id}
    "client_secret_rotated",    # {client_id, secret_hash}
    "tokens_revoked",           # {client_id? , user_id?, family_id?}
    "user_suspended",           # {user_id}
    "user_deleted",             # {user_id}
    "password_changed",         # {user_id, hash_id}  (hash stays in the database; see apply())
    "sessions_revoked",         # {user_id}
    "member_removed",           # {workspace_id, user_id}
    "role_changed",             # {workspace_id, user_id, role}  — only recorded for downgrades
    "entitlement_revoked",      # {workspace_id, app_id, user_id}
    WORKSPACE_FROZEN_KIND,      # {workspace_id, attempt_id?} — with attempt_id: written ahead, by receipt
    VOID_KIND,                  # {attempt_id, voided_kind} — that attempt never committed
    WORKSPACE_DELETED_KIND,     # {event_id, workspace_id, deleted_at, recover_until, previous_status}
    "webhook_disabled",         # {subscription_id}
    "passkey_removed",          # {credential_id}
    "external_identity_unlinked",  # {identity_id} — a sign-in through an external provider detached
    "workspace_app_disabled",   # {workspace_id, app_id}
    # B3: an organization-scoped application (Synwevo Message) switched off for one organization.
    # Its own kind, not `workspace_app_disabled` (whose replay deletes a `workspace_apps` row and
    # which older restore code would apply as a silent no-op): older restore code refuses this one.
    "organization_app_disabled",  # {organization_id, app_id}
    "app_disabled",             # {app_id} — the application switched off platform-wide
    APP_POLICY_KIND,            # {event_id, app_id} — the application's access policy narrowed to explicit grants
    "invitation_revoked",       # {invitation_id}
    "api_key_superseded",       # {key_id} — a rotated-out key; the successor is not recreated
    PLATFORM_GRANT_KIND,       # {event_id, user_id, grant_id, role, granted_at, granted_by}
    UNCLAIM_KIND,               # {workspace_id, label_restored} — the assignment of legacy data undone
    "consent_withdrawn",        # {user_id, client_id, workspace_id?} — the consent and that client's tokens
    "model_dispatch_suspended", # {suspension_id, environment, scope_kind, scope_id} — paid model calls of a payer/route
    "model_breaker_opened",     # {event_id, environment} — paid model calls stopped platform-wide
    # The personal Agent (agent_safety.py): a person's withdrawn consent to send content to a
    # model provider, and an administrator's suspension of a person's Agent access.
    "agent_provider_consent_withdrawn",  # {user_id, provider}
    "agent_access_suspended",   # {suspension_id, user_id}
    # Model governance: a workspace's AI processing policy made narrower, the platform model
    # credential withdrawn (revoked or replaced) and the platform model provider switched off.
    "ai_policy_narrowed",       # {event_id, workspace_id, processing, providers}
    "model_credential_withdrawn",  # {event_id, environment, credential_revision} — that revision and older
    "model_provider_disabled",  # {event_id, environment}
    # A personal or organization wallet frozen by a payment dispute after its credit was spent.
    PAYER_FREEZE_KIND,          # {freeze_id, environment, account_id, payer_kind, payer_id, chargeback_id}
} | ORGANIZATION_KINDS

#: The kinds written through _intent — every withdrawal. Each line carries a fresh `attempt_id`
#: (128 random bits, never one this database already holds a receipt or a void for — unlike
#: `account_event_id`, which a retry repeats) and a receipt that commits with its transaction. By
#: how their writers journalled before (and how their old lines are still read):
#: * receipted already, by their own ids (organizations, workspace deletion, platform grants, AI and
#:   application policies, the account operations' `account_event_id`): `_receipt` keeps those keys;
#: * time-filtered, the line before COMMIT (a backup taken between them lost the withdrawal) or
#:   after it (a process that stopped in between, or a swallowed append failure, lost it) — now
#:   receipted with `journal_receipt:<attempt_id>`;
#: * written ahead and matched by id or "as of `at`" (risk, payer freezes, the personal Agent,
#:   model governance, `workspace_unclaimed`) — the receipt adds nothing to their replay, the void
#:   stops a failed attempt from being re-created on every restore.
#: Reading accepts an `attempt_id` on any kind (`_validate`), so a later release's lines stay readable.
ATTEMPTED = KINDS - {VOID_KIND}
#: Old lines (no `attempt_id`) of these kinds are replayed only when newer than the backup (read()).
TIME_FILTERED = KINDS - ALWAYS_REPLAYED
#: The kinds receipted by their own event ids (_receipt), whose replays check the receipt themselves.
_OWN_RECEIPT_KINDS = ORGANIZATION_KINDS | {WORKSPACE_DELETED_KIND, PLATFORM_GRANT_KIND, "ai_policy_narrowed",
                                           APP_POLICY_KIND}

#: A workspace's AI processing policy, narrowest first (suite agent tools.workspace_policy).
AI_PROCESSING = ("none", "platform", "any")
#: The providers a workspace may name (suite agent tools.MODEL_PROVIDERS).
AI_PROVIDERS = ("anthropic", "deepseek")

_REQUIRED = {
    "api_platform_approval_revoked": ("approval_id",),
    "api_key_revoked": ("key_id",), "api_key_superseded": ("key_id",),
    "client_disabled": ("client_id",), "client_secret_rotated": ("client_id", "secret_hash"),
    "user_suspended": ("user_id",), "user_deleted": ("user_id",),
    "password_changed": ("user_id",), "sessions_revoked": ("user_id",),
    "member_removed": ("workspace_id", "user_id"),
    "role_changed": ("workspace_id", "user_id", "role"),
    "entitlement_revoked": ("workspace_id", "app_id", "user_id"),
    "workspace_frozen": ("workspace_id",), "webhook_disabled": ("subscription_id",),
    WORKSPACE_DELETED_KIND: ("event_id", "workspace_id"),
    "passkey_removed": ("credential_id",), "external_identity_unlinked": ("identity_id",),
    "workspace_app_disabled": ("workspace_id", "app_id"),
    "organization_app_disabled": ("organization_id", "app_id"),
    "app_disabled": ("app_id",), APP_POLICY_KIND: ("event_id", "app_id"),
    "invitation_revoked": ("invitation_id",), "tokens_revoked": (),
    PLATFORM_GRANT_KIND: ("event_id", "user_id", "grant_id", "role", "granted_by"),
    UNCLAIM_KIND: ("workspace_id",),
    "consent_withdrawn": ("user_id", "client_id"),
    "model_dispatch_suspended": ("suspension_id", "environment", "scope_kind", "scope_id"),
    "model_breaker_opened": ("event_id", "environment"),
    "agent_provider_consent_withdrawn": ("user_id", "provider"),
    "agent_access_suspended": ("suspension_id", "user_id"),
    "ai_policy_narrowed": ("event_id", "workspace_id", "processing"),
    "model_credential_withdrawn": ("event_id", "environment"),
    "model_provider_disabled": ("event_id", "environment"),
    PAYER_FREEZE_KIND: ("freeze_id", "environment", "account_id", "payer_kind", "payer_id", "chargeback_id"),
    VOID_KIND: ("attempt_id", "voided_kind"),
    **{kind: ("event_id", "organization_id") for kind in ORGANIZATION_KINDS},
    "organization_member_removed": ("event_id", "organization_id", "user_id"),
    "organization_role_changed": ("event_id", "organization_id", "user_id", "role"),
}


def _validate(entry, line: int) -> None:
    # Do not echo raw input: a journal can contain password-verifier material.
    def bad():
        raise ValueError(f"invalid security journal at line {line}; restore refused; recover the intact journal before retrying")
    if not isinstance(entry, dict):
        bad()
    kind, at = entry.get("kind"), entry.get("at")
    if not isinstance(kind, str) or kind not in KINDS or type(at) not in (int, float) or not math.isfinite(at) or at < 0:
        bad()
    if "account_event_id" in entry and (kind not in {"user_suspended", "sessions_revoked"}
            or not isinstance(entry["account_event_id"], str) or not entry["account_event_id"].strip()):
        bad()
    # On any kind (see ATTEMPTED): a later release's attempted lines stay readable here.
    if "attempt_id" in entry and (not isinstance(entry["attempt_id"], str) or not entry["attempt_id"].strip()):
        bad()
    required = _REQUIRED[kind]
    if kind == "tokens_revoked":
        required = tuple(k for k in ("client_id", "user_id", "family_id") if k in entry)
        if not required:
            bad()
    if any(not isinstance(entry.get(k), str) or not entry[k].strip() for k in required):
        bad()
    if kind == PLATFORM_GRANT_KIND:
        from .platform_access import ROLES
        granted_at = entry.get("granted_at")
        if (entry["role"] not in ROLES or type(granted_at) not in (int, float)
                or not math.isfinite(granted_at) or not 0 <= granted_at <= at):
            bad()
    if kind == "role_changed" and entry["role"] not in {"owner", "admin", "editor", "viewer"}:
        bad()
    if kind == VOID_KIND and entry["voided_kind"] not in KINDS - {VOID_KIND}:
        bad()
    if kind == UNCLAIM_KIND and type(entry.get("label_restored", False)) is not bool:
        bad()
    if kind in RISK_KINDS and (entry["environment"] not in ("test", "live") or (
            kind == "model_dispatch_suspended" and entry["scope_kind"] not in ("user", "organization", "route"))):
        bad()
    if kind == "ai_policy_narrowed":
        providers = entry.get("providers")
        if (entry["processing"] not in AI_PROCESSING or not isinstance(providers, list)
                or any(not isinstance(p, str) or not p.strip() for p in providers)):
            bad()
    if kind in ("model_credential_withdrawn", "model_provider_disabled") and entry["environment"] not in ("test", "live"):
        bad()
    if kind == PAYER_FREEZE_KIND and (entry["environment"] not in ("test", "live")
                                      or entry["payer_kind"] not in ("user", "organization")):
        bad()
    if kind == "model_credential_withdrawn" and (type(entry.get("credential_revision")) is not int
                                                 or entry["credential_revision"] < 1):
        bad()
    if kind in ("organization_deleted", WORKSPACE_DELETED_KIND):
        if any(type(entry.get(k)) not in (int, float) or not math.isfinite(entry[k]) or entry[k] < 0
               for k in ("deleted_at", "recover_until")):
            bad()
        if (entry["recover_until"] != entry["deleted_at"] + 7 * 24 * 3600
                or entry.get("previous_status") not in {"active", "frozen"}):
            bad()
    if kind in ORGANIZATION_KINDS:
        scope = entry.get("workspace_ids")
        if (not isinstance(scope, list) or any(not isinstance(w, str) or not w.strip() for w in scope)
                or len(set(scope)) != len(scope)):
            bad()
        if kind == "organization_role_changed" and entry["role"] not in {"admin", "member"}:
            bad()


def validated_lines(p: Path) -> list[tuple[dict, str]]:
    """Read complete, validated entries; even a torn final withdrawal is fatal.

    Guessing which withdrawal was lost is unsafe. The caller must fail before installing a
    restored database; the original journal stays untouched for operator-led recovery.
    """
    if not p.exists():
        return []
    out = []
    for number, line in enumerate(p.read_text("utf-8").splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except ValueError:
            raise ValueError(f"invalid security journal at line {number}; restore refused; recover the intact journal before retrying") from None
        _validate(entry, number)
        out.append((entry, line))
    return out


def path(root: str | Path) -> Path:
    return Path(root) / FILENAME


def record(store: Store, kind: str, **fields) -> None:
    """Append one entry. Raises on failure: a withdrawal that cannot be journalled must not be
    reported as durable, so callers decide deliberately whether to proceed.

    The append itself, not the way to journal a withdrawal: a writer calls `_intent` (or one of the
    record_* helpers built on it) inside the transaction of its change. A line written here without
    an `attempt_id` has no receipt and no void, and is read by its kind's old rule (read())."""
    if kind not in KINDS:
        raise ValueError(f"unknown journal entry kind: {kind}")
    entry = {"at": time.time(), "kind": kind, **{k: v for k, v in fields.items() if v is not None}}
    _validate(entry, 1)
    p = path(store.root)
    _append(p, (json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8"))
    # fsync(file) alone does not make a newly created directory entry durable.
    _fsync_dir(p.parent)


def _append(p: Path, payload: bytes) -> None:
    """Append one line and fsync the file (record's first durable step)."""
    fd = os.open(p, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    try:
        if os.write(fd, payload) != len(payload):
            raise OSError("incomplete security journal write")
        os.fsync(fd)
    finally:
        os.close(fd)


def _fsync_dir(directory: Path) -> None:
    """fsync the journal's directory (record's last step, after the line is in the file)."""
    fd = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _receipt(e: dict) -> str:
    """The `meta` key that commits with an attempted line's transaction (ATTEMPTED). The kinds
    receipted before `attempt_id` existed keep their keys — by their event id, which restores of
    older lines still look up; every other attempted kind uses the line's own `attempt_id`."""
    kind = e["kind"]
    if kind in ORGANIZATION_KINDS:
        return _ORG_RECEIPT + e["event_id"]
    if kind == WORKSPACE_DELETED_KIND:
        return _WORKSPACE_RECEIPT + e["event_id"]
    if kind == PLATFORM_GRANT_KIND:
        return _PLATFORM_RECEIPT + e["event_id"]
    if kind == "ai_policy_narrowed":
        return _AI_POLICY_RECEIPT + e["event_id"]
    if kind == APP_POLICY_KIND:
        return _APP_POLICY_RECEIPT + e["event_id"]
    if e.get("account_event_id"):
        return "account_withdrawal:" + e["account_event_id"]
    return _RECEIPT + e["attempt_id"]


def _new_attempt_id() -> str:
    """An attempt's id: 128 random bits. A void skips every line naming its id, so an id drawn twice
    would let an earlier failed attempt's void skip a later committed one; _intent also refuses an
    id the database already knows."""
    return "ja_" + secrets.token_hex(16)


def _intent(store: Store, kind: str, *, strict: bool = True, **fields) -> bool:
    """Write one withdrawal ahead of its SQL, inside the caller's write transaction: a fresh
    `attempt_id`, the line's receipt in `meta` (committing with the caller's change, or not at
    all), a void queued for the transaction's rollback (Store.after_rollback), then the line.

    The `attempt_id` is drawn again, in this transaction, while the database already holds a void
    or a generic receipt under it: a void must never name a later attempt. The receipt goes in
    before the append, so a swallowed append failure (`strict=False`, for a writer whose withdrawal
    stands without its line) still commits a correct receipt; `strict` raises it, which rolls the
    caller back unless it decides otherwise. A void queued for a line that was never written is
    harmless. Returns whether the line was written."""
    if kind not in ATTEMPTED:
        raise ValueError(f"not an attempted journal kind: {kind}")
    with store.conn() as c:
        if not c.in_transaction:
            raise RuntimeError(f"{kind} requires a write transaction")
        for _ in range(8):
            attempt = _new_attempt_id()
            if not c.execute("SELECT 1 FROM meta WHERE key IN (?,?)",
                             (_VOID + attempt, _RECEIPT + attempt)).fetchone():
                break
        else:
            raise RuntimeError("no unused security journal attempt id could be drawn")
        receipt = _receipt({"kind": kind, **fields, "attempt_id": attempt})
        c.execute("INSERT INTO meta (key,value) VALUES (?, '1')", (receipt,))
    store.after_rollback(lambda: _void(store, attempt, kind, receipt))
    try:
        record(store, kind, attempt_id=attempt, **fields)
    except (OSError, ValueError) as e:
        if strict:
            raise
        print(f"[synwevo] security journal write failed for {kind}: {type(e).__name__}: {e}", flush=True)
        return False
    return True


def _void(store: Store, attempt: str, kind: str, receipt: str) -> None:
    """After a rollback (outside the write lock, no transaction open): prove the attempt never
    committed, then say so. The proof is a later write transaction that commits — with
    synchronous=FULL, so its frames durably replace whatever the failed one left in the WAL —
    after finding the receipt absent; the writer lock held by the attempt's transaction means it
    had ended by then. Only then the `journal_void:` row (in that transaction) and the
    `intent_voided` line. Any failure on the way leaves no proof, and the attempt is replayed
    (fail-closed). A receipt that is present means the attempt did commit: nothing is written —
    Store.after_rollback also runs this after a COMMIT that raised yet committed, and this check is
    what tells the two apart.

    The connection is this thread's long-lived one: its `synchronous` level is raised to FULL for
    the proof only when it is lower, and put back to exactly what it was — never lowered below the
    level the connection had (a connection opened at FULL or EXTRA keeps it)."""
    c = store._c()
    level = c.execute("PRAGMA synchronous").fetchone()[0]
    if level < 2:
        c.execute("PRAGMA synchronous=FULL")
    try:
        with store.tx() as t:
            if t.execute("SELECT 1 FROM meta WHERE key=?", (receipt,)).fetchone():
                return
            t.execute("INSERT OR IGNORE INTO meta (key,value) VALUES (?,?)", (_VOID + attempt, kind))
    finally:
        if level < 2:
            c.execute(f"PRAGMA synchronous={int(level)}")
    record(store, VOID_KIND, attempt_id=attempt, voided_kind=kind)


def record_organization(store: Store, kind: str, *, organization_id: str, **fields) -> None:
    """Authorize/audit first, then call before SQL withdrawals in a write tx.

    An append/fsync failure aborts that transaction. A later SQL/commit failure in this process
    voids the intent after the rollback (_intent); one the process did not survive leaves a
    restrictive intent without a receipt, which restore must replay.
    The receipt commits atomically with the caller's withdrawal, never separately.
    Only IDs and lifecycle timestamps go into the journal, not names or reasons.
    """
    if kind not in ORGANIZATION_KINDS:
        raise ValueError("not an organization withdrawal")
    with store.conn() as c:
        if not c.in_transaction:
            raise RuntimeError("organization withdrawal requires a write transaction")
        workspace_ids = [r["id"] for r in c.execute(
            "SELECT id FROM workspaces WHERE organization_id=? ORDER BY id", (organization_id,))]
    _intent(store, kind, event_id=new_id("ow"), organization_id=organization_id,
            workspace_ids=workspace_ids, **fields)


def record_workspace(store: Store, kind: str, *, workspace_id: str, **fields) -> None:
    """A workspace deletion (WORKSPACE_DELETED_KIND), written ahead in the caller's write
    transaction after its authorization and audit, before its SQL: an append/fsync failure aborts
    the transaction. A failure after the line was written — the directory fsync, the SQL, the
    COMMIT — rolls the deletion back and, once a later committed transaction has found no receipt,
    voids the line (_intent/_void), so no restore applies it. Only an attempt the process did not
    survive (stopped between the line and its COMMIT, or before the void) stays an intent without
    a receipt, which a later restore — from any backup, even one taken after a successful retry
    and a recovery — applies with this attempt's clock (fail-closed). The receipt commits
    atomically with the deletion. Identifiers and lifecycle timestamps only — never a name or a
    reason."""
    if kind != WORKSPACE_DELETED_KIND:
        raise ValueError("not a workspace withdrawal")
    _intent(store, kind, event_id=new_id("wd"), workspace_id=workspace_id, **fields)


def record_workspace_frozen(store: Store, *, workspace_id: str) -> None:
    """A platform freeze (WORKSPACE_FROZEN_KIND), written ahead in the freeze's own transaction,
    before its UPDATE, with its receipt keyed by the line's `attempt_id` (_intent): a backup taken
    between the line and its COMMIT lacks the receipt and is frozen on restore; a failed attempt
    is voided after its rollback. Raises when the line cannot be written, which refuses the freeze."""
    _intent(store, WORKSPACE_FROZEN_KIND, workspace_id=workspace_id)


def record_account(store: Store, kind: str, *, user_id: str, event_id: str) -> None:
    """Write-ahead account withdrawal plus receipt, like organization withdrawals.

    Existing kinds stay readable by older releases. New restores replay an unreceipted
    intent even across the backup cutoff, and do not reapply a withdrawal already in a
    backup after an explicit recovery. The receipt is keyed by `event_id`, which a retry may
    reuse (a second-factor reset's sweep); the void of a failed attempt is keyed by that line's
    own `attempt_id`, so it never voids the retry. No credential or content enters this file.
    """
    if kind not in {"user_suspended", "sessions_revoked"}:
        raise ValueError("not an account withdrawal")
    _intent(store, kind, user_id=user_id, account_event_id=event_id)


def record_platform_revocation(store: Store, grant: dict, *, at: float) -> None:
    """Persist the exact grant withdrawal before COMMIT, after its authorization and audit.

    The receipt commits with the demotion. A failed fsync aborts the live change; a later
    commit failure in this process is voided after the rollback, and one the process did not
    survive leaves a restrictive intent which recovery must honor. No mailbox,
    administrator-supplied reason or credential is written to the journal.
    """
    _intent(store, PLATFORM_GRANT_KIND, at=at, event_id=new_id("pw"), user_id=grant["user_id"],
            grant_id=grant["id"], role=grant["role"], granted_at=grant["granted_at"],
            granted_by=grant["granted_by"])


def record_ai_policy_narrowed(store: Store, *, workspace_id: str, processing: str, providers: list[str]) -> None:
    """Journal a workspace AI-policy narrowing ahead of the change, with its receipt in the same
    transaction (the caller's): a backup holding the receipt holds the narrowing, and whatever
    the workspace decided after it, so its replay leaves that alone."""
    _intent(store, "ai_policy_narrowed", event_id=new_id("aip"), workspace_id=workspace_id,
            processing=processing, providers=providers)


def record_app_policy_tightened(store: Store, *, app_id: str) -> None:
    """Journal an application's tightening to explicit grants with its receipt, in the caller's
    write transaction (Apps.set_access_policy): see APP_POLICY_KIND."""
    _intent(store, APP_POLICY_KIND, event_id=new_id("apt"), app_id=app_id)


def read(root: str | Path, since: float = 0.0) -> list[dict]:
    """The lines a restore from a backup taken at `since` must check, in file order: every line
    written ahead (`attempt_id`, or an account operation's `account_event_id`) and every void,
    whatever its time — apply() decides by the receipts the restored database holds — and the
    old lines of ALWAYS_REPLAYED kinds; old lines of the TIME_FILTERED kinds only when newer."""
    return [entry for entry, _ in validated_lines(path(root))
            if entry["at"] > since or entry["kind"] in ALWAYS_REPLAYED or entry.get("account_event_id")
            or entry.get("attempt_id")]


def _apply_unclaim(c, e) -> int:
    """Make "the assignment undone at `at`" true, touching nothing granted after it.

    Everything is compared with the entry's own time: a membership, grant, enablement,
    invitation, token or code from after it belongs to a later assignment and stays. The
    status goes back to `pending_claim` only when no membership is left and the workspace row
    itself has not changed since (a later claim sets `updated_at`)."""
    from .apps import DOCS_APP_ID
    from .store import LEGACY_WORKSPACE_NAME
    wid, at = e["workspace_id"], e["at"]
    if not c.execute("SELECT 1 FROM workspaces WHERE id=? AND kind='legacy'", (wid,)).fetchone():
        return 0
    removed = [r["user_id"] for r in c.execute(
        "SELECT user_id FROM memberships WHERE workspace_id=? AND created_at<=?", (wid, at))]
    apps = {r["app_id"] for r in c.execute(
        "SELECT app_id FROM workspace_apps WHERE workspace_id=? AND enabled_at<=?", (wid, at))} | {DOCS_APP_ID}
    for uid in removed:
        for app_id in sorted(apps):
            # an explicit revoked row per former member, as the undo wrote it (under the `members`
            # policy "no row" means "allowed"); the withdrawal keeps its own time
            c.execute("INSERT INTO app_entitlements (workspace_id,app_id,user_id,granted_at,granted_by,revoked_at) "
                      "VALUES (?,?,?,?,NULL,?) ON CONFLICT(workspace_id,app_id,user_id) "
                      "DO UPDATE SET revoked_at=MAX(COALESCE(app_entitlements.revoked_at,0),excluded.revoked_at)",
                      (wid, app_id, uid, at, at))
    n = c.execute("DELETE FROM memberships WHERE workspace_id=? AND created_at<=?", (wid, at)).rowcount
    c.execute("UPDATE app_entitlements SET revoked_at=? WHERE workspace_id=? AND revoked_at IS NULL "
              "AND granted_at<=?", (at, wid, at))
    c.execute("DELETE FROM workspace_apps WHERE workspace_id=? AND enabled_at<=?", (wid, at))
    c.execute("UPDATE invitations SET revoked_at=? WHERE workspace_id=? AND accepted_at IS NULL "
              "AND revoked_at IS NULL AND created_at<=?", (at, wid, at))
    c.execute("UPDATE oauth_tokens SET revoked_at=?, revoked_reason='workspace_unclaimed' "
              "WHERE workspace_id=? AND revoked_at IS NULL AND issued_at<=?", (at, wid, at))
    c.execute("UPDATE oauth_codes SET used_at=COALESCE(used_at,?) WHERE workspace_id=? AND created_at<=?",
              (at, wid, at))
    name = ", name=?" if e.get("label_restored") else ""
    n += c.execute(f"UPDATE workspaces SET status='pending_claim'{name}, updated_at=? WHERE id=? AND kind='legacy' "
                   "AND status IN ('active','frozen') AND updated_at<=? "
                   "AND NOT EXISTS (SELECT 1 FROM memberships WHERE workspace_id=?)",
                   ((LEGACY_WORKSPACE_NAME,) if name else ()) + (at, wid, at, wid)).rowcount
    return n


def _org_scope(c, e):
    # Include the event's old IDs: the backup may predate explicit adoption.
    return sorted(set(e["workspace_ids"]) | {r["id"] for r in c.execute(
        "SELECT id FROM workspaces WHERE organization_id=?", (e["organization_id"],))})


def _org_revoke(c, workspace_ids, at, uid=None):
    """Match live org withdrawal scope; personal/unrelated credentials survive."""
    for wid in workspace_ids:
        user_sql, user_args = (" AND user_id=?", (uid,)) if uid else ("", ())
        for table in ("sessions", "oauth_tokens"):
            c.execute(f"UPDATE {table} SET revoked_at=?,revoked_reason='organization_access_withdrawn' "
                      "WHERE workspace_id=? AND revoked_at IS NULL" + user_sql, (at, wid, *user_args))
        c.execute("UPDATE oauth_codes SET used_at=? WHERE workspace_id=? AND used_at IS NULL" + user_sql,
                  (at, wid, *user_args))
        c.execute("UPDATE invitations SET revoked_at=? WHERE workspace_id=? AND accepted_at IS NULL "
                  "AND revoked_at IS NULL" + (" AND invited_by=?" if uid else ""), (at, wid, *user_args))
        if c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='api_platform_approvals'").fetchone():
            c.execute("UPDATE api_platform_approvals SET status='revoked',revoked_at=? WHERE workspace_id=? "
                      "AND status IN ('pending','approved')" + (" AND (requested_by=? OR approved_by=?)" if uid else ""),
                      (at, wid, *((uid, uid) if uid else ())))


def _apply_organization(c, e, orphan_orgs, orphan_workspaces):
    oid, at, kind = e["organization_id"], e["at"], e["kind"]
    if c.execute("SELECT 1 FROM meta WHERE key=?", (_ORG_RECEIPT + e["event_id"],)).fetchone():
        return 0
    scope = _org_scope(c, e)
    if kind in {"organization_frozen", "organization_deleted"}:
        if kind == "organization_frozen":
            c.execute("UPDATE organizations SET status='frozen',updated_at=?,version=version+1 "
                      "WHERE id=? AND status='active'", (at, oid))
        else:
            # Never restart the seven-day clock on restore or replace a newer deletion.
            c.execute("UPDATE organizations SET status='deleted',deleted_at=?,recover_until=?,previous_status=?, "
                      "updated_at=?,version=version+1 WHERE id=? AND (deleted_at IS NULL OR deleted_at<?)",
                      (e["deleted_at"], e["recover_until"], e["previous_status"], at, oid, e["deleted_at"]))
        _org_revoke(c, scope, at)
        for wid in scope:
            # An old backup can contain the historical workspace but no org row
            # or attachment. Freeze it instead of inventing that missing grant — a deleted one
            # comes back frozen from its recovery.
            _replay_freeze(c, wid, at, " AND (organization_id IS NULL OR organization_id<>? OR NOT EXISTS "
                           "(SELECT 1 FROM organizations WHERE id=?))", (oid, oid))
    elif kind == "organization_member_removed":
        uid = e["user_id"]
        _org_revoke(c, scope, at, uid)
        c.execute("DELETE FROM organization_memberships WHERE organization_id=? AND user_id=?", (oid, uid))
        for wid in scope:
            c.execute("DELETE FROM memberships WHERE workspace_id=? AND user_id=?", (wid, uid))
            c.execute("UPDATE app_entitlements SET revoked_at=COALESCE(revoked_at,?) "
                      "WHERE workspace_id=? AND user_id=?", (at, wid, uid))
        orphan_orgs.add(oid)
        orphan_workspaces.update(scope)
    elif kind == "organization_role_changed":
        uid, role = e["user_id"], e["role"]
        # A transfer's new owner is a grant and is deliberately not recreated.
        c.execute("UPDATE organization_memberships SET role=?,updated_at=? WHERE organization_id=? AND user_id=? "
                  "AND (role='owner' OR (role='admin' AND ?='member'))", (role, at, oid, uid, role))
        for wid in scope:
            c.execute("UPDATE invitations SET revoked_at=? WHERE workspace_id=? AND invited_by=? "
                      "AND accepted_at IS NULL AND revoked_at IS NULL", (at, wid, uid))
        orphan_orgs.add(oid)
    elif kind == "organization_policy_restricted":
        c.execute("UPDATE organizations SET invitation_policy='owners',updated_at=?,version=version+1 "
                  "WHERE id=? AND invitation_policy<>'owners'", (at, oid))
        for wid in scope:
            c.execute("UPDATE invitations SET revoked_at=? WHERE workspace_id=? AND accepted_at IS NULL "
                      "AND revoked_at IS NULL AND invited_by NOT IN (SELECT m.user_id FROM organization_memberships m "
                      "JOIN users u ON u.id=m.user_id WHERE m.organization_id=? AND m.role='owner' "
                      "AND u.status='active' AND u.deleted_at IS NULL)", (at, wid, oid))
    c.execute("INSERT INTO meta (key,value) VALUES (?, '1')", (_ORG_RECEIPT + e["event_id"],))
    return 1


def _replay_freeze(c, wid, t, where: str = "", args: tuple = ()) -> int:
    """A replayed freeze of one workspace (`workspace_frozen`, an organization withdrawal's
    substitute freeze, the ownerless freeze after demotions). An active row becomes frozen. A
    deleted row stays deleted and is marked to come back frozen (`previous_status='frozen'`):
    otherwise a recovery of the restored copy — the deletion replayed, or already in the backup —
    would end in `active` and silently undo the freeze. Entries are applied in file order and a
    freeze needs an active row, so a freeze entry that meets a deleted row postdates a recovery
    (not journalled) the restore lost. `where`/`args` narrow both statements alike."""
    n = c.execute("UPDATE workspaces SET status='frozen', updated_at=? WHERE id=? AND status='active'" + where,
                  (t, wid, *args)).rowcount
    return n + c.execute("UPDATE workspaces SET previous_status='frozen', updated_at=? WHERE id=? AND status='deleted' "
                         "AND COALESCE(previous_status,'active')<>'frozen'" + where, (t, wid, *args)).rowcount


def _apply_workspace_deleted(c, e) -> int:
    """Make "deleted as of the entry" true unless the restored database holds its receipt (it
    then holds the deletion and anything after it — a recovery included). Never on the
    historical workspace (its lifecycle is claim/unclaim), never restarting the seven-day clock or
    replacing a newer deletion (`deleted_at IS NULL OR deleted_at < ?`). A restored row that is
    frozen — or deleted from frozen — stays `previous_status='frozen'`: a recovery must not thaw
    what the restored database had frozen; a freeze entry replayed after this one marks it the
    same way (_replay_freeze). The withdrawals are made again (workspace_lifecycle.withdraw,
    the credentials bound to the revoked approvals included). A backup from before the workspace
    existed has no row: nothing to do."""
    receipt = _WORKSPACE_RECEIPT + e["event_id"]
    if c.execute("SELECT 1 FROM meta WHERE key=?", (receipt,)).fetchone():
        return 0
    wid, at, n = e["workspace_id"], e["at"], 0
    ws = c.execute("SELECT kind, status FROM workspaces WHERE id=?", (wid,)).fetchone()
    if ws and ws["kind"] != "legacy" and ws["status"] != "pending_claim":
        from .workspace_lifecycle import withdraw
        n = c.execute("UPDATE workspaces SET status='deleted', deleted_at=?, recover_until=?, "
                      "previous_status=CASE WHEN status='frozen' OR (status='deleted' AND previous_status='frozen') "
                      "THEN 'frozen' ELSE ? END, updated_at=? WHERE id=? AND kind<>'legacy' "
                      "AND status<>'pending_claim' AND (deleted_at IS NULL OR deleted_at<?)",
                      (e["deleted_at"], e["recover_until"], e["previous_status"], at, wid, e["deleted_at"])).rowcount
        withdraw(c, wid, at)
    c.execute("INSERT INTO meta (key,value) VALUES (?, '1')", (receipt,))
    return n


def _end_platform_grants(c, uid: str, t: float) -> None:
    """A replayed deletion ends the account's platform grants too, as the deletion itself did
    (profile.finalize_deletions), with the same `admin.platform.revoke` record. Left live, they
    would disagree with the `platform_role='none'` just written, and the next Store open would
    end them as if the previous release had demoted the account during a rollback."""
    from .platform_access import SYSTEM_REASONS
    reason = SYSTEM_REASONS["journal_deleted"]
    try:
        rows = c.execute("SELECT id, role FROM platform_role_grants WHERE user_id=? AND revoked_at IS NULL "
                         "ORDER BY granted_at, id", (uid,)).fetchall()
    except sqlite3.OperationalError:   # a database from before schema 27 has no grants
        return
    for g in rows:
        c.execute("UPDATE platform_role_grants SET revoked_at=?, revoked_by='system:journal_replay', "
                  "revoke_reason=? WHERE id=?", (t, reason, g["id"]))
        c.execute("INSERT INTO audit_events (id,at,actor_kind,actor_id,workspace_id,action,object_kind,object_id,"
                  "result,request_id,ip,meta) VALUES (?,?,'system',NULL,NULL,'admin.platform.revoke','user',?,"
                  "'ok',NULL,NULL,?)",
                  (new_id("ae"), t, uid, json.dumps({"grant_id": g["id"], "role": g["role"],
                                                      "source": "system:journal_replay",
                                                      "reason": reason}, ensure_ascii=False)))


def _apply_platform_revocation(store: Store, c, entry: dict) -> int:
    """End the named grant, or its regenerated migration equivalent from a v26 backup.

    A pre-v27 backup cannot hold the original grant ID. Store has just migrated its legacy
    admin mirror into a new host grant. Its unchanged users.updated_at proves that this
    mirror predates the withdrawal. A later legacy regrant updates that timestamp; a later
    normal grant has a different ID and source. Neither is revoked by this old intent.
    """
    from .platform_access import PlatformAccess, SUPER_ADMIN, _system_audit
    receipt = _PLATFORM_RECEIPT + entry["event_id"]
    if c.execute("SELECT 1 FROM meta WHERE key=?", (receipt,)).fetchone():
        return 0
    uid, at = entry["user_id"], entry["at"]
    exact = c.execute("SELECT * FROM platform_role_grants WHERE id=?", (entry["grant_id"],)).fetchone()
    candidates = []
    if exact:
        if any(exact[key] != entry[key] for key in ("user_id", "role", "granted_at", "granted_by")):
            raise ValueError("platform withdrawal does not match the restored grant; restore refused")
        if exact["revoked_at"] is None:
            candidates.append(dict(exact))
    elif entry["role"] == SUPER_ADMIN:
        candidates = [dict(row) for row in c.execute(
            "SELECT g.* FROM platform_role_grants g JOIN users u ON u.id=g.user_id "
            "WHERE g.user_id=? AND g.role=? AND g.revoked_at IS NULL "
            "AND g.granted_by IN ('migration:v27','reconcile:mirror') AND g.granted_at>? "
            "AND u.platform_role='admin' AND u.updated_at<=?", (uid, SUPER_ADMIN, at, at))]
    for grant in candidates:
        c.execute("UPDATE platform_role_grants SET revoked_at=?, revoked_by='system:journal_replay', "
                  "revoke_reason='security_journal_replay' WHERE id=? AND revoked_at IS NULL",
                  (at, grant["id"]))
        _system_audit(store, "admin.platform.revoke", uid,
                      {"grant_id": grant["id"], "role": grant["role"], "source": "system:journal_replay",
                       "withdrawal_id": entry["event_id"], "original_grant_id": entry["grant_id"],
                       "reason": "平台授权已撤销（安全日志重放）"})
    if candidates:
        PlatformAccess(store)._sync_mirror(uid, at)
    c.execute("INSERT INTO meta (key,value) VALUES (?, '1')", (receipt,))
    return len(candidates)


def _disable_suite_webhook(c: sqlite3.Connection, sid, t: float) -> int:
    """Stop a business application's subscription as its own dispatcher stops one: what was
    waiting is dropped, where it stopped is kept as the place to pull from, and its version and
    the time of the change move with the status. A resume then starts from the present, and never
    with a delivery the backup still held. One that was already disabled keeps its reason and when."""
    row = c.execute("SELECT * FROM suite_webhook_subscriptions WHERE id=?", (sid,)).fetchone()
    if not row:
        return 0
    reason = row["disabled_reason"] or "journal_replay"
    if row["status"] == "disabled":
        c.execute("UPDATE suite_webhook_subscriptions SET disabled_reason=?, updated_at=? WHERE id=?", (reason, t, sid))
    else:
        c.execute("UPDATE suite_webhook_subscriptions SET status='disabled', disabled_reason=?, status_changed_at=?, "
                  "updated_at=?, gap_cursor=?, gap_at=?, version=version+1 WHERE id=?",
                  (reason, t, t, *suite_webhooks.stopping(c, row), sid))
    c.execute("UPDATE suite_webhook_deliveries SET status='dropped', last_error=?, next_attempt_at=NULL "
              "WHERE subscription_id=? AND status='pending'", (f"subscription_disabled:{reason}", sid))
    return 1


def _apply_risk(c, e) -> int:
    """Re-suspend / re-open only what the restored database never recorded (by id)."""
    at = int(e["at"])
    if e["kind"] == "model_dispatch_suspended":
        return c.execute("INSERT OR IGNORE INTO model_risk_suspensions(id,environment,scope_kind,scope_id,source,"
                         "reason_code,created_at) VALUES (?,?,?,?,'journal','journal_replay',?)",
                         (e["suspension_id"], e["environment"], e["scope_kind"], e["scope_id"], at)).rowcount
    if c.execute("SELECT 1 FROM model_risk_breaker_events WHERE id=?", (e["event_id"],)).fetchone():
        return 0
    latest = c.execute("SELECT seq,state FROM model_risk_breaker_events WHERE environment=? ORDER BY seq DESC LIMIT 1",
                       (e["environment"],)).fetchone()
    if latest is not None and latest["state"] == "open":
        return 0
    c.execute("INSERT INTO model_risk_breaker_events(id,environment,seq,state,source,reason_code,created_at) "
              "VALUES (?,?,?,'open','journal','journal_replay',?)",
              (e["event_id"], e["environment"], (latest["seq"] if latest else 0) + 1, at))
    return 1


def _apply_payer_frozen(c, e) -> int:
    """Re-create a chargeback freeze the restored database never recorded (by its id).

    A freeze the database already holds is left exactly as it is, including an administrator's
    lift. A wallet account the restored database does not have held no funds there, so there is
    nothing to freeze. When the chargeback itself is not in the restored database (it arrived
    after the backup) the freeze is re-created without that link: the wallet stays frozen, and an
    administrator lifts it by the id in this entry once the dispute is settled."""
    if c.execute("SELECT 1 FROM billing_account_freezes WHERE id=?", (e["freeze_id"],)).fetchone():
        return 0
    if not c.execute("SELECT 1 FROM billing_accounts WHERE id=? AND environment=?",
                     (e["account_id"], e["environment"])).fetchone():
        return 0
    linked = c.execute("SELECT 1 FROM billing_chargebacks WHERE id=? AND account_id=?",
                       (e["chargeback_id"], e["account_id"])).fetchone()
    return c.execute("INSERT OR IGNORE INTO billing_account_freezes(id,account_id,reason,chargeback_id,created_at) "
                     "VALUES (?,?,'chargeback',?,?)",
                     (e["freeze_id"], e["account_id"], e["chargeback_id"] if linked else None, int(e["at"]))).rowcount


def _apply_ai_policy(c, e, t: float) -> int:
    """Make the workspace's policy at most as open as the journalled one: the narrower
    processing and the intersection of providers. A restored row (or its absence, which means
    the default) that is already as narrow is left alone, so a replay changes nothing twice."""
    wid = e["workspace_id"]
    ws = c.execute("SELECT kind FROM workspaces WHERE id=?", (wid,)).fetchone()
    if not ws:
        return 0
    row = c.execute("SELECT processing FROM agent_workspace_policies WHERE workspace_id=?", (wid,)).fetchone()
    prov = c.execute("SELECT providers_json FROM agent_workspace_model_providers WHERE workspace_id=?", (wid,)).fetchone()
    # The defaults of tools.workspace_policy: a personal workspace without a policy is open to
    # every provider; any other is closed, with the legacy provider list.
    personal_default = row is None and ws["kind"] == "personal"
    processing = row["processing"] if row else ("any" if personal_default else "none")
    try:
        providers = json.loads(prov["providers_json"]) if prov else (list(AI_PROVIDERS) if personal_default else ["anthropic"])
        providers = [p for p in providers if isinstance(p, str)] if isinstance(providers, list) else []
    except ValueError:
        providers = []
    narrowed = min(processing, e["processing"], key=AI_PROCESSING.index) if processing in AI_PROCESSING else "none"
    allowed = sorted(set(providers) & set(e["providers"]))
    if narrowed == processing and row is not None and allowed == sorted(providers) and prov is not None:
        return 0
    c.execute("INSERT INTO agent_workspace_policies (workspace_id, processing, updated_by, updated_at) VALUES (?,?,'journal',?) "
              "ON CONFLICT(workspace_id) DO UPDATE SET processing=excluded.processing, updated_by='journal', "
              "updated_at=excluded.updated_at", (wid, narrowed, t))
    c.execute("INSERT INTO agent_workspace_model_providers (workspace_id, providers_json, updated_by, updated_at) "
              "VALUES (?,?,'journal',?) ON CONFLICT(workspace_id) DO UPDATE SET providers_json=excluded.providers_json, "
              "updated_by='journal', updated_at=excluded.updated_at", (wid, json.dumps(allowed), t))
    return 1


def _apply_provider_disabled(c, e) -> int:
    """Append, if the restored history's newest version is enabled, an immutable version that
    switches the platform model provider off — keyed by the journal event, so it happens once.
    It reuses the newest version's author (who exists in that database) and says why."""
    op = "journal_" + e["event_id"]
    # The switch-off's own version (its operation id is the event id) or this replay's: the
    # restored history already has it, and any re-enabling after it stands.
    if c.execute("SELECT 1 FROM model_provider_versions WHERE operation_id IN (?,?)", (e["event_id"], op)).fetchone():
        return 0
    latest = c.execute("SELECT * FROM model_provider_versions WHERE environment=? ORDER BY revision DESC LIMIT 1",
                       (e["environment"],)).fetchone()
    if latest is None:
        return 0
    spec = json.loads(latest["spec_json"])
    if not spec.get("enabled"):
        return 0
    spec["enabled"] = False
    c.execute("INSERT INTO model_provider_versions(environment,revision,spec_json,credential_revision,credential_configured,"
              "actor_id,reason,operation_id,created_at) VALUES (?,?,?,?,?,?,?,?,?)",
              (e["environment"], latest["revision"] + 1, json.dumps(spec, sort_keys=True, separators=(",", ":")),
               latest["credential_revision"], latest["credential_configured"], latest["actor_id"],
               "Journal replay: the model provider was switched off after this backup", op, int(e["at"])))
    return 1


def _app_row(c, app_id: str, t: float) -> None:
    """A registry row for a restriction replayed into a database from before the application was
    registered: without one, the switch-off or tightening would change nothing (and the tightening
    would still consume its receipt), and the next start's registration would create the
    application on and open to every member. Fail closed instead: the placeholder takes the
    replayed restriction (the caller's UPDATE), and `Apps.register` keeps a row's status always and
    its policy when given none (every start-up registration), so the restriction survives the
    registration that fills in the rest. The name is the id until then; the launch address is empty,
    which `apps._http_url` reads as "no address yet" — never another application's default — and the
    process that serves the application writes its own when it starts."""
    c.execute("INSERT OR IGNORE INTO apps (id,name,icon,launch_url,status,access_policy,created_at,updated_at) "
              "VALUES (?,?,NULL,'','active','members',?,?)", (app_id, app_id, t, t))


# What a backup shows of a written-ahead line's own objects, per kind: (table, the rows, their keys
# from the line, the columns that take the time of a committed change). A timestamp there after the
# line and no later than the backup was committed after the line's transaction had ended (see
# _never_committed). Only columns written with the time of the change itself (or a journal line's
# own `at`), never a future time such as an expiry. Kinds absent here have nothing that can show it
# (the row a by-id withdrawal would create, or credentials every restore ends anyway) or are already
# replayed "as of" their time (consent withdrawals, workspace_unclaimed).
_OWN_OBJECTS = {
    "api_platform_approval_revoked": (("api_platform_approvals", "id=?", ("approval_id",),
                                       ("created_at", "decided_at", "consumed_at", "revoked_at")),),
    "api_key_superseded": (("api_keys", "id=?", ("key_id",), ("last_used_at", "revoked_at")),),
    "client_disabled": (("oauth_clients", "client_id=?", ("client_id",), ("updated_at",)),),
    "client_secret_rotated": (("oauth_clients", "client_id=?", ("client_id",), ("updated_at",)),),
    "user_suspended": (("users", "id=?", ("user_id",), ("updated_at",)),),
    "user_deleted": (("users", "id=?", ("user_id",), ("updated_at",)),),
    "password_changed": (("user_passwords", "user_id=?", ("user_id",), ("updated_at",)),),
    "member_removed": (("memberships", "workspace_id=? AND user_id=?", ("workspace_id", "user_id"), ("updated_at",)),
                       ("app_entitlements", "workspace_id=? AND user_id=?", ("workspace_id", "user_id"),
                        ("granted_at", "revoked_at"))),
    "role_changed": (("memberships", "workspace_id=? AND user_id=?", ("workspace_id", "user_id"), ("updated_at",)),),
    "entitlement_revoked": (("app_entitlements", "workspace_id=? AND app_id=? AND user_id=?",
                             ("workspace_id", "app_id", "user_id"), ("granted_at", "revoked_at")),),
    WORKSPACE_FROZEN_KIND: (("workspaces", "id=?", ("workspace_id",), ("updated_at",)),),
    "webhook_disabled": (("webhook_subscriptions", "id=?", ("subscription_id",), ("updated_at",)),
                         ("suite_webhook_subscriptions", "id=?", ("subscription_id",), ("updated_at",))),
    "passkey_removed": (("passkeys", "id=?", ("credential_id",), ("last_used_at",)),
                        ("webauthn_credentials", "id=?", ("credential_id",), ("last_used_at",))),
    "external_identity_unlinked": (("external_identities", "id=?", ("identity_id",), ("last_used_at",)),),
    "workspace_app_disabled": (("workspace_apps", "workspace_id=? AND app_id=?", ("workspace_id", "app_id"),
                                ("enabled_at",)),),
    "organization_app_disabled": (("organization_apps", "organization_id=? AND app_id=?", ("organization_id", "app_id"),
                                   ("updated_at",)),),
    "app_disabled": (("apps", "id=?", ("app_id",), ("updated_at",)),),
    "model_provider_disabled": (("model_provider_versions", "environment=?", ("environment",), ("created_at",)),),
}


def _touched_since(c, e: dict, since: float) -> bool:
    """Whether the restored copy holds a change to the line's own objects committed after the line
    and no later than the backup (_OWN_OBJECTS; an API key by its whole lineage)."""
    at = e["at"]
    objects = [(table, where, tuple(e[f] for f in fields), columns)
               for table, where, fields, columns in _OWN_OBJECTS.get(e["kind"], ())]
    if e["kind"] == "api_key_revoked":
        row = c.execute("SELECT COALESCE(lineage, id) AS l FROM api_keys WHERE id=?", (e["key_id"],)).fetchone()
        lineage = row["l"] if row else e["key_id"]
        objects = [("api_keys", "id=? OR lineage=? OR id=?", (e["key_id"], lineage, lineage),
                    ("created_at", "last_used_at", "revoked_at"))]
    for table, where, args, columns in objects:
        window = " OR ".join(f"({col}>? AND {col}<=?)" for col in columns)
        try:
            if c.execute(f"SELECT 1 FROM {table} WHERE ({where}) AND ({window}) LIMIT 1",
                         (*args, *((at, since) * len(columns)))).fetchone():
                return True
        except sqlite3.OperationalError:     # a table or column this database does not have
            continue
    return False


def _never_committed(c, entries: list[dict], since: float, held) -> set[str]:
    """The attempts older than the backup, lacking their (generic) receipt in it, that the backup
    proves never committed: their withdrawal is not replayed.

    Such a line is one of two things. (1) The backup's own race: it was copied between the line and
    its COMMIT, so it holds only what committed before the line's transaction began — every
    timestamp in it is older than the line. The withdrawal must be replayed. (2) An attempt that
    never committed and was never voided (the process stopped between the line and its COMMIT, or
    before its void). Replaying it would withdraw — once, the restore then writes its receipt —
    what the backup holds of the line's object, a change made after the attempt included. The
    backup tells them apart when it holds something committed after the line and no later than its
    own time: a line's transaction holds the writer lock from its receipt to its end, so a commit
    after the line's time came after that end, and a backup holding it was taken after the
    attempt's transaction had ended without committing. The evidence: the receipt of another
    attempted line written after it, no later than the backup (its time is taken under the writer
    lock, or before its commit), or a change to the line's own objects in that interval
    (_touched_since). Nothing later than the backup's time counts — not a write of this restore
    (the schema migration, an earlier replayed line), not a backup's own clock skew. An attempt the
    backup shows nothing after is replayed (fail-closed): a crashed withdrawal of an object nobody
    touched again before the backup, with no other withdrawal in between, still lands."""
    import bisect
    later = sorted(e["at"] for e in entries
                   if e.get("attempt_id") and e["kind"] != VOID_KIND and e["at"] <= since and held(_receipt(e)))
    out = set()
    for e in entries:
        attempt = e.get("attempt_id")
        if not attempt or e["kind"] == VOID_KIND or e["at"] > since:
            continue
        receipt = _receipt(e)
        if receipt != _RECEIPT + attempt or held(receipt):
            continue
        i = bisect.bisect_right(later, e["at"])
        if (i < len(later) and later[i] <= since) or _touched_since(c, e, since):
            out.add(attempt)
    return out


def apply(store: Store, entries: list[dict], *, since: float | None = None,
          outcome: dict | None = None) -> dict:
    """Make every withdrawal in `entries` true in `store`. Returns counts per kind.

    Runs in one transaction, so a restore either has every newer withdrawal re-applied or none.
    An attempt proven never to have committed — an `intent_voided` line among `entries`, or a
    `journal_void:` row the restored database holds, naming the line's `attempt_id` and its kind —
    is skipped (counted under `intent_voided`) and its void row written; a void naming another kind
    skips nothing (fail-closed). An attempt on the generic receipt (`journal_receipt:`) is skipped
    when the restored database holds that receipt, and the receipt is written once it is applied.

    `since`, the backup's time (snapshot.fence passes it): an attempt on the generic receipt older
    than it and without its receipt that the backup proves never committed (_never_committed) is
    skipped too, and voided in the restored copy. An attempted line older than the restored copy's
    EPOCH_KEY and without its receipt there belongs to a history a restore abandoned: skipped.

    `outcome`, when given, is filled with what happened to each line, for the restore's report:
    `replayed` (lines handed to their kind's rule — every written-ahead line not skipped, and old
    lines newer than the backup), `rechecked` (old lines older than the backup, by their own ids),
    `receipted` (skipped: the restored copy holds the receipt), `uncommitted` (proven never
    committed by the backup), `superseded_history` (older than the epoch) and `before_backup` —
    per kind, the written-ahead lines older than the backup that were replayed: the backup's own
    race, or an attempt nothing showed to have failed.
    """
    for number, entry in enumerate(entries, 1):
        _validate(entry, number)
    t = time.time()
    counts: dict[str, int] = {}
    out = outcome if outcome is not None else {}
    for key in ("replayed", "rechecked", "receipted", "uncommitted", "superseded_history"):
        out.setdefault(key, 0)
    out.setdefault("before_backup", {})
    # attempt_id → the kinds its voids name: a void skips only a line of the kind it names.
    voided: dict[str, set[str]] = {}
    for e in entries:
        if e["kind"] == VOID_KIND:
            voided.setdefault(e["attempt_id"], set()).add(e["voided_kind"])
    demoted_workspaces: set[str] = set()
    demoted_orgs: set[str] = set()
    role_rank = {"viewer": 0, "editor": 1, "admin": 2, "owner": 3}
    with store.tx() as c:
        from .platform_access import PlatformAccess
        # A fresh/never-bootstrapped archive may legitimately have no administrator. Reject
        # only a replay that removes the last usable one present at the start, whether by
        # grant withdrawal, account suspension/deletion or loss of its last sign-in proof.
        had_usable_super_admin = bool(PlatformAccess(store).usable_super_admins())

        def held(key: str) -> bool:
            return c.execute("SELECT 1 FROM meta WHERE key=?", (key,)).fetchone() is not None
        epoch = c.execute("SELECT value FROM meta WHERE key=?", (EPOCH_KEY,)).fetchone()
        epoch = float(epoch["value"]) if epoch else None
        # Decided on the restored copy as it was handed over, before any line below writes to it.
        uncommitted = _never_committed(c, entries, since, held) if since is not None else set()
        for e in entries:
            k = e.get("kind")
            n = 0
            if k == VOID_KIND:
                continue
            attempt = e.get("attempt_id")
            if attempt and (k in voided.get(attempt, ()) or c.execute(
                    "SELECT 1 FROM meta WHERE key=? AND value=?", (_VOID + attempt, k)).fetchone()):
                c.execute("INSERT OR IGNORE INTO meta (key,value) VALUES (?,?)", (_VOID + attempt, k))
                counts[VOID_KIND] = counts.get(VOID_KIND, 0) + 1
                continue
            if attempt and epoch is not None and e["at"] < epoch and not held(_receipt(e)):
                out["superseded_history"] += 1
                continue
            # The kinds receipted before `attempt_id` check their own receipts in their branches.
            generic = _RECEIPT + attempt if attempt and _receipt(e) == _RECEIPT + attempt else None
            if generic and held(generic):
                counts[k] = counts.get(k, 0)
                out["receipted"] += 1
                continue
            account_event = e.get("account_event_id")
            if account_event and held("account_withdrawal:" + account_event):
                out["receipted"] += 1
                continue
            if attempt in uncommitted:
                # Proven by the backup (_never_committed): the attempt's transaction ended without
                # committing. Voided here, so a backup of this restored copy skips it too.
                c.execute("INSERT OR IGNORE INTO meta (key,value) VALUES (?,?)", (_VOID + attempt, k))
                out["uncommitted"] += 1
                continue
            if not (generic or account_event) and k in _OWN_RECEIPT_KINDS and held(_receipt(e)):
                out["receipted"] += 1                    # its branch below finds the receipt and skips it
            elif attempt or account_event or since is None or e["at"] > since:
                out["replayed"] += 1
                if generic and since is not None and e["at"] <= since:
                    out["before_backup"][k] = out["before_backup"].get(k, 0) + 1
            else:
                out["rechecked"] += 1
            if k == PLATFORM_GRANT_KIND:
                n = _apply_platform_revocation(store, c, e)
            elif k in RISK_KINDS:
                n = _apply_risk(c, e)
            elif k == PAYER_FREEZE_KIND:
                n = _apply_payer_frozen(c, e)
            elif k in ORGANIZATION_KINDS:
                n = _apply_organization(c, e, demoted_orgs, demoted_workspaces)
            elif k == UNCLAIM_KIND:
                n = _apply_unclaim(c, e)
            elif k == WORKSPACE_DELETED_KIND:
                n = _apply_workspace_deleted(c, e)
            elif k == "api_key_revoked":
                kid = e.get("key_id")
                row = c.execute("SELECT COALESCE(lineage, id) AS l FROM api_keys WHERE id=?", (kid,)).fetchone()
                lineage = row["l"] if row else kid
                n = c.execute("UPDATE api_keys SET revoked_at=COALESCE(revoked_at, ?), revoke_reason=COALESCE(revoke_reason,'revoked') "
                              "WHERE id=? OR lineage=? OR id=?", (t, kid, lineage, lineage)).rowcount
            elif k == "client_disabled":
                n = c.execute("UPDATE oauth_clients SET disabled_at=COALESCE(disabled_at, ?) WHERE client_id=?",
                              (t, e.get("client_id"))).rowcount
                c.execute("UPDATE oauth_tokens SET revoked_at=?, revoked_reason='client_disabled' "
                          "WHERE client_id=? AND revoked_at IS NULL", (t, e.get("client_id")))
            elif k == "consent_withdrawn":
                # A restored backup must not bring back a consent the person withdrew afterwards: it
                # would let the client's next authorization skip the consent page.
                ws = e.get("workspace_id") or ""
                n = c.execute("UPDATE oauth_consents SET revoked_at=?, revoked_reason='journal_replay', updated_at=? "
                              "WHERE user_id=? AND client_id=? AND workspace_id=? AND revoked_at IS NULL AND granted_at<=?",
                              (t, t, e["user_id"], e["client_id"], ws, e["at"])).rowcount
                c.execute("UPDATE oauth_tokens SET revoked_at=?, revoked_reason='journal_replay' WHERE user_id=? AND client_id=? "
                          "AND COALESCE(workspace_id,'')=? AND revoked_at IS NULL", (t, e["user_id"], e["client_id"], ws))
            elif k == "agent_provider_consent_withdrawn":
                # Only a consent given before the withdrawal is withdrawn again; one the person
                # gave afterwards (and that the restored database holds) stands.
                n = c.execute("UPDATE agent_provider_consents SET withdrawn_at=? WHERE user_id=? AND provider=? "
                              "AND withdrawn_at IS NULL AND accepted_at<=?",
                              (e["at"], e["user_id"], e["provider"], e["at"])).rowcount
            elif k == "agent_access_suspended":
                # By id: a suspension the backup already holds (lifted or not) is left as it is.
                n = c.execute("INSERT OR IGNORE INTO agent_access_suspensions (id,user_id,reason,created_by,created_at) "
                              "VALUES (?,?,'journal_replay','journal',?)",
                              (e["suspension_id"], e["user_id"], e["at"])).rowcount
            elif k == "ai_policy_narrowed":
                receipt = _AI_POLICY_RECEIPT + e["event_id"]
                if not c.execute("SELECT 1 FROM meta WHERE key=?", (receipt,)).fetchone():
                    n = _apply_ai_policy(c, e, t)
                    c.execute("INSERT INTO meta (key,value) VALUES (?, '1')", (receipt,))
            elif k == "model_credential_withdrawn":
                receipt = _CREDENTIAL_RECEIPT + e["event_id"]
                if not (c.execute("SELECT 1 FROM meta WHERE key=?", (receipt,)).fetchone() or c.execute(
                        "SELECT 1 FROM model_provider_versions WHERE environment=? AND operation_id=?",
                        (e["environment"], e["event_id"])).fetchone()):
                    # The withdrawn revision and anything older loses its sealed secret; a newer
                    # credential the restored database already holds is left alone.
                    n = c.execute("UPDATE model_provider_credentials SET secret_enc=NULL WHERE environment=? "
                                  "AND revision<=? AND secret_enc IS NOT NULL",
                                  (e["environment"], e["credential_revision"])).rowcount
                    c.execute("INSERT INTO meta (key,value) VALUES (?, '1')", (receipt,))
            elif k == "model_provider_disabled":
                n = _apply_provider_disabled(c, e)
            elif k == "client_secret_rotated":
                n = c.execute("UPDATE oauth_clients SET secret_hash=? WHERE client_id=?",
                              (e.get("secret_hash"), e.get("client_id"))).rowcount
            elif k == "tokens_revoked":
                where, args = [], []
                for col in ("client_id", "user_id", "family_id"):
                    if e.get(col):
                        where.append(f"{col}=?")
                        args.append(e[col])
                if where:
                    n = c.execute(f"UPDATE oauth_tokens SET revoked_at=?, revoked_reason='journal_replay' "
                                  f"WHERE revoked_at IS NULL AND {' AND '.join(where)}", (t, *args)).rowcount
            elif k in ("user_suspended", "user_deleted"):
                uid, at = e["user_id"], e["at"]
                if k == "user_deleted":
                    # Deletion is terminal even if an earlier withdrawal already suspended
                    # the user. Restore must also erase the profile/credentials which the
                    # older backup still contains. Keep the stable ID and reserved email_key;
                    # never cascade into organization contributions or coworker documents.
                    n = c.execute("UPDATE users SET status='deleted',deleted_at=COALESCE(deleted_at,?),"
                                  "updated_at=?,display_name=NULL,email='',email_verified_at=NULL,"
                                  "avatar_blob=NULL,locale='zh-Hans',timezone='UTC',platform_role='none' WHERE id=?",
                                  (at, at, uid)).rowcount
                    for table in ("user_passwords", "passkeys", "webauthn_credentials", "totp_secrets",
                                  "recovery_codes", "account_recoveries", "mfa_tickets", "webauthn_challenges", "reauth_challenges"):
                        c.execute(f"DELETE FROM {table} WHERE user_id=?", (uid,))
                    c.execute("UPDATE email_tokens SET used_at=COALESCE(used_at,?) WHERE user_id=?", (at, uid))
                    _end_platform_grants(c, uid, t)
                    # The older backup still holds the person's Agent conversations and memories.
                    from .agent_records import erase as erase_agent
                    erase_agent(store, uid)
                else:
                    for table in ("mfa_tickets", "webauthn_challenges", "reauth_challenges"):
                        c.execute(f"DELETE FROM {table} WHERE user_id=?", (uid,))
                    for table in ("email_tokens", "account_recoveries"):
                        c.execute(f"UPDATE {table} SET used_at=COALESCE(used_at,?) WHERE user_id=?", (at, uid))
                    n = c.execute("UPDATE users SET status='suspended' WHERE id=? AND status IN ('active','pending')",
                                  (uid,)).rowcount
                c.execute("UPDATE oauth_codes SET used_at=COALESCE(used_at,?) WHERE user_id=?", (at, uid))
                c.execute("UPDATE sessions SET revoked_at=?, revoked_reason='journal_replay' WHERE user_id=? AND revoked_at IS NULL",
                          (t, e.get("user_id")))
                c.execute("UPDATE oauth_tokens SET revoked_at=?, revoked_reason='journal_replay' WHERE user_id=? AND revoked_at IS NULL",
                          (t, e.get("user_id")))
            elif k in ("sessions_revoked", "password_changed"):
                # The new password hash is not journalled. A restore that predates a password
                # change therefore brings the *old* password back — so every session and token of
                # that account is ended and the account is flagged for a reset rather than
                # silently re-opened with a credential that may be the reason it was changed.
                n = c.execute("UPDATE sessions SET revoked_at=?, revoked_reason='journal_replay' WHERE user_id=? AND revoked_at IS NULL",
                              (t, e.get("user_id"))).rowcount
                c.execute("UPDATE oauth_tokens SET revoked_at=?, revoked_reason='journal_replay' WHERE user_id=? AND revoked_at IS NULL",
                          (t, e.get("user_id")))
                if k == "sessions_revoked":
                    # The live account operation also ends unfinished authentication. An old
                    # backup must not turn its spent MFA/email/Passkey/OIDC proof into a new
                    # session after its restored sessions were revoked. Match invalidate_access.
                    for table in ("oauth_codes", "email_tokens"):
                        c.execute(f"UPDATE {table} SET used_at=COALESCE(used_at,?) WHERE user_id=?",
                                  (t, e["user_id"]))
                    for table in ("mfa_tickets", "webauthn_challenges", "reauth_challenges"):
                        c.execute(f"DELETE FROM {table} WHERE user_id=?", (e["user_id"],))
                if k == "password_changed":
                    c.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
                              (f"password_reset_required:{e.get('user_id')}", str(t)))
            elif k == "member_removed":
                n = c.execute("DELETE FROM memberships WHERE workspace_id=? AND user_id=?",
                              (e.get("workspace_id"), e.get("user_id"))).rowcount
                c.execute("UPDATE app_entitlements SET revoked_at=COALESCE(revoked_at, ?) WHERE workspace_id=? AND user_id=?",
                          (t, e.get("workspace_id"), e.get("user_id")))
            elif k == "role_changed":
                # Grants are not journalled. The new owner may therefore be absent from this
                # backup; retaining the old owner's withdrawn authority is not a safe repair.
                # Apply only reductions, then freeze any affected workspace with no usable
                # owner after all withdrawals. Never recreate a missing grant or membership.
                wid, uid, role = e.get("workspace_id"), e.get("user_id"), e.get("role")
                cur = c.execute("SELECT role, can_export FROM memberships WHERE workspace_id=? AND user_id=?",
                                (wid, uid)).fetchone()
                if role != "owner":
                    demoted_workspaces.add(wid)
                if cur and (role_rank[role] < role_rank.get(cur["role"], -1)
                            or role == cur["role"] == "viewer" and cur["can_export"]):
                    n = c.execute("UPDATE memberships SET role=?, "
                                  "can_export=CASE WHEN ?='viewer' THEN 0 ELSE can_export END, "
                                  "updated_at=? WHERE workspace_id=? AND user_id=?",
                                  (role, role, t, wid, uid)).rowcount
            elif k == "workspace_app_disabled":
                n = c.execute("DELETE FROM workspace_apps WHERE workspace_id=? AND app_id=?",
                              (e.get("workspace_id"), e.get("app_id"))).rowcount
            elif k == "organization_app_disabled":
                # An upsert, not an UPDATE: a backup from before the switch-off has no row (on by
                # default). No organization row is required (the table has no foreign key), so a
                # backup that predates the organization still restores. A later switch-on is in a
                # backup that holds the receipt, and that line is skipped above.
                n = c.execute("INSERT INTO organization_apps (organization_id,app_id,status,updated_at,updated_by) "
                              "VALUES (?,?,'disabled',?,'journal') ON CONFLICT(organization_id,app_id) DO UPDATE SET "
                              "status='disabled', updated_at=excluded.updated_at, updated_by='journal' "
                              "WHERE organization_apps.status<>'disabled'",
                              (e["organization_id"], e["app_id"], t)).rowcount
            elif k == "app_disabled":
                from .apps import SUITE_UNRELEASED_PREFIX
                _app_row(c, e["app_id"], t)
                n = c.execute("UPDATE apps SET status='disabled', updated_at=? WHERE id=? AND status<>'disabled'",
                              (t, e["app_id"])).rowcount
                # The operator's switch-off ends the deployment's hold too, as Apps.set_status does:
                # a marker the restored database still holds would let the next start that releases
                # the application turn it back on.
                c.execute("DELETE FROM meta WHERE key=?", (SUITE_UNRELEASED_PREFIX + e["app_id"],))
            elif k == APP_POLICY_KIND:
                # By receipt (APP_POLICY_KIND): a database holding it holds the tightening and any
                # loosening after it. Entitlement rows are not touched: a grant held then counts now.
                receipt = _APP_POLICY_RECEIPT + e["event_id"]
                if not c.execute("SELECT 1 FROM meta WHERE key=?", (receipt,)).fetchone():
                    _app_row(c, e["app_id"], t)
                    n = c.execute("UPDATE apps SET access_policy='explicit', updated_at=? WHERE id=? "
                                  "AND access_policy<>'explicit'", (t, e["app_id"])).rowcount
                    c.execute("INSERT INTO meta (key,value) VALUES (?, '1')", (receipt,))
            elif k == "invitation_revoked":
                n = c.execute("UPDATE invitations SET revoked_at=COALESCE(revoked_at, ?) WHERE id=? AND accepted_at IS NULL",
                              (t, e.get("invitation_id"))).rowcount
            elif k == "api_key_superseded":
                n = c.execute("UPDATE api_keys SET revoked_at=COALESCE(revoked_at, ?), revoke_reason=COALESCE(revoke_reason,'rotated') "
                              "WHERE id=?", (t, e.get("key_id"))).rowcount
            elif k == "api_platform_approval_revoked":
                n = c.execute("UPDATE api_platform_approvals SET status='revoked',revoked_at=COALESCE(revoked_at,?) WHERE id=?",
                              (e.get("at", t), e.get("approval_id"))).rowcount
            elif k == "entitlement_revoked":
                # The members policy needs no grant row, so older backups can have nothing
                # to UPDATE. A prior membership's stale revocation must also be replaced:
                # current access and deferred work compare it with membership.created_at.
                # Keep the durable withdrawal time, not each replay's wall clock: repeating
                # a restore must not move this revocation past a later membership/grant.
                revoked_at = e.get("at", t)
                n = c.execute("INSERT INTO app_entitlements "
                              "(workspace_id,app_id,user_id,granted_at,granted_by,revoked_at) "
                              "VALUES (?,?,?,?,NULL,?) ON CONFLICT(workspace_id,app_id,user_id) "
                              "DO UPDATE SET revoked_at=MAX(COALESCE(app_entitlements.revoked_at,0),excluded.revoked_at)",
                              (e.get("workspace_id"), e.get("app_id"), e.get("user_id"), revoked_at, revoked_at)).rowcount
            elif k == "workspace_frozen":
                n = _replay_freeze(c, e.get("workspace_id"), t)
            elif k == "webhook_disabled":
                n = c.execute("UPDATE webhook_subscriptions SET status='disabled', disabled_reason=COALESCE(disabled_reason,'journal_replay'), "
                              "updated_at=? WHERE id=?", (t, e.get("subscription_id"))).rowcount
                # The business applications' subscriptions are journalled under the same kind; their
                # ids do not collide with Docs' (suite_schema/webhooks.py).
                n += _disable_suite_webhook(c, e.get("subscription_id"), t)
            elif k == "passkey_removed":
                for table in ("webauthn_credentials", "passkeys"):
                    try:
                        n += c.execute(f"DELETE FROM {table} WHERE id=?", (e.get("credential_id"),)).rowcount
                    except Exception:  # noqa: BLE001 - table name differs across versions
                        pass
            elif k == "external_identity_unlinked":
                # By id: a later link of the same provider account is a new row and stays.
                n = c.execute("DELETE FROM external_identities WHERE id=?", (e["identity_id"],)).rowcount
            if account_event:
                c.execute("INSERT OR IGNORE INTO meta (key,value) VALUES (?, '1')",
                          ("account_withdrawal:" + account_event,))
            if generic:
                c.execute("INSERT OR IGNORE INTO meta (key,value) VALUES (?, '1')", (generic,))
            counts[k] = counts.get(k, 0) + n
        if had_usable_super_admin and not PlatformAccess(store).usable_super_admins():
            # Never resurrect a revoked administrator when the backup lacks its successor.
            raise ValueError("restored backup has no usable super administrator after security withdrawals; "
                             "restore refused; use a newer backup containing an authorized successor")
        for oid in sorted(demoted_orgs):
            owner = c.execute("SELECT 1 FROM organization_memberships m JOIN users u ON u.id=m.user_id "
                              "WHERE m.organization_id=? AND m.role='owner' "
                              "AND u.status='active' AND u.deleted_at IS NULL LIMIT 1", (oid,)).fetchone()
            if not owner:
                n = c.execute("UPDATE organizations SET status='frozen',updated_at=?,version=version+1 "
                              "WHERE id=? AND status='active'", (t, oid)).rowcount
                scope = [r["id"] for r in c.execute("SELECT id FROM workspaces WHERE organization_id=?", (oid,))]
                _org_revoke(c, scope, t)
                counts["ownerless_organizations_frozen"] = counts.get("ownerless_organizations_frozen", 0) + n
        for wid in sorted(demoted_workspaces):
            owner = c.execute("SELECT 1 FROM memberships m JOIN users u ON u.id=m.user_id "
                              "JOIN workspaces w ON w.id=m.workspace_id LEFT JOIN organization_memberships om "
                              "ON om.organization_id=w.organization_id AND om.user_id=u.id "
                              "WHERE m.workspace_id=? AND m.role='owner' "
                              "AND (w.organization_id IS NULL OR om.user_id IS NOT NULL) "
                              "AND u.status='active' AND u.deleted_at IS NULL LIMIT 1", (wid,)).fetchone()
            if not owner:
                n = _replay_freeze(c, wid, t)
                counts["role_changed_workspaces_frozen"] = counts.get("role_changed_workspaces_frozen", 0) + n
    return counts
