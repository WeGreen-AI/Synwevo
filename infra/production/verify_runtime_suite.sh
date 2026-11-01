#!/usr/bin/env bash
# The verify job's runtime suite: the repository's pytest suites, run inside the test derivative of
# the deployment image (infra/production/Dockerfile.verify) as two containers in parallel.
#
#   bash infra/production/verify_runtime_suite.sh <image> <artifacts-root>
#
# <image> is the synwevo:verify image the workflow has just built. <artifacts-root> is an existing
# directory (the runner's $RUNNER_TEMP) in which this script creates ci-test-artifacts-a and
# ci-test-artifacts-b, one bind mount per container, so that failed fixtures survive `--rm` and the
# workflow's diagnostics step can list them; an existing directory of that name is refused (a fresh root
# per run). Nothing else is mounted: no production data, no network. The image's own command stays the
# whole suite in one container, for a manual run of the verify mirror; this script passes each container
# the same pytest flags plus its share of the test paths.
#
# Why two containers, and why this split. One container ran the whole suite in 1h43m on 2026-11-01 (run
# 41267068260, 7,963 tests) against a 120-minute job limit that cancels the run — and a cancelled run
# deploys nothing. The hosted ubuntu-24.04 runner has 4 vCPU and 16 GB; every container keeps the
# 3 GiB / 2 CPU limits the suite is required to fit (unstopped worker threads once thrashed it at 3 GiB:
# the limit is a regression guard, docs/quality/verification.md §16.49), so two containers fill the
# runner and a third would only take CPU from the other two. A suite is not split inside, and
# A = tests/integration + apps/messages/tests, B = the three application suites is the split closest to
# half on both measurements. That run's own progress lines (1 % apart, so ±2 min per suite) put
# tests/integration at ~51 of its 101 suite minutes: A ≈ 52 min, B ≈ 49 min, so on the hosted runner A
# is the longer container and the step is expected to take about 55 minutes plus the image build. A local
# one-process run divides the other way, 43.5 % (A) against 56.5 % (B) — tests/integration ~42 %,
# apps/docs/tests ~21 %, services/identity/tests ~21 %, apps/suite/tests ~15 %, apps/messages/tests ~1.5 % —
# the ratio the split was first chosen on; moving apps/messages/tests (~1 min) would change nothing
# measurable. Together the two lists are exactly pyproject.toml's testpaths
# (infra/production/tests/test_workflow.py checks that), so no test is lost to the split, and running a
# suite on its own means the same as running them together: the root conftest.py holds the shared hooks
# and the `fixtures` session fixture that generates the git-ignored testdata/ files, which
# tests/integration reads as well as the Docs suite. In one container the Docs suite had generated them
# before tests/integration ran; in a container of its own tests/integration generates them itself
# (test_workflow.py checks that no suite reads them by a path of its own). Both containers run to the
# end: a failure in one and a pass in the other says more than the failure alone, and --maxfail=1 still
# ends a failing container at its first failure.
#
# Exit handling is fail-closed and explicit. Under `set -e` a failing `wait` would end the script before
# the second container is waited for, so each wait records its status with `|| status=$?`; both codes
# are printed, and the script fails when either is non-zero — a container killed by the memory limit
# returns 137 and fails the step like a test failure does. `pipefail` makes the prefixed output pipeline
# of each container return docker's exit code, not the prefixer's. Lines are prefixed [A] and [B] as
# they are written, so a traceback is visible when it happens and `grep '^\[A\]'` recovers one
# container's complete log. infra/production/tests/test_verify_runtime_suite.py runs this script against
# throwaway containers that pass, fail and die by SIGKILL.
set -euo pipefail

if [ $# -ne 2 ]; then
  echo "usage: $0 <image> <artifacts-root>" >&2
  exit 2
fi
image=$1
root=$(cd "$2" && pwd -P)    # docker needs an absolute bind source; fails when it is no directory

# The two halves of pyproject.toml's testpaths (see the header for the split).
suite_a=(tests/integration apps/messages/tests)
suite_b=(apps/docs/tests services/identity/tests apps/suite/tests)
# The flags of Dockerfile.verify's CMD, minus --basetemp, which is per container (below).
pytest_flags=(--tb=short --maxfail=1 --durations=20 -ra --ci-clean-passed-tmp)

run_suite() {  # run_suite <A|B> <a|b> <test paths...>
  local label=$1 slot=$2 status=0
  shift 2
  docker run --rm --init --network none --memory 3g --cpus 2 \
      --mount "type=bind,source=$root/ci-test-artifacts-$slot,target=/tmp/ci-test-artifacts" \
      "$image" python -m pytest "${pytest_flags[@]}" "--basetemp=/tmp/ci-test-artifacts/pytest-$slot" "$@" 2>&1 \
    | while IFS= read -r line || [ -n "$line" ]; do printf '[%s] %s\n' "$label" "$line"; done \
    || status=$?
  printf '[%s] container finished after %ss with exit %s\n' "$label" "$SECONDS" "$status"
  return "$status"
}

explain() {  # the meaning of an exit code, for the summary
  case $1 in
    0) echo 'passed' ;;
    1) echo 'tests failed' ;;
    2) echo 'pytest was interrupted' ;;
    3) echo 'pytest internal error' ;;
    4) echo 'pytest usage error' ;;
    5) echo 'no tests were collected' ;;
    125) echo 'docker could not start the container' ;;
    126|127) echo 'the container command could not run' ;;
    137) echo 'killed by SIGKILL: the 3 GiB memory limit, most likely' ;;
    *) echo 'see the output above' ;;
  esac
}

mkdir -m 0777 "$root/ci-test-artifacts-a" "$root/ci-test-artifacts-b"
echo "Runtime suite A: ${suite_a[*]}"
echo "Runtime suite B: ${suite_b[*]}"
run_suite A a "${suite_a[@]}" &
pid_a=$!
run_suite B b "${suite_b[@]}" &
pid_b=$!

status_a=0
status_b=0
wait "$pid_a" || status_a=$?
wait "$pid_b" || status_b=$?
echo "Runtime suite A (${suite_a[*]}): exit $status_a ($(explain "$status_a"))"
echo "Runtime suite B (${suite_b[*]}): exit $status_b ($(explain "$status_b"))"
if [ "$status_a" -ne 0 ] || [ "$status_b" -ne 0 ]; then
  echo "::error::The runtime suite failed (A: exit $status_a, B: exit $status_b); the output above is prefixed [A] and [B]."
  if [ "$status_a" -ne 0 ]; then exit "$status_a"; fi
  exit "$status_b"
fi
echo 'Both runtime suite containers passed.'
