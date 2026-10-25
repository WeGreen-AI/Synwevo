#!/usr/bin/env python3
"""Check the release archive a deploy would send against the host receiver's own rules.

The deploy job sends `git archive --format=tar HEAD | gzip -n` to receive.py, which refuses the whole
release when the compressed stream exceeds 50 MiB or when `validate_members` rejects an entry: anything
but a regular file or directory (a symbolic link anywhere, docs/ included), a path through `data`,
`.local`, `node_modules`, `.venv`, `.git` or a `.env*` name, a duplicate, more than 10,000 members or
150 MiB in all. This builds the same archive from a checkout and applies the receiver's own
`validate_members`, so a change that would make every later deploy fail is refused in its pull
request instead. Standard library only: the records-only verify runs it without installing anything.

    python3 infra/production/check_release_archive.py [checkout]   # default: this repository
"""
from __future__ import annotations

import gzip
import importlib.util
import io
from pathlib import Path
import subprocess
import sys
import tarfile

HERE = Path(__file__).resolve().parent
# receive.main refuses a compressed release above this size (`size > 50 * 1024 * 1024`).
COMPRESSED_LIMIT = 50 * 1024 * 1024


def receiver():
    spec = importlib.util.spec_from_file_location('synwevo_release_receiver', HERE / 'receive.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def problems(checkout: Path) -> list[str]:
    """Every reason the receiver would refuse `git archive HEAD` of this checkout; empty when it accepts it."""
    try:
        tar = subprocess.run(['git', 'archive', '--format=tar', 'HEAD'], cwd=checkout,
                             capture_output=True, check=True).stdout
    except (OSError, subprocess.CalledProcessError) as e:
        detail = getattr(e, 'stderr', b'') or b''
        return [f'cannot build the release archive: {e} {detail.decode("utf-8", "replace").strip()}'.strip()]
    found = []
    # gzip -n: no name, no timestamp; level 6 is gzip's default.
    compressed = len(gzip.compress(tar, compresslevel=6, mtime=0))
    if compressed > COMPRESSED_LIMIT:
        found.append(f'the compressed release is {compressed} bytes; the receiver accepts at most {COMPRESSED_LIMIT}')
    validate = receiver().validate_members
    with tarfile.open(fileobj=io.BytesIO(tar)) as archive:
        members = archive.getmembers()
    # Entry by entry first, so that every refused path is listed, then the whole archive for the
    # limits and duplicates that only the complete list shows.
    entries = []
    for member in members:
        try:
            validate([member])
        except ValueError as e:
            entries.append(str(e))
    if not entries:
        try:
            validate(members)
        except ValueError as e:
            entries.append(str(e))
    return found + entries


def main(argv: list[str]) -> int:
    checkout = Path(argv[1]) if len(argv) > 1 else HERE.parents[1]
    found = problems(checkout)
    for line in found:
        print(f'::error::release archive: {line}')
    if found:
        print('The host receiver would refuse this release (infra/production/receive.py). Untrack the '
              'entry (a link becomes a copy or a written path), rename the directory, or mark generated '
              'evidence export-ignore in .gitattributes.')
        return 1
    print('The release archive passes the receiver\'s checks.')
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
