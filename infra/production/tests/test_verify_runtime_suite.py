"""infra/production/verify_runtime_suite.sh against throwaway containers: the verify step's exit handling.

The verify job runs the runtime suite as two containers in parallel through this script. In the job each
container runs the test derivative of the deployment image for about an hour; here the image is alpine
with a `python` test double on PATH, which reads the arguments the script passes (the --basetemp under
the container's own mount, then the test paths) and ends the way its scenario says: passes, fails the
way pytest does (exit 1 after a traceback), or dies by SIGKILL the way the memory limit kills a container
(137 through --init). The container tests are skipped where docker is not usable or cannot bind-mount
the temporary directory; the verify job's runner has both, so they run in CI. The script's own refusals
need no docker.
"""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
import uuid

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / 'infra' / 'production' / 'verify_runtime_suite.sh'
BASE_IMAGE = 'alpine:3.22'
SUITE_A = ['tests/integration', 'apps/messages/tests']
SUITE_B = ['apps/docs/tests', 'services/identity/tests', 'apps/suite/tests']
# What Dockerfile.verify's CMD runs, up to its --basetemp: the script passes the same flags to both containers.
FLAGS = ['-m', 'pytest', '--tb=short', '--maxfail=1', '--durations=20', '-ra', '--ci-clean-passed-tmp']

# TEST DOUBLE for the verify image's python (no pytest here): the test paths say which container this is.
# umask 000: files the container's root writes into the bind mount stay removable by the test.
PYTHON_DOUBLE = '''#!/bin/sh
umask 000
base=''
for arg in "$@"; do case $arg in --basetemp=*) base=${{arg#--basetemp=}} ;; esac; done
mkdir -p "$base" && printf '%s\\n' "$@" > "$base/arguments.txt"
echo "collecting: $*"
sleep 1
case " $* " in
  *' tests/integration '*) {a} ;;
  *) {b} ;;
esac
'''
PASS = "echo '1 passed in 1.00s'; exit 0"
SLOW_PASS = "sleep 2; echo '1 passed in 3.00s'; exit 0"
FAIL = ("echo 'FAILED tests/integration/test_x.py::test_y - AssertionError: synthetic'; "
        "echo 'Traceback (most recent call last):'; "
        "echo '  File \"tests/integration/test_x.py\", line 1, in test_y'; exit 1")
KILLED = 'sleep 2; kill -KILL $$'      # --init (tini) reports a child killed by signal 9 as 137, as the OOM killer's victim is


def docker_usable():
    if not shutil.which('docker'):
        return False
    try:
        done = subprocess.run(['docker', 'info', '--format', '{{.ServerVersion}}'],
                              capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return done.returncode == 0


def run_script(image, root):
    return subprocess.run(['bash', str(SCRIPT), image, str(root)], cwd=ROOT,
                          capture_output=True, text=True, timeout=300)


def suite_line(label, paths, status, meaning):
    return f"Runtime suite {label} ({' '.join(paths)}): exit {status} ({meaning})"


class ScriptRefusalTests(unittest.TestCase):
    """Refusals that happen before any container starts; no docker involved."""

    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix='synwevo-verify-suite-fixture-')
        self.addCleanup(temp.cleanup)
        self.tmp = Path(temp.name)

    def test_usage(self):
        done = subprocess.run(['bash', str(SCRIPT)], capture_output=True, text=True, timeout=60)
        self.assertEqual(done.returncode, 2)
        self.assertIn('usage:', done.stderr)
        done = subprocess.run(['bash', str(SCRIPT), 'synwevo:verify'], capture_output=True, text=True, timeout=60)
        self.assertEqual(done.returncode, 2)

    def test_a_missing_artifacts_root_is_refused(self):
        done = run_script('synwevo:verify', self.tmp / 'missing')
        self.assertNotEqual(done.returncode, 0)
        self.assertNotIn('Runtime suite A', done.stdout, 'nothing was started')

    def test_an_existing_artifacts_directory_is_refused(self):
        """A fresh root per run: a leftover ci-test-artifacts-a would mix two runs' fixtures."""
        (self.tmp / 'ci-test-artifacts-a').mkdir()
        done = run_script('synwevo:verify', self.tmp)
        self.assertNotEqual(done.returncode, 0)
        self.assertIn('File exists', done.stderr)
        self.assertNotIn('Runtime suite A (', done.stdout, 'nothing was started')


