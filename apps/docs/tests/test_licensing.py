"""Licensing claims must match what the deployment actually ships.

Three statements get conflated routinely — "does not use PyMuPDF", "does not contain MuPDF"
and "has no AGPL dependency" — and the service used to report only the first while wording it
as the third: with SYNWEVO_DOCS_PDF_ENGINE=pdfium and PyMuPDF still installed, /api/about said
「未加载 AGPL 组件」while the AGPL MuPDF binary was still in the image and two fallbacks still
called into it.

Separately, the Corresponding Source archive skipped every path missing from the running
directory in silence, so the deployed container served 90 entries of backend-only while the
bundled README claimed it also held the frontend, scripts and docs. That is precisely the
failure AGPL-3.0 §13 exists to prevent.
"""
from __future__ import annotations

import io
import os
import tarfile
from pathlib import Path

from synwevo_docs import licensing, source_manifest

ROOT = __import__("synwevo_core.http", fromlist=["repo_root"]).repo_root()

# TEST FIXTURE values: the commit and commit time an image build would have been stamped with.
FIXTURE_COMMIT = "0123456789abcdef0123456789abcdef01234567"
FIXTURE_COMMIT_TIME = 1767225600


def _as_built(root: Path) -> Path:
    """TEST FIXTURE: finish a tree as the image build does — the build context's files are in
    place, and the build writes the release manifest from exactly them."""
    source_manifest.write(root, commit=FIXTURE_COMMIT, commit_time=FIXTURE_COMMIT_TIME)
    return root


def test_agpl_obligation_follows_the_binary_not_the_engine_setting(monkeypatch):
    """Selecting pdfium changes which engine runs; it removes nothing from the image."""
    from synwevo_docs.engine import pdf_engine
    monkeypatch.setattr(pdf_engine, "ENGINE", "pdfium")
    monkeypatch.setattr(licensing, "pymupdf_installed", lambda: True)
    a = licensing.about()
    assert a["pymupdf_used_by_engine"] is False       # the engine really is pdfium
    assert a["pymupdf_installed"] is True
    assert a["agpl_component_active"] is True, "装着 AGPL 组件就不能说没有 AGPL 依赖"
    assert "仍然安装了 PyMuPDF" in a["notice"]
    assert "未加载 AGPL 组件" not in a["notice"]


def test_no_agpl_claim_only_when_pymupdf_is_absent(monkeypatch):
    from synwevo_docs.engine import pdf_engine
    monkeypatch.setattr(pdf_engine, "ENGINE", "pdfium")
    monkeypatch.setattr(licensing, "pymupdf_installed", lambda: False)
    a = licensing.about()
    assert a["agpl_component_active"] is False
    assert "无第三方 AGPL 依赖" in a["notice"] and "不含 MuPDF" in a["notice"]
    assert a["license"] == "AGPL-3.0-or-later", "程序自身的许可与第三方依赖分开说"


def test_pymupdf_engine_keeps_the_section_13_offer(monkeypatch):
    from synwevo_docs.engine import pdf_engine
    monkeypatch.setattr(pdf_engine, "ENGINE", "pymupdf")
    monkeypatch.setattr(licensing, "pymupdf_installed", lambda: True)
    a = licensing.about()
    assert a["pymupdf_used_by_engine"] and a["agpl_component_active"]
    assert "第 13 条" in a["notice"]


def test_archive_says_so_when_it_is_incomplete(monkeypatch, tmp_path):
    """Simulate a partial image: the business package present, everything else absent."""
    (tmp_path / "apps" / "docs" / "synwevo_docs").mkdir(parents=True)
    (tmp_path / "apps" / "docs" / "synwevo_docs" / "x.py").write_text("# code\n", encoding="utf-8")
    monkeypatch.setattr(licensing, "ROOT", _as_built(tmp_path))

    status = licensing.source_status()
    assert not status["complete"]
    # The shared packages and the account service are part of the running program after the
    # platform restructure, so their absence has to be reported like any other gap.
    assert "packages" in status["missing"] and "docs" in status["missing"]
    assert "services/identity" in status["missing"] and "apps/suite" in status["missing"]

    blob, name = licensing.source_archive()
    tar = tarfile.open(fileobj=io.BytesIO(blob))
    offer = tar.extractfile([m for m in tar.getmembers() if m.name.endswith("SOURCE_OFFER.txt")][0]).read().decode()
    assert "并不完整" in offer, "缺内容时必须说出来，不能继续声称完整"
    assert "  - packages\n" in offer and "  - apps/messages\n" in offer, "必须逐条列出缺了什么"
    assert "包含所列全部内容" not in offer


def test_archive_reports_complete_when_everything_is_present(monkeypatch, tmp_path):
    for rel in licensing.INCLUDE:
        p = tmp_path / rel
        if "." in Path(rel).name:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text("x\n", encoding="utf-8")
        else:
            p.mkdir(parents=True, exist_ok=True)
            (p / "f.txt").write_text("x\n", encoding="utf-8")
    monkeypatch.setattr(licensing, "ROOT", _as_built(tmp_path))
    assert licensing.source_status()["complete"]
    blob, _ = licensing.source_archive()
    tar = tarfile.open(fileobj=io.BytesIO(blob))
    offer = tar.extractfile([m for m in tar.getmembers() if m.name.endswith("SOURCE_OFFER.txt")][0]).read().decode()
    assert "包含所列全部内容" in offer and "并不完整" not in offer


