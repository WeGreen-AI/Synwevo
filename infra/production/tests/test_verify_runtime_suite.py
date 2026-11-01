"""infra/production/verify_runtime_suite.sh against throwaway containers: the verify step's exit handling.

The verify job runs the runtime suite as two containers in parallel through this script. In the job each
container runs the test derivative of the deployment image for about an hour; here the image is alpine
with a `python` test double on PATH, which reads the arguments the script passes (the --basetemp under
the container's own mount, then the test paths) and ends the way its scenario says: passes, fails the
way pytest does (exit 1 after a traceback), or dies by SIGKILL the way the memory limit kills a container
(137 through --init). A double can also hold its container until this test releases it by creating a
file in that container's bind mount. The two claims about timing are therefore observed, not raced
against docker's start latency (a 1.3 s later start of the second container once failed the former
`sleep 1` version): both containers run at the same time (each is held, and both are seen running before
either is released), and the script waits for the second container after the first has failed (the
second is released only once the first's exit line has been printed). The container tests are skipped
where docker is not usable or cannot bind-mount a directory this test makes; the verify job's runner has
both, so they run in CI. The script's own refusals need no docker.
"""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
import unittest
import uuid

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / 'infra' / 'production' / 'verify_runtime_suite.sh'
BASE_IMAGE = 'alpine:3.22'
SUITE_A = ['tests/integration', 'apps/messages/tests']
SUITE_B = ['apps/docs/tests', 'services/identity/tests', 'apps/suite/tests']
# What Dockerfile.verify's CMD runs, up to its --basetemp: the script passes the same flags to both containers.
FLAGS = ['-m', 'pytest', '--tb=short', '--maxfail=1', '--durations=20', '-ra', '--ci-clean-passed-tmp']
HELD_LIMIT_S = 120     # a held double gives up after this long, so a broken test cannot leave a container running
WAIT_S = 90            # how long a container may take to start, on a cold daemon, before the test gives up

# TEST DOUBLE for the verify image's python (no pytest here): the test paths say which container this is.
# It prints its collecting line, then writes arguments.txt into its mount (the marker this test watches
# for a running container), then does what its scenario says. umask 000: files the container's root
# writes into the bind mount stay removable by the test, which can also create the release file there.
PYTHON_DOUBLE = '''#!/bin/sh
umask 000
base=''
for arg in "$@"; do case $arg in --basetemp=*) base=${{arg#--basetemp=}} ;; esac; done
echo "collecting: $*"
mkdir -p "$base" && printf '%s\\n' "$@" > "$base/arguments.txt"
case " $* " in
  *' tests/integration '*) {a} ;;
  *) {b} ;;
esac
'''
PASS = "echo '1 passed in 1.00s'; exit 0"
HELD_PASS = (f'i=0; while [ ! -e "$base/release" ] && [ $i -lt {HELD_LIMIT_S * 5} ]; do sleep 0.2; i=$((i+1)); done; '
             "echo '1 passed in 1.00s'; exit 0")
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


class Run:
    """One run of the script in progress: what its containers have written, its output so far, releases."""

    def __init__(self, proc, root, logs):
        self.proc, self.root, self.logs = proc, root, logs

    def mount(self, slot):
        return self.root / f'ci-test-artifacts-{slot}' / f'pytest-{slot}'

    def started(self, slot):
        return (self.mount(slot) / 'arguments.txt').exists()

    def arguments(self, slot):
        return (self.mount(slot) / 'arguments.txt').read_text(encoding='utf-8').splitlines()

    def release(self, slot):
        self.mount(slot).mkdir(parents=True, exist_ok=True)     # a double that has not started yet finds it on arrival
        (self.mount(slot) / 'release').touch()

    def stdout(self):
        return (self.logs / 'stdout.txt').read_text(encoding='utf-8', errors='replace')

    def stderr(self):
        return (self.logs / 'stderr.txt').read_text(encoding='utf-8', errors='replace')

    def wait_until(self, condition, timeout=WAIT_S):
        """True once `condition()` holds; False when it still does not after `timeout`, or when the script ended."""
        deadline = time.monotonic() + timeout
        while not condition():
            if self.proc.poll() is not None or time.monotonic() > deadline:
                return condition()
            time.sleep(0.1)
        return True

    def finish(self, timeout=300):
        code = self.proc.wait(timeout=timeout)
        return code, self.stdout(), self.stderr()

    def abandon(self):
        """Cleanup: release whatever is still held and reap the script, so a failed assertion leaves nothing running."""
        if self.proc.poll() is None:
            for slot in 'ab':
                self.release(slot)
            try:
                self.proc.wait(timeout=HELD_LIMIT_S + 60)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait()


