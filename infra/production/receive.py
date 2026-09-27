#!/usr/bin/env python3
"""Root-owned forced SSH command. Accept only `deploy <full git SHA>` plus source tar.gz.

Install outside release directories. The deployment key can submit application code; it cannot
run an arbitrary host shell, forward ports, or replace this script/Compose/runtime config.
"""
from __future__ import annotations

import fcntl
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys
import tarfile
import time

ROOT = Path('/opt/synwevo')
COMPOSE = ['docker', 'compose', '-f', str(ROOT / 'config/compose.yaml')]
SERVICES = ['accounts', 'docs', 'portal', 'messages']


def run(args, *, image, **kwargs):
    return subprocess.run(args, check=True, env={**os.environ, 'IMAGE': image}, **kwargs)


def validate_members(members):
    if len(members) > 10000 or sum(m.size for m in members) > 150 * 1024 * 1024:
        raise ValueError('Release archive exceeds limit')
    names = set()
    for m in members:
        p = PurePosixPath(m.name)
        if (p.is_absolute() or '..' in p.parts or not p.parts or m.name in names
                or not (m.isfile() or m.isdir()) or any(part in ('.git', 'data', '.local', 'node_modules', '.venv') for part in p.parts)
                or any(part.startswith('.env') and part != '.env.example' for part in p.parts)):
            raise ValueError(f'Unsafe release archive entry: {m.name!r}')
        names.add(m.name)


def verify_data(path):
    # Standalone offline backup verification, not a second application database opener/migrator.
    import sqlite3
    with closing(sqlite3.connect(f'file:{path / "docweave.sqlite3"}?mode=ro', uri=True)) as db:
        if db.execute('PRAGMA quick_check').fetchone() != ('ok',):
            raise RuntimeError('Backup SQLite quick_check failed')
    count = 0
    digest = hashlib.sha256()
    for p in sorted((path / 'blobs').rglob('*')):
        if p.is_file():
            with p.open('rb') as stream:
                h = hashlib.file_digest(stream, 'sha256').hexdigest()
            digest.update(str(p.relative_to(path)).encode() + h.encode())
            count += 1
    return {'blob_files': count, 'blob_manifest_sha256': digest.hexdigest()}


def require_idle(data):
    """Do not interrupt jobs or make rollback replay a queued external side effect."""
    import sqlite3
    with closing(sqlite3.connect(f'file:{data / "docweave.sqlite3"}?mode=ro', uri=True)) as db:
        checks = {
            'jobs': "status IN ('queued','running','cancelling')",
            'webhook_deliveries': "status='pending'",
            'mail_outbox': "status='queued'",
        }
        tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        busy = {t: db.execute(f'SELECT count(*) FROM {t} WHERE {condition}').fetchone()[0]
                for t, condition in checks.items() if t in tables}
        if any(busy.values()):
            raise RuntimeError(f'Deployment refused: pending work must finish first: {busy}')


def promote(image):
    state_file = ROOT / 'current.json'
    if not state_file.exists():
        raise RuntimeError('Initial data migration and current.json must be installed by the operator first')
    old = json.loads(state_file.read_text())['image']
    data = ROOT / 'data'
    if not (data / 'docweave.sqlite3').is_file():
        raise RuntimeError('Refusing to initialize an empty production database')
    stamp = time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())
    backup = ROOT / 'backups' / stamp
    backup.mkdir(mode=0o700, parents=True)
    # Quiesce HTTP AND all writers before copying. Public requests remain unavailable until the
    # new services pass checks, so automatic rollback cannot discard writes made after release.
    run(COMPOSE + ['stop', 'gateway'], image=old)
    try:
        require_idle(data)
    except BaseException:
        run(COMPOSE + ['start', 'gateway'], image=old)
        raise
    run(COMPOSE + ['stop', *SERVICES], image=old)
    copied = False
    try:
        require_idle(data)
        subprocess.run(['cp', '-a', str(data), str(backup / 'data')], check=True)
        before = verify_data(data)
        if before != verify_data(backup / 'data'):
            raise RuntimeError('Backup blob manifest mismatch')
        (backup / 'manifest.json').write_text(json.dumps({'image': old, **before}, indent=2))
        copied = True
        run(COMPOSE + ['up', '-d', '--wait', '--wait-timeout', '180', *SERVICES], image=image)
        for service in SERVICES:
            run(COMPOSE + ['exec', '-T', service, 'python', '-c',
                'import urllib.request; assert urllib.request.urlopen("http://127.0.0.1:8765/readyz",timeout=5).status==200'], image=image)
    except BaseException:
        run(COMPOSE + ['stop', *SERVICES], image=image)
        if copied:
            failed = backup / 'failed-candidate-data'
            data.rename(failed)
            subprocess.run(['cp', '-a', str(backup / 'data'), str(data)], check=True)
        run(COMPOSE + ['up', '-d', '--wait', '--wait-timeout', '180', *SERVICES], image=old)
        run(COMPOSE + ['up', '-d', '--force-recreate', '--wait', '--wait-timeout', '60', 'gateway'], image=old)
        raise
    # The release becomes public here. Do NOT restore a database automatically after this point.
    tmp = ROOT / 'current.next.json'
    tmp.write_text(json.dumps({'image': image, 'previous_image': old, 'backup': str(backup), 'at': stamp}, indent=2))
    tmp.replace(state_file)
    (ROOT / 'config/.env').write_text(f'IMAGE={image}\n')
    run(COMPOSE + ['up', '-d', '--force-recreate', '--wait', '--wait-timeout', '60', 'gateway'], image=image)
    print(json.dumps({'deployed': image, 'backup': str(backup)}), flush=True)


def main():
    os.umask(0o077)
    command = os.environ.get('SSH_ORIGINAL_COMMAND', '')
    match = re.fullmatch(r'deploy ([0-9a-f]{40})', command)
    if not match:
        raise SystemExit('Only deploy <40-character commit SHA> is permitted')
    sha = match[1]
    lock = (ROOT / 'deploy.lock').open('a')
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    incoming = ROOT / 'incoming.tar.gz'
    size = 0
    with incoming.open('wb') as out:
        while chunk := sys.stdin.buffer.read(1024 * 1024):
            size += len(chunk)
            if size > 50 * 1024 * 1024:
                raise RuntimeError('Compressed release exceeds 50 MiB')
            out.write(chunk)
    digest = hashlib.sha256(incoming.read_bytes()).hexdigest()
    release = ROOT / 'releases' / sha
    release.mkdir(mode=0o700, parents=True, exist_ok=True)
    marker = release / '.archive-sha256'
    if marker.exists() and marker.read_text() != digest:
        raise RuntimeError('Same commit SHA submitted with different source archive')
    with tarfile.open(incoming, 'r:gz') as archive:
        validate_members(archive.getmembers())
        archive.extractall(release, filter='data')
    marker.write_text(digest)
    incoming.unlink()
    image = f'synwevo:{sha}'
    run(['docker', 'build', '--label', f'org.opencontainers.image.revision={sha}', '-f',
         'infra/Dockerfile', '-t', image, '.'], image=image, cwd=release)
    promote(image)


if __name__ == '__main__':
    main()
