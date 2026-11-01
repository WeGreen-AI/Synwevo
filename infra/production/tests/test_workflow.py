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
import re
import shutil
import subprocess
import sys
import tempfile
import tomllib
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


def evaluate(expression, github):
    """Evaluate the small subset of the Actions expression language the concurrency settings use.

    `a && b || c` returns operands like Python's `and`/`or`; string comparison, `format()` and
    `github.<name>` are all these expressions need. Anything else fails the eval.
    """
    body = expression.strip()
    assert body.startswith('${{') and body.endswith('}}'), expression
    body = body[3:-2].replace('&&', ' and ').replace('||', ' or ')
    body = re.sub(r'\bgithub\.(\w+)', lambda m: repr(github[m.group(1)]), body)
    return eval(body, {'__builtins__': {}}, {'format': lambda text, *args: text.format(*args)})


def git_env(home):
    env = {k: v for k, v in os.environ.items() if not k.startswith('GIT_')}
    env.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM='1', HOME=str(home),
               GIT_AUTHOR_NAME='Fixture', GIT_AUTHOR_EMAIL='fixture@example.test',
               GIT_COMMITTER_NAME='Fixture', GIT_COMMITTER_EMAIL='fixture@example.test',
               # No background housekeeping: after a 600-file commit the hosted runner's git started an
               # automatic gc/maintenance that was still writing into .git while the fixture directory
               # was removed ("Directory not empty", PR #2's first verify).
               GIT_CONFIG_COUNT='3', GIT_CONFIG_KEY_0='gc.auto', GIT_CONFIG_VALUE_0='0',
               GIT_CONFIG_KEY_1='maintenance.auto', GIT_CONFIG_VALUE_1='false',
               GIT_CONFIG_KEY_2='gc.autoDetach', GIT_CONFIG_VALUE_2='false')
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
        self.assertNotIn('needs', verify, 'a failed dependency skips verify, which counts as passed')

    def test_heavy_steps_run_unless_the_classifier_said_records_only(self):
        steps = self.wf['jobs']['verify']['steps']
        # The re-run guard comes first, before checkout and the long verify, on every event.
        self.assertEqual(steps[0]['name'], "Refuse a re-run that is no longer dev's head")
        self.assertNotIn('if', steps[0])
        self.assertEqual(steps[1]['with']['fetch-depth'], 2)
        self.assertEqual(steps[2]['id'], 'changes')
        steps = steps[1:]
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

    def test_concurrency_groups(self):
        # Per job: a re-run must never queue in a shared group, where it would cancel a newer
        # pending member before its own guard refuses it.
        self.assertNotIn('concurrency', self.wf)
        verify = self.wf['jobs']['verify']['concurrency']
        deploy = self.wf['jobs']['deploy']['concurrency']

        self.assertIs(deploy['cancel-in-progress'], False, 'a newer deploy never cancels a running one')

        def groups(event, ref, attempt):
            github = {'event_name': event, 'ref': ref, 'run_attempt': attempt, 'run_id': '4242'}
            return (evaluate(verify['group'], github), evaluate(verify['cancel-in-progress'], github),
                    evaluate(deploy['group'], github))

        dev = ('synwevo-verify-refs/heads/dev', False, 'synwevo-production')
        rerun = ('synwevo-rerun-4242', False, 'synwevo-rerun-deploy-4242')
        self.assertEqual(groups('push', 'refs/heads/dev', '1'), dev)
        self.assertEqual(groups('workflow_dispatch', 'refs/heads/dev', '1'), dev)
        self.assertEqual(groups('push', 'refs/heads/dev', '2'), rerun)
        self.assertEqual(groups('workflow_dispatch', 'refs/heads/dev', '3'), rerun)
        pull = groups('pull_request', 'refs/pull/7/merge', '1')
        self.assertEqual(pull[:2], ('synwevo-verify-refs/pull/7/merge', True))
        # A pull request's re-run stays in its pull request's group (it deploys nothing).
        self.assertEqual(groups('pull_request', 'refs/pull/7/merge', '2')[:2], ('synwevo-verify-refs/pull/7/merge', True))
        self.assertEqual(groups('workflow_dispatch', 'refs/heads/feature', '1')[:2], ('synwevo-verify-refs/heads/feature', False))


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
"""Test double for `gh api [--paginate] ENDPOINT [--jq FILTER]`: fixed responses from GH_FIXTURES, real jq.

A fixture {{"pages": [...]}} is a paginated response: without --paginate only its first page is
returned, as the real API returns one page per request; --paginate applies the filter to every page.
"""
import json, os, subprocess, sys
args = sys.argv[1:]
assert args[0] == 'api', args
rest = args[1:]
jq = rest[rest.index('--jq') + 1] if '--jq' in rest else '.'
paginate = '--paginate' in rest
positional = [a for i, a in enumerate(rest) if not a.startswith('--') and (i == 0 or rest[i - 1] != '--jq')]
assert len(positional) == 1, args
endpoint = positional[0]
with open(os.environ['GH_FIXTURES']) as f:
    fixtures = json.load(f)