def _dockerfile_instructions(text: str) -> list[str]:
    """Logical instructions: continuation lines joined, comments inside a continuation dropped."""
    logical, buf = [], ""
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#") and buf:
            continue                                # comment inside a continued instruction
        if stripped.endswith("\\"):
            buf += stripped[:-1] + " "
            continue
        logical.append(buf + stripped)
        buf = ""
    return logical


def _dockerfile_copy_sources(text: str) -> list[str]:
    """The source paths of every COPY from the build context (not from another stage), exactly.

    Parsed, not substring-matched: `"docs/" in copied` was satisfied by `COPY apps/docs/ …`, so
    removing `COPY docs/ docs/` would not have failed the test that exists to catch it.
    """
    sources = []
    for instr in _dockerfile_instructions(text):
        tokens = instr.split()
        if not tokens or tokens[0].upper() != "COPY":
            continue
        args = [t for t in tokens[1:] if not t.startswith("--")]
        if any(t.startswith("--from=") for t in tokens[1:]):
            continue                                # built output from another stage
        sources += [a.rstrip("/") for a in args[:-1]]
    return sources


def _dockerfile_editable_installs(text: str) -> list[str]:
    """The paths installed with `pip install -e PATH` in RUN instructions: the packages that run."""
    import re
    paths = []
    for instr in _dockerfile_instructions(text):
        if not instr.upper().startswith("RUN "):
            continue
        for command in re.split(r"&&|\|\||;", instr[4:]):
            tokens = command.split()
            if "pip" not in tokens or "install" not in tokens:
                continue                            # `echo -e` is not an installation
            paths += [tokens[i + 1].rstrip("/") for i, t in enumerate(tokens[:-1]) if t in ("-e", "--editable")]
    return paths


def _covered(rel: str, sources: list[str]) -> bool:
    return any(rel == src or rel.startswith(src + "/") for src in sources)


def test_docker_image_carries_every_path_the_offer_promises():
    """The archive is built from the files on disk, so the image must contain them — every entry
    of licensing.INCLUDE, not a hand-picked list that can drift from it.

    The deployed container served a backend-only archive because the Dockerfile COPYed only
    backend/ and the compiled frontend/dist. Every entry exists now that LICENSE is chosen, so a
    missing one is a failure rather than a skip: inside the CI image this test runs against the
    image's own files, and a skip there would let an image without LICENSE pass in silence.
    """
    missing = [rel for rel in licensing.INCLUDE if not (ROOT / rel).exists()]
    assert not missing, f"对应源代码包承诺的这些路径在运行目录里不存在：{missing}"
    sources = _dockerfile_copy_sources((ROOT / "infra" / "Dockerfile").read_text(encoding="utf-8"))
    assert sources, "没有解析出任何 COPY"
    for rel in licensing.INCLUDE:
        assert _covered(rel, sources), f"Dockerfile 没有把 {rel} 放进镜像，对应源代码包会缺这一块"


def test_every_path_the_image_copies_or_installs_is_in_the_offer():
    """The other direction: what the image carries and runs is what the archive offers.

    Testing only INCLUDE ⊆ COPY let apps/suite and apps/messages — mounted by synwevo_server in the
    same process, COPYed into the image and installed with `pip install -e` — stay out of the
    Corresponding Source while the offer would have called itself complete.
    """
    text = (ROOT / "infra" / "Dockerfile").read_text(encoding="utf-8")
    copied, installed = _dockerfile_copy_sources(text), _dockerfile_editable_installs(text)
    assert copied and installed, "没有解析出 COPY 或 pip install -e"
    include = list(licensing.INCLUDE)
    missing = sorted({src for src in copied + installed if not _covered(src, include)})
    assert not missing, f"镜像复制或安装了这些路径，对应源代码包却不含它们：{missing}"


def test_every_tracked_path_is_offered_or_excluded_with_a_reason():
    """Completeness measured against the repository, not against the Dockerfile.

    The two tests above compare INCLUDE with the image's COPY lines, which is the same list twice:
    infra/production (the compose file, nginx and the deploy receiver production actually runs
    from), .dockerignore, .env.example and conftest.py were in neither, while the offer said it
    held the deployment's build and run scripts. Every tracked file must now fall under INCLUDE or
    under NOT_OFFERED, which names its reason.
    """
    import shutil
    import subprocess
    if shutil.which("git") is None or not (ROOT / ".git").exists():
        import pytest
        pytest.skip("不是 Git 检出（镜像由 git archive 构建，没有 .git）：这一项在检出里核对")
    tracked = subprocess.run(["git", "-C", str(ROOT), "ls-files", "-z"], check=True,
                             capture_output=True).stdout.decode("utf-8").split("\0")
    tracked = [t for t in tracked if t]
    assert len(tracked) > 1000, "git ls-files 没有列出仓库文件"
    covered = list(licensing.INCLUDE) + list(licensing.NOT_OFFERED)
    stray = sorted({t.split("/")[0] if "/" not in t else "/".join(t.split("/")[:2])
                    for t in tracked if not _covered(t, covered)})
    assert not stray, f"这些受版本控制的路径既不在对应源代码包里，也没有写明不提供的理由：{stray}"
    assert all(reason.strip() for reason in licensing.NOT_OFFERED.values())
    assert not set(licensing.INCLUDE) & set(licensing.NOT_OFFERED)


