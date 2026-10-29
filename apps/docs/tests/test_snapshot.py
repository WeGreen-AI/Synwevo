"""Backup and restore.

A backup that has never been restored is a file, not a backup — so the tests here restore and
compare, rather than checking that the archive exists. And a restore that brings back access
withdrawn since the backup is a security incident, so the central test here takes a real backup,
withdraws access afterwards, restores into an isolated directory and checks that the withdrawal
is still in force.

Every instance here lives under pytest's tmp_path. SYNWEVO_DATA_DIR is pointed at a tmp path for
every test, so no default in the tool can ever resolve to the repository's real data/.
"""
from __future__ import annotations

import hashlib
import io
import os
import sqlite3
import stat
import tarfile
from pathlib import Path

import pytest

from synwevo_core.errors import ServiceError

ROOT = __import__("synwevo_core.http", fromlist=["repo_root"]).repo_root()
SCRIPTS = ROOT / "apps" / "docs" / "scripts"


def _snapshot():
    """The backup/restore script, loaded by path — it is a script, not an installed package."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("_synwevo_snapshot", SCRIPTS / "snapshot.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(autouse=True)
def _never_the_real_data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("SYNWEVO_DATA_DIR", str(tmp_path / "configured-data-dir-unused"))
    monkeypatch.delenv("DOCWEAVE_DATA_DIR", raising=False)


def _release(store) -> None:
    """Close the connection a Store holds, as a stopped server would."""
    c = getattr(store._local, "c", None)
    if c is not None:
        c.close()
        store._local.c = None


@pytest.fixture()
def instance(tmp_path):
    """TEST FIXTURE: a small instance with real rows and real blob files, written through
    Store.put_blob so they sit exactly where the application reads them (blobs/<h[:2]>/<h>)."""
    from synwevo_core.store import Store, now
    data = tmp_path / "data"
    store = Store(data)
    digests = [store.put_blob(b"content-" + str(i).encode()) for i in range(3)]
    for i, digest in enumerate(digests):
        store.execute(
            "INSERT INTO templates (id,name,kind,format,tenant,created_at,updated_at,"
            "original_blob,deleted) VALUES (?,?,'builtin_doc','docx','default',?,?,?,0)",
            (f"t{i}", f"模板 {i}", now(), now(), digest))
    store.execute(
        "INSERT INTO projects (id,name,tenant,step,rev,mapping_rev,created_at,updated_at,deleted) "
        "VALUES ('p1','真实项目','default','upload',1,1,?,?,0)", (now(), now()))
    _release(store)
    return data


def _backup(snapshot, data: Path, out: Path) -> Path:
    assert snapshot.create(_args(data=str(data), out=str(out))) == 0
    archives = list(out.glob("*.tar.gz"))
    assert len(archives) == 1
    return archives[0]


def _restore(snapshot, archive, into, journal_from=None, **kw) -> int:
    return snapshot.restore(_args(archive=str(archive), into=str(into),
                                  journal_from=str(journal_from) if journal_from else None, **kw))


def _ro(db: Path) -> sqlite3.Connection:
    c = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    c.row_factory = sqlite3.Row
    return c


# ---------------------------------------------------------------- round trip
def test_a_snapshot_restores_to_the_same_content(instance, tmp_path, capsys):
    snapshot = _snapshot()
    archive = _backup(snapshot, instance, tmp_path / "backups")

    # Change the original afterwards; the archive must still restore what it captured.
    from synwevo_core.store import Store, now
    Store(instance).execute(
        "INSERT INTO projects (id,name,tenant,step,rev,mapping_rev,created_at,updated_at,deleted) "
        "VALUES ('p2','备份之后才建的','default','upload',1,1,?,?,0)", (now(), now()))

    into = tmp_path / "restored"
    assert _restore(snapshot, archive, into, journal_from=instance) == 0
    c = _ro(into / "docweave.sqlite3")
    names = {r[0] for r in c.execute("SELECT name FROM projects")}
    c.close()
    assert names == {"真实项目"}, "恢复出来的应当是捕获时的状态"
    restored = Store(into, migrate=False)
    for (digest,) in _ro(into / "docweave.sqlite3").execute("SELECT original_blob FROM templates"):
        assert restored.get_blob(digest).startswith(b"content-"), "blob 正文必须恢复到应用读取的位置"
    assert not list(into.glob(".restore-*")), "临时目录不能留下"


def test_verify_restores_and_checks_rather_than_trusting_the_archive(instance, tmp_path, capsys):
    snapshot = _snapshot()
    archive = _backup(snapshot, instance, tmp_path / "b")
    assert snapshot.verify(_args(archive=str(archive), journal_from=str(instance))) == 0
    text = capsys.readouterr().out
    assert "项通过" in text and "✗" not in text


@pytest.mark.parametrize("old_entitlement", ["absent", "stale_revocation"])
def test_archive_restore_does_not_revive_revoked_application_access(instance, tmp_path, old_entitlement):
    """Synthetic members-policy fixtures; exercise a real archive, not a DB-only replay."""
    from synwevo_core import authority, journal
    from synwevo_core.apps import Apps, bootstrap_docs_app
    from synwevo_core.identity import Identity
    from synwevo_core.store import Store

    store = Store(instance)
    ident = Identity(store)
    apps = Apps(store, ident)
    bootstrap_docs_app(store, ident)
    owner = ident.create_user("restore-owner@example.invalid", "synthetic-restore-passphrase", status="active")
    member = ident.create_user("restore-member@example.invalid", "synthetic-restore-passphrase", status="active")
    wid = ident.workspaces_for(owner["id"])[0]["id"]
    uid = member["id"]
    ident.add_member(wid, uid, "editor")
    if old_entitlement == "stale_revocation":
        since = store.one("SELECT created_at FROM memberships WHERE workspace_id=? AND user_id=?", (wid, uid))["created_at"]
        store.execute("INSERT INTO app_entitlements (workspace_id,app_id,user_id,granted_at,revoked_at) "
                      "VALUES (?,'docweave',?,?,?)", (wid, uid, since - 100, since - 50))
    assert apps.check_access(uid, "docweave", wid) == (True, wid, "ok")
    desc = authority.describe(user=member, tenant=wid)
    assert authority.check(store, desc, need="write", tenant=wid) == (True, None)
    original = [(r["id"], r["original_blob"]) for r in store.all("SELECT id,original_blob FROM templates ORDER BY id")]
    _release(store)
    snapshot = _snapshot()
    archive = _backup(snapshot, instance, tmp_path / "access-backup")

    apps.revoke(wid, "docweave", uid)
    revoked_at = store.one("SELECT revoked_at FROM app_entitlements WHERE workspace_id=? AND user_id=?", (wid, uid))["revoked_at"]
    _release(store)
    live_journal = journal.path(instance)
    journal_before = live_journal.read_bytes()
    withdrawal = next(e for e in journal.read(instance) if e["kind"] == "entitlement_revoked" and e["user_id"] == uid)
    assert withdrawal["at"] >= revoked_at
    into = tmp_path / "access-restored"
    assert _restore(snapshot, archive, into, journal_from=instance) == 0

    restored = Store(into, migrate=False)
    restored_apps = Apps(restored, Identity(restored))
    assert restored_apps.check_access(uid, "docweave", wid)[0] is False
    assert authority.check(restored, desc, need="write", tenant=wid)[0] is False
    # Replay uses the durable journal event's timestamp, recorded just after the live update.
    assert restored.one("SELECT revoked_at FROM app_entitlements WHERE workspace_id=? AND user_id=?", (wid, uid))["revoked_at"] == withdrawal["at"]
    assert restored.one("SELECT role FROM memberships WHERE workspace_id=? AND user_id=?", (wid, uid))["role"] == "editor"
    assert [(r["id"], r["original_blob"]) for r in restored.all("SELECT id,original_blob FROM templates ORDER BY id")] == original
    for _, digest in original:
        assert hashlib.sha256(restored.get_blob(digest)).hexdigest() == digest
    assert live_journal.read_bytes() == journal_before
    _release(restored)


def _rebuild(archive: Path, tmp_path: Path, mutate) -> Path:
    """Re-pack an archive after `mutate(staged_dir)` — how a damaged backup would look."""
    staged = tmp_path / "staged"
    staged.mkdir()
    with tarfile.open(archive, "r:gz") as t:
        t.extractall(staged, filter="data")
    mutate(staged)
    broken = tmp_path / "broken.tar.gz"
    with tarfile.open(broken, "w:gz") as t:
        for item in sorted(staged.iterdir()):
            t.add(item, arcname=item.name)
    return broken


def test_verify_fails_when_the_blob_content_is_missing(instance, tmp_path, capsys):
    """The worst failure mode: a catalogue of documents whose contents are gone. It must be
    caught by verification, not discovered when someone opens a file."""
    import shutil
    snapshot = _snapshot()
    archive = _backup(snapshot, instance, tmp_path / "b")
    broken = _rebuild(archive, tmp_path, lambda d: shutil.rmtree(d / "blobs"))
    assert snapshot.verify(_args(archive=str(broken), journal_from=str(instance))) == 1
    assert "✗" in capsys.readouterr().out


def test_verify_fails_when_the_cost_rollup_disagrees_with_its_facts(instance, tmp_path, capsys):
    """The restored copy would refuse that provider account's paid calls: the rehearsal says so."""
    from synwevo_core.store import Store
    store = Store(instance)
    with store.tx() as t:  # FIXTURE: a derived cost row with no fact behind it, written past the gate
        t.execute("INSERT INTO model_cost_rollup_deltas(kind) VALUES ('fixture-tamper')")
        t.execute("INSERT INTO model_cost_buckets(environment,provider,account_ref,dim,member,span,start,unit,den,"
                  "n_adm,n_res,n_cross,n_exc,bound_hi,bound_lo,rbound_hi,rbound_lo,cost_hi,cost_lo,cost_carry,cost_frac) "
                  "VALUES ('test','fixture-provider','fixture-account','account','',86400,0,'FIXTURE-COST',0,"
                  "1,0,0,0,0,7,0,0,0,0,0,0)")
        t.execute("DELETE FROM model_cost_rollup_deltas")
    _release(store)
    snapshot = _snapshot()
    archive = _backup(snapshot, instance, tmp_path / "b")
    assert snapshot.verify(_args(archive=str(archive), journal_from=str(instance))) == 1
    text = capsys.readouterr().out
    assert "✗ 成本汇总与成本事实一致" in text and "fixture-account" in text


