import io
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from synwevo_core.http import repo_root

ROOT = repo_root()
APP = Path(__file__).resolve().parents[1]          # apps/docs
DATA = ROOT / "testdata"
FIXTURE_MARKERS = ("测试_客户100.xlsx", "测试_发货单模板.pdf", "测试_发货单图片模板.png", "测试_订单段落模板.docx", "测试_多表单登记表.pdf")


GENERATOR = APP / "scripts" / "make_fixtures.py"
STAMP = ".generated-by"          # holds the SHA-256 of the generator that produced testdata/


def _generator_digest() -> str:
    import hashlib
    return hashlib.sha256(GENERATOR.read_bytes()).hexdigest()


@pytest.fixture(scope="session")
def fixtures() -> Path:
    """TEST-ONLY fixture files (generated, synthetic).

    Regenerated when any marker file is missing **or when the generator changed** since the
    files were made: checking only for existence meant a changed make_fixtures.py never reached
    an existing testdata/, and tests kept passing (or failing) against content the generator no
    longer produces. New files are generated into a scratch directory and moved in one file at a
    time with an atomic rename, so a concurrent test run never reads a half-written fixture.
    """
    import shutil
    import tempfile
    want = _generator_digest()
    stamp = DATA / STAMP
    fresh = stamp.exists() and stamp.read_text(encoding="utf-8").strip() == want
    if fresh and all((DATA / n).exists() for n in FIXTURE_MARKERS):
        return DATA
    DATA.mkdir(parents=True, exist_ok=True)
    scratch = Path(tempfile.mkdtemp(prefix=".testdata-", dir=DATA.parent))
    try:
        subprocess.run([sys.executable, str(GENERATOR), str(scratch)], check=True)
        for src in sorted(scratch.rglob("*")):
            if src.is_file():
                dst = DATA / src.relative_to(scratch)
                dst.parent.mkdir(parents=True, exist_ok=True)
                src.replace(dst)
        (DATA / STAMP).write_text(want + "\n", encoding="utf-8")
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    return DATA


@pytest.fixture()
def client(tmp_path):
    from fastapi.testclient import TestClient

    from synwevo_docs.api.app import create_app
    app = create_app(str(tmp_path / "data"))
    with TestClient(app) as c:
        c.app_state = app.state
        yield c
    if app.state.jobs:
        app.state.jobs.stop()


from .helpers import Api  # noqa: F401  (re-exported for the fixtures below)


@pytest.fixture()
def api(client):
    return Api(client)


PORTAL_THREAD = "asyncio-portal-"        # anyio names a blocking portal's thread f"{backend}-portal-{id(portal):x}"
PORTAL_GRACE_S = 1.0                     # total wait for a portal that is still on its way out


def _portals_started_since(before: set[threading.Thread]) -> list[threading.Thread]:
    new = [t for t in threading.enumerate() if t not in before and t.name.startswith(PORTAL_THREAD)]
    deadline = time.monotonic() + PORTAL_GRACE_S
    for t in new:
        t.join(max(0.0, deadline - time.monotonic()))
    return [t for t in new if t.is_alive()]


def _open_sockets_by_portal() -> dict[str, object]:
    """Every WebSocket session that was entered, by the name of its portal's thread (only looked up
    once something leaked: walking the heap is too slow for every test)."""
    import gc

    from starlette.testclient import WebSocketTestSession
    out = {}
    for obj in gc.get_objects():
        if isinstance(obj, WebSocketTestSession) and getattr(obj, "portal", None) is not None:
            out[f"{PORTAL_THREAD}{id(obj.portal):x}"] = obj
    return out


def _exit_within(sessions: list, budget: float) -> bool:
    """Exit leaked sessions so their loops stop reading the test's database; bounded, because a
    handler stuck in a fixture's wait must not hang the run. True when all of them ended."""
    def run():
        for s in sessions:
            try:
                s.__exit__(None, None, None)
            except BaseException:  # noqa: BLE001 — the test already fails for the leak itself
                pass
    t = threading.Thread(target=run, name="exit-leaked-sockets", daemon=True)
    t.start()
    t.join(budget)
    return not t.is_alive()


@pytest.fixture(autouse=True)
def _no_socket_outlives_its_test():
    """A `websocket_connect(...)` session runs on a portal thread of its own that ends only when the
    session is exited — also after the server closed the connection, and also when the test raised
    after entering it. One left open keeps its connection's loop running after the test: a live room
    re-reads the person's session every SYNWEVO_COLLAB_RECHECK_S on that test's database, and in a
    full run that polluted a later test counting statements (CI run 40182684885).

    Autouse fixtures are set up before, and torn down after, the fixtures a test asks for, so the
    portals this finds were started by this test and outlived all of its own teardown (the
    `with TestClient(app)` of a `world` fixture included). Portals alive before the test started are
    not blamed on it. The leaked WebSocket sessions are exited here, so one failing test does not
    pollute the ones after it."""
    before = set(threading.enumerate())
    yield
    leaked = _portals_started_since(before)
    if not leaked:
        return
    sockets = _open_sockets_by_portal()
    named, exitable = [], []
    for t in leaked:
        s = sockets.get(t.name)
        if s is None:
            named.append(f"{t.name} (not a WebSocket session: a TestClient entered and never exited?)")
        else:
            exitable.append(s)
            query = s.scope.get("query_string", b"").decode()
            named.append(f"{t.name} (WebSocket {s.scope.get('path')}{'?' + query if query else ''})")
    cleanup = ""
    if exitable:
        cleanup = (" The WebSocket sessions among them were exited now." if _exit_within(exitable, budget=5.0)
                   else " Exiting the WebSocket sessions among them did not finish within 5 s; later tests may see their loops.")
    pytest.fail(f"{len(leaked)} portal thread(s) outlived the test: " + "; ".join(named)
                + ". Exit every websocket_connect session a test enters — also after the server closed it, and when"
                  " something after entering it raised (the peer helpers close on failure; call .close() on the rest)."
                + cleanup, pytrace=False)


@pytest.fixture(autouse=True)
def _no_worker_outlives_its_test():
    """An application built outside `with TestClient(...)` never runs its shutdown hook. Without
    this net every such test left its runner, dispatcher and sweeper threads — and through them
    its store — alive; the verify container ran out of memory a third of the way through."""
    yield
    from synwevo_core.workers import stop_all
    stop_all(wait=3.0)
