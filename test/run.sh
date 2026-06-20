#!/bin/bash
# test/run.sh — run the full pure-bash test suite and aggregate results.
#
# Runs every test/test_*.sh (each is self-contained and exits non-zero if any
# of its assertions failed) and prints an overall summary. Exit status is 0 only
# if every test file passed — suitable for CI.
#
# NOTE: test/launchd-gap-test.sh is intentionally NOT run here. It requires a
# real macOS launchd session and is a manual check (see test/TESTING.md).

set -uo pipefail
cd "$(dirname "$0")"

files_run=0
files_failed=0

for t in test_*.sh; do
  [[ -e "$t" ]] || continue
  files_run=$((files_run + 1))
  echo "========================================================================"
  echo "== $t"
  echo "========================================================================"
  if bash "$t"; then
    :
  else
    files_failed=$((files_failed + 1))
  fi
  echo
done

echo "========================================================================"
if [[ "$files_failed" -eq 0 ]]; then
  echo "SUITE PASSED: $files_run test file(s), 0 failed"
  exit 0
else
  echo "SUITE FAILED: $files_failed of $files_run test file(s) failed"
  exit 1
fi
