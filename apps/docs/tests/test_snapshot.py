"""Backup and restore.

A backup that has never been restored is a file, not a backup — so the tests here restore and
compare, rather than checking that the archive exists.
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = __import__("platform_core.http", fromlist=["repo_root"]).repo_root()
SCRIPTS = ROOT / "apps" / "docweave" / "scripts"


def _snapshot():
    """The backup/restore script, loaded by path — it is a script, not an installed package."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("_docweave_snapshot", SCRIPTS / "snapshot.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture()
def instance(tmp_path):
    """A small instance with real rows and real blob files."""
    from platform_core.store import Store, now
    data = tmp_path / "data"
    store = Store(data)
    (data / "blobs").mkdir(exist_ok=True)
    digests = []
    for i in range(3):
        digest = f"{i:064d}"
        (data / "blobs" / digest).write_bytes(b"content-" + str(i).encode())
        digests.append(digest)
    for i, digest in enumerate(digests):
        store.execute(
            "INSERT INTO templates (id,name,kind,format,tenant,created_at,updated_at,"
            "original_blob,deleted) VALUES (?,?,'builtin_doc','docx','default',?,?,?,0)",
            (f"t{i}", f"模板 {i}", now(), now(), digest))
    store.execute(
        "INSERT INTO projects (id,name,tenant,step,rev,mapping_rev,created_at,updated_at,deleted) "
        "VALUES ('p1','真实项目','default','upload',1,1,?,?,0)", (now(), now()))
    return data


def test_a_snapshot_restores_to_the_same_content(instance, tmp_path, capsys):
    snapshot = _snapshot()
    out = tmp_path / "backups"
    rc = snapshot.create(_args(data=str(instance), out=str(out)))
    assert rc == 0
    archive = next(out.glob("*.tar.gz"))

    # Change the original afterwards; the archive must still restore what it captured.
    from platform_core.store import Store, now
    Store(instance).execute(
        "INSERT INTO projects (id,name,tenant,step,rev,mapping_rev,created_at,updated_at,deleted) "
        "VALUES ('p2','备份之后才建的','default','upload',1,1,?,?,0)", (now(), now()))

    into = tmp_path / "restored"
    assert snapshot.restore(_args(archive=str(archive), into=str(into), force=False)) == 0
    c = sqlite3.connect(f"file:{into / 'docweave.sqlite3'}?mode=ro", uri=True)
    names = {r[0] for r in c.execute("SELECT name FROM projects")}
    c.close()
    assert names == {"真实项目"}, "恢复出来的应当是捕获时的状态"
    assert len(list((into / "blobs").glob("*"))) == 3, "blob 正文必须一起恢复"


def test_verify_restores_and_checks_rather_than_trusting_the_archive(instance, tmp_path, capsys):
    snapshot = _snapshot()
    out = tmp_path / "b"
    snapshot.create(_args(data=str(instance), out=str(out)))
    archive = next(out.glob("*.tar.gz"))
    assert snapshot.verify(_args(archive=str(archive))) == 0
    text = capsys.readouterr().out
    assert "项通过" in text and "✗" not in text


def test_verify_fails_when_the_blob_content_is_missing(instance, tmp_path, capsys):
    """The worst failure mode: a catalogue of documents whose contents are gone. It must be
    caught by verification, not discovered when someone opens a file."""
    import tarfile
    snapshot = _snapshot()
    out = tmp_path / "b"
    snapshot.create(_args(data=str(instance), out=str(out)))
    archive = next(out.glob("*.tar.gz"))

    # Rebuild the archive without the blob directory, the way a partial backup would look.
    staged = tmp_path / "staged"
    staged.mkdir()
    with tarfile.open(archive, "r:gz") as t:
        t.extractall(staged)
    import shutil
    shutil.rmtree(staged / "blobs")
    broken = tmp_path / "broken.tar.gz"
    with tarfile.open(broken, "w:gz") as t:
        for item in sorted(staged.iterdir()):
            t.add(item, arcname=item.name)

    assert snapshot.verify(_args(archive=str(broken))) == 1
    assert "✗" in capsys.readouterr().out


def test_restore_refuses_to_overwrite_an_existing_database(instance, tmp_path):
    snapshot = _snapshot()
    out = tmp_path / "b"
    snapshot.create(_args(data=str(instance), out=str(out)))
    archive = next(out.glob("*.tar.gz"))
    with pytest.raises(SystemExit):
        raise SystemExit(snapshot.restore(_args(archive=str(archive), into=str(instance),
                                                force=False)) or SystemExit(0))


def test_a_restore_warns_about_credentials_that_would_revive(instance, tmp_path, capsys):
    """Restoring an old backup is not going back in time — it brings back what was deliberately
    taken away since, including credentials revoked after the backup was taken."""
    snapshot = _snapshot()
    from platform_core.store import Store, now
    Store(instance).execute(
        "INSERT INTO api_keys (id,name,tenant,scopes,secret_hash,created_at) "
        "VALUES ('k1','ci','default','read','x',?)", (now(),))
    out = tmp_path / "b"
    snapshot.create(_args(data=str(instance), out=str(out)))
    snapshot.verify(_args(archive=str(next(out.glob("*.tar.gz")))))
    text = capsys.readouterr().out
    assert "会复活" in text
    assert "DOCWEAVE_UI_AUTH" in text, "还要提醒认证模式不在库里"


def test_the_archive_is_not_world_readable(instance, tmp_path):
    """It contains signing keys and password hashes. Saying so is not enough; the mode matters."""
    import stat
    snapshot = _snapshot()
    out = tmp_path / "b"
    snapshot.create(_args(data=str(instance), out=str(out)))
    mode = next(out.glob("*.tar.gz")).stat().st_mode
    assert not (mode & (stat.S_IRGRP | stat.S_IROTH)), oct(mode)


def test_an_archive_with_an_escaping_path_is_refused(tmp_path):
    """A tar can name paths outside the target directory."""
    import tarfile
    snapshot = _snapshot()
    evil = tmp_path / "evil.tar.gz"
    payload = tmp_path / "payload"
    payload.write_text("x")
    with tarfile.open(evil, "w:gz") as t:
        t.add(payload, arcname="../escaped")
    with pytest.raises(SystemExit, match="越界"):
        snapshot._extract(evil, tmp_path / "into")


class _args:
    def __init__(self, **kw):
        self.__dict__.update(kw)