def test_filled_in_configuration_never_enters_the_archive(monkeypatch, tmp_path):
    """The archive is anonymous. An operator's runtime.env or .env beside the templates stays out —
    even when it did reach the release list (a build context that carried it, a file tracked by
    mistake): the name rule is kept behind the manifest, not replaced by it."""
    for rel in ("infra/production/runtime.env", "infra/production/smtp.env", ".env", ".env.local", "apps/docs/.env"):
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text("SECRET=fixture\n", encoding="utf-8")
    for rel in ("infra/production/runtime.env.example", ".env.example", "apps/docs/x.py"):
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text("# template\n", encoding="utf-8")
    monkeypatch.setattr(licensing, "ROOT", _as_built(tmp_path))
    offered = {name for _, name in licensing._archive_files()}
    assert {"infra/production/runtime.env.example", ".env.example", "apps/docs/x.py"} <= offered
    assert not {n for n in offered if n.endswith(".env") or n.split("/")[-1].startswith(".env.local")}
    dockerignore = (ROOT / ".dockerignore").read_text(encoding="utf-8").split()
    assert "**/*.env" in dockerignore, "镜像构建上下文也要挡住填写过的环境文件"


def test_the_offer_holds_every_mounted_application_and_the_licence_files():
    offered = {name for _, name in licensing._archive_files()}
    for must in ("apps/suite/synwevo_suite/app.py", "apps/suite/web/src/main.tsx",
                 "apps/messages/synwevo_messages/app.py", "apps/messages/web/src/main.tsx",
                 "apps/portal/web/src/main.tsx", "packages/ui/src/index.ts",
                 "LICENSE", "NOTICE", "TRADEMARKS.md", "apps/docs/sdk/LICENSE",
                 "apps/docs/tessdata/LICENSE", "apps/docs/tessdata/README.md",
                 "infra/production/compose.yaml", "infra/production/nginx.conf", "infra/production/receive.py",
                 ".github/workflows/production.yml", ".dockerignore", ".env.example", "conftest.py"):
        assert must in offered, f"对应源代码包缺少 {must}"
    assert not any(n.endswith(".traineddata") for n in offered), "第三方 OCR 模型不是源代码，不放进包里"


def test_no_source_file_is_left_out_for_its_size():
    """MAX_FILE used to drop large files without a word; now the offer names them, and the
    repository is kept to none."""
    assert licensing.oversized() == []


def test_an_oversized_file_is_named_in_the_offer(monkeypatch, tmp_path):
    for rel in licensing.INCLUDE:
        p = tmp_path / rel
        if "." in Path(rel).name:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text("x\n", encoding="utf-8")
        else:
            p.mkdir(parents=True, exist_ok=True)
            (p / "f.txt").write_text("x\n", encoding="utf-8")
    (tmp_path / "docs" / "big.bin").write_bytes(b"\0" * 64)
    monkeypatch.setattr(licensing, "ROOT", _as_built(tmp_path))
    monkeypatch.setattr(licensing, "MAX_FILE", 32)
    blob, _ = licensing.source_archive()
    tar = tarfile.open(fileobj=io.BytesIO(blob))
    names = tar.getnames()
    offer = tar.extractfile([m for m in tar.getmembers() if m.name.endswith("SOURCE_OFFER.txt")][0]).read().decode()
    assert not any(n.endswith("docs/big.bin") for n in names)
    assert "  - docs/big.bin" in offer and "包含所列全部内容" not in offer


# Files an operator or an editor leaves beside the source: the shapes a name blocklist missed.
# Placeholder contents only (TEST FIXTURE).
PLANTED = ("infra/production/runtime.env.bak", "infra/production/runtime.env~",
           "infra/production/.runtime.env.swp", "infra/production/smtp.env.orig",
           "infra/production/runtime.env.2026-10-01", "infra/production/origin.key",
           "infra/production/certs/origin.pem", "infra/production/htpasswd",
           "infra/production/deploy_ed25519", "scripts/.pgpass", "apps/docs/.npmrc", "apps/docs/.pypirc",
           "tests/.secrets.toml", "packages/core/credentials.json", ".github/deploy.key",
           "docs/agent-work/.coordinator.lock", "docs/agent-work/CLAUDE_CODE_HANDOFF_probe.txt",
           "scripts/probe_api.txt", "apps/docs/backup.sqlite3")
SOURCE = ("apps/docs/synwevo_docs/x.py", "infra/production/compose.yaml", "infra/production/runtime.env.example",
          "scripts/platform.py", "tests/test_x.py", ".github/workflows/ci.yml", "docs/a.md", "LICENSE")


