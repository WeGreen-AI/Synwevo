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
| 8 | The source's policy is `audience` and the write reaches people who are not necessarily the target's members (an operation whose side effect is `external`: a message, a fill-in request, a public link; or a write the application knows lands in front of link holders, such as publishing a form whose public link follows the latest version): refused, because the membership comparison cannot vouch for those recipients. `any` (with an organization cap of `any`) is the owner's decision to allow it. | `transfer_audience_external` |
| 9 | The source belongs to an organization whose content stays inside it — the workspace's policy is `organization` or the organization's cap is `organization`, including when nobody has chosen either (the defaults) — and the write reaches people outside the target (as in 8): refused, because those recipients need not be anyone in the organization. A send made from the source workspace itself is not a transfer and is not asked. | `transfer_organization_external` |

All refusals are HTTP 403. A workspace whose organization is frozen or deleted, or whose organization the person has left, takes no operation of any application at all (`app_access_denied`), including a change of its own policies. A stored value this code does not know (written by a later version) is treated as
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
  * **Which writes count.** Writing into the workspace that will own the result counts as a transfer from every other workspace whose content reached the model in this conversation branch. Catalogue names alone do not count, and withheld results do not count. The model's input cannot be traced word by word, so every such source counts. A copy-type operation (`writes_elsewhere`) also counts its own source. What a write of this conversation carried into a workspace keeps where it came from: reading it back there, the receipt of that write, and any later write into that workspace — in any branch of the conversation, since a workspace's state is shared — count those sources too (a copy made in the conversation does not lose its source). The same holds for what the writes of a flow carried into a workspace when the conversation started or resumed that flow, or when an event of one of the conversation's writes started it (each such chain of runs, by correlation ID): a copy a started flow made is not laundered by reading it back. This is for the transfer question only: the copy is the target's content, so the model reads it under the target's processing policy, and leaving the source does not hide it.
  * **When it is checked.** At three points:
    * when the call is proposed, so no card is shown for a refused transfer;
    * when the person confirms: the confirmation fingerprint includes each source's policy facts, so any change voids it. The card is then shown again with what it now rests on (for example a source the model read later in the same step), and the answer is 409 `confirmation_mismatch`; nothing runs until the person confirms the new card;
    * immediately before the operation runs.
  * **Scope.** A conversation limited to some workspaces also limits the flows the Agent defines, publishes, starts or resumes: every workspace a step, a result read or a wait acts in must be inside it (`outside_conversation_scope`).
  * **What the person sees.** The card lists the source workspaces under the target, so the person can see who will be able to read the content.
  * **Provenance.** The target's run record keeps `delegation.transfer_from`: the source workspace IDs only, no content.
  * **Personal data.** Personal operations (memories) still never take workspace content (`source_transfer_unavailable`).
  * **Personal reads.** A personal operation's answer names no source workspace of its own, so the Agent's model is not given workspace content through one: `agent.flow.run` called by the Agent returns a run's progress without its steps' snapshots (the page still shows them). A run's progress — which steps ran, its state, its error code — was still decided on what the run read, so every flow operation answering the Agent names the run's sources (`source_workspaces`). They count as sources of the conversation like any other: the answer is withheld from the model where one of them allows no processing with the current credential, nothing of it becomes a personal memory, later writes are asked for them, and it is hidden once the person leaves one of them.
