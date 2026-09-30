"""Live authorization before returning stored write results; never repeats business writes.

Dataset permissions are workspace-scoped. Snapshots made from Forms additionally retain a read
dependency on each originating submission; derived datasets retain their input dependencies.
The coordinator's result_guard/inspect contract invokes this on history and idempotent replay.
"""
from __future__ import annotations

import json
from synwevo_core.errors import ServiceError
from synwevo_core.capabilities import fingerprint


def parsed(value):
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            return None
    return value


def hidden():
    raise ServiceError(403, 'result_unavailable', '历史结果的来源权限已改变，不能显示旧内容')


def check_sources(service, workspace, user, dataset_id, seen=None):
    seen = set() if seen is None else seen
    if dataset_id in seen:
        return
    if len(seen) >= 64:
        hidden()
    seen.add(dataset_id)
    service.caps.authorize(service.caps.registry.get('dataprep.dataset.get'), user, workspace)
    row = service.get('datasets', workspace, dataset_id)
    source = parsed(row['source_json']) or {}
    if row['origin'] == 'form':
        inspect = getattr(service.caps, 'inspect', None)
        if inspect is None:
            # Legacy core cannot provide an unrecorded source read. Fail closed instead of
            # generating new operation records from a history guard.
            hidden()
        for sid in source.get('submission_ids', []):
            current = inspect('forms.submission.get', {'submission_id': sid}, user_id=user, workspace_id=workspace)
            if isinstance(current, dict) and isinstance(current.get('output'), dict):
                current = current['output']
            if not isinstance(current, dict) or 'answers' not in current:
                hidden()
            if source.get('answer_fingerprints', {}).get(sid) != fingerprint(current['answers']):
                hidden()
    for ref in source.get('inputs', {}).values():
        check_sources(service, workspace, user, ref['dataset_id'], seen)


def result_guard(service, call, historic):
    if historic.get('workspace_id') != call.workspace_id or historic.get('user_id') != call.user_id:
        hidden()
    service.caps.authorize(call.op, call.user_id, call.workspace_id)
    input, output = parsed(historic.get('input')), parsed(historic.get('output'))
    if not isinstance(input, dict) or not isinstance(output, dict):
        hidden()
    for sid in input.get('submission_ids', []):
        inspect = getattr(service.caps, 'inspect', None)
        if inspect is None:
            hidden()
        inspect('forms.submission.get', {'submission_id': sid}, user_id=call.user_id, workspace_id=call.workspace_id)
    refs = []
    for obj in (input, output):
        refs.extend(v for k, v in obj.items() if k in ('id', 'dataset_id', 'output_dataset_id', 'recipe_id', 'run_id', 'file_id') and isinstance(v, str))
        for ref in obj.get('inputs', {}).values():
            refs.append(ref.get('dataset_id') if isinstance(ref, dict) else ref)
    for ref in set(refs):
        if not isinstance(ref, str):
            hidden()
        if ref.startswith('dset_'):
            check_sources(service, call.workspace_id, call.user_id, ref)
        elif ref.startswith('dpr_'):
            service.get('recipes', call.workspace_id, ref)
        elif ref.startswith('dpu_'):
            run = service.get('runs', call.workspace_id, ref)
            for source in (parsed(run['inputs_json']) or {}).values():
                check_sources(service, call.workspace_id, call.user_id, source['dataset_id'])
            if run['output_dataset_id']:
                check_sources(service, call.workspace_id, call.user_id, run['output_dataset_id'])
        elif ref.startswith('dpf_'):
            service.get('files', call.workspace_id, ref)
    # Every successful write names a resource; unknown/unprovable shapes are hidden.
    if not refs:
        hidden()
