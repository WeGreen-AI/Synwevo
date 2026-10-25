"""The release archive check against throwaway Git repositories; no network, server or live data.

check_release_archive.py builds `git archive HEAD` as the deploy job does and applies the host
receiver's own `validate_members`. These fixture repositories hold only synthetic files.
"""
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import unittest

HERE = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('check_release_archive', HERE / 'check_release_archive.py')
check = importlib.util.module_from_spec(spec)
spec.loader.exec_module(check)


def git_env(home):
    env = {k: v for k, v in os.environ.items() if not k.startswith('GIT_')}
    env.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM='1', HOME=str(home),
               GIT_AUTHOR_NAME='Fixture', GIT_AUTHOR_EMAIL='fixture@example.test',
               GIT_COMMITTER_NAME='Fixture', GIT_COMMITTER_EMAIL='fixture@example.test')
    return env


class ReleaseArchiveCheckTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix='synwevo-release-archive-fixture-')
        self.addCleanup(temp.cleanup)
        self.tmp = Path(temp.name)
        self.repo = self.tmp / 'repo'
        self.repo.mkdir()
        self.env = git_env(self.tmp)
        self.git('init', '-q', '-b', 'dev')
        self.git('config', 'core.symlinks', 'true')
        self.write({'apps/x.py': 'x = 1\n', 'docs/a.md': 'a\n', '.env.example': 'KEY=placeholder\n'})

    def git(self, *args):
        subprocess.run(['git', '-c', 'commit.gpgsign=false', *args], cwd=self.repo, env=self.env,
                       check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def write(self, files):
        for rel, text in files.items():
            path = self.repo / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding='utf-8')

    def commit(self):
        self.git('add', '-A')
        self.git('commit', '-q', '-m', 'fixture')
        return check.problems(self.repo)

    def test_regular_files_pass(self):
        self.assertEqual(self.commit(), [])

    def test_links_anywhere_are_refused(self):
        (self.repo / 'docs/screenshots').mkdir(parents=True)
        os.symlink('../../docs/a.md', self.repo / 'docs/screenshots/readme-link.pdf')
        os.symlink('/etc', self.repo / 'docs/dist')
        found = self.commit()
        self.assertEqual(len(found), 2, found)
        self.assertTrue(any('docs/screenshots/readme-link.pdf' in line for line in found), found)
        self.assertTrue(any("'docs/dist'" in line for line in found), found)

    def test_paths_the_receiver_reserves_are_refused(self):
        # A record in a directory named `data` is still refused by the receiver.
        self.write({'docs/quality/data/evidence.md': 'e\n', 'docs/.env.local': 'x\n'})
        found = self.commit()
        self.assertTrue(any('docs/quality/data' in line for line in found), found)
        self.assertTrue(any('docs/.env.local' in line for line in found), found)

    def test_export_ignored_paths_are_not_in_the_archive(self):
        (self.repo / 'docs/evidence').mkdir(parents=True)
        os.symlink('../a.md', self.repo / 'docs/evidence/link.md')
        self.write({'.gitattributes': 'docs/evidence/** export-ignore\n'})
        self.assertEqual(self.commit(), [])

    def test_size_limits_are_the_receivers(self):
        members = []
        for i in range(10001):
            item = tarfile.TarInfo(f'f{i}')
            item.size = 1
            members.append(item)
        with self.assertRaises(ValueError):
            check.receiver().validate_members(members)
        source = (HERE / 'receive.py').read_text(encoding='utf-8')
        self.assertIn('size > 50 * 1024 * 1024', source, 'the compressed limit is copied from receive.main')
        self.assertEqual(check.COMPRESSED_LIMIT, 50 * 1024 * 1024)

    def test_command_exit_status(self):
        self.commit()
        script = HERE / 'check_release_archive.py'
        ok = subprocess.run([sys.executable, str(script), str(self.repo)], capture_output=True, text=True, env=self.env)
        self.assertEqual(ok.returncode, 0, ok.stdout + ok.stderr)
        os.symlink('docs/a.md', self.repo / 'link')
        self.commit()
        bad = subprocess.run([sys.executable, str(script), str(self.repo)], capture_output=True, text=True, env=self.env)
        self.assertEqual(bad.returncode, 1, bad.stdout + bad.stderr)
        self.assertIn("'link'", bad.stdout)
        missing = subprocess.run([sys.executable, str(script), str(self.tmp / 'not-a-checkout')],
                                 capture_output=True, text=True, env=self.env)
        self.assertEqual(missing.returncode, 1, missing.stdout + missing.stderr)
        self.assertIn('cannot build the release archive', missing.stdout)


if __name__ == '__main__':
    unittest.main()
