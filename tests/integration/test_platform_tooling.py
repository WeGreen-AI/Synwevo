"""The root commands and checks (scripts/platform.py, scripts/check_boundaries.py).

They are what every document tells a new developer to run first, and they had regressed without
any test noticing: `setup` imported the package it was about to install, `test docs` walked the
letters of "docs", and `down` would signal whatever process had inherited a stale pid.
"""
from __future__ import annotations

import importlib.util
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from synwevo_core.http import repo_root

ROOT = repo_root()


def _load(rel: str, name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture()
def platform():
    return _load("scripts/platform.py", "_synwevo_platform")


# ---------------------------------------------------------------- platform.py
def test_setup_runs_on_an_interpreter_that_has_nothing_from_this_repository():
    """A clean checkout has no synwevo_core anywhere. `-S` drops site-packages (and with it the
    editable installs in this venv), so this is the interpreter a new developer starts with."""
    out = subprocess.run([sys.executable, "-I", "-S", str(ROOT / "scripts" / "platform.py"), "--help"],
                         capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    assert "setup" in out.stdout
    probe = subprocess.run([sys.executable, "-I", "-S", "-c", "import synwevo_core"],
                           capture_output=True, text=True)
    assert probe.returncode != 0, "这个探针解释器本应看不到 synwevo_core"


def test_setup_installs_the_modules_that_exist(platform):
    for m in platform.MODULES:
        assert (ROOT / m / "pyproject.toml").is_file(), f"setup 会去装一个不存在的模块：{m}"


@pytest.mark.parametrize("suite, dirs", [
    ("docs", ["apps/docs/tests"]),
    ("identity", ["services/identity/tests"]),
    ("integration", ["tests/integration"]),
    ("all", ["apps/docs/tests", "services/identity/tests", "tests/integration"]),
])
def test_each_documented_test_command_runs_the_right_suite(platform, monkeypatch, suite, dirs):
    ran = []
    # TEST DOUBLE: records the command instead of starting pytest inside pytest.
    monkeypatch.setattr(platform, "_run", lambda cmd, cwd=None: ran.append(cmd) or 0)
    assert platform.test(type("A", (), {"suite": suite})()) == 0
    assert ran and ran[0][-len(dirs):] == dirs


def test_down_does_not_signal_a_process_that_merely_inherited_the_pid(platform, tmp_path, monkeypatch):
    """A pid file outlives a crash or a reboot. The number it holds may now belong to anything."""
    victim = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        monkeypatch.setattr(platform, "RUN", tmp_path)
        (tmp_path / "docs.pid").write_text(str(victim.pid))
        assert platform._alive("docs") is None, "pid 属于别的程序时不应被当成我们的服务"
        platform.down(type("A", (), {"services": ["docs"]})())
        time.sleep(0.2)
        assert victim.poll() is None, "down 向一个无关进程发了信号"
        assert not (tmp_path / "docs.pid").exists(), "过期的 pid 文件应被清掉"
    finally:
        victim.send_signal(signal.SIGKILL)
        victim.wait(timeout=10)


def test_a_relative_data_dir_means_the_same_directory_from_anywhere(platform, monkeypatch, tmp_path):
    monkeypatch.setenv("SYNWEVO_DATA_DIR", "some/relative/data")
    monkeypatch.chdir(tmp_path)
    assert platform.data_dir() == str((ROOT / "some/relative/data").resolve())


def test_a_venv_that_cannot_import_the_platform_is_reported_not_re_executed_forever(tmp_path):
    """A .venv that exists without synwevo_core (setup failed after creating it) made every
    command but `setup` exec itself into that venv, which exec'd itself again, forever — `status`
    never returned and never said why."""
    (tmp_path / "scripts").mkdir()
    shutil.copy(ROOT / "scripts" / "platform.py", tmp_path / "scripts" / "platform.py")
    made = subprocess.run([sys.executable, "-m", "venv", "--without-pip", str(tmp_path / ".venv")],
                          capture_output=True, text=True, timeout=120)
    assert made.returncode == 0, made.stderr
    clean = {"PATH": "/usr/bin:/bin", "HOME": str(tmp_path)}
    for interpreter in ([sys.executable, "-I", "-S"],                 # hands over to the venv once
                        [str(tmp_path / ".venv" / "bin" / "python")]):  # already is the venv
        out = subprocess.run([*interpreter, str(tmp_path / "scripts" / "platform.py"), "status"],
                             capture_output=True, text=True, timeout=60, env=clean)
        assert out.returncode == 2, out.stdout + out.stderr
        assert "导入不了 synwevo_core" in out.stderr and "setup" in out.stderr


def test_paths_typed_by_the_person_mean_where_they_are_standing(platform, monkeypatch, tmp_path):
    """The snapshot tool runs with the repository root as its working directory, so a relative
    archive or target path passed through unchanged pointed somewhere else."""
    ran = []
    # TEST DOUBLE: records the command instead of running a backup or restore.
    monkeypatch.setattr(platform, "_run", lambda cmd, cwd=None: ran.append(cmd) or 0)
    monkeypatch.chdir(tmp_path)
    here = tmp_path.resolve()
    A = type("A", (), {})

    def args(**kw):
        a = A()
        a.__dict__.update(kw)
        return a
    platform.verify_restore(args(backup="b.tar.gz"))
    platform.restore(args(backup="b.tar.gz", into="restored", force=False, no_journal=False))
    platform.backup(args(out="backups"))
    verify, restore, backup = ran
    assert verify[verify.index("--archive") + 1] == str(here / "b.tar.gz")
    assert restore[restore.index("--archive") + 1] == str(here / "b.tar.gz")
    assert restore[restore.index("--into") + 1] == str(here / "restored")
    assert backup[backup.index("--out") + 1] == str(here / "backups")


# ---------------------------------------------------------------- check_boundaries.py
@pytest.fixture()
def boundaries():
    return _load("scripts/check_boundaries.py", "_synwevo_check_boundaries")


def _tree(root: Path, files: dict[str, str]) -> None:
    for rel, text in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")


def _problems(cb, root: Path, monkeypatch, capsys) -> str:
    monkeypatch.setattr(cb, "ROOT", root)
    rc = cb.main()
    err = capsys.readouterr().err
    return err if rc else ""


PYPROJECT = '[project]\nname = "synwevo-core"\ndependencies = [{deps}]\n'


def test_an_undeclared_third_party_import_fails_the_check(boundaries, tmp_path, monkeypatch, capsys):
    """synwevo_core imported authlib while declaring only fastapi/starlette/argon2: installed on
    its own it raised ImportError, and only worked because another package pulled authlib in."""
    _tree(tmp_path, {
        "packages/core/pyproject.toml": PYPROJECT.format(deps='"fastapi"'),
        "packages/core/requirements.txt": "fastapi==1.0\n",
        "packages/core/synwevo_core/oidc.py": "import json\nfrom authlib.jose import jwt\nimport fastapi\n",
    })
    err = _problems(boundaries, tmp_path, monkeypatch, capsys)
    assert "authlib" in err and "json" not in err and "`fastapi`" not in err


def test_a_declared_dependency_without_a_pinned_version_fails(boundaries, tmp_path, monkeypatch, capsys):
    _tree(tmp_path, {
        "packages/core/pyproject.toml": PYPROJECT.format(deps='"fastapi", "authlib"'),
        "packages/core/requirements.txt": "fastapi==1.0\n",
        "packages/core/synwevo_core/oidc.py": "from authlib.jose import jwt\n",
    })
    assert "没有固定版本" in _problems(boundaries, tmp_path, monkeypatch, capsys)


def test_the_external_sdk_may_not_import_the_repository(boundaries, tmp_path, monkeypatch, capsys):
    _tree(tmp_path, {"apps/docs/sdk/synwevo_sdk/client.py": "from synwevo_core import env\n"})
    assert "apps/docs/sdk/synwevo_sdk/client.py" in _problems(boundaries, tmp_path, monkeypatch, capsys)


def test_one_shell_may_not_import_another(boundaries, tmp_path, monkeypatch, capsys):
    _tree(tmp_path, {
        "apps/portal/web/src/main.tsx": 'import { Grid } from "@synwevo/docs-web";\n',
        "apps/docs/web/src/x.ts": 'import y from "../../../portal/web/src/y";\n',
        "apps/docs/web/src/ok.ts": 'import { useToast } from "@synwevo/ui";\nimport z from "./z";\n',
    })
    err = _problems(boundaries, tmp_path, monkeypatch, capsys)
    assert "@synwevo/docs-web" in err and "跳出了 apps/docs/web" in err and "ok.ts" not in err


def test_the_walk_never_descends_into_the_data_directory(boundaries, tmp_path, monkeypatch, capsys):
    """data/ holds every user blob; statting them all is how this check got slower with use."""
    _tree(tmp_path, {"data/blobs/ab/evil.py": "import synwevo_docs\n",
                     "tests/ok.py": "import synwevo_core\n"})
    seen = [p.relative_to(tmp_path).as_posix() for p in
            (monkeypatch.setattr(boundaries, "ROOT", tmp_path) or boundaries.walk())]
    assert "tests/ok.py" in seen and not any(s.startswith("data/") for s in seen)


def test_a_source_directory_named_like_a_skipped_one_is_still_checked(boundaries, tmp_path, monkeypatch, capsys):
    """The repository's data/, testdata/ … are pruned where they are, not by name at any depth:
    a package directory called data/ would otherwise escape the import boundary unnoticed."""
    _tree(tmp_path, {"apps/docs/synwevo_docs/data/loader.py": "import synwevo_portal\n",
                     "apps/docs/synwevo_docs/testdata/gen.py": "import sys\nsys.path.insert(0, 'x')\n",
                     "data/blobs/ab/evil.py": "import synwevo_portal\n"})
    err = _problems(boundaries, tmp_path, monkeypatch, capsys)
    assert "apps/docs/synwevo_docs/data/loader.py" in err and "synwevo_portal" in err
    assert "apps/docs/synwevo_docs/testdata/gen.py" in err
    assert "data/blobs" not in err, "仓库根目录的 data/ 仍然不进入"


def test_the_repository_itself_passes():
    out = subprocess.run([sys.executable, str(ROOT / "scripts" / "check_boundaries.py")],
                         capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr


# ---------------------------------------------------------------- container deployment
def _compose() -> dict:
    import yaml
    return yaml.safe_load((ROOT / "infra" / "docker-compose.yml").read_text(encoding="utf-8"))


def test_compose_pins_what_describes_the_container_itself():
    """F027: compose read .env.example as its env_file, whose local-development values
    (SYNWEVO_HOST=127.0.0.1, SYNWEVO_DATA_DIR=./data) overrode the image: the server listened on
    the container's own loopback, which the published port cannot reach, and put the database at
    /app/data — unwritable for the unprivileged user, and outside the volume if it were not."""
    svc = _compose()["services"]["synwevo"]
    env_files = [e["path"] if isinstance(e, dict) else e for e in svc.get("env_file", [])]
    assert not any(p.endswith(".env.example") for p in env_files), "示例文件不能直接当成运行配置"
    assert all(isinstance(e, str) or e.get("required") is False for e in svc.get("env_file", [])), \
        "运营方的 .env 应当可以没有"
    env = svc["environment"]
    assert env["SYNWEVO_HOST"] == "0.0.0.0"
    volume_target = svc["volumes"][0].split(":")[1]
    assert env["SYNWEVO_DATA_DIR"] == volume_target == "/data", "数据目录必须就是挂载的卷"
    assert all(p.startswith("127.0.0.1:") for p in svc["ports"]), "对外只经回环地址发布"


def test_the_environment_block_wins_over_a_hostile_env_file(tmp_path):
    """Checked with Compose itself when it is installed: a .env that says loopback and ./data
    must not move the container's bind address or its database. `config` starts nothing."""
    import shutil
    if not shutil.which("docker"):
        pytest.skip("本机没有 docker")
    infra = tmp_path / "infra"
    infra.mkdir()
    text = (ROOT / "infra" / "docker-compose.yml").read_text(encoding="utf-8")
    (infra / "docker-compose.yml").write_text(text.replace("path: ../.env", "path: ./hostile.env"),
                                              encoding="utf-8")
    (infra / "hostile.env").write_text("SYNWEVO_HOST=127.0.0.1\nSYNWEVO_DATA_DIR=./data\n", encoding="utf-8")
    out = subprocess.run(["docker", "compose", "-f", str(infra / "docker-compose.yml"), "config",
                          "--format", "json"], capture_output=True, text=True, timeout=60)
    if out.returncode != 0 and "Cannot connect" in out.stderr:
        pytest.skip("docker 不可用")
    assert out.returncode == 0, out.stderr
    import json
    env = json.loads(out.stdout)["services"]["synwevo"]["environment"]
    assert env["SYNWEVO_HOST"] == "0.0.0.0" and env["SYNWEVO_DATA_DIR"] == "/data"


def test_the_image_runs_unprivileged_with_its_data_in_the_volume():
    text = (ROOT / "infra" / "Dockerfile").read_text(encoding="utf-8")
    final = text[text.rindex("\nFROM "):]
    assert "SYNWEVO_DATA_DIR=/data" in final and "SYNWEVO_HOST=0.0.0.0" in final
    assert 'VOLUME ["/data"]' in final
    assert "\nUSER synwevo" in final and "chown synwevo /data" in final
    assert "pip check" in final, "镜像构建要核对每个模块声明的依赖都已安装"
    assert "infra/server/requirements.txt" in final, "镜像按固定版本的并集安装"


@pytest.mark.parametrize("command", ["up", "down", "restart"])
def test_default_service_commands_accept_an_empty_list(platform, monkeypatch, command):
    # TEST DOUBLE: exercise argparse without starting or stopping the developer's services.
    selected = []
    monkeypatch.setattr(platform, command, lambda args: selected.extend(platform._selected(args)) or 0)
    monkeypatch.setattr(sys, "argv", ["platform.py", command])
    assert platform.main() == 0
    assert selected == ["accounts", "portal", "docs"]


@pytest.mark.parametrize("command", ["up", "down", "restart"])
def test_a_misspelled_service_is_rejected_before_any_action(platform, monkeypatch, command):
    monkeypatch.setattr(sys, "argv", ["platform.py", command, "docss"])
    with pytest.raises(SystemExit) as error:
        platform.main()
    assert error.value.code == 2


def test_a_module_name_in_unrelated_arguments_does_not_make_a_process_ours(platform):
    victim = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)",
                               "synwevo_server.run"])
    try:
        assert not platform._is_ours(victim.pid)
    finally:
        victim.terminate()
        victim.wait(timeout=10)