def test_verify_fails_when_a_blob_is_corrupted_even_though_its_name_is_present(instance, tmp_path, capsys):
    """Name matching said "present"; the application would serve the wrong bytes."""
    snapshot = _snapshot()
    archive = _backup(snapshot, instance, tmp_path / "b")

    def corrupt(d):
        victim = next(p for p in (d / "blobs").rglob("*") if p.is_file())
        victim.write_bytes(b"not what was stored")
    broken = _rebuild(archive, tmp_path, corrupt)
    assert snapshot.verify(_args(archive=str(broken), journal_from=str(instance))) == 1
    out = capsys.readouterr().out
    assert "✗ 内容的 SHA-256 与名字一致" in out


def test_verify_fails_when_blobs_are_not_where_the_application_reads_them(instance, tmp_path, capsys):
    """A flat blobs/<digest> layout passes a name match anywhere under blobs/, and every read
    through Store.blob_path (blobs/<h[:2]>/<h>) fails."""
    snapshot = _snapshot()
    archive = _backup(snapshot, instance, tmp_path / "b")

    def flatten(d):
        for p in [p for p in (d / "blobs").rglob("*") if p.is_file()]:
            p.rename(d / "blobs" / p.name)
    broken = _rebuild(archive, tmp_path, flatten)
    assert snapshot.verify(_args(archive=str(broken), journal_from=str(instance))) == 1
    assert "✗ 都在应用读取的位置" in capsys.readouterr().out