* **Forms duplicate** (`forms.form.duplicate` into another workspace). The check applies in the Forms service, so the operations endpoint and the Agent apply the same rule.
* **Forms seen through links.** A fill page (public link or personal request, open or closed) shows the title, description and fields of a published version, never the editors' draft, so saving a draft reaches nobody outside the workspace. Publishing does when the form's public link follows the latest version: the Forms service then asks every workspace in the call's `delegation.transfer_from` as an outward send (rule 8), whoever the caller is (the Agent, a flow). A person publishing by hand carries no recorded source and is not asked. Changing a request's deadline (`forms.request.extend`) is asked the same way, because the fill page shows the deadline to whoever holds the request's link. A public link pinned to a version, and personal requests (each pins the version it was issued with), are not reached by a new version: a public link answers only for the version it serves (a submit naming another one is 409 `form_version_changed`). A request is named after the version it pins, and notifications to a recipient or a submitter name that version. The person who created a request or opened a public link is told of each submission, naming the version answered, only while they may still read Forms in the workspace: a request keeps its creator after they leave, and re-enabling a public link keeps its request. A link of a workspace whose organization is frozen or deleted closes (410 `link_unavailable`).
* **Flows** (`packages/core/synwevo_core/flows/engine.py`). The question is asked with the flow owner's current rights, every time a step runs. A refusal blocks the run with the refusal's code, and resuming it asks again. This replaces the former blanket `flow_transfer_policy_required`.
  * **Sources of a run.** Inside a run, which value came from where is not traced: a literal re-read of a workspace the run wrote into, a branch or a wait that decided on a value, and a run started by an event of another all carry content with no reference to follow. So, as for the Agent, every source that reached the run counts. A workspace is a source of a run once:
    * a step has read it: a read-only step, a step's result read or a wait's read. This is taken from the stored step, so only reads that happened count;
    * a step took a value or an outcome (`steps.<id>.…`, `outcome.<id>`) from a step that ran or read there. This covers the receipt of a write; a write that no step refers to carries nothing;
    * a step ran there, failed, and the run went on through its `on_error` exit: a handled failure is an answer too (from where the step ran; a result read the step never reached told it nothing);
    * the event that started the run came from it. That the event happened, and its identifiers, are information about that workspace.
  * **Chains of runs.** A run started by an event keeps the correlation ID of the run whose step caused the event (an event that names only its operation run takes that run's correlation ID). The sources of a run include those of every run of the same person with the same correlation ID, including runs the same event started side by side, and every source any write of the chain recorded in its run record (`delegation.transfer_from`) — so a flow started by an Agent write still carries what the Agent was asked about. A second flow started by the first one's write therefore still carries what the first one read. A run a person starts begins its own chain: a correlation the caller names is not used for it.
  * **Writes.** Before a step whose operation has a side effect runs in a workspace, every source other than that workspace is asked, whether or not the step's input refers to anything: the path that led to the step was decided on what the run read. An operation whose side effect is `external` is asked as an outward send (rule 8). A two-phase operation is asked again immediately before it is dispatched.
  * **Reads.** A read whose input takes a value from the run (a step's value or outcome, or a parameter an event filled in) is asked the same way, whether it is a read step, a wait's read or a result read. A read with literal input, or with a parameter the person gave when starting the run, is not a transfer, so a run may read two workspaces to compare them. A result read in another workspace than its step is always asked, for every source and for the step's own workspace: before the step runs, again when the read runs, and step → read when the flow is defined.
  * **Provenance.** A write that was asked keeps `delegation.transfer_from` on the target's run record: the source workspace IDs only, no content.
  * **No personal steps.** Personal operations (memories, conversations, turns, the flow operations themselves) are not flow steps (`person_required`): they have no workspace, so they would take workspace content into personal records, or hand personal records to a workspace, with nothing to check.

## 4. After a transfer

The record created in the target is independent:
* It follows the target's access and policies.
* A write by the Agent or a flow keeps its provenance on the target's run record (`delegation.transfer_from`: source workspace IDs only). A Forms duplicate made by a person on its page is recorded in the source workspace's run log with the copy as its object; the copy itself has no source column.
* Later losing access to the source does not take it away from the target's readers.

Conversation content that still depends on the source stays under the source's access. When the person loses access, it is hidden (`access_changed`), and pending writes are refused (`source_access_changed`).

## 5. Known limits

* A read operation's parameters are not treated as a transfer: an ID or a search term sent to read another workspace is not checked. (Flows are stricter: there, a read whose input takes a value from the run is asked.)
* There is no inbound policy for targets. A target accepts what its writers may write, as when they type it themselves.
* Pending invitations to the target are not counted as members; the check is made at transfer time.
* A person with the export right can still carry content by hand: copy it into a workspace only they belong to, and later, in a separate action, from there to a third. The contract checks each transfer and, inside one flow run, one chain of event-started runs or one Agent conversation, the whole chain; it does not follow a record across separate actions (§4: a transferred record is independent).
* A chain of flow runs is followed for one person, by correlation ID. A run of another person, and a run started by an event that carries neither a correlation ID nor the operation run that caused it (today `extract.file.uploaded`), begin a new chain.
* Closing or revoking a fill-in request and accepting a submission are asked as plain writes, not as outward sends: they put no value of the call in front of link holders. A link holder still sees that the request stopped taking answers or that their answer was accepted, so a flow or Agent action that decides such a write on what it read can show link holders one bit per request. Asking them as outward sends would stop a person from shutting a link once a run has read anything; that trade-off is open for the owner's decision.
* A flow run counts sources conservatively. Once it has read a workspace, a later write elsewhere is asked about that workspace even if the written value is a literal, and runs that one event started side by side count each other's reads. A flow that must write into a workspace its sources may not reach has to do that write before it reads them.
* Docs has its own cross-workspace paths with their own authorization, which do not read this policy: project copies (source authorization, fixed snapshot, target approval; `docs/platform/project-copy.md`) and the organization template library (`docs/platform/org-templates.md`). A workspace set to `none` therefore still allows those two; bringing them under this policy is tracked in `docs/agent-work/BACKLOG.md`.