def _plant(root: Path, names) -> None:
    for rel in names:
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text("PLACEHOLDER-NOT-A-SECRET\n", encoding="utf-8")


def _git(root: Path, *args: str) -> str:
    import subprocess
    return subprocess.run(["git", "-C", str(root), "-c", "user.name=fixture", "-c", "user.email=fixture@example.test",
                           "-c", "commit.gpgsign=false", *args],
                          check=True, capture_output=True, text=True).stdout


def test_files_beside_the_source_in_a_checkout_are_never_offered(monkeypatch, tmp_path):
    """Running from a checkout, the release list is `git ls-files`: an untracked file under an
    offered directory — a backup of runtime.env, a swap file, a key, a .pgpass — is not source and
    never leaves the server, whatever its name. The archive used to walk the directories."""
    import shutil
    if shutil.which("git") is None:
        import pytest
        pytest.skip("needs git")
    _plant(tmp_path, SOURCE)
    (tmp_path / ".gitattributes").write_text("docs/evidence/** export-ignore\n", encoding="utf-8")
    _plant(tmp_path, ["docs/evidence/shot.txt"])
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "add", "-A")
    _plant(tmp_path, PLANTED)
    monkeypatch.setattr(licensing, "ROOT", tmp_path)
    offered = {name for _, name in licensing._archive_files()}
    assert set(SOURCE) <= offered
    assert not offered & set(PLANTED), sorted(offered & set(PLANTED))
    assert "docs/evidence/shot.txt" not in offered, "git archive 不导出的文件，检出里也不提供（与镜像一致）"


def test_files_added_after_the_image_build_are_never_offered(monkeypatch, tmp_path):
    """In the image the release list is the manifest the build wrote from its context (a git
    archive): anything that appears in the directory later is not on it."""
    _plant(tmp_path, SOURCE)
    _as_built(tmp_path)
    _plant(tmp_path, PLANTED)
    monkeypatch.setattr(licensing, "ROOT", tmp_path)
    offered = {name for _, name in licensing._archive_files()}
    assert set(SOURCE) <= offered and not offered & set(PLANTED), sorted(offered & set(PLANTED))


def test_without_a_release_list_nothing_is_offered(monkeypatch, tmp_path):
    """Neither a manifest nor a checkout: refuse, never fall back to walking the directory."""
    import pytest
    _plant(tmp_path, SOURCE + PLANTED)
    monkeypatch.setattr(licensing, "ROOT", tmp_path)
    with pytest.raises(licensing.SourceUnavailable) as refused:
        licensing.source_archive()
    assert "git archive" in str(refused.value)
    status = licensing.source_status()
    assert status["available"] is False and status["complete"] is False


def test_the_manifest_writer_needs_a_context_stamped_by_git_archive(monkeypatch, tmp_path, capsys):
    """From a working tree the writer's stamp still reads as git's placeholder: such a build writes
    no manifest (and says so in the build log), so its image refuses to offer rather than offering
    whatever the working tree held. Stamped, it records the files and the commit. (The installed
    file is stamped inside the image and not in a checkout, so both states are set here.)"""
    placeholder = "$" + "Format:%H$", "$" + "Format:%ct$"      # split: this file is not export-subst
    monkeypatch.setattr(source_manifest, "RELEASE_COMMIT", placeholder[0])
    monkeypatch.setattr(source_manifest, "RELEASE_TIME", placeholder[1])
    assert source_manifest.release_stamp() is None, "占位符不应被当成发布提交"
    _plant(tmp_path, SOURCE)
    assert source_manifest.main([str(tmp_path)]) == 0
    assert not (tmp_path / source_manifest.MANIFEST_NAME).exists()
    assert "git archive" in capsys.readouterr().err

    monkeypatch.setattr(source_manifest, "RELEASE_COMMIT", FIXTURE_COMMIT)
    monkeypatch.setattr(source_manifest, "RELEASE_TIME", str(FIXTURE_COMMIT_TIME))
    assert source_manifest.release_stamp() == (FIXTURE_COMMIT, FIXTURE_COMMIT_TIME)
    assert source_manifest.main([str(tmp_path)]) == 0
    manifest = source_manifest.read(tmp_path)
    assert manifest["commit"] == FIXTURE_COMMIT and manifest["files"] == sorted(SOURCE)