# ---------------------------------------------------------------- overwrite protection
def test_restore_refuses_to_overwrite_an_existing_database(instance, tmp_path):
    snapshot = _snapshot()
    archive = _backup(snapshot, instance, tmp_path / "b")
    db = instance / "docweave.sqlite3"
    before = db.read_bytes()
    assert _restore(snapshot, archive, instance, journal_from=instance, force=False) == 2
    assert db.read_bytes() == before, "拒绝覆盖时原库必须一个字节都不变"


def test_forced_restore_refuses_while_the_database_is_open(instance, tmp_path, capsys):
    """An idle server holds the database open. Overwriting it underneath is how a live instance
    ends up with a restored main file and a stale log replayed onto it."""
    from synwevo_core.store import Store
    snapshot = _snapshot()
    archive = _backup(snapshot, instance, tmp_path / "b")
    server = Store(instance)                              # a running server's connection
    server.one("SELECT 1 AS x")
    listing = sorted(p.name for p in instance.iterdir())
    try:
        assert _restore(snapshot, archive, instance, force=True) == 2
        assert "正被某个进程打开" in capsys.readouterr().err
        assert sorted(p.name for p in instance.iterdir()) == listing
    finally:
        _release(server)


def test_forced_restore_sets_the_old_database_aside_and_leaves_no_stale_log(instance, tmp_path):
    from synwevo_core.store import Store, now
    snapshot = _snapshot()
    archive = _backup(snapshot, instance, tmp_path / "b")
    live = Store(instance)
    live.execute("INSERT INTO projects (id,name,tenant,step,rev,mapping_rev,created_at,updated_at,deleted) "
                 "VALUES ('p_after','备份之后','default','upload',1,1,?,?,0)", (now(), now()))
    _release(live)

    assert _restore(snapshot, archive, instance, force=True) == 0

    quarantine = [p for p in instance.iterdir() if p.name.startswith(".pre-restore-")]
    assert len(quarantine) == 1, "原库要被移开保存，而不是删除"
    kept = _ro(quarantine[0] / "docweave.sqlite3")
    assert kept.execute("SELECT COUNT(*) FROM projects WHERE id='p_after'").fetchone()[0] == 1
    kept.close()
    assert not (instance / "docweave.sqlite3-wal").exists() and not (instance / "docweave.sqlite3-shm").exists(), \
        "恢复后的主库旁边不能留着旧库的日志"
    c = _ro(instance / "docweave.sqlite3")
    assert {r[0] for r in c.execute("SELECT id FROM projects")} == {"p1"}
    c.close()


