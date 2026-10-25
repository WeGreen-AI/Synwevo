"""The production workflow's branch model, run against its own shell text; no network or live data.

`verify` is the status check the branch rules require on dev and main, so it runs on every pull
request into either branch and may skip its heavy steps only when every changed path is provably a
record. The classifier, the deploy guard and the main sync guard are executed exactly as written in
the workflow files: the classifier in throwaway Git repositories built here, the deploy guard against
a `gh` test double (a fixture that answers fixed API responses and applies the real `--jq` filter).
"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[3]
WORKFLOWS = ROOT / '.github' / 'workflows'
REPO = 'WeGreen-AI/Synwevo'


def load(name):
    doc = yaml.safe_load((WORKFLOWS / name).read_text(encoding='utf-8'))
    # YAML 1.1 reads the bare key `on` as the boolean true.
    doc['on'] = doc.pop(True) if True in doc else doc['on']
    return doc


def step(job, *, step_id=None, name=None):
    for item in job['steps']:
        if (step_id and item.get('id') == step_id) or (name and item.get('name') == name):
            return item
    raise AssertionError(f'no step {step_id or name}')


def git_env(home):
    env = {k: v for k, v in os.environ.items() if not k.startswith('GIT_')}
    env.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM='1', HOME=str(home),
               GIT_AUTHOR_NAME='Fixture', GIT_AUTHOR_EMAIL='fixture@example.test',
               GIT_COMMITTER_NAME='Fixture', GIT_COMMITTER_EMAIL='fixture@example.test')
    return env


class BranchModelTests(unittest.TestCase):
    def setUp(self):
        self.wf = load('production.yml')

    def test_every_pull_request_into_dev_or_main_runs_verify(self):
        pull = self.wf['on']['pull_request']
        self.assertEqual(sorted(pull['branches']), ['dev', 'main'])
        self.assertNotIn('paths', pull, 'a path filter would leave the required check waiting forever')
        self.assertNotIn('paths-ignore', pull)

    def test_only_dev_pushes_run_and_records_stay_out(self):
        push = self.wf['on']['push']
        self.assertEqual(push['branches'], ['dev'], 'a push to main must run nothing')
        self.assertEqual(push['paths'], ['**', '!docs/**', 'docs/integration/**', 'docs/operations.md'])
        self.assertIn('workflow_dispatch', self.wf['on'])

    def test_required_check_name_is_verify(self):
        verify = self.wf['jobs']['verify']
        self.assertNotIn('name', verify)
        self.assertNotIn('strategy', verify)
        self.assertNotIn('if', verify, 'a skipped job would let any change through the required check')

    def test_heavy_steps_run_unless_the_classifier_said_records_only(self):
        steps = self.wf['jobs']['verify']['steps']
        self.assertEqual(steps[0]['with']['fetch-depth'], 2)
        self.assertEqual(steps[1]['id'], 'changes')
        records = [s for s in steps[2:] if s.get('if') == "steps.changes.outputs.code == 'false'"]
        self.assertEqual(len(records), 1)
        self.assertIn('check_brand.py', records[0]['run'])
        self.assertIn('check_boundaries.py', records[0]['run'])
        # Both paths check the archive a deploy sends against the receiver's own rules.
        self.assertIn('python3 infra/production/check_release_archive.py', records[0]['run'])
        for item in steps[2:]:
            if item is records[0]:
                continue
            # `!= 'false'`: an empty or missing output still means the full verify.
            self.assertIn("steps.changes.outputs.code != 'false'", item.get('if', ''), item)
        build = step(self.wf['jobs']['verify'], name='Verify and build')['run']
        self.assertIn('python infra/production/check_release_archive.py', build)
        self.assertIn('python -m pytest apps/docs/tests/test_licensing.py', build)
        self.assertIn('python -m unittest discover -s infra/production/tests', build)

    def test_deploy_only_from_dev_of_this_repository(self):
        deploy = self.wf['jobs']['deploy']
        gate = ' '.join(deploy['if'].split())
        self.assertIn(f"github.repository == '{REPO}'", gate)
        self.assertIn("github.ref == 'refs/heads/dev'", gate)
        self.assertIn("(github.event_name == 'push' || github.event_name == 'workflow_dispatch')", gate)
        self.assertNotIn('main', gate)
        self.assertEqual(deploy['environment'], 'production')
        self.assertEqual(deploy['needs'], 'verify')
        self.assertEqual(deploy['permissions'], {'contents': 'read', 'deployments': 'read'})
        self.assertEqual(deploy['steps'][0]['name'], 'Refuse a commit older than the one in production')
        deploy_step = step(deploy, name='Deploy through restricted SSH command')
        self.assertIn('tail -n 30', deploy_step['run'])
        self.assertIn('> "$log" 2>&1', deploy_step['run'])

    def test_only_deploying_runs_share_the_production_group(self):
        group = self.wf['concurrency']['group']
        self.assertIn("(github.event_name != 'pull_request' && github.ref == 'refs/heads/dev') && 'synwevo-production'", group)
        self.assertIn("format('synwevo-verify-{0}', github.ref)", group)


class RecordsOnlyClassifierTests(unittest.TestCase):
    """Run the classifier step on a test merge commit: parent 1 is the base, parent 2 the head."""

    BASE = {'apps/x.py': 'x = 1\n', 'docs/a.md': 'a\n', 'docs/operations.md': 'ops\n',
            'docs/integration/openapi.yaml': 'openapi: 3.1.0\n', 'README.md': 'readme\n'}

    @classmethod
    def setUpClass(cls):
        cls.script = step(load('production.yml')['jobs']['verify'], step_id='changes')['run']

    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix='synwevo-classifier-fixture-')
        self.addCleanup(temp.cleanup)
        self.tmp = Path(temp.name)
        self.repo = self.tmp / 'repo'
        self.env = git_env(self.tmp)

    def git(self, *args):
        subprocess.run(['git', '-c', 'commit.gpgsign=false', *args], cwd=self.repo, env=self.env,
                       check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def write(self, files):
        for rel, text in files.items():
            path = self.repo / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding='utf-8')

    def merge_commit(self, change, before=None):
        """Base commit, a topic commit made by `change`, then a no-ff merge of topic into base.

        (Not a branch called `head`: on a case-insensitive file system it shadows HEAD.)
        """
        self.repo.mkdir()
        self.git('init', '-q', '-b', 'base')
        self.git('config', 'core.symlinks', 'true')
        self.write(self.BASE)
        if before:
            before()
        self.git('add', '-A')
        self.git('commit', '-q', '-m', 'base')
        self.git('checkout', '-q', '-b', 'topic')
        change()
        self.git('add', '-A')
        self.git('commit', '-q', '--allow-empty', '-m', 'head')
        self.git('checkout', '-q', 'base')
        self.git('merge', '-q', '--no-ff', '-m', 'test merge', 'topic')

    def classify(self, event='pull_request'):
        output = self.tmp / 'output'
        runner_temp = self.tmp / 'runner'
        runner_temp.mkdir(exist_ok=True)
        output.write_text('')
        env = {**self.env, 'GITHUB_EVENT_NAME': event, 'GITHUB_OUTPUT': str(output),
               'RUNNER_TEMP': str(runner_temp)}
        # Actions runs an unspecified shell as `bash -e {0}`.
        done = subprocess.run(['bash', '-e', '-c', self.script], cwd=self.repo, env=env,
                              capture_output=True, text=True)
        self.assertEqual(done.returncode, 0, done.stderr)
        lines = [l for l in output.read_text().splitlines() if l.startswith('code=')]
        self.assertEqual(len(lines), 1, lines)
        return lines[0].split('=', 1)[1]

    def test_records_only_change_skips_the_heavy_steps(self):
        self.merge_commit(lambda: self.write({'docs/quality/new.md': 'n\n', 'docs/a.md': 'a2\n'}))
        self.assertEqual(self.classify(), 'false')

    def test_code_change_runs_everything(self):
        self.merge_commit(lambda: self.write({'apps/x.py': 'x = 2\n'}))
        self.assertEqual(self.classify(), 'true')

    def test_mixed_change_runs_everything(self):
        self.merge_commit(lambda: self.write({'docs/a.md': 'a2\n', 'apps/y.py': 'y = 1\n'}))
        self.assertEqual(self.classify(), 'true')

    def test_empty_diff_runs_everything(self):
        self.merge_commit(lambda: None)
        self.assertEqual(self.classify(), 'true')

    def test_600_paths_with_one_early_code_path_run_everything(self):
        # The pipe-based draft read a long list as records-only once grep -q stopped early.
        files = {f'docs/records/{i:04d}.md': f'{i}\n' for i in range(599)}
        files['.github/extra.txt'] = 'sorts before docs/\n'
        self.merge_commit(lambda: self.write(files))
        self.assertEqual(self.classify(), 'true')

    def test_600_records_only_paths_have_no_size_cutoff(self):
        self.merge_commit(lambda: self.write({f'docs/records/{i:04d}.md': f'{i}\n' for i in range(600)}))
        self.assertEqual(self.classify(), 'false')

    def test_3001_paths_with_one_code_path_run_everything(self):
        files = {f'docs/records/{i:04d}.md': f'{i}\n' for i in range(3000)}
        files['apps/late.py'] = 'z = 1\n'
        self.merge_commit(lambda: self.write(files))
        self.assertEqual(self.classify(), 'true')

    def test_docs_that_tests_read_count_as_code(self):
        for rel in ('docs/operations.md', 'docs/integration/openapi.yaml', 'docs/integration/new.json'):
            with self.subTest(rel=rel):
                if self.repo.exists():
                    shutil.rmtree(self.repo)
                self.merge_commit(lambda: self.write({rel: 'changed\n'}))
                self.assertEqual(self.classify(), 'true')

    def test_moving_code_into_docs_counts_both_sides(self):
        def move():
            self.git('mv', 'apps/x.py', 'docs/x.py')
        self.merge_commit(move)
        self.assertEqual(self.classify(), 'true')

    def test_deleting_code_counts_as_code(self):
        self.merge_commit(lambda: (self.repo / 'apps/x.py').unlink())
        self.assertEqual(self.classify(), 'true')

    def test_links_and_submodules_under_docs_count_as_code(self):
        # A link under docs/ is not a record: the release archive carries it and the receiver refuses
        # it (docs/screenshots and any `dist` directory are pruned by the walking guards).
        cases = {
            'link to a file, with a skipped suffix': lambda: os.symlink('../../README.md', self.repo / 'docs/shots/readme.pdf'),
            'absolute link named dist': lambda: os.symlink('/etc', self.repo / 'docs/quality/dist'),
            'submodule': self.nested_repository,
        }
        for label, make in cases.items():
            with self.subTest(label):
                if self.repo.exists():
                    shutil.rmtree(self.repo)

                def change():
                    (self.repo / 'docs/shots').mkdir(parents=True, exist_ok=True)
                    (self.repo / 'docs/quality').mkdir(parents=True, exist_ok=True)
                    make()
                self.merge_commit(change)
                self.assertEqual(self.classify(), 'true')

    def nested_repository(self):
        """An embedded repository, which `git add -A` records as a submodule entry (mode 160000)."""
        sub = self.repo / 'docs/sub'
        sub.mkdir(parents=True)
        (sub / 'x.md').write_text('x\n', encoding='utf-8')
        for args in (('init', '-q'), ('add', 'x.md'), ('commit', '-q', '-m', 'sub')):
            subprocess.run(['git', '-c', 'commit.gpgsign=false', *args], cwd=sub, env=self.env, check=True,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def test_removing_a_link_under_docs_counts_as_code(self):
        def base_link():
            os.symlink('a.md', self.repo / 'docs/link.md')
        self.merge_commit(lambda: None, before=base_link)
        self.git('checkout', '-q', '-b', 'unlink')
        (self.repo / 'docs/link.md').unlink()
        self.git('add', '-A')
        self.git('commit', '-q', '-m', 'unlink')
        self.git('checkout', '-q', 'base')
        self.git('merge', '-q', '--no-ff', '-m', 'test merge', 'unlink')
        self.assertEqual(self.classify(), 'true')

    def test_executable_record_stays_a_record(self):
        self.merge_commit(lambda: (self.repo / 'docs/a.md').chmod(0o755))
        self.assertEqual(self.classify(), 'false')

    def test_non_ascii_record_names_stay_records_and_quoted_names_do_not(self):
        self.merge_commit(lambda: self.write({'docs/记录.md': 'r\n'}))
        self.assertEqual(self.classify(), 'false')
        shutil.rmtree(self.repo)
        self.merge_commit(lambda: self.write({'docs/tab\tname.md': 't\n'}))
        self.assertEqual(self.classify(), 'true')

    def test_not_a_pull_request_or_not_a_merge_commit_runs_everything(self):
        self.merge_commit(lambda: self.write({'docs/a.md': 'a2\n'}))
        self.assertEqual(self.classify(event='push'), 'true')
        self.assertEqual(self.classify(event='workflow_dispatch'), 'true')
        self.git('checkout', '-q', 'topic')         # a single-parent commit: no base to compare with
        self.assertEqual(self.classify(), 'true')

    def test_unreadable_temporary_directory_runs_everything(self):
        self.merge_commit(lambda: self.write({'docs/a.md': 'a2\n'}))
        output = self.tmp / 'output'
        env = {**self.env, 'GITHUB_EVENT_NAME': 'pull_request', 'GITHUB_OUTPUT': str(output),
               'RUNNER_TEMP': str(self.tmp / 'missing')}
        done = subprocess.run(['bash', '-e', '-c', self.script], cwd=self.repo, env=env,
                              capture_output=True, text=True)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn('code=true', output.read_text())


GH_DOUBLE = '''#!{python}
"""Test double for `gh api ENDPOINT [--jq FILTER]`: fixed responses from GH_FIXTURES, real jq."""
import json, os, subprocess, sys
args = sys.argv[1:]
assert args[0] == 'api', args
endpoint = args[1]
jq = args[args.index('--jq') + 1] if '--jq' in args else '.'
with open(os.environ['GH_FIXTURES']) as f:
    fixtures = json.load(f)
with open(os.environ['GH_CALLS'], 'a') as f:
    f.write(endpoint + '\\n')
if fixtures.get(endpoint) is None:
    sys.stderr.write('gh: HTTP 502 (test double)\\n')
    sys.exit(1)
done = subprocess.run(['jq', '-r', jq], input=json.dumps(fixtures[endpoint]), text=True)
sys.exit(done.returncode)
'''


@unittest.skipUnless(shutil.which('jq'), 'the deploy guard test needs jq, as gh --jq does')
class DeployGuardTests(unittest.TestCase):
    OLD, PROD, NEW = 'a' * 40, 'b' * 40, 'c' * 40

    @classmethod
    def setUpClass(cls):
        deploy = load('production.yml')['jobs']['deploy']
        cls.script = step(deploy, name='Refuse a commit older than the one in production')['run']

    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix='synwevo-deploy-guard-fixture-')
        self.addCleanup(temp.cleanup)
        self.tmp = Path(temp.name)
        bin_dir = self.tmp / 'bin'
        bin_dir.mkdir()
        gh = bin_dir / 'gh'
        gh.write_text(GH_DOUBLE.format(python=sys.executable))
        gh.chmod(0o755)
        self.path = f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}"

    def fixtures(self, *, relation='ahead', head=None, deployments='default', statuses=None):
        api = f'repos/{REPO}'
        current = self.NEW
        if deployments == 'default':
            # Newest first: this job's own (in progress), production's, an older superseded one.
            deployments = [{'id': 3, 'sha': current}, {'id': 2, 'sha': self.PROD}, {'id': 1, 'sha': self.OLD}]
        statuses = statuses or {3: [{'state': 'in_progress'}],
                                2: [{'state': 'success'}, {'state': 'in_progress'}],
                                1: [{'state': 'inactive'}, {'state': 'success'}]}
        data = {f'{api}/deployments?environment=production&per_page=100': deployments,
                f'{api}/compare/{self.PROD}...{current}?per_page=1': {'status': relation},
                f'{api}/branches/dev': {'commit': {'sha': head or current}}}
        for key, value in statuses.items():
            data[f'{api}/deployments/{key}/statuses?per_page=100'] = value
        return data

    def guard(self, fixtures, *, attempt='1'):
        path = self.tmp / 'fixtures.json'
        path.write_text(json.dumps(fixtures))
        calls = self.tmp / 'calls.txt'
        calls.write_text('')
        runner_temp = self.tmp / 'runner'
        runner_temp.mkdir(exist_ok=True)
        env = {'PATH': self.path, 'HOME': str(self.tmp), 'GH_FIXTURES': str(path), 'GH_CALLS': str(calls),
               'GITHUB_REPOSITORY': REPO, 'GITHUB_SHA': self.NEW, 'GITHUB_RUN_ATTEMPT': attempt,
               'RUNNER_TEMP': str(runner_temp), 'GH_TOKEN': 'test-double-token'}
        done = subprocess.run(['bash', '-e', '-c', self.script], env=env, capture_output=True, text=True)
        return done.returncode, done.stdout + done.stderr

    def test_newer_commit_deploys(self):
        code, out = self.guard(self.fixtures(relation='ahead'))
        self.assertEqual(code, 0, out)
        self.assertIn(f'Production runs {self.PROD}', out)

    def test_same_commit_redeploys(self):
        # Also the first dev deploy of the commit main deployed last.
        code, out = self.guard(self.fixtures(relation='identical'))
        self.assertEqual(code, 0, out)

    def test_older_or_unrelated_commit_is_refused(self):
        for relation in ('behind', 'diverged'):
            with self.subTest(relation=relation):
                code, out = self.guard(self.fixtures(relation=relation))
                self.assertEqual(code, 1, out)
                self.assertIn('refusing to deploy', out)

    def test_unknown_production_commit_is_refused(self):
        code, out = self.guard(self.fixtures(statuses={3: [{'state': 'in_progress'}],
                                                       2: [{'state': 'failure'}], 1: [{'state': 'error'}]}))
        self.assertEqual(code, 1, out)
        code, out = self.guard(self.fixtures(deployments=[]))
        self.assertEqual(code, 1, out)

    def test_api_failures_refuse(self):
        api = f'repos/{REPO}'
        for broken in (f'{api}/deployments?environment=production&per_page=100',
                       f'{api}/deployments/2/statuses?per_page=100',
                       f'{api}/compare/{self.PROD}...{self.NEW}?per_page=1'):
            with self.subTest(broken=broken):
                fixtures = self.fixtures()
                fixtures[broken] = None
                code, out = self.guard(fixtures)
                self.assertNotEqual(code, 0, out)

    def test_rerun_must_still_be_dev_head(self):
        code, out = self.guard(self.fixtures(head=self.NEW), attempt='2')
        self.assertEqual(code, 0, out)
        code, out = self.guard(self.fixtures(head='d' * 40), attempt='2')
        self.assertEqual(code, 1, out)
        self.assertIn('Dispatch this workflow on dev', out)
        fixtures = self.fixtures()
        fixtures[f'repos/{REPO}/branches/dev'] = None
        code, out = self.guard(fixtures, attempt='3')
        self.assertNotEqual(code, 0, out)


class MainSyncGuardTests(unittest.TestCase):
    def setUp(self):
        self.wf = load('main-sync-guard.yml')
        self.job = self.wf['jobs']['sync-source']
        self.script = self.job['steps'][0]['run']

    def test_shape(self):
        self.assertEqual(self.wf['permissions'], {})
        self.assertEqual(self.wf['on']['pull_request']['branches'], ['main'])
        self.assertNotIn('${{', self.script, 'pull request data reaches the shell only through env')
        self.assertTrue(all('uses' not in s for s in self.job['steps']), 'the guard checks out nothing')

    def run_guard(self, head_ref, head_repo):
        temp = tempfile.TemporaryDirectory(prefix='synwevo-sync-guard-fixture-')
        self.addCleanup(temp.cleanup)
        env = {'PATH': os.environ.get('PATH', ''), 'HEAD_REF': head_ref, 'HEAD_REPO': head_repo,
               'GITHUB_REPOSITORY': REPO}
        done = subprocess.run(['bash', '-e', '-c', self.script], env=env, cwd=temp.name,
                              capture_output=True, text=True)
        self.assertEqual(os.listdir(temp.name), [], 'a branch name must never be executed')
        return done

    def test_only_dev_of_this_repository_passes(self):
        self.assertEqual(self.run_guard('dev', REPO).returncode, 0)
        for ref, repo in (('feature', REPO), ('dev', 'someone/Synwevo'), ('', ''), ('dev', ''),
                          ('main', REPO), ('dev\n', REPO), ('$(touch pwned)', REPO), ('dev', f'{REPO}; touch pwned')):
            with self.subTest(ref=ref, repo=repo):
                done = self.run_guard(ref, repo)
                self.assertEqual(done.returncode, 1)
                self.assertIn('change the base branch of this pull request to dev', done.stdout)


if __name__ == '__main__':
    unittest.main()