def test_git_archive_stamps_the_writer_and_the_manifest_lists_only_the_archive(tmp_path):
    """End to end, with this repository's own writer and its own .gitattributes line: a commit,
    `git archive` of it (what CI and the deploy receiver build from), the writer run from the
    extracted context. The manifest names that commit and exactly the archived files."""
    import shutil
    import subprocess
    import sys
    import pytest
    if shutil.which("git") is None or not (ROOT / ".git").exists():
        # Inside the image the writer is already stamped and .gitattributes is not shipped (it is
        # version-control configuration): this runs in a checkout, as the tracked-path test does.
        pytest.skip("不是 Git 检出（镜像由 git archive 构建，没有 .git）：这一项在检出里核对")
    rel = "apps/docs/synwevo_docs/source_manifest.py"
    lines = [ln for ln in (ROOT / ".gitattributes").read_text(encoding="utf-8").splitlines()
             if ln.strip() and not ln.startswith("#") and "source_manifest" in ln]
    assert lines == [f"{rel} export-subst"], lines
    repo, ctx = tmp_path / "repo", tmp_path / "context"
    (repo / rel).parent.mkdir(parents=True)
    shutil.copyfile(ROOT / rel, repo / rel)
    (repo / ".gitattributes").write_text("\n".join(lines) + "\n", encoding="utf-8")
    _plant(repo, SOURCE)
    _git(repo, "init", "-q")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "fixture")
    head = _git(repo, "rev-parse", "HEAD").strip()
    _plant(repo, PLANTED)                                   # untracked: not in the archive
    raw = subprocess.run(["git", "-C", str(repo), "archive", "--format=tar", "HEAD"], check=True,
                         capture_output=True).stdout
    with tarfile.open(fileobj=io.BytesIO(raw)) as tar:
        tar.extractall(ctx, filter="data")
    run = subprocess.run([sys.executable, "-P", str(ctx / rel), str(ctx)], capture_output=True, text=True)
    assert run.returncode == 0, run.stderr
    manifest = source_manifest.read(ctx)
    assert manifest is not None and manifest["commit"] == head
    assert set(manifest["files"]) == set(SOURCE) | {rel, ".gitattributes"}


def _final_stage(text: str) -> list[str]:
    instructions = _dockerfile_instructions(text)
    last_from = max(i for i, ins in enumerate(instructions) if ins.upper().startswith("FROM "))
    return instructions[last_from + 1:]


def test_the_image_writes_its_manifest_from_the_build_context_alone():
    """The manifest lists what the build context delivered: written after the last COPY from the
    context and before anything the build itself produces (the compiled shells, the installed
    packages' metadata), so a build product can never be offered as source either."""
    stage = _final_stage((ROOT / "infra" / "Dockerfile").read_text(encoding="utf-8"))
    writes = [i for i, ins in enumerate(stage) if ins.upper().startswith("RUN ") and "source_manifest.py" in ins]
    assert len(writes) == 1, "Dockerfile 要在最后阶段写一次发布清单"
    at = writes[0]
    for i, ins in enumerate(stage):
        tokens = ins.split()
        if tokens and tokens[0].upper() == "COPY":
            from_stage = any(t.startswith("--from=") for t in tokens)
            assert (i > at) == from_stage, f"清单必须写在全部上下文 COPY 之后、构建产物之前：{ins}"
        if ins.upper().startswith("RUN ") and _dockerfile_editable_installs(ins):
            assert i > at, "可编辑安装产生的元数据不能先于清单出现"