# ---------------------------------------------------------------- the archive is a secret
def test_the_archive_is_not_world_readable_and_never_exists_half_written(instance, tmp_path):
    """It contains signing keys and password hashes. Saying so is not enough; the mode matters —
    from creation, not after a chmod."""
    snapshot = _snapshot()
    out = tmp_path / "b"
    archive = _backup(snapshot, instance, out)
    assert not (archive.stat().st_mode & (stat.S_IRGRP | stat.S_IROTH | stat.S_IWGRP | stat.S_IWOTH))
    assert not (out.stat().st_mode & 0o077), oct(out.stat().st_mode)
    assert not list(out.glob("*.partial")), "不能留下写了一半的归档"


# ---------------------------------------------------------------- extraction safety
def _evil(tmp_path: Path, *members: tarfile.TarInfo, payload: bytes = b"x") -> Path:
    evil = tmp_path / "evil.tar.gz"
    with tarfile.open(evil, "w:gz") as t:
        for m in members:
            if m.isfile():
                m.size = len(payload)
                t.addfile(m, io.BytesIO(payload))
            else:
                t.addfile(m)
    return evil


def _member(name, kind=tarfile.REGTYPE, linkname=""):
    m = tarfile.TarInfo(name)
    m.type = kind
    m.linkname = linkname
    m.mode = 0o644
    return m


def test_an_archive_with_an_escaping_path_is_refused(tmp_path):
    snapshot = _snapshot()
    evil = _evil(tmp_path, _member("../escaped"))
    with pytest.raises(SystemExit, match="越界"):
        snapshot._extract(evil, tmp_path / "into")
    assert not (tmp_path / "escaped").exists()


@pytest.mark.parametrize("members, why", [
    # A symlink to outside, then a file written through it: the classic escape on 3.12/3.13.
    ([_member("blobs", tarfile.SYMTYPE, "/tmp"), _member("blobs/owned")], "链接"),
    ([_member("docweave.sqlite3", tarfile.LNKTYPE, "/etc/passwd")], "链接"),
    ([_member("/abs/path")], "绝对路径"),
    ([_member("blobs/../../x")], "越界"),
    # The live journal's own name: an archive must never be able to overwrite it.
    ([_member("security-journal.jsonl")], "不属于备份"),
    ([_member("blobs/fifo", tarfile.FIFOTYPE)], "非普通文件"),
])
def test_unsafe_members_are_refused_before_anything_is_written(tmp_path, members, why):
    snapshot = _snapshot()
    evil = _evil(tmp_path, *members)
    into = tmp_path / "into"
    with pytest.raises(SystemExit, match=why):
        snapshot._extract(evil, into)
    assert not into.exists() or not any(into.iterdir()), "拒绝时不能已经写出任何东西"


def test_restore_of_an_unsafe_archive_leaves_the_target_untouched(instance, tmp_path):
    snapshot = _snapshot()
    evil = _evil(tmp_path, _member("docweave.sqlite3"), _member("blobs", tarfile.SYMTYPE, str(tmp_path)))
    into = tmp_path / "target"
    with pytest.raises(SystemExit):
        _restore(snapshot, evil, into, journal_from=instance)
    assert not (into / "docweave.sqlite3").exists()
    assert not list(into.glob(".restore-*"))


# ---------------------------------------------------------------- withdrawn access stays withdrawn
def _people(store, now):
    ts = now()
    with store.tx() as t:
        t.execute("INSERT INTO users (id,email,email_key,status,created_at,updated_at) "
                  "VALUES ('u_alice','alice@example.test','alice@example.test','active',?,?)", (ts, ts))
        t.execute("INSERT INTO workspaces (id,name,kind,status,created_at,updated_at) "
                  "VALUES ('w1','团队','org','active',?,?)", (ts, ts))
        t.execute("INSERT INTO memberships (workspace_id,user_id,role,created_at,updated_at) "
                  "VALUES ('w1','u_alice','admin',?,?)", (ts, ts))


