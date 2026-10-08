"""Exercise the real root pytest hooks in isolated child sessions; no live data or Docker."""
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest

ROOT = Path(__file__).resolve().parents[3]


class PytestRetentionTests(unittest.TestCase):
    def run_session(self, source, *, enabled=True, ci="true", extra=(), custom=False):
        temp = tempfile.TemporaryDirectory(prefix="synwevo-retention-fixture-")
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        shutil.copyfile(ROOT / "conftest.py", root / "conftest.py")
        (root / "test_cases.py").write_text(textwrap.dedent(source))
        if custom:
            custom_dir = root / "custom"
            custom_dir.mkdir()
            (custom_dir / "conftest.py").write_text(
                'import pytest\n@pytest.fixture\ndef tmp_path(tmp_path_factory):\n'
                '    return tmp_path_factory.mktemp("custom-fixture")\n')
            (custom_dir / "test_custom.py").write_text(
                'def test_custom(tmp_path):\n    (tmp_path / "custom.marker").write_text("keep")\n')
        env = {**os.environ, "CI": ci, "PYTHONDONTWRITEBYTECODE": "1", "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1"}
        env.pop("PYTEST_ADDOPTS", None)
        # Resolve an optional worktree PYTHONPATH before changing cwd; CI uses installed packages.
        if env.get("PYTHONPATH"):
            env["PYTHONPATH"] = os.pathsep.join(str(Path(p).resolve()) for p in env["PYTHONPATH"].split(os.pathsep) if p)
        command = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "--tb=short",
                   "--basetemp", str(root / "pytest-tmp"), *extra]
        if enabled:
            command.append("--ci-clean-passed-tmp")
        result = subprocess.run(command, cwd=root, env=env, text=True, capture_output=True, timeout=60)
        return root, result

    def test_cleanup_waits_for_root_teardown_and_keeps_all_failed_phases(self):
        root, result = self.run_session('''
            import pytest

            @pytest.fixture(autouse=True)
            def evidence(tmp_path, request, monkeypatch, release_factory_resources):
                (tmp_path / "case.marker").write_text(request.node.name)
                namespace = release_factory_resources.__globals__
                release = namespace["_release_factory_resources"]
                def checked_release():
                    release()  # real worker stop/cache release precedes any deletion
                    assert (tmp_path / "case.marker").exists(), "deleted before root teardown"
                    if request.node.name == "test_root_teardown_failure":
                        raise AssertionError("fixture root teardown failure")
                monkeypatch.setitem(namespace, "_release_factory_resources", checked_release)

            @pytest.fixture
            def setup_failure(evidence):
                raise AssertionError("fixture setup failure")

            @pytest.fixture
            def teardown_failure(evidence):
                yield
                raise AssertionError("fixture teardown failure")

            def test_pass(tmp_path):
                assert (tmp_path / "case.marker").exists()

            def test_call_failure(tmp_path):
                assert False, "fixture call failure"

            def test_setup_failure(setup_failure):
                pass

            def test_teardown_failure(teardown_failure):
                pass

            def test_root_teardown_failure(tmp_path):
                pass

            def test_skip(tmp_path):
                pytest.skip("fixture skip")

            def test_symlink_replacement(tmp_path):
                # Unknown ownership after the test: never follow it for deletion.
                import shutil
                target = tmp_path.parent.parent / "unrelated-fixture"
                target.mkdir()
                (target / "case.marker").write_text("test_symlink_replacement")
                shutil.rmtree(tmp_path)
                tmp_path.symlink_to(target, target_is_directory=True)
        ''', custom=True)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertNotIn("deleted before root teardown", result.stdout)
        retained = {p.read_text() for p in (root / "pytest-tmp").glob("*/case.marker")}
        self.assertEqual(retained, {"test_call_failure", "test_setup_failure", "test_teardown_failure",
                                    "test_root_teardown_failure", "test_skip", "test_symlink_replacement"})
        self.assertTrue(list((root / "pytest-tmp").glob("custom-fixture*/custom.marker")))
        self.assertEqual((root / "unrelated-fixture/case.marker").read_text(), "test_symlink_replacement")
        self.assertIn("CI tmp_path: removed=1", result.stdout)
        self.assertIn("3 errors", result.stdout)

    def test_default_keeps_passed_tmp_path(self):
        root, result = self.run_session('''
            def test_pass(tmp_path):
                (tmp_path / "case.marker").write_text("keep locally")
        ''', enabled=False)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(len({p.parent.resolve() for p in (root / "pytest-tmp").glob("*/case.marker")}), 1)
        self.assertNotIn("CI tmp_path:", result.stdout)

    def test_unfinished_parser_fails_teardown_and_preserves_evidence(self):
        root, result = self.run_session('''
            import threading
            import pytest
            from synwevo_core.store import Store
            from synwevo_docs.services import App
            from synwevo_docs.engine import sources

            @pytest.fixture
            def held_upload(tmp_path, monkeypatch, release_factory_resources):
                app = App(Store(tmp_path / "data"))
                started, resume = threading.Event(), threading.Event()
                original_parse = sources.parse_source
                def held_parse(*args, **kwargs):
                    parsed = original_parse(*args, **kwargs)
                    started.set()
                    assert resume.wait(15), "synthetic parser gate timed out"
                    return parsed
                monkeypatch.setattr(sources, "parse_source", held_parse)
                namespace = release_factory_resources.__globals__
                release = namespace["_release_factory_resources"]
                def checked_release():
                    try:
                        release()  # must report the real parser that exceeded the deadline
                    finally:
                        # Let the child exit even when checking the old, broken cleanup.
                        resume.set()
                        app.close()
                        app.parse_pool.shutdown(wait=True, cancel_futures=True)
                monkeypatch.setitem(namespace, "_release_factory_resources", checked_release)
                return app, started

            def test_upload(tmp_path, held_upload):
                app, started = held_upload
                pid = app.create_project("Synthetic retained parser fixture")["id"]
                app.add_source(pid, "fixture.csv", b"name,value\\nsynthetic,1\\n")
                assert started.wait(5)
                (tmp_path / "case.marker").write_text("unfinished parser evidence")
        ''')
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("Background workers outlived test teardown: ['parse_0']", result.stdout)
        self.assertIn("1 error", result.stdout)
        self.assertIn("CI tmp_path: removed=0", result.stdout)
        self.assertNotIn("CI tmp_path cleanup failed", result.stdout)
        self.assertEqual({p.read_text() for p in (root / "pytest-tmp").glob("*/case.marker")},
                         {"unfinished parser evidence"})
        self.assertTrue(list((root / "pytest-tmp").glob("test_upload*/data/blobs/*/*")))

    def test_opt_in_requires_ci_and_disallows_early_builtin_cleanup(self):
        for ci, extra in (("false", ()), ("true", ("-o", "tmp_path_retention_policy=failed"))):
            with self.subTest(ci=ci, extra=extra):
                _, result = self.run_session('def test_pass(): pass', ci=ci, extra=extra)
                self.assertEqual(result.returncode, 4, result.stdout + result.stderr)
                self.assertIn("requires CI=true and tmp_path_retention_policy=all", result.stderr)