def test_the_build_context_keeps_backups_swap_files_and_keys_out():
    """`**/*.env` stopped runtime.env but let runtime.env.bak, runtime.env~, a swap file, origin.key,
    htpasswd and .pgpass into a working-tree build — and from there into the image."""
    lines = [ln.strip() for ln in (ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()
             if ln.strip() and not ln.startswith("#")]
    for pattern in ("**/*.env", "**/*.env.*", "**/.*.swp", "**/*~", "**/*.bak", "**/*.orig", "**/*.key",
                    "**/*.pem", "**/*_ed25519", "**/*_rsa", "**/htpasswd", "**/.pgpass", "**/.npmrc",
                    "**/.pypirc", "**/.netrc", "**/credentials.json", "**/.secrets.*", "**/*.sqlite3",
                    "**/*_api.txt", "docs/agent-work/CLAUDE_CODE_HANDOFF_*.txt", "docs/agent-work/.coordinator.lock*"):
        assert pattern in lines, f".dockerignore 缺少 {pattern}"
    # The templates are re-admitted after every exclusion that would match them.
    assert lines.index("!**/*.env.example") > lines.index("**/*.env.*")
    assert lines.index("!**/*.env.example") > lines.index("**/*.env")


def test_archive_members_reveal_no_owner_and_the_archive_is_reproducible(monkeypatch, tmp_path):
    """Members used to carry the server's uid, user name, gid, group and modification times: the
    local account name of a checkout deployment went to every anonymous downloader. Now owner
    0/0 with empty names, the release's commit time, sorted names, gzip without a timestamp — so the
    same release gives the same bytes in every process and on every role (one ETag)."""
    import shutil
    first = tmp_path / "one"
    _plant(first, SOURCE)
    (first / "scripts" / "platform.py").chmod(0o755)
    _as_built(first)
    monkeypatch.setattr(licensing, "ROOT", first)
    blob, name = licensing.source_archive()
    with tarfile.open(fileobj=io.BytesIO(blob)) as tar:
        members = tar.getmembers()
    assert {(m.uid, m.gid, m.uname, m.gname) for m in members} == {(0, 0, "", "")}
    assert {m.mtime for m in members} == {FIXTURE_COMMIT_TIME}
    assert all(m.isfile() for m in members)
    names = [m.name for m in members]
    assert names[:-1] == sorted(names[:-1]) and names[-1].endswith("/SOURCE_OFFER.txt")
    modes = {m.name.split("/", 1)[1]: m.mode for m in members}
    assert modes["scripts/platform.py"] == 0o755 and modes["LICENSE"] == 0o644
    assert name == f"synwevo-source-{FIXTURE_COMMIT[:12]}.tar.gz" and names[0].startswith(f"synwevo-source-{FIXTURE_COMMIT[:12]}/")
    assert blob[4:8] == b"\0\0\0\0", "gzip 头里不带生成时间"

    second = tmp_path / "two"                       # the same release installed elsewhere, later
    shutil.copytree(first, second)
    for f in second.rglob("*"):
        if f.is_file():
            os.utime(f, (1_900_000_000, 1_900_000_000))
    monkeypatch.setattr(licensing, "ROOT", second)
    again, _ = licensing.source_archive()
    assert again == blob, "同一发布在另一个位置、另一个时间生成的包应当逐字节相同"


def _count_filesystem_calls(monkeypatch) -> list:
    calls = []
    for fn in ("stat", "lstat", "scandir", "listdir"):
        real = getattr(os, fn)
        monkeypatch.setattr(os, fn, lambda *a, _real=real, _fn=fn, **k: calls.append(_fn) or _real(*a, **k))
    return calls


def test_repeat_requests_do_not_touch_the_tree(monkeypatch, tmp_path):
    """Each request used to walk and stat the whole tree (about 2,200 files, twice) just to find
    the archive already built: ~120 ms of CPU per anonymous request. The image's release list
    cannot change (read-only root filesystem), so after the first build a request costs nothing."""
    _plant(tmp_path, SOURCE)
    monkeypatch.setattr(licensing, "ROOT", _as_built(tmp_path))
    first = licensing.current_archive()
    calls = _count_filesystem_calls(monkeypatch)
    for _ in range(3):
        again = licensing.current_archive()
    assert again is first and calls == [], calls[:10]


def test_a_checkout_is_listed_again_after_its_ttl(monkeypatch, tmp_path):
    """From a checkout the files can change, so the list is trusted for CHECKOUT_TTL_S and then
    asked of git again; the archive is rebuilt only when what it would hold has changed."""
    import shutil
    import pytest
    if shutil.which("git") is None:
        pytest.skip("needs git")
    _plant(tmp_path, SOURCE)
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "add", "-A")
    monkeypatch.setattr(licensing, "ROOT", tmp_path)
    clock = [1000.0]
    monkeypatch.setattr(licensing, "_now", lambda: clock[0])
    first = licensing.current_archive()
    calls = _count_filesystem_calls(monkeypatch)
    assert licensing.current_archive() is first and calls == []        # within the TTL: nothing
    clock[0] += licensing.CHECKOUT_TTL_S + 1
    monkeypatch.undo()                                                   # real os functions again
    monkeypatch.setattr(licensing, "ROOT", tmp_path)
    monkeypatch.setattr(licensing, "_now", lambda: clock[0])
    assert licensing.current_archive() is first, "没有变化就不重新打包"
    f = tmp_path / "docs" / "a.md"
    f.write_text("changed\n", encoding="utf-8")
    os.utime(f, (1_900_000_000, 1_900_000_000))
    assert licensing.current_archive() is first, "TTL 之内不重新列出"
    clock[0] += licensing.CHECKOUT_TTL_S + 1
    changed = licensing.current_archive()
    assert changed is not first and changed.etag != first.etag


def test_the_install_parser_reads_continued_run_lines():
    text = "RUN pip install --no-deps \\\n      -e packages/core -e apps/docs/ \\\n    && pip check\nRUN echo -e x\n"
    assert _dockerfile_editable_installs(text) == ["packages/core", "apps/docs"]


def test_the_copy_parser_is_not_fooled_by_a_similar_path():
    """The regression the parser exists for: apps/docs/ is not docs/."""
    sources = _dockerfile_copy_sources("COPY apps/docs/ apps/docs/\nCOPY --from=ui /ui/docs docs\n")
    assert _covered("apps/docs/synwevo_docs", sources)
    assert not _covered("docs", sources)


def test_notice_lists_every_third_party_component():
    """NOTICE repeats THIRD_PARTY by hand; this stops the two drifting apart — name, licence, role
    and order, not only "every name appears somewhere"."""
    notice = (ROOT / "NOTICE").read_text(encoding="utf-8")
    block = "\n".join(f"* {name}\n    许可：{lic}\n    用途：{role}\n" for name, lic, role in licensing.THIRD_PARTY)
    assert block in notice, "NOTICE 的第三方组件一节必须与 THIRD_PARTY 逐条一致（名称、许可、用途、顺序）"
    assert "AGPL-3.0" in notice


def _tokens(text: str) -> set[str]:
    """Package names as they appear in a THIRD_PARTY row, normalised like PEP 503 names."""
    import re
    return {re.sub(r"[-_.]+", "-", t.lower()) for t in re.findall(r"@?[A-Za-z0-9][A-Za-z0-9._/-]*", text)}