def test_access_withdrawn_after_the_backup_stays_withdrawn_after_a_restore(instance, tmp_path, capsys):
    """The property a restore must not break (F314).

    Before the backup: an API key, a member, a browser session, a service access token, a
    pending webhook delivery and a queued job. After the backup: the key is revoked and the
    member removed — both through the security journal, as every withdrawal is recorded. Then
    the backup is restored into an isolated directory.
    """
    from synwevo_core import journal
    from synwevo_core.auth import Auth
    from synwevo_core.oidc import Clients, Grants
    from synwevo_core.sessions import Sessions
    from synwevo_core.store import Store, now

    snapshot = _snapshot()
    live = Store(instance)
    _people(live, now)
    raw_key, key = Auth(live).create_key("ci", tenant="w1", scopes=("read",))
    cookie, _ = Sessions(live).create("u_alice", workspace_id="w1", amr="password")
    # A real registered service and enabled application, rather than a fabricated client dict.
    from synwevo_core.apps import bootstrap_docs_app
    from synwevo_core.identity import Identity
    bootstrap_docs_app(live, Identity(live))
    live.execute("INSERT INTO workspace_apps (workspace_id,app_id,enabled_at) VALUES ('w1','docweave',?)", (now(),))
    service, _ = Clients(live).register(client_id="svc-1", app_id="docweave", name="Synthetic restore service",
        redirect_uris=[], kind="service", workspace_id="w1", api_scopes="docweave.read", confidential=True)
    token = Grants(live).issue_for_service(client=service,
                                          scope="docweave.read")["access_token"]
    ts = now()
    with live.tx() as t:
        # An invitation, and the mail carrying its link still queued at backup time.
        t.execute("INSERT INTO invitations (id,email_key,workspace_id,role,can_export,token_hash,invited_by,"
                  "created_at,expires_at) VALUES ('inv1','bob@example.test','w1','editor',0,'h-inv1','u_alice',?,?)",
                  (ts, ts + 7 * 86400))
        t.execute("INSERT INTO mail_outbox (id,to_addr,subject,body,kind,ref_id,status,attempts,created_at) "
                  "VALUES ('m1','bob@example.test','邀请','打开 https://accounts.example.test/invite?token=LIVE-TOKEN-1',"
                  "'invitation','inv1','queued',0,?)", (ts,))
        t.execute("INSERT INTO job_events (id,job_id,tenant,type,version,occurred_at,data) "
                  "VALUES ('ev1','j1','w1','job.completed',1,?, '{}')", (ts,))
        t.execute("INSERT INTO webhook_deliveries (id,event_id,event_seq,endpoint,status,attempts,next_attempt_at,created_at) "
                  "VALUES ('wh1','ev1',1,'https://hooks.example.test/x','pending',0,?,?)", (ts, ts))
        t.execute("INSERT INTO jobs (id,project_id,status,created_at,tenant,run_no) VALUES ('j1','p1','queued',?,'w1',1)", (ts,))
        t.execute("INSERT INTO job_items (id,job_id,seq,status) VALUES ('ji1','j1',1,'pending')")
        t.execute("INSERT INTO job_runs (job_id,run_no,trigger,created_at,status) VALUES ('j1',1,'create',?,'queued')", (ts,))

    archive = _backup(snapshot, instance, tmp_path / "b")

    # --- after the backup: withdraw access, the way the product does (and journals) it
    assert Auth(live).revoke_key(key["id"])
    journal.record(live, "api_key_revoked", key_id=key["id"])
    live.execute("DELETE FROM memberships WHERE workspace_id='w1' AND user_id='u_alice'")
    journal.record(live, "member_removed", workspace_id="w1", user_id="u_alice")
    live.execute("UPDATE invitations SET revoked_at=? WHERE id='inv1'", (now(),))
    journal.record(live, "invitation_revoked", invitation_id="inv1")
    _release(live)
    live_journal = instance / "security-journal.jsonl"
    journal_before = live_journal.read_bytes()

    # --- restore into an isolated directory
    into = tmp_path / "isolated"
    assert _restore(snapshot, archive, into, journal_from=instance) == 0
    out = capsys.readouterr().out

    restored = Store(into)
    # the revoked key is refused, exactly as on the live instance
    with pytest.raises(ServiceError) as e:
        Auth(restored).verify(f"Bearer {raw_key}")
    assert e.value.status == 401
    assert restored.one("SELECT revoked_at FROM api_keys WHERE id=?", (key["id"],))["revoked_at"]
    # the removed member is not a member again
    assert restored.one("SELECT COUNT(*) AS n FROM memberships WHERE workspace_id='w1' AND user_id='u_alice'")["n"] == 0
    # everyone signs in again: the browser session and the service token from before are dead
    assert Sessions(restored).load(cookie, touch=False) is None
    assert Grants(restored).load_access(token) is None
    # nothing is re-sent, nothing is resumed
    assert restored.one("SELECT status FROM webhook_deliveries WHERE id='wh1'")["status"] == "dropped"
    job = restored.one("SELECT status, halt_reason FROM jobs WHERE id='j1'")
    assert job == {"status": "cancelled", "halt_reason": "restored_from_backup"}
    assert restored.one("SELECT status FROM job_items WHERE id='ji1'")["status"] == "cancelled"
    assert int(restored.one("SELECT value FROM meta WHERE key='webhook_last_seq'")["value"]) >= 1, \
        "事件游标要推进到恢复点，旧事件不能被重新排进发送队列"
    # the invitation withdrawn after the backup is not accepted again
    assert restored.one("SELECT revoked_at FROM invitations WHERE id='inv1'")["revoked_at"]
    # and the queued mail with its live link is not sent the moment the instance starts
    mail = restored.one("SELECT status, body, last_error FROM mail_outbox WHERE id='m1'")
    assert mail["status"] == "dropped" and mail["last_error"] == "restored_from_backup"
    assert "LIVE-TOKEN-1" not in mail["body"], "丢弃的邮件不能在库里留着能用的链接"
    assert not restored.all("SELECT id FROM mail_outbox WHERE status='queued'")

    # the live journal was only read; the restored instance carries it forward
    assert live_journal.read_bytes() == journal_before
    carried = journal.read(into)
    assert {e["kind"] for e in carried} == {"api_key_revoked", "member_removed", "invitation_revoked"}
    assert "重放" in out and "所有人需要重新登录" in out and "丢弃排队中的邮件 1 封" in out


