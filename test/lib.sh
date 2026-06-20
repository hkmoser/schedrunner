# test/lib.sh — minimal pure-bash test helpers + a hermetic environment.
#
# Source this at the top of every test_*.sh:
#     source "$(dirname "$0")/lib.sh"
# then use the assert_* helpers and end with:  finish
#
# Design notes (see ../CLAUDE.md "Testing discipline"):
#   - Real collaborators: tests drive the REAL scripts with real `git` against
#     throwaway local repos under temp dirs. Only the external service `gh` is
#     stubbed (as an exported shell function), because it talks to GitHub.
#   - Hermetic: git config is isolated to a temp file so tests neither depend on
#     nor mutate host git settings, and never hang on commit signing.

set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

TESTS_PASSED=0
TESTS_FAILED=0

# ---- hermetic git config (isolated from the host) --------------------------
GIT_CONFIG_GLOBAL="$(mktemp)"; export GIT_CONFIG_GLOBAL
export GIT_CONFIG_SYSTEM=/dev/null
git config -f "$GIT_CONFIG_GLOBAL" user.email "tests@schedrunner.test"
git config -f "$GIT_CONFIG_GLOBAL" user.name  "schedrunner tests"
git config -f "$GIT_CONFIG_GLOBAL" init.defaultBranch main
git config -f "$GIT_CONFIG_GLOBAL" commit.gpgsign false
git config -f "$GIT_CONFIG_GLOBAL" protocol.file.allow always
git config -f "$GIT_CONFIG_GLOBAL" safe.directory '*'
git config -f "$GIT_CONFIG_GLOBAL" advice.detachedHead false

# ---- temp dir tracking + cleanup -------------------------------------------
_TMPDIRS=()
make_tmpdir() { local d; d="$(mktemp -d)"; _TMPDIRS+=("$d"); printf '%s' "$d"; }
_cleanup() { local d; for d in "${_TMPDIRS[@]:-}"; do [[ -n "${d:-}" ]] && rm -rf "$d"; done
             [[ -n "${GIT_CONFIG_GLOBAL:-}" ]] && rm -f "$GIT_CONFIG_GLOBAL"; }
trap _cleanup EXIT

# ---- fake `gh` (the only external service) ---------------------------------
# Exported so child bash scripts (provision-repos.sh, auto-deploy.sh) call this
# instead of any real gh on PATH. Configure per-test via:
#   GH_STUB_LOG        file to append each "gh ..." invocation to (for asserts)
#   GH_STUB_LOGIN      login for `gh api user` (unset->testuser, ""->unauthed)
#   GH_STUB_EXISTING   space-separated owner/repo that `gh repo view` finds
#   GH_STUB_CREATE_RC  exit code for `gh repo create` (default 0)
gh() {
  [[ -n "${GH_STUB_LOG:-}" ]] && printf '%s\n' "$*" >> "$GH_STUB_LOG"
  case "$1 ${2:-}" in
    "api user")
      printf '%s\n' "${GH_STUB_LOGIN-testuser}" ;;
    "repo view")
      local target="$3" r
      for r in ${GH_STUB_EXISTING:-}; do [[ "$r" == "$target" ]] && return 0; done
      return 1 ;;
    "repo create")
      return "${GH_STUB_CREATE_RC:-0}" ;;
    *) return 0 ;;
  esac
}
export -f gh

# ---- assertions ------------------------------------------------------------
_ok()  { printf '  ok   - %s\n' "$1"; TESTS_PASSED=$((TESTS_PASSED + 1)); }
_fail(){ printf '  FAIL - %s\n' "$1"; [[ -n "${2:-}" ]] && printf '         %s\n' "$2"
         TESTS_FAILED=$((TESTS_FAILED + 1)); }

assert_eq()           { [[ "$1" == "$2" ]] && _ok "$3" || _fail "$3" "expected [$1], got [$2]"; }
assert_contains()     { [[ "$1" == *"$2"* ]] && _ok "$3" || _fail "$3" "missing [$2] in: $1"; }
assert_not_contains() { [[ "$1" != *"$2"* ]] && _ok "$3" || _fail "$3" "unexpected [$2] in: $1"; }
assert_file()         { [[ -f "$1" ]] && _ok "$2" || _fail "$2" "missing file: $1"; }
assert_no_file()      { [[ ! -e "$1" ]] && _ok "$2" || _fail "$2" "unexpected file: $1"; }
assert_status()       { [[ "$1" == "$2" ]] && _ok "$3" || _fail "$3" "expected exit $1, got $2"; }

# poll_until <timeout_seconds> <command...> — for backgrounded/detached work.
poll_until() {
  local timeout="$1"; shift
  local end=$(( $(date +%s) + timeout ))
  while (( $(date +%s) <= end )); do
    "$@" && return 0
    sleep 0.1
  done
  return 1
}

# Print this file's tally and exit non-zero if anything failed.
finish() {
  printf '%s: %d passed, %d failed\n' "$(basename "$0")" "$TESTS_PASSED" "$TESTS_FAILED"
  [[ "$TESTS_FAILED" -eq 0 ]]
}