def _named() -> set[str]:
    return set().union(*(_tokens(name) for name, _lic, _role in licensing.THIRD_PARTY))


# Pins in infra/server/requirements.lock that only build or test the program (pip, setuptools and
# wheel build it; pytest and Playwright test it, with what they bring). Every other pin is installed
# by infra/server/requirements.txt and runs in the deployment.
BUILD_AND_TEST_ONLY = {"greenlet", "iniconfig", "packaging", "pip", "playwright", "pluggy", "pyee",
                       "pygments", "pytest", "pyyaml", "setuptools", "wheel"}


def test_every_runtime_python_pin_is_attributed():
    """The list used to stop at the original Docs set: py-vapid and certifi (MPL-2.0), the Anthropic
    and MCP SDKs, pycrdt and a dozen more ran in production unlisted."""
    import re
    lock = (ROOT / "infra" / "server" / "requirements.lock").read_text(encoding="utf-8")
    pins = {re.sub(r"[-_.]+", "-", m.group(1).lower())
            for m in re.finditer(r"^([A-Za-z0-9][A-Za-z0-9._-]*)==", lock, re.M)}
    assert BUILD_AND_TEST_ONLY <= pins, f"例外表里这些包已不在锁文件中，删掉它们：{sorted(BUILD_AND_TEST_ONLY - pins)}"
    missing = sorted(pins - BUILD_AND_TEST_ONLY - _named())
    assert not missing, f"这些运行时依赖没有写进 THIRD_PARTY 与 NOTICE：{missing}"


def test_every_front_end_runtime_dependency_is_attributed():
    """Each direct runtime dependency of a shell has a row (a scope such as @tiptap counts for its
    packages); every other package the shells bundle carries a licence the catch-all row states."""
    import json
    packages = json.loads((ROOT / "package-lock.json").read_text(encoding="utf-8"))["packages"]
    named = _named()
    direct = set()
    for path, entry in packages.items():
        if path and "node_modules" not in path:                      # a workspace
            direct |= set(entry.get("dependencies") or {})
    direct = {d for d in direct if not d.startswith(("@synwevo/", "@types/"))}

    def covered(dep: str) -> bool:
        canon = next(iter(_tokens(dep)))
        return canon in named or (canon.startswith("@") and canon[1:].split("/")[0] in named)

    missing = sorted(d for d in direct if not covered(d))
    assert not missing, f"这些前端运行时依赖没有写进 THIRD_PARTY 与 NOTICE：{missing}"
    catch_all = [lic for name, lic, _role in licensing.THIRD_PARTY if "package-lock.json" in name]
    assert len(catch_all) == 1
    allowed = {part.strip() for part in catch_all[0].split("/")}
    bundled = {path.removeprefix("node_modules/"): entry.get("license") for path, entry in packages.items()
               if path.startswith("node_modules/") and not entry.get("dev") and not entry.get("devOptional")
               and not entry.get("link")}
    assert bundled, "锁文件里没有非开发依赖？"
    other = sorted(f"{name} ({lic})" for name, lic in bundled.items() if lic not in allowed)
    assert not other, f"这些随界面打包的依赖的许可不在兜底行（{catch_all[0]}）里：{other}"


def test_every_attribution_has_its_translation():
    """The About page shows these rows in three languages; a row without a catalogue entry would
    show Chinese inside the English page."""
    import re
    web = ROOT / "apps" / "docs" / "web" / "src"
    catalogues = "".join((web / f).read_text(encoding="utf-8") for f in ("messages.server.ts", "messages.ts"))
    cjk = re.compile(r"[㐀-鿿]")
    untranslated = sorted({text for row in licensing.THIRD_PARTY for text in row
                           if cjk.search(text) and f'"{text}"' not in catalogues and f"'{text}'" not in catalogues})
    assert not untranslated, f"Docs 外壳的目录缺少这些关于页文案的翻译：{untranslated}"


def test_the_licence_and_component_notices_have_their_translations(monkeypatch):
    """The About page shows `license_notice` and `notice` as they are, in three languages: each of
    the three engine states has its sentence in the server catalogue, word for word."""
    from synwevo_docs.engine import pdf_engine
    catalogue = (ROOT / "apps" / "docs" / "web" / "src" / "messages.server.ts").read_text(encoding="utf-8")
    for engine, installed in (("pymupdf", True), ("pdfium", True), ("pdfium", False)):
        monkeypatch.setattr(pdf_engine, "ENGINE", engine)
        monkeypatch.setattr(licensing, "pymupdf_installed", lambda installed=installed: installed)
        a = licensing.about()
        for text in (a["license_notice"], a["notice"]):
            assert f'"{text}"' in catalogue, f"关于页的这句许可说明没有翻译：{text[:40]}…"
            assert "**" not in text, "页面按纯文本显示，不能带 Markdown 标记"


