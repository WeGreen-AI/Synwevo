"""`Client`: Python methods for the Synwevo Docs machine API (`/api/v1`).

Method names follow the resources (projects, files, templates, uploads, jobs, artifacts,
webhooks, events). JSON methods return the server's JSON as an `ApiDict` — a plain ``dict``
that also says which request produced it (``request_id``) and whether it was an idempotent
replay (``replayed``). Lists come two ways: ``list_*`` returns one page as the server sent it,
``iter_*`` follows ``next_cursor`` / ``next_offset`` to the end.
Downloads return local file metadata. See operation-coverage.json for the exact route coverage.

Creating calls (project, source file, template, fields, upload, job, retry, signed links) send an
``Idempotency-Key``: pass your own ``idempotency_key=`` to make a call safe to repeat across
process restarts, or let the SDK generate one per call so its own retries are safe.
"""
from __future__ import annotations

import mimetypes
import hashlib
import math
import os
import platform
import random
import time
import urllib.parse
import zipfile
from collections import Counter
from pathlib import Path
from typing import Any, Callable, Iterator, cast

import httpx

from . import validation as V
from ._files import AtomicWriter, iter_slice, safe_filename, sha256_file
from ._http import ApiDict, Transport, new_idempotency_key, wrap
from .auth import ApiKey, ClientCredentials, credentials_from_env
from .source_types import (CSVOptions, CorrectionValue, SourceStructure, SourceView, SourceBlocks,
                           SourceSearch, SourceWords, SourceFile, SourceRecords, SourceRecord, SourceBlock, SourceWord)
from .errors import (ConflictError, IntegrityError, RangeNotSatisfiableError, RateLimitError, ServerError,
                     SynwevoError, TransportError, ValidationError, WaitTimeoutError, parse_retry_after)

__version__ = "0.1.0"

MiB = 1024 * 1024
API = "/api/v1"
ARCHIVE_MANIFEST = "生成清单.csv"          # the report the server adds to every job archive
_UNSET: Any = object()


def _q(value: str) -> str:
    """One path segment, percent-encoded: ids come from responses, but a path must never be
    steered by what is inside one."""
    if not isinstance(value, str) or not value:
        raise ValidationError("资源 id 必须是非空字符串")
    return urllib.parse.quote(value, safe="")


def _filename_from_disposition(value: str | None) -> str | None:
    if not value:
        return None
    for part in value.split(";"):
        k, _, v = part.strip().partition("=")
        if k.lower() == "filename*" and "''" in v:
            return urllib.parse.unquote(v.split("''", 1)[1])
    for part in value.split(";"):
        k, _, v = part.strip().partition("=")
        if k.lower() == "filename":
            return v.strip('"')
    return None


