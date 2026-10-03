"""Client for the business applications' operations API (Forms, Tables, DataPrep, Extract, Approvals).

Independent of the Docs API and its audience. Every call acts for the person who authorised the
client, in the one workspace they chose, with their current role there — the scopes the client
holds are necessary, never sufficient. The personal Agent is not part of this API.

    from synwevo_docs_sdk import SuiteClient, UserToken

    token = UserToken(refresh_token=stored["refresh_token"], client_id="crm-sync",
                      client_secret=secret, token_url="https://accounts.synwevo.com/oidc/token",
                      resource="urn:synwevo:platform:suite", on_rotate=store.save)
    with SuiteClient(credentials=token) as suite:
        table = suite.invoke("tables.table.list")["output"]
"""
from __future__ import annotations

import mimetypes
import os
import re
import time
from pathlib import Path
from typing import Any, Callable, Literal, TypedDict

from .auth import UserToken, read_secret
from .client import Client, _q, __version__
from .errors import ValidationError, WaitTimeoutError

SUITE_BASE_URL = "https://workplace.synwevo.com"
SUITE_TOKEN_URL = "https://accounts.synwevo.com/oidc/token"
SUITE_RESOURCE = "urn:synwevo:platform:suite"
SUITE_API = "/api/suite/v1"

RunStatus = Literal["queued", "running", "waiting_input", "waiting_approval", "succeeded", "partial",
                    "failed", "cancelled", "unknown"]
TERMINAL = frozenset({"succeeded", "partial", "failed", "cancelled"})
_OPERATION = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$")


class SuiteWhoami(TypedDict):
    kind: Literal["delegated"]
    client_id: str
    user_id: str
    workspace_id: str
    role: Literal["owner", "admin", "editor", "viewer"]
    scopes: list[str]
    resource: str
    apps: list[str]
    expires_at: float | None


class SuiteOperation(TypedDict):
    id: str
    app: str
    version: int
    title: dict[str, str]
    summary: str
    path: str
    scope: str
    also_requires: list[str]
    need: Literal["read", "write", "manage"]
    side_effect: Literal["none", "write", "external"]
    mode: Literal["sync", "async"]
    cancellable: bool
    idempotency: Literal["required", "supported", "none"]
    events: list[str]
    object_kind: str | None
    input_schema: dict
    output_schema: dict | None
    granted: bool


class SuiteOperations(TypedDict):
    workspace_id: str
    operations: list[SuiteOperation]
    apps: list[dict]


class SuiteResult(TypedDict):
    """One call's answer. ``status`` other than succeeded/partial means it was accepted, not done."""
    run_id: str
    operation: str
    status: RunStatus
    output: Any
    replayed: bool
    workspace_id: str


class SuiteRun(TypedDict):
    id: str
    operation: str
    app: str
    workspace_id: str
    status: RunStatus
    via: Literal["api"]
    input: None
    output: Any
    error: dict | None
    object: dict | None
    created_at: float
    finished_at: float | None
    snapshot_hidden: bool


def _operation(name: str) -> str:
    if not isinstance(name, str) or not _OPERATION.match(name) or len(name) > 100:
        raise ValidationError("operation must be an operation id such as 'forms.form.list'", "operation")
    return name


def _key(given: str | None) -> str | None:
    if given is None:
        return None
    if not isinstance(given, str) or not 1 <= len(given) <= 128 or any(not 0x21 <= ord(c) <= 0x7E for c in given):
        raise ValidationError("idempotency_key must be 1–128 visible ASCII characters (a UUID is fine)", "Idempotency-Key")
    return given


def _read(file: bytes | str | os.PathLike, filename: str | None) -> tuple[str, bytes, str]:
    """(name, bytes, content type) of what is to be uploaded. The service's own limits are 8–12 MB,
    so the file is read whole; a path gives the name unless one is passed."""
    if isinstance(file, (bytes, bytearray)):
        if not filename:
            raise ValidationError("filename is required when uploading bytes", "filename")
        data = bytes(file)
    else:
        path = Path(file)
        data, filename = path.read_bytes(), filename or path.name
    return filename, data, mimetypes.guess_type(filename)[0] or "application/octet-stream"