@unittest.skipUnless(docker_usable(), 'needs a usable docker daemon (the verify job has one)')
class ContainerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.shared = Path(tempfile.mkdtemp(prefix='synwevo-verify-suite-mounts-'))
        cls.addClassCleanup(shutil.rmtree, cls.shared, True)
        pulled = subprocess.run(['docker', 'pull', '-q', BASE_IMAGE], capture_output=True, text=True, timeout=600)
        if pulled.returncode != 0:
            raise AssertionError(f'cannot pull {BASE_IMAGE}: {pulled.stderr}')
        # The script bind-mounts directories under the root it is given. A Docker VM (macOS) sees only
        # its shared directories; TMPDIR chooses where this test's roots are made.
        probe = cls.shared / 'probe'
        probe.mkdir()
        (probe / 'marker').write_text('shared\n', encoding='utf-8')
        seen = subprocess.run(['docker', 'run', '--rm', '--mount', f'type=bind,source={probe},target=/probe',
                               BASE_IMAGE, 'test', '-e', '/probe/marker'], capture_output=True, text=True, timeout=120)
        if seen.returncode != 0:
            raise unittest.SkipTest(f'docker cannot bind-mount {cls.shared}; set TMPDIR to a directory the daemon shares')

    def image(self, a, b):
        context = Path(tempfile.mkdtemp(prefix='synwevo-verify-suite-image-'))
        self.addCleanup(shutil.rmtree, context, True)
        (context / 'Dockerfile').write_text(f'FROM {BASE_IMAGE}\nCOPY python /usr/local/bin/python\n', encoding='utf-8')
        python = context / 'python'
        python.write_text(PYTHON_DOUBLE.format(a=a, b=b), encoding='utf-8')
        python.chmod(0o755)
        tag = f'synwevo-verify-suite-double:{uuid.uuid4().hex[:12]}'
        built = subprocess.run(['docker', 'build', '-q', '-t', tag, str(context)], capture_output=True, text=True, timeout=300)
        self.assertEqual(built.returncode, 0, built.stderr)
        self.addCleanup(subprocess.run, ['docker', 'rmi', '-f', tag], capture_output=True, timeout=120)
        return tag

    def start(self, a, b):
        root = Path(tempfile.mkdtemp(prefix='run-', dir=self.shared))
        self.addCleanup(shutil.rmtree, root, True)
        done = run_script(self.image(a, b), root)
        return root, done

    def arguments(self, root, slot):
        return (root / f'ci-test-artifacts-{slot}' / f'pytest-{slot}' / 'arguments.txt').read_text(encoding='utf-8').splitlines()

    def test_both_containers_pass(self):
        root, done = self.start(PASS, PASS)
        out = done.stdout
        self.assertEqual(done.returncode, 0, out + done.stderr)
        self.assertIn(suite_line('A', SUITE_A, 0, 'passed'), out)
        self.assertIn(suite_line('B', SUITE_B, 0, 'passed'), out)
        self.assertIn('Both runtime suite containers passed.', out)
        self.assertNotIn('::error::', out)
        # Each container got the image's own pytest flags, a --basetemp under its own mount, and its paths.
        self.assertEqual(self.arguments(root, 'a'), FLAGS + ['--basetemp=/tmp/ci-test-artifacts/pytest-a'] + SUITE_A)
        self.assertEqual(self.arguments(root, 'b'), FLAGS + ['--basetemp=/tmp/ci-test-artifacts/pytest-b'] + SUITE_B)
        self.assertEqual(sorted(p.name for p in root.iterdir()), ['ci-test-artifacts-a', 'ci-test-artifacts-b'])
        # The containers ran at the same time: both were collecting before either finished.
        lines = out.splitlines()
        finished = [i for i, line in enumerate(lines) if ' container finished after ' in line]
        collecting = [i for i, line in enumerate(lines) if '] collecting: ' in line]
        self.assertEqual(len(collecting), 2, out)
        self.assertLess(max(collecting), min(finished), out)
        # Every container line carries its prefix, so one container's log can be recovered from the stream.
        for line in lines:
            self.assertTrue(line.startswith(('[A] ', '[B] ', 'Runtime suite ', 'Both runtime suite')), line)
        self.assertIn('[A] collecting: ' + ' '.join(FLAGS + ['--basetemp=/tmp/ci-test-artifacts/pytest-a'] + SUITE_A), out)

    def test_a_failing_container_fails_the_step_after_the_other_was_waited_for(self):
        """The `set -e` pitfall: a failing `wait` must not end the script before the second container is
        waited for; the second one runs to its end and both exit codes are reported."""
        root, done = self.start(FAIL, SLOW_PASS)
        out = done.stdout
        self.assertEqual(done.returncode, 1, out + done.stderr)
        self.assertIn(suite_line('A', SUITE_A, 1, 'tests failed'), out)
        self.assertIn(suite_line('B', SUITE_B, 0, 'passed'), out)
        self.assertIn('::error::The runtime suite failed (A: exit 1, B: exit 0)', out)
        self.assertNotIn('Both runtime suite containers passed', out)
        # The traceback streamed with its prefix; B finished after A had already failed.
        self.assertIn('[A] FAILED tests/integration/test_x.py::test_y - AssertionError: synthetic', out)
        self.assertIn('[A] Traceback (most recent call last):', out)
        self.assertIn('[A]   File "tests/integration/test_x.py", line 1, in test_y', out)
        self.assertIn('[A] container finished after', out)
        self.assertIn('[B] 1 passed in 3.00s', out)
        self.assertLess(out.index('[A] container finished after'), out.index('[B] 1 passed in 3.00s'), out)
        self.assertTrue((root / 'ci-test-artifacts-b' / 'pytest-b' / 'arguments.txt').exists())

    def test_a_killed_container_fails_the_step_with_137(self):
        root, done = self.start(PASS, KILLED)
        out = done.stdout
        self.assertEqual(done.returncode, 137, out + done.stderr)
        self.assertIn(suite_line('A', SUITE_A, 0, 'passed'), out)
        self.assertIn(suite_line('B', SUITE_B, 137, 'killed by SIGKILL: the 3 GiB memory limit, most likely'), out)
        self.assertIn('::error::The runtime suite failed (A: exit 0, B: exit 137)', out)
        self.assertNotIn('Both runtime suite containers passed', out)

    def test_two_failures_are_both_reported(self):
        root, done = self.start(FAIL, KILLED)
        out = done.stdout
        self.assertEqual(done.returncode, 1, out + done.stderr)
        self.assertIn(suite_line('A', SUITE_A, 1, 'tests failed'), out)
        self.assertIn(suite_line('B', SUITE_B, 137, 'killed by SIGKILL: the 3 GiB memory limit, most likely'), out)
        self.assertIn('::error::The runtime suite failed (A: exit 1, B: exit 137)', out)


if __name__ == '__main__':
    unittest.main()
