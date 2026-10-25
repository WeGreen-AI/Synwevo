"""The release manifest: which files of an installation are the program's source.

The Corresponding Source archive (``synwevo_docs.licensing``) is anonymous, so it must hold the
files of the source release and nothing else. Walking the installation's directories cannot
tell those apart from what else sits there — a backup of ``runtime.env``, an editor's swap file, a
key, a ``.pgpass`` — and a list of names to leave out is never complete. So the archive is built
only from a list of files that belong to the release:

* **in the image**, this manifest. The production build context is ``git archive`` of a commit
  (CI and the deploy receiver both build from it), so at build time the context *is* the release.
  ``infra/Dockerfile`` runs this file right after copying the context and before building
  anything, and it records every file then present. ``.gitattributes`` marks this file
  ``export-subst``: ``git archive`` writes the commit and its time into the two constants below,
  which is how the writer knows its context came from ``git archive``. In a working tree they keep
  git's placeholder text; the writer then writes nothing, and the image refuses to offer an
  archive rather than offer whatever the working tree held (build such an image from ``git
  archive``, docs/operations.md §1);
* **in a checkout**, ``git ls-files`` (``licensing`` asks git directly).

Standard library only: the Dockerfile runs it before the platform's packages are installed
(``python -P apps/docs/synwevo_docs/source_manifest.py /app``).
"""
from __future__ import annotations

import json
import os
import re
import stat
import sys
from pathlib import Path

#: Written at the root of the installation by the image build; read by ``licensing``.
MANIFEST_NAME = "SOURCE_MANIFEST.json"
FORMAT = 1

# Stamped by `git archive` (export-subst, see .gitattributes): the commit the archive was made from
# and its commit time. In a checkout both still read as git's placeholders.
RELEASE_COMMIT = "$Format:%H$"
RELEASE_TIME = "$Format:%ct$"


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


def write(root: Path, *, commit: str, commit_time: int) -> Path:
    """Record every file now under ``root`` as the release ``commit``. Call it on the build
    context alone: before anything is built or installed into the same directory."""
    root = Path(root)
    body = {"format": FORMAT, "commit": commit, "commit_time": int(commit_time),
            "files": _regular_files(root)}
    target = root / MANIFEST_NAME
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


def main(argv: list[str]) -> int:
    root = Path(argv[0] if argv else ".").resolve()
    stamp = release_stamp()
    if stamp is None:
        # Not fatal: a developer's local image still builds. It just cannot offer its source.
        print("source manifest: this build context is not a `git archive` of a commit, so it cannot tell "
              "the source from other files in the working tree; no manifest written, and /api/source-code "
              "will refuse. Build from git archive instead: "
              "git archive --format=tar HEAD | docker build -f infra/Dockerfile -t synwevo -",
              file=sys.stderr)
        return 0
    target = write(root, commit=stamp[0], commit_time=stamp[1])
    count = len(read(root)["files"])
    print(f"source manifest: {count} files of commit {stamp[0]} -> {target}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
