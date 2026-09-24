"""Deployment archive security regressions; no server or business data is used."""
import importlib.util
from contextlib import closing
from pathlib import Path
import subprocess
import sys
import tarfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / 'receive.py'
spec = importlib.util.spec_from_file_location('receive', SCRIPT)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class ReleaseArchiveTests(unittest.TestCase):
    def member(self, name, *, kind=tarfile.REGTYPE, size=10):
        item = tarfile.TarInfo(name)
        item.type, item.size = kind, size
        return item

    def test_real_source_paths_are_allowed(self):
        module.validate_members([self.member('apps/docs/server.py'), self.member('.github/workflows/production.yml'), self.member('.env.example')])

    def test_traversal_links_secrets_and_runtime_files_are_refused(self):
        for name in ('../config/runtime.env', '/etc/shadow', 'apps/../../data/a', '.git/config', '.env', 'a/.env.prod', 'data/docweave.sqlite3', '.local/secret', 'node_modules/pkg/a'):
            with self.subTest(name=name), self.assertRaises(ValueError):
                module.validate_members([self.member(name)])
        for kind in (tarfile.SYMTYPE, tarfile.LNKTYPE, tarfile.CHRTYPE, tarfile.FIFOTYPE):
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                module.validate_members([self.member('escape', kind=kind)])

    def test_duplicate_paths_and_archive_bombs_are_refused(self):
        for items in ([self.member('same'), self.member('same')], [self.member('huge', size=151 * 1024 * 1024)], [self.member(str(i)) for i in range(10001)]):
            with self.assertRaises(ValueError):
                module.validate_members(items)

    def test_forced_ssh_command_rejects_shell_and_partial_sha(self):
        import os
        for command in ('', 'bash', 'deploy abcdef', 'deploy ' + 'a' * 40 + '; id', 'deploy ' + 'a' * 40 + '\n'):
            run = subprocess.run([sys.executable, str(SCRIPT)], env={**os.environ, 'SSH_ORIGINAL_COMMAND': command}, input=b'', capture_output=True)
            self.assertNotEqual(run.returncode, 0)
            self.assertIn(b'Only deploy', run.stderr)


class DeploymentRecoveryTests(unittest.TestCase):
    def data(self, root):
        import sqlite3
        data = root / 'data'
        (data / 'blobs').mkdir(parents=True)
        (data / 'blobs/original').write_bytes(b'synthetic-document')
        with closing(sqlite3.connect(data / 'docweave.sqlite3')) as db, db:
            db.execute('CREATE TABLE jobs (status TEXT)')
            db.execute("INSERT INTO jobs VALUES ('completed')")
        return data

    def test_pending_work_refuses_deployment(self):
        import tempfile, sqlite3
        with tempfile.TemporaryDirectory() as temp:
            data = self.data(Path(temp))
            module.require_idle(data)
            with closing(sqlite3.connect(data / 'docweave.sqlite3')) as db, db:
                db.execute("UPDATE jobs SET status='running'")
            with self.assertRaisesRegex(RuntimeError, 'pending work'):
                module.require_idle(data)

    def test_candidate_failure_preserves_prior_database_and_blobs(self):
        import tempfile, json, sqlite3
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            data = self.data(root)
            (root / 'config').mkdir()
            (root / 'current.json').write_text(json.dumps({'image': 'synwevo:old'}))
            commands = []
            def runner(args, *, image, **kw):
                commands.append((args, image))
                if image == 'synwevo:new' and 'up' in args:
                    (data / 'blobs/original').write_bytes(b'candidate-change')
                    with closing(sqlite3.connect(data / 'docweave.sqlite3')) as db, db:
                        db.execute("UPDATE jobs SET status='failed'")
                    raise subprocess.CalledProcessError(1, args)
            with patch.object(module, 'ROOT', root), patch.object(module, 'run', runner):
                with self.assertRaises(subprocess.CalledProcessError):
                    module.promote('synwevo:new')
            self.assertEqual((data / 'blobs/original').read_bytes(), b'synthetic-document')
            with closing(sqlite3.connect(data / 'docweave.sqlite3')) as db, db:
                self.assertEqual(db.execute('SELECT status FROM jobs').fetchone()[0], 'completed')
            self.assertEqual(json.loads((root / 'current.json').read_text())['image'], 'synwevo:old')
            self.assertTrue(list((root / 'backups').glob('*/failed-candidate-data/blobs/original')))
            self.assertEqual(commands[-1][1], 'synwevo:old')
            self.assertIn('gateway', commands[-1][0])


if __name__ == '__main__':
    unittest.main()
