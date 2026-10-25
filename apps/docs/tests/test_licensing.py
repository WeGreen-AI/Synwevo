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
import tarfile
from pathlib import Path

from synwevo_docs import licensing

ROOT = __import__("synwevo_core.http", fromlist=["repo_root"]).repo_root()


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
    assert "无 AGPL 依赖" in a["notice"] and "不含 MuPDF" in a["notice"]


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
    monkeypatch.setattr(licensing, "ROOT", tmp_path)

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
    monkeypatch.setattr(licensing, "ROOT", tmp_path)
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


def test_the_offer_holds_every_mounted_application_and_the_licence_files():
    offered = {name for _, name in licensing._archive_files()}
    for must in ("apps/suite/synwevo_suite/app.py", "apps/suite/web/src/main.tsx",
                 "apps/messages/synwevo_messages/app.py", "apps/messages/web/src/main.tsx",
                 "apps/portal/web/src/main.tsx", "packages/ui/src/index.ts",
                 "LICENSE", "NOTICE", "TRADEMARKS.md", "apps/docs/sdk/LICENSE",
                 "apps/docs/tessdata/LICENSE", "apps/docs/tessdata/README.md"):
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
    monkeypatch.setattr(licensing, "ROOT", tmp_path)
    monkeypatch.setattr(licensing, "MAX_FILE", 32)
    blob, _ = licensing.source_archive()
    tar = tarfile.open(fileobj=io.BytesIO(blob))
    names = tar.getnames()
    offer = tar.extractfile([m for m in tar.getmembers() if m.name.endswith("SOURCE_OFFER.txt")][0]).read().decode()
    assert not any(n.endswith("docs/big.bin") for n in names)
    assert "  - docs/big.bin" in offer and "包含所列全部内容" not in offer


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


# SHA-256 of the licence texts exactly as their stewards publish them: the GNU AGPL version 3 from
# https://www.gnu.org/licenses/agpl-3.0.txt and the Apache License 2.0 from
# https://www.apache.org/licenses/LICENSE-2.0.txt. A licence file is copied, never edited: the
# copyright line and the "or later" choice live in NOTICE and the READMEs, not in the licence text.
AGPL_3_0_SHA256 = "8486a10c4393cee1c25392769ddd3b2d6c242d6ec7928e1414efff7dfb2f07ef"
APACHE_2_0_SHA256 = "cfc7749b96f63bd31c3c42b5c471bf756814053e847c10f3eb003417bc523d30"


def _sha256(path: Path) -> str:
    import hashlib
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_license_file_is_shipped():
    """No skip: the root LICENSE is the unmodified GNU AGPL v3 text wherever the program runs."""
    lic = ROOT / "LICENSE"
    assert lic.is_file(), "LICENSE 不在运行目录里：镜像没有复制它，对应源代码包也会缺它"
    assert _sha256(lic) == AGPL_3_0_SHA256, "LICENSE 必须是未经改动的 GNU AGPL 第 3 版原文"


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
        assert tomllib.loads(project.read_text(encoding="utf-8"))["project"].get("license") == licensing.PROGRAM_LICENSE, project


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