with open(os.environ['GH_CALLS'], 'a') as f:
    f.write(endpoint + '\\n')
if fixtures.get(endpoint) is None:
    sys.stderr.write('gh: HTTP 502 (test double)\\n')
    sys.exit(1)
value = fixtures[endpoint]
pages = value['pages'] if isinstance(value, dict) and 'pages' in value else [value]
for page in (pages if paginate else pages[:1]):
    done = subprocess.run(['jq', '-r', jq], input=json.dumps(page), text=True)
    if done.returncode:
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

    def test_success_beyond_the_first_page_is_found(self):
        # 100 newer records without a success (refused, failed, billing-blocked deploy jobs) fill
        # the first page; production's success is on the second.
        failures = [{'id': 1000 + i, 'sha': 'e' * 40} for i in range(100)]
        statuses = {1000 + i: [{'state': 'failure'}, {'state': 'queued'}, {'state': 'waiting'}] for i in range(100)}
        statuses[2] = [{'state': 'success'}]
        fixtures = self.fixtures(deployments={'pages': [failures, [{'id': 2, 'sha': self.PROD}]]},
                                 statuses=statuses, head='d' * 40)
        code, out = self.guard(fixtures)
        self.assertEqual(code, 0, out)
        self.assertIn(f'Production runs {self.PROD}', out)

    def test_no_success_on_record_deploys_only_dev_head(self):
        never = {3: [{'state': 'in_progress'}], 2: [{'state': 'failure'}], 1: [{'state': 'error'}]}
        for deployments in ('default', []):
            with self.subTest(deployments=deployments):
                code, out = self.guard(self.fixtures(deployments=deployments, statuses=never, head='d' * 40))
                self.assertEqual(code, 1, out)
                self.assertIn('only dev\'s head deploys', out)
                code, out = self.guard(self.fixtures(deployments=deployments, statuses=never))
                self.assertEqual(code, 0, out)
                self.assertIn('No production deployment has ever succeeded', out)
        fixtures = self.fixtures(statuses=never)
        fixtures[f'repos/{REPO}/branches/dev'] = None
        code, out = self.guard(fixtures)
        self.assertNotEqual(code, 0, out)

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

    def test_first_attempt_with_production_on_record_does_not_read_dev(self):
        fixtures = self.fixtures()
        fixtures[f'repos/{REPO}/branches/dev'] = None
        code, out = self.guard(fixtures)
        self.assertEqual(code, 0, out)

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