def test_up_refuses_an_occupied_port_without_registering_clients(platform, tmp_path, monkeypatch):
    import socket
    from types import SimpleNamespace
    # SYNTHETIC FIXTURE: an unrelated local listener, with no access to any real data.
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        port = listener.getsockname()[1]
        monkeypatch.setattr(platform, "RUN", tmp_path / "run")
        monkeypatch.setattr(platform, "SERVICES", {
            "docs": ("app", port, f"http://127.0.0.1:{port}", None, None)})
        monkeypatch.setenv("SYNWEVO_DATA_DIR", str(tmp_path / "synthetic"))
        # TEST DOUBLE: DNS is unrelated to port ownership; registration must not be reached.
        monkeypatch.setattr(platform, "check_hostnames", lambda: True)
        def forbidden():
            pytest.fail("occupied-port refusal must precede database/client registration")
        monkeypatch.setattr(platform, "register_clients", forbidden)
        assert platform.up(SimpleNamespace(services=["docs"], init=True)) == 1
        assert not (tmp_path / "synthetic").exists()
        assert not (tmp_path / "run" / "docs.pid").exists()


# ---------------------------------------------------------------- check_brand.py
@pytest.fixture()
def brand(tmp_path, monkeypatch):
    checker = _load("scripts/check_brand.py", "_synwevo_check_brand")
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    monkeypatch.setattr(checker, "ROOT", tmp_path)
    return checker