def test_journal_entries_older_than_the_backup_are_not_replayed(instance, tmp_path):
    """Replaying a withdrawal that the backup already reflects is not harmless when a grant came
    after it: a member downgraded and then promoted again before the backup must stay promoted."""
    from synwevo_core import journal
    from synwevo_core.store import Store, now
    snapshot = _snapshot()
    live = Store(instance)
    _people(live, now)
    live.execute("UPDATE memberships SET role='viewer' WHERE user_id='u_alice'")
    journal.record(live, "role_changed", workspace_id="w1", user_id="u_alice", role="viewer")
    live.execute("UPDATE memberships SET role='admin' WHERE user_id='u_alice'")   # promoted again
    _release(live)

    archive = _backup(snapshot, instance, tmp_path / "b")
    into = tmp_path / "isolated"
    assert _restore(snapshot, archive, into, journal_from=instance) == 0
    c = _ro(into / "docweave.sqlite3")
    assert c.execute("SELECT role FROM memberships WHERE user_id='u_alice'").fetchone()[0] == "admin"
    c.close()


def test_the_report_counts_withdrawals_after_the_backup_and_names_voided_attempts_apart(instance, tmp_path, capsys):
    """journal.read() returns the written-ahead lines and the voids whatever their time, for apply()
    to check against the backup's receipts — they are not "withdrawals after the backup". A freeze
    committed and lifted before the backup and a freeze voided before it are read, checked and
    skipped; only the member removed after the backup is counted as replayed, the receipted freeze
    and the voided attempt are each reported on a line of their own, and a kind nothing was
    replayed for is not listed with a 0 among the withdrawals."""
    from synwevo_core import journal
    from synwevo_core.identity import Identity
    from synwevo_core.store import Store, now
    snapshot = _snapshot()
    live = Store(instance)
    _people(live, now)
    ident = Identity(live)
    ident.set_workspace_frozen("w1", True)
    ident.set_workspace_frozen("w1", False)
    with pytest.raises(RuntimeError, match="fixture"):
        with live.tx():
            journal.record_workspace_frozen(live, workspace_id="w1")
            raise RuntimeError("fixture failure after the line")
    _release(live)
    archive = _backup(snapshot, instance, tmp_path / "b")
    live = Store(instance)
    live.execute("DELETE FROM memberships WHERE workspace_id='w1' AND user_id='u_alice'")
    journal.record(live, "member_removed", workspace_id="w1", user_id="u_alice")
    _release(live)

    into = tmp_path / "isolated"
    assert _restore(snapshot, archive, into, journal_from=instance) == 0
    out = capsys.readouterr().out
    assert "备份未包含的撤销记录 1 条（共读取 4 条），已重放：member_removed 1\n" in out, out
    assert "workspace_frozen" not in out and "intent_voided" not in out, out
    assert "备份已包含的预写记录 1 条" in out and "已作废的尝试 1 条" in out, out
    c = _ro(into / "docweave.sqlite3")
    assert c.execute("SELECT status FROM workspaces WHERE id='w1'").fetchone()[0] == "active"
    assert not c.execute("SELECT 1 FROM memberships WHERE user_id='u_alice'").fetchone()
    c.close()


def test_restore_without_a_live_journal_is_refused_unless_said_explicitly(instance, tmp_path, capsys):
    """Silently skipping the journal would bring back every key revoked since the backup."""
    snapshot = _snapshot()
    archive = _backup(snapshot, instance, tmp_path / "b")
    gone = tmp_path / "lost-live-dir"
    into = tmp_path / "isolated"
    assert _restore(snapshot, archive, into, journal_from=gone) == 2
    assert "--no-journal" in capsys.readouterr().err
    assert not (into / "docweave.sqlite3").exists()

    assert _restore(snapshot, archive, into, journal_from=gone, no_journal=True) == 0
    c = _ro(into / "docweave.sqlite3")
    assert c.execute("SELECT COUNT(*) FROM sessions WHERE revoked_at IS NULL").fetchone()[0] == 0
    c.close()


def test_a_torn_withdrawal_stops_restore_before_the_target_changes(instance, tmp_path):
    """A crash may have torn a revocation. Do not install a database while silently losing it."""
    from synwevo_core import journal
    from synwevo_core.store import Store
    snapshot = _snapshot()
    live = Store(instance)
    journal.record(live, "sessions_revoked", user_id="u_before")
    _release(live)
    archive = _backup(snapshot, instance, tmp_path / "b")
    live = Store(instance)
    journal.record(live, "sessions_revoked", user_id="u_after")
    _release(live)
    with (instance / "security-journal.jsonl").open("a", encoding="utf-8") as fh:
        fh.write('{"at": 17999, "kind": "sessions_rev')          # a crash mid-write

    into = tmp_path / "isolated"
    original = journal.path(instance).read_bytes()
    with pytest.raises(ValueError, match="journal.*line 3"):
        _restore(snapshot, archive, into, journal_from=instance)
    assert not (into / "docweave.sqlite3").exists() and not (into / "blobs").exists()
    assert not (into / "security-journal.jsonl").exists() and not list(into.glob(".restore-*"))
    assert journal.path(instance).read_bytes() == original