@unittest.skipUnless(shutil.which('jq'), 'the re-run guard test needs jq, as gh --jq does')
class VerifyRerunGuardTests(unittest.TestCase):
    """The first verify step refuses a re-run on dev whose commit is no longer dev's head."""
    SHA = 'c' * 40

    @classmethod
    def setUpClass(cls):
        cls.script = step(load('production.yml')['jobs']['verify'], name="Refuse a re-run that is no longer dev's head")['run']

    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix='synwevo-rerun-guard-fixture-')
        self.addCleanup(temp.cleanup)
        self.tmp = Path(temp.name)
        bin_dir = self.tmp / 'bin'
        bin_dir.mkdir()
        gh = bin_dir / 'gh'
        gh.write_text(GH_DOUBLE.format(python=sys.executable))
        gh.chmod(0o755)
        self.path = f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}"

    def guard(self, *, event='push', ref='refs/heads/dev', attempt='1', head=None):
        fixtures = self.tmp / 'fixtures.json'
        fixtures.write_text(json.dumps({f'repos/{REPO}/branches/dev': None if head is None else {'commit': {'sha': head}}}))
        calls = self.tmp / 'calls.txt'
        calls.write_text('')
        env = {'PATH': self.path, 'HOME': str(self.tmp), 'GH_FIXTURES': str(fixtures), 'GH_CALLS': str(calls),
               'GITHUB_REPOSITORY': REPO, 'GITHUB_SHA': self.SHA, 'GITHUB_RUN_ATTEMPT': attempt,
               'GITHUB_EVENT_NAME': event, 'GITHUB_REF': ref, 'GH_TOKEN': 'test-double-token'}
        done = subprocess.run(['bash', '-e', '-c', self.script], env=env, capture_output=True, text=True)
        return done.returncode, done.stdout + done.stderr, calls.read_text().split()

    def test_first_attempts_pull_requests_and_other_refs_pass_without_a_call(self):
        for case in ({'event': 'push'}, {'event': 'workflow_dispatch'},
                     {'event': 'pull_request', 'ref': 'refs/pull/7/merge', 'attempt': '2'},
                     {'event': 'workflow_dispatch', 'ref': 'refs/heads/feature', 'attempt': '2'}):
            with self.subTest(**case):
                code, out, calls = self.guard(**case)
                self.assertEqual((code, calls), (0, []), out)

    def test_rerun_on_dev_must_be_dev_head(self):
        code, out, calls = self.guard(attempt='2', head=self.SHA)
        self.assertEqual(code, 0, out)
        self.assertEqual(calls, [f'repos/{REPO}/branches/dev'])
        code, out, _ = self.guard(event='workflow_dispatch', attempt='3', head='d' * 40)
        self.assertEqual(code, 1, out)
        self.assertIn('Dispatch this workflow on dev', out)
        code, out, _ = self.guard(attempt='2', head=None)        # the API fails: refuse
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