def test_brand_checks_untracked_source_with_spaces_and_unicode(brand, tmp_path, capsys):
    name = "new source 新文件.py"
    (tmp_path / name).write_text('TITLE = "Doc' + 'Weave editor"\n')
    assert brand.main() == 1
    assert name + ":1:" in capsys.readouterr().err


def test_brand_classifies_untracked_protocol_identifiers(brand, tmp_path, capsys):
    (tmp_path / "protocol.py").write_text('AUDIENCE = "docweave-api"\n')
    assert brand.main() == 0
    assert "已归类 1 处" in capsys.readouterr().out


def test_brand_handles_deleted_tracked_files_and_ignores_runtime_data(brand, tmp_path, capsys):
    _tree(tmp_path, {"deleted.py": "# old source\n", ".gitignore": "generated/\n",
                     "generated/ignored.py": "Doc" + "Weave UI\n",
                     "data/private.txt": "Doc" + "Weave private data\n",
                     ".env.local": "Doc" + "Weave private settings\n",
                     "ok.py": "# Synwevo\n"})
    # Synthetic tracked runtime files test the defense even when somebody used git add -f.
    subprocess.run(["git", "add", "deleted.py", "data/private.txt", ".env.local"],
                   cwd=tmp_path, check=True)
    (tmp_path / "deleted.py").unlink()
    assert brand.main() == 0
    output = capsys.readouterr()
    assert "private" not in output.out + output.err