def test_a_damaged_target_journal_is_preserved_for_recovery(tmp_path):
    """Do not move corruption into the middle of the live journal by appending past it."""
    snapshot = _snapshot()
    target = tmp_path / "security-journal.jsonl"
    target.write_text('{"at": 1, "kind": "sessions_revoked", "user_id": "u1"}\n{"at": 2, "kind": "sess',
                      encoding="utf-8")
    entry = '{"at": 3, "kind": "member_removed", "user_id": "u3", "workspace_id": "w1"}'
    original = target.read_bytes()
    with pytest.raises(ValueError, match="journal.*line 2"):
        snapshot._write_journal(target, [entry], tmp_path)
    assert target.read_bytes() == original


def test_a_failure_before_the_swap_leaves_the_target_untouched(instance, tmp_path, monkeypatch):
    """Reading the journal is the last step that depends on input this restore does not control.
    It has to happen before the database is moved into place, not after."""
    snapshot = _snapshot()
    archive = _backup(snapshot, instance, tmp_path / "b")

    def unreadable(*a, **kw):
        raise OSError("journal unreadable")
    monkeypatch.setattr(snapshot, "_journal_additions", unreadable)
    into = tmp_path / "isolated"
    with pytest.raises(OSError):
        _restore(snapshot, archive, into, journal_from=instance)
    assert not (into / "docweave.sqlite3").exists() and not (into / "blobs").exists()
    assert not list(into.glob(".restore-*"))


def test_only_intact_blobs_enter_the_blob_store(instance, tmp_path, capsys):
    """put_blob never overwrites an existing digest. An unreferenced file in the archive under a
    valid name but with other bytes would be served, from then on, to whoever uploads that
    content — so restore merges only files whose SHA-256 is their name, at blobs/<h[:2]>/<h>."""
    snapshot = _snapshot()
    archive = _backup(snapshot, instance, tmp_path / "b")
    genuine = b"an unreferenced but intact blob"
    g = hashlib.sha256(genuine).hexdigest()
    poisoned = hashlib.sha256(b"what a later upload will contain").hexdigest()
    wrong_dir = "ab" if g[:2] != "ab" else "cd"

    def tamper(d):
        (d / "blobs" / g[:2]).mkdir(exist_ok=True)
        (d / "blobs" / g[:2] / g).write_bytes(genuine)
        (d / "blobs" / poisoned[:2]).mkdir(exist_ok=True)
        (d / "blobs" / poisoned[:2] / poisoned).write_bytes(b"attacker bytes")
        (d / "blobs" / "notes.txt").write_text("not a blob")
        (d / "blobs" / wrong_dir).mkdir(exist_ok=True)
        (d / "blobs" / wrong_dir / g).write_bytes(genuine)       # right name, wrong directory
    broken = _rebuild(archive, tmp_path, tamper)

    into = tmp_path / "isolated"
    assert _restore(snapshot, broken, into, journal_from=instance) == 0
    from synwevo_core.store import Store
    restored = Store(into, migrate=False)
    assert restored.get_blob(g) == genuine
    assert not restored.blob_path(poisoned).exists(), "内容与名字不符的文件不能进 blob 存储"
    assert not (into / "blobs" / "notes.txt").exists()
    assert not (into / "blobs" / wrong_dir / g).exists()
    out = capsys.readouterr().out
    assert "内容的 SHA-256 与名字不符" in out and "notes.txt" in out
    # the application still reads the real content of what the archive references
    for (digest,) in _ro(into / "docweave.sqlite3").execute("SELECT original_blob FROM templates"):
        assert restored.get_blob(digest).startswith(b"content-")


def test_the_restored_copy_is_as_protected_as_the_archive(instance, tmp_path):
    """The archive is 0600 in a 0700 directory; what it restores to must not be less. The
    database holds the OIDC signing keys, the server secret and the sealed MFA seeds."""
    snapshot = _snapshot()
    archive = _backup(snapshot, instance, tmp_path / "b")
    into = tmp_path / "new" / "restored"
    assert _restore(snapshot, archive, into, journal_from=instance) == 0
    assert not (into.stat().st_mode & 0o077), oct(into.stat().st_mode)
    db = into / "docweave.sqlite3"
    assert not (db.stat().st_mode & 0o077), oct(db.stat().st_mode)
    journal_file = into / "security-journal.jsonl"
    assert not journal_file.exists() or not (journal_file.stat().st_mode & 0o077)


