# Synwevo Approvals contract

Approvals is a workspace application. Its operations use the shared registry and Store; its pages
export `approvalsRoutes()` under `/approvals/w/:wid/…`. There are no public decision links.

## Rules and lifecycle

Templates have an editable draft with optimistic `draft_rev`, immutable published versions,
version retirement and template archival. Retiring a version blocks new requests; pinned requests
continue. Managing templates requires a workspace owner/admin. Reading requires app access and
membership; requesting and commenting require editor or above.

Requesters cannot decide or receive a transferred task on their own requests, and cannot repair
their own approver assignments. Decisions and transfers are assigned-person-only operations
(`agent="never"`, `unattended=False`). A separate eligible manager can repair a stalled stage.
The application deliberately does not infer permission to approve from an Agent's general task.

Stages execute sequentially. A stage may require all approvers, any one, or N distinct votes.
Conditions use typed field comparisons, including exact decimal amounts. At least one stage must
be unconditional. Role selectors exclude the requester and, when configured, earlier deciders.
Explicitly configured people who become ineligible do not silently lower the required vote count.
Missing people leave a stage stalled. Repair/escalation adds replacements without reducing quorum.
A rejection ends the request; a return requests a new content revision; withdrawal cancels it.

Every revision stores the submitted fields and evidence text, canonical SHA-256 fingerprints,
submitter, timestamp and revision note. These are independent copies belonging to the named
workspace, not dereferenced external-resource grants. No blob or attachment access is implied by
a checksum. A revision restarts **all** stages against the same pinned template version. Old
stages, decisions and snapshots remain available in the history. Approved/rejected requests may
be revised explicitly; withdrawn requests require a new request.

`state_version` is required for request mutations. Store's write transaction serializes votes,
withdrawals and revocations. A competing stale operation is refused rather than silently merged.
The caller and every counted live-stage voter are reauthorized inside that transaction. Replays
never manufacture a new task or decision. Each recorded decision names its task, person, revision,
operation run and invocation channel.

## Automation and durable waiting

- `approvals.request.create`: pins a published template and creates the actual request.
- `approvals.request.get`, `.revision`, `.timeline`, `.list`: query scoped content/history.
- `approvals.request.revise`, `.withdraw`: requester actions with `state_version`.
- `approvals.request.decide`, `.transfer`: assigned-person actions; no unattended decisions.
- `approvals.request.repair`: independent manager supplies missing eligible people.
- `approvals.request.wait`: persist a subscription to `{request_id, revision, timeout_hours}`.

A wait returns a registry `run_id` with `waiting_approval`; it occupies no worker/model session.
The shared run endpoint can query it. Approval/rejection completes the wait with an explicit
business `decision`; **a rejected decision is not permission to continue an approved branch**.
Withdrawal cancels it. Changing revisions cancels old subscriptions with `revision_changed`.
A wait timeout fails that subscription with `approval_timeout`; it does not reject or approve the
business request. Revocation fails the wait without exposing the result.

The installed 30-second ticker reconciles live eligibility, persisted deadlines, reminders,
escalations and wait timeouts. Each reminder/escalation flag, in-app alert and outbox event commits
atomically, so repeated ticks/process restarts do not duplicate them. Reminders are visible at
`/approvals/w/:wid/alerts`; this implementation does not send email or claim external delivery.

Events are version 1, with `object_kind="approval_request"`, the workspace and request identity,
revision, state version, status and correlation. Consume the outbox using its event ID/cursor:

- `approvals.request.approved`, `.rejected`, `.returned`, `.withdrawn`, `.revised`
- `approvals.task.decided`, `.transferred`, `.reassigned`, `.revoked`
- `approvals.stage.opened`, `.skipped`, `.approved`, `.reminder`, `.escalated`
- `approvals.request.created`, `.stalled`, `.resumed`, `.commented`

`approvals.template.published` identifies a template and its published version. The shared flow
runner must deduplicate its own downstream side effects by event/run identity and branch on the
specific request revision and decision, not on reminder delivery or operation success alone.

## Returning historical write results

Every write operation declares `result_guard(call, full_op_runs_row)`. It accepts JSON strings or
decoded `input`/`output`, reauthorizes app/workspace/role, verifies the original run owner and object
binding, reads the current resource, and requires its complete projection to match the stored
output. Changing a draft, revision, assignment, eligibility or status hides the old output with
`approval_result_unavailable`. The guard invokes no mutation or registered operation, writes no
run/event rows, and cannot repeat a decision. Unprovable/malformed projections are refused.

`ApprovalOperation` adds the coordinator's new field to the older base dataclass in this worktree;
it overrides the identical field after the shared change is integrated. It does **not** make an
old `Capabilities` implementation invoke the guard. Shared enforcement must be integrated before
releasing this app. Guarded history must cover individual runs, list/history projections,
idempotent replay and Agent-visible saved results. Read operations use the coordinator's fresh
`inspect`/fingerprint policy. The front end retains the original idempotency key after a guard
refusal, since a hidden result may represent a write that already committed.

## Integrated services

App/schema auto-discovery installs Approvals, and `approvalsRoutes` registers its suite UI.
`approval_template_versions.definition_hash`, `approval_revisions.fields_hash` and
`approval_revisions.basis_hash` are explicitly classified as non-blob fingerprints.
The integrated shared `Operation.result_guard` and `Capabilities.inspect` are exercised by
`test_integrated_history_and_idempotency_never_reexecute_or_expose_revoked_results`.

Local browser evidence covers requester/approver separation, a published pinned request,
restart while waiting and a real independent decision. This does not yet prove complete workflow
continuation or production release. See canonical `docs/agent-work/EVIDENCE.md` in the main checkout.

Limits: 50 fields, 20 stages, 50 selected people per selector, 500 historical tasks per stage,
50 content revisions, 64 KiB of submitted field values, 30,000 evidence characters, 2,000 comment
characters. Lists/history are paginated; current details include snapshot summaries and the
current/previous content, with older revisions retrieved separately. Template publication checks
field/condition consistency and the actual eligibility of explicitly named members.