class SuiteClient:
    """Operations and the files that go with them. Explicit credentials or ``from_env``; the
    workplace origin by default.

    Every `invoke` carries an idempotency key (yours, or one generated for that call) and is
    retried on network failures and 5xx with that same key: a change runs once however many
    attempts it took, and a read — for which the server ignores the key — is simply read again.
    To make a call repeatable across processes, pass your own ``idempotency_key``.
    """

    def __init__(self, base_url: str = SUITE_BASE_URL, credentials: UserToken | None = None, **options):
        if credentials is None:
            raise ValidationError("SuiteClient requires a UserToken (a person's delegation) or from_env()", "credentials")
        options.setdefault("user_agent", f"synwevo-suite-sdk-python/{__version__}")
        self._sleep: Callable[[float], None] = options.get("sleep", time.sleep)
        self._clock: Callable[[], float] = options.get("clock", time.monotonic)
        self._client = Client(base_url, credentials, **options)
        self._workspace: str | None = None

    @classmethod
    def from_env(cls, environ: dict | None = None, *, on_rotate: Callable[[dict], None] | None = None,
                 **options) -> "SuiteClient":
        """Only SYNWEVO_SUITE_* variables are read; Docs and directory credentials are ignored.

        ``SYNWEVO_SUITE_ACCESS_TOKEN[_FILE]`` and/or ``SYNWEVO_SUITE_REFRESH_TOKEN[_FILE]``; with a
        refresh token also ``SYNWEVO_SUITE_CLIENT_ID``, optionally
        ``SYNWEVO_SUITE_CLIENT_SECRET[_FILE]`` and ``SYNWEVO_SUITE_TOKEN_URL``. A refresh token
        rotates on use: without ``on_rotate`` the successor lives only in this process, and the
        value in the environment is spent after the first refresh. For that reason this is for a
        single process — a second one started from the same variable would present a spent token
        and end the grant for both (see `UserToken`).
        """
        env = os.environ if environ is None else environ

        def secret(name: str, what: str) -> str | None:
            if env.get(name) or env.get(name + "_FILE"):
                return read_secret(env.get(name), env.get(name + "_FILE"), what=what)
            return None

        access = secret("SYNWEVO_SUITE_ACCESS_TOKEN", "suite access token")
        refresh = secret("SYNWEVO_SUITE_REFRESH_TOKEN", "suite refresh token")
        if not access and not refresh:
            raise ValidationError("Set SYNWEVO_SUITE_ACCESS_TOKEN or SYNWEVO_SUITE_REFRESH_TOKEN (a person's delegation)",
                                  "SYNWEVO_SUITE_ACCESS_TOKEN")
        credentials = UserToken(
            access, refresh_token=refresh, client_id=env.get("SYNWEVO_SUITE_CLIENT_ID") or None,
            client_secret=secret("SYNWEVO_SUITE_CLIENT_SECRET", "suite client_secret"),
            token_url=(env.get("SYNWEVO_SUITE_TOKEN_URL") or SUITE_TOKEN_URL) if refresh else None,
            resource=SUITE_RESOURCE, on_rotate=on_rotate)
        return cls(env.get("SYNWEVO_SUITE_BASE_URL") or SUITE_BASE_URL, credentials, **options)

    @property
    def base_url(self) -> str:
        return self._client.base_url

    def close(self) -> None:
        self._client.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def whoami(self) -> SuiteWhoami:
        return self._client._get(SUITE_API + "/whoami")

    def operations(self, app: str | None = None) -> SuiteOperations:
        """The contract of every operation the person can use here, with ``granted`` per scope."""
        return self._client._get(SUITE_API + "/operations", {"app": app})

    def invoke(self, operation: str, input: dict | None = None, *, idempotency_key: str | None = None,
               correlation_id: str | None = None) -> SuiteResult:
        """Run one operation. Raises the typed `APIError` for a refusal or a business error;
        ``result.replayed`` says the answer is an earlier call's (same key, same input)."""
        if input is not None and not isinstance(input, dict):
            raise ValidationError("input must be a JSON object", "input")
        body: dict[str, Any] = {"input": input or {}}
        if correlation_id is not None:
            body["correlation_id"] = correlation_id
        key = _key(idempotency_key) or self._client._key(None)
        out = self._client._json("POST", f"{SUITE_API}/ops/{_q(_operation(operation))}", json=body, idempotency_key=key)
        out.replayed = bool(out.get("replayed"))
        return out

    def run(self, run_id: str) -> SuiteRun:
        """A run this client started. Its output is None (``snapshot_hidden``) once what it was
        made from is no longer readable by this credential."""
        return self._client._get(f"{SUITE_API}/runs/{_q(run_id)}")

    def wait(self, run_id: str, *, timeout: float = 300.0, poll: float = 2.0) -> SuiteRun:
        """Poll a run until it settles. A run waiting for a person (an approval, a form) can take
        days: this gives up after ``timeout`` and the run carries on."""
        deadline = self._clock() + timeout
        while True:
            state = self.run(run_id)
            if state["status"] in TERMINAL:
                return state
            if self._clock() >= deadline:
                raise WaitTimeoutError(f"run {run_id} is still {state['status']} after {timeout:g}s", state)
            self._sleep(min(poll, max(0.0, deadline - self._clock())))

    # ------------------------------------------------------------------ files
    # The routes that move bytes. They name the credential's workspace in the query (the service
    # refuses any other), which is read once from `whoami`.
    def _where(self) -> dict:
        if self._workspace is None:
            self._workspace = self.whoami()["workspace_id"]
        return {"workspace_id": self._workspace}

    def _upload(self, path: str, file, filename: str | None, *, idempotency_key: str | None, keyed: bool) -> dict:
        name, data, kind = _read(file, filename)
        key = (_key(idempotency_key) or self._client._key(None)) if keyed else None
        resp = self._client._t.send("POST", SUITE_API + path, params=self._where(),
                                    body=lambda: {"files": {"file": (name, data, kind)}}, idempotency_key=key,
                                    # A keyed upload answers a repeat with the first file. Without a
                                    # key a repeat would stage a second import, so it is not retried.
                                    retry_safe=keyed)
        return resp.json()

    def _download(self, path: str, **query) -> bytes:
        return self._client._t.send("GET", SUITE_API + path, params={**self._where(), **query}).content

    def extract_upload(self, file: bytes | str | os.PathLike, filename: str | None = None, *,
                       idempotency_key: str | None = None) -> dict:
        """Upload a PDF, image or CSV/TSV for `extract.task.create` (scope ``extract.write``). The
        same upload check as the page; the same key and bytes answer with the same file."""
        return self._upload("/extract/files", file, filename, idempotency_key=idempotency_key, keyed=True)

    def extract_download(self, file_id: str) -> bytes:
        """The uploaded original (``extract.read`` and the person's export right)."""
        return self._download(f"/extract/files/{_q(file_id)}")

    def extract_export(self, task_id: str, revision: int) -> dict:
        """A confirmed result revision (``extract.read`` and the person's export right)."""
        import json
        return json.loads(self._download(f"/extract/tasks/{_q(task_id)}/export", revision=revision))

    def dataprep_upload(self, file: bytes | str | os.PathLike, filename: str | None = None, *,
                        idempotency_key: str | None = None) -> dict:
        """Upload a CSV or XLSX for `dataprep.dataset.import` (scope ``dataprep.write``)."""
        return self._upload("/dataprep/files", file, filename, idempotency_key=idempotency_key, keyed=True)

    def dataprep_export(self, dataset_id: str, format: Literal["csv", "xlsx", "json"] = "csv") -> bytes:
        """A dataset as a file (``dataprep.read``, the person's export right, and the read scope of
        whatever application the dataset came from)."""
        return self._download(f"/dataprep/datasets/{_q(dataset_id)}/export", format=format)

    def dataprep_run_issues(self, run_id: str) -> list:
        """Every issue of a run (``dataprep.read`` and the person's export right)."""
        import json
        return json.loads(self._download(f"/dataprep/runs/{_q(run_id)}/issues"))

    def tables_import_upload(self, table_id: str, file: bytes | str | os.PathLike, filename: str | None = None) -> dict:
        """Stage a CSV or XLSX for `tables.import.apply` (scope ``tables.write``). Not retried: a
        repeat would stage a second import."""
        return self._upload(f"/tables/{_q(table_id)}/imports", file, filename, idempotency_key=None, keyed=False)

    def tables_export_download(self, export_id: str) -> bytes:
        """The file of an export made with `tables.export.create` (``tables.read`` and the person's
        export right): the records the person may read at the moment of the download."""
        return self._download(f"/tables/exports/{_q(export_id)}")

    def forms_file(self, file_id: str) -> bytes:
        """An attachment of a submission (``forms.read`` and the person's export right)."""
        return self._download(f"/forms/files/{_q(file_id)}")