def test_a_job_halted_by_a_restore_has_the_counts_every_other_job_has(instance, tmp_path):
    """The fence re-implemented the job counts and drifted: no planned/unplanned, and a job
    cancelled mid-planning showed total = items planned instead of its record count. Checked
    against the method every other job's counts come from."""
    from synwevo_core.store import Store, now
    from synwevo_docs.jobs import JobService
    snapshot = _snapshot()
    live = Store(instance)
    ts = now()
    with live.tx() as t:
        t.execute("INSERT INTO jobs (id,project_id,status,created_at,tenant,run_no,summary) "
                  "VALUES ('jp','p1','running',?,'default',1,?)", (ts, '{"records": 10}'))
        for i, st in enumerate(("success", "pending", "running"), 1):
            t.execute("INSERT INTO job_items (id,job_id,seq,status) VALUES (?,?,?,?)", (f"jp{i}", "jp", i, st))
    _release(live)
    archive = _backup(snapshot, instance, tmp_path / "b")
    into = tmp_path / "isolated"
    assert _restore(snapshot, archive, into, journal_from=instance) == 0

    restored = Store(into)
    stored = restored.one("SELECT counts FROM jobs WHERE id='jp'")["counts"]     # decoded by the store
    assert stored["total"] == 10 and stored["planned"] == 3 and stored["unplanned"] == 7
    assert stored["cancelled"] == 2 and stored["success"] == 1
    # TEST HARNESS: the real method, on an object carrying only the store it reads.
    svc = JobService.__new__(JobService)
    svc.store = restored
    assert svc._update_counts("jp") == stored


def test_verify_rehearses_the_fence_and_names_what_needs_review(instance, tmp_path, capsys):
    """Verification shows what a real restore would do, on the throwaway copy only."""
    from synwevo_core.store import Store, now
    snapshot = _snapshot()
    live = Store(instance)
    live.execute(
        "INSERT INTO api_keys (id,name,tenant,scopes,secret_hash,created_at) "
        "VALUES ('k1','ci','default','read','x',?)", (now(),))
    # Settle the fixture writer before hashing physical database bytes. Leaving its SQLite
    # connection to GC lets a later collection checkpoint WAL pages during the rehearsal.
    _release(live)
    archive = _backup(snapshot, instance, tmp_path / "b")
    before = hashlib.sha256((instance / "docweave.sqlite3").read_bytes()).hexdigest()
    assert snapshot.verify(_args(archive=str(archive), journal_from=str(instance))) == 0
    text = capsys.readouterr().out
    assert "恢复时会" in text and "仍需人工确认" in text and "有效的 API 密钥 1 个" in text
    assert "SYNWEVO_UI_AUTH" in text, "还要提醒认证模式不在库里"
    assert hashlib.sha256((instance / "docweave.sqlite3").read_bytes()).hexdigest() == before, \
        "演练只能动隔离副本"


def test_a_restore_without_the_journal_starts_a_history_its_later_restores_keep(instance, tmp_path, capsys):
    """An in-place `--no-journal` restore keeps the live journal beside the restored database and,
    by choice, replays none of it. A later journalled restore of a backup of that new history must
    not replay what came before it either: a member removed in the abandoned history is back in the
    new one, and stays (journal.EPOCH_KEY). Nothing else happened in the new history — no change to
    the membership, no other withdrawal — so only the epoch tells the old line apart."""
    from synwevo_core import journal
    from synwevo_core.store import Store, now
    snapshot = _snapshot()
    live = Store(instance)
    _people(live, now)
    _release(live)
    b0 = _backup(snapshot, instance, tmp_path / "b0")
    live = Store(instance)
    with live.tx():                                        # the abandoned history: committed, receipted
        live.execute("DELETE FROM memberships WHERE workspace_id='w1' AND user_id='u_alice'")
        journal._intent(live, "member_removed", workspace_id="w1", user_id="u_alice")
    _release(live)
    assert _restore(snapshot, b0, instance, force=True, no_journal=True) == 0
    assert [e["kind"] for e in journal.read(instance)] == ["member_removed"], "the live journal is kept"
    c = _ro(instance / "docweave.sqlite3")
    epoch = float(c.execute("SELECT value FROM meta WHERE key=?", (journal.EPOCH_KEY,)).fetchone()[0])
    assert c.execute("SELECT role FROM memberships WHERE user_id='u_alice'").fetchone()[0] == "admin"
    c.close()
    assert journal.read(instance)[0]["at"] < epoch
    b1 = _backup(snapshot, instance, tmp_path / "b1")
    capsys.readouterr()
    into = tmp_path / "isolated"
    assert _restore(snapshot, b1, into, journal_from=instance) == 0
    out = capsys.readouterr().out
    c = _ro(into / "docweave.sqlite3")
    kept = c.execute("SELECT role FROM memberships WHERE user_id='u_alice'").fetchone()
    c.close()
    assert kept and kept[0] == "admin", "the abandoned history's removal was replayed"
    assert "无日志恢复之前的记录 1 条" in out and "member_removed" not in out, out


class _args:
    def __init__(self, **kw):
        self.__dict__.update(kw)
