"""A DataPrep dataset becomes the input of a new Docs project configured like a template project.

Real composed factory, real Docs ingest/generation and DataPrep service; isolated fixture people.
"""
from __future__ import annotations

import io

import docx
import pytest

from synwevo_core.errors import ServiceError
from .test_suite_adapters import configure, finish, job_input, wait_for, world  # noqa: F401

pytestmark = pytest.mark.parametrize("world", ["forms,agent,dataprep"], indirect=True)


def dataset(w, columns=("Reference", "Marker"), rows=(("REF-001", "a"), ("REF-002", "b")), *, ws=None, user="editor"):
    return w.invoke("dataset.from_records", {"name": "Approved rows", "columns": list(columns),
                    "records": [dict(zip(columns, r)) for r in rows]}, app="dataprep", user=user, ws=ws)["output"]


def materialize(w, template, data, *, key="materialize-1", user="editor", ws=None):
    return w.invoke("project.from_dataset", {"template_project_id": template, "dataset_id": data["id"]},
                    key=key, user=user, ws=ws)


def ready(w, run):
    out = run["output"]
    wait_for(lambda: _status(w, out["source_id"]) == "ready")
    w.adapters.sync_inputs()
    return w.caps.run(run["run_id"], user_id=w.users["editor"])


def _status(w, sid):
    from synwevo_core.context import as_tenant
    with as_tenant(w.a):
        return w.adapters.docs.get_source(sid)["status"]


def test_an_approved_dataset_becomes_its_own_pinned_document_input(world):
    w = world
    template_pid, tid, version = configure(w, value=["TEMPLATE-ROW"])
    before = w.read("project.get", {"project_id": template_pid})
    run = materialize(w, template_pid, dataset(w))
    assert run["status"] == "queued" and run["output"]["project_id"] != template_pid
    done = ready(w, run)
    assert done["status"] == "succeeded" and done["output"]["state"] == "ready"
    pid = done["output"]["project_id"]
    project = w.read("project.get", {"project_id": pid})
    assert project["template"]["id"] == tid and project["template"]["version"] == version  # pinned
    assert [s["status"] for s in project["sources"]] == ["ready"]
    # The template project and its input are untouched by the run.
    assert w.read("project.get", {"project_id": template_pid})["configuration"] == before["configuration"]
    job = w.invoke("job.create", job_input(w, pid), key="generate-from-dataset")["output"]
    finish(w, job["job_id"])
    artifacts = w.read("job.artifacts", {"job_id": job["job_id"]})["items"]
    assert len(artifacts) == 2
    texts = []
    for a in artifacts:
        _, data = w.adapters.jobs.artifact(a["artifact_id"], w.a)
        texts.append("\n".join(p.text for p in docx.Document(io.BytesIO(data)).paragraphs))
    assert sorted(t.split("Receipt: ")[1] for t in texts) == ["REF-001", "REF-002"]
    assert w.store.one("SELECT COUNT(*) AS n FROM app_events WHERE type='docs.project.materialized' AND object_id=?", (pid,))["n"] == 1


def test_a_dataset_without_the_mapped_columns_creates_nothing(world):
    w = world
    template_pid, _, _ = configure(w)
    projects = w.store.one("SELECT COUNT(*) AS n FROM projects")["n"]
    with pytest.raises(ServiceError) as err:
        materialize(w, template_pid, dataset(w, columns=("Other", "Marker")))
    assert err.value.code == "dataset_columns_missing" and err.value.detail["missing"] == ["Reference"]
    assert w.store.one("SELECT COUNT(*) AS n FROM projects")["n"] == projects


def test_access_is_checked_on_both_sides_and_replays_make_one_project(world):
    w = world
    template_pid, _, _ = configure(w)
    data = dataset(w)
    with pytest.raises(ServiceError) as err:  # A viewer may neither write Docs nor export the dataset.
        materialize(w, template_pid, data, user="viewer", key="viewer-try")
    assert err.value.status in (403, 404)
    other = dataset(w, ws=w.b, user="owner")
    with pytest.raises(ServiceError) as err:  # A dataset of another workspace is not readable here.
        materialize(w, template_pid, other, key="cross-space")
    assert err.value.status in (403, 404)
    first = materialize(w, template_pid, data, key="replay")
    again = materialize(w, template_pid, data, key="replay")
    assert again["replayed"] and again["output"]["project_id"] == first["output"]["project_id"]
    assert w.store.one("SELECT COUNT(*) AS n FROM projects WHERE origin=?", (f"suite:{first['run_id']}",))["n"] == 1
    with pytest.raises(ServiceError) as err:
        w.invoke("project.from_dataset", {"template_project_id": template_pid, "dataset_id": data["id"]})
    assert err.value.code == "idempotency_key_required"