def test_brand_refuses_a_failed_inventory(tmp_path, monkeypatch, capsys):
    checker = _load("scripts/check_brand.py", "_synwevo_check_brand_no_git")
    monkeypatch.setattr(checker, "ROOT", tmp_path)
    # tmp_path is outside the repository and intentionally has no .git.
    assert checker.main() == 2
    output = capsys.readouterr()
    assert "无法枚举" in output.err and "没有未分类" not in output.out


def test_nested_sdk_distribution_cannot_declare_a_server_dependency(boundaries, tmp_path, monkeypatch, capsys):
    _tree(tmp_path, {
        "apps/docs/sdk/python/pyproject.toml":
            '[project]\nname = "synwevo-docs-sdk"\ndependencies = ["synwevo-core"]\n',
        "apps/docs/sdk/python/synwevo_docs_sdk/client.py": "import json\n",
    })
    error = _problems(boundaries, tmp_path, monkeypatch, capsys)
    assert "apps/docs/sdk/python" in error and "synwevo-core" in error


def test_down_does_not_signal_the_same_module_in_another_checkout(platform, tmp_path, monkeypatch):
    # SYNTHETIC FIXTURE: a sleeping module with the same launch arguments in another directory.
    other = tmp_path / "other-checkout"
    _tree(other, {"synwevo_server/__init__.py": "",
                  "synwevo_server/run.py": "import time\ntime.sleep(60)\n"})
    victim = subprocess.Popen([sys.executable, "-m", "synwevo_server.run"], cwd=other)
    run = tmp_path / "run"
    run.mkdir()
    try:
        monkeypatch.setattr(platform, "RUN", run)
        (run / "docs.pid").write_text(str(victim.pid))
        assert platform._alive("docs") is None
        platform.down(type("A", (), {"services": ["docs"]})())
        assert victim.poll() is None
    finally:
        victim.terminate()
        victim.wait(timeout=10)


def test_the_source_offer_contains_the_locked_installation_inputs():
    from synwevo_docs.licensing import _archive_files
    offered = {name for _, name in _archive_files()}
    assert {"scripts/platform.py", "requirements-dev.txt", "infra/server/requirements.txt",
            "infra/server/requirements.lock", "infra/server/requirements-build.txt"} <= offered
