"""Live authorization before returning stored write results; never repeats business writes.

Dataset permissions are workspace-scoped. Forms and Extract snapshots additionally retain a
read dependency on their current sources; derived datasets retain their input dependencies.
The coordinator's result_guard/inspect contract invokes this on history and idempotent replay.
"""
from __future__ import annotations

import json
from pydantic import ValidationError
from synwevo_core.errors import ServiceError
from synwevo_core.capabilities import fingerprint
from .models import ExtractSource, ExtractSnapshot
from ..common.files import available_blob


def parsed(value):
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            return None
    return value


def hidden():
    raise ServiceError(403, 'result_unavailable', '历史结果的来源权限已改变，不能显示旧内容')


def extract_result(service, workspace, user, ref):
    """Only the registry may resolve a current confirmed revision, including its live ACLs."""
    inspect = getattr(service.caps, 'inspect', None)
    operation = service.caps.registry.get('extract.task.result')
    if inspect is None or operation is None or operation.version != 1:
        raise ServiceError(409, 'source_unverifiable', '无法核实已确认的提取来源')
    result = inspect(operation.id, {'task_id': ref.task_id, 'revision': ref.revision},
                     user_id=user, workspace_id=workspace)
    if (not isinstance(result, dict) or result.get('task_id') != ref.task_id
            or result.get('revision') != ref.revision or not isinstance(result.get('values'), dict)
            or not isinstance(result.get('review'), dict) or result['review'].get('kind') != 'confirmation'
            or result['review'].get('task_id') != ref.task_id or result['review'].get('revision') != ref.revision
            or result['review'].get('values') != result['values']):
        raise ServiceError(409, 'source_unverifiable', '提取结果不符合已确认版本契约')
    return result


def extract_snapshot(result):
    try:
        return ExtractSnapshot(task_id=result['task_id'], revision=result['revision'],
            template_id=result['template_id'], template_version=result['template_version'],
            definition_digest=result['definition_digest'], file_id=result['file']['id'],
            content_digest=result['file']['content_digest'], result_fingerprint=fingerprint(result))
    except (KeyError, TypeError, ValidationError):
        raise ServiceError(409, 'source_unverifiable', '提取来源信息不完整') from None


def check_sources(service, workspace, user, dataset_id, seen=None):
    seen = set() if seen is None else seen
    if dataset_id in seen:
        return
    if len(seen) >= 64:
        hidden()
    seen.add(dataset_id)
    service.caps.authorize(service.caps.registry.get('dataprep.dataset.get'), user, workspace)
    row = service.get('datasets', workspace, dataset_id)
    available_blob(service.store, row['rows_blob'])
    if row['file_id']:
        service.get('files', workspace, row['file_id'])  # also checks the current quarantine verdict
    source = parsed(row['source_json']) or {}
    if source.get('kind') == 'extract':
        try:
            bound = ExtractSource.model_validate(source)
        except ValidationError:
            hidden()
        if bound.workspace_id != workspace:
            hidden()
        for ref in bound.tasks:
            current = extract_result(service, workspace, user, ref)
            if extract_snapshot(current) != ref:
                hidden()
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
    if call.op.id == 'dataprep.run.cancel':
        # A safe cancellation receipt has no source values, names, parameters or results.
        row = service.get('runs', call.workspace_id, input.get('run_id'))
        if set(output) != {'id', 'status', 'cancel_requested'} or output['id'] != row['id']:
            hidden()
        return
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