class RuntimeSuiteTests(unittest.TestCase):
    """The runtime suite runs as two containers in parallel inside the one verify step, through
    infra/production/verify_runtime_suite.sh; the job stays the one bounded `verify` check. Its exit
    handling is exercised against real containers in test_verify_runtime_suite.py; here the shape."""

    def setUp(self):
        self.wf = load('production.yml')
        self.text = (WORKFLOWS / 'production.yml').read_text(encoding='utf-8')
        self.script = (ROOT / 'infra' / 'production' / 'verify_runtime_suite.sh').read_text(encoding='utf-8')
        self.dockerfile = (ROOT / 'infra' / 'production' / 'Dockerfile.verify').read_text(encoding='utf-8')

    def array(self, name):
        match = re.search(rf'^{name}=\((.*)\)$', self.script, re.M)
        self.assertIsNotNone(match, f'{name}=(...) in the script')
        return match.group(1).split()

    def docker_run(self):
        lines = self.script.splitlines()
        starts = [i for i, line in enumerate(lines) if line.strip().startswith('docker run ')]
        self.assertEqual(len(starts), 1, 'one command runs both containers, so A and B get the same flags')
        end = next(i for i in range(starts[0], len(lines)) if lines[i].rstrip().endswith('2>&1 \\'))
        return ' '.join(line.strip().rstrip('\\').strip() for line in lines[starts[0]:end + 1])

    def test_the_two_containers_cover_the_configured_testpaths_exactly(self):
        pyproject = tomllib.loads((ROOT / 'pyproject.toml').read_text(encoding='utf-8'))
        testpaths = pyproject['tool']['pytest']['ini_options']['testpaths']
        a, b = self.array('suite_a'), self.array('suite_b')
        self.assertTrue(a and b, 'both containers run something')
        self.assertFalse(set(a) & set(b), 'no suite runs twice')
        self.assertEqual(len(a + b), len(set(a + b)))
        self.assertEqual(sorted(a + b), sorted(testpaths), 'together the two containers run exactly the configured suites')
        self.assertIn('run_suite A a "${suite_a[@]}" &', self.script)
        self.assertIn('run_suite B b "${suite_b[@]}" &', self.script)

    def test_each_container_runs_pytest_as_the_image_does_on_its_own_mount(self):
        cmd = json.loads(re.search(r'^CMD (\[.*\])$', self.dockerfile, re.M).group(1))
        self.assertEqual(cmd[:3], ['python', '-m', 'pytest'])
        self.assertTrue(all(arg.startswith('-') for arg in cmd[3:]),
                        'the image default stays the whole suite (testpaths), for the manual verify mirror')
        self.assertIn('--basetemp=/tmp/ci-test-artifacts/pytest', cmd)
        self.assertEqual(self.array('pytest_flags'), [arg for arg in cmd[3:] if not arg.startswith('--basetemp=')],
                         'the containers run the flags of the image command, plus their paths')
        run = self.docker_run()
        for flag in ('--rm', '--init', '--network none', '--memory 3g', '--cpus 2'):
            self.assertIn(f' {flag} ', run)
        self.assertEqual(run.count('--mount'), 1, 'only the artifacts directory is mounted')
        self.assertIn('--mount "type=bind,source=$root/ci-test-artifacts-$slot,target=/tmp/ci-test-artifacts"', run)
        self.assertIn('"$image" python -m pytest "${pytest_flags[@]}" "--basetemp=/tmp/ci-test-artifacts/pytest-$slot" "$@"', run)
        for forbidden in (' -v ', '--volume', '--privileged', '--network host', '/opt/synwevo', 'data/'):
            self.assertNotIn(forbidden, run)
        self.assertIn('mkdir -m 0777 "$root/ci-test-artifacts-a" "$root/ci-test-artifacts-b"', self.script)

    def test_both_exit_codes_are_waited_for_and_checked(self):
        self.assertIn('set -euo pipefail', self.script)
        # Both containers start before either is waited for; each wait records its own status, so a
        # failing first wait cannot end the script (set -e) before the second container is waited for.
        self.assertLess(self.script.index('pid_a=$!'), self.script.index('pid_b=$!'))
        self.assertLess(self.script.index('pid_b=$!'), self.script.index('wait "$pid_a" || status_a=$?'))
        self.assertLess(self.script.index('wait "$pid_a" || status_a=$?'), self.script.index('wait "$pid_b" || status_b=$?'))
        self.assertIn('if [ "$status_a" -ne 0 ] || [ "$status_b" -ne 0 ]; then', self.script)
        self.assertIn('exit "$status_a"', self.script)
        self.assertIn('exit "$status_b"', self.script)
        self.assertIn('Runtime suite A (${suite_a[*]}): exit $status_a', self.script)
        self.assertIn('Runtime suite B (${suite_b[*]}): exit $status_b', self.script)
        # Inside run_suite the pipeline's status (docker's, under pipefail) is recorded and returned, so a
        # container's failure reaches the waits above: `|| :` or `return 0` here would pass both containers.
        body = self.script[self.script.index('run_suite() {'):self.script.index('\nexplain() {')]
        self.assertIn('; done \\\n    || status=$?\n', body)
        self.assertIn("printf '[%s] container finished after %ss with exit %s\\n' \"$label\" \"$SECONDS\" \"$status\"", body)
        self.assertIn('\n  return "$status"\n', body)
        self.assertNotIn('return 0', body)

    def test_the_step_runs_the_script_on_the_image_it_built_and_the_diagnostics_list_both_mounts(self):
        verify = self.wf['jobs']['verify']
        runtime = step(verify, name='Verify the deployment runtime')
        run = runtime['run']
        self.assertIn('docker build -f infra/production/Dockerfile.verify -t synwevo:verify .', run)
        self.assertLess(run.index('-t synwevo:verify .'), run.index('verify_runtime_suite.sh'))
        self.assertNotRegex(run, r'docker run[^\n]*synwevo:verify', 'the step starts no suite container itself; the script does, twice')
        # The script is the step's last command, alone on its line, in the default fail-fast shell: its exit
        # status is the step's. `|| true`, continue-on-error or a shell override would let both containers
        # fail with verify green.
        commands = [line.strip() for line in run.splitlines() if line.strip() and not line.strip().startswith('#')]
        self.assertEqual(commands[-1], 'bash infra/production/verify_runtime_suite.sh synwevo:verify "$RUNNER_TEMP"')
        self.assertNotIn('continue-on-error', runtime)
        self.assertNotIn('shell', runtime)
        diagnostics = step(verify, name='Runtime test disk diagnostics')
        self.assertEqual(diagnostics['if'], "always() && steps.changes.outputs.code != 'false'")
        self.assertIn('for mount in "$RUNNER_TEMP/ci-test-artifacts-a" "$RUNNER_TEMP/ci-test-artifacts-b"; do', diagnostics['run'])
        self.assertIn('sudo du -h --max-depth=2 "$mount"', diagnostics['run'])
        self.assertNotIn('ci-test-artifacts"', diagnostics['run'], 'the former single mount is gone')
        self.assertLessEqual(verify['timeout-minutes'], 120, 'the limit is a guard, not a target: rebalance the split before raising it')
        self.assertIn('one bounded verify job', self.text)
        self.assertIn('two containers', self.text)

    def test_each_container_generates_the_synthetic_fixtures_its_tests_read(self):
        """testdata/ is git-ignored, so neither a fresh checkout nor the verify image has it; the root
        conftest's session fixture `fixtures` generates it on request. tests/integration reads those files
        as well as the Docs suite, and in one container the Docs suite had always generated them first: a
        reader that spells the path instead of requesting the fixture passes there and fails in a container
        of its own (FileNotFoundError at /app/testdata/…, which would have been container A's first failure)."""
        self.assertIn('\ntestdata/\n', (ROOT / '.gitignore').read_text(encoding='utf-8'))
        conftest = (ROOT / 'conftest.py').read_text(encoding='utf-8')
        self.assertIn('@pytest.fixture(scope="session")\ndef fixtures() -> Path:', conftest)
        self.assertIn('FIXTURE_GENERATOR = ROOT / "apps" / "docs" / "scripts" / "make_fixtures.py"', conftest)
        self.assertIn('COPY conftest.py ./conftest.py', self.dockerfile, 'the verify image carries the root conftest')
        pyproject = tomllib.loads((ROOT / 'pyproject.toml').read_text(encoding='utf-8'))
        spelled = re.compile(r"""["']testdata["'/]""")
        readers = [str(path.relative_to(ROOT)) for suite in pyproject['tool']['pytest']['ini_options']['testpaths']
                   for path in sorted((ROOT / suite).rglob('*.py')) if spelled.search(path.read_text(encoding='utf-8'))]
        self.assertEqual(readers, [], 'a test reads testdata/ from the path `fixtures` returns, never by a path of its own')


if __name__ == '__main__':
    unittest.main()
