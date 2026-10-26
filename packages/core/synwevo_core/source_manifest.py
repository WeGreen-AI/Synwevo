"""The release manifest: which files of an installation are the program's source.

The Corresponding Source archive (``synwevo_core.source_offer``) is anonymous, so it must hold the
files of the source release and nothing else. Walking the installation's directories cannot
tell those apart from what else sits there — a backup of ``runtime.env``, an editor's swap file, a
key, a ``.pgpass`` — and a list of names to leave out is never complete. So the archive is built
only from a list of files that belong to the release:

* **in the image**, this manifest. ``infra/Dockerfile`` runs this file in a stage of its own, on
  one ``COPY`` of the whole build context and before anything is built, and it records every file
  of the context — so it writes one only when it can tell that the context is the release, a ``git
  archive`` of one commit with nothing added or changed:

  - ``.gitattributes`` marks this file ``export-subst``: ``git archive`` writes the commit and its
    committer time into the two constants below. In a working tree they keep git's placeholder
    text, and the writer writes nothing.
  - That proves only that *this file* came out of ``git archive``. A directory unpacked from one
    keeps the stamp whatever is put into it afterwards — the deploy receiver's release directory,
    CI's extraction, a release tarball a self-hoster unpacks and builds with ``docker build .`` —
    and a key planted there went onto the manifest and out to every anonymous downloader. So the
    writer also holds every file to the other mark ``git archive`` leaves: it gives *every* member
    the commit's committer time as its modification time, and unpacking (tar, Python's
    ``tarfile``) and ``docker build`` (BuildKit and the classic builder, from a directory or a tar
    on stdin) keep it. A file with any other time was added or changed after unpacking; then the
    context is not the release, and the writer writes nothing (and removes a manifest already
    there), naming the files in the build log.
  - Which is why it runs on that one ``COPY`` and not on the final stage's per-directory ones.
    Docker's layer cache leaves modification times out of a ``COPY``'s cache key, so a directory
    whose content had not changed since an earlier build on the same builder came back as that
    build's layer, with that build's times: every file in it then failed the check, and the deploy
    receiver, whose cache persists between deploys, built images without a manifest whenever
    ``packages/`` was unchanged. The one ``COPY``'s key covers every file of the context, this
    file's stamp among them, so no other commit's build can supply it. A rebuild of the same
    commit with identical content does reuse the earlier result, a refusal included (for instance
    after unpacking with ``tar -m``, which drops the times): rebuild with ``--no-cache`` once the
    context is right. Checking the whole context also checks the files the final stage does not
    copy; the deploy receiver's own ``.archive-sha256`` marker is kept out of it by
    ``.dockerignore``.

  This catches files that arrive by accident; an operator who sets the times on purpose can publish
  what they like from their own server anyway. Without a manifest the image refuses to offer an
  archive rather than offer whatever its context held (build such an image from ``git archive``,
  docs/operations.md §1);
* **in a checkout**, ``git ls-files`` (``source_offer`` asks git directly).

Standard library only: the Dockerfile runs it in a stage without the platform's packages
(``python -P /release/packages/core/synwevo_core/source_manifest.py /release /manifest``: check the
context in ``/release``, write the manifest into ``/manifest``, which the final stage copies to
``/app``).
"""
from __future__ import annotations

import json
import os
import re
import stat
import sys
from pathlib import Path

#: Written at the root of the installation by the image build; read by ``source_offer``.
MANIFEST_NAME = "SOURCE_MANIFEST.json"
FORMAT = 1

# Stamped by `git archive` (export-subst, see .gitattributes): the commit the archive was made from
# and its commit time. In a checkout both still read as git's placeholders.
RELEASE_COMMIT = "$Format:%H$"
RELEASE_TIME = "$Format:%ct$"
#: At most this many of the files that disqualify a context are named in the build log.
NAMED_IN_LOG = 10


def release_stamp() -> tuple[str, int] | None:
    """(commit, commit time) when this file came out of ``git archive``; None in a working tree."""
    commit, when = RELEASE_COMMIT.strip(), RELEASE_TIME.strip()
    if re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", commit) and when.isdigit():
        return commit, int(when)
    return None


