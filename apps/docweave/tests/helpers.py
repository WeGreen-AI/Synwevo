"""Helpers the DocWeave tests share.

These used to live in `conftest.py` and be imported as `from conftest import ...`, which works
only because pytest puts the test directory on `sys.path`. With three test suites in one
repository that is a name collision waiting to happen — and it happened: the platform root has a
`conftest.py` too. A named module says which helpers these are.
"""
from __future__ import annotations

import io
import time
from pathlib import Path


class Api:
    """Small helper around the HTTP API used by the acceptance tests."""

    def __init__(self, c):
        self.c = c

    def ok(self, r):
        assert r.status_code < 400, (r.status_code, r.text[:800])
        return r.json()

    def ok_bytes(self, r):
        assert r.status_code < 400, (r.status_code, r.text[:300])
        return r.content

    def project(self, name="测试项目"):
        return self.ok(self.c.post("/api/projects", json={"name": name}))["id"]

    def upload(self, pid, *paths):
        files = [("files", (Path(p).name, Path(p).read_bytes())) for p in paths]
        out = self.ok(self.c.post(f"/api/projects/{pid}/sources", files=files))
        self.wait_parsed(pid)
        return out

    def upload_bytes(self, pid, name, data):
        out = self.ok(self.c.post(f"/api/projects/{pid}/sources", files=[("files", (name, data))]))
        self.wait_parsed(pid)
        return out

    def wait_parsed(self, pid, timeout=60):
        t0 = time.time()
        while time.time() - t0 < timeout:
            srcs = self.ok(self.c.get(f"/api/projects/{pid}/sources"))
            if all(s["status"] != "parsing" for s in srcs):
                return srcs
            time.sleep(0.1)
        raise AssertionError("parse timeout")

    def template(self, pid, path, name=None):
        data = {"project_id": pid}
        if name:
            data["name"] = name
        return self.ok(self.c.post("/api/templates/upload", files={"file": (Path(path).name, Path(path).read_bytes())}, data=data))

    def view(self, tid):
        return self.ok(self.c.get(f"/api/templates/{tid}/view"))

    def slot(self, tid, **req):
        return self.ok(self.c.post(f"/api/templates/{tid}/slots", json=req))["created_slot"]

    def text_slot(self, tid, needle, name, ctx=None, part_kind="body", **extra):
        v = self.view(tid)
        for part in v["doc"]["parts"]:
            if part_kind and part["kind"] != part_kind:
                continue
            for b in _walk(part["blocks"]):
                if (ctx or needle) in b["text"]:
                    s = b["text"].index(needle)
                    return self.slot(tid, kind="text", part=b["part"], path=b["path"], start=s, end=s + len(needle), name=name, **extra)
        raise AssertionError(f"text not found: {needle}")

    def rule(self, pid, rule):
        return self.ok(self.c.patch(f"/api/projects/{pid}", json={"record_rule": rule}))

    def maps(self, pid, maps):
        return self.ok(self.c.put(f"/api/projects/{pid}/mappings", json={"mappings": maps}))

    def job(self, pid, timeout=180, **opts):
        r = self.c.post(f"/api/projects/{pid}/jobs", json=opts)
        if r.status_code >= 400:
            return r
        j = r.json()
        t0 = time.time()
        while time.time() - t0 < timeout:
            j = self.ok(self.c.get(f"/api/jobs/{j['id']}"))
            if j["status"] not in ("queued", "running", "cancelling"):
                return j
            time.sleep(0.1)
        raise AssertionError("job timeout")

    def items(self, jid):
        return self.ok(self.c.get(f"/api/jobs/{jid}/items", params={"limit": 5000}))

    def artifact(self, aid):
        r = self.c.get(f"/api/artifacts/{aid}")
        assert r.status_code == 200
        return r.content

    def outputs(self, jid):
        """{file_base: bytes} for all successful items (first artifact)."""
        out = {}
        for it in self.items(jid):
            for a in it["artifacts"]:
                out[a["filename"]] = self.artifact(a["id"])
        return out


def _walk(blocks):
    for b in blocks:
        if b["t"] == "p":
            yield b
        else:
            for row in b["rows"]:
                for c in row["cells"]:
                    yield from _walk(c["blocks"])


def docx_text(data: bytes) -> str:
    import docx
    d = docx.Document(io.BytesIO(data))
    parts = [p.text for p in d.paragraphs]
    for t in d.tables:
        for row in t.rows:
            parts.append(" | ".join(c.text for c in row.cells))
    return "\n".join(parts)