@unittest.skipUnless(docker_usable(), 'needs a usable docker daemon (the verify job has one)')
class ContainerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        pulled = subprocess.run(['docker', 'pull', '-q', BASE_IMAGE], capture_output=True, text=True, timeout=600)
        if pulled.returncode != 0:
            raise AssertionError(f'cannot pull {BASE_IMAGE}: {pulled.stderr}')
        cls.shared = cls.mountable_directory()

    @classmethod
    def mountable_directory(cls):
        """A directory the daemon can bind-mount, in which this class makes the roots it gives the script.
        The temporary directory serves on Linux (the verify job's runner). A Docker VM (macOS) sees only
        the directories it shares, usually the home directory, so the user's cache directory is tried next;
        TMPDIR still chooses when it is set to a shared directory."""
        tried = []
        cache = os.environ.get('XDG_CACHE_HOME') or os.path.join(os.path.expanduser('~'), '.cache')
        for base in (tempfile.gettempdir(), cache):
            try:
                shared = Path(tempfile.mkdtemp(prefix='synwevo-verify-suite-mounts-', dir=base))
            except OSError as exc:
                tried.append(f'{base} ({exc.strerror})')
                continue
            probe = shared / 'probe'
            probe.mkdir()
            (probe / 'marker').write_text('shared\n', encoding='utf-8')
            seen = subprocess.run(['docker', 'run', '--rm', '--mount', f'type=bind,source={probe},target=/probe',
                                   BASE_IMAGE, 'test', '-e', '/probe/marker'], capture_output=True, text=True, timeout=120)
            if seen.returncode == 0:
                cls.addClassCleanup(shutil.rmtree, shared, True)
                return shared
            shutil.rmtree(shared, True)
            tried.append(base)
        raise unittest.SkipTest('docker cannot bind-mount a directory under ' + ' or '.join(tried)
                                + '; set TMPDIR to a directory the daemon shares')

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

    def launch(self, a, b):
        """Start the script on a fresh root with the doubles' scenarios; the output goes to files this test reads
        while the script runs. Cleanups run last-registered first: the run is released and reaped, then the
        directories go, then the image."""
        image = self.image(a, b)
        root = Path(tempfile.mkdtemp(prefix='run-', dir=self.shared))
        self.addCleanup(shutil.rmtree, root, True)
        logs = Path(tempfile.mkdtemp(prefix='synwevo-verify-suite-logs-'))
        self.addCleanup(shutil.rmtree, logs, True)
        with open(logs / 'stdout.txt', 'wb') as out, open(logs / 'stderr.txt', 'wb') as err:
            proc = subprocess.Popen(['bash', str(SCRIPT), image, str(root)], cwd=ROOT, stdout=out, stderr=err)
        run = Run(proc, root, logs)
        self.addCleanup(run.abandon)
        return run

    def test_both_containers_pass(self):
        run = self.launch(HELD_PASS, HELD_PASS)
        # Each double holds its container until released: seeing both running is the proof that the script
        # started B without waiting for A, whatever docker's start latency is on the runner.
        together = run.wait_until(lambda: run.started('a') and run.started('b'))
        for slot in 'ab':
            run.release(slot)
        code, out, err = run.finish()
        self.assertTrue(together, 'the two containers were not running at the same time:\n' + out + err)
        self.assertEqual(code, 0, out + err)
        self.assertIn(suite_line('A', SUITE_A, 0, 'passed'), out)
        self.assertIn(suite_line('B', SUITE_B, 0, 'passed'), out)
        self.assertIn('Both runtime suite containers passed.', out)
        self.assertNotIn('::error::', out)
        # Each container got the image's own pytest flags, a --basetemp under its own mount, and its paths.
        self.assertEqual(run.arguments('a'), FLAGS + ['--basetemp=/tmp/ci-test-artifacts/pytest-a'] + SUITE_A)
        self.assertEqual(run.arguments('b'), FLAGS + ['--basetemp=/tmp/ci-test-artifacts/pytest-b'] + SUITE_B)
        self.assertEqual(sorted(p.name for p in run.root.iterdir()), ['ci-test-artifacts-a', 'ci-test-artifacts-b'])
        # The stream shows the same: both were collecting before either finished (each double printed its
        # collecting line before writing the marker this test waited for, and finished only once released).
        lines = out.splitlines()
        finished = [i for i, line in enumerate(lines) if ' container finished after ' in line]
        collecting = [i for i, line in enumerate(lines) if '] collecting: ' in line]
        self.assertEqual(len(collecting), 2, out)
        self.assertEqual(len(finished), 2, out)
        self.assertLess(max(collecting), min(finished), out)
        # Every container line carries its prefix, so one container's log can be recovered from the stream.
        for line in lines:
            self.assertTrue(line.startswith(('[A] ', '[B] ', 'Runtime suite ', 'Both runtime suite')), line)
        self.assertIn('[A] collecting: ' + ' '.join(FLAGS + ['--basetemp=/tmp/ci-test-artifacts/pytest-a'] + SUITE_A), out)

    def test_a_failing_container_fails_the_step_after_the_other_was_waited_for(self):
        """The `set -e` pitfall: a failing `wait` must not end the script before the second container is
        waited for. B is held until A's exit line has been printed, so B ends after A failed by construction,
        and the script has to still be there to wait for it and report both exit codes."""
        run = self.launch(FAIL, HELD_PASS)
        a_finished = run.wait_until(lambda: '[A] container finished after' in run.stdout())
        run.release('b')
        code, out, err = run.finish()
        self.assertTrue(a_finished, out + err)
        self.assertEqual(code, 1, out + err)
        self.assertIn(suite_line('A', SUITE_A, 1, 'tests failed'), out)
        self.assertIn(suite_line('B', SUITE_B, 0, 'passed'), out)
        self.assertIn('::error::The runtime suite failed (A: exit 1, B: exit 0)', out)
        self.assertNotIn('Both runtime suite containers passed', out)
        # The traceback streamed with its prefix; B finished after A had already failed.
        self.assertIn('[A] FAILED tests/integration/test_x.py::test_y - AssertionError: synthetic', out)
        self.assertIn('[A] Traceback (most recent call last):', out)
        self.assertIn('[A]   File "tests/integration/test_x.py", line 1, in test_y', out)
        self.assertIn('[B] 1 passed in 1.00s', out)
        self.assertLess(out.index('[A] container finished after'), out.index('[B] 1 passed in 1.00s'), out)
        self.assertTrue(run.started('b'))

    def test_a_killed_container_fails_the_step_with_137(self):
        code, out, err = self.launch(PASS, KILLED).finish()
        self.assertEqual(code, 137, out + err)
        self.assertIn(suite_line('A', SUITE_A, 0, 'passed'), out)
        self.assertIn(suite_line('B', SUITE_B, 137, 'killed by SIGKILL: the 3 GiB memory limit, most likely'), out)
        self.assertIn('::error::The runtime suite failed (A: exit 0, B: exit 137)', out)
        self.assertNotIn('Both runtime suite containers passed', out)

    def test_two_failures_are_both_reported(self):
        code, out, err = self.launch(FAIL, KILLED).finish()
        self.assertEqual(code, 1, out + err)
        self.assertIn(suite_line('A', SUITE_A, 1, 'tests failed'), out)
        self.assertIn(suite_line('B', SUITE_B, 137, 'killed by SIGKILL: the 3 GiB memory limit, most likely'), out)
        self.assertIn('::error::The runtime suite failed (A: exit 1, B: exit 137)', out)


if __name__ == '__main__':
    unittest.main()