class Client:
    """A client for one Synwevo Docs deployment and one credential.

    >>> from synwevo_docs_sdk import Client, ApiKey
    >>> with Client("https://docs.example.com", ApiKey(open("key.txt").read())) as c:
    ...     print(c.whoami()["tenant"])
    """

    def __init__(self, base_url: str, credentials: ApiKey | ClientCredentials, *, timeout: float = 30.0,
                 max_retries: int = 4, user_agent: str | None = None, validate: bool = True,
                 resumable_threshold: int = 8 * MiB, chunk_size: int = 4 * MiB, problem_json: bool = False,
                 http: httpx.Client | None = None, backoff_base: float = 0.5, backoff_max: float = 30.0,
                 max_retry_wait: float = 120.0, sleep: Callable[[float], None] = time.sleep,
                 rand: Callable[[], float] = random.random, clock: Callable[[], float] = time.monotonic):
        V.positive_int(chunk_size, "chunk_size")
        ua = user_agent or (f"synwevo-docs-sdk-python/{__version__} "
                            f"(python {platform.python_version()}; httpx {httpx.__version__})")
        self._t = Transport(base_url, credentials, timeout=timeout, max_retries=max_retries, user_agent=ua,
                            http=http, backoff_base=backoff_base, backoff_max=backoff_max,
                            max_retry_wait=max_retry_wait, sleep=sleep, rand=rand, problem_json=problem_json)
        self.validate = validate
        self.resumable_threshold = resumable_threshold
        self.chunk_size = chunk_size
        self.max_retries = max(0, int(max_retries))
        self._sleep = sleep
        self._clock = clock

    @classmethod
    def from_env(cls, environ: dict | None = None, **kwargs) -> "Client":
        """``SYNWEVO_BASE_URL`` plus the credentials `credentials_from_env` finds."""
        env = os.environ if environ is None else environ
        base = env.get("SYNWEVO_BASE_URL")
        if not base:
            raise SynwevoError(None, "missing_base_url", "请设置 SYNWEVO_BASE_URL（例如 https://docs.example.com）")
        return cls(base, credentials_from_env(env), **kwargs)

    @property
    def base_url(self) -> str:
        return self._t.base_url

    def close(self) -> None:
        self._t.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    # ================================================================== plumbing
    def _json(self, method: str, path: str, *, params=None, json=None, body=None, headers=None,
              idempotency_key: str | None = None, retry_safe: bool | None = None, auth: bool = True) -> Any:
        resp = self._t.send(method, path, params=params, json=json, body=body, headers=headers,
                            idempotency_key=idempotency_key, retry_safe=retry_safe, auth=auth)
        if not resp.content:
            return wrap({}, resp)
        try:
            data = resp.json()
        except ValueError:
            raise ServerError(resp.status_code, "invalid_json", f"{method} {path} 的响应不是 JSON",
                              resp.headers.get("x-request-id")) from None
        return wrap(data, resp)

    def _get(self, path: str, params=None) -> Any:
        return self._json("GET", path, params=params)

    def _key(self, given: str | None) -> str:
        if given is None:
            return new_idempotency_key()
        if not isinstance(given, str) or not given or len(given) > 255 or any(not 0x20 <= ord(c) <= 0x7E for c in given):
            raise ValidationError("idempotency_key 必须是 1–255 个可打印 ASCII 字符（建议 UUID）", "Idempotency-Key")
        return given

    def _check(self, fn, *args):
        return fn(*args) if self.validate else args[0]

    def _iterate(self, path: str, params: dict | None = None, page_size: int | None = None) -> Iterator[dict]:
        """Every item of a paged list, following next_cursor (preferred) or next_offset."""
        params = {k: v for k, v in (params or {}).items() if v is not None}
        if page_size is not None:
            params["limit"] = page_size
        seen: set = set()
        while True:
            page = self._get(path, params)
            yield from page.get("items") or []
            if page.get("has_more") is False:
                return
            cur = page.get("next_cursor")
            if cur:
                if ("cursor", cur) in seen:
                    raise ServerError(None, "pagination_loop", "Server repeated a pagination cursor")
                seen.add(("cursor", cur))
                params = {**{k: v for k, v in params.items() if k not in ("offset", "cursor")}, "cursor": cur}
                continue
            nxt = page.get("next_offset")
            if nxt is not None:
                if ("offset", nxt) in seen or nxt == params.get("offset", 0):
                    raise ServerError(None, "pagination_loop", "Server repeated a pagination offset")
                seen.add(("offset", nxt))
                params = {**{k: v for k, v in params.items() if k != "cursor"}, "offset": nxt}
                continue
            return

    @staticmethod
    def _page_params(limit=None, cursor=None, offset=None, **extra) -> dict:
        return {"limit": limit, "cursor": cursor, "offset": offset, **extra}

    # ================================================================== system
    def health(self) -> dict:
        """``GET /healthz`` (no credential)."""
        return self._json("GET", "/healthz", auth=False)

    def readiness(self) -> dict:
        """GET /readyz without credentials; an unready service raises its API error."""
        return self._json("GET", "/readyz", auth=False)

    def capabilities(self) -> dict:
        return self._get(f"{API}/capabilities")

    def whoami(self) -> dict:
        return self._get(f"{API}/whoami")

    def usage(self) -> dict:
        return self._get(f"{API}/usage")

    def credential_self(self) -> dict:
        return self._get(f"{API}/credentials/self")

    def rotate_own_key(self, grace_minutes: float | None = None) -> dict:
        """Replace the calling ``dw_`` key; the new key is in the answer, once. Not retried:
        a second rotation would retire the key the first one just issued."""
        body = {} if grace_minutes is None else {"grace_minutes": grace_minutes}
        return self._json("POST", f"{API}/credentials/self/rotate", json=body, retry_safe=False)

    def revoke_own_credential(self) -> dict:
        return self._json("POST", f"{API}/credentials/self/revoke", json={}, retry_safe=False)

    # ================================================================== projects
    def create_project(self, name: str | None = None, *, idempotency_key: str | None = None) -> dict:
        body = {} if name is None else {"name": name}
        return self._json("POST", f"{API}/projects", json=body, idempotency_key=self._key(idempotency_key))

    def list_projects(self, *, limit: int | None = None, cursor: str | None = None, offset: int | None = None) -> dict:
        return self._get(f"{API}/projects", self._page_params(limit, cursor, offset))

    def iter_projects(self, page_size: int | None = None) -> Iterator[dict]:
        return self._iterate(f"{API}/projects", page_size=page_size)

    def get_project(self, project_id: str) -> dict:
        return self._get(f"{API}/projects/{_q(project_id)}")

    # ------------------------------------------------------------------ copy requests (delegated tokens only)
    def copy_targets(self) -> dict:
        """Organization workspaces the person behind this delegated token can copy a project into."""
        return self._get(f"{API}/copy-targets")

    def copy_manifest(self, project_id: str, *, sources: bool = False, corrections: bool = False) -> dict:
        """What a copy request would carry (manifest and digest); creates nothing."""
        return self._get(f"{API}/projects/{_q(project_id)}/copy-manifest",
                         params={"sources": str(bool(sources)).lower(), "corrections": str(bool(corrections)).lower()})

    def request_copy(self, project_id: str, target_workspace_id: str, *, name: str | None = None, purpose: str | None = None,
                     sources: bool = False, corrections: bool = False, idempotency_key: str | None = None) -> dict:
        """Ask for a copy of a personal project into an organization workspace. The target's approver
        decides in the browser; follow the request with get_copy_request or copy.* events. Only a
        delegated token (the project owner's authorization) may ask. Retried with one idempotency key."""
        body: dict = {"target_workspace_id": target_workspace_id, "scope": {"sources": bool(sources), "corrections": bool(corrections)}}
        if name is not None:
            body["name"] = name
        if purpose is not None:
            body["purpose"] = purpose
        return self._json("POST", f"{API}/projects/{_q(project_id)}/copy-requests", json=body,
                          idempotency_key=self._key(idempotency_key))

    def list_copy_requests(self, project_id: str, *, status: str | None = None, limit: int = 50) -> dict:
        """Newest requests first, at most ``limit`` (1–100); this endpoint has no cursor."""
        params: dict = {"limit": limit}
        if status is not None:
            params["status"] = status
        return self._get(f"{API}/projects/{_q(project_id)}/copy-requests", params=params)

    def get_copy_request(self, copy_id: str) -> dict:
        return self._get(f"{API}/copy-requests/{_q(copy_id)}")

    def withdraw_copy_request(self, copy_id: str) -> dict:
        """Withdraw a pending (or approved, not yet run) request. Not retried automatically: a
        repeat of a withdrawal that succeeded answers 409 not_withdrawable."""
        return self._json("POST", f"{API}/copy-requests/{_q(copy_id)}/withdraw", retry_safe=False)

    def update_project(self, project_id: str, *, base_settings_rev: int, name: str | None = None,
                       archived: bool | None = None, output: dict | None = None) -> dict:
        """Rename, archive/unarchive or change output defaults. ``base_settings_rev`` is the project's
        ``settings_rev`` you read; someone else's edit since is 409 stale_project. Not retried."""
        body: dict = {"base_settings_rev": base_settings_rev}
        for key, value in (("name", name), ("archived", archived), ("output", output)):
            if value is not None:
                body[key] = value
        if self.validate and len(body) == 1:
            raise ValidationError("give name, archived or output to change", "body")
        return self._json("PATCH", f"{API}/projects/{_q(project_id)}", json=body, retry_safe=False)

    def delete_project(self, project_id: str) -> dict:
        return self._json("DELETE", f"{API}/projects/{_q(project_id)}", retry_safe=True)

    def set_project_template(self, project_id: str, template_id: str, version: int | None = None) -> dict:
        """Bind a template; ``version`` pins that template version (None follows the latest)."""
        if self.validate:
            V.positive_int(version, "version")
        return self._json("PUT", f"{API}/projects/{_q(project_id)}/template",
                          json={"template_id": template_id, "version": version}, retry_safe=False)

    def put_rule(self, project_id: str, rule: dict) -> dict:
        return self._json("PUT", f"{API}/projects/{_q(project_id)}/rule", json={"rule": rule}, retry_safe=False)

    def get_mappings(self, project_id: str) -> dict:
        return self._get(f"{API}/projects/{_q(project_id)}/mappings")

    def put_mappings(self, project_id: str, mappings: list, *, base_rev: int | None = None, note: str | None = None) -> dict:
        body: dict = {"mappings": mappings}
        if base_rev is not None:
            body["base_rev"] = base_rev
        if note:
            body["note"] = note
        # Each save creates a revision. A lost response must not create another revision.
        return self._json("PUT", f"{API}/projects/{_q(project_id)}/mappings", json=body, retry_safe=False)

    def get_definition(self, project_id: str) -> dict:
        return self._get(f"{API}/projects/{_q(project_id)}/definition")

    def put_definition(self, project_id: str, definition: dict, *, base_rev: int | None = None) -> dict:
        """Import a ``docweave.project`` definition (all or nothing). Mappings may name their
        target by ``field`` (the field name); the server resolves it."""
        return self._json("PUT", f"{API}/projects/{_q(project_id)}/definition", json=definition,
                          params={"base_rev": base_rev}, retry_safe=False)

    def preflight(self, project_id: str, *, formats: list[str] | None = None, naming: str | None = None,
                  simulate: bool | int = False) -> dict:
        """What a job would do: samples, valid/invalid, expected outputs, issues. With
        ``simulate`` the server test-renders (all valid samples, or the first N) and discards it."""
        body: dict = {}
        if formats is not None:
            body["formats"] = self._check(V.formats, formats)
        if naming is not None:
            body["naming"] = naming
        sim = self._check(V.simulate, simulate)
        if sim:
            body["simulate"] = sim
        # Preflight writes nothing (scope read), so repeating it is safe.
        return self._json("POST", f"{API}/projects/{_q(project_id)}/preflight", json=body, retry_safe=True)

    def list_preflight_issues(self, project_id: str, *, level: str | None = None, limit: int | None = None,
                              offset: int | None = None, formats: list[str] | None = None,
                              naming: str | None = None) -> dict:
        if self.validate:
            V.one_of(level, V.ISSUE_LEVELS, "level")
        return self._get(f"{API}/projects/{_q(project_id)}/preflight/issues",
                         {"level": level, "limit": limit, "offset": offset,
                          "formats": ",".join(formats) if formats else None, "naming": naming})

    def download_preview(self, project_id: str, dest: str | os.PathLike, *, index: int | None = None,
                         key: str | None = None, signature: str | None = None) -> dict:
        """Render one sample's native output without creating a job or artifact.

        Select by zero-based index OR record key (default first sample). Pass the preflight
        signature to pin inputs. Streams atomically, verifies the server SHA-256, and returns
        project_signature, template_version (header string), and decoded record_key metadata.
        Requires write and export permission; the service decides the native output format.
        """
        if self.validate:
            if index is not None and (type(index) is not int or index < 0):
                raise ValidationError("index must be a non-negative integer", "index")
            if index is not None and key is not None:
                raise ValidationError("Use index or key, not both", "key")
            for field, value, maximum in (("key", key, 512), ("signature", signature, 64)):
                if value is not None and (not isinstance(value, str) or not 1 <= len(value) <= maximum):
                    raise ValidationError(f"{field} must contain 1–{maximum} characters", field)
        body = {k: v for k, v in {"index": index, "key": key, "signature": signature}.items() if v is not None}
        out = self._download(f"{API}/projects/{_q(project_id)}/preview", dest, method="POST", json=body,
                             fallback_name="preview.bin", metadata_headers={
                                 "project_signature": "x-project-signature", "template_version": "x-template-version",
                                 "record_key": "x-record-key"})
        if out.get("record_key") is not None:
            out["record_key"] = urllib.parse.unquote(out["record_key"])
        return out

    def iter_preflight_issues(self, project_id: str, *, level: str | None = None, page_size: int | None = None,
                              formats: list[str] | None = None, naming: str | None = None) -> Iterator[dict]:
        """Every located issue: ``seq``/``label``/``where`` say which sample and where in the
        source, ``slot`` which field, ``code``/``message`` what is wrong."""
        if self.validate:
            V.one_of(level, V.ISSUE_LEVELS, "level")
        return self._iterate(f"{API}/projects/{_q(project_id)}/preflight/issues",
                             {"level": level, "formats": ",".join(formats) if formats else None, "naming": naming},
                             page_size)

    # ================================================================== source files
    def upload_source(self, project_id: str, path: str | os.PathLike, *, filename: str | None = None,
                      resumable: bool | None = None, chunk_size: int | None = None, upload_id: str | None = None,
                      idempotency_key: str | None = None, wait: bool = True, wait_timeout: float = 120.0,
                      on_progress: Callable[[int, int], None] | None = None) -> dict:
        """Add a source file to a project and (by default) wait until the server has parsed it.

        Files up to ``resumable_threshold`` bytes go as one multipart request with their SHA-256;
        larger ones (or ``resumable=True``) through the resumable upload API, in chunks read from
        disk. ``upload_id`` resumes an earlier, interrupted resumable upload of the same file.
        """
        path = Path(path)
        name = filename or path.name
        use_resumable = resumable if resumable is not None else (upload_id is not None or path.stat().st_size > self.resumable_threshold)
        key = self._key(idempotency_key)
        route = f"{API}/projects/{_q(project_id)}/files"
        if use_resumable:
            up = self.resumable_upload(path, filename=name, purpose="source", chunk_size=chunk_size,
                                       upload_id=upload_id, on_progress=on_progress,
                                       idempotency_key=hashlib.sha256(("source:" + key).encode()).hexdigest())
            out = self._json("POST", route, json={"upload_id": up["id"]}, idempotency_key=key)
        else:
            out = self._multipart(route, path, name, key, on_progress=on_progress)
        if wait and out.get("status") in ("parsing", "scanning", "scan_failed"):
            done = self.wait_for_file(out["id"], timeout=wait_timeout)
            return wrap_like(done, out)
        return out

    def _multipart(self, route: str, path: Path, name: str, key: str, params: dict | None = None,
                   on_progress: Callable[[int, int], None] | None = None) -> dict:
        size = path.stat().st_size
        digest = sha256_file(path)
        mime = mimetypes.guess_type(name)[0] or "application/octet-stream"

        def body():
            # A fresh handle per attempt: a failed attempt has consumed the previous one.
            return {"files": {"file": (name, open(path, "rb"), mime)}}

        out = self._json("POST", route, body=body, params=params, headers={"X-Content-SHA256": digest},
                         idempotency_key=key)
        if on_progress:
            on_progress(size, size)
        return out

    def list_files(self, project_id: str, *, limit: int | None = None, cursor: str | None = None,
                   offset: int | None = None) -> dict:
        return self._get(f"{API}/projects/{_q(project_id)}/files", self._page_params(limit, cursor, offset))

    def iter_files(self, project_id: str, page_size: int | None = None) -> Iterator[dict]:
        return self._iterate(f"{API}/projects/{_q(project_id)}/files", page_size=page_size)

    def get_file(self, file_id: str) -> dict:
        return self._get(f"{API}/files/{_q(file_id)}")

    def delete_file(self, file_id: str) -> dict:
        return self._json("DELETE", f"{API}/files/{_q(file_id)}", retry_safe=True)

    def wait_for_file(self, file_id: str, *, timeout: float = 120.0, poll_interval: float = 0.5) -> dict:
        """Poll until the server has accepted and parsed the file: the status is no longer
        ``scanning`` (the malware check), ``scan_failed`` (the check could not run yet — the server
        keeps the file quarantined and retries it, so re-uploading would only queue the same bytes
        again) or ``parsing``. ``rejected`` (a signature matched) and ``error`` are returned as
        they are; ``error``/``scan`` say why. A ``WaitTimeoutError`` carries the last file record."""
        _check_wait(timeout, poll_interval)
        deadline = self._clock() + timeout
        interval = poll_interval
        while True:
            f = self.get_file(file_id)
            if f.get("status") not in ("parsing", "scanning", "scan_failed"):
                return f
            if self._clock() >= deadline:
                raise WaitTimeoutError(f"来源文件 {file_id} 在 {timeout:g} 秒内没有解析完成", f)
            self._sleep(min(interval, deadline - self._clock()))
            interval = min(interval * 1.5, 5.0)

    # ================================================================== source selection
    def _source_params(self, *, offset=0, limit=50, block=None, max_offset=1000000, **extra):
        if self.validate:
            for field, value, low, high in (("offset", offset, 0, max_offset), ("limit", limit, 1, 200),
                                            ("block", block, 0, 4999)):
                if value is not None and (type(value) is not int or not low <= value <= high):
                    raise ValidationError(f"{field} must be an integer in {low}–{high}", field)
        return {"offset": offset, "limit": limit, "block": block, **extra}

    def get_source_structure(self, file_id: str) -> SourceStructure:
        return cast(SourceStructure, self._get(f"{API}/files/{_q(file_id)}/structure"))

    def get_source_view(self, file_id: str, *, sheet: str | None = None, offset: int = 0,
                        limit: int = 200, block: int | None = None) -> SourceView:
        """Native viewer payload; offset/limit window grid rows only."""
        if self.validate and sheet is not None and (not isinstance(sheet, str) or not 1 <= len(sheet) <= 255):
            raise ValidationError("sheet must contain 1–255 characters", "sheet")
        return cast(SourceView, self._get(f"{API}/files/{_q(file_id)}/view",
                    self._source_params(offset=offset, limit=limit, block=block, sheet=sheet)))

    def list_source_blocks(self, file_id: str, *, offset: int = 0, limit: int = 50) -> SourceBlocks:
        return cast(SourceBlocks, self._get(f"{API}/files/{_q(file_id)}/blocks",
                    self._source_params(offset=offset, limit=limit)))

    def iter_source_blocks(self, file_id: str, *, page_size: int = 50) -> Iterator[SourceBlock]:
        return cast(Iterator[SourceBlock], self._iterate(f"{API}/files/{_q(file_id)}/blocks",
                    self._source_params(limit=page_size)))

    def search_source(self, file_id: str, q: str, *, offset: int = 0, limit: int = 50,
                      block: int | None = None) -> SourceSearch:
        """One literal-search page. Inspect truncated; a bounded search may stop early."""
        if self.validate and (not isinstance(q, str) or not q.strip() or len(q) > 256):
            raise ValidationError("q must contain 1–256 characters and not be blank", "q")
        return cast(SourceSearch, self._get(f"{API}/files/{_q(file_id)}/search",
                    self._source_params(offset=offset, limit=limit, block=block, max_offset=10000, q=q)))

    def iter_source_search_pages(self, file_id: str, q: str, *, block: int | None = None,
                                 page_size: int = 50) -> Iterator[SourceSearch]:
        """Follow reachable search pages while preserving the final truncated flag."""
        offset = 0
        while True:
            page = self.search_source(file_id, q, offset=offset, limit=page_size, block=block)
            yield page
            nxt = page["next_offset"]
            if page["truncated"] or nxt is None:
                return
            if nxt <= offset:
                raise ServerError(None, "pagination_loop", "Server repeated a search offset")
            offset = nxt

    def list_source_page_words(self, file_id: str, page: int, *, offset: int = 0, limit: int = 200,
                               block: int | None = None) -> SourceWords:
        """Read existing text-layer/stored OCR words; never execute OCR."""
        if self.validate and (type(page) is not int or not 0 <= page <= 1000000):
            raise ValidationError("page must be an integer in 0–1000000", "page")
        return cast(SourceWords, self._get(f"{API}/files/{_q(file_id)}/words/{_q(str(page))}",
                    self._source_params(offset=offset, limit=limit, block=block)))

    def iter_source_page_words(self, file_id: str, page: int, *, block: int | None = None,
                               page_size: int = 200) -> Iterator[SourceWord]:
        offset = 0
        while True:
            result = self.list_source_page_words(file_id, page, offset=offset, limit=page_size, block=block)
            yield from result["items"]
            nxt = result["next_offset"]
            if nxt is None:
                return
            if nxt <= offset:
                raise ServerError(None, "pagination_loop", "Server repeated a words offset")
            offset = nxt

    def update_source_options(self, file_id: str, csv: CSVOptions) -> SourceFile:
        """Replace CSV options and synchronously reparse. Inspect status/error; no retry."""
        if self.validate:
            allowed = {"encoding": ("auto", "utf-8", "utf-8-sig", "gb18030", "gbk", "big5", "utf-16", "shift_jis", "latin-1"),
                       "delimiter": ("auto", ",", "\t", "\\t", ";", "|"), "quotechar": ('"', "'")}
            if not isinstance(csv, dict) or set(csv) - set(allowed):
                raise ValidationError("csv must be an object containing encoding, delimiter or quotechar", "csv")
            for key, value in csv.items():
                if value not in allowed[key]:
                    raise ValidationError(f"Unsupported CSV {key}", "csv." + key)
        return cast(SourceFile, self._json("PUT", f"{API}/files/{_q(file_id)}/options", json={"csv": csv}, retry_safe=False))

    def update_source_correction(self, file_id: str, mapping_id: str, value: str | None, *,
                                 confirmed: bool = True) -> dict[str, CorrectionValue]:
        """Set a current mapping's correction; value=None clears it. Not retried."""
        if self.validate:
            if value is not None and (not isinstance(value, str) or len(value) > 20000):
                raise ValidationError("value must be null or a string of at most 20000 characters", "value")
            if type(confirmed) is not bool:
                raise ValidationError("confirmed must be a boolean", "confirmed")
        return cast(dict[str, CorrectionValue], self._json("PUT", f"{API}/files/{_q(file_id)}/corrections/{_q(mapping_id)}",
                    json={"value": value, "confirmed": confirmed}, retry_safe=False))

    def run_source_ocr(self, file_id: str, pages: list[int] | None = None, *, timeout: float | None = None) -> dict:
        """Recognise text on a scanned PDF (default: every scanned/mixed page) or image; at most 50 pages.

        Synchronous on the server, so the call waits longer than the client default: 30 s plus 6 s per
        page unless ``timeout`` says otherwise. Not retried automatically — recognition is expensive.
        After a timeout, ``list_source_page_words`` reports ``source == "ocr"`` for pages already done.
        """
        if self.validate and pages is not None:
            if not isinstance(pages, list) or len(pages) > 50 or any(
                    not isinstance(n, int) or isinstance(n, bool) or n < 0 for n in pages):
                raise ValidationError("pages must be a list of at most 50 zero-based page numbers", "pages")
        wait = timeout if timeout is not None else max(float(self._t.timeout or 30), 30.0 + 6.0 * (len(pages) if pages else 50))
        resp = self._t.send("POST", f"{API}/files/{_q(file_id)}/ocr", json={} if pages is None else {"pages": pages},
                            retry_safe=False, timeout=wait)
        return cast(dict, wrap(resp.json(), resp))

    def select_representative_source(self, file_id: str) -> SourceFile:
        return cast(SourceFile, self._json("POST", f"{API}/files/{_q(file_id)}/representative", retry_safe=False))

    def reorder_project_sources(self, project_id: str, ids: list[str]) -> list[SourceFile]:
        """Submit the complete live-source permutation; the server validates ownership."""
        if self.validate:
            if not isinstance(ids, list) or len(ids) > 10000 or any(not isinstance(i, str) or not 1 <= len(i) <= 128 for i in ids):
                raise ValidationError("ids must be a list of at most 10000 nonempty IDs (maximum 128 characters)", "ids")
            if len(set(ids)) != len(ids):
                raise ValidationError("ids must not contain duplicates", "ids")
        return cast(list[SourceFile], self._json("PUT", f"{API}/projects/{_q(project_id)}/files/order",
                    json={"ids": ids}, retry_safe=False))

    def download_source_content(self, file_id: str, dest: str | os.PathLike) -> dict:
        """Stream original bytes and verify the server SHA-256; requires export permission."""
        return self._download(f"{API}/files/{_q(file_id)}/content", dest, fallback_name=f"source_{file_id}")

    def locate_source_mappings(self, project_id: str, file_id: str, *, block: int | None = None) -> dict[str, list[dict]]:
        params = self._source_params(block=block)
        return cast(dict[str, list[dict]], self._get(f"{API}/projects/{_q(project_id)}/locate",
                    {"file": file_id, "block": params["block"]}))

    def list_source_records(self, project_id: str, *, offset: int = 0, limit: int = 50) -> SourceRecords:
        return cast(SourceRecords, self._get(f"{API}/projects/{_q(project_id)}/records",
                    self._source_params(offset=offset, limit=limit)))

    def iter_source_records(self, project_id: str, *, page_size: int = 50) -> Iterator[SourceRecord]:
        return cast(Iterator[SourceRecord], self._iterate(f"{API}/projects/{_q(project_id)}/records",
                    self._source_params(limit=page_size)))

    # ================================================================== templates
    def _builtin_body(self, editor: dict, name: str | None) -> dict:
        if self.validate:
            if not isinstance(editor, dict):
                raise ValidationError("editor must be an object", "editor")
            if name is not None and (not isinstance(name, str) or not name.strip() or len(name) > 100):
                raise ValidationError("name must contain 1–100 characters and not be blank", "name")
        return {"editor": editor, **({"name": name} if name is not None else {})}

    def create_builtin_template(self, kind: str, editor: dict, name: str | None = None, *,
                                idempotency_key: str | None = None) -> dict:
        """Create builtin_doc or builtin_sheet from the server's editor model.

        The service validates and renders the model; the SDK contains no editor engine.
        Supply a stable idempotency key to repeat this creation across processes.
        """
        if self.validate and kind not in ("builtin_doc", "builtin_sheet"):
            raise ValidationError("kind must be builtin_doc or builtin_sheet", "kind")
        body = {"kind": kind, **self._builtin_body(editor, name)}
        return self._json("POST", f"{API}/templates/builtin", json=body,
                          idempotency_key=self._key(idempotency_key))

    def save_builtin_template(self, template_id: str, editor: dict, *, base_version: int,
                              name: str | None = None, note: str | None = None, idempotency_key: str | None = None,
                              discard_live: bool = False) -> dict:
        """Append a model revision only if base_version is still the template head.

        A stale revision raises ConflictError. Keyed retries replay the original result
        without creating another version, including after the head has advanced. ``note`` names
        the version (a checkpoint people can find in the history); at most 200 characters.
        While the template's editor holds live edits nobody saved as a version, the save would
        replace them and raises ConflictError ``live_edits`` (detail.online / detail.last_by)
        unless ``discard_live`` says to throw them away.
        """
        if self.validate and (type(base_version) is not int or base_version < 1):
            raise ValidationError("base_version must be a positive integer", "base_version")
        if self.validate and note is not None and (not isinstance(note, str) or len(note) > 200):
            raise ValidationError("note must be a string of at most 200 characters", "note")
        body = {"base_version": base_version, **self._builtin_body(editor, name)}
        if note:
            body["note"] = note
        if discard_live:
            body["discard_live"] = True
        return self._json("PUT", f"{API}/templates/{_q(template_id)}/builtin", json=body,
                          idempotency_key=self._key(idempotency_key))

    def get_builtin_template(self, template_id: str, *, version: int | None = None) -> dict:
        """Export editor, fields and version metadata; omit version for the current head."""
        if self.validate:
            V.positive_int(version, "version")
        return self._get(f"{API}/templates/{_q(template_id)}/builtin", {"version": version})

    def upload_template(self, path: str | os.PathLike, name: str | None = None, *, filename: str | None = None,
                        resumable: bool | None = None, chunk_size: int | None = None, upload_id: str | None = None,
                        idempotency_key: str | None = None,
                        on_progress: Callable[[int, int], None] | None = None) -> dict:
        path = Path(path)
        fname = filename or path.name
        use_resumable = resumable if resumable is not None else (upload_id is not None or path.stat().st_size > self.resumable_threshold)
        key = self._key(idempotency_key)
        if use_resumable:
            up = self.resumable_upload(path, filename=fname, purpose="template", chunk_size=chunk_size,
                                       upload_id=upload_id, on_progress=on_progress,
                                       idempotency_key=hashlib.sha256(("template:" + key).encode()).hexdigest())
            body: dict = {"upload_id": up["id"]}
            if name is not None:
                body["name"] = name
            return self._json("POST", f"{API}/templates", json=body, idempotency_key=key)
        return self._multipart(f"{API}/templates", path, fname, key, params={"name": name}, on_progress=on_progress)

    def list_templates(self, *, limit: int | None = None, cursor: str | None = None, offset: int | None = None) -> dict:
        return self._get(f"{API}/templates", self._page_params(limit, cursor, offset))

    def iter_templates(self, page_size: int | None = None) -> Iterator[dict]:
        return self._iterate(f"{API}/templates", page_size=page_size)

    def get_template(self, template_id: str) -> dict:
        return self._get(f"{API}/templates/{_q(template_id)}")

    def delete_template(self, template_id: str) -> dict:
        return self._json("DELETE", f"{API}/templates/{_q(template_id)}", retry_safe=True)

    def get_template_fields(self, template_id: str) -> dict:
        return self._get(f"{API}/templates/{_q(template_id)}/fields")

    def create_template_fields(self, template_id: str, definition: dict, *, idempotency_key: str | None = None) -> dict:
        """Create fields from a ``docweave.template-fields`` definition: every field or none, as
        one new template version."""
        return self._json("POST", f"{API}/templates/{_q(template_id)}/fields", json=definition,
                          idempotency_key=self._key(idempotency_key))

    def get_template_usage(self, template_id: str) -> dict:
        """Projects bound to this template: those following its head and those pinned to a version."""
        return self._get(f"{API}/templates/{_q(template_id)}/usage")

    def update_template_field(self, template_id: str, field_id: str, *, base_head: int, **values: Any) -> dict:
        """Change one field's properties (name, required, data_type, format, default, font sizes,
        alignment, empty_policy, placeholder, binding, columns, overflow, …) as one new version.
        ``base_head`` is the template head you read; a moved head is 409 stale_template (also what a
        repeat of a call that already succeeded answers), so this is not retried automatically."""
        if self.validate and (not isinstance(base_head, int) or isinstance(base_head, bool) or base_head < 1):
            raise ValidationError("base_head must be the template head you read (a positive integer)", "base_head")
        if self.validate and not values:
            raise ValidationError("give at least one property to change", "values")
        return self._json("PATCH", f"{API}/templates/{_q(template_id)}/fields/{_q(field_id)}",
                          json={"base_head": base_head, **values}, retry_safe=False)

    def delete_template_field(self, template_id: str, field_id: str, *, base_head: int, project_id: str | None = None,
                              cascade: bool = True) -> dict:
        """Remove one field as one new version; with ``project_id`` also remove that project's mappings
        bound only to it (``removed_mappings``). Built-in templates keep fields in their editor (409)."""
        params: dict = {"base_head": base_head}
        if project_id:
            params["project_id"] = project_id
            params["cascade"] = "true" if cascade else "false"
        return self._json("DELETE", f"{API}/templates/{_q(template_id)}/fields/{_q(field_id)}", params=params,
                          retry_safe=False)

    def restore_template_version(self, template_id: str, version: int, *, base_head: int | None = None, discard_live: bool = False) -> dict:
        """Make an earlier version current as a new version (history kept). `base_head` names the
        head this decision was made on; a moved head is refused with 409 stale_template. Live edits in
        the template's editing room that nobody saved as a version yet are refused with 409 live_edits
        (detail.online / detail.last_by) unless ``discard_live`` says to throw them away."""
        body: dict = {"version": version}
        if base_head is not None:
            body["base_head"] = base_head
        if discard_live:
            body["discard_live"] = True
        return self._json("POST", f"{API}/templates/{_q(template_id)}/restore", json=body)

    def download_template(self, template_id: str, dest: str | os.PathLike, *, which: str = "original") -> dict:
        if self.validate:
            V.one_of(which, V.TEMPLATE_CONTENT, "which")
        return self._download(f"{API}/templates/{_q(template_id)}/content", dest, params={"which": which},
                              fallback_name=f"template_{template_id}")

    # ================================================================== organization-shared templates
    # These need a delegated token (a person acting through a client): membership in an
    # organization belongs to a person. A service account gets 403 `delegated_required`.
    def list_organization_templates(self, *, include_deprecated: bool = False, limit: int | None = None,
                                    cursor: str | None = None, offset: int | None = None) -> dict:
        return self._get(f"{API}/organization-templates", self._page_params(limit, cursor, offset, include_deprecated=include_deprecated))

    def iter_organization_templates(self, page_size: int | None = None, *, include_deprecated: bool = False) -> Iterator[dict]:
        return self._iterate(f"{API}/organization-templates", page_size=page_size, params={"include_deprecated": include_deprecated})

    def get_organization_template(self, publication_id: str) -> dict:
        return self._get(f"{API}/organization-templates/{_q(publication_id)}")

    def publish_template(self, template_id: str, *, description: str | None = None, idempotency_key: str | None = None) -> dict:
        """Publish this workspace's template to its organization: the head version is frozen as
        release 1. Editors of the workspace only; a personal workspace has no organization."""
        body = {"description": description} if description is not None else {}
        return self._json("POST", f"{API}/templates/{_q(template_id)}/publish", json=body, idempotency_key=self._key(idempotency_key))

    def release_organization_template(self, publication_id: str, *, description: str | None = None, set_description: bool = False) -> dict:
        """A new release from the source template's current head (409 `unchanged` when there is
        none), and/or a new description when `set_description` is true."""
        body = {"description": description} if set_description or description is not None else {}
        return self._json("POST", f"{API}/organization-templates/{_q(publication_id)}/releases", json=body)

    def deprecate_organization_template(self, publication_id: str, *, deprecated: bool = True, note: str | None = None) -> dict:
        body: dict = {"deprecated": deprecated}
        if note is not None:
            body["note"] = note
        return self._json("POST", f"{API}/organization-templates/{_q(publication_id)}/deprecate", json=body)

    def use_organization_template(self, publication_id: str, *, name: str | None = None, project_id: str | None = None,
                                  idempotency_key: str | None = None) -> dict:
        """Copy the current release into this workspace as an ordinary template (origin recorded),
        optionally binding it to a project."""
        body: dict = {}
        if name is not None:
            body["name"] = name
        if project_id is not None:
            body["project_id"] = project_id
        return self._json("POST", f"{API}/organization-templates/{_q(publication_id)}/use", json=body, idempotency_key=self._key(idempotency_key))

    # ================================================================== resumable uploads
    def create_upload(self, filename: str, size: int, sha256: str | None = None, purpose: str | None = None, *,
                      idempotency_key: str | None = None) -> dict:
        if self.validate:
            V.one_of(purpose, V.UPLOAD_PURPOSES, "purpose")
        body: dict = {"filename": filename, "size": size}
        if sha256:
            body["sha256"] = sha256
        if purpose:
            body["purpose"] = purpose
        return self._json("POST", f"{API}/uploads", json=body, idempotency_key=self._key(idempotency_key))

    def get_upload(self, upload_id: str) -> dict:
        return self._get(f"{API}/uploads/{_q(upload_id)}")

    def put_upload_chunk(self, upload_id: str, path: str | os.PathLike, start: int, length: int, total: int) -> dict:
        """Send bytes [start, start+length) of ``path``, streamed from disk. Not retried here:
        after a failure the caller asks the server how much it has (`resumable_upload` does)."""
        headers = {"Content-Range": f"bytes {start}-{start + length - 1}/{total}",
                   "Content-Type": "application/octet-stream", "Content-Length": str(length)}
        return self._json("PUT", f"{API}/uploads/{_q(upload_id)}", headers=headers, retry_safe=False,
                          body=lambda: {"content": iter_slice(path, start, length)})

    def complete_upload(self, upload_id: str, sha256: str | None = None) -> dict:
        # Completing twice is not an error on the server, so a lost answer is safely repeated.
        return self._json("POST", f"{API}/uploads/{_q(upload_id)}/complete",
                          json={"sha256": sha256} if sha256 else {}, retry_safe=True)

    def abort_upload(self, upload_id: str) -> dict:
        return self._json("POST", f"{API}/uploads/{_q(upload_id)}/abort", retry_safe=True)

    def resumable_upload(self, path: str | os.PathLike, *, filename: str | None = None, purpose: str | None = None,
                         chunk_size: int | None = None, upload_id: str | None = None,
                         on_progress: Callable[[int, int], None] | None = None,
                         idempotency_key: str | None = None) -> dict:
        """Upload a file in chunks and complete it; returns the completed upload (use its ``id``
        with `upload_source` / `upload_template`, which do this for you).

        After any failed chunk the server's ``received`` offset is the truth: the next chunk
        starts there, so nothing is sent twice and nothing is skipped. ``upload_id`` continues an
        upload started by an earlier process.
        """
        path = Path(path)
        size = path.stat().st_size
        if size < 1:
            raise ValidationError("空文件不能上传", "file")
        digest = sha256_file(path)
        chunk = self.chunk_size if chunk_size is None else chunk_size
        V.positive_int(chunk, "chunk_size")
        if upload_id:
            up = self.get_upload(upload_id)
            if int(up.get("size") or 0) != size:
                raise ValidationError(f"上传 {upload_id} 声明的大小是 {up.get('size')} 字节，本地文件是 {size} 字节：不是同一个文件",
                                      "upload_id")
            if up.get("sha256") and up["sha256"] != digest:
                raise ValidationError(f"上传 {upload_id} 声明的 SHA-256 与本地文件不一致：不是同一个文件", "upload_id")
        else:
            up = self.create_upload(filename or path.name, size, digest, purpose, idempotency_key=idempotency_key)
            # An idempotent create replays its original zero offset, not the current state.
            if getattr(up, "replayed", False):
                up = self.get_upload(up["id"])
        uid = up["id"]
        if up.get("status") in ("complete", "consumed"):
            return up
        received = int(up.get("received") or 0)
        if on_progress:
            on_progress(received, size)
        failures = 0
        while True:
            while received < size:
                n = min(chunk, size - received)
                try:
                    up = self.put_upload_chunk(uid, path, received, n, size)
                    next_received = int(up["received"])
                    if not received < next_received <= size:
                        raise ServerError(None, "upload_no_progress", "Server returned an invalid upload offset")
                    received = next_received
                    failures = 0
                except (TransportError, ServerError, RateLimitError, ConflictError, RangeNotSatisfiableError) as e:
                    if isinstance(e, ConflictError) and e.code not in ("upload_busy", "upload_not_open"):
                        raise
                    failures += 1
                    if failures > self.max_retries:
                        raise
                    delay = e.retry_after if e.retry_after is not None else self._t.backoff(failures)
                    if delay > self._t.max_retry_wait:
                        raise
                    self._sleep(delay)
                    up = self.get_upload(uid)                   # where to resume: the server decides
                    if up.get("status") in ("complete", "consumed"):
                        return up
                    received = int(up["received"])
                if on_progress:
                    on_progress(received, size)
            try:
                return self.complete_upload(uid, digest)
            except ConflictError as e:
                if e.code != "upload_incomplete":
                    raise
                failures += 1
                if failures > self.max_retries:
                    raise
                received = int(self.get_upload(uid)["received"])
            except SynwevoError as e:
                if e.code == "checksum_mismatch":
                    raise IntegrityError(e.status, e.code, "服务器收到的字节与本地文件的 SHA-256 不一致（上传期间文件被修改？）；"
                                         "该上传已被服务器重置", e.request_id, details=e.details) from None
                raise

    # ================================================================== jobs
    def create_job(self, project_id: str, *, formats: list[str] | None = None, naming: str | None = None,
                   error_policy: str | None = None, layout: str | None = None, keep_fields: bool | None = None,
                   idempotency_key: str | None = None) -> dict:
        """Submit a generation job. Repeating the call with the same ``idempotency_key`` and the
        same options returns the same job (``replayed`` is true); different options with the
        same key are refused with `ConflictError` (``idempotency_conflict``)."""
        body: dict = {}
        if formats is not None:
            body["formats"] = self._check(V.formats, formats)
        if naming is not None:
            body["naming"] = naming
        if error_policy is not None:
            body["error_policy"] = self._check(V.one_of, error_policy, V.ERROR_POLICIES, "error_policy")
        if layout is not None:
            body["layout"] = self._check(V.one_of, layout, V.LAYOUTS, "layout")
        if keep_fields is not None:
            body["keep_fields"] = bool(keep_fields)
        return self._json("POST", f"{API}/projects/{_q(project_id)}/jobs", json=body,
                          idempotency_key=self._key(idempotency_key))

    def list_jobs(self, project_id: str, *, limit: int | None = None, cursor: str | None = None,
                  offset: int | None = None) -> dict:
        return self._get(f"{API}/projects/{_q(project_id)}/jobs", self._page_params(limit, cursor, offset))

    def iter_jobs(self, project_id: str, page_size: int | None = None) -> Iterator[dict]:
        return self._iterate(f"{API}/projects/{_q(project_id)}/jobs", page_size=page_size)

    def get_job(self, job_id: str) -> dict:
        return self._get(f"{API}/jobs/{_q(job_id)}")

    def list_job_items(self, job_id: str, *, status: str | None = None, limit: int | None = None,
                       cursor: str | None = None, offset: int | None = None) -> dict:
        if self.validate:
            V.one_of(status, V.ITEM_STATUSES, "status")
        return self._get(f"{API}/jobs/{_q(job_id)}/items", self._page_params(limit, cursor, offset, status=status))

    def iter_job_items(self, job_id: str, *, status: str | None = None, page_size: int | None = None) -> Iterator[dict]:
        if self.validate:
            V.one_of(status, V.ITEM_STATUSES, "status")
        return self._iterate(f"{API}/jobs/{_q(job_id)}/items", {"status": status}, page_size)

    def iter_job_runs(self, job_id: str, page_size: int | None = None) -> Iterator[dict]:
        return self._iterate(f"{API}/jobs/{_q(job_id)}/runs", page_size=page_size)

    def job_events(self, job_id: str, *, after: int = 0, limit: int | None = None) -> dict:
        return self._get(f"{API}/jobs/{_q(job_id)}/events", {"after": after, "limit": limit})

    def iter_job_events(self, job_id: str, *, after: int = 0) -> Iterator[dict]:
        """The job's events recorded so far, oldest first (``seq`` increases)."""
        while True:
            page = self.job_events(job_id, after=after)
            items = page.get("items") or []
            yield from items
            if not items or page.get("next_after") in (None, after):
                return
            after = page["next_after"]

    def cancel_job(self, job_id: str) -> dict:
        # Cancelling twice is answered, not refused (cancel.reason = already_requested / already_terminal).
        return self._json("POST", f"{API}/jobs/{_q(job_id)}/cancel", json={}, retry_safe=True)

    def retry_job(self, job_id: str, *, include_skipped: bool = False, failed_only: bool = False,
                  idempotency_key: str | None = None) -> dict:
        """Run the failed and cancelled samples again (plus skipped ones with ``include_skipped``)
        from the job's original snapshot; successful samples keep their files. ``failed_only``
        selects only failed items, excludes cancelled/skipped/unplanned work, and cannot be
        combined with include_skipped. Requires a server supporting that option."""
        if self.validate:
            if type(failed_only) is not bool or type(include_skipped) is not bool:
                raise ValidationError("failed_only and include_skipped must be booleans")
            if failed_only and include_skipped:
                raise ValidationError("failed_only cannot include skipped items", "include_skipped")
        body = {"include_skipped": True} if include_skipped else {}
        if failed_only:
            body["failed_only"] = True
        return self._json("POST", f"{API}/jobs/{_q(job_id)}/retry", json=body,
                          idempotency_key=self._key(idempotency_key))

    def wait_for_job(self, job_id: str, *, timeout: float | None = None, poll_interval: float = 1.0,
                     max_poll_interval: float = 10.0, on_event: Callable[[dict], None] | None = None) -> dict:
        """Poll until the job ends and return its final state.

        The interval grows by half each quiet poll up to ``max_poll_interval`` and drops back
        when something changed; a ``Retry-After`` from the server is honoured as a minimum.
        ``on_event`` receives every job event (``/jobs/{id}/events``) exactly once, in order.
        Raises `WaitTimeoutError` (carrying the last state) after ``timeout`` seconds; the job
        itself is not affected.
        """
        _check_wait(timeout, poll_interval)
        _check_wait(None, max_poll_interval)
        deadline = None if timeout is None else self._clock() + timeout
        after = 0
        interval = poll_interval
        last_counts = None
        while True:
            if on_event is not None:
                for ev in self.iter_job_events(job_id, after=after):
                    after = max(after, int(ev.get("seq") or after))
                    on_event(ev)
            job = self.get_job(job_id)
            if job.get("status") in V.JOB_TERMINAL:
                if on_event is not None:
                    for ev in self.iter_job_events(job_id, after=after):
                        after = max(after, int(ev.get("seq") or after))
                        on_event(ev)
                return job
            counts = (job.get("status"), job.get("phase"), tuple(sorted((job.get("counts") or {}).items())))
            interval = poll_interval if counts != last_counts else min(interval * 1.5, max_poll_interval)
            last_counts = counts
            wait = interval
            hinted = parse_retry_after(getattr(job, "headers", {}))
            if hinted is not None:
                wait = max(wait, hinted)
            if deadline is not None:
                left = deadline - self._clock()
                if left <= 0:
                    raise WaitTimeoutError(f"任务 {job_id} 在 {timeout:g} 秒内没有结束（当前 {job.get('status')}）", job)
                wait = min(wait, left)
            self._sleep(wait)

    # ================================================================== artifacts and downloads
    def list_artifacts(self, job_id: str, *, limit: int | None = None, cursor: str | None = None,
                       offset: int | None = None) -> dict:
        return self._get(f"{API}/jobs/{_q(job_id)}/artifacts", self._page_params(limit, cursor, offset))

    def iter_artifacts(self, job_id: str, page_size: int | None = None) -> Iterator[dict]:
        return self._iterate(f"{API}/jobs/{_q(job_id)}/artifacts", page_size=page_size)

    def download_artifact(self, artifact: dict | str, dest: str | os.PathLike, *,
                          expected_sha256: str | None = None) -> dict:
        """Stream one artifact to ``dest`` (a file path, or an existing directory to put it in),
        verify its SHA-256 against ``X-Content-SHA256`` and the manifest, then rename it into
        place. On a mismatch nothing is left under the final name and `IntegrityError` is raised."""
        if isinstance(artifact, dict):
            aid, name = artifact["id"], artifact.get("filename")
            expected_sha256 = expected_sha256 or artifact.get("sha256")
        else:
            aid, name = artifact, None
        out = self._download(f"{API}/artifacts/{_q(aid)}/content", dest, expected_sha256=expected_sha256,
                             name=name, fallback_name=f"artifact_{aid}")
        out["id"] = aid
        return out

    def download_artifacts(self, job_id: str, dest_dir: str | os.PathLike, *, format: str | None = None) -> list[dict]:
        """Every artifact of a job into ``dest_dir``, each verified; returns one result per file."""
        fmt = self._check(V.format_name, format)
        d = Path(dest_dir)
        d.mkdir(parents=True, exist_ok=True)
        used: set[str] = set()
        results = []
        for a in self.iter_artifacts(job_id):
            if fmt and a.get("format") != fmt:
                continue
            name = safe_filename(a.get("filename"), f"artifact_{a['id']}")
            if name.lower() in used:                        # two samples rendered to the same name
                stem, dot, ext = name.rpartition(".")
                name = f"{stem or name}_{a['id']}{dot}{ext}" if dot else f"{name}_{a['id']}"
            used.add(name.lower())
            r = self.download_artifact(a, d / name)
            r.update({"seq": a.get("seq"), "format": a.get("format"), "label": a.get("label")})
            results.append(r)
        return results

    def artifact_link(self, artifact_id: str, *, idempotency_key: str | None = None) -> dict:
        """A short-lived download URL for one artifact (the URL itself is the credential)."""
        return self._json("POST", f"{API}/artifacts/{_q(artifact_id)}/link", json={},
                          idempotency_key=self._key(idempotency_key))

    def archive_link(self, job_id: str, *, format: str | None = None, idempotency_key: str | None = None) -> dict:
        body = {} if format is None else {"format": self._check(V.format_name, format)}
        return self._json("POST", f"{API}/jobs/{_q(job_id)}/archive-link", json=body,
                          idempotency_key=self._key(idempotency_key))

    def download_archive(self, job_id: str, dest: str | os.PathLike, *, format: str | None = None,
                         verify: bool = True) -> dict:
        """Download the job's ZIP. With ``verify`` every entry (except the report the server adds)
        is hashed and must match the artifact manifest one for one."""
        fmt = self._check(V.format_name, format)
        check = None
        if verify:
            def check(tmp_path: Path) -> dict:
                return self._verify_archive(job_id, tmp_path, fmt)
        return self._download(f"{API}/jobs/{_q(job_id)}/archive", dest, params={"format": fmt},
                              fallback_name=f"job_{job_id}.zip", check=check)

    def _verify_archive(self, job_id: str, path: Path, fmt: str | None) -> dict:
        want = Counter(a["sha256"] for a in self.iter_artifacts(job_id) if not fmt or a.get("format") == fmt)
        got: Counter = Counter()
        try:
            with zipfile.ZipFile(path) as z:
                bad = z.testzip()
                if bad is not None:
                    raise IntegrityError(None, "archive_corrupt", f"ZIP 中的 {bad} 校验失败（CRC 不符）")
                for info in z.infolist():
                    if info.is_dir() or info.filename == ARCHIVE_MANIFEST:
                        continue
                    h = _sha256_stream(z.open(info))
                    got[h] += 1
        except zipfile.BadZipFile as e:
            raise IntegrityError(None, "archive_corrupt", f"下载的文件不是有效的 ZIP：{e}") from None
        if got != want:
            missing = sum((want - got).values())
            extra = sum((got - want).values())
            raise IntegrityError(None, "archive_mismatch",
                                 f"ZIP 内容与产物清单不一致：缺少 {missing} 个、多出 {extra} 个文件",
                                 details={"missing": missing, "unexpected": extra})
        return {"entries_verified": sum(got.values())}

    def download_report(self, job_id: str, dest: str | os.PathLike) -> dict:
        return self._download(f"{API}/jobs/{_q(job_id)}/report.csv", dest, fallback_name=f"report_{job_id}.csv")

    def download_signed(self, token: str, dest: str | os.PathLike, *, expected_sha256: str | None = None) -> dict:
        """Download a signed-link token on this origin, without sending client credentials.

        Pass the token path segment, not an arbitrary URL. Treat it as a temporary secret.
        """
        return self._download(f"{API}/download/{_q(token)}", dest, expected_sha256=expected_sha256, auth=False)

    def _download(self, path: str, dest: str | os.PathLike, *, params: dict | None = None,
                  expected_sha256: str | None = None, name: str | None = None, fallback_name: str = "download.bin",
                  check: Callable[[Path], dict] | None = None, method: str = "GET", json: dict | None = None,
                  metadata_headers: dict[str, str] | None = None, auth: bool = True) -> dict:
        dest = Path(dest)
        attempt = 0
        while True:
            resp = self._t.send(method, path, params=params, json=json, stream=True, retry_safe=True, auth=auth)
            try:
                header_sha = (resp.headers.get("x-content-sha256") or "").lower() or None
                fname = safe_filename(name or _filename_from_disposition(resp.headers.get("content-disposition")),
                                      fallback_name)
                target = dest / fname if dest.is_dir() else dest
                length = resp.headers.get("content-length")
                with AtomicWriter(target) as w:
                    for block in resp.iter_bytes():
                        w.write(block)
                    digest = w.hash.hexdigest()
                    if not resp.headers.get("content-encoding") and length is not None and length.isdigit() and int(length) != w.size:
                        raise httpx.ReadError(f"只收到 {w.size} / {length} 字节")
                    verified = []
                    for label, want in (("X-Content-SHA256", header_sha), ("产物清单", (expected_sha256 or "").lower() or None)):
                        if want is None:
                            continue
                        if want != digest:
                            raise IntegrityError(resp.status_code, "checksum_mismatch",
                                                 f"下载的内容与{label}中的 SHA-256 不一致（{digest[:12]}… ≠ {want[:12]}…）",
                                                 resp.headers.get("x-request-id"),
                                                 details={"expected": want, "actual": digest, "source": label})
                        verified.append(label)
                    extra = {}
                    if check is not None:
                        w.flush()
                        extra = check(w.tmp)
                        verified.append("archive_entries")
                    final = w.commit()
                return {"path": str(final), "filename": final.name, "size": w.size, "sha256": digest,
                        "verified": verified, **extra,
                        **{key: resp.headers.get(header) for key, header in (metadata_headers or {}).items()}}
            except (httpx.TransportError, httpx.StreamError) as e:
                if attempt >= self.max_retries:
                    raise TransportError(None, "transport_error", f"下载 {path} 中断：{type(e).__name__} {e}") from None
                self._sleep(self._t.backoff(attempt))
                attempt += 1
            finally:
                resp.close()

    # ================================================================== webhooks
    def create_webhook(self, url: str, *, events: list[str] | None = None, description: str | None = None) -> dict:
        """Subscribe ``url`` to this workspace's job events. The answer carries ``secret``
        (``whsec_…``) exactly once — store it for `verify_webhook`. Not retried: the server has
        no idempotency key for subscriptions, and a repeat would create a second one."""
        body: dict = {"url": url}
        if events is not None:
            body["events"] = self._check(V.events, events)
        if description is not None:
            body["description"] = description
        return self._json("POST", f"{API}/webhooks/subscriptions", json=body, retry_safe=False)

    def list_webhooks(self) -> dict:
        return self._get(f"{API}/webhooks/subscriptions")

    def iter_webhooks(self) -> Iterator[dict]:
        return self._iterate(f"{API}/webhooks/subscriptions")

    def get_webhook(self, subscription_id: str) -> dict:
        return self._get(f"{API}/webhooks/subscriptions/{_q(subscription_id)}")

    def update_webhook(self, subscription_id: str, *, url: str = _UNSET, events: list[str] = _UNSET,
                       description: str = _UNSET, if_match: int | str | None = None) -> dict:
        """Change url / events / description. ``if_match`` (the ``version`` you read) refuses the
        change with 409 if someone else changed the subscription meanwhile."""
        body: dict = {}
        if url is not _UNSET:
            body["url"] = url
        if events is not _UNSET:
            body["events"] = self._check(V.events, events)
        if description is not _UNSET:
            body["description"] = description
        headers = {"If-Match": f'"{if_match}"'} if if_match is not None else None
        return self._json("PATCH", f"{API}/webhooks/subscriptions/{_q(subscription_id)}", json=body, headers=headers,
                          retry_safe=False)

    def delete_webhook(self, subscription_id: str) -> dict:
        return self._json("DELETE", f"{API}/webhooks/subscriptions/{_q(subscription_id)}", retry_safe=True)

    def pause_webhook(self, subscription_id: str) -> dict:
        return self._json("POST", f"{API}/webhooks/subscriptions/{_q(subscription_id)}/pause", retry_safe=True)

    def resume_webhook(self, subscription_id: str) -> dict:
        return self._json("POST", f"{API}/webhooks/subscriptions/{_q(subscription_id)}/resume", retry_safe=True)

    def rotate_webhook_secret(self, subscription_id: str, *, overlap_s: int | None = None) -> dict:
        """New secret (returned once); the old one keeps signing alongside for ``overlap_s``
        seconds (server default one day). Not retried: a repeat would rotate again."""
        body = {} if overlap_s is None else {"overlap_s": overlap_s}
        return self._json("POST", f"{API}/webhooks/subscriptions/{_q(subscription_id)}/rotate-secret", json=body,
                          retry_safe=False)

    def ping_webhook(self, subscription_id: str) -> dict:
        """Send one signed ``webhook.ping`` now and return its delivery record."""
        return self._json("POST", f"{API}/webhooks/subscriptions/{_q(subscription_id)}/ping", retry_safe=False)

    def list_webhook_deliveries(self, subscription_id: str, *, status: str | None = None, limit: int | None = None,
                                cursor: str | None = None) -> dict:
        if self.validate:
            V.one_of(status, V.DELIVERY_STATUSES, "status")
        return self._get(f"{API}/webhooks/subscriptions/{_q(subscription_id)}/deliveries",
                         {"status": status, "limit": limit, "cursor": cursor})

    def iter_webhook_deliveries(self, subscription_id: str, *, status: str | None = None,
                                page_size: int | None = None) -> Iterator[dict]:
        if self.validate:
            V.one_of(status, V.DELIVERY_STATUSES, "status")
        return self._iterate(f"{API}/webhooks/subscriptions/{_q(subscription_id)}/deliveries", {"status": status},
                             page_size)

    def get_webhook_delivery(self, subscription_id: str, delivery_id: str) -> dict:
        return self._get(f"{API}/webhooks/subscriptions/{_q(subscription_id)}/deliveries/{_q(delivery_id)}")

    def redeliver_webhook(self, subscription_id: str, delivery_id: str) -> dict:
        return self._json("POST", f"{API}/webhooks/subscriptions/{_q(subscription_id)}/deliveries/"
                                  f"{_q(delivery_id)}/redeliver", retry_safe=False)

    # ================================================================== workspace event feed
    def events_page(self, *, cursor: str | None = None, types: list[str] | None = None, limit: int | None = None) -> dict:
        if self.validate and types:
            for t in types:
                V.one_of(t, V.EVENT_TYPES, "types")
        return self._get(f"{API}/events", {"cursor": cursor, "types": ",".join(types) if types else None, "limit": limit})

    def iter_event_pages(self, *, cursor: str | None = None, types: list[str] | None = None, follow: bool = False,
                         interval: float = 5.0, page_size: int | None = None) -> Iterator[dict]:
        """Pages of workspace events in log order. Each page's ``next_cursor`` resumes right
        after that page: store it once the page's events are handled. With ``follow`` it keeps
        polling (every ``interval`` seconds when there is nothing new)."""
        while True:
            page = self.events_page(cursor=cursor, types=types, limit=page_size)
            yield page
            cursor = page.get("next_cursor") or cursor
            if page.get("has_more"):
                continue
            if not follow:
                return
            self._sleep(interval)

    def iter_events(self, *, cursor: str | None = None, types: list[str] | None = None, follow: bool = False,
                    interval: float = 5.0, page_size: int | None = None) -> Iterator[dict]:
        """Workspace events in log order. An event's ``id`` equals the ``webhook-id`` of its
        webhook delivery, so one dedupe table serves both channels."""
        for page in self.iter_event_pages(cursor=cursor, types=types, follow=follow, interval=interval,
                                          page_size=page_size):
            yield from page.get("items") or []


def _check_wait(timeout, interval):
    if timeout is not None and (not math.isfinite(timeout) or timeout < 0):
        raise ValidationError("wait timeout must be finite and non-negative", "timeout")
    if not math.isfinite(interval) or interval <= 0:
        raise ValidationError("poll interval must be finite and positive", "poll_interval")


def _sha256_stream(fh) -> str:
    import hashlib
    h = hashlib.sha256()
    with fh:
        for block in iter(lambda: fh.read(256 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def wrap_like(data: dict, like: Any) -> dict:
    """Carry the metadata of the call that created something onto a follow-up read of it."""
    if isinstance(like, ApiDict):
        out = ApiDict(data)
        out.status_code, out.request_id, out.replayed, out.headers = like.status_code, like.request_id, like.replayed, like.headers
        return out
    return data


__all__ = ["Client", "ARCHIVE_MANIFEST", "__version__"]