# SHA-256 of the licence texts as they are published. A licence file is copied, never edited: the
# copyright line and the "or later" choice live in NOTICE and the READMEs, not in the licence text.
#
# The GNU AGPL version 3 is published in two byte forms. They differ only in where two lines of the
# non-operative "How to Apply These Terms" appendix wrap (lines 636-637, "as published by / the
# Free Software Foundation" against "as published / by the Free Software Foundation"), so they are
# the same licence. LICENSE is GitHub's template (choosealicense.com `_licenses/agpl-3.0.txt`: the
# body after its front matter, less the blank line that opens it); the FSF's own file would do as
# well. Either passes; an edit fails.
AGPL_3_0_SHA256 = {
    "8486a10c4393cee1c25392769ddd3b2d6c242d6ec7928e1414efff7dfb2f07ef": "GitHub / choosealicense.com template",
    "0d96a4ff68ad6d4b6f1f30f713b18d5184912ba8dd389f86aa7710db079abcb0": "FSF, https://www.gnu.org/licenses/agpl-3.0.txt",
}
# The Apache License 2.0 exactly as https://www.apache.org/licenses/LICENSE-2.0.txt publishes it.
APACHE_2_0_SHA256 = "cfc7749b96f63bd31c3c42b5c471bf756814053e847c10f3eb003417bc523d30"


def _sha256(path: Path) -> str:
    import hashlib
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_license_file_is_shipped():
    """No skip: the root LICENSE is the unmodified GNU AGPL v3 text wherever the program runs."""
    lic = ROOT / "LICENSE"
    assert lic.is_file(), "LICENSE 不在运行目录里：镜像没有复制它，对应源代码包也会缺它"
    assert _sha256(lic) in AGPL_3_0_SHA256, "LICENSE 必须是未经改动的 GNU AGPL 第 3 版原文"


def test_the_or_later_choice_and_the_exceptions_are_stated():
    """The licence text cannot say "or later" or name the holder; NOTICE and both READMEs do."""
    notice = (ROOT / "NOTICE").read_text(encoding="utf-8")
    assert "AGPL-3.0-or-later" in notice and "either\nversion 3 of the License, or (at your option) any later version" in notice
    assert "Copyright (C) 2026 the Synwevo contributors" in notice
    assert "apps/docs/sdk/" in notice and "Apache-2.0" in notice and "TRADEMARKS.md" in notice
    assert licensing.PROGRAM_LICENSE == "AGPL-3.0-or-later" and licensing.SDK_LICENSE == "Apache-2.0"
    for readme in ("README.md", "README.en.md"):
        text = (ROOT / readme).read_text(encoding="utf-8")
        assert "AGPL-3.0-or-later" in text and "TRADEMARKS.md" in text, readme
        assert "尚未选定许可证" not in text and "has not chosen a license" not in text, readme
    trademarks = (ROOT / "TRADEMARKS.md").read_text(encoding="utf-8")
    assert "packages/ui/brand/" in trademarks and "packages/ui/app-icons/" in trademarks


def test_every_platform_manifest_declares_the_program_licence():
    """`pip show` / package metadata say AGPL-3.0-or-later for every platform package; only the SDKs differ."""
    import json
    import tomllib
    root_package = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))
    manifests = [ROOT / "package.json"] + [ROOT / w / "package.json" for w in root_package["workspaces"]]
    for manifest in manifests:
        assert json.loads(manifest.read_text(encoding="utf-8")).get("license") == licensing.PROGRAM_LICENSE, manifest
    # the platform's Python packages are the ones the image installs (not a glob: a checkout can hold
    # other worktrees under .claude/)
    installed = _dockerfile_editable_installs((ROOT / "infra" / "Dockerfile").read_text(encoding="utf-8"))
    projects = [ROOT / path / "pyproject.toml" for path in installed]
    assert len(projects) >= 8, projects
    for project in projects:
        meta = tomllib.loads(project.read_text(encoding="utf-8"))["project"]
        assert meta.get("license") == licensing.PROGRAM_LICENSE, project
        # A wheel or sdist built from the package states AGPL in its metadata, so it must also carry
        # the text (AGPL §4). PEP 639 licence files cannot point outside the project directory, so
        # each package holds an unmodified copy of the root LICENSE.
        assert meta.get("license-files") == ["LICENSE"], project
        copy = project.parent / "LICENSE"
        assert copy.is_file() and _sha256(copy) in AGPL_3_0_SHA256, f"{copy} 必须是未经改动的 GNU AGPL 第 3 版原文"


def test_the_sdks_carry_their_own_apache_license():
    """apps/docs/sdk/ is Apache-2.0: its files and both packages say so, and ship the text."""
    import json
    import tomllib
    sdk = ROOT / "apps" / "docs" / "sdk"
    for lic in (sdk / "LICENSE", sdk / "python" / "LICENSE", sdk / "typescript" / "LICENSE"):
        assert lic.is_file() and _sha256(lic) == APACHE_2_0_SHA256, lic
    project = tomllib.loads((sdk / "python" / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    assert project["license"] == "Apache-2.0" and project["license-files"] == ["LICENSE"]
    package = json.loads((sdk / "typescript" / "package.json").read_text(encoding="utf-8"))
    assert package["license"] == "Apache-2.0" and "LICENSE" in package["files"]
