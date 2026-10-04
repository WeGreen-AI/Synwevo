# Cross-workspace data flow

When content of one workspace (the **source**) becomes part of something another workspace (the
**target**) owns, Synwevo asks one question in one place: `synwevo_core.data_flow.check`. Being
able to read both workspaces is not enough. Decision of 2026-10-03 in `docs/agent-work/DECISIONS.md`.

## 1. The rule

The check passes only when all of these hold, read fresh on every call:

| # | Condition | Refusal code |
|---|---|---|
| 1 | The person is a current member of an active source workspace (and of its organization) | `transfer_source_unavailable` |
| 2 | The person may export from the source: owner, admin or editor, or a membership with `can_export` (Docs uses the same meaning: reading is not exporting) | `transfer_export_denied` |
| 3 | The target is an active workspace the person currently belongs to. The answer is the same for a workspace that does not exist, so a refusal says nothing about a workspace the person is not in; no policy fact is read before this point. | `transfer_target_unavailable` |
| 4 | The source's outbound policy is not `none` | `transfer_source_closed` |
| 5 | The source's organization cap is `any`, or the target is in the same organization | `transfer_organization_policy_denied` |
| 6 | The source's policy is `organization` only if the target is in the same organization | `transfer_organization_denied` |
| 7 | The source's policy is `audience` only if every current member of the target is already a member of the source. The refusal detail gives the number of members outside the source, never their names. | `transfer_audience_denied` |
| 8 | The source's policy is `audience` and the write reaches people who are not necessarily the target's members (an operation whose side effect is `external`: a message, a fill-in request, a public link): refused, because the membership comparison cannot vouch for those recipients. `organization` and `any` are the owner's decision to allow it. | `transfer_audience_external` |

All refusals are HTTP 403. A stored value this code does not know (written by a later version) is treated as
the strictest one: an unknown workspace policy as `none`, an unknown organization cap as `organization`.
Separately, the caller authorizes the write in the target exactly as for any other write.

## 2. Policies and defaults

| Level | Values | Default | Who changes it |
|---|---|---|---|
| Workspace (`workspace_transfer_policies`) | `none`, `audience`, `organization`, `any` | Personal workspace only its owner belongs to: `any`; workspace of an organization: `organization`; any other shared workspace, including a personal workspace that has been shared: `audience` | Workspace owners and admins. Operation `agent.transfer_policy.set` (`need=manage`, never by the Agent, never unattended) with the `revision` the person was looking at (0 for the default); event `agent.transfer_policy.changed` (`before`, `outbound`, `revision`); Agent settings → "Workspace data-flow policies" |
| Organization (`organization_transfer_policies`) | `organization`, `any` | `organization` | Organization owners only. `PATCH /api/auth/organizations/{id}` with `transfer_policy` and the organization `version`, behind the strict admin re-authentication; recorded in the `organization.update` audit event (before and after) |

The organization cap can only narrow a workspace policy, never widen it. Each change increments a
revision, and a confirmation is bound to that revision. A change is written only against the revision
it was decided on: a page loaded before someone else's change answers 409 (`transfer_policy_changed`,
`organization_changed`) instead of silently undoing it.

## 3. Where it applies

* **Personal Agent** (`apps/suite/synwevo_suite/agent/engine.py`).
  * **Which writes count.** Writing into the workspace that will own the result counts as a transfer from every other workspace whose content reached the model in this conversation branch. Catalogue names alone do not count, and withheld results do not count. The model's input cannot be traced word by word, so every such source counts. A copy-type operation (`writes_elsewhere`) also counts its own source.
  * **When it is checked.** At three points:
    * when the call is proposed, so no card is shown for a refused transfer;
    * when the person confirms: the confirmation fingerprint includes each source's policy facts, so any change voids it;
    * immediately before the operation runs.
  * **What the person sees.** The card lists the source workspaces under the target, so the person can see who will be able to read the content.
  * **Provenance.** The target's run record keeps `delegation.transfer_from`: the source workspace IDs only, no content.
  * **Personal data.** Personal operations (memories) still never take workspace content (`source_transfer_unavailable`).
  * **Personal reads.** A personal operation's answer names no source workspace, so the Agent's model is not given workspace content through one: `agent.flow.run` called by the Agent returns a run's progress without its steps' snapshots (the page still shows them).
* **Forms duplicate** (`forms.form.duplicate` into another workspace). The check applies in the Forms service, so the page, the machine API and the Agent all apply the same rule.
* **Flows** (`packages/core/synwevo_core/flows/engine.py`). A step that uses an earlier step's result from another workspace is checked with the flow owner's current rights. The check runs every time the reference is resolved, and again when a result-read is defined or runs in another workspace. This replaces the former blanket `flow_transfer_policy_required`.
  * **Lineage inside a run.** The sources of a step's value are the workspaces it ran and was read in, and the sources of every step its inputs referenced. A hop through a third workspace in one run therefore does not make the content that workspace's own: each original source is checked against the final target.
  * **No personal steps.** Personal operations (memories, conversations, turns, the flow operations themselves) are not flow steps (`person_required`): they have no workspace, so they would take workspace content into personal records, or hand personal records to a workspace, with nothing to check.

## 4. After a transfer

The record created in the target is independent:
* It follows the target's access and policies.
* It keeps its provenance (the source workspace ID).
* Later losing access to the source does not take it away from the target's readers.

Conversation content that still depends on the source stays under the source's access. When the person loses access, it is hidden (`access_changed`), and pending writes are refused (`source_access_changed`).

## 5. Known limits

* A read operation's parameters are not treated as a transfer: an ID or a search term sent to read another workspace is not checked. (Flows are stricter: there, a reference into a read in another workspace is checked.)
* There is no inbound policy for targets. A target accepts what its writers may write, as when they type it themselves.
* Pending invitations to the target are not counted as members; the check is made at transfer time.
* A person with the export right can still carry content by hand: copy it into a workspace only they belong to, and later, in a separate action, from there to a third. The contract checks each transfer and, inside one flow run or one Agent conversation, the whole chain; it does not follow a record across separate actions (§4: a transferred record is independent).
* Docs has its own cross-workspace paths with their own authorization, which do not read this policy: project copies (source authorization, fixed snapshot, target approval; `docs/platform/project-copy.md`) and the organization template library (`docs/platform/org-templates.md`). A workspace set to `none` therefore still allows those two; bringing them under this policy is tracked in `docs/agent-work/BACKLOG.md`.