def _regular_files(root: Path) -> list[str]:
    """Every regular file under ``root``, relative and sorted. Symbolic links are not followed or
    listed (the repository has none, and one would point outside the release)."""
    names = []
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames.sort()
        for filename in filenames:
            path = Path(dirpath) / filename
            if stat.S_ISREG(os.lstat(path).st_mode):
                names.append(path.relative_to(root).as_posix())
    return sorted(n for n in names if n != MANIFEST_NAME)


def not_from_the_archive(root: Path, names: list[str], commit_time: int) -> list[str]:
    """The files among ``names`` that do not carry the modification time ``git archive`` gave every
    member of the commit's archive (its committer time, in whole seconds): added to the unpacked
    context, or changed there, after it was unpacked."""
    expected = int(commit_time) * 1_000_000_000
    return [name for name in names if os.lstat(Path(root) / name).st_mtime_ns != expected]


def write(root: Path, *, commit: str, commit_time: int, into: Path | None = None) -> Path:
    """Record every file now under ``root`` as the release ``commit``, in ``into`` (``root`` itself
    when left out). Call it on the build context alone: before anything is built or installed into
    the same directory."""
    root = Path(root)
    body = {"format": FORMAT, "commit": commit, "commit_time": int(commit_time),
            "files": _regular_files(root)}
    target = Path(into if into is not None else root) / MANIFEST_NAME
    tmp = target.with_name(target.name + ".tmp")
    tmp.write_text(json.dumps(body, ensure_ascii=False, indent=0) + "\n", encoding="utf-8")
    tmp.replace(target)
    return target


def read(root: Path) -> dict | None:
    """The manifest under ``root``, or None when there is none or it is not one this code wrote."""
    try:
        body = json.loads((Path(root) / MANIFEST_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if (not isinstance(body, dict) or body.get("format") != FORMAT
            or not isinstance(body.get("files"), list) or not all(isinstance(f, str) for f in body["files"])
            or not isinstance(body.get("commit"), str) or not isinstance(body.get("commit_time"), int)):
        return None
    return body


BUILD_FROM_ARCHIVE = ("Build from git archive instead: "
                      "git archive --format=tar HEAD | docker build -f infra/Dockerfile -t synwevo -")


def main(argv: list[str]) -> int:
    """``source_manifest.py CONTEXT [INTO]``: check the build context in CONTEXT and write its
    manifest into the directory INTO (CONTEXT itself when left out), or remove one there."""
    root = Path(argv[0] if argv else ".").resolve()
    into = Path(argv[1]).resolve() if len(argv) > 1 else root
    stamp = release_stamp()
    # Neither refusal is fatal: the image still builds (a developer's local image, say). It just
    # cannot offer its source, and /api/source-code answers 503 saying so.
    if stamp is None:
        (into / MANIFEST_NAME).unlink(missing_ok=True)
        print("source manifest: this build context is not a `git archive` of a commit, so it cannot tell "
              "the source from other files in the working tree; no manifest written, and /api/source-code "
              f"will refuse. {BUILD_FROM_ARCHIVE}", file=sys.stderr)
        return 0
    foreign = not_from_the_archive(root, _regular_files(root), stamp[1])
    if foreign:
        (into / MANIFEST_NAME).unlink(missing_ok=True)
        shown = ", ".join(foreign[:NAMED_IN_LOG]) + (", ..." if len(foreign) > NAMED_IN_LOG else "")
        print(f"source manifest: {len(foreign)} files in this build context were added or changed after it "
              f"was unpacked from `git archive` of commit {stamp[0]} (their modification time is not the "
              f"commit time {stamp[1]} that git archive gives every file): {shown}. The context is not "
              "that commit's release, so no manifest written, and /api/source-code will refuse. Remove "
              "them and unpack the archive again, or commit the change. If only the times were wrong "
              "(an unpacking that drops them), the same content rebuilt reuses this result, because "
              f"Docker's cache ignores times: rebuild with --no-cache. {BUILD_FROM_ARCHIVE}",
              file=sys.stderr)
        return 0
    target = write(root, commit=stamp[0], commit_time=stamp[1], into=into)
    count = len(read(into)["files"])
    print(f"source manifest: {count} files of commit {stamp[0]} -> {target}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
